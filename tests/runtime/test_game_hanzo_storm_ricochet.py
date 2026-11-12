"""Actual Storm Arrow ricochet, lifetime and defense behavior."""

import os
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import test_game_hanzo as hero_tests
import test_game_hanzo_self_destruct as dva_tests
from test_game_combat import aim
from test_game_mover import box, wall
from test_game_hanzo_sonic import profile

import hero_delivery_arrow_deflection as arrow_deflection
import hero_delivery_sigma_grasp as sigma_grasp
from ow174.game import combat, matrix, zarya_barriers as zarya_barrier
from ow174.game.collision import World
from ow174.game.content import heroes


class HanzoStormRicochetTests(unittest.TestCase):
    def arena(self, *, second_wall=False, ally=False, behind_wall=False):
        run, hanzo, target = hero_tests.HanzoCombatTests().arena(8)
        fight = run.match.combat
        x, y, z = hanzo.mover.position
        triangles = box(x - 25, x + 25, y - 1, y, z - 25, z + 25)
        triangles += wall(x - 10, z + 4, x + 15, z + 4)
        if second_wall:
            triangles += wall(x + 6, z - 5, x + 6, z + 8)
        run.match.collision_world = World(triangles)
        target.mover.position[:] = (x + 7, y, z + (7 if behind_wall else 1))
        target.mover.body = None
        if ally:
            run.match.game_map = replace(run.match.game_map, free_for_all=False)
            target.team = hanzo.team
        for _ in range(15):
            run.step({})
        vitals = fight.vitals[target.body] = combat.Vitals(target.body, 500)
        return run, hanzo, target, vitals, (x, y, z)

    def fire(self, run, hanzo, angles, *, storm=True):
        fight = run.match.combat
        if storm:
            run.step({hanzo: (*angles, 16)})
            run.step({hanzo: (*angles, 0)})
            for _ in range(40):
                run.step({hanzo: (*angles, 1)})
                if fight.rockets:
                    break
        else:
            for _ in range(70):
                run.step({hanzo: (*angles, 1)})
            run.step({hanzo: (*angles, 0)})
        rocket, = fight.rockets
        self.assertEqual(rocket.shot.definition, 0x040000000000270F if storm else 0x0400000000000144)
        self.assertEqual(rocket.storm_ricochets, int(storm))
        return rocket

    def fire_final_sonic(self, run, hanzo, angles, *, advance_to_bounce=True):
        fight = run.match.combat
        fight.configure_sonic(profile())
        run.step({hanzo: (-32768, 0, 16)})
        run.step({})
        with patch.object(fight.projectiles, "fired", wraps=fight.projectiles.fired) as fired:
            for _ in range(110):
                run.step({hanzo: (-32768, 0, 1)})
                if fired.call_count == 4:
                    break
            self.assertEqual([call.args[3] for call in fired.call_args_list], [0x040000000000270F] * 4)
        run.step({hanzo: (*angles, 8)})
        run.step({})
        prior = list(fight.rockets)
        for _ in range(40):
            run.step({hanzo: (*angles, 1)})
            new = [arrow for arrow in fight.rockets if arrow not in prior]
            if new:
                break
        arrow, = new
        self.assertEqual(arrow.shot.definition, 0x040000000000020D)
        self.assertEqual((arrow.speed, arrow.direct.amount, arrow.storm_ricochets), (25, 70, 1))
        if not advance_to_bounce:
            return arrow
        for _ in range(20):
            run.step({})
            if arrow.storm_ricochets == 0:
                break
        self.assertEqual(arrow.storm_ricochets, 0)
        self.assertIn(arrow, fight.rockets)
        self.assertFalse(fight.sonars)
        return arrow

    def test_final_sonic_ricochet_is_stopped_by_projected_barrier(self):
        run, hanzo, target, vitals, (x, y, z) = self.arena()
        fight = run.match.combat
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        hanzo.team, target.team = 0, 1
        fixture = dva_tests.HanzoSelfDestructTests()
        zarya = fixture.add_player(run, target, zarya_barrier.HERO, x + 10, z + 1)
        attack = aim(fight._eye(hanzo), (x + 4, y + 1.55, z + 4))
        arrow = self.fire_final_sonic(run, hanzo, attack)
        lease = arrow.shot
        xh, yh, zh = target.mover.position
        shield_aim = aim(fight._eye(zarya), (xh, yh + 1, zh))
        run.step({zarya: (*shield_aim, 16)})
        self.assertEqual(fight.zarya_barriers.projected[zarya.body][1:], (200, target.body))
        for _ in range(30):
            run.step({})
            if arrow not in fight.rockets:
                break
        self.assertNotIn(arrow, fight.rockets)
        self.assertNotIn(lease, fight.projectiles.shots)
        self.assertIsNone(arrow.shot)
        self.assertEqual(fight.zarya_barriers.projected[zarya.body][1:], (130, target.body))
        self.assertEqual(vitals.total, 500)
        self.assertFalse(hanzo.client.messages(combat.HIT))
        self.assertFalse(fight.sonars)

    def test_angled_bounce_hits_outside_initial_line_and_keeps_damage_source(self):
        for upper in (False, True):
            with self.subTest(upper=upper):
                run, hanzo, target, vitals, (x, y, z) = self.arena()
                fight = run.match.combat
                angles = aim(fight._eye(hanzo), (x + 4, y + (1.6 if upper else 1.25), z + 4))
                rocket = self.fire(run, hanzo, angles)
                shot = rocket.shot
                for _ in range(8):
                    run.step({})
                    if not rocket.storm_ricochets:
                        break
                self.assertEqual(rocket.storm_ricochets, 0)
                self.assertIn(rocket, fight.rockets)
                self.assertIs(rocket.shot, shot)
                self.assertIsNone(shot.release_at)
                self.assertGreater(rocket.direction[0], 0)
                self.assertLess(rocket.direction[2], 0)
                for _ in range(16):
                    run.step({})
                self.assertNotIn(rocket, fight.rockets)
                self.assertAlmostEqual(rocket.range_left + rocket.distance_flown, 1000)
                damage = 140 if upper else 70
                self.assertEqual(vitals.total, 500 - damage)
                hit, = hanzo.client.messages(combat.HIT)
                self.assertEqual(hit["+0x78"], {"+0x0": {"+0x0": target.body}, "+0x4": damage,
                                                "+0x8": combat.CRITICAL if upper else 0})
                self.assertEqual(rocket.source_entity, hanzo.body)

    def test_second_wall_ally_and_wall_behind_target_controls(self):
        for options in ({"second_wall": True}, {"ally": True}, {"behind_wall": True}, {}):
            with self.subTest(options=options):
                run, hanzo, _, vitals, (x, y, z) = self.arena(**options)
                fight = run.match.combat
                angles = aim(fight._eye(hanzo), (x + 4, y + 1.25, z + 4))
                rocket = self.fire(run, hanzo, angles, storm=bool(options))
                for _ in range(25):
                    run.step({})
                self.assertEqual(vitals.total, 500)
                self.assertFalse(hanzo.client.messages(combat.HIT))
                if options.get("second_wall"):
                    self.assertNotIn(rocket, fight.rockets)
                    self.assertAlmostEqual(rocket.position[0], x + 6, delta=.01)
                if not options:
                    self.assertNotIn(rocket, fight.rockets)
                    self.assertAlmostEqual(rocket.position[2], z + 4, delta=.01)

    def test_combined_sonic_keeps_storm_bounce_and_reveals_at_the_terminal_hit(self):
        for storm in (False, True):
            with self.subTest(storm=storm):
                run, hanzo, target, vitals, (x, y, z) = self.arena()
                fight = run.match.combat
                fight.configure_sonic(profile())
                angles = aim(fight._eye(hanzo), (x + 4, y + 1.25, z + 4))
                run.step({hanzo: (*angles, 24 if storm else 8)})
                for _ in range(25):
                    run.step({})
                for _ in range(70):
                    run.step({hanzo: (*angles, 1)})
                    if fight.rockets:
                        break
                if not fight.rockets:
                    run.step({hanzo: (*angles, 0)})
                rocket, = fight.rockets
                self.assertEqual(rocket.shot.definition, 0x0400000000000149)
                self.assertEqual((rocket.direct.amount, rocket.storm_ricochets),
                                 (70, 1) if storm else (125, 0))
                if storm:
                    for _ in range(8):
                        run.step({})
                        if not rocket.storm_ricochets:
                            break
                    self.assertIn(rocket, fight.rockets)
                    self.assertFalse(fight.sonars)
                for _ in range(16):
                    run.step({})
                self.assertNotIn(rocket, fight.rockets)
                beacon, = fight.sonars
                self.assertEqual(beacon.attached, target.body if storm else None)
                self.assertEqual(vitals.total, 430 if storm else 500)
                if storm:
                    self.assertTrue(fight.revealed_to(hanzo, target))

    def test_skipped_world_ticks_preserve_ricochet_damage_and_path(self):
        results = []
        for gap in (False, True):
            run, hanzo, _, vitals, (x, y, z) = self.arena()
            fight = run.match.combat
            angles = aim(fight._eye(hanzo), (x + 4, y + 1.25, z + 4))
            rocket = self.fire(run, hanzo, angles)
            run.step({})
            if gap:
                run.frame += 18
                run.match.tick = run.frame
                fight.update(run.frame * combat.TICK, run.frame, frames=False)
            else:
                for _ in range(18):
                    run.step({})
            self.assertNotIn(rocket, fight.rockets)
            self.assertEqual(vitals.total, 430)
            results.append((rocket.position, rocket.range_left, rocket.flown))
        self.assertEqual(results[0], results[1])

    def test_final_sonic_world_gap_preserves_terminal_contact_and_cooldowns(self):
        for outcome in ("body", "wall", "barrier", "new_barrier"):
            with self.subTest(outcome=outcome):
                shielded = outcome in ("barrier", "new_barrier")
                results = []
                for gap in (False, True):
                    run, hanzo, target, vitals, (x, y, z) = self.arena(second_wall=outcome == "wall")
                    match = run.match
                    fight = match.combat
                    if shielded:
                        match.game_map = replace(match.game_map, free_for_all=False)
                        hanzo.team, target.team = 0, 1
                        zarya = dva_tests.HanzoSelfDestructTests().add_player(
                            run, target, zarya_barrier.HERO, x + 10, z + 1,
                        )
                    attack = aim(fight._eye(hanzo), (x + 4, y + 1.55, z + 4))
                    arrow = self.fire_final_sonic(run, hanzo, attack, advance_to_bounce=False)
                    if shielded:
                        tx, ty, tz = target.mover.position
                        shield_aim = aim(fight._eye(zarya), (tx, ty + 1, tz))
                        run.step({zarya: (*shield_aim, 16)})
                        self.assertEqual(fight.zarya_barriers.projected[zarya.body][1:], (200, target.body))
                    else:
                        run.step({})
                    if outcome == "barrier":
                        run.step({})  # Admit the projected barrier before the skipped ticks.
                    self.assertIn(arrow, fight.rockets)
                    self.assertEqual(arrow.storm_ricochets, 1)
                    self.assertFalse(fight.sonars)

                    if gap:
                        run.frame += 36
                        match.update(run.frame * combat.TICK, run.frame)
                    else:
                        for _ in range(36):
                            run.step({})
                    run.step({})

                    self.assertNotIn(arrow, fight.rockets)
                    self.assertEqual(arrow.storm_ricochets, 0)
                    protected = shielded and (outcome == "barrier" or not gap)
                    body_hit = outcome != "wall" and not protected
                    self.assertEqual(vitals.total, 430 if body_hit else 500)
                    hits = [hit["+0x78"] for hit in hanzo.client.messages(combat.HIT)]
                    expected_hit = {"+0x0": {"+0x0": target.body}, "+0x4": 70, "+0x8": 0}
                    self.assertEqual(hits, [expected_hit] if body_hit else [])
                    self.assertAlmostEqual(arrow.range_left + arrow.distance_flown, 1000)
                    beacon_state = None
                    if shielded:
                        self.assertEqual(fight.zarya_barriers.projected[zarya.body][1:],
                                         (130 if protected else 200, target.body))
                    if protected:
                        self.assertFalse(fight.sonars)
                    else:
                        beacon, = fight.sonars
                        self.assertEqual(beacon.source_entity, hanzo.body)
                        self.assertEqual(beacon.attached, target.body if body_hit else None)
                        self.assertTrue(fight.revealed_to(hanzo, target))
                        beacon_state = (beacon.born, beacon.position, beacon.attached)
                    instances = hanzo.body_script.component.instances.values()
                    storm = next(i for i in instances if i.graph.index == 0x146A)
                    sonic = next(i for i in instances if i.graph.index == 0x00C1)
                    results.append((arrow.position, arrow.flown, arrow.range_left, beacon_state,
                                    storm.vars[61].value(), sonic.vars[61].value()))
                # A new cast enters at the catch-up endpoint and cannot protect
                # a body from earlier flight. Neither outcome restarts cooldowns.
                self.assertEqual(results[0][-2:], results[1][-2:])
                if outcome != "new_barrier":
                    self.assertEqual(results[0], results[1])

    def test_projected_barrier_stops_a_ricochet_before_body_contact(self):
        run, hanzo, target, vitals, (x, y, z) = self.arena()
        fight = run.match.combat
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        hanzo.team, target.team = 0, 1
        fixture = dva_tests.HanzoSelfDestructTests()
        zarya = fixture.add_player(run, target, zarya_barrier.HERO, x + 10, z + 1)
        xh, yh, zh = target.mover.position
        shield_aim = aim(fight._eye(zarya), (xh, yh + 1, zh))
        run.step({zarya: (*shield_aim, 16)})
        self.assertEqual(fight.zarya_barriers.projected[zarya.body][1:], (200, target.body))
        angles = aim(fight._eye(hanzo), (x + 4, y + 1.25, z + 4))
        rocket = self.fire(run, hanzo, angles)
        for _ in range(20):
            run.step({})
        self.assertEqual(rocket.storm_ricochets, 0)
        self.assertNotIn(rocket, fight.rockets)
        self.assertEqual(fight.zarya_barriers.projected[zarya.body][1:], (130, target.body))
        self.assertEqual(vitals.total, 500)
        self.assertFalse(hanzo.client.messages(combat.HIT))

    def test_combined_sonic_reveals_on_body_hit_before_first_wall(self):
        run, hanzo, target, vitals, (x, y, z) = self.arena()
        fight = run.match.combat
        fight.configure_sonic(profile())
        target.mover.position[:] = (x + 2, y, z + 2)
        target.mover.body = None
        angles = aim(fight._eye(hanzo), (x + 4, y + 1.05, z + 4))
        run.step({hanzo: (*angles, 24)})
        for _ in range(25):
            run.step({})
        for _ in range(40):
            run.step({hanzo: (*angles, 1)})
            if fight.rockets:
                break
        rocket, = fight.rockets
        self.assertEqual(rocket.storm_ricochets, 1)
        for _ in range(14):
            run.step({})
        self.assertNotIn(rocket, fight.rockets)
        self.assertEqual(rocket.storm_ricochets, 1)
        self.assertEqual(vitals.total, 430)
        beacon, = fight.sonars
        self.assertEqual(beacon.attached, target.body)
        self.assertTrue(fight.revealed_to(hanzo, target))

    def test_genji_deflects_after_bounce_without_restoring_the_budget(self):
        run, hanzo, genji, _, (x, y, z) = self.arena()
        fight = run.match.combat
        position = tuple(genji.mover.position)
        self.assertTrue(run.match.switch_hero(genji, heroes()[arrow_deflection.GENJI]))
        genji.mover.position[:] = position
        genji.mover.body = None
        for _ in range(80):
            run.step({})
        target = next(t for t in fight._targets() if t.owner is genji)
        origin = fight._eye(genji)
        defense = aim(origin, (x + 4, origin[1], z + 4))
        run.step({genji: (*defense, 16)})
        self.assertIsNotNone(arrow_deflection.active_volume(target, 0x03A3, 10))
        angles = aim(fight._eye(hanzo), (x + 4, y + 1.25, z + 4))
        rocket = self.fire(run, hanzo, angles)
        for _ in range(20):
            run.step({genji: (*defense, 0)})
        self.assertIs(rocket.shooter, genji)
        self.assertEqual(rocket.source_entity, genji.body)
        self.assertEqual(rocket.storm_ricochets, 0)
        self.assertNotIn(rocket, fight.rockets)
        self.assertAlmostEqual(rocket.position[2], z + 4, delta=.01)
        self.assertFalse(hanzo.client.messages(combat.HIT))
        self.assertFalse(genji.client.messages(combat.HIT))

    def test_genji_deflects_final_sonic_after_bounce_without_restoring_ricochet(self):
        run, hanzo, genji, _, (x, y, z) = self.arena()
        fight = run.match.combat
        position = tuple(genji.mover.position)
        self.assertTrue(run.match.switch_hero(genji, heroes()[arrow_deflection.GENJI]))
        genji.mover.position[:] = position
        genji.mover.body = None
        for _ in range(80):
            run.step({})
        attack = aim(fight._eye(hanzo), (x + 4, y + 1.55, z + 4))
        arrow = self.fire_final_sonic(run, hanzo, attack)
        lease = arrow.shot
        origin = fight._eye(genji)
        defense = aim(origin, (x + 4, origin[1], z + 4))
        run.step({genji: (*defense, 16)})
        target = next(t for t in fight._targets() if t.owner is genji)
        self.assertIsNotNone(arrow_deflection.active_volume(target, 0x03A3, 10))
        for _ in range(35):
            run.step({genji: (*defense, 0)})
            if arrow not in fight.rockets:
                break
        self.assertNotIn(arrow, fight.rockets)
        self.assertNotIn(lease, fight.projectiles.shots)
        self.assertIsNone(arrow.shot)
        self.assertIs(arrow.shooter, genji)
        self.assertEqual(arrow.source_entity, genji.body)
        self.assertEqual(arrow.storm_ricochets, 0)
        self.assertAlmostEqual(arrow.position[2], z + 4, delta=0.01)
        self.assertFalse(hanzo.client.messages(combat.HIT))
        self.assertFalse(genji.client.messages(combat.HIT))
        beacon, = fight.sonars
        self.assertEqual((beacon.source_entity, beacon.attached), (genji.body, None))
        run.step({})
        self.assertTrue(fight.revealed_to(genji, hanzo))

    def test_sonic_ricochet_is_absorbed_without_spawning_a_beacon(self):
        self.verify_sonic_ricochet_absorption(final=False)

    def test_final_sonic_ricochet_is_absorbed_without_spawning_a_beacon(self):
        self.verify_sonic_ricochet_absorption(final=True)

    def verify_sonic_ricochet_absorption(self, *, final):
        for defender, button in ((matrix.DVA, 0), (matrix.DVA, 2), (sigma_grasp.SIGMA, 0), (sigma_grasp.SIGMA, 8)):
            with self.subTest(defender=defender, button=button):
                run, hanzo, target, _, (x, y, z) = self.arena()
                fight = run.match.combat
                if not final:
                    fight.configure_sonic(profile())
                catalog = os.environ.get("ORPHEUS_MATRIX_TAGS")
                if not catalog:
                    self.skipTest("requires prepared Matrix projectile tags")
                fight.matrix_definitions = matrix.load_admitted_definitions(Path(catalog))
                target.spawn = (x + 9, y, z - 1), 0
                self.assertTrue(run.match.switch_hero(target, heroes()[defender]))
                mech = target.controlled_mech
                if defender == matrix.DVA:
                    self.assertIsNotNone(mech)
                for _ in range(80):
                    run.step({})

                attack = aim(fight._eye(hanzo), (x + 4, y + (1.65 if final else 1.25), z + 4))
                defense = aim((x + 9, y + 1.25, z - 1), (x + 4, y + 1.25, z + 4))
                if final:
                    rocket = self.fire_final_sonic(run, hanzo, attack)
                else:
                    run.step({hanzo: (*attack, 24)})
                    run.step({hanzo: (*attack, 0)})
                    for _ in range(40):
                        run.step({hanzo: (*attack, 1)})
                        if fight.rockets:
                            break
                    rocket, = fight.rockets
                    self.assertEqual(rocket.shot.definition, 0x0400000000000149)
                    for _ in range(8):
                        run.step({})
                        if rocket.storm_ricochets == 0:
                            break
                    self.assertEqual(rocket.storm_ricochets, 0)
                    self.assertIn(rocket, fight.rockets)
                self.assertTrue(rocket.matrix_absorbable)
                lease = rocket.shot

                for _ in range(160):
                    run.step({target: (*defense, button)})
                self.assertNotIn(rocket, fight.rockets)
                target_body = target.body if mech is None else mech.entity
                vitals = fight.vitals[target_body]
                full_hp = 400 if mech is None else 600
                if button:
                    self.assertIsNone(rocket.shot)
                    self.assertNotIn(lease, fight.projectiles.shots)
                    self.assertFalse(fight.sonars)
                    self.assertFalse(hanzo.client.messages(combat.HIT))
                    self.assertEqual(vitals.bonus_shields, 42 if mech is None else 0)
                    self.assertEqual(vitals.total - vitals.bonus_shields, full_hp)
                else:
                    beacon, = fight.sonars
                    self.assertEqual(beacon.attached, target_body)
                    self.assertEqual(vitals.total, full_hp - (70 if mech is None else 65))

    def test_ricochet_shares_the_authored_path_limit(self):
        run, hanzo, target, _, (x, _, z) = self.arena(behind_wall=True)
        fight = run.match.combat
        target.spawned = False
        rocket = self.fire(run, hanzo, (8192, 0))
        for _ in range(8):
            run.step({})
            if not rocket.storm_ricochets:
                break
        self.assertIn(rocket, fight.rockets)
        self.assertEqual(rocket.storm_ricochets, 0)
        self.assertLess(rocket.range_left, 1000)
        self.assertGreater(rocket.distance_flown, 0)
        run.frame += 2000
        run.match.tick = run.frame
        fight.update(run.frame * combat.TICK, run.frame, frames=False)
        self.assertNotIn(rocket, fight.rockets)
        self.assertAlmostEqual(rocket.range_left, 0, places=6)
        self.assertAlmostEqual(rocket.distance_flown, 1000, places=6)
        self.assertLess(rocket.flown, 20)
        self.assertAlmostEqual(x - z, rocket.position[0] + rocket.position[2] - 2 * (z + 4), delta=.01)
