"""Actual Ice Wall deployment and Symmetra attacks on the static host."""

import math
import os
import unittest
from dataclasses import replace
from unittest.mock import patch

import test_symmetra_mei as mei_tests
from test_game_combat import aim, in_world
from test_symmetra_combat import arena, fire, health
from test_symmetra_turrets import wall

from ow174.game import combat, cryo, deflection, stats, symmetra
from ow174.game.collision import World
from ow174.game.collision_fingerprint import world_fingerprint
from ow174.game.commands import Command
from ow174.game.content import SOLDIER, heroes
from ow174.game.physics_snapshot import snapshot_mover


class SymmetraMeiWallTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get("ORPHEUS_EXTRA_GRAPH_BUNDLES"), "requires summon graph shard")
    def test_sentry_attaches_to_solid_ice_and_retires_when_pillar_is_removed(self):
        for removal, allied in (("cancel", True), ("expiry", False), ("break", False)):
            with self.subTest(removal=removal, allied=allied):
                self.check_sentry_ice_attachment(removal, allied)

    def ice_sentry_scene(self, *, allied=False):
        run, sym, mei, pieces = self.deploy(allied=allied)
        fight = run.match.combat
        victim = run.match.add_player(3, "Target", SOLDIER, 1 - sym.team, False)
        in_world(run.match, victim)
        run.players.append(victim)
        self.assertTrue(run.match.switch_hero(victim, heroes()[SOLDIER]))
        victim.team = 1 - sym.team
        victim.mover.position[:] = (1.4, 1, 4.6)
        victim.mover.body = None
        center = min(pieces, key=lambda piece: abs(piece.feet[0]))
        return run, sym, mei, victim, center

    def launch_at_pillar(self, run, sym, center, other_commands=None):
        fight = run.match.combat
        for _ in range(40):
            run.step({**(other_commands or {}), sym: (-32768, 0, 8)})
            if fight.sentries.sentries:
                break
        [sentry] = fight.sentries.sentries
        self.assertIsNone(sentry.attached_at)
        _, crossed, _ = fight.ice_wall.cast(sentry.bot.position, sentry.direction, 4)
        self.assertIs(crossed, center)
        return sentry

    def check_sentry_ice_attachment(self, removal, allied):
        run, sym, mei, victim, center = self.ice_sentry_scene(allied=allied)
        sentry = self.launch_at_pillar(run, sym, center)
        fight = run.match.combat
        pieces = tuple(fight.ice_wall.pieces.values())
        for _ in range(70):
            run.step({})
        self.assertIsNotNone(sentry.attached_at)
        self.assertEqual(sentry.support_pillar, center.entity)
        self.assertAlmostEqual(sentry.bot.position[2], center.feet[2] + center.depth + .01)
        self.assertEqual(health(run, sentry.bot).total, 30)
        self.assertTrue(all(piece.health == 400 for piece in pieces))
        self.assertEqual(sentry.target, victim.body)
        component, aura = sentry.slow
        slow = component.entity_var(23186)
        self.assertAlmostEqual(slow.value(), .2)
        self.assertLess(health(run, victim).total, 200)
        if removal == "cancel":
            run.step({mei: (0, 0, 16)})
        elif removal == "break":
            center.health = .5
            fire(run, sym, yaw=-32768)
            self.assertEqual(len(fight.ice_wall.pieces), 4)
        else:
            while run.frame < center.expires:
                run.step({})
        self.assertNotIn(center.entity, fight.ice_wall.pieces)
        self.assertNotIn(sentry, fight.sentries.sentries)
        self.assertNotIn(sentry.bot, run.match.bots)
        self.assertNotIn(aura.id, component.instances)
        self.assertFalse(slow.value())
        before = health(run, victim).total
        for _ in range(10):
            run.step({})
        self.assertEqual(health(run, victim).total, before)

    @unittest.skipUnless(os.environ.get("ORPHEUS_EXTRA_GRAPH_BUNDLES"), "requires summon graph shard")
    def test_sentry_damage_until_pillar_expires_inside_retained_gap(self):
        for active in (False, True):
            with self.subTest(active=active):
                contiguous, ticks = self.sentry_damage_before_ice_expiry(active, gap=False)
                skipped, skipped_ticks = self.sentry_damage_before_ice_expiry(active, gap=True)
                self.assertGreater(contiguous, 0)
                self.assertAlmostEqual(skipped, contiguous, places=4)
                self.assertEqual(skipped_ticks, ticks)

        long_gap, retained_ticks = self.sentry_damage_before_ice_expiry(True, gap=True, expiry_delay=80)
        # The expiry tick and four following ticks are inside the retained
        # window but no longer have a sentry.
        self.assertEqual(len(retained_ticks), combat.COMBAT_CATCH_UP - 5)
        self.assertAlmostEqual(long_gap, len(retained_ticks) * 40 * combat.TICK, places=4)

    def sentry_damage_before_ice_expiry(self, active, *, gap, expiry_delay=None):
        run, sym, _, victim, pillar = self.ice_sentry_scene()
        sentry = self.launch_at_pillar(run, sym, pillar)
        fight = run.match.combat
        run.step({})
        if active:
            for _ in range(70):
                if sentry.slow is not None:
                    break
                run.step({})
            self.assertIsNotNone(sentry.slow)
        else:
            self.assertIsNone(sentry.attached_at)
        if expiry_delay is None:
            expiry_delay = 8 if active else 48
        pillar.expires = run.frame + expiry_delay
        final_tick = pillar.expires + 4
        before = health(run, victim).total
        damage_before = sym.stats.value(stats.HERO_DAMAGE)
        slow = victim.body_script.component.entity_var(23186)
        ticks = []
        damage = fight._damage

        def record_damage(shooter, target, amount, *args, **kwargs):
            if shooter is sym and target.owner is victim and amount > 0:
                self.assertAlmostEqual(slow.value(), .2)
                ticks.append(fight.tick)
            return damage(shooter, target, amount, *args, **kwargs)

        with patch.object(fight, "_damage", side_effect=record_damage):
            if gap:
                run.frame = final_tick
                run.match.update(run.frame * combat.TICK, run.frame)
            else:
                while run.frame < final_tick:
                    run.step({})
        self.assertNotIn(sentry, fight.sentries.sentries)
        self.assertFalse(slow.value())
        loss = before - health(run, victim).total
        self.assertAlmostEqual(sym.stats.value(stats.HERO_DAMAGE) - damage_before, loss, places=4)
        run.match.update(run.frame * combat.TICK, run.frame)
        self.assertAlmostEqual(before - health(run, victim).total, loss, places=4)
        return loss, ticks

    @unittest.skipUnless(os.environ.get("ORPHEUS_EXTRA_GRAPH_BUNDLES"), "requires summon graph shard")
    def test_sentry_damage_until_orb_breaks_support_inside_retained_gap(self):
        self.check_sentry_projectile_break("orb")

    @unittest.skipUnless(os.environ.get("ORPHEUS_EXTRA_GRAPH_BUNDLES"), "requires summon graph shard")
    def test_sentry_damage_until_helix_impact_or_splash_breaks_support_inside_retained_gap(self):
        for shot in ("helix_impact", "helix_splash"):
            with self.subTest(shot=shot):
                self.check_sentry_projectile_break(shot)

    @unittest.skipUnless(os.environ.get("ORPHEUS_EXTRA_GRAPH_BUNDLES"), "requires summon graph shard")
    def test_one_helix_breaks_two_sentry_supports_and_preserves_third(self):
        flight, damage, hits = self.sentry_damage_across_double_helix_break()
        skipped_flight, skipped_damage, skipped_hits = self.sentry_damage_across_double_helix_break(flight)
        self.assertEqual(skipped_flight, flight)
        self.assertAlmostEqual(skipped_damage, damage, places=4)
        self.assertEqual(skipped_hits, hits)

    def sentry_damage_across_double_helix_break(self, expected_flight=None):
        run, sym, mei, victim, center = self.ice_sentry_scene()
        fight = run.match.combat
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        sym.team = 1 - mei.team
        victim.team = mei.team
        victim.mover.position[:] = (5.3, 1, 4.6)
        side_pillars = sorted((piece for piece in fight.ice_wall.pieces.values()
                               if piece.feet[0] > center.feet[0]), key=lambda piece: piece.feet[0])
        nearby, outer = side_pillars
        owners = [sym]
        for index, pillar in enumerate(side_pillars, start=4):
            ally = run.match.add_player(index, f"Symmetra {index}", SOLDIER, sym.team, False)
            in_world(run.match, ally)
            run.players.append(ally)
            self.assertTrue(run.match.switch_hero(ally, heroes()[symmetra.HERO]))
            ally.team = sym.team
            ally.mover.position[:] = (pillar.feet[0], 1, 5)
            ally.mover.body = None
            owners.append(ally)
        shooter = run.match.add_player(6, "Soldier", SOLDIER, sym.team, False)
        in_world(run.match, shooter)
        run.players.append(shooter)
        self.assertTrue(run.match.switch_hero(shooter, heroes()[SOLDIER]))
        shooter.team = sym.team
        shooter.mover.position[:] = (center.feet[0], 1, 35)
        shooter.mover.body = None

        launch_commands = {owner: (-32768, 0, 8) for owner in owners}
        for _ in range(40):
            run.step(launch_commands)
            if len(fight.sentries.sentries) == 3:
                break
        sentries = {sentry.owner: sentry for sentry in fight.sentries.sentries}
        component = victim.body_script.component
        slow = component.entity_var(23186)
        for _ in range(50):
            run.step({})
            if all(sentries[owner].slow is not None for owner in owners):
                break
        for owner, pillar in zip(owners, (center, nearby, outer), strict=True):
            self.assertEqual(sentries[owner].support_pillar, pillar.entity)
            self.assertEqual(sentries[owner].target, victim.body)
        self.assertAlmostEqual(slow.value(), .6)
        removed_auras = [sentries[owner].slow[1] for owner in owners[:2]]
        survivor = sentries[owners[2]]
        survivor_aura = survivor.slow
        center.health = nearby.health = 5
        angles = aim(fight._eye(shooter), (center.feet[0], 2, center.feet[2]))
        run.step({shooter: (*angles, 2)})
        [rocket] = fight.rockets
        launch_tick = run.frame
        for _ in range(12):
            run.step({})
        self.assertIn(rocket, fight.rockets)
        self.assertAlmostEqual(slow.value(), .6)
        before = health(run, victim).total
        credits = sum(owner.stats.value(stats.HERO_DAMAGE) for owner in owners)
        hits, breaks = [], []
        damage_body, damage_ice = fight._damage, fight.ice_wall.damage

        def observe_body(attacker, target, amount, *args, **kwargs):
            if attacker in owners and target.owner is victim and amount > 0:
                hits.append((fight.tick, owners.index(attacker)))
            return damage_body(attacker, target, amount, *args, **kwargs)

        def observe_ice(pillar, amount, **kwargs):
            damage_ice(pillar, amount, **kwargs)
            if pillar.health == 0:
                breaks.append((fight.tick, pillar.entity, amount, slow.value()))

        with (patch.object(fight, "_damage", side_effect=observe_body),
              patch.object(fight.ice_wall, "damage", side_effect=observe_ice)):
            if expected_flight is None:
                while rocket in fight.rockets and run.frame < launch_tick + 60:
                    run.step({})
                for _ in range(4):
                    run.step({})
            else:
                run.frame = launch_tick + expected_flight + 4
                run.match.update(run.frame * combat.TICK, run.frame)

        impact_event, splash_event = breaks
        impact_tick, impact_id, impact, slow_after_impact = impact_event
        splash_tick, splash_id, splash, slow_after_splash = splash_event
        self.assertEqual((impact_id, impact), (center.entity, 120))
        self.assertEqual(splash_id, nearby.entity)
        self.assertGreater(splash, 5)
        self.assertLessEqual(splash, 80)
        self.assertEqual(impact_tick, splash_tick)
        self.assertAlmostEqual(slow_after_impact, .4)
        self.assertAlmostEqual(slow_after_splash, .2)
        self.assertEqual(len(fight.ice_wall.pieces), 3)
        self.assertEqual(fight.sentries.sentries, [survivor])
        for owner in owners[:2]:
            self.assertNotIn(sentries[owner].bot, run.match.bots)
        self.assertEqual(health(run, survivor.bot).total, 30)
        self.assertIs(survivor.slow, survivor_aura)
        self.assertTrue(all(aura.id not in component.instances for aura in removed_auras))
        self.assertAlmostEqual(slow.value(), .2)
        for index in range(2):
            self.assertEqual(max(tick for tick, owner in hits if owner == index), impact_tick - 1)
        self.assertEqual([hit for hit in hits if hit[0] >= impact_tick],
                         [(tick, 2) for tick in range(impact_tick, run.frame + 1)])
        loss = before - health(run, victim).total
        self.assertAlmostEqual(sum(owner.stats.value(stats.HERO_DAMAGE) for owner in owners) - credits,
                               loss, places=4)
        self.assertEqual(shooter.stats.value(stats.HERO_DAMAGE), 0)
        run.match.update(run.frame * combat.TICK, run.frame)
        self.assertAlmostEqual(before - health(run, victim).total, loss, places=4)
        return impact_tick - launch_tick, loss, hits

    def check_sentry_projectile_break(self, shot):
        for active in (False, True):
            with self.subTest(active=active):
                flight, contiguous, ticks = self.sentry_damage_before_projectile_break(shot, active)
                skipped_flight, skipped, skipped_ticks = self.sentry_damage_before_projectile_break(
                    shot, active, flight,
                )
                self.assertGreater(contiguous, 0)
                self.assertEqual(skipped_flight, flight)
                self.assertAlmostEqual(skipped, contiguous, places=4)
                self.assertEqual(skipped_ticks, ticks)

    def sentry_damage_before_projectile_break(self, shot, active, expected_flight=None):
        run, sym, mei, victim, pillar = self.ice_sentry_scene()
        fight = run.match.combat
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        sym.team = 1 - mei.team
        victim.team = mei.team
        victim.mover.position[:] = (5.3 if shot == "helix_splash" else 4.5, 1, 4.6)
        shooter = run.match.add_player(4, "Projectile shooter", SOLDIER, sym.team, False)
        in_world(run.match, shooter)
        run.players.append(shooter)
        self.assertTrue(run.match.switch_hero(shooter, heroes()[symmetra.HERO if shot == "orb" else SOLDIER]))
        shooter.team = sym.team
        impact = pillar
        if shot == "helix_splash":
            impact = min((piece for piece in fight.ice_wall.pieces.values()
                          if piece.feet[0] > pillar.feet[0]), key=lambda piece: piece.feet[0])
        shooter.mover.position[:] = (impact.feet[0], 1, 20 if shot == "orb" else 35)
        shooter.mover.body = None
        angles = aim(fight._eye(shooter), (impact.feet[0], 2, impact.feet[2]))
        hold = {}
        if shot == "orb":
            hold = {shooter: (*angles, 2)}
            for _ in range(28 if active else 60):
                run.step(hold)
        sentry = self.launch_at_pillar(run, sym, pillar, hold)
        for _ in range(55):
            if sentry.attached_at is not None and (not active or sentry.slow is not None):
                break
            run.step(hold)
        self.assertEqual(sentry.support_pillar, pillar.entity)
        self.assertEqual(sentry.slow is not None, active)
        self.assertFalse(fight.rockets)
        pillar.health = 5
        run.step({shooter: (*angles, 0 if shot == "orb" else 2)})
        [projectile] = fight.rockets
        self.assertAlmostEqual(projectile.impact_damage() + projectile.blast.amount, 120)
        launch = run.frame
        for _ in range(12):
            run.step({})
        self.assertIn(projectile, fight.rockets)
        self.assertIs(fight.ice_wall.pieces[pillar.entity], pillar)
        self.assertEqual(sentry.slow is not None, active)
        before = health(run, victim).total
        credited = sym.stats.value(stats.HERO_DAMAGE)
        slow = victim.body_script.component.entity_var(23186)
        ticks, broken_at, ice_hits = [], [], []
        damage_body, damage_ice = fight._damage, fight.ice_wall.damage

        def observe_body(shooter, target, amount, *args, **kwargs):
            if shooter is sym and target.owner is victim and amount > 0:
                self.assertAlmostEqual(slow.value(), .2)
                ticks.append(fight.tick)
            return damage_body(shooter, target, amount, *args, **kwargs)

        def observe_ice(piece, amount, **kwargs):
            damage_ice(piece, amount, **kwargs)
            ice_hits.append((piece.entity, amount))
            if piece is pillar and piece.health == 0:
                broken_at.append(fight.tick)

        with (patch.object(fight, "_damage", side_effect=observe_body),
              patch.object(fight.ice_wall, "damage", side_effect=observe_ice)):
            if expected_flight is None:
                while projectile in fight.rockets and run.frame < launch + 60:
                    run.step({})
                for _ in range(4):
                    run.step({})
            else:
                run.frame = launch + expected_flight + 4
                run.match.update(run.frame * combat.TICK, run.frame)
        [break_tick] = broken_at
        self.assertNotIn(projectile, fight.rockets)
        self.assertNotIn(pillar.entity, fight.ice_wall.pieces)
        self.assertEqual(len(fight.ice_wall.pieces), 4)
        self.assertEqual(ice_hits[0], (impact.entity, 120))
        if shot == "helix_splash":
            self.assertEqual(impact.health, 280)
            [splash] = [amount for entity, amount in ice_hits if entity == pillar.entity]
            self.assertGreater(splash, 5)
            self.assertLessEqual(splash, 80)
        self.assertNotIn(sentry, fight.sentries.sentries)
        self.assertFalse(slow.value())
        self.assertTrue(ticks)
        self.assertEqual(ticks[-1], break_tick - 1)
        loss = before - health(run, victim).total
        self.assertAlmostEqual(loss, sym.stats.value(stats.HERO_DAMAGE) - credited, places=4)
        self.assertEqual(shooter.stats.value(stats.HERO_DAMAGE), 0)
        run.match.update(run.frame * combat.TICK, run.frame)
        self.assertAlmostEqual(before - health(run, victim).total, loss, places=4)
        return break_tick - launch, loss, ticks

    @unittest.skipUnless(os.environ.get("ORPHEUS_EXTRA_GRAPH_BUNDLES"), "requires summon graph shard")
    def test_nearer_static_wall_keeps_sentry_when_ice_is_cancelled(self):
        run, sym, mei, _ = self.deploy()
        fight = run.match.combat
        static = World([(-5, 0, 4.4, 5, 0, 4.4, 5, 8, 4.4),
                        (-5, 0, 4.4, 5, 8, 4.4, -5, 8, 4.4)])
        with patch.object(fight, "_collision", return_value=static):
            for _ in range(40):
                run.step({sym: (-32768, 0, 8)})
        [sentry] = fight.sentries.sentries
        self.assertIsNotNone(sentry.attached_at)
        self.assertIsNone(sentry.support_pillar)
        self.assertAlmostEqual(sentry.bot.position[2], 4.41, places=4)
        run.step({mei: (0, 0, 16)})
        self.assertFalse(fight.ice_wall.pieces)
        self.assertEqual(fight.sentries.sentries, [sentry])
        self.assertEqual(health(run, sentry.bot).total, 30)

    @unittest.skipUnless(os.environ.get("ORPHEUS_EXTRA_GRAPH_BUNDLES"), "requires summon graph shard")
    def test_airborne_sentry_resolves_ice_expiry_in_retained_world_ticks(self):
        for expires_before_contact in (False, True):
            for gap in (False, True):
                with self.subTest(expires_before_contact=expires_before_contact, gap=gap):
                    self.check_airborne_sentry_ice_expiry(expires_before_contact, gap)

    def check_airborne_sentry_ice_expiry(self, expires_before_contact, gap):
        run, sym, _, _ = self.deploy()
        fight = run.match.combat
        for _ in range(40):
            run.step({sym: (-32768, 0, 8)})
            if fight.sentries.sentries:
                break
        [sentry] = fight.sentries.sentries
        run.step({})
        self.assertEqual(sentry.bot.moved_tick, run.frame)
        self.assertIsNone(sentry.attached_at)
        distance, pillar, _ = fight.ice_wall.cast(sentry.bot.position, sentry.direction, 4)
        flight_ticks = math.ceil(distance / (sentry.speed * combat.TICK))
        self.assertGreater(flight_ticks, 0)
        # Isolate the ordering of contact and expiry without waiting for
        # the full wall lifetime on each fixture.
        pillar.expires = run.frame + flight_ticks + (0 if expires_before_contact else 2)
        final_tick = run.frame + flight_ticks + 4
        if gap:
            run.frame = final_tick
            run.match.update(run.frame * combat.TICK, run.frame)
        else:
            while run.frame < final_tick:
                run.step({})
        self.assertNotIn(pillar.entity, fight.ice_wall.pieces)
        if expires_before_contact:
            self.assertIn(sentry, fight.sentries.sentries)
            self.assertIsNone(sentry.attached_at)
            self.assertLess(sentry.bot.position[2], pillar.feet[2] + pillar.depth)
        else:
            self.assertIsNotNone(sentry.attached_at)
            self.assertEqual(sentry.support_pillar, pillar.entity)
            self.assertNotIn(sentry, fight.sentries.sentries)
            self.assertNotIn(sentry.bot, run.match.bots)

    @unittest.skipUnless(os.environ.get("ORPHEUS_EXTRA_GRAPH_BUNDLES"), "requires summon graph shard")
    def test_new_sentry_only_moves_on_endpoint_during_older_sentry_replay(self):
        run, sym, _, _ = self.deploy()
        other = run.match.add_player(3, "Second Symmetra", SOLDIER, 0, False)
        in_world(run.match, other)
        run.players.append(other)
        self.assertTrue(run.match.switch_hero(other, heroes()[symmetra.HERO]))
        other.mover.position[:] = (1.4, 1, 6)
        other.mover.body = None
        fight = run.match.combat
        for frame in range(42):
            run.step({sym: (-32768, 0, 8), other: (-32768, 0, 8 if frame >= 2 else 0)})
            if len(fight.sentries.sentries) == 2:
                break
        old, new = fight.sentries.sentries
        self.assertIs(old.owner, sym)
        self.assertIs(new.owner, other)
        self.assertIsNotNone(old.bot.moved_tick)
        self.assertIsNone(old.attached_at)
        self.assertIsNone(new.bot.moved_tick)
        before = new.bot.position[2]
        run.frame += 8
        run.match.update(run.frame * combat.TICK, run.frame)
        self.assertIsNotNone(old.support_pillar)
        self.assertIsNone(new.attached_at)
        self.assertAlmostEqual(before - new.bot.position[2], new.speed * combat.TICK, places=4)

    def test_player_movement_blocked_until_wall_cancels_expires_or_center_breaks(self):
        for removal in ("cancel", "expiry", "break"):
            with self.subTest(removal=removal):
                self.check_wall_movement(removal)

    def check_wall_movement(self, removal):
        run, sym, mei, pieces = self.deploy()
        fight = run.match.combat
        base = run.match.collision_world
        center = min(pieces, key=lambda piece: abs(piece.feet[0]))
        yaw, pitch = aim(fight._eye(sym), (0, fight._eye(sym)[1], 0))
        run.players.remove(sym)

        def advance(count, buttons=0):
            for _ in range(count):
                run.step({})
                sym.take_commands([Command(run.frame, yaw=yaw, pitch=pitch,
                                           forward=127, buttons=buttons)])

        advance(55)
        self.assertGreater(sym.mover.position[2], center.feet[2] + center.depth)
        self.assertLess(sym.mover.position[2], 4)
        self.assertIs(run.match.collision_world, base)
        if removal == "cancel":
            self.assertTrue(any(contact.shape in sym.mover.world.addition.engine.shapes
                                for contact in sym.mover.body.contacts))
            physics = snapshot_mover(sym.mover)
            self.assertEqual(physics, snapshot_mover(sym.mover))
            self.assertNotEqual(physics["world_descriptor"], world_fingerprint(base))
            run.step({mei: (0, 0, 16)})
            self.assertFalse(fight.ice_wall.pieces)
        elif removal == "expiry":
            advance(center.expires - run.frame)
            self.assertFalse(fight.ice_wall.pieces)
        else:
            center.health = 2
            advance(8, buttons=1)
            self.assertNotIn(center.entity, fight.ice_wall.pieces)
            self.assertEqual(len(fight.ice_wall.pieces), 4)
        advance(45)
        self.assertLess(sym.mover.position[2], center.feet[2] - center.depth)
        if removal == "cancel":
            self.assertEqual(snapshot_mover(sym.mover)["world_descriptor"], world_fingerprint(base))

    @unittest.skipUnless(os.environ.get("ORPHEUS_EXTRA_GRAPH_BUNDLES"), "requires summon graph shard")
    def test_wall_breaks_active_sentry_lock_and_slow_until_cancel(self):
        run, sym, mei = arena()
        self.assertTrue(run.match.switch_hero(mei, heroes()[cryo.HERO]))
        mei.mover.position[:] = (0, 1, 1.2)
        fight = run.match.combat
        with patch.object(fight, "_collision", return_value=wall()):
            fire(run, sym, 40, buttons=8)
        fire(run, sym, 60, buttons=0)
        [sentry] = fight.sentries.sentries
        component = mei.body_script.component
        slow = component.entity_var(23186)
        self.assertEqual(sentry.target, mei.body)
        self.assertAlmostEqual(slow.value(), .2)
        _, old_aura = sentry.slow

        angles = aim(fight._eye(mei), (0, 1, 2.1))
        for button in (16, 0, 1, 0):
            run.step({mei: (*angles, button)})
        self.assertEqual(len(fight.ice_wall.pieces), 5)
        self.assertIsNone(sentry.target)
        self.assertIsNone(sentry.slow)
        self.assertNotIn(old_aura.id, component.instances)
        self.assertFalse(slow.value())
        before = health(run, mei).total
        for _ in range(20):
            run.step({})
        self.assertEqual(health(run, mei).total, before)
        self.assertTrue(all(piece.health == 400 for piece in fight.ice_wall.pieces.values()))

        run.step({mei: (*angles, 16)})
        self.assertFalse(fight.ice_wall.pieces)
        run.step({})
        self.assertEqual(sentry.target, mei.body)
        _, new_aura = sentry.slow
        self.assertIsNot(new_aura, old_aura)
        self.assertAlmostEqual(slow.value(), .2)
        self.assertLess(health(run, mei).total, before)

    def deploy(self, *, allied=False):
        run, sym, mei = mei_tests.SymmetraMeiWeaponTests().duel()
        if allied:
            run.match.game_map = replace(run.match.game_map, free_for_all=False)
            mei.team = sym.team
        fight = run.match.combat
        angles = aim(fight._eye(mei), (0, 1, 2.5))
        for button in (16, 0, 1, 0):
            run.step({mei: (*angles, button)})
        pieces = list(fight.ice_wall.pieces.values())
        self.assertEqual(len(pieces), 5)
        self.assertTrue(all(piece.health == 400 for piece in pieces))
        return run, sym, mei, pieces

    def test_actual_beam_damages_center_pillar_until_it_opens(self):
        run, sym, mei, pieces = self.deploy()
        fight = run.match.combat
        center = min(pieces, key=lambda p: abs(p.feet[0]))
        angles = aim(fight._eye(sym), (0, 2, 0))
        before = mei_tests.health(run, mei).total
        for _ in range(10):
            run.step({sym: (*angles, 1)})
        self.assertLess(center.health, 400)
        self.assertEqual(mei_tests.health(run, mei).total, before)
        self.assertTrue(all(p.health == 400 for p in pieces if p is not center))
        # Seed the last points to isolate the break and next-frame admission.
        center.health = 2
        for _ in range(15):
            run.step({sym: (*angles, 1)})
            if center.entity not in fight.ice_wall.pieces:
                break
        self.assertNotIn(center.entity, fight.ice_wall.pieces)
        self.assertEqual(mei_tests.health(run, mei).total, before)
        run.step({sym: (*angles, 1)})
        self.assertLess(mei_tests.health(run, mei).total, before)

    def test_actual_orb_damages_wall_without_hitting_mei(self):
        run, sym, mei, pieces = self.deploy()
        fight = run.match.combat
        center = min(pieces, key=lambda p: abs(p.feet[0]))
        angles = aim(fight._eye(sym), (0, 2, 0))
        before = mei_tests.health(run, mei).total
        for _ in range(90):
            run.step({sym: (*angles, 2)})
        run.step({sym: (*angles, 0)})
        self.assertTrue(any(shot.shooter is sym for shot in fight.rockets))
        for _ in range(25):
            run.step({})
        self.assertLess(center.health, 400)
        self.assertEqual(mei_tests.health(run, mei).total, before)
        self.assertFalse(fight.rockets)

    def test_natural_ice_expiry_orders_orb_contact_before_omitted_deflect(self):
        for offset in (-1, 0, 1):
            with self.subTest(contact_offset_from_expiry=offset):
                stepped = self.orb_and_deflect_across_ice_expiry(offset, gap=False)
                self.assertEqual(self.orb_and_deflect_across_ice_expiry(offset, gap=True), stepped)

    def orb_and_deflect_across_ice_expiry(self, offset, *, gap):
        run, owner, mei, genji, pieces, center, distance = self.deflect_ice_scene()
        fight = run.match.combat
        expiry = center.expires
        cast = expiry - math.ceil(distance / (25 * combat.TICK)) + offset
        run.frame = cast - 91
        run.match.update(run.frame * combat.TICK, run.frame)
        for _ in range(90):
            run.step({owner: (0, 0, 2)})
        run.step({genji: (-32768, 0, 16)})
        self.assertEqual(run.frame, cast)
        [orb] = fight.rockets
        hit = fight.ice_wall.cast(orb.position, orb.direction, 100)
        self.assertIs(hit[1], center)
        self.assertEqual(cast + math.ceil(hit[0] / (orb.speed * combat.TICK)), expiry + offset)
        run.step({genji: (-32768, 0, 0)})
        start = cast + 80
        run.match.update(start * combat.TICK, start)
        self.assertIn(orb, fight.rockets)
        self.assertIn(center.entity, fight.ice_wall.pieces)
        defender = next(target for target in fight._targets() if target.owner is genji)
        self.assertIsNotNone(deflection.state_of(defender))
        end = cast + 300
        for tick in (end,) if gap else range(start + 1, end + 1):
            run.match.update(tick * combat.TICK, tick)
        self.assertFalse(fight.ice_wall.pieces)
        self.assertNotIn(orb, fight.rockets)
        self.assertEqual(health(run, genji).total, 200)
        self.assertEqual(health(run, mei).total, 250)
        if offset < 0:
            self.assertIs(orb.shooter, owner)
            self.assertEqual(center.health, 280)
            self.assertEqual(health(run, owner).total, 225)
        else:
            self.assertIs(orb.shooter, genji)
            self.assertTrue(all(piece.health == 400 for piece in pieces))
            self.assertEqual(health(run, owner).total, 105)
            self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), 120)
        damaged_at = health(run, owner).last_damage_at
        hit_tick = round(damaged_at / combat.TICK) if damaged_at is not None else None
        return center.health, hit_tick, orb.shooter is genji

    def deflect_ice_scene(self):
        run, owner, mei, pieces = self.deploy()
        fixture = mei_tests.barrier_tests.PhotonBarrierTests()
        fight = run.match.combat
        fixture.move(owner, (0, 1, -43))
        fixture.move(mei, (20, 1, 5))
        genji = fixture.add_target(run, 3, (0, 1, 6))
        self.assertTrue(run.match.switch_hero(genji, heroes()[deflection.GENJI]))
        fixture.move(genji, (0, 1, 6))
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        genji.team = mei.team = 1
        owner.team = 0
        distance, center, _ = fight.ice_wall.cast(fight._eye(owner), (0, 0, 1), 100)
        return run, owner, mei, genji, pieces, center, distance

    def test_projectile_ice_break_orders_both_flights_before_omitted_deflect(self):
        for offset, helix_launched_first in ((-1, False), (0, False), (0, True), (1, False)):
            with self.subTest(helix_contact_offset=offset, helix_launched_first=helix_launched_first):
                stepped = self.orb_and_helix_break_ice(offset, helix_launched_first, gap=False)
                retained = self.orb_and_helix_break_ice(offset, helix_launched_first, gap=True)
                self.assertEqual(retained, stepped)

    def test_actual_ice_cancel_orders_orb_contact_between_catchup_updates(self):
        for offset in (-1, 0, 1):
            with self.subTest(cancel_offset_from_contact=offset):
                stepped = self.orb_and_deflect_across_ice_cancel(offset, gap=False)
                self.assertEqual(self.orb_and_deflect_across_ice_cancel(offset, gap=True), stepped)

    def orb_and_deflect_across_ice_cancel(self, offset, *, gap):
        run, owner, mei, genji, pieces, center, distance = self.deflect_ice_scene()
        fight = run.match.combat
        for _ in range(90):
            run.step({owner: (0, 0, 2)})
        run.step({genji: (-32768, 0, 16)})
        cast = run.frame
        [orb] = fight.rockets
        hit_distance, pillar, _ = fight.ice_wall.cast(orb.position, orb.direction, 100)
        self.assertIs(pillar, center)
        self.assertAlmostEqual(hit_distance, distance)
        contact_tick = cast + math.ceil(hit_distance / (orb.speed * combat.TICK))
        cancel_tick = contact_tick + offset
        self.assertLess(cancel_tick, center.expires)
        run.step({genji: (-32768, 0, 0)})
        self.assertEqual(orb.last_tick, run.frame)
        for tick in (cancel_tick,) if gap else range(run.frame + 1, cancel_tick + 1):
            run.match.update(tick * combat.TICK, tick)
        self.assertIn(center.entity, fight.ice_wall.pieces)
        self.assertEqual(orb in fight.rockets, offset < 0)
        health_before_cancel = center.health
        mei.take_commands([Command(cancel_tick, buttons=16)])
        self.assertFalse(fight.ice_wall.pieces)
        self.assertEqual(center.health, health_before_cancel)
        self.assertEqual(health_before_cancel, 400 if offset < 0 else 280)
        end = cast + 300
        for tick in (end,) if gap else range(cancel_tick + 1, end + 1):
            run.match.update(tick * combat.TICK, tick)
        self.assertFalse(fight.ice_wall.pieces)
        self.assertNotIn(orb, fight.rockets)
        self.assertEqual(health(run, mei).total, 250)
        self.assertEqual(health(run, genji).total, 200)
        self.assertIs(orb.shooter, genji if offset < 0 else owner)
        self.assertEqual(health(run, owner).total, 105 if offset < 0 else 225)
        self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), 120 if offset < 0 else 0)
        damaged_at = health(run, owner).last_damage_at
        hit_tick = round(damaged_at / combat.TICK) if damaged_at is not None else None
        return [piece.health for piece in pieces], hit_tick

    def test_actual_ice_placement_blocks_inbound_or_reflected_orb_between_updates(self):
        for after_reflect in (False, True):
            with self.subTest(after_reflect=after_reflect):
                stepped = self.place_ice_during_orb_flight(after_reflect, gap=False)
                self.assertEqual(self.place_ice_during_orb_flight(after_reflect, gap=True), stepped)

    def place_ice_during_orb_flight(self, after_reflect, *, gap):
        run, owner, mei = mei_tests.SymmetraMeiWeaponTests().duel()
        fixture = mei_tests.barrier_tests.PhotonBarrierTests()
        fight = run.match.combat
        fixture.move(owner, (0, 1, -43))
        fixture.move(mei, (3, 1, 0))
        genji = fixture.add_target(run, 3, (0, 1, 6))
        self.assertTrue(run.match.switch_hero(genji, heroes()[deflection.GENJI]))
        fixture.move(genji, (0, 1, 6))
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        genji.team = mei.team = 1
        owner.team = 0
        wall_aim = aim(fight._eye(mei), (0, 1, 2.5))
        for _ in range(90):
            run.step({owner: (0, 0, 2)})
        run.step({genji: (-32768, 0, 16), mei: (*wall_aim, 16)})
        cast = run.frame
        [orb] = fight.rockets
        run.step({genji: (-32768, 0, 0), mei: (*wall_aim, 0)})
        defender = next(target for target in fight._targets() if target.owner is genji)
        state = deflection.state_of(defender)
        self.assertIsNotNone(state)
        distance = deflection.box_contact(defender, state, orb.position, orb.direction, 100)
        self.assertIsNotNone(distance)
        reflect_tick = run.frame + math.ceil(distance / (orb.speed * combat.TICK))
        place_tick = reflect_tick + 1 if after_reflect else reflect_tick - 12
        for tick in (place_tick,) if gap else range(run.frame + 1, place_tick + 1):
            run.match.update(tick * combat.TICK, tick)
        self.assertIn(orb, fight.rockets)
        self.assertIs(orb.shooter, genji if after_reflect else owner)
        self.assertFalse(fight.ice_wall.pieces)
        mei.take_commands([Command(place_tick, yaw=wall_aim[0], pitch=wall_aim[1], buttons=1)])
        pieces = tuple(fight.ice_wall.pieces.values())
        self.assertEqual(len(pieces), 5)
        self.assertTrue(all(piece.health == 400 for piece in pieces))
        landed = []
        land = fight._landed

        def observe(rocket):
            if rocket is orb:
                landed.append(fight.tick - cast)
            land(rocket)

        end = cast + 180
        with patch.object(fight, "_landed", side_effect=observe):
            for tick in (end,) if gap else range(place_tick + 1, end + 1):
                run.match.update(tick * combat.TICK, tick)
        self.assertEqual(len(landed), 1)
        self.assertFalse(fight.rockets)
        self.assertEqual(health(run, owner).total, 225)
        self.assertEqual(health(run, genji).total, 200)
        self.assertEqual(health(run, mei).total, 250)
        center = min(pieces, key=lambda piece: abs(piece.feet[0]))
        self.assertEqual(center.health, 400 if after_reflect else 280)
        self.assertIs(orb.shooter, genji if after_reflect else owner)
        return landed, [piece.health for piece in pieces]

    def orb_and_helix_break_ice(self, offset, helix_launched_first, *, gap):
        run, owner, mei, genji, pieces, center, distance = self.deflect_ice_scene()
        fixture = mei_tests.barrier_tests.PhotonBarrierTests()
        fight = run.match.combat
        soldier = fixture.add_target(run, 4, (3, 1, -95 if helix_launched_first else -36))
        soldier.team = owner.team
        center.health = 5
        orb_contact = math.ceil(distance / (25 * combat.TICK))
        x, y, z = center.feet
        angles = aim(fight._eye(soldier), (x, y + 1.5, z))
        helix_contact, pillar, _ = fight.ice_wall.cast(fight._eye(soldier), combat.aim(*angles), 100)
        self.assertIs(pillar, center)
        launch = orb_contact + offset - math.ceil(helix_contact / (50 * combat.TICK))
        self.assertEqual(launch < 0, helix_launched_first)
        cast = run.frame + 91
        self.assertGreater(center.expires, cast + orb_contact + 1)
        start_age = max(launch + 2, 2)
        start = cast + start_age
        for age in range(-90, start_age + 1):
            run.step({
                owner: (0, 0, 2 if age < 0 else 0),
                genji: (-32768, 0, 16 if age == 0 else 0),
                soldier: (*angles, 2 if age == launch else 0),
            })
        [orb] = [rocket for rocket in fight.rockets if rocket.shooter is owner]
        [helix] = [rocket for rocket in fight.rockets if rocket.shooter is soldier]
        self.assertEqual(helix.speed, 50)
        self.assertEqual((orb.last_tick, helix.last_tick), (start, start))
        self.assertEqual(center.health, 5)
        for rocket, age in ((orb, orb_contact), (helix, orb_contact + offset)):
            contact, piece, _ = fight.ice_wall.cast(rocket.position, rocket.direction, 100)
            self.assertIs(piece, center)
            self.assertEqual(start_age + math.ceil(contact / (rocket.speed * combat.TICK)), age)
        defender = next(target for target in fight._targets() if target.owner is genji)
        self.assertIsNotNone(deflection.state_of(defender))
        breaks = []
        fly = fight._fly

        def observe(rocket):
            intact = center.entity in fight.ice_wall.pieces
            fly(rocket)
            if intact and center.entity not in fight.ice_wall.pieces:
                breaks.append((fight.tick - cast, rocket is orb))

        end = cast + 300
        with patch.object(fight, "_fly", side_effect=observe):
            for tick in (end,) if gap else range(start + 1, end + 1):
                run.match.update(tick * combat.TICK, tick)
        helix_breaks = offset < 0 or (offset == 0 and helix_launched_first)
        self.assertEqual(breaks, [(orb_contact + min(offset, 0), not helix_breaks)])
        self.assertFalse(fight.ice_wall.pieces)
        self.assertFalse(fight.rockets)
        self.assertEqual(health(run, genji).total, 200)
        self.assertEqual(health(run, mei).total, 250)
        if helix_breaks:
            self.assertIs(orb.shooter, genji)
            self.assertIs(helix.shooter, soldier)
        else:
            self.assertIs(orb.shooter, owner)
            self.assertIs(helix.shooter, genji)
        self.assertEqual(health(run, owner).total, 105)
        self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), 120)
        self.assertTrue(all(piece.health > 0 for piece in pieces if piece is not center))
        hit_tick = round(health(run, owner).last_damage_at / combat.TICK)
        return breaks, [piece.health for piece in pieces], hit_tick

    def test_allied_wall_blocks_beam_without_pillar_damage(self):
        run, sym, mei, pieces = self.deploy()
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        sym.team = mei.team
        target = mei_tests.barrier_tests.PhotonBarrierTests().add_target(run, 3, (0, 1, -.5))
        target.team = 1 - sym.team
        fight = run.match.combat
        angles = aim(fight._eye(sym), (0, 2, -.5))
        before = mei_tests.health(run, target).total
        for _ in range(20):
            run.step({sym: (*angles, 1)})
        self.assertTrue(all(piece.health == 400 for piece in pieces))
        self.assertEqual(mei_tests.health(run, target).total, before)

    def test_cancel_and_expiry_remove_live_columns(self):
        for cancel in (False, True):
            with self.subTest(cancel=cancel):
                run, _, mei, pieces = self.deploy()
                wall = run.match.combat.ice_wall
                self.assertGreater(wall.instance(mei).state(33).remaining(), 10)
                if cancel:
                    run.step({mei: (0, 0, 16)})
                else:
                    last = max(piece.expires for piece in pieces)
                    run.frame = last
                    run.match.update(last * .016, last)
                self.assertFalse(wall.pieces)

    def test_actual_melee_break_does_not_hit_mei_until_next_swing(self):
        run, sym, mei, pieces = self.deploy()
        fixture = mei_tests.barrier_tests.PhotonBarrierTests()
        fixture.move(sym, (0, 1, 3.8))
        fixture.move(mei, (0, 1, 1.6))
        fight = run.match.combat
        center = min(pieces, key=lambda p: abs(p.feet[0]))
        angles = aim(fight._eye(sym), (0, 2, 1.6))
        before = mei_tests.health(run, mei).total
        for _ in range(16):
            run.step({sym: (*angles, 0x800)})
        self.assertEqual(center.health, 370)
        self.assertEqual(mei_tests.health(run, mei).total, before)
        for _ in range(40):
            run.step({})
        # Isolate the breaking swing with a weakened pillar.
        center.health = 15
        for _ in range(16):
            run.step({sym: (*angles, 0x800)})
        self.assertNotIn(center.entity, fight.ice_wall.pieces)
        self.assertEqual(mei_tests.health(run, mei).total, before)
        for _ in range(40):
            run.step({})
        for _ in range(16):
            run.step({sym: (*angles, 0x800)})
        self.assertEqual(mei_tests.health(run, mei).total, before - 30)

    def test_allied_wall_blocks_actual_melee_without_damage(self):
        run, sym, mei, pieces = self.deploy()
        fixture = mei_tests.barrier_tests.PhotonBarrierTests()
        fixture.move(sym, (0, 1, 3.8))
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        sym.team = mei.team
        target = fixture.add_target(run, 3, (0, 1, 1.6))
        target.team = 1 - sym.team
        fight = run.match.combat
        angles = aim(fight._eye(sym), (0, 2, 1.6))
        before = mei_tests.health(run, target).total
        for _ in range(16):
            run.step({sym: (*angles, 0x800)})
        self.assertEqual(mei_tests.health(run, target).total, before)
        self.assertTrue(all(piece.health == 400 for piece in pieces))

    def test_actual_melee_cannot_damage_ice_through_static_cover(self):
        run, sym, mei, pieces = self.deploy()
        fixture = mei_tests.barrier_tests.PhotonBarrierTests()
        fixture.move(sym, (0, 1, 3.8))
        fixture.move(mei, (0, 1, 1.6))
        fight = run.match.combat
        angles = aim(fight._eye(sym), (0, 2, 1.6))
        with patch.object(fight, "_collision", return_value=wall()):
            for _ in range(16):
                run.step({sym: (*angles, 0x800)})
        self.assertTrue(all(piece.health == 400 for piece in pieces))
        self.assertEqual(mei_tests.health(run, mei).total, 250)

    def test_orb_splashes_adjacent_pillars_once_without_spill_when_center_breaks(self):
        for breaking in (False, True):
            with self.subTest(breaking=breaking):
                run, sym, mei, pieces = self.deploy()
                fixture = mei_tests.barrier_tests.PhotonBarrierTests()
                fixture.move(mei, (0, 1, 1.6))
                approach = fixture.add_target(run, 3, (1.4, 1, 3.6))
                fight = run.match.combat
                left, inner_left, center, inner_right, right = sorted(pieces, key=lambda p: p.feet[0])
                if breaking:
                    center.health = 100
                before = mei_tests.health(run, mei).total
                approach_before = mei_tests.health(run, approach).total
                angles = aim(fight._eye(sym), (0, 2, 1.6))
                for _ in range(90):
                    run.step({sym: (*angles, 2)})
                run.step({sym: (*angles, 0)})
                self.assertTrue(any(shot.shooter is sym for shot in fight.rockets))
                for _ in range(25):
                    run.step({})
                self.assertFalse(fight.rockets)
                self.assertEqual(center.health, 0 if breaking else 280)
                self.assertEqual(center.entity in fight.ice_wall.pieces, not breaking)
                # The 60-point blast interpolates from full damage at .5 m.
                self.assertEqual((inner_left.health, inner_right.health), (348.25, 348.25))
                self.assertEqual((left.health, right.health), (400, 400))
                self.assertEqual(mei_tests.health(run, mei).total, before)
                self.assertLess(mei_tests.health(run, approach).total, approach_before)
