"""Actual Widow inputs against the selected shared Zarya barrier reconstruction.

Capacity/energy/whole-breaking-event policy is explicit host reconstruction.
Native pet geometry, cleanse/immunity and client transport are not accepted here.
"""

import unittest

from test_game_combat import aim, in_world
from test_widowmaker_combat import FIRE, MELEE, SCOPE, _WidowmakerMatch

from ow174.game import combat
from ow174.game.collision import World
from ow174.game.content import heroes
from ow174.game.script import expr
from ow174.game.world import SHIELDS_POOL

ZARYA = 0x02E0000000000068


class _PersonalBarrierMatch(_WidowmakerMatch):
    def setUp(self):
        super().setUp()
        self.run.match.switch_hero(self.b, heroes()[ZARYA])
        self.b.mover.position[:] = (0, 0, 1.5)
        self.b.mover.body = None
        self.b.mover.history.clear()
        self.run.match.combat.history.pop(self.b.body, None)
        self.a.body_script.ult_seconds = 0
        self.a.body_script.set_ult_charge(0)
        self.step()

    def step(self, *, widow=0, zarya=0, height=1):
        x, y, z = self.b.mover.position
        direction = aim(self.run.match.combat._eye(self.a), (x, y + height, z))
        back = aim(self.run.match.combat._eye(self.b), self.run.match.combat._eye(self.a))
        self.run.step({self.a: (*direction, widow), self.b: (*back, zarya)})

    def idle(self, count, *, widow=0):
        for _ in range(count):
            self.step(widow=widow)

    def barrier(self):
        return next(i for i in self.b.body_script.component.instances.values() if i.graph.index == 0x244)

    def activate(self, *, widow=0):
        self.step(widow=widow, zarya=8)
        self.assertTrue(self.barrier().state(2).active)

    def energy(self):
        return self.b.body_script.component.entity_var(87).value()

    def charge(self):
        return self.a.body_script.component.vars[342].value()



class WidowmakerPersonalBarrierTests(_PersonalBarrierMatch, unittest.TestCase):
    def test_primary_and_melee_use_barrier_capacity_and_energy_without_body_charge(self):
        self.activate()
        self.step(widow=FIRE)
        self.idle(15)
        self.step(widow=MELEE)
        self.idle(40)
        fight = self.run.match.combat
        self.assertEqual(self.health(self.b), 400)
        self.assertEqual(fight.zarya_barriers.active[self.b.body].remaining, 157)
        self.assertAlmostEqual(self.energy(), 8.6, places=4)
        self.assertEqual(self.charge(), 0)
        self.assertEqual(self.a.client.messages(combat.HIT), [])

    def test_fully_scoped_body_hit_spends_ammo_but_awards_no_body_damage_charge(self):
        self.idle(100, widow=SCOPE)
        self.activate(widow=SCOPE)
        weapon = next(i for i in self.a.body_script.component.instances.values() if i.graph.index == 0x1B)
        ammo = weapon.vars[53].value()
        self.step(widow=SCOPE | FIRE)
        self.assertEqual(ammo - weapon.vars[53].value(), 5)
        self.assertEqual(self.health(self.b), 400)
        self.assertEqual(self.run.match.combat.zarya_barriers.active[self.b.body].remaining, 80)
        self.assertEqual(self.energy(), 24)
        self.assertEqual(self.charge(), 0)

    def test_critical_breaking_event_is_fully_blocked_and_next_event_hits_body(self):
        self.idle(100, widow=SCOPE)
        self.activate(widow=SCOPE)
        self.step(widow=SCOPE | FIRE, height=combat.EYE)
        self.assertEqual(self.health(self.b), 400)
        self.assertEqual(self.run.match.combat.zarya_barriers.active[self.b.body].remaining, 0)
        self.assertEqual(self.energy(), 40)
        self.assertEqual(self.charge(), 0)
        self.idle(80)
        self.assertFalse(self.barrier().state(2).active)
        self.step(widow=FIRE)
        self.assertEqual(self.health(self.b), 387)
        self.assertEqual(self.charge(), 13)

    def test_expiry_removes_protection_and_repress_does_not_refill_active_barrier(self):
        self.activate()
        self.step(widow=FIRE)
        counter = self.barrier().state(2).counter
        self.step(zarya=8)
        self.idle(1)
        self.assertEqual(self.barrier().state(2).counter, counter)
        self.assertEqual(self.run.match.combat.zarya_barriers.active[self.b.body].remaining, 187)
        self.idle(130)
        self.assertFalse(self.barrier().state(2).active)
        self.step(widow=FIRE)
        self.assertEqual(self.health(self.b), 387)
        self.assertEqual(self.charge(), 13)

    def test_body_damage_starts_shield_recovery_and_blocked_hit_does_not_restart_delay(self):
        self.idle(100, widow=SCOPE)
        self.step(widow=SCOPE | FIRE)
        fight = self.run.match.combat
        vitals = fight.vitals[self.b.body]
        self.assertEqual(vitals.now[SHIELDS_POOL], 80)
        damaged = fight.zarya_recovery.bodies[self.b.body].damaged_tick
        self.idle(187)
        self.assertEqual(vitals.now[SHIELDS_POOL], 80)
        self.activate()
        self.step(widow=FIRE)
        self.assertEqual(fight.zarya_recovery.bodies[self.b.body].damaged_tick, damaged)
        while self.run.frame < damaged + 250:
            self.step()
        self.assertAlmostEqual(vitals.now[SHIELDS_POOL], 110, places=4)
        self.assertEqual(self.charge(), 120)
        old = self.b.body
        self.run.match.switch_hero(self.b, heroes()[ZARYA])
        self.assertNotIn(old, fight.zarya_recovery.bodies)

    def test_broken_barrier_cooldown_rejects_recast_then_new_activation_has_full_capacity(self):
        self.idle(100, widow=SCOPE)
        self.activate(widow=SCOPE)
        self.step(widow=SCOPE | FIRE, height=combat.EYE)
        counter = self.barrier().state(2).counter
        self.step(zarya=8)
        self.assertFalse(self.barrier().state(2).active)
        self.assertEqual(self.barrier().state(2).counter, counter)
        self.assertGreater(self.barrier().vars[61].value(), 9)
        self.idle(650)
        self.activate()
        self.assertGreater(self.barrier().state(2).counter, counter)
        self.step(widow=FIRE)
        self.assertEqual(self.health(self.b), 400)
        self.assertEqual(self.run.match.combat.zarya_barriers.active[self.b.body].remaining, 187)


class _ProjectedBarrierMatch(_WidowmakerMatch):
    def setUp(self):
        super().setUp()
        self.b.mover.position[:] = (0, 0, 3)
        self.b.mover.body = None
        self.c = self.run.match.add_player(3, "ShieldAlly", ZARYA, self.a.team, False)
        in_world(self.run.match, self.c)
        self.run.match.switch_hero(self.c, heroes()[ZARYA])
        self.c.mover.position[:] = (-2, 0, 0)
        self.c.mover.body = None
        self.run.players.append(self.c)
        self.step()

    def step(self, *, enemy=0, zarya=0):
        point = self.run.match.combat._eye(self.a)
        back = aim(self.run.match.combat._eye(self.a), self.run.match.combat._eye(self.b))
        incoming = aim(self.run.match.combat._eye(self.b),
                       (self.a.mover.position[0], self.a.mover.position[1] + 1, self.a.mover.position[2]))
        protecting = aim(self.run.match.combat._eye(self.c), point)
        self.run.step({self.a: (*back, 0), self.b: (*incoming, enemy), self.c: (*protecting, zarya)})

    def idle(self, count):
        for _ in range(count):
            self.step()

    def barrier(self):
        return next(i for i in self.c.body_script.component.instances.values() if i.graph.index == 0x25C)

    def activate(self):
        self.step(zarya=0x10)
        self.assertTrue(self.barrier().state(10).active)
        self.assertEqual(self.barrier().vars[536].value(), expr.Entity(self.a.body))



class WidowmakerProjectedBarrierTests(_ProjectedBarrierMatch, unittest.TestCase):
    def test_projected_barrier_protects_widow_and_credits_absorption_to_ally_zarya(self):
        self.activate()
        self.step(enemy=FIRE)
        for _ in range(12):
            self.step(enemy=FIRE)
        fight = self.run.match.combat
        self.assertEqual(self.health(self.a), 175)
        remaining = fight.zarya_barriers.projected[self.c.body].remaining
        self.assertGreater(remaining, 0)
        self.assertLess(remaining, 200)
        self.assertGreater(self.c.body_script.component.entity_var(87).value(), 0)
        self.assertEqual(self.b.client.messages(combat.HIT), [])
        self.idle(130)
        self.assertFalse(self.barrier().state(10).active)
        self.step(enemy=FIRE)
        self.assertLess(self.health(self.a), 175)

    def test_absorbed_flashbang_cannot_stun_and_unprotected_flashbang_later_can(self):
        self.run.match.switch_hero(self.b, heroes()[0x02E0000000000042])
        self.b.mover.position[:] = (0, 0, 3)
        self.b.mover.body = None
        self.b.mover.history.clear()
        self.run.match.combat.history.pop(self.b.body, None)
        self.activate()
        self.step(enemy=0x10)
        self.idle(50)
        fight = self.run.match.combat
        self.assertEqual(fight.zarya_barriers.projected[self.c.body].remaining, 175)
        self.assertEqual(self.c.body_script.component.entity_var(87).value(), 5)
        self.assertEqual(self.health(self.a), 175)
        self.assertFalse(fight.is_stunned(self.a))
        self.idle(700)
        self.step(enemy=0x10)
        for _ in range(50):
            if fight.is_stunned(self.a):
                break
            self.step()
        self.assertTrue(fight.is_stunned(self.a))
        self.assertEqual(self.health(self.a), 150)

    def test_caster_body_retirement_removes_projected_protection(self):
        self.activate()
        self.step(enemy=FIRE)
        fight = self.run.match.combat
        old = self.c.body
        self.assertIn(old, fight.zarya_barriers.projected)
        self.run.match.switch_hero(self.c, heroes()[0x02E000000000006E])
        self.assertNotIn(old, fight.zarya_barriers.projected)
        self.idle(20)
        self.step(enemy=FIRE)
        self.assertLess(self.health(self.a), 175)

    def test_replacement_widow_body_does_not_inherit_old_target_protection(self):
        self.activate()
        self.step(enemy=FIRE)
        self.assertEqual(self.health(self.a), 175)
        old = self.a.body
        self.run.match.switch_hero(self.a, self.a.hero)
        self.a.mover.position[:] = (0, 0, 0)
        self.a.mover.body = None
        self.assertNotEqual(self.a.body, old)
        self.idle(20)
        self.step(enemy=FIRE)
        self.assertLess(self.health(self.a), 175)

    def test_enemy_wall_and_out_of_range_casts_cannot_protect_widow(self):
        for case in ("enemy", "wall", "range"):
            with self.subTest(case=case):
                self.setUp()
                if case == "enemy":
                    self.c.team = self.b.team
                elif case == "range":
                    self.c.mover.position[:] = (-31, 0, 0)
                    self.c.mover.body = None
                else:
                    self.run.match.collision_world = World([
                        (-50, 0, -50, -50, 0, 50, 50, 0, 50),
                        (-50, 0, -50, 50, 0, 50, 50, 0, -50),
                        (-1, 0, -5, -1, 0, 5, -1, 5, 5),
                        (-1, 0, -5, -1, 5, 5, -1, 5, -5),
                    ], name="projected barrier LOS")
                self.step(zarya=0x10)
                self.assertFalse(self.barrier().state(10).active)
                self.assertEqual(self.barrier().vars[61].value(), 0)
                self.step(enemy=FIRE)
                self.assertLess(self.health(self.a), 175)
                self.assertTrue(self.b.client.messages(combat.HIT))


if __name__ == "__main__":
    unittest.main()
