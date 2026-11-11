"""Actual pilot Q graph reaches scoped placement; mech/Matrix remain unhosted."""

import math
import unittest
from unittest.mock import patch

from test_game_combat import Run

from ow174.game.body_vm_snapshot import export_body_vm, snapshot_digest
from ow174.game.collision import World
from ow174.game.content import heroes
from ow174.game.dva import HERO
from ow174.game.script import expr, runtime


class DvaPlacementTests(unittest.TestCase):
    def arena(self):
        run = Run(names=('Pilot', 'Other'))
        player, other = run.players
        run.match.bots.clear()
        run.match.switch_hero(player, heroes()[HERO])
        x, y, z = player.mover.position
        other.mover.position = [x+20, y, z]
        other.mover.body = None
        return run, player, other

    def cast(self, run, player, *, yaw=0, pitch=0, charge=True, frames=100):
        script = player.body_script
        if charge:
            script.set_ult_charge(script.component.entity_var(338).value())
        calls = []
        original = runtime.Instance.run_node

        def observe(instance, node, *args, **kwargs):
            if instance.graph.index == 0x026A:
                calls.append((node.pos, node.cls))
            return original(instance, node, *args, **kwargs)

        with patch.object(runtime.Instance, 'run_node', observe):
            for frame in range(frames):
                run.step({player: (yaw, pitch, 0x20 if frame == 0 else 0)})
        instance = next(i for i in script.component.instances.values() if i.graph.index == 0x026A)
        state = next((s for s in instance.states.values() if s.node.pos == 38), None)
        return instance, state, calls

    def test_floor_query_uses_nested_direction_and_reach_and_follows_success(self):
        run, player, _ = self.arena()
        x, y, z = player.mover.position
        instance, state, calls = self.cast(run, player)
        self.assertEqual(state.queries, 1)
        self.assertEqual(state.result[::3], (True, 'clear'))
        point = instance.vars[1626].value()
        self.assertAlmostEqual(point.x, x)
        self.assertAlmostEqual(point.y, y)
        self.assertAlmostEqual(point.z, z+1.5)
        self.assertEqual(state.result[2], expr.Vec3(0, 1, 0))
        self.assertIn((93, 'STUStatescriptActionSetVar'), calls)
        # Scoped zero gate now reaches the factory. Actor data is optional in
        # this placement fixture; the separate factory suite checks its result.
        self.assertIn((15, 'STU_B6E3C77D'), calls)

    def test_actual_yaw_rotates_forward_offset_and_pitch_keeps_ground_search_vertical(self):
        for yaw, pitch in ((16384, 0), (0, 5000), (0, -5000)):
            with self.subTest(yaw=yaw, pitch=pitch):
                run, player, _ = self.arena()
                x, y, z = player.mover.position
                _, state, _ = self.cast(run, player, yaw=yaw, pitch=pitch)
                self.assertTrue(state.result[0])
                point = state.result[1]
                self.assertAlmostEqual(point.x-x, 1.5 if yaw else 0)
                self.assertAlmostEqual(point.z-z, 0 if yaw else 1.5)
                self.assertAlmostEqual(point.y, y)

    def test_missing_ground_takes_actual_failure_branch_and_keeps_factory_absent(self):
        run, player, _ = self.arena()
        run.match.collision_world = World([])
        instance, state, calls = self.cast(run, player)
        self.assertEqual(state.result, (False, None, None, 'no-ground'))
        self.assertEqual(state.queries, 1)
        self.assertIn((100, 'STU_3387AB5D'), calls)
        self.assertNotIn((93, 'STUStatescriptActionSetVar'), calls)
        self.assertIsNone(instance.vars[1626].value())
        self.assertFalse(any(cls == 'STU_B6E3C77D' for _, cls in calls))

    def test_steep_ground_rejects_the_authored_forty_five_degree_limit(self):
        run, player, _ = self.arena()
        x, y, z = player.mover.position
        rise = 10*math.tan(math.radians(60))
        run.match.collision_world = World([
            (x-10, y-rise, z-10, x-10, y-rise, z+10, x+10, y+rise, z+10),
            (x-10, y-rise, z-10, x+10, y+rise, z+10, x+10, y+rise, z-10),
        ])
        _, state, _ = self.cast(run, player)
        self.assertEqual(state.result, (False, None, None, 'slope'))

    def test_capsule_fit_rejects_low_ceiling_after_ground_ray_succeeds(self):
        run, player, _ = self.arena()
        x, y, z = player.mover.position
        ceiling = y+1.75
        run.match.collision_world = World([
            (x-5, y, z-5, x-5, y, z+5, x+5, y, z+5),
            (x-5, y, z-5, x+5, y, z+5, x+5, y, z-5),
            (x-5, ceiling, z-5, x+5, ceiling, z+5, x-5, ceiling, z+5),
            (x-5, ceiling, z-5, x+5, ceiling, z-5, x+5, ceiling, z+5),
        ])
        _, state, _ = self.cast(run, player)
        self.assertEqual(state.result, (False, None, None, 'blocked-volume'))

    def test_living_ally_and_enemy_bodies_block_placement(self):
        for friendly in (False, True):
            with self.subTest(friendly=friendly):
                run, player, other = self.arena()
                x, y, z = player.mover.position
                other.team = player.team if friendly else 1-player.team
                other.mover.position = [x, y, z+1.5]
                _, state, _ = self.cast(run, player)
                self.assertEqual(state.result, (False, None, None, 'blocked-body'))

    def test_insufficient_ultimate_charge_does_not_run_placement(self):
        run, player, _ = self.arena()
        _, state, calls = self.cast(run, player, charge=False)
        self.assertIsNone(state)
        self.assertNotIn((93, 'STUStatescriptActionSetVar'), calls)

    def test_query_count_and_stored_result_are_observable_vm_state(self):
        run, player, _ = self.arena()
        _, state, _ = self.cast(run, player)
        before = snapshot_digest(export_body_vm(player.body_script))
        state.queries += 1
        count = snapshot_digest(export_body_vm(player.body_script))
        self.assertNotEqual(before, count)
        state.result = (False, None, None, 'no-ground')
        self.assertNotEqual(count, snapshot_digest(export_body_vm(player.body_script)))


if __name__ == '__main__':
    unittest.main()
