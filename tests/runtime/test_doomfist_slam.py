"""Ground and aerial Slam command/collision tests; native spatial fidelity is separate."""

import unittest

from test_doomfist_combat import DoomfistFixture
from test_game_mover import box

from ow174.game import combat
from ow174.game.collision import World
from ow174.game.commands import Command
from ow174.game.script import nodes


class GroundSlamTests(DoomfistFixture, unittest.TestCase):
    def setUp(self):
        self.prepare(10)

    def slam(self):
        self.run.step({self.a: (0, 0, 0x10)})

    def impact(self):
        self.slam()
        for _ in range(120):
            self.run.step({})
            if self.vitals().total < 200:
                return self.fight.slams.casts[self.a.body]
        self.fail('real ground Slam did not damage the forward target')

    def test_ground_input_launches_lands_charges_damage_and_grants_one_shield_stack(self):
        start = tuple(self.a.mover.position)
        charge = self.charge_checkpoint()
        self.slam()
        self.assertEqual(self.vitals().total, 200)
        self.settle(8)
        self.assertGreater(self.a.mover.position[1], start[1] + .2)
        self.assertGreater(self.a.mover.position[2], start[2] + 1)
        self.assertEqual(self.vitals().total, 200)
        for _ in range(100):
            self.run.step({})
            if self.vitals().total < 200:
                break
        self.settle(20)
        cast = self.fight.slams.casts[self.a.body]
        self.assertIsNotNone(cast.origin)
        self.assertTrue(cast.finished)
        self.assertGreater(cast.damage, 25)
        self.assertLessEqual(cast.damage, 125)
        self.assertAlmostEqual(self.vitals().total, 200 - cast.damage)
        self.assert_damage_charge(charge, cast.damage)
        own = next(t for t in self.fight._targets() if t.owner is self.a)
        self.assertEqual(self.fight.vitals_of(own).temporary_shields, 30)
        self.assertEqual(len(self.a.client.messages(combat.HIT)), 1)

        ability = self.a.body_script.component.instances[8].state(0)
        self.assertFalse(ability.flags & 1)
        self.assertGreater(ability.remaining(), 0)

    def test_wave_survivor_is_pulled_toward_impact_and_lifted_then_lands(self):
        before = tuple(self.b.mover.position)
        self.impact()
        self.settle(8)
        self.assertLess(self.b.mover.position[2], before[2])
        self.assertGreater(self.b.mover.position[1], before[1] + .1)
        self.settle(150)
        self.assertFalse(self.b.mover.airborne)
        self.assertAlmostEqual(self.b.mover.position[1], before[1], delta=.01)

    def test_extra_airtime_increases_authored_charge_without_exceeding_cap(self):
        ground = self.impact().damage
        self.prepare(12)
        self.run.step({self.a: (0, 0, 0x40)})
        self.settle(11)
        airborne = self.impact().damage
        self.assertGreater(airborne, ground)
        self.assertLessEqual(airborne, 125)

    def test_behind_and_beyond_maximum_wave_reach_miss(self):
        for distance in (-3, 30):
            with self.subTest(distance=distance):
                self.prepare(distance)
                self.slam()
                self.settle(140)
                self.assertEqual(self.vitals().total, 200)
                self.assertEqual(len(self.a.client.messages(combat.HIT)), 0)

    def test_wall_blocks_owner_and_wave_from_damaging_enemy(self):
        x, y, z = self.a.mover.position
        world = World(box(x - 30, x + 30, y - 1, y, z - 30, z + 30)
                      + box(x - 30, x + 30, y, y + 6, z + 3, z + 4))
        self.match.collision_world = world
        for player in self.match.players:
            player.mover.use(world=world)
        self.slam()
        self.settle(150)
        self.assertLess(self.a.mover.position[2], z + 3)
        self.assertEqual(self.vitals().total, 200)

    def test_allied_target_is_not_damaged_pulled_or_used_for_shields(self):
        self.match.game_map = self.match.game_map.__class__(**{
            **self.match.game_map.__dict__, 'free_for_all': False})
        self.b.team = self.a.team
        before = tuple(self.b.mover.position)
        self.slam()
        self.settle(140)
        self.assertEqual(self.vitals().total, 200)
        self.assertEqual(tuple(self.b.mover.position), before)
        own = next(t for t in self.fight._targets() if t.owner is self.a)
        self.assertEqual(self.fight.vitals_of(own).temporary_shields, 0)

    def test_cooldown_rejects_pulse_and_accepts_new_input_after_expiry(self):
        self.impact()
        self.settle(70)
        old = self.fight.slams.casts[self.a.body].activation
        after = self.vitals().total
        self.slam()
        self.settle(380)
        self.assertEqual(self.fight.slams.casts[self.a.body].activation, old)
        self.assertEqual(self.vitals().total, after)
        self.assertEqual(self.a.body_script.component.instances[8].state(0).remaining(), 0)
        x, y, z = self.a.mover.position
        self.place_player(self.b, (x, y, z + 10))
        self.slam()
        self.settle(120)
        self.assertNotEqual(self.fight.slams.casts[self.a.body].activation, old)
        self.assertLess(self.vitals().total, after)

    def test_death_during_flight_suppresses_pending_wave(self):
        self.slam()
        self.settle(10)
        owner = next(t for t in self.fight._targets() if t.owner is self.a)
        self.fight._damage(self.b, owner, 1000, False, None, self.run.frame)
        self.settle(120)
        self.assertEqual(self.vitals().total, 200)
        self.assertEqual(len(self.a.client.messages(combat.HIT)), 0)

    def test_duplicate_command_does_not_repeat_landing_damage(self):
        self.impact()
        after = self.vitals().total
        self.a.take_commands([Command(self.run.frame, buttons=0x10)])
        self.settle(80)
        self.assertEqual(self.vitals().total, after)
        self.assertEqual(len(self.a.client.messages(combat.HIT)), 1)


class AerialSlamTests(DoomfistFixture, unittest.TestCase):
    def setUp(self):
        self.prepare_air()

    def prepare_air(self, height=8, distance=10):
        self.prepare(distance)
        x, y, z = self.a.mover.position
        self.place_player(self.a, (x, y + height, z))

    def start(self, pitch=9000):
        self.run.step({self.a: (0, pitch, 0x10)})

    def impact(self):
        self.start()
        for _ in range(100):
            self.run.step({})
            if self.vitals().total < 200:
                return self.fight.slams.casts[self.a.body]
        self.fail('aerial input did not reach the target and damage the enemy')

    def wall(self, distance, bottom=0, top=12, thickness=1):
        x, _, z = self.a.mover.position
        y = self.b.mover.position[1]
        world = World(box(x - 30, x + 30, y - 1, y, z - 30, z + 30)
                      + box(x - 30, x + 30, y + bottom, y + top,
                            z + distance, z + distance + thickness))
        self.match.collision_world = world
        for player in self.match.players:
            player.mover.use(world=world)

    def test_downward_view_selects_floor_moves_at_authored_speed_and_hits_after_landing(self):
        before = tuple(self.a.mover.position)
        charge = self.charge_checkpoint()
        self.start()
        cast = self.fight.slams.casts[self.a.body]
        self.assertEqual(cast.kind, 'aerial')
        self.assertEqual(cast.speed, 25)
        self.assertAlmostEqual(cast.destination[1], self.b.mover.position[1])
        self.assertEqual(self.vitals().total, 200)
        self.settle(5)
        self.assertGreater(self.a.mover.position[2], before[2] + 1)
        self.assertGreater(self.a.mover.position[1], cast.destination[1] + 1)
        self.assertEqual(self.vitals().total, 200)
        for _ in range(100):
            self.run.step({})
            if self.vitals().total < 200:
                break
        self.assert_damage_charge(charge, 200 - self.vitals().total)
        self.assertAlmostEqual(self.vitals().total, 200 - cast.damage)
        self.assertGreater(cast.damage, 25)
        self.assertLessEqual(cast.damage, 125)
        self.assertAlmostEqual(self.a.mover.position[2], cast.destination[2], delta=.01)
        own = next(t for t in self.fight._targets() if t.owner is self.a)
        self.assertEqual(self.fight.vitals_of(own).temporary_shields, 30)
        self.settle(25)
        self.assertFalse(self.a.body_script.component.instances[8].state(0).flags & 1)
        self.assertGreater(self.a.body_script.component.instances[8].state(0).remaining(), 0)

    def test_landed_aerial_wave_respects_player_wall_and_team(self):
        for blocked, allied in ((False, False), (True, False), (False, True)):
            with self.subTest(blocked=blocked, allied=allied):
                self.prepare_air()
                self.match.game_map = self.match.game_map.__class__(**{
                    **self.match.game_map.__dict__, 'free_for_all': False,
                })
                self.a.team, self.b.team = 0, 0 if allied else 1
                charge = self.charge_checkpoint()
                start_z = self.a.mover.position[2]
                self.start()
                cast = self.fight.slams.casts[self.a.body]
                self.assertEqual(cast.kind, 'aerial')
                x, y, z = cast.destination
                target = (x, y, z + 3)
                self.place_player(self.b, target)
                if blocked:
                    self.wall(z - start_z + 1.5, top=5, thickness=.2)
                self.run.step({})
                self.assertIsNone(cast.wave_at)
                settled_target = tuple(self.b.mover.position)
                self.settle(160)
                self.assertTrue(cast.finished)
                self.assertIsNotNone(cast.wave_at)
                self.assertAlmostEqual(cast.origin[2], z, delta=.2)
                hit = not (blocked or allied)
                self.assertEqual(cast.hit, (self.b.body,) if hit else ())
                self.assertAlmostEqual(self.vitals().total, 200 - (cast.damage if hit else 0))
                self.assert_damage_charge(charge, cast.damage if hit else 0)
                self.assertEqual((self.fight.vitals[self.a.body].temporary_shield is not None), hit)
                self.assertEqual(len(self.a.client.messages(combat.HIT)), int(hit))
                if not hit:
                    self.assertEqual(tuple(self.b.mover.position), settled_target)

    def test_release_after_commit_does_not_cancel_or_redirect_fixed_destination(self):
        self.start()
        destination = self.fight.slams.casts[self.a.body].destination
        for _ in range(100):
            self.run.step({self.a: (16000, -9000, 0)})
        self.assertEqual(self.fight.slams.casts[self.a.body].destination, destination)
        self.assertAlmostEqual(self.a.mover.position[2], destination[2], delta=.01)
        self.assertLess(self.vitals().total, 200)
        self.assertEqual(len(self.a.client.messages(combat.HIT)), 1)

    def test_invalid_upward_view_release_cancels_without_cooldown_or_damage(self):
        for _ in range(8):
            self.start(pitch=-9000)
        instance = self.a.body_script.component.instances[8]
        self.assertEqual(instance.vars[59].value(), 2)
        self.assertFalse(instance.vars[1627].value())
        self.assertNotIn(self.a.body, self.fight.slams.casts)
        self.run.step({})
        self.assertFalse(instance.state(0).flags & 1)
        self.assertEqual(instance.state(0).remaining(), 0)
        self.assertEqual(self.vitals().total, 200)
        self.start()
        self.assertEqual(self.fight.slams.casts[self.a.body].kind, 'aerial')

    def test_fifteen_meter_query_rejects_floor_beyond_reach(self):
        self.prepare_air(height=20)
        self.start()
        instance = self.a.body_script.component.instances[8]
        self.assertFalse(instance.vars[1627].value())
        self.assertNotIn(self.a.body, self.fight.slams.casts)
        self.assertEqual(self.vitals().total, 200)

    def test_vertical_wall_is_not_a_valid_floor_target(self):
        self.wall(3)
        self.start()
        instance = self.a.body_script.component.instances[8]
        self.assertFalse(instance.vars[1627].value())
        self.assertNotIn(self.a.body, self.fight.slams.casts)

    def test_floor_under_low_overhang_is_rejected_for_head_clearance(self):
        self.wall(7.5, bottom=1.2, top=1.4, thickness=3)
        self.start()
        instance = self.a.body_script.component.instances[8]
        self.assertFalse(instance.vars[1627].value())
        self.assertNotIn(self.a.body, self.fight.slams.casts)

    def test_new_obstruction_aborts_move_and_lands_on_near_side_without_wall_damage(self):
        charge = self.charge_checkpoint()
        self.start()
        self.wall(3)
        self.settle(170)
        cast = self.fight.slams.casts[self.a.body]
        self.assertLess(self.a.mover.position[2], cast.destination[2] - 4)
        self.assertAlmostEqual(self.a.mover.position[1], self.b.mover.position[1], delta=.02)
        self.assertEqual(self.vitals().total, 200)
        self.assert_damage_charge(charge, 0)
        self.assertFalse((self.fight.vitals[self.a.body].temporary_shield is not None))
        self.assertFalse(self.a.client.messages(combat.HIT))
        self.assertFalse(self.a.body_script.component.instances[8].state(0).flags & 1)

    def test_death_during_committed_aerial_move_suppresses_damage(self):
        self.start()
        self.settle(5)
        owner = next(t for t in self.fight._targets() if t.owner is self.a)
        self.fight._damage(self.b, owner, 1000, False, None, self.run.frame)
        self.settle(120)
        self.assertEqual(self.vitals().total, 200)
        self.assertEqual(len(self.a.client.messages(combat.HIT)), 0)

    def test_duplicate_input_has_one_wave_and_spends_no_hand_cannon_ammo(self):
        weapon = next(i for i in self.a.body_script.component.instances.values() if i.graph.index == 0xD0A)
        ammo = weapon.vars[53].value()
        self.impact()
        self.a.take_commands([Command(self.run.frame, buttons=0x10)])
        self.settle(60)
        self.assertEqual(len(self.a.client.messages(combat.HIT)), 1)
        self.assertEqual(weapon.vars[53].value(), ammo)

    def test_aerial_cooldown_rejects_pulse_then_accepts_a_fresh_press(self):
        self.impact()
        self.settle(30)
        activation = self.fight.slams.casts[self.a.body].activation
        after = self.vitals().total
        x, y, z = self.a.mover.position
        self.place_player(self.a, (x, y + 8, z))
        self.start()
        self.settle(410)
        self.assertEqual(self.fight.slams.casts[self.a.body].activation, activation)
        self.assertEqual(self.vitals().total, after)
        self.assertEqual(self.a.body_script.component.instances[8].state(0).remaining(), 0)
        x, y, z = self.a.mover.position
        self.place_player(self.a, (x, y + 8, z))
        self.place_player(self.b, (x, y, z + 10))
        self.start()
        self.settle(90)
        self.assertNotEqual(self.fight.slams.casts[self.a.body].activation, activation)
        self.assertLess(self.vitals().total, after)


class SlamPlayerDeathTests(DoomfistFixture, unittest.TestCase):
    def test_player_wave_ends_at_authored_reach_without_using_ammo(self):
        for aerial in (False, True):
            with self.subTest(aerial=aerial):
                self.prepare(10)
                if aerial:
                    x, y, z = self.a.mover.position
                    self.place_player(self.a, (x, y + 8, z))
                component = self.a.body_script.component
                weapon = next(i for i in component.instances.values() if i.graph.index == 0xD0A)
                ammo = weapon.vars[53].value()
                self.run.step({self.a: (0, 9000 if aerial else 0, 0x10)})
                cast = self.fight.slams.casts[self.a.body]
                instance = self.fight.slams.instance(self.a)
                ability = instance.state(0)
                self.assertTrue(ability.flags & nodes.Ability.RUNNING)
                self.assertEqual(ability.remaining(), 0)
                self.assertIsNone(cast.wave_at)
                for _ in range(120):
                    self.run.step({})
                    self.assertEqual(weapon.vars[53].value(), ammo)
                    if cast.finished:
                        break
                self.assertTrue(cast.finished)
                self.assertEqual(cast.kind, 'aerial' if aerial else 'ground')
                self.assertEqual(cast.hit, (self.b.body,))
                self.assertAlmostEqual(self.vitals().total, 200 - cast.damage)
                speed = float(instance.evaluate(instance.graph.node(95).field('m_speed')))
                reach = float(instance.vars[422].value())
                elapsed = component.now - cast.wave_at
                self.assertGreaterEqual(elapsed, 1000 * reach / speed)
                self.assertLess(elapsed, 1000 * reach / speed + component.frame_ms)
                self.settle(60)
                self.assertFalse(ability.flags & nodes.Ability.RUNNING)
                self.assertGreater(ability.remaining(), 0)
                self.assertEqual(len(self.a.client.messages(combat.HIT)), 1)

    def test_lethal_ground_and_aerial_slam_do_not_launch_or_repeat_on_player(self):
        for aerial in (False, True):
            with self.subTest(aerial=aerial):
                self.prepare(10)
                if aerial:
                    x, y, z = self.a.mover.position
                    self.place_player(self.a, (x, y + 8, z))
                victim = self.vitals()
                victim.now[combat.HEALTH_POOL] = 20
                origin = tuple(self.b.mover.position)
                self.run.step({self.a: (0, 9000 if aerial else 0, 0x10)})
                cast = self.fight.slams.casts[self.a.body]
                for _ in range(120):
                    self.run.step({})
                    if cast.finished:
                        break
                self.assertTrue(cast.finished)
                self.assertEqual(cast.kind, 'aerial' if aerial else 'ground')
                self.assertEqual(cast.hit, (self.b.body,))
                self.assertTrue(victim.dead)
                self.assertEqual(victim.killer, self.a.body)
                self.assertEqual(victim.total, 0)
                self.assertEqual(self.fight.vitals[self.a.body].temporary_shields, 30)
                self.settle(90)
                self.assertEqual(tuple(self.b.mover.position), origin)
                hit, = self.a.client.messages(combat.HIT)
                self.assertEqual(hit["+0x78"]["+0x4"], 20)

