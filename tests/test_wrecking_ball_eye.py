"""Authored first-person offsets in the static ray model, not animated hitboxes."""

import unittest

from test_game_combat import Run

from ow174.game import combat
from ow174.game.collision import World
from ow174.game.content import heroes
from ow174.game.script import nodes


class BallEyeTests(unittest.TestCase):
    def test_authored_ball_and_enemy_mccree_offsets_and_low_cover(self):
        for hero, standing, crouching in ((0x02E00000000001CA, 1.83, 1.5),
                                          (0x02E0000000000042, 1.425, 0.9975)):
            body = heroes()[hero].body
            self.assertEqual(combat.eye_height(body), standing)
            self.assertEqual(combat.eye_height(body, True), crouching)
            # Ball's current graph rejects grounded crouch; do not force an unavailable stance.
            stances = ((False, standing),) if hero == 0x02E00000000001CA else (
                (False, standing), (True, crouching))
            for crouched, expected in stances:
                # One wall below and one above the authored eye discriminate the old fallback too.
                for blocked in (False, True):
                    with self.subTest(hero=hero, crouched=crouched, blocked=blocked):
                        run = Run(names=('Shooter', 'Target'))
                        shooter, victim = run.players
                        self.assertTrue(run.match.switch_hero(shooter, heroes()[hero]))
                        run.match.bots.clear()
                        y = shooter.mover.position[1]
                        shooter.mover.position[:] = (0, y, 0)
                        victim.mover.position[:] = (0, y, 10)
                        height = y + expected + (0.05 if blocked else -0.05)
                        # Double-sided vertical cover; retain the fixture's ordinary floor.
                        cover = [(-5, y, 2, 5, y, 2, 5, height, 2),
                                 (-5, y, 2, 5, height, 2, -5, height, 2),
                                 (-5, y, 2, 5, height, 2, 5, y, 2),
                                 (-5, y, 2, -5, height, 2, 5, height, 2)]
                        collision = World([(-50, y, -50, -50, y, 50, 50, y, 50),
                                           (-50, y, -50, 50, y, 50, 50, y, -50), *cover])
                        run.match.collision_world = collision
                        for player in run.players:
                            player.mover.use(world=collision)
                        buttons = 0x80 if crouched else 0
                        for _ in range(40):
                            run.step({shooter: (0, 0, buttons)})
                        fight = run.match.combat
                        self.assertEqual(shooter.mover.crouched, crouched)
                        self.assertAlmostEqual(fight._eye(shooter)[1] - shooter.mover.position[1], expected)
                        descriptor = next(t for t in fight._targets() if t.owner is shooter)
                        self.assertEqual(descriptor.eye, expected)
                        target = next(t for t in fight._targets() if t.owner is victim)
                        vitals = fight.vitals_of(target)
                        before = vitals.total
                        volleys = [state for instance in shooter.body_script.component.instances.values()
                                   for state in instance.states.values()
                                   if isinstance(state, nodes.WeaponVolley)]
                        shots = sum(state.reported for state in volleys)
                        run.step({shooter: (0, 0, buttons | 1)})
                        self.assertEqual(sum(state.reported for state in volleys), shots + 1)
                        self.assertEqual(vitals.total < before, not blocked)
                        self.assertEqual(bool(shooter.client.messages(combat.HIT)), not blocked)
