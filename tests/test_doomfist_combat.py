"""Authoritative damage checks; these do not constitute original-client acceptance."""

import math
import struct
import unittest
from dataclasses import replace
from unittest.mock import patch

from test_game_combat import KANEZAKA_DM, Run, aim
from test_game_mover import box

from ow174.game import bots, combat, stats
from ow174.game import sombra_control as sombra_hack
from ow174.game.collision import World
from ow174.game.commands import FIRE, Command
from ow174.game.content import heroes
from ow174.game.script import nodes
from ow174.game.script.runtime import ScriptBudgetError

DOOMFIST = 0x02E000000000012F


class DoomfistFixture:
    def setUp(self):
        self.prepare()

    def prepare(self, distance=3):
        self.run = Run(KANEZAKA_DM, ("Doomfist", "Target"))
        self.a, self.b = self.run.players
        self.match = self.run.match
        self.match.bots.clear()
        self.match._pending_bot_scripts.clear()
        self.match.switch_hero(self.a, heroes()[DOOMFIST])
        self.fight = self.match.combat
        x, y, z = self.a.mover.position
        self.b.mover.position[:] = (x, y, z + distance)
        self.run.step({})

    def fire(self):
        x, y, z = self.b.mover.position
        yaw, pitch = aim(self.fight._eye(self.a), (x, y + 1, z))
        self.run.step({self.a: (yaw, pitch, FIRE)})

    def settle(self, frames=8):
        for _ in range(frames):
            self.run.step({})

    def charge_checkpoint(self):
        return self.run.frame, self.a.body_script.component.entity_var(342).value()

    def assert_damage_charge(self, checkpoint, damage):
        start_frame, start_charge = checkpoint
        frame, charge = self.charge_checkpoint()
        # Each ordinary 16ms command contributes 1680 * .016 / 60 neutral points.
        self.assertAlmostEqual(charge - start_charge - (frame - start_frame) * .448, damage, places=2)

    def assert_scatter_graze(self, owner, clearance):
        target = next(t for t in self.fight._targets() if t.owner is owner)
        x, y, _ = target.feet
        aim_x = x - target.radius - clearance
        yaw, pitch = aim(self.fight._eye(self.a), (aim_x, y + 1, target.feet[2]))
        origin, direction = self.fight._eye(self.a), combat.aim(yaw, pitch)
        start, end = target.segment()
        self.assertIsNone(combat.ray_capsule(origin, direction, start, end, target.radius))
        vitals = self.fight.vitals_of(target)
        before = vitals.total
        charge = self.charge_checkpoint()
        self.run.step({self.a: (yaw, pitch, FIRE)})
        self.assertEqual(len(self.fight.rockets), 11)
        self.settle(14)
        notices = self.a.client.messages(combat.HIT)
        lost = before - vitals.total
        self.assertEqual(lost, 6 * len(notices))
        self.assert_damage_charge(charge, lost)
        return len(notices)

    def vitals(self):
        target = next(t for t in self.fight._targets() if t.owner is self.b)
        return self.fight.vitals_of(target)

    def align_target_with_first_pellet(self):
        """Align the fixture horizontally with an emitted pellet, keeping its floor height."""
        pellet = self.fight.rockets[0]
        _, y, z = self.b.mover.position
        travel = (z - pellet.position[2]) / pellet.direction[2]
        self.place_player(self.b, (pellet.position[0] + travel * pellet.direction[0], y, z))

    @staticmethod
    def place_player(player, position):
        """Fixture relocation updates the real capsule as well as the visible feet."""
        player.mover.position[:] = position
        player.mover.velocity = (0.0, 0.0, 0.0)
        x, y, z = position
        player.mover._engine_body().teleport((x, y + player.mover._lift(), z))


class HandCannonTests(DoomfistFixture, unittest.TestCase):
    def test_actual_volley_jitter_varies_by_activation_and_replays(self):
        runs = []
        for _ in range(2):
            self.prepare(3)
            volleys = []
            for _ in range(2):
                self.fire()
                self.assertEqual(len(self.fight.rockets), 11)
                volleys.append(tuple(p.direction for p in self.fight.rockets))
                self.settle(25)
            self.assertNotEqual(volleys[0], volleys[1])
            runs.append(volleys)
        self.assertEqual(runs[0], runs[1])

    def test_off_axis_volley_spends_ammo_and_misses_enemy_player(self):
        self.prepare(5)
        weapon = next(i for i in self.a.body_script.component.instances.values() if i.graph.index == 0xD0A)
        ammo = weapon.vars[53].value()
        x, y, z = self.b.mover.position
        yaw, pitch = aim(self.fight._eye(self.a), (x + 8, y + 1, z))
        self.run.step({self.a: (yaw, pitch, FIRE)})
        self.assertEqual(weapon.vars[53].value(), ammo - 1)
        self.assertEqual(len(self.fight.rockets), 11)
        self.settle(20)
        self.assertEqual(self.vitals().total, 200)
        self.assertFalse(self.a.client.messages(combat.HIT))

    def test_actual_high_hit_uses_current_null_critical_fallback(self):
        self.prepare(1.5)
        target = next(t for t in self.fight._targets() if t.owner is self.b)
        x, y, z = target.feet
        weapon = next(i for i in self.a.body_script.component.instances.values() if i.graph.index == 0xD0A)
        self.assertIsNone(weapon.state(2).node.field("m_6396149F.m_A872C70E"))
        yaw, pitch = aim(self.fight._eye(self.a), (x, y + target.eye - .08, z))
        self.run.step({self.a: (yaw, pitch, FIRE)})
        start, end = target.segment()
        centre = self.fight.rockets[0]
        self.assertTrue(all(p.direct.critical == -1.0 for p in self.fight.rockets))
        travel = combat.ray_capsule(centre.position, centre.direction, start, end, target.radius)
        self.assertIsNotNone(travel)
        hit_y = centre.position[1] + travel * centre.direction[1]
        self.assertGreaterEqual(hit_y, y + target.eye - combat.HEAD_BELOW)
        self.settle(8)
        notices = self.a.client.messages(combat.HIT)
        self.assertEqual(len(notices), 11)
        self.assertEqual(self.vitals().total, 134)
        self.assertTrue(all(notice["+0x78"]["+0x8"] & combat.CRITICAL == 0 for notice in notices))

        self.prepare(1.5)
        self.fire()
        self.settle(8)
        torso_notices = self.a.client.messages(combat.HIT)
        self.assertEqual(len(torso_notices), 11)
        self.assertEqual(self.vitals().total, 134)
        self.assertEqual([hit["+0x78"]["+0x4"] for hit in notices], [6] * 11)
        self.assertEqual([hit["+0x78"]["+0x4"] for hit in torso_notices], [6] * 11)
        self.assertTrue(all(hit["+0x78"]["+0x8"] & combat.CRITICAL == 0 for hit in torso_notices))

    def test_authored_ring_scatter_grazes_player_beyond_center_ray(self):
        for clearance in (.12, .65):
            with self.subTest(clearance=clearance):
                self.prepare(10)
                hits = self.assert_scatter_graze(self.b, clearance)
                if clearance < .2:
                    self.assertGreater(hits, 0)
                    self.assertLess(hits, 11)
                else:
                    self.assertEqual(hits, 0)

    def test_one_command_fires_eleven_independent_pellets_and_spends_one_ammo(self):
        weapon = next(i for i in self.a.body_script.component.instances.values() if i.graph.index == 0xD0A)
        before = weapon.vars[53].value()
        self.fire()
        self.assertEqual(len(self.fight.rockets), 11)
        self.assertEqual(weapon.vars[53].value(), before - 1)
        self.settle()
        self.assertEqual(self.vitals().total, 134)
        self.assertEqual(len(self.a.client.messages(combat.HIT)), 11)
        self.assertEqual(self.a.stats.value(stats.HERO_DAMAGE), 66)

    def test_armour_applies_per_pellet_rather_than_per_volley(self):
        vitals = self.vitals()
        vitals.now[combat.ARMOUR_POOL] = vitals.most[combat.ARMOUR_POOL] = 100
        self.fire()
        self.settle()
        self.assertEqual(vitals.now[combat.ARMOUR_POOL], 67)
        self.assertEqual(vitals.now[combat.HEALTH_POOL], 200)

    def test_friendly_target_takes_no_pellets_in_team_mode(self):
        self.match.game_map = self.match.game_map.__class__(**{
            **self.match.game_map.__dict__, "free_for_all": False,
        })
        self.b.team = self.a.team
        self.fire()
        self.settle()
        self.assertEqual(self.vitals().total, 200)
        self.assertEqual(self.a.client.messages(combat.HIT), [])

    def test_authored_zarya_shields_absorb_pellets_then_spill_into_health(self):
        self.match.switch_hero(self.b, heroes()[0x02E0000000000068])
        x, y, z = self.a.mover.position
        self.place_player(self.b, (x, y, z + 3))
        self.run.step({})
        vitals = self.vitals()
        self.assertEqual(vitals.now[combat.HEALTH_POOL], 200)
        self.assertEqual(vitals.now[combat.SHIELDS_POOL], 200)
        for shot in range(4):
            self.fire()
            self.settle(25)
            dealt = 66 * (shot + 1)
            self.assertEqual(vitals.now[combat.SHIELDS_POOL], max(0, 200 - dealt))
            self.assertEqual(vitals.now[combat.HEALTH_POOL], 200 - max(0, dealt - 200))
        self.assertEqual(len(self.a.client.messages(combat.HIT)), 44)
        self.assertEqual(self.a.stats.value(stats.HERO_DAMAGE), 264)

    def test_dead_owner_cannot_fire(self):
        target = next(t for t in self.fight._targets() if t.owner is self.a)
        self.fight._damage(self.b, target, 1000, False, None, self.run.frame)
        self.assertTrue(self.fight.is_dead(self.a))
        self.fire()
        self.assertFalse(self.fight.rockets)
        self.assertEqual(self.vitals().total, 200)

    def test_actual_enemy_rifle_death_preserves_emitted_player_volley_only(self):
        self.prepare(20)
        own = self.fight.vitals[self.a.body]
        own.now[combat.HEALTH_POOL] = 5
        x, y, z = self.b.mover.position
        attack = aim(self.fight._eye(self.a), (x, y + 1, z))
        reply = aim(self.fight._eye(self.b), self.fight._eye(self.a))
        self.run.step({self.a: (*attack, FIRE), self.b: (*reply, FIRE)})
        self.assertTrue(own.dead)
        self.assertEqual(own.killer, self.b.body)
        self.assertEqual(self.vitals().total, 200)
        pellets = [p for p in self.fight.rockets if p.shooter is self.a]
        self.assertEqual(len(pellets), 11)
        target = next(t for t in self.fight._targets() if t.owner is self.b)
        start, end = target.segment()
        contacts = [d for p in pellets if (d := combat.ray_capsule(
            p.position, p.direction, start, end, target.radius,
        )) is not None]
        self.assertTrue(contacts)
        emitted = {id(p) for p in pellets}
        for _ in range(70):
            self.run.step({self.a: (*attack, FIRE)})
            self.assertTrue({id(p) for p in self.fight.rockets if p.shooter is self.a} <= emitted)
        expected = sum(6 * (1 - .7 * max(0, min(1, (d - 15) / 15))) for d in contacts)
        self.assertAlmostEqual(200 - self.vitals().total, expected, places=5)
        self.assertEqual(len(self.a.client.messages(combat.HIT)), len(contacts))

    def test_lethal_pellets_award_one_death_and_one_elimination(self):
        self.vitals().now[combat.HEALTH_POOL] = 25
        self.fire()
        self.settle()
        self.assertTrue(self.fight.is_dead(self.b))
        self.assertEqual(self.vitals().killer, self.a.body)
        self.assertEqual(self.a.stats.value(stats.ELIMINATIONS), 1)
        self.assertEqual(self.b.stats.value(stats.DEATHS), 1)
        self.assertEqual(len(self.a.client.messages(combat.KILL_NOTICE)), 1)

    def test_reconstructed_pattern_remains_finite_when_aiming_straight_up(self):
        self.fire()
        weapon = next(i for i in self.a.body_script.component.instances.values() if i.graph.index == 0xD0A)
        state = weapon.state(2)
        headings = combat.hand_cannon_directions(state, (0, 1, 0), self.a.body, 0, (1, 0))
        self.assertEqual(len(headings), 11)
        for heading in headings:
            self.assertTrue(all(math.isfinite(v) for v in heading))
            self.assertAlmostEqual(sum(v * v for v in heading), 1)

    def test_projectile_falloff_uses_total_flight_distance_and_preserves_hit_notices(self):
        # Follow one actual centre pellet beyond the authored 30m falloff end.
        self.prepare(35)
        self.fire()
        centre = self.fight.rockets[0]
        self.fight.rockets[:] = [centre]
        self.settle(35)
        self.assertAlmostEqual(self.vitals().total, 198.2, places=6)
        self.assertAlmostEqual(self.a.stats.value(stats.HERO_DAMAGE), 1.8, places=6)
        self.assertEqual(len(self.a.client.messages(combat.HIT)), 1)

    def test_natural_eleven_pellet_volley_covers_falloff_without_discarding_pellets(self):
        # Predict the stationary intersections once, then let every emitted
        # pellet fly normally. The 15..30m falloff uses contact distance.
        for distance in (15, 22.5, 30, 35):
            with self.subTest(distance=distance):
                self.prepare(distance)
                self.fire()
                self.assertEqual(len(self.fight.rockets), 11)
                target = next(t for t in self.fight._targets() if t.owner is self.b)
                start, end = target.segment()
                contacts = [combat.ray_capsule(p.position, p.direction, start, end, target.radius)
                            for p in self.fight.rockets]
                hits = [contact for contact in contacts if contact is not None]
                self.assertGreater(len(hits), 0)
                self.assertLess(len(hits), 11)
                expected = sum(6 * (1 - .7 * max(0, min(1, (contact - 15) / 15))) for contact in hits)
                self.settle(410)
                dealt = 200 - self.vitals().total
                self.assertAlmostEqual(dealt, expected, places=5)
                self.assertEqual(len(self.a.client.messages(combat.HIT)), len(hits))
                self.assertAlmostEqual(self.a.stats.value(stats.HERO_DAMAGE), dealt)
                self.assertFalse(self.fight.rockets)

    def test_natural_volley_expires_before_target_beyond_authored_five_hundred_meters(self):
        self.prepare(510)
        self.fire()
        self.assertEqual(len(self.fight.rockets), 11)
        self.settle(410)
        self.assertEqual(self.vitals().total, 200)
        self.assertFalse(self.a.client.messages(combat.HIT))
        self.assertFalse(self.fight.rockets)

    def test_doomfist_takes_enemy_rifle_damage(self):
        x, y, z = self.a.mover.position
        yaw, pitch = aim(self.fight._eye(self.b), (x, y + 1, z))
        self.run.step({self.b: (yaw, pitch, FIRE)})
        target = next(t for t in self.fight._targets() if t.owner is self.a)
        self.assertEqual(self.fight.vitals_of(target).total, 230)

    def test_doomfist_quick_melee_deals_thirty_once(self):
        self.prepare(1.5)
        charge = self.charge_checkpoint()
        x, y, z = self.a.mover.position
        yaw, pitch = aim(self.fight._eye(self.a), (x, y + 1, z + 1.5))
        self.run.step({self.a: (yaw, pitch, 0x800)})
        self.settle(40)
        self.assertEqual(self.vitals().total, 170)
        self.assert_damage_charge(charge, 30)

    def test_doomfist_quick_melee_rejects_static_wall_but_hits_without_it(self):
        for blocked in (False, True):
            with self.subTest(wall=blocked):
                self.prepare(1.5)
                x, y, z = self.a.mover.position
                shapes = box(x - 20, x + 20, y - 1, y, z - 20, z + 20)
                if blocked:
                    shapes += box(x - 5, x + 5, y, y + 5, z + .7, z + .8)
                world = World(shapes)
                self.match.collision_world = world
                for player in self.match.players:
                    player.mover.use(world=world)
                yaw, pitch = aim(self.fight._eye(self.a), (x, y + 1, z + 1.5))
                self.run.step({self.a: (yaw, pitch, 0x800)})
                self.settle(40)
                self.assertEqual(self.vitals().total, 200 if blocked else 170)

    def test_static_wall_stops_all_pellets(self):
        self.fire()
        x, y, z = self.a.mover.position
        wall = World([
            (x - 5, y, z + 1, x + 5, y, z + 1, x + 5, y + 5, z + 1),
            (x - 5, y, z + 1, x + 5, y + 5, z + 1, x - 5, y + 5, z + 1),
        ])
        # Use real collision casts without replacing the match's mover floor.
        with patch.object(self.fight, "_collision", return_value=wall):
            self.settle()
        self.assertEqual(self.vitals().total, 200)
        self.assertFalse(self.fight.rockets)
        self.assertFalse(self.a.client.messages(combat.HIT))

    def test_pellets_kill_manual_training_bot_and_leave_retained_corpse(self):
        # This checks the generic Bot path, not any of the five PvE enemy bodies.
        self.b.team = self.a.team
        self.match.game_map = self.match.game_map.__class__(**{
            **self.match.game_map.__dict__, "free_for_all": False,
        })
        x, y, z = self.b.mover.position
        bot = bots.Bot(bots.BOT_ENTITY, {
            "hero": "0x02E000000000016B", "team": self.a.team + 1,
            "position": (x, y, z), "rotation": (0, 0, 0, 1), "graphs": [],
        }, {"name": "Training Bot fixture", "body": "0x0400000000001403", "health": 25}, {})
        bot.retain_corpse = True
        self.match.bots.append(bot)
        self.fire()
        self.settle(210)
        self.assertTrue(bot.dead)
        self.assertIn(bot, self.match.bots)
        self.assertEqual(self.fight.vitals[bot.entity].total, 0)
        self.assertEqual(self.fight.vitals[bot.entity].killer, self.a.body)
        self.assertFalse(any(who is bot for _, who in self.fight.respawns))

    def test_explicit_projectile_critical_multiplier_reports_actual_damage_and_flag(self):
        # Exercise shared projectile logic with an explicit HitDef. Doomfist's
        # current resource evaluator yields critical=1; this does not claim
        # that his native headshot rule has been recovered.
        x, y, z = self.b.mover.position
        yaw, pitch = aim(self.fight._eye(self.a), (x, y + combat.EYE, z))
        self.run.step({self.a: (yaw, pitch, FIRE)})
        centre = self.fight.rockets[0]
        centre.direct = replace(centre.direct, critical=2)
        self.fight.rockets[:] = [centre]
        self.settle()
        self.assertEqual(self.vitals().total, 188)
        hit, = self.a.client.messages(combat.HIT)
        self.assertEqual(hit["+0x78"]["+0x4"], 12)
        self.assertEqual(hit["+0x78"]["+0x8"], combat.CRITICAL)


class UppercutDamageTests(DoomfistFixture, unittest.TestCase):
    def setUp(self):
        self.prepare(1.5)

    def uppercut(self, yaw=None):
        x, y, z = self.b.mover.position
        target_yaw, pitch = aim(self.fight._eye(self.a), (x, y + 1, z))
        self.run.step({self.a: (target_yaw if yaw is None else yaw, pitch, 8)})

    def test_authored_target_search_reaches_health_effect_and_hits_once(self):
        charge = self.charge_checkpoint()
        self.uppercut()
        self.settle(45)
        self.assertEqual(self.vitals().total, 150)
        hit, = self.a.client.messages(combat.HIT)
        self.assertEqual(hit["+0x78"]["+0x4"], 50)
        self.assertEqual(self.a.stats.value(stats.HERO_DAMAGE), 50)
        self.assert_damage_charge(charge, 50)

    def test_facing_away_misses(self):
        self.uppercut(yaw=-32768)
        self.settle(45)
        self.assertEqual(self.vitals().total, 200)

    def test_outside_authored_sphere_misses(self):
        self.prepare(6)
        self.uppercut()
        self.settle(45)
        self.assertEqual(self.vitals().total, 200)

    def test_static_wall_blocks_target_query(self):
        x, y, z = self.a.mover.position
        wall = World([
            (x - 5, y, z + 0.75, x + 5, y, z + 0.75, x + 5, y + 5, z + 0.75),
            (x - 5, y, z + 0.75, x + 5, y + 5, z + 0.75, x - 5, y + 5, z + 0.75),
        ])
        with patch.object(self.fight, "_collision", return_value=wall):
            self.uppercut()
        self.settle(45)
        self.assertEqual(self.vitals().total, 200)

    def test_static_cover_requires_a_clear_lower_or_upper_ray(self):
        for wall_height, damage in ((.85, 50), (1.15, 0), (2., 0)):
            with self.subTest(wall_height=wall_height):
                self.prepare(2.5)
                x, y, z = self.a.mover.position
                self.place_player(self.b, (x, y, z + 2.5))
                floor = box(x - 15, x + 15, y - 1, y, z - 15, z + 15)
                wall = box(x - 1, x + 1, y, y + wall_height, z + 1.2, z + 1.3)
                world = World(floor + wall)
                self.match.collision_world = world
                for player in self.run.players:
                    player.mover.use(world=world)
                health = self.vitals().total
                self.uppercut()
                self.assertEqual(health - self.vitals().total, damage)
                self.assertEqual(len(self.a.client.messages(combat.HIT)), int(bool(damage)))

    def test_armour_reduces_the_single_fifty_damage_hit(self):
        vitals = self.vitals()
        vitals.now[combat.ARMOUR_POOL] = vitals.most[combat.ARMOUR_POOL] = 100
        self.uppercut()
        self.assertEqual(vitals.now[combat.ARMOUR_POOL], 55)
        self.assertEqual(vitals.now[combat.HEALTH_POOL], 200)

    def test_ally_is_excluded_in_team_mode(self):
        self.match.game_map = self.match.game_map.__class__(**{
            **self.match.game_map.__dict__, "free_for_all": False,
        })
        self.b.team = self.a.team
        self.uppercut()
        self.settle(45)
        self.assertEqual(self.vitals().total, 200)

    def test_passive_caps_against_current_intrinsic_shields(self):
        for current, grant in ((130, 20), (150, 0), (160, 0)):
            with self.subTest(current=current):
                self.prepare(1.5)
                shields = combat.Vitals(self.a.body, 250, shields=200)
                shields.now[combat.SHIELDS_POOL] = current
                self.fight.vitals[self.a.body] = shields
                self.uppercut()
                self.assertEqual(self.vitals().total, 150)
                self.assertEqual(shields.now[combat.SHIELDS_POOL], current)
                self.assertEqual(shields.temporary_shields, grant)
                if grant:
                    self.assertEqual(shields.temporary_cap, 150)
                else:
                    self.assertEqual(shields.temporary_decay_at, 0)

    def test_cooldown_rejects_repress_and_expires_before_next_damage(self):
        self.uppercut()
        effect = self.a.body_script.component.instances[6].state(10)
        activation = effect.counter
        self.settle(60)
        owner = next(t for t in self.fight._targets() if t.owner is self.a)
        shields = self.fight.vitals_of(owner)
        self.assertEqual(shields.temporary_shields, 30)
        first_decay_at = shields.temporary_decay_at
        ability = self.a.body_script.component.instances[6].state(0)
        self.assertGreater(ability.remaining(), 0)
        self.uppercut()
        self.assertEqual(self.vitals().total, 150)
        self.assertEqual(shields.temporary_decay_at, first_decay_at)
        self.settle(360)
        before = shields.temporary_shields
        self.assertGreater(before, 0)
        self.assertLess(before, 30)
        self.assertEqual(ability.remaining(), 0)
        self.assertEqual(effect.counter, activation)  # rejected old input must expire, not fire later
        # Knockback moves the victim out of range. Reset only fixture placement;
        # preserve real graph cooldowns and require a new input activation.
        x, y, z = self.a.mover.position
        self.place_player(self.b, (x, y, z + 1.5))
        self.uppercut()
        self.assertEqual(self.vitals().total, 100)
        self.assertEqual(len(self.a.client.messages(combat.HIT)), 2)
        self.assertAlmostEqual(shields.temporary_shields, before + 30 - 3 * combat.TICK)
        self.assertAlmostEqual(shields.temporary_decay_at, self.fight.now + 1)
        refreshed = shields.temporary_shields
        self.settle(62)
        self.assertAlmostEqual(shields.temporary_shields, refreshed)
        self.settle(1)
        self.assertAlmostEqual(shields.temporary_shields, refreshed - 3 * (63 * combat.TICK - 1))

    def test_press_inside_early_buffer_starts_after_cooldown_not_before(self):
        self.uppercut()
        self.settle(60)
        instance = self.a.body_script.component.instances[6]
        ability, effect = instance.state(0), instance.state(10)
        while ability.remaining() > 0.08:
            self.settle(1)
        x, y, z = self.a.mover.position
        self.place_player(self.b, (x, y, z + 1.5))
        before = effect.counter
        self.uppercut()
        self.assertEqual(effect.counter, before)
        self.settle(6)
        self.assertEqual(effect.counter, before + 1)
        self.assertEqual(self.vitals().total, 100)

    def test_holding_through_cooldown_does_not_repeat_without_release(self):
        x, y, z = self.b.mover.position
        yaw, pitch = aim(self.fight._eye(self.a), (x, y + 1, z))
        for _ in range(430):
            self.run.step({self.a: (yaw, pitch, 8)})
        self.assertEqual(self.vitals().total, 150)

    def test_death_prevents_uppercut(self):
        target = next(t for t in self.fight._targets() if t.owner is self.a)
        self.fight._damage(self.b, target, 1000, False, None, self.run.frame)
        self.uppercut()
        self.assertEqual(self.vitals().total, 200)

    def test_unposed_effect_is_discarded_and_not_replayed_on_next_command(self):
        script = self.a.body_script
        script.command(script.frame + 1, 8)
        self.assertEqual(len(script.component.health_reports), 1)
        self.fight.command(self.a, None)
        self.assertFalse(script.component.health_reports)
        self.assertFalse(script.component.motion_reports)
        self.settle(5)
        self.assertEqual(self.vitals().total, 200)

    def test_vm_fault_clears_pending_damage_before_it_can_escape(self):
        script = self.a.body_script
        script.command(script.frame + 1, 8)
        self.assertEqual(len(script.component.health_reports), 1)
        with self.assertRaises(ScriptBudgetError):
            script.component.budget_exceeded("test failure after authored health effect")
        self.assertFalse(script.component.health_reports)
        self.assertFalse(script.component.motion_reports)
        self.assertEqual(self.vitals().total, 200)


class PassiveShieldTests(DoomfistFixture, unittest.TestCase):
    def setUp(self):
        self.prepare(1.5)

    def own_vitals(self):
        target = next(t for t in self.fight._targets() if t.owner is self.a)
        return self.fight.vitals_of(target)

    def hit_uppercut(self):
        self.run.step({self.a: (0, 0, 8)})
        return self.own_vitals()

    def test_confirmed_ability_hit_gives_thirty_and_updates_owner_health(self):
        vitals = self.hit_uppercut()
        self.assertEqual(vitals.temporary_shields, 30)
        self.assertEqual(vitals.total, 280)
        self.assertEqual(self.a.body_script.component.entity_var(1732).value(), 280)
        self.assertEqual(self.a.body_script.component.entity_var(1737).value(), 250)

    def test_missed_uppercut_does_not_grant_shields(self):
        self.prepare(6)
        self.run.step({self.a: (0, 0, 8)})
        self.assertEqual(self.own_vitals().temporary_shields, 0)

    def test_dead_target_does_not_grant_shields(self):
        target = next(t for t in self.fight._targets() if t.owner is self.b)
        self.fight._damage(self.a, target, 1000, False, None, self.run.frame)
        self.run.step({self.a: (0, 0, 8)})
        self.assertEqual(self.own_vitals().temporary_shields, 0)

    def test_capped_pool_rejects_extra_gain_without_extending_delay(self):
        # Pool policy test, separate from the real seven-NPC ability cap test.
        vitals = combat.Vitals(1, 250)
        self.assertEqual(vitals.add_temporary_shields(150, 150, 3, 1, 0), 150)
        self.assertEqual(vitals.add_temporary_shields(30, 150, 3, 1, 0.5), 0)
        self.assertEqual(vitals.temporary_decay_at, 1)
        vitals.decay_temporary_shields(1.25)
        self.assertAlmostEqual(vitals.temporary_shields, 149.25)

    def test_primary_and_quick_melee_do_not_grant_ability_shields(self):
        self.fire()
        self.settle(8)
        self.assertEqual(self.own_vitals().temporary_shields, 0)
        self.run.step({self.a: (0, 0, 0x800)})
        self.settle(45)
        self.assertEqual(self.own_vitals().temporary_shields, 0)

    def test_friendly_uppercut_does_not_grant_shields(self):
        self.match.game_map = self.match.game_map.__class__(**{
            **self.match.game_map.__dict__, "free_for_all": False,
        })
        self.b.team = self.a.team
        self.run.step({self.a: (0, 0, 8)})
        self.assertEqual(self.own_vitals().temporary_shields, 0)

    def test_delay_then_three_per_second_decay(self):
        vitals = self.hit_uppercut()
        self.settle(62)  # 0.992s, before the authored one-second delay.
        self.assertEqual(vitals.temporary_shields, 30)
        self.settle(63)  # exactly2s since the hit: one second at3/s.
        self.assertAlmostEqual(vitals.temporary_shields, 27)

    def test_skipped_ticks_decay_by_elapsed_time_without_double_application(self):
        vitals = self.hit_uppercut()
        frame = self.run.frame + 125
        self.match.update(frame * combat.TICK, frame)
        self.assertAlmostEqual(vitals.temporary_shields, 27)
        self.match.update(frame * combat.TICK, frame)
        self.assertAlmostEqual(vitals.temporary_shields, 27)

    def test_incoming_rifle_consumes_temporary_shields_before_health(self):
        vitals = self.hit_uppercut()
        x, y, z = self.a.mover.position
        yaw, pitch = aim(self.fight._eye(self.b), (x, y + 0.2, z))
        self.run.step({self.b: (yaw, pitch, FIRE)})
        self.assertEqual(vitals.temporary_shields, 10)
        self.assertEqual(vitals.now[combat.HEALTH_POOL], 250)
        self.assertEqual(vitals.total, 260)

    def test_healing_restores_health_without_refilling_temporary_shields(self):
        vitals = self.hit_uppercut()
        target = next(t for t in self.fight._targets() if t.owner is self.a)
        self.fight._damage(self.b, target, 50, False, None, self.run.frame)
        self.assertEqual(vitals.temporary_shields, 0)
        self.assertEqual(vitals.now[combat.HEALTH_POOL], 230)
        self.fight.heal_area(self.a, self.a.mover.position, 5, 100)
        self.assertEqual(vitals.total, 250)
        self.assertEqual(vitals.temporary_shields, 0)

    def test_depleted_temporary_pool_is_explicitly_cleared_in_health_component(self):
        vitals = self.hit_uppercut()
        slot = combat.SHIELDS_POOL * combat.POOLS + 1
        part = vitals.component(vitals.ids)[51][slot]
        self.assertEqual(struct.unpack_from("<IffI", part), (2, 150, 30, combat.SHIELDS_POOL))
        self.settle(720)
        self.assertEqual(vitals.temporary_shields, 0)
        part = vitals.component(vitals.ids)[51][slot]
        self.assertEqual(struct.unpack_from("<IffI", part), (0, 0, 0, combat.SHIELDS_POOL))

    def test_actual_enemy_rifle_depletes_passive_pool_before_death_and_respawn(self):
        owner = self.hit_uppercut()
        owner.now[combat.HEALTH_POOL] = 5
        old_body = self.a.body
        self.assertEqual(owner.temporary_shields, 30)
        for _ in range(45):
            if owner.dead:
                break
            yaw, pitch = aim(self.fight._eye(self.b), self.fight._eye(self.a))
            self.run.step({self.b: (yaw, pitch, FIRE)})
        self.assertTrue(owner.dead)
        self.assertEqual(owner.killer, self.b.body)
        self.assertEqual(owner.total, 0)
        self.assertEqual(owner.temporary_shields, 0)
        for _ in range(700):
            if self.a.body != old_body:
                break
            self.run.step({})
        self.assertNotEqual(self.a.body, old_body)
        self.assertEqual(self.own_vitals().temporary_shields, 0)
        self.assertEqual(self.own_vitals().total, 250)

    def test_lethal_damage_exhausts_temporary_shields_and_hero_switch_clears_them(self):
        vitals = self.hit_uppercut()
        target = next(t for t in self.fight._targets() if t.owner is self.a)
        self.fight._damage(self.b, target, 1000, False, None, self.run.frame)
        self.assertTrue(vitals.dead)
        self.assertEqual(vitals.total, 0)
        self.assertEqual(vitals.temporary_shields, 0)
        self.match.switch_hero(self.a, heroes()[DOOMFIST])
        self.assertEqual(self.own_vitals().temporary_shields, 0)
        self.assertEqual(self.own_vitals().total, 250)


class UppercutMotionTests(DoomfistFixture, unittest.TestCase):
    def setUp(self):
        self.prepare(1.5)

    def test_real_input_lifts_both_bodies_displaces_victim_and_lands(self):
        owner_y, victim_y = self.a.mover.position[1], self.b.mover.position[1]
        victim_z = self.b.mover.position[2]
        self.run.step({self.a: (0, 0, 8)})
        self.assertEqual(self.a.mover.velocity[1], 10)
        self.settle(10)
        self.assertGreater(self.a.mover.position[1], owner_y + 0.5)
        self.assertGreater(self.b.mover.position[1], victim_y + 0.5)
        self.assertGreater(self.b.mover.position[2], victim_z + 0.5)
        self.assertTrue(self.a.mover.airborne)
        self.assertTrue(self.b.mover.airborne)
        self.settle(150)
        self.assertFalse(self.a.mover.airborne)
        self.assertFalse(self.b.mover.airborne)
        self.assertEqual(self.a.mover.position[1], owner_y)
        self.assertEqual(self.b.mover.position[1], victim_y)

    def test_launched_player_can_fire_while_airborne(self):
        self.run.step({self.a: (0, 0, 8)})
        self.settle(8)
        self.assertTrue(self.b.mover.airborne)
        self.assertFalse(self.fight.is_stunned(self.b))
        own = self.fight.vitals[self.a.body]
        before = own.total
        yaw, pitch = aim(self.fight._eye(self.b), self.fight._eye(self.a))
        self.run.step({self.b: (yaw, pitch, FIRE)})
        self.assertTrue(self.b.mover.airborne)
        self.assertLess(own.total, before)
        hit, = self.b.client.messages(combat.HIT)
        self.assertIn(hit["+0x78"]["+0x4"], (20, 40))

    def test_miss_lifts_owner_without_moving_distant_victim(self):
        self.prepare(6)
        before = tuple(self.b.mover.position)
        self.run.step({self.a: (0, 0, 8)})
        self.settle(10)
        self.assertGreater(self.a.mover.position[1], before[1] + 0.5)
        self.assertEqual(tuple(self.b.mover.position), before)

    def test_allied_body_is_not_launched(self):
        self.match.game_map = self.match.game_map.__class__(**{
            **self.match.game_map.__dict__, "free_for_all": False,
        })
        self.b.team = self.a.team
        before = tuple(self.b.mover.position)
        self.run.step({self.a: (0, 0, 8)})
        self.settle(10)
        self.assertEqual(tuple(self.b.mover.position), before)

    def test_duplicate_input_cannot_apply_launch_twice(self):
        from ow174.game.commands import Command

        self.run.step({self.a: (0, 0, 8)})
        own, victim = self.a.mover.velocity, self.b.mover.velocity
        self.a.take_commands([Command(self.run.frame, buttons=8)])
        self.assertEqual(self.a.mover.velocity, own)
        self.assertEqual(self.b.mover.velocity, victim)

    def test_low_ceiling_stops_launch_and_both_bodies_return_to_floor(self):
        from test_game_mover import box

        x, y, z = self.a.mover.position
        collision = World(box(x - 20, x + 20, y - 1, y, z - 20, z + 20)
                          + box(x - 20, x + 20, y + 2.5, y + 4, z - 20, z + 20))
        self.match.collision_world = collision
        for player in self.match.players:
            player.mover.use(world=collision)
        self.run.step({self.a: (0, 0, 8)})
        maximum = [y, y]
        for _ in range(150):
            self.settle(1)
            maximum = [max(top, p.mover.position[1])
                       for top, p in zip(maximum, self.match.players, strict=True)]
        self.assertTrue(all(top < y + 1 for top in maximum))
        self.assertEqual(self.a.mover.position[1], y)
        self.assertEqual(self.b.mover.position[1], y)


if __name__ == "__main__":
    unittest.main()

class AnaSleepInteractionTests(DoomfistFixture, unittest.TestCase):
    def sleep_doomfist(self):
        self.prepare(6)
        self.match.switch_hero(self.b, heroes()[0x02E000000000013B])
        yaw, pitch = aim(self.fight._eye(self.b), self.fight._eye(self.a))
        self.run.step({self.b: (yaw, pitch, 8)})
        for _ in range(45):
            if self.a.body in self.fight.ana_sleeps:
                break
            self.run.step({self.b: (yaw, pitch, 0)})
        sleep = self.fight.ana_sleeps[self.a.body]
        self.assertEqual(self.fight.vitals[self.a.body].now[combat.HEALTH_POOL], 245)
        self.assertEqual(sleep["end"] - sleep["start"], math.ceil(5.0 / combat.TICK))
        return sleep

    def assert_control_releases(self, deadline):
        while self.match.tick < deadline - 1:
            self.run.step({})
        self.assertTrue(self.fight.is_stunned(self.a))
        self.run.step({})
        self.assertFalse(self.fight.is_stunned(self.a))
        self.assertNotIn(self.a.body, self.fight.ana_sleeps)
        self.fire()
        self.settle()
        self.assertLess(self.fight.vitals[self.b.body].now[combat.HEALTH_POOL], 200)

    def test_enemy_ana_sleep_blocks_fire_until_natural_expiry(self):
        sleep = self.sleep_doomfist()
        self.fire()
        self.settle()
        self.assertEqual(self.fight.vitals[self.b.body].now[combat.HEALTH_POOL], 200)
        self.assert_control_releases(sleep["end"])

    def test_enemy_ana_rifle_damage_wakes_doomfist(self):
        sleep = self.sleep_doomfist()
        natural_end = sleep["end"]
        self.settle(math.ceil(0.5 / combat.TICK) + 1)
        yaw, pitch = aim(self.fight._eye(self.b), self.fight._eye(self.a))
        for _ in range(120):
            self.run.step({self.b: (yaw, pitch, FIRE)})
            if sleep["waking"]:
                break
        self.run.step({self.b: (yaw, pitch, 0)})
        self.assertTrue(sleep["waking"])
        self.assertEqual(self.fight.vitals[self.a.body].now[combat.HEALTH_POOL], 175)
        self.assertEqual(sleep["end"], self.fight.tick - 1 + math.ceil(0.5 / combat.TICK))
        self.assertLess(sleep["end"], natural_end)
        self.assert_control_releases(sleep["end"])

class MeiFreezeInteractionTests(DoomfistFixture, unittest.TestCase):
    def test_enemy_mei_spray_freezes_doomfist_until_exclusive_expiry(self):
        self.prepare(5)
        self.match.switch_hero(self.b, heroes()[0x02E00000000000DD])
        mei_aim = aim(self.fight._eye(self.b), self.fight._eye(self.a))
        doom_aim = aim(self.fight._eye(self.a), self.fight._eye(self.b))
        for _ in range(180):
            self.run.step({self.b: (*mei_aim, FIRE), self.a: (*doom_aim, 0)})
            frost = self.fight.frost.get(self.a.body)
            if frost is not None and frost.until:
                break
        else:
            self.fail("Mei's actual spray did not freeze Doomfist")
        deadline = frost.until
        self.assertEqual(deadline - self.match.tick, math.ceil(1.3 / combat.TICK))
        self.assertGreater(self.fight.vitals[self.a.body].total, 0)
        self.assertLess(self.fight.vitals[self.a.body].total, 250)
        start = tuple(self.a.mover.position)
        for _ in range(5):
            self.run.step({self.b: (*mei_aim, FIRE), self.a: (*doom_aim, FIRE | 8)})
        self.assertEqual(frost.until, deadline)
        while self.match.tick < deadline - 1:
            self.run.frame += 1
            self.match.update(self.run.frame * combat.TICK, self.run.frame)
            self.a.take_commands([Command(self.run.frame, yaw=16384, forward=127, buttons=FIRE | 8)])
        self.assertTrue(self.fight.is_stunned(self.a))
        self.assertEqual(tuple(self.a.mover.position), start)
        self.assertEqual(self.a.mover.yaw, frost.yaw)
        self.assertEqual(self.fight.vitals[self.b.body].total, 250)
        self.run.step({})
        self.assertNotIn(self.a.body, self.fight.frost)
        self.assertFalse(self.fight.is_stunned(self.a))
        self.fire()
        self.settle()
        self.assertLess(self.fight.vitals[self.b.body].total, 250)

class SombraHackInteractionTests(DoomfistFixture, unittest.TestCase):
    def test_enemy_sombra_hack_blocks_skills_but_not_hand_cannon(self):
        self.prepare(5)
        self.match.switch_hero(self.b, heroes()[sombra_hack.SOMBRA])
        yaw, pitch = aim(self.fight._eye(self.b), self.fight._eye(self.a))
        for _ in range(65):
            self.run.step({self.b: (yaw, pitch, 2)})
            if sombra_hack.locked(self.a):
                break
        else:
            self.fail("Actual Sombra Hack did not apply")
        receiver = sombra_hack.instance_for(self.a, 0x0B1F)
        timer = receiver.states[58]
        self.assertEqual(timer.ms, 5000)
        script = self.a.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        doom_aim = aim(self.fight._eye(self.a), self.fight._eye(self.b))
        before = tuple(self.a.mover.position)
        self.run.step({self.a: (*doom_aim, FIRE | 2 | 8 | 16 | 32)})
        for _ in range(45):
            self.run.step({self.a: (*doom_aim, 2 | 8 | 16 | 32)})
        self.assertTrue(sombra_hack.locked(self.a))
        self.assertLess(self.fight.vitals[self.b.body].total, 200)
        self.assertEqual(tuple(self.a.mover.position), before)
        self.assertNotIn(self.a.body, self.fight.meteors.casts)
        self.assertNotIn(self.a.body, self.fight.punches.dashes)
        self.assertNotIn(self.a.body, self.fight.slams.casts)
        deadline = timer.start + timer.ms
        for _ in range(math.ceil(timer.ms / script.frame_ms)):
            if not sombra_hack.locked(self.a):
                break
            self.run.step({})
        self.assertLess(abs(self.run.frame * script.frame_ms - deadline), script.frame_ms)
        self.assertFalse(sombra_hack.locked(self.a))
        self.run.step({self.a: (*doom_aim, 8)})
        self.assertFalse(sombra_hack.locked(self.a, self.run.frame))
        self.assertGreater(self.a.mover.velocity[1], 0)

    def test_enemy_hack_interrupts_live_punch_charge_without_dash(self):
        self.prepare(5)
        self.match.switch_hero(self.b, heroes()[sombra_hack.SOMBRA])
        x, y, z = self.a.mover.position
        self.place_player(self.b, (x, y, z + 5))
        yaw, pitch = aim(self.fight._eye(self.b), self.fight._eye(self.a))
        ability = self.a.body_script.component.instances[5].state(0)
        self.run.step({self.a: (0, 0, 2), self.b: (yaw, pitch, 2)})
        self.assertTrue(ability.flags & nodes.Ability.RUNNING)
        for _ in range(65):
            self.run.step({self.a: (0, 0, 2), self.b: (yaw, pitch, 2)})
            if sombra_hack.locked(self.a):
                break
        self.assertTrue(sombra_hack.locked(self.a))
        self.assertFalse(ability.flags & nodes.Ability.RUNNING)
        self.assertNotIn(self.a.body, self.fight.punches.dashes)
        self.settle(60)
        self.assertEqual(tuple(self.a.mover.position), (x, y, z))
        self.assertEqual(self.vitals().total, 200)
        self.assertFalse(self.a.client.messages(combat.HIT))

    def test_enemy_sombra_hack_respects_range_wall_and_team(self):
        for reason in ("range", "wall", "team"):
            with self.subTest(reason=reason):
                self.prepare(25 if reason == "range" else 5)
                self.match.switch_hero(self.b, heroes()[sombra_hack.SOMBRA])
                if reason == "team":
                    self.match.game_map = replace(self.match.game_map, free_for_all=False)
                    self.b.team = self.a.team
                x, y, z = self.a.mover.position
                self.b.mover.position[:] = (x, y, z + (25 if reason == "range" else 5))
                wall = World(box(x - 5, x + 5, y - 1, y + 5, z + 2, z + 2.2))
                yaw, pitch = aim(self.fight._eye(self.b), self.fight._eye(self.a))
                collision = wall if reason == "wall" else self.fight._collision()
                with patch.object(self.fight, "_collision", return_value=collision):
                    for _ in range(65):
                        self.run.step({self.b: (yaw, pitch, 2)})
                self.assertIsNone(sombra_hack.instance_for(self.a, 0x0B1F))

class QuickMeleePlayerTests(DoomfistFixture, unittest.TestCase):
    def melee_states(self):
        graph = next(i for i in self.a.body_script.component.instances.values() if i.graph.index == 0x43)
        return graph.state(6), graph.state(4)

    def punch(self, *, miss=False, enemy_commands=None):
        x, y, z = self.b.mover.position
        if miss:
            x += 8
        yaw, pitch = aim(self.fight._eye(self.a), (x, y + 1, z))
        commands = {self.a: (yaw, pitch, 0x800)}
        if enemy_commands is not None:
            commands[self.b] = enemy_commands
        self.run.step(commands)

    def test_melee_player_miss_range_and_friendly_filter(self):
        for reason in ("off aim", "range", "friendly"):
            with self.subTest(reason=reason):
                self.prepare(5 if reason == "range" else 1.5)
                if reason == "friendly":
                    self.match.game_map = replace(self.match.game_map, free_for_all=False)
                    self.b.team = self.a.team
                _, hit = self.melee_states()
                before = hit.counter
                self.punch(miss=reason == "off aim")
                self.settle(45)
                self.assertEqual(hit.counter, before + 1)
                self.assertEqual(self.fight.vitals[self.b.body].total, 200)
                self.assertFalse(self.a.client.messages(combat.HIT))

    def test_melee_player_early_repress_is_rejected_until_recovery(self):
        self.prepare(1.5)
        ability, hit = self.melee_states()
        victim = self.fight.vitals[self.b.body]
        self.punch()
        first = hit.counter
        self.assertEqual(victim.total, 170)
        self.assertTrue(ability.flags & nodes.Ability.RUNNING)
        self.run.step({})
        self.punch()
        self.assertEqual(hit.counter, first)
        for _ in range(150):
            if not ability.flags & nodes.Ability.RUNNING and ability.remaining() == 0:
                break
            self.run.step({})
        self.assertFalse(ability.flags & nodes.Ability.RUNNING)
        self.assertEqual(ability.remaining(), 0)
        self.settle(10)
        self.assertEqual(hit.counter, first)
        self.assertEqual(victim.total, 170)
        self.punch()
        self.assertEqual(hit.counter, first + 1)
        self.assertEqual(victim.total, 140)

    def test_melee_player_armour_and_lethal_health_use_distinct_pools(self):
        for lethal in (False, True):
            with self.subTest(lethal=lethal):
                self.prepare(1.5)
                target = next(t for t in self.fight._targets() if t.owner is self.b)
                if lethal:
                    victim = self.fight.vitals_of(target)
                    victim.now[combat.HEALTH_POOL] = 20
                else:
                    victim = combat.Vitals(self.b.body, health=200, armour=100)
                    self.fight.vitals[self.b.body] = victim
                self.punch()
                self.assertEqual(victim.dead, lethal)
                if lethal:
                    self.assertEqual(victim.killer, self.a.body)
                    self.assertEqual(victim.total, 0)
                    self.settle(90)
                    self.punch()
                    self.assertEqual(len(self.a.client.messages(combat.HIT)), 1)
                else:
                    self.assertEqual(victim.now[combat.HEALTH_POOL], 200)
                    self.assertEqual(victim.now[combat.ARMOUR_POOL], 75)

    def test_enemy_flashbang_cancels_melee_recovery_and_lethal_hit_retires_owner(self):
        for lethal in (False, True):
            with self.subTest(lethal=lethal):
                self.prepare(1.5)
                self.match.switch_hero(self.b, heroes()[0x02E0000000000042])
                x, y, z = self.a.mover.position
                self.b.mover.position[:] = (x, y, z + 1.5)
                self.run.step({})
                owner = self.fight.vitals[self.a.body]
                if lethal:
                    owner.now[combat.HEALTH_POOL] = 15
                victim = self.fight.vitals[self.b.body]
                ability, hit = self.melee_states()
                yaw, pitch = aim(self.fight._eye(self.b), self.fight._eye(self.a))
                self.punch(enemy_commands=(yaw, pitch, 16))
                self.assertEqual(victim.total, 195)
                self.assertTrue(ability.flags & nodes.Ability.RUNNING)
                first = hit.counter
                for _ in range(20):
                    if self.fight.is_stunned(self.a) or owner.dead:
                        break
                    self.run.step({})
                self.assertEqual(owner.dead, lethal)
                if lethal:
                    self.assertEqual(owner.killer, self.b.body)
                else:
                    self.assertTrue(self.fight.is_stunned(self.a))
                    self.assertFalse(ability.flags & nodes.Ability.RUNNING)
                self.punch()
                self.settle(110)
                self.assertEqual(hit.counter, first)
                self.assertEqual(victim.total, 195)
                if not lethal:
                    self.punch()
                    self.assertEqual(victim.total, 165)

class UppercutPlayerLifecycleTests(DoomfistFixture, unittest.TestCase):
    def uppercut_states(self):
        instance = next(i for i in self.a.body_script.component.instances.values() if i.graph.index == 0xDD8)
        return instance.state(0), instance.state(10)

    def test_uppercut_release_and_hold_end_at_same_graph_cooldown_boundary(self):
        durations = []
        for held in (False, True):
            with self.subTest(held=held):
                self.prepare(1.5)
                ability, effect = self.uppercut_states()
                self.run.step({self.a: (0, 0, 8)})
                first = effect.counter
                self.assertEqual(self.vitals().total, 150)
                self.assertTrue(ability.flags & nodes.Ability.RUNNING)
                self.assertEqual(ability.remaining(), 0)
                for _frames in range(1, 70):
                    self.run.step({self.a: (0, 0, 8 if held else 0)})
                    if not ability.flags & nodes.Ability.RUNNING:
                        break
                    self.assertEqual(ability.remaining(), 0)
                self.assertFalse(ability.flags & nodes.Ability.RUNNING)
                self.assertGreater(_frames, 1)
                self.assertGreater(ability.remaining(), 0)
                cooldown = ability.remaining()
                durations.append((_frames, cooldown))
                self.settle(1)
                self.assertLess(ability.remaining(), cooldown)
                self.assertEqual(effect.counter, first)
                self.assertEqual(self.vitals().total, 150)
        self.assertEqual(durations[0], durations[1])
        frames, cooldown = durations[0]
        self.assertEqual(frames, 37)
        self.assertAlmostEqual(cooldown, 5.993, places=3)

    def test_enemy_fire_cancels_committed_uppercut_or_kills_owner(self):
        for lethal in (False, True):
            with self.subTest(lethal=lethal):
                self.prepare(1.5)
                self.match.switch_hero(self.b, heroes()[0x02E0000000000042])
                x, y, z = self.a.mover.position
                self.place_player(self.b, (x, y, z + 1.5))
                self.run.step({})
                owner = self.fight.vitals[self.a.body]
                if lethal:
                    owner.now[combat.HEALTH_POOL] = 15
                ability, effect = self.uppercut_states()
                ex, ey, ez = self.fight._eye(self.a)
                yaw, pitch = aim(self.fight._eye(self.b), (ex, ey if lethal else ey + .65, ez))
                self.run.step({self.a: (0, 0, 8), self.b: (yaw, pitch, FIRE if lethal else 16)})
                self.assertEqual(self.vitals().total, 175)
                if not lethal:
                    self.assertTrue(ability.flags & nodes.Ability.RUNNING)
                first = effect.counter
                for _ in range(25):
                    if self.fight.is_stunned(self.a) or owner.dead:
                        break
                    self.run.step({})
                self.assertEqual(owner.dead, lethal)
                if lethal:
                    self.assertEqual(owner.killer, self.b.body)
                else:
                    self.assertTrue(self.fight.is_stunned(self.a))
                    self.assertFalse(ability.flags & nodes.Ability.RUNNING)
                    self.assertGreater(ability.remaining(), 0)
                self.run.step({self.a: (0, 0, 8)})
                self.settle(100)
                self.assertEqual(effect.counter, first)
                self.assertEqual(self.vitals().total, 175)

    def test_lethal_uppercut_kills_player_without_launch_or_repeat(self):
        self.prepare(1.5)
        victim = self.vitals()
        victim.now[combat.HEALTH_POOL] = 40
        _, effect = self.uppercut_states()
        before = tuple(self.b.mover.position)
        self.run.step({self.a: (0, 0, 8)})
        self.assertTrue(victim.dead)
        self.assertEqual(victim.killer, self.a.body)
        self.assertEqual(victim.total, 0)
        first = effect.counter
        self.settle(90)
        self.assertEqual(tuple(self.b.mover.position), before)
        self.assertEqual(effect.counter, first)
        hit, = self.a.client.messages(combat.HIT)
        self.assertEqual(hit["+0x78"]["+0x4"], 40)
