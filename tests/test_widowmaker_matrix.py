"""Widow commands against authored mech Matrix; volume attachment is reconstructed."""

import json
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from test_game_combat import aim
from test_widowmaker_combat import FIRE, MELEE, SCOPE, _WidowmakerMatch

from ow174.game import combat, deflection
from ow174.game.collision import World
from ow174.game.content import heroes
from ow174.game.mover import defined_mover_data
from ow174.game.script import graph

MECH = 0x0400000000000407


class _MatrixMatch(_WidowmakerMatch):
    @classmethod
    def setUpClass(cls):
        if graph.entity_body(MECH) is None or defined_mover_data(MECH) is None:
            raise unittest.SkipTest("requires explicit mech graph/mover supplements")
        if not deflection.catalog().absorbable(0x040000000000004C):
            raise unittest.SkipTest("requires explicit projectile tag catalog")

    def setUp(self):
        super().setUp()
        self.assertTrue(self.run.match.switch_hero(
            self.b, replace(heroes()[0x02E000000000007A], body=MECH)))
        self.b.mover.position[:] = (0, 0, 15)
        self.b.mover.body = None
        self.b.mover.history.clear()
        self.run.match.combat.history.pop(self.b.body, None)
        self.a.body_script.ult_seconds = 0
        self.a.body_script.set_ult_charge(0)
        self.step(100)

    def step(self, count=1, *, widow=0, matrix=0, away=False):
        for _ in range(count):
            forward = aim(self.run.match.combat._eye(self.a),
                          (0, self.b.mover.position[1] + 1, self.b.mover.position[2]))
            back = aim(self.run.match.combat._eye(self.b), self.run.match.combat._eye(self.a))
            self.run.step({self.a: (*forward, widow),
                           self.b: (0 if away else back[0], 0, matrix)})

    def matrix(self):
        return next(i for i in self.b.body_script.component.instances.values() if i.graph.index == 0x2CC)

    def charge(self):
        return self.a.body_script.component.vars[342].value()



class WidowmakerMatrixTests(_MatrixMatch, unittest.TestCase):
    def test_primary_absorbed_then_release_reopens_damage(self):
        self.step(2, matrix=2)
        self.assertTrue(self.matrix().state(3).active)
        self.step(30, widow=FIRE, matrix=2)
        self.assertEqual(self.health(self.b), 600)
        self.assertEqual(self.charge(), 0)
        self.assertEqual(self.a.client.messages(combat.HIT), [])
        self.step(45, widow=FIRE)
        self.assertLess(self.health(self.b), 600)
        self.assertGreater(self.charge(), 0)

    def test_scoped_absorbed_spends_five_ammo_without_charge(self):
        self.step(100, widow=SCOPE)
        self.step(2, widow=SCOPE, matrix=2)
        weapon = next(i for i in self.a.body_script.component.instances.values() if i.graph.index == 0x1B)
        ammo = weapon.vars[53].value()
        self.step(widow=SCOPE | FIRE, matrix=2)
        self.assertEqual(ammo - weapon.vars[53].value(), 5)
        self.assertEqual(self.health(self.b), 600)
        self.assertEqual(self.charge(), 0)

    def test_away_facing_and_allied_matrix_do_not_intercept(self):
        self.step(2, matrix=2, away=True)
        self.step(widow=FIRE, matrix=2, away=True)
        self.assertEqual(self.health(self.b), 592)
        self.b.team = self.a.team
        self.assertIsNone(self.run.match.combat._matrix_hit(
            self.a, self.run.match.combat._eye(self.a), (0, 0, 1), 30, 0x040000000000004C))

    def test_melee_is_not_intercepted(self):
        self.b.mover.position[:] = (0, 0, 1.5)
        self.b.mover.body = None
        self.b.mover.history.clear()
        self.run.match.combat.history.pop(self.b.body, None)
        self.step(2, matrix=2)
        self.step(widow=MELEE, matrix=2)
        self.step(30, matrix=2)
        self.assertEqual(self.health(self.b), 575)

    def test_mine_flight_absorbed_without_deployment_or_poison(self):
        self.b.mover.position[:] = (0, 0, 7)
        self.b.mover.body = None
        self.step(2, matrix=2)
        fight = self.run.match.combat
        mines = fight.venom
        with patch.object(mines, "throw", wraps=mines.throw) as thrown:
            self.step(widow=16, matrix=2)
            self.step(65, matrix=2)
            self.assertEqual(thrown.call_count, 1)
        self.assertEqual(mines.flights, [])
        self.assertEqual(mines.mines, [])
        self.assertEqual(mines.poison, [])
        self.assertEqual(self.health(self.b), 600)

    def test_wall_before_matrix_keeps_mine_attachment(self):
        self.run.match.collision_world = World([
            (-10, -10, 2, 10, -10, 2, 10, 10, 2),
            (-10, -10, 2, 10, 10, 2, -10, 10, 2),
        ])
        self.step(2, matrix=2)
        self.step(widow=16, matrix=2)
        self.step(30, matrix=2)
        mines = self.run.match.combat.venom
        self.assertEqual(mines.flights, [])
        self.assertEqual(len(mines.mines), 1)
        self.assertLess(mines.mines[0].position[2], 2)
        self.assertEqual(mines.poison, [])

    def test_dead_mech_and_unknown_projectile_cannot_intercept(self):
        self.step(2, matrix=2)
        fight = self.run.match.combat
        origin = fight._eye(self.a)
        self.assertIsNotNone(fight._matrix_hit(self.a, origin, (0, 0, 1), 30,
                                              0x040000000000004C))
        self.assertIsNone(fight._matrix_hit(self.a, origin, (0, 0, 1), 30, None))
        fight.vitals_of(self.target(self.b)).died_at = 0.0
        self.assertIsNone(fight._matrix_hit(self.a, origin, (0, 0, 1), 30,
                                           0x040000000000004C))


class ProjectileCatalogTests(unittest.TestCase):
    def test_missing_catalog_rejects_admission(self):
        self.assertFalse(deflection.ProjectileTags(None).absorbable(0x040000000000004C))

    def test_catalog_requires_immutable_actual_bytes(self):
        with self.assertRaises(ValueError):
            deflection.ProjectileTags(bytearray())


class MatrixCatalogSnapshotTests(unittest.TestCase):
    def test_actual_catalog_bytes_are_stored_and_sensitive_without_file_reads(self):
        import test_server_snapshot as snapshots

        from ow174.game.server_snapshot import snapshot_digest

        case = snapshots.ServerSnapshotTests()
        session, _, (player,) = case.scene()
        fight = player.match.combat
        data = {"version": 1, "build": deflection.BUILD,
                "entities": {"0x040000000000004C": []}}
        fight.projectile_catalog = deflection.ProjectileTags(json.dumps(data).encode())
        first = snapshot_digest(case.capture(session))
        data["entities"]["0x040000000000004C"] = ["0x0D60000000000145"]
        fight.projectile_catalog = deflection.ProjectileTags(json.dumps(data).encode())
        original = Path.read_bytes

        def guarded_read(path):
            if path == deflection.TAGS_PATH:
                raise AssertionError("snapshot reread projectile catalog")
            return original(path)

        with patch("pathlib.Path.read_bytes", guarded_read):
            second = snapshot_digest(case.capture(session))
        self.assertNotEqual(first, second)
