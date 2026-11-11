"""Opt-in actual five-body fixtures, using an external read-only NPC catalog/bundle.

These test combat targets and pool changes, not autonomous AI or retail difficulty.
Set ORPHEUS_ZARYA_NPC_DATA to the prepared NPC data directory to execute them.
"""

import math
import os
import unittest
from dataclasses import replace
from itertools import product
from pathlib import Path
from unittest.mock import patch

import test_mccree_flashbang as flash
from test_game_combat import FakeClient, Run, aim, in_world

from ow174.game import combat, dva, interception, npc, npc_control, orisa_halt, sigma_flux, zarya
from ow174.game.collision import World
from ow174.game.commands import Command
from ow174.game.content import heroes
from ow174.game.mover import defined_mover_data
from ow174.game.script import graph
from ow174.game.script.driver import ULT_CHARGE, ULT_COST

NPC_DATA = os.environ.get("ORPHEUS_ZARYA_NPC_DATA")
ZARYA = 0x02E0000000000068


class NpcClient(FakeClient):
    """Delivery-model callbacks; native wire/presentation is a separate acceptance gate."""

    def __init__(self, player):
        super().__init__(player)
        self.deaths = []
        self.death_updates = []

    def queue_bot_death(self, entity, event):
        self.deaths.append((entity, event))

    def queue_bot_death_update(self, entity, event):
        self.death_updates.append((entity, event))

    def bot_stream(self, entity):
        return None  # No observer projectile transport in this damage fixture.


@unittest.skipUnless(NPC_DATA, "requires external five-NPC data; not acceptance when skipped")
class ZaryaFiveNpcTests(unittest.TestCase):
    @classmethod
    def clear_graph_cache(cls):
        for function in (graph._data, graph._records, graph.graph):
            function.cache_clear()

    @classmethod
    def setUpClass(cls):
        cls.directory = Path(NPC_DATA)
        cls.catalog = npc.load_catalog(cls.directory / "pve_npcs_174.json")
        if {d.name for d in cls.catalog.values()} != {
                "Trooper", "Enforcer", "Sniper", "Assassin", "Heavy Assault"}:
            raise AssertionError("requires the five actual requested NPC definitions")
        missing = [d.name for d in cls.catalog.values() if defined_mover_data(d.body) is None]
        if missing:
            raise AssertionError("requires actual NPC movers (npc-navigation-runtime): " + ", ".join(missing))
        cls.bundle = patch.object(graph, "GRAPHS_PATH", cls.directory / "game_graphs_174.json.gz")
        cls.bundle.start()
        cls.clear_graph_cache()

    @classmethod
    def tearDownClass(cls):
        cls.bundle.stop()
        cls.clear_graph_cache()

    def encounter(self, definition, *, team=1, z=5):
        run = Run(names=("Zarya",))
        player, = run.players
        player.client = NpcClient(player)
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        self.assertTrue(run.match.switch_hero(player, heroes()[ZARYA]))
        player.mover.position[:] = [0, 1, 0]
        bot = npc.spawn(run.match, definition, (0, 1, z), team=team)
        self.assertEqual(bot.hero, definition.hero)
        self.assertEqual(bot.body, definition.body)
        self.assertIsNotNone(bot.body_script)
        return run, player, bot

    @staticmethod
    def fire(run, player, frames=51, buttons=1):
        for _ in range(frames):
            run.step({player: (0, 0, buttons)})

    def test_flux_selects_all_five_and_applies_their_authored_lift_branches(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, sigma, bot = self.encounter(definition)
                self.assertTrue(run.match.switch_hero(sigma, heroes()[sigma_flux.HERO]))
                sigma.mover.position[:] = [0, 1, 0]
                fight = run.match.combat
                vitals = fight.vitals[bot.entity]
                before = vitals.total
                point = tuple(bot.position)
                flux = self.cast_flux(run, sigma, point)
                peak = point[1]
                for _ in range(150):
                    run.step({})
                    peak = max(peak, bot.position[1])
                self.assertEqual(tuple(entity.id for entity in flux.vars[375].value()), (bot.entity,))
                self.assertEqual(before - vitals.total, 50)
                heavy = definition.name == "Heavy Assault"
                self.assertEqual(bot.impulse_mover is None, heavy)
                if not heavy:
                    self.assertTrue(bot.impulse_mover.airborne)
                    self.assertGreater(bot.movement(run.frame).air_ticks, 0)
                for _ in range(260):
                    run.step({})
                    peak = max(peak, bot.position[1])
                self.assertAlmostEqual(bot.position[1], point[1], delta=0.01)
                self.assertIsNone(bot.impulse_mover)
                self.assertFalse(any(i.graph.index == sigma_flux.FLUX_EFFECT
                                     for i in bot.body_script.component.instances.values()))
                if heavy:
                    self.assertEqual(peak, point[1])
                    self.assertEqual(before - vitals.total, 50)
                else:
                    self.assertGreater(peak - point[1], 1)
                    self.assertEqual(before - vitals.total, 50 + vitals.maximum / 2)

    def test_flux_releases_each_npc_after_team_change(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, sigma, bot = self.encounter(definition)
                self.assertTrue(run.match.switch_hero(sigma, heroes()[sigma_flux.HERO]))
                sigma.mover.position[:] = (0, 1, 0)
                sigma.mover.body = None
                fight = run.match.combat
                vitals = fight.vitals[bot.entity]
                initial = vitals.total
                ground = bot.position[1]
                self.cast_flux(run, sigma, tuple(bot.position))
                heavy = definition.name == "Heavy Assault"
                damage = 0 if heavy else 50
                for _ in range(180):
                    run.step({})
                    if sigma_flux.has_effect(bot) and (heavy or bot.position[1] > ground + 1):
                        break
                self.assertTrue(sigma_flux.has_effect(bot))
                self.assertEqual(vitals.total, initial - damage)
                self.assertEqual(bot.impulse_mover is None, heavy)
                bot.team = sigma.team
                run.step({})
                self.assertFalse(sigma_flux.has_effect(bot))
                if heavy:
                    for _ in range(60):
                        run.step({})
                for _ in range(260):
                    run.step({})
                    if bot.impulse_mover is None:
                        break
                self.assertIsNone(bot.impulse_mover)
                self.assertAlmostEqual(bot.position[1], ground, delta=0.01)
                self.assertEqual(vitals.total, initial - damage)
                self.assertEqual(sigma.stats.values.get(combat.stats.HERO_DAMAGE, 0), damage)

    def start_npc_graviton_flux_overlap(self, definition):
        run, zarya, bot = self.encounter(definition)
        match = run.match
        fight = match.combat
        sigma = match.add_player(2, "Sigma", sigma_flux.HERO, zarya.team, False)
        in_world(match, sigma)
        sigma.client = NpcClient(sigma)
        run.players.append(sigma)
        self.assertTrue(match.switch_hero(sigma, heroes()[sigma_flux.HERO]))
        sigma.mover.position[:] = [-3, 1, 0]
        sigma.mover.body = None
        flux = self.cast_flux(run, sigma, bot.position)
        zarya.body_script.set_ult_charge(2100)
        angles = aim(fight._eye(zarya), (2, 1, 5))
        run.step({zarya: (*angles, 32)})
        return run, zarya, sigma, bot, flux

    def test_graviton_preserves_flux_lift_and_slam_for_each_actual_npc(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, zarya, sigma, bot, flux = self.start_npc_graviton_flux_overlap(definition)
                fight = run.match.combat
                point = bot.position
                vitals = fight.vitals[bot.entity]
                before = vitals.total

                peak = point[1]
                lifted_while_pulled = False
                for _ in range(420):
                    previous_x = bot.position[0]
                    run.step({})
                    peak = max(peak, bot.position[1])
                    if bot.position[1] > point[1] + 1 and fight.zarya_gravitons.fields:
                        lifted_while_pulled |= bot.position[0] > previous_x
                        self.assertIsNotNone(bot.impulse_mover)
                        self.assertEqual(sigma.stats.values.get(combat.stats.HERO_DAMAGE, 0), 50)
                self.assertEqual(tuple(entity.id for entity in flux.vars[375].value()), (bot.entity,))
                self.assertGreater(bot.position[0], point[0] + 1)
                self.assertAlmostEqual(bot.position[1], point[1], delta=0.01)
                self.assertFalse(fight.zarya_gravitons.fields)
                self.assertNotIn(bot.entity, fight.zarya_gravitons.npc_movers)
                self.assertFalse(sigma_flux.has_effect(bot))
                self.assertIsNone(bot.impulse_mover)
                flux_damage = sigma.stats.values[combat.stats.HERO_DAMAGE]
                field_damage = zarya.stats.values[combat.stats.HERO_DAMAGE]
                self.assertAlmostEqual(before - vitals.total, flux_damage + field_damage, places=4)
                if definition.name == "Heavy Assault":
                    self.assertEqual(peak, point[1])
                    self.assertEqual(flux_damage, 50)
                    self.assertAlmostEqual(field_damage, 20, places=4)
                else:
                    self.assertTrue(lifted_while_pulled)
                    self.assertGreater(peak, point[1] + 1)
                    self.assertAlmostEqual(flux_damage, 50 + vitals.maximum / 2)
                    self.assertGreater(field_damage, 0)
                    self.assertLess(field_damage, 20)

    def test_late_projection_absorbs_flux_slam_during_graviton_on_each_npc(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, zarya, sigma, bot, _ = self.start_npc_graviton_flux_overlap(definition)
                match = run.match
                fight = match.combat
                projector = match.add_player(3, "Projector", ZARYA, bot.team, False)
                in_world(match, projector)
                projector.client = NpcClient(projector)
                run.players.append(projector)
                self.assertTrue(match.switch_hero(projector, heroes()[ZARYA]))
                projector.mover.position[:] = [10, 1, 5]
                projector.mover.body = None
                heavy = definition.name == "Heavy Assault"
                for _ in range(300):
                    run.step({})
                    _, y, _ = bot.position
                    if heavy and sigma.stats.values.get(combat.stats.HERO_DAMAGE, 0) == 50:
                        break
                    if not heavy and 2 < y < 4 and bot.velocity[1] < -1:
                        break
                self.assertEqual(sigma.stats.values[combat.stats.HERO_DAMAGE], 50)
                field, = fight.zarya_gravitons.fields
                npc_target = next(t for t in fight._targets() if t.owner is bot)
                x, y, z = npc_target.feet
                angles = aim(fight._eye(projector), (x, y + npc_target.eye, z))
                run.step({projector: (*angles, 16)})
                barriers = fight.zarya_barriers
                self.assertTrue(barriers.protects(bot))
                pool = barriers.projected[projector.body]
                vitals = fight.vitals[bot.entity]
                before = vitals.total
                field_damage = zarya.stats.values[combat.stats.HERO_DAMAGE]
                self.assertEqual(pool.remaining, 200)

                if heavy:
                    for _ in range(12):
                        run.step({})
                    self.assertAlmostEqual(pool.remaining, 199.04, places=4)
                else:
                    for _ in range(35):
                        previous = pool.remaining
                        run.step({})
                        if previous - pool.remaining > 1:
                            break
                    self.assertLess(previous, 200)
                    self.assertGreater(previous, 0)
                    self.assertIsNone(bot.impulse_mover)
                    expected = max(0, previous - vitals.maximum / 2)
                    self.assertAlmostEqual(pool.remaining, expected, delta=0.081)
                    self.assertEqual(pool.remaining == 0, vitals.maximum / 2 >= previous)
                self.assertIn(field, fight.zarya_gravitons.fields)
                self.assertEqual(sigma.stats.values[combat.stats.HERO_DAMAGE], 50)
                self.assertAlmostEqual(before - vitals.total,
                                       zarya.stats.values[combat.stats.HERO_DAMAGE] - field_damage, places=4)
                self.assertLessEqual(before - vitals.total, 0.081)
                energy = projector.body_script.component.entity_var(87).value()
                self.assertAlmostEqual(energy, (200 - pool.remaining) / 5, places=4)

    @staticmethod
    def cast_flux(run, sigma, point):
        fight = run.match.combat
        component = sigma.body_script.component
        sigma.body_script.set_ult_charge(component.entity_var(338).value())
        for frame in range(81):
            angles = aim(fight._eye(sigma), point)
            run.step({sigma: (*angles, 32 if frame == 0 else 1 if frame == 80 else 0)})
        return next(i for i in component.instances.values() if i.graph.index == sigma_flux.FLUX)

    def test_projected_barrier_absorbs_flux_for_each_actual_npc(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run = Run(names=("Sigma", "Zarya"))
                sigma, zarya = run.players
                match = run.match
                fight = match.combat
                match.bots.clear()
                match._pending_bot_scripts.clear()
                for player in run.players:
                    player.client = NpcClient(player)
                self.assertTrue(match.switch_hero(sigma, heroes()[sigma_flux.HERO]))
                self.assertTrue(match.switch_hero(zarya, heroes()[ZARYA]))
                sigma.team, zarya.team = 0, 1
                sigma.mover.position[:] = [0, 1, 0]
                zarya.mover.position[:] = [10, 1, 5]
                bot = npc.spawn(match, definition, (0, 1, 5), team=1)
                vitals = fight.vitals[bot.entity]
                before = vitals.total
                self.cast_flux(run, sigma, tuple(bot.position))
                heavy = definition.name == "Heavy Assault"
                if heavy:
                    for _ in range(40):
                        run.step({})
                    self.assertEqual(vitals.total, before)
                else:
                    for _ in range(250):
                        run.step({})
                        if 3 < bot.position[1] < 8 and bot.velocity[1] < -1:
                            break
                    self.assertLess(bot.velocity[1], -1)
                    self.assertEqual(vitals.total, before - 50)
                angles = aim(fight._eye(zarya), (bot.position[0], bot.position[1] + 1, bot.position[2]))
                run.step({zarya: (*angles, 16)})
                for _ in range(50):
                    run.step({})
                    barrier = fight.zarya_barriers.projected.get(zarya.body)
                    if barrier is not None and barrier.remaining < 200:
                        break
                self.assertIsNotNone(barrier)
                self.assertEqual(barrier.recipient, bot.entity)
                absorbed = 50 if heavy else min(200, vitals.maximum / 2)
                self.assertEqual(barrier.remaining, 200 - absorbed)
                self.assertAlmostEqual(zarya.body_script.component.entity_var(87).value(), absorbed / 5)
                self.assertEqual(vitals.total, before if heavy else before - 50)
                self.assertIsNone(bot.impulse_mover)

    def test_orisa_halt_respects_projected_barrier_and_heavy_immunity(self):
        for definition, shielded in product(self.catalog.values(), (False, True)):
            with self.subTest(npc=definition.name, shielded=shielded):
                run = Run(names=("Orisa", "Zarya"))
                orisa, zarya = run.players
                match = run.match
                fight = match.combat
                match.bots.clear()
                match._pending_bot_scripts.clear()
                for player in run.players:
                    player.client = NpcClient(player)
                self.assertTrue(match.switch_hero(orisa, heroes()[0x02E000000000013E]))
                self.assertTrue(match.switch_hero(zarya, heroes()[ZARYA]))
                orisa.team, zarya.team = 0, 1
                orisa.mover.position[:] = [0, 1, 0]
                zarya.mover.position[:] = [10, 1, 5]
                bot = npc.spawn(match, definition, (0, 1, 5), team=1)
                start = tuple(bot.position)
                before = fight.vitals[bot.entity].total
                for frame in range(26):
                    run.step({orisa: (0, 0, 2 if frame in (0, 25) else 0),
                              zarya: (-16384, 0, 16 if frame == 0 and shielded else 0)})
                orb, = fight.rockets
                self.assertIsNotNone(orb.halt_at)
                for _ in range(32):
                    run.step({})
                pulls = not shielded and definition.name != "Heavy Assault"
                self.assertEqual(bot.entity in fight.halt_pulls, pulls)
                if pulls:
                    self.assertGreater(bot.position[2], start[2] + 1)
                    self.assertIsNotNone(bot.impulse_mover)
                else:
                    self.assertEqual(tuple(bot.position), start)
                if shielded:
                    self.assertTrue(fight.zarya_barriers.protects(bot))
                    barrier = fight.zarya_barriers.projected[zarya.body]
                    self.assertEqual((barrier.recipient, barrier.remaining), (bot.entity, 200))
                    self.assertIn(zarya.body_script.component.entity_var(87).value(), (None, 0))
                for _ in range(55):
                    run.step({})
                self.assertNotIn(bot.entity, fight.halt_pulls)
                self.assertIsNone(bot.impulse_mover)
                self.assertEqual(fight.vitals[bot.entity].total, before)

    def test_late_projection_clears_halt_on_each_pull_eligible_npc(self):
        eligible = (d for d in self.catalog.values() if d.name != "Heavy Assault")
        for definition, slow in product(eligible, (False, True)):
            with self.subTest(npc=definition.name, slow=slow):
                run, fight, caster, orisa, bot = self.ana_projection_arena(definition, protected=False)
                self.assertTrue(run.match.switch_hero(orisa, heroes()[0x02E000000000013E]))
                orisa.mover.position[:] = [0, 1, 0]
                orisa.mover.body = None
                caster.mover.position[:] = [10, 1, 5]
                caster.mover.body = None
                before = fight.vitals[bot.entity].total
                for frame in range(26):
                    run.step({orisa: (0, 0, 2 if frame in (0, 25) else 0)})
                orb, = fight.rockets
                while fight.tick < orb.halt_at + 3:
                    run.step({})
                _, pull_end, effect_end = fight.halt_pulls[bot.entity]
                self.assertGreater(bot.velocity[2], 1)
                if slow:
                    while fight.tick < pull_end + 2:
                        run.step({})
                    self.assertTrue(orisa_halt.slowing(fight, bot.entity, fight.tick))
                npc_target = next(target for target in fight._targets() if target.owner is bot)
                x, y, z = npc_target.feet
                angles = aim(fight._eye(caster), (x, y + npc_target.eye, z))
                run.step({caster: (*angles, 16)})
                self.assertTrue(fight.zarya_barriers.protects(bot))
                run.step({})
                self.assertLess(fight.tick, effect_end if slow else pull_end)
                self.assertNotIn(bot.entity, fight.halt_pulls)
                released_xz = bot.position[::2]
                for _ in range(12):
                    run.step({})
                    self.assertEqual(bot.position[::2], released_xz)
                self.assertEqual(fight.vitals[bot.entity].total, before)
                self.assertEqual(fight.zarya_barriers.projected[caster.body].remaining, 200)
                self.assertIn(caster.body_script.component.entity_var(87).value(), (None, 0))

    def test_sparse_halt_preserves_npc_pull_before_projected_barrier(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                distances = []
                for sparse in (False, True):
                    run, fight, caster, orisa, bot = self.ana_projection_arena(definition, protected=False)
                    match = run.match
                    self.assertTrue(match.switch_hero(orisa, heroes()[0x02E000000000013E]))
                    orisa.mover.position[:] = [0, 1, 0]
                    orisa.mover.body = None
                    caster.mover.position[:] = [10, 1, 5]
                    caster.mover.body = None
                    before = fight.vitals[bot.entity].total
                    for frame in range(26):
                        run.step({orisa: (0, 0, 2 if frame in (0, 25) else 0)})
                    orb, = fight.rockets
                    initial_z = bot.position[2]
                    activation = orb.halt_at + 4
                    endpoint = orb.halt_at + combat.COMBAT_CATCH_UP + 7
                    if not sparse:
                        for tick in range(fight.tick + 1, activation):
                            match.update(tick * combat.TICK, tick, frames=False)

                    target = next(target for target in fight._targets() if target.owner is bot)
                    x, y, z = target.feet
                    yaw, pitch = aim(fight._eye(caster), (x, y + target.eye, z))
                    match.tick = activation
                    caster.take_commands([Command(activation + 500, yaw=yaw, pitch=pitch, buttons=16)])
                    self.assertTrue(fight.zarya_barriers.protects(bot))
                    if not sparse:
                        for tick in range(activation, endpoint):
                            match.update(tick * combat.TICK, tick, frames=False)
                    for _ in range(2):
                        match.update(endpoint * combat.TICK, endpoint, frames=False)
                        self.assertNotIn(bot.entity, fight.halt_pulls)
                    distances.append(bot.position[2] - initial_z)
                    self.assertEqual(fight.vitals[bot.entity].total, before)
                    self.assertEqual(fight.zarya_barriers.projected[caster.body].remaining, 200)
                    self.assertIn(caster.body_script.component.entity_var(87).value(), (None, 0))
                if definition.name == "Heavy Assault":
                    self.assertEqual(distances[0], 0)
                else:
                    self.assertGreater(distances[0], 0.8)
                self.assertAlmostEqual(distances[1], distances[0], places=4)

    def test_graviton_hands_off_to_live_halt_on_each_eligible_npc(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, zarya, bot = self.encounter(definition)
                match = run.match
                fight = match.combat
                orisa = match.add_player(2, "Orisa", 0x02E000000000013E, zarya.team, False)
                in_world(match, orisa)
                orisa.client = NpcClient(orisa)
                run.players.append(orisa)
                self.assertTrue(match.switch_hero(orisa, heroes()[0x02E000000000013E]))
                orisa.mover.position[:] = [2, 1, 0]
                orisa.mover.body = None
                zarya.mover.position[:] = [-3, 1, 0]
                zarya.body_script.set_ult_charge(2100)
                angles = aim(fight._eye(zarya), (2, 1, 5))
                run.step({zarya: (*angles, 32)})
                for _ in range(80):
                    if fight.zarya_gravitons.fields:
                        break
                    run.step({})
                field, = fight.zarya_gravitons.fields
                vitals = fight.vitals[bot.entity]
                before = vitals.total
                while fight.tick < field.end_tick - 56:
                    run.step({})
                for frame in range(26):
                    run.step({orisa: (0, 0, 2 if frame in (0, 25) else 0)})
                orb, = fight.rockets
                self.assertLess(orb.halt_at, field.end_tick)
                while fight.tick < orb.halt_at:
                    run.step({})
                self.assertIn(field, fight.zarya_gravitons.fields)
                self.assertIn(bot.entity, fight.zarya_gravitons.npc_movers)
                self.assertIsNone(bot.impulse_mover)

                heavy = definition.name == "Heavy Assault"
                if heavy:
                    self.assertNotIn(bot.entity, fight.halt_pulls)
                    pull_end = effect_end = field.end_tick
                else:
                    goal, pull_end, effect_end = fight.halt_pulls[bot.entity]
                    self.assertGreater(pull_end, field.end_tick)
                    self.assertGreater(goal[2], bot.position[2] + 1)
                while fight.tick < field.end_tick - 1:
                    run.step({})
                    self.assertIsNone(bot.impulse_mover)
                last_graviton_position = bot.position
                run.step({})
                self.assertFalse(fight.zarya_gravitons.fields)
                self.assertNotIn(bot.entity, fight.zarya_gravitons.npc_movers)
                if heavy:
                    self.assertEqual(bot.position, last_graviton_position)
                else:
                    self.assertGreater(bot.position[2], last_graviton_position[2] + 0.2)
                    self.assertIsNotNone(bot.impulse_mover)
                    while fight.tick < pull_end:
                        run.step({})
                    self.assertTrue(orisa_halt.slowing(fight, bot.entity, fight.tick))
                while fight.tick < effect_end + 45:
                    run.step({})
                self.assertNotIn(bot.entity, fight.halt_pulls)
                self.assertIsNone(bot.impulse_mover)
                self.assertAlmostEqual(before - vitals.total, 20, places=4)
                self.assertAlmostEqual(zarya.stats.values[combat.stats.HERO_DAMAGE], 20, places=4)
                self.assertEqual(orisa.stats.values.get(combat.stats.HERO_DAMAGE, 0), 0)

    def test_orisa_halt_slows_trooper_patrol_then_restores_speed(self):
        definition = next(d for d in self.catalog.values() if d.name == "Trooper")
        run, orisa, bot = self.encounter(definition)
        self.assertTrue(run.match.switch_hero(orisa, heroes()[0x02E000000000013E]))
        orisa.mover.position[:] = [0, 1, 0]
        fight = run.match.combat
        for frame in range(26):
            run.step({orisa: (0, 0, 2 if frame in (0, 25) else 0)})
        orb, = fight.rockets
        while fight.tick < orb.halt_at:
            run.step({})
        _, pull_end, effect_end = fight.halt_pulls[bot.entity]
        while fight.tick < pull_end:
            run.step({})
        x, y, z = bot.position
        bot.route, bot.index, bot.wait = [(x + 25, y, z)], -1, 0.0
        paces = []
        while fight.tick < effect_end - 1:
            run.step({})
            if bot.impulse_mover is None:
                paces.append(bot.pace)
        self.assertTrue(paces)
        self.assertAlmostEqual(max(paces), bot.speed * 0.35)
        for _ in range(45):
            run.step({})
        self.assertNotIn(bot.entity, fight.halt_pulls)
        self.assertAlmostEqual(bot.pace, bot.speed)

    def test_actual_npc_mover_parameters_drive_combat_capsules(self):
        expected = {"Trooper": (1.7, 0.5, 5.75), "Enforcer": (1.9, 0.5, 5.0),
                    "Sniper": (1.75, 0.5, 4.5), "Assassin": (1.5, 0.5, 14.0),
                    "Heavy Assault": (2.3, 0.7, 2.2)}
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                data = defined_mover_data(definition.body)
                self.assertEqual((data.stand_height, data.stand_radius, data.run_forward),
                                 expected[definition.name])
                run, _, bot = self.encounter(definition)
                target = next(t for t in run.match.combat._targets() if t.entity == bot.entity)
                self.assertEqual((target.height, target.radius), expected[definition.name][:2])

    def test_beam_deals_60_to_each_actual_enemy_body(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, player, bot = self.encounter(definition)
                player.body_script.ult_seconds = 0
                vitals = run.match.combat.vitals[bot.entity]
                before = vitals.total
                self.fire(run, player)
                self.assertAlmostEqual(before - vitals.total, 60)
                self.assertAlmostEqual(player.body_script.component.entity_var(342).value(), 60, places=4)
                self.assertTrue(player.client.messages(combat.HIT))

    def test_beam_cover_transition_does_not_backfill_damage_on_each_npc(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, caster, bot = self.encounter(definition)
                fight = run.match.combat
                vitals = fight.vitals[bot.entity]
                before = vitals.total
                self.fire(run, caster, 31)
                self.assertAlmostEqual(vitals.total, before - 36, places=4)
                original = run.match.collision_world
                run.match.collision_world = World([
                    (-10, 1, -10, -10, 1, 20, 20, 1, 20), (-10, 1, -10, 20, 1, 20, 20, 1, -10),
                    (-5, 0, 2.5, 5, 0, 2.5, 5, 5, 2.5), (-5, 0, 2.5, 5, 5, 2.5, -5, 5, 2.5),
                ])
                weapon = next(i for i in caster.body_script.component.instances.values()
                              if i.graph.index == 0x0241)
                ammo = weapon.vars[53].value()
                self.fire(run, caster, 20)
                self.assertAlmostEqual(vitals.total, before - 36, places=4)
                self.assertLess(weapon.vars[53].value(), ammo)
                run.match.collision_world = original
                self.fire(run, caster, 1)
                self.assertAlmostEqual(vitals.total, before - 37.2, places=4)
                self.fire(run, caster, 12, 0)
                self.assertAlmostEqual(vitals.total, before - 37.2, places=4)

    def test_beam_range_reentry_does_not_backfill_damage_on_each_npc(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, caster, bot = self.encounter(definition, z=14)
                fight = run.match.combat
                target = next(t for t in fight._targets() if t.owner is bot)
                a, b = target.segment()
                contact = combat.ray_capsule(fight._eye(caster), (0, 0, 1), a, b, target.radius + 0.15)
                self.assertIsNotNone(contact)
                boundary = bot.position[2] + 15 - contact
                x, y, _ = bot.position
                bot.position = (x, y, boundary - 0.05)
                run.step({})
                vitals = fight.vitals[bot.entity]
                before = vitals.total
                self.fire(run, caster, 31)
                self.assertAlmostEqual(vitals.total, before - 36, places=4)
                bot.position = (x, y, boundary + 0.05)
                self.fire(run, caster, 20)
                self.assertAlmostEqual(vitals.total, before - 36, places=4)
                bot.position = (x, y, boundary - 0.05)
                self.fire(run, caster, 1)
                self.assertAlmostEqual(vitals.total, before - 37.2, places=4)

    def test_held_beam_rechecks_npc_team_without_backfilling_damage(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, caster, bot = self.encounter(definition)
                vitals = run.match.combat.vitals[bot.entity]
                before = vitals.total
                self.fire(run, caster, 31)
                self.assertAlmostEqual(vitals.total, before - 36, places=4)
                enemy_team = bot.team
                bot.team = caster.team
                self.fire(run, caster, 20)
                self.assertAlmostEqual(vitals.total, before - 36, places=4)
                bot.team = enemy_team
                self.fire(run, caster, 1)
                self.assertAlmostEqual(vitals.total, before - 37.2, places=4)

    def test_beam_does_not_damage_allied_five_bodies(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, player, bot = self.encounter(definition, team=0)
                player.body_script.ult_seconds = 0
                before = run.match.combat.vitals[bot.entity].total
                self.fire(run, player)
                self.assertEqual(run.match.combat.vitals[bot.entity].total, before)
                self.assertEqual(player.body_script.component.entity_var(342).value(), 0)

    def test_secondary_deals_authored_47_to_each_body(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, player, bot = self.encounter(definition)
                script = player.body_script
                script.ult_seconds = 0
                charge = script.component.entity_var(ULT_CHARGE)
                vitals = run.match.combat.vitals[bot.entity]
                before = vitals.total
                start = bot.position
                self.fire(run, player, 1, 2)
                for _ in range(60):
                    run.step({})
                    if vitals.total < before:
                        break
                self.assertAlmostEqual(before - vitals.total, 47)
                self.assertAlmostEqual(charge.value(), 47)
                if definition.name == "Heavy Assault":
                    passive = next(i for i in bot.body_script.component.instances.values()
                                   if i.graph.index == 0x1254)
                    self.assertTrue(passive.states[0].active)
                    self.assertIsNone(bot.impulse_mover)
                    self.assertEqual(bot.position, start)
                    self.assertEqual(bot.velocity, (0, 0, 0))
                    continue
                self.assertIsNotNone(bot.impulse_mover)
                self.assertAlmostEqual(bot.velocity[1], 8)
                self.assertAlmostEqual(bot.velocity[2], 5)
                self.fire(run, player, 8, 0)
                self.assertGreater(bot.position[1], start[1])
                self.assertGreater(bot.position[2], start[2])
                self.fire(run, player, 90, 0)
                self.assertAlmostEqual(before - vitals.total, 47)
                self.assertIsNone(bot.impulse_mover)

    def test_secondary_hold_reloads_and_resumes_against_each_npc(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, caster, bot = self.encounter(definition)
                fight = run.match.combat
                vitals = fight.vitals[bot.entity]
                before = vitals.total
                weapon = next(i for i in caster.body_script.component.instances.values()
                              if i.graph.index == 0x0241)
                changes = []
                ammo = 100
                for frame in range(315):
                    if frame in (0, 314):
                        x, y, z = bot.position
                        angles = aim(fight._eye(caster), (x, y + 1, z))
                    else:
                        angles = (16384, 0)
                    run.step({caster: (*angles, 2)})
                    remaining = weapon.vars[53].value()
                    if remaining != ammo:
                        changes.append((frame, remaining))
                        ammo = remaining
                    if frame in (187, 286, 313):
                        self.assertAlmostEqual(vitals.total, before - 47, places=4)
                self.assertEqual(changes, [(0, 75), (62, 50), (125, 25), (188, 0), (287, 100), (314, 75)])
                for _ in range(90):
                    run.step({})
                self.assertAlmostEqual(vitals.total, before - 94, places=4)
                self.assertEqual(weapon.vars[53].value(), 75)

    def test_secondary_uses_shared_ammo_after_primary_on_each_npc(self):
        for definition, low_ammo in product(self.catalog.values(), (False, True)):
            with self.subTest(npc=definition.name, low_ammo=low_ammo):
                run, caster, bot = self.encounter(definition)
                fight = run.match.combat
                vitals = fight.vitals[bot.entity]
                before = vitals.total
                weapon = next(i for i in caster.body_script.component.instances.values()
                              if i.graph.index == 0x0241)
                for _ in range(400):
                    run.step({caster: (16384, 0, 1)})
                    ammo = weapon.vars[53].value()
                    if ammo < (10 if low_ammo else 27):
                        break
                self.assertEqual(vitals.total, before)
                self.assertTrue(9 < ammo < 10 if low_ammo else 26 < ammo < 27)
                for _ in range(6):
                    run.step({caster: (0, 0, 2)})
                    self.assertFalse(fight.rockets)
                    self.assertEqual(weapon.vars[53].value(), ammo)
                run.step({caster: (0, 0, 2)})
                self.assertEqual(len(fight.rockets), 1)
                expected_ammo = 0 if low_ammo else 2
                self.assertEqual(weapon.vars[53].value(), expected_ammo)
                for _ in range(60):
                    run.step({})
                self.assertFalse(fight.rockets)
                self.assertEqual(weapon.vars[53].value(), expected_ammo)
                self.assertAlmostEqual(vitals.total, before - 47, places=4)

    def test_secondary_fractional_ammo_rounding_and_reload_on_each_npc(self):
        for definition, threshold in product(self.catalog.values(), (0.5, 1)):
            with self.subTest(npc=definition.name, threshold=threshold):
                run, caster, bot = self.encounter(definition)
                fight = run.match.combat
                vitals = fight.vitals[bot.entity]
                before = vitals.total
                weapon = next(i for i in caster.body_script.component.instances.values()
                              if i.graph.index == 0x0241)
                for _ in range(400):
                    run.step({caster: (16384, 0, 1)})
                    ammo = weapon.vars[53].value()
                    if ammo < threshold:
                        break
                last_shot = threshold == 1
                self.assertTrue(0 < ammo < threshold)
                self.assertEqual(ammo >= 0.5, last_shot)
                for frame in range(101):
                    run.step({caster: (0 if frame < 73 else 16384, 0, 2)})
                    if frame == 6:
                        self.assertEqual(len(fight.rockets), int(last_shot))
                        self.assertEqual(weapon.vars[53].value(), 0)
                    if frame == 73:
                        self.assertEqual(weapon.vars[53].value(), 100)
                self.assertEqual(weapon.vars[53].value(), 75)
                self.assertEqual(len(fight.rockets), 1)
                for _ in range(90):
                    run.step({})
                self.assertAlmostEqual(vitals.total, before - (47 if last_shot else 0), places=4)
                self.assertEqual(weapon.vars[53].value(), 75)

    def test_primary_has_priority_over_held_secondary_on_each_npc(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, caster, bot = self.encounter(definition)
                fight = run.match.combat
                vitals = fight.vitals[bot.entity]
                before = vitals.total
                weapon = next(i for i in caster.body_script.component.instances.values()
                              if i.graph.index == 0x0241)
                for _ in range(31):
                    run.step({caster: (0, 0, 3)})
                    self.assertFalse(fight.rockets)
                self.assertTrue(weapon.state(10).active)
                self.assertAlmostEqual(vitals.total, before - 36, places=4)
                self.assertGreater(weapon.vars[53].value(), 90)
                for _ in range(6):
                    run.step({caster: (0, 0, 2)})
                    self.assertFalse(fight.rockets)
                run.step({caster: (0, 0, 2)})
                self.assertEqual(len(fight.rockets), 1)
                self.assertFalse(weapon.state(10).active)
                self.assertEqual(weapon.vars[53].value(), 65)
                for _ in range(60):
                    run.step({})
                self.assertFalse(fight.rockets)
                self.assertAlmostEqual(vitals.total, before - 83, places=4)
                self.assertEqual(weapon.vars[53].value(), 65)

    def test_secondary_recovery_delays_primary_on_each_npc(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, caster, bot = self.encounter(definition)
                fight = run.match.combat
                vitals = fight.vitals[bot.entity]
                before = vitals.total
                weapon = next(i for i in caster.body_script.component.instances.values()
                              if i.graph.index == 0x0241)
                run.step({caster: (16384, 0, 2)})
                self.assertEqual(len(fight.rockets), 1)
                for _ in range(30):
                    run.step({caster: (0, 0, 1)})
                    self.assertFalse(weapon.state(10).active)
                    self.assertEqual(weapon.vars[53].value(), 75)
                    self.assertEqual(vitals.total, before)
                run.step({caster: (0, 0, 1)})
                beam = weapon.state(10)
                self.assertTrue(beam.active)
                frame_ms = caster.body_script.component.frame_ms
                self.assertEqual(beam.start - run.frame * frame_ms, 15)
                self.assertEqual(vitals.total, before)
                beam_ticks = 50
                beam_damage = 75 * combat.TICK * beam_ticks
                for _ in range(beam_ticks):
                    run.step({caster: (0, 0, 1)})
                self.assertAlmostEqual(vitals.total, before - beam_damage, places=4)
                remaining = weapon.vars[53].value()
                self.assertLess(remaining, 75)
                for _ in range(60):
                    run.step({})
                self.assertFalse(fight.rockets)
                self.assertAlmostEqual(vitals.total, before - beam_damage, places=4)
                self.assertEqual(weapon.vars[53].value(), remaining)

    def test_allied_bodies_reject_secondary_and_melee_damage(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, player, bot = self.encounter(definition, team=0, z=1.5)
                script = player.body_script
                script.ult_seconds = 0
                charge = script.component.entity_var(ULT_CHARGE)
                before = run.match.combat.vitals[bot.entity].total
                self.fire(run, player, 1, 2)
                self.fire(run, player, 90, 0)
                self.assertEqual(charge.value(), 0)
                self.fire(run, player, 1, 0x800)
                self.fire(run, player, 40, 0)
                self.assertEqual(run.match.combat.vitals[bot.entity].total, before)
                self.assertEqual(charge.value(), 0)
                self.assertIsNone(bot.impulse_mover)

    def test_secondary_impulse_respects_wall_duplicate_tick_and_landing(self):
        wall = World([
            (-100, 1, -100, -100, 1, 100, 100, 1, 100),
            (-100, 1, -100, 100, 1, 100, 100, 1, -100),
            (-10, 1, 6, 10, 10, 6, 10, 1, 6),
            (-10, 1, 6, -10, 10, 6, 10, 10, 6),
        ])
        for definition in self.catalog.values():
            if definition.name == "Heavy Assault":
                continue  # Its authored passive suppresses impulse; covered in the damage case.
            with self.subTest(npc=definition.name):
                run, source, bot = self.encounter(definition)
                run.match.collision_world = wall
                run.step({source: (0, 0, 2)})
                for _ in range(60):
                    run.step({})
                    if bot.impulse_mover is not None:
                        break
                self.assertIsNotNone(bot.impulse_mover)
                position, velocity = bot.position, bot.velocity
                run.match.update(run.frame * combat.TICK, run.frame)
                self.assertEqual((bot.position, bot.velocity), (position, velocity))
                farthest = bot.position[2]
                for _ in range(135):
                    run.step({})
                    farthest = max(farthest, bot.position[2])
                self.assertGreater(farthest, 5)
                self.assertLess(farthest, 6)
                self.assertAlmostEqual(bot.position[1], 1)
                self.assertIsNone(bot.impulse_mover)

    def test_melee_deals_30_to_each_body(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, player, bot = self.encounter(definition, z=1.5)
                script = player.body_script
                script.ult_seconds = 0
                vitals = run.match.combat.vitals[bot.entity]
                before = vitals.total
                self.fire(run, player, 1, 0x800)
                self.fire(run, player, 40, 0)
                self.assertAlmostEqual(before - vitals.total, 30)
                self.assertAlmostEqual(script.component.entity_var(ULT_CHARGE).value(), 30)

    def test_secondary_and_melee_lethal_charge_caps_on_each_actual_body(self):
        for definition, buttons, near_cap in product(self.catalog.values(), (2, 0x800), (False, True)):
            with self.subTest(npc=definition.name, buttons=buttons, near_cap=near_cap):
                run, player, bot = self.encounter(definition, z=5 if buttons == 2 else 1.5)
                script = player.body_script
                script.ult_seconds = 0
                cost = script.component.entity_var(ULT_COST).value()
                self.assertEqual(cost, 2100)
                script.set_ult_charge(cost - 5 if near_cap else 0)
                vitals = run.match.combat.vitals[bot.entity]
                vitals.damage(vitals.total - 12)
                self.assertEqual(vitals.total, 12)
                self.fire(run, player, 1, buttons)
                self.fire(run, player, 60, 0)
                self.assertTrue(vitals.dead)
                self.assertEqual(vitals.total, 0)
                self.assertAlmostEqual(script.component.entity_var(ULT_CHARGE).value(),
                                       cost if near_cap else 12)

    def test_melee_recovery_and_hold_on_each_actual_body(self):
        for definition in self.catalog.values():
            for held in (False, True):
                with self.subTest(npc=definition.name, held=held):
                    run, player, bot = self.encounter(definition, z=1.5)
                    vitals = run.match.combat.vitals[bot.entity]
                    before = vitals.total
                    graph_instance = next(
                        instance for instance in player.body_script.component.instances.values()
                        if instance.graph.index == 0x0043
                    )
                    self.fire(run, player, 1, 0x800)
                    start = run.frame
                    self.assertAlmostEqual(before - vitals.total, 30)
                    for offset in range(1, 130 if held else 62):
                        self.fire(run, player, 1, 0x800 if held or offset % 2 == 0 else 0)
                        self.assertEqual(graph_instance.state(4).counter, 1)
                        self.assertAlmostEqual(before - vitals.total, 30)
                        self.assertEqual(len(player.client.messages(combat.HIT)), 1)
                    if held:
                        self.fire(run, player, 1, 0)
                    self.fire(run, player, 1, 0x800)
                    self.assertEqual(run.frame - start, 131 if held else 62)
                    self.assertEqual(graph_instance.state(4).counter, 2)
                    self.assertAlmostEqual(before - vitals.total, 60)
                    self.assertEqual(len(player.client.messages(combat.HIT)), 2)

    def test_projected_barrier_protects_each_real_allied_body(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, player, bot = self.encounter(definition, team=0)
                self.fire(run, player, 1, 16)
                target = next(t for t in run.match.combat._targets() if t.owner is bot)
                vitals = run.match.combat.vitals[bot.entity]
                before = vitals.total
                # Explicit hostile actor fixture; this is not evidence of autonomous AI.
                enemy = npc.spawn(run.match, definition, (0, 1, 10), team=1)
                run.match.combat._damage(enemy, target, 100, False, None, run.frame)
                self.assertEqual(vitals.total, before)
                self.fire(run, player, 1, 0)
                self.assertEqual(player.body_script.component.entity_var(87).value(), 20)

    def test_beam_hits_projected_edge_outside_each_actual_npc_capsule(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, fight, caster, attacker, bot = self.ana_projection_arena(definition, protected=True)
                self.assertTrue(run.match.switch_hero(attacker, heroes()[ZARYA]))
                attacker.mover.position[:] = [1, 1, 0]
                attacker.mover.body = None
                attacker.body_script.ult_seconds = 0
                target = next(t for t in fight._targets() if t.owner is bot)
                self.assertLess(target.radius + 0.15, 1)
                barriers = fight.zarya_barriers
                self.assertTrue(barriers.protects(bot))
                pool = barriers.projected[caster.body]
                before = fight.vitals[bot.entity].total
                self.fire(run, attacker)
                run.step({})  # Caster processes the final absorption message on its next command.
                self.assertAlmostEqual(pool.remaining, 140, places=4)
                self.assertAlmostEqual(caster.body_script.component.entity_var(87).value(), 12, places=4)
                self.assertEqual(fight.vitals[bot.entity].total, before)
                self.assertEqual(attacker.body_script.component.entity_var(ULT_CHARGE).value(), 0)
                self.assertFalse(attacker.client.messages(combat.HIT))

    def test_zarya_projectiles_hit_projected_edge_outside_each_npc_capsule(self):
        for definition, ultimate in product(self.catalog.values(), (False, True)):
            with self.subTest(npc=definition.name, ultimate=ultimate):
                run, fight, caster, attacker, bot = self.ana_projection_arena(definition, protected=True)
                self.assertTrue(run.match.switch_hero(attacker, heroes()[ZARYA]))
                attacker.mover.position[:] = [1, 1, 0]
                attacker.mover.body = None
                attacker.body_script.ult_seconds = 0
                target = next(t for t in fight._targets() if t.owner is bot)
                self.assertLess(target.radius, 1)
                barriers = fight.zarya_barriers
                self.assertTrue(barriers.protects(bot))
                pool = barriers.projected[caster.body]
                vitals = fight.vitals[bot.entity]
                before = vitals.total
                if ultimate:
                    attacker.body_script.set_ult_charge(2100)
                self.fire(run, attacker, 1, 32 if ultimate else 2)
                rocket, = fight.rockets
                for _ in range(80):
                    if rocket not in fight.rockets:
                        break
                    run.step({})
                self.assertNotIn(rocket, fight.rockets)
                self.assertGreater(rocket.distance, 3)
                self.assertLess(rocket.distance, 5)
                absorbed = 5 if ultimate else 47
                self.assertAlmostEqual(pool.remaining, 200 - absorbed, places=4)
                self.assertAlmostEqual(caster.body_script.component.entity_var(87).value(),
                                       absorbed / 5, places=4)
                self.assertEqual(vitals.total, before)
                self.assertIsNone(bot.impulse_mover)
                self.assertEqual(attacker.body_script.component.entity_var(ULT_CHARGE).value(), 0)
                self.assertFalse(attacker.client.messages(combat.HIT))
                if ultimate:
                    field, = fight.zarya_gravitons.fields
                    self.assertIs(field.shooter, attacker)
                    self.assertAlmostEqual(field.position[0], 1, places=4)
                    self.assertLess(field.position[2], target.feet[2])
                else:
                    self.assertFalse(fight.zarya_gravitons.fields)

    def test_ana_projectiles_and_accretion_hit_projected_edge_on_each_npc(self):
        attacks = ((0x013B, 8, 5), (0x013B, 16, 50), (0x013B, 1, 70), (0x023B, 16, 70))
        for definition, (hero, button, absorbed) in product(self.catalog.values(), attacks):
            with self.subTest(npc=definition.name, hero=hero, button=button):
                run, fight, caster, attacker, bot = self.ana_projection_arena(definition, protected=True)
                if hero == 0x023B:
                    self.assertTrue(run.match.switch_hero(attacker, heroes()[0x02E0000000000000 | hero]))
                attacker.mover.position[:] = [1, 1, 0]
                attacker.mover.body = None
                caster.mover.position[:] = [10, 1, 5]
                caster.mover.body = None
                target = next(t for t in fight._targets() if t.owner is bot)
                self.assertLess(target.radius, 1)
                barriers = fight.zarya_barriers
                self.assertTrue(barriers.protects(bot))
                pool = barriers.projected[caster.body]
                vitals = fight.vitals[bot.entity]
                before = vitals.total
                angles = aim(fight._eye(attacker), (1, 2, 5))
                rocket = None
                for frame in range(100):
                    run.step({attacker: (*angles, button if frame == 0 else 0)})
                    if fight.rockets:
                        rocket, = fight.rockets
                    elif rocket is not None:
                        break
                self.assertIsNotNone(rocket)
                self.assertNotIn(rocket, fight.rockets)
                self.assertGreater(rocket.distance, 3)
                self.assertLess(rocket.distance, 5)
                self.assertEqual(pool.remaining, 200 - absorbed)
                energy = caster.body_script.component.entity_var(87).value()
                self.assertAlmostEqual(energy, absorbed / 5, places=4)
                self.assertEqual(vitals.total, before)
                self.assertIsNone(bot.impulse_mover)
                self.assertNotIn(bot.entity, fight.sleeps)
                self.assertNotIn(bot.entity, fight.stuns)
                self.assertEqual(vitals.healing_block_until, 0)
                self.assertFalse(fight.rifle_doses)
                self.assertFalse(attacker.client.messages(combat.HIT))

    def test_hypersphere_pair_hits_projected_edge_on_each_npc(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, fight, caster, attacker, bot = self.ana_projection_arena(definition, protected=True)
                self.assertTrue(run.match.switch_hero(attacker, heroes()[0x02E000000000023B]))
                attacker.mover.position[:] = [1.2, 1, 0]
                attacker.mover.body = None
                target = next(t for t in fight._targets() if t.owner is bot)
                barriers = fight.zarya_barriers
                self.assertTrue(barriers.protects(bot))
                pool = barriers.projected[caster.body]
                vitals = fight.vitals[bot.entity]
                before = vitals.total
                angles = aim(fight._eye(attacker), (1.2, 2, 9))
                launched = []
                for frame in range(60):
                    run.step({attacker: (*angles, 1 if frame == 0 else 0)})
                    for rocket in fight.rockets:
                        if rocket not in launched:
                            launched.append(rocket)
                    if len(launched) == 2 and not fight.rockets:
                        break
                self.assertEqual(len(launched), 2)
                self.assertFalse(fight.rockets)
                for rocket in launched:
                    self.assertLess(target.radius + rocket.sphere_radius, 1.2)
                    self.assertGreater(rocket.distance, 3)
                    self.assertLess(rocket.distance, 5)
                self.assertEqual(pool.remaining, 90)
                self.assertAlmostEqual(caster.body_script.component.entity_var(87).value(), 22)
                self.assertEqual(vitals.total, before)
                self.assertIsNone(bot.impulse_mover)
                hits = attacker.client.messages(combat.HIT)
                self.assertEqual([hit["+0x78"]["+0x0"]["+0x0"] for hit in hits], [caster.body] * 2)

    def test_hypersphere_airbursts_hit_projected_surface_on_each_npc(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, fight, caster, attacker, bot = self.ana_projection_arena(
                    definition, protected=True, target_z=22,
                )
                self.assertTrue(run.match.switch_hero(attacker, heroes()[0x02E000000000023B]))
                attacker.mover.position[:] = [3.75, 1, 0]
                caster.mover.position[:] = [-6, 1, 22]
                for actor in (attacker, caster):
                    actor.mover.body = None
                barriers = fight.zarya_barriers
                self.assertTrue(barriers.protects(bot))
                pool = barriers.projected[caster.body]
                target = next(t for t in fight._targets() if t.owner is bot)
                vitals = fight.vitals_of(target)
                before = vitals.total
                with patch.object(fight, "_explode", wraps=fight._explode) as bursts:
                    for frame in range(75):
                        run.step({attacker: (0, 0, 1 if frame == 0 else 0)})
                self.assertEqual(bursts.call_count, 2)
                a, b = target.segment()
                x, y, z = target.feet
                centre = (x, y + target.height / 2, z)
                absorbed = 0
                for burst in bursts.call_args_list:
                    rocket, point = burst.args
                    distance = combat.segment_distance(point, a, b) - target.radius
                    self.assertEqual(rocket.blast.splash(distance), 0)
                    surface = math.dist(point, centre) - zarya.BUBBLE_RADIUS
                    amount = rocket.blast.amount * rocket.blast.splash(surface)
                    self.assertGreater(amount, 0)
                    absorbed += amount
                self.assertAlmostEqual(pool.remaining, 200 - absorbed, places=4)
                energy = caster.body_script.component.entity_var(87).value()
                self.assertAlmostEqual(energy, absorbed / 5, places=4)
                self.assertEqual(vitals.total, before)
                self.assertIsNone(bot.impulse_mover)
                self.assertFalse(attacker.client.messages(combat.HIT))

    def test_genji_fan_hits_projected_edge_on_each_npc(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, fight, caster, attacker, bot = self.ana_projection_arena(definition, protected=True)
                self.assertTrue(run.match.switch_hero(attacker, heroes()[0x02E0000000000029]))
                attacker.mover.position[:] = [1.1, 1, 3.5]
                attacker.mover.body = None
                barriers = fight.zarya_barriers
                self.assertTrue(barriers.protects(bot))
                pool = barriers.projected[caster.body]
                vitals = fight.vitals[bot.entity]
                before = vitals.total
                angles = aim(fight._eye(attacker), (1.1, 2, 5))
                run.step({attacker: (*angles, 2)})
                launched = tuple(fight.rockets)
                self.assertEqual(len(launched), 3)
                for _ in range(30):
                    run.step({})
                for rocket in launched:
                    self.assertEqual(rocket.definition, combat.GENJI_SHURIKEN)
                    self.assertLess(rocket.distance, 1.5)
                self.assertEqual(pool.remaining, 113)
                self.assertAlmostEqual(caster.body_script.component.entity_var(87).value(), 17.4, places=4)
                self.assertEqual(vitals.total, before)
                self.assertFalse(attacker.client.messages(combat.HIT))

    def test_swift_strike_hits_projected_edge_on_each_npc(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, fight, caster, genji, bot = self.ana_projection_arena(definition, protected=True)
                self.assertTrue(run.match.switch_hero(genji, heroes()[0x02E0000000000029]))
                genji.mover.position[:] = [2.4, 1, 0]
                genji.mover.body = None
                caster.mover.position[:] = [9, 1, 5]
                caster.mover.body = None
                target = next(t for t in fight._targets() if t.owner is bot)
                self.assertLess(target.radius + 1.5, 2.4)
                barriers = fight.zarya_barriers
                self.assertTrue(barriers.protects(bot))
                pool = barriers.projected[caster.body]
                vitals = fight.vitals[bot.entity]
                before = vitals.total
                self.fire(run, genji, 1, 8)
                for _ in range(40):
                    run.step({})
                self.assertEqual(pool.remaining, 150)
                self.assertAlmostEqual(caster.body_script.component.entity_var(87).value(), 10)
                self.assertEqual(vitals.total, before)
                self.assertAlmostEqual(genji.mover.position[2], 15)
                self.assertFalse(fight.genji_strikes)
                self.assertNotIn(bot.entity, fight.genji_assists)
                self.assertFalse(genji.client.messages(combat.HIT))

    def test_dragonblade_hits_projected_edge_outside_each_npc_capsule(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, fight, caster, genji, bot = self.ana_projection_arena(definition, protected=True)
                self.assertTrue(run.match.switch_hero(genji, heroes()[0x02E0000000000029]))
                genji.mover.position[:] = [0, 1, -0.9]
                genji.mover.body = None
                caster.mover.position[:] = [9, 1, 5]
                caster.mover.body = None
                target = next(t for t in fight._targets() if t.owner is bot)
                self.assertLess(5 + target.radius, target.feet[2] - genji.mover.position[2])
                barriers = fight.zarya_barriers
                self.assertTrue(barriers.protects(bot))
                pool = barriers.projected[caster.body]
                vitals = fight.vitals[bot.entity]
                before = vitals.total
                script = genji.body_script
                script.ult_seconds = 0
                script.set_ult_charge(script.component.entity_var(ULT_COST).value())
                for frame in range(110):
                    buttons = 32 if frame == 1 else 1 if frame > 70 else 0
                    run.step({genji: (0, 0, buttons)})
                self.assertEqual(pool.remaining, 90)
                self.assertAlmostEqual(caster.body_script.component.entity_var(87).value(), 22)
                self.assertEqual(vitals.total, before)
                self.assertFalse(genji.client.messages(combat.HIT))
                self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)

    def test_quick_melee_hits_projected_edge_outside_each_npc_capsule(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, fight, caster, attacker, bot = self.ana_projection_arena(definition, protected=True)
                self.assertTrue(run.match.switch_hero(attacker, heroes()[ZARYA]))
                attacker.mover.position[:] = [1.8, 1, 3.5]
                attacker.mover.body = None
                attacker.body_script.ult_seconds = 0
                target = next(t for t in fight._targets() if t.owner is bot)
                self.assertLess(target.radius + 1, attacker.mover.position[0])
                barriers = fight.zarya_barriers
                self.assertTrue(barriers.protects(bot))
                pool = barriers.projected[caster.body]
                vitals = fight.vitals[bot.entity]
                before = vitals.total
                self.fire(run, attacker, 41, 0x800)
                run.step({})
                self.assertEqual(pool.remaining, 170)
                self.assertAlmostEqual(caster.body_script.component.entity_var(87).value(), 6, places=4)
                self.assertEqual(vitals.total, before)
                self.assertEqual(attacker.body_script.component.entity_var(ULT_CHARGE).value(), 0)
                self.assertFalse(attacker.client.messages(combat.HIT))

    def test_static_wall_blocks_melee_on_each_actual_body(self):
        wall = World([(-10, 0, 0.75, 10, 0, 0.75, 10, 10, 0.75),
                      (-10, 0, 0.75, 10, 10, 0.75, -10, 10, 0.75)])
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, player, bot = self.encounter(definition, z=1.5)
                vitals = run.match.combat.vitals[bot.entity]
                before = vitals.total
                with patch.object(run.match.combat, "_collision", return_value=wall):
                    self.fire(run, player, 1, 0x800)
                    self.fire(run, player, 40, 0)
                self.assertEqual(vitals.total, before)
                self.assertFalse(player.client.messages(combat.HIT))

    def test_static_wall_blocks_secondary_blast_on_each_actual_body(self):
        wall = World([(-10, 0, 5, 10, 0, 5, 10, 10, 5),
                      (-10, 0, 5, 10, 10, 5, -10, 10, 5)])
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, player, bot = self.encounter(definition, z=6)
                vitals = run.match.combat.vitals[bot.entity]
                before = vitals.total
                with patch.object(run.match.combat, "_collision", return_value=wall):
                    self.fire(run, player, 1, 2)
                    self.fire(run, player, 60, 0)
                self.assertEqual(vitals.total, before)
                self.assertFalse(player.client.messages(combat.HIT))

    def test_mounted_sniper_first_authored_shot_deals_100_to_zarya(self):
        definition = next(d for d in self.catalog.values() if d.name == "Sniper")
        run, player, bot = self.encounter(definition)
        bot.yaw = -32768
        bot.firing = True
        bot.attack_target = player.entity
        bot.body_script.component.run_frame(run.frame, [(20, True)])
        target = next(t for t in run.match.combat._targets() if t.owner is player)
        vitals = run.match.combat.vitals_of(target)
        for _ in range(200):
            self.fire(run, player, 1, 0)
            if vitals.total < 400:
                break
        self.assertEqual(vitals.total, 300)
        self.assertEqual(vitals.now[combat.HEALTH_POOL], 200)

    def test_personal_barrier_blocks_mounted_sniper_shot_and_gains_energy(self):
        definition = next(d for d in self.catalog.values() if d.name == "Sniper")
        run, player, bot = self.encounter(definition)
        bot.yaw = -32768
        bot.firing = True
        bot.attack_target = player.entity
        bot.body_script.component.run_frame(run.frame, [(20, True)])
        # Explicit reaction timing fixture; no automatic barrier/AI policy.
        self.fire(run, player, 20, 0)
        self.fire(run, player, 1, 8)
        self.fire(run, player, 120, 0)
        target = next(t for t in run.match.combat._targets() if t.owner is player)
        self.assertEqual(run.match.combat.vitals_of(target).total, 400)
        self.assertEqual(player.body_script.component.entity_var(87).value(), 20)

    def test_trooper_sniper_heavy_fire_hits_bubble_edge_before_rear_player(self):
        shot_damage = {"Trooper": 20, "Sniper": 100, "Heavy Assault": 5}
        for definition in self.catalog.values():
            if definition.name not in shot_damage:
                continue
            amount = shot_damage[definition.name]
            for projected, breaking in product((False, True), repeat=2):
                with self.subTest(npc=definition.name, projected=projected, breaking=breaking):
                    run = Run(names=("Front", "Rear", "Projector"))
                    front, rear, projector = run.players
                    match = run.match
                    fight = match.combat
                    match.bots.clear()
                    match._pending_bot_scripts.clear()
                    poses = ((front, (1, 1, 2.5)), (rear, (0, 1, 0)), (projector, (6, 1, 2.5)))
                    for actor, point in poses:
                        actor.client = NpcClient(actor)
                        self.assertTrue(match.switch_hero(actor, heroes()[ZARYA]))
                        actor.team = 0
                        actor.mover.position[:] = point
                        actor.mover.body = None
                    bot = npc.spawn(match, definition, (0, 1, 5), team=1)
                    bot.firing, bot.attack_target = True, rear.entity
                    bot.body_script.component.run_frame(run.frame, [(20, True)])
                    for _ in range(20):
                        run.step({})
                    front_target = next(t for t in fight._targets() if t.owner is front)
                    rear_target = next(t for t in fight._targets() if t.owner is rear)
                    rear_vitals = fight.vitals_of(rear_target)
                    origin = (0, 1 + combat.EYE, 5)
                    direction = combat.aim(*aim(origin, fight._eye(rear)))
                    a, b = front_target.segment()
                    self.assertIsNone(combat.ray_capsule(origin, direction, a, b, front_target.radius))
                    x, y, z = front_target.feet
                    centre = x, y + front_target.height / 2, z
                    edge = combat.ray_capsule(origin, direction, centre, centre, zarya.BUBBLE_RADIUS)
                    self.assertIsNotNone(edge)
                    caster = projector if projected else front
                    angles = aim(fight._eye(caster), fight._eye(front)) if projected else (0, 0)
                    run.step({caster: (*angles, 16 if projected else 8)})
                    self.assertEqual(rear_vitals.total, 400)
                    barriers = fight.zarya_barriers
                    self.assertTrue(barriers.protects(front))
                    pool = (barriers.projected if projected else barriers.active)[caster.body]
                    capacity = amount / 2 if breaking else 200
                    pool.remaining = capacity
                    for _ in range(125):
                        run.step({})
                        if pool.remaining < capacity or rear_vitals.total < 400:
                            break
                    absorbed = min(capacity, amount)
                    self.assertEqual(pool.remaining, capacity - absorbed)
                    self.assertEqual(rear_vitals.total, 400)
                    self.assertAlmostEqual(caster.body_script.component.entity_var(87).value(), absorbed / 5)
                    if breaking:
                        for _ in range(200):
                            run.step({})
                            if rear_vitals.total < 400:
                                break
                        self.assertEqual(rear_vitals.total, 400 - amount)
                    self.assertEqual(fight.vitals_of(front_target).total, 400)

    def test_lethal_beam_leaves_each_manual_corpse_without_respawn(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, player, bot = self.encounter(definition)
                vitals = run.match.combat.vitals[bot.entity]
                # Shorten setup, keeping the actual body, graphs and final authored shot.
                vitals.now = dict.fromkeys(vitals.now, 0.0)
                vitals.now[combat.HEALTH_POOL] = 1.0
                self.fire(run, player, 5)
                self.assertTrue(bot.dead)
                self.assertTrue(bot.retain_corpse)
                self.assertEqual([entity for entity, _ in player.client.deaths], [bot.entity])
                self.assertIn(bot, run.match.bots)
                self.assertFalse(any(who is bot for _, who in run.match.combat.respawns))
                self.fire(run, player, 200, 0)
                self.assertIn(bot, run.match.bots)
                self.assertEqual(vitals.total, 0)

    def test_authored_trooper_and_heavy_volleys_damage_zarya(self):
        expected = {"Trooper": 120, "Heavy Assault": 110}
        for definition in self.catalog.values():
            if definition.name not in expected:
                continue
            with self.subTest(npc=definition.name):
                run, player, bot = self.encounter(definition)
                bot.yaw = -32768
                bot.firing = True
                bot.attack_target = player.entity
                target = next(t for t in run.match.combat._targets() if t.owner is player)
                vitals = run.match.combat.vitals_of(target)
                # Explicit AI input fixture: actual body graph button20, not autonomous AI.
                bot.body_script.component.run_frame(run.frame, [(20, True)])
                self.fire(run, player, 120, 0)
                self.assertEqual(400 - vitals.total, expected[definition.name])
                self.assertEqual(vitals.now[combat.HEALTH_POOL], 200)

    def test_personal_barrier_blocks_actual_trooper_and_heavy_volleys(self):
        for definition in self.catalog.values():
            if definition.name not in {"Trooper", "Heavy Assault"}:
                continue
            with self.subTest(npc=definition.name):
                run, player, bot = self.encounter(definition)
                self.fire(run, player, 1, 8)
                bot.yaw = -32768
                bot.firing = True
                bot.attack_target = player.entity
                bot.body_script.component.run_frame(run.frame, [(20, True)])
                self.fire(run, player, 120, 0)
                target = next(t for t in run.match.combat._targets() if t.owner is player)
                self.assertEqual(run.match.combat.vitals_of(target).total, 400)
                self.assertGreater(player.body_script.component.entity_var(87).value(), 0)

    def test_projected_barrier_blocks_trooper_sniper_heavy_attacks_on_ally(self):
        for definition in self.catalog.values():
            if definition.name not in {"Trooper", "Sniper", "Heavy Assault"}:
                continue
            with self.subTest(npc=definition.name):
                run = Run(names=("Caster", "Ally"))
                caster, ally = run.players
                for player in run.players:
                    player.client = NpcClient(player)
                match = run.match
                match.bots.clear()
                match._pending_bot_scripts.clear()
                self.assertTrue(match.switch_hero(caster, heroes()[ZARYA]))
                ally.team = caster.team
                caster.mover.position[:] = [0, 1, 0]
                ally.mover.position[:] = [0, 1, 5]
                bot = npc.spawn(match, definition, (0, 1, 10), team=1)
                bot.yaw, bot.firing = -32768, True
                bot.attack_target = ally.entity
                bot.body_script.component.run_frame(run.frame, [(20, True)])
                # Explicit reactive timing puts Sniper's first shot within the
                # actual two-second projection; no AI/barrier automation implied.
                self.fire(run, caster, 20, 0)
                self.fire(run, caster, 1, 16)
                target = next(t for t in match.combat._targets() if t.owner is ally)
                vitals = match.combat.vitals_of(target)
                before = vitals.total
                self.fire(run, caster, 120, 0)
                self.assertEqual(vitals.total, before)
                self.assertGreater(caster.body_script.component.entity_var(87).value(), 0)
                energy = next(i for i in caster.body_script.component.instances.values()
                              if i.graph.index == 0x0242)
                self.assertEqual(energy.vars[392].value().id, bot.entity)

    def test_projected_barrier_blocks_actual_flash_damage_and_stun_on_all_five(self):
        for definition, case in product(self.catalog.values(), ("direct", "edge", "airburst", "unprotected")):
            with self.subTest(npc=definition.name, case=case):
                run = Run(names=("Zarya", "McCree"))
                caster, enemy = run.players
                for player in run.players:
                    player.client = NpcClient(player)
                match = run.match
                match.bots.clear()
                match._pending_bot_scripts.clear()
                self.assertTrue(match.switch_hero(caster, heroes()[ZARYA]))
                self.assertTrue(match.switch_hero(enemy, heroes()[0x02E0000000000042]))
                enemy.team = 1
                direct = case == "direct"
                edge = case == "edge"
                caster.mover.position[:] = [0, 1, 0] if direct else [5, 1, 5 if edge else 7]
                enemy.mover.position[:] = [0, 1, 10] if direct else [0, 1, 0]
                position = (0, 1, 5) if direct else (1, 1, 5) if edge else (2, 1, 7)
                bot = npc.spawn(match, definition, position, team=caster.team)
                before = match.combat.vitals[bot.entity].total
                protected = case != "unprotected"
                if protected:
                    run.step({caster: (0 if direct else -16384, 0, 16)})
                    instance = next(i for i in caster.body_script.component.instances.values()
                                    if i.graph.index == 0x025C)
                    self.assertTrue(instance.state(10).active)
                rocket = None
                for frame in range(36):
                    if direct:
                        flash.FlashbangTests.step(self, run, enemy, bot, cast=16 if frame == 0 else 0)
                    else:
                        run.step({enemy: (0, 0, 16 if frame == 0 else 0)})
                    if match.combat.rockets:
                        rocket, = match.combat.rockets
                if edge:
                    target = next(t for t in match.combat._targets() if t.owner is bot)
                    self.assertLess(target.radius, 1)
                    self.assertIsNotNone(rocket)
                    self.assertGreater(rocket.distance, 3)
                    self.assertLess(rocket.distance, 5)
                self.assertEqual(match.combat.vitals[bot.entity].total, before - (0 if protected else 25))
                self.assertEqual(match.combat.is_stunned(bot), not protected)
                if protected:
                    self.assertEqual(caster.body_script.component.entity_var(87).value(), 5)
                    energy = next(i for i in caster.body_script.component.instances.values()
                                  if i.graph.index == 0x0242)
                    self.assertEqual(energy.vars[392].value().id, enemy.body)
                    storage = match.combat.zarya_barriers.projected
                    self.assertEqual(storage[caster.body].recipient, bot.entity)
                    self.assertIn(bot.entity, npc.remove_spawned(match))
                    self.assertNotIn(caster.body, storage)

    @unittest.skipUnless(os.environ.get("ORPHEUS_PROJECTILE_TAGS"), "requires projectile tag input")
    def test_sigma_grasp_protects_all_five_from_secondary_and_ultimate_but_not_beam(self):
        self.assertTrue(interception.eligible(0x04000000000003DB))
        self.assertTrue(interception.eligible(0x04000000000008CE))
        for definition in self.catalog.values():
            for ultimate in (False, True):
                with self.subTest(npc=definition.name, ultimate=ultimate):
                    run = Run(names=("Zarya", "Sigma"))
                    source, sigma = run.players
                    for player in run.players:
                        player.client = NpcClient(player)
                    match = run.match
                    match.bots.clear()
                    match._pending_bot_scripts.clear()
                    self.assertTrue(match.switch_hero(source, heroes()[ZARYA]))
                    self.assertTrue(match.switch_hero(sigma, heroes()[0x02E000000000023B]))
                    source.team, sigma.team = 0, 1
                    source.mover.position[:] = [0, 1, 0]
                    # Beyond Sigma's .7m body + .15m beam radius, but within
                    # the authored Grasp box; this separates body cover from absorption.
                    sigma.mover.position[:] = [1.0, 1, 5]
                    bot = npc.spawn(match, definition, (0, 1, 8), team=1)
                    vitals = match.combat.vitals[bot.entity]
                    before = vitals.total
                    for frame in range(5):
                        run.step({sigma: (-32768, 0, 8 if frame == 0 else 0)})
                    grasp = next(i for i in sigma.body_script.component.instances.values()
                                 if i.graph.index == 0x1D02)
                    self.assertTrue(grasp.state(1).active)
                    if ultimate:
                        source.body_script.set_ult_charge(2100)
                    run.step({source: (0, 0, 0x20 if ultimate else 2), sigma: (-32768, 0, 0)})
                    for _ in range(60):
                        run.step({sigma: (-32768, 0, 0)})
                    self.assertEqual(vitals.total, before)
                    self.assertEqual(grasp.vars[21312].value(), 5 if ultimate else 47)
                    self.assertFalse(match.combat.zarya_gravitons.fields)
                    if not ultimate:
                        for _ in range(51):
                            run.step({source: (0, 0, 1), sigma: (-32768, 0, 0)})
                        self.assertAlmostEqual(before - vitals.total, 60)
                        self.assertEqual(grasp.vars[21312].value(), 47)

    @unittest.skipUnless(os.environ.get("ORPHEUS_PROJECTILE_TAGS"), "requires projectile tag input")
    def test_reflected_secondary_and_graviton_hit_all_five_original_allied_bodies(self):
        for definition in self.catalog.values():
            for ultimate in (False, True):
                with self.subTest(npc=definition.name, ultimate=ultimate):
                    run = Run(names=("Zarya", "Genji"))
                    source, genji = run.players
                    for player in run.players:
                        player.client = NpcClient(player)
                    match = run.match
                    match.bots.clear()
                    match._pending_bot_scripts.clear()
                    match.switch_hero(source, heroes()[ZARYA])
                    match.switch_hero(genji, heroes()[0x02E0000000000029])
                    source.team, genji.team = 0, 1
                    # The NPC lies between the original source and deflector:
                    # original ally exclusion, then the return hits the NPC first.
                    source.mover.position[:] = [0, 1, -3]
                    genji.mover.position[:] = [0, 1, 5]
                    bot = npc.spawn(match, definition, (0, 1, 0), team=0)
                    vitals = match.combat.vitals[bot.entity]
                    before = vitals.total
                    for frame in range(5):
                        run.step({genji: (-32768, 0, 16 if frame == 0 else 0)})
                    if ultimate:
                        source.body_script.set_ult_charge(2100)
                    run.step({source: (0, 0, 32 if ultimate else 2), genji: (-32768, 0, 0)})
                    for _ in range(300 if ultimate else 60):
                        run.step({genji: (-32768, 0, 0)})
                    self.assertAlmostEqual(before - vitals.total, 25 if ultimate else 47, places=4)
                    self.assertIsNone(vitals.killer)
                    self.assertEqual(match.combat.vitals_of(next(
                        t for t in match.combat._targets() if t.owner is genji)).total, 200)
                    self.assertEqual(source.stats.values.get(combat.stats.HERO_DAMAGE, 0), 0)
                    self.assertFalse(match.combat.zarya_gravitons.fields)

    @unittest.skipUnless(os.environ.get("ORPHEUS_PROJECTILE_TAGS"), "requires actual projectile tags")
    def test_reflected_zarya_projectiles_break_npc_projection_without_spill(self):
        for definition, ultimate in product(self.catalog.values(), (False, True)):
            with self.subTest(npc=definition.name, ultimate=ultimate):
                run, fight, caster, genji, bot = self.ana_projection_arena(definition, protected=True)
                self.assertTrue(run.match.switch_hero(genji, heroes()[0x02E0000000000029]))
                genji.mover.position[:] = [0, 1, 10]
                genji.mover.body = None
                caster.mover.position[:] = [0, 1, 2]
                caster.mover.body = None
                caster.body_script.ult_seconds = 0
                charge = caster.body_script.component.entity_var(ULT_CHARGE)
                self.assertTrue(fight.zarya_barriers.protects(bot))
                pool = fight.zarya_barriers.projected[caster.body]
                pool.remaining = 5.12 if ultimate else 20
                energy = caster.body_script.component.entity_var(87)
                vitals = fight.vitals[bot.entity]
                before = vitals.total

                run.step({genji: (-32768, 0, 16)})
                for _ in range(4):
                    run.step({genji: (-32768, 0, 0)})
                if ultimate:
                    caster.body_script.set_ult_charge(2100)
                run.step({caster: (0, 0, 32 if ultimate else 2), genji: (-32768, 0, 0)})
                for _ in range(60):
                    run.step({genji: (-32768, 0, 0)})
                    if pool.remaining == 0:
                        break
                deflect = next(i for i in genji.body_script.component.instances.values()
                               if i.graph.index == 0x03A3)
                self.assertEqual(deflect.vars[6584].value(), 5 if ultimate else 47)
                self.assertEqual(pool.remaining, 0)
                self.assertEqual(vitals.total, before)
                self.assertAlmostEqual(energy.value(), 1.024 if ultimate else 4, places=4)
                self.assertEqual(charge.value(), 0)

                if ultimate:
                    field, = fight.zarya_gravitons.fields
                    self.assertIs(field.shooter, genji)
                    self.assertEqual(field.source_entity, genji.body)
                    self.assertEqual(field.team, genji.team)
                    run.step({})
                    self.assertAlmostEqual(before - vitals.total, 0.08, places=4)
                    while run.frame < field.end_tick:
                        run.step({})
                    self.assertAlmostEqual(before - vitals.total, 19.84, places=4)
                else:
                    self.assertIsNone(bot.impulse_mover)
                    self.assertFalse(genji.client.messages(combat.HIT))
                self.assertFalse(fight.rockets)
                self.assertFalse(fight.zarya_gravitons.fields)

    @unittest.skipUnless(os.environ.get("ORPHEUS_PROJECTILE_TAGS")
                        and os.environ.get("ORPHEUS_GRAPH_SUPPLEMENT")
                        and os.environ.get("ORPHEUS_MOVERS_SUPPLEMENT"), "requires actual mech/tag inputs")
    def test_matrix_protects_all_five_actual_capsules_from_projectiles_but_not_beam(self):
        for definition in self.catalog.values():
            for ultimate in (False, True):
                with self.subTest(npc=definition.name, ultimate=ultimate):
                    run = Run(names=("Zarya", "Mech"))
                    a, mech = run.players
                    for player in run.players:
                        player.client = NpcClient(player)
                    match = run.match
                    match.bots.clear()
                    match._pending_bot_scripts.clear()
                    match.switch_hero(a, heroes()[ZARYA])
                    self.assertTrue(match.switch_hero(mech, replace(
                        heroes()[0x02E000000000007A], body=0x0400000000000407)))
                    a.team, mech.team = 0, 1
                    a.mover.position[:] = [0, 1, 0]
                    mech.mover.position[:] = [1, 1, 5]  # body cover excluded;beam radius+.7 <1m
                    bot = npc.spawn(match, definition, (0, 1, 8), team=1)
                    vitals = match.combat.vitals[bot.entity]
                    before = vitals.total
                    for _ in range(100):
                        run.step({mech: (-32768, 0, 0)})
                    for _ in range(5):
                        run.step({mech: (-32768, 0, 2)})
                    matrix = next(i for i in mech.body_script.component.instances.values()
                                  if i.graph.index == 0x02CC)
                    self.assertTrue(matrix.state(3).active)
                    if ultimate:
                        a.body_script.set_ult_charge(2100)
                    run.step({a: (0, 0, 32 if ultimate else 2), mech: (-32768, 0, 2)})
                    # Finish the secondary's authored recovery before measuring
                    # a complete .8s beam interval; Matrix still has resource.
                    for _ in range(40):
                        run.step({mech: (-32768, 0, 2)})
                    self.assertEqual(vitals.total, before)
                    self.assertFalse(match.combat.rockets)
                    self.assertFalse(match.combat.zarya_gravitons.fields)
                    if not ultimate:
                        for _ in range(51):
                            run.step({a: (0, 0, 1), mech: (-32768, 0, 2)})
                        self.assertTrue(matrix.state(3).active)
                        self.assertAlmostEqual(before - vitals.total, 60, places=4)

    @unittest.skipUnless(os.environ.get("ORPHEUS_PROJECTILE_TAGS")
                        and os.environ.get("ORPHEUS_GRAPH_SUPPLEMENT")
                        and os.environ.get("ORPHEUS_MOVERS_SUPPLEMENT"), "requires actual mech/tag inputs")
    def test_matrix_protects_all_five_allied_npcs_after_genji_reflects_zarya(self):
        self.check_reflected_npc_absorption(matrix=True)

    @unittest.skipUnless(os.environ.get("ORPHEUS_PROJECTILE_TAGS"), "requires actual projectile tags")
    def test_grasp_protects_all_five_allied_npcs_after_genji_reflects_zarya(self):
        self.check_reflected_npc_absorption(matrix=False)

    def check_reflected_npc_absorption(self, *, matrix):
        if matrix:
            hero = replace(heroes()[0x02E000000000007A], body=0x0400000000000407)
            graph_id, state, button = 0x02CC, 3, 2
        else:
            hero = heroes()[0x02E000000000023B]
            graph_id, state, button = 0x1D02, 1, 8
        for definition, ultimate in product(self.catalog.values(), (False, True)):
            with self.subTest(npc=definition.name, ultimate=ultimate):
                run = Run(names=("Zarya", "Genji", "Receiver"))
                source, genji, receiver = run.players
                match = run.match
                fight = match.combat
                match.bots.clear()
                match._pending_bot_scripts.clear()
                self.assertTrue(match.switch_hero(source, heroes()[ZARYA]))
                self.assertTrue(match.switch_hero(genji, heroes()[0x02E0000000000029]))
                self.assertTrue(match.switch_hero(receiver, hero))
                source.team = receiver.team = 0
                genji.team = 1
                receiver_z = 0 if matrix else 2
                for player, point in (
                    (source, (0, 1, -3)), (genji, (0, 1, 5)), (receiver, (1, 1, receiver_z)),
                ):
                    player.client = NpcClient(player)
                    player.mover.position[:] = point
                bot = npc.spawn(match, definition, (0, 1, 0), team=source.team)
                vitals = fight.vitals[bot.entity]
                before = vitals.total
                defense = next(i for i in receiver.body_script.component.instances.values()
                               if i.graph.index == graph_id)
                reflect = next(i for i in genji.body_script.component.instances.values()
                               if i.graph.index == 0x03A3)
                charge = source.body_script.component.entity_var(ULT_CHARGE)
                source.body_script.ult_seconds = 0
                for _ in range(100):
                    run.step({genji: (-32768, 0, 0)})
                defend = {genji: (-32768, 0, 0), receiver: (0, 0, button)}
                run.step({**defend, genji: (-32768, 0, 16)})
                for _ in range(4):
                    run.step(defend)
                self.assertTrue(defense.state(state).active)
                self.assertTrue(reflect.state(10).active)
                if ultimate:
                    source.body_script.set_ult_charge(2100)
                run.step({**defend, source: (0, 0, 32 if ultimate else 2)})
                self.assertTrue(any(r.shooter is source for r in fight.rockets))
                for _ in range(70):
                    run.step(defend)
                    self.assertEqual(vitals.total, before)
                    self.assertFalse(fight.zarya_gravitons.fields)
                amount = 5 if ultimate else 47
                self.assertEqual(reflect.vars[6584].value(), amount)
                self.assertFalse(fight.rockets)
                self.assertIsNone(bot.impulse_mover)
                self.assertNotIn(bot.entity, fight.zarya_gravitons.npc_movers)
                self.assertEqual(charge.value(), 0)
                self.assertFalse(source.client.messages(combat.HIT))
                self.assertFalse(genji.client.messages(combat.HIT))
                if not matrix:
                    self.assertEqual(defense.vars[21312].value(), amount)
                    target = next(t for t in fight._targets() if t.owner is receiver)
                    health = fight.vitals_of(target)
                    for _ in range(120):
                        if interception.SHIELD_TAG in health.temporary_shields:
                            break
                        run.step({})
                    pool = health.temporary_shields[interception.SHIELD_TAG]
                    self.assertAlmostEqual(pool.now, amount * 0.6, places=4)
                    self.assertAlmostEqual(health.total, 400 + amount * 0.6, places=4)
                    self.assertEqual(vitals.total, before)
                    self.assertFalse(fight.zarya_gravitons.fields)

    def test_ultimate_deals_25_and_pulls_each_actual_enemy_body(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, player, bot = self.encounter(definition)
                vitals = run.match.combat.vitals[bot.entity]
                before, position = vitals.total, bot.position
                player.body_script.set_ult_charge(10000)
                self.fire(run, player, 1, 0x20)
                self.fire(run, player, 280, 0)
                self.assertAlmostEqual(before - vitals.total, 25)
                self.assertLess(bot.position[2], position[2])
                self.assertFalse(run.match.combat.zarya_gravitons.fields)
                self.assertFalse(run.match.combat.zarya_gravitons.npc_movers)

    def test_sparse_secondary_does_not_splash_each_npc_before_its_birth(self):
        for definition, birth_offset in product(self.catalog.values(), (5, 20)):
            with self.subTest(npc=definition.name, birth_offset=birth_offset):
                run, caster, direct = self.encounter(definition)
                match, fight = run.match, run.match.combat
                direct_vitals = fight.vitals[direct.entity]
                direct_health = direct_vitals.total
                run.step({caster: (0, 0, 2)})
                run.step({})
                rocket, = fight.rockets
                started = run.frame
                self.assertEqual(rocket.last_tick, started)
                match.tick = started + birth_offset
                late = npc.spawn(match, definition, (1.5, 1, 5), team=direct.team)
                vitals = fight.vitals[late.entity]
                before = vitals.total
                end = started + 80
                match.update(end * combat.TICK, end, frames=False)
                self.assertFalse(fight.rockets)
                self.assertLess(direct_vitals.total, direct_health)
                if birth_offset == 5:
                    self.assertLess(vitals.total, before)
                else:
                    self.assertEqual(vitals.total, before)
                    self.assertEqual(late.velocity, (0, 0, 0))

    def test_overlapping_actual_gravitons_handoff_pull_on_each_npc(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run = Run(names=("First Zarya", "Second Zarya"))
                first, second = run.players
                match = run.match
                fight = match.combat
                match.bots.clear()
                match._pending_bot_scripts.clear()
                match.collision_world = World([
                    (-10, 1, -10, -10, 1, 10, 10, 1, 10), (-10, 1, -10, 10, 1, 10, 10, 1, -10),
                    (-10, 0, 6, 10, 0, 6, 10, 10, 6), (-10, 0, 6, 10, 10, 6, -10, 10, 6),
                ])
                for caster, x in ((first, -2), (second, 2)):
                    caster.client = NpcClient(caster)
                    self.assertTrue(match.switch_hero(caster, heroes()[ZARYA]))
                    caster.team = 0
                    caster.mover.position[:] = [x, 1, 0]
                    caster.mover.body = None
                    caster.body_script.set_ult_charge(2100)
                bot = npc.spawn(match, definition, (0, 1, 4), team=1)
                vitals = fight.vitals[bot.entity]
                before = vitals.total
                run.step({first: (0, 0, 32), second: (0, 0, 32)})
                for _ in range(60):
                    if len(fight.zarya_gravitons.fields) == 2:
                        break
                    run.step({})
                left, right = fight.zarya_gravitons.fields
                self.assertIs(left.shooter, first)
                self.assertIs(right.shooter, second)
                self.assertEqual(vitals.total, before)
                run.step({})
                pulled_x = bot.position[0]
                self.assertLess(pulled_x, 0)
                self.assertAlmostEqual(vitals.total, before - 0.16, places=4)
                mover = fight.zarya_gravitons.npc_movers[bot.entity]
                first.gone = True
                run.step({})
                self.assertGreater(bot.position[0], pulled_x)
                self.assertAlmostEqual(vitals.total, before - 0.24, places=4)
                self.assertEqual(fight.zarya_gravitons.fields, [right])
                self.assertIs(fight.zarya_gravitons.npc_movers[bot.entity], mover)
                while fight.tick < right.end_tick:
                    run.step({})
                self.assertAlmostEqual(vitals.total, before - 20.08, places=4)
                self.assertFalse(fight.zarya_gravitons.fields)
                self.assertFalse(fight.zarya_gravitons.npc_movers)
                self.assertEqual(bot.velocity, (0, 0, 0))

    def test_secondary_and_beam_do_not_spill_through_breaking_npc_projection(self):
        for definition, buttons in product(self.catalog.values(), (1, 2)):
            with self.subTest(npc=definition.name, buttons=buttons):
                run, fight, caster, attacker, bot = self.ana_projection_arena(definition, protected=True)
                self.assertTrue(run.match.switch_hero(attacker, heroes()[ZARYA]))
                attacker.mover.position[:] = [0, 1, 0]
                attacker.mover.body = None
                script = attacker.body_script
                script.ult_seconds = 0
                charge = script.component.entity_var(ULT_CHARGE)
                barriers = fight.zarya_barriers
                self.assertTrue(barriers.protects(bot))
                pool = barriers.projected[caster.body]
                absorbed = 0.6 if buttons == 1 else 20
                pool.remaining = absorbed
                vitals = fight.vitals[bot.entity]
                before = vitals.total
                energy = caster.body_script.component.entity_var(87)

                for frame in range(60):
                    run.step({attacker: (0, 0, buttons if frame == 0 or buttons == 1 else 0)})
                    if pool.remaining == 0:
                        break
                self.assertEqual(pool.remaining, 0)
                self.assertEqual(vitals.total, before)
                self.assertIsNone(bot.impulse_mover)
                self.assertEqual(charge.value(), 0)
                self.assertFalse(attacker.client.messages(combat.HIT))
                self.assertFalse(barriers.protects(bot))

                # Beam absorption arrives after the caster's command in the breaking frame.
                self.fire(run, attacker, 1, 1 if buttons == 1 else 0)
                self.assertAlmostEqual(energy.value(), absorbed / 5, places=5)
                if buttons == 1:
                    damage = 1.2
                else:
                    self.fire(run, attacker, 61, 0)
                    self.fire(run, attacker, 1, 2)
                    self.fire(run, attacker, 40, 0)
                    damage = 47
                self.assertAlmostEqual(vitals.total, before - damage, places=5)
                self.assertAlmostEqual(charge.value(), damage, places=5)
                self.assertEqual(len(attacker.client.messages(combat.HIT)), 1)

    def test_melee_breaking_npc_projection_keeps_recovery_and_blocks_body_damage(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, fight, caster, attacker, bot = self.ana_projection_arena(
                    definition, protected=True, target_z=1.5
                )
                self.assertTrue(run.match.switch_hero(attacker, heroes()[ZARYA]))
                attacker.mover.position[:] = [0, 1, 0]
                attacker.mover.body = None
                script = attacker.body_script
                script.ult_seconds = 0
                charge = script.component.entity_var(ULT_CHARGE)
                melee = next(i for i in script.component.instances.values()
                             if i.graph.index == 0x0043).state(4)
                barriers = fight.zarya_barriers
                self.assertTrue(barriers.protects(bot))
                pool = barriers.projected[caster.body]
                pool.remaining = 20
                vitals = fight.vitals[bot.entity]
                before = vitals.total
                energy = caster.body_script.component.entity_var(87)

                self.fire(run, attacker, 1, 0x800)
                self.assertEqual(melee.counter, 1)
                self.assertEqual(pool.remaining, 0)
                self.assertFalse(barriers.protects(bot))
                self.assertEqual(vitals.total, before)
                self.assertEqual(charge.value(), 0)
                self.assertFalse(attacker.client.messages(combat.HIT))

                self.fire(run, attacker, 1, 0)
                self.assertAlmostEqual(energy.value(), 4)
                for offset in range(2, 62):
                    self.fire(run, attacker, 1, 0x800 if offset % 2 == 0 else 0)
                    self.assertEqual(melee.counter, 1)
                    self.assertEqual(vitals.total, before)
                    self.assertEqual(charge.value(), 0)
                self.assertFalse(attacker.client.messages(combat.HIT))

                self.fire(run, attacker, 1, 0x800)
                self.assertEqual(melee.counter, 2)
                self.assertAlmostEqual(vitals.total, before - 30)
                self.assertAlmostEqual(charge.value(), 30)
                self.assertEqual(len(attacker.client.messages(combat.HIT)), 1)

    def test_sparse_graviton_limits_damage_and_pull_to_each_actual_npc_live_ticks(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, caster, _ = self.encounter(definition)
                match, fight = run.match, run.match.combat
                caster.body_script.set_ult_charge(2100)
                run.step({caster: (*aim(fight._eye(caster), (0, 2, 5)), 32)})
                for _ in range(60):
                    if fight.zarya_gravitons.fields:
                        break
                    run.step({})
                field, = fight.zarya_gravitons.fields
                x, _, z = field.position
                feet = (x + 5, 1, z)
                born = field.last_tick + 80
                match.tick = born
                ally = npc.spawn(match, definition, feet, team=caster.team)
                enemy = npc.spawn(match, definition, feet, team=1 - caster.team)
                health = fight.vitals[enemy.entity].total
                tick = born + 80
                match.update(tick * combat.TICK, tick, frames=False)
                self.assertEqual(fight.vitals[ally.entity].total, health)
                self.assertEqual(ally.position, feet)
                self.assertAlmostEqual(health - fight.vitals[enemy.entity].total, 6.48, places=4)
                pull = feet[0] - enemy.position[0]
                self.assertGreater(pull, 3)
                self.assertLessEqual(pull, 3.889)

                tick = field.end_tick + 80
                match.tick = field.end_tick - 10
                finishing = npc.spawn(match, definition, feet, team=1 - caster.team)
                match.tick = field.end_tick + 2
                late = npc.spawn(match, definition, feet, team=1 - caster.team)
                match.update(tick * combat.TICK, tick, frames=False)
                self.assertAlmostEqual(health - fight.vitals[finishing.entity].total, 0.88, places=4)
                self.assertAlmostEqual(feet[0] - finishing.position[0], 0.48, delta=0.01)
                self.assertEqual(finishing.velocity, (0, 0, 0))
                self.assertEqual(fight.vitals[late.entity].total, health)
                self.assertEqual(late.position, feet)
                self.assertFalse(fight.zarya_gravitons.fields)

    def test_sparse_graviton_matches_paced_pull_and_death_on_each_npc(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                outcomes = []
                for sparse in (False, True):
                    run, caster, bot = self.encounter(definition)
                    match, fight = run.match, run.match.combat
                    caster.body_script.set_ult_charge(2100)
                    run.step({caster: (*aim(fight._eye(caster), (0, 2, 5)), 32)})
                    for _ in range(60):
                        if fight.zarya_gravitons.fields:
                            break
                        run.step({})
                    field, = fight.zarya_gravitons.fields
                    start = field.last_tick
                    end = start + 120
                    x, _, z = field.position
                    bot.position = (x + 4, 1, z)
                    vitals = fight.vitals[bot.entity]
                    vitals.damage(vitals.total - 0.6, armour_scalar=1.0)
                    for tick in (end,) if sparse else range(start + 1, end + 1):
                        match.update(tick * combat.TICK, tick, frames=False)
                    self.assertTrue(bot.dead)
                    outcomes.append((vitals.death_tick - start, bot.position, bot.velocity))

                death_offset, position, velocity = outcomes[0]
                self.assertEqual(death_offset, 8)
                self.assertLess(position[0], x + 3.7)
                self.assertEqual(velocity, (0, 0, 0))
                self.assertEqual(outcomes[1], outcomes[0])

    def test_graviton_pulls_each_npc_before_projected_barrier_breaks(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, fight, caster, attacker, bot = self.ana_projection_arena(definition, protected=True)
                self.assertTrue(run.match.switch_hero(attacker, heroes()[ZARYA]))
                attacker.mover.position[:] = [0, 1, 0]
                attacker.mover.body = None
                self.assertTrue(fight.zarya_barriers.protects(bot))
                pool = fight.zarya_barriers.projected[caster.body]
                pool.remaining = 5.12
                vitals = fight.vitals[bot.entity]
                before, start = vitals.total, bot.position
                attacker.body_script.set_ult_charge(2100)
                x, y, z = bot.position
                angles = aim(fight._eye(attacker), (x, y + 1, z))
                run.step({attacker: (*angles, 32)})
                for _ in range(60):
                    if fight.zarya_gravitons.fields:
                        break
                    run.step({})
                field, = fight.zarya_gravitons.fields
                self.assertAlmostEqual(pool.remaining, 0.12)
                self.assertEqual(vitals.total, before)
                run.step({})
                self.assertAlmostEqual(pool.remaining, 0.04)
                self.assertEqual(vitals.total, before)
                self.assertNotEqual(bot.position, start)
                run.step({})
                self.assertEqual(pool.remaining, 0)
                self.assertEqual(vitals.total, before)
                self.assertAlmostEqual(caster.body_script.component.entity_var(87).value(), 1.024, places=4)
                run.step({})
                self.assertAlmostEqual(vitals.total, before - 0.08, places=4)
                while fight.tick < field.end_tick:
                    run.step({})
                self.assertFalse(fight.zarya_gravitons.fields)
                self.assertFalse(fight.zarya_gravitons.npc_movers)
                self.assertAlmostEqual(vitals.total, before - 19.84, places=4)

    def test_graviton_admits_each_projected_npc_on_body_entry_not_sphere_overlap(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, fight, caster, attacker, bot = self.ana_projection_arena(definition, protected=True)
                self.assertTrue(run.match.switch_hero(attacker, heroes()[ZARYA]))
                attacker.mover.position[:] = [0, 1, 0]
                caster.mover.position[:] = [11, 1, 4.5]
                for actor in (attacker, caster):
                    actor.mover.body = None
                bot.position = (7, 1, 4.5)
                run.match.collision_world = World([
                    (-20, 1, -20, -20, 1, 20, 20, 1, 20), (-20, 1, -20, 20, 1, 20, 20, 1, -20),
                    (-20, 1, 5, 20, 1, 5, 20, 8, 5), (-20, 1, 5, 20, 8, 5, -20, 8, 5),
                ])
                barriers = fight.zarya_barriers
                self.assertTrue(barriers.protects(bot))
                pool = barriers.projected[caster.body]
                pool.remaining = 0.12
                vitals = fight.vitals[bot.entity]
                before = vitals.total
                attacker.body_script.set_ult_charge(2100)
                run.step({attacker: (0, 0, 32)})
                gravitons = fight.zarya_gravitons
                for _ in range(60):
                    if gravitons.fields:
                        break
                    run.step({})
                field, = gravitons.fields
                target = next(t for t in fight._targets() if t.owner is bot)
                a, b = target.segment()
                x, y, z = target.feet
                centre = (x, y + target.height / 2, z)
                body_distance = combat.segment_distance(field.position, a, b) - target.radius
                self.assertGreater(body_distance, field.radius)
                self.assertLess(math.dist(field.position, centre) - zarya.BUBBLE_RADIUS, field.radius)
                for _ in range(10):
                    run.step({})
                self.assertEqual(bot.position, (7, 1, 4.5))
                self.assertEqual(pool.remaining, 0.12)
                self.assertEqual(vitals.total, before)
                self.assertNotIn(bot.entity, gravitons.npc_movers)
                bot.position = (5.9, 1, 4.5)
                run.step({})
                self.assertAlmostEqual(pool.remaining, 0.04)
                self.assertLess(bot.position[0], 5.9)
                self.assertEqual(vitals.total, before)
                run.step({})
                self.assertEqual(pool.remaining, 0)
                self.assertEqual(vitals.total, before)
                self.assertAlmostEqual(caster.body_script.component.entity_var(87).value(), 0.024, places=4)
                run.step({})
                self.assertAlmostEqual(vitals.total, before - 0.08, places=4)

    def test_late_projection_expires_during_graviton_on_each_npc(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, fight, caster, attacker, bot = self.ana_projection_arena(definition, protected=False)
                self.assertTrue(run.match.switch_hero(attacker, heroes()[ZARYA]))
                attacker.mover.position[:] = [0, 1, 0]
                attacker.mover.body = None
                attacker.body_script.set_ult_charge(2100)
                vitals = fight.vitals[bot.entity]
                before = vitals.total
                x, y, z = bot.position
                angles = aim(fight._eye(attacker), (x, y + 1, z))
                run.step({attacker: (*angles, 32)})
                for _ in range(60):
                    if fight.zarya_gravitons.fields:
                        break
                    run.step({})
                field, = fight.zarya_gravitons.fields
                self.assertEqual(vitals.total, before - 5)
                for _ in range(5):
                    run.step({})
                target = next(t for t in fight._targets() if t.owner is bot)
                x, y, z = target.feet
                angles = aim(fight._eye(caster), (x, y + target.eye, z))
                run.step({caster: (*angles, 16)})
                health_at_cast = vitals.total
                self.assertAlmostEqual(health_at_cast, before - 5.48, places=4)
                service = fight.zarya_barriers
                self.assertTrue(service.protects(bot))
                pool = service.projected[caster.body]
                position_at_cast = bot.position
                run.step({})
                self.assertNotEqual(bot.position, position_at_cast)
                self.assertEqual(vitals.total, health_at_cast)
                self.assertAlmostEqual(pool.remaining, 199.92, places=4)
                cast_tick = fight.tick - 1
                while fight.tick < cast_tick + 124:
                    run.step({})
                self.assertEqual(vitals.total, health_at_cast)
                self.assertAlmostEqual(pool.remaining, 190.08, places=4)
                self.assertFalse(service.protects(bot))
                run.step({})
                self.assertAlmostEqual(vitals.total, health_at_cast - 0.08, places=4)
                self.assertAlmostEqual(caster.body_script.component.entity_var(87).value(), 1.984, places=4)
                while fight.tick < field.end_tick:
                    run.step({})
                self.assertAlmostEqual(vitals.total, before - 15.08, places=4)
                self.assertFalse(fight.zarya_gravitons.fields)
                self.assertFalse(fight.zarya_gravitons.npc_movers)

    def test_projected_barrier_blocks_boosters_on_each_actual_npc(self):
        mech_body = 0x0400000000000407
        self.assertIsNotNone(defined_mover_data(mech_body))
        cases = ("exposed", "protected", "break", "allied", "static", "lateral", "edge", "edge_break")
        for definition, case in product(self.catalog.values(), cases):
            with self.subTest(npc=definition.name, case=case):
                run = Run(names=("Zarya", "Mech"))
                caster, mech = run.players
                match = run.match
                fight = match.combat
                match.bots.clear()
                match._pending_bot_scripts.clear()
                self.assertTrue(match.switch_hero(caster, heroes()[ZARYA]))
                self.assertTrue(match.switch_hero(mech, replace(heroes()[dva.HERO], body=mech_body)))
                caster.team, mech.team = 0, 1
                caster.mover.position[:] = (6, 1, 2)
                mech.mover.position[:] = (0, 1, 0)
                caster.mover.body = mech.mover.body = None
                if case == "static":
                    match.collision_world = World([
                        (-10, 1, -10, -10, 1, 20, 20, 1, 20),
                        (-10, 1, -10, 20, 1, 20, 20, 1, -10),
                        (-10, 0, 1.5, 10, 0, 1.5, 10, 8, 1.5),
                        (-10, 0, 1.5, 10, 8, 1.5, -10, 8, 1.5),
                    ])
                # Keep the Heavy Assault's wider capsule fully behind the wall.
                point = (4 if case == "lateral" else 0, 1, 2.75 if case == "static" else 2)
                edge = case in ("edge", "edge_break")
                if edge:
                    point = (2.8, 1, 2)
                bot = npc.spawn(match, definition, point, team=1 if case == "allied" else 0)
                for _ in range(100):
                    run.step({})
                vitals = fight.vitals[bot.entity]
                before = vitals.total
                armoured = vitals.now[combat.ARMOUR_POOL] > 0 and not vitals.now[combat.SHIELDS_POOL]
                point = tuple(bot.position)
                shielded = edge or case in ("protected", "break")
                breaking = case in ("break", "edge_break")
                if shielded:
                    receiver = next(t for t in fight._targets() if t.owner is bot)
                    x, y, z = receiver.feet
                    angles = aim(fight._eye(caster), (x, y + receiver.eye, z))
                    run.step({caster: (*angles, 16)})
                    self.assertTrue(fight.zarya_barriers.protects(bot))
                    barrier = fight.zarya_barriers.projected[caster.body]
                    if breaking:
                        barrier.remaining = 5
                for _ in range(16):
                    run.step({mech: (0, 0, 8)})
                    activation = fight.booster_contacts.get(mech.body)
                    if activation is not None and bot.entity in activation[2]:
                        break
                self.assertIsNotNone(activation)
                self.assertEqual(bot.entity in activation[2], case not in ("allied", "static", "lateral"))
                if edge:
                    track = dva.boosters(mech).state(6)
                    offset = track.evaluate(track.node.field("m_05BE62DD"), watch=False)
                    radius = track.evaluate(track.node.field("m_radius"), watch=False)
                    x, y, z = mech.mover.position
                    centre = (x + offset.x, y + offset.y, z + offset.z)
                    self.assertGreater(combat.segment_distance(centre, *receiver.segment()),
                                       radius + receiver.radius)
                    x, y, z = receiver.feet
                    self.assertLessEqual(math.dist(centre, (x, y + receiver.height / 2, z)),
                                         radius + zarya.BUBBLE_RADIUS)
                if case == "exposed":
                    self.assertEqual(before - vitals.total, 5 if armoured else 10)
                    self.assertEqual(bot.impulse_mover is None, definition.name == "Heavy Assault")
                    if bot.impulse_mover is not None:
                        self.assertGreater(bot.velocity[2], 0)
                else:
                    self.assertEqual(vitals.total, before)
                    self.assertIsNone(bot.impulse_mover)
                    self.assertEqual(tuple(bot.position), point)
                    self.assertFalse(mech.client.messages(combat.HIT))
                if shielded:
                    self.assertEqual(barrier.recipient, bot.entity)
                    self.assertEqual(barrier.remaining, 0 if breaking else 190)
                    run.step({})
                    self.assertAlmostEqual(caster.body_script.component.entity_var(87).value(),
                                           1 if breaking else 2)

    def test_projected_barrier_blocks_accretion_on_each_actual_npc(self):
        for definition, protected in product(self.catalog.values(), (False, True)):
            with self.subTest(npc=definition.name, protected=protected):
                run = Run(names=("Zarya", "Sigma"))
                caster, sigma = run.players
                for player in run.players:
                    player.client = NpcClient(player)
                match = run.match
                fight = match.combat
                match.bots.clear()
                match._pending_bot_scripts.clear()
                self.assertTrue(match.switch_hero(caster, heroes()[ZARYA]))
                self.assertTrue(match.switch_hero(sigma, heroes()[0x02E000000000023B]))
                caster.team, sigma.team = 0, 1
                caster.mover.position[:] = [3, 1, 5]
                sigma.mover.position[:] = [0, 1, 0]
                bot = npc.spawn(match, definition, (0, 1, 5), team=caster.team)
                run.step({caster: (-16384, 0, 16 if protected else 0)})
                before = fight.vitals[bot.entity].total
                angles = aim(fight._eye(sigma), (0, 2, 5))
                launched = False
                for frame in range(95):
                    run.step({sigma: (*angles, 16 if frame == 0 else 0)})
                    if any(rock.accretion_source is not None for rock in fight.rockets):
                        launched = True
                    elif launched:
                        break
                self.assertTrue(launched)
                if protected:
                    barrier = fight.zarya_barriers.projected[caster.body]
                    self.assertEqual((barrier.recipient, barrier.remaining), (bot.entity, 130))
                    self.assertAlmostEqual(caster.body_script.component.entity_var(87).value(), 14)
                    self.assertEqual(fight.vitals[bot.entity].total, before)
                    self.assertFalse(fight.is_stunned(bot))
                    self.assertIsNone(bot.impulse_mover)
                else:
                    self.assertEqual(fight.vitals[bot.entity].total, before - 70)
                    self.assertEqual(fight.stuns[bot.entity] - fight.tick, 50)
                    if definition.name == "Heavy Assault":
                        self.assertIsNone(bot.impulse_mover)
                    else:
                        self.assertIsNotNone(bot.impulse_mover)
                        self.assertGreater(bot.velocity[2], 4)
                        for _ in range(45):
                            run.step({})
                        self.assertGreater(bot.position[2], 6)
                        self.assertIsNone(bot.impulse_mover)

    def test_fire_strike_crosses_each_actual_npc_and_respects_team_and_projection(self):
        for definition, case in product(self.catalog.values(), ("exposed", "protected", "allied")):
            with self.subTest(npc=definition.name, case=case):
                protected, allied = case == "protected", case == "allied"
                run = Run(names=("Zarya", "Reinhardt", "Rear target"))
                caster, rein, rear = run.players
                match = run.match
                fight = match.combat
                match.bots.clear()
                match._pending_bot_scripts.clear()
                for player in run.players:
                    player.client = NpcClient(player)
                self.assertTrue(match.switch_hero(caster, heroes()[ZARYA]))
                self.assertTrue(match.switch_hero(rein, heroes()[0x02E0000000000007]))
                caster.team = rear.team = 0
                rein.team = 1
                for player, point in ((caster, [4, 1, 5]), (rein, [0, 1, 0]), (rear, [0, 1, 9])):
                    player.mover.position[:] = point
                    player.mover.body = None
                bot = npc.spawn(match, definition, (0, 1, 5), team=rein.team if allied else caster.team)
                run.step({caster: (-16384, 0, 16 if protected else 0)})
                body = fight.vitals[bot.entity]
                before = body.total
                expected = 95 if body.now[combat.ARMOUR_POOL] else 100
                angles = aim(fight._eye(rein), (0, 2, 5))
                rocket = None
                for frame in range(65):
                    run.step({rein: (*angles, 16 if frame == 0 else 0)})
                    if rocket is None and fight.rockets:
                        rocket, = fight.rockets
                        self.assertEqual(rocket.definition, 0x04000000000001EA)
                self.assertIsNotNone(rocket)
                contacts = [rear.body] if allied else [bot.entity, rear.body]
                self.assertCountEqual((key[0] for key in rocket.hit_targets), contacts)
                self.assertEqual(fight.vitals[rear.body].total, 100)
                self.assertEqual(body.total, before - (0 if protected or allied else expected))
                if protected:
                    barrier = fight.zarya_barriers.projected[caster.body]
                    self.assertEqual((barrier.recipient, barrier.remaining), (bot.entity, 100))
                    self.assertAlmostEqual(caster.body_script.component.entity_var(87).value(), 20)

    def test_projected_barrier_prevents_and_cleanses_ana_antiheal_on_each_npc(self):
        cases = ("control", "cleanse", "prevent", "queued_ally", "team_change")
        for definition, case in product(self.catalog.values(), cases):
            with self.subTest(npc=definition.name, case=case):
                run = Run(names=("Zarya", "Hostile Ana", "Friendly Ana"))
                caster, enemy, healer = run.players
                match = run.match
                fight = match.combat
                match.bots.clear()
                match._pending_bot_scripts.clear()
                self.assertTrue(match.switch_hero(caster, heroes()[ZARYA]))
                for ana in (enemy, healer):
                    self.assertTrue(match.switch_hero(ana, heroes()[0x02E000000000013B]))
                caster.team = healer.team = 0
                enemy.team = 1
                for player, point in ((caster, [6, 1, 5]), (enemy, [0, 1, 0]), (healer, [-4, 1, 9])):
                    player.client = NpcClient(player)
                    player.mover.position[:] = point
                    player.mover.body = None
                bot = npc.spawn(match, definition, (0, 1, 5), team=caster.team)
                run.step({})
                if case == "prevent":
                    target = next(t for t in fight._targets() if t.owner is bot)
                    x, y, z = target.feet
                    angles = aim(fight._eye(caster), (x, y + target.eye, z))
                    run.step({caster: (*angles, 16)})
                    self.assertTrue(fight.zarya_barriers.protects(bot))
                vitals = fight.vitals[bot.entity]
                before = vitals.total
                self.throw_grenade_at_npc(run, enemy, bot)
                if case == "prevent":
                    self.assertEqual(vitals.total, before)
                    self.assertEqual(vitals.healing_block_until, 0)
                    pool = fight.zarya_barriers.projected[caster.body]
                    self.assertEqual((pool.recipient, pool.remaining), (bot.entity, 150))
                    self.assertAlmostEqual(caster.body_script.component.entity_var(87).value(), 10)
                    continue
                cast = case == "cleanse"
                injured = vitals.total
                self.assertLess(injured, before)
                deadline = vitals.healing_block_until
                self.assertGreater(deadline, fight.tick)
                x, y, z = bot.position
                angles = aim(fight._eye(caster), (x, y + 1, z))
                if case in ("queued_ally", "team_change"):
                    activation = run.frame + 10
                    self.assertGreater(deadline, activation)
                    match.tick = activation
                    yaw, pitch = angles
                    caster.take_commands([Command(activation, yaw=yaw, pitch=pitch, buttons=16)])
                    self.assertIn((caster.body, True), fight.zarya_barriers.expiries)
                    allied = case == "queued_ally"
                    if not allied:
                        bot.team = enemy.team
                    match.update(activation * combat.TICK, activation, frames=False)
                    self.assertEqual(fight.zarya_barriers.protects(bot), allied)
                    self.assertEqual(vitals.healing_block_until, 0 if allied else deadline)
                    continue
                run.step({caster: (*angles, 16 if cast else 0)})
                self.assertEqual(fight.zarya_barriers.protects(bot), cast)
                self.assertEqual(vitals.healing_block_until, 0 if cast else deadline)
                self.assertEqual(vitals.total, injured)
                self.throw_grenade_at_npc(run, healer, bot)
                self.assertLess(fight.tick, deadline)
                self.assertEqual(vitals.total, before if cast else injured)
                self.assertGreater(vitals.healing_boost_until, fight.tick)
                self.assertEqual(caster.body_script.component.entity_var(87).value() or 0, 0)
                if cast:
                    pool = fight.zarya_barriers.projected[caster.body]
                    self.assertEqual((pool.recipient, pool.remaining), (bot.entity, 200))

    def throw_grenade_at_npc(self, run, ana, bot):
        fight = run.match.combat
        x, y, z = bot.position
        angles = aim(fight._eye(ana), (x, y + 0.8, z))
        run.step({ana: (*angles, 16)})
        launched = False
        for _ in range(65):
            launched |= any(rocket.definition == combat.ANA_GRENADE for rocket in fight.rockets)
            run.step({ana: (*angles, 0)})
            if launched and not fight.rockets:
                return
        self.fail("Actual Biotic Grenade did not launch and land")

    def ana_projection_arena(self, definition, *, protected, target_z=5):
        run = Run(names=("Zarya", "Ana"))
        caster, ana = run.players
        match = run.match
        fight = match.combat
        match.bots.clear()
        match._pending_bot_scripts.clear()
        for player, hero, point in (
            (caster, ZARYA, [4, 1, target_z]), (ana, 0x02E000000000013B, [0, 1, 0]),
        ):
            player.client = NpcClient(player)
            self.assertTrue(match.switch_hero(player, heroes()[hero]))
            player.mover.position[:] = point
            player.mover.body = None
        caster.team, ana.team = 0, 1
        bot = npc.spawn(match, definition, (0, 1, target_z), team=caster.team)
        run.step({caster: (-16384, 0, 16 if protected else 0)})
        return run, fight, caster, ana, bot

    def test_npc_projection_wall_rejection_preserves_immediate_recast(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, fight, caster, ana, bot = self.ana_projection_arena(definition, protected=False)
                original = run.match.collision_world
                run.match.collision_world = World([
                    (-10, 1, -10, -10, 1, 20, 20, 1, 20), (-10, 1, -10, 20, 1, 20, 20, 1, -10),
                    (2, 0, 3, 2, 5, 3, 2, 5, 7), (2, 0, 3, 2, 5, 7, 2, 0, 7),
                ])
                target = next(t for t in fight._targets() if t.owner is bot)
                x, y, z = target.feet
                angles = aim(fight._eye(caster), (x, y + target.eye, z))
                run.step({caster: (*angles, 16)})
                projection = next(i for i in caster.body_script.component.instances.values()
                                  if i.graph.index == 0x025C)
                pet = projection.state(10)
                self.assertFalse(pet.active)
                self.assertFalse(fight.zarya_barriers.protects(bot))
                counter = pet.counter
                run.match.collision_world = original
                run.step({})
                run.step({caster: (*angles, 16)})
                self.assertTrue(pet.active)
                self.assertEqual(pet.counter, counter + 1)
                self.assertTrue(fight.zarya_barriers.protects(bot))
                vitals = fight.vitals[bot.entity]
                before = vitals.total
                run.step({ana: (*aim(fight._eye(ana), (x, y + 1, z)), 1)})
                for _ in range(20):
                    run.step({})
                self.assertEqual(vitals.total, before)
                pool = fight.zarya_barriers.projected[caster.body]
                self.assertEqual((pool.recipient, pool.remaining), (bot.entity, 130))
                self.assertAlmostEqual(caster.body_script.component.entity_var(87).value(), 14)

    def test_npc_projection_expiry_boundary_with_actual_scoped_hit(self):
        for definition, expired in product(self.catalog.values(), (False, True)):
            with self.subTest(npc=definition.name, expired=expired):
                run, fight, caster, ana, bot = self.ana_projection_arena(definition, protected=True)
                service = fight.zarya_barriers
                self.assertTrue(service.protects(bot))
                pool = service.projected[caster.body]
                instance = next(i for i in caster.body_script.component.instances.values()
                                if i.graph.index == 0x025C)
                pet = instance.state(10)
                deadline = run.frame + 124
                fire_tick = deadline if expired else deadline - 1
                vitals = fight.vitals[bot.entity]
                before = vitals.total
                x, y, z = bot.position
                angles = aim(fight._eye(ana), (x, y + 1, z))
                while run.frame < fire_tick - 1:
                    run.step({ana: (*angles, 2)})
                self.assertTrue(pet.active)
                run.step({ana: (*angles, 3)})
                self.assertEqual(pet.active, not expired)
                self.assertEqual(bool(fight.rifle_doses), expired)
                self.assertFalse(fight.rockets)
                self.assertEqual(pool.remaining, 200 if expired else 130)
                for _ in range(45):
                    run.step({ana: (*angles, 2)})
                self.assertFalse(service.protects(bot))
                self.assertFalse(fight.rifle_doses)
                self.assertAlmostEqual(vitals.total, before - (70 if expired else 0), places=4)
                energy = caster.body_script.component.entity_var(87).value()
                self.assertAlmostEqual(energy or 0, 0 if expired else 14)

    def test_barriers_expire_on_either_side_of_ana_projectile_contact(self):
        barriers = [(definition, True) for definition in self.catalog.values()]
        barriers.append((next(iter(self.catalog.values())), False))
        for (definition, projected), early, command_ahead in product(barriers, (False, True), (False, True)):
            with self.subTest(npc=definition.name, projected=projected, early=early, command_ahead=command_ahead):
                run, fight, caster, ana, bot = self.ana_projection_arena(
                    definition, protected=projected, target_z=10,
                )
                if not projected:
                    caster.mover.position[:] = (0, 1, 10)
                    caster.mover.body = None
                    bot.position = (10, 1, 10)
                    run.step({caster: (0, 0, 8)})
                recipient = bot if projected else caster
                service = fight.zarya_barriers
                self.assertTrue(service.protects(recipient))
                storage = service.projected if projected else service.active
                pool = storage[caster.body]
                start = run.frame
                deadline = start + 124
                launch = deadline - (20 if early else 1)
                run.match.update(launch * combat.TICK, launch, frames=False)
                self.assertEqual(caster.body_script.frame, start)
                self.assertTrue(service.protects(recipient))
                target = next(target for target in fight._targets() if target.owner is recipient)
                vitals = fight.vitals_of(target)
                before = vitals.total
                x, y, z = target.feet
                yaw, pitch = aim(fight._eye(ana), (x, y + 1, z))
                ana.take_commands([Command(launch, yaw=yaw, pitch=pitch, buttons=1, extra=0)])
                self.assertEqual(len(fight.rockets), 1)
                run.match.update((launch + 1) * combat.TICK, launch + 1, frames=False)
                self.assertEqual(len(fight.rockets), 1)
                if command_ahead:
                    caster.take_commands([Command(deadline + 3)])
                    self.assertEqual(caster.body_script.frame, deadline + 3)
                    expiry = service.expiries[caster.body, projected]
                    self.assertFalse(expiry.duration.active)
                run.frame = deadline + 90
                run.match.update(run.frame * combat.TICK, run.frame, frames=False)
                run.frame += 40
                run.match.update(run.frame * combat.TICK, run.frame, frames=False)
                self.assertFalse(service.protects(recipient))
                self.assertFalse(fight.rockets)
                self.assertFalse(fight.rifle_doses)
                self.assertAlmostEqual(vitals.total, before - (0 if early else 70), places=4)
                self.assertEqual(pool.remaining, 130 if early else 200)
                energy = caster.body_script.component.entity_var(87).value() or 0
                if early:
                    impact = launch + 5
                    instance = next(i for i in caster.body_script.component.instances.values()
                                    if i.graph.index == 0x0242)
                    wait = instance.state(instance.graph.node(7).state)
                    self.assertEqual(wait.world_deadline, impact + 63)
                    elapsed = (run.frame - impact) * combat.TICK - 1
                    self.assertAlmostEqual(energy, 14 - elapsed * 1.8, places=3)
                else:
                    self.assertEqual(energy, 0)

    def test_barrier_cooldown_cannot_be_skipped_with_future_commands(self):
        definition = next(iter(self.catalog.values()))
        for projected, cooldown, button in ((False, 625, 8), (True, 500, 16)):
            with self.subTest(projected=projected):
                run, fight, caster, _, bot = self.ana_projection_arena(definition, protected=projected)
                if not projected:
                    run.step({caster: (0, 0, button)})
                service = fight.zarya_barriers
                expiry = service.expiries[caster.body, projected]
                pet = expiry.duration.instance.state(10 if projected else 2)
                cooldown_var = pet.instance.vars[61]
                generation = pet.counter
                start_tick = fight.tick
                future = expiry.frame + cooldown + 6
                caster.take_commands([
                    Command(expiry.frame + 3), Command(future, yaw=-16384, buttons=button),
                ])
                self.assertEqual(fight.tick, start_tick)
                self.assertFalse(pet.active)
                self.assertEqual(pet.counter, generation)

                self.assertEqual(cooldown_var.value(), 8 if projected else 10)

                ready = expiry.tick + cooldown
                run.match.update((ready - 1) * combat.TICK, ready - 1, frames=False)
                caster.take_commands([Command(future + 1), Command(future + 2, yaw=-16384, buttons=button)])
                self.assertFalse(pet.active)
                self.assertEqual(pet.counter, generation)
                self.assertAlmostEqual(cooldown_var.value(), combat.TICK, places=5)
                caster.take_commands([Command(future + 3)])
                run.match.update(ready * combat.TICK, ready, frames=False)
                self.assertEqual(cooldown_var.value(), 0)
                caster.take_commands([Command(future + 4, yaw=-16384, buttons=button)])
                self.assertTrue(pet.active)
                self.assertEqual(pet.counter, generation + 1)
                self.assertTrue(service.protects(bot if projected else caster))

    def test_projection_cooldown_uses_break_tick_after_a_future_command(self):
        definition = next(iter(self.catalog.values()))
        run, fight, caster, ana, bot = self.ana_projection_arena(definition, protected=True)
        service = fight.zarya_barriers
        self.assertTrue(service.protects(bot))
        pool = service.projected[caster.body]
        pool.remaining = 10
        expiry = service.expiries[caster.body, True]
        pet = expiry.duration.instance.state(10)
        generation = pet.counter
        before = fight.vitals[bot.entity].total
        launch = run.frame + 65
        run.match.update(launch * combat.TICK, launch, frames=False)
        yaw, pitch = aim(fight._eye(ana), (0, 2, 5))
        ana.take_commands([Command(launch, yaw=yaw, pitch=pitch, buttons=1, extra=0)])
        caster.take_commands([Command(expiry.frame + 3)])
        self.assertFalse(pet.active)
        for at in range(launch + 1, expiry.tick):
            run.match.update(at * combat.TICK, at, frames=False)
            if not pool.remaining:
                break
        self.assertEqual(pool.remaining, 0)
        self.assertEqual(fight.vitals[bot.entity].total, before)
        self.assertEqual(caster.body_script.component.entity_var(87).value(), 2)
        ready = fight.tick + 500
        self.assertLess(ready, expiry.tick + 500)

        run.match.update((ready - 1) * combat.TICK, ready - 1, frames=False)
        caster.take_commands([Command(ready - 2, yaw=-16384, buttons=16), Command(ready - 1)])
        self.assertFalse(pet.active)
        self.assertEqual(pet.counter, generation)
        run.match.update(ready * combat.TICK, ready, frames=False)
        caster.take_commands([Command(ready, yaw=-16384, buttons=16)])
        self.assertTrue(pet.active)
        self.assertEqual(pet.counter, generation + 1)
        self.assertTrue(service.protects(bot))

    def test_silent_personal_and_projected_barrier_run_authored_exit_and_cooldown(self):
        definition = next(iter(self.catalog.values()))
        for projected, sparse in product((False, True), repeat=2):
            with self.subTest(projected=projected, sparse=sparse):
                run, fight, caster, _, bot = self.ana_projection_arena(definition, protected=projected)
                recipient = bot if projected else caster
                cast = {caster: (-16384, 0, 16 if projected else 8)}
                if not projected:
                    run.step(cast)
                service = fight.zarya_barriers
                self.assertTrue(service.protects(recipient))
                start = run.frame
                script = caster.body_script
                instance = next(i for i in script.component.instances.values()
                                if i.graph.index == (0x025C if projected else 0x0244))
                pet = instance.state(10 if projected else 2)
                generation = pet.counter
                deadline = start + 124
                ticks = (deadline - 1,) if sparse else range(start + 1, deadline)
                for tick in ticks:
                    run.match.update(tick * combat.TICK, tick, frames=False)
                    self.assertTrue(service.protects(recipient))
                self.assertEqual(script.frame, start)
                run.frame = deadline + 3
                run.match.update(run.frame * combat.TICK, run.frame, frames=False)
                self.assertFalse(pet.active)
                self.assertFalse(service.protects(recipient))
                self.assertNotIn((caster.body, projected), service.expiries)
                cooldown = 8 if projected else 10
                self.assertAlmostEqual(instance.vars[61].value(), cooldown, delta=0.1)
                self.assertEqual(pet.counter, generation)

                run.frame = start + 200
                run.step(cast)
                self.assertEqual(pet.counter, generation)
                self.assertFalse(service.protects(recipient))
                run.step({})
                run.frame = deadline + math.ceil(cooldown / combat.TICK) + 5
                run.step(cast)
                self.assertEqual(pet.counter, generation + 1)
                self.assertTrue(service.protects(recipient))

    def test_silent_barrier_break_starts_energy_and_cooldown_at_actual_impact(self):
        cases = [(definition, True) for definition in self.catalog.values()]
        cases.append((next(iter(self.catalog.values())), False))
        for definition, projected in cases:
            with self.subTest(npc=definition.name, projected=projected):
                run, fight, caster, ana, bot = self.ana_projection_arena(definition, protected=projected)
                recipient = bot if projected else caster
                cast = {caster: (-16384, 0, 16 if projected else 8)}
                if not projected:
                    caster.mover.position[:] = (0, 1, 5)
                    caster.mover.body = None
                    bot.position = (10, 1, 5)
                    run.step(cast)
                self.assertTrue(fight.zarya_barriers.protects(recipient))
                service = fight.zarya_barriers
                pool = (service.projected if projected else service.active)[caster.body]
                pool.remaining = 10
                script = caster.body_script
                instance = next(i for i in script.component.instances.values()
                                if i.graph.index == (0x025C if projected else 0x0244))
                pet = instance.state(10 if projected else 2)
                generation = pet.counter
                start = run.frame
                launch = start + 65
                target = next(t for t in fight._targets() if t.owner is recipient)
                vitals = fight.vitals_of(target)
                before = vitals.total
                yaw, pitch = aim(fight._eye(ana), (0, 2, 5))
                run.match.update(launch * combat.TICK, launch, frames=False)
                ana.take_commands([Command(launch, yaw=yaw, pitch=pitch, buttons=1, extra=0)])
                self.assertEqual(len(fight.rockets), 1)
                for hit in range(launch + 1, launch + 15):
                    run.match.update(hit * combat.TICK, hit, frames=False)
                    if not fight.rockets:
                        break
                self.assertEqual(pool.remaining, 0)
                self.assertEqual(vitals.total, before)
                self.assertFalse(service.protects(recipient))
                self.assertEqual(script.frame, start)
                run.match.update((hit + 1) * combat.TICK, hit + 1, frames=False)
                self.assertFalse(pet.active)
                self.assertEqual(script.frame, hit)
                cooldown = 8 if projected else 10
                self.assertAlmostEqual(instance.vars[61].value(), cooldown, delta=0.025)
                energy = script.component.entity_var(87)
                self.assertEqual(energy.value(), 2)
                run.match.update((hit + 1) * combat.TICK, hit + 1, frames=False)
                self.assertEqual(energy.value(), 2)
                self.assertNotIn((caster.body, projected), service.expiries)

                run.frame = hit + 50
                run.step({})
                self.assertEqual(energy.value(), 2)
                run.frame = hit + math.ceil(cooldown / combat.TICK) - 3
                run.step(cast)
                self.assertFalse(pet.active)
                self.assertEqual(pet.counter, generation)
                self.assertEqual(energy.value(), 0)
                run.step({})
                run.step({})
                run.step(cast)
                self.assertEqual(pet.counter, generation + 1)
                self.assertTrue(service.protects(recipient))

    def test_silent_dual_barriers_keep_independent_break_and_expiry_clocks(self):
        for definition, projected_break in product(self.catalog.values(), (False, True)):
            with self.subTest(npc=definition.name, projected_break=projected_break):
                run, fight, caster, projected_attacker, bot = self.ana_projection_arena(
                    definition, protected=True,
                )
                match = run.match
                personal_attacker = match.add_player(
                    3, "Second Ana", projected_attacker.hero.guid, projected_attacker.team, False,
                )
                in_world(match, personal_attacker)
                personal_attacker.client = NpcClient(personal_attacker)
                run.players.append(personal_attacker)
                self.assertTrue(match.switch_hero(personal_attacker, projected_attacker.hero))
                personal_attacker.team = projected_attacker.team
                personal_attacker.mover.position[:] = (4, 1, 0)
                personal_attacker.mover.body = None
                projection_start = run.frame
                for _ in range(15):
                    run.step({})
                run.step({caster: (0, 0, 8)})
                personal_start = run.frame
                service = fight.zarya_barriers
                self.assertTrue(service.protects(caster))
                self.assertTrue(service.protects(bot))
                pools = {False: service.active[caster.body], True: service.projected[caster.body]}
                pools[projected_break].remaining = 10
                script = caster.body_script
                instances = {i.graph.index == 0x025C: i for i in script.component.instances.values()
                             if i.graph.index in (0x0244, 0x025C)}
                targets = {False: caster, True: bot}
                before = {t.owner: fight.vitals_of(t).total
                          for t in fight._targets() if t.owner in (caster, bot)}
                attackers = {False: personal_attacker, True: projected_attacker}
                energy = script.component.entity_var(87)
                energy_graph = next(i for i in script.component.instances.values() if i.graph.index == 0x0242)
                survivor = not projected_break
                survivor_expiry = service.expiries[caster.body, survivor]
                for projected, offset in ((projected_break, 58), (survivor, 96)):
                    attacker = attackers[projected]
                    launch = projection_start + offset
                    point = (0 if projected else 4, 2, 5)
                    yaw, pitch = aim(fight._eye(attacker), point)
                    match.update(launch * combat.TICK, launch, frames=False)
                    attacker.take_commands([Command(launch, yaw=yaw, pitch=pitch, buttons=1, extra=0)])
                    self.assertEqual(len(fight.rockets), 1)
                    for hit in range(launch + 1, launch + 15):
                        match.update(hit * combat.TICK, hit, frames=False)
                        if not fight.rockets:
                            break
                    self.assertFalse(fight.rockets)
                    if projected == projected_break:
                        break_frame = hit
                        self.assertEqual(script.frame, personal_start)
                        match.update((hit + 1) * combat.TICK, hit + 1, frames=False)
                        self.assertEqual(script.frame, hit)
                        self.assertEqual(energy.value(), 2)
                        self.assertEqual(energy_graph.vars[392].value().id, attacker.body)
                        self.assertFalse(service.protects(targets[projected]))
                        self.assertNotIn((caster.body, projected), service.expiries)
                        self.assertIs(service.expiries[caster.body, survivor], survivor_expiry)
                        self.assertEqual(pools[survivor].remaining, 200)

                self.assertEqual(pools[survivor].remaining, 130)
                self.assertEqual(pools[projected_break].remaining, 0)
                self.assertEqual(script.frame, break_frame)
                end = survivor_expiry.tick + 40
                match.update(end * combat.TICK, end, frames=False)
                self.assertFalse(service.expiries)
                self.assertFalse(service.protects(caster))
                self.assertFalse(service.protects(bot))
                self.assertEqual(energy.value(), 16)
                self.assertEqual(energy_graph.vars[392].value().id, attackers[survivor].body)
                for target in fight._targets():
                    if target.owner in before:
                        self.assertEqual(fight.vitals_of(target).total, before[target.owner])
                broken_cooldown = (8 if projected_break else 10) - (script.frame - break_frame) * combat.TICK
                self.assertAlmostEqual(instances[projected_break].vars[61].value(),
                                       broken_cooldown, delta=0.04)
                self.assertAlmostEqual(instances[survivor].vars[61].value(),
                                       8 if survivor else 10, delta=0.04)

    def test_npc_projection_recast_on_first_cooldown_free_frame(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, fight, caster, ana, bot = self.ana_projection_arena(definition, protected=True)
                instance = next(i for i in caster.body_script.component.instances.values()
                                if i.graph.index == 0x025C)
                pet = instance.state(10)
                start, counter = run.frame, pet.counter
                while run.frame < start + 124:
                    run.step({})
                self.assertFalse(pet.active)
                self.assertEqual(instance.vars[61].value(), 8)
                while run.frame < start + 621:
                    run.step({})
                run.step({caster: (-16384, 0, 16)})
                self.assertEqual(pet.counter, counter)
                self.assertFalse(pet.active)
                self.assertFalse(fight.zarya_barriers.protects(bot))
                run.step({})
                self.assertAlmostEqual(instance.vars[61].value(), 0.016, places=5)
                run.step({caster: (-16384, 0, 16)})
                self.assertEqual(run.frame, start + 624)
                self.assertEqual(pet.counter, counter + 1)
                self.assertTrue(fight.zarya_barriers.protects(bot))
                pool = fight.zarya_barriers.projected[caster.body]
                self.assertEqual((pool.recipient, pool.remaining), (bot.entity, 200))
                vitals = fight.vitals[bot.entity]
                before = vitals.total
                x, y, z = bot.position
                angles = aim(fight._eye(ana), (x, y + 1, z))
                run.step({ana: (*angles, 1)})
                for _ in range(20):
                    run.step({})
                self.assertEqual(vitals.total, before)
                self.assertEqual(pool.remaining, 130)
                self.assertAlmostEqual(caster.body_script.component.entity_var(87).value(), 14)

    def test_ana_rifle_dose_or_projection_for_each_actual_npc(self):
        for definition, scoped, protected in product(self.catalog.values(), (False, True), (False, True)):
            with self.subTest(npc=definition.name, scoped=scoped, protected=protected):
                run, fight, caster, ana, bot = self.ana_projection_arena(definition, protected=protected)
                self.assertEqual(fight.zarya_barriers.protects(bot), protected)
                vitals = fight.vitals[bot.entity]
                before = vitals.total
                x, y, z = bot.position
                angles = aim(fight._eye(ana), (x, y + 1, z))
                if scoped:
                    for _ in range(35):
                        run.step({ana: (*angles, 2)})
                run.step({ana: (*angles, 3 if scoped else 1)})
                self.assertEqual(bool(fight.rockets), not scoped)
                saw_dose = bool(fight.rifle_doses)
                for _ in range(55):
                    run.step({ana: (*angles, 2 if scoped else 0)})
                    saw_dose |= bool(fight.rifle_doses)
                self.assertFalse(fight.rockets)
                self.assertFalse(fight.rifle_doses)
                self.assertEqual(saw_dose, not protected)
                if protected:
                    self.assertEqual(vitals.total, before)
                    pool = fight.zarya_barriers.projected[caster.body]
                    self.assertEqual((pool.recipient, pool.remaining), (bot.entity, 130))
                    self.assertAlmostEqual(caster.body_script.component.entity_var(87).value(), 14)
                    self.assertFalse(ana.client.messages(combat.HIT))
                else:
                    self.assertAlmostEqual(vitals.total, before - 70, places=4)

    def test_ana_healing_dose_obeys_npc_antiheal_before_projection_or_skipped_expiry(self):
        cases = (("cleanse", False), ("cleanse", True), ("late_cleanse", False),
                 ("skipped", False), ("crossed", False))
        for definition, (case, scoped) in product(self.catalog.values(), cases):
            with self.subTest(npc=definition.name, case=case, scoped=scoped):
                run, fight, caster, enemy, bot = self.ana_projection_arena(definition, protected=False)
                match = run.match
                healer = match.add_player(3, "Friendly Ana", enemy.hero.guid, caster.team, False)
                in_world(match, healer)
                healer.client = NpcClient(healer)
                run.players.append(healer)
                self.assertTrue(match.switch_hero(healer, enemy.hero))
                healer.team = caster.team
                healer.mover.position[:] = (-4, 1, 9)
                healer.mover.body = None
                vitals = fight.vitals[bot.entity]
                vitals.damage(90)
                self.throw_grenade_at_npc(run, enemy, bot)
                deadline = vitals.healing_block_until
                self.assertGreater(deadline, fight.tick)
                before = vitals.total
                self.throw_grenade_at_npc(run, healer, bot)
                self.assertEqual(vitals.total, before)
                boost = vitals.healing_boost_until
                self.assertGreater(boost, deadline)
                for _ in range(32):
                    run.step({})
                if case == "crossed":
                    run.frame = deadline - 12
                    match.update(run.frame * combat.TICK, run.frame, frames=False)

                x, y, z = bot.position
                angles = aim(fight._eye(healer), (x, y + 1, z))
                if scoped:
                    for _ in range(35):
                        run.step({healer: (*angles, 2)})
                run.step({healer: (*angles, 3 if scoped else 1)})
                for _ in range(20):
                    if fight.rifle_doses:
                        break
                    run.step({healer: (*angles, 2 if scoped else 0)})
                dose, = fight.rifle_doses
                self.assertEqual((dose.target, dose.healing, dose.amount), (bot.entity, True, 70))
                if case == "crossed":
                    self.assertLess(dose.start_tick, deadline)
                    self.assertLess(boost, dose.start_tick + 38)
                    blocked = round(70 * (deadline - 1 - dose.start_tick) * combat.TICK / 0.6 * 1024) / 1024
                    boosted_end = round(70 * (boost - 1 - dose.start_tick) * combat.TICK / 0.6 * 1024) / 1024
                    self.assertGreater(blocked, 0)
                    self.assertLess(blocked, boosted_end)
                    self.assertLess(boosted_end, 70)
                    expected = before + (boosted_end - blocked) * 1.5 + 70 - boosted_end
                    run.frame = boost + combat.COMBAT_CATCH_UP + 50
                    for _ in range(2):
                        match.update(run.frame * combat.TICK, run.frame, frames=False)
                        self.assertFalse(fight.rifle_doses)
                        self.assertAlmostEqual(vitals.total, expected, places=4)
                    continue
                self.assertLess(dose.start_tick + 38, deadline)
                for _ in range(8):
                    run.step({})
                self.assertEqual(vitals.total, before)
                if case == "late_cleanse":
                    activation = dose.start_tick + 20
                    self.assertLess(fight.tick, activation)
                    match.tick = activation
                    caster.take_commands([Command(activation + 500, yaw=-16384, buttons=16)])
                    self.assertTrue(fight.zarya_barriers.protects(bot))
                    blocked = round(70 * 19 * combat.TICK / 0.6 * 1024) / 1024
                    tick = dose.start_tick + 38 + combat.COMBAT_CATCH_UP + 2
                    for _ in range(2):
                        match.update(tick * combat.TICK, tick, frames=False)
                        self.assertFalse(fight.rifle_doses)
                        self.assertAlmostEqual(vitals.total - before, (70 - blocked) * 1.5, places=4)
                    self.assertEqual(vitals.healing_block_until, 0)
                    self.assertEqual(fight.zarya_barriers.projected[caster.body].remaining, 200)
                    self.assertEqual(caster.body_script.component.entity_var(87).value() or 0, 0)
                    continue
                if case == "skipped":
                    run.frame = boost + combat.COMBAT_CATCH_UP + 1
                    match.update(run.frame * combat.TICK, run.frame, frames=False)
                    self.assertFalse(fight.rifle_doses)
                    self.assertEqual(vitals.total, before)
                    continue

                run.step({caster: (-16384, 0, 16)})
                self.assertEqual(vitals.healing_block_until, 0)
                self.assertEqual(vitals.healing_boost_until, boost)
                self.assertEqual(vitals.total, before)
                self.assertGreater(dose.applied, 0)
                remaining = 70 - dose.applied
                self.assertTrue(fight.zarya_barriers.protects(bot))
                pool = fight.zarya_barriers.projected[caster.body]
                for _ in range(40):
                    run.step({})
                self.assertFalse(fight.rifle_doses)
                self.assertAlmostEqual(vitals.total - before, remaining * 1.5, places=4)
                self.assertEqual(pool.remaining, 200)
                self.assertEqual(caster.body_script.component.entity_var(87).value() or 0, 0)

    def test_energy_earned_from_npc_projection_scales_outgoing_weapons(self):
        for definition, buttons in product(self.catalog.values(), (1, 2)):
            with self.subTest(npc=definition.name, buttons=buttons):
                run, fight, caster, ana, ally = self.ana_projection_arena(definition, protected=True)
                enemy = npc.spawn(run.match, definition, (4, 1, 10), team=ana.team)
                ally_vitals = fight.vitals[ally.entity]
                enemy_vitals = fight.vitals[enemy.entity]
                ally_before, enemy_before = ally_vitals.total, enemy_vitals.total
                script = caster.body_script
                script.ult_seconds = 0
                charge = script.component.entity_var(ULT_CHARGE)
                energy = script.component.entity_var(87)
                self.assertTrue(fight.zarya_barriers.protects(ally))
                pool = fight.zarya_barriers.projected[caster.body]
                x, y, z = ally.position
                angles = aim(fight._eye(ana), (x, y + 1, z))
                run.step({ana: (*angles, 1)})
                for _ in range(45):
                    run.step({})
                    if pool.remaining < 200:
                        break
                self.assertEqual(pool.remaining, 130)
                self.assertEqual(energy.value(), 14)
                self.assertEqual(ally_vitals.total, ally_before)
                self.assertEqual(enemy_vitals.total, enemy_before)
                self.assertEqual(charge.value(), 0)

                if buttons == 1:
                    self.fire(run, caster, 31, 1)
                    damage = 42.384
                else:
                    self.fire(run, caster, 1, 2)
                    self.fire(run, caster, 40, 0)
                    damage = 53.72
                self.assertAlmostEqual(enemy_before - enemy_vitals.total, damage, places=4)
                self.assertAlmostEqual(charge.value(), damage, places=4)
                self.assertEqual(ally_vitals.total, ally_before)
                self.assertEqual(pool.remaining, 130)

    def test_projection_absorbs_remaining_ana_rifle_dose_on_each_npc(self):
        for definition, scoped in product(self.catalog.values(), (False, True)):
            with self.subTest(npc=definition.name, scoped=scoped):
                run, fight, caster, ana, bot = self.ana_projection_arena(definition, protected=False)
                vitals = fight.vitals[bot.entity]
                before = vitals.total
                x, y, z = bot.position
                angles = aim(fight._eye(ana), (x, y + 1, z))
                if scoped:
                    for _ in range(35):
                        run.step({ana: (*angles, 2)})
                run.step({ana: (*angles, 3 if scoped else 1)})
                for _ in range(15):
                    if vitals.total < before:
                        break
                    run.step({ana: (*angles, 2 if scoped else 0)})
                dose, = fight.rifle_doses
                self.assertEqual(dose.target, bot.entity)
                self.assertLess(vitals.total, before)

                run.step({caster: (-16384, 0, 16)})
                health_at_cast = vitals.total
                self.assertAlmostEqual(before - health_at_cast, dose.applied, places=4)
                self.assertGreater(dose.applied, 0)
                self.assertLess(dose.applied, 70)
                remaining = 70 - dose.applied
                self.assertTrue(fight.zarya_barriers.protects(bot))
                for _ in range(40):
                    run.step({})
                self.assertFalse(fight.rifle_doses)
                self.assertEqual(vitals.total, health_at_cast)
                pool = fight.zarya_barriers.projected[caster.body]
                self.assertEqual(pool.recipient, bot.entity)
                self.assertAlmostEqual(pool.remaining, 200 - remaining, places=4)
                energy = caster.body_script.component.entity_var(87).value()
                self.assertAlmostEqual(energy, remaining * 0.2, places=4)

    def test_rifle_dose_rechecks_projected_npc_team_during_reassignment(self):
        for definition, rejoins_caster in product(self.catalog.values(), (False, True)):
            with self.subTest(npc=definition.name, rejoins_caster=rejoins_caster):
                run, fight, caster, ana, bot = self.ana_projection_arena(definition, protected=True)
                service = fight.zarya_barriers
                self.assertTrue(service.protects(bot))
                pool = service.projected[caster.body]
                vitals = fight.vitals[bot.entity]
                before = vitals.total
                x, y, z = bot.position
                angles = aim(fight._eye(ana), (x, y + 1, z))
                run.step({ana: (*angles, 1)})
                shot, = fight.rockets
                self.assertEqual(shot.team, ana.team)
                caster.team = ana.team
                self.assertFalse(service.protects(bot))
                for _ in range(15):
                    run.step({})
                    if vitals.total < before:
                        break
                dose, = fight.rifle_doses
                self.assertFalse(dose.healing)
                self.assertGreater(dose.applied, 0)
                self.assertLess(dose.applied, 70)
                self.assertEqual(pool.remaining, 200)
                at_switch = vitals.total
                self.assertAlmostEqual(before - at_switch, dose.applied, places=4)
                remaining = 70 - dose.applied
                if rejoins_caster:
                    caster.team = bot.team
                    ana.team = bot.team
                else:
                    bot.team = ana.team
                self.assertTrue(service.protects(bot))
                for _ in range(40):
                    run.step({})
                self.assertFalse(fight.rifle_doses)
                self.assertIs(service.projected[caster.body], pool)
                self.assertEqual(vitals.total, at_switch)
                self.assertAlmostEqual(pool.remaining, 200 - remaining if rejoins_caster else 200, places=4)
                energy = caster.body_script.component.entity_var(87).value()
                self.assertAlmostEqual(energy or 0.0, remaining / 5 if rejoins_caster else 0, places=4)

    def test_npc_death_retires_projection_after_team_change(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, fight, caster, ana, bot = self.ana_projection_arena(definition, protected=True)
                service = fight.zarya_barriers
                self.assertTrue(service.protects(bot))
                pool = service.projected[caster.body]
                bot.team, ana.team = 1, 0
                self.assertFalse(service.protects(bot))
                self.assertIs(service.projected[caster.body], pool)
                vitals = fight.vitals[bot.entity]
                vitals.now = dict.fromkeys(vitals.now, 0.0)
                vitals.now[combat.HEALTH_POOL] = 1.0
                x, y, z = bot.position
                angles = aim(fight._eye(ana), (x, y + 1, z))
                run.step({ana: (*angles, 1)})
                for _ in range(45):
                    if bot.dead:
                        break
                    run.step({})
                self.assertTrue(bot.dead)
                self.assertTrue(bot.retain_corpse)
                self.assertEqual(vitals.total, 0)
                self.assertEqual([entity for entity, _ in ana.client.deaths], [bot.entity])
                self.assertNotIn(caster.body, service.projected)
                self.assertEqual(pool.remaining, 200)
                self.assertEqual(caster.body_script.component.entity_var(87).value() or 0, 0)
                self.assertFalse(fight.rifle_doses)
                replacement = npc.spawn(run.match, definition, (x, y, z), team=caster.team)
                self.assertNotEqual(replacement.entity, bot.entity)
                self.assertFalse(service.protects(replacement))

    def test_removing_projected_npc_does_not_protect_replacement(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, fight, caster, ana, bot = self.ana_projection_arena(definition, protected=True)
                service = fight.zarya_barriers
                self.assertTrue(service.protects(bot))
                pool = service.projected[caster.body]
                old_entity, position = bot.entity, bot.position
                self.assertIn(old_entity, npc.remove_spawned(run.match))
                self.assertNotIn(caster.body, service.projected)
                replacement = npc.spawn(run.match, definition, position, team=caster.team)
                self.assertNotEqual(replacement.entity, old_entity)
                run.step({})
                run.step({caster: (-16384, 0, 16)})
                self.assertFalse(service.protects(replacement))
                self.assertNotIn(caster.body, service.projected)
                vitals = fight.vitals[replacement.entity]
                before = vitals.total
                x, y, z = replacement.position
                angles = aim(fight._eye(ana), (x, y + 1, z))
                run.step({ana: (*angles, 1)})
                for _ in range(55):
                    run.step({})
                self.assertFalse(fight.rifle_doses)
                self.assertAlmostEqual(vitals.total, before - 70, places=4)
                self.assertEqual(pool.remaining, 200)
                self.assertEqual(caster.body_script.component.entity_var(87).value() or 0, 0)

    def test_projected_barrier_absorbs_sleep_dart_on_each_actual_npc(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, fight, caster, ana, bot = self.ana_projection_arena(definition, protected=True)
                self.assertTrue(fight.zarya_barriers.protects(bot))
                before = fight.vitals[bot.entity].total
                x, y, z = bot.position
                angles = aim(fight._eye(ana), (x, y + 1, z))
                launched = False
                for frame in range(60):
                    run.step({ana: (*angles, 8 if frame == 0 else 0)})
                    launched |= bool(fight.rockets)
                    self.assertNotIn(bot.entity, fight.sleeps)
                    if launched and not fight.rockets:
                        break
                self.assertTrue(launched)
                self.assertFalse(fight.rockets)
                self.assertFalse(fight.is_stunned(bot))
                self.assertEqual(fight.vitals[bot.entity].total, before)
                pool = fight.zarya_barriers.projected[caster.body]
                self.assertEqual((pool.recipient, pool.remaining), (bot.entity, 195))
                self.assertAlmostEqual(caster.body_script.component.entity_var(87).value(), 1)
                self.assertFalse(ana.client.messages(combat.HIT))

    def test_sleep_dart_controls_each_actual_npc_and_removal_clears_deadline(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, player, bot = self.encounter(definition)
                self.assertTrue(run.match.switch_hero(player, heroes()[0x02E000000000013B]))
                player.mover.position[:] = [0, 1, 0]
                fight = run.match.combat
                before = fight.vitals[bot.entity].total
                x, y, z = bot.position
                angles = aim(fight._eye(player), (x, y + 1, z))
                run.step({player: (*angles, 8)})
                for _ in range(60):
                    run.step({player: (*angles, 0)})
                    if bot.entity in fight.sleeps:
                        break
                self.assertTrue(fight.is_stunned(bot))
                self.assertGreater(before - fight.vitals[bot.entity].total, 0)
                self.assertLessEqual(before - fight.vitals[bot.entity].total, 5)
                bot.firing = True
                bot.attack_target = player.entity
                run.step({})
                self.assertFalse(bot.attack_input)
                self.assertIn(bot.entity, npc.remove_spawned(run.match))
                self.assertNotIn(bot.entity, fight.sleeps)

    def test_sleep_dart_defers_all_five_npc_attacks_against_zarya_until_wake(self):
        first_hit = {"Trooper": 20, "Enforcer": 50, "Sniper": 100, "Assassin": 25, "Heavy Assault": 5}
        for definition, protected in product(self.catalog.values(), (False, True)):
            with self.subTest(npc=definition.name, protected=protected):
                run, zarya, bot = self.encounter(definition, z=2.5)
                match = run.match
                fight = match.combat
                ana_guid = 0x02E000000000013B
                ana = match.add_player(2, "Ana", ana_guid, zarya.team, False)
                in_world(match, ana)
                ana.client = NpcClient(ana)
                run.players.append(ana)
                self.assertTrue(match.switch_hero(ana, heroes()[ana_guid]))
                ana.mover.position[:] = [3, 1, 2.5]
                ana.mover.body = None
                x, y, z = bot.position
                angles = aim(fight._eye(ana), (x, y + 1, z))
                run.step({ana: (*angles, 8)})
                for _ in range(60):
                    if fight.is_stunned(bot):
                        break
                    run.step({ana: (*angles, 0)})
                self.assertTrue(fight.is_stunned(bot))
                deadline = fight.sleeps[bot.entity]
                selection = aim(fight._eye(zarya), (x, y + 1, z))
                run.step({zarya: (*selection, 0)})
                self.assertEqual(npc_control.start(match, zarya), bot.entity)
                target = next(t for t in fight._targets() if t.owner is zarya)
                health = fight.vitals_of(target)
                before = health.total
                barriers = fight.zarya_barriers
                # The mounted Sniper has a longer windup, so react after waking.
                cast_tick = deadline + 20 if definition.name == "Sniper" else deadline - 1
                cast = {zarya: (0, 0, 8)} if protected else {}
                while run.frame < deadline - 1:
                    run.step(cast if run.frame + 1 == cast_tick else {})
                    self.assertFalse(bot.attack_input)
                    self.assertEqual(health.total, before)
                    self.assertNotIn(bot.entity, fight.pins)
                self.assertTrue(fight.is_stunned(bot))

                if protected and run.frame == cast_tick:
                    self.assertTrue(barriers.protects(zarya))
                run.step({})
                self.assertFalse(fight.is_stunned(bot))
                self.assertTrue(bot.attack_input)
                for _ in range(160):
                    pool = barriers.active.get(zarya.body)
                    if health.total < before or (pool is not None and pool.remaining < 200):
                        break
                    run.step(cast if run.frame + 1 == cast_tick else {})
                damage = first_hit[definition.name]
                self.assertEqual(health.total, before - (0 if protected else damage))
                self.assertEqual(health.now[combat.HEALTH_POOL], 200)
                if protected:
                    pool = barriers.active.get(zarya.body)
                    self.assertIsNotNone(pool)
                    self.assertEqual(pool.remaining, 200 - damage)
                    component = zarya.body_script.component
                    self.assertAlmostEqual(component.entity_var(87).value(), damage / 5, places=5)
                    energy_graph = next(i for i in component.instances.values() if i.graph.index == 0x0242)
                    self.assertEqual(energy_graph.vars[392].value().id, bot.entity)
                    self.assertNotIn(bot.entity, fight.pins)
                elif definition.name == "Assassin":
                    self.assertEqual(fight.pins.get(bot.entity), zarya.body)
                npc_control.stop(match)

    def test_graviton_breaks_projection_before_waking_each_sleeping_npc(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, source, bot = self.encounter(definition, z=2.5)
                match = run.match
                fight = match.combat
                ana_guid = 0x02E000000000013B
                ana = match.add_player(2, "Ana", ana_guid, source.team, False)
                projector = match.add_player(3, "Projector", ZARYA, bot.team, False)
                for player, hero, position in ((ana, ana_guid, [3, 1, 2.5]), (projector, ZARYA, [4, 1, 2.5])):
                    in_world(match, player)
                    player.client = NpcClient(player)
                    run.players.append(player)
                    self.assertTrue(match.switch_hero(player, heroes()[hero]))
                    player.mover.position[:] = position
                    player.mover.body = None
                x, y, z = bot.position
                angles = aim(fight._eye(ana), (x, y + 1, z))
                run.step({ana: (*angles, 8)})
                for _ in range(60):
                    if fight.is_stunned(bot):
                        break
                    run.step({ana: (*angles, 0)})
                self.assertTrue(fight.is_stunned(bot))
                deadline = fight.sleeps[bot.entity]
                vitals = fight.vitals[bot.entity]
                before = vitals.total

                selection = aim(fight._eye(source), (x, y + 1, z))
                run.step({source: (*selection, 0), projector: (-16384, 0, 16)})
                self.assertEqual(npc_control.start(match, source), bot.entity)
                barriers = fight.zarya_barriers
                self.assertTrue(barriers.protects(bot))
                pool = barriers.projected[projector.body]
                pool.remaining = 5.12
                source.body_script.ult_seconds = 0
                source.body_script.set_ult_charge(2100)
                run.step({source: (*selection, 32)})
                for _ in range(60):
                    if fight.zarya_gravitons.fields:
                        break
                    run.step({})
                field, = fight.zarya_gravitons.fields
                for remaining in (0.12, 0.04, 0):
                    self.assertAlmostEqual(pool.remaining, remaining, places=4)
                    self.assertEqual(vitals.total, before)
                    self.assertEqual(fight.sleeps[bot.entity], deadline)
                    self.assertFalse(bot.attack_input)
                    run.step({})

                self.assertLess(run.frame, deadline)
                self.assertNotIn(bot.entity, fight.sleeps)
                self.assertAlmostEqual(vitals.total, before - 0.08, places=4)
                self.assertAlmostEqual(
                    projector.body_script.component.entity_var(87).value(), 1.024, places=4
                )
                self.assertIn(bot.entity, fight.zarya_gravitons.npc_movers)
                self.assertFalse(bot.attack_input)
                run.step({})
                self.assertTrue(bot.attack_input)
                npc_control.stop(match)
                while run.frame < field.end_tick:
                    run.step({})
                self.assertAlmostEqual(vitals.total, before - 19.84, places=4)
                self.assertFalse(fight.zarya_gravitons.fields)
                self.assertNotIn(bot.entity, fight.zarya_gravitons.npc_movers)

    def test_ultimate_never_damages_or_pulls_each_allied_body(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, player, bot = self.encounter(definition, team=0)
                before, position = run.match.combat.vitals[bot.entity].total, bot.position
                player.body_script.set_ult_charge(10000)
                self.fire(run, player, 1, 0x20)
                self.fire(run, player, 280, 0)
                self.assertEqual(run.match.combat.vitals[bot.entity].total, before)
                self.assertEqual(bot.position, position)

    def test_graviton_cover_interrupts_and_restores_each_npc_pull(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, caster, bot = self.encounter(definition)
                fight = run.match.combat
                caster.body_script.set_ult_charge(2100)
                self.fire(run, caster, 1, 32)
                for _ in range(60):
                    if bot.entity in fight.zarya_gravitons.npc_movers:
                        break
                    run.step({})
                field, = fight.zarya_gravitons.fields
                self.assertIn(bot.entity, fight.zarya_gravitons.npc_movers)
                self.assertLess(bot.velocity[2], 0)
                position = bot.position
                vitals = fight.vitals[bot.entity]
                before = vitals.total
                original = run.match.collision_world
                wall_z = (field.position[2] + position[2]) / 2
                run.match.collision_world = World([
                    (-10, 1, -10, -10, 1, 20, 20, 1, 20), (-10, 1, -10, 20, 1, 20, 20, 1, -10),
                    (-5, 0, wall_z, 5, 0, wall_z, 5, 5, wall_z),
                    (-5, 0, wall_z, 5, 5, wall_z, -5, 5, wall_z),
                ])
                for _ in range(5):
                    run.step({})
                self.assertEqual(fight.zarya_gravitons.fields, [field])
                self.assertEqual(vitals.total, before)
                self.assertEqual(bot.position, position)
                self.assertEqual(bot.velocity, (0, 0, 0))
                self.assertNotIn(bot.entity, fight.zarya_gravitons.npc_movers)
                run.match.collision_world = original
                run.step({})
                self.assertIn(bot.entity, fight.zarya_gravitons.npc_movers)
                self.assertLess(bot.position[2], position[2])
                self.assertAlmostEqual(vitals.total, before - 0.08, places=4)

    def test_graviton_and_projection_follow_each_npc_team_change(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, fight, caster, attacker, bot = self.ana_projection_arena(definition, protected=True)
                self.assertTrue(run.match.switch_hero(attacker, heroes()[ZARYA]))
                attacker.mover.position[:] = [0, 1, 0]
                attacker.mover.body = None
                attacker.body_script.set_ult_charge(2100)
                service = fight.zarya_barriers
                self.assertTrue(service.protects(bot))
                pool = service.projected[caster.body]
                vitals = fight.vitals[bot.entity]
                before = vitals.total
                x, y, z = bot.position
                angles = aim(fight._eye(attacker), (x, y + 1, z))
                run.step({attacker: (*angles, 32)})
                for _ in range(60):
                    if fight.zarya_gravitons.fields:
                        break
                    run.step({})
                field, = fight.zarya_gravitons.fields
                self.assertEqual(pool.remaining, 195)
                run.step({})
                self.assertIn(bot.entity, fight.zarya_gravitons.npc_movers)
                self.assertAlmostEqual(pool.remaining, 194.92, places=4)
                energy = caster.body_script.component.entity_var(87)
                self.assertAlmostEqual(energy.value(), 1.016, places=4)

                bot.team = attacker.team
                position = bot.position
                for _ in range(3):
                    run.step({})
                self.assertEqual(fight.zarya_gravitons.fields, [field])
                self.assertFalse(service.protects(bot))
                self.assertNotIn(bot.entity, fight.zarya_gravitons.npc_movers)
                self.assertEqual(bot.position, position)
                self.assertEqual(bot.velocity, (0, 0, 0))
                self.assertEqual(vitals.total, before)
                self.assertAlmostEqual(pool.remaining, 194.92, places=4)
                self.assertAlmostEqual(energy.value(), 1.016, places=4)

                bot.team = caster.team
                run.step({})
                self.assertIn(bot.entity, fight.zarya_gravitons.npc_movers)
                self.assertNotEqual(bot.position, position)
                self.assertEqual(vitals.total, before)
                self.assertIs(service.projected[caster.body], pool)
                self.assertAlmostEqual(pool.remaining, 194.84, places=4)
                self.assertAlmostEqual(energy.value(), 1.032, places=4)

    def test_graviton_releases_velocity_on_each_actual_npc_after_team_change_or_departure(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, player, bot = self.encounter(definition)
                gravitons = run.match.combat.zarya_gravitons
                player.body_script.set_ult_charge(2100)
                self.fire(run, player, 1, 0x20)
                for _ in range(60):
                    self.fire(run, player, 1, 0)
                    if bot.entity in gravitons.npc_movers:
                        break
                self.assertIn(bot.entity, gravitons.npc_movers)
                self.assertLess(bot.velocity[2], 0)
                position = bot.position
                bot.team = player.team
                self.fire(run, player, 1, 0)
                self.assertTrue(gravitons.fields)
                self.assertEqual(bot.position, position)
                self.assertEqual(bot.velocity, (0.0, 0.0, 0.0))
                self.assertFalse(gravitons.npc_movers)

                bot.team = 1 - player.team
                self.fire(run, player, 1, 0)
                self.assertIn(bot.entity, gravitons.npc_movers)
                self.assertLess(bot.velocity[2], 0)
                position = bot.position
                player.gone = True
                run.frame += 1
                run.match.update(run.frame * combat.TICK, run.frame)
                self.assertEqual(bot.position, position)
                self.assertEqual(bot.velocity, (0.0, 0.0, 0.0))
                self.assertFalse(gravitons.npc_movers)


if __name__ == "__main__":
    unittest.main()
