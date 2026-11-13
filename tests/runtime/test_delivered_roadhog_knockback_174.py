"""Original 0043 Roadhog melee force admission; native adoption is separate."""

import unittest
import json
from pathlib import Path
from unittest.mock import patch

import test_delivered_roadhog_hook as fixtures
from ow174.game import combat
from ow174.game import whole_hog
from ow174.game.script import expr


class RoadhogKnockback174Tests(unittest.TestCase):
    def test_unresolved_whole_hog_force_is_not_silently_zero(self):
        run, hog, _, _, _ = fixtures.HookTests().session()
        weapon = next(i for i in hog.body_script.component.instances.values() if i.graph.index == 0x01C6)
        state = weapon.state(weapon.graph.node(33).state)
        modify = state.node.field("m_6396149F.m_modifyHealth")
        with patch.object(state, "evaluate", return_value=None):
            with self.assertRaisesRegex(ValueError, "unresolved Whole Hog"):
                whole_hog.evaluate(state, modify)

    def test_melee_submits_the_authored_flat_ring_once(self):
        contract = json.loads((Path(__file__).resolve().parents[2] / "fixtures" / "behavior"
                               / "roadhog-melee-force-v1.json").read_text(encoding="utf-8"))
        run, hog, target, _, pool = fixtures.HookTests().session(distance=contract["input"]["target_distance"])
        fight = run.match.combat
        start = tuple(target.mover.position)
        with patch.object(fight.knockbacks, "apply", wraps=fight.knockbacks.apply) as apply:
            run.step({hog: (0, 0, 0x800)})
            for _ in range(25):
                run.step({})
        apply.assert_called_once()
        self.assertIs(apply.call_args.args[0].owner, target)
        self.assertEqual(apply.call_args.args[1], expr.Vec3(*contract["expected"]["shared_helper_request"]))
        self.assertEqual(pool.total, contract["expected"]["target_health_after_hit"])
        self.assertEqual(len(hog.client.messages(combat.HIT)), contract["expected"]["hit_messages"])
        self.assertGreater(target.mover.position[2], start[2])

    def test_missed_melee_does_not_submit_force(self):
        run, hog, _, _, pool = fixtures.HookTests().session(distance=8)
        with patch.object(run.match.combat.knockbacks, "apply", wraps=run.match.combat.knockbacks.apply) as apply:
            run.step({hog: (0, 0, 0x800)})
            for _ in range(25):
                run.step({})
        apply.assert_not_called()
        self.assertEqual(pool.total, 200)
