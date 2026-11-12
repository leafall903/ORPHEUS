"""Original accepted Pharah impacts across recipient input gaps."""
import unittest
from unittest.mock import patch
import test_game_sombra_combat as scenes
from test_game_combat import aim
from ow174.game import combat, pharah
from ow174.game.commands import Command
from ow174.game.content import heroes

class ForceWorldGapTests(unittest.TestCase):
    def arena(self):
        f = scenes.SombraCombatTests()
        f.setUp()
        match = f.run.match
        f.b.spawned = True
        match.switch_hero(f.b, heroes()[0x02E0000000000003])
        f.floor = [(-20, 0, -68, -20, 0, 106, 20, 0, 106),
                   (-20, 0, -68, 20, 0, 106, 20, 0, -68)]
        f.geometry()
        for player, position in ((f.a, (0, 0, 0)), (f.b, (0, 0, 6))):
            player.mover.position[:] = position
            player.mover.body = None
        f.press(0, 50)
        match.switch_hero(f.b, heroes()[pharah.HERO])
        f.b.mover.position[:] = (0, 0, 6)
        f.b.mover.body = None
        f.press(0, 50)
        f.b.body_script.set_ult_charge(0)
        return f, match.combat

    def play(self, button, frames, gap, *, toward=True):
        f, fight = self.arena()
        angles = aim(fight._eye(f.b), (0, 1.1, 0)) if toward else (0, 0)
        if button == 32:
            f.b.body_script.set_ult_charge(2100)
        with patch.object(fight, "_shoot", wraps=fight._shoot) as shots:
            f.run.step({f.b: (*angles, button)})
            if gap:
                f.run.frame += frames
                f.run.match.update(f.run.frame * combat.TICK, f.run.frame, frames=False)
                # World update leaves the endpoint for its real command.
                f.b.take_commands([Command(f.run.frame, yaw=angles[0], pitch=angles[1])])
            else:
                for _ in range(frames):
                    f.run.step({f.b: (*angles, 0)})
            count = shots.call_count
        return f, fight, count

    def test_primary_and_concussive_world_gap_match_damage_and_displacement(self):
        for button, frames, health in ((1, 30, 80), (16, 50, 200)):
            with self.subTest(button=button):
                samples = []
                for gap in (False, True):
                    f, fight, shots = self.play(button, frames, gap)
                    self.assertFalse(fight.rockets)
                    self.assertEqual(shots, 1)
                    self.assertEqual(f.health(f.a), health)
                    self.assertLess(f.a.mover.position[2], -1)
                    samples.append((tuple(f.a.mover.position), tuple(f.b.mover.position)))
                self.assertEqual(samples[0], samples[1])


    def test_foreign_victim_completed_endpoint_does_not_replay_late_fire(self):
        for button, frames in ((1, 30), (16, 50)):
            with self.subTest(button=button):
                f, fight, shots = self.play(button, frames, True)
                self.assertEqual(shots, 1)
                script = f.a.body_script
                self.assertEqual(fight.knockbacks.replayed_frames[f.a.body], (script, script.frame))
                position = tuple(f.a.mover.position)
                with patch.object(fight, '_shoot', wraps=fight._shoot) as fired:
                    f.a.take_commands([Command(script.frame, buttons=1)])
                self.assertEqual(fired.call_count, 0)
                self.assertEqual(tuple(f.a.mover.position), position)
                self.assertEqual(f.b.mover.last_command.frame, f.run.frame)


    def test_foreign_idle_pharah_is_a_victim_not_an_owned_action_exemption(self):
        original = self.arena
        def arena():
            f, fight = original()
            self.assertTrue(f.run.match.switch_hero(f.a, heroes()[pharah.HERO]))
            f.a.mover.position[:] = (0, 0, 0)
            f.a.mover.body = None
            f.press(0, 10)
            self.assertNotIn(f.a.body, fight.pharah_airborne)
            self.assertFalse(pharah.pending_release(f.a.body_script))
            self.assertFalse(pharah.hover_recovering(f.a.body_script))
            return f, fight
        samples = []
        with patch.object(self, 'arena', side_effect=arena):
            for gap in (False, True):
                f, fight, shots = self.play(1, 30, gap)
                self.assertEqual(shots, 1)
                self.assertEqual(f.health(f.a), 80)
                self.assertLess(f.a.mover.position[2], -1)
                self.assertNotIn(f.a.body, fight.pharah_airborne)
                samples.append((tuple(f.a.mover.position), tuple(f.b.mover.position)))
        self.assertEqual(samples[0], samples[1])


    def test_unknown_and_self_additions_keep_unretired_foreign_source(self):
        f, fight, _ = self.play(1, 30, True)
        target = fight.player_target(f.a)
        foreign = fight.knockbacks.launch_sources[target.entity]
        self.assertEqual(foreign, f.b.body)
        before = f.a.mover.velocity[2]
        fight._apply_pharah_impulses(((target, (0, 0, .5)),))
        self.assertEqual(fight.knockbacks.launch_sources[target.entity], foreign)
        fight._apply_pharah_impulses(((target, (0, 0, .5)),), source_entity=target.entity)
        self.assertEqual(fight.knockbacks.launch_sources[target.entity], foreign)
        self.assertEqual(f.a.mover.velocity[2], before + 1)
        fight.knockbacks.retire(target.entity)
        self.assertNotIn(target.entity, fight.knockbacks.launch_sources)

    def test_selected_own_jet_endpoint_remains_with_existing_pharah_driver(self):
        from test_delivered_pharah_replay import PharahReplayTests
        run, player, _ = PharahReplayTests().launch()
        fight = run.match.combat
        enemy = next(other for other in run.players if other is not player)
        self.assertIn(player.body, fight.pharah_airborne)
        self.assertIsNotNone(pharah.barrage_channel(player.body_script.component))
        fight._apply_pharah_impulses(((fight.player_target(player), (0, 2, -1)),), source_entity=enemy.body)
        self.assertIn(player.body, fight.knockbacks.launches)
        before = (player.body_script.frame, tuple(player.mover.position))
        fight.knockbacks.replay_launches(fight.tick + 1, combat.TICK, endpoint=True)
        self.assertEqual((player.body_script.frame, tuple(player.mover.position)), before)
        pharah.replay(fight, fight.tick + 1, combat.TICK)
        self.assertGreater(player.body_script.frame, before[0])


class ForceReplayStorageTests(unittest.TestCase):
    def test_actual_server_retains_accepted_impulse_clock_and_retirement(self):
        import test_server_snapshot as stored
        from ow174.game.server_snapshot import snapshot_digest
        case = stored.ServerSnapshotTests()
        session, _, players = case.scene(two=True)
        player = players[1]
        fight = player.match.combat
        target = fight.player_target(player)
        fight._apply_pharah_impulses(((target, (0, 6, -4)),), source_entity=players[0].body)
        self.assertEqual(fight.knockbacks.launch_sources[player.body], players[0].body)
        self.assertEqual(fight.knockbacks.launches[player.body], fight.tick)
        def digest():
            return snapshot_digest(case.capture(session))
        baseline = digest()
        source = fight.knockbacks.launch_sources[player.body]
        fight.knockbacks.launch_sources[player.body] = player.body
        self.assertNotEqual(digest(), baseline)
        fight.knockbacks.launch_sources[player.body] = source
        self.assertEqual(digest(), baseline)
        fight.knockbacks.launches[player.body] += 1
        self.assertNotEqual(digest(), baseline)
        fight.knockbacks.launches[player.body] -= 1
        self.assertEqual(digest(), baseline)
        fight.knockbacks.retire(player.body)
        self.assertNotIn(player.body, fight.knockbacks.launches)
        self.assertNotIn(player.body, fight.knockbacks.launch_sources)
