"""104319 mesh with declared server anchor; original attachment/wire stay open."""

import unittest
from dataclasses import replace

import test_genji_barrier_factory as factory_tests
import test_genji_pools as pool_tests
from test_game_mover import FLOOR, wall
from test_genji_combat import GENJI, GenjiFixture

from ow174.game import barrier_geometry, barriers, combat
from ow174.game.collision import World
from ow174.game.content import SOLDIER, heroes
from ow174.game.mover import CROUCHED


class GenjiBarrierCollisionTests(GenjiFixture):
    raise_barrier = factory_tests.GenjiBarrierFactoryTests.raise_barrier

    def scene(self, distance=4, genji_z=0, genji_x=0):
        run, genji, rein, _ = pool_tests.GenjiPoolTests.scene(self, "Reinhardt", distance)
        # Set the initial pose before the mover constructs its CharacterBody.
        genji.mover.position[2] = genji_z
        genji.mover.position[0] = genji_x
        for _ in range(90):
            run.step({})
        return run, genji, rein

    def setUp(self):
        super().setUp()
        if not barrier_geometry.load()[0]:
            self.skipTest("external 104319 barrier mesh unavailable")

    def shoot(self, run, genji, rein, button=1, frames=60):
        for frame in range(frames):
            run.step({genji: (0, 0, button if frame == 0 else 0), rein: (-32768, 0, 2)})

    def test_primary_and_fan_hit_independent_shield_without_hero_credit(self):
        for button in (1, 2):
            with self.subTest(button=button):
                run, genji, rein = self.scene()
                barrier = self.raise_barrier(run, rein)
                self.shoot(run, genji, rein, button)
                self.assertEqual(barrier.vitals.total, 1513)
                self.assertEqual(self.health(run, rein), 500)
                self.assertEqual(genji.body_script.component.entity_var(342).value(), 0)
                self.assertFalse(genji.client.messages(combat.HIT))
                self.assertFalse(run.match.combat.rockets)
                self.assertEqual(barrier.component.entity_var(253).value(), 1513)
                owner = barrier.source.instances[barrier.instance]
                self.assertEqual(owner.vars[586].value(), 1513)

    def test_primary_radius_clips_moving_shield_mesh_edge(self):
        for offset, expected in ((3.45, 1513), (3.55, 1600)):
            with self.subTest(offset=offset):
                run, genji, rein = self.scene(genji_x=offset)
                barrier = self.raise_barrier(run, rein)
                fight = run.match.combat
                self.assertIsNone(barriers.ray_hit(fight, genji, fight._eye(genji), (0, 0, 1), 10))
                self.shoot(run, genji, rein)
                self.assertEqual(barrier.vitals.total, expected)
                self.assertEqual(self.health(run, rein), 500)
                self.assertFalse(genji.client.messages(combat.HIT))

    def test_reflected_shuriken_hit_the_mesh_after_passing_it_outbound(self):
        run, genji = self.spawn(4, ("Deflector", "Reinhardt", "Shooter"))
        rein, shooter = run.players[1:]
        reinhardt = next(hero for hero in heroes().values() if hero.name == "Reinhardt")
        self.assertTrue(run.match.switch_hero(rein, reinhardt))
        self.assertTrue(run.match.switch_hero(shooter, heroes()[GENJI]))
        rein.mover.position[:] = [2, 1, 4]
        shooter.mover.position[:] = [0, 1, 6]
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        genji.team, rein.team, shooter.team = 0, 1, 1
        for _ in range(90):
            run.step({})
        barrier = self.raise_barrier(run, rein)
        for frame in range(60):
            run.step({
                genji: (0, 0, 16 if frame == 0 else 0),
                shooter: (-32768, 0, 1 if frame == 1 else 0),
                rein: (-32768, 0, 2),
            })
        self.assertEqual(barrier.vitals.total, 1513)
        self.assertEqual(barrier.component.entity_var(253).value(), 1513)
        self.assertEqual([self.health(run, player) for player in (genji, rein, shooter)],
                         [200, 500, 200])
        self.assertFalse(run.match.combat.rockets)
        self.assertFalse(genji.client.messages(combat.HIT))
        self.assertFalse(shooter.client.messages(combat.HIT))
        self.assertEqual(genji.body_script.component.entity_var(342).value(), 0)

    def test_current_mesh_pose_range_edges_friendly_and_free_for_all(self):
        run, genji, rein = self.scene()
        barrier = self.raise_barrier(run, rein)
        fight = run.match.combat
        origin = fight._eye(genji)
        hit = barriers.ray_hit(fight, genji, origin, (0, 0, 1), 10)
        self.assertIs(hit[1], barrier)
        self.assertGreater(hit[0], 2)
        self.assertLess(hit[0], 3.1)
        self.assertIsNone(barriers.ray_hit(fight, genji, origin, (0, 0, 1), 2))
        self.assertIsNone(barriers.ray_hit(fight, genji, (3.5, origin[1], 0), (0, 0, 1), 10))
        self.assertIsNone(barriers.ray_hit(fight, genji, (0, 5.5, 0), (0, 0, 1), 10))
        self.assertIsNotNone(barriers.ray_hit(fight, genji, (3.3, 2.8, 0), (0, 0, 1), 10))
        # Inside the axis-aligned bounds, outside the actual curved top corner.
        self.assertIsNone(barriers.ray_hit(fight, genji, (3.3, 4.6, 0), (0, 0, 1), 10))
        run.match.game_map = run.match.game_map.__class__(
            **{**run.match.game_map.__dict__, "free_for_all": False})
        rein.team = genji.team
        self.assertIsNone(barriers.ray_hit(fight, genji, origin, (0, 0, 1), 10))
        run.match.game_map = run.match.game_map.__class__(
            **{**run.match.game_map.__dict__, "free_for_all": True})
        self.assertIsNotNone(barriers.ray_hit(fight, genji, origin, (0, 0, 1), 10))
        rein.mover.position[0] = 10
        self.assertIsNone(barriers.ray_hit(fight, genji, origin, (0, 0, 1), 10))
        self.assertIsNone(barriers.ray_hit(fight, rein, origin, (0, 0, 1), 10))

    def test_mesh_yaw_pitch_and_crouched_anchor_follow_live_owner_pose(self):
        run, genji, rein = self.scene()
        self.raise_barrier(run, rein)
        fight = run.match.combat
        rein.mover.yaw = 16384
        distance, _ = barriers.ray_hit(fight, genji, (10, 2.8, 4), (-1, 0, 0), 20)
        self.assertGreater(distance, 8.9)
        self.assertLess(distance, 9.7)
        rein.mover.flags_word |= CROUCHED
        distance, _ = barriers.ray_hit(fight, genji, (10, 2.26, 4), (-1, 0, 0), 20)
        self.assertGreater(distance, 8.9)
        self.assertLess(distance, 9.7)
        rein.mover.yaw, rein.mover.pitch = 0, 8192
        rein.mover.flags_word &= ~CROUCHED
        root = 2 ** -0.5
        origin = (0, 2.8 - 11 * root, 4 + 11 * root)
        distance, _ = barriers.ray_hit(fight, genji, origin, (0, root, -root), 20)
        self.assertGreater(distance, 9.9)
        self.assertLess(distance, 10.7)

    def test_static_wall_precedes_shield(self):
        run, genji, rein = self.scene()
        barrier = self.raise_barrier(run, rein)
        run.match.collision_world = World(FLOOR + wall(-5, 2, 5, 2))
        self.shoot(run, genji, rein)
        self.assertEqual(barrier.vitals.total, 1600)
        self.assertEqual(self.health(run, rein), 500)

    def test_actual_soldier_hitscan_terminates_on_mesh(self):
        run, genji, rein = self.scene()
        run.match.switch_hero(genji, heroes()[SOLDIER])
        genji.mover.position[:] = [0, 1, 0]
        for _ in range(90):
            run.step({})
        barrier = self.raise_barrier(run, rein)
        self.shoot(run, genji, rein, frames=20)
        self.assertEqual(barrier.vitals.total, 1580)
        self.assertEqual(self.health(run, rein), 500)
        self.assertFalse(genji.client.messages(combat.HIT))

    def test_body_in_front_of_mesh_receives_rear_attack(self):
        run, genji, rein = self.scene(genji_z=6)
        barrier = self.raise_barrier(run, rein)
        for frame in range(60):
            run.step({genji: (-32768, 0, 1 if frame == 0 else 0), rein: (-32768, 0, 2)})
        self.assertEqual(barrier.vitals.total, 1600)
        self.assertEqual(self.health(run, rein), 428)

    def test_melee_strike_and_blade_pass_through_shield_mesh(self):
        for attack, damage in (("melee", 25), ("strike", 45), ("blade", 105)):
            with self.subTest(attack=attack):
                run, genji, rein = self.scene(distance=1.5)
                barrier = self.raise_barrier(run, rein)
                if attack == "blade":
                    genji.body_script.set_ult_charge(genji.body_script.component.entity_var(338).value())
                for frame in range(110):
                    button = ((32 if frame == 0 else (1 if frame > 70 else 0))
                              if attack == "blade" else ((0x800 if attack == "melee" else 8)
                                                         if frame == 0 else 0))
                    run.step({genji: (0, 0, button), rein: (-32768, 0, 2)})
                self.assertEqual(barrier.vitals.total, 1600)
                self.assertEqual(self.health(run, rein), 500 - damage)

    def test_lower_and_reraise_preserve_damage_and_authored_delayed_regeneration(self):
        run, genji, rein = self.scene()
        barrier = self.raise_barrier(run, rein)
        self.shoot(run, genji, rein)
        for _ in range(30):
            run.step({})
        self.assertTrue(barrier.retired)
        new = self.raise_barrier(run, rein)
        self.assertEqual(new.vitals.total, 1513)
        for _ in range(210):
            run.step({})
        newest = self.raise_barrier(run, rein)
        self.assertEqual(newest.vitals.total, 1600)

    def test_break_uses_actual_owner_message_and_rejects_immediate_raise(self):
        run, genji, rein = self.scene()
        barrier = self.raise_barrier(run, rein)
        self.assertEqual(barriers.damage(run.match.combat, barrier, 1700), 1600)
        self.assertTrue(barrier.retired)
        self.assertFalse(run.match.combat.barriers.scripted)
        owner = barrier.source.instances[barrier.instance]
        self.assertEqual(owner.vars[586].value(), 0)
        self.assertEqual(owner.vars[61].value(), 5)
        for _ in range(120):
            run.step({rein: (-32768, 0, 2)})
        self.assertFalse(run.match.combat.barriers.scripted)
        self.assertEqual(self.health(run, rein), 500)
        self.assertEqual(genji.body_script.component.entity_var(342).value(), 0)
        for _ in range(195):
            run.step({rein: (-32768, 0, 2)})
        self.assertEqual(owner.vars[61].value(), 0)
        run.step({})
        restored = self.raise_barrier(run, rein)
        self.assertGreater(restored.vitals.total, 0)
        self.assertLess(restored.vitals.total, 1600)


    def test_fan_resolves_each_pellet_when_reinhardt_shield_breaks(self):
        for remaining, body_hits in ((20, 2), (58, 1), (59, 0)):
            with self.subTest(remaining=remaining):
                run, genji, rein = self.scene(distance=1.5)
                barrier = self.raise_barrier(run, rein)
                # Seed shield health to isolate the boundary within a single fan.
                barriers.damage(run.match.combat, barrier, 1600 - remaining)
                owner = barrier.source.instances[barrier.instance]
                self.assertEqual(owner.vars[586].value(), remaining)
                self.shoot(run, genji, rein, button=2, frames=20)
                self.assertTrue(barrier.retired)
                self.assertEqual(owner.vars[586].value(), 0)
                self.assertGreater(owner.vars[61].value(), 0)
                # Each body hit loses five damage to Reinhardt's armor.
                self.assertEqual(self.health(run, rein), 500 - 24 * body_hits)
                hits = genji.client.messages(combat.HIT)
                self.assertEqual([hit["+0x78"]["+0x4"] for hit in hits], [24] * body_hits)
                self.assertEqual(genji.body_script.component.entity_var(342).value(), 24 * body_hits)
                self.assertFalse(run.match.combat.rockets)


if __name__ == "__main__":
    unittest.main()
