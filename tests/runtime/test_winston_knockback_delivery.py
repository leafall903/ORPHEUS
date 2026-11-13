"""Winston force arithmetic against native slices, and single production dispatch."""

import json
import unittest
from pathlib import Path
from unittest import mock

import test_delivered_winston as tesla
import test_game_winston_primal as primal

from ow174.game import combat


class WinstonKnockbackTests(unittest.TestCase):
    def test_actual_authored_rows_match_native_force_cases(self):
        path = Path(__file__).resolve().parents[2] / "fixtures/behavior/winston-knockback-174-v1.json"
        contract = json.loads(path.read_text(encoding="utf-8"))
        for row in contract["cases"]:
            with self.subTest(graph=row["graph"], state=row["state"]):
                fixture = primal.PrimalTests()
                fixture.setUp()
                fixture.rage()
                fixture.owner.mover.position[:] = (0, 1, 0)
                fixture.enemy.mover.position[:] = row["target_position"]
                fixture.enemy.mover.velocity = tuple(row["target_velocity"])
                target = next(t for t in fixture.fight._targets() if t.owner is fixture.enemy)
                instance = next(i for i in fixture.owner.body_script.component.instances.values()
                                if i.graph.index == int(row["graph"], 16))
                state = instance.state(row["state"])
                with mock.patch.object(fixture.fight.knockbacks, "apply",
                                       wraps=fixture.fight.knockbacks.apply) as apply:
                    fixture.fight.winston_jump.knockback(fixture.owner, target, state, row["damage_scalar"])
                self.assertEqual(tuple(apply.call_args.args[1]), tuple(row["expected_impulse"]))
                self.assertEqual(fixture.fight.knockbacks.launch_sources[target.entity], fixture.owner.body)

    def test_real_primal_contact_registers_one_foreign_launch(self):
        fixture = primal.PrimalTests()
        fixture.setUp()
        fixture.rage()
        before = fixture.fight.knockbacks.serial
        fixture.first_hit()
        self.assertEqual(fixture.health(fixture.enemy), 160)
        self.assertEqual(fixture.fight.knockbacks.serial, before + 1)
        self.assertEqual(fixture.fight.knockbacks.launch_sources[fixture.enemy.body], fixture.owner.body)

    def test_real_quick_melee_keeps_one_force_dispatch_and_damage(self):
        fixture = tesla.TeslaTests()
        fixture.setUp()
        fixture.winston.mover.position[:] = (0, 1, 0)
        fixture.enemy.mover.position[:] = (0, 1, 1.5)
        fixture.ally.mover.position[:] = (0, 1, 1.5)
        before = fixture.fight.knockbacks.serial
        for _ in range(30):
            fixture.step(0x800)
            if fixture.health(fixture.enemy) < 200:
                break
        self.assertEqual(fixture.health(fixture.enemy), 170)
        self.assertEqual(fixture.health(fixture.ally), 200)
        self.assertEqual(fixture.fight.knockbacks.serial, before + 1)
        self.assertEqual(len(fixture.winston.client.messages(combat.HIT)), 1)
