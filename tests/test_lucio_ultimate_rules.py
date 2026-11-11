"""Resource settings around the actual Sound Barrier graph and host fill clock."""

import unittest
from unittest.mock import patch

import test_lucio_combat as fixtures

from ow174.game import combat, lucio
from ow174.game.body_vm_snapshot import export_body_vm, snapshot_digest
from ow174.game.commands import FIRE, SECONDARY
from ow174.game.content import SOLDIER, heroes
from ow174.game.script import nodes, runtime
from ow174.game.script.driver import ULT_CHARGE, ULT_COST


class LucioUltimateRulesTests(unittest.TestCase):
    def scene(self, overrides):
        original = runtime.World.ruleset

        def ruleset(world, key):
            if world.hero == lucio.HERO and key in overrides:
                return overrides[key]
            return original(world, key)

        self.enterContext(patch.object(runtime.World, "ruleset", ruleset))
        run, player, enemy, ally = fixtures.LucioCombatTests().scenario()
        script = player.body_script
        instance = next(i for i in script.component.instances.values() if i.graph.index == 0x073A)
        self.assertEqual(script.component.entity_var(ULT_COST).value(), 2940.)
        return run, player, enemy, ally, instance.state(10)

    @staticmethod
    def charge(player):
        return player.body_script.component.entity_var(ULT_CHARGE).value()

    def test_spawn_ready_uses_authored_cost_and_casts_without_external_charge(self):
        run, player, _, ally, ability = self.scene({lucio.SPAWN_ULTIMATE_READY: True})
        self.assertEqual(self.charge(player), 2940.)
        run.step({player: (0, 0, 0x20)})
        self.assertTrue(ability.flags & nodes.Ability.RUNNING)
        self.assertEqual(self.charge(player), 0.)
        for _ in range(43):
            run.step({})
        self.assertGreater(fixtures.LucioCombatTests().health(run, ally), 200.)
        self.assertEqual(self.charge(player), 0.)

    def test_disabled_rule_masks_spawn_charge_and_blocks_cast(self):
        overrides = {lucio.ULTIMATE_ENABLED: False, lucio.SPAWN_ULTIMATE_READY: True}
        run, player, _, ally, ability = self.scene(overrides)
        script = player.body_script
        self.assertEqual(self.charge(player), 0.)

        run.step({player: (0, 0, 0x20)})
        for _ in range(43):
            run.step({})
        self.assertEqual(fixtures.LucioCombatTests().health(run, ally), 200.)
        self.assertEqual(self.charge(player), 0.)
        self.assertFalse(ability.flags & nodes.Ability.RUNNING)

        script.component.set(lucio.ULTIMATE_ENABLED, True)
        run.step({})
        self.assertEqual(self.charge(player), 2940.)
        self.assertFalse(ability.flags & nodes.Ability.RUNNING)
        run.step({player: (0, 0, 0x20)})
        self.assertTrue(ability.flags & nodes.Ability.RUNNING)

    def test_passive_rule_scales_authored_ticks_and_respects_cost_cap(self):
        overrides = {lucio.ULTIMATE_GENERATION: 2., lucio.PASSIVE_GENERATION: .25}
        run, player, _, ally, ability = self.scene(overrides)
        script = player.body_script
        for _ in range(60):
            run.step({})
        self.assertEqual(self.charge(player), 0.)
        for _ in range(5):
            run.step({})
        self.assertEqual(self.charge(player), 2.5)
        script.set_ult_charge(2939.9)
        for _ in range(65):
            run.step({})
        self.assertEqual(self.charge(player), 2940.)
        run.step({player: (0, 0, 0x20)})
        self.assertTrue(ability.flags & nodes.Ability.RUNNING)
        for _ in range(130):
            run.step({})
        self.assertEqual(self.charge(player), 0.)
        self.assertGreater(fixtures.LucioCombatTests().health(run, ally), 200.)

    def test_ordinary_frames_generate_authored_passive_and_death_cancels_wait(self):
        run, player, _, _, _ = self.scene({})
        script = player.body_script
        component = script.component
        for _ in range(60):
            run.step({})
        self.assertIs(component.entity_var(2426).value(), True)
        self.assertEqual(self.charge(player), 0.)
        for _ in range(5):
            run.step({})
        self.assertEqual(self.charge(player), 5.)
        for _ in range(65):
            run.step({})
        self.assertEqual(self.charge(player), 10.)
        script.set_health(0., 200.)
        for _ in range(70):
            run.step({})
        self.assertEqual(self.charge(player), 10.)

    def test_zero_passive_setting_blocks_authored_generation(self):
        run, player, _, _, _ = self.scene({lucio.PASSIVE_GENERATION: 0.})
        for _ in range(130):
            run.step({})
        self.assertEqual(self.charge(player), 0.)

    def test_soundwave_uses_effective_pool_loss_and_combat_rules(self):
        overrides = {lucio.PASSIVE_GENERATION: 0., lucio.ULTIMATE_GENERATION: 2.,
                     lucio.COMBAT_GENERATION: .5}
        run, player, enemy, _, _ = self.scene(overrides)
        run.match.combat.vitals[enemy.body] = combat.Vitals(enemy.body, 200, armour=50)
        run.step({player: (0, 0, SECONDARY)})
        self.assertEqual(self.charge(player), 20.)
        self.assertEqual(fixtures.LucioCombatTests().health(run, enemy), 230.)

    def test_primary_charges_only_after_hit_and_never_for_overkill(self):
        run, player, enemy, _, _ = self.scene({lucio.PASSIVE_GENERATION: 0.})
        run.match.combat.vitals[enemy.body] = combat.Vitals(enemy.body, 7)
        run.step({player: (0, 0, FIRE)})
        self.assertEqual(self.charge(player), 0.)
        for _ in range(25):
            run.step({player: (0, 0, FIRE)})
        self.assertEqual(self.charge(player), 7.)
        self.assertEqual(fixtures.LucioCombatTests().health(run, enemy), 0.)

    def test_roadhog_damage_passes_authored_three_quarter_branch(self):
        run, player, enemy, _, _ = self.scene({lucio.PASSIVE_GENERATION: 0.})
        run.match.switch_hero(enemy, heroes()[0x02E0000000000040])
        enemy.mover.position[:] = (0, 1, 4)
        run.step({player: (0, 0, SECONDARY)})
        self.assertEqual(self.charge(player), 18.75)

    def test_temporary_shield_only_hit_gives_no_charge(self):
        run, player, enemy, _, _ = self.scene({lucio.PASSIVE_GENERATION: 0.})
        vitals = combat.Vitals(enemy.body, 200)
        run.match.combat.vitals[enemy.body] = vitals
        shield = vitals.grant_temporary_shields(lucio.BARRIER_KEY, 30.)
        run.step({player: (0, 0, SECONDARY)})
        self.assertEqual(shield.current, 5.)
        self.assertEqual(self.charge(player), 0.)

    @staticmethod
    def start_healing(run, player):
        run.step({player: (0, 0, 8)})
        for _ in range(30):
            run.step({})

    def test_aura_credits_actual_self_and_ally_healing_without_overheal(self):
        overrides = {lucio.PASSIVE_GENERATION: 0., lucio.ULTIMATE_GENERATION: 2.,
                     lucio.COMBAT_GENERATION: .25}
        run, player, enemy, ally, _ = self.scene(overrides)
        self.start_healing(run, player)
        fight = run.match.combat
        for target in fight._targets():
            fight.vitals_of(target).damage(2. if target.owner is player else 3.)
        for _ in range(20):
            run.step({})
        helper = fixtures.LucioCombatTests()
        self.assertEqual(helper.health(run, player), 200.)
        self.assertEqual(helper.health(run, ally), 200.)
        self.assertEqual(helper.health(run, enemy), 197.)
        self.assertAlmostEqual(self.charge(player), 2.5, places=5)
        for _ in range(8):
            run.step({})
        self.assertAlmostEqual(self.charge(player), 2.5, places=5)

    def test_amp_sends_authored_healing_tags_and_charge(self):
        run, player, _, ally, _ = self.scene({lucio.PASSIVE_GENERATION: 0.})
        self.start_healing(run, player)
        fight = run.match.combat
        target = next(target for target in fight._targets() if target.owner is ally)
        run.step({player: (0, 0, 16)})
        for _ in range(20):
            run.step({})
        fight.vitals_of(target).damage(20.)
        events = []
        deliver = runtime.Instance.deliver

        def capture(instance, event):
            if instance.graph.index == 0x0B8B and event.message[0] == 0x0240000000000044:
                events.append(event.message[1])
            return deliver(instance, event)

        with patch.object(runtime.Instance, "deliver", capture):
            for _ in range(8):
                run.step({})
        self.assertTrue(events)
        self.assertTrue(any([tag.guid & 0xFFFF for tag in event[0x41]] == [0x1DC2, 0x34C1, 0x34C2]
                            for event in events))
        actual = fixtures.LucioCombatTests().health(run, ally) - 180.
        self.assertAlmostEqual(actual, 8 * 52 * combat.TICK, places=5)
        self.assertAlmostEqual(self.charge(player), actual, places=5)

    def test_aura_heals_without_charge_while_barrier_is_running(self):
        run, player, _, _, ability = self.scene({lucio.SPAWN_ULTIMATE_READY: True})
        self.start_healing(run, player)
        fight = run.match.combat
        target = next(target for target in fight._targets() if target.owner is player)
        fight.vitals_of(target).damage(30.)
        run.step({player: (0, 0, 0x20)})
        for _ in range(20):
            run.step({})
        self.assertTrue(ability.flags & nodes.Ability.RUNNING)
        self.assertGreater(fight.vitals_of(target).now[combat.HEALTH_POOL], 170.)
        self.assertEqual(self.charge(player), 0.)

    def test_damage_charges_from_zero_then_barrier_expires_and_credit_resumes(self):
        rules = {lucio.ULTIMATE_GENERATION: 5., lucio.COMBAT_GENERATION: 5.,
                 lucio.PASSIVE_GENERATION: 0.}
        run, player, enemy, ally, ability = self.scene(rules)
        helper = fixtures.LucioCombatTests()
        fight = run.match.combat
        ally_target = next(row for row in fight._targets() if row.owner is ally)
        ally_pools = fight.vitals_of(ally_target)
        self.assertEqual(self.charge(player), 0.)
        run.step({player: (0, 0, 0x20)})
        self.assertFalse(ability.flags & nodes.Ability.RUNNING)
        self.assertFalse(ally_pools.temporary_shields)

        for _ in range(150):
            run.step({player: (0, 0, FIRE)})
            if self.charge(player) >= 2940.:
                break
        self.assertEqual(helper.health(run, enemy), 80.)
        self.assertEqual(self.charge(player), 2940.)
        self.assertFalse(ability.flags & nodes.Ability.RUNNING)

        run.step({player: (0, 0, 0x20)})
        self.assertTrue(ability.flags & nodes.Ability.RUNNING)
        self.assertEqual(self.charge(player), 0.)
        for _ in range(43):
            run.step({})
        self.assertTrue(ally_pools.temporary_shields)
        for _ in range(490):
            run.step({})
        self.assertFalse(ability.flags & nodes.Ability.RUNNING)
        self.assertFalse(ally_pools.temporary_shields)
        self.assertEqual(self.charge(player), 0.)

        health = helper.health(run, enemy)
        run.step({player: (0, 0, SECONDARY)})
        self.assertEqual(helper.health(run, enemy), health - 25.)
        self.assertEqual(self.charge(player), 625.)

    def test_passive_charge_advances_without_commands_and_does_not_replay_old_q(self):
        run, player, _, _, ability = self.scene({})
        script = player.body_script
        script.set_ult_charge(2935.)
        run.step({player: (0, 0, 0x20)})
        run.step({})
        command_frame = script.frame
        for _ in range(125):
            run.frame += 1
            run.match.update(run.frame * .016, run.frame)
        self.assertEqual(script.frame, command_frame)
        self.assertEqual(self.charge(player), 2940.)
        self.assertFalse(ability.flags & nodes.Ability.RUNNING)
        run.step({})
        self.assertFalse(ability.flags & nodes.Ability.RUNNING)
        run.step({player: (0, 0, 0x20)})
        self.assertTrue(ability.flags & nodes.Ability.RUNNING)

    def test_passive_charge_due_before_lethal_projectile_survives_catchup(self):
        for offset, expected in ((0, 2935), (1, 2940)):
            with self.subTest(death_minus_due=offset):
                run, player, enemy, _, _ = self.scene({})
                run.step({})
                script = player.body_script
                tick_ms = round(combat.TICK * 1000)
                due = (script.passive_ult_deadline + tick_ms - 1) // tick_ms
                impact = due + offset
                start = impact - 5
                run.match.update(start * combat.TICK, start)
                script.set_ult_charge(2935)
                fight = run.match.combat
                target = next(row for row in fight._targets() if row.owner is player)
                vitals = fight.vitals_of(target)
                vitals.damage(196)
                fight.rockets.append(combat.Rocket(
                    enemy, enemy.team, (0, 1.8, 4), (0, 0, -1), 50, 1.0,
                    combat.HitDef(20), None, last_tick=start,
                ))
                run.match.update(impact * combat.TICK, impact)
                self.assertTrue(vitals.dead)
                self.assertEqual(vitals.death_tick, impact)
                self.assertIsNone(script.passive_ult_deadline)
                self.assertEqual(self.charge(player), expected)

    def test_passive_deadline_is_stored_and_world_tick_jump_counts_once(self):
        run, player, _, _, _ = self.scene({})
        script = player.body_script
        # A single-body snapshot excludes the aura's foreign watchers.
        with patch.object(run.match.combat.lucio_auras, "refresh"):
            run.step({})
        deadline = script.passive_ult_deadline
        saved = snapshot_digest(export_body_vm(script))
        script.passive_ult_deadline += 1
        self.assertNotEqual(snapshot_digest(export_body_vm(script)), saved)
        script.passive_ult_deadline = deadline
        self.assertEqual(snapshot_digest(export_body_vm(script)), saved)

        run.frame += 625
        run.match.update(run.frame * .016, run.frame)
        self.assertEqual(self.charge(player), 50.)
        run.match.update(run.frame * .016, run.frame)
        self.assertEqual(self.charge(player), 50.)
        run.step({})
        self.assertEqual(self.charge(player), 50.)

    def test_earned_charge_survives_automatic_same_hero_respawn_only(self):
        run, player, enemy, _, _ = self.scene({lucio.PASSIVE_GENERATION: 0.})
        fight = run.match.combat
        run.step({player: (0, 0, SECONDARY)})
        self.assertEqual(self.charge(player), 25.)
        old_body = player.body
        old_script = player.body_script
        for _ in range(180):
            run.step({enemy: (32768, 0, FIRE)})
            if fight.is_dead(player):
                break
        self.assertTrue(fight.is_dead(player))
        self.assertEqual(self.charge(player), 25.)
        deadline = next(when for when, who in fight.respawns if who is player)
        run.frame = round(deadline / combat.TICK) + 1
        run.match.update(run.frame * combat.TICK, run.frame)
        self.assertNotEqual(player.body, old_body)
        self.assertTrue(old_script.retired)
        self.assertFalse(fight.is_dead(player))
        self.assertAlmostEqual(self.charge(player), 25., places=5)

        self.assertTrue(run.match.switch_hero(player, heroes()[SOLDIER]))
        self.assertEqual(self.charge(player), 0.)
        self.assertTrue(run.match.switch_hero(player, heroes()[lucio.HERO]))
        self.assertEqual(self.charge(player), 0.)

    def test_spawn_ready_rule_takes_precedence_over_retained_partial_charge(self):
        rules = {lucio.PASSIVE_GENERATION: 0.}
        run, player, _, _, _ = self.scene(rules)
        run.step({player: (0, 0, SECONDARY)})
        self.assertEqual(self.charge(player), 25.)
        rules[lucio.SPAWN_ULTIMATE_READY] = True
        self.assertTrue(run.match.switch_hero(player, heroes()[lucio.HERO]))
        self.assertEqual(self.charge(player), 2940.)

    def test_interrupted_barrier_does_not_refund_spent_charge_on_respawn(self):
        run, player, enemy, ally, ability = self.scene({lucio.PASSIVE_GENERATION: 0.})
        script = player.body_script
        script.set_ult_charge(2940.)
        run.step({player: (0, 0, 0x20)})
        self.assertTrue(ability.flags & nodes.Ability.RUNNING)
        self.assertEqual(self.charge(player), 0.)

        fight = run.match.combat
        target = next(row for row in fight._targets() if row.owner is player)
        fight._damage(enemy, target, 500., False, None, run.frame)
        for _ in range(43):
            run.step({})
        ally_target = next(row for row in fight._targets() if row.owner is ally)
        self.assertFalse(fight.vitals_of(ally_target).temporary_shields)

        deadline = next(when for when, who in fight.respawns if who is player)
        run.frame = round(deadline / combat.TICK) + 1
        run.match.update(run.frame * combat.TICK, run.frame)
        self.assertTrue(script.retired)
        self.assertFalse(fight.is_dead(player))
        self.assertEqual(self.charge(player), 0.)


if __name__ == "__main__":
    unittest.main()
