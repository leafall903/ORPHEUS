"""Actual Ana inputs against Roadhog's 0240 Take a Breather."""

import unittest

from test_game_ana import grenade_aim
import test_game_ana_hook as hook_fixture
from test_game_combat import aim

from ow174.game import breather, combat, stats
from ow174.game.content import find_hero
from ow174.game.script import nodes


class AnaBreatherTests(unittest.TestCase):
    def arena(self, *, friendly=False, ally_hero=None):
        run, ana, hog = hook_fixture.AnaHookTests().arena(distance=6, ally_hero=ally_hero)
        fight = run.match.combat
        pool = fight.vitals[hog.body]
        pool.damage(580 if friendly else 500)
        fight._sync_body_health(hog, pool)
        if friendly:
            hog.team = ana.team
        point = (hog.mover.position[0], hog.mover.position[1] + 1, hog.mover.position[2])
        return run, fight, ana, hog, pool, point

    def until(self, run, ready, *, commands=None, limit=130):
        for _ in range(limit):
            if ready():
                return
            run.step(commands or {})
        self.assertTrue(ready())

    def test_both_rifles_sample_live_reduction_and_resume_normal_damage(self):
        for scoped in (False, True):
            with self.subTest(scoped=scoped):
                run, fight, ana, hog, pool, point = self.arena()
                direction = aim(fight._eye(ana), point)
                scope = 2 if scoped else 0
                for _ in range(35):
                    run.step({ana: (*direction, scope)})
                run.step({hog: (0, 0, 16), ana: (*direction, scope)})
                self.assertEqual(fight.incoming_damage_scale(hog), .5)
                self.assertEqual(pool.total, 100)
                run.step({ana: (*direction, scope | 1)})
                for _ in range(45):
                    run.step({ana: (*direction, scope)})
                self.assertAlmostEqual(ana.stats.value(stats.HERO_DAMAGE), 35)
                self.until(run, lambda: fight.incoming_damage_scale(hog) == 1,
                           commands={ana: (*direction, scope)})
                self.assertAlmostEqual(hog.stats.value(stats.HEALING), 300)
                self.assertAlmostEqual(pool.total, 365)
                run.step({ana: (*direction, scope | 1)})
                for _ in range(45):
                    run.step({ana: (*direction, scope)})
                self.assertAlmostEqual(ana.stats.value(stats.HERO_DAMAGE), 105)
                self.assertAlmostEqual(pool.total, 295)

    def test_enemy_grenade_is_reduced_but_prevents_entire_self_heal(self):
        run, fight, ana, hog, pool, point = self.arena()
        direction = grenade_aim(run, ana, point)
        run.step({hog: (0, 0, 16), ana: (*direction, 16)})
        self.until(run, lambda: pool.healing_block_until > fight.now)
        self.assertAlmostEqual(pool.total, 75)
        self.assertAlmostEqual(ana.stats.value(stats.HERO_DAMAGE), 25)
        self.assertEqual(fight.incoming_damage_scale(hog), .5)
        self.until(run, lambda: fight.incoming_damage_scale(hog) == 1)
        self.assertAlmostEqual(pool.total, 75)
        self.assertEqual(hog.stats.value(stats.HEALING), 0)

    def test_friendly_grenade_applies_direct_heal_and_amplifies_self_heal(self):
        run, fight, ana, hog, pool, point = self.arena(friendly=True)
        direction = grenade_aim(run, ana, point)
        run.step({ana: (*direction, 16)})
        self.until(run, lambda: pool.healing_boost_until > fight.now)
        self.assertEqual(pool.total, 120)
        run.step({hog: (0, 0, 16)})
        self.until(run, lambda: fight.incoming_damage_scale(hog) == 1)
        self.assertAlmostEqual(pool.total, 570)
        self.assertAlmostEqual(hog.stats.value(stats.HEALING), 450)
        self.assertAlmostEqual(ana.stats.value(stats.HEALING), 100)

    def test_projected_barrier_takes_full_rifle_damage_before_breather_reduction(self):
        run, fight, ana, hog, pool, point = self.arena(ally_hero="Zarya")
        zarya = run.players[2]
        zarya.team = hog.team
        projected = aim(fight._eye(zarya), point)
        run.step({hog: (0, 0, 16), zarya: (*projected, 16)})
        barrier = fight.body_barriers[hog.body]
        direction = aim(fight._eye(ana), point)
        run.step({ana: (*direction, 1)})
        self.until(run, lambda: barrier.health < 200)
        self.assertEqual(barrier.health, 130)
        self.assertEqual(ana.stats.value(stats.HERO_DAMAGE), 0)
        self.assertEqual(fight.incoming_damage_scale(hog), .5)
        self.until(run, lambda: fight.incoming_damage_scale(hog) == 1)
        self.assertAlmostEqual(pool.total, 400)

    def test_actual_sleep_dart_is_reduced_then_ends_healing_and_reduction(self):
        run, fight, ana, hog, pool, point = self.arena()
        run.step({hog: (0, 0, 16)})
        self.until(run, lambda: hog.stats.value(stats.HEALING) > 0)
        direction = aim(fight._eye(ana), point)
        run.step({ana: (*direction, 8)})
        self.until(run, lambda: fight.is_asleep(hog))
        self.assertAlmostEqual(ana.stats.value(stats.HERO_DAMAGE), 2.5)
        self.assertEqual(fight.incoming_damage_scale(hog), 1)
        instance = next(i for i in hog.body_script.component.instances.values() if i.graph.index == breather.GRAPH)
        ability = instance.states[instance.graph._by_pos[1].state]
        self.assertFalse(ability.flags & nodes.Ability.RUNNING)
        self.assertGreater(ability.remaining(), 0)
        stopped = pool.total
        for _ in range(110):
            run.step({})
        self.assertEqual(pool.total, stopped)

    def test_started_heal_runs_once_without_owner_commands_and_retires_on_death_or_replacement(self):
        for ending in ("no commands", "death", "replacement"):
            with self.subTest(ending=ending):
                run, fight, ana, hog, pool, _ = self.arena()
                run.step({hog: (0, 0, 16)})
                self.until(run, lambda: hog.stats.value(stats.HEALING) > 0)
                old_body = hog.body
                frame = hog.body_script.frame
                before = pool.total
                if ending == "replacement":
                    self.assertTrue(run.match.switch_hero(hog, find_hero("Soldier")))
                elif ending == "death":
                    target = next(t for t in fight._targets() if t.entity == old_body)
                    fight._damage(ana, target, 2000, False, None, run.frame)
                    self.assertTrue(pool.dead)
                if ending != "no commands":
                    self.assertNotIn(old_body, fight.breathers.channels)
                expected = {"no commands": 400, "death": 0, "replacement": before}[ending]
                for _ in range(140):
                    run.frame += 1
                    run.match.update(run.frame * combat.TICK, run.frame)
                self.assertAlmostEqual(pool.total, expected)
                self.assertEqual(fight.incoming_damage_scale(hog), 1)
                if ending == "no commands":
                    self.assertEqual(hog.body_script.frame, frame)
                    run.step({})
                    self.assertAlmostEqual(pool.total, 400)
