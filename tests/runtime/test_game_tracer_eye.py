"""Verified FirstPerson offsets through actual player input and low cover.

This does not accept native animated camera/muzzle or skeletal hit regions.
"""

import unittest

from test_game_combat import Run

from ow174.game import combat
from ow174.game.collision import World
from ow174.game.commands import CROUCH, Command
from ow174.game.content import heroes


class TracerEyeTests(unittest.TestCase):
    def arena(self, top):
        run = Run(names=('Tracer', 'McCree'))
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        player, enemy = run.players
        run.match.switch_hero(player, heroes()[0x02E0000000000003])
        run.match.switch_hero(enemy, heroes()[0x02E0000000000042])
        player.mover.position[:] = (0, 1, 0)
        enemy.mover.position[:] = (0, 1, 5)
        triangles = [(-30, 1, -30, -30, 1, 30, 30, 1, 30),
                     (-30, 1, -30, 30, 1, 30, 30, 1, -30),
                     (-20, 1, 2.5, 20, 1, 2.5, 20, top, 2.5),
                     (-20, 1, 2.5, 20, top, 2.5, -20, top, 2.5),
                     (-20, 1, 2.5, 20, top, 2.5, 20, 1, 2.5),
                     (-20, 1, 2.5, -20, top, 2.5, 20, top, 2.5)]
        run.match.collision_world = World(triangles, name='FirstPerson low cover')
        for actor in run.players:
            actor.mover.use(world=run.match.collision_world)
        return run, player, enemy

    def health(self, run, player):
        target = next(t for t in run.match.combat._targets() if t.owner is player)
        return run.match.combat.vitals_of(target).total

    def test_xml_offsets_feed_player_eye_target_eye_and_crouch_transition(self):
        run, player, enemy = self.arena(2.1)
        for actor, standing, crouched in ((player, 1.25, 0.875), (enemy, 1.425, 0.9975)):
            self.assertAlmostEqual(run.match.combat._eye(actor)[1]-1, standing)
            run.frame += 1
            run.match.update(run.frame*combat.TICK, run.frame)
            actor.take_commands([Command(run.frame, buttons=CROUCH)])
            self.assertTrue(actor.mover.crouched)
            target = next(t for t in run.match.combat._targets() if t.owner is actor)
            self.assertAlmostEqual(target.eye, crouched)
            self.assertAlmostEqual(run.match.combat._eye(actor)[1]-actor.mover.position[1], crouched)

    def test_standing_tracer_cannot_fire_over_cover_that_wrong_generic_eye_cleared(self):
        run, player, enemy = self.arena(2.4)
        before = self.health(run, enemy)
        run.step({player: (0, 0, 1)})
        self.assertEqual(self.health(run, enemy), before)
        self.assertFalse(player.client.messages(combat.HIT))

    def test_standing_passes_lower_cover_but_actual_crouch_eye_is_blocked(self):
        for crouch in (False, True):
            with self.subTest(crouched=crouch):
                run, player, enemy = self.arena(2.1)
                if crouch:
                    for _ in range(3):
                        run.step({player: (0, 0, CROUCH)})
                before = self.health(run, enemy)
                run.step({player: (0, 0, 1 | (CROUCH if crouch else 0))})
                self.assertEqual(before-self.health(run, enemy), 0 if crouch else 12)

    def test_mccree_reverse_fire_uses_its_own_verified_standing_eye(self):
        run, player, enemy = self.arena(2.48)
        before = self.health(run, player)
        run.step({enemy: (-32768, 0, 1)})
        self.assertEqual(self.health(run, player), before)


if __name__ == '__main__':
    unittest.main()
