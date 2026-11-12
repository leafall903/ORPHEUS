"""Accepted-force body epochs are actual observations, without a new input lease."""
import unittest
from test_game_combat import Run, KANEZAKA_DM
from ow174.game.commands import Command, FIRE
from ow174.game.content import heroes, SOLDIER


class ForceEpochTests(unittest.TestCase):
    def scene(self):
        run = Run(KANEZAKA_DM, ('Target',))
        player = run.players[0]
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        self.assertTrue(run.match.switch_hero(player, heroes()[SOLDIER], start_frame=run.frame + 1000))
        run.match.update(run.frame * .016, run.frame)
        return run, player, run.match.combat

    def test_real_constructor_burst_sparse_and_replay_keep_separate_epochs(self):
        run, player, fight = self.scene()
        script = player.body_script
        bank = fight.knockbacks
        initial = (run.match.tick, script.frame)
        self.assertNotEqual(*initial)
        self.assertEqual(bank.body_epochs[player.body], (script, initial, None))
        self.assertIsNone(script.world_frame_anchor)
        # Several actually received poses in one world tick change the observed
        # input-clock phase, rather than inventing an equal-epoch projection.
        player.take_commands([Command(script.frame + 1), Command(script.frame + 2)])
        observed = (run.match.tick, script.frame)
        self.assertEqual(bank.body_epochs[player.body], (script, initial, observed))
        self.assertEqual(bank.body_frame(player, run.match.tick + 1, .016), observed[1] + 1)
        before = tuple(player.mover.position)
        fight._apply_pharah_impulses(((fight.player_target(player), (0, 6, -4)),))
        run.frame += 5
        run.match.update(run.frame * .016, run.frame, frames=False)
        self.assertLess(player.mover.position[2], before[2])
        self.assertEqual(script.frame, observed[1] + 4)  # Endpoint remains for actual input.
        self.assertEqual(bank.body_epochs[player.body], (script, initial, observed))
        self.assertIsNone(script.world_frame_anchor)
        # Already replayed input cannot move or fire a second time.
        position = tuple(player.mover.position)
        rockets = len(fight.rockets)
        player.take_commands([Command(script.frame, buttons=FIRE)])
        self.assertEqual(tuple(player.mover.position), position)
        self.assertEqual(len(fight.rockets), rockets)
        self.assertEqual(bank.body_epochs[player.body], (script, initial, observed))
        player.take_commands([Command(script.frame + 1)])
        self.assertEqual(bank.body_epochs[player.body][2], (run.match.tick, script.frame))
        self.assertIsNone(script.world_frame_anchor)

    def test_actual_body_replacement_retires_old_observations_and_replay_receipts(self):
        run, player, fight = self.scene()
        old = player.body_script
        entity = player.body
        fight.knockbacks.replayed_frames[entity] = (old, old.frame + 4)
        fight._apply_pharah_impulses(((fight.player_target(player), (0, 6, -4)),))
        self.assertTrue(run.match.switch_hero(player, heroes()[0x02E0000000000029], start_frame=4000))
        self.assertNotEqual(player.body, entity)
        self.assertNotIn(entity, fight.knockbacks.body_epochs)
        self.assertNotIn(entity, fight.knockbacks.replayed_frames)
        self.assertNotIn(entity, fight.knockbacks.launches)
        self.assertEqual(fight.knockbacks.body_epochs[player.body],
                         (player.body_script, (run.match.tick, 4000), None))
        self.assertFalse(fight.knockbacks.replayed_pose(player, 4001))

class ForceEpochStorageTests(unittest.TestCase):
    def test_real_server_stores_constructor_pose_and_completed_replay_receipts(self):
        import test_server_snapshot as stored
        from ow174.game.server_snapshot import snapshot_digest
        case = stored.ServerSnapshotTests()
        session, _, players = case.scene(two=True)
        player = players[1]
        bank = player.match.combat.knockbacks
        row = bank.body_epochs[player.body]
        self.assertIs(row[0], player.body_script)
        self.assertIsNotNone(row[1])
        def digest():
            return snapshot_digest(case.capture(session))
        baseline = digest()
        bank.body_epochs[player.body] = (row[0], (row[1][0] + 1, row[1][1]), row[2])
        self.assertNotEqual(digest(), baseline)
        bank.body_epochs[player.body] = row
        self.assertEqual(digest(), baseline)
        bank.replayed_frames[player.body] = (player.body_script, player.body_script.frame)
        replayed = digest()
        self.assertNotEqual(replayed, baseline)
        self.assertTrue(bank.replayed_pose(player, player.body_script.frame))
        bank.replayed_frames[player.body] = (player.body_script, player.body_script.frame + 1)
        self.assertNotEqual(digest(), replayed)
        bank.replayed_frames.pop(player.body)
        self.assertEqual(digest(), baseline)
