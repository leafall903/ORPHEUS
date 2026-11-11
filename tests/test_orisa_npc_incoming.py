"""Actual authored Talon primary attacks; native acquisition/specials stay open."""

import math
import os
import unittest
from dataclasses import replace

import test_orisa_barrier as barrier_fixture
import test_orisa_combat as combat_fixture
import test_orisa_npc as fixture
import test_orisa_supercharger as supercharger_fixture
from test_game_combat import aim

from ow174.game import npc, npc_control
from ow174.game.collision import flat_world
from ow174.game.commands import FIRE
from ow174.game.content import find_hero
from ow174.game.script import expr

PRIMARY = {"Trooper": (15, 7, 20), "Sniper": (95, 55, 100), "Heavy Assault": (2.5, 1.5, 5),
           "Enforcer": (25, 15, 50)}


@unittest.skipUnless(os.environ.get("ORPHEUS_NPC_RUNTIME"), "actual external Talon graphs required")
class OrisaNpcIncomingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture.OrisaTalonTests.setUpClass()
        cls.catalog = fixture.OrisaTalonTests.catalog

    @classmethod
    def tearDownClass(cls):
        fixture.OrisaTalonTests.tearDownClass()

    def definition(self, name):
        return next(d for d in self.catalog.values() if d.name == name)

    def arena(self, name, *, fortify=False, ahead=8):
        run, player, bot = fixture.OrisaTalonTests().arena(self.definition(name), ahead=ahead)
        # This transport fixture has no registered NPC observer stream. The
        # authoritative projectile still flies/hits; no combat path is mocked.
        player.client.bot_stream = lambda body: None
        run.step({player: (0, 0, 8 if fortify else 0)})
        return run, player, bot

    @staticmethod
    def vitals(run, player):
        fight = run.match.combat
        return fight.vitals_of(combat_fixture.OrisaCombatTests().target(run, player))

    def first_damage(self, run, player, *, limit=200):
        vitals = self.vitals(run, player)
        before = vitals.total
        for _ in range(limit):
            run.step({})
            if vitals.total < before:
                break
        return before - vitals.total

    def start(self, run, player, bot):
        # Manual selection is an actual aim ray, not an NPC attack permission.
        # Orisa's authored eye is above Trooper/Sniper's capsule when level.
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is bot)
        a, b = target.segment()
        centre = tuple((x + y) / 2 for x, y in zip(a, b, strict=True))
        yaw, pitch = aim(fight._eye(player), centre)
        run.step({player: (yaw, pitch, 0)})
        self.assertIs(npc_control.selected(run.match, player), bot)
        return npc_control.start(run.match, player)

    def test_actual_four_primary_attacks_and_fortify_mitigate_before_armour(self):
        for name, (normal, fortified, _) in PRIMARY.items():
            for fortify in (False, True):
                with self.subTest(npc=name, fortify=fortify):
                    run, player, bot = self.arena(name, fortify=fortify)
                    self.assertEqual(self.start(run, player, bot), bot.entity)
                    loss = self.first_damage(run, player)
                    if name == "Enforcer":
                        unit = .6 if fortify else 1
                        self.assertGreater(loss, 0)
                        self.assertLessEqual(loss, fortified if fortify else normal)
                        self.assertAlmostEqual(loss / unit, round(loss / unit), places=4)
                    else:
                        self.assertAlmostEqual(loss, fortified if fortify else normal, places=4)
                    self.assertIsNotNone(bot.body_script)
                    self.assertTrue(bot.attack_input)

    def test_trooper_primary_hits_while_halt_moves_and_slows_it(self):
        for fortified in (False, True):
            with self.subTest(fortified=fortified):
                run, player, bot = self.arena("Trooper", fortify=fortified, ahead=10)
                fight = run.match.combat
                vitals = self.vitals(run, player)
                losses = {"moving": [], "slow": []}
                for frame in range(100):
                    if frame == 33:
                        self.start(run, player, bot)
                    before = vitals.total
                    position = bot.position
                    pull = fight.orisa.pulls.get(bot.entity)
                    slow = pull and pull.script.node_state(pull.script.graph.node(5)).active
                    run.step({player: (0, 0, 2 if frame in (0, 25) else 0)})
                    if bot.position != position and vitals.total < before:
                        losses["moving"].append(before - vitals.total)
                    if slow and vitals.total < before:
                        losses["slow"].append(before - vitals.total)
                self.assertTrue(losses["moving"], losses)
                self.assertTrue(losses["slow"], losses)
                for loss in losses["moving"] + losses["slow"]:
                    self.assertAlmostEqual(loss, 7 if fortified else 15, places=5)
                self.assertTrue(bot.attack_input)

    def test_sniper_and_enforcer_primary_hit_during_halt_phases(self):
        # Align each authored weapon's first shot with the short movement
        # window or the longer post-movement wait.
        for name, phase, engage_frame in (
            ("Sniper", "moving", -75), ("Sniper", "slow", -50),
            ("Enforcer", "moving", -15), ("Enforcer", "slow", 1),
        ):
            with self.subTest(npc=name, phase=phase):
                losses = []
                for fortified in (False, True):
                    run, player, bot = self.arena(name, fortify=fortified, ahead=10)
                    fight = run.match.combat
                    vitals = self.vitals(run, player)
                    for frame in range(min(0, engage_frame), 110):
                        if frame == engage_frame:
                            self.start(run, player, bot)
                        before = vitals.total
                        position = bot.position
                        pull = fight.orisa.pulls.get(bot.entity)
                        slow = pull and pull.script.node_state(pull.script.graph.node(5)).active
                        run.step({player: (0, 0, 2 if frame in (0, 25) else 0)})
                        in_phase = bot.position != position if phase == "moving" else slow
                        if in_phase and vitals.total < before:
                            losses.append(before - vitals.total)
                            self.assertTrue(bot.attack_input)
                            break
                    else:
                        self.fail(f"No {phase} hit with Fortify={fortified}")
                if name == "Sniper":
                    self.assertAlmostEqual(losses[0], 95)
                    self.assertAlmostEqual(losses[1], 55, places=5)
                else:
                    self.assertGreater(losses[0], losses[1])
                    self.assertGreater(losses[1], 0)

    def sniper_kills_caster_during_halt_flight(self, *, wall=False):
        run, player, bot = self.arena("Sniper", ahead=10)
        if wall:
            combat_fixture.OrisaCombatTests().wall(run, player, 14.5)
        fight = run.match.combat
        halt = fight.orisa
        vitals = self.vitals(run, player)
        for _ in range(2):
            vitals.damage(vitals.total - 80)
        self.assertEqual(vitals.total, 80)
        self.start(run, player, bot)
        for _ in range(105):
            run.step({})
        run.step({player: (0, 0, 2)})
        for _ in range(30):
            run.step({})
            if vitals.dead:
                break
        self.assertTrue(vitals.dead)
        orb, = halt.orbs.values()
        self.assertIn(orb.rocket, fight.rockets)
        self.assertIsNone(orb.requested)
        self.assertEqual(orb.rocket.source_body, player.body)

        return run, player, bot, orb

    def test_inflight_halt_retires_on_respawn_after_sniper_kills_caster(self):
        run, player, _, orb = self.sniper_kills_caster_during_halt_flight()
        fight = run.match.combat
        halt = fight.orisa
        vitals = self.vitals(run, player)
        for frame in range(20):
            run.step({player: (0, 0, 2 if frame % 2 == 0 else 0)})
            self.assertIsNone(orb.requested)
        for _ in range(350):
            run.step({})
            if orb.entity not in halt.orbs:
                break
        self.assertTrue(vitals.dead)
        self.assertNotIn(orb.entity, halt.orbs)
        self.assertNotIn(orb.rocket, fight.rockets)
        self.assertEqual(orb.rocket.ended, "retired")
        self.assertFalse(halt.results)
        self.assertNotEqual(player.body, orb.rocket.source_body)
        self.assertFalse(fight.is_dead(player))

    def test_inflight_halt_hits_wall_and_pulls_sniper_after_caster_death(self):
        run, player, bot, orb = self.sniper_kills_caster_during_halt_flight(wall=True)
        fight = run.match.combat
        halt = fight.orisa
        original = bot.position
        for _ in range(100):
            run.step({})
            self.assertTrue(fight.is_dead(player))
            self.assertEqual(player.body, orb.rocket.source_body)
            if bot.position != original:
                break
        self.assertEqual(tuple(halt.results), ((orb.entity, (bot.entity,)),))
        self.assertIsNotNone(orb.requested)
        self.assertIn(orb.rocket, fight.ability_impacts)
        self.assertGreater(bot.position[2], original[2])
        self.assertEqual(halt.pulls[bot.entity].script.graph.index, 0xCCE)

    def test_sniper_kills_halt_caster_without_ending_its_existing_slow(self):
        run, player, bot = self.arena("Sniper", ahead=10)
        fight = run.match.combat
        vitals = self.vitals(run, player)
        # Seed the pre-hit pools. Only the authored Sniper shot is lethal.
        for _ in range(2):
            vitals.damage(vitals.total - 80)
        self.assertEqual(vitals.total, 80)
        self.start(run, player, bot)
        for _ in range(50):
            run.step({})
        for frame in range(100):
            pull = fight.orisa.pulls.get(bot.entity)
            wait = pull and pull.script.node_state(pull.script.graph.node(5))
            was_slow = wait and wait.active
            run.step({player: (0, 0, 2 if frame in (0, 25) else 0)})
            if vitals.dead:
                self.assertTrue(was_slow)
                break
        self.assertTrue(vitals.dead)
        self.assertIs(fight.orisa.pulls[bot.entity], pull)
        self.assertTrue(wait.active)

        fixture.OrisaTalonTests.start_patrol(bot)
        slowed = []
        recovered = []
        for _ in range(80):
            was_slow = wait.active
            run.step({})
            speed = math.hypot(bot.velocity[0], bot.velocity[2])
            (slowed if was_slow else recovered).append(speed)
        self.assertGreater(len(slowed), 1)
        self.assertAlmostEqual(max(slowed), bot.speed * 0.35, places=5)
        self.assertAlmostEqual(max(recovered), bot.speed, places=5)
        self.assertNotIn(bot.entity, fight.orisa.pulls)
        self.assertTrue(pull.script.stopped)

    def test_no_attack_without_input_and_stop_releases_authored_weapon(self):
        for name in PRIMARY:
            with self.subTest(npc=name):
                run, player, bot = self.arena(name)
                for _ in range(150):
                    run.step({})
                self.assertEqual(self.vitals(run, player).total, 450)
                self.start(run, player, bot)
                self.assertGreater(self.first_damage(run, player), 0)
                npc_control.stop(run.match)
                before = self.vitals(run, player).total
                for _ in range(200):
                    run.step({})
                self.assertEqual(self.vitals(run, player).total, before)
                self.assertFalse(bot.attack_input)
                self.assertNotIn(npc_control.button(bot), bot.body_script.component.held)

    def test_actual_npc_fire_kills_owner_releases_input_and_reacquires_respawn(self):
        for name, (damage, _, _) in PRIMARY.items():
            with self.subTest(npc=name):
                run, player, bot = self.arena(name)
                fight = run.match.combat
                old_body = player.body
                health = fight.vitals[bot.entity].total
                self.start(run, player, bot)
                for _ in range(5000):
                    run.step({})
                    if fight.is_dead(player):
                        break
                self.assertTrue(fight.is_dead(player))
                self.assertEqual(self.vitals(run, player).total, 0)
                run.step({})
                self.assertFalse(bot.attack_input)
                self.assertEqual(fight.vitals[bot.entity].total, health)
                for _ in range(1000):
                    run.step({})
                    if player.body != old_body:
                        break
                self.assertNotEqual(player.body, old_body)
                self.assertFalse(fight.is_dead(player))
                loss = self.first_damage(run, player)
                if name == "Enforcer":
                    self.assertGreater(loss, 0)
                    self.assertLessEqual(loss, damage)
                    self.assertAlmostEqual(loss, round(loss), places=4)
                else:
                    self.assertAlmostEqual(loss, damage, places=4)
                self.assertTrue(bot.attack_input)

    def test_manual_attacks_release_for_team_change_or_static_cover(self):
        for name in PRIMARY:
            for blocked in ("team", "wall"):
                with self.subTest(npc=name, blocked=blocked):
                    run, player, bot = self.arena(name)
                    self.start(run, player, bot)
                    if blocked == "team":
                        run.match.game_map = replace(run.match.game_map, free_for_all=False)
                        bot.team = player.team
                    else:
                        combat_fixture.OrisaCombatTests().wall(run, player, 4)
                    for _ in range(200):
                        run.step({})
                    self.assertEqual(self.vitals(run, player).total, 450)
                    self.assertFalse(bot.attack_input)
                    self.assertIsNotNone(bot.body_script)

    @unittest.skipUnless(os.environ.get("ORPHEUS_ORISA_GEOMETRY"), "actual barrier mesh required")
    def test_actual_four_primary_attacks_hit_barrier_without_hurting_owner(self):
        for name, (_, _, damage) in PRIMARY.items():
            with self.subTest(npc=name):
                run, player, enemy, _, shield = barrier_fixture.BarrierTests().deploy()
                enemy.spawned = False
                player.client.bot_stream = lambda body: None
                enemy.client.bot_stream = lambda body: None
                x, y, z = shield.position
                bot = npc.spawn(run.match, self.definition(name), (x, y, z + 5), team=1)
                run.step({player: (0, 0, 0)})
                self.start(run, player, bot)
                for _ in range(200):
                    run.step({})
                    if shield.health < 600:
                        break
                self.assertAlmostEqual(600 - shield.health, damage, places=4)
                self.assertEqual(self.vitals(run, player).total, 450)
                self.assertIsNotNone(bot.body_script)

    @unittest.skipUnless(os.environ.get("ORPHEUS_ORISA_GEOMETRY"), "actual barrier mesh required")
    def test_deployed_barrier_blocks_trooper_fire_after_sniper_kills_owner(self):
        run, player, ally, _, shield = barrier_fixture.BarrierTests().deploy()
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        ally.team = player.team
        for actor in (player, ally):
            actor.client.bot_stream = lambda _: None
        x, y, z = shield.position
        barrier_fixture.BarrierTests.place(ally, (x, y, z - 4))
        sniper = npc.spawn(run.match, self.definition("Sniper"), (x + 5, y, z - 6), team=1)
        trooper = npc.spawn(run.match, self.definition("Trooper"), (x, y, z + 5), team=1)
        fight = run.match.combat
        vitals = self.vitals(run, player)
        for _ in range(2):
            vitals.damage(vitals.total - 80)
        self.start(run, player, sniper)
        self.assertEqual(self.first_damage(run, player), 80)
        self.assertTrue(fight.is_dead(player))
        self.assertIn(shield.entity, fight.orisa.barriers.active)
        self.assertEqual(shield.health, 600)

        ally_vitals = self.vitals(run, ally)
        health = ally_vitals.total
        self.start(run, ally, trooper)
        for _ in range(100):
            run.step({})
            if shield.health < 600:
                break
        self.assertTrue(fight.is_dead(player))
        self.assertEqual(shield.health, 580)
        self.assertEqual(ally_vitals.total, health)
        npc_control.stop(run.match)
        old_body = player.body
        for _ in range(1000):
            run.step({})
            if player.body != old_body:
                break
        self.assertNotEqual(player.body, old_body)
        self.assertEqual(shield.source_body, old_body)
        self.assertIn(shield.entity, fight.orisa.barriers.active)

    @unittest.skipUnless(os.environ.get("ORPHEUS_ORISA_SUPERCHARGER"), "authored device supplement required")
    def test_hostile_talon_primary_breaks_supercharger_and_then_hits_owner(self):
        for name, (body_damage, _, shot_damage) in PRIMARY.items():
            with self.subTest(npc=name):
                run, owner, other, manager, device = supercharger_fixture.SuperchargerTests().deploy()
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                other.spawned = False
                owner.client.bot_stream = lambda _: None
                other.client.bot_stream = lambda _: None
                x, y, z = device.position
                # Put both combatants on a lower lane so the actual weapon ray
                # crosses the device mesh while aimed at the owner behind it.
                floor = y - 1.1
                run.match.collision_world = flat_world(floor)
                barrier_fixture.BarrierTests.place(owner, (x, floor, z - 2))
                bot = npc.spawn(run.match, self.definition(name), (x, floor, z + 3), team=1)
                run.step({})
                self.assertEqual(manager.scale(owner), 1.5)
                self.assertEqual(manager.scale(bot), 1)
                status = manager.buffs[(device.entity, owner.body)]
                charge_gate = owner.body_script.component.entity_var(842)
                self.assertTrue(charge_gate.value())
                vitals = self.vitals(run, owner)
                initial = vitals.total
                self.assertEqual(self.start(run, owner, bot), bot.entity)
                for _ in range(250):
                    run.step({})
                    if device.health < 200:
                        break
                self.assertAlmostEqual(200 - device.health, shot_damage, places=4)
                self.assertEqual(vitals.total, initial)
                for _ in range(device.until - run.frame):
                    run.step({})
                    if device.entity not in manager.active:
                        break
                    self.assertEqual(vitals.total, initial)
                self.assertLess(run.frame, device.until)
                self.assertEqual(device.health, 0)
                self.assertFalse(device.component.instances)
                self.assertTrue(status.stopped)
                self.assertFalse(charge_gate.value())
                self.assertEqual(manager.scale(owner), 1)
                self.assertNotIn((device.entity, owner.body), manager.buffs)
                loss = initial - vitals.total
                if not loss:
                    loss = self.first_damage(run, owner, limit=300)
                self.assertGreater(loss, 0)
                self.assertLessEqual(loss, body_damage + 1e-5)

    def test_enforcer_health_gate_uses_actual_pools_and_declared_manual_controller(self):
        run, player, bot = self.arena("Enforcer")
        script = bot.body_script
        weapon = next(i for i in script.component.instances.values() if i.graph.index == 0x13EB)
        condition = weapon.graph.node(18).field("m_condition")
        self.assertTrue(weapon.evaluate(condition))
        before = run.match.combat.vitals[bot.entity].total
        x, y, z = bot.position
        yaw, pitch = aim(run.match.combat._eye(player), (x, y + 1, z))
        run.step({player: (yaw, pitch, FIRE)})
        for _ in range(8):
            run.step({})
        self.assertEqual(before - run.match.combat.vitals[bot.entity].total, 11)
        query = {"$": "STU_9B7648C3", "m_entity": {"$": "STU_B5A0CAF0"}}
        self.assertEqual(script.component.world.native(query, weapon), before - 11)
        self.assertIsNone(script.component.world.native(query, None))
        script.component.set(65500, expr.Entity(player.body))
        query["m_entity"] = {"$": "STUConfigVarDynamic", "m_identifier": 65500, "m_60DB8F99": 1}
        self.assertIsNone(script.component.world.native(query, weapon))
        self.assertEqual(script.component.world.controller_components, (75,))
        self.assertTrue(weapon.node_state(weapon.graph.node(1)).evaluate(
            weapon.graph.node(1).field("m_condition")))

    @unittest.skipUnless(os.environ.get("ORPHEUS_ORISA_SUPERCHARGER"), "authored device supplement required")
    def test_allied_trooper_deals_boosted_damage_after_supercharger_owner_dies(self):
        run, owner, enemy, manager, device = supercharger_fixture.SuperchargerTests().deploy()
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        fight = run.match.combat
        x, y, z = owner.mover.position
        barrier_fixture.BarrierTests.place(enemy, (x, y, z - 5))
        for actor in (owner, enemy):
            actor.client.bot_stream = lambda _: None
        owner_vitals = self.vitals(run, owner)
        for _ in range(2):
            owner_vitals.damage(owner_vitals.total - 10)
        run.step({})
        yaw, pitch = aim(fight._eye(enemy), (x, y + 1, z))
        run.step({enemy: (yaw, pitch, FIRE)})
        self.assertTrue(fight.is_dead(owner))
        old_body = owner.body

        run.match.switch_hero(enemy, find_hero("Orisa"))
        x, y, z = device.position
        barrier_fixture.BarrierTests.place(enemy, (x, y, z + 8))
        bot = npc.spawn(run.match, self.definition("Trooper"), (x, y, z + 3), team=owner.team)
        run.step({})
        self.assertEqual(manager.scale(owner), 1)
        self.assertEqual(manager.scale(bot), 1.5)
        self.start(run, enemy, bot)
        self.assertAlmostEqual(self.first_damage(run, enemy), 25, places=4)
        self.assertTrue(fight.is_dead(owner))
        self.assertEqual(owner.body, old_body)
        self.assertEqual(device.source_body, old_body)
        self.assertIn((device.entity, bot.entity), manager.buffs)

        collision = run.match.collision_world
        # Only the aura ray crosses the wall. Trooper and its target are
        # both on the far side, so the weapon path remains clear.
        combat_fixture.OrisaCombatTests().wall(run, enemy, -6.5)
        run.step({})
        self.assertNotIn((device.entity, bot.entity), manager.buffs)
        self.assertEqual(manager.scale(bot), 1)
        self.assertAlmostEqual(self.first_damage(run, enemy), 15, places=4)

        run.match.collision_world = collision
        run.step({})
        self.assertIn((device.entity, bot.entity), manager.buffs)
        self.assertEqual(manager.scale(bot), 1.5)
        self.assertAlmostEqual(self.first_damage(run, enemy), 25, places=4)
        self.assertTrue(fight.is_dead(owner))
        self.assertEqual(owner.body, old_body)

        npc_control.stop(run.match)
        while run.frame < device.until - 1:
            run.step({})
        self.assertEqual(run.frame, device.until - 1)
        self.assertIn(device.entity, manager.active)
        self.assertEqual(manager.scale(bot), 1.5)
        self.assertEqual(device.health, 200)
        run.step({})
        self.assertEqual(run.frame, device.until)
        self.assertNotIn(device.entity, manager.active)
        self.assertNotIn((device.entity, bot.entity), manager.buffs)
        self.assertFalse(device.component.instances)
        self.assertEqual(manager.scale(bot), 1)

        self.start(run, enemy, bot)
        self.assertAlmostEqual(self.first_damage(run, enemy), 15, places=4)

    @unittest.skipUnless(os.environ.get("ORPHEUS_ORISA_SUPERCHARGER"), "authored device supplement required")
    def test_allied_npc_actual_attacks_receive_supercharger_boost_and_fortify_mitigation(self):
        expected = {"Trooper": (25, 13), "Sniper": (145, 85), "Heavy Assault": (3.75, 2.25),
                    "Enforcer": (37.5, 22.5)}
        for name, losses in expected.items():
            for fortify in (False, True):
                with self.subTest(npc=name, fortify=fortify):
                    run, owner, enemy, manager, device = supercharger_fixture.SuperchargerTests().deploy()
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    run.match.switch_hero(enemy, find_hero("Orisa"))
                    x, y, z = device.position
                    barrier_fixture.BarrierTests.place(enemy, (x, y, z + 8))
                    for viewer in (owner, enemy):
                        viewer.client.bot_stream = lambda body: None
                    bot = npc.spawn(run.match, self.definition(name), (x, y, z + 3), team=owner.team)
                    run.step({enemy: (32768, 0, 8 if fortify else 0)})
                    self.assertEqual(manager.scale(bot), 1.5)
                    self.start(run, enemy, bot)
                    self.assertAlmostEqual(self.first_damage(run, enemy), losses[fortify], places=4)
                    self.assertIsNotNone(bot.body_script)
