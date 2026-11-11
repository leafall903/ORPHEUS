"""Actual1157 against Zarya under declared controller/contact/health/control policy.

Native forced interaction, barrier immunity, AI and vector motion remain open.
"""

import unittest
from dataclasses import replace

import test_game_zarya_npcs as fixtures
from test_game_combat import aim, in_world

from ow174.game import combat, dva, npc, npc_control, phase
from ow174.game.body_vm_snapshot import export_component_vm, snapshot_digest
from ow174.game.collision import World
from ow174.game.commands import Command
from ow174.game.content import SOLDIER, heroes
from ow174.game.mover import defined_mover_data
from ow174.game.script import expr


@unittest.skipUnless(fixtures.NPC_DATA, "requires actual five-NPC graphs and movers")
class ZaryaAssassinTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.ZaryaFiveNpcTests.setUpClass()

    @classmethod
    def tearDownClass(cls):
        fixtures.ZaryaFiveNpcTests.tearDownClass()

    def scene(self, engage=True, distance=2.5):
        fixture = fixtures.ZaryaFiveNpcTests()
        definition = next(d for d in fixture.catalog.values() if d.name == "Assassin")
        run, player, bot = fixture.encounter(definition, z=distance)
        player.body_script.ult_seconds = 0
        if engage:
            bot.firing, bot.attack_target = True, player.entity
        target = next(t for t in run.match.combat._targets() if t.owner is player)
        return run, player, bot, run.match.combat.vitals_of(target)

    def contact(self):
        run, player, bot, health = self.scene()
        run.step({})
        self.assertEqual(health.total, 375)
        self.assertEqual(run.match.combat.pins.get(bot.entity), player.body)
        return run, player, bot, health

    def continuous(self):
        run, player, bot, health = self.contact()
        instance = self.instance(bot)
        for _ in range(100):
            state = instance.states.get(instance.graph.node(15).state)
            if state is not None and state.active:
                return run, player, bot, health, state
            run.step({})
        self.fail("actual1157 continuous phase did not start")

    @staticmethod
    def instance(bot):
        return next(i for i in bot.body_script.component.instances.values() if i.graph.index == 0x1157)

    @staticmethod
    def advance(run, frames):
        for _ in range(frames):
            run.step({})

    def test_contact_25_uses_distinct_typed_controller_and_foreign_target_member(self):
        run, player, bot, _ = self.contact()
        instance = self.instance(bot)
        controller = bot.body_script.component.world.controller_entity
        self.assertNotIn(controller, (bot.entity, player.entity, player.body))
        self.assertEqual(instance.evaluate({"$": "STU_DB742151", "m_entity": {"$": "STU_B5A0CAF0"}}),
                         expr.Entity(controller))
        self.assertEqual(instance.context().member(controller, 1650), expr.Entity(player.body))
        self.assertEqual(instance.vars[536].value(), expr.Entity(player.body))
        definition = next(d for d in fixtures.ZaryaFiveNpcTests.catalog.values() if d.name == "Trooper")
        other = npc.spawn(run.match, definition, (20, 1, 20), team=1)
        self.assertNotIn(controller, (other.entity, bot.entity))
        self.assertIsNone(bot.body_script.component.fault)

    def test_continuous_deals_16_per_ten_frames_without_duplicate_update_damage(self):
        run, _, _, health, state = self.continuous()
        before = health.total
        self.advance(run, 10)
        self.assertAlmostEqual(before - health.total, 16, places=4)
        stamp, after = state.last_applied, health.total
        run.match.update(run.frame * combat.TICK, run.frame)
        self.assertEqual(health.total, after)
        self.assertEqual(state.last_applied, stamp)

    def test_target_identity_and_continuous_clock_are_causal_snapshot_state(self):
        _, _, bot, _, state = self.continuous()
        component = bot.body_script.component
        before = snapshot_digest(export_component_vm(component))
        stamp = state.last_applied
        state.last_applied -= 16
        self.assertNotEqual(snapshot_digest(export_component_vm(component)), before)
        state.last_applied = stamp
        world, target = component.world, component.world.manual_target
        world.manual_target = None
        self.assertNotEqual(snapshot_digest(export_component_vm(component)), before)
        world.manual_target = target
        self.assertEqual(snapshot_digest(export_component_vm(component)), before)

    def test_pin_interrupts_beam_and_prevents_actions_movement_and_resource_spend(self):
        run, player, bot, health = self.scene(engage=False)
        player.body_script.set_ult_charge(2100)
        run.step({player: (0, 0, 1)})
        bot.firing, bot.attack_target = True, player.entity
        run.step({})
        self.assertEqual(health.total, 375)
        position = tuple(player.mover.position)
        weapon = next(i for i in player.body_script.component.instances.values() if i.graph.index == 0x0241)
        ammo = weapon.vars[53].value()
        for _ in range(5):
            run.frame += 1
            run.match.update(run.frame * combat.TICK, run.frame)
            player.take_commands([Command(run.frame, buttons=1|2|8|16|32|0x800, forward=127)])
        self.assertEqual(tuple(player.mover.position), position)
        self.assertEqual(weapon.vars[53].value(), ammo)
        self.assertEqual(player.body_script.component.entity_var(342).value(), 2100)
        self.assertFalse(run.match.combat.zarya_gravitons.fields)
        self.assertTrue(run.match.combat.is_stunned(player))
        npc_control.stop(run.match)
        self.assertFalse(run.match.combat.is_stunned(player))
        run.step({player: (0, 0, 8)})
        self.assertTrue(next(i for i in player.body_script.component.instances.values()
                             if i.graph.index == 0x0244).state(2).active)

    def test_precast_personal_barrier_blocks_contact_damage_and_host_control(self):
        run, player, bot, health = self.scene(engage=False)
        run.step({player: (0, 0, 8)})
        bot.firing, bot.attack_target = True, player.entity
        run.step({})
        self.assertEqual(health.total, 400)
        self.assertFalse(run.match.combat.is_stunned(player))
        self.assertAlmostEqual(player.body_script.component.entity_var(87).value(), 5)

    def test_barrier_breaking_contact_is_still_fully_blocked(self):
        run, player, bot, health = self.scene(engage=False)
        run.step({player: (0, 0, 8)})
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is player)
        self.assertFalse(fight._damage(bot, target, 195, False, None, run.frame))
        bot.firing, bot.attack_target = True, player.entity
        run.step({})
        self.assertEqual(health.total, 400)
        self.assertFalse(fight.is_stunned(player))
        self.assertAlmostEqual(player.body_script.component.entity_var(87).value(), 40)

    def test_stop_cancels_continuous_damage_and_releases_target(self):
        run, player, _, health, _ = self.continuous()
        npc_control.stop(run.match)
        before = health.total
        self.advance(run, 150)
        self.assertEqual(health.total, before)
        self.assertFalse(run.match.combat.is_stunned(player))
        self.assertFalse(run.match.combat.pins)

    def test_recovery_rejects_repeated_restart_and_reengages_held_request(self):
        run, player, bot, health = self.contact()
        npc_control.stop(run.match)
        run.step({})
        before = health.total
        release = self.instance(bot).state(self.instance(bot).graph.node(31).state)
        self.assertTrue(release.active)
        deadline = release.start + 2000
        while run.frame * 16 < deadline:
            if run.frame % 10 == 0:
                npc_control.stop(run.match)
            bot.firing, bot.attack_target = True, player.entity
            run.step({})
            self.assertEqual(health.total, before)
            self.assertFalse(run.match.combat.is_stunned(player))
        self.assertFalse(release.active)
        run.step({})
        self.assertEqual(health.total, before - 25)
        self.assertEqual(run.match.combat.pins.get(bot.entity), player.body)

    def test_released_request_does_not_reengage_when_recovery_expires(self):
        run, player, bot, health = self.contact()
        bot.firing = False
        run.step({})
        before = health.total
        bot.firing = True
        self.advance(run, 16)
        bot.firing = False
        self.advance(run, 125)
        self.assertEqual(health.total, before)
        self.assertFalse(run.match.combat.is_stunned(player))
        bot.firing = True
        run.step({})
        self.assertEqual(health.total, before - 25)

    def test_switching_target_releases_old_pin_without_skipping_recovery(self):
        run, caster, player, bot = self.ally_scene()
        caster.mover.position[:] = [1, 1, 2.5]
        fight = run.match.combat
        bot.firing, bot.attack_target = True, player.entity
        run.step({})
        self.assertEqual(fight.pins.get(bot.entity), player.body)
        bot.attack_target = caster.entity
        run.step({})
        instance = self.instance(bot)
        release = instance.state(instance.graph.node(31).state)
        self.assertTrue(release.active)
        while run.frame * 16 < release.start + 2000:
            self.assertNotIn(bot.entity, fight.pins)
            run.step({})
        self.assertNotIn(bot.entity, fight.pins)
        run.step({})
        self.assertEqual(fight.pins.get(bot.entity), caster.body)

    def test_admitted_ally_melee_ends_pin_and_preserves_recovery(self):
        run, caster, player, bot = self.ally_scene()
        caster.mover.position[:] = [1, 1, 2.5]
        fight = run.match.combat
        bot.firing, bot.attack_target = True, player.entity
        run.step({})
        self.assertTrue(fight.is_stunned(player))
        body = next(t for t in fight._targets() if t.owner is player)
        health = fight.vitals_of(body)
        before = fight.vitals[bot.entity].total
        run.step({caster: (16384, 0, 0x800)})  # Aim away.
        self.assertEqual(fight.vitals[bot.entity].total, before)
        self.assertTrue(fight.is_stunned(player))
        self.advance(run, 70)
        run.step({caster: (-16384, 0, 0x800)})
        self.assertEqual(fight.vitals[bot.entity].total, before - 30)
        self.assertFalse(fight.is_stunned(player))
        self.assertNotIn(bot.entity, fight.pins)
        after = health.total
        instance = self.instance(bot)
        release = instance.state(instance.graph.node(31).state)
        run.step({})
        self.assertEqual(health.total, after)
        self.assertTrue(release.active)
        while run.frame * 16 < release.start + 2000:
            run.step({})
            self.assertEqual(health.total, after)
        run.step({})
        self.assertEqual(health.total, after - 25)
        self.assertTrue(fight.is_stunned(player))

    def test_projected_barrier_blocks_melee_rescue_before_body_admission(self):
        run, caster, player, bot = self.ally_scene(protector=True)
        protector = run.players[2]
        caster.mover.position[:] = [1, 1, 2.5]
        protector.mover.position[:] = [0, 1, -2]
        fight = run.match.combat
        bot.firing, bot.attack_target = True, player.entity
        run.step({protector: (0, 0, 16)})
        self.assertEqual(fight.pins.get(bot.entity), player.body)
        before = fight.vitals[bot.entity].total
        run.step({caster: (-16384, 0, 0x800)})
        self.assertEqual(fight.vitals[bot.entity].total, before)
        self.assertEqual(protector.body_script.component.entity_var(87).value(), 6)
        self.assertEqual(fight.pins.get(bot.entity), player.body)

    def ally_scene(self, *, protector=False):
        names = ("Caster", "Victim", "Protector") if protector else ("Caster", "Victim")
        run = fixtures.Run(names=names)
        caster, player = run.players[:2]
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        for actor in run.players:
            actor.client = fixtures.NpcClient(actor)
            self.assertTrue(run.match.switch_hero(actor, heroes()[fixtures.ZARYA]))
            actor.team = 0
        if protector:
            run.players[2].team = 1
        caster.mover.position[:] = [0, 1, -5]
        player.mover.position[:] = [0, 1, 0]
        definition = next(d for d in fixtures.ZaryaFiveNpcTests.catalog.values() if d.name == "Assassin")
        bot = npc.spawn(run.match, definition, (0, 1, 2.5), team=1)
        return run, caster, player, bot

    def test_projected_barrier_blocks_contact_and_credits_ally_caster(self):
        run, caster, player, bot = self.ally_scene()
        run.step({caster: (0, 0, 16)})
        bot.firing, bot.attack_target = True, player.entity
        run.step({})
        target = next(t for t in run.match.combat._targets() if t.owner is player)
        self.assertEqual(run.match.combat.vitals_of(target).total, 400)
        self.assertFalse(run.match.combat.is_stunned(player))
        self.assertAlmostEqual(caster.body_script.component.entity_var(87).value(), 5)
        energy = next(i for i in caster.body_script.component.instances.values() if i.graph.index == 0x0242)
        self.assertEqual(energy.vars[392].value().id, bot.entity)

    def test_breaking_projection_blocks_assassin_contact_then_recovery_gates_next_pin(self):
        run, caster, player, bot = self.ally_scene()
        fight = run.match.combat
        run.step({caster: (0, 0, 16)})
        self.assertTrue(fight.zarya_barriers.protects(player))
        pool = fight.zarya_barriers.projected[caster.body]
        self.assertEqual((pool.recipient, pool.remaining), (player.body, 200))
        pool.remaining = 5
        bot.firing, bot.attack_target = True, player.entity
        run.step({})
        health = fight.vitals[player.body]
        self.assertEqual(health.total, 400)
        self.assertEqual(pool.remaining, 0)
        self.assertFalse(fight.is_stunned(player))
        self.assertNotIn(bot.entity, fight.pins)
        self.assertAlmostEqual(caster.body_script.component.entity_var(87).value(), 1)

        npc_control.stop(run.match)
        run.step({})
        instance = self.instance(bot)
        release = instance.state(instance.graph.node(31).state)
        self.assertTrue(release.active)
        deadline = release.start + 2000
        bot.firing, bot.attack_target = True, player.entity
        while run.frame * 16 < deadline:
            run.step({})
            self.assertEqual(health.total, 400)
            self.assertNotIn(bot.entity, fight.pins)
        run.step({})
        self.assertEqual(health.total, 375)
        self.assertEqual(fight.pins.get(bot.entity), player.body)
        self.assertTrue(fight.is_stunned(player))

    def test_projection_during_continuous_damage_releases_host_control_on_blocked_hit(self):
        run, caster, player, bot = self.ally_scene()
        bot.firing, bot.attack_target = True, player.entity
        instance = self.instance(bot)
        for _ in range(100):
            run.step({})
            state = instance.states.get(instance.graph.node(15).state)
            if state is not None and state.active:
                break
        else:
            self.fail("continuous phase did not start")
        self.assertTrue(run.match.combat.is_stunned(player))
        run.step({caster: (0, 0, 16)})
        target = next(t for t in run.match.combat._targets() if t.owner is player)
        health = run.match.combat.vitals_of(target)
        before = health.total
        run.step({})
        self.assertEqual(health.total, before)
        self.assertFalse(run.match.combat.is_stunned(player))
        run.step({player: (0, 0, 8)})
        personal = next(i for i in player.body_script.component.instances.values() if i.graph.index == 0x0244)
        self.assertTrue(personal.state(2).active)

    def test_stopping_one_of_two_attackers_preserves_the_other_control_scope(self):
        run, player, first, health = self.scene()
        definition = next(d for d in fixtures.ZaryaFiveNpcTests.catalog.values() if d.name == "Assassin")
        second = npc.spawn(run.match, definition, (0, 1, 2.5), team=1)
        second.firing, second.attack_target = True, player.entity
        run.step({})
        self.assertEqual(health.total, 350)
        self.assertEqual(set(run.match.combat.pins), {first.entity, second.entity})
        first.firing = False
        run.step({})
        self.assertEqual(run.match.combat.pins, {second.entity: player.body})
        self.assertTrue(run.match.combat.is_stunned(player))
        target = next(t for t in run.match.combat._targets() if t.owner is second)
        run.match.combat._damage(player, target, 1000, False, None, run.frame)
        self.assertFalse(run.match.combat.is_stunned(player))

    def test_lethal_graviton_field_releases_live_assassin_pin(self):
        run, caster, player, bot = self.ally_scene()
        fight = run.match.combat
        bot.firing, bot.attack_target = True, player.entity
        run.step({})
        self.assertEqual(fight.pins.get(bot.entity), player.body)
        vitals = fight.vitals[bot.entity]
        vitals.now = dict.fromkeys(vitals.now, 0.0)
        vitals.now[combat.HEALTH_POOL] = 7.0
        caster.body_script.set_ult_charge(2100)
        x, y, z = bot.position
        angles = aim(fight._eye(caster), (x, y + 1, z))
        run.step({caster: (*angles, 32)})
        for _ in range(60):
            if fight.zarya_gravitons.fields:
                break
            run.step({})
        field, = fight.zarya_gravitons.fields
        self.assertEqual(vitals.total, 2)
        self.assertFalse(bot.dead)
        self.assertEqual(fight.pins.get(bot.entity), player.body)
        for _ in range(40):
            if bot.dead:
                break
            run.step({})
        self.assertTrue(bot.dead)
        self.assertEqual(vitals.total, 0)
        self.assertEqual(vitals.killer, caster.body)
        self.assertLess(fight.tick, field.end_tick)
        self.assertNotIn(bot.entity, fight.pins)
        self.assertFalse(fight.is_stunned(player))
        self.assertEqual([entity for entity, _ in caster.client.deaths], [bot.entity])
        health = fight.vitals[player.body]
        before = health.total
        self.advance(run, 45)
        self.assertEqual(health.total, before)
        self.assertNotIn(bot.entity, fight.pins)

    def test_nonlethal_graviton_only_releases_pin_when_pull_breaks_contact(self):
        for displaced in (False, True):
            with self.subTest(displaced=displaced):
                run, caster, player, bot = self.ally_scene()
                fight = run.match.combat
                if displaced:
                    caster.mover.position[:] = [1.25, 1, -1]
                    run.match.collision_world = World([
                        (-12, 1, -12, -12, 1, 12, 12, 1, 12), (-12, 1, -12, 12, 1, 12, 12, 1, -12),
                        (-12, 1, 6, 12, 1, 6, 12, 10, 6), (-12, 1, 6, 12, 10, 6, -12, 10, 6),
                    ])
                for actor in (caster, player):
                    actor.mover.body = None
                bot.firing, bot.attack_target = True, player.entity
                run.step({})
                self.assertEqual(fight.pins.get(bot.entity), player.body)
                bot_health = fight.vitals[bot.entity]
                before = bot_health.total
                caster.body_script.ult_seconds = 0
                caster.body_script.set_ult_charge(2100)
                angles = (0, 0) if displaced else aim(fight._eye(caster), (0, 2, 2.5))
                run.step({caster: (*angles, 32)})
                for _ in range(60):
                    if fight.zarya_gravitons.fields:
                        break
                    run.step({})
                field, = fight.zarya_gravitons.fields
                self.assertIs(field.shooter, caster)
                self.assertEqual(fight.pins.get(bot.entity), player.body)
                self.assertIsNotNone(fight.zarya_gravitons.movement_for(bot))
                self.assertIsNone(fight.zarya_gravitons.movement_for(player))
                health = fight.vitals[player.body]
                if displaced:
                    self.assertAlmostEqual(field.position[2], 6, delta=.25)
                    for _ in range(30):
                        run.step({})
                        if bot.entity not in fight.pins:
                            break
                    self.assertGreater(bot.position[2], 3)
                    self.assertFalse(fight.is_stunned(player))
                    self.assertIsNone(bot.body_script.component.world.manual_target)
                    after = health.total
                    run.step({player: (0, 0, 8)})
                    self.assertTrue(fight.zarya_barriers.protects(player))
                    self.advance(run, 16)
                    self.assertEqual(health.total, after)
                else:
                    after = health.total
                    self.advance(run, 65)
                    self.assertLess(health.total, after)
                    self.assertEqual(fight.pins.get(bot.entity), player.body)
                    self.assertTrue(fight.is_stunned(player))
                self.assertFalse(bot.dead)
                self.assertLess(bot_health.total, before)
                self.assertEqual(caster.body_script.component.entity_var(342).value(), 0)

    def test_actual_flashbang_or_sleep_dart_releases_pin_before_control_expires(self):
        for hero, button, damage in ((0x02E0000000000042, 16, 25), (0x02E000000000013B, 8, 5)):
            with self.subTest(hero=hero):
                run, rescuer, player, bot = self.ally_scene()
                self.assertTrue(run.match.switch_hero(rescuer, heroes()[hero]))
                rescuer.mover.position[:] = [2, 1, 2.5] if button == 16 else [0, 1, 6]
                rescuer.mover.body = None
                fight = run.match.combat
                bot.firing, bot.attack_target = True, player.entity
                run.step({})
                self.assertEqual(fight.pins.get(bot.entity), player.body)
                health = fight.vitals[player.body]
                bot_health = fight.vitals[bot.entity]
                before = bot_health.total
                angles = aim(fight._eye(rescuer), (0, 2, 2.5))
                run.step({rescuer: (*angles, button)})
                for _ in range(60):
                    if fight.is_stunned(bot):
                        break
                    run.step({rescuer: (*angles, 0)})
                self.assertEqual(bot_health.total, before - damage)
                self.assertTrue(fight.is_stunned(bot))
                self.assertFalse(fight.is_stunned(player))
                self.assertNotIn(bot.entity, fight.pins)
                after = health.total
                run.step({player: (0, 0, 8)})
                self.assertTrue(fight.zarya_barriers.protects(player))
                self.advance(run, 20)
                self.assertEqual(health.total, after)
                self.assertTrue(fight.is_stunned(bot))

    def test_actual_boosters_rescue_zarya_only_when_contact_reaches_assassin(self):
        mech = 0x0400000000000407
        self.assertIsNotNone(defined_mover_data(mech))
        for case in ("nonlethal", "breaking_barrier", "lethal", "overlap"):
            with self.subTest(case=case):
                run, rescuer, player, bot = self.ally_scene(protector=case == "breaking_barrier")
                match = run.match
                fight = match.combat
                self.assertTrue(match.switch_hero(rescuer, replace(heroes()[dva.HERO], body=mech)))
                rescuer.mover.position[:] = (2, 1, 2.5)
                rescuer.mover.body = None
                if case == "breaking_barrier":
                    projector = run.players[2]
                    projector.mover.position[:] = (-5, 1, 2.5)
                    projector.mover.body = None
                self.advance(run, 100)
                bot.firing, bot.attack_target = True, player.entity
                if case == "overlap":
                    definition = next(d for d in fixtures.ZaryaFiveNpcTests.catalog.values()
                                      if d.name == "Assassin")
                    second = npc.spawn(match, definition, (0, 1, -2.5), team=bot.team)
                    second.firing, second.attack_target = True, player.entity
                run.step({})
                self.assertEqual(fight.pins.get(bot.entity), player.body)
                self.assertTrue(fight.is_stunned(player))
                if case == "overlap":
                    self.assertEqual(fight.pins.get(second.entity), player.body)
                health = fight.vitals[player.body]
                bot_health = fight.vitals[bot.entity]
                if case == "breaking_barrier":
                    angles = aim(fight._eye(projector), (0, 2, 2.5))
                    run.step({projector: (*angles, 16)})
                    self.assertTrue(fight.zarya_barriers.protects(bot))
                    pool = fight.zarya_barriers.projected[projector.body]
                    self.assertEqual(pool.recipient, bot.entity)
                    pool.remaining = 5
                elif case == "lethal":
                    bot_health.now = dict.fromkeys(bot_health.now, 0.0)
                    bot_health.now[combat.HEALTH_POOL] = 5
                before = bot_health.total
                run.step({rescuer: (-16384, 0, 8)})
                self.assertIn(bot.entity, fight.booster_contacts[rescuer.body][2])
                if case == "breaking_barrier":
                    self.assertEqual(pool.remaining, 0)
                    self.assertEqual(projector.body_script.component.entity_var(87).value(), 1)
                    self.assertEqual(bot_health.total, before)
                    self.assertIsNone(bot.impulse_mover)
                    self.assertEqual(fight.pins.get(bot.entity), player.body)
                    self.assertTrue(fight.is_stunned(player))
                    self.assertFalse(rescuer.client.messages(combat.HIT))
                else:
                    self.assertEqual(before - bot_health.total, min(10, before))
                    self.assertEqual(bot.dead, case == "lethal")
                    self.assertNotIn(bot.entity, fight.pins)
                    self.assertEqual(fight.is_stunned(player), case == "overlap")
                    if case != "lethal":
                        self.assertIsNotNone(bot.impulse_mover)
                        self.assertLess(bot.velocity[0], 0)
                    else:
                        self.assertIsNone(bot.impulse_mover)
                        self.assertEqual(bot_health.killer, rescuer.body)
                        self.assertEqual([entity for entity, _ in rescuer.client.deaths], [bot.entity])
                    if case == "overlap":
                        self.assertEqual(fight.pins, {second.entity: player.body})
                        self.assertNotIn(second.entity, fight.booster_contacts[rescuer.body][2])
                        continue
                    after = health.total
                    run.step({player: (0, 0, 8)})
                    self.assertTrue(fight.zarya_barriers.protects(player))
                    self.advance(run, 20)
                    self.assertEqual(health.total, after)

    def test_breaking_assassin_projection_blocks_actual_rescue_projectiles(self):
        for hero, button, damage in ((0x02E0000000000042, 16, 25), (0x02E000000000013B, 8, 5)):
            with self.subTest(hero=hero):
                run, rescuer, player, bot = self.ally_scene(protector=True)
                projector = run.players[2]
                self.assertTrue(run.match.switch_hero(projector, heroes()[fixtures.ZARYA]))
                self.assertTrue(run.match.switch_hero(rescuer, heroes()[hero]))
                rescuer.mover.position[:] = [2, 1, 2.5] if button == 16 else [0, 1, 6]
                projector.mover.position[:] = [-5, 1, 2.5]
                for actor in (rescuer, projector):
                    actor.mover.body = None
                fight = run.match.combat
                bot.firing, bot.attack_target = True, player.entity
                angles = aim(fight._eye(projector), (0, 2, 2.5))
                run.step({projector: (*angles, 16)})
                self.assertEqual(fight.pins.get(bot.entity), player.body)
                barriers = fight.zarya_barriers
                self.assertTrue(barriers.protects(bot))
                pool = barriers.projected[projector.body]
                self.assertEqual(pool.recipient, bot.entity)
                pool.remaining = damage / 2
                bot_health = fight.vitals[bot.entity]
                before = bot_health.total
                angles = aim(fight._eye(rescuer), (0, 2, 2.5))
                run.step({rescuer: (*angles, button)})
                for _ in range(60):
                    if pool.remaining == 0:
                        break
                    run.step({rescuer: (*angles, 0)})
                self.assertEqual(pool.remaining, 0)
                self.assertEqual(projector.body_script.component.entity_var(87).value(), damage / 10)
                self.assertEqual(bot_health.total, before)
                self.assertFalse(fight.is_stunned(bot))
                self.assertEqual(fight.pins.get(bot.entity), player.body)

    def test_target_death_clears_pin_without_rebinding_dead_body(self):
        run, player, bot, health = self.contact()
        target = next(t for t in run.match.combat._targets() if t.owner is player)
        run.match.combat._damage(bot, target, 1000, False, None, run.frame)
        self.assertTrue(health.dead)
        run.step({})
        self.assertFalse(run.match.combat.pins)
        self.assertIsNone(bot.body_script.component.world.manual_target)

    def test_range_wall_and_team_reject_engagement(self):
        for reason in ("range", "wall", "team"):
            with self.subTest(reason=reason):
                run, player, bot, health = self.scene(distance=3.5 if reason == "range" else 2.5)
                if reason == "team":
                    bot.team = player.team
                if reason == "wall":
                    run.match.collision_world = World([(-10, 0, 1, 10, 0, 1, 10, 10, 1),
                                                       (-10, 0, 1, 10, 10, 1, -10, 10, 1)])
                self.advance(run, 100)
                self.assertEqual(health.total, 400)
                self.assertFalse(run.match.combat.pins)
                self.assertIsNone(bot.body_script.component.world.manual_target)

    def test_cryo_cover_denies_and_releases_assassin_pin_on_either_team(self):
        for pinned in (False, True):
            for allied in (False, True):
                with self.subTest(pinned=pinned, allied=allied):
                    if pinned:
                        run, player, bot, health, _ = self.continuous()
                    else:
                        run, player, bot, health = self.scene(engage=False)
                    match = run.match
                    fight = match.combat
                    mei_hero = 0x02E00000000000DD
                    mei = match.add_player(2, "Mei", mei_hero, player.team if allied else bot.team, False)
                    in_world(match, mei)
                    run.players.append(mei)
                    self.assertTrue(match.switch_hero(mei, heroes()[mei_hero]))
                    mei.mover.position[:] = [0, 1, 1.25]
                    mei.mover.body = None
                    run.step({mei: (16384, 0, 8)})
                    self.assertEqual(phase.protection(mei), "cryo")
                    before = health.total
                    bot.firing, bot.attack_target = True, player.entity
                    for _ in range(24):
                        run.step({})
                        self.assertEqual(health.total, before)
                        self.assertFalse(fight.is_stunned(player))
                        self.assertNotIn(bot.entity, fight.pins)
                        self.assertIsNone(bot.body_script.component.world.manual_target)

                    run.step({mei: (16384, 0, 1)})
                    self.assertIsNone(phase.protection(mei))
                    for _ in range(150):
                        run.step({})
                        if health.total < before:
                            break
                    self.assertEqual(health.total, before - 25)
                    self.assertEqual(fight.pins.get(bot.entity), player.body)

    def test_source_death_and_removal_release_target_and_retire_context(self):
        for remove in (False, True):
            with self.subTest(remove=remove):
                run, player, bot, health = self.contact()
                world = bot.body_script.component.world
                if remove:
                    npc.remove_spawned(run.match)
                    self.assertIsNone(world.match)
                else:
                    target = next(t for t in run.match.combat._targets() if t.owner is bot)
                    run.match.combat._damage(player, target, 1000, False, None, run.frame)
                self.assertFalse(run.match.combat.pins)
                self.assertIsNone(world.manual_target)
                before = health.total
                self.advance(run, 100)
                self.assertEqual(health.total, before)

    def test_target_replacement_leave_and_match_end_retire_pin(self):
        for operation in ("replace", "leave", "end"):
            with self.subTest(operation=operation):
                run, player, _, _ = self.contact()
                if operation == "replace":
                    self.assertTrue(run.match.switch_hero(player, heroes()[SOLDIER]))
                elif operation == "leave":
                    player.gone = True
                else:
                    run.match.ended = True
                run.step({})
                self.assertFalse(run.match.combat.pins)
