"""Original accepted Pharah world replay through the same Player/Mover host."""
import unittest

import test_pharah_combat as primary
from unittest.mock import patch
from ow174.game import combat, pharah
from ow174.game.commands import Command
from ow174.game.content import SOLDIER, heroes


class PharahReplayTests(unittest.TestCase):
    def launch(self):
        run, player, enemy = primary.PharahCombatTests().encounter()
        enemy.mover.position[0] = 40
        enemy.mover.body = None
        player.body_script.set_ult_charge(player.body_script.component.entity_var(338).value())
        floor = player.mover.position[1]
        run.step({player: (0, 0, 8)})
        for _ in range(7):
            run.step({})
        run.step({player: (0, 0, 32)})
        self.assertIn(player.body, run.match.combat.pharah_airborne)
        self.assertGreater(player.mover.position[1], floor + 2)
        return run, player, floor

    @staticmethod
    def advance(run, player, frames, gap):
        if gap:
            run.frame += frames
            run.match.update(run.frame * combat.TICK, run.frame, frames=False)
            player.take_commands([Command(run.frame)])
        else:
            for _ in range(frames):
                run.step({})

    def test_original_airborne_barrage_locks_falls_and_lands_at_matching_poses(self):
        samples = []
        for gap in (False, True):
            run, player, floor = self.launch()
            fight = run.match.combat
            start = tuple(player.mover.position)
            self.advance(run, player, 70, gap)
            self.assertEqual(tuple(player.mover.position), start)
            self.assertEqual(player.mover.velocity, (0, 0, 0))
            self.advance(run, player, 103, gap)
            self.assertIsNone(pharah.barrage_channel(player.body_script.component))
            self.assertGreater(player.mover.position[1], floor)
            self.assertLess(player.mover.position[1], start[1])
            self.assertLess(player.mover.velocity[1], 0)
            samples.append((tuple(player.mover.position), player.mover.velocity))
            self.advance(run, player, 60, gap)
            self.assertEqual(tuple(player.mover.position), (start[0], floor, start[2]))
            self.assertNotIn(player.body, fight.pharah_airborne)
        self.assertEqual(samples[0], samples[1])

    def test_body_replacement_retires_actual_tracking(self):
        run, player, _ = self.launch()
        old = player.body
        self.assertTrue(run.match.switch_hero(player, heroes()[SOLDIER]))
        self.assertNotIn(old, run.match.combat.pharah_airborne)
        self.assertNotIn(player.body, run.match.combat.pharah_airborne)

    def test_actual_unequal_birth_epoch_and_recorded_shape_keep_current_clocks(self):
        run, player, _ = primary.PharahCombatTests().encounter()
        self.assertTrue(run.match.switch_hero(player, player.hero, start_frame=run.frame + 1000))
        script = player.body_script
        for _ in range(20):
            run.frame += 1
            run.match.update(run.frame * combat.TICK, run.frame)
            player.take_commands([Command(script.frame + 1, buttons=64)])
        fight = run.match.combat
        self.assertIsNotNone(pharah.hover_force(script))
        self.assertIn(player.body, fight.pharah_airborne)
        self.assertGreater(script.frame, fight.tick + 500)
        history = fight.history[player.body]
        _, feet, *shape = history[-1]
        self.assertGreaterEqual(len(shape), 2)
        history[-1] = (fight.tick + 3, feet, *shape)
        clocks = run.match.tick, fight.tick, fight.now
        frame = script.frame
        pharah.replay(fight, fight.tick + 3, combat.TICK)
        self.assertEqual(script.frame, frame + 3)
        self.assertEqual((run.match.tick, fight.tick, fight.now), clocks)
        self.assertEqual(history[-1][2:], tuple(shape))


    def test_original_accepted_barrage_report_count_and_curves_match_world_gap(self):
        for duration, expected in ((125, 61), (240, 76)):
            samples = []
            for gap in (False, True):
                run, player, enemy = primary.PharahCombatTests().encounter()
                fight = run.match.combat
                enemy.mover.position[0] = 40
                enemy.mover.body = None
                player.body_script.set_ult_charge(player.body_script.component.entity_var(338).value())
                with patch.object(fight, '_shoot', wraps=fight._shoot) as shots:
                    run.step({player: (0, 0, 32)})
                    self.advance(run, player, duration, gap)
                    self.assertEqual(shots.call_count, expected)
                samples.append(tuple((r.position, r.flown, r.range_left) for r in fight.rockets))
                if duration == 240:
                    self.assertIsNone(pharah.barrage_channel(player.body_script.component))
            self.assertEqual(samples[0], samples[1])
