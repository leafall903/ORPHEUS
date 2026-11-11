"""Actual Mei spray frost against Torbjorn."""

import os
import unittest
from dataclasses import replace

import test_game_torbjorn_defenses as defenses
from test_game_combat import aim, in_world
from test_game_mover import box, wall

from ow174.game import combat, stats
from ow174.game.collision import World
from ow174.game.commands import Command
from ow174.game.content import heroes
from ow174.game.world import ARMOUR_POOL, HEALTH_POOL


@unittest.skipUnless(all(os.environ.get(name) for name in (
    "ORPHEUS_TORBJORN_GRAPH_BUNDLE", "ORPHEUS_PROJECTILE_TAGS",
)), "needs prepared combat inputs")
class TorbjornFrostTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        defenses.TorbjornDefenseTests.setUpClass()
        cls.addClassCleanup(defenses.TorbjornDefenseTests.tearDownClass)

    def arena(self, distance=3):
        fixture = defenses.TorbjornDefenseTests()
        run, torb, mei, other = fixture.arena("Mei", distance)
        x, y, z = torb.mover.position
        fixture.place(other, (x + 100, y, z + 100))
        return fixture, run, torb, mei, other

    def test_single_spray_slows_and_world_gap_clears_progress(self):
        fixture, run, torb, mei, _ = self.arena()
        fight = run.match.combat
        hp = fixture.health(run, torb)
        fixture.step(run, torb, mei, defense=1)
        fixture.advance(run, torb, mei, 19)
        self.assertAlmostEqual(hp.total, 248.075)
        self.assertAlmostEqual(fight.frost.sample(torb, run.frame)[0], .2)
        run.frame += 1
        run.match.update(run.frame * combat.TICK, run.frame)
        torb.take_commands([Command(run.frame, forward=127)])
        self.assertAlmostEqual(torb.mover.values.run_forward, torb.mover.data.run_forward * .8, places=5)
        run.frame += 70
        run.match.update(run.frame * combat.TICK, run.frame)
        self.assertIsNone(fight.frost.sample(torb, run.frame))
        self.assertEqual(torb.body_script.component.entity_var(2427).value(), 0)

    def test_spray_freezes_overload_torbjorn_and_spends_armour_in_order(self):
        fixture, run, torb, mei, _ = self.arena()
        fight = run.match.combat
        hp = fixture.health(run, torb)
        run.step({torb: (0, 0, 16)})
        self.assertEqual(hp.over_armour.amount, 100)
        source = hp.over_armour.source
        for _ in range(140):
            fixture.step(run, torb, mei, defense=1)
            if fight.is_stunned(torb):
                break
        self.assertTrue(fight.is_stunned(torb))
        frozen = fight.frost.sample(torb, run.frame)
        self.assertEqual(frozen[2] - frozen[1], 1300)
        self.assertEqual(hp.over_armour.source, source)
        self.assertAlmostEqual(hp.over_armour.amount, 42.25)
        self.assertEqual(hp.now[ARMOUR_POOL], 50)
        self.assertEqual(hp.now[HEALTH_POOL], 200)
        self.assertAlmostEqual(mei.stats.value(stats.HERO_DAMAGE), 57.75, places=4)

        for _ in range(190):
            fixture.step(run, torb, mei, defense=1)
            if hp.now[HEALTH_POOL] < 200:
                break
        self.assertEqual(hp.over_armour.source, source)
        self.assertEqual(hp.over_armour.amount, 0)
        self.assertEqual(hp.now[ARMOUR_POOL], 0)
        self.assertAlmostEqual(hp.now[HEALTH_POOL], 200 - (78 * 2.75 - 150 / .7))
        self.assertAlmostEqual(mei.stats.value(stats.HERO_DAMAGE), 350 - hp.total, places=4)

    def test_flashbang_and_sleep_dart_compete_with_existing_freeze(self):
        for effect in ("early_flash", "late_flash", "sleep"):
            with self.subTest(effect=effect):
                fixture, run, torb, mei, caster = self.arena()
                fight = run.match.combat
                ana = next(hero for hero in heroes().values() if hero.name == "Ana")
                self.assertTrue(run.match.switch_hero(
                    caster, ana if effect == "sleep" else heroes()[0x02E0000000000042],
                ))
                x, y, z = torb.mover.position
                fixture.place(caster, (x + 3, y, z))
                fixture.advance(run, torb, mei, 35)
                hp = fixture.health(run, torb)
                for _ in range(140):
                    fixture.step(run, torb, mei, defense=1)
                    if fight.is_stunned(torb):
                        break
                frost = fight.frost.sample(torb, run.frame)
                self.assertEqual(frost[2] - frost[1], 1300)
                if effect == "late_flash":
                    fixture.advance(run, torb, mei, 35)
                before = hp.total
                direction = aim(fight._eye(caster), (x, y + 1, z))
                damage_before = caster.stats.value(stats.HERO_DAMAGE)
                run.step({caster: (*direction, 8 if effect == "sleep" else 16)})
                for _ in range(60):
                    if caster.stats.value(stats.HERO_DAMAGE) > damage_before:
                        break
                    run.step({caster: (*direction, 0)})
                self.assertGreater(caster.stats.value(stats.HERO_DAMAGE), damage_before)
                self.assertGreater(before - hp.total, 0)
                impact = fight.tick
                self.assertLess(impact * 16, frost[2])
                if effect == "early_flash":
                    self.assertGreater(frost[2] - impact * 16, 800)
                    self.assertNotIn(torb.body, fight.stuns)
                    self.assertEqual(fight.frost.sample(torb, run.frame), frost)
                    end = (frost[2] + 15) // 16
                else:
                    self.assertIsNone(fight.frost.sample(torb, run.frame))
                    self.assertEqual(torb.body_script.component.entity_var(2427).value(), 0)
                    if effect == "sleep":
                        end, minimum = fight.sleep[torb.body]
                        self.assertEqual(minimum - impact, 94)
                        self.assertEqual(end - impact, 313)
                    else:
                        self.assertLess(frost[2] - impact * 16, 800)
                        end = fight.stuns[torb.body]
                        self.assertEqual(end, impact + 50)

                while run.frame < end - 1:
                    fixture.step(run, torb, mei, fire=1)
                    self.assertTrue(fight.is_stunned(torb))
                    self.assertFalse(torb.body_script.component.held)
                mei_hp = fixture.health(run, mei)
                before = mei_hp.total
                fixture.step(run, torb, mei, fire=1)
                self.assertFalse(fight.is_stunned(torb))
                fixture.advance(run, torb, mei, 30)
                self.assertEqual(before - mei_hp.total, 70)

    def test_spray_freeze_replaces_flashbang_but_cannot_replace_fresh_sleep(self):
        ana = next(hero for hero in heroes().values() if hero.name == "Ana")
        for sleep in (False, True):
            with self.subTest(sleep=sleep):
                fixture, run, torb, mei, caster = self.arena(distance=1.5)
                fight = run.match.combat
                self.assertTrue(run.match.switch_hero(caster, ana if sleep else heroes()[0x02E0000000000042]))
                x, y, z = torb.mover.position
                fixture.place(caster, (x + 3, y, z))
                fixture.advance(run, torb, mei, 35)

                for _ in range(140):
                    fixture.step(run, torb, mei, defense=1)
                    progress = fight.frost.sample(torb, run.frame)
                    if progress is not None and progress[0] >= .99:
                        break
                self.assertAlmostEqual(progress[0], .998, places=4)
                self.assertFalse(fight.is_stunned(torb))
                fixture.place(mei, (x, y, z + 5))

                direction = aim(fight._eye(caster), (x, y + 1, z))
                incumbent = fight.sleep if sleep else fight.stuns
                run.step({caster: (*direction, 8 if sleep else 16)})
                for _ in range(50):
                    if torb.body in incumbent:
                        break
                    run.step({caster: (*direction, 0)})
                self.assertIn(torb.body, incumbent)
                self.assertAlmostEqual(fight.frost.sample(torb, run.frame)[0], .998, places=4)

                before = fixture.health(run, torb).total
                fixture.step(run, torb, mei, defense=1)
                for _ in range(12):
                    if fixture.health(run, torb).total < before:
                        break
                    fixture.step(run, torb, mei)
                self.assertLess(fixture.health(run, torb).total, before)
                if sleep:
                    end, earliest = fight.sleep[torb.body]
                    self.assertGreater((earliest - fight.tick) * 16, 1300)
                    self.assertEqual(end, earliest)
                    self.assertIsNone(fight.frost.sample(torb, run.frame))
                else:
                    frost = fight.frost.sample(torb, run.frame)
                    self.assertEqual(frost[2] - frost[1], 1300)
                    self.assertNotIn(torb.body, fight.stuns)
                    end = (frost[2] + 15) // 16
                while run.frame < end - 1:
                    fixture.step(run, torb, mei, fire=1)
                    self.assertTrue(fight.is_stunned(torb))
                hp = fixture.health(run, mei)
                before = hp.total
                fixture.step(run, torb, mei, fire=1)
                self.assertFalse(fight.is_stunned(torb))
                fixture.advance(run, torb, mei, 30)
                self.assertEqual(before - hp.total, 70)

    def test_sustained_spray_freezes_aim_movement_and_skills_then_thaws(self):
        fixture, run, torb, mei, _ = self.arena()
        fight = run.match.combat
        charge = torb.body_script.component.entity_var(338).value()
        torb.body_script.set_ult_charge(charge)
        for _ in range(140):
            fixture.step(run, torb, mei, defense=1)
            if fight.is_stunned(torb):
                break
        self.assertTrue(fight.is_stunned(torb))
        sample = fight.frost.sample(torb, run.frame)
        self.assertEqual(sample[2] - sample[1], 1300)
        fixture.advance(run, torb, mei, 15, defense=1)
        self.assertEqual(fight.frost.sample(torb, run.frame), sample)
        position = tuple(torb.mover.position)
        for _ in range(12):
            run.frame += 1
            run.match.update(run.frame * combat.TICK, run.frame)
            torb.take_commands([Command(run.frame, yaw=17000, pitch=5000, forward=127,
                                        buttons=1 | 8 | 16 | 32)])
            self.assertFalse(torb.body_script.component.held)
        self.assertEqual(tuple(torb.mover.position), position)
        self.assertEqual((torb.mover.yaw, torb.mover.pitch), sample[3:])
        self.assertFalse(fight.turrets or fight.molten_pools)
        self.assertEqual(torb.body_script.component.entity_var(342).value(), charge)
        run.frame = (sample[2] + 15) // 16
        run.match.update(run.frame * combat.TICK, run.frame)
        self.assertFalse(fight.is_stunned(torb))
        hp = fixture.health(run, mei)
        before = hp.total
        fixture.advance(run, torb, mei, 5)
        fixture.advance(run, torb, mei, 25, fire=1)
        self.assertLess(hp.total, before)

    def test_quick_melee_hits_before_spray_freeze_and_is_denied_until_thaw(self):
        for contact_first in (False, True):
            with self.subTest(contact_first=contact_first):
                fixture, run, torb, mei, _ = self.arena(distance=1.5)
                fight = run.match.combat
                torb.body_script.ult_seconds = 0
                torb.body_script.set_ult_charge(0)
                mei_hp = fixture.health(run, mei)
                rifle = next(i for i in torb.body_script.component.instances.values()
                             if i.graph.index == 0x1683)
                for _ in range(140):
                    fixture.step(run, torb, mei, defense=1)
                    sample = fight.frost.sample(torb, run.frame)
                    if sample is not None and sample[0] >= .96 - 1e-6:
                        break
                self.assertIsNotNone(sample)
                self.assertGreaterEqual(sample[0], .96 - 1e-6)
                self.assertLess(sample[0], 1)
                self.assertFalse(fight.is_stunned(torb))

                damage = 30 if contact_first else 0
                if contact_first:
                    fixture.step(run, torb, mei, fire=0x800, defense=1)
                    self.assertFalse(fight.is_stunned(torb))
                    self.assertEqual(mei_hp.total, 250 - damage)
                for _ in range(40):
                    fixture.step(run, torb, mei, defense=1)
                    if fight.is_stunned(torb):
                        break
                self.assertTrue(fight.is_stunned(torb))
                self.assertEqual(mei_hp.total, 250 - damage)
                self.assertEqual(torb.stats.value(stats.HERO_DAMAGE), damage)
                self.assertEqual(torb.body_script.component.entity_var(342).value(), damage)

                thaw = (fight.frost.sample(torb, run.frame)[2] + 15) // 16
                while run.frame < thaw - 1:
                    fixture.step(run, torb, mei, fire=0x800)
                    self.assertTrue(fight.is_stunned(torb))
                    self.assertEqual(mei_hp.total, 250 - damage)
                if contact_first:
                    fixture.advance(run, torb, mei, 20)
                    self.assertFalse(fight.is_stunned(torb))
                    self.assertEqual(mei_hp.total, 250 - damage)
                fixture.step(run, torb, mei, fire=0x800)
                self.assertFalse(fight.is_stunned(torb))
                if not contact_first:
                    self.assertEqual(run.frame, thaw)
                self.assertEqual(mei_hp.total, 220 - damage)
                fixture.advance(run, torb, mei, 25)
                self.assertEqual(mei_hp.total, 220 - damage)
                self.assertEqual(torb.stats.value(stats.HERO_DAMAGE), damage + 30)
                self.assertEqual(torb.body_script.component.entity_var(342).value(), damage + 30)
                self.assertEqual(rifle.vars[53].value(), 18)

    def test_landed_molten_burns_during_freeze_and_primary_resumes_after_thaw(self):
        fixture, run, torb, mei, _ = self.arena()
        fight = run.match.combat
        torb.body_script.set_ult_charge(torb.body_script.component.entity_var(338).value())
        run.step({torb: (16384, 4000, 32)})
        fixture.advance(run, torb, mei, 45)
        run.step({torb: (16384, 4000, 1)})
        for _ in range(100):
            if any(pool.phase == 1 for pool in fight.molten_pools.values()):
                break
            fixture.step(run, torb, mei)
        pool, = fight.molten_pools.values()
        self.assertEqual(pool.phase, 1)

        for _ in range(140):
            fixture.step(run, torb, mei, defense=1)
            if fight.is_stunned(torb):
                break
        self.assertTrue(fight.is_stunned(torb))
        _, _, thaw, *_ = fight.frost.sample(torb, run.frame)
        hp = fixture.health(run, mei)
        self.assertEqual(hp.total, hp.maximum)
        original_position = tuple(mei.mover.position)
        fixture.place(mei, pool.position)
        before = hp.total
        for _ in range(20):
            run.step({torb: (-16384, 4000, 1)})
            self.assertTrue(fight.is_stunned(torb))
            self.assertFalse(torb.body_script.component.held)
            self.assertEqual(list(fight.molten_pools.values()), [pool])
        self.assertLess(hp.total, before)
        self.assertIn((torb.entity, mei.body), fight.molten_burns)

        fixture.place(mei, original_position)
        while run.frame * 16 < thaw:
            run.step({})
        self.assertFalse(fight.is_stunned(torb))
        self.assertNotIn((torb.entity, mei.body), fight.molten_burns)
        run.step({torb: (-16384, 4000, 1)})
        for _ in range(65):
            if len(fight.molten_pools) == 2:
                break
            run.step({})
        self.assertEqual(len(fight.molten_pools), 2)
        self.assertIn(pool.entity, fight.molten_pools)

    def test_deployed_turret_keeps_firing_during_spray_freeze(self):
        fixture, run, torb, mei, _ = self.arena()
        match = run.match
        fight = match.combat
        x, y, z = torb.mover.position
        position = tuple(mei.mover.position)
        fixture.place(mei, (x + 100, y, z + 100))
        run.step({torb: (16384, 4000, 8)})
        fixture.advance(run, torb, mei, 225)
        turret = fight.turrets[torb.entity]
        clear = match.collision_world
        divider = (x + turret.actor.position[0]) / 2
        match.collision_world = World(
            box(x - 25, x + 25, y - 1, y, z - 25, z + 25)
            + wall(divider, z - 15, divider, z + 15, y0=y, y1=y + 4)
        )
        fixture.place(mei, position)
        for _ in range(140):
            fixture.step(run, torb, mei, defense=1)
            if fight.is_stunned(torb):
                break
        self.assertTrue(fight.is_stunned(torb))
        self.assertEqual(turret.shots, 0)
        sample = fight.frost.sample(torb, run.frame)
        hp = fixture.health(run, mei)
        before = hp.total
        self.assertEqual(before, hp.maximum)
        match.collision_world = clear
        for _ in range(40):
            fixture.step(run, torb, mei, fire=1 | 8)
            self.assertTrue(fight.is_stunned(torb))
            self.assertFalse(torb.body_script.component.held)
            self.assertEqual(fight.frost.sample(torb, run.frame), sample)
            self.assertIs(fight.turrets[torb.entity], turret)
        self.assertGreaterEqual(turret.shots, 2)
        self.assertEqual(before - hp.total, 14 * turret.shots)
        while run.frame * 16 < sample[2]:
            run.step({})
        self.assertFalse(fight.is_stunned(torb))
        self.assertIs(fight.turrets[torb.entity], turret)

    def damaged_turret_with_hammer_equipped(self):
        fixture, run, torb, mei, _ = self.arena()
        fight = run.match.combat
        x, y, z = torb.mover.position
        distant = (x + 100, y, z + 100)
        fixture.place(mei, distant)
        run.step({torb: (16384, 4000, 8)})
        fixture.advance(run, torb, mei, 225)
        turret = fight.turrets[torb.entity]
        hp = fight.vitals[turret.actor.entity]
        x, y, z = turret.actor.position
        fixture.place(torb, (x - 1.6, y, z))
        fixture.place(mei, (x, y, z + 6))
        icicle_aim = aim(fight._eye(mei), (x, y + .6, z))
        for _ in range(65):
            run.step({mei: (*icicle_aim, 2)})
            if hp.total <= 195:
                break
        self.assertLessEqual(hp.total, 195)
        fixture.place(mei, distant)
        run.frame += 1
        torb.take_commands([Command(run.frame, action=2)])
        fixture.advance(run, torb, mei, 45)
        return fixture, run, torb, mei, turret, hp, distant

    def test_spray_freeze_cancels_pending_repair_and_fresh_swing_recovers(self):
        fixture, run, torb, mei, turret, hp, distant = self.damaged_turret_with_hammer_equipped()
        match = run.match
        fight = match.combat
        x, y, z = turret.actor.position
        before = hp.total
        clear = match.collision_world
        divider = x - .8
        match.collision_world = World(
            box(x - 25, x + 25, y - 1, y, z - 25, z + 25)
            + wall(divider, z - 15, divider, z + 15, y0=y, y1=y + 4)
        )
        fixture.place(mei, (x - 1.6, y, z + 3))
        for _ in range(140):
            fixture.step(run, torb, mei, defense=1)
            sample = fight.frost.sample(torb, run.frame)
            if sample is not None and sample[0] >= .96:
                break
        self.assertIsNotNone(sample)
        self.assertGreaterEqual(sample[0], .96)
        self.assertFalse(fight.is_stunned(torb))
        repair_aim = aim(fight._eye(torb), (x, y + .4, z))
        spray_aim = fixture.body_aim(run, mei, torb)
        run.step({torb: (*repair_aim, 1), mei: (*spray_aim, 1)})
        contact = fight.hammer_contacts[torb.body]
        self.assertLess(torb.body_script.component.now, contact.begin)
        match.collision_world = clear
        for _ in range(20):
            if fight.is_stunned(torb):
                break
            run.step({torb: (*repair_aim, 1), mei: (*spray_aim, 1)})
        self.assertTrue(fight.is_stunned(torb))
        self.assertTrue(contact.cancelled)
        self.assertFalse(contact.repaired)
        fixture.place(mei, distant)
        thaw = fight.frost.sample(torb, run.frame)[2]
        while (run.frame + 1) * 16 < thaw:
            run.step({torb: (*repair_aim, 1)})
            self.assertEqual(hp.total, before)
        run.step({})
        self.assertFalse(fight.is_stunned(torb))
        self.assertEqual(hp.total, before)
        run.step({torb: (*repair_aim, 1)})
        for _ in range(35):
            run.step({torb: (*repair_aim, 0)})
        self.assertEqual(hp.total, before + 50)

    def test_one_spray_pierces_two_enemies_once_each(self):
        fixture, run, torb, mei, other = self.arena()
        run.match.switch_hero(other, torb.hero)
        x, y, z = torb.mover.position
        fixture.place(other, (x, y, z - 1.3))
        fixture.advance(run, torb, mei, 20)
        fixture.step(run, torb, mei, defense=1)
        fixture.advance(run, torb, mei, 21)
        for target in (torb, other):
            self.assertAlmostEqual(fixture.health(run, target).total, 248.075)
            sample = run.match.combat.frost.sample(target, run.frame)
            self.assertAlmostEqual(sample[0], .2)

    def test_friendly_range_and_wall_reject_frost(self):
        for obstacle in ("team", "range", "wall"):
            with self.subTest(obstacle=obstacle):
                fixture, run, torb, mei, _ = self.arena(15 if obstacle == "range" else 3)
                if obstacle == "team":
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    mei.team = torb.team
                if obstacle == "wall":
                    x, y, z = torb.mover.position
                    run.match.collision_world = World(
                        box(x - 25, x + 25, y - 1, y, z - 25, z + 25)
                        + wall(x - 6, z + 1.5, x + 6, z + 1.5, y0=y, y1=y + 4)
                    )
                fixture.advance(run, torb, mei, 40, defense=1)
                self.assertEqual(fixture.health(run, torb).total, 250)
                self.assertFalse(run.match.combat.frost.samples)

    def test_allied_winston_shell_blocks_spray_and_frost(self):
        fixture, run, torb, mei, _ = self.arena(7)
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        mei.team = 1 - torb.team
        hero = 0x02E0000000000009
        winston = run.match.add_player(8, "Winston", hero, torb.team, False)
        in_world(run.match, winston)
        run.players.append(winston)
        run.match.switch_hero(winston, heroes()[hero])
        x, y, z = torb.mover.position
        fixture.place(winston, (x + 2, y, z))
        fixture.advance(run, torb, mei, 25)
        run.step({winston: (0, 0, 16)})
        fixture.advance(run, torb, mei, 15)
        shell, = run.match.combat.world_bubbles.actors.values()
        fixture.step(run, torb, mei, defense=1)
        fixture.advance(run, torb, mei, 22)
        self.assertEqual(shell.health, 697.25)
        self.assertEqual(fixture.health(run, torb).total, 250)
        self.assertFalse(run.match.combat.frost.samples)

    def test_replacing_a_frosted_body_drops_progress(self):
        fixture, run, torb, mei, _ = self.arena()
        fight = run.match.combat
        fixture.step(run, torb, mei, defense=1)
        fixture.advance(run, torb, mei, 20)
        old_body = torb.body
        self.assertIn(old_body, fight.frost.samples)
        run.match.switch_hero(torb, torb.hero)
        self.assertNotEqual(torb.body, old_body)
        self.assertNotIn(old_body, fight.frost.samples)
        self.assertNotIn(old_body, fight.frost.sources)
        self.assertIsNone(fight.frost.sample(torb, run.frame))

    def test_spray_slows_overloaded_movement_and_preserves_remaining_armour(self):
        fixture, run, torb, mei, _ = self.arena()
        fight = run.match.combat
        fixture.step(run, torb, mei, fire=16)
        fixture.advance(run, torb, mei, 30)
        hp = fixture.health(run, torb)
        self.assertEqual(hp.total, 350)
        baseline = torb.mover.values.run_forward
        self.assertGreater(baseline, torb.mover.data.run_forward)
        fixture.step(run, torb, mei, defense=1)
        fixture.advance(run, torb, mei, 20)
        self.assertAlmostEqual(hp.total, 348.075)
        self.assertAlmostEqual(fight.frost.sample(torb, run.frame)[0], .2)
        self.assertAlmostEqual(torb.mover.values.run_forward, baseline * .8, places=5)
        fixture.advance(run, torb, mei, 68)
        self.assertIsNone(fight.frost.sample(torb, run.frame))
        self.assertAlmostEqual(torb.mover.values.run_forward, baseline, places=5)
        self.assertAlmostEqual(hp.total, 348.075)
