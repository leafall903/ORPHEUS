"""Verified104319 FirstPerson eye fields and actual stand/crouch low-cover shots."""

import unittest

import test_baptiste_combat as fixture

from ow174.game import combat
from ow174.game.collision import World
from ow174.game.content import SOLDIER, heroes

ROWS = ((fixture.BAPTISTE, 1.35, .945), (0x02E000000000023B, 2.1, 1.75),
        (0x02E0000000000029, 1.2, .84), (0x02E0000000000042, 1.425, .9975),
        (SOLDIER, 1.55, 1.085))


class BaptisteEyeTests(unittest.TestCase):
    def test_actual_crouch_and_release_update_shooter_eye_and_target_eye_together(self):
        for hero, standing, crouched in ROWS:
            with self.subTest(hero=hex(hero)):
                run, actor, _, _, fight, _, _ = fixture.BaptisteCombatTests().setup_fight()
                run.match.switch_hero(actor, heroes()[hero])
                actor.mover.position[:] = (0, 1, 0)
                actor.mover.body = None
                for buttons, expected in ((0, standing), (0x80, crouched), (0, standing)):
                    for _ in range(4):
                        run.step({actor: (0, 0, buttons)})
                    target = next(t for t in fight._targets() if t.owner is actor)
                    self.assertEqual(actor.mover.crouched, bool(buttons))
                    self.assertAlmostEqual(fight._eye(actor)[1] - actor.mover.position[1], expected)
                    self.assertAlmostEqual(target.eye, expected)

    def test_baptiste_actual_primary_uses_verified_eye_against_low_cover(self):
        for crouched in (False, True):
            for height in (1.0, 1.45):
                with self.subTest(crouched=crouched, height=height):
                    run, actor, ally, enemy, fight, targets, _ = fixture.BaptisteCombatTests().setup_fight()
                    ally.mover.position[0] = 20
                    for player in run.players:
                        player.mover.body = None
                    top = 1 + height
                    run.match.collision_world = World([
                        (-100, 1, -100, -100, 1, 100, 100, 1, 100),
                        (-100, 1, -100, 100, 1, 100, 100, 1, -100),
                        (-10, 1, 4, 10, 1, 4, 10, top, 4),
                        (-10, 1, 4, 10, top, 4, -10, top, 4),
                    ])
                    buttons = 0x80 if crouched else 0
                    for _ in range(4):
                        run.step({actor: (0, 0, buttons)})
                    self.assertEqual(actor.mover.crouched, crouched)
                    before = fight.vitals_of(targets[enemy]).total
                    run.step({actor: (0, 0, buttons | 1)})
                    self.assertEqual(before - fight.vitals_of(targets[enemy]).total,
                                     24 if not crouched and height == 1.0 else 0)

    def test_enemy_mccree_stand_and_crouch_use_verified_height_at_cover(self):
        for crouched in (False, True):
            with self.subTest(crouched=crouched):
                run, bap, ally, enemy, fight, targets, _ = fixture.BaptisteCombatTests().setup_fight()
                run.match.switch_hero(enemy, heroes()[0x02E0000000000042])
                enemy.mover.position[:] = (0, 1, 8)
                ally.mover.position[0] = 20
                for player in run.players:
                    player.mover.body = None
                run.match.collision_world = World([
                    (-100, 1, -100, -100, 1, 100, 100, 1, 100),
                    (-100, 1, -100, 100, 1, 100, 100, 1, -100),
                    (-10, 1, 4, 10, 1, 4, 10, 2.2, 4),
                    (-10, 1, 4, 10, 2.2, 4, -10, 2.2, 4),
                ])
                buttons = 0x80 if crouched else 0
                for _ in range(4):
                    run.step({enemy: (-32768, 0, buttons)})
                self.assertEqual(enemy.mover.crouched, crouched)
                before = fight.vitals_of(targets[bap]).total
                run.step({enemy: (-32768, 0, buttons | 1)})
                after = fight.vitals_of(targets[bap]).total
                if crouched:
                    self.assertEqual(before, after)
                else:
                    self.assertLess(after, before)
                self.assertAlmostEqual(combat.eye_height(enemy.hero.body, crouched),
                                       .9975 if crouched else 1.425)
