"""Shared component values/messages, generation retirement and ordinary scene scheduling."""

import unittest
from functools import partial
from unittest import mock

from ow174.game import collision, contacts, mover
from ow174.game.combat import Vitals
from ow174.game.script import expr, play, play_queue, runtime, scene, scene_host, scopes
from tests.public.test_script_startup import definition, row, schema, spec
from tests.script_fixtures import graph, node, plug, setter

A, B = 0x80000001, 0x80000002
MESSAGE = 0x0240000000000001


def literal(value):
    return {'$': 'SceneLiteral', 'value': value}


class ValueWorld(scene.SceneWorld):
    def native(self, cfg, instance):
        if cfg.get('$') == 'SceneLiteral':
            return cfg['value']
        if cfg.get('$') == 'ReadOther':
            return self.entity_var(B, 9)
        return super().native(cfg, instance)


def make_world():
    world = ValueWorld()
    world.bind(A)
    world.bind(B)
    return world


def component(world, entity, model=None):
    result = runtime.Component(entity, world=world, strict=True)
    instance = result.create(graph() if model is None else model, init=False)
    instance.initialized = True
    world.bind_script_component(entity, result)
    return result, instance


def message_model():
    return graph([node(0, 'STUStatescriptEntryGameMessage',
                       m_gameMessage={'$': 'STUConfigVarGameMessage', 'm_gameMessage': f'0x{MESSAGE:016X}'},
                       m_params=[{'m_B5051BCE': '0x0D80000000000007',
                                  'm_out_Var': {'$': 'STUConfigVarDynamic', 'm_identifier': 9}}],
                       m_outPlug=plug((1, 'm_inPlug'))), setter(1, 23)], entries=[0])


def sender_model():
    return graph([node(0, 'STUStatescriptActionSendGameMessage',
                       m_gameMessage={'$': 'STUConfigVarGameMessage', 'm_gameMessage': f'0x{MESSAGE:016X}'},
                       m_targets=literal(expr.Entity(B)),
                       m_params=[{'m_B5051BCE': '0x0D80000000000007',
                                  'm_value': {'$': 'STUConfigVarInt', 'm_value': 17}}],
                       m_outPlug=plug((1, 'm_inPlug'))), setter(1, 31)])


class SharedSceneTests(unittest.TestCase):
    def test_indexed_switch_draws_one_route_and_follows_the_common_output(self):
        selector = {'$': 'STU_1760BCAD', 'm_min': 0, 'm_max': 2}
        model = graph([
            node(0, 'STUStatescriptActionSwitch', m_F0F0720B=1, m_262AE982=selector,
                 m_FDD34C25=[plug((pos, 'm_inPlug')) for pos in (1, 2, 3)],
                 m_CD38D5DB=plug((4, 'm_inPlug')), m_outPlug=plug((5, 'm_inPlug'))),
            *(setter(pos, pos) for pos in range(1, 5)),
            node(5, 'STUStatescriptActionSetVar',
                 m_out_Var={'$': 'STUConfigVarDynamic', 'm_identifier': 8},
                 m_value={'$': 'STUConfigVarInt', 'm_value': 10}),
        ])
        for value in (0, 1, 2, 3):
            with self.subTest(value=value):
                _, instance = component(make_world(), A, model)
                with mock.patch.object(expr.random.Random, 'randint', return_value=value) as draw:
                    instance.run_node(model.node(0), None)
                draw.assert_called_once_with(0, 2)
                self.assertEqual(instance.find_var(0, 7).value(), value + 1)
                self.assertEqual(instance.find_var(0, 8).value(), 10)

    def test_effect_entity_action_requires_scene_service_acceptance(self):
        model = graph([node(0, 'STU_E0687628', m_outPlug=plug((1, 'm_inPlug'))), setter(1, 17)])
        action = model.node(0)
        for accepted in (NotImplemented, True):
            with self.subTest(accepted=accepted):
                world = make_world()
                _, instance = component(world, A, model)
                world.effect_entity_action = mock.Mock(return_value=accepted)
                if accepted is True:
                    instance.run_node(action, None)
                    self.assertEqual(instance.find_var(0, 7).value(), 17)
                else:
                    with self.assertRaises(runtime.UnsupportedScriptError):
                        instance.run_node(action, None)
                    self.assertNotIn(7, instance.vars)
                world.effect_entity_action.assert_called_once_with(instance, action)

    def test_child_stop_queues_parent_finish_and_preserves_output_order(self):
        world = make_world()
        parent_graph = graph([node(0, 'STU_37D754C8', state=0,
                                   m_onFinishedPlug=plug((1, 'm_inPlug'))), setter(1, 31)])
        owner, parent = component(world, A, parent_graph)
        child_graph = graph([node(0, 'STU_22926252', m_outPlug=plug((1, 'm_inPlug'))), setter(1, 23)])
        state = parent.state(0)
        state.active = True
        child = owner.create(child_graph, parent=parent, parent_state=0, init=False)
        child.initialized = True
        state.child = child
        self.assertTrue(child.run_node(child_graph.node(0), None))
        self.assertEqual(child.find_var(0, 7).value(), 23)
        self.assertFalse(child.stopped)
        owner.run_now()
        self.assertTrue(child.stopped)
        self.assertIsNone(state.child)
        self.assertFalse(state.active)
        self.assertEqual(parent.find_var(0, 7).value(), 31)

    def test_voice_category_skips_unavailable_service_and_passes_null_category(self):
        world = make_world()
        model = graph([node(0, 'STU_3D419B63', m_targets=literal((expr.Entity(B), 12, expr.Entity(B))),
                            m_outPlug=plug((1, 'm_inPlug'))), setter(1, 31)])
        _, instance = component(world, A, model)
        world.voice_category_callback = None
        self.assertFalse(instance.run_node(model.node(0), None))
        self.assertIsNone(instance.find_var(0, 7).value())
        callback = mock.Mock()
        world.voice_category_callback = callback
        self.assertTrue(instance.run_node(model.node(0), None))
        self.assertEqual(callback.call_args_list, [mock.call(B, 0), mock.call(B, 0)])
        self.assertEqual(instance.find_var(0, 7).value(), 31)

    def test_session_entity_query_preserves_order_and_empty_scalar_destination(self):
        world = make_world()
        _, instance = component(world, A)
        cfg = {'$': 'STU_BD2D717B', 'm_B257A3A4': 0}
        world.session_entity_roster = lambda: (B, 0x80000003, A, B)
        expected = (expr.Entity(B), expr.Entity(A), expr.Entity(B))
        self.assertEqual(play.evaluate_value(instance, cfg), expected)
        self.assertEqual(play.evaluate_scalar_result(instance, cfg), (expected[0], True))
        world.session_entity_roster = tuple
        self.assertEqual(play.evaluate_value(instance, cfg), ())
        self.assertEqual(play.evaluate_scalar_result(instance, cfg, initial=8), (8, False))
        world.session_entity_roster = None
        with self.assertRaisesRegex(runtime.UnsupportedScriptError, 'session entity enumeration'):
            play.evaluate_value(instance, cfg)

    def test_ai_control_records_follow_target_and_source_lifetimes(self):
        world = make_world()
        world.bind_ai_control(B)
        model = graph([
            node(0, 'STU_473549CA', state=0, m_00D49978=literal(expr.Entity(B)),
                 m_49EEBB65=literal(12), m_FD4D6C19=literal(12)),
            node(1, 'STU_CF8C7CC5', state=1, m_00D49978=literal(expr.Entity(B))),
        ])
        owner, instance = component(world, A, model)
        first, second = instance.state(0), instance.state(1)
        first.begin()
        second.begin()
        records = world.ai_control_records[B]
        self.assertEqual([(record.kind, record.limits) for record in records.values()],
                         [(1, (12.0, 12.0)), (2, None)])
        first.end(False)
        self.assertEqual(tuple(records), (second,))
        world.unbind_script_component(A, owner)
        self.assertFalse(records)
        world.unbind(B)
        self.assertNotIn(B, world.ai_control_records)

    def test_junkenstein_facing_record_updates_on_the_target_and_clears_on_exit(self):
        world = make_world()
        world.bind_ai_control(B)
        model = graph([node(
            5, 'STU_FC16FF45', state=0, m_00D49978=literal(expr.Entity(B)),
            m_priority={'$': 'STU_8A491388', 'm_priority': '0x0C40000000000003'},
            m_7CCD8B4B={'$': 'STUConfigVarDynamic', 'm_identifier': 7},
        )])
        model.index = 0x0C95
        _, instance = component(world, A, model)
        state = instance.state(0)
        forward, sideways = expr.Vec3(0, 0, 1), expr.Vec3(1, 0, 0)
        instance.write((expr.INSTANCE, 7), forward)
        state.begin()
        records = world.ai_facing_records[B]
        record = records[state]
        self.assertEqual((record.direction, record.priority, record.prefer_later), (forward, 10, False))
        self.assertEqual(world.ai_control_records[B], {})
        instance.write((expr.INSTANCE, 7), sideways)
        self.assertIn(state, instance.ticking)
        state.tick()
        self.assertIs(records[state], record)
        self.assertEqual(record.direction, sideways)
        state.end(False)
        self.assertEqual(records, {})

    def test_junkenstein_team_contact_state_refilters_existing_pairs(self):
        geometry = contacts.Capsule((0, 0.2, 0), (0, 1.5, 0), 0.2, collision.MOVER)
        ally = contacts.PeerShape(geometry, collision.TEAM1)
        enemy = contacts.PeerShape(geometry, collision.TEAM0)
        terrain = contacts.StaticShape(geometry)
        floor = collision.flat_world(0.0)
        floor.engine = contacts.StaticWorld([ally, enemy, terrain])
        movement = mover.Mover((0, 0, 0), world=floor, category=collision.TEAM1)
        body = movement._engine_body()
        body.update_pairs()
        self.assertEqual({pair.shape for pair in body.contacts}, {ally, enemy, terrain})

        world = ValueWorld()
        world.bind(A, same_team_contact=partial(movement.set_same_team_contact, 1))
        model = graph([node(129, 'STU_6CC90892', state=0, m_D7AC2990={'$': 'STU_B5A0CAF0'})])
        model.index = 0x0CBC
        _, instance = component(world, A, model)
        state = instance.state(0)
        state.begin()
        body.update_pairs()
        self.assertEqual({pair.shape for pair in body.contacts}, {enemy, terrain})
        state.end(False)
        body.update_pairs()
        self.assertEqual({pair.shape for pair in body.contacts}, {ally, enemy, terrain})

    def test_spawn_search_waits_for_positions_and_discards_stale_completion(self):
        world = make_world()
        requests = []
        canceled = []

        def submit(state, request, complete):
            requests.append((request, complete))
            return lambda: canceled.append(request)

        world.spawn_search = submit
        _, instance = component(world, A, graph([node(
            0, 'STU_BA2FCDEB', state=0,
            m_8FEA3023={'$': 'STUConfigVarDynamic', 'm_identifier': 7},
            m_ADC02DA1=literal(expr.Vec3(0, 0, 2)), m_radius=literal(30),
            m_A30B383E=literal(90), m_EC1B83C7=literal(10), m_7084DA43=literal(3),
            m_F649CC6C=literal(0),
        )]))
        state = instance.state(0)
        state.begin()
        instance.update()
        request, complete = requests[0]
        self.assertEqual(request.direction, expr.Vec3(0, 0, 1))
        self.assertEqual((request.radius, request.search_scale), (30, 0.5))
        self.assertEqual((request.min_candidates, request.max_candidates), (10, 10))
        self.assertAlmostEqual(request.half_angle, 0.78539816)
        self.assertTrue(state.active)
        self.assertIsNone(instance.find_var(0, 7).value())
        state.end(False)
        state.begin()
        points = (expr.Vec3(1, 2, 3),)
        self.assertFalse(complete(points))
        self.assertTrue(requests[1][1](points))
        self.assertIsNone(instance.find_var(0, 7).value())
        instance.update()
        self.assertEqual(instance.find_var(0, 7).value(), points)
        self.assertFalse(state.active)
        self.assertEqual(canceled, [request, requests[1][0]])

    def test_authored_npc_base_pool_uses_preallocated_combat_vitals(self):
        for capacity in (200, 199):
            with self.subTest(capacity=capacity):
                world = ValueWorld()
                vitals = Vitals(A, capacity)
                vitals.damage(10)
                world.bind(A, pool_capacity=vitals.most.get)
                model = graph([node(5, 'STUStatescriptStateHealthPool', state=0,
                                    m_FA3ECC43=literal(expr.Entity(A)), m_7E8B666C=0, m_amount=literal(200))])
                model.index = 0x0C89
                _, instance = component(world, A, model)
                state = instance.state(0)
                if capacity == 200:
                    state.begin()
                    self.assertTrue(state.active)
                    self.assertEqual(vitals.total, 190)
                else:
                    with self.assertRaisesRegex(runtime.UnsupportedScriptError, 'health pool capacity'):
                        state.begin()

    def test_normal_shock_tire_keeps_its_existing_health_pool(self):
        owner = runtime.GAME_MODE_ENTITY
        for difficulty, amount in ((0, 0), (None, 0), (1, 100)):
            with self.subTest(difficulty=difficulty):
                world = ValueWorld()
                world.bind(owner)
                mode_vm, _ = component(world, owner)
                if difficulty is not None:
                    mode_vm.set(9948, difficulty)
                vitals = Vitals(A, 100)
                vitals.damage(10)
                world.bind(A, pool_capacity=vitals.most.get)
                model = graph([node(43, 'STUStatescriptStateHealthPool', state=0,
                                    m_FA3ECC43=literal(expr.Entity(A)), m_7E8B666C=0,
                                    m_amount=literal(amount))])
                model.index = 0x0CBC
                _, instance = component(world, A, model)
                state = instance.state(0)
                if difficulty == 0:
                    state.begin()
                    self.assertTrue(state.active)
                    self.assertEqual(vitals.total, 90)
                else:
                    with self.assertRaisesRegex(runtime.UnsupportedScriptError, 'wave health modifier'):
                        state.begin()

    def test_ai_control_setting_clamps_refreshes_and_resets(self):
        world = make_world()
        world.bind_ai_control(A)
        model = graph([node(
            0, 'STU_97B1B3EC', state=0, m_D6E83CD4=literal(True),
            m_0468C6D4={'$': 'STUConfigVarDynamic', 'm_identifier': 7},
        )])
        _, instance = component(world, A, model)
        state = instance.state(0)
        state.begin()
        self.assertEqual(world.ai_control_settings[A], (expr.f32(0.1), True))
        instance.write((0, 7), 20)
        instance.update()
        self.assertEqual(world.ai_control_settings[A], (20.0, True))
        state.end(False)
        self.assertEqual(world.ai_control_settings[A], (5.0, False))

    def test_ai_behavior_requests_refresh_without_changing_priority_order(self):
        world = make_world()
        world.bind_ai_behavior(A)
        model = graph([
            node(0, 'STU_B61E6681', state=0, m_203D6B00={'$': 'STUConfigVarDynamic', 'm_identifier': 7}),
            node(1, 'STU_CFA338CB', state=1, m_157D72D1=literal(90)),
            node(2, 'STU_600E3C59', state=2, m_DD792FF0=3,
                 m_priority=literal(expr.Asset(0x0C40000000000003))),
        ])
        _, instance = component(world, A, model)
        first, second, third = (instance.state(index) for index in range(3))
        instance.write((0, 7), 1.5)
        for state in (first, second, third):
            state.begin()
        requests = world.ai_behavior_requests[A]
        self.assertEqual(tuple(requests), (third, first, second))
        self.assertEqual(requests[first].inputs, (1.5, 0.0))
        self.assertEqual(requests[second].inputs[-1], expr.f32(1.5707963267948966))
        self.assertEqual(requests[third].inputs, (2.0, 2.0, 0.0, expr.f32(0.2), 1.0))
        instance.write((0, 7), 2.5)
        instance.update()
        self.assertEqual(requests[first].inputs, (2.5, 0.0))
        self.assertEqual(tuple(requests), (third, first, second))
        first.end(False)
        self.assertEqual(tuple(requests), (third, second))

    def test_ai_weapon_record_requires_matching_controller_slot_and_identifier(self):
        world = make_world()
        world.bind_ai_control(B)
        world.set_linked_controller(B, A)
        weapon = 0x0D8000000000023A
        world.ai_weapon_slots[A] = {0: frozenset({weapon})}
        model = graph([
            node(index, 'STU_066B4605', state=index, m_00D49978=literal(expr.Entity(B)),
                 m_D66E7D68=literal(slot), m_F2810377=literal(expr.Asset(identifier)),
                 m_priority=literal(expr.Asset(0x0C40000000000000 | priority)))
            for index, (slot, identifier, priority) in enumerate(
                ((0, weapon, 1), (0, weapon, 0x3D), (1, weapon, 1), (0, weapon + 1, 1))
            )
        ])
        _, instance = component(world, A, model)
        states = [instance.state(index) for index in range(4)]
        for state in states:
            state.begin()
        self.assertEqual(tuple(world.ai_weapon_records[B]), (states[1], states[0]))
        self.assertFalse(world.ai_control_records[B])
        states[1].end(False)
        self.assertEqual(tuple(world.ai_weapon_records[B]), (states[0],))

    def test_array_filter_queues_an_ordered_result_with_duplicate_matches(self):
        world = make_world()
        world.array_filter_inputs = {A: (False, 0x80000), B: (True, 0x80000)}.get
        destination = {'$': 'STUConfigVarDynamic', 'm_identifier': 7}
        _, instance = component(world, A, graph([node(
            0, 'STU_0A6BE10E', state=0, m_F2805C84=destination,
            m_filter={'$': 'STUConfigVarFilter', 'm_84065FD1': 0x80000},
            m_814F8EC3=literal((expr.Entity(B), expr.Entity(A), expr.Entity(B), 12)),
        )]))
        instance.state(0).begin()
        self.assertIsNone(instance.find_var(0, 7).value())
        instance.update()
        self.assertEqual(instance.find_var(0, 7).value(), (expr.Entity(B), expr.Entity(B), 12))

    def test_scalar_filter_uses_the_bound_entity_bits(self):
        world = make_world()
        _, instance = component(world, A)
        filters = {B: (True, 0x1000000)}
        world.array_filter_inputs = filters.get
        cfg = {'$': 'STU_AC829876', 'm_entity': literal(expr.Entity(B)),
               'm_filter': {'$': 'STUConfigVarFilter', 'm_84065FD1': 0x3FFFFFFF, 'm_4EA686DB': 0x100}}
        self.assertIs(instance.evaluate(cfg), True)
        filters[B] = (True, 0x1000100)
        self.assertIs(instance.evaluate(cfg), False)
        world.unbind(B)
        self.assertIs(instance.evaluate(cfg), False)

    def test_default_velocity_query_reads_live_body_and_preserves_output_if_unbound(self):
        world = make_world()
        world.unbind(B)
        speed = [0.0, 0.0, 0.0]
        world.bind(B, velocity=lambda: tuple(speed))
        _, instance = component(world, A)
        cfg = {'$': 'STU_309B0570', 'm_entity': literal(expr.Entity(B))}
        self.assertEqual(instance.evaluate(cfg), expr.Vec3(0.0, 0.0, 0.0))
        speed[2] = 3.5
        self.assertEqual(instance.evaluate(cfg), expr.Vec3(0.0, 0.0, 3.5))
        world.unbind(B)
        self.assertEqual(instance.evaluate(cfg, initial=7), 7)

    def test_binding_requires_an_actual_component_on_the_bound_entity_and_world(self):
        world = make_world()
        for candidate in (runtime.Component(123, world=world), runtime.Component(A), object()):
            with self.subTest(candidate=type(candidate)), self.assertRaises(ValueError):
                world.bind_script_component(A, candidate)
        with self.assertRaises(ValueError):
            world.bind_script_component(123, None)

    def test_live_foreign_values_include_effective_links_and_subscribe_to_changes(self):
        world = make_world()
        _, caller = component(world, A, graph([node(0, 'STUStatescriptState', state=0)]))
        target, provider = component(world, B, graph([node(0, 'STUStatescriptState', state=0)]))
        watcher, output = caller.state(0), provider.state(0)
        watcher.active = output.active = True
        output.output = lambda slot: expr.Asset(23)
        variable = target.entity_var(9)
        variable.base = 17
        variable.links.append(runtime.Link(output))
        self.assertEqual(caller.context(watcher).member(B, 9), expr.Asset(23))
        self.assertIn(watcher, variable.watchers)
        variable.links.clear()
        target.changed(variable, expr.Asset(23))
        self.assertTrue(watcher.flags_changed)
        self.assertEqual(len(caller.queue), 1)
        self.assertEqual(caller.context().member(B, 9), 17)

    def test_unknown_foreign_components_fail_but_known_absence_and_missing_entities_do_not(self):
        world = make_world()
        _, caller = component(world, A)
        with self.assertRaisesRegex(runtime.UnsupportedScriptError, 'entity variable service'):
            caller.context().member(B, 9)
        world.unbind_script_component(A)
        _, caller = component(world, A)
        world.bind_script_component(B, None)
        self.assertIsNone(caller.context().member(B, 9))
        self.assertIsNone(caller.context().member(0x80000003, 9))

    def test_retiring_a_foreign_component_invalidates_its_watchers(self):
        world = make_world()
        _, caller = component(world, A, graph([node(0, 'STUStatescriptState', state=0)]))
        target, _ = component(world, B)
        watcher = caller.state(0)
        watcher.active = True
        caller.context(watcher).member(B, 9)
        variable = target.entity_var(9)
        world.unbind(B)
        self.assertFalse(variable.watchers)
        self.assertNotIn(variable, watcher.watched)
        self.assertTrue(watcher.flags_changed)
        self.assertIsNone(caller.context().member(B, 9))

    def test_retiring_the_watching_component_unsubscribes_it_from_foreign_storage(self):
        world = make_world()
        _, caller = component(world, A, graph([node(0, 'STUStatescriptState', state=0)]))
        target, _ = component(world, B)
        watcher = caller.state(0)
        watcher.active = True
        caller.context(watcher).member(B, 9)
        variable = target.entity_var(9)
        world.unbind(A)
        self.assertFalse(variable.watchers)
        self.assertFalse(watcher.watched)

    def test_messages_reach_actual_receiver_instances_at_the_receiver_clock(self):
        world = make_world()
        _, sender = component(world, A, sender_model())
        target, receiver = component(world, B, message_model())
        target.now = 400
        sender.run_node(sender.graph.node(0), None)
        self.assertEqual(sender.find_var(0, 7).value(), 31)
        self.assertEqual(len(receiver.queue), 2)
        self.assertEqual(receiver.queue[0].time, 400)
        self.assertEqual([event.kind for event in receiver.queue], [runtime.ASSIGN, runtime.ENTRY])
        self.assertEqual(receiver.queue[0].assignment, (0, 9, (17,)))
        self.assertIsNone(receiver.find_var(0, 7, create=False))
        target.run_now()
        self.assertEqual(receiver.find_var(0, 7).value(), 23)
        self.assertEqual(receiver.find_var(0, 9).value(), (17,))

    def test_unknown_message_transport_stops_before_following_success(self):
        source = runtime.Component(A, world=runtime.World(), strict=True)
        sender = source.create(sender_model(), init=False)
        with self.assertRaisesRegex(runtime.UnsupportedScriptError, 'game message owner'):
            sender.run_node(sender.graph.node(0), None)
        self.assertIsNone(sender.find_var(0, 7, create=False))

    def test_unknown_target_component_stops_instead_of_dropping_authored_messages(self):
        world = make_world()
        _, sender = component(world, A, sender_model())
        with self.assertRaisesRegex(runtime.UnsupportedScriptError, 'scene message target component'):
            sender.run_node(sender.graph.node(0), None)
        self.assertIsNone(sender.find_var(0, 7, create=False))

    def test_known_missing_message_receiver_preserves_the_native_drop_path(self):
        for missing_entity in (False, True):
            world = make_world()
            _, sender = component(world, A, sender_model())
            if missing_entity:
                world.unbind(B)
            else:
                world.bind_script_component(B, None)
            sender.run_node(sender.graph.node(0), None)
            self.assertEqual(sender.find_var(0, 7).value(), 31)

    def test_stale_source_cannot_send_after_component_replacement(self):
        world = make_world()
        old, sender = component(world, A, sender_model())
        _, receiver = component(world, B, message_model())
        world.unbind_script_component(A, old)
        replacement, _ = component(world, A)
        with self.assertRaisesRegex(runtime.UnsupportedScriptError, 'scene message source lifecycle'):
            sender.run_node(sender.graph.node(0), None)
        self.assertIs(world.script_components[A], replacement)
        self.assertFalse(receiver.queue)
        with self.assertRaises(ValueError):
            world.unbind_script_component(A, old)

    def test_failed_target_variables_are_not_read_as_live_world_state(self):
        world = make_world()
        _, caller = component(world, A)
        target, receiver = component(world, B, graph([node(0, 'UnknownAction')]))
        target.set(9, 17)
        with self.assertRaises(runtime.UnsupportedScriptError):
            receiver.run_node(receiver.graph.node(0), None)
        with self.assertRaisesRegex(runtime.UnsupportedScriptError, 'entity variable service'):
            caller.context().member(B, 9)

    def test_failed_mode_component_cannot_fall_back_to_immutable_initial_values(self):
        world = make_world()
        world.bind(runtime.GAME_MODE_ENTITY)
        _, caller = component(world, A)
        _, mode = component(world, runtime.GAME_MODE_ENTITY, graph([node(0, 'UnknownAction')]))
        with self.assertRaises(runtime.UnsupportedScriptError):
            mode.run_node(mode.graph.node(0), None)
        with (mock.patch.object(runtime, 'mode_variables', return_value={9: 17}),
              self.assertRaisesRegex(runtime.UnsupportedScriptError, 'entity variable service')):
            caller.context().member(runtime.GAME_MODE_ENTITY, 9)


class SceneHostTests(unittest.TestCase):
    def test_spawned_junkenstein_boss_loads_encounter_component_for_shared_hero_body(self):
        resource = 0x0400000000000022
        hero = 0x02E0000000000002
        key = f'0x{resource:016X}'
        regular = definition(spec(1))
        encounter = definition(spec(2))
        model = graph([
            node(0, 'STUStatescriptEntry', m_outPlug=plug((1, 'm_inPlug'))), setter(1, 55),
        ], entries=[0])
        model.index = 2
        host = self.host({2: model})
        bundle = {'bodies': {key: {'script_component': regular}}, 'junkenstein': {
            'entities': {key: {'script_component': encounter}},
            'pairs': {f'0x{hero:016X}': {'body': key, 'controller': '0x0400000000001958'}},
        }}
        self.assertTrue(host.advance(10))
        host.world.bind(123, definition=resource)
        with mock.patch.object(scene_host.graph, '_data', return_value=bundle):
            self.assertIs(scene_host.graph.entity_script_component(resource), regular)
            self.assertEqual(scene_host.graph.junkenstein_enemy(hero)['body'], key)
            vm = host.add_spawned_entity_scripts(123, junkenstein=True)
        self.assertTrue(host.advance(11), host.error)
        instance, = vm.instances.values()
        self.assertEqual(instance.graph.index, 2)
        self.assertEqual(instance.find_var(runtime.INSTANCE, 7).value(), 55)

    def test_scene_advances_an_explicit_forward_scope_host(self):
        world = make_world()
        model = graph([
            node(0, 'STUStatescriptEntry', m_outPlug=plug((1, 'm_beginPlug'))),
            node(1, 'STU_BD02E168', state=0, m_5B10D01F=plug((2, 'm_inPlug'))),
            setter(2, 55),
        ], entries=[0])
        host = scene_host.SceneHost(world, graph_loader={1: model}.get)
        vm = host.add(A, definition(spec(1)))
        scope = scopes.ForwardHost(vm)
        world.set_subgraph_scopes(A, scope)

        self.assertTrue(host.advance(70), host.error)
        instance = next(iter(vm.instances.values()))
        self.assertEqual(instance.find_var(runtime.INSTANCE, 7).value(), 55)
        self.assertEqual(scope.frame, 70)
        self.assertTrue(scope.history[instance.state(0)].dispatched)
        self.assertTrue(host.advance(71), host.error)
        self.assertEqual(scope.frame, 71)
        host.close()
        self.assertEqual(scope.states, [])

    def test_owner_voice_set_override_releases_on_end_and_component_retirement(self):
        world = make_world()
        default, override = 0x07A000000000001D, 0x07A000000000013B
        world.bind_voice_set(A, default)
        config = {'$': 'STUConfigVoiceDefinition', 'm_C6C61CB0': f'0x{override:016X}'}
        model = graph([node(0, 'STU_353AF1A9', state=0,
                            m_target={'$': 'STU_B5A0CAF0'}, m_DA4846A5=config)])
        source, instance = component(world, A, model)
        state = instance.state(0)
        for frame in (0, 1):
            state.request(runtime.BEGIN)
            source.run_frame(frame)
            self.assertEqual(world.voice_set(A), override)
            if frame == 0:
                state.end(False)
                self.assertEqual(world.voice_set(A), default)
        world.unbind_script_component(A, source)
        self.assertEqual(world.voice_set(A), default)

    def host(self, models):
        return scene_host.SceneHost(make_world(), graph_loader=models.get)

    def test_junkenstein_retirement_retires_body_and_controller_after_frame(self):
        for index, position, body_action, dead in ((0x0C89, 43, None, True), (0x0C93, 11, None, True),
                                                   (0x0CBC, 36, 38, True), (0x0CBC, 58, 57, True),
                                                   (0x0CBC, 58, 57, False)):
            with self.subTest(index=index, position=position, dead=dead):
                self_entity = {'$': 'STU_B5A0CAF0'}
                actions = [
                    node(0, 'STUStatescriptEntry', m_outPlug=plug((position, 'm_inPlug'))),
                ]
                if body_action is not None:
                    actions.append(node(position, 'STU_523EE38D',
                                        m_entity={'$': 'STU_DB742151', 'm_entity': self_entity},
                                        m_outPlug=plug((body_action, 'm_inPlug'))))
                actions.append(node(body_action or position, 'STU_523EE38D', m_entity=self_entity))
                model = graph(actions, entries=[0])
                model.index = index
                host = self.host({index: model})
                body = host.add(A, definition(spec(index)))
                controller = host.add(B, definition())
                world = host.world
                world.set_possessions(possessors=(B,), primary=((A, B),))
                world.set_linked_controller(A, B)
                world.set_current_possession(B, A)
                world.bind_ai_behavior(B)
                host.configure_play_requests(lambda _: None)
                queue = world.play_queues[B]
                body.owner_dead = dead

                with mock.patch.object(world, 'request_corpse_retirement',
                                       wraps=world.request_corpse_retirement) as request:
                    self.assertTrue(host.advance(20), host.error)
                targets = [call.args[1] for call in request.call_args_list]
                self.assertEqual(targets, [B, A] if body_action is not None else [A])
                self.assertNotIn(A, world.entities)
                self.assertNotIn(B, world.entities)
                self.assertFalse(host.units)
                self.assertFalse(body.instances)
                self.assertFalse(controller.instances)
                self.assertTrue(queue.closed)
                self.assertTrue(host.advance(21), host.error)

    def test_spawned_component_accepts_same_frame_requests_and_runs_next_frame(self):
        actor = 0x80000003
        definition_guid = 0x04000000000016C0
        source = graph([node(0, 'STUStatescriptEntry', m_outPlug=plug((1, 'm_inPlug'))),
                        node(1, 'SpawnFixtureActor')], entries=[0])
        target = graph([node(0, 'STUStatescriptEntry', m_outPlug=plug((1, 'm_inPlug'))),
                        setter(1, 23)], entries=[0])
        target.index = 2
        host = self.host({1: source, 2: target})
        host.add(A, definition(spec(1)))
        played = graph()
        played.index = 3
        host.configure_play_requests(lambda _: play_queue.Load('ready', played))

        def spawn(instance, node, caller):
            host.world.bind(actor, definition=definition_guid)
            component = host.add_spawned_entity_scripts(actor)
            request = play.Request(play.Resource(0x0580000000000003), (actor,),
                                   play.InvocationContext(A, A, A), (), 1, 0)
            host.world.submit_script_request(instance, node, request)
            self.assertEqual(component.frame, 20)
            self.assertEqual([item.graph.index for item in component.instances.values()], [2, 3])
            return True

        spawned_definition = definition(spec(2))
        with (
            mock.patch.dict(runtime.HANDLERS, SpawnFixtureActor=spawn),
            mock.patch.object(scene_host.graph, 'entity_script_component', return_value=spawned_definition),
        ):
            self.assertTrue(host.advance(20), host.error)
        component = host.units[actor].component
        self.assertIsNone(component.instances[1].find_var(0, 7, create=False))
        self.assertTrue(host.advance(21), host.error)
        self.assertEqual(component.instances[1].find_var(0, 7).value(), 23)
        queue = host.world.play_queues[actor]
        host.retire_entity(actor)
        self.assertTrue(queue.closed)
        self.assertFalse(component.instances)
        self.assertNotIn(actor, host.units)
        self.assertNotIn(actor, host.world.entities)
        self.assertTrue(host.advance(22))

    def test_failed_spawn_queue_install_destroys_pending_instances(self):
        actor = 0x80000003
        host = self.host({1: graph()})
        host.configure_play_requests(lambda _: None)
        self.assertTrue(host.advance(20))
        host.world.bind(actor, definition=0x04000000000016C0)
        captured = []

        def reject_queue(entity, queue):
            self.assertEqual(entity, actor)
            component = host.units[entity].component
            self.assertTrue(component.instances[1].initialization_pending)
            captured.append(component)
            raise ValueError('queue install failed')

        with mock.patch.object(scene_host.graph, 'entity_script_component', return_value=definition(spec(1))):
            with (mock.patch.object(host.world, 'set_play_queue', side_effect=reject_queue),
                  self.assertRaisesRegex(ValueError, 'queue install failed')):
                host.add_spawned_entity_scripts(actor)
            self.assertFalse(captured[0].instances)
            self.assertNotIn(actor, host.units)
            self.assertNotIn(actor, host.world.script_components)
            replacement = host.add_spawned_entity_scripts(actor)

        self.assertTrue(host.advance(21), host.error)
        self.assertTrue(replacement.instances[1].initialized)

    def test_all_components_are_bound_before_any_public_schema_is_evaluated(self):
        first, second = graph(), graph()
        first.index, second.index = 1, 2
        first.fields['m_publicSchema'] = schema(row(5, {'$': 'ReadOther'}))
        host = self.host({1: first, 2: second})
        a = host.add(A, definition(spec(1)))
        host.add(B, definition(spec(2)), values={9: 23})
        self.assertTrue(host.advance(20))
        self.assertEqual(a.instances[1].find_var(0, 5).value(), 23)
        self.assertEqual(a.now, 20 * 16 + 15)

    def test_duplicate_frames_do_not_run_again_and_gaps_require_a_new_epoch(self):
        model = graph([node(0, 'STUStatescriptEntry', m_outPlug=plug((1, 'm_inPlug'))),
                       setter(1, 17)], entries=[0])
        host = self.host({1: model})
        component = host.add(A, definition(spec(1)))
        self.assertTrue(host.advance(20))
        sequence = component.seq.next_value
        self.assertTrue(host.advance(20))
        self.assertEqual(component.seq.next_value, sequence)
        for bad in (19, 22, True, -1, 0x80000000):
            with self.subTest(frame=bad), self.assertRaises(ValueError):
                host.advance(bad)
        self.assertTrue(host.advance(21))
        self.assertTrue(host.advance(24, allow_skipped_frames=True))
        self.assertEqual(component.now, 24 * 16 + 15)
        self.assertEqual(component.seq.next_value, sequence)
        with self.assertRaises(ValueError):
            host.advance(23, allow_skipped_frames=True)

    def test_strict_failure_stops_the_scene_before_later_components_run(self):
        first = graph([node(0, 'STUStatescriptEntry', m_outPlug=plug((1, 'm_inPlug'))),
                       node(1, 'UnknownAction')], entries=[0])
        second = graph([node(0, 'STUStatescriptEntry', m_outPlug=plug((1, 'm_inPlug'))),
                        setter(1, 23)], entries=[0])
        first.index, second.index = 1, 2
        host = self.host({1: first, 2: second})
        host.add(A, definition(spec(1)))
        b = host.add(B, definition(spec(2)))
        self.assertFalse(host.advance(0))
        self.assertEqual(host.error['diagnostic']['class'], 'UnknownAction')
        self.assertIsNone(b.instances[1].find_var(0, 7, create=False))
        self.assertFalse(host.advance(1))

    def test_close_cancels_future_scheduling_and_retires_shared_world_bindings(self):
        host = self.host({1: graph()})
        component = host.add(A, definition(spec(1)))
        self.assertTrue(host.advance(0))
        host.close()
        host.close()
        self.assertFalse(host.advance(1))
        self.assertFalse(component.instances)
        self.assertFalse(host.world.entities)
        self.assertFalse(host.world.script_components)

    def test_catalog_is_sealed_after_initial_startup(self):
        host = self.host({1: graph()})
        host.add(A, definition(spec(1)))
        host.advance(0)
        with self.assertRaises(ValueError):
            host.add(B, definition(spec(1)))

    def test_startup_validation_errors_are_terminal_and_not_a_successful_empty_world(self):
        host = self.host({1: graph()})
        host.add(A, definition(spec(1), m_clientOnly=1))
        self.assertFalse(host.advance(0))
        self.assertIn('client-only', host.error['error'])
        self.assertFalse(host.prepared)


if __name__ == '__main__':
    unittest.main()
