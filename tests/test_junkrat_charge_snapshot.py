"""Charge world-clock/native service storage is part of causal server state."""

import os
import unittest

import test_server_snapshot as snapshot_helpers

from ow174.game.content import find_hero
from ow174.game.server_snapshot import snapshot_digest


@unittest.skipUnless(os.environ.get('ORPHEUS_CHARGE_ENTITY_INPUTS'), 'charge inputs not configured')
class JunkratChargeSnapshotTests(unittest.TestCase):
    def test_world_deadline_tags_context_and_owner_lifetime_are_causal(self):
        helper = snapshot_helpers.ServerSnapshotTests()
        session, _peers, players = helper.scene()
        player, = players
        session.apply('switch_hero', {'account': 1, 'hero': find_hero('Junkrat').guid})
        system = player.match.combat.junkrat.charge
        binding = system.bindings[player.body]
        first = snapshot_digest(helper.capture(session))
        self.assertIsNotNone(binding.next_ms)
        deadline = binding.next_ms
        binding.next_ms += 16
        self.assertNotEqual(first, snapshot_digest(helper.capture(session)))
        binding.next_ms = deadline
        self.assertEqual(first, snapshot_digest(helper.capture(session)))
        system.set_tags(player.body, added=(0x3C3,))
        second = snapshot_digest(helper.capture(session))
        self.assertNotEqual(first, second)
        binding.filtered.retired = True
        self.assertNotEqual(second, snapshot_digest(helper.capture(session)))
        binding.filtered.retired = False
        before_reserve = snapshot_digest(helper.capture(session))
        system.reserves[player.entity] = (player.hero.guid, 120.0)
        self.assertNotEqual(before_reserve, snapshot_digest(helper.capture(session)))
        system.reserves.clear()
        old_body = player.body
        self.assertTrue(session.apply('switch_hero', {
            'account': 1, 'hero': find_hero('Soldier: 76').guid}))
        self.assertNotIn(old_body, system.bindings)
        self.assertTrue(binding.filtered.retired)
        self.assertNotIn(old_body, system.tag_overrides)
        helper.capture(session)  # retired callbacks/targets do not survive in the active graph


if __name__ == '__main__':
    unittest.main()
