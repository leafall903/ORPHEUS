"""Actual Ana attacks against a confirmed Photon Barrier."""
import os
import unittest

import test_game_ana as fixture
import test_game_ana_orisa_barrier as helpers

from ow174.game import stats, symmetra_barrier
from ow174.game.content import heroes
from ow174.game.script.driver import ULT_CHARGE, ULT_COST


@unittest.skipUnless(os.environ.get("ORPHEUS_SYMMETRA_COLLISION"), "verified Photon Barrier mesh required")
class AnaPhotonBarrierTests(unittest.TestCase):
    def deploy(self, *, ally=False):
        run, ana, symmetra, _ = fixture.AnaRifleTests().setup_fight(enemy=not ally, distance=8)
        self.assertTrue(run.match.switch_hero(symmetra, heroes()[symmetra_barrier.HERO]))
        x, y, z = ana.mover.position
        helpers.AnaOrisaBarrierTests.place(symmetra, (x, y, z + 8))
        for _ in range(35):
            run.step({symmetra: (-32768, 0, 0)})
        script = symmetra.body_script
        cost = script.component.entity_var(ULT_COST).value()
        script.set_ult_charge(cost)
        run.step({symmetra: (-32768, 0, 32)})
        for _ in range(15):
            run.step({symmetra: (-32768, 0, 0)})
        manager = run.match.combat.symmetra_barriers
        self.assertFalse(manager.actors)
        self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), cost)
        run.step({symmetra: (-32768, 0, 1)})
        for _ in range(35):
            run.step({symmetra: (-32768, 0, 0)})
            if manager.actors:
                break
        [wall] = manager.actors.values()
        self.assertEqual(wall.health, 4000)
        self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)
        self.assertTrue(any(i.graph.index == 0x156C for i in wall.component.instances.values()))
        return run, ana, symmetra, wall

    def test_ana_projectiles_stop_at_enemy_photon_barrier(self):
        for button, scoped, amount in ((1, False, 70), (1, True, 70), (8, False, 5), (16, False, 50)):
            with self.subTest(button=button, scoped=scoped):
                arena = self.deploy()
                run, ana, symmetra, wall = arena
                vitals = helpers.AnaOrisaBarrierTests.vitals(run, symmetra)
                before = vitals.total
                helpers.AnaOrisaBarrierTests.fire(self, arena, button, scoped=scoped)
                self.assertEqual(wall.health, 4000 - amount)
                self.assertEqual(vitals.total, before)
                self.assertFalse(run.match.combat.is_asleep(symmetra))
                self.assertEqual(vitals.healing_block_until, 0)
                self.assertEqual(ana.stats.value(stats.HERO_DAMAGE), 0)

    def test_breaking_grenade_keeps_wall_occlusion_for_that_explosion(self):
        arena = self.deploy()
        run, _, symmetra, wall = arena
        vitals = helpers.AnaOrisaBarrierTests.vitals(run, symmetra)
        before = vitals.total
        wall.health = 40
        helpers.AnaOrisaBarrierTests.fire(self, arena, 16)
        self.assertEqual(wall.health, 0)
        self.assertFalse(run.match.combat.symmetra_barriers.actors)
        self.assertEqual(vitals.total, before)
        self.assertEqual(vitals.healing_block_until, 0)

    def test_friendly_wall_passes_rifle_healing_and_nano(self):
        arena = self.deploy(ally=True)
        run, ana, symmetra, wall = arena
        vitals = helpers.AnaOrisaBarrierTests.vitals(run, symmetra)
        vitals.damage(100, bypass_armour=True)
        before = vitals.total
        helpers.AnaOrisaBarrierTests.fire(self, arena, 1)
        self.assertEqual(vitals.total, before + 70)
        ana.body_script.set_ult_charge(ana.body_script.component.vars[338].value())
        helpers.AnaOrisaBarrierTests.fire(self, arena, 32)
        self.assertGreater(vitals.nano_until, run.match.combat.now)
        self.assertEqual(wall.health, 4000)

    def test_friendly_wall_passes_grenade_healing_and_amplification(self):
        arena = self.deploy(ally=True)
        run, ana, symmetra, wall = arena
        vitals = helpers.AnaOrisaBarrierTests.vitals(run, symmetra)
        vitals.damage(120, bypass_armour=True)
        before = vitals.total
        helpers.AnaOrisaBarrierTests.fire(self, arena, 16)
        self.assertEqual(vitals.total, before + 100)
        self.assertGreater(vitals.healing_boost_until, run.match.combat.now)
        self.assertEqual(vitals.healing_block_until, 0)
        self.assertEqual(wall.health, 4000)
        self.assertEqual(ana.stats.value(stats.HEALING), 100)

    def test_actual_melee_hits_wall_and_vm_exports(self):
        from test_game_combat import aim

        from ow174.game.body_vm_snapshot import export_component_vm

        run, ana, symmetra, wall = self.deploy()
        x, _, z = wall.position
        y = symmetra.mover.position[1]
        helpers.AnaOrisaBarrierTests.place(ana, (x, y, z - 1.5))
        run.step({})
        direction = aim(run.match.combat._eye(ana), (x, y + 1.4, z))
        run.step({ana: (*direction, 0x800)})
        for _ in range(35):
            run.step({})
        self.assertEqual(wall.health, 3970)
        self.assertEqual(helpers.AnaOrisaBarrierTests.vitals(run, symmetra).total, 225)
        self.assertTrue(export_component_vm(wall.component))
