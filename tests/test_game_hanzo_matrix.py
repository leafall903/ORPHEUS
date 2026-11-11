"""Actual 0407 Matrix and Hanzo attacks with prepared build 104319 inputs."""

import math
import os
import unittest
from dataclasses import replace
from itertools import product
from pathlib import Path
from unittest.mock import patch

import test_game_hanzo_dragons as dragon_tests
import test_game_hanzo_npc as npc_tests
import test_game_hanzo_sonic as sonic_tests
import test_game_hanzo_winston_barrier as winston_tests
from test_game_combat import Run, aim, in_world

from ow174.game import arrow_deflection, bodytrace, combat, matrix, movelog, mover, npc, npc_control, sigma_grasp, winston_barrier
from ow174.game.collision import flat_world
from ow174.game.content import heroes
from ow174.game.script import graph
from ow174.game.world import ARMOUR_POOL


class HanzoMatrixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        graphs, movers, tags = (os.environ.get("ORPHEUS_MATRIX_" + name) for name in ("GRAPHS", "MOVERS", "TAGS"))
        additive = graph.GRAPH_SUPPLEMENT_PATH is not None and mover.MOVERS_SUPPLEMENT_PATH is not None
        if not tags or (not additive and not (graphs and movers)):
            raise unittest.SkipTest("prepared 0407 graphs, movers and projectile tags required")
        if not additive:
            cls.enterClassContext(patch.object(graph, "GRAPHS_PATH", Path(graphs)))
            cls.enterClassContext(patch.object(mover, "MOVERS_PATH", Path(movers)))
        cls.admitted = matrix.load_admitted_definitions(Path(tags))
        cls.addClassCleanup(cls.reset_catalogs)
        cls.reset_catalogs()

    @staticmethod
    def reset_catalogs():
        graph._data.cache_clear()
        graph._records.cache_clear()
        graph.graph.cache_clear()
        mover._movers.cache_clear()

    def setUp(self):
        for module in (bodytrace, movelog):
            self.enterContext(patch.object(module, "ENABLED", False))

    def encounter(self, attack, *, facing=0, release=False, distance=8, followup_melee=False, moving_dragon=False,
                  release_in_flight=False, activate_in_flight=False, draw_frames=71):
        run = Run(names=("Hanzo", "Mech"))
        hanzo, mech = run.players
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        self.assertTrue(run.match.switch_hero(hanzo, heroes()[0x02E0000000000005]))
        run.match.collision_world = flat_world(0)
        self.assertTrue(run.match.switch_hero(mech, replace(heroes()[matrix.DVA], body=matrix.MECH)))
        for player, position in ((hanzo, [0, 0, distance]), (mech, [0, 0, 0])):
            player.mover.position = position
            player.mover.body = None
        fight = run.match.combat
        fight.matrix_definitions = self.admitted
        if attack == "sonic":
            fight.configure_sonic(sonic_tests.profile())
        if attack == "dragon":
            fight.configure_dragonstrike(dragon_tests.prepared_profile())
            hanzo.body_script.ult_seconds = 0
            hanzo.body_script.set_ult_charge(1680)
        def step(attack_button=0, held=0):
            angles = aim(fight._eye(hanzo), (0, 1.2, 0))
            run.step({hanzo: (*angles, attack_button), mech: (facing, 0, held)})

        for _ in range(75):
            step()
        instance = next(i for i in mech.body_script.component.instances.values() if i.graph.index == 0x02CC)
        if attack == "sonic":
            step(8)
            step()
        if attack in ("bow", "sonic"):
            for frame in range(draw_frames - 1):
                step(1, 2 if release_in_flight and frame >= 20 else 0)
            step(1, 0 if activate_in_flight else 2)
            if release:
                self.assertTrue(instance.states[3].active)
                for _ in range(100):
                    step(1)
                    if not instance.states[3].active:
                        break
                self.assertFalse(instance.states[3].active)
            step(0, 0 if release or activate_in_flight else 2)
        else:
            step(16 if attack == "storm" else 32)
            step()
            for _ in range(100):
                step(1 if attack == "storm" else 0)
                if fight.rockets:
                    break
            step(0, 2)
        state = instance.states.get(3)
        self.assertEqual(bool(state and state.active), not (release or activate_in_flight))
        arrow, = fight.rockets
        self.assertTrue(arrow.matrix_absorbable)
        lease = arrow.shot
        if activate_in_flight:
            step()
            self.assertTrue(arrow in fight.rockets)
            self.assertFalse(bool(state and state.active))
            step(0, 2)
            self.assertTrue(instance.states[3].active)
            self.assertTrue(arrow in fight.rockets)
        if release_in_flight:
            for _ in range(20):
                step()
                if not instance.states[3].active:
                    break
            self.assertFalse(instance.states[3].active)
            self.assertTrue(arrow in fight.rockets)
            self.assertEqual(fight.vitals[mech.body].total, 600)
        for _ in range(42):
            step(0, 0 if release or release_in_flight else 2)
        if followup_melee:
            self.assertTrue(instance.states[3].active)
            self.assertEqual(fight.vitals[mech.body].total, 600)
            self.assertFalse(hanzo.client.messages(combat.HIT))
            self.assertIsNone(arrow.shot)
            step(0x800, 2)
            for _ in range(12):
                step(0, 2)
            self.assertTrue(instance.states[3].active)
        if moving_dragon:
            self.assertTrue(instance.states[3].active)
            self.assertEqual(fight.vitals[mech.body].total, 600)
            self.assertEqual(len(fight.dragons), 1)
            for _ in range(75):
                if fight.vitals[mech.body].total < 600:
                    break
                step(0, 2)
            self.assertTrue(instance.states[3].active)
        return fight, hanzo, mech, arrow, lease

    def talon_defense_flight(self, key, distance, *placements, alternate_sniper=False):
        folder = os.environ.get("ORPHEUS_NPC_TEST_DATA")
        if not folder:
            self.skipTest("requires explicit prepared NPC data")
        catalog = npc.load_catalog(Path(folder) / "pve_npcs_174.json")
        fixture = npc_tests.HanzoCatalogNpcTests
        run, hanzo, bot = fixture.arena(self, catalog[key], "bow", "hit", distance=distance)
        match = run.match
        self.addCleanup(npc.remove_spawned, match)
        x, y, z = hanzo.mover.position
        match.collision_world = flat_world(y)
        match.game_map = replace(match.game_map, free_for_all=False)
        defenders = []
        for slot, (guid, body, forward) in enumerate(placements, start=3):
            defender = match.add_player(slot, f"Defender {slot}", guid, hanzo.team, False)
            in_world(match, defender)
            defender.client = npc_tests.NpcFixtureClient(defender)
            run.players.append(defender)
            hero = heroes()[guid]
            self.assertTrue(match.switch_hero(defender, hero if body is None else replace(hero, body=body)))
            defender.team = hanzo.team
            defender.mover.position[:] = (x + 1.2, y, z + forward)
            defender.mover.body = None
            defenders.append(defender)
        fight = match.combat
        fight.matrix_definitions = self.admitted
        for _ in range(30):
            run.step({})
        angles = fixture.target_aim(run, hanzo, bot)
        if alternate_sniper:
            self.assertEqual(key, "3")
            component = bot.body_script.component
            component.edges([(16, True)])
            run.step({hanzo: (*angles, 0)})
            component.edges([(16, False)])
        run.step({hanzo: (*angles, 0)})
        self.assertEqual(npc_control.start(match, hanzo), bot.entity)
        with patch.object(fight.projectiles, "fired", wraps=fight.projectiles.fired) as fired:
            for _ in range(150):
                run.step({})
                if fight.rockets:
                    break
        shots = list(fight.rockets)
        self.assertEqual(len(shots), 1 if key == "3" else 25)
        self.assertTrue(all(shot.matrix_absorbable for shot in shots))
        if key == "3":
            definition = 0x04000000000023BB if alternate_sniper else 0x0400000000001E80
        else:
            definition = 0x04000000000023F0
        self.assertEqual([call.args[3] for call in fired.call_args_list], [definition] * len(shots))
        for defender in defenders:
            defender.mover.position[0] = x
            defender.mover.body = None
        return run, hanzo, bot, defenders, shots

    def test_matrix_absorbs_actual_tagged_sniper_and_enforcer_shots(self):
        for key, distance, alternate_sniper, unprotected in (("3", 32, False, 95), ("3", 32, True, 2.5),
                                                            ("2", 5, False, 25)):
            observed = []
            for active in (False, True):
                with self.subTest(npc=key, alternate_sniper=alternate_sniper, active=active):
                    run, hanzo, _, (mech,), shots = self.talon_defense_flight(
                        key, distance, (matrix.DVA, matrix.MECH, 2), alternate_sniper=alternate_sniper)
                    fight = run.match.combat
                    leases = [shot.shot for shot in shots if shot.shot is not None]
                    run.step({mech: (0, 0, 2 if active else 0)})
                    self.assertEqual(fight.rockets, shots)
                    target = next(t for t in fight._targets() if t.owner is mech)
                    self.assertEqual(any(matrix.trace(target, shot.position, shot.direction, distance) is not None
                                         for shot in shots), active)
                    pool = fight.vitals_of(target)
                    self.assertEqual(pool.total, 600)
                    for _ in range(40):
                        run.step({mech: (0, 0, 2 if active else 0)})
                        if not any(shot in fight.rockets for shot in shots):
                            break
                    self.assertFalse(any(shot in fight.rockets for shot in shots))
                    self.assertTrue(all(lease not in fight.projectiles.shots for lease in leases))
                    target_hanzo = next(t for t in fight._targets() if t.owner is hanzo)
                    self.assertEqual(fight.vitals_of(target_hanzo).total, 200)
                    observed.append(600 - pool.total)
            self.assertEqual(observed, [unprotected, 0])

    def test_actual_deflect_and_grasp_handle_each_enforcer_pellet(self):
        for guid, button in ((arrow_deflection.GENJI, 16), (sigma_grasp.SIGMA, 8)):
            for active in (False, True):
                with self.subTest(defender=guid, active=active):
                    run, hanzo, bot, (defender,), pellets = self.talon_defense_flight("2", 5, (guid, None, 2))
                    fight = run.match.combat
                    # Release future fire while preserving the already committed volley.
                    bot.firing = False
                    self.assertTrue(all(pellet.direct.amount == 2 and pellet.direct.deflectable
                                        for pellet in pellets))
                    bot_pool = fight.vitals[bot.entity]
                    before = bot_pool.total
                    run.step({defender: (0, 0, button if active else 0)})
                    self.assertEqual(fight.rockets, pellets)
                    defender_target = next(t for t in fight._targets() if t.owner is defender)
                    defender_pool = fight.vitals_of(defender_target)
                    defender_before = defender_pool.total
                    for _ in range(40):
                        run.step({})
                        if not any(pellet in fight.rockets for pellet in pellets):
                            break
                    self.assertFalse(any(pellet in fight.rockets for pellet in pellets))
                    target_hanzo = next(t for t in fight._targets() if t.owner is hanzo)
                    self.assertEqual(fight.vitals_of(target_hanzo).total, 200)
                    self.assertEqual(defender_pool.total, defender_before if active else defender_before - 50)
                    if active and guid == arrow_deflection.GENJI:
                        self.assertTrue(all(pellet.source_entity == defender.body for pellet in pellets))
                        self.assertEqual(bot_pool.total, before - 50)
                        self.assertEqual([hit["+0x78"]["+0x4"] for hit in defender.client.messages(combat.HIT)],
                                         [2] * 25)
                    elif active:
                        grasp = next(i for i in defender.body_script.component.instances.values()
                                     if i.graph.index == sigma_grasp.GRAPH)
                        self.assertEqual(grasp.find_var(0, 21312).value(), 50)
                        for _ in range(160):
                            run.step({})
                            if defender_pool.bonus_shields:
                                break
                        self.assertEqual(grasp.find_var(0, 21312).value(), 50)
                        self.assertAlmostEqual(defender_pool.bonus_shields, 30, places=4)

    def test_reflected_enforcer_volley_keeps_accumulated_distance(self):
        run, hanzo, bot, (genji,), pellets = self.talon_defense_flight(
            "2", 16, (arrow_deflection.GENJI, None, 2))
        fight = run.match.combat
        bot.firing = False
        bot_pool = fight.vitals[bot.entity]
        before = bot_pool.total
        run.step({genji: (0, 0, 16)})
        self.assertEqual(fight.rockets, pellets)
        reflected_at = {}
        with patch.object(fight, "_damage", wraps=fight._damage) as damage:
            for _ in range(90):
                run.step({})
                for index, pellet in enumerate(pellets):
                    if pellet.source_entity == genji.body:
                        reflected_at.setdefault(index, pellet.distance_flown)
                if not fight.rockets:
                    break
        self.assertFalse(fight.rockets)
        self.assertTrue(reflected_at)
        returned = [call for call in damage.call_args_list if call.args[1].owner is bot]
        self.assertTrue(returned)
        self.assertTrue(all(call.args[0] is genji and call.kwargs["source_entity"] == genji.body
                            for call in returned))
        for call in returned:
            self.assertAlmostEqual(call.args[2], 0.4)
        self.assertAlmostEqual(bot_pool.total, before - len(returned) * 0.4, places=5)
        # Misses fly on to the 100 m limit; returned hits stop at the bot.
        returned_paths = [(reflected_at[index], pellet.distance_flown)
                          for index, pellet in enumerate(pellets)
                          if index in reflected_at and pellet.distance_flown < 40]
        self.assertEqual(len(returned_paths), len(returned))
        for first_leg, total in returned_paths:
            self.assertLess(first_leg, 15)
            self.assertLess(total - first_leg, 15)
            self.assertGreater(total, 15)
        hanzo_target = next(t for t in fight._targets() if t.owner is hanzo)
        self.assertEqual(fight.vitals_of(hanzo_target).total, 200)

    def test_actual_sniper_round_uses_first_of_grasp_and_matrix(self):
        placements = ((False, 2, 8), (True, 20, 2))
        for alternate_sniper, (sigma_first, sigma_z, mech_z) in product((False, True), placements):
            with self.subTest(alternate_sniper=alternate_sniper, sigma_first=sigma_first):
                run, hanzo, bot, (sigma, mech), (shot,) = self.talon_defense_flight(
                    "3", 32, (sigma_grasp.SIGMA, None, sigma_z), (matrix.DVA, matrix.MECH, mech_z),
                    alternate_sniper=alternate_sniper)
                fight = run.match.combat
                bot.firing = False
                damage = 5 if alternate_sniper else 100
                self.assertEqual((shot.source_entity, shot.direct.amount), (bot.entity, damage))
                self.assertIsNotNone(shot.shot)
                held = {mech: (0, 0, 2), sigma: (0, 0, 8)}
                run.step(held)
                self.assertIn(shot, fight.rockets)
                targets = {target.owner: target for target in fight._targets() if target.owner in held}
                grasp_contact = fight.grasp.trace(targets[sigma], shot.position, shot.direction, 32)
                matrix_contact = matrix.trace(targets[mech], shot.position, shot.direction, 32)
                self.assertIsNotNone(grasp_contact)
                self.assertIsNotNone(matrix_contact)
                self.assertEqual(grasp_contact[0] < matrix_contact, sigma_first)
                for _ in range(40):
                    run.step({mech: held[mech]})
                    if shot not in fight.rockets:
                        break
                self.assertNotIn(shot, fight.rockets)
                self.assertIsNone(shot.shot)
                self.assertEqual(shot.source_entity, bot.entity)
                grasp = grasp_contact[1]
                self.assertEqual(grasp.instance.find_var(0, 21312).value(), damage if sigma_first else 0)
                for player, expected in ((hanzo, 200), (sigma, 400), (mech, 600)):
                    self.assertEqual(fight.vitals[player.body].total, expected)
                for _ in range(160):
                    run.step({})
                    if not grasp.active:
                        break
                self.assertFalse(grasp.active)
                self.assertAlmostEqual(fight.vitals[sigma.body].bonus_shields,
                                       damage * 0.6 if sigma_first else 0, places=4)

    def test_matrix_consumes_real_bow_sonic_storm_and_initial_dragon(self):
        for attack in ("bow", "sonic", "storm", "dragon"):
            with self.subTest(attack=attack):
                fight, hanzo, mech, arrow, lease = self.encounter(attack)
                self.assertEqual(fight.vitals[mech.body].total, 600)
                self.assertFalse(hanzo.client.messages(combat.HIT))
                self.assertFalse(fight.rockets)
                self.assertFalse(fight.sonars)
                self.assertFalse(fight.dragons)
                self.assertIsNone(arrow.shot)
                if lease is not None:
                    self.assertNotIn(lease, fight.projectiles.shots)
                self.assertGreater(arrow.position[2], 1)

    def test_away_and_released_matrix_allow_bow_damage(self):
        for facing, release in ((-32768, False), (0, True)):
            with self.subTest(facing=facing, release=release):
                fight, hanzo, mech, _, _ = self.encounter("bow", facing=facing, release=release)
                self.assertLess(fight.vitals[mech.body].total, 600)
                self.assertEqual(len(hanzo.client.messages(combat.HIT)), 1)


    def test_releasing_matrix_in_flight_lets_actual_bow_and_sonic_hit(self):
        for attack in ("bow", "sonic"):
            with self.subTest(attack=attack):
                fight, hanzo, mech, arrow, _ = self.encounter(
                    attack, distance=24, release_in_flight=True,
                )
                self.assertLess(fight.vitals[mech.body].total, 600)
                self.assertEqual(len(hanzo.client.messages(combat.HIT)), 1)
                self.assertFalse(arrow in fight.rockets)
                if attack == "sonic":
                    beacon, = fight.sonars
                    self.assertEqual(beacon.attached, mech.body)
                    self.assertTrue(fight.revealed_to(hanzo, mech))
                else:
                    self.assertFalse(fight.sonars)

    def test_activating_matrix_in_flight_consumes_actual_bow_and_sonic(self):
        for attack, draw_frames in (("bow", 71), ("sonic", 71), ("bow", 5), ("sonic", 5)):
            with self.subTest(attack=attack, draw_frames=draw_frames):
                fight, hanzo, mech, arrow, lease = self.encounter(
                    attack, distance=12 if draw_frames == 5 else 24, activate_in_flight=True, draw_frames=draw_frames,
                )
                self.assertEqual(fight.vitals[mech.body].total, 600)
                self.assertFalse(hanzo.client.messages(combat.HIT))
                self.assertFalse(fight.sonars)
                self.assertFalse(fight.revealed_to(hanzo, mech))
                self.assertFalse(arrow in fight.rockets)
                self.assertIsNone(arrow.shot)
                self.assertIsNotNone(lease)
                self.assertNotIn(lease, fight.projectiles.shots)
                if draw_frames == 5:
                    definition = 0x020D if attack == "sonic" else 0x020B
                    self.assertEqual(lease.definition, 0x0400000000000000 | definition)
                self.assertGreater(arrow.position[2], 1)

    def test_actual_melee_bypasses_active_matrix_at_close_range(self):
        for distance, dealt in ((1.8, 25), (4, 0)):
            with self.subTest(distance=distance):
                fight, hanzo, mech, _, lease = self.encounter(
                    "bow", distance=distance, followup_melee=True,
                )
                self.assertNotIn(lease, fight.projectiles.shots)
                vitals = fight.vitals[mech.body]
                self.assertEqual(vitals.now[ARMOUR_POOL], vitals.most[ARMOUR_POOL] - dealt)
                self.assertEqual(vitals.total, 600 - dealt)
                hits = hanzo.client.messages(combat.HIT)
                self.assertEqual([hit["+0x78"]["+0x4"] for hit in hits], [25] if dealt else [])
                self.assertFalse(fight.rockets)


    def test_winston_sphere_and_matrix_order_the_initial_dragonstrike(self):
        profile = dragon_tests.prepared_profile()
        for allied, mech_z, matrix_first in ((False, 8, True), (False, 17, False), (True, 17, False)):
            with self.subTest(allied=allied, mech_z=mech_z):
                run, hanzo, winston, bubble = winston_tests.HanzoWinstonBarrierTests.arena(self, allied=allied)
                match = run.match
                fight = match.combat
                fight.configure_dragonstrike(profile)
                fight.matrix_definitions = self.admitted
                mech = match.add_player(3, "Enemy mech", matrix.DVA, 1, False)
                in_world(match, mech)
                run.players.append(mech)
                self.assertTrue(match.switch_hero(mech, replace(heroes()[matrix.DVA], body=matrix.MECH)))
                mech.team = 1
                mech.mover.position[:] = (0, 0, mech_z)
                mech.mover.body = None
                hanzo.body_script.ult_seconds = None
                hanzo.body_script.set_ult_charge(1680)
                run.step({hanzo: (0, 0, 32)})

                for frame in range(1, 80):
                    run.step({mech: (-32768, 0, 2 if frame >= 53 else 0)})
                    if fight.rockets:
                        break
                arrow, = fight.rockets
                self.assertIs(arrow.continuation, profile)
                self.assertTrue(arrow.matrix_absorbable)
                lease = arrow.shot
                target = next(t for t in fight._targets() if t.owner is mech)
                self.assertIsNotNone(arrow.range_left)
                ray = arrow.position, arrow.direction, arrow.range_left
                contact = matrix.trace(target, *ray)
                shell = fight.winston_barriers.intersect(bubble, *ray)
                self.assertIsNotNone(contact)
                self.assertIsNotNone(shell)
                self.assertEqual(contact < shell, matrix_first, (contact, shell))

                for _ in range(25):
                    run.step({mech: (-32768, 0, 2)})
                    if arrow not in fight.rockets:
                        break
                self.assertNotIn(arrow, fight.rockets)
                self.assertFalse(arrow.hit_entities)
                self.assertIsNone(arrow.shot)
                if lease is not None:
                    self.assertNotIn(lease, fight.projectiles.shots)
                mech_pool = fight.vitals_of(target)
                self.assertEqual(mech_pool.total, 600)
                self.assertEqual(fight.vitals[winston.body].total, 500)
                if matrix_first or allied:
                    self.assertEqual(bubble.health, 700)
                    self.assertFalse(fight.dragons)
                    self.assertAlmostEqual(arrow.position[2], ray[0][2] + contact * ray[1][2])
                else:
                    self.assertEqual(bubble.health, 575)
                    flight, = fight.dragons
                    self.assertEqual(flight.source_entity, hanzo.body)
                    self.assertAlmostEqual(math.dist(flight.origin, bubble.centre), winston_barrier.RADIUS)
                    for _ in range(95):
                        run.step({mech: (-32768, 0, 2)})
                        if mech_pool.total < 600:
                            break
                    self.assertIsNotNone(arrow_deflection.active_volume(target, 0x02CC, 3))
                    self.assertLess(mech_pool.total, 600)
                    self.assertLess(fight.vitals[winston.body].total, 500)
                    self.assertEqual(bubble.health, 575)


    def test_moving_dragonstrike_damages_through_actual_matrix(self):
        fight, hanzo, mech, arrow, _ = self.encounter(
            "dragon", distance=24, moving_dragon=True,
        )
        flight, = fight.dragons
        self.assertEqual(flight.source_entity, hanzo.body)
        self.assertNotIn(mech.body, arrow.hit_entities)
        self.assertFalse(fight.rockets)
        self.assertLess(fight.vitals[mech.body].total, 600)
