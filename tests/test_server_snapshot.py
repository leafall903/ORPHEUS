"""Stored domain hashes detect hidden causal state without executing the game."""

import copy
import dataclasses
import json
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

from ow174.game import assassin_victim, combat, messages
from ow174.game import earthshatter, reinhardt_charge
from ow174.game import mover as mover_module
from ow174.game.bits import BitReader
from ow174.game.collision import StaticCollisionProvider, flat_world
from ow174.game.combat import HitDef, ProjectedBarrier, TemporaryShield
from ow174.game.commands import FIRE, SECONDARY, Command
from ow174.game.content import SOLDIER
from ow174.game.link import KIND_FRAME, SYN, peek_connection
from ow174.game.match import Match
from ow174.game.mover import Mover
from ow174.game.script import runtime
from ow174.game.script.driver import BodyScript, Snapshot
from ow174.game.server_journal import RecordingServerSession, ServerJournalError, _ReplaySession
from ow174.game.server_snapshot import ServerSnapshotError, export_server_state, snapshot_digest
from ow174.game.world import OP_UPDATE, EntityUpdate, Movement


class ServerSnapshotTests(unittest.TestCase):
    def test_pending_authoritative_self_healing_changes_stored_digest(self):
        session, _, players = self.scene()
        script = players[0].body_script
        before = snapshot_digest(self.capture(session))
        script.self_healing = 0.32
        self.assertNotEqual(snapshot_digest(self.capture(session)), before)
        script.self_healing = 0.0
        self.assertEqual(snapshot_digest(self.capture(session)), before)

    def test_active_wave_and_charge_change_stored_combat_digest(self):
        session, _, (player, target) = self.scene(two=True)
        fight = player.match.combat
        state = next(
            state for instance in player.body_script.component.instances.values()
            for state in instance.states.values()
        )
        wave = earthshatter.Wave(
            shooter=player, state=state, origin=(0., 1., 0.), centre=(0., 1., 2.),
            yaw=0., speed=30., reach=20., cone=60., height=1., radius=1., stun_seconds=2.5,
            normal=HitDef(50), close=HitDef(120), started=fight.tick, fronts=[(0., 1., 0.)],
        )
        charge = reinhardt_charge.Charge(player=target, state=state, deadline=fight.tick + 50)
        before = snapshot_digest(self.capture(session))
        fight.shatters[player.body] = wave
        fight.charges[target.body] = charge
        active = snapshot_digest(self.capture(session))
        self.assertNotEqual(active, before)
        wave.hit.add((target.body, fight.replacement_ticks.get(target.body)))
        hit = snapshot_digest(self.capture(session))
        self.assertNotEqual(hit, active)
        charge.pinned = player.body
        self.assertNotEqual(snapshot_digest(self.capture(session)), hit)

    def test_baptiste_deployable_effect_and_health_inputs_change_digest(self):
        session, _, players = self.scene()
        player = players[0]
        fight = player.match.combat
        before = snapshot_digest(self.capture(session))
        actor = fight.create_deployable(
            player, 0x0400000000002B50, "Amplification Matrix", (0.0, 1.0, 4.0),
            health=0, radius=0, height=0, seconds=10,
        )
        fight.baptiste.matrices[actor.entity] = actor
        created = snapshot_digest(self.capture(session))
        self.assertNotEqual(created, before)
        actor.rotation = (0.0, 1.0, 0.0, 0.0)
        self.assertNotEqual(snapshot_digest(self.capture(session)), created)
        changed = snapshot_digest(self.capture(session))
        player.body_script.component.maximum_health += 1
        self.assertNotEqual(snapshot_digest(self.capture(session)), changed)
        changed = snapshot_digest(self.capture(session))
        fight.stuns[player.body] = fight.tick + 50
        self.assertNotEqual(snapshot_digest(self.capture(session)), changed)
        changed = snapshot_digest(self.capture(session))
        fight.vitals[player.body].ult_healing_deficit += 1
        self.assertNotEqual(snapshot_digest(self.capture(session)), changed)
        from ow174.game.combat import HitDef, Rocket

        rocket = Rocket(player, player.team, (0.0, 1.0, 0.0), (0.0, 0.0, 1.0), 60, 60, None, None)
        fight.rockets.append(rocket)
        changed = snapshot_digest(self.capture(session))
        rocket.source_entity ^= 1
        self.assertNotEqual(snapshot_digest(self.capture(session)), changed)
        changed = snapshot_digest(self.capture(session))
        rocket.definition = 0x04000000000029AD
        self.assertNotEqual(snapshot_digest(self.capture(session)), changed)
        changed = snapshot_digest(self.capture(session))
        rocket.deflections = 1
        self.assertNotEqual(snapshot_digest(self.capture(session)), changed)
        changed = snapshot_digest(self.capture(session))
        rocket.range_left = 490.0
        self.assertNotEqual(snapshot_digest(self.capture(session)), changed)
        from ow174.game.deflection import ProjectileTags

        changed = snapshot_digest(self.capture(session))
        fight.projectile_tags = ProjectileTags(json.dumps({
            "version": 1, "build": "1.74.0.0.104319", "entities": {"0x04000000000029AD": []},
        }).encode())
        self.assertNotEqual(snapshot_digest(self.capture(session)), changed)
        changed = snapshot_digest(self.capture(session))
        player.body_script.passive_charge_ms += 1
        self.assertNotEqual(snapshot_digest(self.capture(session)), changed)
        from ow174.game.healing import Modifier

        fight.healing.anti[player.body] = Modifier(player.body, fight.tick, fight.tick + 250)
        changed = snapshot_digest(self.capture(session))
        fight.healing.anti[player.body].end += 1
        self.assertNotEqual(snapshot_digest(self.capture(session)), changed)
        for mutate in (
            lambda: setattr(fight.absorption, 'last_tick', fight.tick + 100),
            lambda: fight.absorption.converted.update({player.body: (12, 3)}),
            lambda: fight.vitals[player.body].add_temporary((12, 6, 1), 0x5847, 43.2),
            lambda: setattr(rocket, 'direct', HitDef(24)),
            lambda: setattr(rocket.direct, 'health_operation', None),
        ):
            changed = snapshot_digest(self.capture(session))
            mutate()
            self.assertNotEqual(snapshot_digest(self.capture(session)), changed)

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

    def test_wave_health_amount_and_retired_pool_id_are_stored(self):
        session, _, players = self.scene()
        player = players[0]
        fight = player.match.combat
        vitals = fight.vitals_of(next(target for target in fight._targets() if target.owner is player))
        changed = vitals.changed.copy()
        initial = snapshot_digest(self.capture(session))

        remove = vitals.add_wave_health(60)
        added = snapshot_digest(self.capture(session))
        self.assertNotEqual(added, initial)
        vitals.wave_health.current -= 20
        self.assertNotEqual(snapshot_digest(self.capture(session)), added)

        remove()
        vitals.changed.clear()
        vitals.changed.update(changed)
        self.assertNotEqual(snapshot_digest(self.capture(session)), initial)
        vitals.wave_health_id = 0
        self.assertEqual(snapshot_digest(self.capture(session)), initial)

    def test_hidden_vm_counter_transport_and_physics_changes_change_digest(self):
        session, _, players = self.scene()
        player, client = players[0], players[0].client
        initial = snapshot_digest(self.capture(session))
        player.body_script.world.velocity_requests.append((player.match.tick + 1, 'delta', (0.0, 0.0, 15.0)))
        self.assertNotEqual(snapshot_digest(self.capture(session)), initial)
        player.body_script.world.velocity_requests.clear()
        self.assertEqual(snapshot_digest(self.capture(session)), initial)
        client.expected_in += 1
        self.assertNotEqual(snapshot_digest(self.capture(session)), initial)
        client.expected_in -= 1
        self.assertEqual(snapshot_digest(self.capture(session)), initial)
        player.match.combat.stuns[player.body] = player.match.tick + 50
        self.assertNotEqual(snapshot_digest(self.capture(session)), initial)
        player.match.combat.stuns.pop(player.body)
        self.assertEqual(snapshot_digest(self.capture(session)), initial)
        player.match.combat.pins[0xC000100F] = player.body
        self.assertNotEqual(snapshot_digest(self.capture(session)), initial)
        player.match.combat.pins.clear()
        self.assertEqual(snapshot_digest(self.capture(session)), initial)
        from ow174.game.bots import Bot
        from ow174.game.mover import FELL, Mover, mover_data
        from ow174.game.npc_navigation import LedgeDrop, Navigator, begin

        bot = Bot(0xC000100F, {"hero": f"0x{player.hero.guid:016X}", "team": 1,
                              "position": (0, 1, 5), "rotation": (0, 0, 0, 1), "graphs": [], "vars": {}},
                  {"body": f"0x{player.hero.body:016X}", "health": 200, "name": "Navigation fixture"}, {})
        player.match.bots.append(bot)
        before_navigation = snapshot_digest(self.capture(session))
        bot.navigation = Navigator(player.mover, path=((0.0, 1.0, 2.0),), target=player.body)
        route_digest = snapshot_digest(self.capture(session))
        self.assertNotEqual(route_digest, before_navigation)
        bot.navigation.path = ((0.0, 1.0, 3.0),)
        self.assertNotEqual(snapshot_digest(self.capture(session)), route_digest)
        from ow174.game.combat import Target

        target = Target(player.body, player.team, (0.0, 1.0, 20.0), 1.8, 0.5, 1.6, player)
        bot.navigation.search = begin((0.0, 1.0, 5.0), target, 2.5)
        search_digest = snapshot_digest(self.capture(session))
        self.assertNotEqual(search_digest, route_digest)
        bot.navigation.search.visited.add((1, 0, 8))
        self.assertNotEqual(snapshot_digest(self.capture(session)), search_digest)
        bot.navigation.search.visited.clear()
        self.assertEqual(snapshot_digest(self.capture(session)), search_digest)
        bot.navigation.search.blocked_by_teammate = True
        self.assertNotEqual(snapshot_digest(self.capture(session)), search_digest)
        fall = Mover(bot.position, world=player.match.collision_world, data=mover_data(bot.body))
        bot.navigation = Navigator(fall, path=(LedgeDrop(started=player.match.tick),))
        approaching = snapshot_digest(self.capture(session))
        bot.navigation.path = (LedgeDrop(started=player.match.tick, released=True),)
        released = snapshot_digest(self.capture(session))
        self.assertNotEqual(released, approaching)
        fall.flags_word |= FELL
        fall.air_ticks = 8
        falling = snapshot_digest(self.capture(session))
        self.assertNotEqual(falling, released)
        fall.air_ticks = 9
        self.assertNotEqual(snapshot_digest(self.capture(session)), falling)
        fall.air_ticks = 8
        self.assertEqual(snapshot_digest(self.capture(session)), falling)
        bot.dead = True
        self.assertNotEqual(snapshot_digest(self.capture(session)), falling)
        bot.navigation = None
        bot.dead = False
        self.assertEqual(snapshot_digest(self.capture(session)), before_navigation)
        player.match.bots.pop()
        self.assertEqual(snapshot_digest(self.capture(session)), initial)
        next(player.body_script.component.seq)
        changed = snapshot_digest(self.capture(session))
        self.assertNotEqual(changed, initial)
        player.mover.corrected = 17
        self.assertNotEqual(snapshot_digest(self.capture(session)), changed)

    def test_shared_vm_definition_mutation_changes_the_next_capture(self):
        session, _, players = self.scene(two=True)
        left, right = (player.body_script.component for player in players)
        right_graphs = {instance.graph for instance in right.instances.values()}
        shared = next(instance.graph for instance in left.instances.values() if instance.graph in right_graphs)
        original = snapshot_digest(self.capture(session))
        self.assertEqual(session._state_digest(), original)
        self.assertEqual(session._state_digest(), original)
        shared.fields["snapshot_probe"] = [1]
        try:
            changed = session._state_digest()
            self.assertNotEqual(changed, original)
            self.assertEqual(changed, snapshot_digest(self.capture(session)))
            shared.fields["snapshot_probe"][0] = 2
            changed_again = session._state_digest()
            self.assertNotEqual(changed_again, changed)
            self.assertEqual(changed_again, snapshot_digest(self.capture(session)))
        finally:
            del shared.fields["snapshot_probe"]
        self.assertEqual(session._state_digest(), original)

    def test_equal_world_content_with_different_match_mover_alias_is_distinguished(self):
        session, _, players = self.scene()
        player = players[0]
        first = snapshot_digest(self.capture(session))
        # Same static bytes but a different identity: next use(match.world) resets the body.
        player.mover.world = flat_world(0.0)
        self.assertIsNot(player.mover.world, player.match.collision_world)
        self.assertNotEqual(snapshot_digest(self.capture(session)), first)

    def test_genji_hit_ledgers_are_causal_snapshot_state(self):
        session, _, players = self.scene()
        fight = players[0].match.combat
        body = players[0].body
        first = snapshot_digest(self.capture(session))
        fight.genji_strikes[body] = (6, 1, (0.0, 1.0, 0.0), {0xC0001000})
        strike = snapshot_digest(self.capture(session))
        self.assertNotEqual(strike, first)
        fight.genji_blades[body] = {(10, 66): (1, {0xC0001001})}
        self.assertNotEqual(snapshot_digest(self.capture(session)), strike)
        fight.genji_strikes.clear()
        fight.genji_blades.clear()
        self.assertEqual(snapshot_digest(self.capture(session)), first)
        players[0].genji_mobility.air_jump_used = True
        self.assertNotEqual(snapshot_digest(self.capture(session)), first)
        players[0].genji_mobility.air_jump_used = False
        self.assertEqual(snapshot_digest(self.capture(session)), first)
        fight.genji_assists[0xC0001000] = {body: (players[0], 123)}
        self.assertNotEqual(snapshot_digest(self.capture(session)), first)
        fight.genji_assists.clear()
        self.assertEqual(snapshot_digest(self.capture(session)), first)

    def test_live_genji_projectile_extent_is_causal(self):
        session, peers, players = self.scene()
        player = players[0]
        self.assertTrue(session.apply("switch_hero", {"account": player.account_lo,
                                                      "hero": 0x02E0000000000029}))
        for tick in range(8):
            now = 100.96 + tick * 0.016
            session.tick(now)
            peers[0].drain()
            peers[0].send(now=now)
        peers[0].commands(player.body_script.frame + 1, 40, 2, now=101.2)
        rockets = player.match.combat.rockets
        self.assertEqual(len(rockets), 3)
        self.assertTrue(all(rocket.shooter is player for rocket in rockets))
        for rocket in rockets:
            self.assertAlmostEqual(rocket.direct.projectile_extent, .1)

        initial = snapshot_digest(self.capture(session))
        direct = rockets[0].direct
        rockets[0].direct = dataclasses.replace(direct, projectile_extent=0.0)
        self.assertNotEqual(snapshot_digest(self.capture(session)), initial)
        rockets[0].direct = direct
        self.assertEqual(snapshot_digest(self.capture(session)), initial)

    def test_live_piercing_spray_hit_ledger_is_causal(self):
        case = journal_tests.ServerJournalTests()
        session, sent = case.make()
        peers, (genji, mei) = case.connected(session, sent, first_hero=0x02E0000000000029,
                                            second_hero=0x02E00000000000DD)
        fight = genji.match.combat
        peers[1].commands(mei.body_script.frame + 1, 1, 1, yaw=-32768, pitch=500, now=100.944)
        for tick in range(18):
            now = 100.96 + tick * combat.TICK
            session.tick(now)
            if fight.rockets and genji.body in fight.rockets[0].hit_entities:
                break
        rocket, = fight.rockets
        self.assertEqual(rocket.hit_entities, {genji.body})
        self.assertEqual(fight.vitals[genji.body].total, 197.25)
        self.assertIn(genji.body, fight.frost_hits)
        before = snapshot_digest(self.capture(session))
        rocket.hit_entities.clear()
        self.assertNotEqual(snapshot_digest(self.capture(session)), before)

    def test_live_sleep_dart_wake_floor_is_causal(self):
        case = journal_tests.ServerJournalTests()
        session, sent = case.make()
        peers, (genji, ana) = case.connected(session, sent, first_hero=0x02E0000000000029,
                                            second_hero=0x02E000000000013B)
        fight = genji.match.combat
        for tick in range(70):
            now = 100.96 + tick * combat.TICK
            session.tick(now)
            if fight.is_asleep(genji):
                break
            peers[1].commands(ana.body_script.frame + 1, 1, 8 if tick == 0 else 0,
                              yaw=-32768, pitch=500, now=now)
        self.assertTrue(fight.is_asleep(genji))
        vitals = fight.vitals[genji.body]
        self.assertEqual(vitals.total, 195)
        self.assertEqual(vitals.sleep_wake_floor - fight.tick, combat.SLEEP_WAKE_FLOOR_TICKS)
        before = snapshot_digest(self.capture(session))
        vitals.sleep_wake_floor += 1
        self.assertNotEqual(snapshot_digest(self.capture(session)), before)

    def test_temporary_pool_amount_and_decay_clock_are_causal(self):
        session, _, players = self.scene()
        player = players[0]
        fight = player.match.combat
        target = next(t for t in fight._targets() if t.owner is player)
        vitals = fight.vitals_of(target)
        pool = vitals.add_temporary((22, 0, 1), 0x5847, 2, 52.2, player.match.tick)
        initial = snapshot_digest(self.capture(session))
        pool.now -= 1
        damaged = snapshot_digest(self.capture(session))
        self.assertNotEqual(damaged, initial)
        pool.last_tick += 1
        self.assertNotEqual(snapshot_digest(self.capture(session)), damaged)

    def test_pin_source_and_victim_body_identity_are_causal(self):
        session, _, players = self.scene(two=True)
        fight = players[0].match.combat
        initial = snapshot_digest(self.capture(session))
        fight.pins[0xC0001001] = players[0].body
        pinned = snapshot_digest(self.capture(session))
        self.assertNotEqual(pinned, initial)
        fight.pins[0xC0001001] = players[1].body
        self.assertNotEqual(snapshot_digest(self.capture(session)), pinned)
        fight.pins.clear()
        self.assertEqual(snapshot_digest(self.capture(session)), initial)
        lease = assassin_victim.Lease(0xC0001001, 8, 16, 2, players[0].body,
                                      players[1].body_script.component, players[0].body_script.component, 20)
        fight.assassin_victims[lease.source] = lease
        victim_script = snapshot_digest(self.capture(session))
        self.assertNotEqual(victim_script, initial)
        lease.activation += 1
        self.assertNotEqual(snapshot_digest(self.capture(session)), victim_script)
        previous = lease.source_component
        lease.source_component = players[0].body_script.component
        self.assertNotEqual(snapshot_digest(self.capture(session)), victim_script)
        lease.source_component = previous
        fight.assassin_victims.clear()
        self.assertEqual(snapshot_digest(self.capture(session)), initial)

    def test_live_pet_component_health_and_source_generation_are_causal(self):
        session, peers, players = self.scene()
        player = players[0]
        match = player.match
        session.apply("switch_hero", {"account": player.account_lo, "hero": 0x02E0000000000007})
        for tick in range(8):
            now = 100.96 + tick * 0.016
            session.tick(now)
            peers[0].drain()
            peers[0].send(now=now)
        peers[0].commands(player.body_script.frame + 1, 90, 0, now=101.2)
        peers[0].commands(player.body_script.frame + 1, 40, 2, now=101.2)
        self.assertEqual(len(match.combat.barriers.scripted), 1)
        barrier = next(iter(match.combat.barriers.scripted.values()))
        initial = snapshot_digest(self.capture(session))
        mesh, digest = barrier.mesh, barrier.geometry_digest
        barrier.mesh = (((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),)
        self.assertNotEqual(snapshot_digest(self.capture(session)), initial)
        barrier.mesh = mesh
        barrier.geometry_digest = "changed geometry input"
        self.assertNotEqual(snapshot_digest(self.capture(session)), initial)
        barrier.geometry_digest = digest
        self.assertEqual(snapshot_digest(self.capture(session)), initial)
        barrier.vitals.now[0] -= 1
        damaged = snapshot_digest(self.capture(session))
        self.assertNotEqual(damaged, initial)
        barrier.activation += 1
        self.assertNotEqual(snapshot_digest(self.capture(session)), damaged)

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

    def test_brigitte_bridge_and_temporary_armor_expiry_are_stored_without_getters(self):
        session, _, players = self.scene()
        player = players[0]
        self.assertTrue(session.apply("switch_hero", {
            "account": player.account_lo, "hero": 0x02E0000000000195,
        }))
        fight = player.match.combat
        target = next(t for t in fight._targets() if t.owner is player)
        vitals = fight.vitals_of(target)
        vitals.grant_armour(15, 100, fight.now + 30)
        with mock.patch.object(runtime.Var, "value", side_effect=AssertionError("no getter")):
            first = snapshot_digest(self.capture(session))
        vitals.bonus_armour[0].until += 1
        second = snapshot_digest(self.capture(session))
        self.assertNotEqual(first, second)
        # The missing-Pet consumer is causal state even before its getter runs.
        fight.brigitte.barriers[player.body].health -= 1
        self.assertNotEqual(second, snapshot_digest(self.capture(session)))

    def test_repair_initial_controller_and_pending_cleanup_are_causal_without_getters(self):
        from ow174.game import brigitte

        session, _, players = self.scene(two=True)
        source, target = players
        self.assertTrue(session.apply("switch_hero", {
            "account": source.account_lo, "hero": brigitte.HERO,
        }))
        fight = source.match.combat
        effect = brigitte.RepairHeal(source, target.body, fight.now + 6, fight.now, 55,
                                     source, brigitte.HERO, False, fight.now + 3)
        fight.brigitte.repairs[source.body, target.body] = effect
        with mock.patch.object(runtime.Var, "value", side_effect=AssertionError("no getter")):
            first = snapshot_digest(self.capture(session))
            effect.cleanup_at += 0.1
            second = snapshot_digest(self.capture(session))
            effect.controller = target
            third = snapshot_digest(self.capture(session))
        self.assertNotEqual(first, second)
        self.assertNotEqual(second, third)

    def test_pending_brigitte_passive_charge_clock_is_stored_without_getters(self):
        session, _, players = self.scene()
        player = players[0]
        self.assertTrue(session.apply("switch_hero", {
            "account": player.account_lo, "hero": 0x02E0000000000195,
        }))
        script = player.body_script
        with mock.patch.object(runtime.Var, "value", side_effect=AssertionError("no getter")):
            first = snapshot_digest(self.capture(session))
            script.passive_charge_ms += 1
            second = snapshot_digest(self.capture(session))
        self.assertNotEqual(first, second)

    def test_active_brigitte_stun_records_and_recipient_lease_are_stored_without_getters(self):
        session, _, players = self.scene(two=True)
        source, recipient = players
        self.assertTrue(session.apply("switch_hero", {
            "account": source.account_lo, "hero": 0x02E0000000000195,
        }))
        fight = source.match.combat
        target = next(t for t in fight._targets() if t.owner is recipient)
        parent = next(i for i in source.body_script.component.instances.values() if i.graph.index == 0xEE3)
        self.assertTrue(fight.brigitte.host_control(source, target, parent, parent.graph.node(13)))
        self.assertEqual(len(fight.brigitte.stuns), 1)
        lease = fight.brigitte.stuns[0]
        self.assertTrue(lease.instance.states[20].active)
        with mock.patch.object(runtime.Var, "value", side_effect=AssertionError("no getter")):
            before = snapshot_digest(self.capture(session))
        lease.instance.vars[215].base = 0.5
        self.assertNotEqual(before, snapshot_digest(self.capture(session)))

    def test_actual_counter_charge_stores_both_knockdowns_without_getters(self):
        session, _, players = self.scene(two=True)
        brig, rival = players
        for player, hero in zip(players, (0x02E0000000000195, 0x02E0000000000007), strict=True):
            self.assertTrue(session.apply("switch_hero", {"account": player.account_lo, "hero": hero}))
        match = brig.match
        x, y, z = brig.mover.position
        rival.mover.position, rival.mover.body = [x, y, z + 3], None
        frame = max(player.body_script.frame for player in players)

        def step(brig_buttons, rival_buttons):
            nonlocal frame
            frame += 1
            match.update(match.combat.now + 0.016, frame)
            brig.take_commands([Command(frame, buttons=brig_buttons)])
            rival.take_commands([Command(frame, yaw=-32768, buttons=rival_buttons)])

        for _ in range(30):
            step(SECONDARY, 0)
        step(SECONDARY | FIRE, 8)
        step(0, 0)
        leases = match.combat.brigitte.stuns
        self.assertEqual({lease.target for lease in leases}, {brig, rival})
        self.assertTrue(all(lease.instance.states[20].active for lease in leases))
        self.assertEqual({lease.instance.vars[215].value() for lease in leases}, {2})
        with mock.patch.object(runtime.Var, "value", side_effect=AssertionError("no getter")):
            before = snapshot_digest(self.capture(session))
            for lease in leases:
                duration = lease.instance.vars[215]
                original = duration.base
                duration.base = 1.5
                self.assertNotEqual(before, snapshot_digest(self.capture(session)))
                duration.base = original
            self.assertEqual(before, snapshot_digest(self.capture(session)))

    def test_actual_ice_wall_preview_and_pillars_are_stored_without_getters(self):
        from test_game_combat import aim

        from ow174.game.mei_wall import MEI

        session, _, players = self.scene()
        mei = players[0]
        self.assertTrue(session.apply("switch_hero", {"account": mei.account_lo, "hero": MEI}))
        match = mei.match
        fight = match.combat
        frame = mei.body_script.frame
        x, y, z = mei.mover.position
        yaw, pitch = aim(fight._eye(mei), (x, y, z + 4))

        def step(buttons=0):
            nonlocal frame
            frame += 1
            match.update(fight.now + 0.016, frame)
            mei.take_commands([Command(frame, yaw=yaw, pitch=pitch, buttons=buttons)])

        def assert_causal(record, **changes):
            with mock.patch.object(runtime.Var, "value", side_effect=AssertionError("no getter")):
                initial = snapshot_digest(self.capture(session))
                for field, value in changes.items():
                    original = getattr(record, field)
                    setattr(record, field, value)
                    try:
                        self.assertNotEqual(initial, snapshot_digest(self.capture(session)))
                    finally:
                        setattr(record, field, original)

        for _ in range(20):
            step()
        step(16)
        step()
        preview = fight.walls.inputs[mei.body]
        self.assertTrue(preview.preview)
        self.assertFalse(fight.walls.pieces)
        assert_causal(preview, rotated=True)
        step(FIRE)
        step()
        self.assertFalse(preview.preview)
        self.assertEqual(len(fight.walls.pieces), 5)
        centre = list(fight.walls.pieces.values())[2]
        assert_causal(centre, health=330.0, expires=centre.expires - 1)

    def test_actual_orisa_deployment_is_stored_without_getters(self):
        from ow174.game import orisa_barrier

        mesh = Path(__file__).resolve().parents[3] / "013E" / "orisa_geometry_174.json"
        if not mesh.is_file():
            self.skipTest("verified build 104319 Protective Barrier mesh required")
        self.enterContext(mock.patch.dict("os.environ", {"ORPHEUS_ORISA_GEOMETRY": str(mesh)}))
        session, _, players = self.scene()
        orisa = players[0]
        self.assertTrue(session.apply("switch_hero", {
            "account": orisa.account_lo, "hero": orisa_barrier.HERO,
        }))
        match = orisa.match
        frame = orisa.body_script.frame

        def step(buttons=0):
            nonlocal frame
            frame += 1
            match.update(match.combat.now + 0.016, frame)
            orisa.take_commands([Command(frame, yaw=-32768, buttons=buttons)])

        def until(predicate):
            for _ in range(120):
                if predicate():
                    return
                step()
            self.fail("actual deployment did not advance to expected phase")

        def assert_causal(record, field, value):
            with mock.patch.object(runtime.Var, "value", side_effect=AssertionError("no getter")):
                initial = snapshot_digest(self.capture(session))
                original = getattr(record, field)
                setattr(record, field, value)
                self.assertNotEqual(initial, snapshot_digest(self.capture(session)))
                setattr(record, field, original)
                self.assertEqual(initial, snapshot_digest(self.capture(session)))

        for _ in range(25):
            step()
        step(16)
        fight = match.combat
        manager = fight.orisa_barriers
        until(lambda: any(r.deploy_body == orisa.body for r in fight.rockets))
        rocket = next(r for r in fight.rockets if r.deploy_body == orisa.body)
        assert_causal(rocket, "gravity", 0.0)
        until(lambda: manager.pending)
        [(due, rocket)] = manager.pending
        assert_causal(rocket, "position", (0.0, 0.0, 0.0))
        self.assertGreater(due, fight.tick)
        until(lambda: manager.active)
        [shield] = manager.active.values()
        self.assertEqual(shield.health, 600)
        assert_causal(shield, "health", 530.0)
        assert_causal(shield, "until", shield.until - 1)

    def test_winston_dome_health_and_expiry_are_causal_without_getters(self):
        from ow174.game.winston_barrier import Dome

        session, _, players = self.scene()
        player = players[0]
        fight = player.match.combat
        dome = Dome(player.entity, player.team, tuple(player.mover.position), 700, fight.now + 9)
        fight.winston_domes[player.body] = dome
        with mock.patch.object(runtime.Var, "value", side_effect=AssertionError("no getter")):
            before = snapshot_digest(self.capture(session))
            dome.health -= 70
            damaged = snapshot_digest(self.capture(session))
            self.assertNotEqual(before, damaged)
            dome.until -= 1
            self.assertNotEqual(damaged, snapshot_digest(self.capture(session)))

    def test_reinhardt_capture_and_contact_history_are_causal_without_getters(self):
        from ow174.game.combat import ChargeContact

        session, _, players = self.scene(two=True)
        source, target = players
        fight = source.match.combat
        contact = ChargeContact({target.body}, target.body)
        fight.charge_contacts[source.body] = contact
        with mock.patch.object(runtime.Var, "value", side_effect=AssertionError("no getter")):
            captured = snapshot_digest(self.capture(session))
            contact.captured = None
            released = snapshot_digest(self.capture(session))
            self.assertNotEqual(captured, released)
            contact.hit.clear()
            self.assertNotEqual(released, snapshot_digest(self.capture(session)))

    def test_pending_rocket_punch_wall_hit_is_causal_without_getters(self):
        from ow174.game.combat import PunchImpact

        session, _, players = self.scene(two=True)
        source, target = players
        fight = source.match.combat
        with mock.patch.object(runtime.Var, "value", side_effect=AssertionError("no getter")):
            before = snapshot_digest(self.capture(session))
            fight.punch_impacts[target.body] = PunchImpact(
                source, source.body, (0.0, 0.0, 1.0), 150, fight.tick + 32,
            )
            self.assertNotEqual(before, snapshot_digest(self.capture(session)))
            del fight.punch_impacts[target.body]
            self.assertEqual(before, snapshot_digest(self.capture(session)))

    def test_personal_barrier_pool_is_causal_without_getters(self):
        session, _, players = self.scene()
        player = players[0]
        pools = player.match.combat.personal_barriers
        pools[player.body] = (3, 1, 165.0)
        with mock.patch.object(runtime.Var, "value", side_effect=AssertionError("no getter")):
            before = snapshot_digest(self.capture(session))
            pools[player.body] = (3, 1, 130.0)
            self.assertNotEqual(before, snapshot_digest(self.capture(session)))

    def test_projected_barrier_health_and_cast_are_causal_without_getters(self):
        session, _, players = self.scene(two=True)
        source, recipient = players
        combat = source.match.combat
        barrier = ProjectedBarrier(source.body, combat.now + 2.0, 165.0)
        combat.projected_barriers[recipient.body] = barrier
        with mock.patch.object(runtime.Var, "value", side_effect=AssertionError("no getter")):
            before = snapshot_digest(self.capture(session))
            barrier.health = 130.0
            reduced = snapshot_digest(self.capture(session))
            self.assertNotEqual(before, reduced)
            combat.projected_casts[source.body, 7] = 1
            self.assertNotEqual(reduced, snapshot_digest(self.capture(session)))

    def test_grasp_shield_decay_is_causal_without_getters(self):
        session, _, players = self.scene()
        player = players[0]
        combat = player.match.combat
        vitals = combat.vitals_of(next(t for t in combat._targets() if t.owner is player))
        vitals.temporary_shield = TemporaryShield(37, 72, combat.now + 3, 5, (7, 1))
        with mock.patch.object(runtime.Var, "value", side_effect=AssertionError("no getter")):
            before = snapshot_digest(self.capture(session))
            vitals.temporary_shield.decay_from += 1
            self.assertNotEqual(before, snapshot_digest(self.capture(session)))

    def test_healing_modifier_windows_are_causal_without_getters(self):
        session, _, players = self.scene()
        player = players[0]
        combat = player.match.combat
        vitals = combat.vitals_of(next(t for t in combat._targets() if t.owner is player))
        with mock.patch.object(runtime.Var, "value", side_effect=AssertionError("no getter")):
            before = snapshot_digest(self.capture(session))
            vitals.healing_block = (1.0, 5.0)
            blocked = snapshot_digest(self.capture(session))
            self.assertNotEqual(before, blocked)
            vitals.healing_boost = (3.05, 7.0)
            self.assertNotEqual(blocked, snapshot_digest(self.capture(session)))

    def test_bot_impulse_physics_and_pending_tick_are_causal_without_getters(self):
        from test_bots import make_bot

        session, _, players = self.scene()
        match = players[0].match
        bot = make_bot()
        match.bots.append(bot)
        bot.impulse((0, 3, 25), match.tick, match.collision_world)
        bot.step(match.tick + 1, .016, match.collision_world)
        with (
            mock.patch.object(runtime.Var, "value", side_effect=AssertionError("no getter")),
            mock.patch.object(Mover, "step_pose", side_effect=AssertionError("no simulation")),
            mock.patch.object(Mover, "snapshot", side_effect=AssertionError("no getter")),
        ):
            before = snapshot_digest(self.capture(session))
            self.assertEqual(before, snapshot_digest(self.capture(session)))
            bot.impulse_mover.fall += .5
            changed = snapshot_digest(self.capture(session))
            self.assertNotEqual(before, changed)
            bot.last_tick += 1
            self.assertNotEqual(changed, snapshot_digest(self.capture(session)))

    def test_pin_recipient_and_manual_target_are_causal_without_getters(self):
        session, _, players = self.scene()
        player = players[0]
        fight = player.match.combat
        from test_game_brigitte_npcs import ActualNpcTests

        from ow174.game import npc

        fixture = ActualNpcTests()
        fixture.setUpClass()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        definition = next(d for d in fixture.catalog.values() if d.name == "Assassin")
        bot = npc.spawn(player.match, definition, tuple(player.mover.position), team=1 - player.team)
        self.assertIsNotNone(bot.body_script)
        with mock.patch.object(runtime.Var, "value", side_effect=AssertionError("no getter")):
            initial = snapshot_digest(self.capture(session))
            fight.pins[bot.entity] = player.body
            controlled = snapshot_digest(self.capture(session))
            self.assertNotEqual(initial, controlled)
            bot.body_script.component.world.manual_target = player.body
            self.assertNotEqual(controlled, snapshot_digest(self.capture(session)))


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

    def test_stored_actual_baptiste_primary_replays_active_sigma_absorption(self):
        from ow174.game import absorption, deflection

        if not deflection.catalog().admitted(0x0400000000002CFA):
            raise RuntimeError('requires explicit003 interception metadata')
        bap, sigma = 0x02E0000000000221, absorption.SIGMA
        session, sent = self.make()
        handoffs = session.create_match(MAP, [(1, 'Bap', bap, 0, False), (2, 'Sigma', sigma, 1, False)])
        peers = [Peer(session, handoff, sent, ('127.0.0.1', 47400 + index))
                 for index, handoff in enumerate(handoffs)]
        for peer in peers:
            peer.send(flags=SYN)
            peer.send()
            peer.message(21601, {})
        acknowledge_ownership(session, peers)
        session.tick(100.8)
        players = session.server.matches[0].players
        for peer, player, hero in zip(peers, players, (bap, sigma), strict=True):
            peer.message(21616, pick(player.entity, hero), now=100.8)
        peers[1].commands(players[1].body_script.frame + 1, 5, 8, yaw=-32768)
        session.tick(100.816)
        peers[0].commands(players[0].body_script.frame + 1, 1, 1, now=100.816)
        session.tick(100.832)
        fight = players[0].match.combat
        root = next(i for i in players[1].body_script.component.instances.values() if i.graph.index == 0x1D02)
        self.assertTrue(root.state(1).active)
        self.assertEqual(root.vars[21312].value(), 24)
        self.assertEqual(fight.vitals[players[1].body].total, 400)
        document = session.finish()
        replay = _ReplaySession(document, session.registry.resolve)
        self.assertTrue(replay.run()['stored_state_verified'])
        copied = next(i for i in replay.server.matches[0].players[1].body_script.component.instances.values()
                      if i.graph.index == 0x1D02)
        self.assertTrue(copied.state(1).active)
        self.assertEqual(copied.vars[21312].value(), 24)
        self.assertEqual(replay.server.matches[0].combat.absorption.last_tick, fight.absorption.last_tick)

    def test_stored_actual_baptiste_grenade_replays_deflected_flight_authority(self):
        from ow174.game import deflection

        if not deflection.catalog().admitted(0x04000000000029AD):
            raise RuntimeError("requires explicit003 deflection metadata")
        bap, genji = 0x02E0000000000221, deflection.GENJI
        session, sent = self.make()
        handoffs = session.create_match(MAP, [(1, "Bap", bap, 0, False), (2, "Genji", genji, 1, False)])
        peers = [Peer(session, handoff, sent, ("127.0.0.1", 47300 + index))
                 for index, handoff in enumerate(handoffs)]
        for peer in peers:
            peer.send(flags=SYN)
            peer.send()
            peer.message(21601, {})
        acknowledge_ownership(session, peers)
        session.tick(100.8)
        players = session.server.matches[0].players
        for peer, player, hero in zip(peers, players, (bap, genji), strict=True):
            peer.message(21616, pick(player.entity, hero), now=100.8)
        peers[1].commands(players[1].body_script.frame + 1, 5, 16, yaw=-32768)
        peers[0].commands(players[0].body_script.frame + 1, 1, 2)
        for now, missed in ((100.816, 0), (100.880, 3)):
            session.tick(now, missed=missed)
            for peer in peers:
                peer.drain()
                peer.send(now=now)
        fight = players[0].match.combat
        rocket, = fight.rockets
        self.assertEqual(rocket.deflections, 1)
        self.assertIs(rocket.shooter, players[1])
        self.assertEqual((rocket.team, rocket.source_entity, rocket.definition),
                         (1, players[1].body, 0x04000000000029AD))
        self.assertIsNone(rocket.shot)
        self.assertGreater(rocket.left, 59.0)
        self.assertGreater(rocket.range_left, 0.0)
        self.assertLess(rocket.range_left, 500.0)
        document = session.finish()
        replay = _ReplaySession(document, session.registry.resolve)
        self.assertTrue(replay.run()["stored_state_verified"])
        copied, = replay.server.matches[0].combat.rockets
        self.assertEqual((copied.deflections, copied.team, copied.source_entity, copied.definition,
                          copied.position, copied.direction, copied.left, copied.flown, copied.range_left),
                         (rocket.deflections, rocket.team, rocket.source_entity, rocket.definition,
                          rocket.position, rocket.direction, rocket.left, rocket.flown, rocket.range_left))

    def test_stored_actual_ana_grenade_replays_active_received_healing_state(self):
        ana, bap = 0x02E000000000013B, 0x02E0000000000221
        session, sent = self.make()
        handoffs = session.create_match(MAP, [(1, "Ana", ana, 0, False), (2, "Bap", bap, 1, False)])
        peers = [Peer(session, handoff, sent, ("127.0.0.1", 47200 + index))
                 for index, handoff in enumerate(handoffs)]
        for peer in peers:
            peer.send(flags=SYN)
            peer.send()
            peer.message(21601, {})
        acknowledge_ownership(session, peers)
        session.tick(100.8)
        players = session.server.matches[0].players
        for peer, player, hero in zip(peers, players, (ana, bap), strict=True):
            peer.message(21616, pick(player.entity, hero), now=100.8)
        peers[0].commands(players[0].body_script.frame + 1, 15, 0x10)
        # Input roots emit the grenade; scheduler roots advance its world flight.
        for now, missed in ((100.816, 0), (101.024, 12)):
            session.tick(now, missed=missed)
            for peer in peers:
                peer.drain()
                peer.send(now=now)
        fight = players[0].match.combat
        self.assertIsNotNone(fight.healing.spec)
        self.assertIn(players[1].body, fight.healing.anti)
        self.assertEqual(fight.vitals[players[1].body].total, 140)
        document = session.finish()
        replay = _ReplaySession(document, session.registry.resolve)
        result = replay.run()
        self.assertTrue(result["stored_state_verified"])
        copied = replay.server.matches[0].combat
        self.assertEqual(copied.healing.spec, fight.healing.spec)
        self.assertEqual(copied.healing.anti, fight.healing.anti)
        self.assertEqual(copied.healing.amp, fight.healing.amp)

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

    def test_stored_journal_replays_in_flight_genji_fan(self):
        session, sent = self.make()
        genji = 0x02E0000000000029
        # At 10 m, the outer pellets clear the target and remain in flight after the centre hit.
        target_spawn = dataclasses.replace(MAP.spawns[1], position=(0.0, 0.0, 10.0))
        fan_map = dataclasses.replace(MAP, spawns=(MAP.spawns[0], target_spawn))
        handoffs = session.create_match(fan_map, [(1, "A", genji, 0, False), (2, "B", SOLDIER, 1, False)])
        peers = [Peer(session, handoff, sent, ("127.0.0.1", 47100 + index))
                 for index, handoff in enumerate(handoffs)]
        for peer in peers:
            peer.send(flags=SYN)
            peer.send()
            peer.message(21601, {})
        acknowledge_ownership(session, peers)
        session.tick(100.8)
        player, target = session.server.matches[0].players
        peers[0].message(21616, pick(player.entity, genji), now=100.8)
        peers[1].message(21616, pick(target.entity, SOLDIER), now=100.8)
        self.assertEqual(player.hero.guid, genji)
        fight = player.match.combat
        peers[0].commands(player.body_script.frame + 1, 16, 2, now=100.816)
        first_packet = len(sent)
        for tick in range(12):
            now = 100.832 + tick * 0.016
            session.tick(now)
            if tick == 0:
                self.assertEqual(len(fight.rockets), 3)
                self.assertTrue(all(rocket.flown > 0 for rocket in fight.rockets))
            for peer in peers:
                peer.drain()
                peer.send(now=now)
        self.assertEqual(fight.vitals[target.body].total, 171)
        self.assertEqual(player.body_script.component.entity_var(342).value(), 29)
        rockets = tuple(fight.rockets)
        self.assertEqual(len(rockets), 2)

        hits = {}
        for data, address in sent[first_packet:]:
            if peek_connection(data) != peers[0].handoff.conn:
                continue
            self.assertEqual(address, peers[0].address)
            packet = peers[0].cipher.open(data)
            self.assertIsNotNone(packet)
            if packet.kind != KIND_FRAME:
                continue
            reader = BitReader(packet.payload)
            reader.var_a()
            reliable, _ = messages.read_channel(reader)
            hits.update((seq, value) for seq, msg_id, value in reliable if msg_id == combat.HIT)
        hit, = hits.values()
        self.assertEqual(hit["+0x78"], {"+0x0": {"+0x0": target.body}, "+0x4": 29, "+0x8": 0})

        document = session.finish()
        replay = _ReplaySession(document, session.registry.resolve)
        result = replay.run()
        self.assertTrue(result["stored_state_verified"])
        copied_match = replay.server.matches[0]
        copied_player, copied_target = copied_match.players
        self.assertEqual(copied_match.combat.vitals[copied_target.body].total, 171)
        self.assertEqual(copied_player.body_script.component.entity_var(342).value(), 29)
        for fighter in (player, copied_player):
            weapon = next(i for i in fighter.body_script.component.instances.values() if i.graph.index == 0x0304)
            self.assertEqual(weapon.vars[53].value(), 27)
        copied = copied_match.combat.rockets
        self.assertEqual(len(copied), 2)
        for original, restored in zip(rockets, copied, strict=True):
            self.assertIs(restored.shooter, copied_player)
            self.assertEqual(restored.position, original.position)
            self.assertEqual(restored.direction, original.direction)
            self.assertEqual(restored.left, original.left)
            self.assertAlmostEqual(restored.direct.projectile_extent, .1)

    def test_unrecorded_hidden_queue_change_cannot_be_sealed_even_without_wire_effect(self):
        session, _ = self.make()
        session.create_match(MAP, [])
        session.server._next_conn += 1
        with self.assertRaisesRegex(ServerJournalError, "unrecorded stored"):
            session.finish()

    def test_stored_real_projectile_preserves_emission_body_identity(self):
        session, sent = self.make()
        (handoff,) = session.create_match(MAP, [(1, "Projectile", SOLDIER, 0, False)])
        peer = Peer(session, handoff, sent, ("127.0.0.1", 47002))
        peer.send(flags=SYN)
        peer.send()
        peer.message(21601, {})
        acknowledge_ownership(session, (peer,))
        session.tick(100.8)
        player = session.server.matches[0].players[0]
        peer.message(21616, pick(player.entity, SOLDIER), now=100.8)
        peer.commands(player.body_script.frame + 1, 1, 2)
        rocket, = player.match.combat.rockets
        self.assertEqual(rocket.source_entity, player.body)
        self.assertEqual(rocket.shot.body, player.body)
        document = session.finish()
        replay = _ReplaySession(document, session.registry.resolve)
        result = replay.run()
        self.assertTrue(result["stored_state_verified"])
        copied = replay.server.matches[0].combat.rockets
        self.assertEqual(len(copied), 1)
        self.assertEqual(copied[0].source_entity, rocket.source_entity)
        self.assertEqual(copied[0].shot.body, rocket.shot.body)

    def test_stored_baptiste_passive_charge_keeps_fractional_host_phase(self):
        hero = 0x02E0000000000221
        session, sent = self.make()
        (handoff,) = session.create_match(MAP, [(1, "Baptiste", hero, 0, False)])
        peer = Peer(session, handoff, sent, ("127.0.0.1", 47003))
        peer.send(flags=SYN)
        peer.send()
        peer.message(21601, {})
        acknowledge_ownership(session, (peer,))
        session.tick(100.8)
        player = session.server.matches[0].players[0]
        peer.message(21616, pick(player.entity, hero), now=100.8)
        script = player.body_script
        self.assertIsNone(script.ult_seconds)
        self.assertEqual(script.passive_charge_ms, 0)
        peer.commands(script.frame + 1, 62, 0)
        self.assertEqual(script.component.vars[342].value(), 0)
        self.assertEqual(script.passive_charge_ms, 992)
        peer.commands(script.frame + 1, 1, 0)
        self.assertEqual(script.component.vars[342].value(), 5)
        self.assertEqual(script.passive_charge_ms, 8)
        document = session.finish()
        replay = _ReplaySession(document, session.registry.resolve)
        result = replay.run()
        self.assertTrue(result["stored_state_verified"])
        copied = replay.server.matches[0].players[0].body_script
        self.assertIsNone(copied.ult_seconds)
        self.assertEqual(copied.passive_charge_ms, 8)
        self.assertEqual(copied.component.vars[342].value(), 5)

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
