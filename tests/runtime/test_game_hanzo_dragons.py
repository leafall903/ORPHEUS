"""Reconstructed moving dragons execute actual command, capsule and health paths.

Requires ORPHEUS_HANZO_PROFILE and ORPHEUS_NPC_TEST_DATA, both external read-only
inputs. Rate units/geometry/overlap policy are reconstructed, not native proof.
"""

import math
import os
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import test_game_hanzo as hero_tests
import test_game_hanzo_npc as npc_tests
import test_server_snapshot as snapshot_tests
from test_game_combat import KANEZAKA_DM, Run, aim, in_world

from ow174.game import bodytrace, combat, dragon, hanzo_control, movelog, npc, npc_control
from ow174.game.body_vm_snapshot import export_component_vm, snapshot_digest as vm_snapshot_digest
from ow174.game.commands import Command
from ow174.game.content import SOLDIER, heroes
from ow174.game.server_snapshot import snapshot_digest


def prepared_profile():
    path = os.environ.get("ORPHEUS_HANZO_PROFILE")
    if not path:
        raise unittest.SkipTest("requires explicitly extracted Hanzo profile")
    return dragon.load_profile(Path(path), reconstructed_rates=True)


class DragonPlayerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.profile = prepared_profile()

    def setUp(self):
        for module in (bodytrace, movelog):
            self.enterContext(patch.object(module, "ENABLED", False))

    def arena(self, distance=20):
        run, hero, victim = hero_tests.HanzoCombatTests().arena(distance)
        run.match.combat.configure_dragonstrike(self.profile)
        hero.body_script.ult_seconds = 0
        hero.body_script.set_ult_charge(1680)
        return run, hero, victim

    @staticmethod
    def cast(run, hero, frames=240, yaw=0):
        run.step({hero: (yaw, 0, 32)})
        for _ in range(frames):
            run.step({hero: (yaw, 0, 0)})

    def test_profile_requires_declared_rate_reconstruction(self):
        with self.assertRaises(ValueError):
            dragon.load_profile(Path(os.environ["ORPHEUS_HANZO_PROFILE"]))
        self.assertEqual((self.profile.lifetime, self.profile.speed, self.profile.radius), (20, 12, 4))
        self.assertEqual((self.profile.segments, self.profile.spacing, self.profile.initial_hit_limit),
                         (7, 5, 99))

    def test_range_transition_creates_dragons_that_damage_and_kill(self):
        run, hero, victim = self.arena()
        origin = tuple(run.match.combat._eye(hero))
        self.cast(run, hero)
        fight = run.match.combat
        self.assertTrue(fight.vitals[victim.body].dead)
        self.assertEqual(fight.vitals[victim.body].killer, hero.body)
        flight, = fight.dragons
        self.assertAlmostEqual(flight.origin[2] - origin[2], 10)
        self.assertFalse(fight.rockets)

    def test_batched_target_commands_preserve_moving_dragon_doses(self):
        def run_case(batched):
            run, hero, target = self.arena()
            match, fight = run.match, run.match.combat
            spawn = tuple(target.mover.position)
            self.assertTrue(match.switch_hero(target, heroes()[0x02E0000000000040]))
            target.mover.position[:] = spawn
            target.mover.body = None
            for _ in range(25):
                run.step({})
            pool = fight.vitals_of(next(pose for pose in fight._targets() if pose.owner is target))
            starting_health = pool.total
            run.step({hero: (0, 0, 32)})
            for _ in range(160):
                run.step({})
                if pool.total < starting_health:
                    break
            self.assertLess(pool.total, starting_health)
            flight, = fight.dragons
            self.assertEqual(flight.direction, (0, 0, 1))

            offset = 1000
            core_radius = flight.profile.radius * flight.profile.core_factor
            ox, oy, _ = flight.origin
            def step():
                before = pool.total
                run.frame += 1
                match.update(run.frame * combat.TICK, run.frame)
                target.take_commands([Command(run.frame + offset, right=127)])
                return before - pool.total

            for _ in range(90):
                step()
                x, y, _ = target.mover.position
                if math.hypot(x - ox, y - oy) >= core_radius - 0.45:
                    break
            self.assertLess(math.hypot(x - ox, y - oy), core_radius)
            before = pool.total
            age = flight.age
            gap = 12
            if batched:
                target.take_commands([Command(run.frame + offset + n, right=127) for n in range(1, gap + 1)])
                run.frame += gap
                match.update(run.frame * combat.TICK, run.frame)
            else:
                doses = {round(step(), 6) for _ in range(gap)}
                expected = {round(rate * combat.TICK, 6) for rate in
                            (flight.profile.core_rate, flight.profile.outer_rate)}
                self.assertEqual(doses, expected)
            self.assertEqual(fight.dragons, [flight])
            self.assertAlmostEqual(flight.age - age, gap * combat.TICK)
            return tuple(target.mover.position), before - pool.total

        endpoint, damage = run_case(batched=False)
        batched_endpoint, batched_damage = run_case(batched=True)
        self.assertEqual(batched_endpoint, endpoint)
        self.assertAlmostEqual(batched_damage, damage)

    def test_moving_dragon_damage_changes_when_orisa_fortify_expires(self):
        continuous = self.damage_across_fortify_expiry(skipped=False)
        self.assertAlmostEqual(self.damage_across_fortify_expiry(skipped=True), continuous, places=4)

    def damage_across_fortify_expiry(self, *, skipped):
        run, hero, orisa = self.arena()
        match = run.match
        fight = match.combat
        x, y, z = orisa.mover.position
        self.assertTrue(match.switch_hero(orisa, heroes()[0x02E000000000013E]))
        orisa.mover.position[:] = (x + 10, y, z)
        orisa.mover.body = None
        for _ in range(25):
            run.step({})
        run.step({hero: (0, 0, 32), orisa: (0, 0, 8)})
        fortify = combat._active_fortify(orisa)
        self.assertIsNotNone(fortify)
        for _ in range(250):
            if fortify.instance.vars[215].value() < 0.12:
                break
            run.step({})
        self.assertTrue(fortify.active)
        flight, = fight.dragons
        self.assertFalse(fight.rockets)
        target = next(t for t in fight._targets() if t.owner is orisa)
        pool = fight.vitals_of(target)
        self.assertEqual(pool.total, 450)
        orisa.mover.position[:] = (x, y, z)
        orisa.mover.body = None
        for _ in range(4):
            before = pool.total
            run.step({})
            if pool.total < before:
                break
        self.assertTrue(fortify.active)
        self.assertAlmostEqual(before - pool.total, 1.44, places=4)
        start_hp = pool.total
        if skipped:
            tick = run.frame + 15
            match.update(tick * combat.TICK, tick, frames=False)
        else:
            for _ in range(15):
                run.step({})
        damage = start_hp - pool.total
        self.assertFalse(fortify.active)
        before = pool.total
        if skipped:
            match.update((tick + 1) * combat.TICK, tick + 1, frames=False)
        else:
            run.step({})
        self.assertEqual(fight.dragons, [flight])
        self.assertAlmostEqual(before - pool.total, 2.4, places=4)
        return damage

    def test_fresh_fortify_only_reduces_dragon_doses_from_gap_endpoint(self):
        run, hero, orisa = self.arena()
        match = run.match
        fight = match.combat
        x, y, z = orisa.mover.position
        self.assertTrue(match.switch_hero(orisa, heroes()[0x02E000000000013E]))
        orisa.mover.position[:] = (x + 10, y, z)
        orisa.mover.body = None
        for _ in range(25):
            run.step({})
        self.cast(run, hero)
        flight, = fight.dragons
        self.assertFalse(fight.rockets)
        # Readiness was seeded to arrange the flight. Use normal damage charge now.
        hero.body_script.ult_seconds = None
        charge = hero.body_script.component.entity_var(combat.ULT_CHARGE)
        pool = fight.vitals_of(next(t for t in fight._targets() if t.owner is orisa))
        self.assertEqual(pool.total, 450)
        orisa.mover.position[:] = (x, y, z)
        orisa.mover.body = None
        run.frame += 1
        match.update(run.frame * combat.TICK, run.frame, frames=False)
        self.assertAlmostEqual(pool.total, 447.6, places=4)

        orisa.take_commands([Command(run.frame, buttons=8)])
        fortify = combat._active_fortify(orisa)
        self.assertIsNotNone(fortify)
        self.assertTrue(orisa.body_script.orisa_fortify_pending)
        before = pool.total
        initial_charge = charge.value()
        run.frame += 15
        match.update(run.frame * combat.TICK, run.frame, frames=False)
        self.assertEqual(fight.dragons, [flight])
        self.assertTrue(fortify.active)
        self.assertFalse(orisa.body_script.orisa_fortify_pending)
        self.assertAlmostEqual(before - pool.total, 14 * 2.4 + 1.44, places=4)
        self.assertAlmostEqual(charge.value() - initial_charge, before - pool.total, places=4)
        before = pool.total
        run.step({})
        self.assertAlmostEqual(before - pool.total, 1.44, places=4)

    def test_fresh_fortify_does_not_reduce_dragon_born_inside_gap(self):
        continuous, skipped = self.dragon_transition_during_gap(fresh=True)
        # Only the endpoint dose sees the fresh Fortify.
        self.assertAlmostEqual(skipped - continuous, 2.4 - 1.44, places=4)

    def test_established_fortify_expires_during_dragon_transition_gap(self):
        continuous, skipped = self.dragon_transition_during_gap(fresh=False)
        self.assertAlmostEqual(skipped, continuous, places=4)

    def dragon_transition_during_gap(self, *, fresh):
        outcomes = []
        for skipped in (False, True):
            with self.subTest(skipped=skipped):
                run, hero, orisa = self.arena()
                match = run.match
                fight = match.combat
                position = tuple(orisa.mover.position)
                self.assertTrue(match.switch_hero(orisa, heroes()[0x02E000000000013E]))
                orisa.mover.position[:] = position
                orisa.mover.body = None
                for _ in range(25):
                    run.step({})
                if not fresh:
                    run.step({orisa: (0, 0, 8)})
                    for _ in range(147):
                        run.step({})
                run.step({hero: (0, 0, 32)})
                for _ in range(100):
                    if fight.rockets:
                        break
                    run.step({})
                arrow, = fight.rockets
                self.assertIsNotNone(arrow.continuation)
                run.frame += 1
                match.update(run.frame * combat.TICK, run.frame, frames=False)
                self.assertIn(arrow, fight.rockets)
                self.assertFalse(fight.dragons)
                self.assertEqual(combat._active_fortify(orisa) is None, fresh)
                if skipped:
                    if fresh:
                        orisa.take_commands([Command(run.frame, buttons=8)])
                        self.assertTrue(orisa.body_script.orisa_fortify_pending)
                    run.frame += 75
                    match.update(run.frame * combat.TICK, run.frame, frames=False)
                else:
                    for _ in range(75):
                        run.step({})
                self.assertFalse(fight.rockets)
                self.assertEqual(len(fight.dragons), 1)
                self.assertEqual(combat._active_fortify(orisa) is not None, fresh and skipped)
                pool = fight.vitals[orisa.body]
                self.assertLess(pool.total, 450)
                outcomes.append(pool.total)
        return outcomes

    def test_same_world_tick_does_not_transform_a_future_subframe_arrow(self):
        run, hero, _ = self.arena()
        fight = run.match.combat
        run.step({hero: (0, 0, 32)})
        for _ in range(100):
            run.step({})
            if fight.rockets:
                break
        arrow, = fight.rockets
        self.assertGreater(arrow.started_tick, run.frame)
        run.match.update(run.frame * combat.TICK, run.frame)
        self.assertIn(arrow, fight.rockets)
        self.assertFalse(fight.dragons)
        self.assertEqual(arrow.flown, 0)
        run.step({})
        self.assertAlmostEqual(arrow.flown, (run.frame - arrow.started_tick) * combat.TICK)

    def test_initial_arrow_pierces_multiple_bodies_without_repeating_damage(self):
        self.verify_initial_dragon_pierce(fortified=False)

    def test_initial_arrow_pierces_fortified_orisa_and_continues_into_moving_dragon(self):
        self.verify_initial_dragon_pierce(fortified=True)

    def verify_initial_dragon_pierce(self, *, fortified):
        run = Run(KANEZAKA_DM, ("Hanzo", "Near", "Far"))
        match = run.match
        match.bots.clear()
        match._pending_bot_scripts.clear()
        hero, near, far = run.players
        match.switch_hero(hero, heroes()[dragon.HANZO])
        if fortified:
            self.assertTrue(match.switch_hero(near, heroes()[0x02E000000000013E]))
        x, y, z = hero.mover.position
        near.mover.position = [x, y, z + 5]
        far.mover.position = [x, y, z + (20 if fortified else 8)]
        for _ in range(20):
            run.step({})
        match.combat.configure_dragonstrike(self.profile)
        hero.body_script.ult_seconds = 0
        hero.body_script.set_ult_charge(1680)
        if fortified:
            fight = match.combat
            run.step({hero: (0, 0, 32), near: (0, 0, 8)})
            self.assertIsNotNone(combat._active_fortify(near))
            for _ in range(130):
                run.step({})
                if fight.dragons:
                    break
            flight, = fight.dragons
            self.assertFalse(fight.rockets)
            self.assertAlmostEqual(flight.origin[2] - z, 10)
            self.assertEqual(flight.source_entity, hero.body)
            self.assertAlmostEqual(fight.vitals[near.body].total, 380, places=4)
            self.assertEqual(len(hero.client.messages(combat.HIT)), 1)
            far_pool = fight.vitals_of(next(t for t in fight._targets() if t.owner is far))
            self.assertEqual(far_pool.total, 200)
            for _ in range(100):
                run.step({})
                if far_pool.total < 200:
                    break
            self.assertAlmostEqual(far_pool.total, 195.2, places=4)
            self.assertAlmostEqual(fight.vitals[near.body].total, 380, places=4)
            return
        self.cast(run, hero, frames=120)
        self.assertEqual(match.combat.vitals[near.body].total, 75)
        self.assertEqual(match.combat.vitals[far.body].total, 75)
        self.assertEqual(len(hero.client.messages(combat.HIT)), 2)
        self.assertEqual(len(match.combat.dragons), 1)

    def test_off_axis_and_allied_players_take_no_damage(self):
        for allied in (False, True):
            with self.subTest(allied=allied):
                run, hero, victim = self.arena()
                if allied:
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    victim.team = hero.team
                self.cast(run, hero, yaw=0 if allied else 16384)
                target = next(t for t in run.match.combat._targets() if t.owner is victim)
                self.assertEqual(run.match.combat.vitals_of(target).total, 200)
                self.assertFalse(hero.client.messages(combat.HIT))

    def test_launched_dragon_survives_caster_body_change_with_original_kill_source(self):
        run, hero, victim = self.arena(distance=40)
        original_body = hero.body
        self.cast(run, hero, frames=100)
        self.assertTrue(run.match.combat.dragons)
        self.assertTrue(run.match.switch_hero(hero, heroes()[SOLDIER]))
        self.assertNotEqual(hero.body, original_body)
        for _ in range(220):
            run.step({})
        vitals = run.match.combat.vitals[victim.body]
        self.assertTrue(vitals.dead)
        self.assertEqual(vitals.killer, original_body)
        notice, = victim.client.messages(combat.DEATH_NOTICE)
        self.assertEqual(notice["+0x7C"]["+0x0"], original_body)

    def test_launched_dragon_survives_caster_death(self):
        run, hero, enemy = self.arena(distance=40)
        self.cast(run, hero, frames=100)
        fight = run.match.combat
        x, y, z = hero.mover.position
        yaw, pitch = aim(fight._eye(enemy), (x, y + 1.55, z))
        for _ in range(220):
            run.step({enemy: (yaw, pitch, 1)})
        self.assertTrue(fight.vitals[hero.body].dead)
        self.assertTrue(fight.vitals[enemy.body].dead)
        self.assertEqual(fight.vitals[enemy.body].killer, hero.body)

    def test_expiry_and_source_leave_retire_flights(self):
        for left in (False, True):
            with self.subTest(left=left):
                run, hero, victim = self.arena(distance=300)
                self.cast(run, hero, frames=100)
                fight = run.match.combat
                flight, = fight.dragons
                if left:
                    hero.gone = True
                    tick = run.frame + 1
                else:
                    tick = run.frame + 1400
                run.match.update(tick * combat.TICK, tick)
                self.assertFalse(fight.dragons)
                if not left:
                    self.assertAlmostEqual(flight.age, 20)
                target = next(t for t in fight._targets() if t.owner is victim)
                self.assertEqual(fight.vitals_of(target).total, 200)

    def test_two_actual_dragonstrikes_each_dose_once_on_the_same_target(self):
        run, hero, victim = self.arena(distance=300)
        fight = run.match.combat
        script = hero.body_script
        script.ult_seconds = None
        charge = script.component.entity_var(342)
        self.cast(run, hero, frames=100)
        first, = fight.dragons
        hero.body_script.set_ult_charge(1680)  # Fix cast spacing for spatial overlap.
        self.cast(run, hero, frames=100)
        original, second = fight.dragons
        self.assertIs(original, first)
        self.assertGreater(first.age, second.age)
        self.assertEqual((first.source_entity, second.source_entity), (hero.body, hero.body))

        x, _, z = second.origin
        victim.mover.position[:] = (x, hero.mover.position[1], z + 2)
        victim.mover.body = None
        target = next(t for t in fight._targets() if t.owner is victim)
        pool = fight.vitals_of(target)
        self.assertEqual(pool.total, 200)
        dose = second.profile.core_rate * combat.TICK
        prior_charge = charge.value()
        passive_ms = script.passive_charge_ms
        frame = script.frame
        with patch.object(fight, "_damage", wraps=fight._damage) as damage:
            run.step({})
            calls = [call for call in damage.call_args_list if call.args[1].owner is victim]
            self.assertEqual(len(calls), 2)
            for call in calls:
                self.assertAlmostEqual(call.args[2], dose)
                self.assertEqual(call.kwargs["source_entity"], hero.body)
        self.assertAlmostEqual(pool.total, 200 - 2 * dose)
        passive = (passive_ms + (script.frame - frame) * script.frame_ms) // 1000 * 5
        self.assertAlmostEqual(charge.value() - prior_charge, passive + 2 * dose, places=4)

        pool.damage(pool.total - 6)
        with patch.object(fight, "_damage", wraps=fight._damage) as damage:
            run.step({})
        hits = [call.args[2] for call in damage.call_args_list if call.args[1].owner is victim]
        self.assertEqual(len(hits), 2)
        for amount in hits:
            self.assertAlmostEqual(amount, dose)
        self.assertTrue(pool.dead)
        self.assertEqual(pool.killer, hero.body)
        self.assertEqual(fight.dragons, [first, second])
        passive = (passive_ms + (script.frame - frame) * script.frame_ms) // 1000 * 5
        self.assertAlmostEqual(charge.value() - prior_charge, passive + 2 * dose + 6, places=4)
        for _ in range(8):
            run.step({})
            self.assertTrue(pool.dead)
        passive = (passive_ms + (script.frame - frame) * script.frame_ms) // 1000 * 5
        self.assertAlmostEqual(charge.value() - prior_charge, passive + 2 * dose + 6, places=4)

        old_victim = victim.body
        remaining = first.profile.lifetime - first.age
        run.frame += math.ceil(remaining / combat.TICK) + 1
        run.match.update(run.frame * combat.TICK, run.frame)
        self.assertAlmostEqual(first.age, first.profile.lifetime)
        self.assertEqual(fight.dragons, [second])
        self.assertLess(second.age, second.profile.lifetime)
        self.assertNotEqual(victim.body, old_victim)

        run.step({})  # Let the command clock catch up before measuring the next dose.
        prior_charge = charge.value()
        passive_ms = script.passive_charge_ms
        frame = script.frame
        x, _, z = second.origin
        victim.mover.position[:] = (x, hero.mover.position[1], z + second.profile.speed * second.age)
        victim.mover.body = None
        target = next(t for t in fight._targets() if t.owner is victim)
        fresh = fight.vitals_of(target)
        self.assertEqual(fresh.total, 200)
        run.step({})
        self.assertAlmostEqual(fresh.total, 200 - dose)
        self.assertEqual(fight.dragons, [second])
        passive = (passive_ms + (script.frame - frame) * script.frame_ms) // 1000 * 5
        self.assertAlmostEqual(charge.value() - prior_charge, passive + dose, places=4)

        victim.mover.position[:] = (x + 12, hero.mover.position[1], z)
        victim.mover.body = None
        for _ in range(math.ceil((second.profile.lifetime - second.age) / combat.TICK) + 2):
            run.step({})
        self.assertFalse(fight.dragons)
        self.assertAlmostEqual(second.age, second.profile.lifetime)
        self.assertAlmostEqual(fresh.total, 200 - dose)
        passive = (passive_ms + (script.frame - frame) * script.frame_ms) // 1000 * 5
        self.assertAlmostEqual(charge.value() - prior_charge, passive + dose, places=4)

    def test_opposing_hanzo_dragons_keep_sources_and_expire_independently(self):
        run, first, second = self.arena(distance=20)
        match = run.match
        match.game_map = replace(match.game_map, free_for_all=False)
        fight = match.combat
        position = tuple(second.mover.position)
        self.assertTrue(match.switch_hero(second, heroes()[dragon.HANZO]))
        first.team, second.team = 0, 1
        second.mover.position[:] = position
        second.mover.body = None
        for _ in range(20):
            run.step({})
        actors = (first, second)
        origins = {actor.body: fight._eye(actor) for actor in actors}
        for actor in actors:
            actor.body_script.ult_seconds = None
            actor.body_script.set_ult_charge(1680)
        first_charge, second_charge = [actor.body_script.component.entity_var(combat.ULT_CHARGE)
                                       for actor in actors]

        def step(a=0, b=0):
            run.step({first: (0, 0, a), second: (-32768, 0, b)})

        step(a=32)
        self.assertTrue(hanzo_control.casting(first.body_script))
        self.assertEqual((first_charge.value(), second_charge.value()), (0, 1680))
        for _ in range(18):
            step()
        step(b=32)
        self.assertTrue(hanzo_control.casting(second.body_script))
        self.assertEqual(second_charge.value(), 0)
        pools = {target.entity: fight.vitals_of(target) for target in fight._targets() if target.owner in actors}
        arrows = {}
        for _ in range(120):
            step()
            for arrow in fight.rockets:
                arrows[arrow.source_entity] = arrow
            if len(fight.dragons) == 2:
                break
        first_flight, second_flight = fight.dragons
        self.assertEqual([flight.source_entity for flight in fight.dragons], [first.body, second.body])
        self.assertEqual([flight.team for flight in fight.dragons], [0, 1])
        self.assertGreater(first_flight.age, second_flight.age)
        self.assertFalse(fight.rockets)
        for flight in fight.dragons:
            arrow = arrows[flight.source_entity]
            self.assertAlmostEqual(math.dist(origins[flight.source_entity], flight.origin), 10)
            self.assertEqual(flight.origin, arrow.position)
            self.assertEqual(arrow.hit_entities, set())

        for _ in range(200):
            step()
            if all(pool.dead for pool in pools.values()):
                break
        for shooter, victim in ((first, second), (second, first)):
            self.assertTrue(pools[victim.body].dead)
            self.assertEqual(pools[victim.body].killer, shooter.body)
        self.assertEqual(fight.dragons, [first_flight, second_flight])

        for flight, remaining in ((first_flight, [second_flight]), (second_flight, [])):
            run.frame += math.ceil((flight.profile.lifetime - flight.age) / combat.TICK) + 1
            match.update(run.frame * combat.TICK, run.frame)
            self.assertAlmostEqual(flight.age, flight.profile.lifetime)
            self.assertEqual(fight.dragons, remaining)

    def test_last_dragon_dose_is_clamped_and_expiry_stops_damage_in_its_path(self):
        run, hero, victim = self.arena(distance=300)
        self.cast(run, hero, frames=100)
        fight = run.match.combat
        flight, = fight.dragons
        remaining = flight.profile.lifetime - flight.age
        run.frame += math.floor(remaining / combat.TICK) - 1
        run.match.update(run.frame * combat.TICK, run.frame)
        self.assertTrue(0 < flight.profile.lifetime - flight.age <= 2 * combat.TICK)
        self.assertTrue(flight in fight.dragons)

        x, y, z = flight.origin
        victim.mover.position[:] = (x, hero.mover.position[1], z + flight.profile.speed * flight.profile.lifetime)
        victim.mover.body = None
        target = next(t for t in fight._targets() if t.owner is victim)
        pool = fight.vitals_of(target)
        self.assertEqual(pool.total, 200)
        for _ in range(3):
            if flight not in fight.dragons:
                break
            before = pool.total
            dose_time = min(combat.TICK, flight.profile.lifetime - flight.age)
            run.step({})
            self.assertAlmostEqual(before - pool.total, flight.profile.core_rate * dose_time)
        self.assertFalse(fight.dragons)
        self.assertAlmostEqual(flight.age, flight.profile.lifetime)
        self.assertLess(pool.total, 200)
        after = pool.total
        for _ in range(10):
            run.step({})
        self.assertEqual(pool.total, after)
        self.assertFalse(fight.rockets)

    def test_actual_cast_phase_timers_are_in_stored_vm_state(self):
        run, hero, _ = self.arena()
        component = hero.body_script.component
        run.step({hero: (0, 0, 32)})
        cast = next(instance for instance in component.instances.values() if instance.graph.index == 0x0101)
        for position, duration in ((29, 1000), (37, 1200)):
            with self.subTest(position=position):
                for _ in range(100):
                    phase = cast.states.get(cast.graph.node(position).state)
                    if phase is not None and phase.active:
                        break
                    run.step({})
                self.assertIsNotNone(phase)
                self.assertTrue(phase.active)
                timer, = [event for event in cast.queue if event.state is phase and event.param == 1]
                self.assertEqual(timer.time - phase.start, duration)
                captured = vm_snapshot_digest(export_component_vm(component))
                with patch.object(timer, "time", timer.time + 1):
                    self.assertNotEqual(vm_snapshot_digest(export_component_vm(component)), captured)
                self.assertEqual(vm_snapshot_digest(export_component_vm(component)), captured)


    def test_active_profile_and_flight_age_are_in_stored_state(self):
        case = snapshot_tests.ServerSnapshotTests()
        session, _, players = case.scene()
        player = players[0]
        fight = player.match.combat
        before = snapshot_digest(case.capture(session))
        fight.configure_dragonstrike(self.profile)
        configured = snapshot_digest(case.capture(session))
        self.assertNotEqual(before, configured)
        flight = dragon.Flight(player, player.body, player.team, fight._eye(player), (0, 0, 1),
                               self.profile, player.match.tick)
        fight.dragons.append(flight)
        active = snapshot_digest(case.capture(session))
        self.assertNotEqual(configured, active)
        flight.age = 0.25
        self.assertNotEqual(active, snapshot_digest(case.capture(session)))


class DragonCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.profile = prepared_profile()
        npc_tests.HanzoCatalogNpcTests.setUpClass()
        cls.catalog = npc_tests.HanzoCatalogNpcTests.catalog

    @classmethod
    def tearDownClass(cls):
        npc_tests.HanzoCatalogNpcTests.tearDownClass()

    def setUp(self):
        for module in (bodytrace, movelog):
            self.enterContext(patch.object(module, "ENABLED", False))

    def arena(self, definition, situation="hit", lateral=0, distance=20):
        run, hero, bot = npc_tests.HanzoCatalogNpcTests().arena(
            definition, "bow", situation, distance=distance, lateral=lateral,
        )
        run.match.combat.configure_dragonstrike(self.profile)
        hero.body_script.ult_seconds = 0
        hero.body_script.set_ult_charge(1680)
        return run, hero, bot

    def test_initial_arrow_direct_hit_and_admission_for_all_five_talon_bodies(self):
        cases = (("hit", 5, 0, 125), ("miss", 5, 16384, 0),
                 ("ally", 5, 0, 0), ("range", 12, 0, 0), ("wall", 5, 0, 0))
        for definition in self.catalog.values():
            for situation, distance, yaw, dealt in cases:
                with self.subTest(npc=definition.name, situation=situation):
                    run, hero, bot = self.arena(definition, situation, distance=distance)
                    fight = run.match.combat
                    target = next(t for t in fight._targets() if t.owner is bot)
                    vitals = fight.vitals_of(target)
                    before = vitals.total
                    run.step({hero: (yaw, 0, 32)})
                    for _ in range(100):
                        if fight.rockets:
                            break
                        run.step({hero: (yaw, 0, 0)})
                    initial, = fight.rockets
                    self.assertIs(initial.continuation, self.profile)
                    self.assertEqual(initial.direct.amount, 125)
                    self.assertAlmostEqual(initial.range_left, 10)
                    boundary = 2.5 if situation == "wall" else 10
                    checkpoint = boundary - 1
                    for _ in range(40):
                        if initial.distance_flown >= checkpoint:
                            break
                        run.step({hero: (yaw, 0, 0)})
                    self.assertGreaterEqual(initial.distance_flown, checkpoint)
                    self.assertIn(initial, fight.rockets)
                    self.assertFalse(fight.dragons)
                    self.assertEqual(vitals.total, before - dealt)
                    self.assertEqual(bot.entity in initial.hit_entities, bool(dealt))
                    hits = hero.client.messages(combat.HIT)
                    self.assertEqual([hit["+0x78"]["+0x4"] for hit in hits], [125] if dealt else [])

                    for _ in range(8):
                        if initial not in fight.rockets:
                            break
                        run.step({hero: (yaw, 0, 0)})
                    self.assertNotIn(initial, fight.rockets)
                    flight, = fight.dragons
                    self.assertAlmostEqual(initial.distance_flown, boundary, places=5)
                    self.assertEqual(flight.origin, initial.position)
                    self.assertEqual(bot.entity in initial.hit_entities, bool(dealt))


    def test_projected_zarya_barrier_transforms_initial_dragon_before_each_talon_body(self):
        from ow174.game import zarya_barrier

        for definition in self.catalog.values():
            for protected in (False, True):
                with self.subTest(npc=definition.name, protected=protected):
                    run, hero, bot = self.arena(definition, distance=5)
                    match = run.match
                    self.addCleanup(npc.remove_spawned, match)
                    match.game_map = replace(match.game_map, free_for_all=False)
                    fight = match.combat
                    target = next(t for t in fight._targets() if t.owner is bot)
                    pool = fight.vitals_of(target)
                    initial_health = pool.total
                    team = bot.team if protected else hero.team
                    caster = match.add_player(3, "Zarya", zarya_barrier.HERO, team, False)
                    in_world(match, caster)
                    caster.client = npc_tests.NpcFixtureClient(caster)
                    run.players.append(caster)
                    self.assertTrue(match.switch_hero(caster, heroes()[zarya_barrier.HERO]))
                    caster.team = team
                    x, y, z = target.feet
                    caster.mover.position[:] = (x + 4, y, z)
                    caster.mover.body = None
                    for _ in range(30):
                        run.step({})
                    angles = aim(fight._eye(hero), (x, y + target.height / 2, z))
                    cast = aim(fight._eye(caster), (x, y + target.height / 2, z))
                    run.step({hero: (*angles, 32)})
                    for _ in range(100):
                        if fight.rockets:
                            break
                        run.step({})
                    initial, = fight.rockets
                    self.assertEqual(initial.direct.amount, 125)
                    self.assertEqual(initial.source_entity, hero.body)
                    self.assertEqual(hero.body_script.component.entity_var(342).value(), 0)

                    run.step({caster: (*cast, 16)})
                    self.assertIn(initial, fight.rockets)
                    barriers = fight.zarya_barriers
                    contact = barriers.trace(hero, initial.position, initial.direction, initial.range_left)
                    if protected:
                        self.assertEqual(barriers.projected[caster.body][1:], (200, bot.entity))
                        self.assertIsNotNone(contact)
                        distance, owner = contact
                        self.assertIs(owner, bot)
                        endpoint = initial.distance_flown + distance
                    else:
                        self.assertNotIn(caster.body, barriers.projected)
                        self.assertIsNone(contact)
                        endpoint = 10
                    self.assertEqual(pool.total, initial_health)

                    for _ in range(40):
                        if initial not in fight.rockets:
                            break
                        run.step({})
                    self.assertNotIn(initial, fight.rockets)
                    flight, = fight.dragons
                    self.assertAlmostEqual(initial.distance_flown, endpoint, places=5)
                    self.assertEqual(flight.origin, initial.position)
                    self.assertEqual(flight.source_entity, hero.body)
                    self.assertEqual(bot.entity in initial.hit_entities, not protected)
                    if protected:
                        self.assertEqual(barriers.projected[caster.body][1:], (75, bot.entity))
                        self.assertEqual(caster.body_script.component.entity_var(87).value(), 25)
                        before = pool.total
                        with patch.object(fight, "_damage", wraps=fight._damage) as damage:
                            run.step({})
                        dose, = [call for call in damage.call_args_list if call.args[1].owner is bot]
                        self.assertAlmostEqual(dose.args[2], self.profile.core_rate * dragon.STEP)
                        self.assertEqual(dose.kwargs["source_entity"], hero.body)
                        self.assertLess(pool.total, before)
                        self.assertGreater(pool.total, initial_health - 125)
                        self.assertEqual(barriers.projected[caster.body][1:], (75, bot.entity))
                        self.assertEqual(caster.body_script.component.entity_var(87).value(), 25)
                    else:
                        self.assertEqual(pool.total, initial_health - 125)
                        run.step({})
                        self.assertEqual(pool.total, initial_health - 125)

    def test_talon_capsule_entry_straddles_initial_dragon_transformation(self):
        for definition in self.catalog.values():
            for offset in (-0.05, 0.05):
                with self.subTest(npc=definition.name, offset=offset):
                    run, hero, bot = self.arena(definition, lateral=12)
                    self.addCleanup(npc.remove_spawned, run.match)
                    fight = run.match.combat
                    with patch.object(fight.projectiles, "fired", wraps=fight.projectiles.fired) as fired:
                        run.step({hero: (0, 0, 32)})
                        for _ in range(100):
                            if fight.rockets:
                                break
                            run.step({})
                    initial, = fight.rockets
                    fired.assert_called_once()
                    self.assertEqual(fired.call_args.args[3], dragon.INITIAL)
                    self.assertIs(initial.continuation, self.profile)
                    self.assertEqual(initial.range_left, 10)
                    x, y, z = initial.position
                    target = next(t for t in fight._targets() if t.owner is bot)
                    bot.position = (x, y - target.height / 2, z + 10 + target.radius + offset)
                    pool = fight.vitals_of(target)
                    before = pool.total
                    calls = []
                    damage = fight._damage

                    def observe(shooter, struck, amount, critical, *args, **kwargs):
                        if struck.owner is bot:
                            calls.append((initial in fight.rockets, amount, kwargs["source_entity"]))
                        return damage(shooter, struck, amount, critical, *args, **kwargs)

                    with patch.object(fight, "_damage", observe):
                        for _ in range(65):
                            run.step({})
                            if fight.dragons:
                                break
                        flight, = fight.dragons
                        direct_loss = 125 if offset < 0 else 0
                        self.assertEqual(pool.total, before - direct_loss)
                        self.assertAlmostEqual(self.profile.forward_gate, 1)
                        for projected in (0.95, 1.0):
                            bot.position = (x, y - target.height / 2, flight.origin[2] + projected)
                            run.step({})
                            self.assertEqual(pool.total, before - direct_loss)
                        bot.position = (x, y - target.height / 2, flight.origin[2] + 1.05)
                        run.step({})
                    self.assertFalse(fight.rockets)
                    self.assertAlmostEqual(initial.distance_flown, 10)
                    self.assertEqual(flight.origin, initial.position)
                    self.assertEqual(flight.source_entity, hero.body)
                    self.assertEqual(bot.entity in initial.hit_entities, offset < 0)
                    self.assertEqual([amount for direct, amount, _ in calls if direct],
                                     [125] if offset < 0 else [])
                    doses = [amount for direct, amount, _ in calls if not direct]
                    self.assertTrue(doses)
                    self.assertTrue(all(0 < amount <= self.profile.core_rate * combat.TICK for amount in doses))
                    self.assertTrue(all(source == hero.body for _, _, source in calls))
                    self.assertAlmostEqual(before - pool.total, sum(amount for _, amount, _ in calls))

    def test_final_dragon_slice_straddles_all_five_talon_capsules(self):
        for definition in self.catalog.values():
            for offset in (-0.05, 0.05):
                with self.subTest(npc=definition.name, offset=offset):
                    run, hero, bot = self.arena(definition, lateral=20)
                    self.addCleanup(npc.remove_spawned, run.match)
                    match = run.match
                    fight = match.combat
                    DragonPlayerTests.cast(run, hero, frames=100)
                    flight, = fight.dragons
                    profile = flight.profile
                    remaining = profile.lifetime - flight.age
                    run.frame += math.ceil(remaining / combat.TICK - 1e-7) - 1
                    match.update(run.frame * combat.TICK, run.frame)
                    remaining = profile.lifetime - flight.age
                    self.assertGreater(remaining, 0)
                    self.assertLessEqual(remaining, combat.TICK + 1e-9)
                    self.assertIn(flight, fight.dragons)
                    target = next(t for t in fight._targets() if t.owner is bot)
                    pool = fight.vitals_of(target)
                    before = pool.total
                    x, y, z = flight.origin
                    sample = (flight.age + remaining / 2) * profile.speed
                    bot.position = (x, y - target.height / 2, z + sample + profile.radius + target.radius + offset)
                    run.step({})
                    dealt = profile.core_rate * remaining if offset < 0 else 0
                    self.assertAlmostEqual(before - pool.total, dealt)
                    self.assertFalse(fight.dragons)
                    self.assertAlmostEqual(flight.age, profile.lifetime)
                    for _ in range(5):
                        run.step({})
                    self.assertAlmostEqual(before - pool.total, dealt)

    def test_all_five_bodies_take_actual_continuing_damage_and_keep_corpses(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, hero, bot = self.arena(definition)
                DragonPlayerTests.cast(run, hero, frames=340)
                fight = run.match.combat
                vitals = fight.vitals[bot.entity]
                self.assertLess(vitals.total, definition.health + definition.shields - 125)
                self.assertEqual(bot.dead, definition.name != "Heavy Assault")
                if bot.dead:
                    self.assertIsNotNone(bot.body_script)
                    self.assertTrue(bot.body_script.component.owner_dead)
                    self.assertEqual([entity for entity, _ in hero.client.npc_deaths], [bot.entity])
                self.assertFalse(any(who is bot for _, who in fight.respawns))
                tick = run.frame + 1000
                run.match.update(tick * combat.TICK, tick)
                self.assertIn(bot, run.match.bots)

    def test_all_five_talon_lethal_attacks_cancel_unreleased_dragonstrike(self):
        def armed_scene(definition):
            run, hero, bot = self.arena(definition, distance=2.5)
            self.addCleanup(npc.remove_spawned, run.match)
            fight = run.match.combat
            pool = fight.vitals_of(next(t for t in fight._targets() if t.owner is hero))
            pool.damage(pool.total - 1)
            yaw, pitch = npc_tests.HanzoCatalogNpcTests.target_aim(run, hero, bot)
            run.step({hero: (yaw, pitch, 0)})
            return run, hero, bot, pool

        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                probe, probe_hero, probe_bot, probe_pool = armed_scene(definition)
                self.assertEqual(npc_control.start(probe.match, probe_hero), probe_bot.entity)
                for delay in range(1, 201):
                    probe.step({})
                    if probe_pool.dead:
                        break
                self.assertTrue(probe_pool.dead)
                self.assertEqual(probe_pool.killer, probe_bot.entity)

                run, hero, bot, pool = armed_scene(definition)
                fight = run.match.combat
                old = hero.body_script
                charge = old.component.entity_var(combat.ULT_CHARGE)
                yaw, pitch = npc_tests.HanzoCatalogNpcTests.target_aim(run, hero, bot)
                with patch.object(fight.projectiles, "fired", wraps=fight.projectiles.fired) as fired:
                    if delay > 1:
                        self.assertEqual(npc_control.start(run.match, hero), bot.entity)
                        for _ in range(delay - 2):
                            run.step({})
                    run.step({hero: (yaw, pitch, 32)})
                    self.assertFalse(pool.dead)
                    self.assertTrue(hanzo_control.casting(old))
                    self.assertEqual(charge.value(), 0)
                    if delay == 1:
                        self.assertEqual(npc_control.start(run.match, hero), bot.entity)
                    run.step({hero: (0, 0, 32)})
                    self.assertTrue(pool.dead)
                    self.assertEqual(pool.killer, bot.entity)
                    self.assertEqual(charge.value(), 0)
                    self.assertEqual(npc_control.stop(run.match), 1)
                    limit = round(combat.respawn_seconds(run.match) / combat.TICK) + 120
                    for _ in range(limit):
                        run.step({hero: (0, 0, 32)})
                        if hero.body_script is not old:
                            break
                    self.assertTrue(old.retired)
                    self.assertFalse(fight.is_dead(hero))
                    fresh = hero.body_script
                    for _ in range(100):
                        run.step({hero: (0, 0, 32)})
                    self.assertFalse(hanzo_control.casting(fresh))
                    self.assertFalse(any(call.args[0] is hero for call in fired.call_args_list))
                    self.assertFalse(fight.dragons)

    def test_all_five_talon_kills_during_initial_dragon_flight_preserve_transformation(self):
        def scene(definition):
            run, hero, bot = self.arena(definition, distance=0, lateral=2.5)
            self.addCleanup(npc.remove_spawned, run.match)
            fight = run.match.combat
            pool = fight.vitals_of(next(t for t in fight._targets() if t.owner is hero))
            pool.damage(pool.total - 1)
            return run, hero, bot, pool

        probe, hero, _, _ = scene(self.catalog["1"])
        probe.step({hero: (0, 0, 32)})
        for windup in range(100):
            if probe.match.combat.rockets:
                break
            probe.step({})
        self.assertTrue(probe.match.combat.rockets)

        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                probe, hero, bot, pool = scene(definition)
                yaw, pitch = npc_tests.HanzoCatalogNpcTests.target_aim(probe, hero, bot)
                probe.step({hero: (yaw, pitch, 0)})
                self.assertEqual(npc_control.start(probe.match, hero), bot.entity)
                for delay in range(1, 201):
                    probe.step({})
                    if pool.dead:
                        break
                self.assertTrue(pool.dead)

                run, hero, bot, pool = scene(definition)
                fight = run.match.combat
                old_body = hero.body
                cast = max(1, delay - windup)
                release = cast + windup
                # Keep the manual acquisition aim off the frame that launches the arrow.
                death = release + (4 if delay == 3 else 3)
                start = death - delay
                yaw, pitch = npc_tests.HanzoCatalogNpcTests.target_aim(run, hero, bot)
                initial = None
                for frame in range(1, death + 1):
                    angles = (yaw, pitch) if frame == start else (0, 0)
                    run.step({hero: (*angles, 32 if frame == cast else 0)})
                    if frame == start:
                        self.assertEqual(npc_control.start(run.match, hero), bot.entity)
                    if frame == release:
                        initial, = [r for r in fight.rockets if r.shooter is hero]
                        self.assertIs(initial.continuation, self.profile)
                        self.assertFalse(pool.dead)
                self.assertTrue(pool.dead)
                self.assertEqual(pool.killer, bot.entity)
                self.assertIn(initial, fight.rockets)
                self.assertFalse(fight.dragons)
                for _ in range(40):
                    run.step({})
                    if fight.dragons:
                        break
                flight, = fight.dragons
                self.assertNotIn(initial, fight.rockets)
                self.assertAlmostEqual(initial.distance_flown, 10)
                self.assertEqual(flight.origin, initial.position)
                self.assertEqual(flight.source_entity, old_body)

    def test_all_five_talon_counterkills_leave_launched_dragon_and_original_credit(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, hero, bot = self.arena(definition, lateral=8)
                self.addCleanup(npc.remove_spawned, run.match)
                fight = run.match.combat
                old_body = hero.body
                pools = {t.entity: fight.vitals_of(t) for t in fight._targets()}
                hero_pool, bot_pool = pools[old_body], pools[bot.entity]
                hero_pool.damage(hero_pool.total - 5)
                DragonPlayerTests.cast(run, hero, frames=100)
                flight, = fight.dragons
                self.assertEqual(flight.source_entity, old_body)
                self.assertFalse(fight.rockets)
                self.assertEqual(hero_pool.total, 5)
                self.assertEqual(bot_pool.total, definition.health + definition.armour + definition.shields)

                x, y, z = hero.mover.position
                bot.position = (x + 2.5, y, z)
                yaw, pitch = npc_tests.HanzoCatalogNpcTests.target_aim(run, hero, bot)
                run.step({hero: (yaw, pitch, 0)})
                self.assertEqual(npc_control.start(run.match, hero), bot.entity)
                for _ in range(200):
                    run.step({})
                    if hero_pool.dead:
                        break
                self.assertTrue(hero_pool.dead)
                self.assertEqual(hero_pool.killer, bot.entity)
                self.assertIn(flight, fight.dragons)
                self.assertEqual(flight.source_entity, old_body)
                self.assertFalse(bot_pool.dead)

                bot_pool.damage(bot_pool.total - 15)
                advance = flight.profile.speed * flight.age + 1
                bot.position = (flight.origin[0], y, flight.origin[2] + advance)
                for _ in range(40):
                    run.step({})
                    if bot_pool.dead:
                        break
                self.assertTrue(bot_pool.dead)
                self.assertEqual(bot_pool.killer, old_body)
                self.assertTrue(bot.body_script.component.owner_dead)
                self.assertIn(flight, fight.dragons)

    def test_allied_npcs_and_off_axis_attacks_are_excluded(self):
        for situation in ("ally", "miss"):
            for definition in self.catalog.values():
                with self.subTest(npc=definition.name, situation=situation):
                    run, hero, bot = self.arena(definition, situation)
                    DragonPlayerTests.cast(run, hero, yaw=16384 if situation == "miss" else 0)
                    self.assertEqual(run.match.combat.vitals[bot.entity].total,
                                     definition.health + definition.shields)

    def test_wall_transforms_arrow_and_dragons_damage_the_npc_beyond_it(self):
        run, hero, bot = self.arena(self.catalog["5"], "wall")
        origin = run.match.combat._eye(hero)
        DragonPlayerTests.cast(run, hero)
        fight = run.match.combat
        self.assertLess(fight.vitals[bot.entity].total, 4000)
        flight, = fight.dragons
        self.assertAlmostEqual(flight.origin[2] - origin[2], 2.5)

    def test_overlapping_sections_deduplicate_and_outer_band_uses_half_rate(self):
        for lateral, dose in ((0, 4.8), (3, 2.4)):
            with self.subTest(lateral=lateral):
                run, hero, bot = self.arena(self.catalog["5"], lateral=lateral)
                DragonPlayerTests.cast(run, hero, frames=220)
                fight = run.match.combat
                flight, = fight.dragons
                self.assertGreater(flight.age, 2)
                health = fight.vitals[bot.entity].total
                run.step({})
                self.assertAlmostEqual(health - fight.vitals[bot.entity].total, dose)

    def test_enforcer_shield_recovery_orders_moving_dragon_doses_across_gap(self):
        outcomes = []
        for skipped in (False, True):
            with self.subTest(skipped=skipped):
                run, hero, bot = self.arena(self.catalog["2"], distance=5)
                self.addCleanup(npc.remove_spawned, run.match)
                fight = run.match.combat
                pool = fight.vitals[bot.entity]
                component = bot.body_script.component
                recovery = next(i for i in component.instances.values() if i.graph.index == 0x0E62)
                wait = recovery.node_state(recovery.graph.node(52))
                angles = npc_tests.HanzoCatalogNpcTests.target_aim(run, hero, bot)
                for _ in range(70):
                    run.step({hero: (*angles, 1)})
                run.step({hero: (*angles, 0)})
                for _ in range(15):
                    run.step({})
                    if wait.active:
                        break
                self.assertTrue(wait.active)
                self.assertEqual(pool.total, 225)
                old_deadline = wait.start + wait.ms
                x, y, z = hero.mover.position
                bot.position = (x, y, z + 25)
                for _ in range(48):
                    run.step({})
                run.step({hero: (0, 0, 32)})
                for _ in range(110):
                    run.step({})
                    if fight.dragons:
                        break
                flight, = fight.dragons
                self.assertEqual(pool.total, 225)
                self.assertLess(component.now, old_deadline)
                end = run.frame + 85
                if skipped:
                    run.frame = end
                    run.match.update(end * combat.TICK, end)
                else:
                    while run.frame < end:
                        run.step({})
                self.assertIn(flight, fight.dragons)
                self.assertTrue(wait.active)
                self.assertLess(pool.total, 225)
                self.assertGreater(wait.start, old_deadline)
                outcomes.append((round(pool.total, 5), round(flight.age, 5), wait.start))
        self.assertEqual(len(outcomes), 2)
        self.assertEqual(outcomes[0], outcomes[1])

    def test_moving_dragon_applies_armour_half_floor_then_unarmoured_dose(self):
        run, hero, bot = self.arena(self.catalog["5"], distance=20)
        self.addCleanup(npc.remove_spawned, run.match)
        fight = run.match.combat
        health = self.catalog["5"].health
        pool = fight.vitals[bot.entity] = combat.Vitals(bot.entity, health, 1, 1)
        fight._sync_body_health(bot, pool)
        run.step({hero: (0, 0, 32)})
        for _ in range(190):
            run.step({})
            if pool.total < health + 2:
                break
        flight, = fight.dragons
        self.assertFalse(fight.rockets)
        self.assertEqual(pool.now[combat.SHIELDS_POOL], 0)
        self.assertEqual(pool.now[combat.ARMOUR_POOL], 0)
        dose = flight.profile.core_rate * combat.TICK
        self.assertAlmostEqual(dose, 4.8, places=4)
        self.assertAlmostEqual(pool.now[combat.HEALTH_POOL], health - ((dose - 1) / 2 - 1), places=4)
        before = pool.total
        run.step({})
        self.assertAlmostEqual(before - pool.total, dose, places=4)

    def test_skipped_ticks_match_contiguous_damage_and_do_not_extend_lifetime(self):
        outcomes = []
        for skipped in (False, True):
            run, hero, bot = self.arena(self.catalog["5"])
            DragonPlayerTests.cast(run, hero, frames=100)
            fight = run.match.combat
            flight, = fight.dragons
            if skipped:
                tick = run.frame + 1400
                run.match.update(tick * combat.TICK, tick)
            else:
                for _ in range(1400):
                    run.step({})
            self.assertFalse(fight.dragons)
            self.assertAlmostEqual(flight.age, 20)
            outcomes.append(fight.vitals[bot.entity].total)
        self.assertAlmostEqual(*outcomes)
