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

from ow174.game import (
    adaptive_shield,
    area_projectile,
    ball_grapple,
    combat,
    doomfist_passive,
    graviton,
    hand_cannon,
    heavy_charge,
    hook,
    lucio_barrier,
    mei_wall,
    meteor_strike,
    npc,
    orisa_halt,
    piledriver,
    rocket_punch,
    seismic_slam,
)
from ow174.game import mover as mover_module
from ow174.game.collision import StaticCollisionProvider, flat_world
from ow174.game.commands import Command
from ow174.game.content import SOLDIER, heroes
from ow174.game.link import SYN
from ow174.game.match import Match
from ow174.game.mover import Mover
from ow174.game.script import runtime
from ow174.game.script.driver import BodyScript, Snapshot
from ow174.game.server_journal import RecordingServerSession, ServerJournalError, _ReplaySession
from ow174.game.server_snapshot import ServerSnapshotError, export_server_state, snapshot_digest
from ow174.game.world import OP_UPDATE, EntityUpdate, Movement


class ServerSnapshotTests(unittest.TestCase):
    def test_actual_halt_flight_and_pull_are_causal_read_only_storage(self):
        session, _, players = self.scene(two=True)
        orisa, hog = players
        match = orisa.match
        fight = match.combat
        if fight.halt.control is None or fight.halt.query is None:
            self.skipTest("prepared 104319 0CCC/0CCE Halt inputs required")
        for player, name, x, z in ((orisa, "Orisa", 0, 0), (hog, "Roadhog", 1.2, 5)):
            hero = next(hero for hero in heroes().values() if hero.name == name)
            session.apply("switch_hero", {"account": player.account_lo, "hero": hero.guid})
            player.mover.position[:] = (x, match.game_map.spawns[0].position[1], z)
            player.mover.body = None
        for offset in range(61):
            tick = match.tick + 1
            match.update(tick * .016, tick)
            for player in players:
                button = 2 if player is orisa and offset in (30, 55) else 0
                player.take_commands([Command(tick, buttons=button)])
        orb, = fight.halt.orbs.values()
        pull = fight.halt.pulls[hog.body]
        self.assertEqual(pull.script.graph.index, 0xCCE)
        with mock.patch.object(orisa_halt.Halt, "update", side_effect=AssertionError("no Halt simulation")):
            before = snapshot_digest(self.capture(session))
            self.assertEqual(snapshot_digest(self.capture(session)), before)
            remaining = orb.left
            orb.left += .1
            self.assertNotEqual(snapshot_digest(self.capture(session)), before)
            orb.left = remaining
            clock = pull.script.external_time
            self.assertIsNotNone(clock)
            pull.script.external_time = clock + 16
            self.assertNotEqual(snapshot_digest(self.capture(session)), before)
            pull.script.external_time = clock
            fight.halt.pulls.pop(hog.body)
            self.assertNotEqual(snapshot_digest(self.capture(session)), before)

    def test_ultimate_healing_eligibility_is_causal_read_only_storage(self):
        session, _, players = self.scene()
        player = players[0]
        target = next(t for t in player.match.combat._targets() if t.owner is player)
        vitals = player.match.combat.vitals_of(target)
        before = snapshot_digest(self.capture(session))
        vitals.ult_healing_deficit = 20.0
        changed = snapshot_digest(self.capture(session))
        self.assertNotEqual(changed, before)
        self.assertEqual(snapshot_digest(self.capture(session)), changed)
        self.assertEqual(vitals.ult_healing_deficit, 20.0)
        vitals.healing_damage.append((player.match.combat.tick, 20.0))
        timed = snapshot_digest(self.capture(session))
        self.assertNotEqual(timed, changed)
        self.assertEqual(snapshot_digest(self.capture(session)), timed)
        vitals.health_changes[player.match.combat.tick] = -20.0
        player.match.combat.breather_deadlines[player.body] = player.match.combat.tick + 42
        self.assertNotEqual(snapshot_digest(self.capture(session)), timed)
        self.assertEqual(vitals.ult_healing_deficit, 20.0)

    def test_active_hook_and_secondary_fragment_storage_is_read_only_and_hashed(self):
        session, _, players = self.scene()
        player = players[0]
        match = player.match
        session.apply("switch_hero", {"account": player.account_lo, "hero": 0x02E0000000000040})
        script = player.body_script
        for offset in range(12):
            frame = script.frame + 1
            match.tick = match.combat.tick = frame
            script.command(frame, 8 if offset == 0 else 0)
            match.combat.hooks.command(player, Command(frame))
        self.assertTrue(match.combat.hooks.flights)
        flight = match.combat.hooks.flights[player.body]
        weapon = next(i for i in script.component.instances.values() if i.graph.index == 0x01C6)
        state = weapon.state(weapon.graph._by_pos[29].state)
        burst = match.combat._fragment_burst(state, (0.0, 0.0, 1.0))
        self.assertIsNotNone(burst)
        match.combat.rockets.append(combat.Rocket(
            player, player.team, match.combat._eye(player), (0.0, 0.0, 1.0),
            60.0, 0.15, combat.HitDef(50), None, burst=burst,
        ))
        with mock.patch.object(hook.Hooks, "update", side_effect=AssertionError("no hook simulation")):
            before = snapshot_digest(self.capture(session))
            self.assertEqual(snapshot_digest(self.capture(session)), before)
            flight.travelled += 0.25
            travelled = snapshot_digest(self.capture(session))
            self.assertNotEqual(travelled, before)
            match.combat.hooks.player_pose_ticks[player.body] = match.tick
            pulled = snapshot_digest(self.capture(session))
            self.assertNotEqual(pulled, travelled)
            match.combat.hooks.player_command_cutoffs[player.body] = script.frame
            cutoff = snapshot_digest(self.capture(session))
            self.assertNotEqual(cutoff, pulled)
            player.command_clock = script.frame, match.tick
            self.assertNotEqual(snapshot_digest(self.capture(session)), cutoff)

    def test_baptiste_flight_and_device_are_causal_read_only_storage(self):
        session, _, players = self.scene()
        player = players[0]
        fight = player.match.combat
        fields = fight.baptiste_fields
        source = player.body_script.component
        position = tuple(player.mover.position)
        flight = combat.orisa_barrier.Flight(player, source, fight.next_bot, position,
                                             (0.0, 0.0, 60.0), 0, 20.0, 60.0, fight.tick)
        fight.next_bot += 1
        fields.flights[player.body] = flight
        with mock.patch.object(runtime.Var, "value", side_effect=AssertionError("no evaluation")):
            in_flight = snapshot_digest(self.capture(session))
            self.assertEqual(snapshot_digest(self.capture(session)), in_flight)
            flight.remaining -= 1
            self.assertNotEqual(snapshot_digest(self.capture(session)), in_flight)
        del fields.flights[player.body]
        fields.land(flight, position, fight.tick)
        device = fields.devices[player.body]
        with mock.patch.object(runtime.Var, "value", side_effect=AssertionError("no evaluation")):
            landed = snapshot_digest(self.capture(session))
            self.assertEqual(snapshot_digest(self.capture(session)), landed)
            fields.damage(device, 50)
            damaged = snapshot_digest(self.capture(session))
            self.assertNotEqual(damaged, landed)
            device.component.world.retired = True
            self.assertNotEqual(snapshot_digest(self.capture(session)), damaged)

    def test_blizzard_flight_area_and_frost_are_causal_read_only_storage(self):
        session, _, players = self.scene(two=True)
        player, victim = players
        fight = player.match.combat
        blizzard = fight.blizzard
        position = fight._eye(player)
        flight = area_projectile.Flight(
            player, player.body, position, (0.0, 0.0, 15.0), 9.8, 60.0, fight.tick,
        )
        blizzard.flights.append(flight)
        with mock.patch.object(blizzard.__class__, "update", side_effect=AssertionError("no simulation")):
            in_flight = snapshot_digest(self.capture(session))
            self.assertEqual(snapshot_digest(self.capture(session)), in_flight)
            flight.left -= 0.1
            self.assertNotEqual(snapshot_digest(self.capture(session)), in_flight)
            blizzard.land(flight, position, fight.tick)
            area, = blizzard.areas.values()
            blizzard.show()
            landed = snapshot_digest(self.capture(session))
            self.assertEqual(snapshot_digest(self.capture(session)), landed)
            area.frost_due[victim.body] = area.starts + 200
            due = snapshot_digest(self.capture(session))
            self.assertNotEqual(due, landed)
            fight.mei_frost[player.body, victim.body, area.entity] = 5000, fight.tick + 63
            self.assertNotEqual(snapshot_digest(self.capture(session)), due)

    def test_ball_grapple_rope_and_rehit_storage_are_causal_and_read_only(self):
        session, _, players = self.scene(two=True)
        source, victim = players
        fight = source.match.combat
        grapple = ball_grapple.Grapple((7, 1), fight.tick - 4, fight.tick, (0, 8, 12), 22.2, True, 12.1)
        fight.ball_grapples[source.body] = grapple
        fight.grapple_seen[source.body] = grapple.activation
        fight.ball_contacts[source.body] = {victim.body}
        with mock.patch.object(ball_grapple, "movement", side_effect=AssertionError("no simulation")):
            before = snapshot_digest(self.capture(session))
            self.assertEqual(snapshot_digest(self.capture(session)), before)
            grapple.rope += .25
            longer = snapshot_digest(self.capture(session))
            self.assertNotEqual(longer, before)
            fight.ball_contacts[source.body].clear()
            self.assertNotEqual(snapshot_digest(self.capture(session)), longer)

    def test_graviton_world_pull_is_causal_read_only_storage(self):
        session, _, players = self.scene(two=True)
        source, victim = players
        fight = source.match.combat
        manager = fight.gravitons
        field = graviton.Field(source, source.body, source.team, fight._eye(victim),
                               6.0, fight.tick, fight.tick + 250)
        manager.fields.append(field)
        manager.pulled.add(victim.body)
        with mock.patch.object(graviton.Gravitons, "update", side_effect=AssertionError("no simulation")):
            before = snapshot_digest(self.capture(session))
            self.assertEqual(snapshot_digest(self.capture(session)), before)
            manager.pulled.clear()
            released = snapshot_digest(self.capture(session))
            self.assertNotEqual(released, before)
            field.end_tick += 1
            self.assertNotEqual(snapshot_digest(self.capture(session)), released)

    def test_heavy_charge_contact_and_selected_action_are_causal_storage(self):
        session, _, players = self.scene()
        victim = players[0]
        fight = victim.match.combat
        definition = npc.Definition(0x02E00000000001BA, heavy_charge.BODY, "Heavy Assault", 4000)
        bot = npc.spawn(victim.match, definition, (0, 1, 0), team=1)
        contact = heavy_charge.Contact(1, 0.0, victim.body)
        fight.heavy_charges[bot.entity] = contact
        with mock.patch.object(heavy_charge, "advance", side_effect=AssertionError("no simulation")):
            before = snapshot_digest(self.capture(session))
            self.assertEqual(snapshot_digest(self.capture(session)), before)
            contact.pinned = None
            released = snapshot_digest(self.capture(session))
            self.assertNotEqual(released, before)
            bot.attack_action = "charge"
            self.assertNotEqual(snapshot_digest(self.capture(session)), released)

    def test_sniper_reposition_mover_is_causal_read_only_storage(self):
        session, _, players = self.scene()
        player = players[0]
        fight = player.match.combat
        definition = npc.Definition(0x02E00000000001B6, 0x0400000000001E7D, "Sniper", 200)
        bot = npc.spawn(player.match, definition, (0, 1, 2), team=1)
        mover = Mover(bot.position, world=fight._collision())
        fight.sniper_repositions[bot.entity] = mover
        with mock.patch.object(Mover, "step_pose", side_effect=AssertionError("no movement simulation")):
            before = snapshot_digest(self.capture(session))
            self.assertEqual(snapshot_digest(self.capture(session)), before)
            mover.position[2] += 0.25
            self.assertNotEqual(snapshot_digest(self.capture(session)), before)

    def test_ice_wall_pool_and_preview_are_causal_read_only_storage(self):
        session, _, players = self.scene()
        owner = players[0]
        fight = owner.match.combat
        pillar = mei_wall.Pillar(fight.next_bot, owner.body, owner.team, tuple(owner.mover.position),
                                 (1.0, 0.0, 0.0), 400.0, fight.tick + 313)
        fight.next_bot += 1
        fight.walls.pieces[pillar.entity] = pillar
        fight.walls.inputs[owner.body] = mei_wall.Input(preview=True)
        fight.walls.show()
        with mock.patch.object(mei_wall.Walls, "update", side_effect=AssertionError("no simulation")):
            before = snapshot_digest(self.capture(session))
            self.assertEqual(snapshot_digest(self.capture(session)), before)
            pillar.health -= 30
            damaged = snapshot_digest(self.capture(session))
            self.assertNotEqual(damaged, before)
            fight.walls.inputs[owner.body].rotated = True
            self.assertNotEqual(snapshot_digest(self.capture(session)), damaged)

    def test_actual_punch_contact_and_shields_are_causal_read_only_storage(self):
        session, _, players = self.scene(two=True)
        doom, hog = players
        session.apply("switch_hero", {"account": doom.account_lo, "hero": rocket_punch.HERO})
        session.apply("switch_hero", {"account": hog.account_lo, "hero": 0x02E0000000000040})
        match = doom.match
        fight = match.combat
        y = doom.mover.position[1]
        match.collision_world = flat_world(y)
        for player, z in ((doom, 0), (hog, 6)):
            player.mover.position[:] = (0, y, z)
            player.mover.body = None
        frame = max(doom.body_script.frame, hog.body_script.frame)
        for offset in range(1, 142):
            at = frame + offset
            match.tick = at
            fight.update(at * .016, at, frames=False)
            doom.take_commands([Command(at, buttons=2 if 12 <= offset < 102 else 0)])
            hog.take_commands([Command(at)])
            if hog.body in fight.punch_walls:
                break
        self.assertIn(hog.body, fight.punch_walls)
        self.assertIn(doom.body, fight.punch_dashes)
        shield = fight.vitals[doom.body].temporary_shields[doomfist_passive.SHIELD_TAG]
        with mock.patch.object(rocket_punch, "update", side_effect=AssertionError("no simulation")):
            before = snapshot_digest(self.capture(session))
            self.assertEqual(snapshot_digest(self.capture(session)), before)
            fight.punch_walls[hog.body].damage += 1
            changed = snapshot_digest(self.capture(session))
            self.assertNotEqual(changed, before)
            shield.now -= 1
            self.assertNotEqual(snapshot_digest(self.capture(session)), changed)

    def test_actual_slam_wave_is_causal_read_only_storage(self):
        session, _, players = self.scene(two=True)
        doom, hog = players
        session.apply("switch_hero", {"account": doom.account_lo, "hero": rocket_punch.HERO})
        session.apply("switch_hero", {"account": hog.account_lo, "hero": 0x02E0000000000040})
        match = doom.match
        fight = match.combat
        y = doom.mover.position[1]
        match.collision_world = flat_world(y)
        for player, z in ((doom, 0), (hog, 10)):
            player.mover.position[:] = (0, y, z)
            player.mover.body = None
        frame = max(doom.body_script.frame, hog.body_script.frame)
        for offset in range(1, 105):
            at = frame + offset
            match.tick = at
            fight.update(at * .016, at, frames=False)
            doom.take_commands([Command(at, buttons=16 if offset == 12 else 0)])
            hog.take_commands([Command(at)])
            cast = fight.slams.get(doom.body)
            if cast is not None and cast.wave_tick is not None:
                break
        self.assertIsNotNone(cast)
        self.assertIsNotNone(cast.wave_tick)
        self.assertFalse(cast.hit)
        with mock.patch.object(seismic_slam, "update", side_effect=AssertionError("no simulation")):
            before = snapshot_digest(self.capture(session))
            self.assertEqual(snapshot_digest(self.capture(session)), before)
            cast.wave_tick += 1
            retimed = snapshot_digest(self.capture(session))
            self.assertNotEqual(retimed, before)
            cast.hit.add(hog.body)
            self.assertNotEqual(snapshot_digest(self.capture(session)), retimed)

    def test_actual_meteor_pending_landing_is_causal_read_only_storage(self):
        session, _, players = self.scene(two=True)
        doom, hog = players
        session.apply("switch_hero", {"account": doom.account_lo, "hero": rocket_punch.HERO})
        session.apply("switch_hero", {"account": hog.account_lo, "hero": 0x02E0000000000040})
        match = doom.match
        fight = match.combat
        y = doom.mover.position[1]
        match.collision_world = flat_world(y)
        for player, z in ((doom, 0), (hog, 5)):
            player.mover.position[:] = (0, y, z)
            player.mover.body = None
        script = doom.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        frame = max(doom.body_script.frame, hog.body_script.frame)
        for offset in range(1, 95):
            at = frame + offset
            match.tick = at
            fight.update(at * .016, at, frames=False)
            button = 32 if offset == 12 else 1 if offset == 84 else 0
            doom.take_commands([Command(at, buttons=button)])
            hog.take_commands([Command(at)])
        cast = fight.meteors[doom.body]
        self.assertTrue(cast.committed)
        self.assertFalse(cast.impacted)
        with mock.patch.object(meteor_strike, "command", side_effect=AssertionError("no simulation")):
            before = snapshot_digest(self.capture(session))
            self.assertEqual(snapshot_digest(self.capture(session)), before)
            x, y, z = cast.target
            cast.target = (x, y, z + .5)
            moved = snapshot_digest(self.capture(session))
            self.assertNotEqual(moved, before)
            cast.confirm_down = not cast.confirm_down
            self.assertNotEqual(snapshot_digest(self.capture(session)), moved)

    def test_actual_piledriver_air_lock_is_causal_read_only_storage(self):
        session, _, players = self.scene(two=True)
        ball, hog = players
        session.apply("switch_hero", {"account": ball.account_lo, "hero": piledriver.HERO})
        session.apply("switch_hero", {"account": hog.account_lo, "hero": 0x02E0000000000040})
        match = ball.match
        fight = match.combat
        y = ball.mover.position[1]
        match.collision_world = flat_world(y)
        for player, z in ((ball, 0), (hog, 1.5)):
            player.mover.position[:] = (0, y, z)
            player.mover.body = None
        frame = max(ball.body_script.frame, hog.body_script.frame)
        for offset in range(1, 156):
            if offset == 49:
                ball.mover.position[1] += 8
                ball.mover.body = None
                ball.mover.flags_word |= mover_module.FELL
            at = frame + offset
            match.tick = at
            fight.update(at * .016, at, frames=False)
            ball.take_commands([Command(at, buttons=128 if offset == 52 else 0)])
            hog.take_commands([Command(at)])
            if hog.body in fight.pile_air_locks:
                break
        self.assertIn(hog.body, fight.pile_air_locks)
        with mock.patch.object(piledriver, "command", side_effect=AssertionError("no simulation")):
            before = snapshot_digest(self.capture(session))
            self.assertEqual(snapshot_digest(self.capture(session)), before)
            start, deadline = fight.pile_air_locks[hog.body]
            fight.pile_air_locks[hog.body] = (start, deadline + 1)
            self.assertNotEqual(snapshot_digest(self.capture(session)), before)

    def test_actual_adaptive_shield_expiry_is_causal_read_only_storage(self):
        session, _, players = self.scene(two=True)
        ball, hog = players
        session.apply("switch_hero", {"account": ball.account_lo, "hero": piledriver.HERO})
        session.apply("switch_hero", {"account": hog.account_lo, "hero": 0x02E0000000000040})
        match = ball.match
        fight = match.combat
        y = ball.mover.position[1]
        match.collision_world = flat_world(y)
        for player, z in ((ball, 0), (hog, 2)):
            player.mover.position[:] = (0, y, z)
            player.mover.body = None
        frame = max(ball.body_script.frame, hog.body_script.frame)
        for offset in range(1, 53):
            at = frame + offset
            match.tick = at
            fight.update(at * .016, at, frames=False)
            ball.take_commands([Command(at, buttons=16 if offset == 50 else 0)])
            hog.take_commands([Command(at)])
        lease = fight.adaptive_shields[ball.body]
        shield = fight.vitals[ball.body].temporary_shields[adaptive_shield.POOL]
        self.assertEqual(shield.now, 175)
        with mock.patch.object(adaptive_shield, "update", side_effect=AssertionError("no simulation")):
            before = snapshot_digest(self.capture(session))
            self.assertEqual(snapshot_digest(self.capture(session)), before)
            lease.expires += 1
            extended = snapshot_digest(self.capture(session))
            self.assertNotEqual(extended, before)
            shield.now -= 1
            self.assertNotEqual(snapshot_digest(self.capture(session)), extended)

    def test_actual_hand_cannon_origin_identity_is_causal_read_only_storage(self):
        session, _, players = self.scene(two=True)
        doom, hog = players
        session.apply("switch_hero", {"account": doom.account_lo, "hero": hand_cannon.HERO})
        session.apply("switch_hero", {"account": hog.account_lo, "hero": 0x02E0000000000040})
        match = doom.match
        fight = match.combat
        y = doom.mover.position[1]
        match.collision_world = flat_world(y)
        for player, z in ((doom, 0), (hog, 12)):
            player.mover.position[:] = (0, y, z)
            player.mover.body = None
        frame = max(doom.body_script.frame, hog.body_script.frame)
        for offset in range(1, 47):
            at = frame + offset
            match.tick = at
            fight.update(at * .016, at, frames=False)
            doom.take_commands([Command(at, buttons=1 if offset == 46 else 0)])
            hog.take_commands([Command(at)])
        pellets = [pellet for pellet in fight.rockets if pellet.shooter is doom]
        self.assertEqual(len(pellets), 11)
        self.assertTrue(all(pellet.source_body == doom.body for pellet in pellets))
        with mock.patch.object(combat.Combat, "_fly", side_effect=AssertionError("no simulation")):
            before = snapshot_digest(self.capture(session))
            self.assertEqual(snapshot_digest(self.capture(session)), before)
            pellets[0].source_body = hog.body
            self.assertNotEqual(snapshot_digest(self.capture(session)), before)

    def test_sound_barrier_lease_and_pool_are_causal_read_only_storage(self):
        session, _, players = self.scene()
        owner = players[0]
        fight = owner.match.combat
        target = next(t for t in fight._targets() if t.owner is owner)
        manager = fight.sound_barriers
        manager.grant(owner.body, target, owner.body_script.component, 6.0)
        with mock.patch.object(lucio_barrier.Barriers, "update", side_effect=AssertionError("no simulation")):
            before = snapshot_digest(self.capture(session))
            self.assertEqual(snapshot_digest(self.capture(session)), before)
            manager.leases[owner.body].last_tick += 1
            advanced = snapshot_digest(self.capture(session))
            self.assertNotEqual(advanced, before)
            fight.vitals_of(target).temporary_shields[lucio_barrier.POOL].now -= 2
            self.assertNotEqual(snapshot_digest(self.capture(session)), advanced)

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
