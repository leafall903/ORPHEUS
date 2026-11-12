"""Actual Ana and Soldier commands against the two live D.Va forms."""

import unittest
from dataclasses import replace
from unittest.mock import patch

import test_dva_delivery_created_body as created_body_tests
from test_game_combat import KANEZAKA_DM, Run, aim, dva_pilot_input

from ow174.game import combat, projectiles
from ow174.game.content import find_hero
from ow174.game.script.driver import ULT_CHARGE, ULT_COST


class DvaAnaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        created_body_tests.CreatedBodyTests.setUpClass()

    def scene(self, *, pilot_form=False, friendly=False, with_zarya=False):
        names = ("DVa", "Ana", "Soldier", "Zarya") if with_zarya else ("DVa", "Ana", "Soldier")
        run = Run(KANEZAKA_DM, names)
        pilot, caster, healer = run.players[:3]
        match = run.match
        match.bots.clear()
        match._pending_bot_scripts.clear()
        match.game_map = replace(match.game_map, free_for_all=False)
        pilot.team = healer.team = 0
        caster.team = 0 if friendly else 1
        self.assertTrue(match.switch_hero(pilot, find_hero("dva")))
        self.assertTrue(match.switch_hero(caster, find_hero("ana")))
        mech = pilot.controlled_mech
        x, y, z = mech.position
        if pilot_form:
            dva_pilot_input(pilot)
            x += 10
            pilot.mover.position[:] = (x, y, z)
            pilot.mover.body = None
        for player, position in ((caster, (x, y, z + 2.5)), (healer, (x + 2, y, z))):
            player.mover.position[:] = position
            player.mover.body = None
        for _ in range(100):
            run.step({})
        fight = match.combat
        target = fight.player_target(pilot)
        health = fight.vitals_of(target)
        health.damage(130 if pilot_form and friendly else 70 if pilot_form else 350)
        fight._sync_body_health(target.owner, health)
        return run, pilot, caster, healer, target, health

    def grenade(self, run, caster, target):
        yaw, pitch = aim(run.match.combat._eye(caster), target.feet)
        run.step({caster: (yaw, pitch, 16)})
        for _ in range(15):
            run.step({})

    def test_enemy_grenade_and_actual_field_across_expiry_in_either_form(self):
        for pilot_form in (False, True):
            with self.subTest(pilot_form=pilot_form):
                run, _, caster, healer, target, health = self.scene(pilot_form=pilot_form)
                fight = run.match.combat
                before = health.total
                self.grenade(run, caster, target)
                self.assertEqual(health.total, before - 60)
                (start, deadline), = fight.heal_modifiers[target.entity]["anti"]
                self.assertEqual(deadline - start, 250)
                run.step({healer: (0, 0, 16)})
                while run.frame < deadline - 3:
                    run.step({})
                self.assertEqual(health.total, before - 60)
                run.frame += 6
                run.match.update(run.frame * combat.TICK, run.frame, frames=False)
                expected = before - 60 + 4 * projectiles.HEAL_PER_SECOND * combat.TICK
                self.assertAlmostEqual(health.total, expected, places=5)
                self.assertNotIn(target.entity, fight.heal_modifiers)

    def test_friendly_grenade_heals_once_and_boosts_actual_field_in_either_form(self):
        for pilot_form in (False, True):
            with self.subTest(pilot_form=pilot_form):
                run, _, caster, healer, target, health = self.scene(pilot_form=pilot_form, friendly=True)
                fight = run.match.combat
                before = health.total
                self.grenade(run, caster, target)
                self.assertEqual(health.total, before + 100)
                (start, end), = fight.heal_modifiers[target.entity]["boost"]
                self.assertEqual(end - start, 246)
                run.step({healer: (0, 0, 16)})
                for _ in range(50):
                    previous = health.total
                    run.step({})
                    if health.total > previous:
                        break
                expected = projectiles.HEAL_PER_SECOND * combat.TICK * 1.5
                self.assertAlmostEqual(health.total - previous, expected)

    def test_status_on_detached_mech_does_not_move_to_restored_pilot(self):
        run, pilot, caster, healer, target, _ = self.scene()
        fight = run.match.combat
        self.grenade(run, caster, target)
        self.assertIn("anti", fight.heal_modifiers[target.entity])
        component = pilot.controlled_mech.body_script.component
        component.set(ULT_CHARGE, component.entity_var(ULT_COST).value())
        run.step({pilot: (0, 0, 32)})
        run.step({})
        self.assertIsNone(pilot.controlled_mech)
        self.assertTrue(target.owner.detached_bomb)
        restored = fight.player_target(pilot)
        health = fight.vitals_of(restored)
        health.damage(50)
        fight._sync_body_health(pilot, health)
        before = health.total
        run.step({healer: (0, 0, 16)})
        for _ in range(40):
            run.step({})
        self.assertGreater(health.total, before)
        self.assertNotIn(restored.entity, fight.heal_modifiers)
        self.assertNotIn(target.entity, fight.heal_modifiers)

    def test_pilot_status_does_not_move_to_replacement_mech(self):
        run, pilot, caster, healer, target, _ = self.scene(pilot_form=True)
        fight = run.match.combat
        self.grenade(run, caster, target)
        (_, deadline), = fight.heal_modifiers[target.entity]["anti"]
        caster.mover.position[0] += 25
        caster.mover.body = None
        # Keep the allied field caster outside the Call Mech placement capsule.
        healer.mover.position[0] -= 4
        healer.mover.body = None
        script = pilot.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        run.step({pilot: (0, 0, 32)})
        for _ in range(120):
            run.step({})
        new = fight.player_target(pilot)
        self.assertIsNotNone(pilot.controlled_mech)
        self.assertIs(new.owner, pilot.controlled_mech)
        self.assertNotEqual(new.entity, target.entity)
        self.assertLess(run.frame, deadline)
        health = fight.vitals_of(new)
        health.damage(100)
        fight._sync_body_health(new.owner, health)
        before = health.total
        run.step({healer: (0, 0, 16)})
        for _ in range(40):
            run.step({})
        self.assertLess(run.frame, deadline)
        self.assertGreater(health.total, before)
        self.assertNotIn(new.entity, fight.heal_modifiers)

    def test_controlled_matrix_blocks_actual_grenade_damage_and_antiheal(self):
        run, pilot, caster, _, target, health = self.scene()
        fight = run.match.combat
        x, y, z = target.feet
        caster.mover.position[:] = (x, y, z + 4)
        caster.mover.body = None
        for _ in range(5):
            run.step({pilot: (0, 0, 2)})
        before = health.total
        yaw, pitch = aim(fight._eye(caster), (x, y + 1.5, z))
        run.step({pilot: (0, 0, 2), caster: (yaw, pitch, 16)})
        self.assertEqual(len(fight.rockets), 1)
        grenade = fight.rockets[0]
        intercepts = []
        matrix_hit = fight._matrix_hit

        def capture(*args):
            hit = matrix_hit(*args)
            if hit is not None:
                intercepts.append((args[0], args[4]))
            return hit

        with patch.object(fight, "_matrix_hit", side_effect=capture):
            for _ in range(30):
                run.step({pilot: (0, 0, 2)})
        self.assertIn((caster, grenade.definition), intercepts)
        self.assertNotIn(grenade, fight.rockets)
        self.assertEqual(health.total, before, f"grenade 003 asset {grenade.definition:#x}")
        self.assertNotIn(target.entity, fight.heal_modifiers)
