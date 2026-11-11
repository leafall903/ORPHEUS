"""Actual1177 movement data projected to its bound possessed body (host policy)."""

import unittest

import test_game_winston_assassin as fixtures

from ow174.game import mover
from ow174.game.commands import Command
from ow174.game.physics_snapshot import snapshot_mover


class AssassinMovementTests(unittest.TestCase):
    setUpClass = classmethod(fixtures.AssassinBoundaryTests.setUpClass.__func__)
    clear_graphs = classmethod(fixtures.AssassinBoundaryTests.clear_graphs.__func__)
    restore_graphs = classmethod(fixtures.AssassinBoundaryTests.restore_graphs.__func__)
    scene = fixtures.AssassinBoundaryTests.scene

    def step(self, run, bot, script, *, pressed=True):
        run.frame += 1
        run.match.update(run.frame * .016, run.frame)
        script.advance_live(run.frame, bot.position, ability_input=(36, pressed))

    def acquire(self, *, primal=False):
        run, match, player, bot, script, _root, _behavior = self.scene()
        if primal:
            player.body_script.set_ult_charge(player.body_script.component.entity_var(338).value())
            run.step({player: (0, 0, 32)})
        script.set_manual_target(player.entity)
        self.step(run, bot, script)
        self.assertTrue(match.combat.assassins.controlled(player))
        return run, match, player, bot, script

    def test_actual_controller_records_reach_next_body_pose_without_input(self):
        _run, _match, player, _bot, _script = self.acquire()
        frame = player.body_script.frame
        mods = [m for m in player.mover.mods.values() if len(m.key) == 4]
        self.assertEqual({m.index for m in mods}, {17, 18, 19, 20, 21, 22})
        self.assertTrue(all(m.key[0] == player.entity for m in mods))
        self.assertTrue(all(not m.acts(frame) and m.acts(frame + 1) for m in mods))
        ordered = sorted(mods, key=mover.Mod.order)
        values = mover.modded(player.mover.data, ordered)
        self.assertEqual(mover.intent(ordered, mover.JUMP_INTENT, 1), 0)
        self.assertEqual(mover.intent(ordered, mover.CROUCH_INTENT, 1), 0)
        self.assertFalse(values.character_collision)
        for right, forward in ((1, 1), (-1, -1), (1, -1), (-1, 1)):
            self.assertEqual(mover.modified_throttles(ordered, right, forward), (0, 0))

    def test_world_release_restores_first_input_pose_and_preserves_body_primal_mods(self):
        run, match, player, bot, script = self.acquire(primal=True)
        body_mods = {key: mod for key, mod in player.mover.mods.items() if len(key) == 3}
        self.assertTrue(body_mods)
        # The player supplies no commands during acquisition or release.
        self.step(run, bot, script, pressed=False)
        self.assertFalse(match.combat.assassins.controlled(player))
        frame = player.body_script.frame + 1
        self.assertTrue(all(not mod.acts(frame) for key, mod in player.mover.mods.items()
                            if len(key) == 4))
        self.assertTrue(all(player.mover.mods.get(key) is mod for key, mod in body_mods.items()))
        before = tuple(player.mover.position)
        player.take_commands([Command(frame, forward=127)])
        self.assertGreater(player.mover.position[2], before[2])
        self.assertGreater(player.mover.values.run_forward, player.mover.data.run_forward)
        self.assertGreater(player.mover.values.jump_speed, 0)

    def test_projected_mod_values_and_controller_namespace_are_causal_snapshot_data(self):
        _run, _match, player, _bot, _script = self.acquire()
        key, mod = next((k, m) for k, m in player.mover.mods.items() if len(k) == 4)
        before = snapshot_mover(player.mover)
        value = mod.value
        mod.value = .5
        self.assertNotEqual(snapshot_mover(player.mover), before)
        mod.value = value
        self.assertEqual(snapshot_mover(player.mover), before)
        mod.key = key[1:]
        self.assertNotEqual(snapshot_mover(player.mover), before)


if __name__ == '__main__':
    unittest.main()
