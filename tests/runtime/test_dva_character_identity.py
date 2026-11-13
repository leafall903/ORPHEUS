"""The native owned0407 body has CharacterBody123 and needs its hero identity."""

import copy
import json
import unittest
from pathlib import Path

import test_dva_delivery_created_body as created
from test_game_repeated_lifecycles import entity_record

from ow174.game import world
from ow174.game.bits import BitReader, BitWriter


class DvaCharacterIdentityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        created.CreatedBodyTests.setUpClass()

    @staticmethod
    def fields(match, bot):
        update = match._bot_create_update(bot)
        record = update.build(14)
        wrapped = BitWriter()
        wrapped.bits(record.count, 14)
        wrapped.append(record)
        return entity_record(BitReader(wrapped.getvalue()), world.OP_CREATE)

    def test_owned_mech_create_populates_the_native_character_body_hero(self):
        run, pilot, _, _, mech = created.CreatedBodyTests().scenario()
        fields = self.fields(run.match, mech)
        contract = json.loads((Path(__file__).resolve().parents[2]
                               / "fixtures/behavior/dva-owned-character-identity-v1.json").read_text())
        expected = int(contract["expected"]["component_123"]["field_0"], 16)
        self.assertEqual(fields.get(123), {0: expected})
        self.assertEqual(mech.owner, pilot.entity)

    def test_ordinary_npc_and_unowned_bodies_do_not_receive_a_character_component(self):
        run, _, _, _, mech = created.CreatedBodyTests().scenario()
        ordinary = copy.copy(mech)
        ordinary.body = 0x040000000000143E
        self.assertNotIn(123, self.fields(run.match, ordinary))
        unowned = copy.copy(mech)
        unowned.owner = None
        self.assertNotIn(123, self.fields(run.match, unowned))
