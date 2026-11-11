"""Enemy hitscan against Hanzo and active allied absorption volumes."""

import os
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from test_game_combat import aim, in_world
import test_game_hanzo_matrix as matrix_tests
import test_game_hanzo_npc as npc_tests
from test_game_hanzo_mccree_fan import hanzo_arena

from ow174.game import arrow_deflection, combat, matrix, mover, npc, npc_control, sigma_grasp
from ow174.game.collision import flat_world
from ow174.game.content import heroes
from ow174.game.script import graph


def arena(test, kind, *, wall=False, allied=False, available=True, distance=16, defender_z=12):
    fixture, run, shooter, hanzo = hanzo_arena(test, distance=distance, wall=wall)
    fight = run.match.combat
    hero = heroes()[matrix.DVA if kind == "matrix" else sigma_grasp.SIGMA]
    if kind == "matrix":
        hero = replace(hero, body=matrix.MECH)
    team = shooter.team if allied else hanzo.team
    defender = run.match.add_player(3, kind, hero.guid, team, False)
    in_world(run.match, defender)
    run.players.append(defender)
    test.assertTrue(run.match.switch_hero(defender, hero))
    defender.mover.position[:] = (0.8, hanzo.mover.position[1], defender_z)
    defender.mover.body = None
    if available:
        fight.matrix_definitions = matrix.load_admitted_definitions(Path(os.environ["ORPHEUS_MATRIX_TAGS"]))
        test.assertIn(0x04000000000002A7, fight.matrix_definitions)
    else:
        fight.matrix_definitions = frozenset()
    for _ in range(75):
        run.step({})
    return fixture, run, shooter, hanzo, defender


def activate(test, run, defender, kind, *, active=True):
    button = (2 if kind == "matrix" else 8) if active else 0
    run.step({defender: (-32768, 0, button)})
    target = next(target for target in run.match.combat._targets() if target.owner is defender)
    defense_graph, slot = (0x02CC, 3) if kind == "matrix" else (0x1D02, 1)
    state = arrow_deflection.active_volume(target, defense_graph, slot)
    test.assertEqual(state is not None, active)
    return state, button


def shot(test, kind, *, active=True, wall=False, allied=False, available=True, nearer_body=False):
    fixture, run, shooter, hanzo, defender = arena(
        test, kind, wall=wall, allied=allied, available=available,
        distance=6 if nearer_body else 16, defender_z=18 if nearer_body else 12)
    fight = run.match.combat
    state, button = activate(test, run, defender, kind, active=active)
    x, y, z = hanzo.mover.position
    angles = aim(fight._eye(shooter), (x, y + 1, z))
    with patch.object(fight, "_hitscan", wraps=fight._hitscan) as hitscan:
        run.step({shooter: (*angles, 1), defender: (-32768, 0, button)})
        for _ in range(30):
            run.step({defender: (-32768, 0, button)})
    hitscan.assert_called_once()
    test.assertEqual(fixture.health(run, defender), 600 if kind == "matrix" else 400)
    stopped = active and not allied and available and not nearer_body
    test.assertEqual(fixture.health(run, hanzo), 200 if stopped or wall else 130)
    test.assertEqual([hit["+0x78"]["+0x4"] for hit in shooter.client.messages(combat.HIT)],
                     [] if stopped or wall else [70])
    if kind == "grasp" and active:
        amount = state.instance.find_var(0, 21312).value()
        test.assertEqual(amount, 70 if stopped and not wall else 0)
        for _ in range(160):
            if not state.active:
                break
            run.step({defender: (-32768, 0, 0)})
        test.assertFalse(state.active)
        run.step({})
        test.assertAlmostEqual(fight.vitals[defender.body].bonus_shields,
                               42 if stopped and not wall else 0, delta=1e-5)


def fan(test, kind):
    fixture, run, shooter, hanzo, defender = arena(test, kind)
    if kind == "grasp":
        defender.mover.position[:] = (0.65, hanzo.mover.position[1], 4)
        defender.mover.body = None
        for _ in range(20):
            run.step({})
    state, button = activate(test, run, defender, kind)
    fight = run.match.combat
    x, y, z = hanzo.mover.position
    angles = aim(fight._eye(shooter), (x, y + 1, z))
    with patch.object(fight, "_hitscan", wraps=fight._hitscan) as hitscan:
        for _ in range(100):
            run.step({shooter: (*angles, 2), defender: (-32768, 0, button)})
    test.assertEqual(hitscan.call_count, 6)
    test.assertTrue(all(call.args[4].amount == 50 and call.kwargs["absorbable"]
                        for call in hitscan.call_args_list))
    test.assertEqual(fixture.health(run, hanzo), 200)
    test.assertEqual(fixture.health(run, defender), 600 if kind == "matrix" else 400)
    test.assertFalse(shooter.client.messages(combat.HIT))
    if kind == "grasp":
        test.assertEqual(state.instance.find_var(0, 21312).value(), 300)


def deadeye(test, kind):
    if graph.graph(0x01EE) is None:
        test.skipTest("prepared Deadeye target-effect graph required")
    fixture, run, shooter, hanzo, defender = arena(test, kind)
    fight = run.match.combat
    script = shooter.body_script
    script.ult_seconds = 0
    script.set_ult_charge(1680)
    test.assertIn(0x0400000000000420, fight.matrix_definitions)
    run.step({shooter: (0, 0, 32)})
    for _ in range(180):
        run.step({})
    locked = {effect.vars[536].value().id for effect in script.component.instances.values()
              if effect.graph.index == 0x01EE}
    test.assertEqual(locked, {hanzo.body, defender.body})
    state, button = activate(test, run, defender, kind)
    with patch.object(fight, "_hitscan", wraps=fight._hitscan) as hitscan:
        run.step({shooter: (0, 0, 1), defender: (-32768, 0, button)})
        for _ in range(35):
            run.step({defender: (-32768, 0, button)})
    test.assertEqual(hitscan.call_count, 2)
    test.assertTrue(all(call.args[4].amount > 400 and call.kwargs["absorbable"]
                        for call in hitscan.call_args_list))
    test.assertEqual(fixture.health(run, hanzo), 200)
    test.assertEqual(fixture.health(run, defender), 600 if kind == "matrix" else 400)
    test.assertFalse(shooter.client.messages(combat.HIT))
    if kind == "grasp":
        expected = sum(call.args[4].amount for call in hitscan.call_args_list)
        test.assertAlmostEqual(state.instance.find_var(0, 21312).value(), expected, delta=0.001)
        for _ in range(150):
            if not state.active:
                break
            run.step({defender: (-32768, 0, 0)})
        test.assertFalse(state.active)
        run.step({})
        test.assertEqual(fight.vitals[defender.body].bonus_shields, 400)


def trooper(test, kind):
    data = os.environ.get("ORPHEUS_NPC_TEST_DATA")
    if not data:
        test.skipTest("prepared Talon NPC data required")
    catalog = npc.load_catalog(Path(data) / "pve_npcs_174.json")
    fixture = npc_tests.HanzoCatalogNpcTests
    run, hanzo, bot = fixture.arena(test, catalog["1"], "bow", "hit", distance=8)
    match = run.match
    test.addCleanup(npc.remove_spawned, match)
    x, y, z = hanzo.mover.position
    match.collision_world = flat_world(y)
    fight = match.combat
    fight.matrix_definitions = matrix.load_admitted_definitions(Path(os.environ["ORPHEUS_MATRIX_TAGS"]))
    test.assertIn(0x0400000000001FE6, fight.matrix_definitions)
    hero = heroes()[matrix.DVA if kind == "matrix" else sigma_grasp.SIGMA]
    if kind == "matrix":
        hero = replace(hero, body=matrix.MECH)
    defender = match.add_player(2, kind, hero.guid, hanzo.team, False)
    in_world(match, defender)
    defender.client = npc_tests.NpcFixtureClient(defender)
    run.players.append(defender)
    test.assertTrue(match.switch_hero(defender, hero))
    defender.mover.position[:] = (x + 3, y, z + 4)
    defender.mover.body = None
    for _ in range(75):
        run.step({})
    button, graph_id, slot = (2, 0x02CC, 3) if kind == "matrix" else (8, 0x1D02, 1)
    run.step({hanzo: (*fixture.target_aim(run, hanzo, bot), 0), defender: (0, 0, button)})
    body = next(body for body in fight._targets() if body.owner is defender)
    state = arrow_deflection.active_volume(body, graph_id, slot)
    test.assertIsNotNone(state)
    test.assertEqual(npc_control.start(match, hanzo), bot.entity)
    defender.mover.position[0] = x + 0.65
    defender.mover.body = None
    with patch.object(fight, "_hitscan", wraps=fight._hitscan) as hitscan:
        for _ in range(100):
            run.step({defender: (0, 0, button)})
            if hitscan.called:
                break
    hitscan.assert_called_once()
    test.assertIs(hitscan.call_args.args[0], bot)
    test.assertEqual(hitscan.call_args.args[4].amount, 20)
    test.assertTrue(hitscan.call_args.kwargs["absorbable"])
    test.assertEqual(fight.vitals[hanzo.body].total, 200)
    test.assertEqual(fight.vitals[defender.body].total, 600 if kind == "matrix" else 400)
    if kind == "grasp":
        test.assertEqual(state.instance.find_var(0, 21312).value(), 20)


class HanzoHitscanGraspTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.environ.get("ORPHEUS_MATRIX_TAGS"):
            raise unittest.SkipTest("prepared 104319 projectile tags required")

    def test_primary_respects_grasp_wall_faction_and_prepared_admission(self):
        for options in ({}, {"wall": True}, {"active": False}, {"allied": True},
                        {"available": False}, {"nearer_body": True}):
            with self.subTest(**options):
                shot(self, "grasp", **options)

    def test_grasp_accumulates_six_actual_fan_rounds(self):
        fan(self, "grasp")

    def test_grasp_absorbs_both_actual_deadeye_rays(self):
        deadeye(self, "grasp")

    def test_grasp_accumulates_actual_trooper_round(self):
        trooper(self, "grasp")

    def test_reflected_primary_changes_faction_and_grasp_source(self):
        fixture, run, shooter, hanzo, sigma = arena(self, "grasp", allied=True, distance=2)
        match = run.match
        fight = match.combat
        hanzo.team = shooter.team
        floor = hanzo.mover.position[1]
        hanzo.mover.position[0] = 0.85
        hanzo.mover.body = None
        sigma.mover.position[:] = (1.4, floor, 2)
        sigma.mover.body = None
        genji = match.add_player(4, "Genji", arrow_deflection.GENJI, 1 - hanzo.team, False)
        in_world(match, genji)
        run.players.append(genji)
        self.assertTrue(match.switch_hero(genji, heroes()[arrow_deflection.GENJI]))
        genji.mover.position[:] = (0.55, floor, 4)
        genji.mover.body = None
        facing = 28200
        grasp_angles = aim(fight._eye(sigma), fight._eye(genji))
        idle = {sigma: (*grasp_angles, 0), genji: (facing, 0, 0)}
        for _ in range(25):
            run.step(idle)
        run.step({sigma: (*grasp_angles, 8), genji: (facing, 0, 16)})
        angles = aim(fight._eye(shooter), (0, floor + 1.35, 4))
        with patch.object(fight.grasp, "absorb", wraps=fight.grasp.absorb) as absorbed:
            run.step({**idle, shooter: (*angles, 1)})
            for _ in range(30):
                run.step(idle)
        absorbed.assert_called_once()
        state, source, direct, _, _ = absorbed.call_args.args
        self.assertEqual(source, genji.body)
        self.assertEqual(direct.amount, 70)
        self.assertEqual(state.instance.find_var(0, 21312).value(), 70)
        self.assertEqual(fixture.health(run, hanzo), 200)
        self.assertEqual(fixture.health(run, genji), 200)
        self.assertEqual(fixture.health(run, sigma), 400)
        self.assertFalse(genji.client.messages(combat.HIT))


class HanzoHitscanMatrixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        graphs, movers, tags = (os.environ.get("ORPHEUS_MATRIX_" + name) for name in ("GRAPHS", "MOVERS", "TAGS"))
        additive = graph.GRAPH_SUPPLEMENT_PATH is not None and mover.MOVERS_SUPPLEMENT_PATH is not None
        if not tags or (not additive and not (graphs and movers)):
            raise unittest.SkipTest("prepared 0407 graphs, movers and projectile tags required")
        if additive:
            return
        deadeye_effect = graph._records().get(0x01EE)
        cls.enterClassContext(patch.object(graph, "GRAPHS_PATH", Path(graphs)))
        cls.enterClassContext(patch.object(mover, "MOVERS_PATH", Path(movers)))
        cls.addClassCleanup(matrix_tests.HanzoMatrixTests.reset_catalogs)
        matrix_tests.HanzoMatrixTests.reset_catalogs()
        # The mech bundle predates the separately prepared target-effect graph.
        if deadeye_effect is not None:
            records = {**graph._records(), 0x01EE: deadeye_effect}
            cls.enterClassContext(patch.object(graph, "_records", return_value=records))

    def test_primary_respects_matrix_wall_faction_and_prepared_admission(self):
        for options in ({}, {"wall": True}, {"active": False}, {"allied": True},
                        {"available": False}, {"nearer_body": True}):
            with self.subTest(**options):
                shot(self, "matrix", **options)

    def test_matrix_consumes_six_actual_fan_rounds(self):
        fan(self, "matrix")

    def test_matrix_absorbs_both_actual_deadeye_rays(self):
        deadeye(self, "matrix")

    def test_matrix_consumes_actual_trooper_round(self):
        trooper(self, "matrix")
