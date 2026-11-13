"""One real Uppercut, original15NPC identities and the existing physical mover."""
import math
import unittest
from unittest.mock import patch

from test_game_combat import Run, aim
from ow174.game.content import heroes
from ow174.game.mover import SET


class UppercutNPCBodyScriptTests(unittest.TestCase):
    def test_actual_uppercut_uses_existing_npc_component_and_one_mover(self):
        run=Run();match=run.match;owner=run.players[0]
        match.switch_hero(owner,heroes()[0x02E000000000012F])
        identities=tuple(b.entity for b in match.bots)
        self.assertEqual(len(identities),15)
        bot=match.bots[0]
        x,y,z=owner.mover.position
        bot.position=(x,y,z+1.5);bot.route=((x,y,z+1.5),(x+20,y,z+1.5))
        bot.firing=False;bot.team=1-owner.team
        run.step({})
        script=bot.body_script;component=script.component
        fight=match.combat;target=next(t for t in fight._targets() if t.owner is bot)
        before=fight.vitals_of(target).total
        yaw,pitch=aim(fight._eye(owner),(bot.position[0],bot.position[1]+1,bot.position[2]))
        with patch.object(fight.knockbacks,'apply',wraps=fight.knockbacks.apply) as force:
            run.step({owner:(yaw,pitch,8)})
            mover=bot.impulse_mover
            self.assertIsNotNone(mover)
            self.assertIs(fight.knockbacks.active[bot.entity],mover)
            self.assertIs(bot.body_script,script)
            self.assertIs(component,owner.body_script.world._body_component(bot.entity))
            self.assertEqual(component.entity,bot.entity)
            self.assertNotEqual(component.entity,bot.body)
            helper,=(i for i in component.instances.values() if i.graph.index==0xE7D)
            self.assertEqual(helper.source_entity(),owner.body)
            resets=[m for m in mover.velocity_changes if m.index==0 and m.op==SET and m.value==0]
            self.assertEqual(len(resets),1)
            launch,=(m for m in mover.velocity_changes if m.index==-1)
            self.assertEqual(resets[0].key,launch.key)
            self.assertEqual(resets[0].frame,launch.frame)
            self.assertEqual(force.call_count,1)
            self.assertEqual(before-fight.vitals_of(target).total,50)
            run.step({})
            self.assertIs(bot.impulse_mover,mover)
            self.assertAlmostEqual(mover.velocity[1],14,delta=.01)
            self.assertAlmostEqual(math.hypot(*mover.velocity[::2]),7,delta=.15)
            self.assertEqual(component.entity_var(417).value(),mover.airborne)
            self.assertEqual(component.entity_var(545).value(),not mover.airborne)
            run.step({})
            self.assertGreater(mover.velocity[1],13)
            self.assertEqual(component.entity_var(417).value(),mover.airborne)
            self.assertTrue(helper.state(2).active)
            self.assertTrue(helper.state(3).active)
            for _ in range(45):run.step({})
            self.assertFalse(helper.state(2).active)
            for _ in range(195):run.step({})
            self.assertFalse(helper.state(3).active)
            self.assertTrue(helper.stopped)
            self.assertEqual(force.call_count,1)
            self.assertIs(bot.body_script,script)
            self.assertEqual(identities,tuple(b.entity for b in match.bots))
            script.retire()
            self.assertTrue(helper.stopped)
