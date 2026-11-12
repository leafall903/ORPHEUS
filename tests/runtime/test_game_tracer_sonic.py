"""Actual Sonic Arrow attachment and reveal against Tracer."""

import os
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import test_game_tracer_hanzo as hanzo_tests
import test_game_tracer_npcs as npc_tests
import test_game_tracer_pulse_deflect as support_tests
import test_game_tracer_winston_barrier as winston_tests
import test_server_snapshot as snapshot_tests
from test_game_combat import aim
from test_game_mover import box, wall

from ow174.game import dva, npc, npc_control, sigma_grasp, sonic
from ow174.game.collision import World
from ow174.game.commands import FIRE, SECONDARY
from ow174.game.content import heroes
from ow174.game.script import expr
from ow174.game.server_snapshot import snapshot_digest


class TracerSonicTests(unittest.TestCase):
    def profile(self):
        path = os.environ.get("ORPHEUS_HANZO_SONIC_PROFILE")
        if not path:
            self.skipTest("requires explicitly extracted Sonic profile")
        return sonic.load_profile(Path(path), reconstructed_geometry=True)

    def arena(self):
        profile = self.profile()
        arena = hanzo_tests.TracerHanzoTests()
        arena.setUp()
        arena.fight.configure_sonic(profile)
        return arena

    def fire(self, arena):
        arena.run.step({arena.hanzo: (*arena.aim, 8)})
        arena.run.step({})
        arena.draw()
        arrow = arena.release({}, definition=hanzo_tests.FULL_SONIC)
        self.assertFalse(arena.fight.sonars)
        arena.wait_contact(arrow, {})

    def test_sonic_marks_tracer_follows_body_and_expires(self):
        arena = self.arena()
        fight = arena.fight
        self.fire(arena)
        self.assertEqual(arena.health.total, 25)
        beacon, = fight.sonars
        self.assertEqual(beacon.attached, arena.player.body)
        component = arena.player.body_script.component
        flag = component.entity_var(sonic.REVEALED)
        self.assertTrue(expr.truthy(flag.value()))
        self.assertTrue(fight.revealed_to(arena.hanzo, arena.player))
        self.assertFalse(fight.revealed_to(arena.player, arena.player))
        instance = beacon.statuses[arena.player.body]
        self.assertTrue(instance.state(instance.graph.node(9).state).active)
        old = beacon.position
        arena.move(arena.player, (arena.x + 12, arena.y, arena.z))
        arena.run.step({})
        self.assertAlmostEqual(beacon.position[0], old[0] + 12, places=4)
        self.assertTrue(expr.truthy(flag.value()))
        for _ in range(380):
            arena.run.step({})
            if not fight.sonars:
                break
        self.assertFalse(fight.sonars)
        self.assertFalse(expr.truthy(flag.value()))
        self.assertFalse(fight.revealed_to(arena.hanzo, arena.player))

    def test_sonic_wall_field_marks_through_cover_and_releases_on_range_exit(self):
        arena = self.arena()
        x, y, z = arena.x, arena.y, arena.z
        floor = box(x - 25, x + 25, y - 1, y, z - 25, z + 25)
        mesh = World(floor + wall(x - 5, z + 3, x + 5, z + 3, y, y + 4))
        arena.run.match.collision_world = mesh
        for actor in arena.run.players:
            actor.mover.use(world=mesh)
        self.fire(arena)
        self.assertEqual(arena.health.total, 150)
        beacon, = arena.fight.sonars
        self.assertIsNone(beacon.attached)
        self.assertAlmostEqual(beacon.position[2], z + 3, places=4)
        flag = arena.player.body_script.component.entity_var(sonic.REVEALED)
        self.assertTrue(expr.truthy(flag.value()))
        arena.move(arena.player, (x + 12, y, z))
        arena.run.step({})
        self.assertFalse(expr.truthy(flag.value()))
        arena.move(arena.player, (x, y, z))
        arena.run.step({})
        self.assertTrue(expr.truthy(flag.value()))

    def test_winston_dome_stops_sonic_without_reveal(self):
        arena = self.arena()
        winston, _ = support_tests.PulseDeflectTests().support(
            arena.run, arena.player, hero=0x02E0000000000009,
        )
        winston.team = arena.player.team
        arena.move(winston, (arena.x + 2.5, arena.y, arena.z))
        dome = winston_tests.TracerWinstonBarrierTests().cast(arena.run, winston)
        self.fire(arena)
        self.assertEqual(dome.health, 575)
        self.assertEqual(arena.health.total, 150)
        self.assertFalse(arena.fight.sonars)
        self.assertFalse(arena.fight.revealed_to(arena.hanzo, arena.player))

    def test_active_sonic_flag_and_field_clock_are_in_stored_snapshot(self):
        profile = self.profile()
        case = snapshot_tests.ServerSnapshotTests()
        session, _, (source, target) = case.scene(two=True)
        fight = source.match.combat
        original = snapshot_digest(case.capture(session))
        fight.configure_sonic(profile)
        configured = snapshot_digest(case.capture(session))
        self.assertNotEqual(original, configured)
        target.team = source.team + 1
        beacon = sonic.Beacon(
            source, source.team, fight._eye(target), None, (0, 0, 0), source.match.tick, profile,
        )
        fight.sonars.append(beacon)
        self.assertTrue(sonic.advance(fight, beacon, source.match.tick))
        self.assertIn(target.body, beacon.statuses)
        active = snapshot_digest(case.capture(session))
        beacon.born += 1
        self.assertNotEqual(active, snapshot_digest(case.capture(session)))

    def test_sonic_reflection_transfers_reveal_and_absorption_prevents_it(self):
        for defense, hero, button in (
            ("deflect", 0x02E0000000000029, 16),
            ("grasp", sigma_grasp.HERO, 8),
            ("matrix", dva.HERO, SECONDARY),
        ):
            with self.subTest(defense=defense):
                arena = self.arena()
                defender, _ = support_tests.PulseDeflectTests().support(arena.run, arena.player, hero=hero)
                if defense == "matrix":
                    assets = ("ORPHEUS_GRAPH_SUPPLEMENT", "ORPHEUS_MOVERS_SUPPLEMENT")
                    if not all(os.environ.get(key) for key in assets):
                        self.skipTest("requires extracted Matrix graphs and mover")
                    mech = replace(heroes()[hero], body=dva.MECH)
                    self.assertTrue(arena.run.match.switch_hero(defender, mech))
                defender.team = arena.player.team
                arena.move(defender, (arena.x, arena.y, arena.z + 3))
                for _ in range(100 if defense == "matrix" else 30):
                    arena.run.step({})
                arena.run.step({arena.hanzo: (*arena.aim, 8)})
                arena.run.step({})
                arena.draw()
                fight = arena.fight
                facing = aim(fight._eye(defender), (arena.x, arena.y + 1, arena.z + 12))
                arena.run.step({arena.hanzo: (*arena.aim, FIRE), defender: (*facing, button)})
                held = {defender: (*facing, button if defense == "matrix" else 0)}
                arrow = arena.release(held, definition=hanzo_tests.FULL_SONIC)
                registry = fight.projectiles
                with patch.object(registry, "absorbed", wraps=registry.absorbed) as absorbed:
                    arena.wait_contact(arrow, held)
                self.assertEqual(arena.health.total, 150)
                self.assertFalse(fight.revealed_to(arena.hanzo, arena.player))
                if defense == "deflect":
                    absorbed.assert_not_called()
                    beacon, = fight.sonars
                    self.assertIs(beacon.source, defender)
                    self.assertEqual(beacon.attached, arena.hanzo.body)
                    self.assertEqual(fight.vitals[arena.hanzo.body].total, 75)
                    self.assertTrue(fight.revealed_to(arena.player, arena.hanzo))
                    self.assertFalse(fight.revealed_to(arena.hanzo, arena.hanzo))
                else:
                    absorbed.assert_called_once()
                    self.assertFalse(fight.sonars)
                    health = winston_tests.TracerWinstonBarrierTests.health(arena.run, arena.hanzo)
                    self.assertEqual(health.total, 200)

    def test_sonic_clears_old_tracer_body_and_freezes_attachment_on_replacement(self):
        arena = self.arena()
        self.fire(arena)
        fight = arena.fight
        beacon, = fight.sonars
        old_body = arena.player.body
        provider = beacon.statuses[old_body]
        point = beacon.position
        self.assertTrue(arena.run.match.switch_hero(arena.player, arena.player.hero))
        self.assertNotEqual(arena.player.body, old_body)
        arena.move(arena.player, (arena.x + 16, arena.y, arena.z))
        arena.run.step({})
        self.assertIsNone(beacon.attached)
        self.assertEqual(beacon.position, point)
        self.assertNotIn(old_body, beacon.statuses)
        self.assertTrue(provider.stopped)
        self.assertFalse(fight.revealed_to(arena.hanzo, arena.player))
        arena.move(arena.player, (arena.x, arena.y, arena.z))
        arena.run.step({})
        self.assertIsNone(beacon.attached)
        self.assertEqual(beacon.position, point)
        self.assertTrue(fight.revealed_to(arena.hanzo, arena.player))
        self.assertIsNot(beacon.statuses[arena.player.body], provider)

    def test_sonic_persists_after_tracer_kills_hanzo_and_clears_when_hanzo_leaves(self):
        arena = self.arena()
        self.fire(arena)
        fight = arena.fight
        beacon, = fight.sonars
        health = winston_tests.TracerWinstonBarrierTests.health(arena.run, arena.hanzo)
        health.damage(health.total - 1)
        fight._sync_body_health(arena.hanzo, health)
        facing = aim(fight._eye(arena.player), (arena.x, arena.y + 1, arena.z + 12))
        for _ in range(12):
            arena.run.step({arena.player: (*facing, FIRE)})
            if health.dead:
                break
        self.assertTrue(health.dead)
        self.assertEqual(fight.sonars, [beacon])
        self.assertTrue(fight.revealed_to(arena.hanzo, arena.player))
        arena.run.match.leave(arena.hanzo)
        if arena.hanzo in arena.run.players:
            arena.run.players.remove(arena.hanzo)
        arena.run.step({})
        self.assertFalse(fight.sonars)
        flag = arena.player.body_script.component.entity_var(sonic.REVEALED)
        self.assertFalse(expr.truthy(flag.value()))

    def test_overlapping_sonic_hits_retire_independently_and_preserve_existing_flag(self):
        arena = self.arena()
        self.fire(arena)
        fight = arena.fight
        first, = fight.sonars
        other, _ = support_tests.PulseDeflectTests().support(arena.run, arena.hanzo, hero=hanzo_tests.HANZO)
        other.team = arena.hanzo.team
        x, y, z = arena.x, arena.y, arena.z
        arena.move(other, (x + 2, y, z + 10))
        floor = box(x - 25, x + 25, y - 1, y, z - 25, z + 25)
        mesh = World(floor + wall(x - 5, z + 3, x + 5, z + 3, y, y + 4))
        arena.run.match.collision_world = mesh
        for actor in arena.run.players:
            actor.mover.use(world=mesh)
        direction = aim(fight._eye(other), (x, y + 1, z))
        arena.run.step({other: (*direction, 8)})
        arena.run.step({})
        for _ in range(70):
            arena.run.step({other: (*direction, FIRE)})
        arena.run.step({other: (*direction, 0)})
        arrow, = fight.rockets
        self.assertEqual(arrow.definition, hanzo_tests.FULL_SONIC)
        arena.wait_contact(arrow, {})
        self.assertEqual(arena.health.total, 25)
        original, second = fight.sonars
        self.assertIs(original, first)
        self.assertIsNone(second.attached)
        body = arena.player.body
        self.assertIsNot(first.statuses[body], second.statuses[body])
        flag = arena.player.body_script.component.entity_var(sonic.REVEALED)
        for _ in range(380):
            arena.run.step({})
            if first not in fight.sonars:
                break
        self.assertEqual(fight.sonars, [second])
        self.assertTrue(expr.truthy(flag.value()))
        arena.player.body_script.component.set(sonic.REVEALED, True)
        for _ in range(160):
            arena.run.step({})
            if not fight.sonars:
                break
        self.assertFalse(fight.sonars)
        self.assertTrue(expr.truthy(flag.value()))

    def test_combined_ricochet_deploys_only_at_its_terminal_impact(self):
        for gate in ("body", "lethal", "wall", "projected"):
            with self.subTest(gate=gate):
                arena = self.arena()
                arena.check_storm_ricochet(gate, sonic=True)
                fight = arena.fight
                if gate == "projected":
                    self.assertFalse(fight.sonars)
                    continue
                beacon, = fight.sonars
                self.assertEqual(beacon.attached, None if gate == "wall" else arena.player.body)
                self.assertEqual(fight.revealed_to(arena.hanzo, arena.player), gate != "lethal")
                if gate == "wall":
                    self.assertAlmostEqual(beacon.position[0], arena.hanzo.mover.position[0] + 6, places=3)

    def test_lethal_sonic_impact_creates_a_field_without_marking_dead_tracer(self):
        arena = self.arena()
        arena.health.damage(30)
        arena.fight._sync_body_health(arena.player, arena.health)
        self.fire(arena)
        self.assertTrue(arena.health.dead)
        beacon, = arena.fight.sonars
        self.assertEqual(beacon.attached, arena.player.body)
        self.assertNotIn(arena.player.body, beacon.statuses)
        self.assertFalse(arena.fight.revealed_to(arena.hanzo, arena.player))


class TracerSonicNpcTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.addClassCleanup(npc_tests.TracerNpcTests.doClassCleanups)
        npc_tests.TracerNpcTests.setUpClass()

    def test_all_five_revealed_npcs_exchange_actual_damage_with_tracer(self):
        expected = {name: damage for name, _, damage in npc_tests.TracerNpcTests.FIRST_PRIMARY_IMPACTS}
        for definition in npc_tests.TracerNpcTests.definitions.values():
            with self.subTest(npc=definition.name):
                case = TracerSonicTests()
                arena = case.arena()
                self.addCleanup(npc.remove_spawned, arena.run.match)
                for player in arena.run.players:
                    player.client = npc_tests.NpcClient(player)
                arena.hanzo.team = arena.player.team
                x, y, z = arena.x, arena.y, arena.z
                bot = npc.spawn(arena.run.match, definition, (x, y, z + 5), team=1 - arena.player.team)
                self.assertIsNotNone(bot.body_script)
                self.assertIsNone(bot.body_script.component.fault)
                target = next(t for t in arena.fight._targets() if t.owner is bot)
                health = arena.fight.vitals_of(target)
                before = health.total
                arena.aim = aim(arena.fight._eye(arena.hanzo), (x, y + 1, z + 5))
                case.fire(arena)
                beacon, = arena.fight.sonars
                self.assertEqual(beacon.attached, bot.entity)
                self.assertEqual(set(beacon.statuses), {bot.entity})
                self.assertFalse(health.dead)
                self.assertLess(health.total, before)
                self.assertTrue(arena.fight.revealed_to(arena.player, bot))
                self.assertTrue(expr.truthy(bot.body_script.component.entity_var(sonic.REVEALED).value()))
                self.assertFalse(arena.fight.revealed_to(arena.hanzo, arena.player))
                before = health.total
                direction = aim(arena.fight._eye(arena.player), (x, y + 1, z + 5))
                arena.run.step({arena.player: (*direction, FIRE)})
                arena.run.step({})
                self.assertLess(health.total, before)
                arena.player.mover.yaw, arena.player.mover.pitch = direction
                self.assertEqual(npc_control.start(arena.run.match, arena.player), bot.entity)
                self.addCleanup(npc_control.stop, arena.run.match)
                for _ in range(160):
                    arena.run.step({})
                    if arena.health.total < 150:
                        break
                self.assertEqual(arena.health.total, 150 - expected[definition.name])
                self.assertIn(beacon, arena.fight.sonars)
                self.assertTrue(arena.fight.revealed_to(arena.player, bot))
                self.assertTrue(expr.truthy(bot.body_script.component.entity_var(sonic.REVEALED).value()))
                if definition.name == "Assassin":
                    self.assertEqual(arena.fight.pins.get(bot.entity), arena.player.body)
