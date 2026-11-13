"""Ordinary Bash contacts submit one formed delta through the shared receiver."""

import json
import unittest
from pathlib import Path
from unittest.mock import patch

import test_game_brigitte as fixtures
import test_game_brigitte_shield as shields

from ow174.game import brigitte_force, defenses
from ow174.game.commands import FIRE, SECONDARY, Command
from ow174.game.content import heroes


class BashForceTests(unittest.TestCase):
    def test_original_fields_and_minimum_on_resolved_snapshot_exclude_state78(self):
        data = json.loads((Path(__file__).resolve().parents[2] /
                           "fixtures/behavior/brigitte-bash-force-v1.json").read_text())
        run, brig = fixtures.BrigitteTests().fixture()
        shields.BarrierTests().raise_shield(run, brig, 25)
        instance = next(i for i in brig.body_script.component.instances.values() if i.graph.index == 0x0EE3)
        spec = brigitte_force.evaluate_bash(instance.state(10))
        for field, expected in data["fields"].items():
            self.assertEqual(getattr(spec, field), expected)
        self.assertEqual(spec.rings, (tuple(data["ring"]),))
        for case in data["cases"]:
            self.assertEqual(tuple(spec.impulse(data["direction"], case["incoming"])),
                             tuple(case["formed_delta"]))
        self.assertIsNone(brigitte_force.evaluate_bash(instance.state(78)))

    def test_real_bash_uses_world_tick_and_one_canonical_force_across_owner_epochs(self):
        for birth in (1000, 9000):
            with self.subTest(birth=birth):
                case = fixtures.BrigitteTests()
                run, brig = case.fixture()
                enemy = run.players[1]
                brig.commands.last_frame = birth
                run.match.switch_hero(brig, brig.hero)
                x, y, z = brig.mover.position
                enemy.mover.position, enemy.mover.body = [x, y, z + 3], None

                def step(commands, *, run=run, brig=brig, birth=birth):
                    run.frame += 1
                    run.match.update(run.frame * .016, run.frame)
                    for player in run.players:
                        yaw, pitch, buttons = commands.get(player, (0, 0, 0))
                        frame = birth + run.frame - 1000 if player is brig else run.frame
                        player.take_commands([Command(frame, yaw=yaw, pitch=pitch, buttons=buttons)])

                run.step = step
                shields.BarrierTests().raise_shield(run, brig, 25)
                host = run.match.combat.knockbacks
                with patch.object(host, "apply", wraps=host.apply) as submit:
                    run.step({brig: (0, 0, SECONDARY | FIRE)})
                    for _ in range(20):
                        run.step({brig: (0, 0, SECONDARY)})
                self.assertEqual(submit.call_count, 1)
                self.assertEqual(submit.call_args.args[0].entity, enemy.body)
                self.assertEqual(tuple(submit.call_args.args[1]), (0, 1, 3))
                self.assertEqual(submit.call_args.kwargs,
                                 {"impact_tick": 1027, "source_entity": brig.body})
                self.assertEqual(host.serial, 1)
                self.assertEqual(run.match.combat.vitals_of(case.target(run, enemy)).total, 195)

    def test_real_reinhardt_response_is_applied_once_after_formed_delta(self):
        case = fixtures.BrigitteTests()
        run, brig = case.fixture()
        enemy = run.players[1]
        run.match.switch_hero(enemy, heroes()[0x02E0000000000007])
        shields.BarrierTests().raise_shield(run, brig, 30)
        self.assertAlmostEqual(defenses.knockback_scale(enemy), .7)
        x, y, z = brig.mover.position
        enemy.mover.position, enemy.mover.body = [x, y, z + 3], None
        host = run.match.combat.knockbacks
        with (
            patch.object(host, "apply", wraps=host.apply) as submit,
            patch.object(defenses, "knockback_scale", wraps=defenses.knockback_scale) as response,
        ):
            run.step({brig: (0, 0, SECONDARY | FIRE)})
            for _ in range(20):
                run.step({brig: (0, 0, SECONDARY)})
        self.assertEqual(submit.call_count, 1)
        self.assertEqual(response.call_count, 1)
        for actual, expected in zip(submit.call_args.args[1], (0, .7, 2.1), strict=True):
            self.assertAlmostEqual(actual, expected, places=6)
