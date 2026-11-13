"""Executed sleep-dart gameplay reconstruction, not native wake-animation acceptance."""

import unittest

import test_game_ana as ana_fixture
from test_game_combat import aim

from ow174.game import combat
from ow174.game.collision import World
from ow174.game.commands import Command


class AnaSleepTests(unittest.TestCase):
    def fight(self, **kwargs):
        return ana_fixture.AnaRifleTests().setup_fight(enemy=True, **kwargs)

    def cast(self, run, ana, entry, *, miss=False):
        direction = ((16384, 0) if miss else aim(run.match.combat._eye(ana),
                     (entry.feet[0], entry.feet[1] + 1, entry.feet[2])))
        run.step({ana: (*direction, 8)})
        for _ in range(60):
            run.step({ana: (*direction, 0)})

    def input_frame(self, run, target, *, forward=0, buttons=0):
        run.frame += 1
        run.match.update(run.frame * 0.016, run.frame)
        for player in run.players:
            command = (Command(run.frame, forward=forward, buttons=buttons)
                       if player is target else Command(run.frame))
            player.take_commands([command])

    def test_enemy_hit_sleeps_and_authoritative_expiry_releases_control(self):
        run, ana, target, entry = self.fight()
        fight = run.match.combat
        self.cast(run, ana, entry)
        self.assertEqual(fight.vitals_of(entry).total, 195)
        self.assertTrue(fight.is_asleep(target))
        before = tuple(target.mover.position)
        for _ in range(80):
            self.input_frame(run, target, forward=127, buttons=1 | 8 | 16 | 32 | 0x40 | 0x800)
        self.assertEqual(tuple(target.mover.position), before)
        self.assertEqual(target.body_script.buttons, 0)
        self.assertEqual(target.body_script.frame, run.frame)
        self.assertIsNone(target.body_script.component.fault)
        for _ in range(270):
            run.step({})
        self.assertFalse(fight.is_asleep(target))
        for _ in range(20):
            self.input_frame(run, target, forward=127)
        self.assertNotEqual(tuple(target.mover.position), before)

    def test_later_rifle_damage_wakes_but_healing_and_zero_damage_do_not(self):
        run, ana, target, entry = self.fight()
        fight = run.match.combat
        self.cast(run, ana, entry)
        fight.heal_area(target, entry.feet, 1, 5)
        self.assertEqual(fight.vitals_of(entry).total, 200)
        self.assertTrue(fight.is_asleep(target))
        fight._damage(ana, entry, 0, False, None, run.frame)
        self.assertTrue(fight.is_asleep(target))
        for _ in range(30):
            run.step({})
        ana_fixture.AnaRifleTests().shoot(run, ana, entry, False)
        self.assertEqual(fight.vitals_of(entry).total, 130)
        self.assertFalse(fight.is_asleep(target))

    def test_rifle_wake_keeps_entry_floor_or_half_second_and_cannot_extend(self):
        for late in (False, True):
            with self.subTest(late=late):
                run, ana, target, entry = self.fight()
                fight = run.match.combat
                vitals = fight.vitals_of(entry)
                direction = aim(fight._eye(ana), (entry.feet[0], entry.feet[1] + 1, entry.feet[2]))
                run.step({ana: (*direction, 8)})
                for _ in range(60):
                    if fight.is_asleep(target):
                        break
                    run.step({ana: (*direction, 0)})
                floor = vitals.sleep_wake_floor
                self.assertEqual(floor - fight.tick, combat.SLEEP_WAKE_FLOOR_TICKS)
                self.assertEqual(vitals.sleep_until - fight.tick, combat.SLEEP_TICKS)
                if late:
                    while fight.tick < floor:
                        run.step({})
                for frame in range(60):
                    if vitals.total < 195:
                        break
                    run.step({ana: (*direction, 1 if frame % 2 == 0 else 0)})
                self.assertLess(vitals.total, 195)
                deadline = max(floor, fight.tick + combat.SLEEP_WAKE_RECOVERY_TICKS)
                self.assertAlmostEqual(vitals.sleep_until, deadline)
                self.assertEqual(deadline == floor, not late)
                start = tuple(target.mover.position)
                while fight.tick + 1 < deadline:
                    self.input_frame(run, target, forward=127, buttons=1)
                    self.assertEqual(vitals.sleep_until, deadline)
                    self.assertEqual(target.body_script.buttons, 0)
                self.assertEqual(tuple(target.mover.position), start)
                while fight.is_asleep(target):
                    self.input_frame(run, target, forward=127)
                self.assertEqual(vitals.sleep_wake_floor, 0)
                for _ in range(8):
                    self.input_frame(run, target, forward=127)
                self.assertNotEqual(tuple(target.mover.position), start)

    def test_sleeping_shooter_cannot_damage_or_bypass_with_recovered_frames(self):
        run, ana, target, entry = self.fight()
        fight = run.match.combat
        own = next(t for t in fight._targets() if t.owner is ana)
        own_vitals = fight.vitals_of(own)
        self.cast(run, ana, entry)
        before = tuple(target.mover.position)
        target.take_commands([Command(run.frame + 100, yaw=-32768, buttons=1, forward=127)])
        self.assertEqual(own_vitals.total, 200)
        self.assertEqual(tuple(target.mover.position), before)
        self.assertTrue(fight.is_asleep(target))
        self.assertEqual(target.body_script.buttons, 0)

    def test_actual_grenade_wakes_after_recovery_and_keeps_antiheal(self):
        run, ana, target, entry = self.fight()
        fight = run.match.combat
        vitals = fight.vitals_of(entry)
        self.cast(run, ana, entry)
        while fight.tick < vitals.sleep_wake_floor:
            run.step({})
        point = (entry.feet[0], entry.feet[1] + 1, entry.feet[2])
        direction = ana_fixture.grenade_aim(run, ana, point)
        run.step({ana: (*direction, 16)})
        for _ in range(65):
            if fight.healing.factor(entry) == 0:
                break
            run.step({})
        self.assertEqual(vitals.total, 195 - fight.healing.spec.damage)
        self.assertEqual(vitals.sleep_until, fight.tick + combat.SLEEP_WAKE_RECOVERY_TICKS)
        for _ in range(30):
            self.input_frame(run, target, forward=127)
            self.assertTrue(fight.is_asleep(target))
        for _ in range(2):
            self.input_frame(run, target, forward=127)
        self.assertFalse(fight.is_asleep(target))
        self.assertEqual(fight._heal(target, entry, 55), 0)
        self.assertEqual(vitals.total, 195 - fight.healing.spec.damage)

    def test_ally_and_miss_receive_no_sleep_or_damage(self):
        for ally in (False, True):
            with self.subTest(ally=ally):
                run, ana, target, entry = ana_fixture.AnaRifleTests().setup_fight(enemy=not ally)
                self.cast(run, ana, entry, miss=not ally)
                self.assertEqual(run.match.combat.vitals_of(entry).total, 200)
                self.assertFalse(run.match.combat.is_asleep(target))

    def test_static_wall_blocks_sleep_dart(self):
        run, ana, target, entry = self.fight()
        x, y, z = ana.mover.position
        wall_z = z + 2.5
        floor = [(x - 50, y, z - 50, x - 50, y, z + 50, x + 50, y, z + 50),
                 (x - 50, y, z - 50, x + 50, y, z + 50, x + 50, y, z - 50)]
        wall = [(x - 3, y, wall_z, x + 3, y, wall_z, x + 3, y + 4, wall_z),
                (x - 3, y, wall_z, x + 3, y + 4, wall_z, x - 3, y + 4, wall_z)]
        run.match.collision_world = World([*floor, *wall])
        self.cast(run, ana, entry)
        self.assertEqual(run.match.combat.vitals_of(entry).total, 200)
        self.assertFalse(run.match.combat.is_asleep(target))

    def test_lethal_dart_does_not_leave_sleep_on_the_corpse(self):
        run, ana, target, entry = self.fight()
        fight = run.match.combat
        vitals = fight.vitals_of(entry)
        vitals.damage(196)
        self.cast(run, ana, entry)
        self.assertTrue(vitals.dead)
        self.assertEqual(vitals.total, 0)
        self.assertEqual(vitals.sleep_until, 0)
        self.assertEqual(vitals.sleep_wake_floor, 0)
        self.assertFalse(fight.is_asleep(target))

    def test_replacement_body_does_not_inherit_sleep(self):
        run, ana, target, entry = self.fight()
        fight = run.match.combat
        self.cast(run, ana, entry)
        retired = target.body
        self.assertTrue(fight.is_asleep(target))
        self.assertTrue(run.match.switch_hero(target, target.hero))
        self.assertNotEqual(target.body, retired)
        self.assertNotIn(retired, fight.vitals)
        self.assertFalse(fight.is_asleep(target))


if __name__ == "__main__":
    unittest.main()
