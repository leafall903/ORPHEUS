"""Moira's nonpredicted orb delivery and health registration through ability input.

These checks exercise server records. Original-client presentation remains pending.
"""

import json
import unittest
from pathlib import Path

from test_game_moira import MoiraOpenAcceptanceTests
from test_game_moira_orbs import MoiraOrbTests
from test_game_projectiles import bits_of, read_shot

from ow174.game import combat, projectiles, world


class MoiraProjectileDeliveryTests(unittest.TestCase):
    def test_grasp_and_coalescence_register_enemy_health_and_owner_hit(self):
        for ultimate, button in ((False, 2), (True, 0)):
            with self.subTest(ultimate=ultimate):
                fixture = MoiraOpenAcceptanceTests()
                run, owner, target, health = fixture.setup_fight(ally=False)
                if ultimate:
                    fixture.start_ult(run, owner)
                before = health.total
                owner.client.reliable.clear()
                target.owner.client.entities.clear()
                for _ in range(20):
                    run.step({owner: (0, 0, button)})
                self.assertLess(health.total, before)
                hits = owner.client.messages(combat.HIT)
                self.assertTrue(hits)
                self.assertTrue(all(hit['+0x78']['+0x0']['+0x0'] == target.entity for hit in hits))
                self.assertTrue(any(u.entity == target.entity for u in target.owner.client.entities))

    def test_both_orbs_deliver_one_launch_per_viewer_and_preserve_health_semantics(self):
        fixture_path = Path(__file__).resolve().parents[2] / 'fixtures/behavior/moira-orb-delivery-v1.json'
        contract = json.loads(fixture_path.read_text(encoding='utf-8'))
        expected = contract['expected']
        for case in contract['input']['cases']:
            healing = case['healing']
            definition = int(case['definition'], 16)
            state = case['state']
            with self.subTest(healing=healing):
                fixture = MoiraOrbTests()
                run, owner, target, health = fixture.setup_fight(ally=healing)
                orb = fixture.launch(run, owner, healing=healing)
                shot = orb.shot
                self.assertIsNotNone(shot)
                self.assertTrue(shot.authoritative)
                self.assertEqual(shot.definition, definition)
                self.assertEqual(projectiles.definitions(owner.body_script), [])
                self.assertEqual({d.viewer for d in shot.observers}, set(run.players))
                self.assertEqual(len(run.match.combat.moira_orbs), expected['projectiles_per_cast'])
                self.assertEqual(orb.direction, tuple(expected['directions'][0]))
                before = health.total
                for _ in range(contract['input']['sample_frames']):
                    run.step({})
                change = (health.total - before) * (1 if healing else -1)
                self.assertGreater(change, 0)
                hits = owner.client.messages(combat.HIT)
                self.assertEqual(bool(hits), not healing)
                for hit in hits:
                    self.assertEqual(hit['+0x78']['+0x0']['+0x0'], target.entity)
                    self.assertGreater(hit['+0x78']['+0x4'], 0)
                for delivery in shot.observers:
                    self.assertTrue(delivery.launch_arrival.received)
                    creates = [u for u in delivery.client.entities
                               if u.entity == delivery.record.ids[0] and u.op == world.OP_CREATE]
                    self.assertEqual(len(creates), expected['owner_and_observer_create_count'])
                    self.assertGreater(creates[0].build(0).count, 0)
                    record = bits_of(delivery.launch.ch2)
                    self.assertEqual((record.bit(), record.bit(), record.var_b()), (1, 1, shot.frame))
                    decoded = read_shot(record)
                    self.assertEqual(decoded['state'], state)
                    self.assertEqual(decoded['ids'], [delivery.record.ids[0]])
                    self.assertEqual((decoded['yaw16'], decoded['pitch16']),
                                     (expected['launch_yaw16'], expected['launch_pitch16']))

                orb.energy = 0
                run.step({})
                run.step({})
                self.assertNotIn(orb, run.match.combat.moira_orbs)
                for delivery in shot.observers:
                    destroys = [u for u in delivery.client.entities
                                if u.entity == delivery.record.ids[0] and u.op == world.OP_DESTROY]
                    self.assertEqual(len(destroys), expected['owner_and_observer_destroy_count'])
                    self.assertIsNotNone(destroys[0].ch2)

    def test_lifetime_expiry_releases_both_viewers_once(self):
        fixture = MoiraOrbTests()
        run, owner, _, _ = fixture.setup_fight(ally=False)
        orb = fixture.launch(run, owner)
        for _ in range(3):
            run.step({})
        shot = orb.shot
        self.assertIsNotNone(shot)
        orb.left = combat.TICK
        for _ in range(3):
            run.step({})
        self.assertNotIn(orb, run.match.combat.moira_orbs)
        for delivery in shot.observers:
            self.assertTrue(delivery.finished)
            self.assertEqual(sum(u.entity == delivery.record.ids[0] and u.op == world.OP_DESTROY
                                 for u in delivery.client.entities), 1)

    def test_missing_create_ack_times_out_without_launch_or_impact(self):
        fixture = MoiraOrbTests()
        run, owner, _, _ = fixture.setup_fight(ally=False)
        orb = fixture.launch(run, owner)
        self.assertIsNotNone(orb.shot)
        delivery = next(d for d in orb.shot.observers if d.viewer is owner)
        delivery.creation.received = False
        self.assertFalse(delivery.launch_queued)
        self.assertFalse(delivery.advance(delivery.deadline))
        destroys = [u for u in owner.client.entities
                    if u.entity == delivery.record.ids[0] and u.op == world.OP_DESTROY]
        self.assertEqual(len(destroys), 1)
        self.assertIsNone(destroys[0].ch2)
