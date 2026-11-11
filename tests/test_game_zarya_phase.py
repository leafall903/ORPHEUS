"""Actual commands through defensive branch timing and Zarya combat."""

import unittest
from itertools import product

import test_game_zarya_npcs as npc_fixtures
import test_game_zarya_ultimate as fixtures
from test_game_combat import aim

from ow174.game import combat, npc, npc_control, phase
from ow174.game.commands import CROUCH, JUMP, Command
from ow174.game.content import heroes
from ow174.game.script.nodes import Ability

PROFILES = (
    (0x02E0000000000002, 0x0020, 18, 187, 212, 8),
    (0x02E00000000000DD, 0x05FE, 3, 249, 265, 12),
    (0x02E00000000001A2, 0x0F7C, 16, 46, 59, 6),
)


class ZaryaPhaseTests(unittest.TestCase):
    def scene(self, row, distance=5):
        hero, graph, node, *_ = row
        scene = fixtures.ZaryaUltimateTests()
        scene.setUp()
        target = scene.b
        self.assertTrue(scene.run.match.switch_hero(target, heroes()[hero]))
        target.mover.position[:] = [0, 1, distance]
        scene.a.body_script.ult_seconds = 0
        instance = next(i for i in target.body_script.component.instances.values() if i.graph.index == graph)
        cast = instance.state(instance.graph.node(node).state)
        return scene, cast

    def activate(self, scene, cast):
        scene.run.step({scene.b: (0, 0, 8)})
        self.assertTrue(cast.flags & Ability.RUNNING)
        self.assertIsNotNone(phase.protection(scene.b))

    def test_authored_protection_end_recovery_and_cooldown(self):
        for row in PROFILES:
            with self.subTest(hero=row[0]):
                scene, cast = self.scene(row)
                self.activate(scene, cast)
                window, duration, cooldown = row[3:]
                scene.advance(window - 1)
                self.assertIsNotNone(phase.protection(scene.b))
                scene.advance(1)
                self.assertIsNone(phase.protection(scene.b))
                self.assertTrue(cast.flags & Ability.RUNNING)
                scene.advance(duration - window)
                self.assertFalse(cast.flags & Ability.RUNNING)
                self.assertAlmostEqual(cast.cur, cooldown)
                scene.run.step({scene.b: (0, 0, 8)})
                self.assertIsNone(phase.protection(scene.b))
                self.assertFalse(cast.flags & Ability.RUNNING)
                for _ in range(800):
                    scene.run.step({})
                    self.assertFalse(cast.flags & Ability.RUNNING)
                    if cast.remaining() <= 0:
                        break
                self.assertEqual(cast.remaining(), 0)
                self.activate(scene, cast)

    def test_primary_cancels_wraith_and_cryo_while_fade_expires(self):
        for row in PROFILES:
            with self.subTest(hero=row[0]):
                scene, cast = self.scene(row)
                self.activate(scene, cast)
                scene.advance(24)
                scene.run.step({scene.b: (0, 0, 1)})
                self.assertEqual(phase.protection(scene.b) is not None, row == PROFILES[2])
                scene.advance(40)
                self.assertFalse(cast.flags & Ability.RUNNING)

    def test_silent_body_loses_protection_and_old_tick_does_not_restore_it(self):
        for row, ticks in zip(PROFILES, (188, 250, 47), strict=True):
            with self.subTest(hero=row[0]):
                scene, cast = self.scene(row)
                self.activate(scene, cast)
                deadline = scene.run.frame + ticks
                for tick, protected in ((deadline - 1, True), (deadline, False), (deadline - 1, False)):
                    scene.run.match.update(tick * combat.TICK, tick)
                    self.assertEqual(phase.protection(scene.b) is not None, protected)
                self.assertTrue(cast.flags & Ability.RUNNING)
                self.assertIsNone(phase.protection(scene.b))
                before = scene.health()
                for tick in range(deadline + 1, deadline + 15):
                    scene.run.match.update(tick * combat.TICK, tick)
                    scene.a.take_commands([Command(tick, buttons=1)])
                self.assertLess(scene.health(), before)
                self.assertTrue(cast.flags & Ability.RUNNING)

    def test_actual_beam_secondary_and_melee_reject_protected_body(self):
        for row, attack in product(PROFILES, (1, 2, 0x800)):
            with self.subTest(hero=row[0], attack=attack):
                scene, cast = self.scene(row, distance=1.5)
                self.activate(scene, cast)
                before = scene.health()
                for index in range(20):
                    scene.run.step({scene.a: (0, 0, attack if index == 0 or attack == 1 else 0)})
                self.assertEqual(scene.health(), before)
                self.assertEqual(scene.charge(), 0)
                self.assertFalse(scene.a.client.messages(combat.HIT))
                scene.advance(row[4])
                for _ in range(20):
                    scene.run.step({scene.a: (0, 0, 1)})
                self.assertLess(scene.health(), before)

    def test_wraith_and_fade_pass_fire_through_but_cryo_blocks_both_teams(self):
        for row, attack in product(PROFILES, (1, 2)):
            for ally in (False, True) if row == PROFILES[1] else (False,):
                with self.subTest(hero=row[0], attack=attack, ally=ally):
                    scene, cast = self.scene(row, distance=2.5)
                    scene.b.team = scene.a.team if ally else 1
                    scene.c.team = 1
                    scene.c.mover.position[:] = [0, 1, 4]
                    self.activate(scene, cast)
                    for index in range(20):
                        scene.run.step({scene.a: (0, 0, attack if index == 0 or attack == 1 else 0)})
                    if row == PROFILES[1]:
                        self.assertEqual(scene.health(scene.c), 200)
                    else:
                        self.assertLess(scene.health(scene.c), 200)

    def test_beam_sweep_hits_cryo_pet_edge_but_can_pass_outside_it(self):
        for lateral, expected in ((0.55, 200), (0.8, 140)):
            with self.subTest(lateral=lateral):
                scene, cast = self.scene(PROFILES[1], distance=2.5)
                scene.b.mover.position[0] = lateral
                scene.b.mover.body = None
                scene.c.team = 1 - scene.a.team
                scene.c.mover.position[:] = [0, 1, 5]
                scene.c.mover.body = None
                self.activate(scene, cast)
                for _ in range(51):
                    scene.run.step({scene.a: (0, 0, 1)})
                self.assertEqual(scene.health(), 250)
                self.assertAlmostEqual(scene.health(scene.c), expected, places=4)

    def test_cryo_pet_geometry_is_independent_of_crouch_and_retires_on_cancel(self):
        scene, cast = self.scene(PROFILES[1], distance=2.5)
        scene.run.step({scene.b: (0, 0, CROUCH)})
        scene.run.step({scene.b: (0, 0, CROUCH | 8)})
        self.assertTrue(scene.b.mover.crouched)
        self.assertTrue(cast.flags & Ability.RUNNING)
        a, b, radius = phase.cryo_capsule(scene.b)
        self.assertEqual((a[1], b[1], radius), (1.5, 2.5, 0.5))
        self.assertAlmostEqual(scene.fight.cover_ray((0, 2.85, 0), (0, 0, 1), 5),
                               2.5 - (0.5**2 - 0.35**2)**0.5)
        scene.advance(24)
        scene.run.step({scene.b: (0, 0, 1)})
        self.assertIsNone(scene.fight.cover_ray((0, 2.85, 0), (0, 0, 1), 5))

    def test_cryo_blocks_walking_on_both_teams_then_clears(self):
        for allied in (False, True):
            with self.subTest(allied=allied):
                scene, cast = self.scene(PROFILES[1], distance=2.5)
                scene.b.team = scene.a.team if allied else 1 - scene.a.team
                self.activate(scene, cast)
                for _ in range(55):
                    scene.run.frame += 1
                    scene.run.match.update(scene.run.frame * combat.TICK, scene.run.frame)
                    scene.a.take_commands([Command(scene.run.frame, forward=127)])
                self.assertGreater(scene.a.mover.position[2], 1.2)
                self.assertLess(scene.a.mover.position[2], 2.0)
                scene.run.step({scene.b: (0, 0, 1)})
                self.assertIsNone(phase.cryo_capsule(scene.b))
                for _ in range(36):
                    scene.run.frame += 1
                    scene.run.match.update(scene.run.frame * combat.TICK, scene.run.frame)
                    scene.a.take_commands([Command(scene.run.frame, forward=127)])
                self.assertGreater(scene.a.mover.position[2], 3.0)

    @unittest.skipUnless(npc_fixtures.NPC_DATA, "requires all five actual NPC definitions")
    def test_cryo_covers_all_five_actual_npcs_from_zarya_fire(self):
        npc_cases = npc_fixtures.ZaryaFiveNpcTests
        npc_cases.setUpClass()
        self.addCleanup(npc_cases.tearDownClass)
        for definition in npc_cases.catalog.values():
            with self.subTest(npc=definition.name):
                scene, cast = self.scene(PROFILES[1], distance=2.5)
                scene.c.mover.position[0] = 12
                scene.c.mover.body = None
                bot = npc.spawn(scene.run.match, definition, (0, 1, 4), team=1 - scene.a.team)
                self.assertIsNotNone(bot.body_script)
                vitals = scene.fight.vitals[bot.entity]
                before = vitals.total
                self.activate(scene, cast)
                for _ in range(51):
                    scene.run.step({scene.a: (0, 0, 1)})
                scene.advance(40)
                scene.run.step({scene.a: (0, 0, 2)})
                rocket, = scene.fight.rockets
                scene.advance(30)
                self.assertNotIn(rocket, scene.fight.rockets)
                self.assertEqual(vitals.total, before)
                self.assertIsNone(bot.impulse_mover)
                self.assertEqual(scene.charge(), 0)

    @unittest.skipUnless(npc_fixtures.NPC_DATA, "requires actual NPC movers")
    def test_cryo_occlusion_releases_all_five_npc_inputs_until_cancel(self):
        npc_cases = npc_fixtures.ZaryaFiveNpcTests
        npc_cases.setUpClass()
        self.addCleanup(npc_cases.tearDownClass)
        first_damage = {"Trooper": 20, "Enforcer": 50, "Sniper": 100, "Assassin": 25, "Heavy Assault": 5}
        for definition, allied in product(npc_cases.catalog.values(), (False, True)):
            with self.subTest(npc=definition.name, allied=allied):
                scene, cast = self.scene(PROFILES[1], distance=1.25)
                run, fight = scene.run, scene.fight
                scene.a.mover.position[:] = (0, 1, 0)
                scene.b.mover.position[:] = (0.4, 1, 1.25)
                scene.c.mover.position[:] = (20, 1, 20)
                for player in (scene.a, scene.b, scene.c):
                    player.client = npc_fixtures.NpcClient(player)
                    player.mover.body = None
                scene.b.team = scene.a.team if allied else 1 - scene.a.team
                bot = npc.spawn(run.match, definition, (0, 1, 2.5), team=1 - scene.a.team)
                selection = aim(fight._eye(scene.a), (0, 2.4, 2.5))
                posed = {scene.a: (*selection, 0), scene.b: (0, 0, CROUCH)}
                run.step(posed)
                self.assertEqual(npc_control.start(run.match, scene.a), bot.entity)
                run.step({**posed, scene.b: (0, 0, CROUCH | 8)})
                self.assertTrue(cast.flags & Ability.RUNNING)
                direction = combat.aim(*selection)
                self.assertIsNotNone(fight.cover_ray(fight._eye(scene.a), direction, 2.5, exclude=scene.a))
                selection_blocked = npc_control.selected(run.match, scene.a) is None
                run.step(posed)
                self.assertEqual((selection_blocked, bot.attack_input), (True, False))
                self.assertNotIn(npc_control.button(bot), bot.body_script.component.held)
                self.assertNotIn(bot.entity, fight.pins)
                before = scene.health(scene.a)
                for _ in range(26):
                    run.step(posed)
                    self.assertFalse(bot.attack_input)
                self.assertEqual(scene.health(scene.a), before)
                run.step({**posed, scene.b: (0, 0, CROUCH | 1)})
                self.assertIsNone(phase.protection(scene.b))
                run.step(posed)
                if definition.name != "Assassin":
                    self.assertTrue(bot.attack_input)
                for _ in range(170):
                    if scene.health(scene.a) < before:
                        break
                    run.step(posed)
                self.assertEqual(before - scene.health(scene.a), first_damage[definition.name])
                if definition.name == "Assassin":
                    self.assertEqual(fight.pins.get(bot.entity), scene.a.body)

    @unittest.skipUnless(npc_fixtures.NPC_DATA, "requires actual NPC movers")
    def test_secondary_npc_impulse_stops_at_cryo_pet(self):
        npc_cases = npc_fixtures.ZaryaFiveNpcTests
        npc_cases.setUpClass()
        self.addCleanup(npc_cases.tearDownClass)
        for definition in npc_cases.catalog.values():
            if definition.name == "Heavy Assault":
                continue  # Its actual modifier26 rejects the impulse before motion.
            with self.subTest(npc=definition.name):
                scene, cast = self.scene(PROFILES[1], distance=3.65)
                scene.b.team = scene.a.team
                scene.c.mover.position[0] = 12
                scene.c.mover.body = None
                bot = npc.spawn(scene.run.match, definition, (0, 1, 2.3), team=1 - scene.a.team)
                before = scene.fight.vitals[bot.entity].total
                self.activate(scene, cast)
                angles = aim(scene.fight._eye(scene.a), (0, 2.1, 2.3))
                scene.run.step({scene.a: (*angles, 2)})
                for _ in range(35):
                    scene.run.step({})
                    if bot.impulse_mover is not None:
                        break
                self.assertIsNotNone(bot.impulse_mover)
                self.assertAlmostEqual(before - scene.fight.vitals[bot.entity].total, 47)
                self.assertAlmostEqual(bot.velocity[2], 5)
                scene.advance(18)
                self.assertGreater(bot.position[2], 2.4)
                self.assertLess(bot.position[2], 3.1)
                self.assertLess(bot.velocity[2], 1)

    @unittest.skipUnless(npc_fixtures.NPC_DATA, "requires actual NPC movers")
    def test_graviton_npc_pull_meets_cryo_even_when_its_los_ray_clears(self):
        npc_cases = npc_fixtures.ZaryaFiveNpcTests
        npc_cases.setUpClass()
        self.addCleanup(npc_cases.tearDownClass)
        for definition in npc_cases.catalog.values():
            with self.subTest(npc=definition.name):
                scene, cast = self.scene(PROFILES[1], distance=4)
                scene.b.team = scene.a.team
                scene.a.mover.position[0] = 0.72
                scene.a.mover.body = None
                scene.c.mover.position[0] = 12
                scene.c.mover.body = None
                bot = npc.spawn(scene.run.match, definition, (0.72, 1, 5.3), team=1 - scene.a.team)
                self.activate(scene, cast)
                scene.a.body_script.set_ult_charge(2100)
                angles = aim(scene.fight._eye(scene.a), (0.72, 1, 2.6))
                scene.run.step({scene.a: (*angles, 0x20)})
                field = scene.until_field()
                target = next(target for target in scene.fight._targets() if target.owner is bot)
                self.assertTrue(scene.fight.zarya_gravitons.includes(field, target))
                scene.advance(40)
                # The NPC capsule meets the ice despite initial center-ray visibility.
                self.assertLess(bot.position[2], 5.0)
                self.assertGreater(abs(bot.position[0] - field.position[0]), 0.15)
                scene.run.step({scene.b: (0, 0, 1)})
                self.assertIsNone(phase.cryo_capsule(scene.b))
                scene.advance(60)
                self.assertLess(bot.position[2], 3.6)

    def test_graviton_immunity_suspends_pull_and_damage_then_reacquires(self):
        for row in PROFILES:
            with self.subTest(hero=row[0]):
                scene, cast = self.scene(row)
                scene.cast()
                scene.until_field()
                self.assertIsNotNone(scene.fight.zarya_gravitons.movement_for(scene.b))
                self.activate(scene, cast)
                before = scene.health()
                self.assertIsNone(scene.fight.zarya_gravitons.movement_for(scene.b))
                scene.advance(20)
                self.assertEqual(scene.health(), before)
                if row == PROFILES[1]:
                    scene.run.step({scene.b: (0, 0, 1)})
                else:
                    scene.advance(row[3] - 20)
                self.assertIsNotNone(scene.fight.zarya_gravitons.movement_for(scene.b))
                scene.advance(2)
                self.assertLess(scene.health(), before)

    def test_sparse_graviton_does_not_damage_during_silent_phase_windows(self):
        for row in PROFILES:
            with self.subTest(hero=row[0]):
                scene, cast = self.scene(row)
                scene.b.mover.position[0] = 2
                scene.c.team = scene.b.team
                scene.c.mover.position[:] = [0, 1, 5]
                self.activate(scene, cast)
                deadline = scene.b.body_script.world.phase_deadline
                scene.cast()
                field = scene.until_field()
                self.assertIsNotNone(phase.protection(scene.b))
                self.assertLess(field.last_tick, deadline)
                self.assertLess(deadline, field.end_tick)
                before = scene.health()
                tick = deadline + 70
                match = scene.run.match
                match.update(tick * combat.TICK, tick, frames=False)
                self.assertIsNone(phase.protection(scene.b))
                expected = (min(tick, field.end_tick) - deadline + 1) * 0.08
                self.assertAlmostEqual(before - scene.health(), expected)
                after = scene.health()
                match.update(tick * combat.TICK, tick, frames=False)
                self.assertEqual(scene.health(), after)

    def test_late_phase_cancel_does_not_expose_earlier_graviton_ticks(self):
        for row in PROFILES[:2]:
            with self.subTest(hero=row[0]):
                scene, cast = self.scene(row)
                scene.cast()
                scene.until_field()
                self.activate(scene, cast)
                scene.advance(24)
                before = scene.health()
                cancel = scene.run.frame + 10
                match = scene.run.match
                match.tick = cancel
                scene.b.take_commands([Command(cancel, buttons=1)])
                self.assertIsNone(phase.protection(scene.b))
                tick = cancel + 5
                match.update(tick * combat.TICK, tick, frames=False)
                self.assertAlmostEqual(before - scene.health(), 6 * 0.08, places=5)
                self.assertIsNotNone(scene.fight.zarya_gravitons.movement_for(scene.b))

    def test_future_neutral_command_cannot_end_phase_before_its_world_deadline(self):
        for row, profile in zip(PROFILES, phase.PROFILES, strict=True):
            with self.subTest(hero=row[0]):
                scene, cast = self.scene(row)
                self.activate(scene, cast)
                deadline = scene.b.body_script.world.phase_deadline
                scene.b.take_commands([Command(scene.run.frame + 500)])
                window = cast.instance.state(cast.instance.graph.node(profile[4]).state)
                self.assertFalse(window.active)
                match = scene.run.match
                for tick in (deadline - 1, deadline):
                    match.update(tick * combat.TICK, tick, frames=False)
                    self.assertEqual(phase.protection(scene.b) is not None, tick < deadline)

    def test_late_phase_cannot_prevent_an_earlier_graviton_direct_hit(self):
        for row in PROFILES:
            with self.subTest(hero=row[0]):
                scene, _ = self.scene(row)
                scene.cast()
                scene.advance(1)
                rocket, = scene.fight.rockets
                self.assertEqual(rocket.last_tick, scene.run.frame)
                match = scene.run.match
                activation = scene.run.frame + 32
                match.tick = activation
                scene.b.take_commands([Command(activation, buttons=8)])
                self.assertIsNotNone(phase.protection(scene.b))
                before = scene.health()
                match.update(activation * combat.TICK, activation, frames=False)
                field, = scene.fight.zarya_gravitons.fields
                landed = field.end_tick - 250
                self.assertLess(landed, activation)
                self.assertGreater(field.position[2], 4)
                self.assertAlmostEqual(before - scene.health(), 5 + (activation - landed - 1) * 0.08)

    def test_late_phase_preserves_graviton_damage_already_due(self):
        for row in PROFILES:
            with self.subTest(hero=row[0]):
                scene, _ = self.scene(row)
                scene.b.mover.position[0] = 2
                scene.c.team = scene.b.team
                scene.c.mover.position[:] = [0, 1, 5]
                scene.cast()
                field = scene.until_field()
                activation = field.last_tick + 120
                match = scene.run.match
                match.tick = activation
                scene.b.take_commands([Command(activation, buttons=8)])
                self.assertIsNotNone(phase.protection(scene.b))
                before = scene.health()
                tick = activation + 10
                match.update(tick * combat.TICK, tick, frames=False)
                self.assertAlmostEqual(before - scene.health(), 119 * 0.08)
                self.assertIsNotNone(phase.protection(scene.b))
                self.assertIsNone(scene.fight.zarya_gravitons.movement_for(scene.b))

    def test_wraith_movement_owns_velocity_after_same_tick_pull_release(self):
        scene, cast = self.scene(PROFILES[0])
        scene.cast()
        scene.until_field()
        scene.advance(1)
        self.assertLess(scene.b.mover.velocity[2], 0)
        self.activate(scene, cast)
        self.assertIn(scene.b.body, scene.fight.zarya_gravitons.player_pulls)

        # A second command can arrive before the next world update.
        next_frame = scene.run.frame + 1
        scene.b.take_commands([Command(next_frame, forward=127)])
        velocity = scene.b.mover.velocity
        self.assertGreater(velocity[2], 0)
        position = tuple(scene.b.mover.position)
        health = scene.health()
        scene.run.frame = next_frame
        scene.run.match.update(next_frame * combat.TICK, next_frame)
        self.assertNotIn(scene.b.body, scene.fight.zarya_gravitons.player_pulls)
        self.assertEqual(scene.b.mover.velocity, velocity)
        self.assertEqual(tuple(scene.b.mover.position), position)
        self.assertEqual(scene.health(), health)

    def test_cryo_refuses_throttle_and_jump_but_preserves_cancel(self):
        scene, cast = self.scene(PROFILES[1])
        self.activate(scene, cast)
        start = tuple(scene.b.mover.position)
        for index in range(16):
            scene.run.frame += 1
            scene.run.match.update(scene.run.frame * combat.TICK, scene.run.frame)
            scene.b.take_commands([Command(scene.run.frame, forward=127, buttons=JUMP if not index else 0)])
        self.assertEqual(tuple(scene.b.mover.position), start)
        scene.run.step({scene.b: (0, 0, 1)})
        self.assertIsNone(phase.protection(scene.b))

    def test_wraith_and_fade_walk_out_of_live_graviton(self):
        for row in (PROFILES[0], PROFILES[2]):
            with self.subTest(hero=row[0]):
                scene, cast = self.scene(row)
                scene.cast()
                field = scene.until_field()
                x, _, z = field.position
                scene.b.mover.position[:] = [x, 1, z + field.radius - 0.1]
                scene.b.mover.body = None
                self.assertIsNotNone(scene.fight.zarya_gravitons.movement_for(scene.b))
                self.activate(scene, cast)
                before = scene.health()
                for _ in range(30):
                    scene.run.frame += 1
                    scene.run.match.update(scene.run.frame * combat.TICK, scene.run.frame)
                    scene.b.take_commands([Command(scene.run.frame, forward=127)])
                self.assertGreater(scene.b.mover.position[2], z + field.radius + 0.75)
                scene.advance(row[3] - 30)
                self.assertIn(field, scene.fight.zarya_gravitons.fields)
                self.assertIsNone(phase.protection(scene.b))
                self.assertIsNone(scene.fight.zarya_gravitons.movement_for(scene.b))
                scene.advance(2)
                self.assertEqual(scene.health(), before)

    def test_flashbang_does_not_stun_or_damage_active_protection(self):
        for row in PROFILES:
            with self.subTest(hero=row[0]):
                scene, cast = self.scene(row, distance=3)
                self.assertTrue(scene.run.match.switch_hero(scene.a, heroes()[0x02E0000000000042]))
                scene.a.mover.position[:] = [0, 1, 0]
                self.activate(scene, cast)
                before = scene.health()
                scene.run.step({scene.a: (0, 0, 16)})
                scene.advance(35)
                self.assertFalse(scene.fight.rockets)
                self.assertEqual(scene.health(), before)
                self.assertFalse(scene.fight.is_stunned(scene.b))

    def test_cryo_remains_a_valid_ally_projection_recipient(self):
        scene, cast = self.scene(PROFILES[1])
        scene.b.team = scene.a.team
        self.activate(scene, cast)
        scene.run.step({scene.a: (0, 0, 16)})
        projected = next(i for i in scene.a.body_script.component.instances.values()
                         if i.graph.index == 0x025C)
        self.assertTrue(projected.state(10).active)
        self.assertEqual(projected.vars[536].value().id, scene.b.body)
