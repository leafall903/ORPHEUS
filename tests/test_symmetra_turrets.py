"""Authored launch input and real world/pool/aura/movement execution for sentries."""

import math
import os
import unittest
from unittest.mock import patch

from test_game_combat import aim
from test_symmetra_combat import arena, fire, health

from ow174.game import combat, deflection, npc, symmetra_turrets
from ow174.game.collision import World
from ow174.game.commands import Command
from ow174.game.content import SOLDIER, heroes
from ow174.game.script import graph


def wall(blocker=False):
    triangles = [(-10, -5, 3, 10, -5, 3, 10, 8, 3),
                 (-10, -5, 3, 10, 8, 3, -10, 8, 3)]
    if blocker:
        triangles += [(1, -5, -2, 1, 8, -2, 1, 8, 2.9),
                      (1, -5, -2, 1, 8, 2.9, 1, -5, 2.9)]
    return World(triangles)


def check_sentry_crosses_live_defense(test, run, owner, defender, step, active):
    fight = run.match.combat
    weapon = next(i for i in owner.body_script.component.instances.values()
                  if i.graph.index == symmetra_turrets.LAUNCH_GRAPH)
    charges = weapon.find_var(graph.INSTANCE, 11)
    test.assertEqual(charges.value(), 3)
    cover = World([(-10, 0, 9, 10, 0, 9, 10, 8, 9),
                   (-10, 0, 9, 10, 8, 9, -10, 8, 9)])
    before = health(run, defender).total
    owner_before = health(run, owner).total
    with patch.object(fight, "_collision", return_value=cover):
        for _ in range(40):
            step(8)
            if fight.sentries.sentries:
                break
        [sentry] = fight.sentries.sentries
        test.assertIsNone(sentry.attached_at)
        state = active()
        test.assertIsNotNone(state)
        target = next(target for target in fight._targets() if target.owner is defender)
        contact = deflection.box_contact(target, state, sentry.bot.position, sentry.direction, 8)
        test.assertIsNotNone(contact)
        test.assertLess(contact, 8)
        launch = weapon.state(12)
        test.assertFalse(combat.hit_def(launch, launch.node.field("m_6396149F")).deflectable)
        test.assertEqual(sentry.bot.body, symmetra_turrets.BODY)
        for _ in range(45):
            step()
            test.assertIsNotNone(active())
            if sentry.attached_at is not None:
                break
        test.assertIsNotNone(sentry.attached_at)
        test.assertIs(sentry.owner, owner)
        test.assertEqual(sentry.bot.team, owner.team)
        test.assertAlmostEqual(sentry.bot.position[2], 8.99)
        test.assertEqual(health(run, sentry.bot).total, 30)
        test.assertEqual(health(run, defender).total, before)
        test.assertEqual(charges.value(), 2)
        for _ in range(75):
            step()
        test.assertEqual(fight.sentries.sentries, [sentry])
        test.assertEqual(sentry.target, defender.body)
        test.assertIsNotNone(sentry.slow)
        test.assertLess(health(run, defender).total, before)
        test.assertEqual(health(run, owner).total, owner_before)


@unittest.skipUnless(os.environ.get("ORPHEUS_EXTRA_GRAPH_BUNDLES"), "requires explicit summon graph shard")
class SentryTests(unittest.TestCase):
    def setup_arena(self, *, distance=2):
        run, owner, enemy = arena()
        enemy.mover.position = [distance, 1, 1]
        return run, owner, enemy

    def sentries(self, run):
        return run.match.combat.sentries.sentries

    def launch(self, run, owner):
        fire(run, owner, 40, buttons=8)
        fire(run, owner, buttons=0)

    def test_launch_attach_boot_damage_slow_and_real_movement(self):
        run, owner, enemy = self.setup_arena()
        fight = run.match.combat
        with patch.object(fight, "_collision", return_value=wall()):
            self.launch(run, owner)
            [sentry] = self.sentries(run)
            self.assertIsNotNone(sentry.attached_at)
            self.assertEqual(health(run, enemy).total, 200)
            self.assertEqual(health(run, sentry.bot).total, 30)
            fire(run, owner, 80, buttons=0)
            duration = fight.now - (sentry.attached_at + .5)
            self.assertAlmostEqual(200 - health(run, enemy).total, 40 * duration, places=4)
            self.assertEqual(sentry.target, enemy.body)
            self.assertAlmostEqual(enemy.body_script.component.entity_var(23186).value(), .2, places=5)
            old = tuple(enemy.mover.position)
            enemy.take_commands([Command(run.frame + 1, forward=127)])
            self.assertGreater(enemy.mover.position[2], old[2])
            self.assertAlmostEqual(enemy.mover.values.run_forward, 5.5 * .8, places=4)

    def test_actual_sentry_damage_credits_its_living_owner(self):
        run, owner, enemy = self.setup_arena()
        component = owner.body_script.component
        component.set(12918, 0)
        run.match.collision_world = wall()
        self.launch(run, owner)
        self.assertEqual(component.entity_var(342).value(), 0)
        fire(run, owner, 80, buttons=0)
        loss = 200 - health(run, enemy).total
        self.assertGreater(loss, 0)
        self.assertAlmostEqual(component.entity_var(342).value(), loss, places=4)

    def test_three_beams_stack_slow_and_each_retirement_removes_only_its_effect(self):
        run, owner, enemy = self.setup_arena()
        fight = run.match.combat
        with patch.object(fight, "_collision", return_value=wall()):
            for _ in range(3):
                self.launch(run, owner)
            fire(run, owner, 30, buttons=0)
            self.assertEqual(len(self.sentries(run)), 3)
            self.assertAlmostEqual(enemy.body_script.component.entity_var(23186).value(), .6, places=5)
            enemy.take_commands([Command(run.frame + 1, forward=127)])
            self.assertAlmostEqual(enemy.mover.values.run_forward, 5.5 * .4, places=4)
            fight.sentries._retire(self.sentries(run)[0])
            self.assertAlmostEqual(enemy.body_script.component.entity_var(23186).value(), .4, places=5)
            enemy.take_commands([Command(run.frame + 2, forward=127)])
            # MovementMod changes are admitted after input and act next frame.
            self.assertAlmostEqual(enemy.mover.values.run_forward, 5.5 * .4, places=4)
            enemy.take_commands([Command(run.frame + 3, forward=127)])
            self.assertAlmostEqual(enemy.mover.values.run_forward, 5.5 * .6, places=4)

    def test_losing_line_of_sight_clears_slow_and_stops_damage(self):
        run, owner, enemy = self.setup_arena()
        fight = run.match.combat
        with patch.object(fight, "_collision", return_value=wall()):
            self.launch(run, owner)
            fire(run, owner, 80, buttons=0)
        before = health(run, enemy).total
        with patch.object(fight, "_collision", return_value=wall(True)):
            fire(run, owner, 4, buttons=0)
            self.assertEqual(health(run, enemy).total, before)
            self.assertFalse(enemy.body_script.component.entity_var(23186).value())

    def test_static_wall_and_range_prevent_beam_and_slow(self):
        for reason in ("wall", "range"):
            with self.subTest(reason=reason):
                run, owner, enemy = self.setup_arena(distance=30 if reason == "range" else 2)
                with patch.object(run.match.combat, "_collision", return_value=wall(reason == "wall")):
                    self.launch(run, owner)
                    fire(run, owner, 140, buttons=0)
                    self.assertEqual(health(run, enemy).total, 200)
                    self.assertIsNone(self.sentries(run)[0].slow)

    def test_allied_and_own_bodies_are_never_targeted(self):
        run, owner, ally = self.setup_arena()
        run.match.game_map = run.match.game_map.__class__(
            **{**run.match.game_map.__dict__, "free_for_all": False})
        ally.team = owner.team
        with patch.object(run.match.combat, "_collision", return_value=wall()):
            self.launch(run, owner)
            fire(run, owner, 140, buttons=0)
            self.assertEqual(health(run, ally).total, 200)
            self.assertEqual(health(run, owner).total, 225)
            self.assertEqual(health(run, self.sentries(run)[0].bot).total, 30)

    def test_three_charges_reject_fourth_until_authored_recovery_and_cap_owned_objects(self):
        run, owner, _enemy = self.setup_arena(distance=30)
        with patch.object(run.match.combat, "_collision", return_value=wall()):
            for _ in range(3):
                self.launch(run, owner)
            self.assertEqual(len(self.sentries(run)), 3)
            entities = {s.bot.entity for s in self.sentries(run)}
            self.launch(run, owner)
            self.assertEqual({s.bot.entity for s in self.sentries(run)}, entities)
            weapon = next(i for i in owner.body_script.component.instances.values()
                          if i.graph.index == 0x157E)
            self.assertEqual(weapon.find_var(graph.INSTANCE, 11).value(), 0)
            fire(run, owner, 650, buttons=0)
            self.launch(run, owner)
            self.assertEqual(len(self.sentries(run)), 3)
            self.assertNotEqual({s.bot.entity for s in self.sentries(run)}, entities)

    def test_enemy_primary_destroys_turret_and_removes_slow_without_respawn(self):
        run, owner, enemy = self.setup_arena()
        fight = run.match.combat
        with patch.object(fight, "_collision", return_value=wall()):
            self.launch(run, owner)
            fire(run, owner, 70, buttons=0)
            [sentry] = self.sentries(run)
            yaw, pitch = aim(fight._eye(enemy), tuple(v + (0.5 if n == 1 else 0)
                                                    for n, v in enumerate(sentry.bot.position)))
            for _ in range(15):
                run.step({enemy: (yaw, pitch, 1)})
            self.assertEqual(self.sentries(run), [])
            self.assertNotIn(sentry.bot, run.match.bots)
            self.assertFalse(any(who is sentry.bot for _, who in fight.respawns))
            self.assertFalse(enemy.body_script.component.entity_var(23186).value())

    def test_expired_short_press_during_empty_charges_does_not_launch_after_recovery(self):
        run, owner, _enemy = self.setup_arena(distance=30)
        with patch.object(run.match.combat, "_collision", return_value=wall()):
            for _ in range(3):
                self.launch(run, owner)
            weapon = next(i for i in owner.body_script.component.instances.values()
                          if i.graph.index == 0x157E)
            entities = {s.bot.entity for s in self.sentries(run)}
            self.assertEqual(weapon.find_var(graph.INSTANCE, 11).value(), 0)
            recovery = weapon.find_var(graph.INSTANCE, 61).value()
            self.assertGreater(recovery, 1)
            fire(run, owner, buttons=8)
            fire(run, owner, math.ceil(recovery / combat.TICK) + 40, buttons=0)
            self.assertEqual({s.bot.entity for s in self.sentries(run)}, entities)
            self.assertEqual(weapon.find_var(graph.INSTANCE, 11).value(), 1)
            self.launch(run, owner)
            self.assertNotEqual({s.bot.entity for s in self.sentries(run)}, entities)
            self.assertEqual(weapon.find_var(graph.INSTANCE, 11).value(), 0)

    def test_near_recovery_buffer_and_held_press_launch_when_a_charge_returns(self):
        for held in (False, True):
            with self.subTest(held=held):
                run, owner, _enemy = self.setup_arena(distance=30)
                with patch.object(run.match.combat, "_collision", return_value=wall()):
                    for _ in range(3):
                        self.launch(run, owner)
                    weapon = next(i for i in owner.body_script.component.instances.values()
                                  if i.graph.index == 0x157E)
                    if not held:
                        for _ in range(650):
                            if weapon.find_var(graph.INSTANCE, 61).value() <= .08:
                                break
                            fire(run, owner, buttons=0)
                        else:
                            self.fail("authored sentry recovery did not approach expiry")
                    recovery = weapon.find_var(graph.INSTANCE, 61).value()
                    self.assertGreater(recovery, 0)
                    entities = {s.bot.entity for s in self.sentries(run)}
                    fire(run, owner, buttons=8)
                    self.assertEqual({s.bot.entity for s in self.sentries(run)}, entities)
                    fire(run, owner, math.ceil(recovery / combat.TICK) + 40,
                         buttons=8 if held else 0)
                    self.assertNotEqual({s.bot.entity for s in self.sentries(run)}, entities)
                    self.assertEqual(weapon.find_var(graph.INSTANCE, 11).value(), 0)

    def test_owner_death_keeps_sentry_but_hero_switch_removes_it(self):
        run, owner, enemy = self.setup_arena()
        fight = run.match.combat
        with patch.object(fight, "_collision", return_value=wall()):
            self.launch(run, owner)
            target = next(t for t in fight._targets() if t.owner is owner)
            fight._damage(enemy, target, 1000, False, None, run.frame)
            fire(run, owner, 50, buttons=0)
            self.assertEqual(len(self.sentries(run)), 1)
            self.assertLess(health(run, enemy).total, 200)
            run.match.switch_hero(owner, heroes()[SOLDIER])
            self.assertEqual(self.sentries(run), [])
            self.assertFalse(enemy.body_script.component.entity_var(23186).value())

    def test_duplicate_launch_input_does_not_create_or_spend_again_and_dead_owner_cannot_launch(self):
        run, owner, enemy = self.setup_arena(distance=30)
        fight = run.match.combat
        with patch.object(fight, "_collision", return_value=wall()):
            self.launch(run, owner)
            [sentry] = self.sentries(run)
            weapon = next(i for i in owner.body_script.component.instances.values()
                          if i.graph.index == 0x157E)
            charges = weapon.find_var(graph.INSTANCE, 11).value()
            for _ in range(5):
                owner.take_commands([Command(run.frame, buttons=8)])
            self.assertEqual(self.sentries(run), [sentry])
            self.assertEqual(weapon.find_var(graph.INSTANCE, 11).value(), charges)
            target = next(t for t in fight._targets() if t.owner is owner)
            fight._damage(enemy, target, 1000, False, None, run.frame)
            self.launch(run, owner)
            self.assertEqual(self.sentries(run), [sentry])

    def test_manual_removal_does_not_remove_summon_and_owner_leave_does(self):
        run, owner, _enemy = self.setup_arena(distance=30)
        with patch.object(run.match.combat, "_collision", return_value=wall()):
            self.launch(run, owner)
            [sentry] = self.sentries(run)
            self.assertEqual(npc.remove_spawned(run.match), ())
            self.assertIn(sentry.bot, run.match.bots)
            run.match.leave(owner)
            self.assertEqual(self.sentries(run), [])
            self.assertNotIn(sentry.bot, run.match.bots)

    def test_short_gap_matches_contiguous_damage_repeated_tick_is_inert_and_long_gap_is_bounded(self):
        results = []
        for gap in (False, True):
            run, owner, enemy = self.setup_arena()
            fight = run.match.combat
            with patch.object(fight, "_collision", return_value=wall()):
                self.launch(run, owner)
                fire(run, owner, 50, buttons=0)
                before = health(run, enemy).total
                if gap:
                    fight.update(fight.now + .32, fight.tick + 20)
                else:
                    fire(run, owner, 20, buttons=0)
                loss = before - health(run, enemy).total
                self.assertAlmostEqual(loss, 40 * .32, places=4)
                fight.update(fight.now, fight.tick)
                self.assertAlmostEqual(before - health(run, enemy).total, loss, places=4)
                results.append(loss)
                before = health(run, enemy).total
                fight.update(fight.now + 3.2, fight.tick + 200)
                self.assertAlmostEqual(before - health(run, enemy).total,
                                       40 * combat.COMBAT_CATCH_UP * combat.TICK, places=4)
        self.assertAlmostEqual(results[0], results[1], places=4)


if __name__ == "__main__":
    unittest.main()
