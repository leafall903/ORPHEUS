"""A missing pose cannot be a second physical sample for Piledriver's monitor."""

import unittest
from unittest.mock import patch

import test_ball_skill_force_world_tick as clocks
import test_delivered_wrecking_ball_combat as scenarios
import test_delivered_wrecking_ball_piledriver as pile

from ow174.game import ball
from ow174.game.commands import Command


class PiledriverPoseSampleTests(unittest.TestCase):
    def cast(self, shifted=False):
        run, player, enemy, _, _ = scenarios.BallCombatTests().scenario(distance=1.5)
        if shifted:
            clocks.shifted_commands(run, player, enemy)
        target = next(target for target in run.match.combat._targets() if target.owner is enemy)
        pile.lift(run, player)
        run.step({player: (0, 0, 128)})
        return run, player, enemy, target

    def gap(self, run, player, enemy, frames):
        run.frame += frames
        run.match.update(run.frame * .016, run.frame)
        for actor in (player, enemy):
            actor.take_commands([Command(actor.body_script.frame + frames)])

    def test_gap_then_real_poses_preserve_descent_and_hit_once_in_each_owner_epoch(self):
        for shifted in (False, True):
            with self.subTest(shifted=shifted):
                run, player, enemy, target = self.cast(shifted)
                fight = run.match.combat
                events = []
                original = fight.knockbacks.apply

                def observe(victim, impulse, *, original=original, events=events, **kwargs):
                    accepted = original(victim, impulse, **kwargs)
                    events.append((victim.entity, tuple(impulse), accepted, kwargs))
                    return accepted

                with patch.object(fight.knockbacks, 'apply', observe):
                    self.gap(run, player, enemy, 70)
                    instance = ball.instance(player)
                    self.assertTrue(player.mover.airborne)
                    self.assertTrue(instance.states[3].active)
                    self.assertFalse(instance.vars[1949].value())
                    self.assertEqual(instance.states.get(7).counter if 7 in instance.states else 0, 0)
                    self.assertEqual(fight.vitals_of(target).total, 200)
                    self.assertFalse(events)
                    for _ in range(30):
                        run.step({})
                        if events:
                            break
                    self.assertEqual(fight.vitals_of(target).total, 100)
                    self.assertFalse(player.mover.airborne)
                    self.assertEqual(len(events), 1)
                    entity, _, accepted, kwargs = events[0]
                    self.assertEqual(entity, enemy.body)
                    self.assertTrue(accepted)
                    self.assertEqual(kwargs['source_entity'], player.body)
                    self.assertEqual(kwargs['impact_tick'], run.match.tick)
                    self.assertEqual(instance.states[7].counter, 1)
                    for _ in range(40):
                        run.step({})
                    self.assertEqual(fight.vitals_of(target).total, 100)
                    self.assertEqual(len(events), 1)
                    retired = player.body
                    run.match.switch_hero(player, player.hero)
                    self.assertNotEqual(player.body, retired)
                    self.assertFalse(ball.defer_piledriver_motion_sample(instance, instance.graph.node(85)))
                    run.step({})
                    self.assertEqual(len(events), 1)

    def test_original_five_second_safety_timeout_still_completes_once(self):
        run, player, enemy, target = self.cast()
        fight = run.match.combat
        with patch.object(fight.knockbacks, 'apply', wraps=fight.knockbacks.apply) as force:
            self.gap(run, player, enemy, 400)
            instance = ball.instance(player)
            safety = instance.states[36]
            self.assertTrue(safety.active)
            self.assertEqual(safety.ms, 5000)
            frame_ms = instance.component.frame_ms
            through = (safety.start + safety.ms + frame_ms - 1) // frame_ms
            self.gap(run, player, enemy, through - player.body_script.frame + 1)
            self.assertFalse(instance.states[3].active)
            self.assertEqual(instance.states[36].counter, 1)
            self.assertFalse(instance.states[36].active)
            self.assertFalse(instance.vars[1949].value())
            # The unchanged safety plug may run its authored search. It must
            # complete once, using this body and the existing hit producer.
            self.assertEqual(instance.states[7].counter, 1)
            force.assert_called_once()
            self.assertEqual(force.call_args.kwargs['source_entity'], player.body)
            health = fight.vitals_of(target).total
            for _ in range(40):
                run.step({})
            self.assertEqual(fight.vitals_of(target).total, health)
            force.assert_called_once()
