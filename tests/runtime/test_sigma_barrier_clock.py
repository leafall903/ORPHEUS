"""The world-owned barrier's authored timer uses world time at admission."""

import json
import unittest
from pathlib import Path

import test_sigma_combat as sigma_fixtures

from ow174.game import combat
from ow174.game.commands import Command
from ow174.game.content import heroes


class SigmaBarrierClockTests(unittest.TestCase):
    def test_barrier_admitted_during_body_birth_keeps_its_two_second_wait(self):
        fixture = json.loads((Path(__file__).resolve().parents[2] / "fixtures" / "behavior"
                              / "sigma-barrier-world-clock-v1.json").read_text())
        checkpoints = fixture["expected"]["wait_active_at_world_steps"]
        run, owner, enemy, _ = sigma_fixtures.SigmaCombatTests().arena()
        owner.quanta = 32000
        run.match.switch_hero(owner, heroes()[sigma_fixtures.SIGMA], start_frame=9000)
        owner.commands.reset(last_frame=9000)
        actor, = (a for a in run.match.combat.sigma_barriers.values() if a.owner is owner)
        wait = actor.child.node_state(actor.child.graph.node(45))
        self.assertTrue(wait.active)
        for step in range(1, 131):
            run.frame += 1
            run.match.update(run.frame * combat.TICK, run.frame)
            if step % 2 == 0:
                owner.take_commands([Command(9000 + step // 2)])
            enemy.take_commands([Command(run.frame)])
            if str(step) in checkpoints:
                # Original1A2A/node45 waits2s:1.44s of physical world time is not enough.
                self.assertEqual(wait.active, checkpoints[str(step)])
        self.assertFalse(wait.active)


if __name__ == "__main__":
    unittest.main()
