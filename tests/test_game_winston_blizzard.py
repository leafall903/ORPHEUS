"""Winston versus the actual 064A launch and resource-derived 065E area."""

import unittest
from dataclasses import replace
from math import dist

import test_game_winston_frost as fixtures
from test_game_combat import aim
from test_game_mover import box, wall

from ow174.game import combat, dva, protections, winston, zarya
from ow174.game.collision import World
from ow174.game.content import heroes


class WinstonBlizzardTests(unittest.TestCase):
    move = staticmethod(fixtures.WinstonFrostTests.move)
    health = fixtures.WinstonFrostTests.health

    def setUp(self):
        fixtures.WinstonFrostTests.setUp(self)
        self.manager = self.fight.blizzard
        self.move(self.caster, (0, 1, -5))

    def step(self, attack=0, cast=0, *, angle=None, ally=0, ally_yaw=-32768):
        if angle is None:
            angle = aim(self.fight._eye(self.caster), (0, 1, 0))
        self.run.step({self.owner: (-32768, 0, attack), self.caster: (*angle, cast),
                       self.ally: (ally_yaw, 0, ally)})

    def launch(self, *, angle=None):
        script = self.caster.body_script
        cost = script.component.entity_var(338).value()
        self.assertEqual(cost, 1610)
        script.set_ult_charge(cost)
        self.step(cast=32, angle=angle)
        for _ in range(30):
            self.step(angle=angle)
        self.assertEqual(script.component.entity_var(342).value(), 0)
        self.assertFalse(self.manager.flights)
        self.step(angle=angle)
        self.assertEqual(len(self.manager.flights), 1)
        return self.manager.flights[0]

    def landed(self):
        for _ in range(150):
            if self.manager.areas:
                break
            self.step()
        self.assertFalse(self.manager.flights)
        self.assertEqual(len(self.manager.areas), 1)
        return next(iter(self.manager.areas.values()))

    def unarmoured(self):
        target = next(t for t in self.fight._targets() if t.owner is self.owner)
        vitals = self.fight.vitals_of(target)
        vitals.now[combat.ARMOUR_POOL] = 0
        return vitals

    def test_charge_and_ballistic_flight_have_no_direct_contact_damage(self):
        self.step(cast=32)
        for _ in range(40):
            self.step()
        self.assertFalse(self.manager.flights)
        self.assertFalse(self.manager.areas)
        flight = self.launch(angle=(0, 0))
        start = flight.position
        self.assertEqual(flight.left, 60)
        self.assertAlmostEqual(flight.gravity, 9.8, places=6)
        self.match.update(self.run.frame * combat.TICK, self.run.frame, frames=False)
        self.assertEqual(flight.position, start)
        self.assertEqual(flight.left, 60)
        for _ in range(20):
            self.step()
        elapsed = 20 * combat.TICK
        self.assertAlmostEqual(flight.position[1], start[1] - 9.8 * elapsed**2 / 2)
        self.assertGreater(flight.position[2], 0)
        self.assertEqual(self.health(self.owner), 500)
        self.assertFalse(self.manager.areas)
        weapon = next(i for i in self.caster.body_script.component.instances.values()
                      if i.graph.index == 0x05E6)
        self.assertEqual(weapon.vars[53].value(), 120)

    def test_area_damage_freeze_blocks_winston_until_thaw(self):
        vitals = self.unarmoured()
        self.launch()
        area = self.landed()
        self.assertEqual(area.ends - area.starts, 4250)
        self.assertEqual(area.starts - self.run.frame * 16, 200)
        while self.run.frame * 16 + 16 <= area.starts:
            self.step()
        self.assertEqual(vitals.total, 350)
        self.step()
        self.assertLess(vitals.total, 350)
        self.assertAlmostEqual(self.fight.frost_slow(self.owner), .5)
        while self.run.frame * 16 < area.ends and self.fight.frozen_aim(self.owner) is None:
            self.step()
        self.assertIsNotNone(self.fight.frozen_aim(self.owner))
        deadline = vitals.freeze_until
        script = self.owner.body_script
        cost = script.component.entity_var(338).value()
        script.set_ult_charge(cost)
        weapon = next(i for i in script.component.instances.values() if i.graph.index == 0xFE)
        self.step(1 | 8 | 16 | 32)
        self.assertEqual(weapon.vars[53].value(), 100)
        self.assertEqual(script.component.entity_var(342).value(), cost)
        self.assertFalse(winston.primal_active(script.component))
        self.assertNotIn(self.owner.body, self.fight.winston_jump.flights)
        self.assertNotIn(self.owner.body, self.fight.barriers.actors)
        while self.run.frame * 16 < max(deadline, area.ends):
            self.step()
        self.assertFalse(self.manager.areas)
        self.assertAlmostEqual(vitals.total, 265)
        self.assertIsNone(self.fight.frozen_aim(self.owner))
        self.step(8)
        self.assertIn(self.owner.body, self.fight.winston_jump.flights)

    def test_static_wall_range_and_same_team_reject_landed_area(self):
        for rejection in ('wall', 'range', 'team'):
            with self.subTest(rejection=rejection):
                if rejection != 'wall':
                    self.setUp()
                self.launch()
                area = self.landed()
                x, _, z = area.position
                if rejection == 'wall':
                    self.move(self.owner, (x, 1, z + 4))
                    self.match.collision_world = World(
                        box(-40, 40, -1, 1, -40, 40) + wall(-20, z + 2, 20, z + 2, 1, 8)
                    )
                elif rejection == 'range':
                    self.move(self.owner, (x, 1, z + 11))
                else:
                    self.owner.team = self.caster.team
                for _ in range(35):
                    self.step()
                self.assertEqual(self.health(self.owner), 500)
                self.assertEqual(self.fight.frost_slow(self.owner), 1)
                if rejection == 'wall':
                    self.move(self.owner, (x, 1, z + 1))
                elif rejection == 'range':
                    self.move(self.owner, (x, 1, z + 9.9))
                else:
                    self.owner.team = 0
                for _ in range(16):
                    self.step()
                self.assertLess(self.health(self.owner), 500)
                self.assertLess(self.fight.frost_slow(self.owner), 1)

    def test_primal_expires_without_owner_input_while_blizzard_freeze_remains(self):
        script = self.owner.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        self.step(32)
        self.assertTrue(winston.primal_active(script.component))
        self.assertEqual(self.health(self.owner), 1000)
        expires = self.run.frame + 625
        self.run.frame += 345
        self.match.update(self.run.frame * combat.TICK, self.run.frame, frames=False)

        self.launch()
        area = self.landed()
        self.assertLess(self.run.frame, expires - 1)
        self.run.frame = expires - 1
        self.match.update(self.run.frame * combat.TICK, self.run.frame, frames=False)
        self.assertLess(self.run.frame * 16, area.ends)
        self.assertIsNotNone(self.fight.frozen_aim(self.owner))
        self.assertTrue(winston.primal_active(script.component))
        self.assertGreater(self.health(self.owner), 500)

        self.run.frame = expires
        self.match.update(self.run.frame * combat.TICK, self.run.frame, frames=False)
        self.assertFalse(winston.primal_active(script.component))
        self.assertNotIn(self.owner.body, self.fight.primal_bonus)
        self.assertLess(self.health(self.owner), 500)
        self.assertIsNotNone(self.fight.frozen_aim(self.owner))

    def test_sparse_world_tick_preserves_flight_damage_and_expiry(self):
        vitals = self.unarmoured()
        self.launch()
        self.run.frame += 420
        self.match.update(self.run.frame * combat.TICK, self.run.frame, frames=False)
        self.assertFalse(self.manager.flights)
        self.assertFalse(self.manager.areas)
        self.assertAlmostEqual(vitals.total, 265)
        self.assertIsNone(self.fight.frozen_aim(self.owner))

    def test_actual_matrix_consumes_flight_without_creating_an_area(self):
        mech = replace(heroes()[dva.HERO], body=0x0400000000000407)
        self.assertTrue(self.match.switch_hero(self.ally, mech))
        self.move(self.ally, (0, 1, 0))
        for _ in range(100):
            self.step()
        self.launch(angle=(0, 0))
        matrix = next(i for i in self.ally.body_script.component.instances.values()
                      if i.graph.index == 0x02CC)
        for _ in range(70):
            self.step(ally=2)
            if not self.manager.flights:
                break
        self.assertTrue(matrix.states[3].active)
        self.assertFalse(self.manager.flights)
        self.assertFalse(self.manager.areas)
        self.assertEqual(self.health(self.owner), 500)

    def test_deployed_ice_wall_anchors_blizzard_and_occludes_area_until_cancel(self):
        self.assertTrue(self.match.switch_hero(self.ally, heroes()[protections.MEI]))
        self.move(self.ally, (0, 1, 2))
        angle = aim(self.fight._eye(self.ally), (0, 1, -2))
        for button in (16, 0, 1, 0):
            self.run.step({self.ally: (*angle, button)})
        self.assertEqual(len(self.fight.walls.pieces), 5)
        pillar = min(self.fight.walls.pieces.values(), key=lambda piece: abs(piece.feet[0]))
        self.move(self.ally, (20, 1, 0))
        self.launch(angle=(0, 0))
        area = self.landed()
        self.assertAlmostEqual(area.position[2], pillar.feet[2] - pillar.depth, delta=.01)
        self.assertGreater(area.position[1], 1)
        for _ in range(35):
            self.step()
        self.assertEqual(self.health(self.owner), 500)
        self.assertEqual(self.fight.frost_slow(self.owner), 1)
        self.assertEqual(pillar.health, 400)
        self.step(ally=16)
        self.assertFalse(self.fight.walls.pieces)
        self.step()
        self.assertLess(self.health(self.owner), 500)
        self.assertAlmostEqual(self.fight.frost_slow(self.owner), .5)

    def test_projected_zarya_barrier_rejects_area_frost_until_expiry(self):
        self.assertTrue(self.match.switch_hero(self.ally, heroes()[zarya.HERO]))
        self.move(self.ally, (0, 1, 3))
        self.launch()
        self.landed()
        self.step(ally=16)
        for _ in range(35):
            self.step()
        self.assertTrue(self.fight.zarya_barriers.protects(self.owner))
        self.assertEqual(self.health(self.owner), 500)
        self.assertEqual(self.fight.frost_slow(self.owner), 1)
        for _ in range(150):
            self.step()
            if self.health(self.owner) < 500:
                break
        self.assertFalse(self.fight.zarya_barriers.protects(self.owner))
        self.assertLess(self.health(self.owner), 500)
        self.assertAlmostEqual(self.fight.frost_slow(self.owner), .5)

    def test_tesla_kill_during_blizzard_windup_prevents_release(self):
        target = next(t for t in self.fight._targets() if t.owner is self.caster)
        vitals = self.fight.vitals_of(target)
        vitals.damage(249)
        script = self.caster.body_script
        cost = script.component.entity_var(338).value()
        script.set_ult_charge(cost)
        self.step(cast=32)
        self.assertEqual(script.component.entity_var(342).value(), 0)
        self.assertFalse(self.manager.flights)
        for _ in range(5):
            self.step(1)
            if vitals.dead:
                break
        self.assertTrue(vitals.dead)
        self.assertEqual(vitals.killer, self.owner.body)
        self.run.frame += 150
        self.match.update(self.run.frame * combat.TICK, self.run.frame, frames=False)
        self.assertFalse(self.manager.flights)
        self.assertFalse(self.manager.areas)
        self.assertEqual(self.health(self.owner), 500)
        self.assertEqual(self.fight.frost_slow(self.owner), 1)

    def test_released_blizzard_lands_after_actual_winston_tesla_kills_mei(self):
        target = next(t for t in self.fight._targets() if t.owner is self.caster)
        vitals = self.fight.vitals_of(target)
        vitals.damage(249)
        self.launch()
        for _ in range(5):
            self.step(1)
            if vitals.dead:
                break
        self.assertTrue(vitals.dead)
        self.assertEqual(vitals.killer, self.owner.body)
        self.landed()
        for _ in range(20):
            self.step()
        self.assertLess(self.health(self.owner), 500)

    def test_airborne_blizzard_crosses_sphere_before_arming_inside(self):
        self.move(self.caster, (0, 1, -10))
        self.step(16)
        for _ in range(4):
            self.step()
        bubble = self.fight.barriers.actors[self.owner.body]
        angle = aim(self.fight._eye(self.caster), (0, 1, 10))
        flight = self.launch(angle=angle)
        self.assertGreater(dist(flight.position, bubble.centre), bubble.radius)
        for _ in range(30):
            self.step()
            if dist(flight.position, bubble.centre) < bubble.radius:
                break
        self.assertIn(flight, self.manager.flights)
        self.assertLess(dist(flight.position, bubble.centre), bubble.radius)
        self.assertFalse(self.manager.areas)
        self.assertEqual(self.health(self.owner), 500)
        self.assertEqual(bubble.health, 700)

        area = self.landed()
        self.assertLess(dist(area.position, bubble.centre), bubble.radius)
        while self.run.frame * 16 + 16 <= area.starts:
            self.step()
        self.assertEqual(self.health(self.owner), 500)
        self.assertEqual(self.fight.frost_slow(self.owner), 1)
        self.step()
        self.assertLess(self.health(self.owner), 500)
        self.assertAlmostEqual(self.fight.frost_slow(self.owner), .5)
        self.assertEqual(bubble.health, 700)

    def test_winston_sphere_blocks_area_across_shell_and_preserves_hp(self):
        self.move(self.caster, (0, 1, -10))
        angle = aim(self.fight._eye(self.caster), (0, 1, -7))
        self.launch(angle=angle)
        area = self.landed()
        self.step(16)
        for _ in range(30):
            self.step()
        bubble = self.fight.barriers.actors[self.owner.body]
        self.assertGreater(abs(area.position[2] - bubble.centre[2]), bubble.radius)
        self.assertEqual(self.health(self.owner), 500)
        self.assertEqual(self.fight.frost_slow(self.owner), 1)
        self.assertEqual(bubble.health, 700)
        self.move(self.owner, (0, 1, -6))
        for _ in range(5):
            self.step()
        self.assertLess(self.health(self.owner), 500)
        self.assertAlmostEqual(self.fight.frost_slow(self.owner), .5)
        self.assertEqual(bubble.health, 700)

    def test_area_and_target_on_same_side_of_sphere_do_not_cross_shell(self):
        self.launch()
        area = self.landed()
        self.step(16)
        for _ in range(30):
            self.step()
        bubble = self.fight.barriers.actors[self.owner.body]
        self.assertLess(abs(area.position[2] - bubble.centre[2]), bubble.radius)
        self.assertLess(self.health(self.owner), 500)
        self.assertEqual(bubble.health, 700)
        self.move(self.owner, (0, 1, 6))
        before = self.health(self.owner)
        for _ in range(30):
            self.step()
        self.assertEqual(self.health(self.owner), before)

    def test_actual_genji_reflect_changes_area_team_and_damage_credit(self):
        self.assertTrue(self.match.switch_hero(self.ally, heroes()[0x02E0000000000029]))
        self.move(self.ally, (0, 1, 0))
        self.move(self.owner, (0, 1, -4))
        self.owner.team = self.caster.team
        vitals = self.unarmoured()
        flight = self.launch(angle=(0, 0))
        self.step(ally=16)
        for _ in range(25):
            self.step()
            if flight.owner is self.ally:
                break
        self.assertIs(flight.owner, self.ally)
        self.assertEqual(flight.body, self.ally.body)
        self.assertLess(flight.velocity[2], 0)
        area = self.landed()
        self.assertIs(area.owner, self.ally)
        self.move(self.ally, (area.position[0], 1, area.position[2]))
        while self.run.frame * 16 < area.ends:
            self.step()
        self.assertAlmostEqual(vitals.total, 265)
        self.assertEqual(self.health(self.ally), 200)
        self.assertTrue(self.ally.client.messages(combat.HIT))
        self.assertFalse(self.caster.client.messages(combat.HIT))

    def test_active_genji_refuses_rear_and_friendly_blizzard(self):
        for rejection in ('rear', 'team'):
            with self.subTest(rejection=rejection):
                if rejection == 'team':
                    self.setUp()
                self.assertTrue(self.match.switch_hero(self.ally, heroes()[0x02E0000000000029]))
                self.move(self.ally, (0, 1, 0))
                if rejection == 'team':
                    self.ally.team = self.caster.team
                yaw = 0 if rejection == 'rear' else -32768
                flight = self.launch(angle=(0, 0))
                self.step(ally=16, ally_yaw=yaw)
                for _ in range(42):
                    self.step(ally_yaw=yaw)
                instance = next(i for i in self.ally.body_script.component.instances.values()
                                if i.graph.index == 0x03A3)
                self.assertTrue(instance.states[10].active)
                self.assertIs(flight.owner, self.caster)
                self.assertGreater(flight.velocity[2], 0)
                self.assertIs(self.landed().owner, self.caster)

    def test_reflected_flight_retires_with_reflector_body(self):
        self.assertTrue(self.match.switch_hero(self.ally, heroes()[0x02E0000000000029]))
        self.move(self.ally, (0, 1, 0))
        flight = self.launch(angle=(0, 0))
        self.step(ally=16)
        for _ in range(25):
            self.step()
            if flight.owner is self.ally:
                break
        self.assertIs(flight.owner, self.ally)
        self.assertTrue(self.match.switch_hero(self.caster, heroes()[winston.HERO]))
        self.assertIn(flight, self.manager.flights)
        self.assertTrue(self.match.switch_hero(self.ally, heroes()[winston.HERO]))
        self.assertNotIn(flight, self.manager.flights)
        self.assertFalse(self.manager.areas)
