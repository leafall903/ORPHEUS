"""The native wheel graphs enter the ordinary player body stream."""

import json
import struct
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from test_game_combat import KANEZAKA_DM, Run
from test_game_entity_lifecycle import client as game_client

from ow174.accounts.profile import Profile
from ow174.catalog.items import ItemDB
from ow174.catalog.templates import RetailTemplates
from ow174.content.collection import Collection
from ow174.game import combat, messages, observers, statescript
from ow174.game.bits import BitWriter
from ow174.game.body_vm_snapshot import export_body_vm
from ow174.game.collision import StaticCollisionProvider, World, flat_world
from ow174.game.content import PRACTICE_RANGE, SOLDIER
from ow174.game.match import Match
from ow174.game.script import decode, expr, graph, runtime, spray_placement
from ow174.game.script.driver import BodyScript, BodyStream
from ow174.paths import EMOTE_ALIAS_DATA, VOICE_EFFECT_DATA


class CommunicationWheelTests(unittest.TestCase):
    def test_body_create_mounts_and_replicates_the_authored_wheels(self):
        match = Match(PRACTICE_RANGE, collision_provider=StaticCollisionProvider(flat_world(1.0)))
        player = match.add_player(1, "Soldier", SOLDIER, 0, False)
        [create] = match.own_body_updates(player)
        body = player.body_script
        client = decode.ClientBody.for_hero(SOLDIER)
        self.assertEqual(body.frames(), [])

        create.stream.arrived(0)
        [tree] = body.frames()
        client.apply(tree)
        body.stream.arrived(tree.chunk_last)
        instances = {index: instance for instance, index in client.graphs.items()}
        parent = instances[0x004B]
        self.assertEqual(client.records[instances[0x02CF]], (0x02CF, (parent, 6)))
        self.assertEqual(client.records[instances[0x05AD]], (0x05AD, (parent, 7)))
        self.assertNotIn(0x01CB, instances)  # 004B's separate tagged filter

        body.command(body.frame + 1, 0)
        [owner] = body.frames()
        client.apply(owner)
        communication = instances[0x05AD]
        for state in (25, 26, 40, 41, 42):
            self.assertTrue(client.states[communication, state])
        self.assertTrue(client.states[instances[0x02CF], 20])

    def test_client_out_instance_action_reaches_the_current_body(self):
        def receive(connection, sequence, action, confirmed):
            # Protocol 1, message offset 8, then u64, bool and nested u32.
            record = b"\x01\x08" + struct.pack("<QBI", action, confirmed, 0)
            self.assertEqual(messages.encode(21608, {
                "+0x78": action, "+0x80": confirmed, "+0x84": {"+0x0": 0},
            }), record)
            packet = BitWriter()
            messages.write_channel(packet, [(sequence, record)])
            packet.bit(0)
            connection._payload_in(packet.getvalue(), 17.0)

        run = Run(names=("Source", "Observer"))
        player, observer = run.players
        connection = game_client()
        connection.player = player
        player.client = connection
        source = player.body_script
        wheel = next(i for i in source.component.instances.values() if i.graph.index == 0x05AD)
        selected = 0x0A9000000000016B
        for sequence, action in enumerate((0x0A9000010000016B, 0x0A90000000000001)):
            receive(connection, sequence, action, True)
        self.assertEqual(run.match._body_messages, [])

        receive(connection, 2, selected, True)
        receive(connection, 2, selected, True)
        self.assertEqual(len(run.match._body_messages), 1)
        run.step({})
        self.assertEqual(wheel.vars[29699].value(), expr.Asset(selected))
        self.assertIs(wheel.vars[9253].value(), True)
        [callout] = [i for i in source.component.instances.values() if i.graph.index == 0x0686]
        self.assertEqual(callout.vars[3793].value(), expr.Asset(0x0C00000000005A0B))
        self.assertFalse(any(i.graph.index == 0x0686 for i in observer.body_script.component.instances.values()))

        receive(connection, 3, selected, False)
        run.step({})
        self.assertIs(wheel.vars[9253].value(), False)

    def test_wheel_tracks_visible_players_before_the_authored_recipient_filter(self):
        run = Run(names=("Speaker", "Aligned", "Nearer"))
        speaker, aligned, nearer = run.players
        match = run.match
        speaker.mover.position = (0.0, 0.0, 0.0)
        speaker.mover.yaw = speaker.mover.pitch = 0
        aligned.mover.position = (0.0, 0.0, 15.0)
        nearer.mover.position = (0.8, 0.0, 8.0)
        script = speaker.body_script
        wheel = next(i for i in script.component.instances.values() if i.graph.index == 0x05AD)

        def select():
            script.command(script.frame + 150, 0)
            match.communication_action(speaker, {"+0x78": 0x0A900000000001E5, "+0x80": True})
            match.deliver_body_messages()
            return wheel.vars[375].value()

        self.assertEqual(select(), (expr.Entity(aligned.body),))

        wall = ((-0.4, 0, 5, 0.4, 0, 5, -0.4, 4, 5),
                (0.4, 0, 5, 0.4, 4, 5, -0.4, 4, 5))
        match.collision_world = World(wall)
        self.assertEqual(select(), (expr.Entity(nearer.body),))
        nearer.mover.position = (0.0, 0.0, 41.0)
        self.assertEqual(select(), ())
        self.assertFalse(wheel.node_state(wheel.graph.node(115)).active)

    def test_confirmed_action_directs_callout_only_to_the_aimed_teammate(self):
        cases = (
            (0x1E5, 0x12F4, PRACTICE_RANGE, True),
            (0x170, 0x05AC, PRACTICE_RANGE, True),  # Acknowledge without a selected UI marker
            (0x1E5, 0x12F4, PRACTICE_RANGE, False),
            (0x1E5, 0x12F4, KANEZAKA_DM, True),
        )
        for action, target_message, game_map, same_team in cases:
            with self.subTest(action=hex(action), mode=game_map.mode_guid, same_team=same_team):
                directed = same_team and not game_map.free_for_all
                run = Run(game_map, names=("Speaker", "Target", "Other"))
                speaker, target, other = run.players
                target.team = speaker.team if same_team else 1 - speaker.team
                other.team = speaker.team
                speaker.mover.position = (0.0, 0.0, 0.0)
                speaker.mover.yaw = speaker.mover.pitch = 0
                target.mover.position = (0.0, 0.0, 10.0)
                other.mover.position = (10.0, 0.0, 0.0)
                connection = game_client()
                connection.player = speaker
                speaker.client = connection

                record = b"\x01\x08" + struct.pack("<QBI", 0x0A90000000000000 | action, True, 0)
                packet = BitWriter()
                messages.write_channel(packet, [(0, record)])
                packet.bit(0)
                connection._payload_in(packet.getvalue(), 17.0)
                run.match.deliver_body_messages()
                queued = run.match._body_messages
                if directed:
                    [(source, recipient, _, message, _)] = queued
                    self.assertIs(source, speaker.body_script)
                    self.assertIs(recipient, target)
                    self.assertEqual(message, 0x0240000000000000 | target_message)
                else:
                    self.assertEqual(queued, [])
                    self.assertTrue(any(i.graph.index == 0x0686
                                        for i in speaker.body_script.component.instances.values()))
                run.match.deliver_body_messages()
                for player in (target, other):
                    wheel = next(i for i in player.body_script.component.instances.values()
                                 if i.graph.index == 0x05AD)
                    caller = expr.Entity(speaker.body) if directed and player is target else None
                    self.assertEqual(wheel.vars[40].value() if 40 in wheel.vars else None, caller)
                    self.assertEqual(wheel.vars[41].value() if 41 in wheel.vars else None, caller)

    def test_selection_message_takes_the_authored_category_branches(self):
        run_node = runtime.Instance.run_node
        visited = set()

        def record(instance, node, caller):
            visited.add((instance.graph.index, node.pos))
            return run_node(instance, node, caller)

        cases = (
            (0x16B, (0x05AD, 140)),  # fixed communication
            (0x1E5, (0x05AD, 140)),  # configurable communication
            (0x172, (0x05AD, 107)),  # default emote is also in the main wheel
            (0x19F, (0x05AD, 107)),  # alternate emote
            (0x173, (0x02CF, 33)),   # default spray slot
            (0x1A5, (0x02CF, 33)),   # alternate spray slot
        )
        branches = {branch for _, branch in cases}
        with patch.object(runtime.Instance, "run_node", record):
            for choice, expected in cases:
                with self.subTest(choice=hex(choice)):
                    body = BodyScript(SOLDIER, 0xA0000101, 1000, communication_wheel=True)
                    visited.clear()
                    runtime.send_message(
                        body.component, 0x02400000000002A3,
                        {0x8FC: expr.Asset(0x0A90000000000000 | choice), 0x3E1: True}, body.entity,
                    )
                    body.component.run_now()
                    self.assertEqual(visited & branches, {expected})

    def test_category_query_requires_the_full_action_guid(self):
        body = BodyScript(SOLDIER, 0xA0000101, 1000, communication_wheel=True)
        main = next(instance for instance in body.component.instances.values() if instance.graph.index == 0x05AD)
        condition = main.graph.node(182).field("m_condition")
        for value, expected in (
            (expr.Asset(0x0A90000000000170), True),
            (expr.Asset(0x0A90000100000170), False),
            (expr.Asset(0x02E0000000000170), False),
            (0x170, False),
        ):
            with self.subTest(value=value):
                main.write((expr.INSTANCE, 29699), value)
                self.assertIs(main.evaluate(condition), expected)

    def test_emote_and_spray_actions_use_native_slot_order(self):
        body = BodyScript(SOLDIER, 0xA0000101, 1000, communication_wheel=True)
        instances = {instance.graph.index: instance for instance in body.component.instances.values()}
        main, spray = instances[0x05AD], instances[0x02CF]
        emote_slot = main.graph.node(107).field("m_condition")["m_configVars"][0]["m_slot"]
        spray_slot = spray.graph.node(33).field("m_value")
        for instance, config, actions, unmatched in (
            (main, emote_slot, (0x172, 0x1A1, 0x1A0, 0x19F), 0x1A2),
            (spray, spray_slot, (0x173, 0x1A5, 0x1A4, 0x1A3), 0x1A6),
        ):
            for slot, action in enumerate(actions):
                with self.subTest(action=hex(action)):
                    instance.write((expr.INSTANCE, 29699), expr.Asset(0x0A90000000000000 | action))
                    self.assertEqual(instance.evaluate(config), slot)
            instance.write((expr.INSTANCE, 29699), expr.Asset(0x0A90000000000000 | unmatched))
            self.assertEqual(instance.evaluate(config), -1)

        runtime.send_message(
            body.component, 0x02400000000002A3,
            {0x8FC: expr.Asset(0x0A900000000001A4)}, body.entity,
        )
        body.component.run_now()
        self.assertEqual(spray.vars[9259].value(), 2)

    def test_equipped_animation_selection_replicates_authored_emote_graphs(self):
        # Soldier's Heroic and Take a Knee unlocks from 104319.
        heroic, kneel = "0x0250000000000904", "0x0250000000000BE2"
        default_alias, chosen_alias = 0x0C8000000000045C, 0x0C80000000000476
        with TemporaryDirectory() as directory:
            data = Path(directory)
            (data / EMOTE_ALIAS_DATA).write_text(
                json.dumps({heroic: hex(default_alias), kneel: hex(chosen_alias)}), encoding="utf-8",
            )
            items = ItemDB(data)
        collection = Collection(RetailTemplates(), items)
        profile = Profile()
        self.assertEqual(collection.emote_aliases(profile, SOLDIER), (default_alias,))
        profile.loadouts[f"0x{SOLDIER:016X}"] = {"+0x88": [heroic, kneel, "0x0"]}
        match = Match(
            PRACTICE_RANGE, collision_provider=StaticCollisionProvider(flat_world(1.0)),
            emote_aliases_of=lambda account, hero: collection.emote_aliases(profile, hero) if account == 1 else (),
        )
        player = match.add_player(1, "Soldier", SOLDIER, 0, False)
        [create] = match.own_body_updates(player)
        body = player.body_script
        client = decode.ClientBody.for_hero(SOLDIER)
        create.stream.arrived(0)

        def replicate():
            for update in body.frames():
                client.apply(update)
                body.stream.arrived(update.chunk_last)

        replicate()
        self.assertEqual(body.world.emote_aliases, (default_alias, chosen_alias, 0))
        wheel = next(instance for instance in body.component.instances.values() if instance.graph.index == 0x05AD)
        query = wheel.graph.node(107).field("m_condition")["m_configVars"][0]
        for choice, expected in ((0x172, default_alias), (0x1A1, chosen_alias), (0x1A0, 0), (0x19F, 0), (0x1A2, 0)):
            with self.subTest(choice=hex(choice)):
                wheel.write((expr.INSTANCE, 29699), expr.Asset(0x0A90000000000000 | choice))
                self.assertEqual(wheel.evaluate(query), expr.Asset(expected))

        runtime.send_message(
            body.component, 0x02400000000002A3,
            {0x8FC: expr.Asset(0x0A900000000001A1), 0x3E1: True}, body.entity,
        )
        body.command(body.frame + 1, 0)
        replicate()

        instances = {instance.graph.index: instance for instance in body.component.instances.values()}
        emote, animation, helper = (instances[index] for index in (0x069A, 0x023A, 0x1040))
        self.assertIs(animation.parent, emote)
        self.assertEqual(animation.vars[2254].value(), expr.Asset(chosen_alias))
        self.assertEqual(client.graphs[emote.id], 0x069A)
        self.assertEqual(client.records[animation.id], (0x023A, (emote.id, animation.parent_state)))
        self.assertEqual(client.graphs[helper.id], 0x1040)

    def test_voice_lookup_uses_the_authored_equipped_slot_config(self):
        body = BodyScript(SOLDIER, 0xA0000101, 1000, communication_wheel=True)
        voice = body.component.create(graph.graph(0x0686), init=False)
        config = voice.graph.node(67).field("m_value")["m_slot"]
        for action, expected in (
            (expr.Asset(0x0A9000000000016F), 0),
            (expr.Asset(0x0A9000000000019D), 1),
            (expr.Asset(0x0A9000000000019C), 2),
            (expr.Asset(0x0A9000000000019B), 3),
            (expr.Asset(0x0A9000000000019E), -1),
            (expr.Asset(0x0A9000010000016F), -1),
            (expr.Asset(0x02E000000000016F), -1),
            (0x16F, -1),
        ):
            with self.subTest(action=action):
                voice.write((expr.INSTANCE, 29699), action)
                self.assertEqual(voice.evaluate(config), expected)

    def test_spray_unlock_and_voice_effect_use_their_own_equipped_slots(self):
        default_spray, chosen_spray = 0x025000000000008D, 0x02500000000007F1
        default_voice, chosen_voice = 0x02500000000008D7, 0x0250000000000A31
        default_effect, chosen_effect = 0x03000000000018C4, 0x0300000000001879
        with TemporaryDirectory() as directory:
            data = Path(directory)
            (data / VOICE_EFFECT_DATA).write_text(
                json.dumps({hex(default_voice): hex(default_effect), hex(chosen_voice): hex(chosen_effect)}),
                encoding="utf-8",
            )
            items = ItemDB(data)
        collection = Collection(RetailTemplates(), items)
        profile = Profile()
        self.assertEqual(collection.wheel_cosmetics(profile, SOLDIER), ((default_spray,), (default_effect,)))
        profile.loadouts[f"0x{SOLDIER:016X}"] = {
            "+0x58": [hex(default_spray), hex(chosen_spray), "0x0"],
            "+0x70": [hex(default_voice), hex(chosen_voice), "0x0"],
        }
        match = Match(
            PRACTICE_RANGE, collision_provider=StaticCollisionProvider(flat_world(1.0)),
            wheel_cosmetics_of=lambda _, hero: collection.wheel_cosmetics(profile, hero),
        )
        player = match.add_player(1, "Soldier", SOLDIER, 0, False)
        [create] = match.own_body_updates(player)
        create.stream.arrived(0)
        body = player.body_script
        for action in (0x1A5, 0x19D):
            runtime.send_message(
                body.component, 0x02400000000002A3,
                {0x8FC: expr.Asset(0x0A90000000000000 | action), 0x3E1: True}, body.entity,
            )
            body.command(body.frame + 1, 0)
        instances = {instance.graph.index: instance for instance in body.component.instances.values()}
        spray, voice = instances[0x02CF], instances[0x0686]
        self.assertEqual(spray.vars[9259].value(), 1)
        spray_query = spray.graph.node(2).field("m_E441DC25")
        voice_query = voice.graph.node(67).field("m_value")
        self.assertEqual(spray.evaluate(spray_query), expr.Asset(chosen_spray))
        self.assertEqual(voice.evaluate(voice_query), expr.Asset(chosen_effect))
        self.assertFalse(spray.state(1).active)
        self.assertNotIn((spray.id, 1), body.snapshot().states)
        self.assertNotIn((spray.id, 1), observers.snapshot(body).states)

        for slot, expected in ((0, default_spray), (2, 0), (3, 0), (-1, 0)):
            spray.write((expr.INSTANCE, 9259), slot)
            self.assertEqual(spray.evaluate(spray_query), expr.Asset(expected))
        for action, expected in ((0x16F, default_effect), (0x19C, 0), (0x19B, 0), (0x19E, 0)):
            voice.write((expr.INSTANCE, 29699), expr.Asset(0x0A90000000000000 | action))
            self.assertEqual(voice.evaluate(voice_query), expr.Asset(expected))

    def test_spray_selection_places_on_the_nearest_eligible_static_surface(self):
        def wall(z):
            return (-3, 0, z, 3, 0, z, -3, 4, z), (3, 0, z, 3, 4, z, -3, 4, z)

        pixel = 0x02500000000007F1
        for distance, unlocks, yaw, expected in (
            (3.0, (pixel,), 0, True),
            (4.51, (pixel,), 0, False),
            (3.0, (pixel,), 32768, False),
            (3.0, (), 0, False),
        ):
            with self.subTest(distance=distance, unlocks=unlocks, yaw=yaw):
                world = World((*wall(1.0), *wall(distance)), masks=(0x18, 0x18, 0x40, 0x40))
                match = Match(
                    PRACTICE_RANGE, collision_provider=StaticCollisionProvider(world),
                    wheel_cosmetics_of=lambda *_: (unlocks, ()),
                )
                self.assertTrue(match._refresh_collision())
                player = match.add_player(1, "Soldier", SOLDIER, 0, False)
                player.has_body = True
                [create] = match.own_body_updates(player)
                body = player.body_script
                player.mover.position = (0.0, 0.0, 0.0)
                player.mover.yaw = yaw
                player.mover.pitch = 0
                create.stream.arrived(0)
                owner = decode.ClientBody.for_hero(SOLDIER)
                [tree] = body.frames()
                owner.apply(tree)
                body.stream.arrived(tree.chunk_last)
                runtime.send_message(
                    body.component, 0x02400000000002A3,
                    {0x8FC: expr.Asset(0x0A90000000000173), 0x3E1: True}, body.entity,
                )
                body.command(body.frame + 1, 0)
                spray = next(i for i in body.component.instances.values() if i.graph.index == 0x02CF)
                state = spray.state(1)
                self.assertEqual(state.active, expected)
                owner_frames = [owner.apply(update) for update in body.frames()]
                remote = statescript.chunk(observers.full_frame(body, observers.snapshot(body)), 0, 1)
                watched = decode.read_chunk(remote.getvalue(), remote.count).instances[spray.id]
                self.assertEqual(owner.states.get((spray.id, 1), False), expected)
                self.assertEqual(watched.states.get(1, (False, {}))[0], expected)
                if expected:
                    self.assertEqual(state.payload(), {
                        "direction": (0.0, 0.0, 1.0),
                        "point": (0.0, combat.eye_height(player.hero.body), distance),
                        "normal": (0.0, 0.0, -1.0),
                    })
                    result = (True, spray_placement.wire_fields(state.payload()))
                    self.assertEqual(watched.states[1], result)
                    self.assertIn(result, [
                        frame.instances[spray.id].states.get(1) for frame in owner_frames if spray.id in frame.instances
                    ])
                    self.assertTrue(spray.state(14).active)
                    self.assertEqual(spray.state(14).pulses, 1)
                    # A failed replacement cannot carry the new slot with the old geometry.
                    player.mover.yaw = 32768
                    body.command(body.frame + 300, 0)
                    runtime.send_message(
                        body.component, 0x02400000000002A3,
                        {0x8FC: expr.Asset(0x0A90000000000173), 0x3E1: True}, body.entity,
                    )
                    body.command(body.frame + 1, 0)
                    self.assertFalse(state.active)
                    self.assertEqual(state.payload(), {})
                    self.assertEqual(spray.state(14).pulses, 1)
                else:
                    self.assertEqual(state.payload(), {})
                    self.assertFalse(spray.state(14).active)

    def test_callout_and_voice_selections_replicate_the_shared_graph(self):
        cases = (
            (0x16B, 3793, expr.Asset(0x0C00000000005A0B), 27),
            (0x1E5, 29862, expr.Map((
                (2464, expr.Asset(0x0DE0000000010AF3)),
                (1384, expr.Asset(0x0D800000000074BD)),
            )), 59),
            (0x19D, 3793, expr.Asset(0x0C00000000007114), None),
        )
        for action, variable, value, node in cases:
            with self.subTest(action=hex(action)):
                match = Match(PRACTICE_RANGE, collision_provider=StaticCollisionProvider(flat_world(1.0)))
                player = match.add_player(1, "Soldier", SOLDIER, 0, False)
                [create] = match.own_body_updates(player)
                body = player.body_script
                client = decode.ClientBody.for_hero(SOLDIER)
                create.stream.arrived(0)
                [tree] = body.frames()
                client.apply(tree)
                body.stream.arrived(tree.chunk_last)

                choice = expr.Asset(0x0A90000000000000 | action)
                runtime.send_message(
                    body.component, 0x02400000000002A3,
                    {0x8FC: choice, 0x3E1: True}, body.entity,
                )
                body.command(body.frame + 1, 0)
                frames = [client.apply(update) for update in body.frames()]

                [callout] = [number for number, index in client.graphs.items() if index == 0x0686]
                self.assertEqual(client.vars[callout, 29699], choice)
                self.assertEqual(client.vars[callout, variable], value)
                actions = [item for frame in frames for item in frame.actions]
                self.assertEqual(actions, [] if node is None else [(frames[-1].last, callout, node)])
                remote = statescript.chunk(observers.full_frame(body, observers.snapshot(body)), 0, 1)
                remote_callout = decode.read_chunk(remote.getvalue(), remote.count).instances[callout]
                self.assertEqual(remote_callout.vars[variable][0], value)
                self.assertEqual(client.states.get((callout, 13), False), action == 0x19D)
                if action == 0x19D:
                    [pulse] = [
                        frame.instances[callout].states[13] for frame in frames
                        if callout in frame.instances and 13 in frame.instances[callout].states
                    ]
                    self.assertEqual(pulse, (True, {"fresh": True, "count": 1}))
                    self.assertEqual(remote_callout.states[13], pulse)

    def test_callout_deltas_retry_with_one_stamp_and_stop_after_ack(self):
        match = Match(PRACTICE_RANGE, collision_provider=StaticCollisionProvider(flat_world(1.0)))
        player = match.add_player(1, "Soldier", SOLDIER, 0, False)
        [create] = match.own_body_updates(player)
        body = player.body_script
        owner = decode.ClientBody.for_hero(SOLDIER)
        create.stream.arrived(0)
        [tree] = body.frames()
        owner.apply(tree)
        body.stream.arrived(tree.chunk_last)
        body.command(body.frame + 1, 0)
        [initial] = body.frames()
        owner.apply(initial)
        body.stream.arrived(initial.chunk_last)

        watcher = decode.ClientBody.for_hero(SOLDIER)
        stream = BodyStream(body.entity)
        baseline = observers.snapshot(body)
        tree = stream.full(observers.full_frame(body, baseline), baseline)
        watcher.apply(tree)
        stream.arrived(tree.chunk_last)

        def observer_update():
            snap = observers.snapshot(body)
            frame, keys = observers.delta(body, stream, snap)
            return stream.delta(frame, snap, keys, 0) if frame is not None else None

        runtime.send_message(
            body.component, 0x02400000000002A3,
            {0x8FC: expr.Asset(0x0A900000000001E5), 0x3E1: True}, body.entity,
        )
        body.command(body.frame + 16, 0)
        [owner_first] = body.frames()
        viewer_first = observer_update()
        first_seen = watcher.apply(viewer_first).actions
        body.stream.lost(owner_first.chunk_last)
        stream.lost(viewer_first.chunk_last)

        body.command(body.frame + 1, 0)
        [owner_retry] = body.frames()
        viewer_retry = observer_update()
        [callout] = [instance.id for instance in body.component.instances.values()
                     if instance.graph.index == 0x0686]
        self.assertEqual(owner.apply(owner_retry).actions, [(owner_first.chunk_last, callout, 59)])
        self.assertEqual(first_seen, [(viewer_first.chunk_last, callout, 59)])
        self.assertEqual(watcher.apply(viewer_retry).actions, first_seen)
        body.stream.arrived(owner_retry.chunk_last)
        stream.arrived(viewer_retry.chunk_last)

        body.command(body.frame + 1, 0)
        [owner_next] = body.frames()
        self.assertEqual(owner.apply(owner_next).actions, [])
        viewer_next = observer_update()
        if viewer_next is not None:
            self.assertEqual(watcher.apply(viewer_next).actions, [])
        body.command(body.frame + 130, 0)
        self.assertEqual(body.callout_snapshot().callouts, ())

    def test_targeted_wheel_message_table_routes_to_the_selected_body(self):
        run = Run(names=("Speaker", "Target", "Bystander"))
        speaker, target, bystander = run.players
        run.step({})

        def wheel(player):
            return next(instance for instance in player.body_script.component.instances.values()
                        if instance.graph.index == 0x05AD)

        source, receiver, other = (wheel(player) for player in run.players)
        source.write((expr.INSTANCE, 375), (expr.Entity(target.body),))
        dispatch = source.graph.node(183)
        rows = source.graph.node(240).field("m_value")["m_configVars"]
        self.assertEqual(len(rows), 20)
        for row in rows:
            action = expr.guid_of(row["m_key"]["m_548E5344"])
            message = expr.guid_of(row["m_value"]["m_gameMessage"])
            with self.subTest(action=hex(action)):
                [entry] = runtime.message_entries(receiver, message)
                source.write((expr.INSTANCE, 29699), expr.Asset(action))
                with patch.object(receiver, "deliver", wraps=receiver.deliver) as receive:
                    source.run_node(dispatch, None)
                    receive.assert_not_called()
                    run.match.deliver_body_messages()
                    [delivery] = receive.call_args.args
                self.assertIs(delivery.node, entry)
                self.assertEqual(delivery.message, (message, {}, speaker.body))
                self.assertEqual(receiver.vars[40].value(), expr.Entity(speaker.body))
                self.assertEqual(receiver.vars[41].value(), expr.Entity(speaker.body))
                self.assertNotIn(40, other.vars)

        source.write((expr.INSTANCE, 29699), expr.Asset(0x0A900001000001E5))
        source.run_node(dispatch, None)
        self.assertEqual(run.match._body_messages, [])

    def test_directed_callouts_keep_each_speaker_until_its_action_is_acknowledged(self):
        run = Run(names=("First speaker", "Second speaker", "Recipient"))
        first, second, recipient = run.players
        run.step({})
        body = recipient.body_script
        owner = decode.ClientBody.for_hero(SOLDIER)
        watcher = decode.ClientBody.for_hero(SOLDIER)
        tree, baseline = body.tree_frame()
        update = body.stream.full(tree, baseline)
        owner.apply(update)
        body.stream.arrived(update.chunk_last)
        watch = observers.Watch(body.entity)
        observer = observers.Observers(run.match)
        with patch.object(first.client, "queue_entities") as send:
            observer._frames(first, recipient, watch, observers.snapshot(body))
        [update] = send.call_args.args[0]
        watcher.apply(update)
        watch.stream.arrived(update.chunk_last)

        def wheel(player):
            return next(instance for instance in player.body_script.component.instances.values()
                        if instance.graph.index == 0x05AD)

        for speaker, action in ((first, 0x1E5), (second, 0x1E6)):
            source = wheel(speaker)
            source.write((expr.INSTANCE, 375), (expr.Entity(recipient.body),))
            source.write((expr.INSTANCE, 29699), expr.Asset(0x0A90000000000000 | action))
            source.run_node(source.graph.node(183), None)
        run.match.deliver_body_messages()
        main = wheel(recipient)
        self.assertEqual(main.vars[40].value(), expr.Entity(second.body))
        export_body_vm(body)

        def transmit():
            body.command(body.frame + 16, 0)
            [owner_update] = body.frames()
            with patch.object(first.client, "queue_entities") as send:
                observer._frames(first, recipient, watch, observers.snapshot(body))
            [viewer_update] = send.call_args.args[0]
            return owner_update, viewer_update

        def check(updates, speaker, node, stamps):
            for client, update, stamp in zip((owner, watcher), updates, stamps, strict=True):
                self.assertEqual(client.apply(update).actions, [(stamp, main.id, node)])
                self.assertEqual(client.vars[main.id, 40], expr.Entity(speaker.body))

        first_updates = transmit()
        stamps = tuple(update.chunk_last for update in first_updates)
        check(first_updates, first, 188, stamps)
        for update in first_updates:
            update.stream.lost(update.chunk_last)

        run.match.tick += observers.LAZY_TICKS
        retry = transmit()
        check(retry, first, 188, stamps)
        for update in retry:
            update.stream.arrived(update.chunk_last)

        run.match.tick += 1
        second_updates = transmit()
        check(second_updates, second, 195, tuple(update.chunk_last for update in second_updates))
