"""Original03BF/02A3 base views through actual commands and low-cover consumers."""

import unittest
from unittest.mock import patch

from test_game_combat import Run, aim

from ow174.game import combat, mccree, replay_manifest
from ow174.game.collision import World, flat_world
from ow174.game.commands import CROUCH
from ow174.game.content import heroes

ZARYA, MCCREE = 0x02E0000000000068, 0x02E0000000000042


class EyeHeightTests(unittest.TestCase):
    def scene(self, hero=ZARYA, *, crouched=False, wall_top=None, ally=False, distance=5):
        run = Run(names=("Source", "Target"))
        source, target = run.players
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        self.assertTrue(run.match.switch_hero(source, heroes()[hero]))
        self.assertTrue(run.match.switch_hero(target, heroes()[ZARYA]))
        source.mover.position[:] = [0, 1, 0]
        target.mover.position[:] = [0, 1, distance]
        target.team = source.team if ally else 1
        world = flat_world(1)
        run.match.collision_world = world
        for player in run.players:
            player.mover.use(world=world)
        for _ in range(24):
            run.step({source: (0, 0, CROUCH if crouched else 0)})
        if wall_top is not None:
            world = World([(-50, 1, -50, -50, 1, 50, 50, 1, 50),
                           (-50, 1, -50, 50, 1, 50, 50, 1, -50),
                           (-10, 1, 2, 10, 1, 2, 10, wall_top, 2),
                           (-10, 1, 2, 10, wall_top, 2, -10, wall_top, 2),
                           (-10, 1, 2, 10, wall_top, 2, 10, 1, 2),
                           (-10, 1, 2, -10, wall_top, 2, 10, wall_top, 2)])
            run.match.collision_world = world
            for player in run.players:
                player.mover.use(world=world)
        return run, source, target

    def health(self, run, player):
        fight = run.match.combat
        return fight.vitals_of(next(t for t in fight._targets() if t.owner is player)).total

    def test_source_target_and_history_use_same_authored_standing_and_crouched_height(self):
        for hero, high, low in ((ZARYA, 1.6, 1.12), (MCCREE, 1.425, 0.9975)):
            for crouched in (False, True):
                with self.subTest(hero=hero, crouched=crouched):
                    run, source, _ = self.scene(hero, crouched=crouched)
                    self.assertEqual(source.mover.crouched, crouched)
                    expected = low if crouched else high
                    fight = run.match.combat
                    self.assertAlmostEqual(fight._eye(source)[1] - source.mover.position[1], expected)
                    target = next(t for t in fight._targets() if t.owner is source)
                    self.assertEqual(target.eye, expected)
                    self.assertEqual(fight.history[source.body][-1][3], expected)

    def test_standing_zarya_beam_clears_cover_between_old_and_authored_eye(self):
        run, source, target = self.scene(wall_top=2.575)
        for _ in range(51):
            run.step({source: (0, 0, 1)})
        self.assertAlmostEqual(400 - self.health(run, target), 60)

    def test_crouched_zarya_beam_is_blocked_by_cover_below_old_fallback(self):
        for top, damage in ((None, 60), (2.135, 0)):
            with self.subTest(wall_top=top):
                run, source, target = self.scene(crouched=True, wall_top=top)
                for _ in range(51):
                    run.step({source: (0, 0, CROUCH | 1)})
                self.assertAlmostEqual(400 - self.health(run, target), damage)

    def test_mccree_standing_and_crouched_sources_respect_authored_cover_height(self):
        for crouched, top in ((False, 2.48), (True, 2.05)):
            for covered in (False, True):
                with self.subTest(crouched=crouched, covered=covered):
                    run, source, target = self.scene(MCCREE, crouched=crouched,
                                                     wall_top=top if covered else None)
                    flags = CROUCH if crouched else 0
                    run.step({source: (0, 0, flags | 1)})
                    for _ in range(40):
                        run.step({source: (0, 0, flags)})
                    if covered:
                        self.assertEqual(self.health(run, target), 400)
                    else:
                        self.assertLess(self.health(run, target), 400)

    def test_projected_selection_uses_source_and_recipient_eye_heights(self):
        run, source, target = self.scene(wall_top=2.575, ally=True)
        run.step({source: (0, 0, 16)})
        projection = next(i for i in source.body_script.component.instances.values()
                          if i.graph.index == 0x025C)
        self.assertTrue(projection.state(10).active)
        # A lower McCree eye puts the selection ray below this wall. Keeping
        # the old recipient eye while correcting only the source would admit it.
        run, source, target = self.scene(wall_top=2.54, ally=True)
        self.assertTrue(run.match.switch_hero(target, heroes()[MCCREE]))
        target.mover.position[:] = [0, 1, 5]
        run.step({source: (0, 0, 16)})
        projection = next(i for i in source.body_script.component.instances.values()
                          if i.graph.index == 0x025C)
        self.assertFalse(projection.state(10).active)

    def test_enemy_mccree_primary_body_and_reconstructed_head_policy_on_zarya(self):
        for height, damage, critical in ((2.05, 70, False), (2.65, 140, True)):
            with self.subTest(height=height):
                run, source, target = self.scene(MCCREE)
                yaw, pitch = aim(run.match.combat._eye(source), (0, height, 5))
                run.step({source: (yaw, pitch, 1)})
                for _ in range(40):
                    run.step({source: (yaw, pitch, 0)})
                self.assertAlmostEqual(400 - self.health(run, target), damage)
                hits = source.client.messages(combat.HIT)
                self.assertEqual(len(hits), 1)
                self.assertEqual(bool(hits[0]["+0x78"]["+0x8"] & combat.CRITICAL), critical)

    def test_enemy_mccree_fan_keeps_explicit_one_times_critical(self):
        run, source, target = self.scene(MCCREE)
        yaw, pitch = aim(run.match.combat._eye(source), (0, 2.65, 5))
        # Isolate the explicit critical multiplier from physical angular sampling.
        with patch.object(mccree, "fan_direction", side_effect=lambda p, s, d, n, t, a: d):
            run.step({source: (yaw, pitch, 2)})
            for _ in range(50):
                run.step({source: (yaw, pitch, 0)})
        hits = source.client.messages(combat.HIT)
        self.assertEqual(len(hits), 6)
        self.assertTrue(all(not (hit["+0x78"]["+0x8"] & combat.CRITICAL) for hit in hits))
        self.assertAlmostEqual(400 - self.health(run, target), 300)

    def test_actual_fan_rays_damage_zarya_and_exhaust_personal_barrier(self):
        for barrier in (False, True):
            with self.subTest(barrier=barrier):
                run, source, target = self.scene(MCCREE, distance=2)
                run.step({target: (0, 0, 8 if barrier else 0)})
                yaw, pitch = aim(run.match.combat._eye(source), (0, 2, 2))
                samples = []
                original = mccree.fan_direction

                def observe(player, state, forward, number, time, activation,
                            original=original, source=source, samples=samples):
                    ray = original(player, state, forward, number, time, activation)
                    if player is source and state.node.pos == 37:
                        samples.append((forward, ray))
                    return ray

                with patch.object(mccree, "fan_direction", side_effect=observe):
                    run.step({source: (yaw, pitch, 2)})
                    for _ in range(50):
                        run.step({source: (yaw, pitch, 0)})
                self.assertEqual(len(samples), 6)
                self.assertEqual(len({ray for _, ray in samples}), 6)
                self.assertTrue(all(forward != ray for forward, ray in samples))
                self.assertAlmostEqual(400 - self.health(run, target), 100 if barrier else 300)
                self.assertAlmostEqual(target.body_script.component.entity_var(87).value() or 0,
                                       40 if barrier else 0)

    def test_actual_far_fan_does_not_guarantee_six_hits_on_zarya(self):
        run, source, target = self.scene(MCCREE, distance=80)
        yaw, pitch = aim(run.match.combat._eye(source), (0, 2, 80))
        run.step({source: (yaw, pitch, 2)})
        for _ in range(50):
            run.step({source: (yaw, pitch, 0)})
        self.assertLess(400 - self.health(run, target), 90)

    def test_view_profiles_are_detached_and_sealed_in_replay_configuration(self):
        key = "ow174.game.combat.EYE_PROFILES"
        before = replay_manifest.runtime_configuration(quanta_us=16000, run_options={})
        original = combat.EYE_PROFILES
        self.assertEqual(before[key], original)
        with patch.object(combat, "EYE_PROFILES", ((0x04000000000003BF, 1.7, 1.12), *original[1:])):
            after = replay_manifest.runtime_configuration(quanta_us=16000, run_options={})
            self.assertNotEqual(before[key], after[key])
            self.assertEqual(before[key], original)
