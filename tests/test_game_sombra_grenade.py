"""Biotic Grenade and actual Sombra health-pack interaction in a static match."""

import math
import os
import unittest
from dataclasses import replace
from pathlib import Path

import test_game_sombra_health_packs as packs_fixture
import test_game_sombra_sleep as sleep_fixture
from test_game_combat import SOLDIER, aim, in_world
from test_game_sombra_phases import MOIRA, REAPER

from ow174.game import combat, mover, npc, phase_defenses, reinhardt_barrier, shot_guards, translocators
from ow174.game.commands import FIRE
from ow174.game.content import heroes
from ow174.game.script import nodes
from ow174.game.world import HEALTH_POOL

ANA = sleep_fixture.ANA


class SombraGrenadeTests(unittest.TestCase):
    def arena(self, *, ally=False):
        f = sleep_fixture.SombraSleepTests().arena()
        match = f.run.match
        match.game_map = replace(match.game_map, free_for_all=False)
        f.a.team, f.b.team = 0, int(not ally)
        return f

    def join_ally(self, f, hero_id, position):
        match = f.run.match
        hero = heroes()[hero_id]
        player = match.add_player(len(f.run.players) + 1, hero.name, hero_id, f.a.team, False)
        in_world(match, player)
        f.run.players.append(player)
        self.assertTrue(match.switch_hero(player, hero))
        player.team = f.a.team
        player.mover.position[:] = position
        player.mover.body = None
        f.press(0, 35)
        return player

    def throw(self, run, ana, victim, *, change_team=False, held=None):
        fight = run.match.combat
        x, y, z = victim.mover.position
        angles = aim(fight._eye(ana), (x, y + 1, z))
        commands = dict(held or ())
        commands[ana] = (*angles, 16)
        run.step(commands)
        commands[ana] = (*angles, 0)
        flight = None
        for _ in range(80):
            if flight is None:
                flight = next((r for r in fight.rockets if r.definition == shot_guards.BIOTIC_GRENADE), None)
                if flight is not None and change_team:
                    ana.team = victim.team
            elif flight not in fight.rockets:
                return run.frame, flight
            run.step(commands)
        self.fail("Actual 0B97 grenade did not launch and land")

    def test_enemy_grenade_denies_area_healing_until_exact_expiry(self):
        for change_team in (False, True):
            with self.subTest(change_team=change_team):
                f = self.arena()
                fight = f.run.match.combat
                impact, grenade = self.throw(f.run, f.b, f.a, change_team=change_team)
                self.assertEqual(grenade.team, 1)
                self.assertAlmostEqual(grenade.gravity, 9.8, places=5)
                vitals = fight.vitals[f.a.body]
                self.assertEqual(vitals.total, 150)
                self.assertEqual(vitals.healing_boost_until, 0)
                deadline = impact + math.ceil(4 / combat.TICK)
                self.assertEqual(vitals.healing_block_until, deadline)
                point = tuple(f.a.mover.position)
                fight.heal_area(f.a, point, 1, 40)
                self.assertEqual(vitals.total, 150)
                f.press(0, deadline - f.run.frame - 1)
                fight.heal_area(f.a, point, 1, 40)
                self.assertEqual(vitals.total, 150)
                f.press(0)
                self.assertEqual(vitals.healing_block_until, 0)
                fight.heal_area(f.a, point, 1, 40)
                self.assertEqual(vitals.total, 190)

    def test_allied_grenade_heals_and_amplifies_followup_until_expiry(self):
        f = self.arena(ally=True)
        fight = f.run.match.combat
        self.assertEqual(f.health(f.a), 200)
        vitals = fight.vitals[f.a.body]
        vitals.damage(160)
        impact, _ = self.throw(f.run, f.b, f.a)
        self.assertEqual(vitals.total, 140)
        self.assertFalse(f.b.client.messages(combat.HIT))
        self.assertEqual(vitals.healing_block_until, 0)
        deadline = impact + math.ceil(4 / combat.TICK)
        self.assertEqual(vitals.healing_boost_until, deadline)
        point = tuple(f.a.mover.position)
        fight.heal_area(f.a, point, 1, 20)
        self.assertEqual(vitals.total, 170)
        f.press(0, deadline - f.run.frame)
        self.assertEqual(vitals.healing_boost_until, 0)
        fight.heal_area(f.a, point, 1, 20)
        self.assertEqual(vitals.total, 190)

    def test_projected_barrier_or_static_cover_prevents_damage_and_antiheal(self):
        for bubble in (False, True):
            with self.subTest(bubble=bubble):
                f = self.arena()
                match = f.run.match
                fight = match.combat
                if bubble:
                    hero = heroes()[reinhardt_barrier.ZARYA]
                    zarya = match.add_player(3, "Zarya", hero.guid, 0, False)
                    in_world(match, zarya)
                    f.run.players.append(zarya)
                    self.assertTrue(match.switch_hero(zarya, hero))
                    zarya.team = 0
                    zarya.mover.position[:] = (5, 0, 0)
                    zarya.mover.body = None
                    f.press(0, 30)
                    angles = sleep_fixture.SombraSleepTests.angles(f, zarya, f.a)
                    f.run.step({zarya: (*angles, 16)})
                    barrier, = fight.barriers.values()
                    self.assertIs(barrier.protected, f.a)
                else:
                    f.wall()
                self.throw(f.run, f.b, f.a)
                self.assertEqual(f.health(f.a), 200)
                self.assertEqual(fight.vitals[f.a.body].healing_block_until, 0)
                self.assertFalse(f.b.client.messages(combat.HIT))
                if bubble:
                    self.assertEqual(barrier.vitals.total, 150)
                    self.assertEqual(zarya.body_script.component.entity_var(87).value(), 10)

    def test_actual_phase_excludes_hostile_grenade_blast_but_keeps_allied_support(self):
        for hero_id in (REAPER, MOIRA):
            for ally in (False, True):
                with self.subTest(hero=hero_id, ally=ally):
                    f = self.arena(ally=ally)
                    fight = f.run.match.combat
                    target = self.join_ally(f, hero_id, (0, 0, 2))
                    before = f.health(target)
                    vitals = fight.vitals[target.body]
                    if ally:
                        vitals.damage(120)
                    f.run.step({target: (0, 0, 8)})
                    self.assertTrue(phase_defenses.active(target))
                    self.throw(f.run, f.b, f.a)
                    self.assertTrue(phase_defenses.active(target))
                    self.assertEqual(vitals.total, before - 20 if ally else before)
                    self.assertEqual(vitals.healing_block_until, 0)
                    self.assertEqual(vitals.healing_boost_until > f.run.frame, ally)
                    self.assertEqual(f.health(f.a), 200 if ally else 150)

    def test_actual_phase_cleanses_antiheal_on_entry_and_restores_healing(self):
        for hero_id in (REAPER, MOIRA):
            with self.subTest(hero=hero_id):
                f = self.arena()
                fight = f.run.match.combat
                target = self.join_ally(f, hero_id, (0, 0, 2))
                before = f.health(target)
                self.throw(f.run, f.b, target)
                vitals = fight.vitals[target.body]
                self.assertEqual(vitals.total, before - 50)
                deadline = vitals.healing_block_until
                self.assertGreater(deadline, f.run.frame)
                point = tuple(target.mover.position)
                fight.heal_area(target, point, 1, 20)
                self.assertEqual(vitals.total, before - 50)
                f.run.step({target: (0, 0, 8)})
                self.assertTrue(phase_defenses.active(target))
                self.assertEqual(vitals.healing_block_until, 0)
                self.assertLess(f.run.frame, deadline)
                fight.heal_area(target, point, 1, 20)
                self.assertEqual(vitals.total, before - 30)

    def test_actual_projected_barrier_cleanses_sombras_antiheal_and_sleep(self):
        f = self.arena()
        fight = f.run.match.combat
        zarya = self.join_ally(f, reinhardt_barrier.ZARYA, (5, 0, 0))
        ally = self.join_ally(f, ANA, (-6, 0, 0))
        self.throw(f.run, ally, f.a)
        vitals = fight.vitals[f.a.body]
        boost = vitals.healing_boost_until
        self.assertGreater(boost, f.run.frame)
        self.throw(f.run, f.b, f.a)
        self.assertEqual(vitals.total, 150)
        deadline = vitals.healing_block_until
        f.press(0, 35)
        angles = sleep_fixture.SombraSleepTests.angles(f, f.b, f.a)
        f.run.step({f.b: (*angles, sleep_fixture.DART)})
        for _ in range(65):
            if fight.is_stunned(f.a):
                break
            f.run.step({f.b: (*angles, 0)})
        self.assertTrue(fight.is_stunned(f.a))
        self.assertEqual(vitals.total, 145)
        self.assertEqual(vitals.healing_block_until, deadline)
        self.assertGreater(deadline, f.run.frame)
        aim_at_sombra = sleep_fixture.SombraSleepTests.angles(f, zarya, f.a)
        f.run.step({zarya: (*aim_at_sombra, 16)})
        shield, = fight.barriers.values()
        self.assertIs(shield.protected, f.a)
        self.assertEqual(shield.vitals.total, 200)
        self.assertEqual((vitals.sleep_until, vitals.sleep_wake_floor, vitals.healing_block_until), (0, 0, 0))
        self.assertEqual(vitals.healing_boost_until, boost)
        self.assertFalse(fight.is_stunned(f.a))
        self.assertLess(f.run.frame, min(deadline, boost))
        fight.heal_area(f.a, tuple(f.a.mover.position), 1, 20)
        self.assertEqual(vitals.total, 175)
        f.press(FIRE)
        self.assertEqual(f.weapon().vars[53].value(), 59)
        self.assertEqual(f.health(f.b), 192)

    def test_actual_personal_barrier_cleanses_antiheal_without_restoring_past_health(self):
        f = self.arena()
        fight = f.run.match.combat
        zarya = self.join_ally(f, reinhardt_barrier.ZARYA, (0, 0, 2))
        before = f.health(zarya)
        self.throw(f.run, f.b, zarya)
        vitals = fight.vitals[zarya.body]
        self.assertEqual(vitals.total, before - 50)
        self.assertGreater(vitals.healing_block_until, f.run.frame)
        f.run.step({zarya: (0, 0, 8)})
        barrier, = fight.barriers.values()
        self.assertIs(barrier.protected, zarya)
        self.assertEqual(vitals.healing_block_until, 0)
        self.assertEqual(vitals.total, before - 50)
        fight.heal_area(zarya, tuple(zarya.mover.position), 1, 20)
        self.assertEqual(vitals.total, before - 30)

    def test_projected_cleanse_keeps_independent_flashbang_stun(self):
        f = self.arena()
        fight = f.run.match.combat
        zarya = self.join_ally(f, reinhardt_barrier.ZARYA, (5, 0, 0))
        mccree = self.join_ally(f, 0x02E0000000000042, (3, 0, 0))
        mccree.team = 1
        self.assertIsNotNone(sleep_fixture.SombraSleepTests().dart(f))
        vitals = fight.vitals[f.a.body]
        self.assertGreater(vitals.sleep_until, f.run.frame)
        angles = sleep_fixture.SombraSleepTests.angles(f, mccree, f.a)
        f.run.step({mccree: (*angles, 16)})
        for _ in range(45):
            if f.a.body in fight.stuns:
                break
            f.run.step({mccree: (*angles, 0)})
        deadline = fight.stuns[f.a.body]
        self.assertEqual(vitals.total, 170)
        self.assertGreater(vitals.sleep_until, f.run.frame)
        angles = sleep_fixture.SombraSleepTests.angles(f, zarya, f.a)
        f.run.step({zarya: (*angles, 16)})
        shield, = fight.barriers.values()
        self.assertIs(shield.protected, f.a)
        self.assertEqual(vitals.sleep_until, 0)
        self.assertEqual(fight.stuns[f.a.body], deadline)
        f.press(FIRE)
        self.assertTrue(fight.is_stunned(f.a))
        self.assertEqual(f.weapon().vars[53].value(), 60)
        f.press(0, deadline - f.run.frame - 1)
        f.press(FIRE)
        self.assertFalse(fight.is_stunned(f.a))
        self.assertEqual(f.weapon().vars[53].value(), 59)

    def test_reinhardt_barrier_blocks_grenade_blast_even_when_the_impact_breaks_it(self):
        for case in ("front", "break", "rear"):
            with self.subTest(case=case):
                f = self.arena()
                match = f.run.match
                fight = match.combat
                defenders = []
                for slot, (hero_id, team, position) in enumerate((
                    (reinhardt_barrier.HERO, 0, (0, 0, 2)),
                    (SOLDIER, 0, (2.5, 0, 4.5)),
                    (SOLDIER, 1, (-2, 0, 4.5)),
                ), 3):
                    hero = heroes()[hero_id]
                    player = match.add_player(slot, hero.name, hero_id, team, False)
                    in_world(match, player)
                    f.run.players.append(player)
                    self.assertTrue(match.switch_hero(player, hero))
                    player.team = team
                    player.mover.position[:] = position
                    player.mover.body = None
                    defenders.append(player)
                rein, flank, ally = defenders
                f.press(0, 35)
                before = f.health(rein)
                self.assertEqual(f.health(ally), 200)
                fight.vitals[ally.body].damage(130)
                facing = -32768 if case == "rear" else 0
                for _ in range(24):
                    f.run.step({rein: (facing, 0, 2)})
                    if fight.barriers:
                        break
                barrier, = fight.barriers.values()
                if not barrier.mesh:
                    self.skipTest("requires the extracted Reinhardt shield mesh")
                if case == "break":
                    reinhardt_barrier.damage(barrier, 1575)
                self.throw(f.run, f.b, f.a, held={rein: (facing, 0, 2)})
                self.assertEqual(f.health(f.a), 200)
                self.assertEqual(fight.vitals[f.a.body].healing_block_until, 0)
                self.assertEqual(f.health(flank), 150)
                self.assertGreater(fight.vitals[flank.body].healing_block_until, f.run.frame)
                self.assertEqual(f.health(ally), 170)
                self.assertGreater(fight.vitals[ally.body].healing_boost_until, f.run.frame)
                if case == "rear":
                    self.assertEqual(f.health(rein), before - 45)
                    self.assertGreater(fight.vitals[rein.body].healing_block_until, f.run.frame)
                    self.assertEqual(barrier.vitals.total, 1600)
                else:
                    self.assertEqual(f.health(rein), before)
                    self.assertEqual(fight.vitals[rein.body].healing_block_until, 0)
                    self.assertEqual(barrier.vitals.total, 0 if case == "break" else 1550)

    def test_antiheal_leaves_hacked_pack_ready_until_first_legal_pickup(self):
        if not Path(os.environ.get("ORPHEUS_HEALTH_PACK_DATA", "")).is_file():
            self.skipTest("requires the extracted health-pack placement bundle")
        packs = packs_fixture.SombraHealthPackTests()
        run, player, pack, pickup = packs.arena(0x0400000000000FC3, names=("Sombra", "Ana"))
        match = run.match
        ana = run.players[1]
        self.assertTrue(match.switch_hero(ana, heroes()[ANA]))
        match.game_map = replace(match.game_map, free_for_all=False)
        player.team, ana.team = 0, 1
        x, y, z = pack.position
        ana.mover.position[:] = (x, y, z - 12)
        ana.mover.body = None
        packs.cast(run, player, pack, 85)
        self.assertEqual(pack.component.entity_var(6396).value().id, player.body)
        vitals = packs.vitals(run, player)
        vitals.damage(100)
        impact, _ = self.throw(run, ana, player)
        self.assertEqual(vitals.total, 50)
        deadline = impact + math.ceil(4 / combat.TICK)
        self.assertEqual(vitals.healing_block_until, deadline)
        packs.place(player, run, pack, 0)
        while run.frame < deadline - 1:
            run.step({})
            self.assertEqual(vitals.total, 50)
            self.assertEqual(pickup.vars[467].value(), 1)
        run.step({})
        self.assertEqual(vitals.total, 125)
        self.assertTrue(pickup.state(2).active)
        self.assertLess(pickup.vars[467].value(), 1)

    def test_allied_grenade_boost_applies_to_actual_hacked_small_pack(self):
        if not Path(os.environ.get("ORPHEUS_HEALTH_PACK_DATA", "")).is_file():
            self.skipTest("requires the extracted health-pack placement bundle")
        packs = packs_fixture.SombraHealthPackTests()
        run, player, pack, pickup = packs.arena(0x0400000000000FC3, names=("Sombra", "Ana"))
        match = run.match
        ana = run.players[1]
        self.assertTrue(match.switch_hero(ana, heroes()[ANA]))
        match.game_map = replace(match.game_map, free_for_all=False)
        player.team = ana.team = 0
        x, y, z = pack.position
        ana.mover.position[:] = (x, y, z - 12)
        ana.mover.body = None
        packs.cast(run, player, pack, 85)
        self.assertEqual(pack.component.entity_var(6396).value().id, player.body)
        self.throw(run, ana, player)
        vitals = packs.vitals(run, player)
        self.assertEqual(vitals.total, 200)
        self.assertGreater(vitals.healing_boost_until, run.frame)
        vitals.damage(150)
        packs.place(player, run, pack, 0)
        run.step({})
        self.assertEqual(vitals.total, 162.5)
        self.assertTrue(pickup.state(2).active)

    def test_hack_and_emp_block_fresh_grenade_then_restore_ana_input(self):
        for button in (2, 32):
            with self.subTest(button=button):
                f = self.arena()
                fight = f.run.match.combat
                if button == 32:
                    f.a.body_script.set_ult_charge(1400)
                    f.press(32)
                    f.press(0, 45)
                else:
                    f.press(2, 85)
                effect, = fight.hacks[f.b.body]
                self.assertEqual(effect.application.source_entity(), f.a.body)
                aim_at_sombra = sleep_fixture.SombraSleepTests.angles(f, f.b, f.a)
                f.run.step({f.b: (*aim_at_sombra, 16)})
                for _ in range(65):
                    self.assertFalse(any(r.definition == shot_guards.BIOTIC_GRENADE for r in fight.rockets))
                    f.run.step({f.b: (*aim_at_sombra, 0)})
                self.assertEqual(f.health(f.a), 200)
                f.press(0, effect.until_tick - f.run.frame + 1)
                self.assertNotIn(f.b.body, fight.hacks)
                self.throw(f.run, f.b, f.a)
                self.assertEqual(f.health(f.a), 150)
                self.assertGreater(fight.vitals[f.a.body].healing_block_until, f.run.frame)

    def test_grenade_lands_after_a_late_sombra_emp(self):
        f = self.arena()
        fight = f.run.match.combat
        f.a.body_script.set_ult_charge(1400)
        aim_at_sombra = sleep_fixture.SombraSleepTests.angles(f, f.b, f.a)
        f.press(32)
        f.press(0, 5)
        f.run.step({f.b: (*aim_at_sombra, 16)})
        rocket, = fight.rockets
        self.assertEqual(rocket.definition, shot_guards.BIOTIC_GRENADE)
        for _ in range(45):
            if f.b.body in fight.hacks:
                break
            f.run.step({f.b: (*aim_at_sombra, 0)})
        effect, = fight.hacks[f.b.body]
        self.assertEqual(effect.application.source_entity(), f.a.body)
        self.assertIn(rocket, fight.rockets)
        self.assertEqual(f.health(f.a), 200)
        f.run.step({f.b: (*aim_at_sombra, 0)})
        self.assertEqual(f.health(f.a), 150)
        self.assertEqual(fight.hacks[f.b.body], [effect])
        self.assertGreater(fight.vitals[f.a.body].healing_block_until, f.run.frame)
        self.assertNotIn(rocket, fight.rockets)

    def test_pistol_kill_prevents_grenade_release_but_does_not_retire_a_launched_throw(self):
        for launched in (False, True):
            with self.subTest(launched=launched):
                f = self.arena()
                fight = f.run.match.combat
                self.assertEqual(f.health(f.b), 200)
                enemy = fight.vitals[f.b.body]
                enemy.now[HEALTH_POOL] = 8
                aim_at_sombra = sleep_fixture.SombraSleepTests.angles(f, f.b, f.a)
                aim_at_ana = sleep_fixture.SombraSleepTests.angles(f, f.a, f.b)
                if launched:
                    f.run.step({f.b: (*aim_at_sombra, 16)})
                    self.assertTrue(any(r.definition == shot_guards.BIOTIC_GRENADE for r in fight.rockets))
                f.run.step({f.a: (*aim_at_ana, FIRE), f.b: (*aim_at_sombra, 0 if launched else 16)})
                self.assertEqual(enemy.killer, f.a.body)
                self.assertEqual(enemy.death_tick, f.run.frame)
                f.press(0, 35)
                self.assertEqual(f.health(f.a), 150 if launched else 200)
                hit = f.b.client.messages(combat.HIT)
                self.assertEqual(len(hit), int(launched))
                self.assertEqual(fight.vitals[f.a.body].healing_block_until > f.run.frame, launched)

    def test_grenade_during_paid_emp_windup_allows_pulse_only_on_survival(self):
        for lethal in (False, True):
            with self.subTest(lethal=lethal):
                f = self.arena()
                fight = f.run.match.combat
                self.assertEqual(f.health(f.a), 200)
                vitals = fight.vitals[f.a.body]
                if lethal:
                    vitals.now[HEALTH_POOL] = 50
                script = f.a.body_script
                script.set_ult_charge(1400)
                emp = next(i for i in script.component.instances.values() if i.graph.index == 0x0B31)
                pulse = emp.node_state(emp.graph.node(40))
                angles = sleep_fixture.SombraSleepTests.angles(f, f.b, f.a)
                f.run.step({f.b: (*angles, 16)})
                for _ in range(14):
                    f.run.step({f.b: (*angles, 0)})
                f.run.step({f.a: (0, 0, 32), f.b: (*angles, 0)})
                self.assertTrue(emp.state(1).flags & nodes.Ability.RUNNING)
                self.assertEqual(script.component.entity_var(342).value(), 0)
                self.assertEqual(vitals.total, 50 if lethal else 200)
                f.run.step({f.b: (*angles, 0)})
                self.assertEqual(vitals.total, 0 if lethal else 150)
                self.assertEqual(pulse.counter, 0)
                self.assertEqual(vitals.healing_block_until > f.run.frame, not lethal)
                if lethal:
                    self.assertEqual(vitals.death_tick, f.run.frame)
                    self.assertEqual(vitals.killer, f.b.body)
                f.press(0, 80)
                self.assertEqual(pulse.counter, 0 if lethal else 1)
                self.assertEqual(script.component.entity_var(342).value(), 0)
                self.assertEqual(f.b.body in fight.hacks, not lethal)

    def test_grenade_released_on_paid_emp_frame_resolves_before_world_only_pulse(self):
        for lethal in (False, True):
            with self.subTest(lethal=lethal):
                f = self.arena()
                match = f.run.match
                self.assertEqual(f.health(f.a), 200)
                vitals = match.combat.vitals[f.a.body]
                if lethal:
                    vitals.now[HEALTH_POOL] = 50
                cast, pulse = sleep_fixture.SombraSleepTests().emp_at_ana_release(f, 16)
                at = cast + 60
                match.update(at * combat.TICK, at)
                self.assertEqual(vitals.total, 0 if lethal else 150)
                self.assertEqual(pulse.counter, 0 if lethal else 1)
                if lethal:
                    self.assertEqual(vitals.death_tick, cast + 16)
                    self.assertEqual(vitals.killer, f.b.body)
                    self.assertNotIn(f.b.body, match.combat.hacks)
                else:
                    self.assertEqual(vitals.healing_block_until, cast + 16 + math.ceil(4 / combat.TICK))
                    effect, = match.combat.hacks[f.b.body]
                    self.assertEqual(effect.until_tick, cast + 334)

    def test_actual_translocator_recall_preserves_the_grenade_antiheal_deadline(self):
        if not translocators.DATA_PATH.is_file():
            self.skipTest("requires the extracted Translocator entity definition")
        f = self.arena()
        fight = f.run.match.combat
        f.press(16, 20)
        f.press(0, 180)
        beacon, = fight.translocators.entries.values()
        self.assertTrue(beacon.settled)
        impact, _ = self.throw(f.run, f.b, f.a)
        vitals = fight.vitals[f.a.body]
        deadline = impact + math.ceil(4 / combat.TICK)
        self.assertEqual(vitals.total, 150)
        self.assertEqual(vitals.healing_block_until, deadline)
        self.assertEqual(fight.vitals[beacon.entity].total, 5)
        f.press(16)
        f.press(0, 45)
        self.assertNotIn(beacon.entity, fight.translocators.entries)
        self.assertAlmostEqual(f.a.mover.position[2], beacon.position[2], places=2)
        self.assertEqual(vitals.healing_block_until, deadline)
        point = tuple(f.a.mover.position)
        fight.heal_area(f.a, point, 1, 40)
        self.assertEqual(vitals.total, 150)
        f.press(0, deadline - f.run.frame)
        fight.heal_area(f.a, point, 1, 40)
        self.assertEqual(vitals.total, 190)

    def test_lethal_grenade_and_body_replacement_do_not_leave_antiheal(self):
        for lethal in (False, True):
            with self.subTest(lethal=lethal):
                f = self.arena()
                match = f.run.match
                fight = match.combat
                old = f.a.body
                self.assertEqual(f.health(f.a), 200)
                vitals = fight.vitals[old]
                if lethal:
                    vitals.now[HEALTH_POOL] = 50
                impact, _ = self.throw(f.run, f.b, f.a)
                if lethal:
                    self.assertEqual(vitals.death_tick, impact)
                    self.assertEqual(vitals.killer, f.b.body)
                    self.assertEqual(vitals.healing_block_until, 0)
                else:
                    self.assertGreater(vitals.healing_block_until, impact)
                self.assertTrue(match.switch_hero(f.a, f.a.hero))
                self.assertNotEqual(f.a.body, old)
                self.assertNotIn(old, fight.vitals)
                self.assertEqual(f.health(f.a), 200)
                new = fight.vitals[f.a.body]
                self.assertEqual((new.healing_block_until, new.healing_boost_until), (0, 0))

    def test_actual_grenade_applies_antiheal_to_sombra_and_all_five_npcs_in_one_blast(self):
        runtime = os.environ.get("ORPHEUS_NPC_RUNTIME_DIR")
        if not runtime:
            self.skipTest("requires the actual NPC catalog")
        catalog = npc.load_catalog(Path(runtime) / "data" / "pve_npcs_174.json")
        f = self.arena()
        match = f.run.match
        fight = match.combat
        for player in f.run.players:
            player.client.bot_stream = lambda body: None
        positions = ((-1.4, 0, 0.5), (1.4, 0, 0.5), (-1.7, 0, 2), (1.7, 0, 2), (0, 0, 2.5))
        bots = []
        for definition, position in zip(catalog.values(), positions, strict=True):
            self.assertIn(definition.body, mover.loaded_mover_table())
            bots.append(npc.spawn(match, definition, position, team=0))
        f.press(0, 5)
        self.assertEqual(f.health(f.a), 200)
        entities = (f.a.body, *(bot.entity for bot in bots))
        before = {entity: fight.vitals[entity].total for entity in entities}
        impact, _ = self.throw(f.run, f.b, f.a)
        deadline = impact + math.ceil(4 / combat.TICK)
        hit_ids = {hit["+0x78"]["+0x0"]["+0x0"] for hit in f.b.client.messages(combat.HIT)}
        self.assertEqual(hit_ids, set(entities))
        for entity in entities:
            self.assertEqual(fight.vitals[entity].total, before[entity] - 50)
            self.assertEqual(fight.vitals[entity].healing_block_until, deadline)
        fight.heal_area(f.a, (0, 0, 1), 5, 20)
        for entity in entities:
            self.assertEqual(fight.vitals[entity].total, before[entity] - 50)
        f.press(0, deadline - f.run.frame)
        fight.heal_area(f.a, (0, 0, 1), 5, 20)
        for entity in entities:
            self.assertEqual(fight.vitals[entity].total, before[entity] - 30)

    def test_enemy_antiheal_precedes_allied_grenade_boost_until_its_own_expiry(self):
        f = self.arena()
        match = f.run.match
        fight = match.combat
        ally = match.add_player(3, "Allied Ana", ANA, 0, False)
        in_world(match, ally)
        f.run.players.append(ally)
        self.assertTrue(match.switch_hero(ally, heroes()[ANA]))
        ally.team = 0
        ally.mover.position[:] = (5, 0, 0)
        ally.mover.body = None
        f.press(0, 30)
        self.throw(f.run, f.b, f.a)
        vitals = fight.vitals[f.a.body]
        block = vitals.healing_block_until
        self.assertEqual(vitals.total, 150)
        self.throw(f.run, ally, f.a)
        self.assertEqual(vitals.total, 150)
        self.assertEqual(vitals.healing_block_until, block)
        boost = vitals.healing_boost_until
        self.assertGreater(boost, block)
        f.press(0, block - f.run.frame)
        fight.heal_area(f.a, tuple(f.a.mover.position), 1, 20)
        self.assertEqual(vitals.total, 180)
        self.assertEqual(vitals.healing_boost_until, boost)

    def test_genji_returns_grenade_antiheal_and_sigma_absorbs_it_before_explosion(self):
        for hero_id, button in ((shot_guards.GENJI, 16), (shot_guards.SIGMA, 8)):
            with self.subTest(hero=hero_id):
                f = self.arena()
                match = f.run.match
                fight = match.combat
                hero = heroes()[hero_id]
                defender = match.add_player(3, "Defender", hero_id, 0, False)
                in_world(match, defender)
                f.run.players.append(defender)
                self.assertTrue(match.switch_hero(defender, hero))
                defender.team = 0
                defender.mover.position[:] = (0, 0, 3)
                defender.mover.body = None
                f.press(0, 30)
                before = f.health(defender)
                f.run.step({defender: (0, 0, button)})
                guard = shot_guards.active_guard(defender, fight.tick * combat.TICK * 1000)
                self.assertIsNotNone(guard)
                self.throw(f.run, f.b, f.a)
                self.assertEqual(f.health(f.a), 200)
                self.assertEqual(f.health(defender), before)
                self.assertEqual(fight.vitals[f.a.body].healing_block_until, 0)
                self.assertFalse(f.b.client.messages(combat.HIT))
                if hero_id == shot_guards.GENJI:
                    hit, = defender.client.messages(combat.HIT)
                    self.assertEqual(hit["+0x78"]["+0x0"]["+0x0"], f.b.body)
                    self.assertEqual(f.health(f.b), 150)
                    self.assertGreater(fight.vitals[f.b.body].healing_block_until, f.run.frame)
                else:
                    self.assertEqual(guard.instance.vars[21312].value(), 50)
                    self.assertEqual(f.health(f.b), 200)
