"""Actual ultimate command -> physical charge/contact -> uppercut -> gun/end.

Static server reconstruction tests, not native contact/force or client acceptance.
"""

import math
import os
import unittest
from pathlib import Path
from unittest.mock import patch

import test_ashe_combat as fixtures
import test_ashe_pve_npcs as npc_fixtures
from test_ashe_impulses import wall_scene

from ow174.game import combat, immunity, mover, stats, world
from ow174.game.commands import Command
from ow174.game.content import SOLDIER, heroes
from ow174.game.mover import IN_AIR
from ow174.game.script import expr

EXTRA = Path(os.environ.get("ORPHEUS_MOVER_EXTENSION", "."))


@unittest.skipUnless(EXTRA.is_file(), "requires explicit external B.O.B. movement profiles")
class BobChargeTests(unittest.TestCase):
    fight = fixtures.AsheCombatTests.fight
    press = fixtures.AsheCombatTests.press

    def setup(self, *, distance=12, team_game=False):
        run, owner, enemy = self.fight(distance=distance, team_game=team_game)
        for player in run.players:
            player.client.queue_bot_death = lambda *args: None
            player.client.queue_bot_death_update = lambda *args: None
        run.match.combat.vitals[enemy.body] = combat.Vitals(enemy.body, 10000)
        owner.body_script.set_ult_charge(2240)
        return run, owner, enemy

    def begin(self, run, owner):
        self.press(run, owner, 32, frames=1)
        actor, = run.match.combat.companions.active.values()
        self.assertEqual(actor.phase, "charge")
        self.assertEqual(actor.charge_until - actor.born, 250)
        self.assertEqual(actor.mover.data.run_forward, 15)
        self.assertEqual(actor.mover.data.stand_radius, 0.7)
        return actor

    def stop(self, run, actor):
        while actor.phase == "charge" and run.frame <= actor.charge_until:
            run.step({})
        self.assertNotEqual(actor.phase, "charge")

    def test_enemy_contact_deals_one_uppercut_then_launches_and_fires(self):
        run, owner, enemy = self.setup()
        actor = self.begin(run, owner)
        for _ in range(10):
            run.step({})
        self.assertGreater(actor.bot.position[2], 3)
        self.assertEqual(run.match.combat.vitals[enemy.body].total, 10000)
        self.assertEqual(actor.shots, 0)
        fight = run.match.combat
        with patch.object(fight, "_damage", wraps=fight._damage) as damage:
            self.stop(run, actor)
        damage.assert_called_once()
        hit = damage.call_args
        self.assertIs(hit.args[0], actor.bot)
        self.assertEqual((hit.args[2], hit.args[4]), (120, 0x0B9000000000020C))
        self.assertEqual(hit.kwargs["hit_tags"],
                         (0x0D80000000004489, 0x0D80000000000644, 0x0D80000000004D2B))
        self.assertEqual(actor.stopped_reason, "enemy")
        self.assertEqual(actor.uppercut_target, enemy.body)
        impulse, = fight.impulses.pending[enemy.body]
        self.assertEqual(impulse.vector, (0, 15, 0.5))
        self.assertEqual(run.match.combat.vitals[enemy.body].total, 9880)
        self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 120)
        self.assertEqual(owner.body_script.component.entity_var(342).value(), 0)
        y = enemy.mover.position[1]
        for _ in range(5):
            run.step({})
        self.assertGreater(enemy.mover.position[1], y)
        self.assertGreater(enemy.mover.velocity[1], 0)
        self.assertEqual(run.match.combat.vitals[enemy.body].total, 9880)
        self.assertEqual(actor.mover.data.run_forward, 2.2)
        while run.frame < actor.ready + 30:
            run.step({})
        self.assertEqual(actor.phase, "gun")
        self.assertGreater(actor.shots, 0)
        self.assertLess(run.match.combat.vitals[enemy.body].total, 9880)

    def test_off_axis_uppercut_uses_the_authored_radial_launch(self):
        run, owner, enemy = self.setup()
        enemy.mover.position[0] = 0.5
        enemy.mover.body = None
        actor = self.begin(run, owner)
        self.stop(run, actor)
        self.assertEqual(actor.uppercut_target, enemy.body)

        impulse, = run.match.combat.impulses.pending[enemy.body]
        x, y, z = impulse.vector
        dx = enemy.mover.position[0] - actor.bot.position[0]
        dz = enemy.mover.position[2] - (actor.bot.position[2] - 2)
        self.assertGreater(x, 0)
        self.assertGreater(z, 0)
        self.assertAlmostEqual(x / z, dx / dz)
        self.assertAlmostEqual(math.hypot(x, z), 0.5)
        self.assertEqual(y, 15)
        self.assertEqual(impulse.due_tick, run.frame + 1)
        run.step({})
        self.assertGreater(enemy.mover.velocity[0], 0)
        self.assertGreater(enemy.mover.velocity[1], 0)

    def test_live_uppercut_resets_velocity_locks_throttle_and_exits_after_landing(self):
        run, owner, enemy = self.setup()
        actor = self.begin(run, owner)
        self.stop(run, actor)
        fight = run.match.combat
        component = enemy.body_script.component
        victim, = (item for item in component.instances.values() if item.graph.index == 0x17F6)
        self.assertEqual(victim.cached_source_entity, owner.body)
        self.assertEqual(victim.find_var(expr.INSTANCE, 0x1B).value(), expr.Entity(owner.body))
        self.assertTrue(victim.states[1].active)
        impulse, = fight.impulses.pending[enemy.body]
        self.assertTrue(impulse.reset_velocity)

        # A declared starting velocity makes the one-shot reset observable.
        enemy.mover.velocity = (2, 3, 4)
        apply = enemy.mover.add_impulse
        velocity_samples = []

        def record(vector):
            before = enemy.mover.velocity
            apply(vector)
            velocity_samples.append((before, enemy.mover.velocity))

        def move():
            run.frame += 1
            run.match.update(run.frame * combat.TICK, run.frame)
            owner.take_commands([Command(run.frame)])
            enemy.take_commands([Command(run.frame, forward=127, right=127)])

        with (patch.object(enemy.mover, "add_impulse", side_effect=record),
              patch.object(mover, "modified_throttles", wraps=mover.modified_throttles) as throttles):
            move()
        self.assertEqual(velocity_samples, [((0, 0, 0), tuple(mover.f32(axis) for axis in impulse.vector))])
        active, right, forward = throttles.call_args.args
        self.assertGreater(right, 0)
        self.assertGreater(forward, 0)
        self.assertEqual(mover.modified_throttles(active, right, forward), (0, 0))
        self.assertTrue(enemy.mover.airborne)

        for _ in range(12):
            move()
        self.assertIs(component.entity_var(17932, create=False).value(), True)
        for _ in range(160):
            move()
            if victim.id not in component.instances:
                break
        self.assertNotIn(victim.id, component.instances)
        self.assertFalse(component.entity_var(17932, create=False).value())
        self.assertFalse(enemy.mover.airborne)
        with patch.object(mover, "modified_throttles", wraps=mover.modified_throttles) as throttles:
            move()
        active, right, forward = throttles.call_args.args
        self.assertEqual(mover.modified_throttles(active, right, forward), (right, forward))

    def test_live_uppercut_inherits_the_summon_source(self):
        run, owner, enemy = self.setup()
        summon = next(item for item in owner.body_script.component.instances.values()
                      if item.graph.index == 0x1536)
        summon.set_source_entity(enemy.body)
        actor = self.begin(run, owner)
        self.assertEqual(actor.charge_instigator, enemy.body)
        summon.set_source_entity(owner.body)
        self.stop(run, actor)
        victim, = (item for item in enemy.body_script.component.instances.values()
                   if item.graph.index == 0x17F6)
        self.assertEqual(victim.source_entity(), enemy.body)

    def test_live_fade_cancels_the_uppercut_movement_branch_in_air(self):
        run, owner, enemy = self.setup()
        position = tuple(enemy.mover.position)
        self.assertTrue(run.match.switch_hero(enemy, heroes()[immunity.MOIRA]))
        enemy.mover.position[:] = position
        for _ in range(20):
            run.step({})
        actor = self.begin(run, owner)
        self.stop(run, actor)
        component = enemy.body_script.component
        victim, = (item for item in component.instances.values() if item.graph.index == 0x17F6)
        for _ in range(12):
            run.step({})
        self.assertTrue(victim.states[1].active)
        self.assertIs(component.entity_var(17932, create=False).value(), True)

        for _ in range(12):
            run.step({enemy: (0, 0, 8)})
            if immunity.untargetable(enemy, enemy.body):
                break
        self.assertTrue(immunity.untargetable(enemy, enemy.body))
        self.assertTrue(enemy.mover.airborne)
        self.assertFalse(victim.states[0].active)
        self.assertFalse(victim.states[1].active)
        self.assertIs(component.entity_var(17932, create=False).value(), True)

    def test_reinhardt_resists_uppercut_on_the_next_pose(self):
        run, owner, enemy = self.setup()
        position = tuple(enemy.mover.position)
        self.assertTrue(run.match.switch_hero(enemy, heroes()[0x02E0000000000007]))
        enemy.mover.position[:] = position
        for _ in range(20):
            run.step({})
        fight = run.match.combat
        target = next(item for item in fight._targets() if item.owner is enemy)
        before = fight.vitals_of(target).total
        actor = self.begin(run, owner)
        with patch.object(enemy.mover, "add_impulse", wraps=enemy.mover.add_impulse) as applied:
            self.stop(run, actor)
            impulse, = fight.impulses.pending[enemy.body]
            self.assertTrue(impulse.apply_resistance)
            applied.assert_not_called()
            run.step({})
        applied.assert_called_once()
        for actual, raw in zip(applied.call_args.args[0], impulse.vector, strict=True):
            self.assertAlmostEqual(actual, 0.7 * raw, places=5)
        self.assertEqual(before - fight.vitals[enemy.body].total, 115)

    def test_first_pose_is_next_tick_and_repeated_update_does_not_move_twice(self):
        run, owner, _enemy = self.setup(distance=150)
        actor = self.begin(run, owner)
        initial = actor.bot.position
        run.step({})
        self.assertEqual(actor.mover.last_command.frame, actor.born + 1)
        distance = actor.bot.position[2] - initial[2]
        self.assertGreater(distance, 0)
        self.assertLessEqual(distance, 15 * combat.TICK)
        posed = actor.bot.position
        run.match.combat.companions.update(run.frame)
        self.assertEqual(actor.bot.position, posed)

    def opposing_casts(self, reverse):
        run, first, second = self.fight(distance=18)
        self.assertTrue(run.match.switch_hero(second, first.hero))
        second.mover.position[:] = (0, first.mover.position[1], 18)
        second.mover.body = None
        for _ in range(20):
            run.step({})
        for player in (first, second):
            player.body_script.set_ult_charge(2240)
        if reverse:
            run.players.reverse()
        run.step({first: (0, 0, 32), second: (-32768, 0, 32)})
        active = run.match.combat.companions.active
        return run, active[first.body], active[second.body]

    def test_head_on_companion_charges_stop_without_uppercut_in_either_cast_order(self):
        for reverse in (False, True):
            with self.subTest(reverse=reverse):
                run, front, back = self.opposing_casts(reverse)
                fight = run.match.combat
                self.assertEqual(front.born, back.born)
                self.assertEqual(front.phase, "charge")
                self.assertEqual(back.phase, "charge")
                with patch.object(fight, "_damage", wraps=fight._damage) as damage:
                    for _ in range(64):
                        run.step({})
                        if front.phase != "charge" or back.phase != "charge":
                            break
                    for actor in (front, back):
                        self.assertEqual(actor.phase, "settle")
                        self.assertEqual(actor.stopped_reason, "opposing_charge")
                        self.assertIsNone(actor.uppercut_target)
                        self.assertEqual(fight.vitals[actor.bot.entity].total, 1200)
                        self.assertNotIn(actor.bot.entity, fight.knockbacks.active)
                        self.assertEqual(actor.owner.stats.value(stats.HERO_DAMAGE), 0)
                    self.assertEqual(front.ready, back.ready)
                    self.assertEqual(front.until, back.until)
                    for _ in range(12):
                        run.step({})
                    damage.assert_not_called()
                while run.frame < front.ready + 17:
                    run.step({})
                for actor, opponent in ((front, back), (back, front)):
                    self.assertEqual(actor.phase, "gun")
                    self.assertEqual(actor.target, opponent.bot.entity)
                    self.assertEqual(actor.shots, 3)
                    self.assertEqual(fight.vitals[actor.bot.entity].total, 1200 - 3 * 14)
                    self.assertEqual(actor.owner.stats.value(stats.HERO_DAMAGE), 3 * 14)

    def test_head_on_contact_and_pose_match_when_server_ticks_are_skipped(self):
        for reverse in (False, True):
            with self.subTest(reverse=reverse):
                results = []
                for skipped in (False, True):
                    run, front, back = self.opposing_casts(reverse)
                    fight = run.match.combat
                    if skipped:
                        run.frame += 42
                        run.match.update(run.frame * combat.TICK, run.frame)
                    else:
                        for _ in range(42):
                            run.step({})
                    poses = []
                    for actor in (front, back):
                        self.assertEqual(actor.stopped_reason, "opposing_charge")
                        self.assertEqual(actor.phase, "settle")
                        self.assertIsNone(actor.uppercut_target)
                        self.assertEqual(fight.vitals[actor.bot.entity].total, 1200)
                        self.assertEqual(actor.bot.moved_tick, run.frame)
                        poses.append((actor.ready - actor.born, actor.until - actor.born,
                                      actor.bot.position, actor.bot.velocity))
                    results.append(poses)
                self.assertEqual(results[0], results[1])

    def test_only_companion_with_missing_gun_vm_retires(self):
        for reverse in (False, True):
            with self.subTest(reverse=reverse):
                run, front, back = self.opposing_casts(reverse)
                fight = run.match.combat
                self.stop(run, front)
                self.assertEqual(front.ready, back.ready)
                failed, healthy = fight.companions.active.values()
                while run.frame < failed.ready - 1:
                    run.step({})
                # Model loss of the gun VM after both real casts and contacts.
                failed.bot.body_script = None
                run.step({})
                self.assertEqual(set(fight.companions.active), {healthy.owner_body})
                self.assertNotIn(failed.bot, run.match.bots)
                self.assertIn(healthy.bot, run.match.bots)
                self.assertEqual(healthy.phase, "gun")
                self.assertEqual(failed.owner.body_script.component.entity_var(512).value(), expr.Entity(0))
                self.assertEqual(healthy.owner.body_script.component.entity_var(512).value(),
                                 expr.Entity(healthy.bot.entity))

    def test_wall_stops_charge_and_excludes_enemy_behind_it(self):
        run, owner, enemy = self.setup(distance=8)
        run.match.collision_world = wall_scene(5)
        actor = self.begin(run, owner)
        self.stop(run, actor)
        self.assertEqual(actor.stopped_reason, "wall")
        self.assertLessEqual(actor.bot.position[2], 5 - 0.7 + 0.01)
        self.assertIsNone(actor.uppercut_target)
        for _ in range(100):
            run.step({})
        self.assertEqual(run.match.combat.vitals[enemy.body].total, 10000)
        self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 0)

    def test_four_second_charge_timeout_then_ten_second_gun_expiry(self):
        run, owner, _enemy = self.setup(distance=150)
        actor = self.begin(run, owner)
        while run.frame < actor.charge_until - 1:
            run.step({})
        self.assertEqual(actor.phase, "charge")
        self.assertGreater(actor.bot.position[2], 55)
        self.assertLess(actor.bot.position[2], 64)
        run.step({})
        self.assertEqual(actor.stopped_reason, "timeout")
        self.assertEqual(actor.until - actor.ready, math.ceil(10 / combat.TICK))
        self.assertIsNone(actor.uppercut_target)
        while run.frame < actor.until - 1:
            run.step({})
        self.assertIn(actor.bot, run.match.bots)
        run.step({})
        self.assertNotIn(actor.bot, run.match.bots)
        self.assertFalse(run.match.combat.companions.active)

    def test_ally_in_charge_path_takes_no_damage_or_launch(self):
        run, owner, ally = self.setup(distance=8, team_game=True)
        ally.team = owner.team
        actor = self.begin(run, owner)
        self.stop(run, actor)
        self.assertEqual(actor.stopped_reason, "timeout")
        self.assertIsNone(actor.uppercut_target)
        self.assertEqual(run.match.combat.vitals[ally.body].total, 10000)
        self.assertEqual(ally.mover.velocity, (0, 0, 0))
        self.assertEqual(owner.client.messages(combat.HIT), [])

    def test_charge_misses_enemy_outside_capsule_sweep(self):
        run, owner, enemy = self.setup(distance=8)
        enemy.mover.position[0] = 6
        enemy.mover.body = None  # explicit fixture placement before the tested cast
        actor = self.begin(run, owner)
        self.stop(run, actor)
        self.assertEqual(actor.stopped_reason, "timeout")
        self.assertIsNone(actor.uppercut_target)
        self.assertEqual(run.match.combat.vitals[enemy.body].total, 10000)

    def test_external_impulse_uses_same_companion_mover(self):
        run, owner, _enemy = self.setup(distance=150)
        actor = self.begin(run, owner)
        engine = run.match.combat.impulses
        engine.queue(actor.bot, (0, 12, 0))
        self.assertNotIn(actor.bot.entity, engine.npcs)
        for _ in range(8):
            run.step({})
        self.assertGreater(actor.bot.position[1], 1.5)
        self.assertTrue(actor.bot.movement(run.frame).flags & IN_AIR)
        self.assertEqual(actor.bot.velocity, actor.mover.velocity)
        self.assertNotIn(actor.bot.entity, engine.npcs)

    def test_charge_passes_through_active_wraith_and_fade(self):
        for hero, frames in ((immunity.REAPER, 50), (immunity.MOIRA, 35)):
            with self.subTest(hero=hex(hero)):
                run, owner, defender = self.setup(distance=8)
                self.assertTrue(run.match.switch_hero(defender, heroes()[hero]))
                defender.mover.position[:] = (0, 1, 8)
                defender.mover.body = None
                fight = run.match.combat
                target = next(target for target in fight._targets() if target.owner is defender)
                vitals = fight.vitals_of(target)
                before = vitals.total
                run.step({defender: (0, 0, 8)})
                self.assertTrue(immunity.untargetable(defender, defender.body))
                actor = self.begin(run, owner)
                for _ in range(frames):
                    run.step({})
                self.assertTrue(immunity.untargetable(defender, defender.body))
                self.assertEqual(actor.phase, "charge")
                self.assertGreater(actor.bot.position[2], defender.mover.position[2] + 1)
                self.assertIsNone(actor.uppercut_target)
                self.assertEqual(vitals.total, before)
                self.assertEqual(defender.mover.velocity, (0, 0, 0))
                self.assertEqual(owner.client.messages(combat.HIT), [])

    def test_cryo_freeze_stops_charge_without_uppercut(self):
        for cancel in (False, True):
            with self.subTest(cancel=cancel):
                run, owner, mei = self.setup(distance=8)
                self.assertTrue(run.match.switch_hero(mei, heroes()[immunity.MEI]))
                mei.mover.position[:] = (0, 1, 8)
                mei.mover.body = None
                fight = run.match.combat
                target = next(target for target in fight._targets() if target.owner is mei)
                vitals = fight.vitals_of(target)
                before = vitals.total
                run.step({mei: (0, 0, 8)})
                self.assertTrue(immunity.cryo_active(mei, mei.body))
                for _ in range(65):
                    run.step({})
                if cancel:
                    run.step({mei: (0, 0, 2)})
                self.assertEqual(immunity.cryo_active(mei, mei.body), not cancel)
                actor = self.begin(run, owner)
                self.stop(run, actor)
                self.assertEqual(actor.stopped_reason, "enemy")
                self.assertEqual(immunity.cryo_active(mei, mei.body), not cancel)
                self.assertEqual(actor.uppercut_target, mei.body if cancel else None)
                self.assertEqual(vitals.total, before - (120 if cancel else 0))
                self.assertEqual(len(owner.client.messages(combat.HIT)), int(cancel))
                for _ in range(5):
                    run.step({})
                self.assertEqual(mei.mover.velocity[1] > 0, cancel)

    def test_fortify_scales_charge_damage_and_blocks_launch(self):
        run, owner, orisa = self.setup(distance=8)
        self.assertTrue(run.match.switch_hero(orisa, heroes()[immunity.ORISA]))
        orisa.mover.position[:] = (0, 1, 8)
        orisa.mover.body = None
        fight = run.match.combat
        target = next(target for target in fight._targets() if target.owner is orisa)
        vitals = fight.vitals_of(target)
        before = vitals.total
        run.step({orisa: (0, 0, 8)})
        self.assertTrue(immunity.fortify_active(orisa, orisa.body))
        self.assertAlmostEqual(immunity.damage_scale(orisa, orisa.body), 0.6)
        actor = self.begin(run, owner)
        self.stop(run, actor)
        self.assertEqual(actor.uppercut_target, orisa.body)
        self.assertAlmostEqual(immunity.damage_scale(orisa, orisa.body), 0.6)
        self.assertAlmostEqual(vitals.total, before - 67, places=4)
        self.assertAlmostEqual(owner.stats.value(stats.HERO_DAMAGE), 67, places=4)
        self.assertNotIn(orisa.body, fight.impulses.pending)
        y = orisa.mover.position[1]
        for _ in range(5):
            run.step({})
        self.assertAlmostEqual(orisa.mover.position[1], y)
        self.assertEqual(orisa.mover.velocity[1], 0)

    def test_transcendence_blocks_charge_contact_damage(self):
        run, owner, zen = self.setup(distance=8)
        self.assertTrue(run.match.switch_hero(zen, heroes()[immunity.ZENYATTA]))
        zen.mover.position[:] = (0, 1, 8)
        zen.mover.body = None
        component = zen.body_script.component
        zen.body_script.set_ult_charge(expr.to_float(component.entity_var(338).value()))
        fight = run.match.combat
        target = next(target for target in fight._targets() if target.owner is zen)
        vitals = fight.vitals_of(target)
        before = vitals.total
        run.step({zen: (0, 0, 32)})
        self.assertTrue(immunity.damage_immune(zen, zen.body))
        actor = self.begin(run, owner)
        self.stop(run, actor)
        self.assertTrue(immunity.damage_immune(zen, zen.body))
        self.assertEqual(actor.stopped_reason, "enemy")
        self.assertEqual(actor.uppercut_target, zen.body)
        self.assertEqual(vitals.total, before)
        self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 0)
        self.assertEqual(owner.client.messages(combat.HIT), [])

    def test_hero_switch_during_charge_clears_actor_and_pending_impulse(self):
        run, owner, enemy = self.setup()
        actor = self.begin(run, owner)
        run.match.combat.impulses.queue(actor.bot, (0, 12, 0))
        run.match.switch_hero(owner, heroes()[SOLDIER])
        self.assertNotIn(actor.bot, run.match.bots)
        self.assertFalse(run.match.combat.companions.active)
        self.assertNotIn(actor.bot.entity, run.match.combat.impulses.pending)
        for _ in range(30):
            run.step({})
        self.assertEqual(run.match.combat.vitals[enemy.body].total, 10000)

    def test_authored_owner_ruleset_scales_gun_duration(self):
        run, owner, _enemy = self.setup(distance=150)
        actor_world = owner.body_script.component.world
        actual = actor_world.ruleset
        with patch.object(actor_world, "ruleset",
                          side_effect=lambda key: 0.5 if key == 13032 else actual(key)):
            actor = self.begin(run, owner)
        summon = next(item for item in owner.body_script.component.instances.values()
                      if item.graph.index == 0x1536)
        self.assertEqual(summon.vars[14669].value(), 5)
        self.stop(run, actor)
        self.assertEqual(actor.gun_seconds, 5)
        self.assertEqual(actor.until - actor.ready, math.ceil(5 / combat.TICK))

    def test_uppercut_kill_credits_owner_once_and_never_launches_corpse(self):
        run, owner, enemy = self.setup()
        run.match.combat.vitals[enemy.body] = combat.Vitals(enemy.body, 100)
        actor = self.begin(run, owner)
        self.stop(run, actor)
        self.assertEqual(actor.stopped_reason, "enemy")
        self.assertTrue(run.match.combat.is_dead(enemy))
        self.assertEqual(owner.stats.value(stats.ELIMINATIONS), 1)
        self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 100)
        self.assertNotIn(enemy.body, run.match.combat.knockbacks.active)
        for _ in range(90):
            run.step({})
        self.assertEqual(owner.stats.value(stats.ELIMINATIONS), 1)
        self.assertEqual(actor.uppercut_target, enemy.body)

    def test_owner_death_during_charge_does_not_cancel_contact_or_gun(self):
        run, owner, enemy = self.setup()
        actor = self.begin(run, owner)
        target = next(item for item in run.match.combat._targets() if item.owner is owner)
        run.match.combat._damage(enemy, target, 500, False, None, run.frame)
        self.assertTrue(run.match.combat.is_dead(owner))
        self.stop(run, actor)
        self.assertEqual(actor.stopped_reason, "enemy")
        self.assertEqual(run.match.combat.vitals[enemy.body].total, 9880)
        while run.frame < actor.ready + 20:
            run.step({})
        self.assertIn(actor.bot, run.match.bots)
        self.assertGreater(actor.shots, 0)

    def test_actual_enemy_rifle_kill_during_charge_retires_actor(self):
        run, owner, enemy = self.setup()
        actor = self.begin(run, owner)
        # Begin with a wounded actor to isolate the charge interrupt boundary;
        # the existing lifecycle suite separately shoots through all1200HP.
        run.match.combat.vitals[actor.bot.entity] = combat.Vitals(actor.bot.entity, 20)
        yaw, pitch = fixtures.aim(run.match.combat._eye(enemy),
                                  (actor.bot.position[0], actor.bot.position[1] + 1, actor.bot.position[2]))
        for _ in range(20):
            run.step({enemy: (yaw, pitch, 1)})
            if actor.bot not in run.match.bots:
                break
        self.assertNotIn(actor.bot, run.match.bots)
        self.assertFalse(run.match.combat.companions.active)
        self.assertGreater(enemy.stats.value(stats.ELIMINATIONS), 0)
        self.assertEqual(actor.phase, "charge")
        self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 0)


@unittest.skipUnless(npc_fixtures.CATALOG.is_file() and npc_fixtures.BUNDLE.is_file(),
                     "requires actual external PvE inputs")
class BobChargeNpcTests(unittest.TestCase):
    setUpClass = classmethod(npc_fixtures.AshePveNpcTests.setUpClass.__func__)
    tearDownClass = classmethod(npc_fixtures.AshePveNpcTests.tearDownClass.__func__)
    fight = npc_fixtures.AshePveNpcTests.fight
    press = npc_fixtures.AshePveNpcTests.press
    spawn = npc_fixtures.AshePveNpcTests.spawn

    def test_pending_npc_launch_catches_up_after_bob_retires(self):
        definition = next(row for row in self.catalog.values() if row.name == "Trooper")
        outcomes = []
        for skipped in (False, True):
            run, owner, enemy = self.spawn(definition, 10)
            fight = run.match.combat
            owner.body_script.set_ult_charge(2240)
            self.press(run, owner, 32, frames=1)
            actor, = fight.companions.active.values()
            while actor.phase == "charge" and run.frame <= actor.charge_until:
                run.step({})
            self.assertEqual(actor.uppercut_target, enemy.entity)
            impulse, = fight.impulses.pending[enemy.entity]
            self.assertEqual(impulse.due_tick, run.frame + 1)
            fight.companions.retire(owner.body)
            self.assertNotIn(actor.bot, run.match.bots)

            component = enemy.body_script.component
            poses = []
            for frames, airborne in ((48, True), (64, False)):
                if skipped:
                    run.frame += frames
                    run.match.update(run.frame * combat.TICK, run.frame)
                    owner.take_commands([Command(run.frame)])
                else:
                    for _ in range(frames):
                        run.step({})
                movement = enemy.movement(run.frame)
                self.assertEqual(bool(movement.flags & IN_AIR), airborne)
                self.assertNotIn(enemy.entity, fight.impulses.pending)
                active = any(item.graph.index == 0x17F6 for item in component.instances.values())
                self.assertEqual(active, airborne)
                flag = component.entity_var(17932, create=False)
                value = flag.value() if flag is not None else None
                self.assertEqual(value is True, airborne)
                poses.append((
                    enemy.position, movement.velocity, movement.flags, component.frame, component.now, value,
                ))
            outcomes.append(poses)

        self.assertEqual(outcomes[0], outcomes[1])

    def test_charge_uppercuts_all_five_actual_npcs_with_authored_base_pools(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, owner, enemy = self.spawn(definition, 10)
                vitals = run.match.combat.vitals[enemy.entity]
                initial = vitals.total
                position = enemy.position
                owner.body_script.set_ult_charge(2240)
                self.press(run, owner, 32, frames=1)
                actor, = run.match.combat.companions.active.values()
                while actor.phase == "charge" and run.frame <= actor.charge_until:
                    run.step({})
                self.assertEqual(actor.stopped_reason, "enemy")
                self.assertEqual(actor.uppercut_target, enemy.entity)
                self.assertEqual(initial - vitals.total, 120)
                self.assertEqual(vitals.now[world.SHIELDS_POOL], max(0, definition.shields - 120))
                self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 120)
                self.assertEqual(owner.body_script.component.entity_var(342).value(), 0)
                component = enemy.body_script.component
                victim, = (item for item in component.instances.values() if item.graph.index == 0x17F6)
                self.assertEqual(victim.cached_source_entity, owner.body)
                self.assertTrue(victim.states[1].active)
                for _ in range(5):
                    run.step({})
                movement = enemy.movement(run.frame)
                if definition.name == "Heavy Assault":
                    self.assertEqual(enemy.position, position)
                    self.assertEqual(movement.velocity, (0, 0, 0))
                    self.assertFalse(movement.flags & IN_AIR)
                    self.assertNotIn(enemy.entity, run.match.combat.impulses.npcs)
                else:
                    self.assertGreater(enemy.position[1], 1)
                    self.assertTrue(movement.flags & IN_AIR)
                self.assertEqual(initial - vitals.total, 120)


if __name__ == "__main__":
    unittest.main()
