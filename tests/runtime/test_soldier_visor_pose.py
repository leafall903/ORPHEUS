"""Original 0259 presentation graph under ordinary player input and owner wire."""

import json
import unittest
from pathlib import Path

from ow174.game import soldier_visor
from ow174.game.collision import flat_world
from ow174.game.script import expr
from ow174.game.script.decode import ClientBody
from ow174.game.script.driver import BodyStream
from tests.runtime.test_game_combat import Run


class SoldierVisorPoseTests(unittest.TestCase):
    def test_direction_and_mappings_follow_a_moving_off_reticle_target(self):
        run = Run(names=('Soldier', 'Enemy'))
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        run.match.collision_world = flat_world(0)
        soldier, enemy = run.players
        for player, position in ((soldier, (0, 0, 0)), (enemy, (3, 0, 6))):
            player.mover.position = list(position)
            player.mover.body = None
            player.body_script.create_arrived = True
        for _ in range(40):
            run.step({})
        script = soldier.body_script
        script.ult_seconds = 0
        script.set_ult_charge(script.component.vars[338].value())
        run.step({soldier: (0, 0, 32)})
        for _ in range(100):
            run.step({})
        instance = next(i for i in script.component.instances.values() if i.graph.index == 0x259)
        chase = instance.states[29]
        self.assertTrue(chase.active)
        self.assertGreater(instance.vars[1984].value().x, 0.4)
        self.assertTrue(chase.reached)
        weapon = next(i for i in script.component.instances.values() if i.graph.index == 0x254)
        handle = weapon.vars[49].value()
        client, stream = ClientBody.for_hero(soldier.hero.guid), BodyStream(soldier.body)
        client.apply(stream.full(*script.tree_frame()))
        self.assertEqual(client.vars[(instance.id, 1984)], instance.vars[1984].value())
        self.assertEqual(weapon.vars[49].value(), handle)
        for index in (30, 31):
            state = instance.states[index]
            self.assertTrue(state.active)
            self.assertNotEqual(state.evaluate(state.node.field('m_answer'), watch=False), 0)
        enemy.mover.position[0] = -3
        enemy.mover.body = None  # rebase this fixture's authoritative relocation
        for _ in range(20):
            run.step({})
        self.assertLess(instance.vars[1984].value().x, -0.4)
        self.assertTrue(chase.reached)
        for _ in range(150):
            run.step({soldier: (-32768, 0, 0)})
        self.assertEqual(script.component.entity_var(2337).value(), ())
        self.assertFalse(instance.states[30].active)
        self.assertFalse(instance.states[31].active)
        self.assertEqual(instance.vars[3072].value(), 0)
        self.assertEqual(instance.vars[502].value(), 0)
        for _ in range(400):
            run.step({})
        self.assertFalse(chase.active)
        self.assertEqual(weapon.vars[49].value(), handle)

    def test_relative_angles_match_native_arithmetic_cases(self):
        path = Path(__file__).resolve().parents[2] / 'fixtures/behavior/soldier-visor-pose-angles-v1.json'
        contract = json.loads(path.read_text())
        for case in contract['cases']:
            with self.subTest(case=case):
                result = soldier_visor.relative_angles(expr.Vec3(*case['target']), expr.Vec3(*case['view']))
                for actual, expected in zip(result, case['expected'], strict=True):
                    self.assertAlmostEqual(actual, expected, delta=contract['tolerance'])
