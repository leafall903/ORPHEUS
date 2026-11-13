"""Actual skill inputs against read-only canonical r2 plus private producer overlay.

No native-client acceptance or duplicated shared transport tests.
"""
import unittest
from unittest.mock import patch

from test_game_combat import KANEZAKA_DM, Run
from ow174.game import combat, correction
from ow174.game.commands import Command
from ow174.game.content import SOLDIER, heroes

EVIDENCE = {}


def setup():
    run = Run(KANEZAKA_DM, ('Reinhardt', 'Tracer', 'Soldier'))
    owner, a, b = run.players
    for player, epoch, hero in ((owner, 9000, combat.REINHARDT),
                                (a, 6000, 0x02E0000000000003), (b, 3000, SOLDIER)):
        player.commands.last_frame = epoch
        run.match.switch_hero(player, heroes()[hero])
    origin = tuple(owner.mover.position)
    for player, x in ((a, 100.), (b, -100.)):
        player.mover.position[:] = (origin[0]+x, origin[1], origin[2])
        player.mover.body = None
    return run, owner, a, b, origin


def step(run, offset, button=0, recipient_forward=0):
    tick = 1000+offset
    run.match.update(tick*.016, tick)
    snapshot = None
    for player, epoch in zip(run.players, (9000, 6000, 3000)):
        player.take_commands([Command(epoch+offset, buttons=button if player is run.players[0] else 0,
                                     forward=recipient_forward if player is run.players[1] else 0)])
        if player is run.players[0]:
            mover = run.players[1].mover
            if mover.history:
                pose = mover.history[-1]
                snapshot = dict(frame=pose.frame, snapshot_position=list(pose.position),
                                mover_position=list(mover.position))
    return snapshot


def force_capture(fight, events, owner):
    original = fight.knockbacks.apply
    def observe(target, impulse, **kwargs):
        result = original(target, impulse, **kwargs)
        queue = target.owner.mover.velocity_changes
        events.append(dict(target=target.entity, world_tick=fight.tick, source_owner_frame=owner.body_script.component.frame,
                           recipient_frame=target.owner.body_script.component.frame,
                           impulse=list(impulse), kwargs=kwargs, accepted=result,
                           queued_frame=queue[-1].frame if queue and result else None))
        return result
    return patch.object(fight.knockbacks, 'apply', observe)


class ReinhardtR2ProducerTests(unittest.TestCase):
    def assert_world_request(self, row, owner):
        self.assertTrue(row['accepted'])
        self.assertEqual(row['kwargs'].get('impact_tick'), row['world_tick'])
        self.assertEqual(row['kwargs'].get('source_entity'), owner.body)
        self.assertNotIn('impact_frame', row['kwargs'])
        self.assertLess(row['queued_frame'], 7000)
        self.assertGreater(row['queued_frame'], row['recipient_frame'])

    def test_hammer_once_preserves_damage_and_recipient_clock(self):
        run, owner, target, other, origin = setup()
        fight = run.match.combat
        events = []
        target.mover.position[:] = (origin[0], origin[1], origin[2]+3.)
        with force_capture(fight, events, owner):
            for offset in range(1, 71):
                step(run, offset, 1 if offset >= 31 else 0)
        own = [row for row in events if row['target'] == target.body]
        hp = fight.vitals[target.body].total
        EVIDENCE['hammer'] = dict(events=own, health=hp, npc_count=len(run.match.bots),
                                  source_body=owner.body, recipient_body=target.body)
        self.assertEqual(hp, 65.)
        self.assertEqual(len(own), 1)
        self.assert_world_request(own[0], owner)
        hits = [h for h in owner.client.messages(combat.HIT) if h['+0x78']['+0x0']['+0x0'] == target.body]
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]['+0x84'], 9057)

    def test_charge_capture_bump_and_release_each_use_their_authored_force_once(self):
        run, owner, captured, side, origin = setup()
        fight = run.match.combat
        events = []
        pin_lease = acquisition_snapshot = None
        with force_capture(fight, events, owner):
            for offset in range(1, 311):
                if offset == 82:
                    feet = owner.mover.position
                    captured.mover.position[:] = (feet[0], feet[1], feet[2]+1.)
                    side.mover.position[:] = (feet[0]+1.1, feet[1], feet[2]+1.)
                    captured.mover.body = side.mover.body = None
                    side.mover.velocity = (0., 20., 0.)
                snapshot = step(run, offset, 8 if offset == 31 else 0)
                if offset == 82:
                    acquisition_snapshot = snapshot
                if offset == 230:
                    charge = fight.charges.get(owner.body)
                    pin_lease = dict(world_tick=fight.tick,
                                     pinned=charge is not None and charge.pinned == captured.body,
                                     window=correction._motion_window(captured),
                                     deadline=getattr(captured, 'motion_correction_deadline', None))
        capture_rows = [row for row in events if row['target'] == captured.body]
        side_rows = [row for row in events if row['target'] == side.body]
        EVIDENCE['charge'] = dict(capture_release=capture_rows, bump=side_rows,
                                  captured_hp=fight.vitals[captured.body].total,
                                  side_hp=fight.vitals[side.body].total, npc_count=len(run.match.bots),
                                  charge_retired=owner.body not in fight.charges, pin_lease=pin_lease,
                                  acquisition_snapshot=acquisition_snapshot)
        self.assertTrue(pin_lease['pinned'])
        self.assertIsNotNone(pin_lease['window'], 'pin outlives the initial force lease; transport must renew its own finite lease')
        self.assertLess(acquisition_snapshot['frame'], 7000)
        self.assertEqual(acquisition_snapshot['snapshot_position'], acquisition_snapshot['mover_position'])
        self.assertEqual(fight.vitals[captured.body].total, 150.)
        self.assertEqual(fight.vitals[side.body].total, 150.)
        self.assertEqual(len(capture_rows), 2)  #65 acquisition,49 natural release
        self.assertEqual(len(side_rows), 1)  #15 graze, one50HP hit
        for row in capture_rows+side_rows:
            # Soldier's recipient clock starts at3000; Tracer at6000.
            self.assert_world_request(row, owner)
        self.assertAlmostEqual(capture_rows[0]['impulse'][2], 1.)
        self.assertEqual(capture_rows[0]['impulse'][1], 10.)
        self.assertEqual(side_rows[0]['impulse'][1], 2.)
        self.assertAlmostEqual(capture_rows[1]['impulse'][2], 10.)
        self.assertTrue(owner.body not in fight.charges)
        self.assertFalse(fight.is_stunned(captured))
        self.assertFalse(captured.mover.airborne)
        before = captured.mover.position[2]
        for offset in range(311, 319):
            step(run, offset, recipient_forward=127)
        self.assertGreater(captured.mover.position[2], before)
        EVIDENCE['charge']['landing_control_return'] = dict(grounded=True, stunned=False,
                                                          forward_delta=captured.mover.position[2]-before)


if __name__ == '__main__':
    unittest.main()
