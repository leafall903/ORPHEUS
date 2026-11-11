"""Actual cast/projectile/field combat tests; host force policy is reconstructed."""

import math
import unittest
from unittest.mock import patch

from test_game_combat import Run, aim, in_world

from ow174.game import combat, graviton, zarya
from ow174.game.collision import World
from ow174.game.commands import Command
from ow174.game.content import heroes
from ow174.game.script.driver import ULT_CHARGE, ULT_COST

ZARYA = 0x02E0000000000068


class ZaryaUltimateTests(unittest.TestCase):
    def setUp(self):
        self.run = Run(names=("Zarya", "Enemy", "Ally"))
        self.a, self.b, self.c = self.run.players
        self.fight = self.run.match.combat
        self.run.match.bots.clear()
        self.run.match._pending_bot_scripts.clear()
        self.run.match.switch_hero(self.a, heroes()[ZARYA])
        self.c.team = self.a.team
        for player, position in ((self.a, [0, 1, 0]), (self.b, [0, 1, 5]), (self.c, [2, 1, 5])):
            player.mover.position[:] = position

    def health(self, player=None):
        player = self.b if player is None else player
        return self.vitals(player).total

    def vitals(self, player):
        target = next(t for t in self.fight._targets() if t.owner is player)
        return self.fight.vitals_of(target)

    def charge(self):
        return self.a.body_script.component.entity_var(ULT_CHARGE).value()

    def cost(self):
        return self.a.body_script.component.entity_var(ULT_COST).value()

    def cast(self, *, yaw=0):
        self.a.body_script.set_ult_charge(self.cost())
        self.run.step({self.a: (yaw, 0, 0x20)})

    def advance(self, frames):
        for _ in range(frames):
            self.run.step({})

    def until_field(self):
        for _ in range(100):
            self.run.step({})
            if self.fight.zarya_gravitons.fields:
                return self.fight.zarya_gravitons.fields[0]
        self.fail("cast never produced its field")

    def test_zero_charge_rejects_ultimate(self):
        self.a.body_script.ult_seconds = 0
        self.run.step({self.a: (0, 0, 0x20)})
        self.advance(30)
        self.assertFalse(self.fight.rockets)
        self.assertFalse(self.fight.zarya_gravitons.fields)
        self.assertEqual(self.health(), 200)

    def test_below_cost_rejects_held_press_and_full_cost_allows_cast(self):
        self.assertEqual(self.cost(), 2100)
        self.a.body_script.ult_seconds = 0
        self.a.body_script.set_ult_charge(self.cost() - 1)
        for _ in range(20):
            self.run.step({self.a: (0, 0, 0x20)})
        self.assertFalse(self.fight.rockets)
        self.run.step({})
        self.cast()
        self.assertEqual(len(self.fight.rockets), 1)
        self.assertEqual(self.charge(), 0)

    def test_cast_consumes_charge_and_launches_authored_deployable(self):
        self.cast()
        self.assertEqual(self.charge(), 0)
        rocket, = self.fight.rockets
        self.assertEqual(rocket.spawn, graviton.FIELD_ENTITY)
        self.assertAlmostEqual(rocket.gravity, 9.8, places=5)
        self.assertEqual(rocket.direct.amount, 5)

    def test_passive_charge_uses_match_clock_without_commands_or_duplicate_ticks(self):
        self.run.step({})
        for _ in range(100):
            self.run.frame += 1
            self.run.match.update(self.run.frame * combat.TICK, self.run.frame)
        self.assertAlmostEqual(self.charge(), 8, places=4)
        saved = self.charge()
        for offset in (0, -1, 0):
            tick = self.run.frame + offset
            self.run.match.update(tick * combat.TICK, tick)
        self.assertEqual(self.charge(), saved)
        self.run.step({self.a: (0, 0, 0x20)})
        self.assertFalse(self.fight.rockets)

    def test_passive_charge_covers_long_sparse_match_update(self):
        self.run.step({})
        start = self.charge()
        tick = self.run.frame + 200
        self.run.match.update(tick * combat.TICK, tick, frames=False)
        self.assertAlmostEqual(self.charge() - start, 16, places=4)
        self.run.match.update(tick * combat.TICK, tick, frames=False)
        self.assertAlmostEqual(self.charge() - start, 16, places=4)

    def test_passive_charge_starts_at_field_end_across_long_sparse_match_update(self):
        self.cast()
        field = self.until_field()
        self.assertEqual(self.charge(), 0)
        tick = field.end_tick + 170
        self.run.match.update(tick * combat.TICK, tick, frames=False)
        self.assertFalse(self.fight.zarya_gravitons.fields)
        self.assertAlmostEqual(self.charge(), 13.6, places=4)

    def test_future_cast_command_does_not_extend_charge_lock_past_field(self):
        self.run.step({})
        cast_tick = self.run.frame
        script = self.a.body_script
        script.set_ult_charge(self.cost())
        self.a.take_commands([Command(cast_tick + 500, buttons=0x20)])
        self.assertEqual(len(self.fight.rockets), 1)
        self.assertEqual(self.charge(), 0)
        field = self.until_field()
        self.assertGreater(zarya.ultimate_charge_ready_frame(script), field.end_tick)

        tick = field.end_tick
        self.run.match.update(tick * combat.TICK, tick, frames=False)
        self.assertEqual(self.charge(), 0)
        self.run.match.update((tick + 1) * combat.TICK, tick + 1, frames=False)
        self.assertFalse(self.fight.zarya_gravitons.fields)
        self.assertAlmostEqual(self.charge(), 0.08, places=5)

    def test_melee_charge_waits_until_the_tick_after_graviton_damage_ends(self):
        for offset, expected in ((0, 0), (1, 30)):
            with self.subTest(offset=offset):
                self.setUp()
                self.a.body_script.ult_seconds = 0
                self.b.mover.position[2] = 1.5
                self.cast(yaw=-32768)
                field = self.until_field()
                tick = field.end_tick + offset
                self.run.match.update(tick * combat.TICK, tick, frames=False)
                self.assertFalse(self.fight.zarya_gravitons.fields)
                self.assertEqual(field.last_tick, field.end_tick)
                before = self.health()
                self.a.take_commands([Command(tick, buttons=0x800)])
                self.assertAlmostEqual(before - self.health(), 30, places=4)
                self.assertAlmostEqual(self.charge(), expected, places=4)

    def test_sparse_passive_charge_excludes_time_before_body_creation(self):
        for replace in (False, True):
            with self.subTest(replace=replace):
                self.setUp()
                self.run.step({})
                match = self.run.match
                if replace:
                    self.assertTrue(match.switch_hero(self.a, self.b.hero))
                else:
                    self.a = match.add_player(4, "Late Zarya", self.b.hero, 3, False)
                    in_world(match, self.a)
                    self.assertFalse(self.a.has_body)
                # Match has entered the new world tick, before Combat.update catches up.
                tick = self.run.frame + 200
                match.tick = tick
                self.assertTrue(match.switch_hero(self.a, heroes()[ZARYA]))
                match.update(tick * combat.TICK, tick, frames=False)
                self.assertEqual(self.charge(), 0)
                match.update((tick + 1) * combat.TICK, tick + 1, frames=False)
                self.assertAlmostEqual(self.charge(), 0.08)

    def test_actual_beam_secondary_and_melee_credit_body_damage(self):
        for button, expected in ((1, 60), (2, 47), (0x800, 30)):
            with self.subTest(button=button):
                self.setUp()
                self.a.body_script.ult_seconds = 0
                self.b.mover.position[2] = 1.5
                for frame in range(51):
                    self.run.step({self.a: (0, 0, button if frame == 0 or button == 1 else 0)})
                self.assertAlmostEqual(200 - self.health(), expected, places=4)
                self.assertAlmostEqual(self.charge(), expected, places=4)

    def test_charge_excludes_misses_allies_and_absorbed_damage(self):
        for case in ("miss", "ally", "barrier"):
            with self.subTest(case=case):
                self.setUp()
                self.a.body_script.ult_seconds = 0
                if case == "ally":
                    self.b.team = self.a.team
                if case == "barrier":
                    self.run.match.switch_hero(self.b, heroes()[ZARYA])
                    self.b.mover.position[:] = [0, 1, 5]
                    self.run.step({self.b: (0, 0, 8)})
                for _ in range(51):
                    self.run.step({self.a: (16384 if case == "miss" else 0, 0, 1)})
                self.assertEqual(self.charge(), 0)
                if case == "barrier":
                    self.assertAlmostEqual(self.b.body_script.component.entity_var(87).value(), 12, places=4)

    def test_held_cast_starts_when_passive_charge_crosses_cost(self):
        self.run.step({})
        self.a.body_script.set_ult_charge(self.cost() - 0.1)
        self.run.step({self.a: (0, 0, 0x20)})
        self.assertFalse(self.fight.rockets)
        self.run.step({self.a: (0, 0, 0x20)})
        self.assertEqual(len(self.fight.rockets), 1)
        self.assertEqual(self.charge(), 0)

    def test_secondary_charge_clamps_at_cost_and_excludes_overkill(self):
        for starting, expected in ((0, 12), (2095, 2100)):
            with self.subTest(starting=starting):
                self.setUp()
                self.a.body_script.ult_seconds = 0
                self.a.body_script.set_ult_charge(starting)
                self.vitals(self.b).damage(188)
                self.run.step({self.a: (0, 0, 2)})
                self.advance(45)
                self.assertTrue(self.fight.is_dead(self.b))
                self.assertAlmostEqual(self.charge(), expected)

    def test_graviton_blocks_charge_through_flight_and_field_then_releases_it(self):
        self.cast()
        field = self.until_field()
        self.assertEqual(self.charge(), 0)
        for _ in range(20):
            self.run.step({self.a: (0, 0, 1)})
        self.assertLess(self.health(), 193)
        self.assertEqual(self.charge(), 0)
        while self.run.frame < field.end_tick:
            self.run.step({})
        self.assertFalse(self.fight.zarya_gravitons.fields)
        self.assertEqual(self.charge(), 0)
        self.run.step({})
        self.assertAlmostEqual(self.charge(), 0.08)

    def test_death_freezes_charge_and_same_hero_respawn_preserves_it(self):
        self.a.body_script.ult_seconds = 0
        self.a.body_script.set_ult_charge(123)
        old_body = self.a.body
        yaw, pitch = aim(self.fight._eye(self.b), (0, 2, 0))
        for _ in range(210):
            self.run.step({self.b: (yaw, pitch, 1)})
            if self.fight.is_dead(self.a):
                break
        self.assertTrue(self.fight.is_dead(self.a))
        self.a.body_script.ult_seconds = 60
        self.advance(100)
        self.assertEqual(self.a.body, old_body)
        self.assertEqual(self.charge(), 123)
        for _ in range(100):
            self.run.step({})
            if self.a.body != old_body:
                break
        self.assertNotEqual(self.a.body, old_body)
        self.assertEqual(self.charge(), 123)
        self.assertTrue(self.run.match.switch_hero(self.a, self.b.hero))
        self.assertTrue(self.run.match.switch_hero(self.a, heroes()[ZARYA]))
        self.assertEqual(self.charge(), 0)

    def test_held_input_cannot_repeat_without_recharging(self):
        self.cast()
        self.a.body_script.ult_seconds = 0
        for _ in range(300):
            self.run.step({self.a: (0, 0, 0x20)})
        self.assertEqual(self.charge(), 0)
        self.assertFalse(self.fight.rockets)
        self.assertFalse(self.fight.zarya_gravitons.fields)
        self.assertAlmostEqual(self.health(), 175)

    def test_direct_5_plus_field_20_damage_then_effect_ends(self):
        self.cast()
        field = self.until_field()
        self.assertEqual(field.end_tick - field.last_tick, 250)
        self.assertAlmostEqual(self.health(), 195)
        self.advance(270)
        self.assertAlmostEqual(self.health(), 175)
        self.assertFalse(self.fight.zarya_gravitons.fields)
        self.advance(30)
        self.assertAlmostEqual(self.health(), 175)

    def test_allies_and_caster_are_not_damaged_or_pulled(self):
        ally = tuple(self.c.mover.position)
        caster = tuple(self.a.mover.position)
        self.cast()
        self.advance(280)
        self.assertEqual(self.health(self.c), 200)
        self.assertEqual(self.health(self.a), 400)
        self.assertEqual(tuple(self.c.mover.position), ally)
        self.assertEqual(tuple(self.a.mover.position), caster)

    def test_aimed_miss_does_not_damage_enemy(self):
        self.cast(yaw=16384)
        self.advance(280)
        self.assertEqual(self.health(), 200)

    def test_static_wall_blocks_field_from_enemy(self):
        wall = World([(-10, 0, 2, 10, 0, 2, 10, 10, 2),
                      (-10, 0, 2, 10, 10, 2, -10, 10, 2)])
        with patch.object(self.fight, "_collision", return_value=wall):
            self.cast()
            self.advance(280)
        self.assertEqual(self.health(), 200)

    def test_enemy_outside_radius_is_not_affected(self):
        self.c.team = self.b.team
        self.c.mover.position[:] = [8, 1, 5]
        self.cast()
        self.advance(280)
        self.assertEqual(self.health(self.c), 200)
        self.assertEqual(self.c.mover.position[0], 8)

    def test_field_admits_bubbled_player_on_body_entry_not_sphere_overlap(self):
        for projected in (False, True):
            with self.subTest(projected=projected):
                self.setUp()
                self.assertTrue(self.run.match.switch_hero(self.b, self.a.hero))
                self.b.mover.position[:] = [7, 1, 4.5]
                caster = self.c if projected else self.b
                if projected:
                    self.assertTrue(self.run.match.switch_hero(caster, self.a.hero))
                    caster.team = self.b.team
                self.c.mover.position[:] = [11, 1, 4.5]
                for actor in self.run.players:
                    actor.mover.body = None
                self.run.match.collision_world = World([
                    (-20, 1, -20, -20, 1, 20, 20, 1, 20), (-20, 1, -20, 20, 1, 20, 20, 1, -20),
                    (-20, 1, 5, 20, 1, 5, 20, 8, 5), (-20, 1, 5, 20, 8, 5, -20, 8, 5),
                ])
                self.a.body_script.ult_seconds = 0
                self.cast()
                field = self.until_field()
                angles = aim(self.fight._eye(caster), self.fight._eye(self.b)) if projected else (0, 0)
                self.run.step({caster: (*angles, 16 if projected else 8)})
                barriers = self.fight.zarya_barriers
                self.assertTrue(barriers.protects(self.b))
                pool = (barriers.projected if projected else barriers.active)[caster.body]
                pool.remaining = 0.12
                target = next(t for t in self.fight._targets() if t.owner is self.b)
                a, b = target.segment()
                x, y, z = target.feet
                centre = (x, y + target.height / 2, z)
                body_distance = combat.segment_distance(field.position, a, b) - target.radius
                self.assertGreater(body_distance, field.radius)
                self.assertLess(math.dist(field.position, centre) - zarya.BUBBLE_RADIUS, field.radius)
                self.advance(10)
                self.assertEqual(pool.remaining, 0.12)
                self.assertEqual(self.health(), 400)
                self.assertIsNone(self.fight.zarya_gravitons.movement_for(self.b))
                self.assertEqual(self.b.mover.position[0], 7)

                for _ in range(24):
                    self.run.frame += 1
                    self.run.match.update(self.run.frame * combat.TICK, self.run.frame)
                    for actor in self.run.players:
                        command = Command(self.run.frame)
                        if actor is self.b:
                            command = Command(self.run.frame, yaw=-16384, forward=127)
                        actor.take_commands([command])
                    if self.fight.zarya_gravitons.movement_for(self.b) is not None:
                        break
                self.assertIsNotNone(self.fight.zarya_gravitons.movement_for(self.b))
                self.assertEqual(pool.remaining, 0.12)
                entered_x = self.b.mover.position[0]
                self.advance(1)
                self.assertAlmostEqual(pool.remaining, 0.04)
                self.assertLess(self.b.mover.position[0], entered_x)
                self.assertEqual(self.health(), 400)
                self.advance(1)
                self.assertEqual(pool.remaining, 0)
                self.assertEqual(self.health(), 400)
                self.assertAlmostEqual(caster.body_script.component.entity_var(87).value(), 0.024, places=4)
                self.advance(1)
                self.assertAlmostEqual(self.health(), 399.92, places=4)
                self.assertEqual(self.charge(), 0)

    def test_projectile_uses_vertical_acceleration(self):
        self.cast()
        rocket, = self.fight.rockets
        start = rocket.position[1]
        self.advance(1)
        self.assertAlmostEqual(rocket.position[1], start - 0.5 * 9.8 * 0.016**2, places=6)

    def test_upward_flight_reaches_range_lifetime_then_deploys_once(self):
        self.a.body_script.set_ult_charge(self.cost())
        self.run.step({self.a: (0, -16384, 0x20)})
        rocket, = self.fight.rockets
        self.assertEqual(rocket.left, 4)
        self.advance(260)
        self.assertFalse(self.fight.rockets)
        field, = self.fight.zarya_gravitons.fields
        self.assertAlmostEqual(field.position[1], 2.60 + 25 * 4 - 0.5 * 9.8 * 4**2, places=4)
        self.assertEqual(self.health(), 200)
        self.advance(260)
        self.assertFalse(self.fight.zarya_gravitons.fields)

    def test_started_graviton_flight_keeps_impact_and_field_clock_across_sparse_updates(self):
        self.cast()
        self.advance(1)
        rocket, = self.fight.rockets
        self.assertEqual(rocket.last_tick, self.run.frame)
        field = self.until_field()
        impact, end, position = field.last_tick, field.end_tick, field.position
        self.assertEqual(self.health(), 195)
        self.advance(end + 170 - self.run.frame)
        self.assertAlmostEqual(self.health(), 175)
        self.assertAlmostEqual(self.charge(), 13.6, places=4)

        for tick in (impact, end + 170):
            with self.subTest(tick=tick):
                self.setUp()
                self.cast()
                self.advance(1)
                self.run.match.update(tick * combat.TICK, tick, frames=False)
                self.assertFalse(self.fight.rockets)
                self.assertEqual(self.health(self.c), 200)
                if tick == impact:
                    actual, = self.fight.zarya_gravitons.fields
                    self.assertEqual((actual.last_tick, actual.end_tick), (impact, end))
                    for actual_axis, expected_axis in zip(actual.position, position, strict=True):
                        self.assertAlmostEqual(actual_axis, expected_axis)
                    self.assertEqual(self.health(), 195)
                    self.assertEqual(self.charge(), 0)
                else:
                    self.assertFalse(self.fight.zarya_gravitons.fields)
                    self.assertAlmostEqual(self.health(), 175)
                    self.assertAlmostEqual(self.charge(), 13.6, places=4)

    def test_started_graviton_flight_finishes_pull_during_sparse_update(self):
        self.c.team = self.b.team
        self.c.mover.position[:] = (4, 1, 5)
        self.cast()
        self.advance(1)
        rocket, = self.fight.rockets
        self.assertEqual(rocket.last_tick, self.run.frame)
        start = tuple(self.c.mover.position)
        tick = self.run.frame + 420
        self.run.match.update(tick * combat.TICK, tick, frames=False)
        self.assertFalse(self.fight.rockets)
        self.assertFalse(self.fight.zarya_gravitons.fields)
        self.assertAlmostEqual(self.health(self.c), 180, places=4)
        self.assertGreater(start[0] - self.c.mover.position[0], 3)
        self.assertFalse(self.c.mover.driven)
        self.assertEqual(self.c.mover.velocity, (0, 0, 0))

    def test_sparse_graviton_flight_expires_at_authored_lifetime(self):
        self.a.body_script.set_ult_charge(self.cost())
        self.run.step({self.a: (0, -16384, 0x20)})
        cast = self.run.frame
        self.advance(1)
        tick = cast + 260
        self.run.match.update(tick * combat.TICK, tick, frames=False)
        self.assertFalse(self.fight.rockets)
        field, = self.fight.zarya_gravitons.fields
        self.assertEqual(field.end_tick, cast + 500)
        self.assertAlmostEqual(field.position[1], 2.60 + 25 * 4 - 0.5 * 9.8 * 4**2, places=4)
        self.assertEqual(self.health(), 200)
        self.assertEqual(self.charge(), 0)

    def test_graviton_launch_keeps_its_clock_before_the_first_scheduler_update(self):
        self.cast()
        rocket, = self.fight.rockets
        self.assertEqual(rocket.flown, 0)
        launched = self.run.frame
        tick = launched + 200
        self.run.match.update(tick * combat.TICK, tick, frames=False)
        self.assertFalse(self.fight.rockets)
        field, = self.fight.zarya_gravitons.fields
        self.assertEqual(field.end_tick, launched + 262)
        self.assertAlmostEqual(self.health(), 195 - 188 * 0.08)

    def test_new_graviton_does_not_backdate_unsimulated_input_across_scheduler_gap(self):
        self.run.step({})
        tick = self.run.frame + 200
        match = self.run.match
        match.tick = tick
        self.a.body_script.set_ult_charge(self.cost())
        self.a.take_commands([Command(tick + 500, buttons=0x20)])
        rocket, = self.fight.rockets
        match.update(tick * combat.TICK, tick, frames=False)
        self.assertEqual(rocket.flown, 0)
        match.update((tick + 1) * combat.TICK, tick + 1, frames=False)
        self.assertEqual(self.fight.rockets, [rocket])
        self.assertEqual(rocket.last_tick, tick + 1)
        self.assertAlmostEqual(rocket.flown, combat.TICK)
        self.assertFalse(self.fight.zarya_gravitons.fields)
        self.assertEqual(self.health(), 200)

    def test_late_barrier_does_not_absorb_earlier_graviton_contact(self):
        for projected in (False, True):
            for deployed in (False, True):
                with self.subTest(projected=projected, deployed=deployed):
                    self.setUp()
                    match = self.run.match
                    caster = self.c if projected else self.b
                    self.assertTrue(match.switch_hero(caster, heroes()[ZARYA]))
                    caster.team = self.b.team
                    caster.mover.position[:] = [4 if projected else 0, 1, 5]
                    caster.mover.body = None
                    self.cast()
                    if deployed:
                        field = self.until_field()
                        activation = field.last_tick + 120
                    else:
                        self.advance(1)
                        rocket, = self.fight.rockets
                        self.assertEqual(rocket.last_tick, self.run.frame)
                        activation = self.run.frame + 32

                    before = self.health()
                    match.tick = activation
                    yaw, pitch = (0, 0)
                    if projected:
                        yaw, pitch = aim(self.fight._eye(caster), self.fight._eye(self.b))
                    caster.take_commands([Command(activation + 500, yaw=yaw, pitch=pitch,
                                                  buttons=16 if projected else 8)])
                    barriers = self.fight.zarya_barriers
                    self.assertTrue(barriers.protects(self.b))
                    tick = activation + 10
                    match.update(tick * combat.TICK, tick, frames=False)
                    field, = self.fight.zarya_gravitons.fields
                    landed = field.end_tick - 250
                    self.assertGreater(field.position[2], 4)
                    self.assertAlmostEqual(before - self.health(), (activation - landed - 1) * 0.08
                                           + (0 if deployed else 5), places=5)
                    pool = (barriers.projected if projected else barriers.active)[caster.body]
                    self.assertAlmostEqual(pool.remaining, 199.12, places=5)
                    caster.take_commands([Command(tick + 501, yaw=yaw, pitch=pitch)])
                    energy = caster.body_script.component.entity_var(87).value()
                    self.assertAlmostEqual(energy, 0.176, places=5)

    def test_sparse_graviton_limits_damage_and_pull_to_late_players_live_ticks(self):
        for ticks_to_end, damage in ((170, 6.48), (10, 0.88), (0, 0.08), (-1, 0)):
            with self.subTest(ticks_to_end=ticks_to_end):
                self.setUp()
                self.cast()
                field = self.until_field()
                match = self.run.match
                born = field.end_tick - ticks_to_end
                match.tick = born
                x, _, z = field.position
                feet = (x + 5, 1, z)
                late = match.add_player(4, "Late enemy", self.b.hero, 3, False)
                in_world(match, late)
                self.assertTrue(match.switch_hero(late, self.b.hero))
                late.team = self.b.team
                late.mover.position[:] = feet
                tick = born + 80
                match.update(tick * combat.TICK, tick, frames=False)
                self.assertAlmostEqual(200 - self.health(late), damage, places=4)
                pull = feet[0] - late.mover.position[0]
                if tick >= field.end_tick:
                    if ticks_to_end > 0:
                        self.assertAlmostEqual(pull, 0.48, delta=0.01)
                        self.assertEqual(late.mover.velocity, (0, 0, 0))
                    else:
                        self.assertEqual(tuple(late.mover.position), feet)
                    self.assertFalse(self.fight.zarya_gravitons.fields)
                else:
                    self.assertGreater(pull, 3)
                    self.assertLessEqual(pull, 3.889)
                self.assertEqual(late.mover.driven, tick < field.end_tick)

    def test_sparse_graviton_flight_cannot_hit_a_player_before_their_body_exists(self):
        self.b.mover.position[0] = 100
        self.cast()
        self.advance(1)
        match = self.run.match
        tick = self.run.frame + 60
        match.tick = tick
        late = match.add_player(4, "Late enemy", self.b.hero, 3, False)
        in_world(match, late)
        self.assertTrue(match.switch_hero(late, self.b.hero))
        late.team = self.b.team
        late.mover.position[:] = [0, 1, 5]
        match.update(tick * combat.TICK, tick, frames=False)
        self.assertEqual(self.health(late), 200)
        field, = self.fight.zarya_gravitons.fields
        self.assertGreater(field.position[2], 10)

    def test_zero_displacement_at_apex_does_not_freeze_flight_clock(self):
        rocket = combat.Rocket(self.a, self.a.team, (0, 50, 0), (0, 0.008, 0),
                               1, 1, combat.HitDef(), None, last_tick=self.fight.tick, gravity=1)
        self.fight.rockets.append(rocket)
        self.fight.tick += 1
        self.fight._fly(rocket)
        self.assertAlmostEqual(rocket.flown, 0.016)
        self.assertAlmostEqual(rocket.left, 0.984)
        self.fight.tick += 1
        self.fight._fly(rocket)
        self.assertLess(rocket.position[1], 50)

    def test_dead_victim_is_not_pulled_after_lethal_field_tick(self):
        self.cast()
        self.until_field()
        vitals = self.vitals(self.b)
        vitals.now[combat.HEALTH_POOL] = 0.1
        self.advance(2)
        self.assertTrue(vitals.dead)
        position = tuple(self.b.mover.position)
        self.advance(10)
        self.assertEqual(tuple(self.b.mover.position), position)

    def test_sparse_graviton_matches_paced_pull_and_death_tick(self):
        outcomes = []
        for sparse in (False, True):
            self.setUp()
            self.c.team = self.b.team
            self.c.mover.position[:] = (4, 1, 5)
            self.cast()
            field = self.until_field()
            start = field.last_tick
            end = start + 120
            vitals = self.vitals(self.c)
            vitals.now[combat.HEALTH_POOL] = 0.6
            match = self.run.match
            for tick in (end,) if sparse else range(start + 1, end + 1):
                match.update(tick * combat.TICK, tick, frames=False)
            self.assertTrue(vitals.dead)
            outcomes.append((vitals.death_tick - start, tuple(self.c.mover.position),
                             self.c.mover.velocity, self.c.mover.driven))

        death_offset, position, velocity, driven = outcomes[0]
        self.assertEqual(death_offset, 8)
        self.assertLess(position[0], 3.7)
        self.assertEqual(velocity, (0, 0, 0))
        self.assertFalse(driven)
        self.assertEqual(outcomes[1], outcomes[0])

    def test_field_pulls_player_and_expires_before_control_resumes(self):
        self.cast()
        field = self.until_field()
        before = self.b.mover.position[2]
        self.advance(20)
        self.assertLess(self.b.mover.position[2], before)
        # Submit actual movement commands: the field takes priority over forward input.
        for _ in range(60):
            self.run.frame += 1
            self.run.match.update(self.run.frame * 0.016, self.run.frame)
            for player in self.run.players:
                player.take_commands([Command(self.run.frame, forward=127 if player is self.b else 0)])
        self.assertLessEqual(self.b.mover.position[2], field.position[2] + 0.1)
        self.advance(210)
        self.assertIsNone(self.fight.zarya_gravitons.movement_for(self.b))
        before = self.b.mover.position[2]
        for _ in range(20):
            self.run.frame += 1
            self.run.match.update(self.run.frame * 0.016, self.run.frame)
            for player in self.run.players:
                player.take_commands([Command(self.run.frame, forward=127 if player is self.b else 0)])
        self.assertGreater(self.b.mover.position[2], before)

    def test_world_pulls_without_commands_and_resumed_input_does_not_double_step(self):
        self.cast()
        self.until_field()
        start = tuple(self.b.mover.position)
        for _ in range(10):
            self.run.frame += 1
            self.run.match.update(self.run.frame * combat.TICK, self.run.frame)
        pulled = tuple(self.b.mover.position)
        self.assertGreater(start[2] - pulled[2], 0.35)
        self.assertLessEqual(start[2] - pulled[2], 0.49)
        self.assertAlmostEqual(self.health(), 194.2)

        velocity = self.b.mover.velocity
        self.assertLess(velocity[2], 0)
        self.run.match.update(self.run.frame * combat.TICK, self.run.frame)
        self.assertEqual(tuple(self.b.mover.position), pulled)
        self.assertEqual(self.b.mover.velocity, velocity)
        self.b.take_commands([Command(self.run.frame, yaw=16384, forward=127)])
        self.assertEqual(tuple(self.b.mover.position), pulled)
        self.assertEqual(self.b.mover.yaw, 16384)
        self.assertEqual(self.fight.history[self.b.body][-1][1], pulled)

        self.a.gone = True
        self.run.frame += 1
        self.run.match.update(self.run.frame * combat.TICK, self.run.frame)
        self.assertEqual(tuple(self.b.mover.position), pulled)
        self.assertEqual(self.b.mover.velocity, (0.0, 0.0, 0.0))
        self.assertFalse(self.b.mover.driven)

    def test_overlapping_actual_fields_deal_damage_independently_and_handoff_one_pull(self):
        self.assertTrue(self.run.match.switch_hero(self.c, self.a.hero))
        self.c.team = self.a.team
        for player, position in ((self.a, (-2, 1, 0)), (self.b, (0, 1, 4)),
                                 (self.c, (2, 1, 0))):
            player.mover.position[:] = position
            player.mover.body = None
        wall = World([(-10, 0, 6, 10, 0, 6, 10, 10, 6),
                      (-10, 0, 6, 10, 10, 6, -10, 10, 6),
                      (-10, 1, -10, -10, 1, 10, 10, 1, 10),
                      (-10, 1, -10, 10, 1, 10, 10, 1, -10)])
        self.run.match.collision_world = wall
        self.a.body_script.set_ult_charge(self.cost())
        self.c.body_script.set_ult_charge(self.cost())
        self.run.step({self.a: (0, 0, 0x20), self.c: (0, 0, 0x20)})
        for _ in range(50):
            self.run.step({})
            if len(self.fight.zarya_gravitons.fields) == 2:
                break
        first, second = self.fight.zarya_gravitons.fields
        self.assertIs(first.shooter, self.a)
        self.assertIs(second.shooter, self.c)
        self.assertEqual(self.health(), 200)
        before = tuple(self.b.mover.position)

        self.advance(1)
        pulled = tuple(self.b.mover.position)
        self.assertLess(pulled[0], before[0])
        self.assertLessEqual(math.dist(before, pulled), 0.049)
        self.assertAlmostEqual(self.health(), 199.84)

        self.a.gone = True
        self.advance(1)
        handed_off = tuple(self.b.mover.position)
        self.assertGreater(handed_off[0], pulled[0])
        self.assertLessEqual(math.dist(pulled, handed_off), 0.049)
        self.assertAlmostEqual(self.health(), 199.76)
        self.assertEqual(self.fight.zarya_gravitons.fields, [second])
        self.assertEqual(self.health(self.c), 400)

    def test_graviton_blocks_new_swift_strike_without_spending_its_cooldown(self):
        self.assertTrue(self.run.match.switch_hero(self.b, heroes()[0x02E0000000000029]))
        self.b.mover.position[:] = [0, 1, 5]
        self.cast()
        field = self.until_field()
        gravitons = self.fight.zarya_gravitons
        self.assertIsNotNone(gravitons.movement_for(self.b))
        strike = next(i for i in self.b.body_script.component.instances.values()
                      if i.graph.index == 0x01DB)
        cooldown, dash = strike.state(0), strike.state(1)
        initial = dash.counter
        held = {self.b: (-32768, 0, 8)}
        while self.run.frame < field.end_tick - 1:
            self.run.step(held)
            self.assertEqual(dash.counter, initial)
            self.assertFalse(dash.active)
            self.assertEqual(cooldown.remaining(), 0)
            self.assertNotIn(self.b.body, self.fight.genji_strikes)
            self.assertEqual(self.health(self.a), 400)

        self.run.step(held)
        self.assertFalse(gravitons.fields)
        self.assertTrue(dash.active)
        self.assertEqual(dash.counter, initial + 1)
        self.advance(40)
        self.assertEqual(self.health(self.a), 350)
        self.assertFalse(dash.active)
        self.assertGreater(cooldown.remaining(), 0)

    def test_swift_strike_gate_follows_graviton_team_and_cover_changes(self):
        for case in ("team", "cover"):
            with self.subTest(case=case):
                self.setUp()
                self.assertTrue(self.run.match.switch_hero(self.b, heroes()[0x02E0000000000029]))
                self.b.mover.position[:] = [3, 1, 5]
                self.c.team = self.b.team
                self.c.mover.position[:] = [0, 1, 5]
                self.cast()
                field = self.until_field()
                gravitons = self.fight.zarya_gravitons
                strike = next(i for i in self.b.body_script.component.instances.values()
                              if i.graph.index == 0x01DB)
                cooldown, dash = strike.state(0), strike.state(1)
                held = {self.b: (-32768, 0, 8)}
                self.run.step(held)
                self.assertIsNotNone(gravitons.movement_for(self.b))
                self.assertFalse(dash.active)
                self.assertEqual(cooldown.remaining(), 0)
                initial = dash.counter
                before = self.health(self.b)

                enemy_team = self.b.team
                world = self.run.match.collision_world
                if case == "team":
                    self.b.team = self.a.team
                else:
                    self.run.match.collision_world = World([
                        (-10, 1, -10, -10, 1, 20, 20, 1, 20), (-10, 1, -10, 20, 1, 20, 20, 1, -10),
                        (1.5, 0, 2, 1.5, 5, 2, 1.5, 5, 8), (1.5, 0, 2, 1.5, 5, 8, 1.5, 0, 8),
                    ])
                self.run.step(held)
                self.assertEqual(gravitons.fields, [field])
                self.assertIsNone(gravitons.movement_for(self.b))
                self.assertEqual(self.health(self.b), before)
                self.assertTrue(dash.active)
                self.assertEqual(dash.counter, initial + 1)

                self.b.team = enemy_team
                self.run.match.collision_world = world
                self.run.step(held)
                self.assertEqual(gravitons.fields, [field])
                self.assertIsNotNone(gravitons.movement_for(self.b))
                self.assertAlmostEqual(self.health(self.b), before - 0.08)
                self.assertFalse(dash.active)
                self.assertEqual(dash.counter, initial + 1)
                self.assertNotIn(self.b.body, self.fight.genji_strikes)
                for _ in range(40):
                    self.run.step(held)
                    self.assertEqual(dash.counter, initial + 1)
                self.assertGreater(cooldown.remaining(), 0)

    def test_actual_field_interrupts_swift_strike_without_resuming_after_expiry(self):
        self.assertTrue(self.run.match.switch_hero(self.b, heroes()[0x02E0000000000029]))
        self.b.mover.position[:] = [8, 1, 5]
        self.c.team = self.b.team
        self.c.mover.position[:] = [0, 1, 5]
        self.cast()
        field = self.until_field()
        dash = next(i for i in self.b.body_script.component.instances.values()
                    if i.graph.index == 0x01DB).state(1)
        self.run.step({self.b: (-16384, 0, 8)})
        self.assertTrue(dash.active)
        for _ in range(12):
            self.run.step({self.b: (-16384, 0, 0)})
            if self.b.body in self.fight.zarya_gravitons.player_pulls:
                break
        self.assertIn(self.b.body, self.fight.zarya_gravitons.player_pulls)
        self.assertFalse(dash.active)
        self.assertNotIn(self.b.body, self.fight.genji_strikes)
        self.advance(field.end_tick - self.run.frame)
        position = tuple(self.b.mover.position)
        self.advance(5)
        self.assertEqual(tuple(self.b.mover.position), position)
        self.assertFalse(dash.active)
        self.assertEqual(self.health(self.a), 400)

    def test_duplicate_world_tick_cannot_repeat_field_damage(self):
        self.cast()
        self.until_field()
        self.advance(1)
        before = self.health()
        self.fight.update(self.fight.now, self.fight.tick, frames=False)
        self.assertEqual(self.health(), before)

    def test_skipped_scheduler_integrates_only_remaining_field_lifetime(self):
        self.cast()
        field = self.until_field()
        self.fight.update((field.end_tick + 20) * 0.016, field.end_tick + 20, frames=False)
        self.assertAlmostEqual(self.health(), 175)
        self.assertFalse(self.fight.zarya_gravitons.fields)
        self.assertAlmostEqual(self.charge(), 1.6, places=4)

    def test_caster_death_does_not_cancel_existing_field(self):
        self.cast()
        self.until_field()
        target = next(t for t in self.fight._targets() if t.owner is self.a)
        self.fight._damage(self.b, target, 500, False, None, self.run.frame)
        before = self.health()
        self.advance(20)
        self.assertLess(self.health(), before)

    def test_body_replacement_preserves_original_cast_killer_identity(self):
        self.cast()
        field = self.until_field()
        old = self.a.body
        self.run.match.switch_hero(self.a, self.a.hero)
        self.assertNotEqual(self.a.body, old)
        vitals = self.vitals(self.b)
        vitals.now[combat.HEALTH_POOL] = 0.1
        self.advance(3)
        self.assertTrue(vitals.dead)
        self.assertEqual(vitals.killer, old)
        self.assertEqual(field.source_entity, old)
        self.assertEqual(self.b.client.messages(combat.DEATH_NOTICE)[-1]["+0x7C"]["+0x0"], old)

    def test_departure_cancels_remaining_field(self):
        self.cast()
        self.until_field()
        self.a.gone = True
        before = self.health()
        self.advance(2)
        self.assertEqual(self.health(), before)
        self.assertFalse(self.fight.zarya_gravitons.fields)

    def test_inflight_ultimate_uses_cast_team_after_owner_team_change(self):
        self.cast()
        self.a.team = self.b.team
        self.advance(280)
        self.assertAlmostEqual(self.health(), 175)
        self.assertEqual(self.health(self.c), 200)

    def test_inflight_secondary_preserves_team_for_direct_and_splash(self):
        self.run.step({self.a: (0, 0, 2)})
        self.a.team = self.b.team
        self.advance(90)
        self.assertAlmostEqual(self.health(), 153)
        self.assertEqual(self.health(self.c), 200)

    def test_unleased_projectile_keeps_emission_source_after_body_replacement(self):
        # Explicit delivery-unavailable fixture: authoritative flight must still
        # retain its original source; no client presentation is claimed here.
        with patch.object(self.fight.projectiles, "fired", return_value=None):
            self.cast()
        rocket, = self.fight.rockets
        self.assertIsNone(rocket.shot)
        old = self.a.body
        self.run.match.switch_hero(self.a, self.a.hero)
        self.assertNotEqual(self.a.body, old)
        vitals = self.vitals(self.b)
        vitals.now[combat.HEALTH_POOL] = 3
        self.advance(30)
        self.assertTrue(vitals.dead)
        self.assertEqual(vitals.killer, old)
        self.assertEqual(self.b.client.messages(combat.DEATH_NOTICE)[-1]["+0x7C"]["+0x0"], old)

    def test_departure_before_impact_cancels_damage_and_deployment(self):
        self.cast()
        self.a.gone = True
        self.advance(40)
        self.assertEqual(self.health(), 200)
        self.assertFalse(self.fight.rockets)
        self.assertFalse(self.fight.zarya_gravitons.fields)

    def test_match_end_before_impact_cancels_damage_and_deployment(self):
        self.cast()
        self.run.match.ended = True
        self.advance(40)
        self.assertEqual(self.health(), 200)
        self.assertFalse(self.fight.rockets)
        self.assertFalse(self.fight.zarya_gravitons.fields)

    def test_departure_releases_pull_before_next_scheduler_update(self):
        self.cast()
        self.until_field()
        self.assertIsNotNone(self.fight.zarya_gravitons.movement_for(self.b))
        self.a.gone = True
        self.assertIsNone(self.fight.zarya_gravitons.movement_for(self.b))

    def test_match_end_releases_pull_before_next_scheduler_update(self):
        self.cast()
        self.until_field()
        self.run.match.ended = True
        self.assertIsNone(self.fight.zarya_gravitons.movement_for(self.b))


if __name__ == "__main__":
    unittest.main()
