"""Actual Roadhog Chain Hook and Ana on build 104319."""

import math
import unittest

from test_game_ana import TEAM_MAP, set_static_wall
from test_game_combat import Run, aim

from ow174.game.content import find_hero
from ow174.game.script import nodes


class AnaHookTests(unittest.TestCase):
    def arena(self, *, distance=10, ally_hero=None):
        names = ("Ana", "Roadhog", "Ally") if ally_hero else ("Ana", "Roadhog")
        run = Run(TEAM_MAP, names)
        match = run.match
        match.bots.clear()
        match._pending_bot_scripts.clear()
        ana, hog = run.players[:2]
        self.assertTrue(match.switch_hero(ana, find_hero("Ana")))
        self.assertTrue(match.switch_hero(hog, find_hero("Roadhog")))
        ana.team, hog.team = 0, 1
        x, y, z = ana.mover.position
        hog.spawn = (x, y, z + distance), hog.spawn[1]
        hog.mover = hog.new_mover()
        if ally_hero:
            ally = run.players[2]
            self.assertTrue(match.switch_hero(ally, find_hero(ally_hero)))
            ally.team = ana.team
            ally.spawn = (x + 5, y, z), ally.spawn[1]
            ally.mover = ally.new_mover()
        for _ in range(30):
            run.step({})
        for target in match.combat._targets():
            match.combat.vitals_of(target)
        return run, ana, hog

    def press_hook(self, run, hog, ana, extra=None):
        fight = run.match.combat
        direction = aim(fight._eye(hog), fight._eye(ana))
        run.step({hog: (*direction, 8), **(extra or {})})
        return direction

    def advance_until(self, run, condition, commands=None, limit=65):
        for _ in range(limit):
            if condition():
                return
            run.step(commands or {})
        self.assertTrue(condition())

    def test_actual_hook_hits_once_and_forces_ana_to_three_metres(self):
        run, ana, hog = self.arena()
        fight = run.match.combat
        start = tuple(ana.mover.position)
        direction = self.press_hook(run, hog, ana)
        self.advance_until(run, lambda: hog.body in fight.hooks.flights, {hog: (*direction, 0)})
        flight = fight.hooks.flights[hog.body]
        self.assertEqual((flight.reach, flight.radius, flight.damage.amount), (20, 0.5, 30))
        self.assertEqual(fight.vitals[ana.body].total, 200)
        self.advance_until(run, lambda: fight.is_stunned(ana))
        self.assertEqual(fight.vitals[ana.body].total, 170)
        reply = aim(fight._eye(ana), fight._eye(hog))
        for _ in range(12):
            run.step({ana: (*reply, 1)})
        self.assertLess(math.dist(ana.mover.position, hog.mover.position), math.dist(start, hog.mover.position))
        self.assertEqual(fight.vitals[hog.body].total, 600)
        self.advance_until(run, lambda: not fight.is_stunned(ana))
        self.assertAlmostEqual(math.dist(ana.mover.position, hog.mover.position), 3, places=2)
        self.assertEqual(fight.vitals[ana.body].total, 170)

    def test_static_cover_ally_and_projected_barrier_block_damage_and_pull(self):
        for gate in ("wall", "ally", "projected"):
            with self.subTest(gate=gate):
                run, ana, hog = self.arena(ally_hero="Zarya" if gate == "projected" else None)
                fight = run.match.combat
                start = tuple(ana.mover.position)
                if gate == "wall":
                    set_static_wall(run, ana, 5)
                elif gate == "ally":
                    hog.team = ana.team
                direction = self.press_hook(run, hog, ana)
                self.advance_until(run, lambda: hog.body in fight.hooks.flights, {hog: (*direction, 0)})
                if gate == "projected":
                    ally = run.players[2]
                    direction = aim(fight._eye(ally), fight._eye(ana))
                    run.step({ally: (*direction, 16)})
                    barrier = fight.body_barriers[ana.body]
                    barrier.health = 20
                self.advance_until(run, lambda: not fight.hooks.flights)
                self.assertFalse(fight.is_stunned(ana))
                self.assertEqual(fight.vitals[ana.body].total, 200)
                self.assertEqual(tuple(ana.mover.position), start)
                if gate == "projected":
                    self.assertEqual(barrier.health, 0)

    def test_hook_interrupts_nano_before_release(self):
        run, ana, hog = self.arena(distance=6, ally_hero="Soldier")
        ally = run.players[2]
        fight = run.match.combat
        pool = fight.vitals[ally.body]
        pool.damage(100)
        ana.body_script.set_ult_charge(ana.body_script.component.vars[338].value())
        direction = self.press_hook(run, hog, ana)
        self.advance_until(run, lambda: hog.body in fight.hooks.flights, {hog: (*direction, 0)})
        reply = aim(fight._eye(ana), fight._eye(ally))
        run.step({ana: (*reply, 32)})
        ultimate = ana.body_script.ultimates[0]
        self.assertTrue(ultimate.flags & nodes.Ability.RUNNING)
        self.advance_until(run, lambda: fight.is_stunned(ana))
        self.assertFalse(ultimate.flags & nodes.Ability.RUNNING)
        self.advance_until(run, lambda: not fight.is_stunned(ana))
        self.assertEqual(pool.total, 100)
        self.assertEqual(pool.nano_until, 0)

    def test_projected_barrier_releases_active_pull_and_keeps_full_pool(self):
        run, ana, hog = self.arena(ally_hero="Zarya")
        ally = run.players[2]
        fight = run.match.combat
        direction = self.press_hook(run, hog, ana)
        self.advance_until(run, lambda: fight.is_stunned(ana), {hog: (*direction, 0)})
        direction = aim(fight._eye(ally), fight._eye(ana))
        run.step({ally: (*direction, 16)})
        self.assertFalse(fight.is_stunned(ana))
        self.assertFalse(fight.hooks.flights)
        self.assertEqual(fight.barriers.authored_for(ally.body).vitals.total, 200)
        before = tuple(ana.mover.position)
        for _ in range(15):
            run.step({})
        self.assertEqual(tuple(ana.mover.position), before)
        self.assertEqual(fight.vitals[ana.body].total, 170)

    def test_actual_sleep_dart_cancels_hook_in_flight(self):
        run, ana, hog = self.arena(distance=18)
        fight = run.match.combat
        reply = aim(fight._eye(ana), fight._eye(hog))
        run.step({ana: (*reply, 8)})
        for _ in range(12):
            run.step({ana: (*reply, 0)})
        direction = self.press_hook(run, hog, ana, {ana: (*reply, 0)})
        self.advance_until(run, lambda: hog.body in fight.hooks.flights,
                           {hog: (*direction, 0), ana: (*reply, 0)})
        self.advance_until(run, lambda: fight.is_asleep(hog), {ana: (*reply, 0)})
        self.assertFalse(fight.hooks.flights)
        self.assertFalse(fight.is_stunned(ana))
        self.assertEqual(fight.vitals[ana.body].total, 200)

    def test_death_or_replacement_immediately_releases_control(self):
        for participant in ("owner", "target"):
            for ending in ("death", "replacement"):
                with self.subTest(participant=participant, ending=ending):
                    run, ana, hog = self.arena()
                    fight = run.match.combat
                    direction = self.press_hook(run, hog, ana)
                    self.advance_until(run, lambda: fight.is_stunned(ana), {hog: (*direction, 0)})
                    victim, source = (hog, ana) if participant == "owner" else (ana, hog)
                    old_body = victim.body
                    if ending == "death":
                        target = next(t for t in fight._targets() if t.owner is victim)
                        fight._damage(source, target, 2000, False, None, run.frame)
                        self.assertTrue(fight.is_dead(victim))
                    else:
                        self.assertTrue(run.match.switch_hero(victim, find_hero("Soldier")))
                        self.assertNotEqual(victim.body, old_body)
                    self.assertFalse(fight.hooks.flights)
                    self.assertFalse(fight.is_stunned(ana))
                    self.assertFalse(any(key[0] == old_body for key in fight.hooks.seen))

    def test_new_cover_breaks_an_active_pull_without_another_hit(self):
        run, ana, hog = self.arena()
        fight = run.match.combat
        direction = self.press_hook(run, hog, ana)
        self.advance_until(run, lambda: fight.is_stunned(ana), {hog: (*direction, 0)})
        before = tuple(ana.mover.position)
        set_static_wall(run, ana, 5)
        run.step({})
        self.assertFalse(fight.is_stunned(ana))
        self.assertEqual(fight.vitals[ana.body].total, 170)
        self.assertEqual(tuple(ana.mover.position), before)
