"""D.Va's current/view body must be the mech, while primary stays the pilot."""

import unittest
from unittest.mock import patch

from ow174.game import body_entities, world
from ow174.game.content import find_hero
from test_dva_delivery_created_body import CreatedBodyTests
from test_game_combat import KANEZAKA_DM, Run


class DvaPossessionHandoffTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        CreatedBodyTests.setUpClass()

    def test_pick_publishes_created_mech_as_current_and_view_target(self):
        run = Run(KANEZAKA_DM, ("Pilot", "Other"))
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        pilot = run.players[0]
        with patch.object(pilot.client, "queue_possession", wraps=pilot.client.queue_possession) as queue:
            self.assertTrue(run.match.switch_hero(pilot, find_hero("dva")))
        mech = pilot.controlled_mech
        self.assertIsNotNone(mech)
        update, dependency = queue.call_args.args
        self.assertEqual(dependency, mech.entity)
        with patch.object(world, "update", wraps=world.update) as encode:
            update.build(0)
        self.assertEqual(encode.call_args.args[1][44], [mech.entity, mech.entity, pilot.body])
        self.assertTrue(any(row.entity == mech.entity and row.op == world.OP_CREATE
                            for row in pilot.client.entities))

    def test_ready_control_release_and_rebind_publish_both_camera_handoffs(self):
        run, pilot, _, _, mech = CreatedBodyTests().scenario()
        self.assertTrue(pilot.body_script.create_arrived)
        with patch.object(pilot.client, "queue_possession", wraps=pilot.client.queue_possession) as queue:
            body_entities.release_control(pilot)
            update, dependency = queue.call_args.args
            self.assertEqual(dependency, pilot.body)
            with patch.object(world, "update", wraps=world.update) as encode:
                update.build(0)
            self.assertEqual(encode.call_args.args[1][44], [pilot.body] * 3)
            body_entities.begin_initial_control(pilot)
            update, dependency = queue.call_args.args
            self.assertEqual(dependency, mech.entity)
            with patch.object(world, "update", wraps=world.update) as encode:
                update.build(0)
            self.assertEqual(encode.call_args.args[1][44], [mech.entity, mech.entity, pilot.body])
