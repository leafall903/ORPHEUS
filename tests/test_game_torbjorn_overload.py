"""Actual Overload pool lifecycle and cooldown under command-driven graph execution."""

import unittest

import test_game_torbjorn as torbjorn
from test_game_combat import aim

from ow174.game import combat, hero_torbjorn, world
from ow174.game.commands import Command
from ow174.game.content import SOLDIER, heroes
from ow174.game.script import expr


class TorbjornOverloadTests(unittest.TestCase):
    def arena(self):
        run, owner, enemy = torbjorn.TorbjornProjectileTests().arena()
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is owner)
        vitals = fight.vitals_of(target)
        run.step({owner: (0, 0, 16)})
        self.assertEqual(vitals.total, 350.0)
        return run, owner, enemy, target, vitals

    def test_overload_consumes_temporary_armour_before_permanent_armour(self):
        run, owner, enemy, target, vitals = self.arena()
        run.match.combat._damage(enemy, target, 120.0, False, None, run.frame)
        self.assertEqual(vitals.over_armour.amount, 0.0)
        self.assertEqual(vitals.now[world.ARMOUR_POOL], 35.0)
        self.assertEqual(vitals.now[world.HEALTH_POOL], 200.0)
        self.assertEqual(vitals.total, 235.0)  # one armour reduction across both armour pools
        for _ in range(20):
            run.step({owner: (0, 0, 16)})
        self.assertEqual(vitals.total, 235.0)
        self.assertEqual(vitals.heal(1000.0), 15.0)
        self.assertEqual(vitals.over_armour.amount, 0.0)

    def test_overload_expiry_discards_remainder_and_clears_replicated_slot(self):
        run, owner, enemy, target, vitals = self.arena()
        run.match.combat._damage(enemy, target, 70.0, False, None, run.frame)
        self.assertEqual(vitals.total, 285.0)
        part = vitals.component(vitals.ids)[51][world.ARMOUR_POOL * world.POOLS + 1]
        self.assertEqual(part[18], 1)
        for _ in range(320):
            run.step({})
        self.assertEqual(vitals.total, 250.0)
        self.assertIsNone(vitals.over_armour.source)
        part = vitals.component(vitals.ids)[51][world.ARMOUR_POOL * world.POOLS + 1]
        self.assertEqual(part, bytes(20))
        self.assertEqual(owner.body_script.component.vars[1732].value(), 250.0)

    def test_repeated_presses_cannot_refresh_or_bypass_cooldown(self):
        run, owner, enemy, target, vitals = self.arena()
        first = vitals.over_armour.source
        run.match.combat._damage(enemy, target, 70.0, False, None, run.frame)
        for index in range(300):
            run.step({owner: (0, 0, 16 if index % 2 else 0)})
        self.assertEqual(vitals.over_armour.source, first)
        self.assertEqual(vitals.over_armour.amount, 35.0)
        for index in range(600):
            run.step({owner: (0, 0, 16 if index % 2 else 0)})
            if index > 12:
                self.assertIsNone(vitals.over_armour.source)
        self.assertEqual(vitals.total, 250.0)
        for index in range(90):
            run.step({owner: (0, 0, 16 if index % 2 else 0)})
        self.assertEqual(vitals.over_armour.source, (first[0], first[1] + 1))
        self.assertEqual(vitals.total, 350.0)

    def cooling_arena(self):
        run, owner, _, _, vitals = self.arena()
        for _ in range(320):
            run.step({})
        instance = next(i for i in owner.body_script.component.instances.values()
                        if i.graph.index == 0x1721)
        ability = instance.state(19)
        self.assertGreater(ability.remaining(), 9)
        self.assertIsNone(vitals.over_armour.source)
        return run, owner, vitals, ability

    def test_early_short_press_expires_without_automatic_overload_then_fresh_press_works(self):
        run, owner, vitals, ability = self.cooling_arena()
        run.step({owner: (0, 0, 16)})
        original = tuple(ability.presses)
        self.assertTrue(original)
        self.assertIsNone(vitals.over_armour.source)
        for _ in range(630):
            run.step({})
            self.assertIsNone(vitals.over_armour.source)
            # Waiting never rewrites the time of a rejected press.
            self.assertEqual(tuple(ability.presses), original)
        self.assertEqual(ability.remaining(), 0)
        run.step({owner: (0, 0, 16)})
        self.assertIsNotNone(vitals.over_armour.source)
        self.assertEqual(vitals.total, 350)

    def test_near_expiry_short_press_starts_within_authored_buffer(self):
        run, owner, vitals, ability = self.cooling_arena()
        while ability.remaining() > 0.08:
            run.step({})
        run.step({owner: (0, 0, 16)})
        self.assertIsNone(vitals.over_armour.source)
        for _ in range(8):
            run.step({})
        self.assertIsNotNone(vitals.over_armour.source)
        self.assertTrue(ability.flags & ability.RUNNING)
        self.assertEqual(vitals.total, 350)

    def test_body_replacement_preserves_running_overload_cooldown(self):
        for replacement in ("reselect", "skin", "round_trip"):
            with self.subTest(replacement=replacement):
                run, owner, vitals, ability = self.cooling_arena()
                match = run.match
                old_body = owner.body
                self.assertGreater(ability.remaining(), 9)
                run.step({owner: (0, 0, 16)})
                self.assertEqual(vitals.total, 250)
                run.step({})
                deadline = match.combat.now + ability.remaining()
                if replacement == "skin":
                    match.skin_of = lambda *_: (0, True)
                    self.assertTrue(match.skin_changed(owner, owner.hero.guid))
                else:
                    if replacement == "round_trip":
                        self.assertTrue(match.switch_hero(owner, heroes()[SOLDIER]))
                        for _ in range(100):
                            run.step({})
                        self.assertTrue(match.switch_hero(owner, heroes()[hero_torbjorn.HERO]))
                    else:
                        self.assertTrue(match.switch_hero(owner, owner.hero))
                self.assertNotEqual(owner.body, old_body)
                run.step({})
                fresh = match.combat.vitals[owner.body]
                self.assertEqual(fresh.total, 250)
                self.assertAlmostEqual(
                    hero_torbjorn.overload_cooldown_remaining(owner), deadline - match.combat.now, delta=.03)
                run.step({owner: (0, 0, 16)})
                self.assertEqual(fresh.total, 250)
                while match.combat.now < deadline + .05:
                    run.step({})
                self.assertEqual(fresh.total, 250)
                run.step({owner: (0, 0, 16)})
                self.assertEqual(fresh.total, 350)

    def test_overload_replacement_preserves_wall_time_at_modified_cooldown_rates(self):
        for rate, active in ((2.0, True), (.5, False)):
            with self.subTest(rate=rate, active=active):
                run, owner, _, _, _ = self.arena()
                match = run.match
                instance = next(i for i in owner.body_script.component.instances.values()
                                if i.graph.index == 0x1721)
                instance.write((expr.ENTITY, 3652), rate - 1)
                deadline = match.combat.now + 5 + 10 / rate
                for _ in range(40 if active else 320):
                    run.step({})
                self.assertEqual(instance.state(1).active, active)
                self.assertTrue(match.switch_hero(owner, heroes()[SOLDIER]))
                for _ in range(100):
                    run.step({})
                self.assertTrue(match.switch_hero(owner, heroes()[hero_torbjorn.HERO]))
                self.assertAlmostEqual(
                    hero_torbjorn.overload_cooldown_remaining(owner), deadline - match.combat.now, delta=.03)
                run.step({owner: (0, 0, 16)})
                fresh = match.combat.vitals[owner.body]
                self.assertEqual(fresh.total, 250)
                while match.combat.now < deadline + .05:
                    run.step({})
                self.assertEqual(fresh.total, 250)
                run.step({owner: (0, 0, 16)})
                self.assertEqual(fresh.total, 350)

    def test_reselect_resumes_paused_overload_cooldown_without_resetting_it(self):
        run, owner, _, _, vitals = self.arena()
        match = run.match
        instance = next(i for i in owner.body_script.component.instances.values() if i.graph.index == 0x1721)
        instance.write((expr.ENTITY, 3652), -1)
        for _ in range(400):
            run.step({})
        ability = instance.state(19)
        self.assertEqual(vitals.total, 250)
        self.assertEqual(ability.cooldown_rate, 0)
        self.assertEqual(ability.remaining(), 10)
        run.step({owner: (0, 0, 16)})
        self.assertEqual(vitals.total, 250)
        self.assertTrue(match.switch_hero(owner, owner.hero))
        self.assertAlmostEqual(hero_torbjorn.overload_cooldown_remaining(owner), 10)
        deadline = match.combat.now + 10
        run.step({owner: (0, 0, 16)})
        fresh = match.combat.vitals[owner.body]
        self.assertEqual(fresh.total, 250)
        while match.combat.now < deadline + .05:
            run.step({})
        self.assertEqual(fresh.total, 250)
        run.step({owner: (0, 0, 16)})
        self.assertEqual(fresh.total, 350)

    def test_early_press_held_until_ready_starts_at_cooldown_end(self):
        run, owner, vitals, ability = self.cooling_arena()
        for _ in range(500):
            run.step({owner: (0, 0, 16)})
            self.assertIsNone(vitals.over_armour.source)
        for _ in range(130):
            run.step({owner: (0, 0, 16)})
        self.assertIsNotNone(vitals.over_armour.source)
        self.assertTrue(ability.flags & ability.RUNNING)
        self.assertEqual(vitals.total, 350)

    def test_death_clears_pool_and_dead_owner_cannot_reactivate(self):
        run, owner, enemy, target, vitals = self.arena()
        run.match.combat._damage(enemy, target, 1000.0, False, None, run.frame)
        self.assertTrue(vitals.dead)
        self.assertIsNone(vitals.over_armour.source)
        self.assertEqual(vitals.total, 0.0)
        for _ in range(40):
            run.step({owner: (0, 0, 16)})
        self.assertEqual(vitals.total, 0.0)
        self.assertTrue(owner.body_script.component.owner_dead)

    def test_soldier_fire_depletes_overload_and_respawn_starts_with_fresh_pools(self):
        run, owner, enemy, target, old_hp = self.arena()
        fight = run.match.combat
        body = owner.body
        x, y, z = target.feet
        direction = aim(fight._eye(enemy), (x, y + .6, z))
        for _ in range(30):
            run.step({enemy: (*direction, 1)})
            if old_hp.total < 350:
                break
        self.assertEqual(old_hp.over_armour.amount, 85)
        self.assertEqual(old_hp.now[world.ARMOUR_POOL], 50)
        for _ in range(250):
            run.step({enemy: (*direction, 1)})
            if old_hp.dead:
                break
        self.assertTrue(old_hp.dead)
        self.assertEqual(old_hp.killer, enemy.body)
        self.assertIsNone(old_hp.over_armour.source)
        self.assertEqual(old_hp.total, 0)
        for _ in range(40):
            run.step({owner: (0, 0, 16)})
            self.assertEqual(old_hp.total, 0)
        for _ in range(750):
            if owner.body != body:
                break
            run.step({})
        self.assertNotEqual(owner.body, body)
        self.assertFalse(fight.is_dead(owner))
        fresh = fight.vitals_of(next(target for target in fight._targets() if target.owner is owner))
        self.assertIsNot(fresh, old_hp)
        self.assertEqual(fresh.total, 250)
        self.assertIsNone(fresh.over_armour.source)
        for _ in range(30):
            run.step({})
        run.step({owner: (0, 0, 16)})
        self.assertEqual(fresh.total, 350)
        self.assertEqual(fresh.over_armour.amount, 100)
        self.assertIsNotNone(fresh.over_armour.source)

    def test_same_tick_hero_switch_clears_overload_speed_and_armor(self):
        run, owner, enemy, _, old_hp = self.arena()
        match = run.match
        fight = match.combat
        old_body = owner.body
        torbjorn_hero = owner.hero
        deadline = fight.now + 15

        def run_forward():
            for _ in range(40):
                run.frame += 1
                match.update(run.frame * combat.TICK, run.frame)
                owner.take_commands([Command(run.frame, yaw=16384, forward=127)])
                enemy.take_commands([Command(run.frame)])
            return owner.mover.velocity[0]

        self.assertAlmostEqual(run_forward(), 7.15, delta=.002)
        self.assertEqual(old_hp.over_armour.amount, 100)
        self.assertAlmostEqual(
            hero_torbjorn.overload_cooldown_remaining(owner), deadline - fight.now, delta=.03)
        self.assertTrue(match.switch_hero(owner, heroes()[SOLDIER]))
        self.assertNotIn(old_body, fight.vitals)
        self.assertTrue(match.switch_hero(owner, torbjorn_hero))
        self.assertNotEqual(owner.body, old_body)
        self.assertAlmostEqual(run_forward(), 5.5, delta=.002)
        fresh = fight.vitals_of(next(target for target in fight._targets() if target.owner is owner))
        self.assertIsNot(fresh, old_hp)
        self.assertEqual(fresh.total, 250)
        self.assertIsNone(fresh.over_armour.source)
        run.step({owner: (0, 0, 16)})
        self.assertEqual(fresh.total, 250)
        while fight.now < deadline + .05:
            run.step({})
        self.assertEqual(fresh.total, 250)
        run.step({owner: (0, 0, 16)})
        self.assertEqual(fresh.total, 350)
        self.assertAlmostEqual(run_forward(), 7.15, delta=.002)

    def test_reselect_during_depleted_overload_preserves_original_reuse_deadline(self):
        run, owner, enemy, target, old_hp = self.arena()
        match = run.match
        fight = match.combat
        old_body = owner.body
        source = old_hp.over_armour.source
        deadline = fight.now + 15
        x, y, z = target.feet
        direction = aim(fight._eye(enemy), (x, y + .6, z))
        for _ in range(100):
            if old_hp.over_armour.amount == 0:
                break
            run.step({enemy: (*direction, 1)})
        self.assertEqual(old_hp.over_armour.amount, 0)
        self.assertEqual(old_hp.over_armour.source, source)
        self.assertEqual(old_hp.now[world.ARMOUR_POOL], 45)
        self.assertEqual(old_hp.now[world.HEALTH_POOL], 200)

        def run_forward(button=0):
            for offset in range(40):
                run.frame += 1
                match.update(run.frame * combat.TICK, run.frame)
                owner.take_commands([Command(run.frame, yaw=16384, forward=127,
                                             buttons=button if offset % 2 else 0)])
                enemy.take_commands([Command(run.frame)])
            return owner.mover.velocity[0]

        self.assertAlmostEqual(run_forward(16), 7.15, delta=.002)
        self.assertEqual(old_hp.over_armour.amount, 0)
        self.assertEqual(old_hp.over_armour.source, source)
        self.assertAlmostEqual(
            hero_torbjorn.overload_cooldown_remaining(owner), deadline - fight.now, delta=.03)

        self.assertTrue(match.switch_hero(owner, owner.hero))
        self.assertNotEqual(owner.body, old_body)
        self.assertNotIn(old_body, fight.vitals)
        self.assertAlmostEqual(
            hero_torbjorn.overload_cooldown_remaining(owner), deadline - fight.now, delta=.03)
        self.assertAlmostEqual(run_forward(16), 5.5, delta=.002)
        fresh = fight.vitals[owner.body]
        self.assertEqual(fresh.total, 250)
        self.assertIsNone(fresh.over_armour.source)
        while fight.now < deadline + .05:
            run.step({})
            self.assertIsNone(fresh.over_armour.source)
        self.assertEqual(hero_torbjorn.overload_cooldown_remaining(owner), 0)
        run.step({owner: (0, 0, 16)})
        self.assertEqual(fresh.total, 350)
        self.assertEqual(fresh.over_armour.amount, 100)
        self.assertIsNotNone(fresh.over_armour.source)
        self.assertAlmostEqual(run_forward(), 7.15, delta=.002)

    def test_temporary_armour_precedes_recoverable_shields(self):
        vitals = combat.Vitals(1, 200.0, 50.0, 25.0)
        vitals.set_over_armour((1, 1), 100.0)
        self.assertEqual(vitals.damage(40.0), 35.0)
        self.assertEqual(vitals.now[world.SHIELDS_POOL], 25.0)
        self.assertEqual(vitals.over_armour.amount, 65.0)
        self.assertEqual(vitals.now[world.ARMOUR_POOL], 50.0)

    def test_one_shot_crosses_temporary_armour_shield_and_armour_without_double_reduction(self):
        vitals = combat.Vitals(1, 200.0, 50.0, 25.0)
        vitals.set_over_armour((1, 1), 10.0)
        self.assertEqual(vitals.damage(100.0), 95.0)
        self.assertEqual(vitals.over_armour.amount, 0.0)
        self.assertEqual(vitals.now, {world.HEALTH_POOL: 190.0, world.ARMOUR_POOL: 0.0,
                                      world.SHIELDS_POOL: 0.0})

    def test_periodic_damage_reaches_actual_overload_armour_without_per_tick_reduction(self):
        run, _, enemy, target, vitals = self.arena()
        fight = run.match.combat
        for _ in range(10):
            fight._damage(enemy, target, 2.56, False, None, run.frame, armour_reduction=False)
        self.assertAlmostEqual(vitals.over_armour.amount, 74.4)
        self.assertAlmostEqual(vitals.total, 324.4)

    def test_passive_snapshot_observes_temporary_pool_source_and_damage(self):
        from test_server_snapshot import ServerSnapshotTests

        from ow174.game.server_snapshot import snapshot_digest

        case = ServerSnapshotTests()
        session, _, players = case.scene()
        owner = players[0]
        fight = owner.match.combat
        target = next(t for t in fight._targets() if t.owner is owner)
        vitals = fight.vitals_of(target)
        initial = snapshot_digest(case.capture(session))
        vitals.set_over_armour((7, 1), 100.0)
        active = snapshot_digest(case.capture(session))
        self.assertNotEqual(initial, active)
        vitals.damage(50.0)
        damaged = snapshot_digest(case.capture(session))
        self.assertNotEqual(active, damaged)
        vitals.over_armour.source = (7, 2)
        self.assertNotEqual(damaged, snapshot_digest(case.capture(session)))
