"""Reported Orisa lifecycle regressions through real commands and wire writers.

These checks establish server behavior. Original-client visibility remains separate.
"""

import math
import unittest
from unittest.mock import patch

import test_orisa_barrier as barrier_fixture
import test_orisa_combat as combat_fixture
import test_orisa_supercharger as supercharger_fixture
import test_server_journal as journal_fixture
from test_game_projectiles import bits_of, read_shot

from ow174.game import combat, orisa_presentation, projectiles, world
from ow174.game.commands import FIRE
from ow174.game.script import expr, graph


class OrisaReportedLifecycleTests(unittest.TestCase):
    def test_encrypted_driver_command_delivers_damage_hit_and_target_health(self):
        fixture = journal_fixture.ServerJournalTests()
        session, sent = fixture.make()
        peers, players = fixture.connected(session, sent, first_hero=combat_fixture.ORISA)
        owner, enemy = players
        fight = owner.match.combat
        target = next(t for t in fight._targets() if t.owner is enemy)
        vitals = fight.vitals_of(target)
        before = vitals.total
        first_packet = len(sent)
        now = 100.95
        yaw, pitch = journal_fixture.aim(fight._eye(owner), fight._eye(enemy))
        for index in range(24):
            peers[0].commands(owner.body_script.frame + 1, 1, FIRE if index == 0 else 0,
                              yaw=yaw, pitch=pitch, now=now)
            peers[1].commands(enemy.body_script.frame + 1, 1, 0, now=now)
            now += combat.TICK
            session.tick(now)
            for peer in peers:
                peer.drain()
                peer.send(now=now)
        self.assertEqual(before - vitals.total, 22)
        notices = peers[0].hit_notices(first_packet)
        self.assertEqual(len(notices), 1)
        self.assertEqual(notices[0]["+0x78"]["+0x4"], 22)
        self.assertEqual(notices[0]["+0x78"]["+0x0"]["+0x0"], enemy.body)
        for peer in peers:
            health = peer.health_updates(first_packet, enemy.body)
            self.assertTrue(any(current == vitals.total for _, _, current in health))
        self.assertFalse(fight.rockets)
        self.assertFalse(fight.projectiles.observed)

    def test_driver_hit_updates_health_feedback_and_both_projectile_deliveries(self):
        run, owner, enemy = combat_fixture.OrisaCombatTests().arena()
        fight = run.match.combat
        target = combat_fixture.OrisaCombatTests().target(run, enemy)
        vitals = fight.vitals_of(target)
        run.step({owner: (0, 0, FIRE)})
        rocket, = fight.rockets
        shot = rocket.shot
        self.assertEqual(len(shot.observers), 2)
        self.assertIsNone(fight.projectiles.lease_id(owner, rocket.definition, run.frame))
        for delivery in shot.observers:
            reader = bits_of(delivery.launch.ch2)
            self.assertEqual((reader.bit(), reader.bit(), reader.var_b()), (1, 1, shot.frame))
            record = read_shot(reader)
            self.assertEqual(record["ids"], list(delivery.record.ids))
            self.assertEqual((record["instance"], record["state"]), (shot.instance, shot.state))
        for _ in range(12):
            run.step({})
        self.assertEqual(vitals.total, 178)
        self.assertEqual(len(owner.client.messages(combat.HIT)), 1)
        self.assertTrue(all(d.finished for d in shot.observers))
        for delivery in shot.observers:
            self.assertTrue(delivery.launch_arrival.received)
            self.assertTrue(any(u.entity == delivery.record.ids[0] and u.op == world.OP_DESTROY
                                for u in delivery.client.entities))
        self.assertEqual(fight.projectiles.observed, [])
        self.assertEqual(fight.body_health(enemy.body), (vitals.total, vitals.maximum))

    def test_missed_driver_round_expires_without_hit_or_damage(self):
        run, owner, enemy = combat_fixture.OrisaCombatTests().arena()
        x, y, z = owner.mover.position
        barrier_fixture.BarrierTests.place(enemy, (x + 20, y, z + 5))
        run.step({owner: (0, 0, FIRE)})
        rocket, = run.match.combat.rockets
        shot = rocket.shot
        timeout = math.ceil(rocket.left / combat.TICK) + projectiles.DELIVERY_GRACE_TICKS + 2
        for _ in range(timeout):
            run.step({})
            if rocket not in run.match.combat.rockets and not run.match.combat.projectiles.observed:
                break
        self.assertNotIn(rocket, run.match.combat.rockets)
        self.assertFalse(owner.client.messages(combat.HIT))
        target = combat_fixture.OrisaCombatTests().target(run, enemy)
        self.assertEqual(run.match.combat.vitals_of(target).total, 200)
        self.assertTrue(all(d.finished for d in shot.observers))

    def test_supercharger_uses_its_prediction_lease_and_publishes_device_once(self):
        helper = supercharger_fixture.SuperchargerTests()
        run, owner, enemy, manager, device = helper.deploy()
        run.step({})
        self.assertIsNotNone(run.match.combat.projectiles.lease_id(
            owner, 0x0400000000001CCB, run.frame))
        self.assertIs(orisa_presentation.projection(run.match.combat, device.entity).component,
                      device.component)
        for viewer in (owner, enemy):
            creates = [u for u in viewer.client.entities
                       if u.entity == device.entity and u.op == world.OP_CREATE]
            self.assertEqual(len(creates), 1)
            self.assertGreater(creates[0].build(0).count, 0)
            with patch.object(world, "create", return_value=None) as build:
                creates[0].build(0)
            self.assertEqual(build.call_args.args[2][51], world.health(200, 200)[51])
        self.assertEqual(device.script.cached_source_entity, device.source_body)
        self.assertEqual(device.script.find_var(graph.INSTANCE, 27).value(), expr.Entity(device.source_body))
        self.assertEqual(device.script.find_var(graph.INSTANCE, 40).value(), expr.Entity(device.source_body))
        manager.remove(device.entity)
        run.step({})
        for viewer in (owner, enemy):
            self.assertTrue(any(u.entity == device.entity and u.op == world.OP_DESTROY
                                for u in viewer.client.entities))

    def test_source_switch_destroys_each_authoritative_flight_without_damage(self):
        from ow174.game.content import find_hero

        for button in (1, 2, 16):
            with self.subTest(button=button):
                run, owner, enemy = combat_fixture.OrisaCombatTests().arena()
                x, y, z = owner.mover.position
                barrier_fixture.BarrierTests.place(enemy, (x + 20, y, z + 5))
                for index in range(40):
                    run.step({owner: (0, 0, button if index == 0 else 0)})
                    if run.match.combat.rockets:
                        break
                rocket, = run.match.combat.rockets
                shot = rocket.shot
                run.step({})
                run.match.switch_hero(owner, find_hero("Soldier"))
                for _ in range(projectiles.DELIVERY_GRACE_TICKS + 2):
                    run.step({})
                self.assertNotIn(rocket, run.match.combat.rockets)
                for delivery in shot.observers:
                    self.assertTrue(any(u.entity == delivery.record.ids[0] and u.op == world.OP_DESTROY
                                        for u in delivery.client.entities))
