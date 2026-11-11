"""Actual Blizzard launch, storm, and Torbjorn control."""

import os
import unittest
from dataclasses import replace

import test_game_torbjorn_bubbles as bubble_tests
import test_game_torbjorn_frost as frost_tests
from test_game_combat import aim
from test_game_mover import box, wall

from ow174.game import mei_blizzard, mei_frost, stats
from ow174.game.collision import World
from ow174.game.content import heroes
from ow174.game.world import ARMOUR_POOL, HEALTH_POOL


@unittest.skipUnless(all(os.environ.get(name) for name in (
    "ORPHEUS_TORBJORN_GRAPH_BUNDLE", "ORPHEUS_PROJECTILE_TAGS",
)), "needs prepared combat inputs")
class TorbjornBlizzardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        frost_tests.TorbjornFrostTests.setUpClass()
        cls.addClassCleanup(frost_tests.TorbjornFrostTests.doClassCleanups)

    def arena(self):
        fixture, run, torb, mei, other = frost_tests.TorbjornFrostTests().arena()
        x, y, z = torb.mover.position
        fixture.place(torb, (x + 2, y, z + 3))
        return fixture, run, torb, mei, other, (x, y, z - 2)

    def cast(self, run, mei, point, *, charged=True):
        if charged:
            mei.body_script.set_ult_charge(mei.body_script.component.entity_var(338).value())
        direction = aim(run.match.combat._eye(mei), point)
        for frame in range(32):
            run.step({mei: (*direction, 32 if frame == 0 else 0)})

    def land(self, run, mei, point):
        self.cast(run, mei, point)
        fight = run.match.combat
        for _ in range(130):
            if fight.blizzard.areas:
                break
            run.step({})
        area, = fight.blizzard.areas.values()
        self.assertAlmostEqual(area.position[1], point[1])
        self.assertEqual(area.ends - area.starts, 4250)
        self.assertFalse(any(r.definition == mei_blizzard.DEFINITION for r in fight.rockets))
        return area

    @staticmethod
    def until(run, ms, controls=None):
        while run.frame * 16 < ms:
            run.step(controls or {})

    def test_actual_charge_landing_damage_freeze_thaw_and_expiry(self):
        fixture, run, torb, mei, _, point = self.arena()
        fight = run.match.combat
        self.cast(run, mei, point, charged=False)
        self.assertFalse(fight.blizzard.areas or fight.rockets)
        area = self.land(run, mei, point)
        self.assertEqual(mei.body_script.component.entity_var(342).value(), 0)
        hp = fixture.health(run, torb)
        self.until(run, area.starts - 16)
        self.assertEqual(hp.total, 250)
        self.until(run, area.starts + 32)
        self.assertLess(hp.total, 250)
        self.assertAlmostEqual(fight.frost.sample(torb, run.frame)[0], .5)
        self.until(run, area.starts + 2500)
        self.assertFalse(fight.is_stunned(torb))
        self.until(run, area.starts + 2632)
        self.assertTrue(fight.is_stunned(torb))
        sample = fight.frost.sample(torb, run.frame)
        self.assertEqual(sample[2] - sample[1], 1300)
        self.until(run, sample[2] - 16, {torb: (0, 0, 1 | 8 | 16 | 32)})
        self.assertFalse(torb.body_script.component.held)
        self.assertFalse(fight.turrets or fight.molten_pools)
        self.until(run, sample[2] + 16)
        self.assertFalse(fight.is_stunned(torb))
        self.until(run, area.ends + 16)
        self.assertFalse(fight.blizzard.areas)
        self.assertAlmostEqual(hp.total, 165, places=4)

    def test_blizzard_freeze_arbitrates_with_flashbang_and_sleep_dart(self):
        ana = next(hero for hero in heroes().values() if hero.name == "Ana")
        for control, cast_at in (("flash", 2448), ("early_sleep", 2208), ("late_sleep", 2272)):
            with self.subTest(control=control):
                fixture, run, torb, mei, caster, point = self.arena()
                fight = run.match.combat
                sleep = control != "flash"
                self.assertTrue(run.match.switch_hero(caster, ana if sleep else heroes()[0x02E0000000000042]))
                x, y, z = torb.mover.position
                fixture.place(caster, (x + 3, y, z))
                fixture.advance(run, torb, mei, 35)
                area = self.land(run, mei, point)
                self.until(run, area.starts + cast_at)
                direction = aim(fight._eye(caster), (x, y + 1, z))
                run.step({caster: (*direction, 8 if sleep else 16)})
                before = fixture.health(run, torb).total
                for _ in range(50):
                    if caster.stats.value(stats.HERO_DAMAGE) > 0:
                        break
                    run.step({caster: (*direction, 0)})
                self.assertGreater(caster.stats.value(stats.HERO_DAMAGE), 0)
                self.assertLess(fixture.health(run, torb).total, before)
                self.assertTrue(fight.is_stunned(torb))
                impact = run.frame
                if sleep:
                    self.assertEqual(impact * 16 <= area.starts + 2600, control == "early_sleep")
                    deadline, floor = fight.sleep[torb.body]
                    self.assertEqual(floor, impact + 94)
                    self.assertEqual(deadline, floor)  # Storm damage wakes only at the guaranteed floor.
                else:
                    flash_end = fight.stuns[torb.body]
                    self.assertEqual(flash_end, impact + 50)

                self.until(run, area.starts + 2632)
                frost = fight.frost.sample(torb, run.frame)
                if sleep:
                    self.assertIsNone(frost)
                    self.assertEqual(fight.sleep[torb.body], (floor, floor))
                    self.assertEqual(torb.body_script.component.entity_var(2427).value(), 0)
                    end = floor
                else:
                    self.assertEqual(frost[2] - frost[1], 1300)
                    self.assertNotIn(torb.body, fight.stuns)
                    end = (frost[2] + 15) // 16
                    self.assertGreater(end, flash_end)
                self.assertIn(area.entity, fight.blizzard.areas)
                hp = fixture.health(run, torb)
                after_control = hp.total
                while run.frame < end - 1:
                    fixture.step(run, torb, mei, fire=1)
                    self.assertTrue(fight.is_stunned(torb))
                    self.assertFalse(torb.body_script.component.held)
                self.assertLess(hp.total, after_control)
                mei_hp = fixture.health(run, mei)
                before = mei_hp.total
                fixture.step(run, torb, mei, fire=1)
                self.assertFalse(fight.is_stunned(torb))
                fixture.advance(run, torb, mei, 30)
                self.assertEqual(before - mei_hp.total, 70)

    def test_overload_expires_during_blizzard_freeze_and_damage_spends_base_armour(self):
        fixture, run, torb, mei, _, point = self.arena()
        fight = run.match.combat
        hp = fixture.health(run, torb)
        run.step({torb: (0, 0, 16)})
        self.until(run, (run.frame + 70) * 16)
        self.assertEqual(hp.over_armour.amount, 100)
        area = self.land(run, mei, point)
        damage_before = mei.stats.value(stats.HERO_DAMAGE)

        self.until(run, area.starts + 2632)
        self.assertTrue(fight.is_stunned(torb))
        frost = fight.frost.sample(torb, run.frame)
        self.assertEqual(hp.now[ARMOUR_POOL], 50)
        self.assertAlmostEqual(hp.over_armour.amount, 100 - .02 * (area.last_ms - area.starts))
        self.assertAlmostEqual(mei.stats.value(stats.HERO_DAMAGE) - damage_before,
                               100 - hp.over_armour.amount, places=4)

        while hp.over_armour.source is not None and run.frame * 16 < frost[2]:
            run.step({torb: (0, 0, 16)})
        self.assertIsNone(hp.over_armour.source)
        self.assertTrue(fight.is_stunned(torb))
        self.assertEqual(fight.frost.sample(torb, run.frame), frost)
        self.assertEqual(hp.now[HEALTH_POOL], 200)
        armour = hp.now[ARMOUR_POOL]
        run.step({torb: (0, 0, 16)})
        self.assertAlmostEqual(hp.now[ARMOUR_POOL], armour - .32)
        self.assertIsNone(hp.over_armour.source)
        self.until(run, area.ends + 16)
        self.assertFalse(fight.is_stunned(torb))
        self.assertIsNone(hp.over_armour.source)
        self.assertAlmostEqual(hp.now[HEALTH_POOL], 200)
        self.assertAlmostEqual(mei.stats.value(stats.HERO_DAMAGE) - damage_before, 85, places=4)

    @unittest.skipUnless(os.environ.get("ORPHEUS_NPC_CATALOG_DIR"), "needs five-NPC catalog")
    def test_five_npcs_trade_fire_with_overload_inside_allied_blizzard(self):
        from pathlib import Path

        from ow174.game import npc

        catalog = npc.load_catalog(Path(os.environ["ORPHEUS_NPC_CATALOG_DIR"]) / "pve_npcs_174.json")
        damage = {"Trooper": 15, "Enforcer": 25, "Sniper": 95, "Assassin": 20, "Heavy Assault": 2.5}
        self.assertEqual({definition.name for definition in catalog.values()}, damage.keys())
        for definition in catalog.values():
            with self.subTest(npc=definition.name):
                self.exercise_npc_counterfire_in_blizzard(definition, damage[definition.name])

    def exercise_npc_counterfire_in_blizzard(self, definition, incoming):
        from ow174.game import npc, npc_control

        fixture, run, torb, mei, _, point = self.arena()
        match = run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        mei.team = torb.team
        for viewer in match.players:
            viewer.client.bot_stream = lambda *args: None
        area = self.land(run, mei, point)
        x, y, z = torb.mover.position
        bot = npc.spawn(match, definition, (x, y, z + 1.75), team=1 - torb.team, yaw=-32768)
        bot_hp = fight.vitals[bot.entity]
        hp = fixture.health(run, torb)
        self.until(run, area.starts + 944)
        run.step({torb: (*fixture.body_aim(run, torb, bot), 16)})
        self.until(run, area.starts + 1504)
        self.assertAlmostEqual(bot_hp.maximum - bot_hp.total, 30.24)
        self.assertEqual(hp.total, 350)
        source = hp.over_armour.source
        base = dict(hp.now)
        self.assertEqual(npc_control.start(match, torb), bot.entity)
        for _ in range(135):
            if hp.over_armour.amount < 100:
                break
            run.step({})
        self.assertTrue(bot.attack_input)
        self.assertEqual(hp.over_armour.source, source)
        self.assertEqual(hp.over_armour.amount, 100 - incoming)
        self.assertEqual(hp.now, base)
        self.assertIn(area.entity, fight.blizzard.areas)
        self.assertIsNone(fight.frost.sample(bot, run.frame))
        if definition.name == "Assassin":
            self.assertEqual(fight.pins[bot.entity], torb.body)
            self.assertTrue(fight.is_stunned(torb))
        npc_control.stop(match)
        self.assertNotIn(bot.entity, fight.pins)
        for _ in range(10):
            run.step({})
        self.assertFalse(fight.is_stunned(torb))
        run.step({torb: (*fixture.body_aim(run, torb, bot), 1)})
        for _ in range(24):
            run.step({})
        self.assertIn(area.entity, fight.blizzard.areas)
        self.assertIsNone(fight.frost.sample(bot, run.frame))
        self.assertEqual(torb.stats.value(stats.HERO_DAMAGE), 70)
        self.assertAlmostEqual(bot_hp.maximum - bot_hp.total, 70 + mei.stats.value(stats.HERO_DAMAGE))

    def test_landed_molten_keeps_burning_during_blizzard_freeze(self):
        fixture, run, torb, mei, _, point = self.arena()
        fight = run.match.combat
        area = self.land(run, mei, point)
        torb.body_script.set_ult_charge(torb.body_script.component.entity_var(338).value())
        run.step({torb: (16384, 4000, 32)})
        self.until(run, run.frame * 16 + 720)
        run.step({torb: (16384, 4000, 1)})
        for _ in range(100):
            if any(pool.phase == 1 for pool in fight.molten_pools.values()):
                break
            run.step({})
        pool, = fight.molten_pools.values()
        self.assertEqual(pool.phase, 1)
        self.assertFalse(fight.is_stunned(torb))

        self.until(run, area.starts + 2632)
        self.assertTrue(fight.is_stunned(torb))
        _, _, thaw, *_ = fight.frost.sample(torb, run.frame)
        hp = fixture.health(run, mei)
        before = hp.total
        self.assertEqual(before, hp.maximum)
        fixture.place(mei, pool.position)
        for _ in range(20):
            run.step({torb: (-16384, 4000, 1)})
            self.assertTrue(fight.is_stunned(torb))
            self.assertFalse(torb.body_script.component.held)
            self.assertEqual(list(fight.molten_pools.values()), [pool])
        self.assertLess(hp.total, before)
        self.assertIn((torb.entity, mei.body), fight.molten_burns)
        self.assertIn(area.entity, fight.blizzard.areas)
        self.until(run, thaw + 16)
        self.assertFalse(fight.is_stunned(torb))

    def test_deployed_turret_keeps_firing_during_blizzard_freeze(self):
        fixture, run, torb, mei, _, point = self.arena()
        fight = run.match.combat
        x, y, z = torb.mover.position
        position = tuple(mei.mover.position)
        distant = (x + 100, y, z + 100)
        fixture.place(mei, distant)
        run.step({torb: (16384, 4000, 8)})
        self.until(run, (run.frame + 225) * 16)
        turret = fight.turrets[torb.entity]
        fixture.place(mei, position)
        area = self.land(run, mei, point)
        fixture.place(mei, distant)
        self.until(run, area.starts + 2632)
        self.assertTrue(fight.is_stunned(torb))
        sample = fight.frost.sample(torb, run.frame)
        hp = fixture.health(run, mei)
        before = hp.total
        self.assertGreater(before, 56)
        shots = turret.shots

        fixture.place(mei, position)
        for _ in range(40):
            fixture.step(run, torb, mei, fire=1 | 8)
            self.assertTrue(fight.is_stunned(torb))
            self.assertFalse(torb.body_script.component.held)
            self.assertEqual(fight.frost.sample(torb, run.frame), sample)
            self.assertIs(fight.turrets[torb.entity], turret)
        self.assertGreaterEqual(turret.shots - shots, 2)
        self.assertEqual(before - hp.total, 14 * (turret.shots - shots))
        self.until(run, sample[2] + 16)
        self.assertFalse(fight.is_stunned(torb))
        self.assertIs(fight.turrets[torb.entity], turret)

    def test_blizzard_freeze_cancels_pending_repair_and_fresh_swing_recovers(self):
        case = frost_tests.TorbjornFrostTests()
        fixture, run, torb, mei, turret, hp, distant = case.damaged_turret_with_hammer_equipped()
        fight = run.match.combat
        x, y, z = turret.actor.position
        before = hp.total
        fixture.place(mei, (x - 5, y, z))
        area = self.land(run, mei, (x - 11, y, z))
        fixture.place(mei, distant)
        self.until(run, area.starts + 2500)
        self.assertIn(torb.body, area.frost_due)
        self.assertFalse(fight.is_stunned(torb))
        self.assertLess(hp.total, before)
        before = hp.total

        repair_aim = aim(fight._eye(torb), (x, y + .4, z))
        run.step({torb: (*repair_aim, 1)})
        contact = fight.hammer_contacts[torb.body]
        self.assertLess(torb.body_script.component.now, contact.begin)
        self.until(run, area.starts + 2632, {torb: (*repair_aim, 1)})
        self.assertTrue(fight.is_stunned(torb))
        self.assertTrue(contact.cancelled)
        self.assertFalse(contact.repaired)
        self.assertLess(hp.total, before)
        thaw = fight.frost.sample(torb, run.frame)[2]
        while (run.frame + 1) * 16 < thaw:
            before = hp.total
            run.step({torb: (*repair_aim, 1)})
            self.assertAlmostEqual(hp.total, before - mei_blizzard.DAMAGE_RATE * .016)
        run.step({})
        self.assertFalse(fight.is_stunned(torb))
        self.until(run, area.ends + 16)
        self.assertFalse(fight.blizzard.areas)
        before = hp.total
        run.step({torb: (*repair_aim, 1)})
        self.until(run, (run.frame + 35) * 16, {torb: (*repair_aim, 0)})
        self.assertEqual(hp.total, before + 50)

    def test_leaving_radius_retires_pending_frost_and_stops_damage(self):
        fixture, run, torb, mei, _, point = self.arena()
        area = self.land(run, mei, point)
        fight = run.match.combat
        self.until(run, area.starts + 240)
        self.assertIn(torb.body, area.frost_due)
        x, y, z = area.position
        fixture.place(torb, (x + 15, y, z))
        hp = fixture.health(run, torb)
        before = hp.total
        self.until(run, area.starts + 1500)
        self.assertEqual(hp.total, before)
        self.assertNotIn(torb.body, area.frost_due)
        self.assertIsNone(fight.frost.sample(torb, run.frame))
        fixture.place(torb, (x + 2, y, z))
        self.until(run, area.starts + 1540)
        self.assertLess(hp.total, before)
        self.assertAlmostEqual(fight.frost.sample(torb, run.frame)[0], .5)

    def test_wall_and_team_prevent_storm_contact(self):
        for obstacle in ("wall", "team"):
            with self.subTest(obstacle=obstacle):
                fixture, run, torb, mei, _, point = self.arena()
                area = self.land(run, mei, point)
                if obstacle == "team":
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    mei.team = torb.team
                else:
                    x, y, z = area.position
                    fixture.place(torb, (x + 4, y, z))
                    run.match.collision_world = World(
                        box(x - 25, x + 25, y - 1, y, z - 25, z + 25)
                        + wall(x + 2, z - 5, x + 2, z + 5, y0=y, y1=y + 4)
                    )
                self.until(run, area.starts + 600)
                self.assertEqual(fixture.health(run, torb).total, 250)
                self.assertFalse(run.match.combat.frost.samples)

    def test_projected_zarya_shield_absorbs_storm_until_its_phase_ends(self):
        fixture, run, torb, mei, zarya, point = self.arena()
        run.match.switch_hero(zarya, heroes()[0x02E0000000000068])
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        zarya.team = torb.team
        mei.team = 1 - torb.team
        area = self.land(run, mei, point)
        x, y, z = torb.mover.position
        fixture.place(zarya, (x - 3, y, z))
        bubble = bubble_tests.TorbjornBubbleTests().cast(fixture, run, zarya, torb)
        self.until(run, area.starts + 160)
        fight = run.match.combat
        hp = fixture.health(run, torb)
        self.assertLess(bubble.health, 200)
        self.assertEqual(hp.total, 250)
        self.assertIsNone(fight.frost.sample(torb, run.frame))
        self.until(run, area.starts + 2200)
        self.assertIsNone(fight.bubbles.for_target(torb))
        self.assertLess(hp.total, 250)
        self.assertIsNotNone(fight.frost.sample(torb, run.frame))

    def test_landed_storm_survives_caster_death_from_actual_rivet(self):
        fixture, run, torb, mei, _, point = self.arena()
        area = self.land(run, mei, point)
        fight = run.match.combat
        caster = fixture.health(run, mei)
        caster.damage(caster.total - 10)
        fixture.step(run, torb, mei, fire=1)
        self.until(run, area.starts + 220)
        self.assertTrue(caster.dead)
        self.assertIn(area.entity, fight.blizzard.areas)
        self.assertLess(fixture.health(run, torb).total, 250)

    def test_actual_genji_reflect_assigns_landed_storm_to_genji(self):
        fixture = frost_tests.defenses.TorbjornDefenseTests()
        run, torb, genji, mei = fixture.arena("Genji", 5)
        run.match.switch_hero(mei, heroes()[mei_frost.HERO])
        x, y, z = genji.mover.position
        fixture.place(mei, (x, y, z - 4))
        fixture.place(torb, (x + 3, y, z - 4))
        self.until(run, run.frame * 16 + 400)
        mei.body_script.set_ult_charge(mei.body_script.component.entity_var(338).value())
        direction = fixture.body_aim(run, mei, genji)
        for n in range(115):
            run.step({mei: (*direction, 32 if n == 0 else 0),
                      genji: (-32768, 3000, 16 if n == 0 else 0)})
            if run.match.combat.blizzard.areas:
                break
        area, = run.match.combat.blizzard.areas.values()
        self.assertIs(area.owner, genji)
        self.assertEqual(area.body, genji.body)
        self.assertLess(area.position[2], z)

    def test_retained_tick_gap_keeps_snowball_landing_and_storm_clock(self):
        samples = []
        for skip in (False, True):
            fixture, run, torb, mei, _, point = self.arena()
            self.cast(run, mei, point)
            run.step({})
            fight = run.match.combat
            self.assertTrue(any(r.definition == mei_blizzard.DEFINITION and r.last_tick is not None
                                for r in fight.rockets))
            self.assertFalse(fight.blizzard.areas)
            endpoint = run.frame + 35
            if skip:
                run.frame = endpoint
                run.match.update(endpoint * .016, endpoint)
            else:
                self.until(run, endpoint * 16)
            self.assertTrue(fight.blizzard.areas, skip)
            area, = fight.blizzard.areas.values()
            samples.append((area.position, area.starts, area.ends, area.last_ms,
                            fixture.health(run, torb).total))
        self.assertEqual(samples[0], samples[1])
