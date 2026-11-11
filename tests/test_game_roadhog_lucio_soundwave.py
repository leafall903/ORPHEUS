"""Build 104319 Soundwave against Roadhog with real ability commands."""

import unittest
from dataclasses import replace

import test_game_roadhog_defenses as fixtures
import test_game_roadhog_lucio_barrier as barrier

from ow174.game import lucio_barrier
from ow174.game.collision import World
from ow174.game.content import heroes
from ow174.game.script import nodes


class RoadhogSoundwaveTests(unittest.TestCase):
    def scene(self, distance=2.5):
        return barrier.RoadhogSoundBarrierTests().scene(distance)

    def look(self, run, lucio, target):
        x, y, z = target.mover.position
        return fixtures.aim(run.match.combat._eye(lucio), (x, y + 1.2, z))

    def test_soundwave_preserves_melee_buffer_and_moves_its_origin(self):
        fixture = fixtures.RoadhogDefenseTests()
        run, hog, lucio, victim = self.scene(distance=4)
        fight = run.match.combat
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        hog.team, lucio.team, victim.team = 0, 1, 1
        x, y, z = hog.mover.position
        victim.mover.position[:] = (x, y, z + 1.9)
        victim.mover.body = None
        run.step({})
        weapon = next(i for i in hog.body_script.component.instances.values() if i.graph.index == 0x01C6)
        ammo = weapon.vars[53].value()
        caster_health = fixture.health(run, lucio)
        melee = (0, 0, 0x800)
        run.step({hog: melee})
        first = run.frame
        self.assertEqual(fixture.health(run, victim), 19970)
        self.assertEqual(fixture.health(run, lucio), caster_health)
        run.step({})
        run.step({hog: melee})
        run.step({hog: melee, lucio: (*self.look(run, lucio, hog), 2)})
        self.assertTrue(any(fight.impulses.velocity(hog.body)))
        while run.frame < first + 53:
            run.step({hog: melee})
        self.assertFalse(fight.is_stunned(hog))
        self.assertLess(hog.mover.position[2], z - 1.5)

        # Place the caster in range of the displaced Roadhog and leave the first victim in place.
        moved_x, moved_y, moved_z = hog.mover.position
        lucio.mover.position[:] = (moved_x, moved_y, moved_z + 1.5)
        lucio.mover.body = None
        while run.frame < first + 61:
            run.step({hog: melee})
        self.assertEqual(fixture.health(run, lucio), caster_health)
        self.assertEqual(fixture.health(run, victim), 19970)
        run.step({hog: melee})
        self.assertEqual(run.frame, first + 62)
        self.assertEqual(fixture.health(run, lucio), caster_health - 30)
        self.assertEqual(fixture.health(run, victim), 19970)
        for _ in range(65):
            run.step({hog: melee})
        self.assertEqual(fixture.health(run, lucio), caster_health - 30)
        self.assertEqual(fixture.health(run, victim), 19970)
        self.assertEqual(weapon.vars[53].value(), ammo)

    def test_press_hits_once_and_moves_roadhog_without_a_stun(self):
        run, hog, lucio, _ = self.scene()
        fight = run.match.combat
        before = fixtures.RoadhogDefenseTests().health(run, hog)
        _, y, z = hog.mover.position
        look = self.look(run, lucio, hog)
        run.step({lucio: (*look, 2)})
        self.assertAlmostEqual(sum(v * v for v in fight.impulses.velocity(hog.body)) ** .5, 17, places=5)
        for _ in range(10):
            run.step({lucio: (*look, 0)})
        self.assertEqual(fixtures.RoadhogDefenseTests().health(run, hog), before - 25)
        self.assertLess(hog.mover.position[2], z - 1)
        self.assertGreater(hog.mover.position[1], y)
        self.assertFalse(fight.is_stunned(hog))

    def test_range_cone_wall_and_ally_exclude_roadhog(self):
        for mode in ("range", "cone", "wall", "ally"):
            with self.subTest(mode=mode):
                run, hog, lucio, _ = self.scene(10 if mode == "range" else 4)
                fight = run.match.combat
                _, y, z = hog.mover.position
                before = fixtures.RoadhogDefenseTests().health(run, hog)
                look = self.look(run, lucio, hog)
                if mode == "cone":
                    look = (0, 0)
                elif mode == "wall":
                    wall = z + 2
                    run.match.collision_world = World([
                        (-30, y, -30, -30, y, 80, 30, y, 80),
                        (-30, y, -30, 30, y, 80, 30, y, -30),
                        (-15, y - 2, wall, 15, y + 8, wall, 15, y - 2, wall),
                        (-15, y - 2, wall, -15, y + 8, wall, 15, y + 8, wall),
                    ])
                elif mode == "ally":
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    lucio.team = hog.team = 0
                run.step({lucio: (*look, 2)})
                for _ in range(10):
                    run.step({})
                self.assertEqual(fixtures.RoadhogDefenseTests().health(run, hog), before)
                self.assertNotIn(hog.body, fight.impulses.live)
                self.assertEqual(hog.mover.position[2], z)

    def test_breather_halves_damage_without_losing_reduction_or_healing(self):
        totals = []
        for button in (0, 2):
            run, hog, lucio, _ = self.scene()
            fight = run.match.combat
            pool = barrier.RoadhogSoundBarrierTests().vitals(run, hog)
            pool.damage(400)
            fight._sync_body_health(hog, pool)
            run.step({hog: (0, 0, 16)})
            for _ in range(65):
                run.step({})
                if fight.incoming_damage_scale(hog) == .5 and pool.total > 200:
                    break
            self.assertEqual(fight.incoming_damage_scale(hog), .5)
            before = pool.total
            run.step({lucio: (*self.look(run, lucio, hog), button)})
            self.assertEqual(hog.body in fight.impulses.live, button == 2)
            for _ in range(18):
                run.step({})
            self.assertEqual(fight.incoming_damage_scale(hog), .5)
            self.assertGreater(pool.total, before)
            totals.append(pool.total)
        self.assertAlmostEqual(totals[0] - totals[1], 12.5)

    def test_soundwave_pushes_hook_owner_without_releasing_a_clear_tether(self):
        run, hog, lucio, victim = self.scene()
        fight = run.match.combat
        x, y, z = hog.mover.position
        lucio.mover.position[:] = (x, y, z - 4)
        victim.mover.position[:] = (x, y, z + 14)
        for actor in (lucio, victim):
            actor.mover.body = None
        for _ in range(12):
            run.step({})
        before = fixtures.RoadhogDefenseTests().health(run, victim)
        angles = self.look(run, hog, victim)
        run.step({hog: (*angles, 8)})
        for _ in range(50):
            run.step({hog: (*angles, 0)})
            if fight.hooks.controlled(victim.body):
                break
        self.assertTrue(fight.hooks.controlled(victim.body))
        run.step({lucio: (*self.look(run, lucio, hog), 2)})
        self.assertIn(hog.body, fight.impulses.live)
        self.assertTrue(fight.hooks.controlled(victim.body))
        run.step({})
        self.assertGreater(hog.mover.position[2], z)
        self.assertTrue(fight.hooks.controlled(victim.body))
        self.assertEqual(fixtures.RoadhogDefenseTests().health(run, victim), before - 30)

    def test_soundwave_does_not_interrupt_whole_hog(self):
        run, hog, lucio, victim = self.scene()
        fight = run.match.combat
        x, y, z = hog.mover.position
        lucio.mover.position[:] = (x, y, z - 3)
        lucio.mover.body = None
        for _ in range(12):
            run.step({})
        script = hog.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        ability, = script.ultimates
        look = self.look(run, hog, victim)
        run.step({hog: (*look, 32)})
        self.assertTrue(ability.flags & nodes.Ability.RUNNING)
        run.step({hog: (*look, 0), lucio: (*self.look(run, lucio, hog), 2)})
        self.assertIn(hog.body, fight.impulses.live)
        before = fixtures.RoadhogDefenseTests().health(run, victim)
        for _ in range(30):
            run.step({hog: (*look, 0)})
        self.assertTrue(ability.flags & nodes.Ability.RUNNING)
        self.assertLess(fixtures.RoadhogDefenseTests().health(run, victim), before)

    def test_projected_barrier_blocks_force_but_sound_barrier_absorbs_only_damage(self):
        for projected in (False, True):
            with self.subTest(projected=projected):
                run, hog, lucio, support = self.scene()
                fight = run.match.combat
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                hog.team = support.team = 0
                lucio.team = 1
                hero = (next(h for h in heroes().values() if h.name == "Zarya") if projected
                        else heroes()[lucio_barrier.HERO])
                self.assertTrue(run.match.switch_hero(support, hero))
                x, y, z = hog.mover.position
                support.mover.position[:] = (x + 4, y, z)
                support.mover.body = None
                pool = barrier.RoadhogSoundBarrierTests().vitals(run, hog)
                before = pool.total
                angles = self.look(run, support, hog)
                for _ in range(15):
                    run.step({support: (*angles, 0)})
                if projected:
                    run.step({support: (*angles, 16)})
                    bubble = fight.zarya_bubbles.for_target(hog)
                    self.assertIsNotNone(bubble)
                    self.assertEqual(bubble.health, 200)
                else:
                    barrier.RoadhogSoundBarrierTests().cast(run, support)
                    shield = pool.temporary_shields[lucio_barrier.POOL]
                    self.assertEqual(shield.now, 750)
                run.step({lucio: (*self.look(run, lucio, hog), 2)})
                self.assertEqual(sum(pool.now.values()), before)
                self.assertEqual(hog.body in fight.impulses.live, not projected)
                if projected:
                    self.assertEqual(bubble.health, 175)
                else:
                    self.assertEqual(shield.now, 750 - 25 - 2)
