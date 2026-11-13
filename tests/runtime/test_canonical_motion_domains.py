"""Explicit relocation floor and unequal-domain input admission, no native claim."""
import unittest
from test_canonical_knockback_174 import CanonicalKnockbackTests
from ow174.game import combat,correction
from ow174.game.commands import Command

class MotionDomainsTests(unittest.TestCase):
    def test_relocation_floor_rejects_old_record_and_selected_token_without_relabelling(self):
        fixture=CanonicalKnockbackTests()
        run,_,owner,_,_=fixture.scene(victim_birth=9000)
        last=owner.mover.history[-1]
        self.assertTrue(correction.request_motion(owner,first_frame=last.frame,body=owner.body))
        selected=correction.own_record(owner,run.frame)
        self.assertIsNotNone(selected)
        first=last.frame+1
        self.assertTrue(correction.request_motion(owner,first_frame=first,first_valid_frame=first,body=owner.body))
        self.assertEqual(owner.impulse_correction[0],last.frame) # normal merge unchanged
        self.assertIsNone(correction.own_record(owner,run.frame))
        self.assertEqual(owner.mover.history[-1],last)
        correction.sent(owner,selected)
        self.assertIsNone(owner.mover.corrected)
        self.assertEqual(correction.command_ack(owner,run.frame),owner.commands.last_frame)
        fixture.step(run,0,9000)
        selected=correction.own_record(owner,run.frame)
        self.assertEqual(selected.correction.frame,first)
        # Later ordinary overlapping force cannot lower the authored floor.
        self.assertTrue(correction.request_motion(owner,first_frame=last.frame,body=owner.body))
        self.assertEqual(owner.motion_correction_floor,first)
        run.match.tick=owner.motion_correction_deadline+1
        self.assertIsNone(correction.own_record(owner,run.match.tick))
        self.assertFalse(hasattr(owner,'motion_correction_floor'))

    def test_no_force_old_owner_epoch_retains_actual_forward_input(self):
        fixture=CanonicalKnockbackTests()
        run,_,owner,_,_=fixture.scene(victim_birth=200)
        fight=run.match.combat
        run.frame+=1;run.match.update(run.frame*combat.TICK,run.frame)
        command=Command(231,forward=127)
        self.assertEqual(fight.knockbacks.world_frame(owner,command.frame,combat.TICK),1031)
        admitted,blocked=fight.suppress_input(owner,command,tick=run.frame,active=False)
        self.assertIs(admitted,command);self.assertFalse(blocked)
        before=tuple(owner.mover.position)
        owner.take_commands([command])
        self.assertNotEqual(tuple(owner.mover.position),before)
        self.assertEqual(owner.commands.last_frame,231)
        self.assertEqual(fight.knockbacks.serial,0)

    def test_historical_lock_uses_world_time_without_relabelling_command(self):
        fixture=CanonicalKnockbackTests()
        run,_,owner,_,_=fixture.scene(victim_birth=200)
        fight=run.match.combat
        fight.record_input_lock(owner.body,'diagnostic-lock',None,world_tick=1029)
        admitted,blocked=fight.suppress_input(owner,Command(230,forward=127),tick=1032,active=False)
        self.assertTrue(blocked);self.assertEqual(admitted.forward,0);self.assertEqual(admitted.frame,230)
