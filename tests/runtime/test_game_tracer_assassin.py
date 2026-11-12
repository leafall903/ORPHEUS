"""Actual Assassin body/graphs versus Tracer under declared manual host policy."""

import math
import os
import unittest
from dataclasses import replace
from unittest.mock import patch

import test_game_tracer_ana_nano as nano_tests
import test_game_tracer_npcs as npc_tests
import test_game_tracer_orisa_barrier as orisa_tests
import test_game_tracer_winston_barrier as winston_tests
from test_game_combat import aim

from ow174.game import (
    combat,
    deflection,
    dva,
    lucio_barrier,
    npc,
    npc_control,
    orisa_barrier,
    sigma_barrier,
    sigma_grasp,
    stats,
    tracer,
    zarya_barrier,
)
from ow174.game.body_vm_snapshot import export_component_vm, snapshot_digest
from ow174.game.collision import World
from ow174.game.commands import FIRE, SECONDARY, Command
from ow174.game.content import SOLDIER, heroes
from ow174.game.script import expr


class TracerAssassinTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.addClassCleanup(npc_tests.TracerNpcTests.doClassCleanups)
        npc_tests.TracerNpcTests.setUpClass()
        cls.definition = next(d for d in npc_tests.TracerNpcTests.definitions.values()
                              if d.name == "Assassin")

    def arena(self, *, distance=2.5, engage=True):
        run, player, bot, _, yaw, pitch = npc_tests.TracerNpcTests().fight(
            self.definition, distance=distance)
        player.mover.yaw, player.mover.pitch = yaw, pitch
        if engage:
            npc_control.start(run.match, player)
        target = next(t for t in run.match.combat._targets() if t.owner is player)
        return run, player, bot, run.match.combat.vitals_of(target)

    def contact(self):
        run, player, bot, health = self.arena()
        run.step({})
        self.assertEqual(health.total, 125)
        self.assertEqual(run.match.combat.pins.get(bot.entity), player.body)
        return run, player, bot, health

    def continuous(self):
        run, player, bot, health = self.contact()
        instance = next(i for i in bot.body_script.component.instances.values() if i.graph.index == 0x1157)
        for _ in range(100):
            state = instance.states.get(instance.graph.node(15).state)
            if state is not None and state.active:
                return run, player, bot, health, state
            run.step({})
        self.fail("actual1157 continuous health phase did not start")

    def engage(self, run, player, bot):
        x, y, z = bot.position
        player.mover.yaw, player.mover.pitch = aim(run.match.combat._eye(player), (x, y+1, z))
        npc_control.start(run.match, player)

    def support(self, run, player, bot, hero=SOLDIER):
        actor = next(p for p in run.players if p is not player)
        self.assertTrue(run.match.switch_hero(actor, heroes()[hero]))
        actor.team = player.team
        x, y, z = player.mover.position
        # A fresh body is positioned before its first physical pose.
        actor.mover.position[:] = (x+1, y, z)
        facing = aim(run.match.combat._eye(actor), (bot.position[0], bot.position[1]+1, bot.position[2]))
        return actor, facing

    def test_actual_contact_damages_tracer_and_uses_distinct_typed_controller_target(self):
        run, player, bot, health = self.arena()
        for _ in range(200):
            run.step({})
            if health.total < 150:
                break
        self.assertEqual(health.total, 125)
        component = bot.body_script.component
        instance = next(i for i in component.instances.values() if i.graph.index == 0x1157)
        controller = component.world.controller_entity
        self.assertNotIn(controller, (bot.entity, player.entity, player.body))
        self.assertEqual(instance.evaluate({"$": "STU_DB742151", "m_entity": {"$": "STU_B5A0CAF0"}}),
                         expr.Entity(controller))
        self.assertEqual(instance.context().member(controller, 1650), expr.Entity(player.body))
        self.assertEqual(instance.find_var(expr.INSTANCE, 536).value(), expr.Entity(player.body))
        # A later body cannot collide with the reserved controller identity.
        other = npc.spawn(run.match, self.definition, (100, 0, 100), team=1)
        self.assertNotIn(controller, (other.entity, other.body_script.component.world.controller_entity))
        self.assertIsNone(component.fault)

    def test_manual_pin_crosses_live_dome_and_mesh_barriers(self):
        for hero, asset in (
            (0x02E0000000000009, None),
            (0x02E0000000000007, "ORPHEUS_REINHARDT_COLLISION"),
            (sigma_barrier.HERO, "ORPHEUS_SIGMA_BARRIER_MODEL"),
            (orisa_barrier.HERO, "ORPHEUS_ORISA_GEOMETRY"),
        ):
            with self.subTest(hero=f"{hero:016X}"):
                if asset and not os.environ.get(asset):
                    self.skipTest(f"requires {asset}")
                self.check_pin_across_live_barrier(hero)

    def check_pin_across_live_barrier(self, hero):
        run, player, bot, health = self.arena(distance=35, engage=False)
        fight = run.match.combat
        x, y, z = player.mover.position
        first, pool, held = self.deploy_pin_barrier(run, player, bot, hero)
        ray = first(bot, (x, y + combat.EYE, z + 40), (0, 0, -1), 40)
        self.assertIsNotNone(ray)
        crossing = z + 40 - ray[0]
        winston_tests.TracerWinstonBarrierTests.move(player, (x, y, crossing - 1.25))
        bot.position = (x, y, crossing + 1.25)
        run.step(held)

        def assert_separating():
            origin = (bot.position[0], bot.position[1] + combat.EYE, bot.position[2])
            destination = fight._eye(player)
            length = math.dist(origin, destination)
            direction = tuple((end - start) / length for start, end in zip(origin, destination, strict=True))
            hit = first(bot, origin, direction, length)
            self.assertIsNotNone(hit)
            self.assertTrue(0 < hit[0] < length)

        assert_separating()
        shield = pool()
        self.assertGreater(shield, 0)
        self.engage(run, player, bot)
        run.step(held)
        self.assertEqual(fight.pins.get(bot.entity), player.body)
        self.assertEqual(health.total, 125)
        self.assertEqual(pool(), shield)
        assert_separating()

        blink = next(i for i in player.body_script.component.instances.values() if i.graph.index == 0x24)
        charges = blink.vars[11].value()
        run.step({**held, player: (0, 0, 8)})
        run.step({**held, player: (0, 0, 16)})
        self.assertEqual(blink.vars[11].value(), charges)
        self.assertFalse(tracer.recalling(player))
        for _ in range(100):
            if health.total < 125:
                break
            run.step(held)
        self.assertLess(health.total, 125)
        before = health.total
        for _ in range(10):
            run.step(held)
        self.assertAlmostEqual(before - health.total, 16, places=4)
        self.assertEqual(fight.pins.get(bot.entity), player.body)
        self.assertEqual(pool(), shield)
        assert_separating()

        npc_control.stop(run.match)
        run.step(held)
        self.assertNotIn(bot.entity, fight.pins)
        run.step({**held, player: (0, 0, 8)})
        self.assertEqual(blink.vars[11].value(), charges - 1)

    @unittest.skipUnless(deflection.TAGS_PATH, "verified projectile definition tags required")
    def test_manual_pin_persists_while_actual_trooper_ray_is_intercepted(self):
        for defense in ("deflect", "grasp", "matrix"):
            with self.subTest(defense=defense):
                self.check_pin_and_trooper_ray(defense)

    def check_pin_and_trooper_ray(self, defense):
        run, player, assassin, health = self.arena(engage=False)
        match, fight = run.match, run.match.combat
        hero, graph, state, button = {
            "deflect": (0x02E0000000000029, 0x03A3, 10, 16),
            "grasp": (sigma_grasp.HERO, sigma_grasp.GRASP, 1, 8),
            "matrix": (dva.HERO, 0x02CC, 3, SECONDARY),
        }[defense]
        defender, _ = self.support(run, player, assassin, hero)
        if defense == "matrix":
            assets = ("ORPHEUS_GRAPH_SUPPLEMENT", "ORPHEUS_MOVERS_SUPPLEMENT")
            if not all(os.environ.get(key) for key in assets):
                self.skipTest("requires extracted 0407 Matrix graphs and mover")
            self.assertTrue(match.switch_hero(defender, replace(heroes()[hero], body=dva.MECH)))
        x, y, z = player.mover.position
        move = winston_tests.TracerWinstonBarrierTests.move
        move(defender, (x + 3, y, z))
        for _ in range(100 if defense == "matrix" else 30):
            run.step({})
        definition = next(d for d in npc_tests.TracerNpcTests.definitions.values() if d.name == "Trooper")
        trooper = npc.spawn(match, definition, (x + 3, y, z + 5), team=assassin.team)
        before = fight.vitals[trooper.entity].total
        bx, by, bz = trooper.position
        facing = aim(fight._eye(defender), (bx, by + 1, bz))
        run.step({defender: (*facing, button)})
        instances = defender.body_script.component.instances.values()
        instance = next(i for i in instances if i.graph.index == graph)
        self.assertTrue(instance.state(state).active)
        self.engage(run, player, trooper)
        self.assertEqual(trooper.attack_target, player.entity)
        self.engage(run, player, assassin)
        self.assertEqual(assassin.attack_target, player.entity)

        move(defender, (x + 0.9, y, z + 1.5))
        facing = aim(fight._eye(defender), (bx, by + 1, bz))
        held = {defender: (*facing, button if defense == "matrix" else 0)}
        run.step(held)
        self.assertEqual(health.total, 125)
        self.assertEqual(fight.pins.get(assassin.entity), player.body)
        with (
            patch.object(fight, "_hitscan", wraps=fight._hitscan) as scan,
            patch.object(fight, "_damage", wraps=fight._damage) as damage,
        ):
            for _ in range(75):
                run.step(held)
                if scan.called:
                    break
        self.assertEqual(scan.call_count, 1)
        self.assertIs(scan.call_args.args[0], trooper)
        self.assertFalse(any(call.args[0] is trooper for call in damage.call_args_list))
        self.assertTrue(instance.state(state).active)
        self.assertEqual(fight.pins.get(assassin.entity), player.body)
        self.assertEqual(defender.stats.value(stats.HERO_DAMAGE), 20 if defense == "deflect" else 0)
        self.assertEqual(fight.vitals[trooper.entity].total, before - (20 if defense == "deflect" else 0))
        if defense == "grasp":
            self.assertEqual(instance.vars[21312].value(), 20)
        elif defense == "matrix":
            _, _, origin, direction, reach, *_ = scan.call_args.args
            shot_definition = scan.call_args.kwargs["definition"]
            self.assertIsNotNone(fight._matrix_hit(trooper, origin, direction, reach, shot_definition))
            self.assertLess(instance.vars[87].value(), 100)
        before_tick = health.total
        with patch.object(fight, "_damage", wraps=fight._damage) as ticks:
            for _ in range(100):
                run.step(held)
                if health.total < before_tick:
                    break
        pin_damage = [call.args[2] for call in ticks.call_args_list
                      if call.args[0] is assassin and call.args[1].owner is player]
        self.assertGreater(sum(pin_damage), 0)
        self.assertAlmostEqual(before_tick - health.total, sum(pin_damage), places=4)
        self.assertTrue(instance.state(state).active)
        self.assertEqual(fight.pins.get(assassin.entity), player.body)

    def deploy_pin_barrier(self, run, player, bot, hero):
        fight = run.match.combat
        defender, _ = self.support(run, player, bot, hero)
        x, y, z = player.mover.position
        move = winston_tests.TracerWinstonBarrierTests.move
        if hero == 0x02E0000000000009:
            move(defender, (x + 2.5, y, z))
            dome = winston_tests.TracerWinstonBarrierTests().cast(run, defender)
            return fight.winston_barriers.first, lambda: dome.health, {}
        if hero == orisa_barrier.HERO:
            fixture = orisa_tests.TracerOrisaBarrierTests()
            fixture.run, fixture.fight, fixture.tracer, fixture.orisa = run, fight, player, defender
            fixture.deploy()
            move(defender, (x + 3, defender.mover.position[1], defender.mover.position[2]))
            return fight.orisa_barriers.first, lambda: fixture.barrier.health, {}

        move(defender, (x + 1.3, y, z + 0.5))
        for _ in range(30):
            run.step({})
        guard = {defender: (0, 0, SECONDARY)}
        for _ in range(8 if hero == sigma_barrier.HERO else 18):
            run.step(guard)
        if hero == sigma_barrier.HERO:
            for _ in range(27):
                run.step({})
            barrier, = fight.sigma_barriers.values()
            self.assertTrue(barrier.active and barrier.child_request_seen)

            def first(source, origin, direction, distance):
                return sigma_barrier.barrier_intersection(fight, source, origin, direction, distance)

            return first, lambda: barrier.vitals.total, {}
        shield = fight.reinhardt_barriers.states[defender.body]
        self.assertTrue(shield.active)
        return fight.reinhardt_barriers.first, lambda: shield.current, guard

    def test_continuous_phase_uses_elapsed_clock_without_duplicate_frame_damage(self):
        run, _, bot, health, state = self.continuous()
        before = health.total
        for _ in range(10):
            run.step({})
        self.assertAlmostEqual(before-health.total, 16, places=4)
        stamp, after = state.last_applied, health.total
        run.match.update(run.frame * combat.TICK, run.frame)
        self.assertEqual(health.total, after)
        self.assertEqual(state.last_applied, stamp)
        self.assertIsNone(bot.body_script.component.fault)

    def test_admitted_pin_kills_at_same_tick_during_sparse_world_updates(self):
        expected = None
        for gap in (1, 24, 100):
            with self.subTest(gap=gap):
                run, player, bot, health, _ = self.continuous()
                fight = run.match.combat
                start = run.frame
                health.damage(health.total - 6)
                fight._sync_body_health(player, health)
                self.assertEqual(fight.pins.get(bot.entity), player.body)
                while run.frame < start + 100:
                    run.frame = min(start + 100, run.frame + gap)
                    run.match.update(run.frame * combat.TICK, run.frame)
                self.assertTrue(health.dead)
                self.assertEqual(health.killer, bot.entity)
                self.assertNotIn(bot.entity, fight.pins)
                self.assertFalse(bot.attack_input)
                deadline, = (when for when, owner in fight.respawns if owner is player)
                if gap == 1:
                    expected = health.death_tick - start
                    self.assertGreater(expected, 0)
                    self.assertLess(expected, 10)
                self.assertEqual(health.death_tick - start, expected)
                self.assertAlmostEqual(
                    deadline - (start + expected) * combat.TICK, combat.respawn_seconds(run.match),
                )

    def test_active_pin_uses_nano_expiry_at_each_damage_tick(self):
        expected = self.nano_pin_trace(1)
        self.assertEqual(expected[0][:6], [(-3, 0.8), (-2, 0.8), (-1, 0.8), (0, 1.6), (1, 1.6), (2, 1.6)])
        for gap in (24, 100):
            with self.subTest(gap=gap):
                self.assertEqual(self.nano_pin_trace(gap), expected)

    def nano_pin_trace(self, gap):
        fixture = nano_tests.TracerAnaNanoTests()
        fixture.setUp()
        fixture.cast()
        run, player, fight, health = fixture.run, fixture.player, fixture.fight, fixture.health
        for actor in run.players:
            actor.client = npc_tests.NpcClient(actor)
        x, y, z = player.mover.position
        bot = npc.spawn(run.match, self.definition, (x, y, z + 2.5), team=1)
        expiry = health.nano_until
        self.assertEqual(expiry - fight.tick, 500)
        while run.frame < expiry - 80:
            run.step({})
        self.engage(run, player, bot)
        run.step({})
        self.assertEqual(health.total, 137.5)
        while run.frame < expiry - 4:
            run.step({})
        self.assertEqual(fight.pins.get(bot.entity), player.body)

        ticks = []
        apply_damage = fight._damage

        def record_damage(source, target, *args, **kwargs):
            dealt = apply_damage(source, target, *args, **kwargs)
            if source is bot and target.owner is player:
                ticks.append((kwargs["world_tick"] - expiry, round(dealt, 4)))
            return dealt

        end = run.frame + 100
        with patch.object(fight, "_damage", side_effect=record_damage):
            while run.frame < end:
                run.frame = min(run.frame + gap, end)
                run.match.update(run.frame * combat.TICK, run.frame)
        return ticks, health.total, health.death_tick, health.killer, dict(fight.pins)

    def test_unadmitted_pin_does_not_start_before_retained_combat_frames(self):
        run, player, bot, health = self.arena()
        fight = run.match.combat
        self.assertNotIn(bot.entity, fight.pins)
        self.assertEqual(health.total, 150)
        run.frame += 100
        with patch.object(fight, "_damage", wraps=fight._damage) as damage:
            run.match.update(run.frame * combat.TICK, run.frame)
        self.assertTrue(damage.called)
        contact = damage.call_args_list[0]
        self.assertIs(contact.args[0], bot)
        self.assertIs(contact.args[1].owner, player)
        self.assertEqual(contact.args[2], 25)
        self.assertGreaterEqual(contact.kwargs["world_tick"], run.frame - combat.COMBAT_CATCH_UP + 1)

    def test_admitted_pin_resolves_against_sound_barrier_decay_during_long_gap(self):
        if lucio_barrier.model() is None:
            self.skipTest("requires extracted Sound Barrier status")
        for gap in (1, 100):
            with self.subTest(gap=gap):
                run, player, bot, health, _ = self.continuous()
                fight = run.match.combat
                lucio, _ = self.support(run, player, bot, hero=0x02E0000000000079)
                script = lucio.body_script
                script.set_ult_charge(script.component.entity_var(338).value())
                run.step({lucio: (0, 0, 32)})
                for _ in range(60):
                    run.step({})
                    if player.body in fight.lucio_barriers.leases:
                        break
                self.assertEqual(fight.pins.get(bot.entity), player.body)
                shield = health.temporary_shields[lucio_barrier.EFFECT]
                self.assertGreater(shield.now, 9)
                # Isolate the last few frames of the actual shield and a wounded body.
                shield.now = 9
                health.now[combat.HEALTH_POOL] = 6
                fight._sync_body_health(player, health)
                start = run.frame
                while run.frame < start + 100:
                    run.frame += gap
                    run.match.update(run.frame * combat.TICK, run.frame)
                self.assertTrue(health.dead)
                self.assertEqual(health.death_tick, start + 6)
                self.assertEqual(health.killer, bot.entity)
                self.assertNotIn(bot.entity, fight.pins)

    def test_pin_contact_and_continuous_ticks_preserve_authored_damage_tag(self):
        run, player, bot, health = self.arena()
        fight = run.match.combat
        with patch.object(fight, "_damage", wraps=fight._damage) as damage:
            for _ in range(100):
                run.step({})
                if damage.call_count >= 3:
                    break
        self.assertEqual(damage.call_count, 3)
        for call, amount in zip(damage.call_args_list, (25, 0.7, 1.6), strict=True):
            self.assertIs(call.args[0], bot)
            self.assertIs(call.args[1].owner, player)
            self.assertAlmostEqual(call.args[2], amount, places=5)
            self.assertEqual(call.kwargs.get("tags"), (0x0D8000000000302F,))
        self.assertAlmostEqual(health.total, 122.7, places=4)

    def test_continuous_clock_is_exported_without_evaluation(self):
        _, _, bot, _, state = self.continuous()
        component = bot.body_script.component
        before = snapshot_digest(export_component_vm(component))
        stamp = state.last_applied
        state.last_applied -= 16
        self.assertNotEqual(snapshot_digest(export_component_vm(component)), before)
        state.last_applied = stamp
        self.assertEqual(snapshot_digest(export_component_vm(component)), before)

    def test_pin_rejects_tracer_actions_and_movement_then_stop_releases_blink(self):
        run, player, bot, health = self.contact()
        script = player.body_script
        script.ult_seconds = 0
        cost = script.component.entity_var(338).value()
        script.set_ult_charge(cost)
        start = tuple(player.mover.position)
        for _ in range(5):
            run.frame += 1
            run.match.update(run.frame * combat.TICK, run.frame)
            player.take_commands([Command(run.frame, buttons=1|2|8|16|32|0x800, forward=127)])
        self.assertEqual(tuple(player.mover.position), start)
        self.assertTrue(run.match.combat.is_stunned(player))
        weapon = next(i for i in script.component.instances.values() if i.graph.index == 0x16)
        blink = next(i for i in script.component.instances.values() if i.graph.index == 0x24)
        self.assertEqual(weapon.vars[53].value(), 40)
        self.assertEqual(blink.vars[11].value(), 3)
        self.assertEqual(script.component.entity_var(342).value(), cost)
        self.assertFalse(tracer.recalling(player))
        self.assertFalse(run.match.combat.pulse_bombs.bombs)
        npc_control.stop(run.match)
        self.assertNotIn(bot.entity, run.match.combat.pins)
        self.assertFalse(run.match.combat.is_stunned(player))
        run.step({player: (0, 0, 8)})
        self.assertEqual(blink.vars[11].value(), 2)
        self.assertGreater(player.mover.position[2]-start[2], 7.4)
        self.assertEqual(health.total, 125)

    def project_barrier(self, run, player, bot):
        zarya, _ = self.support(run, player, bot, hero=0x02E0000000000068)
        x, y, z = player.mover.position
        zarya.mover.position[:] = (x + 5, y, z)
        zarya.mover.body = None
        fight = run.match.combat
        facing = aim(fight._eye(zarya), fight._eye(player))
        run.step({zarya: (*facing, 0)})
        run.step({zarya: (*facing, 16)})
        pet = zarya_barrier.active(player)
        self.assertIsNotNone(pet)
        self.assertEqual(pet.instance.component.entity, zarya.body)
        return zarya, pet

    def test_projected_barrier_absorbs_pin_contact_and_ticks_while_input_stays_locked(self):
        run, player, bot, health = self.arena(engage=False)
        fight = run.match.combat
        zarya, pet = self.project_barrier(run, player, bot)
        energy = zarya.body_script.component.entity_var(87)
        self.assertEqual(zarya_barrier.barrier_health(pet, health), 200)
        self.engage(run, player, bot)
        run.step({})
        self.assertEqual(fight.pins.get(bot.entity), player.body)
        self.assertEqual(health.total, 150)
        self.assertEqual(zarya_barrier.barrier_health(pet, health), 175)
        self.assertEqual(energy.value(), 5)

        instance = next(i for i in bot.body_script.component.instances.values() if i.graph.index == 0x1157)
        for _ in range(100):
            state = instance.states.get(instance.graph.node(15).state)
            if state is not None and state.active:
                break
            run.step({})
        self.assertIsNotNone(state)
        self.assertTrue(state.active)
        shield = zarya_barrier.barrier_health(pet, health)
        before = energy.value()
        pos = tuple(player.mover.position)
        script = player.body_script.component
        blink = next(i for i in script.instances.values() if i.graph.index == 0x24)
        for _ in range(4):
            run.step({player: (16384, 0, FIRE | 8 | 16)})
            self.assertEqual(health.total, 150)
            self.assertEqual(fight.pins.get(bot.entity), player.body)
            self.assertEqual(tuple(player.mover.position), pos)
        self.assertEqual(blink.vars[11].value(), 3)
        self.assertFalse(tracer.recalling(player))
        self.assertAlmostEqual(shield - zarya_barrier.barrier_health(pet, health), 6.4, places=4)
        self.assertAlmostEqual(energy.value() - before, 1.28, places=4)

        self.assertEqual(npc_control.stop(run.match), 1)
        run.step({})
        self.assertIs(zarya_barrier.active(player), pet)
        self.assertNotIn(bot.entity, fight.pins)
        run.step({player: (16384, 0, 8)})
        self.assertGreater(player.mover.position[0] - pos[0], 7.4)
        self.assertEqual(blink.vars[11].value(), 2)
        self.assertEqual(health.total, 150)

    def test_projected_barrier_cast_during_pin_breaks_without_spill_then_ticks_resume(self):
        run, player, bot, health, _ = self.continuous()
        fight = run.match.combat
        zarya, pet = self.project_barrier(run, player, bot)
        energy = zarya.body_script.component.entity_var(87)
        before = health.total
        baseline = energy.value() or 0
        self.assertEqual(fight.pins.get(bot.entity), player.body)
        # Put the live projected pool one point from breaking.
        health.particle_barriers[zarya_barrier.particle_barrier_key(pet)] = 1
        run.step({})
        self.assertIsNone(zarya_barrier.active(player))
        self.assertEqual(health.total, before)
        self.assertAlmostEqual(energy.value() - baseline, 0.2, places=5)
        self.assertEqual(fight.pins.get(bot.entity), player.body)
        run.step({})
        self.assertAlmostEqual(before - health.total, 1.6, places=4)

    def test_manual_stop_has_no_late_damage_and_explicit_reengagement_can_pin_again(self):
        run, player, bot, health, _ = self.continuous()
        npc_control.stop(run.match)
        before = health.total
        for _ in range(100):
            run.step({})
        self.assertEqual(health.total, before)
        self.assertFalse(run.match.combat.pins)
        self.engage(run, player, bot)
        run.step({})
        self.assertAlmostEqual(health.total, before-25, places=4)
        self.assertEqual(run.match.combat.pins.get(bot.entity), player.body)

    def test_lethal_pin_releases_tracer_and_resurrect_protects_until_reengagement(self):
        for continuous in (False, True):
            with self.subTest(continuous=continuous):
                self.check_pin_resurrect(continuous=continuous)

    def test_revived_tracer_can_blink_after_rising_during_damage_protection(self):
        self.check_pin_resurrect(continuous=False, blink_on_rise=True)

    def check_pin_resurrect(self, *, continuous, blink_on_rise=False):
        run, player, bot, health = self.arena(engage=False)
        fight = run.match.combat
        mercy, _ = self.support(run, player, bot, hero=0x02E0000000000004)
        x, y, z = player.mover.position
        mercy.mover.position[:] = (x, y, z - 3)
        mercy.mover.body = None
        facing = aim(fight._eye(mercy), fight._eye(player))
        for _ in range(30):
            run.step({mercy: (*facing, 0)})
        health.damage(health.total - (26 if continuous else 25))
        fight._sync_body_health(player, health)
        body = player.body
        self.engage(run, player, bot)
        run.step({mercy: (*facing, 0)})
        if continuous:
            self.assertEqual(health.total, 1)
            self.assertEqual(fight.pins.get(bot.entity), body)
            for _ in range(100):
                run.step({mercy: (*facing, 0)})
                if health.dead:
                    break
        self.assertTrue(health.dead)
        self.assertEqual(health.killer, bot.entity)
        self.assertNotIn(bot.entity, fight.pins)
        run.step({mercy: (*facing, 0)})
        self.assertFalse(bot.attack_input)
        self.assertTrue(bot.firing)
        self.assertEqual(bot.attack_target, player.entity)

        run.step({mercy: (*facing, 16)})
        for _ in range(125):
            run.step({mercy: (*facing, 0)})
            self.assertNotIn(bot.entity, fight.pins)
            if not health.dead:
                break
        self.assertFalse(health.dead)
        self.assertEqual(player.body, body)
        self.assertEqual(health.total, 150)
        self.assertFalse(any(owner is player for _, owner in fight.respawns))
        protection = health.revive_protection_until
        self.assertGreater(protection, health.rising_until)
        self.assertGreater(health.rising_until, fight.tick)
        if blink_on_rise:
            while run.frame + 1 < health.rising_until:
                run.step({})
                self.assertNotIn(bot.entity, fight.pins)
            before = player.mover.position[0]
            run.step({player: (16384, 0, 8)})
            self.assertEqual(fight.tick, health.rising_until)
            self.assertLess(fight.tick, protection)
            self.assertGreater(player.mover.position[0] - before, 7.4)
            self.assertEqual(health.total, 150)
            self.assertNotIn(bot.entity, fight.pins)
            return

        while run.frame + 1 < protection:
            run.step({mercy: (*facing, 0)})
            self.assertEqual(health.total, 150)
            self.assertNotIn(bot.entity, fight.pins)
            self.assertFalse(bot.attack_input)

        run.step({mercy: (*facing, 0)})
        self.assertEqual(fight.tick, protection)
        self.assertEqual(fight.pins.get(bot.entity), body)
        self.assertEqual(health.total, 125)

    def test_assassin_retargets_replacement_tracer_body_after_normal_respawn_or_live_swap(self):
        for lethal in (False, True):
            with self.subTest(lethal=lethal):
                self.check_pin_replacement(lethal=lethal)

    def check_pin_replacement(self, *, lethal):
        run, player, bot, health = self.arena()
        fight = run.match.combat
        old_body, old_script = player.body, player.body_script
        entity = player.entity
        component = bot.body_script.component
        old_position = tuple(bot.position)
        x, y, z = player.mover.position
        player.spawn = ((x + 10, y, z), player.spawn[1])
        if lethal:
            health.damage(health.total - 25)
            fight._sync_body_health(player, health)
        run.step({})
        if lethal:
            self.assertTrue(health.dead)
            self.assertEqual(health.killer, bot.entity)
            self.assertNotIn(bot.entity, fight.pins)
            deadline, = (when for when, owner in fight.respawns if owner is player)
            for _ in range(round(combat.respawn_seconds(run.match) / combat.TICK) + 2):
                run.step({})
                self.assertNotIn(old_body, fight.pins.values())
                if player.body != old_body:
                    break
                self.assertTrue(health.dead)
                self.assertFalse(bot.attack_input)
            self.assertGreaterEqual(fight.now, deadline)
        else:
            self.assertEqual(fight.pins.get(bot.entity), old_body)
            self.assertEqual(health.total, 125)
            for _ in range(100):
                run.step({})
                if health.total < 125:
                    break
            self.assertAlmostEqual(health.total, 124.3, places=4)
            self.assertEqual(fight.pins.get(bot.entity), old_body)
            self.assertTrue(run.match.switch_hero(player, player.hero))
            self.assertNotIn(old_body, fight.pins.values())
            run.step({})

        self.assertNotEqual(player.body, old_body)
        self.assertEqual(player.entity, entity)
        self.assertTrue(old_script.retired)
        self.assertNotIn(old_body, fight.vitals)
        self.assertNotIn(bot.entity, fight.pins)
        self.assertIsNone(component.world.manual_target)
        self.assertTrue(bot.firing)
        self.assertEqual(bot.attack_target, entity)
        new_body = player.body
        health = fight.vitals_of(next(t for t in fight._targets() if t.entity == new_body))
        self.assertEqual(health.total, 150)
        for _ in range(150):
            run.step({})
            self.assertNotIn(old_body, fight.pins.values())
            if bot.entity in fight.pins:
                break
            self.assertEqual(health.total, 150)
        self.assertGreater(bot.position[0] - old_position[0], 6)
        self.assertEqual(fight.pins.get(bot.entity), new_body)
        self.assertEqual(health.total, 125)
        self.assertEqual(component.world.manual_target, new_body)
        pin = next(i for i in component.instances.values() if i.graph.index == 0x1157)
        self.assertEqual(pin.find_var(expr.INSTANCE, 536).value(), expr.Entity(new_body))

    def test_reusing_tracer_body_id_in_same_frame_does_not_transfer_continuous_pin(self):
        run, player, bot, _, state = self.continuous()
        fight = run.match.combat
        old_body, old_script = player.body, player.body_script
        component = bot.body_script.component
        self.assertEqual(component.world.manual_target, old_body)
        self.assertTrue(state.active)
        frame = run.frame
        self.assertTrue(run.match.switch_hero(player, player.hero))
        self.assertNotEqual(player.body, old_body)
        retired_target = component.world.manual_target
        # Emulate the ID ring when the retired ID is the only available slot.
        with patch.object(player.client, "body_id_available", side_effect=lambda body: body == old_body):
            self.assertTrue(run.match.switch_hero(player, player.hero))
        self.assertEqual(player.body, old_body)
        self.assertEqual(run.frame, frame)
        self.assertTrue(old_script.retired)
        self.assertNotIn(bot.entity, fight.pins)
        health = fight.vitals_of(next(t for t in fight._targets() if t.owner is player))
        self.assertEqual(health.total, 150)

        self.assertIsNone(retired_target)
        self.assertFalse(state.active)
        for _ in range(60):
            run.step({})
            if health.total < 150:
                break
            self.assertNotIn(bot.entity, fight.pins)
        self.assertEqual(health.total, 125)
        self.assertEqual(fight.pins.get(bot.entity), player.body)

    def test_recall_before_contact_blocks_pin_then_contact_can_resume_after_expiry(self):
        run, player, bot, health = self.arena(engage=False)
        run.step({})
        run.step({player: (0, 0, 16)})
        self.assertTrue(tracer.recalling(player))
        self.engage(run, player, bot)
        for _ in range(100):
            if not tracer.recalling(player):
                break
            self.assertEqual(health.total, 150)
            self.assertNotIn(bot.entity, run.match.combat.pins)
            run.step({})
        self.assertFalse(tracer.recalling(player))
        for _ in range(50):
            run.step({})
            if health.total < 150:
                break
        self.assertEqual(health.total, 125)
        self.assertEqual(run.match.combat.pins.get(bot.entity), player.body)

    def test_pin_interrupts_unreleased_pulse_without_refund_or_delayed_bomb(self):
        run, player, bot, health = self.arena(engage=False)
        script = player.body_script
        script.ult_seconds = 0
        script.set_ult_charge(script.component.entity_var(338).value())
        run.step({player: (0, 0, 32)})
        self.assertFalse(run.match.combat.pulse_bombs.bombs)
        self.engage(run, player, bot)
        run.step({})
        self.assertEqual(health.total, 125)
        self.assertEqual(script.component.entity_var(342).value(), 0)

        self.assertEqual(run.match.combat.pins.get(bot.entity), player.body)
        npc_control.stop(run.match)
        for _ in range(40):
            run.step({})
        self.assertFalse(run.match.combat.pulse_bombs.bombs)
        self.assertEqual(script.component.entity_var(342).value(), 0)

    def test_released_pulse_kills_active_assassin_and_releases_tracer(self):
        for already_stuck, lethal, protected in (
            (False, True, True), (True, True, True), (True, False, True), (True, True, False),
        ):
            with self.subTest(already_stuck=already_stuck, lethal=lethal, protected=protected):
                self.check_released_pulse_against_pin(
                    already_stuck=already_stuck, lethal=lethal, protected=protected,
                )

    def check_released_pulse_against_pin(self, *, already_stuck, lethal, protected):
        run, player, bot, health = self.arena(engage=False)
        fight = run.match.combat
        attacker = fight.vitals_of(next(t for t in fight._targets() if t.owner is bot))
        if lethal:
            attacker.damage(attacker.total - 350)
        before = attacker.total
        script = player.body_script
        script.ult_seconds = 0
        script.set_ult_charge(script.component.entity_var(338).value())
        run.step({player: (0, 0, 32)})
        bombs = fight.pulse_bombs.bombs
        for _ in range(50):
            run.step({})
            if bombs and (not already_stuck or bombs[0].stuck):
                break
        bomb, = bombs
        self.assertIs(bomb.shooter, player)
        self.assertEqual(bomb.stuck, already_stuck)
        self.assertEqual(script.component.entity_var(342).value(), 0)
        self.engage(run, player, bot)
        run.step({})
        self.assertEqual(fight.pins.get(bot.entity), player.body)
        self.assertEqual(health.total, 125)
        self.assertIn(bomb, bombs)
        for _ in range(75):
            if bomb.stuck:
                break
            run.step({})
        self.assertEqual(bomb.attached, bot.entity)
        self.assertEqual(attacker.total, before - 5)
        if protected:
            self.project_barrier(run, player, bot)
        for _ in range(75):
            if not bombs:
                break
            self.assertEqual(fight.pins.get(bot.entity), player.body)
            run.step({})
        self.assertFalse(bombs)
        self.assertEqual(attacker.total, max(0, before - 355))
        self.assertEqual(script.component.entity_var(342).value(), 0)
        self.assertEqual(health.dead, not protected)
        if not lethal:
            self.assertFalse(bot.dead)
            self.assertEqual(fight.pins.get(bot.entity), player.body)
            return
        self.assertTrue(bot.dead)
        self.assertEqual(attacker.killer, player.body)
        self.assertIn(bot, run.match.bots)
        self.assertNotIn(bot.entity, fight.pins)
        if not protected:
            self.assertEqual(health.killer, player.body)
            return
        self.assertFalse(fight.is_stunned(player))
        after = health.total
        start = player.mover.position[0]
        run.step({player: (16384, 0, 8)})
        self.assertGreater(player.mover.position[0] - start, 7.4)
        for _ in range(30):
            run.step({})
        self.assertEqual(health.total, after)

    def test_active_pin_and_pulse_detonation_preserve_same_frame_attribution_across_gaps(self):
        for pin_lethal in (False, True):
            expected = self.pin_pulse_deaths(1, pin_lethal=pin_lethal)
            for gap in (24, 100):
                with self.subTest(gap=gap, pin_lethal=pin_lethal):
                    self.assertEqual(self.pin_pulse_deaths(gap, pin_lethal=pin_lethal), expected)

    def pin_pulse_deaths(self, gap, *, pin_lethal):
        run, player, bot, health = self.arena(engage=False)
        fight = run.match.combat
        attacker = fight.vitals_of(next(t for t in fight._targets() if t.owner is bot))
        attacker.damage(attacker.total - 350)
        script = player.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        run.step({player: (0, 0, 32)})
        bombs = fight.pulse_bombs.bombs
        for _ in range(40):
            if bombs:
                break
            run.step({})
        bomb, = bombs
        self.assertFalse(bomb.stuck)
        self.engage(run, player, bot)
        for _ in range(75):
            if bomb.stuck and bomb.fuse_left <= combat.TICK:
                break
            run.step({})
        self.assertEqual(bomb.attached, bot.entity)
        self.assertGreater(bomb.fuse_left, 0)
        self.assertLessEqual(bomb.fuse_left, combat.TICK)
        self.assertEqual(fight.pins.get(bot.entity), player.body)
        instance = next(i for i in bot.body_script.component.instances.values() if i.graph.index == 0x1157)
        self.assertTrue(instance.state(instance.graph.node(15).state).active)
        health.damage(health.total - (0.5 if pin_lethal else 2))
        fight._sync_body_health(player, health)

        start = run.frame
        while run.frame < start + 100:
            run.frame = min(run.frame + gap, start + 100)
            run.match.update(run.frame * combat.TICK, run.frame)
        self.assertTrue(health.dead)
        self.assertTrue(attacker.dead)
        self.assertEqual((health.death_tick, attacker.death_tick), (start + 1, start + 1))
        self.assertEqual(health.killer, bot.entity if pin_lethal else player.body)
        self.assertEqual(attacker.killer, player.body)
        self.assertFalse(bombs)
        self.assertNotIn(bot.entity, fight.pins)
        self.assertFalse(bot.attack_input)
        self.assertIn(bot, run.match.bots)
        deadline, = (when for when, owner in fight.respawns if owner is player)
        return health.death_tick, attacker.death_tick, health.killer, attacker.killer, round(deadline, 6)

    def test_default_five_metre_engagement_approaches_with_authored_mover_and_pins(self):
        run, _, bot, health = self.arena(distance=5)
        start = bot.position
        for _ in range(200):
            run.step({})
            if health.total < 150:
                break
        self.assertEqual(health.total, 125)
        self.assertLess(bot.position[2], start[2]-2)
        self.assertEqual(bot.navigation.mover.data.stand_height, 1.5)
        self.assertEqual(bot.navigation.mover.data.stand_radius, 0.5)
        self.assertEqual(bot.speed, 14)

    def ice_wall(self, run, player, bot):
        x, y, z = player.mover.position
        actor, _ = self.support(run, player, bot, 0x02E00000000000DD)
        actor.mover.position[:] = (x, y, z + 8)
        actor.mover.body = None
        centre = (x, y, (z + bot.position[2]) / 2)
        facing = aim(run.match.combat._eye(actor), centre)
        for buttons in (16, 0, FIRE, 0):
            run.step({actor: (*facing, buttons)})
        self.assertEqual(len(run.match.combat.walls.pieces), 5)
        return list(run.match.combat.walls.pieces.values())[2]

    def test_assassin_routes_around_actual_ice_wall_before_pinning_tracer(self):
        run, player, bot, health = self.arena(distance=5)
        wall = self.ice_wall(run, player, bot)
        walls = run.match.combat.walls
        x = bot.position[0]
        radius = 0.5  # authored Assassin movement capsule
        furthest = 0
        for _ in range(240):
            run.step({})
            lateral = abs(bot.position[0] - x)
            furthest = max(furthest, lateral)
            if abs(bot.position[2] - wall.feet[2]) < wall.depth + radius - 0.005:
                self.assertGreaterEqual(lateral, 2.5 * wall.width + radius - 0.005)
            if health.total < 150:
                break
        self.assertIn(wall.entity, walls.pieces)
        self.assertGreater(furthest, 4)
        self.assertEqual(health.total, 125)
        self.assertEqual(run.match.combat.pins.get(bot.entity), player.body)

    def test_actual_ice_wall_breaks_existing_assassin_pin(self):
        run, player, bot, health, _ = self.continuous()
        wall = self.ice_wall(run, player, bot)
        before = health.total
        self.assertNotIn(bot.entity, run.match.combat.pins)
        for _ in range(10):
            run.step({})
        self.assertIn(wall.entity, run.match.combat.walls.pieces)
        self.assertEqual(health.total, before)

    def test_actual_support_primary_kills_source_clears_pin_and_retains_corpse(self):
        run, player, bot, health, _ = self.continuous()
        actor, facing = self.support(run, player, bot)
        target = next(t for t in run.match.combat._targets() if t.owner is bot)
        run.match.combat.vitals_of(target).now.update({combat.HEALTH_POOL: 11, combat.SHIELDS_POOL: 0})
        run.step({actor: (*facing, 1)})
        self.assertTrue(bot.dead)
        self.assertNotIn(bot.entity, run.match.combat.pins)
        before = health.total
        for _ in range(200):
            run.step({})
        self.assertEqual(health.total, before)
        self.assertIn(bot, run.match.bots)
        self.assertTrue(bot.retain_corpse)
        self.assertIsNone(bot.body_script.component.world.manual_target)

    def test_actual_support_flash_releases_pin_until_exclusive_stun_expiry(self):
        run, player, bot, health, _ = self.continuous()
        actor, facing = self.support(run, player, bot, 0x02E0000000000042)
        run.step({actor: (*facing, 16)})
        for _ in range(50):
            if run.match.combat.is_stunned(bot):
                break
            run.step({})
        self.assertTrue(run.match.combat.is_stunned(bot))
        self.assertNotIn(bot.entity, run.match.combat.pins)
        before = health.total
        while run.frame < run.match.combat.stuns[bot.entity]-1:
            run.step({})
            self.assertEqual(health.total, before)
        self.assertFalse(run.match.combat.is_stunned(player))

    def test_removing_source_releases_control_without_late_health_ticks(self):
        run, player, bot, health, _ = self.continuous()
        self.assertIn(bot.entity, npc.remove_spawned(run.match))
        self.assertFalse(run.match.combat.is_stunned(player))
        before = health.total
        for _ in range(100):
            run.step({})
        self.assertEqual(health.total, before)

    def test_actual_sleep_dart_releases_pin_and_tracer_pistols_shorten_sleep(self):
        run, player, bot, health, state = self.continuous()
        fight = run.match.combat
        ana, _ = self.support(run, player, bot, hero=0x02E000000000013B)
        x, y, z = bot.position
        ana.mover.position[:] = (x - 4, y, z)
        ana.mover.body = None
        dart = aim(fight._eye(ana), (x, y + 1, z))
        run.step({ana: (*dart, 8)})
        target = next(t for t in fight._targets() if t.owner is bot)
        attacker_health = fight.vitals_of(target)
        for _ in range(55):
            run.step({ana: (*dart, 0)})
            if attacker_health.sleep_until:
                break
        self.assertEqual(attacker_health.sleep_until - fight.tick, combat.SLEEP_TICKS)
        self.assertNotIn(bot.entity, fight.pins)
        self.assertFalse(state.active)
        self.assertFalse(fight.is_stunned(player))
        released_health = health.total
        original_deadline = attacker_health.sleep_until
        fire = aim(fight._eye(player), (x, y + 1, z))
        before = attacker_health.total
        run.step({player: (*fire, FIRE)})
        self.assertEqual(before - attacker_health.total, 12)
        self.assertEqual(attacker_health.sleep_until, original_deadline)
        while run.frame + 1 < attacker_health.sleep_wake_after:
            run.step({})
            self.assertEqual(health.total, released_health)
        before = attacker_health.total
        run.step({player: (*fire, FIRE)})
        self.assertEqual(before - attacker_health.total, 12)
        shortened = attacker_health.sleep_until
        self.assertEqual(shortened, fight.tick + combat.SLEEP_WAKE_TICKS)
        self.assertLess(shortened, original_deadline)
        while run.frame + 1 < shortened:
            run.step({})
            self.assertEqual(health.total, released_health)
            self.assertNotIn(bot.entity, fight.pins)
        run.step({})
        self.assertEqual(fight.pins.get(bot.entity), player.body)
        self.assertEqual(health.total, released_health - 25)

    def test_new_wall_invalidates_active_pin_and_stops_health_clock(self):
        run, player, bot, health, _ = self.continuous()
        x, y, z = player.mover.position
        face = z+1.25
        triangles = [
            (x-50, y, z-50, x-50, y, z+50, x+50, y, z+50),
            (x-50, y, z-50, x+50, y, z+50, x+50, y, z-50),
            (x-5, y, face, x+5, y, face, x+5, y+4, face),
            (x-5, y, face, x+5, y+4, face, x-5, y+4, face),
            (x-5, y, face, x+5, y+4, face, x+5, y, face),
            (x-5, y, face, x-5, y+4, face, x+5, y+4, face),
        ]
        run.match.collision_world = World(triangles, name="Assassin active-pin wall")
        for actor in run.players:
            actor.mover.use(world=run.match.collision_world)
        before = health.total
        run.step({})
        self.assertNotIn(bot.entity, run.match.combat.pins)
        for _ in range(100):
            run.step({})
        self.assertEqual(health.total, before)


if __name__ == "__main__":
    unittest.main()
