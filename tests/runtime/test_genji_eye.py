"""104319 FirstPerson offsets and actual input around low static cover."""

from test_game_mover import FLOOR
from test_genji_combat import GenjiFixture

from ow174.game import combat
from ow174.game.collision import World
from ow174.game.commands import CROUCH
from ow174.game.content import heroes


def cover(top, z=2):
    a, b, c, d = (-3, 0, z), (3, 0, z), (3, top, z), (-3, top, z)
    return [(*a, *b, *c), (*a, *c, *d)]


class GenjiEyeTests(GenjiFixture):
    def shoot_level(self, run, player, *, crouch=False):
        held = CROUCH if crouch else 0
        for _ in range(20):
            run.step({player: (0, 0, held)})
        self.assertEqual(player.mover.crouched, crouch)
        for frame in range(60):
            run.step({player: (0, 0, held | (1 if frame == 0 else 0))})

    def test_standing_and_crouched_source_and_target_share_resource_offset(self):
        for hero, standing, crouched in ((0x29, 1.2, .84), (0x42, 1.425, .9975)):
            with self.subTest(hero=hero):
                run, player = self.spawn(4)
                run.match.switch_hero(player, heroes()[0x02E0000000000000 | hero])
                player.mover.position[:] = [0, 1, 0]
                for buttons, expected in ((0, standing), (CROUCH, crouched)):
                    for _ in range(20):
                        run.step({player: (0, 0, buttons)})
                    target = next(t for t in run.match.combat._targets() if t.owner is player)
                    self.assertAlmostEqual(target.eye, expected)
                    self.assertAlmostEqual(
                        run.match.combat._eye(player)[1], player.mover.position[1] + expected)

    def test_genji_standing_shuriken_cannot_clear_cover_above_actual_eye(self):
        run, player = self.spawn(4)
        run.match.collision_world = World(FLOOR + cover(2.35))
        self.shoot_level(run, player)
        self.assertEqual(self.health(run, run.players[1]), 200)
        self.assertEqual(player.client.messages(combat.HIT), [])

    def test_genji_standing_shuriken_clear_lower_cover(self):
        run, player = self.spawn(4)
        run.match.collision_world = World(FLOOR + cover(2.0))
        self.shoot_level(run, player)
        self.assertEqual(self.health(run, run.players[1]), 113)

    def test_genji_crouched_shuriken_cannot_clear_cover_above_actual_eye(self):
        run, player = self.spawn(4)
        run.match.collision_world = World(FLOOR + cover(2.0))
        self.shoot_level(run, player, crouch=True)
        self.assertEqual(self.health(run, run.players[1]), 200)

    def test_enemy_mccree_camera_is_below_cover_that_generic_eye_cleared(self):
        run, genji = self.spawn(4)
        enemy = run.players[1]
        run.match.switch_hero(enemy, heroes()[0x02E0000000000042])
        enemy.mover.position[:] = [0, 1, 0]
        genji.mover.position[:] = [0, 1, 4]
        run.match.collision_world = World(FLOOR + cover(2.5))
        self.shoot_level(run, enemy)
        self.assertEqual(self.health(run, genji), 200)

    def test_enemy_mccree_can_fire_over_cover_below_its_actual_eye(self):
        run, genji = self.spawn(4)
        enemy = run.players[1]
        run.match.switch_hero(enemy, heroes()[0x02E0000000000042])
        enemy.mover.position[:] = [0, 1, 0]
        genji.mover.position[:] = [0, 1, 4]
        run.match.collision_world = World(FLOOR + cover(2.3))
        self.shoot_level(run, enemy)
        self.assertLess(self.health(run, genji), 200)
