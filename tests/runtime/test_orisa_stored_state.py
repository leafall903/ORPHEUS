"""Orisa metadata and queued ability work remain visible to causal stored-state checks."""

import os
import unittest
from unittest.mock import patch

import test_server_snapshot as fixture

from ow174.game import combat, orisa_charge, orisa_halt
from ow174.game.content import find_hero
from ow174.game.props import PlacedDynamicSphere
from ow174.game.server_snapshot import snapshot_digest


@unittest.skipUnless(os.environ.get("ORPHEUS_ORISA_CHARGE"), "external charge metadata required")
class OrisaStoredStateTests(unittest.TestCase):
    def scene(self):
        session, _ = fixture.journal_tests.ServerJournalTests().make()
        session.create_match(fixture.MAP, [])
        return session, session.server.matches[0].combat

    def digest(self, session):
        return snapshot_digest(fixture.ServerSnapshotTests.capture(session))

    def test_charge_metadata_is_match_owned_hashed_and_not_reread_during_capture(self):
        session, fight = self.scene()
        original = fight.orisa.charge_metadata
        fight.orisa.charge_metadata = dict(original)
        with patch.object(orisa_charge, "catalog", side_effect=AssertionError("external reread")):
            before = self.digest(session)
            key = next(iter(fight.orisa.charge_metadata))
            fight.orisa.charge_metadata[key] |= {0x0D600000000003C3}
            self.assertNotEqual(self.digest(session), before)
        self.assertIsNot(fight.orisa.charge_metadata, original)

    def test_pending_barrier_and_retained_ability_impact_change_causal_digest(self):
        session, fight = self.scene()
        before = self.digest(session)
        rocket = combat.Rocket(None, 1, (0, 1, 2), (0, 0, 1), 25, 1, None, None,
                               source_graph=0xBD7, spawn=0x04000000000014A1)
        fight.orisa.barriers.pending.append((fight.tick + 7, rocket))
        queued = self.digest(session)
        self.assertNotEqual(queued, before)
        fight.ability_impacts.append(rocket)
        self.assertNotEqual(self.digest(session), queued)

    @unittest.skipUnless(os.environ.get("ORPHEUS_ORISA_HALT"), "original Halt input required")
    def test_query_definition_is_match_owned_hashed_and_not_reread_during_capture(self):
        session, fight = self.scene()
        definition = fight.orisa.halt_query_graph
        self.assertEqual(definition.index, 0xCCC)
        value = definition.node(35).fields["m_value"]
        original = value["m_value"]
        with patch.object(orisa_halt, "query_graph", side_effect=AssertionError("external query reread")):
            before = self.digest(session)
            value["m_value"] = original + 0.125  # isolated metadata storage probe, not combat input
            self.assertNotEqual(self.digest(session), before)
            value["m_value"] = original
            self.assertEqual(self.digest(session), before)

    @unittest.skipUnless(os.environ.get("ORPHEUS_ORISA_HALT"), "original Halt input required")
    def test_actual_halt_target_graph_and_hidden_wait_participate_in_server_storage(self):
        session, peers, players = fixture.ServerSnapshotTests().scene(two=True)
        caster, target = players
        match = caster.match
        now = 100.95
        peers[0].message(21616, fixture.journal_tests.pick(caster.entity, find_hero("Orisa").guid), now=now)

        def step(buttons=0):
            nonlocal now
            now += 0.016
            session.tick(now)
            for index, peer in enumerate(peers):
                peer.commands(match.tick + 1, 1, buttons if index == 0 else 0, now=now)

        for index in range(27):
            step(2 if index in (0, 25) else 0)
        orb = next(iter(match.combat.orisa.orbs.values()))
        self.assertIsNotNone(orb.query)
        placement = PlacedDynamicSphere(1, 2, 3, (0.0, 0.0, 0.0), 0.18)
        sphere = match.dynamic_spheres.register(placement, inverse_mass=1.0)
        pull = match.combat.orisa.pulls[target.body]
        instance = pull.script
        self.assertIs(instance.component, target.body_script.component)
        self.assertIs(instance.graph, match.combat.orisa.halt_graph)
        with (patch.object(orisa_halt, "control_graph", side_effect=AssertionError("external reread")),
              patch.object(orisa_halt, "query_graph", side_effect=AssertionError("external query reread"))):
            before = self.digest(session)
            sphere.velocity = (1.0, 0.0, 0.0)
            self.assertNotEqual(self.digest(session), before)
            sphere.velocity = (0.0, 0.0, 0.0)
            wait = instance.states[instance.graph.node(13).state]
            wait.ms += 1  # hidden-state probe, restored before combat continues
            self.assertNotEqual(self.digest(session), before)
            wait.ms -= 1
            self.assertEqual(self.digest(session), before)
        for _ in range(100):
            step()
            wait = instance.states.get(instance.graph.node(5).state)
            if wait is not None and wait.active:
                break
        self.assertTrue(wait.active)
        before = self.digest(session)
        wait.ms += 1
        self.assertNotEqual(self.digest(session), before)
        wait.ms -= 1
        for _ in range(45):
            step()
        self.assertNotIn(target.body, match.combat.orisa.pulls)
        self.assertTrue(instance.stopped)
        self.digest(session)

    @unittest.skipUnless(os.environ.get("ORPHEUS_ORISA_GEOMETRY")
                         and os.environ.get("ORPHEUS_ORISA_SUPERCHARGER"), "both deployable inputs required")
    def test_actual_live_barrier_and_device_graphs_participate_in_server_storage(self):
        session, peers, players = fixture.ServerSnapshotTests().scene()
        player = players[0]
        match = player.match
        peer = peers[0]
        now = 100.95
        peer.message(21616, fixture.journal_tests.pick(player.entity, find_hero("Orisa").guid), now=now)

        def step(buttons=0):
            nonlocal now
            now += 0.016
            session.tick(now)
            peer.commands(match.tick + 1, 1, buttons, now=now)

        step(16)
        for _ in range(150):
            step()
            if match.combat.orisa.barriers.active:
                break
        self.assertTrue(match.combat.orisa.barriers.active)
        shield = next(iter(match.combat.orisa.barriers.active.values()))
        before = self.digest(session)
        clock = shield.script.node_state(shield.script.graph.node(29))
        cur = clock.cur
        clock.cur -= 0.25  # hidden-state storage probe, not a lifetime acceptance input
        self.assertNotEqual(self.digest(session), before)
        clock.cur = cur
        self.assertEqual(self.digest(session), before)
        player.body_script.set_ult_charge(player.body_script.component.entity_var(338).value())
        step(32)
        for _ in range(100):
            step()
            if match.combat.orisa.superchargers.active:
                break
        self.assertTrue(match.combat.orisa.superchargers.active)
        device = next(iter(match.combat.orisa.superchargers.active.values()))
        before = self.digest(session)
        device.script.vars[199].base -= 1
        self.assertNotEqual(self.digest(session), before)
        device.script.vars[199].base += 1
        manager = match.combat.orisa.barriers

        def skip(frames):
            nonlocal now
            now += frames * 0.016
            peer.send(now=now)  # keep the real in-memory connection alive
            session.tick(now, missed=frames - 1)
            peer.commands(match.tick + 1, 1, 0, now=now)

        # Both the server simulation tick and connection time advance. Changing
        # wall time alone would neither finish this VM clock nor keep a client.
        while match.tick < shield.until:
            skip(min(250, shield.until - match.tick))
        self.assertIs(manager.retiring[shield.entity], shield)
        self.assertFalse(shield.blocking())
        before = self.digest(session)
        wait = shield.script.states[shield.script.graph.node(30).state]
        wait.ms += 1  # isolated tail storage probe, not a damage/lifetime input
        self.assertNotEqual(self.digest(session), before)
        wait.ms -= 1
        skip(40)
        self.assertFalse(manager.retiring)


if __name__ == "__main__":
    unittest.main()
