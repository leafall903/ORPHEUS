"""Actual Cryo plus another Mei's wall; camera/FP hiding remain native-open."""
import json
import unittest
from pathlib import Path

import test_delivered_mei_combat as scenes
import test_mei_wall as wall_scenes

from ow174.game import mei, mei_presentation, world
from ow174.game.content import heroes

CONTRACT = json.loads((Path(__file__).resolve().parents[2]
                       / 'fixtures/behavior/mei-cryo-source-pose-v1.json').read_text())


class CryoPoseFollowTests(unittest.TestCase):
    def test_actual_wall_lift_updates_same_cryo_pose_after_create_ack(self):
        run, owner, caster, _ = scenes.MeiCombatTests().duel()
        self.assertTrue(run.match.switch_hero(caster, heroes()[mei.HERO]))
        fight = run.match.combat
        x, y, z = owner.mover.position
        fight.walls.place_player(caster, (x, y, z - .25))
        for frame in range(2):
            run.step({owner: (0, 0, 8 if frame == 0 else 0)})
        ice = next(b for b in fight.barriers.scripted.values() if b.parent is owner)
        component = ice.component
        for viewer in run.players:
            viewer.client.bot_create_acked = lambda entity: False
            viewer.client.entities.clear()
        wall_scenes.WallTests().place(run, caster, distance=.25)
        self.assertGreater(owner.mover.position[1], y + CONTRACT['minimum_lift'])
        self.assertIs(fight.barriers.scripted[ice.entity].component, component)
        for viewer in run.players:
            self.assertFalse(any(u.entity == ice.entity and u.movement is not None
                                 for u in viewer.client.entities))
            viewer.client.bot_create_acked = lambda entity: True
            viewer.client.entities.clear()
        run.step({})
        for viewer in run.players:
            updates = [u for u in viewer.client.entities
                       if u.entity == ice.entity and u.movement is not None]
            self.assertTrue(updates)
            latest = updates[-1]
            self.assertEqual(latest.op, 0)  # movement only; no ch4 component or lifecycle operation
            self.assertFalse(latest.resend)
            self.assertEqual(tuple(latest.movement.position), tuple(owner.mover.position))
            self.assertFalse(any(u.entity == ice.entity and u.op == world.OP_CREATE
                                 for u in viewer.client.entities))
            self.assertIs(mei_presentation.projection(fight, ice.entity).component, component)
