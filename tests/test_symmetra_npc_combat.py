"""Actual five-NPC resource fixtures; opt in with external catalog/graph paths.

These tests exercise player input against real NPC definitions and scripts.
They do not supply enemy acquisition/attack AI or claim original-client proof.
Canonical runtime inputs are read only; supplementary graphs are test-scoped.
"""

import gzip
import json
import math
import os
import unittest
from copy import copy
from dataclasses import replace
from itertools import pairwise
from pathlib import Path
from unittest.mock import patch

import test_symmetra_barriers as barrier_tests
import test_symmetra_mei as mei_tests
import test_symmetra_mei_blizzard as blizzard_tests
import test_symmetra_portals as portal_tests
from test_game_combat import FakeClient, aim, in_world
from test_symmetra_charge import charge
from test_symmetra_combat import arena, fire, health
from test_symmetra_matrix import prepare_matrix_resources

from ow174.game import (
    combat,
    cryo,
    deflection,
    fortify,
    heavy_charge,
    matrix,
    npc,
    npc_console,
    npc_control,
    projectile_tags,
    sigma_grasp,
    stats,
    transcendence,
    zarya_barriers,
)
from ow174.game.body_vm_snapshot import export_component_vm
from ow174.game.collision import World
from ow174.game.commands import Command
from ow174.game.content import SOLDIER, heroes
from ow174.game.script import expr, graph
from ow174.game.script.bot import BotScript
from ow174.game.script.driver import OWNER_HEALTH
from ow174.game.transcendence import active_state

CATALOG = os.environ.get("ORPHEUS_PVE_CATALOG")
BUNDLE = os.environ.get("ORPHEUS_PVE_GRAPHS")


@unittest.skipUnless(CATALOG and BUNDLE, "requires explicit external five-NPC resource fixtures")
class SymmetraNpcResourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.definitions = npc.load_catalog(Path(CATALOG))
        if {d.name for d in cls.definitions.values()} != {
            "Trooper", "Enforcer", "Sniper", "Assassin", "Heavy Assault",
        } or any(d.mover is None for d in cls.definitions.values()):
            raise ValueError("five NPC definitions with authored mover data are required")
        cls.bundle = json.loads(gzip.decompress(Path(BUNDLE).read_bytes()))
        cls.extra = {r["index"]: graph.Graph(r) for r in cls.bundle["graphs"]}
        original_graph, original_entity = graph.graph, graph.entity_body

        def lookup(index):
            return original_graph(index) or cls.extra.get(index)

        def entity(body):
            return original_entity(body) or cls.bundle.get("entities", {}).get(f"0x{body:016X}")

        cls.graph_patch = patch.object(graph, "graph", side_effect=lookup)
        cls.entity_patch = patch.object(graph, "entity_body", side_effect=entity)
        cls.graph_patch.start()
        cls.entity_patch.start()
        cls.addClassCleanup(cls.graph_patch.stop)
        cls.addClassCleanup(cls.entity_patch.stop)

    def create(self, definition, *, distance=4, ally=False):
        run, player, other = arena()
        other.mover.position = [40, 1, 40]
        bot = npc.spawn(run.match, definition, (0, 1, distance),
                        team=player.team if ally else 1 - player.team)
        self.assertIsInstance(bot.body_script, BotScript)
        self.assertIs(bot.movement_data, definition.mover)
        self.assertEqual(bot.body_script.component.entity_var(OWNER_HEALTH).value(),
                         definition.health + definition.armour + definition.shields)
        target = next(t for t in run.match.combat._targets() if t.owner is bot)
        self.assertEqual((target.height, target.radius),
                         (definition.mover.stand_height, definition.mover.stand_radius))
        return run, player, bot, target

    @patch.object(FakeClient, "bot_stream", new=lambda _client, _body: None, create=True)
    def test_live_ice_wall_stops_and_releases_all_five_npc_routes(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                self.check_npc_ice_wall_route(definition)

    def check_npc_ice_wall_route(self, definition):
        run, sym, bot, _ = self.create(definition, distance=5)
        mei = run.players[1]
        self.assertTrue(run.match.switch_hero(mei, heroes()[cryo.HERO]))
        sym.mover.position[:] = (10, 1, 5)
        mei.mover.position[:] = (0, 1, 0)
        for _ in range(25):
            run.step({})
        fight = run.match.combat
        angles = aim(fight._eye(mei), (0, 1, 2.5))
        for button in (16, 0, 1, 0):
            run.step({mei: (*angles, button)})
        self.assertEqual(len(fight.ice_wall.pieces), 5)
        center = min(fight.ice_wall.pieces.values(), key=lambda piece: abs(piece.feet[0]))
        destination = (0, 1, .6)
        bot.route = (bot.position, destination)
        bot.wait = 0
        for _ in range(85):
            run.step({})
        expected = center.feet[2] + center.depth + definition.mover.stand_radius
        self.assertAlmostEqual(bot.position[2], expected, places=4)
        self.assertEqual(bot.target, destination)
        self.assertEqual(bot.velocity, (0, 0, 0))

        run.step({mei: (*angles, 16)})
        self.assertFalse(fight.ice_wall.pieces)
        for _ in range(90):
            run.step({})
            if bot.position[2] < center.feet[2] - center.depth:
                break
        self.assertLess(bot.position[2], center.feet[2] - center.depth)

    @patch.object(FakeClient, "bot_stream", new=lambda _client, _body: None, create=True)
    def test_reflected_blizzard_freezes_all_five_and_suppresses_actual_npc_attacks(self):
        fixture = barrier_tests.PhotonBarrierTests()
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                run, sym, mei = mei_tests.SymmetraMeiWeaponTests().duel()
                genji = fixture.add_target(run, 3, (0, 1, 4))
                self.assertTrue(run.match.switch_hero(genji, heroes()[deflection.GENJI]))
                fixture.move(genji, (0, 1, 4))
                sym.team = genji.team = 1
                mei.team = 0
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                fight = run.match.combat
                blizzard_tests.SymmetraBlizzardTests().cast_toward_defender(run, mei, genji, 16)
                for _ in range(100):
                    if fight.blizzard.areas:
                        break
                    run.step({genji: (-32768, 0, 0)})
                [area] = fight.blizzard.areas.values()
                self.assertIs(area.owner, genji)
                x, y, z = area.position
                bot = npc.spawn(run.match, definition, (x + 1, y, z), team=0)
                fixture.move(sym, (x + 2.3, y, z))
                before = health(run, bot).total
                while run.frame * 16 < area.ends and not fight.is_stunned(bot):
                    run.step({})
                self.assertTrue(fight.is_stunned(bot))
                self.assertEqual(bot.body_script.component.entity_var(2427).value(), 1)
                self.assertLess(health(run, bot).total, before)
                sym.mover.yaw, sym.mover.pitch = aim(fight._eye(sym), (x + 1, y + .9, z))
                self.assertEqual(npc_control.start(run.match, sym), bot.entity)
                body = health(run, sym)
                protected = body.total
                frozen_position = bot.position
                bot.route = (frozen_position, (x + 2, y, z))
                for _ in range(35):
                    run.step({})
                    self.assertTrue(fight.is_stunned(bot))
                    self.assertFalse(bot.attack_input)
                    self.assertEqual(body.total, protected)
                    self.assertEqual(bot.position, frozen_position)
                bot.route = ()
                for _ in range(200):
                    run.step({})
                    if body.total < protected:
                        break
                self.assertFalse(fight.is_stunned(bot))
                self.assertLess(body.total, protected)

    @unittest.skipUnless(os.environ.get("ORPHEUS_EXTRA_GRAPH_BUNDLES"), "requires summon graph shard")
    @patch.object(FakeClient, "bot_stream", new=lambda _client, _body: None, create=True)
    def test_sentry_keeps_damage_and_slow_through_actual_mei_freeze_on_all_five_npcs(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                stepped = self.sentry_across_npc_thaw(definition, schedule="stepped")
                self.assertEqual(self.sentry_across_npc_thaw(definition, schedule="split"), stepped)
                self.assertEqual(self.sentry_across_npc_thaw(definition, schedule="single"), stepped)
                crosses_thaw = self.sentry_across_npc_thaw(definition, schedule="cross_thaw")
                self.assertEqual(crosses_thaw[:2], stepped[:2])
                self.assertEqual(crosses_thaw[3:], stepped[3:])

    @unittest.skipUnless(os.environ.get("ORPHEUS_EXTRA_GRAPH_BUNDLES"), "requires summon graph shard")
    @patch.object(FakeClient, "bot_stream", new=lambda _client, _body: None, create=True)
    def test_sentry_kills_at_same_tick_in_a_single_gap_through_thaw_on_all_five_npcs(self):
        for definition in self.definitions.values():
            for lethal_health, lethal_tick in ((48, 75), (52, 82)):
                with self.subTest(npc=definition.name, lethal_health=lethal_health):
                    lethal = lethal_health, lethal_tick, 0
                    stepped = self.sentry_across_npc_thaw(definition, schedule="stepped", lethal=lethal)
                    skipped = self.sentry_across_npc_thaw(definition, schedule="cross_thaw", lethal=lethal)
                    self.assertEqual(skipped, stepped)

    @unittest.skipUnless(os.environ.get("ORPHEUS_EXTRA_GRAPH_BUNDLES"), "requires summon graph shard")
    @patch.object(FakeClient, "bot_stream", new=lambda _client, _body: None, create=True)
    def test_two_sentry_owners_keep_lethal_attribution_through_thaw_on_all_five_npcs(self):
        for definition in self.definitions.values():
            for lethal in ((96.32, 76, 0), (104.64, 82, 1)):
                with self.subTest(npc=definition.name, lethal=lethal):
                    stepped = self.sentry_across_npc_thaw(
                        definition, schedule="stepped", paired=True, lethal=lethal,
                    )
                    skipped = self.sentry_across_npc_thaw(
                        definition, schedule="cross_thaw", paired=True, lethal=lethal,
                    )
                    self.assertEqual(skipped, stepped)

    @unittest.skipUnless(os.environ.get("ORPHEUS_EXTRA_GRAPH_BUNDLES"), "requires summon graph shard")
    @patch.object(FakeClient, "bot_stream", new=lambda _client, _body: None, create=True)
    def test_two_sentry_owners_keep_damage_charge_and_slow_through_thaw_on_all_five_npcs(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                stepped = self.sentry_across_npc_thaw(definition, schedule="stepped", paired=True)
                skipped = self.sentry_across_npc_thaw(definition, schedule="cross_thaw", paired=True)
                self.assertEqual(skipped[:2], stepped[:2])
                self.assertEqual(skipped[3:], stepped[3:])

    @unittest.skipUnless(os.environ.get("ORPHEUS_EXTRA_GRAPH_BUNDLES"), "requires summon graph shard")
    @patch.object(FakeClient, "bot_stream", new=lambda _client, _body: None, create=True)
    def test_thaw_route_breaks_only_one_of_two_sentry_locks_on_all_five_npcs(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                stepped = self.sentry_across_npc_thaw(
                    definition, schedule="stepped", paired=True, occluded=True,
                )
                skipped = self.sentry_across_npc_thaw(
                    definition, schedule="cross_thaw", paired=True, occluded=True,
                )
                self.assertEqual(skipped[:2], stepped[:2])
                self.assertEqual(skipped[3:], stepped[3:])

    def sentry_across_npc_thaw(self, definition, *, schedule, lethal=None, paired=False, occluded=False):
        from test_symmetra_turrets import wall

        run, sym, bot, _ = self.create(definition)
        mei = run.players[1]
        self.assertTrue(run.match.switch_hero(mei, heroes()[cryo.HERO]))
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        mei.team = sym.team
        fixture = barrier_tests.PhotonBarrierTests()
        fixture.move(mei, (5, 1, 1))
        owners = [sym]
        if paired:
            second = fixture.add_target(run, 3, (3, 1, 0))
            self.assertTrue(run.match.switch_hero(second, sym.hero))
            second.team = sym.team
            owners.append(second)
            _, pitch = aim((0, 2.55, 0), (0, 2.0, 6))
        slow_percent = .2 * len(owners)
        beam_rate = 40 * len(owners)
        bot.position = (2, 1, 1)
        fight = run.match.combat
        pool = health(run, bot)
        component = bot.body_script.component
        slow = component.entity_var(23186)
        world = wall()
        if occluded:
            # The left beam crosses x=1 only after the thawed target moves
            # far enough along +z. The right beam never crosses this plane.
            world = World([
                (-10, -5, 3, 10, -5, 3, 10, 8, 3),
                (-10, -5, 3, 10, 8, 3, -10, 8, 3),
                (1, -5, 2.06, 1, 8, 2.06, 1, 8, 2.9),
                (1, -5, 2.06, 1, 8, 2.9, 1, -5, 2.9),
            ])
        with patch.object(fight, "_collision", return_value=world):
            fire(run, sym, 40, buttons=8)
            if paired:
                second.mover.position[:] = (3, 1, 0)
                second.mover.body = None
            spray = aim(fight._eye(mei), (2, 1.9, 1))
            for frame in range(135):
                inputs = {mei: (*spray, 1)}
                if paired and 8 <= frame < 48:
                    inputs[second] = (0, pitch, 8)
                run.step(inputs)
                if fight.is_stunned(bot):
                    break
            for _ in range(3):
                run.step({})
            self.assertTrue(fight.is_stunned(bot))
            self.assertEqual(component.entity_var(2427).value(), 1)
            sentries = fight.sentries.sentries
            self.assertCountEqual([sentry.owner for sentry in sentries], owners)
            for sentry in sentries:
                self.assertIsNotNone(sentry.attached_at, (sentry.owner.name, sentry.bot.position))
                self.assertEqual(sentry.target, bot.entity, (sentry.owner.name, sentry.bot.position))
            self.assertAlmostEqual(slow.value(), slow_percent)
            auras = [sentry.slow[1] for sentry in sentries]
            start = run.frame
            deadline = fight.frost_hits[bot.entity][2]
            tick_ms = round(combat.TICK * 1000)
            thaw = (deadline + tick_ms - 1) // tick_ms
            frozen_checkpoint = thaw - 5
            end = thaw + 10
            self.assertGreater(frozen_checkpoint - start, combat.COMBAT_CATCH_UP)
            if paired:
                # Restore the health spent establishing the two beams and the freeze.
                pool.now[combat.HEALTH_POOL] = pool.most[combat.HEALTH_POOL]
            if lethal is not None:
                lethal_health, lethal_tick, killer_index = lethal
                pool.damage(pool.total - lethal_health)
                self.assertAlmostEqual(pool.total, lethal_health)
            start_pool = pool.total
            start_charge = [charge(owner) for owner in owners]
            start_damage = [owner.stats.value(stats.HERO_DAMAGE) for owner in owners]
            start_elims = [owner.stats.value(stats.ELIMINATIONS) for owner in owners]
            mei_damage = mei.stats.value(stats.HERO_DAMAGE)
            frozen_pose = bot.position
            bot.route = ((2.0, 1.0, 5.0),)
            bot.wait = 0.0
            if schedule == "cross_thaw":
                frozen_checkpoint = start
            frozen_ticks = frozen_checkpoint - start
            if schedule in {"stepped", "cross_thaw"}:
                updates = range(start + 1, frozen_checkpoint + 1)
            elif schedule == "split":
                updates = (start + frozen_ticks // 2, frozen_checkpoint)
            else:
                updates = (frozen_checkpoint,)
            for tick in updates:
                run.match.update(tick * combat.TICK, tick)
            frozen_damage = start_pool - pool.total
            self.assertTrue(fight.is_stunned(bot))
            self.assertEqual(bot.position, frozen_pose)
            self.assertAlmostEqual(frozen_damage, beam_rate * frozen_ticks * combat.TICK)
            for sentry, aura in zip(sentries, auras, strict=True):
                self.assertIs(sentry.slow[1], aura)

            moved = []
            blocked_ticks = []
            route_tick = bot._tick
            update_sentries = fight.sentries.update

            def observe_route(tick, *args):
                old = bot.position
                route_tick(tick, *args)
                if bot.position != old:
                    moved.append(tick)

            def observe_sentries(*args, **kwargs):
                if not occluded:
                    return update_sentries(*args, **kwargs)
                before = [owner.stats.value(stats.HERO_DAMAGE) for owner in owners]
                update_sentries(*args, **kwargs)
                target = next(target for target in fight._targets() if target.owner is bot)
                for index, (sentry, aura) in enumerate(zip(sentries, auras, strict=True)):
                    direction, reach = combat.capsule_ray(sentry.bot.position, target)
                    blocked = world.cast_ray(sentry.bot.position, direction, reach) is not None
                    if blocked:
                        self.assertEqual(index, 0)
                        blocked_ticks.append(fight.tick)
                    self.assertEqual(sentry.target, None if blocked else bot.entity)
                    if blocked:
                        self.assertIsNone(sentry.slow)
                    elif index == 1 or not blocked_ticks:
                        self.assertIs(sentry.slow[1], aura)
                    else:
                        self.assertIsNot(sentry.slow[1], aura)
                    credited = sentry.owner.stats.value(stats.HERO_DAMAGE) - before[index]
                    self.assertAlmostEqual(credited, 0 if blocked else 40 * combat.TICK)
                self.assertAlmostEqual(slow.value(), .2 if fight.tick in blocked_ticks else .4)

            thaw_updates = range(frozen_checkpoint + 1, end + 1) if schedule == "stepped" else (end,)
            with (
                patch.object(bot, "_tick", side_effect=observe_route),
                patch.object(fight.sentries, "update", side_effect=observe_sentries),
            ):
                for tick in thaw_updates:
                    run.match.update(tick * combat.TICK, tick)
            self.assertFalse(fight.is_stunned(bot))
            self.assertFalse(component.entity_var(2427).value())
            if lethal is not None:
                self.assertTrue(bot.dead)
                self.assertAlmostEqual(pool.total, 0)
                self.assertEqual(pool.death_tick, start + lethal_tick)
                self.assertEqual(moved[0] if moved else None, thaw if pool.death_tick >= thaw else None)
                self.assertEqual(pool.killer, owners[killer_index].body)
                beam_tick_damage = 40 * combat.TICK
                full_damage = (lethal_tick - 1) * beam_tick_damage
                terminal_damage = start_pool - len(owners) * full_damage
                damage_per_owner, charge_per_owner = [], []
                for index, owner in enumerate(owners):
                    eliminations = owner.stats.value(stats.ELIMINATIONS) - start_elims[index]
                    self.assertEqual(eliminations, int(index == killer_index))
                    final_hit = min(beam_tick_damage, max(0, terminal_damage - index * beam_tick_damage))
                    damage = owner.stats.value(stats.HERO_DAMAGE) - start_damage[index]
                    self.assertAlmostEqual(damage, full_damage + final_hit)
                    earned = charge(owner) - start_charge[index]
                    self.assertTrue(any(abs(earned - damage - amount) < .001 for amount in (5, 10)))
                    damage_per_owner.append(round(damage, 4))
                    charge_per_owner.append(round(earned, 3))
                for sentry in sentries:
                    self.assertIsNone(sentry.slow)
                return pool.death_tick, bot.position, tuple(damage_per_owner), tuple(charge_per_owner)
            self.assertTrue(moved)
            self.assertEqual(moved[0], thaw)
            self.assertFalse(bot.dead)
            self.assertGreater(bot.position[2], frozen_pose[2])
            if occluded:
                self.assertTrue(blocked_ticks)
                self.assertLess(thaw, blocked_ticks[0])
                self.assertLess(blocked_ticks[0], end)
                self.assertNotIn(auras[0].id, component.instances)
            else:
                for sentry, aura in zip(sentries, auras, strict=True):
                    self.assertEqual(sentry.target, bot.entity)
                    self.assertIs(sentry.slow[1], aura)
            self.assertAlmostEqual(slow.value(), slow_percent - (.2 if end in blocked_ticks else 0))
            movement_slow = slow_percent - (.2 if end - 1 in blocked_ticks else 0)
            self.assertAlmostEqual(bot.speed, definition.mover.run_forward * (1 - movement_slow), places=5)
            damage = start_pool - pool.total
            expected = [40 * (end - start) * combat.TICK] * len(owners)
            expected[0] -= 40 * len(blocked_ticks) * combat.TICK
            self.assertAlmostEqual(damage, sum(expected))
            passive = []
            owner_baselines = zip(owners, start_damage, start_charge, expected, strict=True)
            for owner, original_damage, original_charge, expected_damage in owner_baselines:
                credited = owner.stats.value(stats.HERO_DAMAGE) - original_damage
                self.assertAlmostEqual(credited, expected_damage)
                passive.append(charge(owner) - original_charge - credited)
                self.assertTrue(any(abs(passive[-1] - amount) < .001 for amount in (5, 10)))
            self.assertEqual(mei.stats.value(stats.HERO_DAMAGE), mei_damage)
        passive_credit = tuple(round(value, 3) for value in passive)
        return (
            thaw, bot.position, round(frozen_damage, 4), round(damage, 4),
            passive_credit, tuple(blocked_ticks),
        )

    @unittest.skipUnless(os.environ.get("ORPHEUS_EXTRA_GRAPH_BUNDLES"), "requires summon graph shard")
    @patch.object(FakeClient, "bot_stream", new=lambda _client, _body: None, create=True)
    def test_ice_supported_sentry_expires_on_both_sides_of_npc_thaw(self):
        for definition in self.definitions.values():
            for expiry_first in (True, False):
                with self.subTest(npc=definition.name, expiry_first=expiry_first):
                    stepped = self.ice_sentry_across_npc_thaw(definition, expiry_first, gap=False)
                    skipped = self.ice_sentry_across_npc_thaw(definition, expiry_first, gap=True)
                    self.assertEqual(skipped, stepped)

    @unittest.skipUnless(os.environ.get("ORPHEUS_EXTRA_GRAPH_BUNDLES"), "requires summon graph shard")
    @patch.object(FakeClient, "bot_stream", new=lambda _client, _body: None, create=True)
    def test_ice_supported_sentry_cancels_at_checkpoints_around_npc_thaw(self):
        for definition in self.definitions.values():
            for offset in (-4, 0, 3):
                with self.subTest(npc=definition.name, cancel_offset=offset):
                    stepped = self.ice_sentry_across_npc_thaw(
                        definition, False, gap=False, checkpoint_offset=offset,
                    )
                    skipped = self.ice_sentry_across_npc_thaw(
                        definition, False, gap=True, checkpoint_offset=offset,
                    )
                    self.assertEqual(skipped, stepped)

    @unittest.skipUnless(os.environ.get("ORPHEUS_EXTRA_GRAPH_BUNDLES"), "requires summon graph shard")
    @patch.object(FakeClient, "bot_stream", new=lambda _client, _body: None, create=True)
    def test_enemy_rifle_breaks_ice_sentry_support_at_checkpoints_around_npc_thaw(self):
        for definition in self.definitions.values():
            for offset in (-4, 0, 3):
                with self.subTest(npc=definition.name, break_offset=offset):
                    stepped = self.ice_sentry_across_npc_thaw(
                        definition, False, gap=False, checkpoint_offset=offset, rifle_break=True,
                    )
                    skipped = self.ice_sentry_across_npc_thaw(
                        definition, False, gap=True, checkpoint_offset=offset, rifle_break=True,
                    )
                    self.assertEqual(skipped, stepped)

    @unittest.skipUnless(os.environ.get("ORPHEUS_EXTRA_GRAPH_BUNDLES"), "requires summon graph shard")
    @patch.object(FakeClient, "bot_stream", new=lambda _client, _body: None, create=True)
    def test_existing_enemy_weakens_and_breaks_ice_sentry_support_around_npc_thaw(self):
        for definition in self.definitions.values():
            for offset in (-4, 0, 3):
                with self.subTest(npc=definition.name, break_offset=offset):
                    stepped = self.ice_sentry_across_npc_thaw(
                        definition, False, gap=False, checkpoint_offset=offset, rifle_break="present",
                    )
                    skipped = self.ice_sentry_across_npc_thaw(
                        definition, False, gap=True, checkpoint_offset=offset, rifle_break="present",
                    )
                    self.assertEqual(skipped, stepped)

    @unittest.skipUnless(os.environ.get("ORPHEUS_EXTRA_GRAPH_BUNDLES"), "requires summon graph shard")
    @patch.object(FakeClient, "bot_stream", new=lambda _client, _body: None, create=True)
    def test_in_flight_helix_breaks_ice_sentry_support_before_npc_thaw(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                stepped = self.ice_sentry_across_npc_thaw(definition, False, gap=False, rifle_break="helix")
                skipped = self.ice_sentry_across_npc_thaw(definition, False, gap=True, rifle_break="helix")
                self.assertEqual(skipped, stepped)

    @unittest.skipUnless(os.environ.get("ORPHEUS_EXTRA_GRAPH_BUNDLES"), "requires summon graph shard")
    @patch.object(FakeClient, "bot_stream", new=lambda _client, _body: None, create=True)
    def test_in_flight_helix_breaks_route_blocking_support_on_or_after_thaw(self):
        for definition in self.definitions.values():
            for offset in (0, 3):
                with self.subTest(npc=definition.name, impact_offset=offset):
                    stepped = self.ice_sentry_across_npc_thaw(
                        definition, False, gap=False, rifle_break="helix", impact_offset=offset,
                    )
                    skipped = self.ice_sentry_across_npc_thaw(
                        definition, False, gap=True, rifle_break="helix", impact_offset=offset,
                    )
                    self.assertEqual(skipped, stepped)

    @unittest.skipUnless(os.environ.get("ORPHEUS_EXTRA_GRAPH_BUNDLES"), "requires summon graph shard")
    @patch.object(FakeClient, "bot_stream", new=lambda _client, _body: None, create=True)
    def test_helix_breaks_support_and_splashes_or_kills_frozen_npcs(self):
        for definition in self.definitions.values():
            for starting_health in (30, 150):
                with self.subTest(npc=definition.name, starting_health=starting_health):
                    stepped = self.ice_sentry_across_npc_thaw(
                        definition, False, gap=False, rifle_break="helix", splash_health=starting_health,
                    )
                    skipped = self.ice_sentry_across_npc_thaw(
                        definition, False, gap=True, rifle_break="helix", splash_health=starting_health,
                    )
                    self.assertEqual(skipped, stepped)

    @unittest.skipUnless(os.environ.get("ORPHEUS_EXTRA_GRAPH_BUNDLES"), "requires summon graph shard")
    @patch.object(FakeClient, "bot_stream", new=lambda _client, _body: None, create=True)
    def test_helix_splashes_a_player_and_each_frozen_npc_in_one_impact(self):
        for definition in self.definitions.values():
            for starting_health in (30, 150):
                with self.subTest(npc=definition.name, starting_health=starting_health):
                    stepped = self.ice_sentry_across_npc_thaw(
                        definition, False, gap=False, rifle_break="helix",
                        splash_health=starting_health, second_splash=True,
                    )
                    skipped = self.ice_sentry_across_npc_thaw(
                        definition, False, gap=True, rifle_break="helix",
                        splash_health=starting_health, second_splash=True,
                    )
                    self.assertEqual(skipped, stepped)

    @unittest.skipUnless(os.environ.get("ORPHEUS_EXTRA_GRAPH_BUNDLES"), "requires summon graph shard")
    @patch.object(FakeClient, "bot_stream", new=lambda _client, _body: None, create=True)
    def test_helix_kills_player_with_frozen_npc_splash_and_respawns_at_deadline(self):
        for definition in self.definitions.values():
            for starting_health in (30, 150):
                with self.subTest(npc=definition.name, starting_health=starting_health):
                    stepped = self.ice_sentry_across_npc_thaw(
                        definition, False, gap=False, rifle_break="helix",
                        splash_health=starting_health, second_splash=True, player_lethal=True,
                    )
                    skipped = self.ice_sentry_across_npc_thaw(
                        definition, False, gap=True, rifle_break="helix",
                        splash_health=starting_health, second_splash=True, player_lethal=True,
                    )
                    self.assertEqual(skipped, stepped)

    @unittest.skipUnless(os.environ.get("ORPHEUS_EXTRA_GRAPH_BUNDLES"), "requires summon graph shard")
    @patch.object(FakeClient, "bot_stream", new=lambda _client, _body: None, create=True)
    def test_helix_splashes_a_frozen_npc_and_a_second_talon_body(self):
        definitions = tuple(self.definitions.values())
        for index, definition in enumerate(definitions):
            extra_npc = definitions[(index + 1) % len(definitions)]
            for starting_health in (30, 150):
                with self.subTest(npc=definition.name, extra=extra_npc.name, starting_health=starting_health):
                    stepped = self.ice_sentry_across_npc_thaw(
                        definition, False, gap=False, rifle_break="helix",
                        splash_health=starting_health, extra_npc=extra_npc,
                    )
                    skipped = self.ice_sentry_across_npc_thaw(
                        definition, False, gap=True, rifle_break="helix",
                        splash_health=starting_health, extra_npc=extra_npc,
                    )
                    self.assertEqual(skipped, stepped)

    @unittest.skipUnless(os.environ.get("ORPHEUS_EXTRA_GRAPH_BUNDLES"), "requires summon graph shard")
    @patch.object(FakeClient, "bot_stream", new=lambda _client, _body: None, create=True)
    def test_helix_uses_moving_talon_recipient_pose_on_the_impact_tick(self):
        definitions = tuple(self.definitions.values())
        for index, definition in enumerate(definitions):
            extra_npc = definitions[(index + 1) % len(definitions)]
            for motion in ("enter", "leave"):
                with self.subTest(npc=definition.name, extra=extra_npc.name, motion=motion):
                    stepped = self.ice_sentry_across_npc_thaw(
                        definition, False, gap=False, rifle_break="helix",
                        splash_health=30, extra_npc=extra_npc, splash_motion=motion,
                    )
                    skipped = self.ice_sentry_across_npc_thaw(
                        definition, False, gap=True, rifle_break="helix",
                        splash_health=30, extra_npc=extra_npc, splash_motion=motion,
                    )
                    self.assertEqual(skipped, stepped)

    @unittest.skipUnless(os.environ.get("ORPHEUS_EXTRA_GRAPH_BUNDLES"), "requires summon graph shard")
    @patch.object(FakeClient, "bot_stream", new=lambda _client, _body: None, create=True)
    def test_moving_talon_intercepts_helix_before_ice_support(self):
        self.check_talon_helix_interception_short_of_ice_support(interceptor_lethal=False)

    def test_helix_kills_talon_interceptor_short_of_ice_support(self):
        self.check_talon_helix_interception_short_of_ice_support(interceptor_lethal=True)

    def check_talon_helix_interception_short_of_ice_support(self, *, interceptor_lethal):
        definitions = tuple(self.definitions.values())
        for index, definition in enumerate(definitions):
            extra_npc = definitions[(index + 1) % len(definitions)]
            with self.subTest(npc=definition.name, interceptor=extra_npc.name):
                stepped = self.ice_sentry_across_npc_thaw(
                    definition, False, gap=False, rifle_break="helix",
                    splash_health=150, extra_npc=extra_npc, splash_motion="intercept",
                    interceptor_lethal=interceptor_lethal,
                )
                skipped = self.ice_sentry_across_npc_thaw(
                    definition, False, gap=True, rifle_break="helix",
                    splash_health=150, extra_npc=extra_npc, splash_motion="intercept",
                    interceptor_lethal=interceptor_lethal,
                )
                self.assertEqual(skipped, stepped)

    def test_talon_intercepts_helix_and_splash_breaks_ice_support(self):
        self.check_talon_helix_interception_at_ice_support(interceptor_lethal=False)

    def test_helix_kills_talon_interceptor_before_splash_breaks_ice_support(self):
        self.check_talon_helix_interception_at_ice_support(interceptor_lethal=True)

    def check_talon_helix_interception_at_ice_support(self, *, interceptor_lethal):
        definitions = tuple(self.definitions.values())
        for index, definition in enumerate(definitions):
            interceptor = definitions[(index + 1) % len(definitions)]
            for splash_health in (30, 150):
                with self.subTest(npc=definition.name, interceptor=interceptor.name, health=splash_health):
                    scenario = {
                        "rifle_break": "helix", "splash_health": splash_health,
                        "extra_npc": interceptor, "splash_motion": "intercept_near",
                        "interceptor_lethal": interceptor_lethal,
                    }
                    stepped = self.ice_sentry_across_npc_thaw(definition, False, gap=False, **scenario)
                    skipped = self.ice_sentry_across_npc_thaw(definition, False, gap=True, **scenario)
                    self.assertEqual(skipped, stepped)

    def test_ice_sentry_retargets_moving_talon_before_helix_breaks_support(self):
        self.check_ice_sentry_retarget(150, receiver_lethal=False)

    def test_helix_kills_talon_after_ice_sentry_retargets(self):
        for primary_health, receiver_lethal in ((30, False), (150, True), (30, True)):
            self.check_ice_sentry_retarget(primary_health, receiver_lethal=receiver_lethal)

    def test_retargeted_ice_sentry_kills_talon_before_helix_impact(self):
        for primary_health in (30, 150):
            self.check_ice_sentry_retarget(primary_health, receiver_lethal=False, receiver_beam_lethal=True)

    def test_talon_intercepts_helix_after_acquiring_ice_sentry_lock(self):
        definitions = tuple(self.definitions.values())
        for index, definition in enumerate(definitions):
            interceptor = definitions[(index + 1) % len(definitions)]
            for primary_health, lethal in ((150, False), (150, True), (30, False), (30, True)):
                with self.subTest(npc=definition.name, interceptor=interceptor.name,
                                  primary_health=primary_health, lethal=lethal):
                    scenario = {
                        "rifle_break": "helix", "splash_health": primary_health, "extra_npc": interceptor,
                        "splash_motion": "retarget_intercept", "interceptor_lethal": lethal,
                    }
                    stepped = self.ice_sentry_across_npc_thaw(definition, False, gap=False, **scenario)
                    skipped = self.ice_sentry_across_npc_thaw(definition, False, gap=True, **scenario)
                    self.assertEqual(skipped, stepped)

    def check_ice_sentry_retarget(self, primary_health, *, receiver_lethal, receiver_beam_lethal=False):
        definitions = tuple(self.definitions.values())
        for index, definition in enumerate(definitions):
            receiver = definitions[(index + 1) % len(definitions)]
            with self.subTest(npc=definition.name, receiver=receiver.name,
                              primary_health=primary_health, receiver_lethal=receiver_lethal):
                scenario = {
                    "rifle_break": "helix", "splash_health": primary_health,
                    "extra_npc": receiver, "splash_motion": "retarget",
                    "receiver_lethal": receiver_lethal,
                    "receiver_beam_lethal": receiver_beam_lethal,
                }
                stepped = self.ice_sentry_across_npc_thaw(definition, False, gap=False, **scenario)
                skipped = self.ice_sentry_across_npc_thaw(definition, False, gap=True, **scenario)
                self.assertEqual(skipped, stepped)

    def test_far_helix_interception_after_ice_sentry_retarget(self):
        definitions = tuple(self.definitions.values())
        for index, definition in enumerate(definitions):
            interceptor = definitions[(index + 1) % len(definitions)]
            for primary_health, lethal in ((150, False), (150, True), (30, False), (30, True)):
                with self.subTest(npc=definition.name, interceptor=interceptor.name,
                                  primary_health=primary_health, lethal=lethal):
                    scenario = {
                        "rifle_break": "helix", "splash_health": primary_health, "extra_npc": interceptor,
                        "splash_motion": "retarget_intercept_far", "interceptor_lethal": lethal,
                    }
                    stepped = self.ice_sentry_across_npc_thaw(definition, False, gap=False, **scenario)
                    skipped = self.ice_sentry_across_npc_thaw(definition, False, gap=True, **scenario)
                    self.assertEqual(skipped, stepped)

    def test_third_talon_intercepts_helix_after_ice_sentry_retargets(self):
        self.check_three_talon_retarget_splash(((150, False),))

    def test_third_talon_interception_lethal_splash_after_retarget(self):
        self.check_three_talon_retarget_splash(((30, False), (150, True), (30, True)))

    def test_near_third_talon_intercept_breaks_ice_after_retarget(self):
        self.check_three_talon_retarget_splash(
            ((150, False), (30, False), (150, True), (30, True)), near=True,
        )

    def check_three_talon_retarget_splash(self, splash_outcomes, *, near=False):
        definitions = tuple(self.definitions.values())
        for index, definition in enumerate(definitions):
            receiver = definitions[(index + 1) % len(definitions)]
            interceptor = definitions[(index + 2) % len(definitions)]
            for primary_health, receiver_lethal in splash_outcomes:
                for third_lethal in (False, True):
                    self.check_three_talon_retarget_pair(
                        definition, receiver, interceptor, primary_health, receiver_lethal, third_lethal,
                        near=near,
                    )

    def check_three_talon_retarget_pair(
        self, definition, receiver, interceptor, primary_health, receiver_lethal, third_lethal, *, near,
    ):
        with self.subTest(
            npc=definition.name, receiver=receiver.name, interceptor=interceptor.name,
            primary_health=primary_health, receiver_lethal=receiver_lethal, third_lethal=third_lethal,
        ):
            scenario = {
                "rifle_break": "helix", "splash_health": primary_health, "extra_npc": receiver,
                "splash_motion": "retarget_intercept" if near else "retarget_intercept_far",
                "third_definition": interceptor,
                "receiver_lethal": receiver_lethal, "third_lethal": third_lethal,
            }
            stepped = self.ice_sentry_across_npc_thaw(definition, False, gap=False, **scenario)
            skipped = self.ice_sentry_across_npc_thaw(definition, False, gap=True, **scenario)
            self.assertEqual(skipped, stepped)

    def test_pharah_primary_breaks_ice_after_sentry_retarget(self):
        for intercepted in (False, True):
            self.check_pharah_primary_after_sentry_retarget(intercepted, ((False, False, False),))

    def test_pharah_primary_lethal_after_sentry_retarget(self):
        for intercepted in (False, True):
            outcomes = [
                (primary, receiver, interceptor)
                for primary in (False, True)
                for receiver in (False, True)
                for interceptor in ((False, True) if intercepted else (False,))
                if primary or receiver or interceptor
            ]
            self.check_pharah_primary_after_sentry_retarget(intercepted, outcomes)

    def test_pharah_far_interception_by_current_sentry_target(self):
        outcomes = [(primary, receiver, False) for primary in (False, True) for receiver in (False, True)]
        self.check_pharah_primary_after_sentry_retarget(False, outcomes, far=True)

    def test_reflected_pharah_far_interception_by_current_sentry_target(self):
        outcomes = [(primary, receiver, False) for primary in (False, True) for receiver in (False, True)]
        self.check_pharah_primary_after_sentry_retarget(False, outcomes, far=True, reflected=True)

    def test_pharah_far_interception_by_third_talon(self):
        self.check_pharah_far_third_interception(reflected=False)

    def test_reflected_pharah_far_interception_by_third_talon(self):
        self.check_pharah_far_third_interception(reflected=True)

    def check_pharah_far_third_interception(self, *, reflected):
        outcomes = [
            (primary, receiver, interceptor)
            for primary in (False, True)
            for receiver in (False, True)
            for interceptor in (False, True)
        ]
        self.check_pharah_primary_after_sentry_retarget(True, outcomes, far=True, reflected=reflected)

    def check_pharah_primary_after_sentry_retarget(
        self, intercepted, outcomes, *, far=False, reflected=False,
    ):
        motion = "retarget_intercept" if intercepted else "retarget"
        if far:
            motion = "retarget_intercept_far"
        definitions = tuple(self.definitions.values())
        for index, definition in enumerate(definitions):
            receiver = definitions[(index + 1) % len(definitions)]
            third = definitions[(index + 2) % len(definitions)] if intercepted else None
            for primary_lethal, receiver_lethal, third_lethal in outcomes:
                with self.subTest(
                    npc=definition.name, receiver=receiver.name, intercepted=intercepted,
                    primary_lethal=primary_lethal, receiver_lethal=receiver_lethal, third_lethal=third_lethal,
                ):
                    scenario = {
                        "rifle_break": "helix", "splash_health": 30 if primary_lethal else 150,
                        "extra_npc": receiver,
                        "splash_motion": motion,
                        "third_definition": third, "pharah_rocket": True,
                        "reflected_pharah": reflected,
                        "receiver_lethal": receiver_lethal and (intercepted or not far),
                        "interceptor_lethal": far and not intercepted and receiver_lethal,
                        "third_lethal": third_lethal,
                    }
                    stepped = self.ice_sentry_across_npc_thaw(definition, False, gap=False, **scenario)
                    skipped = self.ice_sentry_across_npc_thaw(definition, False, gap=True, **scenario)
                    self.assertEqual(skipped, stepped)

    def test_reflected_pharah_rocket_breaks_ice_after_sentry_retarget(self):
        outcomes = [(primary, receiver, False) for primary in (False, True) for receiver in (False, True)]
        self.check_pharah_primary_after_sentry_retarget(False, outcomes, reflected=True)

    def test_reflected_pharah_rocket_interception_breaks_ice(self):
        outcomes = [
            (primary, receiver, interceptor)
            for primary in (False, True)
            for receiver in (False, True)
            for interceptor in (False, True)
        ]
        self.check_pharah_primary_after_sentry_retarget(True, outcomes, reflected=True)

    def launch_reflected_pharah_rocket(self, run, genji, center, fixture):
        fight = run.match.combat
        self.assertTrue(run.match.switch_hero(genji, heroes()[deflection.GENJI]))
        fixture.move(genji, (-18, 1, 28))
        pharah = fixture.add_target(run, 5, (-11, 1, 28))
        self.assertTrue(run.match.switch_hero(pharah, heroes()[barrier_tests.PHARAH]))
        pharah.team = 1 - genji.team
        fixture.move(pharah, (-11, 1, 28))
        redirect = aim(fight._eye(genji), (center.feet[0], 2, 28))
        run.step({genji: (*redirect, 16)})
        self.assertIsNotNone(deflection.active(genji))
        toward_genji = aim(fight._eye(pharah), fight._eye(genji))
        run.step({pharah: (*toward_genji, 1)})
        [rocket] = fight.rockets
        self.assertIs(rocket.shooter, pharah)
        self.assertFalse(rocket.reflected)
        fixture.move(pharah, (-11, 1, 36))
        for _ in range(20):
            run.step({genji: (*redirect, 0)})
            if rocket.shooter is genji:
                break
        self.assertIs(rocket.shooter, genji)
        self.assertEqual(rocket.team, genji.team)
        self.assertTrue(rocket.reflected)
        self.assertIsNotNone(deflection.active(genji))
        return rocket

    def test_projected_talon_absorbs_helix_splash_at_ice_support(self):
        definitions = tuple(self.definitions.values())
        for index, definition in enumerate(definitions):
            receiver = definitions[(index + 1) % len(definitions)]
            for capacity in (200, 30):
                with self.subTest(npc=definition.name, recipient=receiver.name, capacity=capacity):
                    options = {
                        "rifle_break": "helix", "splash_health": 150, "extra_npc": receiver,
                        "splash_motion": "enter", "projected_capacity": capacity,
                    }
                    stepped = self.ice_sentry_across_npc_thaw(definition, False, gap=False, **options)
                    skipped = self.ice_sentry_across_npc_thaw(definition, False, gap=True, **options)
                    self.assertEqual(skipped, stepped)

    def test_projected_talon_intercepts_helix_near_and_far_from_ice(self):
        definitions = tuple(self.definitions.values())
        for index, definition in enumerate(definitions):
            receiver = definitions[(index + 1) % len(definitions)]
            for motion in ("intercept", "intercept_near"):
                for capacity in (40, 41, 200):
                    with self.subTest(
                        npc=definition.name, recipient=receiver.name, motion=motion, capacity=capacity,
                    ):
                        options = {
                            "rifle_break": "helix", "splash_health": 150, "extra_npc": receiver,
                            "splash_motion": motion, "projected_capacity": capacity,
                        }
                        stepped = self.ice_sentry_across_npc_thaw(definition, False, gap=False, **options)
                        skipped = self.ice_sentry_across_npc_thaw(definition, False, gap=True, **options)
                        self.assertEqual(skipped, stepped)

    def test_projected_third_talon_intercepts_helix_after_sentry_retargets(self):
        definitions = tuple(self.definitions.values())
        for index, definition in enumerate(definitions):
            receiver = definitions[(index + 1) % len(definitions)]
            interceptor = definitions[(index + 2) % len(definitions)]
            for motion in ("retarget_intercept_far", "retarget_intercept"):
                for capacity in (40, 41, 200):
                    with self.subTest(
                        npc=definition.name, receiver=receiver.name, interceptor=interceptor.name,
                        motion=motion, capacity=capacity,
                    ):
                        options = {
                            "rifle_break": "helix", "splash_health": 150, "extra_npc": receiver,
                            "third_definition": interceptor, "splash_motion": motion,
                            "projected_capacity": capacity,
                        }
                        stepped = self.ice_sentry_across_npc_thaw(definition, False, gap=False, **options)
                        skipped = self.ice_sentry_across_npc_thaw(definition, False, gap=True, **options)
                        self.assertEqual(skipped, stepped)

    def test_projected_new_sentry_target_intercepts_helix(self):
        definitions = tuple(self.definitions.values())
        for index, definition in enumerate(definitions):
            receiver = definitions[(index + 1) % len(definitions)]
            for motion in ("retarget_intercept_far", "retarget_intercept"):
                for capacity in (30, 70, 200):
                    with self.subTest(
                        npc=definition.name, receiver=receiver.name, motion=motion, capacity=capacity,
                    ):
                        options = {
                            "rifle_break": "helix", "splash_health": 150, "extra_npc": receiver,
                            "splash_motion": motion, "projected_capacity": capacity,
                        }
                        stepped = self.ice_sentry_across_npc_thaw(definition, False, gap=False, **options)
                        skipped = self.ice_sentry_across_npc_thaw(definition, False, gap=True, **options)
                        self.assertEqual(skipped, stepped)

    def test_projected_new_sentry_target_absorbs_helix_wall_splash(self):
        definitions = tuple(self.definitions.values())
        for index, definition in enumerate(definitions):
            receiver = definitions[(index + 1) % len(definitions)]
            for capacity in (30, 200):
                for primary_health in (30, 150):
                    with self.subTest(
                        npc=definition.name, receiver=receiver.name, capacity=capacity,
                        primary_health=primary_health,
                    ):
                        options = {
                            "rifle_break": "helix", "splash_health": primary_health, "extra_npc": receiver,
                            "splash_motion": "retarget", "projected_capacity": capacity,
                        }
                        stepped = self.ice_sentry_across_npc_thaw(definition, False, gap=False, **options)
                        skipped = self.ice_sentry_across_npc_thaw(definition, False, gap=True, **options)
                        self.assertEqual(skipped, stepped)

    def test_frozen_talon_projected_cover_absorbs_helix_wall_splash(self):
        definitions = tuple(self.definitions.values())
        for index, definition in enumerate(definitions):
            receiver = definitions[(index + 1) % len(definitions)]
            for motion in ("enter", "retarget"):
                for capacity in (30, 200):
                    with self.subTest(
                        npc=definition.name, receiver=receiver.name, motion=motion, capacity=capacity,
                    ):
                        options = {
                            "rifle_break": "helix", "splash_health": 30, "extra_npc": receiver,
                            "splash_motion": motion, "projected_capacity": capacity, "projected_frozen": True,
                        }
                        stepped = self.ice_sentry_across_npc_thaw(definition, False, gap=False, **options)
                        skipped = self.ice_sentry_across_npc_thaw(definition, False, gap=True, **options)
                        self.assertEqual(skipped, stepped)

    def test_frozen_talon_projected_cover_absorbs_helix_after_body_intercept(self):
        definitions = tuple(self.definitions.values())
        for index, definition in enumerate(definitions):
            interceptor = definitions[(index + 1) % len(definitions)]
            for motion in ("intercept_near", "retarget_intercept"):
                for capacity in (30, 200):
                    for lethal in (False, True):
                        with self.subTest(
                            npc=definition.name, interceptor=interceptor.name, motion=motion,
                            capacity=capacity, lethal=lethal,
                        ):
                            options = {
                                "rifle_break": "helix", "splash_health": 30, "extra_npc": interceptor,
                                "splash_motion": motion, "projected_capacity": capacity,
                                "projected_frozen": True, "interceptor_lethal": lethal,
                            }
                            stepped = self.ice_sentry_across_npc_thaw(definition, False, gap=False, **options)
                            skipped = self.ice_sentry_across_npc_thaw(definition, False, gap=True, **options)
                            self.assertEqual(skipped, stepped)

    def test_frozen_talon_projected_cover_absorbs_helix_after_far_intercept(self):
        definitions = tuple(self.definitions.values())
        for index, definition in enumerate(definitions):
            interceptor = definitions[(index + 1) % len(definitions)]
            for capacity in (30, 200):
                for lethal in (False, True):
                    with self.subTest(
                        npc=definition.name, interceptor=interceptor.name, capacity=capacity, lethal=lethal,
                    ):
                        options = {
                            "rifle_break": "helix", "splash_health": 150, "extra_npc": interceptor,
                            "splash_motion": "retarget_intercept_far", "projected_capacity": capacity,
                            "projected_frozen": True, "interceptor_lethal": lethal,
                        }
                        stepped = self.ice_sentry_across_npc_thaw(definition, False, gap=False, **options)
                        skipped = self.ice_sentry_across_npc_thaw(definition, False, gap=True, **options)
                        self.assertEqual(skipped, stepped)

    def test_projected_third_talon_absorbs_helix_wall_splash_after_retarget(self):
        definitions = tuple(self.definitions.values())
        for index, definition in enumerate(definitions):
            receiver = definitions[(index + 1) % len(definitions)]
            shielded = definitions[(index + 2) % len(definitions)]
            for capacity in (30, 200):
                for primary_lethal, receiver_lethal in (
                    (False, False), (False, True), (True, False), (True, True),
                ):
                    with self.subTest(
                        npc=definition.name, receiver=receiver.name, shielded=shielded.name,
                        capacity=capacity, primary_lethal=primary_lethal, receiver_lethal=receiver_lethal,
                    ):
                        options = {
                            "rifle_break": "helix", "splash_health": 30 if primary_lethal else 150,
                            "extra_npc": receiver, "third_definition": shielded, "splash_motion": "retarget",
                            "projected_capacity": capacity, "receiver_lethal": receiver_lethal,
                        }
                        stepped = self.ice_sentry_across_npc_thaw(definition, False, gap=False, **options)
                        skipped = self.ice_sentry_across_npc_thaw(definition, False, gap=True, **options)
                        self.assertEqual(skipped, stepped)

    def test_projected_third_talon_absorbs_helix_splash_after_body_intercept(self):
        self.check_projected_third_talon_helix_body_intercept(primary_health=150)

    def test_projected_third_talon_absorbs_helix_splash_when_frozen_target_dies(self):
        self.check_projected_third_talon_helix_body_intercept(primary_health=30)

    def test_projected_third_talon_absorbs_helix_splash_after_far_intercept(self):
        for primary_health in (150, 30):
            self.check_projected_third_talon_helix_body_intercept(
                primary_health=primary_health, motions=("retarget_intercept_far",),
            )

    def test_projected_third_talon_absorbs_helix_splash_with_stable_far_lock(self):
        self.check_projected_third_talon_helix_body_intercept(primary_health=150, motions=("intercept",))

    def test_projected_frozen_talon_absorbs_only_beam_during_far_intercept(self):
        definitions = tuple(self.definitions.values())
        for index, definition in enumerate(definitions):
            interceptor = definitions[(index + 1) % len(definitions)]
            for capacity in (30, 200):
                for lethal in (False, True):
                    with self.subTest(
                        npc=definition.name, interceptor=interceptor.name, capacity=capacity, lethal=lethal,
                    ):
                        options = {
                            "rifle_break": "helix", "splash_health": 150, "extra_npc": interceptor,
                            "splash_motion": "intercept", "projected_capacity": capacity,
                            "projected_frozen": True, "interceptor_lethal": lethal,
                        }
                        stepped = self.ice_sentry_across_npc_thaw(definition, False, gap=False, **options)
                        skipped = self.ice_sentry_across_npc_thaw(definition, False, gap=True, **options)
                        self.assertEqual(skipped, stepped)

    def check_projected_third_talon_helix_body_intercept(
        self, *, primary_health, motions=("intercept_near", "retarget_intercept"),
    ):
        definitions = tuple(self.definitions.values())
        for index, definition in enumerate(definitions):
            interceptor = definitions[(index + 1) % len(definitions)]
            shielded = definitions[(index + 2) % len(definitions)]
            for motion in motions:
                for capacity in (30, 200):
                    for lethal in (False, True):
                        with self.subTest(
                            npc=definition.name, interceptor=interceptor.name, shielded=shielded.name,
                            motion=motion, capacity=capacity, lethal=lethal,
                        ):
                            options = {
                                "rifle_break": "helix", "splash_health": primary_health,
                                "extra_npc": interceptor,
                                "third_definition": shielded, "splash_motion": motion,
                                "projected_capacity": capacity, "third_splash_only": True,
                                "interceptor_lethal": lethal,
                            }
                            stepped = self.ice_sentry_across_npc_thaw(definition, False, gap=False, **options)
                            skipped = self.ice_sentry_across_npc_thaw(definition, False, gap=True, **options)
                            self.assertEqual(skipped, stepped)

    def test_projected_player_absorbs_helix_wall_splash_during_frozen_lock(self):
        self.check_projected_player_helix_support(SOLDIER, (30, 150))

    def test_projected_player_keeps_shield_pool_during_helix_wall_splash(self):
        self.check_projected_player_helix_support(transcendence.HERO, (150,))

    def test_projected_soldier_takes_wall_splash_at_thaw(self):
        self.check_projected_player_helix_support(SOLDIER, (30, 150), impact_offset=0)

    def test_projected_soldier_takes_wall_splash_after_thaw(self):
        self.check_projected_player_helix_support(SOLDIER, (30, 150), impact_offset=1)

    def test_projected_zenyatta_takes_wall_splash_at_thaw(self):
        self.check_projected_player_helix_support(transcendence.HERO, (30, 150), impact_offset=0)

    def test_projected_zenyatta_takes_wall_splash_after_thaw(self):
        self.check_projected_player_helix_support(transcendence.HERO, (30, 150), impact_offset=1)

    def test_projected_soldier_wall_splash_at_thaw_with_caster_in_range(self):
        self.check_projected_player_helix_support(
            SOLDIER, (30, 150), impact_offset=0, wall_caster_in_range=True,
        )

    def test_projected_soldier_wall_splash_after_thaw_with_caster_in_range(self):
        self.check_projected_player_helix_support(
            SOLDIER, (30, 150), impact_offset=1, wall_caster_in_range=True,
        )

    def test_projected_zenyatta_wall_splash_at_thaw_with_caster_in_range(self):
        self.check_projected_player_helix_support(
            transcendence.HERO, (30, 150), impact_offset=0, wall_caster_in_range=True,
        )

    def test_projected_zenyatta_wall_splash_after_thaw_with_caster_in_range(self):
        self.check_projected_player_helix_support(
            transcendence.HERO, (30, 150), impact_offset=1, wall_caster_in_range=True,
        )

    def test_projected_soldier_wall_splash_after_cover_handoff(self):
        self.check_projected_player_helix_support(
            SOLDIER, (30, 150), impact_offset=5, player_lock_on_occlusion=True,
        )

    def test_projected_zenyatta_wall_splash_after_cover_handoff(self):
        self.check_projected_player_helix_support(
            transcendence.HERO, (30, 150), impact_offset=5, player_lock_on_occlusion=True,
        )

    def test_projected_soldier_wall_splash_after_distance_handoff(self):
        self.check_projected_player_helix_support(
            SOLDIER, (30, 150), impact_offset=5, player_lock_change=True,
        )

    def test_projected_zenyatta_wall_splash_after_distance_handoff(self):
        self.check_projected_player_helix_support(
            transcendence.HERO, (30, 150), impact_offset=5, player_lock_change=True,
        )

    def test_projected_soldier_wall_splash_after_original_lock_returns(self):
        self.check_projected_player_helix_support(
            SOLDIER, (30, 150), impact_offset=8, player_lock_on_occlusion=True, wall_reacquire=True,
        )

    def test_projected_zenyatta_wall_splash_after_original_lock_returns(self):
        self.check_projected_player_helix_support(
            transcendence.HERO, (30, 150), impact_offset=8, player_lock_on_occlusion=True,
            wall_reacquire=True,
        )

    def test_assassin_moves_out_of_wall_splash_after_cover_handoff(self):
        assassin = next(npc for npc in self.definitions.values() if npc.name == "Assassin")
        for hero in (SOLDIER, transcendence.HERO):
            with self.subTest(hero=hero):
                self.check_projected_player_helix_support(
                    hero, (30, 150), impact_offset=8, player_lock_on_occlusion=True,
                    definitions=(assassin,), wall_primary_misses=True,
                )

    def test_projected_player_intercepts_helix_before_ice_support(self):
        self.check_projected_player_helix_support(
            SOLDIER, (30, 150), capacities=(40, 41, 200), motion="intercept_near",
        )

    def test_projected_player_intercept_preserves_native_shields(self):
        self.check_projected_player_helix_support(
            transcendence.HERO, (150,), capacities=(40, 41, 200), motion="intercept_near",
        )

    def test_projected_player_intercepts_helix_outside_sentry_range(self):
        self.check_projected_player_helix_support(
            SOLDIER, (150,), capacities=(40, 41, 200), motion="intercept",
        )

    def test_projected_player_far_intercept_preserves_native_shields(self):
        self.check_projected_player_helix_support(
            transcendence.HERO, (150,), capacities=(40, 41, 200), motion="intercept",
        )

    def test_projected_player_intercepts_helix_inside_sentry_range(self):
        self.check_projected_player_helix_support(
            SOLDIER, (150,), capacities=(40, 41, 200), motion="intercept", player_intercept_x=-6.5,
        )

    def test_projected_player_in_range_intercept_preserves_native_shields(self):
        self.check_projected_player_helix_support(
            transcendence.HERO, (150,), capacities=(40, 41, 200), motion="intercept", player_intercept_x=-6.5,
        )

    def test_projected_soldier_direct_helix_at_thaw(self):
        self.check_projected_player_direct_helix_at_thaw(SOLDIER, 0)

    def test_projected_soldier_direct_helix_after_thaw(self):
        self.check_projected_player_direct_helix_at_thaw(SOLDIER, 1)

    def test_projected_zenyatta_direct_helix_at_thaw(self):
        self.check_projected_player_direct_helix_at_thaw(transcendence.HERO, 0)

    def test_projected_zenyatta_direct_helix_after_thaw(self):
        self.check_projected_player_direct_helix_at_thaw(transcendence.HERO, 1)

    def check_projected_player_direct_helix_at_thaw(self, hero, offset):
        self.check_projected_player_helix_support(
            hero, (30, 150), capacities=(40, 41, 200), motion="intercept", player_intercept_x=-6.5,
            impact_offset=offset,
        )

    def test_projected_player_takes_sentry_lock_after_far_intercept(self):
        self.check_projected_player_helix_support(
            SOLDIER, (150,), capacities=(40, 41, 200), motion="intercept",
            player_lock_change=True,
        )

    def test_projected_player_sentry_lock_respects_native_shields_after_far_intercept(self):
        self.check_projected_player_helix_support(
            transcendence.HERO, (150,), capacities=(40, 41, 200), motion="intercept",
            player_lock_change=True,
        )

    def test_projected_player_takes_sentry_lock_after_frozen_target_dies(self):
        self.check_projected_player_helix_support(
            SOLDIER, (30,), capacities=(40, 41, 200), motion="intercept",
            player_intercept_x=-6.5, player_lock_on_death=True,
        )

    def test_projected_player_native_shields_after_frozen_target_dies(self):
        self.check_projected_player_helix_support(
            transcendence.HERO, (30,), capacities=(40, 41, 200), motion="intercept",
            player_intercept_x=-6.5, player_lock_on_death=True,
        )

    def test_projected_player_takes_sentry_lock_after_thawed_target_dies(self):
        self.check_projected_player_helix_support(
            SOLDIER, (47.5,), capacities=(40, 41, 200), motion="intercept",
            player_intercept_x=-6.5, player_lock_on_death=True,
        )

    def test_projected_player_native_shields_after_thawed_target_dies(self):
        self.check_projected_player_helix_support(
            transcendence.HERO, (47.5,), capacities=(40, 41, 200), motion="intercept",
            player_intercept_x=-6.5, player_lock_on_death=True,
        )

    def test_projected_player_takes_sentry_lock_when_thawed_talon_is_occluded(self):
        self.check_projected_player_helix_support(
            SOLDIER, (150,), capacities=(40, 41, 200), motion="intercept",
            player_intercept_x=-6.5, player_lock_on_occlusion=True,
        )

    def test_projected_player_native_shields_after_occlusion_retarget(self):
        self.check_projected_player_helix_support(
            transcendence.HERO, (150,), capacities=(40, 41, 200), motion="intercept",
            player_intercept_x=-6.5, player_lock_on_occlusion=True,
        )

    def test_projected_player_absorbs_splash_from_a_separate_talon_intercept(self):
        self.check_projected_player_splash_after_talon_intercept(SOLDIER)

    def test_projected_player_keeps_native_shields_after_a_talon_intercept(self):
        self.check_projected_player_splash_after_talon_intercept(transcendence.HERO)

    def test_projected_player_absorbs_splash_from_far_talon_intercept(self):
        self.check_projected_player_splash_after_talon_intercept(SOLDIER, motion="intercept")

    def test_projected_player_keeps_native_shields_after_far_talon_intercept(self):
        self.check_projected_player_splash_after_talon_intercept(transcendence.HERO, motion="intercept")

    def test_projected_in_range_player_absorbs_splash_from_far_talon_intercept(self):
        self.check_projected_player_splash_after_talon_intercept(
            SOLDIER, motion="intercept", collateral_in_range=True,
        )

    def test_projected_in_range_player_keeps_native_shields_after_far_talon_intercept(self):
        self.check_projected_player_splash_after_talon_intercept(
            transcendence.HERO, motion="intercept", collateral_in_range=True,
        )

    def test_projected_splash_player_takes_lock_after_both_talon_die(self):
        self.check_projected_player_splash_after_talon_intercept(
            SOLDIER, motion="intercept", collateral_in_range=True,
            primary_health=30, lethal_outcomes=(True,), lock_after_death=True,
        )

    def test_projected_splash_player_native_shields_after_both_talon_die(self):
        self.check_projected_player_splash_after_talon_intercept(
            transcendence.HERO, motion="intercept", collateral_in_range=True,
            primary_health=30, lethal_outcomes=(True,), lock_after_death=True,
        )

    def test_surviving_talon_interceptor_beats_farther_projected_splash_player(self):
        self.check_projected_player_splash_after_talon_intercept(
            SOLDIER, motion="intercept", collateral_in_range=True,
            primary_health=30, lethal_outcomes=(False,), lock_after_death=True,
        )

    def test_surviving_talon_interceptor_leaves_projected_player_native_shields_untouched(self):
        self.check_projected_player_splash_after_talon_intercept(
            transcendence.HERO, motion="intercept", collateral_in_range=True,
            primary_health=30, lethal_outcomes=(False,), lock_after_death=True,
        )

    def test_nearer_projected_splash_player_beats_surviving_talon_interceptor(self):
        self.check_projected_player_splash_after_talon_intercept(
            SOLDIER, motion="intercept", collateral_in_range=True,
            primary_health=30, lethal_outcomes=(False,), lock_after_death=True, nearer_player=True,
        )

    def test_nearer_projected_player_native_shields_with_surviving_talon(self):
        self.check_projected_player_splash_after_talon_intercept(
            transcendence.HERO, motion="intercept", collateral_in_range=True,
            primary_health=30, lethal_outcomes=(False,), lock_after_death=True, nearer_player=True,
        )

    def test_projected_splash_player_takes_lock_after_thawed_talon_dies(self):
        self.check_far_splash_after_thawed_talon_dies(interceptor_lethal=True)

    def test_surviving_interceptor_takes_lock_after_thawed_talon_dies(self):
        self.check_far_splash_after_thawed_talon_dies(interceptor_lethal=False)

    def test_nearer_projected_player_takes_lock_after_thawed_talon_dies(self):
        self.check_far_splash_after_thawed_talon_dies(interceptor_lethal=False, nearer_player=True)

    def check_far_splash_after_thawed_talon_dies(self, *, interceptor_lethal, nearer_player=False):
        for hero in (SOLDIER, transcendence.HERO):
            with self.subTest(hero=hero):
                self.check_projected_player_splash_after_talon_intercept(
                    hero, motion="intercept", collateral_in_range=True,
                    primary_health=47.5, lethal_outcomes=(interceptor_lethal,), lock_after_death=True,
                    nearer_player=nearer_player, expect_thawed_death=True,
                )

    def test_projected_splash_player_takes_lock_when_original_talon_is_occluded(self):
        self.check_far_splash_after_occlusion(interceptor_lethal=True)

    def test_surviving_interceptor_takes_lock_when_original_talon_is_occluded(self):
        self.check_far_splash_after_occlusion(interceptor_lethal=False)

    def test_nearer_projected_player_takes_lock_when_original_talon_is_occluded(self):
        self.check_far_splash_after_occlusion(interceptor_lethal=False, nearer_player=True)

    def check_far_splash_after_occlusion(self, *, interceptor_lethal, nearer_player=False):
        for hero in (SOLDIER, transcendence.HERO):
            with self.subTest(hero=hero):
                self.check_projected_player_splash_after_talon_intercept(
                    hero, motion="intercept", collateral_in_range=True,
                    lethal_outcomes=(interceptor_lethal,), lock_on_occlusion=True,
                    nearer_player=nearer_player,
                )

    def test_projected_splash_player_takes_lock_when_helix_kills_both_talon(self):
        self.check_far_splash_kills_original_talon(interceptor_lethal=True)

    def test_surviving_interceptor_takes_lock_when_helix_kills_original_talon(self):
        self.check_far_splash_kills_original_talon(interceptor_lethal=False)

    def test_nearer_projected_player_takes_lock_when_helix_kills_original_talon(self):
        self.check_far_splash_kills_original_talon(interceptor_lethal=False, nearer_player=True)

    def test_helix_kills_both_talon_at_thaw(self):
        self.check_far_splash_kills_original_talon(interceptor_lethal=True, impact_offset=0)

    def test_helix_kills_both_talon_after_thaw(self):
        self.check_far_splash_kills_original_talon(interceptor_lethal=True, impact_offset=1)

    def test_surviving_interceptor_takes_lock_after_lethal_splash_at_thaw(self):
        self.check_far_splash_kills_original_talon(interceptor_lethal=False, impact_offset=0)

    def test_surviving_interceptor_takes_lock_after_lethal_splash_after_thaw(self):
        self.check_far_splash_kills_original_talon(interceptor_lethal=False, impact_offset=1)

    def test_nearer_player_takes_lock_after_lethal_splash_at_thaw(self):
        self.check_far_splash_kills_original_talon(
            interceptor_lethal=False, nearer_player=True, impact_offset=0,
        )

    def test_nearer_player_takes_lock_after_lethal_splash_after_thaw(self):
        self.check_far_splash_kills_original_talon(
            interceptor_lethal=False, nearer_player=True, impact_offset=1,
        )

    def check_far_splash_kills_original_talon(
        self, *, interceptor_lethal, nearer_player=False, impact_offset=None,
    ):
        for hero in (SOLDIER, transcendence.HERO):
            with self.subTest(hero=hero):
                self.check_projected_player_splash_after_talon_intercept(
                    hero, motion="intercept", collateral_in_range=True,
                    primary_health=30, lethal_outcomes=(interceptor_lethal,),
                    nearer_player=nearer_player, primary_splash_death=True, impact_offset=impact_offset,
                )

    def test_nonlethal_talon_splash_keeps_sentry_lock_with_projected_player(self):
        self.check_projected_player_splash_after_talon_intercept(
            SOLDIER, motion="intercept", collateral_in_range=True, primary_in_splash=True,
        )

    def test_nonlethal_talon_splash_keeps_sentry_lock_with_projected_native_shields(self):
        self.check_projected_player_splash_after_talon_intercept(
            transcendence.HERO, motion="intercept", collateral_in_range=True, primary_in_splash=True,
        )

    def test_nonlethal_splash_then_beam_death_with_lethal_interception(self):
        self.check_far_nonlethal_splash_before_beam_death(interceptor_lethal=True)

    def test_nonlethal_splash_then_beam_death_with_nearer_interceptor(self):
        self.check_far_nonlethal_splash_before_beam_death(interceptor_lethal=False)

    def test_nonlethal_splash_then_beam_death_with_nearer_player(self):
        self.check_far_nonlethal_splash_before_beam_death(interceptor_lethal=False, nearer_player=True)

    def check_far_nonlethal_splash_before_beam_death(self, *, interceptor_lethal, nearer_player=False):
        for hero in (SOLDIER, transcendence.HERO):
            with self.subTest(hero=hero):
                self.check_projected_player_splash_after_talon_intercept(
                    hero, motion="intercept", collateral_in_range=True,
                    primary_health=95, lethal_outcomes=(interceptor_lethal,),
                    nearer_player=nearer_player, primary_in_splash=True, lock_after_death=True,
                )

    def test_nonlethal_splash_from_talon_intercept_at_thaw(self):
        self.check_nonlethal_talon_splash_at_thaw(SOLDIER, 0)

    def test_nonlethal_splash_from_talon_intercept_after_thaw(self):
        self.check_nonlethal_talon_splash_at_thaw(SOLDIER, 1)

    def test_native_shields_after_talon_intercept_at_thaw(self):
        self.check_nonlethal_talon_splash_at_thaw(transcendence.HERO, 0)

    def test_native_shields_after_talon_intercept_after_thaw(self):
        self.check_nonlethal_talon_splash_at_thaw(transcendence.HERO, 1)

    def check_nonlethal_talon_splash_at_thaw(self, hero, offset):
        self.check_projected_player_splash_after_talon_intercept(
            hero, motion="intercept", collateral_in_range=True,
            primary_in_splash=True, impact_offset=offset,
        )

    def check_projected_player_splash_after_talon_intercept(
        self, hero, *, motion="intercept_near", collateral_in_range=False,
        primary_health=150, lethal_outcomes=(False, True), lock_after_death=False, nearer_player=False,
        expect_thawed_death=False, lock_on_occlusion=False, primary_splash_death=False,
        primary_in_splash=False, impact_offset=None,
    ):
        definitions = tuple(self.definitions.values())
        for index, definition in enumerate(definitions):
            interceptor = definitions[(index + 1) % len(definitions)]
            for capacity in (30, 200):
                for lethal in lethal_outcomes:
                    with self.subTest(npc=definition.name, capacity=capacity, lethal=lethal):
                        player_wins = lethal or nearer_player
                        options = {
                            "rifle_break": "helix", "splash_health": primary_health, "splash_motion": motion,
                            "second_splash": True, "extra_npc": interceptor, "interceptor_lethal": lethal,
                            "projected_capacity": capacity, "projected_player_hero": hero,
                            "collateral_in_range": collateral_in_range,
                            "player_lock_on_death": lock_after_death and player_wins,
                            "interceptor_lock_on_death": lock_after_death and not player_wins,
                            "player_lock_on_occlusion": lock_on_occlusion and player_wins,
                            "interceptor_lock_on_occlusion": lock_on_occlusion and not player_wins,
                            "nearer_player": nearer_player, "expect_thawed_death": expect_thawed_death,
                            "primary_splash_death": primary_splash_death,
                            "primary_in_splash": primary_in_splash, "impact_offset": impact_offset,
                        }
                        stepped = self.ice_sentry_across_npc_thaw(definition, False, gap=False, **options)
                        skipped = self.ice_sentry_across_npc_thaw(definition, False, gap=True, **options)
                        self.assertEqual(skipped, stepped)

    def check_projected_player_helix_support(
        self, player_hero, primary_healths, *, capacities=(30, 200), motion=None, player_intercept_x=None,
        player_lock_change=False, player_lock_on_death=False, player_lock_on_occlusion=False,
        impact_offset=None, wall_caster_in_range=False, definitions=None, wall_primary_misses=False,
        wall_reacquire=False,
    ):
        for definition in self.definitions.values() if definitions is None else definitions:
            for capacity in capacities:
                for primary_health in primary_healths:
                    with self.subTest(npc=definition.name, capacity=capacity, primary_health=primary_health):
                        options = {
                            "rifle_break": "helix", "splash_health": primary_health,
                            "second_splash": True, "projected_capacity": capacity,
                            "projected_player_hero": player_hero, "splash_motion": motion,
                            "player_intercept_x": player_intercept_x,
                            "player_lock_change": player_lock_change,
                            "player_lock_on_death": player_lock_on_death,
                            "player_lock_on_occlusion": player_lock_on_occlusion,
                            "impact_offset": impact_offset,
                            "wall_caster_in_range": wall_caster_in_range,
                            "wall_primary_misses": wall_primary_misses, "wall_reacquire": wall_reacquire,
                            "primary_in_splash": impact_offset is not None and motion == "intercept",
                            "primary_splash_death": (
                                impact_offset is not None and motion == "intercept" and primary_health == 30
                            ),
                        }
                        stepped = self.ice_sentry_across_npc_thaw(definition, False, gap=False, **options)
                        skipped = self.ice_sentry_across_npc_thaw(definition, False, gap=True, **options)
                        self.assertEqual(skipped, stepped)

    def ice_sentry_across_npc_thaw(
        self, definition, expiry_first, *, gap, checkpoint_offset=None, rifle_break=False, impact_offset=None,
        splash_health=None, second_splash=False, player_lethal=False, extra_npc=None, splash_motion=None,
        interceptor_lethal=False, receiver_lethal=False, receiver_beam_lethal=False,
        third_definition=None, third_lethal=False, pharah_rocket=False, reflected_pharah=False,
        projected_capacity=None, projected_frozen=False, third_splash_only=False,
        projected_player_hero=SOLDIER, player_intercept_x=None, player_lock_change=False,
        player_lock_on_death=False, player_lock_on_occlusion=False,
        collateral_in_range=False, interceptor_lock_on_death=False, nearer_player=False,
        expect_thawed_death=False, interceptor_lock_on_occlusion=False, primary_splash_death=False,
        primary_in_splash=False, wall_caster_in_range=False, wall_primary_misses=False,
        wall_reacquire=False,
    ):
        retarget_intercept = splash_motion == "retarget_intercept"
        near_third = retarget_intercept and third_definition is not None and not third_splash_only
        far_retarget = splash_motion == "retarget_intercept_far"
        far_loses_receiver = far_retarget and (interceptor_lethal or receiver_lethal)
        near_intercept = retarget_intercept or splash_motion == "intercept_near"
        intercepts = near_intercept or far_retarget or splash_motion == "intercept"
        retargets = retarget_intercept or far_retarget or splash_motion == "retarget"
        projected_lock = (
            retargets and projected_capacity is not None and third_definition is None and not projected_frozen
        )
        projected_beam = projected_lock or projected_frozen
        projected_beam_only = projected_frozen and splash_motion == "intercept"
        third_intercepts = intercepts and third_definition is not None and not third_splash_only
        player_projection = second_splash and projected_capacity is not None
        separate_player_splash = player_projection and extra_npc is not None
        projected_direct = (
            intercepts and projected_capacity is not None and not projected_frozen
            and not third_splash_only and not separate_player_splash
        )
        projected_third_splash = (
            third_definition is not None and projected_capacity is not None and not third_intercepts
        )
        projected_player = player_projection and not separate_player_splash
        wall_splash_at_thaw = projected_player and not intercepts and impact_offset is not None
        player_can_take_lock = player_lock_change or player_lock_on_death or player_lock_on_occlusion
        beam_kills_frozen = player_lock_on_death or interceptor_lock_on_death
        nonlethal_primary_splash = primary_in_splash and not primary_splash_death
        primary_in_splash = primary_in_splash or primary_splash_death
        cover_changes_lock = player_lock_on_occlusion or interceptor_lock_on_occlusion
        wall_distance_handoff = wall_splash_at_thaw and player_lock_change
        wall_handoff = wall_splash_at_thaw and (player_lock_on_occlusion or player_lock_change)
        interceptor_can_take_lock = interceptor_lock_on_death or interceptor_lock_on_occlusion
        if primary_splash_death:
            player_can_take_lock = projected_player or interceptor_lethal or nearer_player
            interceptor_can_take_lock = not player_can_take_lock
        observe_projected_energy = projected_beam or projected_third_splash or player_projection
        run, sym, bot, _ = self.create(definition, distance=28)
        fight = run.match.combat
        mei = run.players[1]
        self.assertTrue(run.match.switch_hero(mei, heroes()[cryo.HERO]))
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        mei.team = sym.team if splash_health is None else 1 - sym.team
        mei.mover.position[:] = (7, 1, 28)
        sym.mover.position[:] = (5.5, 1, 28)
        bot.position = (3.3, 1, 28)
        for _ in range(25):
            run.step({})
        wall_aim = aim(fight._eye(mei), (1, 1, 28))
        for button in (16, 0, 1, 0):
            run.step({mei: (*wall_aim, button)})
        pieces = tuple(fight.ice_wall.pieces.values())
        self.assertEqual(len(pieces), 5)
        center = min(pieces, key=lambda piece: abs(piece.feet[2] - 28))
        self.assertAlmostEqual(center.feet[0], 1, places=2)
        clearance = .000005 if impact_offset is not None else .00002
        bot.position = (center.feet[0] + center.depth + definition.mover.stand_radius + clearance, 1, 28)
        sentry_aim_point = (center.feet[0], 2, 28)
        companion = None
        if splash_health is not None:
            fixture = barrier_tests.PhotonBarrierTests()
            sentry_z = 28.7 if near_intercept and not retargets else 27.4
            bot_z = 28.95 if near_intercept and not retargets else 29.35
            bot_x = -.25 if near_intercept and not retargets else -1.0
            if retarget_intercept and not near_third:
                bot_x, bot_z = (-.25, 28.95) if extra_npc.name == "Heavy Assault" else (-1.35, 29.65)
            if retargets and definition.name == "Heavy Assault":
                bot_x, bot_z = -1.9, 30.0
            if near_third and definition.name in {"Sniper", "Assassin"}:
                bot_x, bot_z = -.25, 28.95
            if near_third and definition.name == "Heavy Assault":
                bot_x, bot_z = -1.35, 29.3
            if far_retarget:
                bot_x, bot_z = -6.25, 29.35
            if player_lock_change and not wall_distance_handoff:
                bot_z = 32.5 if definition.name == "Heavy Assault" else 32.0
            mei_x = -8.75 if far_retarget else -5
            if primary_in_splash:
                bot_x, mei_x = -4.5, -5.8
            fixture.move(sym, (-4, 1, sentry_z))
            fixture.move(mei, (24, 1, 28))
            mei = fixture.add_target(run, 4, (mei_x, 1, bot_z))
            self.assertTrue(run.match.switch_hero(mei, heroes()[cryo.HERO]))
            mei.team = sym.team
            fixture.move(mei, (mei_x, 1, bot_z))
            bot.position = (bot_x, 1, bot_z)
            lower_sentry = near_third and "Heavy Assault" in {definition.name, third_definition.name}
            sentry_y = 4.55 if lower_sentry or wall_distance_handoff else 5.3
            sentry_aim_point = (center.feet[0], sentry_y, sentry_z)
            if second_splash:
                position = (-10, 1, 24.5) if player_lock_change else (-2.45, 1, 26.75)
                if separate_player_splash and not near_intercept:
                    position = (-8.1 if collateral_in_range else -9.7, 1, 26.75)
                    if nearer_player:
                        position = (-5.4, 1, 26.75)
                if projected_player and (primary_in_splash or wall_splash_at_thaw):
                    position = (-11.5, 1, 26.75)
                companion = fixture.add_target(run, 5, position)
                if projected_player_hero != SOLDIER:
                    self.assertTrue(run.match.switch_hero(companion, heroes()[projected_player_hero]))
                    fixture.move(companion, position)
                companion.team = bot.team
                if separate_player_splash:
                    splash_player = companion
            if extra_npc is not None and not far_retarget:
                # The taller Heavy must stay farther from the beam while
                # its capsule still reaches the edge of the splash.
                position = (-1.45, 1, 30.7) if extra_npc.name == "Heavy Assault" else (-2.45, 1, 26.75)
                if retarget_intercept and not near_third:
                    start_x = {"Assassin": -5.4, "Heavy Assault": -2.4}.get(extra_npc.name, -3.15)
                    position = (start_x, 1, 27.1)
                    destination = (-1.15, 1, 28)
                elif near_intercept and not near_third:
                    position = (-1.8, 1, 28 - extra_npc.mover.stand_radius - .3)
                    destination = (-1.8, 1, 28)
                elif intercepts and not near_third:
                    position = (-6.5, 1, 28 + extra_npc.mover.stand_radius + .3)
                    destination = (-6.5, 1, 28)
                elif retargets:
                    start_x = {"Assassin": -5.6, "Heavy Assault": -3.45}.get(extra_npc.name, -3.25)
                    if pharah_rocket and extra_npc.name == "Assassin":
                        start_x = -7.7 if reflected_pharah else -8.25
                    if reflected_pharah and extra_npc.name == "Heavy Assault":
                        start_x = -3.1
                    if near_third:
                        start_x = {
                            "Sniper": -2.7, "Assassin": -2.55, "Heavy Assault": -2.4, "Trooper": -2.5,
                        }.get(extra_npc.name, -3.0)
                        if pharah_rocket and extra_npc.name == "Enforcer":
                            start_x = -2.55
                    if receiver_beam_lethal and extra_npc.name in {"Sniper", "Heavy Assault"}:
                        start_x += .25
                    position = (start_x, 1, 26.75)
                    destination = (-.25, 1, 26.75)
                elif splash_motion is not None:
                    outside = (position[0] - .8, position[1], position[2])
                    if splash_motion == "enter":
                        destination, position = position, outside
                    else:
                        destination = outside
                companion = npc.spawn(run.match, extra_npc, position, team=bot.team)
        soldier = None
        if rifle_break in {"present", "helix"}:
            soldier = self.ice_support_rifle_enemy(run, sym, distance=18)
            if splash_health is not None:
                soldier.team = sym.team
            rifle_aim = aim(fight._eye(soldier), (center.feet[0], 2, 28))
            initial_ammo = soldier.body_script.component.instances[11].vars[53].value()

        def setup_step(inputs):
            if soldier is not None and center.health > 20:
                inputs[soldier] = (*rifle_aim, 1)
            run.step(inputs)

        # Align the natural expiry with the thaw using the usual launch,
        # spray buildup and freeze duration from the no-wall fixture.
        spray_warmup = 95
        freeze_ticks = math.ceil(combat.mei_frost.FREEZE_MS / round(combat.TICK * 1000))
        lead = 40 + spray_warmup + freeze_ticks
        late_expiry = checkpoint_offset is not None or rifle_break == "helix"
        launch_offset = -12 if late_expiry else (5 if expiry_first else -5)
        launch = center.expires - lead + launch_offset
        while run.frame < launch:
            setup_step({})
        direction = aim(fight._eye(sym), sentry_aim_point)
        for _ in range(40):
            setup_step({sym: (*direction, 8)})
        spray = aim(fight._eye(mei), (bot.position[0], 1.9, bot.position[2]))
        for _ in range(135):
            setup_step({mei: (*spray, 1)})
            if fight.is_stunned(bot):
                break
        for _ in range(8):
            run.step({})
        if far_retarget or player_lock_change:
            for _ in range(18):
                if not fight.rockets:
                    break
                run.step({})
            self.assertFalse(fight.rockets)
        self.assertTrue(fight.is_stunned(bot))
        [sentry] = fight.sentries.sentries
        self.assertEqual(sentry.support_pillar, center.entity)
        self.assertEqual(sentry.target, bot.entity)
        component, aura = sentry.slow
        third = None
        if far_retarget:
            companion, destination = self.far_helix_retarget_route(run, bot, sentry, extra_npc)
        if third_definition is not None:
            if projected_third_splash:
                if intercepts and not near_intercept:
                    third_x, third_start_z = -7.6, 30.25
                else:
                    third_x = -1.8
                    third_start_z = 31.1 if third_definition.name == "Heavy Assault" else 30.35
            elif far_retarget:
                # Let the second NPC cross the beam threshold while it
                # stays outside the rocket ray.
                destination = (destination[0], 1, 28 + extra_npc.mover.stand_radius + .06)
                third_x = -7.7
                third_start_z = 28 - third_definition.mover.stand_radius - .16
            else:
                third_x = -1.9 if third_definition.name == "Heavy Assault" else -1.8
                route_lead = {"Assassin": 2.8, "Heavy Assault": .62}.get(third_definition.name, .85)
                if pharah_rocket:
                    third_x += .52 if third_definition.name == "Heavy Assault" else .6
                    route_lead += {"Assassin": 1.5, "Heavy Assault": .37}.get(third_definition.name, .5)
                    if reflected_pharah and third_definition.name == "Heavy Assault":
                        route_lead -= .12
                third_start_z = 28 + third_definition.mover.stand_radius + route_lead
            third_start = (third_x, 1, third_start_z)
            third = npc.spawn(run.match, third_definition, third_start, team=bot.team)
            third_pool = health(run, third)
        if soldier is not None:
            self.assertEqual(center.health, 20)
            self.assertEqual(soldier.body_script.component.instances[11].vars[53].value(), initial_ammo - 19)
            self.assertEqual(soldier.stats.value(stats.HERO_DAMAGE), 0)
        rocket = None
        projected_bubble = None
        if projected_capacity is not None:
            projected_recipient = third if third is not None else companion
            if separate_player_splash:
                projected_recipient = splash_player
            caster_position = (-9.5, 1, 26.75)
            if wall_splash_at_thaw:
                caster_position = (-7.5 if wall_caster_in_range else -12.5, 1, 29.55)
                fixture.move(companion, (-2.45, 1, 29.55))
            if projected_player and intercepts:
                player_x = player_intercept_x
                if player_x is None:
                    player_x = -1.8 if near_intercept else -13.5
                fixture.move(companion, (player_x, 1, 28))
            if projected_frozen:
                projected_recipient = bot
                caster_position = (-10.5, 1, 33.5)
                frost_at_cast = fight.frost(bot)
            elif projected_third_splash:
                caster_position = (-10.5, 1, 33.5)
            elif third is not None or projected_lock:
                caster_position = (-10.5, 1, 22.5)
            elif splash_motion == "intercept":
                caster_position = (-10.5, 1, 22.5 if projected_player else 24.5)
                if separate_player_splash:
                    caster_position = (-13, 1, 24)
            caster = fixture.add_target(run, 6 if player_projection else 5, caster_position)
            self.assertTrue(run.match.switch_hero(caster, heroes()[zarya_barriers.HERO]))
            caster.team = projected_recipient.team
            fixture.move(caster, caster_position)
            recipient = next(target for target in fight._targets() if target.owner is projected_recipient)
            if separate_player_splash and not near_intercept:
                beam = fight.sentries._beam(sentry)
                collateral_reach = expr.to_float(beam.find_var(graph.INSTANCE, 1648).value())
                distance = combat.segment_distance(sentry.bot.position, *recipient.segment())
                self.assertEqual(distance - recipient.radius <= collateral_reach, collateral_in_range)
                if collateral_in_range:
                    self.assertTrue(fight.visible_target(sentry.bot.position, recipient))
            if player_lock_change:
                origin = sentry.bot.position
                frozen_target = next(target for target in fight._targets() if target.owner is bot)
                frozen_distance = combat.segment_distance(origin, *frozen_target.segment())
                frozen_distance -= frozen_target.radius
                player_z = 29 if wall_distance_handoff else 28
                separation = .025 if wall_distance_handoff else .15
                player_distance = frozen_distance + recipient.radius + separation
                lateral = combat.segment_distance(origin, *recipient.segment((origin[0], 1, player_z)))
                player_x = origin[0] - math.sqrt(player_distance**2 - lateral**2)
                fixture.move(companion, (player_x, 1, player_z))
                recipient = replace(recipient, feet=tuple(companion.mover.position))
            if player_intercept_x is not None or player_lock_change:
                beam = fight.sentries._beam(sentry)
                reach = expr.to_float(beam.find_var(graph.INSTANCE, 1648).value())
                player_distance = combat.segment_distance(sentry.bot.position, *recipient.segment())
                self.assertLessEqual(player_distance - recipient.radius, reach)
                self.assertTrue(fight.visible_target(sentry.bot.position, recipient))
            x, y, z = recipient.feet
            bubble_aim = aim(fight._eye(caster), (x, y + recipient.eye, z))
            run.step({caster: (*bubble_aim, 16)})
            source, _, projected_bubble = fight.zarya_barriers.contact(projected_recipient)
            self.assertIs(source, caster)
            self.assertEqual(projected_bubble.remaining, 200)
            projected_bubble.remaining = projected_capacity
            projected_pool = health(run, projected_recipient)
            if wall_caster_in_range:
                caster_target = next(target for target in fight._targets() if target.owner is caster)
                pivot = sentry.bot.position
                caster_distance = (
                    combat.segment_distance(pivot, *caster_target.segment()) - caster_target.radius
                )
                beam = fight.sentries._beam(sentry)
                reach = expr.to_float(beam.find_var(graph.INSTANCE, 1648).value())
                self.assertLessEqual(caster_distance, reach)
                self.assertTrue(fight.visible_target(pivot, caster_target))
                caster_pool = health(run, caster)
                caster_pools_before = caster_pool.now.copy()
                self.assertIsNone(caster_pool.last_damage_at)
            if separate_player_splash:
                projected_pool.now[combat.HEALTH_POOL] = 50 if primary_in_splash else 30
            if projected_frozen:
                self.assertEqual(fight.frost(bot), frost_at_cast)
        tick_ms = round(combat.TICK * 1000)
        thaw = (fight.frost_hits[bot.entity][2] + tick_ms - 1) // tick_ms
        if rifle_break == "helix":
            if pharah_rocket and not reflected_pharah:
                self.assertTrue(run.match.switch_hero(soldier, heroes()[barrier_tests.PHARAH]))
                fixture.move(soldier, (-18, 1, 28))
                rifle_aim = aim(fight._eye(soldier), (center.feet[0], 2, 28))
            if impact_offset is not None:
                # The moving interceptor meets the 50 m/s rocket before the wall.
                flight_ticks = 14 if primary_in_splash else 24
                fire_at = thaw + impact_offset - flight_ticks
                while run.frame < fire_at - 1:
                    run.step({})
            if reflected_pharah:
                rocket = self.launch_reflected_pharah_rocket(run, soldier, center, fixture)
            else:
                run.step({soldier: (*rifle_aim, 1 if pharah_rocket else 2)})
                self.assertEqual(len(fight.rockets), 1, [shot.definition for shot in fight.rockets])
                [rocket] = fight.rockets
            self.assertIs(rocket.shooter, soldier)
            if pharah_rocket:
                self.assertIsNone(rocket.definition)
                self.assertTrue(rocket.implicit_absorbable)
            run.step({})
            self.assertEqual(rocket.last_tick, run.frame)
            reach = rocket.left * rocket.speed
            contact = fight.ice_wall.cast(rocket.position, rocket.direction, reach)
            self.assertIsNotNone(contact, (rocket.position, rocket.direction, reach))
            distance, struck, _ = contact
            self.assertIs(struck, center)
        start = run.frame
        self.assertEqual(center.expires < thaw, expiry_first, (center.expires, thaw))
        removal = center.expires
        last_beam_tick = removal - 1
        if rocket is not None and not intercepts:
            removal = start + math.ceil(distance / (rocket.speed * combat.TICK))
            if impact_offset is None:
                self.assertLess(removal, thaw)
            else:
                self.assertEqual(removal, thaw + impact_offset)
                self.assertLess(removal, center.expires)
            last_beam_tick = removal - 1
        if checkpoint_offset is not None:
            removal = thaw + checkpoint_offset
            self.assertLess(removal, center.expires)
            self.assertGreater(removal - start, combat.COMBAT_CATCH_UP)
            last_beam_tick = removal  # Input is admitted after this world tick.
        first_clear_tick = removal + int(rocket is not None or checkpoint_offset is not None)
        route_opens = thaw if intercepts or wall_splash_at_thaw else max(thaw, first_clear_tick)
        end = route_opens + 10
        if intercepts or retargets:
            end = max(end, center.expires + 2)
        if impact_offset is not None:
            end = max(end, start + 80)
        self.assertGreater(end - start, combat.COMBAT_CATCH_UP)
        self.assertGreater(removal, start)
        pool = health(run, bot)
        if projected_frozen:
            self.assertEqual(fight.frost(bot), frost_at_cast)
            self.assertEqual(sentry.target, bot.entity)
            self.assertIsNone(sentry.slow)
            self.assertNotIn(aura.id, component.instances)
        beam_damage = (last_beam_tick - start) * 40 * combat.TICK
        splash_damage = 0
        companion_damage = 0
        companion_lethal = player_lethal or interceptor_lethal or receiver_lethal or receiver_beam_lethal or (
            extra_npc is not None and splash_health == 30 and splash_motion is None
        )
        splash_kills = False
        if splash_health is not None:
            self.assertEqual(pool.total, pool.now[combat.HEALTH_POOL])
            pool.now[combat.HEALTH_POOL] = splash_health
            impact = combat._along(rocket.position, rocket.direction, distance)
            origin = combat._along(impact, rocket.direction, -.0001)
            splash_targets = fight._splash_targets(rocket, impact, origin)
            offered_damage = {victim.owner: amount for victim, amount in splash_targets}
            expected_victims = [bot]
            if companion is not None and splash_motion != "enter" and not intercepts:
                expected_victims.append(companion)
            if not (near_intercept or retargets or player_lock_change or primary_in_splash):
                self.assertCountEqual(offered_damage, expected_victims)
            if companion is not None:
                companion_pool = health(run, companion)
                companion_full = companion_pool.total
                if player_lethal:
                    self.assertEqual(companion_pool.total, companion_pool.now[combat.HEALTH_POOL])
                if player_lethal or projected_player:
                    companion_pool.now[combat.HEALTH_POOL] = 30
                if projected_player and projected_player_hero == transcendence.HERO:
                    self.assertGreater(companion_pool.now[combat.SHIELDS_POOL], 0)
                if extra_npc is not None and companion_lethal:
                    receiver_health = 1 if receiver_beam_lethal else 30
                    for kind in companion_pool.now:
                        companion_pool.now[kind] = receiver_health if kind == combat.HEALTH_POOL else 0
                if extra_npc is not None:
                    self.assertFalse(fight.is_stunned(companion))
                    companion_position = companion.position
                    companion_script = companion.body_script
                    companion_pools_before = tuple(companion_pool.now.values())
                companion_before = companion_pool.total
                expected_companion_pool = copy(companion_pool)
                expected_companion_pool.now = companion_pool.now.copy()
                expected_companion_pool.changed = set()
                if splash_motion is None and not projected_player:
                    companion_damage = expected_companion_pool.damage(offered_damage[companion])
                    self.assertEqual(expected_companion_pool.now[combat.HEALTH_POOL] <= 0, companion_lethal)
                    self.assertGreater(companion_damage, 0)
            if not (intercepts or retargets or projected_frozen or wall_splash_at_thaw):
                offered = offered_damage[bot]
                splash_damage = min(offered, splash_health - beam_damage)
                splash_kills = splash_health == 30
                self.assertEqual(offered >= splash_health - beam_damage, splash_kills)
                self.assertGreater(splash_damage, 0)
        if projected_bubble is not None:
            projected_pools_before = projected_pool.now.copy()
            projected_damage_time = projected_pool.last_damage_at
            initial_beam_capacity = projected_bubble.remaining
            if separate_player_splash and player_can_take_lock:
                expected_splash_player_pool = copy(projected_pool)
                expected_splash_player_pool.now = projected_pool.now.copy()
                expected_splash_player_pool.changed = set()
        before = pool.total
        wall_health_before = [piece.health for piece in pieces]
        charge_before = charge(sym)
        damage_before = sym.stats.value(stats.HERO_DAMAGE)
        mei_damage_before = mei.stats.value(stats.HERO_DAMAGE)
        if soldier is None and rifle_break:
            center.health = 1
        bot.route = ((0.0, 1.0, 28.0),)
        if splash_health is not None:
            bot.route = ((bot.position[0], 1.0, 36 if player_lock_change else 33.35),)
        if nonlethal_primary_splash and not beam_kills_frozen:
            bot.route = ((bot.position[0], 1.0, 30.1),)
        if wall_reacquire:
            bot.route = ((bot.position[0], 1.0, 29.65),)
        bot.wait = 0.0
        if cover_changes_lock:
            frozen_target = next(target for target in fight._targets() if target.owner is bot)
            cover_lead = .16 if wall_handoff else .42
            hidden_feet = (bot.position[0], bot.position[1], bot.position[2] + cover_lead)
            hidden_target = replace(frozen_target, feet=hidden_feet)
            origin = sentry.bot.position
            plane_x = (origin[0] + bot.position[0]) * .5
            clear_direction, _ = combat.capsule_ray(origin, frozen_target)
            hidden_direction, _ = combat.capsule_ray(origin, hidden_target)
            clear_z = origin[2] + (plane_x - origin[0]) * clear_direction[2] / clear_direction[0]
            hidden_z = origin[2] + (plane_x - origin[0]) * hidden_direction[2] / hidden_direction[0]
            cover_z = (clear_z + hidden_z) * .5
            cover_end = 40
            if wall_reacquire:
                cover_z = clear_z + (hidden_z - clear_z) * .05
                cover_end = clear_z + (hidden_z - clear_z) * 1.15
            cover_bottom = 3.25 if wall_handoff else 2
            run.match.collision_world = World([
                (-25, 1, 20, -25, 1, 40, 25, 1, 40),
                (-25, 1, 20, 25, 1, 40, 25, 1, 20),
                (plane_x, cover_bottom, cover_z, plane_x, 7, cover_z, plane_x, 7, cover_end),
                (plane_x, cover_bottom, cover_z, plane_x, 7, cover_end, plane_x, cover_bottom, cover_end),
            ])
            self.assertTrue(fight.visible_target(origin, frozen_target))
            self.assertFalse(fight.visible_target(origin, hidden_target))
            self.assertTrue(fight.visible_target(origin, recipient))
        if splash_motion is not None and not projected_player:
            companion.route = (destination,)
            companion.wait = 0.0
        if third is not None:
            if third_lethal:
                for kind in third_pool.now:
                    third_pool.now[kind] = 30 if kind == combat.HEALTH_POOL else 0
            third_before = third_pool.total
            destination_z = third_start_z - .25 if projected_third_splash else 28
            third.route = ((third_x, 1, destination_z),)
            third.wait = 0.0
            third_script = third.body_script
        initial_pose = bot.position
        movement_ticks = []
        damage_ticks = []
        route_tick = type(bot)._tick
        update_sentries = fight.sentries.update
        explode = fight._explode
        body_impact = fight._projectile_body_impact
        intercept_ticks = []
        interceptor_lock_ticks = []
        retarget_ticks = []
        reacquired_ticks = []
        third_beam_ticks = []
        third_shield_ticks = []
        receiver_death_pose = None
        third_impact_pose = None
        companion_beam_damage = 0.0
        splash_player_beam_damage = 0.0
        third_beam_damage = 0.0
        companion_impact_pose = None
        beam_shield_ticks = []
        lock_slow_ticks = []
        beam_tick_damage = 40 * combat.TICK
        capacity_before_rocket = projected_capacity
        capacity_after_blast = None
        energy_after_impact = None
        blast_tick = None
        final_frozen_beam_damage = None
        frozen_death_pose = None
        first_occluded_tick = None
        first_nearer_tick = None
        wall_reacquired_tick = None
        primary_impact_pose = None

        def observe_route(actor, tick, *args):
            before_pose = actor.position
            route_tick(actor, tick, *args)
            if (actor is bot and impact_offset is not None and tick <= removal
                    and not (primary_in_splash or wall_splash_at_thaw)):
                self.assertEqual(actor.position, initial_pose)
            if actor is bot and math.dist(before_pose, actor.position) > .0001:
                movement_ticks.append(tick)

        def observe_sentry(*args, **kwargs):
            nonlocal companion_beam_damage, receiver_death_pose, third_beam_damage
            nonlocal splash_player_beam_damage
            nonlocal energy_after_impact, final_frozen_beam_damage, frozen_death_pose
            nonlocal first_occluded_tick, first_nearer_tick, wall_reacquired_tick
            previous = sym.stats.value(stats.HERO_DAMAGE)
            frozen_before_beam = pool.total if beam_kills_frozen else None
            capacity_before_beam = projected_bubble.remaining if observe_projected_energy else None
            if observe_projected_energy and blast_tick is not None and fight.tick == blast_tick + 1:
                energy_after_impact = caster.body_script.component.entity_var(87).value()
                self.assertAlmostEqual(
                    energy_after_impact, (projected_capacity - capacity_before_beam) * .2, places=4,
                )
            occluded_distance = None
            if cover_changes_lock and first_occluded_tick is None:
                moving_target = replace(frozen_target, feet=bot.position)
                if not fight.visible_target(origin, moving_target):
                    first_occluded_tick = fight.tick
                    self.assertGreaterEqual(first_occluded_tick, thaw)
                    self.assertFalse(bot.dead)
                    occluded_distance = (
                        combat.segment_distance(origin, *moving_target.segment()) - moving_target.radius
                    )
                    player_distance = combat.segment_distance(origin, *recipient.segment()) - recipient.radius
                    self.assertLess(occluded_distance, player_distance)
            if wall_reacquire and first_occluded_tick is not None and fight.tick < removal:
                moved = replace(frozen_target, feet=bot.position)
                if wall_reacquired_tick is None and fight.visible_target(origin, moved):
                    distance = combat.segment_distance(origin, *moved.segment()) - moved.radius
                    player_distance = combat.segment_distance(origin, *recipient.segment()) - recipient.radius
                    self.assertLess(distance, player_distance)
                    wall_reacquired_tick = fight.tick
            if wall_distance_handoff and first_nearer_tick is None and fight.tick < removal:
                pivot = sentry.bot.position
                moved = replace(frozen_target, feet=bot.position)
                distance = combat.segment_distance(pivot, *moved.segment()) - moved.radius
                player_distance = combat.segment_distance(pivot, *recipient.segment()) - recipient.radius
                self.assertTrue(fight.visible_target(pivot, moved))
                self.assertTrue(fight.visible_target(pivot, recipient))
                if player_distance < distance:
                    first_nearer_tick = fight.tick
                    self.assertGreaterEqual(first_nearer_tick, thaw)
            death_handoff = beam_kills_frozen and pool.death_tick == fight.tick - 1
            splash_handoff = primary_splash_death and pool.death_tick == fight.tick
            handoff = death_handoff or splash_handoff or occluded_distance is not None
            if separate_player_splash and handoff and not interceptor_lethal:
                candidate = next(target for target in fight._targets() if target.owner is companion)
                pivot = sentry.bot.position
                self.assertTrue(fight.visible_target(pivot, candidate))
                self.assertTrue(fight.visible_target(pivot, recipient))
                candidate_distance = combat.segment_distance(pivot, *candidate.segment()) - candidate.radius
                player_distance = combat.segment_distance(pivot, *recipient.segment()) - recipient.radius
                self.assertLessEqual(max(candidate_distance, player_distance), collateral_reach)
                self.assertNotAlmostEqual(candidate_distance, player_distance)
                self.assertEqual(player_distance < candidate_distance, nearer_player)
                if occluded_distance is not None:
                    self.assertLess(occluded_distance, candidate_distance)
            update_sentries(*args, **kwargs)
            dealt = sym.stats.value(stats.HERO_DAMAGE) - previous
            if wall_reacquired_tick is not None and fight.tick < removal:
                self.assertEqual(sentry.target, bot.entity)
                self.assertIs(sentry.slow[0], component)
                self.assertIsNot(sentry.slow[1], aura)
                self.assertAlmostEqual(dealt, beam_tick_damage)
            player_locked = player_can_take_lock and sentry.target == projected_recipient.body
            if occluded_distance is not None or splash_handoff:
                expected_target = companion.entity if interceptor_can_take_lock else projected_recipient.body
                self.assertEqual(sentry.target, expected_target)
            if player_locked:
                retarget_ticks.append(fight.tick)
                self.assertNotIn(aura.id, component.instances)
                if capacity_before_beam > 0:
                    absorbed = capacity_before_beam - projected_bubble.remaining
                    self.assertAlmostEqual(absorbed, beam_tick_damage)
                    self.assertEqual(dealt, 0)
                    self.assertEqual(projected_pool.now, projected_pools_before)
                    self.assertIsNone(sentry.slow)
                    beam_shield_ticks.append(fight.tick)
                    damage_ticks.append(fight.tick)
                else:
                    player_component = projected_recipient.body_script.component
                    self.assertIs(sentry.slow[0], player_component)
                    self.assertTrue(player_component.entity_var(23186).value())
                    lock_slow_ticks.append(fight.tick)
            if ((projected_beam or projected_third_splash) and sentry.target == projected_recipient.entity
                    and capacity_before_beam > 0):
                absorbed = min(beam_tick_damage, capacity_before_beam)
                self.assertAlmostEqual(capacity_before_beam - projected_bubble.remaining, absorbed)
                self.assertEqual(dealt, 0)
                self.assertEqual(projected_pool.now, projected_pools_before)
                self.assertIsNone(sentry.slow)
                self.assertFalse(projected_recipient.body_script.component.entity_var(23186).value())
                self.assertNotIn(aura.id, component.instances)
                if projected_third_splash:
                    self.assertTrue(far_loses_receiver and splash_kills)
                    third_shield_ticks.append(fight.tick)
                else:
                    beam_shield_ticks.append(fight.tick)
                    if projected_lock:
                        retarget_ticks.append(fight.tick)
                    elif far_loses_receiver and blast_tick is not None:
                        reacquired_ticks.append(fight.tick)
                damage_ticks.append(fight.tick)
            if dealt:
                killed_receiver = receiver_beam_lethal and companion_pool.death_tick == fight.tick
                expected_dealt = expected_companion_pool.total if killed_receiver else 40 * combat.TICK
                if beam_kills_frozen and pool.death_tick == fight.tick:
                    expected_dealt = frozen_before_beam
                    final_frozen_beam_damage = dealt
                    frozen_death_pose = bot.position
                    self.assertIsNone(sentry.target)
                    self.assertIsNone(sentry.slow)
                self.assertAlmostEqual(dealt, expected_dealt)
                damage_ticks.append(fight.tick)
                if player_locked:
                    if separate_player_splash:
                        splash_player_beam_damage += expected_splash_player_pool.damage(dealt)
                    else:
                        companion_beam_damage += expected_companion_pool.damage(dealt)
                elif interceptor_can_take_lock and sentry.target == companion.entity:
                    interceptor_lock_ticks.append(fight.tick)
                    companion_beam_damage += expected_companion_pool.damage(dealt)
                    self.assertNotIn(aura.id, component.instances)
                    self.assertIs(sentry.slow[0], companion.body_script.component)
                    self.assertTrue(companion.body_script.component.entity_var(23186).value())
                elif retargets and (killed_receiver or sentry.target == companion.entity):
                    retarget_ticks.append(fight.tick)
                    companion_beam_damage += expected_companion_pool.damage(dealt)
                    self.assertNotIn(aura.id, component.instances)
                    if killed_receiver:
                        receiver_death_pose = companion.position
                        self.assertIsNone(sentry.slow)
                        self.assertFalse(companion.body_script.component.entity_var(23186).value())
                    else:
                        self.assertIs(sentry.slow[0], companion.body_script.component)
                        if projected_lock:
                            lock_slow_ticks.append(fight.tick)
                elif third is not None and sentry.target == third.entity:
                    route_state = fight.tick, retarget_ticks, third.position, companion.position
                    self.assertTrue(far_loses_receiver and splash_kills and not third_lethal, route_state)
                    self.assertIs(sentry.slow[0], third.body_script.component)
                    if projected_third_splash:
                        self.assertTrue(third.body_script.component.entity_var(23186).value())
                    self.assertFalse(companion.body_script.component.entity_var(23186).value())
                    third_beam_ticks.append(fight.tick)
                    third_beam_damage += dealt
                elif (receiver_beam_lethal or far_loses_receiver) and retarget_ticks:
                    reacquired_ticks.append(fight.tick)
                    self.assertEqual(sentry.target, bot.entity)
                    self.assertIs(sentry.slow[0], component)
                    self.assertIsNot(sentry.slow[1], aura)
                    self.assertFalse(companion.body_script.component.entity_var(23186).value())
                    if projected_frozen:
                        lock_slow_ticks.append(fight.tick)
                elif projected_beam_only:
                    self.assertEqual(sentry.target, bot.entity)
                    self.assertIs(sentry.slow[0], component)
                    self.assertTrue(component.entity_var(23186).value())
                    lock_slow_ticks.append(fight.tick)
            self.assertEqual(sentry in fight.sentries.sentries, fight.tick <= last_beam_tick)
            if far_loses_receiver and splash_kills and (third is None or third_lethal):
                self.assertIsNone(sentry.target)
                self.assertIsNone(sentry.slow)
            if rocket is not None and (not intercepts or near_intercept) and fight.tick == removal:
                self.assertNotIn(rocket, fight.rockets)
                self.assertNotIn(center.entity, fight.ice_wall.pieces)
                self.assertNotIn(aura.id, component.instances)
                self.assertEqual(len(fight.ice_wall.pieces), 4)
                if retargets and projected_bubble is not None:
                    self.assertIsNone(sentry.slow)
                    self.assertFalse(companion.body_script.component.entity_var(23186).value())

        def observe_explosion(shot, point, heading, **kwargs):
            nonlocal companion_damage, companion_impact_pose, removal, last_beam_tick, beam_damage
            nonlocal splash_damage, splash_kills
            nonlocal blast_tick, capacity_before_rocket, capacity_after_blast, third_impact_pose
            nonlocal primary_impact_pose
            if shot is rocket and (splash_motion is not None or projected_player):
                blast_tick = fight.tick
                companion_impact_pose = (
                    tuple(companion.mover.position) if projected_player else companion.position
                )
                self.assertEqual(kwargs["surface"], not intercepts)
                origin = point if intercepts else combat._along(point, heading, -.0001)
                targets = fight._splash_targets(shot, point, origin)
                victims = {victim.owner: amount for victim, amount in targets}
                if wall_caster_in_range:
                    self.assertNotIn(caster, victims)
                    moved = next(target for target in fight._targets() if target.owner is bot)
                    distance = combat.segment_distance(sentry.bot.position, *moved.segment()) - moved.radius
                    self.assertLess(distance, caster_distance)
                if third is not None:
                    self.assertEqual(third.dead, third_lethal)
                    if projected_third_splash:
                        lock_target = companion if retargets else bot
                        self.assertEqual(sentry.target, lock_target.entity)
                        self.assertIs(sentry.slow[0], lock_target.body_script.component)
                        third_impact_pose = third.position
                        self.assertNotEqual(third_impact_pose, third_start)
                        self.assertGreater(victims.get(third, 0), 0, third.position)
                    else:
                        self.assertEqual(victims.get(third), None if third_lethal else 80)
                companion_in_splash = (
                    projected_player or (intercepts and not interceptor_lethal)
                    or splash_motion in {"enter", "retarget"}
                )
                self.assertEqual(companion in victims, companion_in_splash and not receiver_beam_lethal)
                if interceptor_lethal:
                    self.assertTrue(companion.dead)
                    self.assertEqual(companion_pool.death_tick, fight.tick)
                    self.assertEqual(companion_pool.killer, soldier.body)
                    self.assertAlmostEqual(companion_damage + companion_beam_damage, 30)
                    self.assertIn(center.entity, fight.ice_wall.pieces)
                    self.assertEqual(center.health, 20)
                if near_intercept:
                    removal = fight.tick
                    last_beam_tick = removal - 1
                    beam_damage = (last_beam_tick - start) * 40 * combat.TICK
                if projected_frozen:
                    self.assertEqual(fight.frost(bot), frost_at_cast)
                    if projected_beam_only:
                        self.assertNotIn(bot, victims)
                    else:
                        self.assertGreater(victims[bot], 0)
                        if splash_health == 30:
                            self.assertGreaterEqual(victims[bot], pool.total)
                elif near_intercept or retargets or primary_in_splash or wall_splash_at_thaw:
                    if wall_primary_misses:
                        self.assertNotIn(bot, victims)
                    else:
                        splash_damage = min(victims[bot], pool.total)
                        splash_kills = victims[bot] >= pool.total
                        self.assertEqual(splash_kills, splash_health == 30)
                        self.assertGreater(splash_damage, 0)
                    if nonlethal_primary_splash:
                        frozen_pools_at_impact = pool.total
                        frost_before_splash = fight.frost(bot)
                    if primary_in_splash or wall_splash_at_thaw:
                        primary_impact_pose = bot.position
                        if impact_offset is None:
                            self.assertTrue(fight.is_stunned(bot))
                        else:
                            self.assertFalse(fight.is_stunned(bot))
                            if wall_reacquire:
                                self.assertEqual(movement_ticks[0], thaw)
                            else:
                                self.assertEqual(movement_ticks, list(range(thaw, fight.tick + 1)))
                            self.assertGreater(primary_impact_pose[2], initial_pose[2])
                elif intercepts:
                    self.assertNotIn(bot, victims)
                if retargets:
                    self.assertEqual(companion.dead, interceptor_lethal or receiver_beam_lethal)
                    if not companion.dead:
                        self.assertEqual(victims[companion] >= companion_pool.total, receiver_lethal)
                        if retarget_intercept and not third_intercepts:
                            self.assertEqual(victims[companion], 80)
                if projected_bubble is None or projected_recipient is not companion:
                    companion_damage += expected_companion_pool.damage(victims.get(companion, 0))
                if projected_bubble is not None:
                    if player_projection:
                        player_held_lock = wall_handoff and not wall_reacquire
                        expected_lock = projected_recipient.body if player_held_lock else bot.entity
                        self.assertEqual(sentry.target, expected_lock)
                        self.assertGreaterEqual(
                            victims[projected_recipient], projected_pool.now[combat.HEALTH_POOL],
                        )
                    if projected_beam and not projected_direct:
                        expected_lock = companion if retargets else projected_recipient
                        self.assertEqual(sentry.target, expected_lock.entity)
                        if expected_lock is projected_recipient:
                            self.assertIsNone(sentry.slow)
                        absorbed_by_beam = len(beam_shield_ticks) * beam_tick_damage
                        capacity_before_rocket = initial_beam_capacity - absorbed_by_beam
                    if wall_handoff:
                        absorbed_by_beam = len(beam_shield_ticks) * beam_tick_damage
                        capacity_before_rocket = projected_capacity - absorbed_by_beam
                    capacity_at_blast = (
                        max(0, capacity_before_rocket - 40) if projected_direct else capacity_before_rocket
                    )
                    self.assertAlmostEqual(projected_bubble.remaining, capacity_at_blast)
                    contact = fight.zarya_barriers.contact(projected_recipient)
                    if projected_direct:
                        self.assertEqual(victims[projected_recipient], 80)
                    if capacity_at_blast:
                        self.assertIs(contact[2], projected_bubble)
                        self.assertIsNone(kwargs.get("protected_entity"))
                    else:
                        self.assertIsNone(contact)
                        self.assertEqual(kwargs.get("protected_entity"), recipient.entity)
                    splash_offer = victims.get(projected_recipient, 0) if projected_beam_only else (
                        victims[projected_recipient]
                    )
                    expected_barrier_remaining = max(0, capacity_at_blast - splash_offer)
            result = explode(shot, point, heading, **kwargs)
            if shot is rocket and nonlethal_primary_splash:
                self.assertFalse(bot.dead)
                self.assertAlmostEqual(pool.total, frozen_pools_at_impact - splash_damage)
                self.assertEqual(fight.frost(bot), frost_before_splash)
                self.assertEqual(sentry.target, bot.entity)
                self.assertIs(sentry.slow[1], aura)
            if shot is rocket and projected_bubble is not None:
                capacity_after_blast = projected_bubble.remaining
                self.assertAlmostEqual(projected_bubble.remaining, expected_barrier_remaining)
                self.assertEqual(projected_pool.now, projected_pools_before)
                if player_projection:
                    self.assertIsNone(projected_pool.last_damage_at)
                if projected_frozen:
                    self.assertEqual(fight.frost(bot), frost_at_cast)
                    self.assertEqual(pool.last_damage_at, projected_damage_time)
                contact = fight.zarya_barriers.contact(projected_recipient)
                if expected_barrier_remaining == 0:
                    self.assertIsNone(contact)
                else:
                    self.assertIs(contact[2], projected_bubble)
            if shot is rocket and near_intercept:
                impact_state = point, companion_impact_pose, center.health, retarget_ticks
                self.assertNotIn(center.entity, fight.ice_wall.pieces, impact_state)
            if shot is rocket and intercepts and not near_intercept:
                self.assertIn(center.entity, fight.ice_wall.pieces)
                self.assertEqual(center.health, 20)
                if far_retarget:
                    if projected_lock:
                        self.assertEqual(sentry.target, companion.entity)
                        self.assertIsNone(sentry.slow)
                    else:
                        self.assertIs(sentry.slow[0], companion.body_script.component)
                elif projected_beam_only:
                    self.assertEqual(sentry.target, bot.entity)
                    self.assertIsNone(sentry.slow)
                elif primary_splash_death:
                    self.assertTrue(bot.dead)
                    self.assertEqual(pool.killer, soldier.body)
                else:
                    self.assertIs(sentry.slow[1], aura)
            return result

        def observe_body_impact(shot, target, point):
            nonlocal companion_damage, third_impact_pose, capacity_before_rocket
            if shot is rocket and intercepts:
                self.assertIs(target.owner, third if third_intercepts else companion)
                self.assertEqual(shot.impact_damage(), 40)
                if retarget_intercept or far_retarget:
                    beam_state = fight.tick, retarget_ticks, sentry.target, companion.position
                    if projected_lock:
                        self.assertEqual(sentry.target, companion.entity, beam_state)
                        self.assertIsNone(sentry.slow, beam_state)
                        absorbed_by_beam = len(beam_shield_ticks) * beam_tick_damage
                        capacity_before_rocket = projected_capacity - absorbed_by_beam
                    else:
                        self.assertIs(sentry.slow[0], companion.body_script.component, beam_state)
                intercept_ticks.append(fight.tick)
                if not third_intercepts:
                    if not projected_direct:
                        companion_damage += expected_companion_pool.damage(shot.impact_damage())
                else:
                    third_impact_pose = third.position
                    self.assertNotEqual(third_impact_pose, third_start)
                if projected_direct:
                    self.assertIs(target.owner, projected_recipient)
                    self.assertIs(fight.zarya_barriers.contact(projected_recipient)[2], projected_bubble)
                    self.assertAlmostEqual(projected_bubble.remaining, capacity_before_rocket)
                elif projected_frozen or projected_third_splash or separate_player_splash:
                    self.assertIsNone(fight.zarya_barriers.contact(target.owner))
            result = body_impact(shot, target, point)
            if shot is rocket and projected_direct:
                self.assertAlmostEqual(projected_bubble.remaining, max(0, capacity_before_rocket - 40))
                self.assertEqual(projected_pool.now, projected_pools_before)
            return result

        with (
            patch.object(type(bot), "_tick", new=observe_route),
            patch.object(fight.sentries, "update", side_effect=observe_sentry),
            patch.object(fight, "_explode", side_effect=observe_explosion),
            patch.object(fight, "_projectile_body_impact", side_effect=observe_body_impact),
        ):
            checkpoints = (end,) if checkpoint_offset is None else (removal, end)
            for tick in checkpoints if gap else range(start + 1, end + 1):
                run.match.update(tick * combat.TICK, tick)
                if checkpoint_offset is not None and tick == removal:
                    self.assertIn(center.entity, fight.ice_wall.pieces)
                    self.assertIs(sentry.slow[1], aura)
                    if rifle_break:
                        attacker = soldier or self.ice_support_rifle_enemy(run, sym, distance=4)
                        self.break_ice_support_at_checkpoint(fight, center, attacker, pieces)
                    else:
                        mei.take_commands([Command(tick, buttons=16)])
                        self.assertFalse(fight.ice_wall.pieces)
                    self.assertNotIn(sentry, fight.sentries.sentries)
                    self.assertNotIn(aura.id, component.instances)
        if beam_kills_frozen:
            self.assertTrue(bot.dead)
            death_tick = start + math.ceil((before - splash_damage) / beam_tick_damage)
            self.assertEqual(pool.death_tick, death_tick)
            self.assertLess(rocket.last_tick, death_tick)
            if expect_thawed_death:
                self.assertLess(thaw, death_tick)
            if nonlethal_primary_splash:
                self.assertLess(death_tick, thaw)
            self.assertEqual(movement_ticks, list(range(thaw, death_tick + 1)))
            self.assertEqual(pool.killer, sym.body)
            self.assertEqual(bot.body_script.death_frame, death_tick)
            self.assertIn(bot, run.match.bots)
            self.assertEqual(bot.position, frozen_death_pose)
            self.assertEqual(bot.velocity, (0, 0, 0))
            self.assertEqual(bot.position == initial_pose, not movement_ticks)
            self.assertEqual(sym.stats.value(stats.ELIMINATIONS), 1)
            self.assertIsNotNone(final_frozen_beam_damage)
            beam_damage -= beam_tick_damage - final_frozen_beam_damage
        elif splash_kills:
            primary_death_tick = rocket.last_tick if far_retarget or primary_splash_death else removal
            if wall_splash_at_thaw or (primary_splash_death and impact_offset is not None):
                if wall_reacquire:
                    self.assertLessEqual(movement_ticks[-1], primary_death_tick)
                else:
                    self.assertEqual(movement_ticks, list(range(thaw, primary_death_tick + 1)))
                self.assertEqual(bot.position, primary_impact_pose)
                self.assertIn(bot, run.match.bots)
            else:
                self.assertFalse(movement_ticks)
                self.assertEqual(bot.position, initial_pose)
            self.assertTrue(bot.dead)
            self.assertEqual(pool.death_tick, primary_death_tick)
            self.assertEqual(pool.killer, soldier.body)
            self.assertIsNotNone(bot.body_script)
            self.assertEqual(bot.body_script.death_frame, primary_death_tick)
            self.assertTrue(component.owner_dead)
            self.assertEqual(bot.velocity, (0, 0, 0))
        else:
            self.assertFalse(bot.dead)
            self.assertTrue(movement_ticks)
            self.assertEqual(movement_ticks[0], route_opens)
            if nonlethal_primary_splash:
                self.assertGreater(bot.position[2], initial_pose[2])
                self.assertLessEqual(bot.position[2], 30.1)
                self.assertEqual(sym.stats.value(stats.ELIMINATIONS), 0)
        if far_loses_receiver and splash_kills and (third is None or third_lethal):
            last_beam_tick = rocket.last_tick - 1
            beam_damage = (last_beam_tick - start) * 40 * combat.TICK
        self.assertEqual(damage_ticks, list(range(start + 1, last_beam_tick + 1)))
        beam_damage -= len(third_shield_ticks) * beam_tick_damage
        if interceptor_can_take_lock:
            first_lock_tick = pool.death_tick + 1 if interceptor_lock_on_death else first_occluded_tick
            if primary_splash_death:
                first_lock_tick = blast_tick
            self.assertIsNotNone(first_lock_tick)
            self.assertEqual(interceptor_lock_ticks, list(range(first_lock_tick, removal)))
            self.assertFalse(companion.body_script.component.entity_var(23186).value())
            self.assertFalse(projected_recipient.body_script.component.entity_var(23186).value())
            self.assertEqual(projected_bubble.remaining, capacity_after_blast)
        if player_can_take_lock:
            self.assertTrue(
                retarget_ticks, (sentry.bot.position, initial_pose, bot.position, companion_impact_pose),
            )
            if primary_splash_death:
                self.assertEqual(retarget_ticks[0], blast_tick)
            elif player_lock_on_death:
                self.assertEqual(retarget_ticks[0], pool.death_tick + 1)
            else:
                self.assertGreaterEqual(retarget_ticks[0], thaw)
            if player_lock_on_occlusion:
                self.assertEqual(retarget_ticks[0], first_occluded_tick)
            if wall_distance_handoff:
                self.assertEqual(retarget_ticks[0], first_nearer_tick)
            if wall_reacquire:
                self.assertIsNotNone(wall_reacquired_tick)
                self.assertLess(retarget_ticks[0], wall_reacquired_tick)
                self.assertLess(wall_reacquired_tick, removal)
            lock_end = wall_reacquired_tick if wall_reacquire else removal
            self.assertEqual(retarget_ticks, list(range(retarget_ticks[0], lock_end)))
            shielded = wall_handoff or projected_capacity == 200
            self.assertEqual(beam_shield_ticks, retarget_ticks if shielded else [])
            self.assertEqual(lock_slow_ticks, [] if shielded else retarget_ticks)
            self.assertFalse(projected_recipient.body_script.component.entity_var(23186).value())
            beam_damage -= len(beam_shield_ticks) * beam_tick_damage
        if projected_beam:
            self.assertTrue(beam_shield_ticks)
            first_shield_tick = retarget_ticks[0] if projected_lock else start + 1
            self.assertEqual(beam_shield_ticks[0], first_shield_tick)
            self.assertLess(beam_shield_ticks[0], rocket.last_tick)
            if projected_direct:
                self.assertEqual(capacity_before_rocket > 40, projected_capacity != 30)
            self.assertEqual(capacity_after_blast > 0, projected_beam_only or projected_capacity == 200)
            beam_damage -= len(beam_shield_ticks) * beam_tick_damage
            breaks_under_live_lock = far_retarget and projected_capacity != 200 and (
                projected_lock or far_loses_receiver
            )
            first_slow = rocket.last_tick if breaks_under_live_lock else removal
            if projected_beam_only:
                self.assertEqual(projected_bubble.remaining > 0, projected_capacity == 200)
                until_break = math.ceil(initial_beam_capacity / beam_tick_damage)
                shielded_ticks = min(last_beam_tick - start, until_break)
                first_slow = start + 1 + shielded_ticks
                self.assertEqual(beam_shield_ticks, list(range(start + 1, first_slow)))
            self.assertEqual(lock_slow_ticks, list(range(first_slow, removal)))
        if retargets:
            self.assertTrue(retarget_ticks)
            self.assertGreater(retarget_ticks[0], start + 1)
            if receiver_beam_lethal:
                self.assertEqual(retarget_ticks, [retarget_ticks[0], retarget_ticks[0] + 1])
                self.assertTrue(reacquired_ticks)
                self.assertEqual(reacquired_ticks, list(range(retarget_ticks[-1] + 1, removal)))
                beam_damage -= len(retarget_ticks) * 40 * combat.TICK - companion_beam_damage
            elif far_loses_receiver:
                self.assertEqual(retarget_ticks, list(range(retarget_ticks[0], rocket.last_tick)))
                expected_reacquisition = [] if splash_kills else list(range(rocket.last_tick, removal))
                self.assertEqual(reacquired_ticks, expected_reacquisition)
                if third is not None and splash_kills and not third_lethal:
                    self.assertEqual(
                        third_shield_ticks + third_beam_ticks, list(range(rocket.last_tick, removal)),
                    )
            else:
                self.assertEqual(retarget_ticks, list(range(retarget_ticks[0], removal)))
        self.assertFalse(fight.ice_wall.pieces)
        other_pillars = [piece for piece in pieces if piece is not center]
        if intercepts and not near_intercept:
            self.assertEqual([piece.health for piece in pieces], wall_health_before)
        elif rocket is None:
            self.assertTrue(all(piece.health == 400 for piece in other_pillars))
        if rocket is not None:
            if intercepts:
                self.assertEqual(intercept_ticks, [rocket.last_tick])
                if primary_in_splash and impact_offset is not None:
                    self.assertEqual(rocket.last_tick, thaw + impact_offset)
                else:
                    self.assertLess(rocket.last_tick, thaw)
            else:
                self.assertEqual(rocket.last_tick, removal)
            third_damage = third_before - third_pool.total - third_beam_damage if third is not None else 0
            self.assertAlmostEqual(
                soldier.stats.value(stats.HERO_DAMAGE), splash_damage + companion_damage + third_damage,
            )
            eliminations = int(splash_kills) + int(companion_lethal and not receiver_beam_lethal)
            eliminations += int(third_lethal)
            self.assertEqual(soldier.stats.value(stats.ELIMINATIONS), eliminations)
            if companion is not None:
                self.assertAlmostEqual(
                    companion_before - companion_pool.total, companion_damage + companion_beam_damage,
                )
                self.assertEqual(companion_pool.now.keys(), expected_companion_pool.now.keys())
                for kind, expected in expected_companion_pool.now.items():
                    self.assertAlmostEqual(companion_pool.now[kind], expected)
                if companion_damage:
                    last_damage_tick = (
                        last_beam_tick
                        if (far_retarget and not far_loses_receiver) or interceptor_can_take_lock
                        else rocket.last_tick
                    )
                    self.assertAlmostEqual(companion_pool.last_damage_at, last_damage_tick * combat.TICK)
                elif companion_beam_damage:
                    self.assertAlmostEqual(companion_pool.last_damage_at, retarget_ticks[-1] * combat.TICK)
                else:
                    self.assertIsNone(companion_pool.last_damage_at)
                self.assertEqual(companion_pool.dead, companion_lethal)
            self.assertTrue(all(piece.health > 0 for piece in other_pillars))
            if not intercepts or near_intercept:
                self.assertTrue(any(piece.health < 400 for piece in other_pillars))
        support_health = 20 if intercepts and not near_intercept else (0 if rifle_break else 400)
        self.assertEqual(center.health, support_health)
        self.assertNotIn(sentry, fight.sentries.sentries)
        self.assertNotIn(sentry.bot, run.match.bots)
        self.assertNotIn(aura.id, component.instances)
        self.assertFalse(component.entity_var(23186).value())
        self.assertFalse(fight.is_stunned(bot))
        damage = before - pool.total
        self.assertEqual(mei.stats.value(stats.HERO_DAMAGE), mei_damage_before)
        other_beam_damage = companion_beam_damage + splash_player_beam_damage + third_beam_damage
        primary_beam_damage = beam_damage - other_beam_damage
        self.assertAlmostEqual(damage, primary_beam_damage + splash_damage)
        self.assertAlmostEqual(sym.stats.value(stats.HERO_DAMAGE) - damage_before, beam_damage)
        earned = charge(sym) - charge_before
        self.assertTrue(any(abs(earned - beam_damage - passive) < .001 for passive in (5, 10)))
        result = removal, thaw, movement_ticks, round(damage, 4), round(earned, 3), bot.position
        if projected_bubble is not None:
            result = (*result, round(projected_bubble.remaining, 4))
        if separate_player_splash:
            self.assertIsNotNone(energy_after_impact)
            handoff_tick = blast_tick if primary_splash_death else first_occluded_tick
            handoff_observation = round(final_frozen_beam_damage, 4) if beam_kills_frozen else handoff_tick
            self.assertEqual(capacity_after_blast > 0, projected_capacity == 200)
            self.assertEqual(tuple(splash_player.mover.position), recipient.feet)
            if splash_player_beam_damage:
                self.assertEqual(projected_pool.now, expected_splash_player_pool.now)
                self.assertAlmostEqual(projected_pool.last_damage_at, retarget_ticks[-1] * combat.TICK)
            else:
                self.assertEqual(projected_pool.now, projected_pools_before)
                self.assertIsNone(projected_pool.last_damage_at)
            result = (
                *result, round(energy_after_impact, 4), recipient.feet,
                tuple(projected_pool.now.values()), projected_pool.death_tick,
            )
            if player_can_take_lock:
                result = (
                    *result, round(capacity_after_blast, 4), retarget_ticks, beam_shield_ticks,
                    lock_slow_ticks, round(splash_player_beam_damage, 4),
                    handoff_observation,
                )
        if interceptor_can_take_lock:
            result = (
                *result, interceptor_lock_ticks, round(companion_beam_damage, 4),
                handoff_observation,
            )
        if projected_player:
            self.assertIsNotNone(energy_after_impact)
            self.assertEqual(capacity_after_blast > 0, projected_capacity == 200)
            self.assertEqual(tuple(companion.mover.position), companion_impact_pose)
            if not companion_beam_damage:
                self.assertEqual(companion_pool.now, projected_pools_before)
                self.assertIsNone(companion_pool.last_damage_at)
            result = (
                *result, round(energy_after_impact, 4), companion_impact_pose,
                tuple(companion_pool.now.values()), companion_pool.death_tick,
            )
            if intercepts and not near_intercept:
                result = (*result, rocket.last_tick)
            if player_can_take_lock:
                result = (
                    *result, round(capacity_after_blast, 4), retarget_ticks, beam_shield_ticks,
                    lock_slow_ticks, round(companion_beam_damage, 4),
                )
            if player_lock_on_death:
                result = (*result, round(final_frozen_beam_damage, 4))
            if player_lock_on_occlusion:
                result = (*result, first_occluded_tick)
            if wall_distance_handoff:
                result = (*result, first_nearer_tick)
            if wall_reacquire:
                result = (*result, wall_reacquired_tick)
        if projected_beam:
            self.assertIsNotNone(energy_after_impact)
            final_energy = caster.body_script.component.entity_var(87).value()
            result = (
                *result, beam_shield_ticks, lock_slow_ticks, round(capacity_before_rocket, 4),
                round(capacity_after_blast, 4), round(energy_after_impact, 4), round(final_energy, 4),
            )
        if retargets:
            self.assertFalse(companion.body_script.component.entity_var(23186).value())
            result = (*result, retarget_ticks[0], round(companion_beam_damage, 4))
        if receiver_beam_lethal:
            self.assertEqual(sym.stats.value(stats.ELIMINATIONS), 1)
            self.assertEqual(companion_impact_pose, receiver_death_pose)
            result = (*result, reacquired_ticks[0])
        if far_retarget:
            result = (*result, reacquired_ticks[0] if reacquired_ticks else None)
        if third is not None:
            if projected_bubble is not None:
                if third_beam_ticks:
                    self.assertAlmostEqual(third_before - third_pool.total, third_beam_damage)
                    self.assertAlmostEqual(third_pool.last_damage_at, third_beam_ticks[-1] * combat.TICK)
                else:
                    self.assertEqual(third_pool.now, projected_pools_before)
                    self.assertIsNone(third_pool.last_damage_at)
                if projected_third_splash:
                    self.assertEqual(capacity_after_blast > 0, projected_capacity == 200)
                    self.assertIsNotNone(energy_after_impact)
                    result = (
                        *result, round(energy_after_impact, 4),
                        tuple(round(value, 4) for value in third_pool.now.values()),
                    )
                    if far_retarget:
                        result = (
                            *result, round(capacity_after_blast, 4), third_shield_ticks, third_beam_ticks,
                        )
            else:
                self.assertAlmostEqual(third_damage, 30 if third_lethal else 120)
                third_last_damage = third_beam_ticks[-1] if third_beam_ticks else rocket.last_tick
                self.assertAlmostEqual(third_pool.last_damage_at, third_last_damage * combat.TICK)
            self.assertIs(third.body_script, third_script)
            self.assertIn(third, run.match.bots)
            self.assertEqual(third.dead, third_lethal)
            self.assertFalse(third.body_script.component.entity_var(23186).value())
            if third_lethal:
                self.assertEqual(third_pool.killer, soldier.body)
                self.assertEqual(third_pool.death_tick, rocket.last_tick)
                self.assertEqual(third_script.death_frame, rocket.last_tick)
                self.assertEqual(third.position, third_impact_pose)
                self.assertEqual(third.velocity, (0, 0, 0))
            result = (
                *result, third_impact_pose, third.position, round(third_damage, 4), third_pool.death_tick,
                third_beam_ticks[0] if third_beam_ticks else None, round(third_beam_damage, 4),
            )
        if player_lethal:
            respawn_tick = self.verify_helix_victim_respawn(
                run, companion, companion_pool, soldier, removal, companion_full,
            )
            result = (*result, respawn_tick)
        if extra_npc is not None:
            if splash_motion is None:
                self.assertEqual(companion.position, companion_position)
            else:
                self.assertIsNotNone(companion_impact_pose)
                self.assertNotEqual(companion_impact_pose, companion_position)
                result = (*result, companion_impact_pose, companion.position)
            if intercepts:
                result = (*result, rocket.last_tick)
            self.assertIs(companion.body_script, companion_script)
            self.assertIn(companion, run.match.bots)
            self.assertEqual(companion.dead, companion_lethal)
            if companion_lethal:
                death_tick = rocket.last_tick if interceptor_lethal or far_loses_receiver else removal
                if receiver_beam_lethal:
                    death_tick = retarget_ticks[-1]
                killer = sym if receiver_beam_lethal else soldier
                self.assertEqual(companion_pool.killer, killer.body)
                self.assertEqual(companion_pool.death_tick, death_tick)
                self.assertEqual(companion_script.death_frame, death_tick)
            if interceptor_lethal or receiver_lethal or receiver_beam_lethal:
                self.assertEqual(companion.position, companion_impact_pose)
                self.assertEqual(companion.velocity, (0, 0, 0))
            elif retargets and not intercepts:
                self.assertNotEqual(companion.position, companion_impact_pose)
            companion_pools_after = tuple(round(value, 4) for value in companion_pool.now.values())
            result = (*result, companion_pool.death_tick, companion_pools_before, companion_pools_after)
        if splash_health is not None:
            if (primary_in_splash and impact_offset is not None) or wall_splash_at_thaw:
                result = (*result, primary_impact_pose)
            if wall_caster_in_range:
                self.assertEqual(caster_pool.now, caster_pools_before)
                self.assertIsNone(caster_pool.last_damage_at)
                result = (
                    *result, caster_target.feet, round(caster_distance, 4), tuple(caster_pool.now.values()),
                )
            return (*result, round(splash_damage, 4), pool.death_tick, round(companion_damage, 4))
        return result

    def far_helix_retarget_route(self, run, frozen, sentry, definition):
        fight = run.match.combat
        companion = npc.spawn(run.match, definition, (-8, 1, 29), team=frozen.team)
        targets = {target.owner: target for target in fight._targets()}
        moving = targets[companion]
        original = targets[frozen]
        origin = sentry.bot.position
        reach = combat.segment_distance(origin, *original.segment()) - original.radius + moving.radius
        # Cross the beam distance threshold before the capsule enters the
        # rocket ray. Use each body's real radius and capsule height.
        cross_z = 28 + moving.radius + .15
        offset = combat.segment_distance(origin, *moving.segment((origin[0], 1, cross_z)))
        x = origin[0] - math.sqrt(reach**2 - offset**2)
        companion.position = (x, 1, cross_z + .15)
        return companion, (x, 1, 28)

    def verify_helix_victim_respawn(self, run, victim, pool, soldier, impact_tick, full_health):
        fight = run.match.combat
        self.assertEqual(pool.death_tick, impact_tick)
        self.assertEqual(pool.killer, soldier.body)
        self.assertEqual(victim.stats.value(stats.DEATHS), 1)
        old_body = victim.body
        [death_notice] = victim.client.messages(combat.DEATH_NOTICE)
        self.assertEqual(death_notice["+0x78"], combat.KILLED_BY)
        self.assertEqual(death_notice["+0x7C"], {"+0x0": soldier.body})
        kills = soldier.client.messages(combat.KILL_NOTICE)
        [player_kill] = [notice for notice in kills if notice["+0x84"] == {"+0x0": old_body}]
        self.assertEqual(player_kill["+0x80"], death_notice["+0x7C"])
        [deadline] = [when for when, player in fight.respawns if player is victim]
        delay = combat.respawn_seconds(run.match)
        self.assertAlmostEqual(deadline, impact_tick * combat.TICK + delay)
        respawn_tick = impact_tick + math.ceil(delay / combat.TICK)
        before = respawn_tick - 1
        run.match.update(before * combat.TICK, before)
        self.assertEqual(victim.body, old_body)
        self.assertTrue(fight.is_dead(victim))
        run.match.update(respawn_tick * combat.TICK, respawn_tick)
        self.assertNotEqual(victim.body, old_body)
        self.assertFalse(fight.is_dead(victim))
        self.assertEqual(health(run, victim).total, full_health)
        self.assertEqual(victim.stats.value(stats.DEATHS), 1)
        self.assertFalse(any(player is victim for _, player in fight.respawns))
        return respawn_tick

    def ice_support_rifle_enemy(self, run, sym, *, distance):
        fixture = barrier_tests.PhotonBarrierTests()
        soldier = fixture.add_target(run, 3, (-distance, 1, 28))
        soldier.team = 1 - sym.team
        return soldier

    def break_ice_support_at_checkpoint(self, fight, center, soldier, pieces):
        yaw, pitch = aim(fight._eye(soldier), (center.feet[0], 2, 28))
        ammo = soldier.body_script.component.instances[11].vars[53]
        before = ammo.value()
        frame = soldier.body_script.frame + 1
        soldier.take_commands([Command(frame, yaw=yaw, pitch=pitch, buttons=1)])
        self.assertEqual(ammo.value(), before - 1)
        other_pillars = [piece for piece in pieces if piece is not center]
        self.assertCountEqual(fight.ice_wall.pieces.values(), other_pillars)
        self.assertEqual(soldier.stats.value(stats.HERO_DAMAGE), 0)

    def test_actual_mei_spray_freeze_releases_sustained_assassin_pin(self):
        definition = next(d for d in self.definitions.values() if d.name == "Assassin")
        run, sym, bot, _ = self.create(definition, distance=1.5)
        mei = run.players[1]
        self.assertTrue(run.match.switch_hero(mei, heroes()[cryo.HERO]))
        barrier_tests.PhotonBarrierTests().move(mei, (3, 1, 1.5))
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        mei.team = sym.team
        fight = run.match.combat
        sym_aim = aim(fight._eye(sym), (0, 2, 1.5))
        sym.mover.yaw, sym.mover.pitch = sym_aim
        self.assertEqual(npc_control.start(run.match, sym), bot.entity)
        run.step({})
        self.assertEqual(fight.pins.get(bot.entity), sym.body)
        first_contact = health(run, sym).total
        spray = aim(fight._eye(mei), (0, 1.9, 1.5))
        for _ in range(135):
            run.step({mei: (*spray, 1)})
            if fight.is_stunned(bot):
                break
        self.assertTrue(fight.is_stunned(bot))
        self.assertFalse(bot.dead)
        self.assertLess(health(run, sym).total, first_contact)
        self.assertFalse(fight.pins)
        self.assertFalse(fight.is_stunned(sym))
        after = health(run, sym).total
        bot_before = health(run, bot).total
        for _ in range(6):
            run.step({sym: (*sym_aim, 1)})
        self.assertEqual(health(run, sym).total, after)
        self.assertLess(health(run, bot).total, bot_before)
        for _ in range(150):
            run.step({})
            if fight.pins.get(bot.entity) == sym.body:
                break
        self.assertFalse(fight.is_stunned(bot))
        self.assertEqual(fight.pins.get(bot.entity), sym.body)

    def test_heavy_charge_console_drive_takes_actual_beam_and_cancels(self):
        definition = next(d for d in self.definitions.values() if d.name == "Heavy Assault")
        run, sym, bot, _ = self.create(definition, distance=9)
        fight = run.match.combat
        sym.mover.yaw, sym.mover.pitch = aim(fight._eye(sym), (0, 2, 9))
        console = npc_console.Console({})
        start = bot.position
        self.assertEqual(console.press("}", run.match, sym)["entity"], bot.entity)
        run.step({})
        charge = heavy_charge.instance(bot)
        self.assertEqual(heavy_charge.phase(charge), 1)
        self.assertFalse(bot.firing)
        self.assertEqual(bot.position, start)
        for _ in range(75):
            run.step({})
            if bot.position != start:
                break
        self.assertLess(bot.position[2], start[2])
        self.assertAlmostEqual(bot.velocity[2], -15)
        before = health(run, bot).total
        angles = aim(fight._eye(sym), (0, 2, bot.position[2]))
        run.step({sym: (*angles, 1)})
        self.assertLess(health(run, bot).total, before)
        self.assertEqual(heavy_charge.phase(charge), 1)
        self.assertEqual(console.press("{", run.match, sym)["count"], 1)
        stopped = bot.position
        for _ in range(10):
            run.step({})
        self.assertEqual(heavy_charge.phase(charge), 0)
        self.assertEqual(bot.position, stopped)
        self.assertEqual(bot.velocity, (0, 0, 0))

    def test_heavy_charge_stops_on_actual_spray_freeze_or_static_wall(self):
        definition = next(d for d in self.definitions.values() if d.name == "Heavy Assault")
        for frozen in (False, True):
            with self.subTest(frozen=frozen):
                run, sym, bot, _ = self.create(definition, distance=9)
                fight = run.match.combat
                mei = run.players[1]
                if frozen:
                    self.assertTrue(run.match.switch_hero(mei, heroes()[cryo.HERO]))
                    barrier_tests.PhotonBarrierTests().move(mei, (3, 1, 6))
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    mei.team = sym.team
                    spray = aim(fight._eye(mei), (0, 2, 9))
                    for _ in range(65):
                        run.step({mei: (*spray, 1)})
                    self.assertFalse(fight.is_stunned(bot))
                    self.assertGreater(fight.frost(bot)[0], .5)
                sym.mover.yaw, sym.mover.pitch = aim(fight._eye(sym), (0, 2, 9))
                npc_control.start_charge(run.match, sym)
                run.step({})
                charge = heavy_charge.instance(bot)
                self.assertEqual(heavy_charge.phase(charge), 1)
                if frozen:
                    for _ in range(125):
                        x, y, z = bot.position
                        angles = aim(fight._eye(mei), (x, y + 1, z))
                        run.step({mei: (*angles, 1)})
                        if fight.is_stunned(bot):
                            break
                    self.assertTrue(fight.is_stunned(bot))
                    run.step({})
                    self.assertGreater(charge.vars[61].value(), 0)
                    with self.assertRaisesRegex(ValueError, "controlled"):
                        npc_control.start_charge(run.match, sym)
                else:
                    from test_symmetra_turrets import wall

                    with patch.object(fight, "_collision", return_value=wall()):
                        for _ in range(125):
                            run.step({})
                            if heavy_charge.phase(charge) == 0:
                                break
                    self.assertGreater(bot.position[2], 3)
                    self.assertLess(bot.position[2], 9)
                self.assertEqual(heavy_charge.phase(charge), 0)
                stopped = bot.position
                for _ in range(8):
                    run.step({})
                self.assertEqual(bot.position, stopped)
                self.assertEqual(bot.velocity, (0, 0, 0))

    def test_heavy_charge_graze_damages_once_and_respects_team_cone_and_cover(self):
        definition = next(d for d in self.definitions.values() if d.name == "Heavy Assault")
        for case in ("hit", "far", "front", "ally", "wall"):
            with self.subTest(case=case):
                run, sym, bot, _ = self.create(definition, distance=8)
                fixture = barrier_tests.PhotonBarrierTests()
                fight = run.match.combat
                sym.mover.yaw, sym.mover.pitch = aim(fight._eye(sym), (0, 2, 8))
                npc_control.start_charge(run.match, sym)
                run.step({})
                side = {"far": 4.5, "front": 0}.get(case, 1.7)
                fixture.move(sym, (side, 1, 0))
                if case == "ally":
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    sym.team = bot.team
                if case == "wall":
                    cover = World([
                        (.9, 0, -10, .9, 5, -10, .9, 5, 10),
                        (.9, 0, -10, .9, 5, 10, .9, 0, 10),
                        (.9, 0, -10, .9, 5, 10, .9, 5, -10),
                        (.9, 0, -10, .9, 0, 10, .9, 5, 10),
                    ])
                    self.enterContext(patch.object(fight, "_collision", return_value=cover))
                pool = health(run, sym)
                for _ in range(105):
                    run.step({})
                    if case == "front":
                        self.assertNotIn(sym.body, bot.charge_contacts)
                    if case == "hit" and pool.total < 225:
                        break
                if case == "hit":
                    self.assertAlmostEqual(pool.total, 175)
                    self.assertAlmostEqual(pool.now[combat.SHIELDS_POOL], 75)
                    self.assertGreater(sym.mover.position[0], side)
                    charge = heavy_charge.instance(bot)
                    slot = expr.lvalue(charge.graph.node(heavy_charge.SIDE_SEARCH).field("m_8A2905A0"))
                    relative = -bot.position[2]
                    for _ in range(6):
                        fixture.move(sym, (side, 1, bot.position[2] + relative - .24))
                        run.step({})
                        self.assertIn(expr.Entity(sym.body), charge.vars[slot[1]].value())
                        self.assertAlmostEqual(pool.total, 175)
                    npc_control.stop(run.match)
                    run.step({})
                    self.assertFalse(bot.charge_contacts)
                    self.assertFalse(charge.vars[slot[1]].value())
                elif case != "front":
                    self.assertEqual(pool.total, 225)

    @unittest.skipUnless(os.environ.get("ORPHEUS_EXTRA_GRAPH_BUNDLES"), "requires barrier actor shard")
    def test_heavy_charge_close_impact_deals_damage_and_stuns_through_photon_barrier(self):
        definition = next(d for d in self.definitions.values() if d.name == "Heavy Assault")
        for shielded in (False, True):
            with self.subTest(shielded=shielded):
                if shielded:
                    fixture = barrier_tests.PhotonBarrierTests()
                    run, sym, other, barrier = fixture.deploy()
                    fixture.move(other, (15, 0, 0))
                    bot = npc.spawn(run.match, definition, (0, 0, 8), team=1 - sym.team)
                    barrier_pool = run.match.combat.vitals[barrier.bot.entity]
                else:
                    run, sym, bot, _ = self.create(definition, distance=8)
                fight = run.match.combat
                x, y, z = bot.position
                sym.mover.yaw, sym.mover.pitch = aim(fight._eye(sym), (x, y + 1, z))
                npc_control.start_charge(run.match, sym)
                run.step({})
                position = sym.mover.position[:]
                fixture = barrier_tests.PhotonBarrierTests()
                fixture.move(sym, (4, position[1], 0))
                for _ in range(110):
                    run.step({})
                    if bot.position[2] < 1.1:
                        break
                self.assertLess(bot.position[2], 1.1)
                fixture.move(sym, (0, position[1], bot.position[2] - .45))
                pool = health(run, sym)
                for _ in range(130):
                    run.step({})
                    if fight.is_stunned(sym):
                        break
                self.assertTrue(fight.is_stunned(sym))
                self.assertAlmostEqual(pool.total, 50)
                self.assertEqual(pool.now[combat.SHIELDS_POOL], 0)
                self.assertFalse(heavy_charge.phase(heavy_charge.instance(bot)))
                if shielded:
                    self.assertIn(barrier, fight.barriers.walls)
                    self.assertEqual(barrier_pool.total, 4000)
                deadline = fight.stuns[sym.body]
                self.assertEqual(deadline - fight.tick, 125)
                for _ in range(124):
                    run.step({})
                    self.assertTrue(fight.is_stunned(sym))
                    self.assertEqual(pool.total, 50)
                run.step({})
                self.assertFalse(fight.is_stunned(sym))
                before = health(run, bot).total
                x, y, z = bot.position
                run.step({sym: (*aim(fight._eye(sym), (x, y + 1, z)), 1)})
                self.assertLess(health(run, bot).total, before)

    def test_heavy_charge_close_impact_projected_barrier_prevents_damage_and_stun(self):
        run, sym, caster, bot = self.projected_scene("Heavy Assault", distance=8)
        fight = run.match.combat
        npc_control.start_charge(run.match, sym)
        for _ in range(100):
            run.step({})
            if bot.position[2] <= 3:
                break
        self.assertLessEqual(bot.position[2], 3)
        charge = heavy_charge.instance(bot)
        self.assertEqual(heavy_charge.phase(charge), 1)
        run.step({caster: (-16384, 0, 16)})
        _, _, barrier = fight.zarya_barriers.contact(sym)
        for _ in range(28):
            run.step({})
            self.assertFalse(fight.is_stunned(sym))
            self.assertEqual(health(run, sym).total, 225)
        self.assertEqual(heavy_charge.phase(charge), 0)
        self.assertAlmostEqual(barrier.remaining, 25)
        self.assertAlmostEqual(caster.body_script.component.entity_var(87).value(), 35)

    def test_heavy_charge_attaches_transports_and_releases_symmetra(self):
        definition = next(d for d in self.definitions.values() if d.name == "Heavy Assault")
        run, sym, bot, _ = self.create(definition, distance=8)
        fight = run.match.combat
        sym.mover.yaw, sym.mover.pitch = aim(fight._eye(sym), (0, 2, 8))
        npc_control.start_charge(run.match, sym)
        for _ in range(100):
            run.step({})
            if fight.pins.get(bot.entity) == sym.body:
                break
        self.assertEqual(fight.pins.get(bot.entity), sym.body)
        app = fight.heavy_charge_pins[bot.entity]
        self.assertEqual(app.graph.index, 0x1407)
        self.assertIs(app.component, sym.body_script.component)
        self.assertTrue(app.states[app.graph.node(1).state].active)
        self.assertTrue(fight.is_stunned(sym))
        self.assertEqual(health(run, sym).total, 225)
        before = health(run, bot).total
        sym_z, bot_z = sym.mover.position[2], bot.position[2]
        for _ in range(8):
            aim_bot = aim(fight._eye(sym), (0, 2, bot.position[2]))
            run.step({sym: (*aim_bot, 1)})
        self.assertAlmostEqual(sym.mover.position[2] - sym_z, bot.position[2] - bot_z, places=3)
        self.assertLess(sym.mover.position[2], sym_z)
        self.assertEqual(health(run, bot).total, before)
        npc_control.stop(run.match)
        run.step({})
        self.assertNotIn(bot.entity, fight.heavy_charge_pins)
        self.assertNotIn(app.id, app.component.instances)
        self.assertFalse(fight.is_stunned(sym))
        aim_bot = aim(fight._eye(sym), (0, 2, bot.position[2]))
        run.step({sym: (*aim_bot, 1)})
        self.assertLess(health(run, bot).total, before)

    def test_heavy_charge_projected_barrier_releases_active_capture(self):
        run, sym, caster, bot = self.projected_scene("Heavy Assault", distance=8)
        fight = run.match.combat
        npc_control.start_charge(run.match, sym)
        for _ in range(100):
            run.step({})
            if fight.pins.get(bot.entity) == sym.body:
                break
        self.assertEqual(fight.pins.get(bot.entity), sym.body)
        app = fight.heavy_charge_pins[bot.entity]
        self.assertEqual(health(run, sym).total, 225)
        run.step({caster: (*aim(fight._eye(caster), fight._eye(sym)), 16)})
        self.assertIsNotNone(fight.zarya_barriers.contact(sym))
        run.step({})
        self.assertFalse(fight.is_stunned(sym))
        self.assertNotIn(bot.entity, fight.heavy_charge_pins)
        self.assertNotIn(app.id, app.component.instances)
        self.assertEqual(heavy_charge.phase(heavy_charge.instance(bot)), 0)
        self.assertEqual(health(run, sym).total, 225)

    def test_heavy_charge_crushes_captured_symmetra_once_against_static_wall(self):
        definition = next(d for d in self.definitions.values() if d.name == "Heavy Assault")
        for lethal in (False, True):
            with self.subTest(lethal=lethal):
                run, sym, bot, _ = self.create(definition, distance=8)
                fight = run.match.combat
                pool = health(run, sym)
                if not lethal:
                    # Extend the fixture pool to measure the full authored damage.
                    pool.now[combat.HEALTH_POOL] = pool.most[combat.HEALTH_POOL] = 400
                    fight._sync_body_health(sym, pool)
                before = pool.total
                plane = -5
                cover = World([
                    (-10, 0, plane, 10, 0, plane, 10, 5, plane),
                    (-10, 0, plane, 10, 5, plane, -10, 5, plane),
                    (-10, 0, plane, 10, 5, plane, 10, 0, plane),
                    (-10, 0, plane, -10, 5, plane, 10, 5, plane),
                ])
                sym.mover.yaw, sym.mover.pitch = aim(fight._eye(sym), (0, 2, 8))
                npc_control.start_charge(run.match, sym)
                with patch.object(fight, "_collision", return_value=cover):
                    for _ in range(140):
                        run.step({})
                        if bot.entity in fight.heavy_charge_pins:
                            break
                    app = fight.heavy_charge_pins[bot.entity]
                    self.assertEqual(pool.total, before)
                    for _ in range(50):
                        run.step({})
                        if bot.entity not in fight.heavy_charge_pins:
                            break
                    self.assertNotIn(bot.entity, fight.heavy_charge_pins)
                    self.assertNotIn(app.id, app.component.instances)
                    self.assertFalse(fight.is_stunned(sym))
                    self.assertGreater(bot.position[2], plane)
                    self.assertFalse(heavy_charge.phase(heavy_charge.instance(bot)))
                    self.assertEqual(pool.dead, lethal)
                    if lethal:
                        self.assertEqual(pool.killer, bot.entity)
                    self.assertAlmostEqual(pool.total, max(0, before - 300))
                    self.assertEqual(pool.now[combat.SHIELDS_POOL], 0)
                    for _ in range(10):
                        run.step({})
                    self.assertAlmostEqual(pool.total, max(0, before - 300))

    def test_heavy_charge_projected_barrier_releases_capture_before_wall_crush(self):
        run, sym, caster, bot = self.projected_scene("Heavy Assault", distance=8)
        fight = run.match.combat
        npc_control.start_charge(run.match, sym)
        plane = -5
        cover = World([
            (-10, 0, plane, 10, 0, plane, 10, 5, plane),
            (-10, 0, plane, 10, 5, plane, -10, 5, plane),
            (-10, 0, plane, 10, 5, plane, 10, 0, plane),
            (-10, 0, plane, -10, 5, plane, 10, 5, plane),
        ])
        with patch.object(fight, "_collision", return_value=cover):
            for _ in range(130):
                run.step({})
                if bot.entity in fight.heavy_charge_pins and bot.position[2] < -2:
                    break
            self.assertLess(bot.position[2], -2)
            app = fight.heavy_charge_pins[bot.entity]
            run.step({caster: (*aim(fight._eye(caster), fight._eye(sym)), 16)})
            _, _, barrier = fight.zarya_barriers.contact(sym)
            for _ in range(25):
                run.step({})
            self.assertNotIn(bot.entity, fight.heavy_charge_pins)
            self.assertNotIn(app.id, app.component.instances)
            self.assertFalse(fight.is_stunned(sym))
            self.assertEqual(health(run, sym).total, 225)
            self.assertEqual(barrier.remaining, 200)
            self.assertGreater(bot.position[2], plane)

    def test_heavy_charge_retained_world_gap_matches_capture_travel_and_wall_hit(self):
        definition = next(d for d in self.definitions.values() if d.name == "Heavy Assault")
        results = []
        for skip_ticks in (False, True):
            run, sym, bot, _ = self.create(definition, distance=8)
            fight = run.match.combat
            plane = -5
            cover = World([
                (-10, 0, plane, 10, 0, plane, 10, 5, plane),
                (-10, 0, plane, 10, 5, plane, -10, 5, plane),
                (-10, 0, plane, 10, 5, plane, 10, 0, plane),
                (-10, 0, plane, -10, 5, plane, 10, 5, plane),
            ])
            sym.mover.yaw, sym.mover.pitch = aim(fight._eye(sym), (0, 2, 8))
            npc_control.start_charge(run.match, sym)
            with patch.object(fight, "_collision", return_value=cover):
                for _ in range(120):
                    run.step({})
                    if bot.entity in fight.heavy_charge_pins:
                        break
                self.assertIn(bot.entity, fight.heavy_charge_pins)
                start = bot.position[2]
                trace = []
                for frames in (6, 45):
                    if skip_ticks:
                        run.frame += frames
                        run.match.update(run.frame * combat.TICK, run.frame)
                    else:
                        for _ in range(frames):
                            run.step({})
                    pool = health(run, sym)
                    trace.append((bot.position[2], pool.total, pool.death_tick,
                                  bot.entity in fight.heavy_charge_pins))
                self.assertAlmostEqual(start - trace[0][0], 6 * 15 * combat.TICK)
                self.assertTrue(trace[0][3])
                self.assertFalse(trace[1][3])
                self.assertEqual(trace[1][1], 0)
                self.assertEqual(pool.killer, bot.entity)
                results.append(trace)
        self.assertEqual(*results)

    def test_heavy_charge_crushes_captured_symmetra_on_live_cryo_cover(self):
        definition = next(d for d in self.definitions.values() if d.name == "Heavy Assault")
        run, sym, bot, _ = self.create(definition, distance=8)
        mei = run.players[1]
        self.assertTrue(run.match.switch_hero(mei, heroes()[cryo.HERO]))
        fixture = barrier_tests.PhotonBarrierTests()
        fixture.move(mei, (3, 1, -5))
        mei.team = sym.team
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        fight = run.match.combat
        sym.mover.yaw, sym.mover.pitch = aim(fight._eye(sym), (0, 2, 8))
        npc_control.start_charge(run.match, sym)
        for _ in range(120):
            run.step({})
            if bot.entity in fight.heavy_charge_pins:
                break
        app = fight.heavy_charge_pins[bot.entity]
        pool = health(run, sym)
        self.assertEqual(pool.total, 225)
        fixture.move(mei, (0, 1, -5))
        run.step({mei: (0, 0, 8)})
        self.assertTrue(cryo.active(mei))
        self.assertEqual(pool.total, 225)
        for _ in range(50):
            run.step({})
            if bot.entity not in fight.heavy_charge_pins:
                break
        self.assertTrue(cryo.active(mei))
        self.assertEqual(health(run, mei).total, 250)
        self.assertFalse(fight.is_stunned(mei))
        self.assertGreater(bot.position[2], -5)
        self.assertEqual(heavy_charge.phase(heavy_charge.instance(bot)), 0)
        self.assertEqual(pool.killer, bot.entity)
        self.assertTrue(pool.dead)
        self.assertNotIn(bot.entity, fight.pins)
        self.assertNotIn(app.id, app.component.instances)

    def test_heavy_charge_crushes_on_deployed_ice_wall_but_not_after_cancel(self):
        definition = next(d for d in self.definitions.values() if d.name == "Heavy Assault")
        for cancel in (False, True):
            with self.subTest(cancel=cancel):
                run, sym, bot, _ = self.create(definition, distance=8)
                mei = run.players[1]
                self.assertTrue(run.match.switch_hero(mei, heroes()[cryo.HERO]))
                barrier_tests.PhotonBarrierTests().move(mei, (3, 1, -8))
                mei.team = sym.team
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                fight = run.match.combat
                angles = aim(fight._eye(mei), (0, 1, -5))
                for button in (16, 0, 1, 0):
                    run.step({mei: (*angles, button)})
                pieces = tuple(fight.ice_wall.pieces.values())
                self.assertEqual(len(pieces), 5)
                sym.mover.yaw, sym.mover.pitch = aim(fight._eye(sym), (0, 2, 8))
                npc_control.start_charge(run.match, sym)
                for _ in range(120):
                    run.step({})
                    if bot.entity in fight.heavy_charge_pins:
                        break
                app = fight.heavy_charge_pins[bot.entity]
                pool = health(run, sym)
                self.assertEqual(pool.total, 225)
                if cancel:
                    run.step({mei: (*angles, 16)})
                    self.assertFalse(fight.ice_wall.pieces)
                for _ in range(45):
                    run.step({})
                    if pool.dead or (cancel and bot.position[2] < -6):
                        break
                if cancel:
                    self.assertLess(bot.position[2], -6)
                    self.assertEqual(pool.total, 225)
                else:
                    self.assertGreater(bot.position[2], -5)
                    self.assertTrue(pool.dead)
                    self.assertEqual(pool.killer, bot.entity)
                    self.assertNotIn(bot.entity, fight.pins)
                    self.assertNotIn(app.id, app.component.instances)
                    self.assertEqual(tuple(fight.ice_wall.pieces.values()), pieces)
                    self.assertTrue(all(piece.health == 400 for piece in pieces))
                self.assertEqual(health(run, mei).total, 250)

    def test_primary_commands_remove_actual_enemy_pools_for_every_npc(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                run, player, bot, _target = self.create(definition)
                before = health(run, bot).total
                fire(run, player, 4)
                self.assertAlmostEqual(before - health(run, bot).total, 4 * 60 * .016, places=4)

    def test_actual_primary_credits_resolved_damage_for_all_five(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                run, player, bot, _target = self.create(definition)
                component = player.body_script.component
                component.set(12918, 0)
                before = health(run, bot).total
                fire(run, player, 12)
                loss = before - health(run, bot).total
                self.assertGreater(loss, 0)
                self.assertAlmostEqual(component.entity_var(342).value(), loss, places=4)

    def concussion_scene(self, definition):
        run, sym, bot, _ = self.create(definition, distance=5)
        pharah = run.players[1]
        self.assertTrue(run.match.switch_hero(pharah, heroes()[barrier_tests.PHARAH]))
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        pharah.team = sym.team
        pharah.mover.position[:] = (0, 1, 0)
        sym.mover.position[:] = (2, 1, 0)
        for _ in range(75):
            run.step({})
        return run, sym, pharah, bot

    def launch_npc_concussion(self, run, pharah, bot):
        fight = run.match.combat
        x, y, z = bot.position
        aim_at_body = aim(fight._eye(pharah), (x, y + 1, z))
        for _ in range(12):
            run.step({pharah: (*aim_at_body, 16)})
            if fight.rockets:
                break
        [rocket] = fight.rockets
        self.assertEqual(rocket.definition, 0x040000000000010F)
        self.assertIs(rocket.shooter, pharah)
        return rocket

    @patch.object(FakeClient, "bot_stream", new=lambda _client, _body: None, create=True)
    def test_actual_concussion_and_symmetra_beam_against_all_five_manual_npcs(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                run, sym, pharah, bot = self.concussion_scene(definition)
                fight = run.match.combat
                initial, pool = bot.position, health(run, bot)
                before = pool.total
                shot = self.launch_npc_concussion(run, pharah, bot)
                peak = initial[1]
                with patch.object(bot, "apply_impulse", wraps=bot.apply_impulse) as apply:
                    for _ in range(90):
                        run.step({})
                        peak = max(peak, bot.position[1])
                        if bot.impulse_mover is not None:
                            wire = bot.movement(run.frame)
                            self.assertEqual((wire.position, wire.velocity), (bot.position, bot.velocity))
                            self.assertEqual(wire.air_ticks, bot.impulse_mover.air_ticks)
                apply.assert_called_once()
                vx, vy, vz = apply.call_args.args[0]
                self.assertAlmostEqual(math.hypot(vx, vz), 20)
                self.assertEqual(vy, 5)
                self.assertNotIn(shot, fight.rockets)
                self.assertEqual(pool.total, before)
                self.assertEqual(pharah.stats.value(stats.HERO_DAMAGE), 0)
                if definition.name == "Heavy Assault":
                    self.assertEqual(bot.position, initial)
                else:
                    self.assertGreater(peak, initial[1] + .2)
                    self.assertGreater(bot.position[2], initial[2] + 1)
                self.assertIsNone(bot.impulse_mover)
                x, y, z = bot.position
                distance = 1.5 if definition.name == "Assassin" else 4
                sym.mover.position[:] = (x, y, z - distance)
                sym.mover.body = None
                direction = aim(fight._eye(sym), (x, y + 1, z))
                sym.body_script.component.set(12918, 0)
                charge_before = charge(sym)
                for _ in range(12):
                    run.step({sym: (*direction, 1)})
                loss = before - pool.total
                self.assertGreater(loss, 0)
                self.assertAlmostEqual(charge(sym) - charge_before, loss, places=4)
                self.npc_hit_and_stop(run, sym, bot)

    @patch.object(FakeClient, "bot_stream", new=lambda _client, _body: None, create=True)
    def test_orb_uses_all_five_npc_concussion_poses_in_omitted_ticks(self):
        for definition in self.definitions.values():
            for concussion_first in (True, False):
                with self.subTest(npc=definition.name, concussion_first=concussion_first):
                    stepped = self.orb_across_npc_concussion(definition, concussion_first, gap=False)
                    skipped = self.orb_across_npc_concussion(definition, concussion_first, gap=True)
                    self.assertEqual(skipped, stepped)

    def orb_across_npc_concussion(self, definition, concussion_first, *, gap):
        run, sym, pharah, bot = self.concussion_scene(definition)
        fight = run.match.combat
        if not concussion_first:
            pharah.mover.position[:] = (0, 1, -13)
            pharah.mover.body = None
        initial_position = bot.position
        initial_health = health(run, bot).total
        x, y, z = initial_position
        aim_sym = aim(fight._eye(sym), (x, y + 1, z))
        aim_pharah = aim(fight._eye(pharah), (x, y + 1, z))
        for _ in range(90):
            run.step({sym: (*aim_sym, 2)})
        for _ in range(12):
            run.step({sym: (*aim_sym, 2), pharah: (*aim_pharah, 16)})
            if fight.rockets:
                break
        [concussion] = fight.rockets
        self.assertEqual(concussion.definition, 0x040000000000010F)
        run.step({sym: (*aim_sym, 0)})
        run.step({})
        self.assertEqual(len(fight.rockets), 2)
        orb = next(rocket for rocket in fight.rockets if rocket.shooter is sym)
        start = run.frame
        self.assertEqual((concussion.last_tick, orb.last_tick), (start, start))
        self.assertIsNone(bot.impulse_mover)
        self.assertEqual(bot.position, initial_position)
        starting_charge = charge(sym)
        end = start + 180
        with patch.object(bot, "apply_impulse", wraps=bot.apply_impulse) as apply:
            for tick in (end,) if gap else range(start + 1, end + 1):
                run.match.update(tick * combat.TICK, tick)
        apply.assert_called_once()
        shove_tick = apply.call_args.args[1]
        self.assertLess(shove_tick, end - combat.COMBAT_CATCH_UP + 1)
        self.assertFalse(fight.rockets)
        self.assertIsNone(bot.impulse_mover)
        self.assertEqual(pharah.stats.value(stats.HERO_DAMAGE), 0)
        anchored = definition.name == "Heavy Assault"
        if anchored:
            self.assertEqual(bot.position, initial_position)
        else:
            self.assertGreater(bot.position[2], initial_position[2] + 1)
        damage = initial_health - health(run, bot).total
        expect_hit = not concussion_first or anchored
        self.assertEqual(damage, 120 if expect_hit else 0)
        self.assertEqual(sym.stats.value(stats.HERO_DAMAGE), damage)
        gained_charge = charge(sym) - starting_charge
        self.assertLessEqual(damage + 10, gained_charge)
        self.assertLessEqual(gained_charge, damage + 15)
        self.assertLess(orb.last_tick, end - combat.COMBAT_CATCH_UP + 1)
        if expect_hit:
            self.assertEqual(shove_tick < orb.last_tick, concussion_first)
        else:
            self.assertIsNone(health(run, bot).last_damage_at)
        return bot.position, bot.velocity, shove_tick, orb.last_tick, damage, gained_charge

    @patch.object(FakeClient, "bot_stream", new=lambda _client, _body: None, create=True)
    def test_orb_uses_all_five_npc_route_poses_at_contact(self):
        for definition in self.definitions.values():
            for entering in (False, True):
                for move_early in (False, True):
                    with self.subTest(npc=definition.name, entering=entering, move_early=move_early):
                        args = (definition, entering, move_early)
                        stepped = self.orb_across_npc_route(*args, gap=False)
                        self.assertEqual(self.orb_across_npc_route(*args, gap=True), stepped)

    def orb_across_npc_route(self, definition, entering, move_early, *, gap):
        run, sym, bot, _ = self.create(definition, distance=12)
        fight = run.match.combat
        bot.position = (1.5 if entering else 0.0, 1.0, 12.0)
        bot.route = (bot.position,)
        bot.wait = 5.0
        angles = aim(fight._eye(sym), (0, 1.7, 12))
        starting_pool = health(run, bot).total
        for _ in range(90):
            run.step({sym: (*angles, 2)})
        run.step({sym: (*angles, 0)})
        run.step({})
        [orb] = fight.rockets
        start = run.frame
        self.assertEqual(orb.last_tick, start)
        self.assertEqual(bot.last_tick, start)
        start_charge = charge(sym)
        bot.route = ((0.0 if entering else 30.0, 1.0, 12.0),)
        bot.wait = 0.0 if move_early else 0.75
        end = start + 180
        for tick in (end,) if gap else range(start + 1, end + 1):
            run.match.update(tick * combat.TICK, tick)
        self.assertFalse(fight.rockets)
        self.assertLess(orb.last_tick, end - combat.COMBAT_CATCH_UP + 1)
        if entering:
            self.assertAlmostEqual(bot.position[0], 0, places=3)
        else:
            self.assertGreater(bot.position[0], 3)
        vitals = health(run, bot)
        damage = starting_pool - vitals.total
        self.assertEqual(damage, 120 if entering == move_early else 0)
        self.assertEqual(sym.stats.value(stats.HERO_DAMAGE), damage)
        charge_gain = charge(sym) - start_charge
        self.assertLessEqual(damage + 10, charge_gain)
        self.assertLessEqual(charge_gain, damage + 15)
        if damage:
            self.assertAlmostEqual(vitals.last_damage_at, orb.last_tick * combat.TICK)
        else:
            self.assertIsNone(vitals.last_damage_at)
        return orb.last_tick, damage, charge_gain, bot.position, bot.velocity

    @patch.object(FakeClient, "bot_stream", new=lambda _client, _body: None, create=True)
    def test_npc_route_resumes_at_ice_expiry_inside_omitted_orb_history(self):
        for definition in self.definitions.values():
            for early in (True, False):
                with self.subTest(npc=definition.name, early_expiry=early):
                    stepped = self.orb_and_route_across_ice_expiry(definition, early, gap=False)
                    skipped = self.orb_and_route_across_ice_expiry(definition, early, gap=True)
                    self.assertEqual(skipped, stepped)

    def orb_and_route_across_ice_expiry(self, definition, early, *, gap):
        run, sym, bot, _ = self.create(definition, distance=28)
        fight = run.match.combat
        mei = run.players[1]
        self.assertTrue(run.match.switch_hero(mei, heroes()[cryo.HERO]))
        mei.mover.position[:] = (7, 1, 28)
        bot.position = (3.3, 1, 28)
        for _ in range(25):
            run.step({})
        wall_aim = aim(fight._eye(mei), (1, 1, 28))
        for button in (16, 0, 1, 0):
            run.step({mei: (*wall_aim, button)})
        pieces = tuple(fight.ice_wall.pieces.values())
        self.assertEqual(len(pieces), 5)
        center = min(pieces, key=lambda piece: abs(piece.feet[2] - 28))
        self.assertAlmostEqual(center.feet[0], 1.0, places=2)
        bot.route = ((0.0, 1.0, 28.0),)
        bot.wait = 0.0
        start = center.expires - (1 if early else 150)
        while run.frame < start - 92:
            run.step({})
        angles = aim(fight._eye(sym), (0, 1.3, 28))
        initial_health = health(run, bot).total
        for _ in range(90):
            run.step({sym: (*angles, 2)})
        run.step({sym: (*angles, 0)})
        run.step({})
        self.assertEqual(run.frame, start)
        self.assertEqual(bot.velocity, (0, 0, 0))
        self.assertGreater(bot.position[0], center.feet[0] + center.depth)
        [orb] = fight.rockets
        self.assertEqual(orb.last_tick, start)
        self.assertIsNone(fight.ice_wall.cast(orb.position, orb.direction, 100))
        initial_charge = charge(sym)
        end = start + 240
        movement_ticks = []
        step_route_tick = bot._tick

        def observe_route_tick(tick, *args):
            before_x = bot.position[0]
            step_route_tick(tick, *args)
            if before_x - bot.position[0] > 1e-6:
                movement_ticks.append(tick)

        with patch.object(bot, "_tick", side_effect=observe_route_tick):
            for tick in (end,) if gap else range(start + 1, end + 1):
                run.match.update(tick * combat.TICK, tick)
        self.assertTrue(movement_ticks)
        self.assertEqual(movement_ticks[0], center.expires)
        self.assertFalse(fight.ice_wall.pieces)
        self.assertTrue(all(piece.health == 400 for piece in pieces))
        self.assertFalse(fight.rockets)
        self.assertAlmostEqual(bot.position[0], 0, places=3)
        self.assertEqual(bot.position[2], 28)
        damage = initial_health - health(run, bot).total
        self.assertEqual(damage, 120 if early else 0)
        self.assertEqual(sym.stats.value(stats.HERO_DAMAGE), damage)
        self.assertEqual(mei.stats.value(stats.HERO_DAMAGE), 0)
        self.assertEqual(center.expires < orb.last_tick, early)
        self.assertLess(orb.last_tick, end - combat.COMBAT_CATCH_UP + 1)
        charge_gain = charge(sym) - initial_charge
        self.assertLessEqual(damage + 15, charge_gain)
        self.assertLessEqual(charge_gain, damage + 20)
        return (
            center.expires, movement_ticks[0], orb.last_tick, damage, charge_gain, bot.position, bot.velocity,
        )

    def test_retained_concussion_and_npc_flight_match_stepped_positions(self):
        definition = next(d for d in self.definitions.values() if d.name == "Trooper")
        results = []
        for gap in (False, True):
            with self.subTest(gap=gap):
                run, _, pharah, bot = self.concussion_scene(definition)
                shot = self.launch_npc_concussion(run, pharah, bot)
                fight = run.match.combat
                run.frame += 1
                run.match.update(run.frame * combat.TICK, run.frame)
                self.assertEqual(shot.last_tick, run.frame)
                end = run.frame + 26
                for tick in (end,) if gap else range(run.frame + 1, end + 1):
                    run.match.update(tick * combat.TICK, tick)
                self.assertNotIn(shot, fight.rockets)
                self.assertGreater(bot.position[2], 6)
                results.append((bot.position, bot.velocity, health(run, bot).total))
        self.assertEqual(*results)

    @unittest.skipUnless(os.environ.get("ORPHEUS_EXTRA_GRAPH_BUNDLES"), "requires summon graph shard")
    @patch.object(FakeClient, "bot_stream", new=lambda _client, _body: None, create=True)
    def test_concussion_crosses_actual_sentry_range_in_stepped_and_retained_updates(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                stepped = self.sentry_concussion_at_range(definition, entering=False, gap=False)
                retained = self.sentry_concussion_at_range(definition, entering=False, gap=True)
                self.assertEqual(retained, stepped)

    @unittest.skipUnless(os.environ.get("ORPHEUS_EXTRA_GRAPH_BUNDLES"), "requires summon graph shard")
    @patch.object(FakeClient, "bot_stream", new=lambda _client, _body: None, create=True)
    def test_concussion_into_actual_sentry_range_starts_slow_damage_and_charge(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                stepped = self.sentry_concussion_at_range(definition, entering=True, gap=False)
                retained = self.sentry_concussion_at_range(definition, entering=True, gap=True)
                self.assertEqual(retained, stepped)

    @unittest.skipUnless(os.environ.get("ORPHEUS_EXTRA_GRAPH_BUNDLES"), "requires summon graph shard")
    @patch.object(FakeClient, "bot_stream", new=lambda _client, _body: None, create=True)
    def test_concussion_moves_npcs_behind_cover_for_only_one_of_two_sentries(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                stepped = self.sentry_concussion_around_cover(definition, gap=False)
                retained = self.sentry_concussion_around_cover(definition, gap=True)
                self.assertEqual(retained, stepped)

    @unittest.skipUnless(os.environ.get("ORPHEUS_EXTRA_GRAPH_BUNDLES"), "requires summon graph shard")
    @patch.object(FakeClient, "bot_stream", new=lambda _client, _body: None, create=True)
    def test_concussion_occlusion_keeps_two_sentry_owners_charge_independent(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                stepped = self.sentry_concussion_around_cover(definition, gap=False, mode="split")
                retained = self.sentry_concussion_around_cover(definition, gap=True, mode="split")
                self.assertEqual(retained, stepped)

    @unittest.skipUnless(os.environ.get("ORPHEUS_EXTRA_GRAPH_BUNDLES"), "requires summon graph shard")
    @patch.object(FakeClient, "bot_stream", new=lambda _client, _body: None, create=True)
    def test_dead_sentry_owner_keeps_beam_but_does_not_receive_charge_during_concussion(self):
        definition = next(d for d in self.definitions.values() if d.name == "Trooper")
        stepped = self.sentry_concussion_around_cover(definition, gap=False, mode="dead_left")
        retained = self.sentry_concussion_around_cover(definition, gap=True, mode="dead_left")
        self.assertEqual(retained, stepped)

    @unittest.skipUnless(os.environ.get("ORPHEUS_EXTRA_GRAPH_BUNDLES"), "requires summon graph shard")
    @patch.object(FakeClient, "bot_stream", new=lambda _client, _body: None, create=True)
    def test_live_sentry_credits_new_symmetra_body_after_retained_respawn(self):
        stepped = self.sentry_across_owner_respawn(gap=False)
        self.assertEqual(self.sentry_across_owner_respawn(gap=True), stepped)

    @unittest.skipUnless(os.environ.get("ORPHEUS_EXTRA_GRAPH_BUNDLES"), "requires summon graph shard")
    @patch.object(FakeClient, "bot_stream", new=lambda _client, _body: None, create=True)
    def test_live_sentry_credits_every_retained_tick_when_owner_respawn_precedes_window(self):
        for tail in (combat.COMBAT_CATCH_UP + 3, 2 * combat.COMBAT_CATCH_UP + 4):
            with self.subTest(post_respawn_ticks=tail):
                self.sentry_across_owner_respawn(gap=True, tail=tail)

    @unittest.skipUnless(os.environ.get("ORPHEUS_EXTRA_GRAPH_BUNDLES"), "requires summon graph shard")
    @patch.object(FakeClient, "bot_stream", new=lambda _client, _body: None, create=True)
    def test_live_sentry_credits_owner_after_prewindow_allocation_retries(self):
        for tail in (40, 55, combat.COMBAT_CATCH_UP - 1, combat.COMBAT_CATCH_UP + 3):
            with self.subTest(post_respawn_ticks=tail):
                self.sentry_across_owner_respawn(gap=True, tail=tail, failed_allocations=2)

    @unittest.skipUnless(os.environ.get("ORPHEUS_EXTRA_GRAPH_BUNDLES"), "requires summon graph shard")
    @patch.object(FakeClient, "bot_stream", new=lambda _client, _body: None, create=True)
    def test_live_sentry_survives_allocation_failures_across_updates_and_wall_expiry(self):
        for tail in (35, 63, 92):
            with self.subTest(post_respawn_ticks=tail):
                self.sentry_across_owner_respawn(gap=True, tail=tail, failed_allocations=2, photon_wall=True)

    def sentry_across_owner_respawn(self, *, gap, tail=7, failed_allocations=0, photon_wall=False):
        from test_symmetra_turrets import wall

        definition = next(d for d in self.definitions.values() if d.name == "Heavy Assault")
        if photon_wall:
            fixture = barrier_tests.PhotonBarrierTests()
            run, sym, killer, barrier = fixture.deploy()
            bot = npc.spawn(run.match, definition, (2, 0, 1), team=1 - sym.team)
            sym.body_script.ult_seconds = 60
            fixture.move(killer, (0, 0, -10))
        else:
            run, sym, bot, _ = self.create(definition)
            killer = run.players[1]
            bot.position = (2, 1, 1)
            killer.mover.position[:] = (0, 1, -10)
        killer.team = bot.team
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        fight = run.match.combat
        sym.body_script.component.set(12918, 0)
        with patch.object(fight, "_collision", return_value=wall()):
            fire(run, sym, 40, buttons=8)
            fire(run, sym, 50, buttons=0)
            [sentry] = fight.sentries.sentries
            self.assertEqual(sentry.target, bot.entity)
            aura = sentry.slow[1]
            old_body = sym.body
            old_script = sym.body_script
            pool = health(run, sym)
            pool.now[combat.HEALTH_POOL] = 1
            pool.now[combat.SHIELDS_POOL] = 0
            angles = aim(fight._eye(killer), (0, sym.mover.position[1] + 1.2, 0))
            for _ in range(15):
                run.step({killer: (*angles, 1)})
                if fight.is_dead(sym):
                    break
            self.assertTrue(fight.is_dead(sym))
            self.assertEqual(pool.killer, killer.body)
            saved = charge(sym)
            if photon_wall:
                self.assertEqual(saved, 0)
                self.assertIn(barrier, fight.barriers.walls)
            else:
                self.assertGreater(saved, 0)
            [deadline] = [when for when, player in fight.respawns if player is sym]
            respawn = math.ceil(deadline / combat.TICK)
            while respawn * combat.TICK < deadline:
                respawn += 1
            run.frame = respawn - 3
            run.match.update(run.frame * combat.TICK, run.frame)
            self.assertEqual(sym.body, old_body)
            self.assertEqual(charge(sym), saved)
            retry_ticks = math.ceil(combat.RESPAWN_RETRY / combat.TICK)
            expected_attempts = [respawn + retry_ticks * index for index in range(failed_allocations + 1)]
            respawn = expected_attempts[-1]
            charge_start = respawn + 1
            if photon_wall:
                expiry = math.ceil(barrier.expires_at / combat.TICK)
                self.assertLess(respawn + 1, expiry)
                self.assertLess(expiry, respawn + 28)
                charge_start = expiry
            attempts = []
            allocate = run.match._next_body_id

            def next_body_id(player, *, at):
                self.assertIs(player, sym)
                self.assertEqual(player.body, old_body)
                self.assertTrue(fight.is_dead(player))
                self.assertEqual(charge(player), saved)
                self.assertIs(sentry.slow[1], aura)
                attempts.append(fight.tick)
                return allocate(player, at=at)

            target_pool = health(run, bot)
            before = target_pool.total
            endpoint = respawn + tail
            tick_ms = round(combat.TICK * 1000)
            previous = expected_attempts[-2] + 1 if photon_wall else run.frame
            first = max(previous + 1, endpoint - combat.COMBAT_CATCH_UP + 1)
            first_credit = max(first, charge_start)
            observed = []
            update = fight.sentries.update

            def observe(*args, **kwargs):
                update(*args, **kwargs)
                if photon_wall:
                    self.assertEqual(barrier in fight.barriers.walls, fight.tick < expiry)
                observed.append((fight.tick, sym.body == old_body))
                hits = max(0, fight.tick - first_credit + 1)
                passive = 5 * (max(0, fight.tick - charge_start) * tick_ms // 1000)
                self.assertAlmostEqual(charge(sym) - saved, hits * .64 + passive, places=4)

            with (
                patch.object(fight.sentries, "update", side_effect=observe),
                patch.object(run.match, "_next_body_id", side_effect=next_body_id),
                patch.object(sym.client, "body_id_available",
                             side_effect=lambda _entity, *, at: len(attempts) > failed_allocations),
            ):
                if photon_wall:
                    for index, attempt in enumerate(expected_attempts[:-1]):
                        checkpoint = attempt + 1
                        run.match.update(checkpoint * combat.TICK, checkpoint)
                        self.assertEqual(attempts, expected_attempts[:index + 1])
                        self.assertEqual(sym.body, old_body)
                        self.assertEqual(charge(sym), saved)
                        self.assertIn(barrier, fight.barriers.walls)
                        self.assertIs(sentry.slow[1], aura)
                        [due] = [when for when, player in fight.respawns if player is sym]
                        self.assertAlmostEqual(due, attempt * combat.TICK + combat.RESPAWN_RETRY)
                    self.assertEqual(observed, [(tick, True) for tick in range(run.frame + 1, previous + 1)])
                    self.assertAlmostEqual(before - target_pool.total, (previous - run.frame) * .64)
                    observed.clear()
                    before = target_pool.total
                for tick in (endpoint,) if gap else range(run.frame + 1, endpoint + 1):
                    run.match.update(tick * combat.TICK, tick)
            self.assertNotEqual(sym.body, old_body)
            self.assertEqual(attempts, expected_attempts)
            self.assertFalse(any(player is sym for _, player in fight.respawns))
            self.assertTrue(old_script.retired)
            self.assertEqual(fight.replacement_ticks[sym.body], respawn)
            passive = 5 * ((endpoint - charge_start + 1) * tick_ms // 1000)
            earned = (endpoint - first_credit + 1) * .64
            self.assertAlmostEqual(charge(sym) - saved, earned + passive, places=4)
            self.assertEqual(sym.body_script.charge_clock,
                             (endpoint, sym.body_script.component.now + tail * tick_ms))
            self.assertAlmostEqual(before - target_pool.total, (endpoint - first + 1) * .64)
            self.assertEqual(observed, [(tick, tick <= respawn)
                                        for tick in range(first, endpoint + 1)])
            self.assertEqual(old_script.component.entity_var(342).value(), saved)
            self.assertIs(sentry.owner, sym)
            self.assertIs(sentry.slow[1], aura)
            if photon_wall:
                self.assertNotIn(barrier, fight.barriers.walls)
            return observed, charge(sym), target_pool.total

    def sentry_concussion_around_cover(self, definition, *, gap, mode="shared"):
        run, sym, bot, _ = self.create(definition)
        pharah = run.players[1]
        self.assertTrue(run.match.switch_hero(pharah, heroes()[barrier_tests.PHARAH]))
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        pharah.team = sym.team
        pharah.mover.position[:] = (-2, 1, 1)
        bot.position = (2, 1, 1)
        world = World([
            (-25, 1, -25, -25, 1, 25, 25, 1, 25),
            (-25, 1, -25, 25, 1, 25, 25, 1, -25),
            (-10, -5, 3, 10, -5, 3, 10, 8, 3),
            (-10, -5, 3, 10, 8, 3, -10, 8, 3),
            (3, -5, 1.6, 3, 8, 1.6, 3, 8, 1.9),
            (3, -5, 1.6, 3, 8, 1.9, 3, -5, 1.9),
        ])
        run.match.collision_world = world
        fight = run.match.combat
        fixture = barrier_tests.PhotonBarrierTests()
        right_owner = sym
        if mode != "shared":
            right_owner = fixture.add_target(run, 3, (3, 1, 0))
            self.assertTrue(run.match.switch_hero(right_owner, sym.hero))
            right_owner.team = sym.team
        owners = tuple(dict.fromkeys((sym, right_owner)))
        for owner in owners:
            owner.body_script.component.set(12918, 0)
        for owner, x in ((sym, 0), (right_owner, 3)):
            owner.mover.position[:] = (x, 1, 0)
            owner.mover.body = None
            fire(run, owner, 40, buttons=8)
            fire(run, owner, 50, buttons=0)
        left, right = fight.sentries.sentries
        self.assertIs(left.owner, sym)
        self.assertIs(right.owner, right_owner)
        self.assertAlmostEqual(left.bot.position[0], 0)
        self.assertAlmostEqual(right.bot.position[0], 3)
        self.assertEqual((left.target, right.target), (bot.entity, bot.entity))
        left_aura, right_aura = left.slow[1], right.slow[1]
        beam = next(i for i in left.bot.body_script.component.instances.values() if i.graph.index == 0x1590)
        beam_range = beam.find_var(graph.INSTANCE, 1648).value()
        component = bot.body_script.component
        slow = component.entity_var(23186)
        self.assertAlmostEqual(slow.value(), .4)
        shooter = None
        if mode == "dead_left":
            shooter = fixture.add_target(run, 4, (0, 1, -10))
            shooter.team = 1 - sym.team
            owner_pool = health(run, sym)
            owner_pool.now[combat.HEALTH_POOL] = 1
            owner_pool.now[combat.SHIELDS_POOL] = 0
        shot = self.launch_npc_concussion(run, pharah, bot)
        if shooter is not None:
            angles = aim(fight._eye(shooter), (0, 2.2, 0))
            run.step({shooter: (*angles, 2)})
            [helix] = [rocket for rocket in fight.rockets if rocket.shooter is shooter]
        run.frame += 1
        run.match.update(run.frame * combat.TICK, run.frame)
        self.assertEqual(shot.last_tick, run.frame)
        if shooter is not None:
            self.assertEqual(helix.last_tick, run.frame)
            self.assertFalse(fight.is_dead(sym))

        pool = health(run, bot)
        last_pool = pool.total
        last_charge = {owner: charge(owner) for owner in owners}
        last_damage = {owner: owner.stats.value(stats.HERO_DAMAGE) for owner in owners}
        ticks = []
        update = fight.sentries.update

        def observe(*args, **kwargs):
            nonlocal last_pool
            update(*args, **kwargs)
            target = next(target for target in fight._targets() if target.owner is bot)
            direction, reach = combat.capsule_ray(left.bot.position, target)
            self.assertLess(reach, beam_range)
            blocked = world.cast_ray(left.bot.position, direction, reach) is not None
            direction, reach = combat.capsule_ray(right.bot.position, target)
            self.assertLess(reach, beam_range)
            self.assertIsNone(world.cast_ray(right.bot.position, direction, reach))
            self.assertEqual(left.target, None if blocked else bot.entity)
            self.assertEqual(right.target, bot.entity)
            self.assertIs(right.slow[1], right_aura)
            self.assertAlmostEqual(slow.value(), .2 if blocked else .4)
            self.assertAlmostEqual(last_pool - pool.total, .64 if blocked else 1.28)
            last_pool = pool.total
            for owner in owners:
                expected = .64 * ((owner is sym and not blocked) + (owner is right_owner))
                current_damage = owner.stats.value(stats.HERO_DAMAGE)
                self.assertAlmostEqual(current_damage - last_damage[owner], expected)
                current_charge = charge(owner)
                self.assertAlmostEqual(current_charge - last_charge[owner],
                                       0 if fight.is_dead(owner) else expected, places=4)
                last_damage[owner], last_charge[owner] = current_damage, current_charge
            if blocked:
                self.assertIsNone(left.slow)
                self.assertNotIn(left_aura.id, component.instances)
            ticks.append((fight.tick, blocked, fight.is_dead(sym)))

        endpoint = run.frame + 25
        with patch.object(fight.sentries, "update", side_effect=observe):
            for tick in (endpoint,) if gap else range(run.frame + 1, endpoint + 1):
                run.match.update(tick * combat.TICK, tick)
        self.assertNotIn(shot, fight.rockets)
        self.assertEqual([tick for tick, _, _ in ticks], list(range(run.frame + 1, endpoint + 1)))
        blocked_ticks = [blocked for _, blocked, _ in ticks]
        self.assertFalse(blocked_ticks[0])
        self.assertFalse(blocked_ticks[-1])
        if definition.name == "Heavy Assault":
            self.assertNotIn(True, blocked_ticks)
            self.assertIs(left.slow[1], left_aura)
        else:
            self.assertIn(True, blocked_ticks)
            first = blocked_ticks.index(True)
            last = len(blocked_ticks) - 1 - blocked_ticks[::-1].index(True)
            self.assertTrue(all(blocked_ticks[first:last + 1]))
            self.assertIsNot(left.slow[1], left_aura)
            self.assertIn(left.slow[1].id, component.instances)
        if shooter is not None:
            self.assertTrue(fight.is_dead(sym))
            self.assertEqual(owner_pool.killer, shooter.body)
            self.assertEqual(owner_pool.death_tick, next(tick for tick, _, dead in ticks if dead))
            self.assertIn(left, fight.sentries.sentries)
            self.assertTrue(any(not blocked and not dead for _, blocked, dead in ticks))
            self.assertTrue(any(not blocked and dead for _, blocked, dead in ticks))
        return ticks, bot.position, bot.velocity, pool.total, tuple(last_charge.values())

    def sentry_concussion_at_range(self, definition, *, entering, gap):
        from test_symmetra_turrets import wall

        run, sym, bot, _ = self.create(definition)
        pharah = run.players[1]
        self.assertTrue(run.match.switch_hero(pharah, heroes()[barrier_tests.PHARAH]))
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        pharah.team = sym.team
        pharah.mover.position[:] = (16 if entering else 4, 1, 1)
        bot.position = (12 if entering else 8, 1, 1)
        fight = run.match.combat
        sym.body_script.component.set(12918, 0)
        slow = bot.body_script.component.entity_var(23186)
        with patch.object(fight, "_collision", return_value=wall()):
            fire(run, sym, 40, buttons=8)
            fire(run, sym, 50, buttons=0)
            [sentry] = fight.sentries.sentries
            instances = sentry.bot.body_script.component.instances.values()
            beam = next(i for i in instances if i.graph.index == 0x1590)
            reach = beam.find_var(graph.INSTANCE, 1648).value()
            self.assertEqual(sentry.target, None if entering else bot.entity)
            self.assertAlmostEqual(slow.value() or 0, 0 if entering else .2)
            aura = None if entering else sentry.slow[1]
            shot = self.launch_npc_concussion(run, pharah, bot)
            run.frame += 1
            run.match.update(run.frame * combat.TICK, run.frame)
            self.assertEqual(shot.last_tick, run.frame)
            self.assertEqual(sentry.target, None if entering else bot.entity)

            pool = health(run, bot)
            before, charge_before = pool.total, charge(sym)
            last_pool, last_charge = before, charge_before
            ticks = []
            update = fight.sentries.update

            def observe(*args, **kwargs):
                nonlocal last_pool, last_charge
                update(*args, **kwargs)
                target = next(target for target in fight._targets() if target.owner is bot)
                distance = combat.segment_distance(sentry.bot.position, *target.segment()) - target.radius
                locked = sentry.target == bot.entity
                self.assertEqual(locked, distance <= reach)
                self.assertAlmostEqual(slow.value() or 0, .2 if locked else 0)
                self.assertAlmostEqual(last_pool - pool.total, 40 * combat.TICK if locked else 0)
                current_charge = charge(sym)
                self.assertAlmostEqual(current_charge - last_charge, last_pool - pool.total, places=4)
                last_pool, last_charge = pool.total, current_charge
                ticks.append((fight.tick, locked))

            endpoint = run.frame + 25
            with patch.object(fight.sentries, "update", side_effect=observe):
                for tick in (endpoint,) if gap else range(run.frame + 1, endpoint + 1):
                    run.match.update(tick * combat.TICK, tick)
            self.assertNotIn(shot, fight.rockets)
            self.assertEqual([tick for tick, _ in ticks], list(range(run.frame + 1, endpoint + 1)))
            active = sum(locked for _, locked in ticks)
            self.assertAlmostEqual(before - pool.total, active * 40 * combat.TICK)
            self.assertAlmostEqual(charge(sym) - charge_before, before - pool.total, places=4)
            self.assertEqual(pharah.stats.value(stats.HERO_DAMAGE), 0)
            if definition.name == "Heavy Assault":
                self.assertEqual(active, 0 if entering else 25)
                if entering:
                    self.assertIsNone(sentry.slow)
                else:
                    self.assertIs(sentry.slow[1], aura)
                self.assertAlmostEqual(slow.value() or 0, 0 if entering else .2)
            else:
                self.assertGreater(active, 0)
                self.assertLess(active, 25)
                expected = [True] * active + [False] * (25 - active)
                self.assertEqual([locked for _, locked in ticks], expected[::-1] if entering else expected)
                if entering:
                    self.assertEqual(sentry.target, bot.entity)
                    self.assertIn(sentry.slow[1].id, bot.body_script.component.instances)
                    self.assertAlmostEqual(slow.value(), .2)
                else:
                    self.assertIsNone(sentry.target)
                    self.assertIsNone(sentry.slow)
                    self.assertNotIn(aura.id, bot.body_script.component.instances)
                    self.assertFalse(slow.value())
            return ticks, bot.position, bot.velocity, pool.total, charge(sym)

    def test_actual_symmetra_beam_kills_airborne_trooper_and_stops_impulse(self):
        definition = next(d for d in self.definitions.values() if d.name == "Trooper")
        run, sym, pharah, bot = self.concussion_scene(definition)
        fight = run.match.combat
        pool = health(run, bot)
        pool.now[combat.HEALTH_POOL] = 1
        self.launch_npc_concussion(run, pharah, bot)
        floor = bot.position[1]
        for _ in range(20):
            run.step({})
            if bot.position[1] > floor:
                break
        self.assertGreater(bot.position[1], floor)
        self.assertIsNotNone(bot.impulse_mover)
        for _ in range(6):
            x, y, z = bot.position
            direction = aim(fight._eye(sym), (x, y + 1, z))
            run.step({sym: (*direction, 1)})
            if bot.dead:
                break
        self.assertTrue(bot.dead)
        self.assertEqual(pool.killer, sym.body)
        self.assertIsNone(bot.impulse_mover)
        corpse = bot.position
        for _ in range(30):
            run.step({})
        self.assertEqual(bot.position, corpse)
        self.assertEqual(bot.velocity, (0, 0, 0))
        self.assertEqual(npc.remove_spawned(run.match), (bot.entity,))

    @unittest.skipUnless(os.environ.get("ORPHEUS_EXTRA_GRAPH_BUNDLES"), "requires the barrier actor")
    @unittest.skipUnless(os.environ.get("ORPHEUS_EXTRA_GRAPH_BUNDLES"), "requires the portal actors")
    @patch.object(FakeClient, "bot_stream", new=lambda _client, _body: None, create=True)
    def test_mixed_orb_credits_mech_and_all_five_npcs_excluding_portal_damage(self):
        prepare_matrix_resources(self)
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                self.check_mixed_orb_charge(definition)

    def check_mixed_orb_charge(self, definition, *, initial_charge=0):
        fixture = portal_tests.TeleporterTests()
        run, deployer, _, pair = fixture.deploy()
        owner = run.players[1]
        self.assertTrue(run.match.switch_hero(owner, heroes()[deployer.hero.guid]))
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        owner.team = 1 - deployer.team
        mech = run.match.add_player(3, "Mech", SOLDIER, deployer.team, False)
        in_world(run.match, mech)
        run.players.append(mech)
        self.assertTrue(run.match.switch_hero(mech, replace(heroes()[matrix.DVA], body=matrix.MECH)))
        fixture.place(deployer, (20, 0, 20))
        fixture.place(owner, (1.25, 0, -6))
        fixture.place(mech, (1.25, 0, 0))
        bot = npc.spawn(run.match, definition, (2.35, 0, 0), team=deployer.team)
        owner.body_script.component.set(12918, 0)
        owner.body_script.set_ult_charge(initial_charge)
        fight = run.match.combat
        device = fight.vitals[pair.endpoints[1].entity]
        mech_pool, npc_pool = health(run, mech), health(run, bot)
        start_npc = npc_pool.total
        self.assertEqual((mech_pool.total, device.total), (600, 300))
        angles = aim(fight._eye(owner), (1.25, 1.1, 0))
        for _ in range(90):
            run.step({owner: (*angles, 2)})
        run.step({owner: (*angles, 0)})
        [orb] = fight.rockets
        self.assertIs(orb.shooter, owner)
        self.assertEqual(charge(owner), initial_charge)
        with patch.object(fight, "_damage", wraps=fight._damage) as hits:
            for _ in range(40):
                if orb not in fight.rockets:
                    break
                run.step({})
        self.assertNotIn(orb, fight.rockets)
        self.assertIs(hits.call_args_list[0].args[1].owner, mech)
        self.assertEqual({call.args[1].entity for call in hits.call_args_list},
                         {mech.body, bot.entity, pair.endpoints[1].entity})
        self.assertEqual(mech_pool.total, 490)
        npc_loss = start_npc - npc_pool.total
        device_loss = 300 - device.total
        self.assertGreater(npc_loss, 0)
        self.assertGreater(device_loss, 0)
        self.assertAlmostEqual(owner.stats.value(stats.HERO_DAMAGE), 110 + npc_loss + device_loss)
        self.assertAlmostEqual(charge(owner), min(1680, initial_charge + 110 + npc_loss), places=4)
        return run, owner, mech

    @unittest.skipUnless(os.environ.get("ORPHEUS_EXTRA_GRAPH_BUNDLES"), "requires portal and barrier actors")
    @patch.object(FakeClient, "bot_stream", new=lambda _client, _body: None, create=True)
    def test_mixed_mech_and_npc_orb_gains_cap_and_admit_actual_photon_barrier(self):
        prepare_matrix_resources(self)
        definition = next(d for d in self.definitions.values() if d.name == "Trooper")
        run, owner, mech = self.check_mixed_orb_charge(definition, initial_charge=1565)
        self.assertEqual(charge(owner), 1680)
        fight = run.match.combat
        for button in (32, 0, 1):
            run.step({owner: (0, 8192, button)})
        for _ in range(25):
            run.step({})
        [wall] = fight.barriers.walls
        self.assertIs(wall.owner, owner)
        self.assertEqual(charge(owner), 0)
        pool = health(run, mech)
        before = pool.total
        angles = aim(fight._eye(owner), (1.25, 1.1, 0))
        for _ in range(90):
            run.step({owner: (*angles, 1)})
            if pool.total < before:
                break
        self.assertLess(pool.total, before)
        self.assertEqual(charge(owner), 0)

    def test_earned_charge_from_heavy_actual_damage_casts_photon_barrier(self):
        heavy = next(d for d in self.definitions.values() if d.name == "Heavy Assault")
        run, player, bot, _target = self.create(heavy)
        component = player.body_script.component
        component.set(12918, 0)
        for _ in range(3):
            for _ in range(600):
                fire(run, player)
                if component.entity_var(342).value() >= 1680:
                    break
            if component.entity_var(342).value() >= 1680:
                break
            fire(run, player, buttons=0)
            fire(run, player, buttons=0x400)
            fire(run, player, 100, buttons=0)
        self.assertEqual(component.entity_var(342).value(), 1680)
        self.assertGreater(health(run, bot).total, 0)
        fire(run, player, buttons=0)
        run.step({player: (0, 8192, 32)})
        run.step({player: (0, 8192, 0)})
        run.step({player: (0, 8192, 1)})
        for _ in range(25):
            run.step({})
        self.assertEqual(component.entity_var(342).value(), 0)
        self.assertEqual(len(run.match.combat.barriers.walls), 1)

    def test_primary_hits_and_misses_at_each_authored_capsule_radius_and_top(self):
        # Declared relative placement isolates each authored capsule, including
        # Heavy's .7 radius and Assassin's1.5 height. No grounded-map claim.
        for definition in self.definitions.values():
            for boundary in ("radius", "top"):
                for inside in (False, True):
                    with self.subTest(npc=definition.name, boundary=boundary, inside=inside):
                        run, player, other = arena()
                        other.mover.position = [40, 1, 40]
                        run.step({player: (0, 0, 0)})  # Settle the declared shooter pose first.
                        weapon = next(i for i in player.body_script.component.instances.values()
                                      if i.graph.index == 0x157C)
                        ray = weapon.state(101)  # The authored .2-radius query; state100 is radius0.
                        radius = ray.evaluate(ray.node.field("m_57DAE1B5"), watch=False)
                        self.assertGreater(radius, 0)
                        edge = -0.05 if inside else 0.05
                        eye = run.match.combat._eye(player)
                        if boundary == "radius":
                            position = (definition.mover.stand_radius + radius + edge,
                                        eye[1] - definition.mover.stand_height / 2, 4)
                        else:
                            position = (0, eye[1] - definition.mover.stand_height - radius - edge, 4)
                        bot = npc.spawn(run.match, definition, position, team=1 - player.team)
                        self.assertIs(bot.movement_data, definition.mover)
                        before = health(run, bot).total
                        run.step({player: (0, 0, 1)})
                        if inside:
                            self.assertLess(health(run, bot).total, before)
                        else:
                            self.assertEqual(health(run, bot).total, before)

    def test_primary_wall_stops_a_previously_hitting_beam_for_every_npc(self):
        wall = World([(-5, 0, 2, 5, 0, 2, 5, 8, 2),
                      (-5, 0, 2, 5, 8, 2, -5, 8, 2)])
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                run, player, bot, _target = self.create(definition)
                initial = health(run, bot).total
                fire(run, player, 4)
                before_wall = health(run, bot).total
                self.assertLess(before_wall, initial)
                with patch.object(run.match.combat, "_collision", return_value=wall):
                    fire(run, player, 4)
                self.assertEqual(health(run, bot).total, before_wall)

    def test_primary_charge_levels_execute_against_every_actual_npc_body(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                run, player, bot, _target = self.create(definition)
                weapon = next(i for i in player.body_script.component.instances.values()
                              if i.graph.index == 0x157C)
                rates = set()
                vitals = health(run, bot)
                for _ in range(180):
                    before = vitals.total
                    fire(run, player)
                    rate = weapon.find_var(0, 499).value()
                    loss = before - vitals.total
                    self.assertAlmostEqual(loss, rate * .016, places=4)
                    rates.add(rate)
                    # Explicit test healing keeps low-HP authored targets alive;
                    # no maximum pool, mover, graph or damage rate is replaced.
                    self.assertAlmostEqual(vitals.heal(loss), loss, places=4)
                    run.match.combat._sync_body_health(bot, vitals)
                self.assertEqual(rates, {60, 120, 180})

    def test_authored_primary_input_from_three_working_npcs_damages_symmetra_shields(self):
        expected = {'Trooper': 20, 'Sniper': 100, 'Heavy Assault': 5}
        for definition in self.definitions.values():
            if definition.name not in expected:
                continue
            with self.subTest(npc=definition.name):
                run, player, bot, _target = self.create(definition)
                player.mover.yaw, player.mover.pitch = aim(
                    run.match.combat._eye(player), (bot.position[0], bot.position[1] + 1, bot.position[2]))
                self.assertEqual(npc_control.start(run.match, player), bot.entity)
                vitals = health(run, player)
                before = vitals.total
                with patch.object(FakeClient, 'bot_stream', return_value=None, create=True):
                    for _ in range(200):
                        run.step({})
                        self.assertIsNotNone(bot.body_script)
                        if vitals.total < before:
                            break
                self.assertEqual(before - vitals.total, expected[definition.name])
                self.assertEqual(vitals.now[combat.HEALTH_POOL], 100)
                self.assertEqual(vitals.now[combat.SHIELDS_POOL], 125 - expected[definition.name])

    @patch.object(FakeClient, "bot_stream", new=lambda _client, _body: None, create=True)
    def test_allied_deflect_redirects_actual_talon_weapons_and_bypasses_assassin_contact(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                self.check_allied_deflect_against_npc(definition)

    @patch.object(FakeClient, "bot_stream", new=lambda _client, _body: None, create=True)
    def test_projected_barrier_blocks_reflected_talon_hits_until_it_breaks(self):
        for definition in self.definitions.values():
            if definition.name != "Assassin":
                capacities = (200, 1, 3) if definition.name == "Enforcer" else (200, 1)
                for barrier_health in capacities:
                    with self.subTest(npc=definition.name, barrier_health=barrier_health):
                        self.check_allied_deflect_against_npc(definition, barrier_health=barrier_health)

    def check_allied_deflect_against_npc(self, definition, *, barrier_health=None):
        assassin_attack = definition.name == "Assassin"
        run, player, bot, npc_target = self.create(definition, distance=1.5 if assassin_attack else 4)
        fight = run.match.combat
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        genji = run.players[1]
        genji.team = player.team
        self.assertTrue(run.match.switch_hero(genji, heroes()[deflection.GENJI]))
        genji.mover.position[:] = (3, 1, 2)
        genji.mover.body = None
        genji_target = next(target for target in fight._targets() if target.owner is genji)
        npc_vitals = health(run, bot)
        genji_vitals = health(run, genji)
        player_vitals = health(run, player)
        initial_npc = npc_vitals.total
        initial_genji = genji_vitals.total
        initial_player = player_vitals.total
        if definition.name == "Sniper":
            self.engage_npc(run, player, bot)
            for _ in range(85):
                run.step({})
        bubble = None
        if barrier_health is not None:
            fixture = barrier_tests.PhotonBarrierTests()
            caster = fixture.add_target(run, 3, (3, 1, 4))
            self.assertTrue(run.match.switch_hero(caster, heroes()[zarya_barriers.HERO]))
            caster.team = bot.team
            fixture.move(caster, (3, 1, 4))
            x, y, z = bot.position
            projected_aim = aim(fight._eye(caster), (x, y + npc_target.eye, z))
            run.step({caster: (*projected_aim, 16)})
            source, _, bubble = fight.zarya_barriers.contact(bot)
            self.assertIs(source, caster)
            self.assertEqual(bubble.remaining, 200)
            bubble.remaining = barrier_health
        run.step({genji: (0, 0, 16)})
        self.assertIsNotNone(deflection.state_of(genji_target))
        if definition.name != "Sniper":
            self.engage_npc(run, player, bot)
        genji.mover.position[:] = (0, 1, .75 if assassin_attack else 2)
        genji.mover.body = None
        charge_events = []
        hit_ticks = []
        resolve = fight._damage

        def observe(shooter, target, amount, *args, **kwargs):
            before_charge, before_pool = charge(genji), npc_vitals.total
            applied = resolve(shooter, target, amount, *args, **kwargs)
            if shooter is genji:
                self.assertIs(target.owner, bot)
                charge_events.append((amount, before_pool - npc_vitals.total, charge(genji) - before_charge))
                hit_ticks.append(fight.tick)
            return applied

        with patch.object(fight, "_damage", side_effect=observe):
            for _ in range(100):
                run.step({})
                if (npc_vitals.total != initial_npc or genji_vitals.total != initial_genji
                        or player_vitals.total != initial_player
                        or (bubble is not None and bubble.remaining < barrier_health)):
                    break
        self.assertEqual(genji_vitals.total, initial_genji)
        if assassin_attack:
            self.assertEqual(player_vitals.total, initial_player - 25)
            self.assertEqual(fight.pins.get(bot.entity), player.body)
            self.assertEqual(npc_vitals.total, initial_npc)
            self.assertEqual(charge_events, [])
            self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), 0)
        elif bubble is not None:
            self.assertEqual(player_vitals.total, initial_player)
            self.assertTrue(charge_events)
            damage_events = [(dealt, gained) for _, dealt, gained in charge_events]
            total_loss = initial_npc - npc_vitals.total
            self.assertAlmostEqual(genji.stats.value(stats.HERO_DAMAGE), total_loss, places=4)
            self.assertAlmostEqual(sum(gain for _, gain in damage_events), total_loss, places=4)
            hits = genji.client.messages(combat.HIT)
            if barrier_health == 200:
                self.assertTrue(all(dealt == gained == 0 for dealt, gained in damage_events))
                self.assertEqual(total_loss, 0)
                self.assertFalse(hits)
                absorbed = sum(amount for amount, _, _ in charge_events)
                self.assertGreater(absorbed, 0)
                self.assertAlmostEqual(bubble.remaining, barrier_health - absorbed)
            else:
                blocked_count = 2 if barrier_health == 3 else 1
                self.assertEqual(damage_events[:blocked_count], [(0, 0)] * blocked_count)
                blocked_damage = sum(amount for amount, _, _ in charge_events[:blocked_count])
                self.assertGreater(blocked_damage, barrier_health)
                self.assertEqual(bubble.remaining, 0)
                self.assertIsNone(fight.zarya_barriers.contact(bot))
                self.assertEqual(len(set(hit_ticks)), 1)
                # The remaining pellets of the Enforcer volley hit the exposed NPC in this tick.
                if definition.name == "Enforcer":
                    self.assertEqual([amount for amount, _, _ in charge_events], [2] * 25)
                expected = 50 - blocked_count * 2 if definition.name == "Enforcer" else 0
                for dealt, gained in damage_events[blocked_count:]:
                    self.assertAlmostEqual(gained, dealt, places=4)
                self.assertAlmostEqual(total_loss, expected, places=4)
                self.assertEqual(sum(hit["+0x78"]["+0x4"] for hit in hits), expected)
                absorbed = barrier_health
            run.step({})
            energy = caster.body_script.component.entity_var(87).value()
            self.assertAlmostEqual(energy, absorbed * .2, places=4)
            energy_graph = next(i for i in caster.body_script.component.instances.values()
                                if i.graph.index == 0x0242)
            self.assertEqual(energy_graph.find_var(0, 392).value(), expr.Entity(genji.body))
            if barrier_health == 1:
                followup_damage = {"Trooper": 20, "Heavy Assault": 5, "Enforcer": 50, "Sniper": 100}
                recast = definition.name in ("Enforcer", "Sniper")
                breaking_tick = hit_ticks[-1]
                hit_ticks.clear()
                charge_events.clear()
                with patch.object(fight, "_damage", side_effect=observe):
                    for _ in range(150):
                        run.step({})
                        if charge_events or deflection.state_of(genji_target) is None:
                            break
                if recast:
                    self.assertFalse(charge_events)
                    self.assertIsNone(deflection.state_of(genji_target))
                    self.recast_after_unreturned_talon_volley(
                        run, player, genji, bot, genji_target, followup_damage[definition.name],
                    )
                    self.assertEqual(player_vitals.total, initial_player)
                    self.assertAlmostEqual(initial_npc - npc_vitals.total, total_loss, places=4)
                    genji_after_cooldown = genji_vitals.total
                    with patch.object(fight, "_damage", side_effect=observe):
                        for _ in range(125):
                            run.step({})
                            if charge_events:
                                break
                returned = followup_damage[definition.name]
                self.assertTrue(charge_events, "No later reflected hit before Deflect ended")
                for _, dealt, gained in charge_events:
                    self.assertAlmostEqual(gained, dealt, places=4)
                self.assertGreater(hit_ticks[0], breaking_tick)
                self.assertAlmostEqual(initial_npc - npc_vitals.total, total_loss + returned, places=4)
                self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), total_loss + returned)
                self.assertEqual(player_vitals.total, initial_player)
                self.assertEqual(genji_vitals.total, genji_after_cooldown if recast else initial_genji)
        else:
            self.assertEqual(player_vitals.total, initial_player)
            self.assertNotIn(bot.entity, fight.pins)
            returned = {"Trooper": 20, "Enforcer": 50, "Sniper": 100, "Heavy Assault": 5}
            self.assertEqual(initial_npc - npc_vitals.total, returned[definition.name])
            self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), returned[definition.name])
            self.assertTrue(charge_events)
            for _, dealt, gained in charge_events:
                self.assertAlmostEqual(gained, dealt, places=4)
            gained = sum(gained for _, _, gained in charge_events)
            self.assertAlmostEqual(gained, returned[definition.name], places=4)

    def recast_after_unreturned_talon_volley(self, run, player, genji, bot, genji_target, incoming):
        ability = next(i for i in genji.body_script.component.instances.values()
                       if i.graph.index == 0x03A3).state(0)
        pool = health(run, genji)
        before = pool.total
        # The recovery animation follows the active window before the cooldown starts.
        for _ in range(30):
            if ability.remaining() > 0:
                break
            run.step({})
        self.assertEqual(ability.cur, 8)
        self.assertGreater(ability.remaining(), 0)
        self.assertEqual(pool.total, before)
        run.step({genji: (0, 0, 16)})
        self.assertIsNone(deflection.state_of(genji_target))
        for _ in range(180):
            if pool.total < before:
                break
            run.step({})
        self.assertEqual(before - pool.total, incoming)
        self.assertGreater(ability.remaining(), 0)
        self.assertEqual(npc_control.stop(run.match), 1)

        sniper = bot.name == "Sniper"
        windup = 85 * combat.TICK if sniper else 0
        for _ in range(600):
            if ability.remaining() <= windup:
                break
            run.step({})
        genji.mover.position[:] = (3, 1, 2)
        genji.mover.body = None
        if sniper:
            self.engage_npc(run, player, bot)
        for _ in range(90):
            if not ability.remaining():
                break
            run.step({})
        self.assertEqual(ability.remaining(), 0)
        run.step({genji: (0, 0, 16)})
        self.assertIsNotNone(deflection.state_of(genji_target))
        if not sniper:
            self.engage_npc(run, player, bot)
        genji.mover.position[:] = (0, 1, 2)
        genji.mover.body = None

    @patch.object(FakeClient, "bot_stream", new=lambda _client, _body: None, create=True)
    def test_allied_matrix_filters_actual_attacks_from_all_five_npcs(self):
        prepare_matrix_resources(self)
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                self.check_npc_absorption(definition, mech=True)

    @unittest.skipIf(projectile_tags.CATALOG_SHA256 is None, "requires external projectile tags")
    @patch.object(FakeClient, "bot_stream", new=lambda _client, _body: None, create=True)
    def test_allied_grasp_filters_actual_attacks_from_all_five_npcs(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                self.check_npc_absorption(definition, mech=False)

    @patch.object(FakeClient, "bot_stream", new=lambda _client, _body: None, create=True)
    def test_nearer_matrix_or_grasp_absorbs_actual_sniper_shot_once(self):
        prepare_matrix_resources(self)
        definition = next(d for d in self.definitions.values() if d.name == "Sniper")
        for grasp_first in (False, True):
            with self.subTest(grasp_first=grasp_first):
                self.check_competing_absorbers(definition, grasp_first)

    @patch.object(FakeClient, "bot_stream", new=lambda _client, _body: None, create=True)
    def test_matrix_release_during_actual_sniper_flight_exposes_mech(self):
        prepare_matrix_resources(self)
        definition = next(d for d in self.definitions.values() if d.name == "Sniper")
        stepped = self.matrix_release_during_sniper_flight(definition, gap=False)
        self.assertEqual(self.matrix_release_during_sniper_flight(definition, gap=True), stepped)

    def matrix_release_during_sniper_flight(self, definition, *, gap):
        run, owner, bot, _ = self.create(definition, distance=28)
        fight = run.match.combat
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        mech = run.players[1]
        mech.team = owner.team
        self.assertTrue(run.match.switch_hero(mech, replace(heroes()[matrix.DVA], body=matrix.MECH)))
        mech.mover.position[:] = (3, 1, 2)
        self.engage_npc(run, owner, bot)
        for _ in range(85):
            run.step({})
        defend = {mech: (0, 0, 2)}
        run.step(defend)
        mech.mover.position[:] = (0, 1, 2)
        mech.mover.body = None
        for _ in range(60):
            run.step(defend)
            if fight.rockets:
                break
        [rocket] = fight.rockets
        self.assertIs(rocket.shooter, bot)
        self.assertTrue(projectile_tags.absorbable(rocket.definition))
        ray = bot, rocket.definition, rocket.position, rocket.direction, 28
        contact, _ = matrix.trace(fight, *ray)
        self.assertGreater(contact, rocket.speed * combat.TICK)
        start_frame = run.frame
        if gap:
            run.frame += 40
            run.match.update(run.frame * combat.TICK, run.frame)
        else:
            run.step({})
            self.assertIn(rocket, fight.rockets)
            for _ in range(39):
                run.step({})
        self.assertIsNone(active_state(mech, matrix.DVA, 0x02CC, 3))
        self.assertIsNone(matrix.trace(fight, *ray))
        self.assertNotIn(rocket, fight.rockets)
        self.assertGreater(rocket.flown * rocket.speed, contact)
        pool = health(run, mech)
        self.assertEqual(pool.total, 505)
        self.assertEqual(health(run, owner).total, 225)
        return (round(pool.last_damage_at / combat.TICK) - start_frame, round(rocket.flown * rocket.speed, 6))

    def check_competing_absorbers(self, definition, grasp_first):
        run, owner, bot, _ = self.create(definition, distance=18)
        fight = run.match.combat
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        mech = run.players[1]
        mech.team = owner.team
        self.assertTrue(run.match.switch_hero(mech, replace(heroes()[matrix.DVA], body=matrix.MECH)))
        sigma = run.match.add_player(3, "Sigma", SOLDIER, owner.team, False)
        in_world(run.match, sigma)
        run.players.append(sigma)
        self.assertTrue(run.match.switch_hero(sigma, heroes()[sigma_grasp.SIGMA]))
        mech.mover.position[:] = (3, 1, 2)
        sigma.mover.position[:] = (-3, 1, 2)
        self.engage_npc(run, owner, bot)
        for _ in range(90):
            run.step({})
        defend = {mech: (0, 0, 2), sigma: (0, 0, 8)}
        run.step(defend)
        mech.mover.position[:] = (0, 1, 2 if grasp_first else 16)
        sigma.mover.position[:] = (0, 1, 16 if grasp_first else 2)
        mech.mover.body = sigma.mover.body = None
        for _ in range(60):
            run.step(defend)
            if fight.rockets:
                break
        [rocket] = fight.rockets
        self.assertIs(rocket.shooter, bot)
        self.assertTrue(projectile_tags.absorbable(rocket.definition))
        ray = bot, rocket.definition, rocket.position, rocket.direction, 18
        matrix_distance, matrix_target = matrix.trace(fight, *ray)
        grasp_distance, grasp_target, grasp_state = fight.grasp.trace(*ray)
        self.assertIs(matrix_target.owner, mech)
        self.assertIs(grasp_target.owner, sigma)
        self.assertNotEqual(grasp_distance, matrix_distance)
        self.assertEqual(grasp_distance < matrix_distance, grasp_first)
        with patch.object(fight.grasp, "absorb", wraps=fight.grasp.absorb) as absorbed:
            for _ in range(20):
                if rocket not in fight.rockets:
                    break
                run.step(defend)
        self.assertNotIn(rocket, fight.rockets)
        self.assertAlmostEqual(rocket.flown * rocket.speed, min(matrix_distance, grasp_distance))
        self.assertEqual(absorbed.call_count, int(grasp_first))
        self.assertEqual(grasp_state.instance.find_var(0, 21312).value() or 0, 100 if grasp_first else 0)
        self.assertEqual([health(run, actor).total for actor in (owner, mech, sigma)], [225, 600, 400])

    @unittest.skipIf(projectile_tags.CATALOG_SHA256 is None, "requires external projectile tags")
    @patch.object(FakeClient, "bot_stream", new=lambda _client, _body: None, create=True)
    def test_grasp_converts_first_sniper_shot_before_second_in_retained_update(self):
        stepped = self.grasp_expiry_sniper_trace(gap=False)
        self.assertEqual(self.grasp_expiry_sniper_trace(gap=True), stepped)

    def grasp_expiry_sniper_trace(self, *, gap):
        definition = next(d for d in self.definitions.values() if d.name == "Sniper")
        run, owner, early, _ = self.create(definition, distance=6)
        fight = run.match.combat
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        sigma = run.players[1]
        sigma.team = owner.team
        self.assertTrue(run.match.switch_hero(sigma, heroes()[sigma_grasp.SIGMA]))
        self.engage_npc(run, owner, early)
        for _ in range(8):
            run.step({})
        late = npc.spawn(run.match, definition, (0, 1, 4), team=early.team)
        self.engage_npc(run, owner, late)
        run.step({sigma: (0, 0, 8)})
        cast_frame = run.frame
        sigma.mover.position[:] = (0, 1, 2)
        sigma.mover.body = None
        state = active_state(sigma, sigma_grasp.SIGMA, sigma_grasp.GRAPH, sigma_grasp.STATE)
        self.assertIsNotNone(state)
        for _ in range(115):
            run.step({})
        pool = health(run, sigma)
        self.assertTrue(state.active)
        self.assertEqual(pool.total, 400)
        self.assertFalse(state.instance.find_var(0, 21312).value())
        trace = []
        absorb, resolve = fight.grasp.absorb, fight._damage

        def observe_absorb(active, shooter, *args):
            absorb(active, shooter, *args)
            trace.append(("absorb", fight.tick - cast_frame, shooter.entity,
                          active.instance.find_var(0, 21312).value()))

        def observe_damage(shooter, target, *args, **kwargs):
            bonus = pool.bonus_shields
            applied = resolve(shooter, target, *args, **kwargs)
            if target.owner is sigma:
                trace.append(("hit", fight.tick - cast_frame, shooter.entity, round(bonus, 4)))
            return applied

        with (
            patch.object(fight.grasp, "absorb", side_effect=observe_absorb),
            patch.object(fight, "_damage", side_effect=observe_damage),
        ):
            if gap:
                run.frame += 20
                run.match.update(run.frame * combat.TICK, run.frame)
            else:
                for _ in range(20):
                    run.step({})
        self.assertEqual(trace, [
            ("absorb", 118, early.entity, 100),
            ("hit", 126, late.entity, 60),
        ])
        self.assertFalse(state.active)
        self.assertEqual(pool.bonus_shields, 0)
        self.assertAlmostEqual(pool.total, 360, places=4)
        self.assertEqual(health(run, owner).total, 225)
        return trace

    def check_npc_absorption(self, definition, *, mech):
        name = definition.name
        run, owner, bot, _ = self.create(definition, distance=1.5 if name == "Assassin" else 4)
        guard = run.players[1]
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        guard.team = owner.team
        hero = replace(heroes()[matrix.DVA], body=matrix.MECH) if mech else heroes()[sigma_grasp.SIGMA]
        self.assertTrue(run.match.switch_hero(guard, hero))
        guard.mover.position[:] = (3, 1, 2)
        for _ in range(75 if mech else 20):
            run.step({})
        if name == "Sniper":
            self.engage_npc(run, owner, bot)
            for _ in range(85):
                run.step({})
        button = 2 if mech else 8
        defend = {guard: (0, 0, button)}
        run.step(defend)
        spec = (matrix.DVA, 0x02CC, 3) if mech else (sigma_grasp.SIGMA, sigma_grasp.GRAPH, sigma_grasp.STATE)
        state = active_state(guard, *spec)
        self.assertIsNotNone(state)
        if name != "Sniper":
            self.engage_npc(run, owner, bot)
        guard.mover.position[:] = (0, 1, .75 if name == "Assassin" else 2)
        guard.mover.body = None
        fight = run.match.combat
        owner_pool, guard_pool = health(run, owner), health(run, guard)
        guard_initial = guard_pool.total
        self.assertEqual((owner_pool.total, guard_initial), (225, 600 if mech else 400))

        if name in ("Enforcer", "Sniper"):
            self.check_absorbed_npc_projectiles(run, owner, bot, defend, state, mech=mech)
            return

        for _ in range(90):
            run.step(defend)
            if owner_pool.total != 225 or guard_pool.total != guard_initial:
                break
        self.assertIsNotNone(active_state(guard, *spec))
        if name == "Assassin":
            self.assertEqual(owner_pool.total, 200)
            self.assertEqual(guard_pool.total, guard_initial)
            self.assertEqual(fight.pins.get(bot.entity), owner.body)
        else:
            self.assertEqual(owner_pool.total, 225)
            loss = {"Trooper": 15 if mech else 20, "Heavy Assault": 2.5 if mech else 5}
            self.assertEqual(guard_initial - guard_pool.total, loss[name])
        if not mech:
            self.assertFalse(state.instance.find_var(0, 21312).value())
            self.assertEqual(guard_pool.bonus_shields, 0)

    def check_absorbed_npc_projectiles(self, run, owner, bot, defend, state, *, mech):
        guard = next(iter(defend))
        fight = run.match.combat
        guard_pool = health(run, guard)
        initial = guard_pool.total
        amount, count = (50, 25) if bot.name == "Enforcer" else (100, 1)
        for _ in range(90):
            run.step(defend)
            if fight.rockets:
                break
        volley = list(fight.rockets)
        self.assertEqual(len(volley), count)
        self.assertTrue(all(rocket.shooter is bot and projectile_tags.absorbable(rocket.definition)
                            for rocket in volley))
        npc_pool = health(run, bot)
        npc_initial = npc_pool.total
        x, y, z = bot.position
        aim_at_npc = aim(fight._eye(owner), (x, y + 1, z))
        counterfire = {**defend, owner: (*aim_at_npc, 1)}
        with patch.object(fight, "_damage", wraps=fight._damage) as damage:
            for _ in range(15):
                if not fight.rockets:
                    break
                run.step(counterfire)
        self.assertFalse(fight.rockets)
        self.assertTrue(damage.call_args_list)
        self.assertTrue(all(call.args[0] is owner and call.args[1].owner is bot
                            for call in damage.call_args_list))
        self.assertLess(npc_pool.total, npc_initial)
        self.assertAlmostEqual(owner.stats.value(stats.HERO_DAMAGE), npc_initial - npc_pool.total)
        self.assertTrue(state.active)
        self.assertTrue(all(rocket.flown * rocket.speed < 2 for rocket in volley))
        self.assertEqual((health(run, owner).total, guard_pool.total), (225, initial))
        if not mech:
            self.assertEqual(state.instance.find_var(0, 21312).value(), amount)
            source = expr.lvalue(state.node.field("m_0A2CABA3"))
            self.assertEqual(state.instance.find_var(*source).value(), expr.Entity(bot.entity))
        self.assertEqual(npc_control.stop(run.match), 1)
        for _ in range(160):
            if not state.active:
                break
            run.step({})
        self.assertFalse(state.active)
        self.assertAlmostEqual(guard_pool.bonus_shields, 0 if mech else amount * .6, places=4)
        self.assertEqual(guard_pool.total, initial + guard_pool.bonus_shields)

        guard.mover.position[:] = (3, 1, 2)
        self.engage_npc(run, owner, bot)
        guard.mover.position[:] = (0, 1, 2)
        guard.mover.body = None
        for _ in range(180):
            before = guard_pool.total
            run.step({})
            if guard_pool.last_damage_at is not None:
                break
        self.assertEqual(guard_pool.last_damage_at, fight.now)
        if mech:
            self.assertEqual(before - guard_pool.total, 25 if count == 25 else 95)
        else:
            self.assertAlmostEqual(before - guard_pool.total, amount, places=4)
            self.assertEqual(guard_pool.bonus_shields, 0)
            self.assertLess(guard_pool.total, initial)
        self.assertEqual(health(run, owner).total, 225)

    @patch.object(FakeClient, "bot_stream", new=lambda _client, _body: None, create=True)
    def test_actual_hits_from_all_five_npcs_restart_symmetra_shield_recovery(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                self.check_npc_shield_recovery(definition)

    def check_npc_shield_recovery(self, definition):
        distance = 1.5 if definition.name == "Assassin" else 4
        run, player, bot, _ = self.create(definition, distance=distance)
        fight = run.match.combat
        vitals = health(run, player)

        first_hit = self.npc_hit_and_stop(run, player, bot)
        first_shields = vitals.now[combat.SHIELDS_POOL]
        self.assertEqual(vitals.now[combat.HEALTH_POOL], 100)
        for _ in range(24):
            run.step({})
        self.assertEqual(vitals.last_damage_at, first_hit)
        self.assertEqual(vitals.now[combat.SHIELDS_POOL], first_shields)

        second_hit = self.npc_hit_and_stop(run, player, bot)
        self.assertLess(second_hit, first_hit + 3)
        self.assertLess(vitals.now[combat.SHIELDS_POOL], first_shields)
        shields_after_hit = vitals.now[combat.SHIELDS_POOL]
        health_after_hit = vitals.now[combat.HEALTH_POOL]
        self.assertGreater(health_after_hit, 0)
        if definition.name == "Sniper":
            self.assertLess(health_after_hit, 100)

        for _ in range(187):
            run.step({})
            self.assertEqual(vitals.now[combat.SHIELDS_POOL], shields_after_hit)
        self.assertGreater(fight.now, first_hit + 3)
        self.assertEqual(vitals.last_damage_at, second_hit)

        run.step({})
        self.assertAlmostEqual(vitals.now[combat.SHIELDS_POOL], shields_after_hit + .24)
        run.step({})
        self.assertAlmostEqual(vitals.now[combat.SHIELDS_POOL], shields_after_hit + .72)
        for _ in range(270):
            run.step({})
        self.assertEqual(vitals.now[combat.SHIELDS_POOL], 125)
        self.assertEqual(vitals.now[combat.HEALTH_POOL], health_after_hit)
        self.assertEqual(vitals.last_damage_at, second_hit)

    def npc_hit_and_stop(self, run, player, bot):
        vitals = health(run, player)
        previous_damage = vitals.last_damage_at
        previous_total = vitals.total
        self.engage_npc(run, player, bot)
        for _ in range(180):
            run.step({})
            if vitals.last_damage_at != previous_damage:
                break
        self.assertLess(vitals.total, previous_total)
        self.assertEqual(vitals.last_damage_at, run.match.combat.now)
        self.assertEqual(npc_control.stop(run.match), 1)
        return vitals.last_damage_at

    @patch.object(FakeClient, "bot_stream", new=lambda _client, _body: None, create=True)
    def test_projected_barrier_blocks_all_five_npcs_without_resetting_shield_recovery(self):
        for definition in self.definitions.values():
            name = definition.name
            with self.subTest(npc=name):
                self.check_npc_shield_recovery_under_projected_barrier(name)

    @patch.object(FakeClient, "bot_stream", new=lambda _client, _body: None, create=True)
    def test_breaking_projected_barrier_does_not_restart_shield_recovery(self):
        for definition in self.definitions.values():
            name = definition.name
            with self.subTest(npc=name):
                # Let the last pellet at this range break the barrier.
                capacity = 50 if name == "Enforcer" else 1
                self.check_npc_shield_recovery_under_projected_barrier(name, capacity=capacity)

    @patch.object(FakeClient, "bot_stream", new=lambda _client, _body: None, create=True)
    def test_enforcer_pellets_after_barrier_break_restart_shield_recovery(self):
        self.check_npc_shield_recovery_under_projected_barrier("Enforcer", capacity=1,
                                                               followup_damage=48)

    def check_npc_shield_recovery_under_projected_barrier(self, name, *, capacity=200, followup_damage=0):
        distance = 1.5 if name == "Assassin" else 4
        run, player, caster, bot = self.projected_scene(name, distance=distance)
        fight = run.match.combat
        vitals = health(run, player)
        first_hit = self.npc_hit_and_stop(run, player, bot)
        hit_frame = run.frame
        pools_after_hit = vitals.now.copy()

        for _ in range(24):
            run.step({})
        if name == "Sniper":
            # Wait through aim windup so the barrier is still active at impact.
            self.engage_npc(run, player, bot)
            for _ in range(85):
                run.step({})
        run.step({caster: (-16384, 0, 16)})
        _, _, barrier = fight.zarya_barriers.contact(player)
        self.assertEqual(barrier.remaining, 200)
        barrier.remaining = capacity
        if name != "Sniper":
            self.engage_npc(run, player, bot)
        for _ in range(120):
            if barrier.remaining < capacity:
                break
            run.step({})
        self.assertLess(barrier.remaining, capacity)
        if capacity < 200:
            self.assertEqual(barrier.remaining, 0)
            self.assertIsNone(fight.zarya_barriers.contact(player))
        pools_after_hit[combat.SHIELDS_POOL] -= followup_damage
        self.assertEqual(vitals.now, pools_after_hit)
        self.assertNotIn(bot.entity, fight.pins)
        self.assertEqual(npc_control.stop(run.match), 1)
        self.assertLess(run.frame, hit_frame + 187)
        if followup_damage:
            first_hit, hit_frame = fight.now, run.frame
        while run.frame < hit_frame + 187:
            run.step({})
            self.assertEqual(vitals.now, pools_after_hit)
        run.step({})
        pools_after_hit[combat.SHIELDS_POOL] += .24
        self.assertEqual(vitals.last_damage_at, first_hit)
        for pool, expected in pools_after_hit.items():
            self.assertAlmostEqual(vitals.now[pool], expected)

    @patch.object(FakeClient, "bot_stream", new=lambda _client, _body: None, create=True)
    def test_retained_sniper_hit_keeps_shields_recovered_before_impact(self):
        definition = next(d for d in self.definitions.values() if d.name == "Sniper")
        results = []
        for gap in (False, True):
            with self.subTest(gap=gap):
                run, player, bot, _ = self.create(definition)
                fight = run.match.combat
                vitals = health(run, player)
                first_hit = self.npc_hit_and_stop(run, player, bot)
                hit_frame = run.frame
                self.assertEqual(vitals.now[combat.SHIELDS_POOL], 25)
                bot.position = (10, 1, 0)
                second_sniper = npc.spawn(run.match, definition, (0, 1, 4), team=bot.team)
                for _ in range(75):
                    run.step({})
                self.engage_npc(run, player, second_sniper)
                while run.frame < hit_frame + 174:
                    run.step({})
                self.assertEqual(vitals.last_damage_at, first_hit)
                self.assertEqual(vitals.now[combat.SHIELDS_POOL], 25)

                end_frame = hit_frame + 216
                if gap:
                    run.frame = end_frame
                    run.match.update(run.frame * combat.TICK, run.frame)
                else:
                    while run.frame < end_frame:
                        run.step({})
                self.assertGreater(vitals.last_damage_at, first_hit + 3 + combat.TICK)
                self.assertLess(vitals.last_damage_at, fight.now)
                self.assertEqual(vitals.now[combat.SHIELDS_POOL], 0)
                recovered = 30 * (vitals.last_damage_at - combat.TICK - first_hit - 3)
                self.assertAlmostEqual(vitals.now[combat.HEALTH_POOL], 25 + recovered)
                results.append(round(vitals.last_damage_at, 6))
        self.assertEqual(results[:1], results[1:])

    @patch.object(FakeClient, "bot_stream", new=lambda _client, _body: None, create=True)
    def test_projected_barrier_expires_between_actual_sniper_hits_in_retained_update(self):
        stepped = self.projected_barrier_expiry_trace(gap=False)
        self.assertEqual(self.projected_barrier_expiry_trace(gap=True), stepped)

    def projected_barrier_expiry_trace(self, *, gap):
        run, player, caster, early = self.projected_scene("Sniper", distance=4)
        definition = next(d for d in self.definitions.values() if d.name == "Sniper")
        late = npc.spawn(run.match, definition, (-4, 1, 0), team=early.team)
        fight = run.match.combat
        self.engage_npc(run, player, early)
        for _ in range(8):
            run.step({})
        self.engage_npc(run, player, late)
        run.step({caster: (-16384, 0, 16)})
        cast_frame = run.frame
        _, pet, barrier = fight.zarya_barriers.contact(player)
        for _ in range(115):
            run.step({})
        self.assertTrue(pet.active)
        self.assertEqual(barrier.remaining, 200)

        attacks = []
        resolve = fight._damage

        def trace(shooter, target, amount, *args, **kwargs):
            applied = resolve(shooter, target, amount, *args, **kwargs)
            if target.owner is player:
                attacks.append((shooter.entity, fight.tick - cast_frame, amount, applied, barrier.remaining))
            return applied

        with patch.object(fight, "_damage", side_effect=trace):
            if gap:
                run.frame += 20
                run.match.update(run.frame * combat.TICK, run.frame)
            else:
                for _ in range(20):
                    run.step({})
        self.assertEqual(attacks, [
            (early.entity, 119, 100, False, 100),
            (late.entity, 127, 100, True, 100),
        ])
        self.assertFalse(pet.active)
        self.assertIsNone(fight.zarya_barriers.contact(player))
        vitals = health(run, player)
        self.assertEqual(vitals.now[combat.HEALTH_POOL], 100)
        self.assertEqual(vitals.now[combat.SHIELDS_POOL], 25)
        self.assertAlmostEqual(vitals.last_damage_at, (cast_frame + 127) * combat.TICK)
        return attacks

    def test_three_working_npc_weapons_miss_are_blocked_by_walls_and_protect_allies(self):
        working = {'Trooper', 'Sniper', 'Heavy Assault'}
        for definition in self.definitions.values():
            if definition.name not in working:
                continue
            for reason in ('miss', 'wall', 'ally'):
                with self.subTest(npc=definition.name, reason=reason):
                    run, player, bot, _target = self.create(definition)
                    fight = run.match.combat
                    if reason == 'ally':
                        run.match.game_map = run.match.game_map.__class__(
                            **{**run.match.game_map.__dict__, 'free_for_all': False})
                        bot.team = player.team
                    # Isolate raw weapon execution: the manual engagement host would
                    # re-aim or suppress these intentional miss/LOS/team inputs.
                    bot.spawner['manual_spawn'] = False
                    bot.yaw, bot.firing = (0. if reason == 'miss' else 32768.), True
                    bot.body_script.component.edges([(20, True)])
                    wall = World([(-5, 0, 2, 5, 0, 2, 5, 8, 2),
                                  (-5, 0, 2, 5, 8, 2, -5, 8, 2)])
                    collision = wall if reason == 'wall' else run.match.collision_world
                    before = health(run, player).total
                    with (
                        patch.object(FakeClient, 'bot_stream', return_value=None, create=True),
                        patch.object(fight, '_collision', return_value=collision),
                        patch.object(fight, '_shoot', wraps=fight._shoot) as shots,
                    ):
                        for _ in range(200):
                            run.step({})
                        self.assertTrue(any(call.args[0] is bot for call in shots.call_args_list))
                    self.assertEqual(health(run, player).total, before)

    def enforcer_volley(self, distance):
        definition = next(d for d in self.definitions.values() if d.name == 'Enforcer')
        run, player, bot, _ = self.create(definition, distance=distance)
        fight = run.match.combat
        player.mover.yaw, player.mover.pitch = aim(fight._eye(player), (0, 2, distance))
        self.assertEqual(npc_control.start(run.match, player), bot.entity)
        for _ in range(80):
            run.step({})
            if fight.rockets:
                break
        self.assertEqual(len(fight.rockets), 25)
        self.assertTrue(all(r.shooter is bot and r.speed == 80 for r in fight.rockets))
        self.assertEqual(len({r.direction for r in fight.rockets}), 25)
        self.assertEqual(health(run, player).total, 225)
        return run, player, bot

    def test_enforcer_actual_ring_pellets_travel_and_apply_range_falloff(self):
        losses = []
        with patch.object(FakeClient, 'bot_stream', return_value=None, create=True):
            for distance in (4, 14):
                run, player, _ = self.enforcer_volley(distance)
                fight = run.match.combat
                with patch.object(fight, '_damage', wraps=fight._damage) as impacts:
                    for _ in range(16):
                        run.step({})
                self.assertEqual(health(run, player).now[combat.HEALTH_POOL], 100)
                losses.append(125 - health(run, player).now[combat.SHIELDS_POOL])
                if distance == 14:
                    self.assertTrue(impacts.call_args_list)
                    self.assertTrue(all(0 < call.args[2] < 2 for call in impacts.call_args_list))
        self.assertEqual(losses[0], 50)
        self.assertGreater(losses[1], 0)
        self.assertLess(losses[1], losses[0])

    def test_enforcer_actual_flights_obey_cover_ally_miss_and_stop(self):
        with patch.object(FakeClient, 'bot_stream', return_value=None, create=True):
            for reason in ('cover', 'ally', 'miss', 'stop'):
                with self.subTest(reason=reason):
                    run, player, bot = self.enforcer_volley(4)
                    if reason == 'cover':
                        run.match.collision_world = World([(-5, 0, 2, 5, 0, 2, 5, 8, 2),
                                                           (-5, 0, 2, 5, 8, 2, -5, 8, 2)])
                    elif reason == 'ally':
                        run.match.game_map = replace(run.match.game_map, free_for_all=False)
                        bot.team = player.team
                    elif reason == 'miss':
                        player.mover.position[0] = 3
                        player.mover.body = None
                    else:
                        npc_control.stop(run.match)
                        self.assertFalse(run.match.combat.rockets)
                    for _ in range(16):
                        run.step({})
                    self.assertEqual(health(run, player).total, 225)

    @unittest.skipUnless(os.environ.get("ORPHEUS_EXTRA_GRAPH_BUNDLES"), "requires the portal actors")
    @patch.object(FakeClient, "bot_stream", new=lambda _client, _body: None, create=True)
    def test_assassin_pin_blocks_teleporter_until_actual_projected_rescue(self):
        for held in (False, True):
            with self.subTest(held=held):
                self.check_assassin_teleporter_rescue(held)

    def check_assassin_teleporter_rescue(self, held):
        fixture = portal_tests.TeleporterTests()
        run, owner, _, pair = fixture.deploy()
        fight = run.match.combat
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        caster = run.players[1]
        caster.team = owner.team
        self.assertTrue(run.match.switch_hero(caster, heroes()[zarya_barriers.HERO]))
        fixture.place(owner, (.8, 0, 0))
        fixture.place(caster, (.8, 0, -3))
        definition = next(d for d in self.definitions.values() if d.name == "Assassin")
        bot = npc.spawn(run.match, definition, (2.2, 0, 0), team=1 - owner.team)
        fixture.until(run, pair.ready_at)
        self.engage_npc(run, owner, bot)
        run.step({owner: (0, 0, 4)})
        pool = health(run, owner)
        self.assertEqual(fight.pins.get(bot.entity), owner.body)
        self.assertTrue(fight.is_stunned(owner))
        self.assertEqual(pool.now[combat.SHIELDS_POOL], 100)
        self.assertNotIn(owner.body, fight.portals.transits)
        self.assertAlmostEqual(owner.mover.position[2], 0)
        hit_time = pool.last_damage_at
        for _ in range(6):
            run.step({owner: (0, 0, 4 if held else 0)})
            self.assertEqual(fight.pins.get(bot.entity), owner.body)
            self.assertNotIn(owner.body, fight.portals.transits)
        run.step({owner: (0, 0, 4 if held else 0), caster: (0, 0, 16)})
        self.assertEqual(fight.pins.get(bot.entity), owner.body)
        self.assertIs(fight.zarya_barriers.contact(owner)[0], caster)
        self.assertNotIn(owner.body, fight.portals.transits)

        run.step({owner: (0, 0, 4 if held else 0)})
        self.assertNotIn(bot.entity, fight.pins)
        self.assertFalse(fight.is_stunned(owner))
        exit_z = pair.endpoints[0].position[2]
        self.assertAlmostEqual(owner.mover.position[2], exit_z if held else 0)
        if not held:
            self.assertNotIn(owner.body, fight.portals.transits)
            run.step({owner: (0, 0, 4)})
        self.assertIn(owner.body, fight.portals.transits)
        self.assertAlmostEqual(owner.mover.position[2], exit_z)
        for _ in range(8):
            run.step({owner: (0, 0, 4)})
        self.assertFalse(bot.attack_input)
        self.assertNotIn(bot.entity, fight.pins)
        self.assertEqual(pool.last_damage_at, hit_time)
        self.assertEqual(pool.total, 200)
        self.assertIn(pair, fight.portals.pairs)

    @unittest.skipUnless(os.environ.get("ORPHEUS_EXTRA_GRAPH_BUNDLES"), "requires the portal actors")
    @patch.object(FakeClient, "bot_stream", new=lambda _client, _body: None, create=True)
    def test_heavy_capture_rescue_preserves_teleporter_range_gate(self):
        for in_range in (True, False):
            with self.subTest(in_range=in_range):
                self.check_heavy_capture_teleporter_rescue(in_range)

    def check_heavy_capture_teleporter_rescue(self, in_range):
        fixture = portal_tests.TeleporterTests()
        run, owner, _, pair = fixture.deploy()
        fight = run.match.combat
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        caster = run.players[1]
        caster.team = owner.team
        self.assertTrue(run.match.switch_hero(caster, heroes()[zarya_barriers.HERO]))
        entrance = pair.endpoints[1]
        # Keep the first captured pose inside the interaction sphere.
        charge_x = entrance.position[0] + 1.05
        fixture.place(owner, (charge_x, 0, 0))
        fixture.place(caster, (4, 0, 0))
        definition = next(d for d in self.definitions.values() if d.name == "Heavy Assault")
        bot = npc.spawn(run.match, definition, (charge_x, 0, 8), team=1 - owner.team)
        fixture.until(run, pair.ready_at)
        owner.mover.yaw, owner.mover.pitch = aim(fight._eye(owner), (charge_x, 1, 8))
        self.assertEqual(npc_control.start_charge(run.match, owner), bot.entity)
        for _ in range(120):
            run.step({})
            if fight.pins.get(bot.entity) == owner.body:
                break
        self.assertEqual(fight.pins.get(bot.entity), owner.body)
        attachment = fight.heavy_charge_pins[bot.entity]
        radius = graph.graph(0x157D).node(1).field("m_radius")["m_value"]
        self.assertLess(math.dist(owner.mover.position, entrance.position), radius)
        for _ in range(12 if not in_range else 0):
            run.step({owner: (0, 0, 4)})
            self.assertEqual(fight.pins.get(bot.entity), owner.body,
                             (bot.position, owner.mover.position, health(run, entrance).total))
            self.assertNotIn(owner.body, fight.portals.transits)
        angles = aim(fight._eye(caster), fight._eye(owner))
        run.step({owner: (0, 0, 4), caster: (*angles, 16)})
        self.assertEqual(fight.pins.get(bot.entity), owner.body)
        self.assertNotIn(owner.body, fight.portals.transits)
        self.assertEqual(math.dist(owner.mover.position, entrance.position) < radius, in_range)
        self.assertIs(fight.zarya_barriers.contact(owner)[0], caster)
        run.step({owner: (0, 0, 4)})
        self.assertNotIn(bot.entity, fight.pins)
        self.assertNotIn(bot.entity, fight.heavy_charge_pins)
        self.assertNotIn(attachment.id, attachment.component.instances)
        self.assertFalse(fight.is_stunned(owner))
        self.assertEqual(heavy_charge.phase(heavy_charge.instance(bot)), 0)
        self.assertEqual(owner.body in fight.portals.transits, in_range)
        if in_range:
            self.assertAlmostEqual(owner.mover.position[2], pair.endpoints[0].position[2])
        else:
            self.assertGreater(math.dist(owner.mover.position, entrance.position), radius)
            for _ in range(80):
                if math.dist(owner.mover.position, entrance.position) < radius - .05:
                    break
                eye = fight._eye(owner)
                yaw, _ = aim(eye, (entrance.position[0], eye[1], entrance.position[2]))
                run.frame += 1
                run.match.update(run.frame * combat.TICK, run.frame)
                owner.take_commands([Command(run.frame, yaw=yaw, forward=127, buttons=4)])
                self.assertNotIn(owner.body, fight.portals.transits)
            self.assertLess(math.dist(owner.mover.position, entrance.position), radius)
            run.step({})
            run.step({owner: (0, 0, 4)})
            self.assertIn(owner.body, fight.portals.transits)
            self.assertAlmostEqual(owner.mover.position[2], pair.endpoints[0].position[2])
        self.assertEqual(health(run, owner).total, 225)
        self.assertIn(pair, fight.portals.pairs)

    @unittest.skipUnless(os.environ.get("ORPHEUS_EXTRA_GRAPH_BUNDLES"), "requires the portal actors")
    @patch.object(FakeClient, "bot_stream", new=lambda _client, _body: None, create=True)
    def test_heavy_impact_releases_teleporter_capture_and_checks_endpoint_survival(self):
        for weakened in (False, True):
            with self.subTest(weakened=weakened):
                impact = self.check_heavy_impact_teleporter(weakened, gap=None)
                self.assertEqual(self.check_heavy_impact_teleporter(weakened, gap=impact), impact)

    def check_heavy_impact_teleporter(self, weakened, *, gap):
        fixture = portal_tests.TeleporterTests()
        run, owner, instance, pair = fixture.deploy()
        fight = run.match.combat
        enemy = run.players[1]
        entrance = pair.endpoints[1]
        device = fight.vitals[entrance.entity]
        fixture.place(owner, (.8, 0, 0))
        fixture.until(run, pair.ready_at)
        if weakened:
            fixture.place(enemy, (0, 0, 3))
            angles = aim(fight._eye(enemy), (0, .85, 0))
            for _ in range(40):
                if device.total <= 220:
                    break
                run.step({enemy: (*angles, 1)})
        self.assertEqual(device.total, 220 if weakened else 300)
        fixture.place(enemy, (40, 0, 40))
        definition = next(d for d in self.definitions.values() if d.name == "Heavy Assault")
        bot = npc.spawn(run.match, definition, (.8, 0, 8), team=1 - owner.team)
        owner.mover.yaw, owner.mover.pitch = aim(fight._eye(owner), (.8, 1, 8))
        self.assertEqual(npc_control.start_charge(run.match, owner), bot.entity)
        for _ in range(120):
            run.step({})
            if fight.pins.get(bot.entity) == owner.body:
                break
        self.assertEqual(fight.pins.get(bot.entity), owner.body)
        attachment = fight.heavy_charge_pins[bot.entity]
        before_impact = device.total
        capture_frame = run.frame
        if gap is None:
            for _ in range(20):
                run.step({owner: (0, 0, 4)})
                if bot.entity not in fight.pins:
                    break
                self.assertNotIn(owner.body, fight.portals.transits)
        else:
            run.frame += gap
            run.match.update(run.frame * combat.TICK, run.frame)
            owner.take_commands([Command(run.frame, buttons=4)])
        self.assertNotIn(bot.entity, fight.pins)
        self.assertNotIn(attachment.id, attachment.component.instances)
        self.assertFalse(fight.is_stunned(owner))
        self.assertGreater(before_impact, device.total)
        self.assertEqual(health(run, owner).total, 225)
        if weakened:
            self.assertTrue(device.dead)
            self.assertEqual((device.killer, device.death_tick), (bot.entity, run.frame))
            self.assertNotIn(pair, fight.portals.pairs)
            self.assertNotIn(owner.body, fight.portals.transits)
            self.assertGreater(instance.find_var(0, 61).value(), 9)
        else:
            self.assertEqual(device.total, 75)
            self.assertIn(pair, fight.portals.pairs)
            self.assertIn(owner.body, fight.portals.transits)
            self.assertAlmostEqual(owner.mover.position[2], pair.endpoints[0].position[2])
        return run.frame - capture_frame

    @unittest.skipUnless(os.environ.get("ORPHEUS_EXTRA_GRAPH_BUNDLES"), "requires the portal actors")
    @patch.object(FakeClient, "bot_stream", new=lambda _client, _body: None, create=True)
    def test_live_sniper_shot_keeps_entrance_aim_then_next_shot_tracks_teleporter_exit(self):
        for transit in (False, True):
            with self.subTest(transit=transit):
                self.check_npc_teleporter_flight("Sniper", transit)

    @unittest.skipUnless(os.environ.get("ORPHEUS_EXTRA_GRAPH_BUNDLES"), "requires the portal actors")
    @patch.object(FakeClient, "bot_stream", new=lambda _client, _body: None, create=True)
    def test_live_enforcer_volley_keeps_all_entrance_trajectories_during_teleport(self):
        for transit in (False, True):
            with self.subTest(transit=transit):
                self.check_npc_teleporter_flight("Enforcer", transit)

    def check_npc_teleporter_flight(self, name, transit):
        fixture = portal_tests.TeleporterTests()
        run, owner, _, pair = fixture.deploy()
        fight = run.match.combat
        fixture.place(run.players[1], (40, 0, 40))
        offset, distance, count, damage = (.8, 12, 1, 100) if name == "Sniper" else (1.1, 4, 25, 50)
        fixture.place(owner, (offset, 0, 0))
        definition = next(d for d in self.definitions.values() if d.name == name)
        bot = npc.spawn(run.match, definition, (offset, 0, distance), team=1 - owner.team)
        fixture.until(run, pair.ready_at)
        self.engage_npc(run, owner, bot)
        for _ in range(180):
            run.step({})
            if fight.rockets:
                break
        entrance_shots = tuple(fight.rockets)
        self.assertEqual(len(entrance_shots), count)
        original_aims = [shot.direction for shot in entrance_shots]
        self.assertEqual(len(set(original_aims)), count)
        self.assertTrue(all(shot.shooter is bot and shot.direction[2] < 0 for shot in entrance_shots))
        pool = health(run, owner)
        self.assertEqual(pool.total, 225)

        run.step({owner: (0, 0, 4 if transit else 0)})
        self.assertEqual(tuple(fight.rockets), entrance_shots)
        self.assertEqual(owner.body in fight.portals.transits, transit)
        exit_z = pair.endpoints[0].position[2]
        self.assertAlmostEqual(owner.mover.position[2], exit_z if transit else 0)
        for _ in range(100):
            if not any(shot in fight.rockets for shot in entrance_shots):
                break
            run.step({})
        self.assertFalse(any(shot in fight.rockets for shot in entrance_shots))
        self.assertTrue(all(shot.shooter is bot for shot in entrance_shots))
        self.assertEqual([shot.direction for shot in entrance_shots], original_aims)
        self.assertEqual(pool.total, 225 if transit else 225 - damage)
        self.assertEqual([fight.vitals[actor.entity].total for actor in pair.endpoints], [300, 300])
        if not transit or name != "Sniper":
            return
        for _ in range(180):
            run.step({})
            if fight.rockets:
                break
        [exit_shot] = fight.rockets
        self.assertIs(exit_shot.shooter, bot)
        self.assertGreater(exit_shot.direction[2], 0)
        for _ in range(40):
            if exit_shot not in fight.rockets:
                break
            run.step({})
        self.assertNotIn(exit_shot, fight.rockets)
        self.assertEqual(pool.total, 125)
        self.assertEqual([fight.vitals[actor.entity].total for actor in pair.endpoints], [300, 300])

    @unittest.skipUnless(os.environ.get("ORPHEUS_EXTRA_GRAPH_BUNDLES"), "requires the portal actors")
    @patch.object(FakeClient, "bot_stream", new=lambda _client, _body: None, create=True)
    def test_actual_sniper_destroys_either_teleporter_end_before_same_frame_interact(self):
        for at_exit in (False, True):
            with self.subTest(at_exit=at_exit):
                self.check_sniper_breaks_teleporter(at_exit)

    def check_sniper_breaks_teleporter(self, at_exit):
        fixture = portal_tests.TeleporterTests()
        run, owner, instance, pair = fixture.deploy()
        fight = run.match.combat
        fixture.place(run.players[1], (40, 0, 40))
        fixture.until(run, pair.ready_at)
        if at_exit:
            run.step({owner: (0, 0, 4)})
            self.assertIn(owner.body, fight.portals.transits)
            fixture.until(run, fight.portals.transits[owner.body][1])
            self.assertNotIn(owner.body, fight.portals.transits)
        endpoint = pair.endpoints[0 if at_exit else 1]
        x, _, z = endpoint.position
        fixture.place(owner, (x + 1.2, 0, z - .9))
        definition = next(d for d in self.definitions.values() if d.name == "Sniper")
        bot = npc.spawn(run.match, definition, (x, -.85, z + 2.5), team=1 - owner.team)
        self.engage_npc(run, owner, bot)
        fixture.place(owner, (x, 0, z - .9))
        pool = health(run, owner)
        device = fight.vitals[endpoint.entity]
        self.assertEqual(device.total, 300)
        for expected_shields in (150, 50):
            for _ in range(180):
                run.step({})
                if device.now[combat.SHIELDS_POOL] <= expected_shields:
                    break
            self.assertEqual(device.now[combat.SHIELDS_POOL], expected_shields)
            self.assertEqual(device.now[combat.HEALTH_POOL], 50)
            self.assertEqual(pool.total, 225)
            self.assertIn(pair, fight.portals.pairs)
        for _ in range(180):
            run.step({})
            if fight.rockets:
                break
        [lethal] = fight.rockets
        self.assertIs(lethal.shooter, bot)
        self.advance_to_projectile_contact(run, lethal, endpoint)
        self.assertIn(pair, fight.portals.pairs)
        before_interact = owner.mover.position[2]
        self.assertTrue(fight.portals.visible(owner, endpoint))
        self.assertLess(abs(z - before_interact), graph.graph(0x157D).node(1).field("m_radius")["m_value"])
        run.step({owner: (0, 0, 4)})
        self.assertTrue(device.dead)
        self.assertEqual(device.killer, bot.entity)
        self.assertEqual(device.death_tick, run.frame)
        self.assertNotIn(pair, fight.portals.pairs)
        self.assertTrue(all(actor not in run.match.bots for actor in pair.endpoints))
        self.assertNotIn(owner.body, fight.portals.transits)
        self.assertEqual(owner.mover.position[2], before_interact)
        self.assertGreater(instance.find_var(0, 61).value(), 9)
        self.assertEqual(pool.total, 225)
        for _ in range(180):
            if pool.total < 225:
                break
            run.step({})
        self.assertEqual(pool.total, 125)

    def advance_to_projectile_contact(self, run, rocket, recipient):
        fight = run.match.combat
        target = next(target for target in fight._targets() if target.owner is recipient)
        a, b = target.segment()
        for _ in range(20):
            self.assertIn(rocket, fight.rockets)
            contact = combat.ray_capsule(rocket.position, rocket.direction, a, b, target.radius)
            self.assertIsNotNone(contact)
            if contact <= rocket.speed * combat.TICK:
                return
            run.step({})
        self.fail("projectile did not reach the recipient capsule")

    @unittest.skipUnless(os.environ.get("ORPHEUS_EXTRA_GRAPH_BUNDLES"), "requires the portal actors")
    @patch.object(FakeClient, "bot_stream", new=lambda _client, _body: None, create=True)
    def test_lethal_sniper_blocks_transit_and_replacement_owner_can_use_and_dismiss_pair(self):
        fixture = portal_tests.TeleporterTests()
        run, owner, _, pair = fixture.deploy()
        fight = run.match.combat
        fixture.place(run.players[1], (40, 0, 40))
        fixture.place(owner, (.8, 0, 0))
        definition = next(d for d in self.definitions.values() if d.name == "Sniper")
        bot = npc.spawn(run.match, definition, (.8, 0, 4), team=1 - owner.team)
        fixture.until(run, pair.ready_at)
        self.engage_npc(run, owner, bot)
        pool = health(run, owner)
        for expected in (125, 25):
            for _ in range(180):
                run.step({})
                if pool.total <= expected:
                    break
            self.assertEqual(pool.total, expected)
        for _ in range(180):
            run.step({})
            if fight.rockets:
                break
        [lethal] = fight.rockets
        self.assertIs(lethal.shooter, bot)
        self.advance_to_projectile_contact(run, lethal, owner)
        corpse = owner.body
        run.step({owner: (0, 0, 4)})
        self.assertTrue(pool.dead)
        self.assertEqual((pool.killer, pool.death_tick), (bot.entity, run.frame))
        self.assertEqual(owner.body, corpse)
        self.assertNotIn(corpse, fight.portals.transits)
        self.assertEqual(owner.mover.position[2], 0)
        self.assertIn(pair, fight.portals.pairs)
        self.assertEqual(npc_control.stop(run.match), 1)
        deadline, = (when for when, victim in fight.respawns if victim is owner)
        fixture.until(run, deadline + combat.TICK)
        self.assertNotEqual(owner.body, corpse)
        self.assertEqual(health(run, owner).total, 225)
        self.assertIn(pair, fight.portals.pairs)
        fixture.place(owner, (.8, 0, 0))
        run.step({owner: (0, 0, 4)})
        self.assertIn(owner.body, fight.portals.transits)
        self.assertAlmostEqual(owner.mover.position[2], pair.endpoints[0].position[2])
        replacement = next(i for i in owner.body_script.component.instances.values()
                           if i.graph.index == 0x1571)
        preview = replacement.node_state(replacement.graph.node(69))
        run.step({owner: (0, 0, 16)})
        self.assertFalse(fight.portals.pairs)
        self.assertTrue(all(actor not in run.match.bots for actor in pair.endpoints))
        self.assertGreater(replacement.find_var(0, 61).value(), 9)
        self.assertFalse(preview.active)
        fixture.until(run, fight.now + 10.1)
        run.step({owner: (0, 0, 16)})
        self.assertTrue(preview.active)
        for _ in range(3):
            run.step({owner: (0, 0, 16)})
        run.step({owner: (0, 0, 1)})
        [new_pair] = fight.portals.pairs
        self.assertEqual(new_pair.owner_body, owner.body)
        self.assertTrue({actor.entity for actor in pair.endpoints}.isdisjoint(
            actor.entity for actor in new_pair.endpoints))

    @unittest.skipUnless(os.environ.get("ORPHEUS_EXTRA_GRAPH_BUNDLES"), "requires the portal actors")
    @patch.object(FakeClient, "bot_stream", new=lambda _client, _body: None, create=True)
    def test_actual_npc_hitscan_lands_before_same_frame_teleport(self):
        for name, amount in (("Trooper", 20), ("Heavy Assault", 5)):
            with self.subTest(npc=name):
                expected_tick = self.npc_hitscan_teleport_trace(name, amount, transit_tick=None)
                self.assertEqual(self.npc_hitscan_teleport_trace(name, amount, transit_tick=expected_tick),
                                 expected_tick)

    def npc_hitscan_teleport_trace(self, name, amount, *, transit_tick):
        fixture = portal_tests.TeleporterTests()
        run, owner, _, pair = fixture.deploy()
        fight = run.match.combat
        fixture.place(run.players[1], (40, 0, 40))
        fixture.place(owner, (.8, 0, 0))
        definition = next(d for d in self.definitions.values() if d.name == name)
        bot = npc.spawn(run.match, definition, (.8, 0, 4), team=1 - owner.team)
        fixture.until(run, pair.ready_at)
        self.engage_npc(run, owner, bot)
        initial_frame = run.frame
        pool = health(run, owner)
        self.assertEqual(pool.total, 225)
        for frame in range(1, 90):
            run.step({owner: (0, 0, 4 if frame == transit_tick else 0)})
            if pool.last_damage_at is not None:
                break
        self.assertFalse(fight.rockets)
        self.assertEqual(pool.total, 225 - amount)
        self.assertEqual(pool.last_damage_at, fight.now)
        self.assertEqual(owner.body in fight.portals.transits, transit_tick is not None)
        self.assertAlmostEqual(owner.mover.position[2], pair.endpoints[0].position[2] if transit_tick else 0)
        self.assertEqual([fight.vitals[actor.entity].total for actor in pair.endpoints], [300, 300])
        return run.frame - initial_frame

    def test_assassin_actual_contact_pin_delayed_damage_and_stop(self):
        definition = next(d for d in self.definitions.values() if d.name == 'Assassin')
        run, player, bot, _ = self.create(definition, distance=1.5)
        fight = run.match.combat
        player.mover.yaw, player.mover.pitch = aim(fight._eye(player), (0, 2, 1.5))
        self.assertEqual(npc_control.start(run.match, player), bot.entity)
        self.assertEqual(npc_control.button(bot), 36)
        before = health(run, bot).total
        with patch.object(FakeClient, 'bot_stream', return_value=None, create=True):
            run.step({player: (0, 0, 1)})
            self.assertEqual(health(run, player).now[combat.SHIELDS_POOL], 100)
            self.assertEqual(fight.pins.get(bot.entity), player.body)
            fire(run, player, 55)
            self.assertEqual(health(run, player).now[combat.SHIELDS_POOL], 100)
            self.assertEqual(health(run, bot).total, before)
            for _ in range(20):
                previous = health(run, player).total
                fire(run, player)
                if previous - health(run, player).total > 1.5:
                    break
            self.assertAlmostEqual(previous - health(run, player).total, 1.6, places=5)
            saved = json.dumps(export_component_vm(bot.body_script.component))
            self.assertIn('"manual_target"', saved)
            self.assertIn('"last_applied"', saved)
            npc_control.stop(run.match)
            self.assertFalse(fight.pins)
            after_stop = health(run, player).total
            fire(run, player, 12)
            self.assertEqual(health(run, player).total, after_stop)
            self.assertLess(health(run, bot).total, before)

    @patch.object(FakeClient, "bot_stream", new=lambda _client, _body: None, create=True)
    def test_actual_assassin_pin_blocks_beam_while_started_reload_finishes(self):
        definition = next(d for d in self.definitions.values() if d.name == "Assassin")
        run, owner, bot, _ = self.create(definition, distance=1.5)
        fight = run.match.combat
        fire(run, owner, 50, yaw=-32768)
        fire(run, owner, 20, buttons=0)
        instance = next(i for i in owner.body_script.component.instances.values() if i.graph.index == 0x157C)
        ammo = instance.find_var(0, 53).value()
        self.assertGreater(ammo, 0)
        self.assertLess(ammo, 70)
        fire(run, owner, buttons=0x400)
        refill = instance.node_state(instance.graph.node(118))
        animation = instance.node_state(instance.graph.node(14))
        self.assertTrue(refill.active)
        self.assertTrue(animation.active)
        timeout = refill.evaluate(refill.node.field("m_timeout"), watch=False)
        refill_at = refill.start + round(timeout * 1000)
        end_at = animation.start + 1500
        self.engage_npc(run, owner, bot)
        run.step({})
        self.assertEqual(fight.pins.get(bot.entity), owner.body)
        self.assertEqual(health(run, owner).total, 200)
        npc_pools = health(run, bot)
        before = npc_pools.total
        for _ in range(100):
            fire(run, owner)
            self.assertEqual(fight.pins.get(bot.entity), owner.body)
            self.assertTrue(fight.is_stunned(owner))
            self.assertEqual(npc_pools.total, before)
            now = instance.component.now
            self.assertEqual(instance.find_var(0, 53).value(), ammo if now < refill_at else 70)
            self.assertEqual(refill.active, now < refill_at)
            self.assertEqual(animation.active, now < end_at)
            self.assertEqual(instance.find_var(0, 51).value(), now < end_at)
        self.assertGreater(instance.component.now, end_at)
        self.assertLess(health(run, owner).total, 200)
        npc_control.stop(run.match)
        self.assertFalse(fight.is_stunned(owner))
        fire(run, owner, 10)
        self.assertLess(npc_pools.total, before)
        self.assertLess(instance.find_var(0, 53).value(), 70)

    @patch.object(FakeClient, "bot_stream", new=lambda _client, _body: None, create=True)
    def test_assassin_pin_clears_buffered_melee_across_command_gap(self):
        for release, rescue_frame in (
            ("no pin", None),
            ("manual stop", None),
            ("projected barrier", 55),
            ("projected barrier", 59),
            ("projected barrier", 63),
        ):
            with self.subTest(release=release, rescue_frame=rescue_frame):
                self.check_assassin_melee_gap(release, rescue_frame)

    def check_assassin_melee_gap(self, release, rescue_frame):
        run, player, caster, bot = self.projected_scene()
        engaged = release != "no pin"
        protected_at_contact = rescue_frame == 55
        pinned = engaged and not protected_at_contact
        fight = run.match.combat
        target_pools = health(run, bot)
        before = target_pools.total
        melee = 0x800
        fire(run, player, buttons=melee)
        first_frame = run.frame
        self.assertEqual(target_pools.total, before - 30)
        fire(run, player, 53, buttons=0)
        fire(run, player, buttons=melee)
        run.step({caster: (-16384, 0, 16)} if protected_at_contact else {})
        self.assertEqual(run.frame - first_frame, 55)
        self.assertEqual(target_pools.total, before - 30)
        if protected_at_contact:
            _, _, initial_barrier = fight.zarya_barriers.contact(player)
            self.assertEqual(initial_barrier.remaining, 200)
        if engaged:
            self.engage_npc(run, player, bot)

        run.players.remove(player)
        for age in range(56, 119):
            commands = {caster: (-16384, 0, 16)} if age == rescue_frame else {}
            run.step(commands)
            if age in (58, 62):
                still_pinned = pinned and (rescue_frame is None or age < rescue_frame)
                self.assertEqual(fight.pins.get(bot.entity), player.body if still_pinned else None)
        run.players.append(player)
        if pinned:
            self.assertLess(health(run, player).total, 225)
        if protected_at_contact:
            self.assertEqual(health(run, player).total, 225)
            self.assertLess(initial_barrier.remaining, 200)
        if release == "manual stop":
            self.assertEqual(npc_control.stop(run.match), 1)
        if release == "projected barrier":
            owner, _, _ = fight.zarya_barriers.contact(player)
            self.assertIs(owner, caster)
            self.assertTrue(bot.firing)
            self.assertEqual(bot.attack_target, player.entity)
            self.assertNotIn(bot.entity, fight.pins)
        self.assertFalse(fight.is_stunned(player))
        fire(run, player, buttons=0)
        self.assertEqual(run.frame - first_frame, 119)
        expected = before - (30 if pinned else 60)
        self.assertEqual(target_pools.total, expected)
        fire(run, player, 10, buttons=0)
        self.assertEqual(target_pools.total, expected)
        fire(run, player, buttons=melee)
        self.assertEqual(target_pools.total, expected - 30)

    @patch.object(FakeClient, "bot_stream", new=lambda _client, _body: None, create=True)
    def test_actual_assassin_pin_distinguishes_pending_orb_from_in_flight_orb(self):
        for case in ("manual stop", "projected rescue", "projected prevention", "already flying"):
            with self.subTest(case=case):
                self.check_assassin_orb_boundary(case)

    def check_assassin_orb_boundary(self, case):
        run, owner, caster, bot = self.projected_scene()
        fight = run.match.combat
        component = owner.body_script.component
        weapon = next(i for i in component.instances.values() if i.graph.index == 0x157C)
        ammo = weapon.find_var(0, 53)
        target = health(run, bot)
        before = target.total
        fire(run, owner, 90, buttons=2)
        held_ammo = ammo.value()
        self.assertGreater(held_ammo, 60)
        self.assertLess(held_ammo, 70)
        self.assertIn(29, component.held)
        self.assertFalse(fight.rockets)
        self.assertEqual(target.total, before)

        prevention = case == "projected prevention"
        already_flying = case == "already flying"
        if prevention:
            run.step({owner: (0, 0, 2), caster: (-16384, 0, 16)})
            source, _, barrier = fight.zarya_barriers.contact(owner)
            self.assertIs(source, caster)
            self.assertEqual(barrier.remaining, 200)
        if already_flying:
            fire(run, owner, buttons=0)
            [launched] = fight.rockets
            self.assertIs(launched.shooter, owner)
            self.assertEqual(ammo.value(), 60)

        self.engage_npc(run, owner, bot)
        run.step({})  # The NPC contact runs before this frame's owner release.
        self.assertEqual(target.total, before)
        self.assertEqual(health(run, owner).total, 225 if prevention else 200)
        self.assertEqual(fight.pins.get(bot.entity), None if prevention else owner.body)
        if prevention:
            self.assertEqual(barrier.remaining, 175)
            [shot] = fight.rockets
            self.assertIs(shot.shooter, owner)
        elif already_flying:
            self.assertEqual(fight.rockets, [launched])
        else:
            self.assertFalse(fight.rockets)
            self.assertFalse(component.shots)
            self.assertEqual(ammo.value(), held_ammo)
            for _ in range(10):
                fire(run, owner, buttons=2)
                self.assertTrue(fight.is_stunned(owner))
                self.assertNotIn(29, component.held)
                self.assertFalse(fight.rockets)
                self.assertEqual(ammo.value(), held_ammo)

        if case == "manual stop":
            self.assertEqual(npc_control.stop(run.match), 1)
        if case == "projected rescue":
            run.step({caster: (-16384, 0, 16)})
            source, _, barrier = fight.zarya_barriers.contact(owner)
            self.assertIs(source, caster)
            self.assertTrue(bot.firing)
            self.assertEqual(barrier.remaining, 200)
            self.assertEqual(fight.pins.get(bot.entity), owner.body)
            run.step({})
        self.assertEqual(fight.is_stunned(owner), already_flying)
        fire(run, owner, 30, buttons=0)
        self.assertFalse(fight.rockets)
        self.assertFalse(component.shots)
        if prevention or already_flying:
            self.assertEqual(ammo.value(), 60)
            self.assertAlmostEqual(before - target.total, 120, places=4)
        else:
            self.assertEqual(ammo.value(), held_ammo)
            self.assertEqual(target.total, before)
            fire(run, owner, 90, buttons=2)
            self.assertEqual(target.total, before)
            self.assertFalse(fight.rockets)
            fire(run, owner, buttons=0)
            [shot] = fight.rockets
            self.assertIs(shot.shooter, owner)
            fire(run, owner, 30, buttons=0)
            self.assertFalse(fight.rockets)
            self.assertAlmostEqual(before - target.total, 120, places=4)
            self.assertLess(ammo.value(), held_ammo)

    def projected_scene(self, name='Assassin', *, distance=1.5):
        definition = next(d for d in self.definitions.values() if d.name == name)
        run, player, bot, _ = self.create(definition, distance=distance)
        caster = run.players[1]
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        self.assertTrue(run.match.switch_hero(caster, heroes()[zarya_barriers.HERO]))
        caster.team = player.team
        caster.mover.position[:] = (3, 1, 0)
        fight = run.match.combat
        player.mover.yaw, player.mover.pitch = aim(fight._eye(player), (0, 2, distance))
        return run, player, caster, bot

    def test_projected_barrier_blocks_assassin_contact_and_pin_even_when_broken(self):
        for remaining in (200, 10):
            with self.subTest(remaining=remaining), patch.object(
                    FakeClient, 'bot_stream', return_value=None, create=True):
                run, player, caster, bot = self.projected_scene()
                fight = run.match.combat
                run.step({caster: (-16384, 0, 16)})
                _, _, barrier = fight.zarya_barriers.contact(player)
                self.assertEqual(barrier.remaining, 200)
                barrier.remaining = remaining
                self.assertEqual(npc_control.start(run.match, player), bot.entity)
                before = health(run, bot).total
                run.step({player: (0, 0, 1)})
                run.step({player: (0, 0, 1)})
                self.assertEqual(health(run, player).total, 225)
                self.assertNotIn(bot.entity, fight.pins)
                self.assertLess(health(run, bot).total, before)
                self.assertEqual(barrier.remaining, max(0, remaining - 25))
                self.assertEqual(caster.body_script.component.entity_var(87).value(), min(25, remaining) * .2)
                energy = next(i for i in caster.body_script.component.instances.values()
                              if i.graph.index == 0x0242)
                self.assertEqual(energy.find_var(0, 392).value().id, bot.entity)

    def test_projected_barrier_releases_pin_during_damage_free_wait(self):
        with patch.object(FakeClient, 'bot_stream', return_value=None, create=True):
            run, player, caster, bot = self.projected_scene()
            fight = run.match.combat
            self.assertEqual(npc_control.start(run.match, player), bot.entity)
            fire(run, player, 5)
            self.assertEqual(fight.pins.get(bot.entity), player.body)
            self.assertEqual(health(run, player).total, 200)
            before = health(run, bot).total
            run.step({player: (0, 0, 1), caster: (-16384, 0, 16)})
            _, _, barrier = fight.zarya_barriers.contact(player)
            fire(run, player)
            self.assertNotIn(bot.entity, fight.pins)
            self.assertLess(health(run, bot).total, before)
            self.assertEqual(health(run, player).total, 200)
            self.assertEqual(barrier.remaining, 200)
            self.assertFalse(caster.body_script.component.entity_var(87).value())
            for _ in range(80):
                if barrier.remaining < 200:
                    break
                run.step({})
            self.assertLess(barrier.remaining, 200)
            self.assertNotIn(bot.entity, fight.pins)
            self.assertEqual(health(run, player).total, 200)

    def test_projected_barrier_rescues_symmetra_during_actual_sustained_pin(self):
        with patch.object(FakeClient, 'bot_stream', return_value=None, create=True):
            run, player, caster, bot = self.projected_scene()
            fight = run.match.combat
            self.assertEqual(npc_control.start(run.match, player), bot.entity)
            before = health(run, bot).total
            for _ in range(85):
                fire(run, player)
                if health(run, player).total < 198:
                    break
            self.assertLess(health(run, player).total, 198)
            self.assertEqual(health(run, bot).total, before)
            self.assertEqual(fight.pins.get(bot.entity), player.body)
            run.step({player: (0, 0, 1), caster: (-16384, 0, 16)})
            health_at_cast = health(run, player).total
            fire(run, player, 5)
            self.assertEqual(health(run, player).total, health_at_cast)
            self.assertNotIn(bot.entity, fight.pins)
            self.assertLess(health(run, bot).total, before)
            self.assertGreater(caster.body_script.component.entity_var(87).value(), 0)
            self.assertLess(fight.zarya_barriers.contact(player)[2].remaining, 200)
            for _ in range(140):
                if fight.pins.get(bot.entity) == player.body:
                    break
                run.step({})
            self.assertIsNone(fight.zarya_barriers.contact(player))
            self.assertLess(health(run, player).total, health_at_cast)
            self.assertEqual(fight.pins.get(bot.entity), player.body)
            npc_control.stop(run.match)
            self.assertNotIn(bot.entity, fight.pins)

    def test_projected_barrier_intercepts_other_four_actual_talon_weapons(self):
        expected = {'Trooper': 20, 'Enforcer': 50, 'Sniper': 100, 'Heavy Assault': 5}
        for name, amount in expected.items():
            with self.subTest(npc=name), patch.object(
                    FakeClient, 'bot_stream', return_value=None, create=True):
                run, player, caster, bot = self.projected_scene(name, distance=4)
                fight = run.match.combat
                self.assertEqual(npc_control.start(run.match, player), bot.entity)
                # The sniper's initial aim lasts longer than a pre-cast barrier.
                if name == 'Sniper':
                    for _ in range(85):
                        run.step({})
                    self.assertEqual(health(run, player).total, 225)
                run.step({caster: (-16384, 0, 16)})
                _, _, barrier = fight.zarya_barriers.contact(player)
                for _ in range(120):
                    run.step({})
                    if 200 - barrier.remaining >= amount:
                        break
                self.assertAlmostEqual(200 - barrier.remaining, amount, places=4)
                energy = caster.body_script.component.entity_var(87).value()
                self.assertAlmostEqual(energy, amount * .2, places=4)
                self.assertEqual(health(run, player).total, 225)

    def test_actual_assassin_contact_damages_fortified_orisa_without_pinning(self):
        definition = next(d for d in self.definitions.values() if d.name == 'Assassin')
        run, player, bot, _ = self.create(definition, distance=1.5)
        fight = run.match.combat
        self.assertTrue(run.match.switch_hero(player, heroes()[fortify.HERO]))
        player.mover.position[:] = (0, 1, 0)
        run.step({player: (0, 0, 8)})
        self.assertIsNotNone(fortify.scale(player))
        before = health(run, player).total
        player.mover.yaw, player.mover.pitch = aim(fight._eye(player), (0, 2, 1.5))
        self.assertEqual(npc_control.start(run.match, player), bot.entity)
        with patch.object(FakeClient, 'bot_stream', return_value=None, create=True):
            for _ in range(10):
                run.step({})
                if health(run, player).total < before:
                    break
        self.assertAlmostEqual(before - health(run, player).total, 10, places=4)
        self.assertNotIn(bot.entity, fight.pins)

    def test_assassin_contact_loses_scope_on_range_cover_and_ally(self):
        definition = next(d for d in self.definitions.values() if d.name == 'Assassin')
        with patch.object(FakeClient, 'bot_stream', return_value=None, create=True):
            for reason in ('range', 'cover', 'ally'):
                with self.subTest(reason=reason):
                    run, player, bot, _ = self.create(definition, distance=1.5)
                    fight = run.match.combat
                    player.mover.yaw, player.mover.pitch = aim(fight._eye(player), (0, 2, 1.5))
                    self.assertEqual(npc_control.start(run.match, player), bot.entity)
                    run.step({})
                    self.assertEqual(fight.pins.get(bot.entity), player.body)
                    if reason == 'range':
                        player.mover.position[0] = 4
                        player.mover.body = None
                    elif reason == 'cover':
                        run.match.collision_world = World([(-5, 0, .75, 5, 0, .75, 5, 8, .75),
                                                           (-5, 0, .75, 5, 8, .75, -5, 8, .75)])
                    else:
                        run.match.game_map = replace(run.match.game_map, free_for_all=False)
                        bot.team = player.team
                    for _ in range(80):
                        run.step({})
                    self.assertFalse(fight.pins)
                    self.assertEqual(health(run, player).total, 200)

    def test_assassin_pin_releases_on_actual_source_death(self):
        definition = next(d for d in self.definitions.values() if d.name == 'Assassin')
        run, player, bot, _ = self.create(definition, distance=1.5)
        fight = run.match.combat
        ally = run.players[1]
        player.mover.yaw, player.mover.pitch = aim(fight._eye(player), (0, 2, 1.5))
        self.assertEqual(npc_control.start(run.match, player), bot.entity)
        with patch.object(FakeClient, 'bot_stream', return_value=None, create=True):
            run.step({})
            self.assertEqual(fight.pins.get(bot.entity), player.body)
            # Hold the source just above zero to isolate the death boundary.
            health(run, bot).now[combat.HEALTH_POOL] = 5
            ally.mover.position[:] = (0, 1, 5)
            ally.mover.body = None
            yaw, pitch = aim(fight._eye(ally), (0, 2, 1.5))
            for _ in range(60):
                run.step({ally: (yaw, pitch, 1)})
                if bot.dead:
                    break
            self.assertTrue(bot.dead)
            self.assertFalse(fight.pins)
            self.assertFalse(fight.is_stunned(player))
            self.assertEqual(health(run, player).total, 200)

    def test_actual_flashbang_on_assassin_releases_symmetra_pin(self):
        definition = next(d for d in self.definitions.values() if d.name == 'Assassin')
        run, player, bot, _ = self.create(definition, distance=1.5)
        fight = run.match.combat
        caster = run.players[1]
        self.assertTrue(run.match.switch_hero(caster, heroes()[0x02E0000000000042]))
        caster.mover.position[:] = (3, 1, 1.5)
        caster.mover.body = None
        player.mover.yaw, player.mover.pitch = aim(fight._eye(player), (0, 2, 1.5))
        self.assertEqual(npc_control.start(run.match, player), bot.entity)
        with patch.object(FakeClient, 'bot_stream', return_value=None, create=True):
            run.step({})
            self.assertEqual(fight.pins.get(bot.entity), player.body)
            yaw, pitch = aim(fight._eye(caster), (0, 2, 1.5))
            run.step({caster: (yaw, pitch, 16)})
            for _ in range(45):
                run.step({})
                if fight.is_stunned(bot):
                    break
            self.assertTrue(fight.is_stunned(bot))
            self.assertFalse(fight.pins)
            self.assertFalse(bot.dead)
            after = health(run, player).total
            for _ in range(30):
                run.step({})
            self.assertEqual(health(run, player).total, after)

    @unittest.skipUnless(os.environ.get('ORPHEUS_EXTRA_GRAPH_BUNDLES'), 'requires the barrier actor')
    def test_enforcer_pellets_damage_real_barrier_instead_of_owner(self):
        run, player, _, wall = barrier_tests.PhotonBarrierTests().deploy()
        definition = next(d for d in self.definitions.values() if d.name == 'Enforcer')
        bot = npc.spawn(run.match, definition, (0, 0, 4), team=1 - player.team)
        fight = run.match.combat
        player.mover.yaw, player.mover.pitch = aim(fight._eye(player), (0, 1, 4))
        self.assertEqual(npc_control.start(run.match, player), bot.entity)
        before = fight.vitals[wall.bot.entity].total
        with patch.object(FakeClient, 'bot_stream', return_value=None, create=True):
            for _ in range(100):
                run.step({})
                if fight.vitals[wall.bot.entity].total < before:
                    break
            for _ in range(8):
                run.step({})
        self.assertEqual(before - fight.vitals[wall.bot.entity].total, 50)
        self.assertEqual(health(run, player).total, 225)

    def test_charged_orb_commands_fly_and_remove_actual_pools_for_every_npc(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                run, player, bot, _target = self.create(definition)
                before = health(run, bot).total
                fire(run, player, 90, buttons=2)
                self.assertEqual(health(run, bot).total, before)
                fire(run, player, 30, buttons=0)
                self.assertAlmostEqual(before - health(run, bot).total, 120, places=4)

    @patch.object(FakeClient, "bot_stream", new=lambda _client, _body: None, create=True)
    def test_all_five_actual_npc_kills_discard_pending_orb_before_replacement(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                self.check_pending_orb_at_npc_death(definition)

    def stage_orb_npc_lethal(self, definition, death_after):
        distance = 1.5 if definition.name == "Assassin" else 4
        run, owner, bot, _ = self.create(definition, distance=distance)
        own_pool = health(run, owner)
        own_pool.now[combat.HEALTH_POOL] = 1
        own_pool.now[combat.SHIELDS_POOL] = 0
        fire(run, owner, 90, buttons=0)
        # Stagger actual attack and charge starts using each NPC's measured first hit.
        first_hit = {"Trooper": 22, "Enforcer": 66, "Sniper": 128, "Assassin": 1, "Heavy Assault": 32}
        charge_lead = death_after - first_hit[definition.name]
        charge_started = run.frame + max(0, -charge_lead)
        engage_at = run.frame + max(0, charge_lead)

        def step(buttons):
            if run.frame == engage_at:
                self.engage_npc(run, owner, bot)
            fire(run, owner, buttons=buttons)

        while run.frame < charge_started:
            step(0)
            self.assertFalse(own_pool.dead)
        return run, owner, bot, charge_started, step

    def check_pending_orb_at_npc_death(self, definition):
        run, owner, bot, charge_started, step = self.stage_orb_npc_lethal(definition, death_after=90)
        fight = run.match.combat
        old_body = owner.body
        old_script = owner.body_script
        own_pool = health(run, owner)
        npc_pool = health(run, bot)
        before = npc_pool.total
        for _ in range(220):
            step(2)
            self.assertFalse(any(rocket.shooter is owner for rocket in fight.rockets))
            if own_pool.dead:
                break
            self.assertIn(29, old_script.component.held)
        self.assertTrue(own_pool.dead)
        self.assertEqual(run.frame - charge_started, 90)
        self.assertEqual(own_pool.killer, bot.entity)
        self.assertEqual(npc_pool.total, before)
        self.assertEqual(npc_control.stop(run.match), 1)
        fire(run, owner, 30, buttons=0)
        self.assertFalse(old_script.component.shots)
        self.assertFalse(fight.rockets)
        self.assertEqual(npc_pool.total, before)
        [deadline] = [when for when, player in fight.respawns if player is owner]
        run.frame = math.ceil(deadline / combat.TICK) + 1
        run.match.update(run.frame * combat.TICK, run.frame)
        self.assertNotEqual(owner.body, old_body)
        self.assertTrue(old_script.retired)
        self.assertEqual(health(run, owner).total, 225)
        owner.mover.position[:] = (0, 1, 0)
        fire(run, owner, 30, buttons=0)
        self.assertFalse(fight.rockets)
        self.assertEqual(npc_pool.total, before)
        fire(run, owner, 90, buttons=2)
        fire(run, owner, buttons=0)
        [shot] = fight.rockets
        self.assertIs(shot.shooter, owner)
        fire(run, owner, 30, buttons=0)
        self.assertAlmostEqual(before - npc_pool.total, 120, places=4)

    @patch.object(FakeClient, "bot_stream", new=lambda _client, _body: None, create=True)
    def test_launched_orb_credits_posthumous_hits_against_all_five_actual_npc_killers(self):
        for definition in self.definitions.values():
            for trade in (False, True):
                with self.subTest(npc=definition.name, lethal_orb=trade):
                    self.check_in_flight_orb_at_npc_death(definition, trade=trade)

    def check_in_flight_orb_at_npc_death(self, definition, *, trade):
        run, owner, bot, charge_started, step = self.stage_orb_npc_lethal(definition, death_after=92)
        fight = run.match.combat
        old_body = owner.body
        own_pool = health(run, owner)
        npc_pool = health(run, bot)
        if trade:
            npc_pool.now[combat.HEALTH_POOL] = 100
            npc_pool.now[combat.ARMOUR_POOL] = 0
            npc_pool.now[combat.SHIELDS_POOL] = 0
        before = npc_pool.total
        weapon = next(i for i in owner.body_script.component.instances.values() if i.graph.index == 0x157C)
        while run.frame < charge_started + 90:
            step(2)
            self.assertFalse(own_pool.dead)
            self.assertFalse(any(rocket.shooter is owner for rocket in fight.rockets))
        step(0)
        [orb] = [rocket for rocket in fight.rockets if rocket.shooter is owner]
        self.assertEqual(weapon.find_var(0, 53).value(), 60)
        self.assertFalse(own_pool.dead)
        step(0)
        self.assertTrue(own_pool.dead)
        self.assertEqual(own_pool.killer, bot.entity)
        self.assertEqual(run.frame, charge_started + 92)
        self.assertEqual(npc_pool.total, before)
        self.assertIn(orb, fight.rockets)
        self.assertFalse(owner.client.messages(combat.HIT))
        saved = charge(owner)

        fire(run, owner, 30, buttons=0)
        self.assertNotIn(orb, fight.rockets)
        self.assertTrue(own_pool.dead)
        self.assertEqual(owner.body, old_body)
        self.assertEqual(charge(owner), saved)
        self.assertEqual(weapon.find_var(0, 53).value(), 60)
        dealt = 100 if trade else 120
        self.assertAlmostEqual(before - npc_pool.total, dealt, places=4)
        self.assertAlmostEqual(owner.stats.value(stats.HERO_DAMAGE), dealt, places=4)
        hits = [hit["+0x78"] for hit in owner.client.messages(combat.HIT)]
        self.assertEqual([hit["+0x0"] for hit in hits], [{"+0x0": bot.entity}] * 2)
        self.assertEqual(sum(hit["+0x4"] for hit in hits), dealt)
        self.assertEqual([hit["+0x8"] for hit in hits], [0, combat.KILLING if trade else 0])
        self.assertEqual(npc_pool.dead, trade)
        self.assertEqual(owner.stats.value(stats.ELIMINATIONS), int(trade))
        notices = owner.client.messages(combat.KILL_NOTICE)
        if trade:
            self.assertEqual(npc_pool.killer, old_body)
            self.assertGreater(npc_pool.death_tick, own_pool.death_tick)
            [notice] = notices
            self.assertEqual(notice["+0x80"], {"+0x0": old_body})
            self.assertEqual(notice["+0x84"], {"+0x0": bot.entity})
            self.assertIn(bot, run.match.bots)
            self.assertFalse(any(player is bot for _, player in fight.respawns))
        else:
            self.assertFalse(notices)

    @patch.object(FakeClient, "bot_stream", new=lambda _client, _body: None, create=True)
    def test_projected_barrier_on_all_five_allied_npcs_absorbs_orb_and_beam(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                self.check_projected_barrier_on_allied_npc(definition)

    def check_projected_barrier_on_allied_npc(self, definition):
        run, player, bot, target = self.create(definition)
        caster = run.players[1]
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        self.assertTrue(run.match.switch_hero(caster, heroes()[zarya_barriers.HERO]))
        caster.team = bot.team
        caster.mover.position[:] = (3, 1, 4)
        fight = run.match.combat
        attack_aim = aim(fight._eye(player), (0, 2, 4))
        x, y, z = target.feet
        projected_aim = aim(fight._eye(caster), (x, y + target.eye, z))
        component = caster.body_script.component
        projected = next(i for i in component.instances.values() if i.graph.index == 0x025C)
        energy = component.entity_var(87)

        def step(attack=0, defense=0):
            run.step({player: (*attack_aim, attack), caster: (*projected_aim, defense)})

        before = health(run, bot).total
        for _ in range(90):
            step(2)
        step(2, 16)
        self.assertTrue(projected.state(10).active)
        self.assertEqual(projected.find_var(0, 536).value(), expr.Entity(bot.entity))
        source, _, bubble = fight.zarya_barriers.contact(bot)
        self.assertIs(source, caster)
        self.assertEqual(bubble.remaining, 200)
        step()
        self.assertTrue(fight.rockets)
        for _ in range(20):
            if not fight.rockets:
                break
            step()
        self.assertFalse(fight.rockets)
        self.assertAlmostEqual(bubble.remaining, 80, places=4)
        self.assertEqual(health(run, bot).total, before)
        step()
        self.assertAlmostEqual(energy.value(), 24, places=4)

        for _ in range(100):
            step(1)
            self.assertEqual(health(run, bot).total, before)
            if not bubble.remaining:
                break
        self.assertEqual(bubble.remaining, 0)
        self.assertIsNone(fight.zarya_barriers.contact(bot))
        for _ in range(3):
            step(1)
        self.assertFalse(projected.state(10).active)
        self.assertAlmostEqual(energy.value(), 40, places=4)
        self.assertLess(health(run, bot).total, before)

    def test_actual_melee_from_full_pools_kills_and_retains_all_five_npc_bodies(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                self.check_full_pool_melee_kill(definition)

    def check_full_pool_melee_kill(self, definition):
        run, player, bot, _ = self.create(definition, distance=2)
        vitals = health(run, bot)
        full_pools = definition.health + definition.armour + definition.shields
        self.assertEqual(vitals.total, full_pools)
        weapon = next(i for i in player.body_script.component.instances.values() if i.graph.index == 0x157C)
        ammo = weapon.find_var(0, 53)
        initial_ammo = weapon.find_var(0, 198).value()
        self.assertEqual(ammo.value(), initial_ammo)

        fire(run, player, buttons=0x800)
        self.assertEqual(vitals.total, full_pools - 30)
        fire(run, player, buttons=0)
        fire(run, player, buttons=0x800)
        self.assertEqual(vitals.total, full_pools - 30)
        self.assertEqual(ammo.value(), initial_ammo)

        for _ in range((full_pools - 1) // 30):
            before = vitals.total
            fire(run, player, 70, buttons=0)
            self.assertEqual(vitals.total, before)
            fire(run, player, buttons=0x800)
            self.assertEqual(vitals.total, max(0, before - 30))
            self.assertEqual(ammo.value(), initial_ammo)
        self.assert_full_pool_npc_kill(run, player, bot, full_pools)

    def test_quick_melee_range_facing_cover_and_team_for_all_five_npcs(self):
        wall = World([(-5, 0, 1, 5, 0, 1, 5, 8, 1),
                      (-5, 0, 1, 5, 8, 1, -5, 8, 1)])
        for definition in self.definitions.values():
            for case in ("hit", "facing away", "out of range", "static cover", "ally"):
                with self.subTest(npc=definition.name, case=case):
                    self.check_npc_melee_contact(definition, case, wall)

    def check_npc_melee_contact(self, definition, case, wall):
        run, player, bot, _ = self.create(
            definition, distance=4 if case == "out of range" else 2, ally=case == "ally",
        )
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        fight = run.match.combat
        pool = health(run, bot)
        before = pool.now.copy()
        player.body_script.component.set(12918, 0)
        instances = player.body_script.component.instances.values()
        weapon = next(i for i in instances if i.graph.index == 0x157C)
        ammo = weapon.find_var(0, 53)
        initial_ammo = ammo.value()
        initial_charge = charge(player)
        yaw = -32768 if case == "facing away" else 0
        if case == "static cover":
            with patch.object(fight, "_collision", return_value=wall):
                fire(run, player, buttons=0x800, yaw=yaw)
        else:
            fire(run, player, buttons=0x800, yaw=yaw)

        melee = next(i for i in instances if i.graph.index == 0x0043)
        state = melee.state(4)
        self.assertTrue(state.active)
        self.assertEqual(state.counter, 1)
        self.assertEqual(ammo.value(), initial_ammo)
        expected = 30 if case == "hit" else 0
        self.assertAlmostEqual(sum(before.values()) - pool.total, expected)
        if not expected:
            self.assertEqual(pool.now, before)
        self.assertAlmostEqual(charge(player) - initial_charge, expected)
        self.assertAlmostEqual(player.stats.value(stats.HERO_DAMAGE), expected)
        markers = player.client.messages(combat.HIT)
        self.assertEqual(len(markers), int(bool(expected)))
        if expected:
            [marker] = markers
            self.assertEqual(marker["+0x78"]["+0x0"]["+0x0"], bot.entity)
            self.assertEqual(marker["+0x78"]["+0x4"], 30)

    def test_quick_melee_includes_tangent_npc_capsules(self):
        for definition in self.definitions.values():
            for offset in (-.001, 0, .001):
                with self.subTest(npc=definition.name, offset=offset):
                    run, player, bot, target = self.create(definition)
                    fight = run.match.combat
                    # Elevate the stationary capsule so the swing crosses its vertical segment.
                    bot.position = (0, 1.4, 2.5 + target.radius + offset)
                    height = fight._eye(player)[1] - bot.position[1]
                    self.assertGreaterEqual(height, target.radius)
                    self.assertLessEqual(height, target.height - target.radius)
                    pool = health(run, bot)
                    before = pool.total
                    run.step({player: (0, 0, 0x800)})
                    self.assertEqual(before - pool.total, 30 if offset <= 0 else 0)

    def test_quick_melee_capsule_boundary_and_retained_npc_route(self):
        for definition in self.definitions.values():
            for movement, view in (("inside", "live"), ("outside", "live"),
                                   ("entering", "live"), ("entering", "start"),
                                   ("leaving", "live"), ("leaving", "start"),
                                   ("entering", "before crossing"), ("entering", "after crossing"),
                                   ("leaving", "before crossing"), ("leaving", "after crossing")):
                with self.subTest(npc=definition.name, movement=movement, view=view):
                    self.check_melee_route_contact(definition, movement, view)

    def check_melee_route_contact(self, definition, movement, view):
        run, player, bot, target = self.create(definition)
        fight = run.match.combat
        player.body_script.component.set(12918, 0)
        eye = fight._eye(player)[1]
        # A horizontal swing is centered 1.5 m ahead and has a 1 m radius.
        low, high = target.radius + 1, max(target.radius, target.height - target.radius) + 1
        vertical = max(low - eye, eye - high, 0)
        boundary = 1.5 + math.sqrt((1 + target.radius) ** 2 - vertical ** 2)
        starts_inside = movement in ("inside", "leaving")
        direction = 1 if starts_inside else -1
        start_z = boundary - direction * .0625
        bot.position = (0, 1, start_z)
        run.step({})
        view_frame = run.frame
        self.assertAlmostEqual(fight.history[bot.entity][-1][1][2], start_z)

        if movement in ("entering", "leaving"):
            bot.route = ((0, 1, boundary + direction * .25),)
            bot.wait = 0
            for _ in range(50):
                run.step({})
                if direction * (bot.position[2] - boundary) > .04:
                    break
        pool = health(run, bot)
        before = pool.total
        initial_charge = charge(player)
        run.frame += 1
        run.match.update(run.frame * combat.TICK, run.frame)
        live_z = fight.history[bot.entity][-1][1][2]
        live_inside = movement in ("inside", "entering")
        self.assertEqual(live_z < boundary, live_inside)
        tick_ms = round(combat.TICK * 1000)
        view_inside = live_inside
        extra = 0
        if view == "start":
            extra = (run.frame - view_frame) * tick_ms
            view_inside = starts_inside
        elif view != "live":
            earlier, later = next(
                (a, b) for a, b in pairwise(fight.history[bot.entity])
                if a[0] >= view_frame and direction * (a[1][2] - boundary) <= 0
                and direction * (b[1][2] - boundary) > 0
            )
            self.assertEqual(later[0] - earlier[0], 1)
            crossing_ms = tick_ms * (boundary - earlier[1][2]) / (later[1][2] - earlier[1][2])
            before_crossing = view == "before crossing"
            offset_ms = math.floor(crossing_ms) if before_crossing else math.ceil(crossing_ms)
            self.assertGreater(offset_ms, 0)
            self.assertLess(offset_ms, tick_ms)
            self.assertNotEqual(offset_ms, crossing_ms)
            extra = (run.frame - earlier[0]) * tick_ms - offset_ms
            view_inside = starts_inside if before_crossing else live_inside
        player.take_commands([Command(run.frame, yaw=0, pitch=0, buttons=0x800, extra=extra)])

        melee = next(i for i in player.body_script.component.instances.values() if i.graph.index == 0x0043)
        self.assertTrue(melee.state(4).active)
        self.assertEqual(melee.state(4).counter, 1)
        expected = 30 if view_inside else 0
        self.assertAlmostEqual(before - pool.total, expected)
        self.assertAlmostEqual(charge(player) - initial_charge, expected)
        self.assertAlmostEqual(player.stats.value(stats.HERO_DAMAGE), expected)
        self.assertEqual(len(player.client.messages(combat.HIT)), int(bool(expected)))

    def test_delayed_melee_uses_live_ice_wall_and_retained_pose_for_all_five_npcs(self):
        cases = (
            ("deploy", "leaving", True, 0),
            ("none", "leaving", True, 30),
            ("cancel", "leaving", True, 30),
            ("cancel", "leaving", False, 0),
            ("cancel", "entering", True, 0),
            ("cancel", "entering", False, 30),
            ("persistent", "leaving", True, 0),
        )
        for definition in self.definitions.values():
            for wall_change, movement, delay, expected in cases:
                with self.subTest(npc=definition.name, wall=wall_change, movement=movement, delay=delay):
                    self.check_melee_live_wall(definition, wall_change, movement, delay, expected)

    def check_melee_live_wall(self, definition, wall_change, movement, delay, expected):
        leaving = movement == "leaving"
        run, player, bot, _ = self.create(definition, distance=2.25 if leaving else 3.6)
        mei = run.players[1]
        self.assertTrue(run.match.switch_hero(mei, heroes()[cryo.HERO]))
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        mei.team = player.team
        mei.mover.position[:] = (0, 1, -4)
        player.body_script.component.set(12918, 0)
        fight = run.match.combat
        angles = aim(fight._eye(mei), (0, 1, 1.1))

        def deploy():
            for buttons in (16, 0, 1, 0):
                run.step({mei: (*angles, buttons)})
            self.assertEqual(len(fight.ice_wall.pieces), 5)

        if wall_change in ("cancel", "persistent"):
            deploy()
        else:
            run.step({})
        viewed = run.frame
        self.assertEqual(bool(fight.ice_wall.pieces), wall_change in ("cancel", "persistent"))
        start_z = bot.position[2]
        self.assertAlmostEqual(start_z, 2.25 if leaving else 3.6)
        bot.route = ((0, 1, 3.75 if leaving else 2.25),)
        bot.wait = 0
        for _ in range(48):
            run.step({})
            if (bot.position[2] > 3.4 if leaving else bot.position[2] < 2.4):
                break
        if wall_change == "deploy":
            deploy()
        elif wall_change == "cancel":
            run.step({mei: (*angles, 16)})
        wall_live = wall_change in ("deploy", "persistent")
        self.assertEqual(bool(fight.ice_wall.pieces), wall_live)

        pool = health(run, bot)
        base = pool.now.copy()
        initial_charge = charge(player)
        run.frame += 1
        run.match.update(run.frame * combat.TICK, run.frame)
        history = {tick: feet for tick, feet, _, _ in fight.history[bot.entity]}
        self.assertAlmostEqual(history[viewed][2], start_z)
        self.assertEqual(history[run.frame][2] < 2.5, not leaving)
        target = next(t for t in fight._targets() if t.owner is bot)
        self.assertEqual(fight.visible_target(fight._eye(player), target), not wall_live)
        extra = (run.frame - viewed) * round(combat.TICK * 1000) if delay else 0
        player.take_commands([Command(run.frame, yaw=0, pitch=0, buttons=0x800, extra=extra)])

        melee = next(i for i in player.body_script.component.instances.values() if i.graph.index == 0x0043)
        self.assertEqual(melee.state(4).counter, 1)
        self.assertAlmostEqual(sum(base.values()) - pool.total, expected)
        self.assertAlmostEqual(charge(player) - initial_charge, expected)
        self.assertAlmostEqual(player.stats.value(stats.HERO_DAMAGE), expected)
        self.assertEqual(len(player.client.messages(combat.HIT)), int(bool(expected)))
        if not expected:
            self.assertEqual(pool.now, base)
        self.assertTrue(all(piece.health == 400 for piece in fight.ice_wall.pieces.values()))

    def test_melee_breaks_full_health_ice_wall_without_spill_to_all_five_npcs(self):
        for definition in self.definitions.values():
            for followup in ("stationary", "current moving", "delayed moving"):
                with self.subTest(npc=definition.name, followup=followup):
                    self.check_melee_hostile_wall_break(definition, followup)

    def check_melee_hostile_wall_break(self, definition, followup):
        run, player, bot, target = self.create(definition, distance=2.25)
        mei = run.players[1]
        self.assertTrue(run.match.switch_hero(mei, heroes()[cryo.HERO]))
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        mei.team = bot.team
        mei.mover.position[:] = (0, 1, -4)
        player.body_script.component.set(12918, 0)
        fight = run.match.combat
        angles = aim(fight._eye(mei), (0, 1, 1.1))
        for buttons in (16, 0, 1, 0):
            run.step({mei: (*angles, buttons)})
        self.assertEqual(len(fight.ice_wall.pieces), 5)
        center = min(fight.ice_wall.pieces.values(), key=lambda piece: abs(piece.feet[0]))
        sides = set(fight.ice_wall.pieces) - {center.entity}
        self.assertEqual(center.health, 400)
        self.assertFalse(fight.visible_target(fight._eye(player), target))
        pool = health(run, bot)
        base = pool.now.copy()
        initial_charge = charge(player)

        helper = run.match.add_player(3, "Allied Symmetra", player.hero.guid, 2, False)
        in_world(run.match, helper)
        run.players.append(helper)
        self.assertTrue(run.match.switch_hero(helper, player.hero))
        helper.team = player.team
        helper.mover.position[:] = (.2, 1, 0)
        for _ in range(235):
            run.step({player: (0, 0, 1), helper: (0, 0, 1)})
            if center.health <= 27:
                break
        self.assertGreater(center.health, 0)
        self.assertLessEqual(center.health, 27)
        self.assertTrue(all(fight.ice_wall.pieces[entity].health == 400 for entity in sides))
        run.step({})
        run.step({player: (0, 0, 0x800)})
        self.assertEqual(set(fight.ice_wall.pieces), sides)
        self.assertEqual(center.health, 0)
        side_pools = {entity: fight.ice_wall.pieces[entity].health for entity in sides}
        self.assertEqual(sorted(side_pools.values()), [370, 370, 400, 400])
        self.assertEqual(pool.now, base)
        self.assertTrue(fight.visible_target(fight._eye(player), target))
        self.assertEqual(player.stats.value(stats.HERO_DAMAGE), 0)
        self.assertAlmostEqual(charge(player), initial_charge)
        self.assertFalse(player.client.messages(combat.HIT))
        melee = next(i for i in player.body_script.component.instances.values() if i.graph.index == 0x0043)
        self.assertEqual(melee.state(4).counter, 1)

        for _ in range(25):
            run.step({})
        viewed = run.frame
        if followup != "stationary":
            bot.route = ((0, 1, 3.75),)
            bot.wait = 0
        for _ in range(45):
            run.step({})
        run.frame += 1
        run.match.update(run.frame * combat.TICK, run.frame)
        history = {tick: feet for tick, feet, _, _ in fight.history[bot.entity]}
        self.assertAlmostEqual(history[viewed][2], 2.25)
        self.assertEqual(history[run.frame][2] > 3.4, followup != "stationary")
        self.assertEqual(set(fight.ice_wall.pieces), sides)
        self.assertEqual({entity: fight.ice_wall.pieces[entity].health for entity in sides}, side_pools)
        extra = (run.frame - viewed) * round(combat.TICK * 1000) if followup == "delayed moving" else 0
        player.take_commands([Command(run.frame, yaw=0, pitch=0, buttons=0x800, extra=extra)])
        self.assertEqual(melee.state(4).counter, 2)
        expected = 0 if followup == "current moving" else 30
        self.assertEqual(sum(base.values()) - pool.total, expected)
        self.assertEqual(player.stats.value(stats.HERO_DAMAGE), expected)
        self.assertAlmostEqual(charge(player) - initial_charge, expected)
        self.assertEqual(len(player.client.messages(combat.HIT)), int(bool(expected)))

    @patch.object(FakeClient, "bot_stream", new=lambda _client, _body: None, create=True)
    def test_quick_melee_trades_with_npc_attacks_or_is_blocked_by_pin(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                baseline, player, bot, _ = self.create(definition, distance=1.5)
                self.engage_npc(baseline, player, bot)
                player_pool = health(baseline, player)
                start = baseline.frame
                for _ in range(159):
                    baseline.step({})
                    if player_pool.total < 225:
                        break
                self.assertLess(player_pool.total, 225)
                hit_frame = baseline.frame - start
                incoming = 225 - player_pool.total
                for lead in (1, 0):
                    with self.subTest(lead=lead):
                        self.check_melee_against_npc_attack(definition, hit_frame, incoming, lead)

    def check_melee_against_npc_attack(self, definition, hit_frame, incoming, lead):
        run, player, bot, _ = self.create(definition, distance=1.5)
        fight = run.match.combat
        player.body_script.component.set(12918, 0)
        player_pool = health(run, player)
        npc_pool = health(run, bot)
        before = npc_pool.total
        initial_charge = charge(player)
        melee = 0x800
        if hit_frame == 1 and lead:
            # The Assassin can pin on the first tick after attack input.
            run.step({player: (0, 0, melee)})
        self.engage_npc(run, player, bot)
        for frame in range(1, hit_frame + 1):
            if frame < hit_frame:
                self.assertEqual(player_pool.total, 225)
            run.step({player: (0, 0, melee if frame == hit_frame - lead else 0)})
            if frame == hit_frame - lead and lead:
                self.assertEqual(npc_pool.total, before - 30)
        self.assertEqual(player_pool.total, 225 - incoming)
        self.assertEqual(player_pool.now[combat.HEALTH_POOL], 100)
        self.assertEqual(player_pool.now[combat.SHIELDS_POOL], 125 - incoming)
        self.assertEqual(round(player_pool.last_damage_at / combat.TICK), run.frame)
        pinned = definition.name == "Assassin"
        self.assertEqual(fight.pins.get(bot.entity), player.body if pinned else None)
        expected = 0 if pinned and not lead else 30
        self.assertEqual(before - npc_pool.total, expected)
        self.assertEqual(player.stats.value(stats.HERO_DAMAGE), expected)
        self.assertAlmostEqual(charge(player) - initial_charge, expected)
        hits = player.client.messages(combat.HIT)
        self.assertEqual(len(hits), int(bool(expected)))
        if expected:
            self.assertEqual(round(npc_pool.last_damage_at / combat.TICK), run.frame - lead)
            self.assertEqual(hits[0]["+0x78"]["+0x0"]["+0x0"], bot.entity)

        if pinned and not lead:
            self.assertEqual(npc_control.stop(run.match), 1)
            for _ in range(70):
                run.step({})
            self.assertNotIn(bot.entity, fight.pins)
            self.assertEqual(npc_pool.total, before)
            run.step({player: (0, 0, melee)})
            self.assertEqual(npc_pool.total, before - 30)

    def test_heavy_capture_blocks_melee_at_contact_and_rescue_allows_fresh_swing(self):
        run, player, _, bot = self.projected_scene("Heavy Assault", distance=8)
        fight = run.match.combat
        self.assertEqual(npc_control.start_charge(run.match, player), bot.entity)
        start = run.frame
        for _ in range(100):
            run.step({})
            if fight.pins.get(bot.entity) == player.body:
                break
        self.assertEqual(fight.pins.get(bot.entity), player.body)
        capture_frame = run.frame - start
        cases = ((1, "manual", False), (0, "manual", False),
                 (0, "projected barrier", False), (0, "projected barrier", True))
        for lead, release, swing_on_release in cases:
            with self.subTest(lead=lead, release=release, swing_on_release=swing_on_release):
                self.check_melee_at_heavy_capture(capture_frame, lead, release, swing_on_release)

    def check_melee_at_heavy_capture(self, capture_frame, lead, release, swing_on_release):
        run, player, caster, bot = self.projected_scene("Heavy Assault", distance=8)
        fight = run.match.combat
        player.body_script.component.set(12918, 0)
        npc_pool = health(run, bot)
        before = npc_pool.total
        initial_charge = charge(player)
        melee = 0x800

        def aim_bot():
            x, y, z = bot.position
            return aim(fight._eye(player), (x, y + 1, z))

        self.assertEqual(npc_control.start_charge(run.match, player), bot.entity)
        for frame in range(1, capture_frame + 1):
            self.assertNotIn(bot.entity, fight.pins)
            run.step({player: (*aim_bot(), melee if frame == capture_frame - lead else 0)})
        self.assertEqual(fight.pins.get(bot.entity), player.body)
        attachment = fight.heavy_charge_pins[bot.entity]
        self.assertEqual(attachment.graph.index, 0x1407)
        self.assertTrue(fight.is_stunned(player))
        self.assertEqual(health(run, player).total, 225)
        expected = 30 if lead else 0
        self.assertEqual(before - npc_pool.total, expected)
        self.assertEqual(player.stats.value(stats.HERO_DAMAGE), expected)
        self.assertAlmostEqual(charge(player) - initial_charge, expected)
        self.assertEqual(len(player.client.messages(combat.HIT)), int(bool(expected)))
        if lead:
            self.assertEqual(round(npc_pool.last_damage_at / combat.TICK), run.frame - 1)

        if release == "manual":
            self.assertEqual(npc_control.stop(run.match), 1)
        else:
            angles = aim(fight._eye(caster), fight._eye(player))
            run.step({caster: (*angles, 16)})
            self.assertEqual(fight.pins.get(bot.entity), player.body)
            self.assertIs(fight.zarya_barriers.contact(player)[0], caster)
        run.step({player: (*aim_bot(), melee)} if swing_on_release else {})
        self.assertNotIn(bot.entity, fight.pins)
        self.assertNotIn(bot.entity, fight.heavy_charge_pins)
        self.assertNotIn(attachment.id, attachment.component.instances)
        self.assertFalse(fight.is_stunned(player))
        self.assertEqual(heavy_charge.phase(heavy_charge.instance(bot)), 0)
        if swing_on_release:
            expected += 30
            self.assertEqual(round(npc_pool.last_damage_at / combat.TICK), run.frame)
        self.assertEqual(before - npc_pool.total, expected)

        for _ in range(70):
            run.step({})
        self.assertEqual(health(run, player).total, 225)
        self.assertEqual(before - npc_pool.total, expected)
        run.step({player: (*aim_bot(), melee)})
        self.assertEqual(before - npc_pool.total, expected + 30)
        self.assertEqual(player.stats.value(stats.HERO_DAMAGE), expected + 30)
        self.assertAlmostEqual(charge(player) - initial_charge, expected + 30)
        self.assertEqual(len(player.client.messages(combat.HIT)), 1 + expected // 30)

    def test_heavy_wall_crush_drops_buffered_melee_through_respawn_or_rescue(self):
        plane = -5
        cover = World([
            (-10, 0, plane, 10, 0, plane, 10, 5, plane),
            (-10, 0, plane, 10, 5, plane, -10, 5, plane),
            (-10, 0, plane, 10, 5, plane, 10, 0, plane),
            (-10, 0, plane, -10, 5, plane, 10, 5, plane),
        ])
        for rescue_lead in (None, 1, 0):
            with self.subTest(rescue_lead=rescue_lead):
                self.check_buffered_melee_at_heavy_crush(cover, rescue_lead)
        for extended_pool, melee_lead in ((True, None), (True, 1), (True, 0), (False, 0)):
            with self.subTest(extended_pool=extended_pool, melee_lead=melee_lead):
                self.check_buffered_melee_at_heavy_crush(
                    cover, None, extended_pool=extended_pool, melee_lead=melee_lead,
                )

    def check_buffered_melee_at_heavy_crush(
        self, cover, rescue_lead, *, extended_pool=False, melee_lead=None,
    ):
        run, player, caster, bot = self.projected_scene("Heavy Assault", distance=8)
        fight = run.match.combat
        player.spawn = ((0, 1, -1), 180)
        player.body_script.component.set(12918, 0)
        old_body = player.body
        npc_pool = health(run, bot)
        before = npc_pool.total
        pool = health(run, player)
        if extended_pool:
            # Retain the authored shields while allowing the full crush to be nonlethal.
            pool.now[combat.HEALTH_POOL] = pool.most[combat.HEALTH_POOL] = 400
            fight._sync_body_health(player, pool)
        starting_total = pool.total
        melee = next(i for i in player.body_script.component.instances.values() if i.graph.index == 0x0043)
        ability = melee.node_state(melee.graph.node(14))
        swing = melee.state(4)
        # In this lane capture is tick 76 and the unrescued crush is tick 98.
        capture_tick, crush_tick = 76, 98

        def aim_at_heavy():
            x, y, z = bot.position
            return aim(fight._eye(player), (x, y + 1, z))

        self.assertEqual(npc_control.start_charge(run.match, player), bot.entity)
        with patch.object(fight, "_collision", return_value=cover):
            for tick in range(1, capture_tick):
                buttons = 0x800 if tick in (capture_tick - 55, capture_tick - 1) else 0
                run.step({player: (0, 0, buttons)})
            self.assertNotIn(bot.entity, fight.pins)
            self.assertEqual(swing.counter, 1)
            age_ms = ability.now - ability.presses[-1][0]
            self.assertIn(age_ms, range(round(combat.TICK * 1000)))
            self.assertEqual(npc_pool.total, before)
            run.step({})
            attachment = fight.heavy_charge_pins[bot.entity]
            self.assertEqual(fight.pins.get(bot.entity), old_body)
            self.assertFalse(ability.presses)
            for tick in range(capture_tick + 1, crush_tick):
                self.assertEqual(fight.pins.get(bot.entity), old_body)
                if rescue_lead == 1 and tick == crush_tick - 1:
                    angles = aim(fight._eye(caster), fight._eye(player))
                    run.step({caster: (*angles, 16)})
                elif melee_lead == 1 and tick == crush_tick - 1:
                    run.step({player: (*aim_at_heavy(), 0x800)})
                    self.assertFalse(ability.presses)
                    self.assertEqual(swing.counter, 1)
                else:
                    run.step({})
            self.assertEqual(fight.pins.get(bot.entity), old_body)
            self.assertEqual(pool.total, starting_total)
            commands = {}
            if rescue_lead == 0:
                commands[caster] = (*aim(fight._eye(caster), fight._eye(player)), 16)
            if melee_lead == 0:
                commands[player] = (*aim_at_heavy(), 0x800)
            charge_before_crush = charge(player)
            with patch.object(fight, "_damage", wraps=fight._damage) as damage:
                run.step(commands)
        traded = extended_pool and melee_lead == 0
        expected_calls = [] if rescue_lead == 1 else [(bot, player, 300)]
        if traded:
            expected_calls.append((player, bot, 30))
        self.assertEqual([(call.args[0], call.args[1].owner, call.args[2])
                          for call in damage.call_args_list], expected_calls)
        crush_frame = run.frame
        self.assertNotIn(bot.entity, fight.pins)
        self.assertNotIn(bot.entity, fight.heavy_charge_pins)
        self.assertNotIn(attachment.id, attachment.component.instances)
        dealt = 30 if traded else 0
        self.assertEqual(swing.counter, 1 + int(traded))
        self.assertEqual(npc_pool.total, before - dealt)
        self.assertEqual(len(player.client.messages(combat.HIT)), int(traded))
        self.assertEqual(player.stats.value(stats.HERO_DAMAGE), dealt)
        self.assertAlmostEqual(charge(player) - charge_before_crush, dealt)
        if rescue_lead == 1:
            self.assertEqual(pool.total, 225)
            self.assertEqual(fight.zarya_barriers.contact(player)[2].remaining, 200)
            self.assertFalse(fight.is_stunned(player))
        elif extended_pool:
            self.assertFalse(pool.dead)
            self.assertEqual(player.body, old_body)
            self.assertFalse(fight.is_stunned(player))
            self.assertEqual(pool.total, starting_total - 300)
            self.assertEqual(pool.now[combat.SHIELDS_POOL], 0)
            self.assertEqual(round(pool.last_damage_at / combat.TICK), crush_frame)
            self.assertEqual(npc_control.stop(run.match), 1)
        else:
            self.assertIsNone(fight.zarya_barriers.contact(player))
            self.assertTrue(pool.dead)
            self.assertEqual(pool.total, 0)
            self.assertEqual(pool.killer, bot.entity)
            self.assertEqual(player.stats.value(stats.DEATHS), 1)
            for _ in range(800):
                if player.body != old_body:
                    break
                run.step({})
            self.assertNotEqual(player.body, old_body)
            self.assertEqual(health(run, player).total, 225)

        for _ in range(187 if extended_pool else 70):
            run.step({})
        self.assertEqual(npc_pool.total, before - dealt)
        self.assertEqual(len(player.client.messages(combat.HIT)), int(traded))
        if extended_pool:
            self.assertEqual(pool.now[combat.SHIELDS_POOL], 0)
            run.step({})
            self.assertAlmostEqual(pool.now[combat.SHIELDS_POOL], .24)
            self.assertEqual(pool.now[combat.HEALTH_POOL], 225)
        initial_charge = charge(player)
        run.step({player: (*aim_at_heavy(), 0x800)})
        self.assertEqual(npc_pool.total, before - dealt - 30)
        self.assertEqual(player.stats.value(stats.HERO_DAMAGE), dealt + 30)
        self.assertAlmostEqual(charge(player) - initial_charge, 30)
        self.assertEqual(len(player.client.messages(combat.HIT)), 1 + int(traded))

    def test_primary_miss_range_and_ally_protection_for_every_npc(self):
        for definition in self.definitions.values():
            for reason in ("miss", "range", "ally"):
                with self.subTest(npc=definition.name, reason=reason):
                    run, player, bot, _target = self.create(
                        definition, distance=30 if reason == "range" else 4, ally=reason == "ally")
                    if reason == "ally":
                        run.match.game_map = run.match.game_map.__class__(
                            **{**run.match.game_map.__dict__, "free_for_all": False})
                    before = health(run, bot).total
                    fire(run, player, 4, yaw=-32768 if reason == "miss" else 0)
                    self.assertEqual(health(run, bot).total, before)

    def test_actual_beam_from_full_pools_kills_and_retains_all_five_npc_bodies(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                self.check_full_pool_beam_kill(definition)

    def test_actual_orbs_from_full_pools_kill_and_retain_all_five_npc_bodies(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                self.check_full_pool_orb_kill(definition)

    def check_full_pool_orb_kill(self, definition):
        run, player, bot, _ = self.create(definition)
        fight = run.match.combat
        vitals = health(run, bot)
        full_pools = definition.health + definition.armour + definition.shields
        self.assertEqual(vitals.total, full_pools)
        weapon = next(i for i in player.body_script.component.instances.values() if i.graph.index == 0x157C)
        ammo = weapon.find_var(0, 53)
        capacity = weapon.find_var(0, 198).value()
        self.assertEqual(ammo.value(), capacity)
        reloads = 0
        for _ in range(40):
            if vitals.dead:
                break
            if ammo.value() < 10:
                fire(run, player, buttons=0x400)
                fire(run, player, 100, buttons=0)
                self.assertEqual(ammo.value(), capacity)
                reloads += 1
            before, available = vitals.total, ammo.value()
            fire(run, player, 90, buttons=2)
            self.assertEqual(vitals.total, before)
            self.assertFalse(fight.rockets)
            fire(run, player, buttons=0)
            [orb] = fight.rockets
            self.assertIs(orb.shooter, player)
            self.assertEqual(ammo.value(), available - 10)
            self.assertEqual(vitals.total, before)
            fire(run, player, 30, buttons=0)
            self.assertFalse(fight.rockets)
            self.assertAlmostEqual(vitals.total, max(0, before - 120), places=4)
        if definition.name == "Heavy Assault":
            self.assertGreater(reloads, 0)
        self.assert_full_pool_npc_kill(run, player, bot, full_pools)

    def check_full_pool_beam_kill(self, definition):
        run, player, bot, _ = self.create(definition)
        vitals = health(run, bot)
        full_pools = definition.health + definition.armour + definition.shields
        self.assertEqual(vitals.total, full_pools)
        weapon = next(i for i in player.body_script.component.instances.values() if i.graph.index == 0x157C)
        ammo = weapon.find_var(0, 53)
        capacity = weapon.find_var(0, 198).value()
        self.assertEqual(ammo.value(), capacity)
        fire(run, player, 15)
        self.assertLess(ammo.value(), capacity)
        reloads = 0
        for _ in range(2400):
            if bot.dead:
                break
            if ammo.value() <= 5:
                fire(run, player, buttons=0x400)
                fire(run, player, 100, buttons=0)
                self.assertEqual(ammo.value(), capacity)
                reloads += 1
            fire(run, player)
        if definition.name == "Heavy Assault":
            self.assertGreater(reloads, 0)
        self.assertTrue(bot.dead, f"remaining life: {vitals.total}")
        hits = player.client.messages(combat.HIT)
        fire(run, player, 10)
        self.assertEqual(player.client.messages(combat.HIT), hits)
        self.assert_full_pool_npc_kill(run, player, bot, full_pools)

    def assert_full_pool_npc_kill(self, run, player, bot, full_pools):
        fight = run.match.combat
        vitals = health(run, bot)
        self.assertTrue(bot.dead, f"remaining life: {vitals.total}")
        self.assertEqual(vitals.total, 0)
        self.assertEqual(vitals.killer, player.body)
        [notice] = player.client.messages(combat.KILL_NOTICE)
        self.assertEqual(notice["+0x84"], {"+0x0": bot.entity})
        hits = player.client.messages(combat.HIT)
        self.assertAlmostEqual(player.stats.value(stats.HERO_DAMAGE), full_pools, places=4)
        self.assertEqual(player.stats.value(stats.ELIMINATIONS), 1)
        self.assertEqual(sum(bool(hit["+0x78"]["+0x8"] & combat.KILLING) for hit in hits), 1)
        self.assertTrue(hits[-1]["+0x78"]["+0x8"] & combat.KILLING)
        self.assertTrue(any(entity == bot.entity and snapshot is not None
                            for entity, snapshot in player.client.bot_deaths))
        fire(run, player, 200, buttons=0)
        self.assertIn(bot, run.match.bots)
        self.assertIsInstance(bot.body_script, BotScript)
        self.assertFalse(any(who is bot for _, who in fight.respawns))
        self.assertNotIn(bot.entity, {t.entity for t in fight._enemies(player)})

    def test_npc_damage_dispatch_reaches_symmetra_shields_with_npc_attribution(self):
        # This isolates damage dispatch, not the still-missing NPC attack AI.
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                run, player, bot, _target = self.create(definition)
                target = next(t for t in run.match.combat._targets() if t.owner is player)
                fight = run.match.combat
                fight._damage(bot, target, 30, False, None, run.frame)
                self.assertEqual(health(run, player).now[combat.SHIELDS_POOL], 95)
                fight._damage(bot, target, 1000, False, None, run.frame)
                self.assertTrue(fight.is_dead(player))
                self.assertEqual(health(run, player).killer, bot.entity)

    @patch.object(FakeClient, "bot_stream", new=lambda _client, _body: None, create=True)
    def test_all_five_actual_npc_attacks_kill_and_release_symmetra(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                self.check_npc_lethal_attack(definition)

    @patch.object(FakeClient, "bot_stream", new=lambda _client, _body: None, create=True)
    def test_concurrent_actual_npc_attacks_share_pools_and_emit_one_death(self):
        self.check_concurrent_npc_attacks(counterfire=False)

    @patch.object(FakeClient, "bot_stream", new=lambda _client, _body: None, create=True)
    def test_counterfire_overlaps_actual_npc_damage_until_assassin_pin(self):
        self.check_concurrent_npc_attacks(counterfire=True)

    def concurrent_npc_scene(self):
        run, player, other = arena()
        other.mover.position[:] = (40, 1, 40)
        positions = {
            "Sniper": (0, 1, 4), "Trooper": (4, 1, 0), "Enforcer": (0, 1, -4),
            "Heavy Assault": (-4, 1, 0), "Assassin": (1, 1, 1),
        }
        enemies = {definition.name: npc.spawn(run.match, definition, positions[definition.name], team=1)
                   for definition in self.definitions.values()}
        return run, player, other, enemies

    def engage_npc(self, run, player, bot):
        x, y, z = bot.position
        player.mover.yaw, player.mover.pitch = aim(run.match.combat._eye(player), (x, y + 1, z))
        self.assertEqual(npc_control.start(run.match, player), bot.entity)

    def warm_up_concurrent_npcs(self, run, player, enemies):
        self.engage_npc(run, player, enemies["Sniper"])
        # Stagger input by windup and projectile travel so all five attacks overlap.
        for _ in range(55):
            run.step({})
        self.engage_npc(run, player, enemies["Enforcer"])
        for _ in range(30):
            run.step({})

    def check_concurrent_npc_attacks(self, *, counterfire):
        run, player, _, enemies = self.concurrent_npc_scene()
        fight = run.match.combat
        vitals = health(run, player)
        self.warm_up_concurrent_npcs(run, player, enemies)
        self.assertEqual(vitals.total, 225)
        self.engage_npc(run, player, enemies["Trooper"])
        heavy = enemies["Heavy Assault"]
        self.engage_npc(run, player, heavy)
        assassin = enemies["Assassin"]
        if not counterfire:
            self.engage_npc(run, player, assassin)

        original_damage = fight._damage
        losses = []
        sources_by_tick = {}
        outgoing_ticks = set()
        pinned_ticks = set()
        heavy_vitals = health(run, heavy)
        initial_heavy = heavy_vitals.total
        x, y, z = heavy.position
        angles = aim(fight._eye(player), (x, y + 1, z))
        commands = {player: (*angles, 1)} if counterfire else {}

        def observe_damage(shooter, target, *args, **kwargs):
            incoming_before, outgoing_before = vitals.total, heavy_vitals.total
            applied = original_damage(shooter, target, *args, **kwargs)
            if target.owner is player and vitals.total < incoming_before:
                losses.append((shooter.entity, incoming_before - vitals.total))
                sources_by_tick.setdefault(fight.tick, set()).add(shooter.entity)
            if shooter is player and target.owner is heavy and heavy_vitals.total < outgoing_before:
                outgoing_ticks.add(fight.tick)
            return applied

        with patch.object(fight, "_damage", side_effect=observe_damage):
            for _ in range(150):
                run.step(commands)
                if fight.pins.get(assassin.entity) == player.body:
                    pinned_ticks.add(fight.tick)
                traded_damage = fight.tick in outgoing_ticks and fight.tick in sources_by_tick
                if counterfire and assassin.attack_target is None and traded_damage:
                    self.engage_npc(run, player, assassin)
                if vitals.dead:
                    break
        if counterfire:
            self.assertTrue(outgoing_ticks.intersection(sources_by_tick))
            self.assertGreater(len(pinned_ticks), 1)
            self.assertFalse(outgoing_ticks.intersection(pinned_ticks))
            self.assertAlmostEqual(player.stats.value(stats.HERO_DAMAGE),
                                   initial_heavy - heavy_vitals.total)
        self.assertTrue(vitals.dead)
        names_by_entity = {bot.entity: name for name, bot in enemies.items()}
        self.assertEqual({names_by_entity[entity] for entity, _ in losses}, set(enemies), losses)
        self.assertTrue(any(len(sources) > 1 for sources in sources_by_tick.values()))
        self.assertAlmostEqual(sum(loss for _, loss in losses), 225)
        self.assertEqual(vitals.killer, losses[-1][0])
        [notice] = player.client.messages(combat.DEATH_NOTICE)
        self.assertEqual(notice["+0x7C"]["+0x0"], vitals.killer)
        self.assertEqual(player.stats.value(stats.DEATHS), 1)
        self.assertNotIn(player.body, fight.pins.values())
        run.step({})
        self.assertTrue(all(not bot.attack_input for bot in enemies.values()))

    @patch.object(FakeClient, "bot_stream", new=lambda _client, _body: None, create=True)
    def test_projected_barrier_absorbs_concurrent_npc_attacks_during_counterfire(self):
        run, player, caster, enemies = self.concurrent_npc_scene()
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        self.assertTrue(run.match.switch_hero(caster, heroes()[zarya_barriers.HERO]))
        caster.team = player.team
        caster.mover.position[:] = (3, 1, -2)
        fight = run.match.combat
        barriers = fight.zarya_barriers
        self.warm_up_concurrent_npcs(run, player, enemies)
        heavy = enemies["Heavy Assault"]
        self.engage_npc(run, player, heavy)
        aim_cast = aim(fight._eye(caster), fight._eye(player))
        run.step({caster: (*aim_cast, 16)})
        owner, _, pool = barriers.contact(player)
        self.assertIs(owner, caster)
        self.assertEqual(pool.remaining, 200)
        for name in ("Trooper", "Assassin"):
            self.engage_npc(run, player, enemies[name])
        x, y, z = heavy.position
        beam = aim(fight._eye(player), (x, y + 1, z))
        vitals = health(run, player)
        heavy_vitals = health(run, heavy)
        sources_by_tick = {}
        traded_while_protected = False
        original_intercept = barriers.intercept

        def observe_intercept(target, amount, source):
            before = pool.remaining
            result = original_intercept(target, amount, source)
            if target is player and pool.remaining < before:
                sources_by_tick.setdefault(fight.tick, set()).add(source)
            return result

        with patch.object(barriers, "intercept", side_effect=observe_intercept):
            for _ in range(120):
                before = heavy_vitals.total
                run.step({player: (*beam, 1)})
                if pool.remaining <= 0:
                    break
                self.assertEqual(vitals.total, 225)
                self.assertNotIn(player.body, fight.pins.values())
                if fight.tick in sources_by_tick and heavy_vitals.total < before:
                    traded_while_protected = True
        self.assertEqual(pool.remaining, 0)
        self.assertEqual(set().union(*sources_by_tick.values()), {bot.entity for bot in enemies.values()})
        self.assertTrue(any(len(sources) > 1 for sources in sources_by_tick.values()), sources_by_tick)
        self.assertTrue(traded_while_protected)
        self.assertIsNone(barriers.contact(player))
        self.assertAlmostEqual(caster.body_script.component.entity_var(87).value(), 40, places=4)
        for _ in range(80):
            if vitals.total < 225:
                break
            run.step({player: (*beam, 1)})
        self.assertLess(vitals.total, 225)

    @unittest.skipUnless(os.environ.get("ORPHEUS_EXTRA_GRAPH_BUNDLES"), "requires the barrier actor")
    @patch.object(FakeClient, "bot_stream", new=lambda _client, _body: None, create=True)
    def test_photon_barrier_splits_concurrent_npc_attacks_by_side(self):
        run, player, other, wall = barrier_tests.PhotonBarrierTests().deploy()
        other.mover.position[:] = (40, 0, 40)
        positions = {
            "Sniper": (0, 0, 4), "Enforcer": (2, 0, 4), "Heavy Assault": (-4, 0, 4),
            "Trooper": (4, 0, 0), "Assassin": (1, 0, .7),
        }
        enemies = {definition.name: npc.spawn(run.match, definition, positions[definition.name], team=1)
                   for definition in self.definitions.values()}
        fight = run.match.combat
        self.warm_up_concurrent_npcs(run, player, enemies)
        trooper, assassin = enemies["Trooper"], enemies["Assassin"]
        self.engage_npc(run, player, trooper)
        self.engage_npc(run, player, enemies["Heavy Assault"])
        beam = aim(fight._eye(player), (4, 1, 0))
        wall_pools = fight.vitals[wall.bot.entity]
        player_pools, trooper_pools = health(run, player), health(run, trooper)
        self.assertEqual((wall_pools.total, player_pools.total), (4000, 225))
        blocked = set()
        body_sources = set()
        traded = False
        original_trace, original_damage = fight.trace_barrier, fight._damage

        def observe_trace(shooter, *args, **kwargs):
            result = original_trace(shooter, *args, **kwargs)
            if result is not None and result[1] is wall and shooter in enemies.values():
                blocked.add(shooter.name)
            return result

        def observe_damage(shooter, target, *args, **kwargs):
            before = player_pools.total
            result = original_damage(shooter, target, *args, **kwargs)
            if target.owner is player and player_pools.total < before:
                body_sources.add(shooter.name)
            return result

        with (
            patch.object(fight, "trace_barrier", side_effect=observe_trace),
            patch.object(fight, "_damage", side_effect=observe_damage),
        ):
            for _ in range(120):
                barrier_before, trooper_before = wall_pools.total, trooper_pools.total
                run.step({player: (*beam, 1)})
                if not traded and wall_pools.total < barrier_before and trooper_pools.total < trooper_before:
                    traded = True
                    self.engage_npc(run, player, assassin)
                if len(blocked) == 3 and len(body_sources) == 2:
                    break

        self.assertTrue(traded)
        self.assertEqual(blocked, {"Sniper", "Enforcer", "Heavy Assault"})
        self.assertEqual(body_sources, {"Trooper", "Assassin"})
        self.assertLess(wall_pools.total, 4000)
        self.assertGreater(wall_pools.total, 0)
        self.assertEqual(fight.pins.get(assassin.entity), player.body)

    @unittest.skipUnless(os.environ.get("ORPHEUS_EXTRA_GRAPH_BUNDLES"), "requires the barrier actor")
    @patch.object(FakeClient, "bot_stream", new=lambda _client, _body: None, create=True)
    def test_concurrent_ranged_npc_attacks_reach_owner_after_photon_barrier_ends(self):
        for ending in ("break", "expiry", "full_break"):
            with self.subTest(ending=ending):
                self.check_concurrent_photon_barrier_end(ending)

    def check_concurrent_photon_barrier_end(self, ending):
        run, player, other, wall = barrier_tests.PhotonBarrierTests().deploy()
        other.mover.position[:] = (40, 0, 40)
        if ending == "full_break":
            heavy = next(d for d in self.definitions.values() if d.name == "Heavy Assault")
            attackers = [npc.spawn(run.match, heavy, (x + 1.5, 0, 8), team=1)
                         for x in range(-15, 15, 3)]
            for bot in attackers:
                self.engage_npc(run, player, bot)
        else:
            attackers = self.engage_photon_barrier_trio(run, player)
        fight = run.match.combat
        barriers = fight.barriers
        player_pools = health(run, player)
        wall_pools = fight.vitals[wall.bot.entity]
        self.assertEqual(wall_pools.total, 4000)
        if ending == "break":
            wall_pools.now[combat.HEALTH_POOL] = 200
        start_health = wall_pools.total
        retired = []
        blocked = set()
        original_retire = barriers._retire
        original_trace = fight.trace_barrier

        def observe_retire(actor):
            if actor is wall:
                retired.append((fight.now, wall_pools.total, player_pools.total))
            return original_retire(actor)

        def observe_trace(shooter, *args, **kwargs):
            result = original_trace(shooter, *args, **kwargs)
            if result is not None and result[1] is wall:
                blocked.add(shooter.entity)
            return result

        with (
            patch.object(barriers, "_retire", side_effect=observe_retire),
            patch.object(fight, "trace_barrier", side_effect=observe_trace),
        ):
            for _ in range(800):
                run.step({})
                if wall not in barriers.walls:
                    break
                self.assertEqual(player_pools.total, 225)
        [(when, final_health, owner_health)] = retired
        self.assertEqual(blocked, {bot.entity for bot in attackers})
        self.assertEqual(owner_health, 225)
        self.assertLess(final_health, start_health)
        if ending != "expiry":
            self.assertEqual(final_health, 0)
            self.assertLess(when, wall.expires_at)
        else:
            self.assertGreater(final_health, 0)
            self.assertGreaterEqual(when, wall.expires_at)
            self.assertLess(when - wall.expires_at, combat.TICK)
        self.assertNotIn(wall.bot, run.match.bots)
        self.assertNotIn(wall.bot.entity, fight.vitals)
        self.assertFalse(any(who is wall.bot for _, who in fight.respawns))

        for _ in range(180):
            if player_pools.total < 225:
                break
            run.step({})
        self.assertLess(player_pools.total, 225)
        self.assertFalse(barriers.walls)

    def engage_photon_barrier_trio(self, run, player):
        positions = {"Sniper": (0, 0, 4), "Enforcer": (2, 0, 4), "Heavy Assault": (-4, 0, 4)}
        enemies = {definition.name: npc.spawn(run.match, definition, positions[definition.name], team=1)
                   for definition in self.definitions.values() if definition.name in positions}
        self.warm_up_concurrent_npcs(run, player, enemies)
        self.engage_npc(run, player, enemies["Heavy Assault"])
        return enemies.values()

    def check_npc_lethal_attack(self, definition):
        distance = 1.5 if definition.name == "Assassin" else 4
        run, player, bot, _ = self.create(definition, distance=distance)
        fight = run.match.combat
        player.mover.yaw, player.mover.pitch = aim(fight._eye(player), (0, 2, distance))
        self.assertEqual(npc_control.start(run.match, player), bot.entity)
        body = player.body
        vitals = health(run, player)
        self.assertEqual(vitals.total, 225)
        depleted_shields = False
        pinned = False
        for _ in range(1000):
            run.step({})
            depleted_shields |= vitals.now[combat.SHIELDS_POOL] == 0 < vitals.total
            pinned |= fight.pins.get(bot.entity) == body
            if vitals.dead:
                break
        self.assertTrue(depleted_shields)
        self.assertEqual(pinned, definition.name == "Assassin")
        self.assertTrue(vitals.dead, f"remaining life: {vitals.total}")
        self.assertEqual(vitals.total, 0)
        self.assertEqual(vitals.killer, bot.entity)
        self.assertNotIn(body, fight.pins.values())
        [notice] = player.client.messages(combat.DEATH_NOTICE)
        self.assertEqual(notice["+0x7C"]["+0x0"], bot.entity)

        run.step({})
        self.assertFalse(bot.attack_input)
        self.assertNotIn(npc_control.button(bot), bot.body_script.component.held)
        [deadline] = [when for when, victim in fight.respawns if victim is player]
        self.assertGreater(deadline, fight.now)
        self.assertEqual(npc_control.stop(run.match), 1)
        while player.body == body and run.frame * combat.TICK < deadline + 1:
            run.step({})
        self.assertNotEqual(player.body, body)
        self.assertEqual(health(run, player).total, 225)

    @unittest.skipUnless(os.environ.get("ORPHEUS_EXTRA_GRAPH_BUNDLES"), "requires summon graph shard")
    def test_actual_sentries_from_full_pools_kill_and_retain_all_five_npc_bodies(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                self.check_full_pool_sentry_kill(definition)

    def check_full_pool_sentry_kill(self, definition):
        from test_symmetra_turrets import wall

        run, player, bot, _ = self.create(definition)
        bot.position = (2, 1, 1)
        fight = run.match.combat
        vitals = health(run, bot)
        full_pools = definition.health + definition.armour + definition.shields
        self.assertEqual(vitals.total, full_pools)
        count = 3 if definition.name == "Heavy Assault" else 1
        with patch.object(fight, "_collision", return_value=wall()):
            for _ in range(count):
                fire(run, player, 40, buttons=8)
                fire(run, player, buttons=0)
        sentries = fight.sentries.sentries
        self.assertEqual(len(sentries), count)
        self.assertTrue(all(sentry.attached_at is not None for sentry in sentries))
        component = bot.body_script.component
        slow = component.entity_var(23186)
        for _ in range(90):
            if all(sentry.target == bot.entity for sentry in sentries):
                break
            fire(run, player, buttons=0)
        self.assertTrue(all(sentry.target == bot.entity for sentry in sentries))
        self.assertAlmostEqual(slow.value(), .2 * count)
        auras = [sentry.slow[1].id for sentry in sentries]
        self.assertEqual(len(set(auras)), count)
        self.assertTrue(all(aura in component.instances for aura in auras))
        before = vitals.total
        fire(run, player, buttons=0)
        self.assertAlmostEqual(before - vitals.total, 40 * count * combat.TICK, places=4)

        for _ in range(2500):
            if vitals.dead:
                break
            fire(run, player, buttons=0)
        self.assertTrue(vitals.dead, f"remaining life: {vitals.total}")
        fire(run, player, buttons=0)
        self.assertTrue(all(sentry.slow is None and sentry.target is None for sentry in sentries))
        self.assertTrue(all(aura not in component.instances for aura in auras))
        self.assertFalse(slow.value())
        self.assert_full_pool_npc_kill(run, player, bot, full_pools)

    @unittest.skipUnless(os.environ.get("ORPHEUS_EXTRA_GRAPH_BUNDLES"), "requires summon graph shard")
    def test_sentry_hits_slows_and_restores_actual_npc_route_movement(self):
        from test_symmetra_turrets import wall

        from ow174.game.content import SOLDIER, heroes

        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                run, owner, bot, _target = self.create(definition)
                bot.position = (2, 1, 1)
                fight = run.match.combat
                before = health(run, bot).total
                with patch.object(fight, "_collision", return_value=wall()):
                    fire(run, owner, 40, buttons=8)
                    fire(run, owner, 80, buttons=0)
                    self.assertLess(health(run, bot).total, before)
                    self.assertAlmostEqual(bot.body_script.component.entity_var(23186).value(), .2, places=5)
                    self.assertAlmostEqual(bot.speed, definition.mover.run_forward * .8, places=5)
                    bot.route = [(2, 1, 1), (2, 1, 9)]
                    bot.target, bot.start, bot.wait = (2, 1, 9), bot.position, 0
                    bot.direction = (0, 1)
                    bot.keys = True
                    old = bot.position
                    fire(run, owner, 4, buttons=0)
                    self.assertGreater(bot.position[2], old[2])
                    run.match.switch_hero(owner, heroes()[SOLDIER])
                    fire(run, owner, buttons=0)
                    self.assertFalse(bot.body_script.component.entity_var(23186).value())
                    self.assertEqual(bot.speed, definition.mover.run_forward)


if __name__ == "__main__":
    unittest.main()
