"""Opt-in actual PVE bodies with explicit primary-fire input, not production AI.

Set ORPHEUS_PVE_NPC_DATA to the prepared read-only data directory. Missing
input leaves the matrix unaccepted; no training-bot substitution is permitted.
"""

import math
import os
import unittest
from dataclasses import replace
from itertools import product
from pathlib import Path
from unittest.mock import patch

from test_game_combat import SOLDIER, FakeClient, Run, aim, in_world
from test_game_mover import box, wall

from ow174.game import combat, hacks, heavy_charge, npc, npc_specials, protections, sniper_reposition, stats
from ow174.game.collision import World
from ow174.game.content import find_hero, heroes
from ow174.game.script import expr, graph, play, runtime

WINSTON = 0x02E0000000000009


class NpcClientFixture(FakeClient):
    """Collect real death snapshots at the transport boundary; no wire claim."""

    def __init__(self, player):
        super().__init__(player)
        self.death_snapshots = []

    def queue_bot_death(self, entity, snapshot):
        self.death_snapshots.append((entity, snapshot))

    def queue_bot_death_update(self, entity, snapshot):
        self.death_snapshots.append((entity, snapshot))

    def bot_stream(self, entity):
        return None  # this fixture has no acknowledged NPC observer/projectile presentation


class WinstonNpcTests(unittest.TestCase):
    def setUp(self):
        # Consume actual target/input graphs, never a fabricated weapon volley.
        if self._testMethodName == 'test_assassin_original_targeted_attack_damages_winston':
            run, match, player, bot = self.create(self.definitions['4'])
            bot.position = (0, 1, player.mover.data.stand_radius + bot.movement_data.stand_radius - .05)
            script = bot.body_script
            script.set_manual_target(player.entity)
            for _ in range(150):
                run.step({})
                self.emit_assassin_strike(run, match, bot)
            self.assassin_damage = 500 - match.combat.vitals[player.body].total

    @classmethod
    def setUpClass(cls):
        folder = os.environ.get('ORPHEUS_PVE_NPC_DATA')
        if not folder:
            raise unittest.SkipTest('actual PVE catalog not configured; acceptance remains OPEN')
        cls.folder = Path(folder)
        catalog = os.environ.get('ORPHEUS_PVE_NPC_CATALOG')
        cls.definitions = npc.load_catalog(Path(catalog) if catalog else cls.folder / 'pve_npcs_174.json')
        cls.graph_patch = patch.object(graph, 'GRAPHS_PATH', cls.folder / 'game_graphs_174.json.gz')
        cls.graph_patch.start()
        cls.clear_graphs()
        cls.addClassCleanup(cls.restore_graphs)

    @classmethod
    def clear_graphs(cls):
        for function in (graph.graph, graph._records, graph._data):
            function.cache_clear()

    @classmethod
    def restore_graphs(cls):
        cls.graph_patch.stop()
        cls.clear_graphs()

    def create(self, definition, *, control_caster=False, auto_target=False, auto_fire=False):
        run = Run(names=('Winston', 'ControlCaster')) if control_caster else Run()
        match, player = run.match, run.players[0]
        match.bots.clear()
        player.client = NpcClientFixture(player)
        match.switch_hero(player, heroes()[WINSTON])
        # Isolate damage charge from the still-unaccepted legacy passive rate.
        player.body_script.ult_seconds = 0
        player.mover.position[:] = [0, 1, 0]
        bot = npc.spawn(match, definition, (0, 1, 4), team=1, yaw=32768,
                        auto_target=auto_target, auto_fire=auto_fire)
        self.assertIsNotNone(bot.body_script)
        self.assertEqual(bot.hero, definition.hero)
        self.assertEqual(bot.body, definition.body)
        self.assertTrue(bot.retain_corpse)
        if control_caster:
            caster = run.players[1]
            caster.client = NpcClientFixture(caster)
            self.assertTrue(match.switch_hero(caster, heroes()[0x02E0000000000042]))
            caster.team = player.team
            caster.mover.position[:] = (3, 1, 1.5)
        return run, match, player, bot

    def test_actual_five_class_catalog(self):
        self.assertEqual({d.name for d in self.definitions.values()},
                         {'Trooper', 'Enforcer', 'Sniper', 'Assassin', 'Heavy Assault'})

    def test_authored_reaction_category_requires_mounted_enforcer_controller(self):
        definition = next(d for d in self.definitions.values() if d.name == 'Enforcer')
        self.assertEqual(definition.controller_definition, 0x040000000000227A)
        for configured in (True, False):
            with self.subTest(configured=configured):
                selected = definition if configured else replace(definition, controller_definition=None)
                run, _, player, bot = self.create(selected)
                component = bot.body_script.component
                reaction = next(i for i in component.instances.values() if i.graph.index == 0x142F)
                for _ in range(12):
                    run.step({})
                switch = reaction.states[reaction.graph.node(8).state]
                self.assertEqual(switch.current, configured)
                category = component.entity_var(9956, create=False)
                self.assertEqual(category.value() if category is not None else None,
                                 expr.Asset(0x0280000000000210) if configured else None)
                listener = next(i for i in component.instances.values() if i.graph.index == 0x0E20)
                routed = []

                def observe(instance, node, _caller, *, listener=listener, routed=routed):
                    if instance is listener and node.pos == 34:
                        routed.append(node.pos)
                    return True

                message = 0x024000000000077A
                payload = {0x0403: expr.Asset(0x0D800000000035AE), 0x0407: True}
                with patch.dict(runtime.HANDLERS, {'STU_DE66D3F5': observe}):
                    self.assertGreater(runtime.send_message(component, message, payload, player.body), 0)
                    component.run_now()
                self.assertEqual(routed, [34] if configured else [])

    def test_trooper_starts_and_retires_the_authored_nested_reaction(self):
        definition = next(d for d in self.definitions.values() if d.name == 'Trooper')
        _, _, _, bot = self.create(definition)
        component = bot.body_script.component
        root = next(i for i in component.instances.values() if i.graph.index == 0x0E62)
        binding = root.states[root.graph.node(120).state]
        played = binding.played
        child = next(i for i in component.instances.values() if i.graph.index == 0x13AC)
        self.assertTrue(binding.active and child.initialized)
        self.assertEqual(played.graph.index, 0x1231)
        self.assertIs(child.parent, played)
        self.assertEqual(child.parent_state, 7)
        binding.request(runtime.BEGIN)
        component.run_now()
        self.assertEqual([i for i in component.instances.values() if i.graph.index == 0x1231], [played])
        binding.request(runtime.ABORT)
        component.run_now()
        self.assertIsNone(binding.played)
        self.assertNotIn(played.id, component.instances)
        self.assertNotIn(child.id, component.instances)
        binding.request(runtime.BEGIN)
        component.run_now()
        self.assertIsNot(binding.played, played)
        self.assertTrue(any(i.graph.index == 0x13AC and i.parent is binding.played
                            for i in component.instances.values()))

    def test_talon_reaction_startup_binds_each_body_and_runs_the_delayed_listener(self):
        reactions = {'Trooper': 0x1231, 'Enforcer': 0x142F, 'Sniper': 0x14D1,
                     'Assassin': None, 'Heavy Assault': 0x0E54}
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                run, _, _, bot = self.create(definition)
                component = bot.body_script.component
                root = next(i for i in component.instances.values() if i.graph.index == 0x0E62)
                reaction = root.states[root.graph.node(120).state].played
                self.assertEqual(reaction.graph.index if reaction else None, reactions[definition.name])
                self.assertTrue(any(i.graph.index == 0x0E5E for i in component.instances.values()))
                self.assertFalse(any(i.graph.index == 0x0E20 for i in component.instances.values()))
                wait = root.states[root.graph.node(76).state]
                self.assertTrue(wait.active)
                for _ in range(12):
                    run.step({})
                self.assertFalse(wait.active)
                listener = root.states[root.graph.node(122).state].played
                self.assertEqual(listener.graph.index, 0x0E20)
                self.assertEqual(listener.start, wait.start + 125)

    def test_controlled_hit_message_uses_heavys_live_reaction_graph(self):
        definition = next(d for d in self.definitions.values() if d.name == 'Heavy Assault')
        run, match, player, bot = self.create(definition)
        component = bot.body_script.component
        for _ in range(12):
            run.step({})
        body_graphs = {i.graph.index: i for i in component.instances.values()}
        reaction = body_graphs[0x0E54]
        selector = body_graphs[0x14EB]
        health = match.combat.vitals[bot.entity].total
        message = 0x024000000000077A
        runtime.send_message(component, message, {0x0403: expr.Asset(0x0D800000000035AE)}, player.body)
        component.run_now()
        self.assertFalse(any(i in reaction.states for i in (reaction.graph.node(8).state,
                                                            reaction.graph.node(11).state)))
        runtime.send_message(component, message, {0x0403: expr.Asset(0x0D8000000000266F)}, player.body)
        component.run_now()
        self.assertEqual(reaction.states[reaction.graph.node(8).state].counter, 1)
        self.assertEqual(reaction.vars[9565].value(), expr.Asset(0x0D8000000000266F))
        self.assertFalse(any(key in selector.vars for key in (13233, 13234, 13235)))
        self.assertEqual(match.combat.vitals[bot.entity].total, health)

    def test_controlled_trooper_hit_action_wait_ends_on_idle_world_clock(self):
        definition = next(d for d in self.definitions.values() if d.name == 'Trooper')
        run, match, player, bot = self.create(definition)
        for _ in range(12):
            run.step({})
        script = bot.body_script
        script.advance_live(run.frame, bot.position)
        component = script.component
        listener = next(i for i in component.instances.values() if i.graph.index == 0x0E20)
        wait_node = listener.graph.node(27)
        self.assertFalse(npc_specials.clocked(bot))
        health = match.combat.vitals[bot.entity].total
        runtime.send_message(component, 0x024000000000077A,
                             {0x0403: expr.Asset(0x0D8000000000255F), 0x0407: True, 0x041C: (0.0, 0.0, 1.0)},
                             player.body)
        component.run_now()
        wait = listener.states[wait_node.state]
        self.assertTrue(wait.active)
        self.assertEqual(wait.ms, round(wait_node.field('m_timeout.m_value') * 1000))
        # The four tied native 079C.021 forward rows use the graph's random ordinal.
        self.assertIn(listener.vars[9687].value(), {expr.Asset(0x0C80000000000000 | alias)
                                                   for alias in (0x518, 0x4AF, 0x524, 0x523)})
        expiry = (wait.start + wait.ms) // component.frame_ms
        while run.frame < expiry:
            run.step({})
        self.assertTrue(wait.active)
        self.assertEqual(script.frame, expiry - 1)
        run.step({})
        self.assertFalse(wait.active)
        self.assertEqual(script.frame, expiry)
        self.assertFalse(npc_specials.clocked(bot))
        run.step({})
        self.assertEqual(script.frame, expiry)
        self.assertEqual(match.combat.vitals[bot.entity].total, health)

    def test_controlled_trooper_38db_dispatches_authored_alias_and_retires(self):
        definition = next(d for d in self.definitions.values() if d.name == 'Trooper')
        run, match, player, bot = self.create(definition)
        for _ in range(12):
            run.step({})
        script = bot.body_script
        script.advance_live(run.frame, bot.position)
        component = script.component
        listener = next(i for i in component.instances.values() if i.graph.index == 0x0E20)
        selector = next(i for i in component.instances.values() if i.graph.index == 0x13AC)
        selector_gate = selector.graph.node(9).field('m_condition')
        self.assertIs(selector.evaluate(selector_gate), True)
        wait_node, anim_node = (listener.graph.node(pos) for pos in (27, 23))
        health = match.combat.vitals[bot.entity].total
        params = {0x0403: expr.Asset(0x0D800000000038DB), 0x0407: False, 0x041C: (1.0, 0.0, 0.0)}
        runtime.send_instance_message(listener, npc_specials.TALON_HIT_MESSAGE, params, player.body)
        component.run_now()
        self.assertNotIn(wait_node.state, listener.states)
        self.assertNotIn(9687, listener.vars)

        params[0x0407] = True
        runtime.send_instance_message(listener, npc_specials.TALON_HIT_MESSAGE, params, player.body)
        component.run_now()
        alias = expr.Asset(0x0C800000000004B9)
        self.assertEqual(listener.vars[9687].value(), alias)
        self.assertEqual(listener.evaluate(anim_node.field('m_11E0A658')), alias)
        wait, anim = (listener.states[n.state] for n in (wait_node, anim_node))
        self.assertTrue(wait.active)
        self.assertEqual((wait.counter, anim.counter), (1, 1))
        self.assertEqual(anim_node.field('m_86701C3E.m_value'), 0)
        self.assertFalse(anim.active)
        self.assertIs(component.entity_var(888).value(), True)
        stack_node = listener.graph.node(24)
        stack = listener.states[stack_node.state]
        rank, _ = expr.stack_priority(stack_node.field('m_priority'))
        self.assertIn((stack, rank), [(link.state, link.priority) for link in component.entity_var(33).links])
        self.assertIs(selector.evaluate(selector_gate), False)
        expiry = (wait.start + wait.ms) // component.frame_ms
        while run.frame < expiry:
            run.step({})
        self.assertTrue(wait.active)
        run.step({})
        self.assertFalse(wait.active or anim.active)
        self.assertIsNone(component.entity_var(888).value())
        self.assertNotIn(stack, component.entity_var(33).stack_states())
        self.assertIs(selector.evaluate(selector_gate), True)
        self.assertEqual(match.combat.vitals[bot.entity].total, health)

    def test_controlled_enforcer_reaction_uses_authored_model_alias_and_fallback(self):
        definition = next(d for d in self.definitions.values() if d.name == 'Enforcer')
        for reaction, direction, alias in (
            (0x2596, (0, 0, -1), 0x04BA), (0x2596, (1, 0, 0), 0x056C),
            (0x2596, (-1, 0, 0), 0x056D), (0x2596, (0, 0, 1), 0x056E),
            (0x2639, (0, 0, 1), None),
        ):
            with self.subTest(reaction=reaction, direction=direction):
                run, match, player, bot = self.create(definition)
                bot.yaw = 0
                for _ in range(12):
                    run.step({})
                component = bot.body_script.component
                listener = next(i for i in component.instances.values() if i.graph.index == 0x0E20)
                health = match.combat.vitals[bot.entity].total
                payload = {0x0403: expr.Asset(0x0D80000000000000 | reaction), 0x0407: True, 0x041C: direction}
                runtime.send_instance_message(listener, npc_specials.TALON_HIT_MESSAGE, payload, player.body)
                component.run_now()

                selected = None if alias is None else expr.Asset(0x0C80000000000000 | alias)
                self.assertEqual(listener.vars[9687].value(), selected)
                anim_node = listener.graph.node(23)
                expected_anim = selected or expr.Asset(0x0C800000000004BA)
                self.assertEqual(listener.evaluate(anim_node.field('m_11E0A658')), expected_anim)
                wait = listener.states[listener.graph.node(27).state]
                self.assertTrue(wait.active)
                self.assertEqual((wait.counter, listener.states[anim_node.state].counter), (1, 1))
                self.assertEqual(match.combat.vitals[bot.entity].total, health)

    def test_lethal_winston_tesla_retires_enforcer_directional_reaction(self):
        definition = next(d for d in self.definitions.values() if d.name == 'Enforcer')
        run, match, player, bot = self.create(definition)
        for _ in range(12):
            run.step({})
        component = bot.body_script.component
        listener = next(i for i in component.instances.values() if i.graph.index == 0x0E20)
        payload = {0x0403: expr.Asset(0x0D80000000002596), 0x0407: True, 0x041C: (0, 0, -1)}
        runtime.send_instance_message(listener, npc_specials.TALON_HIT_MESSAGE, payload, player.body)
        component.run_now()
        wait = listener.states[listener.graph.node(27).state]
        self.assertTrue(wait.active)
        self.assertIs(component.entity_var(888).value(), True)
        expiry = (wait.start + wait.ms) // component.frame_ms

        target = match.combat.vitals[bot.entity]
        target.now = {combat.HEALTH_POOL: .5, combat.ARMOUR_POOL: 0, combat.SHIELDS_POOL: 0}
        run.step({player: (0, 0, 1)})
        self.assertTrue(bot.dead and component.owner_dead)
        self.assertLess(run.frame, expiry)
        self.assertFalse(wait.active)
        self.assertIsNone(component.entity_var(888).value())
        self.assertEqual(target.killer, player.body)
        self.assertEqual(player.stats.value(stats.ELIMINATIONS), 1)

    def test_sniper_2596_directional_action_requires_reaction_category(self):
        definition = next(d for d in self.definitions.values() if d.name == 'Sniper')
        self.assertEqual(definition.controller_definition, 0x0400000000001E7E)
        for mounted, direction, alias in (
            (False, (0, 0, -1), None), (True, (0, 0, -1), 0x04BA),
            (True, (1, 0, 0), 0x056C), (True, (-1, 0, 0), 0x056D),
            (True, (0, 0, 1), 0x056E),
        ):
            with self.subTest(mounted=mounted, direction=direction):
                selected = definition if mounted else replace(definition, controller_definition=None)
                run, _, player, bot = self.create(selected)
                bot.yaw = 0
                for _ in range(12):
                    run.step({})
                component = bot.body_script.component
                listener = next(i for i in component.instances.values() if i.graph.index == 0x0E20)
                wait_node = listener.graph.node(27)
                payload = {0x0403: expr.Asset(0x0D80000000002596), 0x0407: True, 0x041C: direction}
                expected_category = expr.Asset(0x0280000000000210) if mounted else None
                self.assertEqual(component.entity_var(9956).value(), expected_category)
                runtime.send_instance_message(listener, npc_specials.TALON_HIT_MESSAGE, payload, player.body)
                component.run_now()
                wait = listener.states.get(wait_node.state)
                self.assertEqual(wait is not None and wait.active, mounted)
                if mounted:
                    self.assertEqual(listener.vars[9687].value(), expr.Asset(0x0C80000000000000 | alias))

    def test_controlled_sniper_reaction_writes_selected_clip_duration(self):
        definition = next(d for d in self.definitions.values() if d.name == 'Sniper')
        for reaction, direction, durations in (
            (0x255F, (0, 0, 1), {0x052A: 0.9333334, 0x0529: 1.2666668}),
            (0x2560, (0, 0, -1), {0x04CC: 1.2666668, 0x051D: 0.7666667, 0x051E: 1.5333334, 0x04AE: 1.5000001}),
            (0x2560, (1, 0, 0), {None: 0.0}),
        ):
            with self.subTest(reaction=reaction, direction=direction):
                run, match, player, bot = self.create(definition)
                bot.yaw = 0
                for _ in range(12):
                    run.step({})
                component = bot.body_script.component
                listener = next(i for i in component.instances.values() if i.graph.index == 0x0E20)
                listener.write((expr.INSTANCE, 7310), 7.0)
                health = match.combat.vitals[bot.entity].total
                payload = {0x0403: expr.Asset(0x0D80000000000000 | reaction), 0x0407: False, 0x041C: direction}
                runtime.send_instance_message(listener, npc_specials.TALON_HIT_MESSAGE, payload, player.body)
                component.run_now()

                selected = listener.vars[9687].value()
                alias = selected.guid & 0xFFFF if selected is not None else None
                self.assertIn(alias, durations)
                self.assertEqual(listener.vars[7310].value(), expr.f32(durations[alias]))
                self.assertEqual(match.combat.vitals[bot.entity].total, health)

    def test_sniper_reaction_waits_expire_without_input_or_retire_on_death(self):
        definition = next(d for d in self.definitions.values() if d.name == 'Sniper')
        for reaction, direction, root, child in (
            (0x255F, (0, 0, 1), 87, 119),
            (0x2560, (0, 0, -1), 20, 120),
        ):
            for killed in (False, True):
                with self.subTest(reaction=reaction, killed=killed):
                    run, match, player, bot = self.create(definition)
                    bot.yaw = 0
                    for _ in range(12):
                        run.step({})
                    component = bot.body_script.component
                    listener = next(i for i in component.instances.values() if i.graph.index == 0x0E20)
                    payload = {
                        0x0403: expr.Asset(0x0D80000000000000 | reaction), 0x0407: False, 0x041C: direction,
                    }
                    runtime.send_instance_message(listener, npc_specials.TALON_HIT_MESSAGE, payload, player.body)
                    component.run_now()
                    wait, nested = (listener.states[listener.graph.node(pos).state] for pos in (root, child))
                    self.assertTrue(wait.active and nested.active)
                    self.assertTrue(npc_specials.clocked(bot))

                    if killed:
                        target = match.combat.vitals[bot.entity]
                        target.now = {combat.HEALTH_POOL: .5, combat.ARMOUR_POOL: 0, combat.SHIELDS_POOL: 0}
                        run.step({player: (0, 0, 1)})
                        self.assertTrue(bot.dead)
                        self.assertEqual(target.killer, player.body)
                    else:
                        # The idle driver advances the preceding world frame.
                        expiry = (wait.start + wait.ms) // component.frame_ms + 1
                        while run.frame < expiry - 1:
                            run.step({})
                        self.assertTrue(wait.active)
                        run.step({})

                    self.assertFalse(wait.active or nested.active)
                    self.assertFalse(npc_specials.clocked(bot))

    def test_sniper_reaction_interrupts_primed_primary_and_held_input_recovers(self):
        definition = next(d for d in self.definitions.values() if d.name == 'Sniper')
        for reaction, direction, root in ((0x255F, (0, 0, -1), 87), (0x2560, (0, 0, 1), 20)):
            with self.subTest(reaction=reaction):
                run, match, player, bot = self.create(definition)
                for _ in range(12):
                    run.step({})
                component = bot.body_script.component
                listener = next(i for i in component.instances.values() if i.graph.index == 0x0E20)
                for _ in range(100):
                    run.step({})
                    self.assertFalse(self.emit_primary(run, match, bot))
                self.assertTrue(component.held)

                payload = {
                    0x0403: expr.Asset(0x0D80000000000000 | reaction), 0x0407: False, 0x041C: direction,
                }
                runtime.send_instance_message(listener, npc_specials.TALON_HIT_MESSAGE, payload, player.body)
                component.run_now()
                wait = listener.states[listener.graph.node(root).state]
                self.assertTrue(wait.active)
                vitals = match.combat.vitals[player.body]
                before = vitals.total
                for _ in range(wait.ms // component.frame_ms + 3):
                    run.step({})
                    self.assertFalse(self.emit_primary(run, match, bot))
                    if not wait.active:
                        break
                self.assertFalse(wait.active)
                expiry = run.frame

                for _ in range(2000 // component.frame_ms - 1):
                    run.step({})
                    self.assertFalse(self.emit_primary(run, match, bot))
                self.assertEqual(vitals.total, before)
                run.step({})
                self.assertEqual(len(self.emit_primary(run, match, bot)), 1)
                self.assertEqual(run.frame - expiry, 2000 // component.frame_ms)
                for _ in range(2):
                    run.step({})
                    self.emit_primary(run, match, bot)
                self.assertEqual(before - vitals.total, 95)

    def test_controlled_talon_reaction_moves_the_actual_body_until_playback_ends(self):
        for name, graph_id, trigger, blocked in (
            ('Enforcer', 0x1506, 12, False), ('Heavy Assault', 0x14EB, 12, False),
            ('Enforcer', 0x1506, 12, True), ('Heavy Assault', 0x14EB, 12, True),
            ('Trooper', 0x13AC, 25, False), ('Trooper', 0x13AC, 25, True),
        ):
            with self.subTest(npc=name, blocked=blocked):
                definition = next(d for d in self.definitions.values() if d.name == name)
                run, match, _, bot = self.create(definition)
                floor = box(-30, 30, -1, 1, -30, 30)
                match.collision_world = World(floor + wall(-10, 4.85, 10, 4.85) if blocked else floor)
                bot.position, bot.yaw = (0, 1, 4), 0
                script = bot.body_script
                script.advance_live(run.frame, bot.position)
                component = script.component
                # Isolate the playback/mover boundary from the graph's entry and
                # event producer. The body, graph, clock and collision stay live.
                if graph_id == 0x13AC:
                    instance = next(i for i in component.instances.values() if i.graph.index == graph_id)
                    self.assertEqual(instance.parent.graph.index, 0x1231)
                else:
                    instance = component.create(graph.graph(graph_id), init=False)
                    instance.initialized = True
                instance.write((0, 681), expr.Vec3(0, 0, 4))
                instance.write((0, 13235), 0.25)
                instance.follow(instance.graph.node(trigger), 'm_806B068F', None)
                component.run_now()
                self.assertIsNotNone(npc_specials.reaction_movement(bot))

                health = match.combat.vitals[bot.entity].total
                for _ in range(8):
                    run.step({})
                if blocked:
                    self.assertLess(bot.position[2], 4.4)
                    self.assertAlmostEqual(bot.velocity[2], 0, delta=0.05)
                else:
                    self.assertGreater(bot.position[2], 4.4)
                    self.assertAlmostEqual(bot.velocity[2], 4, delta=0.05)
                self.assertEqual(match.combat.vitals[bot.entity].total, health)
                for _ in range(40):
                    run.step({})
                self.assertIsNone(npc_specials.reaction_movement(bot))
                self.assertNotIn(bot.entity, match.combat.impulses.npcs)

    def test_tesla_damages_all_five_actual_bodies_with_authored_base_pools(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                run, match, player, bot = self.create(definition)
                vitals = match.combat.vitals[bot.entity]
                before = vitals.total
                self.assertEqual(vitals.now[combat.SHIELDS_POOL], definition.shields)
                for _ in range(10):
                    run.step({player: (0, 0, 1)})
                self.assertAlmostEqual(before - vitals.total, 9.6)
                self.assertFalse(bot.dead)

    def test_one_tesla_tick_hits_player_and_five_npcs_without_duplicate_credit(self):
        trooper = next(d for d in self.definitions.values() if d.name == 'Trooper')
        run, match, player, first = self.create(trooper, control_caster=True)
        enemy = run.players[1]
        enemy.team = first.team
        enemy.mover.position[:] = (0.8, 1, 4)
        first.position = (-0.8, 1, 4)
        hostile = [first]
        for definition, x in zip((d for d in self.definitions.values() if d is not trooper),
                                 (-0.55, -0.3, 0.3, 0.55), strict=True):
            hostile.append(npc.spawn(match, definition, (x, 1, 4), team=enemy.team,
                                     yaw=32768, auto_target=False, auto_fire=False))
        ally = npc.spawn(match, trooper, (0, 1, 4), team=player.team,
                        yaw=32768, auto_target=False, auto_fire=False)
        occluded = npc.spawn(match, trooper, (2.8, 1, 4), team=enemy.team,
                             yaw=32768, auto_target=False, auto_fire=False)
        match.collision_world = World(box(-30, 30, -1, 1, -30, 30) + wall(1.0, 2.0, 1.8, 2.0))
        fight = match.combat
        targets = [fight.vitals[bot.entity] for bot in (*hostile, ally, occluded)]
        targets.append(fight.vitals_of(next(t for t in fight._targets() if t.owner is enemy)))
        before = [vitals.total for vitals in targets]
        component = player.body_script.component
        tesla = next(i for i in component.instances.values() if i.graph.index == 0x00FE)
        ammo, charge = tesla.vars[53], component.entity_var(342)
        initial_ammo = ammo.value()

        run.step({player: (0, 0, 1)})
        for initial, vitals, loss in zip(before, targets, (0.96,) * 5 + (0, 0, 0.96), strict=True):
            self.assertAlmostEqual(initial - vitals.total, loss)
        self.assertAlmostEqual(player.stats.value(stats.HERO_DAMAGE), 6 * .96)
        self.assertAlmostEqual(charge.value(), (5 * .5 + 1) * .96, places=4)
        self.assertLess(ammo.value(), initial_ammo)

        released_health = [vitals.total for vitals in targets]
        for _ in range(5):
            run.step({player: (0, 0, 0)})
        self.assertEqual([vitals.total for vitals in targets], released_health)
        self.assertAlmostEqual(player.stats.value(stats.HERO_DAMAGE), 6 * .96)
        self.assertAlmostEqual(charge.value(), (5 * .5 + 1) * .96, places=4)

        self.assertTrue(match.switch_hero(enemy, heroes()[WINSTON]))
        enemy.mover.position[:] = (0, 1, 6)
        enemy.mover.body = None
        run.step({enemy: (0, 0, 16)})
        for _ in range(4):
            run.step({})
        bubble = fight.barriers.actors[enemy.body]
        targets[-1] = fight.vitals_of(next(t for t in fight._targets() if t.owner is enemy))
        protected = [vitals.total for vitals in targets]
        damage_before, charge_before = player.stats.value(stats.HERO_DAMAGE), charge.value()
        bubble_before = bubble.health
        for _ in range(30):
            run.step({player: (0, 0, 1)})
            self.assertEqual([vitals.total for vitals in targets], protected)
            if bubble.health < bubble_before:
                break
        self.assertAlmostEqual(bubble_before - bubble.health, .96)
        self.assertEqual([vitals.total for vitals in targets], protected)
        self.assertEqual(player.stats.value(stats.HERO_DAMAGE), damage_before)
        self.assertEqual(charge.value(), charge_before)

        bubble.health = .5  # Leave a breaking hit for the regular beam path.
        run.step({player: (0, 0, 1)})
        self.assertLessEqual(bubble.health, 0)
        self.assertEqual([vitals.total for vitals in targets], protected)
        self.assertEqual(player.stats.value(stats.HERO_DAMAGE), damage_before)
        self.assertEqual(charge.value(), charge_before)
        run.step({player: (0, 0, 1)})
        for initial, vitals in zip(protected[:5], targets[:5], strict=True):
            self.assertAlmostEqual(initial - vitals.total, .96)
        self.assertEqual([vitals.total for vitals in targets[5:7]], protected[5:7])
        self.assertLess(targets[-1].total, protected[-1])

    def test_tesla_kills_injured_actual_npcs_and_keeps_corpses(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                run, match, player, bot = self.create(definition)
                vitals = match.combat.vitals[bot.entity]
                # An explicitly injured NPC, preserving its original maximum pools.
                vitals.now = {combat.HEALTH_POOL: 2.0, combat.ARMOUR_POOL: 0.0,
                              combat.SHIELDS_POOL: 0.0}
                with self.assertNoLogs('ow174.game', level='ERROR'):
                    for _ in range(3):
                        run.step({player: (0, 0, 1)})
                self.assertTrue(bot.dead)
                self.assertEqual(vitals.total, 0)
                self.assertEqual(vitals.killer, player.body)
                charge = player.body_script.component.entity_var(342)
                self.assertAlmostEqual(charge.value(), 1)
                self.assertAlmostEqual(player.stats.value(stats.HERO_DAMAGE), 2)
                self.assertEqual(player.stats.value(stats.ELIMINATIONS), 1)
                with self.assertNoLogs('ow174.game', level='ERROR'):
                    for _ in range(5):
                        run.step({player: (0, 0, 1)})
                    for _ in range(200):
                        run.step({})
                self.assertAlmostEqual(charge.value(), 1)
                self.assertAlmostEqual(player.stats.value(stats.HERO_DAMAGE), 2)
                self.assertEqual(player.stats.value(stats.ELIMINATIONS), 1)
                self.assertIn(bot, match.bots)
                self.assertIsNotNone(bot.body_script)
                self.assertTrue(any(entity == bot.entity for entity, _ in player.client.death_snapshots))
                self.assertFalse(any(who is bot for _, who in match.combat.respawns))

    def test_tesla_all_five_reject_range_rear_allies_and_static_occlusion(self):
        for definition in self.definitions.values():
            for case in ('range', 'rear', 'ally', 'wall'):
                with self.subTest(npc=definition.name, case=case):
                    run, match, player, bot = self.create(definition)
                    if case == 'range':
                        bot.position = (0, 1, 20)
                    elif case == 'rear':
                        bot.position = (0, 1, -4)
                    elif case == 'ally':
                        bot.team = player.team
                    else:
                        match.collision_world = World(box(-30, 30, -1, 1, -30, 30)
                                                      + wall(-10, 2, 10, 2))
                    vitals = match.combat.vitals[bot.entity]
                    before = vitals.total
                    for _ in range(10):
                        run.step({player: (0, 0, 1)})
                    self.assertEqual(vitals.total, before)
                    self.assertEqual(player.body_script.component.entity_var(342).value(), 0)

    def test_quick_melee_all_five_hits_once_and_rejects_misses(self):
        for definition in self.definitions.values():
            for case in ('hit', 'range', 'rear', 'ally', 'wall'):
                with self.subTest(npc=definition.name, case=case):
                    run, match, player, bot = self.create(definition)
                    bot.position = (0, 1, 1.5)
                    if case == 'hit':
                        match.collision_world = World(box(-30, 30, -1, 1, -30, 30))
                        bot.position = (0, 2.25, 1.5)
                    elif case == 'range':
                        bot.position = (0, 1, 8)
                    elif case == 'rear':
                        bot.position = (0, 1, -4)
                    elif case == 'ally':
                        bot.team = player.team
                    elif case == 'wall':
                        match.collision_world = World(box(-30, 30, -1, 1, -30, 30)
                                                      + wall(-10, .75, 10, .75))
                    vitals = match.combat.vitals[bot.entity]
                    before = vitals.total
                    initial = bot.position
                    run.step({player: (0, 0, 0x800)})
                    self.assertEqual(bot.entity in match.combat.impulses.npcs, case == 'hit')
                    if case == 'hit':
                        self.assertEqual(match.combat.impulses.npcs[bot.entity][1].velocity[2], 1)
                    for _ in range(40):
                        run.step({})
                    self.assertAlmostEqual(before - vitals.total, 30 if case == 'hit' else 0)
                    if case == 'hit':
                        self.assertGreater(bot.position[2], initial[2])
                    else:
                        self.assertEqual(bot.position, initial)

    def test_trooper_melee_reaction_tracks_actual_nano_boosted_damage(self):
        definition = next(d for d in self.definitions.values() if d.name == 'Trooper')
        for boosted in (False, True):
            with self.subTest(boosted=boosted):
                run, match, player, bot = self.create(definition, control_caster=boosted)
                for _ in range(12):
                    run.step({})
                if boosted:
                    self.nano_winston(run, match, player)
                    winston_vitals = match.combat.vitals[player.body]
                    health_before_shot = winston_vitals.total
                    for _ in range(150):
                        run.step({})
                        self.emit_primary(run, match, bot)
                        if winston_vitals.total < health_before_shot:
                            break
                    self.assertLess(winston_vitals.total, health_before_shot)
                    self.assertTrue(bot.body_script.component.held)
                match.collision_world = World(box(-30, 30, -1, 1, -30, 30))
                bot.position = (0, 2.25, 1.5)
                listener = next(i for i in bot.body_script.component.instances.values() if i.graph.index == 0x0E20)
                vitals = match.combat.vitals[bot.entity]
                health = vitals.total
                run.step({player: (0, 0, 0x800)})
                for _ in range(5):
                    run.step({})
                self.assertAlmostEqual(health - vitals.total, 45 if boosted else 30)
                self.assertEqual(9687 in listener.vars, boosted)
                if boosted:
                    self.assertIsInstance(listener.vars[9687].value(), expr.Asset)
                    wait = listener.states[listener.graph.node(27).state]
                    self.assert_trooper_primary_resumes_after_reaction(run, match, player, bot, listener, wait)

    def test_primal_255f_suppresses_and_releases_trooper_primary(self):
        definition = next(d for d in self.definitions.values() if d.name == 'Trooper')
        run, match, player, bot = self.create(definition)
        for _ in range(12):
            run.step({})
        winston_vitals = match.combat.vitals[player.body]
        before_fire = winston_vitals.total
        for _ in range(150):
            run.step({})
            self.emit_primary(run, match, bot)
            if winston_vitals.total < before_fire:
                break
        self.assertLess(winston_vitals.total, before_fire)
        self.assertTrue(bot.body_script.component.held)

        script = player.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        run.step({player: (0, 0, 32)})
        bot.position = (0, 1, 1.5)
        target = match.combat.vitals[bot.entity]
        before_punch = target.total
        component = bot.body_script.component
        listener = next(i for i in component.instances.values() if i.graph.index == 0x0E20)
        for _ in range(75):
            run.step({player: (0, 0, 1)})
            if target.total < before_punch:
                break
            self.emit_primary(run, match, bot)

        self.assertEqual(before_punch - target.total, 40)
        self.assertEqual(script.component.entity_var(342).value(), 0)
        hit_time = run.frame * component.frame_ms
        pending = [event for event in listener.queue if event.message is not None
                   and event.message[0] == npc_specials.TALON_HIT_MESSAGE]
        self.assertEqual([event.node.pos for event in pending], [4, 39])
        for event in pending:
            self.assertEqual(event.time, hit_time)
            self.assertEqual(event.message[1][0x0403], expr.Asset(0x0D8000000000255F))
            self.assertEqual(event.message[2], player.body)

        run.step({})
        wait = listener.states[listener.graph.node(27).state]
        self.assertEqual((wait.start, wait.counter), (hit_time, 1))
        self.assertIsInstance(listener.vars[9687].value(), expr.Asset)
        self.assert_trooper_primary_resumes_after_reaction(run, match, player, bot, listener, wait)

    def assert_trooper_primary_resumes_after_reaction(self, run, match, player, bot, listener, wait):
        winston_vitals = match.combat.vitals[player.body]
        before_fire = winston_vitals.total
        for _ in range(150):
            run.step({})
            self.assertFalse(self.emit_primary(run, match, bot))
        self.assertTrue(wait.active)
        self.assertEqual(winston_vitals.total, before_fire)

        run.step({})
        self.emit_primary(run, match, bot, held=False)
        expiry = (wait.start + wait.ms) // listener.component.frame_ms
        while run.frame <= expiry:
            run.step({})
        self.assertFalse(wait.active)
        run.step({})
        self.emit_primary(run, match, bot, held=False)
        bot.position = (0, 1, 4)
        for _ in range(150):
            run.step({})
            self.emit_primary(run, match, bot)
            if winston_vitals.total < before_fire:
                break
        self.assertLess(winston_vitals.total, before_fire)

    def test_second_actual_trooper_hit_replaces_reaction_deadline(self):
        definition = next(d for d in self.definitions.values() if d.name == 'Trooper')
        run, match, player, bot = self.create(definition, control_caster=True)
        for _ in range(12):
            run.step({})
        self.nano_winston(run, match, player)
        match.collision_world = World(box(-30, 30, -1, 1, -30, 30))
        component = bot.body_script.component
        listener = next(i for i in component.instances.values() if i.graph.index == 0x0E20)
        target = match.combat.vitals[bot.entity]
        wait_node = listener.graph.node(27)
        starts = []
        for index in range(2):
            bot.position = (0, 2.25, 1.5)
            health = target.total
            run.step({player: (0, 0, 0x800)})
            self.assertEqual(health - target.total, 45)
            hit_time = run.frame * component.frame_ms
            pending = [event for event in listener.queue if event.message is not None
                       and event.message[0] == npc_specials.TALON_HIT_MESSAGE]
            self.assertEqual([event.node for event in pending],
                             runtime.message_entries(listener, npc_specials.TALON_HIT_MESSAGE))
            self.assertEqual({event.time for event in pending}, {hit_time})
            run.step({})
            wait = listener.states[wait_node.state]
            self.assertEqual((wait.active, wait.counter, wait.start), (True, 1, hit_time))
            starts.append(wait.start)
            if index == 0:
                for _ in range(85):
                    run.step({})

        original_expiry, current_expiry = ((start + wait.ms) // component.frame_ms for start in starts)
        while run.frame <= original_expiry:
            run.step({})
        self.assertTrue(wait.active)
        self.assertIs(component.entity_var(888).value(), True)
        while run.frame <= current_expiry:
            run.step({})
        self.assertFalse(wait.active)
        self.assertIsNone(component.entity_var(888).value())
        self.assertAlmostEqual(player.stats.value(stats.HERO_DAMAGE), 90)

    def test_trooper_death_retires_active_winston_hit_reaction(self):
        definition = next(d for d in self.definitions.values() if d.name == 'Trooper')
        run, match, player, bot = self.create(definition, control_caster=True)
        for _ in range(12):
            run.step({})
        self.nano_winston(run, match, player)
        match.collision_world = World(box(-30, 30, -1, 1, -30, 30))
        bot.position = (0, 2.25, 1.5)
        fight = match.combat
        target = fight.vitals[bot.entity]
        target.now[combat.HEALTH_POOL] = 47
        component = bot.body_script.component
        listener = next(i for i in component.instances.values() if i.graph.index == 0x0E20)
        run.step({player: (0, 0, 0x800)})
        for _ in range(5):
            run.step({})
        self.assertEqual(target.total, 2)
        wait = listener.states[listener.graph.node(27).state]
        self.assertTrue(wait.active)
        self.assertIs(component.entity_var(888).value(), True)
        expiry = (wait.start + wait.ms) // component.frame_ms

        bot.position = (0, 1, 4)
        for _ in range(120):
            run.step({player: (0, 0, 1)})
            if bot.dead:
                break
        self.assertTrue(bot.dead)
        self.assertLess(run.frame, expiry)
        self.assertTrue(component.owner_dead)
        self.assertFalse(wait.active)
        self.assertIsNone(component.entity_var(888).value())
        self.assertFalse(component.shots)
        self.assertEqual(target.total, 0)
        self.assertEqual(target.killer, player.body)
        self.assertEqual(player.stats.value(stats.HERO_DAMAGE), 47)
        self.assertEqual(player.stats.value(stats.ELIMINATIONS), 1)
        while run.frame <= expiry:
            run.step({player: (0, 0, 1)})
        self.assertFalse(self.emit_primary(run, match, bot))
        self.assertEqual(player.stats.value(stats.HERO_DAMAGE), 47)
        self.assertEqual(player.stats.value(stats.ELIMINATIONS), 1)
        self.assertIn(bot, match.bots)

    def test_trooper_lethal_same_frame_discards_queued_hit_reaction(self):
        definition = next(d for d in self.definitions.values() if d.name == 'Trooper')
        run, match, player, bot = self.create(definition, control_caster=True)
        for _ in range(12):
            run.step({})
        self.nano_winston(run, match, player)
        ally = run.players[1]
        self.assertTrue(match.switch_hero(ally, heroes()[SOLDIER]))
        ally.mover.position[:] = (4, 1, 1.5)
        ally.mover.body = None
        match.collision_world = World(box(-30, 30, -1, 1, -30, 30))
        bot.position = (0, 2.25, 1.5)
        fight = match.combat
        target = fight.vitals[bot.entity]
        target.now[combat.HEALTH_POOL] = 47
        component = bot.body_script.component
        listener = next(i for i in component.instances.values() if i.graph.index == 0x0E20)
        yaw, pitch = aim(fight._eye(ally), (0, 3.25, 1.5))
        queued_at_death = []
        die = bot.body_script.die

        def observe_death(*args):
            queued_at_death.extend(event for event in listener.queue if event.message is not None
                                   and event.message[0] == npc_specials.TALON_HIT_MESSAGE)
            return die(*args)

        with patch.object(bot.body_script, 'die', side_effect=observe_death):
            run.step({player: (0, 0, 0x800), ally: (yaw, pitch, 1)})

        self.assertEqual(player.stats.value(stats.HERO_DAMAGE), 45)
        self.assertEqual(ally.stats.value(stats.HERO_DAMAGE), 2)
        self.assertEqual(target.killer, ally.body)
        self.assertTrue(bot.dead)
        self.assertEqual([event.node.pos for event in queued_at_death], [4, 39])
        self.assertEqual({event.time for event in queued_at_death}, {run.frame * component.frame_ms})
        self.assertNotIn(9565, listener.vars)
        self.assertNotIn(9687, listener.vars)
        self.assertNotIn(listener.graph.node(27).state, listener.states)

    def test_primal_all_five_reject_range_rear_allies_and_static_occlusion(self):
        for definition in self.definitions.values():
            for case in ('range', 'rear', 'ally', 'wall'):
                with self.subTest(npc=definition.name, case=case):
                    run, match, player, bot = self.create(definition)
                    bot.position = (0, 1, 1.5)
                    if case == 'range':
                        bot.position = (0, 1, 8)
                    elif case == 'rear':
                        bot.position = (0, 1, -4)
                    elif case == 'ally':
                        bot.team = player.team
                    else:
                        match.collision_world = World(box(-30, 30, -1, 1, -30, 30)
                                                      + wall(-10, .75, 10, .75))
                    initial = bot.position
                    before = match.combat.vitals[bot.entity].total
                    script = player.body_script
                    script.set_ult_charge(script.component.entity_var(338).value())
                    run.step({player: (0, 0, 32)})
                    for _ in range(75):
                        run.step({player: (0, 0, 1)})
                    self.assertEqual(match.combat.vitals[bot.entity].total, before)
                    self.assertEqual(bot.position, initial)
                    self.assertNotIn(bot.entity, match.combat.impulses.npcs)

    def test_primal_punch_damages_and_physically_moves_all_five_authored_bodies(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                self.assertIsNotNone(definition.mover, 'authored NPC mover catalog is required')
                run, match, player, bot = self.create(definition)
                bot.position = (0, 1, 1.5)
                self.assertIs(bot.movement_data, definition.mover)
                before = match.combat.vitals[bot.entity].total
                player.body_script.set_ult_charge(player.body_script.component.entity_var(338).value())
                run.step({player: (0, 0, 32)})
                with self.assertNoLogs('ow174.game', level='ERROR'):
                    for _ in range(75):
                        run.step({player: (0, 0, 1)})
                self.assertAlmostEqual(before - match.combat.vitals[bot.entity].total, 40)
                self.assertGreater(bot.position[2], 5)
                self.assertGreater(bot.position[1], 1)
                self.assertNotEqual(bot.movement(run.frame).velocity, (0, 0, 0))
                for _ in range(200):
                    run.step({})
                self.assertAlmostEqual(bot.position[1], 1)
                self.assertNotIn(bot.entity, match.combat.impulses.npcs)

    def test_alternating_primal_fists_damage_all_five_authored_bodies(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                run, match, player, bot = self.create(definition)
                vitals = match.combat.vitals[bot.entity]
                script = player.body_script
                script.set_ult_charge(script.component.entity_var(338).value())
                run.step({player: (0, 0, 32)})
                for _ in range(35):
                    run.step({})
                hits = []
                for _ in range(120):
                    # Keep the target in range between the two real swings.
                    bot.position = (0, 1, 1.5)
                    before = vitals.total
                    seen = set(match.combat.primal_punches.seen.get(player.body, ()))
                    run.step({player: (0, 0, 1)})
                    if vitals.total < before:
                        self.assertAlmostEqual(before - vitals.total, 40)
                        activation, = match.combat.primal_punches.seen[player.body] - seen
                        hits.append((run.frame, activation[1]))
                        match.combat.impulses.retire(bot.entity)
                    if len(hits) == 2:
                        break
                self.assertEqual(tuple(state for _, state in hits), (35, 36), hits)
                self.assertTrue(40 <= hits[1][0] - hits[0][0] <= 42)

    def test_one_primal_swing_damages_player_and_all_five_npcs_on_same_frame(self):
        trooper = next(definition for definition in self.definitions.values()
                       if definition.name == 'Trooper')
        run, match, player, first_bot = self.create(trooper, control_caster=True)
        enemy = run.players[1]
        enemy.team = first_bot.team
        enemy.mover.position[:] = (0.45, 1, 1.5)
        first_bot.position = (-0.45, 1, 1.5)
        enemies = [first_bot]
        for definition, x in zip((d for d in self.definitions.values() if d is not trooper),
                                 (-0.30, -0.15, 0.15, 0.30), strict=True):
            enemies.append(npc.spawn(match, definition, (x, 1, 1.5), team=enemy.team,
                                     yaw=32768, auto_target=False, auto_fire=False))
        ally = npc.spawn(match, trooper, (0, 1, 1.5), team=player.team,
                         yaw=32768, auto_target=False, auto_fire=False)
        fight = match.combat
        enemy_vitals = [fight.vitals[body.entity] for body in enemies]
        enemy_vitals.append(fight.vitals_of(next(target for target in fight._targets()
                                                if target.owner is enemy)))
        ally_vitals = fight.vitals[ally.entity]
        before = [vitals.total for vitals in enemy_vitals]
        ally_before = ally_vitals.total
        script = player.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        run.step({player: (0, 0, 32)})
        for _ in range(35):
            run.step({})

        for _ in range(75):
            run.step({player: (0, 0, 1)})
            losses = [initial - vitals.total for initial, vitals in zip(before, enemy_vitals, strict=True)]
            if any(losses):
                break
        self.assertEqual(losses, [40] * len(enemy_vitals))
        self.assertEqual(ally_vitals.total, ally_before)
        self.assertNotIn(ally.entity, fight.impulses.npcs)
        self.assertTrue(all(body.entity in fight.impulses.npcs for body in enemies))
        trooper_listener = next(i for i in first_bot.body_script.component.instances.values() if i.graph.index == 0x0E20)
        ally_listener = next(i for i in ally.body_script.component.instances.values() if i.graph.index == 0x0E20)
        activation, = fight.primal_punches.seen[player.body]
        self.assertIn(activation[1], (35, 36))
        for _ in range(10):
            run.step({})
        self.assertIsInstance(trooper_listener.vars[9687].value(), expr.Asset)
        wait = trooper_listener.states[trooper_listener.graph.node(27).state]
        self.assertTrue(wait.active)
        self.assertEqual(wait.counter, 1)
        self.assertNotIn(9687, ally_listener.vars)
        self.assertEqual([initial - vitals.total for initial, vitals in zip(before, enemy_vitals, strict=True)],
                         losses)

    def test_jump_launch_all_five_deals_one_charges_and_moves_real_bodies(self):
        for definition in self.definitions.values():
            for side in (-1, 1):
                with self.subTest(npc=definition.name, side=side):
                    run, match, player, bot = self.create(definition)
                    bot.position = (0, 1, side * 2)
                    before = match.combat.vitals[bot.entity].total
                    run.step({player: (0, 0, 8)})
                    self.assertEqual(before - match.combat.vitals[bot.entity].total, 1)
                    self.assertAlmostEqual(player.body_script.component.entity_var(342).value(), .5)
                    self.assertIn(bot.entity, match.combat.impulses.npcs)
                    for _ in range(10):
                        run.step({})
                    self.assertEqual(before - match.combat.vitals[bot.entity].total, 1)
                    self.assertGreater(side * bot.position[2], 2)
                    self.assertGreater(side * bot.velocity[2], 0)

    def test_jump_launch_all_five_rejects_range_allies_and_static_occlusion(self):
        for definition in self.definitions.values():
            for case in ('range', 'ally', 'wall'):
                with self.subTest(npc=definition.name, case=case):
                    run, match, player, bot = self.create(definition)
                    bot.position = (0, 1, 10 if case == 'range' else 2)
                    if case == 'ally':
                        bot.team = player.team
                    elif case == 'wall':
                        match.collision_world = World(box(-30, 30, -1, 1, -30, 30)
                                                      + wall(-10, 1, 10, 1))
                    before, position = match.combat.vitals[bot.entity].total, bot.position
                    run.step({player: (0, 0, 8)})
                    for _ in range(10):
                        run.step({})
                    self.assertEqual(match.combat.vitals[bot.entity].total, before)
                    self.assertEqual(bot.position, position)
                    self.assertNotIn(bot.entity, match.combat.impulses.npcs)
                    self.assertEqual(player.body_script.component.entity_var(342).value(), 0)

    def land(self, run, match, player):
        for _ in range(150):
            run.step({})
            if player.body not in match.combat.winston_jump.flights:
                break
        self.assertNotIn(player.body, match.combat.winston_jump.flights)
        self.assertTrue(player.mover.landed or player.mover.velocity[1] == 0)

    def test_one_jump_landing_hits_player_and_five_npcs_and_credits_each_once(self):
        trooper = next(d for d in self.definitions.values() if d.name == 'Trooper')
        run, match, player, first = self.create(trooper, control_caster=True)
        enemy = run.players[1]
        enemy.team = first.team
        enemy.mover.position[:] = (0, 1, 100)
        enemy.mover.body = None
        first.position = (0, 1, 100)
        hostile = [first]
        for definition in self.definitions.values():
            if definition is not trooper:
                hostile.append(npc.spawn(match, definition, (0, 1, 100), team=enemy.team,
                                         yaw=32768, auto_target=False, auto_fire=False))
        ally = npc.spawn(match, trooper, (0, 1, 100), team=player.team,
                        yaw=32768, auto_target=False, auto_fire=False)
        occluded = npc.spawn(match, trooper, (0, 1, 100), team=enemy.team,
                             yaw=32768, auto_target=False, auto_fire=False)
        match.collision_world = World(box(-30, 30, -1, 1, -30, 30) + wall(1.4, -2, 1.4, 2))
        fight = match.combat
        targets = [fight.vitals[bot.entity] for bot in (*hostile, ally, occluded)]
        targets.append(fight.vitals_of(next(t for t in fight._targets() if t.owner is enemy)))
        before = [vitals.total for vitals in targets]
        charge = player.body_script.component.entity_var(342)

        run.step({player: (0, -16384, 8)})
        self.assertEqual([vitals.total for vitals in targets], before)
        for bot in (*hostile, ally):
            bot.position = (0, 1, 0)
        occluded.position = (2.8, 1, 0)
        enemy.mover.position[:] = (0, 1, 0)
        enemy.mover.body = None
        self.land(run, match, player)
        landed = [vitals.total for vitals in targets]
        self.assertEqual([initial - remaining for initial, remaining in zip(before, landed, strict=True)],
                         [50] * 5 + [0, 0, 50])
        self.assertEqual(player.stats.value(stats.HERO_DAMAGE), 300)
        self.assertEqual(charge.value(), 175)
        self.assertTrue(all(bot.entity in fight.impulses.npcs for bot in hostile))
        self.assertGreater(enemy.mover.velocity[1], 0)
        self.assertNotIn(ally.entity, fight.impulses.npcs)
        self.assertNotIn(occluded.entity, fight.impulses.npcs)
        for _ in range(10):
            run.step({})
        self.assertEqual([vitals.total for vitals in targets], landed)
        self.assertEqual(player.stats.value(stats.HERO_DAMAGE), 300)
        self.assertEqual(charge.value(), 175)

    def test_jump_landing_all_five_full_hit_and_authored_capsule_falloff(self):
        for definition in self.definitions.values():
            for offset in (0, 3):
                with self.subTest(npc=definition.name, offset=offset):
                    run, match, player, bot = self.create(definition)
                    bot.position = (0, 1, 100)
                    before = match.combat.vitals[bot.entity].total
                    run.step({player: (0, -16384, 8)})
                    bot.position = (offset, 1, 0)
                    self.land(run, match, player)
                    # Independent analytic distance from feet-level impact to
                    # the lower capsule sphere. Falloff is reconstructed, and
                    # Heavy's original .7 radius differs from the other .5s.
                    radius = definition.mover.stand_radius
                    distance = math.hypot(offset, radius) - radius
                    expected = 50 * (1 - distance / 5)
                    self.assertAlmostEqual(before - match.combat.vitals[bot.entity].total, expected)
                    self.assertAlmostEqual(player.body_script.component.entity_var(342).value(),
                                           expected / 2, places=4)
                    self.assertIn(bot.entity, match.combat.impulses.npcs)
                    peak = bot.position[1]
                    for _ in range(20):
                        run.step({})
                        peak = max(peak, bot.position[1])
                    if offset:
                        self.assertGreater(bot.position[0], offset)
                    self.assertGreater(peak, 1)

    def test_trooper_landing_reaction_uses_admitted_health_loss_and_hit_clock(self):
        definition = next(d for d in self.definitions.values() if d.name == 'Trooper')
        for case, expected in (('full', 50), ('mitigated', 25), ('ally', 0), ('lethal', 40)):
            with self.subTest(case=case):
                run, match, player, bot = self.create(definition)
                bot.position = (0, 1, 100)
                for _ in range(12):
                    run.step({})
                component = bot.body_script.component
                listener = next(i for i in component.instances.values() if i.graph.index == 0x0E20)
                entries = runtime.message_entries(listener, npc_specials.TALON_HIT_MESSAGE)
                vitals = match.combat.vitals[bot.entity]
                if case == 'mitigated':
                    vitals.nano_until = match.combat.tick + 180
                elif case == 'ally':
                    bot.team = player.team
                elif case == 'lethal':
                    vitals.now[combat.HEALTH_POOL] = 40
                health = vitals.total
                run.step({player: (0, -16384, 8)})
                bot.position = (0, 1, 0)
                self.land(run, match, player)
                hit_tick = run.frame
                self.assertAlmostEqual(health - vitals.total, expected)
                pending = [event for event in listener.queue if event.message is not None
                           and event.message[0] == npc_specials.TALON_HIT_MESSAGE]
                self.assertEqual([event.node for event in pending], entries if case == 'full' else [])
                run.step({})
                if case == 'full':
                    self.assertEqual({event.time for event in pending}, {hit_tick * component.frame_ms})
                    self.assertEqual({event.message[2] for event in pending}, {player.body})
                    self.assertIsInstance(listener.vars[9687].value(), expr.Asset)
                    wait = listener.states[listener.graph.node(27).state]
                    self.assertEqual((wait.start, wait.counter), (hit_tick * component.frame_ms, 1))
                    self.assertTrue(wait.active)
                else:
                    self.assertNotIn(9687, listener.vars)

    def test_trooper_landing_falloff_reaches_authored_2596_range(self):
        definition = next(d for d in self.definitions.values() if d.name == 'Trooper')
        reaction = expr.Asset(0x0D80000000002596)
        for amount in (19.5, 20.5, 21.5):
            with self.subTest(amount=amount):
                run, match, player, bot = self.create(definition)
                bot.position = (0, 1, 100)
                for _ in range(12):
                    run.step({})
                component = bot.body_script.component
                listener = next(i for i in component.instances.values() if i.graph.index == 0x0E20)
                vitals = match.combat.vitals[bot.entity]
                health = vitals.total
                radius = definition.mover.stand_radius
                distance = radius + 5 * (1 - amount / 50)
                offset = math.sqrt(distance ** 2 - radius ** 2)
                run.step({player: (0, -16384, 8)})
                bot.position = (offset, 1, 0)
                self.land(run, match, player)
                self.assertAlmostEqual(health - vitals.total, amount)
                run.step({})
                if amount == 20.5:
                    self.assertEqual(listener.vars[9565].value(), reaction)
                    self.assertIn(listener.vars[9687].value(), {expr.Asset(0x0C8000000000056C),
                                                               expr.Asset(0x0C8000000000056D)})
                    wait = listener.states[listener.graph.node(27).state]
                    self.assertEqual((wait.active, wait.counter), (True, 1))
                    self.assert_trooper_primary_resumes_after_reaction(run, match, player, bot, listener, wait)
                else:
                    self.assertNotIn(9687, listener.vars)

    def test_trooper_2560_damage_pipeline_and_no_directional_candidate(self):
        definition = next(d for d in self.definitions.values() if d.name == 'Trooper')
        reaction = expr.Asset(0x0D80000000002560)
        forward_aliases = {expr.Asset(0x0C80000000000000 | alias) for alias in (0x51E, 0x51D, 0x4CC, 0x4AE)}
        for amount, yaw in ((79, 0), (80, 0), (120, 0), (121, 0), (100, 16384)):
            with self.subTest(amount=amount, yaw=yaw):
                run, match, player, bot = self.create(definition)
                for _ in range(12):
                    run.step({})
                fight = match.combat
                target = next(t for t in fight._targets() if t.owner is bot)
                vitals = fight.vitals_of(target)
                health = vitals.total
                component = bot.body_script.component
                listener = next(i for i in component.instances.values() if i.graph.index == 0x0E20)
                # The supported player attacks peak at 75 even with Nano. Supply the
                # larger contact amount to the real damage pipeline as a control.
                self.assertTrue(fight._damage(player, target, amount, False, None, run.frame,
                                              reaction_direction=combat.aim(yaw, 0)))
                self.assertEqual(health - vitals.total, amount)
                run.step({})
                if not 80 <= amount <= 120:
                    self.assertNotIn(9687, listener.vars)
                    continue
                self.assertEqual(listener.vars[9565].value(), reaction)
                alias = listener.vars[9687].value()
                if yaw == 0:
                    self.assertIn(alias, forward_aliases)
                else:
                    self.assertIsNone(alias)
                    anim = listener.graph.node(23)
                    self.assertEqual(listener.evaluate(anim.field('m_11E0A658')),
                                     expr.Asset(0x0C800000000004BA))
                wait = listener.states[listener.graph.node(27).state]
                self.assertEqual((wait.active, wait.counter), (True, 1))

    def test_heavy_2560_nano_landing_runs_empty_candidate_branch(self):
        definition = next(d for d in self.definitions.values() if d.name == 'Heavy Assault')
        for case, loss in (('normal', 50), ('boosted', 75), ('ally', 0), ('killed', 75)):
            with self.subTest(case=case):
                run, match, player, bot = self.create(definition, control_caster=True)
                bot.position = (0, 1, 100)
                for _ in range(12):
                    run.step({})
                if case != 'normal':
                    self.nano_winston(run, match, player)
                if case == 'ally':
                    bot.team = player.team
                component = bot.body_script.component
                self.assertIsNone(component.entity_var(9956).value())
                table, common = (next(i for i in component.instances.values() if i.graph.index == index)
                                 for index in (0x0E54, 0x0E20))
                common.write((expr.INSTANCE, 7310), 7.0)
                vitals = match.combat.vitals[bot.entity]
                if case == 'killed':
                    vitals.now = {combat.HEALTH_POOL: 77, combat.ARMOUR_POOL: 0, combat.SHIELDS_POOL: 0}
                health = vitals.total
                run.step({player: (0, -16384, 8)})
                bot.position = (0, 1, 0)
                self.land(run, match, player)
                self.assertEqual(health - vitals.total, loss)
                pending = [event for instance in (table, common) for event in instance.queue
                           if event.message is not None and event.message[0] == npc_specials.TALON_HIT_MESSAGE]
                if case in ('boosted', 'killed'):
                    self.assertEqual([event.node.pos for event in pending], [29, 4, 39])
                    self.assertEqual({event.time for event in pending}, {run.frame * component.frame_ms})
                    for event in pending:
                        self.assertEqual(event.message[1][0x0403], expr.Asset(0x0D80000000002560))
                        self.assertIs(event.message[1][0x0407], False)
                        self.assertEqual(event.message[2], player.body)
                else:
                    self.assertFalse(pending)
                if case == 'killed':
                    # Apply a second contact at the landing tick, before either
                    # graph runs again. Its amount is controlled by this fixture.
                    ally = run.players[1]
                    target = next(t for t in match.combat._targets() if t.owner is bot)
                    self.assertTrue(match.combat._damage(ally, target, 2, False, None, run.frame))
                    self.assertTrue(bot.dead)
                    self.assertEqual(vitals.killer, ally.body)
                    self.assertEqual(ally.stats.value(stats.HERO_DAMAGE), 2)
                    self.assertFalse(any(event in instance.queue for instance in (table, common) for event in pending))
                run.step({})
                if case == 'boosted':
                    self.assertIsNone(common.vars[9687].value())
                    self.assertIs(common.vars[9607].value(), False)
                    self.assertEqual(common.vars[7310].value(), 0.0)
                else:
                    self.assertNotIn(9687, common.vars)
                    self.assertEqual(common.vars[7310].value(), 7.0)
                for instance, positions in ((table, (8, 11)), (common, (20, 27))):
                    self.assertFalse(any(instance.graph.node(pos).state in instance.states for pos in positions))
                self.assertEqual(health - vitals.total, loss + (2 if case == 'killed' else 0))

    def test_jump_landing_all_five_rejects_range_allies_and_static_occlusion(self):
        for definition in self.definitions.values():
            for case in ('range', 'ally', 'wall'):
                with self.subTest(npc=definition.name, case=case):
                    run, match, player, bot = self.create(definition)
                    bot.position = (0, 1, 100)
                    before = match.combat.vitals[bot.entity].total
                    run.step({player: (0, -16384, 8)})
                    bot.position = (0, 1, 12 if case == 'range' else 2)
                    if case == 'ally':
                        bot.team = player.team
                    elif case == 'wall':
                        match.collision_world = World(box(-30, 30, -1, 1, -30, 30)
                                                      + wall(-10, 1, 10, 1))
                    position = bot.position
                    self.land(run, match, player)
                    self.assertEqual(match.combat.vitals[bot.entity].total, before)
                    self.assertEqual(bot.position, position)
                    self.assertNotIn(bot.entity, match.combat.impulses.npcs)
                    self.assertEqual(player.body_script.component.entity_var(342).value(), 0)

    def test_all_five_injured_deaths_from_melee_primal_and_both_jump_impacts(self):
        for definition in self.definitions.values():
            for attack in ('melee', 'primal', 'launch', 'landing'):
                with self.subTest(npc=definition.name, attack=attack):
                    run, match, player, bot = self.create(definition)
                    bot.position = (0, 1, 1.5)
                    vitals = match.combat.vitals[bot.entity]
                    vitals.now = {combat.HEALTH_POOL: .5, combat.ARMOUR_POOL: 0,
                                  combat.SHIELDS_POOL: 0}
                    if attack == 'primal':
                        script = player.body_script
                        script.set_ult_charge(script.component.entity_var(338).value())
                        run.step({player: (0, 0, 32)})
                        for _ in range(75):
                            run.step({player: (0, 0, 1)})
                    elif attack == 'landing':
                        bot.position = (0, 1, 100)
                        run.step({player: (0, -16384, 8)})
                        bot.position = (0, 1, 0)
                        self.land(run, match, player)
                    else:
                        run.step({player: (0, 0, 0x800 if attack == 'melee' else 8)})
                        for _ in range(40 if attack == 'melee' else 1):
                            run.step({})
                    self.assertTrue(bot.dead)
                    self.assertEqual(vitals.total, 0)
                    self.assertEqual(vitals.killer, player.body)
                    self.assertNotIn(bot.entity, match.combat.impulses.npcs)
                    death_pose = bot.position
                    for _ in range(210):
                        run.step({})
                    self.assertIn(bot, match.bots)
                    self.assertEqual(bot.position, death_pose)
                    self.assertFalse(any(actor is bot for _, actor in match.combat.respawns))

    def test_absent_secondary_attack_does_not_damage_any_actual_npc_or_consume_ammo(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                run, match, player, bot = self.create(definition)
                weapon = next(i for i in player.body_script.component.instances.values()
                              if i.graph.index == 0xFE)
                before = match.combat.vitals[bot.entity].total
                ammo = weapon.vars[53].value()
                for _ in range(20):
                    run.step({player: (0, 0, 2)})
                self.assertEqual(match.combat.vitals[bot.entity].total, before)
                self.assertEqual(weapon.vars[53].value(), ammo)
                self.assertEqual(player.body_script.component.entity_var(342).value(), 0)

    def emit_primary(self, run, match, bot, held=True):
        """Explicit authored input adapter; does not choose AI targets or bypass control."""
        script = bot.body_script
        script.advance_live(run.frame, bot.position, weapon_input=(0, held))
        shots, script.component.shots[:] = list(script.component.shots), []
        for state, number, time, activation in shots:
            match.combat._shoot(bot, None, state, number, time, activation=activation)
        return shots

    @staticmethod
    def emit_assassin_strike(run, match, bot):
        script = bot.body_script
        script.advance_live(run.frame, bot.position, ability_input=(36, True))
        shots, script.component.shots[:] = list(script.component.shots), []
        for state, number, time, activation in shots:
            match.combat._shoot(bot, None, state, number, time, activation=activation)

    def flash_npc(self, run, match, bot, *, primary=False):
        caster = run.players[1]
        point = (bot.position[0], bot.position[1] + 1, bot.position[2])
        yaw, pitch = aim(match.combat._eye(caster), point)
        run.step({caster: (yaw, pitch, 16)})
        if primary:
            self.emit_primary(run, match, bot)
        for _ in range(50):
            if match.combat.is_stunned(bot):
                return match.combat.stuns[bot.entity]
            run.step({})
            if primary:
                self.emit_primary(run, match, bot)
        self.fail('actual Flashbang did not control actual NPC')

    def test_all_five_actual_flashbang_control_and_primal_external_motion_compose(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                run, match, player, bot = self.create(definition, control_caster=True)
                bot.position = (0, 1, 1.5)
                before = match.combat.vitals[bot.entity].total
                script = player.body_script
                script.set_ult_charge(script.component.entity_var(338).value())
                run.step({player: (0, 0, 32)})
                for _ in range(35):
                    run.step({})
                end = self.flash_npc(run, match, bot)
                self.assertEqual(before - match.combat.vitals[bot.entity].total, 25)
                self.assertFalse(bot.body_script.component.held)
                for _ in range(30):
                    run.step({player: (0, 0, 1)})
                    if before - match.combat.vitals[bot.entity].total > 25:
                        break
                self.assertEqual(before - match.combat.vitals[bot.entity].total, 65)
                for _ in range(10):
                    run.step({})
                self.assertLess(run.frame, end)
                self.assertTrue(match.combat.is_stunned(bot))
                self.assertGreater(bot.position[2], 1.5)
                self.assertGreater(bot.position[1], 1)

    def test_four_working_actual_primaries_cannot_bypass_stun_then_recover(self):
        for name in ('Trooper', 'Enforcer', 'Sniper', 'Heavy Assault'):
            with self.subTest(npc=name):
                definition = next(d for d in self.definitions.values() if d.name == name)
                run, match, player, bot = self.create(definition, control_caster=True)
                vitals = match.combat.vitals_of(next(t for t in match.combat._targets()
                                                   if t.owner is player))
                for _ in range(180):
                    run.step({})
                    self.emit_primary(run, match, bot)
                    if vitals.total < 500:
                        break
                self.assertLess(vitals.total, 500, 'requires working original primary positive control')
                end = self.flash_npc(run, match, bot, primary=True)
                # Already-fired physical projectiles are allowed to finish.
                for _ in range(20):
                    run.step({})
                    self.emit_primary(run, match, bot)
                before = vitals.total
                while run.frame < end - 1:
                    run.step({})
                    self.emit_primary(run, match, bot)
                    self.assertEqual(vitals.total, before)
                    self.assertFalse(any(r.shooter is bot for r in match.combat.rockets))
                run.step({})
                self.emit_primary(run, match, bot, False)
                for _ in range(180):
                    run.step({})
                    self.emit_primary(run, match, bot)
                    if vitals.total < before:
                        break
                self.assertLess(vitals.total, before)

    def moving_trooper(self):
        run, match, player, bot = self.create(self.definitions['1'])
        bot.position = (0, 1, 1.5)
        player.body_script.set_ult_charge(player.body_script.component.entity_var(338).value())
        run.step({player: (0, 0, 32)})
        for _ in range(75):
            run.step({player: (0, 0, 1)})
        self.assertIn(bot.entity, match.combat.impulses.npcs)
        return run, match, player, bot

    def test_primal_push_cannot_carry_actual_trooper_through_ice_wall(self):
        for cancel in (False, True):
            with self.subTest(cancel=cancel):
                run, match, player, bot = self.create(self.definitions['1'], control_caster=True)
                fight = match.combat
                mei, pillar = self.deploy_ice_wall_for_npc(run, fight)
                if cancel:
                    run.step({mei: (0, 0, 16)})
                    self.assertFalse(fight.walls.pieces)
                player.mover.position[:] = (0, 1, 1.5)
                player.mover.body = None
                bot.position = (0, 1, 3)
                vitals = fight.vitals[bot.entity]
                before = vitals.total
                script = player.body_script
                script.set_ult_charge(script.component.entity_var(338).value())
                run.step({player: (0, 0, 32)})
                for _ in range(75):
                    run.step({player: (0, 0, 1)})
                    if vitals.total < before:
                        break
                self.assertEqual(before - vitals.total, 40)
                self.assertIn(bot.entity, fight.impulses.npcs)
                for _ in range(30):
                    run.step({})
                if cancel:
                    self.assertGreater(bot.position[2], pillar.feet[2] + pillar.depth)
                else:
                    face = pillar.feet[2] - pillar.depth - bot.movement_data.stand_radius
                    self.assertAlmostEqual(bot.position[2], face, delta=.04)

    def test_npc_death_cancels_impulse_and_corpse_stays_at_death_pose(self):
        run, match, player, bot = self.moving_trooper()
        target = next(t for t in match.combat._targets() if t.owner is bot)
        # Explicit lethal fixture isolates cancellation from further weapon input.
        match.combat._damage(player, target, 1000, False, None, run.frame)
        position = bot.position
        for _ in range(200):
            run.step({})
        self.assertNotIn(bot.entity, match.combat.impulses.npcs)
        self.assertEqual(bot.position, position)
        self.assertIn(bot, match.bots)
        self.assertTrue(bot.dead)

    def test_explicit_remove_cleans_active_npc_impulse_immediately(self):
        _run, match, _player, bot = self.moving_trooper()
        self.assertEqual(npc.remove_spawned(match), (bot.entity,))
        self.assertNotIn(bot.entity, match.combat.impulses.npcs)
        self.assertNotIn(bot, match.bots)

    def incoming(self, name, *, shield=False, require_damage=True):
        definition = next(d for d in self.definitions.values() if d.name == name)
        run, match, player, bot = self.create(definition)
        script = bot.body_script
        bubble = None
        if shield:
            bot.position = (0, 1, 10)
            run.step({player: (0, 0, 16)})
            for _ in range(3):
                run.step({})
            bubble = match.combat.barriers.actors[player.body]
        before = match.combat.vitals_of(next(t for t in match.combat._targets()
                                             if t.owner is player)).total
        for _ in range(150):
            run.step({})
            # Explicit static input fixture enters the real weapon graph. It does
            # not fabricate a volley or call _damage directly, and does not prove AI.
            script.advance_live(run.frame, bot.position, weapon_input=(0, True))
            shots, script.component.shots[:] = list(script.component.shots), []
            for state, number, time, activation in shots:
                match.combat._shoot(bot, None, state, number, time, activation=activation)
        if shield:
            self.assertEqual(match.combat.vitals[player.body].total, before)
            self.assertLess(bubble.health, 700)
        elif require_damage:
            self.assertLess(match.combat.vitals[player.body].total, before,
                            f'{name} primary input still cannot damage Winston')
        return before - match.combat.vitals[player.body].total

    def test_trooper_primary_damages_winston(self):
        self.incoming('Trooper')

    def deploy_ice_wall_for_npc(self, run, fight):
        mei = run.players[1]
        self.assertTrue(run.match.switch_hero(mei, heroes()[protections.MEI]))
        mei.mover.position[:] = (0, 1, 2)
        mei.mover.body = None
        angle = aim(fight._eye(mei), (0, 1, 5))
        for button in (16, 0, 1, 0):
            run.step({mei: (*angle, button)})
        self.assertEqual(len(fight.walls.pieces), 5)
        return mei, min(fight.walls.pieces.values(), key=lambda piece: abs(piece.feet[0]))

    def test_all_five_talon_routes_stop_at_ice_wall_and_resume_after_cancel(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                run, match, _player, bot = self.create(definition, control_caster=True)
                bot.position = (0, 1, 10)
                mei, pillar = self.deploy_ice_wall_for_npc(run, match.combat)
                bot.route = [(0, 1, 0)]
                bot.wait = 0
                face = pillar.feet[2] + pillar.depth + bot.movement_data.stand_radius
                for _ in range(150):
                    run.step({})
                self.assertAlmostEqual(bot.position[2], face, delta=.04)
                self.assertEqual(bot.pace, 0)
                self.assertEqual(bot.target, (0, 1, 0))
                run.step({mei: (0, 0, 16)})
                for _ in range(65):
                    run.step({})
                self.assertLess(bot.position[2], pillar.feet[2] - pillar.depth)

    def test_assassin_contact_requires_clear_ice_wall_axis(self):
        for cancel in (False, True):
            with self.subTest(cancel=cancel):
                run, match, player, bot = self.create(self.definitions['4'], control_caster=True)
                fight = match.combat
                mei, pillar = self.deploy_ice_wall_for_npc(run, fight)
                if cancel:
                    run.step({mei: (0, 0, 16)})
                    self.assertFalse(fight.walls.pieces)
                # Isolate contact admission at the pillar face from mover collision.
                player.mover.position[:] = (0, 1, pillar.feet[2] + pillar.depth - .05)
                player.mover.body = None
                radius = player.mover.data.stand_radius + bot.movement_data.stand_radius
                bot.position = (0, 1, player.mover.position[2] + radius - .05)
                bot.body_script.set_manual_target(player.entity)
                vitals = fight.vitals_of(next(t for t in fight._targets() if t.owner is player))
                for _ in range(90):
                    run.step({})
                    self.emit_assassin_strike(run, match, bot)
                    if vitals.total < 500:
                        break
                self.assertEqual(vitals.total < 500, cancel)

    def test_actual_talon_volleys_hit_ice_wall_then_winston_after_cancel(self):
        for name, alternate in (('Trooper', False), ('Enforcer', False), ('Sniper', False),
                                ('Sniper', True), ('Heavy Assault', False)):
            with self.subTest(npc=name, alternate=alternate):
                definition = next(d for d in self.definitions.values() if d.name == name)
                run, match, player, bot = self.create(definition, control_caster=True)
                bot.position = (0, 1, 10)
                fight = match.combat
                mei, centre = self.deploy_ice_wall_for_npc(run, fight)
                mei.mover.position[:] = (12, 1, 0)
                mei.mover.body = None
                if alternate:
                    self.select_sniper_weapon(run, bot, 16, 2)
                vitals = fight.vitals_of(next(t for t in fight._targets() if t.owner is player))
                for _ in range(150):
                    run.step({})
                    self.emit_primary(run, match, bot)
                    if centre.health < 400:
                        break
                self.assertLess(centre.health, 400)
                self.assertEqual(vitals.total, 500)
                run.step({mei: (0, 0, 16)})
                self.assertFalse(fight.walls.pieces)
                for _ in range(180):
                    run.step({})
                    self.emit_primary(run, match, bot)
                    if vitals.total < 500:
                        break
                self.assertLess(vitals.total, 500)

    def test_all_five_actual_npcs_respect_winston_team_on_incoming_attack(self):
        for definition in self.definitions.values():
            for friendly in (False, True):
                with self.subTest(npc=definition.name, friendly=friendly):
                    run, match, player, bot = self.create(definition)
                    if friendly:
                        bot.team = player.team
                    assassin = definition.name == 'Assassin'
                    if assassin:
                        radius = player.mover.data.stand_radius + bot.movement_data.stand_radius
                        bot.position = (0, 1, radius - .05)
                        bot.body_script.set_manual_target(player.entity)
                    fight = match.combat
                    vitals = fight.vitals_of(next(t for t in fight._targets() if t.owner is player))
                    volleys = 0
                    for _ in range(150):
                        run.step({})
                        if assassin:
                            self.emit_assassin_strike(run, match, bot)
                        else:
                            volleys += len(self.emit_primary(run, match, bot))
                        if vitals.total < 500:
                            break
                    self.assertEqual(vitals.total < 500, not friendly)
                    if assassin:
                        self.assertEqual(fight.assassins.controlled(player), not friendly)
                    else:
                        self.assertGreater(volleys, 0)
                    self.assertEqual(player.body_script.component.entity_var(342).value(), 0)

    def test_ally_matrix_intercepts_actual_talon_weapon_variants(self):
        for name, alternate in (('Trooper', False), ('Enforcer', False), ('Sniper', False),
                                ('Sniper', True), ('Heavy Assault', False)):
            with self.subTest(npc=name, alternate=alternate):
                definition = next(d for d in self.definitions.values() if d.name == name)
                run, match, player, bot = self.create(definition, control_caster=True)
                mech = run.players[1]
                self.assertTrue(match.switch_hero(mech, replace(find_hero('dva'), body=0x0400000000000407)))
                mech.mover.position[:] = (0, 1, -2)
                mech.mover.body = None
                bot.position = (0, 1, 9)
                if alternate:
                    self.select_sniper_weapon(run, bot, 16, 2)
                for _ in range(100):
                    run.step({})
                if name == 'Sniper' and not alternate:
                    # Begin the original scope windup before committing Matrix resource.
                    for _ in range(50):
                        run.step({})
                        self.assertFalse(self.emit_primary(run, match, bot))
                matrix = next(i for i in mech.body_script.component.instances.values()
                              if i.graph.index == 0x02CC)
                fight = match.combat
                vitals = fight.vitals_of(next(t for t in fight._targets() if t.owner is player))
                volleys = 0
                for _ in range(100):
                    run.step({mech: (0, 0, 2)})
                    volleys += len(self.emit_primary(run, match, bot))
                    if volleys and not any(rocket.shooter is bot for rocket in fight.rockets):
                        break
                self.assertGreater(volleys, 0)
                self.assertTrue(matrix.state(3).active)
                self.assertFalse(any(rocket.shooter is bot for rocket in fight.rockets))
                self.assertEqual(vitals.total, 500)
                for _ in range(180):
                    run.step({})
                    self.emit_primary(run, match, bot)
                    if vitals.total < 500:
                        break
                self.assertFalse(matrix.state(3).active)
                self.assertLess(vitals.total, 500)

    def test_ally_genji_reflects_actual_talon_volleys_and_owns_the_damage(self):
        for name, alternate in (('Trooper', False), ('Enforcer', False), ('Sniper', False),
                                ('Sniper', True), ('Heavy Assault', False)):
            with self.subTest(npc=name, alternate=alternate):
                definition = next(d for d in self.definitions.values() if d.name == name)
                run, match, player, bot = self.create(definition, control_caster=True)
                genji = run.players[1]
                self.assertTrue(match.switch_hero(genji, heroes()[0x02E0000000000029]))
                genji.mover.position[:] = (0, 1, 2)
                genji.mover.body = None
                bot.position = (0, 1, 9)
                if alternate:
                    self.select_sniper_weapon(run, bot, 16, 2)
                elif name == 'Sniper':
                    for _ in range(50):
                        run.step({})
                        self.assertFalse(self.emit_primary(run, match, bot))
                fight = match.combat
                player_hp = fight.vitals_of(next(t for t in fight._targets() if t.owner is player))
                genji_hp = fight.vitals_of(next(t for t in fight._targets() if t.owner is genji))
                bot_hp = fight.vitals[bot.entity]
                before = bot_hp.total
                run.step({genji: (0, 0, 16)})
                for _ in range(4):
                    run.step({})
                state = next(i for i in genji.body_script.component.instances.values()
                             if i.graph.index == 0x03A3).state(10)
                self.assertTrue(state.active)
                for _ in range(90):
                    run.step({})
                    self.emit_primary(run, match, bot)
                    if bot_hp.total < before:
                        break
                self.assertTrue(state.active)
                self.assertEqual(player_hp.total, 500)
                self.assertEqual(genji_hp.total, 200)
                self.assertLess(bot_hp.total, before)
                self.assertAlmostEqual(genji.stats.value(stats.HERO_DAMAGE), before - bot_hp.total)
                genji.mover.position[:] = (8, 1, 2)
                genji.mover.body = None
                for _ in range(180):
                    run.step({})
                    if not state.active:
                        self.emit_primary(run, match, bot)
                    if player_hp.total < 500:
                        break
                self.assertFalse(state.active)
                self.assertLess(player_hp.total, 500)

    def test_allied_projected_barrier_absorbs_actual_talon_weapon_variants(self):
        variants = (('Trooper', False), ('Enforcer', False), ('Sniper', False),
                    ('Sniper', True), ('Heavy Assault', False))
        for (name, alternate), breaking in product(variants, (False, True)):
            with self.subTest(npc=name, alternate=alternate, breaking=breaking):
                definition = next(d for d in self.definitions.values() if d.name == name)
                run, match, player, bot = self.create(definition, control_caster=True)
                caster = run.players[1]
                self.assertTrue(match.switch_hero(caster, heroes()[0x02E0000000000068]))
                caster.mover.position[:] = (0, 1, -3)
                caster.mover.body = None
                if alternate:
                    self.select_sniper_weapon(run, bot, 16, 2)
                elif name == 'Sniper':
                    for _ in range(50):
                        run.step({})
                        self.assertFalse(self.emit_primary(run, match, bot))
                run.step({caster: (0, 0, 16)})
                for _ in range(4):
                    run.step({})
                fight = match.combat
                self.assertTrue(fight.zarya_barriers.protects(player))
                barrier = fight.zarya_barriers.projected[caster.body]
                self.assertEqual(barrier.remaining, 200)
                vitals = fight.vitals_of(next(t for t in fight._targets() if t.owner is player))
                initial = 1 if breaking else 200
                barrier.remaining = initial
                damage = fight._damage
                breaking_losses = []

                def observe_damage(*args, barrier=barrier, vitals=vitals, damage=damage,
                                   losses=breaking_losses, **kwargs):
                    remaining, health = barrier.remaining, vitals.total
                    admitted = damage(*args, **kwargs)
                    if remaining > 0 and barrier.remaining == 0:
                        losses.append(health - vitals.total)
                    return admitted

                with patch.object(fight, '_damage', side_effect=observe_damage):
                    for _ in range(110):
                        run.step({})
                        self.emit_primary(run, match, bot)
                        if barrier.remaining < initial:
                            break
                self.assertLess(barrier.remaining, initial)
                self.assertEqual(breaking_losses, [0] if breaking else [])
                run.step({})
                self.emit_primary(run, match, bot, False)
                self.assertAlmostEqual(caster.body_script.component.entity_var(87).value(),
                                       (initial - barrier.remaining) / 5, places=4)
                if breaking and name == 'Enforcer':
                    self.assertLess(vitals.total, 500)
                else:
                    self.assertEqual(vitals.total, 500)
                for _ in range(130):
                    run.step({})
                    if not fight.zarya_barriers.protects(player):
                        break
                self.assertFalse(fight.zarya_barriers.protects(player))
                self.assertEqual(barrier.remaining == 0, breaking)
                if not breaking:
                    self.assertEqual(vitals.total, 500)
                before = vitals.total
                for _ in range(180):
                    run.step({})
                    self.emit_primary(run, match, bot)
                    if vitals.total < before:
                        break
                self.assertLess(vitals.total, before)

    def nano_winston(self, run, match, player):
        ana = run.players[1]
        self.assertTrue(match.switch_hero(ana, heroes()[combat.ANA]))
        ana.mover.position[:] = (0, 1, -3)
        ana.mover.body = None
        fight = match.combat
        vitals = fight.vitals_of(next(t for t in fight._targets() if t.owner is player))
        nano = ana.body_script
        nano.set_ult_charge(nano.component.entity_var(338).value())
        yaw, pitch = aim(fight._eye(ana), fight._eye(player))
        run.step({ana: (yaw, pitch, 32)})
        for _ in range(30):
            if vitals.nano_until > run.frame:
                break
            run.step({ana: (yaw, pitch, 0)})
        self.assertEqual(vitals.nano_until - run.frame, 500)
        return vitals

    def test_actual_nano_boosts_tesla_damage_and_charge_against_all_five_talon_bodies(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                run, match, player, bot = self.create(definition, control_caster=True)
                vitals = self.nano_winston(run, match, player)
                enemy = match.combat.vitals[bot.entity]
                charge = player.body_script.component.entity_var(342)
                self.assertEqual(charge.value(), 0)
                before = enemy.total
                run.step({player: (0, 0, 1)})
                self.assertAlmostEqual(before - enemy.total, 1.44)
                self.assertAlmostEqual(charge.value(), .72, places=5)
                run.step({})
                run.frame = vitals.nano_until
                match.update(run.frame * combat.TICK, run.frame, frames=False)
                before = enemy.total
                run.step({player: (0, 0, 1)})
                self.assertAlmostEqual(before - enemy.total, .96)
                self.assertAlmostEqual(charge.value(), 1.2, places=5)

    def test_authored_talon_tag_lifetime_controls_damage_charge(self):
        definition = next(d for d in self.definitions.values() if d.name == 'Trooper')
        run, match, player, bot = self.create(definition)
        component = bot.body_script.component
        tag_graph = next(i for i in component.instances.values() if i.graph.index == 0x0E62)
        self.assertTrue(tag_graph.state(7).active)
        charge = player.body_script.component.entity_var(342)
        vitals = match.combat.vitals[bot.entity]
        before = vitals.total
        run.step({player: (0, 0, 1)})
        self.assertAlmostEqual(before - vitals.total, .96)
        self.assertAlmostEqual(charge.value(), .48)

        component.destroy(tag_graph)
        run.step({player: (0, 0, 1)})
        self.assertAlmostEqual(before - vitals.total, 1.92)
        self.assertAlmostEqual(charge.value(), 1.44, places=5)

    def test_actual_nano_reduces_first_hits_from_all_five_talon_bodies(self):
        losses = {'Trooper': (5, 15), 'Enforcer': (12.5, 25), 'Sniper': (45, 95),
                  'Heavy Assault': (1.25, 2.5), 'Assassin': (7.5, 20)}
        for definition in self.definitions.values():
            for expired in (False, True):
                with self.subTest(npc=definition.name, expired=expired):
                    run, match, player, bot = self.create(definition, control_caster=True)
                    fight = match.combat
                    vitals = self.nano_winston(run, match, player)
                    if expired:
                        run.frame = vitals.nano_until
                        match.update(run.frame * combat.TICK, run.frame, frames=False)
                    assassin = definition.name == 'Assassin'
                    if assassin:
                        radius = player.mover.data.stand_radius + bot.movement_data.stand_radius
                        bot.position = (0, 1, radius - .05)
                        bot.body_script.set_manual_target(player.entity)
                    for _ in range(150):
                        run.step({})
                        if assassin:
                            self.emit_assassin_strike(run, match, bot)
                        else:
                            self.emit_primary(run, match, bot)
                        if vitals.total < 500:
                            break
                    loss = losses[definition.name][expired]
                    self.assertEqual(vitals.now[combat.ARMOUR_POOL], 150 - loss)
                    self.assertEqual(vitals.now[combat.HEALTH_POOL], 350)
                    self.assertEqual(vitals.nano_until > run.frame, not expired)
                    if assassin:
                        self.assertTrue(fight.assassins.controlled(player))
                    self.assertEqual(player.body_script.component.entity_var(342).value(), 0)

    def test_actual_npc_first_hits_keep_armour_damage_after_primal_expires(self):
        expected_loss = {'Trooper': 15, 'Enforcer': 25, 'Sniper': 95,
                         'Heavy Assault': 2.5, 'Assassin': 20}
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                run, match, player, bot = self.create(definition)
                script = player.body_script
                script.set_ult_charge(script.component.entity_var(338).value())
                run.step({player: (0, 0, 32)})
                vitals = match.combat.vitals[player.body]
                self.assertEqual(vitals.total, 1000)
                assassin = definition.name == 'Assassin'
                if assassin:
                    radius = player.mover.data.stand_radius + bot.movement_data.stand_radius
                    bot.position = (0, 1, radius - .05)
                    bot.body_script.set_manual_target(player.entity)
                for _ in range(150):
                    run.step({})
                    if assassin:
                        self.emit_assassin_strike(run, match, bot)
                    else:
                        self.emit_primary(run, match, bot)
                    if vitals.total < 1000:
                        break
                loss = expected_loss[definition.name]
                self.assertEqual(vitals.now[combat.ARMOUR_POOL], 150 - loss)
                self.assertEqual(vitals.now[combat.HEALTH_POOL], 850)
                npc.remove_spawned(match)
                run.frame += 750
                match.update(run.frame * combat.TICK, run.frame)
                self.assertEqual(vitals.most[combat.HEALTH_POOL], 350)
                self.assertEqual(vitals.total, 500 - loss)
                self.assertNotIn(player.body, match.combat.primal_bonus)

    def test_actual_sniper_armour_overflow_and_primal_health_clamp(self):
        definition = next(d for d in self.definitions.values() if d.name == 'Sniper')
        for count, health in ((1, 610), (2, 210)):
            with self.subTest(snipers=count):
                run, match, player, bot = self.create(definition)
                snipers = [bot]
                if count == 2:
                    snipers.append(npc.spawn(match, definition, (0, 1, 6), team=1, yaw=32768))
                script = player.body_script
                script.set_ult_charge(script.component.entity_var(338).value())
                run.step({player: (0, 0, 32)})
                vitals = match.combat.vitals[player.body]
                self.assertEqual(vitals.total, 1000)
                for _ in range(580):
                    run.step({})
                    for sniper in snipers:
                        self.emit_primary(run, match, sniper)
                self.assertIn(player.body, match.combat.primal_bonus)
                self.assertEqual(vitals.now[combat.ARMOUR_POOL], 0)
                self.assertEqual(vitals.now[combat.HEALTH_POOL], health)
                npc.remove_spawned(match)
                run.frame += 750
                match.update(run.frame * combat.TICK, run.frame)
                self.assertEqual(vitals.most[combat.HEALTH_POOL], 350)
                self.assertEqual(vitals.total, min(health, 350))
                self.assertNotIn(player.body, match.combat.primal_bonus)

    def test_trooper_controller_magazine_tracks_actual_genji_deflect(self):
        definition = next(d for d in self.definitions.values() if d.name == 'Trooper')
        for deflect, front in ((False, True), (True, False), (True, True)):
            with self.subTest(deflect=deflect, front=front):
                run, match, winston, bot = self.create(definition, control_caster=True)
                script = bot.body_script
                controller = script.controller
                if controller is None:
                    self.skipTest('Trooper controller graph requires the additive catalog')
                owner = next(i for i in controller.instances.values() if i.graph.index == 0x11E3)
                weapon = next(i for i in script.component.instances.values() if i.graph.index == 0x102E)
                gate = owner.node_state(owner.graph.node(32))
                genji = run.players[1]
                self.assertTrue(match.switch_hero(genji, heroes()[0x02E0000000000029]))
                genji.mover.position[:] = (0, 1, 2)
                genji.mover.body = None
                yaw = 0 if front else -32768
                script.set_manual_target(genji.entity)

                def step(button=0):
                    run.step({genji: (yaw, 0, button)})

                for _ in range(10):
                    step()
                marker = genji.body_script.component.entity_var(13393)
                self.assertEqual(marker.value(), 1)
                self.assertEqual(weapon.vars[53].value(), 30)
                step(16 if deflect else 0)
                for _ in range(7):
                    step()
                reflected = deflect and front
                self.assertEqual(marker.value(), 2.5 if deflect else 1)
                self.assertEqual(gate.current, reflected)
                self.assertEqual(weapon.vars[53].value(), 60 if reflected else 30)

                fight = match.combat
                vitals = {id(t.owner): fight.vitals_of(t) for t in fight._targets()}
                npc_vitals, genji_vitals, winston_vitals = (
                    vitals[id(actor)] for actor in (bot, genji, winston)
                )
                npc_health, genji_health = npc_vitals.total, genji_vitals.total
                for _ in range(50):
                    step()
                    self.emit_primary(run, match, bot)
                    if npc_vitals.total < npc_health or genji_vitals.total < genji_health:
                        break
                self.assertEqual(winston_vitals.total, 500)
                if reflected:
                    self.assertEqual(npc_health - npc_vitals.total, 20)
                    self.assertEqual(genji_vitals.total, genji_health)
                    self.assertTrue(gate.current)
                    original_team = genji.team
                    genji.team = bot.team
                    for _ in range(3):
                        step()
                    self.assertEqual(controller.world.manual_target, genji.entity)
                    self.assertEqual(marker.value(), 2.5)
                    self.assertFalse(gate.current)
                    genji.team = original_team
                    for _ in range(3):
                        step()
                    self.assertTrue(gate.current)
                else:
                    self.assertEqual(npc_vitals.total, npc_health)
                    self.assertLess(genji_vitals.total, genji_health)
                script.set_manual_target(None)
                for _ in range(3):
                    step()
                self.assertFalse(gate.current)

    def test_trooper_original_magazine_empties_and_logical_reload_restores_fire(self):
        definition = next(d for d in self.definitions.values() if d.name == 'Trooper')
        run, match, player, bot = self.create(definition)
        bot.yaw = 0
        script = bot.body_script
        weapon = next(i for i in script.component.instances.values() if i.graph.index == 0x102E)
        count = 0
        for _ in range(1300):
            run.step({})
            shots = self.emit_primary(run, match, bot)
            count += len(shots)
            if shots:
                self.assertEqual(weapon.vars[53].value(), 30 - count)
            if count == 30:
                break
        self.assertEqual(count, 30)
        for _ in range(70):
            run.step({})
            self.assertEqual(self.emit_primary(run, match, bot), [])
        self.assertEqual(weapon.vars[53].value(), 0)
        for pressed in (True, False):
            run.step({})
            script.advance_live(run.frame, bot.position, weapon_input=(0, False), ability_input=(14, pressed))
        self.assertTrue(weapon.vars[51].value())
        self.assertEqual(weapon.vars[53].value(), 0)
        for _ in range(150):
            if not weapon.vars[51].value():
                break
            run.step({})
            self.assertEqual(self.emit_primary(run, match, bot), [])
        self.assertFalse(weapon.vars[51].value())
        self.assertEqual(weapon.vars[53].value(), 30)
        bot.yaw = -32768
        for _ in range(80):
            run.step({})
            if self.emit_primary(run, match, bot):
                break
        self.assertEqual(weapon.vars[53].value(), 29)
        for _ in range(15):
            run.step({})
            self.emit_primary(run, match, bot, False)
        self.assertLess(match.combat.vitals[player.body].total, 500)

    def test_actual_npc_reloads_finish_without_further_input(self):
        for name, graph_id, shots_before, capacity in (
            ('Trooper', 0x102E, 1, 30), ('Enforcer', 0x13EB, 4, 4),
        ):
            with self.subTest(npc=name):
                definition = next(d for d in self.definitions.values() if d.name == name)
                run, match, player, bot = self.create(definition)
                bot.yaw = 0
                script = bot.body_script
                weapon = next(i for i in script.component.instances.values()
                              if i.graph.index == graph_id)
                count = 0
                for _ in range(650):
                    run.step({})
                    count += len(self.emit_primary(run, match, bot))
                    if count >= shots_before:
                        break
                self.assertEqual(count, shots_before)
                if graph_id == 0x102E:
                    for pressed in (True, False):
                        run.step({})
                        script.advance_live(run.frame, bot.position, weapon_input=(0, False),
                                            ability_input=(14, pressed))
                else:
                    run.step({})
                    self.emit_primary(run, match, bot, False)
                self.assertTrue(weapon.vars[51].value())
                before = script.frame
                run.frame += 160
                match.update(run.frame * combat.TICK, run.frame, frames=False)
                self.assertGreater(script.frame, before)
                self.assertLess(script.frame, run.frame - 1)
                self.assertFalse(weapon.vars[51].value())
                self.assertEqual(weapon.vars[53].value(), capacity)
                self.assertFalse(script.component.shots)
                self.assertEqual(match.combat.vitals[player.body].total, 500)

    def test_flashbang_during_enforcer_reload_clears_fire_and_keeps_refill(self):
        definition = next(d for d in self.definitions.values() if d.name == 'Enforcer')
        run, match, player, bot = self.create(definition, control_caster=True)
        bot.yaw = 0
        script = bot.body_script
        weapon = next(i for i in script.component.instances.values() if i.graph.index == 0x13EB)
        for _ in range(650):
            run.step({})
            self.emit_primary(run, match, bot)
            if weapon.vars.get(51) is not None and weapon.vars[51].value():
                break
        self.assertEqual(weapon.vars[53].value(), 0)
        self.assertIn(20, script.component.held)
        end = self.flash_npc(run, match, bot)
        self.assertTrue(weapon.vars[51].value())
        self.assertNotIn(20, script.component.held)
        run.frame = max(run.frame + 160, end)
        match.update(run.frame * combat.TICK, run.frame, frames=False)
        self.assertFalse(weapon.vars[51].value())
        self.assertEqual(weapon.vars[53].value(), 4)
        self.assertEqual(match.combat.vitals[player.body].total, 500)

    def melee_fixture(self, name):
        definition = next(d for d in self.definitions.values() if d.name == name)
        run, match, player, bot = self.create(definition)
        bot.position = (0, 1, 2)
        return run, match, player, bot

    @staticmethod
    def melee_input(run, bot, pressed):
        run.step({})
        bot.body_script.advance_live(run.frame, bot.position, ability_input=(31, pressed))

    def test_trooper_and_heavy_authored_melee_damages_armour_once_per_hold(self):
        for name, attack, damage in (('Trooper', 0x1422, 25), ('Heavy Assault', 0x140E, 75)):
            with self.subTest(npc=name):
                run, match, player, bot = self.melee_fixture(name)
                for _ in range(12):
                    run.step({})
                self.melee_input(run, bot, True)
                self.assertEqual(match.combat.vitals[player.body].total, 500)
                for _ in range(65):
                    self.melee_input(run, bot, True)
                self.assertEqual(match.combat.vitals[player.body].total, 500 - damage)
                instance = next(i for i in bot.body_script.component.instances.values()
                                if i.graph.index == attack)
                node = instance.graph.node(4 if attack == 0x1422 else 5)
                self.assertEqual(instance.node_state(node).counter, 1)

    def test_trooper_and_heavy_melee_reject_early_repress_and_allow_fresh_recast(self):
        for name, attack, pos, damage in (('Trooper', 0x1422, 8, 25), ('Heavy Assault', 0x140E, 9, 75)):
            with self.subTest(npc=name):
                run, match, player, bot = self.melee_fixture(name)
                for _ in range(12):
                    run.step({})
                instance = next(i for i in bot.body_script.component.instances.values()
                                if i.graph.index == attack)
                ability = instance.node_state(instance.graph.node(pos))
                self.melee_input(run, bot, True)
                self.melee_input(run, bot, False)
                self.assertEqual(ability.cur, 1)
                for offset in range(65):
                    self.melee_input(run, bot, offset == 5)
                self.assertEqual(ability.remaining(), 0)
                self.assertEqual(match.combat.vitals[player.body].total, 500 - damage)
                player.mover.position[:] = (0, 1, 0)
                player.mover.velocity = (0, 0, 0)
                player.mover.body = None
                self.melee_input(run, bot, True)
                for _ in range(40):
                    self.melee_input(run, bot, False)
                self.assertEqual(match.combat.vitals[player.body].total, 500 - 2 * damage)

    def test_trooper_and_heavy_melee_reject_range_allies_and_walls(self):
        for name in ('Trooper', 'Heavy Assault'):
            for reason in ('range', 'ally', 'wall'):
                with self.subTest(npc=name, reason=reason):
                    run, match, player, bot = self.melee_fixture(name)
                    if reason == 'range':
                        bot.position = (0, 1, 9)
                    elif reason == 'ally':
                        bot.team = player.team
                    else:
                        match.collision_world = World(box(-30, 30, -1, 1, -30, 30)
                                                      + wall(-8, 1, 8, 1, -2, 8))
                    for _ in range(65):
                        self.melee_input(run, bot, True)
                    self.assertEqual(match.combat.vitals[player.body].total, 500)

    def test_actual_ice_wall_occludes_trooper_and_heavy_melee(self):
        for name, damage in (('Trooper', 25), ('Heavy Assault', 75)):
            definition = next(d for d in self.definitions.values() if d.name == name)
            for cancel in (False, True):
                with self.subTest(npc=name, cancel=cancel):
                    run, match, player, bot = self.create(definition, control_caster=True)
                    fight = match.combat
                    mei, _ = self.deploy_ice_wall_for_npc(run, fight)
                    if cancel:
                        run.step({mei: (0, 0, 16)})
                        self.assertFalse(fight.walls.pieces)
                    player.mover.position[:] = (0, 1, 3.9)
                    player.mover.body = None
                    bot.position = (0, 1, 5.9)
                    for _ in range(65):
                        self.melee_input(run, bot, True)
                    self.assertEqual(fight.vitals[player.body].total, 500 - damage if cancel else 500)

    def test_pending_trooper_and_heavy_melee_resolves_during_idle_world_time(self):
        for name, damage in (('Trooper', 25), ('Heavy Assault', 75)):
            with self.subTest(npc=name):
                run, match, player, bot = self.melee_fixture(name)
                for _ in range(12):
                    run.step({})
                self.melee_input(run, bot, True)
                self.melee_input(run, bot, False)
                self.assertEqual(match.combat.vitals[player.body].total, 500)
                end = run.frame + 70
                match.update(end * combat.TICK, end, frames=False)
                self.assertEqual(match.combat.vitals[player.body].total, 500 - damage)

    def test_actual_flashbang_interrupts_pending_trooper_and_heavy_melee(self):
        for name, damage in (('Trooper', 25), ('Heavy Assault', 75)):
            with self.subTest(npc=name):
                definition = next(d for d in self.definitions.values() if d.name == name)
                run, match, player, bot = self.create(definition, control_caster=True)
                bot.position = (0, 1, 2)
                for _ in range(12):
                    run.step({})
                self.melee_input(run, bot, True)
                end = self.flash_npc(run, match, bot)
                while run.frame < end + 20:
                    run.step({})
                self.assertEqual(match.combat.vitals[player.body].total, 500)
                self.assertFalse(bot.body_script.component.held)
                for _ in range(180):
                    self.melee_input(run, bot, True)
                    if match.combat.vitals[player.body].total < 500:
                        break
                self.assertEqual(match.combat.vitals[player.body].total, 500 - damage)

    def test_trooper_and_heavy_melee_contact_actual_winston_sphere_and_body(self):
        for name, raw in (('Trooper', 30), ('Heavy Assault', 80)):
            with self.subTest(npc=name):
                run, match, player, bot = self.melee_fixture(name)
                run.step({player: (0, 0, 16)})
                for _ in range(12):
                    run.step({})
                bubble = match.combat.barriers.actors[player.body]
                player.mover.position[:] = (0, 1, 4.2)
                player.mover.body = None
                bot.position = (0, 1, 6)
                for _ in range(65):
                    self.melee_input(run, bot, True)
                self.assertEqual(bubble.health, 700 - raw)
                self.assertEqual(match.combat.vitals[player.body].total, 505 - raw)

    def test_heavy_primary_damages_winston(self):
        self.incoming('Heavy Assault')

    def test_heavy_primary_volley_fires_beyond_the_unbound_ammo_value(self):
        definition = next(d for d in self.definitions.values() if d.name == 'Heavy Assault')
        run, match, player, bot = self.create(definition)
        bot.yaw = 0
        weapon = next(i for i in bot.body_script.component.instances.values() if i.graph.index == 0x114F)
        self.assertIsNone(weapon.graph.node(47).field('m_24E351A4'))
        count = 0
        for _ in range(1400):
            run.step({})
            shots = self.emit_primary(run, match, bot)
            self.assertTrue(all(state.instance is weapon for state, *_ in shots))
            count += len(shots)
            if count >= 151:
                break
        self.assertGreater(count, 150)
        self.assertEqual(weapon.vars[53].value(), 150)
        self.assertEqual(match.combat.vitals[player.body].total, 500)

    def charge_fixture(self, *, wall_at=None, control_caster=False):
        definition = next(d for d in self.definitions.values() if d.name == 'Heavy Assault')
        run, match, player, bot = self.create(definition, control_caster=control_caster)
        bot.position = (0, 1, 9)
        player.mover.position[:] = (10, 1, 0)
        if wall_at is not None:
            match.collision_world = World(box(-60, 60, -1, 1, -60, 60)
                                          + wall(-8, wall_at, 8, wall_at, -2, 8))
        for _ in range(15):
            run.step({})
        return run, match, player, bot

    @staticmethod
    def charge_input(run, bot, pressed):
        run.step({})
        bot.body_script.advance_live(run.frame, bot.position, ability_input=(35, pressed))

    def test_heavy_charge_original_windup_travel_idle_clock_and_timeout(self):
        run, match, player, bot = self.charge_fixture()
        instance = heavy_charge.charge(bot)
        self.charge_input(run, bot, True)
        self.charge_input(run, bot, False)
        initial = run.frame - 1
        self.assertEqual(heavy_charge.phase(instance), 1)
        for _ in range(48):
            self.charge_input(run, bot, False)
        self.assertEqual(bot.position, (0, 1, 9))
        self.assertFalse(heavy_charge.travelling(instance))
        self.charge_input(run, bot, False)
        self.assertTrue(heavy_charge.travelling(instance))
        self.charge_input(run, bot, False)
        self.assertAlmostEqual(bot.position[2], 9 - 15 * combat.TICK)
        self.assertAlmostEqual(bot.velocity[2], -15)
        end = initial + 240
        match.update(end * combat.TICK, end, frames=False)
        self.assertEqual(heavy_charge.phase(instance), 0)
        self.assertAlmostEqual(bot.position[2], -35.88)
        self.assertEqual(bot.velocity, (0, 0, 0))
        self.assertEqual(instance.state(41).cur, 15)
        self.assertEqual(match.combat.vitals[player.body].total, 500)

    def test_heavy_charge_recovery_188_has_no_play_script_recipient(self):
        _run, _match, _player, bot = self.charge_fixture()
        instance = heavy_charge.charge(bot)
        node = instance.graph.node(188)
        resource = play.resolve_resource(instance, node)
        self.assertEqual(resource.guid, heavy_charge.STUN_GRAPH_GUID)
        self.assertIsNone(node.field("m_targets"))
        self.assertIsNone(play.resolve_request(
            instance, node, resource,
            context_provider=lambda: self.fail("empty targets cannot resolve context"),
        ))

    def test_heavy_clash_graph_dispatches_stun_to_self_and_first_recipient(self):
        run, match, player, bot = self.charge_fixture()
        definition = next(d for d in self.definitions.values() if d.name == 'Heavy Assault')
        other = npc.spawn(match, definition, (0, 1, 12), team=0, yaw=0)
        self.charge_input(run, bot, True)
        instance = heavy_charge.charge(bot)
        self.assertEqual(heavy_charge.phase(instance), 1)
        # Explicit tracker output isolates authored action dispatch from node 134 selection.
        from ow174.game.script import expr

        instance.write((expr.INSTANCE, 597), (expr.Entity(other.entity), expr.Entity(player.body)))
        instance.run_node(instance.graph.node(131), None)
        instance.component.run_now()
        fight = match.combat
        deadline = fight.tick + math.ceil(2 / combat.TICK)
        self.assertEqual(fight.stuns, {bot.entity: deadline, other.entity: deadline})
        self.assertEqual(heavy_charge.phase(instance), 0)
        self.assertEqual(fight.vitals[player.body].total, 500)
        self.assertFalse(fight.is_stunned(player))
        match.update((deadline - 1) * combat.TICK, deadline - 1, frames=False)
        self.assertTrue(fight.is_stunned(bot))
        self.assertTrue(fight.is_stunned(other))
        match.update(deadline * combat.TICK, deadline, frames=False)
        self.assertFalse(fight.is_stunned(bot))
        self.assertFalse(fight.is_stunned(other))

    def test_heavy_clash_graph_routes_actual_fortify_without_stun(self):
        from ow174.game.script import expr

        run, match, player, bot = self.charge_fixture()
        self.assertTrue(match.switch_hero(player, heroes()[protections.ORISA]))
        run.step({player: (0, 0, 8)})
        self.assertTrue(protections.force_immune(player))
        self.charge_input(run, bot, True)
        instance = heavy_charge.charge(bot)
        self.assertEqual(heavy_charge.phase(instance), 1)
        instance.write((expr.INSTANCE, 597), (expr.Entity(player.body),))
        instance.run_node(instance.graph.node(131), None)
        instance.component.run_now()
        self.assertTrue(expr.truthy(instance.vars[559].value()))
        self.assertEqual(heavy_charge.phase(instance), 0)
        self.assertFalse(match.combat.stuns)

    def test_live_opposed_heavy_charges_clash_from_authored_flags(self):
        run, match, player, bot = self.charge_fixture()
        definition = next(d for d in self.definitions.values() if d.name == 'Heavy Assault')
        other = npc.spawn(match, definition, (0, 1, 3), team=0, yaw=0)
        fight = match.combat
        for frame in range(90):
            run.step({})
            for actor in (bot, other):
                actor.body_script.advance_live(run.frame, actor.position, ability_input=(35, frame == 0))
            if fight.stuns:
                break
        deadline = fight.tick + math.ceil(2 / combat.TICK)
        self.assertEqual(fight.stuns, {bot.entity: deadline, other.entity: deadline})
        self.assertEqual(fight.vitals[player.body].total, 500)
        self.assertTrue(all(heavy_charge.phase(heavy_charge.charge(actor)) == 0 for actor in (bot, other)))

    def test_live_heavy_clash_rejects_same_direction_and_allies(self):
        definition = next(d for d in self.definitions.values() if d.name == 'Heavy Assault')
        for team, yaw in ((0, 32768), (1, 0)):
            with self.subTest(team=team, yaw=yaw):
                run, match, player, bot = self.charge_fixture()
                other = npc.spawn(match, definition, (0, 1, 7), team=team, yaw=yaw)
                for frame in range(80):
                    run.step({})
                    for actor in (bot, other):
                        actor.body_script.advance_live(
                            run.frame, actor.position, ability_input=(35, frame == 0))
                    self.assertFalse(match.combat.stuns)
                for actor in (bot, other):
                    self.assertTrue(heavy_charge.travelling(heavy_charge.charge(actor)))
                self.assertEqual(match.combat.vitals[player.body].total, 500)

    def test_live_heavy_clash_selects_fortify_without_stunning_either_actor(self):
        from ow174.game.script import expr

        run, match, player, bot = self.charge_fixture()
        self.assertTrue(match.switch_hero(player, heroes()[protections.ORISA]))
        player.mover.position[:] = (0, 1, 5)
        player.mover.body = None
        run.step({player: (0, 0, 8)})
        self.assertTrue(protections.force_immune(player))
        self.charge_input(run, bot, True)
        instance = heavy_charge.charge(bot)
        for _ in range(90):
            self.charge_input(run, bot, False)
            if heavy_charge.phase(instance) == 0:
                break
        self.assertTrue(expr.truthy(instance.vars[559].value()))
        self.assertEqual(heavy_charge.phase(instance), 0)
        self.assertFalse(match.combat.stuns)
        self.assertNotIn(bot.entity, match.combat.heavy_charge_pins)

    def test_heavy_impacts_actual_deployed_ice_wall_pillar(self):
        run, match, player, bot = self.charge_fixture()
        self.assertTrue(match.switch_hero(player, heroes()[protections.MEI]))
        player.mover.position[:] = (0, 1, 4)
        player.mover.body = None
        fight = match.combat
        angle = aim(fight._eye(player), (0, 1, 7))
        for button in (16, 0, 1, 0):
            run.step({player: (*angle, button)})
        self.assertEqual(len(fight.walls.pieces), 5)
        centre = min(fight.walls.pieces.values(), key=lambda piece: abs(piece.feet[0]))
        self.assertTrue(centre.impact_marked)
        self.charge_input(run, bot, True)
        for _ in range(70):
            self.charge_input(run, bot, False)
            if centre.health < 400:
                break
        self.assertEqual(centre.health, 225)
        self.assertFalse(fight.is_stunned(player))
        self.assertNotIn(bot.entity, fight.heavy_charge_pins)

    def test_heavy_crushes_bound_winston_against_actual_ice_wall(self):
        for friendly_wall, projected in product((False, True), repeat=2):
            with self.subTest(friendly_wall=friendly_wall, projected=projected):
                self.check_heavy_crush_against_ice_wall(friendly_wall, projected)

    def check_heavy_crush_against_ice_wall(self, friendly_wall, projected):
        run, match, player, bot = self.charge_fixture(control_caster=True)
        mei = run.players[1]
        self.assertTrue(match.switch_hero(mei, heroes()[protections.MEI]))
        if friendly_wall:
            mei.team = bot.team
        mei.mover.position[:] = (0, 1, 2)
        mei.mover.body = None
        player.mover.position[:] = (0, 1, 7)
        player.mover.body = None
        fight = match.combat
        angle = aim(fight._eye(mei), (0, 1, 5))
        for button in (16, 0, 1, 0):
            run.step({mei: (*angle, button)})
        self.assertEqual(len(fight.walls.pieces), 5)
        centre = min(fight.walls.pieces.values(), key=lambda piece: abs(piece.feet[0]))
        support = None
        if projected:
            zarya_hero = 0x02E0000000000068
            support = match.add_player(3, 'Zarya', zarya_hero, player.team, False)
            in_world(match, support)
            run.players.append(support)
            self.assertTrue(match.switch_hero(support, heroes()[zarya_hero]))
            support.mover.position[:] = (3, 1, 7)
            support.mover.body = None
        self.charge_input(run, bot, True)
        pinned = False
        shield = None
        for _ in range(100):
            self.charge_input(run, bot, False)
            if not pinned and bot.entity in fight.heavy_charge_pins and support is not None:
                pinned = True
                yaw, pitch = aim(fight._eye(support), fight._eye(player))
                run.step({support: (yaw, pitch, 16)})
            if support is not None and shield is None:
                shield = fight.zarya_barriers.projected.get(support.body)
            pinned |= bot.entity in fight.heavy_charge_pins
            if pinned and bot.entity not in fight.heavy_charge_pins:
                break
        self.assertTrue(pinned)
        self.assertEqual(centre.health, 400 if friendly_wall else 225)
        self.assertEqual(fight.vitals[player.body].total, 500 if projected else 205)
        if projected:
            self.assertIsNotNone(shield)
            self.assertEqual(shield.remaining, 0)
        self.assertNotIn(bot.entity, fight.heavy_charge_pins)
        self.assertFalse(fight.is_stunned(player))
        self.assertGreater(bot.position[2], centre.feet[2])

    def test_actual_ice_wall_occludes_heavy_graze_and_pin(self):
        for x in (0, 2):
            for cancel in (False, True):
                with self.subTest(x=x, cancel=cancel):
                    run, match, player, bot = self.charge_fixture(control_caster=True)
                    fight = match.combat
                    mei, pillar = self.deploy_ice_wall_for_npc(run, fight)
                    if cancel:
                        run.step({mei: (0, 0, 16)})
                        self.assertFalse(fight.walls.pieces)
                    player.mover.position[:] = (x, 1, 3.9)
                    player.mover.body = None
                    self.charge_input(run, bot, True)
                    pinned = grazed = False
                    for _ in range(100):
                        self.charge_input(run, bot, False)
                        pinned |= bot.entity in fight.heavy_charge_pins
                        grazed |= player.body in fight.heavy_charge_contacts.get(bot.entity, ())
                        if cancel and (pinned or grazed):
                            break
                    self.assertEqual(pinned, cancel and x == 0)
                    self.assertEqual(grazed, cancel and x == 2)
                    self.assertEqual(fight.vitals[player.body].total, 455 if grazed else 500)
                    self.assertEqual(pillar.health, 400 if cancel else 225)

    def test_rotated_ice_wall_blocks_heavy_graze_search(self):
        run, match, player, bot = self.charge_fixture(control_caster=True)
        fight = match.combat
        mei = run.players[1]
        self.assertTrue(match.switch_hero(mei, heroes()[protections.MEI]))
        mei.mover.position[:] = (1, 1, 4)
        mei.mover.body = None
        angle = aim(fight._eye(mei), (1, 1, 7))
        for button in (16, 0, 2, 0, 1, 0):
            run.step({mei: (*angle, button)})
        self.assertEqual(len(fight.walls.pieces), 5)
        bot.position = (0, 1, 14)
        self.charge_input(run, bot, True)
        instance = heavy_charge.charge(bot)
        for _ in range(60):
            self.charge_input(run, bot, False)
            if heavy_charge.travelling(instance):
                break
        self.assertTrue(heavy_charge.travelling(instance))
        # Fix the contact pose before resolving impact so both queries use the same cone.
        bot.position = (0, 1, 8.76)
        player.mover.position[:] = (2, 1, 7)
        tracker = instance.node_state(instance.graph.node(heavy_charge.GRAZE_SEARCH))
        world = instance.component.world
        self.assertNotIn(player.body, heavy_charge.targets(world, tracker))
        bot.position = (0, 1, 14)  # keep the active charge away from pillar impact during cancel
        run.step({mei: (0, 0, 16)})
        self.assertFalse(fight.walls.pieces)
        self.assertTrue(heavy_charge.travelling(instance))
        bot.position = (0, 1, 8.76)
        player.mover.position[:] = (2, 1, 7)
        self.assertIn(player.body, heavy_charge.targets(world, tracker))

    def test_actual_fortify_excludes_heavy_pin_and_graze(self):
        for x in (0, 2):
            with self.subTest(x=x):
                run, match, player, bot = self.charge_fixture()
                self.assertTrue(match.switch_hero(player, heroes()[protections.ORISA]))
                player.mover.position[:] = (x, 1, 7)
                player.mover.body = None
                run.step({player: (0, 0, 8)})
                self.assertTrue(protections.force_immune(player))
                fight = match.combat
                before = fight.vitals_of(next(t for t in fight._targets() if t.owner is player)).total
                self.charge_input(run, bot, True)
                for _ in range(75):
                    self.charge_input(run, bot, False)
                    self.assertNotIn(bot.entity, match.combat.heavy_charge_pins)
                if x == 0:
                    self.assertEqual(bot.position[2], 9)
                    self.assertEqual(heavy_charge.phase(heavy_charge.charge(bot)), 0)
                else:
                    self.assertLess(bot.position[2], 9)
                loss = before - match.combat.vitals[player.body].total
                self.assertEqual(loss, 0)
                self.assertFalse(fight.is_stunned(player))
                self.assertNotIn(player.body, match.combat.heavy_charge_contacts.get(bot.entity, ()))
                self.assertEqual(tuple(player.mover.position), (x, 1, 7))

    def test_heavy_charge_wall_ends_travel_and_repress_cannot_bypass_cooldown(self):
        run, match, player, bot = self.charge_fixture(wall_at=2)
        instance = heavy_charge.charge(bot)
        self.charge_input(run, bot, True)
        for _ in range(80):
            self.charge_input(run, bot, False)
        stopped = bot.position
        self.assertGreater(stopped[2], 2)
        self.assertLess(stopped[2], 4)
        self.assertEqual(heavy_charge.phase(instance), 0)
        self.assertGreater(instance.state(41).remaining(), 14)
        for _ in range(80):
            self.charge_input(run, bot, True)
        self.assertEqual(bot.position, stopped)
        self.assertEqual(heavy_charge.phase(instance), 0)
        self.assertEqual(match.combat.vitals[player.body].total, 500)

    def test_actual_flashbang_cancels_heavy_charge_windup(self):
        run, match, player, bot = self.charge_fixture(control_caster=True)
        caster = run.players[1]
        caster.mover.position[:] = (2, 1, 9)
        caster.mover.body = None
        instance = heavy_charge.charge(bot)
        self.charge_input(run, bot, True)
        self.assertEqual(heavy_charge.phase(instance), 1)
        end = self.flash_npc(run, match, bot)
        while run.frame < end + 70:
            run.step({})
        self.assertEqual(heavy_charge.phase(instance), 0)
        self.assertEqual(bot.position, (0, 1, 9))
        self.assertEqual(match.combat.vitals[player.body].total, 500)

    def test_heavy_charge_fresh_press_after_cooldown_restarts_windup(self):
        run, match, _, bot = self.charge_fixture(wall_at=7)
        instance = heavy_charge.charge(bot)
        self.charge_input(run, bot, True)
        for _ in range(60):
            self.charge_input(run, bot, False)
        self.assertEqual(heavy_charge.phase(instance), 0)
        cooldown = instance.state(41)
        self.assertGreater(cooldown.remaining(), 14)
        self.charge_input(run, bot, True)
        self.charge_input(run, bot, False)
        run.frame += math.ceil(15 / combat.TICK)
        match.update(run.frame * combat.TICK, run.frame, frames=False)
        self.charge_input(run, bot, False)
        self.assertEqual(cooldown.remaining(), 0)
        self.assertEqual(heavy_charge.phase(instance), 0)
        self.charge_input(run, bot, True)
        self.assertEqual(heavy_charge.phase(instance), 1)

    def test_heavy_charge_grazes_winston_once_and_retires_contact_at_exit(self):
        run, match, player, bot = self.charge_fixture()
        player.mover.position[:] = (2, 1, 7)
        player.mover.body = None
        self.charge_input(run, bot, True)
        for _ in range(65):
            self.charge_input(run, bot, False)
            if match.combat.vitals[player.body].total < 500:
                break
        self.assertEqual(match.combat.vitals[player.body].total, 455)
        self.assertEqual(match.combat.heavy_charge_contacts[bot.entity], {player.body})
        vx, vy, vz = player.mover.velocity
        self.assertAlmostEqual(math.hypot(vx, vz), 30)
        self.assertAlmostEqual(vy, 10)
        for _ in range(10):
            self.charge_input(run, bot, False)
        self.assertGreater(player.mover.position[0], 2)
        self.assertGreater(player.mover.position[1], 1)
        end = run.frame + 200
        match.update(end * combat.TICK, end, frames=False)
        self.assertNotIn(bot.entity, match.combat.heavy_charge_contacts)
        self.assertEqual(match.combat.vitals[player.body].total, 455)

    def test_heavy_charge_side_graze_rejects_range_teammate_and_occlusion(self):
        for reason, x in (('range', 5), ('ally', 2), ('wall', 2), ('frontal route', 0)):
            with self.subTest(reason=reason):
                run, match, player, bot = self.charge_fixture()
                player.mover.position[:] = (x, 1, 7)
                player.mover.body = None
                if reason == 'ally':
                    bot.team = player.team
                if reason == 'wall':
                    match.collision_world = World(box(-60, 60, -1, 1, -60, 60)
                                                  + wall(1, 4, 1, 11, -2, 8))
                self.charge_input(run, bot, True)
                for _ in range(80):
                    self.charge_input(run, bot, False)
                self.assertEqual(match.combat.vitals[player.body].total, 500)
                self.assertNotIn(bot.entity, match.combat.heavy_charge_contacts)

    def test_projected_barrier_absorbs_one_heavy_graze_and_removal_retires_contact(self):
        for initial in (200, 1):
            with self.subTest(barrier_health=initial):
                run, match, player, bot = self.charge_fixture(control_caster=True)
                caster = run.players[1]
                self.assertTrue(match.switch_hero(caster, heroes()[0x02E0000000000068]))
                caster.mover.position[:] = (4, 1, 7)
                caster.mover.body = None
                player.mover.position[:] = (2, 1, 7)
                player.mover.body = None
                self.charge_input(run, bot, True)
                for _ in range(35):
                    self.charge_input(run, bot, False)
                yaw, pitch = aim(match.combat._eye(caster), match.combat._eye(player))
                run.step({caster: (yaw, pitch, 16)})
                for _ in range(4):
                    self.charge_input(run, bot, False)
                fight = match.combat
                self.assertTrue(fight.zarya_barriers.protects(player))
                shield = fight.zarya_barriers.projected[caster.body]
                self.assertEqual(shield.remaining, 200)
                shield.remaining = initial
                for _ in range(23):
                    self.charge_input(run, bot, False)
                self.assertEqual(shield.remaining, max(0, initial - 50))
                for _ in range(10):
                    self.charge_input(run, bot, False)
                self.assertAlmostEqual(caster.body_script.component.entity_var(87).value(),
                                       min(initial, 50) / 5, places=4)
                if initial == 1:
                    self.assertFalse(fight.zarya_barriers.protects(player))
                self.assertNotIn(bot.entity, fight.heavy_charge_pins)
                self.assertEqual(match.combat.vitals[player.body].total, 500)
                self.assertAlmostEqual(player.mover.position[0], 2)
                self.assertIn(player.body, match.combat.heavy_charge_contacts[bot.entity])
                self.assertEqual(npc.remove_spawned(match), (bot.entity,))
                self.assertNotIn(bot.entity, match.combat.heavy_charge_contacts)

    def test_trooper_and_heavy_melee_apply_authored_impulse_on_living_contact(self):
        for name, horizontal, vertical in (('Trooper', 1, 0), ('Heavy Assault', 20, 8)):
            with self.subTest(npc=name):
                run, match, player, bot = self.melee_fixture(name)
                for _ in range(70):
                    self.melee_input(run, bot, True)
                    if match.combat.vitals[player.body].total < 500:
                        break
                self.assertLess(match.combat.vitals[player.body].total, 500)
                self.assertAlmostEqual(player.mover.velocity[2], -horizontal)
                self.assertAlmostEqual(player.mover.velocity[1], vertical)
                if vertical:
                    self.melee_input(run, bot, True)
                    self.assertLess(player.mover.position[2], 0)
                    self.assertGreater(player.mover.position[1], 1)

    def test_lethal_heavy_graze_does_not_enqueue_impulse(self):
        run, match, player, bot = self.charge_fixture()
        player.mover.position[:] = (2, 1, 7)
        player.mover.body = None
        fight = match.combat
        vitals = fight.vitals[player.body]
        vitals.now[combat.ARMOUR_POOL] = 0
        vitals.now[combat.HEALTH_POOL] = 20
        with patch.object(fight.impulses, 'apply', wraps=fight.impulses.apply) as apply:
            self.charge_input(run, bot, True)
            for _ in range(65):
                self.charge_input(run, bot, False)
                if vitals.dead:
                    break
        self.assertTrue(vitals.dead)
        self.assertEqual(vitals.killer, bot.entity)
        apply.assert_not_called()

    def pin_fixture(self, *, wall_at=None, control_caster=False, deploy_barrier=False):
        run, match, player, bot = self.charge_fixture(wall_at=wall_at, control_caster=control_caster)
        player.mover.position[:] = (0, 1, 7)
        player.mover.body = None
        if deploy_barrier:
            run.step({player: (0, 0, 16)})
            for _ in range(3):
                run.step({})
        self.charge_input(run, bot, True)
        for _ in range(70):
            self.charge_input(run, bot, False)
            if bot.entity in match.combat.heavy_charge_pins:
                return run, match, player, bot
        self.fail("authored frontal search did not attach the victim graph")

    def test_heavy_frontal_pin_carries_winston_through_own_barrier(self):
        run, match, player, bot = self.pin_fixture(wall_at=0, deploy_barrier=True)
        fight = match.combat
        bubble = fight.barriers.actors[player.body]
        self.assertTrue(fight.actions_blocked(player))
        self.assertEqual(bubble.health, 700)
        for _ in range(90):
            self.charge_input(run, bot, False)
            if bot.entity not in fight.heavy_charge_pins:
                break
        self.assertNotIn(bot.entity, fight.heavy_charge_pins)
        self.assertLess(player.mover.position[2], bubble.centre[2] - bubble.radius)
        self.assertEqual(fight.vitals[player.body].total, 205)
        self.assertIs(fight.barriers.actors[player.body], bubble)
        self.assertEqual(bubble.health, 700)

    def test_actual_heavy_frontal_pin_blocks_winston_inputs_and_follows_until_wall(self):
        run, match, player, bot = self.pin_fixture(wall_at=3)
        fight = match.combat
        owner, app = fight.heavy_charge_pins[bot.entity]
        self.assertIs(owner, player)
        self.assertEqual(app.graph.index, 0x1407)
        self.assertEqual(app.source_entity(), bot.entity)
        self.assertTrue(fight.actions_blocked(player))
        before = tuple(player.mover.position)
        for _ in range(6):
            run.step({player: (12000, 0, 1 | 8 | 16 | 32)})
            bot.body_script.advance_live(run.frame, bot.position)
            self.assertTrue(fight.actions_blocked(player))
            separation = bot.movement_data.stand_radius + player.mover.data.stand_radius
            self.assertAlmostEqual(player.mover.position[2], bot.position[2] - separation)
            self.assertEqual(player.mover.yaw, 0)
        self.assertLess(player.mover.position[2], before[2])
        self.assertNotIn(player.body, fight.winston_jump.flights)
        self.assertNotIn(player.body, fight.barriers.actors)
        self.assertFalse(player.body_script.component.held)
        end = run.frame + 90
        match.update(end * combat.TICK, end, frames=False)
        self.assertFalse(fight.actions_blocked(player))
        self.assertNotIn(bot.entity, fight.heavy_charge_pins)
        self.assertNotIn(app.id, app.component.instances)
        self.assertEqual(fight.vitals[player.body].total, 205)
        self.assertEqual(heavy_charge.charge(bot).state(11).counter, 1)

    def test_actual_heavy_pin_retires_on_victim_replacement_or_source_removal(self):
        for reason in ('victim', 'source'):
            with self.subTest(reason=reason):
                _, match, player, bot = self.pin_fixture()
                _, app = match.combat.heavy_charge_pins[bot.entity]
                if reason == 'victim':
                    self.assertTrue(match.switch_hero(player, player.hero))
                else:
                    self.assertEqual(npc.remove_spawned(match), (bot.entity,))
                self.assertNotIn(bot.entity, match.combat.heavy_charge_pins)
                self.assertNotIn(app.id, app.component.instances)
                self.assertFalse(match.combat.actions_blocked(player))

    def test_allied_peacekeeper_kill_releases_heavy_pin_and_restores_jump(self):
        run, match, player, bot = self.pin_fixture(control_caster=True)
        fight = match.combat
        caster = run.players[1]
        _, app = fight.heavy_charge_pins[bot.entity]
        vitals = fight.vitals[bot.entity]
        vitals.damage(vitals.total - 1)  # injured source, actual shot owns the kill
        x, y, z = bot.position
        caster.mover.position[:] = (x + 2.5, y, z)
        caster.mover.body = None
        for _ in range(25):
            x, y, z = bot.position
            yaw, pitch = aim(fight._eye(caster), (x, y + 1, z))
            run.step({player: (0, 0, 8), caster: (yaw, pitch, 1)})
            if bot.dead:
                break
        self.assertTrue(bot.dead)
        self.assertEqual(vitals.killer, caster.body)
        self.assertNotIn(bot.entity, fight.heavy_charge_pins)
        self.assertNotIn(app.id, app.component.instances)
        self.assertFalse(fight.actions_blocked(player))
        self.assertNotIn(player.body, fight.winston_jump.flights)
        run.step({})
        run.step({player: (0, 0, 8)})
        self.assertIn(player.body, fight.winston_jump.flights)
        self.assertEqual(fight.vitals[player.body].total, 500)
        self.assertIn(bot, match.bots)

    def test_allied_sleep_dart_stops_heavy_pin_only_with_clear_projectile_path(self):
        for obstructed in (False, True):
            with self.subTest(obstructed=obstructed):
                run, match, player, bot = self.pin_fixture(wall_at=0, control_caster=True)
                fight = match.combat
                caster = run.players[1]
                self.assertTrue(match.switch_hero(caster, heroes()[0x02E000000000013B]))
                x, y, z = bot.position
                caster.mover.position[:] = (x + 2.5, y, z - 4)
                caster.mover.body = None
                if obstructed:
                    match.collision_world = World(
                        box(-60, 60, -1, 1, -60, 60) + wall(-8, 0, 8, 0, -2, 8)
                        + wall(x + 1.25, 0, x + 1.25, 16, 1, 8)
                    )
                _, app = fight.heavy_charge_pins[bot.entity]
                # Lead the charge across the dart's windup and lateral flight.
                yaw, pitch = aim(fight._eye(caster), (x, y + 1, z - 5))
                for frame in range(70):
                    run.step({caster: (yaw, pitch, 8 if frame == 0 else 0)})
                    if fight.is_asleep(bot):
                        break

                self.assertEqual(fight.is_asleep(bot), not obstructed)
                self.assertEqual(fight.vitals[player.body].total, 205 if obstructed else 500)
                self.assertEqual(heavy_charge.phase(heavy_charge.charge(bot)), 0)
                self.assertNotIn(bot.entity, fight.heavy_charge_pins)
                self.assertNotIn(app.id, app.component.instances)
                self.assertFalse(fight.actions_blocked(player))
                if not obstructed:
                    self.assertEqual(fight.sleeps[bot.entity] - run.frame, 313)
                    self.assertEqual(bot.velocity, (0., 0., 0.))
                    run.step({player: (-32768, 0, 8)})
                    self.assertIn(player.body, fight.winston_jump.flights)

    def test_allied_emp_stops_active_heavy_charge_and_releases_winston(self):
        run, match, player, bot = self.pin_fixture(control_caster=True)
        fight = match.combat
        caster = run.players[1]
        self.assertTrue(match.switch_hero(caster, heroes()[hacks.SOMBRA]))
        caster.mover.position[:] = (2, 1, 6)
        caster.mover.body = None
        script = caster.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        _, app = fight.heavy_charge_pins[bot.entity]
        run.step({caster: (0, 0, 32)})
        for _ in range(55):
            if fight.hacks.active(bot.entity):
                break
            run.step({})
        self.assertTrue(fight.hacks.active(bot.entity))
        run.step({})
        self.assertEqual(heavy_charge.phase(heavy_charge.charge(bot)), 0)
        self.assertNotIn(bot.entity, fight.heavy_charge_pins)
        self.assertNotIn(app.id, app.component.instances)
        self.assertFalse(fight.actions_blocked(player))
        self.assertFalse(fight.hacks.active(player.body))
        self.assertEqual(fight.vitals[player.body].total, 500)

    def test_actual_flashbang_releases_bound_heavy_charge_victim(self):
        run, match, player, bot = self.pin_fixture(control_caster=True)
        caster = run.players[1]
        caster.mover.position[:] = (1.2, 1, 5.5)
        caster.mover.body = None
        _, app = match.combat.heavy_charge_pins[bot.entity]
        yaw, pitch = aim(match.combat._eye(caster), (0, 2, 7))
        run.step({caster: (yaw, pitch, 16)})
        for _ in range(35):
            if match.combat.is_stunned(bot):
                break
            run.step({})
        self.assertTrue(match.combat.is_stunned(bot))
        self.assertFalse(match.combat.actions_blocked(player))
        self.assertNotIn(bot.entity, match.combat.heavy_charge_pins)
        self.assertNotIn(app.id, app.component.instances)
        self.assertEqual(match.combat.vitals[player.body].total, 500)

    def test_heavy_pin_cancels_pending_primal_punch_before_flashbang_rescue(self):
        run, match, player, bot = self.charge_fixture(control_caster=True)
        fight = match.combat
        script = player.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        run.step({player: (0, 0, 32)})
        self.charge_input(run, bot, True)
        charge = heavy_charge.charge(bot)
        for _ in range(70):
            self.charge_input(run, bot, False)
            if heavy_charge.travelling(charge):
                break
        self.assertTrue(heavy_charge.travelling(charge))
        weapon = next(i for i in script.component.instances.values() if i.graph.index == 0xFE)
        pending = False
        for _ in range(20):
            run.step({player: (0, 0, 1)})
            pending = any(weapon.states.get(i) is not None and weapon.states[i].active for i in (20, 21))
            if pending:
                break
        self.assertTrue(pending)
        player.mover.position[:] = (0, 1, bot.position[2] - 2)
        player.mover.body = None
        self.charge_input(run, bot, False)
        self.assertIn(bot.entity, fight.heavy_charge_pins)
        self.assertTrue(fight.actions_blocked(player))
        self.assertFalse(any(weapon.states.get(i) is not None and weapon.states[i].active
                             for i in (20, 21, 35, 36)))
        vitals = fight.vitals[bot.entity]
        before = vitals.total
        caster = run.players[1]
        caster.mover.position[:] = (1.2, 1, bot.position[2] - 1.5)
        caster.mover.body = None
        self.flash_npc(run, match, bot)
        self.assertNotIn(bot.entity, fight.heavy_charge_pins)
        self.assertFalse(fight.actions_blocked(player))
        self.assertTrue(combat.winston.primal_active(script.component))
        self.assertEqual(vitals.total, before - 25)
        bot.position = (0, 1, player.mover.position[2] + 1.5)
        for _ in range(25):
            run.step({})
            self.assertEqual(vitals.total, before - 25)
        for _ in range(50):
            run.step({player: (0, 0, 1)})
            if vitals.total < before - 25:
                break
        self.assertEqual(vitals.total, before - 65)
        self.assertEqual(script.component.entity_var(342).value(), 0)

    def test_open_floor_heavy_pin_timeout_releases_without_wall_damage(self):
        run, match, player, bot = self.pin_fixture()
        end = run.frame + 210
        match.update(end * combat.TICK, end, frames=False)
        self.assertFalse(match.combat.actions_blocked(player))
        self.assertNotIn(bot.entity, match.combat.heavy_charge_pins)
        self.assertEqual(match.combat.vitals[player.body].total, 500)
        self.assertEqual(heavy_charge.charge(bot).state(11).counter, 0)

    def test_lethal_wall_crush_releases_victim_once(self):
        run, match, player, bot = self.pin_fixture(wall_at=3)
        fight = match.combat
        _, app = fight.heavy_charge_pins[bot.entity]
        vitals = fight.vitals[player.body]
        vitals.now[combat.HEALTH_POOL] = 50
        for _ in range(40):
            self.charge_input(run, bot, False)
            if vitals.dead:
                break
        self.assertTrue(vitals.dead)
        self.assertEqual(vitals.killer, bot.entity)
        self.assertEqual(heavy_charge.charge(bot).state(11).counter, 1)
        self.assertNotIn(bot.entity, fight.heavy_charge_pins)
        self.assertNotIn(app.id, app.component.instances)

    def test_projected_barrier_applied_after_pin_absorbs_wall_crush(self):
        run, match, player, bot = self.pin_fixture(wall_at=3, control_caster=True)
        caster = run.players[1]
        self.assertTrue(match.switch_hero(caster, heroes()[0x02E0000000000068]))
        caster.mover.position[:] = (3, 1, player.mover.position[2])
        caster.mover.body = None
        yaw, pitch = aim(match.combat._eye(caster), match.combat._eye(player))
        run.step({caster: (yaw, pitch, 16)})
        for _ in range(6):
            self.charge_input(run, bot, False)
        self.assertTrue(match.combat.zarya_barriers.protects(player))
        shield = match.combat.zarya_barriers.projected[caster.body]
        for _ in range(30):
            self.charge_input(run, bot, False)
        self.assertEqual(shield.remaining, 0)
        self.assertEqual(match.combat.vitals[player.body].total, 500)
        self.assertFalse(match.combat.actions_blocked(player))

    def test_heavy_close_contact_cannot_apply_node100_to_ordinary_winston(self):
        for primal in (False, True):
            with self.subTest(primal=primal):
                run, match, player, bot = self.charge_fixture()
                player.mover.position[:] = (0, 1, 8.3)
                player.mover.body = None
                if primal:
                    script = player.body_script
                    script.set_ult_charge(script.component.entity_var(338).value())
                    run.step({player: (0, 0, 32)})
                self.charge_input(run, bot, True)
                for _ in range(70):
                    self.charge_input(run, bot, False)
                fight = match.combat
                self.assertEqual(fight.vitals[player.body].total, 1000 if primal else 500)
                self.assertFalse(fight.stuns)
                instance = heavy_charge.charge(bot)
                tracker = instance.states[instance.graph.node(heavy_charge.CLASH_SEARCH).state]
                self.assertGreater(tracker.counter, 0)
                self.assertFalse(tracker.targets)
                state = instance.states.get(instance.graph.node(heavy_charge.IMPACT_HEALTH).state)
                self.assertTrue(state is None or state.counter == 0)

    @staticmethod
    def author_pillar_impact_flag(player):
        # Isolate the actual 0636 wall marker writer on a body fixture.
        # The test exercises dispatch and does not model pillar deployment.
        component = player.body_script.component
        pillar = component.create(graph.graph(0x0636), init=False)
        pillar.run_node(pillar.graph.node(27), None)
        component.run_now()

    def test_heavy_tagged_impact_fixture_damages_and_stuns_once(self):
        run, match, player, bot = self.charge_fixture()
        self.author_pillar_impact_flag(player)
        player.mover.position[:] = (0, 1, 8.3)
        player.mover.body = None
        self.charge_input(run, bot, True)
        for _ in range(70):
            self.charge_input(run, bot, False)
            if match.combat.is_stunned(player):
                break
        fight = match.combat
        self.assertEqual(fight.vitals[player.body].total, 330)
        self.assertEqual(fight.stuns[player.body] - run.frame, math.ceil(2 / combat.TICK))
        self.assertNotIn(bot.entity, fight.heavy_charge_pins)
        self.assertNotIn(bot.entity, fight.heavy_charge_impact_requests)
        instance = heavy_charge.charge(bot)
        self.assertEqual(instance.state(53).counter, 1)
        self.assertEqual(instance.state(41).cur, 15)
        end = fight.stuns[player.body]
        match.update((end - 1) * combat.TICK, end - 1, frames=False)
        self.assertTrue(fight.actions_blocked(player))
        match.update(end * combat.TICK, end, frames=False)
        self.assertFalse(fight.actions_blocked(player))
        self.assertEqual(fight.vitals[player.body].total, 330)

    def test_heavy_tagged_impact_finishes_the_separate_bound_victim(self):
        run, match, player, bot = self.pin_fixture(control_caster=True)
        other = run.players[1]
        self.author_pillar_impact_flag(other)
        other.mover.position[:] = (0.4, 1, bot.position[2] - 0.3)
        other.mover.body = None
        fight = match.combat
        _, app = fight.heavy_charge_pins[bot.entity]
        other_before = fight.vitals[other.body].total
        self.charge_input(run, bot, False)
        self.assertEqual(fight.vitals[player.body].total, 205)
        self.assertEqual(fight.vitals[other.body].total, other_before - 175)
        self.assertTrue(fight.is_stunned(other))
        self.assertFalse(fight.actions_blocked(player))
        self.assertNotIn(app.id, app.component.instances)

    def test_projected_barrier_absorbs_tagged_impact_fixture_without_stun(self):
        run, match, player, bot = self.charge_fixture(control_caster=True)
        self.author_pillar_impact_flag(player)
        caster = run.players[1]
        self.assertTrue(match.switch_hero(caster, heroes()[0x02E0000000000068]))
        caster.mover.position[:] = (4, 1, 8.3)
        caster.mover.body = None
        player.mover.position[:] = (0, 1, 8.3)
        player.mover.body = None
        self.charge_input(run, bot, True)
        for _ in range(35):
            self.charge_input(run, bot, False)
        yaw, pitch = aim(match.combat._eye(caster), match.combat._eye(player))
        run.step({caster: (yaw, pitch, 16)})
        for _ in range(35):
            self.charge_input(run, bot, False)
        fight = match.combat
        self.assertEqual(fight.zarya_barriers.projected[caster.body].remaining, 25)
        self.assertEqual(fight.vitals[player.body].total, 500)
        self.assertFalse(fight.actions_blocked(player))

    def test_projected_zarya_barrier_rejects_frontal_heavy_pin(self):
        run, match, player, bot = self.charge_fixture(control_caster=True)
        caster = run.players[1]
        self.assertTrue(match.switch_hero(caster, heroes()[0x02E0000000000068]))
        caster.mover.position[:] = (4, 1, 7)
        caster.mover.body = None
        player.mover.position[:] = (0, 1, 7)
        player.mover.body = None
        self.charge_input(run, bot, True)
        for _ in range(35):
            self.charge_input(run, bot, False)
        yaw, pitch = aim(match.combat._eye(caster), match.combat._eye(player))
        run.step({caster: (yaw, pitch, 16)})
        for _ in range(35):
            self.charge_input(run, bot, False)
        self.assertTrue(match.combat.zarya_barriers.protects(player))
        self.assertNotIn(bot.entity, match.combat.heavy_charge_pins)
        self.assertEqual(match.combat.vitals[player.body].total, 500)

    def test_actual_four_working_primaries_are_blocked_by_winston_shield(self):
        for name in ('Trooper', 'Enforcer', 'Sniper', 'Heavy Assault'):
            with self.subTest(npc=name):
                self.incoming(name, shield=True)

    def test_enforcer_primary_damages_winston(self):
        self.incoming('Enforcer')

    def test_enforcer_automatic_fire_hits_winston_and_releases_on_team_change(self):
        definition = next(d for d in self.definitions.values() if d.name == 'Enforcer')
        run, match, player, bot = self.create(definition, auto_target=True, auto_fire=True)
        bot.position = (4, 1, 0)
        weapon = next(i for i in bot.body_script.component.instances.values() if i.graph.index == 0x13EB)
        ammo = weapon.vars[53]
        vitals = match.combat.vitals_of(next(t for t in match.combat._targets() if t.owner is player))
        floor = match.collision_world
        match.collision_world = World(box(-40, 40, -1, 1, -40, 40) + wall(2, -20, 2, 20, 1, 8))
        for _ in range(70):
            run.step({})
        self.assertEqual(ammo.value(), 4)
        self.assertEqual(vitals.total, 500)

        match.collision_world = floor
        for _ in range(90):
            run.step({})
            if vitals.total < 500:
                break
        self.assertEqual(ammo.value(), 3)
        self.assertLess(vitals.total, 500)
        self.assertIn(20, bot.body_script.component.held)

        hostile_team = bot.team
        bot.team = player.team
        health = vitals.total
        for _ in range(80):
            run.step({})
        self.assertEqual(ammo.value(), 3)
        self.assertEqual(vitals.total, health)
        self.assertNotIn(20, bot.body_script.component.held)

        bot.team = hostile_team
        for _ in range(100):
            run.step({})
            if vitals.total < health:
                break
        self.assertEqual(ammo.value(), 2)
        self.assertLess(vitals.total, health)

    def test_enforcer_automatic_volley_hits_winston_barrier_before_body(self):
        definition = next(d for d in self.definitions.values() if d.name == 'Enforcer')
        run, match, player, bot = self.create(definition, auto_target=True, auto_fire=True)
        bot.position = (0, 1, 20)
        run.step({player: (0, 0, 16)})
        for _ in range(8):
            run.step({})
        bubble = match.combat.barriers.actors[player.body]
        self.assertEqual(bubble.health, 700)
        bot.position = (0, 1, 8)
        for _ in range(120):
            run.step({})
            if bubble.health < 700:
                break
        self.assertLess(bubble.health, 700)
        self.assertEqual(match.combat.vitals[player.body].total, 500)
        weapon = next(i for i in bot.body_script.component.instances.values() if i.graph.index == 0x13EB)
        self.assertEqual(weapon.vars[53].value(), 3)

    def test_enforcer_automatic_fire_releases_and_recovers_after_flashbang(self):
        definition = next(d for d in self.definitions.values() if d.name == 'Enforcer')
        run, match, player, bot = self.create(
            definition, control_caster=True, auto_target=True, auto_fire=True,
        )
        run.players[1].mover.position[:] = (4, 1, 1)
        weapon = next(i for i in bot.body_script.component.instances.values() if i.graph.index == 0x13EB)
        ammo = weapon.vars[53]
        vitals = match.combat.vitals_of(next(t for t in match.combat._targets() if t.owner is player))
        for _ in range(120):
            run.step({})
            if vitals.total < 500:
                break
        self.assertEqual(ammo.value(), 3)
        self.assertIn(20, bot.body_script.component.held)
        deadline = self.flash_npc(run, match, bot)
        self.assertNotIn(20, bot.body_script.component.held)
        while run.frame + 1 < deadline:
            run.step({})
        self.assertEqual(ammo.value(), 3)

        health = vitals.total
        for _ in range(120):
            run.step({})
            if vitals.total < health:
                break
        self.assertEqual(ammo.value(), 2)
        self.assertLess(vitals.total, health)

    def test_enforcer_directional_reaction_interrupts_and_resumes_automatic_fire(self):
        definition = next(d for d in self.definitions.values() if d.name == 'Enforcer')
        control, _, _, control_bot = self.create(definition, auto_target=True, auto_fire=True)
        control_bot.position = (4, 1, 0)
        control_weapon = next(i for i in control_bot.body_script.component.instances.values()
                              if i.graph.index == 0x13EB)
        control_ammo = control_weapon.vars[53]
        for _ in range(300):
            control.step({})
            if control_ammo.value() == 2:
                break
        self.assertEqual(control_ammo.value(), 2)
        uninterrupted_shot = control.frame

        run, match, player, bot = self.create(definition, auto_target=True, auto_fire=True)
        bot.position = (4, 1, 0)
        component = bot.body_script.component
        weapon = next(i for i in component.instances.values() if i.graph.index == 0x13EB)
        ammo = weapon.vars[53]
        vitals = match.combat.vitals_of(next(t for t in match.combat._targets() if t.owner is player))
        for _ in range(120):
            run.step({})
            if vitals.total < 500:
                break
        self.assertEqual(ammo.value(), 3)
        self.assertLess(vitals.total, 500)
        self.assertIn(20, component.held)
        while run.frame < uninterrupted_shot - 2:
            run.step({})
        self.assertEqual(ammo.value(), 3)
        before = vitals.total

        listener = next(i for i in component.instances.values() if i.graph.index == 0x0E20)
        payload = {0x0403: expr.Asset(0x0D80000000002596), 0x0407: True, 0x041C: (0, 0, -1)}
        runtime.send_instance_message(listener, npc_specials.TALON_HIT_MESSAGE, payload, player.body)
        component.run_now()
        wait = listener.states[listener.graph.node(27).state]
        self.assertTrue(wait.active)
        for _ in range(wait.ms // component.frame_ms + 3):
            run.step({})
            if not wait.active:
                break
            self.assertEqual(ammo.value(), 3)
            self.assertEqual(vitals.total, before)
        self.assertFalse(wait.active)
        self.assertGreater(run.frame, uninterrupted_shot)

        for _ in range(180):
            run.step({})
            if vitals.total < before:
                break
        self.assertIn(20, component.held)
        self.assertEqual(ammo.value(), 2)
        self.assertLess(vitals.total, before)

    def test_enforcer_controller_modifier_changes_route_and_winston_tesla_admission(self):
        definition = next(d for d in self.definitions.values() if d.name == 'Enforcer')
        for mode in ('none', 'manual', 'automatic'):
            with self.subTest(mode=mode):
                run, match, player, bot = self.create(definition, auto_target=mode == 'automatic')
                bot.position = (0, 1, 10.5)
                bot.route = [(0, 1, -10)]
                bot.wait = 0
                script = bot.body_script
                controller = next(i for i in script.controller.instances.values() if i.graph.index == 0x13EA)
                if mode == 'manual':
                    script.set_manual_target(player.entity)
                elif mode == 'none':
                    script.component.set(12299, 1)
                    run.step({})
                    script.advance_live(run.frame, bot.position)
                for _ in range(44):
                    run.step({})
                scale = npc_specials.enforcer_walk_scale(bot)
                self.assertAlmostEqual(scale, 1.0 if mode == 'none' else 0.4)
                self.assertAlmostEqual(bot.pace, bot.speed * scale)
                before = match.combat.vitals[bot.entity].total
                run.step({player: (0, 0, 1)})
                delta = before - match.combat.vitals[bot.entity].total
                if mode != 'none':
                    self.assertEqual(delta, 0)
                    self.assertTrue(controller.states[24].active)
                    self.assertEqual(script.controller.entity_var(12299).value(), 1)
                    if mode == 'manual':
                        script.set_manual_target()
                    else:
                        bot.team = player.team
                    for _ in range(8):
                        run.step({})
                    self.assertIsNone(npc_specials.enforcer_speed_state(bot))
                    self.assertAlmostEqual(bot.pace, bot.speed)
                else:
                    self.assertGreater(delta, 0)

    def test_enforcer_selected_target_hysteresis_and_visibility_release(self):
        self.check_enforcer_target_hysteresis_and_visibility_release(auto_target=False)

    def test_enforcer_automatic_target_uses_authored_hysteresis_and_visibility_release(self):
        self.check_enforcer_target_hysteresis_and_visibility_release(auto_target=True)

    def check_enforcer_target_hysteresis_and_visibility_release(self, *, auto_target):
        definition = next(d for d in self.definitions.values() if d.name == 'Enforcer')
        run, match, player, bot = self.create(definition, auto_target=auto_target)
        script = bot.body_script
        controller = next(i for i in script.controller.instances.values() if i.graph.index == 0x13EA)
        bot.position = (0, 1, 16)
        if not auto_target:
            script.set_manual_target(player.entity)
        for _ in range(5):
            run.step({})
        self.assertTrue(controller.states[25].active)
        self.assertAlmostEqual(controller.vars[680].value(), 16)
        self.assertEqual(npc_specials.enforcer_walk_scale(bot), 1)
        bot.position = (0, 1, 14)
        for _ in range(6):
            run.step({})
        self.assertAlmostEqual(npc_specials.enforcer_walk_scale(bot), .4)
        bot.position = (0, 1, 16)
        for _ in range(70):
            run.step({})
        self.assertAlmostEqual(npc_specials.enforcer_walk_scale(bot), .4)
        floor = match.collision_world
        match.collision_world = World(box(-40, 40, -1, 1, -40, 40) + wall(-20, 8, 20, 8, 1, 8))
        for _ in range(30):
            run.step({})
        self.assertTrue(controller.states[28].active)
        self.assertAlmostEqual(npc_specials.enforcer_walk_scale(bot), .4)
        run.frame += 50
        match.update(run.frame * combat.TICK, run.frame, frames=False)
        self.assertFalse(controller.states[28].active)
        self.assertEqual(npc_specials.enforcer_walk_scale(bot), 1)
        match.collision_world = floor
        bot.position = (0, 1, 14)
        for _ in range(6):
            run.step({})
        self.assertAlmostEqual(npc_specials.enforcer_walk_scale(bot), .4)
        bot.position = (0, 1, 18)
        for _ in range(70):
            run.step({})
        self.assertEqual(npc_specials.enforcer_walk_scale(bot), 1)
        bot.position = (0, 1, 14)
        for _ in range(6):
            run.step({})
        self.assertAlmostEqual(npc_specials.enforcer_walk_scale(bot), .4)
        bot.team = player.team
        for _ in range(6):
            run.step({})
        self.assertEqual(npc_specials.enforcer_walk_scale(bot), 1)
        self.assertFalse(controller.states[24].active)
        if auto_target:
            self.assertEqual(script.controller.world.manual_target, 0)

    def test_enforcer_automatic_slot_reselects_when_the_nearest_becomes_an_ally(self):
        definition = next(d for d in self.definitions.values() if d.name == 'Enforcer')
        run, _, _, bot = self.create(definition, control_caster=True, auto_target=True)
        caster = run.players[1]
        caster.mover.position[:] = (0, 1, 2)
        controller = next(i for i in bot.body_script.controller.instances.values()
                          if i.graph.index == 0x13EA)
        for _ in range(6):
            run.step({})
        self.assertAlmostEqual(controller.vars[680].value(), 2)
        self.assertAlmostEqual(npc_specials.enforcer_walk_scale(bot), .4)

        caster.team = bot.team
        for _ in range(6):
            run.step({})
        self.assertAlmostEqual(controller.vars[680].value(), 4)
        self.assertAlmostEqual(npc_specials.enforcer_walk_scale(bot), .4)
        script = bot.body_script
        self.assertEqual(script.controller.world.manual_target, 0)
        script.set_manual_target(caster.entity)
        for _ in range(6):
            run.step({})
        self.assertEqual(npc_specials.enforcer_walk_scale(bot), 1)
        script.set_manual_target()
        for _ in range(6):
            run.step({})
        self.assertAlmostEqual(controller.vars[680].value(), 4)
        self.assertAlmostEqual(npc_specials.enforcer_walk_scale(bot), .4)

    def test_enforcer_automatic_visibility_uses_deployed_ice_wall(self):
        definition = next(d for d in self.definitions.values() if d.name == 'Enforcer')
        run, match, _, bot = self.create(definition, control_caster=True, auto_target=True)
        mei = run.players[1]
        mei.mover.position[:] = (20, 1, 0)
        bot.position = (0, 1, 14)
        controller = next(i for i in bot.body_script.controller.instances.values()
                          if i.graph.index == 0x13EA)
        for _ in range(6):
            run.step({})
        self.assertAlmostEqual(controller.vars[680].value(), 14)
        self.assertAlmostEqual(npc_specials.enforcer_walk_scale(bot), .4)

        bot.position = (0, 1, 16)
        for _ in range(6):
            run.step({})
        self.assertNotIn(28, controller.states)
        self.assertAlmostEqual(npc_specials.enforcer_walk_scale(bot), .4)

        mei, pillar = self.deploy_ice_wall_for_npc(run, match.combat)
        mei.mover.position[:] = (20, 1, 0)
        mei.mover.body = None
        for _ in range(30):
            run.step({})
        self.assertAlmostEqual(controller.vars[680].value(), 16)
        self.assertTrue(controller.states[28].active)
        self.assertAlmostEqual(npc_specials.enforcer_walk_scale(bot), .4)
        run.frame += 50
        match.update(run.frame * combat.TICK, run.frame, frames=False)
        self.assertEqual(npc_specials.enforcer_walk_scale(bot), 1)
        self.assertEqual(pillar.health, 400)

        run.step({mei: (0, 0, 16)})
        self.assertFalse(match.combat.walls.pieces)
        bot.position = (0, 1, 14)
        for _ in range(6):
            run.step({})
        self.assertAlmostEqual(npc_specials.enforcer_walk_scale(bot), .4)

    def test_enforcer_selected_body_identity_retires_with_target(self):
        definition = next(d for d in self.definitions.values() if d.name == 'Enforcer')
        run, match, player, bot = self.create(definition)
        script = bot.body_script
        script.set_manual_target(player.body)
        for _ in range(6):
            run.step({})
        self.assertAlmostEqual(npc_specials.enforcer_walk_scale(bot), .4)
        self.assertTrue(match.switch_hero(player, heroes()[0x02E0000000000042]))
        player.mover.position[:] = (0, 1, 0)
        for _ in range(6):
            run.step({})
        self.assertEqual(npc_specials.enforcer_walk_scale(bot), 1)
        script.set_manual_target(player.body)
        for _ in range(6):
            run.step({})
        self.assertAlmostEqual(npc_specials.enforcer_walk_scale(bot), .4)

    def test_enforcer_original_four_shot_magazine_automatically_reloads(self):
        definition = next(d for d in self.definitions.values() if d.name == 'Enforcer')
        run, match, player, bot = self.create(definition)
        bot.yaw = 0
        weapon = next(i for i in bot.body_script.component.instances.values() if i.graph.index == 0x13EB)
        count = 0
        for _ in range(650):
            run.step({})
            shots = self.emit_primary(run, match, bot)
            count += len(shots)
            if shots:
                self.assertEqual(weapon.vars[53].value(), 4 - count)
            if count == 4:
                break
        self.assertEqual(count, 4)
        self.assertTrue(weapon.vars[51].value())
        for _ in range(120):
            if not weapon.vars[51].value():
                break
            run.step({})
            self.assertEqual(self.emit_primary(run, match, bot), [])
        self.assertFalse(weapon.vars[51].value())
        self.assertEqual(weapon.vars[53].value(), 4)
        bot.yaw = -32768
        for _ in range(160):
            run.step({})
            if self.emit_primary(run, match, bot):
                break
        self.assertEqual(weapon.vars[53].value(), 3)
        for _ in range(15):
            run.step({})
            self.emit_primary(run, match, bot, False)
        self.assertLess(match.combat.vitals[player.body].total, 500)

    def test_sniper_primary_damages_winston(self):
        self.incoming('Sniper')

    def sniper_move_fixture(self, *, control_caster=False):
        definition = next(d for d in self.definitions.values() if d.name == 'Sniper')
        run, match, player, bot = self.create(definition, control_caster=control_caster)
        bot.position = (0, 1, 2)
        run.step({})
        return run, match, player, bot

    def test_sniper_authored_move_to_runs_on_idle_world_time_and_enforces_recast(self):
        run, match, player, bot = self.sniper_move_fixture()
        self.assertTrue(sniper_reposition.request(match, bot, player))
        state = sniper_reposition.move_state(bot)
        destination, speed = state.move_request()
        self.assertEqual(state.instance.graph.index, 0x142C)
        self.assertEqual(state.node.pos, 16)
        self.assertEqual(speed, 20)
        self.assertAlmostEqual(destination.z, 6)
        self.assertFalse(sniper_reposition.request(match, bot, player))
        end = run.frame + 40
        match.update(end * combat.TICK, end, frames=False)
        run.frame = end
        self.assertFalse(state.active)
        self.assertAlmostEqual(bot.position[2], 6)
        self.assertEqual(bot.velocity, (0, 0, 0))
        self.assertNotIn(bot.entity, match.combat.sniper_repositions)
        player.mover.position[:] = (0, 1, 4)
        player.mover.body = None
        self.assertFalse(sniper_reposition.request(match, bot, player))
        end += math.ceil(6 / combat.TICK)
        match.update(end * combat.TICK, end, frames=False)
        self.assertTrue(sniper_reposition.request(match, bot, player))
        self.assertEqual(state.counter, 2)

    def test_sniper_authored_move_to_stops_at_ice_wall_and_obeys_recast(self):
        run, match, player, bot = self.sniper_move_fixture(control_caster=True)
        mei, pillar = self.deploy_ice_wall_for_npc(run, match.combat)
        self.assertTrue(sniper_reposition.request(match, bot, player))
        state = sniper_reposition.move_state(bot)
        self.assertAlmostEqual(state.move_request()[0].z, 6)
        for _ in range(40):
            run.step({})
        face = pillar.feet[2] - pillar.depth - bot.movement_data.stand_radius
        self.assertAlmostEqual(bot.position[2], face, delta=.04)
        self.assertFalse(state.active)
        self.assertNotIn(bot.entity, match.combat.sniper_repositions)
        self.assertEqual(bot.velocity, (0, 0, 0))
        run.step({mei: (0, 0, 16)})
        self.assertFalse(match.combat.walls.pieces)
        self.assertFalse(sniper_reposition.request(match, bot, player))
        end = run.frame + math.ceil(sniper_reposition.COOLDOWN_MS / (1000 * combat.TICK))
        match.update(end * combat.TICK, end, frames=False)
        run.frame = end
        player.mover.position[:] = (0, 1, bot.position[2] - 2)
        player.mover.body = None
        self.assertTrue(sniper_reposition.request(match, bot, player))
        for _ in range(40):
            run.step({})
        self.assertGreater(bot.position[2], pillar.feet[2] + pillar.depth)
        self.assertFalse(state.active)

    def test_sniper_move_rejects_allies_range_and_occlusion(self):
        for reason in ('ally', 'range', 'wall'):
            with self.subTest(reason=reason):
                _, match, player, bot = self.sniper_move_fixture()
                if reason == 'ally':
                    bot.team = player.team
                elif reason == 'range':
                    bot.position = (0, 1, 6)
                else:
                    match.collision_world = World(box(-20, 20, -1, 1, -20, 20)
                                                  + wall(-8, 1, 8, 1, -2, 8))
                self.assertFalse(sniper_reposition.request(match, bot, player))
                self.assertNotIn(bot.entity, match.combat.sniper_repositions)

    def test_actual_flashbang_aborts_sniper_move_without_late_travel(self):
        run, match, player, bot = self.sniper_move_fixture(control_caster=True)
        caster = run.players[1]
        caster.mover.position[:] = (1.2, 1, 5.5)
        caster.mover.body = None
        self.assertTrue(sniper_reposition.request(match, bot, player))
        yaw, pitch = aim(match.combat._eye(caster), (0, 2, 4))
        run.step({caster: (yaw, pitch, 16)})
        for _ in range(30):
            if match.combat.is_stunned(bot):
                break
            run.step({})
        self.assertTrue(match.combat.is_stunned(bot))
        self.assertFalse(sniper_reposition.move_state(bot).active)
        self.assertNotIn(bot.entity, match.combat.sniper_repositions)
        stopped = bot.position
        for _ in range(30):
            run.step({})
        self.assertEqual(bot.position, stopped)

    def test_allied_hack_interrupts_sniper_move_on_application(self):
        run, match, player, bot = self.sniper_move_fixture(control_caster=True)
        fight = match.combat
        caster = run.players[1]
        self.assertTrue(match.switch_hero(caster, heroes()[hacks.SOMBRA]))
        caster.mover.position[:] = (0, 1, -2)
        caster.mover.body = None
        yaw, pitch = aim(fight._eye(caster), (0, 2.4, 2))
        for _ in range(35):
            run.step({caster: (yaw, pitch, 2)})
        self.assertFalse(fight.hacks.active(bot.entity))
        start = bot.position
        health = fight.vitals[bot.entity].total
        self.assertTrue(sniper_reposition.request(match, bot, player))
        move = sniper_reposition.move_state(bot)
        for _ in range(12):
            self.assertTrue(move.active)
            run.step({caster: (yaw, pitch, 2)})
            if fight.hacks.active(bot.entity):
                break
        self.assertTrue(fight.hacks.active(bot.entity))
        [(application, _)] = fight.hacks.effects[bot.entity]
        self.assertEqual(application.graph.index, hacks.APPLICATION)
        self.assertEqual(application.source_entity(), caster.body)
        self.assertFalse(fight.hacks.active(player.body))
        self.assertEqual(fight.vitals[bot.entity].total, health)
        self.assertFalse(move.active)
        self.assertNotIn(bot.entity, fight.sniper_repositions)
        self.assertEqual(bot.velocity, (0.0, 0.0, 0.0))
        stopped = bot.position
        self.assertGreater(stopped[2], start[2])
        run.step({})
        self.assertEqual(bot.position, stopped)

    def test_allied_emp_interrupts_sniper_move_and_allows_recast(self):
        run, match, player, bot = self.sniper_move_fixture(control_caster=True)
        fight = match.combat
        caster = run.players[1]
        self.assertTrue(match.switch_hero(caster, heroes()[hacks.SOMBRA]))
        caster.mover.position[:] = (2, 1, 4)
        caster.mover.body = None
        script = caster.body_script
        charge = script.component.entity_var(338).value()
        script.set_ult_charge(charge)
        run.step({caster: (0, 0, 32)})
        self.assertLess(script.component.entity_var(342).value(), charge)
        for _ in range(15):
            run.step({})
        self.assertFalse(fight.hacks.active(bot.entity))
        health = fight.vitals[bot.entity].total
        self.assertTrue(sniper_reposition.request(match, bot, player))
        move = sniper_reposition.move_state(bot)
        for _ in range(30):
            self.assertTrue(move.active)
            run.step({})
            if fight.hacks.active(bot.entity):
                break
        self.assertTrue(fight.hacks.active(bot.entity))
        self.assertFalse(fight.hacks.active(player.body))
        self.assertEqual(fight.vitals[bot.entity].total, health)
        self.assertFalse(move.active)
        self.assertNotIn(bot.entity, fight.sniper_repositions)
        self.assertEqual(bot.velocity, (0.0, 0.0, 0.0))
        stopped = bot.position
        player.mover.position[:] = (stopped[0], stopped[1], stopped[2] - 2)
        player.mover.body = None
        self.assertFalse(sniper_reposition.request(match, bot, player))

        [(_, hack_end)] = fight.hacks.effects[bot.entity]
        frame_ms = move.instance.component.frame_ms
        cooldown_end = math.ceil((move.ended_at + sniper_reposition.COOLDOWN_MS) / frame_ms)
        ready = max(hack_end, cooldown_end)
        match.update((ready - 1) * combat.TICK, ready - 1, frames=False)
        self.assertEqual(bot.position, stopped)
        self.assertFalse(sniper_reposition.request(match, bot, player))
        match.update(ready * combat.TICK, ready, frames=False)
        self.assertFalse(fight.hacks.active(bot.entity))
        self.assertTrue(sniper_reposition.request(match, bot, player))
        self.assertEqual(move.counter, 2)

    def test_sniper_move_uses_capsule_collision_after_admission(self):
        run, match, player, bot = self.sniper_move_fixture()
        match.collision_world = World(box(-20, 20, -1, 1, -20, 20)
                                      + box(-8, 8, 1, 2.4, 4, 4.2))
        self.assertTrue(sniper_reposition.request(match, bot, player))
        state = sniper_reposition.move_state(bot)
        end = run.frame + 45
        match.update(end * combat.TICK, end, frames=False)
        self.assertFalse(state.active)
        self.assertGreater(bot.position[2], 2)
        self.assertLess(bot.position[2], 4)
        self.assertNotIn(bot.entity, match.combat.sniper_repositions)

    def test_actual_tesla_kills_repositioning_sniper_and_retires_move(self):
        run, match, player, bot = self.sniper_move_fixture()
        self.assertTrue(sniper_reposition.request(match, bot, player))
        match.combat.vitals[bot.entity].now[combat.HEALTH_POOL] = 0.5
        for _ in range(8):
            run.step({player: (0, 0, 1)})
            if bot.dead:
                break
        self.assertTrue(bot.dead)
        self.assertNotIn(bot.entity, match.combat.sniper_repositions)
        stopped = bot.position
        for _ in range(25):
            run.step({})
        self.assertEqual(bot.position, stopped)

    def test_actual_jump_pack_takes_over_repositioning_sniper_motion(self):
        run, match, player, bot = self.sniper_move_fixture()
        self.assertTrue(sniper_reposition.request(match, bot, player))
        before = match.combat.vitals[bot.entity].total
        run.step({player: (0, 0, 8)})
        self.assertEqual(match.combat.vitals[bot.entity].total, before - 1)
        self.assertFalse(sniper_reposition.move_state(bot).active)
        self.assertNotIn(bot.entity, match.combat.sniper_repositions)
        self.assertIn(bot.entity, match.combat.impulses.npcs)
        self.assertFalse(sniper_reposition.request(match, bot, player))
        for _ in range(5):
            run.step({})
        self.assertGreater(bot.position[2], 2)
        self.assertGreater(bot.position[1], 1)

    def sniper_fixture(self):
        definition = next(d for d in self.definitions.values() if d.name == 'Sniper')
        return self.create(definition)

    def select_sniper_weapon(self, run, bot, button, slot):
        for pressed in (True, False):
            run.step({})
            bot.body_script.advance_live(run.frame, bot.position, ability_input=(button, pressed))
        self.assertEqual(bot.body_script.component.entity_var(31).value(), slot)

    def fire_sniper_once(self, run, match, bot, graph_id, projectile, *, settle=48):
        for _ in range(180):
            run.step({})
            shots = self.emit_primary(run, match, bot)
            if shots:
                break
        else:
            self.fail("selected Sniper graph did not report an authored volley")
        self.assertEqual([state.instance.graph.index for state, *_ in shots], [graph_id])
        rocket = match.combat.rockets[-1]
        self.assertEqual(rocket.definition, projectile)
        if graph_id == 0x1134:
            self.assertEqual(rocket.speed, 200)
            self.assertEqual(rocket.left, 100 / 200)
        for _ in range(settle):
            run.step({})
            self.assertEqual(self.emit_primary(run, match, bot, False), [])

    def test_actual_sniper_weapon_manager_selects_each_distinct_volley(self):
        run, match, player, bot = self.sniper_fixture()
        self.assertEqual(bot.body_script.component.entity_var(31).value(), 1)
        self.select_sniper_weapon(run, bot, 16, 2)
        self.select_sniper_weapon(run, bot, 17, 2)
        self.fire_sniper_once(run, match, bot, 0x1134, 0x04000000000023BB)
        self.assertEqual(match.combat.vitals[player.body].total, 497.5)
        self.select_sniper_weapon(run, bot, 15, 1)
        self.fire_sniper_once(run, match, bot, 0x1122, 0x0400000000001E80)
        self.assertEqual(match.combat.vitals[player.body].total, 402.5)

    def test_sniper_alternate_uses_nano_at_projectile_impact(self):
        definition = next(d for d in self.definitions.values() if d.name == 'Sniper')
        for expires_in_flight in (False, True):
            with self.subTest(expires_in_flight=expires_in_flight):
                run, match, player, bot = self.create(definition, control_caster=True)
                bot.position = (0, 1, 40)
                self.select_sniper_weapon(run, bot, 16, 2)
                vitals = self.nano_winston(run, match, player)
                end = vitals.nano_until
                run.frame = end - (12 if expires_in_flight else 80)
                match.update(run.frame * combat.TICK, run.frame, frames=False)
                fight = match.combat
                for _ in range(11):
                    run.step({})
                    shots = self.emit_primary(run, match, bot)
                    if shots:
                        break
                self.assertEqual([state.instance.graph.index for state, *_ in shots], [0x1134])
                self.assertLess(run.frame, end)
                self.assertEqual(fight.rockets[-1].definition, 0x04000000000023BB)
                self.assertEqual(vitals.total, 500)
                for _ in range(40):
                    run.step({})
                    self.assertFalse(self.emit_primary(run, match, bot, False))
                    if vitals.total < 500:
                        break
                self.assertEqual(run.frame >= end, expires_in_flight)
                self.assertEqual(vitals.now[combat.ARMOUR_POOL], 147.5 if expires_in_flight else 148.75)
                self.assertEqual(vitals.now[combat.HEALTH_POOL], 350)
                self.assertEqual(player.body_script.component.entity_var(342).value(), 0)

    def test_both_sniper_volleys_fire_beyond_the_unbound_ammo_value(self):
        for slot, graph_id in ((1, 0x1122), (2, 0x1134)):
            with self.subTest(slot=slot):
                run, match, player, bot = self.sniper_fixture()
                bot.yaw = 0
                if slot == 2:
                    self.select_sniper_weapon(run, bot, 16, 2)
                weapon = next(i for i in bot.body_script.component.instances.values()
                              if i.graph.index == graph_id)
                self.assertIsNone(weapon.graph.node(6).field('m_24E351A4'))
                count = 0
                for _ in range(3000):
                    run.step({})
                    shots = self.emit_primary(run, match, bot)
                    self.assertTrue(all(state.instance is weapon for state, *_ in shots))
                    count += len(shots)
                    if count >= 21:
                        break
                self.assertEqual(count, 21)
                self.assertEqual(weapon.vars[53].value(), 20)
                self.assertEqual(match.combat.vitals[player.body].total, 500)

    def test_winston_deployed_barrier_consumes_actual_sniper_alternate_projectile(self):
        run, match, player, bot = self.sniper_fixture()
        bot.position = (0, 1, 10)
        run.step({player: (0, 0, 16)})
        for _ in range(4):
            run.step({})
        shield = match.combat.barriers.actors[player.body]
        self.select_sniper_weapon(run, bot, 16, 2)
        self.fire_sniper_once(run, match, bot, 0x1134, 0x04000000000023BB)
        self.assertEqual(shield.health, 695)
        self.assertEqual(match.combat.vitals[player.body].total, 500)

    def test_sniper_alternate_respects_team_occlusion_and_projectile_range(self):
        for reason in ('ally', 'wall', 'range'):
            with self.subTest(reason=reason):
                run, match, player, bot = self.sniper_fixture()
                if reason == 'ally':
                    bot.team = player.team
                elif reason == 'wall':
                    match.collision_world = World(box(-30, 30, -1, 1, -30, 30)
                                                  + wall(-6, 2, 6, 2, -2, 8))
                else:
                    bot.position = (0, 1, 105)
                self.select_sniper_weapon(run, bot, 16, 2)
                self.fire_sniper_once(run, match, bot, 0x1134, 0x04000000000023BB)
                self.assertEqual(match.combat.vitals[player.body].total, 500)

    def test_assassin_original_targeted_attack_damages_winston(self):
        self.assertAlmostEqual(self.assassin_damage, 130.72)


if __name__ == '__main__':
    unittest.main()
