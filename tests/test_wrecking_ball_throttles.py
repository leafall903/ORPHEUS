"""Actual Piledriver input restrictions, recovery and authored modifier boundaries.

The existing Piledriver hover/descent bridge remains reconstructed. Grounded
landing recovery exercises ordinary acceleration, independently of that bridge.
"""

import math
import unittest

import test_mccree_flashbang as flash_tests
import test_wrecking_ball_combat as ball_tests
from test_wrecking_ball_fireball import relocate
from test_wrecking_ball_piledriver import lift, pile_instance

from ow174.game import combat, mover
from ow174.game.commands import Command
from ow174.game.content import heroes


def step(run, player, buttons=0, *, forward=0, right=0):
    run.frame += 1
    run.match.update(run.frame * combat.TICK, run.frame)
    for actor in run.players:
        actor.take_commands([Command(run.frame, buttons=buttons if actor is player else 0,
                                     forward=forward if actor is player else 0,
                                     right=right if actor is player else 0)])


def restrictions(player):
    return {key: value for key, value in mover.statescript_mods(player.body_script.component).items()
            if value[0] in (17, 18, 19)}


class PiledriverThrottleTests(unittest.TestCase):
    def landing(self):
        run, player, _, _, _ = ball_tests.BallCombatTests().scenario()
        lift(run, player)
        step(run, player, 128)
        self.assertEqual({value[:3] for value in restrictions(player).values()},
                         {(17, mover.SET, 0), (18, mover.MULTIPLY, 0), (19, mover.MULTIPLY, 0)})
        for _ in range(120):
            step(run, player)
            if not player.mover.airborne:
                break
        self.assertFalse(player.mover.airborne)
        self.assertTrue(restrictions(player))
        self.assertTrue(pile_instance(player).states[24].flags & 1)
        return run, player

    def test_grounded_recovery_rejects_all_directions_then_restores_raw_input(self):
        for forward, right in ((127, 0), (-127, 0), (0, 127), (0, -127)):
            with self.subTest(forward=forward, right=right):
                run, player = self.landing()
                origin = tuple(player.mover.position)
                for _ in range(8):
                    step(run, player, forward=forward, right=right)
                    self.assertTrue(restrictions(player))
                    self.assertAlmostEqual(player.mover.position[0], origin[0], places=5)
                    self.assertAlmostEqual(player.mover.position[2], origin[2], places=5)
                    self.assertEqual(player.mover.throttles, (right, forward))
                    self.assertEqual(player.mover.history[-1].throttles, (right, forward))
                for _ in range(100):
                    step(run, player)
                    if not restrictions(player):
                        break
                self.assertFalse(restrictions(player))
                self.assertFalse(pile_instance(player).states[24].flags & 1)
                origin = tuple(player.mover.position)
                for _ in range(12):
                    step(run, player, forward=forward, right=right)
                self.assertGreater(math.dist(player.mover.position, origin), 0.3)

    def test_authored_scope_modifiers_start_and_end_on_the_following_frame(self):
        run, player, _, _, _ = ball_tests.BallCombatTests().scenario()
        lift(run, player)
        step(run, player, 128)
        keys = set(restrictions(player))
        self.assertEqual(len(keys), 3)
        for key in keys:
            mod = player.mover.mods[key]
            self.assertEqual(mod.start, run.frame + 1)
            self.assertFalse(mod.acts(run.frame))
            self.assertTrue(mod.acts(run.frame + 1))
        while restrictions(player):
            previous = dict(player.mover.mods)
            self.assertLess(run.frame, 1300)
            step(run, player)
        ended = [mod for key, mod in previous.items() if key in keys]
        self.assertEqual(len(ended), 3)
        for mod in ended:
            self.assertEqual(mod.end, run.frame + 1)
            self.assertTrue(mod.acts(run.frame))
            self.assertFalse(mod.acts(run.frame + 1))
        step(run, player)
        self.assertFalse(any(mod.index in (17, 18, 19) and mod.acts(run.frame)
                             for mod in player.mover.mods.values()))

    def test_body_replacement_during_recovery_does_not_retain_restrictions(self):
        run, player = self.landing()
        previous_body = player.body
        previous_mover = player.mover
        self.assertTrue(restrictions(player))
        self.assertTrue(run.match.switch_hero(player, heroes()[ball_tests.BALL]))
        self.assertNotEqual(player.body, previous_body)
        self.assertIsNot(player.mover, previous_mover)
        for _ in range(40):
            step(run, player)
        self.assertFalse(restrictions(player))
        origin = tuple(player.mover.position)
        for _ in range(12):
            step(run, player, forward=127)
        self.assertGreater(player.mover.position[2] - origin[2], 0.3)

    def test_actual_flashbang_interrupts_recovery_and_movement_returns_after_stun(self):
        run, player = self.landing()
        caster = run.players[1]
        run.match.switch_hero(caster, heroes()[0x02E0000000000042])
        x, y, z = player.mover.position
        relocate(run, caster, (x, y, z + 1.5))
        flash_tests.FlashbangTests().cast(run, caster, player)
        self.assertTrue(run.match.combat.is_stunned(player))
        self.assertFalse(pile_instance(player).states[24].flags & 1)
        # State20 belongs to Ability24's authored subgraph; landing recovery14
        # is outside that list and retains its own timed lifetime after stop.
        self.assertFalse(any(key[1] == 20 for key in restrictions(player)))
        origin = tuple(player.mover.position)
        end = run.match.combat.stuns[player.body]
        while run.frame < end - 1:
            step(run, player, forward=127)
            self.assertEqual(tuple(player.mover.position), origin)
        for _ in range(12):
            step(run, player, forward=127)
        self.assertFalse(restrictions(player))
        self.assertGreater(player.mover.position[2] - origin[2], 0.3)

    def test_death_and_timed_respawn_do_not_carry_recovery_restrictions_to_new_body(self):
        run, player = self.landing()
        previous_body = player.body
        fight = run.match.combat
        own = next(target for target in fight._targets() if target.owner is player)
        fight._damage(run.players[1], own, 10000, False, None, run.frame)
        self.assertTrue(fight.is_dead(player))
        for _ in range(1000):
            step(run, player)
            if player.body != previous_body:
                break
        self.assertNotEqual(player.body, previous_body)
        self.assertFalse(fight.is_dead(player))
        self.assertFalse(restrictions(player))
        for _ in range(40):
            step(run, player)
        origin = tuple(player.mover.position)
        for _ in range(12):
            step(run, player, forward=127)
        self.assertGreater(player.mover.position[2] - origin[2], 0.3)


if __name__ == '__main__':
    unittest.main()
