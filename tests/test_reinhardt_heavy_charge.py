"""Actual Heavy Assault 1408 input and static drive contacts."""

import os
import unittest
from dataclasses import replace
from unittest.mock import patch

import test_game_reinhardt as fixtures
import test_reinhardt_mei_wall as ice_wall
import test_reinhardt_npc_incoming as incoming
import test_reinhardt_orisa_supercharger as supercharger_fixture
import test_reinhardt_torbjorn_turret as turret_fixture
from test_game_combat import aim

from ow174.game import defenses, heavy_charge, npc, npc_control, zarya_barrier
from ow174.game.content import heroes


@unittest.skipUnless((fixtures.NPC_DATA / "pve_npcs_174.json").is_file()
                     and fixtures.NPC_MOVERS.is_file() and fixtures.BARRIER_DATA.is_file(),
                     "authorized NPC and barrier inputs required")
class HeavyChargeTests(unittest.TestCase):
    scenario = fixtures.TalonInteractionTests.scenario
    advance = fixtures.HammerTests.advance
    place = staticmethod(fixtures.HammerTests.place)
    setUp = incoming.ReinhardtNpcIncomingTests.setUp
    victim = incoming.ReinhardtNpcIncomingTests.victim
    health = fixtures.HammerTests.health
    tap = ice_wall.ReinhardtIceWallTests.tap

    def test_heavy_charge_hits_hostile_turret_before_reinhardt(self):
        device = turret_fixture.ReinhardtTurretTests.deploy(self)
        x, y, z = device.position
        self.rein.team = self.enemy.team
        self.place(self.rein, (x, y, z - 4))
        heavy = next(item for item in self.catalog.values() if item.name == "Heavy Assault")
        actor = npc.spawn(self.match, heavy, (x, y, z + 5), team=0, yaw=-32768)
        self.advance(4, buttons=0)
        self.rein.mover.yaw, self.rein.mover.pitch = aim(self.fight._eye(self.rein), (x, y + 1, z + 5))
        self.assertEqual(npc_control.start(self.match, self.rein, action="charge"), actor.entity)
        weapon = heavy_charge.instance_of(actor)
        for _ in range(95):
            self.advance(1, buttons=0)
            if device.health < 250:
                break
        self.assertEqual(device.health, 75)
        self.assertEqual(weapon.node_state(weapon.graph.node(102)).counter, 1)
        self.assertEqual(weapon.vars[6836].value(), ())
        self.assertEqual(weapon.vars[59].value(), 2)
        self.assertGreater(weapon.vars[61].value(), 14)
        self.assertEqual(self.victim().total, 500)
        self.assertFalse(self.fight.pinned(self.rein.body))
        self.advance(40, buttons=0)
        self.assertEqual(weapon.vars[59].value(), 0)

    @classmethod
    def setUpClass(cls):
        fixtures.TalonInteractionTests.setUpClass()
        cls.catalog = fixtures.TalonInteractionTests.catalog

    @classmethod
    def tearDownClass(cls):
        fixtures.TalonInteractionTests.tearDownClass()

    def engage(self, *, shield=False):
        definition = next(item for item in self.catalog.values() if item.name == "Heavy Assault")
        bot, _, _ = self.scenario(definition, (0, 1, 8))
        self.advance(25, buttons=2 if shield else 0)
        self.rein.mover.yaw, self.rein.mover.pitch = aim(self.match.combat._eye(self.rein), (0, 2, 8))
        self.assertEqual(npc_control.start(self.match, self.rein, action="charge"), bot.entity)
        self.assertEqual(npc_control.button(bot), 35)
        return bot, heavy_charge.instance_of(bot)

    def test_actual_windup_pin_transport_and_manual_cancel(self):
        bot, graph = self.engage(shield=True)
        self.advance(49, buttons=2)
        self.assertEqual(bot.position[2], 8)
        self.advance(32, buttons=2)
        self.assertTrue(graph.states[3].active)
        fight = self.match.combat
        self.assertTrue(fight.pinned(self.rein.body))
        self.assertLess(self.rein.mover.position[2], -0.3)
        self.assertFalse(fight.barriers[self.rein.body].active)
        self.assertEqual(self.victim().total, 500)
        npc_control.stop(self.match)
        self.assertFalse(fight.pinned(self.rein.body))
        end = bot.position
        self.advance(30, buttons=0)
        self.assertEqual(bot.position, end)
        self.assertEqual(self.victim().total, 500)

    def test_actual_cryo_ends_heavy_charge_through_the_3692_tracker(self):
        bot, weapon = self.engage_cryo_target()
        fight = self.match.combat
        before = self.health(self.enemy)
        with patch.object(fight, "_charge_damage", wraps=fight._charge_damage) as damage:
            for _ in range(70):
                self.advance(1, buttons=0)
                cooldown = weapon.vars.get(61)
                if cooldown is not None and cooldown.value() > 0:
                    break
        self.assertTrue(weapon.states[42].counter)
        self.assertFalse(weapon.states[3].active)
        self.assertGreater(bot.position[2], 4)
        self.assertEqual(self.health(self.enemy), before)
        self.assertEqual(self.victim().total, 500)
        self.assertFalse(fight.controlled(self.enemy.body))
        self.assertFalse(fight.pinned(self.rein.body))
        self.assertNotIn(bot.entity, fight.charge_contacts)
        self.assertEqual(weapon.vars[468].value(), ())
        self.assertFalse(damage.called)
        self.assertGreater(weapon.vars[61].value(), 14)

    def test_cryo_cancel_before_contact_restores_heavy_charge_capture(self):
        bot, weapon = self.engage_cryo_target()
        self.advance(32, buttons=0)
        self.run.step({self.enemy: (0, 0, 2)})
        self.advance(2, buttons=0)
        self.assertFalse(self.enemy.body_script.component.entity_var(3692).value())
        fight = self.match.combat
        for _ in range(70):
            self.advance(1, buttons=0)
            if fight.pinned(self.enemy.body):
                break
        self.assertTrue(weapon.states[3].active)
        self.assertTrue(fight.pinned(self.enemy.body))
        self.assertEqual(fight.charge_contacts[bot.entity].pinned, self.enemy.body)
        self.assertEqual(npc_control.stop(self.match), 1)
        self.assertFalse(fight.pinned(self.enemy.body))

    def engage_cryo_target(self):
        bot, weapon = self.engage()
        self.match.game_map = replace(self.match.game_map, free_for_all=False)
        self.assertTrue(self.match.switch_hero(self.enemy, heroes()[defenses.MEI]))
        self.rein.team = self.enemy.team = 0
        self.place(self.enemy, (0, 1, 4))
        self.advance(5, buttons=0)
        self.run.step({self.enemy: (0, 0, 8)})
        self.assertTrue(self.enemy.body_script.component.entity_var(3692).value())
        return bot, weapon

    @unittest.skipUnless(supercharger_fixture.PROFILE.is_file(), "authored Supercharger profile required")
    def test_actual_supercharger_takes_heavy_961_impact_before_reinhardt(self):
        actor, weapon, device = self.engage_supercharger_target()
        fight = self.match.combat
        for _ in range(100):
            self.advance(1, buttons=0)
            if device.health < 200:
                break
        self.assertEqual(device.health, 25)
        self.assertEqual(device.component.entity_var(253).value(), 25)
        self.assertEqual(weapon.node_state(weapon.graph.node(102)).counter, 1)
        self.assertFalse(weapon.states[3].active)
        self.assertGreater(weapon.vars[61].value(), 14)
        self.assertEqual(weapon.vars[6836].value(), ())
        self.assertNotIn(actor.entity, fight.charge_contacts)
        self.assertEqual(self.victim().total, 500)
        self.assertFalse(fight.controlled(self.rein.body))
        self.assertEqual(npc_control.stop(self.match), 1)
        self.advance(20, buttons=0)
        self.assertEqual(device.health, 25)

    @unittest.skipUnless(supercharger_fixture.PROFILE.is_file(), "authored Supercharger profile required")
    def test_heavy_charge_passes_allied_supercharger_and_captures_reinhardt(self):
        actor, weapon, device = self.engage_supercharger_target(allied=True)
        fight = self.match.combat
        for _ in range(100):
            self.advance(1, buttons=0)
            if fight.pinned(self.rein.body):
                break
        self.assertTrue(weapon.states[3].active)
        self.assertTrue(fight.pinned(self.rein.body))
        self.assertEqual(fight.charge_contacts[actor.entity].pinned, self.rein.body)
        self.assertEqual(device.health, 200)

    step = supercharger_fixture.ReinhardtSuperchargerTests.step
    cover_at = supercharger_fixture.ReinhardtSuperchargerTests.cover_at

    @unittest.skipUnless(supercharger_fixture.PROFILE.is_file(), "authored Supercharger profile required")
    def test_wall_blocks_heavy_961_impact_after_charge_input(self):
        actor, weapon, device = self.engage_supercharger_target()
        x, _, z = device.position
        for _ in range(70):
            self.advance(1, buttons=0)
            tracker = weapon.states.get(51)
            if tracker is not None and tracker.active:
                break
        self.assertTrue(weapon.states[51].active)
        self.cover_at(x, z + 1)
        for _ in range(100):
            self.advance(1, buttons=0)
            cooldown = weapon.vars.get(61)
            if cooldown is not None and cooldown.value() > 0:
                break
        self.assertEqual(weapon.node_state(weapon.graph.node(102)).counter, 0)
        self.assertGreater(actor.position[2], z + 1)
        self.assertEqual(device.health, 200)
        self.assertEqual(self.victim().total, 500)

    @unittest.skipUnless(supercharger_fixture.PROFILE.is_file(), "authored Supercharger profile required")
    def test_heavy_961_impact_destroys_a_hammer_damaged_supercharger_and_removes_aura(self):
        _, weapon, device = self.engage_supercharger_target(hammer_first=True)
        manager = self.fight.superchargers
        self.assertEqual(manager.scale(self.enemy), 1.5)
        for _ in range(100):
            self.advance(1, buttons=0)
            if device.entity not in manager.active:
                break
        self.assertEqual(device.health, 0)
        self.assertNotIn(device.entity, manager.active)
        self.assertNotIn((device.entity, self.enemy.body), manager.buffs)
        self.assertEqual(manager.scale(self.enemy), 1)
        self.assertEqual(weapon.node_state(weapon.graph.node(102)).counter, 1)
        self.assertEqual(self.victim().total, 500)

    def engage_supercharger_target(self, *, allied=False, hammer_first=False):
        device = supercharger_fixture.ReinhardtSuperchargerTests.deploy(self)
        self.match.game_map = replace(self.match.game_map, free_for_all=False)
        x, y, z = device.position
        if hammer_first:
            self.place(self.rein, (x, y, z - 2.5))
            self.advance(48, buttons=1)
            self.assertEqual(device.health, 115)
        self.rein.team = device.team
        self.place(self.rein, (x, y, z - 4))
        opposing_team = 1 - device.team
        actor = npc.spawn(self.match, next(item for item in self.catalog.values() if item.name == "Heavy Assault"),
                          (x, y, z + 5), team=device.team if allied else opposing_team, yaw=-32768)
        if allied:
            self.rein.team = opposing_team
        self.advance(25, buttons=0)
        self.assertTrue(device.component.entity_var(961).value())
        self.rein.mover.yaw, self.rein.mover.pitch = aim(
            self.fight._eye(self.rein), (x, y + 1, z + 5))
        self.assertEqual(npc_control.start(self.match, self.rein, action="charge"), actor.entity)
        return actor, heavy_charge.instance_of(actor), device

    def test_wall_impact_releases_pin_and_held_barrier_blocks_followup_primary(self):
        bot, graph = self.engage(shield=True)
        fixtures.charge_wall(self, z=-4)
        fight = self.match.combat
        barrier = fight.barriers[self.rein.body]
        self.assertTrue(barrier.active)
        self.advance(81, buttons=2)
        self.assertTrue(fight.pinned(self.rein.body))
        self.assertFalse(barrier.active)
        self.assertEqual(self.victim().total, 500)
        self.advance(29, buttons=2)
        self.assertEqual(self.victim().total, 205)
        self.assertFalse(fight.pinned(self.rein.body))
        self.assertNotIn(bot.entity, fight.charge_contacts)
        self.assertGreater(graph.vars[61].value(), 13)
        self.assertEqual(graph.vars[562].value(), ())
        self.assertIsNone(graph.vars[536].value())
        self.assertTrue(barrier.active)
        self.assertEqual(barrier.current, 1600)

        x, y, z = self.rein.mover.position
        bot.position = (x, y, z + 8)
        incoming.ReinhardtNpcIncomingTests.arm(self, bot)
        for _ in range(120):
            self.advance(1, buttons=2)
            if barrier.current < 1600:
                break
        self.assertEqual(barrier.current, 1595)
        self.assertEqual(self.victim().total, 205)
        self.assertFalse(fight.controlled(self.rein.body))

    def test_late_field_and_projected_barrier_during_actual_heavy_drive(self):
        for cast_during_capture in (False, True):
            with self.subTest(cast_during_capture=cast_during_capture):
                self.check_late_field_and_projected_barrier(cast_during_capture)

    def check_late_field_and_projected_barrier(self, cast_during_capture):
        bot, graph = self.engage()
        fight = self.match.combat
        self.match.game_map = replace(self.match.game_map, free_for_all=False)
        self.rein.team = self.other.team = 0
        bot.team = 1
        self.assertTrue(self.match.switch_hero(self.other, heroes()[zarya_barrier.HERO]))
        self.place(self.other, (3, 1, 0))
        fixtures.charge_wall(self, z=-4)
        barrier = fight.barriers[self.rein.body]
        vitals = self.victim()
        for _ in range(75):
            self.advance(1, buttons=0)
            if bot.entity in fight.charge_drives:
                break
        self.assertIn(bot.entity, fight.charge_drives)
        self.assertTrue(graph.states[3].active)
        self.assertFalse(fight.pinned(self.rein.body))
        self.assertFalse(barrier.active)
        self.assertNotIn(self.rein.body, fight.zarya_bubbles)

        if not cast_during_capture:
            self.cast_projected_at_reinhardt()
        self.advance(10, buttons=2)
        self.assertTrue(barrier.active)
        self.assertEqual(self.rein.body in fight.zarya_bubbles, not cast_during_capture)

        for _ in range(30):
            self.advance(1, buttons=2)
            if fight.pinned(self.rein.body):
                break
        self.assertTrue(fight.pinned(self.rein.body))
        self.assertFalse(barrier.active)
        self.assertEqual(barrier.current, 1600)
        self.assertEqual(vitals.total, 500)
        if cast_during_capture:
            self.assertNotIn(self.rein.body, fight.zarya_bubbles)
            self.cast_projected_at_reinhardt()
            self.assertTrue(fight.pinned(self.rein.body))
        bubble = fight.zarya_bubbles[self.rein.body]
        self.assertEqual(bubble.source, self.other.body)
        self.assertEqual(bubble.health, 200)

        for _ in range(35):
            self.advance(1, buttons=2)
            if bot.entity not in fight.charge_drives:
                break
        self.assertEqual(bubble.health, 0)
        self.assertEqual(vitals.total, 500)
        self.assertEqual(vitals.now[1], 200)
        self.assertAlmostEqual(self.other.body_script.component.entity_var(87).value(), 40, places=4)
        self.assertFalse(fight.pinned(self.rein.body))
        self.assertNotIn(bot.entity, fight.charge_contacts)
        self.assertEqual(graph.vars[562].value(), ())
        self.assertIsNone(graph.vars[536].value())
        self.assertGreater(graph.vars[61].value(), 13)
        self.advance(25, buttons=2)
        self.assertTrue(barrier.active)
        self.assertEqual(barrier.current, 1600)
        self.assertEqual(vitals.total, 500)

    def cast_projected_at_reinhardt(self):
        fight = self.match.combat
        x, y, z = self.rein.mover.position
        angles = aim(fight._eye(self.other), (x, y + 1.3, z))
        self.run.step({self.other: (*angles, 16), self.rein: (0, 0, 2)})

    def test_lethal_wall_impact_retires_capture_and_respawns_with_fresh_barrier(self):
        bot, graph = self.engage(shield=True)
        fight = self.match.combat
        old_body = self.rein.body
        vitals = self.victim()
        vitals.damage(vitals.total - 40, bypass_armour=True)
        fixtures.charge_wall(self, z=-4)
        self.advance(81, buttons=2)
        self.assertTrue(fight.pinned(old_body))
        self.assertEqual(vitals.total, 40)
        self.advance(29, buttons=2)
        self.assertTrue(vitals.dead)
        self.assertEqual(vitals.killer, bot.entity)
        self.assertFalse(fight.pinned(old_body))
        self.assertFalse(fight.barriers[old_body].active)
        self.assertNotIn(bot.entity, fight.charge_contacts)
        self.assertEqual(graph.vars[562].value(), ())
        self.assertIsNone(graph.vars[536].value())
        fixtures.TalonInteractionTests.assert_reinhardt_respawns_with_fresh_barrier(self, old_body)
        self.assertEqual(vitals.total, 0)

    def test_live_capture_releases_new_ally_before_transport_or_wall_damage(self):
        bot, graph = self.engage(shield=True)
        self.match.game_map = replace(self.match.game_map, free_for_all=False)
        self.rein.team, bot.team = 0, 1
        self.advance(81, buttons=2)
        fight = self.match.combat
        self.assertTrue(fight.pinned(self.rein.body))
        self.assertTrue(graph.states[3].active)
        captured_at = tuple(self.rein.mover.position)
        stopped_at = bot.position
        fixtures.charge_wall(self, z=-4)

        self.rein.team = bot.team
        self.advance(1, buttons=2)
        self.assertFalse(fight.pinned(self.rein.body))
        self.assertNotIn(bot.entity, fight.charge_contacts)
        self.assertFalse(graph.states[3].active)
        self.assertEqual(tuple(self.rein.mover.position), captured_at)
        self.assertEqual(bot.position, stopped_at)
        self.assertEqual(graph.vars[562].value(), ())
        self.advance(45, buttons=2)
        self.assertTrue(fight.barriers[self.rein.body].active)
        self.assertEqual(self.victim().total, 500)

    def test_allied_and_out_of_range_targets_deny_charge_until_eligible(self):
        for reason in ("ally", "range"):
            with self.subTest(reason=reason):
                actor, weapon = self.engage()
                self.match.game_map = replace(self.match.game_map, free_for_all=False)
                self.rein.team = actor.team if reason == "ally" else 1 - actor.team
                if reason == "range":
                    self.place(self.rein, (0, 1, -101))
                self.advance(90, buttons=0)
                self.assertFalse(actor.attack_input)
                self.assertEqual(weapon.state(3).counter, 0)
                self.assertEqual(actor.position, (0, 1, 8))
                self.assertEqual(self.victim().total, 500)

                self.rein.team = 1 - actor.team
                self.place(self.rein, (0, 1, 0))
                fight = self.match.combat
                for _ in range(150):
                    self.advance(1, buttons=0)
                    if fight.pinned(self.rein.body):
                        break
                self.assertTrue(fight.pinned(self.rein.body))
                self.assertEqual(self.victim().total, 500)
                self.assertEqual(npc_control.stop(self.match), 1)
                self.assertFalse(fight.pinned(self.rein.body))

    def test_wall_before_target_stops_drive_without_damage(self):
        bot, _ = self.engage()
        self.advance(55, buttons=0)
        fixtures.charge_wall(self, z=3)
        self.advance(65, buttons=0)
        self.assertEqual(self.victim().total, 500)
        self.assertGreater(bot.position[2], 3)
        self.assertNotIn(bot.entity, self.match.combat.charge_contacts)

    def place_ice_wall(self, z):
        self.assertTrue(self.match.switch_hero(self.enemy, heroes()[defenses.MEI]))
        self.place(self.enemy, (0, 1, z + 4))
        centre = ice_wall.ReinhardtIceWallTests.wall(self, z)
        self.place(self.enemy, (10, 1, 0))
        return centre

    def test_live_ice_wall_stops_heavy_before_capture(self):
        bot, graph = self.engage()
        centre = self.place_ice_wall(3)
        fight = self.match.combat
        for _ in range(120):
            self.advance(1, buttons=0)
            self.assertFalse(fight.pinned(self.rein.body))
            if graph.vars[61].value() > 0 and not graph.states[3].active:
                break
        self.assertFalse(graph.states[3].active)
        self.assertGreater(bot.position[2], centre.feet[2] + centre.depth)
        self.assertLess(bot.position[2], centre.feet[2] + centre.depth + 2)
        self.assertEqual(self.victim().total, 500)
        self.assertNotIn(bot.entity, fight.charge_contacts)
        self.assertEqual(centre.health, 225)
        self.assertTrue(all(piece.health == 400 for piece in fight.walls.pieces.values() if piece is not centre))
        self.assertEqual(graph.node_state(graph.graph.node(102)).counter, 1)
        self.assertEqual(graph.vars[6836].value(), ())

    def test_heavy_charge_stops_at_friendly_ice_wall_without_damaging_it(self):
        bot, graph = self.engage()
        self.match.game_map = replace(self.match.game_map, free_for_all=False)
        self.enemy.team = bot.team
        centre = self.place_ice_wall(3)
        for _ in range(120):
            self.advance(1, buttons=0)
            if graph.vars[61].value() > 0 and not graph.states[3].active:
                break
        self.assertFalse(graph.states[3].active)
        self.assertGreater(bot.position[2], centre.feet[2] + centre.depth)
        self.assertEqual(self.victim().total, 500)
        self.assertTrue(all(piece.health == 400 for piece in self.match.combat.walls.pieces.values()))

    def test_captured_reinhardt_impacts_a_live_ice_wall(self):
        bot, graph = self.engage(shield=True)
        centre = self.place_ice_wall(-4)
        fight = self.match.combat
        self.advance(76, buttons=2)
        self.assertTrue(fight.pinned(self.rein.body))
        self.assertEqual(self.victim().total, 500)
        for _ in range(35):
            self.advance(1, buttons=2)
            if not fight.pinned(self.rein.body):
                break
        self.assertEqual(self.victim().total, 205)
        self.assertEqual(centre.health, 400)
        self.assertGreater(self.rein.mover.position[2], centre.feet[2] + centre.depth)
        self.assertGreater(bot.position[2], self.rein.mover.position[2])
        self.assertFalse(fight.pinned(self.rein.body))
        self.assertNotIn(bot.entity, fight.charge_contacts)
        self.assertGreater(graph.vars[61].value(), 13)
        self.assertEqual(graph.vars[562].value(), ())
        self.advance(15, buttons=2)
        self.assertTrue(fight.barriers[self.rein.body].active)
        self.assertEqual(self.victim().total, 205)

    def test_side_contact_applies_50_once_and_cannot_replace_centre_capture(self):
        bot, _ = self.engage()
        self.place(self.other, (2.2, 1, 4))
        self.advance(82, buttons=0)
        fight = self.match.combat
        self.assertEqual(self.health(self.other), 150)
        self.assertFalse(fight.pinned(self.other.body))
        self.assertEqual(fight.charge_contacts[bot.entity].pinned, self.rein.body)

    def test_side_contact_hits_replacement_with_the_same_body_id(self):
        bot, _ = self.engage()
        fight = self.match.combat
        old = self.other.body
        self.place(self.other, (2.2, 1, 4))
        for _ in range(90):
            self.advance(1, buttons=0)
            if self.health(self.other) == 150:
                break
        self.assertEqual(self.health(self.other), 150)
        contact = fight.charge_contacts[bot.entity]
        self.assertFalse(fight.pinned(old))

        self.assertTrue(self.match.switch_hero(self.other, self.other.hero))
        self.assertNotEqual(self.other.body, old)
        with patch.object(self.other.client, 'body_id_available', side_effect=lambda entity: entity == old):
            self.assertTrue(self.match.switch_hero(self.other, self.other.hero))
        self.assertEqual(self.other.body, old)
        self.place(self.other, (2.2, 1, -2))
        self.assertEqual(self.health(self.other), 200)
        for _ in range(45):
            self.advance(1, buttons=0)
            if self.health(self.other) == 150:
                break
        self.assertIs(fight.charge_contacts.get(bot.entity), contact)
        self.assertEqual(self.health(self.other), 150)
        self.assertFalse(fight.pinned(old))
        self.assertEqual(contact.pinned, self.rein.body)
        self.advance(20, buttons=0)
        self.assertEqual(self.health(self.other), 150)

    def test_target_reassignment_and_body_reuse_cancel_old_charge_windup(self):
        for retarget in (False, True):
            with self.subTest(retarget=retarget):
                bot, graph = self.engage()
                self.advance(26, buttons=0)
                fight = self.match.combat
                self.assertTrue(bot.attack_input)
                self.assertEqual(bot.position[2], 8)
                self.assertFalse(graph.states.get(3) and graph.states[3].active)
                old_health = self.victim()
                replacement = self.other if retarget else self.rein
                old_id = replacement.body
                self.assertTrue(self.match.switch_hero(replacement, self.rein.hero))
                if retarget:
                    self.place(self.rein, (10, 1, 0))
                    self.place(replacement, (0, 1, 0))
                    replacement.mover.yaw, replacement.mover.pitch = aim(
                        fight._eye(replacement), (0, 2, 8))
                    self.assertEqual(npc_control.start(self.match, replacement, action="charge"), bot.entity)
                else:
                    with patch.object(replacement.client, 'body_id_available', side_effect=lambda body: body == old_id):
                        self.assertTrue(self.match.switch_hero(replacement, replacement.hero))
                    self.assertEqual(replacement.body, old_id)
                    self.place(replacement, (0, 1, 0))
                new_health = fight.vitals_of(fight._target_entity(replacement.body))
                self.advance(49, buttons=0)
                self.assertEqual(bot.position[2], 8)
                self.assertNotIn(bot.entity, fight.charge_drives)
                self.assertFalse(fight.pinned(replacement.body))
                self.assertGreater(graph.vars[61].value(), 0)
                for _ in range(960):
                    if graph.vars[61].value() <= 0:
                        break
                    self.assertFalse(bot.attack_input)
                    self.assertEqual(bot.position[2], 8)
                    self.advance(1, buttons=0)
                self.assertLessEqual(graph.vars[61].value(), 0)
                self.advance(48, buttons=0)
                self.assertEqual(bot.position[2], 8)
                self.assertFalse(fight.pinned(replacement.body))
                for _ in range(45):
                    self.advance(1, buttons=0)
                    if fight.pinned(replacement.body):
                        break
                self.assertTrue(fight.pinned(replacement.body))
                self.assertEqual(fight.charge_contacts[bot.entity].pinned, replacement.body)
                self.assertEqual(new_health.total, 500)
                self.assertEqual(old_health.total, 500)

    def test_manual_retarget_releases_live_capture_without_impact(self):
        bot, graph = self.engage()
        self.advance(75, buttons=0)
        fight = self.match.combat
        old_health = self.victim()
        self.assertTrue(fight.pinned(self.rein.body))
        stopped_at = bot.position
        self.assertTrue(self.match.switch_hero(self.other, self.rein.hero))
        self.place(self.other, (3, 1, 6))
        self.other.mover.yaw, self.other.mover.pitch = aim(
            fight._eye(self.other), (bot.position[0], bot.position[1] + 1, bot.position[2]))
        self.assertEqual(npc_control.start(self.match, self.other, action="charge"), bot.entity)
        self.assertFalse(fight.pinned(self.rein.body))
        self.assertNotIn(bot.entity, fight.charge_contacts)
        self.assertFalse(graph.states[3].active)
        self.assertEqual(graph.vars[562].value(), ())
        self.assertGreater(graph.vars[61].value(), 14)
        self.advance(30, buttons=0)
        self.assertEqual(bot.position, stopped_at)
        self.assertEqual(old_health.total, 500)
        self.assertEqual(self.health(self.other), 500)
        self.assertFalse(fight.pinned(self.other.body))

    def test_actual_earthshatter_cancels_windup_without_resuming_old_drive(self):
        bot, graph = self.engage()
        script = self.rein.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        self.advance(1, buttons=32)
        self.advance(67, buttons=0)
        self.assertTrue(self.match.combat.controlled(bot.entity))
        self.assertEqual(bot.position[2], 8)
        self.assertFalse(graph.states.get(3) and graph.states[3].active)
        self.assertNotIn(bot.entity, self.match.combat.charge_drives)
        npc_control.stop(self.match)
        self.advance(165, buttons=0)
        self.assertFalse(self.match.combat.controlled(bot.entity))
        self.assertNotIn(bot.entity, self.match.combat.charge_contacts)
        self.assertEqual(bot.position[2], 8)
        self.assertEqual(self.victim().total, 500)

    def test_open_ground_timeout_does_not_apply_wall_damage(self):
        bot, graph = self.engage()
        self.advance(75, buttons=0)
        self.assertTrue(self.match.combat.pinned(self.rein.body))
        self.advance(230, buttons=0)
        self.assertFalse(graph.states[3].active)
        self.assertFalse(self.match.combat.pinned(self.rein.body))
        self.assertNotIn(bot.entity, self.match.combat.charge_contacts)
        self.assertEqual(self.victim().total, 500)

    def test_lethal_rifle_hit_releases_heavy_capture_without_late_damage(self):
        actor, weapon = self.engage()
        fight = self.match.combat
        target = fight._target_entity(actor.entity)
        health = fight.vitals_of(target)
        fight._damage(self.enemy, target, health.total - 10, False, None, self.run.frame)
        self.advance(75, buttons=0)
        self.assertTrue(weapon.state(3).active)
        self.assertTrue(fight.pinned(self.rein.body))
        self.assertEqual(self.victim().total, 500)

        x, y, z = actor.position
        self.place(self.enemy, (x + 5, y, z))
        angles = aim(fight._eye(self.enemy), (x, y + 1.3, z))
        self.run.step({self.enemy: (*angles, 1)})
        self.assertTrue(actor.dead)
        self.assertEqual(health.killer, self.enemy.body)
        self.assertFalse(weapon.state(3).active)
        self.assertFalse(fight.pinned(self.rein.body))
        self.assertNotIn(actor.entity, fight.charge_contacts)
        self.assertNotIn(actor.entity, fight.charge_drives)
        end = actor.position
        self.advance(90, buttons=0)
        self.assertEqual(actor.position, end)
        self.assertEqual(self.victim().total, 500)

    def test_replacement_or_npc_removal_releases_live_capture(self):
        for remove in (False, True):
            with self.subTest(remove=remove):
                bot, _ = self.engage()
                self.advance(75, buttons=0)
                fight = self.match.combat
                old = self.rein.body
                self.assertTrue(fight.pinned(old))
                if remove:
                    self.assertIn(bot.entity, npc.remove_spawned(self.match))
                else:
                    hero = heroes()[self.rein.hero.guid]
                    self.assertTrue(self.match.switch_hero(self.rein, hero))
                self.assertFalse(fight.pinned(old))
                self.advance(4, buttons=0)
                self.assertNotIn(bot.entity, fight.charge_contacts)
                self.assertEqual(self.victim().total, 500)

    def test_heavy_counter_catches_an_oblique_opposing_reinhardt_windup(self):
        bot, graph = self.engage()
        fight = self.match.combat
        before = fight.vitals_of(fight._target_entity(bot.entity)).total
        self.advance(64, buttons=0)
        self.run.step({self.rein: (14500, 0, 8)})
        for _ in range(18):
            self.run.step({self.rein: (14500, 0, 0)})
            if fight.controlled(bot.entity) and fight.controlled(self.rein.body):
                break
        self.assertEqual(fight.control_deadline(bot.entity) - fight.tick, 125)
        self.assertEqual(fight.control_deadline(self.rein.body) - fight.tick, 125)
        self.assertIn((self.rein.body, bot.entity, "charge_knockdown"), fight.controls)
        self.assertEqual(next(iter(fight.controls)), (bot.entity, self.rein.body, "charge_knockdown"))
        self.assertEqual(self.victim().total, 500)
        self.assertEqual(fight.vitals_of(fight._target_entity(bot.entity)).total, before)
        self.assertFalse(fight.charge_contacts)
        self.assertFalse(fight.charge_drives)
        self.assertGreater(graph.vars[61].value(), 13)
        rein_pose, heavy_pose = tuple(self.rein.mover.position), bot.position
        npc_control.stop(self.match)
        self.advance(124, buttons=0)
        self.assertTrue(fight.controlled(bot.entity))
        self.assertTrue(fight.controlled(self.rein.body))
        self.advance(1, buttons=0)
        self.assertFalse(fight.controlled(bot.entity))
        self.assertFalse(fight.controlled(self.rein.body))
        self.advance(20, buttons=0)
        self.assertFalse(fight.charge_contacts)
        self.assertFalse(fight.charge_drives)
        self.assertEqual(tuple(self.rein.mover.position), rein_pose)
        self.assertEqual(bot.position, heavy_pose)

    def test_early_reinhardt_contact_uses_heavy_6601_impact(self):
        bot, _ = self.engage()
        fight = self.match.combat
        instance = next(i for i in self.rein.body_script.component.instances.values()
                        if i.graph.index == 0x7D)
        self.advance(1, buttons=8)
        for _ in range(70):
            self.advance(1, buttons=0)
            if instance.find_var(0, 61).value() > 0:
                break
        self.assertGreater(instance.find_var(0, 61).value(), 9)
        self.assertFalse(fight.controlled(bot.entity))
        self.assertFalse(fight.controlled(self.rein.body))
        self.assertNotIn(self.rein.body, fight.charge_contacts)
        self.assertEqual(self.victim().total, 500)

    def test_wall_separates_two_actual_windups(self):
        bot, _ = self.engage()
        self.advance(1, buttons=8)
        self.advance(10, buttons=0)
        fixtures.charge_wall(self, z=4)
        self.advance(90, buttons=0)
        fight = self.match.combat
        self.assertFalse(fight.controlled(bot.entity))
        self.assertFalse(fight.controlled(self.rein.body))
        self.assertGreater(bot.position[2], 4)
        self.assertLess(self.rein.mover.position[2], 4)
        self.assertEqual(self.victim().total, 500)
