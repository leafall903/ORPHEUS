"""Actual grenade expiry crossed by one delayed authoritative healing orb."""

import math
import unittest
from typing import ClassVar
from unittest.mock import patch

from test_game_combat import Run, aim

from ow174.game import combat, moira, world
from ow174.game.commands import Command
from ow174.game.content import find_hero


class MoiraOrbStatusClockTests(unittest.TestCase):
    observations: ClassVar[list] = []

    def sample(self, batch, *, anti=True):
        run = Run(names=('Ana', 'Moira', 'Recipient'))
        match = run.match
        match.bots.clear()
        match._pending_bot_scripts.clear()
        ana, owner, recipient = run.players
        self.assertTrue(match.switch_hero(ana, find_hero('ana')))
        self.assertTrue(match.switch_hero(owner, find_hero('moira')))
        owner.body_script.component.set(12918, 0)
        owner.team = recipient.team = 0
        ana.team = 1
        x, y, z = owner.mover.position
        recipient.mover.position = [x, y, z + 6]
        ana.mover.position = [x + 3, y, z + 6]
        for player in run.players:
            player.mover.body = None
        for _ in range(20):
            run.step({})
        fight = match.combat
        target = next(t for t in fight._targets() if t.owner is recipient)
        health = fight.vitals_of(target)
        if anti:
            centre = (target.feet[0], target.feet[1] + target.height / 2, target.feet[2])
            direction = aim(fight._eye(ana), centre)
            run.step({ana: (*direction, 16)})
            for _ in range(60):
                run.step({})
            self.assertEqual(fight.healing.factor(target), 0)
            begins = fight.healing.filter_history[recipient.body][0][0]
            until = begins + math.ceil(fight.healing.spec.duration / combat.TICK)
            item = fight.healing.applied[ana.body, recipient.body]
            self.assertEqual(item.component.next_event_time(), until * item.component.frame_ms)
        else:
            until = 1276  # Same input epoch as the real-grenade case, without a status.
        while run.frame < until - 16:
            run.step({})
        health.damage(health.total - 40)
        fight._sync_body_health(recipient, health)
        start = run.frame
        casts = []
        original = moira.orb_fired

        def fired(*args, **kwargs):
            accepted = original(*args, **kwargs)
            if accepted:
                casts.append(fight.moira_orbs[-1])
            return accepted

        with patch.object(moira, 'orb_fired', side_effect=fired):
            for end in range(start + batch, start + 33, batch):
                match.update(end * combat.TICK, end)
                commands = [
                    Command(frame, buttons=16 if frame == start + 1 else 1 if frame <= start + 8 else 0)
                    for frame in range(end - batch + 1, end + 1)]
                owner.take_commands(commands)
                owner.take_commands(commands)
            match.update((start + 33) * combat.TICK, start + 33)
        self.assertEqual(len(casts), 1)
        orb = casts[0]
        result = {'batch': batch, 'anti': anti, 'event': orb.shot.frame, 'health': health.total,
                  'energy': orb.energy, 'flown': orb.flown, 'position': orb.position}
        for tick in range(start + 34, start + 510):
            match.update(tick * combat.TICK, tick)
            if orb not in fight.moira_orbs:
                result['retired'] = tick
                break
        self.assertNotIn(orb, fight.moira_orbs)
        for tick in range(fight.tick + 1, fight.tick + 4):
            match.update(tick * combat.TICK, tick)
        for delivery in orb.shot.observers:
            self.assertTrue(delivery.finished)
            self.assertEqual(sum(u.entity == delivery.record.ids[0] and u.op == world.OP_DESTROY
                                 for u in delivery.client.entities), 1)
        self.observations.append(result)
        return result, run, recipient

    def compare(self, *, anti):
        first, _, _ = self.sample(1, anti=anti)
        delayed, run, recipient = self.sample(32, anti=anti)
        for row in (first, delayed):
            self.assertEqual(row['event'], 1267)
            # The real graph expires at1276: eighteen pulses through1293.
            self.assertAlmostEqual(row['health'], 58.72 if anti else 66, places=6)
            self.assertAlmostEqual(row['energy'], 281.28 if anti else 274, places=6)
            self.assertAlmostEqual(row['flown'], 0.416, places=6)
        self.assertEqual(first['position'], delayed['position'])
        self.assertEqual(first['retired'], delayed['retired'])
        return run, recipient

    def test_real_antiheal_expiry_uses_each_orb_slice_world_tick(self):
        run, recipient = self.compare(anti=True)
        self.assertNotIn(recipient.body, run.match.combat.healing.filter_history)

    def test_unmodified_healing_and_lifecycle_without_antiheal(self):
        self.compare(anti=False)

if __name__ == '__main__':
    unittest.main()
