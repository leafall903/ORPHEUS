"""The native wheel graphs enter the ordinary player body stream."""

import unittest
from unittest.mock import patch

from ow174.game.collision import StaticCollisionProvider, flat_world
from ow174.game.content import PRACTICE_RANGE, SOLDIER
from ow174.game.match import Match
from ow174.game.script import decode, expr, runtime
from ow174.game.script.driver import BodyScript, BodyWorld


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
        match = Match(PRACTICE_RANGE, collision_provider=StaticCollisionProvider(flat_world(1.0)))
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
        original_native = BodyWorld.native

        def equipped_animation(world, config, instance):
            if config.get("$") == "STU_250DEABD" and instance.evaluate(config["m_slot"]) == 1:
                return expr.Asset(0x028000000000006E)
            return original_native(world, config, instance)

        with patch.object(BodyWorld, "native", equipped_animation):
            runtime.send_message(
                body.component, 0x02400000000002A3,
                {0x8FC: expr.Asset(0x0A900000000001A1), 0x3E1: True}, body.entity,
            )
            body.command(body.frame + 1, 0)
        replicate()

        instances = {instance.graph.index: instance for instance in body.component.instances.values()}
        emote, animation, helper = (instances[index] for index in (0x069A, 0x023A, 0x1040))
        self.assertIs(animation.parent, emote)
        self.assertEqual(client.graphs[emote.id], 0x069A)
        self.assertEqual(client.records[animation.id], (0x023A, (emote.id, animation.parent_state)))
        self.assertEqual(client.graphs[helper.id], 0x1040)
