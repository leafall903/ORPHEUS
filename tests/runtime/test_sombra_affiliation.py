"""Actual Translocator cast keeps source affiliation on spawn and late join.

Native relationship palette/rendering remains unaccepted.
"""

import json
import unittest
from pathlib import Path

from test_game_combat import Run

from ow174.game import world
from ow174.game.content import PRACTICE_RANGE, heroes
from ow174.game.link import KIND_FRAME, Packet
from ow174.game.script.entity_team import index_from_filters
from tests.runtime.test_game_repeated_lifecycles import wire_frame


class SombraAffiliationTests(unittest.TestCase):
    def test_actual_beacon_preserves_source_team_and_pet_for_each_viewer_and_late_join(self):
        path = Path(__file__).resolve().parents[2] / "fixtures/behavior/sombra-affiliation-v1.json"
        case = json.loads(path.read_text(encoding="utf-8"))
        run = Run(PRACTICE_RANGE, ("Sombra", "Ally", "Enemy"))
        owner, ally, enemy = run.players
        owner.team = ally.team = case["input"]["source_team"]
        enemy.team = 1 - owner.team
        run.match.switch_hero(owner, heroes()[0x02E000000000012E])
        for _ in range(50):
            run.step({})
        for offset in range(20):
            run.step({owner: (0, 0, case["input"]["throw_button"] if offset == 0 else 0)})
        actor, = run.match.combat.translocators.entries.values()

        def decoded_fields(update):
            packet = Packet(1, KIND_FRAME, payload=world.frame(run.match.tick, 0, entities=[update]))
            _, records = wire_frame(packet)
            self.assertEqual(len(records), 1)
            return records[0][1]

        for viewer in (owner, ally, enemy):
            with self.subTest(viewer=viewer.name):
                update, = [u for u in viewer.client.entities
                           if u.entity == actor.entity and u.op == world.OP_CREATE]
                fields = decoded_fields(update)
                self.assertEqual(fields[26][0], owner.team_bit)
                self.assertEqual(fields[72][0], owner.body)
                self.assertEqual(index_from_filters(fields[26][0]), owner.team)
        self.assertEqual(actor.filter_bits, owner.team_bit)
        self.assertEqual(actor.pet_owner, actor.source_body)
        self.assertEqual(actor.source_body, owner.body)
        late, = [u for u in run.match.spawn_updates(enemy)
                 if u.entity == actor.entity and u.op == world.OP_CREATE]
        fields = decoded_fields(late)
        self.assertEqual(fields[26][0], owner.team_bit)
        self.assertEqual(fields[72][0], actor.source_body)


if __name__ == "__main__":
    unittest.main()
