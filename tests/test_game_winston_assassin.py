"""Actual Assassin target/controller-script/health core; physical pin/AI stays OPEN."""

import os
import unittest
from pathlib import Path
from unittest.mock import patch

import test_game_winston_npc as fixtures
from test_game_mover import wall

from ow174.game import npc, winston
from ow174.game.body_vm_snapshot import export_component_vm, snapshot_digest
from ow174.game.collision import World
from ow174.game.commands import Command
from ow174.game.script import expr, filter_condition, graph, nodes


class AssassinBoundaryTests(unittest.TestCase):
    @classmethod
    def clear_graphs(cls):
        fixtures.WinstonNpcTests.clear_graphs()

    @classmethod
    def restore_graphs(cls):
        cls.graph_patch.stop()
        cls.clear_graphs()

    @classmethod
    def setUpClass(cls):
        folder = os.environ.get('ORPHEUS_PVE_NPC_DATA')
        catalog = os.environ.get('ORPHEUS_PVE_NPC_CATALOG')
        if not folder or not catalog:
            raise unittest.SkipTest('requires real Assassin controller inputs; acceptance OPEN')
        cls.definition = next(d for d in npc.load_catalog(Path(catalog)).values() if d.name == 'Assassin')
        cls.graph_patch = patch.object(graph, 'GRAPHS_PATH', Path(folder) / 'game_graphs_174.json.gz')
        cls.graph_patch.start()
        cls.clear_graphs()
        cls.addClassCleanup(cls.restore_graphs)
        if cls.definition.controller_definition is None or graph.entity_body(0x0400000000001EBE) is None:
            raise unittest.SkipTest('requires associated1EBE input; Assassin acceptance OPEN')

    def scene(self):
        run, match, player, bot = fixtures.WinstonNpcTests.create(self, self.definition)
        distance = player.mover.data.stand_radius + bot.movement_data.stand_radius - .05
        bot.position = (0, 1, distance)  # positive attack fixtures actually touch
        script = bot.body_script
        root = script.controller
        behavior = next(i for i in root.instances.values() if i.graph.index == 0x13C2)
        return run, match, player, bot, script, root, behavior

    def step(self, run, bot, script, *, pressed=True):
        run.step({})
        script.advance_live(run.frame, bot.position, ability_input=(36, pressed))

    def test_actual_writer_and_victim_script_block_retargeting_while_latched_attack_damages(self):
        run, match, player, bot, script, root, behavior = self.scene()
        self.assertEqual(bot.controller_definition, 0x0400000000001EBE)
        self.assertEqual(script.component.entity_var(13401, create=False).value(), 1)
        script.set_manual_target(player.entity)
        with patch.object(behavior, 'write', wraps=behavior.write) as writes:
            for _ in range(150):
                self.step(run, bot, script)
        target_values = [call.args[1] for call in writes.call_args_list if call.args[0] == (1, 1650)]
        self.assertIn(expr.Entity(player.entity), target_values)
        self.assertIn(0, target_values)
        self.assertEqual(root.entity_var(1650).value(), 0)
        self.assertEqual(root.entity_var(14365).value(), 0)
        candidate = behavior.node_state(behavior.graph.node(1))
        self.assertIsInstance(candidate, nodes.AIBehavior)
        self.assertTrue(candidate.selected)
        body = next(i for i in script.component.instances.values() if i.graph.index == 0x1157)
        self.assertEqual(body.vars[536].value(), expr.Entity(player.entity))
        self.assertTrue(body.node_state(body.graph.node(15)).active)
        self.assertAlmostEqual(match.combat.vitals[player.body].total, 369.28)
        self.assertTrue(player.controller_component.entity_var(14695).value())
        self.assertTrue(match.combat.actions_blocked(player))
        self.assertEqual(player.client.messages(25001), [])
        self.assertIsNone(root.fault)
        self.assertIsNone(script.component.fault)

    def test_stop_deselects_actual_behavior_and_cleans_selected_subgraph(self):
        run, match, player, bot, script, root, behavior = self.scene()
        script.set_manual_target(player.entity)
        self.step(run, bot, script)
        candidate = behavior.node_state(behavior.graph.node(1))
        self.assertTrue(candidate.selected)
        script.set_manual_target()
        self.step(run, bot, script, pressed=False)
        self.assertFalse(candidate.selected)
        self.assertFalse(behavior.node_state(behavior.graph.node(4)).active)
        self.assertEqual(root.world.manual_target, 0)
        self.assertFalse(match.combat.actions_blocked(player))
        self.assertNotIn(36, script.component.held)

    def test_authored_possessed_body_criterion_can_deselect_even_with_manual_input(self):
        run, _match, player, bot, script, _root, behavior = self.scene()
        script.set_manual_target(player.entity)
        self.step(run, bot, script)
        candidate = behavior.node_state(behavior.graph.node(1))
        self.assertTrue(candidate.selected)
        script.component.set(13401, 0)
        self.step(run, bot, script)
        self.assertFalse(candidate.selected)
        self.assertNotEqual(script.controller.world.manual_target, 0)
        script.component.set(13401, 1)
        script.set_manual_target(player.entity)
        self.step(run, bot, script)
        self.assertTrue(candidate.selected)

    def test_real_paired_queries_and_declared_ability_input_validation(self):
        run, _match, player, bot, script, root, behavior = self.scene()
        body = next(i for i in script.component.instances.values() if i.graph.index == 0x1157)
        config = body.graph.node(51).field('m_value')['m_configVars'][0]
        self.assertEqual(body.evaluate(config), expr.Entity(root.entity))
        possession = {'$': 'STU_0AC15F78', 'm_entity': {'$': 'STU_B5A0CAF0'}}
        self.assertEqual(behavior.evaluate(possession), expr.Entity(bot.entity))
        for target in (True, -1, 2 ** 32, bot.entity, root.entity):
            with self.subTest(target=target), self.assertRaises(ValueError):
                script.set_manual_target(target)
        for value in ((255, True), (36, 1), (True, True), [36, True]):
            with self.subTest(value=value), self.assertRaises(ValueError):
                script.advance_live(run.frame + 1, bot.position, ability_input=value)
        script.set_manual_target(player.entity)
        self.step(run, bot, script)
        self.assertIn(36, script.component.held)

    def test_actual_controller_victim_flag_rejects_candidate_and_cleans_on_release_before_primal(self):
        run, match, player, bot, script, root, behavior = self.scene()
        condition = behavior.graph.node(10).field('m_condition')['m_filter']['m_condition']
        self.assertTrue(filter_condition.evaluate(behavior, condition, player.entity))
        script.set_manual_target(player.entity)
        self.step(run, bot, script)
        self.assertFalse(filter_condition.evaluate(behavior, condition, player.entity))
        self.step(run, bot, script, pressed=False)
        self.assertTrue(filter_condition.evaluate(behavior, condition, player.entity))
        player.body_script.set_ult_charge(player.body_script.component.entity_var(338).value())
        run.step({player: (0, 0, 32)})
        self.assertTrue(winston.primal_active(player.body_script.component))
        self.assertTrue(filter_condition.evaluate(behavior, condition, player.entity))
        self.assertEqual(root.parameter_scopes, [])
        self.assertNotIn(8, root.vars)
        self.assertNotIn(8, behavior.vars)
        script.set_manual_target(player.entity)
        for _ in range(150):
            self.step(run, bot, script, pressed=False)
            if root.entity_var(1650).value() == expr.Entity(player.entity):
                break
        self.assertEqual(root.entity_var(1650).value(), expr.Entity(player.entity))
        for _ in range(80):
            self.step(run, bot, script)
        # The victim flag rejects a fresh candidate. Original13401 may also
        # deselect tracking during a repeated attack, retaining its old output.
        self.assertFalse(filter_condition.evaluate(behavior, condition, player.entity))
        body = next(i for i in script.component.instances.values() if i.graph.index == 0x1157)
        self.assertEqual(body.vars[536].value(), expr.Entity(player.entity))
        self.assertAlmostEqual(match.combat.vitals[player.body].total, 958.88)

    def test_resource_recovery_duration_runs_original_override_and_target_cleanup(self):
        run, _match, player, bot, script, root, _behavior = self.scene()
        script.set_manual_target(player.entity)
        self.step(run, bot, script)
        self.step(run, bot, script, pressed=False)
        body = next(i for i in script.component.instances.values() if i.graph.index == 0x1157)
        recovery = body.node_state(body.graph.node(31))
        self.assertTrue(recovery.active)
        self.assertEqual(script.component.entity_var(13401).value(), 0)
        self.assertAlmostEqual(script.component.world.animation_duration(recovery), 1.4666666984558105)
        other = body.node_state(body.graph.node(44))
        self.assertIs(script.component.world.animation_duration(other), NotImplemented)
        for _ in range(90):  # a frame runs through its final millisecond
            self.step(run, bot, script, pressed=False)
        self.assertTrue(recovery.active)
        self.assertEqual(script.component.entity_var(13401).value(), 0)
        self.step(run, bot, script, pressed=False)
        self.assertFalse(recovery.active)
        self.assertEqual(recovery.ended_at - recovery.start, 1467)
        self.assertEqual(body.vars[536].value(), 0)
        self.assertEqual(script.component.entity_var(13401).links, [])
        self.step(run, bot, script, pressed=False)  # foreign-member poll observes original stack pop
        self.assertEqual(root.entity_var(1650).value(), expr.Entity(player.entity))

    def test_retired_controller_rejects_reactivation_and_foreign_target_output(self):
        run, _match, player, bot, script, root, _behavior = self.scene()
        script.set_manual_target(player.entity)
        self.step(run, bot, script)
        script.retire_controller()
        self.assertEqual(root.world.manual_target, 0)
        self.assertEqual(root.instances, {})
        script.set_manual_target(player.entity)
        for _ in range(10):
            self.step(run, bot, script)
        self.assertEqual(root.world.manual_target, 0)
        self.assertEqual(root.instances, {})

    def test_actual_victim_scope_cleans_on_release_retirement_removal_death_and_stun(self):
        for action in ('release', 'retire', 'remove', 'source_death', 'source_stun', 'victim_death'):
            with self.subTest(action=action):
                run, match, player, bot, script, _root, _behavior = self.scene()
                script.set_manual_target(player.entity)
                self.step(run, bot, script)
                self.assertTrue(match.combat.actions_blocked(player))
                self.assertTrue(player.controller_component.entity_var(14695).value())
                if action == 'release':
                    self.step(run, bot, script, pressed=False)
                elif action == 'retire':
                    script.retire_controller()
                elif action == 'remove':
                    npc.remove_spawned(match)
                elif action == 'source_stun':
                    match.combat.stuns[bot.entity] = match.tick + 50
                else:
                    victim = bot if action == 'source_death' else player
                    target = next(t for t in match.combat._targets() if t.owner is victim)
                    match.combat._damage(player if victim is bot else bot, target, 100000,
                                         False, None, match.tick)
                match.combat.assassins.update()
                self.assertFalse(match.combat.actions_blocked(player))
                self.assertIsNone(player.controller_component.entity_var(14695).value())
                self.assertFalse(match.combat.assassins.health_clock)
                self.assertIsNone(script.component.fault)
                self.assertIsNone(player.controller_component.fault)

    def test_friendly_target_does_not_start_victim_script_or_damage(self):
        run, match, player, bot, script, root, _behavior = self.scene()
        bot.team = player.team
        script.set_manual_target(player.entity)
        for _ in range(150):
            self.step(run, bot, script)
        # Original manual override can remain latched; the hostile effect
        # consumer must still reject this ordinary Match teammate.
        self.assertEqual(root.entity_var(1650).value(), expr.Entity(player.entity))
        self.assertFalse(player.controller_component.instances)
        self.assertFalse(match.combat.actions_blocked(player))
        self.assertEqual(match.combat.vitals[player.body].total, 500)

    def test_future_source_input_rejects_without_advancing_body_or_target_state(self):
        run, match, player, bot, script, _root, _behavior = self.scene()
        run.step({})  # establish the ordinary current-world health pools
        script.set_manual_target(player.entity)
        before = tuple(snapshot_digest(export_component_vm(c))
                       for c in (script.component, script.controller))
        with self.assertRaisesRegex(ValueError, 'current world tick'):
            script.advance_live(run.frame + 1000, bot.position, ability_input=(36, True))
        self.assertEqual(tuple(snapshot_digest(export_component_vm(c))
                               for c in (script.component, script.controller)), before)
        self.assertFalse(player.controller_component.instances)
        self.assertEqual(match.combat.vitals[player.body].total, 500)

    def test_controlled_winston_current_commands_cannot_move_fire_jump_or_primal(self):
        run, match, player, bot, script, _root, _behavior = self.scene()
        player.body_script.set_ult_charge(player.body_script.component.entity_var(338).value())
        script.set_manual_target(player.entity)
        self.step(run, bot, script)
        position = tuple(player.mover.position)
        health = match.combat.vitals[bot.entity].total
        for _ in range(30):
            run.frame += 1
            match.update(run.frame * .016, run.frame)
            player.take_commands([Command(run.frame, forward=127, buttons=1 | 4 | 32)])
            script.advance_live(run.frame, bot.position, ability_input=(36, True))
        self.assertEqual(tuple(player.mover.position), position)
        self.assertFalse(winston.primal_active(player.body_script.component))
        self.assertEqual(match.combat.vitals[bot.entity].total, health)
        self.assertFalse(player.body_script.component.held)

    def test_initial_health_entry_precedes_victim_transition_and_is_not_lost_or_repeated(self):
        run, match, player, bot, script, _root, _behavior = self.scene()
        script.set_manual_target(player.entity)
        seen = []
        damage = match.combat._damage

        def observe(*args, **kwargs):
            seen.append((args[2], bool(player.controller_component.instances)))
            return damage(*args, **kwargs)

        with patch.object(match.combat, '_damage', side_effect=observe):
            self.step(run, bot, script)
        self.assertEqual(seen, [(25, False)])
        body = next(i for i in script.component.instances.values() if i.graph.index == 0x1157)
        self.assertFalse(body.node_state(body.graph.node(25)).active)
        self.assertTrue(player.controller_component.entity_var(14695).value())
        self.assertEqual(match.combat.vitals[player.body].total, 480)
        for _ in range(20):
            script.advance_live(run.frame, bot.position, ability_input=(36, True))
            match.combat.assassins.update()
        self.assertEqual(match.combat.vitals[player.body].total, 480)
        for _ in range(62):
            self.step(run, bot, script)
        self.assertTrue(body.node_state(body.graph.node(15)).active)
        self.assertEqual(match.combat.vitals[player.body].total, 480)
        self.step(run, bot, script)
        self.assertAlmostEqual(match.combat.vitals[player.body].total, 479.36)
        self.step(run, bot, script, pressed=False)
        stopped = match.combat.vitals[player.body].total
        for _ in range(100):
            run.step({})
        self.assertEqual(match.combat.vitals[player.body].total, stopped)
        self.assertFalse(match.combat.assassins.health_clock)

    def test_idle_and_sparse_world_run_original_warmup_and_equal_continuous_damage(self):
        results = []
        for sparse in (False, True):
            run, match, player, bot, script, _root, _behavior = self.scene()
            script.set_manual_target(player.entity)
            self.step(run, bot, script)
            if sparse:
                run.frame += 120
                match.update(run.frame * .016, run.frame, frames=False)
            else:
                for _ in range(120):
                    run.step({})  # no further NPC input producer
            self.assertEqual(script.frame, run.frame - 1)
            self.assertIn(36, script.component.held)
            self.assertTrue(match.combat.actions_blocked(player))
            results.append(match.combat.vitals[player.body].total)
        self.assertAlmostEqual(results[0], 406.4)
        self.assertAlmostEqual(results[1], results[0])

    def test_initial_health_can_kill_before_original_victim_script_request(self):
        run, match, player, bot, script, _root, _behavior = self.scene()
        target = next(t for t in match.combat._targets() if t.owner is player)
        vitals = match.combat.vitals_of(target)
        vitals.now = dict.fromkeys(vitals.now, 0)
        vitals.now[0] = 20  # explicit injured-body fixture, no armour remains
        script.set_manual_target(player.entity)
        self.step(run, bot, script)
        self.assertTrue(vitals.dead)
        self.assertEqual(vitals.killer, bot.entity)
        self.assertFalse(player.controller_component.instances)
        self.assertFalse(match.combat.actions_blocked(player))
        self.assertFalse(match.combat.assassins.health_clock)

    def test_target_selection_alone_cannot_damage_or_pin_without_capsule_contact(self):
        for position in ((0, 1, 4), (0, 8, .5)):
            with self.subTest(position=position):
                run, match, player, bot, script, _root, _behavior = self.scene()
                bot.position = position
                script.set_manual_target(player.entity)
                for _ in range(80):
                    self.step(run, bot, script)
                self.assertEqual(match.combat.vitals[player.body].total, 500)
                self.assertFalse(player.controller_component.instances)
                self.assertFalse(match.combat.assassins.bindings)
                self.assertFalse(match.combat.actions_blocked(player))

    def test_actual_capsule_contact_boundary_and_static_wall_admission(self):
        for condition in ('touch', 'gap', 'wall'):
            with self.subTest(condition=condition):
                run, match, player, bot, script, _root, _behavior = self.scene()
                distance = player.mover.data.stand_radius + bot.movement_data.stand_radius
                bot.position = (0, 1, distance + (.001 if condition == 'gap' else 0))
                if condition == 'wall':
                    match.collision_world = World(wall(-4, distance / 2, 4, distance / 2, -2, 8))
                script.set_manual_target(player.entity)
                self.step(run, bot, script)
                accepted = condition == 'touch'
                self.assertEqual(match.combat.vitals[player.body].total, 480 if accepted else 500)
                self.assertEqual(match.combat.actions_blocked(player), accepted)
                self.assertEqual(bool(match.combat.assassins.bindings), accepted)

    def test_contact_loss_and_new_wall_end_original_hold_and_victim_scope(self):
        for condition in ('source_moves', 'victim_moves', 'wall'):
            with self.subTest(condition=condition):
                run, match, player, bot, script, _root, _behavior = self.scene()
                script.set_manual_target(player.entity)
                self.step(run, bot, script)
                self.assertEqual(match.combat.assassins.bindings[bot.entity], (player.entity, player.body))
                health = match.combat.vitals[player.body].total
                if condition == 'source_moves':
                    bot.position = (0, 1, 30)
                elif condition == 'victim_moves':
                    player.mover.position[:] = (0, 1, -30)
                    player.mover.body = None
                else:
                    # The attached child now has zero local offset. Nudge it
                    # within capsule contact before introducing intervening
                    # cover; coincident axes cannot have a wall between them.
                    bot.position = (0, 1, .5)
                    match.collision_world = World(wall(-4, bot.position[2] / 2,
                                                       4, bot.position[2] / 2, -2, 8))
                match.combat.assassins.update()
                self.assertFalse(match.combat.actions_blocked(player))
                self.assertIsNone(player.controller_component.entity_var(14695).value())
                self.assertNotIn(36, script.component.held)
                self.assertFalse(match.combat.assassins.bindings)
                self.assertFalse(match.combat.assassins.health_clock)
                for _ in range(80):
                    run.step({})
                self.assertEqual(match.combat.vitals[player.body].total, health)

    def test_victim_body_replacement_cannot_inherit_a_controller_target_pin(self):
        run, match, player, bot, script, _root, _behavior = self.scene()
        script.set_manual_target(player.entity)
        self.step(run, bot, script)
        old = player.body
        self.assertEqual(match.combat.assassins.bindings[bot.entity], (player.entity, old))
        self.assertTrue(match.switch_hero(player, player.hero))
        self.assertNotEqual(player.body, old)
        match.combat.assassins.update()
        self.assertFalse(match.combat.actions_blocked(player))
        self.assertFalse(player.controller_component.instances)
        self.assertFalse(match.combat.assassins.bindings)
        self.assertNotIn(36, script.component.held)


if __name__ == '__main__':
    unittest.main()
