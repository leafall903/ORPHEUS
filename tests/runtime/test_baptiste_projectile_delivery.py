"""Baptiste command-path projectile delivery. Original-client visibility is pending."""

import unittest
from unittest.mock import patch

from ow174.game import combat, projectiles, world
from ow174.game.commands import Command
from tests.runtime import test_baptiste_combat as fixture


class BaptisteProjectileDeliveryTests(unittest.TestCase):
    def arena(self, *, friendly_target=False):
        run, owner, ally, enemy, fight, targets, _ = fixture.BaptisteCombatTests().setup_fight()
        for player in run.players:
            player.mover.body = None
            if player is not owner:
                player.mover.position[0] = 20
        if friendly_target:
            ally.mover.position[:] = (0, 1, 4)
        for _ in range(30):
            run.step({})
        return run, owner, ally, enemy, fight, targets

    def batched_throw(self, button, batch, pitch, *, friendly_target=False):
        run, owner, ally, _, fight, targets = self.arena(friendly_target=friendly_target)
        if friendly_target:
            fight.vitals_of(targets[ally]).damage(100)
        first = run.frame + 1
        launched = first + 10
        commands = []
        impacts = []
        landed = fight._landed

        def observe(rocket, **kwargs):
            impacts.append((fight.tick, rocket.source_graph))
            return landed(rocket, **kwargs)

        with patch.object(fight, '_landed', side_effect=observe):
            for frame in range(first, first + 66):
                run.match.update(frame * combat.TICK, frame)
                commands.append(Command(frame, yaw=0, pitch=pitch,
                                        buttons=button if frame == launched else 0))
                if len(commands) == batch or frame == first + 65:
                    owner.take_commands(commands)
                    commands = []
        flights = [(rocket.started_tick, rocket.flown, rocket.left, rocket.position)
                   for rocket in fight.rockets]
        fields = [(ward.actor.created_tick, ward.active_tick, ward.actor.expires_tick, ward.actor.position)
                  for ward in fight.baptiste.fields.values()]
        return launched, flights, fields, impacts, fight.vitals_of(targets[ally]).total

    def test_batched_throw_preserves_command_launch_and_flight(self):
        for button in (2, 16):
            reference = self.batched_throw(button, 1, -8192)
            launched, flights, fields, impacts, _ = reference
            self.assertEqual(fields, [])
            self.assertEqual(impacts, [])
            self.assertEqual(len(flights), 1)
            self.assertEqual(flights[0][0], launched)
            self.assertAlmostEqual(flights[0][1], 55 * combat.TICK)
            for batch in (8, 32):
                with self.subTest(button=button, batch=batch):
                    actual = self.batched_throw(button, batch, -8192)
                    self.assertEqual(actual[0], launched)
                    self.assertEqual(actual[2:], reference[2:])
                    self.assertEqual(actual[1][0][0], launched)
                    for observed, expected in zip(actual[1][0][1:3], flights[0][1:3], strict=True):
                        self.assertAlmostEqual(observed, expected)
                    for observed, expected in zip(actual[1][0][3], flights[0][3], strict=True):
                        self.assertAlmostEqual(observed, expected)

    def test_batched_throw_preserves_ground_impact_and_field_deadlines(self):
        for button in (2, 16):
            reference = self.batched_throw(button, 1, 8192)
            self.assertEqual(reference[1], [])
            self.assertEqual(len(reference[3]), 1)
            if button == 16:
                self.assertEqual(len(reference[2]), 1)
                self.assertEqual(reference[2][0][:2], (reference[0] + 2, reference[0] + 24))
            for batch in (8, 32):
                with self.subTest(button=button, batch=batch):
                    self.assertEqual(self.batched_throw(button, batch, 8192), reference)

    def test_batched_healing_contact_keeps_impact_time_and_amount(self):
        reference = self.batched_throw(2, 1, 0, friendly_target=True)
        self.assertEqual(reference[1], [])
        self.assertEqual(len(reference[3]), 1)
        self.assertEqual(reference[4], 170)
        for batch in (8, 32):
            with self.subTest(batch=batch):
                self.assertEqual(self.batched_throw(2, batch, 0, friendly_target=True), reference)

    def test_primary_command_registers_three_enemy_hits_and_owner_feedback(self):
        run, owner, ally, enemy, fight, targets, _ = fixture.BaptisteCombatTests().setup_fight()
        ally.mover.position[0] = 20
        for player in run.players:
            player.mover.body = None
        for _ in range(30):
            run.step({})
        run.step({owner: (0, 0, 1)})
        for _ in range(26):
            run.step({})
        self.assertEqual(fight.vitals_of(targets[enemy]).total, 128)
        self.assertEqual(fight.vitals_of(targets[ally]).total, 200)
        hits = owner.client.messages(combat.HIT)
        self.assertEqual(len(hits), 3)
        self.assertEqual([hit['+0x78']['+0x4'] for hit in hits], [24, 24, 24])
        self.assertEqual([hit['+0x78']['+0x0']['+0x0'] for hit in hits], [enemy.body] * 3)
        frames = [hit['+0x84'] for hit in hits]
        self.assertEqual([frame - frames[0] for frame in frames], [0, 6, 12])

    def test_nonpredicted_field_canister_reaches_owner_and_both_observers(self):
        run, owner, ally, enemy, fight, _ = self.arena()
        run.step({owner: (0, 8192, 16)})
        rocket, = fight.rockets
        shot = rocket.shot
        self.assertIsNotNone(shot)
        self.assertTrue(shot.authoritative)
        self.assertIsNone(fight.projectiles.lease_id(owner, shot.definition, run.frame))
        self.assertEqual({delivery.viewer for delivery in shot.observers}, {owner, ally, enemy})
        for _ in range(130):
            run.step({})
        self.assertNotIn(rocket, fight.rockets)
        self.assertEqual(len(fight.baptiste.fields), 1)
        for delivery in shot.observers:
            self.assertTrue(delivery.creation.received)
            self.assertTrue(delivery.launch_arrival.received)
            self.assertTrue(delivery.finished)
            entity = delivery.record.ids[0]
            records = [update for update in delivery.client.entities if update.entity == entity]
            self.assertEqual([update.op for update in records], [world.OP_CREATE, world.OP_DESTROY])
            self.assertIsNotNone(records[-1].ch2)
        self.assertFalse(fight.projectiles.observed)

    def test_healing_grenade_preserves_prediction_and_friendly_health_operation(self):
        run, owner, ally, enemy, fight, targets = self.arena(friendly_target=True)
        fight.vitals_of(targets[ally]).damage(100)
        run.step({owner: (0, 0, 2)})
        rocket, = fight.rockets
        shot = rocket.shot
        self.assertIsNotNone(shot)
        self.assertFalse(shot.authoritative)
        self.assertEqual({delivery.viewer for delivery in shot.observers}, {ally, enemy})
        self.assertIn(shot.definition, projectiles.definitions(owner.body_script))
        for _ in range(110):
            run.step({})
        self.assertEqual(fight.vitals_of(targets[ally]).total, 170)
        self.assertEqual(fight.vitals_of(targets[enemy]).total, 200)
        self.assertNotIn(rocket, fight.rockets)
        self.assertNotIn(shot, fight.projectiles.shots)
        self.assertTrue(shot.created)
        for delivery in shot.observers:
            self.assertTrue(delivery.creation.received)
            self.assertTrue(delivery.launch_arrival.received)
            self.assertTrue(delivery.finished)
        self.assertFalse(fight.projectiles.observed)
