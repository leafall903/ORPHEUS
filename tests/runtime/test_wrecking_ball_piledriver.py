"""Piledriver runs through the authored graph, shared mover and combat paths."""

import math
import unittest
from dataclasses import replace

from ow174.game import combat, stats
from ow174.game.body_vm_snapshot import export_body_vm
from ow174.game.collision import StaticCollisionProvider, World
from ow174.game.commands import CROUCH
from ow174.game.content import SOLDIER, heroes
from ow174.game.mover import FELL, Mover
from ow174.game.script.expr import Entity
from tests.runtime.test_game_combat import KANEZAKA_DM, Run, in_world
from tests.runtime.test_game_mover import box, wall

WRECKING_BALL = 0x02E00000000001CA
PILEDRIVER = 0x160A


class PiledriverGroundTests(unittest.TestCase):
    def setUp(self):
        self.run = Run(KANEZAKA_DM)
        self.match = self.run.match
        self.match.bots.clear()
        self.match._pending_bot_scripts.clear()
        self.ball = self.run.players[0]
        self.match.switch_hero(self.ball, heroes()[WRECKING_BALL])
        self.advance(80)
        self.floor = self.ball.mover.position[1]
        self.graph = next(instance for instance in self.ball.body_script.component.instances.values()
                          if instance.graph.index == PILEDRIVER)

    def advance(self, ticks, buttons=0):
        for _ in range(ticks):
            self.run.step({self.ball: (0, 0, buttons)})

    def place_above_ground(self, height):
        mover = self.ball.mover
        x, _, z = mover.position
        self.ball.mover = Mover(
            (x, self.floor + height, z), world=self.match.collision_world,
            data=mover.data, category=mover.category,
        )
        self.ball.mover.flags_word = FELL
        self.advance(4)

    def test_near_ground_does_not_enable_or_start_piledriver(self):
        self.place_above_ground(3.0)
        component = self.ball.body_script.component
        self.assertTrue(component.entity_var(417).value())
        self.assertFalse(component.entity_var(545).value())
        self.assertTrue(component.entity_var(2413).value())
        self.advance(1, 0x80)
        self.assertFalse(self.graph.state(18).active)

    def test_high_airborne_cast_runs_windup_descent_and_lands(self):
        self.place_above_ground(8.0)
        component = self.ball.body_script.component
        self.assertFalse(component.entity_var(2413).value())
        self.assertTrue(self.graph.vars[9570].value())
        self.advance(1, 0x80)
        self.assertTrue(self.graph.state(18).active)
        export_body_vm(self.ball.body_script)

        fastest_descent = 0.0
        reached_impact = False
        for _ in range(70):
            self.advance(1)
            fastest_descent = min(fastest_descent, self.ball.mover.velocity[1])
            reached_impact |= self.graph.state(9).active

        self.assertAlmostEqual(fastest_descent, -98.0)
        self.assertTrue(reached_impact)
        self.assertAlmostEqual(self.ball.mover.position[1], self.floor)
        self.assertFalse(self.graph.state(3).active)
        self.assertFalse(component.entity_var(417).value())
        self.assertTrue(component.entity_var(545).value())
        self.assertTrue(component.entity_var(2413).value())
        self.assertFalse(self.graph.vars[9570].value())

    def test_impact_uses_visible_hostile_capsule_distances_and_hits_each_once(self):
        self.match.game_map = replace(self.match.game_map, free_for_all=False, team_sizes=(2, 5))
        x, y, z = self.ball.mover.position
        scene = World(
            box(x - 30, x + 30, y - 1, y, z - 30, z + 30)
            + wall(x - 2, z + 2, x - 2, z + 6, y - 1, y + 4)
        )
        self.match.collision_provider = StaticCollisionProvider(scene)
        self.match.collision_world = scene

        opponents_and_ally = (
            ("near", 1, 1.5, 4), ("edge", 1, 8.3, 4), ("outside", 1, 0, -10),
            ("behind wall", 1, -4, 4), ("ally", 0, 0, 6),
        )
        targets = []
        for slot, (name, team, dx, dz) in enumerate(opponents_and_ally, 2):
            target = self.match.add_player(slot, name, SOLDIER, team, False)
            in_world(self.match, target)
            self.match.switch_hero(target, heroes()[SOLDIER])
            self.run.players.append(target)
            mover = target.mover
            target.mover = Mover((x + dx, y, z + dz), world=scene, data=mover.data, category=mover.category)
            targets.append(target)
        health = [self.match.combat.body_vitals(target.body, target) for target in targets]

        self.place_above_ground(8.0)
        self.advance(1, CROUCH)
        for _ in range(70):
            self.advance(1)
            if self.graph.state(9).active:
                break
        self.assertTrue(self.graph.state(9).active)
        self.assertIsNone(self.ball.body_script.component.fault)

        near, edge = targets[:2]
        self.assertEqual(self.graph.vars[375].value(), (Entity(near.body), Entity(edge.body)))
        distances = self.graph.vars[9326].value()
        self.assertEqual(len(distances), 2)
        cx, _, cz = self.ball.mover.position
        for target, distance in zip((near, edge), distances, strict=True):
            tx, _, tz = target.mover.position
            # Soldier's lower capsule endpoint is 0.5 m up, 0.4 m above the query centre.
            axis_distance = math.dist((cx, 0.1, cz), (tx, 0.5, tz))
            self.assertAlmostEqual(distance, axis_distance - 0.5, places=5)
        self.assertLess(distances[0], 1.5)
        self.assertGreater(distances[1] + 0.5, 8.0)
        self.assertLess(distances[1], 8.0)

        edge_damage = 20.0 + 80.0 * (8.0 - distances[1]) / 6.5
        self.assertEqual(health[0].total, 100.0)
        self.assertAlmostEqual(health[1].total, 200.0 - edge_damage, places=4)
        self.assertEqual([vitals.total for vitals in health[2:]], [200.0, 200.0, 200.0])
        self.assertAlmostEqual(self.ball.stats.value(stats.HERO_DAMAGE), 100.0 + edge_damage, places=4)

        def hit_recipients():
            return [notice["+0x78"]["+0x0"]["+0x0"] for notice in self.ball.client.messages(combat.HIT)]

        self.assertEqual(hit_recipients(), [near.body, edge.body])
        self.advance(70)
        self.assertEqual(hit_recipients(), [near.body, edge.body])
        self.assertEqual(health[0].total, 100.0)
        self.assertAlmostEqual(health[1].total, 200.0 - edge_damage, places=4)
