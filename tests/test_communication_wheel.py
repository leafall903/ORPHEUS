"""The native wheel graphs enter the ordinary player body stream."""

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from test_game_combat import Run

from ow174.accounts.profile import Profile
from ow174.catalog.items import ItemDB
from ow174.catalog.templates import RetailTemplates
from ow174.content.collection import Collection
from ow174.game import observers, statescript
from ow174.game.body_vm_snapshot import export_body_vm
from ow174.game.collision import StaticCollisionProvider, flat_world
from ow174.game.content import PRACTICE_RANGE, SOLDIER
from ow174.game.match import Match
from ow174.game.script import decode, expr, graph, runtime
from ow174.game.script.driver import BodyScript, BodyStream
from ow174.paths import EMOTE_ALIAS_DATA


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
            (0x173, (0x02CF, 33)),   # default voice slot
            (0x1A5, (0x02CF, 33)),   # alternate voice slot
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

    def test_emote_and_voice_actions_use_native_slot_order(self):
        body = BodyScript(SOLDIER, 0xA0000101, 1000, communication_wheel=True)
        instances = {instance.graph.index: instance for instance in body.component.instances.values()}
        main, voice = instances[0x05AD], instances[0x02CF]
        emote_slot = main.graph.node(107).field("m_condition")["m_configVars"][0]["m_slot"]
        voice_slot = voice.graph.node(33).field("m_value")
        for instance, config, actions, unmatched in (
            (main, emote_slot, (0x172, 0x1A1, 0x1A0, 0x19F), 0x1A2),
            (voice, voice_slot, (0x173, 0x1A5, 0x1A4, 0x1A3), 0x1A6),
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
        self.assertEqual(voice.vars[9259].value(), 2)

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

    def test_spray_lookup_uses_the_authored_equipped_slot_config(self):
        body = BodyScript(SOLDIER, 0xA0000101, 1000, communication_wheel=True)
        spray = body.component.create(graph.graph(0x0686), init=False)
        config = spray.graph.node(67).field("m_value")["m_slot"]
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
                spray.write((expr.INSTANCE, 29699), action)
                self.assertEqual(spray.evaluate(config), expected)

    def test_voice_unlock_query_uses_the_equipped_slot(self):
        default_voice, chosen_voice = 0x02500000000008D7, 0x0250000000000A31
        collection = Collection(RetailTemplates(), ItemDB())
        profile = Profile()
        self.assertEqual(collection.voice_unlocks(profile, SOLDIER), (default_voice,))
        profile.loadouts[f"0x{SOLDIER:016X}"] = {"+0x70": [hex(default_voice), hex(chosen_voice), "0x0"]}
        match = Match(
            PRACTICE_RANGE, collision_provider=StaticCollisionProvider(flat_world(1.0)),
            voice_unlocks_of=lambda _, hero: collection.voice_unlocks(profile, hero),
        )
        player = match.add_player(1, "Soldier", SOLDIER, 0, False)
        [create] = match.own_body_updates(player)
        create.stream.arrived(0)
        body = player.body_script
        runtime.send_message(
            body.component, 0x02400000000002A3,
            {0x8FC: expr.Asset(0x0A900000000001A5), 0x3E1: True}, body.entity,
        )
        body.command(body.frame + 1, 0)
        voice = next(instance for instance in body.component.instances.values() if instance.graph.index == 0x02CF)
        self.assertEqual(voice.vars[9259].value(), 1)
        query = voice.graph.node(50).field("m_value")
        self.assertEqual(voice.evaluate(query), expr.Asset(chosen_voice))

        for slot, expected in ((0, default_voice), (2, 0), (3, 0), (-1, 0)):
            voice.write((expr.INSTANCE, 9259), slot)
            self.assertEqual(voice.evaluate(query), expr.Asset(expected))

    def test_callout_and_spray_selections_replicate_the_shared_graph(self):
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
