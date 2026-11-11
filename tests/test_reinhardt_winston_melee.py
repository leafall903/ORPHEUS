"""Actual Winston quick melee against Reinhardt and authored Ice Wall."""

import unittest

import test_game_reinhardt as fixtures
import test_reinhardt_winston_tesla as tesla_fixture
from test_game_combat import aim

from ow174.game import defenses
from ow174.game.commands import Command
from ow174.game.content import heroes

MELEE = 0x800


class ReinhardtWinstonMeleeTests(unittest.TestCase):
    step = tesla_fixture.ReinhardtTeslaTests.step
    weapon = tesla_fixture.ReinhardtTeslaTests.weapon
    place = staticmethod(fixtures.HammerTests.place)
    health = fixtures.HammerTests.health
    advance = fixtures.HammerTests.advance
    shield = fixtures.BarrierTests.shield

    def setUp(self):
        tesla_fixture.ReinhardtTeslaTests.setUp(self)
        self.place(self.enemy, (0, 1, 2))
        self.step(2, fire=0)

    def melee(self):
        return next(i for i in self.enemy.body_script.component.instances.values() if i.graph.index == 0x43)

    def test_hold_one_hit_then_fresh_press_after_authored_cooldown(self):
        self.step(fire=MELEE)
        ability = self.melee().states[6]
        self.assertEqual(self.health(self.rein), 475)
        self.assertAlmostEqual(ability.remaining(), .985, places=5)
        self.step(80, fire=MELEE)
        self.assertEqual(self.health(self.rein), 475)
        self.assertEqual(ability.remaining(), 0)
        self.step(fire=0)
        self.step(fire=MELEE)
        self.assertEqual(self.health(self.rein), 450)

    def test_buffered_tap_executes_once_and_stale_commands_cannot_repeat(self):
        self.step(fire=MELEE)
        first = self.run.frame
        for offset in range(1, 62):
            self.step(fire=MELEE if offset == 55 else 0)
        self.assertEqual(self.health(self.rein), 475)
        self.step(fire=0)
        self.assertEqual(self.health(self.rein), 450)
        self.assertEqual(self.melee().states[4].counter, 2)
        for frame in (first, self.run.frame - 1, self.run.frame, self.run.frame + 1):
            self.enemy.take_commands([Command(frame, buttons=MELEE, yaw=-32768)])
        self.assertEqual(self.health(self.rein), 450)

    def test_quick_melee_interrupts_tesla_and_held_primary_recovers(self):
        self.step()
        before = self.health(self.rein)
        self.step(fire=1 | MELEE)
        self.assertAlmostEqual(self.health(self.rein), before - 25)
        self.step(32)
        self.assertAlmostEqual(self.health(self.rein), before - 25)
        self.step()
        self.assertAlmostEqual(self.health(self.rein), before - 25 - .96 * .8)

    def test_quick_melee_contacts_body_and_shield_independently(self):
        self.place(self.enemy, (0, 1, 2.4))
        self.step(15, fire=0, rein=2)
        self.step(fire=MELEE, rein=2)
        self.assertEqual(self.health(self.rein), 475)
        self.assertEqual(self.shield().current, 1570)
        self.assertFalse(self.rein.mover.impulse_active)

    def test_authored_ice_wall_break_does_not_allow_same_melee_to_hit_body(self):
        self.place(self.enemy, (0, 1, 2.9))
        self.assertTrue(self.match.switch_hero(self.other, heroes()[defenses.MEI]))
        self.other.team = self.rein.team
        self.place(self.other, (0, 1, 7))
        angles = aim(self.fight._eye(self.other), (0, 1, 1.4))
        self.run.step({self.other: (*angles, 16)})
        self.run.step({self.other: (*angles, 1)})
        self.assertEqual(len(self.fight.walls.pieces), 5)
        centre = list(self.fight.walls.pieces.values())[2]
        centre.health = 30
        self.step(fire=MELEE)
        self.assertNotIn(centre.entity, self.fight.walls.pieces)
        self.assertEqual(self.health(self.rein), 500)
        self.step(64, fire=0)
        self.step(fire=MELEE)
        self.assertEqual(self.health(self.rein), 475)

    def test_actual_earthshatter_denies_quick_melee_until_control_expires(self):
        self.rein.body_script.set_ult_charge(1540)
        self.step(rein=32, fire=0)
        for _ in range(65):
            if self.fight.controlled(self.enemy.body):
                break
            self.step(fire=0)
        self.assertTrue(self.fight.controlled(self.enemy.body))
        self.step(fire=MELEE)
        self.assertEqual(self.health(self.rein), 500)
        self.step(185, fire=0)
        self.assertFalse(self.fight.controlled(self.enemy.body))
        self.step(fire=MELEE)
        self.assertEqual(self.health(self.rein), 475)


    def test_actual_melee_rejects_world_cover_rear_range_and_team(self):
        for case in ("wall", "rear", "range", "team"):
            with self.subTest(case=case):
                if case != "wall":
                    self.setUp()
                if case == "wall":
                    fixtures.charge_wall(self, 1)
                if case == "range":
                    self.place(self.enemy, (0, 1, 4))
                if case == "team":
                    self.enemy.team = self.rein.team
                self.step(fire=MELEE, yaw=0 if case == "rear" else -32768)
                self.assertEqual(self.melee().states[4].counter, 1)
                self.assertEqual(self.health(self.rein), 500)

    def test_lethal_melee_updates_killer_and_lowers_live_barrier(self):
        self.place(self.enemy, (0, 1, 2.4))
        self.step(15, fire=0, rein=2)
        target = self.fight._target_entity(self.rein.body)
        vitals = self.fight.vitals_of(target)
        vitals.damage(480, bypass_armour=True)
        self.step(fire=MELEE, rein=2)
        self.assertTrue(self.fight.is_dead(self.rein))
        self.assertEqual(vitals.killer, self.enemy.body)
        self.assertEqual(self.shield().current, 1570)
        self.assertFalse(self.shield().active)
        self.step(65, fire=MELEE)
        self.assertEqual(self.health(self.rein), 0)
