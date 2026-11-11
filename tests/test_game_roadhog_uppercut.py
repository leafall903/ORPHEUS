"""Actual Rising Uppercut commands against the 104319 Roadhog body."""

import math
import unittest
from dataclasses import replace

import test_game_roadhog_rocket_punch as fixtures

from ow174.game import doomfist_passive, uppercut
from ow174.game.content import heroes
from ow174.game.script import nodes


class RoadhogUppercutTests(unittest.TestCase):
    def scene(self, distance=1.5, *, wall=None):
        return fixtures.RoadhogRocketPunchTests().scene(distance, wall=wall)

    def aim(self, run, actor, target):
        return fixtures.RoadhogRocketPunchTests().look(run, actor, target)

    def vitals(self, run, actor):
        fight = run.match.combat
        return fight.vitals_of(next(t for t in fight._targets() if t.owner is actor))

    def test_authored_hit_once_lifts_both_bodies_and_grants_shields(self):
        run, hog, doom, _ = self.scene()
        fight = run.match.combat
        y = hog.mover.position[1]
        look = self.aim(run, doom, hog)
        run.step({doom: (*look, 8)})
        self.assertEqual(self.vitals(run, hog).total, 550)
        vx, vy, vz = fight.impulses.velocity(hog.body)
        self.assertAlmostEqual(math.hypot(vx, vz), 7)
        self.assertEqual(vy, 14)
        self.assertAlmostEqual(math.hypot(*doom.mover.velocity), 10, places=5)
        self.assertGreater(doom.mover.velocity[1], 0)
        shield = self.vitals(run, doom).temporary_shields[doomfist_passive.SHIELD_TAG]
        self.assertEqual(shield.now, 30)
        for _ in range(10):
            run.step({doom: (*look, 8)})
        self.assertGreater(hog.mover.position[1], y + 1)
        self.assertGreater(doom.mover.position[1], y)
        self.assertFalse(fight.is_stunned(hog))
        self.assertEqual(self.vitals(run, hog).total, 550)
        self.assertEqual(shield.now, 30)

    def test_range_facing_static_cover_and_allies_exclude_body_contact(self):
        for mode in ("range", "facing", "wall", "ally"):
            with self.subTest(mode=mode):
                run, hog, doom, rear = self.scene(7 if mode == "range" else 1.5,
                                                  wall=.8 if mode == "wall" else None)
                rear.mover.position[0] += 20
                rear.mover.body = None
                fight = run.match.combat
                if mode == "ally":
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    hog.team = doom.team = 0
                look = (0, 0) if mode == "facing" else self.aim(run, doom, hog)
                run.step({doom: (*look, 8)})
                for _ in range(12):
                    run.step({})
                self.assertEqual(self.vitals(run, hog).total, 600)
                self.assertNotIn(hog.body, fight.impulses.live)
                self.assertNotIn(doom.body, fight.doomfist_shields)

    def test_projected_barrier_absorbs_damage_and_prevents_target_lift(self):
        run, hog, doom, support = self.scene()
        fight = run.match.combat
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        hog.team = support.team = 0
        doom.team = 1
        zarya = next(h for h in heroes().values() if h.name == "Zarya")
        self.assertTrue(run.match.switch_hero(support, zarya))
        x, y, z = hog.mover.position
        support.mover.position[:] = (x + 6, y, z)
        support.mover.body = None
        for _ in range(12):
            run.step({})
        look = self.aim(run, support, hog)
        run.step({support: (*look, 16)})
        bubble = fight.zarya_bubbles.for_target(hog)
        self.assertIsNotNone(bubble)
        run.step({doom: (*self.aim(run, doom, hog), 8)})
        self.assertEqual(bubble.health, 150)
        self.assertEqual(self.vitals(run, hog).total, 600)
        self.assertNotIn(hog.body, fight.impulses.live)
        self.assertNotIn(doom.body, fight.doomfist_shields)
        self.assertGreater(doom.mover.velocity[1], 0)

    def test_breather_halves_damage_without_losing_heal_or_reduction(self):
        totals = []
        for buttons in (0, 8):
            run, hog, doom, _ = self.scene()
            fight = run.match.combat
            pool = self.vitals(run, hog)
            pool.damage(400)
            fight._sync_body_health(hog, pool)
            run.step({hog: (0, 0, 16)})
            for _ in range(65):
                run.step({})
                if fight.incoming_damage_scale(hog) == .5 and pool.total > 200:
                    break
            self.assertEqual(fight.incoming_damage_scale(hog), .5)
            before = pool.total
            run.step({doom: (*self.aim(run, doom, hog), buttons)})
            self.assertEqual(hog.body in fight.impulses.live, buttons == 8)
            for _ in range(18):
                run.step({})
            self.assertEqual(fight.incoming_damage_scale(hog), .5)
            self.assertGreater(pool.total, before)
            totals.append(pool.total)
        self.assertAlmostEqual(totals[0] - totals[1], 25)

    def test_lift_keeps_whole_hog_and_clear_chain_hook_running(self):
        for button in (32, 8):
            with self.subTest(button=button):
                run, hog, doom, victim = self.scene()
                fight = run.match.combat
                x, y, z = hog.mover.position
                doom.mover.position[:] = (x, y, z - 1.5)
                victim.mover.position[:] = (x, y, z + 14)
                doom.mover.body = victim.mover.body = None
                for _ in range(12):
                    run.step({})
                ultimate, = hog.body_script.ultimates
                if button == 32:
                    script = hog.body_script
                    script.set_ult_charge(script.component.entity_var(338).value())
                look = self.aim(run, hog, victim)
                run.step({hog: (*look, button)})

                def active(button=button, ultimate=ultimate, fight=fight, victim=victim):
                    if button == 32:
                        return bool(ultimate.flags & nodes.Ability.RUNNING)
                    return fight.hooks.controlled(victim.body)

                for _ in range(55):
                    if active():
                        break
                    run.step({hog: (*look, 0)})
                self.assertTrue(active())
                run.step({doom: (*self.aim(run, doom, hog), 8)})
                self.assertEqual(self.vitals(run, hog).total, 550)
                self.assertTrue(active())
                run.step({})
                self.assertGreater(hog.mover.position[1], y)
                self.assertFalse(fight.is_stunned(hog))
                self.assertTrue(active())
                if button == 32:
                    pool = self.vitals(run, victim)
                    before = pool.total
                    for _ in range(45):
                        run.step({hog: (*self.aim(run, hog, victim), 0)})
                        if pool.total < before:
                            break
                    self.assertLess(pool.total, before)

    def test_uppercut_lift_preserves_buffered_quick_melee(self):
        run, hog, doom, victim = self.scene()
        fight = run.match.combat
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        hog.team, doom.team, victim.team = 0, 1, 1
        x, y, z = hog.mover.position
        doom.mover.position[:] = (x, y, z - 1.5)
        victim.mover.position[:] = (x, y, z + 1.5)
        doom.mover.body = victim.mover.body = None
        run.step({})

        weapon = next(i for i in hog.body_script.component.instances.values() if i.graph.index == 0x01C6)
        ammo = weapon.vars[53].value()
        before = self.vitals(run, victim).total
        melee = (0, 0, 0x800)
        run.step({hog: melee})
        first = run.frame
        self.assertEqual(self.vitals(run, victim).total, before - 30)
        run.step({})
        run.step({hog: melee})
        while run.frame < first + 50:
            run.step({hog: melee})
        run.step({hog: melee, doom: (*self.aim(run, doom, hog), 8)})
        self.assertEqual(self.vitals(run, hog).total, 550)
        self.assertGreater(fight.impulses.velocity(hog.body)[1], 0)

        # Keep the grounded target beside the lift and aim down from the current eye.
        victim.mover.position[:] = (x + 1, y, z + 1)
        victim.mover.body = None
        while run.frame < first + 61:
            run.step({hog: (*self.aim(run, hog, victim), 0x800)})
        self.assertEqual(self.vitals(run, victim).total, before - 30)
        run.step({hog: (*self.aim(run, hog, victim), 0x800)})
        self.assertEqual(run.frame, first + 62)
        self.assertGreater(hog.mover.position[1], y + 1)
        self.assertFalse(fight.is_stunned(hog))
        self.assertEqual(self.vitals(run, victim).total, before - 60)
        for _ in range(65):
            run.step({hog: (*self.aim(run, hog, victim), 0x800)})
        self.assertEqual(self.vitals(run, victim).total, before - 60)
        self.assertEqual(weapon.vars[53].value(), ammo)

    def test_cooldown_rejects_repress_and_fresh_cast_works_after_expiry(self):
        run, hog, doom, _ = self.scene()
        instance = next(i for i in doom.body_script.component.instances.values()
                        if i.graph.index == uppercut.GRAPH)
        ability, effect = instance.state(0), instance.state(10)
        look = self.aim(run, doom, hog)
        run.step({doom: (*look, 8)})
        for _ in range(60):
            run.step({})
        self.assertGreater(ability.remaining(), 0)
        self.assertEqual(effect.counter, 1)
        run.step({doom: (*look, 8)})
        for _ in range(450):
            run.step({})
            if ability.remaining() <= 0:
                break
        self.assertEqual(ability.remaining(), 0)
        self.assertEqual(effect.counter, 1)
        self.assertEqual(self.vitals(run, hog).total, 550)
        for actor, z in ((hog, 0), (doom, 1.5)):
            actor.mover.position[2] = z
            actor.mover.body = None
        look = self.aim(run, doom, hog)
        run.step({doom: (*look, 8)})
        self.assertEqual(effect.counter, 2)
        self.assertEqual(self.vitals(run, hog).total, 500)

    def test_lethal_uppercut_credits_doomfist_and_does_not_launch_corpse(self):
        run, hog, doom, _ = self.scene()
        fight = run.match.combat
        pool = self.vitals(run, hog)
        pool.damage(575)
        fight._sync_body_health(hog, pool)
        run.step({doom: (*self.aim(run, doom, hog), 8)})
        self.assertTrue(pool.dead)
        self.assertEqual(pool.killer, doom.body)
        self.assertNotIn(hog.body, fight.impulses.live)
        self.assertEqual(sum(who is hog for _, who in fight.respawns), 1)
