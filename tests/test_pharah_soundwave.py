"""Actual Lúcio weapon input against a Pharah player in the static server."""

import math
import os
import unittest
from dataclasses import replace
from unittest.mock import patch

import test_pharah_combat as pharah
import test_pharah_defenses as defense
import test_pharah_orisa_barrier as orisa
import test_pharah_sigma_barrier as sigma
import test_reinhardt_resources as reinhardt
from test_game_combat import KANEZAKA_DM, Run

from ow174.game import combat, replay_values, soundwave
from ow174.game.collision import World
from ow174.game.commands import SECONDARY
from ow174.game.content import heroes
from ow174.game.mover import ADD
from ow174.game.physics_snapshot import snapshot_mover
from ow174.game.script.health_queries import pool_sum
from ow174.game.zarya_barrier import PERSONAL, PROJECTED


class PharahSoundwaveTests(unittest.TestCase):
    health = pharah.PharahCombatTests.health
    clear_graphs = staticmethod(reinhardt.ReinhardtResourceTests.clear_graphs)

    def assert_soundwave_cast(self, source):
        weapon = next(i for i in source.body_script.component.instances.values()
                      if i.graph.index == soundwave.GRAPH)
        self.assertEqual(weapon.states[11].counter, 1)

    def encounter(self, *, z=4, ally=False, x=0):
        run = Run(KANEZAKA_DM, ("Lucio", "Pharah"))
        source, target = run.players
        catalog = heroes()
        run.match.switch_hero(source, catalog[soundwave.HERO])
        run.match.switch_hero(target, catalog[pharah.PHARAH])
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        source.team, target.team = 0, 0 if ally else 1
        y = source.mover.position[1]
        for player, side, depth in ((source, 0.0, 0.0), (target, x, z)):
            player.mover.position = [side, y, depth]
            player.mover.body = None
            player.body_script.ult_seconds = 0
            player.body_script.set_ult_charge(0)
        for _ in range(30):
            run.step({})
        return run, source, target

    def test_actual_soundwave_damages_and_pushes_pharah_once(self):
        run, source, target = self.encounter()
        run.players.reverse()
        before = tuple(target.mover.position)
        run.step({source: (0, 0, SECONDARY)})
        self.assertEqual(self.health(run, target).total, 175)
        self.assertEqual(tuple(target.mover.position), before)
        run.step({source: (0, 0, SECONDARY)})
        vx, vy, vz = target.mover.velocity
        self.assertAlmostEqual(vx, 0, delta=.002)
        self.assertAlmostEqual(vy / vz, .5, delta=.01)
        self.assertAlmostEqual(math.hypot(vx, vy, vz), 17, delta=.08)
        for _ in range(8):
            run.step({source: (0, 0, SECONDARY)})
        self.assertGreater(target.mover.position[1], before[1])
        self.assertGreater(target.mover.position[2], before[2])
        hits = source.client.messages(combat.HIT)
        self.assertEqual([hit["+0x78"]["+0x4"] for hit in hits], [25])
        self.assertEqual(self.health(run, target).total, 175)

    def test_actual_soundwave_excludes_ally_range_cone_and_world_cover(self):
        for label, args in (("team", {"ally": True}), ("range", {"z": 8}),
                            ("cone", {"z": 2, "x": 4}), ("wall", {})):
            with self.subTest(label=label):
                run, source, target = self.encounter(**args)
                fight = run.match.combat
                y = source.mover.position[1]
                triangles = [
                    (-3, y-2, 2, 3, y-2, 2, 3, y+4, 2),
                    (-3, y-2, 2, 3, y+4, 2, -3, y+4, 2),
                ] if label == "wall" else []
                with patch.object(fight, "_collision", return_value=World(triangles)):
                    run.step({source: (0, 0, SECONDARY)})
                self.assertEqual(self.health(run, target).total, 200)
                self.assertEqual(target.mover.velocity, (0, 0, 0))
                self.assertFalse(source.client.messages(combat.HIT))

    def test_actual_soundwave_displaces_pharah_without_stopping_held_hover(self):
        run, source, target = self.encounter()
        hover = next(i for i in target.body_script.component.instances.values() if i.graph.index == 0x25)
        thrust = hover.state(hover.graph.node(12).state)
        held = {target: (0, 0, SECONDARY)}
        for _ in range(12):
            run.step(held)
        self.assertTrue(thrust.active)
        self.assertTrue(target.mover.airborne)
        before_z = target.mover.position[2]
        fuel = hover.vars[87]
        before = fuel.value()
        run.step({**held, source: (0, 0, SECONDARY)})
        self.assertEqual(self.health(run, target).total, 175)
        self.assert_soundwave_cast(source)
        self.assertTrue(thrust.active)
        self.assertLess(fuel.value(), before)
        for _ in range(8):
            before = fuel.value()
            run.step(held)
            self.assertTrue(thrust.active)
            self.assertLess(fuel.value(), before)
        self.assertGreater(target.mover.position[2], before_z + .5)
        run.step({target: (0, 0, 0)})
        self.assertFalse(thrust.active)

    def test_actual_soundwave_displaces_pharah_during_barrage(self):
        run, source, target = self.encounter()
        script = target.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        run.step({target: (16384, 0, 32)})
        self.assertTrue(script.ultimate_running())
        barrage = next(i for i in script.component.instances.values() if i.graph.index == 0x2B).states[1]
        shots = barrage.reported
        before = tuple(target.mover.position)
        run.step({source: (0, 0, SECONDARY), target: (16384, 0, 0)})
        for _ in range(12):
            run.step({target: (16384, 0, 0)})
        self.assertEqual(self.health(run, target).total, 175)
        self.assertGreater(target.mover.position[2], before[2])
        self.assertTrue(script.ultimate_running())
        self.assertGreater(barrage.reported, shots)

    def test_actual_winston_sphere_filters_soundwave_by_surface_and_team(self):
        for label, source_z, target_z, yaw, allied, blocked in (
            ("hostile entry", 0, 4, 0, False, True),
            ("hostile exit", 4, 0, -32768, False, True),
            ("both inside", 2, 4, 0, False, False),
            ("friendly entry", 0, 4, 0, True, False),
        ):
            with self.subTest(label=label):
                run, source, owner, target = defense.PharahDefenseTests().encounter("Winston")
                run.match.switch_hero(source, heroes()[soundwave.HERO])
                run.match.switch_hero(target, heroes()[pharah.PHARAH])
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                source.team, owner.team, target.team = 0, 0 if allied else 1, 1
                y = owner.mover.position[1]
                for player, z in ((source, source_z), (owner, 6), (target, target_z)):
                    player.mover.position[:] = (0, y, z)
                    player.mover.body = None
                run.step({owner: (0, 0, 16)})
                for _ in range(4):
                    run.step({})
                bubble = run.match.combat.winston_bubbles[owner.body]
                run.step({source: (yaw, 0, SECONDARY)})
                run.step({})
                self.assert_soundwave_cast(source)
                self.assertEqual(self.health(run, target).total, 200 if blocked else 175)
                self.assertEqual(bubble.health, 700)
                self.assertEqual(any(target.mover.velocity), not blocked)

    def test_actual_held_barrier_blocks_soundwave_without_spending_its_pool(self):
        if not os.environ.get("ORPHEUS_REINHARDT_COLLISION"):
            self.skipTest("prepared 104319 Reinhardt mesh required")
        reinhardt.ReinhardtResourceTests.use_pet_bundle(self)
        run, source, owner, target = defense.PharahDefenseTests().encounter("Reinhardt")
        run.match.switch_hero(source, heroes()[soundwave.HERO])
        run.match.switch_hero(target, heroes()[pharah.PHARAH])
        y = owner.mover.position[1]
        for player, z in ((source, 0), (owner, 4.5), (target, 6)):
            player.mover.position[:] = (0, y, z)
            player.mover.body = None
        for _ in range(20):
            run.step({owner: (-32768, 0, SECONDARY)})
        pet = run.match.combat.barrier_pets[owner.body]
        run.step({source: (0, 0, SECONDARY), owner: (-32768, 0, SECONDARY)})
        run.step({owner: (-32768, 0, SECONDARY)})
        self.assert_soundwave_cast(source)
        self.assertEqual(self.health(run, target).total, 200)
        self.assertEqual(target.mover.velocity, (0.0, 0.0, 0.0))
        self.assertEqual(pool_sum(pet.world.health_pools), 1600)

    def test_actual_deployed_meshes_block_soundwave_without_spending_their_pools(self):
        for hero, asset in (("Orisa", "ORPHEUS_ORISA_GEOMETRY"), ("Sigma", "ORPHEUS_SIGMA_BARRIER_MODEL")):
            with self.subTest(hero=hero):
                if not os.environ.get(asset):
                    self.skipTest("prepared 104319 barrier mesh required")
                if hero == "Orisa":
                    _, run, source, _, target, barrier = orisa.OrisaProjectileTests().deployed()
                    yaw, source_side = -32768, 1
                else:
                    _, run, source, _, barrier = sigma.SigmaProjectileTests().setup_barrier()
                    target = run.players[2]
                    yaw, source_side = 0, -1
                run.match.switch_hero(source, heroes()[soundwave.HERO])
                run.match.switch_hero(target, heroes()[pharah.PHARAH])
                x, _, z = barrier.position
                y = source.mover.position[1]
                for player, side in ((source, source_side), (target, -source_side)):
                    player.mover.position[:] = (x, y, z + side * 3)
                    player.mover.body = None
                run.step({})
                before = barrier.health if hero == "Orisa" else barrier.vitals.total
                run.step({source: (yaw, 0, SECONDARY)})
                run.step({})
                self.assert_soundwave_cast(source)
                self.assertEqual(self.health(run, target).total, 200)
                self.assertEqual(target.mover.velocity, (0.0, 0.0, 0.0))
                after = barrier.health if hero == "Orisa" else barrier.vitals.total
                self.assertEqual(after, before)

    def test_actual_zarya_barriers_screen_pharah_from_soundwave(self):
        for projected in (False, True):
            with self.subTest(projected=projected):
                run, source, caster, target = defense.PharahDefenseTests().encounter("Zarya")
                run.match.switch_hero(source, heroes()[soundwave.HERO])
                run.match.switch_hero(target, heroes()[pharah.PHARAH])
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                source.team, caster.team, target.team = 0, 1, 1
                y = caster.mover.position[1]
                caster_x, caster_z = (4.5, 4.5) if projected else (0, 2.5)
                for player, x, z in ((source, 0, 0), (caster, caster_x, caster_z), (target, 0, 4.5)):
                    player.mover.position[:] = (x, y, z)
                    player.mover.body = None
                run.step({caster: (-16384, 1000, 16) if projected else (0, 0, 8)})
                for _ in range(3):
                    run.step({})
                barrier = run.match.combat.zarya_bubbles[caster.body, PROJECTED if projected else PERSONAL]
                self.assertIs(barrier.target, target if projected else caster)
                energy = caster.body_script.component.entity_var(87)
                energy_before = energy.value()
                run.step({source: (0, 0, SECONDARY)})
                run.step({})
                self.assert_soundwave_cast(source)
                self.assertEqual(self.health(run, target).total, 200)
                self.assertEqual(target.mover.velocity, (0.0, 0.0, 0.0))
                self.assertEqual(barrier.health, 200)
                self.assertEqual(energy.value(), energy_before)

    def test_body_replacement_discards_unconsumed_soundwave(self):
        run, source, target = self.encounter()
        run.players.reverse()
        run.step({source: (0, 0, SECONDARY)})
        mover = target.mover
        self.assertTrue(any(key[0] == -1 for key in mover.mods))
        pending = snapshot_mover(mover)
        self.assertEqual(pending, snapshot_mover(mover))
        nodes = replay_values.decode(pending["state"])["nodes"]
        modifiers = (dict(node["fields"]) for node in nodes if node["class"] == "Mod")
        queued = [mod for mod in modifiers if mod["op"] == ADD and mod["index"] == 0
                  and isinstance(mod["value"], dict)]
        self.assertEqual(len(queued), 1)
        self.assertEqual(queued[0]["start"], target.body_script.component.frame + 1)
        self.assertEqual(queued[0]["end"], queued[0]["start"] + 1)
        run.match.switch_hero(target, heroes()[pharah.PHARAH])
        self.assertIsNot(target.mover, mover)
        self.assertFalse(any(key[0] == -1 for key in mover.mods))
        run.step({})
        self.assertEqual(target.mover.velocity[0::2], (0.0, 0.0))
