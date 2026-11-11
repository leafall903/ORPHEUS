"""Declared associated scripts: local permissive ownership, not native AI parity."""
import math
import os
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import test_pharah_pve as pve
from test_game_mover import FLOOR, box, wall

from ow174.game import combat, defenses, heavy_charge, npc, npc_control, sigma_grasp, sniper_reposition, zarya_barrier
from ow174.game.body_vm_snapshot import export_component_vm, snapshot_digest
from ow174.game.collision import World
from ow174.game.commands import FIRE, Command
from ow174.game.content import heroes
from ow174.game.script import ai_behavior, entity_queries, expr, graph, message_receive, message_receiver, message_state
from ow174.game.script.health_queries import pool_sum


class NpcAssociatedScriptsTests(unittest.TestCase):
    encounter = pve.PharahPveTests.encounter
    health = pve.PharahPveTests.health
    aim_at_body = staticmethod(pve.PharahPveTests.aim_at_body)
    create = pve.PharahPveTests.create

    @classmethod
    def setUpClass(cls):
        bundle = Path(os.environ.get('ORPHEUS_ASSOCIATED_GRAPH_BUNDLE', ''))
        if not bundle.is_file():
            raise unittest.SkipTest('Supply actual associated NPC graph closure explicitly')
        pve.PharahPveTests.setUpClass()
        cls.addClassCleanup(pve.PharahPveTests.tearDownClass)
        cls.catalog = pve.PharahPveTests.catalog
        mount = patch.object(graph, 'GRAPHS_PATH', bundle)
        mount.start()
        cls.addClassCleanup(mount.stop)
        pve.PharahPveTests.clear_graphs()
        cls.addClassCleanup(pve.PharahPveTests.clear_graphs)

    def enforcer(self):
        return self.create(next(row for row in self.catalog.values() if row.name == 'Enforcer'))

    def test_factory_uses_actual_roots_identity_and_peer_health(self):
        run, _, bot = self.enforcer()
        script = bot.body_script
        body, associated = script.component, script.associated_component
        self.assertIsNotNone(associated)
        self.assertNotEqual(body.entity, associated.entity)
        self.assertEqual(script.associated_definition, 0x040000000000227A)
        self.assertEqual([i.graph.index for i in associated.instances.values()][:5],
                         [0x142A, 0x0E5C, 0x0C83, 0x13E9, 0x13EA])
        owner = {'$': 'STU_B5A0CAF0'}
        instance = next(iter(body.instances.values()))
        self.assertEqual(instance.evaluate({'$': 'STU_F7262B36', 'm_801B298F': owner}),
                         expr.Entity(associated.entity))
        ai = next(iter(associated.instances.values()))
        possessed = {'$': 'STU_0AC15F78', 'm_entity': owner}
        self.assertEqual(ai.evaluate(possessed), expr.Entity(body.entity))
        self.assertEqual(ai.evaluate({'$': 'STU_9B7648C3', 'm_entity': possessed}),
                         self.health(run, bot).total)
        run.step({})
        self.assertEqual(associated.frame, body.frame)

    def test_autonomous_npc_primary_acquires_fires_and_releases_through_target_graph(self):
        definitions = {row.name: row for row in self.catalog.values()}
        for name, index, first_hit in (("Trooper", 0x11E3, 20), ("Enforcer", 0x13E9, 50),
                                       ("Sniper", 0x1138, 100), ("Heavy Assault", 0x1251, 5)):
            with self.subTest(name=name):
                run, pharah, spare = self.encounter()
                match = run.match
                match.game_map = replace(match.game_map, free_for_all=False)
                pharah.client = pve.NpcClient(pharah)
                spare.client = pve.NpcClient(spare)
                pharah.team, spare.team = 0, 1
                y = pharah.mover.position[1]
                spare.mover.position = [30, y, 0]
                spare.mover.body = None
                bot = npc.spawn(match, definitions[name], (0, y, 8), team=1, autonomous=True)
                controller = bot.body_script.associated_component
                publication = next(i for i in controller.instances.values() if i.graph.index == index)
                health = self.health(run, pharah)
                clear = match.collision_world
                covered = World(FLOOR + wall(-8, 4, 8, 4))
                match.collision_world = covered
                for _ in range(35):
                    run.step({})
                    self.assertFalse(bot.attack_input)
                self.assertEqual(health.total, 200)

                match.collision_world = clear
                for _ in range(170):
                    run.step({})
                    if health.total < 200:
                        break
                self.assertEqual(bot.attack_target, pharah.entity)
                self.assertEqual(publication.find_var(0, 536).value(), expr.Entity(pharah.body))
                self.assertEqual(controller.entity_var(1650).value(), expr.Entity(pharah.body))
                self.assertIn(npc_control.button(bot), bot.body_script.component.held)
                self.assertEqual(health.total, 200 - first_hit)

                match.collision_world = covered
                for _ in range(6):
                    run.step({})
                self.assertFalse(bot.attack_input)
                self.assertIsNone(bot.attack_target)
                self.assertEqual(expr.to_entity(publication.find_var(0, 536).value()), 0)
                self.assertNotIn(npc_control.button(bot), bot.body_script.component.held)
                health_after_cover = 200 - first_hit
                self.assertEqual(health.total, health_after_cover)

                pharah.team = bot.team
                match.collision_world = clear
                for _ in range(35):
                    run.step({})
                    self.assertFalse(bot.attack_input)
                self.assertEqual(health.total, health_after_cover)

                pharah.team = 0
                for _ in range(8):
                    run.step({})
                    if bot.attack_input:
                        break
                self.assertTrue(bot.attack_input)
                self.assertEqual(publication.find_var(0, 536).value(), expr.Entity(pharah.body))
                for _ in range(175):
                    run.step({})
                    if health.total < health_after_cover:
                        break
                self.assertLess(health.total, health_after_cover)
                self.assertIsNone(controller.fault)

    def test_trooper_controller_reads_current_body_position_after_pharah_rocket(self):
        row = next(row for row in self.catalog.values() if row.name == "Trooper")
        run, pharah, trooper = self.create(row)
        controller = trooper.body_script.associated_component
        behavior = next(item for item in controller.instances.values() if item.graph.index == 0x102F)
        position = behavior.graph.nodes[132].field("m_value")
        self.assertEqual(position["$"], "STU_71A87E92")
        self.assertEqual(position["m_entity"]["$"], "STU_0AC15F78")
        before = behavior.evaluate(position)
        self.assertEqual(before, expr.Vec3(*trooper.position))

        pve.PharahPveTests.fire_once(self, run, pharah)
        self.assertEqual(self.health(run, trooper).total, 80)
        after = behavior.evaluate(position)
        self.assertEqual(after, expr.Vec3(*trooper.position))
        self.assertGreater(after.z, before.z)

    def test_trooper_target_message_and_linked_slot_use_separate_sources(self):
        row = next(row for row in self.catalog.values() if row.name == "Trooper")
        run, pharah, trooper = self.create(row)
        controller = trooper.body_script.associated_component
        publication = next(item for item in controller.instances.values() if item.graph.index == 0x11E3)
        behavior = next(item for item in controller.instances.values() if item.graph.index == 0x102F)
        slot = publication.graph.node(32).field("m_condition")["m_configVars"][0]
        behavior_slot = behavior.graph.node(1).field("m_AAAC951E")[1]
        candidate = expr.Entity(pharah.body)
        records, scores = {}, {}
        seen = []
        scalar_lookups = []

        def lookup(owner, selector):
            seen.append((owner, selector))
            return records.get((owner, selector))

        def score(owner, target):
            scalar_lookups.append((owner, target))
            return scores.get((owner, target))

        owned = entity_queries.Target(
            controller.entity, entity_queries.Possession(trooper.entity, body_reference=trooper.entity),
        )
        query = entity_queries.Context(
            controller.entity, True, True, True,
            lookup=lambda entity: owned if entity == controller.entity else None,
            watch_environment=None, watch_possession=lambda *_: None,
            watch_body_reference=lambda *_: None, lookup_linked=lookup,
        )
        packet = message_receive.Packet(0x0240000000000079, trooper.entity, trooper.entity, (
            (0x0A40000000000057, 0), (0x0A40000000000059, candidate),
        ))

        with patch.object(controller.world, "entity_query_context", return_value=query), patch.object(
            controller.world, "target_record_scalar", side_effect=score,
        ):
            self.assertEqual(message_receive.receive(publication, packet), 1)
            run.step({})
            self.assertEqual(publication.find_var(0, 536).value(), candidate)
            self.assertEqual(controller.entity_var(1650).value(), candidate)
            self.assertEqual(publication.evaluate(slot), expr.Entity(0))
            self.assertEqual(behavior.evaluate(behavior_slot), expr.Entity(0))
            records[(controller.entity, 0)] = pharah.body
            self.assertEqual(publication.evaluate(slot), candidate)
            self.assertEqual(behavior.evaluate(behavior_slot), candidate)
            records.clear()
            self.assertEqual(publication.evaluate(slot), expr.Entity(0))
            self.assertEqual(controller.entity_var(1650).value(), candidate)

            gate = publication.node_state(publication.graph.node(20))
            with controller.operation():
                gate.begin()
            self.assertTrue(gate.active)
            self.assertFalse(gate.current)
            cfg = gate.node.field("m_condition")
            self.assertEqual(publication.evaluate(cfg, initial=42), 42)
            scores[(controller.entity, pharah.body)] = 0.75
            run.step({})
            self.assertTrue(gate.current)
            self.assertEqual(publication.evaluate(cfg), 0.75)
            scores[(controller.entity, pharah.body)] = 0.0
            run.step({})
            self.assertFalse(gate.current)
            self.assertEqual(publication.evaluate(cfg, initial=42), 0.0)
            scores.clear()
            self.assertEqual(publication.evaluate(cfg, initial=42), 42)
            self.assertEqual(set(scalar_lookups), {(controller.entity, pharah.body)})
        self.assertEqual(set(seen), {(controller.entity, 0)})
        self.assertIsNone(controller.fault)

    def test_trooper_body_projects_ordered_message_targets(self):
        row = next(row for row in self.catalog.values() if row.name == "Trooper")
        run, pharah, trooper = self.create(row)
        body = trooper.body_script.component
        instance = next(item for item in body.instances.values() if item.graph.index == 0x1034)
        candidate = expr.Entity(pharah.body)
        other = expr.Entity(trooper.body_script.associated_component.entity)
        for _ in range(32):
            run.step({})
        projection = instance.node_state(instance.graph.node(47))
        self.assertTrue(projection.active)
        self.assertIsNone(projection.node.field("m_filter"))

        # Seed the two upstream lists while running the real body graph.
        instance.write((1, 11186), (candidate,))
        instance.write((0, 11192), (other, candidate))
        run.step({})
        self.assertEqual(instance.find_var(0, 14090).value(), (candidate, other))
        self.assertTrue(instance.node_state(instance.graph.node(48)).active)
        instance.write((1, 11184), 3)
        run.step({})
        self.assertEqual(instance.find_var(0, 14090).value(), (candidate, other, candidate))
        instance.write((1, 11186), ())
        instance.write((0, 11192), ())
        run.step({})
        self.assertEqual(instance.find_var(0, 14090).value(), ())
        self.assertIsNone(body.fault)

    def test_trooper_body_persistent_message_updates_other_trooper(self):
        row = next(row for row in self.catalog.values() if row.name == "Trooper")
        run, _, sender = self.create(row)
        receiver = npc.spawn(run.match, row, (2, *sender.position[1:]), team=sender.team)
        sender_body = sender.body_script.component
        receiver_body = receiver.body_script.component
        sender_graph = next(item for item in sender_body.instances.values() if item.graph.index == 0x1034)
        receiver_graph = next(item for item in receiver_body.instances.values() if item.graph.index == 0x1034)
        host = message_state.Host(notify=message_receiver.notify)
        sender_policy = message_state.Policy(False, False, False)
        receiver_policy = message_receiver.Policy(
            False, False, False, False, False, False, True, False, False, False,
        )

        def context(body):
            return message_state.Context(
                body, host, policy=lambda _: sender_policy, frame=lambda _: body.frame,
                priority=lambda *_: None, receiver_policy=lambda _: receiver_policy,
                receiver_generation=lambda _: body.frame,
                permissive_nodes=lambda state: state.instance.graph.index == 0x1034 and state.node.pos in (17, 48),
            )

        source = context(sender_body)
        destination = context(receiver_body)
        controller = receiver.body_script.associated_component
        controller_graph = next(item for item in controller.instances.values() if item.graph.index == 0x102F)
        reference = controller_graph.graph.node(87).field("m_7E122730")
        selection = ai_behavior.Context(controller)
        record = entity_queries.Possession(receiver_body.entity, body_reference=receiver_body.entity)
        targets = {
            controller.entity: entity_queries.Target(controller.entity, record),
            receiver_body.entity: entity_queries.Target(receiver_body.entity, None),
        }
        query = entity_queries.Context(
            controller.entity, True, True, True, lookup=targets.get,
            watch_environment=None, watch_possession=lambda *_: None, watch_body_reference=lambda *_: None,
        )
        with patch.object(sender_body.world, "message_state_context", return_value=source), patch.object(
            receiver_body.world, "message_state_context", return_value=destination,
        ), patch.object(controller.world, "ai_behavior_context", return_value=selection), patch.object(
            controller.world, "entity_query_context", return_value=query,
        ):
            for _ in range(32):
                run.step({})
            sending = sender_graph.node_state(sender_graph.graph.node(48))
            receiving = receiver_graph.node_state(receiver_graph.graph.node(17))
            self.assertIs(sending.message_context, source)
            self.assertIs(receiving.message_context, destination)
            self.assertIsNone(receiver_body.entity_var(11187).value())
            controller_graph.states[20].timer(2)

            sender_graph.write((1, 11186), (expr.Entity(receiver_body.entity),))
            for _ in range(2):
                run.step({})
            self.assertEqual([(key.entity, key.message) for key in sending.message_keys],
                             [(receiver_body.entity, 0x861)])
            self.assertIs(receiving.message_selected, sending)
            self.assertEqual(receiver_body.entity_var(11187).value(), expr.Entity(sender_body.entity))
            self.assertEqual(controller_graph.evaluate(reference), expr.Entity(sender_body.entity))

            sender_graph.write((1, 11186), ())
            for _ in range(2):
                run.step({})
            self.assertFalse(sending.message_keys)
            self.assertIsNone(receiving.message_selected)
            self.assertIsNone(receiver_body.entity_var(11187).value())
            self.assertTrue(receiver_graph.node_state(receiver_graph.graph.node(16)).active)
            self.assertIsNone(controller_graph.evaluate(reference))

            sender_graph.write((1, 11186), (expr.Entity(receiver_body.entity),))
            for _ in range(2):
                run.step({})
            self.assertIs(receiving.message_selected, sending)
            sender_body.destroy(sender_graph)
            for _ in range(2):
                run.step({})
            self.assertFalse(sending.message_keys)
            self.assertIsNone(receiving.message_selected)
            self.assertIsNone(controller_graph.evaluate(reference))
            self.assertIsNone(sender_body.fault)
            self.assertIsNone(receiver_body.fault)
            host.retire()

    def test_trooper_selected_branch_reads_component44_body_reference(self):
        row = next(row for row in self.catalog.values() if row.name == "Trooper")
        run, pharah, trooper = self.create(row)
        body = trooper.body_script.component
        controller = trooper.body_script.associated_component
        root = next(instance for instance in controller.instances.values()
                    if instance.graph.index == 0x102F)
        fields = root.graph.state(36).fields
        self.assertEqual(fields["m_7E122730"]["m_identifier"], 11187)
        world = controller.world
        original_native = world.native
        selection = ai_behavior.Context(controller)
        record = entity_queries.Possession(body.entity, body_reference=0)
        references = []
        # Supply the body-side message result as fixture data. The controller
        # still has to resolve it through the query and the authored Stack.
        body.entity_var(11187).base = expr.Entity(pharah.body)
        targets = {
            controller.entity: entity_queries.Target(controller.entity, record),
            body.entity: entity_queries.Target(body.entity, None),
            pharah.body: entity_queries.Target(pharah.body, None),
        }
        query = entity_queries.Context(
            controller.entity, True, True, True, lookup=targets.get,
            watch_environment=None, watch_possession=lambda *_: None,
            watch_body_reference=lambda watcher, target: references.append((watcher, target)),
        )

        def native(cfg, instance):
            if cfg.get("$") == "STU_A37A910D":
                return expr.Entity(pharah.body)
            return original_native(cfg, instance)

        with patch.object(world, "native", native), patch.object(
            world, "ai_behavior_context", return_value=selection,
        ), patch.object(world, "entity_query_context", return_value=query):
            root.states[20].timer(2)
            run.step({})
            self.assertIsNone(root.evaluate(fields["m_7E122730"]))
            self.assertEqual(root.evaluate(fields["m_C698BFCC"]), 5)
            self.assertTrue(root.states[36].active)
            self.assertTrue(any(watcher.node.pos == 55 and target is targets[controller.entity]
                                for watcher, target in references))
            record.body_reference = body.entity
            run.step({})
            self.assertEqual(root.evaluate(fields["m_7E122730"]), expr.Entity(pharah.body))
            record.body_reference = 0
            run.step({})
            self.assertIsNone(root.evaluate(fields["m_7E122730"]))
            root.states[20].timer(1)
            run.step({})
            self.assertFalse(root.states[36].active)
        self.assertIsNone(controller.fault)

    def test_trooper_selected_branch_reads_target_from_its_live_body(self):
        row = next(row for row in self.catalog.values() if row.name == "Trooper")
        run, pharah, trooper = self.create(row)
        body = trooper.body_script.component
        controller = trooper.body_script.associated_component
        behavior = next(i for i in controller.instances.values() if i.graph.index == 0x102F)
        body_reference = behavior.graph.node(55).field("m_value")["m_configVars"][0]
        self.assertEqual(body_reference["$"], "STU_00707E1E")
        self.assertEqual(behavior.evaluate(body_reference), expr.Entity(body.entity))

        target = behavior.graph.state(36).field("m_7E122730")
        body_target = body.entity_var(11187)
        selection = ai_behavior.Context(controller)
        with patch.object(controller.world, "ai_behavior_context", return_value=selection):
            behavior.states[20].timer(2)
            run.step({})
            self.assertIsNone(behavior.evaluate(target))
            body_target.base = expr.Entity(pharah.body)
            run.step({})
            self.assertEqual(behavior.evaluate(target), expr.Entity(pharah.body))
            body_target.base = None
            run.step({})
            self.assertIsNone(behavior.evaluate(target))
            behavior.states[20].timer(1)
        self.assertIsNone(controller.fault)

    def test_enforcer_authored_walk_moves_and_trades_real_hits_with_pharah(self):
        run, pharah, bot = self.enforcer()
        fight = run.match.combat
        vitals = self.health(run, pharah)
        enemy = self.health(run, bot)
        self.assertEqual(npc_control.start(run.match, pharah), bot.entity)
        self.assertEqual(npc_control.enforcer_walk(run.match, pharah), bot.entity)
        controller = bot.body_script.associated_component
        self.assertEqual(controller.entity_var(12299).value(), 1)
        start = bot.position[2]
        speeds = []
        for _ in range(100):
            run.step({})
            speeds.append(abs(bot.velocity[2]))
            if vitals.total < 200:
                break
        self.assertLess(bot.position[2], start - .5)
        self.assertAlmostEqual(max(speeds), bot.speed * .4, delta=.08)
        self.assertEqual(vitals.total, 150)
        before = enemy.total
        direction = self.aim_at_body(run, pharah, bot)
        run.step({pharah: (*direction, FIRE)})
        for _ in range(40):
            run.step({})
            if enemy.total < before:
                break
        self.assertLess(enemy.total, before)
        self.assertEqual(npc_control.stop(run.match), 1)
        for _ in range(120):
            run.step({})
        self.assertFalse(controller.entity_var(12299).value())
        self.assertFalse(bot.attack_input)
        self.assertEqual(bot.velocity, (0, 0, 0))
        self.assertFalse(fight.rockets)

    def test_autonomous_enforcer_walks_fires_stops_for_cover_and_resumes(self):
        definition = next(row for row in self.catalog.values() if row.name == "Enforcer")
        run, pharah, spare = self.encounter()
        match = run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        pharah.team, spare.team = 0, 1
        pharah.client = pve.NpcClient(pharah)
        spare.client = pve.NpcClient(spare)
        y = pharah.mover.position[1]
        spare.mover.position = [30, y, 0]
        spare.mover.body = None
        bot = npc.spawn(match, definition, (0, y, 12), team=1, autonomous=True)
        controller = bot.body_script.associated_component
        speeds = []
        for _ in range(100):
            run.step({})
            speeds.append(abs(bot.velocity[2]))
            if fight.rockets:
                break
        self.assertEqual(sum(rocket.shooter is bot for rocket in fight.rockets), 25)
        self.assertLess(bot.position[2], 11)
        self.assertAlmostEqual(max(speeds), bot.speed * .4, delta=.08)
        self.assertEqual(controller.entity_var(12299).value(), 1)

        clear = match.collision_world
        cover = bot.position[2] - 2
        match.collision_world = World(FLOOR + wall(-8, cover, 8, cover))
        for _ in range(30):
            run.step({})
        stopped = bot.position
        self.assertGreater(stopped[2], cover + 1)
        self.assertAlmostEqual(bot.velocity[2], 0, delta=.02)
        self.assertFalse(controller.entity_var(12299).value())
        self.assertFalse(bot.attack_input)
        self.assertEqual(self.health(run, pharah).total, 200)

        match.collision_world = clear
        for _ in range(125):
            run.step({})
            if self.health(run, pharah).total < 200:
                break
        self.assertLess(bot.position[2], stopped[2] - 1)
        self.assertLess(self.health(run, pharah).total, 200)
        self.assertTrue(bot.attack_input)
        self.assertEqual(controller.entity_var(12299).value(), 1)

        health = self.health(run, bot)
        health.damage(health.total - 1)
        direction = self.aim_at_body(run, pharah, bot)
        run.step({pharah: (*direction, FIRE)})
        for _ in range(60):
            run.step({})
            if health.dead:
                break
        self.assertTrue(health.dead)
        self.assertFalse(bot.attack_input)
        self.assertFalse(controller.entity_var(12299).value())
        stopped = bot.position
        for _ in range(10):
            run.step({})
        self.assertEqual(bot.position, stopped)

    def test_autonomous_weapon_npcs_detour_on_rialto_and_trade_hits_with_pharah(self):
        cache = Path(os.environ.get('ORPHEUS_RIALTO_COLLISION', ''))
        if not cache.is_file():
            self.skipTest('Supply the extracted build 104319 Rialto collision cache')
        world = World.load(cache, frozenset({0x0D80000000002F4A}))
        start = (-143, world.ground(-143, -118, 0, -4).y, -118)
        target = (-139, world.ground(-139, -112, 0, -4).y, -112)
        definitions = {row.name: row for row in self.catalog.values()}
        for name, first_hit in (("Trooper", 20), ("Enforcer", 30), ("Sniper", 100), ("Heavy Assault", 5)):
            with self.subTest(name=name):
                run, pharah, spare = self.encounter()
                match = run.match
                fight = match.combat
                match.game_map = replace(match.game_map, free_for_all=False)
                pharah.team, spare.team = 0, 1
                pharah.client = pve.NpcClient(pharah)
                spare.client = pve.NpcClient(spare)
                match.collision_world = world
                pharah.mover.use(world=world)
                pharah.mover.position = list(target)
                pharah.mover.body = None
                bot = npc.spawn(match, definitions[name], start, team=1, autonomous=True)
                self.assertIsNone(npc_control._visible_aim(fight, bot, pharah, npc_control.AIM_RANGE))
                health = self.health(run, pharah)
                seen = False
                for _ in range(380):
                    run.step({})
                    seen |= npc_control._visible_aim(fight, bot, pharah, npc_control.AIM_RANGE) is not None
                    if not seen:
                        self.assertFalse(bot.attack_input)
                        self.assertEqual(health.total, 200)
                    if health.total < 200:
                        break
                self.assertGreater(math.dist(start, bot.position), .5)
                self.assertEqual(health.total, 200 - first_hit)
                self.assertEqual(bot.body_script.associated_component.entity_var(1650).value(),
                                 expr.Entity(pharah.body))

                npc_control.stop(match)
                self.assertIsNone(bot.combat_detour)
                bot_health = self.health(run, bot)
                before = bot_health.total
                run.step({pharah: (*self.aim_at_body(run, pharah, bot), FIRE)})
                for _ in range(50):
                    run.step({})
                    if bot_health.total < before:
                        break
                self.assertEqual(bot_health.total, max(0, before - 120))

    def test_autonomous_enforcer_detours_around_static_cover_and_fires_at_pharah(self):
        definition = next(row for row in self.catalog.values() if row.name == "Enforcer")
        run, pharah, spare = self.encounter()
        match = run.match
        match.game_map = replace(match.game_map, free_for_all=False)
        pharah.team, spare.team = 0, 1
        pharah.client = pve.NpcClient(pharah)
        y = pharah.mover.position[1]
        spare.mover.position = [30, y, 0]
        spare.mover.body = None
        match.collision_world = World(FLOOR + wall(-2, 4, 2, 4))
        bot = npc.spawn(match, definition, (0, y, 8), team=1, autonomous=True)
        health = self.health(run, pharah)
        path = [bot.position]
        for _ in range(420):
            run.step({})
            path.append(bot.position)
            if npc_control._visible_aim(match.combat, bot, pharah, npc_control.AIM_RANGE) is None:
                self.assertFalse(bot.attack_input)
                self.assertEqual(health.total, 200)
            if health.total < 200:
                break

        self.assertGreater(max(abs(position[0]) for position in path), 2)
        crossings = [point for before, point in zip(path, path[1:])
                     if before[2] >= 4 > point[2]]
        self.assertTrue(all(abs(point[0]) > 2 for point in crossings))
        self.assertTrue(bot.attack_input, (path[::30], path[-1], health.total))
        self.assertEqual(bot.body_script.associated_component.entity_var(1650).value(), expr.Entity(pharah.body))
        self.assertLess(health.total, 200)

        npc_control.stop(match)
        stopped_health = health.total
        for _ in range(30):
            run.step({})
        self.assertIsNone(bot.combat_detour)
        self.assertFalse(bot.attack_input)
        self.assertEqual(health.total, stopped_health)

    def test_enforcer_walk_and_airborne_fan_stop_at_cover_then_resume(self):
        run, pharah, bot = self.enforcer()
        match = run.match
        fight = match.combat
        original = match.collision_world
        self.assertEqual(npc_control.start(match, pharah), bot.entity)
        self.assertEqual(npc_control.enforcer_walk(match, pharah), bot.entity)
        for _ in range(100):
            run.step({})
            if fight.rockets:
                break
        self.assertEqual(sum(rocket.shooter is bot for rocket in fight.rockets), 25)
        cover = 5.0
        self.assertGreater(bot.position[2], cover)
        match.collision_world = World(FLOOR + wall(-8, cover, 8, cover))
        vitals = self.health(run, pharah)
        for _ in range(65):
            run.step({})
            self.assertGreater(bot.position[2], cover)
            self.assertEqual(vitals.total, 200)
        stopped = bot.position[2]
        self.assertLess(stopped, cover + 1)
        self.assertAlmostEqual(bot.velocity[2], 0, delta=.02)
        self.assertFalse(bot.attack_input)
        self.assertFalse(any(rocket.shooter is bot for rocket in fight.rockets))
        match.collision_world = original
        for _ in range(120):
            run.step({})
            if vitals.total < 200:
                break
        self.assertLess(bot.position[2], stopped - .5)
        self.assertLess(vitals.total, 200)
        self.assertTrue(bot.attack_input)

    def test_lethal_pharah_rocket_releases_enforcer_walk_and_fire(self):
        run, pharah, bot = self.enforcer()
        match = run.match
        self.assertEqual(npc_control.start(match, pharah), bot.entity)
        self.assertEqual(npc_control.enforcer_walk(match, pharah), bot.entity)
        controller = bot.body_script.associated_component
        for _ in range(10):
            run.step({})
        self.assertLess(bot.velocity[2], 0)
        health = self.health(run, bot)
        health.damage(health.total - 1)
        direction = self.aim_at_body(run, pharah, bot)
        run.step({pharah: (*direction, FIRE)})
        for _ in range(40):
            if health.dead:
                break
            run.step({})
        self.assertTrue(health.dead)
        self.assertEqual(health.killer, pharah.body)
        self.assertFalse(controller.entity_var(12299).value())
        self.assertFalse(bot.attack_input)
        position = bot.position
        for _ in range(70):
            run.step({})
        self.assertEqual(bot.position, position)
        self.assertEqual(self.health(run, pharah).total, 200)
        self.assertFalse(any(rocket.shooter is bot for rocket in match.combat.rockets))

    def test_enforcer_authored_fan_hits_nearby_pharah_and_obeys_partial_cover(self):
        definition = next(row for row in self.catalog.values() if row.name == 'Enforcer')
        for distance, partial_cover, expected in ((2, False, 150), (8, False, 162),
                                                  (12, False, 176), (5, True, None)):
            with self.subTest(distance=distance, partial_cover=partial_cover):
                run, pharah, bot = self.create(definition, distance=distance)
                fight = run.match.combat
                self.assertEqual(npc_control.start(run.match, pharah), bot.entity)
                for _ in range(90):
                    run.step({})
                    if fight.rockets:
                        break
                rays = [rocket for rocket in fight.rockets if rocket.shooter is bot]
                self.assertEqual(len(rays), 25)
                self.assertEqual(len({rocket.direction for rocket in rays}), 25)
                self.assertEqual(self.health(run, pharah).total, 200)
                if partial_cover:
                    run.match.collision_world = World(FLOOR + wall(.05, 2.5, 3, 2.5))
                for _ in range(40):
                    run.step({})
                health = self.health(run, pharah).total
                if partial_cover:
                    self.assertGreater(health, 150)
                    self.assertLess(health, 200)
                else:
                    self.assertEqual(health, expected)

    def test_reverse_jump_jet_reduces_enforcer_fan_hits_with_more_travel_time(self):
        definition = next(row for row in self.catalog.values() if row.name == "Enforcer")
        for distance, grounded_loss, airborne_loss in ((5, 50, 50), (8, 38, 8), (12, 24, 0)):
            for jump in (False, True):
                with self.subTest(distance=distance, jump=jump):
                    run, pharah, bot = self.create(definition, distance=distance)
                    fight = run.match.combat
                    vitals = self.health(run, pharah)
                    ground = pharah.mover.position[1]
                    self.assertEqual(npc_control.start(run.match, pharah), bot.entity)
                    for _ in range(120):
                        if fight.rockets:
                            break
                        run.step({})
                    fan = [rocket for rocket in fight.rockets if rocket.shooter is bot]
                    self.assertEqual(len(fan), 25)
                    self.assertEqual({rocket.speed for rocket in fan}, {80})
                    self.assertEqual(vitals.total, 200)
                    run.step({pharah: (-32768, 0, 8 if jump else 0)})
                    if jump:
                        self.assertEqual(pharah.mover.velocity[1], 20)
                    for _ in range(32):
                        if not any(rocket in fight.rockets for rocket in fan):
                            break
                        run.step({pharah: (-32768, 0, 0)})

                    remaining = [rocket for rocket in fight.rockets if rocket.shooter is bot]
                    self.assertTrue(all(rocket in fan for rocket in remaining))
                    self.assertTrue(all(rocket.position[2] < pharah.mover.position[2] - .5
                                        for rocket in remaining))
                    self.assertEqual(vitals.total, 200 - (airborne_loss if jump else grounded_loss))
                    if jump:
                        self.assertGreater(pharah.mover.position[1], ground)
                    else:
                        self.assertEqual(pharah.mover.position[1], ground)

    def test_enforcer_fan_in_flight_hits_during_barrage(self):
        definition = next(row for row in self.catalog.values() if row.name == 'Enforcer')
        for initial_health in (200, 20):
            with self.subTest(initial_health=initial_health):
                run, pharah, bot = self.create(definition, distance=8)
                fight = run.match.combat
                script = pharah.body_script
                script.set_ult_charge(script.component.entity_var(338).value())
                vitals = self.health(run, pharah)
                if initial_health < vitals.total:
                    vitals.damage(vitals.total - initial_health)
                self.assertEqual(npc_control.start(run.match, pharah), bot.entity)
                for _ in range(90):
                    run.step({})
                    if fight.rockets:
                        break
                rays = [rocket for rocket in fight.rockets if rocket.shooter is bot]
                self.assertEqual(len(rays), 25)
                self.assertEqual(vitals.total, initial_health)

                run.step({pharah: (-32768, 0, 32)})
                self.assertTrue(script.ultimate_running())
                volley = next(i for i in script.component.instances.values() if i.graph.index == 0x2B).states[1]
                for _ in range(30):
                    run.step({pharah: (-32768, 0, 0)})
                    if vitals.total == (162 if initial_health == 200 else 0):
                        break
                count = volley.reported
                if initial_health == 20:
                    self.assertTrue(vitals.dead)
                    self.assertEqual(vitals.killer, bot.entity)
                    self.assertFalse(script.ultimate_running())
                    for _ in range(12):
                        run.step({})
                    self.assertEqual(volley.reported, count)
                    self.assertFalse(bot.attack_input)
                else:
                    self.assertEqual(vitals.total, 162)
                    self.assertTrue(script.ultimate_running())
                    for _ in range(12):
                        run.step({pharah: (-32768, 0, 0)})
                    self.assertGreater(volley.reported, count)

    def test_actual_enforcer_fans_kill_full_health_pharah_and_release_input(self):
        definition = next(row for row in self.catalog.values() if row.name == "Enforcer")
        run, pharah, bot = self.create(definition, distance=2)
        vitals = self.health(run, pharah)
        self.assertEqual(vitals.total, 200)
        self.assertEqual(npc_control.start(run.match, pharah), bot.entity)
        for _ in range(600):
            if vitals.dead:
                break
            run.step({})
        self.assertTrue(vitals.dead)
        self.assertEqual(vitals.total, 0)
        self.assertEqual(vitals.killer, bot.entity)
        for _ in range(150):
            run.step({})
        self.assertEqual(vitals.total, 0)
        self.assertFalse(bot.attack_input)
        self.assertNotIn(20, bot.body_script.component.held)
        self.assertFalse(any(rocket.shooter is bot for rocket in run.match.combat.rockets))

    def test_actual_enforcer_input_damages_and_stop_has_no_late_hits(self):
        run, player, bot = self.enforcer()
        self.assertTrue(bot.body_script.associated_component.entity_var(9134).value())
        self.assertEqual(npc_control.start(run.match, player), bot.entity)
        for _ in range(68):
            run.step({})
        self.assertEqual(self.health(run, player).total, 200)
        run.step({})
        self.assertEqual(self.health(run, player).total, 176)
        self.assertEqual(npc_control.stop(run.match), 1)
        for _ in range(160):
            run.step({})
        self.assertEqual(self.health(run, player).total, 176)

    def test_all_five_return_attacks_resume_after_actual_ice_wall_and_pharah_can_kill(self):
        first_damage = {"Trooper": 20, "Enforcer": 24, "Sniper": 100, "Assassin": 25, "Heavy Assault": 5}
        for definition in self.catalog.values():
            name = definition.name
            with self.subTest(enemy=name):
                run, player, bot = self.create(definition, distance=3.8 if name == "Assassin" else 8)
                fight = run.match.combat
                mei = next(other for other in run.players if other is not player)
                self.assertTrue(run.match.switch_hero(mei, heroes()[0x02E00000000000DD]))
                mei.mover.position = [0, player.mover.position[1], -1.0]
                mei.mover.body = None
                facing = (0, 6500)
                run.step({mei: (*facing, 16)})
                run.step({mei: (*facing, 0)})
                player.mover.yaw, player.mover.pitch = self.aim_at_body(run, player, bot)
                self.assertEqual(npc_control.start(run.match, player), bot.entity)
                run.step({mei: (*facing, 1)})
                self.assertTrue(bot.attack_input)
                self.assertEqual(len(fight.walls.pieces), 5)
                if name == "Assassin":
                    x, y, _ = bot.position
                    bot.position = (x, y, 1.8)
                player.mover.yaw, player.mover.pitch = self.aim_at_body(run, player, bot)
                self.assertIsNone(npc_control.selected(run.match, player))
                vitals = self.health(run, player)
                for _ in range(150):
                    run.step({})
                    self.assertFalse(bot.attack_input)
                self.assertEqual(vitals.total, 200)
                run.step({mei: (*facing, 16)})
                self.assertFalse(fight.walls.pieces)
                mei.mover.position[0] = 30
                mei.mover.body = None
                for _ in range(150):
                    run.step({})
                    if vitals.total < 200:
                        break
                self.assertEqual(vitals.total, 200 - first_damage[name])
                self.assertTrue(bot.attack_input)
                if name == "Assassin":
                    self.assertTrue(fight.is_pinned(player))
                    self.assertEqual(npc_control.stop(run.match), 1)
                    run.step({})
                    self.assertFalse(fight.is_pinned(player))
                enemy = self.health(run, bot)
                enemy.damage(enemy.total - 100)
                aim = self.aim_at_body(run, player, bot)
                run.step({player: (*aim, FIRE)})
                for _ in range(30):
                    run.step({})
                    if enemy.dead:
                        break
                self.assertTrue(enemy.dead)
                self.assertFalse(bot.attack_input)
                after = vitals.total
                for _ in range(150):
                    run.step({})
                self.assertEqual(vitals.total, after)
                self.assertIn(bot, run.match.bots)

    def test_trooper_and_heavy_actual_melee_hit_pharah_once_per_press(self):
        for name, damage in (("Trooper", 30), ("Heavy Assault", 80)):
            with self.subTest(enemy=name):
                definition = next(row for row in self.catalog.values() if row.name == name)
                run, pharah, bot = self.create(definition, distance=2)
                start = pharah.mover.position[2]
                self.assertEqual(npc_control.start(run.match, pharah, melee=True), bot.entity)
                for _ in range(62):
                    run.step({})
                self.assertEqual(self.health(run, pharah).total, 200 - damage)
                if name == "Heavy Assault":
                    self.assertLess(pharah.mover.position[2], start)
                else:
                    self.assertEqual(pharah.mover.position[2], start)
                self.assertFalse(run.match.combat.rockets)
                self.assertEqual(npc_control.stop(run.match), 1)
                for _ in range(30):
                    run.step({})
                self.assertEqual(self.health(run, pharah).total, 200 - damage)
                self.assertFalse(bot.attack_input)

    def test_trooper_switches_between_actual_primary_and_melee_inputs(self):
        definition = next(row for row in self.catalog.values() if row.name == 'Trooper')
        run, pharah, bot = self.create(definition, distance=2)
        match = run.match
        vitals = self.health(run, pharah)
        component = bot.body_script.component
        primary = component.world.ai_weapon_button(0)
        for melee, expected in ((False, 180), (True, 150), (False, 130)):
            with self.subTest(melee=melee, expected=expected):
                pharah.mover.yaw, pharah.mover.pitch = self.aim_at_body(run, pharah, bot)
                self.assertEqual(npc_control.start(match, pharah, melee=melee), bot.entity)
                for _ in range(120):
                    run.step({})
                    self.assertNotIn(primary if melee else 31, component.held)
                    if vitals.total <= expected:
                        break
                self.assertEqual(vitals.total, expected)
                self.assertIn(31 if melee else primary, component.held)

    def test_autonomous_trooper_switches_to_melee_then_returns_to_primary(self):
        definition = next(row for row in self.catalog.values() if row.name == "Trooper")
        run, pharah, spare = self.encounter()
        match = run.match
        match.game_map = replace(match.game_map, free_for_all=False)
        pharah.team, spare.team = 0, 1
        pharah.client = pve.NpcClient(pharah)
        spare.client = pve.NpcClient(spare)
        y = pharah.mover.position[1]
        bot = npc.spawn(match, definition, (0, y, 8), team=1, autonomous=True)
        component = bot.body_script.component
        primary = component.world.ai_weapon_button(0)
        health = self.health(run, pharah)
        for _ in range(80):
            run.step({})
            if health.total < 200:
                break
        self.assertEqual(health.total, 180)
        self.assertIn(primary, component.held)

        pharah.mover.position = [0, y, 6]
        pharah.mover.body = None
        clear = match.collision_world
        match.collision_world = World(FLOOR + wall(-8, 7, 8, 7))
        for _ in range(25):
            run.step({})
            self.assertFalse(bot.attack_input)
        self.assertEqual(health.total, 180)
        match.collision_world = clear
        for _ in range(65):
            run.step({})
            self.assertNotIn(primary, component.held)
            if health.total < 180:
                break
        self.assertTrue(bot.melee_input)
        self.assertEqual(health.total, 150)
        first_melee = run.frame
        for _ in range(12):
            run.step({})
            self.assertEqual(health.total, 150)
        for _ in range(90):
            run.step({})
            if health.total < 150:
                break
        self.assertEqual(health.total, 120)
        self.assertGreaterEqual((run.frame - first_melee) * 16, 1000)

        pharah.mover.position = [0, y, 0]
        pharah.mover.body = None
        for _ in range(80):
            run.step({})
            self.assertNotIn(31, component.held)
            if health.total < 120:
                break
        self.assertEqual(health.total, 100)
        self.assertFalse(bot.melee_input)
        self.assertIn(primary, component.held)

    def heavy_charge_encounter(self, *, frontal=False):
        definition = next(row for row in self.catalog.values() if row.name == 'Heavy Assault')
        run, pharah, bot = self.create(definition, distance=9)
        self.assertEqual(npc_control.start(run.match, pharah, charge=True), bot.entity)
        if frontal:
            pharah.mover.position[:] = (0, bot.position[1], 7)
            pharah.mover.body = None
        instance = heavy_charge.charge(bot)
        self.assertIsNotNone(instance)
        for _ in range(80):
            run.step({})
            if heavy_charge.travelling(instance):
                return run, pharah, bot, instance
        self.fail("Heavy Assault charge never left windup")

    def test_autonomous_heavy_charges_barrage_and_releases_when_target_becomes_ally(self):
        definition = next(row for row in self.catalog.values() if row.name == "Heavy Assault")
        run, pharah, spare = self.encounter()
        match = run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        pharah.team, spare.team = 0, 1
        pharah.client = pve.NpcClient(pharah)
        spare.client = pve.NpcClient(spare)
        y = pharah.mover.position[1]
        spare.mover.position = [30, y, 0]
        spare.mover.body = None
        script = pharah.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        run.step({pharah: (-16384, 0, 32)})
        self.assertTrue(script.ultimate_running())
        bot = npc.spawn(match, definition, (0, y, 9), team=1, autonomous=True)
        instance = heavy_charge.charge(bot)
        for _ in range(90):
            run.step({pharah: (-16384, 0, 0)})
            if fight.is_pinned(pharah):
                break
        self.assertTrue(fight.is_pinned(pharah))
        self.assertTrue(heavy_charge.travelling(instance))
        self.assertIn(heavy_charge.BUTTON, bot.body_script.component.held)
        self.assertFalse(script.ultimate_running())
        self.assertEqual(self.health(run, pharah).total, 200)

        # The current target changes team during the live carry.
        pharah.team = bot.team
        spare.team = 0
        for _ in range(10):
            run.step({})
        self.assertFalse(fight.is_pinned(pharah))
        self.assertEqual(heavy_charge.phase(instance), 0)
        self.assertNotIn(heavy_charge.BUTTON, bot.body_script.component.held)
        self.assertEqual(self.health(run, pharah).total, 200)
        ability = instance.states[instance.graph.node(heavy_charge.ABILITY).state]
        self.assertGreater(ability.remaining(), 0)

        spare.team = bot.team
        pharah.team = 0
        script.set_ult_charge(script.component.entity_var(338).value())
        run.step({pharah: (-16384, 0, 32)})
        self.assertTrue(script.ultimate_running())
        for _ in range(90):
            run.step({pharah: (-16384, 0, 0)})
            if self.health(run, pharah).total < 200:
                break
        self.assertEqual(self.health(run, pharah).total, 195)
        self.assertIn(npc_control.button(bot), bot.body_script.component.held)
        self.assertFalse(bot.charge_input)
        self.assertTrue(script.ultimate_running())

    def test_heavy_charge_wall(self):
        run, pharah, bot, instance = self.heavy_charge_encounter()
        start = bot.position[2]
        run.step({})
        self.assertAlmostEqual(bot.position[2], start - 0.24)
        run.match.collision_world = World(FLOOR + wall(-8, 2, 8, 2))
        for _ in range(45):
            run.step({})
        self.assertEqual(heavy_charge.phase(instance), 0)
        self.assertTrue(2 < bot.position[2] < 4)
        self.assertEqual(bot.velocity, (0, 0, 0))
        self.assertEqual(self.health(run, pharah).total, 200)

    def test_heavy_charge_stops_at_actual_ice_wall(self):
        run, pharah, bot, instance = self.heavy_charge_encounter()
        fight = run.match.combat
        mei = next(other for other in run.players if other is not pharah)
        self.assertTrue(run.match.switch_hero(mei, heroes()[0x02E00000000000DD]))
        mei.mover.position[:] = (0, pharah.mover.position[1], -1)
        mei.mover.body = None
        facing = (0, 6500)
        run.step({mei: (*facing, 16)})
        run.step({mei: (*facing, 0)})
        run.step({mei: (*facing, 1)})
        self.assertEqual(len(fight.walls.pieces), 5)
        for _ in range(50):
            run.step({})
            if not heavy_charge.travelling(instance):
                break
        self.assertEqual(heavy_charge.phase(instance), 0)
        self.assertEqual(bot.velocity, (0, 0, 0))
        self.assertFalse(fight.is_pinned(pharah))
        self.assertEqual(self.health(run, pharah).total, 200)
        self.assertIsNotNone(fight.walls.cast(
            (bot.position[0], bot.position[1] + 1, bot.position[2]), (0, 0, -1), 2))

    def test_heavy_charge_rejects_actual_pharah_concussion_impulse(self):
        run, pharah, bot, instance = self.heavy_charge_encounter()
        fight = run.match.combat
        health = self.health(run, bot).total
        start = bot.position
        direction = self.aim_at_body(run, pharah, bot)
        with patch.object(fight, '_blast_impulse', wraps=fight._blast_impulse) as impulse:
            run.step({pharah: (*direction, 16)})
            for _ in range(28):
                if any(call.args[0].owner is bot for call in impulse.call_args_list):
                    break
                run.step({})
            self.assertTrue(any(call.args[0].owner is bot for call in impulse.call_args_list))
        self.assertTrue(heavy_charge.travelling(instance))
        self.assertAlmostEqual(bot.position[0], start[0])
        self.assertAlmostEqual(bot.position[1], start[1])
        self.assertLess(bot.position[2], start[2])
        self.assertIsNone(bot.impulse_mover)
        self.assertEqual(self.health(run, bot).total, health)

    def test_heavy_charge_manual_release(self):
        run, pharah, bot, instance = self.heavy_charge_encounter(frontal=True)
        fight = run.match.combat
        self.assertIsNotNone(graph.graph(heavy_charge.VICTIM_GRAPH))
        for _ in range(45):
            run.step({})
            if fight.is_pinned(pharah):
                break
        self.assertTrue(fight.is_pinned(pharah))
        self.assertIs(fight.heavy_pins[bot.entity][0], pharah)
        self.assertEqual(self.health(run, pharah).total, 200)
        self.assertEqual(npc_control.stop(run.match), 1)
        run.step({})
        self.assertFalse(fight.is_pinned(pharah))
        self.assertEqual(heavy_charge.phase(instance), 0)
        self.assertEqual(self.health(run, pharah).total, 200)

    def test_reverse_jump_jet_brackets_heavy_charge_capture_during_ascent(self):
        for z, captured in ((5.4, True), (5.2, False)):
            with self.subTest(z=z):
                run, pharah, bot, instance = self.heavy_charge_encounter()
                fight = run.match.combat
                vitals = self.health(run, pharah)
                ground = bot.position[1]
                pharah.mover.position[:] = (0, ground, z)
                pharah.mover.body = None
                run.step({pharah: (-32768, 0, 8)})
                self.assertEqual(pharah.mover.velocity[1], 20)
                peak = ground
                for _ in range(70):
                    run.step({pharah: (-32768, 0, 0)})
                    peak = max(peak, pharah.mover.position[1])
                    self.assertEqual(vitals.total, 200)
                    if fight.is_pinned(pharah) or bot.position[2] < pharah.mover.position[2] - .5:
                        break

                self.assertEqual(fight.is_pinned(pharah), captured)
                self.assertTrue(heavy_charge.travelling(instance))
                if captured:
                    self.assertGreater(peak, ground + .3)
                    self.assertIs(fight.heavy_pins[bot.entity][0], pharah)
                    hover = next(i for i in pharah.body_script.component.instances.values()
                                 if i.graph.index == 0x25)
                    fuel = hover.vars[87].value()
                    for _ in range(8):
                        run.step({pharah: (-32768, 0, 2)})
                    self.assertTrue(fight.is_pinned(pharah))
                    self.assertEqual(hover.vars[87].value(), fuel)
                else:
                    self.assertGreater(pharah.mover.position[1], ground + 7)
                    self.assertLess(bot.position[2], pharah.mover.position[2] - .5)
                    self.assertFalse(fight.is_stunned(pharah))

    def test_ana_sleep_dart_stops_heavy_charge_and_releases_pharah(self):
        run, pharah, bot, instance = self.heavy_charge_encounter(frontal=True)
        fight = run.match.combat
        for _ in range(12):
            run.step({})
            if fight.is_pinned(pharah):
                break
        self.assertTrue(fight.is_pinned(pharah))
        _, app = fight.heavy_pins[bot.entity]
        ana = next(other for other in run.players if other is not pharah)
        self.assertTrue(run.match.switch_hero(ana, heroes()[0x02E000000000013B]))
        ana.mover.position[:] = (0, bot.position[1], bot.position[2] + 1.5)
        ana.mover.body = None
        yaw, _ = self.aim_at_body(run, ana, bot)
        run.step({ana: (yaw, 0, 8)})
        for _ in range(35):
            if fight.is_asleep(bot):
                break
            run.step({ana: (yaw, 0, 0)})
        self.assertTrue(fight.is_asleep(bot))
        run.step({})
        self.assertEqual(heavy_charge.phase(instance), 0)
        self.assertFalse(fight.is_pinned(pharah))
        self.assertNotIn(app.id, app.component.instances)
        self.assertFalse(bot.attack_input)
        position = bot.position
        for _ in range(15):
            run.step({})
        self.assertEqual(bot.position, position)
        self.assertEqual(self.health(run, pharah).total, 200)

    def test_heavy_charge_carries_pharah_into_lethal_wall_crush(self):
        run, pharah, bot, instance = self.heavy_charge_encounter()
        fight = run.match.combat
        pharah.mover.position[:] = (0, bot.position[1], 7)
        pharah.mover.body = None
        run.match.collision_world = World(FLOOR + wall(-8, 3, 8, 3))
        for _ in range(12):
            run.step({})
            if fight.is_pinned(pharah):
                break
        self.assertTrue(fight.is_pinned(pharah))
        before = pharah.mover.position[2]
        for _ in range(40):
            run.step({})
            if fight.is_dead(pharah):
                break
        self.assertTrue(fight.is_dead(pharah))
        self.assertLess(pharah.mover.position[2], before)
        self.assertEqual(self.health(run, pharah).killer, bot.entity)
        self.assertFalse(fight.heavy_pins)
        self.assertEqual(heavy_charge.phase(instance), 0)

    def test_heavy_charge_crushes_carried_pharah_on_second_body_impact(self):
        run, pharah, bot, instance = self.heavy_charge_encounter(frontal=True)
        fight = run.match.combat
        for _ in range(12):
            run.step({})
            if fight.is_pinned(pharah):
                break
        self.assertTrue(fight.is_pinned(pharah))
        _, app = fight.heavy_pins[bot.entity]
        other = next(player for player in run.match.players if player is not pharah)
        other.team = pharah.team
        other.mover.position[:] = (0.4, bot.position[1], bot.position[2] - 0.3)
        other.mover.body = None
        run.step({})
        victim = self.health(run, pharah)
        self.assertTrue(victim.dead)
        self.assertEqual(victim.killer, bot.entity)
        self.assertEqual(self.health(run, other).total, 25)
        self.assertEqual(fight.stuns[other.body] - run.frame, 125)
        self.assertFalse(fight.heavy_pins)
        self.assertNotIn(app.id, app.component.instances)
        self.assertEqual(heavy_charge.phase(instance), 0)

    def test_heavy_charge_ignores_ally_and_removal_releases_pharah(self):
        run, pharah, bot, instance = self.heavy_charge_encounter(frontal=True)
        fight = run.match.combat
        for _ in range(12):
            run.step({})
            if fight.is_pinned(pharah):
                break
        self.assertTrue(fight.is_pinned(pharah))
        _, app = fight.heavy_pins[bot.entity]
        ally = next(player for player in run.match.players if player is not pharah)
        ally.team = bot.team
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        pharah.team = 1 - bot.team
        ally.mover.position[:] = (0.4, bot.position[1], bot.position[2] - 0.3)
        ally.mover.body = None
        run.step({})
        self.assertTrue(heavy_charge.travelling(instance))
        self.assertTrue(fight.is_pinned(pharah))
        self.assertEqual(self.health(run, ally).total, 200)
        self.assertFalse(fight.is_stunned(ally))
        self.assertEqual(npc.remove_spawned(run.match), (bot.entity,))
        self.assertFalse(fight.is_pinned(pharah))
        self.assertNotIn(app.id, app.component.instances)
        for _ in range(20):
            run.step({})
        self.assertEqual(self.health(run, pharah).total, 200)

    def test_heavy_charge_grazes_pharah_once_and_pushes_her(self):
        run, pharah, bot, instance = self.heavy_charge_encounter()
        pharah.mover.position[:] = (2, bot.position[1], 7)
        pharah.mover.body = None
        health = self.health(run, pharah)
        for _ in range(25):
            run.step({})
            if health.total < 200:
                break
        self.assertEqual(health.total, 150)
        self.assertEqual(run.match.combat.heavy_grazes[bot.entity], {pharah.body})
        for _ in range(10):
            run.step({})
        self.assertEqual(health.total, 150)
        self.assertGreater(pharah.mover.position[0], 2)
        npc_control.stop(run.match)
        self.assertNotIn(bot.entity, run.match.combat.heavy_grazes)
        self.assertEqual(heavy_charge.phase(instance), 0)

    def test_heavy_charge_direct_impact_stuns_pharah_for_two_seconds(self):
        run, pharah, bot, instance = self.heavy_charge_encounter()
        pharah.mover.position[:] = (0, bot.position[1], 8.3)
        pharah.mover.body = None
        health = self.health(run, pharah)
        for _ in range(15):
            run.step({})
            if health.total < 200:
                break
        fight = run.match.combat
        self.assertEqual(health.total, 25)
        self.assertEqual(fight.stuns[pharah.body] - run.frame, 125)
        self.assertFalse(fight.is_pinned(pharah))
        self.assertEqual(heavy_charge.phase(instance), 0)
        for _ in range(124):
            run.step({})
        self.assertTrue(fight.is_stunned(pharah))
        run.step({})
        self.assertFalse(fight.is_stunned(pharah))
        self.assertEqual(health.total, 25)

    def test_projected_barrier_screens_pharah_from_heavy_charge(self):
        definition = next(row for row in self.catalog.values() if row.name == "Heavy Assault")
        for contact, damage, x, z in (("graze", 50, 2, 7), ("impact", 175, 0, 8.3), ("pin", 175, 0, 7)):
            for protected in (False, True):
                with self.subTest(contact=contact, protected=protected):
                    run, pharah, bot = self.create(definition, distance=9)
                    match = run.match
                    fight = match.combat
                    zarya = run.players[1]
                    self.assertTrue(match.switch_hero(zarya, heroes()[zarya_barrier.HERO]))
                    match.game_map = replace(match.game_map, free_for_all=False)
                    pharah.team = zarya.team = 0
                    y = pharah.mover.position[1]
                    zarya.mover.position = [4.5, y, 0]
                    zarya.mover.body = None
                    for _ in range(30):
                        run.step({})
                    self.assertEqual(npc_control.start(match, pharah, charge=True), bot.entity)
                    script = pharah.body_script
                    script.set_ult_charge(script.component.entity_var(338).value())
                    run.step({pharah: (-16384, 0, 32), zarya: (-16384, 1000, 16 if protected else 0)})
                    self.assertTrue(script.ultimate_running())
                    instance = heavy_charge.charge(bot)
                    for _ in range(80):
                        if heavy_charge.travelling(instance):
                            break
                        run.step({pharah: (-16384, 0, 0)})
                    self.assertTrue(heavy_charge.travelling(instance))
                    barrier = fight.zarya_bubbles.get((zarya.body, zarya_barrier.PROJECTED))
                    if protected:
                        self.assertIs(barrier.target, pharah)
                        self.assertEqual(barrier.health, 200)
                    else:
                        self.assertIsNone(barrier)
                    pharah.mover.position = [x, y, z]
                    pharah.mover.body = None
                    health = self.health(run, pharah)
                    for _ in range(30):
                        run.step({pharah: (-16384, 0, 0)})
                        if (fight.is_pinned(pharah) or health.total < 200
                                or (barrier is not None and barrier.health < 200)):
                            break
                    pinned = contact == "pin" and not protected
                    self.assertEqual(fight.is_pinned(pharah), pinned)
                    self.assertEqual(health.total, 200 if protected or pinned else 200 - damage)
                    self.assertEqual(fight.is_stunned(pharah), contact == "impact" and not protected)
                    self.assertEqual(script.ultimate_running(), contact == "graze" or protected)
                    if protected:
                        self.assertEqual(barrier.health, 200 - damage)
                        for actual, expected in zip(pharah.mover.position, (x, y, z), strict=True):
                            self.assertAlmostEqual(actual, expected, delta=1 / 1024)
                        self.assertEqual(pharah.mover.velocity, (0.0, 0.0, 0.0))
                        self.assertAlmostEqual(zarya.body_script.component.entity_var(87).value(),
                                               damage * .2, places=4)

    def test_fortified_orisa_stops_heavy_charge_before_it_reaches_pharah(self):
        definition = next(row for row in self.catalog.values() if row.name == "Heavy Assault")
        for fortify in (False, True):
            with self.subTest(fortify=fortify):
                run, pharah, bot = self.create(definition, distance=9)
                match = run.match
                fight = match.combat
                orisa = run.players[1]
                self.assertTrue(match.switch_hero(orisa, heroes()[0x02E000000000013E]))
                match.game_map = replace(match.game_map, free_for_all=False)
                pharah.team = orisa.team = 0
                y = pharah.mover.position[1]
                orisa.mover.position = [5, y, 0]
                orisa.mover.body = None
                for _ in range(30):
                    run.step({})
                self.assertEqual(npc_control.start(match, pharah, charge=True), bot.entity)
                run.step({orisa: (0, 0, 8 if fortify else 0)})
                instance = heavy_charge.charge(bot)
                for _ in range(80):
                    if heavy_charge.travelling(instance):
                        break
                    run.step({})
                self.assertTrue(heavy_charge.travelling(instance))
                self.assertEqual(defenses.fortified(orisa), fortify)
                orisa.mover.position = [0, y, 7]
                orisa.mover.body = None
                health = self.health(run, orisa)
                before = health.total
                for _ in range(30):
                    run.step({})
                    if fight.is_pinned(orisa) or health.total < before:
                        break
                self.assertEqual(fight.is_pinned(orisa), not fortify)
                self.assertFalse(fight.is_stunned(orisa))
                self.assertEqual(self.health(run, pharah).total, 200)
                if fortify:
                    self.assertAlmostEqual(before - health.total, 100, places=4)
                    self.assertEqual(heavy_charge.phase(instance), 0)
                    stopped_at = bot.position
                    for _ in range(16):
                        run.step({})
                    self.assertEqual(bot.position, stopped_at)
                    self.assertEqual(self.health(run, pharah).total, 200)
                else:
                    self.assertEqual(health.total, before)

    def test_lethal_pharah_rocket_stops_active_heavy_charge(self):
        run, pharah, bot, instance = self.heavy_charge_encounter()
        fight = run.match.combat
        enemy = self.health(run, bot)
        enemy.damage(enemy.total - 1)
        start = bot.position[2]
        direction = self.aim_at_body(run, pharah, bot)
        run.step({pharah: (*direction, FIRE)})
        for _ in range(30):
            run.step({})
            if enemy.dead:
                break
        self.assertTrue(enemy.dead)
        self.assertEqual(enemy.killer, pharah.body)
        self.assertLess(bot.position[2], start)
        self.assertFalse(fight.is_pinned(pharah))
        self.assertFalse(heavy_charge.travelling(instance))
        position = bot.position
        for _ in range(30):
            run.step({})
        self.assertEqual(bot.position, position)
        self.assertEqual(self.health(run, pharah).total, 200)
        self.assertFalse(bot.attack_input)

    def test_trooper_and_heavy_nonlethal_melee_allow_barrage_to_continue(self):
        for name, damage in (('Trooper', 30), ('Heavy Assault', 80)):
            with self.subTest(npc=name):
                definition = next(row for row in self.catalog.values() if row.name == name)
                run, pharah, bot = self.create(definition, distance=2)
                script = pharah.body_script
                script.set_ult_charge(script.component.entity_var(338).value())
                vitals = self.health(run, pharah)
                self.assertEqual(npc_control.start(run.match, pharah, melee=True), bot.entity)
                run.step({pharah: (-32768, 0, 32)})
                self.assertTrue(script.ultimate_running())
                for _ in range(80):
                    run.step({pharah: (-32768, 0, 0)})
                    if vitals.total < 200:
                        break
                self.assertEqual(vitals.total, 200 - damage)
                self.assertTrue(script.ultimate_running())
                volley = next(i for i in script.component.instances.values() if i.graph.index == 0x2B).states[1]
                count = volley.reported
                for _ in range(10):
                    run.step({pharah: (-32768, 0, 0)})
                self.assertGreater(volley.reported, count)
                self.assertEqual(vitals.total, 200 - damage)

    def test_trooper_and_heavy_melee_kill_barraging_pharah_and_release_input(self):
        for name, initial_health in (('Trooper', 25), ('Heavy Assault', 75)):
            with self.subTest(npc=name):
                definition = next(row for row in self.catalog.values() if row.name == name)
                run, pharah, bot = self.create(definition, distance=2)
                script = pharah.body_script
                script.set_ult_charge(script.component.entity_var(338).value())
                vitals = self.health(run, pharah)
                vitals.damage(vitals.total - initial_health)
                self.assertEqual(npc_control.start(run.match, pharah, melee=True), bot.entity)
                run.step({pharah: (-32768, 0, 32)})
                self.assertTrue(script.ultimate_running())
                for _ in range(80):
                    run.step({pharah: (-32768, 0, 0)})
                    if vitals.dead:
                        break
                self.assertTrue(vitals.dead)
                self.assertEqual(vitals.killer, bot.entity)
                self.assertFalse(script.ultimate_running())
                volley = next(i for i in script.component.instances.values() if i.graph.index == 0x2B).states[1]
                count = volley.reported
                for _ in range(30):
                    run.step({})
                self.assertFalse(bot.attack_input)
                self.assertNotIn(npc_control.button(bot), bot.body_script.component.held)
                self.assertEqual(volley.reported, count)

    def test_trooper_and_heavy_actual_melee_reject_range_and_wall(self):
        for name in ("Trooper", "Heavy Assault"):
            for obstructed in (False, True):
                with self.subTest(enemy=name, obstructed=obstructed):
                    definition = next(row for row in self.catalog.values() if row.name == name)
                    run, pharah, bot = self.create(definition, distance=2 if obstructed else 8)
                    fight = run.match.combat
                    y = pharah.mover.position[1]
                    wall = World(box(-4, 4, y, y + 5, .9, 1.1))
                    self.assertEqual(npc_control.start(run.match, pharah, melee=True), bot.entity)
                    with patch.object(fight, "_collision", return_value=wall if obstructed else fight._collision()):
                        for _ in range(70):
                            run.step({})
                    self.assertEqual(self.health(run, pharah).total, 200)
                    self.assertFalse(fight.rockets)

    def test_enforcer_auto_reload_restores_fire_after_four_actual_rounds(self):
        run, pharah, bot = self.enforcer()
        match = run.match
        fight = match.combat
        weapon = next(i for i in bot.body_script.component.instances.values() if i.graph.index == 0x13EB)
        self.assertEqual(weapon.vars[53].value(), 4)
        self.assertEqual(npc_control.start(match, pharah), bot.entity)
        with patch.object(fight, "_shoot", wraps=fight._shoot) as shot:
            for _ in range(700):
                run.step({})
                if weapon.vars[53].value() == 0 and weapon.vars[51].value():
                    break
            self.assertEqual(weapon.vars[53].value(), 0)
            self.assertTrue(weapon.vars[51].value())
            self.assertEqual(shot.call_count, 4)
            self.assertEqual(npc_control.stop(match), 1)
            for _ in range(150):
                run.step({})
                if not weapon.vars[51].value():
                    break
            self.assertFalse(weapon.vars[51].value())
            self.assertEqual(weapon.vars[53].value(), 4)
            self.assertEqual(shot.call_count, 4)
            self.assertFalse(bot.attack_input)
        vitals = self.health(run, pharah)
        before = vitals.total
        pharah.mover.yaw, pharah.mover.pitch = self.aim_at_body(run, pharah, bot)
        self.assertEqual(npc_control.start(match, pharah), bot.entity)
        for _ in range(150):
            run.step({})
            if vitals.total < before:
                break
        self.assertEqual(vitals.total, before - 24)
        self.assertEqual(weapon.vars[53].value(), 3)

    def test_trooper_manual_reload_refills_and_restores_actual_fire_against_pharah(self):
        definition = next(row for row in self.catalog.values() if row.name == 'Trooper')
        run, pharah, bot = self.create(definition, distance=5)
        match = run.match
        vitals = self.health(run, pharah)
        weapon = next(i for i in bot.body_script.component.instances.values() if i.graph.index == 0x102E)
        self.assertEqual(npc_control.start(match, pharah), bot.entity)
        for _ in range(90):
            run.step({})
            if vitals.total < 200:
                break
        self.assertEqual(vitals.total, 180)
        self.assertEqual(weapon.vars[53].value(), 29)
        self.assertEqual(npc_control.stop(match), 1)
        aim = self.aim_at_body(run, pharah, bot)
        run.step({pharah: (*aim, 0)})
        self.assertEqual(npc_control.reload(match, pharah), bot.entity)
        run.step({})
        self.assertTrue(weapon.vars[51].value())
        self.assertEqual(weapon.vars[53].value(), 29)
        start = run.frame
        for _ in range(150):
            run.step({})
            self.assertEqual(vitals.total, 180)
            self.assertFalse(bot.attack_input)
            if not weapon.vars[51].value():
                break
        self.assertGreater(run.frame - start, 10)
        self.assertFalse(weapon.vars[51].value())
        self.assertEqual(weapon.vars[53].value(), 30)
        pharah.mover.yaw, pharah.mover.pitch = self.aim_at_body(run, pharah, bot)
        self.assertEqual(npc_control.start(match, pharah), bot.entity)
        for _ in range(90):
            run.step({})
            if vitals.total < 180:
                break
        self.assertEqual(vitals.total, 160)
        self.assertEqual(weapon.vars[53].value(), 29)

    def test_trooper_reload_suspends_held_fire_then_resumes_against_pharah(self):
        definition = next(row for row in self.catalog.values() if row.name == 'Trooper')
        run, pharah, bot = self.create(definition, distance=5)
        match = run.match
        vitals = self.health(run, pharah)
        weapon = next(i for i in bot.body_script.component.instances.values() if i.graph.index == 0x102E)
        self.assertEqual(npc_control.start(match, pharah), bot.entity)
        for _ in range(90):
            if vitals.total < 200:
                break
            run.step({})
        self.assertEqual(vitals.total, 180)
        self.assertEqual(weapon.vars[53].value(), 29)
        self.assertTrue(bot.attack_input)
        self.assertEqual(npc_control.reload(match, pharah), bot.entity)
        run.step({})
        self.assertTrue(weapon.vars[51].value())
        self.assertTrue(bot.attack_input)
        ammo_during_reload = []
        for _ in range(150):
            if not weapon.vars[51].value():
                break
            self.assertTrue(bot.attack_input)
            ammo_during_reload.append(weapon.vars[53].value())
            self.assertEqual(vitals.total, 180)
            run.step({})
        self.assertEqual(list(dict.fromkeys(ammo_during_reload)), [29, 30])
        self.assertEqual(ammo_during_reload, sorted(ammo_during_reload))
        self.assertFalse(weapon.vars[51].value())
        self.assertTrue(bot.attack_input)
        self.assertEqual(weapon.vars[53].value(), 30)
        for _ in range(90):
            run.step({})
            self.assertTrue(bot.attack_input)
            if vitals.total < 180:
                break
            self.assertEqual(weapon.vars[53].value(), 30)
        self.assertEqual(vitals.total, 160)
        self.assertEqual(weapon.vars[53].value(), 29)

    def test_autonomous_trooper_reloads_empty_magazine_and_resumes_fire(self):
        definition = next(row for row in self.catalog.values() if row.name == "Trooper")
        run, pharah, spare = self.encounter()
        match = run.match
        match.game_map = replace(match.game_map, free_for_all=False)
        pharah.team, spare.team = 0, 1
        pharah.client = pve.NpcClient(pharah)
        spare.client = pve.NpcClient(spare)
        bot = npc.spawn(match, definition, (0, pharah.mover.position[1], 8), team=1, autonomous=True)
        weapon = next(i for i in bot.body_script.component.instances.values() if i.graph.index == 0x102E)
        health = self.health(run, pharah)
        health.grant_temporary_shields(0x100, 700, 700)
        for _ in range(1000):
            run.step({})
            if health.total <= 300:
                break
        self.assertEqual(health.total, 300)
        self.assertEqual(weapon.vars[53].value(), 0)
        reloaded = False
        for _ in range(180):
            run.step({})
            reloaded |= bool(weapon.vars[51].value())
            if weapon.vars[53].value() == 30 and not weapon.vars[51].value():
                break
            self.assertEqual(health.total, 300)
        self.assertTrue(reloaded)
        self.assertEqual(weapon.vars[53].value(), 30)
        for _ in range(80):
            run.step({})
            if health.total < 300:
                break
        self.assertEqual(health.total, 280)
        self.assertEqual(weapon.vars[53].value(), 29)

    def test_trooper_releases_outside_manual_range_and_reengages_pharah(self):
        definition = next(row for row in self.catalog.values() if row.name == 'Trooper')
        run, pharah, bot = self.create(definition, distance=5)
        vitals = self.health(run, pharah)
        component = bot.body_script.component
        weapon = next(i for i in component.instances.values() if i.graph.index == 0x102E)
        button = npc_control.button(bot)
        self.assertEqual(npc_control.start(run.match, pharah), bot.entity)
        for _ in range(90):
            run.step({})
            if vitals.total < 200:
                break
        self.assertEqual(vitals.total, 180)
        self.assertEqual(weapon.vars[53].value(), 29)
        self.assertIn(button, component.held)

        original_position = bot.position
        bot.position = (*original_position[:2], npc_control.AIM_RANGE + 1)
        ammo_outside_range = []
        for _ in range(100):
            run.step({})
            self.assertFalse(bot.attack_input)
            self.assertNotIn(button, component.held)
            self.assertEqual(vitals.total, 180)
            ammo_outside_range.append(weapon.vars[53].value())
        remaining = ammo_outside_range[-1]
        self.assertEqual(ammo_outside_range[-40:], [remaining] * 40, ammo_outside_range)
        self.assertTrue(bot.firing)

        bot.position = original_position
        for _ in range(90):
            run.step({})
            if vitals.total < 180:
                break
        self.assertEqual(vitals.total, 160)
        self.assertEqual(weapon.vars[53].value(), remaining - 1)
        self.assertIn(button, component.held)

    def test_trooper_primary_tracks_and_hits_pharah_during_actual_flight(self):
        definition = next(row for row in self.catalog.values() if row.name == "Trooper")
        run, pharah, bot = self.create(definition)
        vitals = self.health(run, pharah)
        ground = pharah.mover.position[1]
        hover = next(i for i in pharah.body_script.component.instances.values() if i.graph.index == 0x25)
        fuel = hover.vars[87].value()
        self.assertEqual(npc_control.start(run.match, pharah), bot.entity)
        run.step({pharah: (0, 0, 8 | 2)})
        self.assertEqual(pharah.mover.velocity[1], 20)
        for _ in range(60):
            if vitals.total < 200:
                break
            run.step({pharah: (0, 0, 2)})
        self.assertEqual(vitals.total, 180)
        self.assertGreater(pharah.mover.position[1], ground + 2)
        self.assertLess(hover.vars[87].value(), fuel)
        self.assertLess(bot.pitch, 0)
        self.assertTrue(bot.attack_input)
        weapon = next(i for i in bot.body_script.component.instances.values() if i.graph.index == 0x102E)
        self.assertEqual(weapon.vars[53].value(), 29)

    def test_trooper_releases_when_jump_jet_exits_range_then_hits_after_pharah_returns(self):
        definition = next(row for row in self.catalog.values() if row.name == "Trooper")
        run, pharah, bot = self.create(definition, distance=npc_control.AIM_RANGE - .1)
        vitals = self.health(run, pharah)
        held = bot.body_script.component.held
        button = npc_control.button(bot)
        self.assertEqual(npc_control.start(run.match, pharah), bot.entity)
        run.step({})
        self.assertTrue(bot.attack_input)
        run.step({pharah: (-32768, 0, 8)})
        self.assertEqual(pharah.mover.velocity[1], 20)
        for _ in range(35):
            if not bot.attack_input:
                break
            run.step({})
        self.assertFalse(bot.attack_input)
        self.assertNotIn(button, held)
        self.assertEqual(vitals.total, 200)
        for _ in range(15):
            run.step({})
            self.assertFalse(bot.attack_input)
            self.assertEqual(vitals.total, 200)
        for _ in range(260):
            if vitals.total < 200:
                break
            run.frame += 1
            run.match.update(run.frame * .016, run.frame)
            pharah.take_commands([Command(run.frame, yaw=0, forward=127)])
        self.assertTrue(bot.attack_input)
        self.assertIn(button, held)
        self.assertEqual(vitals.total, 180)

    def test_winston_barrier_intercepts_hostile_trooper_and_heavy_hitscan(self):
        for name, damage in (("Trooper", 20), ("Heavy Assault", 5)):
            definition = next(row for row in self.catalog.values() if row.name == name)
            for hostile in (False, True):
                with self.subTest(name=name, hostile=hostile):
                    run, pharah, attacker = self.create(definition)
                    match = run.match
                    fight = match.combat
                    winston = run.players[1]
                    self.assertTrue(match.switch_hero(winston, heroes()[0x02E0000000000009]))
                    match.game_map = replace(match.game_map, free_for_all=False)
                    pharah.team = 0
                    winston.team = pharah.team if hostile else attacker.team
                    winston.mover.position = [2, pharah.mover.position[1], 0]
                    winston.mover.body = None
                    run.step({winston: (0, 0, 16)})
                    for _ in range(4):
                        run.step({})
                    bubble = fight.winston_bubbles[winston.body]
                    self.assertEqual(bubble.health, 700)
                    pharah.mover.yaw, pharah.mover.pitch = self.aim_at_body(run, pharah, attacker)
                    self.assertEqual(npc_control.start(match, pharah), attacker.entity)
                    health = self.health(run, pharah)
                    for _ in range(160):
                        run.step({})
                        if bubble.health < 700 or health.total < 200:
                            break

                    self.assertEqual(bubble.health, 700 - damage if hostile else 700)
                    self.assertEqual(health.total, 200 if hostile else 200 - damage)
                    if hostile:
                        bubble.health = damage
                        for _ in range(120):
                            run.step({})
                            if bubble.health == 0:
                                break
                        self.assertEqual(bubble.health, 0)
                        self.assertEqual(health.total, 200)
                        for _ in range(120):
                            run.step({})
                            if health.total < 200:
                                break
                        self.assertEqual(health.total, 200 - damage)

    def test_deployed_orisa_and_sigma_meshes_intercept_trooper_hitscan(self):
        definition = next(row for row in self.catalog.values() if row.name == "Trooper")
        cases = (("Orisa", "ORPHEUS_ORISA_GEOMETRY", 1), ("Sigma", "ORPHEUS_SIGMA_BARRIER_MODEL", -1))
        for name, asset, attacker_side in cases:
            with self.subTest(name=name):
                if not os.environ.get(asset):
                    self.skipTest("prepared 104319 barrier mesh required")
                run, pharah, attacker = self.create(definition)
                match = run.match
                fight = match.combat
                owner = run.players[1]
                hero = next(hero for hero in heroes().values() if hero.name == name)
                self.assertTrue(match.switch_hero(owner, hero))
                match.game_map = replace(match.game_map, free_for_all=False)
                pharah.team = owner.team = 0
                y = pharah.mover.position[1]
                owner.mover.position = [12, y, 8]
                owner.mover.body = None
                if name == "Orisa":
                    run.step({owner: (0, 0, 16)})
                    for _ in range(120):
                        if owner.body in fight.orisa_shields:
                            break
                        run.step({})
                    barrier = fight.orisa_shields[owner.body]
                else:
                    for frame in range(15):
                        run.step({owner: (-32768, 0, 2 if frame < 8 else 0)})
                    barrier, = fight.sigma_barriers.values()
                    self.assertTrue(barrier.active)

                x, _, z = barrier.position
                pharah.mover.position = [x, y, z - 3 * attacker_side]
                owner.mover.position = [x + 15, y, z]
                pharah.mover.body = owner.mover.body = None
                attacker.position = (x, y, z + 6 * attacker_side)
                pharah.mover.yaw, pharah.mover.pitch = self.aim_at_body(run, pharah, attacker)
                self.assertEqual(npc_control.start(match, pharah), attacker.entity)
                before = barrier.health if name == "Orisa" else barrier.vitals.total
                self.assertEqual(before, 600 if name == "Orisa" else 700)
                pharah_health = self.health(run, pharah)
                for _ in range(100):
                    run.step({})
                    remaining = barrier.health if name == "Orisa" else barrier.vitals.total
                    if remaining < before or pharah_health.total < 200:
                        break
                self.assertEqual(remaining, before - 20)
                self.assertEqual(pharah_health.total, 200)

    def test_held_and_attached_barriers_intercept_trooper_hitscan(self):
        definition = next(row for row in self.catalog.values() if row.name == "Trooper")
        cases = (
            ("Reinhardt", "ORPHEUS_REINHARDT_COLLISION", 1600, 0),
            ("Brigitte", "ORPHEUS_BRIGITTE_SHIELD_MODEL", 250, 0),
            ("Brigitte", "ORPHEUS_BRIGITTE_SHIELD_MODEL", 250, 0.55),
            ("Zarya", None, 200, 0),
        )
        for name, asset, before, shield_x in cases:
            with self.subTest(name=name, shield_x=shield_x):
                if asset is not None and not Path(os.environ.get(asset, "")).is_file():
                    self.skipTest("prepared 104319 barrier mesh required")
                run, pharah, attacker = self.create(definition)
                match = run.match
                fight = match.combat
                owner = run.players[1]
                hero = next(hero for hero in heroes().values() if hero.name == name)
                self.assertTrue(match.switch_hero(owner, hero))
                match.game_map = replace(match.game_map, free_for_all=False)
                pharah.team = owner.team = 0
                y = pharah.mover.position[1]
                pharah.mover.position = [0, y, 8]
                owner.mover.position = [4.5, y, 8]
                pharah.mover.body = owner.mover.body = None
                attacker.position = (0, y, 0)
                for _ in range(30):
                    run.step({})
                if name == "Zarya":
                    run.step({owner: (-16384, 1000, 16)})
                    for _ in range(3):
                        run.step({})
                    barrier, = fight.zarya_bubbles.values()
                    self.assertIs(barrier.target, pharah)
                    held = {}
                else:
                    held = {owner: (-32768, 0, 2)}
                    for _ in range(20):
                        run.step(held)
                    if name == "Reinhardt":
                        barrier = fight.barrier_pets[owner.body]
                    else:
                        barrier = fight.brigitte_barriers[owner.body]
                        self.assertTrue(barrier.raised())
                        self.assertIsNotNone(barrier.geometry)

                def shield_health():
                    if name == "Reinhardt":
                        return pool_sum(barrier.world.health_pools)
                    return barrier.health

                pharah.mover.yaw, pharah.mover.pitch = self.aim_at_body(run, pharah, attacker)
                self.assertEqual(npc_control.start(match, pharah), attacker.entity)
                if held:
                    owner.mover.position = [shield_x, y, 4.5]
                    owner.mover.body = None
                self.assertEqual(shield_health(), before)
                pharah_health = self.health(run, pharah)
                owner_health = self.health(run, owner)
                owner_before = owner_health.total
                for _ in range(100):
                    run.step(held)
                    if shield_health() < before or pharah_health.total < 200 or owner_health.total < owner_before:
                        break
                self.assertEqual(shield_health(), before - 20)
                self.assertEqual(pharah_health.total, 200)
                self.assertEqual(owner_health.total, owner_before)

    def test_genji_deflect_redirects_trooper_hitscan_and_damage_credit(self):
        definition = next(row for row in self.catalog.values() if row.name == "Trooper")
        for allied_to_trooper in (False, True):
            with self.subTest(allied_to_trooper=allied_to_trooper):
                run, pharah, attacker = self.create(definition)
                match = run.match
                genji = run.players[1]
                self.assertTrue(match.switch_hero(genji, heroes()[0x02E0000000000029]))
                genji.body_script.ult_seconds = 0
                genji.body_script.set_ult_charge(0)
                match.game_map = replace(match.game_map, free_for_all=False)
                pharah.team = 0
                genji.team = attacker.team if allied_to_trooper else pharah.team
                y = pharah.mover.position[1]
                pharah.mover.position = [0, y, 8]
                pharah.mover.body = None
                attacker.position = (0, y, 0)
                for _ in range(30):
                    run.step({})
                pharah.mover.yaw, pharah.mover.pitch = self.aim_at_body(run, pharah, attacker)
                self.assertEqual(npc_control.start(match, pharah), attacker.entity)
                genji.mover.position = [0, y, 4]
                genji.mover.body = None
                yaw, pitch = self.aim_at_body(run, genji, attacker)
                run.step({genji: (yaw, pitch, 16)})
                pharah_health = self.health(run, pharah)
                genji_health = self.health(run, genji)
                trooper_health = self.health(run, attacker)
                before = trooper_health.total
                charge = genji.body_script.component.entity_var(342)
                charge_before = charge.value()
                for _ in range(90):
                    run.step({genji: (yaw, pitch, 0)})
                    if trooper_health.total < before or genji_health.total < 200 or pharah_health.total < 200:
                        break
                self.assertEqual(genji_health.total, 200)
                self.assertEqual(trooper_health.total, before if allied_to_trooper else before - 20)
                self.assertEqual(pharah_health.total, 180 if allied_to_trooper else 200)
                self.assertEqual(charge.value() - charge_before, 0 if allied_to_trooper else 20)

    def test_sigma_grasp_absorbs_trooper_hitscan_and_grants_temporary_shield(self):
        definition = next(row for row in self.catalog.values() if row.name == "Trooper")
        run, pharah, attacker = self.create(definition)
        match = run.match
        sigma = run.players[1]
        self.assertTrue(match.switch_hero(sigma, heroes()[0x02E000000000023B]))
        match.game_map = replace(match.game_map, free_for_all=False)
        pharah.team = sigma.team = 0
        sigma.mover.position = [0.75, pharah.mover.position[1], 4]
        sigma.mover.body = None
        for _ in range(30):
            run.step({})
        pharah.mover.yaw, pharah.mover.pitch = self.aim_at_body(run, pharah, attacker)
        self.assertEqual(npc_control.start(match, pharah), attacker.entity)
        run.step({sigma: (0, 0, 8)})
        grasp = next(i for i in sigma.body_script.component.instances.values() if i.graph.index == sigma_grasp.GRASP)
        weapon = next(i for i in attacker.body_script.component.instances.values() if i.graph.index == 0x102E)
        for _ in range(80):
            run.step({})
            if weapon.vars[53].value() < 30:
                break
        self.assertEqual(weapon.vars[53].value(), 29)
        self.assertTrue(grasp.states[1].active)
        self.assertEqual(grasp.vars[21312].value(), 20)
        self.assertEqual(self.health(run, pharah).total, 200)
        self.assertEqual(self.health(run, sigma).total, 400)
        npc_control.stop(match)
        for _ in range(160):
            if sigma.body in match.combat.grasp_shields:
                break
            run.step({})
        self.assertEqual(self.health(run, sigma).temporary_shields[sigma_grasp.SHIELD_TAG].now, 12)
        self.assertEqual(self.health(run, sigma).total, 412)

    def test_trooper_primary_has_independent_authored_hitscan_range(self):
        definition = next(row for row in self.catalog.values() if row.name == 'Trooper')
        # Permit input and aiming beyond the weapon reach in both cases.
        with patch.object(npc_control, 'AIM_RANGE', 110.0):
            for distance, expected_health in ((99.9, 180), (101, 200)):
                with self.subTest(distance=distance):
                    run, pharah, bot = self.create(definition, distance=distance)
                    vitals = self.health(run, pharah)
                    component = bot.body_script.component
                    weapon = next(i for i in component.instances.values() if i.graph.index == 0x102E)
                    self.assertEqual(npc_control.start(run.match, pharah), bot.entity)
                    for _ in range(90):
                        run.step({})
                        if 53 in weapon.vars and weapon.vars[53].value() < 30:
                            break
                    self.assertTrue(bot.attack_input)
                    self.assertEqual(weapon.vars[53].value(), 29)
                    self.assertEqual(vitals.total, expected_health)

    def test_lethal_pharah_rocket_stops_trooper_fire_and_bounds_corpse_script(self):
        definition = next(row for row in self.catalog.values() if row.name == 'Trooper')
        run, pharah, bot = self.create(definition, distance=5)
        script = bot.body_script
        controller = script.associated_component
        vitals = self.health(run, pharah)
        self.assertEqual(npc_control.start(run.match, pharah), bot.entity)
        for _ in range(90):
            run.step({})
            if vitals.total < 200:
                break
        self.assertEqual(vitals.total, 180)

        enemy = self.health(run, bot)
        enemy.damage(enemy.total - 1)
        run.step({pharah: (*self.aim_at_body(run, pharah, bot), FIRE)})
        for _ in range(40):
            if enemy.dead:
                break
            run.step({})
        self.assertTrue(enemy.dead)
        self.assertEqual(enemy.killer, pharah.body)
        self.assertFalse(bot.attack_input)
        self.assertTrue(script.component.owner_dead)
        self.assertIs(bot.body_script.associated_component, controller)

        after_death = vitals.total
        limit = script.death_frame + int(combat.BOT_RESPAWN / combat.TICK)
        while run.frame <= limit + 2:
            run.step({})
            self.assertEqual(vitals.total, after_death)
        self.assertEqual(controller.frame, limit)
        self.assertIs(bot.body_script, script)

    def test_sniper_actual_alternate_and_primary_switch_against_pharah(self):
        definition = next(row for row in self.catalog.values() if row.name == 'Sniper')
        run, pharah, bot = self.create(definition)
        match = run.match
        fight = match.combat
        selector = bot.body_script.component.entity_var(31)
        vitals = self.health(run, pharah)
        for alternate, slot, graph_id, damage in ((True, 2, 0x1134, 5), (False, 1, 0x1122, 100)):
            with self.subTest(alternate=alternate):
                before = vitals.total
                self.assertEqual(npc_control.start(match, pharah, alternate=alternate), bot.entity)
                self.assertEqual(selector.value(), slot)
                with patch.object(fight, "_shoot", wraps=fight._shoot) as shoot:
                    for _ in range(160):
                        run.step({})
                        if shoot.called:
                            break
                self.assertEqual([call.args[2].instance.graph.index for call in shoot.call_args_list],
                                 [graph_id])
                for _ in range(15):
                    run.step({})
                    if vitals.total < before:
                        break
                self.assertEqual(vitals.total, before - damage)

    def test_allied_genji_reflects_both_sniper_projectiles_away_from_pharah(self):
        definition = next(row for row in self.catalog.values() if row.name == "Sniper")
        for alternate, damage in ((True, 5), (False, 100)):
            for deflect in (False, True):
                with self.subTest(alternate=alternate, deflect=deflect):
                    run, pharah, sniper = self.create(definition, distance=25)
                    genji = run.players[1]
                    self.assertTrue(run.match.switch_hero(genji, heroes()[0x02E0000000000029]))
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    genji.team = pharah.team = 0
                    script = genji.body_script
                    script.ult_seconds = 0
                    script.set_ult_charge(0)
                    fight = run.match.combat
                    pharah_health = self.health(run, pharah)
                    sniper_health = self.health(run, sniper)
                    before = sniper_health.total
                    self.assertEqual(npc_control.start(run.match, pharah, alternate=alternate), sniper.entity)
                    genji.mover.position = [.55, pharah.mover.position[1], 12]
                    genji.mover.body = None

                    for _ in range(160):
                        if fight.rockets:
                            break
                        run.step({})
                    shot, = (rocket for rocket in fight.rockets if rocket.shooter is sniper)
                    self.assertEqual(shot.direct.amount, damage)
                    self.assertEqual(shot.speed, 200)
                    run.step({genji: (0, 0, 16 if deflect else 0)})
                    reflected = None
                    for _ in range(30):
                        reflected = reflected or next((r for r in fight.rockets if r.shooter is genji), None)
                        if pharah_health.total < 200 or sniper_health.total < before:
                            break
                        run.step({genji: (0, 0, 0)})

                    if deflect:
                        self.assertIsNotNone(reflected)
                        self.assertEqual(reflected.source_body, genji.body)
                    else:
                        self.assertIsNone(reflected)
                    self.assertEqual(pharah_health.total, 200 if deflect else 200 - damage)
                    self.assertEqual(sniper_health.total, before - damage if deflect else before)
                    self.assertEqual(self.health(run, genji).total, 200)
                    self.assertEqual(script.component.entity_var(342).value(), damage if deflect else 0)

    def test_allied_genji_reflects_enforcer_fan_and_earns_damage_credit(self):
        definition = next(row for row in self.catalog.values() if row.name == "Enforcer")
        for deflect in (False, True):
            with self.subTest(deflect=deflect):
                run, pharah, enforcer = self.create(definition)
                match = run.match
                genji = run.players[1]
                self.assertTrue(match.switch_hero(genji, heroes()[0x02E0000000000029]))
                match.game_map = replace(match.game_map, free_for_all=False)
                genji.team = pharah.team = 0
                script = genji.body_script
                script.ult_seconds = 0
                script.set_ult_charge(0)
                for _ in range(30):
                    run.step({})
                self.assertEqual(npc_control.start(match, pharah), enforcer.entity)
                fight = match.combat
                for _ in range(120):
                    if fight.rockets:
                        break
                    run.step({})
                self.assertEqual(len(fight.rockets), 25)
                self.assertTrue(all(rocket.shooter is enforcer for rocket in fight.rockets))
                genji.mover.position = [.55, pharah.mover.position[1], 4]
                genji.mover.body = None
                yaw, pitch = self.aim_at_body(run, genji, enforcer)
                run.step({genji: (yaw, pitch, 16 if deflect else 0)})
                health = self.health(run, enforcer)
                before = health.total
                for _ in range(20):
                    run.step({genji: (yaw, pitch, 0)})
                self.assertEqual(self.health(run, pharah).total, 200 if deflect else 176)
                self.assertEqual(self.health(run, genji).total, 200 if deflect else 180)
                self.assertEqual(before - health.total, 38 if deflect else 0)
                self.assertEqual(script.component.entity_var(342).value(), 38 if deflect else 0)

    def test_allied_sigma_absorbs_sniper_rounds_and_enforcer_fan(self):
        definitions = {row.name: row for row in self.catalog.values()}
        cases = (
            ("Sniper", True, 25, 1, 5),
            ("Sniper", False, 25, 1, 100),
            ("Enforcer", False, 8, 25, 2),
        )
        for name, alternate, distance, count, damage in cases:
            with self.subTest(name=name, alternate=alternate):
                run, pharah, attacker = self.create(definitions[name], distance=distance)
                match = run.match
                sigma = run.players[1]
                self.assertTrue(match.switch_hero(sigma, heroes()[0x02E000000000023B]))
                match.game_map = replace(match.game_map, free_for_all=False)
                sigma.team = pharah.team = 0
                y = pharah.mover.position[1]
                sigma.mover.position = [10, y, distance / 2]
                sigma.mover.body = None
                for _ in range(30):
                    run.step({})
                self.assertEqual(npc_control.start(match, pharah, alternate=alternate), attacker.entity)
                fight = match.combat
                for _ in range(160):
                    if fight.rockets:
                        break
                    run.step({})
                fan = tuple(rocket for rocket in fight.rockets if rocket.shooter is attacker)
                self.assertEqual(len(fan), count)
                self.assertEqual({rocket.direct.amount for rocket in fan}, {damage})
                sigma.mover.position = [.55, y, distance / 2]
                sigma.mover.body = None
                run.step({sigma: (0, 0, 8)})
                for _ in range(15):
                    run.step({})
                grasp = next(i for i in sigma.body_script.component.instances.values()
                             if i.graph.index == sigma_grasp.GRASP)
                self.assertTrue(grasp.states[1].active)
                self.assertEqual(self.health(run, pharah).total, 200)
                self.assertEqual(self.health(run, sigma).total, 400)
                absorbed = grasp.vars[21312].value()
                self.assertEqual(absorbed, count * damage)
                npc_control.stop(match)
                for _ in range(160):
                    if sigma.body in fight.grasp_shields:
                        break
                    run.step({})
                shield = self.health(run, sigma).temporary_shields[sigma_grasp.SHIELD_TAG]
                self.assertAlmostEqual(shield.now, absorbed * .6, places=4)

    def test_jump_jet_evades_sniper_alternate_after_projectile_release(self):
        definition = next(row for row in self.catalog.values() if row.name == "Sniper")
        for jump in (False, True):
            with self.subTest(jump=jump):
                run, pharah, bot = self.create(definition, distance=25)
                fight = run.match.combat
                vitals = self.health(run, pharah)
                ground = pharah.mover.position[1]
                self.assertEqual(npc_control.start(run.match, pharah, alternate=True), bot.entity)
                for _ in range(150):
                    if fight.rockets:
                        break
                    run.step({})
                shot, = (rocket for rocket in fight.rockets if rocket.shooter is bot)
                self.assertEqual(shot.speed, 200)
                self.assertEqual(vitals.total, 200)

                run.step({pharah: (-32768, 0, 8 if jump else 0)})
                if jump:
                    self.assertEqual(pharah.mover.velocity[1], 20)
                for _ in range(24):
                    if shot not in fight.rockets or shot.position[2] < pharah.mover.position[2] - 1:
                        break
                    run.step({})
                self.assertEqual(vitals.total, 200 if jump else 195)
                if jump:
                    self.assertIn(shot, fight.rockets)
                    self.assertLess(shot.position[2], pharah.mover.position[2] - 1)
                    self.assertGreater(pharah.mover.position[1], ground + 1)
                else:
                    self.assertNotIn(shot, fight.rockets)

    def test_sniper_primary_aims_up_at_launch_and_brackets_airborne_contact(self):
        definition = next(row for row in self.catalog.values() if row.name == "Sniper")
        for distance, expected in ((8, 100), (25, 200)):
            with self.subTest(distance=distance):
                run, pharah, bot = self.create(definition, distance=distance)
                fight = run.match.combat
                vitals = self.health(run, pharah)
                ground = pharah.mover.position[1]
                self.assertEqual(npc_control.start(run.match, pharah), bot.entity)
                for _ in range(100):
                    run.step({})
                self.assertEqual(vitals.total, 200)
                run.step({pharah: (-32768, 0, 8)})
                self.assertEqual(pharah.mover.velocity[1], 20)
                for _ in range(60):
                    if fight.rockets:
                        break
                    run.step({})
                shot, = (rocket for rocket in fight.rockets if rocket.shooter is bot)
                self.assertEqual(shot.speed, 200)
                self.assertGreater(shot.direction[1], 0)
                self.assertLess(bot.pitch, 0)
                self.assertEqual(vitals.total, 200)
                for _ in range(24):
                    if shot not in fight.rockets or shot.position[2] < pharah.mover.position[2] - 1:
                        break
                    run.step({})
                self.assertEqual(vitals.total, expected)
                self.assertGreater(pharah.mover.position[1], ground + 2)
                if expected == 200:
                    self.assertIn(shot, fight.rockets)
                    self.assertLess(shot.position[2], pharah.mover.position[2] - 1)
                else:
                    self.assertNotIn(shot, fight.rockets)

    def test_sniper_alternate_in_flight_hits_during_barrage(self):
        definition = next(row for row in self.catalog.values() if row.name == 'Sniper')
        for initial_health in (200, 5):
            with self.subTest(initial_health=initial_health):
                run, pharah, bot = self.create(definition, distance=25)
                fight = run.match.combat
                script = pharah.body_script
                script.set_ult_charge(script.component.entity_var(338).value())
                vitals = self.health(run, pharah)
                if initial_health < vitals.total:
                    vitals.damage(vitals.total - initial_health)
                self.assertEqual(npc_control.start(run.match, pharah, alternate=True), bot.entity)
                for _ in range(150):
                    run.step({})
                    if any(rocket.shooter is bot for rocket in fight.rockets):
                        break
                rocket, = [rocket for rocket in fight.rockets if rocket.shooter is bot]
                self.assertEqual(rocket.speed, 200)
                self.assertEqual(vitals.total, initial_health)

                run.step({pharah: (-32768, 0, 32)})
                self.assertTrue(script.ultimate_running())
                volley = next(i for i in script.component.instances.values() if i.graph.index == 0x2B).states[1]
                for _ in range(20):
                    run.step({pharah: (-32768, 0, 0)})
                    if vitals.total < initial_health:
                        break
                self.assertEqual(vitals.total, initial_health - 5)
                self.assertNotIn(rocket, fight.rockets)
                count = volley.reported
                for _ in range(12):
                    run.step({pharah: (-32768, 0, 0)})
                if initial_health == 5:
                    self.assertEqual(vitals.killer, bot.entity)
                    self.assertFalse(script.ultimate_running())
                    self.assertEqual(volley.reported, count)
                    self.assertFalse(bot.attack_input)
                else:
                    self.assertTrue(script.ultimate_running())
                    self.assertGreater(volley.reported, count)

    def test_stopping_sniper_alternate_cancels_its_live_projectile(self):
        definition = next(row for row in self.catalog.values() if row.name == 'Sniper')
        run, pharah, bot = self.create(definition, distance=25)
        fight = run.match.combat
        self.assertEqual(npc_control.start(run.match, pharah, alternate=True), bot.entity)
        for _ in range(150):
            run.step({})
            if fight.rockets:
                break
        rocket, = [rocket for rocket in fight.rockets if rocket.shooter is bot]
        self.assertEqual(rocket.speed, 200)
        self.assertEqual(self.health(run, pharah).total, 200)
        self.assertEqual(npc_control.stop(run.match), 1)
        self.assertFalse(fight.rockets)
        for _ in range(25):
            run.step({})
        self.assertEqual(self.health(run, pharah).total, 200)
        self.assertFalse(bot.attack_input)

    def test_sniper_graph_reposition_windup_and_selector_reset(self):
        definition = next(row for row in self.catalog.values() if row.name == "Sniper")
        run, _, bot = self.create(definition, distance=2)
        move = sniper_reposition.move_state(bot, create=True)
        instance = move.instance
        component = instance.component

        # Let the initial graph setup and its delayed selector reset finish.
        for _ in range(50):
            run.step({})
        switch = instance.node_state(instance.graph.node(55))
        destination = expr.Vec3(0, bot.position[1], 6)
        instance.write((expr.INSTANCE, 1220), destination)
        switch.plug_enter("m_beginPlug")
        component.run_now()

        def advance(frames):
            for _ in range(frames):
                component.run_frame(component.frame + 1)

        def state(position):
            return instance.states[instance.graph.node(position).state]

        instance.run_node(instance.graph.node(45), None)
        component.run_now()
        first_wait = state(96)
        self.assertTrue(first_wait.active)
        advance(5)
        instance.run_node(instance.graph.node(106), None)
        component.run_now()
        self.assertFalse(first_wait.active)
        advance(48)
        self.assertEqual(move.counter, 0)

        started_at = component.now
        instance.run_node(instance.graph.node(45), None)
        component.run_now()
        advance(15)
        self.assertTrue(first_wait.active)
        self.assertFalse(move.active)
        advance(1)
        second_wait = state(94)
        self.assertTrue(second_wait.active)
        self.assertEqual(second_wait.start - started_at, 250)
        advance(30)
        self.assertFalse(move.active)
        advance(1)
        self.assertEqual(move.start - started_at, 750)
        self.assertEqual(move.move_request(), (destination, 20))

    def wait_for_sniper_motion(self, run, bot):
        for _ in range(115):
            state = sniper_reposition.move_state(bot)
            if state is not None and state.active:
                first = sniper_reposition.state_at(state.instance, 96)
                self.assertEqual(state.start - first.start, 750)
                return state
            run.step({})
        self.fail("Sniper never started the authored MoveTo")

    def assert_sniper_arrived(self, bot, destination):
        for actual, expected in zip(bot.position, destination, strict=True):
            self.assertAlmostEqual(actual, expected, places=2)

    def test_autonomous_sniper_repositions_and_fires_during_reposition_cooldown(self):
        for interrupted in (False, True):
            with self.subTest(interrupted=interrupted):
                self.autonomous_sniper_reposition(interrupted)

    def autonomous_sniper_reposition(self, interrupted):
        definition = next(row for row in self.catalog.values() if row.name == "Sniper")
        run, pharah, spare = self.encounter()
        match = run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        pharah.team, spare.team = 0, 1
        pharah.client = pve.NpcClient(pharah)
        spare.client = pve.NpcClient(spare)
        y = pharah.mover.position[1]
        spare.mover.position = [30, y, 0]
        spare.mover.body = None
        bot = npc.spawn(match, definition, (0, y, 2), team=1, autonomous=True)
        health = self.health(run, pharah)
        state = self.wait_for_sniper_motion(run, bot)
        destination, speed = state.move_request()
        self.assertEqual((state.node.pos, speed), (16, 20))
        if interrupted:
            run.step({})
            self.assertTrue(state.active)
            stopped = bot.position
            self.assertGreater(math.dist(stopped, (0, y, 2)), 0)
            pharah.team = bot.team
            run.step({})
            self.assertFalse(state.active)
            self.assertEqual(bot.position, stopped)
            self.assertFalse(bot.attack_input)
            self.assertIsNone(bot.attack_target)
            self.assertEqual(health.total, 200)
            pharah.team = 0
        for _ in range(40):
            if not state.active:
                break
            self.assertFalse(bot.attack_input)
            self.assertFalse(fight.rockets)
            self.assertEqual(health.total, 200)
            run.step({})
        self.assertFalse(state.active)
        if not interrupted:
            self.assert_sniper_arrived(bot, destination)
        if interrupted:
            pharah.mover.position = [bot.position[0], y, bot.position[2] - 4]
            pharah.mover.body = None
        for _ in range(210):
            run.step({})
            self.assertEqual(state.counter, 1)
            if health.total < 200:
                break
        self.assertEqual(health.total, 100)
        self.assertTrue(bot.attack_input)

    def test_sniper_repositions_then_returns_fire_and_takes_pharah_concussion(self):
        definition = next(row for row in self.catalog.values() if row.name == 'Sniper')
        run, pharah, bot = self.create(definition, distance=2)
        match = run.match
        self.assertEqual(npc_control.start(match, pharah), bot.entity)
        self.assertEqual(npc_control.reposition(match, pharah), bot.entity)
        state = self.wait_for_sniper_motion(run, bot)
        destination, speed = state.move_request()
        self.assertEqual((state.node.pos, speed), (16, 20))
        mover = bot.impulse_mover
        vitals = self.health(run, pharah)
        for _ in range(40):
            run.step({})
            self.assertEqual(vitals.total, 200)
            if not state.active:
                break
            self.assertFalse(bot.attack_input)
        self.assertFalse(state.active)
        self.assert_sniper_arrived(bot, destination)
        self.assertEqual(bot.velocity, (0, 0, 0))
        for _ in range(210):
            run.step({})
            if vitals.total < 200:
                break
        self.assertEqual(vitals.total, 100)
        npc_control.stop(match)
        pharah.mover.position[0] = bot.position[0]
        pharah.mover.position[2] = bot.position[2] - 2
        pharah.mover.body = None
        before = bot.position[2]
        health = self.health(run, bot).total
        direction = self.aim_at_body(run, pharah, bot)
        run.step({pharah: (*direction, 16)})
        for _ in range(70):
            run.step({})
        self.assertGreater(bot.position[2], before + 1)
        self.assertIs(bot.impulse_mover, mover)
        self.assertEqual(self.health(run, bot).total, health)

    def test_concussion_displaces_sniper_during_authored_reposition(self):
        definition = next(row for row in self.catalog.values() if row.name == "Sniper")
        for behind_wall in (False, True):
            with self.subTest(behind_wall=behind_wall):
                run, pharah, bot = self.create(definition, distance=2)
                match = run.match
                fight = match.combat
                if behind_wall:
                    match.collision_world = World(FLOOR + box(-6.2, -6, -5, 15, -20, 20))
                    pharah.mover.use(world=match.collision_world)
                before_health = self.health(run, bot).total
                script = pharah.body_script
                script.ult_seconds = 0
                script.set_ult_charge(0)
                self.assertEqual(npc_control.reposition(match, pharah), bot.entity)
                instance = sniper_reposition.instance_for(bot)
                for _ in range(110):
                    second = sniper_reposition.state_at(instance, 94)
                    if second is not None and second.active and instance.component.now >= second.start + 300:
                        break
                    run.step({})
                else:
                    self.fail("Sniper never entered the second wait")
                facing = self.aim_at_body(run, pharah, bot)
                run.step({pharah: (*facing, 16)})
                state = self.wait_for_sniper_motion(run, bot)
                destination, _ = state.move_request()
                mover = bot.impulse_mover
                for _ in range(20):
                    run.step({})
                    if mover.external_impulse:
                        break
                self.assertTrue(state.active)
                self.assertTrue(mover.external_impulse)
                self.assertFalse(fight.rockets)
                self.assertGreater(bot.velocity[2], 10)
                self.assertLess(bot.velocity[0], -25)
                self.assertEqual(bot.velocity[1], 5)
                contact = bot.position
                run.step({})
                self.assertTrue(state.active)
                self.assertGreater(bot.position[1], contact[1])
                self.assertLess(bot.position[0] - contact[0], -0.4)
                furthest = bot.position[0]
                for _ in range(180):
                    if not state.active:
                        break
                    run.step({})
                    furthest = min(furthest, bot.position[0])
                if behind_wall:
                    radius, _ = mover._capsule()
                    self.assertAlmostEqual(furthest, -6 + radius, delta=.04)
                self.assertFalse(state.active)
                self.assertFalse(mover.external_impulse)
                self.assertIs(bot.impulse_mover, mover)
                self.assert_sniper_arrived(bot, destination)
                self.assertEqual(self.health(run, bot).total, before_health)
                self.assertEqual(pharah.body_script.component.entity_var(342).value(), 0)

    def test_sniper_capsule_stops_on_low_obstacle_during_a_batched_update(self):
        definition = next(row for row in self.catalog.values() if row.name == 'Sniper')
        run, pharah, bot = self.create(definition, distance=2)
        match = run.match
        y = pharah.mover.position[1]
        self.assertEqual(npc_control.reposition(match, pharah), bot.entity)
        state = self.wait_for_sniper_motion(run, bot)
        destination, _ = state.move_request()
        dx, dz = destination.x - bot.position[0], destination.z - bot.position[2]
        if abs(dx) >= abs(dz):
            midpoint = (bot.position[0] + destination.x) / 2
            obstacle = box(midpoint - .1, midpoint + .1, y, y + 1.05, -8, 8)
        else:
            midpoint = (bot.position[2] + destination.z) / 2
            obstacle = box(-8, 8, y, y + 1.05, midpoint - .1, midpoint + .1)
        match.collision_world = World(FLOOR + obstacle)
        bot.impulse_mover.use(world=match.collision_world)
        origin = tuple(bot.position)
        start = run.frame
        run.frame += 35
        match.update(run.frame * .016, run.frame, frames=False)
        self.assertFalse(state.active)
        self.assertGreater(state.ended_at, start * 16)
        self.assertLessEqual(state.ended_at, run.frame * 16)
        self.assertGreater(math.dist(bot.position, origin), 0)
        self.assertGreater(math.dist(bot.position, destination), 0.5)
        self.assertEqual(bot.velocity, (0, 0, 0))

    def test_sniper_retries_blocked_search_and_stop_cancels_windup(self):
        definition = next(row for row in self.catalog.values() if row.name == "Sniper")
        run, pharah, bot = self.create(definition, distance=2)
        match = run.match
        clear = match.collision_world
        y = bot.position[1]
        enclosure = (box(-1.2, -1, y, y + 3, 0, 3.2)
                     + box(1, 1.2, y, y + 3, 0, 3.2)
                     + box(-1.2, 1.2, y, y + 3, 3, 3.2))
        match.collision_world = World(FLOOR + enclosure)
        pharah.mover.use(world=match.collision_world)
        origin = tuple(bot.position)
        self.assertEqual(npc_control.reposition(match, pharah), bot.entity)
        instance = sniper_reposition.instance_for(bot)
        for _ in range(65):
            run.step({})
            self.assertEqual(tuple(bot.position), origin)
        search = sniper_reposition.state_at(instance, 39)
        retry = sniper_reposition.state_at(instance, 41)
        self.assertTrue(search.active)
        self.assertGreater(retry.counter, 1)
        self.assertFalse(sniper_reposition.moving(bot))
        self.assertIsNone(instance.context().var(expr.INSTANCE, 1220))

        match.collision_world = clear
        pharah.mover.use(world=clear)
        for _ in range(25):
            run.step({})
            windup = sniper_reposition.state_at(instance, 96)
            if windup is not None and windup.active:
                break
        else:
            self.fail("Sniper did not retry after a path opened")
        self.assertFalse(search.active)
        self.assertEqual(npc_control.stop(match), 1)
        self.assertFalse(sniper_reposition.busy(bot))
        self.assertFalse(windup.active)
        for _ in range(60):
            run.step({})
        self.assertEqual(tuple(bot.position), origin)
        move = sniper_reposition.move_state(bot)
        self.assertTrue(move is None or move.counter == 0)

    def test_sniper_stop_cancels_travel_and_recast_opens_at_cooldown_boundary(self):
        definition = next(row for row in self.catalog.values() if row.name == 'Sniper')
        run, pharah, bot = self.create(definition, distance=2)
        match = run.match
        self.assertEqual(npc_control.reposition(match, pharah), bot.entity)
        state = self.wait_for_sniper_motion(run, bot)
        run.step({})
        self.assertEqual(npc_control.stop(match), 1)
        self.assertFalse(state.active)
        stopped = tuple(bot.position)
        pharah.mover.position[0] = stopped[0]
        pharah.mover.position[2] = stopped[2] - 2
        pharah.mover.body = None
        recovery = sniper_reposition.state_at(state.instance, 9)
        component = state.instance.component
        deadline = recovery.start + 750
        while component.now < deadline:
            self.assertFalse(sniper_reposition.request(match, bot, pharah))
            run.step({})
            self.assertEqual(tuple(bot.position), stopped)
        self.assertLess(component.now - deadline, component.frame_ms)
        cooldown = sniper_reposition.state_at(state.instance, 73)
        self.assertTrue(cooldown.active)
        self.assertEqual(cooldown.start, deadline)
        self.assertEqual(cooldown.ms, 10000)
        deadline = cooldown.start + cooldown.ms
        while component.now < deadline:
            self.assertFalse(sniper_reposition.request(match, bot, pharah))
            run.step({})
        self.assertEqual(tuple(bot.position), stopped)
        self.assertLess(component.now - deadline, component.frame_ms)
        self.assertTrue(sniper_reposition.request(match, bot, pharah))
        self.assertIs(self.wait_for_sniper_motion(run, bot), state)
        self.assertEqual(state.counter, 2)

    def test_lethal_pharah_rocket_ends_sniper_reposition_without_late_motion(self):
        for wait_ms, moves in ((420, 0), (440, 1)):
            with self.subTest(move_started=bool(moves)):
                self.lethal_rocket_during_sniper_reposition(wait_ms, moves)

    def lethal_rocket_during_sniper_reposition(self, wait_ms, moves):
        definition = next(row for row in self.catalog.values() if row.name == 'Sniper')
        run, pharah, bot = self.create(definition, distance=2)
        match = run.match
        self.assertEqual(npc_control.reposition(match, pharah), bot.entity)
        instance = sniper_reposition.instance_for(bot)
        for _ in range(115):
            wait = sniper_reposition.state_at(instance, 94)
            if wait is not None and wait.active and instance.component.now >= wait.start + wait_ms:
                break
            run.step({})
        else:
            self.fail("Sniper never entered its second wait")
        enemy = self.health(run, bot)
        enemy.damage(enemy.total - 1)
        direction = self.aim_at_body(run, pharah, bot)
        run.step({pharah: (*direction, FIRE)})
        for _ in range(40):
            run.step({})
            if enemy.dead:
                break
        self.assertTrue(enemy.dead)
        state = sniper_reposition.state_at(instance, 16)
        self.assertTrue(state is None or not state.active)
        self.assertEqual(state.counter if state is not None else 0, moves)
        position = tuple(bot.position)
        self.assertFalse(sniper_reposition.busy(bot))
        for _ in range(30):
            run.step({})
        self.assertEqual(tuple(bot.position), position)
        self.assertFalse(bot.attack_input)

    def test_removal_retires_both_namespaces_and_respawn_does_not_reuse_identity(self):
        run, player, bot = self.enforcer()
        script = bot.body_script
        components = (script.component, script.associated_component)
        old_ids = {c.entity for c in components}
        npc_control.start(run.match, player)
        self.assertEqual(npc.remove_spawned(run.match), (bot.entity,))
        self.assertTrue(script.retired)
        for component in components:
            self.assertIsNone(component.world.match)
            self.assertTrue(component.owner_dead)
            self.assertFalse(component.held)
            self.assertFalse(component.shots)
            self.assertFalse(component.instances)
            self.assertEqual(run.match.script_npc_peer(component.world), (None, None))
        self.assertIsNone(script.advance_live(run.frame + 100))
        self.assertEqual(script.advance_death(run.frame + 100, 10), [])
        fresh = npc.spawn(run.match, next(d for d in self.catalog.values() if d.name == 'Enforcer'),
                          (0, player.mover.position[1], 8), team=1)
        self.assertTrue(old_ids.isdisjoint({fresh.entity, fresh.body_script.associated_component.entity}))

    def test_passive_exports_preserve_independent_component_storage(self):
        run, _, bot = self.enforcer()
        body, associated = bot.body_script.component, bot.body_script.associated_component
        run.step({})
        with patch.object(type(next(iter(body.instances.values()))), 'evaluate',
                          side_effect=AssertionError('passive export evaluated script')):
            first_body = snapshot_digest(export_component_vm(body))
            first_ai = snapshot_digest(export_component_vm(associated))
            associated.entity_var(0x7FFE).base = 123
            self.assertEqual(snapshot_digest(export_component_vm(body)), first_body)
            self.assertNotEqual(snapshot_digest(export_component_vm(associated)), first_ai)

    def test_server_storage_includes_associated_hidden_state(self):
        import test_server_snapshot as server_tests

        from ow174.game.server_snapshot import snapshot_digest as server_digest

        fixture = server_tests.ServerSnapshotTests()
        session, _, players = fixture.scene()
        definition = next(d for d in self.catalog.values() if d.name == 'Enforcer')
        bot = npc.spawn(players[0].match, definition, (0, 0, 8), team=1)
        self.assertIsNotNone(bot.body_script)
        associated = bot.body_script.associated_component
        self.assertIsNotNone(associated)
        first = server_digest(fixture.capture(session))
        associated.entity_var(0x7FFE).base = 123
        self.assertNotEqual(server_digest(fixture.capture(session)), first)

    def test_server_storage_distinguishes_npc_melee_input_mode(self):
        import test_server_snapshot as server_tests

        from ow174.game.server_snapshot import snapshot_digest as server_digest

        fixture = server_tests.ServerSnapshotTests()
        session, _, players = fixture.scene()
        definition = next(d for d in self.catalog.values() if d.name == 'Trooper')
        bot = npc.spawn(players[0].match, definition, (0, 0, 8), team=1)
        baseline = server_digest(fixture.capture(session))
        bot.melee_input = True
        self.assertNotEqual(server_digest(fixture.capture(session)), baseline)
        bot.melee_input = False
        self.assertEqual(server_digest(fixture.capture(session)), baseline)

    def test_failed_replacement_retires_old_pair_and_leaves_no_partial_pair(self):
        run, _, bot = self.enforcer()
        old = bot.body_script
        actual = graph.entity_body

        def missing_association(definition):
            return None if definition == 0x040000000000227A else actual(definition)

        with patch.object(graph, "entity_body", side_effect=missing_association), self.assertLogs(
                "ow174.game", level="ERROR"):
            run.match._prepare_bot_script(bot)
        self.assertTrue(old.retired)
        self.assertIsNone(bot.body_script)
        self.assertEqual(run.match.script_npc_peer(old.component.world), (None, None))
        self.assertEqual(run.match.script_npc_peer(old.associated_component.world), (None, None))

    def test_actual_assassin_ai_callback_reaches_unimplemented_target_search(self):
        import test_script_ai_behavior as behavior_tests

        from ow174.game.script import ai_behavior, runtime, target_search

        definition = graph.graph(0x13C2)
        if definition is None:
            self.skipTest('Supply the complete five-associated-entity graph closure')
        # Check the actual extracted filter contract without supplying a target
        # or pretending that the engine's reference/relation producers exist.
        config = definition.node(4).field('m_filter')
        self.assertFalse(target_search.candidate_masks(config, 0x40000,
                         component97=False, relationship=target_search.Relationship(False, True)))
        self.assertTrue(target_search.candidate_masks(config, 0x40000,
                        component97=True, relationship=target_search.Relationship(False, True)))
        self.assertTrue(target_search.mask_matches(config, 0x40000,
                        relationship=target_search.Relationship(False, True)))
        self.assertFalse(target_search.mask_matches(config, 0x40000,
                         relationship=target_search.Relationship(False, False)))
        for bit in (8, 16, 32, 256):
            self.assertFalse(target_search.mask_matches(config, 0x40000 | bit,
                             relationship=target_search.Relationship(False, True)))
        world = behavior_tests.BehaviorWorld()
        component = runtime.Component(0xC0100000, world=world, strict=True)
        world.context = ai_behavior.Context(component)
        instance = component.create(definition, init=False)
        # Isolate the actual selected node/links from unrelated graph entries.
        instance.initialized = True
        state = instance.node_state(definition.node(1))
        state.begin()
        self.assertIsNone(world.context.current)
        state.timer(2)
        self.assertIs(world.context.current, state)
        with self.assertRaises(runtime.UnsupportedScriptError) as raised:
            component.run_now()
        diagnostic = raised.exception.diagnostic
        self.assertEqual((diagnostic['graph'], diagnostic['node'], diagnostic['feature']),
                         (0x13C2, 4, 'native target search'))
        self.assertIsNone(world.context.current)
