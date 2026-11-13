"""Actual grapple entry geometry; selected host pose, not native map acceptance."""

import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from test_widowmaker_combat import WidowmakerGrappleTests

from ow174.game.script import expr
from ow174.game.script.nodes import MoveTo

FIXTURES = Path(os.environ.get(
    "ORPHEUS_BEHAVIOR_FIXTURES", Path(__file__).resolve().parents[2] / "fixtures/behavior",
))


class WidowmakerGrappleEntryGeometryTests(unittest.TestCase):
    def test_actual_cast_initializes_vectors_then_uses_one_existing_move_to(self):
        case = json.loads((FIXTURES / "widowmaker-grapple-entry-geometry-v1.json").read_text())
        cfg, expected = case["input"], case["expected"]
        scene = WidowmakerGrappleTests()
        scene.setUp()
        scene.wall(cfg["wall_z"])
        scene.a.mover.position[:] = cfg["body_position"]
        scene.a.mover.body = None
        fight = scene.run.match.combat
        initial, moves = [], []
        cast = fight.grapple_cast
        begin_move = MoveTo.on_begin

        def capture_cast(state):
            result = cast(state)
            initial.append(tuple(state.instance.vars[i].value() for i in (431, 432, 433)))
            return result

        def capture_move(state):
            begin_move(state)
            if state.instance.graph.index == 0x5A:
                moves.append(state.movement)

        with patch.object(fight, "grapple_cast", capture_cast), \
                patch.object(MoveTo, "on_begin", capture_move), \
                patch.object(fight.knockbacks, "apply", wraps=fight.knockbacks.apply) as impulse:
            for offset in range(cfg["frames"]):
                scene.run.step({scene.a: (0, 0, cfg["button"] if offset == 0 else 0)})
        origin, direction = expr.Vec3(*expected["origin"]), expr.Vec3(*expected["direction"])
        self.assertEqual(initial, [(origin, origin, direction)])
        self.assertEqual(len(moves), expected["move_to_entries"])
        self.assertEqual(moves[0], (expr.Vec3(*expected["contact"]), expected["pull_speed"]))
        self.assertEqual(scene.hook().vars[1220].value(), expr.Vec3(*expected["contact"]))
        self.assertEqual(scene.hook().vars[434].value(), expr.Vec3(*expected["normal"]))
        impulse.assert_not_called()
