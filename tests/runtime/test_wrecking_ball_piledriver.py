"""Piledriver runs through the authored graph, shared mover and combat paths."""

import math
import unittest
from dataclasses import replace
from unittest.mock import patch

from ow174.game import combat, observers, stats
from ow174.game.body_vm_snapshot import export_body_vm
from ow174.game.collision import StaticCollisionProvider, World
from ow174.game.commands import CROUCH, JUMP, Command
from ow174.game.content import SOLDIER, heroes
from ow174.game.mover import FELL, Mover
from ow174.game.script.expr import Entity
from tests.runtime.test_game_combat import KANEZAKA_DM, Run, in_world
from tests.runtime.test_game_mover import box, wall

WRECKING_BALL = 0x02E00000000001CA
PILEDRIVER = 0x160A
PILEDRIVER_VICTIM = 0x165A


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

    def add_target(self, slot, name, team, dx, dz):
        target = self.match.add_player(slot, name, SOLDIER, team, False)
        in_world(self.match, target)
        self.match.switch_hero(target, heroes()[SOLDIER])
        self.place_target(target, dx, dz)
        return target

    def place_target(self, target, dx, dz):
        x, _, z = self.ball.mover.position
        mover = target.mover
        target.mover = Mover(
            (x + dx, self.floor, z + dz), world=self.match.collision_world,
            data=mover.data, category=mover.category,
        )

    def target_command(self, target, **inputs):
        target.take_commands([Command(self.run.frame, **inputs)])
        return target.mover.history[-1]

    def land_piledriver(self, moving_target=None):
        self.place_above_ground(8.0)
        self.advance(1, CROUCH)
        jumped = False
        for _ in range(70):
            self.advance(1)
            if moving_target is not None:
                jump = self.graph.state(3).active and not jumped
                jumped |= jump
                self.target_command(
                    moving_target, buttons=JUMP if jump else 0,
                    forward=127 if jumped else 0, right=127 if jumped else 0,
                )
            if self.graph.state(9).active:
                return
        self.fail("Piledriver did not reach impact")

    @staticmethod
    def victim_instances(player):
        return [
            instance for instance in player.body_script.component.instances.values()
            if instance.graph.index == PILEDRIVER_VICTIM
        ]

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
            target = self.add_target(slot, name, team, dx, dz)
            self.run.players.append(target)
            targets.append(target)
        health = [self.match.combat.body_vitals(target.body, target) for target in targets]

        self.land_piledriver()
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

    def test_impact_resets_airborne_victim_releases_input_and_reacts_on_recast(self):
        target = self.add_target(2, "moving", 1, 4, 4)
        self.land_piledriver(moving_target=target)
        moving = target.mover.history[-1]
        self.assertTrue(target.mover.airborne)
        self.assertGreater(moving.velocity[1], 0.0)
        self.assertNotEqual(moving.velocity[::2], (0.0, 0.0))
        self.assertEqual(self.victim_instances(target), [])

        self.advance(1)
        helper, = self.victim_instances(target)
        self.assertFalse(helper.auto_retire)
        self.assertEqual(helper.source_entity(), self.ball.body)
        self.assertEqual(self.victim_instances(self.ball), [])
        self.assertIsNone(target.body_script.component.fault)
        for snapshot in (target.body_script.snapshot(), observers.snapshot(target.body_script)):
            self.assertEqual(snapshot.instances[helper.id], (PILEDRIVER_VICTIM, None))
            self.assertTrue(snapshot.states[(helper.id, 1)][1])
        export_body_vm(target.body_script)

        halted = self.target_command(target, forward=127, right=127)
        self.assertEqual(halted.position, moving.position)
        self.assertEqual(halted.velocity, (0.0, 0.0, 0.0))
        self.advance(1)
        falling = self.target_command(target, forward=127, right=127)
        self.assertLess(falling.velocity[1], 0.0)
        self.assertEqual(falling.velocity[::2], (0.0, 0.0))

        released = False
        for _ in range(34):
            self.advance(1)
            moved = self.target_command(target, forward=127, right=127)
            if helper.stopped:
                break
            if not helper.state(1).active and moved.velocity[::2] != (0.0, 0.0):
                released = True
        self.assertTrue(released)
        self.assertTrue(helper.stopped)
        self.assertEqual(self.victim_instances(target), [])
        self.assertNotIn(helper.id, target.body_script.snapshot().instances)
        self.assertNotIn(helper.id, observers.snapshot(target.body_script).instances)

        ability = self.graph.state(24)
        self.assertGreater(ability.remaining(), 0.0)
        frame_ms = self.ball.body_script.component.frame_ms
        self.run.frame += math.ceil(1000 * ability.remaining() / frame_ms)
        self.advance(1)
        self.assertEqual(ability.remaining(), 0.0)

        self.place_target(target, 4, 4)
        self.land_piledriver(moving_target=target)
        self.advance(1)
        repeated, = self.victim_instances(target)
        self.assertNotEqual(repeated.id, helper.id)
        self.assertEqual(repeated.source_entity(), self.ball.body)
        self.assertEqual(self.target_command(target, forward=127, right=127).velocity, (0.0, 0.0, 0.0))

    def test_queued_impact_reaction_cannot_reach_replacement_body(self):
        target = self.add_target(2, "target", 1, 4, 4)
        old_body = target.body
        old_script = target.body_script
        with patch.object(self.match, "queue_body_play", wraps=self.match.queue_body_play) as queued:
            self.land_piledriver()
        queued.assert_called_once()
        self.assertEqual(queued.call_args.args[1].targets, (old_body,))
        self.assertEqual(self.victim_instances(target), [])

        self.assertTrue(self.match.switch_hero(target, target.hero))
        self.assertNotEqual(target.body, old_body)
        self.assertTrue(old_script.retired)
        self.advance(1)
        self.assertEqual(self.victim_instances(target), [])
        self.assertGreater(self.target_command(target, buttons=JUMP).velocity[1], 0.0)
