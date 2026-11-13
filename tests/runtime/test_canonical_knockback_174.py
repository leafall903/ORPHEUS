"""Actual0043 contact and finite owner delivery; no native-client PASS claim."""
import unittest
from unittest.mock import patch

import test_dva_movement_correction as dva_fixture
import test_game_projectiles as delivery
import test_game_repeated_lifecycles as wire
from test_game_combat import Run
from test_game_packet_budget import decoded

from ow174.game import combat, correction, defenses, mccree
from ow174.game.collision import flat_world
from ow174.game.commands import Command
from ow174.game.content import find_hero, heroes
from ow174.game.script import expr


class CanonicalKnockbackTests(unittest.TestCase):
    def test_finite_lease_storage_preserves_deadline_and_bound_mover_identity(self):
        from test_server_snapshot import ServerSnapshotTests
        from ow174.game.server_snapshot import snapshot_digest

        fixture=ServerSnapshotTests()
        session,_,players=fixture.scene(two=True)
        owner,other=players
        first=(owner.commands.last_frame or owner.match.tick)+1
        self.assertTrue(correction.request_motion(owner,first_frame=first))
        capture=lambda:snapshot_digest(fixture.capture(session))
        original=capture()
        self.assertEqual(capture(),original)
        owner.motion_correction_deadline+=1
        self.assertNotEqual(capture(),original)
        owner.motion_correction_deadline-=1
        binding=owner.motion_correction_target
        owner.motion_correction_target=(binding[0],other.mover,binding[2])
        self.assertNotEqual(capture(),original)
        owner.motion_correction_target=binding
        self.assertEqual(capture(),original)

    def scene(self, *, source_offset=0, victim_birth=1000, npc=False):
        run=Run(names=('Cassidy','Ashe'))
        match=run.match
        source,victim=run.players
        source.commands.reset(last_frame=run.frame+source_offset)
        match.switch_hero(source,heroes()[mccree.HERO])
        victim.commands.reset(last_frame=victim_birth)
        match.switch_hero(victim,find_hero('Ashe'))
        match.collision_world=flat_world(0)
        source.mover.position=[1000.,0.,1000.]
        victim.mover.position=[1000.,0.,1002. if not npc else 1030.]
        for player in run.players:
            player.mover.body=None
            player.body_script.create_arrived=True
        target=match.bots[0] if npc else victim
        if npc:
            target.position=(1000.,0.,1002.)
            target.route=()
            target.firing=False
            target.team=1
        npcs=tuple(bot.entity for bot in match.bots)
        self.assertEqual(len(npcs),15)
        for _ in range(30):self.step(run,source_offset,victim_birth)
        return run,source,victim,target,npcs

    def step(self,run,source_offset,victim_birth,buttons=0):
        run.frame+=1
        run.match.update(run.frame*combat.TICK,run.frame)
        run.players[0].take_commands([Command(run.frame+source_offset,buttons=buttons)])
        run.players[1].take_commands([Command(victim_birth+run.frame-1000)])

    def test_actual_cassidy_melee_hits_player_and_npc_with_one_original_force(self):
        for npc,offset in ((False,0),(False,4000),(True,0)):
            with self.subTest(npc=npc,source_offset=offset):
                run,source,victim,target,npcs=self.scene(source_offset=offset,npc=npc)
                fight=run.match.combat
                selected=next(t for t in fight._targets() if t.owner is target)
                before=fight.vitals_of(selected).total
                position=tuple(target.position if npc else target.mover.position)
                contacts=[]
                submit=fight._submit_attack
                def capture(shooter,frame,payload):
                    if shooter is source and isinstance(payload,combat.MeleeContact):
                        contacts.append((frame,getattr(payload,'impact_tick',None)))
                    return submit(shooter,frame,payload)
                with patch.object(fight,'_submit_attack',side_effect=capture):
                    self.step(run,offset,1000,0x800)
                    for _ in range(40):self.step(run,offset,1000)
                self.assertEqual(fight.vitals_of(selected).total,before-30)
                self.assertEqual(fight.knockbacks.serial,1)
                self.assertEqual(len(contacts),1)
                self.assertEqual(contacts[0][0]-offset,contacts[0][1])
                hits=source.client.messages(combat.HIT)
                self.assertEqual(len(hits),1)
                self.assertEqual(hits[0]['+0x84'],contacts[0][0])
                after=target.position if npc else target.mover.position
                self.assertGreater(after[2],position[2])
                self.assertEqual(source.mover.position[::2],[1000.,1000.])
                self.assertEqual(tuple(bot.entity for bot in run.match.bots),npcs)

    def test_explicit_world_impact_maps_to_recipient_next_command_not_packet_domain(self):
        for birth in (100,9000):
            with self.subTest(victim_birth=birth):
                run,source,victim,target,_=self.scene(victim_birth=birth)
                fight=run.match.combat
                selected=next(t for t in fight._targets() if t.owner is target)
                at=run.frame
                self.assertTrue(fight.knockbacks.apply(selected,expr.Vec3(0,0,8),
                    impact_tick=at,source_entity=source.body))
                self.assertEqual([c.frame for c in victim.mover.velocity_changes],[birth+31])
                self.step(run,0,birth)
                self.assertGreater(victim.mover.position[2],1002.)
                if birth==9000:
                    own=correction.own_record(victim,run.frame)
                    self.assertIsNotNone(own)
                    self.assertEqual(own.movement.frame,birth+31)
                    self.assertTrue(correction.wrapped_record(own,run.frame))
                    self.assertEqual(run.match.tick,at+1)  # no global packet-clock rewrite
                deadline=victim.motion_correction_deadline
                run.match.tick=deadline+1
                self.assertFalse(correction.correcting(victim))
                self.assertIsNone(victim.impulse_correction)

    def test_actual_0043_live_variables_and_recipient_passive_are_applied_once(self):
        run,source,victim,_,npcs=self.scene()
        run.match.switch_hero(victim,find_hero('Reinhardt'))
        victim.mover.position=[1000.,0.,1002.]
        victim.mover.body=None
        for _ in range(10):self.step(run,0,1000)
        self.assertAlmostEqual(defenses.knockback_scale(victim),.7,places=6)
        fight=run.match.combat
        original=combat.knockback.quick_melee_ring
        captures=[]
        def configure(state):
            self.assertEqual((state.instance.graph.index,state.node.pos),(0x0043,10))
            state.instance.write((expr.INSTANCE,1257),4.)
            state.instance.write((expr.INSTANCE,1258),2.)
            ring=original(state)
            captures.append(ring)
            return ring
        with patch.object(combat.knockback,'quick_melee_ring',side_effect=configure), \
                patch.object(fight.knockbacks,'apply',wraps=fight.knockbacks.apply) as calls:
            self.step(run,0,1000,0x800)
            for _ in range(20):self.step(run,0,1000)
        self.assertEqual(captures,[(100.,4.,2.)])
        receiver=[call for call in calls.call_args_list if call.args[0].owner is victim]
        self.assertEqual(len(receiver),1)
        for actual,expected in zip(receiver[0].args[1],(0.,1.4,2.8),strict=True):
            self.assertAlmostEqual(actual,expected,places=6)
        self.assertEqual(fight.knockbacks.serial,1)
        self.assertEqual(tuple(bot.entity for bot in run.match.bots),npcs)

    def test_finite_lease_waits_for_create_ack_and_retires_replaced_body(self):
        run,source,victim,target,_=self.scene()
        selected=next(t for t in run.match.combat._targets() if t.owner is target)
        self.assertTrue(run.match.combat.knockbacks.apply(selected,expr.Vec3(0,0,8),
            impact_tick=run.frame,source_entity=source.body))
        self.step(run,0,1000)
        victim.body_script.create_arrived=False
        self.assertIsNone(correction.own_record(victim,run.frame))
        victim.body_script.create_arrived=True
        update=correction.own_record(victim,run.frame)
        self.assertIsNotNone(update)
        self.assertIsNone(victim.mover.corrected)
        old_mover=victim.mover
        run.match.switch_hero(victim,find_hero('Winston'))
        correction.sent(victim,update)
        self.assertIsNone(old_mover.corrected)
        self.assertFalse(correction._eligible(victim))
        self.assertIsNone(victim.impulse_correction)

    def test_controlled_dva_force_uses_the_actual_control_mover(self):
        fixture=dva_fixture.DvaMovementCorrectionTests()
        fixture.setUpClass()
        _,pilot,mech=fixture.scene()
        fight=pilot.match.combat
        selected=next(t for t in fight._targets() if t.owner is mech)
        mover=mech.control_mover
        position=tuple(mover.position)
        self.assertTrue(fight.knockbacks.apply(selected,expr.Vec3(0,0,8),
            impact_tick=1070,source_entity=0xA000FF01))
        self.assertIsNone(mech.impulse_mover)
        self.assertIs(fight.knockbacks.active[mech.entity],mover)
        self.assertEqual([c.frame for c in mover.velocity_changes],[1071])
        pilot.match.update(1071*combat.TICK,1071)
        pilot.take_commands([Command(1071)])
        self.assertGreater(mover.position[2],position[2])
        update=correction.own_record(pilot,1071)
        self.assertIsNotNone(update)
        self.assertEqual(update.entity,mech.entity)
        self.assertIs(update.correction.mover,mover)
        with patch.object(mech,'dead',True):
            self.assertFalse(fight.knockbacks.apply(selected,expr.Vec3(0,0,8),impact_tick=1071))
        self.assertIsNone(mech.impulse_mover)

    def test_actual_contact_survives_failed_send_and_lost_packet_on_authenticated_wire(self):
        self.contact_wire()

    def test_future_owner_contact_survives_failed_send_and_lost_packet_on_authenticated_wire(self):
        self.contact_wire(owner_offset=8000)

    def contact_wire(self, *, owner_offset=0):
        fixture=delivery.ObserverProjectileDeliveryTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        source,owner=fixture.shooter,fixture.viewer
        match,fight=fixture.match,fixture.fight
        match.switch_hero(source,heroes()[mccree.HERO])
        owner.commands.reset(last_frame=fixture.tick+owner_offset)
        match.switch_hero(owner,find_hero('Ashe'))
        match.collision_world=flat_world(0)
        source.mover.position=[1000.,0.,1000.]
        owner.mover.position=[1000.,0.,1002.]
        source.mover.body=owner.mover.body=None
        source.body_script.create_arrived=True
        def step(buttons=0):
            fixture.advance(1)
            source.take_commands([Command(fixture.tick,buttons=buttons)])
            owner.take_commands([Command(fixture.tick+owner_offset)])
        for _ in range(12):
            for _ in range(3):step()
            fixture.send();fixture.ack()
        self.assertTrue(owner.body_script.create_arrived)
        self.assertFalse(correction._eligible(owner))
        start=tuple(owner.mover.position)
        step(0x800)
        for _ in range(20):
            if fight.knockbacks.serial:break
            step()
        self.assertEqual(fight.knockbacks.serial,1)
        step()  # the first recipient command after physical impact consumes once
        self.assertGreater(owner.mover.position[2],start[2])
        payloads={}
        original=fixture.client._payload
        def capture(tick,reliable,unreliable,entities):
            payload=original(tick,reliable,unreliable,entities)
            payloads[payload]=tuple(entities)
            return payload
        with patch.object(fixture.client,'_payload',side_effect=capture):
            previous=len(fixture.server.sock.sent)
            fixture.server.sock.fail_next=True
            fixture.client.send_frame(fixture.tick,fixture.tick*combat.TICK)
            self.assertEqual(len(fixture.server.sock.sent),previous)
            self.assertIsNone(owner.mover.corrected)
            step()
            fixture.send(lose=True)
            packet,tick,_,_,ack,_=decoded(self,fixture.cipher,fixture.server.sock.sent[-1][0])
            selected=payloads[packet.payload]
            updates=[u for u in selected if u.entity==owner.body and u.correction is not None]
            self.assertEqual(len(updates),1)
            own=updates[0]
            self.assertGreater(own.movement.position[2],start[2])
            self.assertEqual(own.movement.frame,tick+owner_offset)
            self.assertEqual(tick,fixture.tick)  # outer world clock remains unchanged
            self.assertLessEqual(ack,own.movement.frame+correction.ACK_LEAD)
            self.assertEqual(owner.mover.corrected,own.movement.frame)
            decoded_movements=[]
            movement_reader=wire.read_movement
            def observe_movement(reader,frame,**kwargs):
                value=movement_reader(reader,frame,**kwargs)
                decoded_movements.append(value)
                return value
            with patch.object(wire,'read_movement',side_effect=observe_movement):
                _,actual=wire.wire_frame(packet)
            self.assertEqual([u.entity for u,_ in actual],[u.entity for u in selected])
            self.assertFalse(any(u.op==combat.world.OP_CREATE for u,_ in actual))
            movement_entities=[u.entity for u in selected if u.movement is not None]
            self.assertEqual(len(decoded_movements),len(movement_entities))
            own_decoded=dict(zip(movement_entities,decoded_movements,strict=True))[owner.body]
            self.assertEqual(own_decoded['frame'],own.correction.frame)
            self.assertEqual(own_decoded['position'],own.movement.position)
            self.assertEqual(own_decoded['velocity'],own.movement.velocity)
            # A later success carries a newer authoritative endpoint after the
            # first datagram was deliberately withheld from the receive window.
            step();fixture.send();fixture.ack()
            packet2,*_=decoded(self,fixture.cipher,fixture.server.sock.sent[-1][0])
            later=[u for u in payloads[packet2.payload] if u.entity==owner.body and u.correction is not None]
            self.assertEqual(len(later),1)
            self.assertGreater(later[0].movement.frame,own.movement.frame)
        self.assertEqual(fight.knockbacks.serial,1)
        self.assertEqual(len(source.client.messages(combat.HIT)),1)
