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


class ForceReplayStorageTests(unittest.TestCase):
    def test_actual_server_retains_accepted_impulse_clock_and_retirement(self):
        import test_server_snapshot as stored
        from ow174.game.server_snapshot import snapshot_digest
        case = stored.ServerSnapshotTests()
        session, _, players = case.scene(two=True)
        player = players[1]
        fight = player.match.combat
        target = fight.player_target(player)
        fight._apply_pharah_impulses(((target, (0, 6, -4)),))
        self.assertEqual(fight.knockbacks.launches[player.body], fight.tick)
        def digest():
            return snapshot_digest(case.capture(session))
        baseline = digest()
        fight.knockbacks.launches[player.body] += 1
        self.assertNotEqual(digest(), baseline)
        fight.knockbacks.launches[player.body] -= 1
        self.assertEqual(digest(), baseline)
        fight.knockbacks.retire(player.body)
        self.assertNotIn(player.body, fight.knockbacks.launches)
