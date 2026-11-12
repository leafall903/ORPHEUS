"""Focused wire checks; original-client visual acceptance is separate."""

import unittest
from unittest.mock import patch

from test_orisa_combat import OrisaCombatTests
from test_orisa_barrier import BarrierTests
from ow174.game import orisa_presentation, world


class OrisaPresentationTests(unittest.TestCase):
    def test_original_projectile_definitions_are_published_to_owner_without_lease(self):
        for button, definition in ((1, 0x04000000000017EF), (2, 0x040000000000172C),
                                   (16, 0x0400000000001846)):
            with self.subTest(button=button):
                run, player, _ = OrisaCombatTests().arena()
                for frame in range(40):
                    run.step({player: (0, 0, button if frame == 0 else 0)})
                    if run.match.combat.rockets:
                        break
                shot = run.match.combat.rockets[0].shot
                self.assertIsNotNone(shot)
                self.assertTrue(shot.authoritative)
                delivery = next(d for d in shot.observers if d.viewer is player)
                self.assertIs(delivery.stream, player.body_script.stream)
                create = delivery.pending_create or next(
                    u for u in player.client.entities
                    if u.entity == delivery.record.ids[0] and u.op == world.OP_CREATE)
                with patch.object(world, "create", return_value=None) as build:
                    create.build(0)
                self.assertEqual(build.call_args.args[1], definition)
                run.match.combat.projectiles.flown(shot, .1)
                self.assertTrue(delivery.finished)
                self.assertTrue(delivery.explode)

    def test_deployed_barrier_publishes_original_definition_vm_and_destroy(self):
        run, player, _, _, shield = BarrierTests().deploy()
        creates = [u for u in player.client.entities
                   if u.entity == shield.entity and u.op == world.OP_CREATE]
        self.assertEqual(len(creates), 1)
        with patch.object(world, "create", return_value=None) as build:
            creates[0].build(0)
        self.assertEqual(build.call_args.args[1], 0x04000000000014A1)
        self.assertEqual(build.call_args.kwargs["position"], shield.position)
        self.assertIs(orisa_presentation.projection(run.match.combat, shield.entity).component,
                      shield.component)
        run.match.combat.orisa_barriers.remove(shield.entity)
        run.step({})
        self.assertTrue(any(u.entity == shield.entity and u.op == world.OP_DESTROY
                            for u in player.client.entities))
