"""McCree's Peacekeeper attacks against Hanzo through actual input."""

import hashlib
import math
import struct
import unittest
from unittest.mock import patch

from test_game_combat import aim, in_world
import test_mccree_combat as melee_scene

from ow174.game import arrow_deflection, combat, dragon, weapon_spread, zarya_barrier
from ow174.game.commands import CROUCH, FIRE, Command
from ow174.game.content import heroes


def hanzo_arena(test, **options):
    fixture = melee_scene.McCreeMeleeTests()
    run, shooter, hanzo = fixture.arena(**options)
    position = tuple(hanzo.mover.position)
    test.assertTrue(run.match.switch_hero(hanzo, heroes()[dragon.HANZO]))
    hanzo.mover.position[:] = position
    return fixture, run, shooter, hanzo


def add_genji(test, run, team, position):
    match = run.match
    genji = match.add_player(3, "Genji", arrow_deflection.GENJI, team, False)
    in_world(match, genji)
    run.players.append(genji)
    test.assertTrue(match.switch_hero(genji, heroes()[arrow_deflection.GENJI]))
    genji.mover.position[:] = position
    genji.mover.body = None
    return genji


class HanzoMcCreePrimaryTests(unittest.TestCase):
    def fire(self, *, head, crouched=False, **options):
        fixture, run, shooter, hanzo = hanzo_arena(self, **options)
        posture = CROUCH if crouched else 0
        idle = {hanzo: (0, 0, posture)}
        for _ in range(20):
            run.step(idle)
        self.assertEqual(hanzo.mover.crouched, crouched)
        fight = run.match.combat
        before = fixture.health(run, hanzo)
        target = fight._eye(hanzo)
        if not head:
            x, y, z = hanzo.mover.position
            target = x, y + 0.55, z
        yaw, pitch = aim(fight._eye(shooter), target)
        with patch.object(fight, "_hitscan", wraps=fight._hitscan) as hitscan:
            run.step({**idle, shooter: (yaw, pitch, FIRE)})
            for _ in range(30):
                run.step(idle)
        hitscan.assert_called_once()
        self.assertEqual(hitscan.call_args.args[3], combat.aim(yaw, pitch))
        self.assertEqual(hitscan.call_args.args[5], 400)
        weapon = next(instance for instance in shooter.body_script.component.instances.values()
                      if instance.graph.index == 0x01D2)
        primary = next(node for node in weapon.graph.nodes if node.pos == 31)
        self.assertIsNone(primary.field("m_6396149F").get("m_A872C70E"))
        self.assertEqual(weapon.vars[53].value(), 5)
        return before - fixture.health(run, hanzo), [
            (hit["+0x78"]["+0x4"], bool(hit["+0x78"]["+0x8"] & combat.CRITICAL))
            for hit in shooter.client.messages(combat.HIT)
        ]

    def test_primary_body_and_upper_hits_on_standing_and_crouched_hanzo(self):
        for crouched in (False, True):
            for head, expected in ((False, 70), (True, 140)):
                with self.subTest(crouched=crouched, head=head):
                    self.assertEqual(self.fire(distance=10, head=head, crouched=crouched),
                                     (expected, [(expected, head)]))

    def test_primary_falloff_preserves_hit_region_policy(self):
        for head, expected in ((False, 21), (True, 42)):
            with self.subTest(head=head):
                damage, hits = self.fire(distance=41, head=head)
                self.assertAlmostEqual(damage, expected, delta=1e-5)
                self.assertEqual(hits, [(expected, head)])

    def test_primary_wall_team_and_range_filter(self):
        damage, hits = self.fire(distance=399, head=True)
        self.assertAlmostEqual(damage, 42, delta=1e-5)
        self.assertEqual(hits, [(42, True)])
        for options in ({"wall": True}, {"ally": True}, {"distance": 401}):
            with self.subTest(**options):
                self.assertEqual(self.fire(head=True, **options), (0, []))

    def test_genji_between_mccree_and_hanzo_uses_active_hostile_deflect_boxes(self):
        for defense, hanzo_z, genji_z, hanzo_hp, mccree_hp, damage in (
            ("frontal", 8, 4, 200, 85, 140),
            ("rear", 8, 4, 60, 225, 140),
            ("allied_to_shooter", 8, 4, 60, 225, 140),
            ("nearer_wall", 8, 4, 200, 225, 0),
            ("nearer_body", 8, 10, 60, 225, 140),
            ("inactive", 8, 4, 60, 225, 140),
            ("round_trip_falloff", 30, 23, 200, 183, 42),
            ("round_trip_out_of_range", 220, 205, 200, 225, 0),
        ):
            with self.subTest(defense=defense):
                fixture, run, shooter, hanzo = hanzo_arena(
                    self, distance=hanzo_z, wall=defense == "nearer_wall")
                fight = run.match.combat
                team = shooter.team if defense == "allied_to_shooter" else hanzo.team
                genji = add_genji(self, run, team, (0.55, hanzo.mover.position[1], genji_z))
                facing = 0 if defense == "rear" else -32768
                idle = {genji: (facing, 0, 0)}
                for _ in range(20):
                    run.step(idle)
                run.step({genji: (facing, 0, 16 if defense != "inactive" else 0)})
                genji_target = next(target for target in fight._targets() if target.owner is genji)
                self.assertLess(genji_target.radius, 0.55)
                self.assertEqual(arrow_deflection.active_volume(genji_target, 0x03A3, 10) is not None,
                                 defense != "inactive")
                yaw, pitch = aim(fight._eye(shooter), fight._eye(hanzo))
                if defense == "frontal":
                    self.assertIsNotNone(arrow_deflection.trace(
                        genji_target, fight._eye(shooter), combat.aim(yaw, pitch), 8))
                run.step({**idle, shooter: (yaw, pitch, FIRE)})
                for _ in range(30):
                    run.step(idle)
                self.assertEqual(fixture.health(run, hanzo), hanzo_hp)
                self.assertAlmostEqual(fixture.health(run, shooter), mccree_hp, delta=1e-5)
                self.assertEqual(fixture.health(run, genji), 200)
                for source, target, struck in ((genji, shooter, mccree_hp < 225),
                                               (shooter, hanzo, hanzo_hp < 200)):
                    hits = [hit["+0x78"] for hit in source.client.messages(combat.HIT)]
                    self.assertEqual(hits, [{"+0x0": {"+0x0": target.body}, "+0x4": damage,
                                             "+0x8": combat.CRITICAL}] if struck else [])

    def test_enemy_genji_redirects_allied_mccree_primary_into_hanzo(self):
        fixture, run, shooter, hanzo = hanzo_arena(self, distance=2, ally=True)
        fight = run.match.combat
        floor = hanzo.mover.position[1]
        hanzo.mover.position[0] = 0.85
        hanzo.mover.body = None
        genji = add_genji(self, run, 1 - hanzo.team, (0.55, floor, 4))
        facing = 28200
        idle = {genji: (facing, 0, 0)}
        for _ in range(20):
            run.step(idle)
        run.step({genji: (facing, 0, 16)})
        yaw, pitch = aim(fight._eye(shooter), (0, floor + 1.35, 4))
        run.step({**idle, shooter: (yaw, pitch, FIRE)})
        for _ in range(30):
            run.step(idle)
        self.assertEqual(fixture.health(run, hanzo), 60)
        self.assertEqual(fixture.health(run, genji), 200)
        self.assertEqual(fixture.health(run, shooter), 225)
        self.assertEqual([hit["+0x78"] for hit in genji.client.messages(combat.HIT)],
                         [{"+0x0": {"+0x0": hanzo.body}, "+0x4": 140, "+0x8": combat.CRITICAL}])
        self.assertFalse(shooter.client.messages(combat.HIT))


class HanzoMcCreeFanTests(unittest.TestCase):
    def test_spread_table_and_vertical_basis_match_capture(self):
        table = struct.pack("<16384I", *weapon_spread.uniform_table())
        self.assertEqual(hashlib.sha256(table).hexdigest(),
                         "4f99ff210621528fff6737582c39f0ed5464cdf1c9980f03d503dcf311b356f6")
        ray = weapon_spread.direction((0, 1, math.cos(math.pi / 2)), math.radians(6), 284, 46, 3)
        captured = (-0.023236999288201332, 0.9946451783180237, -0.10070230811834335)
        for actual, native in zip(ray, captured, strict=True):
            self.assertAlmostEqual(actual, native, delta=2e-6)

    def fire(self, *, lethal=False, projected=False, **options):
        fixture, run, shooter, hanzo = hanzo_arena(self, **options)
        match = run.match
        position = tuple(hanzo.mover.position)
        caster = None
        if projected:
            caster = match.add_player(3, "Zarya", zarya_barrier.HERO, hanzo.team, False)
            in_world(match, caster)
            run.players.append(caster)
            self.assertTrue(match.switch_hero(caster, heroes()[zarya_barrier.HERO]))
            x, y, z = position
            caster.mover.position[:] = (x + 4, y, z + 2)
            caster.mover.body = None
        for _ in range(20):
            run.step({})

        fight = match.combat
        before = fixture.health(run, hanzo)
        x, y, z = hanzo.mover.position
        shields = fight.zarya_barriers
        if caster is not None:
            cast = aim(fight._eye(caster), (x, y + 1, z))
            run.step({caster: (*cast, 16)})
            self.assertEqual(shields.projected[caster.body][1:], (200, hanzo.body))

        yaw, pitch = aim(fight._eye(shooter), (x, y + 1, z))
        forward = combat.aim(yaw, pitch)
        shield_steps = []
        with patch.object(fight, "_hitscan", wraps=fight._hitscan) as hitscan:
            for _ in range(100):
                prior = hitscan.call_count
                run.step({shooter: (yaw, pitch, 2)})
                shooter.take_commands([Command(run.frame, yaw=yaw, pitch=pitch, buttons=2)])
                if caster is not None and hitscan.call_count > prior:
                    shield_steps.append((shields.projected.get(caster.body, (None, 0))[1],
                                         fixture.health(run, hanzo)))

        self.assertEqual(hitscan.call_count, 6)
        if caster is not None:
            self.assertEqual(shield_steps, [(150, 200), (100, 200), (50, 200), (0, 200),
                                            (0, 150), (0, 100)])
        rays = [call.args[3] for call in hitscan.call_args_list]
        self.assertEqual(len(set(rays)), 6)
        for ray in rays:
            self.assertAlmostEqual(math.hypot(*ray), 1, delta=5e-7)
            self.assertGreaterEqual(math.fsum(a * b for a, b in zip(ray, forward, strict=True)),
                                    math.cos(math.radians(6.001)))
        weapon = next(instance for instance in shooter.body_script.component.instances.values()
                      if instance.graph.index == 0x01D2)
        self.assertEqual(weapon.vars[53].value(), 0)
        hits = shooter.client.messages(combat.HIT)
        self.assertFalse(any(hit["+0x78"]["+0x8"] & combat.CRITICAL for hit in hits))
        self.assertEqual(fight.is_dead(hanzo), lethal)
        if lethal:
            self.assertEqual(shooter.client.messages(combat.KILL_NOTICE)[-1]["+0x84"],
                             {"+0x0": hanzo.body})
            self.assertEqual(hanzo.client.messages(combat.DEATH_NOTICE),
                             [{"+0x78": combat.KILLED_BY, "+0x7C": {"+0x0": shooter.body}}])
            self.assertTrue(hanzo.body_script.component.owner_dead)
        return before - fixture.health(run, hanzo), [hit["+0x78"]["+0x4"] for hit in hits]

    def test_close_visible_hanzo_dies_with_attribution(self):
        self.assertEqual(self.fire(distance=2, lethal=True), (200, [50, 50, 50, 50]))

    def test_projected_barrier_breaks_before_later_shots_hit_hanzo(self):
        self.assertEqual(self.fire(distance=2, projected=True), (100, [50, 50]))

    def test_genji_reflects_fan_without_converting_its_authored_multiplier(self):
        fixture, run, shooter, hanzo = hanzo_arena(self, distance=8)
        fight = run.match.combat
        genji = add_genji(self, run, hanzo.team, (0, hanzo.mover.position[1], 3))
        idle = {genji: (-32768, 0, 0)}
        for _ in range(20):
            run.step(idle)
        run.step({genji: (-32768, 0, 16)})
        yaw, pitch = aim(fight._eye(shooter), fight._eye(hanzo))
        with patch.object(fight, "_hitscan", wraps=fight._hitscan) as hitscan:
            for _ in range(100):
                run.step({**idle, shooter: (yaw, pitch, 2)})
        self.assertEqual([call.args[0] for call in hitscan.call_args_list], [shooter, genji] * 5)
        self.assertEqual(fixture.health(run, hanzo), 200)
        self.assertEqual(fixture.health(run, genji), 200)
        self.assertFalse(shooter.client.messages(combat.HIT))
        hits = [hit["+0x78"] for hit in genji.client.messages(combat.HIT)]
        self.assertEqual(hits, [
            {"+0x0": {"+0x0": shooter.body}, "+0x4": damage, "+0x8": flags}
            for damage, flags in [(50, 0)] * 4 + [(25, combat.KILLING)]
        ])
        self.assertTrue(fight.is_dead(shooter))
        self.assertEqual(shooter.client.messages(combat.DEATH_NOTICE),
                         [{"+0x78": combat.KILLED_BY, "+0x7C": {"+0x0": genji.body}}])

    def test_wall_and_team_filter_block_damage(self):
        for options in ({"wall": True}, {"ally": True}):
            with self.subTest(**options):
                self.assertEqual(self.fire(**options), (0, []))

    def test_distance_changes_hit_count_and_falloff(self):
        for distance, expected in ((10, [50, 50]), (41, [15, 15]), (80, [15])):
            with self.subTest(distance=distance):
                damage, hits = self.fire(distance=distance)
                self.assertEqual(hits, expected)
                self.assertAlmostEqual(damage, sum(expected), delta=1e-5)
