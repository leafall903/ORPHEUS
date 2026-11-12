"""Actual raw unequal-epoch Cryo expiry and strict causal storage regression."""
import unittest
import test_server_snapshot as snapshot_tests
from test_server_journal import pick
from ow174.game import mei, combat
from ow174.game.content import SOLDIER
from ow174.game.body_vm_snapshot import export_body_vm, snapshot_digest as vm_digest
from ow174.game.server_snapshot import snapshot_digest

class MeiCryoEpochTests(unittest.TestCase):
    def test_actual_raw_birth_expiry_replace_and_strict_storage(self):
        case=snapshot_tests.ServerSnapshotTests();session,peers,players=case.scene(two=True);player,enemy=players
        now=101.1
        old=player.body_script
        peers[0].commands(old.frame+1,40,0,now=now)
        expected_pair=(player.match.tick,old.frame)
        peers[0].message(21616,pick(player.entity,mei.HERO),now=now)
        assert player.body_script is not old
        script=player.body_script
        assert script.cryo_birth==expected_pair
        assert script.cryo_birth[0]!=script.cryo_birth[1],script.cryo_birth
        for _ in range(8):
         now+=.016;session.tick(now)
         for peer in peers:peer.drain();peer.send(now=now)
        peers[0].commands(script.frame+1,40,0,now=now)
        cast_frame=script.frame+1
        peers[0].commands(cast_frame,1,8,now=now)
        instance=mei.cryo_instance(player);state=instance.states[35]
        assert state.active
        fight=player.match.combat;target=fight.player_target(player)
        assert target is not None and fight._has_protection(target,combat.DAMAGE_IMMUNITY_STATES)
        assert fight._invulnerable(player)
        assert script.world_frame_anchor is None
        pair=script.cryo_birth
        assert script.frame_ms==16
        assert instance.states[6].evaluate(instance.states[6].node.field("m_EB0FC261"),watch=False)==4
        deadline=expected_pair[0]+cast_frame-expected_pair[1]+250
        assert mei.cryo_world_deadline(player)==deadline
        print('RAW active pair',pair,'world',fight.tick,'body',script.frame,'deadline',deadline,'state',state.start)
        vm=vm_digest(export_body_vm(script));server=snapshot_digest(case.capture(session))
        script.cryo_birth=(pair[0]+1,pair[1])
        assert mei.cryo_world_deadline(player)==deadline+1
        assert vm_digest(export_body_vm(script))!=vm
        assert snapshot_digest(case.capture(session))!=server
        script.cryo_birth=pair
        assert vm_digest(export_body_vm(script))==vm
        assert snapshot_digest(case.capture(session))==server
        frozen=(script.frame,script.component.now)
        def to_tick(wanted):
         nonlocal now
         amount=wanted-session.server.tick
         assert amount>0,(wanted,session.server.tick)
         now+=amount*.016
         # Keep UDP alive while omitting all body commands.
         for peer in peers:peer.send(now=now-.001)
         session.tick(now,missed=amount-1)
         for peer in peers:peer.drain();peer.send(now=now)
         assert fight.tick==wanted,(fight.tick,wanted)
        to_tick(deadline-1)
        assert fight._has_protection(target,combat.DAMAGE_IMMUNITY_STATES)
        assert fight._invulnerable(player)
        to_tick(deadline)
        assert (script.frame,script.component.now)==frozen
        assert state.active
        assert not fight._has_protection(target,combat.DAMAGE_IMMUNITY_STATES)
        assert not fight._invulnerable(player)
        v=fight.vitals_of(target);before=v.total
        assert fight._damage(enemy,target,130,False,0x0B900000000001ED,fight.tick,award_ult=False,source_scaled=True)
        assert before-v.total==130,(before,v.total)
        assert (script.frame,script.component.now)==frozen
        assert script.world_frame_anchor is None
        vm_digest(export_body_vm(script));snapshot_digest(case.capture(session))
        print('RAW expiry accepted actual130 while frozen',frozen,'state35 still active; no input lease')
        old_pair=script.cryo_birth
        peers[0].message(21616,pick(player.entity,SOLDIER),now=now)
        peers[0].message(21616,pick(player.entity,mei.HERO),now=now)
        replacement=player.body_script
        assert replacement is not script and script.retired
        assert script.cryo_birth==old_pair
        assert replacement.cryo_birth!=(old_pair)
        assert replacement.cryo_birth[0]==player.match.tick
        vm_digest(export_body_vm(replacement));snapshot_digest(case.capture(session))
        print('RAW replacement new pair',replacement.cryo_birth,'old pair unchanged',old_pair)
