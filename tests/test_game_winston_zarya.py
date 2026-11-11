"""Winston damage/impulses against actual Zarya personal/projected casts."""

import unittest

from test_game_combat import Run
from test_game_mover import box, wall

from ow174.game import combat, stats, winston, zarya
from ow174.game.collision import World
from ow174.game.content import heroes
from ow174.game.script import expr


class WinstonZaryaTests(unittest.TestCase):
    def setUp(self):
        self.run = Run(names=('Winston', 'Zarya', 'Receiver'))
        self.match = self.run.match
        self.match.bots.clear()
        self.owner, self.zarya, self.receiver = self.run.players
        self.match.switch_hero(self.owner, heroes()[winston.HERO])
        self.match.switch_hero(self.zarya, heroes()[zarya.HERO])
        self.owner.team = 0
        self.zarya.team = self.receiver.team = 1
        self.move(self.owner, (0, 1, 0))
        self.move(self.zarya, (0, 1, 1.5))
        self.move(self.receiver, (30, 1, 30))
        self.fight = self.match.combat
        self.owner.body_script.ult_seconds = 0

    @staticmethod
    def move(player, point):
        player.mover.position[:] = point
        player.mover.velocity = (0, 0, 0)
        player.mover.body = None

    def step(self, attack=0, cast=0, receive=0, *, pitch=0):
        self.run.step({self.owner: (0, pitch, attack), self.zarya: (0, 0, cast),
                       self.receiver: (-32768, 0, receive)})

    def health(self, player):
        return self.fight.vitals_of(next(t for t in self.fight._targets()
                                           if t.owner is player)).total

    def instance(self, index):
        return next(i for i in self.zarya.body_script.component.instances.values() if i.graph.index == index)

    def energy(self):
        return self.zarya.body_script.component.entity_var(87).value()

    def personal(self):
        self.step(cast=8)
        self.assertTrue(self.instance(0x244).state(2).active)

    def projected(self):
        self.move(self.zarya, (0, 1, -3))
        self.move(self.receiver, (0, 1, 1.5))
        self.step(cast=16)
        for _ in range(3):
            self.step()
        instance = self.instance(0x25C)
        self.assertTrue(instance.state(10).active)
        self.assertEqual(instance.vars[536].value(), expr.Entity(self.receiver.body))

    def rage(self):
        script = self.owner.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        self.step(32)
        for _ in range(35):
            self.step()

    def test_tesla_is_absorbed_into_personal_barrier_and_authored_energy(self):
        self.personal()
        for _ in range(20):
            self.step(1)
        self.assertEqual(self.health(self.zarya), 400)
        self.assertAlmostEqual(self.fight.zarya_barriers.active[self.zarya.body].remaining, 180.8)
        self.assertAlmostEqual(self.energy(), 3.84, places=4)
        self.assertEqual(self.owner.body_script.component.entity_var(342).value(), 0)
        self.assertEqual(self.owner.client.messages(combat.HIT), [])

    def test_quick_melee_absorption_generates_energy_without_body_damage(self):
        self.personal()
        self.step(0x800)
        for _ in range(40):
            self.step()
        self.assertEqual(self.health(self.zarya), 400)
        self.assertAlmostEqual(self.fight.zarya_barriers.active[self.zarya.body].remaining, 170)
        self.assertAlmostEqual(self.energy(), 6)

    def test_primal_absorption_blocks_both_damage_and_knockback(self):
        self.rage()
        self.personal()
        for _ in range(30):
            self.step(1)
        self.assertEqual(self.health(self.zarya), 400)
        self.assertEqual(self.zarya.mover.position[2], 1.5)
        self.assertEqual(self.zarya.mover.velocity, (0, 0, 0))
        self.assertEqual(self.fight.zarya_barriers.active[self.zarya.body].remaining, 160)
        self.assertAlmostEqual(self.energy(), 8)

    def test_actual_breaking_punch_has_no_spill_or_impulse_next_punch_hits(self):
        self.rage()
        self.personal()
        target = next(t for t in self.fight._targets() if t.owner is self.zarya)
        # An explicitly previously damaged barrier; the breaking event is a real punch.
        self.fight._damage(self.owner, target, 190, False, None, self.run.frame)
        for _ in range(30):
            self.step(1)
        self.assertEqual(self.health(self.zarya), 400)
        self.assertEqual(self.zarya.mover.position[2], 1.5)
        self.assertAlmostEqual(self.energy(), 40)
        for _ in range(45):
            self.step(1)
        self.assertEqual(self.health(self.zarya), 360)
        self.assertGreater(self.zarya.mover.position[2], 2)

    def test_expiry_and_cooldown_repress_do_not_restore_absorption(self):
        self.personal()
        for _ in range(130):
            self.step()
        self.assertFalse(self.instance(0x244).state(2).active)
        self.step(cast=8)
        self.step(1)
        self.assertEqual(self.instance(0x244).state(2).counter, 1)
        self.assertAlmostEqual(self.health(self.zarya), 399.04)

    def test_jump_takeoff_is_absorbed_without_knockback(self):
        self.personal()
        self.step(8)
        self.assertEqual(self.health(self.zarya), 400)
        self.assertEqual(self.zarya.mover.position[2], 1.5)
        self.assertEqual(self.zarya.mover.velocity, (0, 0, 0))
        self.assertAlmostEqual(self.fight.zarya_barriers.active[self.zarya.body].remaining, 199)
        self.assertAlmostEqual(self.energy(), .2, places=5)

    def test_jump_landing_personal_and_projected_barriers_absorb_damage_and_impulse(self):
        for protection, remaining in (('none', 0), ('personal', 200), ('projected', 200),
                                      ('personal', 10), ('projected', 10)):
            with self.subTest(protection=protection, remaining=remaining):
                self.setUp()
                self.move(self.zarya, (0, 1, 30))
                self.step(8, pitch=-16384)
                for _ in range(250):
                    self.step()
                    if self.owner.mover.velocity[1] < 0 and self.owner.mover.position[1] < 4:
                        break
                self.assertIn(self.owner.body, self.fight.winston_jump.flights)
                target = self.receiver if protection == 'projected' else self.zarya
                if protection == 'personal':
                    self.personal()
                elif protection == 'projected':
                    self.projected()
                    self.move(self.zarya, (0, 1, -30))
                self.move(target, (0, 1, 0))
                health = self.health(target)
                barrier = None
                if protection != 'none':
                    barriers = self.fight.zarya_barriers
                    self.assertTrue(barriers.protects(target))
                    table = barriers.active if protection == 'personal' else barriers.projected
                    barrier = table[self.zarya.body]
                    self.assertEqual(barrier.remaining, 200)
                    if remaining < 200:
                        barrier.remaining = remaining  # controlled prior damage for the breaking cases
                for _ in range(40):
                    self.step()
                    if self.owner.body not in self.fight.winston_jump.flights:
                        break
                self.assertNotIn(self.owner.body, self.fight.winston_jump.flights)
                charge = self.owner.body_script.component.entity_var(342).value()
                if barrier is None:
                    self.assertEqual(self.health(target), health - 50)
                    self.assertGreater(target.mover.velocity[1], 0)
                    self.assertEqual(charge, 50)
                else:
                    self.assertEqual(self.health(target), health)
                    self.assertEqual(target.mover.velocity, (0, 0, 0))
                    self.assertEqual(charge, 0)
                    self.assertEqual(self.owner.client.messages(combat.HIT), [])
                    self.assertAlmostEqual(self.energy(), min(50, remaining) / 5)
                    self.assertEqual(barrier.remaining, max(0, remaining - 50))

    def test_actual_projected_cast_selects_ally_and_absorbs_tesla(self):
        self.projected()
        for _ in range(20):
            self.step(1)
        self.assertEqual(self.health(self.receiver), 200)
        self.assertAlmostEqual(self.fight.zarya_barriers.projected[self.zarya.body].remaining, 180.8)
        self.assertAlmostEqual(self.energy(), 3.84, places=4)
        self.assertEqual(self.owner.body_script.component.entity_var(342).value(), 0)

    def test_breaking_projected_barrier_only_grants_charge_on_next_tesla_tick(self):
        self.projected()
        self.step(1)
        barrier = self.fight.zarya_barriers.projected[self.zarya.body]
        barrier.remaining = .5  # existing damage fixture, actual Tesla does the break
        charge = self.owner.body_script.component.entity_var(342)
        self.step(1)
        self.assertEqual(barrier.remaining, 0)
        self.assertEqual(self.health(self.receiver), 200)
        self.assertEqual(charge.value(), 0)
        self.assertEqual(self.owner.stats.value(stats.HERO_DAMAGE), 0)
        self.assertEqual(self.owner.client.messages(combat.HIT), [])
        self.step(1)
        self.assertAlmostEqual(self.health(self.receiver), 199.04)
        self.assertAlmostEqual(charge.value(), .96, places=5)
        self.assertAlmostEqual(self.owner.stats.value(stats.HERO_DAMAGE), .96)

    def test_personal_then_projected_barrier_own_separate_tesla_breaks(self):
        projector = self.receiver
        self.assertTrue(self.match.switch_hero(projector, heroes()[zarya.HERO]))
        self.move(projector, (0, 1, 5))
        self.step(cast=8, receive=16)
        for _ in range(3):
            self.step()
        projected = next(i for i in projector.body_script.component.instances.values()
                         if i.graph.index == 0x25C)
        self.assertTrue(projected.state(10).active)
        self.assertEqual(projected.vars[536].value(), expr.Entity(self.zarya.body))
        self.move(projector, (0, 1, 25))
        self.step(1)
        personal_barrier = self.fight.zarya_barriers.active[self.zarya.body]
        self.assertAlmostEqual(personal_barrier.remaining, 199.04)
        personal_barrier.remaining = .5  # previously damaged barrier fixture
        self.step(1)
        self.assertEqual(personal_barrier.remaining, 0)
        self.assertFalse(projector.body_script.component.entity_var(87).value())
        self.step(1)
        projected_barrier = self.fight.zarya_barriers.projected[projector.body]
        self.assertAlmostEqual(projected_barrier.remaining, 199.04)
        projected_barrier.remaining = .5
        self.step(1)
        self.assertEqual(projected_barrier.remaining, 0)
        self.assertEqual(self.health(self.zarya), 400)
        charge = self.owner.body_script.component.entity_var(342)
        self.assertEqual(charge.value(), 0)
        self.assertEqual(self.owner.client.messages(combat.HIT), [])
        self.step(1)
        self.assertAlmostEqual(self.health(self.zarya), 399.04)
        self.assertAlmostEqual(charge.value(), .96, places=5)
        self.assertAlmostEqual(self.energy(), .292, places=5)
        self.assertAlmostEqual(projector.body_script.component.entity_var(87).value(), .292, places=5)

    def test_hostile_winston_shell_breaks_before_projected_barrier_absorption(self):
        target = self.receiver
        self.assertTrue(self.match.switch_hero(target, heroes()[winston.HERO]))
        self.move(target, (0, 1, 7))
        self.move(self.zarya, (0, 1, 13))
        self.run.step({self.zarya: (-32768, 0, 16), target: (0, 0, 16)})
        for _ in range(3):
            self.step()
        self.assertTrue(self.instance(0x25C).state(10).active)
        self.assertEqual(self.instance(0x25C).vars[536].value(), expr.Entity(target.body))
        self.assertTrue(self.fight.zarya_barriers.protects(target))
        projected = self.fight.zarya_barriers.projected[self.zarya.body]
        shell = self.fight.barriers.actors[target.body]
        self.step(1)
        self.assertAlmostEqual(shell.health, 699.04)
        self.assertEqual(projected.remaining, 200)
        shell.health = .5  # previously damaged shell fixture
        self.step(1)
        self.assertEqual(shell.health, 0)
        self.assertEqual(projected.remaining, 200)
        self.step(1)
        self.assertAlmostEqual(projected.remaining, 199.04)
        projected.remaining = .5
        self.step(1)
        self.assertEqual(projected.remaining, 0)
        self.assertEqual(self.health(target), 500)
        charge = self.owner.body_script.component.entity_var(342)
        self.assertEqual(charge.value(), 0)
        self.assertEqual(self.owner.client.messages(combat.HIT), [])
        self.step(1)
        self.assertAlmostEqual(self.health(target), 499.232)
        self.assertAlmostEqual(charge.value(), .768, places=5)
        self.assertAlmostEqual(self.energy(), .292, places=5)

    def test_projected_cast_rejects_enemy_self_far_and_occluded_targets(self):
        for case in ('enemy', 'far', 'wall'):
            with self.subTest(case=case):
                self.setUp()
                self.move(self.zarya, (0, 1, -3))
                self.move(self.receiver, (0, 1, 1.5))
                if case == 'enemy':
                    self.receiver.team = 0
                elif case == 'far':
                    self.move(self.receiver, (0, 1, 100))
                else:
                    self.match.collision_world = World(box(-30, 30, -1, 1, -30, 30)
                                                       + wall(-10, -1, 10, -1))
                self.step(cast=16)
                for _ in range(5):
                    self.step()
                self.assertFalse(self.instance(0x25C).state(10).active)

    def test_personal_shields_regenerate_only_after_real_damage_delay(self):
        for _ in range(30):
            self.step(1)
        before = self.health(self.zarya)
        self.assertAlmostEqual(before, 371.2)
        for _ in range(180):
            self.step()
        self.assertAlmostEqual(self.health(self.zarya), before)
        for _ in range(20):
            self.step()
        self.assertAlmostEqual(self.health(self.zarya), 377.2, places=4)

    def test_projected_shield_precedes_genji_parry_for_primal_energy(self):
        self.match.switch_hero(self.receiver, heroes()[0x02E0000000000029])
        self.rage()
        self.projected()
        self.step(receive=16)
        for _ in range(4):
            self.step()
        for _ in range(30):
            self.step(1)
        self.assertEqual(self.health(self.receiver), 200)
        self.assertEqual(self.receiver.mover.velocity, (0, 0, 0))
        self.assertAlmostEqual(self.fight.zarya_barriers.projected[self.zarya.body].remaining, 160)
        self.assertAlmostEqual(self.energy(), 8)

    def test_ally_projected_shield_protects_winston_from_actual_rifle(self):
        self.zarya.team = 0
        self.receiver.team = 1
        self.move(self.zarya, (0, 1, -3))
        self.move(self.receiver, (0, 1, 10))
        self.step(cast=16)
        for _ in range(3):
            self.step()
        self.assertEqual(self.instance(0x25C).vars[536].value(), expr.Entity(self.owner.body))
        self.step(receive=1)
        for _ in range(5):
            self.step()
        self.assertEqual(self.health(self.owner), 500)
        self.assertLess(self.fight.zarya_barriers.projected[self.zarya.body].remaining, 200)
        self.assertGreater(self.energy(), 0)


if __name__ == '__main__':
    unittest.main()
