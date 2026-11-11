"""Ultimate points from executed combat, eligible healing and lifecycle gates.

The Practice Range passive timer and charge expressions execute in the body VM.
Mode broadcast delivery and combat eligibility still use scoped host services.
"""

import unittest
from unittest.mock import patch

import test_soldier_combat_lane as fixture
from test_game_combat import Run, aim

from ow174.game import combat, projectile_tags, ultimate
from ow174.game.collision import World, flat_world
from ow174.game.commands import Command
from ow174.game.content import PRACTICE_RANGE, PRACTICE_RANGE_VARIANT_MODE, game_map, heroes
from ow174.game.native_replay import snapshot_hash
from ow174.game.script import (
    entity_queries,
    expr,
    graph,
    message_receive,
    message_receiver,
    message_state,
    runtime,
    scene,
)
from ow174.game.world import GAME_MODE_ENTITY


class SoldierUltimateChargeTests(unittest.TestCase):
    input = fixture.SoldierCombatLaneTests.input
    vitals = fixture.SoldierCombatLaneTests.vitals
    add_bot = fixture.SoldierCombatLaneTests.add_bot
    start_visor = fixture.SoldierCombatLaneTests.start_visor

    def arena(self):
        run, soldier, other = fixture.SoldierCombatLaneTests().arena()
        run.match.collision_world = flat_world(0.0)
        soldier.mover.position = list(soldier.mover.position)
        other.mover.position = [0.0, 0.0, 1.0]
        return run, soldier, other

    def charge(self, actor):
        return actor.body_script.component.vars[342].value()

    def test_default_passive_is_points_per_second_and_duplicate_input_cannot_add_points(self):
        run, soldier, _ = self.arena()
        script = soldier.body_script
        self.assertIsNone(script.ult_seconds)
        for _ in range(625):
            self.input(run, soldier)
        self.assertAlmostEqual(self.charge(soldier), 50, delta=0.001)
        before = self.charge(soldier)
        script.command(script.frame, 0)
        run.match.combat.command(soldier, Command(script.frame))
        self.assertEqual(self.charge(soldier), before)

    def test_rifle_mitigated_damage_generates_only_actual_pool_loss_once(self):
        run, soldier, other = self.arena()
        soldier.body_script.ult_seconds = 0
        fight = run.match.combat
        pool = combat.Vitals(other.body, 200, armour=50, shields=25)
        fight.vitals[other.body] = pool
        yaw, pitch = aim(fight._eye(soldier), (0.0, 0.8, 1.0))
        for _ in range(15):
            self.input(run, soldier, 1, yaw, pitch)
        self.assertEqual(pool.total, 225)
        self.assertEqual(self.charge(soldier), 50)
        self.assertEqual(pool.ult_healing_deficit, 50)
        script = soldier.body_script
        script.command(script.frame, 1)
        fight.command(soldier, Command(script.frame, yaw=yaw, pitch=pitch, buttons=1))
        self.assertEqual(self.charge(soldier), 50)
        self.assertEqual(pool.total, 225)

    def test_overkill_grants_only_remaining_health_and_dead_targets_grant_no_more(self):
        run, soldier, other = self.arena()
        soldier.body_script.ult_seconds = 0
        pool = combat.Vitals(other.body, 10)
        run.match.combat.vitals[other.body] = pool
        yaw, pitch = aim(run.match.combat._eye(soldier), (0.0, 0.8, 1.0))
        self.input(run, soldier, 1, yaw, pitch)
        self.assertTrue(pool.dead)
        self.assertEqual(self.charge(soldier), 10)
        for _ in range(25):
            self.input(run, soldier, 1, yaw, pitch)
        self.assertEqual(self.charge(soldier), 10)

    def test_miss_friendly_and_static_wall_cannot_generate_combat_charge(self):
        for case in ("miss", "ally", "wall"):
            with self.subTest(case=case):
                run, soldier, other = self.arena()
                soldier.body_script.ult_seconds = 0
                yaw, pitch = aim(run.match.combat._eye(soldier), (0.0, 0.8, 1.0))
                if case == "miss":
                    yaw = 16384
                elif case == "ally":
                    other.team = soldier.team
                else:
                    run.match.collision_world = World([
                        (-10, -10, 0, 10, -10, 0, 10, 10, 0),
                        (-10, -10, 0, 10, 10, 0, -10, 10, 0),
                    ])
                self.input(run, soldier, 1, yaw, pitch)
                self.assertEqual(self.vitals(run, other).total, 200)
                self.assertEqual(self.charge(soldier), 0)

    def test_authored_field_self_healing_awards_only_enemy_caused_missing_hp(self):
        for cause in ("enemy", "environment", "self"):
            with self.subTest(cause=cause):
                run, soldier, other = self.arena()
                soldier.body_script.ult_seconds = 0
                other.body_script.ult_seconds = 0
                fight = run.match.combat
                target = next(target for target in fight._targets() if target.owner is soldier)
                if cause == "enemy":
                    yaw, pitch = aim(fight._eye(other), (0.0, 0.8, -3.0))
                    self.input(run, other, 1, yaw, pitch)
                elif cause == "self":
                    fight._damage(soldier, target, 20, False, None, fight.tick)
                else:
                    fight.vitals_of(target).damage(20)
                self.assertEqual(fight.vitals_of(target).total, 180)
                self.input(run, soldier, 0x10)
                for _ in range(180):
                    self.input(run, soldier)
                self.assertEqual(fight.vitals_of(target).total, 200)
                self.assertAlmostEqual(self.charge(soldier), 20 if cause == "enemy" else 0, places=5)
                self.assertEqual(fight.vitals_of(target).ult_healing_deficit, 0)
                charge = self.charge(soldier)
                for _ in range(80):
                    self.input(run, soldier)
                self.assertEqual(self.charge(soldier), charge)  # overhealing cannot farm charge

    def test_deployed_field_heals_after_caster_death_but_cannot_charge_the_dead(self):
        run, soldier, ally = self.arena()
        soldier.body_script.ult_seconds = 0
        ally.team = soldier.team
        ally.mover.position = soldier.mover.position
        attacker = self.add_bot(run, 1 - soldier.team, (20.0, 0.0, 20.0))
        fight = run.match.combat
        target = next(target for target in fight._targets() if target.owner is ally)
        fight._damage(attacker, target, 100, False, None, fight.tick)
        self.input(run, soldier, 0x10)
        for _ in range(80):
            self.input(run, soldier)
        pool = fight.vitals_of(target)
        self.assertGreater(pool.total, 100)
        before = pool.total
        own = next(target for target in fight._targets() if target.owner is soldier)
        fight._damage(attacker, own, 200, False, None, fight.tick)
        self.assertTrue(fight.is_dead(soldier))
        charge = self.charge(soldier)
        for _ in range(80):
            self.input(run, soldier)
        self.assertGreater(pool.total, before)
        self.assertEqual(self.charge(soldier), charge)

    def test_field_healing_during_visor_cannot_charge_or_defer_points_until_expiry(self):
        run, soldier, ally = self.arena()
        ally.team = soldier.team
        ally.mover.position = soldier.mover.position
        attacker = self.add_bot(run, 1 - soldier.team, (20.0, 0.0, 20.0))
        fight = run.match.combat
        target = next(target for target in fight._targets() if target.owner is ally)
        fight._damage(attacker, target, 100, False, None, fight.tick)
        self.start_visor(run, soldier)
        self.input(run, soldier, 0x10)
        for _ in range(240):
            self.input(run, soldier)
        self.assertEqual(fight.vitals_of(target).total, 200)
        self.assertEqual(self.charge(soldier), 0)
        self.assertEqual(fight.vitals_of(target).ult_healing_deficit, 0)
        for _ in range(400):
            self.input(run, soldier)
        self.assertEqual(self.charge(soldier), 0)

    def test_visor_starts_and_expires_without_more_owner_commands(self):
        run, soldier, other = self.arena()
        script = soldier.body_script
        script.ult_seconds = 0
        script.set_ult_charge(script.component.vars[338].value())
        run.step({soldier: (0, 0, 0x20)})
        visor = next(i for i in script.component.instances.values() if i.graph.index == 0x0259)
        windup, countdown = visor.state(visor.graph.node(91).state), visor.state(6)
        self.assertTrue(windup.active)
        self.assertFalse(countdown.active)
        self.assertEqual(self.charge(soldier), 0)

        run.frame += 77  # 75 body frames for the 1.2-second windup, plus the retained frame.
        run.match.update(run.frame * combat.TICK, run.frame)
        self.assertFalse(windup.active)
        self.assertTrue(countdown.active)
        self.assertEqual(countdown.counter, 1)
        run.frame += 380
        run.match.update(run.frame * combat.TICK, run.frame)
        self.assertFalse(countdown.active)
        self.assertEqual(countdown.counter, 1)
        self.assertEqual(self.charge(soldier), 0)
        self.assertEqual(self.vitals(run, other).total, 200)

    def test_in_flight_helix_charge_matches_idle_ticks_across_visor_expiry(self):
        for early in (True, False):
            outcomes = []
            for catch_up in (False, True):
                with self.subTest(early=early, catch_up=catch_up):
                    run, soldier, enemy = self.arena()
                    enemy.mover.position[2] = 7 if early else 22
                    script = soldier.body_script
                    script.ult_seconds = enemy.body_script.ult_seconds = 0
                    script.set_ult_charge(script.component.vars[338].value())
                    run.step({soldier: (0, 0, 0x20)})
                    visor = next(i for i in script.component.instances.values() if i.graph.index == 0x0259)
                    countdown = visor.state(6)
                    for _ in range(85):
                        run.step({})
                        if countdown.active:
                            break
                    self.assertTrue(countdown.active)
                    deadline = countdown.start + 6000
                    while script.component.now < deadline - 320:
                        run.step({})
                    fight = run.match.combat
                    pool = self.vitals(run, enemy)
                    yaw, pitch = aim(fight._eye(soldier), (0, .8, enemy.mover.position[2]))
                    run.step({soldier: (yaw, pitch, 2)})
                    for _ in range(8):
                        if fight.rockets and fight.rockets[0].last_tick is not None:
                            break
                        run.step({})
                    rocket, = fight.rockets
                    self.assertIsNotNone(rocket.last_tick)
                    self.assertTrue(countdown.active)
                    self.assertFalse(any(shot.field_due is not None for shot in fight.projectiles.shots))
                    self.assertEqual(pool.total, 200)

                    first_tick = run.frame + 1
                    end_tick = first_tick + 42
                    ticks = (end_tick,) if catch_up else range(first_tick, end_tick + 1)
                    impact_during_visor = None
                    for tick in ticks:
                        run.match.update(tick * combat.TICK, tick)
                        if pool.total < 200 and impact_during_visor is None:
                            impact_during_visor = countdown.active
                    if not catch_up:
                        self.assertIs(impact_during_visor, early)
                    self.assertNotIn(rocket, fight.rockets)
                    self.assertFalse(countdown.active)
                    self.assertEqual(pool.total, 80)
                    self.assertEqual(self.charge(soldier), 0 if early else 120)
                    outcomes.append((rocket.last_tick, rocket.flown))
            (sequential_tick, sequential_time), (caught_up_tick, caught_up_time) = outcomes
            self.assertEqual(caught_up_tick, sequential_tick)
            self.assertAlmostEqual(caught_up_time, sequential_time, places=6)

    def test_field_healing_charge_matches_idle_ticks_across_visor_expiry(self):
        stepped = {}
        for elapsed, catch_up in ((41, False), (41, True), (96, False), (96, True)):
            with self.subTest(elapsed=elapsed, catch_up=catch_up):
                run, soldier, ally = self.arena()
                script = soldier.body_script
                script.ult_seconds = ally.body_script.ult_seconds = 0
                fight = run.match.combat
                pool = self.vitals(run, ally)
                angles = aim(fight._eye(soldier), (0, .8, 1))
                for _ in range(40):
                    run.step({soldier: (*angles, 1)})
                    if pool.total <= 140:
                        break
                self.assertEqual(pool.total, 140)
                self.assertEqual(pool.ult_healing_deficit, 60)
                script.set_ult_charge(script.component.vars[338].value())
                run.step({soldier: (0, 0, 0x20)})
                visor = next(i for i in script.component.instances.values() if i.graph.index == 0x0259)
                countdown = visor.state(6)
                for _ in range(85):
                    run.step({})
                    if countdown.active:
                        break
                self.assertTrue(countdown.active)
                deadline = countdown.start + 6000
                while script.component.now < deadline - 4200:
                    run.step({})
                run.step({soldier: (0, 0, 0x10)})
                while script.component.now < deadline - 320:
                    run.step({})
                self.assertTrue(countdown.active)
                field, = fight.projectiles.shots
                self.assertIsNotNone(field.field_tick)
                self.assertEqual(self.charge(soldier), 0)
                self.assertFalse(fight.rockets)  # no flight can independently schedule retained frames
                before = pool.total
                self.assertEqual(before, 140)
                ally.team = soldier.team

                first_tick = run.frame + 1
                end_tick = run.frame + elapsed
                ticks = (end_tick,) if catch_up else range(first_tick, end_tick + 1)
                expected_charge = 0.0
                for tick in ticks:
                    run.match.update(tick * combat.TICK, tick)
                    if not catch_up:
                        if not countdown.active:
                            if elapsed > combat.COMBAT_CATCH_UP and expected_charge == 0:
                                self.assertLess(tick, end_tick - combat.COMBAT_CATCH_UP + 1)
                            expected_charge += 35 * combat.TICK
                        self.assertAlmostEqual(self.charge(soldier), expected_charge, places=4)
                self.assertFalse(countdown.active)
                self.assertIn(field, fight.projectiles.shots)
                self.assertAlmostEqual(pool.total - before, elapsed * 35 * combat.TICK)
                self.assertGreater(self.charge(soldier), 0)
                self.assertLess(self.charge(soldier), pool.total - before)
                if catch_up:
                    self.assertAlmostEqual(self.charge(soldier), stepped[elapsed], places=4)
                else:
                    stepped[elapsed] = self.charge(soldier)

    def test_late_landing_field_recovers_charge_after_visor_expires_in_a_gap(self):
        results = {}
        for catch_up in (False, True):
            with self.subTest(catch_up=catch_up):
                run, soldier, ally = self.arena()
                script = soldier.body_script
                script.ult_seconds = ally.body_script.ult_seconds = 0
                fight = run.match.combat
                pool = self.vitals(run, ally)
                angles = aim(fight._eye(soldier), (0, .8, 1))
                for _ in range(100):
                    run.step({soldier: (*angles, 1)})
                    if pool.total <= 20:
                        break
                self.assertEqual(pool.total, 20)
                self.assertEqual(pool.ult_healing_deficit, 180)
                script.set_ult_charge(script.component.vars[338].value())
                run.step({soldier: (0, 0, 0x20)})
                visor = next(i for i in script.component.instances.values() if i.graph.index == 0x0259)
                countdown = visor.state(6)
                for _ in range(85):
                    run.step({})
                    if countdown.active:
                        break
                self.assertTrue(countdown.active)

                soldier.mover.position = [0, 110, -3]
                soldier.mover.body = None
                run.step({soldier: (0, 0, 0x10)})
                for _ in range(50):
                    if fight.rockets:
                        break
                    run.step({})
                rocket, = fight.rockets
                field = rocket.shot
                self.assertTrue(field.field_airborne)
                early_due = field.field_due
                self.assertLess((early_due - run.frame) * 16 + 5000,
                                countdown.start + 6000 - script.component.now)
                ally.team = soldier.team
                ally.mover.position = [0, 0, 15]
                ally.mover.body = None

                end = run.frame + 520
                ticks = (end,) if catch_up else range(run.frame + 1, end + 1)
                last_visor_health = pool.total
                for tick in ticks:
                    run.match.update(tick * combat.TICK, tick, frames=False)
                    if countdown.active:
                        self.assertEqual(self.charge(soldier), 0)
                        last_visor_health = pool.total
                self.assertFalse(countdown.active)
                self.assertGreater(field.field_tick, early_due + 60)
                self.assertGreater(pool.total, 160)
                if not catch_up:
                    self.assertGreater(last_visor_health, 20)
                    self.assertAlmostEqual(self.charge(soldier), pool.total - last_visor_health, places=4)
                results[catch_up] = (field.field_tick, pool.total, self.charge(soldier))

        self.assertEqual(len(results), 2)
        self.assertEqual(results[False][0], results[True][0])
        self.assertAlmostEqual(results[False][1], results[True][1], places=4)
        self.assertGreater(results[False][2], 0)
        self.assertAlmostEqual(results[False][2], results[True][2], places=4)

    def test_authored_damage_multipliers_and_explicit_zero_change_points_not_hp_loss(self):
        run, soldier, other = self.arena()
        script = soldier.body_script
        script.ult_seconds = 0
        script.component.set(9532, 2.0)
        script.component.set(13022, 3.0)
        yaw, pitch = aim(run.match.combat._eye(soldier), (0.0, 0.8, 1.0))
        self.input(run, soldier, 1, yaw, pitch)
        self.assertEqual(self.vitals(run, other).total, 180)
        self.assertEqual(self.charge(soldier), 120)
        script.component.set(13022, 0.0)
        for _ in range(30):
            self.input(run, soldier)
        self.input(run, soldier, 1, yaw, pitch)
        self.assertEqual(self.vitals(run, other).total, 160)
        self.assertEqual(self.charge(soldier), 120)

    def test_roadhog_resource_tag_scales_charge_without_reducing_attack_damage(self):
        tags = projectile_tags.tags_of(0x040000000000028F)
        if tags is None:
            self.skipTest("prepared target tag catalog required")
        self.assertIn(0x0D6000000000011E, tags)
        for buttons, z, damage in ((1, 1, 20), (2, 5, 120), (0x800, -1.5, 30)):
            with self.subTest(buttons=buttons):
                run, soldier, target = self.arena()
                soldier.body_script.ult_seconds = 0
                self.assertTrue(run.match.switch_hero(target, heroes()[0x02E0000000000040]))
                self.assertEqual(target.hero.body, 0x040000000000028F)
                target.mover.position = [0, 0, z]
                target.mover.body = None
                before = self.vitals(run, target).total
                angles = aim(run.match.combat._eye(soldier), (0, .8, z))
                run.step({soldier: (*angles, buttons)})
                for _ in range(25):
                    run.step({})
                self.assertEqual(before - self.vitals(run, target).total, damage)
                self.assertEqual(self.charge(soldier), damage * .75)

    def test_training_bot_damage_entry_records_actual_target_and_honors_suppression(self):
        tags = projectile_tags.tags_of(0x0400000000001403)
        if tags is None:
            self.skipTest("prepared target tag catalog required")
        self.assertIn(ultimate.TRAINING_TARGET_TAG, tags)
        for suppressed in (False, True):
            with self.subTest(suppressed=suppressed):
                run = Run()
                soldier, = run.players
                bot = next(bot for bot in run.match.bots
                           if bot.body == 0x0400000000001403 and bot.team != soldier.team)
                run.match.bots[:] = [bot]
                bot.position = (0, 0, 3)
                bot.route = []
                bot.firing = False
                soldier.mover.position = [0, 0, -3]
                soldier.mover.body = None
                run.match.collision_world = flat_world(0)
                script = soldier.body_script
                script.ult_seconds = 0
                if suppressed:
                    script.component.set(12321, True)
                angles = aim(run.match.combat._eye(soldier), (0, .8, 3))
                pool = self.vitals(run, bot)
                before = pool.total
                run.step({soldier: (*angles, 1)})
                self.assertEqual(before - pool.total, 20)
                self.assertEqual(self.charge(soldier), 0 if suppressed else 20)
                instance = ultimate.instance_of(script)
                self.assertEqual(instance.find_var(0, 393).value().id, bot.entity)
                self.assertEqual(instance.find_var(0, 336).value(), 20)

    def test_pilot_damage_entry_uses_enemy_tag_filter(self):
        tags = projectile_tags.tags_of(0x040000000000040A)
        if tags is None:
            self.skipTest("prepared target tag catalog required")
        self.assertIn(ultimate.DVA_TARGET_TAG, tags)
        for ally in (False, True):
            with self.subTest(ally=ally):
                run, soldier, pilot = self.arena()
                script = soldier.body_script
                script.ult_seconds = 0
                self.assertTrue(run.match.switch_hero(pilot, heroes()[0x02E000000000007A]))
                self.assertEqual(pilot.hero.body, 0x040000000000040A)
                pilot.mover.position = [0, 0, 1]
                pilot.mover.body = None
                if ally:
                    pilot.team = soldier.team
                pool = self.vitals(run, pilot)
                before = pool.total
                angles = aim(run.match.combat._eye(soldier), (0, .8, 1))
                run.step({soldier: (*angles, 1)})
                self.assertEqual(before - pool.total, 0 if ally else 20)
                self.assertEqual(self.charge(soldier), 0 if ally else 20)
                instance = ultimate.instance_of(script)
                recipient = instance.find_var(0, 393, create=False)
                if ally:
                    self.assertTrue(recipient is None or recipient.value() is None)
                    # Exercise the receiver's relation gate independently of combat's ally gate.
                    runtime.send_instance_message(instance, ultimate.DAMAGE_CREDIT, {
                        0x13: 20.0, 0x15: expr.Entity(pilot.body),
                    }, soldier.body)
                    script.component.run_now()
                    self.assertEqual(instance.find_var(0, 393).value().id, pilot.body)
                    self.assertEqual(self.charge(soldier), 0)
                else:
                    self.assertEqual(recipient.value().id, pilot.body)

    def test_practice_permission_controls_charge_without_blocking_damage_or_healing(self):
        run, soldier, target = self.arena()
        script = soldier.body_script
        script.ult_seconds = 0
        mode = next(i for i in script.component.instances.values() if i.graph.index == 0x01C7)
        permission = mode.states[mode.graph.node(77).state]
        self.assertTrue(permission.active)
        permission.end(False)
        script.component.run_now()
        self.assertFalse(script.component.entity_var(2426).value())
        angles = aim(run.match.combat._eye(soldier), (0, .8, 1))
        run.step({soldier: (*angles, 1)})
        self.assertEqual(self.vitals(run, target).total, 180)
        self.assertEqual(self.charge(soldier), 0)
        reverse = aim(run.match.combat._eye(target), (0, .8, -3))
        run.step({target: (*reverse, 1)})
        self.assertEqual(self.vitals(run, soldier).total, 180)
        run.step({soldier: (0, 0, 0x10)})
        for _ in range(180):
            run.step({})
        self.assertEqual(self.vitals(run, soldier).total, 200)
        self.assertEqual(self.charge(soldier), 0)
        ultimate.start_practice_timer(script, GAME_MODE_ENTITY)
        self.assertTrue(permission.active)
        run.step({soldier: (*angles, 1)})
        self.assertEqual(self.vitals(run, target).total, 160)
        self.assertEqual(self.charge(soldier), 20)

    def test_authored_healing_multipliers_apply_to_actual_eligible_self_healing(self):
        run, soldier, other = self.arena()
        script = soldier.body_script
        script.ult_seconds = 0
        other.body_script.ult_seconds = 0
        script.component.set(9532, 2.0)
        script.component.set(13022, 3.0)
        yaw, pitch = aim(run.match.combat._eye(other), (0.0, 0.8, -3.0))
        self.input(run, other, 1, yaw, pitch)
        self.input(run, soldier, 0x10)
        for _ in range(180):
            self.input(run, soldier)
        self.assertEqual(self.vitals(run, soldier).total, 200)
        self.assertAlmostEqual(self.charge(soldier), 120, delta=0.0001)

    def test_field_healing_entry_reads_current_recipient_charge_suppression(self):
        for suppressed in (False, True):
            with self.subTest(suppressed=suppressed):
                run, soldier, ally = self.arena()
                script = soldier.body_script
                script.ult_seconds = 0
                angles = aim(run.match.combat._eye(soldier), (0, .8, 1))
                run.step({soldier: (*angles, 1)})
                pool = self.vitals(run, ally)
                self.assertEqual(pool.total, 180)
                script.set_ult_charge(0)
                ally.team = soldier.team
                if suppressed:
                    # Fixture for the recipient flag written by authored graph 1537/node145.
                    ally.body_script.component.set(17625, True)
                run.step({soldier: (0, 0, 0x10)})
                for _ in range(180):
                    run.step({})
                self.assertEqual(pool.total, 200)
                self.assertAlmostEqual(self.charge(soldier), 0 if suppressed else 20, places=4)
                instance = ultimate.instance_of(script)
                self.assertEqual(instance.find_var(0, 536).value().id, ally.body)
                self.assertEqual(pool.ult_healing_deficit, 0)

    def test_authored_passive_interval_multipliers_and_causal_deadline(self):
        run, soldier, _ = self.arena()
        script = soldier.body_script
        instance = ultimate.instance_of(script)
        wait = instance.states[1]
        self.assertTrue(wait.active)
        self.assertIs(script.component.entity_var(2426).value(), True)
        self.assertIsNone(script.component.entity_var(2426).base)
        first = wait.start
        script.component.set(9532, 2.0)
        script.component.set(12918, 3.0)
        for _ in range(61):
            self.input(run, soldier)
        self.assertEqual(self.charge(soldier), 0)
        for _ in range(2):
            self.input(run, soldier)
        self.assertEqual(self.charge(soldier), 30)
        self.assertEqual(script.passive_charge_ms, 0)
        self.assertEqual(wait.start, first + 1000)
        event, = [event for event in instance.queue if event.state is wait]
        self.assertEqual(event.time, first + 2000)
        before = snapshot_hash(script)
        event.time += 1
        self.assertNotEqual(snapshot_hash(script), before)
        event.time -= 1
        for _ in range(62):
            self.input(run, soldier)
        self.assertEqual(self.charge(soldier), 60)

    def test_practice_pause_discards_elapsed_time_and_resumes_on_a_new_deadline(self):
        run, soldier, _ = self.arena()
        script = soldier.body_script
        wait = ultimate.instance_of(script).states[1]
        for _ in range(60):
            run.step({})
        self.assertTrue(wait.active)
        script.ult_seconds = 0
        for _ in range(20):
            run.step({})
        self.assertFalse(wait.active)
        self.assertEqual(self.charge(soldier), 0)
        script.ult_seconds = None
        run.step({})
        self.assertTrue(wait.active)
        deadline = wait.start + 1000
        while (run.frame + 1) * script.frame_ms + script.frame_ms - 1 < deadline:
            run.step({})
            self.assertEqual(self.charge(soldier), 0)
        run.step({})
        self.assertEqual(self.charge(soldier), 5)

    def test_practice_large_command_gap_runs_overdue_wait_once_then_bounded_frames(self):
        _, soldier, _ = self.arena()
        script = soldier.body_script
        wait = ultimate.instance_of(script).states[1]
        script.command(script.frame + 4096, 0)
        self.assertEqual((self.charge(soldier), wait.counter), (25, 6))
        script.command(script.frame, 0)
        self.assertEqual((self.charge(soldier), wait.counter), (25, 6))

    def test_practice_positive_sandbox_override_has_only_one_charge_source(self):
        run, soldier, _ = self.arena()
        script = soldier.body_script
        wait = ultimate.instance_of(script).states[1]
        script.ult_seconds = 2
        cost = script.component.entity_var(338).value()
        for _ in range(63):
            run.step({})
        self.assertFalse(wait.active)
        self.assertAlmostEqual(self.charge(soldier), cost * .504, delta=.002)
        self.assertEqual(script.passive_charge_ms, 0)

    def test_practice_passive_runs_on_idle_world_ticks_before_and_after_visor(self):
        for visor_active in (False, True):
            with self.subTest(visor_active=visor_active):
                run, soldier, enemy = self.arena()
                script = soldier.body_script
                wait = ultimate.instance_of(script).states[1]
                if visor_active:
                    script.set_ult_charge(script.component.vars[338].value())
                    run.step({soldier: (0, 0, 0x20)})
                    self.assertFalse(wait.active)
                    run.frame += 460  # passes the 1.2-second startup and six-second active phase
                    run.match.update(run.frame * combat.TICK, run.frame)
                else:
                    run.step({})
                self.assertTrue(wait.active)
                self.assertEqual(self.charge(soldier), 0)

                body_deadline = (wait.start + 1000) // script.frame_ms
                self.assertIsNotNone(script.world_frame_anchor)
                anchor_tick, anchor_frame = script.world_frame_anchor
                world_deadline = anchor_tick + body_deadline - anchor_frame + 1
                self.assertGreater(world_deadline, run.frame)
                run.match.update((world_deadline - 1) * combat.TICK, world_deadline - 1)
                self.assertEqual(self.charge(soldier), 0)
                run.match.update(world_deadline * combat.TICK, world_deadline)
                self.assertEqual(self.charge(soldier), 5)
                run.match.update(world_deadline * combat.TICK, world_deadline)
                self.assertEqual(self.charge(soldier), 5)
                yaw, pitch = aim(run.match.combat._eye(soldier), (0, .8, 1))
                soldier.take_commands([Command(world_deadline, yaw=yaw, pitch=pitch, buttons=1)])
                self.assertEqual(self.vitals(run, enemy).total, 180)
                self.assertEqual(self.charge(soldier), 25)

    def test_practice_visor_blocks_passive_until_a_new_wait_after_expiry(self):
        run, soldier, _ = self.arena()
        script = soldier.body_script
        wait = ultimate.instance_of(script).states[1]
        script.set_ult_charge(script.component.entity_var(338).value())
        run.step({soldier: (0, 0, 32)})
        self.assertFalse(wait.active)
        self.assertEqual(self.charge(soldier), 0)
        for _ in range(500):
            run.step({})
            self.assertEqual(self.charge(soldier), 0)
            if wait.active:
                break
        self.assertTrue(wait.active)
        deadline = wait.start + 1000
        while (run.frame + 1) * script.frame_ms + script.frame_ms - 1 < deadline:
            run.step({})
            self.assertEqual(self.charge(soldier), 0)
        run.step({})
        self.assertEqual(self.charge(soldier), 5)

    def test_practice_passive_long_idle_world_gap_preserves_every_award(self):
        outcomes = []
        for jump in (False, True):
            with self.subTest(single_world_update=jump):
                run, soldier, _ = self.arena()
                script = soldier.body_script
                wait = ultimate.instance_of(script).states[1]
                run.step({})
                start = run.frame
                end = start + 641
                for tick in ((end,) if jump else range(start + 1, end + 1)):
                    run.match.update(tick * combat.TICK, tick)
                self.assertEqual(self.charge(soldier), 50)
                outcome = self.charge(soldier), wait.counter, wait.start, script.frame
                run.match.update(end * combat.TICK, end)
                self.assertEqual((self.charge(soldier), wait.counter, wait.start, script.frame), outcome)
                outcomes.append(outcome)
        self.assertEqual(len(outcomes), 2)
        self.assertEqual(*outcomes)

    def test_authored_practice_variant_keeps_its_mode_and_runs_passive_and_rifle_charge(self):
        variant = game_map(PRACTICE_RANGE.map_guid, PRACTICE_RANGE_VARIANT_MODE, team_sizes=(1, 1))
        self.assertIsNotNone(variant)
        self.assertEqual(variant.mode_guid, PRACTICE_RANGE_VARIANT_MODE)
        self.assertEqual({spawn.team for spawn in variant.spawns}, {0, 1})
        run = Run(variant, names=("Soldier", "Enemy"))
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        run.match.collision_world = flat_world(0)
        soldier, enemy = run.players
        soldier.mover.position = [0, 0, -3]
        enemy.mover.position = [0, 0, 1]
        for player in run.players:
            player.mover.body = None
            script = player.body_script
            self.assertEqual(script.world.mode, PRACTICE_RANGE_VARIANT_MODE)
            self.assertTrue(script.component.entity_var(2426).value())
            self.assertTrue(ultimate.instance_of(script).states[1].active)
            self.assertEqual(self.charge(player), 0)
        run.step({})
        run.frame += 64
        run.match.update(run.frame * combat.TICK, run.frame)
        self.assertEqual([self.charge(p) for p in run.players], [5, 5])
        yaw, pitch = aim(run.match.combat._eye(soldier), (0, .8, 1))
        run.step({soldier: (yaw, pitch, 1)})
        self.assertEqual(self.vitals(run, enemy).total, 180)
        self.assertEqual([self.charge(p) for p in run.players], [25, 5])

    def test_skirmish_startup_condition_distinguishes_unset_from_false_and_zero(self):
        mode = 0x0230000000000010
        self.assertEqual(graph.mode_scripts(mode)["graph"], "0x0580000000000D76")
        source = graph.graph(0x0D76)
        component = runtime.Component(GAME_MODE_ENTITY)
        instance = runtime.Instance(component, 1, source)
        switch = instance.state(source.node(72).state)
        self.assertFalse(switch.condition())
        variable = instance.find_var(graph.INSTANCE, 13155)
        for value in (False, 0, True):
            with self.subTest(value=value):
                variable.base = value
                self.assertTrue(switch.condition())
        variable.base = None
        self.assertFalse(switch.condition())

    def test_skirmish_entry_broadcasts_to_authored_charge_and_controller_receivers(self):
        allow, configured = 0x0240000000000694, 0x0240000000000695

        class BroadcastFixture(runtime.World):
            def __init__(self, recipients):
                super().__init__(mode=0x0230000000000010)
                self.recipients = {instance.component.entity: instance for instance in recipients}
                self.messages = []

            def native(self, cfg, instance):
                if instance.graph.index == 0x0D76 and cfg == {
                    "$": "STU_1D719351", "m_6A8DB059": 1, "m_1199BFE0": 1, "m_111E4A0E": 0,
                }:
                    return tuple(expr.Entity(entity) for entity in self.recipients)
                return super().native(cfg, instance)

            def send_message(self, target, message, params, sender):
                self.messages.append((target, message))
                if message in (allow, configured):
                    instance = self.recipients[target]
                    runtime.send_instance_message(instance, message, params, sender)
                    instance.drain()

        for value, initially_allowed in ((None, False), (False, False), (0, False), (False, True)):
            with self.subTest(startup_value=value, initially_allowed=initially_allowed):
                run, soldier, enemy = self.arena()
                script = soldier.body_script
                body_receiver = next(
                    i for i in script.component.instances.values() if i.graph.index == 0x01C7
                )
                permission = body_receiver.states[body_receiver.graph.node(77).state]
                if not initially_allowed:
                    permission.end(False)
                    script.component.run_now()
                self.assertEqual(bool(script.component.entity_var(2426).value()), initially_allowed)

                controller_component = runtime.Component(soldier.entity)
                controller = controller_component.create(graph.graph(0x0C90), init=False)
                # Start only the authored listeners for the selected broadcasts.
                for receiver in (28, 75):
                    controller.state(controller.graph.node(receiver).state).begin()
                controller.drain()

                world = BroadcastFixture((body_receiver, controller))
                component = runtime.Component(GAME_MODE_ENTITY, world=world)
                mode = component.create(graph.graph(0x0D76), init=False)
                if value is not None:
                    runtime.send_instance_message(mode, 0x02400000000009A3, {0x8D: value}, soldier.entity)
                    mode.drain()
                    self.assertIs(mode.find_var(graph.INSTANCE, 13155).value(), value)
                mode.run_node(mode.graph.node(36), None)
                mode.drain()
                script.component.run_now()

                expected = allow if value is None else configured
                self.assertEqual(
                    [(target, msg) for target, msg in world.messages if msg in (allow, configured)],
                    [(soldier.body, expected), (soldier.entity, expected)],
                )
                permitted = initially_allowed or value is None
                self.assertEqual(bool(script.component.entity_var(2426).value()), permitted)
                marker = controller.find_var(graph.INSTANCE, 19526, create=False)
                self.assertEqual(bool(marker and marker.value()), value is not None)
                run.step({})
                run.frame += 64
                run.match.update(run.frame * combat.TICK, run.frame)
                self.assertEqual(self.charge(soldier), 5 if permitted else 0)
                yaw, pitch = aim(run.match.combat._eye(soldier), (0, .8, 1))
                run.step({soldier: (yaw, pitch, 1)})
                self.assertEqual(self.vitals(run, enemy).total, 180)
                self.assertEqual(self.charge(soldier), 25 if permitted else 0)

    def test_skirmish_configuration_action_uses_base_output_in_strict_vm(self):
        source = graph.graph(0x0D76)
        node = source.node(84)
        component = runtime.Component(GAME_MODE_ENTITY, strict=True)
        instance = component.create(source, init=False)
        with (
            patch.object(instance, "evaluate", side_effect=AssertionError("configuration read")),
            patch.object(instance, "follow") as follow,
        ):
            self.assertTrue(instance.run_node(node, None))
        follow.assert_called_once_with(node, "m_outPlug", None)

    def test_skirmish_persistent_broadcasts_match_receivers_by_channel(self):
        run, soldier, enemy = self.arena()
        combat_component = soldier.body_script.component
        practice_receiver = next(
            instance for instance in combat_component.instances.values() if instance.graph.index == 0x01C7
        )
        with combat_component.operation():
            practice_receiver.state(practice_receiver.graph.node(77).state).end(False)
        combat_component.run_now()

        world = scene.SceneWorld(mode=0x0230000000000010)
        world.map_guid = 0x08000000000000D4
        host = message_state.Host(notify=message_receiver.notify)

        def mount(entity, graph_id, *, receiver_channel=16):
            world.bind(entity)
            component = runtime.Component(entity, world=world, strict=True)
            world.bind_script_component(entity, component)
            context = message_state.Context(
                component, host,
                policy=lambda state: message_state.Policy(True, True, False, False, world_a=True),
                frame=lambda state: 10,
                priority=lambda state, resource: None,
                receiver_policy=lambda state: message_receiver.Policy(
                    mode=True, instance_flag3=receiver_channel != 2, state_mode=False, local=False,
                    world_a=receiver_channel == 16, world_b=receiver_channel == 32, authoritative=True,
                    instance_flag19=False, skip_application=False, deferred_begin=False,
                ),
                receiver_generation=lambda state: 17,
            )
            world.set_message_state_context(entity, context)
            instance = component.create(graph.graph(graph_id), init=False)
            instance.initialized = True
            return instance

        def begin(instance, position):
            state = instance.state(instance.graph.node(position).state)
            with instance.component.operation():
                state.begin()
            return state

        def tick_and_shoot(passive, damage):
            before = self.charge(soldier)
            for _ in range(64):
                self.input(run, soldier)
            self.assertEqual(self.charge(soldier), before + passive)
            health = self.vitals(run, enemy).total
            yaw, pitch = aim(run.match.combat._eye(soldier), (0, .8, 1))
            self.input(run, soldier, 1, yaw, pitch)
            self.assertEqual(self.vitals(run, enemy).total, health - 20)
            self.assertEqual(self.charge(soldier), before + passive + damage)

        mode = mount(GAME_MODE_ENTITY, 0x0D76)
        body = mount(soldier.body, 0x01C7)
        # The two scoped VMs share only the body permission slot.
        body.component.vars[2426] = combat_component.entity_var(2426)
        controller = mount(soldier.entity, 0x0C90)
        other_world = mount(0xA0000202, 0x01C7, receiver_channel=32)
        secondary_body = mount(0xA0000203, 0x01C7, receiver_channel=2)
        secondary_controller = mount(0xA0000204, 0x0C90, receiver_channel=2)
        world.set_subgraph_scopes(GAME_MODE_ENTITY, None)
        self.enterContext(patch.object(world, "tagged_entity_world_available", return_value=True))
        self.enterContext(patch.object(world, "tagged_entity_candidates", return_value=()))
        with mode.component.operation():
            mode.run_node(mode.graph.node(36), None)
        mode.component.run_now()
        allow = mode.state(mode.graph.node(2).state)
        configured = mode.state(mode.graph.node(85).state)
        self.assertTrue(allow.active)
        self.assertFalse(configured.active)
        self.assertEqual([(key.entity, key.flags, key.message) for key in allow.message_keys],
                         [(0, 1, 0x694), (0, 16, 0x694)])
        receiver = begin(body, 75)
        other_receiver = begin(other_world, 75)
        controller_receiver = begin(controller, 28)
        secondary_body_receiver = begin(secondary_body, 75)
        secondary_controller_receiver = begin(secondary_controller, 28)
        self.assertIs(receiver.message_selected, allow)
        self.assertIsNone(controller_receiver.message_selected)
        self.assertIsNone(other_receiver.message_selected)
        self.assertIsNone(secondary_body_receiver.message_selected)
        self.assertIsNone(secondary_controller_receiver.message_selected)
        for recipient in (body, other_world, controller, secondary_body, secondary_controller):
            recipient.component.run_now()
        self.assertIs(body.component.entity_var(2426).value(), True)
        self.assertFalse(other_world.component.entity_var(2426).value())
        self.assertFalse(secondary_body.component.entity_var(2426).value())
        tick_and_shoot(5, 20)

        condition = mode.graph.node(72).field("m_condition")
        configuration = expr.dynamic(condition["m_value"])
        sender = body.component.entity
        packet = message_receive.Packet(0x02400000000009A3, sender, sender, ((0x0A4000000000008D, (False,)),))
        message_receive.deliver(mode.component, packet)
        mode.component.run_now()
        self.assertEqual(mode.find_var(*configuration).value(), (False,))
        for recipient in (body, controller, secondary_controller):
            recipient.component.run_now()
        self.assertFalse(allow.active)
        self.assertTrue(configured.active)
        self.assertEqual([(key.entity, key.flags, key.message) for key in configured.message_keys],
                         [(0, 1, 0x695), (0, 2, 0x695), (0, 16, 0x695)])
        self.assertFalse(body.component.entity_var(2426).value())
        self.assertIs(controller_receiver.message_selected, configured)
        self.assertIs(secondary_controller_receiver.message_selected, configured)
        self.assertIs(controller.find_var(graph.INSTANCE, 19526).value(), True)
        self.assertIs(secondary_controller.find_var(graph.INSTANCE, 19526).value(), True)
        tick_and_shoot(0, 0)

        mode.write(configuration, None)
        mode.component.run_now()
        for recipient in (body, controller, secondary_controller):
            recipient.component.run_now()
        self.assertTrue(allow.active)
        self.assertFalse(configured.active)
        self.assertIs(body.component.entity_var(2426).value(), True)
        self.assertIsNone(controller_receiver.message_selected)
        self.assertIsNone(secondary_controller_receiver.message_selected)
        tick_and_shoot(5, 20)

        # Keep one Field active while the strict receiver changes the charge gate.
        soldier.body_script.ult_seconds = 0
        run.frame = soldier.body_script.frame
        reverse = aim(run.match.combat._eye(enemy), (0, .8, -3))
        for phase, permitted in enumerate((True, False, True)):
            with self.subTest(field_permission=permitted, phase=phase):
                if not permitted:
                    message_receive.deliver(mode.component, packet)
                elif phase:
                    mode.write(configuration, None)
                mode.component.run_now()
                body.component.run_now()
                self.assertEqual(bool(combat_component.entity_var(2426).value()), permitted)
                before = self.charge(soldier)
                run.step({enemy: (*reverse, 1)})
                self.assertEqual(self.vitals(run, soldier).total, 180)
                if phase == 0:
                    run.step({soldier: (0, 0, 0x10)})
                for _ in range(180):
                    if self.vitals(run, soldier).total == 200:
                        break
                    run.step({})
                self.assertEqual(self.vitals(run, soldier).total, 200)
                self.assertAlmostEqual(self.charge(soldier) - before, 20 if permitted else 0, delta=0.001)

    def test_skirmish_map_query_uses_authored_header_membership(self):
        source = graph.graph(0x0D76)
        cfg = source.node(44).field("m_condition")
        special = {**cfg, "m_gamemode": {**cfg["m_gamemode"], "m_gamemode": "0x0230000000000045"}}
        world = scene.SceneWorld(mode=0x0230000000000010)
        instance = runtime.Component(GAME_MODE_ENTITY, world=world, strict=True).create(source, init=False)
        for map_guid, expected in ((0x08000000000000D4, False), (0x08000000000004B7, True)):
            with self.subTest(map_guid=map_guid):
                world.map_guid = map_guid
                self.assertIs(instance.evaluate(cfg), expected)
                self.assertIs(instance.evaluate({**cfg, "m_gamemode": None}), False)
                self.assertIs(instance.evaluate(special), True)

    def test_skirmish_targeted_sender_registers_authored_entities_in_world_a(self):
        world = scene.SceneWorld(mode=0x0230000000000010)
        world.bind(GAME_MODE_ENTITY)
        component = runtime.Component(GAME_MODE_ENTITY, world=world, strict=True)
        world.bind_script_component(GAME_MODE_ENTITY, component)
        host = message_state.Host(notify=message_receiver.notify)
        world.set_message_state_context(GAME_MODE_ENTITY, message_state.Context(
            component, host,
            policy=lambda state: message_state.Policy(
                mode=True, instance_flag3=False, state_mode=False, local=False, world_a=True,
            ),
            frame=lambda state: 10,
            priority=lambda state, resource: None,
        ))
        source = graph.graph(0x0D76)
        instance = component.create(source, init=False)
        sender = instance.state(source.node(43).state)
        targets = sender.node.field("m_target")["m_targets"]["m_configVars"]
        first, second = 0xA0000101, 0xA0000102
        with component.operation():
            instance.write(expr.dynamic(targets[0]), expr.Entity(first))
            instance.write(expr.dynamic(targets[1]), expr.Entity(second))
            sender.begin()
        self.assertEqual(
            [(key.entity, key.flags, key.message) for key in sender.message_keys],
            [(first, 16, 0x702), (second, 16, 0x702)],
        )
        with component.operation():
            sender.end(False)
        self.assertEqual(sender.message_keys, [])

    def test_skirmish_child_list_state_uses_recent_indexes_and_resets_on_begin(self):
        source = graph.graph(0x0C8F)
        world = scene.SceneWorld(mode=0x0230000000000010)
        world.bind(GAME_MODE_ENTITY)
        component = runtime.Component(GAME_MODE_ENTITY, world=world, strict=True)
        world.bind_script_component(GAME_MODE_ENTITY, component)
        instance = component.create(source, init=False)
        node = source.node(7)
        state = instance.state(node.state)
        input_var = expr.dynamic(node.field("m_814F8EC3"))
        output = instance.find_var(*expr.dynamic(node.field("m_C51AF683")))
        assets = tuple(expr.Asset(0x02E0000000000000 | key) for key in (0x6E, 0xA, 0xB))
        with (
            patch.object(world, "list_selection_random", return_value=2) as draw,
            patch.object(instance, "run_node", wraps=instance.run_node) as run_node,component.operation()
        ):
            instance.write(input_var, assets)
            state.plug_enter("m_F4DD996D")
            draw.assert_not_called()
            instance.run_node(source.node(0), None)
            instance.drain()
            self.assertTrue(state.active)
            draw.assert_not_called()
            for expected in (assets[2], assets[0], assets[1], assets[2]):
                state.plug_enter("m_F4DD996D")
                self.assertEqual(output.value(), expected)
            self.assertTrue(any(call.args[0] is source.node(5) for call in run_node.call_args_list))

            state.end(False)
            state.begin()
            self.assertEqual(state.recent, [])
            instance.write(input_var, ())
            state.plug_enter("m_F4DD996D")
            self.assertEqual(output.value(), 0)
            self.assertEqual(draw.call_count, 4)

    def test_skirmish_child_tag_state_refreshes_live_matches_until_end(self):
        source = graph.graph(0x0C8F)
        world = runtime.World()
        component = runtime.Component(GAME_MODE_ENTITY, world=world, strict=True)
        instance = component.create(source, init=False)
        instance.initialized = True
        node = source.node(2)
        state = instance.state(node.state)
        output = instance.find_var(*expr.dynamic(node.field("m_4AEEE6A1")))
        previous = (expr.Entity(0xA0000100),)
        output.base = previous
        any_tags = (graph.ENTITY, 8364)
        all_tags = (graph.INSTANCE, 11016)
        tag_a, tag_b, required = (expr.Asset(0x0D60000000000000 | k) for k in (0x30D, 0x3CE, 0x520))
        candidate = entity_queries.TaggedCandidate
        candidates = (
            candidate(0xA0000104, frozenset({0x3CE, 0x520})),
            candidate(0xA0000101, frozenset({0x30D, 0x520})),
            candidate(0xA0000103, frozenset({0x3CE})),
        )
        with (
            patch.object(world, "tagged_entity_world_available", return_value=False) as present,
            patch.object(world, "tagged_entity_candidates", return_value=candidates) as enumerate_tags,
        ):
            with component.operation():
                instance.write(any_tags, (tag_a, tag_b))
                instance.write(all_tags, required)
                state.begin()
            self.assertEqual(output.value(), previous)
            enumerate_tags.assert_not_called()

            present.return_value = True
            component.now = 1
            component.update()
            self.assertEqual(output.value(), (expr.Entity(0xA0000104),))
            self.assertIn(instance.find_var(*any_tags), state.watched)

            with component.operation():
                instance.write(any_tags, (tag_b, tag_a))
            component.update()
            self.assertEqual(output.value(), (expr.Entity(0xA0000101),))

            with component.operation():
                state.end(False)
            enumerate_tags.reset_mock()
            component.now = 2
            component.update()
            enumerate_tags.assert_not_called()
            self.assertEqual(output.value(), (expr.Entity(0xA0000101),))

    def test_skirmish_tag_action_writes_ordered_entities_from_declared_candidates(self):
        source = graph.graph(0x0D76)
        node = source.node(62)
        world = runtime.World()
        component = runtime.Component(GAME_MODE_ENTITY, world=world, strict=True)
        instance = component.create(source, init=False)
        output = instance.find_var(*expr.dynamic(node.field("m_4AEEE6A1")))
        output.base = (expr.Entity(0xA0000100),)
        candidate = entity_queries.TaggedCandidate
        candidates = (
            candidate(0xA0000104, frozenset({0x03CE})),
            candidate(0xA0000102, frozenset({0x030D}), suppressed=True),
            candidate(0xA0000101, frozenset({0x030D})),
            candidate(0xA0000103, frozenset({0x0400})),
        )
        with patch.object(world, "tagged_entity_candidates", side_effect=[candidates, ()]):
            with component.operation():
                instance.run_node(node, None)
            self.assertEqual(output.value(), (expr.Entity(0xA0000104), expr.Entity(0xA0000101)))
            self.assertTrue(any(event.state is not None and event.state.node.pos == 43
                                for event in instance.queue))
            with component.operation():
                instance.run_node(node, None)
            self.assertEqual(output.value(), ())

        with (
            self.assertRaisesRegex(runtime.UnsupportedScriptError, "tag query enumeration"),
            component.operation(),
        ):
            instance.run_node(node, None)

    def test_practice_passive_earns_points_during_actual_sleep_and_combat_resumes_after_wake(self):
        outcomes = []
        for jump in (False, True):
            with self.subTest(world_gaps=jump):
                run, soldier, ana = self.arena()
                self.assertTrue(run.match.switch_hero(ana, heroes()[0x02E000000000013B]))
                ana.mover.position = [0, 0, 1]
                ana.mover.body = None
                ana.body_script.ult_seconds = 0
                fight = run.match.combat
                script = soldier.body_script
                wait = ultimate.instance_of(script).states[1]
                run.step({})
                yaw, pitch = aim(fight._eye(ana), (0, .8, -3))
                run.step({ana: (yaw, pitch, 8)})
                for _ in range(45):
                    sleep = fight.sleeps.get(soldier.body)
                    if sleep is not None:
                        break
                    run.step({ana: (yaw, pitch, 0)})
                self.assertIsNotNone(sleep)
                self.assertEqual(self.vitals(run, soldier).total, 195)
                self.assertTrue(wait.active)
                self.assertEqual(self.charge(soldier), 0)

                aim_at_ana = aim(fight._eye(soldier), (0, .8, 1))
                ammo = script.component.instances[11].vars[53]
                rounds = ammo.value()
                run.step({soldier: (*aim_at_ana, 1)})
                self.assertEqual(ammo.value(), rounds)
                self.assertEqual(self.vitals(run, ana).total, 200)
                anchor_tick, anchor_frame = script.world_frame_anchor
                first_award = anchor_tick + (wait.start + 1000) // script.frame_ms - anchor_frame + 1
                self.assertLess(run.frame, first_award)
                for checkpoint in (first_award - 1, first_award):
                    while run.frame < checkpoint:
                        run.frame = checkpoint if jump else run.frame + 1
                        run.match.update(run.frame * combat.TICK, run.frame)
                    self.assertTrue(fight.is_controlled(soldier))
                    self.assertEqual(self.charge(soldier), 0 if checkpoint < first_award else 5)

                while run.frame < sleep.end:
                    run.frame = sleep.end if jump else run.frame + 1
                    run.match.update(run.frame * combat.TICK, run.frame)
                self.assertFalse(fight.is_controlled(soldier))
                before = self.charge(soldier)
                self.assertEqual(before, 25)
                run.step({soldier: (*aim_at_ana, 1)})
                self.assertEqual(ammo.value(), rounds - 1)
                self.assertEqual(self.vitals(run, ana).total, 180)
                self.assertEqual(self.charge(soldier), before + 20)
                outcomes.append(before)
        self.assertEqual(len(outcomes), 2)
        self.assertEqual(*outcomes)

    def test_practice_death_cancels_pending_wait_and_replacement_starts_fresh(self):
        run, soldier, other = self.arena()
        script = soldier.body_script
        wait = ultimate.instance_of(script).states[1]
        pool = self.vitals(run, soldier)
        pool.damage(180)  # injury setup; the lethal hit comes from the enemy rifle
        for _ in range(55):
            run.step({})
        yaw, pitch = aim(run.match.combat._eye(other), (0, .8, -3))
        run.step({other: (yaw, pitch, 1)})
        self.assertTrue(run.match.combat.is_dead(soldier))
        self.assertFalse(wait.active)
        for _ in range(75):
            run.step({})
            self.assertEqual(self.charge(soldier), 0)
        self.assertTrue(run.match.switch_hero(soldier, soldier.hero))
        self.assertTrue(script.retired)
        self.assertEqual(self.charge(soldier), 0)
        self.assertTrue(ultimate.instance_of(soldier.body_script).states[1].active)
        script.command(script.frame + 125, 0)
        self.assertEqual(script.component.entity_var(342).value(), 0)


if __name__ == "__main__":
    unittest.main()
