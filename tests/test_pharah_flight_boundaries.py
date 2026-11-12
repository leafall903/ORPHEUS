"""Actual flight input at authored fuel gates and swept static ceiling boundaries.

These exercise the reconstructed mover/force adapter, not native phase parity.
"""

import math
import unittest

import test_pharah_combat as primary
from test_game_mover import box, ramp

from ow174.game.collision import World
from ow174.game.mover import STEP


class PharahFlightBoundaryTests(unittest.TestCase):
    encounter = primary.PharahCombatTests.encounter

    def room(self, *, roof_end=100):
        run, player, enemy = self.encounter()
        floor = player.mover.position[1]
        enemy.mover.position[0] = 30
        enemy.mover.body = None
        ceiling = floor + 3.2
        world = World(box(-100, 100, floor - 1, floor, -100, 100)
                      + box(-100, 100, ceiling, ceiling + 1, -100, roof_end))
        run.match.collision_world = world
        player.mover.use(world=world)  # retire the old CharacterBody/world binding
        return run, player, floor, ceiling

    def hover(self, player):
        return next(i for i in player.body_script.component.instances.values() if i.graph.index == 0x25)

    def thrust(self, instance):
        return any(s.active and s.node.cls == "STU_A9C4FF83" for s in instance.states.values())

    def exhaust_hover(self, buttons):
        run, player, _ = self.encounter()
        hover = self.hover(player)
        for _ in range(180):
            run.step({player: (0, 0, buttons)})
            if hover.vars[87].value() == 0:
                break
        self.assertEqual(hover.vars[87].value(), 0)
        self.assertFalse(self.thrust(hover))
        return run, player, hover

    def test_jump_jet_hits_ceiling_spends_cooldown_and_lands_without_recast(self):
        run, player, floor, ceiling = self.room()
        jet = next(i for i in player.body_script.component.instances.values() if i.graph.index == 0x27)
        run.step({player: (0, 0, 8)})
        self.assertEqual(player.mover.velocity[1], 20)
        self.assertGreater(jet.vars[61].value(), 9.9)
        top = floor
        touched = False
        for _ in range(160):
            run.step({})
            top = max(top, player.mover.position[1])
            touched |= abs(player.mover.position[1] + player.mover.data.stand_height - ceiling) < .01
            self.assertLessEqual(player.mover.position[1] + player.mover.data.stand_height,
                                 ceiling + STEP)
        self.assertTrue(touched)
        self.assertGreater(top, floor + 1)
        self.assertAlmostEqual(player.mover.position[1], floor, delta=STEP)
        self.assertEqual(player.mover.velocity[1], 0)
        self.assertGreater(jet.vars[61].value(), 7)
        run.step({player: (0, 0, 8)})
        self.assertEqual(player.mover.velocity[1], 0)
        self.assertGreater(jet.vars[61].value(), 7)

    def test_hover_at_ceiling_still_spends_fuel_then_release_falls_and_recharges(self):
        run, player, floor, ceiling = self.room()
        free, control, _ = self.encounter()
        hover, normal = self.hover(player), self.hover(control)
        touched = False
        for _ in range(100):
            run.step({player: (0, 0, 2)})
            free.step({control: (0, 0, 2)})
            touched |= abs(player.mover.position[1] + player.mover.data.stand_height - ceiling) < .01
            self.assertLessEqual(player.mover.position[1] + player.mover.data.stand_height,
                                 ceiling + STEP)
        self.assertTrue(touched)
        self.assertEqual(hover.vars[87].value(), normal.vars[87].value())
        self.assertLess(hover.vars[87].value(), 21)
        for _ in range(180):
            run.step({})
        self.assertFalse(self.thrust(hover))
        self.assertEqual(hover.vars[87].value(), hover.vars[971].value())
        self.assertAlmostEqual(player.mover.position[1], floor, delta=STEP)

    def test_hover_resumes_rise_after_actual_jet_carries_capsule_past_roof_edge(self):
        run, player, _, ceiling = self.room(roof_end=2)
        run.step({player: (0, 0, 8 | 2)})
        touched = cleared = False
        top_after_clear = 0
        for _ in range(100):
            run.step({player: (0, 0, 2)})
            _, y, z = player.mover.position
            if z < 1.5:
                self.assertLessEqual(y + player.mover.data.stand_height, ceiling + STEP)
                touched |= abs(y + player.mover.data.stand_height - ceiling) < .01
            if z > 2.5:
                cleared = True
                top_after_clear = max(top_after_clear, y)
        self.assertTrue(touched)
        self.assertTrue(cleared)
        self.assertGreater(top_after_clear, ceiling)
        self.assertGreater(self.hover(player).vars[87].value(), 0)

    def ramp_room(self, edge):
        run, player, enemy = self.encounter()
        floor = player.mover.position[1]
        slope = math.tan(math.radians(15))
        ceiling = floor + 3.5
        world = World(box(-100, 100, floor - 1, floor, -100, 100)
                      + ramp(0, 6, floor, 15, x0=-100, x1=100)
                      + box(-100, 100, ceiling, ceiling + 1, -4, edge))
        run.match.collision_world = world
        player.mover.position = [0, floor + slope, 1]
        player.mover.use(world=world)
        player.mover.body = None
        enemy.mover.position[0] = 30
        enemy.mover.body = None
        for _ in range(10):
            run.step({})
        self.assertAlmostEqual(player.mover.position[1], floor + slope, delta=3 * STEP)
        return run, player, floor, slope, ceiling

    def test_jet_and_held_hover_clear_a_roof_over_a_ramp(self):
        edge = 4
        run, player, _, _, ceiling = self.ramp_room(edge)
        hover = self.hover(player)
        fuel = hover.vars[87].value()
        run.step({player: (0, 0, 8 | 2)})
        touched = cleared = False
        for _ in range(120):
            run.step({player: (0, 0, 2)})
            _, y, z = player.mover.position
            if z < edge - .5:
                self.assertLessEqual(y + player.mover.data.stand_height, ceiling + STEP)
                touched |= abs(y + player.mover.data.stand_height - ceiling) < .015
            if z > edge + .5 and y > ceiling:
                cleared = True
                break
        self.assertTrue(touched)
        self.assertTrue(cleared)
        self.assertLess(hover.vars[87].value(), fuel)
        self.assertGreater(hover.vars[87].value(), 0)
        for _ in range(230):
            run.step({})
        self.assertFalse(player.mover.airborne)
        self.assertFalse(self.thrust(hover))
        self.assertEqual(hover.vars[87].value(), hover.vars[971].value())

    def test_jet_grounds_on_ramp_after_roof_impact_and_spends_cooldown(self):
        run, player, floor, slope, ceiling = self.ramp_room(edge=30)
        jet = next(i for i in player.body_script.component.instances.values() if i.graph.index == 0x27)
        run.step({player: (16384, 0, 8)})
        self.assertGreater(jet.vars[61].value(), 9.9)
        touched = False
        for _ in range(120):
            run.step({})
            top = player.mover.position[1] + player.mover.data.stand_height
            self.assertLessEqual(top, ceiling + STEP)
            touched |= abs(top - ceiling) < .015
        self.assertTrue(touched)
        self.assertFalse(player.mover.airborne)
        _, y, z = player.mover.position
        self.assertAlmostEqual(y, floor + slope * z, delta=3 * STEP)
        self.assertGreater(jet.vars[61].value(), 7)
        run.step({player: (16384, 0, 8)})
        self.assertFalse(player.mover.airborne)

    def test_held_empty_hover_waits_for_authored_ten_fuel_restart_gate(self):
        run, player, hover = self.exhaust_hover(2)
        before_velocity = player.mover.velocity[1]
        saw_recharge = False
        restarted = False
        for _ in range(80):
            run.step({player: (0, 0, 2)})
            fuel = hover.vars[87].value()
            if self.thrust(hover):
                self.assertGreaterEqual(fuel, 10)
                restarted = True
                break
            saw_recharge |= fuel > 0
            self.assertLess(fuel, 10)
            self.assertLess(player.mover.velocity[1], before_velocity)
            before_velocity = player.mover.velocity[1]
        self.assertTrue(saw_recharge)
        self.assertTrue(restarted)
        for _ in range(200):
            run.step({})
        self.assertFalse(self.thrust(hover))
        self.assertEqual(hover.vars[87].value(), hover.vars[971].value())

    def test_empty_jump_hover_recovers_fuel_and_a_fresh_press_restores_thrust(self):
        run, player, hover = self.exhaust_hover(0x40)
        before_velocity = player.mover.velocity[1]
        for _ in range(10):
            run.step({player: (0, 0, 0x40)})
            self.assertFalse(self.thrust(hover))
            self.assertLess(player.mover.velocity[1], before_velocity)
            before_velocity = player.mover.velocity[1]
        for _ in range(200):
            run.step({})
        self.assertFalse(self.thrust(hover))
        self.assertEqual(hover.vars[87].value(), hover.vars[971].value())
        run.step({player: (0, 0, 0x40)})
        # A fresh grounded jump begins with the normal jump, before the
        # authored hover gate. It must not spend fuel as an instant hover.
        self.assertFalse(self.thrust(hover))
        self.assertEqual(hover.vars[87].value(), hover.vars[971].value())
        self.assertGreater(player.mover.velocity[1], 5)
        restarted = False
        for _ in range(40):
            run.step({player: (0, 0, 0x40)})
            restarted |= self.thrust(hover)
        self.assertTrue(restarted)
        self.assertLess(hover.vars[87].value(), hover.vars[971].value())
        self.assertGreater(player.mover.velocity[1], 0)

    def test_empty_held_jump_restarts_after_fuel_gate_and_authored_hold(self):
        for initial in (0x40, 2 | 0x40):
            with self.subTest(initial=initial):
                run, player, hover = self.exhaust_hover(initial)
                wait = hover.state(hover.graph.node(2).state)
                recharge_delay = hover.state(hover.graph.node(8).state)
                fuel = hover.vars[87]
                self.assertTrue(recharge_delay.active)
                for _ in range(11):
                    run.step({player: (0, 0, initial)})
                    self.assertEqual(fuel.value(), 0)
                    self.assertFalse(self.thrust(hover))
                for _ in range(30):
                    previous_velocity = player.mover.velocity[1]
                    run.step({player: (0, 0, 0x40)})
                    self.assertFalse(self.thrust(hover))
                    self.assertLess(player.mover.velocity[1], previous_velocity)
                    if fuel.value() >= 10:
                        break
                    self.assertFalse(wait.active)
                self.assertGreaterEqual(fuel.value(), 10)
                self.assertTrue(wait.active)
                for _ in range(12):
                    run.step({player: (0, 0, 0x40)})
                    self.assertTrue(wait.active)
                    self.assertFalse(self.thrust(hover))
                run.step({player: (0, 0, 0x40)})
                self.assertFalse(wait.active)
                self.assertTrue(self.thrust(hover))
                previous_velocity = player.mover.velocity[1]
                before = fuel.value()
                run.step({player: (0, 0, 0x40)})
                self.assertLess(fuel.value(), before)
                self.assertGreater(player.mover.velocity[1], previous_velocity)
                for _ in range(60):
                    run.step({player: (0, 0, 0x40)})
                    if fuel.value() == 0:
                        break
                    self.assertTrue(self.thrust(hover))
                self.assertEqual(fuel.value(), 0)
                self.assertFalse(self.thrust(hover))

    def test_empty_jump_restart_wait_cancels_on_release_and_secondary_can_take_over(self):
        for replace_binding in (False, True):
            with self.subTest(replace_binding=replace_binding):
                run, player, hover = self.exhaust_hover(0x40)
                wait = hover.state(hover.graph.node(2).state)
                for _ in range(45):
                    run.step({player: (0, 0, 0x40)})
                    if wait.active:
                        break
                self.assertTrue(wait.active)
                for _ in range(11):
                    run.step({player: (0, 0, 0x40)})
                    self.assertFalse(self.thrust(hover))
                fuel = hover.vars[87]
                before = fuel.value()
                run.step({player: (0, 0, 2 if replace_binding else 0)})
                self.assertFalse(wait.active)
                if replace_binding:
                    self.assertTrue(self.thrust(hover))
                    self.assertLess(fuel.value(), before)
                    continue
                self.assertFalse(self.thrust(hover))
                self.assertGreater(fuel.value(), before)
                for _ in range(12):
                    run.step({player: (0, 0, 0x40)})
                    self.assertTrue(wait.active)
                    self.assertFalse(self.thrust(hover))
                run.step({player: (0, 0, 0x40)})
                self.assertFalse(wait.active)
                self.assertTrue(self.thrust(hover))

    def test_empty_secondary_release_prevents_restart_until_fresh_press(self):
        run, player, hover = self.exhaust_hover(2)
        fuel = hover.vars[87]
        for _ in range(40):
            run.step({player: (0, 0, 2)})
            if fuel.value() >= 8:
                break
        self.assertGreaterEqual(fuel.value(), 8)
        self.assertLess(fuel.value(), 10)
        self.assertFalse(self.thrust(hover))
        for _ in range(5):
            run.step({player: (0, 0, 0)})
            self.assertFalse(self.thrust(hover))
        before = fuel.value()
        self.assertGreaterEqual(before, 10)
        run.step({player: (0, 0, 2)})
        self.assertTrue(self.thrust(hover))
        self.assertLess(fuel.value(), before)

    def test_fresh_secondary_press_below_restart_gate_waits_for_recharge(self):
        run, player, hover = self.exhaust_hover(2)
        fuel = hover.vars[87]
        for _ in range(35):
            run.step({player: (0, 0, 0)})
            if fuel.value() >= 5:
                break
        self.assertGreaterEqual(fuel.value(), 5)
        self.assertLess(fuel.value(), 9)
        run.step({player: (0, 0, 2)})
        self.assertLess(fuel.value(), 10)
        self.assertFalse(self.thrust(hover))
        for _ in range(12):
            run.step({player: (0, 0, 2)})
            if self.thrust(hover):
                break
            self.assertLess(fuel.value(), 10)
        self.assertTrue(self.thrust(hover))
        self.assertGreaterEqual(fuel.value(), 10)


if __name__ == "__main__":
    unittest.main()
