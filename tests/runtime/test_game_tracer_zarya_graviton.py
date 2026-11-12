"""Actual Zarya ultimate projectile and field against Tracer."""

import unittest
from dataclasses import replace

import test_game_tracer_pulse_deflect as support_tests
import test_game_tracer_zarya_secondary as secondary_tests
from test_game_combat import aim

from ow174.game import tracer
from ow174.game.commands import FIRE, Command
from ow174.game.content import heroes
from ow174.game.script.driver import ULT_CHARGE, ULT_COST


class TracerZaryaGravitonTests(unittest.TestCase):
    def setUp(self):
        self.case = secondary_tests.TracerZaryaSecondaryTests()
        self.case.setUp()
        self.fight = self.case.fight
        self.run = self.case.run
        self.tracer = self.case.tracer
        self.caster = self.case.zarya
        self.script = self.caster.body_script
        self.health = self.case.health
        self.manager = self.fight.gravitons
        self.cost = self.script.component.entity_var(ULT_COST).value()

    def charge(self):
        return self.script.component.entity_var(ULT_CHARGE).value()

    def launch(self):
        self.script.set_ult_charge(self.cost)
        self.case.step(fire=32)
        self.assertEqual(self.charge(), 0)
        for _ in range(35):
            if self.fight.rockets:
                return self.fight.rockets[0]
            self.case.step()
        self.fail("Graviton projectile did not launch")

    def field(self):
        for _ in range(85):
            if self.manager.fields:
                return self.manager.fields[0]
            self.case.step()
        self.fail("Graviton did not deploy")

    def test_actual_cost_field_damage_pull_and_release(self):
        self.assertEqual(self.cost, 2100)
        self.case.step(fire=32)
        for _ in range(16):
            self.case.step()
        self.assertFalse(self.fight.rockets)
        rocket = self.launch()
        self.assertAlmostEqual(rocket.gravity, 9.8, places=5)
        self.assertEqual(rocket.graviton_body, self.caster.body)
        before = self.tracer.mover.position[2]
        field = self.field()
        self.assertEqual(field.end_tick - self.fight.tick, 250)
        self.assertEqual((field.radius, field.damage, field.speed), (6, 5, 3))
        self.assertEqual(self.health.total, 145)
        for _ in range(25):
            self.run.frame += 1
            frame = self.run.frame
            self.run.match.update(frame * 0.016, frame)
            self.caster.take_commands([Command(frame)])
        self.assertGreater(self.tracer.mover.position[2], before + 0.4)
        self.assertAlmostEqual(self.health.total, 143)
        self.assertIn(self.tracer.body, self.manager.player_pulls)
        self.assertEqual(self.charge(), 0)
        tick = self.fight.tick
        position = tuple(self.tracer.mover.position)
        self.fight.gravitons.update(tick)
        self.assertAlmostEqual(self.health.total, 143)
        self.assertEqual(tuple(self.tracer.mover.position), position)
        self.assertIn(self.tracer.body, self.manager.player_pulls)
        for _ in range(250):
            self.case.step()
            if not self.manager.fields:
                break
        self.assertFalse(self.manager.fields)
        self.assertNotIn(self.tracer.body, self.manager.player_pulls)
        self.assertFalse(self.tracer.mover.driven)
        self.assertAlmostEqual(self.health.total, 125)
        for _ in range(10):
            self.case.step()
        self.assertAlmostEqual(self.health.total, 125)
        self.assertGreater(self.charge(), 0)

    def test_blink_before_impact_moves_outside_field(self):
        self.case.wall(behind=True)
        self.launch()
        self.case.step(buttons=8, yaw=16384)
        self.field()
        for _ in range(70):
            self.case.step()
        self.assertEqual(self.health.total, 150)
        self.assertNotIn(self.tracer.body, self.manager.player_pulls)

    def test_blink_crosses_active_graviton_edge_but_landing_inside_is_pulled(self):
        for lands_inside in (False, True):
            with self.subTest(lands_inside=lands_inside):
                self.setUp()
                self.case.wall(behind=True)
                self.launch()
                mover = self.tracer.mover
                floor = mover.position[1]
                self.case.step(buttons=8, yaw=16384)
                field = self.field()
                self.assertEqual(self.health.total, 150)
                fx, _, fz = field.position
                dx, dz = (-7.5, 2) if lands_inside else (-3.75, 6)
                mover.position[:] = (fx + dx, floor, fz + dz)
                mover.body = None
                self.case.step()

                def target():
                    return next(t for t in self.fight._targets() if t.owner is self.tracer)

                initial = target()
                middle = (initial.feet[0] + 3.75, floor, initial.feet[2])
                self.assertFalse(self.manager.includes(field, initial))
                self.assertTrue(self.manager.includes(field, replace(initial, feet=middle)))
                script = self.tracer.body_script
                blink = next(i for i in script.component.instances.values() if i.graph.index == 0x24)
                self.assertEqual(blink.vars[11].value(), 2)
                self.case.step(buttons=8, yaw=16384)
                self.assertEqual(blink.vars[11].value(), 1)
                self.assertAlmostEqual(mover.position[0] - initial.feet[0], 7.5, places=2)
                self.assertEqual(self.manager.includes(field, target()), lands_inside)
                self.assertEqual(self.health.total, 150)
                self.assertNotIn(self.tracer.body, self.manager.player_pulls)

                self.case.step()
                if lands_inside:
                    self.assertIn(self.tracer.body, self.manager.player_pulls)
                    self.assertAlmostEqual(self.health.total, 150 - field.damage * 0.016)
                else:
                    for _ in range(30):
                        self.case.step()
                    self.assertNotIn(self.tracer.body, self.manager.player_pulls)
                    self.assertEqual(self.health.total, 150)

    def test_active_pull_rejects_blink_and_recall_then_blink_recovers(self):
        self.launch()
        field = self.field()
        self.case.step()
        self.assertIn(self.tracer.body, self.manager.player_pulls)
        script = self.tracer.body_script
        blink = next(i for i in script.component.instances.values() if i.graph.index == 0x24)
        recall = next(i for i in script.component.instances.values() if i.graph.index == 0x1A)
        weapon = next(i for i in script.component.instances.values() if i.graph.index == 0x16)
        x = self.tracer.mover.position[0]
        for button in (8, 2, 16):
            self.case.step(buttons=button, yaw=16384)
            self.assertIn(self.tracer.body, self.manager.player_pulls)
            self.assertAlmostEqual(self.tracer.mover.position[0], x, places=2)
            self.assertEqual(blink.vars[11].value(), 3)
            self.assertFalse(tracer.recalling(self.tracer))
            self.assertEqual(recall.vars[61].value(), 0)
            self.case.step()

        ammo = weapon.vars[53].value()
        self.case.step(buttons=1)
        self.assertLess(weapon.vars[53].value(), ammo)
        while self.fight.tick <= field.end_tick:
            self.case.step(buttons=8, yaw=16384)
        self.assertNotIn(self.tracer.body, self.manager.player_pulls)
        self.assertFalse(self.tracer.mover.driven)
        self.assertEqual(blink.vars[11].value(), 3)

        self.case.step()
        x = self.tracer.mover.position[0]
        self.case.step(buttons=8, yaw=16384)
        self.assertAlmostEqual(self.tracer.mover.position[0] - x, 7.5, places=2)
        self.assertEqual(blink.vars[11].value(), 2)
        self.case.step(buttons=16)
        self.assertTrue(tracer.recalling(self.tracer))

    def test_overlapping_actual_fields_keep_tracer_locked_until_last_expiry(self):
        second, _ = support_tests.PulseDeflectTests().support(
            self.run, self.tracer, hero=self.caster.hero.guid,
        )
        self.run.match.game_map = replace(self.run.match.game_map, free_for_all=False)
        self.tracer.team = 0
        self.caster.team = second.team = 1
        self.case.case.place(second, 1, 6)
        for _ in range(30):
            self.case.step()
        self.launch()
        first = self.field()
        self.case.step()
        self.assertIn(self.tracer.body, self.manager.player_pulls)

        other_script = second.body_script
        other_script.set_ult_charge(other_script.component.entity_var(ULT_COST).value())
        x, y, z = self.tracer.mover.position
        facing = aim(self.fight._eye(second), (x, y + 1, z))
        self.run.step({second: (*facing, 32)})
        self.assertEqual(other_script.component.entity_var(ULT_CHARGE).value(), 0)
        for _ in range(100):
            if len(self.manager.fields) == 2:
                break
            self.run.step({second: (*facing, 0)})
        _, last = self.manager.fields
        self.assertIs(first.caster, self.caster)
        self.assertIs(last.caster, second)
        self.assertLess(first.end_tick, last.end_tick)
        before = self.health.total
        self.case.step()
        self.assertAlmostEqual(self.health.total, before - (first.damage + last.damage) * 0.016)

        while self.fight.tick < first.end_tick:
            self.case.step()
        self.assertEqual(self.manager.fields, [last])
        self.assertIn(self.tracer.body, self.manager.player_pulls)
        self.assertTrue(self.tracer.mover.driven)
        script = self.tracer.body_script
        blink = next(i for i in script.component.instances.values() if i.graph.index == 0x24)
        before = self.health.total
        self.case.step(buttons=8 | 16, yaw=16384)
        self.assertAlmostEqual(self.health.total, before - last.damage * 0.016)
        self.assertEqual(blink.vars[11].value(), 3)
        self.assertFalse(tracer.recalling(self.tracer))

        while self.fight.tick <= last.end_tick:
            self.case.step(buttons=8 | 16, yaw=16384)
        self.assertFalse(self.manager.fields)
        self.assertNotIn(self.tracer.body, self.manager.player_pulls)
        self.assertFalse(self.tracer.mover.driven)
        self.assertEqual(blink.vars[11].value(), 3)
        self.assertFalse(tracer.recalling(self.tracer))
        self.case.step()
        x = self.tracer.mover.position[0]
        self.case.step(buttons=8, yaw=16384)
        self.assertAlmostEqual(self.tracer.mover.position[0] - x, 7.5, places=2)
        self.assertEqual(blink.vars[11].value(), 2)
        self.case.step(buttons=16)
        self.assertTrue(tracer.recalling(self.tracer))

    def test_actual_pulse_can_stick_and_detonate_during_active_pull(self):
        self.launch()
        field = self.field()
        self.case.step()
        self.assertIn(self.tracer.body, self.manager.player_pulls)
        script = self.tracer.body_script
        script.ult_seconds = 0
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        zarya_health = self.case.case.health(self.caster)
        before = zarya_health.total
        bombs = self.fight.pulse_bombs
        self.case.step(buttons=32)
        self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)
        self.assertFalse(bombs.bombs)
        for _ in range(16):
            if bombs.bombs:
                break
            self.case.step()
        bomb, = bombs.bombs
        self.assertIs(bomb.shooter, self.tracer)
        self.assertIn(self.tracer.body, self.manager.player_pulls)
        for _ in range(55):
            if bomb.stuck:
                break
            self.case.step()
        self.assertEqual(bomb.attached, self.caster.body)
        self.assertEqual(zarya_health.total, before - 5)
        self.assertIn(self.tracer.body, self.manager.player_pulls)

        for _ in range(75):
            if bomb not in bombs.bombs:
                break
            self.case.step()
        self.assertNotIn(bomb, bombs.bombs)
        self.assertEqual(zarya_health.total, before - 355)
        self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)
        self.assertLess(self.fight.tick, field.end_tick)
        self.assertIn(self.tracer.body, self.manager.player_pulls)

        x, y, z = self.caster.mover.position
        for _ in range(80):
            if zarya_health.dead:
                break
            facing = aim(self.fight._eye(self.tracer), (x, y + 1, z))
            self.run.step({self.tracer: (*facing, FIRE)})
        self.assertTrue(zarya_health.dead)
        self.assertIn(field, self.manager.fields)
        self.assertIn(self.tracer.body, self.manager.player_pulls)
        at_death = self.health.total
        self.case.step()
        self.assertAlmostEqual(self.health.total, at_death - field.damage * 0.016)
        while self.fight.tick < field.end_tick:
            self.case.step()
        self.assertNotIn(field, self.manager.fields)
        self.assertNotIn(self.tracer.body, self.manager.player_pulls)

    def test_pull_discards_recall_buffered_before_capture(self):
        for held in (False, True):
            with self.subTest(held=held):
                self.setUp()
                self.check_recall_buffer_on_capture(held)

    def check_recall_buffer_on_capture(self, held):
        self.case.step()
        self.case.step(buttons=16)
        self.assertTrue(tracer.recalling(self.tracer))
        script = self.tracer.body_script
        recall = next(i for i in script.component.instances.values() if i.graph.index == 0x1A)
        warmup = recall.state(recall.graph.node(4).state)
        for _ in range(100):
            if not tracer.recalling(self.tracer):
                break
            self.case.step()
        self.assertGreater(recall.vars[61].value(), 11)
        for _ in range(800):
            if recall.vars[61].value() <= 1.5:
                break
            self.case.step()
        self.assertLessEqual(recall.vars[61].value(), 1.5)

        world = self.run.match.collision_world
        self.case.wall(behind=True)
        self.launch()
        mover = self.tracer.mover
        before = tuple(mover.position)
        self.case.step(buttons=8, yaw=16384)
        field = self.field()
        self.run.match.collision_world = world
        self.assertNotIn(self.tracer.body, self.manager.player_pulls)
        for _ in range(100):
            if recall.vars[61].value() <= 0.1:
                break
            self.case.step()
        self.assertLessEqual(recall.vars[61].value(), 0.1)
        self.assertGreater(recall.vars[61].value(), 0)
        count = warmup.counter
        self.case.step(buttons=16)
        self.assertEqual(warmup.counter, count)

        mover.position[:] = before
        mover.body = None
        buttons = 16 if held else 0
        self.case.step(buttons=buttons)
        self.assertIn(self.tracer.body, self.manager.player_pulls)
        for _ in range(12):
            self.case.step(buttons=buttons)
            self.assertIn(self.tracer.body, self.manager.player_pulls)
            self.assertFalse(tracer.recalling(self.tracer))
            self.assertEqual(warmup.counter, count)
        self.assertEqual(recall.vars[61].value(), 0)
        while self.fight.tick <= field.end_tick:
            self.case.step(buttons=buttons)
        self.assertFalse(tracer.recalling(self.tracer))
        self.assertEqual(warmup.counter, count)
        self.case.step()
        self.case.step(buttons=16)
        self.assertTrue(tracer.recalling(self.tracer))
        self.assertEqual(warmup.counter, count + 1)

    def test_recall_protects_during_deploy_then_field_can_reacquire(self):
        self.case.wall(behind=True)
        self.launch()
        self.case.step(buttons=16)
        self.assertTrue(tracer.recalling(self.tracer))
        self.field()
        self.assertTrue(tracer.recalling(self.tracer))
        self.assertEqual(self.health.total, 150)
        self.assertNotIn(self.tracer.body, self.manager.player_pulls)
        for _ in range(100):
            self.case.step()
            if self.health.total < 150:
                break
        self.assertFalse(tracer.recalling(self.tracer))
        self.assertLess(self.health.total, 150)
        self.assertIn(self.tracer.body, self.manager.player_pulls)

    def test_ally_and_wall_reject_field_damage_and_pull(self):
        for reason in ("ally", "wall"):
            with self.subTest(reason=reason):
                self.setUp()
                if reason == "ally":
                    self.run.match.game_map = replace(self.run.match.game_map, free_for_all=False)
                    self.tracer.team = self.caster.team
                    self.case.wall(behind=True)
                else:
                    self.case.wall()
                self.launch()
                self.field()
                for _ in range(65):
                    self.case.step()
                self.assertEqual(self.health.total, 150)
                self.assertNotIn(self.tracer.body, self.manager.player_pulls)

    def test_caster_replacement_ends_field_and_releases_tracer(self):
        self.launch()
        self.field()
        self.case.step()
        self.assertIn(self.tracer.body, self.manager.player_pulls)
        health = self.health.total
        self.assertTrue(self.run.match.switch_hero(self.caster, heroes()[0x02E000000000006E]))
        self.case.step()
        self.assertFalse(self.manager.fields)
        self.assertNotIn(self.tracer.body, self.manager.player_pulls)
        self.assertEqual(self.health.total, health)
