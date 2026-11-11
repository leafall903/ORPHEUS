"""Actual Orisa deployment followed by Pharah projectile input."""

import os
import unittest
from dataclasses import replace

import test_pharah_defenses as defense

from ow174.game.body_vm_snapshot import export_component_vm


@unittest.skipUnless(os.environ.get("ORPHEUS_ORISA_GEOMETRY"), "prepared 104319 mesh required")
class OrisaProjectileTests(unittest.TestCase):
    def deployed(self):
        fixture = defense.PharahDefenseTests()
        run, shooter, owner, behind = fixture.encounter("Orisa")
        fight = run.match.combat
        run.step({owner: (0, 0, 16)})
        self.assertNotIn(owner.body, fight.orisa_shields)
        for _ in range(120):
            run.step({})
            if owner.body in fight.orisa_shields:
                break
        self.assertIn(owner.body, fight.orisa_shields)
        shield = fight.orisa_shields[owner.body]
        self.assertEqual(shield.health, 600)
        self.assertTrue(shield.state_active(0))
        self.assertEqual([i.graph.index for i in shield.component.instances.values()], [0xBD8])
        export_component_vm(shield.component)
        x, y, z = shield.position
        for player, point in ((shooter, (x, y, z + 6)), (owner, (x, y, z - 5)), (behind, (x + 15, y, z))):
            player.mover.position = list(point)
            player.mover.body = None
        return fixture, run, shooter, owner, behind, shield

    @staticmethod
    def fire(run, shooter, buttons):
        run.step({shooter: (-32768, 0, buttons)})
        for _ in range(40):
            run.step({})

    def test_primary_concussion_and_break_against_actual_mesh(self):
        fixture, run, shooter, owner, _, shield = self.deployed()
        before = fixture.health(run, owner).total
        self.fire(run, shooter, 1)
        self.assertEqual(shield.health, 480)
        self.assertEqual(fixture.health(run, owner).total, before)
        position = tuple(owner.mover.position)
        self.fire(run, shooter, 16)
        self.assertEqual(tuple(owner.mover.position), position)
        self.assertEqual(shield.health, 480)
        for _ in range(360):
            run.step({shooter: (-32768, 0, 1)})
            if shield.health == 0:
                break
        self.assertEqual(shield.health, 0)
        self.assertEqual(fixture.health(run, owner).total, before)
        self.assertEqual(shooter.body_script.component.entity_var(342).value(), 0)

    def test_friendly_pass_through_and_authored_expiry(self):
        fixture, run, shooter, owner, behind, shield = self.deployed()
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        shooter.team = owner.team
        behind.team = 1 - owner.team
        x, y, z = shield.position
        behind.mover.position = [x, y, z - 3]
        behind.mover.body = None
        self.fire(run, shooter, 1)
        self.assertEqual(shield.health, 600)
        self.assertEqual(fixture.health(run, behind).total, 80)
        for _ in range(1300):
            run.step({})
        self.assertFalse(shield.state_active(0))
        self.assertNotIn(owner.body, run.match.combat.orisa_shields)

    def test_barrage_debits_mesh_after_owner_moves_away(self):
        fixture, run, shooter, owner, behind, shield = self.deployed()
        x, y, z = shield.position
        owner.mover.position = [x + 15, y, z]
        behind.mover.position = [x, y, z - 3]
        owner.mover.body = behind.mover.body = None
        before = fixture.health(run, behind).total
        script = shooter.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        run.step({shooter: (-32768, 0, 32)})
        for _ in range(22):
            run.step({shooter: (-32768, 0, 0)})
        self.assertGreater(shield.health, 0)
        self.assertLess(shield.health, 600)
        self.assertEqual(fixture.health(run, behind).total, before)
