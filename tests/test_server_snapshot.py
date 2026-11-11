"""Stored domain hashes detect hidden causal state without executing the game."""

import copy
import dataclasses
import sys
import tempfile
import types
import unittest
import weakref
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import test_server_journal as journal_tests
from test_server_journal import MAP, FixedSource, Peer, acknowledge_ownership, pick

from ow174.game import mover as mover_module
from ow174.game.collision import StaticCollisionProvider, flat_world
from ow174.game.content import SOLDIER
from ow174.game.link import SYN
from ow174.game.match import Match
from ow174.game.mover import Mover
from ow174.game.script import runtime
from ow174.game.script.driver import BodyScript, Snapshot
from ow174.game.server_journal import RecordingServerSession, ServerJournalError, _ReplaySession
from ow174.game.server_snapshot import ServerSnapshotError, export_server_state, snapshot_digest
from ow174.game.world import OP_UPDATE, EntityUpdate, Movement


class ServerSnapshotTests(unittest.TestCase):
    def test_baked_tracer_ramp_and_reported_angle_are_registered_and_replay(self):
        case = journal_tests.ServerJournalTests()
        session, sent = case.make(verify_stored_state=True)
        peers, players = case.connected(session, sent)
        player = players[0]
        peers[0].message(21616, pick(player.entity, 0x02E0000000000003), now=100.8)
        peers[0].commands(player.body_script.frame+1, 1, 1)
        instance = next(i for i in player.body_script.component.instances.values() if i.graph.index == 0x16)
        volley = instance.state(8)
        self.assertEqual(volley.spread_ramp, 3)
        self.assertEqual(list(volley.shot_spread.values()), [0])
        baseline = snapshot_digest(self.capture(session))
        ramp = volley.spread_ramp
        volley.spread_ramp = 6.0
        self.assertNotEqual(snapshot_digest(self.capture(session)), baseline)
        volley.spread_ramp = ramp
        self.assertEqual(snapshot_digest(self.capture(session)), baseline)
        document = session.finish()
        replay = _ReplaySession(document, session.registry.resolve)
        result = replay.run()
        self.assertTrue(result['stored_state_verified'])
        self.assertFalse(result['complete_battle_replay'])

    def test_actual_winston_cast_actor_health_expiry_and_guard_are_causal_storage(self):
        session, peers, players = self.scene()
        player = players[0]
        peers[0].message(21616, pick(player.entity, 0x02E0000000000009), now=100.8)
        peers[0].commands(player.body_script.frame+1, 5, 16)
        manager = player.match.combat.winston_barriers
        bubble = manager.actors[player.body]
        baseline = snapshot_digest(self.capture(session))
        for field in ('health', 'expires'):
            before = getattr(bubble, field)
            setattr(bubble, field, before-1)
            self.assertNotEqual(snapshot_digest(self.capture(session)), baseline)
            setattr(bubble, field, before)
        token = manager.seen[player.body]
        manager.seen[player.body] = (token[0], token[1]+1)
        self.assertNotEqual(snapshot_digest(self.capture(session)), baseline)
        manager.seen[player.body] = token
        with (mock.patch.object(type(manager), 'snapshot', side_effect=AssertionError('no query')),
              mock.patch.object(type(manager), 'update', side_effect=AssertionError('no simulation'))):
            self.assertEqual(snapshot_digest(self.capture(session)), baseline)

    def test_actual_winston_deployment_and_world_expiry_replay_with_registered_actor(self):
        case = journal_tests.ServerJournalTests()
        session, sent = case.make(verify_stored_state=True)
        peers, players = case.connected(session, sent)
        player = players[0]
        peers[0].message(21616, pick(player.entity, 0x02E0000000000009), now=100.8)
        peers[0].commands(player.body_script.frame+1, 5, 16)
        self.assertIn(player.body, player.match.combat.winston_barriers.actors)
        session.tick(110.0)
        self.assertNotIn(player.body, player.match.combat.winston_barriers.actors)
        document = session.finish()
        replay = _ReplaySession(document, session.registry.resolve)
        result = replay.run()
        self.assertTrue(result['stored_state_verified'])
        self.assertFalse(result['complete_battle_replay'])

    def test_manual_assassin_controller_pin_and_pending_route_are_causal_storage(self):
        import test_game_tracer_npcs as npc_tests

        from ow174.game import npc, npc_navigation

        fixture = npc_tests.TracerNpcTests
        self.addCleanup(fixture.doClassCleanups)
        fixture.setUpClass()
        definition = next(d for d in fixture.definitions.values() if d.name == "Assassin")
        session, _, players = self.scene()
        player = players[0]
        match = player.match
        bot = npc.spawn(match, definition, (100, 0, 100), team=1)
        self.assertIsNotNone(bot.body_script)
        world = bot.body_script.component.world
        self.assertIsNotNone(world.controller_entity)
        self.assertNotEqual(world.controller_entity, bot.entity)
        baseline = snapshot_digest(self.capture(session))
        world.manual_target = player.body
        self.assertNotEqual(snapshot_digest(self.capture(session)), baseline)
        world.manual_target = None
        self.assertEqual(snapshot_digest(self.capture(session)), baseline)
        match.combat.pins[bot.entity] = player.body
        self.assertNotEqual(snapshot_digest(self.capture(session)), baseline)
        match.combat.pins.clear()
        self.assertEqual(snapshot_digest(self.capture(session)), baseline)
        # Storage coverage only; combat/navigation execution has separate fixtures.
        bot.navigation = npc_navigation.Navigator(Mover(bot.position, world=match.collision_world,
                                                       data=mover_module.mover_data(bot.body)))
        target = next(t for t in match.combat._targets() if t.owner is player)
        bot.navigation.search = npc_navigation.begin(bot.position, target, 2.5)
        searching = snapshot_digest(self.capture(session))
        self.assertNotEqual(searching, baseline)
        bot.navigation.search.visited.add((0, 0, 0))
        self.assertNotEqual(snapshot_digest(self.capture(session)), searching)
        bot.navigation.search.visited.clear()
        self.assertEqual(snapshot_digest(self.capture(session)), searching)
        bot.navigation = None
        self.assertEqual(snapshot_digest(self.capture(session)), baseline)
        bot.impulse_mover = Mover(bot.position, world=match.collision_world,
                                  data=mover_module.mover_data(bot.body))
        before_impulse = snapshot_digest(self.capture(session))
        bot.impulse_mover.add_external_impulse((0, 7, 15))
        self.assertNotEqual(snapshot_digest(self.capture(session)), before_impulse)

    def test_revive_control_and_damage_deadlines_each_change_server_digest(self):
        session, _, players = self.scene()
        player = players[0]
        fight = player.match.combat
        target = next(target for target in fight._targets() if target.owner is player)
        vitals = fight.vitals_of(target)
        before = snapshot_digest(self.capture(session))
        for field in ("rising_until", "revive_protection_until"):
            setattr(vitals, field, fight.tick + 60)
            self.assertNotEqual(snapshot_digest(self.capture(session)), before)
            setattr(vitals, field, 0)
        self.assertEqual(snapshot_digest(self.capture(session)), before)

    def test_active_pulse_bomb_fuse_is_registered_and_changes_the_server_digest(self):
        from ow174.game import combat
        session, peers, players = self.scene(two=True)
        player = players[0]
        match = player.match
        peers[0].message(21616, pick(player.entity, 0x02E0000000000003), now=101.0)
        self.assertEqual(player.hero.guid, 0x02E0000000000003)
        script = player.body_script
        instance = next(i for i in script.component.instances.values() if i.graph.index == 0x34)
        state = instance.state(0)
        bomb = match.combat.pulse_bombs.launch(
            player, state, match.combat._eye(player), (0, 0, 1), 15, 5,
            combat.hit_def(state, state.node.field("m_6396149F")),
            combat.hit_def(state, state.node.field("m_D2D11CE9")), None)
        bomb.stuck = True
        bomb.fuse_left = 0.5
        baseline = snapshot_digest(self.capture(session))
        bomb.fuse_left = 0.4
        self.assertNotEqual(snapshot_digest(self.capture(session)), baseline)

    def test_active_halt_orb_is_registered_in_server_digest(self):
        import os

        from ow174.game import orisa_barrier

        if not os.environ.get("ORPHEUS_ORISA_HALT"):
            self.skipTest("verified 104319 Halt supplement required")
        session, peers, players = self.scene()
        player = players[0]
        peers[0].message(21616, pick(player.entity, orisa_barrier.HERO), now=101.0)
        peers[0].commands(player.body_script.frame + 1, 16, 2, now=101.1)
        manager = player.match.combat.halt
        orb, = manager.orbs.values()
        before = snapshot_digest(self.capture(session))
        self.assertIsNone(orb.requested)
        orb.requested = player.match.tick
        self.assertNotEqual(snapshot_digest(self.capture(session)), before)
        orb.requested = None
        self.assertEqual(snapshot_digest(self.capture(session)), before)

    def test_active_orisa_barrier_graph_is_registered_in_server_digest(self):
        import os

        from ow174.game import orisa_barrier

        if not os.environ.get("ORPHEUS_ORISA_GEOMETRY"):
            self.skipTest("verified external Orisa mesh required")
        session, peers, players = self.scene()
        player = players[0]
        peers[0].message(21616, pick(player.entity, orisa_barrier.HERO), now=101.0)
        peers[0].commands(player.body_script.frame + 1, 16, 16, now=101.1)
        manager = player.match.combat.orisa_barriers
        for frame in range(120):
            session.tick(101.12 + frame * 0.016)
            if manager.active:
                break
        barrier, = manager.active.values()
        health = barrier.component.entity_var(253)
        before = snapshot_digest(self.capture(session))
        amount = health.value()
        barrier.component.set(253, amount - 1)
        self.assertNotEqual(snapshot_digest(self.capture(session)), before)
        barrier.component.set(253, amount)
        self.assertEqual(snapshot_digest(self.capture(session)), before)

    def test_active_supercharger_graph_is_registered_in_server_digest(self):
        import os

        from ow174.game import orisa_barrier

        if not os.environ.get("ORPHEUS_ORISA_SUPERCHARGER"):
            self.skipTest("verified 104319 Supercharger supplement required")
        session, peers, players = self.scene()
        player = players[0]
        peers[0].message(21616, pick(player.entity, orisa_barrier.HERO), now=101.0)
        script = player.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        peers[0].commands(script.frame + 1, 40, 32, now=101.1)
        manager = player.match.combat.superchargers
        for frame in range(120):
            session.tick(101.12 + frame * 0.016)
            if manager.active:
                break
        device, = manager.active.values()
        before = snapshot_digest(self.capture(session))
        device.component.set(253, device.health - 1)
        self.assertNotEqual(snapshot_digest(self.capture(session)), before)
        device.component.set(253, device.health)
        self.assertEqual(snapshot_digest(self.capture(session)), before)

    def test_active_deadeye_child_lock_is_registered_in_server_digest(self):
        from ow174.game import deadeye
        from ow174.game.script import graph

        if graph.graph(deadeye.TARGET_EFFECT) is None:
            self.skipTest("verified Deadeye target-effect graph required")
        session, peers, players = self.scene(two=True)
        player, target = players
        peers[0].message(21616, pick(player.entity, deadeye.HERO), now=101.0)
        x, y, z = player.mover.position
        target.mover.position[:] = (x + 2, y, z + 6)
        target.mover.body = None
        session.tick(101.05)
        script = player.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        peers[0].commands(script.frame + 1, 60, 32, now=101.1)
        root = next(i for i in script.component.instances.values() if i.graph.index == deadeye.ULTIMATE)
        children = root.state(root.graph.node(11).state).children
        child = children[target.body]
        before = snapshot_digest(self.capture(session))
        del children[target.body]
        self.assertNotEqual(snapshot_digest(self.capture(session)), before)
        children[target.body] = child
        self.assertEqual(snapshot_digest(self.capture(session)), before)

    def test_mccree_roll_pending_velocity_is_registered_in_server_digest(self):
        from ow174.game import mccree_roll

        session, peers, players = self.scene()
        player = players[0]
        peers[0].message(21616, pick(player.entity, mccree_roll.HERO), now=101.0)
        script = player.body_script
        peers[0].commands(script.frame + 1, 1, 8, now=101.1)
        requests = script.world.velocity_requests
        self.assertTrue(requests)
        before = snapshot_digest(self.capture(session))
        request = requests.pop()
        self.assertNotEqual(snapshot_digest(self.capture(session)), before)
        requests.append(request)
        self.assertEqual(snapshot_digest(self.capture(session)), before)

    def test_mccree_fan_retained_cone_is_registered_in_server_digest(self):
        from ow174.game import mccree_fan

        session, peers, players = self.scene()
        player = players[0]
        peers[0].message(21616, pick(player.entity, mccree_fan.HERO), now=101.0)
        script = player.body_script
        peers[0].commands(script.frame + 1, 20, 2, now=101.1)
        weapon = next(i for i in script.component.instances.values() if i.graph.index == mccree_fan.WEAPON)
        retained = weapon.state(15).shot_spread
        key = next(iter(retained))
        before = snapshot_digest(self.capture(session))
        angle = retained[key]
        retained[key] = angle * 0.5
        self.assertNotEqual(snapshot_digest(self.capture(session)), before)
        retained[key] = angle
        self.assertEqual(snapshot_digest(self.capture(session)), before)

    def test_active_coalescence_targets_are_registered_in_server_digest(self):
        from ow174.game import moira

        session, peers, players = self.scene(two=True)
        player, tracer = players
        peers[0].message(21616, pick(player.entity, moira.HERO), now=101.0)
        peers[1].message(21616, pick(tracer.entity, 0x02E0000000000003), now=101.01)
        x, y, z = player.mover.position
        tracer.mover.position[:] = (x, y, z + 3)
        tracer.mover.body = None
        session.tick(101.05)
        script = player.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        peers[0].commands(script.frame + 1, 40, 32, now=101.1)
        beam = next(i for i in script.component.instances.values() if i.graph.index == moira.COALESCENCE)
        search = beam.state(19)
        self.assertEqual(search.targets, (tracer.body,))
        before = snapshot_digest(self.capture(session))
        search.targets = ()
        self.assertNotEqual(snapshot_digest(self.capture(session)), before)
        search.targets = (tracer.body,)
        self.assertEqual(snapshot_digest(self.capture(session)), before)

    def test_live_lucio_aura_receiver_and_amp_deadline_change_server_digest(self):
        from ow174.game import lucio_aura, lucio_soundwave

        if lucio_aura.model() is None:
            self.skipTest("requires extracted Crossfade status and speed controller")
        session, peers, players = self.scene(two=True)
        player, ally = players
        peers[0].message(21616, pick(player.entity, lucio_soundwave.HERO), now=101.0)
        ally.team = player.team
        x, y, z = player.mover.position
        ally.mover.position[:] = (x, y, z + 3)
        ally.mover.body = None
        session.tick(101.05)
        peers[0].commands(player.body_script.frame + 1, 30, 16, now=101.1)
        session.tick(101.116)
        host = player.match.combat.lucio_auras
        receiver = host.receivers[ally.body]
        self.assertEqual({i.graph.index for i in receiver.component.instances.values()}, {0x0267, 0x07C7})
        activation, deadline = host.amp_deadlines[player.body]
        before = snapshot_digest(self.capture(session))
        host.amp_deadlines[player.body] = activation, deadline + 1
        self.assertNotEqual(snapshot_digest(self.capture(session)), before)
        host.amp_deadlines[player.body] = activation, deadline
        self.assertEqual(snapshot_digest(self.capture(session)), before)

    def test_live_lucio_barrier_clock_and_component_change_server_digest(self):
        from ow174.game import lucio_barrier, lucio_soundwave

        if lucio_barrier.model() is None:
            self.skipTest("requires extracted Sound Barrier status")
        session, peers, players = self.scene()
        player = players[0]
        peers[0].message(21616, pick(player.entity, lucio_soundwave.HERO), now=101.0)
        session.tick(101.05)
        script = player.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        peers[0].commands(script.frame + 1, 42, 32, now=101.1)
        lease = player.match.combat.lucio_barriers.leases[player.body]
        self.assertEqual(lease.component.instances[lease.instance_id].graph.index, 0x073B)
        before = snapshot_digest(self.capture(session))
        lease.last_tick -= 1
        self.assertNotEqual(snapshot_digest(self.capture(session)), before)
        lease.last_tick += 1
        self.assertEqual(snapshot_digest(self.capture(session)), before)

    def test_live_mercy_effect_component_is_registered_in_server_digest(self):
        from ow174.game import mercy, mercy_effects
        from ow174.game.script import graph

        if graph.effect_script(mercy_effects.BOOST) is None:
            self.skipTest("requires extracted Mercy effect bindings")
        session, peers, players = self.scene(two=True)
        player, ally = players
        peers[0].message(21616, pick(player.entity, mercy.HERO), now=101.0)
        ally.team = player.team
        x, y, z = player.mover.position
        ally.mover.position[:] = (x, y, z + 3)
        ally.mover.body = None
        session.tick(101.05)
        peers[0].commands(player.body_script.frame + 1, 15, 2, now=101.1)
        attachment, = player.match.combat.mercy_effects.attachments.values()
        self.assertEqual(attachment.instance.graph.index, 0x006C)
        self.assertEqual(attachment.target, ally.body)
        before = snapshot_digest(self.capture(session))
        attachment.last_frame -= 1
        self.assertNotEqual(snapshot_digest(self.capture(session)), before)
        attachment.last_frame += 1
        self.assertEqual(snapshot_digest(self.capture(session)), before)

    def test_active_moira_orb_energy_is_registered_in_server_digest(self):
        from ow174.game import moira

        session, peers, players = self.scene()
        player = players[0]
        peers[0].message(21616, pick(player.entity, moira.HERO), now=101.0)
        peers[0].commands(player.body_script.frame + 1, 1, 16, now=101.1)
        peers[0].commands(player.body_script.frame + 1, 20, 2, now=101.2)
        orb, = player.match.combat.moira_orbs
        before = snapshot_digest(self.capture(session))
        energy = orb.energy
        orb.energy -= 0.5
        self.assertNotEqual(snapshot_digest(self.capture(session)), before)
        orb.energy = energy
        self.assertEqual(snapshot_digest(self.capture(session)), before)

    def test_active_moira_spray_packet_contacts_are_registered_in_server_digest(self):
        from ow174.game import moira

        session, peers, players = self.scene()
        player = players[0]
        peers[0].message(21616, pick(player.entity, moira.HERO), now=101.0)
        peers[0].commands(player.body_script.frame + 1, 8, 1, now=101.1)
        packet = next(rocket.spray for rocket in player.match.combat.rockets if rocket.spray is not None)
        before = snapshot_digest(self.capture(session))
        packet.hits.add(player.body)
        self.assertNotEqual(snapshot_digest(self.capture(session)), before)
        packet.hits.clear()
        self.assertEqual(snapshot_digest(self.capture(session)), before)

    def test_active_shadow_step_selection_is_registered_in_server_digest(self):
        from ow174.game import reaper

        session, peers, players = self.scene()
        player = players[0]
        peers[0].message(21616, pick(player.entity, reaper.HERO), now=101.0)
        peers[0].commands(player.body_script.frame + 1, 8, 16, now=101.1)
        shadow = player.match.combat.reaper_shadows[player.body]
        baseline = snapshot_digest(self.capture(session))
        buttons = shadow.buttons
        shadow.buttons = 0
        self.assertNotEqual(snapshot_digest(self.capture(session)), baseline)
        shadow.buttons = buttons
        self.assertEqual(snapshot_digest(self.capture(session)), baseline)

    def test_active_death_blossom_deadline_is_registered_in_server_digest(self):
        from ow174.game import reaper

        session, peers, players = self.scene()
        player = players[0]
        peers[0].message(21616, pick(player.entity, reaper.HERO), now=101.0)
        script = player.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        peers[0].commands(script.frame + 1, 40, 32, now=101.1)
        channel = player.match.combat.reaper_blossoms[player.body]
        baseline = snapshot_digest(self.capture(session))
        deadline = channel.end
        channel.end -= 0.1
        self.assertNotEqual(snapshot_digest(self.capture(session)), baseline)
        channel.end = deadline
        self.assertEqual(snapshot_digest(self.capture(session)), baseline)

    def test_active_earthshatter_front_is_registered_in_server_digest(self):
        session, peers, players = self.scene()
        player = players[0]
        peers[0].message(21616, pick(player.entity, 0x02E0000000000007), now=101.0)
        script = player.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        peers[0].commands(script.frame + 1, 34, 32, now=101.1)
        wave = player.match.combat.earthshatters.waves[player.body]
        before = snapshot_digest(self.capture(session))
        front = wave.fronts[0]
        wave.fronts[0] = None
        self.assertNotEqual(snapshot_digest(self.capture(session)), before)
        wave.fronts[0] = front
        self.assertEqual(snapshot_digest(self.capture(session)), before)

    def test_live_fire_strike_barrier_contacts_change_server_digest(self):
        from ow174.game import combat

        session, peers, players = self.scene()
        player = players[0]
        peers[0].message(21616, pick(player.entity, 0x02E0000000000007), now=101.0)
        peers[0].commands(player.body_script.frame + 1, 34, 16, now=101.1)
        strike, = player.match.combat.rockets
        self.assertEqual(strike.definition, combat.FIRE_STRIKE)
        self.assertFalse(strike.hit_barriers)
        baseline = snapshot_digest(self.capture(session))
        # Test storage sensitivity independently from combat contact resolution.
        strike.hit_barriers.add(player.body)
        self.assertNotEqual(snapshot_digest(self.capture(session)), baseline)
        strike.hit_barriers.clear()
        self.assertEqual(snapshot_digest(self.capture(session)), baseline)

    def test_transferred_pulse_owner_and_body_are_causal_snapshot_inputs(self):
        from ow174.game import combat

        session, peers, players = self.scene(two=True)
        player, genji = players
        peers[0].message(21616, pick(player.entity, 0x02E0000000000003), now=101.0)
        peers[1].message(21616, pick(genji.entity, 0x02E0000000000029), now=101.0)
        instance = next(i for i in player.body_script.component.instances.values() if i.graph.index == 0x34)
        state = instance.state(0)
        bomb = player.match.combat.pulse_bombs.launch(
            player, state, player.match.combat._eye(player), (0, 0, 1), 15, 5,
            combat.hit_def(state, state.node.field("m_6396149F")),
            combat.hit_def(state, state.node.field("m_D2D11CE9")), None)
        baseline = snapshot_digest(self.capture(session))
        # Storage proof only: actual Q/E transfer is covered by combat fixtures.
        bomb.shooter, bomb.source = genji, genji.body
        self.assertNotEqual(snapshot_digest(self.capture(session)), baseline)
        bomb.shooter, bomb.source = player, player.body
        self.assertEqual(snapshot_digest(self.capture(session)), baseline)

    def scene(self, two=False):
        case = journal_tests.ServerJournalTests()
        session, sent = case.make()
        peers, players = case.connected(session, sent, two=two)
        return session, peers, players

    @staticmethod
    def capture(session):
        return export_server_state(session.server, archives=session._matches.values(),
                                   collision_provider=session._collision_reads, assets=session.registry)

    def test_export_never_evaluates_or_sends_and_keeps_rng_queue_and_allocation_order(self):
        session, _, players = self.scene()
        script = players[0].body_script
        component = script.component
        rng = component.rng.getstate()
        counter = component.seq.next_value
        instances = [(i.id, list(i.states), list(i.queue)) for i in component.instances.values()]
        with (
            mock.patch.object(runtime.Var, "value", side_effect=AssertionError("no variable evaluation")),
            mock.patch.object(runtime.State, "payload", side_effect=AssertionError("no payload evaluation")),
            mock.patch.object(BodyScript, "snapshot", side_effect=AssertionError("no driver snapshot")),
            mock.patch.object(Mover, "snapshot", side_effect=AssertionError("no mover simulation snapshot")),
            mock.patch.object(session.server.sock, "sendto", side_effect=AssertionError("no send")),
            mock.patch.object(runtime.Instance, "state", side_effect=AssertionError("no lazy allocation")),
        ):
            first = self.capture(session)
            self.assertEqual(snapshot_digest(first), snapshot_digest(self.capture(session)))
        self.assertEqual(component.rng.getstate(), rng)
        self.assertEqual(component.seq.next_value, counter)
        actual = [(i.id, list(i.states), list(i.queue)) for i in component.instances.values()]
        self.assertEqual(actual, instances)
        self.assertFalse(first["restorable"])

    def test_hidden_vm_counter_transport_and_physics_changes_change_digest(self):
        session, _, players = self.scene()
        player, client = players[0], players[0].client
        initial = snapshot_digest(self.capture(session))
        client.expected_in += 1
        self.assertNotEqual(snapshot_digest(self.capture(session)), initial)
        client.expected_in -= 1
        self.assertEqual(snapshot_digest(self.capture(session)), initial)
        player.match.combat.stuns[player.body] = player.match.tick + 50
        self.assertNotEqual(snapshot_digest(self.capture(session)), initial)
        player.match.combat.stuns.pop(player.body)
        self.assertEqual(snapshot_digest(self.capture(session)), initial)
        next(player.body_script.component.seq)
        changed = snapshot_digest(self.capture(session))
        self.assertNotEqual(changed, initial)
        player.mover.corrected = 17
        self.assertNotEqual(snapshot_digest(self.capture(session)), changed)

    def test_equal_world_content_with_different_match_mover_alias_is_distinguished(self):
        session, _, players = self.scene()
        player = players[0]
        first = snapshot_digest(self.capture(session))
        # Same static bytes but a different identity: next use(match.world) resets the body.
        player.mover.world = flat_world(0.0)
        self.assertIsNot(player.mover.world, player.match.collision_world)
        self.assertNotEqual(snapshot_digest(self.capture(session)), first)

    def test_equal_data_clone_is_distinguished_from_the_canonical_lazy_table(self):
        session, _, players = self.scene()
        player = players[0]
        first = snapshot_digest(self.capture(session))
        original = player.mover.data
        self.assertIs(mover_module.loaded_mover_table()[player.hero.body], original)
        replacement = dataclasses.replace(original)
        player.mover.data = player.mover.values = replacement
        self.assertEqual(replacement, original)
        self.assertIsNot(replacement, original)
        self.assertNotEqual(snapshot_digest(self.capture(session)), first)

    def test_cross_mover_mutable_physics_alias_is_rejected(self):
        session, _, players = self.scene(two=True)
        players[1].mover.position = players[0].mover.position
        with self.assertRaisesRegex(ServerSnapshotError, "shared across Mover"):
            self.capture(session)

    def test_queue_movement_callback_closure_and_stream_alias_are_stored_without_building(self):
        session, _, players = self.scene()
        player = players[0]
        player.client.queue_entities([EntityUpdate(player.body, movement=Movement((1, 2, 3)))])
        # A real source builder is stored, not executed at hash time.
        record = EntityUpdate(0xD0000010, OP_UPDATE, player.match._game_mode_create)
        player.client.queue_entities([record])
        with mock.patch("ow174.game.match.world.create", side_effect=AssertionError("no builder execution")):
            first = snapshot_digest(self.capture(session))
        function = Match._game_mode_create
        constants = tuple(75 if value == 114 else value for value in function.__code__.co_consts)
        changed = types.FunctionType(function.__code__.replace(co_consts=constants), function.__globals__)
        changed.__qualname__ = function.__qualname__
        record.build = types.MethodType(changed, player.match)
        self.assertNotEqual(snapshot_digest(self.capture(session)), first)
        base = Snapshot(100)
        player.body_script.stream.base = base
        player.body_script.stream.pending = {1: (base, set())}
        shared = snapshot_digest(self.capture(session))
        player.body_script.stream.pending = {1: (copy.deepcopy(base), set())}
        self.assertNotEqual(snapshot_digest(self.capture(session)), shared)

    def test_weak_generation_values_only_project_strong_domain_records(self):
        session, _, players = self.scene()
        client = players[0].client
        self.assertIs(type(client._entity_record_generations), weakref.WeakKeyDictionary)
        before = snapshot_digest(self.capture(session))
        outside = EntityUpdate(0xA0000120)
        client._entity_record_generations[outside] = 15
        self.assertEqual(snapshot_digest(self.capture(session)), before)
        client.entities.append(outside)
        strong = snapshot_digest(self.capture(session))
        client._entity_record_generations[outside] = 16
        self.assertNotEqual(snapshot_digest(self.capture(session)), strong)

    def test_unknown_fields_and_callback_classes_are_rejected(self):
        session, _, players = self.scene()
        client = players[0].client
        client.new_unregistered_field = 7
        with self.assertRaises(ServerSnapshotError):
            self.capture(session)
        del client.new_unregistered_field
        client.entities.append(EntityUpdate(1))
        client.entities[-1].stream = object()
        with self.assertRaises(ServerSnapshotError):
            self.capture(session)


class StoredJournalTests(unittest.TestCase):
    def make(self):
        sent = []

        def send(data, address):
            sent.append((data, address))
            return len(data)

        session = RecordingServerSession(
            environment=FixedSource(), collision_provider=StaticCollisionProvider(flat_world(0.0)),
            sendto=send, verify_stored_state=True,
        )
        return session, sent

    def test_stored_profile_replays_creation_raw_spawn_and_vm_state(self):
        with mock.patch("socket.socket", side_effect=AssertionError("no sockets")):
            session, sent = self.make()
            (handoff,) = session.create_match(MAP, [(1, "Alpha", SOLDIER, 0, False)])
            peer = Peer(session, handoff, sent, ("127.0.0.1", 47000))
            peer.send(flags=SYN)
            peer.send()
            peer.message(21601, {})
            acknowledge_ownership(session, (peer,))
            session.tick(100.8)
            player = session.server.matches[0].players[0]
            peer.message(21616, pick(player.entity, SOLDIER), now=100.8)
            document = session.finish()
            self.assertEqual(document["state_profile"], "registered-domain-v1")
            self.assertTrue(all(len(row["state_before"]) == len(row["state_after"]) == 64
                                for row in document["roots"]))
            replay = _ReplaySession(document, session.registry.resolve)
            result = replay.run()
        self.assertTrue(result["stored_state_verified"])
        self.assertFalse(result["complete_battle_replay"])
        damaged = copy.deepcopy(document)
        damaged["roots"][-1]["state_after"] = "0" * 64
        with self.assertRaises(ServerJournalError):
            _ReplaySession(damaged, session.registry.resolve).run()

    def test_unrecorded_hidden_queue_change_cannot_be_sealed_even_without_wire_effect(self):
        session, _ = self.make()
        session.create_match(MAP, [])
        session.server._next_conn += 1
        with self.assertRaisesRegex(ServerJournalError, "unrecorded stored"):
            session.finish()

    def test_two_player_stored_graphs_match_through_damage_death_respawn_and_hero_change(self):
        session, sent = self.make()
        handoffs = session.create_match(MAP, [(1, "A", SOLDIER, 0, False), (2, "B", SOLDIER, 1, False)])
        peers = [Peer(session, handoff, sent, ("127.0.0.1", 47100 + index))
                 for index, handoff in enumerate(handoffs)]
        for peer in peers:
            peer.send(flags=SYN)
            peer.send()
            peer.message(21601, {})
        acknowledge_ownership(session, peers)
        session.tick(100.8)
        players = session.server.matches[0].players
        for peer, player in zip(peers, players, strict=True):
            peer.message(21616, pick(player.entity, SOLDIER), now=100.8)
        peers[0].commands(players[0].body_script.frame + 1, 60, 1)
        self.assertTrue(players[1].match.combat.is_dead(players[1]))
        dead_body = players[1].body
        session.tick(105.0, missed=200)
        self.assertNotEqual(players[1].body, dead_body)
        self.assertFalse(players[1].match.combat.is_dead(players[1]))
        session.apply("switch_hero", {"account": 1, "hero": 0x02E0000000000002})
        session.report_callbacks()
        document = session.finish()
        replay = _ReplaySession(document, session.registry.resolve)
        result = replay.run()
        self.assertTrue(result["stored_state_verified"])
        copied = replay.server.matches[0].players
        self.assertEqual([p.body for p in copied], [p.body for p in players])
        self.assertEqual(copied[0].hero.guid, players[0].hero.guid)

    def test_unrecorded_change_between_roots_fails_before_running_the_next_tick(self):
        session, _ = self.make()
        session.create_match(MAP, [])
        session.server.tick += 1
        before = session.server.tick
        with self.assertRaisesRegex(ServerJournalError, "between controlled roots"):
            session.tick(100.8)
        self.assertEqual(session.server.tick, before)
        with self.assertRaises(ServerJournalError):
            session.finish()


class MoverTableTests(unittest.TestCase):
    def test_peek_is_cold_without_io_and_canonical_identity_is_cached(self):
        original = mover_module.loaded_mover_table()
        self.addCleanup(setattr, mover_module, "_mover_table", original)
        mover_module._movers.cache_clear()
        with mock.patch.object(Path, "read_text", side_effect=AssertionError("peek must not read")):
            self.assertIsNone(mover_module.loaded_mover_table())
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "movers.json"
            path.write_text('{"0x04000000000003CF":{"run_forward":7.0}}', encoding="utf-8")
            with mock.patch.object(mover_module, "MOVERS_PATH", path):
                first = mover_module.mover_data(None)
                self.assertEqual(first.run_forward, 7.0)
                self.assertIs(mover_module.mover_data(0x04000000000003CF), first)
                self.assertIs(mover_module.loaded_mover_table()[0x04000000000003CF], first)
                path.write_text('{}', encoding="utf-8")
                self.assertIs(mover_module.mover_data(None), first)
                mover_module._movers.cache_clear()
                self.assertEqual(mover_module.mover_data(None).run_forward, 5.5)


if __name__ == "__main__":
    unittest.main()
