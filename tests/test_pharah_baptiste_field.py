"""Actual Baptiste field launch and Pharah lethality."""

import unittest
from dataclasses import replace
from unittest.mock import patch

import test_pharah_ana_grenade as grenade
import test_pharah_defenses as defense
from test_game_combat import aim

from ow174.game.collision import World
from ow174.game.content import heroes


class PharahFieldTests(unittest.TestCase):
    def arena(self, *, team=False, armed=True, low=False, antiheal=False):
        fixture = defense.PharahDefenseTests()
        run, pharah, baptiste, other = fixture.encounter("Baptiste")
        other.mover.position[0] = 20
        other.mover.body = None
        if team:
            run.match.game_map = replace(run.match.game_map, free_for_all=False)
            pharah.team = 0
            baptiste.team = other.team = 1
            other.mover.position = [0, pharah.mover.position[1], 6]
        fight = run.match.combat
        if antiheal:
            ana = next(hero for hero in heroes().values() if hero.name == "Ana")
            self.assertTrue(run.match.switch_hero(other, ana))
            other.mover.position = [0, pharah.mover.position[1], 4]
            other.mover.body = None
            grenade.PharahGrenadeTests().throw(run, other, baptiste)
            other.mover.position[0] = 20
            other.mover.body = None
        if low:
            vitals = fixture.health(run, baptiste)
            vitals.damage(vitals.total - 5)
            fight._sync_body_health(baptiste, vitals)
        run.step({baptiste: (16384, 3800, 16)})
        for _ in range(100):
            run.step({})
            ward = fight.baptiste_fields.get(baptiste.body)
            if ward is not None and (not armed or baptiste.body in ward.bindings):
                break
        ward = fight.baptiste_fields[baptiste.body]
        self.assertEqual(baptiste.body in ward.bindings, armed)
        self.assertEqual(ward.health, 150)
        return fixture, run, pharah, baptiste, ward

    def test_primary_clamps_health_at_authored_floor_then_kills_after_expiry(self):
        fixture, run, pharah, baptiste, ward = self.arena()
        vitals = fixture.health(run, baptiste)
        vitals.damage(150)
        fixture.fire(run, pharah)
        self.assertAlmostEqual(vitals.total, 20)
        self.assertFalse(vitals.dead)
        self.assertFalse(run.match.combat.respawns)
        while run.frame < ward.expires:
            run.step({})
        self.assertNotIn(baptiste.body, run.match.combat.baptiste_fields)
        fixture.fire(run, pharah)
        self.assertTrue(vitals.dead)

    def test_quick_melee_clamps_at_immortality_floor_then_kills_after_expiry(self):
        fixture, run, pharah, baptiste, ward = self.arena()
        vitals = fixture.health(run, baptiste)
        vitals.damage(vitals.total - 35)
        pharah.mover.position[2] = baptiste.mover.position[2] - 1.8
        pharah.mover.body = None
        for _ in range(4):
            run.step({})
        charge = pharah.body_script.component.entity_var(342)
        run.step({pharah: (0, 0, 0x800)})
        self.assertEqual(vitals.total, 20)
        self.assertFalse(vitals.dead)
        self.assertEqual(ward.health, 150)
        self.assertEqual(charge.value(), 15)
        while run.frame < ward.expires:
            run.step({})
        self.assertNotIn(baptiste.body, run.match.combat.baptiste_fields)
        run.step({pharah: (0, 0, 0x800)})
        self.assertTrue(vitals.dead)
        self.assertEqual(vitals.killer, pharah.body)
        self.assertEqual(charge.value(), 35)

    def test_same_barrage_clamps_then_kills_when_immortality_field_expires(self):
        fixture, run, pharah, baptiste, ward = self.arena()
        fight = run.match.combat
        vitals = fixture.health(run, baptiste)
        vitals.damage(vitals.total - 50)
        for _ in range(ward.expires - run.frame - 45):
            run.step({})
        script = pharah.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        run.step({pharah: (0, 0, 32)})
        for _ in range(35):
            if vitals.total <= 20:
                break
            run.step({})
        self.assertAlmostEqual(vitals.total, 20)
        self.assertIn(baptiste.body, ward.bindings)
        self.assertEqual(ward.health, 150)
        while run.frame < ward.expires - 1:
            run.step({})
            self.assertEqual(vitals.total, 20)
            self.assertFalse(vitals.dead)
        for _ in range(12):
            run.step({})
            if vitals.dead:
                break
        self.assertTrue(vitals.dead)
        self.assertGreaterEqual(vitals.death_tick, ward.expires)
        self.assertEqual(vitals.killer, pharah.body)
        self.assertNotIn(baptiste.body, fight.baptiste_fields)
        self.assertTrue(script.ultimate_running())
        self.assertEqual(script.component.entity_var(342).value(), 0)

    def test_same_barrage_destroys_device_then_kills_exposed_baptiste(self):
        fixture, run, pharah, baptiste, ward = self.arena()
        fight = run.match.combat
        vitals = fixture.health(run, baptiste)
        vitals.damage(vitals.total - 35)
        script = pharah.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        x, y, z = ward.segment()[0]
        pharah.mover.position[0] = x
        pharah.mover.position[2] = z - 4
        pharah.mover.body = None
        yaw, pitch = aim(fight._eye(pharah), (x, y, z))
        run.step({pharah: (yaw, pitch, 32)})
        for _ in range(65):
            if baptiste.body not in fight.baptiste_fields:
                break
            run.step({pharah: (yaw, pitch, 0)})
        self.assertNotIn(baptiste.body, fight.baptiste_fields)
        self.assertLess(run.frame, ward.expires)
        self.assertFalse(ward.bindings)
        self.assertEqual(ward.health, 0)
        self.assertEqual(vitals.total, 35)
        yaw, pitch = fixture.aim_at_body(fight, pharah, baptiste)
        for _ in range(55):
            run.step({pharah: (yaw, pitch, 0)})
            if vitals.dead:
                break
        self.assertTrue(vitals.dead)
        self.assertEqual(vitals.killer, pharah.body)
        self.assertTrue(script.ultimate_running())
        self.assertEqual(script.component.entity_var(342).value(), 0)

    def test_pharah_can_destroy_the_deployed_device(self):
        fixture, run, pharah, baptiste, ward = self.arena()
        fight = run.match.combat
        yaw, pitch = aim(fight._eye(pharah), ward.segment()[0])
        run.step({pharah: (yaw, pitch, 1)})
        for _ in range(55):
            run.step({})
            if ward.health < 150:
                break
        self.assertEqual(ward.health, 30)
        for _ in range(60):
            run.step({})
        run.step({pharah: (yaw, pitch, 1)})
        for _ in range(55):
            run.step({})
            if baptiste.body not in fight.baptiste_fields:
                break
        self.assertNotIn(baptiste.body, fight.baptiste_fields)
        self.assertFalse(ward.bindings)
        vitals = fixture.health(run, baptiste)
        self.assertEqual(vitals.total, 200)

    def test_lethal_rocket_when_protected_body_is_outside_radius_or_occluded(self):
        for wall_case in (False, True):
            with self.subTest(wall=wall_case):
                fixture, run, pharah, baptiste, ward = self.arena()
                fight = run.match.combat
                vitals = fixture.health(run, baptiste)
                vitals.damage(150)
                x, y, z = ward.position
                collision = fight._collision()
                if wall_case:
                    mid = x / 2
                    collision = World([(mid, y - 5, z - 5, mid, y + 5, z - 5, mid, y + 5, z + 5),
                                       (mid, y - 5, z - 5, mid, y + 5, z + 5, mid, y - 5, z + 5)])
                else:
                    baptiste.mover.position[2] += 12
                    baptiste.mover.body = None
                with patch.object(fight, "_collision", return_value=collision):
                    fixture.fire(run, pharah)
                self.assertTrue(vitals.dead)
                self.assertNotIn(baptiste.body, ward.bindings)

    def test_unarmed_device_does_not_stop_lethal_rocket(self):
        fixture, run, pharah, baptiste, ward = self.arena(armed=False)
        vitals = fixture.health(run, baptiste)
        vitals.damage(150)
        fixture.fire(run, pharah)
        self.assertTrue(vitals.dead)
        self.assertLess(vitals.death_tick, ward.active_tick)

    def test_allied_body_receives_protection_but_attacker_does_not(self):
        fixture, run, pharah, _, ward = self.arena(team=True)
        ally = run.players[2]
        self.assertIn(ally.body, ward.bindings)
        self.assertNotIn(pharah.body, ward.bindings)
        vitals = fixture.health(run, ally)
        vitals.damage(150)
        fixture.fire(run, pharah)
        self.assertAlmostEqual(vitals.total, 20)
        self.assertFalse(vitals.dead)

    def test_active_field_restores_a_living_body_to_floor_on_entry(self):
        fixture, run, pharah, baptiste, ward = self.arena(low=True)
        vitals = fixture.health(run, baptiste)
        self.assertAlmostEqual(vitals.total, 20)
        self.assertIn(baptiste.body, ward.restoration_seen)
        fixture.fire(run, pharah)
        self.assertAlmostEqual(vitals.total, 20)

    def test_enemy_grenade_defers_restoration_but_preserves_damage_floor(self):
        fixture, run, pharah, baptiste, ward = self.arena(low=True, antiheal=True)
        vitals = fixture.health(run, baptiste)
        (_, end), = vitals.healing_blocks
        self.assertEqual(vitals.total, 5)
        self.assertNotIn(baptiste.body, ward.restoration_seen)
        fixture.fire(run, pharah)
        self.assertEqual(vitals.total, 5)
        self.assertFalse(vitals.dead)
        while run.frame < end - 1:
            run.step({})
        self.assertEqual(vitals.total, 5)
        run.step({})
        self.assertAlmostEqual(vitals.total, 20)
        self.assertIn(baptiste.body, ward.restoration_seen)
