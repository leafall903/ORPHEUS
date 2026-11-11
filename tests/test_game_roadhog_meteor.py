"""Real 104319 Meteor Strike stages against Roadhog commands."""

import unittest
from dataclasses import replace

import test_game_roadhog_rocket_punch as fixtures
from test_game_combat import aim

from ow174.game import combat, defenses, doomfist_passive, meteor_strike, stats
from ow174.game.commands import Command
from ow174.game.content import SOLDIER, heroes
from ow174.game.script import nodes


class RoadhogMeteorTests(unittest.TestCase):
    def scene(self, distance=1.5, *, wall=None, rear=False):
        run, hog, doom, other = fixtures.RoadhogRocketPunchTests().scene(distance, wall=wall)
        if not rear:
            other.mover.position[0] += 25
            other.mover.body = None
        for _ in range(8):
            run.step({})
        return run, hog, doom, other

    def vitals(self, run, actor):
        fight = run.match.combat
        return fight.vitals_of(next(t for t in fight._targets() if t.owner is actor))

    def start(self, run, doom, *, charged=True):
        fight = run.match.combat
        script = doom.body_script
        script.set_ult_charge(script.component.entity_var(338).value() if charged else 0)
        run.step({doom: (0, 0, 32)})
        return fight.meteors.get(doom.body)

    def select(self, run, doom):
        for _ in range(77):
            if meteor_strike.aiming(meteor_strike.phase_of(doom)):
                return
            run.step({})
        self.fail("actual 0C51 did not reach selection")

    def land(self, run, doom, cast, *, confirm=True):
        if confirm:
            run.step({doom: (0, 0, 1)})
            self.assertTrue(cast.committed)
        for _ in range(380):
            if cast.impacted:
                return
            run.step({})
        self.fail("actual 0C51 did not impact")

    def test_uncharged_q_refused(self):
        run, hog, doom, _ = self.scene()
        self.assertIsNone(self.start(run, doom, charged=False))
        for _ in range(80):
            run.step({})
        self.assertEqual(self.vitals(run, hog).total, 600)
        self.assertEqual(defenses.protection(doom), (False, False))
        self.assertIsNone(run.match.combat.meteors.get(doom.body))

    def test_confirm_and_timeout_land_once_with_authored_damage_and_cost(self):
        for confirm, distance, damage in ((True, 1.5, 300), (False, 1.5, 300), (True, 5, 107.5)):
            with self.subTest(confirm=confirm, distance=distance):
                run, hog, doom, _ = self.scene(distance)
                fight = run.match.combat
                cast = self.start(run, doom)
                script = doom.body_script
                charge = script.component.entity_var(342)
                full = script.component.entity_var(338).value()
                self.assertEqual(defenses.protection(doom), (True, True))
                self.select(run, doom)
                self.assertEqual(self.vitals(run, hog).total, 600)
                self.assertEqual(charge.value(), full)
                self.land(run, doom, cast, confirm=confirm)
                self.assertTrue(cast.committed)
                self.assertEqual(defenses.protection(doom), (False, False))
                self.assertEqual(self.vitals(run, hog).total, 600 - damage)
                self.assertEqual(cast.hit, (hog.body,))
                self.assertEqual(len(doom.client.messages(combat.HIT)), 1)
                self.assertGreater(fight.impulses.velocity(hog.body)[1], 0)
                shield = self.vitals(run, doom).temporary_shields[doomfist_passive.SHIELD_TAG]
                self.assertEqual(shield.now, 75)
                for _ in range(50):
                    run.step({doom: (0, 0, 1)})
                self.assertTrue(cast.finished)
                self.assertEqual(charge.value(), 0)
                self.assertEqual(self.vitals(run, hog).total, 600 - damage)
                self.assertEqual(len(doom.client.messages(combat.HIT)), 1)

    def test_meteor_core_and_ring_preserve_buffered_quick_melee(self):
        for distance, damage in ((1.5, 300), (5, 107.5)):
            with self.subTest(distance=distance):
                self.check_meteor_preserves_melee(distance, damage)

    def check_meteor_preserves_melee(self, distance, damage):
        run, hog, doom, victim = self.scene(distance)
        fight = run.match.combat
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        hog.team, doom.team, victim.team = 0, 1, 1
        x, y, z = hog.mover.position
        victim.mover.position[:] = (x + 1.5, y, z)
        victim.mover.body = None
        cast = self.start(run, doom)
        self.select(run, doom)
        run.step({doom: (0, 0, 1)})
        self.assertTrue(cast.committed)
        for _ in range(9):
            run.step({})

        weapon = next(i for i in hog.body_script.component.instances.values() if i.graph.index == 0x01C6)
        ammo = weapon.vars[53].value()
        target = (x + 1.5, y + 1.2, z)
        victim_pool = self.vitals(run, victim)
        before = victim_pool.total
        run.step({hog: (16384, 0, 0x800)})
        first = run.frame
        run.step({})
        run.step({hog: (16384, 0, 0x800)})
        self.assertFalse(cast.impacted)
        self.assertEqual(victim_pool.total, before - 30)
        while run.frame < first + 61:
            run.step({hog: (*aim(fight._eye(hog), target), 0x800)})
        self.assertTrue(cast.impacted)
        self.assertEqual(cast.hit, (hog.body,))
        self.assertEqual(self.vitals(run, hog).total, 600 - damage)
        self.assertEqual(victim_pool.total, before - 30)

        run.step({hog: (*aim(fight._eye(hog), target), 0x800)})
        self.assertEqual(run.frame, first + 62)
        self.assertGreater(hog.mover.position[1], y)
        self.assertFalse(fight.is_stunned(hog))
        self.assertEqual(victim_pool.total, before - 60)
        for _ in range(65):
            run.step({hog: (*aim(fight._eye(hog), target), 0x800)})
        self.assertEqual(victim_pool.total, before - 60)
        self.assertEqual(weapon.vars[53].value(), ammo)

    def test_selection_moves_the_landing_anchor_and_descent_holds_it(self):
        run, hog, doom, _ = self.scene(5)
        fight = run.match.combat
        cast = self.start(run, doom)
        self.select(run, doom)
        start = cast.target
        for _ in range(25):
            at = run.frame + 1
            run.frame = at
            run.match.update(at * .016, at)
            doom.take_commands([Command(at, yaw=-32768, forward=127)])
            hog.take_commands([Command(at)])
        self.assertLess(cast.target[2], start[2] - 1)
        self.assertEqual(self.vitals(run, hog).total, 600)
        run.step({doom: (0, 0, 1)})
        target = cast.target
        for _ in range(82):
            at = run.frame + 1
            run.frame = at
            run.match.update(at * .016, at)
            doom.take_commands([Command(at, yaw=-32768, forward=127)])
            hog.take_commands([Command(at)])
            if cast.impacted:
                break
        self.assertTrue(cast.impacted)
        self.assertEqual(cast.target, target)
        self.assertEqual(tuple(doom.mover.position), target)
        self.assertGreater(doom.stats.value(stats.HERO_DAMAGE), 107.5)
        self.assertFalse(fight.is_stunned(hog))

    def test_actual_inner_and_outer_radius_boundaries(self):
        for distance, expected in ((2, 300), (2.0625, 200 - .0625 * 185 / 6),
                                   (8, 15), (8.0625, 0)):
            with self.subTest(distance=distance):
                run, hog, doom, _ = self.scene(distance)
                cast = self.start(run, doom)
                self.select(run, doom)
                self.land(run, doom, cast)
                self.assertAlmostEqual(600 - self.vitals(run, hog).total, expected, places=4)
                self.assertEqual(cast.hit, (hog.body,) if expected else ())

    def test_range_static_cover_and_team_exclude_impact_but_spend_charge(self):
        for mode in ("range", "wall", "ally"):
            with self.subTest(mode=mode):
                run, hog, doom, _ = self.scene(10 if mode == "range" else 5,
                                               wall=2.5 if mode == "wall" else None)
                fight = run.match.combat
                if mode == "ally":
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    hog.team = doom.team = 0
                cast = self.start(run, doom)
                self.select(run, doom)
                self.land(run, doom, cast)
                self.assertEqual(cast.hit, ())
                self.assertEqual(self.vitals(run, hog).total, 600)
                self.assertNotIn(hog.body, fight.impulses.live)
                self.assertNotIn(doom.body, fight.doomfist_shields)
                for _ in range(45):
                    run.step({})
                self.assertEqual(doom.body_script.component.entity_var(342).value(), 0)

    def test_roadhog_weapons_cannot_damage_protected_meteor_body(self):
        for button in (1, 2, 0x800, 8, 32):
            with self.subTest(button=button):
                run, hog, doom, _ = self.scene()
                fight = run.match.combat
                cast = self.start(run, doom)
                self.select(run, doom)
                before = self.vitals(run, doom).total
                script = hog.body_script
                if button == 32:
                    script.set_ult_charge(script.component.entity_var(338).value())
                look = aim(fight._eye(hog), fight._eye(doom))
                run.step({hog: (*look, button)})
                for _ in range(65):
                    run.step({hog: (*look, 0)})
                    self.assertFalse(fight.hooks.controlled(doom.body))
                self.assertFalse(cast.committed)
                self.assertEqual(defenses.protection(doom), (True, True))
                self.assertEqual(self.vitals(run, doom).total, before)
                self.assertFalse(hog.client.messages(combat.HIT))
                self.assertEqual(script.component.entity_var(342).value(), 0)

    def test_hook_tethers_in_recovery_and_does_not_bypass_meteor_cost(self):
        run, hog, doom, _ = self.scene()
        fight = run.match.combat
        cast = self.start(run, doom)
        self.select(run, doom)
        self.land(run, doom, cast)
        before = self.vitals(run, doom).total
        charge = doom.body_script.component.entity_var(342)
        self.assertGreater(charge.value(), 0)
        look = aim(fight._eye(hog), fight._eye(doom))
        run.step({hog: (*look, 8)})
        for _ in range(55):
            run.step({hog: (*look, 0)})
            if fight.hooks.controlled(doom.body):
                break
        self.assertTrue(fight.hooks.controlled(doom.body))
        self.assertEqual(self.vitals(run, doom).total, before - 30)
        for _ in range(48):
            run.step({})
        self.assertEqual(charge.value(), 0)
        self.assertTrue(cast.finished)
        self.assertEqual(self.vitals(run, hog).total, 300)

    def test_already_launched_hook_cannot_attach_after_meteor_begins(self):
        run, hog, doom, _ = self.scene(10)
        fight = run.match.combat
        look = aim(fight._eye(hog), fight._eye(doom))
        run.step({hog: (*look, 8)})
        for _ in range(45):
            if hog.body in fight.hooks.flights:
                break
            run.step({hog: (*look, 0)})
        self.assertIn(hog.body, fight.hooks.flights)
        self.assertFalse(fight.hooks.controlled(doom.body))
        cast = self.start(run, doom)
        self.assertIsNotNone(cast)
        for _ in range(55):
            run.step({hog: (*look, 0)})
            self.assertFalse(fight.hooks.controlled(doom.body))
        self.assertEqual(self.vitals(run, doom).total, 20000)
        self.assertFalse(hog.client.messages(combat.HIT))
        self.assertEqual(defenses.protection(doom), (True, True))

    def test_breather_halves_damage_and_keeps_healing(self):
        run, hog, doom, _ = self.scene()
        fight = run.match.combat
        pool = self.vitals(run, hog)
        pool.damage(400)
        fight._sync_body_health(hog, pool)
        cast = self.start(run, doom)
        self.select(run, doom)
        run.step({doom: (0, 0, 1)})
        for _ in range(23):
            run.step({})
        run.step({hog: (0, 0, 16)})
        self.land(run, doom, cast, confirm=False)
        self.assertEqual(fight.incoming_damage_scale(hog), .5)
        self.assertEqual(doom.stats.value(stats.HERO_DAMAGE), 150)
        self.assertFalse(fight.is_stunned(hog))
        before = pool.total
        for _ in range(15):
            run.step({})
        self.assertGreater(pool.total, before)

    def test_projected_barrier_consumes_impact_without_body_damage_or_shields(self):
        run, hog, doom, support = self.scene(5)
        fight = run.match.combat
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        hog.team = support.team = 0
        doom.team = 1
        zarya = next(h for h in heroes().values() if h.name == "Zarya")
        self.assertTrue(run.match.switch_hero(support, zarya))
        x, y, z = hog.mover.position
        support.mover.position[:] = (x + 8, y, z)
        support.mover.body = None
        cast = self.start(run, doom)
        self.select(run, doom)
        run.step({doom: (0, 0, 1)})
        for _ in range(19):
            run.step({})
        look = aim(fight._eye(support), (x, y + 1.2, z))
        run.step({support: (*look, 16)})
        bubble = fight.zarya_bubbles.for_target(hog)
        self.assertIsNotNone(bubble)
        self.land(run, doom, cast, confirm=False)
        self.assertEqual(bubble.health, 92.5)
        self.assertEqual(self.vitals(run, hog).total, 600)
        self.assertNotIn(hog.body, fight.impulses.live)
        self.assertNotIn(doom.body, fight.doomfist_shields)

    def test_primary_passes_through_protected_doomfist_to_enemy_behind(self):
        run, hog, doom, rear = self.scene(rear=True)
        fight = run.match.combat
        cast = self.start(run, doom)
        self.select(run, doom)
        look = aim(fight._eye(hog), fight._eye(doom))
        run.step({hog: (*look, 1)})
        for _ in range(35):
            run.step({})
        self.assertEqual(self.vitals(run, doom).total, 20000)
        self.assertLess(self.vitals(run, rear).total, 20000)
        self.assertEqual(cast.hit, ())

    def test_lift_keeps_whole_hog_hitting(self):
        run, hog, doom, victim = self.scene(5)
        fight = run.match.combat
        x, y, z = hog.mover.position
        doom.mover.position[:] = (x, y, z - 5)
        victim.mover.position[:] = (x, y, z + 15)
        doom.mover.body = victim.mover.body = None
        for _ in range(8):
            run.step({})
        cast = self.start(run, doom)
        self.select(run, doom)
        run.step({doom: (0, 0, 1)})
        for _ in range(53):
            run.step({})
        script = hog.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        ultimate, = script.ultimates
        look = aim(fight._eye(hog), (x, y + 1.2, z + 15))
        run.step({hog: (*look, 32)})
        self.land(run, doom, cast, confirm=False)
        self.assertIn(hog.body, fight.impulses.live)
        self.assertFalse(fight.is_stunned(hog))
        self.assertTrue(ultimate.flags & nodes.Ability.RUNNING)
        pool = self.vitals(run, victim)
        before = pool.total
        for _ in range(35):
            look = aim(fight._eye(hog), (x, y + 1.2, z + 15))
            run.step({hog: (*look, 0)})
            if pool.total < before:
                break
        self.assertLess(pool.total, before)

    def test_lethal_meteor_credits_doomfist_without_launching_roadhogs_corpse(self):
        run, hog, doom, _ = self.scene()
        fight = run.match.combat
        pool = self.vitals(run, hog)
        pool.damage(400)
        fight._sync_body_health(hog, pool)
        old_body = hog.body
        cast = self.start(run, doom)
        self.select(run, doom)
        self.land(run, doom, cast)
        self.assertTrue(pool.dead)
        self.assertEqual(pool.killer, doom.body)
        self.assertNotIn(old_body, fight.impulses.live)
        self.assertEqual(sum(who is hog for _, who in fight.respawns), 1)
        for _ in range(1000):
            run.step({})
            if hog.body != old_body:
                break
        self.assertNotEqual(hog.body, old_body)
        self.assertEqual(self.vitals(run, hog).total, 600)
        self.assertNotIn(old_body, fight.impulses.live)

    def test_body_replacement_retires_confirmed_but_pending_impact(self):
        run, hog, doom, _ = self.scene()
        fight = run.match.combat
        old_body = doom.body
        cast = self.start(run, doom)
        self.select(run, doom)
        run.step({doom: (0, 0, 1)})
        self.assertTrue(cast.committed)
        self.assertFalse(cast.impacted)
        self.assertTrue(run.match.switch_hero(doom, heroes()[SOLDIER]))
        self.assertNotEqual(doom.body, old_body)
        for _ in range(110):
            run.step({})
        self.assertFalse(cast.impacted)
        self.assertNotIn(old_body, fight.meteors)
        self.assertEqual(self.vitals(run, hog).total, 600)
        self.assertEqual(defenses.protection(doom), (False, False))
