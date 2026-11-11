"""Winston interactions with real Genji Deflect states from actual input.

Frontal hemisphere/volume admission and melee shield penetration are explicitly
reconstructed policies, not native animation/filter equivalence.
"""

import unittest

from test_game_combat import Run, aim
from test_game_mover import box, wall

from ow174.game import stats, winston
from ow174.game.collision import World
from ow174.game.content import heroes

GENJI = 0x02E0000000000029


class WinstonDeflectTests(unittest.TestCase):
    def setUp(self):
        self.run = Run(names=('Winston', 'Genji', 'Shield owner'))
        self.match = self.run.match
        self.match.bots.clear()
        self.owner, self.genji, self.shielder = self.run.players
        self.match.switch_hero(self.owner, heroes()[winston.HERO])
        self.match.switch_hero(self.genji, heroes()[GENJI])
        self.owner.team = 0
        self.genji.team = self.shielder.team = 1
        self.move(self.owner, (0, 1, 0))
        self.move(self.genji, (0, 1, 1.5))
        self.move(self.shielder, (30, 1, 30))
        self.fight = self.match.combat
        self.facing = -32768
        self.owner.body_script.ult_seconds = 0

    @staticmethod
    def move(player, point):
        player.mover.position[:] = point
        player.mover.body = None
        player.mover.velocity = (0, 0, 0)

    def health(self, player):
        return self.fight.vitals_of(next(t for t in self.fight._targets()
                                           if t.owner is player)).total

    def step(self, attack=0, defense=0, *, pitch=0):
        self.run.step({self.owner: (0, pitch, attack), self.genji: (self.facing, 0, defense)})

    def state(self):
        instance = next(i for i in self.genji.body_script.component.instances.values()
                        if i.graph.index == 0x03A3)
        return instance.state(10)

    def deflect(self):
        self.step(defense=16)
        for _ in range(4):
            self.step()
        self.assertTrue(self.state().active)

    def rage(self):
        script = self.owner.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        self.step(32)
        for _ in range(35):
            self.step()
        self.assertTrue(winston.primal_active(script.component))

    def test_reflected_allied_helix_returns_direct_and_splash_damage(self):
        for reflect, shield_hp in ((False, None), (True, None), (True, 700), (True, 5)):
            with self.subTest(reflect=reflect, shield_hp=shield_hp):
                self.setUp()
                soldier = self.shielder
                soldier.team = self.owner.team
                self.move(soldier, (0, 1, -8))
                self.move(self.genji, (0, 1, 8))
                shield = None
                if shield_hp is not None:
                    self.step(16)
                    for _ in range(4):
                        self.step()
                    shield = self.fight.barriers.actors[self.owner.body]
                    if shield_hp < shield.health:
                        shield.health = shield_hp  # prior damage fixture for the breaking impact
                if reflect:
                    self.deflect()
                yaw, pitch = aim(self.fight._eye(soldier), (0, 2.35, 8))
                self.run.step({soldier: (yaw, pitch, 2), self.genji: (self.facing, 0, 0)})
                for _ in range(60):
                    self.step()
                self.assertEqual(self.health(self.genji), 200 if reflect else 80)
                loss = 500 - self.health(self.owner)
                if reflect and shield is None:
                    self.assertEqual(loss, 110)
                    self.assertEqual(self.genji.stats.value(stats.HERO_DAMAGE), 110)
                else:
                    self.assertEqual(loss, 0)
                    self.assertEqual(self.genji.stats.value(stats.HERO_DAMAGE), 0)
                if shield is not None:
                    self.assertEqual(shield.health, max(0, shield_hp - 120))
                self.assertFalse(self.fight.rockets)

    def test_tesla_ignores_frontal_deflect_without_return_damage(self):
        self.deflect()
        for _ in range(3):
            self.step(1)
        self.assertAlmostEqual(self.health(self.genji), 197.12)
        self.assertEqual(self.health(self.owner), 500)
        self.assertTrue(self.state().active)

    def test_frontal_quick_melee_is_blocked_without_reflection_or_charge(self):
        self.deflect()
        self.step(0x800)
        for _ in range(40):
            self.step()
        self.assertEqual(self.health(self.genji), 200)
        self.assertEqual(self.health(self.owner), 500)
        self.assertEqual(self.owner.body_script.component.entity_var(342).value(), 0)

    def test_rear_quick_melee_is_not_blocked(self):
        self.facing = 0
        self.deflect()
        self.step(0x800)
        for _ in range(40):
            self.step()
        self.assertEqual(self.health(self.genji), 170)

    def test_primal_punch_is_parried_without_damage_or_knockback(self):
        self.rage()
        self.deflect()
        for _ in range(60):
            self.step(1)
        self.assertEqual(self.health(self.genji), 200)
        self.assertEqual(self.health(self.owner), 1000)
        self.assertEqual(self.genji.mover.position[2], 1.5)
        self.assertEqual(self.genji.mover.velocity, (0, 0, 0))
        self.assertTrue(self.state().active)

    def test_rear_primal_punch_damages_and_knocks_back(self):
        self.rage()
        self.facing = 0
        self.deflect()
        for _ in range(40):
            self.step(1)
        self.assertEqual(self.health(self.genji), 160)
        self.assertGreater(self.genji.mover.position[2], 2)

    def test_jump_takeoff_damage_and_knockback_are_not_deflected(self):
        self.deflect()
        self.step(8)
        self.assertEqual(self.health(self.genji), 199)
        self.assertGreater(self.genji.mover.velocity[1], 0)
        self.assertEqual(self.health(self.owner), 500)

    def test_actual_jump_landing_damage_is_not_deflected(self):
        self.move(self.genji, (0, 1, 30))
        self.step(8, pitch=-16384)
        for _ in range(250):
            self.step()
            if self.owner.mover.velocity[1] < 0 and self.owner.mover.position[1] < 3:
                break
        self.move(self.genji, (0, 1, 0))
        self.deflect()
        for _ in range(40):
            self.step()
            if self.owner.body not in self.fight.winston_jump.flights:
                break
        self.assertTrue(self.state().active)
        self.assertNotIn(self.owner.body, self.fight.winston_jump.flights)
        self.assertEqual(self.health(self.genji), 150)
        self.assertEqual(self.health(self.owner), 500)
        self.assertGreater(self.genji.mover.velocity[1], 0)
        landing_height = self.genji.mover.position[1]
        self.step()
        self.assertGreater(self.genji.mover.position[1], landing_height)

    def test_expiry_removes_melee_block(self):
        self.deflect()
        for _ in range(140):
            self.step()
        self.assertFalse(self.state().active)
        self.step(0x800)
        for _ in range(40):
            self.step()
        self.assertEqual(self.health(self.genji), 170)

    def test_cancel_removes_melee_block_and_cooldown_rejects_reactivation(self):
        self.deflect()
        for _ in range(25):
            self.step()
        self.step(defense=16)
        self.assertFalse(self.state().active)
        self.step()
        self.step(0x800, defense=16)
        for _ in range(40):
            self.step()
        self.assertEqual(self.state().counter, 1)
        self.assertEqual(self.health(self.genji), 170)

    def test_rear_melee_cannot_reach_genji_through_a_static_wall(self):
        self.facing = 0
        self.deflect()
        self.match.collision_world = World(box(-30, 30, -1, 1, -30, 30) + wall(-10, .7, 10, .7))
        self.step(0x800)
        for _ in range(40):
            self.step()
        self.assertEqual(self.health(self.genji), 200)

    def test_exposed_capsule_before_enemy_bubble_still_takes_tesla(self):
        self.match.switch_hero(self.shielder, heroes()[winston.HERO])
        self.move(self.shielder, (0, 1, 6.5))
        self.move(self.genji, (0, 1, 2))
        self.run.step({self.shielder: (0, 0, 16)})
        for _ in range(3):
            self.step()
        barrier = self.fight.barriers.actors[self.shielder.body]
        self.move(self.shielder, (30, 1, 30))
        self.deflect()
        self.step(1)
        self.assertAlmostEqual(self.health(self.genji), 199.04)
        self.assertEqual(barrier.health, 700)


if __name__ == '__main__':
    unittest.main()
