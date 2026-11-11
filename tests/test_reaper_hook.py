"""Actual 01C8 launch input against Reaper's body and ability graphs."""

import math
import unittest
from dataclasses import replace

import test_game_reaper as reaper_tests
import test_reaper_control as control_tests
from test_game_combat import aim

from ow174.game import ana, combat, hook, reaper, zarya_barriers
from ow174.game.collision import World
from ow174.game.commands import Command
from ow174.game.content import heroes


class ReaperHookTests(unittest.TestCase):
    def scenario(self, distance=10, *, names=("Reaper", "Roadhog")):
        run, owner, hog, _ = reaper_tests.ReaperTests().scenario(names=names)
        self.assertTrue(run.match.switch_hero(hog, heroes()[hook.ROADHOG]))
        x, y, z = owner.mover.position
        hog.mover.position[:] = (x, y, z - distance)
        hog.mover.body = None
        for _ in range(30):
            self.step(run, owner, hog)
        fight = run.match.combat
        targets = {t.owner: t for t in fight._targets()}
        return run, owner, hog, fight.vitals_of(targets[owner]), fight.vitals_of(targets[hog])

    @staticmethod
    def step(run, owner, hog, *, cast=0, action=0):
        fight = run.match.combat
        x, y, z = owner.mover.position
        yaw, pitch = aim(fight._eye(hog), (x, y + 1, z))
        x, y, z = hog.mover.position
        back_yaw, back_pitch = aim(fight._eye(owner), (x, y + 1, z))
        run.step({hog: (yaw, pitch, cast), owner: (back_yaw, back_pitch, action)})

    def catch(self, run, owner, hog):
        fight = run.match.combat
        self.step(run, owner, hog, cast=8)
        for _ in range(60):
            self.step(run, owner, hog)
            if fight.hooks.controlled(owner.body):
                return fight.hooks.flights[hog.body]
        self.fail("actual Chain Hook did not latch onto Reaper")

    def test_actual_hook_one_hit_pull_blocks_fire_then_reaper_countershot(self):
        run, owner, hog, victim, attacker = self.scenario()
        start = math.dist(owner.mover.position, hog.mover.position)
        flight = self.catch(run, owner, hog)
        fight = run.match.combat
        self.assertEqual(victim.total, 220)
        self.assertEqual((flight.reach, flight.radius, flight.damage.amount), (20, 0.5, 30))
        self.assertEqual((flight.speed, flight.pull_speed, flight.gap, flight.recovery, flight.limit),
                         (60, 40, 1, 0.3, 2))
        before = attacker.total
        for _ in range(8):
            self.step(run, owner, hog, action=1)
        self.assertTrue(fight.hooks.controlled(owner.body))
        self.assertLess(math.dist(owner.mover.position, hog.mover.position), start)
        self.assertEqual(attacker.total, before)
        for _ in range(60):
            self.step(run, owner, hog)
        self.assertFalse(fight.is_stunned(owner))
        self.assertAlmostEqual(math.dist(owner.mover.position, hog.mover.position), 3, places=2)
        self.assertEqual(len(hog.client.messages(combat.HIT)), 1)
        self.assertEqual(victim.total, 220)
        self.step(run, owner, hog, action=1)
        self.assertEqual(attacker.total, before - 126)
        self.assertEqual(victim.total, 250)

    def test_actual_wraith_rejects_flying_hook(self):
        run, owner, hog, victim, _ = self.scenario()
        fight = run.match.combat
        self.step(run, owner, hog, cast=8)
        for _ in range(12):
            self.step(run, owner, hog)
        self.step(run, owner, hog, action=8)
        self.assertTrue(reaper.intangible(owner))
        for _ in range(65):
            self.step(run, owner, hog)
        self.assertTrue(reaper.intangible(owner))
        self.assertEqual(victim.total, 250)
        self.assertFalse(fight.hooks.flights)
        self.assertEqual(hog.client.messages(combat.HIT), [])

    def test_actual_hook_interrupts_blossom_without_late_pulses(self):
        run, owner, hog, victim, attacker = self.scenario(distance=6)
        fight = run.match.combat
        owner.body_script.set_ult_charge(2100)
        self.step(run, owner, hog, cast=8, action=32)
        channel = fight.reaper_blossoms[owner.body]
        self.assertFalse(channel["ended"])
        self.assertLess(attacker.total, 700)
        for _ in range(60):
            self.step(run, owner, hog)
            if fight.hooks.controlled(owner.body):
                break
        self.assertTrue(fight.hooks.controlled(owner.body))
        self.assertTrue(channel["ended"])
        self.assertFalse(channel["targets"])
        self.assertEqual(victim.total, 220)
        after = attacker.total
        for _ in range(220):
            self.step(run, owner, hog)
        self.assertEqual(attacker.total, after)
        self.assertLess(owner.body_script.component.vars[342].value(), 30)

    def test_actual_hook_miss_ally_and_wall_do_not_hit(self):
        for case in ("miss", "ally", "wall"):
            with self.subTest(case=case):
                run, owner, hog, victim, _ = self.scenario()
                fight = run.match.combat
                if case == "ally":
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    owner.team = hog.team
                if case == "wall":
                    x, y, z = hog.mover.position
                    mid = z + 4
                    run.match.collision_world = World([
                        (x-5,y,mid,x+5,y,mid,x+5,y+5,mid),
                        (x-5,y,mid,x+5,y+5,mid,x-5,y+5,mid),
                    ], name="Chain Hook cover")
                if case == "miss":
                    run.step({hog: (16384, 0, 8)})
                    for _ in range(100):
                        run.step({hog: (16384, 0, 0)})
                else:
                    self.step(run, owner, hog, cast=8)
                    for _ in range(100):
                        self.step(run, owner, hog)
                self.assertEqual(victim.total, 250)
                self.assertFalse(fight.hooks.flights)
                self.assertEqual(hog.client.messages(combat.HIT), [])

    def test_actual_hook_pull_and_release_without_victim_input(self):
        run, owner, hog, victim, _ = self.scenario()
        self.catch(run, owner, hog)
        fight = run.match.combat
        before = math.dist(owner.mover.position, hog.mover.position)
        for _ in range(12):
            run.frame += 1
            run.match.update(run.frame * combat.TICK, run.frame)
        self.assertTrue(fight.hooks.controlled(owner.body))
        self.assertLess(math.dist(owner.mover.position, hog.mover.position), before)
        for _ in range(75):
            run.frame += 1
            run.match.update(run.frame * combat.TICK, run.frame)
        self.assertFalse(fight.hooks.controlled(owner.body))
        self.assertAlmostEqual(math.dist(owner.mover.position, hog.mover.position), 3, places=2)
        self.assertEqual(owner.mover.velocity, (0.0, 0.0, 0.0))
        self.assertEqual(victim.total, 220)

    def test_actual_hook_cooldown_refuses_then_reuses(self):
        run, owner, hog, victim, _ = self.scenario()
        flight = self.catch(run, owner, hog)
        ability = next(s for s in flight.instance.states.values() if s.node.pos == 11)
        launch = next(s for s in flight.instance.states.values() if s.node.pos == 12)
        for _ in range(75):
            self.step(run, owner, hog)
        self.assertEqual(flight.instance.vars[61].base, 8)
        self.assertGreater(ability.remaining(), 0)
        self.step(run, owner, hog, cast=8)
        for _ in range(50):
            self.step(run, owner, hog)
        self.assertEqual(launch.counter, 1)
        self.assertEqual(victim.total, 220)
        for _ in range(550):
            self.step(run, owner, hog)
            if ability.remaining() == 0:
                break
        self.assertEqual(ability.remaining(), 0)
        self.catch(run, owner, hog)
        self.assertEqual(launch.counter, 2)
        self.assertEqual(victim.total, 190)

    def test_actual_hook_interrupts_locked_shadow_before_immunity(self):
        run, owner, hog, victim, _ = self.scenario(distance=6)
        fixture = control_tests.ReaperControlTests()
        record = fixture.start_shadow(run, owner)
        self.catch(run, owner, hog)
        shadow = fixture.shadow(owner)
        self.assertEqual(victim.total, 220)
        self.assertFalse(shadow.state(19).active)
        self.assertFalse(shadow.state(32).active)
        self.assertGreater(shadow.vars[61].value(), 9)
        for _ in range(110):
            self.step(run, owner, hog)
        self.assertGreater(math.dist(owner.mover.position, record["target"]), 1)
        self.assertFalse(run.match.combat.is_stunned(owner))

    def test_actual_hook_passes_through_shadow_immunity(self):
        run, owner, hog, victim, _ = self.scenario(distance=6)
        fight = run.match.combat
        fixture = control_tests.ReaperControlTests()
        record = fixture.start_shadow(run, owner)
        for _ in range(52):
            self.step(run, owner, hog)
        self.step(run, owner, hog, cast=8)
        flight = None
        passed = False
        for _ in range(65):
            self.step(run, owner, hog)
            flight = fight.hooks.flights.get(hog.body) or flight
            if flight and reaper.intangible(owner):
                passed |= flight.travelled >= math.dist(flight.origin, owner.mover.position)
            self.assertFalse(fight.hooks.controlled(owner.body))
        self.assertTrue(passed)
        self.assertEqual(victim.total, 250)
        self.assertLess(math.dist(owner.mover.position, record["target"]), 0.15)

    def test_actual_hook_resolves_shadow_windup_and_immunity_across_world_gap(self):
        fixture = control_tests.ReaperControlTests()
        for immune in (False, True):
            outcomes = []
            for skipped in (False, True):
                with self.subTest(immune=immune, skipped=skipped):
                    run, owner, hog, victim, _ = self.scenario(distance=6)
                    fight = run.match.combat
                    record = fixture.start_shadow(run, owner)
                    if immune:
                        for _ in range(52):
                            self.step(run, owner, hog)
                    self.step(run, owner, hog, cast=8)
                    for _ in range(25):
                        if fight.hooks.flights:
                            break
                        self.step(run, owner, hog)
                    flight, = fight.hooks.flights.values()
                    self.assertIsNone(flight.target)
                    self.assertEqual(reaper.intangible(owner), immune)
                    distance = math.dist(flight.origin, owner.mover.position)
                    frame = owner.commands.last_frame
                    end = run.frame + 150
                    while run.frame < end:
                        run.frame = end if skipped else run.frame + 1
                        run.match.update(run.frame * combat.TICK, run.frame, frames=False)
                    self.assertEqual(owner.commands.last_frame, frame)
                    self.assertFalse(fight.hooks.flights)
                    self.assertFalse(fight.hooks.controlled(owner.body))
                    self.assertFalse(fight.is_stunned(owner))
                    self.assertFalse(reaper.intangible(owner))
                    self.assertEqual(victim.total, 250 if immune else 220)
                    position = tuple(owner.mover.position)
                    self.assertGreater(fixture.shadow(owner).vars[61].value(), 7)
                    hits = hog.client.messages(combat.HIT)
                    if immune:
                        self.assertFalse(hits)
                        self.assertGreater(flight.travelled, distance)
                        self.assertLess(math.dist(position, record["target"]), 0.15)
                    else:
                        self.assertEqual(len(hits), 1)
                        self.assertGreater(math.dist(position, record["target"]), 1)
                        self.assertAlmostEqual(math.dist(position, hog.mover.position), 3, places=2)
                    outcomes.append((position, owner.mover.velocity, victim.total, hits))
            if len(outcomes) == 2:
                self.assertEqual(outcomes[0], outcomes[1])

    def test_actual_lethal_hook_and_caster_death_release_control(self):
        run, owner, hog, victim, _ = self.scenario()
        fight = run.match.combat
        victim.damage(225)
        self.step(run, owner, hog, cast=8)
        for _ in range(60):
            self.step(run, owner, hog)
        self.assertTrue(victim.dead)
        self.assertEqual(victim.killer, hog.body)
        self.assertFalse(fight.hooks.flights)
        self.assertFalse(fight.is_stunned(owner))

        run, owner, hog, victim, caster_pool = self.scenario(names=("Reaper", "Roadhog", "Support"))
        support = run.players[2]
        fight = run.match.combat
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        hog.team = 1
        owner.team = support.team = 0
        self.assertTrue(run.match.switch_hero(support, heroes()[reaper.HERO]))
        x, y, z = hog.mover.position
        support.mover.position[:] = (x + 1.5, y, z)
        support.mover.body = None
        for _ in range(30):
            self.step(run, owner, hog)
        self.catch(run, owner, hog)
        caster_pool.damage(caster_pool.total - 50)
        reaper_tests.ReaperTests().fire(run, support, hog)
        self.assertTrue(caster_pool.dead)
        self.assertEqual(caster_pool.killer, support.body)
        self.assertFalse(fight.is_stunned(owner))
        self.assertFalse(fight.hooks.flights)
        self.assertEqual(victim.total, 220)

    def test_actual_hook_range_bounds_capsule_contact(self):
        for distance, damage in ((20.7, 30), (21.5, 0)):
            with self.subTest(distance=distance):
                run, owner, hog, victim, _ = self.scenario(distance=distance)
                self.step(run, owner, hog, cast=8)
                for _ in range(90):
                    self.step(run, owner, hog)
                self.assertEqual(victim.total, 250 - damage)
                self.assertEqual(len(hog.client.messages(combat.HIT)), int(bool(damage)))

    def test_actual_projected_barrier_absorbs_hook_without_pulling_reaper(self):
        run, owner, hog, victim, _ = self.scenario(names=("Reaper", "Roadhog", "Zarya"))
        ally = run.players[2]
        fight = run.match.combat
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        hog.team = 1
        owner.team = ally.team = 0
        self.assertTrue(run.match.switch_hero(ally, heroes()[zarya_barriers.HERO]))
        x, y, z = owner.mover.position
        ally.mover.position[:] = (x + 4, y, z)
        ally.mover.body = None
        for _ in range(30):
            run.step({})
        yaw, pitch = aim(fight._eye(ally), (x, y + 1, z))
        run.step({ally: (yaw, pitch, 16)})
        bubble = fight.zarya_bubbles.for_target(owner)
        self.assertIsNotNone(bubble)
        self.assertEqual(bubble.health, 200)
        start = tuple(owner.mover.position)
        self.step(run, owner, hog, cast=8)
        for _ in range(55):
            self.step(run, owner, hog)
            self.assertFalse(fight.hooks.controlled(owner.body))
        self.assertEqual(bubble.health, 170)
        self.assertEqual(victim.total, 250)
        self.assertEqual(tuple(owner.mover.position), start)

    def test_actual_sleep_dart_on_caster_releases_active_hook(self):
        run, owner, hog, victim, _ = self.scenario(names=("Reaper", "Roadhog", "Ana"))
        ally = run.players[2]
        fight = run.match.combat
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        hog.team = 1
        owner.team = ally.team = 0
        self.assertTrue(run.match.switch_hero(ally, heroes()[ana.HERO]))
        x, y, z = hog.mover.position
        ally.mover.position[:] = (x + 2, y, z)
        ally.mover.body = None
        for _ in range(30):
            run.step({})
        self.catch(run, owner, hog)
        yaw, pitch = aim(fight._eye(ally), (x, y + 1, z))
        run.step({ally: (yaw, pitch, 8)})
        for _ in range(30):
            run.step({ally: (yaw, pitch, 0)})
            if hog.body in fight.ana_sleeps:
                break
            self.assertTrue(fight.hooks.controlled(owner.body))
        self.assertIn(hog.body, fight.ana_sleeps)
        self.assertFalse(fight.hooks.flights)
        self.assertFalse(fight.is_stunned(owner))
        self.assertEqual(victim.total, 220)

    def test_actual_batched_launch_keeps_world_flight_after_body_cast_ends(self):
        run, owner, hog, victim, _ = self.scenario()
        fight = run.match.combat
        x, y, z = owner.mover.position
        yaw, pitch = aim(fight._eye(hog), (x, y + 1, z))
        first = hog.body_script.frame + 1
        hog.take_commands([
            Command(first + index, yaw=yaw, pitch=pitch, buttons=8 if index == 0 else 0)
            for index in range(40)
        ])
        flight = fight.hooks.flights[hog.body]
        ability = next(s for s in flight.instance.states.values() if s.node.pos == 11)
        self.assertFalse(ability.flags & ability.RUNNING)
        self.assertEqual(flight.travelled, 0)
        for _ in range(25):
            run.frame += 1
            run.match.update(run.frame * combat.TICK, run.frame)
            if fight.hooks.controlled(owner.body):
                break
        self.assertTrue(fight.hooks.controlled(owner.body))
        self.assertEqual(victim.total, 220)
