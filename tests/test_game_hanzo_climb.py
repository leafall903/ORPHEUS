"""Actual wall-climb commands under explicitly reconstructed contact/budget rules."""

import unittest
from unittest.mock import patch

import test_game_hanzo_movement as movement_tests
import test_server_snapshot as snapshot_tests
from test_game_mover import box, wall

from ow174.game import bodytrace, hanzo_movement, movelog
from ow174.game.collision import World
from ow174.game.commands import JUMP, Command
from ow174.game.content import SOLDIER, heroes
from ow174.game.script import runtime
from ow174.game.server_snapshot import snapshot_digest


class HanzoClimbTests(unittest.TestCase):
    def setUp(self):
        for module in (bodytrace, movelog):
            self.enterContext(patch.object(module, "ENABLED", False))

    step = staticmethod(movement_tests.HanzoLungeTests.step)

    def arena(self, distance=0.7, ceiling=False):
        run, hero, victim, instance = movement_tests.HanzoLungeTests().arena()
        victim.spawned = False
        x, y, z = hero.mover.position
        geometry = box(x - 20, x + 20, y - 1, y, z - 20, z + 20)
        if distance is not None:
            geometry += wall(x - 10, z + distance, x + 10, z + distance, y0=y, y1=y + 20)
        if ceiling:
            geometry += box(x - 5, x + 5, y + 3, y + 3.2, z - 5, z + 5)
        run.match.collision_world = World(geometry, name="declared Hanzo climb floor/wall/ceiling fixture")
        return run, hero, instance

    def start(self, run, hero):
        self.step(run, hero, buttons=JUMP)
        self.step(run, hero, buttons=JUMP)
        self.assertTrue(hero.body_script.world.hanzo_climb.active)

    def test_held_jump_climbs_with_authored_speed_and_bounded_airborne_budget(self):
        run, hero, instance = self.arena()
        self.step(run, hero, buttons=JUMP)
        start = hero.mover.position[1]
        for _ in range(94):
            self.step(run, hero, buttons=JUMP)
        progress = hero.body_script.world.hanzo_climb
        self.assertEqual(progress.elapsed, 1.5)
        self.assertTrue(progress.active)
        self.assertAlmostEqual(hero.mover.position[1] - start, 4.5 * 1.5, delta=0.03)
        self.assertTrue(instance.component.entity_var(1204).value())
        self.assertTrue(instance.state(instance.graph.node(15).state).active)
        end = hero.mover.position[1]
        for _ in range(10):
            self.step(run, hero, buttons=JUMP)
        self.assertFalse(progress.active)
        self.assertEqual(progress.elapsed, 1.5)
        self.assertLess(hero.mover.position[1], end)
        self.assertFalse(instance.component.entity_var(1204).value())
        self.assertTrue(instance.vars[1347].value())

    def test_miss_out_of_range_and_sideways_facing_do_not_climb(self):
        for distance, yaw in ((None, 0), (1.1, 0), (0.7, 16384)):
            with self.subTest(distance=distance, yaw=yaw):
                run, hero, instance = self.arena(distance=distance)
                for _ in range(24):
                    self.step(run, hero, buttons=JUMP, yaw=yaw)
                self.assertEqual(hero.body_script.world.hanzo_climb.elapsed, 0)
                self.assertFalse(instance.component.entity_var(1204).value())

    def test_release_lost_contact_and_authored_disable_gate_end_climb(self):
        for condition in ("release", "turn", "disable"):
            with self.subTest(condition=condition):
                run, hero, instance = self.arena()
                self.start(run, hero)
                progress = hero.body_script.world.hanzo_climb
                used = progress.elapsed
                if condition == "disable":
                    # Explicit supplied authored gate, not an enemy-control implementation.
                    instance.component.set(1956, True)
                    instance.component.run_now()
                self.step(run, hero, buttons=0 if condition == "release" else JUMP,
                          yaw=16384 if condition == "turn" else 0)
                self.assertFalse(progress.active)
                self.assertEqual(progress.elapsed, used)
                self.assertLess(hero.mover.velocity[1], 0)
                self.assertFalse(instance.component.entity_var(1204).value())

    def test_landing_resets_spent_budget_and_fresh_jump_climbs_again(self):
        run, hero, instance = self.arena()
        self.start(run, hero)
        self.step(run, hero)
        for _ in range(100):
            self.step(run, hero)
            if not hero.mover.airborne:
                break
        self.step(run, hero)
        self.assertFalse(hero.mover.airborne)
        self.assertEqual(hero.body_script.world.hanzo_climb.elapsed, 0)
        self.assertFalse(instance.vars[1347].value())
        self.start(run, hero)
        self.assertGreater(hero.mover.velocity[1], 4.4)

    def test_ceiling_sweep_stops_climb_without_passing_through(self):
        run, hero, instance = self.arena(ceiling=True)
        floor = hero.mover.position[1]
        self.start(run, hero)
        for _ in range(30):
            self.step(run, hero, buttons=JUMP)
        progress = hero.body_script.world.hanzo_climb
        self.assertFalse(progress.active)
        self.assertTrue(progress.blocked)
        self.assertLess(hero.mover.position[1], floor + 1.01)
        self.assertFalse(instance.component.entity_var(1204).value())

    def test_profile_values_drive_speed_and_duration(self):
        run, hero, instance = self.arena()
        row = instance.graph.node(21).field("m_82B8DE3B")[0]
        with patch.dict(row, {
            "m_82BF9656": {"$": "STUConfigVarFloat", "m_value": 2.25},
            "m_21411EEE": {"$": "STUConfigVarFloat", "m_value": 0.25},
        }):
            self.step(run, hero, buttons=JUMP)
            start = hero.mover.position[1]
            for _ in range(16):
                self.step(run, hero, buttons=JUMP)
            self.assertEqual(hero.body_script.world.hanzo_climb.elapsed, 0.25)
            self.assertAlmostEqual(hero.mover.position[1] - start, 2.25 * 0.25, delta=0.01)
            self.step(run, hero, buttons=JUMP)
            self.assertFalse(hero.body_script.world.hanzo_climb.active)

    def test_death_and_body_replacement_retire_active_climb(self):
        for death in (False, True):
            with self.subTest(death=death):
                run, hero, _ = self.arena()
                self.start(run, hero)
                old = hero.body_script.world.hanzo_climb
                if death:
                    target = next(t for t in run.match.combat._targets() if t.owner is hero)
                    run.match.combat._damage(run.players[1], target, 200, False, None, run.frame)
                    self.assertTrue(run.match.combat.is_dead(hero))
                    self.assertFalse(old.active)  # immediate, without another input frame
                    pose = tuple(hero.mover.position)
                    self.step(run, hero, buttons=JUMP)
                    self.assertEqual(tuple(hero.mover.position), pose)
                else:
                    run.match.switch_hero(hero, heroes()[SOLDIER])
                    self.assertFalse(old.active)
                    self.assertIsNone(hero.body_script.world.hanzo_climb)

    def test_contiguous_and_missing_command_frames_agree_without_duplicate_budget(self):
        for gap in (24, 40):
            with self.subTest(gap=gap):
                results = []
                for skipped in (False, True):
                    run, hero, _ = self.arena()
                    self.step(run, hero, buttons=JUMP)
                    if skipped:
                        run.frame += gap
                        run.match.tick = run.frame
                        hero.take_commands([Command(run.frame, buttons=JUMP)])
                    else:
                        # Existing input recovery repeats held keys for32 frames,
                        # then releases them until the next real command arrives.
                        for offset in range(1, gap + 1):
                            self.step(run, hero, buttons=JUMP if offset <= 32 or offset == gap else 0)
                    progress = hero.body_script.world.hanzo_climb
                    result = tuple(hero.mover.position), progress.elapsed
                    hero.take_commands([Command(run.frame, buttons=JUMP)] * 3)
                    self.assertEqual((tuple(hero.mover.position), progress.elapsed), result)
                    results.append(result)
                self.assertEqual(results[0], results[1])

    def test_strict_mode_refuses_contact_host_and_budget_changes_stored_digest(self):
        run, hero, instance = self.arena()
        self.step(run, hero, buttons=JUMP)
        instance.component.strict = True
        with self.assertRaisesRegex(runtime.UnsupportedScriptError, "reconstructed Hanzo wall climb"):
            hanzo_movement.climb_request(hero, Command(run.frame + 1, buttons=JUMP), 0.016)
        case = snapshot_tests.ServerSnapshotTests()
        session, _, players = case.scene()
        world = players[0].body_script.world
        before = snapshot_digest(case.capture(session))
        world.hanzo_climb = hanzo_movement.Climb(active=True, elapsed=0.5)
        active = snapshot_digest(case.capture(session))
        self.assertNotEqual(before, active)
        world.hanzo_climb.elapsed = 0.6
        self.assertNotEqual(active, snapshot_digest(case.capture(session)))
