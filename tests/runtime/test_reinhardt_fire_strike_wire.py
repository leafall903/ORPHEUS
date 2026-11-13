"""Server delivery regression; these tests do not establish native visual acceptance."""

import json
import unittest
from pathlib import Path
from unittest.mock import patch

import test_delivered_reinhardt as delivered
from ow174.game import combat, world


class FireStrikeWireTests(unittest.TestCase):
    def setUp(self):
        self.fixture = json.loads((Path(__file__).resolve().parents[2]
                                  / "fixtures/behavior/reinhardt-fire-strike-wire-v1.json").read_text())
        self.case = delivered.FireStrikeTests()
        self.case.setUp()

    def cast(self):
        for commands in self.fixture["input"]["commands"]:
            self.case.advance(**commands)
        rocket, = self.case.match.combat.rockets
        self.assertIsNotNone(rocket.shot)
        self.assertEqual(rocket.definition, int(self.fixture["expected"]["definition"], 16))
        self.assertEqual(rocket.shot.authoritative, self.fixture["expected"]["authoritative"])
        self.assertEqual(list(rocket.direction), self.fixture["expected"]["direction"])
        return rocket

    def test_owner_and_observer_get_single_launch_damage_feedback_and_retirement(self):
        case = self.case
        rocket = self.cast()
        deliveries = {d.viewer: d for d in rocket.shot.observers}
        self.assertIn(case.rein, deliveries)
        self.assertIn(case.enemy, deliveries)
        for viewer, delivery in deliveries.items():
            self.assertEqual(len(delivery.record.ids), self.fixture["expected"]["projectiles_per_viewer"])
            self.assertEqual(sum(u.op == world.OP_CREATE and u.entity == delivery.record.ids[0]
                                 for u in viewer.client.entities), 1)
        case.advance(330, buttons=0)  # four-second authored flight plus delivery grace
        self.assertEqual(case.health(case.enemy), self.fixture["expected"]["enemy_health"])
        self.assertIn(self.fixture["expected"]["owner_hit_damage"],
                      [h["+0x78"]["+0x4"] for h in case.rein.client.messages(combat.HIT)])
        self.assertNotIn(rocket, case.match.combat.rockets)
        for viewer, delivery in deliveries.items():
            self.assertTrue(delivery.launch_arrival.received)
            self.assertTrue(delivery.finished)
            self.assertNotIn(delivery, case.match.combat.projectiles.observed)
            self.assertTrue(any(u.op == world.OP_DESTROY and u.entity == delivery.record.ids[0]
                                for u in viewer.client.entities))

    def test_unacknowledged_owner_create_never_launches_and_times_out(self):
        case = self.case
        client = case.rein.client
        # Keep the established body baseline; delay the newly created projectile's ACK.
        with patch.object(client, "queue_entities", lambda updates: client.entities.extend(updates)):
            rocket = self.cast()
            owner = next(d for d in rocket.shot.observers if d.viewer is case.rein)
            self.assertFalse(owner.creation.received)
            self.assertFalse(owner.launch_queued)
            case.advance(400, buttons=0)
            self.assertFalse(owner.launch_queued)
            self.assertNotIn(owner, case.match.combat.projectiles.observed)
            self.assertEqual(case.health(case.enemy), self.fixture["expected"]["enemy_health"])
