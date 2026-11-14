"""Actual encrypted personal-bubble hit preserves values across private VMs."""
import json
import unittest
from pathlib import Path
from unittest.mock import patch

import test_server_snapshot as snapshots
from test_game_combat import aim
from test_server_journal import pick

from ow174.game.script import expr, runtime

CONTRACT = json.loads((Path(__file__).resolve().parents[2]
                       / 'fixtures/behavior/zarya-bubble-relay-entity-v1.json').read_text())


class ZaryaBubbleRelayEntityTests(unittest.TestCase):
    def test_actual_hit_keeps_attacker_value_with_private_vm_records(self):
        messages = []
        original_send = runtime.send_instance_message

        def send(instance, message, params, sender, **kwargs):
            if instance.graph.index == 0x0242 and message == 0x0240000000000146:
                messages.append((params[0x12], params[0x2A], sender))
            return original_send(instance, message, params, sender, **kwargs)

        with patch.object(runtime, 'send_instance_message', send):
            session, peers, players = snapshots.ServerSnapshotTests().scene(two=True)
            player, enemy = players
            now = 101.1
            peers[0].commands(player.body_script.frame + 1, 40, 0, now=now)
            peers[0].message(21616, pick(player.entity, 0x02E0000000000068), now=now)
            script = player.body_script
            birth = player.match.tick, script.frame
            for _ in range(8):
                now += .016
                session.tick(now)
                for peer in peers:
                    peer.drain()
                    peer.send(now=now)
            peers[0].commands(script.frame + 1, 40, 0, now=now)
            peers[1].commands(enemy.body_script.frame + 1, 40, 0, now=now)
            peers[0].commands(script.frame + 1, 1, 8, now=now)
            fight = player.match.combat
            child = next(b for b in fight.barriers.scripted.values() if b.parent is player)
            before = child.component.frame
            wanted = birth[0] + (before - birth[1]) * script.frame_ms // 16 + 1
            amount = wanted - session.server.tick
            now += amount * .016
            for peer in peers:
                peer.send(now=now - .001)
            session.tick(now, missed=amount - 1)
            for peer in peers:
                peer.drain()
                peer.send(now=now)
            health, pool = fight.vitals[player.body].total, child.vitals.total
            angles = aim(fight._eye(enemy), fight._eye(player))
            peers[1].commands(enemy.body_script.frame + 1, 8, 1, yaw=angles[0], pitch=angles[1], now=now)
            now += .016
            session.tick(now)
            for peer in peers:
                peer.drain()
                peer.send(now=now)
        source_instance = next(i for i in script.component.instances.values()
                               if i.graph.index == 0x0242)
        child_instance = next(i for i in child.component.instances.values()
                              if i.graph.index == 0x0256)
        source_attacker = source_instance.vars[392].base
        child_attacker = child_instance.vars[392].base
        self.assertIs(type(source_attacker), expr.Entity)
        self.assertIs(type(child_attacker), expr.Entity)
        self.assertEqual(source_attacker.id, enemy.body)
        self.assertEqual(child_attacker.id, enemy.body)
        self.assertIsNot(source_attacker, child_attacker)
        self.assertIsNot(vars(source_attacker), vars(child_attacker))
        self.assertEqual(pool, CONTRACT['poolBefore'])
        self.assertEqual(child.vitals.total, CONTRACT['poolAfter'])
        self.assertEqual(health, CONTRACT['protectedHP'])
        self.assertEqual(fight.vitals[player.body].total, health)
        self.assertEqual(len(messages), CONTRACT['energyMessages'])
        self.assertEqual([amount for _, amount, _ in messages], CONTRACT['energyAmounts'])
        self.assertEqual(sum(amount for _, amount, _ in messages), pool - child.vitals.total)
        self.assertTrue(all(attacker.id == enemy.body and sender == child.entity
                            for attacker, _, sender in messages))
        self.assertIs(source_attacker, messages[-1][0])
        self.assertEqual(source_instance.vars[499].base, messages[-1][1])
        snapshots.ServerSnapshotTests.capture(session)
        self.evidence = {'source': player.body, 'child': child.entity,
                         'attacker': source_attacker.id, 'wrappersSeparated': True,
                         'fieldDictionariesSeparated': True, 'poolBefore': pool,
                         'poolAfter': child.vitals.total, 'protectedHP': health,
                         'energyAmounts': [amount for _, amount, _ in messages],
                         'energySenders': [sender for _, _, sender in messages],
                         'strictExportPassed': True, 'worldTick': fight.tick}
