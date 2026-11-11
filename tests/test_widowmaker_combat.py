"""Actual command-to-pool regressions; these are not original-client acceptance.

Use extracted 104319 body/weapon graphs, flat collision and acknowledged fake
peers. NPC coverage here is a training bot, not the five Archives enemy classes.
"""

import unittest
from dataclasses import replace
from unittest.mock import patch

from test_game_combat import KANEZAKA_DM, Run, aim

from ow174.game import bots, combat
from ow174.game.collision import World, flat_world
from ow174.game.commands import Command
from ow174.game.content import heroes
from ow174.game.script.expr import Asset, Entity
from ow174.game.script.graph import graph

WIDOWMAKER = 0x02E000000000000A
FIRE, SCOPE, MELEE = 1, 2, 0x800


class _WidowmakerMatch:
    def setUp(self):
        self.run = Run(replace(KANEZAKA_DM, free_for_all=False), ("Widowmaker", "Enemy"))
        self.a, self.b = self.run.players
        self.run.match.switch_hero(self.a, heroes()[WIDOWMAKER])
        self.run.match.bots.clear()
        self.a.mover.position[:] = (0.0, 0.0, 0.0)
        self.b.mover.position[:] = (0.0, 0.0, 1.5)
        self.run.match.collision_world = flat_world(0)
        for _ in range(60):
            self.run.step({})

    def target(self, owner):
        return next(t for t in self.run.match.combat._targets() if t.owner is owner)

    def health(self, owner):
        return self.run.match.combat.vitals_of(self.target(owner)).total

    def press(self, buttons, frames=1, *, attacker=None, defender=None, height=1.0):
        attacker, defender = attacker or self.a, defender or self.b
        x, y, z = defender.mover.position
        direction = aim(self.run.match.combat._eye(attacker), (x, y + height, z))
        for _ in range(frames):
            self.run.step({attacker: (*direction, buttons)})


class WidowmakerCombatTests(_WidowmakerMatch, unittest.TestCase):
    def test_primary_body_hit_and_release(self):
        self.press(FIRE)
        self.press(0, 20)
        self.assertEqual(self.health(self.b), 187.0)
        self.assertEqual(self.a.client.messages(combat.HIT)[0]["+0x78"]["+0x4"], 13)

    def test_full_charge_scoped_body_hit_consumes_five_ammo(self):
        weapon = next(i for i in self.a.body_script.component.instances.values() if i.graph.index == 0x1B)
        before = weapon.vars[53].value()
        self.press(SCOPE, 100)
        self.press(FIRE | SCOPE)
        self.assertEqual(self.health(self.b), 80.0)
        self.assertEqual(before - weapon.vars[53].value(), 5)

    def test_partial_charge_is_weaker_than_full_charge(self):
        self.press(SCOPE, 25)
        self.press(FIRE | SCOPE)
        dealt = 200.0 - self.health(self.b)
        self.assertGreaterEqual(dealt, 12.0)
        self.assertLess(dealt, 120.0)

    def test_primary_head_hit_deals_double_damage_and_reports_critical(self):
        self.press(FIRE, height=combat.EYE)
        self.press(0, 20)
        self.assertEqual(self.health(self.b), 174.0)
        self.assertEqual(self.a.client.messages(combat.HIT)[0]["+0x78"]["+0x8"], combat.CRITICAL)

    def test_fully_scoped_head_hit_deals_300_and_can_kill(self):
        self.run.match.switch_hero(self.b, heroes()[0x02E0000000000040])  # Roadhog:600 health
        self.press(SCOPE, 100, height=combat.EYE)
        self.press(SCOPE | FIRE, height=combat.EYE)
        self.assertEqual(self.health(self.b), 300.0)
        self.press(SCOPE, 100, height=combat.EYE)
        self.press(SCOPE | FIRE, height=combat.EYE)
        self.assertEqual(self.health(self.b), 0.0)
        last = self.a.client.messages(combat.HIT)[-1]["+0x78"]
        self.assertEqual(last["+0x4"], 300)
        self.assertEqual(last["+0x8"], combat.CRITICAL | combat.KILLING)

    def test_explicit_noncritical_scalar_overrides_widowmaker_reconstruction(self):
        weapon = next(i for i in self.a.body_script.component.instances.values() if i.graph.index == 0x1B)
        state = weapon.state(6)
        config = dict(state.node.field("m_6396149F"))
        config["m_A872C70E"] = {"$": "STUConfigVarInt", "m_value": 1}
        hit = combat.hit_def(state, config)
        target = self.target(self.b)
        self.run.match.combat._damage(
            self.a, target, hit.amount * hit.critical, False, hit.loadout, self.run.frame,
        )
        self.assertEqual(self.health(self.b), 187.0)

    def test_allied_player_is_not_damaged(self):
        self.b.team = self.a.team
        self.press(FIRE, 30)
        self.press(MELEE)
        self.press(0, 40)
        self.assertEqual(self.health(self.b), 200.0)
        self.assertEqual(self.a.client.messages(combat.HIT), [])

    def test_quick_melee_damages_enemy_once(self):
        self.press(MELEE)
        self.press(0, 40)
        self.assertEqual(self.health(self.b), 170.0)
        self.assertEqual([h["+0x78"]["+0x4"] for h in self.a.client.messages(combat.HIT)], [30])

    def test_quick_melee_recovery_rejects_repress_then_allows_new_hit(self):
        melee = next(i for i in self.a.body_script.component.instances.values() if i.graph.index == 0x43)
        self.press(MELEE)
        self.press(0)
        self.press(MELEE)
        self.press(0, 28)
        self.press(MELEE)
        self.press(0, 20)
        hit_state = melee.state(4)
        self.assertEqual(hit_state.counter, 1)
        self.assertEqual(self.health(self.b), 170)

        self.press(0, 45)
        self.press(MELEE)
        self.press(0, 40)
        self.assertEqual(hit_state.counter, 2)
        self.assertEqual(self.health(self.b), 140)
        self.assertEqual([h["+0x78"]["+0x4"] for h in self.a.client.messages(combat.HIT)], [30, 30])

    def test_melee_does_not_hit_through_wall(self):
        self.run.match.collision_world = World([
            (-5, 0, -5, -5, 0, 5, 5, 0, 5),
            (-5, 0, -5, 5, 0, 5, 5, 0, -5),
            (-5, 0, 0.75, 5, 0, 0.75, 5, 5, 0.75),
            (-5, 0, 0.75, 5, 5, 0.75, -5, 5, 0.75),
        ], name="melee wall")
        self.press(MELEE)
        self.press(0, 40)
        self.assertEqual(self.health(self.b), 200.0)
        self.assertEqual(self.a.client.messages(combat.HIT), [])

    def test_enemy_player_can_kill_widowmaker_and_dead_input_cannot_fire(self):
        self.press(FIRE, 100, attacker=self.b, defender=self.a)
        self.assertTrue(self.run.match.combat.is_dead(self.a))
        self.assertTrue(self.a.body_script.component.owner_dead)
        self.assertTrue(self.a.movement().flags & combat.DEAD)
        self.assertTrue(self.a.client.messages(combat.DEATH_NOTICE))
        self.press(FIRE, 30)
        self.assertEqual(self.health(self.b), 200.0)
        self.assertEqual(self.a.client.messages(combat.HIT), [])

    def test_manual_training_bot_corpse_stays_after_widowmaker_kills_it(self):
        # An actual Bot target through the same weapon command path. This does not
        # stand in for Archives AI, native body rendering, or their special attacks.
        from ow174.game.content import PRACTICE_RANGE

        other = Run(PRACTICE_RANGE)
        bot = next(bot for bot in other.match.bots if bot.team != other.players[0].team)
        bot.position = (0.0, 0.0, 1.5)
        bot.route = []
        bot.firing = False
        bot.retain_corpse = True
        self.run.match.bots[:] = [bot]
        self.b.team = self.a.team
        for _ in range(2):
            self.press(SCOPE, 100)
            self.press(FIRE | SCOPE)
            self.press(SCOPE, 60)
        self.assertTrue(bot.dead)
        for _ in range(round(combat.BOT_RESPAWN / combat.TICK) + 10):
            self.run.step({})
        self.assertIn(bot, self.run.match.bots)
        self.assertEqual(self.health(bot), 0)
        self.assertTrue(bot.movement(self.run.frame).flags & bots.DEAD)
        self.assertEqual(self.health(self.b), 200.0)
        self.assertFalse(any(who is bot for _, who in self.run.match.combat.respawns))


class WidowmakerInfraSightTests(_WidowmakerMatch, unittest.TestCase):
    # Keep these as independent executed match cases, not a ready-made target list.
    def infra(self):
        return next(i for i in self.a.body_script.component.instances.values() if i.graph.index == 0x67)

    def activate(self):
        script = self.a.body_script
        script.ult_seconds = 0  # isolate charge spending from the optional accelerated sandbox fill
        script.set_ult_charge(script.component.vars[338].value())
        self.press(0x20)
        self.press(0, 45)

    def test_infra_sight_requires_charge_and_spends_it_on_start(self):
        self.a.body_script.ult_seconds = 0
        self.a.body_script.set_ult_charge(0)
        self.press(0x20)
        self.press(0, 45)
        self.assertFalse(self.run.match.combat.revealed_to(self.a, self.b.body))
        self.assertFalse(self.infra().state(0).active)
        self.activate()
        self.assertEqual(self.a.body_script.component.vars[342].value(), 0)
        self.assertTrue(self.run.match.combat.revealed_to(self.a, self.b.body))
        self.assertIn(self.b.body, [entity for entity, _ in self.infra().state(0).payload()["targets"]])

    def test_infra_damage_message_replaces_stale_deduction_with_authored_empty_array(self):
        script = self.a.body_script
        script.ult_seconds = 0
        script.set_ult_charge(0)
        match = self.run.match
        charge_graph = next(i for i in script.component.instances.values() if i.graph.index == 0xB8B)
        charge_graph.write((0, 16102), (99,))
        self.assertTrue(match.queue_body_message(self.a.body, 0x024000000000002D,
                                                {0x29: Asset(0x0D8000000000018D)}, self.a.body))
        match.deliver_body_messages()
        world_type = type(script.component.world)
        original = world_type.native

        def predicates(world, config, instance):
            kind = config.get("$")
            if instance is charge_graph and kind in ("STU_AC829876", "STU_A58C1E63", "STU_25ECB55F"):
                return kind != "STU_25ECB55F"
            return original(world, config, instance)

        params = {0x13: 13, 0x15: Entity(self.b.body), 0x624: 7}
        with patch.object(world_type, "native", predicates):
            self.assertTrue(match.queue_body_message(self.a.body, 0x0240000000000012,
                                                     params, self.a.body))
            match.deliver_body_messages()
        self.assertEqual(charge_graph.vars[16100].value(), 7)
        self.assertEqual(charge_graph.vars[16102].value(), ())
        self.assertEqual(charge_graph.vars[336].value(), 13)
        self.assertEqual(script.component.vars[342].value(), 13)

    def test_infra_eligibility_reads_live_target_stack_without_creating_foreign_storage(self):
        owner = self.a.body_script.component
        target = self.b.body_script.component
        charge = next(i for i in owner.instances.values() if i.graph.index == 0xB8B)
        charge.write((0, 393), Entity(self.b.body))
        eligibility = charge.graph.node(53).field("m_condition")["m_configVars"][3]
        condition = eligibility["m_filter"]["m_condition"]
        original_slot = target.vars.get(28985)
        owner_slot = owner.vars.get(28985)
        self.assertIsNone(charge.evaluate(condition))
        self.assertIs(target.vars.get(28985), original_slot)
        self.assertNotIn(0xFFFF, target.vars)
        self.assertIsNone(owner.world.entity_var(self.b.body, 0xFFFF))
        self.assertNotIn(0xFFFF, target.vars)

        definition = graph(0x1544)
        writer = target.create(definition, init=False)
        self.assertTrue(writer.run_node(definition.node(54), None))
        writer.drain()
        self.assertTrue(writer.node_state(definition.node(66)).active)
        self.assertIs(charge.evaluate(condition), True)
        self.assertIs(owner.vars.get(28985), owner_slot)

        target.destroy(writer)
        self.assertIsNone(charge.evaluate(condition))
        self.assertFalse(target.vars[28985].links)

    def test_enemy_player_trades_damage_across_infra_visibility_window(self):
        script = self.a.body_script
        script.ult_seconds = 0
        charge = script.component.entity_var(342)
        cost = script.component.entity_var(338).value()
        script.set_ult_charge(cost - 120)
        fight = self.run.match.combat

        self.press(SCOPE, 100)
        self.press(SCOPE | FIRE)
        self.assertEqual(self.health(self.b), 80)
        self.assertEqual(charge.value(), cost)
        self.press(0x20)
        self.press(0, 45)
        self.assertTrue(fight.revealed_to(self.a, self.b.body))
        self.assertEqual(charge.value(), 0)

        self.run.match.collision_world = World([
            (-5, 0, 0.75, 5, 0, 0.75, 5, 5, 0.75),
            (-5, 0, 0.75, 5, 5, 0.75, -5, 5, 0.75),
        ], name="Infra-Sight cover")
        before = self.health(self.a)
        self.press(FIRE, attacker=self.b, defender=self.a)
        self.assertEqual(self.health(self.a), before)
        self.assertTrue(fight.revealed_to(self.a, self.b.body))
        self.press(0, 20, attacker=self.b, defender=self.a)

        future = self.run.frame + 1000
        self.run.match.update(future * combat.TICK, future)
        self.run.frame = future
        self.assertFalse(fight.revealed_to(self.a, self.b.body))
        self.run.match.collision_world = flat_world(0)
        self.press(FIRE, attacker=self.b, defender=self.a)
        self.assertLess(self.health(self.a), before)
        self.assertEqual(charge.value(), 0)

    def test_actual_damage_and_neutral_frames_charge_infra_through_expiry(self):
        match = self.run.match
        fight = match.combat
        script = self.a.body_script
        charge = script.component.entity_var(342)
        cost = script.component.entity_var(338).value()
        self.assertGreater(cost, 0)
        self.press(0x20)
        self.press(0, 30)
        self.assertFalse(fight.revealed_to(self.a, self.b.body))
        before = charge.value()
        self.press(FIRE)
        self.assertEqual(self.health(self.b), 187)
        passive = cost * script.frame_ms / (1000 * script.ult_seconds)
        self.assertAlmostEqual(charge.value() - before, 13 + passive, places=4)

        start = self.run.frame
        while charge.value() < cost:
            self.assertLess(self.run.frame - start, 3800)
            first = self.run.frame + 1
            self.run.frame += 32
            match.update(self.run.frame * combat.TICK, self.run.frame)
            for player in self.run.players:
                player.take_commands([Command(frame) for frame in range(first, self.run.frame + 1)])
        self.assertEqual(charge.value(), cost)
        self.press(0x20)
        self.press(0, 45)
        self.assertEqual(charge.value(), 0)
        self.assertTrue(fight.revealed_to(self.a, self.b.body))
        self.press(FIRE)
        self.assertEqual(self.health(self.b), 174)
        self.assertEqual(charge.value(), 0)

        _, _, end_ms = script.infra_window()
        before_end = (end_ms - 1) // script.frame_ms
        after_end = (end_ms + script.frame_ms - 1) // script.frame_ms
        for frame, visible in ((before_end, True), (after_end, False)):
            match.update(frame * combat.TICK, frame)
            self.assertEqual(fight.revealed_to(self.a, self.b.body), visible)
        self.run.frame = after_end
        self.run.step({})
        self.assertGreater(charge.value(), 0)
        self.assertFalse(fight.revealed_to(self.a, self.b.body))

    def test_infra_sight_shares_with_allies_through_walls_and_expires_without_refresh(self):
        from test_game_combat import in_world

        from ow174.game.content import SOLDIER

        ally = self.run.match.add_player(3, "Ally", SOLDIER, self.a.team, False)
        in_world(self.run.match, ally)
        self.run.match.switch_hero(ally, heroes()[SOLDIER])
        self.run.match.collision_world = World([
            (-5, 0, -5, -5, 0, 5, 5, 0, 5),
            (-5, 0, -5, 5, 0, 5, 5, 0, -5),
            (-5, 0, 0.75, 5, 0, 0.75, 5, 5, 0.75),
            (-5, 0, 0.75, 5, 5, 0.75, -5, 5, 0.75),
        ], name="reveal through wall")
        self.activate()
        fight = self.run.match.combat
        self.assertTrue(fight.revealed_to(ally, self.b.body))
        self.assertFalse(fight.revealed_to(self.b, self.a.body))
        self.assertFalse(fight.revealed_to(self.a, ally.body))
        self.press(0, 500)
        self.press(0x20)  # uncharged second press cannot refresh or extend the active reveal
        self.press(0, 500)
        self.assertFalse(self.infra().state(0).active)
        self.assertFalse(fight.revealed_to(self.a, self.b.body))
        self.assertFalse(fight.revealed_to(ally, self.b.body))
        self.assertEqual(self.health(self.b), 200)

    def test_infra_sight_range_and_source_death_remove_status(self):
        self.activate()
        fight = self.run.match.combat
        self.assertTrue(fight.revealed_to(self.a, self.b.body))
        self.b.mover.position[:] = (0, 0, 201)
        self.b.mover.body = None
        self.assertFalse(fight.revealed_to(self.a, self.b.body))
        self.b.mover.position[:] = (0, 0, 2)
        self.b.mover.body = None
        self.press(0, 2)
        self.assertTrue(fight.revealed_to(self.a, self.b.body))
        fight._damage(self.b, self.target(self.a), 200, False, None, self.run.frame)
        self.assertFalse(fight.revealed_to(self.a, self.b.body))

    def test_infra_sight_dead_target_and_free_for_all_do_not_leak_to_enemies(self):
        self.run.match.game_map = replace(self.run.match.game_map, free_for_all=True)
        self.b.team = self.a.team  # nominal same team is still hostile in FFA
        self.activate()
        fight = self.run.match.combat
        self.assertTrue(fight.revealed_to(self.a, self.b.body))
        self.assertFalse(fight.revealed_to(self.b, self.a.body))
        fight._damage(self.a, self.target(self.b), 200, False, None, self.run.frame)
        self.assertFalse(fight.revealed_to(self.a, self.b.body))

    def test_damage_charge_uses_actual_damage_caps_and_waits_for_effect_end(self):
        script = self.a.body_script
        script.ult_seconds = 0
        script.set_ult_charge(0)
        self.press(FIRE)
        self.assertEqual(script.component.vars[342].value(), 13)
        script.set_ult_charge(script.component.vars[338].value() - 5)
        self.press(0, 20)
        self.press(FIRE)
        self.assertEqual(script.component.vars[342].value(), script.component.vars[338].value())
        self.press(0x20)
        self.press(0, 45)
        self.assertTrue(script.ultimate_active())
        before = self.health(self.b)
        self.press(FIRE)
        self.assertEqual(before - self.health(self.b), 13)
        self.assertEqual(script.component.vars[342].value(), 0)
        self.press(0, 1000)
        self.assertFalse(script.ultimate_active())
        self.press(FIRE)
        self.assertEqual(script.component.vars[342].value(), 13)

    def test_misses_allies_and_dead_targets_award_no_charge(self):
        script = self.a.body_script
        script.ult_seconds = 0
        script.set_ult_charge(0)
        self.b.team = self.a.team
        self.press(FIRE, 30)
        self.assertEqual(script.component.vars[342].value(), 0)
        self.b.team = 1
        self.press(0, 20)
        self.run.step({self.a: (32767, 0, FIRE)})
        self.press(0, 20)
        self.assertEqual(script.component.vars[342].value(), 0)
        self.run.match.combat._damage(self.a, self.target(self.b), 200, False, None, self.run.frame)
        script.set_ult_charge(0)
        self.press(FIRE, 20)
        self.assertEqual(script.component.vars[342].value(), 0)

    def test_world_clock_ends_reveal_without_owner_commands(self):
        self.activate()
        self.assertTrue(self.run.match.combat.revealed_to(self.a, self.b.body))
        future = self.run.frame + 1000
        self.run.match.update(future * combat.TICK, future)
        self.assertFalse(self.run.match.combat.revealed_to(self.a, self.b.body))
        self.assertEqual(self.infra().state(0).payload()["targets"], ())


class WidowmakerVenomTests(_WidowmakerMatch, unittest.TestCase):
    def test_mine_arms_then_poisons_for_75_and_reveals_only_to_owner(self):
        owner_health = self.health(self.a)
        self.press(0x10)
        self.press(0, 30)
        mines = self.run.match.combat.venom
        self.assertEqual(len(mines.mines), 1)
        self.assertEqual(self.health(self.b), 200)
        self.assertFalse(mines.poison)
        self.press(0, 100)
        self.assertTrue(mines.poison)
        self.assertLess(self.health(self.b), 200)
        self.assertTrue(self.run.match.combat.revealed_to(self.a, self.b.body))
        self.assertFalse(self.run.match.combat.revealed_to(self.b, self.a.body))
        self.press(0, 400)
        self.assertAlmostEqual(self.health(self.b), 125, places=5)
        self.assertEqual(sum(h["+0x78"]["+0x4"] for h in self.a.client.messages(combat.HIT)), 75)
        self.assertFalse(mines.poison)
        self.assertFalse(self.run.match.combat.revealed_to(self.a, self.b.body))
        self.assertEqual(self.health(self.a), owner_health)

    def test_allies_do_not_trigger_and_cooldown_rejects_then_allows_replacement(self):
        self.b.team = self.a.team
        self.press(0x10)
        self.press(0, 100)
        mines = self.run.match.combat.venom
        first = mines.mines[0].number
        self.press(0x10)
        self.press(0, 100)
        self.assertEqual([mine.number for mine in mines.mines], [first])
        self.assertFalse(mines.flights)
        self.assertEqual(self.health(self.b), 200)
        self.press(0, 1000)
        self.press(0x10)
        self.press(0, 100)
        self.assertEqual(len(mines.mines), 1)
        self.assertGreater(mines.mines[0].number, first)
        self.assertFalse(mines.poison)

    def test_enemy_rifle_can_destroy_unarmed_mine_before_it_poisons(self):
        self.press(0x10)
        self.press(0, 30)
        mines = self.run.match.combat.venom
        mine = mines.mines[0]
        direction = aim(self.run.match.combat._eye(self.b), mine.position)
        self.run.step({self.b: (*direction, FIRE)})
        self.assertFalse(mines.mines)
        self.press(0, 400)
        self.assertFalse(mines.poison)
        self.assertEqual(self.health(self.b), 200)

        self.press(0x10)
        self.press(0, 100)
        self.assertFalse(mines.flights)
        self.assertFalse(mines.mines)
        self.assertFalse(mines.poison)
        self.assertEqual(self.health(self.b), 200)

        self.press(0, 1000)
        self.press(0x10)
        self.press(0, 30)
        self.assertEqual(len(mines.mines), 1)
        self.assertGreater(mines.mines[0].number, mine.number)
        self.press(0, 100)
        self.assertTrue(mines.poison)
        self.assertLess(self.health(self.b), 200)

    def test_enemy_melee_destroys_unarmed_mine_without_later_poison(self):
        self.press(0x10)
        self.press(0, 30)
        mines = self.run.match.combat.venom
        mine = mines.mines[0]
        direction = aim(self.run.match.combat._eye(self.b), mine.position)
        self.run.step({self.b: (*direction, MELEE)})
        for _ in range(40):
            self.run.step({self.b: (*direction, 0)})
        self.assertFalse(mines.mines)
        self.press(0, 400)
        self.assertFalse(mines.poison)
        self.assertEqual(self.health(self.b), 200)

    def test_enemy_helix_direct_hit_destroys_unarmed_mine(self):
        self.press(0x10)
        self.press(0, 30)
        mines = self.run.match.combat.venom
        direction = aim(self.run.match.combat._eye(self.b), mines.mines[0].position)
        self.run.step({self.b: (*direction, SCOPE)})
        for _ in range(40):
            self.run.step({self.b: (*direction, 0)})
        self.assertFalse(mines.mines)
        self.assertFalse(mines.poison)
        self.press(0, 400)
        self.assertEqual(self.health(self.b), 200)

    def test_helix_splash_destroys_enemy_mine_and_preserves_allied_mine(self):
        for ally in (False, True):
            with self.subTest(ally=ally):
                self.setUp()
                if ally:
                    self.b.team = self.a.team
                self.press(0x10)
                self.press(0, 30)
                mines = self.run.match.combat.venom
                mine = mines.mines[0]
                # Hit the floor0.6m to the side: outside the0.19 mine sphere,
                # inside Helix's actual blast. No direct mine collision.
                x, _, z = mine.position
                direction = aim(self.run.match.combat._eye(self.b), (x + 0.6, 0, z))
                self.run.step({self.b: (*direction, SCOPE)})
                for _ in range(40):
                    self.run.step({self.b: (*direction, 0)})
                self.assertEqual(mines.mines, [mine] if ally else [])
                self.assertFalse(mines.poison)

    def test_allied_melee_leaves_mine_armed_and_enemy_melee_cannot_reach_through_wall(self):
        self.b.team = self.a.team
        self.press(0x10)
        self.press(0, 30)
        mines = self.run.match.combat.venom
        mine = mines.mines[0]
        direction = aim(self.run.match.combat._eye(self.b), mine.position)
        self.run.step({self.b: (*direction, MELEE)})
        for _ in range(40):
            self.run.step({self.b: (*direction, 0)})
        self.assertEqual(mines.mines, [mine])
        self.assertFalse(mines.poison)

        # Put the opponent across a solid plane, still within melee radius.
        self.b.mover.position[:] = (mine.position[0], 0, mine.position[2] + 1)
        self.b.mover.body = None
        barrier = mine.position[2] + 0.5
        self.run.match.collision_world = World([
            (-50, 0, -50, -50, 0, 50, 50, 0, 50),
            (-50, 0, -50, 50, 0, 50, 50, 0, -50),
            (-50, 0, barrier, 50, 0, barrier, 50, 50, barrier),
            (-50, 0, barrier, 50, 50, barrier, -50, 50, barrier),
        ], name="mine melee wall")
        self.b.team = 1 - self.a.team
        self.press(0, 30)
        direction = aim(self.run.match.combat._eye(self.b), mine.position)
        self.run.step({self.b: (*direction, MELEE)})
        for _ in range(40):
            self.run.step({self.b: (*direction, 0)})
        self.assertEqual(mines.mines, [mine])
        self.assertEqual(self.health(self.b), 200)

    def test_static_wall_blocks_helix_direct_and_splash_damage_to_mine(self):
        self.b.team = self.a.team
        self.press(0x10)
        self.press(0, 30)
        mines = self.run.match.combat.venom
        mine = mines.mines[0]
        self.b.mover.position[:] = (mine.position[0], 0, mine.position[2] + 1)
        self.b.mover.body = None
        barrier = mine.position[2] + 0.5
        self.run.match.collision_world = World([
            (-50, 0, -50, -50, 0, 50, 50, 0, 50),
            (-50, 0, -50, 50, 0, 50, 50, 0, -50),
            (-50, 0, barrier, 50, 0, barrier, 50, 50, barrier),
            (-50, 0, barrier, 50, 50, barrier, -50, 50, barrier),
        ], name="mine splash wall")
        self.b.team = 1 - self.a.team
        direction = aim(self.run.match.combat._eye(self.b), mine.position)
        self.run.step({self.b: (*direction, SCOPE)})
        for _ in range(40):
            self.run.step({self.b: (*direction, 0)})
        self.assertEqual(mines.mines, [mine])
        self.assertFalse(mines.poison)

    def test_wall_blocks_mine_trigger_and_hero_switch_retires_ownership(self):
        self.run.match.collision_world = World([
            (-5, 0, -5, -5, 0, 5, 5, 0, 5),
            (-5, 0, -5, 5, 0, 5, 5, 0, -5),
            (-5, 0, 0.75, 5, 0, 0.75, 5, 5, 0.75),
            (-5, 0, 0.75, 5, 5, 0.75, -5, 5, 0.75),
        ], name="mine wall")
        self.press(0x10)
        self.press(0, 400)
        mines = self.run.match.combat.venom
        self.assertEqual(len(mines.mines), 1)
        self.assertFalse(mines.poison)
        self.assertEqual(self.health(self.b), 200)
        self.run.match.switch_hero(self.a, heroes()[0x02E000000000006E])
        self.assertFalse(mines.flights)
        self.assertFalse(mines.mines)
        self.assertFalse(mines.poison)


class WidowmakerGrappleTests(_WidowmakerMatch, unittest.TestCase):
    def hook(self):
        return next(i for i in self.a.body_script.component.instances.values() if i.graph.index == 0x5A)

    def wall(self, distance):
        # Both windings: a closed wall's near face must stop the mover, not
        # merely the two-sided ray. This exercises the actual swept capsule.
        faces = [(-50, 0, distance, 50, 0, distance, 50, 50, distance),
                 (-50, 0, distance, 50, 50, distance, -50, 50, distance)]
        faces += [(*face[6:9], *face[3:6], *face[0:3]) for face in faces[:]]
        self.run.match.collision_world = World([
            (-50, 0, -50, -50, 0, 50, 50, 0, 50),
            (-50, 0, -50, 50, 0, 50, 50, 0, -50),
            *faces,
        ], name="solid hook wall")

    def test_static_hook_pulls_and_swept_mover_stops_before_wall(self):
        self.wall(5)
        self.press(8, height=combat.eye_height(self.a.hero.body))
        self.press(0, 80, height=combat.eye_height(self.a.hero.body))
        self.assertGreater(self.a.mover.position[2], 4)
        self.assertLess(self.a.mover.position[2], 5)
        self.assertFalse(self.hook().state(1).active)
        self.assertGreater(self.hook().vars[61].value(), 10)
        self.assertEqual(self.health(self.b), 200)

    def test_hook_hit_cooldown_rejects_then_allows_another_actual_pull(self):
        self.wall(5)
        self.press(8, height=combat.eye_height(self.a.hero.body))
        self.press(0, 80, height=combat.eye_height(self.a.hero.body))
        counter = self.hook().state(0).counter
        self.press(8, height=combat.eye_height(self.a.hero.body))
        self.press(0, 30, height=combat.eye_height(self.a.hero.body))
        self.assertEqual(self.hook().state(0).counter, counter)
        self.press(0, 800, height=combat.eye_height(self.a.hero.body))
        self.a.mover.position[:] = (0, 0, 0)
        self.a.mover.body = None
        self.press(8, height=combat.eye_height(self.a.hero.body))
        self.press(0, 80, height=combat.eye_height(self.a.hero.body))
        self.assertGreater(self.hook().state(0).counter, counter)
        self.assertGreater(self.a.mover.position[2], 4)
        self.assertLess(self.a.mover.position[2], 5)

    def test_out_of_range_hook_does_not_move_or_damage_targets(self):
        self.wall(30)
        self.press(8, height=combat.eye_height(self.a.hero.body))
        self.press(0, 200, height=combat.eye_height(self.a.hero.body))
        self.assertAlmostEqual(self.a.mover.position[2], 0)
        self.assertFalse(self.hook().state(1).active)
        self.assertEqual(self.health(self.b), 200)
        # Declared host miss policy: release the action and allow another cast.
        # Native retail miss-cooldown equivalence is unproved.
        ability = next(s for s in self.hook().states.values()
                       if s.node.cls == 'STUStatescriptStateAbility')
        self.assertEqual(ability.remaining(), 0)
        self.assertFalse(ability.flags & (ability.RUNNING | ability.PUSHED))
        counter = self.hook().state(0).counter
        self.press(8, height=combat.eye_height(self.a.hero.body))
        self.press(0, 50, height=combat.eye_height(self.a.hero.body))
        self.assertEqual(self.hook().state(0).counter, counter + 1)

    def test_initial_hook_range_uses_twenty_not_contact_recheck_twenty_two(self):
        self.wall(19.9)
        self.press(8, height=combat.eye_height(self.a.hero.body))
        self.press(0, 160, height=combat.eye_height(self.a.hero.body))
        self.assertGreater(self.a.mover.position[2], 19)
        self.assertGreater(self.hook().vars[61].value(), 9)

        self.setUp()
        self.wall(20.1)
        self.press(8, height=combat.eye_height(self.a.hero.body))
        self.press(0, 160, height=combat.eye_height(self.a.hero.body))
        self.assertAlmostEqual(self.a.mover.position[2], 0)
        self.assertFalse(self.hook().state(1).active)

    def test_repressing_hook_during_pull_does_not_restart_cast_or_bypass_cooldown(self):
        self.wall(19)
        self.press(8, height=combat.eye_height(self.a.hero.body))
        self.press(0, 41, height=combat.eye_height(self.a.hero.body))
        self.assertTrue(self.hook().state(1).active)
        counter = self.hook().state(0).counter
        for _ in range(60):
            self.press(8, height=combat.eye_height(self.a.hero.body))
            self.press(0, height=combat.eye_height(self.a.hero.body))
        self.assertEqual(self.hook().state(0).counter, counter)
        self.assertFalse(self.hook().state(1).active)
        self.assertGreater(self.hook().vars[61].value(), 9)

    def test_jump_cancels_an_active_pull_and_death_prevents_late_pull(self):
        self.wall(20)
        self.press(8, height=combat.eye_height(self.a.hero.body))
        self.press(0, 43, height=combat.eye_height(self.a.hero.body))
        self.assertTrue(self.hook().state(1).active)
        self.press(0x40, height=combat.eye_height(self.a.hero.body))
        self.press(0, 5, height=combat.eye_height(self.a.hero.body))
        self.assertFalse(self.hook().state(1).active)
        stopped = self.a.mover.position[2]
        self.press(0, 30, height=combat.eye_height(self.a.hero.body))
        self.assertAlmostEqual(self.a.mover.position[2], stopped, places=3)

        self.setUp()
        self.wall(20)
        self.press(8, height=combat.eye_height(self.a.hero.body))
        self.press(0, 10, height=combat.eye_height(self.a.hero.body))
        self.run.match.combat._damage(self.b, self.target(self.a), 500, False, None, self.run.frame)
        self.press(0, 100, height=combat.eye_height(self.a.hero.body))
        self.assertEqual(self.health(self.a), 0)
        self.assertAlmostEqual(self.a.mover.position[2], 0)


if __name__ == "__main__":
    unittest.main()
