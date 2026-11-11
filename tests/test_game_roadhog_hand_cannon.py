"""Actual 104319 Hand Cannon release, collision and attribution against Roadhog."""

import unittest
from dataclasses import replace

import test_game_roadhog_rocket_punch as fixtures
from test_game_combat import aim

from ow174.game import combat, hand_cannon, stats
from ow174.game.content import SOLDIER, heroes
from ow174.game.script import expr


class RoadhogHandCannonTests(unittest.TestCase):
    def scene(self, distance=2, *, wall=None):
        run, hog, doom, rear = fixtures.RoadhogRocketPunchTests().scene(distance, wall=wall)
        rear.mover.position[0] += 25
        rear.mover.body = None
        for _ in range(12):
            run.step({})
        return run, hog, doom, rear

    def vitals(self, run, actor):
        fight = run.match.combat
        return fight.vitals_of(next(t for t in fight._targets() if t.owner is actor))

    def look(self, run, doom, target, height=1.2):
        x, y, z = target.mover.position
        return aim(run.match.combat._eye(doom), (x, y + height, z))

    def weapon(self, doom):
        return next(i for i in doom.body_script.component.instances.values()
                    if i.graph.index == hand_cannon.GRAPH)

    def ammo(self, doom):
        return self.weapon(doom).find_var(expr.INSTANCE, 53).value()

    def fire(self, run, doom, look):
        run.step({doom: (*look, 1)})
        return [pellet for pellet in run.match.combat.rockets if pellet.shooter is doom]

    def test_one_ammo_emits_eleven_directions_and_credits_real_damage(self):
        run, hog, doom, _ = self.scene()
        self.assertEqual(self.ammo(doom), 4)
        pellets = self.fire(run, doom, self.look(run, doom, hog))
        self.assertEqual(len(pellets), 11)
        self.assertEqual(len({pellet.direction for pellet in pellets}), 11)
        self.assertTrue(all(pellet.direct.critical == -1 for pellet in pellets))
        self.assertTrue(all(pellet.source_body == doom.body for pellet in pellets))
        self.assertLessEqual(sum(pellet.shot is not None for pellet in pellets), 1)
        self.assertEqual(self.ammo(doom), 3)
        self.assertEqual(self.vitals(run, hog).total, 600)
        for _ in range(25):
            run.step({})
        self.assertEqual(self.vitals(run, hog).total, 534)
        self.assertEqual(doom.body_script.component.entity_var(342).value(), 49.5)
        self.assertEqual(len(doom.client.messages(combat.HIT)), 11)

    def test_breather_halves_each_pellet_and_keeps_healing(self):
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
        self.fire(run, doom, self.look(run, doom, hog))
        for _ in range(10):
            run.step({})
        self.assertEqual(doom.stats.value(stats.HERO_DAMAGE), 33)
        self.assertEqual(doom.body_script.component.entity_var(342).value(), 24.75)
        before = pool.total
        for _ in range(13):
            run.step({})
        self.assertEqual(fight.incoming_damage_scale(hog), .5)
        self.assertGreater(pool.total, before)

    def test_actual_high_contacts_keep_the_null_critical_fallback(self):
        run, hog, doom, _ = self.scene()
        fight = run.match.combat
        look = aim(fight._eye(doom), fight._eye(hog))
        pellets = self.fire(run, doom, look)
        target = next(t for t in fight._targets() if t.owner is hog)
        a, b = target.segment()
        contacts = [(pellet, combat.ray_capsule(pellet.position, pellet.direction, a, b, target.radius))
                    for pellet in pellets]
        threshold = target.feet[1] + target.eye - combat.HEAD_BELOW
        heights = [pellet.position[1] + distance * pellet.direction[1]
                   for pellet, distance in contacts if distance is not None]
        self.assertTrue(any(height >= threshold for height in heights))
        for _ in range(18):
            run.step({})
        hits = doom.client.messages(combat.HIT)
        self.assertTrue(hits)
        self.assertTrue(all(hit["+0x78"]["+0x4"] == 6 for hit in hits))
        self.assertTrue(all(not hit["+0x78"]["+0x8"] & combat.CRITICAL for hit in hits))

    def test_wall_team_and_off_axis_reject_the_real_fan(self):
        for mode in ("wall", "team", "miss"):
            with self.subTest(mode=mode):
                run, hog, doom, _ = self.scene(5, wall=2.5 if mode == "wall" else None)
                if mode == "team":
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    hog.team = doom.team = 0
                look = (0, 0) if mode == "miss" else self.look(run, doom, hog)
                self.assertEqual(len(self.fire(run, doom, look)), 11)
                for _ in range(35):
                    run.step({})
                self.assertEqual(self.vitals(run, hog).total, 600)
                self.assertFalse(doom.client.messages(combat.HIT))
                self.assertEqual(doom.body_script.component.entity_var(342).value(), 0)

    def test_in_flight_hand_cannon_uses_both_live_teams_for_damage_and_credit(self):
        cases = (
            ((1, 0), (0, 0)),
            ((1, 0), (1, 1)),
            ((0, 0), (1, 0)),
            ((0, 0), (0, 1)),
            ((1, 0), (0, 1)),
            ((0, 0), (1, 1)),
        )
        for launch, contact in cases:
            with self.subTest(launch=launch, contact=contact):
                run, hog, doom, _ = self.scene()
                match = run.match
                fight = match.combat
                match.game_map = replace(match.game_map, free_for_all=False)
                doom.team, hog.team = launch
                doom.body_script.ult_seconds = 0
                charge = doom.body_script.component.entity_var(342)
                pool = self.vitals(run, hog)
                bodies = doom.body, hog.body
                pellets = self.fire(run, doom, self.look(run, doom, hog))
                self.assertEqual(len(pellets), 11)
                self.assertTrue(all(pellet.team == launch[0] for pellet in pellets))
                self.assertEqual(pool.total, 600)
                self.assertEqual(self.ammo(doom), 3)

                doom.team, hog.team = contact
                for _ in range(45):
                    run.step({})
                loss = 66 if contact[0] != contact[1] else 0
                self.assertEqual((doom.body, hog.body), bodies)
                self.assertFalse(fight.rockets)
                self.assertEqual(pool.total, 600 - loss)
                self.assertEqual(pool.ult_healing_deficit, loss)
                self.assertEqual(doom.stats.value(stats.HERO_DAMAGE), loss)
                self.assertEqual(charge.value(), loss * 0.75)
                hits = doom.client.messages(combat.HIT)
                self.assertEqual(len(hits), 11 if loss else 0)
                self.assertTrue(all(hit["+0x78"]["+0x0"]["+0x0"] == hog.body for hit in hits))

    def test_long_range_fan_has_real_misses_and_damage_falloff(self):
        run, hog, doom, _ = self.scene(28)
        self.assertEqual(len(self.fire(run, doom, self.look(run, doom, hog))), 11)
        for _ in range(48):
            run.step({})
        count = len(doom.client.messages(combat.HIT))
        self.assertGreater(count, 0)
        self.assertLess(count, 11)
        damage = 600 - self.vitals(run, hog).total
        self.assertGreater(damage, 0)
        self.assertLess(damage, count * 6)

    def test_four_rounds_then_authored_refill_and_fresh_release(self):
        run, hog, doom, _ = self.scene()
        fight = run.match.combat
        look = (0, 0)
        for expected in (3, 2, 1, 0):
            self.fire(run, doom, look)
            self.assertEqual(self.ammo(doom), expected)
            for _ in range(20):
                run.step({})
        count = self.weapon(doom).state(2).counter
        self.fire(run, doom, look)
        for _ in range(205):
            run.step({})
            if self.ammo(doom) == 4:
                break
        self.assertEqual(self.ammo(doom), 4)
        self.assertEqual(self.weapon(doom).state(2).counter, count)
        self.assertEqual(self.vitals(run, hog).total, 600)
        before = {id(pellet) for pellet in fight.rockets}
        self.assertEqual(sum(id(pellet) not in before for pellet in self.fire(run, doom, look)), 11)
        self.assertEqual(self.ammo(doom), 3)

    def test_hook_suppresses_hand_cannon_until_release(self):
        run, hog, doom, _ = self.scene()
        fight = run.match.combat
        hook = self.look(run, hog, doom)
        run.step({hog: (*hook, 8)})
        for _ in range(55):
            run.step({hog: (*hook, 0)})
            if fight.hooks.controlled(doom.body):
                break
        self.assertTrue(fight.hooks.controlled(doom.body))
        for _ in range(10):
            look = self.look(run, doom, hog)
            run.step({doom: (*look, 1)})
            self.assertTrue(fight.hooks.controlled(doom.body))
            self.assertEqual(self.ammo(doom), 4)
            self.assertFalse(fight.rockets)
        for _ in range(95):
            if not fight.hooks.controlled(doom.body):
                break
            run.step({})
        self.assertFalse(fight.hooks.controlled(doom.body))
        self.assertEqual(self.ammo(doom), 4)
        run.step({})
        self.assertEqual(len(self.fire(run, doom, self.look(run, doom, hog))), 11)
        self.assertEqual(self.ammo(doom), 3)

    def test_fired_pellets_keep_old_body_kill_identity_without_new_body_credit(self):
        for swap_teams in (False, True):
            with self.subTest(swap_teams=swap_teams):
                self.assert_old_body_kill_credit(swap_teams)

    def assert_old_body_kill_credit(self, swap_teams):
        run, hog, doom, _ = self.scene(12)
        match = run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        doom.team, hog.team = 1, 0
        pool = self.vitals(run, hog)
        pool.damage(599)
        fight._sync_body_health(hog, pool)
        old = doom.body
        self.assertEqual(len(self.fire(run, doom, self.look(run, doom, hog))), 11)
        self.assertTrue(match.switch_hero(doom, heroes()[SOLDIER]))
        self.assertNotEqual(doom.body, old)
        if swap_teams:
            doom.team, hog.team = 0, 1
        self.assertEqual(sum(p.source_body == old for p in fight.rockets), 11)
        doom.body_script.ult_seconds = 0
        doom.body_script.set_ult_charge(0)
        for _ in range(22):
            run.step({})
        self.assertTrue(pool.dead)
        self.assertEqual(pool.killer, old)
        self.assertEqual(doom.body_script.component.entity_var(342).value(), 0)
        self.assertFalse(doom.client.messages(combat.HIT))

    def test_projected_barrier_absorbs_all_eleven_pellets(self):
        run, hog, doom, zarya = self.scene()
        fight = run.match.combat
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        hog.team = zarya.team = 0
        doom.team = 1
        hero = next(h for h in heroes().values() if h.name == "Zarya")
        self.assertTrue(run.match.switch_hero(zarya, hero))
        x, y, z = hog.mover.position
        zarya.mover.position[:] = (x + 7, y, z)
        zarya.mover.body = None
        for _ in range(30):
            run.step({})
        run.step({zarya: (*self.look(run, zarya, hog), 16)})
        bubble = fight.zarya_bubbles.for_target(hog)
        self.assertIsNotNone(bubble)
        self.fire(run, doom, self.look(run, doom, hog))
        for _ in range(22):
            run.step({})
        self.assertEqual(bubble.health, 134)
        self.assertEqual(self.vitals(run, hog).total, 600)
        self.assertEqual(doom.body_script.component.entity_var(342).value(), 0)

    def test_genji_and_sigma_protect_roadhog_from_the_fan(self):
        for name, button in (("Genji", 16), ("Sigma", 8)):
            with self.subTest(name=name):
                run, hog, doom, defender = self.scene(5)
                fight = run.match.combat
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                hog.team = defender.team = 0
                doom.team = 1
                hero = next(h for h in heroes().values() if h.name == name)
                self.assertTrue(run.match.switch_hero(defender, hero))
                x, y, z = hog.mover.position
                defender.mover.position[:] = (x, y, z + 2)
                defender.mover.body = None
                for _ in range(45):
                    run.step({})
                health = self.vitals(run, defender).total
                face = aim(fight._eye(defender), fight._eye(doom))
                run.step({defender: (*face, button)})
                look = aim(fight._eye(doom), fight._eye(defender))
                self.fire(run, doom, look)
                for _ in range(28):
                    run.step({defender: (*face, 0)})
                self.assertEqual(self.vitals(run, hog).total, 600)
                self.assertEqual(self.vitals(run, defender).total, health)
                self.assertEqual(doom.body_script.component.entity_var(342).value(), 0)
                if name == "Genji":
                    self.assertLess(self.vitals(run, doom).total, 20000)
                    self.assertEqual(len(defender.client.messages(combat.HIT)), 11)
                else:
                    grasp = next(i for i in defender.body_script.component.instances.values()
                                 if i.graph.index == 0x1D02)
                    self.assertEqual(grasp.vars[21312].value(), 66)
                    self.assertEqual(self.vitals(run, doom).total, 20000)
