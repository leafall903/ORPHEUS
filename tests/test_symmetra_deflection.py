"""Actual secondary projectile and primary beam against the live Deflect state."""

import math
import os
import unittest
from contextlib import nullcontext
from dataclasses import replace
from unittest.mock import patch

from test_game_combat import Run, aim
from test_symmetra_barriers import PhotonBarrierTests
from test_symmetra_charge import charge, orb_after_pvp_respawn
from test_symmetra_combat import arena, health
from test_symmetra_turrets import check_sentry_crosses_live_defense

from ow174.game import combat, deflection, phase_defenses, sigma_grasp, stats, symmetra
from ow174.game.collision import World
from ow174.game.content import heroes
from ow174.game.script.driver import ULT_COST


class SymmetraDeflectionTests(unittest.TestCase):
    def scene(self, facing=-32768):
        run, owner, genji = arena()
        self.assertTrue(run.match.switch_hero(genji, heroes()[deflection.GENJI]))
        genji.mover.position[:] = (0, 1, 6)
        owner.body_script.component.set(12918, 0)
        fight = run.match.combat

        def step(attack=0, defense=0, count=1):
            for _ in range(count):
                angles = aim(fight._eye(owner), fight._eye(genji))
                run.step({owner: (*angles, attack), genji: (facing, 0, defense)})

        def active():
            target = next(t for t in fight._targets() if t.owner is genji)
            return deflection.state_of(target)

        return run, owner, genji, step, active

    def test_orb_reflects_and_returns_damage_credit_to_genji(self):
        run, owner, genji, step, active = self.scene()
        fight = run.match.combat
        step(2, count=90)
        step(2, 16)
        self.assertIsNotNone(active())
        step()
        [orb] = fight.rockets
        self.assertTrue(orb.direct.deflectable)
        budget = orb.left
        for _ in range(30):
            if orb not in fight.rockets or orb.shooter is not owner:
                break
            step()
        self.assertIs(orb.shooter, genji)
        self.assertAlmostEqual(orb.left + orb.flown, budget)
        cost = genji.body_script.component.entity_var(ULT_COST).value()
        genji.body_script.set_ult_charge(cost - 90)
        del genji.body_script.charge_clock  # A snapshot created before Genji used the authored clock.
        step(count=30)
        self.assertEqual(health(run, genji).total, 200)
        self.assertAlmostEqual(health(run, owner).now[combat.SHIELDS_POOL], 5)
        self.assertAlmostEqual(health(run, owner).now[combat.HEALTH_POOL], 100)
        self.assertAlmostEqual(genji.stats.value(stats.HERO_DAMAGE), 120)
        self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 0)
        self.assertEqual(owner.body_script.component.entity_var(342).value(), 0)
        self.assertEqual(charge(genji), cost)
        self.assertNotIn(orb, fight.rockets)

    def test_orb_uses_its_authored_range_beyond_the_defensive_clock_budget(self):
        for distance, expected in ((450, "lethal"), (624, "hit"), (626, "miss")):
            with self.subTest(distance=distance, expected=expected):
                stepped = self.long_orb_across_deflect(distance, expected, gap=False)
                self.assertEqual(self.long_orb_across_deflect(distance, expected, gap=True), stepped)
                self.assertEqual(
                    self.long_orb_across_deflect(distance, expected, gap=True, split_after=180), stepped,
                )

    def long_orb_across_deflect(self, distance, expected, *, gap, split_after=None):
        run, owner, genji, step, active = self.scene()
        fight = run.match.combat
        old_body = genji.body
        victim = health(run, genji)
        lethal = expected == "lethal"
        if lethal:
            step(2, count=90)
            step(count=35)
            self.assertEqual(victim.total, 80)
        PhotonBarrierTests().move(genji, (0, 1, distance))
        step(2, count=90)
        step(2, 16)
        self.assertIsNotNone(active())
        step()
        [orb] = fight.rockets
        run.step({})
        start = run.frame
        self.assertEqual(orb.last_tick, start)
        self.assertEqual(orb.definition, symmetra.ORB_PROJECTILE)
        self.assertAlmostEqual(orb.left + orb.flown, 25.0)
        expiry_tick = start + math.ceil(orb.left / combat.TICK)
        end = start + 1880
        if split_after is not None:
            checkpoint = start + split_after
            run.match.update(checkpoint * combat.TICK, checkpoint)
            self.assertIsNone(active())
            self.assertIn(orb, fight.rockets)
        for at in (end,) if gap else range(start + 1, end + 1):
            run.match.update(at * combat.TICK, at)
        self.assertIsNone(active())
        self.assertFalse(fight.rockets)
        self.assertIs(orb.shooter, owner)
        self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), 0)
        self.assertEqual(health(run, owner).total, 225)
        if expected == "miss":
            self.assertEqual(victim.total, 200)
            self.assertIsNone(victim.last_damage_at)
            self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 0)
            self.assertEqual(orb.last_tick, expiry_tick)
            return expiry_tick, None
        impact = round(victim.last_damage_at / combat.TICK)
        self.assertEqual(orb.last_tick, impact)
        self.assertGreater(impact, start + phase_defenses.MAX_IDLE_STEPS)
        self.assertLess(impact, end - combat.COMBAT_CATCH_UP + 1)
        self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 200 if lethal else 120)
        if lethal:
            self.assertTrue(victim.dead)
            self.assertEqual(victim.death_tick, impact)
            self.assertNotEqual(genji.body, old_body)
            self.assertEqual(health(run, genji).total, 200)
            return impact, fight.replacement_ticks[genji.body]
        self.assertEqual(victim.total, 80)
        self.assertEqual(genji.body, old_body)
        return impact, None

    def test_active_deflect_does_not_redirect_primary_beam(self):
        run, owner, genji, step, active = self.scene()
        step(defense=16)
        self.assertIsNotNone(active())
        step(1, count=5)
        self.assertAlmostEqual(health(run, genji).total, 200 - 5 * 60 * .016, places=4)
        self.assertEqual(health(run, owner).total, 225)

    def test_actual_orb_respects_deflect_expiry_without_commands(self):
        for arrival in (125, 126):
            with self.subTest(arrival=arrival):
                stepped = self.orb_at_deflect_expiry(arrival, gap=False)
                self.assertEqual(self.orb_at_deflect_expiry(arrival, gap=True), stepped)
                self.assertEqual(self.orb_at_deflect_expiry(arrival, gap=True, end_age=205), stepped)
                self.assertEqual(self.orb_at_deflect_expiry(arrival, gap=True, end_age=340), stepped)

    def test_lethal_orb_after_deflect_expiry_uses_the_omitted_impact_tick(self):
        stepped = self.orb_at_deflect_expiry(126, gap=False, lethal=True)
        self.assertEqual(self.orb_at_deflect_expiry(126, gap=True, end_age=205, lethal=True), stepped)

    def test_lethal_orb_in_one_gap_replaces_genji_at_the_actual_respawn_tick(self):
        for endpoint in (800, 815, 880):
            with self.subTest(endpoint=endpoint):
                stepped = self.orb_at_deflect_expiry(126, gap=False, end_age=endpoint, lethal=True)
                retained = self.orb_at_deflect_expiry(126, gap=True, end_age=endpoint, lethal=True)
                self.assertEqual(retained, stepped)

    def test_lethal_orb_replaces_genji_while_another_eligible_orb_is_still_flying(self):
        options = {"end_age": 880, "lethal": True, "parallel_flight": True}
        stepped = self.orb_at_deflect_expiry(126, gap=False, **options)
        self.assertEqual(self.orb_at_deflect_expiry(126, gap=True, **options), stepped)

    def test_omitted_orb_death_keeps_respawn_and_retry_timing_across_updates(self):
        for retry in (False, True):
            with self.subTest(retry=retry):
                endpoint = 895 if retry else 880
                options = {"end_age": endpoint, "lethal": True, "retry": retry}
                checkpoints = (205, 760) if retry else (205,)
                stepped = self.orb_at_deflect_expiry(126, gap=False, **options)
                retained = self.orb_at_deflect_expiry(126, gap=True, checkpoints=checkpoints, **options)
                self.assertEqual(retained, stepped)

    def test_mixed_orb_and_accretion_keep_omitted_impact_order(self):
        for rock_start in (60, 75):
            with self.subTest(rock_start=rock_start):
                stepped = self.mixed_orb_and_accretion(rock_start, gap=False)
                self.assertEqual(self.mixed_orb_and_accretion(rock_start, gap=True), stepped)
                self.assertEqual(self.mixed_orb_and_accretion(rock_start, gap=True, end_age=340), stepped)

    def mixed_orb_and_accretion(self, rock_start, *, gap, end_age=205):
        run, owner, genji, _, active = self.scene()
        fixture = PhotonBarrierTests()
        fight = run.match.combat
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        owner.team, genji.team = 0, 1
        fixture.move(genji, (0, 1, 50))
        sigma = fixture.add_target(run, 3, (3, 1, 40))
        self.assertTrue(run.match.switch_hero(sigma, heroes()[sigma_grasp.SIGMA]))
        sigma.team = owner.team
        fixture.move(sigma, (3, 1, 40))
        rear = fixture.add_target(run, 4, (0, 1, 58))
        rear.team = genji.team
        genji_pool = health(run, genji)
        genji_pool.now[combat.HEALTH_POOL] = 65
        for _ in range(90):
            run.step({owner: (0, 0, 2)})
        run.step({genji: (-32768, 0, 16)})
        cast = run.frame
        [orb] = fight.rockets
        self.assertTrue(orb.deflectable())
        defender = next(target for target in fight._targets() if target.owner is genji)
        state = active()
        self.assertIsNotNone(state)
        contact = deflection.box_contact(defender, state, orb.position, orb.direction, 100)
        self.assertIsNotNone(contact)
        fixture.move(genji, (0, 1, 50 + 124.5 * orb.speed * combat.TICK - contact))
        x, y, z = genji.mover.position
        angles = aim(fight._eye(sigma), (x, y + 1.1, z))
        for age in range(1, 118):
            run.step({genji: (-32768, 0, 0), sigma: (*angles, 16 if age == rock_start else 0)})
        [rock] = [rocket for rocket in fight.rockets if rocket.shooter is sigma]
        self.assertFalse(rock.deflectable())
        self.assertIsNotNone(rock.last_tick)
        self.assertIsNotNone(orb.last_tick)
        self.assertIsNotNone(active())
        self.assertFalse(genji_pool.dead)
        endpoint = cast + end_age
        for tick in (endpoint,) if gap else range(run.frame + 1, endpoint + 1):
            run.match.update(tick * combat.TICK, tick)
        self.assertTrue(genji_pool.dead)
        self.assertEqual(genji_pool.killer, sigma.body)
        [deadline] = [when for when, player in fight.respawns if player is genji]
        self.assertAlmostEqual(deadline - genji_pool.died_at, combat.RESPAWN)
        if rock_start == 60:
            self.assertLess(genji_pool.death_tick, cast + 125)
            self.assertIs(orb.shooter, owner)
            self.assertEqual(health(run, rear).total, 80)
            self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 120)
            self.assertNotIn(orb, fight.rockets)
        else:
            self.assertGreater(genji_pool.death_tick, cast + 125)
            self.assertIs(orb.shooter, genji)
            self.assertEqual(health(run, rear).total, 200)
            for tick in range(endpoint + 1, cast + 276):
                if orb not in fight.rockets:
                    break
                run.match.update(tick * combat.TICK, tick)
            self.assertEqual(health(run, owner).total, 105)
            self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), 120)
        self.assertNotIn(orb, fight.rockets)
        self.assertEqual(sigma.stats.value(stats.HERO_DAMAGE), 65)
        impact_pool = health(run, rear if rock_start == 60 else owner)
        return (genji_pool.death_tick - cast, round((deadline / combat.TICK) - cast),
                round(impact_pool.last_damage_at / combat.TICK) - cast)

    def test_deflect_recovery_and_cooldown_finish_without_owner_commands(self):
        run, _, genji, step, active = self.scene()
        fight = run.match.combat
        step(defense=16)
        cast = run.frame
        state = active()
        self.assertIsNotNone(state)
        ability = state.instance.state(0)
        for elapsed, still_active in ((125, True), (126, False)):
            run.frame = cast + elapsed
            run.match.update(run.frame * combat.TICK, run.frame)
            self.assertEqual(active() is not None, still_active)
            self.assertEqual(ability.remaining(), 0)
        run.frame = cast + 140
        run.match.update(run.frame * combat.TICK, run.frame)
        self.assertEqual(ability.cur, 8)
        self.assertGreater(ability.remaining(), 0)
        step(defense=16)
        self.assertIsNone(active())
        run.frame = cast + 650
        run.match.update(run.frame * combat.TICK, run.frame)
        self.assertEqual(ability.remaining(), 0)
        self.assertNotIn(genji.body, fight.phase_clocks)
        step()
        step(defense=16)
        self.assertIsNotNone(active())

    def test_actual_swift_strike_cancels_deflect_before_an_orb_arrives(self):
        for cancel in (False, True):
            with self.subTest(cancel=cancel):
                run, owner, genji, _, active = self.scene()
                fight = run.match.combat
                PhotonBarrierTests().move(genji, (0, 1, 30))
                for _ in range(90):
                    run.step({owner: (0, 0, 2)})
                run.step({genji: (-32768, 0, 16)})
                self.assertIsNotNone(active())
                [orb] = fight.rockets
                run.step({genji: (-32768, 0, 8 if cancel else 0)})
                self.assertEqual(active() is None, cancel)
                self.assertIn(orb, fight.rockets)
                for _ in range(85):
                    if orb.shooter is genji or orb not in fight.rockets:
                        break
                    run.step({genji: (-32768, 0, 0)})
                self.assertEqual(health(run, genji).total, 80 if cancel else 200)
                self.assertIs(orb.shooter, owner if cancel else genji)
                self.assertEqual(orb not in fight.rockets, cancel)

    def orb_at_deflect_expiry(
        self, arrival, *, gap, end_age=150, lethal=False, parallel_flight=False, checkpoints=(), retry=False,
    ):
        run, owner, genji, _, active = self.scene()
        fight = run.match.combat
        move = PhotonBarrierTests().move
        move(genji, (0, 1, 50))
        genji_pool = health(run, genji)
        old_body, old_script = genji.body, genji.body_script
        if lethal:
            genji_pool.now[combat.HEALTH_POOL] = 100
        if parallel_flight:
            fixture = PhotonBarrierTests()
            second = fixture.add_target(run, 3, (0, 1, -40))
            self.assertTrue(run.match.switch_hero(second, heroes()[symmetra.HERO]))
            move(second, (0, 1, -40))
        for _ in range(90):
            commands = {owner: (0, 0, 2)}
            if parallel_flight:
                commands[second] = (-32768, 0, 2)
            run.step(commands)
        if parallel_flight:
            # Keep the second owner's release pointed away from the duel.
            run.step({owner: (0, 0, 2), second: (-32768, 0, 0)})
            [other_orb] = fight.rockets
            self.assertIs(other_orb.shooter, second)
        run.step({genji: (-32768, 0, 16)})
        cast_tick = run.frame
        [orb] = [rocket for rocket in fight.rockets if rocket.shooter is owner]
        state = active()
        self.assertIsNotNone(state)
        defender = next(target for target in fight._targets() if target.owner is genji)
        contact = deflection.box_contact(defender, state, orb.position, orb.direction, 100)
        self.assertIsNotNone(contact)
        distance = (arrival - .5) * orb.speed * combat.TICK
        move(genji, (0, 1, 50 + distance - contact))
        run.step({genji: (-32768, 0, 0)})
        self.assertEqual(orb.last_tick, run.frame)
        start = cast_tick + 110
        run.match.update(start * combat.TICK, start)
        self.assertIsNotNone(active())
        self.assertIn(orb, fight.rockets)
        returns = []
        trace = deflection.trace

        def observe(*args):
            contact = trace(*args)
            if contact is not None:
                self.assertIs(contact[1].owner, genji)
                returns.append(fight.tick - cast_tick)
            return contact

        end = cast_tick + end_age
        allocate = run.match._next_body_id
        attempts = []

        def observe_allocation(player, *, at):
            if player is genji:
                attempts.append(fight.tick - cast_tick)
                if retry and len(attempts) == 1:
                    return None
            return allocate(player, at=at)

        with (
            patch.object(deflection, "trace", side_effect=observe),
            patch.object(run.match, "_next_body_id", side_effect=observe_allocation),
        ):
            ticks = (*(cast_tick + age for age in checkpoints), end) if gap else range(start + 1, end + 1)
            for tick in ticks:
                run.match.update(tick * combat.TICK, tick)
                if checkpoints and tick == cast_tick + checkpoints[0]:
                    self.assertTrue(genji_pool.dead)
                    self.assertIn(old_body, fight.early_projectile_respawns)
                if checkpoints and tick == cast_tick + checkpoints[-1] and retry:
                    self.assertEqual(attempts, [752])
                    self.assertEqual(genji.body, old_body)
                    [pending] = [when for when, player in fight.respawns if player is genji]
                    self.assertAlmostEqual(pending, (cast_tick + 752) * combat.TICK + combat.RESPAWN_RETRY)
        self.assertIsNone(active())
        self.assertEqual(returns, [125] if arrival == 125 else [])
        self.assertEqual(genji_pool.total, 200 if returns else 0 if lethal else 80)
        owner_pool = health(run, owner)
        if returns:
            self.assertIs(orb.shooter, genji)
            for tick in range(end + 1, cast_tick + 276):
                if orb not in fight.rockets:
                    break
                run.match.update(tick * combat.TICK, tick)
            self.assertEqual(owner_pool.total, 105)
            self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), 120)
        else:
            self.assertEqual(owner_pool.total, 225)
        self.assertNotIn(orb, fight.rockets)
        if parallel_flight:
            self.assertIn(other_orb, fight.rockets)
            self.assertEqual(other_orb.last_tick, end)
            self.assertLess(other_orb.position[2], -350)
            self.assertEqual(health(run, second).total, 225)
        impacted = owner_pool if returns else genji_pool
        hit_age = round(impacted.last_damage_at / combat.TICK) - cast_tick
        self.assertLess(hit_age, 260 if returns else 142)
        if lethal:
            self.assertTrue(genji_pool.dead)
            self.assertEqual(genji_pool.killer, owner.body)
            self.assertEqual(genji_pool.death_tick, cast_tick + hit_age)
            deadline = genji_pool.died_at + combat.RESPAWN
            if end * combat.TICK >= deadline:
                self.assertNotEqual(genji.body, old_body)
                self.assertTrue(old_script.retired)
                self.assertEqual(health(run, genji).total, 200)
                self.assertNotIn(old_body, fight.early_projectile_respawns)
                self.assertFalse(any(player is genji for _, player in fight.respawns))
                replacement = fight.replacement_ticks[genji.body]
                self.assertEqual(attempts, [752, 768] if retry else [752])
                self.assertEqual(replacement, cast_tick + attempts[-1])
                charge_gain = charge(genji) - old_script.component.entity_var(342).value()
                tick_ms = round(combat.TICK * 1000)
                self.assertEqual(charge_gain, 5 * ((end - replacement) * tick_ms // 1000))
                return (returns, genji_pool.total, owner_pool.total, hit_age,
                        replacement - cast_tick, charge_gain)
            [pending] = [when for when, player in fight.respawns if player is genji]
            self.assertAlmostEqual(pending, deadline)
        return returns, genji_pool.total, owner_pool.total, hit_age

    def test_genji_authored_charge_advances_without_input_and_survives_actual_death(self):
        run, owner, genji, step, _active = self.scene()
        fight = run.match.combat
        genji.body_script.set_ult_charge(15)
        run.frame += 126
        run.match.update(run.frame * combat.TICK, run.frame)
        self.assertEqual(charge(genji), 25)
        pool = health(run, genji)
        pool.now[combat.HEALTH_POOL] = 1
        for _ in range(6):
            step(attack=1)
            if pool.dead:
                break
        self.assertTrue(pool.dead)
        self.assertEqual(pool.killer, owner.body)
        old_body = genji.body
        [deadline] = [when for when, player in fight.respawns if player is genji]
        run.frame = math.ceil(deadline / combat.TICK) - 1
        run.match.update(run.frame * combat.TICK, run.frame)
        self.assertEqual(genji.body, old_body)
        self.assertEqual(charge(genji), 25)
        run.frame += 2
        run.match.update(run.frame * combat.TICK, run.frame)
        self.assertNotEqual(genji.body, old_body)
        self.assertEqual(charge(genji), 25)

    def test_deflect_returns_an_orb_launched_by_the_original_body_to_its_replacement(self):
        for blade_active in (False, True):
            with self.subTest(blade_active=blade_active):
                self.check_reflect_charge_after_orb_owner_respawn(blade_active)

    def check_reflect_charge_after_orb_owner_respawn(self, blade_active):
        run, owner, genji, orb = orb_after_pvp_respawn(self, heroes()[deflection.GENJI])
        fight = run.match.combat
        PhotonBarrierTests().move(owner, (0, 1, 245))
        owner_pool, genji_pool = health(run, owner), health(run, genji)
        initial_charge = charge(owner)
        defender = next(target for target in fight._targets() if target.owner is genji)
        run.step({genji: (-32768, 0, 16)})
        self.assertIsNotNone(deflection.state_of(defender))
        for _ in range(50):
            if orb.shooter is genji:
                break
            run.step({genji: (-32768, 0, 0)})
        self.assertIs(orb.shooter, genji)
        self.assertIn(orb, fight.rockets)
        self.assertEqual(owner_pool.total, 225)
        self.assertEqual(genji_pool.total, 200)
        self.assertAlmostEqual(orb.left + orb.flown, 25)
        if blade_active:
            genji.body_script.set_ult_charge(genji.body_script.component.entity_var(ULT_COST).value())
            run.step({genji: (-32768, 0, 32)})
        resolve = fight._damage
        damage_charge = []

        def observe(shooter, target, amount, *args, **kwargs):
            before = charge(genji)
            applied = resolve(shooter, target, amount, *args, **kwargs)
            if shooter is genji and target.owner is owner:
                self.assertEqual(bool(genji.body_script.component.entity_var(842).value()), blade_active)
                damage_charge.append((amount, charge(genji) - before))
            return applied

        with patch.object(fight, "_damage", side_effect=observe):
            for _ in range(70):
                if orb not in fight.rockets:
                    break
                run.step({})
        self.assertNotIn(orb, fight.rockets)
        self.assertEqual(owner_pool.total, 105)
        self.assertEqual(genji_pool.total, 200)
        self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), 120)
        self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 0)
        self.assertEqual(charge(owner), initial_charge)
        self.assertEqual(damage_charge, [(60, 0 if blade_active else 60)] * 2)
        if blade_active:
            self.assertEqual(charge(genji), 0)
        self.assertFalse(owner.client.messages(combat.HIT))
        hits = [hit["+0x78"] for hit in genji.client.messages(combat.HIT)]
        self.assertEqual([hit["+0x0"] for hit in hits], [{"+0x0": owner.body}] * 2)
        self.assertEqual(sum(hit["+0x4"] for hit in hits), 120)

    def test_allied_deflect_redirects_actual_rifle_by_facing_and_respects_cover(self):
        for reason in ("return", "miss", "rear", "cover"):
            with self.subTest(reason=reason):
                self.check_rifle_deflect(reason)

    def check_rifle_deflect(self, reason):
        run = Run(names=("Symmetra", "Genji", "Soldier"))
        owner, genji, soldier = run.players
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        self.assertTrue(run.match.switch_hero(owner, heroes()[symmetra.HERO]))
        self.assertTrue(run.match.switch_hero(genji, heroes()[deflection.GENJI]))
        owner.team = genji.team = 0
        soldier.team = 1
        owner.mover.position[:] = (0, 1, 0)
        genji.mover.position[:] = (0, 1, 3)
        soldier.mover.position[:] = (0, 1, 6)
        fight = run.match.combat
        yaw = {"return": 0, "miss": 5461, "rear": -32768, "cover": 0}[reason]
        run.step({genji: (yaw, 0, 16)})
        target = next(t for t in fight._targets() if t.owner is genji)
        self.assertIsNotNone(deflection.state_of(target))
        pools = [health(run, player) for player in run.players]
        before = [pool.total for pool in pools]
        wall = World([(-5, 0, 4.5, 5, 0, 4.5, 5, 8, 4.5),
                      (-5, 0, 4.5, 5, 8, 4.5, -5, 8, 4.5)])
        cover = patch.object(fight, "_collision", return_value=wall) if reason == "cover" else nullcontext()
        angles = aim(fight._eye(soldier), (0, 2.25, 0))
        with cover, patch.object(fight, "_hitscan", wraps=fight._hitscan) as shots:
            for _ in range(6):
                run.step({genji: (yaw, 0, 0), soldier: (*angles, 1)})
                if shots.call_count:
                    break
        self.assertEqual(shots.call_count, 1)
        hit = shots.call_args.args[4]
        self.assertTrue(hit.deflectable)
        self.assertEqual((hit.amount, hit.critical), (20, 2))
        expected = [0, 40 if reason == "rear" else 0, 40 if reason == "return" else 0]
        self.assertEqual([start - pool.total for start, pool in zip(before, pools, strict=True)], expected)
        self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), expected[2])
        self.assertEqual(soldier.stats.value(stats.HERO_DAMAGE), expected[1])

    @unittest.skipUnless(os.environ.get("ORPHEUS_EXTRA_GRAPH_BUNDLES"), "requires the barrier actor")
    def test_actual_rifle_resolves_nearer_photon_barrier_or_deflect(self):
        for order in ("wall_first", "deflect_first", "wall_on_return"):
            with self.subTest(order=order):
                fixture = PhotonBarrierTests()
                run, owner, soldier, wall = fixture.deploy()
                fight = run.match.combat
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                owner.team, soldier.team = 0, 1
                position = (0, 0, 3 if order == "deflect_first" else -1)
                genji = fixture.add_target(run, 3, position)
                self.assertTrue(run.match.switch_hero(genji, heroes()[deflection.GENJI]))
                if order == "wall_on_return":
                    soldier.team, genji.team = owner.team, 1
                fixture.move(genji, position)
                fixture.move(owner, (0, 0, -2))
                run.step({genji: (0, 0, 16)})
                genji_target = next(target for target in fight._targets() if target.owner is genji)
                self.assertIsNotNone(deflection.state_of(genji_target))
                wall_vitals = fight.vitals[wall.bot.entity]
                self.assertEqual(wall_vitals.total, 4000)
                angles = aim(fight._eye(soldier), (0, 1.25, -2))
                with (patch.object(fight, "_hitscan", wraps=fight._hitscan) as shots,
                      patch.object(fight, "trace_barrier", wraps=fight.trace_barrier) as traces):
                    for _ in range(6):
                        run.step({soldier: (*angles, 1)})
                        if shots.call_count:
                            break
                self.assertEqual(shots.call_count, 1)
                sources = [soldier] if order == "wall_first" else [soldier, genji]
                self.assertEqual([call.args[0] for call in traces.call_args_list], sources)
                self.assertEqual(health(run, owner).total, 225)
                self.assertEqual(health(run, genji).total, 200)
                self.assertEqual(wall_vitals.total, 4000 if order == "deflect_first" else 3980)
                returned = 200 - health(run, soldier).total
                if order == "deflect_first":
                    self.assertGreater(returned, 0)
                else:
                    self.assertEqual(returned, 0)
                self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), returned)

    @unittest.skipUnless(os.environ.get("ORPHEUS_EXTRA_GRAPH_BUNDLES"), "requires the summon graph shard")
    def test_live_deflect_does_not_redirect_sentry_launch(self):
        run, owner, genji, step, active = self.scene()
        step(defense=16)
        check_sentry_crosses_live_defense(self, run, owner, genji, step, active)

    def test_rear_orb_hits_and_cover_stops_orb_before_deflect(self):
        for cover in (False, True):
            with self.subTest(cover=cover):
                run, owner, genji, step, active = self.scene(-32768 if cover else 0)
                fight = run.match.combat
                step(2, count=90)
                step(2, 16)
                self.assertIsNotNone(active())
                step()
                [orb] = fight.rockets
                wall = World([(-5, 0, 2, 5, 0, 2, 5, 8, 2),
                              (-5, 0, 2, 5, 8, 2, -5, 8, 2)])
                with patch.object(fight, "_collision", return_value=wall) if cover else nullcontext():
                    step(count=30)
                self.assertIs(orb.shooter, owner)
                self.assertNotIn(orb, fight.rockets)
                self.assertEqual(health(run, owner).total, 225)
                self.assertAlmostEqual(health(run, genji).total, 200 if cover else 80)
