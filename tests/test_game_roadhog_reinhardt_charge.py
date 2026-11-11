"""Authored Reinhardt Charge input against Roadhog's real command path."""

import os
import unittest
from dataclasses import replace
from unittest.mock import patch

import test_game_roadhog_defenses as fixtures

from ow174.game import combat, reinhardt_charge, stats
from ow174.game.collision import World
from ow174.game.commands import Command
from ow174.game.script import nodes
from ow174.game.script.driver import ULT_CHARGE, ULT_COST


class RoadhogReinhardtChargeTests(unittest.TestCase):
    def scene(self, *, wall=None, target_x=0, distance=6):
        fixture = fixtures.RoadhogDefenseTests()
        run, hog, rein, third = fixture.session("Reinhardt")
        for actor, position in ((rein, (0, 1, 0)), (hog, (target_x, 1, distance)),
                                (third, (18, 1, distance))):
            actor.mover.position[:] = position
            actor.mover.body = None
        if wall is not None:
            run.match.collision_world = World([
                (-30, 1, -30, -30, 1, 80, 30, 1, 80),
                (-30, 1, -30, 30, 1, 80, 30, 1, -30),
                (-15, -5, wall, 15, 10, wall, 15, -5, wall),
                (-15, -5, wall, -15, 10, wall, 15, 10, wall),
            ])
        for _ in range(12):
            run.step({})
        return run, hog, rein, third

    def test_late_input_during_reinhardt_pin_cannot_cast_after_release(self):
        for wall in (False, True):
            with self.subTest(wall=wall):
                run, hog, rein, _ = self.scene(wall=13 if wall else None)
                run.step({rein: (0, 0, 8)})
                self.check_late_charge_pin_input(run, hog, wall)

    @unittest.skipUnless(os.environ.get("ORPHEUS_NPC_GRAPHS"), "actual NPC graphs required")
    def test_late_input_during_heavy_assault_pin_cannot_cast_after_release(self):
        from test_game_roadhog_npcs import RoadhogFiveNpcTests

        RoadhogFiveNpcTests.setUpClass()
        self.addCleanup(RoadhogFiveNpcTests.tearDownClass)
        fixture = RoadhogFiveNpcTests()
        for wall in (False, True):
            with self.subTest(wall=wall):
                run, hog, _, _, _ = fixture.heavy_charge_scene(wall=wall, wall_z=13)
                self.check_late_charge_pin_input(run, hog, wall)

    def check_late_charge_pin_input(self, run, hog, wall):
        fight = run.match.combat
        for _ in range(120):
            if reinhardt_charge.pinned(fight, hog.body):
                break
            run.step({})
        self.assertTrue(reinhardt_charge.pinned(fight, hog.body))
        script = hog.body_script
        initial_frame = script.frame
        pool = fight.vitals[hog.body]
        before = pool.total
        script.ult_seconds = 0
        cost = script.component.entity_var(ULT_COST).value()
        script.set_ult_charge(cost)
        charge = script.component.entity_var(ULT_CHARGE)
        ultimate, = script.ultimates
        with patch.object(hog, "take_commands"):
            for _ in range(250):
                if not reinhardt_charge.pinned(fight, hog.body):
                    break
                run.step({})
            self.assertFalse(reinhardt_charge.pinned(fight, hog.body))
            last_pinned = run.frame - 1
            self.assertGreater(last_pinned, initial_frame)
            self.assertEqual(script.frame, initial_frame)
            for _ in range(2):
                run.step({})
        cutoff = fight.hard_control_command_cutoffs[hog.body]
        self.assertLessEqual(last_pinned, cutoff)
        self.assertLess(cutoff, run.frame)
        after_release = pool.total
        self.assertEqual(before - after_release, 300 if wall else 0)
        pose = tuple(hog.mover.position)
        hog.take_commands([Command(cutoff, buttons=32, forward=127), Command(run.frame)])
        self.assertFalse(ultimate.flags & nodes.Ability.RUNNING)
        self.assertEqual(charge.value(), cost)
        self.assertEqual(pool.total, after_release)
        self.assertEqual(tuple(hog.mover.position), pose)
        run.step({hog: (0, 0, 32)})
        self.assertTrue(ultimate.flags & nodes.Ability.RUNNING)
        self.assertEqual(charge.value(), 0)

    def test_charge_pins_roadhog_interrupts_whole_hog_and_slams_for_300(self):
        run, hog, rein, _ = self.scene(wall=11)
        fight = run.match.combat
        health = fixtures.RoadhogDefenseTests().health
        before = health(run, hog)
        ult, = hog.body_script.ultimates
        hog.body_script.set_ult_charge(hog.body_script.component.entity_var(338).value())
        run.step({rein: (0, 0, 8), hog: (16384, 0, 32)})
        self.assertTrue(ult.flags & 1)
        for _ in range(100):
            run.step({hog: (16384, 0, 0)})
            if reinhardt_charge.pinned(fight, hog.body):
                break
        self.assertTrue(reinhardt_charge.pinned(fight, hog.body))
        self.assertTrue(fight.is_stunned(hog))
        self.assertFalse(ult.flags & 1)
        self.assertEqual(health(run, hog), before)
        pinned_z = hog.mover.position[2]
        for _ in range(60):
            run.step({hog: (16384, 0, 32)})
            if not reinhardt_charge.pinned(fight, hog.body):
                break
        self.assertFalse(fight.is_stunned(hog))
        self.assertGreater(hog.mover.position[2], pinned_z)
        self.assertEqual(health(run, hog), before - 300)
        self.assertFalse(ult.flags & 1)
        self.assertFalse(fight.charges)
        for _ in range(25):
            run.step({})
        self.assertEqual(health(run, hog), before - 300)

    def test_charge_discards_buffered_quick_melee_before_wall_release(self):
        run, hog, rein, victim = self.scene(wall=11)
        health = fixtures.RoadhogDefenseTests().health
        match = run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        hog.team, rein.team, victim.team = 0, 1, 1
        x, y, z = hog.mover.position
        victim.mover.position[:] = (x + 1.5, y, z)
        victim.mover.body = None
        run.step({})
        look = (16384, 0)
        run.step({hog: (*look, 0x800), rein: (0, 0, 8)})
        first = run.frame
        run.step({})
        run.step({hog: (*look, 0x800)})
        melee = next(s for i in hog.body_script.component.instances.values() if i.graph.index == 0x0043
                     for s in i.states.values() if isinstance(s, nodes.Ability))
        weapon = next(i for i in hog.body_script.component.instances.values() if i.graph.index == 0x01C6)
        ammo = weapon.vars[53].value()
        hit = melee.instance.state(4)
        self.assertTrue(melee.presses)
        self.assertEqual(hit.counter, 1)
        self.assertEqual(health(run, victim), 19970)
        while run.frame < first + 61 and not reinhardt_charge.pinned(fight, hog.body):
            run.step({hog: (*look, 0x800)})
        self.assertTrue(reinhardt_charge.pinned(fight, hog.body))
        self.assertFalse(melee.presses)
        self.assertEqual(health(run, hog), 600)
        for _ in range(6):
            run.step({hog: (*look, 0x800)})
            self.assertTrue(reinhardt_charge.pinned(fight, hog.body))
        self.assertEqual(hit.counter, 1)
        while run.frame < first + 125 and reinhardt_charge.pinned(fight, hog.body):
            run.step({})
        self.assertFalse(reinhardt_charge.pinned(fight, hog.body))
        self.assertEqual(health(run, hog), 300)

        x, y, z = hog.mover.position
        victim.mover.position[:] = (x + 1.5, y, z)
        victim.mover.body = None
        while run.frame <= first + 125:
            run.step({})
        self.assertEqual(hit.counter, 1)
        self.assertEqual(health(run, victim), 19970)
        run.step({hog: (*look, 0x800)})
        self.assertEqual(hit.counter, 2)
        self.assertEqual(health(run, victim), 19940)
        self.assertEqual(weapon.vars[53].value(), ammo)

    def test_open_charge_releases_roadhog_on_source_interruption(self):
        run, hog, rein, caster = self.scene()
        fight = run.match.combat
        self.assertTrue(run.match.switch_hero(caster, fixtures.heroes()[0x02E0000000000042]))
        caster.mover.position[:] = (2.5, 1, 6)
        caster.mover.body = None
        health = fixtures.RoadhogDefenseTests().health
        before = health(run, hog)
        run.step({rein: (0, 0, 8)})
        for _ in range(100):
            run.step({})
            if reinhardt_charge.pinned(fight, hog.body):
                break
        self.assertTrue(reinhardt_charge.pinned(fight, hog.body))
        x, y, z = rein.mover.position
        caster.mover.position[:] = (x + 1.5, y, z + 2.0)
        caster.mover.body = None
        for offset in range(40):
            x, y, z = rein.mover.position
            angles = fixtures.aim(fight._eye(caster), (x, y + 1.25, z + 0.5))
            run.step({caster: (*angles, 16 if offset == 0 else 0)})
            if fight.is_stunned(rein):
                break
        self.assertTrue(fight.is_stunned(rein))
        self.assertFalse(reinhardt_charge.pinned(fight, hog.body))
        self.assertEqual(health(run, hog), before - 25)  # Flashbang splash, no Charge impact.
        self.assertFalse(fight.charges)

    def test_whole_hog_damage_does_not_displace_the_charge_victim_until_release(self):
        run, hog, rein, shooter = self.scene()
        match = run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        hog.team, rein.team, shooter.team = 0, 1, 1
        self.assertTrue(match.switch_hero(shooter, hog.hero))
        shooter.mover.position[:] = (4, 1, 8)
        shooter.mover.body = None
        fight.vitals[hog.body] = combat.Vitals(hog.body, 20000)
        for _ in range(35):
            run.step({})
        shooter.body_script.set_ult_charge(2240)
        away = (16384, 0)
        run.step({shooter: (*away, 32), rein: (0, 0, 8)})
        for _ in range(90):
            run.step({shooter: (*away, 0)})
            if reinhardt_charge.pinned(fight, hog.body):
                break
        self.assertTrue(reinhardt_charge.pinned(fight, hog.body))
        pinned_x, _, pinned_z = hog.mover.position
        initial_damage = shooter.stats.value(stats.HERO_DAMAGE)
        pinned_damage = initial_damage
        for _ in range(210):
            damage = shooter.stats.value(stats.HERO_DAMAGE)
            x, y, z = hog.mover.position
            look = fixtures.aim(fight._eye(shooter), (x, y + 1, z))
            run.step({shooter: (*look, 0)})
            if reinhardt_charge.pinned(fight, hog.body):
                self.assertNotIn(hog.body, fight.impulses.live)
                self.assertAlmostEqual(hog.mover.position[0], pinned_x, places=3)
                pinned_damage = shooter.stats.value(stats.HERO_DAMAGE)
            elif shooter.stats.value(stats.HERO_DAMAGE) > damage:
                self.assertIn(hog.body, fight.impulses.live)
                break
        self.assertFalse(reinhardt_charge.pinned(fight, hog.body))
        self.assertIn(hog.body, fight.impulses.live)
        self.assertGreater(pinned_damage, initial_damage)
        self.assertGreater(hog.mover.position[2], pinned_z)

    def test_charge_preserves_team_cover_and_range_gates(self):
        health = fixtures.RoadhogDefenseTests().health
        for condition, options in (("team", {}), ("cover", {"wall": 4}), ("range", {"distance": 65})):
            with self.subTest(condition=condition):
                run, hog, rein, _ = self.scene(**options)
                fight = run.match.combat
                if condition == "team":
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    hog.team = rein.team
                before = health(run, hog)
                run.step({rein: (0, 0, 8)})
                for _ in range(250):
                    run.step({})
                    self.assertFalse(reinhardt_charge.pinned(fight, hog.body))
                self.assertEqual(health(run, hog), before)
                self.assertFalse(fight.charges)

    def test_charge_glancing_contact_deals_50_without_pinning(self):
        run, hog, rein, _ = self.scene(wall=11, target_x=1.5)
        fight = run.match.combat
        health = fixtures.RoadhogDefenseTests().health
        before = health(run, hog)
        run.step({rein: (0, 0, 8)})
        for _ in range(110):
            run.step({})
            self.assertFalse(reinhardt_charge.pinned(fight, hog.body))
        self.assertEqual(health(run, hog), before - 50)
        self.assertFalse(fight.is_stunned(hog))

    def test_charge_glance_preserves_buffered_quick_melee(self):
        run, hog, rein, victim = self.scene(wall=11, target_x=1.5)
        fight = run.match.combat
        health = fixtures.RoadhogDefenseTests().health
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        hog.team, rein.team, victim.team = 0, 1, 1
        x, y, z = hog.mover.position
        victim.mover.position[:] = (x + 1.5, y, z)
        victim.mover.body = None
        run.step({})

        held = {hog: (16384, 0, 0x800)}
        weapon = next(i for i in hog.body_script.component.instances.values() if i.graph.index == 0x01C6)
        ammo = weapon.vars[53].value()
        run.step({**held, rein: (0, 0, 8)})
        first = run.frame
        run.step({})
        run.step(held)
        self.assertEqual(health(run, victim), 19970)
        while run.frame < first + 54 and health(run, hog) == 600:
            run.step(held)
            self.assertFalse(reinhardt_charge.pinned(fight, hog.body))
        self.assertEqual(health(run, hog), 550)
        self.assertGreater(hog.mover.velocity[1], 0)
        self.assertFalse(fight.is_stunned(hog))
        while run.frame < first + 59:
            run.step(held)
        moved_x, moved_y, moved_z = hog.mover.position
        self.assertGreater(moved_x, x)
        victim.mover.position[:] = (moved_x + 1.5, moved_y, moved_z)
        victim.mover.body = None
        target = (moved_x + 1.5, moved_y + 1.2, moved_z)
        while run.frame < first + 61:
            run.step({hog: (*fixtures.aim(fight._eye(hog), target), 0x800)})
        self.assertEqual(health(run, victim), 19970)

        run.step({hog: (*fixtures.aim(fight._eye(hog), target), 0x800)})
        self.assertEqual(run.frame, first + 62)
        self.assertEqual(health(run, victim), 19940)
        for _ in range(65):
            run.step(held)
        self.assertEqual(health(run, hog), 550)
        self.assertEqual(health(run, victim), 19940)
        self.assertEqual(weapon.vars[53].value(), ammo)

    def test_charge_expiry_releases_roadhog_without_wall_damage(self):
        run, hog, rein, _ = self.scene()
        fight = run.match.combat
        health = fixtures.RoadhogDefenseTests().health
        before = health(run, hog)
        run.step({rein: (0, 0, 8)})
        captured = False
        for _ in range(260):
            run.step({})
            captured |= reinhardt_charge.pinned(fight, hog.body)
            if captured and not fight.charges:
                break
        self.assertTrue(captured)
        self.assertFalse(fight.charges)
        self.assertFalse(fight.is_stunned(hog))
        self.assertEqual(health(run, hog), before)

    def test_charge_interrupts_breather_hook_flight_and_active_pull(self):
        for graph, button, cast, target_x, caught in (
            (0x0240, 16, 6, 18, False),
            (0x01C8, 8, 34, 18, False),
            (0x01C8, 8, 18, 14, True),
        ):
            with self.subTest(graph=graph, caught=caught):
                run, hog, rein, third = self.scene(wall=11)
                fight = run.match.combat
                third.mover.position[:] = (target_x, 1, 6)
                third.mover.body = None
                pool = fight.vitals_of(next(t for t in fight._targets() if t.owner is hog))
                pool.damage(200)
                fight._sync_body_health(hog, pool)
                state = next(s for i in hog.body_script.component.instances.values() if i.graph.index == graph
                             for s in i.states.values() if isinstance(s, nodes.Ability))
                for offset in range(100):
                    if graph == 0x0240:
                        active = fight.incoming_damage_scale(hog) == 0.5
                    else:
                        active = hog.body in fight.hooks.flights
                    tethered = fight.hooks.controlled(third.body)
                    run.step({rein: (0, 0, 8 if offset == 0 else 0),
                              hog: (16384, 0, button if offset == cast else 0)})
                    if reinhardt_charge.pinned(fight, hog.body):
                        break
                self.assertTrue(reinhardt_charge.pinned(fight, hog.body))
                self.assertTrue(active)
                self.assertEqual(tethered, caught)
                self.assertFalse(state.flags & nodes.Ability.RUNNING)
                self.assertGreater(state.remaining(), 0)
                self.assertFalse(fight.hooks.controlled(third.body))
                self.assertNotIn(hog.body, fight.hooks.flights)
                self.assertEqual(fight.incoming_damage_scale(hog), 1)

    def test_roadhog_hook_prevents_charge_windup_and_stops_live_charge(self):
        for hook_at, saw_live in ((4, False), (34, True)):
            with self.subTest(hook_at=hook_at):
                run, hog, rein, _ = self.scene(distance=8)
                fight = run.match.combat
                live = caught = False
                for offset in range(110):
                    run.step({rein: (0, 0, 8 if offset == 0 else 0),
                              hog: (-32768, 0, 8 if offset == hook_at else 0)})
                    live |= rein.body in fight.charges
                    caught |= fight.hooks.controlled(rein.body)
                    self.assertFalse(reinhardt_charge.pinned(fight, hog.body))
                self.assertTrue(caught)
                self.assertEqual(live, saw_live)
                self.assertFalse(fight.charges)
                instance = next(i for i in rein.body_script.component.instances.values()
                                if i.graph.index == 0x007D)
                self.assertFalse(instance.state(3).active)
