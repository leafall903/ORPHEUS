"""Rocket Punch input-to-collision reconstruction; original-client fidelity is separate."""

import unittest

from test_doomfist_combat import DOOMFIST, DoomfistFixture
from test_game_combat import aim
from test_game_mover import box

from ow174.game import combat, stats
from ow174.game.collision import World
from ow174.game.commands import FIRE, Command
from ow174.game.content import heroes


class RocketPunchTests(DoomfistFixture, unittest.TestCase):
    def setUp(self):
        self.prepare(6)

    def charge(self, frames):
        for _ in range(frames):
            self.run.step({self.a: (0, 0, 2)})

    def hit(self, frames=1):
        self.charge(frames)
        self.run.step({})
        for _ in range(90):
            if self.vitals().total < 200:
                return
            self.run.step({})
        self.fail("real release did not damage the enemy")

    def wall(self, ahead):
        x, y, z = self.a.mover.position
        collision = World(box(x - 30, x + 30, y - 1, y, z - 30, z + 30)
                          + box(x - 30, x + 30, y, y + 6, z + ahead, z + ahead + 1))
        self.match.collision_world = collision
        for player in self.match.players:
            player.mover.use(world=collision)

    def test_charge_does_not_damage_or_move_before_release(self):
        before = tuple(self.a.mover.position)
        self.charge(30)
        self.assertEqual(self.vitals().total, 200)
        self.assertEqual(tuple(self.a.mover.position), before)
        self.assertNotIn(self.a.body, self.fight.punches.dashes)

    def test_explicit_release_starts_dash_and_cooldown_starts_after_contact(self):
        self.charge(30)
        ability = self.a.body_script.component.instances[5].state(0)
        self.assertEqual(ability.remaining(), 0)
        self.assertNotIn(self.a.body, self.fight.punches.dashes)
        self.run.step({})
        for _ in range(35):
            dash = self.fight.punches.dashes.get(self.a.body)
            if dash is not None and dash.finished:
                break
            self.assertEqual(ability.remaining(), 0)
            self.assertEqual(self.vitals().total, 200)
            self.run.step({})
        self.assertTrue(dash.finished)
        self.assertLess(self.vitals().total, 200)
        self.settle(60)
        self.assertGreater(ability.remaining(), 0)
        self.assertEqual(len(self.a.client.messages(combat.HIT)), 1)

    def test_actual_enemy_rifle_death_during_charge_prevents_player_contact(self):
        own = self.fight.vitals[self.a.body]
        own.now[combat.HEALTH_POOL] = 5
        self.charge(20)
        yaw, pitch = aim(self.fight._eye(self.b), self.fight._eye(self.a))
        self.run.step({self.a: (0, 0, 2), self.b: (yaw, pitch, FIRE)})
        self.assertTrue(own.dead)
        self.assertEqual(own.killer, self.b.body)
        self.settle(60)
        self.assertNotIn(self.a.body, self.fight.punches.dashes)
        self.assertEqual(self.vitals().total, 200)
        self.assertFalse(self.a.client.messages(combat.HIT))

    def test_minimum_and_maximum_charge_apply_authored_fifty_and_hundred(self):
        for held, expected in ((1, 50), (90, 100)):
            with self.subTest(held=held):
                self.prepare(6)
                charge = self.charge_checkpoint()
                self.hit(held)
                self.assertEqual(self.vitals().total, 200 - expected)
                self.assert_damage_charge(charge, expected)
                self.assertEqual(self.fight.punches.dashes[self.a.body].damage, expected)
                own = next(t for t in self.fight._targets() if t.owner is self.a)
                self.assertEqual(self.fight.vitals_of(own).temporary_shields, 30)

    def test_intermediate_charge_is_between_bounds_and_hits_once(self):
        self.hit(60)
        after = self.vitals().total
        self.assertGreater(200 - after, 50)
        self.assertLess(200 - after, 100)
        self.settle(90)
        self.assertEqual(self.vitals().total, after)
        self.assertEqual(len(self.a.client.messages(combat.HIT)), 1)

    def test_missed_dash_is_finite_and_charge_increases_range(self):
        travel = []
        for held in (1, 90):
            self.prepare(30)
            start = self.a.mover.position[2]
            self.charge(held)
            self.settle(90)
            travel.append(self.a.mover.position[2] - start)
            self.assertEqual(self.vitals().total, 200)
            self.assertTrue(self.fight.punches.dashes[self.a.body].finished)
        self.assertGreater(travel[0], 4)
        self.assertLessEqual(travel[0], 37.5 * 0.15 + 0.01)
        self.assertGreater(travel[1], travel[0] + 5)
        self.assertLessEqual(travel[1], 37.5 * 0.45 + 0.01)

    def test_close_off_axis_player_is_not_hit_by_forward_dash(self):
        self.prepare(1.5)
        x, y, z = self.a.mover.position
        self.place_player(self.b, (x + 3, y, z + 1.5))
        self.settle(5)
        self.charge(1)
        self.settle(65)
        self.assertTrue(self.fight.punches.dashes[self.a.body].finished)
        self.assertGreater(self.a.mover.position[2] - z, 4)
        self.assertEqual(self.vitals().total, 200)
        self.assertFalse(self.a.client.messages(combat.HIT))

    def test_wall_between_bodies_stops_dash_without_enemy_damage(self):
        start = self.a.mover.position[2]
        self.wall(3)
        self.charge(90)
        self.settle(90)
        self.assertEqual(self.vitals().total, 200)
        self.assertLess(self.a.mover.position[2], start + 3)
        self.assertTrue(self.fight.punches.dashes[self.a.body].finished)

    def test_full_wall_impact_kills_once_and_does_not_grant_another_passive_stack(self):
        self.wall(8)
        self.hit(90)
        for _ in range(40):
            if self.fight.is_dead(self.b):
                break
            self.settle(1)
        self.assertTrue(self.fight.is_dead(self.b))
        self.assertEqual(self.a.stats.value(stats.ELIMINATIONS), 1)
        self.assertEqual(self.b.stats.value(stats.DEATHS), 1)
        self.assertEqual(len(self.a.client.messages(combat.HIT)), 2)
        own = next(t for t in self.fight._targets() if t.owner is self.a)
        self.assertEqual(self.fight.vitals_of(own).temporary_shields, 30)

    def test_wall_impact_after_owner_death_keeps_player_damage_and_attribution(self):
        for health in (200, 500):
            with self.subTest(health=health):
                self.prepare(6)
                victim = self.vitals()
                victim.now[combat.HEALTH_POOL] = victim.most[combat.HEALTH_POOL] = health
                self.b.body_script.set_health(health, health)
                self.wall(8)
                self.charge(90)
                self.run.step({})
                for _ in range(35):
                    if self.fight.punches.controlled(self.b.body):
                        break
                    self.run.step({})
                self.assertTrue(self.fight.punches.controlled(self.b.body))
                self.assertEqual(victim.total, health - 100)
                impact, = self.fight.punches.impacts
                self.assertEqual(impact.target, self.b.body)
                owner = next(t for t in self.fight._targets() if t.owner is self.a)
                self.fight._damage(self.b, owner, 1000, False, None, self.run.frame)
                self.assertTrue(self.fight.is_dead(self.a))
                self.assertIn(impact, self.fight.punches.impacts)
                self.settle(90)
                lethal = health == 200
                self.assertEqual(victim.total, max(0, health - 250))
                self.assertEqual(victim.dead, lethal)
                self.assertEqual(self.a.stats.value(stats.ELIMINATIONS), int(lethal))
                self.assertEqual(self.b.stats.value(stats.DEATHS), int(lethal))
                if lethal:
                    self.assertEqual(victim.killer, self.a.body)
                else:
                    self.assertFalse(self.b.body_script.component.input_suppressed)
                self.assertFalse(self.fight.punches.controlled(self.b.body))
                self.assertFalse(self.fight.punches.impacts)
                self.assertEqual(len(self.a.client.messages(combat.HIT)), 2)
                self.assertEqual(self.fight.vitals_of(owner).temporary_shields, 0)

    def test_incoming_enemy_punch_displacement_clips_and_releases_control(self):
        travel = []
        for wall in (False, True):
            with self.subTest(wall=wall):
                self.prepare(6)
                self.match.switch_hero(self.b, heroes()[DOOMFIST])
                body = next(t for t in self.fight._targets() if t.owner is self.a)
                own = self.fight.vitals_of(body)
                own.now[combat.HEALTH_POOL] = own.most[combat.HEALTH_POOL] = 500
                self.a.body_script.set_health(500, 500)
                start = self.a.mover.position[2]
                if wall:
                    self.wall(-1.8)
                for _ in range(90):
                    self.run.step({self.b: (-32768, 0, 2)})
                self.run.step({self.b: (-32768, 0, 0)})
                for _ in range(35):
                    if self.fight.punches.controlled(self.a.body):
                        break
                    self.run.step({})
                self.assertTrue(self.fight.punches.controlled(self.a.body))
                self.assertEqual(own.total, 400)
                self.settle(70)
                self.assertFalse(self.fight.punches.controlled(self.a.body))
                self.assertFalse(self.a.body_script.component.input_suppressed)
                self.assertEqual(own.total, 250 if wall else 400)
                end = self.a.mover.position[2]
                self.assertLess(end, start)
                if wall:
                    self.assertGreater(end, start - .8)
                travel.append(start - end)
                self.settle(35)
                self.assertEqual(self.a.mover.position[2], end)
                self.assertEqual(own.total, 250 if wall else 400)
        self.assertGreater(travel[0], travel[1] + .1)

    def test_outgoing_player_punch_pushes_and_clips_at_wall_before_control_ends(self):
        travel = []
        for wall in (False, True):
            with self.subTest(wall=wall):
                self.prepare(6)
                victim = self.vitals()
                victim.now[combat.HEALTH_POOL] = victim.most[combat.HEALTH_POOL] = 500
                self.b.body_script.set_health(500, 500)
                x, _, z = self.b.mover.position
                if wall:
                    self.wall(7.8)
                self.charge(90)
                self.run.step({})
                for _ in range(35):
                    if self.fight.punches.controlled(self.b.body):
                        break
                    self.run.step({})
                self.assertTrue(self.fight.punches.controlled(self.b.body))
                self.assertTrue(self.b.body_script.component.input_suppressed)
                self.assertEqual(victim.total, 400)
                self.settle(70)
                self.assertFalse(self.fight.punches.controlled(self.b.body))
                self.assertFalse(self.b.body_script.component.input_suppressed)
                self.assertEqual(victim.total, 250 if wall else 400)
                final = tuple(self.b.mover.position)
                self.assertAlmostEqual(final[0], x, delta=.01)
                self.assertGreater(final[2], z)
                if wall:
                    self.assertLess(final[2], z + 1.8)
                travel.append(final[2] - z)
                self.settle(35)
                self.assertEqual(tuple(self.b.mover.position), final)
                self.assertEqual(victim.total, 250 if wall else 400)
        self.assertGreater(travel[0], travel[1] + .1)

    def test_wall_bonus_applies_only_once_to_a_surviving_target(self):
        vitals = self.vitals()
        vitals.now[combat.HEALTH_POOL] = vitals.most[combat.HEALTH_POOL] = 1000
        self.b.body_script.set_health(1000, 1000)
        self.wall(8)
        self.charge(90)
        self.settle(90)
        self.assertEqual(vitals.total, 750)
        self.settle(90)
        self.assertEqual(vitals.total, 750)
        self.assertEqual(len(self.a.client.messages(combat.HIT)), 2)

    def test_stun_blocks_actual_weapon_damage_and_ammo_until_expiry(self):
        self.hit()
        rifle = self.b.body_script.component.instances[11]
        before = rifle.vars[53].value()
        for _ in range(30):
            self.run.step({self.b: (-32768, 0, FIRE)})
        self.assertTrue(self.b.body_script.component.input_suppressed)
        self.assertEqual(rifle.vars[53].value(), before)
        self.assertEqual(self.b.stats.value(stats.HERO_DAMAGE), 0)
        for _ in range(15):
            self.run.step({self.b: (-32768, 0, FIRE)})
        self.assertFalse(self.b.body_script.component.input_suppressed)
        self.assertLess(rifle.vars[53].value(), before)
        self.assertGreater(self.b.stats.value(stats.HERO_DAMAGE), 0)

    def test_cooldown_rejects_another_charge_then_accepts_a_new_press(self):
        self.hit()
        self.settle(70)
        instance = self.a.body_script.component.instances[5]
        self.assertGreater(instance.state(0).remaining(), 0)
        activation = self.fight.punches.dashes[self.a.body].activation
        self.charge(20)
        self.settle(300)
        self.assertEqual(self.fight.punches.dashes[self.a.body].activation, activation)
        self.assertEqual(instance.state(0).remaining(), 0)
        x, y, z = self.a.mover.position
        self.place_player(self.b, (x, y, z + 6))
        self.charge(1)
        self.settle(30)
        self.assertNotEqual(self.fight.punches.dashes[self.a.body].activation, activation)
        self.assertEqual(self.vitals().total, 100)

    def test_allies_are_not_damaged_controlled_or_used_for_shields(self):
        self.match.game_map = self.match.game_map.__class__(**{
            **self.match.game_map.__dict__, "free_for_all": False,
        })
        self.b.team = self.a.team
        self.charge(90)
        self.settle(90)
        self.assertEqual(self.vitals().total, 200)
        self.assertFalse(self.fight.punches.controlled(self.b.body))
        own = next(t for t in self.fight._targets() if t.owner is self.a)
        self.assertEqual(self.fight.vitals_of(own).temporary_shields, 0)

    def test_duplicate_release_does_not_repeat_contact(self):
        self.hit()
        self.a.take_commands([Command(self.run.frame, buttons=0)])
        self.settle(60)
        self.assertEqual(self.vitals().total, 150)
        self.assertEqual(len(self.a.client.messages(combat.HIT)), 1)

    def test_death_while_charging_prevents_dash_and_damage(self):
        self.charge(20)
        target = next(t for t in self.fight._targets() if t.owner is self.a)
        self.fight._damage(self.b, target, 1000, False, None, self.run.frame)
        self.settle(60)
        self.assertNotIn(self.a.body, self.fight.punches.dashes)
        self.assertEqual(self.vitals().total, 200)

    def test_primary_cancel_during_charge_has_no_dash_or_damage_and_starts_cooldown(self):
        before = tuple(self.a.mover.position)
        self.charge(20)
        self.run.step({self.a: (0, 0, FIRE)})
        self.settle(70)
        self.assertEqual(tuple(self.a.mover.position), before)
        self.assertEqual(self.vitals().total, 200)
        self.assertNotIn(self.a.body, self.fight.punches.dashes)
        self.assertGreater(self.a.body_script.component.instances[5].state(0).remaining(), 0)

    def test_holding_past_charge_cap_auto_releases_only_once(self):
        self.charge(400)
        self.assertEqual(self.vitals().total, 100)
        self.assertEqual(len(self.a.client.messages(combat.HIT)), 1)
        self.assertTrue(self.fight.punches.dashes[self.a.body].finished)

    def test_contact_interrupts_enemy_doomfist_charge(self):
        self.match.switch_hero(self.b, heroes()[DOOMFIST])
        x, y, z = self.a.mover.position
        self.place_player(self.b, (x, y, z + 6))
        self.charge(1)
        for _ in range(30):
            self.run.step({self.b: (0, 0, 2)})
            if self.fight.punches.controlled(self.b.body):
                break
        ability = self.b.body_script.component.instances[5].state(0)
        self.assertTrue(self.fight.punches.controlled(self.b.body))
        self.assertFalse(ability.flags & 1)
        self.assertGreater(ability.remaining(), 0)
