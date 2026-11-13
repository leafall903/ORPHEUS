"""Roadhog lane regressions; these do not establish complete hero/client acceptance."""

import math
import os
import unittest
from dataclasses import replace
from itertools import pairwise
from pathlib import Path
from unittest.mock import patch

from test_game_combat import KANEZAKA_DM, Run, aim, in_world

from ow174.game import combat, stats
from ow174.game.collision import World, flat_world
from ow174.game.commands import Command
from ow174.game.content import PRACTICE_RANGE, game_map, heroes
from ow174.game.script.driver import ULT_CHARGE, ULT_COST

ROADHOG = 0x02E0000000000040


def torso_aim(run, hog, enemy):
    x, y, z = enemy.mover.position
    return aim(run.match.combat._eye(hog), (x, y + 0.7, z))


@unittest.skipUnless(os.environ.get("ORPHEUS_TEST_MINE_COLLISION"), "build 104319 map caches required")
class RoadhogMapCombatTests(unittest.TestCase):
    def maps(self):
        kings_row = game_map(0x08000000000000D4, KANEZAKA_DM.mode_guid, free_for_all=True)
        self.assertIsNotNone(kings_row)
        yield PRACTICE_RANGE, (54.56507, 1, 42.14788), 32, 19, 8.5
        yield kings_row, (-85.048645, .99401355, -6.1768403), 34, 0, 5

    def scene(self, level, world, anchor, bearing, distance):
        run = Run(level, ("Roadhog", "Enemy"))
        match = run.match
        match.bots.clear()
        match._pending_bot_scripts.clear()
        match.collision_world = world
        hog, enemy = run.players
        self.assertTrue(match.switch_hero(hog, heroes()[ROADHOG]))
        hog.body_script.ult_seconds = 0
        x, y, z = anchor
        angle = bearing * math.tau / 64
        tx, tz = x + math.sin(angle) * distance, z + math.cos(angle) * distance
        for player, px, pz in ((hog, x, z), (enemy, tx, tz)):
            floor = world.ground(px, pz, y + 1, y - 1)
            self.assertIsNotNone(floor)
            player.mover.position[:] = (px, floor.y, pz)
            player.mover.body = None
        for _ in range(30):
            run.step({})
        fight = match.combat
        targets = {target.owner: target for target in fight._targets()}
        pool = combat.Vitals(enemy.body, 20000)
        fight.vitals[enemy.body] = pool
        weapon = next(i for i in hog.body_script.component.instances.values() if i.graph.index == 0x01C6)
        return run, hog, enemy, targets, pool, weapon

    def load(self, level):
        root = Path(os.environ["ORPHEUS_TEST_MINE_COLLISION"])
        return World.load(root / f"{level.map_guid & 0xFFFF:04X}.col")

    def test_real_maps_execute_scrap_melee_hook_whole_hog_and_breather(self):
        cases = (("primary", 1, 2, 165), ("slug", 2, 2, 50), ("fragments", 2, 10, 165),
                 ("melee", 0x800, 1.4, 30), ("hook", 8, 6, 30), ("ultimate", 32, 3, None))
        for level, anchor, clear, _, _ in self.maps():
            world = self.load(level)
            for name, button, distance, damage in cases:
                with self.subTest(map=level.name, attack=name):
                    run, hog, enemy, _, pool, weapon = self.scene(level, world, anchor, clear, distance)
                    fight = run.match.combat
                    if button == 32:
                        script = hog.body_script
                        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
                    look = torso_aim(run, hog, enemy)
                    run.step({hog: (*look, button)})
                    pulled = pushed = False
                    for _ in range(80 if button in (8, 32) else 25):
                        run.step({hog: (*look, 0)})
                        pulled |= fight.hooks.controlled(enemy.body)
                        pushed |= enemy.body in fight.impulses.live
                    if damage is None:
                        self.assertLess(pool.total, 20000)
                        self.assertTrue(pushed)
                        self.assertEqual(hog.body_script.component.entity_var(ULT_CHARGE).value(), 0)
                    else:
                        self.assertAlmostEqual(20000 - pool.total, damage, places=3)
                    self.assertEqual(pulled, button == 8)
                    self.assertFalse(fight.hooks.controlled(enemy.body))
                    self.assertEqual(weapon.vars[53].value(), 4 if button in (1, 2) else 5)
            with self.subTest(map=level.name, attack="breather"):
                run, hog, enemy, targets, _, _ = self.scene(level, world, anchor, clear, 4)
                fight = run.match.combat
                pool = fight.vitals_of(targets[hog])
                run.step({enemy: (*torso_aim(run, enemy, hog), 1)})
                self.assertEqual(pool.total, 580)
                run.step({hog: (0, 0, 16)})
                self.assertEqual(fight.incoming_damage_scale(hog), .5)
                for _ in range(185):
                    run.step({})
                self.assertEqual(pool.total, 600)
                self.assertEqual(hog.body_script.component.entity_var(ULT_CHARGE).value(), 20)
                self.assertEqual(fight.incoming_damage_scale(hog), 1)

    def test_real_map_cover_blocks_slug_and_hook_before_a_target(self):
        for level, anchor, _, blocked, distance in self.maps():
            world = self.load(level)
            for button in (2, 8):
                with self.subTest(map=level.name, button=button):
                    run, hog, enemy, _, pool, weapon = self.scene(level, world, anchor, blocked, distance)
                    fight = run.match.combat
                    look = torso_aim(run, hog, enemy)
                    wall = combat.ray_world(world, fight._eye(hog), combat.aim(*look), distance - 1)
                    self.assertIsNotNone(wall)
                    run.step({hog: (*look, button)})
                    for _ in range(65):
                        run.step({hog: (*look, 0)})
                        self.assertFalse(fight.hooks.controlled(enemy.body))
                    self.assertEqual(pool.total, 20000)
                    self.assertFalse(hog.client.messages(combat.HIT))
                    self.assertEqual(weapon.vars[53].value(), 4 if button == 2 else 5)


class RoadhogProjectileTests(unittest.TestCase):
    def test_real_primary_volley_hits_with_25_pellets_and_consumes_one_round(self):
        run = Run(KANEZAKA_DM, ("Roadhog", "Enemy"))
        match = run.match
        match.bots.clear()
        match._pending_bot_scripts.clear()
        hog, enemy = run.players
        match.switch_hero(hog, heroes()[ROADHOG])
        x, y, z = hog.mover.position
        enemy.mover.position = [x, y, z + 2]
        fight = match.combat
        vitals = fight.vitals_of(next(t for t in fight._targets() if t.owner is enemy))
        weapon = next(i for i in hog.body_script.component.instances.values() if i.graph.index == 0x01C6)
        ammo = weapon.vars[53].value()
        run.step({hog: (*torso_aim(run, hog, enemy), 1)})
        self.assertEqual(len(fight.rockets), 25)
        self.assertEqual(weapon.vars[53].value(), ammo - 1)
        for _ in range(15):
            run.step({})
        self.assertAlmostEqual(200 - vitals.total, 25 * 6.6, places=4)
        self.assertEqual(len(hog.client.messages(combat.HIT)), 25)

    def test_primary_miss_team_filter_and_real_wall_do_not_damage(self):
        for mode in ("miss", "ally", "wall"):
            with self.subTest(mode=mode):
                run = Run(replace(KANEZAKA_DM, free_for_all=False), ("Roadhog", "Target"))
                match = run.match
                match.bots.clear()
                match._pending_bot_scripts.clear()
                hog, enemy = run.players
                match.switch_hero(hog, heroes()[ROADHOG])
                if mode == "ally":
                    enemy.team = hog.team
                x, y, z = hog.mover.position
                enemy.mover.position = [x, y, z + 2]
                fight = match.combat
                vitals = fight.vitals_of(next(t for t in fight._targets() if t.owner is enemy))
                wall = World([
                    (x - 5, y, z + 1, x + 5, y, z + 1, x + 5, y + 5, z + 1),
                    (x - 5, y, z + 1, x + 5, y + 5, z + 1, x - 5, y + 5, z + 1),
                ])
                with patch.object(fight, "_collision", return_value=wall if mode == "wall" else None):
                    run.step({hog: (-32768 if mode == "miss" else 0, 0, 1)})
                    for _ in range(20):
                        run.step({})
                self.assertEqual(vitals.total, 200.0)
                self.assertEqual(hog.client.messages(combat.HIT), [])

    def test_actual_pellet_contact_on_either_side_of_authored_range(self):
        for button in (1, 2):
            for offset in (-0.01, 0.01):
                with self.subTest(button=button, offset=offset):
                    run, hog, enemy, weapon, vitals = RoadhogWeaponResourceTests().session()
                    fight = run.match.combat
                    x, y, z = hog.mover.position
                    run.match.collision_world = flat_world(y)
                    enemy.mover.position[:] = (x, y, z + (100 if button == 1 else 109))
                    enemy.mover.body = None
                    run.step({})
                    angles = torso_aim(run, hog, enemy)
                    run.step({hog: (*angles, button)})
                    if button == 2:
                        parent, = fight.rockets
                        self.assertAlmostEqual(parent.speed * parent.left, 9, places=5)
                        for _ in range(12):
                            run.step({})
                            if parent not in fight.rockets:
                                break
                        self.assertNotIn(parent, fight.rockets)
                    self.assertEqual(len(fight.rockets), 25)
                    forward = combat.aim(*angles)
                    pellet = max(fight.rockets, key=lambda r: sum(a * b for a, b in
                                                                zip(r.direction, forward, strict=True)))
                    self.assertEqual(pellet.speed, 80)
                    remaining = pellet.speed * pellet.left
                    reach = pellet.speed * (pellet.left + pellet.flown)
                    self.assertAlmostEqual(reach, 100)
                    target = next(t for t in fight._targets() if t.owner is enemy)
                    a, b = target.segment()
                    contact = combat.ray_capsule(pellet.position, pellet.direction, a, b, target.radius)
                    self.assertIsNotNone(contact)
                    adjustment = remaining + offset - contact
                    enemy.mover.position[:] = tuple(p + d * adjustment for p, d in
                                                    zip(target.feet, pellet.direction, strict=True))
                    enemy.mover.body = None
                    for _ in range(90):
                        run.step({})
                    self.assertEqual(weapon.vars[53].value(), 4)
                    self.assertFalse(fight.rockets)
                    expected = pellet.direct.amount * pellet.direct.scale(reach + offset) if offset < 0 else 0
                    self.assertAlmostEqual(20000 - vitals.total, expected, places=5)
                    self.assertEqual(len(hog.client.messages(combat.HIT)), int(offset < 0))

    def test_real_melee_range_aim_team_and_triangle_wall(self):
        for case in ("hit", "out_of_range", "away", "ally", "wall"):
            with self.subTest(case=case):
                run = Run(KANEZAKA_DM, ("Roadhog", "Enemy"))
                match = run.match
                match.bots.clear()
                match._pending_bot_scripts.clear()
                hog, enemy = run.players
                match.switch_hero(hog, heroes()[ROADHOG])
                if case == "ally":
                    match.game_map = replace(match.game_map, free_for_all=False)
                    enemy.team = hog.team
                x, y, z = hog.mover.position
                enemy.mover.position = [x, y, z + (3.5 if case == "out_of_range" else 1.5)]
                fight = match.combat
                vitals = fight.vitals_of(next(t for t in fight._targets() if t.owner is enemy))
                wall = World([
                    (x - 5, y, z + 0.5, x + 5, y, z + 0.5, x + 5, y + 5, z + 0.5),
                    (x - 5, y, z + 0.5, x + 5, y + 5, z + 0.5, x - 5, y + 5, z + 0.5),
                ])
                with patch.object(fight, "_collision", return_value=wall if case == "wall" else None):
                    run.step({hog: (-32768 if case == "away" else 0, 0, 0x800)})
                    for _ in range(25):
                        run.step({})
                self.assertEqual(vitals.total, 170.0 if case == "hit" else 200.0)

    def test_actual_melee_contact_boundary_and_recovery_after_miss(self):
        for offset in (-0.01, 0.01):
            with self.subTest(offset=offset):
                run, hog, enemy, weapon, vitals = RoadhogWeaponResourceTests().session()
                fight = run.match.combat
                x, y, z = hog.mover.position
                run.match.collision_world = flat_world(y)
                enemy.mover.position[:] = (x, y, z + 3)
                enemy.mover.body = None
                run.step({})
                target = next(t for t in fight._targets() if t.owner is enemy)
                a, b = target.segment()
                contact = combat.ray_capsule(fight._eye(hog), (0, 0, 1), a, b, target.radius + 1)
                self.assertIsNotNone(contact)
                enemy.mover.position[2] += 1.5 + offset - contact
                enemy.mover.body = None
                run.step({})
                run.step({hog: (0, 0, 0x800)})
                expected = 20000 - (30 if offset < 0 else 0)
                self.assertEqual(vitals.total, expected)

                enemy.mover.position[:] = (x, y, z + 1.5)
                enemy.mover.body = None
                run.step({})
                for frame in range(2, 75):
                    run.step({hog: (0, 0, 0x800)})
                    self.assertEqual(vitals.total, expected - (30 if frame >= 62 else 0))
                self.assertEqual(weapon.vars[53].value(), 5)

    def test_real_melee_buffers_only_held_repress_until_recovery(self):
        for hold in (False, True):
            with self.subTest(hold=hold):
                self.check_melee_repress(hold)

    def check_melee_repress(self, hold):
        run = Run(KANEZAKA_DM, ("Roadhog", "Enemy"))
        match = run.match
        match.bots.clear()
        match._pending_bot_scripts.clear()
        hog, enemy = run.players
        match.switch_hero(hog, heroes()[ROADHOG])
        x, y, z = hog.mover.position
        enemy.mover.position = [x, y, z + 1.5]
        fight = match.combat
        vitals = fight.vitals_of(next(t for t in fight._targets() if t.owner is enemy))
        weapon = next(i for i in hog.body_script.component.instances.values() if i.graph.index == 0x1C6)
        ammo = weapon.vars[53].value()

        run.step({hog: (0, 0, 0x800)})
        self.assertEqual(vitals.total, 170)
        run.step({})
        for offset in range(2, 130):
            run.step({hog: (0, 0, 0x800 if hold or offset == 2 else 0)})
            self.assertEqual(vitals.total, 140 if hold and offset >= 62 else 170)
        run.step({})
        run.step({hog: (0, 0, 0x800)})
        self.assertEqual(vitals.total, 110 if hold else 140)
        self.assertEqual(weapon.vars[53].value(), ammo)

    def pellet_hit(self, distance, *, critical=1.0, head=False, gap=False):
        run = Run(KANEZAKA_DM, ("Roadhog", "Enemy"))
        match = run.match
        match.bots.clear()
        match._pending_bot_scripts.clear()
        shooter, enemy = run.players
        match.switch_hero(shooter, heroes()[ROADHOG])
        x, y, z = shooter.mover.position
        enemy.mover.position = [x, y, z + distance]
        fight = match.combat
        target = next(t for t in fight._targets() if t.owner is enemy)
        vitals = fight.vitals_of(target)
        weapon = next(i for i in shooter.body_script.component.instances.values() if i.graph.index == 0x01C6)
        state = next(s for s in weapon.states.values() if s.node.pos == 26)
        direct = combat.hit_def(state, state.node.field("m_6396149F"))
        direct.critical = critical
        origin = (x, y + (combat.EYE if head else 1.0), z)
        rocket = combat.Rocket(shooter, shooter.team, origin, (0.0, 0.0, 1.0), 80.0, 1.25,
                               direct, None, last_tick=run.frame)
        fight.rockets.append(rocket)
        frames = (100,) if gap else range(1, 101)
        for advance in frames:
            frame = run.frame + advance
            match.update(frame * combat.TICK, frame)
        self.assertNotIn(rocket, fight.rockets)
        return 200.0 - vitals.total, direct, rocket, shooter.client.messages(combat.HIT)

    def test_authored_primary_pellet_falloff_reaches_actual_player_pools(self):
        for distance in (8.0, 22.0, 35.0):
            with self.subTest(distance=distance):
                damage, definition, rocket, _ = self.pellet_hit(distance)
                self.assertAlmostEqual(definition.amount, 6.6, places=5)
                expected = definition.amount * definition.scale(rocket.speed * rocket.flown)
                self.assertAlmostEqual(damage, expected)
                if distance > 15:
                    self.assertLess(damage, definition.amount)

    def test_skipped_ticks_preserve_total_distance_falloff(self):
        continuous, _, _, _ = self.pellet_hit(22.0)
        skipped, _, _, _ = self.pellet_hit(22.0, gap=True)
        self.assertAlmostEqual(continuous, skipped)

    def test_projectile_critical_multiplier_and_hit_notice_agree(self):
        # Explicit shared-mechanism fixture: Roadhog node26 has no authored critical
        # multiplier in this extracted input, so this does not invent a hero default.
        body_damage, _, _, body_hits = self.pellet_hit(22.0, critical=2.0)
        head_damage, definition, rocket, head_hits = self.pellet_hit(22.0, critical=2.0, head=True)
        self.assertGreater(head_damage, body_damage)
        self.assertAlmostEqual(
            head_damage, 2.0 * definition.amount * definition.scale(rocket.speed * rocket.flown),
        )
        self.assertEqual(body_hits[-1]["+0x78"]["+0x8"] & combat.CRITICAL, 0)
        self.assertEqual(head_hits[-1]["+0x78"]["+0x8"] & combat.CRITICAL, combat.CRITICAL)


class RoadhogWeaponResourceTests(unittest.TestCase):
    def session(self):
        run = Run(KANEZAKA_DM, ("Roadhog", "Enemy"))
        match = run.match
        match.bots.clear()
        match._pending_bot_scripts.clear()
        hog, enemy = run.players
        match.switch_hero(hog, heroes()[ROADHOG])
        x, y, z = hog.mover.position
        enemy.mover.position = [x, y, z + 2]
        weapon = next(i for i in hog.body_script.component.instances.values()
                      if i.graph.index == 0x01C6)
        # Retain a live enemy for several full volleys without changing the weapon.
        vitals = combat.Vitals(enemy.body, 20000)
        match.combat.vitals[enemy.body] = vitals
        return run, hog, enemy, weapon, vitals

    def test_held_primary_secondary_and_both_empty_reload_and_resume(self):
        for button, node, amount in ((1, 26, 165), (2, 29, 50), (3, 26, 165)):
            with self.subTest(button=button):
                run, hog, enemy, weapon, vitals = self.session()
                fight = run.match.combat
                shoot = fight._shoot
                shots, ammo = [], []

                def trace(*args, shots=shots, run=run, shoot=shoot, **kwargs):
                    shots.append((run.frame, args[2].node.pos))
                    return shoot(*args, **kwargs)

                start = run.frame
                with patch.object(fight, "_shoot", side_effect=trace):
                    for _ in range(430):
                        run.step({hog: (*torso_aim(run, hog, enemy), button)})
                        ammo.append(weapon.vars[53].value())
                self.assertEqual([n for _, n in shots], [node] * 6)
                self.assertEqual(ammo[0], 4)
                self.assertEqual(min(ammo), 0)
                self.assertTrue(all(0 <= value <= 5 for value in ammo))
                # The first magazine uses authored 0.85s recovery, quantized to
                # 16ms frames; this tests graph execution, not native agreement.
                gaps = [b[0] - a[0] for a, b in pairwise(shots[:5])]
                self.assertTrue(all(gap in (53, 54) for gap in gaps))
                empty = next(i for i, value in enumerate(ammo) if value == 0)
                refill = next(i for i in range(empty + 1, len(ammo)) if ammo[i] == 5)
                self.assertTrue(all(value == 0 for value in ammo[empty:refill]))
                self.assertGreater(shots[5][0], start + refill + 1)
                self.assertEqual(ammo[-1], 4)
                self.assertAlmostEqual(20000 - vitals.total, amount * 6, places=3)

    def test_manual_reload_fills_without_extra_shots_or_damage(self):
        run, hog, enemy, weapon, vitals = self.session()
        for frame in range(240):
            button = 1 if frame == 0 else 0x400 if frame == 60 else 0
            run.step({hog: (*torso_aim(run, hog, enemy), button)})
            if 1 <= frame < 60:
                self.assertEqual(weapon.vars[53].value(), 4)
        self.assertEqual(weapon.vars[53].value(), 5)
        self.assertAlmostEqual(20000 - vitals.total, 165, places=3)
        self.assertEqual(len(hog.client.messages(combat.HIT)), 25)

    def test_alternating_primary_secondary_share_one_magazine(self):
        run, hog, enemy, weapon, vitals = self.session()
        for frame in range(255):
            button = (1 if (frame // 60) % 2 == 0 else 2) if frame % 60 == 0 else 0
            # Try the other firing mode immediately after the fifth round.
            if frame == 248:
                button = 2
            run.step({hog: (*torso_aim(run, hog, enemy), button)})
        self.assertEqual(weapon.vars[53].value(), 0)
        self.assertAlmostEqual(20000 - vitals.total, 3 * 165 + 2 * 50, places=3)
        self.assertEqual(len(hog.client.messages(combat.HIT)), 3 * 25 + 2)

    def test_hook_and_breather_cancel_reload_without_delayed_refill(self):
        for interrupt in (8, 16):
            with self.subTest(interrupt=interrupt):
                run, hog, enemy, weapon, vitals = self.session()
                for frame in range(280):
                    button = (1 if frame == 0 else 0x400 if frame == 60
                              else interrupt if frame == 90 else 0)
                    angles = torso_aim(run, hog, enemy) if button == 1 else (0, 0)
                    run.step({hog: (*angles, button)})
                self.assertEqual(weapon.vars[53].value(), 4)
                # Hook also deals30 to the close enemy; Breather does not.
                self.assertAlmostEqual(20000 - vitals.total,
                                       195 if interrupt == 8 else 165, places=3)
                run.step({hog: (0, 0, 0x400)})
                for _ in range(180):
                    run.step({})
                self.assertEqual(weapon.vars[53].value(), 5)

    def test_whole_hog_cancels_manual_reload_and_refills_on_its_own_timeline(self):
        for manual_reload in (False, True):
            with self.subTest(manual_reload=manual_reload):
                run, hog, enemy, weapon, pool = self.session()
                run.step({hog: (*torso_aim(run, hog, enemy), 1)})
                for _ in range(59):
                    run.step({})
                run.step({hog: (0, 0, 0x400 if manual_reload else 0)})
                for _ in range(29):
                    run.step({})
                reload_wait = weapon.state(weapon.graph.node(65).state)
                self.assertEqual(reload_wait.active, manual_reload)
                self.assertEqual(weapon.vars[53].value(), 4)
                count = reload_wait.counter
                script = hog.body_script
                ultimate, = script.ultimates
                script.set_ult_charge(script.component.entity_var(ULT_COST).value())
                run.step({hog: (16384, 0, 32)})
                self.assertTrue(ultimate.flags & 1)
                self.assertFalse(reload_wait.active)
                self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)
                for elapsed in range(1, 438):
                    run.step({hog: (16384, 0, 0x400 if manual_reload and elapsed == 90 else 0)})
                    self.assertFalse(reload_wait.active)
                    self.assertEqual(reload_wait.counter, count)
                    self.assertEqual(weapon.vars[53].value(), 4 if elapsed < 374 else 5)
                    self.assertEqual(bool(ultimate.flags & 1), elapsed < 437)
                for _ in range(25):
                    run.step({})
                self.assertEqual(reload_wait.counter, count)
                self.assertEqual(weapon.vars[53].value(), 5)
                before = pool.total
                run.step({hog: (*torso_aim(run, hog, enemy), 1)})
                for _ in range(25):
                    run.step({})
                self.assertEqual(weapon.vars[53].value(), 4)
                self.assertAlmostEqual(pool.total, before - 165, places=3)

    def test_duplicate_and_old_commands_cannot_spend_ammo_or_repeat_hits(self):
        for button, amount, count in ((1, 165, 25), (2, 50, 1)):
            with self.subTest(button=button):
                run, hog, enemy, weapon, vitals = self.session()
                old = None
                for _ in range(120):
                    run.step({hog: (*torso_aim(run, hog, enemy), button)})
                    yaw, pitch = torso_aim(run, hog, enemy)
                    current = Command(run.frame, yaw=yaw, pitch=pitch, buttons=button)
                    if old is None:
                        old = current
                    before = (weapon.vars[53].value(), vitals.total,
                              len(hog.client.messages(combat.HIT)), len(run.match.combat.rockets))
                    hog.take_commands([current, current, old])
                    after = (weapon.vars[53].value(), vitals.total,
                             len(hog.client.messages(combat.HIT)), len(run.match.combat.rockets))
                    self.assertEqual(before, after)
                self.assertEqual(weapon.vars[53].value(), 2)
                self.assertAlmostEqual(20000 - vitals.total, amount * 3, places=3)
                self.assertEqual(len(hog.client.messages(combat.HIT)), count * 3)

    def test_command_gap_releases_weapon_hold_and_finishes_manual_reload_once(self):
        for button, amount in ((1, 165), (2, 50)):
            with self.subTest(button=button):
                run, hog, enemy, weapon, vitals = self.session()
                run.step({hog: (*torso_aim(run, hog, enemy), button)})
                self.assertEqual(weapon.vars[53].value(), 4)

                run.frame += 140
                run.match.update(run.frame * combat.TICK, run.frame)
                run.step({})
                self.assertEqual(weapon.vars[53].value(), 4)
                self.assertAlmostEqual(vitals.total, 20000-amount, places=3)

                run.step({hog: (0, 0, 0x400)})
                run.frame += 160
                run.match.update(run.frame * combat.TICK, run.frame)
                run.step({})
                self.assertEqual(weapon.vars[53].value(), 5)
                self.assertAlmostEqual(vitals.total, 20000-amount, places=3)
                run.step({hog: (*torso_aim(run, hog, enemy), button)})
                for _ in range(20):
                    run.step({})
                self.assertEqual(weapon.vars[53].value(), 4)
                self.assertAlmostEqual(vitals.total, 20000-2*amount, places=3)

    def test_whole_hog_does_not_replay_damage_after_command_gap_crosses_end(self):
        run, hog, enemy, weapon, vitals = self.session()
        script = hog.body_script
        script.ult_seconds = 0
        run.step({hog: (*torso_aim(run, hog, enemy), 2)})
        for _ in range(65):
            run.step({})
        self.assertEqual(vitals.total, 19950)
        self.assertEqual(weapon.vars[53].value(), 4)
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        run.step({hog: (*torso_aim(run, hog, enemy), 32)})
        for _ in range(65):
            run.step({hog: (*torso_aim(run, hog, enemy), 0)})
        self.assertLess(vitals.total, 19950)
        self.assertEqual(weapon.vars[53].value(), 4)
        command_frame = script.frame
        before = vitals.total
        run.frame += 550
        run.match.update(run.frame * combat.TICK, run.frame)
        self.assertEqual(script.frame, command_frame)
        self.assertEqual(weapon.vars[53].value(), 4)
        run.step({})
        self.assertEqual(vitals.total, before)
        self.assertEqual(weapon.vars[53].value(), 5)
        self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)
        x, y, z = hog.mover.position
        enemy.mover.position[:] = (x, y, z+2)
        enemy.mover.body = None
        run.step({})
        run.step({hog: (*torso_aim(run, hog, enemy), 1)})
        for _ in range(20):
            run.step({})
        self.assertEqual(weapon.vars[53].value(), 4)
        self.assertAlmostEqual(before-vitals.total, 165, places=3)

    def test_match_reentry_retires_active_hook_and_resets_weapon_state(self):
        run, hog, enemy, weapon, enemy_hp = self.session()
        match = run.match
        fight = match.combat
        x, y, z = hog.mover.position
        enemy.mover.position[:] = (x, y, z + 12)
        enemy.mover.body = None
        run.step({hog: (-32768, 0, 1)})
        for _ in range(60):
            run.step({})
        self.assertEqual(weapon.vars[53].value(), 4)
        angles = torso_aim(run, hog, enemy)
        run.step({hog: (*angles, 8)})
        for _ in range(40):
            if hog.body in fight.hooks.flights:
                break
            run.step({hog: (*angles, 0)})
        self.assertIn(hog.body, fight.hooks.flights)
        old_script = hog.body_script
        health = enemy_hp.total

        match.leave(hog, "test disconnect")
        run.players.remove(hog)
        self.assertTrue(old_script.retired)
        self.assertNotIn(hog.body, fight.hooks.flights)
        returning = match.add_player(hog.account_lo, hog.name, ROADHOG, hog.team, False)
        in_world(match, returning)
        run.players.append(returning)
        match.switch_hero(returning, heroes()[ROADHOG])
        self.assertNotEqual(returning.body, hog.body)
        returning.mover.position[:] = (x, y, z)
        returning.mover.body = None
        new_weapon = next(i for i in returning.body_script.component.instances.values()
                          if i.graph.index == 0x01C6)
        for _ in range(60):
            run.step({})
        self.assertEqual(enemy_hp.total, health)
        self.assertFalse(fight.hooks.controlled(enemy.body))
        self.assertEqual(new_weapon.vars[53].value(), 5)

        enemy.mover.position[:] = (x, y, z + 2)
        enemy.mover.body = None
        run.step({})
        run.step({returning: (*torso_aim(run, returning, enemy), 2)})
        for _ in range(25):
            run.step({})
        self.assertEqual(new_weapon.vars[53].value(), 4)
        self.assertEqual(enemy_hp.total, health - 50)

    def test_actual_lethal_shot_cancels_channels_and_respawns_without_stale_input(self):
        for button in (16, 32):
            with self.subTest(button=button):
                run, hog, enemy, _, enemy_hp = self.session()
                fight = run.match.combat
                old_script = hog.body_script
                old_body = hog.body
                if button == 32:
                    old_script.set_ult_charge(old_script.component.entity_var(ULT_COST).value())
                run.step({hog: (-32768, 0, button)})
                for _ in range(80):
                    run.step({hog: (-32768, 0, 0)})
                    if button == 16 and fight.incoming_damage_scale(hog) < 1:
                        break
                    if button == 32 and any(state.flags & 1 for state in old_script.ultimates):
                        break
                if button == 16:
                    self.assertLess(fight.incoming_damage_scale(hog), 1)
                else:
                    self.assertTrue(any(state.flags & 1 for state in old_script.ultimates))

                own = fight.vitals_of(next(t for t in fight._targets() if t.owner is hog))
                own.now[combat.HEALTH_POOL] = 1
                run.step({enemy: (*torso_aim(run, enemy, hog), 1)})
                for _ in range(25):
                    if own.dead:
                        break
                    run.step({})
                self.assertTrue(own.dead)
                self.assertFalse(any(state.flags & 1 for state in old_script.ultimates))
                self.assertEqual(fight.incoming_damage_scale(hog), 1)
                health = enemy_hp.total
                for _ in range(90):
                    run.step({hog: (*torso_aim(run, hog, enemy), button | 3 | 8)})
                self.assertTrue(fight.is_dead(hog))
                self.assertEqual(enemy_hp.total, health)

                for _ in range(600):
                    run.step({})
                    if not fight.is_dead(hog):
                        break
                self.assertFalse(fight.is_dead(hog))
                self.assertTrue(old_script.retired)
                self.assertNotEqual(hog.body, old_body)
                self.assertEqual(enemy_hp.total, health)
                self.assertNotIn(hog.body, fight.hooks.flights)
                self.assertEqual(fight.incoming_damage_scale(hog), 1)
                self.assertFalse(any(state.flags & 1 for state in hog.body_script.ultimates))
                weapon = next(i for i in hog.body_script.component.instances.values()
                              if i.graph.index == 0x01C6)
                self.assertEqual(weapon.vars[53].value(), 5)
                x, y, z = hog.mover.position
                enemy.mover.position[:] = (x, y, z + 2)
                enemy.mover.body = None
                run.step({})
                run.step({hog: (*torso_aim(run, hog, enemy), 2)})
                for _ in range(25):
                    run.step({})
                self.assertEqual(weapon.vars[53].value(), 4)
                self.assertEqual(enemy_hp.total, health - 50)

    def test_released_scrap_damage_respects_death_replacement_and_reentry(self):
        for phase, button, distance in (("primary", 1, 6), ("slug", 2, 6), ("fragments", 2, 16)):
            for transition in ("death", "switch", "reentry"):
                with self.subTest(phase=phase, transition=transition):
                    self.check_released_scrap_lifetime(phase, button, distance, transition)

    def check_released_scrap_lifetime(self, phase, button, distance, transition):
        run, hog, enemy, weapon, pool = self.session()
        match = run.match
        fight = match.combat
        x, y, z = hog.mover.position
        enemy.mover.position[:] = (x, y, z + distance)
        enemy.mover.body = None
        run.step({})
        owner = fight.vitals_of(next(t for t in fight._targets() if t.owner is hog))
        owner.damage(owner.total - 1)
        pool.damage(pool.total - 1)
        fight._sync_body_health(hog, owner)
        fight._sync_body_health(enemy, pool)
        old_body, old_script = hog.body, hog.body_script
        old_script.ult_seconds = 0
        old_script.set_ult_charge(0)
        attack = torso_aim(run, hog, enemy)
        counter = torso_aim(run, enemy, hog)
        run.step({hog: (*attack, button)})
        self.assertEqual(weapon.vars[53].value(), 4)
        if phase == "fragments":
            for _ in range(15):
                run.step({})
                if len(fight.rockets) == 25:
                    break
        self.assertEqual(len(fight.rockets), 1 if phase == "slug" else 25)
        self.assertTrue(all(rocket.shooter is hog for rocket in fight.rockets))
        self.assertEqual(pool.total, 1)

        if transition == "death":
            run.step({enemy: (*counter, 1)})
            self.assertTrue(owner.dead)
            self.assertEqual(owner.killer, enemy.body)
            self.assertFalse(pool.dead)
            self.assertTrue(fight.rockets)
        elif transition == "switch":
            self.assertTrue(match.switch_hero(hog, enemy.hero))
            self.assertFalse(fight.rockets)
            self.assertTrue(match.switch_hero(hog, heroes()[ROADHOG]))
        else:
            match.leave(hog, "test released Scrap Gun retirement")
            run.players.remove(hog)
            self.assertFalse(fight.rockets)
            fresh = match.add_player(hog.account_lo, hog.name, ROADHOG, hog.team, False)
            in_world(match, fresh)
            run.players.append(fresh)
            self.assertTrue(match.switch_hero(fresh, heroes()[ROADHOG]))
            hog = fresh

        if transition != "death":
            hog.body_script.ult_seconds = 0
        for _ in range(90):
            run.step({})
        self.assertFalse(fight.rockets)
        if transition == "death":
            self.assertTrue(pool.dead)
            self.assertEqual(pool.killer, old_body)
            self.assertEqual(old_script.component.entity_var(ULT_CHARGE).value(), 0)
            for _ in range(650):
                if hog.body != old_body:
                    break
                run.step({})
            self.assertFalse(fight.is_dead(hog))
        else:
            self.assertEqual(pool.total, 1)
        self.assertTrue(old_script.retired)
        self.assertNotEqual(hog.body, old_body)
        self.assertEqual(hog.body_script.component.entity_var(ULT_CHARGE).value(), 0)

    def test_dead_owner_cannot_fire_or_finish_reload_before_respawn(self):
        run, hog, enemy, weapon, vitals = self.session()
        for frame in range(90):
            button = 1 if frame == 0 else 0x400 if frame == 60 else 0
            run.step({hog: (*torso_aim(run, hog, enemy), button)})
        self.assertEqual(weapon.vars[53].value(), 4)
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is hog)
        fight._damage(enemy, target, 2000, False, None, run.frame)
        self.assertTrue(fight.is_dead(hog))
        ended = vitals.total
        body = hog.body
        for frame in range(180):
            run.step({hog: (0, 0, 3 if frame % 2 else 0x400)})
        self.assertEqual(hog.body, body)
        self.assertTrue(fight.is_dead(hog))
        self.assertEqual(weapon.vars[53].value(), 4)
        self.assertEqual(vitals.total, ended)

    def test_ready_fresh_primary_after_whole_hog_uses_live_full_partial_or_empty_magazine(self):
        for mode in ("full", "partial", "empty"):
            with self.subTest(magazine=mode):
                run, hog, enemy, weapon, vitals = self.session()
                if mode == "partial":
                    run.step({hog: (0, 0, 1)})
                    for _ in range(60):
                        run.step({})
                elif mode == "empty":
                    for _ in range(400):
                        run.step({hog: (0, 0, 1)})
                        if weapon.vars[53].value() == 0:
                            break
                self.assertEqual(weapon.vars[53].value(), {"full": 5, "partial": 4, "empty": 0}[mode])
                run.step({})
                primary = weapon.state(weapon.graph.node(26).state)
                before = primary.counter
                hog.body_script.set_ult_charge(2240)
                run.step({hog: (0, 0, 32)})
                ultimate, = hog.body_script.ultimates
                for _ in range(500):
                    run.step({hog: (0, 0, 1)})
                    if ultimate.flags & 1:
                        self.assertEqual(primary.counter, before)
                self.assertFalse(ultimate.flags & 1)
                for _ in range(100):
                    run.step({})  # finish authored normal-weapon recovery and release the old input
                # Whole Hog now physically pushes the target out of the fixed
                # primary ray. Restore this resource fixture's close shot pose.
                x, y, z = hog.mover.position
                enemy.mover.position[:] = (x, y, z+2)
                enemy.mover.body = None
                run.step({})
                ammo, health = weapon.vars[53].value(), vitals.total
                self.assertGreater(ammo, 0)
                run.step({hog: (*torso_aim(run, hog, enemy), 1)})
                for _ in range(25):
                    run.step({})
                self.assertEqual(weapon.vars[53].value(), ammo - 1)
                self.assertAlmostEqual(health - vitals.total, 165, places=3)


class RoadhogSelfHealTests(unittest.TestCase):
    def session(self, missing=400.0):
        run = Run(KANEZAKA_DM, ("Roadhog", "Enemy"))
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        hog, enemy = run.players
        run.match.switch_hero(hog, heroes()[ROADHOG])
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is hog)
        vitals = fight.vitals_of(target)
        vitals.damage(missing)
        fight._sync_body_health(hog, vitals)
        return run, hog, enemy, target, vitals

    def advance(self, run, hog, count):
        for _ in range(count):
            run.step({hog: (0, 0, 0)})

    def test_real_breather_input_heals_only_self_and_respects_windup(self):
        run, hog, enemy, _, vitals = self.session()
        fight = run.match.combat
        other = fight.vitals_of(next(t for t in fight._targets() if t.owner is enemy))
        other.damage(100)
        run.step({hog: (0, 0, 16)})
        self.advance(run, hog, 25)
        self.assertEqual(vitals.total, 200.0)
        self.advance(run, hog, 160)
        self.assertAlmostEqual(vitals.total, 500.0)
        self.assertEqual(other.total, 100.0)
        self.assertAlmostEqual(hog.stats.value(stats.HEALING), 300.0)

    def test_breather_catches_up_once_across_command_gap_without_replay(self):
        run, hog, _, _, vitals = self.session()
        fight = run.match.combat
        run.step({hog: (0, 0, 16)})
        self.advance(run, hog, 60)
        self.assertGreater(vitals.total, 200)
        self.assertLess(vitals.total, 500)
        instance = next(i for i in hog.body_script.component.instances.values() if i.graph.index == 0x0240)
        heal = instance.state(instance.graph.node(18).state)
        ability = instance.state(instance.graph.node(1).state)
        before = vitals.total
        run.frame += 160
        run.match.update(run.frame * combat.TICK, run.frame)
        self.assertEqual(vitals.total, before)
        run.step({})
        self.assertAlmostEqual(vitals.total, 500)
        self.assertEqual(heal.counter, 1)
        self.assertEqual(fight.incoming_damage_scale(hog), 1)
        self.assertGreater(ability.remaining(), 0)
        run.step({hog: (0, 0, 16)})
        self.advance(run, hog, 20)
        self.assertEqual(heal.counter, 1)
        self.assertAlmostEqual(hog.stats.value(stats.HEALING), 300)

    def test_overheal_is_capped_and_not_counted_as_healing(self):
        run, hog, _, _, vitals = self.session(missing=50)
        run.step({hog: (0, 0, 16)})
        self.advance(run, hog, 180)
        self.assertEqual(vitals.total, 600.0)
        self.assertAlmostEqual(hog.stats.value(stats.HEALING), 50.0)

    def test_ending_heal_state_stops_future_healing(self):
        run, hog, _, _, vitals = self.session()
        run.step({hog: (0, 0, 16)})
        self.advance(run, hog, 60)
        instance = next(i for i in hog.body_script.component.instances.values() if i.graph.index == 0x0240)
        state = next(s for s in instance.states.values() if s.node.pos == 18)
        self.assertTrue(state.active)
        self.assertGreater(vitals.total, 200)
        state.end(False)
        stopped = vitals.total
        self.advance(run, hog, 150)
        self.assertEqual(vitals.total, stopped)

    def test_death_during_heal_does_not_resurrect(self):
        run, hog, enemy, target, vitals = self.session()
        run.step({hog: (0, 0, 16)})
        self.advance(run, hog, 60)
        run.match.combat._damage(enemy, target, 2000, False, None, run.frame)
        self.assertTrue(vitals.dead)
        self.advance(run, hog, 150)
        self.assertEqual(vitals.total, 0)

    def test_breather_cooldown_rejects_repress_and_expires_before_reuse(self):
        run, hog, _, _, vitals = self.session()
        run.step({hog: (0, 0, 16)})
        self.advance(run, hog, 180)
        instance = next(i for i in hog.body_script.component.instances.values() if i.graph.index == 0x0240)
        heal = next(s for s in instance.states.values() if s.node.pos == 18)
        ability = next(s for s in instance.states.values() if s.node.pos == 1)
        self.assertEqual(heal.counter, 1)
        self.assertEqual(instance.find_var(0, 61).base, 8.0)
        self.assertGreater(ability.remaining(), 0.0)
        for _ in range(5):
            run.step({hog: (0, 0, 16)})
            run.step({hog: (0, 0, 0)})
        self.assertEqual(heal.counter, 1)
        self.assertAlmostEqual(vitals.total, 500.0)
        self.advance(run, hog, 450)
        self.assertEqual(ability.remaining(), 0.0)
        run.step({hog: (0, 0, 16)})
        self.advance(run, hog, 180)
        self.assertEqual(heal.counter, 2)
        self.assertEqual(vitals.total, 600.0)

    def test_breather_tap_buffers_only_inside_authored_cooldown_window(self):
        for threshold, casts in ((0.20, 1), (0.10, 2)):
            with self.subTest(threshold=threshold):
                run, hog, _, _, vitals = self.session()
                fight = run.match.combat
                run.step({hog: (0, 0, 16)})
                self.advance(run, hog, 180)
                instance = next(i for i in hog.body_script.component.instances.values()
                                if i.graph.index == 0x0240)
                ability = next(s for s in instance.states.values() if s.node.pos == 1)
                heal = next(s for s in instance.states.values() if s.node.pos == 18)
                for _ in range(600):
                    if ability.remaining() <= threshold:
                        break
                    self.advance(run, hog, 1)
                self.assertGreater(ability.remaining(), 0)
                self.assertLessEqual(ability.remaining(), threshold)
                run.step({hog: (0, 0, 16)})
                self.assertEqual(heal.counter, 1)
                self.assertEqual(fight.incoming_damage_scale(hog), 1)
                self.advance(run, hog, 180)
                self.assertEqual(heal.counter, casts)
                self.assertAlmostEqual(vitals.total, 500 if casts == 1 else 600)

    def test_breather_held_and_duplicate_inputs_do_not_bypass_cooldown(self):
        run, hog, _, _, vitals = self.session()
        run.step({hog: (0, 0, 16)})
        instance = next(i for i in hog.body_script.component.instances.values() if i.graph.index == 0x0240)
        ability = next(s for s in instance.states.values() if s.node.pos == 1)
        for _ in range(180):
            run.step({hog: (0, 0, 16)})
        heal = next(s for s in instance.states.values() if s.node.pos == 18)
        self.assertEqual(heal.counter, 1)
        self.assertAlmostEqual(vitals.total, 500)
        self.assertGreater(ability.remaining(), 0)
        for _ in range(600):
            run.step({hog: (0, 0, 16)})
            hog.take_commands([Command(run.frame, buttons=0), Command(run.frame, buttons=16)])
            self.assertEqual(heal.counter, 1)
            if ability.remaining() == 0:
                break
        self.assertEqual(ability.remaining(), 0)
        for _ in range(35):
            run.step({hog: (0, 0, 16)})
        self.assertEqual(heal.counter, 1)
        self.assertAlmostEqual(vitals.total, 500)

        run.step({hog: (0, 0, 0)})
        run.step({hog: (0, 0, 16)})
        hog.take_commands([Command(run.frame, buttons=16)])
        self.advance(run, hog, 180)
        self.assertEqual(heal.counter, 2)
        self.assertAlmostEqual(vitals.total, 600)


class RoadhogSecondaryTests(unittest.TestCase):
    def session(self, distance):
        run = Run(KANEZAKA_DM, ("Roadhog", "Enemy"))
        match = run.match
        match.bots.clear()
        match._pending_bot_scripts.clear()
        hog, enemy = run.players
        match.switch_hero(hog, heroes()[ROADHOG])
        x, y, z = hog.mover.position
        enemy.mover.position = [x, y, z + distance]
        fight = match.combat
        vitals = fight.vitals_of(next(t for t in fight._targets() if t.owner is enemy))
        return run, hog, enemy, vitals

    def test_close_slug_and_post_expiry_burst_have_distinct_damage_without_extra_ammo(self):
        for distance, expected, hits in ((5, 50.0, 1), (10, 25 * 6.6, 25)):
            with self.subTest(distance=distance):
                run, hog, enemy, vitals = self.session(distance)
                weapon = next(i for i in hog.body_script.component.instances.values()
                              if i.graph.index == 0x01C6)
                ammo = weapon.vars[53].value()
                run.step({hog: (*torso_aim(run, hog, enemy), 2)})
                for _ in range(40):
                    run.step({})
                self.assertAlmostEqual(200 - vitals.total, expected, places=4)
                self.assertEqual(len(hog.client.messages(combat.HIT)), hits)
                self.assertEqual(weapon.vars[53].value(), ammo - 1)

    def test_secondary_slug_and_fragments_ignore_allies_and_aimed_misses(self):
        for distance, ally_distance in ((5, 2.5), (12, 10.5)):
            baseline = None
            for mode in ("clear", "ally in line", "miss"):
                with self.subTest(distance=distance, mode=mode):
                    run, hog, enemy, vitals = self.session(distance)
                    match = run.match
                    fight = match.combat
                    match.game_map = replace(match.game_map, free_for_all=False)
                    ally = match.add_player(9, "Allied witness", enemy.hero.guid, 0, False)
                    in_world(match, ally)
                    self.assertTrue(match.switch_hero(ally, enemy.hero))
                    hog.team = ally.team = 0
                    enemy.team = 1
                    x, y, z = hog.mover.position
                    match.collision_world = flat_world(y)
                    ally.mover.position[:] = (x + (4 if mode == "clear" else 0), y, z + ally_distance)
                    ally.mover.body = enemy.mover.body = None
                    ally_target = next(t for t in fight._targets() if t.owner is ally)
                    ally_pool = fight.vitals_of(ally_target)
                    hog.body_script.ult_seconds = 0
                    hog.body_script.set_ult_charge(0)
                    charge = hog.body_script.component.entity_var(ULT_CHARGE)
                    weapon = next(i for i in hog.body_script.component.instances.values()
                                  if i.graph.index == 0x01C6)
                    ammo = weapon.vars[53].value()
                    yaw, pitch = torso_aim(run, hog, enemy)
                    if mode == "miss":
                        yaw += 16384

                    run.step({hog: (yaw, pitch, 2)})
                    parent, = fight.rockets
                    with patch.object(fight, "_expire", wraps=fight._expire) as expired:
                        for _ in range(100):
                            run.step({})
                    burst_count = sum(call.args[0] is parent for call in expired.call_args_list)
                    self.assertEqual(burst_count, int(distance > 9 or mode == "miss"))
                    self.assertEqual(weapon.vars[53].value(), ammo - 1)
                    self.assertEqual(ally_pool.total, 200)
                    self.assertFalse(fight.rockets)
                    loss = 200 - vitals.total
                    hit_count = len(hog.client.messages(combat.HIT))
                    self.assertAlmostEqual(charge.value(), loss, places=4)
                    if mode == "clear":
                        self.assertGreater(loss, 0)
                        self.assertGreater(hit_count, 0)
                        baseline = loss, hit_count
                    elif mode == "ally in line":
                        self.assertEqual((loss, hit_count), baseline)
                    else:
                        self.assertEqual((loss, hit_count), (0, 0))

    def test_prior_wall_hit_does_not_emit_fragments_through_wall(self):
        run, hog, _, vitals = self.session(10)
        x, y, z = hog.mover.position
        wall = World([
            (x - 5, y, z + 4, x + 5, y, z + 4, x + 5, y + 5, z + 4),
            (x - 5, y, z + 4, x + 5, y + 5, z + 4, x - 5, y + 5, z + 4),
        ])
        with patch.object(run.match.combat, "_collision", return_value=wall):
            run.step({hog: (0, 0, 2)})
            for _ in range(40):
                run.step({})
        self.assertEqual(vitals.total, 200)
        self.assertEqual(run.match.combat.rockets, [])
        self.assertEqual(hog.client.messages(combat.HIT), [])

    def test_body_contact_on_either_side_of_slug_expiry_changes_damage(self):
        for offset, expected_damage, hits in ((-0.01, 50, 1), (0.01, 165, 25)):
            with self.subTest(offset=offset):
                run, hog, enemy, vitals = self.session(10)
                fight = run.match.combat
                angles = torso_aim(run, hog, enemy)
                run.step({hog: (*angles, 2)})
                parent, = fight.rockets
                target = next(t for t in fight._targets() if t.owner is enemy)
                a, b = target.segment()
                contact = combat.ray_capsule(parent.position, parent.direction, a, b, target.radius)
                self.assertIsNotNone(contact)
                adjustment = parent.speed * parent.left + offset - contact
                enemy.mover.position[:] = tuple(p + d * adjustment for p, d in
                                                zip(target.feet, parent.direction, strict=True))
                enemy.mover.body = None
                for _ in range(40):
                    run.step({})
                self.assertAlmostEqual(200 - vitals.total, expected_damage, places=4)
                self.assertEqual(len(hog.client.messages(combat.HIT)), hits)
                self.assertFalse(fight.rockets)

    def test_wall_contact_on_either_side_of_slug_expiry_changes_burst(self):
        for offset, expected in ((-0.01, 0), (0.01, 1)):
            with self.subTest(offset=offset):
                run, hog, _, vitals = self.session(10)
                fight = run.match.combat
                weapon = next(i for i in hog.body_script.component.instances.values()
                              if i.graph.index == 0x01C6)
                ammo = weapon.vars[53].value()
                run.step({hog: (0, 0, 2)})
                parent, = fight.rockets
                self.assertIsNotNone(parent.split_on_expiry)
                self.assertEqual(len(parent.split_on_expiry.directions), 25)
                x, y, z = parent.position
                wall_z = z + parent.speed * parent.left + offset
                wall = World([
                    (x - 5, y - 5, wall_z, x + 5, y - 5, wall_z, x + 5, y + 5, wall_z),
                    (x - 5, y - 5, wall_z, x + 5, y + 5, wall_z, x - 5, y + 5, wall_z),
                ])
                with (
                    patch.object(fight, "_collision", return_value=wall),
                    patch.object(fight, "_expire", wraps=fight._expire) as expired,
                ):
                    for _ in range(40):
                        run.step({})
                self.assertEqual(sum(call.args[0] is parent for call in expired.call_args_list), expected)
                self.assertEqual(weapon.vars[53].value(), ammo - 1)
                self.assertEqual(vitals.total, 200)
                self.assertFalse(fight.rockets)
                self.assertFalse(hog.client.messages(combat.HIT))

    def test_world_gap_carries_parent_expiry_remainder_into_fragment_flight(self):
        results = []
        for skipped in (False, True):
            run, hog, enemy, vitals = self.session(10)
            run.step({hog: (*torso_aim(run, hog, enemy), 2)})
            run.step({})  # Admit one normal projectile step before the scheduler gap.
            if skipped:
                frame = run.frame + 40
                run.match.update(frame * combat.TICK, frame)
            else:
                for _ in range(40):
                    run.step({})
            results.append((
                vitals.total, len(hog.client.messages(combat.HIT)), len(run.match.combat.rockets),
            ))
        self.assertEqual(results[0], results[1])
        self.assertAlmostEqual(results[0][0], 35, places=4)


class WholeHogTests(unittest.TestCase):
    def session(self, *, charged=True):
        run = Run(KANEZAKA_DM, ("Roadhog", "Enemy"))
        match = run.match
        match.bots.clear()
        match._pending_bot_scripts.clear()
        hog, enemy = run.players
        match.switch_hero(hog, heroes()[ROADHOG])
        x, y, z = hog.mover.position
        enemy.mover.position = [x, y, z + 2]
        fight = match.combat
        targets = {t.owner: t for t in fight._targets()}
        # High-health fixture retains one target for the entire ultimate, without
        # claiming that a retail Soldier has this amount of health.
        vitals = combat.Vitals(enemy.body, 20000)
        fight.vitals[enemy.body] = vitals
        if charged:
            cost = hog.body_script.component.entity_var(ULT_COST).value()
            hog.body_script.component.set(ULT_CHARGE, cost)
        angles = aim(fight._eye(hog), (x, y + 0.9, z + 2))
        return run, hog, enemy, targets[hog], vitals, angles

    def advance(self, run, hog, angles, count, *, press=False):
        for frame in range(count):
            run.step({hog: (*angles, 32 if press and frame == 0 else 0)})

    def test_charge_consumption_multiple_pellets_end_and_reject_uncharged_recast(self):
        run, hog, _, _, vitals, angles = self.session()
        ultimate, = hog.body_script.ultimates
        self.advance(run, hog, angles, 1, press=True)
        self.assertTrue(ultimate.flags & 1)
        self.assertEqual(hog.body_script.component.entity_var(ULT_CHARGE).value(), 0)
        self.advance(run, hog, angles, 449)
        self.assertFalse(ultimate.flags & 1)
        hits = hog.client.messages(combat.HIT)
        self.assertGreater(len(hits), 16)  # Later hits before external motion leaves the fixed aim.
        critical_hits = sum(bool(hit["+0x78"]["+0x8"] & combat.CRITICAL) for hit in hits)
        self.assertAlmostEqual(20000 - vitals.total, 7 * (len(hits) + critical_hits))
        ended = vitals.total
        self.advance(run, hog, angles, 100, press=True)
        self.assertEqual(vitals.total, ended)
        self.assertEqual(len(hog.client.messages(combat.HIT)), len(hits))

    def test_whole_hog_held_input_cannot_recast_even_after_charge_returns(self):
        run, hog, enemy, _, vitals, _ = self.session()
        script = hog.body_script
        script.ult_seconds = 0
        ultimate, = script.ultimates
        cost = script.component.entity_var(ULT_COST).value()
        charge = script.component.entity_var(ULT_CHARGE)
        weapon = next(i for i in script.component.instances.values() if i.graph.index == 0x01C6)
        ammo = weapon.vars[53].value()
        away = (-32768, 0)
        run.step({hog: (*away, 32)})
        self.assertTrue(ultimate.flags & 1)
        self.assertEqual(charge.value(), 0)

        for _ in range(450):
            run.step({hog: (*away, 32)})
            hog.take_commands([Command(run.frame, buttons=0), Command(run.frame, buttons=32)])
            if not ultimate.flags & 1:
                break
        self.assertFalse(ultimate.flags & 1)
        self.assertEqual(vitals.total, 20000)
        self.assertEqual(weapon.vars[53].value(), ammo)

        # Supply charge independently to isolate the old button latch from charge gain.
        script.set_ult_charge(cost)
        toward = torso_aim(run, hog, enemy)
        for _ in range(100):
            run.step({hog: (*toward, 32)})
            self.assertFalse(ultimate.flags & 1)
        self.assertEqual(charge.value(), cost)
        self.assertEqual(vitals.total, 20000)

        run.step({hog: (*toward, 0)})
        run.step({hog: (*toward, 32)})
        hog.take_commands([Command(run.frame, buttons=32)])
        self.assertTrue(ultimate.flags & 1)
        self.assertEqual(charge.value(), 0)
        for _ in range(90):
            run.step({})
        self.assertLess(vitals.total, 20000)
        self.assertEqual(weapon.vars[53].value(), ammo)

    def test_uncharged_ultimate_cannot_start_or_damage(self):
        run, hog, _, _, vitals, angles = self.session(charged=False)
        self.advance(run, hog, angles, 120, press=True)
        ultimate, = hog.body_script.ultimates
        self.assertFalse(ultimate.flags & 1)
        self.assertEqual(vitals.total, 20000)
        self.assertEqual(hog.client.messages(combat.HIT), [])

    def test_owner_death_ends_ultimate_damage(self):
        run, hog, enemy, target, vitals, angles = self.session()
        self.advance(run, hog, angles, 80, press=True)
        self.assertLess(vitals.total, 20000)
        run.match.combat._damage(enemy, target, 2000, False, None, run.frame)
        self.assertTrue(run.match.combat.vitals_of(target).dead)
        ended = vitals.total
        self.advance(run, hog, angles, 100)
        self.assertEqual(vitals.total, ended)

    def test_whole_hog_hits_and_submits_force_only_inside_authored_range(self):
        for offset in (-0.01, 0.01):
            with self.subTest(offset=offset):
                run, hog, enemy, _, vitals, _ = self.session()
                fight = run.match.combat
                x, y, z = hog.mover.position
                run.match.collision_world = flat_world(y)
                enemy.mover.position[:] = (x, y, z + 100)
                enemy.mover.body = None
                angles = torso_aim(run, hog, enemy)
                origin, direction = fight._eye(hog), combat.aim(*angles)
                target = next(t for t in fight._targets() if t.owner is enemy)
                a, b = target.segment()
                contact = combat.ray_capsule(origin, direction, a, b, target.radius)
                self.assertIsNotNone(contact)
                adjustment = 100 + offset - contact
                enemy.mover.position[:] = tuple(p + d * adjustment for p, d in
                                                zip(target.feet, direction, strict=True))
                enemy.mover.body = None
                for _ in range(4):
                    run.step({})
                with patch.object(fight.knockbacks, "whole_hog_hit",
                                  wraps=fight.knockbacks.whole_hog_hit) as impulse:
                    run.step({hog: (*angles, 32)})
                    for _ in range(100):
                        run.step({hog: (*angles, 0)})
                        if impulse.called:
                            break
                if offset < 0:
                    self.assertLess(vitals.total, 20000)
                    hit, = impulse.call_args_list
                    self.assertIs(hit.args[0].owner, enemy)
                    self.assertAlmostEqual(hit.args[2], 99.99, places=3)
                    self.assertFalse(fight.knockbacks.whole_hog_motion)
                    self.assertIn(enemy.body, fight.knockbacks.active)
                else:
                    self.assertEqual(vitals.total, 20000)
                    impulse.assert_not_called()
                    self.assertNotIn(enemy.body, fight.knockbacks.whole_hog_motion)
                self.assertEqual(len(hog.client.messages(combat.HIT)), int(offset < 0))

    def test_charged_ultimate_miss_allies_and_triangle_wall(self):
        for mode in ("miss", "ally", "wall"):
            with self.subTest(mode=mode):
                run, hog, enemy, _, vitals, angles = self.session()
                fight = run.match.combat
                if mode == "miss":
                    angles = (-32768, 0)
                if mode == "ally":
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    enemy.team = hog.team
                x, y, z = hog.mover.position
                wall = World([
                    (x - 5, y, z + 1, x + 5, y, z + 1, x + 5, y + 5, z + 1),
                    (x - 5, y, z + 1, x + 5, y + 5, z + 1, x - 5, y + 5, z + 1),
                ])
                with patch.object(fight, "_collision", return_value=wall if mode == "wall" else None):
                    self.advance(run, hog, angles, 100, press=True)
                self.assertEqual(vitals.total, 20000)
                self.assertEqual(hog.client.messages(combat.HIT), [])
                self.assertEqual(hog.body_script.component.entity_var(ULT_CHARGE).value(), 0)


if __name__ == "__main__":
    unittest.main()
