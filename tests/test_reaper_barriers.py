"""Actual Reaper commands against authored Reinhardt0095 and external curved mesh."""

import math
import unittest
from dataclasses import replace
from unittest.mock import patch

import test_game_reaper as reaper_tests
import test_reaper_control as control_tests
from test_game_combat import aim

from ow174.game import barriers, combat, lucio_barrier, pools, reaper, sigma_grasp, stats, zarya_barriers
from ow174.game.commands import Command
from ow174.game.content import SOLDIER, find_hero, heroes
from ow174.game.script import expr
from ow174.game.script.driver import ULT_CHARGE, ULT_COST
from ow174.game.script.graph import LUCIO_STATUS_SHA256


class ReaperBarrierTests(unittest.TestCase):
    def test_actual_sleep_dart_resolves_reinhardt_shield_release_across_world_gap(self):
        from ow174.game import ana

        def scene():
            run, owner, rein, _ = reaper_tests.ReaperTests().scenario(names=("Reaper", "Reinhardt", "Ana"))
            caster = run.players[2]
            self.assertTrue(run.match.switch_hero(rein, heroes()[barriers.REINHARDT]))
            self.assertTrue(run.match.switch_hero(caster, heroes()[ana.HERO]))
            run.match.game_map = replace(run.match.game_map, free_for_all=False)
            owner.team = rein.team = 0
            caster.team = 1
            x, y, z = owner.mover.position
            rein.mover.position[:] = (x + 1.4, y, z + 0.35)
            caster.mover.position[:] = (x, y, z - 8)
            rein.mover.body = caster.mover.body = None
            guard = {rein: (-32768, 0, 2)}
            for _ in range(30):
                run.step(guard)
            fight = run.match.combat
            shield = barriers.active(rein)
            self.assertIsNotNone(shield)
            heading = aim(fight._eye(caster), (x, y + 1, z))
            run.step(guard | {caster: (*heading, 8)})
            for _ in range(30):
                if fight.rockets:
                    break
                run.step(guard)
            dart, = fight.rockets
            self.assertEqual(dart.direct.effect, ana.SLEEP_EFFECT)
            return run, owner, rein, caster, shield, guard, dart

        run, owner, _, _, shield, guard, _ = scene()
        fight = run.match.combat
        launch = run.frame
        for _ in range(25):
            run.step(guard)
            if not fight.rockets:
                break
        self.assertFalse(fight.rockets)
        self.assertEqual(shield.vars[586].value(), 1595)
        self.assertEqual(fight.vitals[owner.body].total, 250)
        travel = run.frame - launch
        self.assertGreater(travel, 2)

        for blocked in (True, False):
            outcomes = []
            for skipped in (False, True):
                with self.subTest(blocked=blocked, skipped=skipped):
                    run, owner, rein, caster, shield, guard, dart = scene()
                    fight = run.match.combat
                    surface_tick = run.frame + travel
                    last_held = surface_tick - (1 if blocked else 2)
                    while run.frame < last_held:
                        run.step(guard)
                    self.assertEqual(dart.last_tick, run.frame)
                    self.assertIs(barriers.active(rein), shield)
                    frame = rein.commands.last_frame
                    impacts = []

                    def observe(missile, *, world=fight, guarder=rein, hits=impacts, landed=fight._landed):
                        hits.append((world.tick, barriers.active(guarder) is not None))
                        landed(missile)

                    end = surface_tick + 30
                    with patch.object(fight, "_landed", side_effect=observe):
                        while run.frame < end:
                            run.frame = end if skipped else run.frame + 1
                            run.match.update(run.frame * combat.TICK, run.frame, frames=False)
                    self.assertEqual(rein.commands.last_frame, frame)
                    self.assertIsNone(barriers.active(rein))
                    self.assertFalse(fight.rockets)
                    self.assertEqual(len(impacts), 1)
                    tick, shield_active = impacts[0]
                    self.assertEqual(shield_active, blocked)
                    self.assertGreaterEqual(tick, surface_tick)
                    self.assertEqual(shield.vars[586].value(), 1595 if blocked else 1600)
                    self.assertEqual(fight.vitals[owner.body].total, 250 if blocked else 245)
                    self.assertEqual(caster.stats.value(stats.HERO_DAMAGE), 0 if blocked else 5)
                    sleep = fight.ana_sleeps.get(owner.body)
                    if blocked:
                        self.assertEqual(tick, surface_tick)
                        self.assertIsNone(sleep)
                    else:
                        self.assertIsNotNone(sleep)
                        self.assertEqual(sleep["start"], tick)
                        self.assertEqual(sleep["end"] - tick, 313)
                    outcomes.append((impacts, sleep))
            if len(outcomes) == 2:
                self.assertEqual(outcomes[0], outcomes[1])

    def test_actual_dragonblade_hits_reaper_through_held_reinhardt_shield(self):
        run, owner, rein, _ = reaper_tests.ReaperTests().scenario(names=("Reaper", "Reinhardt", "Genji"))
        genji = run.players[2]
        self.assertTrue(run.match.switch_hero(rein, heroes()[barriers.REINHARDT]))
        self.assertTrue(run.match.switch_hero(genji, heroes()[0x02E0000000000029]))
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        owner.team = rein.team = 0
        genji.team = 1
        fight = run.match.combat
        x, y, z = owner.mover.position
        rein.mover.position[:] = (x + 1.4, y, z + 0.35)
        genji.mover.position[:] = (x, y, z - 2)
        rein.mover.body = genji.mover.body = None
        guard = {rein: (-32768, 0, 2)}
        for _ in range(30):
            run.step(guard)
        shield = barriers.active(rein)
        self.assertIsNotNone(shield)
        heading = aim(fight._eye(genji), (x, y + 1, z))
        contact = barriers.intercept(fight, genji, fight._eye(genji), combat.aim(*heading), 1.7)
        self.assertIsNotNone(contact)
        self.assertIs(contact[1], shield)
        genji.body_script.set_ult_charge(1932)
        run.step(guard | {genji: (*heading, 32)})
        for _ in range(84):
            run.step(guard | {genji: (*heading, 0)})
        run.step(guard | {genji: (*heading, 1)})
        for _ in range(24):
            run.step(guard | {genji: (*heading, 0)})
        self.assertIs(barriers.active(rein), shield)
        self.assertEqual(shield.vars[586].value(), 1600)
        self.assertEqual(fight.vitals[owner.body].total, 140)
        rein_target = next(t for t in fight._targets() if t.owner is rein)
        self.assertEqual(fight.vitals_of(rein_target).total, 395)
        self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), 215)

    def test_actual_genji_fan_resolves_held_reinhardt_shield_then_reaper(self):
        for breaks in (False, True):
            with self.subTest(breaks=breaks):
                run, owner, rein, _ = reaper_tests.ReaperTests().scenario(
                    names=("Reaper", "Reinhardt", "Genji")
                )
                genji = run.players[2]
                self.assertTrue(run.match.switch_hero(rein, heroes()[barriers.REINHARDT]))
                self.assertTrue(run.match.switch_hero(genji, heroes()[0x02E0000000000029]))
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                owner.team = rein.team = 0
                genji.team = 1
                fight = run.match.combat
                x, y, z = owner.mover.position
                rein.mover.position[:] = (x + 1.4, y, z + 0.35)
                genji.mover.position[:] = (x, y, z - 1.5)
                rein.mover.body = genji.mover.body = None
                guard = {rein: (-32768, 0, 2)}
                for _ in range(30):
                    run.step(guard)
                shield = barriers.active(rein)
                self.assertIsNotNone(shield)
                self.assertEqual(shield.vars[586].value(), 1600)
                if breaks:
                    shield.write((expr.INSTANCE, 586), 40.0)
                    shield.component.run_now()
                heading = aim(fight._eye(genji), (x, y + 0.8, z))
                run.step(guard | {genji: (*heading, 2)})
                self.assertEqual(len(fight.rockets), 3)
                for _ in range(18):
                    run.step(guard)
                self.assertFalse(fight.rockets)
                self.assertEqual(shield.vars[586].value(), 0 if breaks else 1513)
                self.assertEqual(fight.vitals[owner.body].total, 221 if breaks else 250)
                self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), 29 if breaks else 0)

    def scene(self, *, team_mode=False):
        run, owner, defender, _ = reaper_tests.ReaperTests().scenario(team_mode=team_mode)
        position = tuple(defender.mover.position)
        self.assertTrue(run.match.switch_hero(defender, heroes()[barriers.REINHARDT]))
        defender.mover.position[:] = position
        defender.mover.body = None
        for _ in range(30):
            run.step({defender: (-32768, 0, 2)})
        instance = barriers.active(defender)
        self.assertIsNotNone(instance)
        self.assertEqual(instance.vars[586].value(), 1600)
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is defender)
        fight.vitals_of(target)
        owner.body_script.ult_seconds = 0
        owner.body_script.set_ult_charge(0)
        return run, owner, defender, target, instance

    def fire(self, run, owner, defender):
        x, y, z = defender.mover.position
        yaw, pitch = aim(run.match.combat._eye(owner), (x, y + 1, z))
        # Run supplies zero input for omitted players; keep the shield held on
        # the same actual input frame as the incoming shell.
        run.step({owner: (yaw, pitch, 1), defender: (-32768, 0, 2)})

    def test_actual_shell_hits_curved_shield_without_body_damage_reaping_or_charge(self):
        run, owner, defender, target, instance = self.scene()
        fight = run.match.combat
        fight.vitals[owner.body].damage(100)
        self.fire(run, owner, defender)
        self.assertEqual(fight.vitals_of(target).total, 500)
        self.assertEqual(instance.vars[586].value(), 1480)
        self.assertEqual(fight.vitals[owner.body].total, 150)
        self.assertEqual(owner.body_script.component.vars[342].value(), 0)
        self.assertEqual(owner.client.messages(combat.HIT), [])

    def test_actual_blossom_damages_held_shield_and_protects_body(self):
        run, owner, defender, target, instance = self.scene()
        fight = run.match.combat
        fight.vitals[owner.body].damage(100)
        owner.body_script.set_ult_charge(2100)
        run.step({owner: (0, 0, 32), defender: (-32768, 0, 2)})
        for _ in range(200):
            run.step({defender: (-32768, 0, 2)})
        self.assertTrue(fight.reaper_blossoms[owner.body]["ended"])
        self.assertEqual(instance.vars[586].value(), 1090)
        self.assertEqual(fight.vitals_of(target).total, 500)
        self.assertEqual(fight.vitals[owner.body].total, 150)
        self.assertEqual(owner.body_script.component.vars[342].value(), 0)

    def test_actual_blossom_break_pulse_waits_for_next_body_pulse(self):
        run, owner, defender, target, instance = self.scene()
        fight = run.match.combat
        instance.write((expr.INSTANCE, 586), 34.0)
        instance.component.run_now()
        owner.body_script.set_ult_charge(2100)
        run.step({owner: (0, 0, 32), defender: (-32768, 0, 2)})
        self.assertIsNone(barriers.active(defender))
        self.assertEqual(instance.vars[586].value(), 0)
        for _ in range(12):
            run.step({defender: (-32768, 0, 2)})
            self.assertEqual(fight.vitals_of(target).total, 500)
        run.step({defender: (-32768, 0, 2)})
        self.assertEqual(fight.vitals_of(target).total, 471)

    def test_actual_blossom_acquires_shield_with_body_outside_range(self):
        run, owner, defender, target, instance = self.scene()
        fight = run.match.combat
        x, y, z = owner.mover.position
        defender.mover.position[:] = (x, y, z + 8.85)
        defender.mover.body = None
        owner.body_script.set_ult_charge(2100)
        run.step({owner: (0, 0, 32), defender: (-32768, 0, 2)})
        self.assertEqual(instance.vars[586].value(), 1566)
        self.assertEqual(fight.vitals_of(target).total, 500)

    def test_actual_blossom_shield_los_team_and_range(self):
        from ow174.game.collision import World

        for case in ("wall", "friendly", "range"):
            with self.subTest(case=case):
                run, owner, defender, target, instance = self.scene(team_mode=True)
                fight = run.match.combat
                x, y, z = owner.mover.position
                if case == "wall":
                    z += 0.2
                    run.match.collision_world = World([
                        (x-10, y-10, z, x+10, y-10, z, x+10, y+10, z),
                        (x-10, y-10, z, x+10, y+10, z, x-10, y+10, z),
                    ], name="wall before Death Blossom target")
                elif case == "friendly":
                    defender.team = owner.team
                else:
                    defender.mover.position[:] = (x, y, z + 11)
                    defender.mover.body = None
                owner.body_script.set_ult_charge(2100)
                run.step({owner: (0, 0, 32), defender: (-32768, 0, 2)})
                self.assertIn(owner.body, fight.reaper_blossoms)
                self.assertEqual(instance.vars[586].value(), 1600)
                self.assertEqual(fight.vitals_of(target).total, 500)

    def test_actual_helix_hits_shield_or_earlier_wall_and_never_splashes_through(self):
        from ow174.game.collision import World

        for case in ("shield", "wall", "break"):
            with self.subTest(case=case):
                run, shooter, defender, target, instance = self.scene()
                position = tuple(shooter.mover.position)
                self.assertTrue(run.match.switch_hero(shooter, heroes()[SOLDIER]))
                shooter.mover.position[:] = position
                shooter.mover.body = None
                x, y, z = position
                defender.mover.position[:] = (x, y, z + 5)
                defender.mover.body = None
                if case == "wall":
                    wall_z = z + 1.5
                    run.match.collision_world = World([
                        (x-10, y-10, wall_z, x+10, y-10, wall_z, x+10, y+10, wall_z),
                        (x-10, y-10, wall_z, x+10, y+10, wall_z, x-10, y+10, wall_z),
                    ], name="wall before projectile and shield")
                elif case == "break":
                    instance.write((expr.INSTANCE, 586), 6.0)
                    instance.component.run_now()
                fight = run.match.combat
                yaw, pitch = aim(fight._eye(shooter), (x, y + 1, z + 5))
                run.step({shooter: (yaw, pitch, 2), defender: (-32768, 0, 2)})
                self.assertTrue(fight.rockets)
                for _ in range(20):
                    run.step({defender: (-32768, 0, 2)})
                self.assertFalse(fight.rockets)
                self.assertEqual(instance.vars[586].value(), {"shield": 1480, "wall": 1600, "break": 0}[case])
                self.assertEqual(fight.vitals_of(target).total, 500)

    def test_actual_melee_reaches_body_through_shield_but_obeys_wall_team_and_range(self):
        from ow174.game.collision import World

        for case in ("shield", "wall", "friendly", "range"):
            with self.subTest(case=case):
                run, owner, defender, target, instance = self.scene(team_mode=True)
                fight = run.match.combat
                owner.team = 0
                defender.team = 0 if case == "friendly" else 1
                x, y, z = owner.mover.position
                if case == "wall":
                    wall_z = z + 0.25
                    run.match.collision_world = World([
                        (x-10, y-10, wall_z, x+10, y-10, wall_z, x+10, y+10, wall_z),
                        (x-10, y-10, wall_z, x+10, y+10, wall_z, x-10, y+10, wall_z),
                    ], name="wall before quick melee target")
                elif case == "range":
                    defender.mover.position[:] = (x, y, z + 5)
                    defender.mover.body = None
                vx, vy, vz = defender.mover.position
                yaw, pitch = aim(fight._eye(owner), (vx, vy + 1, vz))
                fight.vitals[owner.body].damage(100)
                run.step({owner: (yaw, pitch, 0x800), defender: (-32768, 0, 2)})
                for _ in range(30):
                    run.step({defender: (-32768, 0, 2)})
                self.assertIsNotNone(barriers.active(defender))
                self.assertEqual(instance.vars[586].value(), 1600)
                self.assertEqual(fight.vitals_of(target).total, 475 if case == "shield" else 500)
                self.assertAlmostEqual(fight.vitals[owner.body].total, 158.75 if case == "shield" else 150,
                                       places=4)

    def test_authored_release_delay_and_regeneration_retain_damaged_health(self):
        run, owner, defender, _, instance = self.scene()
        self.fire(run, owner, defender)
        for _ in range(100):
            run.step({defender: (-32768, 0, 0)})
        self.assertIsNone(barriers.active(defender))
        self.assertEqual(instance.vars[586].value(), 1480)
        for _ in range(100):
            run.step({defender: (-32768, 0, 0)})
        self.assertEqual(instance.vars[586].value(), 1600)
        for _ in range(30):
            run.step({defender: (-32768, 0, 2)})
        self.assertIsNotNone(barriers.active(defender))

    def test_actual_first_pellet_breaks_shield_and_remaining_pellets_hit_body(self):
        run, owner, defender, target, instance = self.scene()
        instance.write((expr.INSTANCE, 586), 6.0)
        instance.component.run_now()
        self.fire(run, owner, defender)
        self.assertIsNone(barriers.active(defender))
        self.assertEqual(instance.vars[586].value(), 0)
        self.assertGreater(instance.vars[61].value(), 4.9)
        self.assertEqual(run.match.combat.vitals_of(target).total, 443)
        for _ in range(100):
            run.step({defender: (-32768, 0, 2)})
        self.assertIsNone(barriers.active(defender))
        for _ in range(250):
            run.step({defender: (-32768, 0, 0)})
        self.assertEqual(instance.vars[61].value(), 0)
        for _ in range(30):
            run.step({defender: (-32768, 0, 2)})
        self.assertIsNotNone(barriers.active(defender))

    def test_held_shield_never_regenerates_damaged_health(self):
        run, owner, defender, _, instance = self.scene()
        self.fire(run, owner, defender)
        for _ in range(200):
            run.step({defender: (-32768, 0, 2)})
        self.assertIsNotNone(barriers.active(defender))
        self.assertEqual(instance.vars[586].value(), 1480)

    def test_shield_intercepts_actual_shell_even_when_no_body_lies_on_the_rays(self):
        run, owner, defender, target, instance = self.scene()
        x, y, z = defender.mover.position
        yaw, pitch = aim(run.match.combat._eye(owner), (x+2, y+1, z))
        run.step({owner: (yaw, pitch, 1), defender: (-32768, 0, 2)})
        self.assertEqual(run.match.combat.vitals_of(target).total, 500)
        self.assertLess(instance.vars[586].value(), 1600)

    def test_friendly_shield_and_missed_shell_do_not_lose_health(self):
        for friendly in (True, False):
            with self.subTest(friendly=friendly):
                run, owner, defender, target, instance = self.scene(team_mode=True)
                if friendly:
                    defender.team = owner.team
                    self.fire(run, owner, defender)
                else:
                    run.step({owner: (-32768, 0, 1), defender: (-32768, 0, 2)})
                self.assertEqual(run.match.combat.vitals_of(target).total, 500)
                self.assertEqual(instance.vars[586].value(), 1600)

    def test_turning_active_shield_away_exposes_actual_body(self):
        run, owner, defender, target, instance = self.scene()
        for _ in range(2):
            run.step({defender: (0, 0, 2)})
        x, y, z = defender.mover.position
        yaw, pitch = aim(run.match.combat._eye(owner), (x, y+1, z))
        run.step({owner: (yaw, pitch, 1), defender: (0, 0, 2)})
        self.assertEqual(instance.vars[586].value(), 1600)
        self.assertEqual(run.match.combat.vitals_of(target).total, 440)

    def test_actual_static_wall_before_shield_prevents_shield_and_body_damage(self):
        from ow174.game.collision import World

        run, owner, defender, target, instance = self.scene()
        x, y, z = owner.mover.position
        z += 0.2
        run.match.collision_world = World([
            (x-10, y-10, z, x+10, y-10, z, x+10, y+10, z),
            (x-10, y-10, z, x+10, y+10, z, x-10, y+10, z),
        ], name="wall before authored shield")
        self.fire(run, owner, defender)
        self.assertEqual(instance.vars[586].value(), 1600)
        self.assertEqual(run.match.combat.vitals_of(target).total, 500)


class ReaperZaryaBarrierTests(unittest.TestCase):
    def test_actual_overlapping_zarya_bubbles_retire_on_separate_idle_deadlines(self):
        run, owner, caster, _ = reaper_tests.ReaperTests().scenario(team_mode=True, names=("Reaper", "Zarya"))
        self.assertTrue(run.match.switch_hero(caster, heroes()[zarya_barriers.HERO]))
        owner.team = caster.team = 0
        x, y, z = owner.mover.position
        caster.mover.position[:] = (x, y, z + 3)
        caster.mover.body = None
        fight = run.match.combat
        facing = aim(fight._eye(caster), (x, y + 1, z))
        run.step({caster: (*facing, 8)})
        start = run.frame
        personal = fight.zarya_bubbles.for_target(caster)
        self.assertIsNotNone(personal)
        for _ in range(34):
            run.step({})
        run.step({caster: (*facing, 16)})
        projected = fight.zarya_bubbles.for_target(owner)
        self.assertIsNotNone(projected)
        script = caster.body_script
        self.assertIsNotNone(script.world_frame_anchor)
        run.frame = start + 135
        run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        self.assertIsNone(fight.zarya_bubbles.for_target(caster))
        self.assertIs(fight.zarya_bubbles.for_target(owner), projected)
        self.assertIsNotNone(script.world_frame_anchor)
        run.frame = start + 170
        run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        self.assertIsNone(fight.zarya_bubbles.for_target(owner))
        self.assertIsNone(script.world_frame_anchor)
        self.assertEqual((personal.health, projected.health), (200, 200))

    def test_actual_blossom_breaks_bubble_then_hits_on_later_pulses(self):
        run, owner, caster, _ = reaper_tests.ReaperTests().scenario()
        position = tuple(caster.mover.position)
        self.assertTrue(run.match.switch_hero(caster, heroes()[zarya_barriers.HERO]))
        caster.mover.position[:] = position
        caster.mover.body = None
        for _ in range(30):
            run.step({})
        run.step({caster: (-32768, 0, 8)})
        fight = run.match.combat
        bubble = fight.zarya_bubbles.for_target(caster)
        self.assertIsNotNone(bubble)
        target = next(t for t in fight._targets() if t.owner is caster)
        initial = fight.vitals_of(target).total
        owner.body_script.set_ult_charge(2100)
        run.step({owner: (0, 0, 32)})
        self.assertEqual(bubble.health, 166)
        for _ in range(80):
            if bubble.health == 0:
                break
            run.step({})
        self.assertEqual(bubble.health, 0)
        self.assertEqual(fight.vitals_of(target).total, initial)
        for _ in range(160):
            run.step({})
        self.assertEqual(fight.vitals_of(target).total, initial - 306)

    def staggered_bubble_scene(self):
        run, owner, caster, _ = reaper_tests.ReaperTests().scenario(
            team_mode=True, names=("Reaper", "Zarya", "Soldier"),
        )
        recipient = run.players[2]
        self.assertTrue(run.match.switch_hero(caster, heroes()[zarya_barriers.HERO]))
        owner.team, caster.team, recipient.team = 0, 1, 1
        x, y, z = owner.mover.position
        caster.mover.position[:] = (x - 2, y, z + 3)
        recipient.mover.position[:] = (x + 2, y, z + 3)
        caster.mover.body = recipient.mover.body = None
        fight = run.match.combat
        facing = aim(fight._eye(caster), (x + 2, y + 1, z + 3))
        for _ in range(28):
            run.step({})
        run.step({caster: (*facing, 8)})
        personal = fight.zarya_bubbles.for_target(caster)
        for _ in range(24):
            run.step({})
        run.step({caster: (*facing, 16)})
        projected = fight.zarya_bubbles.for_target(recipient)
        self.assertIsNotNone(personal)
        self.assertIsNotNone(projected)
        self.assertEqual((personal.health, projected.health), (200, 200))
        return run, owner, (caster, recipient), (personal, projected)

    def test_actual_blossom_resolves_staggered_zarya_barrier_expiries(self):
        def advance(run, tick):
            run.frame = tick
            run.match.update(tick * combat.TICK, tick, frames=False)

        run, _, actors, _ = self.staggered_bubble_scene()
        expiries = {}
        fight = run.match.combat
        for _ in range(150):
            advance(run, run.frame + 1)
            for actor in actors:
                if actor not in expiries and fight.zarya_bubbles.for_target(actor) is None:
                    expiries[actor] = run.frame
            if len(expiries) == 2:
                break
        self.assertEqual(len(expiries), 2)
        personal_end, projected_end = (expiries[actor] for actor in actors)
        self.assertEqual(projected_end - personal_end, 25)
        outcomes = {}
        for lethal, skipped in ((False, False), (False, True), (True, False), (True, True)):
            with self.subTest(lethal=lethal, skipped=skipped):
                run, owner, actors, bubbles = self.staggered_bubble_scene()
                fight = run.match.combat
                # Put the fourth pulse on personal expiry and the sixth on projected expiry.
                while run.frame < personal_end - 39:
                    run.step({})
                own = fight.vitals[owner.body]
                own.damage(170)
                caster_pool = fight.vitals[actors[0].body]
                if lethal:
                    caster_pool.damage(caster_pool.total - 34)
                initial = [fight.vitals[actor.body].total for actor in actors]
                script = owner.body_script
                script.set_ult_charge(script.component.entity_var(ULT_COST).value())
                run.step({owner: (0, 0, 32)})
                self.assertEqual([bubble.health for bubble in bubbles], [166, 166])
                last_input = tuple(actor.commands.last_frame for actor in actors)
                end = projected_end + 24
                while run.frame < end:
                    advance(run, end if skipped else run.frame + 1)
                self.assertEqual(tuple(actor.commands.last_frame for actor in actors), last_input)
                self.assertTrue(all(fight.zarya_bubbles.for_target(actor) is None for actor in actors))
                self.assertEqual([bubble.health for bubble in bubbles], [98, 30])
                self.assertEqual(caster_pool.total, 0 if lethal else initial[0] - 4 * 34)
                self.assertEqual(fight.vitals[actors[1].body].total, initial[1] - 2 * 34)
                self.assertAlmostEqual(own.total, 80 + (3 if lethal else 6) * 34 * reaper.REAPING_RATIO)
                if lethal:
                    self.assertEqual((caster_pool.death_tick, caster_pool.killer), (personal_end, owner.body))
                hits = owner.client.messages(combat.HIT)
                hit_frames = [
                    [hit["+0x84"] for hit in hits if hit["+0x78"]["+0x0"]["+0x0"] == actor.body]
                    for actor in actors
                ]
                caster_frames = [personal_end]
                if not lethal:
                    caster_frames.extend((personal_end + 12, projected_end, projected_end + 12))
                self.assertEqual(hit_frames, [caster_frames, [projected_end, projected_end + 12]])
                self.assertTrue(reaper.blossom_state(script))
                outcomes[lethal, skipped] = hit_frames, own.total, caster_pool.death_tick
        if len(outcomes) == 4:
            for lethal in (False, True):
                self.assertEqual(outcomes[lethal, False], outcomes[lethal, True])

    def test_actual_helix_resolves_projected_barrier_expiry_across_world_gap(self):
        def scene():
            run, owner, caster, _ = reaper_tests.ReaperTests().scenario(
                team_mode=True, names=("Reaper", "Zarya", "Soldier"),
            )
            shooter = run.players[2]
            self.assertTrue(run.match.switch_hero(caster, heroes()[zarya_barriers.HERO]))
            owner.team = caster.team = 0
            shooter.team = 1
            x, y, z = owner.mover.position
            caster.mover.position[:] = (x + 4, y, z)
            shooter.mover.position[:] = (x, y, z + 8)
            caster.mover.body = shooter.mover.body = None
            for _ in range(28):
                run.step({})
            fight = run.match.combat
            facing = aim(fight._eye(caster), (x, y + 1, z))
            run.step({caster: (*facing, 16)})
            bubble = fight.zarya_bubbles.for_target(owner)
            self.assertIsNotNone(bubble)
            self.assertEqual(bubble.health, 200)
            heading = aim(fight._eye(shooter), (x, y + 1, z))
            return run, owner, caster, shooter, bubble, heading

        def advance(run, tick):
            run.frame = tick
            run.match.update(tick * combat.TICK, tick, frames=False)

        run, owner, _, shooter, bubble, heading = scene()
        fight = run.match.combat
        run.step({shooter: (*heading, 2)})
        fired = run.frame
        for _ in range(25):
            advance(run, run.frame + 1)
            if not fight.rockets:
                break
        self.assertFalse(fight.rockets)
        self.assertEqual(bubble.health, 80)
        travel = run.frame - fired
        for _ in range(140):
            advance(run, run.frame + 1)
            if fight.zarya_bubbles.for_target(owner) is None:
                break
        self.assertIsNone(fight.zarya_bubbles.for_target(owner))
        expiry = run.frame

        for protected in (True, False):
            outcomes = []
            for skipped in (False, True):
                with self.subTest(protected=protected, skipped=skipped):
                    run, owner, caster, shooter, bubble, heading = scene()
                    fight = run.match.combat
                    impact = expiry - int(protected)
                    while run.frame < impact - travel - 1:
                        run.step({})
                    run.step({shooter: (*heading, 2)})
                    rocket, = fight.rockets
                    advance(run, run.frame + 1)
                    self.assertEqual(rocket.last_tick, run.frame)
                    self.assertIs(fight.zarya_bubbles.for_target(owner), bubble)
                    frame = caster.commands.last_frame
                    contacts = []

                    def observe(missile, *, world=fight, hits=contacts, landed=fight._landed):
                        hits.append(world.tick)
                        landed(missile)

                    end = expiry + 35
                    with patch.object(fight, "_landed", side_effect=observe):
                        while run.frame < end:
                            advance(run, end if skipped else run.frame + 1)
                    self.assertEqual(caster.commands.last_frame, frame)
                    self.assertFalse(fight.rockets)
                    # Without the outer sphere the rocket needs one more tick to reach the body.
                    contact = impact + int(not protected)
                    self.assertEqual(contacts, [contact])
                    self.assertIsNone(fight.zarya_bubbles.for_target(owner))
                    self.assertEqual(bubble.health, 80 if protected else 200)
                    self.assertEqual(fight.vitals[owner.body].total, 250 if protected else 130)
                    self.assertEqual(shooter.stats.value(stats.HERO_DAMAGE), 0 if protected else 120)
                    hits = shooter.client.messages(combat.HIT)
                    self.assertEqual([hit["+0x84"] for hit in hits], [] if protected else [contact, contact])
                    outcomes.append((contacts, hits))
            if len(outcomes) == 2:
                self.assertEqual(outcomes[0], outcomes[1])

    def test_actual_personal_and_projected_bubbles_spend_shield_and_credit_zarya(self):
        for projected in (False, True):
            with self.subTest(projected=projected):
                names = ("Reaper", "Zarya", "Recipient") if projected else ("Reaper", "Zarya")
                run, owner, caster, _ = reaper_tests.ReaperTests().scenario(team_mode=projected, names=names)
                self.assertTrue(run.match.switch_hero(caster, heroes()[zarya_barriers.HERO]))
                owner.team, caster.team = 0, 1
                recipient = run.players[2] if projected else caster
                recipient.team = 1
                x, y, z = owner.mover.position
                caster.mover.position[:] = (x - 4 if projected else x, y, z + 4)
                caster.mover.body = None
                if projected:
                    recipient.mover.position[:] = (x, y, z + 4)
                    recipient.mover.body = None
                fight = run.match.combat
                yaw, pitch = aim(fight._eye(caster), (x, y + 1, z + 4))
                for _ in range(30):
                    run.step({caster: (yaw, pitch, 0)})
                run.step({caster: (yaw, pitch, 16 if projected else 8)})
                bubble = fight.zarya_bubbles.for_target(recipient)
                self.assertIsNotNone(bubble)
                self.assertIs(bubble.caster, caster)
                self.assertEqual(bubble.health, 200)
                target = next(t for t in fight._targets() if t.owner is recipient)
                initial = fight.vitals_of(target).total
                fight.vitals[owner.body].damage(100)
                owner.body_script.set_ult_charge(0)
                reaper_tests.ReaperTests().fire(run, owner, recipient)
                self.assertEqual(bubble.health, 80)
                self.assertEqual(fight.vitals_of(target).total, initial)
                self.assertEqual(fight.vitals[owner.body].total, 150)
                self.assertEqual(owner.body_script.component.vars[342].value(), 0)
                self.assertGreater(caster.body_script.component.entity_var(87).value(), 5)
                for _ in range(40):
                    run.step({})
                reaper_tests.ReaperTests().fire(run, owner, recipient)
                self.assertEqual(bubble.health, 0)
                self.assertIsNone(fight.zarya_bubbles.for_target(recipient))
                self.assertLess(fight.vitals_of(target).total, initial)
                self.assertLessEqual(len(owner.client.messages(combat.HIT)), 6)


class ReaperOrisaBarrierTests(unittest.TestCase):
    def launch_barrier(self, *, distance=8, pitch=8192, extra=()):
        run, owner, defender, _ = reaper_tests.ReaperTests().scenario(
            names=("Reaper", "Target", *extra),
        )
        self.assertTrue(run.match.switch_hero(defender, heroes()[0x02E000000000013E]))
        x, y, z = owner.mover.position
        defender.mover.position[:] = (x, y, z + distance)
        defender.mover.body = None
        for _ in range(30):
            run.step({})
        service = run.match.combat.orisa_barriers
        self.assertIsNotNone(service.geometry)
        run.step({defender: (-32768, pitch, 16)})
        for _ in range(45):
            flight = service.flights.get(defender.body)
            if flight is not None:
                return run, owner, defender, service, flight
            run.step({defender: (-32768, pitch, 0)})
        self.fail("Protective Barrier did not launch")

    def test_actual_orisa_projectile_lands_and_blocks_reaper_shell(self):
        run, owner, defender, service, flight = self.launch_barrier()
        for _ in range(45):
            run.step({defender: (-32768, 8192, 0)})
            if defender.body in service.actors:
                break
        self.assertAlmostEqual(flight.gravity, 9.8, places=5)
        actor = service.actors[defender.body]
        self.assertLess(actor.position[2], defender.mover.position[2])
        self.assertEqual(actor.health, 600)
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is defender)
        initial = fight.vitals_of(target).total
        fight.vitals[owner.body].damage(100)
        owner.body_script.set_ult_charge(0)
        reaper_tests.ReaperTests().fire(run, owner, defender)
        self.assertEqual(actor.health, 480)
        self.assertEqual(fight.vitals_of(target).total, initial)
        self.assertEqual(fight.vitals[owner.body].total, 150)
        self.assertEqual(owner.body_script.component.vars[342].value(), 0)

        start = run.frame
        fight.update((start + 1200) * combat.TICK, start + 1200, frames=False)
        self.assertTrue(actor.active())
        fight.update((start + 1300) * combat.TICK, start + 1300, frames=False)
        self.assertNotIn(defender.body, service.actors)

    def test_actual_orisa_barrier_first_blocks_shell_on_landing_tick(self):
        run, _, defender, service, _ = self.launch_barrier()
        for _ in range(45):
            run.step({})
            if defender.body in service.actors:
                break
        self.assertIn(defender.body, service.actors)
        landing_tick = run.frame

        for landed in (False, True):
            outcomes = []
            for skipped in (False, True):
                with self.subTest(landed=landed, skipped=skipped):
                    run, owner, defender, service, _ = self.launch_barrier()
                    fight = run.match.combat
                    contact = landing_tick if landed else landing_tick - 1
                    self.assertLess(run.frame + 1, contact)
                    input_frame = owner.commands.last_frame
                    while run.frame < contact:
                        run.frame = contact if skipped else run.frame + 1
                        run.match.update(run.frame * combat.TICK, run.frame, frames=False)
                    self.assertEqual(owner.commands.last_frame, input_frame)
                    self.assertEqual(defender.body in service.flights, not landed)
                    self.assertEqual(defender.body in service.actors, landed)
                    target = next(t for t in fight._targets() if t.owner is defender)
                    health = fight.vitals_of(target)
                    before = health.total
                    fight.vitals[owner.body].damage(100)
                    x, y, z = defender.mover.position
                    yaw, pitch = aim(fight._eye(owner), (x, y + 1.0, z))
                    owner.take_commands([Command(contact, yaw=yaw, pitch=pitch, buttons=1)])
                    self.assertEqual(owner.commands.last_frame, contact)
                    hits = owner.client.messages(combat.HIT)
                    reaper_health = fight.vitals[owner.body].total
                    if landed:
                        self.assertEqual(health.total, before)
                        self.assertEqual(reaper_health, 150)
                        self.assertFalse(hits)
                    else:
                        self.assertLess(health.total, before)
                        self.assertGreater(reaper_health, 150)
                        self.assertTrue(hits)
                        self.assertEqual({hit["+0x84"] for hit in hits}, {contact})
                        self.assertNotIn(defender.body, service.actors)
                        run.step({})
                    actor = service.actors.get(defender.body)
                    self.assertIsNotNone(actor)
                    self.assertNotIn(defender.body, service.flights)
                    self.assertEqual(actor.health, 480 if landed else 600)
                    outcomes.append((actor.position, actor.health, health.total, reaper_health, hits))
            self.assertEqual(len(outcomes), 2)
            self.assertEqual(outcomes[0], outcomes[1])


    def test_actual_orisa_barrier_lands_during_blossom_across_world_gap(self):
        outcomes = []
        for skipped in (False, True):
            with self.subTest(skipped=skipped):
                run, owner, defender, service, _ = self.launch_barrier(distance=6)
                fight = run.match.combat
                own = fight.vitals[owner.body]
                enemy = fight.vitals_of(next(t for t in fight._targets() if t.owner is defender))
                initial = enemy.total
                own.damage(100)
                script = owner.body_script
                script.set_ult_charge(script.component.entity_var(ULT_COST).value())
                run.step({owner: (0, 0, 32)})
                self.assertLess(enemy.total, initial)
                self.assertNotIn(defender.body, service.actors)
                channel = fight.reaper_blossoms[owner.body]
                self.assertFalse(channel["ended"])
                input_frame = owner.commands.last_frame
                shield_ticks = []

                def observe_shield(actor, amount, *, world=fight, ticks=shield_ticks, apply=service.damage):
                    ticks.append(world.tick)
                    apply(actor, amount)

                with (
                    patch.object(service, "land", wraps=service.land) as land,
                    patch.object(service, "damage", side_effect=observe_shield),
                ):
                    until = run.frame + 200
                    while run.frame < until:
                        run.frame = until if skipped else run.frame + 1
                        run.match.update(run.frame * combat.TICK, run.frame, frames=False)
                land.assert_called_once()
                landing_tick = land.call_args.args[2]
                self.assertEqual(owner.commands.last_frame, input_frame)
                self.assertTrue(channel["ended"])
                self.assertNotIn(defender.body, service.flights)
                actor = service.actors[defender.body]
                hits = [hit["+0x84"] for hit in owner.client.messages(combat.HIT)]
                self.assertTrue(hits)
                self.assertLess(hits[-1], landing_tick)
                self.assertGreater(len(shield_ticks), 1)
                self.assertEqual(shield_ticks[0], landing_tick)
                self.assertEqual(actor.health, 600 - reaper.BLOSSOM_DAMAGE * len(shield_ticks))
                self.assertAlmostEqual(own.total, 150 + (initial - enemy.total) * reaper.REAPING_RATIO,
                                       places=4)
                outcomes.append((actor.position, actor.health, enemy.total, own.total, hits, shield_ticks))
        if len(outcomes) == 2:
            self.assertEqual(outcomes[0], outcomes[1])


    def test_actual_orisa_barrier_break_pulse_precedes_blossom_body_damage(self):
        outcomes = []
        for skipped in (False, True):
            with self.subTest(skipped=skipped):
                run, owner, defender, service, _ = self.launch_barrier(distance=6)
                for _ in range(45):
                    run.step({})
                    if defender.body in service.actors:
                        break
                actor = service.actors.get(defender.body)
                self.assertIsNotNone(actor)
                fight = run.match.combat
                enemy = fight.vitals_of(next(t for t in fight._targets() if t.owner is defender))
                initial = enemy.total
                for expected in (480, 360, 240, 120):
                    reaper_tests.ReaperTests().fire(run, owner, defender)
                    self.assertEqual(actor.health, expected)
                    for _ in range(40):
                        run.step({})
                self.assertEqual(enemy.total, initial)
                self.assertFalse(owner.client.messages(combat.HIT))
                own = fight.vitals[owner.body]
                own.damage(150)
                script = owner.body_script
                script.set_ult_charge(script.component.entity_var(ULT_COST).value())
                shield_ticks = []

                def observe_shield(shield, amount, *, world=fight, ticks=shield_ticks, apply=service.damage):
                    ticks.append(world.tick)
                    apply(shield, amount)

                with patch.object(service, "damage", side_effect=observe_shield):
                    run.step({owner: (0, 0, 32)})
                    self.assertEqual(actor.health, 86)
                    cast_tick = run.frame
                    until = cast_tick + 200
                    while run.frame < until:
                        run.frame = until if skipped else run.frame + 1
                        run.match.update(run.frame * combat.TICK, run.frame, frames=False)
                self.assertEqual(owner.commands.last_frame, cast_tick)
                self.assertTrue(fight.reaper_blossoms[owner.body]["ended"])
                self.assertEqual(len(shield_ticks), 4)
                self.assertEqual(actor.health, 0)
                self.assertFalse(actor.active())
                hits = [hit["+0x84"] for hit in owner.client.messages(combat.HIT)]
                self.assertTrue(hits)
                self.assertIn(hits[0] - shield_ticks[-1], (12, 13))
                self.assertAlmostEqual(own.total, 100 + (initial - enemy.total) * reaper.REAPING_RATIO,
                                       places=4)
                outcomes.append((shield_ticks, hits, own.total, enemy.total))
        if len(outcomes) == 2:
            self.assertEqual(outcomes[0], outcomes[1])


    def test_actual_orisa_barrier_break_exposes_projected_barrier_to_next_shell(self):
        self.check_orisa_projected_shells(pitch=4096, remaining_outer=0)

    def test_actual_shell_splits_across_orisa_and_projected_barrier_surfaces(self):
        self.check_orisa_projected_shells(pitch=8192, remaining_outer=96)

    def prepare_overlapping_barriers(self, *, pitch, caster_offset=4):
        run, owner, defender, service, _ = self.launch_barrier(distance=6, pitch=pitch, extra=("Zarya",))
        caster = run.players[2]
        self.assertTrue(run.match.switch_hero(caster, heroes()[zarya_barriers.HERO]))
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        owner.team, defender.team, caster.team = 0, 1, 1
        x, y, z = defender.mover.position
        caster.mover.position[:] = (x - caster_offset, y, z)
        caster.mover.body = None
        for _ in range(45):
            run.step({})
            if defender.body in service.actors:
                break
        actor = service.actors.get(defender.body)
        self.assertIsNotNone(actor)
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is defender)
        health = fight.vitals_of(target)
        initial = health.total
        fire = reaper_tests.ReaperTests().fire
        for expected in (480, 360, 240, 120):
            fire(run, owner, defender)
            self.assertEqual(actor.health, expected)
            for _ in range(40):
                run.step({})
        self.assertEqual(health.total, initial)
        self.assertFalse(owner.client.messages(combat.HIT))

        heading = aim(fight._eye(caster), (x, y + 1, z))
        run.step({caster: (*heading, 16)})
        bubble = fight.zarya_bubbles.for_target(defender)
        self.assertIsNotNone(bubble)
        self.assertIs(bubble.caster, caster)
        return run, owner, defender, actor, bubble

    def check_orisa_projected_shells(self, *, pitch, remaining_outer):
        run, owner, defender, actor, bubble = self.prepare_overlapping_barriers(pitch=pitch)
        fight = run.match.combat
        health = fight.vitals[defender.body]
        initial = health.total
        own = fight.vitals[owner.body]
        own.damage(100)
        fire = reaper_tests.ReaperTests().fire
        energy = bubble.caster.body_script.component.entity_var(87)
        start_energy = energy.value()
        fire(run, owner, defender)
        if remaining_outer:
            self.assertEqual((actor.health, bubble.health, health.total, own.total),
                             (remaining_outer, 200 - remaining_outer, initial, 150))
            self.assertFalse(owner.client.messages(combat.HIT))
            self.assertGreater(energy.value(), start_energy or 0)
            return
        self.assertEqual((actor.health, bubble.health, health.total, own.total), (0, 200, initial, 150))
        self.assertEqual(energy.value(), start_energy)
        for _ in range(40):
            run.step({})

        fire(run, owner, defender)
        self.assertEqual((bubble.health, health.total, own.total), (80, initial, 150))
        self.assertFalse(owner.client.messages(combat.HIT))
        self.assertGreater(energy.value(), start_energy or 0)
        for _ in range(40):
            run.step({})

        fire(run, owner, defender)
        self.assertEqual(bubble.health, 0)
        self.assertIsNone(fight.zarya_bubbles.for_target(defender))
        hits = owner.client.messages(combat.HIT)
        self.assertEqual(len(hits), 6)
        critical = sum(bool(hit["+0x78"]["+0x8"] & combat.CRITICAL) for hit in hits)
        self.assertEqual(critical, 3)
        body_damage = (6 - critical) * 3 + critical * 7
        self.assertEqual(health.total, initial - body_damage)
        self.assertAlmostEqual(own.total, 150 + body_damage * reaper.REAPING_RATIO, places=4)

    def test_actual_blossom_breaks_overlapping_orisa_and_projected_barriers(self):
        outcomes = []
        for skipped in (False, True):
            with self.subTest(skipped=skipped):
                run, owner, defender, actor, bubble = self.prepare_overlapping_barriers(
                    pitch=4096, caster_offset=8,
                )
                fight = run.match.combat
                health = fight.vitals[defender.body]
                initial = health.total
                caster = fight.vitals_of(next(t for t in fight._targets() if t.owner is bubble.caster))
                caster_health = caster.total
                own = fight.vitals[owner.body]
                own.damage(100)
                script = owner.body_script
                script.set_ult_charge(script.component.entity_var(ULT_COST).value())
                shield_hits = []

                def observe(world, shield, amount, shooter, *, hits=shield_hits, apply=barriers.damage):
                    hits.append((shield, world.tick))
                    apply(world, shield, amount, shooter)

                with patch.object(barriers, "damage", side_effect=observe):
                    run.step({owner: (0, 0, 32)})
                    cast_tick = run.frame
                    self.assertEqual((actor.health, bubble.health, health.total, own.total),
                                     (86, 166, initial, 150))
                    until = cast_tick + 200
                    while run.frame < until:
                        run.frame = until if skipped else run.frame + 1
                        run.match.update(run.frame * combat.TICK, run.frame, frames=False)
                self.assertEqual(owner.commands.last_frame, cast_tick)
                self.assertTrue(fight.reaper_blossoms[owner.body]["ended"])
                outer_ticks = [tick for shield, tick in shield_hits if shield is actor]
                inner_ticks = [tick for shield, tick in shield_hits if shield is bubble]
                self.assertEqual((len(outer_ticks), len(inner_ticks)), (4, 6))
                self.assertEqual(outer_ticks, inner_ticks[:4])
                self.assertEqual(outer_ticks[0], cast_tick)
                self.assertEqual((actor.health, bubble.health), (0, 0))
                self.assertIsNone(fight.zarya_bubbles.for_target(defender))
                hits = [hit["+0x84"] for hit in owner.client.messages(combat.HIT)]
                self.assertTrue(hits)
                self.assertIn(hits[0] - inner_ticks[-1], (12, 13))
                self.assertEqual(caster.total, caster_health)
                self.assertAlmostEqual(own.total, 150 + (initial - health.total) * reaper.REAPING_RATIO,
                                       places=4)
                outcomes.append((outer_ticks, inner_ticks, hits, own.total, health.total))
        if len(outcomes) == 2:
            self.assertEqual(outcomes[0], outcomes[1])


    def helix_scene(self, offset=0):
        run, owner, orisa, service, _ = self.launch_barrier(pitch=4096, extra=("Soldier",))
        shooter = run.players[2]
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        owner.team = orisa.team = 1
        shooter.team = 0
        x, y, z = owner.mover.position
        shooter.mover.position[:] = (x, y, z + offset)
        owner.mover.position[:] = (x, y, z + 9)
        orisa.mover.position[0] += 5
        shooter.mover.body = owner.mover.body = orisa.mover.body = None
        angle = aim(run.match.combat._eye(shooter), (x, y + 1.0, z + 9))
        return run, owner, orisa, service, shooter, angle

    def test_actual_helix_crosses_orisa_barrier_landing_in_world_order(self):
        for offset, expected in ((0, (130, 600)), (-6, (250, 480))):
            outcomes = []
            for skipped in (False, True):
                with self.subTest(offset=offset, skipped=skipped):
                    run, owner, orisa, service, shooter, angle = self.helix_scene(offset)
                    fight = run.match.combat
                    run.step({shooter: (*angle, 2)})
                    rocket, = fight.rockets
                    self.assertIs(rocket.shooter, shooter)
                    run.step({})
                    self.assertEqual(rocket.last_tick, run.frame)
                    self.assertIn(orisa.body, service.flights)
                    landings, impacts = [], []

                    def observe_landing(flight, position, tick, *, missile=rocket,
                                        samples=landings, land=service.land):
                        samples.append((tick, missile.position[2], position[2]))
                        land(flight, position, tick)

                    def observe_impact(missile, *, world=fight, ticks=impacts, landed=fight._landed):
                        ticks.append(world.tick)
                        landed(missile)

                    with (
                        patch.object(service, "land", side_effect=observe_landing),
                        patch.object(fight, "_landed", side_effect=observe_impact),
                    ):
                        until = run.frame + 30
                        while run.frame < until:
                            run.frame = until if skipped else run.frame + 1
                            run.match.update(run.frame * combat.TICK, run.frame, frames=False)
                    self.assertFalse(fight.rockets)
                    actor = service.actors[orisa.body]
                    self.assertEqual((fight.vitals[owner.body].total, actor.health), expected)
                    self.assertEqual((len(landings), len(impacts)), (1, 1))
                    landing_tick, rocket_z, barrier_z = landings[0]
                    self.assertGreater(impacts[0], landing_tick)
                    self.assertEqual(rocket_z > barrier_z, offset == 0)
                    hits = [hit["+0x84"] for hit in shooter.client.messages(combat.HIT)]
                    self.assertEqual(bool(hits), offset == 0)
                    outcomes.append((landings, impacts, hits))
            if len(outcomes) == 2:
                self.assertEqual(outcomes[0], outcomes[1])


    def test_actual_helix_resolves_orisa_barrier_expiry_in_world_order(self):
        def advance_to_active_barrier():
            run, owner, orisa, service, shooter, angle = self.helix_scene()
            for _ in range(40):
                run.step({})
                if orisa.body in service.actors:
                    break
            actor = service.actors.get(orisa.body)
            self.assertIsNotNone(actor)
            run.frame += 1200
            run.match.update(run.frame * combat.TICK, run.frame, frames=False)
            self.assertTrue(actor.active())
            return run, owner, service, shooter, angle, actor

        run, _, _, _, _, actor = advance_to_active_barrier()
        for _ in range(100):
            run.frame += 1
            run.match.update(run.frame * combat.TICK, run.frame, frames=False)
            if not actor.active():
                break
        self.assertFalse(actor.active())
        expiry = run.frame

        for lead, expected in ((10, (250, 480)), (4, (130, 600))):
            outcomes = []
            for skipped in (False, True):
                with self.subTest(lead=lead, skipped=skipped):
                    run, owner, service, shooter, angle, actor = advance_to_active_barrier()
                    fight = run.match.combat
                    run.frame = expiry - lead - 1
                    run.match.update(run.frame * combat.TICK, run.frame, frames=False)
                    run.step({shooter: (*angle, 2)})
                    rocket, = fight.rockets
                    run.step({})
                    self.assertEqual(rocket.last_tick, run.frame)
                    self.assertTrue(actor.active())
                    impacts, expired = [], []

                    def observe_impact(missile, *, world=fight, ticks=impacts, landed=fight._landed):
                        ticks.append(world.tick)
                        landed(missile)

                    def observe_barrier(tick, dt, *, shield=actor, ticks=expired, update=service.update):
                        active = shield.active()
                        update(tick, dt)
                        if active and not shield.active():
                            ticks.append(tick)

                    with (
                        patch.object(fight, "_landed", side_effect=observe_impact),
                        patch.object(service, "update", side_effect=observe_barrier),
                    ):
                        until = run.frame + 30
                        while run.frame < until:
                            run.frame = until if skipped else run.frame + 1
                            run.match.update(run.frame * combat.TICK, run.frame, frames=False)
                    self.assertFalse(fight.rockets)
                    self.assertEqual(expired, [expiry])
                    self.assertEqual(len(impacts), 1)
                    self.assertEqual(impacts[0] < expiry, lead == 10)
                    self.assertEqual((fight.vitals[owner.body].total, actor.health), expected)
                    hits = [hit["+0x84"] for hit in shooter.client.messages(combat.HIT)]
                    self.assertEqual(bool(hits), lead == 4)
                    outcomes.append((impacts, hits))
            if len(outcomes) == 2:
                self.assertEqual(outcomes[0], outcomes[1])


class ReaperWinstonBarrierTests(unittest.TestCase):
    def dome(self, distance=7, *, allied=False):
        names = ("Reaper", "Winston", "Enemy") if allied else ("Reaper", "Winston")
        run, owner, defender, _ = reaper_tests.ReaperTests().scenario(team_mode=allied, names=names)
        self.assertTrue(run.match.switch_hero(defender, heroes()[0x02E0000000000009]))
        if allied:
            owner.team = defender.team = 0
            run.players[2].team = 1
        x, y, z = owner.mover.position
        defender.mover.position[:] = (x, y, z + distance)
        defender.mover.body = None
        run.step({defender: (-32768, 0, 16)})
        fight = run.match.combat
        for _ in range(45):
            if fight.winston_bubbles.actors:
                break
            run.step({defender: (-32768, 0, 0)})
        bubble, = fight.winston_bubbles.actors.values()
        self.assertEqual(bubble.health, 700)
        target = next(t for t in fight._targets() if t.owner is defender)
        return run, owner, defender, target, bubble

    def test_actual_helix_resolves_winston_dome_expiry_across_world_gap(self):
        def scene():
            run, owner, winston, target, bubble = self.dome(1.5, allied=True)
            shooter = run.players[2]
            x, y, z = owner.mover.position
            # The dome remains at its deployed position after Winston moves.
            winston.mover.position[:] = (x + 10, y, z + 1.5)
            shooter.mover.position[:] = (x, y, z - 10)
            winston.mover.body = shooter.mover.body = None
            heading = aim(run.match.combat._eye(shooter), (x, y + 1, z))
            return run, owner, target, shooter, bubble, heading

        def advance(run, tick):
            run.frame = tick
            run.match.update(tick * combat.TICK, tick, frames=False)

        run, owner, _, shooter, bubble, heading = scene()
        fight = run.match.combat
        run.step({shooter: (*heading, 2)})
        fired = run.frame
        for _ in range(25):
            advance(run, run.frame + 1)
            if not fight.rockets:
                break
        self.assertFalse(fight.rockets)
        self.assertEqual(bubble.health, 580)
        self.assertEqual(fight.vitals[owner.body].total, 250)
        travel = run.frame - fired
        advance(run, run.frame + 500)
        self.assertFalse(bubble.retired)
        for _ in range(150):
            advance(run, run.frame + 1)
            if bubble.retired:
                break
        self.assertTrue(bubble.retired)
        expiry = run.frame

        for intercepted in (True, False):
            outcomes = []
            for skipped in (False, True):
                with self.subTest(intercepted=intercepted, skipped=skipped):
                    run, owner, target, shooter, bubble, heading = scene()
                    fight = run.match.combat
                    surface_tick = expiry - int(intercepted)
                    advance(run, surface_tick - travel - 1)
                    self.assertFalse(bubble.retired)
                    run.step({shooter: (*heading, 2)})
                    rocket, = fight.rockets
                    advance(run, run.frame + 1)
                    self.assertEqual(rocket.last_tick, run.frame)
                    contacts = []

                    def observe(missile, *, world=fight, dome=bubble, hits=contacts, landed=fight._landed):
                        hits.append((world.tick, dome.retired))
                        landed(missile)

                    end = expiry + 35
                    with patch.object(fight, "_landed", side_effect=observe):
                        while run.frame < end:
                            advance(run, end if skipped else run.frame + 1)
                    self.assertTrue(bubble.retired)
                    self.assertFalse(fight.rockets)
                    self.assertEqual(len(contacts), 1)
                    if intercepted:
                        self.assertEqual(contacts[0], (surface_tick, False))
                    else:
                        self.assertEqual(contacts[0], (surface_tick + 3, True))
                    self.assertEqual(bubble.health, 580 if intercepted else 700)
                    self.assertEqual(fight.vitals[owner.body].total, 250 if intercepted else 130)
                    self.assertEqual(fight.vitals_of(target).total, 500)
                    self.assertEqual(shooter.stats.value(stats.HERO_DAMAGE), 0 if intercepted else 120)
                    hit_frames = [hit["+0x84"] for hit in shooter.client.messages(combat.HIT)]
                    self.assertEqual(hit_frames, [] if intercepted else [contacts[0][0]] * 2)
                    outcomes.append(contacts)
            if len(outcomes) == 2:
                self.assertEqual(outcomes[0], outcomes[1])

    def test_actual_shot_from_inside_dome_hits_body_without_spending_shield(self):
        run, owner, defender, target, bubble = self.dome(1.5)
        fight = run.match.combat
        initial = fight.vitals_of(target).total
        fight.vitals[owner.body].damage(100)
        reaper_tests.ReaperTests().fire(run, owner, defender)
        self.assertEqual(bubble.health, 700)
        self.assertEqual(fight.vitals_of(target).total, initial - 60)
        self.assertAlmostEqual(fight.vitals[owner.body].total, 171, places=4)

    def test_actual_shell_passes_allied_dome_to_enemy_body(self):
        run, owner, _, target, bubble = self.dome(6, allied=True)
        enemy = run.players[2]
        x, y, z = owner.mover.position
        enemy.mover.position[:] = (x, y, z + 7)
        enemy.mover.body = None
        for _ in range(4):
            run.step({})
        fight = run.match.combat
        enemy_target = next(t for t in fight._targets() if t.owner is enemy)
        enemy_vitals = fight.vitals_of(enemy_target)
        initial = enemy_vitals.total
        ally_initial = fight.vitals_of(target).total
        reaper_tests.ReaperTests().fire(run, owner, enemy)
        self.assertEqual(bubble.health, 700)
        self.assertEqual(fight.vitals_of(target).total, ally_initial)
        self.assertLess(enemy_vitals.total, initial)

    def test_actual_six_shells_break_dome_and_remaining_pellets_hit_body(self):
        run, owner, defender, target, bubble = self.dome(5.5)
        fight = run.match.combat
        initial = fight.vitals_of(target).total
        for expected in (580, 460, 340, 220, 100):
            reaper_tests.ReaperTests().fire(run, owner, defender)
            self.assertEqual(bubble.health, expected)
            self.assertEqual(fight.vitals_of(target).total, initial)
            for _ in range(40):
                run.step({})
        reaper_tests.ReaperTests().fire(run, owner, defender)
        self.assertEqual(bubble.health, 0)
        self.assertFalse(bubble.retired)
        hits = owner.client.messages(combat.HIT)
        self.assertEqual(len(hits), 3)
        self.assertEqual(sum(hit["+0x78"]["+0x4"] for hit in hits), 21)
        self.assertEqual(fight.vitals_of(target).total, initial - 21)
        callee = next(i for i in bubble.component.instances.values() if i.graph.index == 0xFC)
        self.assertTrue(callee.states[12].active)
        for _ in range(4):
            run.step({})
        self.assertTrue(bubble.retired)
        self.assertFalse(fight.winston_bubbles.actors)

    def overlapping_domes(self):
        fixture = reaper_tests.ReaperTests()
        run, owner, far_defender, _ = fixture.scenario(
            team_mode=True, names=("Reaper", "Far Winston", "Near Winston"),
        )
        near_defender = run.players[2]
        owner.team = 0
        x, y, z = owner.mover.position
        for defender, distance in ((far_defender, 7.5), (near_defender, 5.5)):
            defender.team = 1
            self.assertTrue(run.match.switch_hero(defender, find_hero("Winston")))
            defender.mover.position[:] = (x, y, z + distance)
            defender.mover.body = None
        run.step({far_defender: (-32768, 0, 16), near_defender: (-32768, 0, 16)})
        fight = run.match.combat
        for _ in range(45):
            if len(fight.winston_bubbles.actors) == 2:
                break
            run.step({})
        near, far = sorted(fight.winston_bubbles.actors.values(), key=lambda dome: dome.centre[2])
        self.assertEqual((near.health, far.health), (700, 700))
        defenders = [fight.vitals_of(t) for t in fight._targets()
                     if t.owner in (near_defender, far_defender)]
        self.assertEqual(len(defenders), 2)
        return run, owner, near_defender, near, far, defenders

    def test_actual_shell_breaks_near_dome_then_spends_far_overlapping_dome(self):
        run, owner, near_defender, near, far, defenders = self.overlapping_domes()
        fixture = reaper_tests.ReaperTests()
        initial = [pool.total for pool in defenders]
        own = run.match.combat.vitals[owner.body]
        own.damage(100)
        for expected in (580, 460, 340, 220, 100):
            fixture.fire(run, owner, near_defender)
            self.assertEqual((near.health, far.health), (expected, 700))
            for _ in range(40):
                run.step({})
        fixture.fire(run, owner, near_defender)
        self.assertEqual((near.health, far.health), (0, 682))
        self.assertEqual([pool.total for pool in defenders], initial)
        self.assertEqual(own.total, 150)

    def test_actual_blossom_pulses_both_overlapping_domes_before_bodies(self):
        run, owner, _, near, far, defenders = self.overlapping_domes()
        fight = run.match.combat
        fight.winston_bubbles.damage(near, 680)
        fight.winston_bubbles.damage(far, 646)
        initial = [pool.total for pool in defenders]
        own = fight.vitals[owner.body]
        own.damage(100)
        owner.body_script.set_ult_charge(2100)
        run.step({owner: (0, 0, 32)})
        self.assertEqual((near.health, far.health), (0, 20))
        self.assertEqual([pool.total for pool in defenders], initial)
        self.assertEqual(own.total, 150)
        for _ in range(15):
            run.step({})
            if far.health == 0:
                break
        self.assertEqual(far.health, 0)
        self.assertEqual([pool.total for pool in defenders], initial)
        self.assertEqual(own.total, 150)
        for _ in range(15):
            run.step({})
            if [pool.total for pool in defenders] != initial:
                break
        self.assertEqual([pool.total for pool in defenders], [health - 29 for health in initial])
        self.assertAlmostEqual(own.total, 150 + 58 * 0.35, places=4)

    def test_actual_blossom_resumes_body_damage_after_winston_dome_expires(self):
        run, owner, _, target, bubble = self.dome(6)
        fight = run.match.combat
        for _ in range(555):
            run.step({})
        self.assertFalse(bubble.retired)
        own = fight.vitals[owner.body]
        own.damage(100)
        enemy = fight.vitals_of(target)
        initial = enemy.total
        owner.body_script.set_ult_charge(2100)
        run.step({owner: (0, 0, 32)})
        self.assertEqual((bubble.health, enemy.total, own.total), (666, initial, 150))
        for _ in range(12):
            run.step({})
            if bubble.retired:
                break
        self.assertTrue(bubble.retired)
        self.assertNotIn(bubble.body, fight.winston_bubbles.actors)
        self.assertEqual((enemy.total, own.total), (initial, 150))
        for _ in range(15):
            run.step({})
            if enemy.total < initial:
                break
        self.assertEqual(enemy.total, initial - 29)
        self.assertAlmostEqual(own.total, 150 + 29 * 0.35, places=4)
        self.assertEqual(bubble.health, 666)

    def test_actual_blossom_preserves_dome_expiry_order_across_skipped_frames(self):
        for skipped in (False, True):
            with self.subTest(skipped=skipped):
                run, owner, _, target, bubble = self.dome(6)
                fight = run.match.combat
                for _ in range(530):
                    run.step({})
                own = fight.vitals[owner.body]
                own.damage(100)
                enemy = fight.vitals_of(target)
                initial = enemy.total
                owner.body_script.set_ult_charge(2100)
                run.step({owner: (0, 0, 32)})
                self.assertEqual((bubble.health, enemy.total, own.total), (666, initial, 150))
                if skipped:
                    run.frame += 55
                    run.match.update(run.frame * combat.TICK, run.frame)
                else:
                    for _ in range(55):
                        run.step({})
                self.assertTrue(bubble.retired)
                self.assertEqual(bubble.health, 598)
                self.assertEqual(enemy.total, initial - 58)
                self.assertAlmostEqual(own.total, 150 + 58 * 0.35, places=4)

    def test_actual_winston_dome_deployed_during_blossom_stops_body_damage(self):
        run, owner, defender, _ = reaper_tests.ReaperTests().scenario()
        self.assertTrue(run.match.switch_hero(defender, find_hero("Winston")))
        x, y, z = owner.mover.position
        defender.mover.position[:] = (x, y, z + 6)
        defender.mover.body = None
        fight = run.match.combat
        own = fight.vitals[owner.body]
        own.damage(100)
        owner.body_script.set_ult_charge(2100)
        run.step({owner: (0, 0, 32)})
        enemy = fight.vitals_of(next(t for t in fight._targets() if t.owner is defender))
        self.assertFalse(fight.winston_bubbles.actors)
        self.assertEqual(enemy.total, 471)
        healed = own.total
        self.assertAlmostEqual(healed, 150 + 29 * 0.35, places=4)

        run.step({defender: (-32768, 0, 16)})
        for _ in range(24):
            if fight.winston_bubbles.actors:
                break
            run.step({})
        bubble, = fight.winston_bubbles.actors.values()
        for _ in range(15):
            if bubble.health < 700:
                break
            run.step({})
        self.assertEqual(bubble.health, 666)
        self.assertEqual(enemy.total, 471)
        self.assertEqual(own.total, healed)
        for _ in range(190):
            run.step({})
        self.assertIsNone(reaper.blossom_state(owner.body_script))
        self.assertLess(bubble.health, 666)
        self.assertEqual(enemy.total, 471)
        self.assertEqual(own.total, healed)

    def test_actual_shell_and_blossom_hit_deployed_winston_dome(self):
        run, owner, defender, target, bubble = self.dome()
        fight = run.match.combat
        initial = fight.vitals_of(target).total
        reaper_tests.ReaperTests().fire(run, owner, defender)
        self.assertEqual(bubble.health, 580)
        self.assertEqual(fight.vitals_of(target).total, initial)
        owner.body_script.set_ult_charge(2100)
        run.step({owner: (0, 0, 32)})
        for _ in range(200):
            run.step({})
        self.assertEqual(bubble.health, 70)
        self.assertEqual(fight.vitals_of(target).total, initial)
        for _ in range(430):
            run.step({})
        self.assertFalse(fight.winston_bubbles.actors)


class ReaperDeflectionTests(unittest.TestCase):
    def dragonblade_sleep_scene(self):
        run, owner, genji, _ = reaper_tests.ReaperTests().scenario(names=("Reaper", "Genji", "Ana"))
        ana = run.players[2]
        self.assertTrue(run.match.switch_hero(genji, heroes()[0x02E0000000000029]))
        self.assertTrue(run.match.switch_hero(ana, find_hero("Ana")))
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        owner.team = ana.team = 0
        genji.team = 1
        fight = run.match.combat
        x, y, z = owner.mover.position
        genji.mover.position[:] = (x, y, z + 2)
        ana.mover.position[:] = (x + 4, y, z + 2)
        genji.mover.body = ana.mover.body = None
        for _ in range(30):
            run.step({})
        sword = aim(fight._eye(genji), (x, y + 1, z))
        dart = aim(fight._eye(ana), (x, y + 1, z + 2))
        genji.body_script.set_ult_charge(1932)
        run.step({genji: (*sword, 32)})
        return run, owner, genji, ana, sword, dart

    def test_actual_sleep_dart_blocks_dragonblade_windup_and_fresh_slash_recovers(self):
        run, owner, genji, ana, sword, dart = self.dragonblade_sleep_scene()
        fight = run.match.combat
        for _ in range(84):
            run.step({})
        run.step({ana: (*dart, 8)})
        for _ in range(30):
            if fight.rockets:
                break
            run.step({ana: (*dart, 0)})
        self.assertEqual(len(fight.rockets), 1)
        blade = next(i for i in genji.body_script.component.instances.values() if i.graph.index == 0x0317)
        duration = blade.vars[215].value()
        run.step({genji: (*sword, 1)})
        weapon = next(i for i in genji.body_script.component.instances.values() if i.graph.index == 0x0304)
        self.assertTrue(weapon.node_state(weapon.graph.node(41)).active)
        for _ in range(36):
            run.step({genji: (*sword, 0)})
        sleep = fight.ana_sleeps[genji.body]
        self.assertEqual(fight.vitals[genji.body].total, 195)
        self.assertEqual(fight.vitals[owner.body].total, 250)
        reaper_tests.ReaperTests().fire(run, owner, genji)
        self.assertTrue(sleep["waking"])
        while run.frame < sleep["end"]:
            run.step({genji: (*sword, 0)})
        self.assertFalse(fight.is_stunned(genji))
        self.assertEqual(fight.vitals[owner.body].total, 250)
        self.assertGreater(blade.vars[215].value(), 0)
        self.assertLess(blade.vars[215].value(), duration - 0.9)
        run.step({genji: (*sword, 1)})
        for _ in range(30):
            run.step({genji: (*sword, 0)})
        self.assertEqual(fight.vitals[owner.body].total, 140)

    def test_actual_dragonblade_expires_during_sleep_and_primary_returns_after_wake(self):
        run, owner, genji, ana, sword, dart = self.dragonblade_sleep_scene()
        fight = run.match.combat
        script = genji.body_script
        blade = next(i for i in script.component.instances.values() if i.graph.index == 0x0317)
        weapon = next(i for i in script.component.instances.values() if i.graph.index == 0x0304)
        for _ in range(260):
            run.step({})
        self.assertGreater(blade.vars[215].value(), 0)
        run.step({ana: (*dart, 8)})
        for _ in range(40):
            run.step({ana: (*dart, 0)})
            if genji.body in fight.ana_sleeps:
                break
        sleep = fight.ana_sleeps[genji.body]
        self.assertEqual(sleep["end"] - sleep["start"], 313)
        self.assertEqual(fight.vitals[genji.body].total, 195)
        self.assertGreater(blade.vars[215].value(), 0)
        while blade.vars[215].value() > 0 and run.frame < sleep["end"]:
            run.step({})
        self.assertEqual(blade.vars[215].value(), 0)
        self.assertTrue(fight.is_stunned(genji))
        self.assertTrue(blade.node_state(blade.graph.node(18)).active)
        run.step({genji: (*sword, 1)})
        while run.frame < sleep["end"]:
            run.step({})
        self.assertFalse(fight.is_stunned(genji))
        self.assertFalse(blade.node_state(blade.graph.node(18)).active)
        self.assertFalse(fight.rockets)
        self.assertEqual(weapon.vars[53].value(), 30)
        self.assertEqual(fight.vitals[owner.body].total, 250)
        run.step({genji: (*sword, 1)})
        shuriken, = fight.rockets
        self.assertEqual(shuriken.definition, 0x04000000000007CA)
        for _ in range(24):
            run.step({genji: (*sword, 0)})
        self.assertEqual(weapon.vars[53].value(), 27)
        self.assertEqual(fight.vitals[owner.body].total, 163)

    def test_actual_reaper_shell_kills_genji_before_dragonblade_contact(self):
        run, owner, genji, _ = reaper_tests.ReaperTests().scenario()
        self.assertTrue(run.match.switch_hero(genji, heroes()[0x02E0000000000029]))
        fight = run.match.combat
        x, y, z = owner.mover.position
        genji.mover.position[:] = (x, y, z + 2)
        genji.mover.body = None
        for _ in range(30):
            run.step({})
        own = fight.vitals[owner.body]
        own.damage(100)
        enemy = fight.vitals_of(next(t for t in fight._targets() if t.owner is genji))
        enemy.damage(160)
        script = genji.body_script
        weapon = next(i for i in script.component.instances.values() if i.graph.index == 0x0304)
        script.set_ult_charge(1932)
        heading = aim(fight._eye(genji), (x, y + 1, z))
        run.step({genji: (*heading, 32)})
        for _ in range(84):
            run.step({genji: (*heading, 0)})
        run.step({genji: (*heading, 1)})
        for _ in range(3):
            run.step({genji: (*heading, 0)})
        self.assertTrue(weapon.node_state(weapon.graph.node(41)).active)
        self.assertEqual(own.total, 150)
        self.assertEqual(script.component.entity_var(342).value(), 0)
        reaper_tests.ReaperTests().fire(run, owner, genji)
        self.assertTrue(enemy.dead)
        self.assertEqual(enemy.killer, owner.body)
        self.assertAlmostEqual(own.total, 164, places=4)
        for _ in range(35):
            run.step({genji: (*heading, 0)})
        self.assertAlmostEqual(own.total, 164, places=4)
        self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), 0)
        self.assertLess(script.component.entity_var(342).value(), 1932)

    def test_actual_dragonblade_ends_blossom_only_when_lethal(self):
        for lethal in (False, True):
            with self.subTest(lethal=lethal):
                run, owner, genji, _ = reaper_tests.ReaperTests().scenario(
                    names=("Reaper", "Genji", "Witness")
                )
                witness = run.players[2]
                self.assertTrue(run.match.switch_hero(genji, heroes()[0x02E0000000000029]))
                fight = run.match.combat
                x, y, z = owner.mover.position
                genji.mover.position[:] = (x, y, z + 2)
                witness.mover.position[:] = (x + 5, y, z)
                genji.mover.body = witness.mover.body = None
                for _ in range(30):
                    run.step({})
                own = fight.vitals[owner.body]
                own.damage(240 if lethal else 50)
                witness_pool = fight.vitals_of(next(t for t in fight._targets() if t.owner is witness))
                initial = witness_pool.total
                heading = aim(fight._eye(genji), (x, y + 1, z))
                genji.body_script.set_ult_charge(1932)
                run.step({genji: (*heading, 32)})
                for _ in range(84):
                    run.step({genji: (*heading, 0)})
                owner.body_script.set_ult_charge(2100)
                run.step({owner: (0, 0, 32), genji: (*heading, 1)})
                channel = fight.reaper_blossoms[owner.body]
                self.assertEqual(witness_pool.total, initial - 34)
                for _ in range(24):
                    run.step({genji: (*heading, 0)})
                    if genji.stats.value(stats.HERO_DAMAGE):
                        break
                self.assertEqual(witness_pool.total, initial - 68)
                self.assertEqual(own.dead, lethal)
                dealt = genji.stats.value(stats.HERO_DAMAGE)
                self.assertAlmostEqual(dealt, 57.6 if lethal else 110, places=4)
                self.assertEqual(owner.body_script.component.vars[342].value(), 0)
                if lethal:
                    self.assertEqual(own.killer, genji.body)
                for _ in range(26):
                    run.step({})
                self.assertEqual(channel["ended"], lethal)
                if lethal:
                    run.frame += 200
                    run.match.update(run.frame * combat.TICK, run.frame, frames=False)
                    self.assertEqual(witness_pool.total, initial - 68)
                else:
                    self.assertLess(witness_pool.total, initial - 68)

    def test_actual_dragonblade_shadow_step_survival_and_immunity(self):
        fixture = control_tests.ReaperControlTests()
        for case in ("early", "lethal", "immune"):
            with self.subTest(case=case):
                run, owner, genji, _ = reaper_tests.ReaperTests().scenario()
                self.assertTrue(run.match.switch_hero(genji, heroes()[0x02E0000000000029]))
                fight = run.match.combat
                x, y, z = owner.mover.position
                genji.mover.position[:] = (x, y, z + 2)
                genji.mover.body = None
                for _ in range(30):
                    run.step({})
                pool = fight.vitals[owner.body]
                if case == "lethal":
                    pool.damage(150)
                genji.body_script.set_ult_charge(1932)
                heading = aim(fight._eye(genji), (x, y + 1, z))
                run.step({genji: (*heading, 32)})
                for _ in range(84):
                    run.step({})
                start = tuple(owner.mover.position)
                destination = fixture.start_shadow(run, owner)["target"]
                if case == "immune":
                    for _ in range(64):
                        run.step({})
                self.assertEqual(reaper.intangible(owner), case == "immune")
                with patch.object(fight, "dragonblade_damage", wraps=fight.dragonblade_damage) as slash:
                    run.step({genji: (*heading, 1)})
                    for _ in range(24):
                        run.step({genji: (*heading, 0)})
                self.assertEqual(sum(call.args[1] == 0 for call in slash.call_args_list), 1)
                self.assertEqual(pool.total, {"early": 140, "lethal": 0, "immune": 250}[case])
                if case == "lethal":
                    self.assertEqual(pool.killer, genji.body)
                for _ in range(125):
                    run.step({})
                if case == "lethal":
                    self.assertEqual(tuple(owner.mover.position), start)
                else:
                    for actual, expected in zip(owner.mover.position, destination, strict=True):
                        self.assertAlmostEqual(actual, expected, delta=0.1)

    def test_actual_dragonblade_breaks_projected_barrier_without_body_spill(self):
        for breaks in (False, True):
            with self.subTest(breaks=breaks):
                run, owner, genji, bubble = self.projected_barrier_against_genji(breaks=False, distance=2)
                fight = run.match.combat
                x, y, z = owner.mover.position
                heading = aim(fight._eye(genji), (x, y + 1, z))
                genji.body_script.set_ult_charge(1932)
                run.step({genji: (*heading, 32)})
                for _ in range(84):
                    run.step({genji: (*heading, 0)})
                self.assertIs(fight.zarya_bubbles.for_target(owner), bubble)
                if breaks:
                    bubble.health = 80
                run.step({genji: (*heading, 1)})
                for _ in range(20):
                    run.step({genji: (*heading, 0)})
                self.assertEqual(bubble.health, 0 if breaks else 90)
                self.assertEqual(fight.vitals[owner.body].total, 250)
                self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), 0)
                self.assertEqual(fight.zarya_bubbles.for_target(owner) is None, breaks)
                for _ in range(39):
                    run.step({genji: (*heading, 0)})
                self.assertIsNone(fight.zarya_bubbles.for_target(owner))
                run.step({genji: (*heading, 1)})
                for _ in range(24):
                    run.step({genji: (*heading, 0)})
                self.assertEqual(fight.vitals[owner.body].total, 140)
                self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), 110)

    def test_actual_dragonblade_two_swings_then_shuriken_after_sheathe(self):
        run, owner, genji, _ = reaper_tests.ReaperTests().scenario()
        self.assertTrue(run.match.switch_hero(genji, heroes()[0x02E0000000000029]))
        fight = run.match.combat
        x, y, z = owner.mover.position
        genji.mover.position[:] = (x, y, z + 2)
        genji.mover.body = None
        for _ in range(30):
            run.step({})
        script = genji.body_script
        weapon = next(i for i in script.component.instances.values() if i.graph.index == 0x0304)
        blade = next(i for i in script.component.instances.values() if i.graph.index == 0x0317)
        script.set_ult_charge(1932)
        heading = aim(fight._eye(genji), (x, y + 1, z))
        pool = fight.vitals[owner.body]
        run.step({genji: (*heading, 32)})
        self.assertEqual(script.component.entity_var(342).value(), 0)
        cast = run.frame
        for offset, health in ((85, 140), (145, 30)):
            while run.frame < cast + offset - 1:
                run.step({genji: (*heading, 0)})
            run.step({genji: (*heading, 1)})
            for _ in range(30):
                run.step({genji: (*heading, 0)})
            self.assertEqual(pool.total, health)
            self.assertFalse(fight.rockets)
            self.assertEqual(weapon.vars[53].value(), 30)
        while run.frame < cast + 469:
            run.step({genji: (*heading, 0)})
        self.assertEqual(blade.vars[215].value(), 0)
        self.assertTrue(blade.node_state(blade.graph.node(18)).active)
        run.step({genji: (*heading, 1)})
        self.assertFalse(fight.rockets)
        self.assertEqual(weapon.vars[53].value(), 30)
        while run.frame < cast + 549:
            run.step({genji: (*heading, 0)})
        self.assertFalse(blade.node_state(blade.graph.node(18)).active)
        self.assertEqual(pool.total, 30)
        run.step({genji: (*heading, 1)})
        shuriken, = fight.rockets
        self.assertEqual(shuriken.definition, 0x04000000000007CA)
        self.assertEqual(shuriken.direct.amount, 29)
        for _ in range(25):
            run.step({genji: (*heading, 0)})
        self.assertTrue(pool.dead)
        self.assertEqual(pool.killer, genji.body)
        self.assertEqual(weapon.vars[53].value(), 27)

    def test_actual_dragonblade_slash_hits_reaper_and_wraith_rejects_it(self):
        from test_game_mover import wall

        from ow174.game.collision import World

        for case, distance, damage in (
            ("front", 2, 110), ("wraith", 2, 0), ("edge", 5.3, 110), ("far", 5.7, 0),
            ("rear", 2, 0), ("ally", 2, 0), ("wall", 2, 0),
        ):
            with self.subTest(case=case):
                run, owner, genji, _ = reaper_tests.ReaperTests().scenario()
                self.assertTrue(run.match.switch_hero(genji, heroes()[0x02E0000000000029]))
                fight = run.match.combat
                x, y, z = owner.mover.position
                genji.mover.position[:] = (x, y, z + distance)
                genji.mover.body = None
                if case == "ally":
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    genji.team = owner.team = 0
                if case == "wall":
                    floor = [(x-20, y, z-20, x+20, y, z-20, x+20, y, z+20),
                             (x-20, y, z-20, x+20, y, z+20, x-20, y, z+20)]
                    run.match.collision_world = World(floor + wall(x-5, z+1, x+5, z+1, y0=y))
                for _ in range(30):
                    run.step({})
                script = genji.body_script
                self.assertEqual(script.component.entity_var(ULT_COST).value(), 1932)
                script.set_ult_charge(1932)
                heading = aim(fight._eye(genji), (x, y + 1, z))
                if case == "rear":
                    heading = (0, heading[1])
                run.step({genji: (*heading, 32)})
                for _ in range(84):
                    run.step({})
                if case == "wraith":
                    run.step({owner: (0, 0, 8)})
                    self.assertTrue(reaper.intangible(owner))
                with patch.object(fight, "dragonblade_damage", wraps=fight.dragonblade_damage) as slash:
                    run.step({genji: (*heading, 1)})
                    for _ in range(30):
                        run.step({genji: (*heading, 0)})
                self.assertEqual(sum(call.args[1] == 0 for call in slash.call_args_list), 1)
                self.assertFalse(fight.rockets)
                self.assertEqual(fight.vitals[owner.body].total, 250 - damage)
                self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), damage)

    def test_actual_genji_fan_reflects_three_shuriken_into_reaper(self):
        run, owner, defender, _ = reaper_tests.ReaperTests().scenario(
            names=("Reaper", "Deflecting Genji", "Throwing Genji")
        )
        shooter = run.players[2]
        for genji in (defender, shooter):
            self.assertTrue(run.match.switch_hero(genji, heroes()[0x02E0000000000029]))
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        owner.team = shooter.team = 0
        defender.team = 1
        fight = run.match.combat
        x, y, z = owner.mover.position
        defender.mover.position[:] = (x, y, z + 3)
        shooter.mover.position[:] = (x, y, z - 1)
        defender.mover.body = shooter.mover.body = None
        for _ in range(30):
            run.step({})
        guard = aim(fight._eye(defender), (x, y + 0.8, z))
        for frame in range(12):
            run.step({defender: (*guard, 16 if frame == 0 else 0)})
        heading = aim(fight._eye(shooter), (x, y + 1.1, z + 3))
        run.step({shooter: (*heading, 2), defender: (*guard, 0)})
        self.assertEqual(len(fight.rockets), 3)
        for _ in range(8):
            run.step({defender: (*guard, 0)})
            if all(shot.reflected for shot in fight.rockets):
                break
        self.assertEqual(len(fight.rockets), 3)
        self.assertTrue(all(shot.reflected and shot.shooter is defender and shot.team == 1
                            for shot in fight.rockets))
        self.assertEqual({shot.definition for shot in fight.rockets}, {0x04000000000007CA})
        self.assertEqual({shot.direct.amount for shot in fight.rockets}, {29})
        for _ in range(15):
            run.step({defender: (*guard, 0)})
        self.assertFalse(fight.rockets)
        self.assertEqual(fight.vitals[owner.body].total, 163)
        for genji in (defender, shooter):
            target = next(t for t in fight._targets() if t.owner is genji)
            self.assertEqual(fight.vitals_of(target).total, 200)
        self.assertEqual(defender.stats.value(stats.HERO_DAMAGE), 87)
        self.assertEqual(shooter.stats.value(stats.HERO_DAMAGE), 0)

    def reflected_fan_projection_trace(self, *, case, skipped):
        run, owner, reflector, _ = reaper_tests.ReaperTests().scenario(
            names=("Reaper", "Deflecting Genji", "Throwing Genji", "Zarya"),
        )
        thrower, zarya = run.players[2:4]
        for genji in (reflector, thrower):
            self.assertTrue(run.match.switch_hero(genji, heroes()[0x02E0000000000029]))
        self.assertTrue(run.match.switch_hero(zarya, find_hero("Zarya")))
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        owner.team = thrower.team = zarya.team = 0
        reflector.team = 1
        fight = run.match.combat
        x, y, z = owner.mover.position
        reflector.mover.position[:] = (x, y, z + 3)
        thrower.mover.position[:] = (x, y, z - 1)
        zarya.mover.position[:] = (x + 7, y, z)
        reflector.mover.body = thrower.mover.body = zarya.mover.body = None
        for _ in range(30):
            run.step({})
        guard = aim(fight._eye(reflector), (x, y + 0.8, z))
        projection = aim(fight._eye(zarya), fight._eye(owner))
        run.step({reflector: (*guard, 16), zarya: (*projection, 16)})
        for _ in range(11):
            run.step({reflector: (*guard, 0)})
        bubble = fight.zarya_bubbles.for_target(owner)
        self.assertIsNotNone(bubble)
        self.assertEqual(bubble.health, 200)
        bubble.health = 87 if case == "absorbed" else 40
        health = fight.vitals[owner.body]
        if case != "survives":
            health.damage(249)
        script = owner.body_script
        script.ult_seconds = 0
        charge = script.component.entity_var(ULT_CHARGE)
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        heading = aim(fight._eye(thrower), (x, y + 1.1, z + 3))
        run.step({thrower: (*heading, 2), reflector: (*guard, 0), owner: (0, 0, 32)})
        self.assertIsNotNone(reaper.blossom_state(script))
        self.assertEqual(charge.value(), 0)
        self.assertEqual(len(fight.rockets), 3)
        for _ in range(8):
            run.step({reflector: (*guard, 0)})
            if all(shot.reflected for shot in fight.rockets):
                break
        self.assertEqual(len(fight.rockets), 3)
        self.assertTrue(all(shot.reflected and shot.shooter is reflector for shot in fight.rockets))
        self.assertEqual({shot.definition for shot in fight.rockets}, {0x04000000000007CA})
        self.assertEqual({shot.direct.amount for shot in fight.rockets}, {29})
        frame = owner.commands.last_frame
        contacts = []
        apply_damage = fight._damage
        damage_shield = fight.zarya_bubbles.damage

        def observe(shooter, target, amount, *args, **kwargs):
            shield_before, body_before = bubble.health, health.total
            result = apply_damage(shooter, target, amount, *args, **kwargs)
            if shooter is reflector and target.owner is owner:
                contacts.append((fight.tick, amount, shield_before, bubble.health, body_before, health.total))
            return result

        def observe_shield(target, amount, shooter):
            shield_before, body_before = target.health, health.total
            result = damage_shield(target, amount, shooter)
            if target is bubble and shooter is reflector:
                contacts.append((fight.tick, amount, shield_before, target.health, body_before, health.total))
            return result

        end = run.frame + 205
        with patch.object(fight, "_damage", side_effect=observe), patch.object(
            fight.zarya_bubbles, "damage", side_effect=observe_shield,
        ):
            while run.frame < end:
                run.frame = end if skipped else run.frame + 1
                run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        self.assertEqual(len(contacts), 3)
        self.assertEqual([event[1] for event in contacts], [29] * 3)
        shield_trace = [(event[2], event[3]) for event in contacts]
        expected_shield = [(87, 58), (58, 29), (29, 0)] if case == "absorbed" else [(40, 11), (11, 0), (0, 0)]
        self.assertEqual(shield_trace, expected_shield)
        self.assertEqual([before - after for *_, before, after in contacts[:2]], [0, 0])
        tick, _, _, _, before, after = contacts[-1]
        expected_damage = 0 if case == "absorbed" else min(before, 29)
        self.assertAlmostEqual(before - after, expected_damage)
        self.assertAlmostEqual(reflector.stats.value(stats.HERO_DAMAGE), expected_damage)
        self.assertEqual(thrower.stats.value(stats.HERO_DAMAGE), 0)
        later = [event["+0x84"] for event in owner.client.messages(combat.HIT) if event["+0x84"] > tick]
        self.assertEqual(health.dead, case == "lethal")
        if case == "lethal":
            self.assertEqual((health.death_tick, health.killer), (tick, reflector.body))
            self.assertEqual(later, [])
        else:
            self.assertTrue(later)
        self.assertEqual(owner.commands.last_frame, frame)
        self.assertFalse(fight.rockets)
        self.assertIsNone(reaper.blossom_state(script))
        self.assertEqual(charge.value(), 0)
        return contacts, later, health.total

    def test_actual_reflected_fan_breaks_projection_during_blossom(self):
        for case in ("survives", "lethal", "absorbed"):
            with self.subTest(case=case):
                stepped = self.reflected_fan_projection_trace(case=case, skipped=False)
                skipped = self.reflected_fan_projection_trace(case=case, skipped=True)
                self.assertEqual(stepped, skipped)

    def test_actual_reaper_shell_kills_genji_mid_dash_before_contact(self):
        run, owner, genji, _ = reaper_tests.ReaperTests().scenario()
        self.assertTrue(run.match.switch_hero(genji, heroes()[0x02E0000000000029]))
        fight = run.match.combat
        x, y, z = owner.mover.position
        genji.mover.position[:] = (x, y, z - 4)
        genji.mover.body = None
        for _ in range(30):
            run.step({})
        own = fight.vitals[owner.body]
        own.damage(100)
        victim = fight.vitals_of(next(t for t in fight._targets() if t.owner is genji))
        victim.damage(160)
        run.step({genji: (0, 0, 8)})
        dash = next(i for i in genji.body_script.component.instances.values() if i.graph.index == 0x01DB)
        start = genji.mover.position[2]
        run.step({})
        self.assertTrue(dash.state(1).active)
        self.assertGreater(genji.mover.position[2], start)
        self.assertEqual(own.total, 150)
        reaper_tests.ReaperTests().fire(run, owner, genji)
        self.assertTrue(victim.dead)
        self.assertEqual(victim.killer, owner.body)
        self.assertAlmostEqual(own.total, 164, places=4)
        stopped = tuple(genji.mover.position)
        for _ in range(32):
            run.step({})
        self.assertEqual(tuple(genji.mover.position), stopped)
        self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), 0)
        self.assertAlmostEqual(own.total, 164, places=4)

    def test_actual_swift_strike_only_ends_blossom_on_lethal_hit(self):
        for lethal in (False, True):
            with self.subTest(lethal=lethal):
                run, owner, genji, _ = reaper_tests.ReaperTests().scenario(
                    names=("Reaper", "Genji", "Bystander")
                )
                bystander = run.players[2]
                self.assertTrue(run.match.switch_hero(genji, heroes()[0x02E0000000000029]))
                fight = run.match.combat
                x, y, z = owner.mover.position
                genji.mover.position[:] = (x, y, z - 4)
                bystander.mover.position[:] = (x + 5, y, z)
                genji.mover.body = bystander.mover.body = None
                for _ in range(30):
                    run.step({})
                own = fight.vitals[owner.body]
                own.damage(240 if lethal else 50)
                enemy = fight.vitals_of(next(t for t in fight._targets() if t.owner is bystander))
                initial = enemy.total
                owner.body_script.set_ult_charge(2100)
                run.step({owner: (0, 0, 32), genji: (0, 0, 8)})
                channel = fight.reaper_blossoms[owner.body]
                self.assertEqual(enemy.total, initial - 34)
                self.assertFalse(channel["ended"])
                before_dash = own.total
                for _ in range(10):
                    run.step({})
                    if genji.stats.value(stats.HERO_DAMAGE):
                        break
                self.assertAlmostEqual(genji.stats.value(stats.HERO_DAMAGE), min(before_dash, 50), places=4)
                self.assertEqual(own.dead, lethal)
                if lethal:
                    self.assertEqual(own.killer, genji.body)
                self.assertEqual(owner.body_script.component.vars[342].value(), 0)
                for _ in range(25):
                    run.step({})
                self.assertEqual(channel["ended"], lethal)
                if lethal:
                    self.assertEqual(enemy.total, initial - 34)
                    run.frame += 200
                    run.match.update(run.frame * combat.TICK, run.frame, frames=False)
                    self.assertEqual(enemy.total, initial - 34)
                else:
                    self.assertLess(enemy.total, initial - 34)

    def test_actual_swift_strike_respects_shadow_immunity_without_canceling_cast(self):
        fixture = control_tests.ReaperControlTests()
        for immune in (False, True):
            with self.subTest(immune=immune):
                run, owner, genji, _ = reaper_tests.ReaperTests().scenario()
                self.assertTrue(run.match.switch_hero(genji, heroes()[0x02E0000000000029]))
                fight = run.match.combat
                x, y, z = owner.mover.position
                genji.mover.position[:] = (x, y, z - 4)
                genji.mover.body = None
                for _ in range(30):
                    run.step({})
                record = fixture.start_shadow(run, owner)
                destination = record["target"]
                if immune:
                    for _ in range(64):
                        run.step({})
                self.assertEqual(reaper.intangible(owner), immune)
                run.step({genji: (0, 0, 8)})
                for _ in range(12):
                    run.step({})
                self.assertGreater(genji.mover.position[2], z)
                self.assertEqual(fight.vitals[owner.body].total, 250 if immune else 200)
                self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), 0 if immune else 50)
                self.assertTrue(fixture.shadow(owner).state(32).active)
                self.assertEqual(fixture.shadow(owner).vars[61].value(), 0)
                for _ in range(135):
                    run.step({})
                for actual, expected in zip(owner.mover.position, destination, strict=True):
                    self.assertAlmostEqual(actual, expected, delta=0.1)
                self.assertGreater(fixture.shadow(owner).vars[61].value(), 8)

    def projected_barrier_against_genji(self, *, breaks, distance):
        run, owner, genji, _ = reaper_tests.ReaperTests().scenario(
            names=("Reaper", "Genji", "Zarya", "Soldier")
        )
        zarya, soldier = run.players[2:]
        for actor, hero in ((genji, 0x02E0000000000029), (zarya, zarya_barriers.HERO),
                            (soldier, SOLDIER)):
            self.assertTrue(run.match.switch_hero(actor, heroes()[hero]))
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        owner.team = zarya.team = 0
        genji.team = soldier.team = 1
        fight = run.match.combat
        x, y, z = owner.mover.position
        genji.mover.position[:] = (x, y, z - distance)
        zarya.mover.position[:] = (x + 4, y, z)
        soldier.mover.position[:] = (x, y, z + 6)
        for actor in (genji, zarya, soldier):
            actor.mover.body = None
        for _ in range(30):
            run.step({})
        guard = aim(fight._eye(zarya), (x, y + 1, z))
        run.step({zarya: (*guard, 16)})
        bubble = fight.zarya_bubbles.for_target(owner)
        self.assertIsNotNone(bubble)
        self.assertEqual(bubble.health, 200)
        if breaks:
            heading = aim(fight._eye(soldier), (x, y + 1, z))
            for _ in range(80):
                run.step({soldier: (*heading, 1)})
                if bubble.health <= 40:
                    break
            self.assertEqual(bubble.health, 40)
        return run, owner, genji, bubble

    def test_actual_swift_strike_spends_projected_barrier_without_body_damage(self):
        for breaks in (False, True):
            with self.subTest(breaks=breaks):
                run, owner, genji, bubble = self.projected_barrier_against_genji(breaks=breaks, distance=4)
                fight = run.match.combat
                z = owner.mover.position[2]
                run.step({genji: (0, 0, 8)})
                for _ in range(26):
                    run.step({})
                self.assertGreater(genji.mover.position[2], z)
                self.assertEqual(bubble.health, 0 if breaks else 150)
                self.assertEqual(fight.vitals[owner.body].total, 250)
                self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), 0)
                self.assertEqual(fight.zarya_bubbles.for_target(owner) is None, breaks)

    def test_actual_genji_fan_breaks_barrier_before_last_projectile_hits(self):
        for breaks in (False, True):
            with self.subTest(breaks=breaks):
                run, owner, genji, bubble = self.projected_barrier_against_genji(breaks=breaks, distance=1)
                fight = run.match.combat
                x, y, z = owner.mover.position
                heading = aim(fight._eye(genji), (x, y + 0.8, z))
                run.step({genji: (*heading, 2)})
                self.assertEqual(len(fight.rockets), 3)
                for _ in range(20):
                    run.step({})
                self.assertEqual(bubble.health, 0 if breaks else 113)
                self.assertEqual(fight.vitals[owner.body].total, 221 if breaks else 250)
                self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), 29 if breaks else 0)
                self.assertFalse(fight.rockets)

    def test_actual_swift_strike_elimination_resets_cooldown_for_second_reaper(self):
        for lethal in (False, True):
            with self.subTest(lethal=lethal):
                run, owner, genji, _ = reaper_tests.ReaperTests().scenario(
                    names=("Reaper", "Genji", "Second Reaper")
                )
                second = run.players[2]
                self.assertTrue(run.match.switch_hero(genji, heroes()[0x02E0000000000029]))
                self.assertTrue(run.match.switch_hero(second, heroes()[reaper.HERO]))
                fight = run.match.combat
                x, y, z = owner.mover.position
                genji.mover.position[:] = (x, y, z - 4)
                second.mover.position[:] = (x + 6, y, z)
                genji.mover.body = second.mover.body = None
                for _ in range(30):
                    run.step({})
                pool = fight.vitals[owner.body]
                if lethal:
                    pool.damage(210)
                dash = next(i for i in genji.body_script.component.instances.values()
                            if i.graph.index == 0x01DB)
                run.step({genji: (0, 0, 8)})
                for _ in range(26):
                    run.step({})
                self.assertEqual(pool.dead, lethal)
                if lethal:
                    self.assertEqual(pool.killer, genji.body)
                    self.assertEqual(dash.vars[61].value(), 0)
                else:
                    self.assertEqual(pool.total, 200)
                    self.assertGreater(dash.vars[61].value(), 7)
                gx, gy, gz = genji.mover.position
                second.mover.position[:] = (gx, gy, gz - 3)
                second.mover.body = None
                counter = dash.state(1).counter
                run.step({genji: (-32768, 0, 8)})
                for _ in range(26):
                    run.step({})
                self.assertEqual(dash.state(1).counter, counter + int(lethal))
                target = next(t for t in fight._targets() if t.owner is second)
                self.assertEqual(fight.vitals_of(target).total, 200 if lethal else 250)

    def test_actual_swift_strike_hits_once_and_respects_wraith_team_and_wall(self):
        from test_game_mover import wall

        from ow174.game.collision import World

        for case in ("hit", "wraith", "ally", "wall"):
            with self.subTest(case=case):
                run, owner, genji, _ = reaper_tests.ReaperTests().scenario()
                self.assertTrue(run.match.switch_hero(genji, heroes()[0x02E0000000000029]))
                fight = run.match.combat
                x, y, z = owner.mover.position
                genji.mover.position[:] = (x, y, z - 4)
                genji.mover.body = None
                if case == "ally":
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    genji.team = owner.team = 0
                if case == "wall":
                    floor = [(x-20, y, z-20, x+20, y, z-20, x+20, y, z+20),
                             (x-20, y, z-20, x+20, y, z+20, x-20, y, z+20)]
                    run.match.collision_world = World(floor + wall(x-5, z-1, x+5, z-1, y0=y))
                for _ in range(30):
                    run.step({})
                if case == "wraith":
                    run.step({owner: (0, 0, 8)})
                    self.assertTrue(reaper.intangible(owner))
                run.step({genji: (0, 0, 8)})
                for _ in range(26):
                    run.step({})
                expected = 50 if case == "hit" else 0
                self.assertEqual(fight.vitals[owner.body].total, 250 - expected)
                self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), expected)
                if case == "wall":
                    self.assertLess(genji.mover.position[2], z - 1)
                else:
                    self.assertGreater(genji.mover.position[2], z)
                if case == "hit":
                    enemy = fight.vitals_of(next(t for t in fight._targets() if t.owner is genji))
                    reaper_tests.ReaperTests().fire(run, owner, genji)
                    dealt = 200 - enemy.total
                    self.assertGreater(dealt, 0)
                    self.assertAlmostEqual(fight.vitals[owner.body].total, 200 + dealt * 0.35, places=4)

    def test_actual_genji_shuriken_wraith_immunity_and_reaper_return_fire(self):
        run, owner, genji, _ = reaper_tests.ReaperTests().scenario()
        self.assertTrue(run.match.switch_hero(genji, heroes()[0x02E0000000000029]))
        x, y, z = owner.mover.position
        genji.mover.position[:] = (x, y, z + 3)
        genji.mover.body = None
        for _ in range(30):
            run.step({})
        fight = run.match.combat
        own = fight.vitals[owner.body]
        victim = next(t for t in fight._targets() if t.owner is genji)
        heading = aim(fight._eye(genji), (x, y + 0.8, z))
        run.step({genji: (*heading, 1)})
        shuriken, = fight.rockets
        self.assertEqual(shuriken.definition, 0x04000000000007CA)
        self.assertEqual(shuriken.direct.amount, 29)
        run.step({owner: (0, 0, 8), genji: (*heading, 0)})
        self.assertTrue(reaper.intangible(owner))
        for _ in range(30):
            run.step({genji: (*heading, 0)})
        self.assertFalse(fight.rockets)
        self.assertEqual(own.total, 250)
        self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), 0)
        for _ in range(210):
            run.step({})
        self.assertFalse(reaper.intangible(owner))
        run.step({genji: (*heading, 1)})
        for _ in range(30):
            run.step({genji: (*heading, 0)})
        self.assertEqual(own.total, 163)
        self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), 87)
        reaper_tests.ReaperTests().fire(run, owner, genji)
        dealt = 200 - fight.vitals_of(victim).total
        self.assertGreater(dealt, 0)
        self.assertAlmostEqual(own.total, 163 + dealt * 0.35, places=4)

    def test_actual_genji_secondary_fan_spreads_and_wraith_rejects_all_three(self):
        for distance, hits in ((1, 3), (5, 1)):
            with self.subTest(distance=distance):
                run, owner, genji, _ = reaper_tests.ReaperTests().scenario()
                self.assertTrue(run.match.switch_hero(genji, heroes()[0x02E0000000000029]))
                fight = run.match.combat
                x, y, z = owner.mover.position
                genji.mover.position[:] = (x, y, z + distance)
                genji.mover.body = None
                for _ in range(30):
                    run.step({})
                heading = aim(fight._eye(genji), (x, y + 0.8, z))
                run.step({owner: (0, 0, 8), genji: (*heading, 2)})
                self.assertTrue(reaper.intangible(owner))
                self.assertEqual(len(fight.rockets), 3)
                self.assertEqual({shot.definition for shot in fight.rockets}, {0x04000000000007CA})
                self.assertEqual({shot.direct.amount for shot in fight.rockets}, {29})
                self.assertEqual(len({shot.direction for shot in fight.rockets}), 3)
                for _ in range(220):
                    run.step({})
                self.assertFalse(reaper.intangible(owner))
                self.assertEqual(fight.vitals[owner.body].total, 250)
                self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), 0)
                run.step({genji: (*heading, 2)})
                for _ in range(30):
                    run.step({})
                self.assertEqual(fight.vitals[owner.body].total, 250 - 29 * hits)
                self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), 29 * hits)

    def test_actual_blossom_damages_body_through_deflect_and_grasp(self):
        for hero, graph, state, button, maximum in (
            (0x02E0000000000029, 0x03A3, 10, 16, 200),
            (sigma_grasp.HERO, sigma_grasp.GRASP, 1, 8, 400),
        ):
            with self.subTest(hero=hero):
                run, owner, defender, _ = reaper_tests.ReaperTests().scenario()
                position = tuple(defender.mover.position)
                self.assertTrue(run.match.switch_hero(defender, heroes()[hero]))
                defender.mover.position[:] = position
                defender.mover.body = None
                for frame in range(12):
                    run.step({defender: (-32768, 0, button if frame == 0 else 0)})
                fight = run.match.combat
                defense = next(i for i in defender.body_script.component.instances.values()
                               if i.graph.index == graph)
                self.assertTrue(defense.states[state].active)
                pool = fight.vitals_of(next(t for t in fight._targets() if t.owner is defender))
                fight.vitals[owner.body].damage(100)
                owner.body_script.set_ult_charge(2100)
                run.step({owner: (0, 0, 32), defender: (-32768, 0, 0)})
                self.assertTrue(defense.states[state].active)
                self.assertEqual(pool.total, maximum - 34)
                self.assertAlmostEqual(fight.vitals[owner.body].total, 150 + 34 * 0.35, places=4)
                if hero == sigma_grasp.HERO:
                    self.assertEqual(defense.vars[21312].value(), 0)

    def reflected_shell_blossom_trace(self, *, lethal, skipped):
        run, owner, genji, _ = reaper_tests.ReaperTests().scenario(names=("Reaper", "Genji", "Channeler"))
        channeler = run.players[2]
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        owner.team = channeler.team = 0
        genji.team = 1
        self.assertTrue(run.match.switch_hero(genji, heroes()[0x02E0000000000029]))
        self.assertTrue(run.match.switch_hero(channeler, heroes()[reaper.HERO]))
        x, y, z = owner.mover.position
        genji.mover.position[:] = (x, y, z + 4)
        channeler.mover.position[:] = (x + 2.9, y, z)
        genji.mover.body = channeler.mover.body = None
        fight = run.match.combat
        facing = aim(fight._eye(genji), (x + 2.9, y + 1, z))
        for frame in range(30):
            run.step({genji: (*facing, 16 if frame == 18 else 0)})
        deflect = next(i for i in genji.body_script.component.instances.values() if i.graph.index == 0x03A3)
        self.assertTrue(deflect.states[10].active)
        victim = fight.vitals_of(next(t for t in fight._targets() if t.owner is channeler))
        defender = fight.vitals_of(next(t for t in fight._targets() if t.owner is genji))
        own = fight.vitals[owner.body]
        own.damage(100)
        for actor in (owner, channeler, genji):
            actor.body_script.ult_seconds = 0
            actor.body_script.set_ult_charge(2100 if actor is channeler else 0)
        pre_hit = 119 if lethal else 121
        victim.damage(victim.total - pre_hit + reaper.BLOSSOM_DAMAGE * reaper.REAPING_RATIO)
        run.step({channeler: (0, 0, 32), genji: (*facing, 0)})
        self.assertIsNotNone(reaper.blossom_state(channeler.body_script))
        self.assertAlmostEqual(victim.total, pre_hit, places=4)
        self.assertEqual(defender.total, 166)
        channel = fight.reaper_blossoms[channeler.body]
        heading = aim(fight._eye(owner), (x, y + 1, z + 4))
        with patch.object(fight, "_hitscan", wraps=fight._hitscan) as rays:
            run.step({owner: (*heading, 1), genji: (*facing, 0)})
        returns = [ray for ray in rays.call_args_list if ray.kwargs.get("reflected")]
        self.assertEqual(len(returns), 20)
        self.assertTrue(all(ray.args[0] is genji for ray in returns))
        self.assertEqual(victim.dead, lethal)
        self.assertAlmostEqual(victim.total, 0 if lethal else 1, places=4)
        if lethal:
            self.assertNotIn(channeler.body, fight.reaper_blossoms)
        else:
            self.assertIs(fight.reaper_blossoms[channeler.body], channel)
            self.assertFalse(channel["ended"])
        self.assertAlmostEqual(genji.stats.value(stats.HERO_DAMAGE), 119 if lethal else 120, places=4)
        self.assertEqual(own.total, 150)
        self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 0)
        self.assertEqual(owner.body_script.component.entity_var(ULT_CHARGE).value(), 0)
        shot = run.frame
        end = shot + 205
        for tick in (end,) if skipped else range(shot + 1, end + 1):
            run.match.update(tick * combat.TICK, tick, frames=False)
        self.assertIsNone(reaper.blossom_state(channeler.body_script))
        if not lethal:
            self.assertTrue(channel["ended"])
        self.assertEqual(channeler.body_script.component.entity_var(ULT_CHARGE).value(), 0)
        if lethal:
            self.assertEqual((victim.death_tick, victim.killer), (shot, genji.body))
            self.assertEqual(len(channeler.client.messages(combat.DEATH_NOTICE)), 1)
            self.assertEqual(defender.total, 166)
        else:
            self.assertTrue(defender.dead)
            self.assertEqual(defender.killer, channeler.body)
            self.assertEqual(len(genji.client.messages(combat.DEATH_NOTICE)), 1)
            self.assertAlmostEqual(victim.total, 1 + 166 * reaper.REAPING_RATIO, places=4)
        return victim.death_tick, defender.death_tick, victim.total, defender.total

    def test_actual_reflected_reaper_shell_ends_blossom_only_on_lethal_return(self):
        for lethal in (False, True):
            outcomes = []
            for skipped in (False, True):
                with self.subTest(lethal=lethal, skipped=skipped):
                    outcomes.append(self.reflected_shell_blossom_trace(lethal=lethal, skipped=skipped))
            if len(outcomes) == 2:
                self.assertEqual(outcomes[0], outcomes[1])

    def test_actual_helix_reflects_in_flight_with_genji_attribution(self):
        for retired in (False, True):
            with self.subTest(retired=retired):
                run, shooter, defender, _ = reaper_tests.ReaperTests().scenario()
                for actor, hero in ((shooter, SOLDIER), (defender, 0x02E0000000000029)):
                    position = tuple(actor.mover.position)
                    self.assertTrue(run.match.switch_hero(actor, heroes()[hero]))
                    actor.mover.position[:] = position
                    actor.mover.body = None
                x, y, z = shooter.mover.position
                defender.mover.position[:] = (x, y, z + 5)
                for frame in range(12):
                    run.step({defender: (-32768, 1000, 16 if frame == 0 else 0)})
                fight = run.match.combat
                target = next(t for t in fight._targets() if t.owner is defender)
                yaw, pitch = aim(fight._eye(shooter), (x, y + 1, z + 5))
                run.step({shooter: (yaw, pitch, 2), defender: (-32768, 1000, 0)})
                original, = fight.rockets
                self.assertIs(original.shooter, shooter)
                flight_budget = original.left + original.flown
                for _ in range(20):
                    run.step({defender: (-32768, 1000, 0)})
                    if any(rocket.reflected for rocket in fight.rockets):
                        break
                returned, = fight.rockets
                self.assertTrue(returned.reflected)
                self.assertIs(returned.shooter, defender)
                self.assertAlmostEqual(returned.left + returned.flown, flight_budget)
                if retired:
                    self.assertTrue(run.match.switch_hero(defender, heroes()[SOLDIER]))
                    self.assertFalse(fight.rockets)
                for _ in range(20):
                    run.step({})
                self.assertFalse(fight.rockets)
                if not retired:
                    self.assertEqual(fight.vitals_of(target).total, 200)
                source = next(t for t in fight._targets() if t.owner is shooter)
                self.assertEqual(fight.vitals_of(source).total, 200 if retired else 80)
                self.assertEqual(defender.stats.value(stats.HERO_DAMAGE), 0 if retired else 120)

    def helix_deflect_expiry_scene(self):
        run, owner, shooter, _ = reaper_tests.ReaperTests().scenario(names=("Reaper", "Soldier", "Genji"))
        genji = run.players[2]
        self.assertTrue(run.match.switch_hero(genji, heroes()[0x02E0000000000029]))
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        owner.team = shooter.team = 0
        genji.team = 1
        x, y, z = owner.mover.position
        shooter.mover.position[:] = (x + 3, y, z + 4)
        genji.mover.position[:] = (x, y, z + 10)
        shooter.mover.body = genji.mover.body = None
        run.step({genji: (-32768, 1000, 16)})
        state = next(i.states[10] for i in genji.body_script.component.instances.values()
                     if i.graph.index == 0x03A3)
        self.assertTrue(state.active)
        heading = aim(run.match.combat._eye(shooter), (x, y + 1, z + 10))
        return run, owner, shooter, genji, state, heading

    def test_actual_helix_resolves_deflect_expiry_before_reaching_reaper(self):
        guard = (-32768, 1000, 0)

        def advance(run, tick):
            run.frame = tick
            run.match.update(tick * combat.TICK, tick, frames=False)

        run, _, shooter, genji, state, heading = self.helix_deflect_expiry_scene()
        fight = run.match.combat
        for _ in range(12):
            run.step({genji: guard})
        run.step({shooter: (*heading, 2), genji: guard})
        fired = run.frame
        for _ in range(20):
            advance(run, run.frame + 1)
            if any(rocket.reflected for rocket in fight.rockets):
                break
        self.assertTrue(any(rocket.reflected for rocket in fight.rockets))
        travel = run.frame - fired
        for _ in range(140):
            advance(run, run.frame + 1)
            if not state.active:
                break
        self.assertFalse(state.active)
        expiry = run.frame

        for reflected in (True, False):
            outcomes = []
            for skipped in (False, True):
                with self.subTest(reflected=reflected, skipped=skipped):
                    run, owner, shooter, genji, state, heading = self.helix_deflect_expiry_scene()
                    fight = run.match.combat
                    contact = expiry - int(reflected)
                    while run.frame < contact - travel - 1:
                        run.step({genji: guard})
                    run.step({shooter: (*heading, 2), genji: guard})
                    original, = fight.rockets
                    advance(run, run.frame + 1)
                    self.assertEqual(original.last_tick, run.frame)
                    self.assertTrue(state.active)
                    contacts = []

                    def observe(rocket, *, world=fight, hits=contacts, landed=fight._landed):
                        hits.append((world.tick, rocket.reflected, rocket.shooter.entity))
                        landed(rocket)

                    end = expiry + 40
                    with patch.object(fight, "_landed", side_effect=observe):
                        while run.frame < end:
                            advance(run, end if skipped else run.frame + 1)
                    self.assertFalse(state.active)
                    self.assertFalse(fight.rockets)
                    if reflected:
                        self.assertEqual(len(contacts), 2)
                        self.assertEqual(contacts[0], (contact, False, shooter.entity))
                        self.assertEqual(contacts[1][1:], (True, genji.entity))
                    else:
                        self.assertEqual(contacts, [(expiry, False, shooter.entity)])
                    reaper_health = fight.vitals[owner.body].total
                    self.assertAlmostEqual(reaper_health, 187.5725 if reflected else 250, places=4)
                    self.assertEqual(fight.vitals[genji.body].total, 200 if reflected else 80)
                    self.assertAlmostEqual(genji.stats.value(stats.HERO_DAMAGE), 250 - reaper_health)
                    self.assertEqual(shooter.stats.value(stats.HERO_DAMAGE), 0 if reflected else 120)
                    outcomes.append(contacts)
            if len(outcomes) == 2:
                self.assertEqual(outcomes[0], outcomes[1])

    def test_actual_genji_deflect_frontal_shell_and_back_exposure(self):
        for frontal in (True, False):
            with self.subTest(frontal=frontal):
                run, owner, defender, _ = reaper_tests.ReaperTests().scenario()
                position = tuple(defender.mover.position)
                self.assertTrue(run.match.switch_hero(defender, heroes()[0x02E0000000000029]))
                defender.mover.position[:] = position
                defender.mover.body = None
                heading = -32768 if frontal else 0
                for frame in range(12):
                    run.step({defender: (heading, 2200, 16 if frame == 0 else 0)})
                fight = run.match.combat
                instance = next(i for i in defender.body_script.component.instances.values()
                                if i.graph.index == 0x03A3)
                self.assertTrue(instance.states[10].active)
                target = next(t for t in fight._targets() if t.owner is defender)
                x, y, z = defender.mover.position
                yaw, pitch = aim(fight._eye(owner), (x, y + 1, z))
                run.step({owner: (yaw, pitch, 1), defender: (heading, 2200, 0)})
                self.assertEqual(fight.vitals_of(target).total, 200 if frontal else 80)
                self.assertEqual(fight.vitals[owner.body].total, 130 if frontal else 250)
                if frontal:
                    for _ in range(180):
                        run.step({defender: (heading, 2200, 0)})
                        if not instance.states[10].active:
                            break
                    self.assertFalse(instance.states[10].active)
                    run.step({owner: (yaw, pitch, 1), defender: (heading, 2200, 0)})
                    self.assertEqual(fight.vitals_of(target).total, 80)
                    self.assertAlmostEqual(fight.vitals[owner.body].total, 172, places=4)


class ReaperAbsorptionTests(unittest.TestCase):
    def test_actual_sigma_grasp_converts_all_three_genji_fan_projectiles(self):
        run, owner, genji, _ = reaper_tests.ReaperTests().scenario(names=("Reaper", "Genji", "Sigma"))
        sigma = run.players[2]
        self.assertTrue(run.match.switch_hero(genji, heroes()[0x02E0000000000029]))
        self.assertTrue(run.match.switch_hero(sigma, heroes()[sigma_grasp.HERO]))
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        owner.team = sigma.team = 0
        genji.team = 1
        fight = run.match.combat
        x, y, z = owner.mover.position
        sigma.mover.position[:] = (x, y, z + 1.5)
        genji.mover.position[:] = (x, y, z + 4)
        sigma.mover.body = genji.mover.body = None
        for _ in range(30):
            run.step({})
        for frame in range(12):
            run.step({sigma: (0, 0, 8 if frame == 0 else 0)})
        grasp = next(i for i in sigma.body_script.component.instances.values()
                     if i.graph.index == sigma_grasp.GRASP)
        self.assertTrue(grasp.states[1].active)
        sx, sy, sz = sigma.mover.position
        heading = aim(fight._eye(genji), (sx, sy + 1.25, sz))
        run.step({genji: (*heading, 2)})
        self.assertEqual(len(fight.rockets), 3)
        for _ in range(25):
            run.step({})
        self.assertFalse(fight.rockets)
        self.assertEqual(grasp.vars[21312].value(), 87)
        self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), 0)
        self.assertEqual(fight.vitals[owner.body].total, 250)
        pool = fight.vitals_of(next(t for t in fight._targets() if t.owner is sigma))
        self.assertEqual(pool.total, 400)
        for _ in range(220):
            if sigma.body in fight.grasp_shields:
                break
            run.step({})
        self.assertFalse(grasp.states[1].active)
        self.assertAlmostEqual(pool.temporary_shields[sigma_grasp.SHIELD_TAG].now, 52.2, places=4)

    def reflected_fan_grasp_scene(self):
        run, owner, reflector, _ = reaper_tests.ReaperTests().scenario(
            names=("Reaper", "Deflecting Genji", "Throwing Genji", "Sigma"),
        )
        thrower, sigma = run.players[2:4]
        for genji in (reflector, thrower):
            self.assertTrue(run.match.switch_hero(genji, heroes()[0x02E0000000000029]))
        self.assertTrue(run.match.switch_hero(sigma, heroes()[sigma_grasp.HERO]))
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        owner.team = thrower.team = sigma.team = 0
        reflector.team = 1
        x, y, z = owner.mover.position
        reflector.mover.position[:] = (x, y, z + 3)
        thrower.mover.position[:] = (x, y, z - 1)
        sigma.mover.position[:] = (x + 0.8, y, z + 1.5)
        reflector.mover.body = thrower.mover.body = sigma.mover.body = None
        for _ in range(30):
            run.step({})
        run.step({sigma: (0, 0, 8)})
        self.assertTrue(sigma_grasp.active(sigma))
        return run, owner, reflector, thrower, sigma

    def launch_reflected_fan(self, run, owner, reflector, thrower):
        fight = run.match.combat
        x, y, z = owner.mover.position
        guard = aim(fight._eye(reflector), (x, y + 0.8, z))
        for frame in range(12):
            run.step({reflector: (*guard, 16 if frame == 0 else 0)})
        sx, sy, sz = reflector.mover.position
        heading = aim(fight._eye(thrower), (sx, sy + 1.1, sz))
        run.step({thrower: (*heading, 2), reflector: (*guard, 0)})
        self.assertEqual(len(fight.rockets), 3)
        self.assertEqual({shot.definition for shot in fight.rockets}, {0x04000000000007CA})
        self.assertEqual({shot.direct.amount for shot in fight.rockets}, {29})

    def reflected_fan_grasp_trace(self, travel, expiry, *, protected, skipped):
        run, owner, reflector, thrower, sigma = self.reflected_fan_grasp_scene()
        fight = run.match.combat
        expected = expiry - int(protected)
        while run.frame < expected - travel:
            run.frame += 1
            run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        self.launch_reflected_fan(run, owner, reflector, thrower)
        contacts = []
        absorb = fight._absorb_hit
        damage = fight._damage

        def observe_absorb(state, shooter, amount):
            self.assertIs(shooter, reflector)
            self.assertEqual(state.instance.graph.index, sigma_grasp.GRASP)
            contacts.append((fight.tick, "grasp", amount))
            return absorb(state, shooter, amount)

        def observe_damage(shooter, target, amount, *args, **kwargs):
            self.assertIs(shooter, reflector)
            self.assertIn(target.owner, (owner, sigma))
            contacts.append((fight.tick, "reaper" if target.owner is owner else "sigma", amount))
            return damage(shooter, target, amount, *args, **kwargs)

        last_command = sigma.commands.last_frame
        end = expiry + 120
        with patch.object(fight, "_absorb_hit", side_effect=observe_absorb), patch.object(
            fight, "_damage", side_effect=observe_damage,
        ):
            # Admit the input-created projectiles before skipping world ticks.
            run.frame += 1
            run.match.update(run.frame * combat.TICK, run.frame, frames=False)
            self.assertEqual(len(fight.rockets), 3)
            self.assertTrue(all(shot.last_tick == run.frame for shot in fight.rockets))
            self.assertEqual(contacts, [])
            while run.frame < end:
                run.frame = end if skipped else run.frame + 1
                run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        self.assertFalse(fight.rockets)
        self.assertEqual(contacts, [
            (expected, "grasp" if protected else "sigma", 29),
            (expected + int(not protected), "sigma" if protected else "reaper", 29),
            (expected + 2, "reaper", 29),
        ])
        self.assertEqual(sigma.commands.last_frame, last_command)
        self.assertFalse(sigma_grasp.active(sigma))
        self.assertEqual(fight.vitals[owner.body].total, 221 if protected else 192)
        self.assertEqual(reflector.stats.value(stats.HERO_DAMAGE), 58 if protected else 87)
        self.assertEqual(thrower.stats.value(stats.HERO_DAMAGE), 0)
        sigma_pool = fight.vitals[sigma.body]
        shield = sigma_pool.temporary_shields.get(sigma_grasp.SHIELD_TAG)
        self.assertAlmostEqual(shield.now if shield is not None else 0, 17.4 if protected else 0, places=4)
        self.assertAlmostEqual(sigma_pool.total, 388.4 if protected else 371, places=4)
        return contacts, sigma_pool.total

    def test_actual_reflected_genji_fan_resolves_grasp_expiry(self):
        run, owner, reflector, thrower, sigma = self.reflected_fan_grasp_scene()
        fight = run.match.combat
        start = run.frame
        self.launch_reflected_fan(run, owner, reflector, thrower)
        with patch.object(fight, "_absorb_hit", wraps=fight._absorb_hit) as absorbed:
            for _ in range(30):
                run.frame += 1
                run.match.update(run.frame * combat.TICK, run.frame, frames=False)
                if absorbed.called:
                    break
        self.assertEqual(absorbed.call_count, 1)
        grasp = next(i for i in sigma.body_script.component.instances.values()
                     if i.graph.index == sigma_grasp.GRASP)
        self.assertEqual(grasp.vars[21312].value(), 29)
        travel = run.frame - start
        for _ in range(160):
            if not sigma_grasp.active(sigma):
                break
            run.frame += 1
            run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        self.assertFalse(sigma_grasp.active(sigma))
        expiry = run.frame
        for protected in (False, True):
            with self.subTest(protected=protected):
                stepped = self.reflected_fan_grasp_trace(travel, expiry, protected=protected, skipped=False)
                skipped = self.reflected_fan_grasp_trace(travel, expiry, protected=protected, skipped=True)
                self.assertEqual(stepped, skipped)

    def test_actual_helix_is_absorbed_without_explosive_damage(self):
        for blocked in (False, True):
            with self.subTest(blocked=blocked):
                run, shooter, defender, _ = reaper_tests.ReaperTests().scenario()
                for actor, hero in ((shooter, SOLDIER), (defender, sigma_grasp.HERO)):
                    position = tuple(actor.mover.position)
                    self.assertTrue(run.match.switch_hero(actor, heroes()[hero]))
                    actor.mover.position[:] = position
                    actor.mover.body = None
                from ow174.game.collision import World

                x, y, z = shooter.mover.position
                defender.mover.position[:] = (x, y, z + 5)
                defender.mover.body = None
                if blocked:
                    wall_z = z + 1.5
                    run.match.collision_world = World([
                        (x-10, y-10, wall_z, x+10, y-10, wall_z, x+10, y+10, wall_z),
                        (x-10, y-10, wall_z, x+10, y+10, wall_z, x-10, y+10, wall_z),
                    ], name="wall before grasp volume")
                for frame in range(12):
                    run.step({defender: (-32768, 0, 8 if frame == 0 else 0)})
                fight = run.match.combat
                instance = next(i for i in defender.body_script.component.instances.values()
                                if i.graph.index == sigma_grasp.GRASP)
                self.assertTrue(instance.states[1].active)
                target = next(t for t in fight._targets() if t.owner is defender)
                x, y, z = defender.mover.position
                yaw, pitch = aim(fight._eye(shooter), (x, y + 1, z))
                run.step({shooter: (yaw, pitch, 2), defender: (-32768, 0, 0)})
                self.assertTrue(fight.rockets)
                for _ in range(20):
                    run.step({defender: (-32768, 0, 0)})
                self.assertEqual(instance.vars[21312].value(), 0 if blocked else 120)
                self.assertEqual(fight.vitals_of(target).total, 400)
                self.assertFalse(fight.rockets)

    def test_actual_grasp_absorbs_reflected_helix_before_reaper_splash(self):
        absorption_ticks = []
        for grasp_active, skipped in ((True, False), (True, True), (False, False)):
            with self.subTest(grasp_active=grasp_active, skipped=skipped):
                run, owner, shooter, _ = reaper_tests.ReaperTests().scenario(
                    names=("Reaper", "Soldier", "Genji", "Sigma"),
                )
                genji, sigma = run.players[2:]
                self.assertTrue(run.match.switch_hero(genji, heroes()[0x02E0000000000029]))
                self.assertTrue(run.match.switch_hero(sigma, heroes()[sigma_grasp.HERO]))
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                owner.team = shooter.team = sigma.team = 0
                genji.team = 1
                x, y, z = owner.mover.position
                shooter.mover.position[:] = (x + 3, y, z + 4)
                sigma.mover.position[:] = (x, y, z + 1.5)
                genji.mover.position[:] = (x, y, z + 10)
                shooter.mover.body = sigma.mover.body = genji.mover.body = None
                fight = run.match.combat
                for frame in range(12):
                    run.step({
                        genji: (-32768, 1000, 16 if frame == 0 else 0),
                        sigma: (0, 0, 8 if frame == 0 and grasp_active else 0),
                    })
                grasp = next(i for i in sigma.body_script.component.instances.values()
                             if i.graph.index == sigma_grasp.GRASP)
                self.assertEqual(sigma_grasp.active(sigma), grasp_active)
                heading = aim(fight._eye(shooter), (x, y + 1, z + 10))
                run.step({shooter: (*heading, 2), genji: (-32768, 1000, 0)})
                original, = fight.rockets
                self.assertIs(original.shooter, shooter)
                for _ in range(20):
                    run.step({genji: (-32768, 1000, 0)})
                    if any(rocket.reflected for rocket in fight.rockets):
                        break
                returned, = fight.rockets
                self.assertTrue(returned.reflected)
                self.assertIs(returned.shooter, genji)
                self.assertEqual(returned.definition, original.definition)
                self.assertEqual(returned.last_tick, run.frame)
                impact_ticks = []

                def observe(missile, *, world=fight, hits=impact_ticks, landed=fight._landed):
                    hits.append(world.tick)
                    landed(missile)

                until = run.frame + 40
                with patch.object(fight, "_landed", side_effect=observe):
                    while run.frame < until:
                        run.frame = until if skipped else run.frame + 1
                        run.match.update(run.frame * combat.TICK, run.frame, frames=False)
                self.assertFalse(fight.rockets)
                self.assertEqual(len(impact_ticks), 1)
                reaper_health = fight.vitals[owner.body].total
                sigma_vitals = fight.vitals[sigma.body]
                if not grasp_active:
                    self.assertEqual(sigma_vitals.total, 280)
                    self.assertAlmostEqual(reaper_health, 179.5092, places=4)
                    self.assertGreater(genji.stats.value(stats.HERO_DAMAGE), 120)
                    continue

                absorption_ticks.append(impact_ticks[0])
                self.assertEqual(sigma_vitals.total, 400)
                self.assertEqual(reaper_health, 250)
                self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), 0)
                self.assertFalse(genji.client.messages(combat.HIT))
                self.assertEqual(grasp.vars[21312].value(), 120)
                for _ in range(180):
                    if sigma.body in fight.grasp_shields:
                        break
                    run.step({})
                self.assertFalse(grasp.states[1].active)
                self.assertEqual(sigma_vitals.temporary_shields[sigma_grasp.SHIELD_TAG].now, 72)
        if len(absorption_ticks) == 2:
            self.assertEqual(absorption_ticks[0], absorption_ticks[1])

    def test_actual_grasp_resolves_reflected_reaper_shell_front_and_rear(self):
        for frontal in (True, False):
            with self.subTest(frontal=frontal):
                self.check_reflected_shell_grasp(frontal=frontal)

    def test_actual_reflected_shell_breaks_experimental_barrier_before_later_pellets(self):
        from ow174.game.sigma_barrier import GEOMETRY_SHA256

        if GEOMETRY_SHA256 is None:
            self.skipTest("requires the pinned Sigma C015 collision mesh")
        for frontal, barrier_health in ((True, 700), (True, 12), (False, 12)):
            with self.subTest(frontal=frontal, barrier_health=barrier_health):
                self.check_reflected_shell_grasp(frontal=frontal, barrier_health=barrier_health)

    def check_reflected_shell_grasp(self, *, frontal, barrier_health=0):
        run, owner, genji, _ = reaper_tests.ReaperTests().scenario(names=("Reaper", "Genji", "Sigma"))
        sigma = run.players[2]
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        owner.team = sigma.team = 0
        genji.team = 1
        self.assertTrue(run.match.switch_hero(genji, heroes()[0x02E0000000000029]))
        self.assertTrue(run.match.switch_hero(sigma, heroes()[sigma_grasp.HERO]))
        x, y, z = owner.mover.position
        genji.mover.position[:] = (x, y, z + 4)
        sx, sz = (x + 2.9, z) if barrier_health else (x + 1.45, z + 2)
        sigma.mover.position[:] = (sx, y, sz)
        genji.mover.body = sigma.mover.body = None
        fight = run.match.combat
        genji_aim = aim(fight._eye(genji), (sx, y + 1, sz))
        toward_genji = aim(fight._eye(sigma), (x, y + 1, z + 4))[0]
        sigma_yaw = toward_genji if frontal else genji_aim[0]
        barrier = None
        if barrier_health:
            for _ in range(14):
                run.step({sigma: (toward_genji, 0, 2)})
            barrier = fight.sigma_barriers.actors[sigma.body]
            self.assertEqual((barrier.mode, barrier.health), (2, 700))
            run.step({sigma: (toward_genji, 0, 0)})
            self.assertEqual(barrier.mode, 3)
            if barrier_health < barrier.health:
                barrier.health = barrier_health
        for frame in range(12):
            run.step({
                genji: (*genji_aim, 16 if frame == 0 else 0),
                sigma: (sigma_yaw, 0, 8 if frame == 0 else 0),
            })
        deflect = next(i for i in genji.body_script.component.instances.values()
                       if i.graph.index == 0x03A3)
        grasp = next(i for i in sigma.body_script.component.instances.values()
                     if i.graph.index == sigma_grasp.GRASP)
        self.assertTrue(deflect.states[10].active)
        self.assertTrue(sigma_grasp.active(sigma))
        if barrier is not None:
            origin, direction = fight._eye(genji), combat.aim(*genji_aim)
            contact = barriers.intercept(fight, genji, origin, direction, 20)
            self.assertIsNotNone(contact)
            self.assertIs(contact[1], barrier)
            if frontal:
                absorb = fight._projectile_defense_contact(genji, origin, direction, 20)
                self.assertIsNotNone(absorb)
                self.assertLess(contact[0], absorb[0])
        own = fight.vitals[owner.body]
        own.damage(100)
        genji_pool = fight.vitals_of(next(t for t in fight._targets() if t.owner is genji))
        sigma_pool = fight.vitals_of(next(t for t in fight._targets() if t.owner is sigma))
        owner.body_script.ult_seconds = genji.body_script.ult_seconds = 0
        owner.body_script.set_ult_charge(0)
        genji.body_script.set_ult_charge(0)
        heading = aim(fight._eye(owner), (x, y + 1, z + 4))
        with patch.object(fight, "_hitscan", wraps=fight._hitscan) as rays:
            run.step({owner: (*heading, 1), genji: (*genji_aim, 0), sigma: (sigma_yaw, 0, 0)})
        returns = [ray for ray in rays.call_args_list if ray.kwargs.get("reflected")]
        self.assertEqual(rays.call_count - len(returns), 20)
        reflected_pellets = 20 if barrier is not None else 18
        self.assertEqual(len(returns), reflected_pellets)
        self.assertTrue(all(ray.args[0] is genji for ray in returns))
        direct_damage = (20 - reflected_pellets) * 6
        self.assertAlmostEqual(own.total, 150 + direct_damage * reaper.REAPING_RATIO, places=4)
        self.assertEqual(genji_pool.total, 200 - direct_damage)
        reflected_damage = reflected_pellets * 6
        blocked = min(barrier_health, reflected_damage)
        returned = reflected_damage - blocked
        absorbed, damage = (returned, 0) if frontal else (0, returned)
        if barrier is not None:
            self.assertEqual(barrier.health, barrier_health - blocked)
            self.assertEqual(barrier.active(), barrier_health > blocked)
            if not barrier.active():
                self.assertGreater(barrier.source.vars[61].value(), 4.9)
        self.assertEqual(sigma_pool.total, 400 - damage)
        self.assertEqual(grasp.vars[21312].value(), absorbed)
        self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), direct_damage)
        self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), damage)
        self.assertEqual(owner.body_script.component.entity_var(ULT_CHARGE).value(), direct_damage)
        genji_charge = genji.body_script.component.entity_var(ULT_CHARGE).value()
        if damage == 0:
            self.assertEqual(genji_charge, 0)
        else:
            self.assertGreater(genji_charge, 0)
        for _ in range(150):
            if not sigma_grasp.active(sigma):
                break
            run.step({})
        self.assertFalse(sigma_grasp.active(sigma))
        shield = sigma_pool.temporary_shields.get(sigma_grasp.SHIELD_TAG)
        remaining = shield.now if shield is not None else 0
        self.assertAlmostEqual(remaining, absorbed * 0.6, places=4)

    def test_actual_sigma_absorbs_frontal_shell_and_accumulates_authored_damage(self):
        for frontal in (True, False):
            with self.subTest(frontal=frontal):
                run, owner, defender, _ = reaper_tests.ReaperTests().scenario()
                position = tuple(defender.mover.position)
                self.assertTrue(run.match.switch_hero(defender, heroes()[0x02E000000000023B]))
                defender.mover.position[:] = position
                defender.mover.body = None
                heading = -32768 if frontal else 0
                for frame in range(12):
                    run.step({defender: (heading, 0, 8 if frame == 0 else 0)})
                fight = run.match.combat
                instance = next(i for i in defender.body_script.component.instances.values()
                                if i.graph.index == 0x1D02)
                self.assertTrue(instance.states[1].active)
                target = next(t for t in fight._targets() if t.owner is defender)
                x, y, z = defender.mover.position
                yaw, pitch = aim(fight._eye(owner), (x, y + 1, z))
                fight.vitals[owner.body].damage(100)
                owner.body_script.set_ult_charge(0)
                run.step({owner: (yaw, pitch, 1), defender: (heading, 0, 0)})
                self.assertEqual(fight.vitals_of(target).total, 400 if frontal else 280)
                if frontal:
                    self.assertEqual(instance.vars[21312].value(), 120)
                    self.assertEqual(fight.vitals[owner.body].total, 150)
                    self.assertEqual(owner.body_script.component.vars[342].value(), 0)
                    for _ in range(220):
                        run.step({})
                        if defender.body in fight.grasp_shields:
                            break
                    self.assertFalse(instance.states[1].active)
                    vitals = fight.vitals_of(target)
                    pool = vitals.temporary_shields[sigma_grasp.SHIELD_TAG]
                    self.assertAlmostEqual(pool.now, 72)
                    self.assertAlmostEqual(vitals.total, 472)
                    decay = fight.grasp_shields[defender.body]
                    self.assertEqual(decay.rate, 5)
                    self.assertAlmostEqual(decay.starts - decay.last, 3, delta=combat.TICK)
                    for _ in range(200):
                        if fight.now + combat.TICK >= decay.starts:
                            break
                        run.step({})
                    self.assertGreaterEqual(fight.now + combat.TICK, decay.starts)
                    self.assertAlmostEqual(pool.now, 72)
                    for _ in range(125):
                        run.step({})
                    self.assertAlmostEqual(pool.now, 62, delta=0.081)
                    remaining = pool.now - combat.TICK * decay.rate
                    run.step({owner: (yaw, pitch, 1)})
                    self.assertEqual(pool.now, 0)
                    self.assertAlmostEqual(vitals.now[combat.SHIELDS_POOL], remaining - 20)
                    self.assertAlmostEqual(vitals.total, 280 + remaining)


    def blossom_layered_shields_trace(self, *, sound_last, skipped):
        run, owner, sigma, _ = reaper_tests.ReaperTests().scenario(names=("Reaper", "Sigma", "Lucio"))
        lucio = run.players[2]
        self.assertTrue(run.match.switch_hero(sigma, heroes()[sigma_grasp.HERO]))
        self.assertTrue(run.match.switch_hero(lucio, heroes()[lucio_barrier.HERO]))
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        owner.team, sigma.team, lucio.team = 0, 1, 1
        x, y, z = owner.mover.position
        sigma.mover.position[:] = (x, y, z + 1.5)
        lucio.mover.position[:] = (x + 15, y, z + 1.5)
        sigma.mover.body = lucio.mover.body = None
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is sigma)
        vitals = fight.vitals_of(target)
        vitals.damage(vitals.total - 33)
        own = fight.vitals[owner.body]
        own.damage(own.total - 100)

        def sound():
            control_tests.ReaperControlTests().start_sound_barrier(run, lucio, fight)
            lease = fight.sound_barriers.leases[sigma.body]
            self.assertEqual((lease.capacity, lease.duration), (750, 6))
            self.assertIsNotNone(lease.effect)
            self.assertEqual(lease.effect.graph.index, 0x073B)

        if not sound_last:
            sound()
        for frame in range(12):
            run.step({sigma: (-32768, 0, 8 if frame == 0 else 0)})
        heading = aim(fight._eye(owner), (x, y + 1, z + 1.5))
        run.step({owner: (*heading, 1), sigma: (-32768, 1000, 0)})
        grasp = next(i for i in sigma.body_script.component.instances.values()
                     if i.graph.index == sigma_grasp.GRASP)
        self.assertEqual(grasp.vars[21312].value(), 120)
        for _ in range(180):
            if sigma.body in fight.grasp_shields:
                break
            run.step({})
        self.assertEqual(vitals.temporary_shields[sigma_grasp.SHIELD_TAG].now, 72)
        self.assertFalse(sigma_grasp.active(sigma))
        if sound_last:
            sound()

        sound_pool = vitals.temporary_shields[lucio_barrier.POOL]
        grasp_pool = vitals.temporary_shields[sigma_grasp.SHIELD_TAG]
        self.assertEqual(sum(vitals.now.values()), 33)
        self.assertEqual(own.total, 100)
        # Leave equal shields at the first pulse. Sound loses two on its next tick.
        vitals.decay_temporary_shields(lucio_barrier.POOL, sound_pool.now - 52)
        vitals.decay_temporary_shields(sigma_grasp.SHIELD_TAG, grasp_pool.now - 50)
        owner.body_script.ult_seconds = 0
        owner.body_script.set_ult_charge(2100)
        pulses = []
        damage = fight._damage
        start = run.frame + 1

        def observe(source, target, amount, *args, **kwargs):
            before = sound_pool.now, grasp_pool.now
            result = damage(source, target, amount, *args, **kwargs)
            if source is owner and target.owner is sigma:
                after = sound_pool.now, grasp_pool.now
                pulses.append((fight.tick - start, before, after, sum(vitals.now.values()), own.total))
            return result

        with patch.object(fight, "_damage", side_effect=observe):
            run.step({owner: (0, 0, 32)})
            self.assertIsNotNone(reaper.blossom_state(owner.body_script))
            end = start + 205
            for tick in (end,) if skipped else range(start + 1, end + 1):
                run.match.update(tick * combat.TICK, tick, frames=False)
        expected = (
            [(0, (50, 50), (16, 50), 33), (13, (0, 50), (0, 16), 33),
             (25, (0, 16), (0, 0), 15), (38, (0, 0), (0, 0), 0)]
            if sound_last else
            [(0, (50, 50), (50, 16), 33), (13, (24, 16), (6, 0), 33), (25, (0, 0), (0, 0), 0)]
        )
        self.assertEqual([sample[:4] for sample in pulses], expected)
        effective, previous_health = 0, 33
        for _, before, after, base_health, healed_total in pulses:
            effective += sum(before) - sum(after) + previous_health - base_health
            self.assertAlmostEqual(healed_total, 100 + effective * reaper.REAPING_RATIO, places=4)
            previous_health = base_health
        self.assertEqual(effective, 117 if sound_last else 101)
        self.assertEqual((vitals.death_tick, vitals.killer), (start + expected[-1][0], owner.body))
        self.assertEqual(len(sigma.client.messages(combat.DEATH_NOTICE)), 1)
        self.assertNotIn(sigma.body, fight.sound_barriers.leases)
        self.assertNotIn(sigma.body, fight.grasp_shields)
        self.assertIsNone(reaper.blossom_state(owner.body_script))
        self.assertEqual(owner.body_script.component.entity_var(ULT_CHARGE).value(), 0)
        return pulses, vitals.death_tick

    def test_actual_blossom_spends_layered_sound_and_grasp_shields_in_grant_order(self):
        if not LUCIO_STATUS_SHA256:
            self.skipTest("requires the pinned 073B Sound Barrier supplement")
        for sound_last in (False, True):
            outcomes = []
            for skipped in (False, True):
                with self.subTest(sound_last=sound_last, skipped=skipped):
                    outcome = self.blossom_layered_shields_trace(sound_last=sound_last, skipped=skipped)
                    outcomes.append(outcome)
            if len(outcomes) == 2:
                self.assertEqual(outcomes[0], outcomes[1])

    def test_actual_helix_resolves_grasp_shield_depletion_across_world_gap(self):
        base_health = 119.99

        def scene():
            run, owner, sigma, _ = reaper_tests.ReaperTests().scenario(names=("Reaper", "Sigma", "Soldier"))
            self.assertTrue(run.match.switch_hero(sigma, heroes()[sigma_grasp.HERO]))
            shooter = run.players[2]
            run.match.game_map = replace(run.match.game_map, free_for_all=False)
            owner.team = shooter.team = 0
            sigma.team = 1
            x, y, z = owner.mover.position
            sigma.mover.position[:] = (x, y, z + 1.5)
            shooter.mover.position[:] = (x, y, z - 8)
            sigma.mover.body = shooter.mover.body = None
            fight = run.match.combat
            target = next(t for t in fight._targets() if t.owner is sigma)
            vitals = fight.vitals_of(target)
            vitals.damage(vitals.total - base_health)
            for frame in range(12):
                run.step({sigma: (-32768, 0, 8 if frame == 0 else 0)})
            heading = aim(fight._eye(owner), (x, y + 1, z + 1.5))
            run.step({owner: (*heading, 1), sigma: (-32768, 1000, 0)})
            instance = next(i for i in sigma.body_script.component.instances.values()
                            if i.graph.index == sigma_grasp.GRASP)
            self.assertEqual(instance.vars[21312].value(), 120)
            for _ in range(180):
                if sigma.body in fight.grasp_shields:
                    break
                run.step({})
            decay = fight.grasp_shields[sigma.body]
            pool = vitals.temporary_shields[sigma_grasp.SHIELD_TAG]
            self.assertEqual((pool.now, decay.rate), (72, 5))
            self.assertFalse(sigma_grasp.active(sigma))
            expiry = math.ceil((decay.starts + pool.now / decay.rate) / combat.TICK - 1e-8)
            residual = pool.now - ((expiry - 1) * combat.TICK - decay.starts) * decay.rate
            self.assertGreater(base_health + residual, 120)
            heading = aim(fight._eye(shooter), (x, y + 1, z + 1.5))
            return run, sigma, shooter, vitals, pool, expiry, residual, heading

        def advance(run, tick):
            run.frame = tick
            run.match.update(tick * combat.TICK, tick, frames=False)

        run, _, shooter, vitals, _, _, _, heading = scene()
        fight = run.match.combat
        run.step({shooter: (*heading, 2)})
        fired = run.frame
        for _ in range(25):
            advance(run, run.frame + 1)
            if not fight.rockets:
                break
        self.assertFalse(fight.rockets)
        self.assertAlmostEqual(vitals.total, base_health + 72 - 120)
        travel = run.frame - fired
        self.assertGreater(travel, 1)

        for protected in (True, False):
            outcomes = []
            for skipped in (False, True):
                with self.subTest(protected=protected, skipped=skipped):
                    run, sigma, shooter, vitals, pool, expiry, residual, heading = scene()
                    fight = run.match.combat
                    contact = expiry - int(protected)
                    advance(run, contact - travel - 1)
                    run.step({shooter: (*heading, 2)})
                    rocket, = fight.rockets
                    advance(run, run.frame + 1)
                    self.assertEqual(rocket.last_tick, run.frame)
                    self.assertIn(sigma.body, fight.grasp_shields)
                    impacts = []

                    def observe(missile, *, world=fight, shield=pool, hits=impacts, landed=fight._landed):
                        hits.append((world.tick, shield.now))
                        landed(missile)

                    end = expiry + 30
                    with patch.object(fight, "_landed", side_effect=observe):
                        while run.frame < end:
                            advance(run, end if skipped else run.frame + 1)
                    self.assertFalse(fight.rockets)
                    self.assertEqual(len(impacts), 1)
                    self.assertEqual(impacts[0][0], contact)
                    self.assertAlmostEqual(impacts[0][1], residual if protected else 0)
                    self.assertNotIn(sigma.body, fight.grasp_shields)
                    self.assertAlmostEqual(vitals.total, base_health + residual - 120 if protected else 0)
                    credited = 120 if protected else base_health
                    self.assertAlmostEqual(shooter.stats.value(stats.HERO_DAMAGE), credited)
                    if protected:
                        self.assertFalse(vitals.dead)
                    else:
                        self.assertEqual((vitals.death_tick, vitals.killer), (contact, shooter.body))
                    outcomes.append(impacts[0][0])
            if len(outcomes) == 2:
                self.assertEqual(outcomes[0], outcomes[1])

    def test_actual_grasp_retires_on_death_or_body_replacement(self):
        for end in ("death_during_grasp", "death_after_grant", "live_replacement"):
            with self.subTest(end=end):
                run, owner, sigma, _ = reaper_tests.ReaperTests().scenario()
                position = tuple(sigma.mover.position)
                self.assertTrue(run.match.switch_hero(sigma, heroes()[sigma_grasp.HERO]))
                sigma.mover.position[:] = position
                sigma.mover.body = None
                fight = run.match.combat
                for frame in range(12):
                    run.step({sigma: (-32768, 0, 8 if frame == 0 else 0)})
                old_body = sigma.body
                old_script = sigma.body_script
                grasp = next(i for i in old_script.component.instances.values()
                             if i.graph.index == sigma_grasp.GRASP)
                self.assertTrue(grasp.states[1].active)
                target = next(t for t in fight._targets() if t.owner is sigma)
                vitals = fight.vitals_of(target)
                if end != "live_replacement":
                    vitals.damage(vitals.total - 20)
                reaper_tests.ReaperTests().fire(run, owner, sigma)
                self.assertEqual(grasp.vars[21312].value(), 120)

                if end == "death_during_grasp":
                    x, y, z = position
                    yaw, pitch = aim(fight._eye(owner), (x, y + 1, z))
                    run.step({owner: (yaw, pitch, 0x800)})
                    for _ in range(30):
                        if vitals.dead:
                            break
                        run.step({})
                else:
                    for _ in range(180):
                        if old_body in fight.grasp_shields:
                            break
                        run.step({})
                    self.assertAlmostEqual(vitals.temporary_shields[sigma_grasp.SHIELD_TAG].now, 72)
                    if end == "death_after_grant":
                        reaper_tests.ReaperTests().fire(run, owner, sigma)

                if end != "live_replacement":
                    self.assertTrue(vitals.dead)
                    self.assertEqual(vitals.killer, owner.body)
                    self.assertNotIn(old_body, fight.grasp_shields)
                    self.assertNotIn(old_body, fight.grasp_grants)
                self.assertTrue(run.match.switch_hero(sigma, heroes()[sigma_grasp.HERO], preserve_pose=True))
                self.assertTrue(old_script.retired)
                self.assertNotEqual(sigma.body, old_body)
                self.assertNotIn(old_body, fight.grasp_shields)
                self.assertNotIn(old_body, fight.grasp_grants)
                with self.assertNoLogs("ow174.game", level="ERROR"):
                    run.frame += 240
                    run.match.update(run.frame * combat.TICK, run.frame, frames=False)
                    reaper_tests.ReaperTests().fire(run, owner, sigma)
                new_target = next(t for t in fight._targets() if t.owner is sigma)
                new_vitals = fight.vitals_of(new_target)
                self.assertEqual(new_vitals.total, 280)
                self.assertNotIn(sigma_grasp.SHIELD_TAG, new_vitals.temporary_shields)
                self.assertNotIn(sigma.body, fight.grasp_grants)
                if end == "live_replacement":
                    for frame in range(40):
                        run.step({sigma: (-32768, 0, 8 if frame == 0 else 0)})
                    reaper_tests.ReaperTests().fire(run, owner, sigma)
                    self.assertEqual(new_vitals.total, 280)
                    for _ in range(180):
                        if sigma.body in fight.grasp_shields:
                            break
                        run.step({})
                    self.assertAlmostEqual(new_vitals.temporary_shields[sigma_grasp.SHIELD_TAG].now, 72)
                    self.assertEqual(new_vitals.total, 352)


class ReaperDvaMatrixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from ow174.game.mover import DVA_MOVER_SHA256
        from ow174.game.script.graph import DVA_GRAPH_SHA256

        if not (DVA_MOVER_SHA256 and DVA_GRAPH_SHA256):
            raise unittest.SkipTest("requires the pinned 0407 mover and graph supplements")

    def scenario(self, *, names=("Reaper", "Target")):
        run, owner, defender, _ = reaper_tests.ReaperTests().scenario(names=names)
        position = tuple(defender.mover.position)
        self.assertTrue(run.match.switch_hero(defender, replace(find_hero("dva"), body=pools.DVA_MECH)))
        defender.mover.position[:] = position
        defender.mover.body = None
        for _ in range(100):
            run.step({})
        instance = next(i for i in defender.body_script.component.instances.values()
                        if i.graph.index == 0x02CC)
        target = next(t for t in run.match.combat._targets() if t.owner is defender)
        return run, owner, defender, instance, run.match.combat.vitals_of(target)

    def test_actual_dragonblade_reaches_reaper_through_matrix_and_grasp(self):
        run, owner, mech, matrix, mech_pool = self.scenario(names=("Reaper", "Dva", "Genji", "Sigma"))
        genji, sigma = run.players[2:]
        self.assertTrue(run.match.switch_hero(genji, heroes()[0x02E0000000000029]))
        self.assertTrue(run.match.switch_hero(sigma, heroes()[sigma_grasp.HERO]))
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        owner.team = mech.team = sigma.team = 0
        genji.team = 1
        fight = run.match.combat
        x, y, z = owner.mover.position
        genji.mover.position[:] = (x, y, z + 3)
        mech.mover.position[:] = (x + 0.8, y, z + 1.5)
        sigma.mover.position[:] = (x - 0.8, y, z + 1.5)
        for actor in (genji, mech, sigma):
            actor.mover.body = None
        for _ in range(30):
            run.step({})
        heading = aim(fight._eye(genji), (x, y + 1, z))
        genji.body_script.set_ult_charge(1932)
        run.step({genji: (*heading, 32)})
        for _ in range(72):
            run.step({genji: (*heading, 0)})
        guard = {mech: (0, 0, 2), sigma: (0, 0, 0)}
        run.step(guard | {sigma: (0, 0, 8)})
        for _ in range(11):
            run.step(guard)
        grasp = next(i for i in sigma.body_script.component.instances.values()
                     if i.graph.index == sigma_grasp.GRASP)
        self.assertTrue(matrix.states[3].active)
        self.assertTrue(grasp.states[1].active)
        run.step(guard | {genji: (*heading, 1)})
        for _ in range(24):
            run.step(guard | {genji: (*heading, 0)})
        self.assertTrue(matrix.states[3].active)
        self.assertTrue(grasp.states[1].active)
        self.assertEqual(grasp.vars[21312].value(), 0)
        self.assertFalse(fight.rockets)
        self.assertEqual(fight.vitals[owner.body].total, 140)
        self.assertEqual(mech_pool.total, 495)
        sigma_target = next(t for t in fight._targets() if t.owner is sigma)
        self.assertEqual(fight.vitals_of(sigma_target).total, 290)

    def test_actual_accretion_exclusion_tag_passes_matrix_to_reaper(self):
        from ow174.game import dva_matrix

        if dva_matrix.TAG_CATALOG_SHA256 is None:
            self.skipTest("requires the pinned projectile tag catalog")
        run, owner, mech, matrix, mech_pool = self.scenario(names=("Reaper", "Dva", "Sigma"))
        caster = run.players[2]
        self.assertTrue(run.match.switch_hero(caster, find_hero("Sigma")))
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        owner.team = mech.team = 0
        caster.team = 1
        fight = run.match.combat
        x, y, z = owner.mover.position
        mech.mover.position[:] = (x + 1.2, y, z + 1.5)
        caster.mover.position[:] = (x, y, z + 6)
        mech.mover.body = caster.mover.body = None
        heading = aim(fight._eye(caster), fight._eye(owner))
        guard = {mech: (0, 0, 2)}
        for _ in range(14):
            run.step(guard)
        run.step(guard | {caster: (*heading, 16)})
        for _ in range(100):
            run.step(guard | {caster: (*heading, 0)})
            if fight.rockets:
                break
        rock, = fight.rockets
        self.assertIsNotNone(rock.accretion_source)
        self.assertTrue(matrix.states[3].active)
        self.assertIn(dva_matrix.EXCLUDED_TAG, dva_matrix._tags()[rock.definition])
        self.assertFalse(dva_matrix.eligible(rock.definition))
        control = fight._projectile_defense_contact(
            caster, rock.position, rock.direction, 6, moving=True, definition=dva_matrix.REAPER_SHELL,
        )
        self.assertIsNotNone(control)
        self.assertIs(control[1], mech)
        for _ in range(25):
            run.step(guard)
            if rock not in fight.rockets:
                break
        self.assertFalse(fight.rockets)
        self.assertTrue(matrix.states[3].active)
        self.assertEqual(mech_pool.total, 565)  # 40 splash less 5 armour, no direct hit
        self.assertFalse(fight.is_stunned(mech))
        splash_velocity = fight.npc_impulses[mech.body][1]
        direct_velocity = fight.npc_impulses[owner.body][1]
        self.assertGreater(splash_velocity[0], 0)
        self.assertGreater(splash_velocity[2], 0)
        self.assertGreater(direct_velocity[1], splash_velocity[1])
        self.assertAlmostEqual(direct_velocity[2], -6.0)
        mech_before = tuple(mech.mover.position)
        run.step(guard)
        self.assertGreater(mech.mover.position[0], mech_before[0])
        self.assertEqual(fight.vitals[owner.body].total, 180)
        self.assertTrue(fight.is_stunned(owner))

    def test_actual_matrix_intercepts_genji_fan_until_released(self):
        from ow174.game import dva_matrix

        if dva_matrix.TAG_CATALOG_SHA256 is None:
            self.skipTest("requires the pinned projectile tag catalog")
        for held in (False, True):
            with self.subTest(held=held):
                run, owner, mech, matrix, mech_pool = self.scenario(names=("Reaper", "Dva", "Genji"))
                genji = run.players[2]
                self.assertTrue(run.match.switch_hero(genji, heroes()[0x02E0000000000029]))
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                owner.team = mech.team = 0
                genji.team = 1
                fight = run.match.combat
                x, y, z = owner.mover.position
                mech.mover.position[:] = (x + 1.2, y, z + 1.5)
                genji.mover.position[:] = (x, y, z + 4)
                mech.mover.body = genji.mover.body = None
                for _ in range(60):
                    run.step({mech: (0, 0, 2)})
                self.assertTrue(matrix.states[3].active)
                heading = aim(fight._eye(genji), (x, y + 0.8, z))
                guard = {mech: (0, 0, 2)} if held else {}
                run.step(guard | {genji: (*heading, 2)})
                self.assertEqual(len(fight.rockets), 3)
                self.assertTrue(all(dva_matrix.eligible(shot.definition) for shot in fight.rockets))
                self.assertEqual(matrix.states[3].active, held)
                for _ in range(25):
                    run.step(guard)
                self.assertEqual(fight.vitals[owner.body].total, 250 if held else 221)
                if held:
                    self.assertFalse(fight.rockets)
                    self.assertEqual(mech_pool.total, 600)
                    self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), 0)

    def test_actual_secondary_drains_releases_refuses_during_cooldown_and_restarts(self):
        run, _, mech, instance, vitals = self.scenario()
        self.assertEqual(sorted(vitals.now.values()), [0, 300, 300])
        self.assertFalse(instance.component.entity_var(33).links)
        for _ in range(60):
            run.step({mech: (-32768, 0, 2)})
        self.assertTrue(instance.states[3].active)
        self.assertAlmostEqual(instance.vars[87].value(), 52, delta=0.1)
        run.step({})
        self.assertFalse(instance.states[3].active)
        self.assertGreater(instance.vars[61].value(), 0.9)
        for _ in range(5):
            run.step({mech: (-32768, 0, 2)})
        self.assertFalse(instance.states[3].active)
        for _ in range(70):
            run.step({})
        self.assertEqual(instance.vars[61].value(), 0)
        run.step({mech: (-32768, 0, 2)})
        self.assertTrue(instance.states[3].active)

    def test_actual_shell_absorbed_only_with_front_facing_active_matrix(self):
        damage = {}
        for case in ("front", "rear", "released"):
            with self.subTest(case=case):
                run, owner, mech, instance, vitals = self.scenario()
                heading = 0 if case == "rear" else -32768
                for _ in range(60):
                    run.step({mech: (heading, 0, 2)})
                self.assertTrue(instance.states[3].active)
                if case == "released":
                    run.step({mech: (heading, 0, 0)})
                    self.assertFalse(instance.states[3].active)
                x, y, z = mech.mover.position
                yaw, pitch = aim(run.match.combat._eye(owner), (x, y + 1, z))
                with self.assertNoLogs("ow174.game", level="ERROR"):
                    run.step({owner: (yaw, pitch, 1), mech: (heading, 0, 0 if case == "released" else 2)})
                damage[case] = 600 - vitals.total
        self.assertEqual(damage["front"], 0)
        self.assertGreater(damage["released"], 0)
        self.assertEqual(damage["rear"], damage["released"])


    def test_actual_helix_absorbs_without_impact_and_wall_keeps_matrix_occluded(self):
        from ow174.game.collision import World
        from ow174.game.dva_matrix import TAG_CATALOG_SHA256

        if TAG_CATALOG_SHA256 is None:
            self.skipTest("requires the pinned projectile tag catalog")
        for case in ("front", "rear", "wall"):
            with self.subTest(case=case):
                run, shooter, mech, instance, vitals = self.scenario()
                position = tuple(shooter.mover.position)
                self.assertTrue(run.match.switch_hero(shooter, heroes()[SOLDIER]))
                shooter.mover.position[:] = position
                shooter.mover.body = None
                x, y, z = position
                mech.mover.position[:] = (x, y, z + 5)
                mech.mover.body = None
                heading = 0 if case == "rear" else -32768
                if case == "wall":
                    wall_z = z + 2
                    run.match.collision_world = World([
                        (x-10, y-10, wall_z, x+10, y-10, wall_z, x+10, y+10, wall_z),
                        (x-10, y-10, wall_z, x+10, y+10, wall_z, x-10, y+10, wall_z),
                    ], name="wall between mech and Matrix contact")
                for _ in range(30):
                    run.step({mech: (heading, 0, 2)})
                self.assertTrue(instance.states[3].active)
                fight = run.match.combat
                yaw, pitch = aim(fight._eye(shooter), (x, y + 1, z + 5))
                with patch.object(fight.projectiles, "absorbed",
                                  wraps=fight.projectiles.absorbed) as absorbed:
                    run.step({shooter: (yaw, pitch, 2), mech: (heading, 0, 2)})
                    rocket, = fight.rockets
                    self.assertEqual(rocket.definition, 0x040000000000072C)
                    self.assertIsNotNone(rocket.shot)
                    for _ in range(20):
                        run.step({mech: (heading, 0, 2)})
                    self.assertEqual(absorbed.call_count, int(case == "front"))
                    if case == "front":
                        self.assertNotIn(rocket.shot, fight.projectiles.shots)
                        self.assertIsNone(rocket.shot.release_at)
                self.assertFalse(fight.rockets)
                if case == "rear":
                    self.assertLess(vitals.total, 600)
                else:
                    self.assertEqual(vitals.total, 600)

    def test_actual_helix_resolves_matrix_release_across_world_gap(self):
        from ow174.game import dva_matrix

        if not dva_matrix.eligible(0x040000000000072C):
            self.skipTest("requires the pinned Helix projectile tag")

        def scene():
            run, owner, mech, matrix, pool = self.scenario(names=("Reaper", "Dva", "Soldier"))
            shooter = run.players[2]
            self.assertTrue(run.match.switch_hero(shooter, heroes()[SOLDIER]))
            run.match.game_map = replace(run.match.game_map, free_for_all=False)
            owner.team = mech.team = 0
            shooter.team = 1
            x, y, z = owner.mover.position
            mech.mover.position[:] = (x + 1.2, y, z + 2)
            shooter.mover.position[:] = (x, y, z + 16)
            mech.mover.body = shooter.mover.body = None
            guard = {mech: (0, 0, 2)}
            for _ in range(30):
                run.step(guard)
            self.assertTrue(matrix.states[dva_matrix.STATE].active)
            heading = aim(run.match.combat._eye(shooter), (x, y + 1, z))
            run.step({**guard, shooter: (*heading, 2)})
            rocket, = run.match.combat.rockets
            self.assertEqual(rocket.definition, 0x040000000000072C)
            self.assertIsNotNone(rocket.shot)
            return run, owner, mech, shooter, matrix, pool, guard, rocket

        run, owner, _, _, _, _, guard, rocket = scene()
        fight = run.match.combat
        fired = run.frame
        with patch.object(fight.projectiles, "absorbed", wraps=fight.projectiles.absorbed) as absorbed:
            for _ in range(20):
                run.step(guard)
                if not fight.rockets:
                    break
        absorbed.assert_called_once_with(rocket.shot)
        travel = run.frame - fired
        self.assertGreater(travel, 2)
        self.assertEqual(fight.vitals[owner.body].total, 250)

        for intercepted in (True, False):
            outcomes = []
            for skipped in (False, True):
                with self.subTest(intercepted=intercepted, skipped=skipped):
                    run, owner, mech, shooter, matrix, pool, guard, rocket = scene()
                    fight = run.match.combat
                    contact = run.frame + travel
                    last_held = contact - (1 if intercepted else 2)
                    while run.frame < last_held:
                        run.step(guard)
                    self.assertEqual(rocket.last_tick, run.frame)
                    self.assertTrue(matrix.states[dva_matrix.STATE].active)
                    frame = mech.commands.last_frame
                    impacts = []

                    def absorb(shot, *, world=fight, hits=impacts, finish=fight.projectiles.absorbed):
                        hits.append((world.tick, True))
                        finish(shot)

                    def land(missile, *, world=fight, hits=impacts, finish=fight._landed):
                        hits.append((world.tick, False))
                        finish(missile)

                    end = contact + 35
                    with patch.object(fight.projectiles, "absorbed", side_effect=absorb), patch.object(
                        fight, "_landed", side_effect=land,
                    ):
                        while run.frame < end:
                            run.frame = end if skipped else run.frame + 1
                            run.match.update(run.frame * combat.TICK, run.frame, frames=False)
                    self.assertEqual(mech.commands.last_frame, frame)
                    self.assertFalse(matrix.states[dva_matrix.STATE].active)
                    self.assertFalse(fight.rockets)
                    self.assertEqual(len(impacts), 1)
                    self.assertEqual(impacts[0][1], intercepted)
                    if intercepted:
                        self.assertEqual(impacts[0][0], contact)
                        self.assertNotIn(rocket.shot, fight.projectiles.shots)
                        self.assertIsNone(rocket.shot.release_at)
                    else:
                        self.assertGreater(impacts[0][0], contact)
                    self.assertAlmostEqual(pool.total, 600 if intercepted else 529.4212, places=4)
                    self.assertEqual(fight.vitals[owner.body].total, 250 if intercepted else 130)
                    expected_damage = 0 if intercepted else 120 + (600 - pool.total)
                    self.assertAlmostEqual(shooter.stats.value(stats.HERO_DAMAGE), expected_damage)
                    outcomes.append(impacts)
            if len(outcomes) == 2:
                self.assertEqual(outcomes[0], outcomes[1])

    def test_actual_shell_resolves_dome_and_matrix_in_contact_order(self):
        cases = (
            ("near dome", 7, 1, 580),
            ("near hostile Matrix", 11, 1, 700),
            ("near allied Matrix", 11, 0, 580),
        )
        for label, winston_distance, mech_team, expected_shield in cases:
            with self.subTest(case=label):
                run, shooter, winston, _ = reaper_tests.ReaperTests().scenario(
                    team_mode=True, names=("Reaper", "Winston", "D.Va"))
                mech = run.players[2]
                shooter.team, winston.team, mech.team = 0, 1, mech_team
                x, y, z = shooter.mover.position
                for player, hero, distance in (
                    (winston, heroes()[0x02E0000000000009], winston_distance),
                    (mech, replace(find_hero("dva"), body=pools.DVA_MECH), 15),
                ):
                    self.assertTrue(run.match.switch_hero(player, hero))
                    player.mover.position[:] = (x, y, z + distance)
                    player.mover.body = None
                for _ in range(100):
                    run.step({})
                fight = run.match.combat
                for frame in range(15):
                    run.step({winston: (-32768, 0, 16 if frame == 0 else 0),
                              mech: (-32768, 0, 2)})
                dome, = fight.winston_bubbles.actors.values()
                matrix = next(i for i in mech.body_script.component.instances.values()
                              if i.graph.index == 0x02CC)
                self.assertTrue(matrix.states[3].active)
                self.assertEqual(dome.health, 700)
                vitals = [fight.vitals_of(t) for t in fight._targets() if t.owner in (winston, mech)]
                self.assertEqual(len(vitals), 2)
                before = [v.total for v in vitals]
                shooter_vitals = fight.vitals[shooter.body]
                shooter_vitals.damage(100)
                yaw, pitch = aim(fight._eye(shooter), (x, y + 1, z + winston_distance))
                with self.assertNoLogs("ow174.game", level="ERROR"):
                    run.step({shooter: (yaw, pitch, 1), mech: (-32768, 0, 2)})
                self.assertEqual(dome.health, expected_shield)
                self.assertEqual([v.total for v in vitals], before)
                self.assertEqual(shooter_vitals.total, 150)
                self.assertEqual(shooter.client.messages(combat.HIT), [])

    def test_actual_melee_and_blossom_damage_body_while_matrix_remains_active(self):
        for action, expected_damage, frames in (("melee", 25, 30), ("Blossom", 29, 0)):
            with self.subTest(action=action):
                run, owner, mech, matrix, vitals = self.scenario()
                for _ in range(20):
                    run.step({mech: (-32768, 0, 2)})
                self.assertTrue(matrix.states[3].active)
                fight = run.match.combat
                own = fight.vitals[owner.body]
                own.damage(100)
                x, y, z = mech.mover.position
                yaw, pitch = aim(fight._eye(owner), (x, y + 1, z))
                if action == "Blossom":
                    owner.body_script.set_ult_charge(2100)
                button = 0x800 if action == "melee" else 32
                with self.assertNoLogs("ow174.game", level="ERROR"):
                    run.step({owner: (yaw, pitch, button), mech: (-32768, 0, 2)})
                    for _ in range(frames):
                        run.step({mech: (-32768, 0, 2)})
                self.assertTrue(matrix.states[3].active)
                self.assertEqual(vitals.total, 600 - expected_damage)
                self.assertAlmostEqual(own.total, 150 + 0.35 * expected_damage, places=4)

    def test_actual_shell_hits_after_held_matrix_exhausts(self):
        run, owner, mech, matrix, vitals = self.scenario()
        for _ in range(10):
            run.step({mech: (-32768, 0, 2)})
        self.assertTrue(matrix.states[3].active)
        for _ in range(140):
            run.step({mech: (-32768, 0, 2)})
        self.assertFalse(matrix.states[3].active)
        self.assertEqual(matrix.vars[87].value(), 0)
        x, y, z = mech.mover.position
        yaw, pitch = aim(run.match.combat._eye(owner), (x, y + 1, z))
        with self.assertNoLogs("ow174.game", level="ERROR"):
            run.step({owner: (yaw, pitch, 1), mech: (-32768, 0, 2)})
        self.assertFalse(matrix.states[3].active)
        self.assertLess(vitals.total, 600)
        self.assertEqual(len(owner.client.messages(combat.HIT)), 20)

    def test_actual_shell_passes_dead_or_replaced_matrix_to_enemy_behind(self):
        for case in ("melee kill", "pilot replacement"):
            with self.subTest(case=case):
                run, owner, mech, matrix, vitals = self.scenario(names=("Reaper", "Mech", "Rear"))
                rear = run.players[2]
                x, y, z = owner.mover.position
                rear.mover.position[:] = (x, y, z + 3.5)
                rear.mover.body = None
                for _ in range(20):
                    run.step({mech: (-32768, 0, 2)})
                self.assertTrue(matrix.states[3].active)
                fight = run.match.combat
                rear_target = next(target for target in fight._targets() if target.owner is rear)
                rear_vitals = fight.vitals_of(rear_target)
                self.assertEqual(rear_vitals.total, 200)
                old = mech.body_script
                if case == "melee kill":
                    vitals.damage(580)
                    vx, vy, vz = mech.mover.position
                    yaw, pitch = aim(fight._eye(owner), (vx, vy + 1, vz))
                    run.step({owner: (yaw, pitch, 0x800), mech: (-32768, 0, 2)})
                    for _ in range(55):
                        run.step({mech: (-32768, 0, 2)})
                    self.assertTrue(vitals.dead)
                    self.assertEqual(vitals.killer, owner.body)
                else:
                    self.assertTrue(run.match.switch_hero(mech, find_hero("dva")))
                    self.assertTrue(old.retired)
                    mech.mover.position[:] = (x + 8, y, z + 1.5)
                    mech.mover.body = None
                    for _ in range(4):
                        run.step({})
                self.assertEqual(rear_vitals.total, 200)
                yaw, pitch = aim(fight._eye(owner), (x, y + 1, z + 3.5))
                with self.assertNoLogs("ow174.game", level="ERROR"):
                    run.step({owner: (yaw, pitch, 1), mech: (-32768, 0, 2)})
                self.assertLess(rear_vitals.total, 200)

    def test_actual_rear_sleep_dart_ends_matrix_before_frontal_shell(self):
        from ow174.game import ana

        run, owner, mech, matrix, vitals = self.scenario(names=("Reaper", "Mech", "Ana"))
        caster = run.players[2]
        self.assertTrue(run.match.switch_hero(caster, heroes()[ana.HERO]))
        x, y, z = mech.mover.position
        caster.mover.position[:] = (x + 1, y, z + 4)
        caster.mover.body = None
        for _ in range(20):
            run.step({mech: (-32768, 0, 2)})
        self.assertTrue(matrix.states[3].active)
        fight = run.match.combat
        yaw, pitch = aim(fight._eye(caster), (x, y + 1, z))
        run.step({caster: (yaw, pitch, 8), mech: (-32768, 0, 2)})
        for _ in range(40):
            run.step({caster: (yaw, pitch, 0), mech: (-32768, 0, 2)})
            if fight.is_stunned(mech):
                break
        self.assertIn(mech.body, fight.ana_sleeps)
        self.assertFalse(matrix.states[3].active)
        before = vitals.total
        yaw, pitch = aim(fight._eye(owner), (x, y + 1, z))
        with self.assertNoLogs("ow174.game", level="ERROR"):
            run.step({owner: (yaw, pitch, 1), mech: (-32768, 0, 2)})
        self.assertLess(vitals.total, before)
        self.assertFalse(matrix.states[3].active)

class ReaperSigmaBarrierTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from ow174.game.sigma_barrier import GEOMETRY_SHA256

        if GEOMETRY_SHA256 is None:
            raise unittest.SkipTest("requires the pinned Sigma C015 collision mesh")

    def scenario(self, distance=8, *, extra=()):
        run, owner, sigma, _ = reaper_tests.ReaperTests().scenario(
            names=("Reaper", "Target", *extra),
        )
        x, y, z = owner.mover.position
        self.assertTrue(run.match.switch_hero(sigma, heroes()[sigma_grasp.HERO]))
        sigma.mover.position[:] = (x, y, z + distance)
        sigma.mover.body = None
        for _ in range(30):
            run.step({})
        fight = run.match.combat
        barrier = fight.sigma_barriers.actors[sigma.body]
        self.assertEqual((barrier.mode, barrier.health, barrier.speed), (1, 700, 16.5))
        self.assertAlmostEqual(barrier.clearance, 0.9)
        return run, owner, sigma, barrier

    def deploy(self, run, sigma, barrier):
        for _ in range(15):
            run.step({sigma: (-32768, 0, 2)})
        self.assertEqual(barrier.mode, 2)
        run.step({sigma: (-32768, 0, 0)})
        self.assertEqual(barrier.mode, 3)

    def test_actual_shell_hits_deployed_mesh_without_body_damage_or_reaping(self):
        run, owner, sigma, barrier = self.scenario()
        self.deploy(run, sigma, barrier)
        fight = run.match.combat
        fight.vitals[owner.body].damage(100)
        owner.body_script.set_ult_charge(0)
        with self.assertNoLogs("ow174.game", level="ERROR"):
            reaper_tests.ReaperTests().fire(run, owner, sigma)
        self.assertEqual(barrier.health, 580)
        self.assertEqual(fight.vitals[sigma.body].total, 400)
        self.assertEqual(fight.vitals[owner.body].total, 150)
        self.assertEqual(owner.body_script.component.vars[342].value(), 0)

    def test_actual_helix_meets_sigma_barrier_as_it_parks(self):
        outcomes = []
        for skipped in (False, True):
            with self.subTest(skipped=skipped):
                run, owner, sigma, _ = reaper_tests.ReaperTests().scenario(
                    names=("Reaper", "Sigma", "Soldier"),
                )
                self.assertTrue(run.match.switch_hero(sigma, heroes()[sigma_grasp.HERO]))
                shooter = run.players[2]
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                owner.team = sigma.team = 1
                shooter.team = 0
                x, y, z = owner.mover.position
                shooter.mover.position[:] = (x, y, z)
                owner.mover.position[:] = (x, y, z + 15)
                sigma.mover.position[:] = (x, y, z + 11)
                shooter.mover.body = owner.mover.body = sigma.mover.body = None
                fight = run.match.combat
                for _ in range(30):
                    run.step({})
                barrier = fight.sigma_barriers.actors[sigma.body]
                facing = aim(fight._eye(sigma), (x, fight._eye(sigma)[1], z))
                for _ in range(15):
                    run.step({sigma: (*facing, 2)})
                shot = aim(fight._eye(shooter), (x, y + 1, z + 15))
                run.step({shooter: (*shot, 2), sigma: (*facing, 2)})
                rocket, = fight.rockets
                run.step({sigma: (*facing, 2)})
                self.assertEqual((rocket.last_tick, barrier.mode), (run.frame, 2))
                start_z = barrier.position[2]
                impacts = []

                def observe(missile, *, world=fight, shield=barrier, hits=impacts, landed=fight._landed):
                    hits.append((world.tick, shield.mode, shield.position))
                    landed(missile)

                until = run.frame + 30
                with patch.object(fight, "_landed", side_effect=observe):
                    while run.frame < until:
                        run.frame = until if skipped else run.frame + 1
                        run.match.update(run.frame * combat.TICK, run.frame, frames=False)
                self.assertFalse(fight.rockets)
                self.assertEqual((barrier.mode, barrier.health), (3, 580))
                self.assertEqual(fight.vitals[owner.body].total, 250)
                self.assertEqual(len(impacts), 1)
                impact_tick, impact_mode, impact_position = impacts[0]
                self.assertLess(impact_tick, until)
                self.assertEqual((impact_mode, impact_position), (3, barrier.position))
                self.assertAlmostEqual(start_z - barrier.position[2], barrier.speed * combat.TICK)
                outcomes.append(impacts[0])
        self.assertEqual(len(outcomes), 2)
        self.assertEqual(outcomes[0], outcomes[1])

    def test_actual_parked_sigma_barrier_resolves_sleep_then_helix_in_world_order(self):
        from ow174.game import ana

        def scene():
            run, owner, sigma, barrier = self.scenario(extra=("Soldier", "Ana"))
            soldier, caster = run.players[2:]
            self.assertTrue(run.match.switch_hero(caster, heroes()[ana.HERO]))
            run.match.game_map = replace(run.match.game_map, free_for_all=False)
            owner.team = sigma.team = barrier.team
            soldier.team = caster.team = 1 - barrier.team
            self.deploy(run, sigma, barrier)
            x, y, z = owner.mover.position
            sigma.mover.position[:] = (x + 10, y, z + 8)
            soldier.mover.position[:] = (x, y, z + 20)
            caster.mover.position[:] = (x + 1.2, y, z + 7)
            sigma.mover.body = soldier.mover.body = caster.mover.body = None
            run.step({})
            self.assertEqual(barrier.mode, 3)
            fight = run.match.combat
            fight.sigma_barriers.damage(barrier, 580)
            self.assertEqual(barrier.health, 120)
            headings = [aim(fight._eye(shooter), (x, y + 1, z)) for shooter in (soldier, caster)]
            return run, owner, soldier, caster, barrier, headings

        run, _, _, caster, _, (_, heading) = scene()
        fight = run.match.combat
        start = run.frame
        run.step({caster: (*heading, 8)})
        for _ in range(30):
            if fight.rockets:
                break
            run.step({caster: (*heading, 0)})
        dart, = fight.rockets
        self.assertEqual(dart.direct.effect, ana.SLEEP_EFFECT)
        windup = run.frame - start
        self.assertGreater(windup, 2)

        outcomes = []
        for skipped in (False, True):
            with self.subTest(skipped=skipped):
                run, owner, soldier, caster, barrier, (helix_aim, dart_aim) = scene()
                fight = run.match.combat
                run.step({caster: (*dart_aim, 8)})
                tracking = {caster: (*dart_aim, 0)}
                for _ in range(windup - 3):
                    run.step(tracking)
                run.step(tracking | {soldier: (*helix_aim, 2)})
                run.step(tracking)
                run.step(tracking)
                self.assertEqual([rocket.shooter for rocket in fight.rockets], [soldier, caster])
                run.step({})
                self.assertEqual([rocket.last_tick for rocket in fight.rockets], [run.frame] * 2)
                self.assertEqual((barrier.mode, barrier.health), (3, 120))
                for rocket in fight.rockets:
                    contact = barriers.intercept(fight, rocket.shooter, rocket.position, rocket.direction, 25)
                    self.assertIsNotNone(contact)
                    self.assertIs(contact[1], barrier)
                contacts = []

                def observe(missile, *, world=fight, shield=barrier, hits=contacts, landed=fight._landed):
                    hits.append((world.tick, missile.shooter.entity, shield.health))
                    landed(missile)

                end = run.frame + 40
                with patch.object(fight, "_landed", side_effect=observe):
                    while run.frame < end:
                        run.frame = end if skipped else run.frame + 1
                        run.match.update(run.frame * combat.TICK, run.frame, frames=False)
                self.assertFalse(fight.rockets)
                self.assertEqual(len(contacts), 2)
                self.assertEqual(
                    [contact[1:] for contact in contacts], [(caster.entity, 120), (soldier.entity, 115)],
                )
                self.assertLess(contacts[0][0], contacts[1][0])
                self.assertEqual(barrier.health, 0)
                self.assertFalse(barrier.active())
                self.assertNotIn(owner.body, fight.ana_sleeps)
                self.assertEqual(fight.vitals[owner.body].total, 250)
                self.assertEqual(soldier.stats.value(stats.HERO_DAMAGE), 0)
                self.assertEqual(caster.stats.value(stats.HERO_DAMAGE), 0)
                outcomes.append(contacts)
        if len(outcomes) == 2:
            self.assertEqual(outcomes[0], outcomes[1])

    def test_actual_recall_rejects_early_deploy_then_recovers(self):
        run, owner, sigma, barrier = self.scenario()
        self.deploy(run, sigma, barrier)
        reaper_tests.ReaperTests().fire(run, owner, sigma)
        self.assertEqual(barrier.health, 580)
        for _ in range(20):
            run.step({sigma: (-32768, 0, 0)})
        run.step({sigma: (-32768, 0, 2)})
        self.assertFalse(barrier.active())
        self.assertGreater(barrier.source.vars[61].value(), 1.9)
        run.step({})
        for _ in range(30):
            run.step({sigma: (-32768, 0, 2)})
        self.assertEqual(barrier.mode, 1)
        self.assertEqual(barrier.health, 580)
        for _ in range(130):
            run.step({})
        self.assertEqual(barrier.source.vars[61].value(), 0)
        self.assertGreater(barrier.health, 580)
        self.deploy(run, sigma, barrier)
        self.assertTrue(barrier.active())

    def test_actual_shells_break_sigma_barrier_and_later_pellets_hit(self):
        run, owner, sigma, barrier = self.scenario(distance=4.5)
        self.deploy(run, sigma, barrier)
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is sigma)
        vitals = fight.vitals_of(target)
        for expected in (580, 460, 340, 220, 100):
            reaper_tests.ReaperTests().fire(run, owner, sigma)
            self.assertEqual(barrier.health, expected)
            self.assertEqual(vitals.total, 400)
            for _ in range(40):
                run.step({})
        reaper_tests.ReaperTests().fire(run, owner, sigma)
        self.assertEqual(barrier.health, 0)
        self.assertFalse(barrier.active())
        self.assertGreater(barrier.source.vars[61].value(), 4.9)
        self.assertLess(vitals.total, 400)
        hits = owner.client.messages(combat.HIT)
        self.assertEqual(len(hits), 3)
        self.assertEqual(sum(hit["+0x78"]["+0x4"] for hit in hits), 400 - vitals.total)
        for _ in range(30):
            run.step({sigma: (-32768, 0, 2)})
        self.assertFalse(barrier.active())
        self.assertEqual(barrier.health, 0)
        for _ in range(310):
            run.step({})
        self.assertEqual(barrier.source.vars[61].value(), 0)
        self.assertGreater(barrier.health, 0)
        self.deploy(run, sigma, barrier)
        self.assertTrue(barrier.active())

    def test_actual_shell_resolves_experimental_barrier_and_grasp_in_contact_order(self):
        for grasp_first, expected_pool, absorbed in ((True, 700, 120), (False, 580, 0)):
            with self.subTest(grasp_first=grasp_first):
                run, owner, sigma, barrier = self.scenario()
                self.deploy(run, sigma, barrier)
                if grasp_first:
                    sigma.mover.position[2] -= 2
                    sigma.mover.body = None
                for frame in range(12):
                    run.step({sigma: (-32768, 0, 8 if frame == 0 else 0)})
                grasp = next(i for i in sigma.body_script.component.instances.values()
                             if i.graph.index == sigma_grasp.GRASP)
                self.assertTrue(grasp.states[1].active)
                fight = run.match.combat
                fight.vitals[owner.body].damage(100)
                owner.body_script.set_ult_charge(0)
                with self.assertNoLogs("ow174.game", level="ERROR"):
                    reaper_tests.ReaperTests().fire(run, owner, sigma)
                self.assertEqual(barrier.health, expected_pool)
                self.assertEqual(grasp.vars[21312].value(), absorbed)
                self.assertEqual(fight.vitals[sigma.body].total, 400)
                self.assertEqual(fight.vitals[owner.body].total, 150)
                self.assertEqual(owner.body_script.component.vars[342].value(), 0)

    def helix_grasp_scene(self, grasp_first):
        run, owner, sigma, barrier = self.scenario(extra=("Soldier",))
        shooter = run.players[2]
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        owner.team = sigma.team = 1
        shooter.team = 0
        barrier.team = sigma.team
        x, y, z = owner.mover.position
        shooter.mover.position[:] = (x, y, z)
        owner.mover.position[:] = (x, y, z + 10)
        shooter.mover.body = owner.mover.body = None
        self.deploy(run, sigma, barrier)
        if grasp_first:
            sigma.mover.position[2] -= 2
            sigma.mover.body = None
        for frame in range(12):
            run.step({sigma: (-32768, 0, 8 if frame == 0 else 0)})
        grasp = next(i for i in sigma.body_script.component.instances.values()
                     if i.graph.index == sigma_grasp.GRASP)
        self.assertTrue(grasp.states[1].active)
        heading = aim(run.match.combat._eye(shooter), (x, y + 1, z + 10))
        return run, owner, sigma, barrier, shooter, grasp, heading

    def test_actual_helix_resolves_experimental_barrier_and_grasp_in_contact_order(self):
        for grasp_first in (False, True):
            with self.subTest(grasp_first=grasp_first):
                run, owner, sigma, barrier, shooter, grasp, heading = self.helix_grasp_scene(grasp_first)
                facing = (-32768, 0)
                fight = run.match.combat
                run.step({shooter: (*heading, 2), sigma: (*facing, 0)})
                rocket, = fight.rockets
                self.assertIs(rocket.shooter, shooter)
                for _ in range(22):
                    run.step({sigma: (*facing, 0)})
                    if not fight.rockets:
                        break
                self.assertFalse(fight.rockets)
                self.assertTrue(grasp.states[1].active)
                self.assertEqual(barrier.health, 700 if grasp_first else 580)
                self.assertEqual(grasp.vars[21312].value(), 120 if grasp_first else 0)
                self.assertFalse(shooter.client.messages(combat.HIT))
                self.assertEqual(fight.vitals[owner.body].total, 250)
                self.assertEqual(fight.vitals[sigma.body].total, 400)

                for _ in range(125):
                    run.step({})
                    if not grasp.states[1].active:
                        break
                self.assertFalse(grasp.states[1].active)
                pool = fight.vitals[sigma.body].temporary_shields.get(sigma_grasp.SHIELD_TAG)
                self.assertEqual(pool.now if pool is not None else 0, 72 if grasp_first else 0)

    def test_actual_helix_grasp_expiry_exposes_experimental_barrier_across_world_gap(self):
        facing = (-32768, 0, 0)
        run, _, sigma, _, shooter, grasp, heading = self.helix_grasp_scene(True)
        fight = run.match.combat
        run.step({shooter: (*heading, 2), sigma: facing})
        fired = run.frame
        for _ in range(22):
            run.step({sigma: facing})
            if not fight.rockets:
                break
        self.assertFalse(fight.rockets)
        self.assertEqual(grasp.vars[21312].value(), 120)
        travel = run.frame - fired
        for _ in range(125):
            run.frame += 1
            run.match.update(run.frame * combat.TICK, run.frame, frames=False)
            if not grasp.states[1].active:
                break
        self.assertFalse(grasp.states[1].active)
        expiry = run.frame

        for absorbed in (True, False):
            outcomes = []
            for skipped in (False, True):
                with self.subTest(absorbed=absorbed, skipped=skipped):
                    run, owner, sigma, barrier, shooter, grasp, heading = self.helix_grasp_scene(True)
                    fight = run.match.combat
                    fire_tick = expiry - travel - int(absorbed)
                    while run.frame < fire_tick - 1:
                        run.step({sigma: facing})
                    run.step({shooter: (*heading, 2), sigma: facing})
                    rocket, = fight.rockets
                    run.step({sigma: facing})
                    self.assertEqual(rocket.last_tick, run.frame)
                    self.assertTrue(grasp.states[1].active)
                    impacts = []

                    def observe(missile, *, world=fight, state=grasp.states[1], hits=impacts,
                                landed=fight._landed):
                        hits.append((world.tick, state.active))
                        landed(missile)

                    until = run.frame + 30
                    with patch.object(fight, "_landed", side_effect=observe):
                        while run.frame < until:
                            run.frame = until if skipped else run.frame + 1
                            run.match.update(run.frame * combat.TICK, run.frame, frames=False)
                    self.assertFalse(fight.rockets)
                    self.assertEqual(len(impacts), 1)
                    self.assertEqual(impacts[0][1], absorbed)
                    self.assertEqual(impacts[0][0] < expiry, absorbed)
                    self.assertEqual(barrier.health, 700 if absorbed else 580)
                    pool = fight.vitals[sigma.body].temporary_shields.get(sigma_grasp.SHIELD_TAG)
                    self.assertEqual(pool.now if pool is not None else 0, 72 if absorbed else 0)
                    self.assertEqual(fight.vitals[owner.body].total, 250)
                    self.assertFalse(shooter.client.messages(combat.HIT))
                    outcomes.append(impacts[0])
            if len(outcomes) == 2:
                self.assertEqual(outcomes[0], outcomes[1])

    def test_actual_blossom_spends_sigma_barrier_without_grasp_credit(self):
        run, owner, sigma, barrier = self.scenario(distance=4.5)
        self.deploy(run, sigma, barrier)
        for frame in range(12):
            run.step({sigma: (-32768, 0, 8 if frame == 0 else 0)})
        grasp = next(i for i in sigma.body_script.component.instances.values()
                     if i.graph.index == sigma_grasp.GRASP)
        self.assertTrue(grasp.states[1].active)
        fight = run.match.combat
        fight.vitals[owner.body].damage(100)
        owner.body_script.set_ult_charge(2100)
        run.step({owner: (0, 0, 32), sigma: (-32768, 0, 0)})
        for _ in range(200):
            run.step({})
        self.assertTrue(fight.reaper_blossoms[owner.body]["ended"])
        self.assertEqual(barrier.health, 190)
        self.assertEqual(grasp.vars[21312].value(), 0)
        self.assertEqual(fight.vitals[sigma.body].total, 400)
        self.assertEqual(fight.vitals[owner.body].total, 150)

    def test_actual_sigma_barrier_stops_at_wall_and_shell_cannot_cross(self):
        from ow174.game.collision import World

        run, owner, sigma, barrier = self.scenario()
        x, y, z = owner.mover.position
        mid = z + 6
        lo, hi = x - 20, x + 20
        wall = World([
            (lo,y,z-20,lo,y,z+20,hi,y,z+20),
            (lo,y,z-20,hi,y,z+20,hi,y,z-20),
            (lo,y,mid,hi,y,mid,hi,y+5,mid),
            (lo,y,mid,hi,y+5,mid,lo,y+5,mid),
            (lo,y,mid,hi,y+5,mid,hi,y,mid),
            (lo,y,mid,lo,y+5,mid,hi,y+5,mid),
        ], name="Sigma deployment wall")
        run.match.collision_world = wall
        owner.mover.use(world=wall)
        sigma.mover.use(world=wall)
        self.deploy(run, sigma, barrier)
        self.assertTrue(barrier.blocked)
        self.assertAlmostEqual(barrier.position[2], mid + barrier.clearance, places=4)
        reaper_tests.ReaperTests().fire(run, owner, sigma)
        self.assertEqual(barrier.health, 700)
        self.assertEqual(run.match.combat.vitals[sigma.body].total, 400)

    def test_actual_quick_melee_reaches_sigma_through_parked_barrier(self):
        run, owner, sigma, barrier = self.scenario(distance=4.5)
        self.deploy(run, sigma, barrier)
        x, y, z = owner.mover.position
        sigma.mover.position[:] = (x, y, z + 2.3)
        sigma.mover.body = None
        for _ in range(10):
            run.step({})
        fight = run.match.combat
        self.assertLess(barrier.position[2], sigma.mover.position[2])
        own = fight.vitals[owner.body]
        own.damage(100)
        yaw, pitch = aim(fight._eye(owner), (x, y + 1, z + 2.3))
        run.step({owner: (yaw, pitch, 0x800)})
        for _ in range(30):
            run.step({})
        self.assertTrue(barrier.active())
        self.assertEqual(barrier.health, 700)
        self.assertEqual(fight.vitals[sigma.body].total, 370)
        self.assertAlmostEqual(own.total, 160.5, places=4)


class ReaperBrigitteBarrierTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from ow174.game.brigitte_shield import GEOMETRY_SHA256

        if GEOMETRY_SHA256 is None:
            raise unittest.SkipTest("requires the pinned Brigitte 5DA0 collision mesh")

    def scenario(self, facing=-32768, *, names=("Reaper", "Brigitte")):
        from ow174.game.brigitte_shield import HERO

        run, owner, brig, _ = reaper_tests.ReaperTests().scenario(names=names)
        self.assertTrue(run.match.switch_hero(brig, heroes()[HERO]))
        x, y, z = owner.mover.position
        brig.mover.position[:] = (x, y, z + 1.8)
        brig.mover.body = None
        for _ in range(35):
            run.step({brig: (facing, 0, 2)})
        fight = run.match.combat
        fight.vitals_of(next(t for t in fight._targets() if t.owner is brig))
        shield = fight.brigitte_shields.by_body[brig.body]
        self.assertTrue(shield.active())
        self.assertEqual((shield.health, shield.maximum), (250, 250))
        return run, owner, brig, shield

    @staticmethod
    def fire(run, owner, brig):
        fight = run.match.combat
        x, y, z = brig.mover.position
        yaw, pitch = reaper_tests.aim(fight._eye(owner), (x, y + 1.0, z))
        run.step({owner: (yaw, pitch, 1), brig: (brig.mover.yaw, 0, 2)})

    def test_actual_helix_resolves_brigitte_shield_release_across_world_gap(self):
        def scene():
            run, owner, brig, shield = self.scenario(names=("Reaper", "Brigitte", "Soldier"))
            shooter = run.players[2]
            run.match.game_map = replace(run.match.game_map, free_for_all=False)
            owner.team = brig.team = 0
            shooter.team = 1
            x, y, z = owner.mover.position
            brig.mover.position[:] = (x + 0.55, y, z - 4)
            shooter.mover.position[:] = (x, y, z - 10)
            brig.mover.body = shooter.mover.body = None
            guard = {brig: (-32768, 0, 2)}
            heading = aim(run.match.combat._eye(shooter), (x, y + 1, z))
            run.step(guard | {shooter: (*heading, 2)})
            rocket, = run.match.combat.rockets
            return run, owner, brig, shooter, shield, guard, rocket

        run, owner, _, _, shield, guard, _ = scene()
        fight = run.match.combat
        fired = run.frame
        for _ in range(20):
            run.step(guard)
            if not fight.rockets:
                break
        self.assertFalse(fight.rockets)
        self.assertEqual(shield.health, 130)
        self.assertEqual(fight.vitals[owner.body].total, 250)
        travel = run.frame - fired
        self.assertGreater(travel, 2)

        for blocked in (True, False):
            outcomes = []
            for skipped in (False, True):
                with self.subTest(blocked=blocked, skipped=skipped):
                    run, owner, brig, shooter, shield, guard, rocket = scene()
                    fight = run.match.combat
                    surface_tick = run.frame + travel
                    last_held = surface_tick - (1 if blocked else 2)
                    while run.frame < last_held:
                        run.step(guard)
                    self.assertEqual(rocket.last_tick, run.frame)
                    self.assertTrue(shield.active())
                    frame = brig.commands.last_frame
                    impacts = []

                    def observe(missile, *, world=fight, barrier=shield, hits=impacts, landed=fight._landed):
                        hits.append((world.tick, barrier.active()))
                        landed(missile)

                    end = surface_tick + 30
                    with patch.object(fight, "_landed", side_effect=observe):
                        while run.frame < end:
                            run.frame = end if skipped else run.frame + 1
                            run.match.update(run.frame * combat.TICK, run.frame, frames=False)
                    self.assertEqual(brig.commands.last_frame, frame)
                    self.assertFalse(shield.active())
                    self.assertFalse(fight.rockets)
                    self.assertEqual(len(impacts), 1)
                    tick, active = impacts[0]
                    self.assertEqual(active, blocked)
                    if blocked:
                        self.assertEqual(tick, surface_tick)
                    else:
                        self.assertGreater(tick, surface_tick)
                    self.assertEqual(shield.health, 130 if blocked else 250)
                    self.assertEqual(fight.vitals[owner.body].total, 250 if blocked else 130)
                    self.assertEqual(shooter.stats.value(stats.HERO_DAMAGE), 0 if blocked else 120)
                    outcomes.append(impacts)
            if len(outcomes) == 2:
                self.assertEqual(outcomes[0], outcomes[1])

    def test_actual_shell_spends_raised_mesh_without_body_feedback(self):
        run, owner, brig, shield = self.scenario()
        fight = run.match.combat
        pool = fight.vitals[brig.body]
        before = pool.total
        fight.vitals[owner.body].damage(100)
        owner.body_script.set_ult_charge(0)
        self.fire(run, owner, brig)
        self.assertEqual(shield.health, 130)
        self.assertEqual(pool.total, before)
        self.assertEqual(fight.vitals[owner.body].total, 150)
        self.assertEqual(owner.body_script.component.vars[342].value(), 0)
        self.assertEqual(owner.client.messages(combat.HIT), [])

    def test_actual_shell_from_behind_bypasses_raised_mesh(self):
        run, owner, brig, shield = self.scenario(facing=0)
        pool = run.match.combat.vitals[brig.body]
        before = pool.total
        self.fire(run, owner, brig)
        self.assertEqual(shield.health, 250)
        self.assertLess(pool.total, before)
        self.assertEqual(len(owner.client.messages(combat.HIT)), 20)

    def test_actual_third_shell_breaks_then_remaining_pellets_hit(self):
        run, owner, brig, shield = self.scenario()
        fight = run.match.combat
        pool = fight.vitals[brig.body]
        before = pool.total
        for expected in (130, 10):
            self.fire(run, owner, brig)
            self.assertEqual(shield.health, expected)
            self.assertEqual(pool.total, before)
            for _ in range(40):
                run.step({brig: (-32768, 0, 2)})
        self.fire(run, owner, brig)
        self.assertEqual(shield.health, 0)
        self.assertFalse(shield.active())
        self.assertEqual(len(owner.client.messages(combat.HIT)), 18)
        self.assertLess(pool.total, before)
        for _ in range(35):
            run.step({brig: (-32768, 0, 2)})
        self.assertFalse(shield.active())
        self.assertEqual(shield.health, 0)
        for _ in range(320):
            run.step({})
        self.assertEqual(shield.health, 250)
        for _ in range(35):
            run.step({brig: (-32768, 0, 2)})
        self.assertTrue(shield.active())

    def test_actual_blossom_breaks_raised_mesh_then_damages_body(self):
        run, owner, brig, shield = self.scenario()
        fight = run.match.combat
        pool = fight.vitals[brig.body]
        before = pool.total
        fight.vitals[owner.body].damage(100)
        owner.body_script.set_ult_charge(2100)
        run.step({owner: (0, 0, 32), brig: (-32768, 0, 2)})
        for _ in range(25):
            run.step({brig: (-32768, 0, 2)})
        self.assertLess(shield.health, 250)
        self.assertEqual((pool.total, fight.vitals[owner.body].total), (before, 150))
        for _ in range(175):
            run.step({brig: (-32768, 0, 2)})
        self.assertTrue(fight.reaper_blossoms[owner.body]["ended"])
        self.assertFalse(shield.active())
        self.assertLess(pool.total, before)
        self.assertGreater(fight.vitals[owner.body].total, 150)


class ReaperSymmetraBarrierTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from ow174.game.symmetra_barrier import GEOMETRY_SHA256

        if GEOMETRY_SHA256 is None:
            raise unittest.SkipTest("requires the pinned Symmetra 6880 collision mesh")

    def scenario(self, *, names=("Reaper", "Symmetra")):
        from ow174.game.symmetra_barrier import HERO

        run, owner, sym, _ = reaper_tests.ReaperTests().scenario(names=names)
        self.assertTrue(run.match.switch_hero(sym, heroes()[HERO]))
        x, y, z = owner.mover.position
        sym.mover.position[:] = (x, y, z + 6)
        sym.mover.body = None
        for _ in range(40):
            run.step({})
        fight = run.match.combat
        fight.vitals_of(next(t for t in fight._targets() if t.owner is sym))
        return run, owner, sym

    @staticmethod
    def preview(run, sym):
        run.step({sym: (-32768, 0, 32)})
        for _ in range(15):
            run.step({sym: (-32768, 0, 0)})

    @staticmethod
    def charge(sym):
        from ow174.game.script.driver import ULT_COST

        cost = sym.body_script.component.entity_var(ULT_COST).value()
        sym.body_script.set_ult_charge(cost)
        return cost

    def cast(self, run, sym, *, rotate=False):
        from ow174.game.script.driver import ULT_CHARGE

        fight = run.match.combat
        cost = self.charge(sym)
        self.preview(run, sym)
        charge = sym.body_script.component.entity_var(ULT_CHARGE)
        self.assertEqual(charge.value(), cost)
        self.assertNotIn(sym.body, fight.symmetra_barriers.actors)
        if rotate:
            self.preview(run, sym)
        run.step({sym: (-32768, 0, 1)})
        for _ in range(35):
            run.step({sym: (-32768, 0, 0)})
            actor = fight.symmetra_barriers.actors.get(sym.body)
            if actor is not None:
                self.assertEqual(charge.value(), 0)
                self.assertEqual((actor.health, actor.expires - run.frame), (4000, 750))
                self.assertTrue(any(i.graph.index == 0x156C for i in actor.component.instances.values()))
                return actor
        self.fail("Photon Barrier did not deploy from actual preview and primary confirm")

    def test_actual_shell_spends_barrier_then_expiry_exposes_body(self):
        run, owner, sym = self.scenario()
        fight = run.match.combat
        pool = fight.vitals[sym.body]
        before = pool.total
        wall = self.cast(run, sym)
        fight.vitals[owner.body].damage(100)
        owner.body_script.set_ult_charge(0)
        reaper_tests.ReaperTests().fire(run, owner, sym)
        self.assertEqual(wall.health, 3880)
        self.assertEqual((pool.total, fight.vitals[owner.body].total), (before, 150))
        self.assertEqual(owner.body_script.component.vars[342].value(), 0)
        self.assertEqual(owner.client.messages(combat.HIT), [])
        while run.frame < wall.expires - 2:
            run.step({})
        self.assertIs(fight.symmetra_barriers.actors.get(sym.body), wall)
        while run.frame < wall.expires and sym.body in fight.symmetra_barriers.actors:
            run.step({})
        self.assertNotIn(sym.body, fight.symmetra_barriers.actors)
        self.assertFalse(wall.source.states[9].active)
        self.assertFalse(wall.component.instances)
        reaper_tests.ReaperTests().fire(run, owner, sym)
        self.assertLess(pool.total, before)
        self.assertGreater(fight.vitals[owner.body].total, 150)

    def test_actual_helix_resolves_photon_barrier_expiry_across_world_gap(self):
        def scene():
            run, owner, sym = self.scenario(names=("Reaper", "Symmetra", "Soldier"))
            shooter = run.players[2]
            run.match.game_map = replace(run.match.game_map, free_for_all=False)
            owner.team = sym.team = 0
            shooter.team = 1
            wall = self.cast(run, sym)
            x, _, z = wall.position
            y = owner.mover.position[1]
            for player, position in (
                (owner, (x, y, z + 4)), (shooter, (x, y, z - 8)), (sym, (x + 10, y, z + 4)),
            ):
                player.mover.position[:] = position
                player.mover.body = None
            run.step({})
            heading = aim(run.match.combat._eye(shooter), (x, y + 1, z + 4))
            return run, owner, shooter, wall, heading

        def advance(run, tick):
            run.frame = tick
            run.match.update(tick * combat.TICK, tick, frames=False)

        run, owner, shooter, wall, heading = scene()
        fight = run.match.combat
        run.step({shooter: (*heading, 2)})
        fired = run.frame
        for _ in range(25):
            advance(run, run.frame + 1)
            if not fight.rockets:
                break
        self.assertFalse(fight.rockets)
        self.assertEqual(wall.health, 3880)
        self.assertEqual(fight.vitals[owner.body].total, 250)
        travel = run.frame - fired
        self.assertGreater(travel, 1)

        for intercepted in (True, False):
            outcomes = []
            for skipped in (False, True):
                with self.subTest(intercepted=intercepted, skipped=skipped):
                    run, owner, shooter, wall, heading = scene()
                    fight = run.match.combat
                    surface_tick = wall.expires - int(intercepted)
                    advance(run, surface_tick - travel - 1)
                    run.step({shooter: (*heading, 2)})
                    rocket, = fight.rockets
                    advance(run, run.frame + 1)
                    self.assertEqual(rocket.last_tick, run.frame)
                    self.assertIn(wall, fight.symmetra_barriers.actors.values())
                    contacts = []
                    expiries = []

                    def observe(missile, *, world=fight, barrier=wall, hits=contacts, landed=fight._landed):
                        hits.append((world.tick, barrier in world.symmetra_barriers.actors.values()))
                        landed(missile)

                    service = fight.symmetra_barriers

                    def expire(barrier, *, world=fight, events=expiries, finish=service.end):
                        events.append((world.tick, barrier.health))
                        finish(barrier)

                    end = wall.expires + 35
                    with patch.object(fight, "_landed", side_effect=observe), patch.object(
                        service, "end", side_effect=expire,
                    ):
                        while run.frame < end:
                            advance(run, end if skipped else run.frame + 1)
                    self.assertFalse(fight.symmetra_barriers.actors)
                    self.assertFalse(wall.component.instances)
                    self.assertFalse(fight.rockets)
                    self.assertEqual(len(contacts), 1)
                    if intercepted:
                        self.assertEqual(contacts[0], (surface_tick, True))
                    else:
                        self.assertGreater(contacts[0][0], surface_tick)
                        self.assertFalse(contacts[0][1])
                    self.assertEqual(expiries, [(wall.expires, 3880 if intercepted else 4000)])
                    self.assertEqual(fight.vitals[owner.body].total, 250 if intercepted else 130)
                    self.assertEqual(shooter.stats.value(stats.HERO_DAMAGE), 0 if intercepted else 120)
                    frames = [hit["+0x84"] for hit in shooter.client.messages(combat.HIT)]
                    self.assertEqual(frames, [] if intercepted else [contacts[0][0]] * 2)
                    outcomes.append((contacts, expiries))
            if len(outcomes) == 2:
                self.assertEqual(outcomes[0], outcomes[1])

    def test_actual_blossom_spends_full_channel_on_wall_without_body_feedback(self):
        run, owner, sym = self.scenario()
        fight = run.match.combat
        wall = self.cast(run, sym)
        before = fight.vitals[sym.body].total
        fight.vitals[owner.body].damage(100)
        owner.body_script.set_ult_charge(2100)
        run.step({owner: (0, 0, 32)})
        for _ in range(200):
            run.step({})
        self.assertTrue(fight.reaper_blossoms[owner.body]["ended"])
        self.assertEqual(wall.health, 3490)
        self.assertEqual((fight.vitals[sym.body].total, fight.vitals[owner.body].total), (before, 150))
        self.assertEqual(owner.client.messages(combat.HIT), [])

    def test_actual_preview_cancel_and_uncharged_refusal_then_rotate(self):
        from ow174.game.script.driver import ULT_CHARGE

        run, _, sym = self.scenario()
        fight = run.match.combat
        self.preview(run, sym)
        run.step({sym: (-32768, 0, 1)})
        for _ in range(25):
            run.step({})
        self.assertFalse(fight.symmetra_barriers.actors)
        cost = self.charge(sym)
        self.preview(run, sym)
        run.step({sym: (-32768, 0, 2)})
        for _ in range(30):
            run.step({})
        self.assertFalse(fight.symmetra_barriers.actors)
        self.assertEqual(sym.body_script.component.entity_var(ULT_CHARGE).value(), cost)
        self.assertEqual(self.cast(run, sym, rotate=True).yaw, -16384)

    def test_actual_reverse_shell_and_allied_barrier_filter(self):
        run, owner, sym = self.scenario(names=("Reaper", "Symmetra", "Target"))
        victim = run.players[2]
        fight = run.match.combat
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        owner.team = 0
        sym.team = 1
        victim.team = 1
        wall = self.cast(run, sym)
        x, _, z = wall.position
        floor = owner.mover.position[1]
        for player, position in (
            (owner, (x, floor, z + 1.2)), (victim, (x, floor, z - 1.2)),
            (sym, (x + 10, floor, z + 1.2)),
        ):
            player.mover.position[:] = position
            player.mover.body = None
        for _ in range(40):
            run.step({})
        pool = fight.vitals_of(next(t for t in fight._targets() if t.owner is victim))
        before = pool.total
        reaper_tests.ReaperTests().fire(run, owner, victim)
        self.assertEqual((wall.health, pool.total), (3880, before))
        for _ in range(40):
            run.step({})
        owner.team = sym.team
        victim.team = 0
        reaper_tests.ReaperTests().fire(run, owner, victim)
        self.assertEqual(wall.health, 3880)
        self.assertLess(pool.total, before)

    def test_actual_coordinated_shells_break_full_wall_before_expiry(self):
        from ow174.game.reaper import HERO

        run, owner, sym = self.scenario(names=("Reaper", "Symmetra", "Two", "Three", "Four"))
        fight = run.match.combat
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        shooters = [owner, *run.players[2:]]
        sym.team = 1
        x, y, z = owner.mover.position
        for index, shooter in enumerate(shooters):
            shooter.team = 0
            if shooter is not owner:
                self.assertTrue(run.match.switch_hero(shooter, heroes()[HERO]))
            shooter.mover.position[:] = (x + index - 1.5, y, z + 1.5)
            shooter.mover.body = None
        for _ in range(30):
            run.step({})
        wall = self.cast(run, sym)
        pool = fight.vitals[sym.body]
        before = pool.total
        x, y, z = sym.mover.position
        commands = {p: (*aim(fight._eye(p), (x, y + 1, z)), 1) for p in shooters}
        for _ in range(580):
            run.step(commands)
            if wall.health == 0:
                break
        self.assertLess(run.frame, wall.expires)
        self.assertEqual(wall.health, 0)
        self.assertNotIn(sym.body, fight.symmetra_barriers.actors)
        self.assertFalse(wall.component.instances)
        self.assertLess(pool.total, before)
        self.assertTrue(any(p.client.messages(combat.HIT) for p in shooters))
