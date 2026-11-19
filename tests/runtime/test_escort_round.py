"""Drive the Rialto round through the loopback game link and decode what clients receive."""

import math
import unittest
from dataclasses import replace
from unittest.mock import patch

from ow174.game import collision, combat, contacts, content, escort, statescript, world
from ow174.game.bits import BitReader
from ow174.game.commands import Command
from ow174.game.match import SEND_EVERY
from ow174.game.script import decode, expr, graph
from tests.runtime.test_game_repeated_lifecycles import ProtocolHarness
from tests.runtime.test_game_server import pick

RIALTO = 0x0800000000000871
GIBRALTAR = 0x0800000000000184


class EscortRoundWireTests(unittest.TestCase):
    def test_gibraltar_rider_moves_the_cart_and_opens_the_first_route_gate(self):
        scene = content.game_map(GIBRALTAR, content.ESCORT_MODE)
        gates = tuple(escort.MAP_ENTITY | index for index in (1289, 1469))
        if scene is None or scene.escort is None or not collision.cache_path(GIBRALTAR).is_file():
            self.skipTest("Gibraltar and its collision cache are required")
        mode_gates = {escort.MAP_ENTITY | p.index for p in scene.escort.room_placeables if p.door}
        self.assertTrue(set(gates) <= mode_gates)
        run = ProtocolHarness()
        self.addCleanup(run.close)
        geometry = collision._read_cache(GIBRALTAR, scene.name, False, frozenset())
        run.server.collision_provider_factory = lambda: collision.StaticCollisionProvider(geometry)
        (attack,) = run.server.create_match(
            replace(scene, custom_start=content.START_IMMEDIATELY),
            [(1, "Attacker", content.SOLDIER, 1, False)],
        )
        link, player = run.connect(attack)
        match = player.match
        mode = match.escort
        spawn_doors = {entity for entity, placement in mode.doors.items()
                       if (0, escort.ATTACKERS) in placement.rooms}
        self.assertTrue(spawn_doors)
        run.until(lambda: all(entity in player.escort_door_states
                              and escort.DOOR_CLOSE_ALIAS in player.escort_door_states[entity].states
                              for entity in (*gates, *spawn_doors)))
        for entity in (*gates, *spawn_doors):
            records = [record for _, record in link.records if record.entity == entity]
            self.assertEqual(records[0].op, world.OP_CREATE)
            baseline = next(record for record in records if record.chunk is not None)
            self.assertIsNotNone(baseline.movement)
            self.assertLess(math.dist(baseline.movement.position, mode.doors[entity].position), 0.002)
            yaw = baseline.movement.yaw / world.ANGLE_UNITS
            pitch = -baseline.movement.body_pitch / world.ANGLE_UNITS
            forward = (math.sin(yaw) * math.cos(pitch), math.sin(pitch), math.cos(yaw) * math.cos(pitch))
            authored = contacts.rotate(mode.doors[entity].rotation, (0.0, 0.0, 1.0))
            self.assertLess(math.dist(forward, authored), 0.001)
            self.assertEqual(baseline.movement.velocity, (0, 0, 0))
        run.message(link, 21616, pick(player.entity, content.SOLDIER))
        run.until(lambda: player.has_body and player.body_script is not None)
        self.assertTrue(all(player.escort_door_states[entity].pose == escort.DoorPose.CLOSED
                            for entity in (*gates, *spawn_doors)))
        run.now = match.assemble_ends
        run.send(link)
        run.step(5)
        run.now = mode.deadline
        run.send(link)
        run.step(5)

        x, y, z = mode.movement().position
        support = match.collision_world.ground(x, z, y + 4, y + 0.5, collision.TEAM1)
        self.assertIsNotNone(support)
        player.mover.position = [x, support.y, z]
        player.mover.body = None
        start = mode.payload_collision.rider(player.mover)
        self.assertIsNotNone(start)
        first_frame = player.body_script.frame + 1
        for frame in range(first_frame, first_frame + 80):
            player.take_commands([Command(frame)])
            run.step()
        self.assertGreater(mode.distance, 1.5)
        self.assertLess(math.dist(mode.payload_collision.rider(player.mover), start), 0.12)
        self.assertTrue(any(record.entity == mode.payload and record.movement is not None
                            and record.movement.body_pitch != 0 for _, record in link.records))
        self.assertEqual(player.escort_door_states[gates[0]].pose, escort.DoorPose.CLOSED)

        for _ in range(100):
            if mode.stage:
                break
            player.mover.position = mode.movement().position
            run.now += 0.984
            run.step()
        self.assertEqual(mode.stage, 1)
        run.until(lambda: player.escort_door_states[gates[0]].pose == escort.DoorPose.OPENING
                  and escort.DOOR_OPEN_ANIM in player.escort_door_states[gates[0]].states
                  and not player.escort_door_scripts[gates[0]].data_in_flight)
        door = decode.ClientBody()
        poses = []
        for _, record in link.records:
            if record.entity == gates[0] and record.chunk is not None:
                door.apply(record)
                poses.append(door.on())
        self.assertEqual(door.on(), {(1, 0)})
        self.assertIs(door.vars[1, 0x02BB], True)
        self.assertIs(door.vars[0, 0x22A5], True)
        self.assertIs(door.vars[0, 0x475B], False)
        self.assertEqual((door.vars[1, 0x07F0], door.vars[1, 0x07F3]), (7, 10))
        self.assertEqual(door.vars[1, 0x09CB], 0.5)
        self.assertIs(type(door.vars[1, 0x1B7F]), int)
        self.assertEqual(door.vars[1, 0x1B7F], 1)
        self.assertEqual(player.escort_door_states[gates[1]].pose, escort.DoorPose.CLOSED)
        self.assertTrue(set(mode.room_collision[gates[0]]).isdisjoint(mode.payload_collision.room_shapes))
        self.assertEqual(poses, [set(), {(1, 1)}, {(1, 1), (1, 8)}, {(1, 1), (1, 8)}, {(1, 0)}])
        for entity in spawn_doors:
            spawn = decode.ClientBody()
            for _, record in link.records:
                if record.entity == entity and record.chunk is not None:
                    spawn.apply(record)
            self.assertEqual(spawn.on(), {(1, escort.DOOR_OPEN_ANIM), (1, escort.DOOR_OPEN_ALIAS)})
        link.records.clear()
        run.now = player.escort_door_states[gates[0]].ends_at
        run.send(link)
        run.until(lambda: player.escort_door_states[gates[0]].pose == escort.DoorPose.OPEN
                  and not player.escort_door_scripts[gates[0]].data_in_flight)
        settle = next(record for _, record in link.records
                      if record.entity == gates[0] and record.chunk is not None)
        finished = door.apply(settle)
        self.assertEqual(door.on(), {(1, escort.DOOR_OPEN_ANIM), (1, escort.DOOR_OPEN_ALIAS)})
        self.assertEqual(finished.instances[1].states[escort.DOOR_OPEN_ANIM],
                         (True, {"counter": 2, "time": 7000}))
        self.assertIs(door.vars[1, 0x02BB], False)
        self.assertIs(door.vars[0, 0x475B], True)

    def test_waiting_hud_is_removed_when_escort_begins_assembly(self):
        scene = content.game_map(GIBRALTAR, content.ESCORT_MODE)
        if scene is None or scene.escort is None:
            self.skipTest("Gibraltar rail is required in the local 104319 runtime")
        mode = escort.EscortRound(scene.escort)
        stream = statescript.Stream(world.GAME_MODE_ENTITY)
        client = decode.ClientBody()

        waiting = mode.mode_state(0, None)
        client.apply(stream.full_frame(waiting.frame(None)))
        self.assertEqual(client.vars[(1, 59)], escort.Phase.WAITING)
        self.assertNotIn((0, escort.COUNTDOWN), client.vars)
        self.assertIs(client.vars[0, combat.WAITING_FOR_PLAYERS], True)
        self.assertIs(client.vars[3, combat.WAITING_FOR_PLAYERS], True)
        self.assertIn((3, 16), client.on())
        self.assertIn((1, combat.WAITING_FOR_PLAYERS_STATE), client.on())
        self.assertNotIn((1, combat.ASSEMBLE_HEROES_STATE), client.on())
        stream.delivered = stream.last

        assembling = mode.mode_state(5, 35)
        (delta,) = stream.reliable_frames(assembling.frame(waiting), 8192, full=False)
        client.apply(delta)
        self.assertEqual(client.vars[(1, 59)], escort.Phase.ASSEMBLE)
        self.assertEqual(client.vars[(0, escort.COUNTDOWN)], 30)
        self.assertNotIn(3, client.graphs)
        self.assertIs(client.vars[0, combat.WAITING_FOR_PLAYERS], False)
        self.assertIn((1, combat.ASSEMBLE_HEROES_STATE), client.on())
        self.assertNotIn((1, combat.WAITING_FOR_PLAYERS_STATE), client.on())

    def test_rialto_cart_phases_checkpoints_and_win_reach_the_client_link(self):
        scene = content.game_map(RIALTO, content.ESCORT_MODE)
        if scene is None or scene.escort is None or graph.graph(escort.PAYLOAD_GRAPH) is None:
            self.skipTest("Rialto rail and payload graph are required in the local 104319 runtime")
        cache_file = collision.cache_path(RIALTO)
        if not cache_file.is_file():
            self.skipTest("Rialto collision cache is required")
        run = ProtocolHarness()
        self.addCleanup(run.close)
        # Use the existing map geometry and the real match path with controlled tick deadlines.
        geometry = collision._read_cache(RIALTO, scene.name, False, frozenset())
        run.server.collision_provider_factory = lambda: collision.StaticCollisionProvider(geometry)
        attack, late = run.server.create_match(
            replace(scene, custom_start=content.START_IMMEDIATELY),
            [(1, "Attacker", content.SOLDIER, 1, False), (2, "Late", content.SOLDIER, 0, False)],
        )
        link, player = run.connect(attack)
        match = player.match
        mode = match.escort
        mode_ui, cart_ui = decode.ClientBody(), decode.ClientBody()
        self.assertIsNotNone(mode)
        checkpoint_uis = {entity: decode.ClientBody() for _, entity in mode.checkpoints}
        door_uis, visible_barriers, mode_actions, progress_frames = {}, {}, {}, {}
        local_hud_graphs = {0x1FEB, combat.MODE_UI, 0x106B}

        def receive(peer, mode_client, cart_client):
            pos = None
            visible = visible_barriers.setdefault(peer, set())
            for _, record in peer.records:
                if record.entity in mode.barriers or record.entity in mode.overlays:
                    if record.op == world.OP_CREATE:
                        visible.add(record.entity)
                    elif record.op == world.OP_DESTROY:
                        visible.discard(record.entity)
                    if record.chunk is not None:
                        receiver = door_uis.setdefault((peer, record.entity), decode.ClientBody())
                        receiver.apply(record)
                    continue
                if record.entity in mode.doors:
                    receiver = door_uis.setdefault((peer, record.entity), decode.ClientBody())
                elif record.entity == mode.payload:
                    if record.movement is not None:
                        pos = record.movement.position
                    receiver = cart_client
                elif record.entity == world.GAME_MODE_ENTITY:
                    receiver = mode_client
                elif record.entity in checkpoint_uis:
                    receiver = checkpoint_uis[record.entity]
                else:
                    continue
                if record.chunk is not None:
                    header = BitReader(record.chunk.getvalue())
                    first = decode.w_u32(header)
                    last = first + decode.w_var(header)
                    if first <= receiver.applied + 1 and last > receiver.applied:
                        if receiver is cart_client and not receiver.applied:
                            self.assertEqual(mode_client.graphs.get(1), escort.MODE_GRAPH)
                        frame = receiver.apply(record)
                        if receiver is mode_client:
                            self.assertTrue(local_hud_graphs.isdisjoint(mode_client.graphs.values()))
                            phase_update = frame.instances.get(1)
                            if (first and phase_update
                                    and phase_update.states.get(escort.MATCH_LIVE) == (True, {})):
                                self.assertIs(cart_client.vars[0, escort.PAYLOAD_ACTIVE], True)
                                self.assertIs(cart_client.vars[1, escort.PAYLOAD_ENABLED], True)
                            mode_actions.setdefault(peer, []).extend(frame.actions)
                            if frame.owner:
                                progress_frames.setdefault(peer, []).append(frame)
            peer.records.clear()
            return pos

        def check_rooms(peer, player):
            self.assertEqual(visible_barriers[peer], mode.room_visuals(player.team))
            for entity in visible_barriers[peer]:
                index = escort.BARRIER_GRAPH if entity in mode.barriers else escort.ROOM_OVERLAY_GRAPH
                if graph.graph(index) is not None:
                    visual = door_uis[(peer, entity)]
                    self.assertEqual(visual.graphs[1], index)
                    self.assertIn((1, 3), visual.on())
            for entity in mode.doors:
                ui = door_uis[(peer, entity)]
                if graph.graph(escort.DOOR_GRAPH) is not None:
                    pose = player.escort_door_states[entity].pose
                    opened = pose != escort.DoorPose.CLOSED
                    self.assertIs(ui.vars[(0, 0x22A5)], opened)
                    self.assertIs(ui.vars[(0, 0x475B)], pose == escort.DoorPose.OPEN)
                    self.assertIs(ui.vars[(0, 0x0804)], True)
                    expected = {(1, state) for state in player.escort_door_states[entity].states}
                    self.assertEqual(ui.on(), expected)
                    playing = pose == escort.DoorPose.OPENING and (1, escort.DOOR_OPEN_ANIM) in expected
                    self.assertIs(ui.vars[1, 0x02BB], playing)
                    self.assertIs(ui.vars[1, 0x3DF2], not playing and pose != escort.DoorPose.OPEN)

        def check_cart_route(cart):
            # 1225 state 119 positions the 2C8 HUD markers using these replicated values.
            route_length = cart.vars[0, 0x1D9F]
            self.assertAlmostEqual(route_length, mode.length, delta=0.001)
            self.assertEqual(cart.vars[0, 0x1EC7], 1.0)
            fractions = cart.vars[1, 0x1D9E]
            for fraction, (distance, _) in zip(fractions, mode.checkpoints, strict=True):
                self.assertAlmostEqual(fraction * route_length, distance, delta=0.001)
            stage = cart.vars[1, 0x2934]
            start = fractions[stage - 1] * route_length if stage else 0.0
            section_length = fractions[stage] * route_length - start
            distance = start + cart.vars[0, 0x009A] * section_length
            self.assertAlmostEqual(cart.vars[0, 0x01DF], distance, delta=0.001 + 0.0005 * section_length)

        run.until(lambda: player.mode_script.delivered and player.escort_payload_script.delivered
                  and not player.mode_script.data_in_flight and player.escort_mode_state is not None
                  and combat.ASSEMBLE_HEROES_STATE in player.escort_mode_state.states)
        run.until(lambda: {escort.MAP_ENTITY | index for index in scene.placeables} <= {
            record.entity for _, record in link.records if record.op == world.OP_CREATE
        })
        created = {record.entity for _, record in link.records if record.op == world.OP_CREATE}
        self.assertIn(mode.payload, created)
        self.assertTrue({entity for _, entity in mode.checkpoints} <= created)
        pos = receive(link, mode_ui, cart_ui)
        self.assertEqual(mode_ui.vars[(1, 59)], escort.Phase.ASSEMBLE)
        self.assertIn((1, combat.ASSEMBLE_HEROES_STATE), mode_ui.on())
        self.assertNotIn((1, combat.WAITING_FOR_PLAYERS_STATE), mode_ui.on())
        self.assertIs(mode_ui.vars[0, combat.WAITING_FOR_PLAYERS], False)
        self.assertIn((1, 95), mode_ui.on())
        self.assertEqual(mode_ui.vars[(0, 1923)], expr.Asset(0x0DE0000000006115))
        for i, checkpoint in enumerate(checkpoint_uis.values()):
            self.assertEqual(checkpoint.vars[(0, escort.OBJECTIVE_KIND)], escort.PAYLOAD_OBJECTIVE)
            self.assertEqual(checkpoint.vars[(0, 10548)], i)
            self.assertEqual(checkpoint.vars[(0, 2190)], expr.Entity(mode.payload))
        self.assertEqual(cart_ui.on(), {(1, 23)})
        check_cart_route(cart_ui)
        self.assertLess(math.dist(mode.movement().position, pos), 0.002)
        run.message(link, 21616, pick(player.entity, content.SOLDIER))
        run.until(lambda: player.has_body)
        first_spawn = player.spawn
        run.now = match.assemble_ends
        run.send(link)
        run.step(5)
        receive(link, mode_ui, cart_ui)
        self.assertEqual(mode_ui.vars[(1, 59)], escort.Phase.SETUP)
        self.assertEqual(mode_ui.vars[(0, escort.COUNTDOWN)], 45)
        self.assertEqual(mode_ui.vars[(0, 1923)], expr.Asset(0x0DE0000000006116))
        self.assertNotIn((1, combat.ASSEMBLE_HEROES_STATE), mode_ui.on())
        self.assertIs(cart_ui.vars[0, escort.PAYLOAD_ACTIVE], False)
        self.assertIs(cart_ui.vars[1, escort.PAYLOAD_ENABLED], False)
        self.assertFalse(player.select_open)
        check_rooms(link, player)

        late_link, late_player = run.connect(late)
        run.until(lambda: late_player.escort_payload_script.delivered)
        late_mode, late_cart = decode.ClientBody(), decode.ClientBody()
        receive(late_link, late_mode, late_cart)
        self.assertEqual(late_mode.vars[(1, 59)], escort.Phase.SETUP)
        self.assertIs(late_cart.vars[0, escort.PAYLOAD_ACTIVE], False)
        self.assertTrue(late_player.select_open)
        check_rooms(late_link, late_player)
        late_first_spawn = late_player.spawn

        run.now = mode.deadline
        run.send(link)
        run.send(late_link)
        run.until(lambda: all(p.escort_hud_bound and not p.mode_script.data_in_flight
                              for p in (player, late_player)))
        receive(link, mode_ui, cart_ui)
        receive(late_link, late_mode, late_cart)
        self.assertEqual(mode_ui.vars[(1, 59)], escort.Phase.ACTIVE)
        self.assertEqual(mode_ui.vars[(0, escort.COUNTDOWN)], 240)
        self.assertEqual(mode_ui.vars[0, 1923], expr.Asset(escort.ACTIVE_HEADING[escort.ATTACKERS]))
        self.assertEqual(late_mode.vars[0, 1923], expr.Asset(escort.ACTIVE_HEADING[escort.DEFENDERS]))
        self.assertNotIn((1, 14), mode_ui.on())
        self.assertIn((1, escort.MATCH_LIVE), mode_ui.on())
        self.assertIn((1, escort.ESCORT_HUD), mode_ui.on())
        self.assertIn((1, escort.HUD_PROGRESS), mode_ui.on())
        self.assertIn((1, escort.HUD_PAYLOAD), mode_ui.on())
        self.assertIn((1, escort.HUD_OBJECTIVE_KIND), mode_ui.on())
        self.assertIn((2, 2), mode_ui.on())
        self.assertIn((2, 54), mode_ui.on())
        self.assertNotIn((2, 10), mode_ui.on())
        self.assertEqual(mode_ui.records[2], (escort.MODE_MESSAGES, (1, 12)))
        self.assertEqual(mode_ui.records[5], (escort.MODE_HUD_VALUES, (2, 50)))
        self.assertIn((5, 0), mode_ui.on())
        context = mode_ui.vars[0, escort.HUD_ROUND_DATA]
        self.assertEqual(context.get(escort.CHECKPOINT_INDEX), 0)
        self.assertEqual(context.get(escort.HUD_CLOCK), 240)
        self.assertIs(context.get(0x2259), True)  # the native presenter selects the payload title branch
        self.assertIs(mode_ui.vars[0, 0x4C47], True)
        self.assertEqual(mode_ui.vars[0, 59], escort.Phase.ACTIVE)
        self.assertEqual(mode_ui.vars[0, 2190], expr.Entity(mode.payload))
        self.assertEqual(mode_ui.vars[0, 12376], tuple(expr.Entity(entity) for _, entity in mode.checkpoints))
        self.assertEqual([(target, node) for _, target, node in mode_actions[link]],
                         [(1, escort.HUD_CART_CHANGED)])
        self.assertEqual([(target, node) for _, target, node in mode_actions[late_link]],
                         [(1, escort.HUD_CART_CHANGED)])
        for peer, client in ((link, mode_ui), (late_link, late_mode)):
            (binding_frame,) = progress_frames[peer]
            self.assertGreater(binding_frame.cmfd, 0)
            self.assertTrue(all(binding_frame.instances[1].states[state] == (True, {})
                                for state in escort.HUD_PROGRESS_BINDINGS))
            self.assertTrue({(1, state) for state in escort.HUD_PROGRESS_BINDINGS} <= client.on())
        self.assertIs(cart_ui.vars[1, escort.PAYLOAD_ENABLED], True)
        self.assertEqual(cart_ui.vars[1, 0x2934], 0)
        self.assertEqual(cart_ui.vars[1, 0x1A9F], ())
        self.assertEqual(cart_ui.vars[1, 0x1AA0], ())
        self.assertEqual(cart_ui.vars[0, 0x01B3], 0)
        check_cart_route(cart_ui)
        self.assertEqual(cart_ui.vars[0, 0x01DF], 0.0)
        native_adder = graph.graph(escort.MODE_GRAPH).state(11)
        self.assertTrue(native_adder.client_only)
        self.assertEqual(native_adder.field("m_36F7B64C.m_36F7B64C.m_C974E369.m_graph"),
                         "0x0580000000001FEB")
        container = graph.graph(0x1FEB).state(2)
        self.assertEqual(container.cls, "STUStatescriptStateUXPresenter")
        self.assertEqual(container.field("m_45216F79.m_D44EB181.m_identifier"), 0x2CB5)
        self.assertEqual(container.field("m_45017CEA.m_identifier"), 0x2F9E)

        class ObjectiveContext(expr.Context):
            def var(self, scope, variable):
                return mode_ui.vars.get((0 if scope else 1, variable))

            def member(self, entity, variable):
                if entity == mode.payload:
                    return cart_ui.vars.get((0, variable))
                return checkpoint_uis[entity].vars.get((0, variable))

        hud = graph.graph(escort.MODE_GRAPH).state(escort.ESCORT_HUD)
        objective = next(param["m_value"] for param in hud.field("m_params")
                         if param["m_B5051BCE"] == "0x0A400000000004E2")
        self.assertEqual(expr.evaluate(objective, ObjectiveContext()), expr.Asset(0x0D8000000000088E))
        native_binding = graph.graph(escort.MODE_GRAPH).state(95)
        cart = next(row["m_value"] for row in native_binding.field("m_values")
                    if row["m_identifier"] == "0x0D80000000003267")
        self.assertEqual(expr.evaluate(cart, ObjectiveContext()), expr.Entity(mode.payload))
        check_rooms(link, player)
        check_rooms(late_link, late_player)

        with patch.object(run.server.environment, "wall_time", return_value=run.now):
            (active_join,) = run.server.join_match(match, [(3, "Active join", content.SOLDIER, 0, False)])
        active_link, active_player = run.connect(active_join)
        run.until(lambda: active_player.escort_hud_bound and not active_player.mode_script.data_in_flight)
        active_mode, active_cart = decode.ClientBody(), decode.ClientBody()
        receive(active_link, active_mode, active_cart)
        self.assertEqual(active_mode.vars[1, 59], escort.Phase.ACTIVE)
        self.assertEqual(active_mode.vars[1, escort.HUD_CART_REFERENCE], expr.Entity(mode.payload))
        self.assertIn((5, 0), active_mode.on())
        self.assertEqual(active_mode.vars[0, 1923], expr.Asset(escort.ACTIVE_HEADING[escort.DEFENDERS]))
        self.assertEqual(len(progress_frames[active_link]), 1)
        self.assertTrue({(1, state) for state in escort.HUD_PROGRESS_BINDINGS} <= active_mode.on())
        self.assertIs(active_cart.vars[1, escort.PAYLOAD_ENABLED], True)
        self.assertIs(active_cart.vars[0, escort.PAYLOAD_ACTIVE], True)
        check_cart_route(active_cart)
        self.assertEqual([(target, node) for _, target, node in mode_actions[active_link]],
                         [(1, escort.HUD_CART_CHANGED)])
        for entity in mode.doors:
            if mode.door_open(entity):
                door = door_uis[active_link, entity]
                self.assertEqual(door.on(), {(1, escort.DOOR_OPEN_ANIM), (1, escort.DOOR_OPEN_ALIAS)})
                self.assertIs(door.vars[1, 0x3DF1], True)
                self.assertIs(door.vars[1, 0x02BB], False)

        if scene.escort.payload_collision:
            x, y, z = mode.movement().position
            player.mover.position = [x, y + 0.5, z - 3.5]
            run.until(lambda: player.body_script is not None)
            first_frame = player.body_script.frame + 1
            for frame in range(first_frame, first_frame + 40):
                player.take_commands([Command(frame, forward=127)])
                run.step()
            self.assertLess(player.mover.position[2], z - 0.2)
            self.assertGreater(mode.distance, 0.0)
            receive(link, mode_ui, cart_ui)
            check_cart_route(cart_ui)
            self.assertGreater(cart_ui.vars[0, 0x01DF], 0.0)
            self.assertLess(cart_ui.vars[0, 0x01DF], cart_ui.vars[0, 0x1D9F])
            self.assertEqual(cart_ui.vars[1, 0x1A9F], (expr.Entity(player.body),))
            self.assertEqual(cart_ui.vars[0, 0x01B3], 1)
            self.assertGreater(mode_ui.vars[(0, 13468)], 0.0)
            native_progress = graph.graph(escort.MODE_GRAPH).state(escort.HUD_PROGRESS).field("m_value")
            progress = expr.evaluate(native_progress, ObjectiveContext())
            self.assertGreater(progress, 0.0)
            self.assertEqual(progress, cart_ui.vars[(0, 0x009A)])
            self.assertAlmostEqual(progress, mode_ui.vars[0, 13468], delta=0.002)
            for state, key in zip(escort.HUD_PROGRESS_BINDINGS, (0x349C, 0x34F3), strict=True):
                (field,) = graph.graph(escort.MODE_GRAPH).state(state).field("m_values")
                self.assertEqual(int(field["m_identifier"], 16) & 0xFFFF, key)
                self.assertIn((1, state), mode_ui.on())
                self.assertGreater(expr.evaluate(field["m_value"], ObjectiveContext()), 0.0)

        run.message(late_link, 21616, pick(late_player.entity, content.SOLDIER))
        run.until(lambda: late_player.has_body)
        player.mover.position = late_player.mover.position = mode.movement().position
        run.step(12)
        for peer, mode_ui_peer, cart in ((link, mode_ui, cart_ui), (late_link, late_mode, late_cart)):
            receive(peer, mode_ui_peer, cart)
            self.assertEqual(cart.vars[1, 0x1A9F], (expr.Entity(player.body),))
            self.assertEqual(cart.vars[1, 0x1AA0], (expr.Entity(late_player.body),))
            self.assertEqual(cart.vars[0, 0x01B3], 0)
        late_player.mover.position = late_first_spawn[0]
        run.step(12)
        receive(link, mode_ui, cart_ui)
        self.assertEqual(cart_ui.vars[1, 0x1AA0], ())
        self.assertEqual(cart_ui.vars[0, 0x01B3], 1)
        windows = []
        for stage in (1, 2, 3):
            end_before = mode.deadline
            # Stand next to the cart at each sample so the server advances it along the whole rail.
            for _ in range(120):
                if mode.stage == stage:
                    break
                player.mover.position = mode.movement().position
                run.now += 0.984
                run.step()
            else:
                self.fail(f"Cart never reached stage {stage}")
            run.step(5)
            run.step(-match.tick % SEND_EVERY)
            pos = receive(link, mode_ui, cart_ui)
            receive(late_link, late_mode, late_cart)
            check_rooms(link, player)
            check_rooms(late_link, late_player)
            check_cart_route(cart_ui)
            check_cart_route(late_cart)
            self.assertLess(math.dist(mode.movement().position, pos), 0.002)
            windows.append(mode.deadline - end_before)
            if stage < 3:
                self.assertEqual(mode_ui.vars[0, escort.CHECKPOINT_INDEX], stage)
                self.assertEqual(mode_ui.vars[0, escort.HUD_ROUND_DATA].get(escort.CHECKPOINT_INDEX), stage)
                self.assertEqual(mode_ui.vars[(1, 10551)], expr.Entity(mode.checkpoints[stage][1]))
                self.assertEqual(cart_ui.on(), {(1, 17)})
                self.assertNotEqual(player.spawn, first_spawn)
                self.assertNotEqual(late_player.spawn, late_first_spawn)
        self.assertEqual(windows, [150.0, 90.0, 0.0])
        self.assertEqual(mode.winner, 1)
        self.assertEqual(mode_ui.vars[(1, 59)], escort.Phase.COMPLETE)
        self.assertEqual(mode_ui.vars[(1, 7525)], escort.ATTACKERS)
        self.assertIn((1, 20), mode_ui.on())  # 0F4A sends the winning team in message 0695
        self.assertEqual(cart_ui.on(), {(1, 23)})
        self.assertIs(cart_ui.vars[1, escort.PAYLOAD_ENABLED], True)
        self.assertIs(cart_ui.vars[0, escort.PAYLOAD_ACTIVE], False)
        self.assertEqual(cart_ui.vars[1, 0x1A9F], ())
        self.assertEqual(cart_ui.vars[0, 0x01B3], 0)
        self.assertAlmostEqual(cart_ui.vars[0, 0x01DF], cart_ui.vars[0, 0x1D9F], delta=0.001)
