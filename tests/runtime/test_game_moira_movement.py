"""Run actual movement commands through Fade's authored movement modifiers."""

import math
import unittest
from unittest.mock import patch

import test_game_moira as fixtures
from test_game_combat import Run, aim

from ow174.game import combat, mei_wall
from ow174.game.collision import World
from ow174.game.commands import Command
from ow174.game.content import heroes
from ow174.game.mover import statescript_mods
from ow174.game.script import graph


class MoiraFadeMovementTests(unittest.TestCase):
    setup_fight = fixtures.MoiraSprayTests.setup_fight

    @staticmethod
    def moving_step(run, source, *, buttons=0, forward=127):
        run.frame += 1
        run.match.update(run.frame * combat.TICK, run.frame)
        for player in run.players:
            player.take_commands([Command(run.frame, forward=forward if player is source else 0,
                                          yaw=0, pitch=0, buttons=buttons if player is source else 0)])

    def test_fade_accelerates_actual_movement_and_releases_speed_at_end(self):
        run, source, _target, _vitals = self.setup_fight()
        start = tuple(source.mover.position)
        speeds = []
        for frame in range(90):
            self.moving_step(run, source, buttons=8 if frame == 0 else 0)
            speeds.append(math.hypot(source.mover.velocity[0], source.mover.velocity[2]))
        self.assertGreater(max(speeds), 19)
        self.assertLessEqual(max(speeds), 19.251)
        self.assertLessEqual(speeds[-1], 5.501)
        self.assertGreater(source.mover.position[2] - start[2], 12)

    def test_delayed_movement_replays_fade_speed_until_its_original_end(self):
        samples = []
        for delayed in (False, True):
            run, source, _, _ = self.setup_fight()
            self.moving_step(run, source, buttons=8)
            for offset in range(1, 61):
                if delayed and offset < 60:
                    run.frame += 1
                    run.match.update(run.frame * combat.TICK, run.frame)
                else:
                    held = offset <= 32
                    self.moving_step(run, source, buttons=8 if held else 0, forward=127 if held else 0)
            samples.append((tuple(source.mover.position), source.mover.velocity))
        self.assertEqual(*samples)

    def test_actual224e_throttle_modifiers_stop_acceleration_and_restore_on_next_frame(self):
        for forward, right in ((127, 0), (-127, 0), (0, 127), (0, -127)):
            with self.subTest(forward=forward, right=right):
                run, source, _, _ = self.setup_fight()
                component = source.body_script.component
                instance = component.create(graph.graph(0x224E), init=False)
                instance.initialized = True  # selected actual scope, not full Sleep admission/host
                state = instance.state(62)  # actual node168: throttle17/18/19=0
                state.begin()
                component.run_now()
                self.assertTrue({17, 18, 19} <= {row[0] for row in statescript_mods(component).values()})

                def step(run=run, source=source, forward=forward, right=right):
                    run.frame += 1
                    run.match.update(run.frame * combat.TICK, run.frame)
                    for actor in run.players:
                        actor.take_commands([Command(run.frame, forward=forward if actor is source else 0,
                                                     right=right if actor is source else 0)])

                step()  # records produced by this body frame affect the following pose
                self.assertGreater(math.hypot(source.mover.velocity[0], source.mover.velocity[2]), 0)
                for _ in range(12):
                    step()
                self.assertAlmostEqual(math.hypot(source.mover.velocity[0], source.mover.velocity[2]), 0)
                self.assertEqual(source.mover.throttles, (right, forward))
                state.end(False)
                step()  # previously tracked scope still covers this pose
                self.assertAlmostEqual(math.hypot(source.mover.velocity[0], source.mover.velocity[2]), 0)
                step()
                self.assertGreater(math.hypot(source.mover.velocity[0], source.mover.velocity[2]), 0)

    def test_fade_does_not_cross_a_static_wall(self):
        run, source, _target, _vitals = self.setup_fight()
        x, y, z = source.mover.position
        plane = z + 2
        # Actual ground and wall geometry, not a patched collision result.
        run.match.collision_world = World([
            (x - 20, y, z - 20, x - 20, y, z + 20, x + 20, y, z + 20),
            (x - 20, y, z - 20, x + 20, y, z + 20, x + 20, y, z - 20),
            (x - 10, y - 5, plane, x + 10, y + 5, plane, x + 10, y - 5, plane),
            (x - 10, y - 5, plane, x - 10, y + 5, plane, x + 10, y + 5, plane),
        ])
        for frame in range(60):
            self.moving_step(run, source, buttons=8 if frame == 0 else 0)
        self.assertGreater(source.mover.position[2], z + 1)
        self.assertLess(source.mover.position[2], plane)

    def test_fade_jump_uses_the_authored_additive_jump_modifier(self):
        peaks = []
        for fade in (False, True):
            run, source, _target, _vitals = self.setup_fight()
            vertical = []
            for frame in range(20):
                buttons = 8 if fade and frame == 0 else 0x40 if frame == 5 else 0
                self.moving_step(run, source, buttons=buttons)
                vertical.append(source.mover.velocity[1])
            peaks.append(max(vertical))
        self.assertLess(peaks[0], 6.1)
        self.assertGreater(peaks[1], 11)
        self.assertGreater(peaks[1], peaks[0] + 5)

    def test_actual_moira_eye_heights_change_spray_occlusion_at_low_cover(self):
        for crouched in (False, True):
            with self.subTest(crouched=crouched):
                run, source, _target, vitals = self.setup_fight()
                x, y, z = source.mover.position
                z += 1.5
                # Cover top1.6m is below the authored standing eye1.7m and
                # above crouched eye1.1m. Generic1.55m gives the wrong outcome.
                cover = World([
                    (x - 10, y - 5, z, x + 10, y - 5, z, x + 10, y + 1.6, z),
                    (x - 10, y - 5, z, x + 10, y + 1.6, z, x - 10, y + 1.6, z),
                ])
                buttons = 0x80 if crouched else 0
                for _ in range(20):
                    run.step({source: (0, 0, buttons)})
                with patch.object(run.match.combat, "_collision", return_value=cover):
                    for _ in range(40):
                        run.step({source: (0, 0, buttons | 1)})
                if crouched:
                    self.assertEqual(vitals.total, 100)
                else:
                    self.assertGreater(vitals.total, 100)


    def deploy_wall(self, *, friendly, overlap=False, ceiling=False, fade=False):
        run = Run(names=("Moira", "Mei"))
        match = run.match
        match.bots.clear()
        match._pending_bot_scripts.clear()
        source, mei = run.players
        self.assertTrue(match.switch_hero(source, heroes()[fixtures.MOIRA]))
        self.assertTrue(match.switch_hero(mei, heroes()[mei_wall.MEI]))
        mei.team = source.team if friendly else 1 - source.team
        x, y, z = source.mover.position
        mei.mover.position = [x, y, z + 9]
        mei.mover.body = None
        if overlap:
            source.mover.position = [x, y, z + 2]
            source.mover.body = None
        if ceiling:
            high = y + 4.7
            match.collision_world = World([
                (x-20, y, z-20, x-20, y, z+20, x+20, y, z+20),
                (x-20, y, z-20, x+20, y, z+20, x+20, y, z-20),
                (x-.6, high, z+1.4, x+.6, high, z+1.4, x+.6, high, z+2.6),
                (x-.6, high, z+1.4, x+.6, high, z+2.6, x-.6, high, z+2.6),
            ])
        for _ in range(40):
            run.step({})
        fight = match.combat
        direction = aim(fight._eye(mei), (x, y, z + 2))
        for button in (16, 0, 1, 0):
            commands = {mei: (*direction, button)}
            if fade and button == 16:
                commands[source] = (0, 0, 8)
            run.step(commands)
        self.assertEqual(len(fight.walls.pieces), 4 if ceiling else 5)
        pillar = min(fight.walls.pieces.values(), key=lambda piece: abs(piece.feet[0] - x))
        return run, source, mei, direction, pillar

    def test_actual_wall_blocks_walking_and_active_fade_until_cancel(self):
        for friendly in (False, True):
            for fade in (False, True):
                with self.subTest(friendly=friendly, fade=fade):
                    run, source, mei, direction, pillar = self.deploy_wall(friendly=friendly)
                    mover = source.mover
                    radius, _ = mover._capsule()
                    face = pillar.feet[2] - pillar.depth - radius
                    start = mover.position[2]
                    for frame in range(28):
                        self.moving_step(run, source, buttons=8 if fade and frame == 0 else 0)
                        self.assertLessEqual(mover.position[2], face + 0.002)
                    self.assertGreater(mover.position[2], start + 0.5)
                    self.assertAlmostEqual(mover.position[2], face, places=3)
                    self.assertAlmostEqual(mover.velocity[2], 0, places=3)
                    self.assertEqual(run.match.combat.damage_immune(source), fade)
                    self.assertEqual(pillar.health, 400)
                    run.step({mei: (*direction, 16)})
                    self.assertFalse(run.match.combat.walls.pieces)
                    for _ in range(30):
                        self.moving_step(run, source)
                    self.assertGreater(mover.position[2], pillar.feet[2] + pillar.depth)

    def test_actual_wall_top_supports_moira_and_cancel_releases_her(self):
        run, source, mei, direction, pillar = self.deploy_wall(friendly=False)
        x, y, z = pillar.feet
        top = y + pillar.height
        source.mover.position = [x, top + 1, z]
        source.mover.body = None
        for _ in range(40):
            self.moving_step(run, source, forward=0)
        self.assertAlmostEqual(source.mover.position[1], top, places=2)
        self.assertFalse(source.mover.airborne)
        run.step({mei: (*direction, 16)})
        for _ in range(40):
            self.moving_step(run, source, forward=0)
        self.assertLess(source.mover.position[1], top - 1)


    def test_deploying_wall_lifts_overlapping_moira_during_fade(self):
        run, source, mei, direction, pillar = self.deploy_wall(friendly=False, overlap=True, fade=True)
        fight = run.match.combat
        top = pillar.feet[1] + pillar.height
        self.assertTrue(fight.damage_immune(source))
        self.assertAlmostEqual(source.mover.position[1], top, places=2)
        self.assertFalse(source.mover.airborne)
        for _ in range(20):
            run.step({})
        self.assertAlmostEqual(source.mover.position[1], top, places=2)
        run.step({mei: (*direction, 16)})
        self.assertTrue(fight.damage_immune(source))
        for _ in range(60):
            run.step({})
        self.assertAlmostEqual(source.mover.position[1], pillar.feet[1], places=2)
        self.assertFalse(fight.damage_immune(source))

    def test_deploying_wall_omits_column_when_lift_would_hit_ceiling(self):
        run, source, mei, direction, _ = self.deploy_wall(friendly=True, overlap=True, ceiling=True)
        x, y, z = source.mover.position
        pieces = run.match.combat.walls.pieces
        self.assertTrue(all(abs(piece.feet[0] - x) > 1 for piece in pieces.values()))
        self.assertAlmostEqual(y, mei.mover.position[1], places=2)
        self.assertFalse(source.mover.airborne)
        for _ in range(24):
            self.moving_step(run, source)
        self.assertGreater(source.mover.position[2], z + 1)
        run.step({mei: (*direction, 16)})
        self.assertFalse(pieces)
