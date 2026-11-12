"""Authored owned0407 creation, static command handoff and return."""

import math
import unittest
from unittest.mock import patch

import test_game_dva_mech as mech_tests
import test_server_snapshot as storage_tests
from test_game_combat import KANEZAKA_DM, Run, aim, dva_pilot_input

from ow174.game import body_entities, fusion_spread, npc, weapon_aim
from ow174.game.body_vm_snapshot import export_component_vm, snapshot_digest
from ow174.game.content import find_hero
from ow174.game.script import entity_queries, expr, nodes, runtime
from ow174.game.script.driver import ULT_CHARGE, ULT_COST


class CreatedBodyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        mech_tests.AuthoredMechTests.setUpClass()

    def scenario(self):
        run = Run(KANEZAKA_DM, ("DVa", "Other"))
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        pilot, other = run.players
        self.assertTrue(run.match.switch_hero(pilot, find_hero("dva")))
        instance = next(i for i in pilot.body_script.component.instances.values() if i.graph.index == 0x26A)
        created = [bot for bot in run.match.bots if bot.owner == pilot.entity]
        self.assertEqual(len(created), 1)
        return run, pilot, other, instance, created[0]

    def test_hero_pick_runs_actual_creation_action_with_authored_pools_graphs_and_direct_owner(self):
        run, pilot, _, instance, bot = self.scenario()
        self.assertEqual(instance.vars[38].value(), expr.Entity(bot.entity))
        self.assertEqual((bot.owner, bot.source_body, bot.team), (pilot.entity, pilot.body, pilot.team))
        self.assertEqual(bot.body, body_entities.MECH)
        self.assertEqual(run.match.combat.vitals[bot.entity].total, 600)
        self.assertEqual(run.match.combat.vitals[bot.entity].now, {0: 300, 1: 300, 2: 0})
        self.assertTrue({0x50A, 0x47E, 0x2CC, 0x1128}.issubset(
            {i.graph.index for i in bot.body_script.component.instances.values()}))
        self.assertEqual(bot.body_script.component.entity_var(253).value(), 600)
        self.assertEqual(instance.evaluate({"$": "STU_F7262B36", "m_801B298F": expr.Entity(bot.entity)}),
                         expr.Entity(pilot.entity))
        expected = instance.evaluate(instance.graph.node(7).field("m_position"))
        self.assertEqual(tuple(bot.position), tuple(expected))
        self.assertNotEqual(pilot.body, bot.entity)  # creation alone does not prove possession.
        self.assertIs(pilot.controlled_mech, bot)

    def test_normal_pick_routes_actual_fire_to_initial_mech(self):
        from ow174.game.combat import CRITICAL, HIT, Vitals

        run, pilot, other, instance, mech = self.scenario()
        script = mech.body_script
        self.assertIs(pilot.controlled_mech, mech)
        x, y, z = mech.position
        other.mover.position = [x, y, z + 2]
        other.mover.body = None
        for _ in range(100):
            run.step({})
        fight = run.match.combat
        vitals = Vitals(other.body, 1000)
        fight.vitals[other.body] = vitals
        target = fight.player_target(other)
        x, y, z = target.feet
        yaw, pitch = aim(fight._eye(mech), (x, y + target.height * .4, z))
        run.step({pilot: (yaw, pitch, 1)})
        self.assertEqual(vitals.total, 978)
        hits = pilot.client.messages(HIT)
        self.assertEqual(len(hits), 11)
        self.assertTrue(all(not hit["+0x78"]["+0x8"] & CRITICAL for hit in hits))
        self.assertEqual(script.frame, run.frame)
        self.assertTrue(instance.state(11).active)
        self.assertFalse(pilot.body_script.component.held)

    def test_passive_ultimate_meters_across_lethal_demech(self):
        from ow174.game.combat import Vitals

        run, pilot, enemy, _, mech = self.scenario()
        fight = run.match.combat
        pilot_script = pilot.body_script
        pilot_component = pilot_script.component
        mech_component = mech.body_script.component
        pilot_meter = pilot_component.entity_var(ULT_CHARGE)
        mech_meter = mech_component.entity_var(ULT_CHARGE)
        x, y, z = mech.position
        enemy.mover.position[:] = (x, y, z + 4)
        enemy.mover.body = None
        for _ in range(100):
            run.step({})
        pilot_before, mech_before = pilot_meter.value(), mech_meter.value()
        for _ in range(30):
            run.step({})
        pilot_step = pilot_component.entity_var(ULT_COST).value() * pilot_component.frame_ms
        mech_step = mech_component.entity_var(ULT_COST).value() * mech_component.frame_ms
        self.assertAlmostEqual(pilot_meter.value() - pilot_before, 30 * pilot_step / 60000, places=3)
        self.assertAlmostEqual(mech_meter.value() - mech_before, 30 * mech_step / 60000, places=3)

        mech_health = fight.vitals[mech.entity] = Vitals(mech.entity, 5)
        yaw, pitch = aim(fight._eye(enemy), (x, y + 1.2, z))
        run.step({enemy: (yaw, pitch, 1)})
        self.assertTrue(mech_health.dead)
        run.step({})
        self.assertIsNone(pilot.controlled_mech)
        self.assertIs(fight.player_target(pilot).owner, pilot)
        pilot_before, mech_before = pilot_meter.value(), mech_meter.value()
        for _ in range(30):
            run.step({})
        self.assertAlmostEqual(pilot_meter.value() - pilot_before, 30 * pilot_step / 60000, places=3)
        self.assertEqual(mech_meter.value(), mech_before)

    def test_controlled_fusion_scores_body_and_critical_pellets_in_one_volley(self):
        from ow174.game.combat import CRITICAL, HEAD_BELOW, HIT
        from ow174.game.combat import aim as shot_aim
        from ow174.game.commands import FIRE

        run, pilot, enemy, _, mech = self.scenario()
        fight = run.match.combat
        x, y, z = mech.position
        enemy.mover.position[:] = (x, y, z + 2)
        enemy.mover.body = None
        for _ in range(100):
            run.step({})
        target = fight.player_target(enemy)
        health = fight.vitals_of(target)
        before = health.total
        point = (x, target.feet[1] + target.eye - HEAD_BELOW, z + 2 - target.radius)
        yaw, pitch = aim(fight._eye(mech), point)
        with patch.object(fight, "_hitscan", wraps=fight._hitscan) as trace:
            run.step({pilot: (yaw, pitch, FIRE)})
        self.assertEqual(trace.call_count, 11)
        self.assertEqual({call.args[1] for call in trace.call_args_list}, {fight._eye(mech)})
        self.assertGreater(math.dist(trace.call_args_list[0].args[2], shot_aim(yaw, pitch)), 1e-5)
        weapon = next(i for i in mech.body_script.component.instances.values() if i.graph.index == 0x050A)
        volley = weapon.state(3)
        launch = volley.node.field("m_5EC1EB38")
        self.assertEqual(launch["$"], "STU_613BDDB0")
        self.assertEqual(volley.evaluate(launch["m_spread"], watch=False), 4)
        self.assertIsNone(volley.spread_ramp)
        self.assertEqual((volley.index, volley.node.pos), (3, 6))
        seed = fusion_spread.seed_base(mech.entity, weapon.id, 3, volley.counter)
        expected = fusion_spread.directions(
            weapon_aim.quantized_direction(shot_aim(yaw, pitch)),
            4, .7, 0, ((0, 1), (.5, 4), (1, 6)), seed, 0,
        )
        self.assertEqual(tuple(call.args[2] for call in trace.call_args_list),
                         tuple(weapon_aim.normalize_direction(ray) for ray in expected))
        hits = [message["+0x78"] for message in pilot.client.messages(HIT)]
        self.assertEqual(len(hits), 11)
        self.assertEqual({(hit["+0x4"], hit["+0x8"]) for hit in hits}, {(2, 0), (4, CRITICAL)})
        self.assertEqual(before - health.total, sum(hit["+0x4"] for hit in hits))

    def test_controlled_fusion_selects_both_authored_muzzles_on_volley_reports(self):
        run, pilot, _, _, mech = self.scenario()
        for _ in range(100):
            run.step({})
        muzzle_ids = []
        report = nodes.WeaponVolley.report

        def record(state, until):
            previous = state.reported
            report(state, until)
            if (state.reported > previous and state.instance.graph.index == 0x050A
                    and state.instance.component is mech.body_script.component):
                launch = state.node.field("m_5EC1EB38")
                muzzle, = launch["m_muzzles"]
                self.assertIsNone(muzzle["m_entityHandle"])
                hardpoint = state.evaluate(muzzle["m_hardPoint"], watch=False)
                self.assertIsInstance(hardpoint, expr.Asset)
                muzzle_ids.append(hardpoint.guid)

        with patch.object(nodes.WeaponVolley, "report", record):
            for _ in range(60):
                run.step({pilot: (0, 0, 1)})
                if len(muzzle_ids) == 4:
                    break
        self.assertEqual(muzzle_ids, [0x0DC0000000000005, 0x0DC0000000000007] * 2)

    def test_controlled_fusion_walk_penalty_yields_to_boosters_and_releases(self):
        from ow174.game.combat import TICK
        from ow174.game.commands import Command

        run, pilot, enemy, _, mech = self.scenario()
        enemy.spawned = False
        fight = run.match.combat
        component = mech.body_script.component
        weapon = next(i for i in component.instances.values() if i.graph.index == 0x50A)
        matrix = next(i for i in component.instances.values() if i.graph.index == 0x2CC)
        boosters = next(i for i in component.instances.values() if i.graph.index == 0x47E)
        slot = component.entity_var(23184)

        def walk(frames, buttons):
            with patch.object(fight, "_shoot", wraps=fight._shoot) as shoot:
                for _ in range(frames):
                    run.frame += 1
                    run.match.update(run.frame * TICK, run.frame)
                    pilot.take_commands([Command(run.frame, forward=127, buttons=buttons)])
            return sum(call.args[2].instance is weapon for call in shoot.call_args_list)

        self.assertEqual(walk(80, 0), 0)
        self.assertAlmostEqual(mech.velocity[2], 5.5, delta=.02)
        self.assertGreater(walk(40, 1), 0)
        self.assertAlmostEqual(slot.value(), .4)
        self.assertAlmostEqual(mech.velocity[2], 3.3, delta=.02)
        self.assertEqual(walk(20, 3), 0)
        self.assertTrue(matrix.state(3).active)
        self.assertAlmostEqual(slot.value(), .4)
        self.assertAlmostEqual(mech.velocity[2], 3.3, delta=.02)
        self.assertEqual(walk(25, 2), 0)
        self.assertIsNone(slot.value())
        self.assertAlmostEqual(mech.velocity[2], 5.5, delta=.02)
        self.assertGreater(walk(35, 1), 0)
        self.assertGreater(walk(30, 9), 0)
        self.assertTrue(boosters.state(2).active)
        self.assertIsNone(slot.value())
        self.assertAlmostEqual(mech.velocity[2], 11.99, delta=.02)
        walk(1, 1)
        walk(1, 9)
        self.assertGreater(walk(40, 1), 0)
        self.assertFalse(boosters.state(2).active)
        self.assertAlmostEqual(slot.value(), .4)
        self.assertAlmostEqual(mech.velocity[2], 3.3, delta=.02)
        self.assertEqual(walk(30, 0), 0)
        self.assertIsNone(slot.value())
        self.assertAlmostEqual(mech.velocity[2], 5.5, delta=.02)

    def test_light_gun_does_not_apply_the_mechs_walk_penalty(self):
        from ow174.game.combat import TICK
        from ow174.game.commands import Command

        run, pilot, enemy, _, _ = self.scenario()
        enemy.spawned = False
        dva_pilot_input(pilot)
        fight = run.match.combat
        pilot.mover.position[0] += 10
        pilot.mover.body = None

        def walk(frames, buttons):
            with patch.object(fight, "_shoot", wraps=fight._shoot) as shoot:
                for _ in range(frames):
                    run.frame += 1
                    run.match.update(run.frame * TICK, run.frame)
                    pilot.take_commands([Command(run.frame, forward=127, buttons=buttons)])
            return sum(call.args[2].instance.graph.index == 0x2B8 for call in shoot.call_args_list)

        self.assertEqual(walk(100, 0), 0)
        normal_speed = pilot.mover.velocity[2]
        self.assertGreater(normal_speed, 0)
        self.assertGreater(walk(40, 1), 0)
        self.assertAlmostEqual(pilot.mover.velocity[2], normal_speed, delta=.02)
        self.assertIsNone(pilot.body_script.component.entity_var(23184).value())

    def test_controlled_fusion_uses_the_pilots_view_delay(self):
        from ow174.game.commands import FIRE, Command

        for delay, subframe, expected_damage in ((0, 255, 0), (8, 255, 0), (16, 255, 22), (0, 0, 22)):
            with self.subTest(delay_ms=delay, subframe=subframe):
                run, pilot, enemy, _, mech = self.scenario()
                fight = run.match.combat
                for _ in range(100):
                    run.step({})
                x, y, z = mech.position
                old_feet = x, y, z + 2
                enemy.mover.position[:] = old_feet
                enemy.mover.body = None
                run.step({})
                enemy.mover.position[:] = (x + 6, y, z + 2)
                enemy.mover.body = None
                run.frame += 1
                run.match.update(run.frame * .016, run.frame)
                yaw, pitch = aim(fight._eye(mech), (x, y + 1.0, z + 2))
                target = fight.player_target(enemy)
                health = fight.vitals_of(target)
                before = health.total
                pilot.take_commands([
                    Command(run.frame, yaw=yaw, pitch=pitch, buttons=FIRE, extra=delay, subframe=subframe),
                ])
                self.assertEqual(before - health.total, expected_damage)

    def test_controlled_fusion_rewind_excludes_unborn_replacement_body(self):
        from ow174.game.commands import FIRE, Command

        for delay, expected_damage in ((17, 0), (16, 22)):
            with self.subTest(delay_ms=delay):
                run, pilot, enemy, _, mech = self.scenario()
                match, fight = run.match, run.match.combat
                for _ in range(100):
                    run.step({})
                x, y, z = mech.position
                enemy.spawn = ((x, y, z + 2), enemy.spawn[1])
                old_body = enemy.body
                self.assertTrue(match.switch_hero(enemy, enemy.hero))
                self.assertNotEqual(enemy.body, old_body)
                run.frame += 1
                match.update(run.frame * .016, run.frame)
                target = fight.player_target(enemy)
                health = fight.vitals_of(target)
                yaw, pitch = aim(fight._eye(mech), (x, y + 1.0, z + 2))
                before = health.total
                pilot.take_commands([Command(run.frame, yaw=yaw, pitch=pitch, buttons=FIRE, extra=delay)])
                self.assertEqual(before - health.total, expected_damage)

    def test_controlled_fusion_uses_authored_falloff_and_range(self):
        from ow174.game.commands import FIRE

        for distance in (6, 15, 25, 101):
            with self.subTest(distance=distance):
                run, pilot, enemy, _, mech = self.scenario()
                fight = run.match.combat
                for _ in range(100):
                    run.step({})
                x, y, z = mech.position
                enemy.mover.position[:] = (x, y, z + distance)
                enemy.mover.body = None
                run.step({})
                target = fight.player_target(enemy)
                yaw, pitch = aim(fight._eye(mech), (x, y + 1.1, z + distance))
                health = fight.vitals_of(target)
                before = health.total
                with patch.object(fight, "_damage", wraps=fight._damage) as damage:
                    run.step({pilot: (yaw, pitch, FIRE)})
                hits = [
                    call.args[2] for call in damage.call_args_list if call.args[1].entity == target.entity
                ]
                if distance > 100:
                    self.assertFalse(hits)
                    self.assertEqual(health.total, before)
                    continue
                self.assertTrue(hits)
                centre_contact = distance - target.radius
                centre_amount = 2 if distance < 10 else max(.6, 2 - .14 * (centre_contact - 10))
                self.assertAlmostEqual(hits[0], centre_amount, delta=.01)
                self.assertAlmostEqual(before - health.total, sum(hits), places=4)

    def test_controlled_mech_earns_and_spends_its_own_self_destruct_charge(self):
        from ow174.game.combat import Vitals

        run, pilot, enemy, _, mech = self.scenario()
        fight = run.match.combat
        x, y, z = mech.position
        enemy.mover.position[:] = (x, y, z + 2)
        enemy.mover.body = None
        pilot.body_script.ult_seconds = 0
        pilot_charge = pilot.body_script.component.entity_var(ULT_CHARGE)
        component = mech.body_script.component
        charge = component.entity_var(ULT_CHARGE)
        cost = component.entity_var(ULT_COST).value()
        self.assertEqual(cost, 1540)
        enemy_health = Vitals(enemy.body, 5000)
        fight.vitals[enemy.body] = enemy_health
        fight._sync_body_health(enemy, enemy_health)
        yaw, pitch = aim((x, y + 1.74, z), (x, y + 1.1, z + 2))
        with patch.object(body_entities, "ULT_SECONDS", 0):
            for _ in range(100):
                run.step({})
            starting_pilot_charge = pilot_charge.value()
            for _ in range(1000):
                run.step({pilot: (yaw, pitch, 1)})
                self.assertEqual(charge.value(), min(cost, 5000 - enemy_health.total))
                if charge.value() == cost:
                    break
            self.assertEqual(charge.value(), cost)
            self.assertEqual(pilot_charge.value(), starting_pilot_charge)
            run.step({pilot: (yaw, pitch, 32)})
            self.assertEqual(charge.value(), 0)
            run.step({})
            self.assertTrue(mech.detached_bomb)
            self.assertIsNone(pilot.controlled_mech)
            before_blast = enemy_health.total
            self.assertEqual(pilot_charge.value(), starting_pilot_charge)
            for _ in range(220):
                run.step({})
                if mech not in run.match.bots:
                    break
            self.assertNotIn(mech, run.match.bots)
            self.assertEqual(before_blast - enemy_health.total, 1000)
            pilot_cost = pilot.body_script.component.entity_var(ULT_COST).value()
            self.assertEqual(pilot_charge.value(), pilot_cost)

    def test_detached_fuse_unlocks_call_mech_at_detonation(self):
        for hold_through_refill in (False, True):
            with self.subTest(hold_through_refill=hold_through_refill):
                self.check_fuse_unlock(hold_through_refill)

    def check_fuse_unlock(self, hold_through_refill):
        run, pilot, enemy, _, mech = self.scenario()
        enemy.spawned = False
        for _ in range(100):
            run.step({})
        match = run.match
        pilot_component = pilot.body_script.component
        charge = pilot_component.entity_var(ULT_CHARGE)
        cost = pilot_component.entity_var(ULT_COST).value()
        component = mech.body_script.component
        component.set(ULT_CHARGE, component.entity_var(ULT_COST).value())
        with patch.object(match, "queue_body_message", wraps=match.queue_body_message) as queued:
            run.step({pilot: (0, 0, 32)})
            cast_frame = run.frame
            run.step({})
            self.assertTrue(mech.detached_bomb)
            self.assertIsNone(pilot.controlled_mech)
            while run.frame < cast_frame + 186:
                pressed = run.frame == cast_frame + 60 or (
                    hold_through_refill and run.frame > cast_frame + 60
                )
                run.step({pilot: (0, 0, 32)} if pressed else {})
                self.assertEqual(charge.value(), 0)
                self.assertIsNone(pilot.controlled_mech)
                self.assertEqual(match.bots, [mech])
            run.step({pilot: (0, 0, 32)} if hold_through_refill else {})
            self.assertIn(mech, match.bots)
            self.assertEqual(charge.value(), 0 if hold_through_refill else cost)
            run.step({pilot: (0, 0, 32)})
            self.assertEqual(charge.value(), 0)
            for _ in range(120):
                run.step({})
                if pilot.controlled_mech is not None:
                    break
            self.assertIsNotNone(pilot.controlled_mech)
            self.assertIsNot(pilot.controlled_mech, mech)
            self.assertNotIn(mech, match.bots)
            refills = [call for call in queued.call_args_list
                       if call.args[:2] == (pilot.body, 0x0240000000000592)]
            self.assertEqual(len(refills), 1)

    def test_detached_fuse_detonates_and_refills_once_across_world_tick_gap(self):
        from ow174.game.combat import COMBAT_CATCH_UP, TICK, Vitals

        for resume_offset in (190, 300):
            with self.subTest(resume_offset=resume_offset):
                run, pilot, enemy, _, mech = self.scenario()
                match = run.match
                x, y, z = mech.position
                enemy.mover.position[:] = (x, y, z + 2)
                enemy.mover.body = None
                for _ in range(100):
                    run.step({})
                health = Vitals(enemy.body, 5000)
                match.combat.vitals[enemy.body] = health
                match.combat._sync_body_health(enemy, health)
                pilot.body_script.ult_seconds = 0
                pilot_component = pilot.body_script.component
                pilot_charge = pilot_component.entity_var(ULT_CHARGE)
                cost = pilot_component.entity_var(ULT_COST).value()
                component = mech.body_script.component
                component.set(ULT_CHARGE, component.entity_var(ULT_COST).value())
                with patch.object(match, "queue_body_message", wraps=match.queue_body_message) as queued:
                    run.step({pilot: (0, 0, 32)})
                    cast_frame = run.frame
                    run.step({})
                    self.assertTrue(mech.detached_bomb)
                    run.frame = cast_frame + 186
                    match.update(run.frame * TICK, run.frame, frames=False)
                    self.assertIn(mech, match.bots)
                    self.assertEqual(health.total, 5000)
                    self.assertEqual(pilot_charge.value(), 0)
                    run.frame = cast_frame + resume_offset
                    for tick in (run.frame, run.frame, run.frame + 30):
                        match.update(tick * TICK, tick, frames=False)
                    self.assertEqual(health.total, 4000)
                    self.assertEqual(pilot_charge.value(), cost)
                    self.assertNotIn(mech, match.bots)
                    hit, = pilot.client.messages(25001)
                    first_retained = resume_offset - COMBAT_CATCH_UP + 1
                    self.assertEqual(hit["+0x84"], cast_frame + max(187, first_retained))
                    refill = pilot.body, 0x0240000000000592
                    refills = [call for call in queued.call_args_list if call.args[:2] == refill]
                    self.assertEqual(len(refills), 1)

    def test_detached_fuse_does_not_refill_after_source_body_retires(self):
        from ow174.game.combat import TICK, Vitals

        for operation in ("leave", "switch", "queued_refill"):
            with self.subTest(operation=operation):
                run, pilot, enemy, _, mech = self.scenario()
                match = run.match
                x, y, z = mech.position
                enemy.mover.position[:] = (x, y, z + 2)
                enemy.mover.body = None
                for _ in range(100):
                    run.step({})
                health = Vitals(enemy.body, 5000)
                match.combat.vitals[enemy.body] = health
                old_script = pilot.body_script
                old_body = pilot.body
                old_script.ult_seconds = 0
                old_charge = old_script.component.entity_var(ULT_CHARGE)
                component = mech.body_script.component
                component.set(ULT_CHARGE, component.entity_var(ULT_COST).value())
                run.step({pilot: (0, 0, 32)})
                cast = run.frame
                run.step({})
                self.assertTrue(mech.detached_bomb)
                self.assertEqual(old_charge.value(), 0)
                if operation == "queued_refill":
                    # Delay delivery of the real graph message until the recipient retires.
                    with patch.object(match, "deliver_body_messages"):
                        tick = cast + 187
                        match.update(tick * TICK, tick, frames=False)
                    self.assertTrue(any(entry[2] is old_script and entry[3] == 0x0240000000000592
                                        for entry in match._body_messages))
                if operation == "leave":
                    match.leave(pilot)
                else:
                    hero = find_hero("dva" if operation == "queued_refill" else "soldier76")
                    self.assertTrue(match.switch_hero(pilot, hero))
                    self.assertNotEqual(pilot.body, old_body)
                    new_charge = pilot.body_script.component.entity_var(ULT_CHARGE)
                    initial = new_charge.value()
                self.assertNotIn(mech, match.bots)
                tick = cast + 300
                match.update(tick * TICK, tick, frames=False)
                match.update(tick * TICK, tick, frames=False)
                self.assertEqual(health.total, 4000 if operation == "queued_refill" else 5000)
                self.assertEqual(old_charge.value(), 0)
                self.assertFalse(match._body_messages)
                if operation != "leave":
                    self.assertEqual(new_charge.value(), initial)

    def test_lethal_damage_credits_only_remaining_health_in_either_form(self):
        from ow174.game.combat import KILLING, Vitals

        self.enterContext(patch.object(body_entities, "ULT_SECONDS", 0))
        for pilot_form in (False, True):
            with self.subTest(pilot_form=pilot_form):
                run, pilot, enemy, _, mech = self.scenario()
                pilot.body_script.ult_seconds = 0
                if pilot_form:
                    dva_pilot_input(pilot)
                    pilot.mover.position[0] += 10
                    pilot.mover.body = None
                fight = run.match.combat
                actor = fight.player_target(pilot).owner
                self.assertIs(actor, pilot if pilot_form else mech)
                x, y, z = fight.player_target(pilot).feet
                enemy.mover.position[:] = (x, y, z + 2)
                enemy.mover.body = None
                for _ in range(100):
                    run.step({})
                health = Vitals(enemy.body, 1)
                fight.vitals[enemy.body] = health
                fight._sync_body_health(enemy, health)
                charge = actor.body_script.component.entity_var(ULT_CHARGE)
                initial_charge = charge.value() or 0
                x, y, z = fight.player_target(enemy).feet
                yaw, pitch = aim(fight._eye(actor), (x, y + 1.1, z))
                for _ in range(40):
                    run.step({pilot: (yaw, pitch, 1)})
                self.assertTrue(health.dead)
                self.assertEqual(health.killer, pilot.body if pilot_form else mech.entity)
                self.assertEqual(charge.value(), initial_charge + 1)
                hit, = pilot.client.messages(25001)
                self.assertEqual(hit["+0x78"]["+0x4"], 1)
                self.assertTrue(hit["+0x78"]["+0x8"] & KILLING)

    def test_controlled_mech_recovered_poses_match_continuous_input(self):
        from ow174.game.combat import Vitals
        from ow174.game.commands import Command

        def simulate(buttons, recovered):
            run, pilot, other, _, mech = self.scenario()
            match = run.match
            x, y, z = mech.position
            other.mover.position = [x, y, z + 4]
            other.mover.body = None
            for _ in range(100):
                run.step({})
            health = Vitals(other.body, 5000)
            match.combat.vitals[other.body] = health
            start = run.frame
            aim_pitch = aim((x, y + 1.74, z), (x, y + 1.2, z + 4))[1] if buttons == 1 else 0
            initial = Command(start + 1, buttons=buttons, pitch=aim_pitch)
            final = Command(start + 26, pitch=aim_pitch)
            pilot.take_commands([initial])
            if not recovered:
                for frame in range(start + 2, start + 26):
                    pilot.take_commands([Command(frame, buttons=buttons, pitch=aim_pitch)])
            pilot.take_commands([final])
            component = mech.body_script.component
            matrix = next(i for i in component.instances.values() if i.graph.index == 0x2CC)
            return (
                mech.position, mech.velocity, health.total, len(pilot.client.messages(25001)),
                matrix.vars[87].value(), component.entity_var(ULT_CHARGE).value(), tuple(component.held),
                len(match.combat.rockets),
            )

        for buttons in (1, 2, 8, 16):
            with self.subTest(buttons=buttons):
                continuous = simulate(buttons, False)
                self.assertEqual(simulate(buttons, True), continuous)
                if buttons in (1, 8):
                    self.assertLess(continuous[2], 5000)
                elif buttons == 2:
                    self.assertLess(continuous[4], 100)
                else:
                    self.assertGreater(continuous[-1], 0)

    def test_controlled_mech_long_gap_releases_input_without_replaying_unposed_damage(self):
        from ow174.game.combat import Vitals
        from ow174.game.commands import Command

        run, pilot, other, _, mech = self.scenario()
        match = run.match
        x, y, z = mech.position
        other.mover.position = [x, y, z + 4]
        other.mover.body = None
        for _ in range(100):
            run.step({})
        health = Vitals(other.body, 5000)
        match.combat.vitals[other.body] = health
        pitch = aim((x, y + 1.74, z), (x, y + 1.2, z + 4))[1]
        pilot.take_commands([Command(run.frame + 1, buttons=1, pitch=pitch)])
        self.assertLess(health.total, 5000)
        before = health.total
        pilot.take_commands([Command(run.frame + 100, pitch=pitch)])
        self.assertEqual(health.total, before)
        self.assertFalse(mech.body_script.component.held)
        self.assertEqual(mech.body_script.frame, run.frame + 100)
        self.assertEqual(pilot.body_script.frame, run.frame + 100)

    def test_long_command_gap_counts_retained_passive_ticks_for_both_body_clocks(self):
        from ow174.game.commands import Command
        from ow174.game.script.driver import MAX_GAP, ULT_SECONDS

        for pilot_form in (False, True):
            for gap, retained in ((99, 99), (350, MAX_GAP + 3)):
                with self.subTest(pilot_form=pilot_form, gap=gap):
                    run, pilot, other, _, mech = self.scenario()
                    other.spawned = False
                    if pilot_form:
                        dva_pilot_input(pilot)
                    for _ in range(30):
                        run.step({})
                    bodies = (pilot.body_script,) if pilot_form else (pilot.body_script, mech.body_script)
                    expected = []
                    for script in bodies:
                        component = script.component
                        cost = component.entity_var(ULT_COST).value()
                        step = expr.f32(cost * component.frame_ms / (1000 * ULT_SECONDS))
                        charge = component.entity_var(ULT_CHARGE).value()
                        for _ in range(retained):
                            charge = expr.f32(charge + step)
                        expected.append(charge)
                    command = Command(run.frame + gap)
                    for _ in range(2):
                        pilot.take_commands([command])
                        self.assertEqual([script.frame for script in bodies], [command.frame] * len(bodies))
                        self.assertEqual([script.component.entity_var(ULT_CHARGE).value()
                                          for script in bodies], expected)
                    self.assertFalse(run.match.combat.rockets)

    def test_long_mech_command_gap_keeps_launched_missile_and_rejects_unposed_volleys(self):
        from ow174.game.combat import TICK, Vitals
        from ow174.game.commands import Command

        run, pilot, enemy, _, mech = self.scenario()
        match, fight = run.match, run.match.combat
        x, y, z = mech.position
        enemy.mover.position[:] = (x, y, z + 12)
        enemy.mover.body = None
        for _ in range(35):
            run.step({})
        health = fight.vitals[enemy.body] = Vitals(enemy.body, 1000)
        angles = aim(fight._eye(mech), (x, y + 1, z + 12))
        for _ in range(45):
            run.step({pilot: (*angles, 16)})
            if fight.rockets:
                break
        rocket, = fight.rockets
        self.assertIs(rocket.shooter, mech)
        script = mech.body_script
        missiles = next(i for i in script.component.instances.values() if i.graph.index == 0x1128)
        before = health.total
        frame = run.frame + 99
        with patch.object(pilot.client, "queue_bot_live", wraps=pilot.client.queue_bot_live) as queued:
            pilot.take_commands([Command(frame, yaw=angles[0], pitch=angles[1])])
        published = [call.args[1].k for call in queued.call_args_list if call.args[0] == mech.entity]
        self.assertTrue(published)
        self.assertTrue(set(published) <= {run.frame + 1, frame - 1, frame})
        self.assertEqual(fight.rockets, [rocket])
        self.assertEqual(health.total, before)
        self.assertFalse(script.component.held)
        ability = missiles.state(0)
        self.assertGreater(ability.remaining(), 0)
        volley = missiles.state(5).counter
        pilot.take_commands([Command(frame + 1, yaw=angles[0], pitch=angles[1], buttons=16)])
        self.assertEqual(missiles.state(5).counter, volley)
        self.assertEqual(fight.rockets, [rocket])
        for _ in range(40):
            run.frame += 1
            match.update(run.frame * TICK, run.frame)
            if rocket not in fight.rockets:
                break
        self.assertFalse(fight.rockets)
        self.assertAlmostEqual(before - health.total, 7, places=6)
        self.assertEqual(missiles.state(5).counter, volley)
        ready_frame = script.frame + math.ceil(ability.remaining() / TICK) + 1
        pilot.take_commands([Command(ready_frame, yaw=angles[0], pitch=angles[1])])
        self.assertEqual(ability.remaining(), 0)
        self.assertFalse(fight.rockets)
        for command_frame in range(ready_frame + 1, ready_frame + 40):
            pilot.take_commands([Command(command_frame, yaw=angles[0], pitch=angles[1], buttons=16)])
            if fight.rockets:
                break
        fresh, = fight.rockets
        self.assertIs(fresh.shooter, mech)
        self.assertEqual(missiles.state(5).counter, volley + 1)

    def test_both_forms_quick_melee_use_command_view_time_and_click_aim(self):
        from ow174.game.combat import HIT, TICK
        from ow174.game.commands import Command

        for controlled in (False, True):
            for delay, subframe, expected in ((0, 255, 0), (16, 255, 30), (0, 0, 30)):
                with self.subTest(controlled=controlled, delay_ms=delay, subframe=subframe):
                    run, pilot, enemy, _, mech = self.scenario()
                    if not controlled:
                        dva_pilot_input(pilot)
                    actor = mech if controlled else pilot
                    fight = run.match.combat
                    for _ in range(100):
                        run.step({})
                    x, y, z = actor.position if controlled else actor.mover.position
                    enemy.mover.position[:] = (x, y, z + 1.5)
                    enemy.mover.body = None
                    run.step({})
                    enemy.mover.position[:] = (x + 6, y, z + 1.5)
                    enemy.mover.body = None
                    run.frame += 1
                    run.match.update(run.frame * TICK, run.frame)
                    target = fight.player_target(enemy)
                    health = fight.vitals_of(target)
                    yaw, pitch = aim(fight._eye(actor), (x, y + 1.2, z + 1.5))
                    command = Command(
                        run.frame, yaw=16384, buttons=0x800, extra=delay, subframe=subframe,
                        click_yaw=yaw, click_pitch=pitch,
                    )
                    before = health.total
                    pilot.take_commands([command, command])
                    self.assertEqual(before - health.total, expected)
                    hits = pilot.client.messages(HIT)
                    self.assertEqual([(hit["+0x78"]["+0x4"], hit["+0x84"]) for hit in hits],
                                     [(30, command.frame)] if expected else [])

    def test_actual_flashbang_ends_quick_melee_and_fresh_input_recovers_both_forms(self):
        from ow174.game.combat import HIT
        from ow174.game.content import heroes

        for controlled in (False, True):
            with self.subTest(controlled=controlled):
                run, pilot, enemy, _, mech = self.scenario()
                match, fight = run.match, run.match.combat
                if not controlled:
                    dva_pilot_input(pilot)
                actor = mech if controlled else pilot
                self.assertTrue(match.switch_hero(enemy, heroes()[0x02E0000000000042]))
                for _ in range(100):
                    run.step({})
                x, y, z = actor.position if controlled else actor.mover.position
                enemy.mover.position[:] = (x, y, z + 1.5)
                enemy.mover.body = None
                run.step({})
                health = fight.vitals_of(fight.player_target(enemy))
                before = health.total
                yaw, pitch = aim(fight._eye(actor), (x, y + 1.0, z + 1.5))
                flash_yaw, flash_pitch = aim(fight._eye(enemy), (x, y + 1.0, z))
                run.step({pilot: (yaw, pitch, 0x800), enemy: (flash_yaw, flash_pitch, 16)})
                self.assertEqual(before - health.total, 30)
                script = actor.body_script
                melee = next(i for i in script.component.instances.values() if i.graph.index == 0x43)
                ability = melee.state(6)
                self.assertTrue(ability.flags & nodes.Ability.RUNNING)
                for _ in range(12):
                    run.step({pilot: (yaw, pitch, 0x800)})
                    if fight.is_stunned(actor):
                        break
                self.assertTrue(fight.is_stunned(actor))
                self.assertFalse(ability.flags & nodes.Ability.RUNNING)
                for _ in range(100):
                    if not fight.is_stunned(actor) and ability.remaining() <= 0:
                        break
                    run.step({})
                self.assertFalse(fight.is_stunned(actor))
                self.assertEqual(ability.remaining(), 0)
                self.assertEqual(before - health.total, 30)
                run.step({pilot: (yaw, pitch, 0x800)})
                self.assertEqual(before - health.total, 60)
                self.assertEqual(len(pilot.client.messages(HIT)), 2)

    def test_initial_controlled_mech_uses_its_pose_for_melee_and_booster_contact(self):
        from ow174.game.combat import Vitals

        for buttons, distance, damage in ((0x800, 1.5, 30), (8, 4.5, 10)):
            with self.subTest(buttons=buttons):
                run, pilot, other, _, mech = self.scenario()
                fight = run.match.combat
                x, y, z = mech.position
                other.mover.position = [x, y, z + distance]
                other.mover.body = None
                for _ in range(100):
                    run.step({})
                vitals = Vitals(other.body, 1000)
                fight.vitals[other.body] = vitals
                yaw, pitch = aim(fight._eye(mech), (x, y + 1, z + distance))
                if buttons == 8:
                    pitch = 0
                run.step({pilot: (yaw, pitch, buttons)})
                for _ in range(35):
                    run.step({pilot: (yaw, pitch, 0)})
                self.assertEqual(vitals.total, 1000 - damage)
                if buttons == 8:
                    self.assertGreater(mech.position[2], z + 3)
                    self.assertGreater(other.mover.position[2], z + distance + 1)

    def test_controlled_micro_missiles_quantize_the_launch_direction(self):
        from ow174.game.combat import aim as shot_aim

        run, pilot, _, _, mech = self.scenario()
        for _ in range(100):
            run.step({})
        yaw, pitch = 16384, 16250
        fight = run.match.combat
        for _ in range(40):
            run.step({pilot: (yaw, pitch, 16)})
            if fight.rockets:
                break
        rocket, = fight.rockets
        self.assertIs(rocket.shooter, mech)
        self.assertEqual(rocket.definition, 0x0400000000001E8A)
        raw = shot_aim(yaw, pitch)
        expected = weapon_aim.normalize_direction(weapon_aim.quantized_direction(raw))
        self.assertEqual(rocket.direction, expected)
        self.assertGreater(math.dist(rocket.direction, raw), .01)

    def test_initial_controlled_mech_launches_all_authored_micro_missiles(self):
        from ow174.game.combat import Vitals

        run, pilot, other, _, mech = self.scenario()
        fight = run.match.combat
        x, y, z = mech.position
        other.mover.position = [x, y, z + 2]
        other.mover.body = None
        for _ in range(100):
            run.step({})
        vitals = Vitals(other.body, 1000)
        fight.vitals[other.body] = vitals
        yaw, pitch = aim(fight._eye(mech), (x, y + 1.1, z + 2))
        with patch.object(fight, "_shoot", wraps=fight._shoot) as shoot:
            for _ in range(160):
                run.step({pilot: (yaw, pitch, 16)})
        reports = [call.args for call in shoot.call_args_list if call.args[0] is mech]
        self.assertEqual([args[3] for args in reports], list(range(18)))
        state, = {args[2] for args in reports}
        self.assertEqual((state.instance.graph.index, state.node.pos), (0x1128, 10))
        launch = state.node.field("m_5EC1EB38")
        muzzles = launch["m_muzzles"]
        self.assertTrue(all(muzzle["m_entityHandle"] is None for muzzle in muzzles))
        self.assertEqual([(muzzle["m_8457D578"], state.evaluate(muzzle["m_hardPoint"], watch=False))
                          for muzzle in muzzles],
                         [(0, expr.Asset(0x0DC0000000000075)), (1, expr.Asset(0x0DC0000000000077))])
        self.assertEqual(vitals.total, 874)

        missiles = next(i for i in mech.body_script.component.instances.values() if i.graph.index == 0x1128)
        ability = missiles.state(0)
        self.assertIsNone(ability.node.field("m_246438AD"))

        def near_expiry():
            run.step({})
            for _ in range(700):
                if ability.remaining() <= 0.08:
                    break
                run.step({})
            self.assertGreater(ability.remaining(), 0.032)
            self.assertLessEqual(ability.remaining(), 0.08)

        near_expiry()
        run.step({pilot: (yaw, pitch, 16)})
        self.assertFalse(ability.flags & nodes.Ability.RUNNING)
        run.step({})
        # A release received before the gap cannot be replayed as a held input.
        run.frame += 12
        run.step({})
        self.assertEqual(ability.remaining(), 0)
        self.assertFalse(ability.flags & nodes.Ability.RUNNING)
        for _ in range(155):
            run.step({})
        self.assertEqual(vitals.total, 874)
        self.assertEqual(len(pilot.client.messages(25001)), 36)
        run.step({pilot: (yaw, pitch, 16)})
        self.assertTrue(ability.flags & nodes.Ability.RUNNING)
        for _ in range(160):
            run.step({})
        self.assertEqual(vitals.total, 748)
        self.assertEqual(len(pilot.client.messages(25001)), 72)

        near_expiry()
        run.step({pilot: (yaw, pitch, 16)})
        self.assertFalse(ability.flags & nodes.Ability.RUNNING)
        # With no release received, recovery repeats the last known held input.
        run.frame += 12
        run.step({})
        self.assertTrue(ability.flags & nodes.Ability.RUNNING)
        for _ in range(160):
            run.step({})
        self.assertEqual(vitals.total, 622)
        self.assertEqual(len(pilot.client.messages(25001)), 108)

    def test_micro_missile_launch_boundary_at_demech(self):
        self.enterContext(patch.object(body_entities, "ULT_SECONDS", 0))
        for after_launch in (False, True):
            with self.subTest(after_launch=after_launch):
                self.check_micro_missile_launch_boundary(after_launch, self_destruct=False)

    def test_micro_missile_launch_boundary_at_self_destruct(self):
        self.enterContext(patch.object(body_entities, "ULT_SECONDS", 0))
        for after_launch in (False, True):
            with self.subTest(after_launch=after_launch):
                self.check_micro_missile_launch_boundary(after_launch, self_destruct=True)

    def check_micro_missile_launch_boundary(self, after_launch, *, self_destruct):
        from ow174.game.combat import Vitals

        run, pilot, enemy, _, mech = self.scenario()
        fight = run.match.combat
        pilot.body_script.ult_seconds = 0
        pilot_charge = pilot.body_script.component.entity_var(ULT_CHARGE)
        x, y, z = mech.position
        enemy.mover.position[:] = (x, y, z + 12)
        enemy.mover.body = None
        for _ in range(100):
            run.step({})
        health = fight.vitals[enemy.body] = Vitals(enemy.body, 1000)
        yaw, pitch = aim(fight._eye(mech), (x, y + 1.1, z + 12))
        run.step({pilot: (yaw, pitch, 16)})
        ability = next(i.state(0) for i in mech.body_script.component.instances.values()
                       if i.graph.index == 0x1128)
        self.assertTrue(ability.flags & nodes.Ability.RUNNING)
        self.assertFalse(fight.rockets)
        if after_launch:
            for _ in range(45):
                if fight.rockets:
                    break
                run.step({})
            missile, = fight.rockets
            self.assertIs(missile.shooter, mech)
        with patch.object(fight, "_shoot", wraps=fight._shoot) as shoot:
            if self_destruct:
                component = mech.body_script.component
                component.set(ULT_CHARGE, component.entity_var(ULT_COST).value())
                run.step({pilot: (yaw, pitch, 32)})
            else:
                mech_health = fight.vitals[mech.entity] = Vitals(mech.entity, 5)
                enemy_yaw, enemy_pitch = aim(fight._eye(enemy), (x, y + 1.2, z))
                run.step({enemy: (enemy_yaw, enemy_pitch, 1)})
                self.assertTrue(mech_health.dead)
            if after_launch:
                self.assertIn(missile, fight.rockets)
            else:
                self.assertFalse(fight.rockets)
            run.step({})
            self.assertIsNone(pilot.controlled_mech)
            self.assertEqual(mech.detached_bomb, self_destruct)
            self.assertEqual(health.total, 1000)
            starting_charge = pilot_charge.value()
            mech_charge = mech.body_script.component.entity_var(ULT_CHARGE)
            starting_mech_charge = mech_charge.value()
            for _ in range(140):
                run.step({})
            self.assertFalse(any(call.args[0] is mech for call in shoot.call_args_list))
        self.assertEqual(health.total, 993 if after_launch else 1000)
        self.assertFalse(fight.rockets)
        self.assertEqual(pilot_charge.value(), starting_charge)
        self.assertEqual(mech_charge.value(), starting_mech_charge)
        self.assertEqual(len(pilot.client.messages(25001)), 2 if after_launch else 0)

    def test_micro_missiles_held_through_ejection_only_rearm_on_the_new_mech(self):
        from ow174.game.combat import HIT, Vitals

        run, pilot, enemy, _, original = self.scenario()
        fight = run.match.combat
        pilot_script = pilot.body_script
        pilot_script.ult_seconds = 0
        self.enterContext(patch.object(body_entities, "ULT_SECONDS", 0))
        x, y, z = original.position
        enemy.mover.position[:] = (x, y, z + 25)
        enemy.mover.body = None
        for _ in range(100):
            run.step({})
        health = fight.vitals[enemy.body] = Vitals(enemy.body, 1000)
        source = original.body_script.component
        source.set(ULT_CHARGE, source.entity_var(ULT_COST).value())
        old_volley = next(i for i in source.instances.values() if i.graph.index == 0x1128)
        with patch.object(fight, "_shoot", wraps=fight._shoot) as shoot:
            run.step({pilot: (0, 0, 16)})
            self.assertTrue(old_volley.state(0).flags & nodes.Ability.RUNNING)
            self.assertFalse(fight.rockets)
            run.step({pilot: (0, 0, 32 | 16)})
            run.step({pilot: (0, 0, 16)})
            self.assertIsNone(pilot.controlled_mech)
            self.assertTrue(original.detached_bomb)
            self.assertFalse(old_volley.state(0).flags & nodes.Ability.RUNNING)
            for _ in range(220):
                run.step({pilot: (0, 0, 16)})
            self.assertNotIn(original, run.match.bots)
            self.assertIs(pilot.body_script, pilot_script)
            self.assertIsNone(pilot.controlled_mech)
            pilot_graphs = {instance.graph.index for instance in pilot_script.component.instances.values()}
            self.assertNotIn(0x1128, pilot_graphs)
            self.assertFalse(fight.rockets)
            self.assertFalse(shoot.call_args_list)
            self.assertEqual(health.total, 1000)
            self.assertEqual(pilot.client.messages(HIT), [])
            self.assertEqual(pilot_script.component.entity_var(ULT_CHARGE).value(),
                             pilot_script.component.entity_var(ULT_COST).value())

            run.step({})
            run.step({pilot: (0, 0, 32)})
            for _ in range(120):
                run.step({})
            replacement = pilot.controlled_mech
            self.assertIsNotNone(replacement)
            self.assertIsNot(replacement, original)
            self.assertFalse(shoot.call_args_list)
            new_component = replacement.body_script.component
            new_volley = next(i for i in new_component.instances.values() if i.graph.index == 0x1128)
            self.assertEqual(new_volley.state(0).remaining(), 0)
            x, y, z = replacement.position
            enemy.mover.position[:] = (x, y, z + 3)
            enemy.mover.body = None
            run.step({})
            yaw, pitch = aim(fight._eye(replacement), (x, y + .85, z + 3))
            run.step({pilot: (yaw, pitch, 16)})
            for _ in range(159):
                run.step({pilot: (yaw, pitch, 0)})
            self.assertEqual([(call.args[0], call.args[3]) for call in shoot.call_args_list],
                             [(replacement, index) for index in range(18)])
            self.assertEqual(health.total, 874)
            self.assertEqual(len(pilot.client.messages(HIT)), 36)
            self.assertEqual(new_component.entity_var(ULT_CHARGE).value(), 126)
            self.assertEqual(pilot_script.component.entity_var(ULT_CHARGE).value(), 0)

    def _pilot_mech_button_trace(self, self_destruct, buttons):
        from ow174.game.combat import HIT, Vitals

        run, pilot, enemy, _, mech = self.scenario()
        fight = run.match.combat
        x, y, z = mech.position
        enemy.mover.position[:] = (x, y, z + 4)
        enemy.mover.body = None
        for _ in range(100):
            run.step({})
        if self_destruct:
            enemy.mover.position[:] = (x, y, z + 25)
            component = mech.body_script.component
            component.set(ULT_CHARGE, component.entity_var(ULT_COST).value())
            run.step({pilot: (0, 0, 32)})
        else:
            mech_health = fight.vitals[mech.entity] = Vitals(mech.entity, 5)
            yaw, pitch = aim(fight._eye(enemy), (x, y + 1.2, z))
            run.step({enemy: (yaw, pitch, 1)})
            self.assertTrue(mech_health.dead)
        for _ in range(8):
            if pilot.controlled_mech is None:
                break
            run.step({})
        self.assertIsNone(pilot.controlled_mech)
        self.assertEqual(mech.detached_bomb, self_destruct)
        positions = []
        for _ in range(100):
            run.step({pilot: (0, 0, buttons)})
            positions.append(tuple(pilot.mover.position))
            self.assertFalse(fight.rockets)
        self.assertFalse(pilot.client.messages(HIT))
        x, y, z = pilot.mover.position
        enemy.mover.position[:] = (x, y, z + 4)
        enemy.mover.body = None
        health = fight.vitals[enemy.body] = Vitals(enemy.body, 1000)
        run.step({pilot: (0, 0, buttons)})
        feet = fight.player_target(enemy).feet
        yaw, pitch = aim(fight._eye(pilot), (feet[0], feet[1] + .8, feet[2]))
        run.step({pilot: (yaw, pitch, buttons | 1)})
        for _ in range(30):
            run.step({pilot: (yaw, pitch, buttons)})
        self.assertEqual(health.total, 986)
        self.assertEqual(len(pilot.client.messages(HIT)), 1)
        self.assertFalse(fight.rockets)
        meter = pilot.body_script.component.entity_var(ULT_CHARGE).value()
        return positions, tuple(pilot.mover.position), meter

    def test_simultaneous_mech_buttons_leave_pilot_pose_and_light_gun_unchanged(self):
        for self_destruct in (False, True):
            with self.subTest(self_destruct=self_destruct):
                baseline = self._pilot_mech_button_trace(self_destruct, 0)
                self.assertEqual(self._pilot_mech_button_trace(self_destruct, 2 | 8 | 16), baseline)

    def test_switch_retires_in_flight_micro_missiles_and_light_gun_rounds(self):
        from ow174.game.combat import TICK, Vitals

        self.enterContext(patch.object(body_entities, "ULT_SECONDS", 0))
        for pilot_form, button in ((False, 16), (True, 1)):
            with self.subTest(pilot_form=pilot_form):
                run, pilot, enemy, _, mech = self.scenario()
                match = run.match
                fight = match.combat
                if pilot_form:
                    dva_pilot_input(pilot)
                    pilot.mover.position[0] += 10
                    pilot.mover.body = None
                source = fight.player_target(pilot)
                x, y, z = source.feet
                enemy.mover.position[:] = (x, y, z + 12)
                enemy.mover.body = None
                for _ in range(100):
                    run.step({})
                health = Vitals(enemy.body, 1000)
                fight.vitals[enemy.body] = health
                yaw, pitch = aim(fight._eye(source.owner), (x, y + 1.1, z + 12))
                run.step({pilot: (yaw, pitch, button)})
                for _ in range(45):
                    if any(rocket.shooter is source.owner for rocket in fight.rockets):
                        break
                    run.step({})
                self.assertTrue(any(rocket.shooter is source.owner for rocket in fight.rockets))
                self.assertEqual(health.total, 1000)
                old_body = pilot.body
                self.assertTrue(match.switch_hero(pilot, find_hero("soldier76")))
                self.assertTrue(match.switch_hero(pilot, find_hero("dva")))
                self.assertNotEqual(pilot.body, old_body)
                self.assertIsNot(pilot.controlled_mech, mech)
                self.assertFalse(fight.rockets)
                charge = pilot.controlled_mech.body_script.component.entity_var(ULT_CHARGE)
                initial = charge.value()
                tick = run.frame + 80
                match.update(tick * TICK, tick, frames=False)
                self.assertEqual(health.total, 1000)
                self.assertEqual(charge.value(), initial)
                self.assertFalse(pilot.client.messages(25001))

    def test_initial_mech_ultimate_detaches_returns_pilot_and_blasts_before_retirement(self):
        from ow174.game.combat import Vitals

        run, pilot, other, _, mech = self.scenario()
        match = run.match
        script = mech.body_script
        pilot_script = pilot.body_script
        x, y, z = mech.position
        other.mover.position = [x, y, z + 3]
        other.mover.body = None
        for _ in range(100):
            run.step({})
        vitals = Vitals(other.body, 5000)
        match.combat.vitals[other.body] = vitals
        component = script.component
        cost = component.entity_var(ULT_COST).value()
        self.assertEqual(cost, 1540)
        self.assertGreater(component.entity_var(ULT_CHARGE).value(), 0)
        component.set(ULT_CHARGE, cost)
        run.step({pilot: (0, 0, 32)})
        self.assertEqual(component.entity_var(ULT_CHARGE).value(), 0)
        run.step({})
        self.assertIsNone(pilot.controlled_mech)
        self.assertIs(pilot.body_script, pilot_script)
        self.assertTrue(mech.detached_bomb)
        self.assertNotIn(mech.entity, {target.entity for target in match.combat._targets()})
        for _ in range(185):
            run.step({})
        self.assertEqual(vitals.total, 5000)
        run.step({})
        self.assertEqual(vitals.total, 4000)
        self.assertIn(mech, match.bots)
        for _ in range(25):
            run.step({})
        self.assertNotIn(mech, match.bots)
        self.assertIsNone(mech.body_script)
        other.mover.position = [x, y, z + 25]
        other.mover.body = None
        self.assertEqual(pilot_script.component.entity_var(ULT_CHARGE).value(),
                         pilot_script.component.entity_var(ULT_COST).value())
        run.step({pilot: (0, 0, 32)})
        for _ in range(120):
            run.step({})
        replacement, = match.bots
        self.assertIsNot(replacement, mech)
        self.assertIs(pilot.controlled_mech, replacement)
        self.assertEqual(match.combat.vitals[replacement.entity].total, 600)

    def test_boosting_bomb_moves_and_finishes_fuse_after_pilot_death(self):
        from ow174.game.combat import TICK, Vitals

        run, pilot, other, _, mech = self.scenario()
        match = run.match
        pilot.body_script.ult_seconds = 0
        pilot_component = pilot.body_script.component
        pilot_charge = pilot_component.entity_var(ULT_CHARGE)
        pilot_cost = pilot_component.entity_var(ULT_COST).value()
        x, y, z = mech.position
        other.mover.position = [x, y, z + 9]
        other.mover.body = None
        for _ in range(100):
            run.step({})
        health = Vitals(other.body, 5000)
        match.combat.vitals[other.body] = health
        component = mech.body_script.component
        component.set(ULT_CHARGE, component.entity_var(ULT_COST).value())
        for _ in range(10):
            run.step({pilot: (0, 0, 8)})
        self.assertGreater(mech.velocity[2], 10)
        run.step({pilot: (0, 0, 32)})
        launch = mech.position
        run.step({})
        self.assertTrue(mech.detached_bomb)
        self.assertIsNone(pilot.controlled_mech)
        run.step({})
        self.assertGreater(mech.position[2], launch[2])
        match.combat.vitals[pilot.body] = Vitals(pilot.body, 5)
        point = match.combat._eye(pilot)
        yaw, pitch = aim(match.combat._eye(other), point)
        run.step({other: (yaw, pitch, 1)})
        self.assertTrue(match.combat.is_dead(pilot))
        self.assertIn(mech, match.bots)
        self.assertEqual(health.total, 5000)
        self.assertEqual(pilot_charge.value(), 0)
        refill = (pilot.body, 0x0240000000000592)
        with patch.object(match, "queue_body_message", wraps=match.queue_body_message) as queued:
            for _ in range(185):
                run.step({pilot: (0, 0, 32)})
            self.assertEqual(sum(call.args[:2] == refill for call in queued.call_args_list), 1)
        self.assertLess(health.total, 5000)
        self.assertEqual(pilot_charge.value(), pilot_cost)
        self.assertIsNone(pilot.controlled_mech)
        self.assertGreater(other.mover.velocity[2], 0)
        self.assertGreater(other.mover.velocity[1], 0)
        for _ in range(25):
            run.step({pilot: (0, 0, 32)})
        self.assertNotIn(mech, match.bots)
        self.assertFalse(match.bots)
        self.assertTrue(match.combat.is_dead(pilot))
        self.assertEqual(pilot_charge.value(), pilot_cost)

        old_body = pilot.body
        deadline, = [when for when, actor in match.combat.respawns if actor is pilot]
        while run.frame * TICK < deadline:
            run.step({})
        self.assertNotEqual(pilot.body, old_body)
        self.assertIsNot(pilot.body_script.component, pilot_component)
        self.assertNotIn(old_body, match.body_components)
        self.assertFalse(match.combat.is_dead(pilot))
        fresh = match.combat.player_target(pilot)
        self.assertIs(fresh.owner, pilot.controlled_mech)
        self.assertEqual(match.combat.vitals_of(fresh).total, 600)
        fresh_component = pilot.body_script.component
        fresh_charge = fresh_component.entity_var(ULT_CHARGE).value() or 0
        self.assertLess(fresh_charge, pilot_cost)
        self.assertFalse(match._body_messages)

    def test_boosting_bomb_stops_at_wall_and_cannot_blast_through_it(self):
        from test_game_mover import box, wall

        from ow174.game.collision import World
        from ow174.game.combat import Vitals

        run, pilot, other, _, mech = self.scenario()
        match = run.match
        x, y, z = mech.position
        wall_z = z + 3
        match.collision_world = World(
            box(x - 30, x + 30, 0, y, z - 30, z + 30) + wall(x - 15, wall_z, x + 15, wall_z)
        )
        other.mover.position = [x, y, z + 5]
        other.mover.body = None
        for _ in range(100):
            run.step({})
        health = Vitals(other.body, 5000)
        match.combat.vitals[other.body] = health
        component = mech.body_script.component
        component.set(ULT_CHARGE, component.entity_var(ULT_COST).value())
        for _ in range(10):
            run.step({pilot: (0, 0, 8)})
        run.step({pilot: (0, 0, 32)})
        for _ in range(187):
            run.step({})
        self.assertTrue(mech.detached_bomb)
        self.assertLess(mech.position[2], wall_z)
        self.assertEqual(health.total, 5000)
        self.assertEqual(other.mover.velocity, (0.0, 0.0, 0.0))

    def test_lethal_enemy_command_returns_live_pilot_to_defeated_mech_pose(self):
        from ow174.game.combat import Vitals
        from ow174.game.commands import Command

        run, pilot, other, instance, mech = self.scenario()
        match = run.match
        script = pilot.body_script
        parked = tuple(pilot.mover.position)
        for _ in range(35):
            run.frame += 1
            match.update(run.frame * .016, run.frame)
            pilot.take_commands([Command(run.frame, yaw=0, forward=127)])
        x, y, z = mech.position
        self.assertGreater(z, parked[2] + 1)
        other.mover.position = [x, y, z - 3]
        other.mover.body = None
        run.step({})
        match.combat.vitals[mech.entity] = Vitals(mech.entity, 5)
        yaw, pitch = aim(match.combat._eye(other), (x, y + 1.2, z))
        run.step({other: (yaw, pitch, 1)})
        self.assertTrue(mech.dead)
        self.assertIs(pilot.controlled_mech, mech)
        self.assertTrue(any(row[3] == body_entities.RETURN_TO_PILOT for row in match._body_messages))
        run.step({})
        self.assertIsNone(pilot.controlled_mech)
        self.assertIs(pilot.body_script, script)
        self.assertFalse(instance.state(11).active)
        self.assertEqual(tuple(pilot.mover.position), mech.position)
        self.assertEqual(match.combat.vitals_of(match.combat.player_target(pilot)).total, 150)
        other.mover.position = [x, y, z + 3]
        other.mover.body = None
        enemy = match.combat.vitals_of(match.combat.player_target(other))
        before = enemy.total
        yaw, pitch = aim(match.combat._eye(pilot), (x, y + 1.1, z + 3))
        lock = instance.state(92)
        self.assertTrue(lock.active)
        self.assertAlmostEqual(lock.evaluate(lock.node.field("m_timeout"), watch=False), 0.15)
        for _ in range(5):
            run.step({pilot: (yaw, pitch, 1)})
            self.assertTrue(lock.active)
            self.assertFalse(match.combat.rockets)
        for _ in range(10):
            run.step({pilot: (yaw, pitch, 1)})
            if match.combat.rockets:
                break
        self.assertFalse(lock.active)
        projectile, = match.combat.rockets
        self.assertIs(projectile.shooter, pilot)
        for _ in range(12):
            run.step({})
        self.assertEqual(enemy.total, before - 14)

    def test_returned_pilot_cannot_be_hit_before_becoming_a_combat_target(self):
        from ow174.game.combat import HIT, TICK, Vitals
        from ow174.game.commands import FIRE, Command

        for self_destruct in (False, True):
            for before_return in (False, True):
                with self.subTest(self_destruct=self_destruct, before_return=before_return):
                    run, pilot, enemy, lifecycle, mech = self.scenario()
                    match, fight = run.match, run.match.combat
                    x, y, z = mech.position
                    enemy.mover.position[:] = (x, y, z - 3)
                    enemy.mover.body = None
                    for _ in range(100):
                        run.step({})
                    pilot_body = pilot.body
                    self.assertIs(fight.player_target(pilot).owner, mech)
                    parked_mods = lifecycle.state(38)
                    self.assertEqual(parked_mods.node.pos, 70)
                    self.assertTrue(parked_mods.active)
                    yaw, pitch = aim(fight._eye(enemy), (x, y + 1.0, z))
                    if self_destruct:
                        component = mech.body_script.component
                        component.set(ULT_CHARGE, component.entity_var(ULT_COST).value())
                        run.step({pilot: (0, 0, 32)})
                    else:
                        fight.vitals[mech.entity] = Vitals(mech.entity, 5)
                        run.step({enemy: (yaw, pitch, FIRE)})
                        self.assertTrue(mech.dead)
                    run.step({})
                    self.assertIsNone(pilot.controlled_mech)
                    self.assertEqual(pilot.body, pilot_body)
                    self.assertFalse(parked_mods.active)
                    returned_tick = run.frame
                    for _ in range(20):
                        run.step({})
                    run.frame += 1
                    match.update(run.frame * TICK, run.frame)
                    target = fight.player_target(pilot)
                    health = fight.vitals_of(target)
                    before = health.total
                    old_hits = len(enemy.client.messages(HIT))
                    delay = (run.frame - returned_tick) * 16 + int(before_return)
                    enemy.take_commands([Command(run.frame, yaw=yaw, pitch=pitch, buttons=FIRE, extra=delay)])
                    self.assertEqual(before - health.total, 0 if before_return else 20)
                    hits = enemy.client.messages(HIT)[old_hits:]
                    self.assertEqual([hit["+0x78"]["+0x0"]["+0x0"] for hit in hits],
                                     [] if before_return else [pilot_body])

    def test_ejection_uses_parked_pose_when_pilot_capsule_cannot_fit(self):
        from test_game_mover import FLOOR

        from ow174.game.collision import World
        from ow174.game.combat import TICK, Vitals
        from ow174.game.commands import FIRE, Command
        from ow174.game.mover import statescript_mods

        for self_destruct in (False, True):
            with self.subTest(self_destruct=self_destruct):
                run, pilot, enemy, _, mech = self.scenario()
                match, fight = run.match, run.match.combat
                parked_mover = pilot.mover
                parked = tuple(parked_mover.position)
                # Model an existing mover with authored parked rows already tracked.
                parked_mover.track_mods(statescript_mods(pilot.body_script.component), run.frame)
                parked_mods = [mod for mod in parked_mover.mods.values() if mod.index in (11, 23)]
                self.assertEqual(sorted(mod.index for mod in parked_mods), [11, 23])
                pilot_body = pilot.body
                for _ in range(65):
                    run.frame += 1
                    match.update(run.frame * TICK, run.frame)
                    pilot.take_commands([Command(run.frame, forward=127)])
                x, y, z = mech.position
                self.assertGreater(z, parked[2] + 2)
                enemy.mover.position[:] = (x, y, z - 3)
                enemy.mover.body = None
                run.step({})
                yaw, pitch = aim(fight._eye(enemy), (x, y + 1.0, z))
                if self_destruct:
                    component = mech.body_script.component
                    component.set(ULT_CHARGE, component.entity_var(ULT_COST).value())
                    run.step({pilot: (0, 0, 32)})
                else:
                    fight.vitals[mech.entity] = Vitals(mech.entity, 5)
                    run.step({enemy: (yaw, pitch, FIRE)})
                    self.assertTrue(mech.dead)
                x, y, z = mech.position
                ceiling = y + .8
                roof = [
                    (x - 1, ceiling, z - 1, x + 1, ceiling, z + 1, x + 1, ceiling, z - 1),
                    (x - 1, ceiling, z - 1, x - 1, ceiling, z + 1, x + 1, ceiling, z + 1),
                ]
                match.collision_world = World(FLOOR + roof)
                match.deliver_body_messages()
                self.assertIsNone(pilot.controlled_mech)
                self.assertIs(pilot.mover, parked_mover)
                self.assertEqual(tuple(pilot.mover.position), parked)
                self.assertTrue(all(mod.acts(match.tick) for mod in parked_mods))
                self.assertFalse(any(mod.acts(match.tick + 1) for mod in parked_mods))
                self.assertEqual(fight.player_target(pilot).entity, pilot_body)
                self.assertEqual(fight.vitals_of(fight.player_target(pilot)).total, 150)
                self.assertEqual(mech.detached_bomb, self_destruct)

    def test_child_queries_live_bodies_and_current_possession_without_claiming_control(self):
        run, pilot, other, _, bot = self.scenario()
        component = bot.body_script.component
        instance = next(iter(component.instances.values()))
        def exists(entity):
            return instance.evaluate({"$": entity_queries.EXISTS, "m_entity": expr.Entity(entity)})

        self.assertIs(exists(bot.entity), True)
        self.assertIs(exists(pilot.entity), True)
        self.assertIs(exists(0xA0123456), False)
        self.assertEqual(instance.evaluate({"$": entity_queries.POSSESSED,
                                            "m_entity": expr.Entity(pilot.entity)}), expr.Entity(pilot.body))
        self.assertEqual(instance.evaluate({"$": entity_queries.POSSESSED,
                                            "m_entity": expr.Entity(bot.entity)}, initial=99), expr.Entity(0))
        old = other.body
        self.assertTrue(run.match.switch_hero(other, find_hero("soldier")))
        self.assertIs(exists(old), False)
        self.assertIs(exists(other.body), True)
        self.assertEqual(instance.evaluate({"$": "STU_DB742151", "m_entity": expr.Entity(bot.entity)}),
                         expr.Entity(0))
        self.assertTrue(component.world.script_component_present(bot.entity))
        npc.remove_body(run.match, bot)
        self.assertIs(component.world.entity_query_context(bot.entity), NotImplemented)
        self.assertIs(component.world.script_component_present(bot.entity), NotImplemented)

    def test_child_spatial_queries_read_own_and_pilot_pose_and_storage_tracks_context(self):
        run, pilot, _, _, bot = self.scenario()
        component = bot.body_script.component
        instance = next(iter(component.instances.values()))
        bot.position = (12, 4, -3)
        bot.yaw = 16384
        pilot.mover.position = [20, 8, 9]
        self.assertEqual(instance.evaluate({"$": "STU_0BA27A07", "m_entity": expr.Entity(bot.entity)}),
                         expr.Vec3(12, 4, -3))
        self.assertEqual(instance.evaluate({"$": "STU_0BA27A07", "m_entity": expr.Entity(pilot.body)}),
                         expr.Vec3(20, 8, 9))
        direction = instance.evaluate({"$": "STU_2EDEE064", "m_entity": expr.Entity(bot.entity)})
        self.assertAlmostEqual(direction.x, 1, places=6)
        self.assertEqual(direction.y, 0)
        bot.position = (15, 4, -3)
        self.assertEqual(instance.evaluate({"$": "STU_0BA27A07", "m_entity": expr.Entity(bot.entity)}),
                         expr.Vec3(15, 4, -3))
        before = snapshot_digest(export_component_vm(component))
        context = component.world._spatial_query_context
        context.namespace_present = False
        self.assertNotEqual(snapshot_digest(export_component_vm(component)), before)
        context.namespace_present = True
        npc.remove_body(run.match, bot)
        self.assertIs(component.world.spatial_query_context(bot.entity), NotImplemented)

    def test_actual_pilot_and_mech_messages_route_both_directions_at_existing_vm_clocks(self):
        run, pilot, _, pilot_instance, bot = self.scenario()
        child = next(i for i in bot.body_script.component.instances.values() if i.graph.index == 0x276)
        before = pilot.body_script.frame, child.component.frame, child.component.now
        listener = child.node_state(child.graph.node(107))
        count = listener.counter
        nodes.send(pilot_instance, pilot_instance.graph.node(43), None)  # actual007A to child and self.
        self.assertEqual(listener.counter, count)
        self.assertTrue(run.match._body_messages)
        with patch.object(runtime, "send_message", wraps=runtime.send_message) as deliver:
            run.match.deliver_body_messages()
        deliver.assert_any_call(child.component, 0x024000000000007A,
                                {0x5B: expr.Entity(bot.entity)}, pilot.body)
        self.assertGreater(listener.counter, count)
        nodes.send(child, child.graph.node(5), None)  # actual016C through owner->current body.
        self.assertTrue(run.match._body_messages)
        with patch.object(runtime, "send_message", wraps=runtime.send_message) as deliver:
            run.match.deliver_body_messages()
        deliver.assert_any_call(pilot.body_script.component, 0x024000000000016C, {}, bot.entity)
        self.assertEqual((pilot.body_script.frame, child.component.frame, child.component.now), before)

    def test_queued_owned_messages_do_not_survive_source_or_recipient_child_removal(self):
        for retire_source in (False, True):
            with self.subTest(retire_source=retire_source):
                run, _, _, pilot_instance, bot = self.scenario()
                child = next(i for i in bot.body_script.component.instances.values()
                             if i.graph.index == 0x276)
                if retire_source:
                    nodes.send(child, child.graph.node(5), None)
                else:
                    nodes.send(pilot_instance, pilot_instance.graph.node(43), None)
                self.assertTrue(run.match._body_messages)
                npc.remove_body(run.match, bot)
                with patch.object(runtime, "send_message", wraps=runtime.send_message) as deliver:
                    run.match.deliver_body_messages()
                deliver.assert_not_called()
                self.assertEqual(run.match._body_messages, [])

    def test_initial_return_requires_the_live_owned_mech(self):
        run, pilot, other, instance, bot = self.scenario()
        match = run.match
        control = instance.node_state(instance.graph.node(20))
        self.assertTrue(control.active)
        self.assertTrue(match.queue_body_message(pilot.body, body_entities.RETURN_TO_PILOT, {}, other.body))
        match.deliver_body_messages()
        self.assertTrue(control.active)

        child = next(i for i in bot.body_script.component.instances.values() if i.graph.index == 0x276)
        nodes.send(child, child.graph.node(5), None)
        self.assertTrue(control.active)
        match.deliver_body_messages()
        self.assertFalse(control.active)
        self.assertIs(match.body_components[pilot.body], instance.component)
        self.assertIs(match.body_components[bot.entity], child.component)

    def ready_call_mech(self):
        run, pilot, other, instance, old = self.scenario()
        child = next(i for i in old.body_script.component.instances.values() if i.graph.index == 0x276)
        nodes.send(child, child.graph.node(5), None)
        run.match.deliver_body_messages()
        for _ in range(100):
            run.step({})
        script = pilot.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        return run, pilot, other, instance, old

    def test_call_mech_blocked_placement_preserves_charge_and_existing_mech(self):
        run, pilot, other, instance, old = self.ready_call_mech()
        x, y, z = pilot.mover.position
        other.mover.position = [x, y, z + 2]
        other.mover.body = None
        script = pilot.body_script
        cost = script.component.entity_var(ULT_COST).value()
        run.step({pilot: (0, 0, 32)})
        for _ in range(60):
            run.step({})
        self.assertEqual(instance.state(15).counter, 1)
        self.assertEqual(instance.state(19).counter, 0)
        self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), cost)
        self.assertEqual(run.match.bots, [old])

    def test_call_mech_entry_sweeps_wall_and_does_not_finish_through_it(self):
        from test_game_mover import box, wall

        from ow174.game.collision import World

        run, pilot, _, instance, old = self.ready_call_mech()
        run.step({pilot: (0, 0, 32)})
        for _ in range(24):
            run.step({})
        mech, = run.match.bots
        self.assertIsNot(mech, old)
        x, y, z = pilot.mover.position
        entry = instance.state(47)
        destination = entry.evaluate(entry.node.field("m_865F3D01"), watch=False)
        wall_z = destination.z + pilot.mover.data.stand_radius - .05
        run.match.collision_world = World(
            box(x - 20, x + 20, 0, y, z - 20, z + 20) + wall(x - 2, wall_z, x + 2, wall_z)
        )
        for _ in range(100):
            run.step({})
        self.assertEqual(entry.counter, 1)
        self.assertFalse(entry.active)
        self.assertGreater(pilot.mover.position[2], z)
        self.assertLess(pilot.mover.position[2], destination.z - .04)
        self.assertEqual(instance.state(99).counter, 0)
        self.assertIsNone(pilot.controlled_mech)

    def test_call_mech_entry_immunity_blocks_flashbang_effect(self):
        from ow174.game.combat import FLASHBANG_EFFECT

        run, pilot, _, instance, _ = self.ready_call_mech()
        run.step({pilot: (0, 0, 32)})
        entry = instance.state(47)
        for _ in range(80):
            if entry.active:
                break
            run.step({})
        self.assertTrue(entry.active)
        fight = run.match.combat
        target = next(target for target in fight._targets() if target.owner is pilot)
        self.assertTrue(instance.state(24).active)
        fight._hit_effect(target, FLASHBANG_EFFECT)
        self.assertFalse(fight.is_stunned(pilot))
        for _ in range(20):
            run.step({})
        self.assertFalse(entry.active)
        self.assertEqual(instance.state(99).counter, 1)
        self.assertIsNotNone(pilot.controlled_mech)

    def test_call_mech_immunity_ends_on_lethal_replacement_return(self):
        from ow174.game import defenses
        from ow174.game.combat import Vitals

        run, pilot, enemy, lifecycle, old = self.ready_call_mech()
        fight = run.match.combat
        x, y, z = pilot.mover.position
        enemy.mover.position[:] = (x, y, z + 25)
        enemy.mover.body = None
        run.step({pilot: (0, 0, 32)})
        for _ in range(120):
            run.step({})
            if pilot.controlled_mech is not None:
                break
        mech = pilot.controlled_mech
        self.assertIsNotNone(mech)
        self.assertIsNot(mech, old)
        self.assertTrue(lifecycle.state(24).active)
        self.assertEqual(defenses.protection(pilot), "immune")
        self.assertIsNone(defenses.protection(mech))
        x, y, z = mech.position
        enemy.mover.position[:] = (x, y, z + 4)
        enemy.mover.body = None
        run.step({})
        mech_health = fight.vitals[mech.entity] = Vitals(mech.entity, 5)
        yaw, pitch = aim(fight._eye(enemy), (x, y + 1.2, z))
        run.step({enemy: (yaw, pitch, 1)})
        self.assertTrue(mech_health.dead)
        self.assertEqual(mech_health.killer, enemy.body)
        run.step({})
        self.assertIsNone(pilot.controlled_mech)
        self.assertIsNone(defenses.protection(pilot))
        target = fight.player_target(pilot)
        self.assertIs(target.owner, pilot)
        health = fight.vitals_of(target)
        self.assertEqual(health.total, 150)
        yaw, pitch = aim(fight._eye(enemy), (x, y + 1.1, z))
        for _ in range(15):
            run.step({enemy: (yaw, pitch, 1)})
            if health.total < 150:
                break
        self.assertLess(health.total, 150)

    def test_lethal_rifle_during_call_mech_retires_uncontrolled_replacement(self):
        from ow174.game.combat import Vitals

        for landed in (False, True):
            with self.subTest(landed=landed):
                run, pilot, enemy, instance, old = self.ready_call_mech()
                match = run.match
                fight = match.combat
                run.step({pilot: (0, 0, 32)})
                self.assertEqual(pilot.body_script.component.entity_var(ULT_CHARGE).value(), 0)
                for _ in range(24):
                    run.step({})
                replacement, = match.bots
                self.assertIsNot(replacement, old)
                replacement_script = replacement.body_script
                self.assertIsNone(pilot.controlled_mech)
                self.assertEqual(instance.state(20).counter, 0)
                x, y, z = replacement.position
                enemy.mover.position[:] = (x + 1.5, y, z)
                enemy.mover.body = None
                enemy_health = Vitals(enemy.body, 1000)
                fight.vitals[enemy.body] = enemy_health
                fight._sync_body_health(enemy, enemy_health)
                if landed:
                    for _ in range(45):
                        run.step({})
                        if instance.state(20).counter:
                            break
                    self.assertGreater(enemy.mover.velocity[0], 20)
                expected_health = 750 if landed else 1000
                self.assertEqual(enemy_health.total, expected_health)
                self.assertEqual(instance.state(20).counter, int(landed))
                self.assertEqual(instance.state(99).counter, 0)
                self.assertIsNone(pilot.controlled_mech)
                target = fight.player_target(pilot)
                self.assertIs(target.owner, pilot)
                health = fight.vitals_of(target)
                health.damage(health.total - 1)
                fight._sync_body_health(pilot, health)
                for _ in range(6):
                    x, y, z = pilot.mover.position
                    yaw, pitch = aim(fight._eye(enemy), (x, y + 1, z))
                    run.step({enemy: (yaw, pitch, 1)})
                    if health.dead:
                        break
                self.assertTrue(health.dead)
                self.assertEqual(health.killer, enemy.body)
                if landed:
                    self.assertGreater(enemy.mover.velocity[0], 0)
                else:
                    self.assertEqual(enemy.mover.velocity, (0, 0, 0))
                self.assertNotIn(replacement, match.bots)
                self.assertNotIn(replacement.entity, fight.vitals)
                self.assertNotIn(replacement.entity, match.body_components)
                self.assertIsNone(replacement.body_script)
                self.assertTrue(replacement_script.component.owner_dead)
                self.assertFalse(replacement_script.component.instances)
                for _ in range(80):
                    run.step({})
                self.assertIsNone(pilot.controlled_mech)
                self.assertEqual(instance.state(20).counter, int(landed))
                self.assertEqual(instance.state(99).counter, 0)
                self.assertEqual(enemy_health.total, expected_health)

    def test_in_flight_light_gun_cannot_hit_own_call_mech_replacement(self):
        from ow174.game.combat import Vitals

        run, pilot, enemy, _, old = self.ready_call_mech()
        fight = run.match.combat
        pilot.mover.position[0] += 10
        pilot.mover.body = None
        x, y, z = pilot.mover.position
        enemy.mover.position[:] = (x, y, z + 80)
        enemy.mover.body = None
        health = Vitals(enemy.body, 1000)
        fight.vitals[enemy.body] = health
        pilot.body_script.ult_seconds = 0
        self.enterContext(patch.object(body_entities, "ULT_SECONDS", 0))
        run.step({pilot: (0, 0, 1)})
        rocket, = fight.rockets
        self.assertIs(rocket.shooter, pilot)
        run.step({pilot: (0, 0, 32)})
        for _ in range(125):
            run.step({})
            if pilot.controlled_mech is not None:
                break
        mech = pilot.controlled_mech
        self.assertIsNotNone(mech)
        self.assertIsNot(mech, old)
        self.assertIn(rocket, fight.rockets)
        self.assertEqual(health.total, 1000)
        x, _, z = rocket.position
        position = (x, y, z + 1.5)
        mech.position = position
        mech.control_mover.position[:] = position
        mech.control_mover.body = None
        mech_health = fight.vitals_of(fight.player_target(pilot))
        pilot_charge = pilot.body_script.component.entity_var(ULT_CHARGE)
        mech_charge = mech.body_script.component.entity_var(ULT_CHARGE)
        starting_charge = pilot_charge.value(), mech_charge.value()
        for _ in range(110):
            run.step({})
            if rocket not in fight.rockets:
                break
        self.assertNotIn(rocket, fight.rockets)
        self.assertEqual(mech_health.total, 600)
        self.assertEqual(health.total, 986)
        self.assertEqual((pilot_charge.value(), mech_charge.value()), starting_charge)

    def test_call_mech_routes_player_fire_to_existing_mech_then_returns_to_pilot(self):
        from test_game_combat import aim

        from ow174.game.combat import CRITICAL, HIT, Vitals
        from ow174.game.commands import Command

        run, pilot, other, instance, old = self.ready_call_mech()
        pilot_script = pilot.body_script
        run.step({pilot: (0, 0, 32)})
        for _ in range(24):
            run.step({})
        mech, = run.match.bots
        self.assertIsNot(mech, old)
        child_script = mech.body_script
        for _ in range(100):
            if pilot.controlled_mech is not None:
                break
            run.step({})
        self.assertIs(pilot.controlled_mech, mech)
        self.assertEqual(instance.state(99).counter, 1)
        self.assertIs(pilot.body_script, pilot_script)
        self.assertIs(mech.body_script, child_script)
        x, y, z = mech.position
        other.mover.position = [x, y, z + 2]
        other.mover.body = None
        fight = run.match.combat
        vitals = Vitals(other.body, 1000)
        fight.vitals[other.body] = vitals
        run.step({})
        notices = len(pilot.client.messages(HIT))
        target = fight.player_target(other)
        x, y, z = target.feet
        yaw, pitch = aim(fight._eye(mech), (x, y + target.height * .4, z))
        run.step({pilot: (yaw, pitch, 1)})
        self.assertEqual(vitals.total, 978)
        hits = pilot.client.messages(HIT)[notices:]
        self.assertEqual(len(hits), 11)
        self.assertTrue(all(not hit["+0x78"]["+0x8"] & CRITICAL for hit in hits))
        self.assertEqual(child_script.frame, run.frame)
        self.assertFalse(pilot_script.component.held)
        match = run.match
        self.assertNotIn(pilot.body, {target.entity for target in match.combat._targets()})
        start = mech.position
        parked = tuple(pilot.mover.position)
        for _ in range(12):
            run.frame += 1
            match.update(run.frame * .016, run.frame)
            pilot.take_commands([Command(run.frame, yaw=yaw, forward=127)])
        self.assertGreater(mech.position[2], start[2])
        self.assertEqual(tuple(pilot.mover.position), parked)

        self.assertTrue(match.queue_body_message(pilot.body, body_entities.RETURN_TO_PILOT, {}, other.body))
        match.deliver_body_messages()
        self.assertIs(pilot.controlled_mech, mech)

        run.step({pilot: (yaw, pitch, 1)})
        self.assertIn(20, child_script.component.held)
        child = next(i for i in child_script.component.instances.values() if i.graph.index == 0x276)
        nodes.send(child, child.graph.node(5), None)
        run.match.deliver_body_messages()
        self.assertIsNone(pilot.controlled_mech)
        self.assertIs(pilot.body_script, pilot_script)
        run.step({pilot: (yaw, pitch, 1)})
        self.assertFalse(child_script.component.held)
        self.assertIn(20, pilot_script.component.held)

    def test_light_gun_reloads_during_possession_and_fires_after_replacement_ejection(self):
        for self_destruct in (False, True):
            with self.subTest(self_destruct=self_destruct):
                self.check_replacement_ejection_restores_light_gun(self_destruct)

    def check_replacement_ejection_restores_light_gun(self, self_destruct):
        from ow174.game.combat import Vitals

        run, pilot, enemy, lifecycle, old = self.ready_call_mech()
        script = pilot.body_script
        script.ult_seconds = 0
        charge = script.component.entity_var(ULT_CHARGE)
        weapon = next(i for i in script.component.instances.values() if i.graph.index == 0x2B8)
        magazine = weapon.vars[53]
        x, y, z = pilot.mover.position
        enemy.mover.position[:] = (x, y, z + 3)
        enemy.mover.body = None
        run.step({})
        run.match.combat.vitals[enemy.body] = Vitals(enemy.body, 5000)
        for _ in range(220):
            run.step({pilot: (0, 0, 1)})
            if magazine.value() == 0:
                break
        self.assertEqual(magazine.value(), 0)
        enemy.mover.position[:] = (x, y, z + 25)
        enemy.mover.body = None
        run.step({pilot: (0, 0, 32)})
        self.assertEqual(magazine.value(), 0)
        for _ in range(120):
            run.step({})
            if pilot.controlled_mech is not None:
                break
        replacement = pilot.controlled_mech
        self.assertIsNotNone(replacement)
        self.assertIsNot(replacement, old)
        self.assertIs(pilot.body_script, script)
        for _ in range(150):
            if magazine.value() == 20:
                break
            run.step({})
        self.assertIs(pilot.controlled_mech, replacement)
        self.assertEqual(magazine.value(), 20)

        fight = run.match.combat
        self.assertIn(lifecycle.state(5), script.component.entity_var(33).stack_states())
        if self_destruct:
            component = replacement.body_script.component
            component.set(ULT_CHARGE, component.entity_var(ULT_COST).value())
            run.step({pilot: (0, 0, 32)})
        else:
            x, y, z = replacement.position
            enemy.mover.position[:] = (x, y, z + 3)
            enemy.mover.body = None
            run.step({})
            mech_health = fight.vitals[replacement.entity] = Vitals(replacement.entity, 5)
            yaw, pitch = aim(fight._eye(enemy), (x, y + 1.2, z))
            run.step({enemy: (yaw, pitch, 1)})
            self.assertTrue(mech_health.dead)
            self.assertEqual(mech_health.killer, enemy.body)
        run.step({})
        self.assertEqual(replacement.detached_bomb, self_destruct)
        self.assertIsNone(pilot.controlled_mech)
        self.assertIs(pilot.body_script, script)
        self.assertEqual(magazine.value(), 20)
        self.assertNotIn(lifecycle.state(5), script.component.entity_var(33).stack_states())
        x, y, z = pilot.mover.position
        enemy.mover.position[:] = (x, y, z + 3)
        enemy.mover.body = None
        for _ in range(20):
            run.step({})
        health = fight.vitals_of(fight.player_target(enemy))
        before = health.total
        yaw, pitch = aim(fight._eye(pilot), (x, y + 1.1, z + 3))
        run.step({pilot: (yaw, pitch, 1)})
        for _ in range(12):
            run.step({})
        self.assertEqual(magazine.value(), 19)
        self.assertEqual(health.total, before - 14)

        if self_destruct:
            return
        cost = script.component.entity_var(ULT_COST).value()
        self.assertAlmostEqual(cost, 319.2, places=4)
        self.assertEqual(charge.value(), 14)
        for _ in range(600):
            run.step({pilot: (yaw, pitch, 1)})
            if charge.value() >= cost:
                break
        self.assertEqual(charge.value(), cost)
        self.assertEqual(health.total, before - 23 * 14)

        enemy.mover.position[:] = (x, y, z + 25)
        enemy.mover.body = None
        run.step({pilot: (0, 0, 32)})
        for _ in range(120):
            run.step({})
            if pilot.controlled_mech is not None:
                break
        new_mech = pilot.controlled_mech
        self.assertIsNotNone(new_mech)
        self.assertNotEqual(new_mech.entity, replacement.entity)
        self.assertEqual(charge.value(), 0)
        self.assertIs(pilot.body_script, script)
        self.assertIs(fight.player_target(pilot).owner, new_mech)

        for _ in range(100):
            run.step({})
        x, y, z = new_mech.position
        enemy.mover.position[:] = (x, y, z + 2)
        enemy.mover.body = None
        for _ in range(15):
            run.step({})
        tx, ty, tz = fight.player_target(enemy).feet
        yaw, pitch = aim(fight._eye(new_mech), (tx, ty + .8, tz))
        mech_charge = new_mech.body_script.component.entity_var(ULT_CHARGE)
        charge_before = mech_charge.value() or 0
        ammo_before = magazine.value()
        health_before = health.total
        notices = len(pilot.client.messages(25001))
        with patch.object(body_entities, "ULT_SECONDS", 0):
            run.step({pilot: (yaw, pitch, 1)})
        self.assertEqual(health.total, health_before - 22)
        self.assertEqual(len(pilot.client.messages(25001)) - notices, 11)
        self.assertAlmostEqual(mech_charge.value() - charge_before, 22, places=4)
        self.assertEqual(charge.value(), 0)
        self.assertEqual(magazine.value(), ammo_before)
        self.assertFalse(script.component.held)

    def test_control_binding_and_held_input_are_causal_snapshot_state(self):
        from ow174.game import bots
        from ow174.game.content import PRACTICE_RANGE
        from ow174.game.mover import Mover
        from ow174.game.server_snapshot import snapshot_digest as server_digest

        case = storage_tests.ServerSnapshotTests()
        session, _, players = case.scene()
        pilot = players[0]
        match = pilot.match
        bot = bots.make(PRACTICE_RANGE.map_guid)[0]
        match.bots.append(bot)
        unbound = server_digest(case.capture(session))
        pilot.controlled_mech = bot
        bot.control_mover = Mover(bot.position, world=match.collision_world)
        bound = server_digest(case.capture(session))
        self.assertNotEqual(unbound, bound)
        bot.control_buttons = 1
        self.assertNotEqual(bound, server_digest(case.capture(session)))

    def test_child_message_failure_retires_only_the_failing_body(self):
        run, pilot, _, instance, bot = self.scenario()
        pilot_script, child_script = pilot.body_script, bot.body_script
        nodes.send(instance, instance.graph.node(43), None)
        with (
            patch.object(runtime, "send_message", side_effect=RuntimeError("receiver failed")),
            self.assertLogs("ow174.game", level="ERROR"),
        ):
            run.match.deliver_body_messages()
        self.assertNotIn(bot, run.match.bots)
        self.assertIsNone(bot.body_script)
        self.assertIsNone(child_script.component.world.match)
        self.assertIs(pilot.body_script, pilot_script)
        self.assertFalse(pilot_script.retired)
        self.assertEqual(run.match._body_messages, [])

    def test_enemy_command_damages_created_mech_armour(self):
        run, pilot, other, _, bot = self.scenario()
        pilot.mover.position = [20, 1, 20]
        pilot.mover.body = None
        other.mover.position = [0, 1, 0]
        other.mover.body = None
        bot.position = (0, 1, 3)
        bot.control_mover.position = list(bot.position)
        yaw, pitch = aim(run.match.combat._eye(other), (0, 2, 3))
        run.step({other: (yaw, pitch, 1)})
        vitals = run.match.combat.vitals[bot.entity]
        self.assertEqual((vitals.now[0], vitals.now[1]), (300, 285))

    def test_switch_retires_owned_body_vm_and_stale_batch_cannot_recreate_health(self):
        run, pilot, other, _, bot = self.scenario()
        component = bot.body_script.component
        target = next(t for t in run.match.combat._targets() if t.owner is bot)
        run.match.switch_hero(pilot, find_hero("soldier"))
        self.assertNotIn(bot, run.match.bots)
        self.assertTrue(component.owner_dead)
        self.assertFalse(component.instances)
        self.assertNotIn(bot.entity, run.match.combat.vitals)
        run.match.combat._damage(other, target, 20, False, None, run.frame)
        self.assertNotIn(bot.entity, run.match.combat.vitals)

    def test_pilot_death_and_leave_retire_the_creating_bodys_mech(self):
        for operation in ("death", "leave"):
            run, pilot, other, _, bot = self.scenario()
            if operation == "leave":
                run.match.leave(pilot)
            else:
                dva_pilot_input(pilot)
                target = next(t for t in run.match.combat._targets() if t.owner is pilot)
                run.match.combat._damage(other, target, 1000, False, None, run.frame)
            self.assertNotIn(bot, run.match.bots)
            self.assertNotIn(bot.entity, run.match.combat.vitals)

    def test_manual_npc_removal_does_not_remove_a_script_created_mech(self):
        run, _, _, _, bot = self.scenario()
        self.assertEqual(npc.remove_spawned(run.match), ())
        self.assertIn(bot, run.match.bots)

    def test_missing_prepared_definition_refuses_creation_before_allocating(self):
        run, _, _, instance, _ = self.scenario()
        before = run.match.combat.next_bot, tuple(run.match.bots)
        with patch.object(body_entities.graph, "entity_body", return_value=None):
            result = body_entities.create_mech(run.match, instance, instance.graph.node(7))
        self.assertEqual(result, expr.Entity(0))
        self.assertEqual((run.match.combat.next_bot, tuple(run.match.bots)), before)

    def test_authored_predecessor_cleans_old_body_before_creating_each_replacement(self):
        for position in (211, 212):
            run, pilot, _, instance, old = self.scenario()
            instance.find_var(0, 6472).base = expr.Vec3(5, 1, 5)
            instance.run_node(instance.graph.node(position), None)
            instance.component.run_now()
            self.assertNotIn(old, run.match.bots)
            current = [b for b in run.match.bots if b.owner == pilot.entity]
            self.assertEqual(len(current), 1)
            self.assertGreater(current[0].entity, old.entity)
            self.assertEqual(instance.vars[38].value(), expr.Entity(current[0].entity))
            if position == 212:
                self.assertEqual(current[0].position, (5, 1, 5))

    def test_authored_cleanup_preserves_flag842_body_for_ten_plus_three_second_waits(self):
        run, _, _, instance, old = self.scenario()
        old.body_script.component.set(842, True)
        instance.run_node(instance.graph.node(211), None)
        instance.component.run_now()
        self.assertIn(old, run.match.bots)
        self.assertEqual(len(run.match.bots), 2)
        run.match.tick += 625  #10s: second authored3s wait begins.
        run.match.combat._bot_weapons(run.match.tick)
        self.assertIn(old, run.match.bots)
        run.match.tick += 188
        run.match.combat._bot_weapons(run.match.tick)
        self.assertNotIn(old, run.match.bots)
        self.assertEqual(len(run.match.bots), 1)

    def test_owned_body_and_match_bound_vm_are_causal_passive_server_storage(self):
        case = storage_tests.ServerSnapshotTests()
        session, peers, players = case.scene()
        pilot = players[0]
        peers[0].message(21616, storage_tests.pick(pilot.entity, body_entities.DVA), now=101.0)
        self.assertEqual(pilot.hero.guid, body_entities.DVA)
        bot = next(b for b in pilot.match.bots if b.owner == pilot.entity)
        first = storage_tests.snapshot_digest(case.capture(session))
        bot.owner += 0x100
        self.assertNotEqual(first, storage_tests.snapshot_digest(case.capture(session)))
        bot.owner = pilot.entity
        bot.clock_offset += 10
        self.assertNotEqual(first, storage_tests.snapshot_digest(case.capture(session)))
        bot.clock_offset -= 10
        self.assertTrue(pilot.match.queue_body_message(bot.entity, 0x024000000000007A,
                                                       {0x5B: expr.Entity(bot.entity)}, pilot.body))
        pending = storage_tests.snapshot_digest(case.capture(session))
        self.assertNotEqual(first, pending)
        self.assertEqual(pending, storage_tests.snapshot_digest(case.capture(session)))
        pilot.match._body_messages.clear()
        peers[0].message(21616, storage_tests.pick(pilot.entity, find_hero("soldier").guid), now=101.1)
        self.assertNotIn(bot, pilot.match.bots)
        self.assertIsNone(bot.body_script)
        self.assertIsInstance(case.capture(session), dict)

    def test_child_vm_resolves_direct_owner_during_initialization_but_not_primary_possession(self):
        constructor = body_entities.BotScript
        observations = []

        def boot(*args, **kwargs):
            script = constructor(*args, **kwargs)
            instance = next(iter(script.component.instances.values()))
            entity = expr.Entity(script.component.entity)
            owner = instance.evaluate({"$": "STU_F7262B36", "m_801B298F": entity})
            primary = instance.evaluate({"$": "STU_DB742151", "m_entity": entity})
            observations.append((owner, primary))
            return script

        with patch.object(body_entities, "BotScript", side_effect=boot):
            run, pilot, _, instance, bot = self.scenario()
        self.assertEqual(observations, [(expr.Entity(pilot.entity), expr.Entity(0))])
        child = next(iter(bot.body_script.component.instances.values()))
        for target in (pilot.body, pilot.entity):
            self.assertEqual(child.evaluate({"$": "STU_DB742151", "m_entity": expr.Entity(target)}),
                             expr.Entity(pilot.entity))
        self.assertEqual(instance.evaluate({"$": "STU_DB742151", "m_entity": expr.Entity(bot.entity)}),
                         expr.Entity(0))
        possession = instance.node_state(instance.graph.node(20))
        self.assertTrue(possession.active)
        self.assertEqual(possession.evaluate(possession.node.field("m_F3E612FD"), watch=False),
                         expr.Entity(pilot.entity))
        self.assertEqual(possession.evaluate(possession.node.field("m_C98661CF"), watch=False),
                         expr.Entity(bot.entity))
        self.assertEqual(run.match.combat.vitals[bot.entity].total, 600)

    def test_owned_cleanup_clock_preserves_ten_plus_three_seconds_with_different_command_origins(self):
        for command_frame in (500, 5000):
            with self.subTest(command_frame=command_frame):
                run = Run(KANEZAKA_DM, ("DVa",))
                run.match.bots.clear()
                run.match._pending_bot_scripts.clear()
                pilot = run.players[0]
                pilot.commands.last_frame = command_frame  # explicit input-clock fixture.
                self.assertTrue(run.match.switch_hero(pilot, find_hero("dva")))
                instance = next(i for i in pilot.body_script.component.instances.values()
                                if i.graph.index == 0x26A)
                old = next(b for b in run.match.bots if b.owner == pilot.entity)
                self.assertEqual(old.body_script.frame, command_frame)
                self.assertEqual(old.clock_offset, command_frame-run.match.tick)
                old.body_script.component.set(842, True)
                instance.run_node(instance.graph.node(211), None)
                instance.component.run_now()
                run.match.tick += 625
                run.match.combat._bot_weapons(run.match.tick)
                self.assertIn(old, run.match.bots)
                self.assertEqual(old.body_script.frame, command_frame+625)
                run.match.tick += 188
                run.match.combat._bot_weapons(run.match.tick)
                self.assertNotIn(old, run.match.bots)

    def test_failed_child_vm_initialization_withdraws_pending_body_without_publishing_identity(self):
        run, _, _, instance, _ = self.scenario()
        before = run.match.combat.next_bot, tuple(run.match.bots), dict(run.match.combat.vitals)
        with (
            patch.object(body_entities, "BotScript", side_effect=RuntimeError("initialization failed")),
            self.assertRaisesRegex(RuntimeError, "initialization failed"),
        ):
            body_entities.create_mech(run.match, instance, instance.graph.node(7))
        after = run.match.combat.next_bot, tuple(run.match.bots), dict(run.match.combat.vitals)
        self.assertEqual(after, before)

    def test_owned_death_script_uses_vm_clock_and_vitals_keep_world_tick(self):
        for command_frame in (500, 5000):
            run = Run(KANEZAKA_DM, ("DVa", "Enemy"))
            run.match.bots.clear()
            run.match._pending_bot_scripts.clear()
            pilot, enemy = run.players
            pilot.commands.last_frame = command_frame
            self.assertTrue(run.match.switch_hero(pilot, find_hero("dva")))
            bot = next(b for b in run.match.bots if b.owner == pilot.entity)
            target = next(t for t in run.match.combat._targets() if t.owner is bot)
            with (
                patch.object(pilot.client, "queue_bot_death", create=True),
                patch.object(enemy.client, "queue_bot_death", create=True),
                patch.object(pilot.client, "queue_bot_death_update", create=True),
                patch.object(enemy.client, "queue_bot_death_update", create=True),
            ):
                run.match.combat._damage(enemy, target, 1000, False, None, command_frame)
                self.assertTrue(bot.dead)
                self.assertIsNotNone(bot.body_script)
                self.assertEqual(bot.body_script.death_frame, command_frame)
                self.assertEqual(run.match.combat.vitals[bot.entity].death_tick, run.match.tick)
                run.match.combat._advance_bot_death(bot, run.match.tick+188)
                self.assertIsNotNone(bot.body_script)


if __name__ == "__main__":
    unittest.main()
