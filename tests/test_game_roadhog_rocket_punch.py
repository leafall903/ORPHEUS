"""Actual 104319 Rocket Punch inputs against Roadhog."""

import unittest
from dataclasses import replace

import test_game_roadhog_defenses as fixtures
import test_game_roadhog_mei_wall as wall_fixtures
from test_game_mover import box

from ow174.game import combat, doomfist_passive
from ow174.game.collision import World
from ow174.game.content import heroes
from ow174.game.script import nodes


class RoadhogRocketPunchTests(unittest.TestCase):
    def scene(self, distance=6, *, wall=None):
        run, hog, doom, rear = fixtures.RoadhogDefenseTests().session("Doomfist", distance=distance)
        _, y, _ = hog.mover.position
        if wall is not None:
            run.match.collision_world = World(
                box(-30, 30, y - 1, y, -30, 55) + box(-30, 30, y, y + 8, wall - .2, wall),
            )
        return run, hog, doom, rear

    def look(self, run, actor, target):
        x, y, z = target.mover.position
        return fixtures.aim(run.match.combat._eye(actor), (x, y + 1.2, z))

    def charge(self, run, doom, look, frames):
        for _ in range(frames):
            run.step({doom: (*look, 2)})

    def health(self, run, actor):
        return fixtures.RoadhogDefenseTests().health(run, actor)

    def ability(self, doom):
        return next(i.state(0) for i in doom.body_script.component.instances.values()
                    if i.graph.index == 0x0D06)

    def test_charge_release_hits_once_and_wall_bonus_applies_at_contact(self):
        for held, wall, direct, bonus in ((1, None, 50, 0), (90, None, 100, 0),
                                          (1, -1.3, 50, 50), (90, -4, 100, 150)):
            with self.subTest(held=held, wall=wall):
                run, hog, doom, rear = self.scene(wall=wall)
                fight = run.match.combat
                look = self.look(run, doom, hog)
                start = tuple(doom.mover.position)
                self.charge(run, doom, look, held)
                self.assertEqual(self.health(run, hog), 600)
                self.assertEqual(tuple(doom.mover.position), start)
                self.assertEqual(self.ability(doom).remaining(), 0)
                totals = []
                last = 600
                for _ in range(100):
                    run.step({doom: (*look, 0)})
                    now = self.health(run, hog)
                    if now != last:
                        totals.append(now)
                        if not bonus or len(totals) == 1:
                            self.assertTrue(fight.is_stunned(hog))
                        last = now
                self.assertEqual(totals, [600 - direct] + ([600 - direct - bonus] if bonus else []))
                self.assertLess(doom.mover.position[2], start[2])
                self.assertLess(hog.mover.position[2], -.3)
                self.assertEqual(self.health(run, rear), 20000)
                self.assertFalse(fight.is_stunned(hog))
                self.assertFalse(fight.punch_walls)
                self.assertGreater(self.ability(doom).remaining(), 0)

    def test_front_wall_range_and_team_prevent_contact(self):
        for mode in ("wall", "range", "ally"):
            with self.subTest(mode=mode):
                run, hog, doom, _ = self.scene(25 if mode == "range" else 6,
                                               wall=3 if mode == "wall" else None)
                fight = run.match.combat
                if mode == "ally":
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    hog.team = doom.team = 0
                look = self.look(run, doom, hog)
                self.charge(run, doom, look, 90)
                for _ in range(95):
                    run.step({doom: (*look, 0)})
                self.assertEqual(self.health(run, hog), 600)
                self.assertFalse(fight.is_stunned(hog))
                self.assertNotIn(hog.body, fight.impulses.live)
                self.assertGreater(self.ability(doom).remaining(), 0)

    def test_ice_wall_stops_dash_before_covered_roadhog(self):
        run, hog, doom, mei = self.scene(8)
        fight = run.match.combat
        self.assertTrue(run.match.switch_hero(mei, next(h for h in heroes().values() if h.name == "Mei")))
        x, y, z = hog.mover.position
        mei.mover.position[:] = (x, y, z + 10)
        mei.mover.body = None
        for _ in range(10):
            run.step({})
        pillar, _ = wall_fixtures.RoadhogMeiWallTests().place(run, mei, distance=6)
        look = self.look(run, doom, hog)
        self.charge(run, doom, look, 90)
        for _ in range(70):
            run.step({doom: (*look, 0)})
        self.assertEqual(self.health(run, hog), 600)
        self.assertFalse(fight.is_stunned(hog))
        self.assertGreater(doom.mover.position[2], pillar.feet[2])
        self.assertLess(doom.mover.position[2], z + 8)
        self.assertEqual(pillar.health, 400)

    def test_authored_cooldown_refuses_repress_then_accepts_fresh_release(self):
        run, hog, doom, rear = self.scene()
        look = self.look(run, doom, hog)
        ability = self.ability(doom)
        self.charge(run, doom, look, 1)
        for _ in range(80):
            run.step({doom: (*look, 0)})
            if ability.remaining() > 0:
                break
        self.assertEqual(self.health(run, hog), 550)
        self.assertGreater(ability.remaining(), 0)
        for actor, z in ((hog, 0), (doom, 6), (rear, 8)):
            actor.mover.position[2] = z
            actor.mover.body = None
        for _ in range(38):
            run.step({})
        self.charge(run, doom, look, 1)
        for _ in range(50):
            run.step({doom: (*look, 0)})
        self.assertEqual(self.health(run, hog), 550)
        self.assertEqual(doom.mover.position[2], 6)
        for _ in range(300):
            run.step({})
            if ability.remaining() <= 0:
                break
        self.assertEqual(ability.remaining(), 0)
        self.charge(run, doom, look, 1)
        for _ in range(40):
            run.step({doom: (*look, 0)})
        self.assertEqual(self.health(run, hog), 500)

    def test_rocket_punch_discards_buffered_melee_before_wall_bonus(self):
        for wall in (None, -1.3):
            with self.subTest(wall=wall):
                self.check_rocket_punch_discards_melee(wall)

    def check_rocket_punch_discards_melee(self, wall):
        run, hog, doom, victim = self.scene(wall=wall)
        fight = run.match.combat
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        hog.team, doom.team, victim.team = 0, 1, 1
        x, y, z = hog.mover.position
        victim.mover.position[:] = (x + 1.5, y, z)
        victim.mover.body = None
        run.step({})

        look = self.look(run, doom, hog)
        held = {hog: (16384, 0, 0x800)}
        run.step(held)
        first = run.frame
        run.step({})
        run.step({**held, doom: (*look, 2)})
        melee = next(s for i in hog.body_script.component.instances.values() if i.graph.index == 0x0043
                     for s in i.states.values() if isinstance(s, nodes.Ability))
        weapon = next(i for i in hog.body_script.component.instances.values() if i.graph.index == 0x01C6)
        ammo = weapon.vars[53].value()
        hit = melee.instance.state(4)
        self.assertTrue(melee.presses)
        self.assertEqual(hit.counter, 1)
        self.assertEqual(self.health(run, victim), 19970)

        while run.frame < first + 61 and not fight.is_stunned(hog):
            run.step({**held, doom: (*look, 0)})
        self.assertTrue(fight.is_stunned(hog))
        self.assertEqual(self.health(run, hog), 550)
        self.assertFalse(melee.presses)
        for _ in range(5):
            run.step(held)
            self.assertTrue(fight.is_stunned(hog))
        self.assertEqual(hit.counter, 1)
        while run.frame < first + 110 and (fight.is_stunned(hog) or fight.punch_walls):
            run.step({})
        self.assertFalse(fight.is_stunned(hog))
        self.assertFalse(fight.punch_walls)
        self.assertEqual(self.health(run, hog), 550 if wall is None else 500)

        x, y, z = hog.mover.position
        victim.mover.position[:] = (x + 1.5, y, z)
        victim.mover.body = None
        while run.frame <= first + 125:
            run.step({})
        self.assertEqual(hit.counter, 1)
        self.assertEqual(self.health(run, victim), 19970)
        run.step(held)
        self.assertEqual(hit.counter, 2)
        self.assertEqual(self.health(run, victim), 19940)
        self.assertEqual(weapon.vars[53].value(), ammo)

    def test_incoming_punch_interrupts_breather_whole_hog_and_hook(self):
        for ability in ("breather", "whole_hog", "hook"):
            with self.subTest(ability=ability):
                run, hog, doom, victim = self.scene()
                fight = run.match.combat
                x, y, z = hog.mover.position
                doom.mover.position[:] = (x, y, z - 6)
                victim.mover.position[:] = (x, y, z + 18)
                doom.mover.body = victim.mover.body = None
                angles = self.look(run, doom, hog)
                attack = self.look(run, hog, victim)
                target = next(t for t in fight._targets() if t.owner is hog)
                pool = fight.vitals_of(target)
                if ability == "breather":
                    pool.damage(400)
                    fight._sync_body_health(hog, pool)
                elif ability == "whole_hog":
                    hog.body_script.set_ult_charge(hog.body_script.component.entity_var(338).value())
                self.charge(run, doom, angles, 40)
                button = {"breather": 16, "whole_hog": 32, "hook": 8}[ability]
                run.step({doom: (*angles, 2), hog: (*attack, button)})
                for _ in range(49):
                    run.step({doom: (*angles, 2), hog: (*attack, 0)})
                    active = (
                        fight.incoming_damage_scale(hog) == .5 if ability == "breather"
                        else bool(hog.body_script.ultimates[0].flags & nodes.Ability.RUNNING)
                        if ability == "whole_hog" else fight.hooks.controlled(victim.body)
                    )
                    if active:
                        break
                self.assertTrue(active)
                for _ in range(35):
                    run.step({doom: (*angles, 0), hog: (*attack, 0)})
                    if fight.is_stunned(hog):
                        break
                self.assertTrue(fight.is_stunned(hog))
                if ability == "breather":
                    self.assertEqual(fight.incoming_damage_scale(hog), 1)
                    after = pool.total
                    for _ in range(20):
                        run.step({hog: (*attack, 16)})
                    self.assertEqual(pool.total, after)
                elif ability == "whole_hog":
                    self.assertFalse(hog.body_script.ultimates[0].flags & nodes.Ability.RUNNING)
                    self.assertEqual(hog.body_script.component.entity_var(342).value(), 0)
                else:
                    self.assertNotIn(hog.body, fight.hooks.flights)
                    self.assertFalse(fight.hooks.controlled(victim.body))
                    self.assertEqual(self.health(run, victim), 19970)

    def test_roadhog_hook_cancels_enemy_charge_before_release(self):
        run, hog, doom, _ = self.scene()
        fight = run.match.combat
        charge = self.look(run, doom, hog)
        hook = self.look(run, hog, doom)
        self.charge(run, doom, charge, 12)
        run.step({hog: (*hook, 8), doom: (*charge, 2)})
        for _ in range(60):
            run.step({doom: (*charge, 2), hog: (*hook, 0)})
            if fight.hooks.controlled(doom.body):
                break
        self.assertTrue(fight.hooks.controlled(doom.body))
        self.assertEqual(self.health(run, doom), 19970)
        for _ in range(90):
            run.step({doom: (*charge, 0)})
        self.assertEqual(self.health(run, hog), 600)
        self.assertNotIn(doom.body, fight.punch_dashes)

    def test_projected_bubble_prevents_punch_damage_force_and_control(self):
        run, hog, doom, support = self.scene()
        fight = run.match.combat
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        hog.team = support.team = 0
        doom.team = 1
        zarya = next(hero for hero in heroes().values() if hero.name == "Zarya")
        self.assertTrue(run.match.switch_hero(support, zarya))
        x, y, z = hog.mover.position
        support.mover.position[:] = (x + 7, y, z)
        support.mover.body = None
        look = self.look(run, doom, hog)
        self.charge(run, doom, look, 90)
        shield = self.look(run, support, hog)
        run.step({support: (*shield, 16), doom: (*look, 0)})
        bubble = fight.zarya_bubbles.for_target(hog)
        self.assertIsNotNone(bubble)
        for _ in range(30):
            run.step({doom: (*look, 0)})
        self.assertEqual(bubble.health, 100)
        self.assertEqual(self.health(run, hog), 600)
        self.assertNotIn(hog.body, fight.punch_walls)
        self.assertNotIn(hog.body, fight.impulses.live)
        self.assertFalse(fight.is_stunned(hog))
        self.assertNotIn(doom.body, fight.doomfist_shields)

    def test_roadhog_slug_spends_punch_shield_before_base_health(self):
        run, hog, doom, _ = self.scene()
        fight = run.match.combat
        look = self.look(run, doom, hog)
        self.charge(run, doom, look, 90)
        for _ in range(50):
            run.step({doom: (*look, 0)})
            if doom.body in fight.doomfist_shields:
                break
        pool = fight.vitals[doom.body]
        shield = pool.temporary_shields[doomfist_passive.SHIELD_TAG]
        self.assertEqual(shield.now, 30)
        initial = sum(pool.now.values())
        for _ in range(64):
            run.step({})
        self.assertLess(shield.now, 30)
        self.assertGreater(shield.now, 29)
        self.assertFalse(fight.is_stunned(hog))
        look = self.look(run, hog, doom)
        run.step({hog: (*look, 2)})
        for _ in range(25):
            run.step({hog: (*look, 0)})
            if shield.now == 0:
                break
        self.assertEqual(shield.now, 0)
        self.assertGreater(initial - sum(pool.now.values()), 20)
        self.assertLess(initial - sum(pool.now.values()), 22)
        self.assertNotIn(doom.body, fight.doomfist_shields)

    def test_repeated_real_punches_saturate_shield_then_roadhog_spends_only_that_pool(self):
        run, hog, doom, _ = self.scene()
        fight = run.match.combat
        fight.vitals[hog.body] = combat.Vitals(hog.body, 2000)
        y = hog.mover.position[1]
        ability = self.ability(doom)
        grants = []

        for _ in range(12):
            for _ in range(330):
                if ability.remaining() == 0:
                    break
                run.step({})
            self.assertEqual(ability.remaining(), 0)
            for actor, z in ((hog, 0), (doom, 6)):
                actor.mover.position[:] = (0, y, z)
                actor.mover.body = None
            run.step({})
            before = self.health(run, hog)
            look = self.look(run, doom, hog)
            self.charge(run, doom, look, 1)
            for _ in range(80):
                run.step({doom: (*look, 0)})
                if self.health(run, hog) < before:
                    break
            self.assertEqual(self.health(run, hog), before - 50)
            pool = fight.vitals[doom.body]
            shield = pool.temporary_shields[doomfist_passive.SHIELD_TAG]
            self.assertLessEqual(shield.now, 150)
            grants.append(shield.now)
            if shield.now == 150:
                break
            for _ in range(60):
                if ability.remaining() > 0:
                    break
                run.step({})
            self.assertGreater(ability.remaining(), 0)

        self.assertEqual(grants[0], 30)
        self.assertEqual(grants[-1], 150)
        self.assertGreater(len(grants), 5)  # Natural cooldowns allow decay between punches.
        for _ in range(50):
            if not fight.is_stunned(hog):
                break
            run.step({})
        self.assertFalse(fight.is_stunned(hog))
        self.assertEqual(shield.now, 150)
        base = pool.permanent_total
        charge = hog.body_script.component.entity_var(342)
        self.assertEqual(charge.value(), 0)
        look = self.look(run, hog, doom)
        run.step({hog: (*look, 2)})
        for _ in range(18):
            if shield.now < 150:
                break
            run.step({hog: (*look, 0)})
        self.assertEqual(shield.now, 100)
        self.assertEqual(pool.permanent_total, base)
        self.assertEqual(charge.value(), 0)

    def test_wall_bonus_kills_roadhog_once_and_normal_respawn_clears_contact(self):
        run, hog, doom, _ = self.scene(wall=-4)
        fight = run.match.combat
        body = hog.body
        target = next(t for t in fight._targets() if t.owner is hog)
        pool = fight.vitals_of(target)
        pool.damage(475)
        fight._sync_body_health(hog, pool)
        look = self.look(run, doom, hog)
        self.charge(run, doom, look, 90)
        for _ in range(65):
            run.step({doom: (*look, 0)})
            if body in fight.punch_walls:
                break
        self.assertEqual(pool.total, 25)
        self.assertIn(body, fight.punch_walls)
        for _ in range(30):
            run.step({})
            if pool.dead:
                break
        self.assertTrue(pool.dead)
        self.assertEqual(pool.killer, doom.body)
        self.assertNotIn(body, fight.punch_walls)
        self.assertFalse(fight.is_stunned(hog))
        self.assertEqual(sum(who is hog for _, who in fight.respawns), 1)
        for _ in range(1000):
            run.step({})
            if hog.body != body:
                break
        self.assertNotEqual(hog.body, body)
        self.assertEqual(self.health(run, hog), 600)
        self.assertNotIn(body, fight.impulses.live)
