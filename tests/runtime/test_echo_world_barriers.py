"""Actual copied Winston creation/host/finite world surface; native hosting/filters remain partial."""
import dataclasses
import unittest
from dataclasses import replace
from unittest.mock import patch

import test_echo_combat as echoes
import test_echo_movement as movement
import test_mccree_flashbang as flash
import test_server_snapshot as snapshots
from test_game_combat import aim, in_world
from test_game_mover import box, wall

from ow174.game.collision import World
from ow174.game.content import SOLDIER, heroes
from ow174.game.mover import Mover, mover_data
from ow174.game.script import expr, runtime
from ow174.game.world_bubbles import FIRST, SPAN

WINSTON = 0x02E0000000000009


class EchoWorldBarrierTests(unittest.TestCase):
    def session(self, distance=8):
        run,caster,enemy,_,angles = echoes.EchoPrimaryTests().session()
        run.match.game_map = replace(run.match.game_map,free_for_all=False)
        run.match.switch_hero(enemy,heroes()[WINSTON],preserve_pose=True)
        caster.body_script.set_ult_charge(caster.body_script.component.entity_var(338).value())
        run.step({caster:(*angles,32)})
        self.assertEqual(caster.hero.guid,WINSTON)
        self.assertTrue(run.match.combat.echo_duplicates.active(caster))
        run.match.switch_hero(enemy,heroes()[echoes.ECHO],preserve_pose=True)
        x,y,z = caster.mover.position
        enemy.mover = Mover((x,y,z+distance),world=run.match.collision_world,data=mover_data(enemy.hero.body))
        enemy.body_script.ult_seconds = 0
        enemy.body_script.set_ult_charge(0)
        for _ in range(25):
            run.step({})
        target = next(t for t in run.match.combat._targets() if t.owner is caster)
        x, y, z = target.feet
        point = (x, y + .8, z)
        attack = aim(run.match.combat._eye(enemy),point)
        instance = next(i for i in caster.body_script.component.instances.values() if i.graph.index == 0xFA)
        return run,caster,enemy,target,instance,attack

    def cast(self,run,caster):
        run.step({caster:(0,0,16)})
        self.assertFalse(run.match.combat.barriers.world.actors)
        for _ in range(10):
            run.step({})
        self.assertEqual(len(run.match.combat.barriers.world.actors),1)
        return next(iter(run.match.combat.barriers.world.actors.values()))

    @staticmethod
    def fire(run,actor,angles,button=1,frames=21):
        run.step({actor:(*angles,button)})
        for _ in range(frames):
            controls = {actor:(*angles,button)} if button in (2,16) else {}
            run.step(controls)

    def test_actual_create_output_play_parameters_and_native_timer_protect_from_outside(self):
        run,caster,enemy,target,instance,attack = self.session()
        actor = self.cast(run,caster)
        self.assertEqual(instance.vars[38].value(),expr.Entity(actor.entity))
        self.assertNotIn(38,dict(caster.body_script.replicated_vars(instance)))
        hosted = next(i for i in actor.component.instances.values() if i.graph.index == 0xFC)
        self.assertEqual(expr.to_float(hosted.vars[587].value()),700)
        self.assertEqual(expr.to_float(hosted.vars[567].value()),9)
        self.assertTrue(hosted.states[0].active)
        self.assertTrue(hosted.states[11].active)
        self.assertEqual(actor.health,700)
        self.fire(run,enemy,attack)
        self.assertEqual(actor.health,649)
        self.assertEqual(run.match.combat.vitals_of(target).total,500)
        self.assertEqual(enemy.body_script.component.entity_var(342).value(),0)

    def test_origin_inside_hits_body_without_crossing_the_shell(self):
        run,caster,enemy,target,_,attack = self.session(distance=2)
        actor = self.cast(run,caster)
        self.fire(run,enemy,attack)
        self.assertEqual(actor.health,700)
        self.assertEqual(run.match.combat.vitals_of(target).total,464)
        self.assertEqual(enemy.body_script.component.entity_var(342).value(),36)

    def test_bubble_stays_at_creation_position_when_caster_moves_out(self):
        run,caster,enemy,target,_,_ = self.session()
        actor = self.cast(run,caster)
        centre = actor.point
        for _ in range(150):
            movement.EchoMovementTests.step(run,caster,forward=127,yaw=16384)
        self.assertEqual(actor.point,centre)
        self.assertGreater(caster.mover.position[0]-centre[0],10)
        attack = aim(run.match.combat._eye(enemy),run.match.combat._eye(caster))
        self.fire(run,enemy,attack,frames=40)
        self.assertEqual(actor.health,700)
        self.assertLess(run.match.combat.vitals_of(target).total,500)

    def test_native_timer_expires_and_cooldown_rejects_then_fresh_cast_reuses_new_identity(self):
        run,caster,enemy,target,instance,attack = self.session()
        first = self.cast(run,caster)
        hosted = next(i for i in first.component.instances.values() if i.graph.index == 0xFC)
        timer = hosted.states[11]
        for _ in range(570):
            run.step({})
        self.assertTrue(timer.reached)
        self.assertFalse(run.match.combat.barriers.world.actors)
        self.assertGreater(instance.vars[61].value(),0)
        run.step({caster:(0,0,16)})
        for _ in range(10):
            run.step({})
        self.assertFalse(run.match.combat.barriers.world.actors)
        self.fire(run,enemy,attack)
        self.assertEqual(run.match.combat.vitals_of(target).total,464)
        while instance.vars[61].value() > 0:
            run.step({})
        second = self.cast(run,caster)
        self.assertNotEqual(first.entity,second.entity)
        self.assertEqual(second.health,700)

    def test_real_primary_breaks_finite_pool_and_only_next_volley_damages_body(self):
        run,caster,enemy,target,_,attack = self.session()
        actor = self.cast(run,caster)
        for number in range(14):
            if number == 12:
                for _ in range(110):
                    run.step({})
            self.fire(run,enemy,attack)
        self.assertEqual(actor.health,0)
        self.assertNotIn(actor.entity,run.match.combat.barriers.world.actors)
        self.assertEqual(run.match.combat.vitals_of(target).total,500)
        self.fire(run,enemy,attack)
        self.assertEqual(run.match.combat.vitals_of(target).total,464)

    def test_real_wall_blocks_before_world_bubble(self):
        run,caster,enemy,target,_,attack = self.session()
        actor = self.cast(run,caster)
        x,y,z = caster.mover.position
        collision = World(box(x-20,x+20,y-1,y,z-20,z+20)
                          + wall(x-10,z+6,x+10,z+6,y0=y,y1=y+6))
        run.match.collision_world = collision
        self.fire(run,enemy,attack)
        self.assertEqual(actor.health,700)
        self.assertEqual(run.match.combat.vitals_of(target).total,500)

    def test_friendly_primary_passes_shell_to_enemy_outside(self):
        run,caster,enemy,_,_,_ = self.session()
        ally = run.match.add_player(4,'Allied Echo',echoes.ECHO,caster.team,False)
        in_world(run.match,ally)
        run.match.switch_hero(ally,heroes()[echoes.ECHO])
        run.players.append(ally)
        ally.mover = Mover(tuple(caster.mover.position),world=run.match.collision_world,
                           data=mover_data(ally.hero.body))
        for _ in range(25):
            run.step({})
        actor = self.cast(run,caster)
        x, y, z = enemy.mover.position
        attack = aim(run.match.combat._eye(ally), (x, y + .8, z))
        self.fire(run,ally,attack)
        target = next(t for t in run.match.combat._targets() if t.owner is enemy)
        self.assertEqual(actor.health,700)
        self.assertEqual(run.match.combat.vitals_of(target).total,149)

    def test_real_stickies_debit_contact_and_blast_once_per_actor(self):
        run,caster,enemy,target,_,attack = self.session()
        actor = self.cast(run,caster)
        self.fire(run,enemy,attack,button=2,frames=145)
        self.assertEqual(actor.health,520)
        self.assertEqual(run.match.combat.vitals_of(target).total,500)

    def test_real_beam_hits_world_pool(self):
        run,caster,enemy,target,_,attack = self.session()
        actor = self.cast(run,caster)
        self.fire(run,enemy,attack,button=16,frames=25)
        self.assertLess(actor.health,700)
        self.assertGreater(actor.health,600)
        self.assertEqual(run.match.combat.vitals_of(target).total,500)

    def add_ally(self,run,caster,slot,offset,hero=SOLDIER):
        actor = run.match.add_player(slot,f'Ally{slot}',hero,caster.team,False)
        in_world(run.match,actor)
        run.match.switch_hero(actor,heroes()[hero])
        run.players.append(actor)
        point = tuple(x+d for x,d in zip(caster.mover.position,offset,strict=True))
        actor.mover = Mover(point,world=run.match.collision_world,data=mover_data(actor.hero.body))
        return actor

    def test_blast_protects_multiple_bodies_without_multiplying_pool_loss(self):
        run,caster,enemy,_,_,attack = self.session()
        allies = [self.add_ally(run,caster,3,(-.3,0,3.8)),self.add_ally(run,caster,4,(.3,0,4.1))]
        for _ in range(25):
            run.step({})
        actor = self.cast(run,caster)
        self.fire(run,enemy,attack,button=2,frames=145)
        self.assertEqual(actor.health,520)
        targets = {t.owner:t for t in run.match.combat._targets()}
        self.assertEqual([run.match.combat.vitals_of(targets[a]).total for a in allies],[200,200])

    def test_declared_melee_filter_bypasses_world_shell(self):
        run,caster,enemy,_,_,_ = self.session(distance=5.5)
        ally = self.add_ally(run,caster,3,(0,0,4.1))
        for _ in range(25):
            run.step({})
        actor = self.cast(run,caster)
        target = next(t for t in run.match.combat._targets() if t.owner is ally)
        point = (target.feet[0],target.feet[1]+1,target.feet[2])
        attack = aim(run.match.combat._eye(enemy),point)
        self.fire(run,enemy,attack,button=0x800,frames=25)
        self.assertEqual(actor.health,700)
        self.assertEqual(run.match.combat.vitals_of(target).total,170)

    def test_native_copy_expiry_retires_world_actor_before_its_own_deadline(self):
        run,caster,_,_,_,_ = self.session()
        copies = run.match.combat.echo_duplicates
        while copies.states[caster.entity].expires-run.match.combat.now > .3:
            run.step({})
        actor = self.cast(run,caster)
        for _ in range(40):
            run.step({})
            if caster.hero.guid == echoes.ECHO:
                break
        self.assertEqual(caster.hero.guid,echoes.ECHO)
        self.assertNotIn(actor.entity,run.match.combat.barriers.world.actors)
        self.assertFalse(actor.component.instances)

    def test_retired_source_cannot_block_in_same_tick_before_scheduler_cleanup(self):
        run,caster,enemy,target,_,attack = self.session()
        actor = self.cast(run,caster)
        run.match.stop_body_script(caster)
        self.assertFalse(run.match.combat.barriers.world.enemies(enemy))
        self.assertIsNone(run.match.combat.barriers.attached(actor.entity))
        self.fire(run,enemy,attack)
        self.assertEqual(run.match.combat.vitals_of(target).total,464)

    def test_real_foreign_flashbang_does_not_end_an_already_hosted_bubble(self):
        run,caster,enemy,target,_,_ = self.session(distance=2)
        run.match.switch_hero(enemy,heroes()[0x02E0000000000042],preserve_pose=True)
        for _ in range(25):
            run.step({})
        actor = self.cast(run,caster)
        flash.FlashbangTests().cast(run,enemy,caster)
        self.assertTrue(run.match.combat.is_stunned(caster))
        self.assertEqual(run.match.combat.vitals_of(target).total,480)
        self.assertEqual(actor.health,700)
        self.assertIn(actor.entity,run.match.combat.barriers.world.actors)

    def test_actual_actor_component_and_output_are_in_passive_server_capture(self):
        observer = snapshots.ServerSnapshotTests()
        session,peers,players = observer.scene(two=True)
        caster,enemy = players
        session.apply('switch_hero',{'account':enemy.account_lo,'hero':WINSTON})
        session.apply('switch_hero',{'account':caster.account_lo,'hero':echoes.ECHO})
        caster.body_script.set_ult_charge(caster.body_script.component.entity_var(338).value())
        peers[0].commands(caster.body_script.frame+1,1,32)
        self.assertEqual(caster.hero.guid,WINSTON)
        peers[0].commands(caster.body_script.frame+1,25,0)
        peers[0].commands(caster.body_script.frame+1,1,16)
        peers[0].commands(caster.body_script.frame+1,10,0)
        actor = next(iter(caster.match.combat.barriers.world.actors.values()))
        self.assertEqual(actor.health,700)
        first = snapshots.snapshot_digest(observer.capture(session))
        actor.health -= 1  # passive sensitivity only; damage evidence uses real weapons elsewhere
        self.assertNotEqual(snapshots.snapshot_digest(observer.capture(session)),first)

    def test_two_world_bubbles_intercept_the_nearest_surface_only(self):
        run,caster,enemy,_,_,attack = self.session()
        second = self.add_ally(run,caster,3,(0,0,1),hero=WINSTON)
        for _ in range(25):
            run.step({})
        back = self.cast(run,caster)
        run.step({second:(0,0,16)})
        for _ in range(10):
            run.step({})
        front = next(a for a in run.match.combat.barriers.world.actors.values() if a.owner is second)
        self.fire(run,enemy,attack)
        self.assertEqual(front.health,649)
        self.assertEqual(back.health,700)

    def test_actual_allied_field_heals_injured_body_without_refilling_world_pool(self):
        run,caster,enemy,target,_,attack = self.session()
        soldier = self.add_ally(run,caster,3,(0,0,0))
        for _ in range(25):
            run.step({})
        actor = self.cast(run,caster)
        self.fire(run,enemy,attack)
        self.assertEqual(actor.health,649)
        x,y,z = caster.mover.position
        enemy.mover = Mover((x,y,z+2),world=run.match.collision_world,data=mover_data(enemy.hero.body))
        self.fire(run,enemy,aim(run.match.combat._eye(enemy),(x,y+1,z)))
        self.assertEqual(run.match.combat.vitals_of(target).total,464)
        run.step({soldier:(0,0,16)})
        for _ in range(160):
            run.step({})
        self.assertEqual(run.match.combat.vitals_of(target).total,500)
        self.assertEqual(actor.health,649)

    def test_actual_uncopied_caster_death_does_not_freeze_or_remove_hosted_timer(self):
        run,enemy,caster,_,_ = echoes.EchoPrimaryTests().session()
        run.match.game_map = replace(run.match.game_map,free_for_all=False)
        run.match.switch_hero(caster,heroes()[WINSTON],preserve_pose=True)
        x,y,z = caster.mover.position
        enemy.mover = Mover((x,y,z+2),world=run.match.collision_world,data=mover_data(enemy.hero.body))
        for _ in range(25):
            run.step({})
        actor = self.cast(run,caster)
        script = caster.body_script
        target = next(t for t in run.match.combat._targets() if t.owner is caster)
        a,b = target.segment()
        attack = aim(run.match.combat._eye(enemy),tuple((x+y)*.5 for x,y in zip(a,b,strict=True)))
        for _ in range(12):
            self.fire(run,enemy,attack)
            if run.match.combat.is_dead(caster):
                break
        self.assertTrue(run.match.combat.is_dead(caster))
        self.assertFalse(script.retired)
        hosted = next(i for i in actor.component.instances.values() if i.graph.index == 0xFC)
        before = expr.to_float(actor.component.entity_var(215).value())
        for _ in range(30):
            run.step({})
        self.assertIn(actor.entity,run.match.combat.barriers.world.actors)
        self.assertTrue(hosted.states[11].active)
        self.assertLess(expr.to_float(actor.component.entity_var(215).value()),before)

    def test_actual_missed_and_out_of_range_primary_emit_without_damaging_pool(self):
        for far in (False,True):
            with self.subTest(out_of_range=far):
                run,caster,enemy,target,_,attack = self.session(distance=120 if far else 8)
                actor = self.cast(run,caster)
                weapon = next(i for i in enemy.body_script.component.instances.values()
                              if i.graph.index == 0x19E5)
                if not far:
                    attack = (attack[0]+32768,attack[1])
                self.fire(run,enemy,attack,frames=140 if far else 25)
                self.assertEqual(weapon.vars[53].value(),11)
                self.assertEqual(actor.health,700)
                self.assertEqual(run.match.combat.vitals_of(target).total,500)

    def test_beam_cannot_reach_a_shell_beyond_its_authored_range(self):
        run,caster,enemy,target,_,attack = self.session(distance=25)
        actor = self.cast(run,caster)
        self.fire(run,enemy,attack,button=16,frames=25)
        beam = next(i for i in enemy.body_script.component.instances.values() if i.graph.index == 0x1FBD)
        self.assertTrue(beam.states[17].active)
        self.assertEqual(actor.health,700)
        self.assertEqual(run.match.combat.vitals_of(target).total,500)

    def test_direct_helix_break_preserves_same_event_cover_for_inside_body(self):
        run,caster,enemy,_,_,attack = self.session()
        ally = self.add_ally(run,caster,3,(0,0,4.1))
        for _ in range(25):
            run.step({})
        actor = self.cast(run,caster)
        for number in range(13):
            if number == 12:
                for _ in range(110):
                    run.step({})
            self.fire(run,enemy,attack)
        self.assertEqual(actor.health,37)
        run.match.switch_hero(enemy,heroes()[SOLDIER],preserve_pose=True)
        for _ in range(25):
            run.step({})
        self.fire(run,enemy,attack,button=2,frames=50)
        target = next(t for t in run.match.combat._targets() if t.owner is ally)
        self.assertEqual(actor.health,0)
        self.assertNotIn(actor.entity,run.match.combat.barriers.world.actors)
        self.assertEqual(run.match.combat.vitals_of(target).total,200)

    def test_private_static_actor_references_stay_out_of_typed_owner_snapshots(self):
        run,caster,_,_,_,_ = self.session()
        self.cast(run,caster)

        def inspect(value,seen):
            if type(value) is int:
                self.assertFalse(FIRST <= (value & 0xFFFFFFFF) < FIRST+SPAN)
                return
            if id(value) in seen:
                return
            seen.add(id(value))
            if isinstance(value,dict):
                for item in value.values():
                    inspect(item,seen)
            elif isinstance(value,(tuple,list,set,frozenset)):
                for item in value:
                    inspect(item,seen)
            elif dataclasses.is_dataclass(value):
                for field in dataclasses.fields(value):
                    inspect(getattr(value,field.name),seen)

        for count in (0,80,400,100):
            for _ in range(count):
                run.step({})
            inspect(caster.body_script.snapshot(),set())

    def test_reconstructed_static_creation_does_not_downgrade_strict_vm(self):
        run,_caster,_,_,instance,_ = self.session()
        with patch.object(instance.component,'strict',True),self.assertRaises(runtime.UnsupportedScriptError):
            instance.run_node(instance.graph.node(26),None)
        self.assertFalse(run.match.combat.barriers.world.actors)
