"""Actual Zarya E target query -> native phase -> finite ally barrier -> Echo damage."""
import unittest
from dataclasses import replace
from unittest.mock import patch

import test_echo_barriers as personal
import test_server_snapshot as snapshots
from test_game_combat import aim, in_world
from test_game_mover import box, wall

from ow174.game import world
from ow174.game.collision import World
from ow174.game.commands import Command
from ow174.game.content import SOLDIER, heroes
from ow174.game.mover import Mover, mover_data
from ow174.game.script import expr, nodes


class EchoProjectedBarrierTests(unittest.TestCase):
    def session(self):
        case = personal.EchoBarrierTests()
        run, echo, caster, _, _ = case.session()
        fight = run.match.combat
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        x, y, z = caster.mover.position
        caster.mover = Mover((x-4, y, z), world=run.match.collision_world,
                             data=mover_data(caster.hero.body))
        ally = run.match.add_player(3, 'Projected ally', SOLDIER, caster.team, False)
        in_world(run.match, ally)
        run.match.switch_hero(ally, heroes()[SOLDIER])
        run.players.append(ally)
        ally.mover = Mover((x, y, z), world=run.match.collision_world, data=mover_data(ally.hero.body))
        for _ in range(25):
            run.step({})
        target = next(t for t in fight._targets() if t.owner is ally)
        a, b = target.segment()
        centre = tuple((p+q)*.5 for p, q in zip(a, b, strict=True))
        cast = aim(fight._eye(caster), centre)
        attack = aim(fight._eye(echo), centre)
        return run, echo, caster, ally, target, cast, attack

    @staticmethod
    def instance(caster):
        return next(i for i in caster.body_script.component.instances.values() if i.graph.index == 0x25C)

    def cast(self, run, caster, angles):
        run.step({caster: (*angles, 16)})
        return next((b for b in run.match.combat.barriers.live.values()
                     if b.caster is caster and b.definition == 0x04000000000003DA), None)

    def test_native_e_selects_ally_and_protects_its_body_while_energy_returns_to_caster(self):
        run, echo, caster, ally, target, cast, attack = self.session()
        bubble = self.cast(run, caster, cast)
        self.assertIsNotNone(bubble)
        instance = self.instance(caster)
        self.assertTrue(instance.states[10].active)
        self.assertEqual(instance.vars[536].value(), expr.Entity(ally.body))
        self.assertEqual(bubble.health, 200)
        self.assertIs(bubble.owner, ally)
        self.assertIs(bubble.caster, caster)
        personal.EchoBarrierTests.fire(run, echo, attack)
        self.assertEqual(bubble.health, 149)
        self.assertEqual(run.match.combat.vitals_of(target).total, 200)
        self.assertEqual(echo.body_script.component.entity_var(342).value(), 0)
        self.assertGreater(caster.body_script.component.entity_var(87).value(), 9)
        self.assertIsNone(ally.body_script.component.entity_var(87, create=False))

    def test_actual_e_rejects_enemy_self_miss_range_wall_dead_and_ffa_without_cooldown(self):
        for mode in ('enemy', 'self', 'miss', 'range', 'wall', 'dead', 'ffa'):
            with self.subTest(mode=mode):
                run, echo, caster, ally, _, cast, attack = self.session()
                fight = run.match.combat
                if mode in ('enemy', 'self'):
                    ally.team = echo.team
                    if mode == 'self':
                        cast = aim(fight._eye(caster), tuple(caster.mover.position))
                elif mode == 'miss':
                    cast = (cast[0]-32768, 0)
                elif mode == 'range':
                    x, y, z = caster.mover.position
                    ally.mover = Mover((x+31, y, z), world=run.match.collision_world,
                                       data=mover_data(ally.hero.body))
                    cast = aim(fight._eye(caster), fight._eye(ally))
                elif mode == 'wall':
                    x, y, z = caster.mover.position
                    run.match.collision_world = World(box(x-20, x+20, y-1, y, z-20, z+20)
                        + wall(x+2, z-4, x+2, z+4, y0=y, y1=y+6))
                elif mode == 'dead':
                    for _ in range(4):
                        personal.EchoBarrierTests.fire(run, echo, attack)
                    self.assertTrue(fight.is_dead(ally))
                else:
                    run.match.game_map = replace(run.match.game_map, free_for_all=True)
                self.assertIsNone(self.cast(run, caster, cast))
                instance = self.instance(caster)
                self.assertGreater(instance.states[7].counter, 0)
                self.assertFalse(instance.states[9].flags & nodes.Ability.RUNNING)
                self.assertEqual(instance.vars[61].value(), 0)
                self.assertIsNone(fight.barriers.for_target(ally))

    def test_native_lifetime_active_repress_cooldown_rejection_and_fresh_reuse(self):
        run, echo, caster, ally, target, cast, attack = self.session()
        fight = run.match.combat
        first = self.cast(run, caster, cast)
        self.assertIsNotNone(first)
        personal.EchoBarrierTests.fire(run, echo, attack)
        run.step({caster: (*cast, 0)})
        run.step({caster: (*cast, 16)})
        self.assertIs(fight.barriers.for_target(ally), first)
        self.assertEqual(first.health, 149)
        for _ in range(145):
            run.step({})
        self.assertFalse(first.phase.active)
        self.assertIsNone(fight.barriers.for_target(ally))
        instance = self.instance(caster)
        counter = instance.states[7].counter
        self.assertGreater(instance.vars[61].value(), 0)
        self.assertIsNone(self.cast(run, caster, cast))
        self.assertEqual(instance.states[7].counter, counter)
        personal.EchoBarrierTests.fire(run, echo, attack)
        self.assertEqual(fight.vitals_of(target).total, 149)
        for _ in range(540):
            run.step({})
        second = self.cast(run, caster, cast)
        self.assertIsNotNone(second)
        self.assertEqual(second.health, 200)
        self.assertNotEqual(first.entity, second.entity)
        self.assertGreater(second.counter, first.counter)

    def test_early_released_e_expires_and_fresh_press_after_cooldown_starts(self):
        run, _, caster, ally, _, cast, _ = self.session()
        first = self.cast(run, caster, cast)
        instance = self.instance(caster)
        ability = instance.states[9]
        for _ in range(150):
            run.step({})
        self.assertGreater(ability.remaining(), 2)
        counter = instance.states[7].counter
        self.assertIsNone(self.cast(run, caster, cast))
        while ability.remaining() > 0:
            run.step({caster: (*cast, 0)})
        for _ in range(5):
            run.step({})
        self.assertEqual(instance.states[7].counter, counter)
        self.assertIsNone(run.match.combat.barriers.for_target(ally))
        second = self.cast(run, caster, cast)
        self.assertIsNotNone(second)
        self.assertNotEqual(first.entity, second.entity)
        self.assertGreater(instance.states[7].counter, counter)

    def test_zero_window_rejects_near_expiry_tap_but_held_e_starts_when_ready(self):
        for held in (False, True):
            with self.subTest(held=held):
                run, _, caster, ally, _, cast, _ = self.session()
                first = self.cast(run, caster, cast)
                instance = self.instance(caster)
                ability = instance.states[9]
                self.assertIsNone(nodes._field(ability, 'm_246438AD'))
                for _ in range(150):
                    run.step({})
                if not held:
                    while ability.remaining() > 0.08:
                        run.step({})
                counter = instance.states[7].counter
                self.assertIsNone(self.cast(run, caster, cast))
                while ability.remaining() > 0:
                    run.step({caster: (*cast, 16 if held else 0)})
                for _ in range(5):
                    run.step({caster: (*cast, 16 if held else 0)})
                second = run.match.combat.barriers.for_target(ally)
                if held:
                    self.assertIsNotNone(second)
                    self.assertNotEqual(first.entity, second.entity)
                    self.assertEqual(second.health, 200)
                    self.assertGreater(instance.states[7].counter, counter)
                else:
                    self.assertIsNone(second)
                    self.assertEqual(instance.states[7].counter, counter)

    def test_self_and_projected_barriers_coexist_with_independent_pools(self):
        run, echo, caster, _ally, target, cast, attack = self.session()
        fight = run.match.combat
        projected = self.cast(run, caster, cast)
        run.step({caster: (*cast, 8)})
        own = fight.barriers.sync(caster)
        self.assertIsNotNone(own)
        self.assertIsNotNone(projected)
        self.assertNotEqual(own.entity, projected.entity)
        self.assertEqual(len(fight.barriers.live), 2)
        personal.EchoBarrierTests.fire(run, echo, attack)
        self.assertEqual(projected.health, 149)
        self.assertEqual(own.health, 200)
        toward = aim(fight._eye(echo), own.centre())
        personal.EchoBarrierTests.fire(run, echo, toward)
        self.assertEqual(own.health, 149)
        self.assertEqual(projected.health, 149)
        self.assertEqual(fight.vitals_of(target).total, 200)
        source = next(t for t in fight._targets() if t.owner is caster)
        self.assertEqual(fight.vitals_of(source).total, 400)

    def test_second_caster_rejects_already_protected_ally(self):
        run, _, caster, ally, _, cast, _ = self.session()
        fight = run.match.combat
        second = run.match.add_player(4, 'Other Zarya', caster.hero.guid, caster.team, False)
        in_world(run.match, second)
        run.match.switch_hero(second, caster.hero)
        run.players.append(second)
        x, y, z = caster.mover.position
        second.mover = Mover((x, y, z+1), world=run.match.collision_world,
                             data=mover_data(second.hero.body))
        for _ in range(25):
            run.step({})
        first = self.cast(run, caster, cast)
        self.assertIsNotNone(first)
        toward = aim(fight._eye(second), first.centre())
        self.assertIsNone(self.cast(run, second, toward))
        self.assertIs(fight.barriers.for_target(ally), first)
        self.assertEqual(self.instance(second).vars[61].value(), 0)
        self.assertEqual(len(fight.barriers.live), 1)

    def test_projected_barrier_follows_ally_after_it_moves_beyond_cast_range(self):
        run, echo, caster, ally, target, cast, _ = self.session()
        fight = run.match.combat
        bubble = self.cast(run, caster, cast)
        self.assertIsNotNone(bubble)
        x, y, z = ally.mover.position
        ally.mover = Mover((x, y, z+31), world=run.match.collision_world,
                           data=mover_data(ally.hero.body))
        run.step({})
        self.assertIs(fight.barriers.for_target(ally), bubble)
        self.assertGreater(bubble.centre()[2], z+30)
        attack = aim(fight._eye(echo), bubble.centre())
        run.step({echo: (*attack, 1)})
        for _ in range(70):
            run.step({echo: (*attack, 0)})
        self.assertTrue(bubble.phase.active)
        self.assertEqual(bubble.health, 149)
        self.assertEqual(fight.vitals_of(target).total, 200)

    def test_friendly_echo_fire_passes_allied_projected_barrier_and_hits_enemy(self):
        run, enemy, caster, ally, _, cast, _ = self.session()
        fight = run.match.combat
        friend = run.match.add_player(4, 'Allied Echo', personal.echoes.ECHO, caster.team, False)
        in_world(run.match, friend)
        run.match.switch_hero(friend, heroes()[personal.echoes.ECHO])
        run.players.append(friend)
        x, y, z = ally.mover.position
        friend.mover = Mover((x, y, z+5), world=run.match.collision_world,
                             data=mover_data(friend.hero.body))
        for _ in range(25):
            run.step({})
        bubble = self.cast(run, caster, cast)
        x, y, z = enemy.mover.position
        toward = aim(fight._eye(friend), (x, y + .8, z))
        personal.EchoBarrierTests.fire(run, friend, toward)
        own = next(t for t in fight._targets() if t.owner is enemy)
        self.assertEqual(fight.vitals_of(own).total, 149)
        self.assertEqual(bubble.health, 200)

    def test_target_body_replacement_retires_shield_without_protecting_new_body(self):
        run, echo, caster, ally, _, cast, _ = self.session()
        fight = run.match.combat
        bubble = self.cast(run, caster, cast)
        old = ally.body
        run.match.switch_hero(ally, heroes()[0x02E0000000000002], preserve_pose=True)
        for _ in range(25):
            run.step({})
        self.assertNotEqual(ally.body, old)
        self.assertIsNone(fight.barriers.for_target(ally))
        self.assertIsNone(fight.barriers.attached(bubble.entity))
        target = next(t for t in fight._targets() if t.owner is ally)
        x, y, z = target.feet
        personal.EchoBarrierTests.fire(run, echo, aim(fight._eye(echo), (x, y + .7, z)))
        self.assertEqual(fight.vitals_of(target).total, 199)

    def test_copy_expiry_retires_a_projected_shield_on_the_copied_body(self):
        run, enemy, caster, ally, _, _, _ = self.session()
        fight = run.match.combat
        echo = heroes()[personal.echoes.ECHO]
        run.match.switch_hero(ally, echo, preserve_pose=True)
        run.match.switch_hero(enemy, heroes()[SOLDIER], preserve_pose=True)
        for _ in range(25):
            run.step({})
        ally.body_script.set_ult_charge(ally.body_script.component.entity_var(338).value())
        copy_aim = aim(fight._eye(ally), fight._eye(enemy))
        run.step({ally: (*copy_aim, 32)})
        self.assertTrue(fight.echo_duplicates.active(ally))
        copied_body = ally.body
        run.match.switch_hero(enemy, echo, preserve_pose=True)
        expires = fight.echo_duplicates.states[ally.entity].expires
        while expires - fight.now > 1:
            run.step({})
        target = next(t for t in fight._targets() if t.owner is ally)
        a, b = target.segment()
        centre = tuple((lo + hi) / 2 for lo, hi in zip(a, b, strict=True))
        bubble = self.cast(run, caster, aim(fight._eye(caster), centre))
        self.assertIsNotNone(bubble)
        self.assertEqual(bubble.body, copied_body)
        attack = aim(fight._eye(enemy), centre)
        personal.EchoBarrierTests.fire(run, enemy, attack)
        self.assertEqual(bubble.health, 149)
        self.assertEqual(fight.vitals_of(target).total, 200)
        while fight.echo_duplicates.active(ally):
            run.step({})
        self.assertEqual(ally.hero.guid, echo.guid)
        self.assertIsNone(fight.barriers.for_target(ally))
        self.assertIsNone(fight.barriers.attached(bubble.entity))
        restored = next(t for t in fight._targets() if t.owner is ally)
        personal.EchoBarrierTests.fire(run, enemy, attack)
        self.assertEqual(fight.vitals_of(restored).total, 149)

    def test_native_copy_expiry_retires_projected_shield_on_unchanged_ally(self):
        run, echo, enemy, ally, target, _, _ = self.session()
        fight = run.match.combat
        ally.team = echo.team
        echo.body_script.set_ult_charge(echo.body_script.component.entity_var(338).value())
        copy_aim = aim(fight._eye(echo), fight._eye(enemy))
        run.step({echo: (*copy_aim, 32)})
        self.assertEqual(echo.hero.guid, enemy.hero.guid)
        run.match.switch_hero(enemy, heroes()[personal.echoes.ECHO], preserve_pose=True)
        for _ in range(845):
            run.step({})
        toward = aim(fight._eye(echo), (target.feet[0], target.feet[1]+target.height*.5, target.feet[2]))
        bubble = self.cast(run, echo, toward)
        self.assertIsNotNone(bubble)
        for _ in range(200):
            if not fight.echo_duplicates.active(echo):
                break
            run.step({})
        self.assertFalse(fight.echo_duplicates.active(echo))
        self.assertLess(echo.body_script.component.now, bubble.phase.start+2000)
        self.assertIsNone(fight.barriers.for_target(ally))
        self.assertEqual(fight.vitals_of(target).total, 200)
        x, y, z = target.feet
        personal.EchoBarrierTests.fire(run, enemy, aim(fight._eye(enemy), (x, y + .8, z)))
        self.assertEqual(fight.vitals_of(target).total, 149)

    def test_stopped_caster_script_cannot_leave_a_stale_projected_pool(self):
        run, echo, caster, ally, target, cast, attack = self.session()
        bubble = self.cast(run, caster, cast)
        self.assertIsNotNone(bubble)
        script = caster.body_script
        run.match.stop_body_script(caster)
        self.assertTrue(script.retired)
        self.assertIsNone(run.match.combat.barriers.for_target(ally))
        personal.EchoBarrierTests.fire(run, echo, attack)
        self.assertEqual(run.match.combat.vitals_of(target).total, 149)

    def test_restarting_same_body_script_does_not_reuse_old_barrier_activation(self):
        run, _, caster, ally, _, cast, _ = self.session()
        first = self.cast(run, caster, cast)
        old_script = caster.body_script
        frame = caster.body_script.frame
        body = caster.body
        create = next(u for u in reversed(caster.client.entities)
                      if u.entity == body and u.op == world.OP_CREATE)
        run.match.start_body_script(caster, create, start_frame=frame)
        for _ in range(25):
            run.step({})
        self.assertEqual(caster.body, body)
        second = self.cast(run, caster, cast)
        self.assertIsNotNone(second)
        self.assertIsNot(first.phase, second.phase)
        self.assertNotEqual(first.entity, second.entity)
        self.assertIs(run.match.combat.barriers.for_target(ally), second)
        self.assertTrue(old_script.retired)

    def test_real_target_array_and_projected_pool_export_without_evaluating_query(self):
        observer = snapshots.ServerSnapshotTests()
        session, peers, players = observer.scene(two=True)
        caster, ally = players
        # Team preparation is for this passive capture only; no causal journal replay is claimed.
        ally.team = caster.team
        session.apply('switch_hero', {'account': caster.account_lo, 'hero': personal.ZARYA})
        peers[0].commands(caster.body_script.frame+1, 25, 0)
        instance = self.instance(caster)
        observed = []
        original = nodes.TrackTargets.on_begin

        def entered(state):
            original(state)
            if (state.instance.graph.index, state.index) == (0x25C, 7):
                observed.append((state.targets, state.payload()['targets']))

        with patch.object(nodes.TrackTargets, 'on_begin', entered):
            peers[0].commands(caster.body_script.frame+1, 1, 16)
        self.assertEqual(observed, [((expr.Entity(ally.body),), ((ally.body, 0),))])
        self.assertEqual(instance.vars[536].value(), expr.Entity(ally.body))
        self.assertIsNotNone(caster.match.combat.barriers.for_target(ally))
        with patch.object(type(caster.body_script.world), 'track_targets',
                          side_effect=AssertionError('passive export must not evaluate queries')):
            first = observer.capture(session)
            self.assertEqual(snapshots.snapshot_digest(first),
                             snapshots.snapshot_digest(observer.capture(session)))
        peers[0].commands(caster.body_script.frame+1, 1, 0)
        current = observer.capture(session)
        self.assertNotEqual(snapshots.snapshot_digest(first), snapshots.snapshot_digest(current))

    def test_retired_source_does_not_block_another_caster_before_scheduler_cleanup(self):
        run, _, caster, ally, _, cast, _ = self.session()
        fight = run.match.combat
        second = run.match.add_player(4, 'Fresh caster', caster.hero.guid, caster.team, False)
        in_world(run.match, second)
        run.match.switch_hero(second, caster.hero)
        run.players.append(second)
        x, y, z = caster.mover.position
        second.mover = Mover((x, y, z+1), world=run.match.collision_world,
                             data=mover_data(second.hero.body))
        for _ in range(25):
            run.step({})
        first = self.cast(run, caster, cast)
        self.assertIsNotNone(first)
        run.match.stop_body_script(caster)
        toward = aim(fight._eye(second), first.centre())
        # No intervening match tick or global barrier scan: process the next real command now.
        second.take_commands([Command(second.body_script.frame+1, yaw=toward[0], pitch=toward[1],
                                      buttons=16)])
        current = fight.barriers.for_target(ally)
        self.assertIsNotNone(current)
        self.assertIs(current.caster, second)
        self.assertNotEqual(current.entity, first.entity)

    def test_foreign_flashbang_stops_caster_ability_but_native_pet_timer_keeps_ally_protected(self):
        run, flash, caster, ally, target, cast, _ = self.session()
        fight = run.match.combat
        run.match.switch_hero(flash, heroes()[0x02E0000000000042], preserve_pose=True)
        x, y, z = caster.mover.position
        flash.mover = Mover((x, y, z-2), world=run.match.collision_world,
                            data=mover_data(flash.hero.body))
        attacker = run.match.add_player(4, 'Incoming Echo', personal.echoes.ECHO, flash.team, False)
        in_world(run.match, attacker)
        run.match.switch_hero(attacker, heroes()[personal.echoes.ECHO])
        run.players.append(attacker)
        x, y, z = ally.mover.position
        attacker.mover = Mover((x, y, z-5), world=run.match.collision_world,
                               data=mover_data(attacker.hero.body))
        attacker.body_script.ult_seconds = 0
        for _ in range(25):
            run.step({})
        bubble = self.cast(run, caster, cast)
        toward = aim(fight._eye(flash), fight._eye(caster))
        run.step({flash: (*toward, 16)})
        for _ in range(50):
            if fight.is_stunned(caster):
                break
            run.step({flash: (*toward, 0)})
        self.assertTrue(fight.is_stunned(caster))
        own = next(t for t in fight._targets() if t.owner is caster)
        self.assertEqual(fight.vitals_of(own).total, 375)
        self.assertFalse(self.instance(caster).states[9].flags & nodes.Ability.RUNNING)
        self.assertTrue(bubble.phase.active)
        self.assertEqual(bubble.health, 175)
        attack = aim(fight._eye(attacker), bubble.centre())
        personal.EchoBarrierTests.fire(run, attacker, attack)
        self.assertTrue(fight.is_stunned(caster))
        self.assertEqual(bubble.health, 124)
        self.assertEqual(fight.vitals_of(target).total, 200)
        self.assertEqual(attacker.body_script.component.entity_var(342).value(), 0)
        for _ in range(140):
            run.step({})
        self.assertFalse(fight.is_stunned(caster))
        self.assertIsNone(fight.barriers.for_target(ally))
        personal.EchoBarrierTests.fire(run, attacker, attack)
        self.assertEqual(fight.vitals_of(target).total, 149)

    def test_real_caster_death_preserves_existing_projected_timer_until_native_expiry(self):
        run, echo, caster, ally, target, cast, attack = self.session()
        fight = run.match.combat
        own = next(t for t in fight._targets() if t.owner is caster)
        toward = aim(fight._eye(echo), (own.feet[0], own.feet[1]+1, own.feet[2]))
        for _ in range(6):
            personal.EchoBarrierTests.fire(run, echo, toward)
        self.assertEqual(fight.vitals_of(own).total, 94)
        bubble = self.cast(run, caster, cast)
        self.assertIsNotNone(bubble)
        for _ in range(2):
            personal.EchoBarrierTests.fire(run, echo, toward)
        self.assertTrue(fight.is_dead(caster))
        self.assertFalse(caster.body_script.retired)
        self.assertTrue(bubble.phase.active)
        self.assertIs(fight.barriers.for_target(ally), bubble)
        instance = self.instance(caster)
        count = instance.states[7].counter
        run.step({caster: (*cast, 16)})
        self.assertEqual(instance.states[7].counter, count)
        personal.EchoBarrierTests.fire(run, echo, attack)
        self.assertEqual(bubble.health, 149)
        self.assertEqual(fight.vitals_of(target).total, 200)
        self.assertTrue(fight.is_dead(caster))
        for _ in range(140):
            run.step({})
        self.assertFalse(bubble.phase.active)
        self.assertIsNone(fight.barriers.for_target(ally))
        self.assertGreater(instance.vars[61].value(), 0)
        personal.EchoBarrierTests.fire(run, echo, attack)
        self.assertEqual(fight.vitals_of(target).total, 149)


if __name__ == '__main__':
    unittest.main()
