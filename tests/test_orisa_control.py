"""Real Flashbang/Fortify/weapon/ultimate inputs under scoped control policies."""

import os
import unittest

import test_mccree_flashbang as flash_fixture
import test_orisa_barrier as barrier_fixture

from ow174.game.collision import World
from ow174.game.commands import FIRE
from ow174.game.content import find_hero


class OrisaControlTests(unittest.TestCase):
    def arena(self):
        helper = flash_fixture.FlashbangTests()
        run, caster, player = helper.arena(distance=3)
        run.match.switch_hero(player, find_hero("Orisa"))
        floor = caster.mover.position[1]
        barrier_fixture.BarrierTests.place(player, (0, floor, 3))
        player.body_script.ult_seconds = 0
        run.step({})
        return helper, run, caster, player

    @staticmethod
    def immune(player):
        value = player.body_script.component.entity_var(9161, create=False)
        return value is not None and bool(value.value())

    def impact(self, helper, run, caster, player, *, buttons=0, forward=0):
        before = helper.health(run, player)
        helper.step(run, caster, player, cast=16, target_buttons=buttons, forward=forward)
        for _ in range(50):
            if helper.health(run, player) < before:
                return before - helper.health(run, player)
            helper.step(run, caster, player, target_buttons=buttons, forward=forward)
        self.fail("actual Flashbang command did not damage Orisa")

    def test_active_fortify_takes_reduced_flash_damage_without_losing_control_or_fire(self):
        helper, run, caster, player = self.arena()
        helper.step(run, caster, player, target_buttons=8)
        self.assertTrue(self.immune(player))
        self.assertAlmostEqual(self.impact(helper, run, caster, player, buttons=FIRE), 10, places=4)
        self.assertFalse(run.match.combat.is_stunned(player))
        self.assertTrue(self.immune(player))
        before = helper.health(run, caster)
        position = tuple(player.mover.position)
        for _ in range(12):
            helper.step(run, caster, player, target_buttons=FIRE, forward=127)
            self.assertFalse(run.match.combat.is_stunned(player))
        self.assertNotEqual(tuple(player.mover.position), position)
        self.assertLess(helper.health(run, caster), before)

    def test_fortify_expiry_restores_flashbang_vulnerability(self):
        helper, run, caster, player = self.arena()
        helper.step(run, caster, player, target_buttons=8)
        for _ in range(270):
            helper.step(run, caster, player)
        self.assertFalse(self.immune(player))
        self.assertAlmostEqual(self.impact(helper, run, caster, player), 20)
        self.assertTrue(run.match.combat.is_stunned(player))

    def test_curved_flashbang_expires_beside_orisa_and_checks_blast_visibility(self):
        for fortified, covered, loss in ((False, False, 20), (True, False, 10), (False, True, 0)):
            with self.subTest(fortified=fortified, covered=covered):
                helper, run, caster, player = self.arena()
                fight = run.match.combat
                floor = caster.mover.position[1]
                barrier_fixture.BarrierTests.place(player, (1.3, floor, 7))
                if covered:
                    match = run.match
                    match.collision_world = World([
                        (-20, floor, -20, -20, floor, 20, 20, floor, 20),
                        (-20, floor, -20, 20, floor, 20, 20, floor, -20),
                        (.65, floor, 5, .65, floor, 9, .65, floor + 4, 9),
                        (.65, floor, 5, .65, floor + 4, 9, .65, floor + 4, 5),
                        (.65, floor, 5, .65, floor + 4, 9, .65, floor, 9),
                        (.65, floor, 5, .65, floor + 4, 5, .65, floor + 4, 9),
                    ], name="Flashbang side wall")
                    for participant in run.players:
                        participant.mover.use(world=match.collision_world)
                run.step({player: (0, 0, 8)} if fortified else {})
                before = helper.health(run, player)
                run.step({caster: (0, 0, 16)})
                rocket = None
                for _ in range(50):
                    rocket = next((r for r in fight.rockets if r.source_graph == 0x01E9), None)
                    if rocket is not None:
                        break
                    run.step({})
                self.assertIsNotNone(rocket)
                self.assertEqual(rocket.gravity, 20)
                self.assertGreater(rocket.launch_direction[1], 0)
                for _ in range(25):
                    run.step({})
                    if rocket.ended:
                        break
                self.assertEqual(rocket.ended, "expired")
                self.assertLess(rocket.velocity[1], 0)
                self.assertAlmostEqual(before - helper.health(run, player), loss, places=5)
                self.assertEqual(fight.is_stunned(player), not (fortified or covered))

    def test_stunned_fortify_tap_is_refused_and_fresh_input_after_expiry_works(self):
        helper, run, caster, player = self.arena()
        self.assertAlmostEqual(self.impact(helper, run, caster, player), 20)
        fight = run.match.combat
        end = fight.stuns[player.body]
        helper.step(run, caster, player, target_buttons=8)
        self.assertFalse(self.immune(player))
        while run.frame < end:
            helper.step(run, caster, player)
            self.assertFalse(self.immune(player))
        helper.step(run, caster, player, target_buttons=8)
        self.assertTrue(self.immune(player))

    def test_unprotected_stun_suppresses_fire_and_movement_then_recovers(self):
        helper, run, caster, player = self.arena()
        self.impact(helper, run, caster, player)
        fight = run.match.combat
        end = fight.stuns[player.body]
        before = helper.health(run, caster)
        position = tuple(player.mover.position)
        while run.frame < end - 1:
            helper.step(run, caster, player, target_buttons=FIRE, forward=127)
            self.assertEqual(tuple(player.mover.position), position)
            self.assertEqual(helper.health(run, caster), before)
        for _ in range(15):
            helper.step(run, caster, player, target_buttons=FIRE, forward=127)
        self.assertNotEqual(tuple(player.mover.position), position)
        self.assertLess(helper.health(run, caster), before)

    def test_flashbang_interrupts_supercharger_windup_and_does_not_replay_stale_press(self):
        helper, run, caster, player = self.arena()
        script = player.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        volley = next(i for i in script.component.instances.values() if i.graph.index == 0xCF5).state(10)
        self.impact(helper, run, caster, player, buttons=32)
        self.assertTrue(run.match.combat.is_stunned(player))
        for _ in range(100):
            helper.step(run, caster, player)
        self.assertEqual(volley.reported, 0)
        self.assertFalse(run.match.combat.orisa.superchargers.active)
        self.assertFalse(any(r.source_graph == 0xCF5 for r in run.match.combat.ability_impacts))
        self.assertEqual(script.component.entity_var(342).value(), 0)  # original spent charge stays spent

    @unittest.skipUnless(os.environ.get("ORPHEUS_ORISA_SUPERCHARGER"), "authored device supplement required")
    def test_canceled_ultimate_refuses_empty_charge_then_accepts_new_eligible_input(self):
        helper, run, caster, player = self.arena()
        script = player.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        self.impact(helper, run, caster, player, buttons=32)
        for _ in range(100):
            helper.step(run, caster, player)
        helper.step(run, caster, player, target_buttons=32)
        for _ in range(45):
            helper.step(run, caster, player)
        self.assertFalse(run.match.combat.orisa.superchargers.active)
        script.set_ult_charge(script.component.entity_var(338).value())
        helper.step(run, caster, player, target_buttons=32)
        for _ in range(70):
            helper.step(run, caster, player)
            if run.match.combat.orisa.superchargers.active:
                break
        self.assertTrue(run.match.combat.orisa.superchargers.active)

    @unittest.skipUnless(os.environ.get("ORPHEUS_ORISA_SUPERCHARGER"), "authored device supplement required")
    def test_fortify_protects_supercharger_windup_from_same_flashbang(self):
        helper, run, caster, player = self.arena()
        helper.step(run, caster, player, target_buttons=8)
        self.assertTrue(self.immune(player))
        script = player.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        self.assertAlmostEqual(self.impact(helper, run, caster, player, buttons=32), 10, places=4)
        self.assertFalse(run.match.combat.is_stunned(player))
        for _ in range(70):
            helper.step(run, caster, player)
            if run.match.combat.orisa.superchargers.active:
                break
        self.assertTrue(run.match.combat.orisa.superchargers.active)

    @unittest.skipUnless(os.environ.get("ORPHEUS_ORISA_SUPERCHARGER"), "authored device supplement required")
    def test_flashbang_after_deployment_stuns_owner_without_retiring_device(self):
        helper, run, caster, player = self.arena()
        script = player.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        helper.step(run, caster, player, target_buttons=32)
        manager = run.match.combat.orisa.superchargers
        for _ in range(70):
            helper.step(run, caster, player)
            if manager.active:
                break
        device = next(iter(manager.active.values()))
        deadline = device.until
        self.impact(helper, run, caster, player)
        self.assertTrue(run.match.combat.is_stunned(player))
        self.assertIn(device.entity, manager.active)
        self.assertEqual(device.until, deadline)

    def test_body_switch_clears_control_and_does_not_borrow_old_fortify_state(self):
        helper, run, caster, player = self.arena()
        self.impact(helper, run, caster, player)
        retired = player.body
        self.assertTrue(run.match.combat.is_stunned(player))
        run.match.switch_hero(player, find_hero("Orisa"))
        self.assertNotEqual(player.body, retired)
        self.assertNotIn(retired, run.match.combat.stuns)
        self.assertFalse(run.match.combat.is_stunned(player))
        self.assertFalse(self.immune(player))


if __name__ == "__main__":
    unittest.main()
