"""Actual Ana Sleep Dart and Sombra inputs in a static server match."""

import math
import os
import unittest
from dataclasses import replace
from pathlib import Path

import test_game_sombra_assassin as assassin_fixture
import test_game_sombra_combat as sombra
import test_game_sombra_npc as npc_fixture
from test_game_combat import SOLDIER, in_world
from test_game_sombra_phases import MOIRA, REAPER

from ow174.game import combat, mover, npc, phase_defenses, reinhardt_barrier, shot_guards, translocators
from ow174.game.commands import FIRE, Command
from ow174.game.content import heroes
from ow174.game.script import expr, nodes
from ow174.game.world import HEALTH_POOL, OP_DESTROY

ANA = 0x02E000000000013B
DART = 8


class SombraSleepTests(unittest.TestCase):
    def arena(self):
        f = sombra.SombraCombatTests()
        f.setUp()
        match = f.run.match
        self.assertTrue(match.switch_hero(f.b, heroes()[ANA]))
        f.b.mover.position[:] = (0, 0, 8)
        f.b.mover.body = None
        f.press(0, 50)
        f.a.body_script.ult_seconds = 0
        return f

    @staticmethod
    def angles(f, caster, victim):
        x, y, z = victim.mover.position
        return sombra.aim(f.run.match.combat._eye(caster), (x, y + 1, z))

    def dart(self, f):
        angles = self.angles(f, f.b, f.a)
        f.run.step({f.b: (*angles, DART)})
        for _ in range(65):
            if f.b.client.messages(combat.HIT):
                return f.run.frame
            f.run.step({f.b: (*angles, 0)})
        return None

    def emp_at_ana_release(self, f, button):
        match = f.run.match
        script = f.a.body_script
        script.set_ult_charge(1400)
        yaw, pitch = self.angles(f, f.b, f.a)
        for delay in range(45):
            f.run.frame += 1
            at = f.run.frame
            match.update(at * combat.TICK, at)
            f.b.take_commands([Command(at, yaw=yaw, pitch=pitch, buttons=button if not delay else 0)])
            if match.combat.rockets:
                f.a.take_commands([Command(at, buttons=0x20)])
                emp = next(i for i in script.component.instances.values() if i.graph.index == 0x0B31)
                self.assertTrue(emp.state(1).flags & nodes.Ability.RUNNING)
                self.assertEqual(script.component.entity_var(342).value(), 0)
                return at, emp.node_state(emp.graph.node(40))
            f.a.take_commands([Command(at)])
        self.fail("Ana did not release the requested projectile")

    def test_dart_sleeps_sombra_and_denies_movement_and_abilities_until_expiry(self):
        f = self.arena()
        fight = f.run.match.combat
        script = f.a.body_script
        script.set_ult_charge(1400)
        contact = self.dart(f)
        self.assertIsNotNone(contact)
        vitals = fight.vitals[f.a.body]
        self.assertEqual(vitals.total, 195)
        deadline = contact + math.ceil(5 / combat.TICK)
        self.assertEqual(vitals.sleep_until, deadline)
        self.assertEqual(vitals.sleep_wake_floor, contact + math.ceil(1.5 / combat.TICK))
        position = tuple(f.a.mover.position)
        yaw, pitch = self.angles(f, f.a, f.b)
        for _ in range(32):
            f.run.frame += 1
            f.run.match.update(f.run.frame * combat.TICK, f.run.frame)
            f.a.take_commands([Command(f.run.frame, forward=127, yaw=yaw, pitch=pitch,
                                       buttons=FIRE | 2 | 8 | 16 | 32 | sombra.MELEE | sombra.RELOAD)])
        self.assertEqual(tuple(f.a.mover.position), position)
        self.assertEqual(f.weapon().vars[53].value(), 60)
        self.assertEqual(script.component.entity_var(342).value(), 1400)
        self.assertFalse(fight.hacks)
        self.assertFalse(fight.translocators.entries)
        self.assertFalse(f.camo().state(13).flags & nodes.Ability.RUNNING)
        f.press(0, deadline - f.run.frame - 1)
        self.assertTrue(fight.is_stunned(f.a))
        f.run.step({f.a: (yaw, pitch, FIRE)})
        self.assertFalse(fight.is_stunned(f.a))
        self.assertEqual((vitals.sleep_until, vitals.sleep_wake_floor), (0, 0))
        self.assertEqual(f.weapon().vars[53].value(), 59)
        self.assertEqual(f.health(f.b), 192)

    def test_real_rifle_damage_shortens_sleep_without_releasing_early(self):
        for late in (False, True):
            with self.subTest(late=late):
                f = self.arena()
                match = f.run.match
                fight = match.combat
                shooter = match.add_player(3, "Soldier", SOLDIER, 1, False)
                in_world(match, shooter)
                f.run.players.append(shooter)
                self.assertTrue(match.switch_hero(shooter, heroes()[SOLDIER]))
                shooter.mover.position[:] = (3, 0, 0)
                shooter.mover.body = None
                f.press(0, 30)
                contact = self.dart(f)
                self.assertIsNotNone(contact)
                vitals = fight.vitals[f.a.body]
                floor = vitals.sleep_wake_floor
                if late:
                    f.press(0, floor - f.run.frame)
                yaw, pitch = self.angles(f, shooter, f.a)
                f.run.step({shooter: (yaw, pitch, FIRE)})
                self.assertEqual(vitals.total, 175)
                wake = max(floor, f.run.frame + math.ceil(0.5 / combat.TICK))
                self.assertEqual(vitals.sleep_until, wake)
                self.assertEqual(wake == floor, not late)
                f.press(0, wake - f.run.frame - 1)
                self.assertTrue(fight.is_stunned(f.a))
                f.press(0)
                self.assertFalse(fight.is_stunned(f.a))

    def test_wall_team_and_actual_projected_barrier_prevent_sleep_and_damage(self):
        for case in ("wall", "ally", "bubble"):
            with self.subTest(case=case):
                f = self.arena()
                match = f.run.match
                fight = match.combat
                match.game_map = replace(match.game_map, free_for_all=False)
                f.a.team, f.b.team = 0, int(case != "ally")
                if case == "wall":
                    f.wall()
                if case == "bubble":
                    hero = heroes()[reinhardt_barrier.ZARYA]
                    zarya = match.add_player(3, "Zarya", hero.guid, 0, False)
                    in_world(match, zarya)
                    f.run.players.append(zarya)
                    self.assertTrue(match.switch_hero(zarya, hero))
                    zarya.team = 0
                    zarya.mover.position[:] = (4, 0, 0)
                    zarya.mover.body = None
                    f.press(0, 30)
                    f.run.step({zarya: (*self.angles(f, zarya, f.a), 16)})
                    bubble, = fight.barriers.values()
                    self.assertIs(bubble.protected, f.a)
                self.assertIsNone(self.dart(f))
                self.assertEqual(f.health(f.a), 200)
                self.assertFalse(fight.is_stunned(f.a))
                self.assertEqual(fight.vitals[f.a.body].sleep_until, 0)
                if case == "bubble":
                    self.assertEqual(bubble.vitals.total, 195)
                    self.assertEqual(zarya.body_script.component.entity_var(87).value(), 1)

    def test_lethal_dart_and_hero_replacement_clear_body_sleep(self):
        for lethal in (False, True):
            with self.subTest(lethal=lethal):
                f = self.arena()
                match = f.run.match
                fight = match.combat
                self.assertEqual(f.health(f.a), 200)
                vitals = fight.vitals[f.a.body]
                if lethal:
                    vitals.now[HEALTH_POOL] = 5
                impact = self.dart(f)
                self.assertIsNotNone(impact)
                if lethal:
                    self.assertEqual(vitals.death_tick, impact)
                    self.assertEqual(vitals.killer, f.b.body)
                    self.assertEqual((vitals.sleep_until, vitals.sleep_wake_floor), (0, 0))
                    self.assertFalse(fight.is_stunned(f.a))
                else:
                    self.assertTrue(fight.is_stunned(f.a))
                    old = f.a.body
                    self.assertTrue(match.switch_hero(f.a, f.a.hero))
                    self.assertNotEqual(f.a.body, old)
                    self.assertNotIn(old, fight.vitals)
                    self.assertFalse(fight.is_stunned(f.a))

    def test_actual_hack_and_emp_deny_ana_dart_until_silence_expires(self):
        for attack in (2, 32):
            with self.subTest(attack=attack):
                f = self.arena()
                fight = f.run.match.combat
                if attack == 32:
                    f.a.body_script.set_ult_charge(1400)
                    f.press(32)
                    f.press(0, 45)
                else:
                    f.press(2, 85)
                effect, = fight.hacks[f.b.body]
                self.assertEqual(effect.application.source_entity(), f.a.body)
                self.assertIsNone(self.dart(f))
                component = f.b.body_script.component
                root = next(i for i in component.instances.values() if i.graph.index == 0x0BDD)
                self.assertFalse(root.state(2).flags & nodes.Ability.RUNNING)
                self.assertEqual(root.state(2).remaining(), 0)
                self.assertEqual(f.health(f.a), 200)
                f.press(0, effect.until_tick - f.run.frame + 1)
                self.assertNotIn(f.b.body, fight.hacks)
                self.assertIsNotNone(self.dart(f))
                self.assertEqual(f.health(f.a), 195)
                self.assertTrue(fight.is_stunned(f.a))

    def test_ana_dart_and_sombra_emp_have_independent_deadlines_for_all_five_npcs(self):
        runtime = os.environ.get("ORPHEUS_NPC_RUNTIME_DIR")
        if not runtime:
            self.skipTest("requires the actual NPC catalog")
        catalog = npc.load_catalog(Path(runtime) / "data" / "pve_npcs_174.json")
        for definition in catalog.values():
            with self.subTest(npc=definition.name):
                f = self.arena()
                match = f.run.match
                fight = match.combat
                match.game_map = replace(match.game_map, free_for_all=False)
                f.a.team = f.b.team = 0
                for player in f.run.players:
                    player.client.bot_stream = lambda body: None
                self.assertIn(definition.body, mover.loaded_mover_table())
                bot = npc.spawn(match, definition, (0, 0, 4), team=1)
                f.press(0, 5)
                f.a.body_script.set_ult_charge(1400)
                f.press(32)
                f.press(0, 40)
                effect, = fight.hacks[bot.entity]
                self.assertEqual(effect.application.source_entity(), f.a.body)
                vitals = fight.vitals[bot.entity]
                before = vitals.total
                angles = sombra.aim(fight._eye(f.b), (0, 1, 4))
                f.run.step({f.b: (*angles, DART)})
                for _ in range(65):
                    if fight.is_stunned(bot):
                        break
                    f.run.step({f.b: (*angles, 0)})
                self.assertTrue(fight.is_stunned(bot))
                self.assertEqual(vitals.total, before - 5)
                original_end = f.run.frame + math.ceil(5 / combat.TICK)
                self.assertEqual(vitals.sleep_until, original_end)
                floor = vitals.sleep_wake_floor
                yaw, pitch = sombra.aim(fight._eye(f.a), (0, 1, 4))
                f.run.step({f.a: (yaw, pitch, FIRE)})
                self.assertLess(vitals.total, before - 5)
                self.assertEqual(vitals.sleep_until, floor)
                self.assertLess(floor, effect.until_tick)
                f.press(0, floor - f.run.frame)
                self.assertFalse(fight.is_stunned(bot))
                self.assertEqual(fight.hacks[bot.entity], [effect])
                self.assertTrue(bot.body_script.component.entity_var(888).value())
                f.press(0, effect.until_tick - f.run.frame)
                self.assertNotIn(bot.entity, fight.hacks)

    def test_nonlethal_dart_interrupts_paid_emp_before_pulse(self):
        baseline = self.arena()
        started = baseline.run.frame
        contact = self.dart(baseline)
        self.assertIsNotNone(contact)
        flight_frames = contact - started
        self.assertGreater(flight_frames, 2)
        f = self.arena()
        fight = f.run.match.combat
        script = f.a.body_script
        script.set_ult_charge(1400)
        emp = next(i for i in script.component.instances.values() if i.graph.index == 0x0B31)
        pulse = emp.node_state(emp.graph.node(40))
        angles = self.angles(f, f.b, f.a)
        f.run.step({f.b: (*angles, DART)})
        for _ in range(flight_frames - 3):
            f.run.step({f.b: (*angles, 0)})
        f.run.step({f.a: (0, 0, 32), f.b: (*angles, 0)})
        self.assertTrue(emp.state(1).flags & nodes.Ability.RUNNING)
        self.assertEqual(script.component.entity_var(342).value(), 0)
        self.assertEqual(f.health(f.a), 200)
        f.run.step({f.b: (*angles, 0)})
        self.assertEqual(f.health(f.a), 195)
        self.assertTrue(fight.is_stunned(f.a))
        self.assertFalse(emp.state(1).flags & nodes.Ability.RUNNING)
        f.press(0, 85)
        self.assertEqual(pulse.counter, 0)
        self.assertNotIn(f.b.body, fight.hacks)
        self.assertEqual(script.component.entity_var(342).value(), 0)

    def test_dart_released_on_paid_emp_frame_interrupts_during_world_only_update(self):
        f = self.arena()
        match = f.run.match
        cast, pulse = self.emp_at_ana_release(f, DART)
        at = cast + 60
        match.update(at * combat.TICK, at)
        self.assertEqual(f.health(f.a), 195)
        vitals = match.combat.vitals[f.a.body]
        self.assertEqual(vitals.sleep_until, cast + 8 + math.ceil(5 / combat.TICK))
        self.assertEqual(pulse.counter, 0)
        self.assertNotIn(f.b.body, match.combat.hacks)
        self.assertEqual(f.a.body_script.component.entity_var(342).value(), 0)

    def test_dart_sleep_interrupts_held_hack_with_the_damage_cooldown(self):
        f = self.arena()
        match = f.run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        f.a.team, f.b.team = 0, 1
        target = match.add_player(3, "Hack target", SOLDIER, 1, False)
        in_world(match, target)
        f.run.players.append(target)
        self.assertTrue(match.switch_hero(target, heroes()[SOLDIER]))
        target.team = 1
        target.mover.position[:] = (5, 0, 0)
        target.mover.body = None
        f.press(0, 30)
        dart = self.angles(f, f.b, f.a)
        aim = self.angles(f, f.a, target)
        f.run.step({f.b: (*dart, DART)})
        for _ in range(10):
            f.run.step({f.b: (*dart, 0)})
        for _ in range(10):
            f.run.step({f.b: (*dart, 0), f.a: (*aim, 2)})
        hack = f.weapon().state(53)
        self.assertTrue(hack.flags & nodes.Ability.RUNNING)
        self.assertEqual(f.weapon().vars[8909].value().id, target.body)
        for _ in range(35):
            if fight.is_stunned(f.a):
                break
            f.run.step({f.b: (*dart, 0), f.a: (*aim, 2)})
        self.assertEqual(f.health(f.a), 195)
        self.assertTrue(fight.is_stunned(f.a))
        self.assertFalse(hack.flags & nodes.Ability.RUNNING)
        self.assertAlmostEqual(hack.remaining(), 2, delta=0.04)
        self.assertFalse(fight.hacks)
        self.assertEqual(f.health(target), 200)

    def test_emp_after_launch_does_not_remove_the_in_flight_dart(self):
        f = self.arena()
        fight = f.run.match.combat
        f.a.body_script.set_ult_charge(1400)
        aim = self.angles(f, f.b, f.a)
        f.run.step({f.b: (*aim, DART)})
        # At 8 m the dart launches before this delayed EMP pulse and hits one frame after it.
        for _ in range(3):
            f.run.step({f.b: (*aim, 0)})
        f.run.step({f.a: (0, 0, 32), f.b: (*aim, 0)})
        for _ in range(35):
            if f.b.body in fight.hacks:
                break
            f.run.step({f.b: (*aim, 0)})
        effect, = fight.hacks[f.b.body]
        self.assertEqual(effect.application.source_entity(), f.a.body)
        self.assertEqual(f.health(f.a), 200)
        rocket, = fight.rockets
        self.assertEqual(rocket.definition, shot_guards.SLEEP_DART)
        f.run.step({f.b: (*aim, 0)})
        self.assertEqual(f.health(f.a), 195)
        self.assertTrue(fight.is_stunned(f.a))
        self.assertEqual(fight.hacks[f.b.body], [effect])
        self.assertNotIn(rocket, fight.rockets)

    def test_dart_hits_distant_cloaked_sombra_and_ends_camo(self):
        f = self.arena()
        fight = f.run.match.combat
        f.press(8)
        f.press(0, 65)
        camo = f.camo().state(13)
        tracker = f.camo().node_state(f.camo().graph.node(128))
        self.assertTrue(camo.flags & nodes.Ability.RUNNING)
        self.assertTrue(tracker.active)
        self.assertFalse(tracker.targets)
        self.assertIsNotNone(self.dart(f))
        self.assertEqual(f.health(f.a), 195)
        self.assertTrue(fight.is_stunned(f.a))
        f.press(0, 60)
        self.assertFalse(camo.flags & nodes.Ability.RUNNING)
        self.assertFalse(tracker.active)
        self.assertGreater(camo.remaining(), 0)

    def test_sleep_preserves_settled_translocator_and_denies_recall_until_wake(self):
        if not translocators.DATA_PATH.is_file():
            self.skipTest("requires the extracted Translocator entity definition")
        f = self.arena()
        fight = f.run.match.combat
        f.press(16, 20)
        f.press(0, 180)
        beacon, = fight.translocators.entries.values()
        self.assertTrue(beacon.settled)
        self.assertIsNotNone(self.dart(f))
        vitals = fight.vitals[f.a.body]
        before = tuple(f.a.mover.position)
        f.press(16, 12)
        self.assertEqual(tuple(f.a.mover.position), before)
        self.assertEqual(fight.vitals[beacon.entity].total, 5)
        self.assertEqual(fight.vitals[beacon.entity].sleep_until, 0)
        f.press(0, vitals.sleep_until - f.run.frame + 2)
        self.assertFalse(fight.is_stunned(f.a))
        self.assertIn(beacon.entity, fight.translocators.entries)
        self.assertEqual(tuple(f.a.mover.position), before)
        f.press(16)
        f.press(0, 45)
        self.assertNotIn(beacon.entity, fight.translocators.entries)
        self.assertAlmostEqual(f.a.mover.position[2], beacon.position[2], places=2)
        destroys = [u for u in f.a.client.entities if u.entity == beacon.entity and u.op == OP_DESTROY]
        self.assertEqual(len(destroys), 1)

    def test_actual_dart_destroys_settled_beacon_without_sleep_or_hero_credit(self):
        if not translocators.DATA_PATH.is_file():
            self.skipTest("requires the extracted Translocator entity definition")
        f = self.arena()
        fight = f.run.match.combat
        f.press(16, 20)
        f.press(0, 180)
        beacon, = fight.translocators.entries.values()
        self.assertTrue(beacon.settled)
        self.assertEqual(fight.vitals[beacon.entity].total, 5)
        aim = sombra.aim(fight._eye(f.b), beacon.position)
        f.run.step({f.b: (*aim, DART)})
        for _ in range(75):
            if beacon.entity not in fight.translocators.entries:
                break
            f.run.step({f.b: (*aim, 0)})
        self.assertNotIn(beacon.entity, fight.translocators.entries)
        self.assertNotIn(beacon.entity, fight.vitals)
        self.assertFalse(beacon.body_script.component.instances)
        self.assertFalse(f.b.client.messages(combat.HIT))
        self.assertFalse(f.b.client.messages(combat.KILL_NOTICE))
        self.assertEqual(f.health(f.a), 200)
        self.assertFalse(fight.is_stunned(f.a))
        self.assertFalse(fight.respawns)
        destroys = [u for u in f.a.client.entities if u.entity == beacon.entity and u.op == OP_DESTROY]
        self.assertEqual(len(destroys), 1)

    def test_actual_deflect_returns_sleep_and_grasp_absorbs_the_dart(self):
        for hero_id, button in ((shot_guards.GENJI, 16), (shot_guards.SIGMA, 8)):
            with self.subTest(hero=hero_id):
                f = self.arena()
                match = f.run.match
                fight = match.combat
                match.game_map = replace(match.game_map, free_for_all=False)
                f.a.team, f.b.team = 0, 1
                hero = heroes()[hero_id]
                defender = match.add_player(3, "Defender", hero.guid, 0, False)
                in_world(match, defender)
                f.run.players.append(defender)
                self.assertTrue(match.switch_hero(defender, hero))
                defender.team = 0
                defender.mover.position[:] = (0, 0, 3)
                defender.mover.body = None
                f.press(0, 30)
                before = f.health(defender)
                angles = self.angles(f, f.b, f.a)
                f.run.step({f.b: (*angles, DART), defender: (0, 0, button)})
                guard = shot_guards.active_guard(defender, fight.tick * combat.TICK * 1000)
                self.assertIsNotNone(guard)
                for _ in range(65):
                    f.run.step({f.b: (*angles, 0), defender: (0, 0, 0)})
                self.assertFalse(f.b.client.messages(combat.HIT))
                self.assertEqual(f.health(f.a), 200)
                self.assertEqual(f.health(defender), before)
                self.assertFalse(fight.is_stunned(f.a))
                self.assertFalse(fight.is_stunned(defender))
                if hero_id == shot_guards.GENJI:
                    hit, = defender.client.messages(combat.HIT)
                    self.assertEqual(hit["+0x78"]["+0x0"]["+0x0"], f.b.body)
                    self.assertEqual(f.health(f.b), 195)
                    self.assertTrue(fight.is_stunned(f.b))
                else:
                    self.assertEqual(guard.instance.vars[21312].value(), 5)
                    self.assertFalse(fight.is_stunned(f.b))

    def test_actual_wraith_or_fade_lets_dart_pass_to_sombra(self):
        for hero_id in (REAPER, MOIRA):
            for phase in (False, True):
                with self.subTest(hero=hero_id, phase=phase):
                    f = self.arena()
                    match = f.run.match
                    fight = match.combat
                    match.game_map = replace(match.game_map, free_for_all=False)
                    f.a.team, f.b.team = 0, 1
                    hero = heroes()[hero_id]
                    defender = match.add_player(3, "Phase defender", hero.guid, 0, False)
                    in_world(match, defender)
                    f.run.players.append(defender)
                    self.assertTrue(match.switch_hero(defender, hero))
                    defender.team = 0
                    defender.mover.position[:] = (0, 0, 4)
                    defender.mover.body = None
                    f.press(0, 30)
                    before = f.health(defender)
                    angles = self.angles(f, f.b, f.a)
                    f.run.step({f.b: (*angles, DART), defender: (0, 0, 8 if phase else 0)})
                    self.assertEqual(phase_defenses.active(defender), phase)
                    for _ in range(65):
                        if f.b.client.messages(combat.HIT):
                            break
                        f.run.step({f.b: (*angles, 0)})
                    hit, = f.b.client.messages(combat.HIT)
                    victim = f.a if phase else defender
                    self.assertEqual(hit["+0x78"]["+0x0"]["+0x0"], victim.body)
                    self.assertEqual(f.health(f.a), 195 if phase else 200)
                    self.assertEqual(f.health(defender), before if phase else before - 5)
                    self.assertEqual(fight.is_stunned(f.a), phase)
                    self.assertEqual(fight.is_stunned(defender), not phase)


class SombraSleepRescueTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        npc_fixture.SombraActualNpcTests.setUpClass.__func__(cls)

    @classmethod
    def restore_graphs(cls, path):
        npc_fixture.SombraActualNpcTests.restore_graphs(path)

    def test_actual_ally_dart_releases_assassin_pin_and_heavy_carry(self):
        for key in ("4", "5"):
            with self.subTest(npc=self.catalog[key].name):
                f, bot = npc_fixture.SombraActualNpcTests.make(self, key, distance=1.5)
                match = f.run.match
                fight = match.combat
                match.game_map = replace(match.game_map, free_for_all=False)
                f.a.team = f.b.team = 0
                f.b.spawned = True
                self.assertTrue(match.switch_hero(f.b, heroes()[ANA]))
                f.b.mover.position[:] = (3, 0, -2)
                f.b.mover.body = None
                f.press(0, 50)
                if key == "4":
                    assassin_fixture.SombraAssassinTests().engage(f, bot)
                else:
                    bot.yaw = -32768
                    bot.body_script.component.edges([(35, True)])
                    f.press(0, 62)
                self.assertEqual(fight.pins.get(bot.entity), f.a.body)
                self.assertTrue(fight.is_stunned(f.a))
                before = f.health(f.a)
                bot_vitals = fight.vitals[bot.entity]
                before_npc = bot_vitals.total
                for frame in range(55):
                    x, y, z = bot.position
                    vx, _, vz = bot.velocity
                    aim = sombra.aim(fight._eye(f.b), (x + vx * 0.065, y + 1, z + vz * 0.065))
                    f.run.step({f.b: (*aim, DART if frame == 0 else 0)})
                    if bot_vitals.sleep_until:
                        break
                self.assertGreater(bot_vitals.sleep_until, f.run.frame)
                self.assertEqual(bot_vitals.total, before_npc - 5)
                hit, = f.b.client.messages(combat.HIT)
                self.assertEqual(hit["+0x78"]["+0x0"]["+0x0"], bot.entity)
                f.press(0, 3)
                self.assertNotIn(bot.entity, fight.pins)
                self.assertFalse(fight.is_stunned(f.a))
                self.assertTrue(fight.is_stunned(bot))
                if key == "5":
                    component = f.a.body_script.component
                    self.assertFalse(any(i.graph.index == 0x1407 for i in component.instances.values()))
                    bot_component = bot.body_script.component
                    special = next(i for i in bot_component.instances.values() if i.graph.index == 0x1408)
                    self.assertEqual(special.vars[59].value(), 0)
                    self.assertGreater(special.vars[61].value(), 0)
                f.press(8)
                f.press(0, 60)
                self.assertTrue(f.camo().state(13).flags & nodes.Ability.RUNNING)
                self.assertEqual(f.health(f.a), before)
                if key == "5":
                    f.press(0, bot_vitals.sleep_until - f.run.frame + 1)
                    self.assertFalse(fight.is_stunned(bot))
                    self.assertNotIn(bot.entity, fight.pins)
                    self.assertEqual(special.vars[59].value(), 0)

    def test_actual_dart_cancels_moving_sniper_special_and_enters_landing_recovery(self):
        f, bot = npc_fixture.SombraActualNpcTests.make(self, "3", distance=6)
        match = f.run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        f.a.team = f.b.team = 0
        f.b.spawned = True
        self.assertTrue(match.switch_hero(f.b, heroes()[ANA]))
        f.b.mover.position[:] = (0, 0, 1)
        f.b.mover.body = None
        f.press(0, 70)
        component = bot.body_script.component
        special = next(i for i in component.instances.values() if i.graph.index == 0x142C)
        # Use a resolved destination to exercise the authored moving and landing phases.
        destination = expr.Vec3(0, 0, 30)
        special.write((expr.INSTANCE, 1220), destination)
        special.write((expr.INSTANCE, 431), destination)
        special.follow(special.graph.node(39), "m_onFinishedPlug")
        f.press(0, 54)
        movement = special.node_state(special.graph.node(16))
        self.assertTrue(movement.active)
        self.assertGreater(bot.position[2], 6)
        vitals = fight.vitals[bot.entity]
        before = vitals.total
        for frame in range(60):
            x, y, z = bot.position
            aim = sombra.aim(fight._eye(f.b), (x, y + 1, z))
            f.run.step({f.b: (*aim, DART if frame == 0 else 0)})
            if vitals.sleep_until:
                break
        self.assertTrue(fight.is_stunned(bot))
        self.assertEqual(vitals.total, before - 5)
        stop = bot.position
        self.assertLess(stop[2], 30)
        f.press(0, 10)
        self.assertFalse(movement.active)
        self.assertEqual(special.vars[59].value(), 2)
        self.assertEqual(bot.position, stop)
        self.assertEqual(bot.velocity, (0, 0, 0))
        f.press(0, vitals.sleep_until - f.run.frame + 1)
        self.assertFalse(fight.is_stunned(bot))
        self.assertEqual(special.vars[59].value(), 0)
        self.assertEqual(bot.position, stop)
