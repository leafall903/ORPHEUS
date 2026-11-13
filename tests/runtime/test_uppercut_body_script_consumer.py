"""Real Uppercut request reaches its actual victim VM; no invented stun/force."""
import math
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from test_doomfist_combat import DoomfistFixture
from ow174.game import combat
from ow174.game.commands import Command
from ow174.game.mover import SET, VELOCITY_MAGNITUDE


class UppercutBodyScriptConsumerTests(unittest.TestCase):
    contract=json.loads((Path(__file__).resolve().parents[2]/'fixtures/behavior/uppercut-body-script-consumer-v1.json').read_text())
    def scene(self):
        fixture=DoomfistFixture();fixture.prepare(self.contract['input']['distance'])
        fixture.match.switch_hero(fixture.b,fixture.a.hero)
        fixture.settle(1)
        return fixture

    def test_actual_uppercut_runs_reset_then_single_add_and_original_control_exit(self):
        f=self.scene();fight=f.match.combat
        f.b.mover.velocity=tuple(self.contract['input']['pre_velocity'])
        with patch.object(fight.knockbacks,'apply',wraps=fight.knockbacks.apply) as forces:
            f.run.step({f.a:(0,0,8)})
            component=f.b.body_script.component
            helper,=(i for i in component.instances.values() if i.graph.index==0xE7D)
            self.assertEqual(helper.source_entity(),f.a.body)
            self.assertEqual(component.entity,f.b.body)
            resets=[m for m in f.b.mover.mods.values()
                    if m.index==VELOCITY_MAGNITUDE and m.op==SET and m.value==0]
            self.assertEqual(len(resets),1)
            self.assertEqual(resets[0].key[0],helper.id)
            self.assertEqual(forces.call_count,1)
            self.assertEqual(f.vitals().total,200)
            f.run.step({});f.run.step({})
            self.assertTrue(helper.state(2).active)
            self.assertEqual(helper.state(2).counter,1)
            self.assertAlmostEqual(f.b.mover.velocity[1],14-17.5*.016,delta=.01)
            self.assertAlmostEqual(math.hypot(*f.b.mover.velocity[::2]),7,delta=.2)
            f.settle(45)
            self.assertFalse(helper.state(2).active)
            self.assertEqual(forces.call_count,1)
            self.assertNotIn(f.b.body,fight.stuns)

    def test_shifted_victim_clock_uses_actual_component_and_old_body_retires(self):
        f=DoomfistFixture();f.prepare(1.5)
        f.b.commands.last_frame=9000
        f.match.switch_hero(f.b,f.a.hero,preserve_pose=True,start_frame=9000)
        old=f.b.body_script;old_body=f.b.body
        def step(buttons=0):
            f.run.frame+=1
            f.match.update(f.run.frame*combat.TICK,f.run.frame)
            for p in f.run.players:
                p.take_commands([Command(p.commands.last_frame+1,buttons=buttons if p is f.a else 0)])
        step(8);step();step()
        helper,=(i for i in old.component.instances.values() if i.graph.index==0xE7D)
        self.assertEqual(old.component.frame,9003)
        self.assertNotEqual(old.component.frame,f.run.frame)
        self.assertEqual(helper.source_entity(),f.a.body)
        self.assertTrue(helper.state(2).active)
        self.assertEqual(f.match.combat.knockbacks.serial,1)
        f.match.switch_hero(f.b,f.b.hero,preserve_pose=True)
        self.assertTrue(old.retired)
        self.assertTrue(helper.stopped)
        self.assertNotEqual(f.b.body,old_body)
        self.assertFalse(any(i.graph.index==0xE7D for i in f.b.body_script.component.instances.values()))
        old_frame=old.component.frame
        force_count=f.match.combat.knockbacks.serial
        self.assertEqual(old.frames(),[])
        f.place_player(f.b,(30,0,30))
        step();step()
        self.assertEqual(old.component.frame,old_frame)
        self.assertEqual(old.frames(),[])
        self.assertEqual(f.match.combat.knockbacks.serial,force_count)
        self.assertFalse(any(i.graph.index==0xE7D for i in f.b.body_script.component.instances.values()))
