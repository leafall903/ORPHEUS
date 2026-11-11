"""Actual0B8B arithmetic and explicit Winston server cadence; native gates OPEN."""

import unittest
from dataclasses import replace
from unittest.mock import patch

import test_game_winston as fixtures
from test_game_combat import Run, aim

from ow174.game import combat, stats, winston
from ow174.game.body_vm_snapshot import export_body_vm, snapshot_digest
from ow174.game.content import PRACTICE_RANGE, game_map, heroes
from ow174.game.script import expr, graph, runtime
from ow174.game.script.driver import BodyScript


class ChargeTests(unittest.TestCase):
    def scene(self, passive=False):
        case = fixtures.TeslaTests()
        case.setUp()
        script = case.winston.body_script
        script.ult_seconds = 60 if passive else 0
        script.set_ult_charge(0)
        return case, script

    def charge(self, script):
        return script.component.entity_var(342).value()

    def idle(self, case, frames):
        for _ in range(frames):
            case.step(0)

    def quiet(self, case, frames):
        case.run.frame += frames
        case.match.update(case.run.frame * combat.TICK, case.run.frame)

    def test_passive_original_five_per_second_multipliers_cap_and_enable_switch(self):
        case, script = self.scene(True)
        self.idle(case, 62)
        self.assertEqual(self.charge(script), 0)
        self.idle(case, 1)
        self.assertEqual(self.charge(script), 5)
        self.idle(case, 125)
        self.assertEqual(self.charge(script), 15)
        script.component.set(9532, 2)
        script.component.set(12918, 3)
        script.ult_seconds = 1  # legacy fill duration must not change authored cadence
        self.idle(case, 63)
        self.assertEqual(self.charge(script), 45)
        script.set_ult_charge(1539)
        self.idle(case, 63)
        self.assertEqual(self.charge(script), 1540)
        script.set_ult_charge(0)
        script.ult_seconds = 0
        self.idle(case, 100)
        self.assertEqual(self.charge(script), 0)
        self.assertEqual(script.winston_charge_ms, 0)

    def test_no_owner_input_sparse_world_ticks_and_duplicate_updates_preserve_cadence(self):
        case, script = self.scene(True)
        owner_frame = script.frame
        self.quiet(case, 62)
        self.assertEqual(self.charge(script), 0)
        self.quiet(case, 1)
        self.assertEqual(self.charge(script), 5)
        for _ in range(3):
            case.match.combat.update(case.run.frame * combat.TICK, case.run.frame)
        self.assertEqual(self.charge(script), 5)
        self.quiet(case, 125)
        self.assertEqual(self.charge(script), 15)
        self.assertEqual(script.frame, owner_frame)
        self.assertEqual(script.winston_charge_ms, 8)

    def test_command_bursts_cannot_add_world_time_or_duplicate_credit(self):
        case, script = self.scene(True)
        for frame in range(case.run.frame + 1, case.run.frame + 201):
            script.command(frame, 0)
        self.assertEqual(self.charge(script), 0)
        self.assertEqual(script.winston_charge_ms, 0)
        self.quiet(case, 63)
        self.assertEqual(self.charge(script), 5)

    def test_explicit_positive_eligibility_runs_original_wait_without_command_clock_bypass(self):
        case, script = self.scene(True)
        script.component.set(2426, True)
        case.step(0)
        instance = next(i for i in script.component.instances.values() if i.graph.index == 0xB8B)
        wait = instance.states[instance.graph.node(3).state]
        self.assertTrue(wait.active)
        first = wait.counter
        for frame in range(script.frame + 1, script.frame + 201):
            script.command(frame, 0)
        self.assertEqual(self.charge(script), 0)
        self.assertEqual(wait.counter, first)
        self.quiet(case, 62)
        self.assertEqual(self.charge(script), 5)
        self.assertEqual(wait.counter, first + 1)
        for frame in (script.frame, script.frame - 1):
            script.command(frame, 0)
        self.assertEqual(self.charge(script), 5)

    def test_standalone_body_uses_same_authored_interval_once_per_new_command(self):
        script = BodyScript(winston.HERO, 0xA0000101, 100, ult_seconds=1)
        for frame in range(101, 163):
            script.command(frame, 0)
        self.assertEqual(self.charge(script), 0)
        script.command(163, 0)
        self.assertEqual(self.charge(script), 5)
        script.command(163, 0)
        script.command(162, 0)
        self.assertEqual(self.charge(script), 5)

    def test_actual_tesla_damage_uses_multipliers_armour_actual_loss_and_cap(self):
        case, script = self.scene()
        script.component.set(9532, 2)
        script.component.set(13022, 3)
        case.fight.vitals[case.enemy.body] = combat.Vitals(case.enemy.body, 100, 100)
        case.step()
        self.assertAlmostEqual(self.charge(script), .96 * .8 * 6, places=5)
        script.set_ult_charge(1539)
        case.step()
        self.assertEqual(self.charge(script), 1540)

    def test_overkill_and_corpse_award_only_remaining_health(self):
        case, script = self.scene()
        case.fight.vitals[case.enemy.body] = combat.Vitals(case.enemy.body, .25)
        case.step()
        self.assertTrue(case.fight.is_dead(case.enemy))
        self.assertAlmostEqual(self.charge(script), .25)
        for _ in range(5):
            case.step()
        self.assertAlmostEqual(self.charge(script), .25)

    def test_melee_and_jump_launch_award_actual_health_loss(self):
        case, script = self.scene()
        case.enemy.mover.position = [0, 0, 1]
        case.step(0x800)
        self.idle(case, 40)
        self.assertEqual(self.charge(script), 30)
        case.enemy.mover.position = [0, 0, 1]
        case.step(8)
        self.assertEqual(self.charge(script), 31)

    def test_miss_wall_ally_secondary_and_actual_enemy_bubble_do_not_award_damage_charge(self):
        for reason in ('miss', 'wall', 'ally', 'secondary', 'bubble'):
            with self.subTest(reason=reason):
                case, script = self.scene()
                if reason == 'miss':
                    case.enemy.mover.position = [0, 0, -4]
                if reason == 'ally':
                    case.enemy.team = 0
                if reason == 'bubble':
                    case.match.switch_hero(case.enemy, heroes()[winston.HERO])
                    case.enemy.mover.position = [0, 0, 6]
                    case.run.step({case.enemy: (0, 0, 16)})
                    self.idle(case, 4)
                    self.assertIn(case.enemy.body, case.fight.barriers.actors)
                with patch.object(combat, 'ray_world', return_value=.01 if reason == 'wall' else None):
                    case.step(2 if reason == 'secondary' else 1)
                self.assertEqual(self.charge(script), 0)

    def test_incoming_actual_attacks_only_damage_enemy_winston(self):
        for attack, button, distance in (('Tesla', 1, 3), ('rifle', 1, 7), ('Helix', 2, 7),
                                         ('melee', 0x800, 1.5), ('Flashbang', 16, 3.5)):
            for friendly in (False, True):
                with self.subTest(attack=attack, friendly=friendly):
                    case, target_script = self.scene()
                    attacker, target = case.enemy, case.winston
                    if attack == 'Tesla':
                        self.assertTrue(case.match.switch_hero(attacker, heroes()[winston.HERO]))
                    elif attack == 'Flashbang':
                        self.assertTrue(case.match.switch_hero(attacker, heroes()[0x02E0000000000042]))
                    attacker.team = target.team if friendly else 1
                    case.ally.mover.position[:] = (25, 1, 25)
                    attacker.mover.position[:] = (0, 1, distance)
                    target.mover.position[:] = (0, 1, 0)
                    for player in case.run.players:
                        player.mover.body = None
                    script = attacker.body_script
                    script.ult_seconds = 0
                    script.set_ult_charge(0)
                    point = (0, 1, 0) if attack == 'Flashbang' else case.fight._eye(target)
                    yaw, pitch = aim(case.fight._eye(attacker), point)
                    case.run.step({attacker: (yaw, pitch, button)})
                    stunned = False
                    for _ in range(60):
                        case.run.step({attacker: (yaw, pitch, 0)})
                        stunned |= case.fight.is_stunned(target)
                    loss = 500 - case.health(target)
                    self.assertEqual(loss > 0, not friendly)
                    self.assertAlmostEqual(attacker.stats.value(stats.HERO_DAMAGE), loss)
                    self.assertEqual(stunned, attack == 'Flashbang' and not friendly)
                    self.assertEqual(self.charge(target_script), 0)
                    if attack == 'Tesla':
                        self.assertAlmostEqual(self.charge(script), loss)

    def test_actual_deathmatch_mode_same_team_number_allows_tesla_credit(self):
        deathmatch = game_map(0x0800000000000CD3, 0x023000000000001E, 'Deathmatch', True, (8,))
        self.assertIsNotNone(deathmatch)
        for mode in (PRACTICE_RANGE, deathmatch):
            with self.subTest(mode=mode.mode_name):
                run = Run(mode, names=('Winston', 'Target'))
                match = run.match
                match.bots.clear()
                attacker, target = run.players
                self.assertTrue(match.switch_hero(attacker, heroes()[winston.HERO]))
                attacker.team = target.team = 0
                attacker.mover.position[:] = (0, 1, 0)
                target.mover.position[:] = (0, 1, 3)
                attacker.mover.body = target.mover.body = None
                script = attacker.body_script
                script.ult_seconds = 0
                script.set_ult_charge(0)
                fight = match.combat
                fight.vitals[target.body] = combat.Vitals(target.body, .25)
                run.step({attacker: (0, 0, 1)})
                credit = .25 if mode.free_for_all else 0
                self.assertEqual(fight.is_dead(target), mode.free_for_all)
                self.assertEqual(attacker.stats.value(stats.HERO_DAMAGE), credit)
                self.assertEqual(attacker.stats.value(stats.ELIMINATIONS), int(mode.free_for_all))
                self.assertEqual(target.stats.value(stats.DEATHS), int(mode.free_for_all))
                self.assertAlmostEqual(self.charge(script), credit)
                script.ult_seconds = 60
                end = run.frame + 63
                match.update(end * combat.TICK, end, frames=False)
                self.assertAlmostEqual(self.charge(script), credit + 5)

    def test_authored_echo_marker_lifetime_is_visible_to_charge_target_predicates(self):
        case, script = self.scene()
        component = case.enemy.body_script.component
        # Exercise the authored stack on a controlled target without Echo's body scheduler.
        duplicate = component.create(graph.graph(0x1544), init=False)
        marker = duplicate.node_state(duplicate.graph.node(66))
        charge = next(i for i in script.component.instances.values() if i.graph.index == 0x0B8B)
        predicates = [charge.graph.node(i).field('m_condition.m_configVars')[3]['m_filter']['m_condition']
                      for i in (53, 54)]
        charge.write((expr.INSTANCE, 393), expr.Entity(case.enemy.body))
        self.assertFalse(any(expr.truthy(charge.evaluate(cfg)) for cfg in predicates))
        marker.begin()
        self.assertTrue(marker.active)
        self.assertTrue(all(expr.truthy(charge.evaluate(cfg)) for cfg in predicates))
        charge.write((expr.INSTANCE, 393), expr.Entity(case.ally.body))
        self.assertFalse(any(expr.truthy(charge.evaluate(cfg)) for cfg in predicates))
        charge.write((expr.INSTANCE, 393), expr.Entity(case.enemy.body))
        marker.end(False)
        self.assertFalse(any(expr.truthy(charge.evaluate(cfg)) for cfg in predicates))

    def test_practice_range_tagged_bots_keep_team_and_damage_credit_boundaries(self):
        run = Run(PRACTICE_RANGE, names=('Winston',))
        match, (player,) = run.match, run.players
        self.assertTrue(match.switch_hero(player, heroes()[winston.HERO]))
        hostile = next(bot for bot in match.bots if bot.body == 0x0400000000001403)
        friendly = next(bot for bot in match.bots if bot.body == 0x040000000000143E)
        self.assertNotEqual(hostile.team, friendly.team)
        match.bots[:] = [hostile, friendly]
        player.team = friendly.team
        player.mover.position[:] = (0, 1, 0)
        player.mover.body = None
        for bot, x in ((hostile, -1), (friendly, 1)):
            bot.position, bot.route = (x, 1, 3), []
        script = player.body_script
        script.ult_seconds = 0
        fight = match.combat
        targets = {target.entity: fight.vitals_of(target) for target in fight._targets()}
        hostile_before = targets[hostile.entity].total
        friendly_before = targets[friendly.entity].total
        run.step({player: (0, 0, 1)})
        self.assertAlmostEqual(hostile_before - targets[hostile.entity].total, .96)
        self.assertEqual(targets[friendly.entity].total, friendly_before)
        self.assertAlmostEqual(self.charge(script), .96)
        self.assertAlmostEqual(player.stats.value(stats.HERO_DAMAGE), .96)

    def test_both_tagged_dva_bodies_credit_actual_damage_after_armour(self):
        dva = heroes()[0x02E000000000007A]
        for body, expected in ((0x0400000000000407, .768), (0x040000000000040A, .96)):
            with self.subTest(body=body):
                case, script = self.scene()
                self.assertTrue(case.match.switch_hero(case.enemy, replace(dva, body=body)))
                case.enemy.mover.position[:] = (0, 0, 3)
                case.enemy.mover.body = None
                case.step()
                vitals = case.fight.vitals[case.enemy.body]
                self.assertAlmostEqual(sum(vitals.most.values()) - vitals.total, expected)
                self.assertAlmostEqual(self.charge(script), expected)

    def test_resource_eligibility_and_channel_blocks_reset_partial_time(self):
        for values in ({2426: False}, {9530: False}, {10482: False, 10480: False}, {842: True}):
            with self.subTest(values=values):
                case, script = self.scene(True)
                self.idle(case, 30)
                for key, value in values.items():
                    script.component.set(key, value)
                case.step()
                self.idle(case, 80)
                self.assertEqual(self.charge(script), 0)
                self.assertEqual(script.winston_charge_ms, 0)

    def test_authored_exclusion_stack_blocks_damage_charge_only_on_marked_target(self):
        for graph_id, position, variable in ((0x0636, 16, 6442), (0x0096, 11, 1363)):
            with self.subTest(variable=variable):
                case, script = self.scene()
                enemy = case.enemy.body_script.component
                source = enemy.create(graph.graph(graph_id), init=False)
                marker = source.node_state(source.graph.node(position))
                marker.begin()
                self.assertTrue(enemy.entity_var(variable).value())
                case.ally.team = case.enemy.team
                case.ally.mover.position[:] = (1, 0, 3)
                case.step()
                self.assertAlmostEqual(case.health(case.enemy), 199.04)
                self.assertAlmostEqual(case.health(case.ally), 199.04)
                self.assertAlmostEqual(self.charge(script), .96)

                marker.end(False)
                self.assertFalse(enemy.entity_var(variable).value())
                case.step()
                self.assertAlmostEqual(case.health(case.enemy), 198.08)
                self.assertAlmostEqual(case.health(case.ally), 198.08)
                self.assertAlmostEqual(self.charge(script), 2.88, places=5)

    def test_explicit_mode_passive_block_keeps_damage_charge_and_recovers_without_banked_time(self):
        case, script = self.scene(True)
        original = graph.mode_variables
        variables = dict(original(case.match.game_map.mode_guid) or {})
        variables[11002] = True
        with patch.object(runtime, 'mode_variables', return_value=variables):
            case.step()
            self.assertAlmostEqual(self.charge(script), .96, places=5)
            self.quiet(case, 125)
            self.assertAlmostEqual(self.charge(script), .96, places=5)
            self.assertEqual(script.winston_charge_ms, 0)
        self.quiet(case, 63)
        self.assertAlmostEqual(self.charge(script), 5.96, places=5)

    def test_roadhog_body_tag_reduces_tesla_charge_without_reducing_damage(self):
        case, script = self.scene()
        hog = heroes()[0x02E0000000000040]
        self.assertEqual(hog.body, 0x040000000000028F)
        self.assertTrue(case.match.switch_hero(case.enemy, hog))
        case.enemy.mover.position[:] = (0, 0, 3)
        case.enemy.mover.body = None
        case.ally.team = case.enemy.team
        case.ally.mover.position[:] = (1, 0, 3)
        case.step()
        vitals = case.fight.vitals[case.enemy.body]
        self.assertAlmostEqual(vitals.total, sum(vitals.most.values()) - .96)
        self.assertAlmostEqual(case.health(case.ally), 199.04)
        self.assertAlmostEqual(self.charge(script), .96 * .75 + .96, places=5)
        # Controlled overlap to verify 0B8B's priority. The stock body has only 011E.
        tag_graph = case.enemy.body_script.component.create(graph.graph(0x0E62), init=False)
        tag_graph.state(7).begin()
        before = self.charge(script)
        case.step()
        self.assertAlmostEqual(self.charge(script) - before, .96 * .5 + .96, places=5)

    def test_passive_and_damage_specific_blocks_are_independent_and_recover(self):
        case, script = self.scene(True)
        script.component.set(12321, True)
        case.step()
        self.assertEqual(self.charge(script), 0)
        self.idle(case, 62)
        self.assertEqual(self.charge(script), 5)
        script.component.set(12321, False)
        script.component.set(11942, True)
        case.step()
        self.assertAlmostEqual(self.charge(script), 5.96, places=5)
        self.idle(case, 80)
        self.assertAlmostEqual(self.charge(script), 5.96, places=5)
        script.component.set(11942, False)
        self.idle(case, 63)
        self.assertAlmostEqual(self.charge(script), 10.96, places=5)

    def test_capped_time_is_not_banked_for_future_spend(self):
        case, script = self.scene(True)
        self.idle(case, 60)
        script.set_ult_charge(1540)
        self.quiet(case, 1000)
        self.assertEqual(script.winston_charge_ms, 0)
        script.set_ult_charge(0)
        self.quiet(case, 62)
        self.assertEqual(self.charge(script), 0)
        self.quiet(case, 1)
        self.assertEqual(self.charge(script), 5)

    def test_zero_rate_discards_elapsed_pulses_but_keeps_the_current_cadence_phase(self):
        case, script = self.scene(True)
        script.component.set(12918, 0)
        self.quiet(case, 188)  #3008ms, three zero-valued pulses and8ms phase
        self.assertEqual(self.charge(script), 0)
        self.assertEqual(script.winston_charge_ms, 8)
        script.component.set(12918, 1)
        self.quiet(case, 61)
        self.assertEqual(self.charge(script), 0)
        self.quiet(case, 1)
        self.assertEqual(self.charge(script), 5)

    def test_actual_primal_suppresses_and_expiry_restarts_a_fresh_interval(self):
        case, script = self.scene(True)
        self.idle(case, 60)
        script.set_ult_charge(1540)
        case.step(32)
        self.assertTrue(winston.primal_active(script.component))
        self.assertEqual(self.charge(script), 0)
        for _ in range(700):
            case.step(0)
            if not winston.primal_active(script.component):
                break
            self.assertEqual(self.charge(script), 0)
            self.assertEqual(script.winston_charge_ms, 0)
        self.assertFalse(winston.primal_active(script.component))
        self.idle(case, 62)
        self.assertEqual(self.charge(script), 0)
        self.idle(case, 1)
        self.assertEqual(self.charge(script), 5)

    def test_death_self_friendly_and_replacement_cannot_fund_charge(self):
        case, script = self.scene(True)
        self.idle(case, 30)
        for target in (case.targets[case.winston], case.targets[case.ally]):
            case.fight._damage(case.winston, target, 1, False, None, case.run.frame)
        self.assertEqual(self.charge(script), 0)
        case.fight._damage(case.enemy, case.targets[case.winston], 2000, False, None, case.run.frame)
        self.quiet(case, 100)
        self.assertEqual(self.charge(script), 0)
        self.assertEqual(script.winston_charge_ms, 0)
        case.match.switch_hero(case.winston, heroes()[winston.HERO])
        replacement = case.winston.body_script
        self.assertIsNot(script, replacement)
        self.assertEqual(replacement.winston_charge_ms, 0)
        self.assertEqual(replacement.winston_charge_tick, case.match.tick)

    def test_primal_world_expiry_clears_original_scopes_without_advancing_owner_frontier(self):
        case, script = self.scene(True)
        script.set_ult_charge(1540)
        case.step(32)
        owner_frame = script.frame
        jump = next(i for i in script.component.instances.values() if i.graph.index == 0x10A)
        self.assertEqual(jump.vars[472].value(), 2)
        self.quiet(case, 624)
        self.assertTrue(winston.primal_active(script.component))
        self.assertEqual(case.fight.vitals[case.winston.body].total, 1000)
        self.quiet(case, 1)
        self.assertFalse(winston.primal_active(script.component))
        self.assertFalse(script.component.entity_var(842).value())
        self.assertEqual(jump.vars[472].value(), 6)
        self.assertEqual(case.fight.vitals[case.winston.body].total, 500)
        self.assertEqual(self.charge(script), 0)
        self.quiet(case, 62)
        self.assertEqual(self.charge(script), 0)
        self.quiet(case, 1)
        self.assertEqual(self.charge(script), 5)
        self.assertEqual(script.frame, owner_frame)

    def test_sparse_world_span_awards_only_post_primal_time_and_owner_catchup_cannot_replay_it(self):
        case, script = self.scene(True)
        script.set_ult_charge(1540)
        case.step(32)
        self.quiet(case, 750)  #10s Primal plus2s passive, in a single world update
        self.assertFalse(winston.primal_active(script.component))
        self.assertEqual(self.charge(script), 10)
        self.assertEqual(case.fight.vitals[case.winston.body].total, 500)
        case.step(0)
        self.assertFalse(winston.primal_active(script.component))
        self.assertEqual(self.charge(script), 10)
        self.assertEqual(script.winston_charge_ms, 16)

    def test_command_bursts_cannot_shorten_primal_or_double_advance_its_lifetime(self):
        case, script = self.scene(True)
        script.set_ult_charge(1540)
        case.step(32)
        for frame in range(script.frame + 1, script.frame + 701):
            script.command(frame, 0)
        self.assertTrue(winston.primal_active(script.component))
        self.quiet(case, 624)
        self.assertTrue(winston.primal_active(script.component))
        self.quiet(case, 1)
        self.assertFalse(winston.primal_active(script.component))
        self.assertEqual(self.charge(script), 0)

    def test_vm_snapshot_observes_both_cadence_fields_without_evaluation(self):
        _case, script = self.scene(True)
        baseline = snapshot_digest(export_body_vm(script))
        for name in ('winston_charge_ms', 'winston_charge_tick', 'winston_primal_clock'):
            original = getattr(script, name)
            setattr(script, name, (1, 2, 3, 4) if name == 'winston_primal_clock' else original + 1)
            with patch.object(runtime.Component, 'run_frame', side_effect=AssertionError('no execution')):
                self.assertNotEqual(snapshot_digest(export_body_vm(script)), baseline)
            setattr(script, name, original)
            self.assertEqual(snapshot_digest(export_body_vm(script)), baseline)


if __name__ == '__main__':
    unittest.main()
