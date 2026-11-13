"""Actual captured Pet source remains part of the stored domain graph."""
import unittest
import test_server_snapshot as snapshots
from ow174.game import replay_values,sigma_barrier
from ow174.game.content import heroes

class SigmaPetSnapshotStorageTests(unittest.TestCase):
    def test_actual_barrier_constructor_pet_source_is_stored_as_controller(self):
        fixture=snapshots.ServerSnapshotTests()
        session,peers,players=fixture.scene()
        owner=players[0]
        peers[0].message(21616, snapshots.pick(owner.entity, sigma_barrier.HERO), now=100.8)
        actor,=(a for a in owner.match.combat.sigma_barriers.values() if a.owner is owner)
        self.assertEqual(actor.pet_source,owner.entity)
        self.assertNotEqual(actor.pet_source,owner.body)
        document=fixture.capture(session)
        node,=(n for n in document['nodes'] if n['type']=='ow174.game.sigma_barrier.Barrier')
        self.assertIn('pet_source',node['state']['fields'])
        kind,number=node['state']['storage']
        self.assertEqual(kind,'ref')
        storage=document['nodes'][number]
        self.assertEqual(storage['type'],'builtins.dict')
        actual={replay_values.decode(k):v for k,v in storage['state']}
        self.assertEqual(replay_values.decode(actual['pet_source']),owner.entity)
        self.assertFalse(document['restorable'])
