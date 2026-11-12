"""Actual Dragonstrike windup and initial arrow against Tracer."""

import os
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import test_game_tracer_hanzo as hanzo_tests
import test_game_tracer_npcs as npc_tests
from test_game_combat import aim
from test_game_mover import box, wall

from ow174.game import combat, dragon, dva, npc, npc_control, sigma_grasp, tracer, zarya_barrier
from ow174.game.collision import World
from ow174.game.commands import FIRE, SECONDARY, Command
from ow174.game.content import heroes
from ow174.game.script.driver import ULT_CHARGE, ULT_COST

INITIAL_ARROW = 0x040000000000034F
DRAGON_INTERCEPTORS = (
    ("deflect", 0x02E0000000000029, 16, 0x03A3, 10),
    ("grasp", sigma_grasp.HERO, 8, sigma_grasp.GRASP, 1),
    ("matrix", dva.HERO, SECONDARY, 0x02CC, 3),
)


class TracerHanzoUltimateTests(unittest.TestCase):
    def arena(self, distance):
        arena = hanzo_tests.TracerHanzoTests()
        arena.setUp()
        arena.move(arena.hanzo, (arena.x, arena.y, arena.z + distance))
        arena.aim = aim(arena.fight._eye(arena.hanzo), (arena.x, arena.y + 1, arena.z))
        body = arena.hanzo.body_script
        body.ult_seconds = 0
        cost = body.component.entity_var(ULT_COST).value()
        self.assertEqual(cost, 1680)
        return arena, body, cost

    def test_ready_cast_spends_once_and_arrow_hits_only_inside_authored_range(self):
        for distance, health in ((8, 25), (12, 150)):
            with self.subTest(distance=distance):
                arena, body, cost = self.arena(distance)
                body.set_ult_charge(cost)
                begin = arena.run.frame + 1
                registry = arena.fight.projectiles
                with (
                    patch.object(arena.fight, "_shoot", wraps=arena.fight._shoot) as shoot,
                    patch.object(registry, "fired", wraps=registry.fired) as fired,
                ):
                    arena.run.step({arena.hanzo: (*arena.aim, 32)})
                    self.assertEqual(body.component.entity_var(ULT_CHARGE).value(), 0)
                    for _ in range(61):
                        arena.run.step({arena.hanzo: (*arena.aim, 0)})
                    fired.assert_not_called()
                    for _ in range(100):
                        button = 32 if arena.run.frame % 2 else 0
                        arena.run.step({arena.hanzo: (*arena.aim, button)})
                fired.assert_called_once()
                self.assertEqual(fired.call_args.args[3], INITIAL_ARROW)
                shoot.assert_called_once()
                state = shoot.call_args.args[2]
                self.assertEqual((state.instance.graph.index, state.index), (0x0101, 21))
                self.assertEqual(shoot.call_args.args[4] - begin * 16, 1000)
                phases = [s for s in state.instance.states.values() if s.node.pos in (29, 37)]
                self.assertEqual(len(phases), 2)
                self.assertTrue(all(not s.active for s in phases))
                self.assertEqual(body.component.entity_var(ULT_CHARGE).value(), 0)
                self.assertEqual(arena.health.total, health)

    def test_undercharge_preserves_charge_and_emits_no_arrow(self):
        arena, body, cost = self.arena(8)
        body.set_ult_charge(cost - 1)
        registry = arena.fight.projectiles
        with patch.object(registry, "fired", wraps=registry.fired) as fired:
            arena.run.step({arena.hanzo: (*arena.aim, 32)})
            for _ in range(100):
                arena.run.step({arena.hanzo: (*arena.aim, 0)})
        fired.assert_not_called()
        self.assertEqual(body.component.entity_var(ULT_CHARGE).value(), cost - 1)
        self.assertEqual(arena.health.total, 150)

    def test_tracer_kills_hanzo_during_windup_and_prevents_arrow(self):
        arena, body, cost = self.arena(8)
        body.set_ult_charge(cost)
        x, y, z = arena.hanzo.mover.position
        angles = aim(arena.fight._eye(arena.player), (x, y + 1, z))
        registry = arena.fight.projectiles
        with patch.object(registry, "fired", wraps=registry.fired) as fired:
            arena.run.step({arena.hanzo: (*arena.aim, 32)})
            for _ in range(100):
                arena.run.step({arena.hanzo: (*arena.aim, 0), arena.player: (*angles, FIRE)})
        self.assertTrue(arena.fight.vitals[arena.hanzo.body].dead)
        self.assertFalse(any(call.args[3] == INITIAL_ARROW for call in fired.call_args_list))
        self.assertEqual(arena.health.total, 150)

    def dragon_arena(self, *, wall_impact=False):
        path = os.environ.get("ORPHEUS_HANZO_DRAGON_PROFILE")
        if not path:
            self.skipTest("requires explicitly extracted Dragonstrike profile")
        arena, body, cost = self.arena(20)
        arena.fight.configure_dragonstrike(dragon.load_profile(Path(path), reconstructed_rates=True))
        if wall_impact:
            x, y, z = arena.x, arena.y, arena.z
            floor = box(x - 30, x + 30, y - 1, y, z - 30, z + 30)
            mesh = World(floor + wall(x - 5, z + 15, x + 5, z + 15, y, y + 4))
            arena.run.match.collision_world = mesh
            for actor in arena.run.players:
                actor.mover.use(world=mesh)
        body.set_ult_charge(cost)
        return arena

    def launch_dragons(self, arena):
        arena.run.step({arena.hanzo: (*arena.aim, 32)})
        for _ in range(110):
            arena.run.step({arena.hanzo: (*arena.aim, 0)})
            if arena.fight.dragons:
                break
        flight, = arena.fight.dragons
        self.assertFalse(arena.fight.rockets)
        self.assertEqual(arena.health.total, 150)
        return flight

    def test_moving_dragons_hit_tracer_from_range_end_or_wall(self):
        for wall_impact in (False, True):
            with self.subTest(wall_impact=wall_impact):
                arena = self.dragon_arena(wall_impact=wall_impact)
                flight = self.launch_dragons(arena)
                expected_z = arena.z + (15 if wall_impact else 10)
                self.assertAlmostEqual(flight.origin[2], expected_z, places=2)
                for _ in range(120):
                    arena.run.step({})
                    if arena.health.total < 150:
                        break
                self.assertAlmostEqual(arena.health.total, 145.2)
                self.assertIn(flight, arena.fight.dragons)

    def test_dragons_respect_team_and_tracer_blink(self):
        for gate in ("ally", "blink"):
            with self.subTest(gate=gate):
                arena = self.dragon_arena()
                if gate == "ally":
                    arena.player.team = arena.hanzo.team
                flight = self.launch_dragons(arena)
                if gate == "blink":
                    arena.run.step({arena.player: (16384, 0, 8)})
                    self.assertAlmostEqual(arena.player.mover.position[0], arena.x + 7.5, places=2)
                for _ in range(260):
                    arena.run.step({})
                self.assertIn(flight, arena.fight.dragons)
                self.assertEqual(arena.health.total, 150)

    def test_moving_dragons_keep_original_killer_after_hanzo_replaces_body(self):
        arena = self.dragon_arena()
        flight = self.launch_dragons(arena)
        old = arena.hanzo.body
        self.assertTrue(arena.run.match.switch_hero(arena.hanzo, arena.hanzo.hero))
        self.assertNotEqual(arena.hanzo.body, old)
        for _ in range(130):
            arena.run.step({})
            if arena.health.dead:
                break
        self.assertTrue(arena.health.dead)
        self.assertEqual(arena.health.killer, old)
        self.assertEqual(arena.player.client.messages(combat.DEATH_NOTICE)[-1]["+0x7C"]["+0x0"], old)
        self.assertEqual(flight.source_entity, old)

    def test_recall_blocks_moving_dragon_damage_until_recovery(self):
        arena = self.dragon_arena()
        for _ in range(195):
            arena.run.step({})
        flight = self.launch_dragons(arena)
        for _ in range(120):
            arena.run.step({})
            if arena.health.total < 150:
                break
        self.assertAlmostEqual(arena.health.total, 145.2)
        arena.run.step({arena.player: (0, 0, 16)})
        self.assertTrue(tracer.recalling(arena.player))
        # The world update for the input frame runs before the Recall command.
        before = arena.health.total
        self.assertAlmostEqual(before, 140.4)
        notices = len(arena.hanzo.client.messages(combat.HIT))
        for _ in range(110):
            arena.run.step({})
            if not tracer.recalling(arena.player):
                break
            self.assertEqual(len(arena.hanzo.client.messages(combat.HIT)), notices)
            self.assertGreaterEqual(arena.health.total, before)
        self.assertFalse(tracer.recalling(arena.player))
        self.assertIn(flight, arena.fight.dragons)
        healed = arena.health.total
        for _ in range(50):
            arena.run.step({})
            if arena.health.total < healed:
                break
        self.assertLess(arena.health.total, healed)

    def test_moving_dragons_retire_on_lifetime_or_source_leave(self):
        for leave in (False, True):
            with self.subTest(leave=leave):
                arena = self.dragon_arena()
                arena.player.team = arena.hanzo.team
                flight = self.launch_dragons(arena)
                if leave:
                    arena.run.match.leave(arena.hanzo)
                    arena.run.players.remove(arena.hanzo)
                    arena.run.step({})
                    self.assertLess(flight.age, flight.profile.lifetime)
                else:
                    for _ in range(1300):
                        if flight.age >= flight.profile.lifetime - 0.016:
                            break
                        arena.run.step({})
                        self.assertIn(flight, arena.fight.dragons)
                    arena.run.step({})
                    self.assertAlmostEqual(flight.age, flight.profile.lifetime, places=6)
                self.assertFalse(arena.fight.dragons)
                self.assertEqual(arena.health.total, 150)

    def test_batched_tracer_movement_preserves_dragon_damage_across_core_boundary(self):
        continuous = self.damage_across_dragon_boundary(batched=False)
        batched = self.damage_across_dragon_boundary(batched=True)
        self.assertEqual(continuous[0], batched[0])
        self.assertAlmostEqual(continuous[1], 33.6)
        self.assertAlmostEqual(continuous[1], batched[1], places=5)

    def damage_across_dragon_boundary(self, *, batched):
        arena = self.dragon_arena()
        arena.move(arena.player, (arena.x + 1.65, arena.y, arena.z))
        flight = self.launch_dragons(arena)
        run, match, player = arena.run, arena.run.match, arena.player
        for _ in range(100):
            run.step({})
            if arena.health.total < 150:
                break
        self.assertAlmostEqual(arena.health.total, 145.2)
        offset = 1000

        def step():
            before = arena.health.total
            run.frame += 1
            match.update(run.frame * combat.TICK, run.frame)
            player.take_commands([Command(run.frame + offset, right=-127)])
            return before - arena.health.total

        for _ in range(10):
            step()
            if player.mover.position[0] - arena.x >= 2.0:
                break
        before, age = arena.health.total, flight.age
        gap = 12
        if batched:
            player.take_commands([Command(run.frame + offset + n, right=-127) for n in range(1, gap + 1)])
            run.frame += gap
            match.update(run.frame * combat.TICK, run.frame)
        else:
            doses = {round(step(), 6) for _ in range(gap)}
            self.assertEqual(doses, {4.8, 2.4})
        self.assertEqual(arena.fight.dragons, [flight])
        self.assertAlmostEqual(flight.age - age, gap * combat.TICK)
        self.assertFalse(arena.health.dead)
        return tuple(player.mover.position), before - arena.health.total

    def test_stationary_target_matches_when_arrow_transitions_inside_scheduler_gap(self):
        for distance in (19.85, 20.0, 20.2):
            with self.subTest(distance=distance):
                continuous = self.dragon_transition_during_gap(distance, skipped=False)
                skipped = self.dragon_transition_during_gap(distance, skipped=True)
                self.assertAlmostEqual(continuous[0], skipped[0], places=5)
                self.assertAlmostEqual(continuous[1], skipped[1], places=6)

    def dragon_transition_during_gap(self, distance, *, skipped):
        arena = self.dragon_arena()
        arena.move(arena.hanzo, (arena.x, arena.y, arena.z + distance))
        arena.aim = aim(arena.fight._eye(arena.hanzo), (arena.x, arena.y + 1, arena.z))
        run = arena.run
        run.step({arena.hanzo: (*arena.aim, 32)})
        for _ in range(75):
            run.step({arena.hanzo: (*arena.aim, 0)})
        arrow, = arena.fight.rockets
        self.assertGreater(arrow.flown, 0)
        self.assertFalse(arena.fight.dragons)
        if skipped:
            run.frame += 55
            run.match.update(run.frame * combat.TICK, run.frame)
        else:
            for _ in range(55):
                run.step({})
        flight, = arena.fight.dragons
        self.assertLess(arena.health.total, 150)
        return arena.health.total, flight.age

    def test_initial_arrow_and_moving_dragons_resolve_live_barriers_separately(self):
        for gate in ("arrow_dome", "moving_dome", "moving_projected"):
            with self.subTest(gate=gate):
                arena = self.dragon_arena()
                if gate == "moving_projected":
                    self.check_dragon_projected_barrier(arena)
                    continue
                winston, _ = hanzo_tests.support_tests.PulseDeflectTests().support(
                    arena.run, arena.player, hero=0x02E0000000000009,
                )
                winston.team = arena.player.team
                z = arena.z + 14 if gate == "arrow_dome" else arena.z
                arena.move(winston, (arena.x + 2.5, arena.y, z))
                dome = hanzo_tests.winston_tests.TracerWinstonBarrierTests().cast(arena.run, winston)
                if gate == "arrow_dome":
                    arena.run.step({arena.hanzo: (*arena.aim, 32)})
                    for _ in range(130):
                        arena.run.step({arena.hanzo: (*arena.aim, 0)})
                    self.assertEqual(dome.health, 575)
                    self.assertFalse(arena.fight.rockets)
                    self.assertFalse(arena.fight.dragons)
                    self.assertEqual(arena.health.total, 150)
                else:
                    self.launch_dragons(arena)
                    for _ in range(120):
                        arena.run.step({})
                        if arena.health.total < 150:
                            break
                    self.assertAlmostEqual(arena.health.total, 145.2)
                    self.assertEqual(dome.health, 700)

    def check_dragon_projected_barrier(self, arena):
        zarya, _ = hanzo_tests.support_tests.PulseDeflectTests().support(
            arena.run, arena.player, hero=0x02E0000000000068,
        )
        zarya.team = arena.player.team
        arena.move(zarya, (arena.x + 3, arena.y, arena.z))
        self.launch_dragons(arena)
        direction = aim(arena.fight._eye(zarya), arena.fight._eye(arena.player))
        arena.run.step({zarya: (*direction, 16)})
        pet = zarya_barrier.active(arena.player)
        self.assertIsNotNone(pet)
        before = zarya_barrier.barrier_health(pet, arena.health)
        for _ in range(90):
            arena.run.step({})
            if zarya_barrier.barrier_health(pet, arena.health) < before:
                break
        self.assertAlmostEqual(before - zarya_barrier.barrier_health(pet, arena.health), 4.8)
        self.assertEqual(arena.health.total, 150)

    def test_dragonstrike_initial_arrow_can_be_reflected_or_absorbed(self):
        if not os.environ.get("ORPHEUS_PROJECTILE_TAGS"):
            self.skipTest("requires the authored projectile tag catalog")
        for defense, hero, button, _, _ in DRAGON_INTERCEPTORS:
            with self.subTest(defense=defense):
                arena = self.dragon_arena()
                run, fight = arena.run, arena.fight
                defender = self.dragon_interceptor(arena, defense, hero, 14)
                x, y, z = arena.x, arena.y, arena.z
                if defense == "deflect":
                    arena.player.team = arena.hanzo.team
                    arena.move(arena.player, (x, y, z + 25))
                facing = aim(fight._eye(defender), (x, y + 1.55, z + 20))
                run.step({arena.hanzo: (*arena.aim, 32)})
                for _ in range(75):
                    run.step({arena.hanzo: (*arena.aim, 0)})
                    if fight.rockets:
                        break
                arrow, = fight.rockets
                self.assertEqual(arrow.definition, INITIAL_ARROW)
                lease = arrow.shot
                registry = fight.projectiles
                with patch.object(registry, "absorbed", wraps=registry.absorbed) as absorbed:
                    run.step({defender: (*facing, button)})
                    held = {defender: (*facing, button if defense == "matrix" else 0)}
                    for _ in range(55):
                        run.step(held)
                        if arrow not in fight.rockets:
                            break
                if defense == "deflect":
                    absorbed.assert_not_called()
                    self.assertIs(arrow.shooter, defender)
                    flight, = fight.dragons
                    self.assertIs(flight.shooter, defender)
                    self.assertEqual(flight.source_entity, defender.body)
                    self.assertEqual(flight.team, defender.team)
                    self.assertGreater(flight.direction[2], 0)
                    for _ in range(100):
                        run.step({})
                        if arena.health.total < 150:
                            break
                    self.assertLess(arena.health.total, 150)
                else:
                    if lease is not None:
                        absorbed.assert_called_once()
                    self.assertNotIn(arrow, fight.rockets)
                    self.assertFalse(fight.dragons)
                    self.assertEqual(arena.health.total, 150)
                    if defense == "grasp":
                        grasp = next(i for i in defender.body_script.component.instances.values()
                                     if i.graph.index == sigma_grasp.GRASP)
                        self.assertEqual(grasp.vars[21312].value(), 125)

    def test_moving_dragon_damage_continues_through_active_projectile_interceptors(self):
        for defense, hero, button, graph_index, state_index in DRAGON_INTERCEPTORS:
            with self.subTest(defense=defense):
                arena = self.dragon_arena()
                run, fight = arena.run, arena.fight
                defender = self.dragon_interceptor(arena, defense, hero, 2)
                flight = self.launch_dragons(arena)
                health = hanzo_tests.winston_tests.TracerWinstonBarrierTests.health(run, defender)
                before = health.total
                facing = aim(fight._eye(defender), flight.origin)
                run.step({defender: (*facing, button)})
                instance = next(i for i in defender.body_script.component.instances.values()
                                if i.graph.index == graph_index)
                self.assertTrue(instance.states[state_index].active)
                held = {defender: (*facing, button if defense == "matrix" else 0)}
                for _ in range(100):
                    run.step(held)
                    if arena.health.total < 150 and health.total < before:
                        break
                self.assertLess(health.total, before)
                self.assertAlmostEqual(arena.health.total, 145.2)
                self.assertTrue(instance.states[state_index].active)
                self.assertEqual(fight.dragons, [flight])
                self.assertIs(flight.shooter, arena.hanzo)
                if defense == "grasp":
                    self.assertEqual(instance.vars[21312].value(), 0)

    def dragon_interceptor(self, arena, defense, hero, z_offset):
        run = arena.run
        defender, _ = hanzo_tests.support_tests.PulseDeflectTests().support(run, arena.player, hero=hero)
        if defense == "matrix":
            required = ("ORPHEUS_GRAPH_SUPPLEMENT", "ORPHEUS_MOVERS_SUPPLEMENT")
            if not all(os.environ.get(name) for name in required):
                self.skipTest("requires the authored Matrix body and graphs")
            self.assertTrue(run.match.switch_hero(defender, replace(heroes()[hero], body=dva.MECH)))
        defender.team = 1 - arena.hanzo.team
        arena.move(defender, (arena.x, arena.y, arena.z + z_offset))
        return defender

    def test_batched_recall_preserves_dragon_damage_at_protection_boundaries(self):
        for boundary in ("entry", "exit"):
            with self.subTest(boundary=boundary):
                continuous = self.dragon_damage_across_recall(boundary, batched=False)
                batched = self.dragon_damage_across_recall(boundary, batched=True)
                self.assertEqual(continuous[1], boundary == "entry")
                self.assertEqual(continuous[1], batched[1])
                self.assertAlmostEqual(continuous[0], batched[0], places=5)

    def dragon_damage_across_recall(self, boundary, *, batched):
        arena = self.dragon_arena()
        run, player = arena.run, arena.player
        for _ in range(195):
            run.step({})
        self.launch_dragons(arena)
        for _ in range(120):
            run.step({})
            if arena.health.total < 150:
                break
        self.assertAlmostEqual(arena.health.total, 145.2)
        if boundary == "entry":
            inputs = [0] * 6 + [16] + [0] * 5
        else:
            run.step({player: (0, 0, 16)})
            for _ in range(72):
                run.step({})
            self.assertTrue(tracer.recalling(player))
            inputs = [0] * 16
        before = arena.health.total
        if batched:
            player.take_commands([
                Command(run.frame + i, buttons=buttons)
                for i, buttons in enumerate(inputs, 1)
            ])
            run.frame += len(inputs)
            run.match.update(run.frame * combat.TICK, run.frame)
        else:
            for buttons in inputs:
                run.step({player: (0, 0, buttons)})
            if boundary == "entry":
                self.assertAlmostEqual(before - arena.health.total, 33.6)
            else:
                self.assertAlmostEqual(arena.health.total, 106.8)
        return arena.health.total, tracer.recalling(player)


class TracerHanzoDragonNpcTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.addClassCleanup(npc_tests.TracerNpcTests.doClassCleanups)
        npc_tests.TracerNpcTests.setUpClass()

    def test_all_talon_bodies_return_fire_and_die_during_actual_dragonstrike(self):
        for definition in npc_tests.TracerNpcTests.definitions.values():
            with self.subTest(npc=definition.name):
                case = TracerHanzoUltimateTests()
                arena = case.dragon_arena()
                self.addCleanup(npc.remove_spawned, arena.run.match)
                for player in arena.run.players:
                    player.client = npc_tests.NpcClient(player)
                arena.hanzo.team = arena.player.team
                position = (arena.x, arena.y, arena.z + 5)
                bot = npc.spawn(arena.run.match, definition, position, team=1 - arena.player.team)
                self.assertIsNotNone(bot.body_script)
                self.assertIsNone(bot.body_script.component.fault)
                target = next(t for t in arena.fight._targets() if t.owner is bot)
                health = arena.fight.vitals_of(target)
                before = health.total
                flight = case.launch_dragons(arena)
                self.assertEqual(health.total, before)
                for _ in range(90):
                    arena.run.step({})
                    if health.total < before:
                        break
                self.assertAlmostEqual(before - health.total, 4.8)
                self.assertIn(flight, arena.fight.dragons)
                self.assertEqual(arena.health.total, 150)
                hits = arena.hanzo.client.messages(combat.HIT)
                self.assertEqual(len(hits), 1)
                self.assertEqual(hits[0]["+0x78"]["+0x0"]["+0x0"], bot.entity)
                self.assertEqual(hits[0]["+0x78"]["+0x4"], 5)
                self.check_return_and_dragon_death(arena, bot, health, flight)

    def check_return_and_dragon_death(self, arena, bot, health, flight):
        run, fight, player = arena.run, arena.fight, arena.player
        x, y, z = arena.x, arena.y, arena.z
        # Set a five-metre engagement beside the dragon path so the NPC can finish its first attack.
        arena.move(player, (x + 7, y, z))
        bot.position = (x + 7, y, z + 5)
        direction = aim(fight._eye(player), (x + 7, y + 1, z + 5))
        before = health.total
        run.step({player: (*direction, FIRE)})
        self.assertLess(health.total, before)
        self.assertEqual(npc_control.start(run.match, player), bot.entity)
        expected = {name: damage for name, _, damage in npc_tests.TracerNpcTests.FIRST_PRIMARY_IMPACTS}
        for _ in range(160):
            run.step({})
            if arena.health.total < 150:
                break
        self.assertEqual(arena.health.total, 150 - expected[bot.name])
        self.assertIn(flight, fight.dragons)
        if bot.name == "Assassin":
            self.assertEqual(fight.pins.get(bot.entity), player.body)
        self.assertEqual(npc_control.stop(run.match), 1)

        # Place a wounded body back in the path, then let the live dragon deliver the lethal dose.
        health.damage(health.total - 4)
        fight._sync_body_health(bot, health)
        self.assertEqual(health.total, 4)
        bot.position = (x, y, z + 5)
        for _ in range(150):
            run.step({})
            if health.dead:
                break
        self.assertTrue(health.dead)
        self.assertEqual(health.killer, flight.source_entity)
        self.assertEqual(health.killer, arena.hanzo.body)
        self.assertTrue(bot.dead)
        self.assertIsNotNone(bot.body_script)
        self.assertTrue(bot.body_script.component.owner_dead)
        events = [event for entity, event in player.client.deaths if entity == bot.entity]
        self.assertTrue(any(event is not None for event in events))
        notice, = arena.hanzo.client.messages(combat.KILL_NOTICE)
        self.assertEqual(notice["+0x84"]["+0x0"], bot.entity)
        for _ in range(40):
            run.step({})
        self.assertIn(bot, run.match.bots)
        self.assertNotIn(bot.entity, fight.pins)
        self.assertFalse(any(who is bot for _, who in fight.respawns))
