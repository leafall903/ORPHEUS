"""Declared original023A scope; actual Player input/phase, not natural trigger proof."""
import math
import unittest

import test_game_ana as ana_fixture

from ow174.game.commands import Command
from ow174.game.content import find_hero
from ow174.game.script import expr, graph


class AnaThrottleScopeTests(unittest.TestCase):
    def arena(self, hero='Ana'):
        run, actor, _enemy, _entry = ana_fixture.AnaRifleTests().setup_fight(enemy=True)
        if hero != 'Ana':
            self.assertTrue(run.match.switch_hero(actor, find_hero(hero)))
            for _ in range(25):
                run.step({})
        return run, actor

    def scope(self, actor, fraction=0):
        definition = graph.graph(0x23A)
        instance = actor.body_script.component.create(
            definition, values={(expr.INSTANCE, 2101): fraction}, init=False)
        # This is a declared original-state host fixture. It deliberately does
        # not claim to restore023A's animation/foreign effect/start predicates.
        state = instance.node_state(next(n for n in definition.nodes if n.pos == 2))
        state.begin()
        return instance, state

    @staticmethod
    def step(run, actor, forward=0, right=0):
        run.frame += 1
        run.match.update(run.frame * .016, run.frame)
        for player in run.players:
            player.take_commands([Command(run.frame, forward=forward if player is actor else 0,
                                           right=right if player is actor else 0)])

    def test_original_zero_rows_block_four_actual_input_directions_on_both_bodies(self):
        for hero in ('Ana', 'Zarya'):
            for forward, right in ((127, 0), (-127, 0), (0, 127), (0, -127)):
                with self.subTest(hero=hero, forward=forward, right=right):
                    run, actor = self.arena(hero)
                    self.scope(actor)
                    self.step(run, actor)
                    before = tuple(actor.mover.position)
                    self.step(run, actor, forward, right)
                    self.assertEqual(tuple(actor.mover.position), before)
                    self.assertEqual(actor.mover.velocity, (0, 0, 0))
                    self.assertEqual(actor.mover.throttles, (right, forward))
                    self.assertIsNone(actor.body_script.component.fault)

    def test_scope_end_restores_input_on_following_frame_and_keeps_raw_bytes(self):
        for hero in ('Ana', 'Zarya'):
            with self.subTest(hero=hero):
                run, actor = self.arena(hero)
                instance, state = self.scope(actor)
                self.step(run, actor)
                before = tuple(actor.mover.position)
                self.step(run, actor, 127, -127)
                self.assertEqual(tuple(actor.mover.position), before)
                keys = [(instance.id, state.index, slot) for slot in range(3)]
                self.assertEqual([actor.mover.mods[k].start for k in keys], [run.frame] * 3)
                state.end(False)
                self.step(run, actor, 127, -127)
                self.assertEqual(tuple(actor.mover.position), before)
                self.assertEqual([actor.mover.mods[k].end for k in keys], [run.frame + 1] * 3)
                self.assertEqual(actor.mover.input_throttles, (-127, 127))
                self.step(run, actor, 127, -127)
                self.assertGreater(math.dist(actor.mover.position, before), 0)
                self.assertFalse(any(k in actor.mover.mods for k in keys))
                self.assertEqual(actor.mover.throttles, (-127, 127))

    def test_scope_begin_keeps_current_frame_then_restricts_next_frame_acceleration(self):
        for hero in ('Ana', 'Zarya'):
            with self.subTest(hero=hero):
                run, actor = self.arena(hero)
                baseline, control = self.arena(hero)
                instance, state = self.scope(actor)
                self.step(run, actor, 127)
                self.step(baseline, control, 127)
                self.assertEqual(actor.mover.velocity, control.mover.velocity)
                self.assertGreater(actor.mover.velocity[2], 0)
                self.assertEqual(actor.mover.mods[(instance.id, state.index, 0)].start, run.frame + 1)
                self.step(run, actor, 127)
                self.step(baseline, control, 127)
                self.assertLess(actor.mover.velocity[2], control.mover.velocity[2])

    def test_fractional_rows_keep_lateral_sign_and_limit_actual_acceleration(self):
        for hero in ('Ana', 'Zarya'):
            for right in (-127, 127):
                with self.subTest(hero=hero, right=right):
                    run, actor = self.arena(hero)
                    baseline, control = self.arena(hero)
                    self.scope(actor, .25)
                    self.step(run, actor)
                    self.step(baseline, control)
                    self.step(run, actor, right=right)
                    self.step(baseline, control, right=right)
                    vx, _, _ = actor.mover.velocity
                    self.assertGreater(abs(vx), 0)
                    self.assertLess(abs(vx), abs(control.mover.velocity[0]))
                    self.assertLess(vx * right, 0)  # positive raw right uses negative world X at yaw0
                    self.assertEqual(actor.mover.throttles, (right, 0))

    def test_body_replacement_does_not_inherit_the_old_input_scope(self):
        for hero in ('Ana', 'Zarya'):
            with self.subTest(hero=hero):
                run, actor = self.arena(hero)
                instance, state = self.scope(actor)
                self.step(run, actor)
                self.step(run, actor, 127)
                old = actor.mover
                self.assertIn((instance.id, state.index, 0), old.mods)
                self.assertTrue(run.match.switch_hero(actor, actor.hero))
                self.assertIsNot(actor.mover, old)
                self.assertEqual(actor.mover.mods, {})
                before = tuple(actor.mover.position)
                self.step(run, actor, 127)
                self.assertGreater(math.dist(actor.mover.position, before), 0)


if __name__ == '__main__':
    unittest.main()
