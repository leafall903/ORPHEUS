"""Opt-in NPC target acquisition using actual bodies and Match commands."""

import unittest
from dataclasses import replace
from unittest.mock import patch

import test_game_dva_created_body as created
import test_game_dva_manual_npcs as manual_tests
from test_game_combat import dva_pilot_input
from test_game_mover import box, wall

from ow174.game import combat, npc, npc_control
from ow174.game.collision import World


class DvaNpcAcquisitionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        manual_tests.ManualIncomingTests.setUpClass.__func__(cls)

    def test_all_five_acquire_controlled_mech_and_reacquire_after_lethal_hit(self):
        damage = {"Trooper": 20, "Enforcer": 12, "Sniper": 100, "Assassin": 25, "Heavy Assault": 10}
        for name, amount in damage.items():
            with self.subTest(npc=name):
                run, pilot, other, _, mech = created.CreatedBodyTests().scenario()
                other.spawned = False
                match = run.match
                fight = match.combat
                x, y, z = mech.position
                distance = 2 if name == "Assassin" else 6
                bot = npc.spawn(match, self.by_name[name], (x, y, z - distance), team=1, autonomous=True)
                self.assertIsNone(bot.attack_target)
                fight.vitals[mech.entity] = combat.Vitals(mech.entity, 1)
                selected_mech = False
                for _ in range(300):
                    run.step({})
                    selected_mech |= bot.attack_target == pilot.entity
                    if pilot.controlled_mech is None:
                        break
                self.assertTrue(selected_mech)
                self.assertTrue(mech.dead)
                self.assertIsNone(pilot.controlled_mech)
                self.assertNotIn(mech.entity, fight.pins.values())
                live = fight.player_target(pilot)
                self.assertIs(live.owner, pilot)
                health = fight.vitals_of(live)
                before = health.total
                for _ in range(460):
                    run.step({})
                    if health.total < before:
                        break
                self.assertEqual(health.total, before - amount)
                self.assertEqual(bot.attack_target, pilot.entity)
                if name == "Assassin":
                    self.assertEqual(fight.pins, {bot.entity: pilot.body})

    def test_autonomous_npc_attacks_at_matrix_boundary(self):
        leads = {"Trooper": 18, "Enforcer": 50, "Sniper": 108, "Assassin": 0, "Heavy Assault": 20}
        for name, lead in leads.items():
            for deplete in ((False,) if name == "Assassin" else (False, True)):
                with self.subTest(npc=name, deplete=deplete):
                    self.check_autonomous_npc_matrix(name, lead, deplete=deplete)

    def check_autonomous_npc_matrix(self, name, lead, *, deplete):
        run, pilot, other, _, mech = created.CreatedBodyTests().scenario()
        other.spawned = False
        match = run.match
        fight = match.combat
        x, y, z = mech.position
        distance = 2 if name == "Assassin" else 6
        vitals = fight.vitals_of(fight.player_target(pilot))
        matrix = next(i for i in mech.body_script.component.instances.values() if i.graph.index == 0x02CC)
        for _ in range(100):
            run.step({})
        if name == "Assassin":
            run.step({pilot: (-32768, 0, 2)})
            self.assertTrue(matrix.state(3).active)
        bot = npc.spawn(match, self.by_name[name], (x, y, z - distance), team=1, autonomous=True)
        self.assertIsNone(bot.attack_target)
        for _ in range(lead):
            run.step({})
        self.assertEqual(vitals.total, 600)
        contacts = []
        matrix_hit = fight._matrix_hit

        def observe(shooter, *args):
            distance = matrix_hit(shooter, *args)
            if shooter is bot and distance is not None:
                contacts.append(distance)
            return distance

        with patch.object(fight, "_matrix_hit", side_effect=observe):
            for _ in range(30):
                run.step({pilot: (-32768, 0, 2)})
                if name == "Assassin" and fight.pins:
                    break
        self.assertEqual(bot.attack_target, pilot.entity)
        self.assertIs(fight.player_target(pilot).owner, mech)
        if name == "Assassin":
            self.assertEqual(fight.pins, {bot.entity: mech.entity})
            self.assertEqual(vitals.total, 580)
            self.assertFalse(matrix.state(3).active)
        else:
            self.assertTrue(contacts)
            self.assertTrue(matrix.state(3).active)
            self.assertEqual(vitals.total, 600)
            for _ in range(320):
                run.step({pilot: (-32768, 0, 2)} if deplete else {})
                if vitals.total < 600:
                    break
            self.assertFalse(matrix.state(3).active)
            self.assertLess(vitals.total, 600)
            if deplete:
                self.assertEqual(matrix.vars[87].value(), 0)
                self.assertEqual(bot.attack_target, pilot.entity)
                self.assertIs(pilot.controlled_mech, mech)

    def test_assassin_uses_capsule_contact_for_acquisition_and_pin_under_partial_cover(self):
        for pilot_form in (False, True):
            with self.subTest(pilot_form=pilot_form):
                run, pilot, other, _, mech = created.CreatedBodyTests().scenario()
                match = run.match
                fight = match.combat
                other.spawned = False
                x, y, z = mech.position
                if pilot_form:
                    dva_pilot_input(pilot)
                    x += 8
                    pilot.mover.position[:] = (x, y, z)
                    pilot.mover.body = None
                for _ in range(35):
                    run.step({})
                target = fight.player_target(pilot)
                x, y, z = target.feet
                floor = box(x - 20, x + 20, 0, y, z - 20, z + 20)
                solid = World(floor + wall(x - 4, z - 1, x + 4, z - 1))
                match.collision_world = solid
                bot = npc.spawn(match, self.by_name["Assassin"], (x, y, z - 2), team=1, autonomous=True)
                health = fight.vitals_of(target)
                initial = health.total
                for _ in range(12):
                    run.step({})
                self.assertIsNone(bot.attack_target)
                self.assertEqual(health.total, initial)
                # This narrow band cuts the eye ray at its midpoint.
                eye_midpoint = y + (combat.EYE + target.eye) * 0.5
                match.collision_world = World(
                    floor + wall(x - 4, z - 1, x + 4, z - 1, eye_midpoint - 0.015, eye_midpoint + 0.015)
                )
                origin = (x, y + combat.EYE, z - 2)
                a, b = target.segment()
                self.assertTrue(combat.capsule_visible(match.collision_world, origin, a, b, target.radius))
                for _ in range(25):
                    run.step({})
                    if health.total < initial:
                        break
                self.assertEqual(bot.attack_target, pilot.entity)
                self.assertEqual(fight.pins.get(bot.entity), target.entity)
                self.assertEqual(initial - health.total, 25 if pilot_form else 20)
                match.collision_world = solid
                pinned_health = health.total
                for _ in range(12):
                    run.step({})
                self.assertIsNone(bot.attack_target)
                self.assertNotIn(bot.entity, fight.pins)
                self.assertFalse(fight.is_stunned(target.owner))
                self.assertEqual(health.total, pinned_health)

    def test_all_five_require_hostility_and_visibility_to_attack_either_form(self):
        for name, definition in self.by_name.items():
            for pilot_form in (False, True):
                with self.subTest(npc=name, pilot_form=pilot_form):
                    self.check_incoming_acquisition_filters(name, definition, pilot_form=pilot_form)

    def check_incoming_acquisition_filters(self, name, definition, *, pilot_form):
        run, pilot, other, _, mech = created.CreatedBodyTests().scenario()
        other.spawned = False
        match = run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        if pilot_form:
            dva_pilot_input(pilot)
            pilot.mover.position[0] += 8
            pilot.mover.body = None
        for _ in range(35):
            run.step({})
        target = fight.player_target(pilot)
        self.assertIs(target.owner, pilot if pilot_form else mech)
        health = fight.vitals_of(target)
        before = dict(health.now)
        x, y, z = target.feet
        distance = 2 if name == "Assassin" else 6
        bot = npc.spawn(match, definition, (x, y, z - distance), team=target.team, autonomous=True)
        open_world = match.collision_world
        for covered in (False, True):
            if covered:
                bot.team = target.team + 1
                match.collision_world = World(
                    box(x - 20, x + 20, 0, y, z - 20, z + 20)
                    + wall(x - 8, z - 1, x + 8, z - 1, y, y + 6)
                )
            for _ in range(40):
                run.step({})
                self.assertIsNone(bot.attack_target)
                self.assertFalse(bot.attack_input)
            self.assertEqual(health.now, before)
            self.assertNotIn(bot.entity, fight.pins)
            self.assertFalse(fight.rockets)

        match.collision_world = open_world
        for _ in range(300):
            run.step({})
            if health.now != before:
                break
        self.assertEqual(bot.attack_target, pilot.entity)
        self.assertLess(health.total, sum(before.values()))
        if name == "Assassin":
            self.assertEqual(fight.pins.get(bot.entity), target.entity)

    def test_cover_and_allies_prevent_acquisition_then_stop_disables_it(self):
        run, pilot, other, _, mech = created.CreatedBodyTests().scenario()
        match = run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        pilot.team, other.team = 0, 1
        x, y, z = mech.position
        other.mover.position[:] = (x + 3, y, z - 5)
        other.mover.body = None
        open_world = match.collision_world
        match.collision_world = World(
            box(x - 20, x + 20, 0, y, z - 20, z + 20) + wall(x - 10, z - 2, x + 10, z - 2)
        )
        bot = npc.spawn(match, self.by_name["Trooper"], (x, y, z - 6), team=1, autonomous=True)
        health = fight.vitals[mech.entity]
        for _ in range(55):
            run.step({})
        self.assertIsNone(bot.attack_target)
        self.assertEqual(health.total, 600)
        match.collision_world = open_world
        run.step({})
        self.assertEqual(bot.attack_target, pilot.entity)
        # A closer enemy does not replace the visible controller.
        other.team = 0
        for _ in range(30):
            run.step({})
        self.assertEqual(bot.attack_target, pilot.entity)
        self.assertLess(health.total, 600)
        # The other eligible enemy acquires after the old input is released.
        pilot.spawned = False
        run.step({})
        self.assertIsNone(bot.attack_target)
        self.assertFalse(bot.attack_input)
        run.step({})
        self.assertEqual(bot.attack_target, other.entity)
        npc_control.stop(match)
        for _ in range(20):
            run.step({})
        self.assertIsNone(bot.attack_target)
        self.assertFalse(bot.firing)
