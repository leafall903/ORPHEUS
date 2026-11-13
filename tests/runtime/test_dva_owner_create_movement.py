"""The local owned mech needs a movement baseline before native44 can possess it."""

import copy
import json
import unittest
from pathlib import Path
from unittest.mock import patch

import test_dva_delivery_created_body as created
import test_game_repeated_lifecycles as wire

from ow174.game import world
from ow174.game.bits import BitReader, BitWriter


class DvaOwnerCreateMovementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        created.CreatedBodyTests.setUpClass()
        cls.contract = json.loads((Path(__file__).resolve().parents[2]
                                  / "fixtures/behavior/dva-owner-create-movement-v1.json").read_text())

    def movement(self, update):
        record = update.build(14)
        wrapped = BitWriter()
        wrapped.bits(record.count, 14)
        wrapped.append(record)
        states = []
        original = wire.read_movement

        def read(reader, frame):
            state = original(reader, frame)
            states.append(state)
            return state

        with patch.object(wire, "read_movement", side_effect=read):
            wire.entity_record(BitReader(wrapped.getvalue()), world.OP_CREATE)
        return states

    def scene(self):
        run, pilot, other, _, mech = created.CreatedBodyTests().scenario()
        mech.position = tuple(self.contract["input"]["position"])
        mech.yaw = self.contract["input"]["yaw"]
        mech.control_mover.position[:] = mech.position
        mech.control_mover.yaw = mech.yaw
        return run.match, pilot, other, mech

    def test_production_owner_spawn_contains_frozen_mech_movement_before_handoff(self):
        match, pilot, _, mech = self.scene()
        create = next(u for u in match.spawn_updates(pilot) if u.entity == mech.entity)
        before = create.build(14).getvalue()
        states = self.movement(create)
        self.assertEqual(len(states), 1)
        state = states[0]
        expected = self.contract["expected"]
        self.assertEqual(state["frame"], expected["owner_create_state_frame"])
        self.assertEqual(state["position"], tuple(expected["owner_create_position"]))
        self.assertEqual(state["yaw"], expected["owner_create_yaw"])
        self.assertEqual(state["spring"], tuple(expected["neutral_spring_steps"]))
        mech.position = (100., 200., 300.)
        mech.velocity = (10., 20., 30.)
        self.assertEqual(create.build(14).getvalue(), before)

    def test_publication_before_control_mover_exists_still_seeds_owner_only(self):
        match, pilot, other, mech = self.scene()
        mech.control_mover = None  # actual026A factory publishes before its Possess node
        pilot.spawned = other.spawned = True
        match.publish_bot(mech)
        for viewer, expected_count in ((pilot, 1), (other, 0)):
            create = next(u for u in reversed(viewer.client.entities)
                          if u.entity == mech.entity and u.op == world.OP_CREATE)
            self.assertEqual(len(self.movement(create)), expected_count)

    def test_observers_and_ordinary_npc_keep_transform_only_creates(self):
        match, pilot, other, mech = self.scene()
        create = next(u for u in match.spawn_updates(other) if u.entity == mech.entity)
        self.assertEqual(self.movement(create), [])
        ordinary = copy.copy(mech)
        ordinary.body = 0x040000000000143E
        create = match._bot_create_update(ordinary, pilot)
        self.assertEqual(self.movement(create), [])
        unowned = copy.copy(mech)
        unowned.owner = None
        self.assertEqual(self.movement(match._bot_create_update(unowned, pilot)), [])
        for field, value in (("dead", True), ("detached_bomb", True),
                             ("source_body", pilot.body + 100)):
            old = copy.copy(mech)
            setattr(old, field, value)
            self.assertEqual(self.movement(match._bot_create_update(old, pilot)), [])
