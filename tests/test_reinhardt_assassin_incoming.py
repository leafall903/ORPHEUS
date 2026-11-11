"""Actual1157 pin action with typed controller target and documented host policies.

Primary AI slot20 remains intact;manual pin explicitly selects authored button36.
Contact range3m and elapsed ModifyHealth policy are server reconstruction.
"""

import math
import os
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import test_game_reinhardt as fixtures
import test_reinhardt_mei_wall as ice_wall
from test_game_combat import aim
from test_game_graph_analysis import make_graph, node, output

from ow174.game import combat, defenses, deflection, npc, npc_control, sigma_grasp, zarya_barrier
from ow174.game.body_vm_snapshot import export_component_vm, snapshot_digest
from ow174.game.commands import Command
from ow174.game.content import SOLDIER, heroes
from ow174.game.mover import IN_AIR
from ow174.game.script import expr, graph, health_queries, message_receive, runtime
from ow174.game.script.bot import AssassinControllerWorld

CONTROLLER_BUNDLE = (Path(__file__).resolve().parents[2] / 'runtime-inputs'
                     / 'assassin-controller-graphs-104319.json.gz')


@unittest.skipUnless((fixtures.NPC_DATA / 'pve_npcs_174.json').is_file()
                     and fixtures.NPC_MOVERS.is_file() and fixtures.BARRIER_DATA.is_file(),
                     'authorized NPC/mover/barrier data required')
class AssassinIncomingTests(unittest.TestCase):
    advance = fixtures.HammerTests.advance
    place = staticmethod(fixtures.HammerTests.place)
    scenario = fixtures.TalonInteractionTests.scenario
    tap = ice_wall.ReinhardtIceWallTests.tap

    @classmethod
    def setUpClass(cls):
        fixtures.TalonInteractionTests.setUpClass()
        cls.catalog = fixtures.TalonInteractionTests.catalog

    @classmethod
    def tearDownClass(cls):
        fixtures.TalonInteractionTests.tearDownClass()

    def setUp(self):
        self.enterContext(patch.dict(os.environ, {
            'ORPHEUS_REINHARDT_COLLISION': str(fixtures.BARRIER_DATA)}))

    def engage(self, *, shield=False, action='pin'):
        definition = next(d for d in self.catalog.values() if d.name == 'Assassin')
        actor, target, health = self.scenario(definition, (0, 1, 2))
        self.advance(20, buttons=2 if shield else 0)
        self.rein.mover.yaw, self.rein.mover.pitch = aim(self.match.combat._eye(self.rein), (0, 2, 2))
        self.assertEqual(npc_control.start(self.match, self.rein, action=action), actor.entity)
        victim = self.match.combat.vitals_of(self.match.combat._target_entity(self.rein.body))
        return actor, target, health, victim

    def pin(self, *, shield=False):
        actor, target, health, victim = self.engage(shield=shield)
        self.advance(1, buttons=2 if shield else 0)
        self.assertTrue(self.match.combat.pinned(self.rein.body))
        self.assertEqual(victim.total, 480)
        self.assertIsNotNone(actor.body_script)
        return actor, target, health, victim

    def damage_phase(self, actor):
        instance = next(i for i in actor.body_script.component.instances.values() if i.graph.index == 0x1157)
        for _ in range(200):
            state = instance.states.get(instance.graph.node(15).state)
            if state is not None and state.active:
                return state
            self.assertTrue(self.match.combat.pinned(self.rein.body))
            self.advance(1, buttons=0)
        self.fail('actual1157 damage phase did not start')

    def test_movement_query_host_reads_player_and_npc_body_state(self):
        actor, _, _, _ = self.engage()
        world = actor.body_script.component.world
        self.assertEqual(world.movement_flags(actor.entity), actor.movement(self.match.tick).flags)
        self.assertFalse(world.movement_flags(actor.entity) & IN_AIR)
        self.rein.mover.flags_word |= IN_AIR
        self.assertTrue(world.movement_flags(self.rein.body) & IN_AIR)
        self.assertIsNone(world.movement_flags(0xA0000000))

    @unittest.skipUnless(CONTROLLER_BUNDLE.is_file(), 'authorized Assassin controller graphs required')
    def test_assassin_controller_mounts_separately_and_retires_on_death_and_removal(self):
        self.enterContext(patch.object(graph, "GRAPHS_PATH", CONTROLLER_BUNDLE))
        self.addCleanup(fixtures.TalonInteractionTests.clear_graphs)
        fixtures.TalonInteractionTests.clear_graphs()
        definition = next(d for d in self.catalog.values() if d.name == 'Assassin')
        actor, target, _ = self.scenario(definition, (0, 1, 2))
        script = actor.body_script
        controller = script.assassin_controller
        self.assertIsNotNone(controller)
        self.assertEqual(controller.entity, script.component.world.manual_controller_descriptor.entity)
        self.assertNotEqual(controller.entity, actor.entity)
        self.assertEqual([instance.graph.index for instance in controller.instances.values()],
                         [0x142A, 0x1172, 0x0E5C, 0x0C83, 0x1158, 0x13C2])
        self.assertEqual(controller.world.target.possession.current, actor.entity)
        self.assertEqual(controller.world.body_health(actor.entity, None), (500, 500))
        self.assertIsNone(controller.fault)
        initial_frame = controller.frame
        entry_instance = controller.instances[1]
        receiver = entry_instance.states[entry_instance.graph.node(29).state]
        self.assertEqual((receiver.counter, receiver.message_key), (1, 0x09DB))
        self.assertEqual(controller.world.records.serial, 2)
        self.assertFalse(controller.world.messages.retired)
        self.assertTrue(controller.world.messages.host.manager.channels)
        self.assertIsNone(script.component.fault)
        self.assertNotIn(0x1158, [instance.graph.index for instance in script.component.instances.values()])
        records = controller.world.records.entries
        self.assertEqual([(entry.token.node.pos, entry.priority) for entry in records], [(2, 10.0), (3, 0.0)])
        self.assertEqual(records[1].payload.extras, (1.5, 1.0))
        self.assertEqual(records[0].payload.extras, (2.0, 2.0, 1.0, expr.f32(.2), 1.0))

        # Exercise the original listener independently with one explicit sender.
        world = AssassinControllerWorld(self.match, controller.world.mode, controller.entity, actor.entity)
        isolated = runtime.Component(controller.entity, world=world, strict=True)
        definition_graph = controller.instances[1].graph
        instance = isolated.create(definition_graph, init=False)
        self.assertIsNone(instance.state(definition_graph.node(12).state))
        self.assertIsNone(instance.state(definition_graph.node(7).state))
        graph_1172 = isolated.create(controller.instances[2].graph, init=False)
        graph_0c83 = isolated.create(controller.instances[4].graph, init=False)
        for pos in (9, 10, 15, 19, 22, 23, 24):
            self.assertIsNone(graph_1172.state(graph_1172.graph.node(pos).state))
        missing = object()
        for graph_instance, position in ((graph_1172, 13), (graph_1172, 26), (graph_0c83, 5)):
            condition = graph_instance.graph.node(position).field("m_condition")
            for cfg in condition.get("m_configVars", (condition,)):
                self.assertIs(graph_instance.evaluate(cfg, initial=missing), missing)
        self.assertIsNone(world.target_record_scalar(controller.entity, self.rein.body))
        self.assertIs(world.target_record_scalar(self.rein.body, actor.entity), NotImplemented)
        world.mount_messages(isolated)
        instance.initialized = True
        receiver = instance.state(definition_graph.node(29).state)
        receiver.begin()
        sender_instance = isolated.create(make_graph(0xF106, [
            node(0, "STU_38EE1100", state=0, fields={
                "m_gameMessage": receiver.node.field("m_gameMessage"),
                "m_target": {"$": "STU_9B213168", "m_targets": {"$": "STU_B5A0CAF0"}},
            }),
        ], states=(0,)), init=False)
        sender_instance.initialized = True
        sender = sender_instance.state(0)
        sender.begin()
        self.assertTrue(any(event.kind == runtime.CHANGED and event.state is receiver for event in instance.queue))
        receiver.dependency_changed()
        self.assertIs(receiver.message_selected, sender)
        receive_state = instance.states[definition_graph.node(32).state]
        self.assertTrue(any(event.state is receive_state and event.op == runtime.BEGIN for event in instance.queue))
        with patch.object(type(world.messages.host.manager), "select", side_effect=AssertionError("no selection")):
            before = snapshot_digest(export_component_vm(isolated))
        sender.end(False)
        receiver.dependency_changed()
        self.assertFalse(any(event.state is receive_state and event.op == runtime.BEGIN for event in instance.queue))
        self.assertNotEqual(snapshot_digest(export_component_vm(isolated)), before)
        world.messages.host.retire()
        self.assertFalse(world.messages.host.manager.channels)
        isolated.destroy(instance)
        isolated.destroy(sender_instance)
        self.advance(120, buttons=0)
        self.assertEqual(controller.frame, initial_frame + 120)
        self.assertIsNone(controller.fault)
        self.assertTrue(entry_instance.states[entry_instance.graph.node(29).state].active)
        packet = message_receive.Packet(0x0240000000000079, self.rein.body, self.rein.body,
                                        ((0x0A40000000000059, (expr.Entity(self.rein.body),)),))
        controller_entry = controller.instances[2]
        target_slot = controller_entry.find_var(expr.INSTANCE, 536)
        body = next(i for i in script.component.instances.values() if i.graph.index == 0x0E62)
        alias_slot = body.find_var(expr.INSTANCE, 10161)
        player_world = self.rein.body_script.world
        controller_clock = (controller.frame, controller.now)
        body_clock = (script.frame, script.component.now)
        selected = controller.entity_var(1650).value()
        self.assertIsInstance(selected, expr.Entity)
        self.assertFalse(self.match.queue_body_packet(packet, (controller.entity,), controller.world))
        self.assertTrue(self.match.queue_body_packet(packet, (controller.entity,), player_world))
        behavior = controller.instances[5]
        self.assertEqual(behavior.graph.index, 0x1158)
        behavior.run_node(behavior.graph.node(34), None)
        self.assertEqual(self.match._body_messages[-1][3].caller, controller.entity)
        self.assertIsNone(target_slot.value())
        self.assertIsNone(alias_slot.value())
        self.match.deliver_body_messages()
        self.assertEqual(target_slot.value(), (expr.Entity(self.rein.body),))
        self.assertEqual(alias_slot.value(), (expr.Asset(0x0C80000000000561),))
        self.assertEqual(body.find_var(expr.INSTANCE, 10162).value(), (expr.Asset(0x028000000000022D),))
        self.assertEqual(body.find_var(expr.INSTANCE, 14003).value(), (2.0,))
        self.assertEqual(controller.entity_var(1650).value(), selected)
        self.assertEqual((controller.frame, controller.now), controller_clock)
        self.assertEqual((script.frame, script.component.now), body_clock)
        self.rein.mover.yaw, self.rein.mover.pitch = aim(self.match.combat._eye(self.rein), (0, 2, 2))
        self.assertEqual(npc_control.start(self.match, self.rein, action='pin'), actor.entity)
        self.advance(1, buttons=0)
        self.assertTrue(self.match.combat.pinned(self.rein.body))

        stale = message_receive.Packet(packet.message, packet.caller, packet.instigator,
                                       ((0x0A40000000000059, (expr.Entity(self.other.body),)),))
        self.assertTrue(self.match.queue_body_packet(stale, (controller.entity,), player_world))
        behavior.run_node(behavior.graph.node(59), None)
        self.match.combat._damage(self.rein, target, 10000, False, None, self.run.frame)
        self.assertTrue(actor.dead)
        self.assertFalse(self.match.combat.pinned(self.rein.body))
        self.assertIsNone(script.assassin_controller)
        self.assertTrue(controller.owner_dead)
        self.assertTrue(controller.world.context.retired)
        self.assertTrue(controller.world.messages.retired)
        self.assertFalse(controller.world.messages.host.manager.channels)
        self.assertFalse(controller.world.records.entries)
        self.assertFalse(controller.instances)
        self.assertIsNone(self.match.message_component(controller.entity))
        self.assertIn(actor.entity, npc.remove_spawned(self.match))

        replacement = npc.spawn(self.match, definition, (0, 1, 2), team=actor.team)
        second = replacement.body_script.assassin_controller
        self.assertIsNotNone(second)
        self.assertNotEqual(second.entity, controller.entity)
        self.assertEqual(second.world.target.possession.current, replacement.entity)
        self.match.deliver_body_messages()
        self.assertEqual(target_slot.value(), (expr.Entity(self.rein.body),))
        self.assertEqual(alias_slot.value(), (expr.Asset(0x0C80000000000561),))
        self.assertIsNone(second.instances[2].find_var(expr.INSTANCE, 536).value())

        fault_message = 0x024000000000F107
        second.create(make_graph(0xF107, [
            node(0, "STUStatescriptEntryGameMessage", fields={
                "m_gameMessage": {"$": "STUConfigVarGameMessage", "m_gameMessage": f"0x{fault_message:016X}"},
                "m_outPlug": output([1, "m_inPlug"]),
            }),
            node(1, "STU_TEST_UNMODELED_CONTROLLER_ACTION"),
        ], entries=(0,)))
        fault_packet = message_receive.Packet(fault_message, self.rein.body, self.rein.body)
        self.assertTrue(self.match.queue_body_packet(fault_packet, (second.entity,), player_world))
        with self.assertLogs("ow174.game", level="ERROR"):
            self.match.deliver_body_messages()
        self.assertIsInstance(second.fault, runtime.UnsupportedScriptError)
        self.assertIs(replacement.body_script.assassin_controller, second)
        self.assertIsNone(replacement.body_script.component.fault)
        self.assertIsNone(self.match.message_component(second.entity))
        self.assertIn(replacement.entity, npc.remove_spawned(self.match))
        self.assertTrue(second.world.context.retired)
        self.assertFalse(second.instances)

    @unittest.skipUnless(CONTROLLER_BUNDLE.is_file(), 'authorized Assassin controller graphs required')
    def test_assassin_targeting_acquires_visible_enemy_and_reacquires_after_team_change(self):
        self.enterContext(patch.object(graph, 'GRAPHS_PATH', CONTROLLER_BUNDLE))
        self.addCleanup(fixtures.TalonInteractionTests.clear_graphs)
        fixtures.TalonInteractionTests.clear_graphs()
        definition = next(d for d in self.catalog.values() if d.name == 'Assassin')
        fixtures.HammerTests.setUp(self)
        for player in self.run.players:
            player.client = fixtures.NPCRecordingClient(player)
        self.match.game_map = replace(self.match.game_map, free_for_all=False)
        self.enemy.team = self.other.team = 1
        self.rein.team = 2
        self.place(self.enemy, (0, 1, 6))
        self.place(self.other, (1, 1, 6))
        self.place(self.rein, (0, 1, 0))
        fixtures.charge_wall(self, 2)
        actor = npc.spawn(self.match, definition, (0, 1, 5), team=1, yaw=-32768)
        controller = actor.body_script.assassin_controller
        slot = controller.entity_var(1650)
        self.advance(120, buttons=0)
        self.assertIsNone(controller.fault)
        self.assertFalse(expr.valid_entity(expr.to_entity(slot.value())))

        self.place(self.rein, (10050, 1, 4))
        self.advance(5, buttons=0)
        self.assertIsNone(controller.fault)
        self.assertFalse(expr.valid_entity(expr.to_entity(slot.value())))

        self.place(self.rein, (0, 1, 4))
        self.advance(5, buttons=0)
        self.assertIsNone(controller.fault)
        self.assertEqual(slot.value(), expr.Entity(self.rein.body))

        self.rein.team = actor.team
        self.advance(5, buttons=0)
        self.assertIsNone(controller.fault)
        self.assertFalse(expr.valid_entity(expr.to_entity(slot.value())))

        self.enemy.team = self.other.team = actor.team + 1
        self.place(self.enemy, (0, 1, 6))
        self.place(self.other, (0, 1, 9))
        self.advance(5, buttons=0)
        self.assertIsNone(controller.fault)
        eligible = {self.enemy.body, self.other.body}
        chosen = expr.to_entity(slot.value())
        self.assertIn(chosen, eligible)

        fight = self.match.combat
        fight._damage(actor, fight._target_entity(chosen), 10000, False, None, self.run.frame)
        self.advance(5, buttons=0)
        self.assertIsNone(controller.fault)
        self.assertEqual({expr.to_entity(slot.value())}, eligible - {chosen})

    @unittest.skipUnless(CONTROLLER_BUNDLE.is_file(), 'authorized Assassin controller graphs required')
    def test_assassin_controller_acquires_only_after_ice_wall_is_cancelled(self):
        self.enterContext(patch.object(graph, 'GRAPHS_PATH', CONTROLLER_BUNDLE))
        self.addCleanup(fixtures.TalonInteractionTests.clear_graphs)
        fixtures.TalonInteractionTests.clear_graphs()
        definition = next(d for d in self.catalog.values() if d.name == 'Assassin')
        fixtures.HammerTests.setUp(self)
        for player in self.run.players:
            player.client = fixtures.NPCRecordingClient(player)
        self.match.game_map = replace(self.match.game_map, free_for_all=False)
        self.rein.team, self.enemy.team, self.other.team = 0, 1, 1
        self.place(self.rein, (0, 1, 0))
        self.assertTrue(self.match.switch_hero(self.enemy, heroes()[defenses.MEI]))
        self.place(self.enemy, (0, 1, 7))
        self.place(self.other, (12, 1, 0))
        ice_wall.ReinhardtIceWallTests.wall(self, 2.5)
        fight = self.match.combat
        self.assertTrue(fight.walls.blocked((0, 2.55, 5), fight._eye(self.rein)))
        actor = npc.spawn(self.match, definition, (0, 1, 5), team=1, yaw=-32768)
        controller = actor.body_script.assassin_controller
        slot = controller.entity_var(1650)
        self.advance(40, buttons=0)
        self.assertIsNone(controller.fault)
        self.assertFalse(expr.valid_entity(expr.to_entity(slot.value())))
        self.tap(16, (0, 0))
        self.assertFalse(fight.walls.pieces)
        self.advance(8, buttons=0)
        self.assertIsNone(controller.fault)
        self.assertEqual(slot.value(), expr.Entity(self.rein.body))

    def select_assassin_attack(self):
        self.enterContext(patch.object(graph, 'GRAPHS_PATH', CONTROLLER_BUNDLE))
        self.addCleanup(fixtures.TalonInteractionTests.clear_graphs)
        fixtures.TalonInteractionTests.clear_graphs()
        selects = AssassinControllerWorld.selects_ai_behavior
        self.enterContext(patch.object(AssassinControllerWorld, 'selects_ai_behavior',
                                       lambda world, instance: instance.graph.index == 0x1158
                                       or selects(world, instance)))

    def spawn_near_selected_assassin(self):
        self.select_assassin_attack()
        fixtures.HammerTests.setUp(self)
        for player in self.run.players:
            player.client = fixtures.NPCRecordingClient(player)
        self.match.game_map = replace(self.match.game_map, free_for_all=False)
        self.rein.team, self.enemy.team, self.other.team = 0, 1, 1
        self.place(self.rein, (0, 1, 0))
        fixtures.charge_wall(self, 20)
        definition = next(d for d in self.catalog.values() if d.name == 'Assassin')
        return npc.spawn(self.match, definition, (0, 1, 2), team=1, yaw=-32768)

    @unittest.skipUnless(CONTROLLER_BUNDLE.is_file(), 'authorized Assassin controller graphs required')
    def test_selected_assassin_attack_graph_sends_close_range_windup_and_body_signal(self):
        actor = self.spawn_near_selected_assassin()
        body = actor.body_script.component
        controller = actor.body_script.assassin_controller
        attack = next(i for i in controller.instances.values() if i.graph.index == 0x1158)
        animation = next(i for i in body.instances.values() if i.graph.index == 0x0E62)
        slot = animation.find_var(expr.INSTANCE, 10161)
        self.assertIsNone(controller.fault)
        self.assertEqual(controller.entity_var(1650).value(), expr.Entity(self.rein.body))
        self.assertEqual(attack.find_var(expr.INSTANCE, 536).value(), expr.Entity(self.rein.body))
        self.assertEqual(attack.find_var(expr.INSTANCE, 9149).value(), 2.0)
        self.assertIs(attack.find_var(expr.INSTANCE, 13835).value(), True)
        self.assertIsNone(slot.value())

        self.advance(1, buttons=0)
        self.assertEqual(slot.value(), (expr.Asset(0x0C80000000000561),))
        category = animation.find_var(expr.INSTANCE, 10162)
        duration = animation.find_var(expr.INSTANCE, 14003)
        self.assertEqual(category.value(), (expr.Asset(0x028000000000022C),))
        self.assertEqual(duration.value(), (expr.f32(1.33),))
        self.advance(6, buttons=0)
        wait = attack.states[attack.graph.node(33).state]
        self.assertTrue(wait.active)
        self.assertEqual(wait.ms, 2000)
        self.assertEqual(category.value(), (expr.Asset(0x028000000000022D),))
        self.assertEqual(duration.value(), (2.0,))
        remaining = wait.start + wait.ms - controller.now
        frames = math.ceil(remaining / controller.frame_ms)
        self.advance(frames - 1, buttons=0)
        phase = body.entity_var(13401)
        self.assertTrue(wait.active)
        self.assertEqual(phase.value(), 2)
        self.advance(1, buttons=0)
        self.assertFalse(wait.active)
        self.assertEqual(phase.value(), 3)  # Original 13C3 receiver of the timed 09DA.
        self.assertIsNone(controller.fault)
        self.assertIsNone(body.fault)
        self.assertFalse(actor.attack_input)
        self.assertFalse(self.match.combat.pinned(self.rein.body))

    @unittest.skipUnless(CONTROLLER_BUNDLE.is_file(), 'authorized Assassin controller graphs required')
    def test_authored_assassin_phase_two_drives_pin_input_and_damage(self):
        actor = self.spawn_near_selected_assassin()
        script = actor.body_script
        controller = script.assassin_controller
        attack = next(i for i in controller.instances.values() if i.graph.index == 0x1158)
        fight = self.match.combat
        victim = fight.vitals_of(fight._target_entity(self.rein.body))
        self.assertEqual(controller.entity_var(1650).value(), expr.Entity(self.rein.body))
        self.advance(20, buttons=2)
        self.assertTrue(fight.barriers[self.rein.body].active)
        self.assertIsNone(controller.world.pin_input)

        attack.run_node(attack.graph.node(4), None)  # Explicitly select the authored phase 2.
        self.advance(2, buttons=2)
        self.assertIsNone(controller.fault)
        source = attack.states[attack.graph.node(12).state]
        self.assertIs(controller.world.pin_input, source)
        self.assertFalse(actor.firing)
        self.assertTrue(actor.attack_input)
        self.assertIn(36, script.component.held)
        self.assertTrue(fight.pinned(self.rein.body))
        self.assertEqual(victim.total, 480)
        self.assertFalse(fight.barriers[self.rein.body].active)
        with patch.object(AssassinControllerWorld, 'set_ai_button', side_effect=AssertionError('passive export')):
            self.assertEqual(snapshot_digest(export_component_vm(controller)),
                             snapshot_digest(export_component_vm(controller)))

        self.damage_phase(actor)
        health_before = victim.total
        self.advance(8, buttons=0)
        self.assertLess(victim.total, health_before)

        attack.run_node(attack.graph.node(97), None)  # Exit through the graph's phase switch.
        self.advance(2, buttons=2)
        self.assertIsNone(controller.fault)
        self.assertIsNone(controller.world.pin_input)
        self.assertNotIn(36, script.component.held)
        self.assertFalse(actor.attack_input)
        self.assertFalse(fight.pinned(self.rein.body))
        health_after = victim.total
        self.advance(20, buttons=2)
        self.assertEqual(victim.total, health_after)

    @unittest.skipUnless(CONTROLLER_BUNDLE.is_file(), 'authorized Assassin controller graphs required')
    def test_manual_primary_suspends_controller_pin_and_stop_rearms_contact(self):
        actor = self.spawn_near_selected_assassin()
        script = actor.body_script
        component = script.component
        controller = script.assassin_controller
        attack = next(i for i in controller.instances.values() if i.graph.index == 0x1158)
        listener = next(i for i in component.instances.values() if i.graph.index == 0x13B8)
        primary = listener.state(listener.graph.node(7).state)
        fight = self.match.combat
        victim = fight.vitals_of(fight._target_entity(self.rein.body))
        attack.run_node(attack.graph.node(4), None)
        self.advance(2, buttons=0)
        source = controller.world.pin_input
        self.assertIsNotNone(source)
        self.assertTrue(fight.pinned(self.rein.body))
        self.assertEqual(victim.total, 480)

        self.rein.mover.yaw, self.rein.mover.pitch = aim(fight._eye(self.rein), (0, 2, 2))
        self.assertEqual(npc_control.start(self.match, self.rein, action='primary'), actor.entity)
        self.advance(1, buttons=0)
        self.assertTrue(actor.attack_input)
        self.assertTrue(primary.active and primary.down)
        self.assertIn(20, component.held)
        self.assertNotIn(36, component.held)
        self.assertIsNone(component.world.manual_target)
        self.assertFalse(fight.pinned(self.rein.body))
        self.assertIs(controller.world.pin_input, source)
        self.assertTrue(source.active)
        self.advance(40, buttons=2)
        self.assertEqual(victim.total, 480)
        self.assertTrue(fight.barriers[self.rein.body].active)

        self.assertEqual(npc_control.stop(self.match), 1)
        self.advance(2, buttons=2)
        self.assertIs(controller.world.pin_input, source)
        self.assertFalse(primary.down)
        self.assertNotIn(20, component.held)
        self.assertIn(36, component.held)
        self.assertEqual(component.world.manual_target, self.rein.body)
        self.assertTrue(fight.pinned(self.rein.body))
        self.assertFalse(fight.barriers[self.rein.body].active)
        self.assertEqual(victim.total, 460)
        self.advance(20, buttons=0)
        self.assertEqual(victim.total, 460)
        self.assertIsNone(controller.fault)
        self.assertIsNone(component.fault)

    @unittest.skipUnless(CONTROLLER_BUNDLE.is_file(), 'authorized Assassin controller graphs required')
    def test_assassin_controller_pin_does_not_reuse_retired_victim_body(self):
        actor = self.spawn_near_selected_assassin()
        controller = actor.body_script.assassin_controller
        attack = next(i for i in controller.instances.values() if i.graph.index == 0x1158)
        fight = self.match.combat
        self.advance(20, buttons=0)
        old_body = self.rein.body
        old_vitals = fight.vitals_of(fight._target_entity(old_body))
        self.assertEqual(controller.entity_var(1650).value(), expr.Entity(old_body))
        attack.run_node(attack.graph.node(4), None)
        self.advance(2, buttons=0)
        self.assertTrue(fight.pinned(old_body))
        self.assertEqual(old_vitals.total, 480)

        hero = self.rein.hero
        self.assertTrue(self.match.switch_hero(self.rein, heroes()[SOLDIER]))
        self.assertIsNone(controller.world.pin_input)
        self.assertFalse(expr.valid_entity(expr.to_entity(controller.entity_var(1650).value())))
        self.assertFalse(expr.valid_entity(expr.to_entity(attack.find_var(expr.INSTANCE, 536).value())))
        with patch.object(self.rein.client, 'body_id_available', side_effect=lambda body: body == old_body):
            self.assertTrue(self.match.switch_hero(self.rein, hero))
        self.assertEqual(self.rein.body, old_body)
        self.place(self.rein, (0, 1, 0))
        new_vitals = fight.vitals_of(fight._target_entity(old_body))
        self.assertIsNot(old_vitals, new_vitals)
        for _ in range(65):
            self.advance(1, buttons=2)
            self.assertFalse(fight.pinned(old_body))
            self.assertEqual(new_vitals.total, 500)
        self.assertIsNone(controller.fault)
        self.assertEqual(old_vitals.total, 480)
        self.assertTrue(fight.barriers[old_body].active)
        self.assertEqual(controller.entity_var(1650).value(), expr.Entity(old_body))

    @unittest.skipUnless(CONTROLLER_BUNDLE.is_file(), 'authorized Assassin controller graphs required')
    def test_assassin_close_range_watch_needs_a_live_target_record_change(self):
        self.select_assassin_attack()
        definition = next(d for d in self.catalog.values() if d.name == 'Assassin')
        original = AssassinControllerWorld.target_record_scalar
        for record in (None, 0.0, 1.0):
            with self.subTest(record=record):
                fixtures.HammerTests.setUp(self)
                for player in self.run.players:
                    player.client = fixtures.NPCRecordingClient(player)
                self.place(self.enemy, (10, 1, 0))
                self.place(self.other, (0, 1, 0))
                self.place(self.rein, (-11, 1, 0))
                actor = npc.spawn(self.match, definition, (0, 1, 2), team=1, yaw=-32768)
                controller = actor.body_script.assassin_controller
                attack = next(i for i in controller.instances.values() if i.graph.index == 0x1158)
                self.assertIsNone(controller.fault)
                target = self.rein.body
                self.assertEqual(controller.entity_var(1650).value(), expr.Entity(target))
                self.advance(3, buttons=0)
                self.place(self.rein, (1, 1, 2))
                self.advance(4, buttons=0)
                wait = attack.states[attack.graph.node(74).state]
                watch = attack.states[attack.graph.node(75).state]
                self.assertTrue(wait.active and watch.active)
                self.assertEqual(wait.ms, 500)
                self.assertIsNone(watch.seen)
                fight = self.match.combat
                self.assertFalse(fight.pinned(target))

                def record_scalar(world, owner, candidate):
                    if (record is not None and world is controller.world
                            and (owner, candidate) == (controller.entity, target)):
                        return record
                    return original(world, owner, candidate)

                with patch.object(AssassinControllerWorld, 'target_record_scalar', record_scalar):
                    self.advance(3, buttons=0)
                    self.assertIsNone(controller.fault)
                    if record == 1.0:
                        self.assertEqual(attack.find_var(expr.INSTANCE, 59).value(), 2)
                        self.assertFalse(wait.active or watch.active)
                        self.assertTrue(controller.world.pin_input.active)
                        self.assertTrue(fight.pinned(target))
                        self.assertFalse(actor.firing)
                        self.assertEqual(fight.vitals_of(fight._target_entity(target)).total, 480)
                    else:
                        frames = math.ceil((wait.start + wait.ms - controller.now) / controller.frame_ms)
                        self.advance(frames - 1, buttons=0)
                        self.assertTrue(wait.active and watch.active)
                        self.assertIsNone(controller.world.pin_input)
                        self.advance(1, buttons=0)
                        self.assertIsNone(controller.fault)
                        self.assertEqual(attack.find_var(expr.INSTANCE, 59).value(), 3)
                        self.assertFalse(wait.active or watch.active)
                        self.assertFalse(fight.pinned(target))
                        self.assertFalse(actor.attack_input)
                        self.assertEqual(fight.vitals_of(fight._target_entity(target)).total, 500)

    @unittest.skipUnless(CONTROLLER_BUNDLE.is_file(), 'authorized Assassin controller graphs required')
    def test_selected_assassin_motion_request_faces_target_and_releases_on_death(self):
        self.select_assassin_attack()
        definition = next(d for d in self.catalog.values() if d.name == 'Assassin')
        actor, target, _ = self.scenario(definition, (0, 1, 2))
        controller = actor.body_script.assassin_controller
        attack = next(i for i in controller.instances.values() if i.graph.index == 0x1158)
        fight = self.match.combat
        chosen = fight._target_entity(expr.to_entity(controller.entity_var(1650).value()))
        self.assertIsNot(chosen.owner, self.rein)
        initial_yaw = actor.yaw
        self.assertIsNone(controller.world.motion_request)
        self.advance(1, buttons=0)
        self.assertIsNone(controller.fault)
        request = attack.states[attack.graph.node(2).state]
        facing = attack.states[attack.graph.node(102).state]
        self.assertIs(controller.world.motion_request, request)
        self.assertTrue(request.registered and facing.active)
        facing_yaw, _ = aim(actor.position, chosen.feet)
        self.assertEqual(actor.yaw, facing_yaw)
        self.assertNotEqual(actor.yaw, initial_yaw)
        self.assertEqual(actor.position, (0, 1, 2))

        fight.stuns[actor.entity] = self.match.tick + 20
        self.place(chosen.owner, (6, 1, 2))
        self.advance(2, buttons=0)
        self.assertEqual(actor.yaw, facing_yaw)
        fight.stuns.pop(actor.entity)
        self.advance(2, buttons=0)
        self.assertEqual(actor.yaw, 16384)
        with patch.object(AssassinControllerWorld, 'face_position', side_effect=AssertionError('passive export')):
            before = snapshot_digest(export_component_vm(controller))
            self.assertEqual(snapshot_digest(export_component_vm(controller)), before)
        self.assertIs(controller.world.motion_request, request)

        fight._damage(self.rein, target, 10000, False, None, self.run.frame)
        self.assertTrue(actor.dead)
        self.assertIsNone(actor.body_script.assassin_controller)
        self.assertIsNone(controller.world.motion_request)
        self.assertFalse(request.registered or facing.active)
        self.assertFalse(actor.attack_input)

    @unittest.skipUnless(CONTROLLER_BUNDLE.is_file(), 'authorized Assassin controller graphs required')
    def test_selected_assassin_motion_request_releases_at_windup_end(self):
        self.select_assassin_attack()
        definition = next(d for d in self.catalog.values() if d.name == 'Assassin')
        actor, _, _ = self.scenario(definition, (0, 1, 2))
        controller = actor.body_script.assassin_controller
        attack = next(i for i in controller.instances.values() if i.graph.index == 0x1158)
        self.advance(1, buttons=0)
        request = attack.states[attack.graph.node(2).state]
        wait = attack.states[attack.graph.node(36).state]
        self.assertEqual(wait.ms, 1330)
        remaining = wait.start + wait.ms - controller.now
        frames = math.ceil(remaining / controller.frame_ms)
        self.advance(frames - 1, buttons=0)
        self.assertIs(controller.world.motion_request, request)
        self.assertTrue(request.registered and wait.active)
        self.advance(1, buttons=0)
        self.assertIsNone(controller.fault)
        self.assertIsNone(controller.world.motion_request)
        self.assertFalse(request.registered or wait.active)
        self.assertEqual(attack.find_var(expr.INSTANCE, 59).value(), 3)
        self.assertFalse(actor.attack_input)

    def test_match_routes_messages_between_mounted_reinhardt_and_assassin_bodies(self):
        definition = next(d for d in self.catalog.values() if d.name == 'Assassin')
        actor, target, _ = self.scenario(definition, (0, 1, 2))
        script = actor.body_script
        body = next(i for i in script.component.instances.values() if i.graph.index == 0x0E62)
        player = self.rein.body_script
        probe_message = 0x024000000000F103
        listener = player.component.create(make_graph(0xF103, [
            node(0, 'STUStatescriptEntryGameMessage', fields={
                'm_gameMessage': {'$': 'STUConfigVarGameMessage', 'm_gameMessage': f'0x{probe_message:016X}'},
                'm_params': [{'m_B5051BCE': '0x0A400000000000A3', 'm_out_Var': {
                    '$': 'STUConfigVarDynamic', 'm_identifier': 77, 'm_60DB8F99': 0,
                }}],
            }),
        ], entries=(0,)))
        def read(instance, var):
            return instance.find_var(expr.INSTANCE, var).value()

        sender = script.component.create(make_graph(0xF104, [
            node(0, 'STUStatescriptActionSendGameMessage', fields={
                'm_gameMessage': {'$': 'STUConfigVarGameMessage', 'm_gameMessage': f'0x{probe_message:016X}'},
                'm_targets': expr.Entity(self.rein.body),
                'm_params': [{'m_B5051BCE': '0x0A400000000000A3',
                              'm_value': {'$': 'STUConfigVarInt', 'm_value': 17}}],
            }),
        ]))
        alias = expr.Asset(0x0C80000000000561)
        category = expr.Asset(0x028000000000022D)
        params = {0xA3: alias, 0x43F: category, 0x5A5: 2.0}
        clock = (script.frame, script.component.now, player.frame, player.component.now)
        player.world.send_message(actor.entity, 0x02400000000007C8, params, self.rein.body)
        self.assertFalse(self.match.queue_body_message(self.rein.body, probe_message, {}, actor.entity,
                                                       source_world=player.world))
        sender.run_node(sender.graph.node(0), None)
        self.assertIsNone(read(body, 10161))
        self.assertIsNone(read(listener, 77))
        self.match.deliver_body_messages()
        self.assertEqual([read(body, var) for var in (10161, 10162, 14003)], [alias, category, 2.0])
        self.assertEqual(read(listener, 77), 17)
        self.assertEqual((script.frame, script.component.now, player.frame, player.component.now), clock)

        player.world.send_message(actor.entity, 0x02400000000007C8, {0xA3: expr.Asset(42)}, self.rein.body)
        script.component.world.send_message(self.rein.body, probe_message, {0xA3: 99}, actor.entity)
        self.match.combat._damage(self.rein, target, 10000, False, None, self.run.frame)
        self.assertTrue(actor.dead)
        self.assertFalse(self.match.queue_body_message(self.rein.body, probe_message, {}, actor.entity))
        self.assertFalse(self.match.queue_body_message(actor.entity, probe_message, {}, self.rein.body))
        self.assertIn(actor.entity, npc.remove_spawned(self.match))
        replacement = npc.spawn(self.match, definition, (0, 1, 2), team=actor.team)
        self.assertNotEqual(replacement.entity, actor.entity)
        new_body = next(i for i in replacement.body_script.component.instances.values() if i.graph.index == 0x0E62)
        self.match.deliver_body_messages()
        self.assertEqual(read(body, 10161), alias)
        self.assertIsNone(read(new_body, 10161))
        self.assertEqual(read(listener, 77), 17)

    def test_typed_match_action_preserves_authored_assassin_message_bindings(self):
        definition = next(d for d in self.catalog.values() if d.name == 'Assassin')
        actor, _, _ = self.scenario(definition, (0, 1, 2))
        script = actor.body_script
        body = next(i for i in script.component.instances.values() if i.graph.index == 0x0E62)
        player = self.rein.body_script
        alias = expr.Asset(0x0C80000000000561)
        alternate = expr.Asset(0x0C80000000000572)
        category = expr.Asset(0x028000000000022D)

        def action(position, params):
            return node(position, 'STUStatescriptActionSendGameMessage', fields={
                'm_gameMessage': {'$': 'STUConfigVarGameMessage', 'm_gameMessage': '0x02400000000007C8'},
                'm_targets': expr.Entity(actor.entity),
                'm_8B34E9C3': expr.Entity(self.other.body),
                'm_params': [{'m_B5051BCE': key, 'm_value': value} for key, value in params],
            })

        alias_key = '0x0A400000000000A3'
        source = player.component.create(make_graph(0xF105, [
            action(0, [
                (alias_key, (alias, alternate)),
                ('0x0D800000000000A3', expr.Asset(42)),
                ('0x0A4000000000043F', category),
                ('0x0A400000000005A5', 2.0),
            ]),
            action(1, [(alias_key, alternate)]),
        ]))

        def send(instance, position):
            with patch.object(instance.component, "strict", True):
                instance.run_node(instance.graph.node(position), None)

        def read(var):
            return body.find_var(expr.INSTANCE, var).value()

        clock = (script.frame, script.component.now)
        send(source, 0)
        self.assertIsNone(read(10161))
        packet = self.match._body_messages[-1][3]
        self.assertIsInstance(packet, message_receive.Packet)
        self.assertEqual((packet.caller, packet.instigator), (self.rein.body, self.other.body))
        self.assertEqual(packet.params[:2], (
            (int(alias_key, 16), (alias, alternate)), (0x0D800000000000A3, (expr.Asset(42),)),
        ))
        self.match.deliver_body_messages()
        self.assertEqual([read(var) for var in (10161, 10162, 14003)],
                         [(alias, alternate), (category,), (2.0,)])

        npc_source = script.component.create(source.graph)
        send(npc_source, 1)
        self.assertEqual(self.match._body_messages[-1][3].caller, actor.entity)
        with patch("ow174.game.match.MAX_BODY_MESSAGES", 2):
            self.assertFalse(self.match.queue_body_packet(packet, (actor.entity, actor.entity), player.world))
            self.assertEqual(len(self.match._body_messages), 1)
        self.match.deliver_body_messages()
        self.assertEqual([read(var) for var in (10161, 10162, 14003)], [(alternate,), (category,), (2.0,)])
        self.assertEqual((script.frame, script.component.now), clock)

        send(source, 0)
        self.assertIn(actor.entity, npc.remove_spawned(self.match))
        replacement = npc.spawn(self.match, definition, (0, 1, 2), team=actor.team)
        self.match.deliver_body_messages()
        new_body = next(i for i in replacement.body_script.component.instances.values() if i.graph.index == 0x0E62)
        self.assertEqual(read(10161), (alternate,))
        self.assertIsNone(new_body.find_var(expr.INSTANCE, 10161).value())

    def test_health_queries_read_real_pin_damage_and_body_replacement(self):
        actor, _, health, victim = self.pin()
        fight = self.match.combat
        npc_instances = actor.body_script.component.instances.values()
        player_instances = self.rein.body_script.component.instances.values()
        npc_instance = next(i for i in npc_instances if i.graph.index == 0x1460)
        player_instance = next(i for i in player_instances if i.graph.index == 0x48)

        def read(instance, kind, entity, initial=None, *, pool=None):
            cfg = {'$': kind, 'm_entity': expr.Entity(entity)}
            if pool is not None:
                cfg['m_7E8B666C'] = {'$': 'STU_297E1A30', 'm_7E8B666C': pool}
            return instance.evaluate(cfg, initial=initial)

        old = self.rein.body
        pools = set(fight.vitals)
        for instance in (npc_instance, player_instance):
            self.assertEqual(read(instance, health_queries.CURRENT, old), victim.total)
            self.assertEqual(read(instance, health_queries.MAXIMUM, old), 500)
            self.assertEqual(read(instance, health_queries.CURRENT, actor.entity), health.total)
            self.assertEqual(read(instance, health_queries.MAXIMUM, actor.entity), 500)
            self.assertEqual(read(instance, health_queries.MAXIMUM, self.rein.entity), 0)
            for pool_kind in (0, 1, 2):
                self.assertEqual(read(instance, health_queries.FILTERED_CURRENT, old, pool=pool_kind),
                                 victim.now[pool_kind])
            self.assertEqual(read(instance, health_queries.FILTERED_CURRENT, self.rein.entity, pool=0), 0)
        self.assertEqual(set(fight.vitals), pools)

        self.assertTrue(self.match.switch_hero(self.rein, self.rein.hero))
        self.assertNotEqual(self.rein.body, old)
        missing = object()
        self.assertIs(read(npc_instance, health_queries.CURRENT, old, missing), missing)
        self.assertIs(read(npc_instance, health_queries.FILTERED_CURRENT, old, missing, pool=0), missing)
        self.assertEqual(read(npc_instance, health_queries.CURRENT, self.rein.body), 500)
        self.assertEqual(read(npc_instance, health_queries.MAXIMUM, self.rein.body), 500)

    def test_typed_controller_target_initial_hit_delay_and_damage_clock_preserve_primary_slot(self):
        actor, _, _, victim = self.pin()
        world = actor.body_script.component.world
        self.assertEqual(world.ai_weapon_button(0), 20)
        self.assertEqual(npc_control.button(actor), 36)
        self.assertIs(type(world.entity_var(world.manual_controller_descriptor.entity, 1650)), expr.Entity)
        instance = next(i for i in actor.body_script.component.instances.values() if i.graph.index == 0x1157)
        selected = instance.evaluate(instance.graph.node(51).field('m_value'))
        self.assertEqual(selected, expr.Entity(self.rein.body))
        self.advance(20, buttons=1)
        self.assertTrue(self.match.combat.pinned(self.rein.body))
        self.assertEqual(victim.total, 480)
        self.assertEqual(self.rein.body_script.buttons, 0)
        state = self.damage_phase(actor)
        before = victim.total
        self.advance(10, buttons=1)
        self.assertAlmostEqual(before - victim.total, 8.0, places=5)
        self.assertEqual(state.last_applied, actor.body_script.component.now)
        self.assertTrue(self.match.combat.pinned(self.rein.body))

    def test_pin_lowers_actual_barrier_and_rejects_throttle_jump_and_attack(self):
        actor, _, _, victim = self.pin(shield=True)
        barrier = self.match.combat.barriers[self.rein.body]
        self.assertFalse(barrier.active)
        self.assertEqual(barrier.current, 1600)
        before = tuple(self.rein.mover.position)
        self.run.frame += 1
        self.match.update(self.run.frame * .016, self.run.frame)
        self.rein.take_commands([Command(self.run.frame, forward=127, right=127, buttons=0x25)])
        self.assertAlmostEqual(math.dist(before, self.rein.mover.position), 0, places=3)
        self.assertFalse(self.rein.mover.crouched)
        self.assertEqual(self.rein.body_script.buttons, 0)
        self.assertEqual(victim.total, 480)
        self.assertTrue(actor.attack_input)

    def test_assassin_pin_outlasts_a_real_barrier_break_lockout(self):
        definition = next(d for d in self.catalog.values() if d.name == 'Assassin')
        actor, _, _ = self.scenario(definition, (2, 1, 0))
        fight = self.match.combat
        self.match.game_map = replace(self.match.game_map, free_for_all=False)
        self.enemy.team = actor.team
        self.rein.team = 1 - actor.team
        self.place(self.enemy, (0, 1, 8))
        self.advance(20, buttons=2)
        shield = fight.barriers[self.rein.body]
        victim = fight.vitals_of(fight._target_entity(self.rein.body))
        self.assertTrue(shield.active)
        self.assertEqual(shield.current, 1600)
        angles = aim(fight._eye(self.enemy), fight._eye(self.rein))
        for _ in range(2000):
            self.run.step({self.enemy: (*angles, 1), self.rein: (0, 0, 2)})
            if not shield.active:
                break
        self.assertEqual(shield.current, 0)
        self.assertFalse(shield.active)
        self.assertEqual(victim.total, 500)
        break_deadline = shield.broken_until

        while fight.now + 1 < break_deadline:
            self.advance(1, buttons=2)
        angles = aim(fight._eye(self.rein), (2, 2, 0))
        self.rein.mover.yaw, self.rein.mover.pitch = angles
        self.assertEqual(npc_control.start(self.match, self.rein, action='pin'), actor.entity)
        self.advance(1, buttons=2, yaw=angles[0], pitch=angles[1])
        self.assertTrue(fight.pinned(self.rein.body))
        self.assertEqual(victim.total, 480)
        self.assertEqual(shield.broken_until, break_deadline)
        while fight.now <= break_deadline + combat.TICK:
            self.advance(1, buttons=2)
        self.assertTrue(fight.pinned(self.rein.body))
        self.assertFalse(shield.active)
        self.assertGreater(shield.current, 0)
        self.assertEqual(shield.broken_until, break_deadline)

        self.assertEqual(npc_control.stop(self.match), 1)
        before = victim.total
        self.advance(12, buttons=2)
        self.assertFalse(fight.pinned(self.rein.body))
        self.assertTrue(shield.active)
        self.assertEqual(victim.total, before)
        angles = aim(fight._eye(self.enemy), fight._eye(self.rein))
        recovered = shield.current
        for _ in range(30):
            self.run.step({self.enemy: (*angles, 1), self.rein: (0, 0, 2)})
            if shield.current < recovered:
                break
        self.assertEqual(shield.current, recovered - 20)
        self.assertEqual(victim.total, before)

    def test_assassin_pin_interrupts_active_deflect_and_grasp(self):
        cases = ((deflection.GENJI, 0x03A3, 10, 16), (sigma_grasp.SIGMA, sigma_grasp.GRAPH, 1, 8))
        for hero, index, state_index, button in cases:
            with self.subTest(hero=hero):
                definition = next(d for d in self.catalog.values() if d.name == 'Assassin')
                actor, _, source_health = self.scenario(definition, (0, 1, 2))
                self.assertTrue(self.match.switch_hero(self.rein, heroes()[hero]))
                self.place(self.rein, (0, 1, 0))
                self.advance(80, buttons=0)
                self.advance(1, buttons=button)
                active = defenses.active_state(self.rein, hero, index, state_index)
                self.assertIsNotNone(active)
                fight = self.match.combat
                victim = fight.vitals_of(fight._target_entity(self.rein.body))
                before, source_before = victim.total, source_health.total
                self.rein.mover.yaw, self.rein.mover.pitch = aim(fight._eye(self.rein), (0, 2, 2))
                self.assertEqual(npc_control.start(self.match, self.rein, action='pin'), actor.entity)
                self.advance(1, buttons=0)
                self.assertTrue(fight.pinned(self.rein.body))
                self.assertFalse(active.active)
                self.assertEqual(victim.total, before - 25)
                self.assertEqual(source_health.total, source_before)
                self.assertEqual(victim.grasp_shields, 0)

    def test_projected_barrier_absorbs_initial_assassin_damage_without_preventing_pin(self):
        actor, _, _, victim = self.engage(shield=True)
        self.assertEqual(npc_control.stop(self.match), 1)
        fight = self.match.combat
        self.match.game_map = replace(self.match.game_map, free_for_all=False)
        self.rein.team, self.other.team, actor.team = 0, 0, 1
        self.assertTrue(self.match.switch_hero(self.other, heroes()[zarya_barrier.HERO]))
        self.place(self.other, (3, 1, 0))
        self.advance(20, buttons=2)
        angles = aim(fight._eye(self.other), (0, 2.3, 0))
        self.run.step({self.other: (*angles, 16), self.rein: (0, 0, 2)})
        bubble = fight.zarya_bubbles[self.rein.body]
        shield = fight.barriers[self.rein.body]
        self.assertTrue(shield.active)
        self.assertEqual(bubble.health, 200)
        self.assertFalse(fight.pinned(self.rein.body))

        self.rein.mover.yaw, self.rein.mover.pitch = aim(fight._eye(self.rein), (0, 2, 2))
        self.assertEqual(npc_control.start(self.match, self.rein, action='pin'), actor.entity)
        self.advance(1, buttons=2)
        self.assertTrue(fight.pinned(self.rein.body))
        self.assertFalse(shield.active)
        self.assertEqual(shield.current, 1600)
        self.assertEqual(victim.total, 500)
        self.assertEqual(bubble.health, 175)
        self.assertAlmostEqual(self.other.body_script.component.entity_var(87).value(), 5, places=4)

    def test_projected_barrier_absorbs_live_assassin_pin_damage_until_break(self):
        for source in ('active', 'recycled', 'killed'):
            with self.subTest(source=source):
                self.check_projected_barrier_pin_break(source)

    def check_projected_barrier_pin_break(self, source):
        actor, _, _, victim = self.pin(shield=True)
        fight = self.match.combat
        self.match.game_map = replace(self.match.game_map, free_for_all=False)
        self.rein.team, self.other.team, actor.team = 0, 0, 1
        self.assertTrue(self.match.switch_hero(self.other, heroes()[zarya_barrier.HERO]))
        self.place(self.other, (3, 1, 0))
        state = self.damage_phase(actor)
        shield = fight.barriers[self.rein.body]
        pin = (self.rein.body, actor.entity, 'pin')
        angles = aim(fight._eye(self.other), (0, 2.3, 0))
        self.run.step({self.other: (*angles, 16), self.rein: (0, 0, 2)})
        bubble = fight.zarya_bubbles[self.rein.body]
        self.assertEqual(bubble.source, self.other.body)
        self.assertIn(pin, fight.controls)
        self.assertTrue(state.active)
        self.assertFalse(shield.active)
        self.assertEqual(shield.current, 1600)

        old_body = self.other.body
        if source == 'recycled':
            generation = fight.replacement_ticks[old_body]
            self.assertTrue(self.match.switch_hero(self.other, heroes()[SOLDIER]))
            with patch.object(self.other.client, 'body_id_available', side_effect=lambda body: body == old_body):
                self.assertTrue(self.match.switch_hero(self.other, heroes()[zarya_barrier.HERO]))
            self.assertEqual(self.other.body, old_body)
            self.assertGreater(fight.replacement_ticks[old_body], generation)
            self.place(self.other, (3, 1, 0))
        elif source == 'killed':
            self.enemy.team = 1
            self.place(self.enemy, (6, 1, 0))
            caster = fight.vitals_of(fight._target_entity(old_body))
            caster.damage(caster.total - 1)
            angles = aim(fight._eye(self.enemy), fight._eye(self.other))
            self.run.step({self.enemy: (*angles, 1), self.rein: (0, 0, 2)})
            self.assertTrue(caster.dead)
            self.assertEqual(caster.killer, self.enemy.body)
        self.assertIs(fight.zarya_bubbles[self.rein.body], bubble)
        self.assertEqual(bubble.source_retired, source != 'active')
        self.assertIn(pin, fight.controls)

        before = victim.total
        pool = bubble.health
        energy = self.other.body_script.component.entity_var(87)
        before_energy = energy.value() or 0
        self.advance(10, buttons=2)
        self.assertAlmostEqual(bubble.health, pool - 16, places=4)
        self.assertAlmostEqual((energy.value() or 0) - before_energy, 3.2 if source == "active" else 0, places=4)
        self.assertEqual(victim.total, before)
        self.assertIn(pin, fight.controls)

        bubble.health = 1
        before_energy = energy.value() or 0
        self.advance(1, buttons=2)
        self.assertEqual(bubble.health, 0)
        self.assertAlmostEqual((energy.value() or 0) - before_energy, .2 if source == "active" else 0, places=4)
        self.assertEqual(victim.total, before)
        self.assertIn(pin, fight.controls)
        self.advance(1, buttons=2)
        self.assertAlmostEqual(victim.total, before - .8, places=4)
        self.assertIn(pin, fight.controls)
        self.assertTrue(state.active)
        self.assertEqual(npc_control.stop(self.match), 1)
        self.advance(20, buttons=2)
        self.assertNotIn(pin, fight.controls)
        self.assertTrue(shield.active)
        self.assertEqual(shield.current, 1600)
        self.assertAlmostEqual(victim.total, before - .8, places=4)

    def test_stop_releases_immediately_and_new_explicit_engagement_can_pin_again(self):
        actor, _, _, victim = self.pin()
        self.damage_phase(actor)
        npc_control.stop(self.match)
        frozen = victim.total
        self.assertFalse(self.match.combat.pinned(self.rein.body))
        self.assertIsNone(actor.body_script.component.world.manual_target)
        self.advance(80, buttons=0)
        self.assertEqual(victim.total, frozen)
        self.assertFalse(actor.body_script.component.held)
        self.rein.mover.yaw, self.rein.mover.pitch = aim(
            self.match.combat._eye(self.rein), (0, 2, 2))
        npc_control.start(self.match, self.rein, action='pin')
        self.advance(1, buttons=0)
        self.assertTrue(self.match.combat.pinned(self.rein.body))
        self.assertAlmostEqual(victim.total, frozen - 20)

    def test_range_wall_and_allied_team_deny_pin_before_any_damage(self):
        for reason in ('range', 'wall', 'ally'):
            with self.subTest(reason=reason):
                actor, _, _, victim = self.engage()
                if reason == 'range':
                    actor.position = (0, 1, 4)
                elif reason == 'wall':
                    fixtures.charge_wall(self, 1)
                else:
                    self.match.game_map = self.match.game_map.__class__(
                        **{**self.match.game_map.__dict__, 'free_for_all': False})
                    actor.team = self.rein.team = 0
                self.advance(100, buttons=0)
                self.assertEqual(victim.total, 500)
                self.assertFalse(self.match.combat.pinned(self.rein.body))
                self.assertFalse(actor.attack_input)

    def test_source_control_releases_own_pin_preserves_other_control_and_recovers(self):
        actor, target, _, victim = self.pin()
        fight = self.match.combat
        own = fight._target_entity(self.rein.body)
        fight.control(own, self.other.body, 'stun', .2)
        fight.control(target, self.other.body, 'stun', .5)
        self.assertFalse(fight.pinned(self.rein.body))
        self.assertTrue(fight.controlled(self.rein.body))
        frozen = victim.total
        self.advance(25, buttons=0)
        self.assertEqual(victim.total, frozen)
        self.assertFalse(actor.attack_input)
        self.assertFalse(fight.controlled(self.rein.body))
        for _ in range(100):
            self.advance(1, buttons=0)
            if fight.pinned(self.rein.body):
                break
        self.assertTrue(fight.pinned(self.rein.body))
        self.assertLess(victim.total, frozen)

    def test_cover_or_team_change_interrupts_live_pin_damage_and_restores_barrier(self):
        for reason in ('cover', 'team'):
            with self.subTest(reason=reason):
                actor, _, _, victim = self.pin(shield=True)
                state = self.damage_phase(actor)
                self.advance(10, buttons=2)
                self.assertLess(victim.total, 480)
                self.assertTrue(state.active)
                fight = self.match.combat
                barrier = fight.barriers[self.rein.body]
                self.assertFalse(barrier.active)
                frozen = victim.total

                if reason == 'cover':
                    fixtures.charge_wall(self, 1)
                else:
                    self.match.game_map = self.match.game_map.__class__(
                        **{**self.match.game_map.__dict__, 'free_for_all': False})
                    actor.team = self.rein.team
                self.advance(1, buttons=2)
                self.assertFalse(fight.pinned(self.rein.body))
                self.assertFalse(state.active)
                self.assertFalse(actor.attack_input)
                self.assertIsNone(actor.body_script.component.world.manual_target)
                self.advance(100, buttons=2)
                self.assertTrue(barrier.active)
                self.assertEqual(barrier.current, 1600)
                self.assertEqual(victim.total, frozen)

    def test_deploying_ice_wall_interrupts_live_pin_and_allows_reengagement(self):
        actor, _, _, victim = self.pin(shield=True)
        state = self.damage_phase(actor)
        fight = self.match.combat
        barrier = fight.barriers[self.rein.body]
        self.assertTrue(self.match.switch_hero(self.enemy, heroes()[defenses.MEI]))
        self.place(self.enemy, (0, 1, 5))
        angles = aim(fight._eye(self.enemy), (0, 1, 1))
        self.tap(16, angles)
        self.assertTrue(state.active)
        self.assertTrue(fight.pinned(self.rein.body))
        self.run.step({self.enemy: (*angles, 1), self.rein: (0, 0, 2)})
        self.assertTrue(fight.walls.pieces)
        frozen = victim.total
        self.advance(2, buttons=2)
        self.assertFalse(state.active)
        self.assertFalse(fight.pinned(self.rein.body))
        self.assertFalse(actor.attack_input)
        self.assertIsNone(actor.body_script.component.world.manual_target)
        self.advance(30, buttons=2)
        self.assertEqual(victim.total, frozen)
        self.assertTrue(barrier.active)
        self.assertEqual(barrier.current, 1600)
        self.tap(16, (0, 0))
        self.assertFalse(fight.walls.pieces)
        for _ in range(140):
            self.advance(1, buttons=2)
            if fight.pinned(self.rein.body):
                break
        self.assertTrue(fight.pinned(self.rein.body))
        self.assertLess(victim.total, frozen)

    def test_death_removal_target_departure_and_range_break_release_without_late_damage(self):
        for reason in ('death', 'remove', 'target_gone', 'range'):
            with self.subTest(reason=reason):
                actor, target, _, victim = self.pin()
                fight = self.match.combat
                if reason == 'death':
                    fight._damage(self.other, target, 10000, False, None, self.run.frame)
                elif reason == 'remove':
                    world = actor.body_script.component.world
                    self.assertEqual(npc.remove_spawned(self.match), (actor.entity,))
                    descriptor = world.manual_controller_descriptor.entity
                    self.assertIsNone(world.entity_var(descriptor, 1650))
                elif reason == 'target_gone':
                    self.rein.gone = True
                else:
                    actor.position = (0, 1, 4)
                self.advance(1, buttons=0)
                self.assertFalse(fight.pinned(self.rein.body))
                frozen = victim.total
                self.advance(100, buttons=0)
                self.assertEqual(victim.total, frozen)

    def test_hero_switch_discards_live_pin_and_starts_fresh_on_reacquisition(self):
        actor, _, _, old_vitals = self.pin(shield=True)
        damage = self.damage_phase(actor)
        self.advance(10, buttons=0)
        self.assertTrue(damage.active)
        fight = self.match.combat
        old_body = self.rein.body
        old_position = tuple(self.rein.mover.position)
        old_health = old_vitals.total

        self.assertTrue(self.match.switch_hero(self.rein, heroes()[SOLDIER]))
        replacement = self.rein.body
        self.assertNotEqual(replacement, old_body)
        self.assertFalse(fight.pinned(old_body))
        self.assertNotIn(old_body, fight.barriers)
        self.assertNotIn(old_body, fight.vitals)
        self.place(self.rein, (0, 1, 8))
        self.advance(20, buttons=0)
        vitals = fight.vitals_of(fight._target_entity(replacement))
        self.assertFalse(damage.active)
        self.assertFalse(fight.pinned(replacement))
        self.assertIsNone(actor.body_script.component.world.manual_target)
        self.assertEqual(vitals.total, 200)
        self.assertEqual(old_vitals.total, old_health)

        self.place(self.rein, old_position)
        self.advance(1, buttons=0)
        self.assertTrue(fight.pinned(replacement))
        self.assertEqual(actor.body_script.component.world.manual_target, replacement)
        self.assertEqual(vitals.total, 175)
        self.advance(20, buttons=0)
        self.assertFalse(damage.active)
        self.assertEqual(vitals.total, 175)
        self.assertEqual(old_vitals.total, old_health)

    def test_reused_victim_body_id_restarts_pin_with_a_fresh_damage_clock(self):
        for protected in (False, True):
            with self.subTest(protected=protected):
                self.check_reused_pin_victim(protected)

    def check_reused_pin_victim(self, protected):
        actor, _, _, old_vitals = self.pin()
        fight = self.match.combat
        old_body = self.rein.body
        generation = fight.replacement_ticks[old_body]
        if protected:
            self.match.game_map = replace(self.match.game_map, free_for_all=False)
            self.rein.team, self.other.team, actor.team = 0, 0, 1
            self.assertTrue(self.match.switch_hero(self.other, heroes()[zarya_barrier.HERO]))
            self.place(self.other, (3, 1, 0))
        old_damage = self.damage_phase(actor)
        if protected:
            angles = aim(fight._eye(self.other), (0, 2.3, 0))
            self.run.step({self.other: (*angles, 16)})
            bubble = fight.zarya_bubbles[old_body]
            self.advance(10, buttons=0)
            self.assertLess(bubble.health, 200)
            self.assertTrue(fight.pinned(old_body))
            bubble_health = bubble.health
            energy = self.other.body_script.component.entity_var(87)
            earned = energy.value()
            self.assertGreater(earned, 0)
        old_health = old_vitals.total
        hero = self.rein.hero
        position = tuple(self.rein.mover.position)

        self.assertTrue(self.match.switch_hero(self.rein, heroes()[SOLDIER]))
        self.assertNotEqual(self.rein.body, old_body)
        self.assertFalse(old_damage.active)
        self.assertFalse(actor.attack_input)
        self.assertFalse(fight.pinned(old_body))
        self.assertNotIn(old_body, fight.zarya_bubbles)
        with patch.object(self.rein.client, "body_id_available", side_effect={old_body}.__contains__):
            self.assertTrue(self.match.switch_hero(self.rein, hero))
        self.assertEqual(self.rein.body, old_body)
        self.assertGreater(fight.replacement_ticks[old_body], generation)
        self.place(self.rein, (0, 1, 8))
        new_vitals = fight.vitals_of(fight._target_entity(old_body))
        self.advance(20, buttons=0)
        self.assertEqual(new_vitals.total, 500)
        self.assertFalse(fight.pinned(old_body))
        self.assertNotIn(old_body, fight.zarya_bubbles)
        self.assertIsNone(actor.body_script.component.world.manual_target)
        self.place(self.rein, position)

        for _ in range(30):
            self.advance(1, buttons=0)
            self.assertFalse(old_damage.active)
            if fight.pinned(old_body):
                break
            self.assertEqual(new_vitals.total, 500)
        self.assertTrue(fight.pinned(old_body))
        self.assertEqual(new_vitals.total, 480)
        self.advance(20, buttons=0)
        self.assertEqual(new_vitals.total, 480)
        self.assertEqual(old_vitals.total, old_health)
        if protected:
            self.assertEqual(bubble.health, bubble_health)
            self.assertEqual(energy.value(), earned)
            self.assertNotIn(old_body, fight.zarya_bubbles)

    def test_live_defenses_deny_assassin_pin_and_allow_fresh_contact_afterward(self):
        cases = (
            (defenses.ORISA, 8, 0, 20),
            (defenses.REAPER, 8, 0, 25),
            (defenses.MEI, 8, 2, 25),
            (defenses.ZENYATTA, 32, 0, 25),
        )
        for hero, activate, cancel, initial_damage in cases:
            with self.subTest(hero=hero):
                actor, _, _, _ = self.pin()
                fight = self.match.combat
                self.assertTrue(self.match.switch_hero(self.rein, heroes()[hero]))
                self.place(self.rein, (0, 1, 8))
                self.advance(4, buttons=0)
                if activate == 32:
                    script = self.rein.body_script
                    script.set_ult_charge(script.component.entity_var(338).value())
                self.advance(1, buttons=activate)
                self.assertTrue(defenses.control_immune(self.rein))
                component = actor.body_script.component
                vitals = fight.vitals_of(fight._target_entity(self.rein.body))
                before = vitals.total
                self.place(self.rein, (0, 1, 0))
                for frame in range(400):
                    if not defenses.control_immune(self.rein):
                        break
                    self.assertFalse(fight.pinned(self.rein.body))
                    self.assertFalse(actor.attack_input)
                    self.assertNotIn(36, component.held)
                    self.assertIsNone(component.world.manual_target)
                    self.assertEqual(vitals.total, before)
                    self.advance(1, buttons=cancel if frame == 35 else 0)

                self.assertFalse(defenses.control_immune(self.rein))
                if cancel:
                    self.assertIn(frame, range(36, 41))
                self.advance(2, buttons=0)
                self.assertTrue(fight.pinned(self.rein.body))
                self.assertEqual(component.world.manual_target, self.rein.body)
                self.assertEqual(vitals.total, before - initial_damage)
                self.advance(20, buttons=0)
                self.assertEqual(vitals.total, before - initial_damage)

    def test_damage_clock_and_typed_target_are_causal_storage_without_evaluation(self):
        from ow174.game.body_vm_snapshot import export_component_vm, snapshot_digest
        from ow174.game.script import runtime

        actor, _, _, _ = self.pin()
        state = self.damage_phase(actor)
        component = actor.body_script.component
        with patch.object(runtime.Instance, 'evaluate', side_effect=AssertionError('no query execution')):
            before = snapshot_digest(export_component_vm(component))
            state.last_applied += 1
            self.assertNotEqual(snapshot_digest(export_component_vm(component)), before)
            state.last_applied -= 1
            self.assertEqual(snapshot_digest(export_component_vm(component)), before)
            component.world.manual_target = None
            self.assertNotEqual(snapshot_digest(export_component_vm(component)), before)


    def test_actual_other_reinhardt_charge_captures_attacker_and_releases_its_victim(self):
        from ow174.game import combat
        from ow174.game.content import heroes

        actor, _, _, _ = self.pin()
        self.match.switch_hero(self.other, heroes()[combat.REINHARDT])
        self.place(self.other, (5, 1, 2))
        self.advance(20, buttons=0)
        self.run.step({self.other: (-16384, 0, 8)})
        for _ in range(80):
            self.run.step({self.other: (-16384, 0, 0)})
            if self.match.combat.pinned(actor.entity):
                break
        self.assertTrue(self.match.combat.pinned(actor.entity))
        self.assertFalse(self.match.combat.pinned(self.rein.body))
        self.assertIsNone(actor.body_script.component.world.manual_target)
        self.assertFalse(actor.attack_input)
        self.match.combat.charge_blocked(self.other, impact=False)
        self.assertFalse(self.match.combat.pinned(actor.entity))

    def test_allied_earthshatter_rescues_pinned_reinhardt_until_control_expires(self):
        from ow174.game import combat
        from ow174.game.content import heroes

        actor, _, _, victim = self.pin()
        self.match.game_map = self.match.game_map.__class__(
            **{**self.match.game_map.__dict__, 'free_for_all': False})
        self.other.team = self.rein.team = 0
        actor.team = 1
        self.match.switch_hero(self.other, heroes()[combat.REINHARDT])
        self.place(self.other, (5, 1, 2))
        self.advance(20, buttons=0)
        self.other.body_script.set_ult_charge(1540)
        fight = self.match.combat
        self.assertTrue(fight.pinned(self.rein.body))
        self.run.step({self.other: (-16384, 0, 32)})
        for _ in range(85):
            self.run.step({self.other: (-16384, 0, 0)})
            if fight.controlled(actor.entity):
                break
        self.assertTrue(fight.controlled(actor.entity))
        self.assertFalse(fight.pinned(self.rein.body))
        self.assertFalse(actor.attack_input)
        self.assertIsNone(actor.body_script.component.world.manual_target)
        after_rescue = victim.total
        self.advance(100, buttons=0)
        self.assertTrue(fight.controlled(actor.entity))
        self.assertEqual(victim.total, after_rescue)
        for _ in range(160):
            self.advance(1, buttons=0)
            if fight.pinned(self.rein.body):
                break
        self.assertFalse(fight.controlled(actor.entity))
        self.assertTrue(fight.pinned(self.rein.body))
        self.assertLess(victim.total, after_rescue)

    def test_allied_hammer_hit_within_pin_range_does_not_release_victim(self):
        from dataclasses import replace

        from ow174.game import combat

        actor, _, health, victim = self.pin()
        fight = self.match.combat
        self.match.game_map = replace(self.match.game_map, free_for_all=False)
        self.rein.team, self.other.team, actor.team = 0, 0, 1
        start = (0, 1, 2.98)
        actor.position = start
        self.assertTrue(self.match.switch_hero(self.other, heroes()[combat.REINHARDT]))
        self.place(self.other, (0.8, 1, 0.3))
        damage = self.damage_phase(actor)
        self.assertTrue(fight.pinned(self.rein.body))
        before = health.total
        yaw, pitch = aim(fight._eye(self.other), (0, 2.55, start[2]))
        self.run.step({self.other: (yaw, pitch, 1)})
        for _ in range(45):
            self.run.step({self.other: (yaw, pitch, 0)})
        self.assertFalse(actor.dead)
        self.assertEqual(health.total, before - 85)
        self.assertNotEqual(actor.position, start)
        x, y, z = actor.position
        pin_distance = math.dist((x, y + combat.EYE, z), fight._eye(self.rein))
        self.assertLess(pin_distance, 3)
        self.assertTrue(fight.pinned(self.rein.body))
        self.assertTrue(damage.active)
        self.assertEqual(actor.body_script.component.world.manual_target, self.rein.body)
        victim_after_hit = victim.total
        self.advance(10, buttons=0)
        self.assertAlmostEqual(victim_after_hit - victim.total, 8, places=5)

    def test_allied_fire_strike_kills_assassin_and_ends_pin_damage(self):
        from ow174.game import combat
        from ow174.game.content import heroes

        actor, target, health, victim = self.pin()
        self.match.game_map = self.match.game_map.__class__(
            **{**self.match.game_map.__dict__, 'free_for_all': False})
        self.other.team = self.rein.team = 0
        actor.team = 1
        self.match.switch_hero(self.other, heroes()[combat.REINHARDT])
        self.place(self.other, (5, 1, 2))
        self.advance(20, buttons=0)
        fight = self.match.combat
        fight._damage(self.other, target, health.total - 100, False, None, self.run.frame)
        self.assertTrue(fight.pinned(self.rein.body))
        self.run.step({self.other: (-16384, 0, 16)})
        for _ in range(90):
            self.run.step({self.other: (-16384, 0, 0)})
            if actor.dead:
                break
        self.assertTrue(actor.dead)
        self.assertEqual(health.total, 0)
        self.assertFalse(fight.pinned(self.rein.body))
        self.assertIsNone(actor.body_script.component.world.manual_target)
        after_rescue = victim.total
        self.advance(100, buttons=0)
        self.assertEqual(victim.total, after_rescue)
        self.assertTrue(actor.body_script.component.owner_dead)

    def test_manual_action_target_and_clock_are_in_full_server_storage(self):
        from test_server_snapshot import ServerSnapshotTests

        from ow174.game.script import runtime
        from ow174.game.server_snapshot import snapshot_digest

        case = ServerSnapshotTests()
        session, _, players = case.scene()
        player = players[0]
        x, y, z = player.mover.position
        definition = next(d for d in self.catalog.values() if d.name == 'Assassin')
        actor = npc.spawn(player.match, definition, (x, y, z + 2), team=1)
        world = actor.body_script.component.world
        instance = next(i for i in actor.body_script.component.instances.values() if i.graph.index == 0x1157)
        state = instance.state(instance.graph.node(15).state)
        with patch.object(runtime.Instance, 'evaluate', side_effect=AssertionError('no query execution')):
            before = snapshot_digest(case.capture(session))
            for obj, key, value in ((actor, 'attack_action', 'pin'),
                                    (world, 'manual_target', player.body), (state, 'last_applied', 1)):
                old = getattr(obj, key)
                setattr(obj, key, value)
                self.assertNotEqual(snapshot_digest(case.capture(session)), before)
                setattr(obj, key, old)
                self.assertEqual(snapshot_digest(case.capture(session)), before)


    def test_actual_pin_damage_kills_victim_then_cleans_up_without_duplicate_effects(self):
        actor, _, _, victim = self.pin(shield=True)
        old_body = self.rein.body
        for _ in range(700):
            self.advance(1, buttons=0)
            if victim.dead:
                break
            self.assertTrue(self.match.combat.pinned(self.rein.body))
        self.assertTrue(victim.dead)
        self.assertEqual(victim.killer, actor.entity)
        self.advance(2, buttons=0)
        self.assertFalse(self.match.combat.pinned(self.rein.body))
        self.assertFalse(actor.attack_input)
        self.assertIsNone(actor.body_script.component.world.manual_target)
        frozen = victim.total
        self.advance(60, buttons=0)
        self.assertEqual(victim.total, frozen)
        fixtures.TalonInteractionTests.assert_reinhardt_respawns_with_fresh_barrier(self, old_body)


    def test_switching_to_primary_keeps_authored_slot_and_retires_pin(self):
        actor, _, _, victim = self.pin()
        self.rein.mover.yaw, self.rein.mover.pitch = aim(self.match.combat._eye(self.rein), (0, 2, 2))
        npc_control.start(self.match, self.rein, action='primary')
        self.assertFalse(self.match.combat.pinned(self.rein.body))
        self.assertIsNone(actor.body_script.component.world.manual_target)
        self.advance(1, buttons=0)
        self.assertEqual(npc_control.button(actor), 20)
        self.assertIn(20, actor.body_script.component.held)
        self.assertNotIn(36, actor.body_script.component.held)
        component = actor.body_script.component
        listener = next(i for i in component.instances.values() if i.graph.index == 0x13B8)
        button = listener.state(listener.graph.node(7).state)
        self.assertEqual(button.button_id(), 20)
        self.assertTrue(button.active and button.down)
        health = victim.total
        self.advance(240, buttons=0)
        self.assertEqual(victim.total, health)
        self.assertFalse(self.match.combat.pinned(self.rein.body))
        self.assertTrue(button.down)

        self.rein.mover.yaw, self.rein.mover.pitch = aim(self.match.combat._eye(self.rein), (0, 2, 2))
        self.assertEqual(npc_control.start(self.match, self.rein, action='pin'), actor.entity)
        self.advance(1, buttons=0)
        self.assertFalse(button.down)
        self.assertTrue(self.match.combat.pinned(self.rein.body))
        self.assertEqual(victim.total, health - 20)


if __name__ == '__main__':
    unittest.main()
