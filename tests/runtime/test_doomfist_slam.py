"""Run grounded Seismic Slam through real command input and static collision."""

import unittest
from dataclasses import replace
from unittest.mock import patch

from ow174.game import bodytrace, bots, combat, movelog
from ow174.game.collision import World
from ow174.game.content import SOLDIER
from ow174.game.script import nodes
from ow174.game.script.expr import Entity
from tests.runtime import test_game_move_to as movement
from tests.runtime.test_game_combat import in_world
from tests.runtime.test_game_mover import FLOOR, wall


class GroundedSeismicSlamTests(unittest.TestCase):
    def test_ground_press_launches_lands_and_applies_close_damage_once(self):
        self.enterContext(patch.object(bodytrace, "ENABLED", False))
        self.enterContext(patch.object(movelog, "ENABLED", False))
        harness = movement.FixedTargetMoveToTests()
        geometry = World(FLOOR + wall(-0.7, 5.0, -0.7, 12.0, 1.0, 4.0))
        with patch.object(bots, "make", return_value=[]):
            player, slam = harness.spawn(movement.DOOMFIST, 0x0B27, geometry)
        match = player.match
        match.game_map = replace(match.game_map, free_for_all=False, team_sizes=(2, 4))
        in_world(match, player)
        cast = movement.START + 2
        harness.command(player, cast - 1)
        start = harness.command(player, cast, buttons=0x10)
        self.assertTrue(slam.state(0).flags & nodes.Ability.RUNNING)
        self.assertEqual(slam.vars[59].value(), 1.0)
        self.assertTrue(slam.state(1).active)
        self.assertEqual(start.position, (0.0, 1.0, 0.0))

        launch = harness.command(player, cast + 1)
        self.assertGreater(launch.velocity[1], 5.0)
        self.assertGreater(launch.velocity[2], 18.0)
        self.assertTrue(player.mover.airborne)
        self.assertGreater(launch.position[2], start.position[2])

        for frame in range(cast + 2, cast + 37):
            landing = harness.command(player, frame)
            if slam.state(58).active:
                break
        else:
            self.fail("Grounded Seismic Slam did not enter its impact phase")
        self.assertFalse(slam.state(1).active)
        self.assertEqual(slam.vars[59].value(), 3)
        self.assertAlmostEqual(landing.position[1], 1.0)
        self.assertGreater(landing.position[2], 7.0)
        self.assertLess(landing.position[2], 9.0)

        # Spawn after landing so target contacts do not change the launch distance.
        x, y, z = landing.position
        targets = []
        cases = (
            ("visible", 1, 1.25, 0.65), ("also visible", 1, 1.25, 1.6),
            ("occluded", 1, -1.25, 0.65), ("ally", 0, 1.2, 0.65), ("out of range", 1, 3.0, 0.65),
        )
        for slot, (name, team, dx, dz) in enumerate(cases, 2):
            target = match.add_player(slot, name, SOLDIER, team, False)
            in_world(match, target)
            self.assertTrue(match.switch_hero(target, target.hero))
            target.mover.position[:] = (x + dx, y, z + dz)
            targets.append(target)
        vitals = [match.combat.body_vitals(target.body, target) for target in targets]

        harness.commands(player, range(frame + 1, frame + 45))
        self.assertIsNone(slam.component.fault)
        self.assertFalse(slam.state(0).flags & nodes.Ability.RUNNING)
        hit_bodies = [target.body for target in targets[:2]]
        self.assertEqual(slam.vars[1182].value(), tuple(Entity(body) for body in hit_bodies))
        damage = slam.vars[476].value()
        self.assertGreater(damage, 1.0)
        for target in vitals[:2]:
            self.assertAlmostEqual(target.total, 200.0 - damage, places=4)
        self.assertEqual([target.total for target in vitals[2:]], [200.0, 200.0, 200.0])
        recipients = [notice["+0x78"]["+0x0"]["+0x0"] for notice in player.client.messages(combat.HIT)]
        self.assertEqual(recipients, hit_bodies)
