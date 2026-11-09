"""The native wheel graphs enter the ordinary player body stream."""

import unittest
from unittest.mock import patch

from ow174.game.collision import StaticCollisionProvider, flat_world
from ow174.game.content import PRACTICE_RANGE, SOLDIER
from ow174.game.match import Match
from ow174.game.script import decode, expr, runtime
from ow174.game.script.driver import BodyScript


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
