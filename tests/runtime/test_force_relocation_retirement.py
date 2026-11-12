"""Original force retirement on actual Shadow, Halt and Translocator entry."""
import unittest
import math
import os
from pathlib import Path
from unittest.mock import patch
import test_reaper_shadow as shadow
import test_orisa_halt as halt
import test_game_sombra_combat as sombra
from ow174.game.script import expr
from ow174.game import npc
from ow174.game.commands import Command
import test_delivered_force_world_gap as force_gap


def pending_force(case, fight, owner, source):
    target = next(t for t in fight._targets() if t.owner is owner)
    case.assertTrue(fight.knockbacks.apply(target, expr.Vec3(1., 1., 0.),
                                         impact_frame=fight.match.tick + 200))
    fight.knockbacks.record_launch(target, source_entity=source)
    case.assertIn(target.entity, fight.knockbacks.launches)
    case.assertEqual(fight.knockbacks.launch_sources[target.entity], source)
    return target.entity


def retired(case, fight, entity):
    case.assertNotIn(entity, fight.knockbacks.launches)
    case.assertNotIn(entity, fight.knockbacks.launch_sources)
    case.assertNotIn(entity, fight.knockbacks.active)


class RelocationRetirementTests(unittest.TestCase):
    def test_actual_shadow_success_retires_old_accepted_force(self):
        fixture = shadow.ShadowStepTests()
        run, owner, victim, instance = fixture.scene()
        fixture.select(run, owner)
        fixture.confirm(run, owner)
        fight = run.match.combat
        entity = pending_force(self, fight, owner, victim.body)
        for _ in range(110):
            run.step({})
            if fight.reaper_shadows[owner.body]['moved']:
                break
        self.assertTrue(fight.reaper_shadows[owner.body]['moved'])
        retired(self, fight, entity)

    def test_actual_halt_moveto_retires_old_accepted_force(self):
        fixture = halt.HaltTests()
        run, caster, enemy = fixture.arena()
        fixture.detonate(run, caster)
        fight = run.match.combat
        entity = pending_force(self, fight, enemy, caster.body)
        start = tuple(enemy.mover.position)
        for _ in range(40):
            run.step({})
            if tuple(enemy.mover.position) != start:
                break
        self.assertNotEqual(tuple(enemy.mover.position), start)
        retired(self, fight, entity)

    def test_actual_translocator_recall_calls_existing_force_retirement(self):
        f = sombra.SombraCombatTests()
        f.setUp()
        f.b.mover.position[:] = (0, 20, 5)
        f.b.mover.body = None
        f.press(16, 20)
        fight = f.run.match.combat
        self.assertEqual(len(fight.translocators.entries), 1)
        f.press(0, 60)
        with patch.object(fight.knockbacks, 'retire', wraps=fight.knockbacks.retire) as cleared:
            f.press(16)
        root = next(i for i in f.a.body_script.component.instances.values() if i.graph.index == 0x0C19)
        self.assertTrue(any(root.node_state(root.graph._by_pos[pos]).accepted for pos in (18, 131)))
        cleared.assert_any_call(f.a.body)
        retired(self, fight, f.a.body)

    def test_actual_halt_npc_retires_queued_original_force(self):
        fixture = halt.HaltTests()
        run, caster, enemy = fixture.arena()
        enemy.spawned = False
        catalog = npc.load_catalog(Path(os.environ['ORPHEUS_PVE_NPC_DATA']) / 'pve_npcs_174.json')
        definition = next(d for d in catalog.values() if d.name == 'Enforcer')
        x,y,z = caster.mover.position
        bot = npc.spawn(run.match, definition, (x,y,z+5), team=1-caster.team)
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is bot)
        self.assertTrue(fight.knockbacks.apply(target, expr.Vec3(1.,1.,0.), impact_frame=run.match.tick+200))
        self.assertIn(bot.entity, fight.knockbacks.pending_npc)
        start = bot.position
        fixture.detonate(run, caster)
        for _ in range(45):
            run.step({})
            if bot.position != start:
                break
        self.assertNotEqual(bot.position,start)
        self.assertNotIn(bot.entity, getattr(fight.knockbacks,'pending_npc',{}))
        self.assertIsNone(bot.body_script.component.fault)

    def test_actual_recall_keeps_completed_force_pose_receipt_and_rejects_late_fire(self):
        f, fight, _ = force_gap.ForceWorldGapTests().play(1,30,True)
        owner = f.a
        self.assertIn(owner.body, fight.knockbacks.replayed_frames)
        birth = fight.knockbacks.body_epochs[owner.body][1]
        f.b.mover.position[:] = (0, 20, 5)
        f.b.mover.body = None
        f.press(0,180)
        f.press(16,20)
        self.assertEqual(len(fight.translocators.entries),1)
        f.press(0,60)
        completed = fight.knockbacks.replayed_frames[owner.body]
        f.press(16)
        root = next(i for i in owner.body_script.component.instances.values() if i.graph.index==0x0C19)
        self.assertTrue(any(root.node_state(root.graph._by_pos[pos]).accepted for pos in (18,131)))
        self.assertEqual(fight.knockbacks.replayed_frames[owner.body],completed)
        self.assertEqual(fight.knockbacks.body_epochs[owner.body][1],birth)
        position=tuple(owner.mover.position)
        with patch.object(fight,'_shoot',wraps=fight._shoot) as fired:
            owner.take_commands([Command(completed[1],buttons=1)])
        self.assertEqual(fired.call_count,0)
        self.assertEqual(tuple(owner.mover.position),position)


    def test_halt_release_does_not_resume_old_npc_force_pose(self):
        fixture=halt.HaltTests()
        run,caster,enemy=fixture.arena()
        enemy.spawned=False
        catalog=npc.load_catalog(Path(os.environ['ORPHEUS_PVE_NPC_DATA'])/'pve_npcs_174.json')
        definition=next(d for d in catalog.values() if d.name=='Enforcer')
        x,y,z=caster.mover.position
        bot=npc.spawn(run.match,definition,(x,y,z+5),team=1-caster.team)
        fixture.detonate(run,caster)
        fight=run.match.combat
        target=next(t for t in fight._targets() if t.owner is bot)
        self.assertTrue(fight.knockbacks.apply(target,expr.Vec3(1.,2.,0.)))
        retained=bot.impulse_mover
        self.assertIsNotNone(retained)
        data=retained.data
        previous=bot.position
        active_before=False
        entered=False
        released=False
        after=0
        for _ in range(120):
            run.step({})
            active=fight.orisa.controls(bot.entity)
            entered |= active
            if entered and active_before and not active:
                released=True
            if released:
                self.assertLess(math.dist(previous,bot.position),0.5)
                self.assertIs(bot.impulse_mover,retained)
                self.assertIs(retained.data,data)
                self.assertFalse(retained.external_impulse)
                after+=1
                if after==6:
                    break
            previous=bot.position
            active_before=active
        self.assertTrue(entered)
        self.assertTrue(released)
        self.assertEqual(after,6)
        self.assertIsNone(bot.body_script.component.fault)
