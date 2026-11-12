"""Actual Heavy melee retains its existing action force on configured Sentry pose."""
import os
import unittest
from pathlib import Path
import test_bastion_combat as bastion
from test_game_combat import aim
from ow174.game import npc,npc_control


class HeavyImpulseTests(unittest.TestCase):
    def test_actual_heavy_melee_moves_configured_sentry_once(self):
        case=bastion.BastionCombatTests()
        script=case.fixture(enemy_position=(10,1,0))
        case.configure()
        catalog=npc.load_catalog(Path(os.environ['ORPHEUS_NPC_DATA_DIR'])/'pve_npcs_174.json')
        bot=npc.spawn(case.run.match,catalog['5'],(0,1,2),team=1,yaw=-32768)
        target=case.target(bot)
        case.command_aim=aim(case.fight._eye(case.player),
                             (bot.position[0],bot.position[1]+target.height/2,bot.position[2]))
        case.step(0,8)
        health=case.health(case.player)
        before=health.total
        position=tuple(case.player.mover.position)
        self.assertEqual(script.component.entity_var(1216).value(),2)
        self.assertEqual(npc_control.start(case.run.match,case.player,melee=True),bot.entity)
        case.step(0,65)
        self.assertAlmostEqual(before-health.total,80*.8-5,places=5)
        self.assertLess(case.player.mover.position[2],position[2])
        self.assertEqual(script.component.entity_var(1216).value(),2)
        self.assertEqual(npc_control.stop(case.run.match),1)
        after=health.total
        case.step(0,30)
        self.assertEqual(health.total,after)
        self.assertFalse(case.player.mover.velocity_changes)


    def test_actual_trooper_melee_keeps_authored_position_gate(self):
        for distance,damage in ((1,25),(2,0)):
            with self.subTest(distance=distance):
                case=bastion.BastionCombatTests()
                case.fixture(enemy_position=(10,1,0))
                catalog=npc.load_catalog(Path(os.environ['ORPHEUS_NPC_DATA_DIR'])/'pve_npcs_174.json')
                bot=npc.spawn(case.run.match,catalog['1'],(0,1,distance),team=1,yaw=-32768)
                target=case.target(bot)
                case.command_aim=aim(case.fight._eye(case.player),
                                     (bot.position[0],bot.position[1]+target.height/2,bot.position[2]))
                case.step(0,8)
                before=case.health(case.player).total
                position=tuple(case.player.mover.position)
                self.assertEqual(npc_control.start(case.run.match,case.player,melee=True),bot.entity)
                case.step(0,65)
                self.assertEqual(before-case.health(case.player).total,damage)
                instance=next(i for i in bot.body_script.component.instances.values() if i.graph.index==0x1422)
                self.assertEqual(instance.states[instance.graph.node(4).state].counter,int(bool(damage)))
                self.assertEqual(tuple(case.player.mover.position),position)
                self.assertEqual(npc_control.stop(case.run.match),1)
