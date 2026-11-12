"""Normal Soldier and Reinhardt input against the reconstructed 104319 shield."""

import unittest
from unittest.mock import patch

import test_baptiste_combat as fixture
from test_baptiste_matrix import deploy_matrix
from test_game_combat import aim

from ow174.game import barrier, collision, combat
from ow174.game.content import heroes
from ow174.game.script.driver import ULT_COST


class BaptisteHelixBarrierTests(unittest.TestCase):
    def setUp(self):
        if barrier.mesh() is None:
            self.skipTest("requires the verified Reinhardt barrier model")

    def fire(
        self, *, capacity=1600, friendly=False, wall=False, victim=(0, 1, 4.75),
        matrix_origin=None, terminal_offset=None, visor=False,
    ):
        run, baptiste, reinhardt, soldier, fight, targets, _ = fixture.BaptisteCombatTests().setup_fight()
        run.match.switch_hero(reinhardt, heroes()[barrier.REINHARDT])
        if friendly:
            reinhardt.team = soldier.team
        for actor, position in ((baptiste, victim), (reinhardt, (1.25, 1, 4)), (soldier, (0, 1, 0))):
            actor.mover.position[:] = position
            actor.mover.body = None
        run.match.collision_world = collision.flat_world(1)
        if matrix_origin is not None:
            baptiste.team = soldier.team
            baptiste.mover.position[:] = matrix_origin
            deploy_matrix(run, baptiste)
            baptiste.mover.position[:] = (20, 1, 4.75)
        if wall:
            run.match.collision_world = collision.World([
                (-30, 1, -30, 30, 1, -30, 30, 1, 30), (-30, 1, -30, 30, 1, 30, -30, 1, 30),
                (-8, 0, 1, 8, 0, 1, 0, 12, 1),
            ])
        for _ in range(24):
            run.step({reinhardt: (-32768, 0, 2)})
        self.assertIn(reinhardt.body, fight.barrier.children)
        self.assertEqual(fight.barrier.health[reinhardt.body], 1600)
        if visor:
            script = soldier.body_script
            script.set_ult_charge(script.component.vars[ULT_COST].value())
            run.step({reinhardt: (-32768, 0, 2), soldier: (0, 0, 0x20)})
            self.assertIs(script.component.entity_var(842).value(), True)
            for _ in range(90):
                run.step({reinhardt: (-32768, 0, 2)})
        fight.barrier.health[reinhardt.body] = capacity
        yaw, pitch = aim(fight._eye(soldier), (0, 1.8, 4.75))
        fly = fight._fly
        terminal_checks = []

        def fly_with_terminal_clip(rocket):
            if terminal_offset is not None:
                reach = rocket.speed * rocket.left
                hit = fight.barrier.intersect(soldier, rocket.position, rocket.direction, reach)
                self.assertIsNotNone(hit)
                terminal = hit[0] + terminal_offset
                rocket.left = terminal / rocket.speed
                self.assertEqual(rocket.left * rocket.speed, terminal)
                # Fit the clipped flight in one skipped-tick sweep.
                rocket.last_tick = fight.tick - 20
                self.assertLess(rocket.left, 20 * combat.TICK)
                terminal_checks.append(terminal)
            fly(rocket)

        with (patch.object(fight.projectiles, "fired", return_value=None) as fired,
              patch.object(fight, "_fly", fly_with_terminal_clip)):
            for frame in range(45):
                run.step({reinhardt: (-32768, 0, 2), soldier: (yaw, pitch, 2 if frame == 0 else 0)})
        fired.assert_called_once()
        self.assertEqual(fired.call_args.args[3], 0x0400000000001C86 if visor else 0x040000000000072C)
        if terminal_offset is not None:
            self.assertEqual(len(terminal_checks), 1)
            self.assertFalse(fight.rockets)
        return (
            fight.vitals_of(targets[baptiste]).total,
            fight.barrier.health[reinhardt.body],
            reinhardt.body in fight.barrier.children,
        )

    def test_hostile_contact_and_same_impact_cover_when_barrier_breaks(self):
        for capacity, expected in ((1600, (200, 1480, True)), (120, (200, 0, False))):
            with self.subTest(capacity=capacity):
                self.assertEqual(self.fire(capacity=capacity), expected)

    def test_friendly_barrier_pass_through_and_nearer_solid_wall(self):
        self.assertEqual(self.fire(friendly=True), (80, 1600, True))
        self.assertEqual(self.fire(wall=True), (200, 1600, True))

    def test_incident_side_splash_still_hits(self):
        hp, capacity, active = self.fire(victim=(-0.8, 1, 2.5))
        self.assertEqual((hp, capacity, active), (120, 1480, True))

    def test_matrix_amplifies_only_when_crossed_before_the_barrier(self):
        for origin, remaining in (((0, 1, 0), 1360), ((0, 1, 4), 1480)):
            with self.subTest(matrix_origin=origin):
                self.assertEqual(self.fire(matrix_origin=origin), (200, remaining, True))

    def test_terminal_contact_includes_the_endpoint_without_extending_reach(self):
        for offset, capacity in ((-1e-5, 1600), (0, 1480), (1e-5, 1480)):
            with self.subTest(terminal_offset=offset):
                self.assertEqual(self.fire(terminal_offset=offset), (200, capacity, True))

    def test_active_visor_uses_the_authored_alternate_helix(self):
        self.assertEqual(self.fire(visor=True), (200, 1480, True))
