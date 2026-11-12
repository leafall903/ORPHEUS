"""Map entity identities and the native no-output UUID lookup contract."""

import copy
import struct
import unittest
import uuid
from types import SimpleNamespace
from unittest import mock

from ow174.game import mover
from ow174.game.combat import Vitals
from ow174.game.content import JUNKENSTEIN_MODES
from ow174.game.match import _rotation
from ow174.game.script import distance_constraints, entity_team, expr, modifiers, npc_queries, play, runtime, scene
from tests.test_script_for_code import KEY, make, row
from tests.test_script_play_resource import array, inline, integer, requesting, with_schema
from tests.test_script_play_resource import row as schema_row
from tests.test_script_runtime_budget import graph, node, plug, setter

ALPHA = '12345678-1234-4321-9876-112233445566'
BETA = '87654321-1234-4321-9876-112233445566'
DEF = '0x0400000000000001'
IDENT = 0x0D80000000000007


def reference(value=ALPHA):
    return {'$': 'STUConfigVarEntityReference', 'm_4A2AD74A': value}


def placement(index, *, client_id, value=ALPHA, identifier=0, position=(1, 2, 3)):
    header = uuid.UUID(value).bytes_le + bytes((3, 0, 0, 9))
    metadata = bytearray(44)
    struct.pack_into('<H', metadata, 36, client_id)
    return {'index': index, 'definition': DEF, 'placeable_header': header.hex(),
            'record_metadata': metadata.hex(),
            'identifiers': [f'0x{identifier:016X}', '0x0000000000000000'], 'position': list(position),
            'scale': [1, 1, 1], 'rotation': [0, 0, 0, 1],
            'components': [{'class': 'Instance', 'fields': {'x': 7}}]}


def manifest(*placements):
    return {'version': 1, 'map': '0x0800000000000002', 'entities': list(placements),
            'definitions': {DEF: {'fields': {'x': 1}, 'components': {'entries': []}}}, 'executable': False}


def caller(world):
    return runtime.Component(123, world=world, strict=True).create(graph(), init=False)


class SceneIdentityTests(unittest.TestCase):
    def test_distance_constraint_registry_orders_equal_priority_and_cleans_up_on_failure(self):
        world = scene.SceneWorld()
        world.bind(123)
        registry = distance_constraints.Registry()
        world.set_distance_constraints(123, registry)
        old = registry.register(object(), priority=1.5)
        newest = registry.register(object(), priority=1.5, above_equal=True)
        low = registry.register(object(), priority=-1)
        component = runtime.Component(123, world=world, strict=True)
        active = component.create(graph([node(0, 'STU_B61E6681', state=0)]), init=False)
        state = active.state(0)
        state.begin()
        added = state.registration[1]
        self.assertEqual(registry.entries, (newest, old, added, low))
        with self.assertRaises(runtime.UnsupportedScriptError):
            component.unsupported(active, 'fixture failure', 'Fixture')
        self.assertEqual(registry.entries, (newest, old, low))
        world.unbind(123)
        self.assertFalse(registry.entries)
        self.assertIs(world.distance_constraint_registry(123), NotImplemented)

    def test_target_tag_query_replaces_old_results_in_declared_order_and_forgets_reused_ids(self):
        def tag(key):
            return {'$': 'STU_357DABBB', 'm_7D7C86A1': f'0x{0x0D60000000000000 | key:016X}'}

        world = scene.SceneWorld()
        for entity in (123, 124, 125, 126, 127):
            world.bind(entity)
        world.set_target_tag_candidates([(126, (2, 3)), (125, (1, 2, 3)),
                                         (124, (1, 2)), (127, (1, 2, 3, 4))])
        query = node(0, 'STU_42578342', m_4AEEE6A1={
            '$': 'STUConfigVarDynamic', 'm_identifier': 9, 'm_60DB8F99': 0},
            m_9C7BD8B8=array(tag(1), tag(3)), m_B486603E=array(tag(2), tag(3)),
            m_8B2A4DF3=tag(4), m_outPlug=plug((1, 'm_inPlug')))
        instance = runtime.Component(123, world=world, strict=True).create(graph([query, setter(1, 99)]), init=False)
        instance.write((0, 9), (expr.Entity(999),))
        instance.run_node(instance.graph.node(0), None)
        self.assertEqual(instance.vars[9].value(), (expr.Entity(126), expr.Entity(125)))
        self.assertEqual(instance.vars[7].value(), 99)
        world.unbind(126)
        world.bind(126)
        instance.run_node(instance.graph.node(0), None)
        self.assertEqual(instance.vars[9].value(), (expr.Entity(125),))
        world.set_target_tag_candidates([])
        instance.run_node(instance.graph.node(0), None)
        self.assertEqual(instance.vars[9].value(), ())

    def test_junkenstein_handoff_builds_the_authored_phase_duration_and_graph_list(self):
        world = scene.SceneWorld(mode=JUNKENSTEIN_MODES[1])
        mode = runtime.GAME_MODE_ENTITY
        world.bind(mode)
        component = runtime.Component(mode, world=world, strict=True)
        world.bind_script_component(mode, component)
        model = scene.graph.graph(0x180A)
        instance = component.create(model, init=False)
        instance.initialized = True
        for position in (55, 12):
            instance.run_node(model.node(position), None)

        duration = model.node(53).field('m_143D4C5B')[0]['m_value']
        self.assertAlmostEqual(instance.evaluate(duration), 56.5)
        first_phase = instance.find_var(runtime.INSTANCE, 16638).value()[0]
        instance.write((runtime.INSTANCE, 13151), first_phase)
        self.assertEqual(instance.evaluate(model.node(42).field('m_814F8EC3')), tuple(
            expr.Asset(0x0580000000000000 | index) for index in (0x027F, 0x17DF, 0x181E, 0x0D94, 0x184C)
        ))

        references = (expr.Entity(123), expr.Entity(456), expr.Entity(789))
        for var, value in zip((11251, 4912, 4913), references, strict=True):
            instance.write((runtime.INSTANCE, var), value)
        launch = model.node(41)
        for selected in instance.evaluate(model.node(42).field('m_814F8EC3')):
            with self.subTest(graph=selected):
                # Model the native list state's local item scope independently of its scheduler.
                scope = component.create(
                    model, parent=instance, parent_state=42,
                    values={(runtime.INSTANCE, 9694): selected}, init=False,
                )
                resource = play.resolve_resource(scope, launch)
                self.assertEqual(resource, play.Resource(selected.guid))
                request = play.resolve_request(
                    scope, launch, resource,
                    context_provider=lambda scope=scope: world.play_script_context(scope, launch),
                )
                self.assertEqual(request.targets, (mode,))
                self.assertEqual(request.params, (
                    (0x0A4000000000066B, (expr.Map(((7310, 2.5),)),)),
                    (0x0A4000000000002B, (expr.f32(2.9),)),
                    (0x0A4000000000066E, (references[0],)),
                    (0x0A400000000003B8, (expr.Map(((4912, references[1]), (4913, references[2]))),)),
                ))
                component.destroy(scope)

        phase = instance.node_state(model.node(42))
        phase.begin()
        scopes = dict(phase.children)
        self.assertEqual(tuple(scopes), instance.evaluate(model.node(42).field('m_814F8EC3')))
        callees = {}
        for selected, scope in phase.children:
            scope.update()
            child = scope.node_state(launch).child
            self.assertEqual(child.graph.index, selected.guid & 0xFFFF)
            self.assertIs(child.parent, scope)
            callees[selected] = child

        time_scale_graph = callees[expr.Asset(0x058000000000181E)]
        component.frame = 123
        time_scale_graph.update()
        self.assertEqual(time_scale_graph.find_var(runtime.INSTANCE, 13784).value(),
                         (expr.Map(((7310, 2.5),)),))
        # The native keyed member lookup F65300 requires a map at the outer tag.
        # A start parameter is an array, so the authored false branch begins
        # the scale directly, without the duration Wait.
        self.assertFalse(any(
            state.node.pos == 3 and state.active for state in time_scale_graph.states.values()
        ))
        time_scale = time_scale_graph.node_state(time_scale_graph.graph.node(0))
        self.assertEqual(time_scale.payload(), {"which": True, "value": 123})

        retained = next(iter(scopes))
        new_phase = expr.Map(tuple((key, (retained, None, retained) if key == 16748 else value)
                                   for key, value in first_phase.items))
        instance.write((runtime.INSTANCE, 13151), new_phase)
        component.frame = 124
        instance.update()
        self.assertEqual(time_scale.payload(), {"which": False, "value": 124})
        self.assertEqual([selected for selected, _ in phase.children], [retained, retained])
        self.assertIs(phase.children[0][1], scopes[retained])
        self.assertIsNot(phase.children[1][1], scopes[retained])
        for selected, child in callees.items():
            self.assertEqual(child.id in component.instances, selected == retained)

        phase.end(False)
        self.assertEqual(list(component.instances.values()), [instance])

    def test_junkenstein_mode_has_no_local_presentation_entity(self):
        world = scene.SceneWorld(mode=JUNKENSTEIN_MODES[1])
        mode = runtime.GAME_MODE_ENTITY
        world.bind(mode)
        component = runtime.Component(mode, world=world, strict=True)
        world.bind_script_component(mode, component)
        model = scene.graph.graph(0x180A)
        instance = component.create(model, init=False)
        condition = model.node(10).field('m_condition')
        query = condition['m_configVars'][0]
        self.assertEqual(play.evaluate_scalar_result(instance, query, initial=42), (expr.Entity(0), True))
        component.set(29350, True)
        self.assertIs(instance.evaluate(condition), True)
        self.assertTrue(instance.run_node(model.node(22), None))

    def test_junkenstein_phase_transition_dispatches_at_full_progress_and_releases(self):
        model = scene.graph.graph(0x180A)
        component = runtime.Component(runtime.GAME_MODE_ENTITY, world=scene.SceneWorld(), strict=True)
        instance = component.create(model, init=False)
        instance.init(run_entries=False)
        transition = instance.node_state(model.node(46))
        actions = []

        def record(node, caller):
            actions.append((component.now, node.pos))
            return True

        with mock.patch.object(instance, 'run_node', side_effect=record):
            transition.begin()
            for elapsed in range(16, 705, 16):
                component.now = elapsed
                component.run_now()
                self.assertEqual(transition.active, elapsed < 704)
            self.assertEqual(actions, [(0, 47), (304, 51)])

            transition.begin()
            transition.timer(1)
            component.now = 720
            component.run_now()
            self.assertFalse(transition.active)
            self.assertEqual(actions, [(0, 47), (304, 51), (704, 47)])

    def test_junkenstein_phase_helper_preserves_native_config_failure(self):
        model = scene.graph.graph(0x0D94)
        component = runtime.Component(runtime.GAME_MODE_ENTITY, world=scene.SceneWorld(), strict=True)
        instance = component.create(model, init=False)
        initial = object()
        start = model.node(3).field('m_value')
        remaining = model.node(5).field('m_value')
        sampled = remaining['m_configVars'][0]
        for config in (start, sampled):
            with self.subTest(config=config['$']):
                value, success = play.evaluate_scalar_result(instance, config, initial=initial)
                self.assertIs(value, initial)
                self.assertFalse(success)
                self.assertEqual(play.evaluate_value(instance, config, initial=0), 0)

        instance.write((runtime.INSTANCE, 8191), play.evaluate_value(instance, start, initial=0))
        remaining_value = play.evaluate_value(instance, remaining, initial=0)
        self.assertIsNone(remaining_value)
        instance.write((runtime.INSTANCE, 8189), remaining_value)
        self.assertIs(instance.evaluate(model.node(4).field('m_condition')), True)
        self.assertEqual(play.evaluate_value(instance, model.node(2).field('m_value'), initial=0), 0.0)

        instance.init()
        actions = []
        run_node = instance.run_node

        def trace(node, caller):
            if node.pos in (1, 6, 7, 8):
                actions.append((component.frame, node.pos))
            return run_node(node, caller)

        with mock.patch.object(instance, 'run_node', side_effect=trace):
            for frame in range(5):
                component.run_frame(frame)
        self.assertEqual(actions, [(0, 8), (1, 7), (2, 6), (4, 1)])
        self.assertFalse(any(state.active for state in instance.states.values()))

    def test_junkenstein_phase_message_reaches_each_mounted_receiver_once(self):
        world = scene.SceneWorld(mode=JUNKENSTEIN_MODES[1])
        mode = runtime.GAME_MODE_ENTITY
        components = {}
        for entity, indices in ((mode, (0x0C8C, 0x0CBC, 0x0CC7, 0x181D, 0x0CBB)), (123, (0x0CA5,)),
                                (456, (0x0CA2, 0x0C90, 0x13A4))):
            world.bind(entity)
            component = runtime.Component(entity, world=world, strict=True)
            world.bind_script_component(entity, component)
            components[entity] = [
                component.create(scene.graph.graph(index), init=False) for index in indices
            ]
        sender = components[mode][0]
        cue = 0x02400000000006BE
        self.assertEqual(world.routed_game_message_targets(sender, sender.graph.node(84), cue),
                         (expr.Entity(mode), expr.Entity(123)))
        for helper, position in zip(components[mode][-2:], (0, 21), strict=True):
            self.assertEqual(world.routed_game_message_targets(helper, helper.graph.node(position),
                                                               0x0240000000000669), (expr.Entity(456),))
        waves = sender.component.create(scene.graph.graph(0x0CB3), init=False)
        for position in (98, 101, 102, 104):
            self.assertEqual(world.routed_game_message_targets(
                waves, waves.graph.node(position), 0x02400000000006D3,
            ), (expr.Entity(mode),))
        sender.component.create(scene.graph.graph(0x0CED), init=False)
        cinematic = sender.component.create(scene.graph.graph(0x0CEC), init=False)
        for position in (2, 3):
            self.assertEqual(world.routed_game_message_targets(
                cinematic, cinematic.graph.node(position), 0x02400000000006BB,
            ), (expr.Entity(mode), expr.Entity(123)))

    def test_junkenstein_completion_rechecks_the_final_wave_flag(self):
        authored = scene.graph.graph(0x0C8C)
        model = graph([
            node(275, 'STUStatescriptState', state=0,
                 m_transitionPlug=authored.node(275).field('m_transitionPlug')[:1]),
            node(34, 'STU_3387AB5D', **authored.node(34).fields),
            node(78, 'STUStatescriptActionSetVar',
                 m_out_Var=authored.node(78).field('m_out_Var'),
                 m_value=authored.node(78).field('m_value')),
        ])
        model.index = authored.index
        component = runtime.Component(runtime.GAME_MODE_ENTITY, strict=True)
        instance = component.create(model, init=False)
        instance.init(run_entries=False)
        phase = instance.state(0)
        phase.begin()
        self.assertTrue(phase.active)

        instance.write((runtime.ENTITY, 8493), True)
        component.run_now()

        self.assertFalse(phase.active)
        self.assertIs(instance.find_var(runtime.INSTANCE, 8617).value(), True)

    def test_junkenstein_phase_cleanup_damages_a_living_boss_once(self):
        world = scene.SceneWorld(mode=JUNKENSTEIN_MODES[0])
        damage = mock.Mock()
        world.bind(123, health=lambda: 250.0, self_damage=damage)
        component = runtime.Component(123, world=world, strict=True)
        world.bind_script_component(123, component)
        model = scene.graph.graph(0x0CBC)
        instance = component.create(model, init=False)
        instance.init(run_entries=False)
        cleanup = instance.node_state(model.node(69))
        cleanup.begin()
        component.run_frame(1)
        damage.assert_called_once_with(10000.0)

        cleanup.end(False)
        component.owner_dead = True
        cleanup.begin()
        component.run_frame(2)
        damage.assert_called_once()

    def test_junkenstein_transition_releases_0cbb_wait_and_stops_its_helper(self):
        world = scene.SceneWorld(mode=JUNKENSTEIN_MODES[1])
        mode = runtime.GAME_MODE_ENTITY
        world.bind(mode)
        component = runtime.Component(mode, world=world, strict=True)
        world.bind_script_component(mode, component)
        receiver = component.create(scene.graph.graph(0x0CBB), init=False)
        receiver.init(run_entries=False)
        wait = receiver.node_state(receiver.graph.node(89))
        wait.begin()
        sender = component.create(scene.graph.graph(0x1F93))

        original_send = world.send_game_message_packet
        with mock.patch.object(world, 'send_game_message_packet', wraps=original_send) as send:
            component.run_now()
        send.assert_called_once()
        _, _, target, packet = send.call_args.args
        self.assertEqual((target, packet.message), (mode, 0x0240000000000F58))
        self.assertFalse(wait.active)
        self.assertTrue(sender.stopped)

    def test_symmetra_beam_contact_exits_without_local_presentation(self):
        world = scene.SceneWorld(mode=JUNKENSTEIN_MODES[1])
        world.outline_context_available = False
        component = runtime.Component(123, world=world, strict=True)
        model = scene.graph.graph(0x157C)
        instance = component.create(model, init=False)
        contact = instance.node_state(model.node(143))

        contact.begin()
        self.assertTrue(contact.active)
        self.assertNotIn(536, instance.vars)
        contact.end(False)
        self.assertFalse(contact.active)

        world.outline_context_available = True
        with self.assertRaisesRegex(runtime.UnsupportedScriptError, "beam contact presentation context"):
            contact.begin()

    def test_junkenstein_player_body_query_tracks_possession_and_retirement(self):
        world = scene.SceneWorld(mode=JUNKENSTEIN_MODES[1])
        controller, body = 0xA0000100, 0xA0000101
        world.bind(controller)
        world.bind(body)
        world.set_possessions()
        world.set_possessor(controller, True)
        world.set_entity_query_context(controller)
        component = runtime.Component(controller, world=world, strict=True)
        world.bind_script_component(controller, component)
        model = scene.graph.graph(0x0C90)
        instance = component.create(model, init=False)
        query = model.node(49).field('m_condition')
        self.assertIs(instance.evaluate(query), False)

        world.set_current_possession(controller, body)
        self.assertIs(instance.evaluate(query), True)

        world.unbind(body)
        self.assertIs(instance.evaluate(query), False)

    def test_junkenstein_cleanup_continues_only_when_the_authored_tag_is_absent(self):
        world = scene.SceneWorld(mode=JUNKENSTEIN_MODES[1])
        mode = runtime.GAME_MODE_ENTITY
        world.bind(mode)
        component = runtime.Component(mode, world=world, strict=True)
        world.bind_script_component(mode, component)
        model = scene.graph.graph(0x181D)
        sender = component.create(model)
        component.run_now()
        self.assertTrue(sender.stopped)
        self.assertEqual(world.corpse_retirements, {})

        component.create(model)
        with (
            mock.patch.object(world, 'static_tag_targets', return_value=(expr.Entity(123),)) as lookup,
            self.assertRaisesRegex(runtime.UnsupportedScriptError, 'STU_523EE38D'),
        ):
            component.run_now()
        lookup.assert_called_once_with(0x0D60000000000044)

    def test_junkenstein_message_receiver_reads_live_body_facing(self):
        world = scene.SceneWorld()
        yaw = 0
        world.bind(123, position=(0, 0, 0), rotation_reader=lambda: _rotation(yaw))
        world.bind(456, position=(0, 0, 4))
        definition = scene.graph.graph(0x0CA5)
        instance = runtime.Component(123, world=world, strict=True).create(definition, init=False)
        instance.write((runtime.INSTANCE, 40), expr.Entity(456))
        condition = definition.node(80).field('m_condition')
        self.assertIs(instance.evaluate(condition), True)
        yaw = -32768
        self.assertIs(instance.evaluate(condition), False)
        yaw = 0
        self.assertIs(instance.evaluate(condition), True)

    def test_junkenstein_phase_aim_lock_keeps_the_entry_pose_until_cleanup(self):
        world = scene.SceneWorld(mode=JUNKENSTEIN_MODES[1])
        body = 123
        pose = SimpleNamespace(yaw=-500, pitch=150)
        world.bind(body, movement_reader=lambda _frame: pose)
        world.set_property_modifiers(body, modifiers.NumericModifiers())
        component = runtime.Component(body, world=world, strict=True)
        world.bind_script_component(body, component)
        model = scene.graph.graph(0x0CA5)
        instance = component.create(model, init=False)
        lock = instance.node_state(model.node(70))
        lock.begin()
        pose.yaw, pose.pitch = 500, 0
        self.assertEqual(lock.aim, (-500, 150))
        self.assertEqual(component.state_registries[lock.node.cls], [lock])
        lock.end(False)
        self.assertFalse(component.state_registries.get(lock.node.cls))

    def test_junkenstein_body_aim_lock_reads_the_authored_heading_and_pitch(self):
        world = scene.SceneWorld(mode=JUNKENSTEIN_MODES[1])
        body = 123
        pose = SimpleNamespace(yaw=-8192, pitch=-4096)
        world.bind(body, rotation_reader=lambda: _rotation(pose.yaw),
                   movement_reader=lambda _frame: pose)
        world.set_property_modifiers(body, modifiers.NumericModifiers())
        component = runtime.Component(body, world=world, strict=True)
        world.bind_script_component(body, component)
        model = scene.graph.graph(0x00CB)
        instance = component.create(model, init=False)
        instance.initialized = True
        instance.run_node(model.node(23), None)
        component.run_frame(0)

        lock = instance.node_state(model.node(21))
        pose.yaw, pose.pitch = 0, 0
        self.assertEqual(lock.aim, (-8192, -4096))
        self.assertEqual(component.state_registries[lock.node.cls], [lock])
        lock.end(False)
        self.assertFalse(component.state_registries.get(lock.node.cls))

    def test_junkenstein_health_gate_uses_live_damage_and_all_wave_pool_maxima(self):
        world = scene.SceneWorld()
        vitals = Vitals(123, 250)
        remove = vitals.add_wave_health(750)
        world.bind(123, health=lambda: vitals.normal_health(False),
                   maximum_health=lambda: vitals.normal_health(True))
        switch = scene.graph.graph(0x0CBC).node(53)
        condition = switch.field('m_condition')
        maximum = condition['m_configVars'][1]
        definition = graph([
            node(0, 'STUStatescriptEntry', m_outPlug=plug((1, 'm_beginPlug'))),
            node(1, switch.cls, state=0, m_condition=condition,
                 m_onTruePlug=plug((2, 'm_inPlug')), m_onFalsePlug=plug((3, 'm_inPlug'))),
            setter(2, 1),
            setter(3, 0),
        ], entries=[0])
        component = runtime.Component(123, world=world, strict=True)
        instance = component.create(definition)
        component.run_frame(0)
        self.assertEqual(instance.evaluate(maximum), 1000)
        self.assertEqual(instance.vars[7].value(), 0)
        vitals.damage(250)
        component.run_frame(16)
        self.assertEqual(instance.vars[7].value(), 0)
        vitals.damage(1)
        component.run_frame(32)
        self.assertEqual(instance.vars[7].value(), 1)
        self.assertEqual(instance.evaluate(maximum), 1000)
        remove()
        self.assertEqual(instance.evaluate(maximum), 250)
        world.unbind(123)
        marker = object()
        self.assertIs(instance.evaluate(maximum, initial=marker), marker)

    def test_junkenstein_shared_boss_gate_preserves_empty_query_outputs(self):
        definition = scene.graph.graph(0x0C83)
        condition = definition.node(5).field('m_condition')
        for mode in JUNKENSTEIN_MODES:
            with self.subTest(mode=mode):
                world = scene.SceneWorld(mode=mode)
                component = runtime.Component(123, world=world, strict=True)
                instance = component.create(definition, init=False)
                for query in condition['m_configVars']:
                    marker = object()
                    self.assertIs(instance.evaluate(query, initial=marker), marker)
                self.assertIs(instance.evaluate(condition), False)

        component = runtime.Component(123, world=scene.SceneWorld(), strict=True)
        instance = component.create(definition, init=False)
        with self.assertRaises(runtime.UnsupportedScriptError):
            instance.evaluate(condition)

    def test_junkenstein_display_name_tracks_component_presence_and_state_lifetime(self):
        for present in (False, None, True):
            with self.subTest(present=present):
                world = scene.SceneWorld()
                world.bind(123, component92_present=present)
                definition = graph([
                    node(0, 'STUStatescriptEntry', m_outPlug=plug((24, 'm_beginPlug'))),
                    node(24, 'STU_B7D2C9C0', state=0, m_overrideName='0x0DE0000000007108',
                         m_14EDDDA7={'$': 'STU_B5A0CAF0'}, m_onBeginPlug=plug((1, 'm_inPlug'))),
                    setter(1, 99),
                ], entries=[0])
                definition.index = 0x0CF3
                component = runtime.Component(123, world=world, strict=True)
                world.bind_script_component(123, component)
                instance = component.create(definition)
                if present is not None:
                    component.run_frame(0)
                    self.assertEqual(instance.vars[7].value(), 99)
                    expected = 0x0DE0000000007108 if present else None
                    self.assertEqual(world.display_name_override(123), expected)
                    instance.state(0).end(False)
                    self.assertIsNone(world.display_name_override(123))
                else:
                    with self.assertRaisesRegex(
                            runtime.UnsupportedScriptError, 'display name override target'):
                        component.run_frame(0)
                    self.assertNotIn(7, instance.vars)

    def test_junkenstein_wave_name_overrides_then_reveals_the_controller_name(self):
        world = scene.SceneWorld()
        world.possessors = set()
        world.bind(123, component92_present=True)
        world.bind(456)
        world.set_possessor(123, True)
        world.set_primary_possessor(456, 123)
        controller = graph([node(22, 'STU_B7D2C9C0', state=0, m_overrideName='0x0DE0000000007106',
                                 m_14EDDDA7={'$': 'STU_B5A0CAF0'})])
        controller.index = 0x0CF3
        body = graph([node(22, 'STU_B7D2C9C0', state=0, m_overrideName='0x0DE0000000004CCE',
                           m_14EDDDA7={'$': 'STU_DB742151', 'm_entity': {'$': 'STU_B5A0CAF0'}})])
        body.index = 0x0CBC
        for owner, definition in ((123, controller), (456, body)):
            vm = runtime.Component(owner, world=world, strict=True)
            world.bind_script_component(owner, vm)
            vm.create(definition, init=False).state(0).begin()
        self.assertEqual(world.display_name_override(123), 0x0DE0000000004CCE)
        world.unbind_script_component(456)
        self.assertEqual(world.display_name_override(123), 0x0DE0000000007106)
        world.unbind(123)
        self.assertIsNone(world.display_name_override(123))

    def test_on_fire_query_reads_linked_controller_and_preserves_missing_stats_output(self):
        world = scene.SceneWorld()
        stats = mock.Mock(return_value=None)
        world.bind(456, ALPHA)
        world.bind(789, on_fire_reader=stats)
        world.set_linked_controller(456, 789)
        instance = caller(world)
        query = {'$': 'STU_1ED4F039', 'm_entity': reference()}
        self.assertIs(instance.evaluate(query, initial=True), True)
        stats.return_value = False
        self.assertIs(instance.evaluate(query, initial=True), False)
        stats.return_value = True
        self.assertIs(instance.evaluate(query, initial=False), True)
        world.unbind(789)
        self.assertIs(instance.evaluate(query, initial=False), False)
        world.bind(789)
        world.set_linked_controller(456, 789)
        with self.assertRaises(runtime.UnsupportedScriptError):
            instance.evaluate(query)

    def test_scale_query_reads_the_bound_entity_at_evaluation_time(self):
        world = scene.SceneWorld()
        scale = mock.Mock(return_value=0.75)
        world.bind(456, ALPHA, scale_reader=scale)
        instance = caller(world)
        query = {'$': 'STU_FDBB0B67', 'm_entity': reference()}
        self.assertEqual(instance.evaluate(query), 0.75)
        scale.return_value = 1.5
        self.assertEqual(instance.evaluate(query), 1.5)
        world.unbind(456)
        self.assertEqual(instance.evaluate(query, initial=91), 91)
        world.bind(456, ALPHA)
        with self.assertRaises(runtime.UnsupportedScriptError):
            instance.evaluate(query)

    def test_junkenstein_stat_time_states_require_declared_absent_player_stats(self):
        for graph_id, pos, server_only, fields in (
            (0x0B8B, 66, True, {'m_stat': '0x08600000000005BF'}),
            (0x01A3, 14, False, {'m_D32CB484': ['0x086000000000037A']}),
        ):
            with self.subTest(graph_id=graph_id):
                self._check_stat_time_state(graph_id, pos, server_only, fields)

    def _check_stat_time_state(self, graph_id, pos, server_only, fields):
        row = node(pos, 'STU_441B50E4', state=0, m_targets={'$': 'STU_B5A0CAF0'}, **fields)
        row['server_only'] = server_only
        model = graph([row])
        model.index = graph_id
        for reader, absent in ((lambda: None, True), (lambda: False, False), (None, False)):
            with self.subTest(absent=absent, reader_bound=reader is not None):
                world = scene.SceneWorld()
                world.bind(123)
                world.bind(456, on_fire_reader=reader)
                world.set_linked_controller(123, 456)
                vm = runtime.Component(123, world=world, strict=True)
                instance = vm.create(model, init=False)
                instance.initialized = True
                state = instance.state(0)
                state.request(runtime.BEGIN)
                if absent:
                    vm.run_frame(1)
                    self.assertTrue(state.active)
                    state.request(runtime.FINISH)
                    vm.run_frame(2)
                    self.assertFalse(state.active)
                else:
                    with self.assertRaises(runtime.UnsupportedScriptError):
                        vm.run_frame(1)

    def test_junkenstein_npc_stays_out_of_the_authored_player_objective_stat_branch(self):
        model = scene.graph.graph(0x0BA4)
        for mode, reader, accepted in (
            (JUNKENSTEIN_MODES[0], lambda: None, True),
            (JUNKENSTEIN_MODES[1], lambda: None, True),
            (JUNKENSTEIN_MODES[0], lambda: False, False),
            (JUNKENSTEIN_MODES[0], None, False),
            (0, lambda: None, False),
        ):
            with self.subTest(mode=mode, accepted=accepted):
                world = scene.SceneWorld(mode=mode)
                world.bind(123)
                world.bind(456, on_fire_reader=reader)
                world.set_linked_controller(123, 456)
                vm = runtime.Component(123, world=world, strict=True)
                instance = vm.create(model, init=False)
                instance.initialized = True
                state = instance.node_state(model.node(10))
                state.request(runtime.BEGIN)
                if accepted:
                    vm.run_frame(1)
                    self.assertTrue(state.active)
                    self.assertIsNone(vm.entity_var(7059, create=False))
                else:
                    with self.assertRaisesRegex(runtime.UnsupportedScriptError, 'STU_ABB6C23E'):
                        vm.run_frame(1)

    def test_encounter_tag_is_visible_to_roster_and_removed_with_the_last_grant(self):
        world = scene.SceneWorld()
        world.bind(123, target_tags=())
        component = runtime.Component(123, world=world, strict=True)
        world.bind_script_component(123, component)
        model = graph([node(
            96, 'STU_E56BA8D8', state=0, m_31714AFC={'$': 'STU_B5A0CAF0'},
            m_targetTags={'$': 'STU_357DABBB', 'm_7D7C86A1': '0x0D60000000000422'},
        )])
        model.index = 0x01C7
        instance, other = (component.create(model, init=False) for _ in range(2))
        first, second = instance.state(0), other.state(0)
        query = {'$': 'STU_25ECB55F', 'm_88B0050A': '0x0D60000000000422',
                 'm_entity': {'$': 'STU_B5A0CAF0'}}
        filter_config = {'m_84065FD1': 1, 'm_1E14CCA8': 0x1000000, 'm_4EA686DB': 0x10,
                         'm_B486603E': model.node(96).field('m_targetTags')}
        world.participant_roster = lambda: ((123, 0x1000001, 0),)
        self.assertFalse(instance.evaluate(query))
        first.begin()
        second.begin()
        self.assertTrue(instance.evaluate(query))
        first.end(False)
        self.assertTrue(instance.evaluate(query))
        tag = expr.Asset(0x0D60000000000422)
        participants = world.query_participants(instance, filter_config, required_tag=tag)
        self.assertEqual(participants, (expr.Entity(123),))
        second.end(False)
        self.assertFalse(instance.evaluate(query))
        self.assertEqual(world.query_participants(instance, filter_config, required_tag=tag), ())
        first.begin()
        world.unbind_script_component(123, component)
        self.assertFalse(instance.evaluate(query))

    def test_movement_flags_publish_only_changed_outputs_and_follow_air_edges(self):
        world = scene.SceneWorld()
        body = mover.Mover((0, 0, 0))
        world.bind(123, movement_reader=body.snapshot)
        component = runtime.Component(123, world=world, strict=True)
        world.bind_script_component(123, component)
        model = graph([
            node(0, 'STU_96F1DBCF', state=0,
                 m_105DF1D6={'$': 'STUConfigVarDynamic', 'm_identifier': 10},
                 m_49F4B33C={'$': 'STUConfigVarDynamic', 'm_identifier': 11},
                 m_2102C541=plug((1, 'm_inPlug')), m_E37BC6C2=plug((2, 'm_inPlug'))),
            setter(1, 2),
            setter(2, 1),
        ])
        instance = component.create(model, init=False)
        instance.initialized = True
        state = instance.state(0)
        state.begin()
        self.assertEqual(state.payload(), {'value': 2})
        self.assertIsNone(instance.find_var(runtime.INSTANCE, 10, create=False))
        self.assertIs(instance.find_var(runtime.INSTANCE, 11).value(), True)

        body.flags_word = mover.JUMPED
        body.air_ticks = 1
        body.velocity = (0.0, 6.0, 0.0)
        body.throttles = (0, 127)
        state.tick()
        self.assertEqual(state.payload(), {'value': 2})
        component.run_frame(1)
        self.assertEqual(state.payload(), {'value': 0x835})
        self.assertIs(instance.find_var(runtime.INSTANCE, 10).value(), True)
        self.assertIs(instance.find_var(runtime.INSTANCE, 11).value(), False)
        self.assertEqual(instance.find_var(runtime.INSTANCE, 7).value(), 1)

        body.flags_word = 0
        body.velocity = (0.0, 0.0, 0.0)
        body.throttles = (0, 0)
        component.run_frame(2)
        self.assertEqual(state.payload(), {'value': 2})
        self.assertIs(instance.find_var(runtime.INSTANCE, 10).value(), False)
        self.assertEqual(instance.find_var(runtime.INSTANCE, 7).value(), 2)

    def test_entity_filter_applies_authored_literal_tags_after_the_bit_masks(self):
        tag = 0x0D60000000000069
        world = scene.SceneWorld()
        world.bind(123)
        world.bind(456, ALPHA, target_tags=(tag,))
        world.bind(457, BETA, target_tags=())
        world.array_filter_inputs = lambda entity: (True, 1 if entity == 456 else 2)
        instance = caller(world)
        world.bind_script_component(123, instance.component)
        tag_config = {'$': 'STU_357DABBB', 'm_7D7C86A1': f'0x{tag:016X}'}
        mask = {'$': 'STUConfigVarFilter', 'm_84065FD1': 0x3FFFFFFF}
        query = {'$': 'STU_AC829876'}
        for field, expected in (
                ('m_9C7BD8B8', (True, False)), ('m_B486603E', (True, False)),
                ('m_8B2A4DF3', (False, True))):
            with self.subTest(field=field):
                filter_config = {**mask, field: tag_config}
                actual = tuple(
                    instance.evaluate({**query, 'm_entity': reference(ref), 'm_filter': filter_config})
                    for ref in (ALPHA, BETA)
                )
                self.assertEqual(actual, expected)
        authored = {**mask, 'm_9C7BD8B8': {'$': 'STU_357DABBB', 'm_7D7C86A1': None},
                    'm_8B2A4DF3': tag_config, 'm_4EA686DB': 2}
        self.assertFalse(instance.evaluate({**query, 'm_entity': reference(BETA), 'm_filter': authored}))
        world.unbind(457)
        world.bind(457, BETA)
        filter_config = {**mask, 'm_8B2A4DF3': tag_config}
        with self.assertRaises(runtime.UnsupportedScriptError):
            instance.evaluate({**query, 'm_entity': reference(BETA), 'm_filter': filter_config})

    def test_game_mode_query_returns_the_active_scene_asset(self):
        mode = 0x023000000000002A
        config = {'$': 'STU_37F20D24', 'm_resourceKey': None}
        self.assertEqual(caller(scene.SceneWorld(mode=mode)).evaluate(config), expr.Asset(mode))
        with self.assertRaises(runtime.UnsupportedScriptError):
            caller(scene.SceneWorld()).evaluate(config)

    def test_junkenstein_death_filter_requires_enemy_and_hero_tag(self):
        world = scene.SceneWorld(mode=JUNKENSTEIN_MODES[1])
        tag = 0x0D600000000002FA
        world.bind(123, target_tags=(tag,))
        world.bind(456, ALPHA, target_tags=(tag,))
        world.bind(457, BETA, target_tags=())
        teams = {123: 0x1000000, 456: 0x800000, 457: 0x800000}
        world.array_filter_inputs = lambda entity: (True, teams[entity] | 1)
        world.set_entity_team_context(123, entity_team.Context(
            123, True, True, True,
            lookup=lambda entity: entity_team.Target(entity, entity_team.FilterBits(teams[entity])),
        ))
        instance = caller(world)
        instance.graph.index = 0x05AC
        world.bind_script_component(123, instance.component)
        filter_config = {
            '$': 'STUConfigVarFilter', 'm_84065FD1': 1, 'm_66747E7D': 2,
            'm_B486603E': {'$': 'STU_357DABBB', 'm_7D7C86A1': f'0x{tag:016X}'},
        }
        query = {'$': 'STU_AC829876', 'm_filter': filter_config}
        self.assertTrue(instance.evaluate({**query, 'm_entity': reference(ALPHA)}))
        self.assertFalse(instance.evaluate({**query, 'm_entity': reference(BETA)}))
        teams[456] = teams[123]
        self.assertFalse(instance.evaluate({**query, 'm_entity': reference(ALPHA)}))

    def test_junkenstein_death_search_skips_tagged_dead_bodies(self):
        world = scene.SceneWorld(mode=JUNKENSTEIN_MODES[1])
        tag = 0x0D600000000000F6
        world.bind(123, target_tags=(tag,))
        world.bind(456, target_tags=(tag,))
        for entity in (123, 456):
            world.set_entity_team_context(entity, entity_team.Context(entity, True, True, True))
        bits = {123: 0x11, 456: 1}
        world.array_filter_inputs = lambda entity: (True, bits[entity])
        instance = caller(world)
        instance.graph.index = 0x0CCD
        world.bind_script_component(123, instance.component)
        query = {
            '$': 'STU_B3E67A36', 'm_9C7BD8B8': {'$': 'STU_357DABBB', 'm_7D7C86A1': f'0x{tag:016X}'},
            'm_filter': {'$': 'STUConfigVarFilter', 'm_84065FD1': 1, 'm_4EA686DB': 16},
        }
        self.assertEqual(instance.evaluate(query), expr.Entity(456))
        bits[456] = 0x11
        self.assertEqual(instance.evaluate(query), expr.Entity(0))

    def test_roadhog_weapon_cleanup_allows_an_unallocated_temporary_entity(self):
        world = scene.SceneWorld(mode=JUNKENSTEIN_MODES[1])
        world.bind(123)
        component = runtime.Component(123, world=world, strict=True)
        world.bind_script_component(123, component)
        weapon = component.create(scene.graph.graph(0x01C7), init=False)
        for position in (33, 92):
            weapon.run_node(weapon.graph.node(position), None)
        weapon.write((runtime.INSTANCE, 669), expr.Entity(456))
        with self.assertRaises(runtime.UnsupportedScriptError):
            weapon.run_node(weapon.graph.node(33), None)

    def test_junkenstein_wave_broadcast_checks_the_received_boss_tag(self):
        world = scene.SceneWorld(mode=JUNKENSTEIN_MODES[0])
        tag = 0x0D600000000000F6
        world.bind(123, target_tags=(tag,))
        world.array_filter_inputs = lambda entity: (True, 1)
        instance = caller(world)
        world.bind_script_component(123, instance.component)
        boss = instance.component.create(scene.graph.graph(0x0CBC), init=False)
        query = boss.graph.node(96).field('m_condition')

        boss.write((runtime.INSTANCE, 6299), expr.Asset(tag))
        self.assertTrue(boss.evaluate(query))
        boss.write((runtime.INSTANCE, 6299), expr.Asset(tag + 1))
        self.assertFalse(boss.evaluate(query))
        boss.write((runtime.INSTANCE, 6299), (expr.Asset(tag),))
        self.assertTrue(boss.evaluate(query))
        boss.write((runtime.INSTANCE, 6299), (expr.Asset(tag), expr.Asset(tag + 1)))
        self.assertFalse(boss.evaluate(query))

    def test_witch_death_query_requires_another_ally_and_uses_the_authored_filter(self):
        world = scene.SceneWorld(mode=JUNKENSTEIN_MODES[0])
        model = scene.graph.graph(0x0071)
        component = runtime.Component(123, world=world, strict=True)
        tags = (0x0D600000000000F8,)
        bodies = (
            (123, 1, (0, 0, 0), tags),
            (124, 0, (2, 0, 0), tags),
            (125, 1, (30, 0, 0), ()),
            (126, 1, (12, 0, 0), tags),
        )
        teams = {entity: 0x800000 << team for entity, team, _, _ in bodies}
        world.array_filter_inputs = lambda entity: (True, teams[entity] | 1)

        def lookup(entity):
            return entity_team.Target(entity, entity_team.FilterBits(teams[entity]))

        for entity, _, position, target_tags in bodies:
            world.bind(entity, position=position, target_tags=target_tags)
            world.set_entity_team_context(entity, entity_team.Context(
                entity, True, True, True, lookup=lookup,
            ))
        world.bind_script_component(123, component)
        instance = component.create(model, init=False)
        required_tag = model.node(24).field('m_value')
        nearby = model.node(35).field('m_value')
        self.assertEqual(instance.evaluate(required_tag), expr.Entity(126))
        self.assertEqual(instance.evaluate(nearby), expr.Entity(126))
        world.unbind(126)
        self.assertEqual(instance.evaluate(required_tag), expr.Entity(0))
        self.assertEqual(instance.evaluate(nearby), expr.Entity(0))

    def test_symmetra_death_filter_checks_killer_team_tag_and_authored_predicate(self):
        world = scene.SceneWorld(mode=JUNKENSTEIN_MODES[1])
        owner, killer = 123, 456
        world.bind(owner)
        world.bind(killer, target_tags=(0x0D60000000000100,))
        teams = {owner: 0x1000000, killer: 0x800000}
        world.array_filter_inputs = lambda entity: (True, teams[entity] | 1)
        world.set_entity_team_context(owner, entity_team.Context(
            owner, True, True, True,
            lookup=lambda entity: entity_team.Target(entity, entity_team.FilterBits(teams[entity])),
        ))
        component = runtime.Component(owner, world=world, strict=True)
        target = runtime.Component(killer, world=world, strict=True)
        world.bind_script_component(owner, component)
        world.bind_script_component(killer, target)
        model = scene.graph.graph(0x01A3)
        instance = component.create(model, init=False)
        instance.write((runtime.INSTANCE, 392), expr.Entity(killer))
        query = model.node(11).field('m_condition')

        target.set(1216, 1)
        self.assertFalse(instance.evaluate(query))
        target.set(1216, 2)
        self.assertTrue(instance.evaluate(query))
        teams[killer] = teams[owner]
        self.assertFalse(instance.evaluate(query))

    def test_component92_query_distinguishes_presence_absence_and_unknown(self):
        config = {'$': 'STU_D3EB8D87', 'm_00903303': reference()}
        for present in (False, True, None):
            with self.subTest(present=present):
                world = scene.SceneWorld()
                world.bind(456, ALPHA, component92_present=present)
                instance = caller(world)
                if present is None:
                    with self.assertRaises(runtime.UnsupportedScriptError):
                        instance.evaluate(config)
                else:
                    self.assertIs(instance.evaluate(config, initial=91), present)
                    world.unbind(456)
                    self.assertIs(instance.evaluate(config, initial=91), False)

    def test_reaper_cinematic_time_scale_uses_component_frames(self):
        authored = scene.graph.graph(0x0CE0).node(3)
        model = graph([node(3, authored.cls, state=0, m_17D12F6B=authored.field('m_17D12F6B'))])
        model.index = 0x0CE0
        instance = runtime.Component(123, world=scene.SceneWorld(), strict=True).create(model, init=False)
        instance.initialized = True
        instance.component.frame = 4004
        state = instance.state(0)
        state.begin()
        self.assertEqual(state.payload(), {'which': True, 'value': 4005})
        instance.component.frame = 4350
        state.end(False)
        self.assertEqual(state.payload(), {'which': False, 'value': 4351})

    def test_deployable_count_tracks_owner_definition_and_retirement(self):
        world = scene.SceneWorld()
        world.bind(456, ALPHA)
        world.bind(457, BETA)
        instance = caller(world)
        definition = 0x0400000000002658
        config = {
            '$': 'STU_967463AB',
            'm_92D12BE6': reference(),
            'm_C8655030': {'$': 'STU_8556841E', 'm_entityDef': f'0x{definition:016X}'},
        }
        world.set_owned_deployables(456, ())
        self.assertEqual(instance.evaluate(config), 0)

        world.bind(500, definition=definition)
        world.bind(501, definition=definition + 1)
        world.bind(502, definition=definition)
        world.set_owned_deployables(456, (500, 501, 500))
        world.set_owned_deployables(457, (502,))
        self.assertEqual(instance.evaluate(config), 1)
        world.unbind(500)
        world.bind(500, definition=definition)
        self.assertEqual(instance.evaluate(config), 0)
        world.unbind(456)
        self.assertEqual(instance.evaluate(config, initial=91), 91)
        world.bind(456, ALPHA)
        with self.assertRaises(runtime.UnsupportedScriptError):
            instance.evaluate(config)

    def test_pet_owner_query_returns_typed_zero_only_for_declared_absence(self):
        config = {'$': 'STU_F76C486C', 'm_D63CBEC2': {'$': 'STU_B5A0CAF0'}}
        for absent in (False, True):
            with self.subTest(absent=absent):
                world = scene.SceneWorld()
                world.bind(123, pet_component_absent=absent)
                world.set_possessions(possessors=())
                instance = caller(world)
                world.bind_script_component(123, instance.component)
                world.set_entity_query_context(123)
                if absent:
                    self.assertEqual(instance.evaluate(config, initial=41), expr.Entity(0))
                else:
                    with self.assertRaises(runtime.UnsupportedScriptError):
                        instance.evaluate(config)

    def test_linked_controller_does_not_follow_primary_possession_or_reused_ids(self):
        world = scene.SceneWorld()
        body, possessor, linked = 456, 457, 458
        world.bind(body, ALPHA)
        world.bind(possessor)
        world.bind(linked)
        world.set_possessions(possessors=(possessor,), primary=((body, possessor),))
        cfg = {'$': 'STU_F7262B36', 'm_801B298F': reference()}
        with self.assertRaises(runtime.UnsupportedScriptError):
            caller(world).evaluate(cfg)
        world.set_linked_controller(body, linked)
        instance = caller(world)
        self.assertEqual(instance.evaluate(cfg), expr.Entity(linked))
        world.unbind(linked)
        world.bind(linked)
        self.assertEqual(instance.evaluate(cfg, initial=41), 41)

    def test_hero_creation_preserves_authored_spawn_inputs(self):
        owner, body, hero = 123, 456, 0x02E0000000000175
        creator = mock.Mock()
        world = scene.SceneWorld(hero_creator=creator)
        world.bind(owner)
        creator.side_effect = lambda *args, **kwargs: world.bind(body, definition=int(DEF, 16))
        output = {'$': 'STUConfigVarDynamic', 'm_identifier': 38}
        model = graph([
            node(0, 'STUStatescriptEntry', m_outPlug=plug((1, 'm_inPlug'))),
            node(1, 'STUStatescriptActionCreateEntity',
                 m_A1A715A6={'$': 'STUConfigVarHero', 'm_hero': f'0x{hero:016X}'},
                 m_position={'$': 'STUConfigVarVec3', 'm_value': {'X': 1, 'Y': 2, 'Z': 3}},
                 m_3EBF988B={'$': 'STU_BD6CC023', 'm_6CE7A900': 1},
                 m_1C558638={'$': 'STUConfigVarFloat', 'm_value': -180},
                 m_F8E56B94={'$': 'STUConfigVarBool', 'm_value': 1},
                 m_DE3B4F01={'$': 'STUConfigVarInt', 'm_value': 0}, m_E95B313B=output),
        ], entries=[0])
        component = runtime.Component(owner, world=world, strict=True)
        world.bind_script_component(owner, component)
        instance = component.create(model)
        component.run_frame(0)
        creator.assert_called_once_with(hero, expr.Vec3(1, 2, 3), 1, -180.0, (True, 0))
        self.assertEqual(instance.evaluate(output), expr.Entity(body))

        world.unbind(body)
        creator.reset_mock()
        marker = 789
        world.bind(marker, position=(8, 0, -6))
        marker_config = {'$': 'STUConfigVarDynamic', 'm_identifier': 8370}
        instance.write((runtime.INSTANCE, 8370), expr.Entity(marker))
        model.node(1).fields.update(
            m_position=marker_config,
            m_D7F85181={'$': 'STU_8556841E', 'm_entityDef': '0x0400000000001959'},
            m_246C47AF={'$': 'STUConfigVarSkinTheme', 'm_skinTheme': '0x0A5000000000195C'},
        )
        instance.run_node(model.node(1), None)
        creator.assert_called_once_with(
            hero, expr.Vec3(8, 0, -6), 1, -180.0, (True, 0),
            controller_definition=0x0400000000001959, skin_theme=0x0A5000000000195C,
        )
        self.assertEqual(instance.evaluate(output), expr.Entity(body))
        self.assertEqual(instance.evaluate(marker_config), expr.Entity(marker))

        world.unbind(marker)
        creator.reset_mock()
        with self.assertRaisesRegex(runtime.UnsupportedScriptError, 'entity creation transform'):
            instance.run_node(model.node(1), None)
        creator.assert_not_called()

    def test_create_entity_writes_only_a_new_bound_result_and_continues(self):
        for accepted in (False, True):
            with self.subTest(accepted=accepted):
                world = scene.SceneWorld()
                owner, entity, definition = 123, 456, int(DEF, 16)
                world.bind(owner)
                request = []

                def create(body, position, *, request=request, accepted=accepted, world=world, entity=entity):
                    request.append((body, position))
                    if accepted:
                        return world.bind(entity, position=tuple(position), definition=body)
                    return None

                world.entity_creator = create
                output = {'$': 'STUConfigVarDynamic', 'm_identifier': 41}
                model = graph([
                    node(0, 'STUStatescriptEntry', m_outPlug=plug((1, 'm_inPlug'))),
                    node(1, 'STUStatescriptActionCreateEntity',
                         m_entityDef={'$': 'STU_8556841E', 'm_entityDef': DEF},
                         m_position={'$': 'STUConfigVarVec3', 'm_value': {'X': 1, 'Y': 2, 'Z': 3}},
                         m_E95B313B=output, m_outPlug=plug((2, 'm_inPlug'))),
                    setter(2, 19),
                ], entries=[0])
                component = runtime.Component(owner, world=world, strict=True)
                world.bind_script_component(owner, component)
                instance = component.create(model, values={(runtime.INSTANCE, 41): 7})
                component.run_frame(0)
                self.assertEqual(request, [(definition, expr.Vec3(1, 2, 3))])
                self.assertEqual(instance.evaluate(output), expr.Entity(entity) if accepted else 7)
                self.assertEqual(instance.find_var(runtime.INSTANCE, 7).value(), 19)

    def test_entity_position_config_accepts_bound_origin_and_nonzero_vector(self):
        world = scene.SceneWorld()
        world.bind(456, ALPHA, position=(0, 0, 0))
        cfg = {'$': 'STU_71A87E92', 'm_entity': reference()}
        instance = caller(world)
        self.assertEqual(instance.evaluate(cfg, initial=41), expr.Vec3(0, 0, 0))
        world.unbind(456)
        self.assertEqual(instance.evaluate(cfg, initial=41), 41)
        cfg['m_entity'] = {'$': 'STUConfigVarVec3', 'm_value': {'X': 9, 'Y': 2, 'Z': 3}}
        self.assertEqual(instance.evaluate(cfg), expr.Vec3(9, 2, 3))
        cfg['m_entity']['m_value'] = {}
        self.assertEqual(instance.evaluate(cfg, initial=41), 41)

    def test_entity_position_query_reads_live_coordinates_until_binding_retires(self):
        world = scene.SceneWorld()
        target = 0x80000034
        position = [1.0, 2.0, 3.0]
        world.bind(target, ALPHA, position_reader=lambda: tuple(position))
        cfg = {'$': 'STU_71A87E92', 'm_entity': reference()}
        instance = caller(world)
        self.assertEqual(instance.evaluate(cfg), expr.Vec3(1, 2, 3))
        position[2] = 7.0
        self.assertEqual(instance.evaluate(cfg), expr.Vec3(1, 2, 7))
        world.unbind(target)
        self.assertEqual(instance.evaluate(cfg, initial=41), 41)

    def test_heading_query_uses_bound_placement_rotation_in_degrees(self):
        marker = placement(0, client_id=1)
        marker['rotation'] = [0, 0.7071067811865476, 0, 0.7071067811865476]
        world = scene.SceneWorld.from_map(manifest(marker), entity_start=34)
        cfg = {'$': 'STU_C8C4C20D', 'm_entity': reference()}
        instance = caller(world)
        self.assertAlmostEqual(instance.evaluate(cfg), 90.0, places=4)
        world.unbind(0xC0000001)
        self.assertEqual(instance.evaluate(cfg, initial=41), 41)
        marker['rotation'] = [0, 1, 0, 0]
        world = scene.SceneWorld.from_map(manifest(marker), entity_start=34)
        self.assertEqual(caller(world).evaluate(cfg), 0.0)

    def test_spawn_direction_uses_bound_placement_forward_basis(self):
        marker = placement(0, client_id=1)
        marker['rotation'] = [0.5, 0.5, 0.5, 0.5]
        world = scene.SceneWorld.from_map(manifest(marker), entity_start=34)
        cfg = {'$': 'STU_021D01CD', 'm_entity': reference()}
        instance = caller(world)
        self.assertEqual(instance.evaluate(cfg), expr.Vec3(1, 0, 0))
        world.unbind(0xC0000001)
        self.assertIsNone(instance.evaluate(cfg))

    def test_fieldless_body_state_preserves_base_lifecycle_in_strict_execution(self):
        model = graph([
            node(0, 'STUStatescriptEntry', m_outPlug=plug((1, 'm_beginPlug'))),
            node(1, 'STU_90C3BCEA', state=0, m_onBeginPlug=plug((2, 'm_inPlug')),
                 m_onEndPlug=plug((3, 'm_inPlug'))),
            setter(2, 19),
            setter(3, 41),
        ], entries=[0])
        component = runtime.Component(123, strict=True)
        instance = component.create(model)
        component.run_frame(0)
        self.assertTrue(instance.state(0).active)
        self.assertEqual(instance.find_var(runtime.INSTANCE, 7).value(), 19)
        instance.state(0).end(False)
        self.assertFalse(instance.state(0).active)
        self.assertEqual(instance.find_var(runtime.INSTANCE, 7).value(), 41)

    def test_reaper_spawn_base_state_owns_its_authored_subgraph(self):
        world = scene.SceneWorld()
        component = runtime.Component(123, world=world, strict=True)
        world.bind(123)
        world.bind_script_component(123, component)
        world.set_property_modifiers(123, modifiers.NumericModifiers())
        definition = scene.graph.graph(0x0CE0)
        instance = component.create(definition, init=False)
        instance.initialized = True
        parent = instance.state(definition.node(39).state)
        parent.request(runtime.BEGIN)
        component.run_frame(0)
        self.assertIs(type(parent), runtime.State)
        self.assertEqual(parent.payload(), {})
        self.assertEqual({state.node.pos for state in instance.states.values() if state.active},
                         {29, 30, 39, 40, 41, 42})
        for pos in (29, 30):
            child = definition.node(pos)
            self.assertEqual(component.state_registries[child.cls], [instance.state(child.state)])
        parent.end(False)
        self.assertFalse(any(state.active for state in instance.states.values()))
        self.assertFalse(any(component.state_registries.values()))

    def test_health_query_reads_current_combat_pools_and_retains_failure_destination(self):
        world = scene.SceneWorld()
        vitals = Vitals(456, 200, 50, 25)
        world.bind(456, ALPHA, health=lambda: vitals.total)
        cfg = {'$': 'STU_9B7648C3', 'm_entity': reference()}
        instance = caller(world)
        self.assertEqual(instance.evaluate(cfg), 275.0)
        vitals.damage(25)
        self.assertEqual(instance.evaluate(cfg), 250.0)
        vitals.damage(1000)
        self.assertEqual(instance.evaluate(cfg, initial=91), 0.0)
        world.unbind(456)
        self.assertEqual(instance.evaluate(cfg, initial=91), 91)

    def test_health_pool_queries_read_current_health_and_capacity_separately(self):
        world = scene.SceneWorld()
        vitals = Vitals(456, 200, 50, 25)
        world.bind(456, ALPHA, health_pool=vitals.now.__getitem__, pool_capacity=vitals.most.__getitem__)
        cfg = {
            '$': 'STU_783B4CF0', 'm_entity': reference(),
            'm_7E8B666C': {'$': 'STU_297E1A30', 'm_7E8B666C': 0},
        }
        capacity = {**cfg, '$': 'STU_68883EAC'}
        instance = caller(world)
        self.assertEqual(instance.evaluate(cfg), 200.0)
        self.assertEqual(instance.evaluate(capacity), 200.0)
        vitals.damage(500)
        self.assertEqual(instance.evaluate(cfg), 0.0)
        self.assertEqual(instance.evaluate(capacity), 200.0)
        world.unbind(456)
        self.assertEqual(instance.evaluate(cfg, initial=91), 0.0)

    def test_normal_pool_totals_read_only_the_declared_body_records(self):
        world = scene.SceneWorld()
        vitals = Vitals(456, 200, 50, 25)
        world.bind(456, ALPHA, health=lambda: vitals.total + 100, normal_health=vitals.normal_health)
        instance = caller(world)
        current = {'$': 'STU_32E6E4D8', 'm_entity': reference()}
        maximum = {**current, '$': 'STU_7D7D2470'}
        self.assertEqual(instance.evaluate(current), 275.0)
        vitals.damage(20)
        self.assertEqual(instance.evaluate(current), 255.0)
        self.assertEqual(instance.evaluate(maximum), 275.0)
        self.assertEqual(instance.evaluate({**current, 'm_entity': None}, initial=91), 0.0)

    def test_common_body_startup_preserves_damage_on_the_preallocated_pool(self):
        for initial in (250, 100):
            with self.subTest(initial=initial):
                world = scene.SceneWorld()
                vitals = Vitals(456, 250)
                vitals.damage(40)
                world.bind(456, pool_capacity=vitals.most.__getitem__)
                component = runtime.Component(456, world=world, strict=True)
                model = graph([node(1, 'STUStatescriptStateHealthPool', state=0, m_7E8B666C=0,
                                    m_FA3ECC43={'$': 'STU_B5A0CAF0'},
                                    m_amount={'$': 'STUConfigVarInt', 'm_value': 250},
                                    m_BCFDD2E9={'$': 'STUConfigVarInt', 'm_value': initial})])
                model.index = 0x0033
                state = component.create(model, init=False).state(0)
                if initial == 250:
                    state.begin()
                    self.assertTrue(state.active)
                    state.end(False)
                else:
                    with self.assertRaises(runtime.UnsupportedScriptError):
                        state.begin()
                self.assertEqual(vitals.total, 210.0)

    def test_junkenstein_self_damage_runs_once_and_only_hits_the_bound_owner(self):
        world = scene.SceneWorld()
        vitals = Vitals(456, 200)
        damage = mock.Mock(side_effect=vitals.damage)
        world.bind(456, health=lambda: vitals.total, self_damage=damage)
        world.bind(457, ALPHA)
        component = runtime.Component(456, world=world, strict=True)
        world.bind_script_component(456, component)
        owner = {'$': 'STU_B5A0CAF0'}
        model = graph([node(46, 'STUStatescriptStateModifyHealth', state=0, m_FA3ECC43=owner,
                            m_modifyHealth={'m_93CDFD2F': 1, 'm_instigator': owner, 'm_724CBA57': owner,
                                            'm_amount': {'$': 'STUConfigVarInt', 'm_value': 1000}})])
        model.index = 0x0C89
        instance = component.create(model, init=False)
        state = instance.state(0)
        state.node.fields['m_FA3ECC43'] = reference()
        self.assertIs(world.modify_health(state, 0), NotImplemented)
        damage.assert_not_called()
        state.node.fields['m_FA3ECC43'] = owner
        state.begin()
        component.run_frame(1)
        damage.assert_called_once_with(1000.0)
        self.assertEqual(vitals.total, 0)

    def test_junkenstein_contact_refreshes_and_takes_the_transition_after_100_ms(self):
        world = scene.SceneWorld()
        world.bind(456)
        world.contact_effect = mock.Mock(return_value=True)
        component = runtime.Component(456, world=world, strict=True)
        world.bind_script_component(456, component)
        model = graph([
            node(49, 'STU_23A00A53', state=0,
                 m_53FCEDBF={'$': 'STUConfigVarDynamic', 'm_identifier': 375},
                 m_63CB0D07={'$': 'STU_B5A0CAF0'},
                 m_contactSet={'$': 'STUConfigVarContactSet', 'm_contactSet': '0x0120000000000015'},
                 m_transitionPlug=[plug((51, 'm_inPlug'))]),
            node(51, 'STU_4E2BCAAB', m_ADEB6E05=0,
                 m_AA6E611E={'$': 'STUConfigVarFloat', 'm_value': 0.1}, m_truePlug=plug((50, 'm_inPlug'))),
            setter(50, 19),
        ])
        model.index = 0x0C89
        instance = component.create(model)
        state = instance.state(0)
        state.begin()
        world.contact_effect.assert_called_once_with(state, 456, 0x0120000000000015, ())
        instance.write((runtime.INSTANCE, 375), (expr.Entity(457),))
        component.run_frame(6)
        world.contact_effect.assert_called_with(state, 456, 0x0120000000000015, (expr.Entity(457),))
        self.assertEqual(instance.vars[7].value(), 19)
        self.assertEqual(state.ended_at, 100)

    def test_junkenstein_explosion_applies_the_authored_amount_to_the_selected_victim(self):
        world = scene.SceneWorld()
        world.bind(456)
        world.bind(457)
        world.explosion_damage = mock.Mock(return_value=True)
        component = runtime.Component(456, world=world, strict=True)
        world.bind_script_component(456, component)
        owner = {'$': 'STU_B5A0CAF0'}
        model = graph([
            node(38, 'STUStatescriptStateModifyHealth', state=0,
                 m_FA3ECC43={'$': 'STUConfigVarDynamic', 'm_identifier': 375},
                 m_modifyHealth={'m_93CDFD2F': 1, 'm_instigator': owner, 'm_724CBA57': owner,
                                 'm_amount': {'$': 'STUConfigVarDynamic', 'm_identifier': 476}},
                 m_onFinishedPlug=plug((39, 'm_inPlug'))),
            setter(39, 19),
        ])
        model.index = 0x0C89
        instance = component.create(model)
        instance.write((runtime.INSTANCE, 375), expr.Entity(457))
        instance.write((runtime.INSTANCE, 476), 29.5)
        instance.state(0).request(runtime.BEGIN)
        component.run_frame(0)
        component.run_frame(1)
        world.explosion_damage.assert_called_once_with(456, 457, 29.5)
        self.assertEqual(instance.vars[7].value(), 19)

    def test_gate_stop_animation_follows_output_when_there_is_no_handle(self):
        model = graph([
            node(11, 'STU_6E18DEA0',
                 m_EDB2BD2A={'$': 'STUConfigVarDynamic', 'm_identifier': 2254},
                 m_outPlug=plug((19, 'm_inPlug'))),
            setter(19, 31),
        ])
        model.index = 0x0CA7
        component = runtime.Component(456, strict=True)
        instance = component.create(model)
        instance.run_node(model.node(11), None)
        self.assertEqual(instance.vars[7].value(), 31)

    def test_entity_tag_query_distinguishes_present_absent_and_unknown_tags(self):
        world = scene.SceneWorld()
        tag = 0x0D60000000000A6E
        cfg = {'$': 'STU_25ECB55F', 'm_88B0050A': f'0x{tag:016X}', 'm_entity': reference()}
        instance = caller(world)
        self.assertIs(instance.evaluate(cfg), False)
        world.bind(456, ALPHA, target_tags=(tag,))
        self.assertIs(instance.evaluate(cfg), True)
        world.unbind(456)
        world.bind(456, ALPHA, target_tags=())
        self.assertIs(instance.evaluate(cfg), False)
        world.unbind(456)
        world.bind(456, ALPHA)
        with self.assertRaises(runtime.UnsupportedScriptError):
            instance.evaluate(cfg)

    def test_scene_queries_track_forward_possession_and_body_retirement(self):
        world = scene.SceneWorld()
        owner, body = 123, 456
        world.bind(owner)
        world.bind(body, definition=int(DEF, 16))
        world.set_possessions(possessors=(owner,), primary=((body, owner),))
        world.set_entity_query_context(owner)
        instance = caller(world)
        possession = {'$': 'STU_0AC15F78', 'm_entity': {'$': 'STU_B5A0CAF0'}}
        cfg = {'$': 'STU_1A3FA5B0', 'm_entity': possession}
        self.assertEqual(instance.evaluate(cfg), expr.Asset(0))
        world.set_current_possession(owner, body)
        self.assertEqual(instance.evaluate(cfg), expr.Asset(int(DEF, 16)))
        world.unbind(body)
        world.bind(body, definition=int(DEF, 16) + 1)
        self.assertEqual(instance.evaluate(cfg), expr.Asset(0))
        world.set_current_possession(owner, body)
        self.assertEqual(instance.evaluate(cfg), expr.Asset(int(DEF, 16) + 1))
        world.set_possessor(owner, False)
        self.assertEqual(instance.evaluate(cfg), expr.Asset(0))

    def test_native_trace_action_follows_authored_output_in_strict_execution(self):
        model = graph([
            node(0, 'STUStatescriptEntry', m_outPlug=plug((1, 'm_inPlug'))),
            node(1, 'STU_6E5C5F66', m_outPlug=plug((2, 'm_inPlug'))),
            setter(2, 19),
        ], entries=[0])
        component = runtime.Component(123, strict=True)
        instance = component.create(model)
        component.run_frame(0)
        self.assertEqual(instance.find_var(runtime.INSTANCE, 7).value(), 19)

    def test_tutorial_mode_condition_uses_the_declared_scene_mode(self):
        cfg = {'$': 'STU_A77AC2AC'}
        for mode, expected in ((0x0230000000000019, True), (0x0230000000000003, False)):
            self.assertIs(caller(scene.SceneWorld(mode=mode)).evaluate(cfg), expected)

    def test_hero_resource_query_reads_the_current_body_binding(self):
        world = scene.SceneWorld()
        target = 0x80000034
        hero = 0x02E0000000000175
        world.bind(target, ALPHA, hero=hero)
        cfg = {'$': 'STU_93DCDDFD', 'm_entity': reference()}
        instance = caller(world)
        self.assertEqual(instance.evaluate(cfg), expr.Asset(hero))
        world.unbind(target)
        self.assertEqual(instance.evaluate(cfg, initial=41), 41)
        world.bind(target, ALPHA, hero=0)
        self.assertEqual(instance.evaluate(cfg), expr.Asset(0))

    def test_definition_query_reads_live_binding_and_clears_on_id_reuse(self):
        world = scene.SceneWorld.from_map(manifest(placement(0, client_id=1)), entity_start=34)
        target = 0xC0000001
        cfg = {'$': 'STU_1A3FA5B0', 'm_entity': reference()}
        instance = caller(world)
        self.assertEqual(instance.evaluate(cfg), expr.Asset(int(DEF, 16)))
        world.unbind(target)
        self.assertEqual(instance.evaluate(cfg), expr.Asset(0))
        body = 0x04000000000016BE
        world.bind(target, ALPHA, definition=body)
        self.assertEqual(instance.evaluate(cfg), expr.Asset(body))
        world.unbind(target)
        world.bind(target, ALPHA)
        with self.assertRaises(runtime.UnsupportedScriptError):
            instance.evaluate(cfg)

    def test_gate_query_uses_bound_definition_tags_with_null_placed_tags(self):
        data = manifest(placement(0, client_id=0), placement(1, client_id=0, value=BETA))
        tag = '0x0D6000000000030E'
        data['definitions'][DEF]['components']['entries'].append({
            'value': {'$': 'STUTargetTagComponent', 'm_targetTags': [tag]},
        })
        data['entities'][0]['components'].append({
            'class': 'STUTargetTagInstanceData', 'fields': {'m_targetTags': None},
        })
        world = scene.SceneWorld.from_map(data, entity_start=50, server_placeables=[50])
        model = graph([node(0, 'STU_160F2578', state=0,
                            m_9C7BD8B8={'$': 'STU_357DABBB', 'm_7D7C86A1': tag},
                            m_4AEEE6A1={'$': 'STUConfigVarDynamic', 'm_identifier': 8439,
                                        'm_60DB8F99': 1})])
        component = runtime.Component(123, world=world, strict=True)
        instance = component.create(model, init=False)
        instance.state(0).begin()
        self.assertEqual(component.entity_var(8439).value(), (expr.Entity(0x80000032),))
        world.unbind(0x80000032)
        self.assertIs(world.static_target_tag(int(tag, 16)), NotImplemented)
        duplicate = scene.SceneWorld.from_map(data, entity_start=50, server_placeables=[50, 51])
        self.assertIs(duplicate.static_target_tag(int(tag, 16)), NotImplemented)
        expected = (expr.Entity(0x80000032), expr.Entity(0x80000033))
        self.assertEqual(duplicate.static_tag_targets(int(tag, 16)), expected)
        self.assertEqual(duplicate.static_tag_targets(None), expected)
        self.assertEqual(duplicate.static_tag_targets(0x0D60000000000001), ())

    def test_witch_target_tag_query_applies_exclusion_to_placed_and_dynamic_tags(self):
        required = 0x0D60000000000350
        excluded = 0x0D60000000000353
        data = manifest(placement(0, client_id=0), placement(1, client_id=0, value=BETA))
        data['definitions'][DEF]['components']['entries'].append({
            'value': {'$': 'STUTargetTagComponent', 'm_targetTags': None},
        })
        for record, tags in zip(data['entities'], ((required,), (required, excluded)), strict=True):
            record['components'].append({
                'class': 'STUTargetTagInstanceData',
                'fields': {'m_targetTags': [f'0x{tag:016X}' for tag in tags]},
            })
        world = scene.SceneWorld.from_map(data, entity_start=50, server_placeables=[50, 51])
        world.bind(123)
        world.bind(124, target_tags=(required,))
        world.bind(125, target_tags=(required, excluded))
        world.bind(126, target_tags=())
        component = runtime.Component(123, world=world, strict=True)
        world.bind_script_component(123, component)
        instance = component.create(graph(), init=False)

        self.assertEqual(world.targets_with_tag(instance, required, excluded_tag=excluded),
                         (expr.Entity(0x80000032), expr.Entity(124)))
        world.unbind(124)
        self.assertEqual(world.targets_with_tag(instance, required, excluded_tag=excluded),
                         (expr.Entity(0x80000032),))

    def test_junkenstein_boss_action_collects_only_bound_placements_with_required_tag(self):
        data = manifest(placement(0, client_id=0), placement(1, client_id=0, value=BETA))
        tag = '0x0D60000000000321'
        data['definitions'][DEF]['components']['entries'].append({
            'value': {'$': 'STUTargetTagComponent', 'm_targetTags': None},
        })
        data['entities'][1]['components'].append({
            'class': 'STUTargetTagInstanceData', 'fields': {'m_targetTags': [tag]},
        })
        world = scene.SceneWorld.from_map(data, entity_start=50, server_placeables=[50, 51])
        action = node(9, 'STU_42578342', m_ADEB6E05=1,
                      m_B486603E={'$': 'STU_357DABBB', 'm_7D7C86A1': tag},
                      m_4AEEE6A1={'$': 'STUConfigVarDynamic', 'm_identifier': 536})
        model = graph([action])
        model.index = 0x0C95
        instance = runtime.Component(123, world=world, strict=True).create(model, init=False)
        instance.run_node(model.node(9), None)
        self.assertEqual(instance.vars[536].value(), (expr.Entity(0x80000033),))
        world.unbind(0x80000033)
        instance.run_node(model.node(9), None)
        self.assertEqual(instance.vars[536].value(), ())

    def test_junkenstein_participant_query_preserves_order_and_filters_candidates(self):
        world = scene.SceneWorld()
        owner = runtime.GAME_MODE_ENTITY
        world.bind(owner)
        for entity in range(0xA0000001, 0xA0000006):
            world.bind(entity)
        world.participant_roster = lambda: (
            (0xA0000005, 0x840001, 0), (0xA0000002, 0x1040001, 0),
            (0xA0000003, 0x840011, 0), (0xA0000004, 0x840001, 4),
            (0xA0000001, 0x840001, 0), (0xA0000006, 0x840001, 0),
        )
        filter_config = {'$': 'STUConfigVarFilter', 'm_84065FD1': 0x40000,
                         'm_1E14CCA8': 0x800000, 'm_4EA686DB': 0x10}
        model = graph([node(0, 'STU_160F2578', state=0, m_filter=filter_config,
                            m_4AEEE6A1={'$': 'STUConfigVarDynamic', 'm_identifier': 8618})])
        component = runtime.Component(owner, world=world, strict=True)
        world.bind_script_component(owner, component)
        instance = component.create(model, init=False)
        instance.initialized = True
        instance.state(0).request(runtime.BEGIN)
        component.run_frame(0)
        self.assertEqual(instance.vars[8618].value(), (expr.Entity(0xA0000005), expr.Entity(0xA0000001)))
        enemy_filter = {**filter_config, 'm_84065FD1': 1, 'm_1E14CCA8': 0x1000000}
        action = graph([node(0, 'STU_42578342', m_filter=enemy_filter, m_ADEB6E05=1,
                             m_4AEEE6A1={'$': 'STUConfigVarDynamic', 'm_identifier': 8681})])
        action_instance = component.create(action, init=False)
        action_instance.run_node(action.node(0), None)
        self.assertEqual(action_instance.vars[8681].value(), (expr.Entity(0xA0000002),))
        world.unbind(0xA0000002)
        action_instance.run_node(action.node(0), None)
        self.assertEqual(action_instance.vars[8681].value(), ())
        tag = 0x0D60000000000326
        enemy_filter['m_B486603E'] = {'$': 'STU_357DABBB', 'm_7D7C86A1': f'0x{tag:016X}'}
        model = graph([node(0, 'STU_160F2578', state=0, m_filter=enemy_filter,
                            m_4AEEE6A1={'$': 'STUConfigVarDynamic', 'm_identifier': 8724})])
        world.bind(0xA0000002, target_tags=(tag,))
        world.unbind(0xA0000003)
        world.bind(0xA0000003, target_tags=())
        world.participant_roster = lambda: ((0xA0000003, 0x1000001, 0), (0xA0000002, 0x1000001, 0))
        instance = component.create(model, init=False)
        instance.state(0).begin()
        self.assertEqual(instance.vars[8724].value(), (expr.Entity(0xA0000002),))

    def test_junkenstein_wave_roster_updates_on_death_and_retirement(self):
        world = scene.SceneWorld(mode=JUNKENSTEIN_MODES[0])
        world.bind(runtime.GAME_MODE_ENTITY)
        world.bind(123)
        world.bind(124)
        filters = {123: 1, 124: 1}
        world.array_filter_inputs = lambda entity: (True, filters[entity])
        component = runtime.Component(runtime.GAME_MODE_ENTITY, world=world, strict=True)
        world.bind_script_component(component.entity, component)
        model = scene.graph.graph(0x0CBA)
        instance = component.create(model, init=False)
        instance.initialized = True
        instance.write((runtime.INSTANCE, 8399), (expr.Entity(123), expr.Entity(124)))
        instance.node_state(model.node(6)).begin()
        component.run_frame(0)

        filters[123] |= 0x10
        world.notify_entity_filter_change(123)
        component.run_frame(16)
        self.assertEqual(instance.vars[8399].value(), (expr.Entity(124),))
        world.unbind(124)
        component.run_frame(32)
        self.assertEqual(instance.vars[8399].value(), ())

    def test_junkenstein_spawn_selection_skips_recent_slots_and_expires_history(self):
        candidates = {'$': 'STUConfigVarDynamic', 'm_identifier': 8402}
        output = {'$': 'STUConfigVarDynamic', 'm_identifier': 8370}
        model = graph([node(0, 'STU_5E805B5A', state=0, m_814F8EC3=candidates, m_C51AF683=output,
                            m_82317B5F={'$': 'STUConfigVarInt', 'm_value': 1})])
        model.index = 0x0CBA
        component = runtime.Component(runtime.GAME_MODE_ENTITY, strict=True)
        instance = component.create(model, init=False)
        state = instance.state(0)
        positions = tuple(expr.Vec3(x, 0, 0) for x in range(3))
        instance.write((runtime.INSTANCE, 8402), positions)
        state.begin()
        with mock.patch.object(component.rng, 'randrange', return_value=0):
            for now, expected in ((0, 0), (16, 1), (32, 2), (48, 0), (1048, 1), (1049, 0)):
                component.now = now
                self.assertTrue(state.plug_enter('m_F4DD996D'))
                self.assertEqual(instance.vars[8370].value(), positions[expected])
            instance.write((runtime.INSTANCE, 8402), ())
            state.plug_enter('m_F4DD996D')
            self.assertIsNone(instance.vars[8370].value())
            instance.write((runtime.INSTANCE, 8402), positions)
            state.end(False)
            state.begin()
            with mock.patch.object(component.rng, 'randrange', side_effect=(2, 0, 1, 0)):
                for now in (2000, 2016, 2032):
                    component.now = now
                    state.plug_enter('m_F4DD996D')
                instance.write((runtime.INSTANCE, 8402), positions[:2])
                state.plug_enter('m_F4DD996D')
                self.assertEqual(instance.vars[8370].value(), positions[0])

    def test_position_config_resolves_authored_placed_tag(self):
        data = manifest(placement(0, client_id=0, position=(83, 18, -86)))
        tag = '0x0D60000000000A69'
        data['definitions'][DEF]['components']['entries'].append({
            'value': {'$': 'STUTargetTagComponent', 'm_targetTags': None},
        })
        data['entities'][0]['components'].append({
            'class': 'STUTargetTagInstanceData', 'fields': {'m_targetTags': [tag]},
        })
        world = scene.SceneWorld.from_map(data, entity_start=145, server_placeables=[145])
        query = {'$': 'STU_B3E67A36', 'm_9C7BD8B8': {'$': 'STU_357DABBB', 'm_7D7C86A1': tag}}
        instance = caller(world)
        self.assertEqual(instance.evaluate(query), expr.Entity(0x80000091))
        position = {'$': 'STU_71A87E92', 'm_entity': query}
        self.assertEqual(instance.evaluate(position), expr.Vec3(83, 18, -86))
        instance.write((runtime.INSTANCE, 8364), expr.Asset(int(tag, 16)))
        required_query = {'$': 'STU_B3E67A36', 'm_B486603E': {
            '$': 'STUConfigVarDynamic', 'm_identifier': 8364,
        }}
        self.assertEqual(instance.evaluate(required_query), expr.Entity(0x80000091))
        world.unbind(0x80000091)
        with self.assertRaises(runtime.UnsupportedScriptError):
            instance.evaluate(position)

    def test_client_id_namespace_is_independent_of_local_and_flat_indexes(self):
        data = manifest(placement(0, client_id=55))
        world = scene.SceneWorld.from_map(data, entity_start=419)
        record = world.entities[0xC0000037]
        self.assertEqual(record.placement.flat, 419)
        self.assertEqual(record.placement.index, 0)
        self.assertEqual(world.position(record.id), expr.Vec3(1, 2, 3))
        self.assertEqual(caller(world).evaluate(reference()), expr.Entity(record.id))
        self.assertNotIn(0x800001A3, world.entities)

    def test_server_ids_are_bound_only_for_explicit_actual_creates(self):
        data = manifest(placement(0, client_id=0), placement(1, client_id=0, value=BETA))
        world = scene.SceneWorld.from_map(data, entity_start=419, server_placeables=[420])
        self.assertEqual(set(world.entities), {0x800001A4})
        self.assertIs(caller(world).evaluate(reference(), initial=False), False)
        self.assertEqual(caller(world).evaluate(reference(BETA)), expr.Entity(0x800001A4))

    def test_mode_selection_applies_to_uuid_binding_without_rewriting_catalog(self):
        data = manifest(placement(0, client_id=1, identifier=IDENT), placement(1, client_id=2, value=BETA))
        filtered = scene.SceneWorld.from_map(data, entity_start=419)
        kept = scene.SceneWorld.from_map(data, entity_start=419, kept_identifiers=[IDENT])
        self.assertEqual(set(filtered.entities), {0xC0000002})
        self.assertEqual(set(kept.entities), {0xC0000001, 0xC0000002})
        self.assertEqual(len(filtered.placements), 2)
        self.assertIs(caller(filtered).evaluate(reference(), initial=False), False)

    def test_defaults_and_instance_components_are_detached_and_not_merged(self):
        data = manifest(placement(0, client_id=1))
        before = copy.deepcopy(data)
        world = scene.SceneWorld.from_map(data, entity_start=419)
        self.assertEqual(data, before)
        data['entities'][0]['position'][0] = 999
        data['entities'][0]['components'][0]['fields']['x'] = 99
        self.assertEqual(world.position(0xC0000001), expr.Vec3(1, 2, 3))
        self.assertEqual(world.placements[0].source['components'][0]['fields']['x'], 7)
        self.assertEqual(world.definitions[int(DEF, 16)]['fields'], {'x': 1})

    def test_unbind_removes_uuid_and_id_reuse_requires_a_fresh_binding(self):
        world = scene.SceneWorld()
        world.bind(0xC0000001, ALPHA, (1, 2, 3))
        active = caller(world)
        self.assertEqual(active.evaluate(reference()), expr.Entity(0xC0000001))
        old = world.unbind(0xC0000001)
        self.assertIsNone(world.position(old.id))
        self.assertIsNone(active.evaluate(reference()))
        world.bind(0xC0000001, BETA, (4, 5, 6))
        self.assertIsNone(active.evaluate(reference()))
        self.assertEqual(active.evaluate(reference(BETA)), expr.Entity(old.id))
        self.assertIsNone(world.unbind(0xC0000099))

    def test_zero_uuid_is_not_a_registered_lookup_key(self):
        world = scene.SceneWorld()
        world.bind(0xC0000001)
        world.bind(0xC0000002, scene.ZERO_UUID)
        self.assertFalse(world.references)
        self.assertEqual(caller(world).evaluate(reference(str(scene.ZERO_UUID)), initial=7), 7)

    def test_duplicate_bindings_invalid_ids_and_nonfinite_positions_are_rejected(self):
        world = scene.SceneWorld()
        world.bind(0xC0000001, ALPHA)
        for arguments in ((0xC0000001, BETA), (0xC0000002, ALPHA), (0, BETA),
                          (0x1FFFFFFF, BETA), (True, BETA), (1 << 32, BETA)):
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                world.bind(*arguments)
        for position in ((float('nan'), 0, 0), (1e60, 0, 0), (True, 0, 0), (1, 2)):
            with self.subTest(position=position), self.assertRaises(ValueError):
                world.bind(0xC0000002, BETA, position)
        self.assertEqual(len(world.entities), 1)

    def test_missing_scene_position_fails_strict_evaluation_context(self):
        world = scene.SceneWorld()
        world.bind(123, ALPHA)
        for entity in (123, 456):
            active = caller(world)
            with self.subTest(entity=entity), self.assertRaisesRegex(
                    runtime.UnsupportedScriptError, 'entity position'):
                active.context().position(entity)
            self.assertIsNotNone(active.component.fault)

    def test_malformed_uuid_and_unknown_services_keep_strict_diagnostics(self):
        for config in (reference('bad'), {'$': 'STU_DB742151'}):
            active = caller(scene.SceneWorld())
            with self.assertRaises(runtime.UnsupportedScriptError):
                active.evaluate(config)
            self.assertIsNotNone(active.component.fault)

    def test_foreign_script_variables_require_an_actual_component_service(self):
        world = scene.SceneWorld(mode=0x0230000000000025)
        world.bind(456, ALPHA)
        active = caller(world)
        self.assertIsNone(active.context().member(active.component.entity, 7))
        variables = runtime.mode_variables(world.mode)
        key, value = next(iter(variables.items()))
        self.assertEqual(active.context().member(runtime.GAME_MODE_ENTITY, key), value)
        with self.assertRaisesRegex(runtime.UnsupportedScriptError, 'entity variable service'):
            active.context().member(456, 7)
        self.assertIsNotNone(active.component.fault)

    def test_inconsistent_map_inputs_are_not_partially_accepted(self):
        original = manifest(placement(0, client_id=1), placement(1, client_id=2, value=BETA))
        variations = []
        duplicate = original['entities'][0]['placeable_header']
        for path, value in ((('version',), True), (('entities', 1, 'index'), 7),
                            (('entities', 1, 'placeable_header'), duplicate),
                            (('entities', 1, 'definition'), '0x0400000000000099'),
                            (('entities', 1, 'record_metadata'), '00'),
                            (('entities', 1, 'position'), [float('inf'), 0, 0])):
            data = copy.deepcopy(original)
            parent = data
            for key in path[:-1]:
                parent = parent[key]
            parent[path[-1]] = value
            variations.append(data)
        for data in variations:
            with self.subTest(data=data), self.assertRaises(ValueError):
                scene.SceneWorld.from_map(data, entity_start=419)
        for options in ({'entity_start': -1}, {'entity_start': 0x1FFFFFFE},
                        {'entity_start': 419, 'server_placeables': [419]},
                        {'entity_start': 419, 'server_placeables': [999]}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                scene.SceneWorld.from_map(original, **options)


    def test_orientation_queries_use_the_addressed_entity_and_its_own_binding(self):
        world = scene.SceneWorld()
        for entity in (123, 124):
            world.bind(entity)
        world.set_entity_directions(123, aim=(0, 0, 1))
        world.set_entity_directions(124, aim=(1, 0, 0), forward=(0, 0, -1))
        instance = caller(world)
        aim = {"$": "STU_2200A66D", "m_entity": expr.Entity(124)}
        forward = {**aim, "$": "STU_021D01CD"}
        self.assertEqual(instance.evaluate(aim), expr.Vec3(1, 0, 0))
        self.assertEqual(instance.evaluate(forward), expr.Vec3(0, 0, -1))
        with self.assertRaisesRegex(runtime.UnsupportedScriptError, "STU_021D01CD"):
            caller(world).evaluate({**forward, "m_entity": expr.Entity(123)})
        world.unbind(124)
        self.assertEqual(instance.evaluate(aim, initial=99), 99)
        world.bind(124)
        with self.assertRaisesRegex(runtime.UnsupportedScriptError, "STU_2200A66D"):
            caller(world).evaluate(aim)


    def test_target_tag_query_replaces_destination_and_retires_unbound_members(self):
        tag = 0x0D6000000000081F
        world = scene.SceneWorld()
        for entity in (123, 124, 125):
            world.bind(entity)
        world.set_target_tag_entities(tag, (125, 124, 125))
        destination = {'$': 'STUConfigVarDynamic', 'm_identifier': 7, 'm_60DB8F99': expr.INSTANCE}
        action = node(0, 'STU_42578342', m_4AEEE6A1=destination,
                      m_B486603E={'$': 'STU_357DABBB', 'm_7D7C86A1': f'0x{tag:016X}'})
        definition = graph([action])
        component = runtime.Component(123, world=world, strict=True)
        instance = component.create(definition, init=False)
        instance.initialized = True
        target = expr.INSTANCE, 7
        instance.write(target, 99)
        instance.run_node(definition.node(0), None)
        self.assertEqual(instance.find_var(*target).value(), (expr.Entity(125), expr.Entity(124)))
        world.unbind(125)
        world.bind(125)
        world.unbind(124)
        instance.run_node(definition.node(0), None)
        self.assertEqual(instance.find_var(*target).value(), ())



class NativeFailureTests(unittest.TestCase):
    def test_npc_query_context_forwards_typed_arguments_and_retires_on_unbind(self):
        world = scene.SceneWorld()
        world.bind(123, ALPHA)
        world.bind(456, BETA)
        calls = []
        world.set_npc_query_context(123, npc_queries.Context(
            lambda owner, selector: calls.append((owner, selector)) or 456,
            lambda owner, target: calls.append((owner, target)) or True,
        ))
        active = caller(world)
        entity = {'$': npc_queries.ENTITY_QUERY, 'm_152B6961': reference(),
                  'm_A60150E2': {'$': 'STUConfigVarInt', 'm_value': 4}}
        pair = {'$': npc_queries.PAIR_QUERY, 'm_7826B991': reference(), 'm_target': reference(BETA)}
        self.assertEqual(active.evaluate(entity), expr.Entity(456))
        self.assertIs(active.evaluate(pair), True)
        self.assertEqual(calls, [(123, 4), (123, 456)])
        world.unbind(123)
        world.bind(123, ALPHA)
        with self.assertRaises(runtime.UnsupportedScriptError):
            active.evaluate(entity)

    def test_failed_reference_preserves_destination_but_successful_null_overwrites_it(self):
        active = caller(scene.SceneWorld())
        initial = expr.Asset(17)
        self.assertIs(active.evaluate(reference(), initial=initial), initial)
        self.assertIsNone(active.evaluate({'$': 'STUConfigVarDynamic', 'm_identifier': 7}, initial=initial))
        with mock.patch.object(active.component.world, 'native', return_value=expr.EVALUATION_FAILED):
            self.assertIs(active.evaluate({'$': 'Service'}, initial=initial), initial)

    def test_setvar_failure_uses_int_zero_and_does_not_store_the_provider_marker(self):
        action = setter(0, 99)
        action['fields']['m_value'] = reference()
        component = runtime.Component(123, world=scene.SceneWorld(), strict=True)
        active = component.create(graph([action]), init=False)
        active.run_node(active.graph.node(0), None)
        value = active.find_var(0, 7).value()
        self.assertIs(type(value), int)
        self.assertEqual(value, 0)

    def test_forcode_failure_keeps_the_native_callers_existing_value(self):
        component, _ = make([[row(reference())]], world=scene.SceneWorld())
        initial = expr.Asset(17)
        self.assertEqual(component.code_value(KEY, initial=initial), (True, initial))

    def test_schema_skips_failed_reference_but_keeps_empty_collected_array(self):
        world = scene.SceneWorld()
        model = with_schema(rows=[schema_row(IDENT, reference()),
                                  schema_row(IDENT + 1, array(reference())),
                                  schema_row(IDENT + 2, integer(9))])
        active = runtime.Component(123, world=world, strict=True).play_loaded_graph(model, source=456)
        values = [event.assignment for event in active.queue if event.kind == runtime.ASSIGN]
        self.assertEqual(values, [(0, 8, ()), (0, 9, 9)])
        self.assertFalse(any(value is expr.EVALUATION_FAILED for _, _, value in values))

    def test_inline_override_failure_is_zero_and_bound_reference_is_a_typed_entity(self):
        world = scene.SceneWorld()
        world.bind(0xC0000001, BETA)
        active, action = requesting(inline([schema_row(IDENT, reference()),
                                           schema_row(IDENT + 1, reference(BETA))]), world=world)
        self.assertEqual(dict(play.resolve_resource(active, action).overrides),
                         {IDENT: 0, IDENT + 1: expr.Entity(0xC0000001)})

    def test_empty_target_tag_is_an_asset_in_scalar_and_collecting_paths(self):
        active = caller(scene.SceneWorld())
        empty = {'$': 'STU_357DABBB', 'm_resourceKey': None, 'm_7D7C86A1': None}
        self.assertEqual(active.evaluate(empty), expr.Asset(0))
        self.assertEqual(play.evaluate_value(active, array(empty, empty)), (expr.Asset(0), expr.Asset(0)))
        with self.assertRaises(runtime.UnsupportedScriptError):
            active.evaluate({**empty, 'm_resourceKey': '0x0D80000000000001'})

    def test_reference_state_polls_binding_liveness_without_fake_success(self):
        world = scene.SceneWorld()
        model = graph([node(0, 'STUStatescriptEntry', m_outPlug=plug((1, 'm_inPlug'))),
                       node(1, 'STUStatescriptStateBooleanSwitch', state=0, m_2BBEEAB8=1,
                            m_condition=reference(), m_onTruePlug=plug((2, 'm_inPlug')),
                            m_onFalsePlug=plug((3, 'm_inPlug'))), setter(2, 1), setter(3, 0)],
                      entries=[0])
        component = runtime.Component(123, world=world, strict=True)
        active = component.create(model)
        component.run_now()
        self.assertEqual(active.find_var(0, 7).value(), 0)
        self.assertTrue(active.states[0].polled)
        world.bind(0xC0000001, ALPHA)
        component.run_frame(1)
        self.assertEqual(active.find_var(0, 7).value(), 1)
        world.unbind(0xC0000001)
        component.run_frame(2)
        self.assertEqual(active.find_var(0, 7).value(), 0)


if __name__ == '__main__':
    unittest.main()
