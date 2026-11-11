"""The real Match owner starts, advances, rejects and retires shared scene scripts."""

import dataclasses
import unittest
from types import SimpleNamespace
from unittest import mock

from ow174.game import bots, combat, junkenstein_ai, junkenstein_scene, npc, observers, statescript
from ow174.game.collision import RayHit, StaticCollisionProvider, flat_world
from ow174.game.content import JUNKENSTEIN_MAP, JUNKENSTEIN_MODES, PRACTICE_RANGE, SOLDIER
from ow174.game.match import Match, _rotation
from ow174.game.mover import Mover, mover_data
from ow174.game.script import entity_team, expr, runtime, scene, scene_host
from ow174.game.script.bot import DIED
from ow174.game.script.driver import DEFAULT_SLOT, OWNER_HEALTH, WEAPON_SLOTS, BodyScript
from ow174.game.server import GameServer
from ow174.game.server_snapshot import ServerSnapshotError, export_server_state
from tests.test_game_server import REAPER, pick
from tests.test_script_runtime_budget import graph, node, plug, setter
from tests.test_script_scene import manifest, placement
from tests.test_script_scene_host import A, ValueWorld, literal
from tests.test_script_startup import definition, spec

MAP = dataclasses.replace(PRACTICE_RANGE, name='shared script fixture',
                          map_guid=0x0800000000FFCC01, placeables=())


def host_for(match, model=None):
    world = ValueWorld(mode=match.game_map.mode_guid)
    world.map_guid = match.game_map.map_guid
    world.bind(A)
    host = scene_host.SceneHost(world, graph_loader={1: graph() if model is None else model}.get)
    host.add(A, definition(spec(1)))
    return host


class HomeClient:
    def __init__(self):
        self.home = []

    def go_home(self, now):
        self.home.append(now)


class MatchSceneHostTests(unittest.TestCase):
    def test_junkenstein_keeps_phase_list_scopes_off_the_client_wire(self):
        component = runtime.Component(runtime.GAME_MODE_ENTITY)
        model = scene_host.graph.graph(0x180A)
        phase_list = model.node(42)
        root = component.create(model, init=False)
        projection = observers.SceneBody(component)

        root.states[phase_list.state] = SimpleNamespace(active=True)
        row = component.create(model, parent=root, parent_state=phase_list.state, init=False)
        cinematic = scene_host.graph.graph(0x027F)
        phase = component.create(cinematic, parent=row, parent_state=model.node(41).state, init=False)
        sibling = component.create(model, init=False)

        snapshot = observers.snapshot(projection)
        self.assertIn(phase_list, model.remote_states())
        self.assertNotIn((root.id, phase_list.state), snapshot.states)
        self.assertEqual(snapshot.instances[phase.id], (cinematic.index, None))
        self.assertEqual(set(snapshot.instances), {root.id, phase.id, sibling.id})
        self.assertEqual(set(projection.graphs()), {root.id, phase.id, sibling.id})
        self.assertIs(component.instances[phase.id], phase)
        self.assertIsNotNone(observers.full_frame(projection, snapshot))
        self.assertIsNotNone(observers.acknowledged_delta(projection, projection.latest_live, snapshot))

    def test_junkenstein_mounts_boss_intro_on_the_player_owner_stream(self):
        mode = JUNKENSTEIN_MODES[0]
        host = junkenstein_scene.JunkensteinSceneHost(scene.SceneWorld(mode=mode))
        self.addCleanup(host.close)
        script = BodyScript(0x02E0000000000042, 0xA0000101, 0, mode=mode)

        host.mount_player_intro_receiver(script)

        intro, = (instance for instance in script.component.instances.values()
                  if instance.graph.index == junkenstein_scene.TEAM_BODY_GRAPH)
        receiver = intro.graph.node(33)
        snapshot = script.snapshot()
        self.assertEqual(snapshot.instances[intro.id], (intro.graph.index, None))
        self.assertTrue(snapshot.states[(intro.id, receiver.state)][1])
        self.assertNotIn((intro.id, intro.graph.node(43).state), snapshot.states)

    def test_mystery_swap_selects_authored_weighted_count_from_bound_controllers(self):
        mode = runtime.GAME_MODE_ENTITY
        world = scene.SceneWorld(mode=JUNKENSTEIN_MODES[0])
        world.bind(mode)
        controllers = (0xA0000100, 0xA0000200, 0xA0000300, 0xA0000400)
        for entity in controllers:
            world.bind(entity)
        world.session_entity_roster = lambda: (*controllers, 0xA0000500)
        host = scene_host.SceneHost(world)
        self.addCleanup(host.close)
        component = host.add(mode, definition())
        authored = scene_host.graph.graph(0x0C8C)
        model = graph([node(authored.node(pos).pos, authored.node(pos).cls, **authored.node(pos).fields)
                       for pos in (303, 304, 305, 307, 308, 309, 310)])
        model.index = authored.index
        for draw, count in ((0.0, 1), (70.0, 2), (95.0, 3)):
            with self.subTest(draw=draw):
                instance = component.create(model, init=False)
                with (mock.patch.object(component.rng, 'uniform', return_value=draw),
                      mock.patch.object(component.rng, 'randint', return_value=1)):
                    instance.run_node(model.node(303), None)
                self.assertEqual(instance.find_var(0, 32463).value(), count)
                expected = tuple(map(expr.Entity, (controllers[0], *controllers[5 - count:])))
                self.assertEqual(instance.find_var(0, 28428).value(), expected)

    def test_junkenstein_gate_publishes_its_authored_team_to_scene_filters(self):
        world = scene.SceneWorld(mode=JUNKENSTEIN_MODES[0])
        gate = 0x80000032
        world.bind(gate, definition=0x04000000000016F6)
        host = junkenstein_scene.JunkensteinSceneHost(world)
        self.addCleanup(host.close)
        host.bind_match(self.match(None), mock.Mock())
        component = host.add(gate, definition())
        authored = scene_host.graph.graph(0x0CA7).node(3)
        model = graph([node(3, authored.cls, **(authored.fields | {'m_outPlug': plug()}))])
        model.index = 0x0CA7
        instance = component.create(model, init=False)
        instance.run_node(model.node(3), None)

        self.assertEqual(host.gate_team, 2)
        present, bits = world.array_filter_inputs(gate)
        self.assertTrue(present)
        self.assertEqual(entity_team.index_from_filters(bits), 2)
        self.assertIs(world.script_team_setter(A, 1), NotImplemented)

    def test_junkenstein_mode_actions_queue_one_per_acknowledged_frame(self):
        mode = runtime.GAME_MODE_ENTITY
        world = scene.SceneWorld(mode=JUNKENSTEIN_MODES[1])
        world.bind(mode)
        host = junkenstein_scene.JunkensteinSceneHost(world)
        self.addCleanup(host.close)
        component = host.add(mode, definition())
        model = scene_host.graph.graph(0x180A)
        instance = component.create(model, init=False)
        wave_model = scene_host.graph.graph(0x0CB3)
        wave = component.create(wave_model, init=False)
        root_model = scene_host.graph.graph(0x0C8C)
        root = component.create(root_model, init=False)
        host.replicate_mode = True
        host.mode_projection = observers.SceneBody(component)
        stream = statescript.Stream(mode)
        stream.last = stream.data_last = 1
        client = mock.Mock(player=SimpleNamespace(mode_script=stream), scene_mode_writable=False)
        def queue_frame(frame, full):
            return stream.reliable_frames(frame, 500, full=full)

        client.queue_scene_mode_frame.side_effect = queue_frame
        player = SimpleNamespace(spawned=True, gone=False, client=client)
        host.bind_match(SimpleNamespace(players=[player]), None)
        host.mode_views[client] = host.mode_projection.latest_live

        instance.run_node(model.node(47), None)
        with mock.patch.object(scene_host.SceneHost, 'advance', return_value=True):
            self.assertTrue(host.advance(0))
            client.queue_scene_mode_frame.assert_not_called()
            self.assertEqual(list(host.mode_actions[client]), [(instance.id, 47)])

            stream.arrived(1)
            client.scene_mode_writable = True
            self.assertTrue(host.advance(1))
            client.queue_scene_mode_frame.assert_called_once()
            frame = client.queue_scene_mode_frame.call_args.args[0]
            expected = statescript.remote_delta([], {}, {instance.id: model}, callout=(2, instance.id, 47))
            self.assertEqual((frame.count, frame.value), (expected.count, expected.value))
            self.assertFalse(client.queue_scene_mode_frame.call_args.kwargs['full'])

            stream.arrived(2)
            self.assertTrue(host.advance(2))
            client.queue_scene_mode_frame.assert_called_once()

            wave.run_node(wave_model.node(127), None)
            self.assertTrue(host.advance(3))
            expected = statescript.remote_delta(
                [], {}, {wave.id: wave_model}, callout=(3, wave.id, 127))
            frame = client.queue_scene_mode_frame.call_args.args[0]
            self.assertEqual((frame.count, frame.value), (expected.count, expected.value))

            stream.arrived(3)
            root.run_node(root_model.node(268), None)
            self.assertTrue(host.advance(4))
            expected = statescript.remote_delta([], {}, {root.id: root_model}, callout=(4, root.id, 268))
            frame = client.queue_scene_mode_frame.call_args.args[0]
            self.assertEqual((frame.count, frame.value), (expected.count, expected.value))

            stream.arrived(4)
            root.run_node(root_model.node(265), None)
            self.assertTrue(host.advance(5))
            expected = statescript.remote_delta([], {}, {root.id: root_model}, callout=(5, root.id, 265))
            frame = client.queue_scene_mode_frame.call_args.args[0]
            self.assertEqual((frame.count, frame.value), (expected.count, expected.value))

    def test_junkenstein_factory_starts_map_and_mode_through_match(self):
        roots = (0x0CC8, 0x0C8C)
        models = {index: graph() for index in roots}
        models[0x0CA5] = scene_host.graph.graph(0x0CA5)
        models[0x0CA2] = graph([node(22, 'STUStatescriptState', state=0)])
        models[0x0C90] = graph([node(11, 'STUStatescriptState', state=0)])
        for index, model in models.items():
            model.index = index
        world_data = manifest(placement(0, client_id=0))
        world_data['map'] = f'0x{JUNKENSTEIN_MAP:016X}'
        shared, primary = (f'0x{0x0580000000000000 | index:016X}' for index in roots)
        mode = {'pve': True, 'shared_graph': shared, 'graph': primary,
                'teams': {'0': {'controller': '0x0580000000000CA2', 'body': '0x0580000000000CA5'}}}
        configure_world = mock.Mock()
        configure_component = mock.Mock()
        match = self.match(lambda match: junkenstein_scene.create_host(
            match, map_data=world_data, definitions=(), configure_world=configure_world,
            configure_component=configure_component,
        ))
        match.game_map = dataclasses.replace(MAP, map_guid=JUNKENSTEIN_MAP, mode_guid=JUNKENSTEIN_MODES[0],
                                             placeables=(50,))
        with (mock.patch.object(junkenstein_scene.content, '_map_entries',
                                return_value={JUNKENSTEIN_MAP: {'first_entity': 50}}),
              mock.patch.object(junkenstein_scene.props, 'mode_identifiers', return_value=()),
              mock.patch.object(scene_host.graph, 'mode_scripts', return_value=mode),
              mock.patch.object(scene_host.graph, 'mode_variables', return_value={10274: True}),
              mock.patch.object(scene_host.graph, 'entity_script_component', return_value=definition()),
              mock.patch.object(scene_host.graph, 'graph', side_effect=models.get)):
            match.update(1.6, 100)

        host = match.scene_host
        self.assertFalse(match.ended)
        self.assertEqual(set(host.world.entities), {0x80000032, runtime.GAME_MODE_ENTITY})
        owner = host.units[runtime.GAME_MODE_ENTITY].component
        self.assertEqual(tuple(item.graph.index for item in owner.instances.values()), roots)
        self.assertEqual(owner.entity_var(10274).value(), True)
        self.assertIn(owner.entity, host.world.play_queues)
        messages = host.world.message_state_context(owner.entity, component=owner)
        self.assertIs(messages.host, host.messages)
        priority = messages.priority(None, 0x0C40000000000003)
        self.assertEqual(priority.value, 10)
        self.assertIs(priority.prefer_later, False)
        self.assertEqual(host.world.voice_set(owner.entity), 0x07A000000000001D)
        self.assertEqual(host.world.mode_score_threshold_row()[0][0], 3000.0)
        self.assertEqual(host.world.array_filter_inputs(owner.entity), (True, 0x80000))
        self.assertIsNone(host.world.voice_category_callback)
        self.assertTrue(callable(host.world.hero_creator))
        configure_world.assert_called_once_with(match, host)
        configure_component.assert_called_once_with(host, owner)

        player = match.add_player(1, 'First', SOLDIER, 0, False)
        other = match.add_player(2, 'Second', SOLDIER, 0, False)
        match.update(1.616, 101)
        binding = host.player_bindings[player.entity]
        self.assertIsNone(binding.sender)
        phase_two = (0x0C8C, 125, 0x02400000000006DE)
        self.assertEqual(host.world.mission_message_route(*phase_two), ())
        match.game_message(player, pick(player.entity, SOLDIER), 1.616)
        match.update(1.632, 102)
        body, sender = binding.body, binding.sender
        self.assertEqual(body, player.body)
        self.assertEqual(host.world.mission_message_route(*phase_two), (expr.Entity(body),))
        self.assertEqual(host.world.mission_message_route(0x0CC0, 38, 0x02400000000014D4),
                         (expr.Entity(player.entity), expr.Entity(other.entity)))
        for pos in (52, 53, 54):
            self.assertEqual(
                host.world.mission_message_route(0x0CE0, pos, phase_two[2]), (expr.Entity(body),),
            )
        self.assertTrue(sender.state(0).active)
        self.assertEqual(sender.find_var(runtime.INSTANCE, 2114).value(), expr.Asset(SOLDIER))
        authored = scene_host.graph.graph(0x01C9).node(60)
        hook_action = graph([node(60, authored.cls, **(authored.fields | {'m_outPlug': plug()}))])
        hook_action.index = 0x01C9
        victim_vm = host.units[body].component
        victim_graph = victim_vm.create(hook_action, init=False)
        victim_graph.initialized = True
        victim_graph.run_node(hook_action.node(60), None)
        backend = host.world.property_modifiers(body)
        self.assertEqual(backend.resolve(32, 2.0, frame=102), (False, 2.0))
        self.assertEqual(backend.resolve(32, 2.0, frame=103), (True, 1.0))
        self.assertEqual(backend.resolve(32, 0.5, frame=103), (True, 0.5))
        self.assertEqual(backend.resolve(32, 2.0, frame=104), (False, 2.0))
        victim_vm.destroy(victim_graph)
        player.mover.velocity = (1.0, -2.0, 3.0)
        sender.write((runtime.INSTANCE, 60000), expr.Entity(body))
        velocity = {'$': 'STU_309B0570', 'm_entity': {'$': 'STUConfigVarDynamic', 'm_identifier': 60000}}
        self.assertEqual(sender.evaluate(velocity), expr.Vec3(1.0, -2.0, 3.0))

        # Exercise the authored recipient query, send and receive together.
        enemy = 0xC0000100
        x, y, z = player.mover.position
        player.mover.yaw = 0
        host.world.bind(enemy, position=(x, y, z + 4), target_tags=())
        enemy_vm = host._start_live_component(enemy, definition())
        host.world.set_entity_team_context(enemy, entity_team.Context(
            enemy, True, True, True,
            lookup=lambda entity: entity_team.Target(entity, entity_team.FilterBits(player.team_bit << 1)),
        ))
        source = enemy_vm.create(scene_host.graph.graph(0x14B1), init=False)
        source.initialized = True
        source.run_node(source.graph.node(53), None)
        self.assertEqual(source.find_var(runtime.INSTANCE, 54).value(), (expr.Entity(body),))
        receiver, = host.units[body].component.instances.values()
        receiver.component.run_now()
        self.assertEqual(receiver.find_var(runtime.INSTANCE, 40).value(), expr.Entity(enemy))
        self.assertEqual(receiver.find_var(runtime.INSTANCE, 47).value(), (1,))
        self.assertEqual(receiver.find_var(runtime.INSTANCE, 536).value(), (expr.Entity(enemy),))
        self.assertEqual(receiver.find_var(runtime.INSTANCE, 1725).value(), 0)
        selector = source.graph.node(53).field('m_value')

        match.game_message(player, pick(player.entity, REAPER), 1.632)
        match.update(1.648, 103)
        self.assertNotIn(body, host.world.entities)
        self.assertNotIn(body, host.world.team_contexts)
        self.assertIs(binding.sender, sender)
        self.assertEqual(host.world.current_possessions[player.entity], player.body)
        self.assertEqual(sender.find_var(runtime.INSTANCE, 2114).value(), expr.Asset(REAPER))
        self.assertEqual(source.evaluate(selector), (expr.Entity(player.body),))
        match.leave(player)
        match.update(1.664, 104)
        self.assertNotIn(player.entity, host.world.entities)
        self.assertNotIn(player.body, host.world.entities)
        self.assertEqual(host.world.mission_message_route(*phase_two), ())
        self.assertEqual(host.world.mission_message_route(0x0CC0, 38, 0x02400000000014D4),
                         (expr.Entity(other.entity),))
        self.assertEqual(source.evaluate(selector), ())
        host.retire_entity(enemy)
        match.leave(other)
        self.assertTrue(host.closed)
        self.assertFalse(host.player_bindings)

    def test_roadhog_hook_queries_distinguish_wall_hits_and_target_capture(self):
        world = ValueWorld(mode=JUNKENSTEIN_MODES[0])
        host = junkenstein_scene.JunkensteinSceneHost(world)
        floor = mock.Mock()
        fight = mock.Mock()
        host.match = SimpleNamespace(collision_world=floor, combat=fight)
        world.roadhog_hook_cast = host._roadhog_hook_cast
        world.roadhog_hook_targets = host._roadhog_hook_targets
        hero = junkenstein_ai.ROADHOG
        pose = SimpleNamespace(yaw=0, pitch=0)
        world.bind(A, hero=hero, position=(0, 0, 0), movement_reader=lambda _: pose, scale_reader=lambda: 1.0)
        component = host.add(A, definition())
        controller = A + 1
        world.bind(controller)
        world.set_possessions(possessors=(controller,), primary=((A, controller),))
        world.set_linked_controller(A, controller)
        world.set_current_possession(controller, A)
        world.bind_ai_behavior(controller)
        actor = SimpleNamespace(entity=A, hero=hero, position=(0, 0, 0))
        host.bind_combatant(actor, mock.Mock())

        hook = scene_host.graph.graph(0x01C8)
        ray, wall, targets = (hook.node(pos) for pos in (100, 137, 17))
        model = graph([
            node(100, ray.cls, **(ray.fields | {'m_outPlug': plug()})),
            node(137, wall.cls, state=0, **(wall.fields | {'m_subgraphPlug': None})),
            node(17, targets.cls, state=1, **(targets.fields | {'m_subgraphPlug': None})),
        ])
        model.index = 0x01C8
        instance = component.create(model, init=False)
        instance.initialized = True

        target = combat.Target(A + 2, 0, (0, 0, 2), 2.0, 0.4, 1.6, None)
        fight._enemies.return_value = [target]
        floor.cast_ray.return_value = None
        instance.run_node(model.node(100), None)
        self.assertIs(instance.find_var(runtime.INSTANCE, 2413).value(), True)
        self.assertEqual(instance.find_var(runtime.INSTANCE, 3094).value(), expr.Entity(target.entity))
        self.assertAlmostEqual(instance.find_var(runtime.INSTANCE, 431).value().z, 1.6)
        self.assertLess(instance.find_var(runtime.INSTANCE, 434).value().z, 0)

        floor.cast_ray.return_value = RayHit(1, (0, combat.EYE, 1), (0, 0, -1), 1, 0)
        instance.run_node(model.node(100), None)
        self.assertIsNone(instance.find_var(runtime.INSTANCE, 3094).value())
        self.assertEqual(instance.find_var(runtime.INSTANCE, 431).value().z, 1)

        floor.cast_ray.return_value = None
        fight._enemies.return_value = []
        instance.run_node(model.node(100), None)
        self.assertIs(instance.find_var(runtime.INSTANCE, 2413).value(), False)
        self.assertIsNone(instance.find_var(runtime.INSTANCE, 431).value())

        world.bind(target.entity, position=target.feet, definition=0x0400000000001424,
                   rotation_reader=lambda: _rotation(16384))
        fight._enemies.return_value = [target]
        tip = expr.Vec3(0, combat.EYE, 2)
        instance.write((runtime.INSTANCE, 431), tip)
        instance.state(0).begin()
        instance.state(1).begin()
        self.assertIs(instance.find_var(runtime.INSTANCE, 2413).value(), False)
        self.assertEqual(instance.find_var(runtime.INSTANCE, 432).value(), tip)
        self.assertEqual(instance.find_var(runtime.INSTANCE, 375).value(), (expr.Entity(target.entity),))
        chest = instance.evaluate(hook.node(15).field('m_value'))
        self.assertAlmostEqual(chest.x, -0.0033355807)
        self.assertAlmostEqual(chest.y, 1.0662696)
        self.assertAlmostEqual(chest.z, 2)

        return_anchor = instance.evaluate(hook.node(83).field('m_value.m_configVars')[0])
        self.assertAlmostEqual(return_anchor.x, 1.1662592)
        self.assertAlmostEqual(return_anchor.y, 0.99586946)
        self.assertAlmostEqual(return_anchor.z, -0.016212437)

        floor.cast_ray.return_value = RayHit(1, (0, combat.EYE, 1), (0, 0, -1), 1, 0)
        instance.state(0).tick()
        instance.state(1).tick()
        self.assertIs(instance.find_var(runtime.INSTANCE, 2413).value(), True)
        self.assertIsNone(instance.find_var(runtime.INSTANCE, 38).value())
        self.assertEqual(instance.find_var(runtime.INSTANCE, 432).value().z, 1)
        self.assertEqual(instance.find_var(runtime.INSTANCE, 375).value(), ())

    def test_symmetra_beam_tracks_a_wider_target_and_respects_world_occlusion(self):
        world = ValueWorld(mode=JUNKENSTEIN_MODES[0])
        host = junkenstein_scene.JunkensteinSceneHost(world)
        floor = mock.Mock()
        floor.cast_ray.return_value = None
        fight = mock.Mock()
        host.match = SimpleNamespace(collision_world=floor, combat=fight)
        world.symmetra_beam_cast = host._symmetra_beam_cast
        hero = 0x02E0000000000016
        pose = SimpleNamespace(yaw=0, pitch=0)
        world.bind(A, hero=hero, position=(0, 0, 0), movement_reader=lambda _: pose)
        component = host.add(A, definition())
        controller = A + 2
        world.bind(controller)
        world.set_possessions(possessors=(controller,), primary=((A, controller),))
        world.set_linked_controller(A, controller)
        world.set_current_possession(controller, A)
        world.bind_ai_behavior(controller)
        host.bind_combatant(SimpleNamespace(entity=A, hero=hero), mock.Mock())
        target = combat.Target(A + 1, 0, (0.5, 0, 3), 2.0, 0.4, 1.6, None)
        world.bind(target.entity)
        world.array_filter_inputs = lambda _: (True, 1)
        fight._enemies.return_value = [target]

        authored = scene_host.graph.graph(0x157C)
        model = graph([
            node(ray.pos, ray.cls, state=i, **(ray.fields | {'m_subgraphPlug': None}))
            for i, ray in enumerate(authored.node(pos) for pos in (202, 203))
        ])
        model.index = authored.index
        instance = component.create(model, init=False)
        instance.initialized = True
        instance.write((runtime.INSTANCE, 1648), 7.75)
        instance.state(0).begin()
        instance.state(1).begin()
        self.assertIsNone(instance.find_var(runtime.INSTANCE, 16627).value())
        self.assertEqual(instance.find_var(runtime.INSTANCE, 2815).value(), expr.Entity(target.entity))
        self.assertIs(instance.find_var(runtime.INSTANCE, 23531).value(), True)

        floor.cast_ray.return_value = RayHit(1, (0, combat.EYE, 1), (0, 0, -1), 1, 0)
        instance.state(1).tick()
        self.assertIsNone(instance.find_var(runtime.INSTANCE, 2815).value())
        self.assertIs(instance.find_var(runtime.INSTANCE, 23531).value(), True)

    def test_junkenstein_symmetra_beam_applies_damage_only_while_a_live_enemy_is_selected(self):
        world = scene.SceneWorld(mode=JUNKENSTEIN_MODES[0])
        host = junkenstein_scene.JunkensteinSceneHost(world)
        self.addCleanup(host.close)
        match = self.match(None)
        match.game_map = dataclasses.replace(MAP, map_guid=JUNKENSTEIN_MAP, mode_guid=JUNKENSTEIN_MODES[0])
        match.scene_host = host
        host.bind_match(match, mock.Mock())
        player = match.add_player(1, 'Defender', SOLDIER, 0, False)
        player.has_body = player.spawned = True
        player.client = mock.Mock()
        target, = match.combat._targets()
        vitals = match.combat.vitals_of(target)
        initial = vitals.total

        hero, controller = 0x02E0000000000016, A + 1
        world.bind(A, hero=hero, definition=0x04000000000026F0)
        on_fire = mock.Mock(return_value=None)
        world.bind(controller, on_fire_reader=on_fire)
        world.set_possessions(possessors=(controller,), primary=((A, controller),))
        world.set_linked_controller(A, controller)
        world.set_current_possession(controller, A)
        world.bind_ai_behavior(controller)
        component = host.add(A, definition())
        shooter = mock.Mock(spec=bots.Bot, entity=A, hero=hero, team=1, dead=False)
        host.bind_combatant(shooter, mock.Mock())

        authored = scene_host.graph.graph(0x157C)
        model = graph([
            node(source.pos, source.cls, state=index, **source.fields) | {'server_only': True}
            for index, source in enumerate(authored.node(pos) for pos in (170, 200))
        ])
        model.index = 0x157C
        weapon = component.create(model, init=False)
        weapon.initialized = True
        weapon.write((runtime.INSTANCE, 499), 8.0)
        weapon.write((runtime.INSTANCE, 536), expr.Entity(player.body))
        weapon.state(0).begin()
        criteria_aura = weapon.state(1)
        criteria_aura.begin()
        self.assertTrue(criteria_aura.active)
        self.assertFalse(criteria_aura.children)
        self.assertEqual(vitals.total, initial)
        component.run_frame(0)
        component.run_frame(1)
        self.assertAlmostEqual(vitals.total, initial - 8.0 * component.now / 1000, places=5)

        weapon.write((runtime.INSTANCE, 536), None)
        after_contact = vitals.total
        component.run_frame(2)
        self.assertEqual(vitals.total, after_contact)
        weapon.write((runtime.INSTANCE, 536), expr.Entity(player.body))
        player.team = 1
        component.run_frame(3)
        self.assertEqual(vitals.total, after_contact)
        player.team = 0
        shooter.dead = True
        component.run_frame(4)
        self.assertEqual(vitals.total, after_contact)
        weapon.write((runtime.ENTITY, 7059), True)
        self.assertIs(world.target_effects(criteria_aura), True)
        on_fire.return_value = False
        self.assertIs(world.target_effects(criteria_aura), NotImplemented)

    def test_summoner_confirmation_reads_the_authored_message_vectors(self):
        world = scene.SceneWorld(mode=JUNKENSTEIN_MODES[0])
        host = scene_host.SceneHost(world)
        self.addCleanup(host.close)
        controller = A + 1
        world.bind(A, definition=0x04000000000026F0)
        world.bind(controller)
        world.set_possessions(possessors=(controller,), primary=((A, controller),))
        world.set_linked_controller(A, controller)
        world.set_current_possession(controller, A)
        world.bind_ai_behavior(controller)
        owner = host.add(A, definition())
        authored = scene_host.graph.graph(0x156B).node(57)
        fields = {**authored.fields, 'm_subgraphPlug': None, 'm_F6955E81': None, 'm_425337C6': None}
        model = graph([node(57, authored.cls, state=0, **fields)])
        model.index = 0x156B
        instance = owner.create(model, init=False)
        instance.initialized = True
        placement = instance.state(0)
        location = expr.Vec3(83.0, 12.0, -85.0)
        axis = expr.Vec3(0.0, 0.0, -1.0)
        for variable, value in ((17098, True), (17096, (location,)), (17097, (axis,))):
            instance.write((runtime.INSTANCE, variable), value)
        placement.begin()
        placement.plug_enter('m_6647ED22')
        self.assertEqual(placement.payload(), {'m_B4802CE3': 1})
        placement.tick()
        self.assertEqual(placement.payload(), {'m_B4802CE3': 0})
        self.assertEqual(instance.find_var(runtime.INSTANCE, 2478).value(), location)
        self.assertEqual(instance.find_var(runtime.INSTANCE, 1883).value(), expr.Entity(0))
        self.assertIs(instance.find_var(runtime.INSTANCE, 1002).value(), True)
        instance.write((runtime.INSTANCE, 17097), (axis, axis))
        placement.plug_enter('m_6647ED22')
        placement.tick()
        self.assertIs(instance.find_var(runtime.INSTANCE, 1002).value(), False)

    def test_junkenstein_shield_recharge_only_restores_the_bound_living_pool(self):
        world = scene.SceneWorld(mode=JUNKENSTEIN_MODES[0])
        host = junkenstein_scene.JunkensteinSceneHost(world)
        self.addCleanup(host.close)
        match = self.match(None)
        match.scene_host = host
        host.bind_match(match, mock.Mock())
        hero, controller = 0x02E0000000000016, A + 1
        vitals = combat.Vitals(A, 100, armour=25, shields=125)
        match.combat.vitals[A] = vitals
        world.bind(A, hero=hero, health_pool=vitals.pool_current, pool_capacity=vitals.most.get)
        world.bind(controller)
        world.set_possessions(possessors=(controller,), primary=((A, controller),))
        world.set_linked_controller(A, controller)
        world.set_current_possession(controller, A)
        world.bind_ai_behavior(controller)
        component = host.add(A, definition())
        actor = mock.Mock(spec=bots.Bot, entity=A, hero=hero)
        host.bind_combatant(actor, mock.Mock())
        source = scene_host.graph.graph(0x0033).node(5)
        model = graph([node(5, source.cls, state=0, **(source.fields | {'m_subgraphPlug': None}))])
        model.index = 0x0033
        instance = component.create(model, init=False)
        instance.initialized = True
        instance.write((runtime.ENTITY, 21869), 1)
        pools = combat.HEALTH_POOL, combat.ARMOUR_POOL, combat.SHIELDS_POOL
        vitals.now.update(zip(pools, (80, 10, 90)))
        recharge = instance.state(0)
        recharge.begin()
        self.assertTrue(world.modify_health(recharge, 1000))
        self.assertEqual(tuple(map(vitals.pool_current, pools)), (80, 10, 120))
        self.assertTrue(world.modify_health(recharge, 1000))
        self.assertEqual(tuple(map(vitals.pool_current, pools)), (80, 10, 125))
        vitals.now[combat.SHIELDS_POOL] = 0
        vitals.died_at = 2
        self.assertTrue(world.modify_health(recharge, 1000))
        self.assertEqual(vitals.pool_current(combat.SHIELDS_POOL), 0)

    def test_returning_junkrat_uses_its_own_wave_and_seen_flag(self):
        mode = runtime.GAME_MODE_ENTITY
        world = scene.SceneWorld(mode=JUNKENSTEIN_MODES[0])
        world.bind(mode)
        mode_vm = runtime.Component(mode, world=world)
        world.bind_script_component(mode, mode_vm)
        body = runtime.Component(A, world=world)
        model = scene_host.graph.graph(0x0CBC)
        wave = body.create(model, init=False)
        wave.states[5] = SimpleNamespace(active=True)
        mode_vm.set(8566, True)
        mode_vm.set(8521, True)
        self.assertFalse(junkenstein_ai.returning_boss_ready(world, junkenstein_ai.JUNKRAT, body))
        mode_vm.set(8522, True)
        self.assertTrue(junkenstein_ai.returning_boss_ready(world, junkenstein_ai.JUNKRAT, body))
        body.create(scene_host.graph.graph(0x0CE0), init=False)
        self.assertFalse(junkenstein_ai.returning_boss_ready(world, junkenstein_ai.JUNKRAT, body))

    def test_roadhog_ai_uses_hook_readiness_and_releases_its_input(self):
        world = ValueWorld(mode=JUNKENSTEIN_MODES[0])
        host = junkenstein_scene.JunkensteinSceneHost(world)
        floor = mock.Mock()
        floor.cast_ray.return_value = None
        fight = mock.Mock()
        fight._collision.return_value = floor
        fight.is_stunned.return_value = False
        host.match = SimpleNamespace(combat=fight)
        hero = junkenstein_ai.ROADHOG
        world.bind(A, hero=hero, position=(0, 0, 0), aim_writer=mock.Mock())
        world.bind_ai_control(A)
        vm = host.add(A, definition())
        controller = A + 1
        world.bind(controller)
        control = host.add(controller, definition())
        world.ai_weapon_slots[controller] = {0: (junkenstein_ai.BOSS_PRIMARY_IDENTIFIER,)}
        world.set_possessions(possessors=(controller,), primary=((A, controller),))
        world.set_linked_controller(A, controller)
        world.set_current_possession(controller, A)
        world.bind_ai_behavior(controller)

        def state(owner, index, position):
            authored = scene_host.graph.graph(index).node(position)
            fields = authored.fields | {'m_subgraphPlug': None}
            model = graph([node(position, authored.cls, state=0, **fields)])
            model.index = index
            instance = owner.create(model, init=False)
            instance.initialized = True
            result = instance.state(0)
            result.begin()
            return result

        state(vm, 0x08B6, 3)
        hook = state(vm, 0x01C8, 11)
        hook.instance.write((runtime.INSTANCE, 9570), True)
        state(vm, 0x01C6, 28)
        gate = state(control, 0x0BC6, 25)
        nav = SimpleNamespace(handoff_complete=False, search=None, path=(), drop_started=None)
        actor = SimpleNamespace(
            entity=A, hero=hero, position=(0, 0, 0), keys=False, dead=False, navigation=nav,
        )
        target = combat.Target(A + 2, 0, (0, 0, 12), 1.8, 0.4, 1.6, None)
        world.bind(target.entity)
        fight._enemies.return_value = [target]

        mode = runtime.GAME_MODE_ENTITY
        world.bind(mode)
        mode_vm = host.add(mode, definition())
        wave = scene_host.graph.graph(0x0CBC)
        gate_node = wave.states[4]
        returning = graph([node(index, 'STUStatescriptStateWait', state=index, m_timeout=literal(1))
                           for index in range(4)]
                          + [node(gate_node.pos, 'STUStatescriptStateWait', state=4, m_timeout=literal(1))])
        returning.index = wave.index
        wave_instance = vm.create(returning, init=False)
        wave_instance.initialized = True
        wave_instance.state(4).begin()
        with mock.patch.object(vm, 'edges') as edges:
            junkenstein_ai.update_boss_combat(host, actor, vm)
            self.assertFalse(nav.handoff_complete)
            edges.assert_not_called()
            mode_vm.set(8521, True)
            mode_vm.set(8566, True)
            junkenstein_ai.update_boss_combat(host, actor, vm)
            self.assertTrue(nav.handoff_complete)
            edges.assert_called_once_with([(35, True)])
            vm.held[:] = [35]
            hook.flags = hook.RUNNING
            edges.reset_mock()
            junkenstein_ai.update_boss_combat(host, actor, vm)
            edges.assert_called_once_with([(35, False)])
            self.assertEqual(world._lookup_ai_linked_target(controller, 0), target.entity)

            vm.held.clear()
            hook.flags = 0
            hook.cur = 5.0
            edges.reset_mock()
            junkenstein_ai.update_boss_combat(host, actor, vm)
            edges.assert_called_once_with([(29, True)])
            hook.cur = 0.0
            gate.end(False)
            edges.reset_mock()
            junkenstein_ai.update_boss_combat(host, actor, vm)
            edges.assert_called_once_with([(29, True)])

    def test_junkenstein_symmetra_weapon_uses_the_possessing_controllers_difficulty(self):
        world = scene.SceneWorld(mode=JUNKENSTEIN_MODES[1])
        body, controller = A, A + 1
        world.bind(body, definition=0x04000000000026F0)
        world.bind(controller, definition=0x0400000000001DD3, component92_present=True)
        world.set_possessions(possessors=(controller,), primary=((body, controller),))
        world.set_linked_controller(body, controller)
        world.set_current_possession(controller, body)
        component = runtime.Component(body, world=world, strict=True)
        world.bind_script_component(body, component)
        model = scene_host.graph.graph(0x157C)
        weapon = component.create(model, init=False)
        setter = model.node(60)
        damage = model.node(170).field('m_modifyHealth.m_amount')
        orb = model.node(207).field('m_value')
        weapon.write((runtime.INSTANCE, 176), 0.5)
        for card, beam_amount, orb_amount in ((0x43, expr.f32(3.2), 16.0), (0x44, 8.0, 40.0),
                                             (0x49, 12.0, 60.0), (0x45, 16.0, 80.0)):
            world.configure_junkenstein_card(0x0630000000000000 | card)
            weapon.run_node(setter, None)
            self.assertEqual(weapon.evaluate(damage), beam_amount)
            self.assertEqual(weapon.evaluate(orb), orb_amount * 0.5)

        world.set_current_possession(controller, None)
        lookup = setter.field('m_value.m_configVars')[1]
        self.assertEqual(weapon.evaluate(lookup, initial=99), 99)

    def test_junkenstein_roadhog_combat_filter_excludes_tagged_hits_and_clears_on_end(self):
        hero = 0x02E0000000000040
        authored = scene_host.graph.graph(0x0CBC).node(141)
        fields = {
            key: authored.field(key) for key in ('m_60703B1E', 'm_41B10FF2', 'm_5CFFCBAE', 'm_50530EAB')
        }
        model = graph([node(141, authored.cls, state=0, **fields)])
        model.index = 0x0CBC
        match = self.match(host_for)
        match.game_map = dataclasses.replace(MAP, map_guid=JUNKENSTEIN_MAP, mode_guid=JUNKENSTEIN_MODES[0])
        player = match.add_player(1, 'Defender', SOLDIER, 0, False)
        match.update(1.6, 100)
        host = match.scene_host
        enemy = A + 1
        host.world.bind(enemy, hero=hero, definition=0x040000000000028F)
        component = host._start_live_component(enemy, definition())
        instance = component.create(model, init=False)
        instance.initialized = True
        state = instance.state(0)
        shooter = mock.Mock(spec=bots.Bot, entity=enemy, hero=hero, team=1, dead=False,
                            position=(0, 0, 0), yaw=0, pitch=0)
        controller = enemy + 1
        host.world.bind(controller, definition=0x0400000000001959, component92_present=True)
        host.world.set_possessions(possessors=(controller,), primary=((enemy, controller),))
        host.world.set_linked_controller(enemy, controller)
        host.world.set_current_possession(controller, enemy)
        host.world.bind_ai_behavior(controller)
        host.bind_combatant(shooter, mock.Mock())
        weapon = component.create(scene_host.graph.graph(0x01C6), init=False)
        hook = component.create(scene_host.graph.graph(0x01C8), init=False)
        hook_damage = hook.graph.node(19)
        for card, primary, secondary, chain in ((0x3C, expr.f32(1.8000001), 10.0, 6.0),
                                                 (0x3D, 4.5, 25.0, 15.0),
                                                 (0x4A, 6.75, 37.5, 22.5), (0x3E, 9.0, 50.0, 30.0)):
            host.world.configure_junkenstein_card(0x0630000000000000 | card)
            for pos, expected in ((26, primary), (29, secondary)):
                volley = weapon.graph.node(pos)
                self.assertEqual(combat.hit_def(weapon.node_state(volley), volley.field('m_6396149F')).amount,
                                 expected)
            self.assertEqual(hook.evaluate(hook_damage.field('m_modifyHealth.m_amount')), chain)
        target = combat.Target(player.body, 0, (0, 0, 5), 1.8, 0.4, 1.6, player)
        fight = match.combat
        vitals = fight.vitals_of(target)
        cfg = {'m_modifyHealth': {'m_amount': {'$': 'STUConfigVarInt', 'm_value': 20}}}
        ordinary = combat.hit_def(state, cfg)
        cfg['m_modifyHealth']['m_148F3152'] = [{
            'm_EE3055BD': {'$': 'STUConfigVarIdentifier', 'm_identifier': '0x0D80000000003FCE'},
        }]
        excluded = combat.hit_def(state, cfg)
        self.assertEqual(excluded.tags, (0x0D80000000003FCE,))

        def hit(definition):
            before = vitals.total
            fight._hitscan(
                shooter, state, (0, 1, 0), (0, 0, 1), 10, ((target, target),), definition, 100, 100,
            )
            return before - vitals.total

        state.begin()
        self.assertAlmostEqual(hit(ordinary), 16.0)
        self.assertEqual(hit(excluded), 20.0)
        state.end(False)
        self.assertEqual(hit(ordinary), 20.0)
        self.assertFalse(host.world.combat_damage_filters)

        primary = weapon.node_state(weapon.graph.node(26))
        weapon.write((runtime.INSTANCE, 230), expr.f32(4.5))
        component.shots.append((primary, 0, 1600, (1, 0)))
        fight.consume_scene_boss_shots(shooter, component, 100)
        self.assertFalse(component.shots)
        self.assertEqual(len(fight.rockets), 25)
        self.assertEqual(len({rocket.direction for rocket in fight.rockets}), 25)
        self.assertTrue(all(rocket.direct.amount == 9 and rocket.speed == 80 for rocket in fight.rockets))
        fight.rockets.clear()

        secondary = weapon.node_state(weapon.graph.node(29))
        weapon.write((runtime.INSTANCE, 6368), 60)

        def launch_secondary():
            component.shots.append((secondary, 0, 1600, (1, 0)))
            fight.consume_scene_boss_shots(shooter, component, 100)
            projectile, = fight.rockets
            self.assertEqual(projectile.direct.amount, 50)
            return projectile

        with (mock.patch.object(fight, '_enemies', return_value=[target]),
              mock.patch.object(fight, '_damage') as damage):
            slug = launch_secondary()
            fight._fly_step(slug, slug.left)
        self.assertFalse(fight.rockets)
        damage.assert_called_once()
        self.assertEqual(damage.call_args.args[:3], (shooter, target, 50))

        with (mock.patch.object(fight, '_enemies', return_value=[]),
              mock.patch.object(combat, 'ray_world', return_value=3.0)):
            slug = launch_secondary()
            fight._fly_step(slug, slug.left)
        self.assertFalse(fight.rockets)

        with mock.patch.object(fight, '_enemies', return_value=[]):
            slug = launch_secondary()
            fight._fly_step(slug, slug.left)
        self.assertEqual(len(fight.rockets), 25)
        self.assertEqual(len({rocket.direction for rocket in fight.rockets}), 25)
        self.assertAlmostEqual(slug.position[2], 9.0, places=5)
        self.assertTrue(all(
            rocket.position == slug.position and rocket.direct.amount == 9 and rocket.speed == 80
            and rocket.split_on_expiry is None for rocket in fight.rockets
        ))

    def test_junkenstein_boss_uses_authored_body_pools_and_controller_override(self):
        hero = 0x02E0000000000002
        body_definition, controller_definition = 0x0400000000000022, 0x0400000000001958
        body_key, controller_key = (f'0x{value:016X}' for value in (body_definition, controller_definition))
        health = {'m_0D09D2D9': '0x0D800000000000C7', 'm_value': {'m_value': 250}}
        record = {'script_component': definition(), 'target_tags': [], 'pet_component_absent': True,
                  'manager': 0x0015, 'weapons': [None, 0x0019],
                  'graphs': [{'graph': 0x0033, 'm_1EB5A024': [health]}]}
        candidate = {'junkenstein': {
            'pairs': {f'0x{hero:016X}': {'body': body_key, 'controller': controller_key}},
            'entities': {body_key: record, controller_key: {
                'script_component': definition(),
                'ai_weapon_identifiers': {'0': [0x0D8000000000023A]},
            }},
        }}

        def factory(match):
            world = ValueWorld(mode=match.game_map.mode_guid)
            world.map_guid = match.game_map.map_guid
            world.ux_presenter_available = False
            world.set_possessions(possessors=(), primary=())
            host = junkenstein_scene.JunkensteinSceneHost(world)
            host.bind_match(match, lambda *_: None)
            junkenstein_ai.install_enemy_creator(match, host, (), lambda _: None)
            return host

        match = self.match(factory)
        match.game_map = dataclasses.replace(MAP, map_guid=JUNKENSTEIN_MAP, mode_guid=JUNKENSTEIN_MODES[0])
        match.card = 0x063000000000003C
        match.update(1.6, 100)
        host = match.scene_host
        create = host.world.hero_creator
        pool_schema = SimpleNamespace(fields={'m_publicSchema': {'m_entries': []}})
        manager, weapon = (scene_host.graph.graph(index) for index in (0x0015, 0x0019))
        assets = {0x0015: manager, 0x0019: weapon}
        with (mock.patch.object(scene_host.graph, '_data', return_value=candidate),
              mock.patch.object(scene_host.graph, 'graph', side_effect=lambda i: assets.get(i, pool_schema))):
            with self.assertRaisesRegex(ValueError, 'differs from the authored pair'):
                create(hero, (0, 0, 0), 1, 0, ())
            body = create(hero, (0, 0, 0), 1, 0, (), controller_definition=controller_definition,
                          skin_theme=junkenstein_ai.REAPER_ENCOUNTER_SKIN)
        self.assertTrue(body.pet_component_absent)
        self.assertEqual(match.combat.vitals[body.id].total, 250)
        self.assertIs(host.world.data_flow_context(body.id).component, host.units[body.id].component)
        self.assertIs(host.world.subgraph_scopes(body.id).component, host.units[body.id].component)
        controller = host.world.linked_controllers[body.id]
        self.assertEqual(host.world.entities[controller].definition, controller_definition)
        vm = host.units[body.id].component
        self.assertEqual(tuple(item.graph.index for item in vm.instances.values()), (0x0015, 0x0019))
        self.assertEqual(vm.entity_var(WEAPON_SLOTS).value(), (False, True))
        self.assertEqual(vm.entity_var(DEFAULT_SLOT).value(), 1)
        weapon_instance = next(item for item in vm.instances.values() if item.graph is weapon)
        self.assertEqual(weapon_instance.find_var(runtime.INSTANCE, 28).value(), 1)
        source = vm.create(graph(), init=False)
        self.assertEqual(source.evaluate({'$': 'STU_65ABFCCC', 'm_target': {'$': 'STU_B5A0CAF0'}}), 1)

        control_node = scene_host.graph.graph(0x0CE0).node(17)
        spawn = graph([node(17, control_node.cls, state=0,
                            m_7E42F868=control_node.field('m_7E42F868'))])
        spawn.index = 0x0CE0
        instance = vm.create(spawn, init=False)
        instance.initialized = True
        control = instance.state(0)
        model = host.world.entity_control_context(body.id).write_entity.__self__
        model.flags = 0x102
        control.begin()
        self.assertEqual(model.flags, 0x200000008102)
        control.end(False)
        self.assertEqual(model.flags, 0x102)
        vm.destroy(instance)

        marker = 0x800000C8
        target_yaw = 0
        host.world.bind(marker, rotation_reader=lambda: _rotation(target_yaw))
        authored = scene_host.graph.graph(0x0C95)
        facing = host.units[controller].component.create(authored, init=False)
        facing.initialized = True
        facing.write((runtime.INSTANCE, 8370), expr.Entity(marker))
        state = facing.node_state(authored.node(5))
        state.request(runtime.BEGIN)
        bot, = match.bots
        self.assertTrue(host.advance(101), host.error)
        self.assertEqual((bot.yaw, bot.moved_tick), (-32768, 101))
        self.assertEqual(body.movement_reader(101).yaw, bot.yaw)
        body.aim_writer(bot.yaw, 2048, 101)
        cinematic = vm.create(scene_host.graph.graph(0x0CE0), init=False)
        cinematic.initialized = True
        lock = cinematic.node_state(cinematic.graph.node(45))
        lock.begin()
        self.assertEqual(lock.aim, (-32768, 2048))
        target_yaw = 16384
        target = combat.Target(0xA0000200, 0, (-2, 0, 0), 2.5, 0.6, 2.2, None)
        host.world.bind(target.entity, position=target.feet)
        with mock.patch.object(match.combat, '_enemies', return_value=[target]):
            self.assertTrue(host.advance(102), host.error)
        self.assertIsNone(host.world._lookup_ai_linked_target(controller, 0))
        self.assertEqual((bot.yaw, bot.pitch, bot.moved_tick), (-32768, 2048, 101))
        lock.end(False)
        vm.destroy(cinematic)
        host._apply_spawn_facing(102)
        self.assertEqual((bot.yaw, bot.moved_tick), (-16384, 102))
        state.end(False)
        facing.component.destroy(facing)
        # 06AC can tear down the controller's facing graph before the next scene tick.
        handoff_graph = graph([node(4, 'STUStatescriptStateWait', state=0, m_timeout=literal(1))])
        handoff_graph.index = 0x0CE0
        handoff = vm.create(handoff_graph, init=False)
        handoff.initialized = True
        handoff.state(0).begin()
        handoff.state(0).end(True)
        target_yaw = 8192
        self.assertTrue(host.advance(103), host.error)
        self.assertEqual((bot.yaw, bot.moved_tick), (-16384, 102))
        self.assertTrue(bot.navigation.handoff_complete)
        vm.destroy(handoff)
        self.assertTrue(weapon_instance.initialized)
        self.assertEqual(weapon_instance.find_var(runtime.INSTANCE, 198).value(), 8)
        self.assertEqual(weapon_instance.find_var(runtime.INSTANCE, 53).value(), 8)

        # Inject the volley report. The scene resolves its difficulty, drains
        # the report and casts the authored pellet directions.
        shot = weapon_instance.node_state(weapon.node(10))
        hit_cfg = shot.node.field('m_6396149F')
        for card, amount in ((0x3D, 3.5), (0x4A, 5.25), (0x3E, 7.0), (0x3C, 1.75)):
            host.world.configure_junkenstein_card(0x0630000000000000 | card)
            self.assertEqual(combat.hit_def(shot, hit_cfg).amount, amount)
        vm.shots.append((shot, 0, 104 * vm.frame_ms, (1, 0)))
        with (mock.patch.object(match.combat, '_enemies', return_value=[target]),
              mock.patch.object(match.combat, '_hitscan', wraps=match.combat._hitscan) as cast,
              mock.patch.object(match.combat, '_damage') as damage):
            self.assertTrue(host.advance(104), host.error)
        self.assertEqual(damage.call_count, 20)
        self.assertEqual(len({call.args[3] for call in cast.call_args_list}), 20)
        self.assertTrue(all(call.args[1:4] == (target, 1.75, False) for call in damage.call_args_list))
        self.assertFalse(vm.shots)

        with mock.patch.object(match.combat, '_enemies', return_value=[target]):
            self.assertTrue(host.advance(105), host.error)
        self.assertEqual(host.world._lookup_ai_linked_target(controller, 0), target.entity)
        self.assertEqual(bot.yaw, -16384)

        fire_control = vm.create(scene_host.graph.graph(0x08B6), init=False)
        fire_control.initialized = True
        ordinary = fire_control.node_state(fire_control.graph.node(3))
        ordinary.begin()
        with (mock.patch.object(match.combat, '_enemies', return_value=[target]),
              mock.patch.object(vm, 'edges') as edges):
            junkenstein_ai.update_boss_combat(host, bot, vm)
            edges.assert_called_once_with([(20, True)])
        ordinary.end(False)
        with (mock.patch.object(match.combat, '_enemies', return_value=[target]),
              mock.patch.object(vm, 'edges') as edges):
            junkenstein_ai.update_boss_combat(host, bot, vm)
            edges.assert_not_called()
            vm.held.append(20)
            junkenstein_ai.update_boss_combat(host, bot, vm)
            edges.assert_called_once_with([(20, False)])
            vm.held.remove(20)
        vm.destroy(fire_control)

        self.assertTrue(host.advance(106), host.error)
        self.assertIsNone(host.world._lookup_ai_linked_target(controller, 0))
        distant = dataclasses.replace(target, entity=target.entity + 1, feet=(-21, 0, 0))
        host.world.bind(distant.entity, position=distant.feet)
        with mock.patch.object(match.combat, '_enemies', return_value=[distant]):
            for frame in range(107, 139):
                self.assertTrue(host.advance(frame), host.error)
        self.assertLess(bot.position[0], -0.9)
        self.assertEqual(host.world._lookup_ai_linked_target(controller, 0), distant.entity)
        host.retire_entity(distant.entity)

        voice_node = scene_host.graph.graph(0x0CE0).node(61)
        voice_graph = graph([node(61, voice_node.cls, state=0,
            m_stimulus=voice_node.field('m_stimulus'), m_targets=voice_node.field('m_targets'),
        )])
        voice_graph.index = 0x0CE0
        instance = vm.create(voice_graph, init=False)
        instance.initialized = True
        mode = runtime.GAME_MODE_ENTITY
        host.world.bind(mode)
        host.world.bind_voice_set(mode, junkenstein_ai.ENCOUNTER_VOICE_SET)
        listener = SimpleNamespace(
            spawned=True, gone=False, has_body=True, team=0, body=123, client=mock.Mock(),
        )
        voice = instance.state(0)
        voice.begin()
        context = host.world.voice_context(body.id)
        with mock.patch.object(match, 'players', [listener]):
            context.poll()
            vm.run_now()
            self.assertTrue(voice.active)
            self.assertAlmostEqual(voice.voice_duration, 3.337, places=4)
            self.assertTrue(voice.voice_submitted)
            vm.now += 3337
            context.poll()
            vm.run_now()
        self.assertFalse(voice.active)
        self.assertFalse(context.registered)
        listener.client.queue_combat_notice.assert_called_once()
        anchor, message, fields = listener.client.queue_combat_notice.call_args.args
        self.assertEqual((anchor, message), (body.id, 27500))
        self.assertEqual(fields['+0x78'], {'+0x0': mode})
        self.assertEqual(fields['+0x7C'], {'+0x0': listener.body})
        self.assertEqual((fields['+0x88'], fields['+0x90']), (0x0EE00000000006B5, 0x0760000000008CB1))

        body_voice = vm.create(scene_host.graph.graph(0x0CE0), init=False)
        body_voice.initialized = True
        cue = body_voice.node_state(body_voice.graph.node(77))
        cue.begin()
        listener.client.queue_combat_notice.reset_mock()
        with mock.patch.object(match, 'players', [listener]):
            context.poll()
            listener.client.queue_combat_notice.assert_not_called()
            vm.now += 201
            context.poll()
            vm.run_now()
        self.assertAlmostEqual(cue.voice_duration, 3.591, places=4)
        listener.client.queue_combat_notice.assert_called_once()
        anchor, message, fields = listener.client.queue_combat_notice.call_args.args
        self.assertEqual((anchor, message), (body.id, 27500))
        self.assertEqual(fields['+0x78'], {'+0x0': body.id})
        self.assertEqual((fields['+0x88'], fields['+0x90']), (0x0EE00000000006BD, 0x0760000000008A2B))

        handoff_node = body_voice.graph.node(2)
        handoff_message = 0x02400000000006AC
        route = host.world.routed_game_message_targets
        self.assertEqual(route(body_voice, handoff_node, handoff_message), ())
        controller_vm = host.units[controller].component
        receiver = controller_vm.create(scene_host.graph.graph(0x0CDC), init=False)
        receiver.initialized = True
        self.assertEqual(route(body_voice, handoff_node, handoff_message), (expr.Entity(controller),))
        controller_vm.destroy(receiver)
        self.assertEqual(route(body_voice, handoff_node, handoff_message), ())

        finishing_voice = vm.create(scene_host.graph.graph(0x0CE0), init=False)
        finishing_voice.initialized = True
        cue = finishing_voice.node_state(finishing_voice.graph.node(66))
        cue.begin()
        listener.client.queue_combat_notice.reset_mock()
        with mock.patch.object(match, 'players', [listener]):
            vm.now += 201
            context.poll()
            vm.run_now()
        listener.client.queue_combat_notice.assert_called_once()
        anchor, message, fields = listener.client.queue_combat_notice.call_args.args
        self.assertEqual((anchor, message), (body.id, 27500))
        self.assertEqual(fields['+0x78'], {'+0x0': mode})
        self.assertEqual(fields['+0x88'], 0x0EE00000000006D6)
        durations = {0x0760000000008CB2: 5.841, 0x0760000000008CB3: 5.220, 0x076000000000A45E: 6.729}
        self.assertAlmostEqual(cue.voice_duration, durations[fields['+0x90']], places=4)

        vm.destroy(finishing_voice)
        vm.destroy(body_voice)
        defeat_node = scene_host.graph.graph(0x0CCD).node(11)
        defeat_graph = graph([node(11, defeat_node.cls, state=0,
            m_stimulus=defeat_node.field('m_stimulus'), m_targets=defeat_node.field('m_targets'),
            m_8C8C5285=defeat_node.field('m_8C8C5285'),
        )])
        defeat_graph.index = 0x0CCD
        defeat_voice = vm.create(defeat_graph, init=False)
        defeat_voice.initialized = True
        ana = 0x02E000000000013B
        killer = target.entity + 2
        host.world.bind(killer, hero=ana)
        defeat_voice.write((runtime.INSTANCE, 392), expr.Entity(killer))
        cue = defeat_voice.state(0)
        cue.begin()
        listener.client.queue_combat_notice.reset_mock()

        def choose_ana(rows, *, weights):
            self.assertEqual([row[2] for row in rows], [None, ana, None, None])
            self.assertEqual(weights, [0.5, 1, 1, 1])
            return [rows[1]]

        with (mock.patch.object(match, 'players', [listener]),
              mock.patch.object(junkenstein_ai.Random, 'choices', side_effect=choose_ana) as select):
            vm.now += 201
            context.poll()
            vm.run_now()
        select.assert_called_once()
        listener.client.queue_combat_notice.assert_called_once()
        anchor, message, fields = listener.client.queue_combat_notice.call_args.args
        self.assertEqual((anchor, message), (body.id, 27500))
        self.assertEqual(fields['+0x78'], {'+0x0': mode})
        self.assertEqual((fields['+0x88'], fields['+0x90']), (0x0EE00000000006D9, 0x0760000000008CBD))
        self.assertAlmostEqual(cue.voice_duration, 6.822, places=5)

    def test_junkenstein_spawn_search_is_sliced_and_canceled_with_its_state(self):
        model = graph([node(0, 'STU_BA2FCDEB', state=0,
                            m_8FEA3023={'$': 'STUConfigVarDynamic', 'm_identifier': 7},
                            m_radius=literal(8), m_EC1B83C7=literal(10), m_7084DA43=literal(25),
                            m_F649CC6C=literal(0))])
        model.index = 0x0CBA

        def factory(match):
            world = ValueWorld(mode=match.game_map.mode_guid)
            world.map_guid = match.game_map.map_guid
            world.bind(A)
            world.set_possessions(possessors=(), primary=())
            host = scene_host.SceneHost(world, graph_loader={model.index: model}.get)
            host.add(A, definition(spec(model.index)))
            junkenstein_ai.install_enemy_creator(match, host, (), lambda _: None)
            return host

        match = self.match(factory)
        match.game_map = dataclasses.replace(MAP, map_guid=JUNKENSTEIN_MAP, mode_guid=JUNKENSTEIN_MODES[0])
        match.update(1.6, 100)
        host = match.scene_host
        instance, = host.units[A].component.instances.values()
        search = instance.state(0)
        search.request(runtime.BEGIN)
        self.assertTrue(host.advance(101), host.error)
        self.assertTrue(host.advance(102), host.error)
        job, = host.spawn_searches.values()
        self.assertGreater(len(job.positions), 0)
        self.assertLess(len(job.positions), 25)
        self.assertIsNone(instance.find_var(runtime.INSTANCE, 7).value())
        search.end(False)
        self.assertFalse(host.spawn_searches)
        search.request(runtime.BEGIN)
        for frame in range(103, 110):
            self.assertTrue(host.advance(frame), host.error)
        self.assertEqual(len(instance.find_var(runtime.INSTANCE, 7).value()), 25)
        self.assertFalse(host.spawn_searches)

    def test_junkenstein_createhero_mounts_paired_entities_and_rolls_back_partial_startup(self):
        hero = 0x02E0000000000177
        body_resource, control_resource = 0x040000000000171C, 0x040000000000171D
        hero_key, body_key, control_key = (
            f'0x{resource:016X}' for resource in (hero, body_resource, control_resource)
        )
        source = graph([node(0, 'STUStatescriptActionCreateEntity',
                             m_A1A715A6={'$': 'STUConfigVarHero', 'm_hero': hero_key},
                             m_position={'$': 'STUConfigVarVec3', 'm_value': {'X': 2, 'Y': 0, 'Z': 4}},
                             m_3EBF988B={'$': 'STUConfigVarInt', 'm_value': 1},
                             m_1C558638={'$': 'STUConfigVarFloat', 'm_value': 90},
                             m_E95B313B={'$': 'STUConfigVarDynamic', 'm_identifier': 7})])
        candidate = {'junkenstein': {
            'pairs': {hero_key: {'body': body_key, 'controller': control_key}},
            'entities': {
                body_key: {
                    'script_component': definition(), 'target_tags': [], 'ai_weapon_buttons': {'0': 29},
                },
                control_key: {
                    'script_component': definition(), 'ai_weapon_identifiers': {'0': [0x0D8000000000023A]},
                },
            },
        }}
        configure = mock.Mock()

        def factory(match):
            host = host_for(match, source)
            host.world.set_possessions(possessors=(), primary=())
            host.world.array_filter_inputs = {A: (True, 0x80000)}.get
            profile = npc.Definition(hero, body_resource, 'Zombardier', 80)
            junkenstein_ai.install_enemy_creator(match, host, (profile,), configure)
            return host

        match = self.match(factory)
        match.game_map = dataclasses.replace(MAP, map_guid=JUNKENSTEIN_MAP, mode_guid=JUNKENSTEIN_MODES[0])
        with mock.patch.object(scene_host.graph, '_data', return_value=candidate):
            match.update(1.6, 100)
            host = match.scene_host
            body = match.combat.next_bot
            controller = body + 1
            with self.assertRaisesRegex(ValueError, 'differs from the authored pair'):
                host.world.hero_creator(hero, (2, 0, 4), 1, 90, (True, False),
                                        controller_definition=body_resource)
            configure.assert_not_called()
            configure.side_effect = [None, ValueError('controller configuration failed')]
            with self.assertRaisesRegex(ValueError, 'controller configuration failed'):
                host.world.hero_creator(hero, (2, 0, 4), 1, 90, (True, False))
            self.assertEqual(set(host.world.entities), {A})
            self.assertEqual(set(host.units), {A})
            self.assertFalse(match.bots or match.combat.vitals or host.weapons)
            self.assertFalse(host.world.linked_controllers or host.world.current_possessions)
            self.assertFalse(host.combatants)
            self.assertEqual(match.combat.next_bot, body)
            configure.side_effect = None
            instance, = host.units[A].component.instances.values()
            instance.run_node(source.node(0), None)
            self.assertEqual(instance.find_var(runtime.INSTANCE, 7).value(), expr.Entity(body))
            self.assertEqual(match.combat.next_bot, controller + 1)
            bot, = match.bots
            self.assertEqual((bot.entity, bot.position, bot.yaw), (body, (2, 0, 4), 16384))
            self.assertEqual(host.world.linked_controllers[body], controller)
            self.assertEqual(host.world.current_possessions[controller], body)
            self.assertEqual(host.world.entities[controller].definition, control_resource)
            self.assertIs(host.combatant_component(bot), host.units[body].component)
            self.assertIs(host.weapons[body].control.shooter, bot)
            self.assertEqual(match.combat.vitals[body].total, 80)

            enemy = host.world.hero_creator(hero, (3, 0, 4), 0, 0, (True, False))
            friend = host.world.hero_creator(hero, (3, 0, 4), 1, 0, (True, False))
            distant = host.world.hero_creator(hero, (8, 0, 4), 0, 0, (True, False))
            candidate_entity = {'$': 'STU_919FD47C', 'm_expression': {
                'm_opcodes': [13, 0, 0], 'm_dynamicVars': [{'m_identifier': 8}],
            }}
            opposing_team = {'$': 'STU_919FD47C', 'm_expression': {'m_opcodes': [14, 0, 14, 1, 38, 0]},
                             'm_configVars': [{'$': 'STU_65ABFCCC', 'm_target': target}
                                              for target in (candidate_entity, literal(expr.Entity(body)))]}
            model = graph([node(52, 'STUStatescriptStateTrackTargets', state=0,
                                m_8DCA77B6=literal(expr.Entity(body)),
                                m_05BE62DD=literal(expr.Vec3(0, 0.6, 0)),
                                m_radius=literal(3), m_filter={'m_condition': opposing_team},
                                m_8A2905A0={'$': 'STUConfigVarDynamic', 'm_identifier': 375})])
            model.index = 0x0C89
            explosion = host.combatant_component(bot).create(model, init=False)
            explosion.node_state(model.node(52)).refresh()
            self.assertEqual(explosion.find_var(runtime.INSTANCE, 375).value(), (expr.Entity(enemy.id),))
            self.assertTrue(host.world.explosion_damage(body, enemy.id, 29.5))
            self.assertEqual([match.combat.vitals[entry.id].total for entry in (enemy, friend, distant)],
                             [50.5, 80, 80])
            enemy_filter = {'$': 'STUConfigVarFilter', 'm_84065FD1': 1,
                            'm_1E14CCA8': 0x1000000, 'm_4EA686DB': 0x10}
            self.assertEqual(host.world.array_filter_inputs(A), (True, 0x80000))
            self.assertEqual(host.world.array_filter_inputs(body), (True, 0x1000001))
            self.assertEqual(host.world.query_participants(instance, enemy_filter),
                             (expr.Entity(body), expr.Entity(friend.id)))
            self.assertTrue(host.world.explosion_damage(enemy.id, friend.id, 80))
            self.assertEqual(host.world.array_filter_inputs(friend.id), (True, 0x1000011))
            self.assertEqual(host.world.query_participants(instance, enemy_filter), (expr.Entity(body),))
            host.retire_entity(friend.id)
            self.assertIsNone(host.world.array_filter_inputs(friend.id))
            self.assertNotIn(friend.id, (entity for entity, _, _ in host.world.enemy_participant_roster()))

    def test_scene_gate_approach_holds_while_stunned_and_releases_on_retirement(self):
        controller = A + 1
        model = graph([setter(20, 1)])
        model.index = 0x0C87
        actor = bots.Bot(A, {'hero': '02E0000000000175', 'team': 1, 'position': (0, 0, 0),
                            'rotation': (0, 0, 0, 1), 'graphs': []},
                         {'body': '04000000000016BE', 'health': 200, 'name': 'Zomnic'}, {})
        match = self.match(host_for)
        match.bots.append(actor)
        world = ValueWorld()
        world.bind(A)
        world.bind(controller)
        world.set_possessions(possessors=(controller,), primary=((A, controller),))
        world.set_current_possession(controller, A)
        world.set_linked_controller(A, controller)
        world.bind_ai_behavior(controller)
        gate = world.bind(A + 2, position=(0, 0, 4))
        host = scene_host.SceneHost(world, graph_loader={model.index: model}.get)
        host.add(A, definition())
        control = host.add(controller, definition(spec(model.index)))
        match.combat.bind_scene_enemy(host, actor)
        movement = Mover(actor.position, world=flat_world(0), data=mover_data(actor.body))
        approach = junkenstein_ai.GateApproach(match, actor, gate, movement, (0, 0, 4), 3)
        host.objective_approaches[controller] = approach
        match.combat.stuns[A] = 6
        for frame in range(6):
            self.assertTrue(host.advance(frame), host.error)
        self.assertEqual(actor.position, (0, 0, 0))
        self.assertFalse(approach.route.arrived)
        script, = control.instances.values()
        with mock.patch.object(script, 'post', wraps=script.post) as post:
            for frame in range(6, 100):
                self.assertTrue(host.advance(frame), host.error)
            post.assert_called_once()
        self.assertEqual(script.find_var(runtime.INSTANCE, 7).value(), 1)
        self.assertGreater(actor.position[2], 1)
        host.retire_entity(A)
        self.assertFalse(host.objective_approaches)

    def test_scene_enemy_death_uses_script_timer_and_retires_match_actor(self):
        index, controller = 0x0C93, A + 1
        model = graph([
            node(0, 'STUStatescriptEntryGameMessage',
                 m_gameMessage={'$': 'STUConfigVarGameMessage', 'm_gameMessage': f'0x{DIED:016X}'},
                 m_outPlug=plug((2, 'm_beginPlug'))),
            node(2, 'STUStatescriptStateWait', state=0,
                 m_timeout={'$': 'STUConfigVarFloat', 'm_value': 0.128},
                 m_onFinishedPlug=plug((11, 'm_inPlug'))),
            node(11, 'STU_523EE38D', m_entity={'$': 'STU_B5A0CAF0'}),
        ])
        model.index = index
        bot = bots.Bot(A, {'hero': '02E0000000000176', 'team': 1, 'position': (0, 0, 8),
                          'rotation': (0, 0, 0, 1), 'graphs': []},
                       {'body': '04000000000016D6', 'health': 100, 'name': 'Shock-Tire'}, {})

        def factory(match):
            world = ValueWorld(mode=match.game_map.mode_guid)
            world.map_guid = match.game_map.map_guid
            world.bind(A)
            world.bind(controller)
            world.set_possessions(possessors=(controller,), primary=((A, controller),))
            world.set_linked_controller(A, controller)
            world.set_current_possession(controller, A)
            world.bind_ai_behavior(controller)
            host = scene_host.SceneHost(world, graph_loader={index: model}.get)
            host.add(A, definition(spec(index)))
            host.add(controller, definition())
            match.bots.append(bot)
            match.combat.bind_scene_enemy(host, bot)
            return host

        match = self.match(factory)
        match.update(1.6, 100)
        fight = match.combat
        target, = fight._targets()
        host = match.scene_host
        vm = host.combatant_component(bot)
        killer = mock.Mock(spec=bots.Bot, entity=A + 2)
        killer.name = 'Killer'
        fight._damage(killer, target, 10, False, None, 100)
        self.assertEqual(vm.entity_var(OWNER_HEALTH).value(), 90)
        with mock.patch.object(runtime, 'send_message', wraps=runtime.send_message) as send:
            fight._damage(killer, target, 90, False, None, 100)
            fight._damage(killer, target, 1, False, None, 100)
        send.assert_called_once()
        self.assertEqual(send.call_args.args[:2], (vm, DIED))
        self.assertEqual(send.call_args.args[2][combat.KILLER], expr.Entity(A + 2))
        self.assertEqual(send.call_args.args[3], A + 2)
        self.assertTrue(vm.owner_dead)
        self.assertEqual(vm.entity_var(OWNER_HEALTH).value(), 0)
        self.assertFalse(fight.respawns)
        for frame in range(101, 108):
            match.update(frame * combat.TICK, frame)
        self.assertIn(bot, match.bots)
        for frame in range(108, 111):
            match.update(frame * combat.TICK, frame)
        self.assertIsNone(host.error)
        self.assertNotIn(bot, match.bots)
        self.assertNotIn(A, fight.vitals)
        self.assertNotIn(A, host.world.entities)
        self.assertNotIn(controller, host.world.entities)
        self.assertIsNone(host.combatant_component(bot))

    def test_junkenstein_common_enemy_kill_retires_body_and_controller(self):
        controller = A + 1
        bot = bots.Bot(A, {'hero': '02E0000000000175', 'team': 1, 'position': (0, 0, 8),
                          'rotation': (0, 0, 0, 1), 'graphs': []},
                       {'body': '04000000000016BE', 'health': 200, 'name': 'Zomnic'}, {})

        def factory(match):
            world = ValueWorld(mode=JUNKENSTEIN_MODES[0])
            world.map_guid = match.game_map.map_guid
            world.bind(A)
            world.bind(controller)
            world.set_possessions(possessors=(controller,), primary=((A, controller),))
            world.set_linked_controller(A, controller)
            world.set_current_possession(controller, A)
            world.bind_ai_behavior(controller)
            host = junkenstein_scene.JunkensteinSceneHost(world)
            host.add(A, definition())
            host.add(controller, definition())
            host.bind_match(match, mock.Mock())
            match.bots.append(bot)
            match.combat.bind_scene_enemy(host, bot)
            return host

        match = self.match(factory)
        match.game_map = dataclasses.replace(MAP, mode_guid=JUNKENSTEIN_MODES[0])
        match.update(1.6, 100)
        fight, host = match.combat, match.scene_host
        target, = fight._targets()
        killer = mock.Mock(spec=bots.Bot, entity=A + 2)
        killer.name = 'Killer'
        fight._damage(killer, target, 200, False, None, 100)
        deadline = 100 + junkenstein_scene.COMMON_CORPSE_GRACE_MS // host.frame_ms
        self.assertTrue(host.advance(deadline - 1), host.error)
        self.assertIn(bot, match.bots)
        self.assertTrue(host.advance(deadline), host.error)
        self.assertNotIn(bot, match.bots)
        self.assertNotIn(A, fight.vitals)
        self.assertFalse(host.world.entities)
        self.assertFalse(host.units)

    def test_match_scene_weapon_fires_once_and_releases_when_controller_retires(self):
        index, controller, victim = 0x0CA1, A + 1, A + 2
        model = graph([
            node(0, 'STUStatescriptEntry', m_outPlug=plug((16, 'm_beginPlug'))),
            node(16, 'STUStatescriptStateLogicalButton', state=0,
                 m_logicalButton={'$': 'STUConfigVarLogicalButton', 'm_logicalButton': 29},
                 m_80AF45FB=plug((14, 'm_beginPlug'))),
            node(14, 'STUStatescriptStateWeaponVolley', state=1,
                 m_projectileSpeed={'$': 'STUConfigVarInt', 'm_value': 30},
                 m_projectileMotions=[{'$': 'STU_E1A5B579',
                                       'm_599AB8D7': {'$': 'STUConfigVarInt', 'm_value': 20}}],
                 m_D2D11CE9={'m_modifyHealth': {'m_amount': {'$': 'STUConfigVarInt', 'm_value': 60}},
                             'm_0D30E40C': [{'m_radius': {'$': 'STUConfigVarInt', 'm_value': 1},
                                             'm_B399CACB': {'$': 'STUConfigVarInt', 'm_value': 1}}]}),
        ], entries=[0])
        model.index = index
        model.fields['m_680A2CB2'] = [0]

        def factory(match):
            world = ValueWorld(mode=match.game_map.mode_guid)
            world.map_guid = match.game_map.map_guid
            world.bind(A, definition=0x040000000000171C)
            world.bind(controller, definition=0x040000000000171D, component92_present=True)
            world.bind(victim)
            host = scene_host.SceneHost(world, graph_loader={index: model}.get)
            component = host.add(A, definition(spec(index)))
            host.add(controller, definition())
            world.set_linked_controller(A, controller)
            world.bind_ai_behavior(controller)
            world.set_possessions(possessors=(controller,), primary=((A, controller),))
            world.set_current_possession(controller, A)
            shooter = mock.Mock(spec=bots.Bot, entity=A, hero=junkenstein_ai.ZOMBARDIER,
                                team=1, position=(0, 0, 0), dead=False)
            shooter.body_script = SimpleNamespace(component=component)
            control = junkenstein_ai.ZombardierControl(controller, shooter, component, 29)
            match.combat.bind_scene_zombardier(host, control)
            return host

        match = self.match(factory)
        match.game_map = dataclasses.replace(MAP, map_guid=JUNKENSTEIN_MAP, mode_guid=JUNKENSTEIN_MODES[0])
        fight = match.combat
        target = combat.Target(victim, 0, (0, 0, 8), 1.8, 0.4, 1.55, None)
        with (mock.patch.object(fight, '_enemies', return_value=[target]),
              mock.patch.object(fight, '_damage') as damage):
            match.update(1.6, 100)
            match.update(1.616, 101)
            host = match.scene_host
            self.assertIsNone(host.error)
            vm = host.units[A].component
            instance, = vm.instances.values()
            selector = scene_host.graph.graph(index).node(0).field('m_value')
            for card, expected in ((0x3C, 30.0), (0x3D, 60.0), (0x4A, 60.0), (0x3E, 90.0)):
                host.world.configure_junkenstein_card(0x0630000000000000 | card)
                self.assertEqual(instance.evaluate(selector), expected)
            self.assertIn(29, vm.held)
            rocket, = fight.rockets
            self.assertEqual(rocket.last_tick, 101)
            self.assertEqual(rocket.flown, 0.0)
            self.assertEqual(rocket.gravity, 20)
            self.assertEqual(host.world._lookup_ai_linked_target(controller, 0), victim)

            host.retire_entity(controller)
            host.world.bind(controller)
            for frame in range(102, 125):
                match.update(frame * combat.TICK, frame)
            self.assertNotIn(29, vm.held)
            self.assertFalse(host.weapons)
            self.assertFalse(fight.rockets)
            damage.assert_called_once()
            self.assertEqual(damage.call_args.args[1:3], (target, 60))

    def match(self, factory):
        return Match(MAP, collision_provider=StaticCollisionProvider(flat_world(0)),
                     scene_host_factory=factory)

    def test_match_starts_factory_once_and_advances_on_the_simulation_ticks(self):
        factory = mock.Mock(side_effect=host_for)
        match = self.match(factory)
        match.update(1.0, 100)
        match.update(1.016, 101)
        factory.assert_called_once_with(match)
        self.assertEqual(match.scene_host.frame, 101)
        self.assertFalse(match.ended)
        self.assertEqual(match.snapshot()['scene_script']['host']['frame'], 101)

    def test_junkenstein_scene_roster_tracks_match_controllers_in_slot_order(self):
        match = self.match(host_for)
        match.game_map = dataclasses.replace(MAP, map_guid=JUNKENSTEIN_MAP, mode_guid=JUNKENSTEIN_MODES[0])
        first = match.add_player(1, 'First', SOLDIER, 0, False)
        second = match.add_player(2, 'Second', SOLDIER, 1, False)
        self.assertTrue(match._advance_scene(1.0, 100))
        world = match.scene_host.world
        roster = world.session_entity_roster
        self.assertEqual(tuple(roster()), (first.entity, second.entity))
        world.bind(first.entity)
        world.bind(second.entity)
        instance = SimpleNamespace(component=match.scene_host.units[A].component,
                                   evaluate=lambda config: config['m_6CE7A900'])
        team_size = {'$': 'STU_D289FA32', 'm_team': {'$': 'STU_BD6CC023', 'm_6CE7A900': 0}}
        self.assertEqual(world.native(team_size, instance), 1)
        config = {'$': 'STUConfigVarFilter', 'm_84065FD1': 0x40000,
                  'm_1E14CCA8': 0x800000, 'm_4EA686DB': 0x10}
        self.assertEqual(world.query_participants(instance, config), (expr.Entity(first.entity),))
        match.leave(first)
        self.assertEqual(tuple(roster()), (second.entity,))
        self.assertEqual(world.native(team_size, instance), 0)
        self.assertEqual(world.query_participants(instance, config), ())
        config.update(m_84065FD1=1, m_1E14CCA8=0x1000000)
        self.assertIs(world.query_participants(instance, config), NotImplemented)

    def test_junkenstein_arcade_cards_feed_the_mode_difficulty_switch(self):
        owner = runtime.GAME_MODE_ENTITY
        selector = {'$': 'STUConfigVarDynamic', 'm_identifier': 6751, 'm_60DB8F99': 1}
        tier = {'$': 'STUConfigVarDynamic', 'm_identifier': 9948, 'm_60DB8F99': 1}
        identifiers = (0x195E, 0x195F, 0x26DF, 0x1960)
        cases = [
            {'m_value': {'$': 'STUConfigVarIdentifier', 'm_identifier': f'0x0D8000000000{identifier:04X}'},
             **plug((148 - index, 'm_inPlug'))}
            for index, identifier in enumerate(identifiers)
        ]
        model = graph([
            node(0, 'STUStatescriptEntry', m_outPlug=plug((263, 'm_inPlug'))),
            node(263, 'STUStatescriptActionSetVar', m_out_Var=selector, m_outPlug=plug((149, 'm_inPlug')),
                 m_value={'$': 'STU_7BD2CA19', 'm_resourceKey': None, 'm_EE729DCB': 0}),
            node(149, 'STUStatescriptActionSwitch', m_262AE982=selector, m_FDD34C25=cases),
            *(node(148 - index, 'STUStatescriptActionSetVar', m_out_Var=tier,
                   m_value={'$': 'STUConfigVarInt', 'm_value': index}) for index in range(4)),
        ], entries=[0])
        model.index = 0x0C8C

        def factory(match):
            world = ValueWorld(mode=match.game_map.mode_guid)
            world.map_guid = match.game_map.map_guid
            world.bind(owner)
            host = scene_host.SceneHost(world, graph_loader={model.index: model}.get)
            host.add(owner, definition(spec(model.index)))
            return host

        server = GameServer(scene_host_factory=factory,
                            collision_provider_factory=lambda: StaticCollisionProvider(flat_world(0)))
        card_groups = ((0x3C, 0x3D, 0x4A, 0x3E), (0x43, 0x44, 0x49, 0x45))
        challenge_flags = (0x7D53, 0x7D54, 0x7D60, 0x7D61, 0x7D62, 0x7D63)
        for mode, cards in zip(JUNKENSTEIN_MODES, card_groups, strict=True):
            levels = list(enumerate(cards))
            if mode == JUNKENSTEIN_MODES[0]:
                levels.extend((2, card) for card in range(0x16B, 0x171))
            levels.append((None, 0))
            game_map = dataclasses.replace(MAP, map_guid=JUNKENSTEIN_MAP, mode_guid=mode)
            for expected, card in levels:
                with self.subTest(card=card, mode=mode):
                    server.create_match(game_map, [], 0x0630000000000000 | card if card else 0)
                    match = server.matches[-1]
                    self.assertTrue(match._advance_scene(1.0, 100), match.scene_host_error)
                    world = match.scene_host.world
                    self.assertEqual(world.entity_var(owner, 9948), expected)
                    expected_flag = challenge_flags[card - 0x16B] if 0x16B <= card <= 0x170 else None
                    for flag in challenge_flags:
                        self.assertIs(world.entity_var(owner, flag), True if flag == expected_flag else None)
                    self.assertEqual(world.entity_var(owner, 0x7E0F), 1.0 if card == 0x16F else None)

    def test_script_failure_returns_players_home_before_spawn_or_combat_runs(self):
        model = graph([node(0, 'STUStatescriptEntry', m_outPlug=plug((1, 'm_inPlug'))),
                       node(1, 'UnknownAction')], entries=[0])
        match = self.match(lambda match: host_for(match, model))
        player = match.add_player(1, 'Player', SOLDIER, 0, False)
        client = HomeClient()
        player.client = client
        with (mock.patch.object(match.combat, 'update', side_effect=AssertionError('must not run')),
              self.assertLogs('ow174.game', level='ERROR')):
            match.update(1.0, 100)
        self.assertTrue(match.ended and player.gone and match.scene_host.closed)
        self.assertEqual(client.home, [1.0])
        self.assertIn('UnknownAction', match.scene_host_error)
        self.assertFalse(match.scene_host.world.entities)

    def test_last_player_leave_closes_scene_and_cannot_send_from_old_components(self):
        match = self.match(host_for)
        player = match.add_player(1, 'Player', SOLDIER, 0, False)
        match.update(1.0, 100)
        match.leave(player)
        self.assertTrue(match.scene_host.closed and match.ended)
        self.assertFalse(match.scene_host.world.script_components)
        match.update(1.016, 101)
        self.assertIsNone(match.scene_host_error)

    def test_wrong_map_or_missing_placeable_creates_are_rejected(self):
        for mismatch in ('map', 'creates'):
            def factory(match, mismatch=mismatch):
                host = host_for(match)
                if mismatch == 'map':
                    host.world.map_guid += 1
                return host

            match = self.match(factory)
            if mismatch == 'creates':
                match.game_map = dataclasses.replace(MAP, placeables=(123,))
            with self.assertLogs('ow174.game', level='ERROR'):
                match.update(1.0, 100)
            self.assertTrue(match.ended and match.scene_host.closed)

    def test_factory_error_is_terminal_and_is_not_retried(self):
        factory = mock.Mock(side_effect=ValueError('invalid explicit world'))
        match = self.match(factory)
        with self.assertLogs('ow174.game', level='ERROR'):
            match.update(1.0, 100)
        match.update(1.016, 101)
        self.assertEqual(factory.call_count, 1)
        self.assertTrue(match.ended)

    def test_game_server_passes_the_factory_to_each_real_match(self):
        server = GameServer(scene_host_factory=host_for,
            collision_provider_factory=lambda: StaticCollisionProvider(flat_world(0)))
        server.create_match(MAP, [])
        server.create_match(MAP, [])
        for match in server.matches:
            match.update(1.0, 100)
        self.assertIsNot(server.matches[0].scene_host, server.matches[1].scene_host)
        self.assertIsNot(server.matches[0].scene_host.world, server.matches[1].scene_host.world)

    def test_stored_server_export_refuses_unimplemented_shared_runtime_storage(self):
        server = GameServer(scene_host_factory=host_for)
        with self.assertRaisesRegex(ServerSnapshotError, 'shared scene script runtime storage'):
            export_server_state(server)

    def test_server_stop_retires_owned_scene_runtime(self):
        server = GameServer(scene_host_factory=host_for,
            collision_provider_factory=lambda: StaticCollisionProvider(flat_world(0)))
        server.create_match(MAP, [])
        match = server.matches[0]
        match.update(1.0, 100)
        server.stop()
        self.assertTrue(match.ended and match.scene_host.closed)

    def test_successful_vm_pass_does_not_send_the_prepared_entrance_readiness(self):
        match = self.match(host_for)
        player = match.add_player(1, 'Player', SOLDIER, 0, False)
        match.update(1.0, 100)
        player.loaded_at, player.steps_done = 0.0, 2
        client = SimpleNamespace(ownership_delivered=lambda: True,
            queue_entities=mock.Mock(side_effect=AssertionError('scene replication is not ready')))
        match._spawn_steps(player, client, 2.0)
        self.assertEqual(player.steps_done, 2)
        self.assertFalse(match.scene_host.client_ready)
        client.queue_entities.assert_not_called()


if __name__ == '__main__':
    unittest.main()
