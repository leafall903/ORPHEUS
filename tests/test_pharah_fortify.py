"""Actual Orisa Fortify input against Pharah rockets, Concussion, and melee."""

import unittest
from dataclasses import replace

import test_pharah_defenses as defense
from test_game_combat import aim

from ow174.game import combat, defenses
from ow174.game.content import heroes


class PharahFortifyTests(unittest.TestCase):
    def arena(self):
        fixture = defense.PharahDefenseTests()
        run, pharah, orisa, other = fixture.encounter("Orisa")
        other.mover.position[0] = 30
        other.mover.body = None
        return fixture, run, pharah, orisa

    def fortify(self, run, orisa):
        run.step({orisa: (-32768, 0, 8)})
        for _ in range(5):
            run.step({})
        self.assertTrue(defenses.fortified(orisa))
        self.assertAlmostEqual(defenses.fortify_scale(orisa), .6)

    def test_primary_reduces_direct_and_blast_before_armor_and_credits_dealt_damage(self):
        fixture, run, pharah, orisa = self.arena()
        self.fortify(run, orisa)
        vitals = fixture.health(run, orisa)
        before = vitals.total
        fixture.fire(run, pharah)
        self.assertAlmostEqual(before - vitals.total, 62, places=4)
        self.assertAlmostEqual(pharah.body_script.component.entity_var(342).value(), 62, places=4)

    def test_same_barrage_recomputes_fortify_after_expiry_without_charge(self):
        fixture, run, pharah, orisa = self.arena()
        orisa.mover.position[2] = 4
        orisa.mover.body = None
        self.fortify(run, orisa)
        for _ in range(222):
            run.step({})
        self.assertTrue(defenses.fortified(orisa))

        vitals = fixture.health(run, orisa)
        script = pharah.body_script
        charge = script.component.entity_var(342)
        script.set_ult_charge(script.component.entity_var(338).value())
        damage_by_phase = {True: [], False: []}
        for frame in range(42):
            fortified = defenses.fortified(orisa)
            before = vitals.total
            hit_count = len(pharah.client.messages(combat.HIT))
            run.step({pharah: (0, 0, 32)} if frame == 0 else {})
            self.assertEqual(charge.value(), 0)
            if vitals.total != before:
                hits = pharah.client.messages(combat.HIT)[hit_count:]
                damage = [hit["+0x78"]["+0x4"] for hit in hits]
                self.assertAlmostEqual(before - vitals.total, sum(damage), places=4)
                damage_by_phase[fortified].append(damage)

        self.assertIn([3, 13], damage_by_phase[True])
        self.assertIn([5, 25], damage_by_phase[False])
        self.assertFalse(defenses.fortified(orisa))

    def test_concussion_is_blocked_until_fortify_expires(self):
        fixture, run, pharah, orisa = self.arena()
        self.fortify(run, orisa)
        vitals = fixture.health(run, orisa)
        before, start = vitals.total, tuple(orisa.mover.position)
        run.step({pharah: (0, 0, 16)})
        for _ in range(40):
            run.step({})
        self.assertEqual(tuple(orisa.mover.position), start)
        self.assertEqual(vitals.total, before)
        self.assertTrue(defenses.fortified(orisa))
        for _ in range(620):
            run.step({})
        self.assertFalse(defenses.fortified(orisa))
        self.assertEqual(defenses.fortify_scale(orisa), 1)
        run.step({pharah: (0, 0, 16)})
        for _ in range(40):
            run.step({})
        self.assertGreater(orisa.mover.position[2], start[2] + 1)
        self.assertEqual(vitals.total, before)

    def test_fortify_started_after_concussion_launch_still_blocks_impact(self):
        _, run, pharah, orisa = self.arena()
        fight = run.match.combat
        orisa.mover.position[2] = 24
        orisa.mover.body = None
        run.step({pharah: (0, 0, 16)})
        for _ in range(40):
            if fight.rockets:
                break
            run.step({})
        self.assertTrue(fight.rockets)
        self.assertFalse(defenses.fortified(orisa))
        start = tuple(orisa.mover.position)
        self.fortify(run, orisa)
        for _ in range(40):
            run.step({})
        self.assertFalse(fight.rockets)
        self.assertEqual(tuple(orisa.mover.position), start)

    def apply_discord(self, run, pharah, orisa):
        zen = run.players[2]
        self.assertTrue(run.match.switch_hero(zen, heroes()[0x02E0000000000020]))
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        pharah.team = zen.team = 0
        orisa.team = 1
        zen.mover.position = [3, pharah.mover.position[1], 2]
        zen.mover.body = None
        fight = run.match.combat
        direction = aim(fight._eye(zen), fight._eye(orisa))
        run.step({zen: (*direction, 8)})
        self.assertEqual(fight.discord[zen.body].target, orisa.body)

    def test_actual_discord_and_fortify_compose_at_rocket_impact(self):
        fixture, run, pharah, orisa = self.arena()
        self.apply_discord(run, pharah, orisa)
        self.fortify(run, orisa)
        vitals = fixture.health(run, orisa)
        before = vitals.total
        fixture.fire(run, pharah)
        self.assertAlmostEqual(before - vitals.total, 80, places=4)
        self.assertAlmostEqual(pharah.body_script.component.entity_var(342).value(), 80, places=4)

    def test_actual_melee_recomputes_fortify_and_discord_before_armor(self):
        fixture, run, pharah, orisa = self.arena()
        orisa.mover.position[2] = 1.8
        orisa.mover.body = None
        self.apply_discord(run, pharah, orisa)
        self.fortify(run, orisa)
        vitals = fixture.health(run, orisa)
        before = vitals.total
        run.step({pharah: (0, 0, 0x800)})
        self.assertAlmostEqual(before - vitals.total, 17.5, places=4)
        charge = pharah.body_script.component.entity_var(342)
        self.assertAlmostEqual(charge.value(), 17.5, places=4)
        self.assertEqual(len(pharah.client.messages(25001)), 1)

        for _ in range(300):
            run.step({})
        self.assertFalse(defenses.fortified(orisa))
        zen = run.players[2]
        self.assertEqual(run.match.combat.discord[zen.body].target, orisa.body)
        before = vitals.total
        run.step({pharah: (0, 0, 0x800)})
        self.assertAlmostEqual(before - vitals.total, 32.5, places=4)
        self.assertAlmostEqual(charge.value(), 50, places=4)
        self.assertEqual(len(pharah.client.messages(25001)), 2)

    def test_control_attacks_deal_reduced_damage_without_disabling_orisa(self):
        cases = (("Ana", 8, 8, 1.5), ("Cassidy", 16, 3, 10), ("Roadhog", 8, 8, 13))
        by_name = {hero.name: hero for hero in heroes().values()}
        for name, button, distance, damage in cases:
            with self.subTest(caster=name):
                fixture, run, pharah, orisa = self.arena()
                caster = run.players[2]
                self.assertTrue(run.match.switch_hero(caster, by_name[name]))
                y = pharah.mover.position[1]
                orisa.mover.position = [0, y, 0]
                orisa.mover.body = None
                pharah.mover.position = [20, y, -8]
                pharah.mover.body = None
                caster.mover.position = [0, y, distance]
                caster.mover.body = None
                self.fortify(run, orisa)
                fight = run.match.combat
                vitals = fixture.health(run, orisa)
                before = vitals.total
                start = tuple(orisa.mover.position)
                centre = (start[0], start[1] + 1, start[2])
                direction = aim(fight._eye(caster), centre)
                run.step({caster: (*direction, button)})
                for _ in range(85):
                    run.step({caster: (*direction, 0)})
                    self.assertFalse(fight.is_incapacitated(orisa))
                self.assertAlmostEqual(before - vitals.total, damage, places=4)
                self.assertEqual(tuple(orisa.mover.position), start)
                self.assertTrue(defenses.fortified(orisa))
                pharah.mover.position[0] = 0
                pharah.mover.body = None
                before = vitals.total
                fixture.fire(run, pharah)
                self.assertAlmostEqual(before - vitals.total, 62, places=4)
