"""Static Ice Wall checks driven by actual Mei and Tracer inputs."""

import unittest
from dataclasses import replace

import test_game_tracer_mei_frost as frost_tests
import test_game_tracer_pulse_deflect as support_tests
import test_game_tracer_winston_barrier as bubble_tests
from test_game_combat import aim

from ow174.game.body_vm_snapshot import export_body_vm, snapshot_digest
from ow174.game.commands import FIRE, JUMP


class TracerMeiWallTests(unittest.TestCase):
    def setUp(self):
        self.fixture = frost_tests.TracerMeiFrostTests()
        self.fixture.setUp()
        self.step = self.fixture.step
        self.fight = self.fixture.fight
        self.tracer, self.mei = self.fixture.tracer, self.fixture.mei
        self.walls = self.fight.walls
        x, y, z = self.tracer.mover.position
        self.fixture.aim = aim(self.fight._eye(self.mei), (x, y, z + 1.5))

    def place(self):
        self.step(spray=16)
        self.assertTrue(self.walls.inputs[self.mei.body].preview)
        self.assertFalse(self.walls.pieces)
        self.step()
        self.step(spray=FIRE)
        self.step()
        self.assertEqual(len(self.walls.pieces), 5)
        return list(self.walls.pieces.values())[2]

    def test_pistols_damage_pillar_and_resume_after_break(self):
        wall = self.place()
        self.assertEqual(wall.health, 400)
        self.assertGreater(self.walls.instance(self.mei).state(33).remaining(), 11)
        health = self.fixture.case.health(self.mei)
        self.step(buttons=FIRE)
        self.assertEqual((wall.health, health.total), (388, 250))
        wall.health = 6
        for _ in range(12):
            self.step(buttons=FIRE)
            if wall.entity not in self.walls.pieces:
                break
        self.assertNotIn(wall.entity, self.walls.pieces)
        self.assertEqual(health.total, 244)
        self.assertEqual([p.health for p in self.walls.pieces.values()], [400] * 4)

    def test_melee_hits_wall_and_blink_stops_at_it(self):
        wall = self.place()
        self.step(buttons=0x800)
        for _ in range(40):
            self.step()
        self.assertEqual(wall.health, 370)
        self.assertEqual(self.fixture.case.health(self.mei).total, 250)
        origin = self.tracer.mover.position[2]
        self.step(buttons=8)
        self.assertGreater(self.tracer.mover.position[2], origin)
        self.assertLess(self.tracer.mover.position[2], wall.feet[2] - wall.depth)
        self.step(spray=16)
        self.assertFalse(self.walls.pieces)
        for _ in range(24):
            self.step()
        origin = self.tracer.mover.position[2]
        self.step(buttons=8)
        self.assertGreater(self.tracer.mover.position[2] - origin, 5)

    def throw(self):
        script = self.tracer.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        self.step(buttons=32)
        for _ in range(60):
            self.step()
            for bomb in self.fight.pulse_bombs.bombs:
                if bomb.stuck:
                    return bomb
        self.fail("actual Pulse Bomb did not stick")

    def throw_at(self, wall):
        bomb = self.throw()
        self.assertEqual(bomb.attached_pillar, wall.entity)
        self.assertIsNone(bomb.attached)
        return bomb

    def wall_and_bubble(self, centre_z):
        run = self.fixture.run
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        self.tracer.team, self.mei.team = 0, 1
        owner, _ = support_tests.PulseDeflectTests().support(run, self.mei, hero=0x02E0000000000009)
        owner.team = self.mei.team
        x, y, z = self.tracer.mover.position
        bubble_tests.TracerWinstonBarrierTests.move(owner, (x, y, z + centre_z))
        for _ in range(25):
            self.step()
        bubble = bubble_tests.TracerWinstonBarrierTests().cast(run, owner)
        return self.place(), bubble

    def test_pulse_bubble_before_ice_wall_blocks_contact_and_blast_to_pillar(self):
        wall, bubble = self.wall_and_bubble(5.4)
        bomb = self.throw()
        self.assertIs(bomb.attached_bubble, bubble)
        self.assertIsNone(bomb.attached_pillar)
        self.assertEqual(bubble.health, 695)
        for _ in range(70):
            self.step()
        self.assertNotIn(bomb, self.fight.pulse_bombs.bombs)
        self.assertEqual(bubble.health, 345)
        self.assertEqual(wall.health, 400)
        self.assertEqual(self.fixture.case.health(self.mei).total, 250)

    def test_ice_wall_covers_bubble_from_pistols_and_melee(self):
        wall, bubble = self.wall_and_bubble(7)
        self.step(buttons=FIRE)
        self.assertEqual(wall.health, 388)
        self.assertEqual(bubble.health, 700)
        self.step()
        self.step(buttons=0x800)
        for _ in range(40):
            self.step()
        self.assertEqual(wall.health, 358)
        self.assertEqual(bubble.health, 700)

    def test_pulse_ice_wall_before_bubble_blocks_contact_and_blast_to_bubble(self):
        wall, bubble = self.wall_and_bubble(7)
        bomb = self.throw_at(wall)
        self.assertIsNone(bomb.attached_bubble)
        self.assertEqual(wall.health, 395)
        for _ in range(70):
            self.step()
        self.assertNotIn(bomb, self.fight.pulse_bombs.bombs)
        self.assertEqual(wall.health, 45)
        self.assertEqual(bubble.health, 700)
        self.assertEqual(self.fixture.case.health(self.mei).total, 250)

    def test_pulse_attaches_and_breaks_pillar_without_leaking_blast(self):
        wall = self.place()
        wall.health = 350
        health = self.fixture.case.health(self.mei)
        bomb = self.throw_at(wall)
        self.assertEqual(wall.health, 345)
        self.assertEqual(health.total, 250)
        self.assertGreater(bomb.fuse_left, 0.98)
        for _ in range(40):
            self.step()
        self.assertIn(wall.entity, self.walls.pieces)
        self.assertIn(bomb, self.fight.pulse_bombs.bombs)
        for _ in range(30):
            self.step()
        self.assertNotIn(bomb, self.fight.pulse_bombs.bombs)
        self.assertNotIn(wall.entity, self.walls.pieces)
        self.assertEqual(health.total, 250)

    def test_pulse_keeps_fuse_at_contact_after_manual_wall_removal(self):
        wall = self.place()
        bomb = self.throw_at(wall)
        anchor, fuse = bomb.position, bomb.fuse_left
        self.step(spray=16)
        self.step()
        self.assertIsNone(bomb.attached_pillar)
        self.assertEqual(bomb.position, anchor)
        self.assertAlmostEqual(bomb.fuse_left, fuse - 0.032)
        self.assertFalse(self.walls.pieces)
        for _ in range(65):
            self.step()
        self.assertNotIn(bomb, self.fight.pulse_bombs.bombs)
        self.assertTrue(self.fixture.case.health(self.mei).dead)

    def test_earthshatter_stopped_front_does_not_restart_when_ice_wall_drops(self):
        for removal in ("early", "late"):
            with self.subTest(removal=removal):
                self.setUp()
                self.place()
                run = self.fixture.run
                caster, _ = support_tests.PulseDeflectTests().support(
                    run, self.tracer, hero=0x02E0000000000007,
                )
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                self.tracer.team = self.mei.team = 0
                caster.team = 1
                x, y, z = self.tracer.mover.position
                bubble_tests.TracerWinstonBarrierTests.move(caster, (x, y, z + 8))
                bubble_tests.TracerWinstonBarrierTests.move(self.mei, (x + 6, y, z + 3))
                script = caster.body_script
                script.set_ult_charge(script.component.entity_var(338).value())
                run.step({caster: (-32768, 0, 32)})
                self.assertEqual(script.component.entity_var(342).value(), 0)
                if removal == "late":
                    for _ in range(65):
                        wave = self.fight.earthshatters.waves.get(caster.body)
                        if wave is not None and wave.fronts[len(wave.fronts) // 2] is None:
                            break
                        run.step({caster: (-32768, 0, 0)})
                    self.assertIsNotNone(wave)
                    self.assertIsNone(wave.fronts[len(wave.fronts) // 2])
                    self.assertEqual(self.fixture.health.total, 150)
                run.step({caster: (-32768, 0, 0), self.mei: (0, 0, 16)})
                self.assertFalse(self.walls.pieces)
                for _ in range(75):
                    run.step({caster: (-32768, 0, 0)})
                self.assertEqual(self.fixture.health.total, 100 if removal == "early" else 150)
                self.assertEqual(self.fight.is_stunned(self.tracer), removal == "early")
                self.assertFalse(self.fight.earthshatters.waves)

    def test_recall_reaches_clear_historical_position_across_new_wall(self):
        start = tuple(self.tracer.mover.position)
        for _ in range(200):
            self.step()
        self.step(buttons=8)
        self.assertGreater(self.tracer.mover.position[2] - start[2], 5)
        wall = self.place()
        self.assertLess(start[2], wall.feet[2] - wall.depth)
        self.assertGreater(self.tracer.mover.position[2], wall.feet[2] + wall.depth)
        self.step(buttons=16)
        world = self.tracer.body_script.world
        self.assertIsNotNone(world.recall_session)
        for _ in range(100):
            self.step()
            if world.recall_session is None:
                break
        self.assertIsNone(world.recall_session)
        self.assertAlmostEqual(self.tracer.mover.position[2], start[2])
        self.assertEqual(wall.health, 400)
        self.assertIn(wall.entity, self.walls.pieces)

    def rewind_setup(self):
        start = tuple(self.tracer.mover.position)
        self.fixture.aim = aim(self.fight._eye(self.mei), start)
        for _ in range(200):
            self.step()
        self.step(buttons=8)
        first_clear = tuple(self.tracer.mover.position)
        for _ in range(24):
            self.step()
        self.step(buttons=8)
        self.assertGreater(self.tracer.mover.position[2] - first_clear[2], 5)
        return start, first_clear

    def start_playback(self):
        self.step(buttons=16)
        world = self.tracer.body_script.world
        for _ in range(40):
            self.assertIsNotNone(world.recall_session)
            if world.recall_session.state.node.pos == 1:
                return
            self.step()
        self.fail("actual Recall never reached playback")

    def finish_recall(self):
        world = self.tracer.body_script.world
        self.assertIsNotNone(world.recall_session)
        for _ in range(100):
            self.step()
            if world.recall_session is None:
                break
        self.assertIsNone(world.recall_session)

    def test_active_recall_storage_tracks_candidate_positions(self):
        self.rewind_setup()
        self.start_playback()
        script = self.tracer.body_script
        session = script.world.recall_session
        self.assertTrue(session.later_positions)
        before = snapshot_digest(export_body_vm(script))
        session.later_positions = session.later_positions[1:]
        self.assertNotEqual(snapshot_digest(export_body_vm(script)), before)

    def test_recall_uses_first_clear_recorded_position_if_wall_appears_during_playback(self):
        start, first_clear = self.rewind_setup()
        self.fixture.health.damage(40)
        self.start_playback()
        wall = self.place()
        self.assertTrue(self.walls.occupies(self.tracer.mover, start))
        self.finish_recall()
        self.assertAlmostEqual(self.tracer.mover.position[2], first_clear[2])
        self.assertFalse(self.walls.occupies(self.tracer.mover, self.tracer.mover.position))
        self.assertEqual(self.fixture.health.total, 150)
        self.assertIn(wall.entity, self.walls.pieces)

    def test_recall_uses_original_position_when_wall_is_removed_during_playback(self):
        start, _ = self.rewind_setup()
        self.place()
        self.assertTrue(self.walls.occupies(self.tracer.mover, start))
        self.start_playback()
        self.step(spray=16)
        self.assertFalse(self.walls.pieces)
        self.finish_recall()
        self.assertAlmostEqual(self.tracer.mover.position[2], start[2])

    def test_recall_retains_lifted_position_when_all_recorded_destinations_are_occupied(self):
        start = tuple(self.tracer.mover.position)
        self.fixture.aim = aim(self.fight._eye(self.mei), start)
        for _ in range(200):
            self.step()
        self.start_playback()
        wall = self.place()
        self.assertTrue(self.walls.occupies(self.tracer.mover, start))
        self.finish_recall()
        self.assertAlmostEqual(self.tracer.mover.position[1], wall.feet[1] + wall.height, places=3)
        self.assertFalse(self.walls.occupies(self.tracer.mover, self.tracer.mover.position))

    def test_wall_lifts_tracer_and_walking_off_lands_on_the_floor(self):
        start = tuple(self.tracer.mover.position)
        self.fixture.aim = aim(self.fight._eye(self.mei), start)
        wall = self.place()
        top = wall.feet[1] + wall.height
        self.assertAlmostEqual(self.tracer.mover.position[1], top, places=3)
        for _ in range(24):
            self.step()
            self.assertAlmostEqual(self.tracer.mover.position[1], top, places=3)
        self.step(buttons=JUMP)
        self.assertGreater(self.tracer.mover.position[1], top + 0.05)
        for _ in range(50):
            self.step()
        self.assertAlmostEqual(self.tracer.mover.position[1], top, places=3)
        for _ in range(100):
            self.step(forward=127)
        self.assertGreater(self.tracer.mover.position[2] - wall.feet[2], 2)
        self.assertAlmostEqual(self.tracer.mover.position[1], start[1], places=3)
        self.assertIn(wall.entity, self.walls.pieces)

    def test_expiry_and_cooldown_reject_early_recast(self):
        wall = self.place()
        while self.fixture.run.frame < wall.expires - 1:
            self.step()
        self.assertEqual(len(self.walls.pieces), 5)
        self.step()
        self.assertFalse(self.walls.pieces)
        self.step(spray=16)
        self.assertFalse(self.walls.inputs[self.mei.body].preview)
        self.assertGreater(self.walls.instance(self.mei).state(33).remaining(), 0)


if __name__ == "__main__":
    unittest.main()
