"""Orisa versus the authored Assassin 1157 graph under manual engagement."""

import math
import os
import unittest
from dataclasses import replace

import test_orisa_barrier as barrier_fixture
import test_orisa_combat as combat_fixture
import test_orisa_npc as fixture
import test_orisa_npc_incoming as incoming
import test_orisa_supercharger as supercharger_fixture
from test_game_combat import KANEZAKA_DM, SOLDIER, Run, aim, in_world

from ow174.game import assassin, combat, npc, npc_control
from ow174.game.body_vm_snapshot import export_component_vm, snapshot_digest
from ow174.game.commands import FIRE
from ow174.game.content import find_hero, heroes


@unittest.skipUnless(os.environ.get("ORPHEUS_NPC_RUNTIME"), "actual Talon graph package required")
class OrisaAssassinTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture.OrisaTalonTests.setUpClass()

    @classmethod
    def tearDownClass(cls):
        fixture.OrisaTalonTests.tearDownClass()

    def arena(self, *, fortify=False, engage=True, ahead=2):
        helper = fixture.OrisaTalonTests()
        definition = next(d for d in helper.catalog.values() if d.name == "Assassin")
        run, player, bot = helper.arena(definition, ahead=ahead)
        player.client.bot_stream = lambda _: None
        run.step({player: (0, 0, 8 if fortify else 0)})
        if engage:
            incoming.OrisaNpcIncomingTests().start(run, player, bot)
        instance = next(i for i in bot.body_script.component.instances.values() if i.graph.index == 0x1157)
        self.assertEqual(bot.body_script.component.world.ai_weapon_button(0), 36)
        return run, player, bot, instance

    def until_pin(self, run, player, bot, limit=100):
        fight = run.match.combat
        for _ in range(limit):
            run.step({})
            if fight.pins.get(bot.entity) == player.body:
                return
        self.fail("original Assassin graph did not pin")

    def test_actual_contact_rate_and_stop_release(self):
        run, player, bot, instance = self.arena()
        fight = run.match.combat
        vitals = incoming.OrisaNpcIncomingTests.vitals(run, player)
        self.until_pin(run, player, bot)
        self.assertEqual(vitals.total, 430)
        self.assertTrue(fight.is_stunned(player))
        rate = instance.state(instance.graph.node(15).state)
        for _ in range(100):
            if rate.active:
                break
            run.step({})
        self.assertTrue(rate.active)
        component = instance.component
        before_digest = snapshot_digest(export_component_vm(component))
        clock = rate.last_applied
        rate.last_applied += 1
        try:
            self.assertNotEqual(snapshot_digest(export_component_vm(component)), before_digest)
        finally:
            rate.last_applied = clock
        before = vitals.total
        for _ in range(10):
            run.step({player: (0, 0, FIRE)})
        self.assertAlmostEqual(before - vitals.total, 8, places=5)
        self.assertFalse(any(rocket.shooter is player for rocket in fight.rockets))
        npc_control.stop(run.match)
        self.assertFalse(fight.is_stunned(player))
        before = vitals.total
        for _ in range(40):
            run.step({})
        self.assertEqual(vitals.total, before)
        bot_vitals = fight.vitals[bot.entity]
        before = bot_vitals.total
        x, y, z = bot.position
        yaw, pitch = aim(fight._eye(player), (x, y + 1, z))
        run.step({player: (yaw, pitch, FIRE)})
        for _ in range(5):
            run.step({})
        self.assertLess(bot_vitals.total, before)

    def test_allied_flashbang_breaks_assassin_pin_and_restores_primary_fire(self):
        run, player, bot, instance = self.arena(engage=False)
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        caster = run.match.add_player(3, "Ally McCree", SOLDIER, 2, False)
        in_world(run.match, caster)
        run.players.append(caster)
        self.assertTrue(run.match.switch_hero(caster, heroes()[0x02E0000000000042]))
        caster.team = player.team
        caster.client.bot_stream = lambda _: None
        x, y, z = bot.position
        barrier_fixture.BarrierTests.place(caster, (x + 3, y, z))
        fight = run.match.combat
        incoming.OrisaNpcIncomingTests().start(run, player, bot)
        self.until_pin(run, player, bot)
        rate = instance.state(instance.graph.node(15).state)
        for _ in range(100):
            if rate.active:
                break
            run.step({})
        self.assertTrue(rate.active)
        health = incoming.OrisaNpcIncomingTests.vitals(run, player)
        bot_health = fight.vitals[bot.entity]
        before_flash = bot_health.total
        x, y, z = bot.position
        yaw, pitch = aim(fight._eye(caster), (x, y + 1, z))
        run.step({caster: (yaw, pitch, 16)})
        for _ in range(50):
            if fight.is_stunned(bot):
                break
            run.step({})
        self.assertTrue(fight.is_stunned(bot))
        self.assertAlmostEqual(before_flash - bot_health.total, 25, places=4)
        self.assertNotIn(bot.entity, fight.pins)
        self.assertFalse(rate.active)
        self.assertFalse(fight.is_stunned(player))
        rescued_health = health.total

        before_shot = bot_health.total
        yaw, pitch = aim(fight._eye(player), (x, y + 1, z))
        run.step({player: (yaw, pitch, FIRE)})
        for _ in range(12):
            run.step({})
        self.assertTrue(fight.is_stunned(bot))
        self.assertEqual(rescued_health, health.total)
        self.assertAlmostEqual(before_shot - bot_health.total, 11, places=4)
        self.assertEqual(len(player.client.messages(combat.HIT)), 1)

        run.step({player: (yaw, pitch, 8)})
        self.assertTrue(fight.orisa._immune(player))
        self.assertTrue(bot.firing)
        self.assertEqual(bot.attack_target, player.entity)
        stun_end = fight.stuns[bot.entity]
        while run.frame <= stun_end + 30:
            run.step({})
            self.assertNotIn(bot.entity, fight.pins)
        self.assertFalse(fight.is_stunned(bot))
        self.assertTrue(fight.orisa._immune(player))
        self.assertEqual(health.total, rescued_health)

        self.until_pin(run, player, bot, limit=300)
        self.assertFalse(fight.orisa._immune(player))
        self.assertAlmostEqual(rescued_health - health.total, 20, places=4)
        self.assertTrue(fight.is_stunned(player))

    @unittest.skipUnless(os.environ.get("ORPHEUS_ORISA_SUPERCHARGER"), "authored device required")
    def test_supercharger_boosts_assassin_contact_and_periodic_pin_damage(self):
        definition = next(d for d in fixture.OrisaTalonTests.catalog.values() if d.name == "Assassin")
        for boosted in (False, True):
            with self.subTest(boosted=boosted):
                run, owner, victim, manager, device = supercharger_fixture.SuperchargerTests().deploy()
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                run.match.switch_hero(victim, find_hero("Orisa"))
                x, y, z = device.position
                barrier_fixture.BarrierTests.place(victim, (x, y, z + 5))
                for player in (owner, victim):
                    player.client.bot_stream = lambda _: None
                clear_world = run.match.collision_world
                # Occlude the aura while leaving the pin contact path clear.
                combat_fixture.OrisaCombatTests().wall(run, victim, -3.5)
                covered_world = run.match.collision_world
                run.match.collision_world = clear_world if boosted else covered_world
                bot = npc.spawn(run.match, definition, (x, y, z + 3), team=owner.team)
                run.step({})
                self.assertEqual(manager.scale(bot), 1.5 if boosted else 1)
                health = incoming.OrisaNpcIncomingTests.vitals(run, victim)
                before = health.total
                incoming.OrisaNpcIncomingTests().start(run, victim, bot)
                self.until_pin(run, victim, bot)
                self.assertAlmostEqual(before - health.total, 32.5 if boosted else 20, places=4)

                graph = next(i for i in bot.body_script.component.instances.values() if i.graph.index == 0x1157)
                rate = graph.state(graph.graph.node(15).state)
                for _ in range(100):
                    if rate.active:
                        break
                    run.step({})
                self.assertTrue(rate.active)
                before = health.total
                for _ in range(10):
                    run.step({})
                self.assertAlmostEqual(before - health.total, 12 if boosted else 8, places=4)

                run.match.collision_world = covered_world if boosted else clear_world
                run.step({})
                self.assertEqual(manager.scale(bot), 1 if boosted else 1.5)
                before = health.total
                for _ in range(10):
                    run.step({})
                self.assertAlmostEqual(before - health.total, 8 if boosted else 12, places=4)
                self.assertTrue(rate.active)
                self.assertEqual(run.match.combat.pins.get(bot.entity), victim.body)
                self.assertTrue(run.match.combat.is_stunned(victim))

    @unittest.skipUnless(os.environ.get("ORPHEUS_ORISA_SUPERCHARGER"), "authored device required")
    def test_supercharger_expires_during_assassin_pin_without_releasing_victim(self):
        run, owner, victim, manager, device = supercharger_fixture.SuperchargerTests().deploy()
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        self.assertTrue(run.match.switch_hero(victim, find_hero("Orisa")))
        x, y, z = device.position
        barrier_fixture.BarrierTests.place(victim, (x, y, z + 5))
        for player in (owner, victim):
            player.client.bot_stream = lambda _: None
        while run.frame < device.until - 105:
            run.step({})
        definition = next(d for d in fixture.OrisaTalonTests.catalog.values() if d.name == "Assassin")
        bot = npc.spawn(run.match, definition, (x, y, z + 3), team=owner.team)
        run.step({})
        self.assertEqual(manager.scale(bot), 1.5)
        health = incoming.OrisaNpcIncomingTests.vitals(run, victim)
        incoming.OrisaNpcIncomingTests().start(run, victim, bot)
        self.until_pin(run, victim, bot)
        instance, = (i for i in bot.body_script.component.instances.values() if i.graph.index == 0x1157)
        rate = instance.state(instance.graph.node(15).state)
        while run.frame < device.until - 11:
            run.step({})
        fight = run.match.combat
        for multiplier, damage in ((1.5, 12), (1, 8)):
            self.assertTrue(rate.active)
            self.assertEqual(fight.pins.get(bot.entity), victim.body)
            self.assertEqual(manager.scale(bot), multiplier)
            before = health.total
            for _ in range(10):
                run.step({})
            self.assertAlmostEqual(before - health.total, damage, places=4)
            if multiplier == 1.5:
                run.step({})
                self.assertEqual(run.frame, device.until)
                self.assertNotIn(device.entity, manager.active)
        self.assertTrue(rate.active)
        self.assertEqual(fight.pins.get(bot.entity), victim.body)
        self.assertTrue(fight.is_stunned(victim))

    def test_active_fortify_refuses_pin_then_attack_recovers(self):
        run, player, bot, _ = self.arena(fortify=True)
        fight = run.match.combat
        self.assertTrue(fight.orisa._immune(player))
        vitals = incoming.OrisaNpcIncomingTests.vitals(run, player)
        for _ in range(100):
            run.step({})
        self.assertEqual(vitals.total, 450)
        self.assertFalse(fight.is_stunned(player))
        self.until_pin(run, player, bot, limit=300)
        self.assertFalse(fight.orisa._immune(player))
        self.assertEqual(vitals.total, 430)

    def test_reload_input_while_pinned_and_reload_already_in_progress(self):
        for started_before_pin in (False, True):
            with self.subTest(started_before_pin=started_before_pin):
                run, player, bot, _ = self.arena(engage=False)
                fight = run.match.combat
                rifle = player.body_script.component.instances[11]
                run.step({player: (16384, 0, FIRE)})
                run.step({})
                self.assertEqual(rifle.vars[53].value(), 149)
                if started_before_pin:
                    run.step({player: (0, 0, 0x400)})

                incoming.OrisaNpcIncomingTests().start(run, player, bot)
                self.until_pin(run, player, bot)
                self.assertEqual(rifle.vars[53].value(), 149)
                for frame in range(250):
                    reload = 0x400 if not started_before_pin and frame in (0, 25) else 0
                    run.step({player: (0, 0, reload)})
                    self.assertTrue(fight.is_stunned(player))
                self.assertEqual(rifle.vars[53].value(), 150 if started_before_pin else 149)

                npc_control.stop(run.match)
                for _ in range(150):
                    run.step({})
                self.assertEqual(rifle.vars[53].value(), 150 if started_before_pin else 149)
                if not started_before_pin:
                    run.step({player: (0, 0, 0x400)})
                    for _ in range(250):
                        run.step({})
                        if rifle.vars[53].value() == 150:
                            break
                    self.assertEqual(rifle.vars[53].value(), 150)

    def test_pin_discards_melee_presses_and_fresh_press_recovers(self):
        run, player, bot, _ = self.arena(ahead=1.5)
        fight = run.match.combat
        self.until_pin(run, player, bot)
        vitals = fight.vitals[bot.entity]
        health = vitals.total
        x, y, z = bot.position
        yaw, pitch = aim(fight._eye(player), (x, y + 1, z))
        for frame in range(30):
            run.step({player: (yaw, pitch, 0x800 if frame in (0, 15) else 0)})
            self.assertTrue(fight.is_stunned(player))
            self.assertEqual(vitals.total, health)

        npc_control.stop(run.match)
        for _ in range(25):
            run.step({})
            self.assertFalse(fight.is_stunned(player))
            self.assertEqual(vitals.total, health)
        run.step({player: (yaw, pitch, 0x800)})
        for _ in range(25):
            run.step({})
        self.assertEqual(health - vitals.total, 30)

    def test_established_pin_blocks_fortify_without_spending_cooldown(self):
        run, player, bot, _ = self.arena()
        self.until_pin(run, player, bot)
        fight = run.match.combat
        target = combat_fixture.OrisaCombatTests().target(run, player)
        cooldown = player.body_script.component.instances[6].state(0)
        for frame in range(20):
            run.step({player: (0, 0, 8 if frame % 2 == 0 else 0)})
            self.assertTrue(fight.is_stunned(player))
            self.assertFalse(fight.orisa._immune(player))
            self.assertIsNone(fight._fortify_reduction(target))
            self.assertEqual(cooldown.remaining(), 0)

        npc_control.stop(run.match)
        run.step({})
        self.assertFalse(fight.is_stunned(player))
        self.assertIsNone(fight._fortify_reduction(target))
        run.step({player: (0, 0, 8)})
        self.assertTrue(fight.orisa._immune(player))
        self.assertAlmostEqual(fight._fortify_reduction(target), 0.6)

        incoming.OrisaNpcIncomingTests().start(run, player, bot)
        vitals = fight.vitals_of(target)
        health = vitals.total
        for _ in range(25):
            run.step({})
            self.assertFalse(fight.is_stunned(player))
        self.assertEqual(vitals.total, health)

    def test_active_pin_releases_on_cover_or_range_loss_and_reacquires(self):
        for cover in (False, True):
            with self.subTest(cover=cover):
                run, player, bot, _ = self.arena()
                self.until_pin(run, player, bot)
                fight = run.match.combat
                vitals = incoming.OrisaNpcIncomingTests.vitals(run, player)
                before = vitals.total
                original = bot.position
                world = run.match.collision_world
                if cover:
                    combat_fixture.OrisaCombatTests().wall(run, player, 1)
                else:
                    x, y, z = original
                    bot.position = (x, y, z + 3)
                run.step({})
                self.assertNotIn(bot.entity, fight.pins)
                self.assertFalse(bot.attack_input)
                self.assertFalse(fight.is_stunned(player))
                for _ in range(20):
                    run.step({})
                self.assertEqual(vitals.total, before)
                self.assertTrue(bot.firing)
                bot.position = original
                run.match.collision_world = world
                self.until_pin(run, player, bot)
                self.assertEqual(vitals.total, before - 20)

    def test_authored_pin_wait_requires_victim_flag(self):
        run, player, bot, instance = self.arena()
        self.until_pin(run, player, bot)
        pin = instance.state(instance.graph.node(23).state)
        timer = instance.state(instance.graph.node(42).state)
        for _ in range(100):
            run.step({})
        self.assertTrue(pin.active)
        self.assertFalse(timer.active)
        self.assertEqual(timer.counter, 0)

    def test_supplied_victim_flag_allows_timed_release_and_primary_input(self):
        run, player, bot, instance = self.arena()
        player.body_script.component.set(478, True)
        self.assertIsNone(instance.component.world.entity_var(player.body, 478))
        self.until_pin(run, player, bot)
        fight = run.match.combat
        pin = instance.state(instance.graph.node(23).state)
        timer = instance.state(instance.graph.node(42).state)
        self.assertTrue(timer.active)
        self.assertIs(instance.component.world.entity_var(player.body, 478), True)
        for _ in range(120):
            if not pin.active:
                break
            run.step({})
        self.assertFalse(pin.active)
        self.assertEqual(timer.ended_at - timer.start, 1500)
        self.assertNotIn(bot.entity, fight.pins)
        self.assertFalse(fight.is_stunned(player))
        self.assertTrue(bot.firing)
        vitals = fight.vitals[bot.entity]
        before = vitals.total
        x, y, z = bot.position
        yaw, pitch = aim(fight._eye(player), (x, y + 1, z))
        run.step({player: (yaw, pitch, FIRE)})
        for _ in range(6):
            run.step({})
        self.assertLess(vitals.total, before)

    def test_flagged_victim_threshold_before_lethal_pin_damage(self):
        for initial, reaches_threshold_alive in ((29.5, True), (30, False)):
            with self.subTest(initial=initial):
                self.check_flagged_victim_threshold(initial, reaches_threshold_alive)

    def check_flagged_victim_threshold(self, initial, reaches_threshold_alive):
        run, player, bot, instance = self.arena()
        fight = run.match.combat
        vitals = incoming.OrisaNpcIncomingTests.vitals(run, player)
        # Fixture health before contact. Subsequent damage comes from the
        # graph's contact hit and timed damage, through the combat pools.
        vitals.damage(vitals.total - initial)
        vitals.damage(vitals.total - initial)
        self.assertEqual(vitals.total, initial)
        player.body_script.component.set(2380, True)
        self.until_pin(run, player, bot)
        self.assertEqual(vitals.total, initial - 25)
        timer = instance.state(instance.graph.node(42).state)
        self.assertFalse(timer.active)
        crossed_alive = False
        for _ in range(100):
            run.step({})
            if vitals.total > 1:
                self.assertFalse(timer.active)
            crossed_alive |= 0 < vitals.total <= 1
            if vitals.dead:
                break
        self.assertTrue(vitals.dead)
        self.assertEqual(crossed_alive, reaches_threshold_alive)
        self.assertEqual(timer.counter, int(reaches_threshold_alive))
        if reaches_threshold_alive:
            self.assertEqual(timer.ms, 1500)
        run.step({})
        self.assertNotIn(bot.entity, fight.pins)
        self.assertFalse(timer.active)
        self.assertFalse(fight.is_stunned(player))

    @unittest.skipUnless(os.environ.get("ORPHEUS_ORISA_GEOMETRY"), "external barrier mesh required")
    def test_assassin_contact_pin_crosses_active_protective_barrier(self):
        run, player, enemy, _, shield = barrier_fixture.BarrierTests().deploy()
        x, y, z = shield.position
        barrier_fixture.BarrierTests.place(player, (x, y, z - 1.4))
        for actor in (player, enemy):
            actor.client.bot_stream = lambda _: None
        definition = next(d for d in fixture.OrisaTalonTests.catalog.values() if d.name == "Assassin")
        bot = npc.spawn(run.match, definition, (x, y, z + 1.4), team=player.team + 1)
        run.step({})
        fight = run.match.combat
        origin = (x, y + combat.EYE, z + 1.4)
        point = fight._eye(player)
        delta = tuple(end - start for start, end in zip(origin, point, strict=True))
        distance = math.dist(origin, point)
        direction = tuple(axis / distance for axis in delta)
        hit = fight.orisa.barriers.trace(bot, origin, direction, distance)
        self.assertIsNotNone(hit)
        health = shield.health
        vitals = incoming.OrisaNpcIncomingTests.vitals(run, player)
        before = vitals.total
        incoming.OrisaNpcIncomingTests().start(run, player, bot)
        self.until_pin(run, player, bot)
        self.assertTrue(fight.is_stunned(player))
        self.assertEqual(before - vitals.total, 20)
        self.assertEqual(shield.health, health)
        self.assertIn(shield.entity, fight.orisa.barriers.active)

        combat_fixture.OrisaCombatTests().wall(run, player, 0.7)
        run.step({})
        self.assertFalse(fight.is_stunned(player))
        self.assertNotIn(bot.entity, fight.pins)
        self.assertEqual(shield.health, health)

    @unittest.skipUnless(os.environ.get("ORPHEUS_ORISA_GEOMETRY"), "external barrier mesh required")
    def test_pin_refuses_new_barrier_then_allows_fresh_input(self):
        run, player, bot, _ = self.arena()
        self.until_pin(run, player, bot)
        fight = run.match.combat
        for _ in range(20):
            run.step({player: (0, 0, 16)})
            self.assertTrue(fight.is_stunned(player))
            self.assertFalse(any(r.source_graph == 0xBD7 for r in fight.rockets))
            self.assertFalse(fight.orisa.barriers.pending or fight.orisa.barriers.active)
        npc_control.stop(run.match)
        run.step({})
        for tick in range(120):
            run.step({player: (0, 0, 16 if tick == 0 else 0)})
            if fight.orisa.barriers.active:
                break
        shield, = fight.orisa.barriers.active.values()
        self.assertEqual(shield.source_body, player.body)

    @unittest.skipUnless(os.environ.get("ORPHEUS_ORISA_GEOMETRY"), "external barrier mesh required")
    def test_already_launched_barrier_deploys_while_owner_is_pinned(self):
        run, player, bot, _ = self.arena(engage=False)
        fight = run.match.combat
        for tick in range(30):
            run.step({player: (0, 0, 16 if tick == 0 else 0)})
            rocket = next((r for r in fight.rockets if r.source_graph == 0xBD7), None)
            if rocket is not None:
                break
        self.assertIsNotNone(rocket)
        incoming.OrisaNpcIncomingTests().start(run, player, bot)
        self.until_pin(run, player, bot)
        self.assertFalse(fight.orisa.barriers.active)
        for _ in range(100):
            run.step({})
            if fight.orisa.barriers.active:
                break
        self.assertTrue(fight.is_stunned(player))
        shield, = fight.orisa.barriers.active.values()
        self.assertEqual(shield.source_body, player.body)

    def test_halt_detonation_before_pin_pulls_enemy_and_retires_during_pin(self):
        run, player, enemy = combat_fixture.OrisaCombatTests().arena()
        x, y, z = player.mover.position
        enemy.mover.position = [x + 2, y, z + 5]
        enemy.client.bot_stream = player.client.bot_stream = lambda _: None
        definition = next(d for d in fixture.OrisaTalonTests.catalog.values() if d.name == "Assassin")
        bot = npc.spawn(run.match, definition, (x, y, z + 2), team=player.team + 1)
        fight = run.match.combat
        halt = fight.orisa
        for frame in range(26):
            run.step({player: (0, 0, 2 if frame in (0, 25) else 0)})
        orb, = halt.orbs.values()
        self.assertEqual(orb.requested, run.match.tick)
        centre = orb.rocket.position
        original = tuple(enemy.mover.position)
        target = combat_fixture.OrisaCombatTests().target(run, enemy)
        vitals = fight.vitals_of(target)
        health = vitals.total

        incoming.OrisaNpcIncomingTests().start(run, player, bot)
        self.until_pin(run, player, bot)
        self.assertIn(orb.entity, halt.orbs)
        self.assertEqual(tuple(enemy.mover.position), original)
        self.assertEqual(tuple(halt.results), ((orb.entity, (enemy.body,)),))
        pull = halt.pulls[enemy.body]
        self.assertEqual(pull.script.graph.index, 0xCCE)
        self.assertTrue(pull.script.node_state(pull.script.graph.node(13)).active)

        for _ in range(40):
            if orb.entity not in halt.orbs:
                break
            self.assertTrue(fight.is_stunned(player))
            self.assertEqual(orb.rocket.position, centre)
            run.step({})
        self.assertTrue(fight.is_stunned(player))
        self.assertNotIn(orb.entity, halt.orbs)
        self.assertLess(math.dist(enemy.mover.position, centre), math.dist(original, centre) - 1)
        self.assertEqual(vitals.total, health)
        self.assertEqual(tuple(halt.results), ((orb.entity, (enemy.body,)),))
        self.assertGreater(orb.rocket.source_state.instance.state(2).remaining(), 0)

    def test_halt_pull_moves_pinning_assassin_out_of_contact_range(self):
        run, player, bot, instance = self.arena(engage=False, ahead=2.8)
        fight = run.match.combat
        halt = fight.orisa
        original = bot.position
        script = player.body_script
        script.ult_seconds = 0
        for frame in range(26):
            run.step({player: (0, 0, 2 if frame in (0, 25) else 0)})
        orb, = halt.orbs.values()
        self.assertEqual(orb.requested, run.match.tick)

        incoming.OrisaNpcIncomingTests().start(run, player, bot)
        self.until_pin(run, player, bot)
        self.assertEqual(bot.position, original)
        self.assertEqual(tuple(halt.results), ((orb.entity, (bot.entity,)),))
        pull = halt.pulls[bot.entity]
        self.assertEqual(pull.script.graph.index, 0xCCE)
        pin = instance.node_state(instance.graph.node(23))
        self.assertTrue(pin.active)
        x, y, z = bot.position
        self.assertLessEqual(math.dist((x, y + combat.EYE, z), fight._eye(player)), assassin.PIN_RANGE)
        for _ in range(40):
            run.step({})
            if bot.entity not in fight.pins:
                break
        self.assertLess(math.dist(bot.position, orb.rocket.position),
                        math.dist(original, orb.rocket.position) - 0.2)
        x, y, z = bot.position
        self.assertGreater(math.dist((x, y + combat.EYE, z), fight._eye(player)), assassin.PIN_RANGE)
        self.assertNotIn(bot.entity, fight.pins)
        self.assertFalse(fight.is_stunned(player))
        self.assertFalse(pin.active)
        self.assertFalse(bot.attack_input)
        self.assertTrue(bot.firing)

        vitals = fight.vitals[bot.entity]
        health = vitals.total
        charge = script.component.entity_var(342).value()
        yaw, pitch = aim(fight._eye(player), (x, y + 1, z))
        run.step({player: (yaw, pitch, FIRE)})
        self.assertTrue(any(r.shooter is player and r.source_graph == 0xBD4 for r in fight.rockets))
        for _ in range(8):
            run.step({})
        self.assertEqual(health - vitals.total, 11)
        with self.subTest(check="ultimate charge"):
            if halt.charge_metadata is None:
                self.skipTest("external charge metadata required")
            self.assertEqual(script.component.entity_var(342).value() - charge, 11)

    def test_pin_blocks_halt_reactivation_until_fresh_input_after_release(self):
        run, player, bot, _ = self.arena(engage=False)
        fight = run.match.combat
        halt = fight.orisa
        cast_frame = run.frame
        run.step({player: (0, 0, 2)})
        for _ in range(20):
            if halt.orbs:
                break
            run.step({})
        orb, = halt.orbs.values()
        self.assertEqual(orb.rocket.source_body, player.body)

        incoming.OrisaNpcIncomingTests().start(run, player, bot)
        self.until_pin(run, player, bot)
        while run.frame < cast_frame + 25:
            run.step({})
        before_press = orb.rocket.position
        for _ in range(3):
            run.step({player: (0, 0, 2)})
            self.assertTrue(fight.is_stunned(player))
            self.assertIsNone(orb.requested)
        self.assertNotEqual(orb.rocket.position, before_press)

        npc_control.stop(run.match)
        run.step({})
        self.assertFalse(fight.is_stunned(player))
        self.assertIsNone(orb.requested)
        run.step({player: (0, 0, 2)})
        self.assertEqual(orb.requested, run.match.tick)
        centre = orb.rocket.position
        run.step({})
        self.assertEqual(orb.rocket.position, centre)
        for _ in range(40):
            run.step({})
            if orb.entity not in halt.orbs:
                break
        self.assertNotIn(orb.entity, halt.orbs)
        self.assertEqual(halt.results[-1][0], orb.entity)

    @unittest.skipUnless(
        os.environ.get("ORPHEUS_ORISA_SUPERCHARGER"), "external authored supplement required"
    )
    def test_pin_interrupts_ultimate_windup_and_preserves_spent_charge(self):
        run, player, bot, _ = self.arena(engage=False)
        fight = run.match.combat
        manager = fight.orisa.superchargers
        script = player.body_script
        script.ult_seconds = 0
        cost = script.component.entity_var(338).value()
        script.set_ult_charge(cost)
        run.step({player: (0, 0, 32)})
        ultimate = next(i for i in script.component.instances.values() if i.graph.index == 0xCF5)
        pending = ultimate.state(ultimate.graph.node(30).state)
        self.assertTrue(pending.active)
        self.assertEqual(script.component.entity_var(342).value(), 0)
        self.assertFalse(any(r.source_graph == 0xCF5 for r in fight.rockets))
        incoming.OrisaNpcIncomingTests().start(run, player, bot)
        self.until_pin(run, player, bot)
        self.assertFalse(pending.active)
        for _ in range(70):
            run.step({})
            self.assertFalse(any(r.source_graph == 0xCF5 for r in fight.rockets))
            self.assertFalse(manager.active)
        npc_control.stop(run.match)
        run.step({})
        run.step({player: (0, 0, 32)})
        self.assertFalse(pending.active)
        self.assertEqual(script.component.entity_var(342).value(), 0)

    @unittest.skipUnless(
        os.environ.get("ORPHEUS_ORISA_SUPERCHARGER"), "external authored supplement required"
    )
    def test_pin_refuses_full_charge_cast_and_fresh_input_recovers(self):
        run, player, bot, _ = self.arena()
        fight = run.match.combat
        manager = fight.orisa.superchargers
        script = player.body_script
        script.ult_seconds = 0
        cost = script.component.entity_var(338).value()
        script.set_ult_charge(cost)
        self.until_pin(run, player, bot)
        for _ in range(20):
            run.step({player: (0, 0, 32)})
            self.assertTrue(fight.is_stunned(player))
            self.assertEqual(script.component.entity_var(342).value(), cost)
            self.assertFalse(any(r.source_graph == 0xCF5 for r in fight.rockets))
            self.assertFalse(manager.active)
        npc_control.stop(run.match)
        run.step({})
        for tick in range(90):
            run.step({player: (0, 0, 32 if tick == 0 else 0)})
            if manager.active:
                break
        device, = manager.active.values()
        self.assertEqual(device.source_body, player.body)
        self.assertEqual(script.component.entity_var(342).value(), 0)

    @unittest.skipUnless(
        os.environ.get("ORPHEUS_ORISA_SUPERCHARGER"), "external authored supplement required"
    )
    def test_inflight_supercharger_deploys_and_boosts_ally_during_pin(self):
        run = Run(replace(KANEZAKA_DM, free_for_all=False), ("Orisa", "Ally", "Enemy"))
        player, ally, enemy = run.players
        run.match.switch_hero(player, heroes()[combat_fixture.ORISA])
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        ally.team = player.team
        x, y, z = player.mover.position
        ally.mover.position = [x + 4, y, z + 2]
        enemy.mover.position = [x, y, z + 25]
        for actor in run.players:
            actor.client.bot_stream = lambda _: None
        definition = next(d for d in fixture.OrisaTalonTests.catalog.values() if d.name == "Assassin")
        bot = npc.spawn(run.match, definition, (x, y, z + 2), team=player.team + 1)
        fight = run.match.combat
        manager = fight.orisa.superchargers
        script = player.body_script
        script.ult_seconds = 0
        script.set_ult_charge(script.component.entity_var(338).value())
        for tick in range(90):
            run.step({player: (0, 0, 32 if tick == 0 else 0)})
            if any(r.source_graph == 0xCF5 for r in fight.rockets):
                break
        else:
            self.fail("Supercharger did not launch")

        incoming.OrisaNpcIncomingTests().start(run, player, bot)
        self.until_pin(run, player, bot)
        self.assertFalse(manager.active)
        for _ in range(100):
            run.step({})
            if manager.active:
                break
        device, = manager.active.values()
        self.assertEqual(device.source_body, player.body)
        self.assertTrue(fight.is_stunned(player))
        self.assertEqual(manager.scale(ally), 1.5)
        self.assertEqual(manager.scale(bot), 1)

        target = combat_fixture.OrisaCombatTests().target(run, bot)
        a, b = target.segment()
        centre = tuple((lo + hi) / 2 for lo, hi in zip(a, b, strict=True))
        yaw, pitch = aim(fight._eye(ally), centre)
        vitals = fight.vitals_of(target)
        before = vitals.total
        run.step({ally: (yaw, pitch, FIRE)})
        self.assertTrue(fight.is_stunned(player))
        self.assertEqual(before - vitals.total, 30)

        x, y, z = device.position
        barrier_fixture.BarrierTests.place(enemy, (x - 5, y, z))
        yaw, pitch = aim(fight._eye(enemy), (x, y + 0.4, z))
        hits = 0
        for _ in range(180):
            previous = device.health
            run.step({enemy: (yaw, pitch, FIRE)})
            self.assertTrue(fight.is_stunned(player))
            if device.health < previous:
                self.assertEqual(previous - device.health, 20)
                hits += 1
            if device.entity not in manager.active:
                break
        self.assertEqual(hits, 10)
        self.assertEqual(device.health, 0)
        self.assertNotIn(device.entity, manager.active)
        self.assertNotIn((device.entity, ally.body), manager.buffs)
        self.assertEqual(manager.scale(ally), 1)

    def test_source_removal_and_victim_replacement_clear_pin_identity(self):
        for remove_source in (False, True):
            with self.subTest(remove_source=remove_source):
                run, player, bot, _ = self.arena()
                self.until_pin(run, player, bot)
                old_body = player.body
                fight = run.match.combat
                old_vitals = incoming.OrisaNpcIncomingTests.vitals(run, player)
                if remove_source:
                    self.assertIn(bot.entity, npc.remove_spawned(run.match))
                else:
                    self.assertTrue(run.match.switch_hero(player, player.hero))
                    self.assertNotEqual(player.body, old_body)
                self.assertNotIn(old_body, fight.pins.values())
                before = old_vitals.total
                for _ in range(20):
                    run.step({})
                self.assertEqual(old_vitals.total, before)
                self.assertNotIn(old_body, fight.pins.values())
