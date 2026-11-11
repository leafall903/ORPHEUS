"""Actual Zarya secondary projectile/splash behavior; geometry is reconstructed."""

import math
import unittest
from itertools import product
from unittest.mock import patch

from test_game_combat import Run, aim, in_world

from ow174.game import combat, stats, zarya
from ow174.game.collision import World
from ow174.game.commands import Command
from ow174.game.content import heroes


class ZaryaSecondaryTests(unittest.TestCase):
    def setUp(self):
        self.run = Run(names=("Zarya", "Enemy", "Other"))
        self.a, self.b, self.c = self.run.players
        self.fight = self.run.match.combat
        self.run.match.bots.clear()
        self.run.match._pending_bot_scripts.clear()
        self.run.match.switch_hero(self.a, heroes()[0x02E0000000000068])
        for player, position in ((self.a, [0, 1, 0]), (self.b, [0, 1, 5]),
                                 (self.c, [10, 1, 5])):
            player.mover.position[:] = position
        self.c.team = self.b.team

    def health(self, player):
        target = next(t for t in self.fight._targets() if t.owner is player)
        return self.fight.vitals_of(target).total

    def fire(self, *, energy=0, yaw=0, pitch=0):
        self.a.body_script.component.entity_var(87).base = energy
        self.run.step({self.a: (yaw, pitch, 2)})

    def advance(self, frames=60):
        for _ in range(frames):
            self.run.step({self.a: (0, 0, 0)})

    def land_without_input(self):
        rocket, = self.fight.rockets
        for _ in range(60):
            self.run.frame += 1
            self.run.match.update(self.run.frame * combat.TICK, self.run.frame)
            if rocket not in self.fight.rockets:
                return
        self.fail("secondary did not hit")

    def test_self_splash_uses_half_damage_and_upward_impulse_without_credit(self):
        for barrier in (False, True):
            with self.subTest(barrier=barrier):
                self.setUp()
                self.a.body_script.ult_seconds = 0
                self.run.step({self.a: (0, 0, 8 if barrier else 0)})
                self.fire(pitch=16384)
                rocket, = self.fight.rockets
                self.assertEqual(rocket.blast.self_scale, 0.5)
                self.assertEqual(rocket.blast.self_impulse_scale, 0.5)
                self.land_without_input()
                self.assertAlmostEqual(self.health(self.a), 376.5)
                self.assertAlmostEqual(self.a.mover.velocity[1], 4)
                self.assertEqual(self.a.body_script.component.entity_var(87).value(), 0)
                self.assertEqual(self.a.body_script.component.entity_var(342).value(), 0)
                self.assertFalse(self.a.client.messages(combat.HIT))
                self.assertEqual(self.a.stats.values.get(stats.HERO_DAMAGE, 0), 0)
                before = self.a.mover.position[1]
                self.advance(8)
                self.assertGreater(self.a.mover.position[1], before)

    def test_enemy_body_impulse_and_personal_barrier_admission(self):
        for barrier in (False, True):
            with self.subTest(barrier=barrier):
                self.setUp()
                self.assertTrue(self.run.match.switch_hero(self.b, self.a.hero))
                self.b.mover.position[:] = [0, 1, 5]
                self.run.step({self.b: (0, 0, 8 if barrier else 0)})
                self.fire()
                self.land_without_input()
                if barrier:
                    self.assertEqual(self.health(self.b), 400)
                    self.assertEqual(tuple(self.b.mover.velocity), (0, 0, 0))
                else:
                    self.assertEqual(self.health(self.b), 353)
                    self.assertAlmostEqual(self.b.mover.velocity[1], 8)
                    self.assertAlmostEqual(self.b.mover.velocity[2], 5)

    def test_lethal_self_splash_uses_death_and_respawn_without_combat_credit(self):
        source = next(t for t in self.fight._targets() if t.owner is self.a)
        self.fight.vitals_of(source).damage(390)
        old_body = self.a.body
        self.a.body_script.ult_seconds = 0
        self.a.body_script.set_ult_charge(123)
        self.fire(pitch=16384)
        self.land_without_input()
        self.assertTrue(self.fight.is_dead(self.a))
        self.assertEqual(self.a.stats.values.get(stats.ELIMINATIONS, 0), 0)
        self.assertEqual(self.a.stats.values.get(stats.HERO_DAMAGE, 0), 0)
        self.assertFalse(self.a.client.messages(combat.HIT))
        self.assertEqual(self.a.body_script.component.entity_var(342).value(), 123)
        self.advance(190)
        self.assertNotEqual(self.a.body, old_body)
        self.assertEqual(self.health(self.a), 400)

    def test_lethal_self_splash_retires_ally_projected_pool(self):
        self.assertTrue(self.run.match.switch_hero(self.b, self.a.hero))
        self.b.team = self.a.team
        self.b.mover.position[:] = [3, 1, 0]
        self.c.mover.position[:] = [0, 1, -5]
        self.run.step({self.b: (-16384, 0, 16)})
        projected = self.fight.zarya_barriers.projected
        for _ in range(20):
            self.run.step({self.c: (0, 0, 1)})
            if self.b.body in projected:
                break
        self.assertEqual(projected[self.b.body].recipient, self.a.body)
        self.assertEqual(self.health(self.a), 400)

        source = next(t for t in self.fight._targets() if t.owner is self.a)
        self.fight.vitals_of(source).damage(390)
        self.fire(pitch=16384)
        self.land_without_input()
        self.assertTrue(self.fight.is_dead(self.a))
        self.assertNotIn(self.b.body, projected)

    def test_zero_energy_contact_deals_47_after_input_release(self):
        self.fire()
        self.advance()
        self.assertAlmostEqual(self.health(self.b), 153)
        self.assertFalse(self.fight.rockets)
        self.assertFalse(any(m["+0x78"]["+0x8"] & combat.CRITICAL
                             for m in self.a.client.messages(combat.HIT)))

    def test_started_secondary_keeps_lethal_impact_time_across_sparse_updates(self):
        outcomes = []
        for sparse in (False, True):
            self.setUp()
            self.a.body_script.ult_seconds = 0
            target = next(t for t in self.fight._targets() if t.owner is self.b)
            vitals = self.fight.vitals_of(target)
            vitals.damage(vitals.total - 30)
            self.fire()
            rocket, = self.fight.rockets
            launched = self.run.frame
            self.assertEqual(rocket.flown, 0)
            end = launched + 80
            for tick in (end,) if sparse else range(launched + 1, end + 1):
                self.run.match.update(tick * combat.TICK, tick, frames=False)
            self.assertFalse(self.fight.rockets)
            self.assertTrue(vitals.dead)
            self.assertEqual(vitals.killer, self.a.body)
            self.assertEqual(self.a.body_script.component.entity_var(342).value(), 30)
            outcomes.append((vitals.death_tick - launched, rocket.distance))
        self.assertLess(outcomes[0][0], 15)
        self.assertEqual(outcomes[1], outcomes[0])

    def test_secondary_launches_on_separate_world_ticks_during_catchup(self):
        match = self.run.match
        self.assertTrue(match.switch_hero(self.c, self.a.hero))
        self.c.mover.position[:] = (10, 1, 5)
        self.fire()
        first, = self.fight.rockets
        launched = self.run.frame
        match.update(launched * combat.TICK, launched, frames=False)
        self.assertEqual(first.flown, 0)

        late_tick = launched + 200
        match.tick = late_tick
        self.c.take_commands([Command(late_tick + 500, buttons=2)])
        _, late = self.fight.rockets
        match.update(late_tick * combat.TICK, late_tick, frames=False)
        self.assertEqual(self.fight.rockets, [late])
        self.assertEqual(self.health(self.b), 153)
        self.assertEqual(late.flown, 0)
        match.update((late_tick + 1) * combat.TICK, late_tick + 1, frames=False)
        self.assertAlmostEqual(late.flown, combat.TICK)

    def test_sparse_secondary_splash_only_reaches_bodies_alive_at_impact(self):
        for birth_offset in (5, 20):
            with self.subTest(birth_offset=birth_offset):
                self.setUp()
                self.fire()
                self.advance(1)
                match = self.run.match
                started = self.run.frame
                match.tick = started + birth_offset
                late = match.add_player(4, "Late enemy", self.b.hero.guid, self.b.team, False)
                in_world(match, late)
                self.assertTrue(match.switch_hero(late, self.b.hero))
                late.mover.position[:] = (1.5, 1, 5)
                end = started + 80
                match.update(end * combat.TICK, end, frames=False)
                self.assertFalse(self.fight.rockets)
                self.assertEqual(self.health(self.b), 153)
                if birth_offset == 5:
                    self.assertLess(self.health(late), 200)
                else:
                    self.assertEqual(self.health(late), 200)
                    self.assertEqual(late.mover.velocity, (0, 0, 0))

    def test_50_energy_contact_deals_71(self):
        self.fire(energy=50)
        self.advance()
        self.assertAlmostEqual(self.health(self.b), 129)

    def test_100_energy_contact_deals_95(self):
        self.fire(energy=100)
        self.advance()
        self.assertAlmostEqual(self.health(self.b), 105, places=4)

    def test_energy_gained_during_flight_does_not_change_emitted_damage(self):
        self.fire()
        self.a.body_script.component.entity_var(87).base = 100
        self.advance()
        self.assertAlmostEqual(self.health(self.b), 153)

    def test_aimed_miss_has_no_damage_or_hit_feedback(self):
        self.fire(yaw=16384)
        self.advance()
        self.assertEqual(self.health(self.b), 200)
        self.assertEqual(self.health(self.c), 200)
        self.assertFalse(self.a.client.messages(combat.HIT))

    def test_wall_explosion_blocks_far_side_but_damages_near_side(self):
        self.b.mover.position[:] = [0, 1, 6]
        self.c.mover.position[:] = [1.2, 1, 4]
        wall = World([(-10, 0, 5, 10, 0, 5, 10, 10, 5),
                      (-10, 0, 5, 10, 10, 5, -10, 10, 5)])
        with patch.object(self.fight, "_collision", return_value=wall):
            self.fire()
            self.land_without_input()
        self.assertEqual(self.health(self.b), 200)
        self.assertEqual(tuple(self.b.mover.velocity), (0, 0, 0))
        self.assertLess(self.health(self.c), 200)
        self.assertGreater(self.health(self.c), 153)
        self.assertGreater(self.c.mover.velocity[1], 0)

    def test_body_impact_splash_does_not_cross_a_wall(self):
        self.b.mover.position[:] = [0, 1, 4]
        self.c.mover.position[:] = [0, 1, 6]
        wall = World([(-10, 0, 5, 10, 0, 5, 10, 10, 5),
                      (-10, 0, 5, 10, 10, 5, -10, 10, 5)])
        with patch.object(self.fight, "_collision", return_value=wall):
            self.fire()
            self.advance()
        self.assertAlmostEqual(self.health(self.b), 153)
        self.assertEqual(self.health(self.c), 200)

    def test_nearby_enemy_takes_reduced_splash_without_a_contact_hit(self):
        self.c.mover.position[:] = [1.5, 1, 5]
        self.fire()
        self.land_without_input()
        self.assertAlmostEqual(self.health(self.b), 153)
        self.assertGreater(self.health(self.c), 153)
        self.assertLess(self.health(self.c), 200)
        self.assertGreater(self.c.mover.velocity[0], 0)
        self.assertGreater(self.c.mover.velocity[1], 6)
        self.assertLess(self.c.mover.velocity[1], 8)

    def test_enemy_outside_blast_radius_takes_no_splash(self):
        self.c.mover.position[:] = [3.5, 1, 5]
        self.fire()
        self.land_without_input()
        self.assertAlmostEqual(self.health(self.b), 153)
        self.assertEqual(self.health(self.c), 200)
        self.assertEqual(tuple(self.c.mover.velocity), (0, 0, 0))

    def test_splash_hits_visible_bubble_surface_outside_recipient_body_reach(self):
        cases = ("edge", "break", "outside", "wall", "ally")
        for projected, case in product((False, True), cases):
            with self.subTest(projected=projected, case=case):
                self.setUp()
                self.assertTrue(self.run.match.switch_hero(self.c, self.a.hero))
                self.c.team = self.a.team if case == "ally" else self.b.team
                self.c.mover.position[:] = [4.7 if case == "outside" else 3.3, 1, 4.5]
                self.c.mover.body = None
                caster = self.c
                if projected:
                    match = self.run.match
                    caster = match.add_player(4, "Projector", zarya.HERO, self.c.team, False)
                    in_world(match, caster)
                    self.run.players.append(caster)
                    self.assertTrue(match.switch_hero(caster, self.a.hero))
                    caster.mover.position[:] = [7, 1, 4.5]
                    caster.mover.body = None
                angles = aim(self.fight._eye(caster), self.fight._eye(self.c)) if projected else (0, 0)
                self.run.step({caster: (*angles, 16 if projected else 8)})
                barriers = self.fight.zarya_barriers
                self.assertTrue(barriers.protects(self.c))
                pool = (barriers.projected if projected else barriers.active)[caster.body]
                capacity = 8 if case == "break" else 200
                pool.remaining = capacity
                if case == "wall":
                    self.run.match.collision_world = World([
                        (-12, 1, -12, -12, 1, 12, 12, 1, 12), (-12, 1, -12, 12, 1, 12, 12, 1, -12),
                        (1.4, 0, 2, 1.4, 0, 8, 1.4, 6, 8), (1.4, 0, 2, 1.4, 6, 8, 1.4, 6, 2),
                    ])
                target = next(t for t in self.fight._targets() if t.owner is self.c)
                self.fire()
                with patch.object(self.fight, "_explode", wraps=self.fight._explode) as explosion:
                    self.land_without_input()
                explosion.assert_called_once()
                rocket, point = explosion.call_args.args
                a, b = target.segment()
                body_distance = combat.segment_distance(point, a, b) - target.radius
                self.assertEqual(rocket.blast.splash(body_distance), 0)
                x, y, z = target.feet
                centre = (x, y + target.height / 2, z)
                surface_distance = math.dist(point, centre) - zarya.BUBBLE_RADIUS
                edge_damage = rocket.blast.amount * rocket.blast.splash(surface_distance)
                if case == "outside":
                    self.assertEqual(edge_damage, 0)
                else:
                    self.assertGreater(edge_damage, 8)
                    self.assertLess(edge_damage, 47)
                absorbed = min(capacity, edge_damage) if case in ("edge", "break") else 0
                self.assertAlmostEqual(pool.remaining, capacity - absorbed)
                self.assertEqual(self.health(self.b), 153)
                self.assertEqual(self.health(self.c), 400)
                self.assertEqual(tuple(self.c.mover.velocity), (0, 0, 0))
                self.run.step({})
                energy = caster.body_script.component.entity_var(87).value() or 0
                self.assertAlmostEqual(energy, absorbed / 5, places=4)

    def test_nearby_ally_takes_no_splash(self):
        self.c.mover.position[:] = [1.5, 1, 5]
        self.c.team = self.a.team
        self.fire()
        self.land_without_input()
        self.assertAlmostEqual(self.health(self.b), 153)
        self.assertEqual(self.health(self.c), 200)
        self.assertEqual(tuple(self.c.mover.velocity), (0, 0, 0))


if __name__ == "__main__":
    unittest.main()
