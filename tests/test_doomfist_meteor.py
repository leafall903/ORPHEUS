"""Meteor acceptance with real neutral input charging; no direct resource writes."""

import math
import unittest
from unittest.mock import patch

from test_doomfist_combat import DoomfistFixture
from test_game_combat import aim, in_world
from test_game_mover import box

from ow174.game import combat, mei_frost, sombra_hack
from ow174.game.collision import World
from ow174.game.commands import Command
from ow174.game.content import heroes
from ow174.game.script import nodes


def flat_meteor_damage(horizontal, mover):
    """Expected damage for a standing target on the same floor as the caster."""
    radius = mover.stand_radius
    axis_y = min(radius, mover.stand_height / 2)
    surface = math.hypot(horizontal, max(axis_y - .1, 0.)) - radius
    if surface > 8.:
        return 0.
    if surface <= 2.:
        return 300.
    return 15. + (8. - surface) * (185. / 6.)


def charge_by_neutral_commands(fixture):
    """Admit every neutral pose/body frame in normal32-command packet batches.

    Only the scheduler cadence is batched while this fresh scene is idle. No
    charge, cooldown, rate, ability flag or component clock is assigned here.
    """
    script = fixture.a.body_script
    before = fixture.run.frame
    while (script.component.entity_var(342).value()
           < script.component.entity_var(338).value()):
        if fixture.run.frame - before > 3800:
            raise AssertionError('normal neutral commands failed to reach full charge')
        start = fixture.run.frame + 1
        fixture.run.frame += 32
        fixture.match.update(fixture.run.frame * .016, fixture.run.frame)
        for player in fixture.run.players:
            player.take_commands([Command(frame) for frame in range(start, fixture.run.frame + 1)])
    return fixture.run.frame - before


class MeteorFixture(DoomfistFixture):
    def ready(self, distance=1):
        self.prepare(distance)
        frames = charge_by_neutral_commands(self)
        if not 3740 <= frames <= 3800:
            raise AssertionError(f'unexpected charge duration {frames}')

    def ultimate(self, confirm=True):
        self.run.step({self.a: (0, 0, 0x20)})
        self.settle(70)
        if confirm:
            self.run.step({self.a: (0, 0, 1)})
        self.settle(110 if confirm else 330)


class MeteorTests(MeteorFixture, unittest.TestCase):
    def test_meteor_equal_metrics_use_the_shortened_array_in_candidate_order(self):
        self.ready()
        players = [self.b]
        for index in (3, 4):
            player = self.match.add_player(index, f'Target {index}', self.b.hero.guid, index - 1, False)
            in_world(self.match, player)
            self.match.switch_hero(player, self.b.hero)
            self.run.players.append(player)
            players.append(player)

        x, y, z = self.a.mover.position
        world = World(box(x - 20, x + 20, y - 1, y, z - 20, z + 20))
        self.match.collision_world = world
        self.a.mover.use(world=world)
        for player, (dx, dz) in zip(players, ((-2.25, 0.), (2.25, 0.), (0., 5.5)), strict=True):
            player.mover.use(world=world)
            self.place_player(player, (x + dx, y, z + dz))
            target = next(t for t in self.fight._targets() if t.owner is player)
            pool = self.fight.vitals_of(target)
            pool.now[combat.HEALTH_POOL] = pool.most[combat.HEALTH_POOL] = 1000
            player.body_script.set_health(1000, 1000)

        self.ultimate()

        cast = self.fight.meteors.casts[self.a.body]
        instance = self.fight.meteors.instance(self.a)
        ids = tuple(player.body for player in players)
        self.assertEqual(cast.hit, ids)
        self.assertEqual(tuple(entity.id for entity in instance.vars[375].value()), ids)
        self.assertEqual(len(instance.vars[9326].value()), 2)
        outer = flat_meteor_damage(5.5, self.b.mover.data)
        self.assertEqual(len(cast.damage), 3)
        for (entity, requested, dealt), player, amount in zip(cast.damage, players, (300., outer, 300.), strict=True):
            self.assertEqual(entity, player.body)
            self.assertAlmostEqual(requested, amount, delta=.01)
            self.assertAlmostEqual(dealt, amount, delta=.01)
            self.assertAlmostEqual(self.fight.vitals[player.body].total, 1000. - amount, delta=.01)
        self.assertTrue(cast.finished)

    def test_meteor_range_includes_capsule_beyond_eight_meter_feet_distance(self):
        for offset in (-.01, .01):
            with self.subTest(offset=offset):
                self.ready()
                body = self.b.mover.data
                radius = body.stand_radius
                axis_y = min(radius, body.stand_height / 2)
                vertical = max(axis_y - .1, 0.)
                edge = math.sqrt((8. + radius) ** 2 - vertical ** 2)
                distance = edge + offset
                self.assertGreater(distance, 8.)
                x, y, z = self.a.mover.position
                world = World(box(x - 20, x + 20, y - 1, y, z - 20, z + 20))
                self.match.collision_world = world
                for player in self.match.players:
                    player.mover.use(world=world)
                self.place_player(self.b, (x, y, z + distance))

                self.ultimate()

                cast = self.fight.meteors.casts[self.a.body]
                self.assertTrue(cast.finished)
                if offset > 0:
                    self.assertEqual(cast.hit, ())
                    self.assertEqual(self.vitals().total, 200)
                else:
                    self.assertEqual(cast.hit, (self.b.body,), (cast.target, self.b.mover.position, radius))
                    self.assertAlmostEqual(200 - self.vitals().total, flat_meteor_damage(distance, body), delta=.01)

    def test_actual_hand_cannon_charge_counts_health_lost_and_clamps_overkill(self):
        def charge():
            return self.a.body_script.component.entity_var(342).value()

        self.prepare(3)
        self.settle(100)
        neutral = charge()
        self.prepare(3)
        for _ in range(4):
            self.fire()
            self.settle(24)
        self.assertEqual(self.vitals().total, 0)
        self.assertAlmostEqual(charge() - neutral, 200, places=2)

    def test_actual_miss_earns_only_neutral_charge(self):
        self.prepare(3)
        self.settle(25)
        neutral = self.a.body_script.component.entity_var(342).value()
        self.prepare(3)
        self.run.step({self.a: (32768, 0, 1)})
        self.settle(24)
        self.assertEqual(self.vitals().total, 200)
        self.assertEqual(self.a.body_script.component.entity_var(342).value(), neutral)

    def test_released_hand_cannon_damages_after_body_replacement_without_crediting_new_body(self):
        charges = []
        for fired in (False, True):
            self.prepare(6)
            if fired:
                self.fire()
            else:
                self.settle(1)
            old_body = self.a.body
            self.match.switch_hero(self.a, self.a.hero)
            self.assertNotEqual(self.a.body, old_body)
            self.settle(12)
            self.assertEqual(self.vitals().total < 200, fired)
            charges.append(self.a.body_script.component.entity_var(342).value())
        self.assertEqual(*charges)

    def test_released_hand_cannon_damage_survives_meteor_selection_and_recovery(self):
        for distance in (3, 250):
            with self.subTest(distance=distance):
                self.ready(distance)
                self.fire()
                self.assertEqual(len(self.fight.rockets), 11)
                if distance == 250:
                    self.align_target_with_first_pellet()
                self.run.step({self.a: (0, 0, 0x20)})
                self.assertTrue(self.fight.meteors.protected(self.a.body))
                cast = self.fight.meteors.casts[self.a.body]
                self.settle(70)
                self.assertFalse(cast.impacted)
                self.assertEqual(self.a.body_script.component.entity_var(342).value(), 1680)
                if distance == 3:
                    self.assertEqual(self.vitals().total, 134)
                    self.assertEqual(len(self.a.client.messages(combat.HIT)), 11)
                else:
                    self.assertEqual(self.vitals().total, 200)
                    self.run.step({self.a: (0, 0, 1)})
                    self.settle(110)
                    self.assertTrue(cast.finished)
                    self.assertFalse(cast.hit)
                    self.assertEqual(self.vitals().total, 200)
                    charge = self.charge_checkpoint()
                    self.settle(260)
                    self.assertAlmostEqual(self.vitals().total, 198.2, places=6)
                    self.assertEqual(len(self.a.client.messages(combat.HIT)), 1)
                    self.assert_damage_charge(charge, 1.8)

    def test_actual_passive_shield_loss_is_excluded_from_hand_cannon_charge(self):
        self.prepare(1.5)
        self.match.switch_hero(self.b, self.a.hero)
        x, y, z = self.a.mover.position
        self.place_player(self.b, (x, y, z + 1.5))
        yaw, pitch = aim(self.fight._eye(self.b), (x, y + 1, z))
        self.run.step({self.b: (yaw, pitch, 8)})
        self.settle(4)
        vitals = self.vitals()
        self.assertEqual(vitals.temporary_shields, 30)
        self.place_player(self.a, (x, y, z))
        self.place_player(self.b, (x, y, z + 1.5))
        charge = self.a.body_script.component.entity_var(342)
        start = charge.value()
        self.fire()
        self.settle(8)
        self.assertEqual(vitals.temporary_shields, 0)
        health_loss = 250 - vitals.total
        self.assertGreater(health_loss, 0)
        self.assertAlmostEqual(charge.value() - start, health_loss + 9 * .448, places=3)

    def test_actual_charge_gate_cost_inner_damage_passive_and_end(self):
        self.prepare(1)
        self.run.step({self.a: (0, 0, 0x20)})
        instance = next(i for i in self.a.body_script.component.instances.values() if i.graph.index == 0xC51)
        self.assertFalse(instance.state(25).flags & 1)
        charge_by_neutral_commands(self)
        self.assertEqual(instance.component.entity_var(342).value(), 1680)
        self.vitals().now[combat.HEALTH_POOL] = self.vitals().most[combat.HEALTH_POOL] = 1000
        self.b.body_script.set_health(1000, 1000)
        self.ultimate()
        cast = self.fight.meteors.casts[self.a.body]
        self.assertTrue(cast.impacted)
        self.assertTrue(cast.finished)
        self.assertEqual(self.vitals().total, 700)
        self.assertFalse(instance.state(25).flags & 1)
        self.assertLess(instance.component.entity_var(342).value(), 30)
        self.assertEqual(len(self.a.client.messages(combat.HIT)), 1)
        own = next(t for t in self.fight._targets() if t.owner is self.a)
        self.assertGreater(self.fight.vitals_of(own).temporary_shields, 70)

    def test_outer_falloff_and_miss_use_authored_distance_expression(self):
        timelines = []
        for distance in (5, 8, 9):
            with self.subTest(distance=distance):
                self.ready(distance)
                damage = flat_meteor_damage(distance, self.b.mover.data)
                charge = self.a.body_script.component.entity_var(342)
                self.run.step({self.a: (0, 0, 0x20)})
                self.settle(70)
                self.assertEqual(charge.value(), 1680)
                self.run.step({self.a: (0, 0, 1)})
                timeline = [charge.value()]
                cast = self.fight.meteors.casts[self.a.body]
                impact_frame = None
                for frame in range(110):
                    self.run.step({})
                    timeline.append(charge.value())
                    if cast.impacted and impact_frame is None:
                        impact_frame = frame
                        self.assertAlmostEqual(200 - self.vitals().total, damage, places=3)
                self.assertIsNotNone(impact_frame)
                self.assertAlmostEqual(min(timeline), .448)
                self.assertGreater(timeline[-1], 0)
                timelines.append((impact_frame, timeline))
                self.assertAlmostEqual(200 - self.vitals().total, damage, places=3)
                cast = self.fight.meteors.casts[self.a.body]
                self.assertTrue(cast.finished)
                self.assertEqual(len(cast.hit), int(damage > 0))
                own = next(t for t in self.fight._targets() if t.owner is self.a)
                self.assertEqual(self.fight.vitals_of(own).temporary_pool_used, damage > 0)
                self.assertLess(self.a.body_script.component.entity_var(342).value(), 30)
        self.assertEqual(timelines[0], timelines[1])
        self.assertEqual(timelines[0], timelines[2])

    def test_maximum_selection_time_auto_impacts_and_cannot_recast_below_cost(self):
        self.ready(1)
        self.run.step({self.a: (0, 0, 0x20)})
        cast = self.fight.meteors.casts[self.a.body]
        activation = cast.activation
        for _ in range(520):
            self.run.step({self.a: (0, 0, 0x20)})
            if cast.finished:
                break
        self.assertTrue(cast.finished, (cast.committed, cast.impacted, cast.activation))
        self.assertTrue(cast.impacted)
        self.assertEqual(self.vitals().total, 0)
        self.assertEqual(len(self.a.client.messages(combat.HIT)), 1)
        charge = self.charge_checkpoint()
        self.assertLess(charge[1], 30)
        for _ in range(30):
            self.run.step({self.a: (0, 0, 0x20)})
        self.run.step({})
        self.run.step({self.a: (0, 0, 0x20)})
        self.assertIs(self.fight.meteors.casts[self.a.body], cast)
        self.assertEqual(cast.activation, activation)
        self.assertEqual(len(self.a.client.messages(combat.HIT)), 1)
        self.assert_damage_charge(charge, 0)

    def test_ally_and_static_wall_miss_still_spend_charge_and_end(self):
        for wall in (False, True):
            with self.subTest(wall=wall):
                self.ready(3)
                if wall:
                    x, y, z = self.a.mover.position
                    world = World(box(x - 20, x + 20, y - 1, y, z - 20, z + 20)
                                  + box(x - 10, x + 10, y, y + 6, z + 1, z + 1.2))
                    self.match.collision_world = world
                    for player in self.match.players:
                        player.mover.use(world=world)
                else:
                    self.match.game_map = self.match.game_map.__class__(**{
                        **self.match.game_map.__dict__, 'free_for_all': False})
                    self.b.team = self.a.team
                self.ultimate()
                self.assertEqual(self.vitals().total, 200)
                self.assertTrue(self.fight.meteors.casts[self.a.body].finished)
                self.assertLess(self.a.body_script.component.entity_var(342).value(), 30)
                own = next(t for t in self.fight._targets() if t.owner is self.a)
                self.assertEqual(self.fight.vitals_of(own).temporary_shields, 0)

    def test_meteor_hits_over_low_static_cover_with_upper_visibility_ray(self):
        self.ready(4)
        x, y, z = self.a.mover.position
        world = World(box(x - 20, x + 20, y - 1, y, z - 20, z + 20)
                      + box(x - 10, x + 10, y, y + .7, z + 2, z + 2.1))
        self.match.collision_world = world
        for player in self.match.players:
            player.mover.use(world=world)

        self.ultimate()

        cast = self.fight.meteors.casts[self.a.body]
        self.assertEqual(cast.hit, (self.b.body,))
        self.assertAlmostEqual(200 - self.vitals().total, flat_meteor_damage(4, self.b.mover.data), places=3)
        self.assertTrue(cast.finished)
        self.assertLess(self.a.body_script.component.entity_var(342).value(), 30)

    def test_real_rifle_cannot_target_selection_but_hits_after_landing(self):
        self.ready(12)
        own = next(t for t in self.fight._targets() if t.owner is self.a)
        vitals = self.fight.vitals_of(own)
        before = vitals.total
        self.run.step({self.a: (0, 0, 0x20)})
        for _ in range(30):
            yaw, pitch = aim(self.fight._eye(self.b), self.fight._eye(self.a))
            self.run.step({self.b: (yaw, pitch, 1)})
        self.assertEqual(vitals.total, before)
        self.assertTrue(self.fight.meteors.protected(self.a.body))
        self.settle(40)
        self.run.step({self.a: (0, 0, 1)})
        self.settle(100)
        self.assertFalse(self.fight.meteors.protected(self.a.body))
        yaw, pitch = aim(self.fight._eye(self.b), self.fight._eye(self.a))
        self.run.step({self.b: (yaw, pitch, 1)})
        self.assertLess(vitals.total, before)

    def test_actual_flashbang_is_rejected_in_selection_and_controls_outside_it(self):
        for protected in (False, True):
            with self.subTest(meteor=protected):
                if protected:
                    self.ready(3)
                else:
                    self.prepare(3)
                self.match.switch_hero(self.b, heroes()[0x02E0000000000042])
                x, y, z = self.a.mover.position
                self.place_player(self.b, (x, y, z + 3))
                if protected:
                    self.run.step({self.a: (0, 0, 0x20)})
                    self.settle(70)
                own = next(t for t in self.fight._targets() if t.owner is self.a)
                vitals = self.fight.vitals_of(own)
                before = vitals.total
                yaw, pitch = aim(self.fight._eye(self.b), (x, y + 1, z))
                self.run.step({self.b: (yaw, pitch, 16)})
                self.settle(20)
                self.assertEqual(before - vitals.total, 0 if protected else 25)
                self.assertEqual(self.fight.is_stunned(self.a), not protected)

    def test_non_primary_combat_inputs_do_not_cancel_meteor_selection(self):
        self.ready(5)
        weapon = next(i for i in self.a.body_script.component.instances.values()
                      if i.graph.index == 0xD0A)
        ammo = weapon.vars[53].value()
        charge = self.a.body_script.component.entity_var(342)
        self.run.step({self.a: (0, 0, 0x20)})
        self.settle(70)
        cast = self.fight.meteors.casts[self.a.body]
        self.assertTrue(self.fight.meteors.instance(self.a).state(18).active)
        for button in (0x2, 0x8, 0x10, 0x20, 0x800):
            with self.subTest(button=button):
                for _ in range(12):
                    self.run.step({self.a: (0, 0, button)})
                self.run.step({})
                self.assertIs(self.fight.meteors.casts[self.a.body], cast)
                self.assertTrue(self.fight.meteors.protected(self.a.body))
                self.assertFalse(cast.committed or cast.impacted or cast.finished)
                self.assertNotIn(self.a.body, self.fight.punches.dashes)
                self.assertNotIn(self.a.body, self.fight.slams.casts)
                self.assertEqual(self.vitals().total, 200)
                self.assertEqual(weapon.vars[53].value(), ammo)
                self.assertEqual(charge.value(), 1680)
                self.assertFalse(self.a.client.messages(combat.HIT))
        self.run.step({self.a: (0, 0, 1)})
        self.settle(110)
        self.assertTrue(cast.finished)
        self.assertEqual(cast.hit, (self.b.body,))
        expected = flat_meteor_damage(5, self.b.mover.data)
        self.assertAlmostEqual(self.vitals().total, 200 - expected, places=3)
        self.assertEqual(weapon.vars[53].value(), ammo)
        self.assertEqual(len(self.a.client.messages(combat.HIT)), 1)
        self.assertLess(charge.value(), 30)

    def test_confirmation_does_not_spend_ammo_or_duplicate_damage(self):
        self.ready(3)
        weapon = next(i for i in self.a.body_script.component.instances.values() if i.graph.index == 0xD0A)
        ammo = weapon.vars[53].value()
        self.ultimate()
        before = self.vitals().total
        self.a.take_commands([Command(self.run.frame, buttons=1)])
        self.settle(20)
        self.assertEqual(self.vitals().total, before)
        self.assertEqual(weapon.vars[53].value(), ammo)
        self.assertEqual(len(self.a.client.messages(combat.HIT)), 1)

    def test_actual_throttle_moves_selection_and_landing_uses_frozen_target(self):
        self.ready(10)
        self.run.step({self.a: (0, 0, 32)})
        self.settle(70)
        cast = self.fight.meteors.casts[self.a.body]
        initial = cast.target
        for _ in range(30):
            self.run.frame += 1
            self.match.update(self.run.frame * .016, self.run.frame)
            self.a.take_commands([Command(self.run.frame, forward=127)])
            self.b.take_commands([Command(self.run.frame)])
        self.assertGreater(cast.target[2], initial[2] + 1)
        self.run.step({self.a: (0, 0, 1)})
        target = cast.target
        self.settle(110)
        self.assertEqual(cast.target, target)
        self.assertAlmostEqual(self.a.mover.position[2], target[2], delta=.01)
        self.assertLess(self.vitals().total, 200)

    def test_resource_recharges_normally_and_fresh_input_starts_second_cast(self):
        self.ready(9)
        self.ultimate()
        old = self.fight.meteors.casts[self.a.body].activation
        charge_by_neutral_commands(self)
        x, y, z = self.a.mover.position
        self.place_player(self.b, (x, y, z + 1))
        self.ultimate()
        self.assertNotEqual(self.fight.meteors.casts[self.a.body].activation, old)
        self.assertEqual(self.vitals().total, 0)

    def test_body_replacement_during_selection_retires_pending_impact(self):
        self.ready(1)
        self.run.step({self.a: (0, 0, 32)})
        self.settle(70)
        body = self.a.body
        old_script = self.a.body_script
        doomfist = self.a.hero
        self.assertTrue(self.fight.meteors.protected(body))
        self.assertEqual(old_script.component.entity_var(342).value(), 1680)
        self.match.switch_hero(self.a, heroes()[0x02E000000000006E])
        self.assertTrue(old_script.retired)
        self.assertNotEqual(self.a.body, body)
        self.match.switch_hero(self.a, doomfist)
        self.assertNotEqual(self.a.body, body)
        self.assertEqual(self.a.body_script.component.entity_var(342).value(), 0)
        self.assertFalse(self.fight.meteors.protected(self.a.body))
        charge = self.charge_checkpoint()
        self.run.step({self.a: (0, 0, 32)})
        self.assertNotIn(self.a.body, self.fight.meteors.casts)
        self.settle(400)
        self.assertNotIn(body, self.fight.meteors.casts)
        self.assertNotIn(self.a.body, self.fight.meteors.casts)
        self.assertFalse(self.fight.meteors.protected(body))
        self.assertEqual(self.vitals().total, 200)
        self.assertFalse(self.a.client.messages(combat.HIT))
        self.assert_damage_charge(charge, 0)


class MeteorPlayerLifecycleTests(MeteorFixture, unittest.TestCase):
    def start_descent(self):
        self.run.step({self.a: (0, 0, 32)})
        self.settle(70)
        cast = self.fight.meteors.casts[self.a.body]
        self.run.step({self.a: (0, 0, 1)})
        for _ in range(12):
            if cast.committed:
                break
            self.run.step({})
        self.assertTrue(cast.committed)
        self.assertFalse(cast.impacted)
        self.assertTrue(self.fight.meteors.protected(self.a.body))
        return cast

    def land(self, cast):
        for _ in range(90):
            if cast.impacted:
                break
            self.run.step({})
        self.assertTrue(cast.impacted)
        self.assertFalse(self.fight.meteors.protected(self.a.body))

    def test_lethal_meteor_does_not_launch_player_or_replay_impact(self):
        self.ready(1)
        victim = self.vitals()
        position = tuple(self.b.mover.position)
        cast = self.start_descent()
        self.land(cast)
        self.assertEqual(cast.damage, ((self.b.body, 300, 200),))
        self.assertTrue(victim.dead)
        self.assertEqual(victim.killer, self.a.body)
        self.settle(90)
        self.assertTrue(cast.finished)
        self.assertEqual(tuple(self.b.mover.position), position)
        hit, = self.a.client.messages(combat.HIT)
        self.assertEqual(hit["+0x78"]["+0x4"], 200)

    def test_lethal_enemy_rifle_is_blocked_in_descent_then_kills_without_replay(self):
        self.check_rifle_death_and_respawn_after_meteor(distance=12)

    def test_enemy_rifle_kills_during_meteor_recovery_after_player_hit(self):
        self.check_rifle_death_and_respawn_after_meteor(distance=5)

    def check_rifle_death_and_respawn_after_meteor(self, *, distance):
        self.ready(distance)
        damage = flat_meteor_damage(distance, self.b.mover.data)
        own = self.fight.vitals[self.a.body]
        own.now[combat.HEALTH_POOL] = 15
        cast = self.start_descent()
        yaw, pitch = aim(self.fight._eye(self.b), self.fight._eye(self.a))
        self.run.step({self.b: (yaw, pitch, 1)})
        self.assertEqual(own.total, 15)
        self.assertFalse(cast.impacted)
        self.land(cast)
        old_body = self.a.body
        old_charge = self.a.body_script.component.entity_var(342)
        self.assertEqual(old_charge.value(), 1680)
        ability = self.fight.meteors.instance(self.a).state(25)
        self.assertTrue(ability.flags & 1)
        expected_hit = (self.b.body,) if damage else ()
        self.assertEqual(cast.hit, expected_hit)
        victim_health = self.vitals().total
        self.assertAlmostEqual(victim_health, 200 - damage, places=3)
        self.assertEqual(own.temporary_shields, 75 if damage else 0)
        for _ in range(30):
            if own.dead:
                break
            self.assertTrue(ability.flags & 1)
            yaw, pitch = aim(self.fight._eye(self.b), self.fight._eye(self.a))
            self.run.step({self.b: (yaw, pitch, 1)})
        self.assertTrue(own.dead)
        self.assertTrue(cast.finished)
        self.assertEqual(own.temporary_shields, 0)
        self.assertEqual(own.killer, self.b.body)
        self.assertLessEqual(old_charge.value(), .448001)
        self.assertFalse(self.fight.meteors.protected(old_body))
        x, y, z = cast.target
        self.place_player(self.b, (x, y, z + 1))
        self.settle(110)
        self.assertEqual(cast.hit, expected_hit)
        self.assertEqual(self.vitals().total, victim_health)
        self.assertEqual(len(self.a.client.messages(combat.HIT)), len(expected_hit))

        respawn_at = next(when for when, player in self.fight.respawns if player is self.a)
        self.assertAlmostEqual(respawn_at - own.died_at, 10)
        while (self.run.frame + 1) * combat.TICK < respawn_at:
            self.run.step({})
            self.assertEqual(self.a.body, old_body)
        for _ in range(2):
            self.run.step({})
            if self.a.body != old_body:
                break
        self.assertNotEqual(self.a.body, old_body)
        self.assertNotIn(old_body, self.fight.meteors.casts)
        self.assertEqual(self.fight.vitals[self.a.body].total, 250)
        fresh_charge = self.a.body_script.component.entity_var(342)
        self.assertLessEqual(fresh_charge.value(), .448001)
        charge = self.charge_checkpoint()
        self.run.step({self.a: (0, 0, 32)})
        self.assertNotIn(self.a.body, self.fight.meteors.casts)
        self.assert_damage_charge(charge, 0)
        self.assertEqual(self.vitals().total, victim_health)

    def test_sleep_dart_finishes_meteor_recovery_while_owner_is_asleep(self):
        self.ready(5)
        self.match.switch_hero(self.b, heroes()[0x02E000000000013B])
        x, y, z = self.a.mover.position
        self.place_player(self.b, (x, y, z + 5))
        body = self.a.body
        owner = self.fight.vitals[body]
        victim = self.vitals()
        cast = self.start_descent()
        self.land(cast)
        self.assertFalse(cast.finished)
        self.assertEqual(cast.hit, (self.b.body,))
        self.assertEqual(owner.temporary_shields, 75)
        damage, victim_health = cast.damage, victim.total
        hit, = self.a.client.messages(combat.HIT)
        ability = self.fight.meteors.instance(self.a).state(25)
        self.assertTrue(ability.flags & nodes.Ability.RUNNING)
        # Meteor displaced Ana. Restore the fixture spacing for the incoming shot.
        self.place_player(self.b, (x, y, z + 5))
        yaw, pitch = aim(self.fight._eye(self.b), self.fight._eye(self.a))
        self.run.step({self.b: (yaw, pitch, 8)})
        for _ in range(45):
            if body in self.fight.ana_sleeps:
                break
            self.assertFalse(cast.finished)
            self.run.step({self.b: (yaw, pitch, 0)})
        self.assertIn(body, self.fight.ana_sleeps)
        self.assertTrue(self.fight.is_stunned(self.a))
        self.assertFalse(ability.flags & nodes.Ability.RUNNING)
        self.assertTrue(cast.finished)
        self.assertEqual(owner.now[combat.HEALTH_POOL], 250)
        self.assertEqual(owner.temporary_shields, 70)
        self.assertFalse(self.fight.meteors.protected(body))
        self.settle(80)
        self.assertTrue(self.fight.is_stunned(self.a))
        self.assertIs(self.fight.meteors.casts[body], cast)
        self.assertTrue(cast.finished)
        self.assertEqual(cast.damage, damage)
        self.assertEqual(victim.total, victim_health)
        self.assertEqual(self.a.client.messages(combat.HIT), [hit])

    def test_sleep_dart_and_frost_spray_are_rejected_during_meteor_descent(self):
        for hero, button, effect, held in (
                (0x02E000000000013B, 8, combat.ANA_SLEEP_EFFECT, False),
                (0x02E00000000000DD, 1, mei_frost.SPRAY_EFFECT, True)):
            with self.subTest(hero=hero):
                self.ready(5)
                self.match.switch_hero(self.b, heroes()[hero])
                x, y, z = self.a.mover.position
                self.place_player(self.b, (x, y, z + 5))
                body = self.a.body
                own = self.fight.vitals[body]
                cast = self.start_descent()
                yaw, pitch = aim(self.fight._eye(self.b), self.fight._eye(self.a))
                with patch.object(self.fight, '_shoot', wraps=self.fight._shoot) as shots:
                    for frame in range(45):
                        self.run.step({self.b: (yaw, pitch, button if held or frame == 0 else 0)})
                        self.assertTrue(self.fight.protected(body))
                        self.assertFalse(cast.impacted)
                        self.assertEqual(own.total, 250)
                        self.assertNotIn(body, self.fight.ana_sleeps)
                        self.assertNotIn(body, self.fight.frost)
                    definitions = [combat.hit_def(state, state.node.field('m_6396149F'))
                                   for shooter, _, state, *_ in (call.args for call in shots.call_args_list)
                                   if shooter is self.b]
                self.assertTrue(any(hit is not None and hit.effect == effect for hit in definitions))
                self.place_player(self.b, (x, y, z + 12))
                self.land(cast)
                self.assertFalse(cast.hit)
                self.assertEqual(own.total, 250)
                self.assertNotIn(body, self.fight.ana_sleeps)
                self.assertNotIn(body, self.fight.frost)

    def test_hack_cannot_acquire_during_descent_but_retries_after_meteor_impact(self):
        self.ready(10)
        self.match.switch_hero(self.b, heroes()[sombra_hack.SOMBRA])
        x, y, z = self.a.mover.position
        self.place_player(self.b, (x, y, z + 10))
        cast = self.start_descent()
        yaw, pitch = aim(self.fight._eye(self.b), self.fight._eye(self.a))
        query = sombra_hack.query
        results = []

        def observe(fight, state):
            result = query(fight, state)
            if state.instance.component.entity == self.b.body and state.node.pos == 50:
                results.append(result)
            return result

        with patch.object(sombra_hack, 'query', new=observe):
            for _ in range(45):
                self.run.step({self.b: (yaw, pitch, 2)})
                self.assertTrue(self.fight.protected(self.a.body))
                self.assertFalse(cast.impacted)
                self.assertIsNone(sombra_hack.instance_for(self.a, 0x0B1F))
        self.assertTrue(results)
        self.assertTrue(all(result == () for result in results))
        self.land(cast)
        self.assertFalse(cast.hit)
        self.assertIsNone(sombra_hack.instance_for(self.a, 0x0B1F))

        for _ in range(65):
            self.run.step({self.b: (yaw, pitch, 2)})
            if sombra_hack.locked(self.a):
                break
        self.assertTrue(sombra_hack.locked(self.a))
        receiver = sombra_hack.instance_for(self.a, 0x0B1F)
        self.assertEqual(receiver.states[58].ms, 5000)
        self.assertEqual(self.fight.vitals[self.a.body].total, 250)

    def test_flashbang_expires_without_damage_or_stun_during_meteor_descent(self):
        self.ready(3)
        self.match.switch_hero(self.b, heroes()[0x02E0000000000042])
        x, y, z = self.a.mover.position
        self.place_player(self.b, (x, y, z + 3))
        own = self.fight.vitals[self.a.body]
        cast = self.start_descent()
        yaw, pitch = aim(self.fight._eye(self.b), (x, y + 1, z))
        with patch.object(self.fight, '_landed', wraps=self.fight._landed) as landed:
            self.run.step({self.b: (yaw, pitch, 16)})
            for _ in range(20):
                self.run.step({})
                self.assertTrue(self.fight.protected(self.a.body))
                self.assertFalse(cast.impacted)
                self.assertEqual(own.total, 250)
                self.assertFalse(self.fight.is_stunned(self.a))
            flashbang, = (call.args[0] for call in landed.call_args_list
                          if call.args[0].shooter is self.b)
        self.assertEqual(flashbang.blast.effect, combat.FLASHBANG_EFFECT)
        self.assertNotIn(flashbang, self.fight.rockets)
        self.place_player(self.b, (x, y, z + 12))
        self.land(cast)
        self.assertFalse(cast.hit)
        self.assertEqual(own.total, 250)
        self.assertFalse(self.fight.is_stunned(self.a))

    def test_inflight_flashbang_hits_on_first_update_after_meteor_impact(self):
        self.ready(3)
        self.match.switch_hero(self.b, heroes()[0x02E0000000000042])
        x, y, z = self.a.mover.position
        self.place_player(self.b, (x, y, z + 3))
        own = self.fight.vitals[self.a.body]
        cast = self.start_descent()
        ability = self.fight.meteors.instance(self.a).state(25)
        # Release late enough for the projectile to still be in flight at impact.
        self.settle(57)
        yaw, pitch = aim(self.fight._eye(self.b), (x, y + 1, z))
        self.run.step({self.b: (yaw, pitch, 16)})
        flashbang, = (rocket for rocket in self.fight.rockets if rocket.shooter is self.b)
        self.assertEqual(flashbang.blast.effect, combat.FLASHBANG_EFFECT)
        self.assertTrue(self.fight.protected(self.a.body))
        self.assertFalse(cast.impacted)

        # Move the thrower clear of Meteor while keeping the released trajectory.
        self.place_player(self.b, (x, y, z + 12))
        self.land(cast)
        self.assertFalse(cast.hit)
        self.assertIn(flashbang, self.fight.rockets)
        self.assertEqual(own.total, 250)
        self.assertFalse(self.fight.is_stunned(self.a))
        self.assertTrue(ability.flags & 1)

        self.run.step({})
        self.assertNotIn(flashbang, self.fight.rockets)
        self.assertEqual(own.total, 225)
        self.assertTrue(self.fight.is_stunned(self.a))
        self.assertFalse(ability.flags & 1)
        self.settle(90)
        self.assertTrue(cast.finished)
        self.assertFalse(cast.hit)
        self.assertEqual(own.total, 225)

    def test_flashbang_interrupts_meteor_recovery_after_landing(self):
        self.ready(12)
        self.match.switch_hero(self.b, heroes()[0x02E0000000000042])
        x, y, z = self.a.mover.position
        self.place_player(self.b, (x, y, z + 12))
        self.run.step({})
        own = self.fight.vitals[self.a.body]
        cast = self.start_descent()
        self.land(cast)
        script = self.a.body_script
        ability = self.fight.meteors.instance(self.a).state(25)
        self.assertTrue(ability.flags & 1)
        self.assertFalse(cast.hit)
        self.place_player(self.b, (x, y, z + 2))
        yaw, pitch = aim(self.fight._eye(self.b), self.fight._eye(self.a))
        self.run.step({self.b: (yaw, pitch, 16)})
        for _ in range(15):
            if self.fight.is_stunned(self.a):
                break
            self.run.step({})
        self.assertTrue(self.fight.is_stunned(self.a))
        self.assertEqual(own.total, 225)
        self.assertFalse(ability.flags & 1)
        self.settle(90)
        self.assertTrue(cast.finished)
        self.assertFalse(cast.hit)
        self.assertLess(script.component.entity_var(342).value(), 100)
        self.assertEqual(self.vitals().total, 225)
