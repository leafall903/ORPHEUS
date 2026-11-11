"""Actual 157C beam and charge against Tracer."""
import unittest
from dataclasses import replace

import test_game_tracer as tracer_tests
import test_game_tracer_winston_barrier as barrier_tests
from test_game_combat import aim
from test_game_mover import box

from ow174.game import combat, tracer
from ow174.game.collision import World
from ow174.game.commands import FIRE, Command
from ow174.game.content import heroes

SYMMETRA = 0x02E0000000000016


class TracerSymmetraBeamTests(unittest.TestCase):
    def setUp(self):
        self.run, self.target, self.caster, _ = tracer_tests.TracerPistolTests().fight()
        self.match = self.run.match
        self.match.switch_hero(self.caster, heroes()[SYMMETRA])
        self.fight = self.match.combat
        self.shown = next(target for target in self.fight._targets() if target.owner is self.target)
        self.vitals = self.fight.vitals_of(self.shown)
        self.weapon = next(i for i in self.caster.body_script.component.instances.values()
                           if i.graph.index == 0x157C)
        self.place(5)

    def place(self, distance):
        x, y, z = self.shown.feet
        self.caster.mover.position = [x, y, z + distance]
        self.caster.mover.body = None
        self.direction = aim(self.fight._eye(self.caster), (x, y + 1, z))

    def fire(self, frames=1, buttons=FIRE, direction=None):
        for _ in range(frames):
            self.run.step({self.caster: (*(self.direction if direction is None else direction), buttons)})

    def test_actual_release_reuse_and_duplicate_command(self):
        self.fire(10)
        self.assertEqual(self.weapon.vars[16627].value().id, self.target.body)
        self.assertAlmostEqual(self.vitals.total, 150 - 9 * .96)
        self.fight.command(self.caster, Command(self.run.frame))
        self.assertAlmostEqual(self.vitals.total, 150 - 9 * .96)
        self.fire(20, buttons=0)
        self.assertAlmostEqual(self.vitals.total, 150 - 9 * .96)
        self.fire(10)
        self.assertLess(self.vitals.total, 150 - 9 * .96)

    def test_actual_contact_advances_all_three_damage_rates(self):
        rates = set()
        for _ in range(180):
            self.fire()
            loss = 150 - self.vitals.total
            if loss:
                rate = self.weapon.vars[499].value()
                self.assertAlmostEqual(loss, rate * .016)
                rates.add(rate)
                self.vitals.heal(loss)  # keep the authored 150-health body in the test
        self.assertEqual(rates, {60, 120, 180})

    def test_miss_range_team_and_wall_exclude_tracer(self):
        for reason in ("miss", "range", "team", "wall"):
            with self.subTest(reason=reason):
                self.setUp()
                direction = (0, 0) if reason == "miss" else None
                if reason == "range":
                    self.place(30)
                elif reason == "team":
                    self.match.game_map = replace(self.match.game_map, free_for_all=False)
                    self.caster.team = self.target.team
                elif reason == "wall":
                    x, y, z = self.shown.feet
                    self.match.collision_world = World(box(x - 5, x + 5, y, y + 5, z + 2, z + 2.1))
                self.fire(10, direction=direction)
                self.assertEqual(self.vitals.total, 150)
                self.assertFalse(self.caster.client.messages(combat.HIT))

    def test_actual_recall_then_blink_interrupts_target_contact(self):
        self.run.step({self.target: (0, 0, 16)})
        self.assertTrue(tracer.recalling(self.target))
        self.fire(30)
        self.assertEqual(self.vitals.total, 150)
        for _ in range(80):
            if not tracer.recalling(self.target):
                break
            self.fire(buttons=0)
        self.assertFalse(tracer.recalling(self.target))
        self.fire(10)
        self.assertLess(self.vitals.total, 150)
        before = self.vitals.total
        self.run.step({self.target: (16384, 0, 8), self.caster: (*self.direction, FIRE)})
        # The beam reads the retained pose captured at the start of this tick.
        self.assertAlmostEqual(self.vitals.total, before - .96)
        after = self.vitals.total
        self.fire(10)
        self.assertEqual(self.vitals.total, after)

    def test_enemy_bubble_stops_contact_before_tracer(self):
        fixture = barrier_tests.TracerWinstonBarrierTests()
        run, target, owner, shooter = fixture.arena()
        run.match.switch_hero(shooter, heroes()[SYMMETRA])
        shooter.team, target.team = 0, owner.team
        fixture.move(target, (0, 1, 2))
        fixture.move(shooter, (0, 1, 7))
        bubble = fixture.cast(run, owner)
        for _ in range(10):
            run.step({shooter: (-32768, 0, FIRE)})
        self.assertLess(bubble.health, 700)
        self.assertEqual(fixture.health(run, target).total, 150)
        self.assertFalse(shooter.client.messages(combat.HIT))
