"""Actual Reaper combat consumers of0B8B arithmetic; eligibility bridge is explicit."""

import unittest
from dataclasses import replace
from unittest.mock import patch

import test_game_reaper as reaper_tests
from test_game_combat import Run, aim

from ow174.game import combat, damage_charge, pools, reaper
from ow174.game.commands import Command
from ow174.game.content import PRACTICE_RANGE, find_hero
from ow174.game.mover import defined_mover_data
from ow174.game.script import expr, graph


class ReaperChargeTests(unittest.TestCase):
    def scene(self, **kwargs):
        run, owner, victim, target = reaper_tests.ReaperTests().scenario(**kwargs)
        script = owner.body_script
        script.ult_seconds = 0  # isolate damage awards from passive time
        script.set_ult_charge(0)
        script.reaper_charge_ms = 0
        return run, owner, victim, target

    def charge(self, owner):
        return owner.body_script.component.entity_var(342).value()

    def fire(self, run, owner, victim):
        reaper_tests.ReaperTests().fire(run, owner, victim)

    def test_lethal_shot_retires_full_charge_and_respawn_earns_a_fresh_cast(self):
        run, owner, victim, target = self.scene()
        fight = run.match.combat
        old = owner.body_script
        old_body = owner.body
        old.set_ult_charge(2100)
        fight.vitals[old_body].damage(249)
        self.fire(run, victim, owner)
        self.assertTrue(fight.is_dead(owner))
        self.assertEqual(fight.vitals[old_body].killer, victim.body)
        for _ in range(round(combat.respawn_seconds(run.match) / combat.TICK) + 2):
            if owner.body != old_body:
                break
            run.step({})
        self.assertNotEqual(owner.body, old_body)
        self.assertTrue(old.retired)
        self.assertNotIn(old_body, fight.reaper_blossoms)
        self.assertEqual(self.charge(owner), 0)
        x, y, z = owner.mover.position
        victim.mover.position[:] = (x, y, z + 1.5)
        victim.mover.body = None
        for _ in range(15):
            run.step({})
        enemy = fight.vitals_of(target)
        before = enemy.total
        run.step({owner: (0, 0, 32)})
        self.assertIsNone(reaper.blossom_state(owner.body_script))
        self.assertEqual(enemy.total, before)
        run.step({})
        owner.body_script.set_ult_charge(2000)
        self.fire(run, owner, victim)
        self.assertEqual(enemy.total, before - 120)
        self.assertEqual(self.charge(owner), 2100)
        run.step({owner: (0, 0, 32)})
        self.assertEqual(self.charge(owner), 0)
        self.assertEqual(enemy.total, before - 154)

    def test_actual_shotgun_damage_awards_once_without_reaping_feedback(self):
        run, owner, victim, target = self.scene()
        fight = run.match.combat
        fight.vitals[owner.body].damage(100)
        self.fire(run, owner, victim)
        self.assertEqual(fight.vitals_of(target).total, 80)
        self.assertAlmostEqual(fight.vitals[owner.body].total, 150 + 120 * reaper.REAPING_RATIO)
        self.assertEqual(self.charge(owner), 120)

    def roadhog_target(self):
        run, owner, victim, _ = self.scene()
        position = tuple(victim.mover.position)
        self.assertTrue(run.match.switch_hero(victim, find_hero("Roadhog")))
        victim.mover.position[:] = position
        victim.mover.body = None
        for _ in range(15):
            run.step({})
        fight = run.match.combat
        health = fight.vitals_of(next(t for t in fight._targets() if t.owner is victim))
        return run, owner, victim, health

    def test_actual_roadhog_tag_reduces_reaper_charge_without_reducing_reaping(self):
        for melee, expected in ((False, 120), (True, 30)):
            with self.subTest(melee=melee):
                run, owner, victim, health = self.roadhog_target()
                before = health.total
                own = run.match.combat.vitals[owner.body]
                own.damage(100)
                if melee:
                    run.step({owner: (0, 0, 0x800)})
                    for _ in range(40):
                        run.step({})
                else:
                    self.fire(run, owner, victim)
                self.assertEqual(before - health.total, expected)
                self.assertAlmostEqual(own.total, 150 + expected * reaper.REAPING_RATIO)
                self.assertEqual(self.charge(owner), expected * 0.75)

    def test_actual_reaper_shell_uses_live_pve_tag_before_roadhog_tag(self):
        run, owner, victim, health = self.roadhog_target()
        component = victim.body_script.component
        # The real 0E62 marker is exercised on a controlled overlapping target.
        tagged = component.create(graph.graph(0x0E62), init=False)
        marker = tagged.state(7)
        marker.begin()
        initial = health.total
        self.fire(run, owner, victim)
        self.assertEqual(health.total, initial - 120)
        self.assertEqual(self.charge(owner), 60)
        marker.end(False)
        for _ in range(35):
            run.step({})
        self.fire(run, owner, victim)
        self.assertEqual(health.total, initial - 240)
        self.assertEqual(self.charge(owner), 150)

    def test_actual_shell_respects_authored_target_charge_exclusions(self):
        for flag in (6442, 1363):
            with self.subTest(flag=flag):
                run, owner, victim, target = self.scene()
                fight = run.match.combat
                health = fight.vitals_of(target)
                own = fight.vitals[owner.body]
                own.damage(100)
                victim.body_script.component.set(flag, True)
                self.fire(run, owner, victim)
                self.assertEqual(health.total, 80)
                self.assertAlmostEqual(own.total, 150 + 120 * reaper.REAPING_RATIO)
                self.assertEqual(self.charge(owner), 0)
                victim.body_script.component.set(flag, False)
                for _ in range(35):
                    run.step({})
                self.fire(run, owner, victim)
                self.assertTrue(health.dead)
                self.assertEqual(self.charge(owner), 80)

    def test_actual_shell_respects_live_1503_charge_stack(self):
        run, owner, victim, target = self.scene()
        fight = run.match.combat
        health = fight.vitals_of(target)
        own = fight.vitals[owner.body]
        own.damage(100)
        component = victim.body_script.component
        marker = component.create(graph.graph(0x1503), init=False)
        stack = marker.node_state(marker.graph.node(32))
        stack.begin()
        self.assertTrue(component.entity_var(602).value())
        self.fire(run, owner, victim)
        self.assertEqual(health.total, 80)
        self.assertAlmostEqual(own.total, 150 + 120 * reaper.REAPING_RATIO)
        self.assertEqual(self.charge(owner), 0)
        stack.end(False)
        self.assertFalse(component.entity_var(602).value())
        for _ in range(35):
            run.step({})
        self.fire(run, owner, victim)
        self.assertTrue(health.dead)
        self.assertEqual(self.charge(owner), 80)

    def test_actual_shell_routes_charge_tags_through_authored_entry_gates(self):
        for node, flag, expected in ((30, False, 0), (36, False, 0), (86, False, 120),
                                     (86, True, 0), (None, True, 120)):
            with self.subTest(node=node, flag=flag):
                run, owner, victim, target = self.scene()
                fight = run.match.combat
                source = owner.body_script.component
                charge = next(i for i in source.instances.values() if i.graph.index == damage_charge.GRAPH)
                tags = () if node is None else (charge.evaluate(charge.graph.node(node).field("m_value")),)
                victim.body_script.component.set(20682, flag)
                own = fight.vitals[owner.body]
                own.damage(100)
                # Hold the native message tags constant while driving the actual pellet volley.
                with patch.object(damage_charge, "event_tags", return_value=tags):
                    self.fire(run, owner, victim)
                self.assertEqual(fight.vitals_of(target).total, 80)
                self.assertAlmostEqual(own.total, 150 + 120 * reaper.REAPING_RATIO)
                self.assertEqual(self.charge(owner), expected)

    def test_authored_reaper_effects_expose_both_damage_charge_exclusion_tags(self):
        _, owner, _, _ = self.scene()
        component = owner.body_script.component
        for index, pos, tag in ((0x0044, 79, 0x0D800000000001FD),
                                (0x0075, 38, 0x0D8000000000025A)):
            instance = next(i for i in component.instances.values() if i.graph.index == index)
            node = instance.graph.node(pos)
            state = instance.node_state(node)
            definition = combat.hit_def(state, {"m_modifyHealth": node.field("m_modifyHealth")})
            self.assertIn(expr.Asset(tag), definition.damage_tags)

    def test_actual_practice_range_tagged_bots_respect_charge_faction(self):
        run = Run(PRACTICE_RANGE, names=("Reaper",))
        (owner,) = run.players
        self.assertTrue(run.match.switch_hero(owner, find_hero("Reaper")))
        hostile = next(bot for bot in run.match.bots if bot.body == 0x0400000000001403)
        friendly = next(bot for bot in run.match.bots if bot.body == 0x040000000000143E)
        self.assertNotEqual(hostile.team, friendly.team)
        run.match.bots[:] = [hostile, friendly]
        owner.team = friendly.team
        owner.mover.position[:] = (0, 1, 0)
        owner.mover.body = None
        for bot, x in ((hostile, -1.5), (friendly, 1.5)):
            bot.position, bot.route = (x, 1, 2), []
        owner.body_script.ult_seconds = 0
        owner.body_script.set_ult_charge(0)
        fight = run.match.combat
        targets = {target.entity: fight.vitals_of(target) for target in fight._targets()}
        friendly_hp, hostile_hp = targets[friendly.entity], targets[hostile.entity]
        initial = friendly_hp.total, hostile_hp.total
        for bot in (friendly, hostile):
            x, y, z = bot.position
            run.step({owner: (*aim(fight._eye(owner), (x, y + 1, z)), 1)})
            if bot is friendly:
                self.assertEqual(self.charge(owner), 0)
                for _ in range(35):
                    run.step({})
        self.assertEqual(friendly_hp.total, initial[0])
        self.assertLess(hostile_hp.total, initial[1])
        self.assertEqual(self.charge(owner), initial[1] - hostile_hp.total)

    def test_actual_dva_mech_and_pilot_award_their_post_armour_loss(self):
        if (defined_mover_data(0x0400000000000407) is None
                or graph.entity_body(0x0400000000000407) is None):
            self.skipTest("requires the pinned D.Va supplements")
        for body, expected in ((0x0400000000000407, 60), (0x040000000000040A, 120)):
            with self.subTest(body=body):
                run, owner, victim, _ = self.scene()
                position = tuple(victim.mover.position)
                self.assertTrue(run.match.switch_hero(victim, replace(find_hero("Dva"), body=body)))
                victim.mover.position[:] = position
                victim.mover.body = None
                for _ in range(15):
                    run.step({})
                fight = run.match.combat
                health = fight.vitals_of(next(t for t in fight._targets() if t.owner is victim))
                before = health.total
                self.fire(run, owner, victim)
                self.assertEqual(before - health.total, expected)
                self.assertEqual(self.charge(owner), expected)

    def test_armour_overkill_and_corpse_award_only_actual_damage(self):
        for armour, health, expected in ((100, 200, 60), (0, 5, 5)):
            with self.subTest(armour=armour):
                run, owner, victim, target = self.scene()
                fight = run.match.combat
                fight.vitals[victim.body] = combat.Vitals(victim.body, health, armour=armour)
                self.fire(run, owner, victim)
                self.assertEqual(self.charge(owner), expected)
                if health == 5:
                    self.assertTrue(fight.is_dead(victim))
                    fight._damage(owner, target, 1000, False, None, run.frame)
                    self.assertEqual(self.charge(owner), 5)

    def test_melee_awards_and_miss_wall_and_ally_do_not(self):
        run, owner, victim, target = self.scene()
        run.step({owner: (0, 0, 0x800)})
        for _ in range(40):
            run.step({})
        self.assertEqual(run.match.combat.vitals_of(target).total, 170)
        self.assertEqual(self.charge(owner), 30)
        for reason in ("miss", "wall", "ally"):
            with self.subTest(reason=reason):
                run, owner, victim, _ = self.scene(team_mode=reason == "ally")
                if reason == "ally":
                    victim.team = owner.team
                with patch.object(combat, "ray_world", return_value=0.01 if reason == "wall" else None):
                    if reason == "miss":
                        run.step({owner: (-32768, 0, 1)})
                    else:
                        self.fire(run, owner, victim)
                self.assertEqual(self.charge(owner), 0)

    def test_damage_multipliers_cap_and_real_ultimate_consumption(self):
        run, owner, victim, _ = self.scene()
        component = owner.body_script.component
        component.set(9532, 2.0)
        component.set(13022, 0.5)
        self.fire(run, owner, victim)
        self.assertEqual(self.charge(owner), 120)
        owner.body_script.set_ult_charge(2099)
        for _ in range(33):
            self.fire(run, owner, victim)
        self.assertEqual(self.charge(owner), 2100)
        run.step({})
        run.step({owner: (0, 0, 32)})
        self.assertIsNotNone(reaper.blossom_state(owner.body_script))
        self.assertEqual(self.charge(owner), 0)
        for _ in range(190):
            run.step({})
        self.assertIsNone(reaper.blossom_state(owner.body_script))
        self.assertEqual(self.charge(owner), 0)

    def test_passive_uses_authored_five_points_per_second_and_rate_multipliers(self):
        run, owner, _, _ = self.scene()
        owner.body_script.ult_seconds = 60
        for _ in range(62):
            run.step({})
        self.assertEqual(self.charge(owner), 0)
        run.step({})
        self.assertEqual(self.charge(owner), 5)
        for _ in range(125):
            run.step({})
        self.assertEqual(self.charge(owner), 15)
        component = owner.body_script.component
        component.set(9532, 2)
        component.set(12918, 3)
        for _ in range(63):
            run.step({})
        self.assertEqual(self.charge(owner), 45)
        owner.body_script.set_ult_charge(2099)
        for _ in range(63):
            run.step({})
        self.assertEqual(self.charge(owner), 2100)

    def test_explicit_eligibility_charge_and_channel_blocks_are_respected(self):
        for values in ({2426: 0}, {9530: False}, {10482: False, 10480: False}, {842: True}):
            with self.subTest(values=values):
                run, owner, victim, _ = self.scene()
                script = owner.body_script
                script.ult_seconds = 60
                for key, value in values.items():
                    script.component.set(key, value)
                self.fire(run, owner, victim)
                for _ in range(80):
                    run.step({})
                self.assertEqual(self.charge(owner), 0)
                self.assertEqual(script.reaper_charge_ms, 0)

    def test_damage_and_passive_specific_blocks_are_independent(self):
        run, owner, victim, _ = self.scene()
        script = owner.body_script
        script.ult_seconds = 60
        script.component.set(12321, True)
        self.fire(run, owner, victim)
        self.assertEqual(self.charge(owner), 0)
        for _ in range(63):
            run.step({})
        self.assertEqual(self.charge(owner), 5)
        script.component.set(12321, False)
        script.component.set(11942, True)
        for _ in range(40):
            run.step({})
        self.fire(run, owner, victim)
        self.assertEqual(self.charge(owner), 85)  # remaining enemy80, not a full120 shell
        for _ in range(80):
            run.step({})
        self.assertEqual(self.charge(owner), 85)

    def test_death_and_duplicate_command_cannot_award_passive_or_damage_charge(self):
        run, owner, victim, target = self.scene()
        script = owner.body_script
        script.ult_seconds = 60
        for _ in range(30):
            run.step({})
        remainder = script.reaper_charge_ms
        owner.take_commands([Command(run.frame, buttons=1)] * 3)
        self.assertEqual(script.reaper_charge_ms, remainder)
        self.assertEqual(self.charge(owner), 0)
        fight = run.match.combat
        own_target = next(t for t in fight._targets() if t.owner is owner)
        fight._damage(victim, own_target, 1000, False, None, run.frame)
        fight._damage(owner, target, 50, False, None, run.frame)
        for _ in range(80):
            run.step({})
        self.assertEqual(self.charge(owner), 0)
        self.assertEqual(script.reaper_charge_ms, 0)

    def world_step(self, run, count=1):
        run.frame += count
        run.match.update(run.frame * combat.TICK, run.frame, frames=False)

    def test_standalone_driver_retains_explicit_command_clock(self):
        from ow174.game.script.driver import BodyScript

        for quanta, frames, remainder in ((8000, 250, 0), (32000, 63, 16)):
            with self.subTest(quanta=quanta):
                script = BodyScript(reaper.HERO, 0xA0000001, 0, quanta=quanta)
                for frame in range(1, frames + 1):
                    script.command(frame, 0)
                self.assertEqual(script.component.vars[342].value(), 10)
                self.assertEqual(script.reaper_charge_ms, remainder)
                self.assertIsNone(script.reaper_charge_tick)

    def test_large_world_gap_zero_rate_cap_and_new_body_clock(self):
        from ow174.game.content import SOLDIER, heroes

        run, owner, _, _ = self.scene()
        old = owner.body_script
        old.ult_seconds = 60
        old.component.set(9532, 0)
        self.world_step(run, 1000000)
        self.assertEqual(self.charge(owner), 0)
        old.component.set(9532, 1)
        self.world_step(run, 1000001)
        self.assertEqual(self.charge(owner), 2100)
        self.assertEqual(old.reaper_charge_ms, 0)
        old_tick, old_remainder = old.reaper_charge_tick, old.reaper_charge_ms
        self.assertTrue(run.match.switch_hero(owner, heroes()[SOLDIER]))
        self.assertTrue(run.match.switch_hero(owner, heroes()[reaper.HERO]))
        current = owner.body_script
        self.assertTrue(old.retired)
        self.assertEqual(current.reaper_charge_tick, run.match.tick)
        self.world_step(run, 62)
        self.assertEqual(self.charge(owner), 0)
        self.world_step(run)
        self.assertEqual(self.charge(owner), 5)
        self.assertEqual((old.reaper_charge_tick, old.reaper_charge_ms), (old_tick, old_remainder))

    def test_world_charge_advances_without_input_and_cannot_double_with_future_commands(self):
        for inputs in (False, True):
            with self.subTest(inputs=inputs):
                run, owner, _, _ = self.scene()
                script = owner.body_script
                script.ult_seconds = 60
                start = script.frame
                last_input = owner.commands.last_frame
                for _ in range(125):
                    if inputs:
                        run.step({})
                    else:
                        self.world_step(run)
                self.assertEqual(self.charge(owner), 10)
                self.assertEqual(script.reaper_charge_ms, 0)
                self.assertEqual(script.frame - start, 125 if inputs else 124)
                self.assertEqual(owner.commands.last_frame, run.frame if inputs else last_input)
                script.command(script.frame + 200, 0)
                self.assertEqual(self.charge(owner), 10)
                self.assertEqual(script.reaper_charge_ms, 0)
                run.match.combat.update(run.frame * combat.TICK, run.frame, frames=False)
                self.assertEqual(self.charge(owner), 10)

    def test_skipped_world_ticks_equal_contiguous_ticks_and_disabled_time_is_not_banked(self):
        for skip in (False, True):
            with self.subTest(skip=skip):
                run, owner, _, _ = self.scene()
                script = owner.body_script
                script.ult_seconds = 60
                if skip:
                    self.world_step(run, 156)
                else:
                    for _ in range(156):
                        self.world_step(run)
                self.assertEqual(self.charge(owner), 10)
                self.assertEqual(script.reaper_charge_ms, 496)
                script.component.set(11942, True)
                self.world_step(run, 1000)
                self.assertEqual(self.charge(owner), 10)
                self.assertEqual(script.reaper_charge_ms, 0)
                script.component.set(11942, False)
                self.world_step(run, 62)
                self.assertEqual(self.charge(owner), 10)
                self.world_step(run)
                self.assertEqual(self.charge(owner), 15)

    def test_world_channel_expiration_resumes_charge_without_owner_input(self):
        for skip in (False, True):
            with self.subTest(skip=skip):
                run, owner, _, _ = self.scene()
                script = owner.body_script
                script.ult_seconds = 60
                script.set_ult_charge(2100)
                run.step({owner: (0, 0, 32)})
                channel = run.match.combat.reaper_blossoms[owner.body]
                self.assertIsNotNone(reaper.blossom_state(script))
                if skip:
                    self.world_step(run, 313)
                else:
                    for _ in range(313):
                        self.world_step(run)
                self.assertTrue(channel["ended"])
                self.assertIsNone(reaper.blossom_state(script))  # world-filled input windows expire the VM
                self.assertEqual(self.charge(owner), 10)
                self.assertLess(script.reaper_charge_ms, 32)

    def test_interrupted_world_channel_resumes_from_actual_end_and_death_clears_remainder(self):
        run, owner, victim, _ = self.scene()
        script = owner.body_script
        script.ult_seconds = 60
        script.set_ult_charge(2100)
        run.step({owner: (0, 0, 32)})
        self.world_step(run, 30)
        self.assertTrue(reaper.interrupt_blossom(owner))
        run.match.combat.command(owner, None)
        channel = run.match.combat.reaper_blossoms[owner.body]
        self.assertAlmostEqual(channel["end"], run.match.combat.now)
        self.world_step(run, 63)
        self.assertEqual(self.charge(owner), 5)
        fight = run.match.combat
        own_target = next(t for t in fight._targets() if t.owner is owner)
        fight._damage(victim, own_target, 1000, False, None, run.frame)
        self.world_step(run, 125)
        self.assertEqual(self.charge(owner), 5)
        self.assertEqual(script.reaper_charge_ms, 0)


if __name__ == "__main__":
    unittest.main()
