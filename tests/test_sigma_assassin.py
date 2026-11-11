"""Actual external Assassin1157 against Sigma; manual contact/clock remain reconstructed."""

import math
import os
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import test_sigma_combat as sigma_cases
from test_game_combat import FIRE, KANEZAKA_DM, FakeClient, Run, aim
from test_sigma_flux_placement import SigmaFluxPlacementTests

from ow174.game import assassin, assassin_controller, combat, mover, npc, npc_control, sigma
from ow174.game.content import heroes
from ow174.game.npc_context import ENFORCER
from ow174.game.script import expr, graph, nodes


class ManualClient(FakeClient):
    def queue_bot_death(self, entity, event):
        pass

    def queue_bot_death_update(self, entity, event):
        pass

    def bot_stream(self, entity):
        return None


@unittest.skipUnless(os.environ.get("ORPHEUS_NPC_CATALOG") and os.environ.get("ORPHEUS_NPC_GRAPHS"),
                     "requires external five-NPC catalog and graphs")
class SigmaAssassinTests(unittest.TestCase):
    def setUp(self):
        self.graphs = patch.object(graph, "GRAPHS_PATH", Path(os.environ["ORPHEUS_NPC_GRAPHS"]))
        self.graphs.start()
        self.clear_graphs()
        self.addCleanup(self.clear_graphs)
        self.addCleanup(self.graphs.stop)
        self.definition = next(d for d in npc.load_catalog(Path(os.environ["ORPHEUS_NPC_CATALOG"])).values()
                               if d.hero == assassin.HERO)
        self.assertEqual(mover.mover_data(self.definition.body).stand_height, 1.5)

    @staticmethod
    def clear_graphs():
        for function in (graph._data, graph._records, graph.graph):
            function.cache_clear()

    def arena(self, names=("A",), *, target_graph=False):
        run = Run(KANEZAKA_DM, names=names)
        match, player = run.match, run.players[0]
        match.bots.clear()
        match._pending_bot_scripts.clear()
        match.game_map = match.game_map.__class__(**{**match.game_map.__dict__, "free_for_all": False})
        player.client = ManualClient(player)
        match.switch_hero(player, heroes()[sigma.HERO])
        x, y, z = player.mover.position
        actor = npc.spawn(match, self.definition, (x, y, z + 2), team=1, yaw=-32768,
                          assassin_target_graph=target_graph)
        yaw, pitch = aim(match.combat._eye(player), (x, y + 0.75, z + 2))
        run.step({player: (yaw, pitch, 0)})
        self.assertEqual(npc_control.button(actor), 36)
        self.assertTrue(any(i.graph.index == assassin.GRAPH
                            for i in actor.body_script.component.instances.values()))
        return run, player, actor, yaw, pitch

    def pin(self, run, player, actor, yaw, pitch):
        npc_control.start(run.match, player)
        for _ in range(10):
            run.step({player: (yaw, pitch, 0)})
            if run.match.combat.pins.get(actor.entity) == player.body:
                return
        self.fail("Actual manual1157 input did not establish contact control")

    @staticmethod
    def instance(player, index):
        return next(i for i in player.body_script.component.instances.values() if i.graph.index == index)

    def test_contact_interrupts_grasp_without_absorption_and_cooldown_blocks_reentry(self):
        run, player, actor, yaw, pitch = self.arena()
        run.step({player: (yaw, pitch, 0x08)})
        grasp = self.instance(player, sigma.GRASP)
        self.assertTrue(grasp.states[1].active)
        self.pin(run, player, actor, yaw, pitch)
        self.assertFalse(grasp.states[1].active)
        self.assertEqual(grasp.vars[21312].value(), 0)
        self.assertEqual(run.match.combat.vitals[player.body].total, 375)
        for _ in range(8):
            run.step({player: (yaw, pitch, 0x08)})
            self.assertFalse(grasp.states[1].active)
        npc_control.stop(run.match)
        before = run.match.combat.vitals[player.body].total
        for _ in range(80):
            run.step({player: (yaw, pitch, 0x08)})
        self.assertFalse(run.match.combat.is_stunned(player))
        self.assertEqual(run.match.combat.vitals[player.body].total, before)
        self.assertGreater(grasp.states[0].remaining(), 0)
        self.assertFalse(grasp.states[1].active)

    def test_pin_after_enforcer_absorption_prevents_grasp_shield_grant(self):
        catalog = npc.load_catalog(Path(os.environ["ORPHEUS_NPC_CATALOG"]))
        enforcer = next(definition for definition in catalog.values() if definition.hero == ENFORCER)
        for mode in ("natural", "interrupted", "lethal"):
            with self.subTest(mode=mode):
                run, player, actor, _, _ = self.arena()
                fight = run.match.combat
                script = player.body_script
                script.ult_seconds = 0
                script.set_ult_charge(73)
                target = next(t for t in fight._targets() if t.owner is player)
                vitals = fight.vitals_of(target)
                if mode == "lethal":
                    vitals.now[combat.HEALTH_POOL] = 25
                    vitals.now[combat.SHIELDS_POOL] = 0
                x, y, z = player.mover.position
                actor.position = (x + 2, y, z + 1)
                shooter = npc.spawn(run.match, enforcer, (x, y, z + 6), team=actor.team, yaw=-32768)
                yaw, pitch = aim(fight._eye(player), (x, y + 1.25, z + 6))
                run.step({player: (yaw, pitch, 0x08)})
                self.assertEqual(npc_control.start(run.match, player), shooter.entity)
                grasp = self.instance(player, sigma.GRASP)
                for _ in range(100):
                    run.step({player: (yaw, pitch, 0)})
                    if grasp.vars[21312].value() == 50:
                        break
                self.assertTrue(grasp.states[1].active)
                self.assertEqual(grasp.vars[21312].value(), 50)
                npc_control.stop(run.match)
                if mode != "natural":
                    yaw, pitch = aim(fight._eye(player), (x + 2, y + 0.75, z + 1))
                    run.step({player: (yaw, pitch, 0)})
                    if mode == "lethal":
                        self.assertEqual(npc_control.start(run.match, player), actor.entity)
                        for _ in range(10):
                            run.step({player: (yaw, pitch, 0)})
                            if fight.is_dead(player):
                                break
                        self.assertTrue(fight.is_dead(player))
                        self.assertEqual(vitals.killer, actor.entity)
                        self.assertNotIn(actor.entity, fight.pins)
                    else:
                        self.pin(run, player, actor, yaw, pitch)
                    self.assertFalse(grasp.states[1].active)
                    npc_control.stop(run.match)
                for _ in range(130):
                    run.step({})
                self.assertFalse(grasp.states[1].active)
                expected = {"natural": 400, "interrupted": 375, "lethal": 0}[mode]
                self.assertEqual(sum(vitals.now.values()), expected)
                self.assertEqual(script.component.entity_var(342).value(), 73)
                if mode != "natural":
                    self.assertNotIn(sigma.SHIELD_TAG, vitals.temporary_shields)
                    self.assertNotIn(player.body, fight.grasp_grants)
                    if mode == "interrupted":
                        self.assertGreater(grasp.states[0].remaining(), 0)
                    else:
                        self.assert_grasp_death_respawn(run, player)
                else:
                    self.assertAlmostEqual(vitals.temporary_shields[sigma.SHIELD_TAG].now, 30, places=4)

    def assert_grasp_death_respawn(self, run, player):
        fight = run.match.combat
        old_body = player.body
        received = []
        respawn = fight._respawn

        def capture(who):
            respawn(who)
            if who is player and player.body != old_body:
                received.append(player.body_script.component.entity_var(342).value())

        with patch.object(fight, "_respawn", side_effect=capture):
            for _ in range(750):
                run.step({})
                if received:
                    break
        self.assertEqual(received, [73])
        self.assertNotIn(old_body, fight.grasp_grants)
        self.assertNotIn(player.body, fight.grasp_grants)
        target = next(t for t in fight._targets() if t.owner is player)
        self.assertFalse(fight.vitals_of(target).temporary_shields)
        grasp = self.instance(player, sigma.GRASP)
        self.assertEqual(grasp.states[0].remaining(), 0)
        run.step({player: (0, 0, 0x08)})
        self.assertTrue(grasp.states[1].active)
        self.assertEqual(grasp.vars[21312].value(), 0)

    @unittest.skipUnless(os.environ.get("ORPHEUS_SIGMA_BARRIER_MODEL"), "requires external Barrier model")
    def test_barrier_blocks_enforcer_while_pinned_and_recalls_after_release(self):
        run, player, actor, yaw, pitch = self.arena()
        fight = run.match.combat
        x, y, z = player.mover.position
        catalog = npc.load_catalog(Path(os.environ["ORPHEUS_NPC_CATALOG"]))
        enforcer = next(definition for definition in catalog.values() if definition.hero == ENFORCER)
        shooter = npc.spawn(run.match, enforcer, (x + 2, y, z + 6), team=actor.team, yaw=-32768)
        for frame in range(25):
            run.step({player: (yaw, 0, 0x02 if frame < 4 else 0)})
        barrier, = fight.barriers.values()
        self.assertTrue(barrier.active)
        target = next(t for t in fight._targets() if t.owner is player)
        source = (x, y + 1.25, z + 2)
        self.assertFalse(fight._blast_passes_barriers(actor, source, target, None))
        aim_shooter = aim(fight._eye(player), (x + 2, y + 1.25, z + 6))
        run.step({player: (*aim_shooter, 0)})
        self.assertEqual(npc_control.start(run.match, player), shooter.entity)
        run.step({player: (yaw, pitch, 0)})
        self.pin(run, player, actor, yaw, pitch)
        self.assertEqual(barrier.vitals.total, 700)
        body_hits = []
        damage = fight._damage

        def record_damage(attacker, victim, amount, *args, **kwargs):
            if victim.owner is player:
                body_hits.append(attacker)
            return damage(attacker, victim, amount, *args, **kwargs)

        with patch.object(fight, "_damage", side_effect=record_damage):
            for frame in range(100):
                run.step({player: (yaw, pitch, 0x02 if frame == 0 else 0)})
                self.assertEqual(fight.pins.get(actor.entity), player.body)
                self.assertTrue(barrier.active)
                if barrier.vitals.total < 700:
                    break
        self.assertEqual(barrier.vitals.total, 650)
        self.assertNotIn(shooter, body_hits)
        npc_control.stop(run.match)
        run.step({player: (yaw, pitch, 0)})
        self.assertNotIn(actor.entity, fight.pins)
        run.step({player: (yaw, pitch, 0x02)})
        run.step({player: (yaw, pitch, 0)})
        self.assertFalse(barrier.active)
        self.assertGreater(barrier.instance.states[111].remaining(), 1.9)

    @unittest.skipUnless(os.environ.get("ORPHEUS_SIGMA_BARRIER_MODEL"), "requires external Barrier model")
    def test_lethal_pin_retires_barrier_and_respawn_gets_a_fresh_one(self):
        run, player, actor, yaw, pitch = self.arena()
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is player)
        vitals = fight.vitals_of(target)
        vitals.now[combat.HEALTH_POOL] = 25
        vitals.now[combat.SHIELDS_POOL] = 0
        for frame in range(25):
            run.step({player: (yaw, 0, 0x02 if frame < 4 else 0)})
        old_barrier, = fight.barriers.values()
        old_body = player.body
        self.assertTrue(old_barrier.active)
        x, y, z = actor.position
        self.assertFalse(fight._blast_passes_barriers(actor, (x, y + 1.25, z), target, None))
        run.step({player: (yaw, pitch, 0)})
        self.assertEqual(npc_control.start(run.match, player), actor.entity)
        for _ in range(10):
            run.step({player: (yaw, pitch, 0)})
            if fight.is_dead(player):
                break
        self.assertTrue(fight.is_dead(player))
        self.assertEqual(vitals.killer, actor.entity)
        self.assertNotIn(actor.entity, fight.pins)
        self.assertNotIn(old_barrier.entity, fight.barriers)
        self.assertEqual(old_barrier.instance.component.entity_var(512).value(), expr.Entity(0))
        self.assertTrue(fight._blast_passes_barriers(actor, (x, y + 1.25, z), target, None))
        npc_control.stop(run.match)
        for _ in range(750):
            run.step({})
            if player.body != old_body:
                break
        self.assertNotEqual(player.body, old_body)
        self.assertNotIn(old_barrier.entity, fight.barriers)
        for frame in range(25):
            run.step({player: (yaw, 0, 0x02 if frame < 4 else 0)})
        fresh, = fight.barriers.values()
        self.assertIsNot(fresh, old_barrier)
        self.assertEqual(fresh.owner_body, player.body)
        self.assertTrue(fresh.active)
        self.assertEqual(fresh.vitals.total, 700)

    def test_flux_lifts_assassin_and_releases_pinned_ally(self):
        run, player, actor, _, _ = self.arena(names=("Sigma", "Ally"))
        ally = run.players[1]
        ally.team = player.team
        fight = run.match.combat
        x, y, z = actor.position
        ally.mover.position[:] = (x + 1.5, y, z)
        script = player.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        aim_yaw, aim_pitch = SigmaFluxPlacementTests().select(run, player, tuple(actor.position), click=False)
        pin_yaw, pin_pitch = aim(fight._eye(ally), (x, y + 0.75, z))
        run.step({ally: (pin_yaw, pin_pitch, 0), player: (aim_yaw, aim_pitch, 0)})
        self.pin(run, ally, actor, pin_yaw, pin_pitch)
        ally_health = fight.vitals[ally.body]
        self.assertEqual(ally_health.total, 175)
        attacker_health = fight.vitals[actor.entity]
        initial = attacker_health.total
        run.step({player: (aim_yaw, aim_pitch, FIRE)})
        for _ in range(150):
            run.step({})
            if actor.effect_mover is not None and actor.effect_mover.airborne:
                break
            self.assertEqual(fight.pins.get(actor.entity), ally.body)
        self.assertIsNotNone(actor.effect_mover)
        self.assertTrue(actor.effect_mover.airborne)
        self.assertEqual(initial - attacker_health.total, 50)
        self.assertNotIn(actor.entity, fight.pins)
        self.assertFalse(fight.is_stunned(ally))
        released_health = ally_health.total
        for _ in range(350):
            run.step({})
            if actor.effect_mover is None:
                break
            self.assertNotIn(actor.entity, fight.pins)
            self.assertEqual(ally_health.total, released_health)
        self.assertEqual(initial - attacker_health.total, 50 + attacker_health.maximum * 0.5)
        self.assertIsNone(actor.effect_mover)
        for _ in range(10):
            if fight.pins.get(actor.entity) == ally.body:
                break
            run.step({})
        self.assertEqual(fight.pins.get(actor.entity), ally.body)
        self.assertAlmostEqual(ally_health.total, released_health - 25)

    def test_flux_exits_assassin_13c3_attack_scope_before_slam(self):
        if graph.graph(assassin_controller.TARGET_GRAPH) is None:
            self.skipTest("requires the external Assassin target graph")
        run, victim, actor, _, _ = self.arena(names=("Victim", "Caster"), target_graph=True)
        match, caster = run.match, run.players[1]
        fight = match.combat
        caster.client = ManualClient(caster)
        caster.team = victim.team
        match.switch_hero(caster, heroes()[sigma.HERO])
        x, y, z = victim.mover.position
        caster.spawn = ((x + 9, y, z - 2), caster.spawn[1])
        caster.mover = caster.new_mover()
        for _ in range(75):
            run.step({})
            if fight.pins.get(actor.entity) == victim.body:
                break
        self.assertEqual(fight.pins.get(actor.entity), victim.body)
        body = next(i for i in actor.body_script.component.instances.values()
                    if i.graph.index == assassin_controller.TARGET_BODY_GRAPH)
        modifier = body.states[body.graph.node(106).state]
        self.assertTrue(modifier.active)
        caster.body_script.set_ult_charge(caster.body_script.component.entity_var(338).value())
        direction = SigmaFluxPlacementTests().select(run, caster, tuple(actor.position), click=False)
        run.step({caster: (*direction, FIRE)})
        with patch.object(fight, "_damage", wraps=fight._damage) as damage:
            for _ in range(150):
                run.step({})
                if actor.effect_mover is not None and actor.effect_mover.airborne:
                    break
            self.assertIsNotNone(actor.effect_mover)
            self.assertNotIn(actor.entity, fight.pins)
            for _ in range(350):
                run.step({})
                self.assertNotIn(actor.entity, fight.pins)
                self.assertFalse(modifier.active)
                if actor.effect_mover is None:
                    break
            self.assertIsNone(actor.effect_mover)
            hits = [call.args[2] for call in damage.call_args_list
                    if call.args[0] is caster and call.args[1].owner is actor]
            self.assertEqual(hits, [50, 250])

    def test_pin_interrupts_charged_flux_without_refund(self):
        run, player, actor, yaw, pitch = self.arena()
        script = player.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        run.step({player: (yaw, pitch, 0x20)})
        ability = self.instance(player, sigma.FLUX).states[0]
        self.assertTrue(ability.flags & nodes.Ability.RUNNING)
        self.pin(run, player, actor, yaw, pitch)
        self.assertFalse(ability.flags & nodes.Ability.RUNNING)
        self.assertLess(script.component.entity_var(342).value(), 10)
        npc_control.stop(run.match)
        for _ in range(50):
            run.step({player: (yaw, pitch, 0x20)})
        self.assertFalse(ability.flags & nodes.Ability.RUNNING)
        self.assertLess(script.component.entity_var(342).value(), script.component.entity_var(338).value())

    def assert_hypersphere_volley(self, *, pinned):
        run, player, actor, _, _ = self.arena()
        fight = run.match.combat
        catalog = npc.load_catalog(Path(os.environ["ORPHEUS_NPC_CATALOG"]))
        enforcer = next(definition for definition in catalog.values() if definition.hero == ENFORCER)
        x, y, z = player.mover.position
        victim = npc.spawn(run.match, enforcer, (x + 14, y, z), team=actor.team)
        victim_health = fight.vitals[victim.entity]
        before = victim_health.total
        shot_yaw, shot_pitch = aim(fight._eye(player), (x + 14, y + 1.25, z))
        run.step({player: (shot_yaw, shot_pitch, FIRE)})
        for _ in range(30):
            if fight.rockets:
                break
            run.step({player: (shot_yaw, shot_pitch, 0)})
        first, = [rocket for rocket in fight.rockets if rocket.shooter is player]
        pin_yaw, pin_pitch = aim(fight._eye(player), (x, y + 0.75, z + 2))
        run.step({player: (pin_yaw, pin_pitch, 0)})
        if pinned:
            self.pin(run, player, actor, pin_yaw, pin_pitch)
        else:
            run.step({player: (pin_yaw, pin_pitch, 0)})
        self.assertEqual(victim_health.total, before)
        saw_second = False
        for _ in range(45):
            run.step({player: (shot_yaw, shot_pitch, 0)})
            self.assertEqual(fight.pins.get(actor.entity), player.body if pinned else None)
            saw_second |= any(rocket.shooter is player and rocket is not first for rocket in fight.rockets)
        self.assertEqual(saw_second, not pinned)
        self.assertEqual(before - victim_health.total, 55 if pinned else 110)
        self.assertFalse(any(rocket.shooter is player for rocket in fight.rockets))
        if not pinned:
            return
        npc_control.stop(run.match)
        for _ in range(80):
            run.step({player: (shot_yaw, shot_pitch, 0)})
            self.assertFalse(any(rocket.shooter is player for rocket in fight.rockets))
        self.assertNotIn(actor.entity, fight.pins)
        self.assertEqual(before - victim_health.total, 55)

    def test_only_lethal_quick_melee_prevents_followup_pin(self):
        for health in (30, 31):
            with self.subTest(health=health):
                run, player, actor, _, _ = self.arena()
                fight = run.match.combat
                x, y, z = player.mover.position
                actor.position = (x, y, z + 1.2)
                yaw, pitch = aim(fight._eye(player), (x, y + 0.75, z + 1.2))
                vitals = fight.vitals[actor.entity]
                player_target = next(target for target in fight._targets() if target.owner is player)
                player_vitals = fight.vitals_of(player_target)
                vitals.now[combat.HEALTH_POOL] = health
                vitals.now[combat.SHIELDS_POOL] = 0
                run.step({player: (yaw, pitch, 0x800)})
                self.assertEqual(vitals.total, health - 30)
                if health > 30:
                    self.pin(run, player, actor, yaw, pitch)
                    self.assertEqual(player_vitals.total, 375)
                    continue
                self.assertTrue(actor.dead)
                self.assertEqual(vitals.killer, player.body)
                with self.assertRaisesRegex(ValueError, "No living manual enemy"):
                    npc_control.start(run.match, player)
                for _ in range(50):
                    run.step({player: (yaw, pitch, 0)})
                    self.assertNotIn(actor.entity, fight.pins)
                self.assertEqual(player_vitals.total, 400)

    def test_pin_denies_quick_melee_and_fresh_input_works_after_release(self):
        run, player, actor, _, _ = self.arena()
        fight = run.match.combat
        x, y, z = player.mover.position
        actor.position = (x, y, z + 1.2)
        yaw, pitch = aim(fight._eye(player), (x, y + 0.75, z + 1.2))
        self.pin(run, player, actor, yaw, pitch)
        vitals = fight.vitals[actor.entity]
        before = vitals.total
        for frame in range(35):
            run.step({player: (yaw, pitch, 0x800 if frame % 2 == 0 else 0)})
            self.assertEqual(fight.pins.get(actor.entity), player.body)
            self.assertEqual(vitals.total, before)
        npc_control.stop(run.match)
        for _ in range(80):
            run.step({player: (yaw, pitch, 0)})
            self.assertEqual(vitals.total, before)
        self.assertFalse(fight.is_stunned(player))
        run.step({player: (yaw, pitch, 0x800)})
        for _ in range(50):
            run.step({player: (yaw, pitch, 0)})
        self.assertEqual(before - vitals.total, 30)

    def test_in_flight_hypersphere_resolves_but_pin_interrupts_second_launch(self):
        for pinned in (False, True):
            with self.subTest(pinned=pinned):
                self.assert_hypersphere_volley(pinned=pinned)

    def test_pin_during_accretion_windup_prevents_delayed_rock(self):
        run, player, actor, yaw, pitch = self.arena()
        run.step({player: (yaw, pitch, 0x10)})
        self.pin(run, player, actor, yaw, pitch)
        npc_control.stop(run.match)
        before = run.match.combat.vitals[actor.entity].total
        for _ in range(120):
            run.step({player: (yaw, pitch, 0)})
            self.assertFalse(any(rocket.shooter is player for rocket in run.match.combat.rockets))
        self.assertEqual(run.match.combat.vitals[actor.entity].total, before)

    def pin_before_rock_impact(self, run, player, actor, yaw, pitch):
        run.step({player: (yaw, pitch, 0x10)})
        for _ in range(40):
            run.step({player: (yaw, pitch, 0)})
        self.assertTrue(any(rocket.shooter is player for rocket in run.match.combat.rockets))
        self.pin(run, player, actor, yaw, pitch)

    def test_in_flight_accretion_releases_pin_and_assassin_can_reengage_after_stun(self):
        run, player, actor, yaw, pitch = self.arena()
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is player)
        health = fight.vitals_of(target)
        npc_health = fight.vitals[actor.entity]
        before = npc_health.total
        self.pin_before_rock_impact(run, player, actor, yaw, pitch)
        self.assertEqual(health.total, 375)
        for _ in range(8):
            run.step({player: (yaw, pitch, 0)})
            if fight.is_stunned(actor):
                break
        self.assertTrue(fight.is_stunned(actor))
        self.assertNotIn(actor.entity, fight.pins)
        self.assertFalse(fight.is_stunned(player))
        self.assertEqual(before - npc_health.total, 70)
        after_rock = health.total
        self.assertLess(after_rock, 375)
        take_commands = player.take_commands

        def approach(commands):
            if math.dist(player.mover.position, actor.position) > 2.4:
                commands = [replace(command, forward=127) for command in commands]
            return take_commands(commands)

        nearby_during_stun = False
        with patch.object(player, "take_commands", side_effect=approach):
            for _ in range(85):
                run.step({player: (yaw, pitch, 0)})
                if fight.is_stunned(actor):
                    distance = math.dist(player.mover.position, actor.position)
                    nearby_during_stun |= distance < assassin.PIN_RANGE
                    self.assertNotIn(actor.entity, fight.pins)
                    self.assertEqual(health.total, after_rock)
                elif fight.pins.get(actor.entity) == player.body:
                    break
        self.assertTrue(nearby_during_stun)
        self.assertFalse(fight.is_stunned(actor))
        self.assertEqual(actor.attack_target, player.entity)
        self.assertEqual(fight.pins.get(actor.entity), player.body)
        self.assertEqual(health.total, after_rock - 25)

    def test_contact_cleanup_on_source_death_removal_and_target_replacement(self):
        for action in ("source_death", "remove", "replace"):
            with self.subTest(action=action):
                run, player, actor, yaw, pitch = self.arena()
                old = player.body
                if action == "source_death":
                    health = run.match.combat.vitals[actor.entity]
                    health.now[combat.HEALTH_POOL] = 45
                    health.now[combat.SHIELDS_POOL] = 0
                    self.pin_before_rock_impact(run, player, actor, yaw, pitch)
                    for _ in range(8):
                        run.step({player: (yaw, pitch, 0)})
                        if actor.dead:
                            break
                    self.assertTrue(actor.dead)
                    self.assertEqual(health.killer, player.body)
                else:
                    self.pin(run, player, actor, yaw, pitch)
                    if action == "remove":
                        npc.remove_spawned(run.match)
                    else:
                        run.match.switch_hero(player, player.hero)
                self.assertNotIn(old, run.match.combat.pins.values())
                self.assertFalse(run.match.combat.is_stunned(player))
                npc_control.stop(run.match)
                target = next(t for t in run.match.combat._targets() if t.owner is player)
                before = run.match.combat.vitals_of(target).total
                for _ in range(80):
                    run.step({})
                self.assertEqual(run.match.combat.vitals[player.body].total, before)

    def test_range_wall_and_ally_changes_release_control_without_late_damage(self):
        for change in ("range", "wall", "ally"):
            with self.subTest(change=change):
                run, player, actor, yaw, pitch = self.arena()
                self.pin(run, player, actor, yaw, pitch)
                if change == "range":
                    x, y, z = actor.position
                    actor.position = (x, y, z + 10)
                elif change == "wall":
                    sigma_cases.SigmaCombatTests().wall(run, sideways=False, offset=1)
                else:
                    actor.team = player.team
                before = run.match.combat.vitals[player.body].total
                for _ in range(80):
                    run.step({})
                self.assertNotIn(actor.entity, run.match.combat.pins)
                self.assertEqual(run.match.combat.vitals[player.body].total, before)


if __name__ == "__main__":
    unittest.main()
