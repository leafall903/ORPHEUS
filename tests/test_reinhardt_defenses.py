"""Actual opposing ability inputs through Reinhardt's attack entry points."""

import math
import os
import unittest
from dataclasses import replace
from unittest.mock import patch

import test_game_reinhardt
from test_game_combat import aim

from ow174.game import combat, defenses
from ow174.game.content import heroes


class ReinhardtDefensesTests(unittest.TestCase):
    health = test_game_reinhardt.HammerTests.health
    place = staticmethod(test_game_reinhardt.HammerTests.place)
    advance = test_game_reinhardt.HammerTests.advance

    def soundwave_arena(self, position=(0, 1, 4), *, wall=False):
        test_game_reinhardt.HammerTests.setUp(self)
        lucio = heroes()[0x02E0000000000079]
        self.assertTrue(self.match.switch_hero(self.enemy, lucio))
        self.match.game_map = replace(self.match.game_map, free_for_all=False)
        self.rein.team, self.enemy.team, self.other.team = 0, 1, 1
        self.place(self.enemy, (0, 1, 0))
        self.place(self.rein, position)
        self.place(self.other, (1, 1, 4))
        if wall:
            test_game_reinhardt.charge_wall(self, z=2)
        self.advance(12, buttons=0)
        return next(i for i in self.enemy.body_script.component.instances.values() if i.graph.index == 0x0776)

    def test_actual_soundwave_damages_and_displaces_reinhardt_once(self):
        instance = self.soundwave_arena()
        ally_start = tuple(self.other.mover.position)
        with patch.object(self.rein.mover, "add_impulse", wraps=self.rein.mover.add_impulse) as impulse:
            self.run.step({self.enemy: (0, 0, 2)})
            self.assertTrue(instance.states[11].active)
            self.advance(12, buttons=0)
        self.assertEqual(self.health(self.rein), 480)
        self.assertEqual(self.health(self.other), 200)
        self.assertEqual(tuple(self.other.mover.position), ally_start)
        impulse.assert_called_once()
        (x, y, z), _ = impulse.call_args.args
        self.assertAlmostEqual(x, 0)
        self.assertAlmostEqual(y, 17 / math.sqrt(5), places=4)
        self.assertAlmostEqual(z, 34 / math.sqrt(5), places=4)
        self.assertGreater(self.rein.mover.position[1], 1)
        self.assertGreater(self.rein.mover.position[2], 4)
        self.run.step({self.enemy: (0, 0, 2)})
        self.advance(12, buttons=0)
        self.assertEqual(self.health(self.rein), 480)
        self.assertEqual(instance.states[11].counter, 1)

        self.advance(250, buttons=0)
        self.place(self.rein, (0, 1, 4))
        self.advance(4, buttons=0)
        fight = self.match.combat
        vitals = fight.vitals_of(fight._target_entity(self.rein.body))
        vitals.damage(vitals.total - 1, bypass_armour=True)
        with patch.object(self.rein.mover, "add_impulse", wraps=self.rein.mover.add_impulse) as impulse:
            self.run.step({self.enemy: (0, 0, 2)})
        self.assertEqual(instance.states[11].counter, 2)
        self.assertTrue(vitals.dead)
        self.assertEqual(vitals.killer, self.enemy.body)
        impulse.assert_not_called()

    def test_actual_soundwave_misses_outside_cone_range_or_cover(self):
        cases = (((0, 1, 10), False), ((4, 1, 3), False), ((0, 1, -4), False), ((0, 1, 4), True))
        for position, wall in cases:
            with self.subTest(position=position, wall=wall):
                instance = self.soundwave_arena(position, wall=wall)
                self.run.step({self.enemy: (0, 0, 2)})
                self.assertTrue(instance.states[11].active)
                self.advance(8, buttons=0)
                self.assertEqual(self.health(self.rein), 500)
                self.assertEqual(tuple(self.rein.mover.position), position)

    def test_actual_soundwave_damages_pinned_reinhardt_without_taking_pose_ownership(self):
        instance = self.soundwave_arena()
        self.assertTrue(self.match.switch_hero(self.other, heroes()[combat.REINHARDT]))
        self.place(self.other, (0, 1, 11))
        fight = self.match.combat
        self.run.step({self.other: (-32768, 0, 8)})
        for _ in range(80):
            self.run.step({self.other: (-32768, 0, 0)})
            if fight.pinned(self.rein.body):
                break
        capture = fight.charge_contacts[self.other.body]
        self.assertEqual(capture.pinned, self.rein.body)
        self.assertEqual(self.health(self.rein), 500)
        with patch.object(self.rein.mover, "add_impulse", wraps=self.rein.mover.add_impulse) as impulse:
            self.run.step({self.enemy: (0, 0, 2), self.other: (-32768, 0, 0)})
        self.assertTrue(instance.states[11].active)
        self.assertEqual(self.health(self.rein), 480)
        self.assertIs(fight.charge_contacts[self.other.body], capture)
        self.assertEqual(capture.pinned, self.rein.body)
        self.assertTrue(fight.pinned(self.rein.body))
        impulse.assert_not_called()

    def test_actual_soundwave_lifts_charging_reinhardt_without_ending_the_drive(self):
        instance = self.soundwave_arena()
        self.place(self.enemy, (5, 1, 6))
        self.place(self.other, (20, 1, 0))
        self.advance(5, buttons=0)
        fight = self.match.combat
        self.advance(1, buttons=8)
        for _ in range(55):
            self.advance(1, buttons=0)
            if self.rein.body in fight.charge_drives:
                break
        drive = fight.charge_drives[self.rein.body]
        capture = fight.charge_contacts[self.rein.body]
        x, y, z = self.rein.mover.position
        angles = aim(fight._eye(self.enemy), (x, y + 1.2, z))
        self.run.step({self.enemy: (*angles, 2)})
        self.assertTrue(instance.states[11].active)
        self.assertEqual(self.health(self.rein), 480)
        self.assertLess(self.rein.mover.velocity[0], 0)
        self.assertGreater(self.rein.mover.velocity[1], 0)
        self.assertFalse(fight.controlled(self.rein.body))
        self.advance(8, buttons=0)
        self.assertEqual(fight.charge_drives[self.rein.body], drive)
        self.assertIs(fight.charge_contacts[self.rein.body], capture)
        self.assertAlmostEqual(self.rein.mover.position[0], x, places=5)
        self.assertGreater(self.rein.mover.position[1], y)
        self.assertGreater(self.rein.mover.position[2], z + 1)
        self.assertEqual(self.health(self.rein), 480)

    def test_actual_soundwave_during_airborne_shatter_preserves_landing_release(self):
        soundwave = self.soundwave_arena()
        self.place(self.rein, (0, 6, 4))
        self.place(self.enemy, (0, 6, 0))
        self.place(self.other, (0, 1, 20))
        self.advance(5, buttons=0)
        self.assertTrue(self.rein.mover.airborne)
        fight = self.match.combat
        script = self.rein.body_script
        meter = script.component.entity_var(342)
        script.set_ult_charge(script.component.entity_var(338).value())
        self.advance(1, buttons=32)
        self.advance(4, buttons=0)
        self.assertEqual(meter.value(), 0)
        self.assertNotIn(self.rein.body, fight.shatters)
        x, y, z = self.rein.mover.position
        angles = aim(fight._eye(self.enemy), (x, y + 1.2, z))
        self.run.step({self.enemy: (*angles, 2)})
        self.assertTrue(soundwave.states[11].active)
        self.assertEqual(self.health(self.rein), 480)
        self.assertFalse(fight.controlled(self.rein.body))
        self.assertGreater(self.rein.mover.velocity[1], 0)
        self.assertNotIn(self.rein.body, fight.shatters)

        wave = None
        for _ in range(140):
            self.advance(1, buttons=0)
            active = fight.shatters.get(self.rein.body)
            if active is not None:
                if wave is None:
                    self.assertAlmostEqual(active.origin[1], 1, places=3)
                    wave = active
                self.assertIs(active, wave)
        self.assertIsNotNone(wave)
        self.assertNotIn(self.rein.body, fight.shatters)
        self.assertEqual(self.health(self.other), 150)

    def defend(self, hero, z=3):
        self.assertTrue(self.match.switch_hero(self.enemy, heroes()[hero]))
        self.place(self.enemy, (0, 1, z))
        self.place(self.other, (20, 1, 0))
        self.advance(3, buttons=0)
        if hero == defenses.ZENYATTA:
            script = self.enemy.body_script
            self.run.step({self.enemy: (0, 0, 32)})
            self.assertFalse(defenses.control_immune(self.enemy))
            self.run.step({})
            script.set_ult_charge(script.component.entity_var(338).value())
        self.run.step({self.enemy: (0, 0, 32 if hero == defenses.ZENYATTA else 8)})
        self.assertTrue(defenses.control_immune(self.enemy))

    def attack(self, button):
        if button == 32:
            script = self.rein.body_script
            script.set_ult_charge(script.component.entity_var(338).value())
        self.advance(1, buttons=button)
        self.advance(68, buttons=0)

    def test_actual_wraith_and_fade_exclude_reinhardt_attacks_during_phase(self):
        for hero in (defenses.REAPER, defenses.MOIRA):
            for button in (1, 0x800, 8, 16, 32):
                with self.subTest(hero=hero, button=button):
                    test_game_reinhardt.HammerTests.setUp(self)
                    self.assertTrue(self.match.switch_hero(self.enemy, heroes()[hero]))
                    self.place(self.enemy, (0, 1, 3))
                    self.place(self.other, (0, 1, 6) if button == 16 else (20, 1, 0))
                    before = self.health(self.enemy)
                    if button == 32:
                        script = self.rein.body_script
                        script.set_ult_charge(script.component.entity_var(338).value())
                    self.advance(1, buttons=button)
                    self.advance(19, buttons=0)
                    self.run.step({self.enemy: (0, 0, 8)})
                    self.assertTrue(defenses.phased(self.enemy))
                    self.advance(34, buttons=0)
                    self.assertTrue(defenses.phased(self.enemy))
                    fight = self.match.combat
                    self.assertEqual(self.health(self.enemy), before)
                    self.assertFalse(fight.controlled(self.enemy.body))
                    if button == 8:
                        self.assertGreater(self.rein.mover.position[2], 3)
                        hits = fight.charge_contacts[self.rein.body].hit
                        self.assertNotIn(self.enemy.body, {entity for entity, _ in hits})
                    if button == 16:
                        self.assertEqual(self.health(self.other), 100)

    @unittest.skipUnless(test_game_reinhardt.BARRIER_DATA.is_file(), "prepared Barrier Field required")
    def test_late_wraith_and_fade_pass_incoming_helix_to_reinhardt(self):
        self.enterContext(patch.dict(os.environ, {
            "ORPHEUS_REINHARDT_COLLISION": str(test_game_reinhardt.BARRIER_DATA),
        }))
        for hero in (defenses.REAPER, defenses.MOIRA):
            for phase, shield_hp in ((False, 0), (True, 0), (True, 1600), (True, 100)):
                with self.subTest(hero=hero, phase=phase, shield_hp=shield_hp):
                    test_game_reinhardt.HammerTests.setUp(self)
                    self.assertTrue(self.match.switch_hero(self.enemy, heroes()[hero]))
                    self.match.game_map = replace(self.match.game_map, free_for_all=False)
                    self.rein.team = self.enemy.team = 0
                    self.other.team = 1
                    self.place(self.enemy, (0, 1, 6))
                    self.place(self.other, (0, 1, 16))
                    fight = self.match.combat
                    held = 2 if shield_hp else 0
                    self.advance(20, buttons=held)
                    if shield_hp:
                        barrier = fight.barriers[self.rein.body]
                        self.assertTrue(barrier.active)
                        barrier.current = shield_hp
                    before = self.health(self.enemy)
                    start = tuple(self.rein.mover.position)
                    angles = aim(fight._eye(self.other), (0, 2.2, 0))
                    self.run.step({self.rein: (0, 0, held), self.other: (*angles, 2)})
                    [rocket] = fight.rockets
                    self.assertEqual(rocket.definition, 0x040000000000072C)
                    self.assertFalse(defenses.phased(self.enemy))
                    self.run.step({self.rein: (0, 0, held), self.enemy: (0, 0, 8 if phase else 0)})
                    self.assertEqual(defenses.phased(self.enemy), phase)
                    self.advance(35, buttons=held)
                    self.assertEqual(defenses.phased(self.enemy), phase)
                    self.assertFalse(fight.rockets)
                    self.assertEqual(self.health(self.enemy), before if phase else before - 120)
                    self.assertEqual(self.health(self.rein), 390 if phase and not shield_hp else 500)
                    if phase and not shield_hp:
                        self.assertGreater(self.rein.mover.position[1], start[1])
                        self.assertLess(self.rein.mover.position[2], start[2])
                    else:
                        self.assertEqual(tuple(self.rein.mover.position), start)
                    if shield_hp:
                        self.assertEqual(barrier.current, max(0, shield_hp - 120))
                        self.assertEqual(barrier.active, shield_hp > 120)
                    if shield_hp == 100:
                        self.assertGreater(barrier.broken_until, fight.now)
                        self.run.step({self.rein: (0, 0, held), self.other: (*angles, 1)})
                        self.assertEqual(self.health(self.rein), 485)
                        self.assertEqual(self.health(self.enemy), before)

    def test_helix_impulse_uses_inner_and_outer_rings_with_cover_and_range(self):
        cases = (
            ("inner", (0, 2.2, 0), False, (4, 6)),
            ("outer", (2, 1, 0), False, (3, 1.5)),
            ("outside", (4, 1, 0), False, None),
            ("wall", (0, 2.2, 0), True, None),
        )
        for name, destination, wall, expected in cases:
            with self.subTest(name=name):
                test_game_reinhardt.HammerTests.setUp(self)
                self.place(self.enemy, (20, 1, 0))
                self.place(self.other, (0, 1, 8))
                if wall:
                    test_game_reinhardt.charge_wall(self, z=3)
                self.advance(6, buttons=0)
                fight = self.match.combat
                angles = aim(fight._eye(self.other), destination)
                start = tuple(self.rein.mover.position)
                applied = []
                add_impulse = self.rein.mover.add_impulse

                def record_impulse(velocity, frame):
                    result = add_impulse(velocity, frame)
                    applied.append(result)
                    return result

                with patch.object(self.rein.mover, "add_impulse", side_effect=record_impulse) as impulse:
                    self.run.step({self.other: (*angles, 2)})
                    self.assertEqual(len(fight.rockets), 1)
                    for _ in range(25):
                        self.advance(1, buttons=0)
                        if not fight.rockets:
                            break
                    self.advance(2, buttons=0)
                self.assertFalse(fight.rockets)
                if expected is None:
                    impulse.assert_not_called()
                    self.assertEqual(tuple(self.rein.mover.position), start)
                    self.assertEqual(self.health(self.rein), 500)
                else:
                    impulse.assert_called_once()
                    (x, y, z), _ = impulse.call_args.args
                    self.assertAlmostEqual(math.hypot(x, z), expected[0])
                    self.assertAlmostEqual(y, expected[1])
                    [(x, y, z)] = applied
                    self.assertAlmostEqual(math.hypot(x, z), expected[0] * 0.7, places=5)
                    self.assertAlmostEqual(y, expected[1] * 0.7, places=5)
                    self.assertGreater(self.rein.mover.position[1], start[1])
                    self.assertLess(self.health(self.rein), 500)

    def test_shield_bash_respects_live_defense_and_reinhardt_behind_it(self):
        for hero in (defenses.ORISA, defenses.ZENYATTA, defenses.REAPER, defenses.MOIRA):
            with self.subTest(hero=hero):
                test_game_reinhardt.HammerTests.setUp(self)
                self.assertTrue(self.match.switch_hero(self.enemy, heroes()[hero]))
                self.assertTrue(self.match.switch_hero(self.other, heroes()[0x02E0000000000195]))
                self.place(self.enemy, (0, 1, 2))
                self.place(self.other, (0, 1, 4.8))
                hold = {self.other: (-32768, 0, 2)}
                for _ in range(35):
                    self.run.step(hold)
                if hero == defenses.ZENYATTA:
                    script = self.enemy.body_script
                    script.set_ult_charge(script.component.entity_var(338).value())
                self.run.step({**hold, self.enemy: (0, 0, 32 if hero == defenses.ZENYATTA else 8)})
                self.assertTrue(defenses.control_immune(self.enemy))
                phased = defenses.phased(self.enemy)
                self.assertEqual(phased, hero in (defenses.REAPER, defenses.MOIRA))
                start = tuple(self.enemy.mover.position)
                before = self.health(self.enemy)
                fight = self.match.combat
                with patch.object(fight, "_damage", wraps=fight._damage) as damage:
                    self.run.step({self.other: (-32768, 0, 3)})
                    dash = fight.brigitte_barriers.shields[self.other.body].instance.states[14]
                    self.assertTrue(dash.active)
                    for _ in range(12):
                        self.run.step(hold)
                        if not dash.active:
                            break
                self.assertFalse(dash.active)
                self.assertTrue(defenses.control_immune(self.enemy))
                self.assertFalse(fight.controlled(self.enemy.body))
                self.assertEqual(tuple(self.enemy.mover.position), start)
                self.assertAlmostEqual(before - self.health(self.enemy), 1.5 if hero == defenses.ORISA else 0)
                self.assertEqual(self.health(self.rein), 497.5 if phased else 500)
                self.assertEqual(fight.controlled(self.rein.body), phased)
                self.assertEqual(
                    [(call.args[1].entity, call.args[2]) for call in damage.call_args_list],
                    [(self.rein.body if phased else self.enemy.body, 5)],
                )

    def test_fortify_allows_helix_damage_but_denies_the_blast_impulse(self):
        test_game_reinhardt.HammerTests.setUp(self)
        self.defend(defenses.ORISA, z=6)
        self.place(self.rein, (20, 1, 0))
        self.place(self.other, (0, 1, 12))
        self.advance(6, buttons=0)
        start = tuple(self.enemy.mover.position)
        before = self.health(self.enemy)
        fight = self.match.combat
        angles = aim(fight._eye(self.other), (0, 2.2, 6))
        with patch.object(self.enemy.mover, "add_impulse", wraps=self.enemy.mover.add_impulse) as impulse:
            self.run.step({self.other: (*angles, 2)})
            self.assertEqual(len(fight.rockets), 1)
            self.advance(25, buttons=0)
        self.assertFalse(fight.rockets)
        self.assertTrue(defenses.control_immune(self.enemy))
        self.assertAlmostEqual(self.health(self.enemy), before - 62, places=4)
        self.assertEqual(tuple(self.enemy.mover.position), start)
        impulse.assert_not_called()

    def test_wraith_and_fade_recovery_tail_does_not_extend_protection(self):
        for hero in (defenses.REAPER, defenses.MOIRA):
            with self.subTest(hero=hero):
                test_game_reinhardt.HammerTests.setUp(self)
                self.assertTrue(self.match.switch_hero(self.enemy, heroes()[hero]))
                self.place(self.enemy, (0, 1, 3))
                self.place(self.other, (20, 1, 0))
                self.run.step({self.enemy: (0, 0, 8)})
                self.assertTrue(defenses.phased(self.enemy))
                before = self.health(self.enemy)
                self.advance((187 if hero == defenses.REAPER else 46) - 23, buttons=0)
                self.assertTrue(defenses.phased(self.enemy))
                self.advance(1, buttons=1)
                for _ in range(45):
                    self.advance(1, buttons=0)
                    if self.health(self.enemy) < before:
                        break
                self.assertFalse(defenses.phased(self.enemy))
                self.assertTrue(self.enemy.body_script.component.entity_var(10467).value())
                self.assertEqual(self.health(self.enemy), before - 85)

    def test_active_hammer_swing_can_hit_once_after_wraith_or_fade_ends(self):
        for hero, phase_frames in ((defenses.REAPER, 187), (defenses.MOIRA, 46)):
            for button in (1, 0x800):
                with self.subTest(hero=hero, button=button):
                    test_game_reinhardt.HammerTests.setUp(self)
                    self.assertTrue(self.match.switch_hero(self.enemy, heroes()[hero]))
                    self.place(self.enemy, (0, 1, 3))
                    self.place(self.other, (20, 1, 0))
                    self.run.step({self.enemy: (0, 0, 8)})
                    before = self.health(self.enemy)
                    self.advance(phase_frames - 36, buttons=0)
                    self.advance(1, buttons=button)
                    self.advance(32, buttons=0)
                    self.assertTrue(defenses.phased(self.enemy))
                    fight = self.match.combat
                    records = fight.hammer_hits[self.rein.body]
                    [(attack, (counter, contacts))] = records.items()
                    self.assertFalse(contacts)
                    self.assertEqual(self.health(self.enemy), before)

                    for _ in range(10):
                        self.advance(1, buttons=0)
                        if self.health(self.enemy) < before:
                            break
                    self.assertFalse(defenses.phased(self.enemy))
                    self.assertEqual(records[attack][0], counter)
                    self.assertEqual(self.health(self.enemy), before - 85)
                    self.advance(25, buttons=0)
                    self.assertEqual(self.health(self.enemy), before - 85)
                    notices = self.rein.client.messages(combat.HIT)
                    self.assertEqual([notice["+0x78"]["+0x4"] for notice in notices], [85])

    def test_fortify_reduces_hammer_and_fire_strike_before_armor(self):
        for button, expected in ((1, 46), (0x800, 46), (16, 55)):
            with self.subTest(button=button):
                test_game_reinhardt.HammerTests.setUp(self)
                self.defend(defenses.ORISA)
                before = self.health(self.enemy)
                self.attack(button)
                self.assertAlmostEqual(before - self.health(self.enemy), expected, places=4)
                self.assertEqual(self.enemy.mover.position[0], 0)

    def test_fortify_rejects_charge_capture_and_shatter_control(self):
        for button, z, expected in ((8, 3, 0), (32, 6, 25)):
            with self.subTest(button=button):
                test_game_reinhardt.HammerTests.setUp(self)
                self.defend(defenses.ORISA, z)
                before = self.health(self.enemy)
                self.attack(button)
                self.assertAlmostEqual(before - self.health(self.enemy), expected, places=4)
                self.assertFalse(self.match.combat.controlled(self.enemy.body))
                self.assertFalse(self.match.combat.pinned(self.enemy.body))

    def test_charge_side_bump_damages_and_displaces_after_transcendence_expires(self):
        test_game_reinhardt.HammerTests.setUp(self)
        self.defend(defenses.ZENYATTA)
        self.place(self.enemy, (1.6, 1, 3))
        self.advance(385, buttons=0)
        self.assertFalse(defenses.control_immune(self.enemy))
        before = self.health(self.enemy)
        self.attack(8)
        self.assertEqual(self.health(self.enemy), before - 50)
        self.assertGreater(self.enemy.mover.position[0], 1.6)
        self.assertGreater(self.enemy.mover.position[1], 1)
        self.assertFalse(self.match.combat.pinned(self.enemy.body))

    def test_fortify_and_cryo_end_charge_without_a_body_hit(self):
        for hero in (defenses.ORISA, defenses.MEI):
            with self.subTest(hero=hero):
                test_game_reinhardt.HammerTests.setUp(self)
                self.defend(hero)
                component = self.enemy.body_script.component
                self.assertTrue(component.entity_var(6601).value())
                self.assertEqual(bool(component.entity_var(9161).value()), hero == defenses.ORISA)
                before = self.health(self.enemy)
                self.attack(8)
                fight = self.match.combat
                self.assertFalse(fight.charge_drives)
                self.assertFalse(fight.charge_contacts)
                self.assertFalse(fight.controlled(self.enemy.body))
                self.assertEqual(self.health(self.enemy), before)
                self.assertFalse(self.rein.client.messages(combat.HIT))
                charge = next(i for i in self.rein.body_script.component.instances.values()
                              if i.graph.index == 0x007D)
                self.assertGreater(charge.find_var(0, 61).value(), 8)

    def test_cryo_blocks_hammer_fire_strike_and_shatter_until_cancel(self):
        for button, z in ((1, 3), (0x800, 3), (16, 3), (32, 6)):
            with self.subTest(button=button):
                test_game_reinhardt.HammerTests.setUp(self)
                self.defend(defenses.MEI, z)
                before = self.health(self.enemy)
                fight = self.match.combat
                with patch.object(fight, "_damage", wraps=fight._damage) as damage:
                    self.attack(button)
                self.assertEqual(
                    [call.args[2] for call in damage.call_args_list
                     if call.args[1].entity == self.enemy.body],
                    [{1: 85, 0x800: 85, 16: 100, 32: 50}[button]],
                )
                self.assertEqual(self.health(self.enemy), before)
                self.assertFalse(fight.controlled(self.enemy.body))
                self.assertFalse(self.rein.client.messages(combat.HIT))
                self.run.step({self.enemy: (0, 0, 2)})
                self.advance(2, buttons=0)
                self.assertFalse(defenses.control_immune(self.enemy))
                self.assertFalse(self.enemy.body_script.component.entity_var(6601).value())
                self.place(self.enemy, (0, 1, 3))
                self.advance(4, buttons=0)
                self.attack(1)
                self.assertEqual(self.health(self.enemy), before - 85)

    def test_cryo_expiry_clears_impact_flag_and_allows_charge_capture(self):
        test_game_reinhardt.HammerTests.setUp(self)
        self.defend(defenses.MEI)
        self.advance(243, buttons=0)
        self.assertTrue(defenses.control_immune(self.enemy))
        self.advance(12, buttons=0)
        self.assertFalse(defenses.control_immune(self.enemy))
        self.assertFalse(self.enemy.body_script.component.entity_var(6601).value())
        self.attack(8)
        self.assertTrue(self.match.combat.pinned(self.enemy.body))

    def test_transcendence_denies_damage_and_control_for_all_attacks(self):
        for button, z in ((1, 3), (16, 3), (8, 3), (32, 6)):
            with self.subTest(button=button):
                test_game_reinhardt.HammerTests.setUp(self)
                self.defend(defenses.ZENYATTA, z)
                before = self.health(self.enemy)
                position = tuple(self.enemy.mover.position)
                fight = self.match.combat
                with patch.object(fight, "_damage", wraps=fight._damage) as damage:
                    self.attack(button)
                self.assertEqual(
                    [call.args[2] for call in damage.call_args_list
                     if call.args[1].entity == self.enemy.body],
                    [{1: 85, 16: 100, 8: 50, 32: 50}[button]],
                )
                self.assertEqual(self.health(self.enemy), before)
                self.assertEqual(tuple(self.enemy.mover.position), position)
                self.assertFalse(self.enemy.mover.impulse_active)
                self.assertFalse(fight.controlled(self.enemy.body))
                self.assertFalse(fight.pinned(self.enemy.body))
                self.assertFalse(self.rein.client.messages(combat.HIT))

    def test_earthshatter_uses_defences_activated_after_wave_release(self):
        for hero, button, damage, contacted in (
            (defenses.ORISA, 8, 25, True),
            (defenses.MEI, 8, 0, True),
            (defenses.ZENYATTA, 32, 0, True),
            (defenses.REAPER, 8, 0, False),
            (defenses.MOIRA, 8, 0, False),
        ):
            with self.subTest(hero=hero):
                test_game_reinhardt.HammerTests.setUp(self)
                self.assertTrue(self.match.switch_hero(self.enemy, heroes()[hero]))
                self.place(self.enemy, (0, 1, 14))
                self.place(self.other, (0, 1, 18))
                self.advance(12, buttons=0)
                script = self.rein.body_script
                script.set_ult_charge(script.component.entity_var(338).value())
                if button == 32:
                    target = self.enemy.body_script
                    target.set_ult_charge(target.component.entity_var(338).value())
                fight = self.match.combat
                before = self.health(self.enemy)
                self.advance(1, buttons=32)
                for _ in range(50):
                    self.advance(1, buttons=0)
                    wave = fight.shatters.get(self.rein.body)
                    if wave is not None and wave.travelled > 0:
                        break
                self.assertIsNotNone(wave)
                self.assertGreater(wave.travelled, 0)
                self.assertLess(wave.travelled, 8)
                self.assertFalse(defenses.control_immune(self.enemy))
                self.assertEqual(self.health(self.enemy), before)
                self.run.step({self.enemy: (0, 0, button)})
                self.assertTrue(defenses.control_immune(self.enemy))

                downstream = (self.other.body, fight.replacement_ticks.get(self.other.body))
                for _ in range(40):
                    self.advance(1, buttons=0)
                    if downstream in wave.hit:
                        break
                self.assertTrue(defenses.control_immune(self.enemy))
                self.assertIn(downstream, wave.hit)
                self.assertEqual(self.health(self.other), 150)
                self.assertTrue(fight.controlled(self.other.body))
                key = (self.enemy.body, fight.replacement_ticks.get(self.enemy.body))
                self.assertEqual(key in wave.hit, contacted)
                self.assertAlmostEqual(self.health(self.enemy), before - damage, places=4)
                self.assertFalse(fight.controlled(self.enemy.body))

    def test_fortify_expires_and_ordinary_damage_resumes(self):
        test_game_reinhardt.HammerTests.setUp(self)
        self.defend(defenses.ORISA)
        self.advance(220, buttons=0)
        self.assertTrue(defenses.control_immune(self.enemy))
        self.advance(40, buttons=0)
        self.assertFalse(defenses.control_immune(self.enemy))
        before = self.health(self.enemy)
        self.attack(1)
        self.assertEqual(before - self.health(self.enemy), 80)

    def test_fire_strike_penetrates_transcendence_and_damages_enemy_behind(self):
        test_game_reinhardt.HammerTests.setUp(self)
        self.defend(defenses.ZENYATTA)
        self.place(self.other, (0, 1, 6))
        self.advance(3, buttons=0)
        before = self.health(self.enemy)
        self.attack(16)
        self.assertEqual(self.health(self.enemy), before)
        self.assertEqual(self.health(self.other), 100)
        [shot] = self.match.combat.rockets
        self.assertEqual({body for body, _ in shot.hit_targets}, {self.enemy.body, self.other.body})
        [hit] = self.rein.client.messages(combat.HIT)
        self.assertEqual(hit["+0x78"]["+0x0"]["+0x0"], self.other.body)

    def test_live_fire_strike_can_hit_after_an_earlier_phased_pass(self):
        for hero, phase_frames in ((defenses.REAPER, 187), (defenses.MOIRA, 46)):
            with self.subTest(hero=hero):
                test_game_reinhardt.HammerTests.setUp(self)
                self.assertTrue(self.match.switch_hero(self.enemy, heroes()[hero]))
                self.place(self.enemy, (0, 1, 2))
                self.place(self.other, (20, 1, 0))
                self.run.step({self.enemy: (0, 0, 8)})
                before = self.health(self.enemy)
                self.advance(phase_frames - 45, buttons=0)
                self.advance(1, buttons=16)
                fight = self.match.combat
                for _ in range(43):
                    self.advance(1, buttons=0)
                    if fight.rockets and fight.rockets[0].position[2] >= 4.5:
                        break
                [shot] = fight.rockets
                self.assertGreaterEqual(shot.position[2], 4.5)
                self.assertTrue(defenses.phased(self.enemy))
                self.assertEqual(self.health(self.enemy), before)
                self.assertFalse(shot.hit_targets)

                # Bring the same body into the projectile's path a second time.
                self.place(self.enemy, (0, 1, 16))
                for _ in range(45):
                    self.advance(1, buttons=0)
                    if self.health(self.enemy) < before:
                        break
                self.assertFalse(defenses.phased(self.enemy))
                self.assertIn(shot, fight.rockets)
                self.assertEqual(self.health(self.enemy), before - 100)
                self.assertEqual({body for body, _ in shot.hit_targets}, {self.enemy.body})
                self.advance(20, buttons=0)
                self.assertEqual(self.health(self.enemy), before - 100)
                notices = self.rein.client.messages(combat.HIT)
                self.assertEqual([notice["+0x78"]["+0x4"] for notice in notices], [100])

    def test_transcendence_expires_and_does_not_protect_replacement(self):
        test_game_reinhardt.HammerTests.setUp(self)
        self.defend(defenses.ZENYATTA)
        self.advance(345, buttons=0)
        self.assertTrue(defenses.control_immune(self.enemy))
        self.advance(40, buttons=0)
        self.assertFalse(defenses.control_immune(self.enemy))
        self.attack(1)
        self.assertEqual(self.health(self.enemy), 115)
        self.defend(defenses.ZENYATTA)
        self.assertTrue(self.match.switch_hero(self.enemy, heroes()[defenses.ZENYATTA]))
        self.assertFalse(defenses.control_immune(self.enemy))
