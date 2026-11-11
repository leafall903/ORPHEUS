"""Actual Heavy and Trooper melee inputs against Reinhardt."""

import unittest
from dataclasses import replace
from unittest.mock import patch

import test_game_reinhardt as fixtures
import test_reinhardt_mei_wall as ice_wall
import test_reinhardt_npc_incoming as incoming
from test_game_combat import aim

from ow174.game import defenses, deflection, npc_control, npc_melee
from ow174.game.content import heroes
from ow174.game.world import ARMOUR_POOL


@unittest.skipUnless((fixtures.NPC_DATA / "pve_npcs_174.json").is_file()
                     and fixtures.NPC_MOVERS.is_file() and fixtures.BARRIER_DATA.is_file(),
                     "authorized NPC and barrier inputs required")
class ReinhardtNpcMeleeTests(unittest.TestCase):
    scenario = fixtures.TalonInteractionTests.scenario
    advance = fixtures.HammerTests.advance
    place = staticmethod(fixtures.HammerTests.place)
    setUp = incoming.ReinhardtNpcIncomingTests.setUp
    victim = incoming.ReinhardtNpcIncomingTests.victim
    tap = ice_wall.ReinhardtIceWallTests.tap

    @classmethod
    def setUpClass(cls):
        fixtures.TalonInteractionTests.setUpClass()
        cls.catalog = fixtures.TalonInteractionTests.catalog

    @classmethod
    def tearDownClass(cls):
        fixtures.TalonInteractionTests.tearDownClass()

    def engage(self, name, distance, *, shield=False):
        definition = next(item for item in self.catalog.values() if item.name == name)
        actor, _, _ = self.scenario(definition, (0, 1, distance))
        self.advance(25, buttons=2 if shield else 0)
        self.arm(actor)
        return actor

    def arm(self, actor):
        self.rein.mover.yaw, self.rein.mover.pitch = aim(
            self.match.combat._eye(self.rein), (0, 2, actor.position[2]))
        self.assertEqual(npc_control.start(self.match, self.rein, action="melee"), actor.entity)
        self.assertEqual(npc_control.button(actor), 31)
        self.assertEqual(actor.body_script.component.world.ai_weapon_button(0), 20)

    def test_real_windups_and_armour_damage_then_stop(self):
        for name, distance, frames, amount in (("Heavy Assault", 2, 19, 75), ("Trooper", 1.3, 13, 25)):
            with self.subTest(name=name):
                self.engage(name, distance)
                self.advance(frames - 1, buttons=0)
                self.assertEqual(self.victim().total, 500)
                fight = self.match.combat
                with patch.object(fight, "knockback", wraps=fight.knockback) as impulse:
                    self.advance(1, buttons=0)
                impulse.assert_called_once()
                self.assertLess(impulse.call_args.args[1][2], 0)
                self.assertEqual(self.victim().now[ARMOUR_POOL], 200 - amount)
                npc_control.stop(self.match)
                self.advance(65, buttons=0)
                self.assertEqual(self.victim().total, 500 - amount)

    def test_genji_deflect_blocks_frontal_npc_melee_without_redirecting_damage(self):
        for name, distance, damage in (("Heavy Assault", 2, 80), ("Trooper", 1.3, 30)):
            for facing in (0, -32768):
                with self.subTest(name=name, frontal=facing == 0):
                    definition = next(item for item in self.catalog.values() if item.name == name)
                    actor, _, actor_health = self.scenario(definition, (0, 1, distance))
                    self.assertTrue(self.match.switch_hero(self.rein, heroes()[deflection.GENJI]))
                    self.place(self.rein, (0, 1, 0))
                    self.advance(80, buttons=0)
                    self.arm(actor)
                    self.advance(1, buttons=16, yaw=facing)
                    active = defenses.active_state(self.rein, deflection.GENJI, 0x03A3, 10)
                    self.assertIsNotNone(active)
                    graph_index = npc_melee.GRAPHS[actor.body]
                    weapon = next(i for i in actor.body_script.component.instances.values()
                                  if i.graph.index == graph_index)
                    contact = weapon.node_state(weapon.graph.node(npc_melee.QUERY_AND_DAMAGE[graph_index][0]))
                    source_before = actor_health.total
                    self.advance(30, buttons=0, yaw=facing)
                    self.assertTrue(active.active)
                    self.assertEqual(contact.counter, 1)
                    self.assertEqual(self.victim().total, 200 if facing == 0 else 200 - damage)
                    self.assertEqual(actor_health.total, source_before)

    def test_melee_windups_restart_on_body_reuse_or_manual_retarget(self):
        for name, distance, frames, damage in (("Heavy Assault", 2, 19, 75), ("Trooper", 1.3, 13, 25)):
            for retarget in (False, True):
                with self.subTest(name=name, retarget=retarget):
                    actor = self.engage(name, distance)
                    self.advance(5, buttons=0)
                    old_health = self.victim()
                    self.assertEqual(old_health.total, 500)
                    self.assertTrue(actor.attack_input)
                    fight = self.match.combat
                    replacement = self.other if retarget else self.rein
                    old_id = replacement.body
                    self.assertTrue(self.match.switch_hero(replacement, self.rein.hero))
                    if retarget:
                        self.place(self.rein, (10, 1, 0))
                        self.place(replacement, (0, 1, 0))
                        replacement.mover.yaw, replacement.mover.pitch = aim(
                            fight._eye(replacement), (0, 2, distance))
                        self.assertEqual(npc_control.start(self.match, replacement, action="melee"), actor.entity)
                    else:
                        with patch.object(replacement.client, 'body_id_available',
                                          side_effect=lambda body: body == old_id):
                            self.assertTrue(self.match.switch_hero(replacement, replacement.hero))
                        self.assertEqual(replacement.body, old_id)
                        self.place(replacement, (0, 1, 0))
                    new_health = fight.vitals_of(fight._target_entity(replacement.body))
                    self.assertIsNot(old_health, new_health)
                    self.advance(frames - 1, buttons=0)
                    self.assertEqual(new_health.total, 500)
                    for _ in range(150):
                        self.advance(1, buttons=0)
                        if new_health.total < 500:
                            break
                    self.assertEqual(new_health.total, 500 - damage)
                    self.assertEqual(old_health.total, 500)

    def test_range_and_cancel_before_contact(self):
        for name in ("Heavy Assault", "Trooper"):
            with self.subTest(name=name):
                self.engage(name, 6)
                self.advance(45, buttons=0)
                self.assertEqual(self.victim().total, 500)
                self.engage(name, 1.4)
                self.advance(5, buttons=0)
                npc_control.stop(self.match)
                self.advance(45, buttons=0)
                self.assertEqual(self.victim().total, 500)

    def test_npc_melee_uses_its_contact_volume_against_raised_barrier_and_body(self):
        for name, distance, shield_damage, armour_damage in (
            ("Heavy Assault", 2.4, 80, 75), ("Trooper", 1.3, 0, 25),
            ("Trooper", 2.3, 30, 25),
        ):
            with self.subTest(name=name, distance=distance):
                self.engage(name, distance, shield=True)
                self.advance(24, buttons=2)
                barrier = self.match.combat.barriers[self.rein.body]
                self.assertTrue(barrier.active)
                self.assertEqual(barrier.current, 1600 - shield_damage)
                self.assertEqual(self.victim().total, 500 - armour_damage)

    def test_static_wall_inserted_during_heavy_windup_prevents_contact(self):
        self.engage("Heavy Assault", 2.4)
        self.advance(5, buttons=0)
        fixtures.charge_wall(self, z=1.2)
        self.advance(45, buttons=0)
        self.assertEqual(self.victim().total, 500)

    def test_live_ice_wall_blocks_both_melee_windups_until_cancelled(self):
        for name, distance, damage in (("Heavy Assault", 2.4, 75), ("Trooper", 1.3, 25)):
            with self.subTest(name=name):
                actor = self.engage(name, distance)
                self.assertTrue(self.match.switch_hero(self.enemy, heroes()[defenses.MEI]))
                self.place(self.enemy, (0, 1, 5))
                self.advance(3, buttons=0)
                pillar = ice_wall.ReinhardtIceWallTests.wall(self, distance / 2)
                self.advance(24, buttons=0)
                self.assertEqual(self.victim().total, 500)
                self.assertIn(pillar.entity, self.match.combat.walls.pieces)
                npc_control.stop(self.match)
                self.tap(16, (0, 0))
                self.assertFalse(self.match.combat.walls.pieces)
                self.advance(70, buttons=0)
                self.arm(actor)
                self.advance(20, buttons=0)
                self.assertEqual(self.victim().total, 500 - damage)

    def test_npc_melee_releases_when_target_becomes_an_ally(self):
        for name, distance, remaining in (("Trooper", 1.3, 475), ("Heavy Assault", 2, 425)):
            with self.subTest(name=name):
                actor = self.engage(name, distance)
                self.advance(5, buttons=0)
                self.assertTrue(actor.attack_input)
                self.match.game_map = replace(self.match.game_map, free_for_all=False)
                self.rein.team = actor.team
                self.advance(60, buttons=0)
                self.assertFalse(actor.attack_input)
                self.assertEqual(self.victim().total, 500)
                self.rein.team = 1 - actor.team
                for _ in range(120):
                    self.advance(1, buttons=0)
                    if self.victim().total < 500:
                        break
                self.assertEqual(self.victim().total, remaining)

    def test_actual_earthshatter_interrupts_heavy_melee_then_fresh_contact_recovers(self):
        actor = self.engage("Heavy Assault", 2.4)
        npc_control.stop(self.match)
        script = self.rein.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        self.advance(1, buttons=32)
        self.advance(24, buttons=0)
        self.arm(actor)
        self.advance(30, buttons=0)
        self.assertTrue(self.match.combat.controlled(actor.entity))
        self.assertFalse(actor.attack_input)
        self.assertEqual(self.victim().total, 500)
        self.advance(90, buttons=0)
        self.assertTrue(self.match.combat.controlled(actor.entity))
        self.assertEqual(self.victim().total, 500)
        for _ in range(170):
            self.advance(1, buttons=0)
            if self.victim().total < 500:
                break
        self.assertFalse(self.match.combat.controlled(actor.entity))
        self.assertEqual(self.victim().total, 425)

    def test_actual_earthshatter_interrupts_trooper_melee_then_fresh_contact_recovers(self):
        definition = next(item for item in self.catalog.values() if item.name == "Trooper")
        actor, _, actor_health = self.scenario(definition, (0, 1, 1.3))
        self.match.game_map = replace(self.match.game_map, free_for_all=False)
        self.assertTrue(self.match.switch_hero(self.other, self.rein.hero))
        self.other.team = self.rein.team = 1 - actor.team
        self.place(self.other, (0, 1, -7))
        self.advance(25, buttons=0)
        caster = self.other.body_script
        caster.set_ult_charge(caster.component.entity_var(338).value())
        self.run.step({self.other: (0, 0, 32)})
        fight = self.match.combat
        for _ in range(75):
            self.advance(1, buttons=0)
            wave = fight.shatters.get(self.other.body)
            if wave is not None and wave.travelled >= 5:
                break
        self.assertIsNotNone(wave)
        self.assertGreaterEqual(wave.travelled, 5)
        self.assertFalse(fight.controlled(actor.entity))
        before = actor_health.total
        self.arm(actor)
        self.advance(3, buttons=0)
        self.assertTrue(actor.attack_input)
        self.assertFalse(fight.controlled(actor.entity))
        for _ in range(9):
            self.advance(1, buttons=0)
            if fight.controlled(actor.entity):
                break
        self.assertTrue(fight.controlled(actor.entity))
        self.assertEqual(actor_health.total, before - 50)
        self.assertEqual(self.victim().total, 500)
        self.advance(50, buttons=0)
        self.assertFalse(actor.attack_input)
        self.assertEqual(self.victim().total, 500)
        for _ in range(240):
            self.advance(1, buttons=0)
            if self.victim().total < 500:
                break
        self.assertFalse(fight.controlled(actor.entity))
        self.assertEqual(self.victim().total, 475)
