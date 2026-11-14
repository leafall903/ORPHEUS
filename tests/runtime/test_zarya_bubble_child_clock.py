"""Personal bubble child clock after an actual encrypted unequal-epoch switch."""
import json
import unittest
from pathlib import Path

import test_server_snapshot as snapshot_tests
from test_game_combat import aim
from test_server_journal import pick

from ow174.game import barriers

CONTRACT = json.loads((Path(__file__).resolve().parents[2]
                       / 'fixtures/behavior/zarya-bubble-child-clock-v1.json').read_text())
HERO = int(CONTRACT['hero'], 16)


class ZaryaBubbleChildClockTests(unittest.TestCase):
    def test_self_bubble_child_advances_without_owner_input_then_retires(self):
        session, peers, players = snapshot_tests.ServerSnapshotTests().scene(two=True)
        player, enemy = players
        now = 101.1
        peers[0].commands(player.body_script.frame + 1, 40, 0, now=now)
        peers[0].message(21616, pick(player.entity, HERO), now=now)
        script = player.body_script
        birth = player.match.tick, script.frame
        self.evidence = {'birth': birth, 'sourceBody': player.body}
        self.assertEqual(script.world_frame_anchor, birth)
        self.assertNotEqual(*birth)
        for _ in range(8):
            now += .016
            session.tick(now)
            for peer in peers:
                peer.drain()
                peer.send(now=now)
        peers[0].commands(script.frame + 1, 40, 0, now=now)
        peers[1].commands(enemy.body_script.frame + 1, 40, 0, now=now)
        peers[0].commands(script.frame + 1, 1, CONTRACT['button'], now=now)
        fight = player.match.combat
        child = next(b for b in fight.barriers.scripted.values() if b.parent is player)
        component = child.component
        self.assertEqual(component.world.body_definition, 0x04000000000013F4)
        self.assertIs(child.source, script.component)
        self.assertEqual(child.parent_body, player.body)
        self.assertTrue(any(i.graph.index == 0x0256 for i in component.instances.values()))
        before = component.frame
        frozen = script.frame, script.component.now
        wanted = birth[0] + (before - birth[1]) * script.frame_ms // 16 + CONTRACT['child_frame_step']
        amount = wanted - session.server.tick
        self.assertGreater(amount, 0)
        now += amount * .016
        for peer in peers:
            peer.send(now=now - .001)
        session.tick(now, missed=amount - 1)
        for peer in peers:
            peer.drain()
            peer.send(now=now)
        self.assertEqual(fight.tick, wanted)
        self.assertIs(fight.barriers.scripted[child.entity], child)
        self.assertIs(child.component, component)
        self.evidence.update(childEntity=child.entity, childBefore=before, childAfter=component.frame,
                             expectedAfter=before + CONTRACT['child_frame_step'], worldTick=fight.tick,
                             frozenOwner=frozen, sourceBodyStillSame=child.parent_body == player.body)
        self.assertEqual((script.frame, script.component.now), frozen)
        self.assertEqual(component.frame, before + CONTRACT['child_frame_step'])
        self.assertEqual(component.now, (component.frame + 1) * component.frame_ms - 1)
        self.assertEqual(script.world_frame_anchor, birth)
        self.assertTrue(barriers._live(fight, child))
        self.assertEqual(child.vitals.total, CONTRACT['pool'])
        owner_health = fight.vitals[player.body].total
        angles = aim(fight._eye(enemy), fight._eye(player))
        peers[1].commands(enemy.body_script.frame + 1, 8, 1, yaw=angles[0], pitch=angles[1], now=now)
        self.assertLess(child.vitals.total, CONTRACT['pool'])
        self.evidence.update(poolBefore=CONTRACT['pool'], poolAfter=child.vitals.total)
        self.assertEqual(fight.vitals[player.body].total, owner_health)
        now += .016
        session.tick(now)
        for peer in peers:
            peer.drain()
            peer.send(now=now)
        self.assertIs(fight.barriers.scripted[child.entity], child)
        self.assertEqual((script.frame, script.component.now), frozen)
        expiry = fight.barriers.expiries[player.body, False]
        amount = expiry.tick + 2 - session.server.tick
        self.assertGreater(amount, 0)
        now += amount * .016
        for peer in peers:
            peer.send(now=now - .001)
        session.tick(now, missed=amount - 1)
        for peer in peers:
            peer.drain()
            peer.send(now=now)
        self.assertNotIn(child.entity, fight.barriers.scripted)
        self.assertTrue(child.retired)
        self.assertFalse(component.instances)
        self.evidence.update(retiredAt=fight.tick, originalExpiry=expiry.tick,
                             childRetired=child.retired, noRemainingChildInstances=not component.instances)
