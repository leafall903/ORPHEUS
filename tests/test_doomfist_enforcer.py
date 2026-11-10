"""Actual Enforcer13EB input against Doomfist under the declared manual controller policy."""

import os
import unittest
from contextlib import contextmanager
from unittest.mock import patch

import test_doomfist_pve as npc_tests
from test_doomfist_combat import DoomfistFixture
from test_doomfist_meteor import charge_by_neutral_commands
from test_game_combat import aim

from ow174.game import combat, npc, npc_control, stats


@unittest.skipUnless(os.environ.get('ORPHEUS_NPC_DATA_DIR'), 'requires actual five-body NPC bundle')
class EnforcerIncomingTests(DoomfistFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        npc_tests.ActualNpcDamageTests.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        npc_tests.ActualNpcDamageTests.tearDownClass.__func__(cls)

    create_enemy = npc_tests.ActualNpcDamageTests.create_enemy
    def wall(self, present):
        npc_tests.ActualNpcDamageTests.install_wall(self, present=present)

    def enemy(self, *, team=1):
        definition = next(d for d in self.catalog.values() if d.name == 'Enforcer')
        bot, _ = self.create_enemy(definition, team=team, distance=3)
        x, y, z = self.a.mover.position
        self.place_player(self.b, (x + 30, y, z))
        self.assertEqual(bot.body_script.component.world.controller_components, (0x4B,))
        self.a.mover.yaw, self.a.mover.pitch = aim(self.fight._eye(self.a),
                                                (bot.position[0], bot.position[1] + 1, bot.position[2]))
        own = self.fight.vitals_of(next(t for t in self.fight._targets() if t.owner is self.a))
        return bot, own

    @contextmanager
    def damage_events(self, bot):
        original = self.fight._damage
        events = []

        def observed(shooter, target, amount, *args, **kwargs):
            dealt = original(shooter, target, amount, *args, **kwargs)
            if shooter is bot and target.owner is self.a and dealt > 0:
                events.append({'tick': self.fight.tick, 'amount': amount, 'dealt': dealt})
            return dealt

        with patch.object(self.fight, '_damage', new=observed):
            yield events

    def test_actual_attack_stop_and_reengagement_damage_doomfist_without_query_overrides(self):
        bot, own = self.enemy()
        before = own.total
        with self.damage_events(bot) as events:
            self.assertEqual(npc_control.start(self.match, self.a), bot.entity)
            for _ in range(300):
                self.run.step({})
                self.assertFalse(self.fight.is_stunned(self.a))
        self.assertTrue(events)
        self.assertTrue(all(e['amount'] == 2 for e in events))
        self.assertEqual(before - own.total, sum(e['dealt'] for e in events))
        self.assertLess(own.total, before)
        self.assertTrue(bot.body_script.component.instances)
        self.assertEqual(npc_control.stop(self.match), 1)
        stopped = own.total
        self.settle(150)
        self.assertEqual(own.total, stopped)
        self.a.mover.yaw, self.a.mover.pitch = aim(self.fight._eye(self.a),
                                                (bot.position[0], bot.position[1] + 1, bot.position[2]))
        npc_control.start(self.match, self.a)
        self.settle(300)
        self.assertLess(own.total, stopped)

    def test_actual_enforcer_shots_consume_uppercut_shields_without_refreshing_decay(self):
        bot, own = self.enemy()
        x, y, z = self.a.mover.position
        self.b.team = 1
        self.place_player(self.b, (x + 1.5, y, z))
        yaw, pitch = aim(self.fight._eye(self.a), (x + 1.5, y + 1, z))
        self.run.step({self.a: (yaw, pitch, 8)})
        self.assertEqual(self.vitals().total, 150)
        self.assertEqual(own.temporary_shields, 30)
        deadline = own.temporary_decay_at
        self.place_player(self.a, (x, y, z))
        self.place_player(self.b, (x + 30, y, z))
        self.a.mover.yaw, self.a.mover.pitch = aim(
            self.fight._eye(self.a), (bot.position[0], bot.position[1] + 1, bot.position[2]))
        self.assertEqual(npc_control.start(self.match, self.a), bot.entity)

        absorbed = spilled = False
        with self.damage_events(bot) as events:
            for _ in range(900):
                shields = own.temporary_shields
                permanent = sum(own.now.values())
                before_tick = self.fight.now
                start = len(events)
                self.run.step({})
                shots = events[start:]
                if not shots:
                    continue
                self.assertEqual(own.temporary_decay_at, deadline)
                self.assertTrue(all(event['amount'] == 2 for event in shots))
                damage = sum(event['dealt'] for event in shots)
                decay = min(shields, 3 * max(0, self.fight.now - max(before_tick, deadline)))
                remaining = shields - decay
                self.assertAlmostEqual(own.temporary_shields, max(0, remaining - damage))
                self.assertAlmostEqual(permanent - sum(own.now.values()), max(0, damage - remaining))
                absorbed |= remaining > 0 and damage <= remaining
                spilled |= damage > remaining
                if absorbed and spilled:
                    break
        self.assertTrue(absorbed)
        self.assertTrue(spilled, (len(events), own.temporary_shields, own.total))
        npc_control.stop(self.match)

    def test_actual_wall_suppresses_ongoing_attack_then_removal_allows_damage(self):
        bot, own = self.enemy()
        npc_control.start(self.match, self.a)
        self.wall(True)
        before = own.total
        self.settle(300)
        self.assertEqual(own.total, before)
        self.assertFalse(bot.attack_input)
        self.wall(False)
        self.settle(300)
        self.assertLess(own.total, before)

    def test_actual_ally_refused_then_enemy_team_is_a_positive_control(self):
        bot, own = self.enemy(team=0)
        with self.assertRaises(ValueError):
            npc_control.start(self.match, self.a)
        self.settle(100)
        self.assertEqual(own.total, 250)
        bot.team = 1  # faction fixture only, not a health/input/service override
        self.a.mover.yaw, self.a.mover.pitch = aim(self.fight._eye(self.a),
                                                (bot.position[0], bot.position[1] + 1, bot.position[2]))
        npc_control.start(self.match, self.a)
        self.settle(300)
        self.assertLess(own.total, 250)

    def test_actual_punch_control_suppresses_new_incoming_damage_then_attack_resumes(self):
        bot, _ = self.enemy()
        with self.damage_events(bot) as events:
            npc_control.start(self.match, self.a)
            self.settle(120)
            self.assertTrue(events)
            for _ in range(40):
                self.run.step({self.a: (0, 0, 2)})
            self.run.step({})
            for _ in range(80):
                if self.fight.punches.controlled(bot.entity):
                    break
                self.run.step({})
            self.assertTrue(self.fight.punches.controlled(bot.entity))
            self.settle(8)  # allow already released projectiles to finish, not replay later
            count, checked = len(events), 0
            while self.fight.punches.controlled(bot.entity):
                self.run.step({})
                self.assertEqual(len(events), count)
                checked += 1
            self.assertGreater(checked, 0)
            self.settle(400)
            self.assertGreater(len(events), count)

    def test_actual_meteor_selection_rejects_real_enforcer_shots_then_incoming_resumes(self):
        bot, _ = self.enemy()
        charge_by_neutral_commands(self)
        self.a.mover.yaw, self.a.mover.pitch = aim(self.fight._eye(self.a),
                                                (bot.position[0], bot.position[1] + 1, bot.position[2]))
        npc_control.start(self.match, self.a)
        with self.damage_events(bot) as events:
            self.settle(120)
            self.assertTrue(events)  # same body/weapon is a real positive control
            self.run.step({self.a: (0, 0, 32)})
            self.assertTrue(self.fight.meteors.protected(self.a.body))
            count = len(events)
            with patch.object(self.fight, '_shoot', wraps=self.fight._shoot) as shots:
                self.settle(180)
                self.assertTrue(any(call.args[0] is bot for call in shots.call_args_list))
            self.assertEqual(len(events), count)
            self.assertTrue(self.fight.meteors.protected(self.a.body))
            self.run.step({self.a: (0, 0, 1)})
            self.settle(120)
            self.assertFalse(self.fight.meteors.protected(self.a.body))
            self.settle(400)
            self.assertGreater(len(events), count)

    def test_actual_hand_cannon_kills_enforcer_and_retained_corpse_stops_attacking(self):
        bot, _ = self.enemy()
        npc_control.start(self.match, self.a)
        with self.damage_events(bot) as events:
            for _ in range(800):
                if bot.dead:
                    break
                yaw, pitch = aim(self.fight._eye(self.a),
                                 (bot.position[0], bot.position[1] + 1, bot.position[2]))
                self.run.step({self.a: (yaw, pitch, 1)})
            self.assertTrue(bot.dead)
            self.assertIn(bot, self.match.bots)
            self.assertEqual(self.fight.vitals[bot.entity].killer, self.a.body)
            self.assertFalse(any(who is bot for _, who in self.fight.respawns))
            self.settle(10)
            count = len(events)
            self.settle(100)
            self.assertEqual(len(events), count)
        self.assertEqual(npc.remove_spawned(self.match), (bot.entity,))
        self.assertNotIn(bot, self.match.bots)

    def test_actual_sustained_fire_kills_doomfist_then_normal_deadline_restores_fresh_body(self):
        bot, own = self.enemy()
        old_body = self.a.body
        npc_control.start(self.match, self.a)
        previous, changed = own.total, self.run.frame
        for frame in range(24000):
            if self.fight.is_dead(self.a):
                break
            self.run.step({})
            if own.total != previous:
                previous, changed = own.total, self.run.frame
            self.assertLess(self.run.frame - changed, 2001)
            if frame % 256 == 0:
                # These FakeClient lists only retain already delivered/acked
                # observations. Preserve all server state, inputs and resources.
                for player in self.run.players:
                    player.client.entities.clear()
                    player.client.reliable.clear()
        self.assertTrue(self.fight.is_dead(self.a))
        self.assertEqual(own.total, 0)
        self.assertEqual(own.killer, bot.entity)
        self.assertEqual(self.a.stats.value(stats.DEATHS), 1)
        self.assertFalse(bot.dead)
        npc_control.stop(self.match)
        deadline = next(when for when, player in self.fight.respawns if player is self.a)
        self.assertAlmostEqual(deadline - own.died_at, 10)
        while (self.run.frame + 1) * combat.TICK < deadline:
            self.run.step({})
            self.assertEqual(self.a.body, old_body)
        for _ in range(2):
            self.run.step({})
            if self.a.body != old_body:
                break
        self.assertNotEqual(self.a.body, old_body)
        self.assertFalse(self.fight.is_dead(self.a))
        self.assertGreaterEqual(self.fight.now + 1e-8, deadline)
        self.assertLessEqual(self.fight.now - deadline, combat.TICK + 1e-8)
        self.assertNotIn(old_body, self.fight.vitals)
        fresh = self.fight.vitals_of(next(t for t in self.fight._targets() if t.owner is self.a))
        self.assertEqual(fresh.total, 250)
        self.assertEqual(fresh.temporary_shields, 0)
        weapon = next(i for i in self.a.body_script.component.instances.values() if i.graph.index == 0xD0A)
        self.assertEqual(weapon.vars[53].value(), 4)
