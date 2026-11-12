"""Normal D.Va selection and actual Hanzo bow against the owned Matrix."""

import os
import unittest
from pathlib import Path
from unittest.mock import patch

import test_server_journal as journal_tests
from test_game_combat import Run, aim
from test_game_hanzo_npc import NpcFixtureClient

from ow174.game import combat, dva as dva_owned, matrix, mover
from ow174.game.body_vm_snapshot import export_component_vm, snapshot_digest
from ow174.game.collision import flat_world
from ow174.game.commands import Command
from ow174.game.content import SOLDIER, heroes
from ow174.game.link import SYN
from ow174.game.server_journal import _ReplaySession
from ow174.game.server_snapshot import export_server_state
from ow174.game.script import expr, graph
from ow174.game.script.driver import ULT_CHARGE
from ow174.game.mccree import HERO as MCCREE

HANZO = 0x02E0000000000005


class UnpreparedDvaTests(unittest.TestCase):
    def test_normal_pilot_entry_has_no_owned_scope_without_mech_inputs(self):
        run = Run()
        pilot, = run.players
        with patch.object(dva_owned, "available", return_value=False):
            self.assertTrue(run.match.switch_hero(pilot, heroes()[matrix.DVA]))
        self.assertIsNone(pilot.controlled_mech)
        root = next(i for i in pilot.body_script.component.instances.values() if i.graph.index == 0x026A)
        self.assertFalse(root.states.get(11) and root.states[11].active)


class HanzoOwnedMechTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tags = os.environ.get("ORPHEUS_MATRIX_TAGS")
        if not tags or graph.GRAPH_SUPPLEMENT_PATH is None or mover.MOVERS_SUPPLEMENT_PATH is None:
            raise unittest.SkipTest("prepared 0407 graph, mover and projectile inputs required")
        cls.admitted = matrix.load_admitted_definitions(Path(tags))

    def arena(self, distance=9, *, protection=False):
        names = ("Pilot", "Hanzo", "McCree") if protection else ("Pilot", "Hanzo")
        run = Run(names=names)
        match = run.match
        match.bots.clear()
        match._pending_bot_scripts.clear()
        match.collision_world = flat_world(0)
        pilot, hanzo, *attackers = run.players
        for player in run.players:
            player.client = NpcFixtureClient(player)
        pilot.spawn = ((0, 0, 0), 0)
        hanzo.spawn = ((0, 0, distance), 180)
        if protection:
            shooter, = attackers
            pilot.team = hanzo.team
            shooter.team = 1 - hanzo.team
            pilot.spawn = ((0.8, 0, 4), 180)
            shooter.spawn = ((0, 0, 0), 0)
            self.assertTrue(match.switch_hero(shooter, heroes()[MCCREE]))
        self.assertTrue(match.switch_hero(pilot, heroes()[matrix.DVA]))
        self.assertTrue(match.switch_hero(hanzo, heroes()[HANZO]))
        mech = pilot.controlled_mech
        self.assertIsNotNone(mech)
        self.assertEqual((pilot.hero.body, mech.body), (0x040000000000040A, matrix.MECH))
        self.assertIs(mech.control_owner, pilot)
        root = next(instance for instance in pilot.body_script.component.instances.values()
                    if instance.graph.index == 0x026A)
        self.assertEqual(root.vars[38].value(), expr.Entity(mech.entity))
        self.assertTrue(root.states[11].active)
        self.assertNotEqual(pilot.body, mech.entity)
        fight = match.combat
        fight.matrix_definitions = self.admitted
        self.assertNotIn(pilot.body, {target.entity for target in fight._targets()})
        matrix_vm = next(instance for instance in mech.body_script.component.instances.values()
                         if instance.graph.index == 0x02CC)
        for _ in range(80):
            run.step({})
        return run, pilot, mech, hanzo, root, matrix_vm

    def draw(self, run, pilot, mech, hanzo, *, shield=0, facing=0):
        fight = run.match.combat
        x, y, z = mech.position
        angles = aim(fight._eye(hanzo), (x, y + 1.1, z))
        for _ in range(70):
            run.step({pilot: (facing, 0, shield), hanzo: (*angles, 1)})
        return angles

    def test_matrix_absorbs_frontal_bow_and_allows_rear_or_inactive_hit(self):
        for shield, facing, expected in ((2, 0, 600), (0, 0, 480), (2, -32768, 480)):
            with self.subTest(shield=shield, facing=facing):
                run, pilot, mech, hanzo, _, matrix_vm = self.arena()
                angles = self.draw(run, pilot, mech, hanzo, shield=shield, facing=facing)
                self.assertEqual(bool(matrix_vm.states.get(3) and matrix_vm.states[3].active), bool(shield))
                self.assertFalse(pilot.body_script.component.held)
                run.step({pilot: (facing, 0, shield), hanzo: (*angles, 0)})
                arrow, = run.match.combat.rockets
                for _ in range(30):
                    run.step({pilot: (facing, 0, shield)})
                fight = run.match.combat
                self.assertNotIn(arrow, fight.rockets)
                self.assertEqual(fight.vitals[mech.entity].total, expected)
                self.assertEqual([hit["+0x78"]["+0x4"] for hit in hanzo.client.messages(combat.HIT)],
                                 [] if expected == 600 else [120])

    def test_lethal_bow_returns_to_same_pilot_and_switch_removes_old_mech(self):
        run, pilot, mech, hanzo, root, matrix_vm = self.arena()
        fight = run.match.combat
        pilot_body = pilot.body
        fight.vitals[mech.entity] = combat.Vitals(mech.entity, 1)
        angles = self.draw(run, pilot, mech, hanzo)
        with patch.object(run.match, "queue_body_message", wraps=run.match.queue_body_message) as queued:
            run.step({hanzo: (*angles, 0)})
            for _ in range(30):
                run.step({})
        self.assertTrue(mech.dead)
        self.assertTrue(any(call.args[0] == pilot_body and call.args[1] == dva_owned.RETURN_TO_PILOT
                            and call.args[3] == mech.entity for call in queued.call_args_list))
        self.assertIsNone(pilot.controlled_mech)
        self.assertEqual(pilot.body, pilot_body)
        self.assertFalse(root.states[11].active)
        self.assertFalse(bool(matrix_vm.states.get(3) and matrix_vm.states[3].active))
        target = next(target for target in fight._targets() if target.entity == pilot_body)
        self.assertEqual(fight.vitals_of(target).total, 150)
        self.assertTrue(run.match.switch_hero(pilot, heroes()[SOLDIER]))
        self.assertNotIn(mech, run.match.bots)
        self.assertNotIn(mech.entity, fight.vitals)

    def test_activating_matrix_during_flight_stops_bow(self):
        run, pilot, mech, hanzo, _, matrix_vm = self.arena(distance=17)
        fight = run.match.combat
        angles = self.draw(run, pilot, mech, hanzo)
        run.step({hanzo: (*angles, 0)})
        arrow, = fight.rockets
        for _ in range(2):
            run.step({})
        self.assertIn(arrow, fight.rockets)
        run.step({pilot: (0, 0, 2)})
        self.assertTrue(matrix_vm.states[3].active)
        self.assertEqual(len(snapshot_digest(export_component_vm(mech.body_script.component))), 64)
        for _ in range(30):
            run.step({pilot: (0, 0, 2)})
        self.assertNotIn(arrow, fight.rockets)
        self.assertEqual(fight.vitals[mech.entity].total, 600)
        self.assertFalse(hanzo.client.messages(combat.HIT))

    def test_owned_matrix_exhausts_and_rearms_on_same_body(self):
        run, pilot, mech, hanzo, _, matrix_vm = self.arena()
        fight = run.match.combat
        for initial, hold, expected in ((True, True, 600), (False, True, 480), (True, False, 480)):
            with self.subTest(initial=initial, hold=hold):
                if not hold:
                    for _ in range(135):
                        run.step({})
                angles = self.draw(run, pilot, mech, hanzo, shield=2 if hold else 0)
                if not hold:
                    run.step({pilot: (0, 0, 2), hanzo: (*angles, 1)})
                self.assertEqual(matrix_vm.states[3].active, initial)
                run.step({pilot: (0, 0, 2), hanzo: (*angles, 0)})
                for _ in range(30):
                    run.step({pilot: (0, 0, 2)})
                self.assertEqual(fight.vitals[mech.entity].total, expected)
                self.assertIs(pilot.controlled_mech, mech)

    def test_owned_matrix_protects_hanzo_from_enemy_mccree(self):
        for shield, health in ((2, 200), (0, 130)):
            with self.subTest(shield=shield):
                run, pilot, mech, hanzo, _, matrix_vm = self.arena(distance=8, protection=True)
                shooter = run.players[-1]
                fight = run.match.combat
                x, y, z = hanzo.mover.position
                angles = aim(fight._eye(shooter), (x, y + 1, z))
                run.step({pilot: (-32768, 0, shield)})
                with patch.object(fight, "_hitscan", wraps=fight._hitscan) as ray:
                    run.step({pilot: (-32768, 0, shield), shooter: (*angles, 1)})
                    for _ in range(30):
                        run.step({pilot: (-32768, 0, shield)})
                ray.assert_called_once()
                self.assertEqual(bool(matrix_vm.states.get(3) and matrix_vm.states[3].active), bool(shield))
                self.assertEqual(fight.vitals[hanzo.body].total, health)
                self.assertEqual(fight.vitals[mech.entity].total, 600)

    def test_owned_matrix_contact_respects_input_gap_and_energy_deadline(self):
        for held, gap, health in ((1, 10, 200), (1, 40, 130), (120, 10, 130)):
            with self.subTest(held=held, gap=gap):
                run, pilot, mech, hanzo, _, matrix_vm = self.arena(distance=8, protection=True)
                shooter = run.players[-1]
                fight = run.match.combat
                x, y, z = hanzo.mover.position
                yaw, pitch = aim(fight._eye(shooter), (x, y + 1, z))
                for _ in range(held):
                    run.step({pilot: (-32768, 0, 2)})
                self.assertTrue(matrix_vm.states[3].active)
                with patch.object(fight, "_hitscan", wraps=fight._hitscan) as ray:
                    for _ in range(gap):
                        run.frame += 1
                        run.match.update(run.frame * combat.TICK, run.frame, frames=False)
                    shooter.take_commands([Command(run.frame, yaw=yaw, pitch=pitch, buttons=1)])
                    for _ in range(5):
                        run.frame += 1
                        run.match.update(run.frame * combat.TICK, run.frame, frames=False)
                        shooter.take_commands([Command(run.frame, yaw=yaw, pitch=pitch)])
                ray.assert_called_once()
                self.assertTrue(matrix_vm.states[3].active)
                self.assertEqual(fight.vitals[hanzo.body].total, health)
                self.assertEqual(fight.vitals[mech.entity].total, 600)

    def test_raw_pick_matrix_and_bow_replay_with_same_owned_body(self):
        from dataclasses import replace

        base = journal_tests.MAP
        arena = replace(base, free_for_all=False, spawns=(
            base.spawns[0], replace(base.spawns[1], position=(0, 0, 9)),
        ))
        session, sent = journal_tests.ServerJournalTests().make()
        session.apply("configure_startup_projectile_absorption", {"definitions": tuple(sorted(self.admitted))})
        handoffs = session.create_match(arena, [(1, "Pilot", matrix.DVA, 0, False),
                                                (2, "Hanzo", HANZO, 1, False)])
        peers = [journal_tests.Peer(session, handoff, sent, ("127.0.0.1", 47620 + index))
                 for index, handoff in enumerate(handoffs)]
        for peer in peers:
            peer.send(flags=SYN)
            peer.send()
            peer.message(21601, {})
        journal_tests.acknowledge_ownership(session, peers)
        now = 100.8
        session.tick(now)
        match = session.server.matches[0]
        pilot, hanzo = match.players
        for peer, player, hero in zip(peers, match.players, (matrix.DVA, HANZO), strict=True):
            peer.message(21616, journal_tests.pick(player.entity, hero), now=now)
        mech = pilot.controlled_mech
        self.assertIsNotNone(mech)
        self.assertNotEqual(mech.entity, pilot.body)
        peers[0].commands(mech.body_script.frame + 1, 80, 0, now=now)
        fight = match.combat
        x, y, z = mech.position
        yaw, pitch = aim(fight._eye(hanzo), (x, y + 1.1, z))
        peers[1].commands(hanzo.body_script.frame + 1, 70, 1, yaw=yaw, pitch=pitch, now=now)
        peers[0].commands(mech.body_script.frame + 1, 1, 2, now=now)
        matrix_vm = next(instance for instance in mech.body_script.component.instances.values()
                         if instance.graph.index == 0x02CC)
        self.assertTrue(matrix_vm.states[3].active)
        export_server_state(session.server, archives=session._matches.values(),
                            collision_provider=session._collision_reads)
        peers[1].commands(hanzo.body_script.frame + 1, 1, 0, yaw=yaw, pitch=pitch, now=now)
        self.assertEqual(len(fight.rockets), 1)
        for _ in range(12):
            now += combat.TICK
            session.tick(now)
        self.assertFalse(fight.rockets)
        self.assertEqual(fight.vitals[mech.entity].total, 600)

        # A fresh primary after releasing Matrix must execute in the same owned VM.
        hx, hy, hz = hanzo.mover.position
        x, y, z = mech.position
        yaw, pitch = aim((x, y + combat.eye_height(mech.body), z), (hx, hy + 0.85, hz))
        peers[0].commands(mech.body_script.frame + 1, 70, 0, yaw=yaw, pitch=pitch, now=now)
        self.assertFalse(matrix_vm.states[3].active)
        peers[0].commands(mech.body_script.frame + 1, 1, 1, yaw=yaw, pitch=pitch, now=now)
        peers[0].commands(mech.body_script.frame + 1, 20, 0, yaw=yaw, pitch=pitch, now=now)
        damaged = fight.vitals[hanzo.body].total
        charge = mech.body_script.component.entity_var(ULT_CHARGE).value()
        self.assertLess(damaged, 200)
        self.assertAlmostEqual(charge, 200 - damaged, delta=1e-5)

        replay = _ReplaySession(session.finish(), session.registry.resolve)
        replay.run()
        copied_match = replay.server.matches[0]
        copied_mech = copied_match.players[0].controlled_mech
        self.assertEqual(copied_mech.entity, mech.entity)
        self.assertIs(copied_mech.control_owner, copied_match.players[0])
        self.assertEqual(copied_match.combat.vitals[copied_mech.entity].total, 600)
        self.assertEqual(copied_match.combat.vitals[copied_match.players[1].body].total, damaged)
        self.assertEqual(copied_mech.body_script.component.entity_var(ULT_CHARGE).value(), charge)
        self.assertFalse(copied_match.combat.rockets)
