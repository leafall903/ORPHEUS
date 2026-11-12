"""Charge through actual inputs and the build's mounted charge graph."""

import math
import unittest
from dataclasses import replace

from test_game_combat import KANEZAKA_DM, Run, aim
from test_symmetra_combat import arena, fire, health

from ow174.game import combat, stats, symmetra
from ow174.game.combat import TICK
from ow174.game.commands import Command
from ow174.game.content import SOLDIER, heroes
from ow174.game.script.driver import ULT_CHARGE
from ow174.game.world import HEALTH_POOL, SHIELDS_POOL


def charge(player):
    return player.body_script.component.entity_var(ULT_CHARGE).value()


def launch_orb_before_pvp_respawn(test, distance, defender=None):
    run = Run(KANEZAKA_DM, ("Symmetra", "Killer", "Orb target"))
    owner, killer, target = run.players
    run.match.bots.clear()
    run.match._pending_bot_scripts.clear()
    test.assertTrue(run.match.switch_hero(owner, heroes()[symmetra.HERO]))
    if defender is not None:
        test.assertTrue(run.match.switch_hero(target, defender))
    owner.mover.position[:] = (0, 1, 0)
    killer.mover.position[:] = (0, 1, -6)
    target.mover.position[:] = (0, 1, distance)
    fight = run.match.combat
    own_pool = health(run, owner)
    own_pool.now[HEALTH_POOL] = 1
    own_pool.now[SHIELDS_POOL] = 0
    for _ in range(90):
        run.step({owner: (0, 0, 2)})
    run.step({})
    [orb] = fight.rockets
    test.assertIs(orb.shooter, owner)
    test.assertEqual((orb.speed, orb.left), (25, 25))
    aim_at_owner = aim(fight._eye(killer), (0, 2.2, 0))
    run.step({killer: (*aim_at_owner, 1)})
    test.assertTrue(own_pool.dead)
    test.assertEqual(own_pool.killer, killer.body)
    test.assertIn(orb, fight.rockets)
    [deadline] = [when for when, player in fight.respawns if player is owner]
    test.assertAlmostEqual(deadline - own_pool.died_at, 10)
    return run, owner, target, orb, deadline


def orb_after_pvp_respawn(test, defender):
    run, owner, target, orb, deadline = launch_orb_before_pvp_respawn(test, 265, defender)
    old_body = owner.body
    saved = charge(owner)
    run.frame = math.ceil(deadline / TICK) + 1
    run.match.update(run.frame * TICK, run.frame)
    test.assertNotEqual(owner.body, old_body)
    test.assertIn(orb, run.match.combat.rockets)
    test.assertEqual(charge(owner), saved)
    owner.body_script.component.set(12918, 0)  # Isolate damage charge after replacement.
    return run, owner, target, orb


class SymmetraChargeTests(unittest.TestCase):
    def test_world_idle_charge_keeps_command_frames_and_authored_cadence(self):
        for offset in (0, 1000):
            with self.subTest(offset=offset):
                run, owner, enemy = arena()
                script = owner.body_script
                initial = run.frame
                command_now = script.component.now

                def update(frames, initial=initial, match=run.match, offset=offset):
                    tick = initial + frames
                    match.update(tick * TICK + offset, tick)

                update(62)
                self.assertEqual(charge(owner), 0)
                update(63)
                self.assertEqual(charge(owner), 5)
                update(250)
                self.assertEqual(charge(owner), 20)
                update(250)
                self.assertEqual(charge(owner), 20)
                self.assertEqual((script.frame, script.component.now), (initial, command_now))

                before = health(run, enemy).total
                yaw, pitch = aim((0, 2.55, 0), (0, 2.0, 6))
                owner.take_commands([Command(initial + 249, yaw=yaw, pitch=pitch, buttons=1)])
                self.assertEqual(script.frame, initial + 249)
                self.assertLess(health(run, enemy).total, before)
                self.assertAlmostEqual(charge(owner), 20 + before - health(run, enemy).total, places=4)
                before = charge(owner)
                update(313)
                self.assertAlmostEqual(charge(owner), before + 5, places=4)
                script.set_ult_charge(1678)
                update(438)
                self.assertEqual(charge(owner), 1680)

    def test_idle_charge_runs_on_authored_second_and_respects_multiplier_and_cap(self):
        run, owner, _ = arena()
        component = owner.body_script.component
        component.set(12918, 2)
        fire(run, owner, 50, buttons=0)
        self.assertEqual(charge(owner), 0)
        fire(run, owner, 15, buttons=0)
        self.assertEqual(charge(owner), 10)
        self.assertIs(component.entity_var(2426).value(), True)
        owner.body_script.set_ult_charge(1678)
        fire(run, owner, 65, buttons=0)
        self.assertEqual(charge(owner), 1680)
        fire(run, owner, 65, buttons=0)
        self.assertEqual(charge(owner), 1680)

    def test_primary_and_secondary_credit_resolved_pool_loss(self):
        for button in (1, 2):
            with self.subTest(button=button):
                run, owner, enemy = arena()
                owner.body_script.component.set(12918, 0)
                before = health(run, enemy).total
                if button == 1:
                    fire(run, owner, 20)
                else:
                    fire(run, owner, 90, buttons=2)
                    fire(run, owner, 30, buttons=0)
                dealt = before - health(run, enemy).total
                self.assertGreater(dealt, 0)
                self.assertAlmostEqual(charge(owner), dealt, places=4)

    def test_enemy_relation_and_actual_miss_do_not_credit_damage(self):
        run, owner, enemy = arena()
        owner.body_script.component.set(12918, 0)
        before = health(run, enemy).total
        fire(run, owner, 12, yaw=-32768)
        self.assertEqual(health(run, enemy).total, before)
        self.assertEqual(charge(owner), 0)
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        enemy.team = owner.team
        fire(run, owner, 12)
        self.assertEqual(health(run, enemy).total, before)
        self.assertEqual(charge(owner), 0)

    def test_melee_credits_the_actual_pool_loss(self):
        run, owner, enemy = arena()
        owner.body_script.component.set(12918, 0)
        enemy.mover.position[:] = (0, 1, 1.5)
        before = health(run, enemy).total
        fire(run, owner, buttons=0x800)
        fire(run, owner, 30, buttons=0)
        self.assertEqual(before - health(run, enemy).total, 30)
        self.assertEqual(charge(owner), 30)

    def test_fatal_overkill_credits_only_available_health_with_damage_multiplier(self):
        run, owner, enemy = arena()
        component = owner.body_script.component
        component.set(12918, 0)
        component.set(9532, 2)
        health(run, enemy).now[HEALTH_POOL] = .5
        fire(run, owner)
        self.assertTrue(run.match.combat.is_dead(enemy))
        self.assertEqual(charge(owner), 1)

    def test_charge_survives_real_same_hero_respawn_and_resets_on_selection(self):
        run, owner, enemy = arena()
        owner.body_script.component.set(12918, 0)
        fire(run, owner, 8)
        yaw, pitch = aim(run.match.combat._eye(enemy), (0, 2.1, 0))
        for _ in range(180):
            run.step({enemy: (yaw, pitch, 1)})
            if run.match.combat.is_dead(owner):
                break
        self.assertTrue(run.match.combat.is_dead(owner))
        saved = charge(owner)
        self.assertGreater(saved, 0)
        owner.body_script.component.set(12918, 1)
        old_body = owner.body
        for _ in range(700):
            run.frame += 1
            run.match.update(run.frame * TICK, run.frame)
            if owner.body != old_body:
                break
            self.assertEqual(charge(owner), saved)
        self.assertNotEqual(owner.body, old_body)
        self.assertEqual(charge(owner), saved)
        run.frame += 63
        run.match.update(run.frame * TICK, run.frame)
        self.assertAlmostEqual(charge(owner), saved + 5, places=4)
        self.assertTrue(run.match.switch_hero(owner, heroes()[SOLDIER]))
        self.assertEqual(charge(owner), 0)

    def test_launched_orb_resolves_before_or_after_actual_pvp_respawn(self):
        for distance, after_respawn in ((245, False), (265, True)):
            with self.subTest(distance=distance):
                stepped = self.orb_impact_across_respawn(distance, after_respawn, gap=False)
                self.assertEqual(self.orb_impact_across_respawn(distance, after_respawn, gap=True), stepped)

    def test_launched_orb_hits_before_lifetime_limit_and_expires_beyond_it(self):
        for distance, damage in ((624, 120), (626, 0)):
            with self.subTest(distance=distance):
                run, owner, target = arena()
                target.mover.position[:] = (0, 1, distance)
                fight = run.match.combat
                pool = health(run, target)
                before = pool.total
                for _ in range(90):
                    run.step({owner: (0, 0, 2)})
                run.step({})
                [orb] = fight.rockets
                self.assertEqual(orb.speed * orb.left, 625)
                run.step({})  # Admit flight before the scheduler catches up.
                run.frame += math.ceil(orb.left / TICK) + 1
                run.match.update(run.frame * TICK, run.frame)
                self.assertNotIn(orb, fight.rockets)
                self.assertAlmostEqual(before - pool.total, damage, places=4)
                self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), damage)
                if damage == 0:
                    self.assertFalse(owner.client.messages(combat.HIT))
                    self.assertAlmostEqual(orb.flown, 25)

    def orb_impact_across_respawn(self, distance, after_respawn, *, gap):
        run, owner, target, orb, deadline = launch_orb_before_pvp_respawn(self, distance)
        fight = run.match.combat
        target_pool = health(run, target)
        target_pool.now[HEALTH_POOL] = 100
        old_body = owner.body
        saved = charge(owner)
        respawn_tick = math.ceil(deadline / TICK)
        before, after = respawn_tick - 1, respawn_tick + 1
        impact_end = after + 50 if after_respawn else before
        checkpoints = (before, after, impact_end) if after_respawn else (before,)
        for tick in checkpoints if gap else range(run.frame + 1, impact_end + 1):
            run.match.update(tick * TICK, tick)
            if after_respawn and tick == after:
                self.assertNotEqual(owner.body, old_body)
                self.assertIn(orb, fight.rockets)
                self.assertFalse(target_pool.dead)
                self.assertEqual(charge(owner), saved)
        self.assertEqual(owner.body != old_body, after_respawn)
        self.assertNotIn(orb, fight.rockets)
        self.assertTrue(target_pool.dead)
        self.assertEqual(target_pool.died_at >= deadline, after_respawn)
        self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 100)
        self.assertEqual(owner.stats.value(stats.ELIMINATIONS), 1)
        [kill] = owner.client.messages(combat.KILL_NOTICE)
        [death] = target.client.messages(combat.DEATH_NOTICE)
        self.assertEqual(kill["+0x84"], {"+0x0": target.body})
        self.assertEqual(kill["+0x80"], {"+0x0": target_pool.killer})
        self.assertEqual(death["+0x7C"], kill["+0x80"])
        if not after_respawn:
            self.assertEqual(target_pool.killer, old_body)
            self.assertEqual(charge(owner), saved)
        # Compare observed attribution and charge across scheduler paths. Build
        # parity for a hit after replacement is still an open acceptance item.
        return target_pool.killer == old_body, charge(owner) - saved
