"""Build 104319 Seismic Slam against Roadhog through real body commands."""

import unittest
from dataclasses import replace

import test_game_roadhog_rocket_punch as fixtures
from test_game_combat import aim
from test_game_mover import box

from ow174.game import doomfist_passive, stats
from ow174.game.collision import World
from ow174.game.content import heroes
from ow174.game.script import expr, nodes


class RoadhogSlamTests(unittest.TestCase):
    def scene(self, distance=10, *, aerial=False, wall=None):
        run, hog, doom, rear = fixtures.RoadhogRocketPunchTests().scene(distance, wall=wall)
        rear.mover.position[0] += 20
        rear.mover.body = None
        if aerial:
            doom.mover.position[1] += 7
            doom.mover.body = None
        for _ in range(5):
            run.step({})
        return run, hog, doom, rear

    def aim(self, run, doom, hog, *, aerial=False):
        x, y, z = hog.mover.position
        return aim(run.match.combat._eye(doom), (x, y, z + 3) if aerial else (x, y + 1.2, z))

    def health(self, run, actor):
        return self.vitals(run, actor).total

    def vitals(self, run, actor):
        fight = run.match.combat
        return fight.vitals_of(next(t for t in fight._targets() if t.owner is actor))

    def damage(self, cast):
        return expr.to_float(cast.state.instance.find_var(expr.INSTANCE, 476).value())

    def start(self, run, doom, look):
        run.step({doom: (*look, 16)})

    def tick_to_wave(self, run, doom, look):
        fight = run.match.combat
        for _ in range(95):
            run.step({doom: (*look, 0)})
            cast = fight.slams.get(doom.body)
            if cast is not None and cast.wave_tick is not None:
                return cast
        self.fail("actual Slam did not reach the wave phase")

    def test_ground_and_aerial_casts_travel_land_hit_once_and_start_cooldown(self):
        for aerial in (False, True):
            with self.subTest(aerial=aerial):
                run, hog, doom, _ = self.scene(aerial=aerial)
                fight = run.match.combat
                _, y, z = doom.mover.position
                look = self.aim(run, doom, hog, aerial=aerial)
                self.start(run, doom, look)
                cast = fight.slams[doom.body]
                self.assertEqual(cast.destination is not None, aerial)
                self.assertEqual(self.health(run, hog), 600)
                for _ in range(8):
                    run.step({doom: (*look, 0)})
                self.assertLess(doom.mover.position[2], z - 1)
                self.assertEqual(doom.mover.position[1] < y, aerial)
                self.assertEqual(self.health(run, hog), 600)
                self.assertIs(self.tick_to_wave(run, doom, look), cast)
                self.assertIsNotNone(cast.origin)
                self.assertEqual(self.health(run, hog), 600)
                # The wave travels on the match clock with no further body commands.
                for _ in range(12):
                    run.frame += 1
                    run.match.update(run.frame * .016, run.frame)
                amount = self.damage(cast)
                self.assertGreater(amount, 25)
                self.assertLessEqual(amount, 125)
                self.assertAlmostEqual(self.health(run, hog), 600 - amount)
                self.assertEqual(cast.hit, {hog.body})
                self.assertFalse(fight.is_stunned(hog))
                self.assertGreater(hog.mover.position[1], 1)
                self.assertGreater(hog.mover.position[2], 0)
                shield = self.vitals(run, doom).temporary_shields[doomfist_passive.SHIELD_TAG]
                self.assertEqual(shield.now, 30)
                for _ in range(50):
                    run.step({})
                self.assertAlmostEqual(self.health(run, hog), 600 - amount)
                self.assertGreater(cast.state.instance.state(0).remaining(), 0)

    def test_ground_and_aerial_slam_preserve_buffered_quick_melee(self):
        for aerial in (False, True):
            with self.subTest(aerial=aerial):
                self.check_slam_preserves_melee(aerial)

    def check_slam_preserves_melee(self, aerial):
        run, hog, doom, victim = self.scene(aerial=aerial)
        fight = run.match.combat
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        hog.team, doom.team, victim.team = 0, 1, 1
        x, y, z = hog.mover.position
        victim.mover.position[:] = (x + 1.5, y, z)
        victim.mover.body = None
        run.step({})

        held = {hog: (16384, 0, 0x800)}
        weapon = next(i for i in hog.body_script.component.instances.values() if i.graph.index == 0x01C6)
        ammo = weapon.vars[53].value()
        run.step(held)
        first = run.frame
        run.step({})
        run.step(held)
        self.assertEqual(self.health(run, victim), 19970)
        while run.frame < first + 11:
            run.step(held)
        look = self.aim(run, doom, hog, aerial=aerial)
        run.step({**held, doom: (*look, 16)})
        cast = fight.slams[doom.body]
        self.assertEqual(cast.destination is not None, aerial)
        while run.frame < first + 61:
            run.step(held)
        self.assertEqual(cast.hit, {hog.body})
        self.assertAlmostEqual(self.health(run, hog), 600 - self.damage(cast))
        self.assertEqual(self.health(run, victim), 19970)

        run.step(held)
        self.assertEqual(run.frame, first + 62)
        self.assertGreater(hog.mover.position[1], y + .2)
        self.assertFalse(fight.is_stunned(hog))
        self.assertEqual(self.health(run, victim), 19940)
        for _ in range(65):
            run.step(held)
        self.assertEqual(self.health(run, victim), 19940)
        self.assertEqual(weapon.vars[53].value(), ammo)

    def test_short_aerial_contact_uses_live_charge_instead_of_ground_hop_damage(self):
        for height, aerial, damage in ((2.74, False, 74.5006), (2.8, True, 12.2001)):
            with self.subTest(height=height):
                run, hog, doom, _ = self.scene(distance=3)
                x, y, z = doom.mover.position
                doom.mover.position[1] += height
                doom.mover.body = None
                run.step({})
                look = aim(run.match.combat._eye(doom), (x, y, z - 0.55))
                cast = self.land_and_hit_roadhog(run, hog, doom, look)
                self.assertEqual(cast.destination is not None, aerial)
                self.assertAlmostEqual(self.damage(cast), damage, places=3)
                self.assertAlmostEqual(self.health(run, hog), 600 - damage, places=3)

    def test_ground_slam_reaches_charge_cap_and_breather_halves_capped_damage(self):
        for height, expected, breather in ((7, 101.7009, False), (12, 125, False), (20, 125, True)):
            with self.subTest(height=height, breather=breather):
                run, hog, doom, _ = self.scene()
                _, y, z = doom.mover.position
                run.match.collision_world = World(
                    box(-80, 80, y - 1, y, -80, 80)
                    + box(-3, 3, y + height - 1, y + height, z - 1, z + 3),
                )
                doom.mover.position[1] += height
                doom.mover.body = None
                for _ in range(12):
                    run.step({})
                self.assertFalse(doom.mover.airborne)
                cast = self.land_and_hit_roadhog(run, hog, doom, (-32768, 0), breather=breather)
                self.assertIsNone(cast.destination)
                self.assertAlmostEqual(self.damage(cast), expected, places=3)
                actual = expected / 2 if breather else expected
                self.assertAlmostEqual(doom.stats.value(stats.HERO_DAMAGE), actual, places=3)
                self.assertAlmostEqual(self.health(run, hog), 600 - actual, places=3)
                if breather:
                    self.assertEqual(run.match.combat.incoming_damage_scale(hog), 0.5)
                    for _ in range(150):
                        run.step({})
                    self.assertEqual(self.health(run, hog), 600)
                    self.assertAlmostEqual(hog.body_script.component.entity_var(342).value(), 62.5, places=4)

    def land_and_hit_roadhog(self, run, hog, doom, look, *, breather=False):
        fight = run.match.combat
        self.start(run, doom, look)
        cast = fight.slams[doom.body]
        for _ in range(135):
            run.step({doom: (*look, 0)})
            if cast.origin is not None:
                break
        self.assertIsNotNone(cast.origin)
        self.assertIsNone(cast.wave_tick)
        x, y, z = cast.origin
        # Position the witness just ahead of the actual landing, before wave release.
        hog.mover.position[:] = (x, y, z - 2)
        hog.mover.body = None
        run.step({hog: (0, 0, 16)} if breather else {})
        for _ in range(45):
            run.step({doom: (*look, 0)})
            if hog.body in cast.hit:
                break
        self.assertEqual(cast.hit, {hog.body})
        return cast

    def test_ground_wave_respects_wall_team_and_range(self):
        for mode in ("wall", "ally", "range"):
            with self.subTest(mode=mode):
                run, hog, doom, _ = self.scene(25 if mode == "range" else 10,
                                               wall=2 if mode == "wall" else None)
                fight = run.match.combat
                if mode == "ally":
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    doom.team = hog.team = 0
                look = self.aim(run, doom, hog)
                self.start(run, doom, look)
                for _ in range(90):
                    run.step({doom: (*look, 0)})
                self.assertEqual(self.health(run, hog), 600)
                self.assertNotIn(hog.body, fight.impulses.live)
                self.assertNotIn(doom.body, fight.doomfist_shields)

    def test_invalid_aerial_aim_falls_without_slam_contact(self):
        run, hog, doom, _ = self.scene(aerial=True)
        fight = run.match.combat
        start = doom.mover.position[2]
        run.step({doom: (-32768, -4500, 16)})
        for _ in range(15):
            run.step({doom: (-32768, -4500, 16)})
        for _ in range(85):
            run.step({})
        self.assertNotIn(doom.body, fight.slams)
        self.assertEqual(doom.mover.position[2], start)
        self.assertFalse(doom.mover.airborne)
        self.assertEqual(self.health(run, hog), 600)

    def test_hook_cancels_ground_and_aerial_slam_before_impact(self):
        for aerial in (False, True):
            with self.subTest(aerial=aerial):
                run, hog, doom, _ = self.scene(aerial=aerial)
                fight = run.match.combat
                look = self.aim(run, doom, hog, aerial=aerial)
                self.start(run, doom, look)
                cast = fight.slams[doom.body]
                caught = False
                for frame in range(70):
                    hook = aim(fight._eye(hog), fight._eye(doom))
                    run.step({hog: (*hook, 8 if frame == 0 else 0), doom: (*look, 0)})
                    caught |= fight.hooks.controlled(doom.body)
                    if caught:
                        break
                self.assertTrue(caught)
                self.assertTrue(cast.finished)
                self.assertIsNone(cast.wave_tick)
                for _ in range(95):
                    run.step({})
                self.assertEqual(self.health(run, hog), 600)
                self.assertEqual(self.health(run, doom), 19970)
                self.assertNotIn(doom.body, fight.doomfist_shields)

    def test_breather_reduces_wave_and_preserves_healing_and_lift(self):
        run, hog, doom, _ = self.scene()
        fight = run.match.combat
        pool = self.vitals(run, hog)
        pool.damage(400)
        fight._sync_body_health(hog, pool)
        look = self.aim(run, doom, hog)
        run.step({hog: (0, 0, 16)})
        for _ in range(10):
            run.step({})
        self.start(run, doom, look)
        cast = self.tick_to_wave(run, doom, look)
        for _ in range(14):
            run.step({})
            if hog.body in cast.hit:
                break
        self.assertIn(hog.body, cast.hit)
        self.assertEqual(fight.incoming_damage_scale(hog), .5)
        self.assertIn(hog.body, fight.impulses.live)
        self.assertFalse(fight.is_stunned(hog))
        actual = doom.stats.value(stats.HERO_DAMAGE)
        self.assertAlmostEqual(actual, self.damage(cast) / 2)
        after = pool.total
        for _ in range(13):
            run.step({})
        self.assertGreater(pool.total, after)

    def test_projected_barrier_absorbs_wave_and_prevents_lift(self):
        run, hog, doom, support = self.scene()
        fight = run.match.combat
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        doom.team = 1
        hog.team = support.team = 0
        zarya = next(h for h in heroes().values() if h.name == "Zarya")
        self.assertTrue(run.match.switch_hero(support, zarya))
        x, y, z = hog.mover.position
        support.mover.position[:] = (x + 6, y, z)
        support.mover.body = None
        for _ in range(12):
            run.step({})
        shield_aim = aim(fight._eye(support), (x, y + 1.2, z))
        run.step({support: (*shield_aim, 16)})
        bubble = fight.zarya_bubbles.for_target(hog)
        self.assertIsNotNone(bubble)
        look = self.aim(run, doom, hog)
        self.start(run, doom, look)
        cast = self.tick_to_wave(run, doom, look)
        for _ in range(16):
            run.step({})
        self.assertEqual(self.health(run, hog), 600)
        self.assertAlmostEqual(bubble.health, 200 - self.damage(cast))
        self.assertNotIn(hog.body, fight.impulses.live)
        self.assertNotIn(doom.body, fight.doomfist_shields)

    def test_lift_leaves_whole_hog_running(self):
        run, hog, doom, victim = self.scene()
        fight = run.match.combat
        x, y, z = hog.mover.position
        doom.mover.position[:] = (x, y, z - 10)
        victim.mover.position[:] = (x, y, z + 15)
        doom.mover.body = victim.mover.body = None
        for _ in range(12):
            run.step({})
        look = self.aim(run, doom, hog)
        self.start(run, doom, look)
        cast = self.tick_to_wave(run, doom, look)
        script = hog.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        ultimate, = script.ultimates
        fire = aim(fight._eye(hog), (x, y + 1.2, z + 15))
        run.step({hog: (*fire, 32)})
        for _ in range(16):
            run.step({hog: (*fire, 0)})
            if hog.body in cast.hit:
                break
        self.assertIn(hog.body, cast.hit)
        self.assertIn(hog.body, fight.impulses.live)
        self.assertTrue(ultimate.flags & nodes.Ability.RUNNING)
        self.assertFalse(fight.is_stunned(hog))
        pool = self.vitals(run, victim)
        before = pool.total
        for _ in range(35):
            look = aim(fight._eye(hog), (x, y + 1.2, z + 15))
            run.step({hog: (*look, 0)})
            if pool.total < before:
                break
        self.assertLess(pool.total, before)

    def test_cooldown_refuses_a_new_cast_until_expiry(self):
        run, hog, doom, _ = self.scene()
        fight = run.match.combat
        look = self.aim(run, doom, hog)
        self.start(run, doom, look)
        cast = self.tick_to_wave(run, doom, look)
        for _ in range(90):
            run.step({})
        ability = cast.state.instance.state(0)
        self.assertGreater(ability.remaining(), 0)
        before = self.health(run, hog)
        for actor, z in ((doom, 10), (hog, 0)):
            actor.mover.position[2] = z
            actor.mover.body = None
        self.start(run, doom, look)
        for _ in range(420):
            run.step({})
            if ability.remaining() <= 0:
                break
        self.assertIs(fight.slams[doom.body], cast)
        self.assertEqual(ability.remaining(), 0)
        self.assertEqual(self.health(run, hog), before)
        self.start(run, doom, look)
        fresh = self.tick_to_wave(run, doom, look)
        self.assertIsNot(fresh, cast)
        for _ in range(20):
            run.step({})
        self.assertAlmostEqual(self.health(run, hog), before - self.damage(fresh))
