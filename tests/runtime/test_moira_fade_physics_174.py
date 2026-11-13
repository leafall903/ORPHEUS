"""Fade's original PhysicsExplosion targets qualified props, not player movers."""

import json
import unittest
from pathlib import Path
from unittest.mock import patch

import test_game_moira as fixtures

from ow174.game import props
from ow174.game.commands import Command


class MoiraFadePhysics174Tests(unittest.TestCase):
    expected = json.loads((Path(__file__).resolve().parents[2]
                           / "fixtures/behavior/moira-fade-physics-174-v1.json").read_text())["expected"]

    def test_actual_fade_dispatches_pull_and_push_once_only_to_eligible_props(self):
        run, owner, target, hp = fixtures.MoiraSprayTests().setup_fight(ally=False, distance=1)
        match = run.match
        self.assertTrue(match.switch_hero(owner, owner.hero, preserve_pose=True, start_frame=9000))
        owner.commands.reset(last_frame=9000)
        sent = 0

        def step(button=0):
            nonlocal sent
            run.frame += 1
            match.update(run.frame * .016, run.frame)
            sent += 1
            owner.take_commands([Command(9000 + sent, buttons=button)])
            target.owner.take_commands([Command(run.frame)])

        for _ in range(20):
            step()
        x, y, z = owner.mover.position
        base = props.PlacedDynamicSphere(1, 0x0400000000001567, 0, (x + .75, y, z), .02)
        near = match.dynamic_spheres.register(base, inverse_mass=1)
        outside = match.dynamic_spheres.register(
            props.PlacedDynamicSphere(2, base.definition, 0, (x + 1.53, y, z), .02), inverse_mass=1)
        proxy = match.dynamic_spheres.register(
            props.PlacedDynamicSphere(3, base.definition, 0, base.centre, .02, category=0x18), inverse_mass=1)
        requests = []
        before = hp.total

        def delivered(request):
            requests.append((request, near.velocity, match.tick))

        with patch.object(match.collision_world, 'dispatch_physics_explosion', delivered, create=True):
            step(8)
            for _ in range(65):
                step()
        self.assertEqual(len(requests), self.expected["dispatch_count"])
        self.assertEqual([r.strength for r, _, _ in requests], self.expected["strengths"])
        self.assertLess(requests[0][1][0], 0)
        self.assertAlmostEqual(near.velocity[0], 0, places=6)
        for request, _, tick in requests:
            self.assertGreaterEqual(request.start_ms, tick * 16)
            self.assertLess(request.start_ms, (tick + 1) * 16)
            self.assertEqual(request.entity, owner.body)
            self.assertEqual(tuple(request.position), (x, y, z))
            self.assertEqual((request.radius, request.parameter, request.interval), (1.5, 0, 0))
        self.assertEqual(outside.velocity, (0, 0, 0))
        self.assertEqual(proxy.velocity, (0, 0, 0))
        self.assertEqual(match.combat.knockbacks.serial, 0)
        self.assertFalse(owner.mover.velocity_changes)
        self.assertFalse(target.owner.mover.velocity_changes)
        self.assertEqual(hp.total, before)
