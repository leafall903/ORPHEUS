"""Actual McCree Roll inputs verify Echo lane's shared original-age press buffer."""
import unittest

from test_game_combat import Run

from ow174.game.content import heroes
from ow174.game.script import nodes


class EchoSharedCooldownTests(unittest.TestCase):
    def test_authored_roll_buffer_expires_early_accepts_near_end_and_preserves_held_input(self):
        for mode in ('early', 'near', 'held'):
            with self.subTest(mode=mode):
                run = Run()
                run.match.bots.clear()
                run.match._pending_bot_scripts.clear()
                player = run.players[0]
                run.match.switch_hero(player, heroes()[0x02E0000000000042])
                for _ in range(25):
                    run.step({})
                instance = next(i for i in player.body_script.component.instances.values()
                                if i.graph.index == 0x1F0)
                ability = next(s for s in instance.states.values() if s.node.pos == 2)
                self.assertAlmostEqual(ability.evaluate(nodes._field(ability, 'm_246438AD')), .15)
                run.step({player: (0, 0, 8)})
                self.assertTrue(ability.flags & nodes.Ability.RUNNING)
                for _ in range(40):
                    run.step({})
                self.assertFalse(ability.flags & nodes.Ability.RUNNING)
                self.assertGreater(ability.remaining(), 2)
                if mode == 'near':
                    while ability.remaining() > .08:
                        run.step({})
                run.step({player: (0, 0, 8)})
                self.assertFalse(ability.flags & nodes.Ability.RUNNING)
                for _ in range(450):
                    run.step({player: (0, 0, 8 if mode == 'held' else 0)})
                    if ability.flags & nodes.Ability.RUNNING or ability.remaining() <= 0:
                        break
                for _ in range(2):
                    run.step({player: (0, 0, 8 if mode == 'held' else 0)})
                if mode == 'early':
                    self.assertFalse(ability.flags & nodes.Ability.RUNNING)
                    run.step({player: (0, 0, 8)})
                self.assertTrue(ability.flags & nodes.Ability.RUNNING)
