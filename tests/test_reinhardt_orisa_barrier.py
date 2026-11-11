"""Actual Orisa Protective Barrier against Reinhardt's original inputs."""

import math
import os
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import test_game_reinhardt as fixtures

from ow174.game import orisa_barrier
from ow174.game.body_vm_snapshot import export_component_vm, snapshot_digest
from ow174.game.content import SOLDIER, heroes

MESH = Path(__file__).resolve().parents[3] / "013E" / "orisa_geometry_174.json"


@unittest.skipUnless(MESH.is_file(), "verified build104319 Orisa mesh required")
class ReinhardtOrisaBarrierTests(unittest.TestCase):
    place = staticmethod(fixtures.HammerTests.place)
    health = fixtures.HammerTests.health
    advance = fixtures.HammerTests.advance

    def launch(self):
        self.enterContext(patch.dict(os.environ, {"ORPHEUS_ORISA_GEOMETRY": str(MESH)}))
        fixtures.HammerTests.setUp(self)
        self.assertTrue(self.match.switch_hero(self.enemy, heroes()[orisa_barrier.HERO]))
        self.place(self.enemy, (0, 1, 6))
        self.place(self.other, (30, 1, 0))
        self.fight = self.match.combat
        self.advance(15, buttons=0)
        self.run.step({self.enemy: (0, 0, 16)})
        for _ in range(20):
            shot = next((r for r in self.fight.rockets if r.deploy_body == self.enemy.body), None)
            if shot is not None:
                return shot
            self.run.step({})
        self.fail("actual barrier cast emitted no deployment flight")

    def deploy(self, *, distance=5):
        self.launch()
        for _ in range(120):
            self.run.step({})
            if self.fight.orisa_barriers.active:
                break
        [shield] = self.fight.orisa_barriers.active.values()
        self.assertEqual(shield.health, 600)
        self.assertEqual(shield.script.graph.index, 0xBD8)
        self.assertEqual(shield.component.entity_var(567).value(), 20)
        x, y, z = shield.position
        self.place(self.rein, (x, y, z + distance))
        self.place(self.enemy, (x, y, z - 2))
        self.attack(5)
        return shield

    def attack(self, frames=1, *, button=0):
        self.advance(frames, buttons=button, yaw=-32768)

    def test_shield_bash_ignores_its_allied_deployment(self):
        shield = self.deploy()
        fixtures.shield_bash_at_deployment(self, shield, shield.position[2] + 5, -1, allied=True)

    def test_shield_bash_stays_stopped_after_protective_barrier_expires(self):
        shield = self.deploy()
        fixtures.shield_bash_at_deployment(self, shield, shield.position[2] + 5, -1, expiry=shield.until)
        self.assertNotIn(shield.entity, self.fight.orisa_barriers.active)

    def test_actual_fire_strike_hits_shield_once_and_penetrates(self):
        shield = self.deploy()
        self.attack(button=16)
        self.attack(65)
        self.assertEqual(shield.health, 500)
        self.assertEqual(shield.component.entity_var(253).value(), 500)
        self.assertEqual(self.health(self.enemy), 355)

    def test_actual_hammer_and_alias_hit_mesh_without_reaching_body(self):
        for button in (1, 0x800):
            with self.subTest(button=button):
                shield = self.deploy(distance=3)
                before = snapshot_digest(export_component_vm(shield.component))
                self.attack(button=button)
                self.attack(44)
                self.assertEqual(shield.health, 515)
                self.assertEqual(self.health(self.enemy), 450)
                self.assertNotEqual(snapshot_digest(export_component_vm(shield.component)), before)


    def test_actual_earthshatter_break_stops_ground_front(self):
        shield = self.deploy(distance=6)
        self.fight.orisa_barriers.damage(shield, 560)
        self.rein.body_script.set_ult_charge(1540)
        self.attack(button=32)
        self.attack(75)
        self.assertEqual(shield.health, 0)
        self.assertNotIn(shield.body, self.fight.orisa_barriers.active)
        self.assertEqual(self.health(self.enemy), 450)
        self.assertFalse(self.fight.controlled(self.enemy.body))

    def test_expiring_barrier_only_blocks_earthshatter_rays_that_reached_it(self):
        shield = self.deploy(distance=10)
        x, y, z = self.rein.mover.position
        angle = -math.pi + math.radians(15)
        dx, dz = math.sin(angle), math.cos(angle)
        self.place(self.other, (x + 15 * dx, y, z + 15 * dz))
        geometry = self.fight.orisa_barriers.geometry
        late_contact = geometry.cast(shield.position, shield.yaw, (x, y + .1, z), (dx, 0, dz), 15)
        self.assertGreater(late_contact, 12)
        self.attack(shield.until - self.match.tick - 47)
        self.rein.body_script.set_ult_charge(1540)
        self.attack(button=32)
        for _ in range(60):
            self.attack()
            if shield.health < 600:
                break
        wave = self.fight.shatters[self.rein.body]
        self.assertEqual(shield.health, 550)
        self.assertIn(50, wave.blocked)
        self.assertNotIn(80, wave.blocked)
        stopped = wave.fronts[50]

        for _ in range(8):
            if not shield.blocking():
                break
            self.attack()
        self.assertFalse(shield.blocking())
        self.assertLess(wave.travelled, late_contact)
        self.attack(28)
        self.assertEqual(wave.fronts[50], stopped)
        self.assertNotIn(80, wave.blocked)
        self.assertEqual(self.health(self.enemy), 450)
        self.assertEqual(self.health(self.other), 150)
        self.assertTrue(self.fight.controlled(self.other.body))

    def test_charge_passes_mesh_and_captures_orisa(self):
        shield = self.deploy(distance=4)
        self.attack(button=8)
        for _ in range(100):
            self.attack()
            if self.fight.pinned(self.enemy.body):
                break
        self.assertTrue(self.fight.pinned(self.enemy.body))
        self.assertEqual(shield.health, 600)
        self.assertEqual(self.health(self.enemy), 450)

    def test_deployed_barrier_survives_owner_replacement_and_expires(self):
        shield = self.deploy()
        manager = self.fight.orisa_barriers
        self.assertTrue(self.match.switch_hero(self.enemy, heroes()[SOLDIER]))
        before = shield.until - 1
        self.match.update(before * .016, before)
        self.assertIn(shield.body, manager.active)
        for at in (shield.until + 2, shield.until + 400):
            self.match.update(at * .016, at)
        self.assertNotIn(shield.body, manager.active)
        self.assertNotIn(shield.body, manager.retiring)
        self.assertFalse(shield.component.instances)


    def test_replacing_caster_retires_unlanded_round(self):
        shot = self.launch()
        self.assertTrue(self.match.switch_hero(self.enemy, heroes()[SOLDIER]))
        self.assertNotIn(shot, self.fight.rockets)
        self.attack(80)
        self.assertFalse(self.fight.orisa_barriers.pending)
        self.assertFalse(self.fight.orisa_barriers.active)

    def test_allied_barrier_passes_fire_strike_to_enemy(self):
        shield = self.deploy()
        self.match.game_map = replace(self.match.game_map, free_for_all=False)
        self.rein.team = self.enemy.team = shield.team
        self.other.team = shield.team + 1
        x, y, z = shield.position
        self.place(self.enemy, (5, y, z - 2))
        self.place(self.other, (x, y, z - 2))
        self.attack(button=16)
        self.attack(65)
        self.assertEqual(shield.health, 600)
        self.assertEqual(self.health(self.other), 100)
