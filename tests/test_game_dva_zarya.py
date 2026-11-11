"""Actual Zarya barriers and cleanse against the live D.Va body."""

import unittest
from dataclasses import replace
from unittest.mock import patch

import test_game_dva_ana as ana_tests
import test_game_dva_created_body as created_tests
import test_game_dva_manual_npcs as manual_tests
import test_game_dva_sleep as sleep_tests
from test_game_combat import aim, in_world

from ow174.game import (
    assassin,
    body_entities,
    combat,
    defenses,
    graviton,
    npc,
    npc_charge,
    npc_control,
    projectiles,
    sigma_grasp,
    zarya_barriers,
)
from ow174.game.commands import FIRE, JUMP, SECONDARY, Command
from ow174.game.content import find_hero, heroes
from ow174.game.script.driver import ULT_CHARGE, ULT_COST


class DvaZaryaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ana_tests.DvaAnaTests.setUpClass()

    def graviton_scene(self, *, pilot_form=False, matrix=False, miss=False, with_zarya=False):
        run, pilot, caster, soldier, target, health = ana_tests.DvaAnaTests().scene(
            pilot_form=pilot_form, with_zarya=with_zarya,
        )
        fight = run.match.combat
        soldier.spawned = False
        health.heal(350)
        self.assertTrue(run.match.switch_hero(caster, find_hero("zarya")))
        x, y, z = target.feet
        caster.mover.position[:] = (x, y, z + 8)
        caster.mover.body = None
        for _ in range(30):
            run.step({})
        angles = aim(fight._eye(caster), (x + (3 if miss else 0), y + .85, z))
        guard = {pilot: (0, 0, 2)} if matrix else {}
        if matrix:
            for _ in range(4):
                run.step(guard)
        rocket = self.launch_graviton(run, caster, angles, guard)
        return run, pilot, caster, target, health, guard, rocket

    def launch_graviton(self, run, caster, angles, commands):
        script = caster.body_script
        cost = script.component.entity_var(ULT_COST).value()
        self.assertEqual(cost, 2100)
        script.set_ult_charge(cost)
        run.step({**commands, caster: (*angles, 0x20)})
        rocket, = run.match.combat.rockets
        self.assertEqual(rocket.spawn, graviton.FIELD_ENTITY)
        self.assertAlmostEqual(rocket.gravity, 9.8, places=5)
        self.assertEqual(rocket.direct.amount, 5)
        self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)
        return rocket

    @staticmethod
    def until_graviton(run):
        host = run.match.combat.zarya_gravitons
        for _ in range(90):
            run.step({})
            if host.fields:
                break
        field, = host.fields
        return field

    def test_actual_graviton_matrix_intercepts_before_field(self):
        run, _, _, _, health, guard, rocket = self.graviton_scene(matrix=True)
        fight = run.match.combat
        before = health.total
        self.assertIsNotNone(rocket.shot)
        with patch.object(fight.projectiles, "absorbed", wraps=fight.projectiles.absorbed) as absorbed:
            for _ in range(45):
                run.step(guard)
        absorbed.assert_called_once_with(rocket.shot)
        self.assertFalse(fight.rockets)
        self.assertFalse(fight.zarya_gravitons.fields)
        self.assertFalse(fight.zarya_gravitons.pulled)
        self.assertEqual(health.total, before)

    def graviton_defender(self, pilot_form, hero, *, extra_player=False):
        run, pilot, caster, defender, target, health = ana_tests.DvaAnaTests().scene(
            pilot_form=pilot_form, with_zarya=extra_player,
        )
        match, fight = run.match, run.match.combat
        self.assertTrue(match.switch_hero(caster, find_hero("zarya")))
        self.assertTrue(match.switch_hero(defender, find_hero(hero)))
        health.heal(350)
        x, y, z = target.feet
        caster.mover.position[:] = (x, y, z + 8)
        defender.mover.position[:] = (x, y, z + 2)
        caster.mover.body = defender.mover.body = None
        if hero == "genji":
            # Both Zarya and D.Va are enemies of the new projectile owner.
            caster.team, defender.team = pilot.team, caster.team
            mover = pilot.mover if pilot_form else target.owner.control_mover
            mover.position[:] = (x, y, z + 5)
            mover.body = None
            if not pilot_form:
                target.owner.position = tuple(mover.position)
        for _ in range(30):
            run.step({})
        angles = aim(fight._eye(caster), (x, y + 1.25, z + 2))
        return run, pilot, caster, defender, target, health, angles

    def test_actual_genji_redirects_graviton_to_either_dva_form_or_matrix(self):
        for pilot_form, matrix in ((False, False), (True, False), (False, True)):
            with self.subTest(pilot_form=pilot_form, matrix=matrix):
                self.check_genji_graviton(pilot_form, matrix)

    def check_genji_graviton(self, pilot_form, matrix):
        run, pilot, caster, genji, target, health, angles = self.graviton_defender(pilot_form, "genji")
        fight = run.match.combat
        genji_graph = next(i for i in genji.body_script.component.instances.values()
                           if i.graph.index == 0x03A3)
        guard = {genji: (0, 0, 16)}
        if matrix:
            guard[pilot] = (-32768, 0, 2)
        for _ in range(4):
            run.step(guard)
        self.assertTrue(genji_graph.state(10).active)
        caster.body_script.ult_seconds = 0
        before = health.total
        rocket = self.launch_graviton(run, caster, angles, guard)
        lifetime = rocket.left + rocket.flown
        original = rocket.shot
        with patch.object(fight.projectiles, "absorbed", wraps=fight.projectiles.absorbed) as absorbed:
            for _ in range(60):
                run.step(guard)
                if rocket not in fight.rockets:
                    break
                self.assertAlmostEqual(rocket.left + rocket.flown, lifetime, places=8)
        absorbed.assert_called_once_with(original)
        self.assertIsNone(rocket.shot)
        self.assertNotIn(rocket, fight.rockets)
        self.assertIs(rocket.shooter, genji)
        self.assertEqual(rocket.source_entity, genji.body)
        self.assertEqual(genji_graph.vars[6584].value(), 5)
        self.assertEqual(genji_graph.vars[41].value().id, caster.body)
        self.assertEqual(caster.body_script.component.entity_var(ULT_CHARGE).value(), 0)
        if matrix:
            self.assertFalse(fight.zarya_gravitons.fields)
            self.assertEqual(health.total, before)
            self.assertNotIn(target.entity, fight.zarya_gravitons.pulled)
            return
        field, = fight.zarya_gravitons.fields
        self.assertIs(field.shooter, genji)
        self.assertEqual((field.source_entity, field.team), (genji.body, genji.team))
        impact = 5 if pilot_form else 2.5
        self.assertAlmostEqual(before - health.total, impact, places=6)
        run.step({})
        self.assertIn(target.entity, fight.zarya_gravitons.pulled)
        self.assertAlmostEqual(before - health.total, impact + .08, places=6)
        self.assertNotIn(genji.body, fight.zarya_gravitons.pulled)
        while run.frame < field.end_tick:
            run.frame = min(run.frame + 32, field.end_tick)
            run.match.update(run.frame * combat.TICK, run.frame)
        self.assertFalse(fight.zarya_gravitons.fields)
        self.assertNotIn(target.entity, fight.zarya_gravitons.pulled)
        self.assertAlmostEqual(before - health.total, impact + 20, places=6)

    def test_actual_sigma_grasp_converts_graviton_before_either_dva_form(self):
        for pilot_form in (False, True):
            with self.subTest(pilot_form=pilot_form):
                run, _, caster, sigma, target, health, angles = self.graviton_defender(pilot_form, "sigma")
                fight = run.match.combat
                sigma_health = fight.vitals_of(fight.player_target(sigma))
                grasp = next(i for i in sigma.body_script.component.instances.values()
                             if i.graph.index == sigma_grasp.GRASP)
                run.step({sigma: (0, 0, 8)})
                for _ in range(30):
                    if grasp.state(1).active:
                        break
                    run.step({})
                self.assertTrue(grasp.state(1).active)
                caster.body_script.ult_seconds = 0
                before = health.total
                rocket = self.launch_graviton(run, caster, angles, {})
                original = rocket.shot
                with patch.object(
                    fight.projectiles, "absorbed", wraps=fight.projectiles.absorbed,
                ) as absorbed:
                    for _ in range(145):
                        run.step({})
                absorbed.assert_called_once_with(original)
                self.assertFalse(fight.rockets)
                self.assertFalse(fight.zarya_gravitons.fields)
                self.assertNotIn(target.entity, fight.zarya_gravitons.pulled)
                self.assertEqual(health.total, before)
                self.assertEqual(caster.body_script.component.entity_var(ULT_CHARGE).value(), 0)
                self.assertEqual(grasp.vars[21312].value(), 5)
                bonus = sigma_health.temporary_shields[sigma_grasp.SHIELD_TAG].now
                self.assertAlmostEqual(bonus, 3, places=5)

    def test_actual_sigma_grasp_converts_genji_returned_graviton(self):
        run, _, caster, genji, target, health, angles = self.graviton_defender(
            True, "genji", extra_player=True,
        )
        match, fight = run.match, run.match.combat
        sigma = run.players[3]
        self.assertTrue(match.switch_hero(sigma, find_hero("sigma")))
        sigma.team = caster.team
        x, y, z = genji.mover.position
        sigma.mover.position[:] = (x, y, z + 2)
        sigma.mover.body = None
        for _ in range(30):
            run.step({})
        grasp = next(i for i in sigma.body_script.component.instances.values()
                     if i.graph.index == sigma_grasp.GRASP)
        genji_graph = next(i for i in genji.body_script.component.instances.values()
                           if i.graph.index == 0x03A3)
        guard = {genji: (0, 0, 16), sigma: (-32768, 0, 8)}
        for _ in range(4):
            run.step(guard)
        self.assertTrue(grasp.state(1).active)
        self.assertTrue(genji_graph.state(10).active)
        caster.body_script.ult_seconds = 0
        before = health.total
        rocket = self.launch_graviton(run, caster, angles, guard)
        for _ in range(145):
            run.step({genji: (0, 0, 0), sigma: (-32768, 0, 0)})
        self.assertFalse(fight.rockets)
        self.assertFalse(fight.zarya_gravitons.fields)
        self.assertIs(rocket.shooter, genji)
        self.assertEqual(genji_graph.vars[6584].value(), 5)
        self.assertEqual(grasp.vars[21312].value(), 5)
        self.assertEqual(grasp.vars[41].value().id, genji.body)
        self.assertNotIn(target.entity, fight.zarya_gravitons.pulled)
        self.assertEqual(health.total, before)
        self.assertEqual(caster.body_script.component.entity_var(ULT_CHARGE).value(), 0)
        sigma_health = fight.vitals_of(fight.player_target(sigma))
        self.assertAlmostEqual(sigma_health.temporary_shields[sigma_grasp.SHIELD_TAG].now, 3, places=5)

    def test_actual_graviton_field_pulls_each_form_and_expires(self):
        for pilot_form in (False, True):
            with self.subTest(pilot_form=pilot_form):
                run, pilot, _, target, health, _, rocket = self.graviton_scene(
                    pilot_form=pilot_form, miss=True,
                )
                fight = run.match.combat
                before = health.total
                field = self.until_graviton(run)
                self.assertNotIn(rocket, fight.rockets)
                self.assertEqual(field.radius, 6)
                self.assertEqual(field.end_tick - field.last_tick, 250)
                mover = pilot.mover if pilot_form else target.owner.control_mover
                start = mover.position[0]
                # Run the world without player commands to exercise authoritative pull.
                run.frame += 30
                run.match.update(run.frame * combat.TICK, run.frame)
                self.assertIn(target.entity, fight.zarya_gravitons.pulled)
                self.assertGreater(mover.position[0], start + .5)
                self.assertAlmostEqual(before - health.total, 2.4, places=5)
                if not pilot_form:
                    matrix = next(i for i in target.owner.body_script.component.instances.values()
                                  if i.graph.index == 0x02CC)
                    before_matrix = health.total
                    for _ in range(4):
                        run.step({pilot: (0, 0, 2)})
                    self.assertTrue(matrix.state(3).active)
                    self.assertIn(field, fight.zarya_gravitons.fields)
                    self.assertAlmostEqual(before_matrix - health.total, .32, places=5)
                    for _ in range(45):
                        run.step({})
                    self.assertFalse(matrix.state(3).active)
                    for _ in range(8):
                        run.step({pilot: (0, 0, 8)})
                    boosters = next(i for i in target.owner.body_script.component.instances.values()
                                    if i.graph.index == 0x047E)
                    self.assertFalse(boosters.state(2).active)
                with patch.object(fight, "_shoot", wraps=fight._shoot) as shoot:
                    for _ in range(8):
                        run.frame += 1
                        run.match.update(run.frame * combat.TICK, run.frame)
                        position = tuple(mover.position)
                        pilot.take_commands([Command(run.frame, buttons=FIRE, forward=127)])
                        self.assertEqual(tuple(mover.position), position)
                self.assertTrue(any(call.args[0] is target.owner for call in shoot.call_args_list))
                for _ in range(8):
                    run.frame = min(run.frame + 30, field.end_tick)
                    run.match.update(run.frame * combat.TICK, run.frame)
                self.assertFalse(fight.zarya_gravitons.fields)
                self.assertFalse(fight.zarya_gravitons.pulled)
                self.assertFalse(mover.driven)
                self.assertEqual(mover.velocity, (0, 0, 0))
                self.assertAlmostEqual(before - health.total, 20, places=5)

    def test_actual_graviton_release_filters_delayed_movement_only(self):
        for pilot_form, early_release in ((False, False), (True, True)):
            with self.subTest(pilot_form=pilot_form, early_release=early_release):
                run, pilot, caster, target, _, _, _ = self.graviton_scene(
                    pilot_form=pilot_form, miss=True,
                )
                match, owner = run.match, target.owner
                fight = match.combat
                host = fight.zarya_gravitons
                mover = pilot.mover if pilot_form else owner.control_mover
                field = self.until_graviton(run)
                start = run.frame + 1
                release = start + 4 if early_release else field.end_tick
                while run.frame < release - 1:
                    run.frame = min(run.frame + 30, release - 1)
                    match.update(run.frame * combat.TICK, run.frame)
                self.assertIn(target.entity, host.pulled)
                if early_release:
                    match.leave(caster)
                run.frame = release
                match.update(run.frame * combat.TICK, run.frame)
                self.assertFalse(host.fields)
                self.assertNotIn(target.entity, host.pulled)
                ability = 0 if pilot_form else 8
                with patch.object(fight, "_shoot", wraps=fight._shoot) as shoot:
                    pilot.take_commands([Command(release - 2, buttons=FIRE | JUMP | ability,
                                                 forward=127, right=127, yaw=16384)])
                self.assertTrue(any(call.args[0] is owner for call in shoot.call_args_list))
                self.assertEqual(mover.throttles, (0, 0))
                self.assertEqual(mover.last_command.buttons, FIRE)
                self.assertEqual(mover.yaw, 16384)
                if pilot_form:
                    pilot.take_commands([Command(release, forward=127)])
                    self.assertNotEqual(mover.throttles, (0, 0))
                    continue
                graphs = {i.graph.index: i for i in owner.body_script.component.instances.values()}
                boosters, matrix = graphs[0x047E], graphs[0x02CC]
                self.assertEqual(boosters.state(2).counter, 0)
                pilot.take_commands([Command(release - 1, buttons=SECONDARY | ability)])
                self.assertTrue(matrix.state(3).active)
                self.assertEqual(mover.last_command.buttons, SECONDARY)
                self.assertEqual(boosters.state(2).counter, 0)
                pilot.take_commands([Command(release, buttons=ability)])
                self.assertTrue(boosters.state(2).active)
                self.assertEqual(boosters.state(2).counter, 1)

    def test_actual_overlapping_gravitons_release_movement_after_last_field(self):
        run, pilot, caster, target, _, _, _ = self.graviton_scene(miss=True, with_zarya=True)
        match, owner = run.match, target.owner
        fight = match.combat
        host = fight.zarya_gravitons
        first = self.until_graviton(run)
        run.step({})
        self.assertIn(target.entity, host.pulled)
        other = run.players[3]
        other.team = caster.team
        self.assertTrue(match.switch_hero(other, find_hero("zarya")))
        x, y, z = owner.position
        other.mover.position[:] = (x, y, z + 8)
        other.mover.body = None
        for _ in range(30):
            run.step({})
        angles = aim(fight._eye(other), (x + 2, y + .85, z))
        self.launch_graviton(run, other, angles, {})
        for _ in range(90):
            run.step({})
            if len(host.fields) == 2:
                break
        self.assertEqual(len(host.fields), 2)
        self.assertIs(host.fields[0], first)
        second = host.fields[1]
        match.leave(caster)
        run.frame += 1
        match.update(run.frame * combat.TICK, run.frame)
        pilot.take_commands([Command(run.frame, buttons=8)])
        self.assertEqual(host.fields, [second])
        self.assertIn(target.entity, host.pulled)
        mover = owner.control_mover
        self.assertEqual(mover.last_command.buttons, 0)
        lock, = (lock for lock in fight.input_locks[target.entity] if lock.effect == graviton.INPUT_LOCK)
        self.assertIsNone(lock.end)
        run.frame += 2
        match.update(run.frame * combat.TICK, run.frame)
        match.leave(other)
        run.frame += 1
        match.update(run.frame * combat.TICK, run.frame)
        self.assertFalse(host.fields)
        self.assertEqual(lock.end, run.frame)
        boosters = next(i for i in owner.body_script.component.instances.values() if i.graph.index == 0x047E)
        pilot.take_commands([Command(run.frame - 1, buttons=8)])
        self.assertEqual(boosters.state(2).counter, 0)
        pilot.take_commands([Command(run.frame, buttons=8)])
        self.assertEqual(boosters.state(2).counter, 1)

    def test_actual_graviton_direct_and_caster_replacement_keep_field_source(self):
        run, _, caster, _, health, _, rocket = self.graviton_scene(pilot_form=True)
        fight = run.match.combat
        before = health.total
        field = self.until_graviton(run)
        self.assertEqual(before - health.total, 5)
        self.assertEqual(field.source_entity, caster.body)
        self.assertEqual(field.source_entity, rocket.source_entity)
        self.assertTrue(run.match.switch_hero(caster, find_hero("ana")))
        self.assertNotEqual(caster.body, field.source_entity)
        health.damage(health.total - .05)
        run.step({})
        self.assertTrue(health.dead)
        self.assertEqual(health.killer, field.source_entity)
        self.assertIn(field, fight.zarya_gravitons.fields)

    def test_actual_graviton_detached_bomb_releases_old_body_and_reacquires_pilot(self):
        run, pilot, _, target, health, _, _ = self.graviton_scene(miss=True)
        fight = run.match.combat
        host = fight.zarya_gravitons
        field = self.until_graviton(run)
        mech = target.owner
        component = mech.body_script.component
        component.set(ULT_CHARGE, component.entity_var(ULT_COST).value())
        for _ in range(8):
            run.step({})
        self.assertIn(mech.entity, host.pulled)
        self.assertGreater(mech.velocity[0], 1)
        run.step({pilot: (0, 0, 32)})
        run.step({})
        self.assertTrue(mech.detached_bomb)
        self.assertIsNone(pilot.controlled_mech)
        self.assertIsNone(mech.impulse_mover)
        self.assertEqual(mech.velocity, (0, 0, 0))
        self.assertNotIn(mech.entity, host.pulled)
        self.assertNotIn(mech.entity, host.npc_movers)
        bomb_health, bomb_position = health.total, mech.position
        pilot_target = fight.player_target(pilot)
        pilot_health = fight.vitals_of(pilot_target)
        before = pilot_health.total
        run.step({})
        self.assertIn(pilot.body, host.pulled)
        self.assertAlmostEqual(before - pilot_health.total, .08, places=6)
        self.assertEqual(health.total, bomb_health)
        self.assertEqual(mech.position, bomb_position)

        charge = pilot.body_script.component.entity_var(ULT_CHARGE)
        cost = pilot.body_script.component.entity_var(ULT_COST).value()
        while charge.value() < cost and run.frame < field.end_tick:
            run.step({})
        self.assertEqual(charge.value(), cost)
        self.assertIn(field, host.fields)
        run.step({pilot: (0, 0, 32)})
        self.assertEqual(charge.value(), 0)
        for _ in range(160):
            run.step({})
            if pilot.controlled_mech is not None:
                break
        replacement = pilot.controlled_mech
        self.assertIsNotNone(replacement)
        self.assertNotEqual(replacement.entity, mech.entity)
        self.assertNotIn(pilot.body, host.pulled)
        self.assertNotIn(mech, run.match.bots)
        self.assertNotIn(replacement.entity, host.npc_movers)

    def test_actual_graviton_lethal_tick_credits_caster_and_reacquires_ejected_pilot(self):
        run, pilot, caster, target, _, _, _ = self.graviton_scene(miss=True)
        fight = run.match.combat
        host = fight.zarya_gravitons
        self.until_graviton(run)
        mech = target.owner
        for _ in range(8):
            run.step({})
        self.assertIn(mech.entity, host.pulled)
        health = combat.Vitals(mech.entity, .05)
        fight.vitals[mech.entity] = health
        run.step({})
        self.assertTrue(mech.dead)
        self.assertEqual(health.killer, caster.body)
        self.assertNotIn(mech.entity, host.pulled)
        self.assertIsNone(pilot.controlled_mech)
        pilot_health = fight.vitals_of(fight.player_target(pilot))
        self.assertEqual(pilot_health.total, 150)
        run.step({})
        self.assertIn(pilot.body, host.pulled)
        self.assertAlmostEqual(pilot_health.total, 149.92, places=6)

    def test_actual_graviton_field_filters_radius_static_cover_and_teams(self):
        from test_game_mover import box

        from ow174.game.collision import World

        for pilot_form in (False, True):
            with self.subTest(pilot_form=pilot_form):
                run, pilot, caster, target, health, _, _ = self.graviton_scene(
                    pilot_form=pilot_form, miss=True,
                )
                fight = run.match.combat
                field = self.until_graviton(run)
                x, y, z = field.position
                floor = box(x - 20, x + 20, y - 1.25, y - .25, z - 20, z + 20)
                wall = box(x + 1.4, x + 1.6, y - .25, y + 3, z - 3, z + 3)
                actor = target.owner
                mover = pilot.mover if pilot_form else actor.control_mover
                base_team = actor.team
                for case in ("radius", "cover", "ally", "enemy"):
                    with self.subTest(case=case):
                        run.match.collision_world = World(floor + wall if case == "cover" else floor)
                        mover.use(world=run.match.collision_world)
                        offset = field.radius + target.radius + .25 if case == "radius" else 3
                        position = (x + offset, y - .25, z)
                        mover.position[:] = position
                        mover.body = None
                        if not pilot_form:
                            actor.position = position
                        actor.team = caster.team if case == "ally" else base_team
                        before = health.total
                        run.step({})
                        admitted = case == "enemy"
                        self.assertEqual(target.entity in fight.zarya_gravitons.pulled, admitted)
                        self.assertAlmostEqual(before - health.total, .08 if admitted else 0, places=6)

    def test_actual_call_mech_entry_immunity_releases_live_graviton_pull(self):
        run, pilot, _, target, health, _, _ = self.graviton_scene(pilot_form=True, miss=True)
        fight = run.match.combat
        host = fight.zarya_gravitons
        field = self.until_graviton(run)
        run.step({})
        self.assertIn(target.entity, host.pulled)
        script = pilot.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        run.step({pilot: (0, 0, 32)})
        self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)
        for _ in range(100):
            if defenses.protection(pilot) == "immune":
                break
            run.step({})
        self.assertEqual(defenses.protection(pilot), "immune")
        self.assertIn(field, host.fields)
        self.assertIn(target.entity, host.pulled)
        before = health.total
        start = tuple(pilot.mover.position)
        run.step({})
        self.assertNotIn(target.entity, host.pulled)
        for _ in range(60):
            if pilot.controlled_mech is not None:
                break
            self.assertEqual(health.total, before)
            run.step({})
        replacement = pilot.controlled_mech
        self.assertIsNotNone(replacement)
        self.assertNotEqual(tuple(pilot.mover.position), start)
        self.assertEqual(health.total, before)
        self.assertIn(field, host.fields)
        self.assertNotIn(target.entity, host.pulled)
        body = fight.vitals_of(fight.player_target(pilot))
        before = body.total
        run.step({})
        self.assertIn(replacement.entity, host.pulled)
        self.assertAlmostEqual(before - body.total, .08, places=6)

    def test_actual_graviton_wakes_either_form_only_after_projected_barrier_ends(self):
        for pilot_form in (False, True):
            for expiry in (False, True):
                with self.subTest(pilot_form=pilot_form, expiry=expiry):
                    self.check_graviton_sleep_barrier(pilot_form, expiry)

    def check_graviton_sleep_barrier(self, pilot_form, expiry):
        run, pilot, caster, target, health, _, _ = self.graviton_scene(
            pilot_form=pilot_form, miss=True, with_zarya=True,
        )
        match, fight = run.match, run.match.combat
        host = fight.zarya_gravitons
        field = self.until_graviton(run)
        ana, projector = run.players[2:]
        ana.spawned = True
        ana.team = caster.team
        projector.team = pilot.team
        self.assertTrue(match.switch_hero(ana, find_hero("ana")))
        self.assertTrue(match.switch_hero(projector, find_hero("zarya")))
        x, _, z = field.position
        for player, offset in ((ana, -7), (projector, 7)):
            player.mover.position[:] = (x + offset, target.feet[1], z)
            player.mover.body = None
        for _ in range(85):
            run.step({})
        target = fight.player_target(pilot)
        sleep = sleep_tests.DvaSleepTests().sleep(run, ana, target)
        ally_angles = aim(fight._eye(projector), fight._eye(target.owner))
        run.step({projector: (*ally_angles, 16)})
        bubble = fight.zarya_bubbles.for_target(target.owner)
        self.assertIsNotNone(bubble)
        self.assertEqual(bubble.health, 200)
        self.assertEqual(fight.ana_sleeps[target.entity], sleep)
        before = health.total
        for _ in range(32):
            run.step({})
        self.assertIn(field, host.fields)
        self.assertIn(target.entity, host.pulled)
        self.assertAlmostEqual(bubble.health, 200 - 32 * .08, places=6)
        self.assertEqual(health.total, before)
        self.assertEqual(fight.ana_sleeps[target.entity], sleep)
        if expiry:
            for _ in range(125):
                run.step({})
                if fight.zarya_bubbles.for_target(target.owner) is None:
                    break
                self.assertEqual(health.total, before)
                self.assertEqual(fight.ana_sleeps[target.entity], sleep)
            self.assertGreater(bubble.health, 0)
        else:
            self.assertTrue(match.switch_hero(caster, find_hero("soldier")))
            self.assertNotEqual(caster.body, field.source_entity)
            bubble.health = .04
            with patch.object(zarya_barriers.runtime, "send_message",
                              wraps=zarya_barriers.runtime.send_message) as send:
                run.step({})
            energy = [call for call in send.call_args_list if call.args[1] == 0x0240000000000146]
            self.assertEqual(len(energy), 1)
            self.assertEqual(energy[0].args[2][0x12].id, field.source_entity)
        self.assertIsNone(fight.zarya_bubbles.for_target(target.owner))
        self.assertEqual(health.total, before)
        self.assertEqual(fight.ana_sleeps[target.entity], sleep)
        self.assertIn(field, host.fields)
        run.step({})
        wake = run.frame + 32
        self.assertEqual(fight.ana_sleeps[target.entity], (sleep[0], wake, True))
        self.assertAlmostEqual(before - health.total, .08, places=6)
        for _ in range(31):
            run.step({})
            self.assertEqual(fight.ana_sleeps[target.entity][1], wake)
        self.assertTrue(fight.is_stunned(target.owner))
        run.step({})
        self.assertFalse(fight.is_stunned(target.owner))
        self.assertEqual(target.entity in host.pulled, run.frame < field.end_tick)

    def test_actual_graviton_stops_active_boosters_through_its_cooldown(self):
        run, pilot, _, target, _, _, _ = self.graviton_scene(miss=True)
        fight = run.match.combat
        host = fight.zarya_gravitons
        mech = target.owner
        graph = next(i for i in mech.body_script.component.instances.values() if i.graph.index == 0x047E)
        ability, flight, post_end = (graph.state(index) for index in (0, 2, 5))
        boost = {pilot: (16384, 0, 8)}
        for _ in range(8):
            run.step({})
        run.step(boost)
        self.assertTrue(flight.active)
        for _ in range(80):
            run.step(boost)
            if mech.entity in host.pulled:
                break
        self.assertIn(mech.entity, host.pulled)
        self.assertFalse(flight.active)
        self.assertTrue(post_end.active)
        self.assertFalse(fight.is_stunned(mech))
        self.assertEqual(ability.remaining(), 0)
        cooldown_start = post_end.start + 300
        for _ in range(24):
            run.step({})
        self.assertEqual(ability.last, cooldown_start)
        self.assertEqual(ability.cur, 4)
        while host.fields:
            run.step({})
        run.step(boost)
        self.assertEqual(flight.counter, 1)
        run.step({})
        while ability.remaining() > 0:
            run.step({})
        run.step(boost)
        self.assertEqual(flight.counter, 2)
        self.assertTrue(flight.active)

    def graviton_npc_scene(self, pilot_form, name, *, wall=False):
        from test_game_mover import box

        from ow174.game.collision import World

        fixture = manual_tests.ManualIncomingTests
        fixture.setUpClass()
        run, pilot, caster, other, target, health = ana_tests.DvaAnaTests().scene(pilot_form=pilot_form)
        match, fight = run.match, run.match.combat
        other.spawned = False
        caster.team = pilot.team
        self.assertTrue(match.switch_hero(caster, find_hero("zarya")))
        health.heal(350)
        x, y, z = target.feet
        triangles = box(x - 20, x + 20, y - 1, y, z - 20, z + 20)
        if wall:
            triangles += box(x - 10, x + 10, y, y + 7, z + 6.5, z + 6.7)
        match.collision_world = World(triangles)
        position = (x + 2, y, z - 2) if name == "Assassin" else (x + 5, y, z + 10)
        caster.mover.position[:] = position
        caster.mover.body = None
        for _ in range(30):
            run.step({})
        distance = 2 if name == "Assassin" else 10
        bot = npc.spawn(match, fixture.by_name[name], (x, y, z + distance), team=1)
        return run, pilot, caster, fight.player_target(pilot), health, bot

    def test_actual_graviton_releases_assassin_only_after_separating_either_form(self):
        for pilot_form in (False, True):
            for separates in (False, True):
                with self.subTest(pilot_form=pilot_form, separates=separates):
                    self.check_graviton_assassin(pilot_form, separates)

    def check_graviton_assassin(self, pilot_form, separates):
        run, pilot, caster, target, health, bot = self.graviton_npc_scene(
            pilot_form, "Assassin", wall=separates,
        )
        fight = run.match.combat
        x, y, z = target.feet
        npc_control.engage(run.match, bot, pilot)
        run.step({})
        self.assertEqual(fight.pins[bot.entity], target.entity)
        self.assertTrue(fight.is_stunned(target.owner))
        before = fight.vitals[bot.entity].total
        victim_before = health.total
        point = (x + 2, y + 1.3, z + 6.5) if separates else (x, y + 1.1, z + 2)
        self.launch_graviton(run, caster, aim(fight._eye(caster), point), {})
        field = self.until_graviton(run)
        for _ in range(65):
            run.step({})
            if bot.entity not in fight.pins:
                break
        self.assertFalse(bot.dead)
        self.assertLess(fight.vitals[bot.entity].total, before)
        self.assertIn(field, fight.zarya_gravitons.fields)
        self.assertIn(bot.entity, fight.zarya_gravitons.pulled)
        self.assertNotIn(target.entity, fight.zarya_gravitons.pulled)
        if separates:
            self.assertNotIn(bot.entity, fight.pins)
            self.assertIsNone(assassin.contact_sample(fight, bot, fight.player_target(pilot)))
            self.assertIsNone(bot.body_script.component.world.manual_target)
            self.assertNotIn(pilot.entity, fight.assassin_effects)
            self.assertFalse(fight.is_stunned(target.owner))
            after = health.total
            with patch.object(fight, "_shoot", wraps=fight._shoot) as shoot:
                for _ in range(12):
                    run.step({pilot: (0, 0, FIRE)})
            self.assertTrue(any(call.args[0] is target.owner for call in shoot.call_args_list))
            self.assertEqual(health.total, after)
        else:
            self.assertEqual(fight.pins[bot.entity], target.entity)
            self.assertTrue(fight.is_stunned(target.owner))
            self.assertLess(health.total, victim_before)

    def test_actual_graviton_blocks_heavy_charge_against_either_form(self):
        for pilot_form in (False, True):
            for stage in ("idle", "windup", "motion"):
                with self.subTest(pilot_form=pilot_form, stage=stage):
                    self.check_graviton_heavy(pilot_form, stage)

    def check_graviton_heavy(self, pilot_form, stage):
        run, pilot, caster, target, health, bot = self.graviton_npc_scene(pilot_form, "Heavy Assault")
        fight = run.match.combat
        host = fight.zarya_gravitons
        instance = npc_charge.instance(bot)
        initial = health.total
        if stage == "motion":
            npc_control.engage(run.match, bot, pilot, charge=True)
            for _ in range(70):
                run.step({})
                if bot.charge_mover is not None:
                    break
            self.assertIsNotNone(bot.charge_mover)
            self.assertLess(bot.velocity[2], -10)
        x, y, z = target.feet
        aim_point = (x + 1.4, y + .05, z + 10)
        self.launch_graviton(run, caster, aim(fight._eye(caster), aim_point), {})
        if stage == "windup":
            landed = host.landed

            def engage_at_landing(*args):
                landed(*args)
                npc_control.engage(run.match, bot, pilot, charge=True)

            # The landing tick precedes the field's first active interval.
            with patch.object(host, "landed", side_effect=engage_at_landing):
                field = self.until_graviton(run)
            self.assertTrue(instance.state(71).active)
            self.assertIsNone(bot.charge_mover)
        else:
            field = self.until_graviton(run)
        run.step({})
        self.assertIn(bot.entity, host.pulled)
        self.assertIsNone(bot.charge_mover)
        ability = instance.state(41)
        motion = instance.state(33)
        self.assertFalse(motion.active)
        self.assertEqual(motion.counter, int(stage == "motion"))
        if stage != "idle":
            self.assertGreater(ability.remaining(), 14)
        else:
            self.assertEqual(ability.remaining(), 0)
            npc_control.engage(run.match, bot, pilot, charge=True)
        for _ in range(55):
            run.step({})
        self.assertEqual(health.total, initial)
        self.assertFalse(motion.active)
        self.assertEqual(motion.counter, int(stage == "motion"))
        self.assertEqual(instance.state(53).counter, 0)
        if stage == "idle":
            self.assertEqual(ability.remaining(), 0)
        while run.frame < field.end_tick:
            run.step({})
        self.assertNotIn(bot.entity, host.pulled)
        if stage == "idle":
            for _ in range(90):
                run.step({})
                if instance.state(53).counter:
                    break
            self.assertEqual(instance.state(53).counter, 1)
            self.assertEqual(initial - health.total, 150 if pilot_form else 170)
        else:
            self.assertGreater(ability.remaining(), 10)
            self.assertEqual(health.total, initial)
            self.assertEqual(instance.state(53).counter, 0)

    def test_actual_zarya_beam_bypasses_matrix_and_respects_range_cover_and_teams(self):
        for pilot_form, scenario in ((False, "matrix"), (True, "pilot"), (False, "range"),
                                     (False, "wall"), (False, "ally"), (False, "projected")):
            with self.subTest(pilot_form=pilot_form, scenario=scenario):
                self.check_zarya_beam(pilot_form, scenario)

    def check_zarya_beam(self, pilot_form, scenario):
        from test_game_mover import box

        from ow174.game.collision import World

        run, pilot, caster, soldier, target, health = ana_tests.DvaAnaTests().scene(
            pilot_form=pilot_form, with_zarya=scenario == "projected",
        )
        match, fight = run.match, run.match.combat
        soldier.spawned = False
        health.heal(350)
        self.assertTrue(match.switch_hero(caster, find_hero("zarya")))
        x, y, z = target.feet
        distance = 17 if scenario == "range" else 6
        caster.mover.position[:] = (x, y, z + distance)
        caster.mover.body = None
        if scenario == "ally":
            caster.team = pilot.team
        if scenario == "wall":
            match.collision_world = World(box(x - 20, x + 20, y - 1, y, z - 20, z + 20)
                                          + box(x - 3, x + 3, y, y + 3, z + 2.9, z + 3.1))
        protector = None
        if scenario == "projected":
            protector = run.players[3]
            protector.team = pilot.team
            self.assertTrue(match.switch_hero(protector, find_hero("zarya")))
            protector.mover.position[:] = (x - 4, y, z)
            protector.mover.body = None
        for _ in range(30):
            run.step({})
        angles = aim(fight._eye(caster), (x, y + 1, z))
        barrier = None
        if protector is not None:
            guard_angles = aim(fight._eye(protector), (x, y + 1, z))
            run.step({protector: (*guard_angles, 16)})
            barrier = fight.zarya_bubbles.for_target(target.owner)
            self.assertIsNotNone(barrier)
            self.assertEqual(barrier.health, 200)
        matrix = None
        if scenario == "matrix":
            matrix = next(i for i in target.owner.body_script.component.instances.values()
                          if i.graph.index == 0x02CC)
            run.step({pilot: (0, 0, 2)})
            self.assertTrue(matrix.state(3).active)
        before = health.total
        commands = {caster: (*angles, 1)}
        if matrix is not None:
            commands[pilot] = (0, 0, 2)
        for _ in range(30):
            run.step(commands)
        beam = next(i for i in caster.body_script.component.instances.values() if i.graph.index == 0x0241)
        state = beam.state(10)
        self.assertTrue(state.active)
        _, last = fight.beam_seen[caster.body]
        raw = 75 * (last - state.start) / 1000
        self.assertGreater(raw, 0)
        loss = raw * .7 if scenario == "matrix" else raw if scenario == "pilot" else 0
        self.assertAlmostEqual(before - health.total, loss, places=5)
        self.assertFalse(fight.rockets)
        if matrix is not None:
            self.assertTrue(matrix.state(3).active)
        if barrier is not None:
            self.assertAlmostEqual(barrier.health, 200 - raw, places=5)
        after = health.total
        fight.command(caster, caster.mover.last_command)
        self.assertEqual(health.total, after)
        if barrier is not None:
            self.assertAlmostEqual(barrier.health, 200 - raw, places=5)
        run.step({})
        self.assertFalse(state.active)
        self.assertEqual(health.total, after)

    def test_held_zarya_beam_cannot_hit_pilot_before_its_exposure_tick(self):
        run, pilot, caster, _, mech = created_tests.CreatedBodyTests().scenario()
        match, fight = run.match, run.match.combat
        self.assertIs(pilot.controlled_mech, mech)
        self.assertTrue(match.switch_hero(caster, find_hero("zarya")))
        x, y, z = mech.position
        caster.mover.position[:] = (x, y, z + 6)
        caster.mover.body = None
        for _ in range(100):
            run.step({})
        angles = aim(fight._eye(caster), (x, y + 1, z))
        mech_health = fight.vitals[mech.entity] = combat.Vitals(mech.entity, .5)
        for _ in range(30):
            run.step({caster: (*angles, FIRE)})
            if mech_health.dead:
                break
        self.assertTrue(mech_health.dead)
        self.assertEqual(mech_health.killer, caster.body)
        self.assertEqual(mech_health.death_tick, run.frame)
        self.assertIs(pilot.controlled_mech, mech)
        beam = next(i for i in caster.body_script.component.instances.values() if i.graph.index == 0x0241)
        self.assertTrue(beam.state(10).active)
        run.frame += 1
        match.update(run.frame * combat.TICK, run.frame, frames=False)
        self.assertIsNone(pilot.controlled_mech)
        target = fight.player_target(pilot)
        self.assertIs(target.owner, pilot)
        exposed = fight.replacement_ticks[pilot.body]
        self.assertEqual(exposed, run.frame)
        health = fight.vitals_of(target)
        self.assertEqual(health.total, 150)

        for view_before_exposure in (True, False):
            if not view_before_exposure:
                run.frame += 1
                match.update(run.frame * combat.TICK, run.frame, frames=False)
            delay = (run.frame - exposed) * 16 + int(view_before_exposure)
            command = Command(run.frame, yaw=angles[0], pitch=angles[1], buttons=FIRE, extra=delay)
            before = health.total
            caster.take_commands([command])
            self.assertAlmostEqual(before - health.total, 0 if view_before_exposure else 1.2)
        self.assertTrue(beam.state(10).active)

    def test_control_stops_zarya_beam_without_replaying_suppressed_input_on_recovery(self):
        for pilot_form in (False, True):
            for flashbang in (False, True):
                with self.subTest(pilot_form=pilot_form, flashbang=flashbang):
                    self.check_zarya_beam_control(pilot_form, flashbang)

    def check_zarya_beam_control(self, pilot_form, flashbang):
        run, pilot, interrupter, caster, target, health = ana_tests.DvaAnaTests().scene(pilot_form=pilot_form)
        match, fight = run.match, run.match.combat
        health.heal(350)
        caster.team = 1
        interrupter.team = pilot.team
        self.assertTrue(match.switch_hero(caster, find_hero("zarya")))
        if flashbang:
            self.assertTrue(match.switch_hero(interrupter, heroes()[0x02E0000000000042]))
        x, y, z = target.feet
        for actor, distance in ((caster, 6), (interrupter, 8.5)):
            actor.mover.position[:] = (x, y, z + distance)
            actor.mover.body = None
        for _ in range(30):
            run.step({})
        caster_angles = aim(fight._eye(caster), (x, y + 1, z))
        interrupter_angles = aim(fight._eye(interrupter), (x, y + 1, z + 6))
        held = {caster: (*caster_angles, FIRE)}
        before = health.total
        for _ in range(30):
            run.step(held)
        self.assertLess(health.total, before)
        beam = next(i for i in caster.body_script.component.instances.values() if i.graph.index == 0x0241)
        state = beam.state(10)
        self.assertTrue(state.active)
        old_activation, _ = fight.beam_seen[caster.body]

        run.step({**held, interrupter: (*interrupter_angles, 16 if flashbang else 8)})
        for _ in range(60):
            if fight.is_stunned(caster):
                break
            run.step({**held, interrupter: (*interrupter_angles, 0)})
        self.assertTrue(fight.is_stunned(caster))
        self.assertFalse(state.active)
        stopped_health = health.total
        stopped_clock = fight.beam_seen[caster.body]
        for _ in range(5):
            run.step(held)
        self.assertEqual(health.total, stopped_health)
        self.assertEqual(fight.beam_seen[caster.body], stopped_clock)
        deadline = fight.stuns[caster.body] if flashbang else fight.ana_sleeps[caster.body][1]
        run.frame = deadline - 2
        run.step({})
        self.assertTrue(fight.is_stunned(caster))
        run.step({})
        self.assertFalse(fight.is_stunned(caster))
        self.assertFalse(state.active)
        self.assertEqual(health.total, stopped_health)
        self.assertEqual(fight.beam_seen[caster.body], stopped_clock)

        for _ in range(30):
            run.step(held)
        activation, consumed = fight.beam_seen[caster.body]
        self.assertNotEqual(activation, old_activation)
        self.assertTrue(state.active)
        raw = 75 * (consumed - state.start) / 1000
        self.assertGreater(raw, 0)
        self.assertAlmostEqual(stopped_health - health.total, raw if pilot_form else raw * .7)

    def test_talon_volley_break_spends_only_the_intercepted_packets_on_either_form(self):
        fixture = manual_tests.ManualIncomingTests
        fixture.setUpClass()
        for name, packets, raw, weakened in (("Enforcer", 25, 2, 5), ("Heavy Assault", 2, 5, 4)):
            for pilot_form in (False, True):
                for barrier_health in (200, weakened):
                    with self.subTest(npc=name, pilot_form=pilot_form, barrier_health=barrier_health):
                        self.check_talon_volley_barrier(
                            fixture.by_name[name], pilot_form, barrier_health, packets, raw,
                        )

    def check_talon_volley_barrier(self, definition, pilot_form, barrier_health, packets, raw):
        run, bot, health, bubble = self.talon_barrier_scene(definition, pilot_form, distance=2.5)
        match = run.match
        fight = match.combat
        bubble.health = barrier_health
        before = health.total
        with patch.object(fight, "_shoot", wraps=fight._shoot) as shoot:
            for _ in range(90):
                run.step({})
                if shoot.called:
                    break
            self.assertEqual(shoot.call_count, 1)
            self.assertIs(shoot.call_args.args[0], bot)
            npc_control.stop(match)
            for _ in range(10):
                run.step({})
            self.assertEqual(shoot.call_count, 1)
        absorbed = min(packets, (barrier_health + raw - 1) // raw)
        self.assertEqual(bubble.health, max(0, barrier_health - packets * raw))
        self.assertIs(fight.zarya_bubbles.for_target(bubble.owner), bubble if bubble.health else None)
        self.assertEqual(before - health.total, (packets - absorbed) * raw / (1 if pilot_form else 2))

    def test_enforcer_falloff_is_measured_at_the_projected_barrier_on_both_forms(self):
        fixture = manual_tests.ManualIncomingTests
        fixture.setUpClass()
        for pilot_form in (False, True):
            for distance in (11, 18):
                with self.subTest(pilot_form=pilot_form, distance=distance):
                    run, bot, health, bubble = self.talon_barrier_scene(
                        fixture.by_name["Enforcer"], pilot_form, distance=distance,
                    )
                    match, fight = run.match, run.match.combat
                    for _ in range(90):
                        run.step({})
                        if fight.rockets:
                            break
                    self.assertEqual(len(fight.rockets), 25)
                    contacts = [
                        fight.zarya_bubbles.intercept(bot, rocket.position, rocket.direction, 100)
                        for rocket in fight.rockets
                    ]
                    contact_distances = [hit[0] for hit in contacts if hit is not None]
                    self.assertTrue(contact_distances)
                    if distance == 11:
                        self.assertTrue(all(8 < contact < 15 for contact in contact_distances))
                    else:
                        self.assertTrue(all(contact >= 15 for contact in contact_distances))
                    expected = [2 * (1 - .8 * min(1, (contact - 8) / 7)) for contact in contact_distances]
                    npc_control.stop(match)
                    before = health.total
                    barriers = fight.zarya_bubbles
                    with patch.object(barriers, "damage", wraps=barriers.damage) as damage:
                        for _ in range(18):
                            run.step({})
                    self.assertTrue(all(call.args[0] is bubble and call.args[2] is bot
                                        for call in damage.call_args_list))
                    actual = [call.args[1] for call in damage.call_args_list]
                    self.assertEqual(len(actual), len(expected))
                    for dealt, wanted in zip(sorted(actual), sorted(expected), strict=True):
                        self.assertAlmostEqual(dealt, wanted, places=6)
                    self.assertAlmostEqual(bubble.health, 200 - sum(expected), places=5)
                    self.assertEqual(health.total, before)

    def talon_barrier_scene(self, definition, pilot_form, *, distance):
        run, pilot, ana, soldier, target, health = ana_tests.DvaAnaTests().scene(
            pilot_form=pilot_form, with_zarya=True,
        )
        ana.spawned = soldier.spawned = False
        health.heal(350)
        match = run.match
        fight = match.combat
        zarya = run.players[3]
        zarya.team = pilot.team
        self.assertTrue(match.switch_hero(zarya, find_hero("zarya")))
        x, y, z = target.feet
        zarya.mover.position[:] = (x - 4, y, z)
        zarya.mover.body = None
        a, b = target.segment()
        centre = tuple((p + q) / 2 for p, q in zip(a, b, strict=True))
        angles = aim(fight._eye(zarya), centre)
        for _ in range(30):
            run.step({})
        bot = npc.spawn(match, definition, (x, y, z + distance), team=1)
        npc_control.engage(match, bot, pilot)
        run.step({zarya: (*angles, 16)})
        bubble = fight.zarya_bubbles.for_target(target.owner)
        self.assertIsNotNone(bubble)
        self.assertEqual(bubble.health, 200)
        return run, bot, health, bubble

    def test_actual_call_mech_breaks_either_zarya_barrier_without_overflow_or_knockback(self):
        for mode in ("personal", "projected", "unprotected"):
            with self.subTest(mode=mode):
                run, pilot, defender, instance, old = created_tests.CreatedBodyTests().ready_call_mech()
                match = run.match
                match.game_map = replace(match.game_map, free_for_all=False)
                defender.team = 1
                zarya = find_hero("zarya")
                self.assertTrue(match.switch_hero(defender, zarya))
                caster = defender
                if mode == "projected":
                    caster = match.add_player(3, "Zarya", zarya.guid, 1, False)
                    in_world(match, caster)
                    run.players.append(caster)
                    self.assertTrue(match.switch_hero(caster, zarya))
                run.step({pilot: (0, 0, 32)})
                for _ in range(24):
                    run.step({})
                mech, = match.bots
                self.assertIsNot(mech, old)
                x, y, z = mech.position
                defender.mover.position[:] = (x + 1.5, y, z)
                defender.mover.body = None
                fight = match.combat
                if mode == "projected":
                    caster.mover.position[:] = (x + 6, y, z)
                    caster.mover.body = None
                    yaw, pitch = aim(fight._eye(caster), (x + 1.5, y + 1, z))
                    run.step({caster: (yaw, pitch, 16)})
                elif mode == "personal":
                    run.step({caster: (0, 0, 8)})
                bubble = fight.zarya_bubbles.for_target(defender)
                if mode != "unprotected":
                    self.assertIsNotNone(bubble)
                    self.assertEqual(bubble.health, 200)
                health = fight.vitals_of(fight.player_target(defender))
                before = health.total
                for _ in range(45):
                    run.step({})
                    if instance.state(20).counter:
                        break
                self.assertEqual(instance.state(20).counter, 1)
                if mode == "unprotected":
                    self.assertEqual(before - health.total, 250)
                    self.assertGreater(defender.mover.velocity[0], 20)
                    self.assertGreater(defender.mover.velocity[1], 2)
                else:
                    self.assertEqual(bubble.health, 0)
                    self.assertIsNone(fight.zarya_bubbles.for_target(defender))
                    self.assertEqual(health.total, before)
                    self.assertEqual(tuple(defender.mover.velocity[0::2]), (0, 0))
                    self.assertLessEqual(defender.mover.velocity[1], 0)
                    for _ in range(10):
                        run.step({})
                    self.assertEqual(health.total, before)
                    self.assertFalse(pilot.client.messages(combat.HIT))

    def test_actual_projected_barrier_cleanses_antiheal_on_both_forms(self):
        for pilot_form in (False, True):
            with self.subTest(pilot_form=pilot_form):
                fixture = ana_tests.DvaAnaTests()
                scene = fixture.scene(pilot_form=pilot_form, with_zarya=True)
                run, pilot, ana, soldier, target, health = scene
                fight = run.match.combat
                zarya = run.players[3]
                zarya.team = pilot.team
                self.assertTrue(run.match.switch_hero(zarya, find_hero("zarya")))
                x, y, z = target.feet
                zarya.mover.position[:] = (x - 4, y, z)
                zarya.mover.body = None
                a, b = target.segment()
                centre = tuple((left + right) * 0.5 for left, right in zip(a, b, strict=True))
                yaw, pitch = aim(fight._eye(zarya), centre)
                for _ in range(20):
                    run.step({zarya: (yaw, pitch, 0)})
                fixture.grenade(run, ana, target)
                (_, expiry), = fight.heal_modifiers[target.entity]["anti"]
                before = health.total
                run.step({soldier: (0, 0, 16)})
                for _ in range(50):
                    run.step({zarya: (yaw, pitch, 0)})
                self.assertTrue(any(s.player is soldier and s.field_tick is not None
                                    for s in fight.projectiles.shots))
                self.assertEqual(health.total, before)
                run.step({zarya: (yaw, pitch, 16)})
                bubble = fight.zarya_bubbles.for_target(target.owner)
                self.assertIsNotNone(bubble)
                self.assertEqual(bubble.body, target.entity)
                self.assertEqual(bubble.health, 200)
                self.assertNotIn(target.entity, fight.heal_modifiers)
                run.step({zarya: (yaw, pitch, 0)})
                self.assertLess(run.frame, expiry)
                self.assertAlmostEqual(health.total - before, projectiles.HEAL_PER_SECOND * combat.TICK)

    def test_projected_barrier_control_contact_and_existing_deadlines_on_both_forms(self):
        for pilot_form in (False, True):
            for flashbang in (False, True):
                for timing in ("before", "break", "after"):
                    with self.subTest(pilot_form=pilot_form, flashbang=flashbang, timing=timing):
                        self.check_projected_barrier_control(pilot_form, flashbang, timing)

    def test_actual_projected_barrier_clears_mei_slow_and_freeze_on_both_forms(self):
        for pilot_form in (False, True):
            for frozen in (False, True):
                with self.subTest(pilot_form=pilot_form, frozen=frozen):
                    self.check_projected_frost_cleanse(pilot_form, frozen)

    def test_frost_and_flashbang_have_independent_deadlines_and_cleanse_on_both_forms(self):
        for pilot_form in (False, True):
            for cleanse in (False, True):
                with self.subTest(pilot_form=pilot_form, cleanse=cleanse):
                    self.check_overlapping_frost_and_stun(pilot_form, cleanse)

    def check_overlapping_frost_and_stun(self, pilot_form, cleanse):
        run, pilot, mei, cassidy, target, health = ana_tests.DvaAnaTests().scene(
            pilot_form=pilot_form, with_zarya=True,
        )
        fight = run.match.combat
        health.heal(350)
        zarya = run.players[3]
        cassidy.team = mei.team
        zarya.team = pilot.team
        for player, hero in ((mei, find_hero("mei")), (cassidy, heroes()[0x02E0000000000042]),
                             (zarya, find_hero("zarya"))):
            self.assertTrue(run.match.switch_hero(player, hero))
        mei.body_script.component.set(combat.FROST_MINIMUM, 1)
        x, y, z = target.feet
        for player, position in ((mei, (x, y, z + 4)), (cassidy, (x + 2.5, y, z)),
                                 (zarya, (x - 4, y, z))):
            player.mover.position[:] = position
            player.mover.body = None
        for _ in range(30):
            run.step({})
        point = (x, y + 1, z)
        effects = ((cassidy, 16, fight.stuns), (mei, FIRE, fight.frost))
        for shooter, button, status in (effects if cleanse else reversed(effects)):
            angles = aim(fight._eye(shooter), point)
            run.step({shooter: (*angles, button)})
            for _ in range(25):
                if target.entity in status:
                    break
                run.step({shooter: (*angles, 0)})
            self.assertIn(target.entity, status)
        flash_deadline = fight.stuns[target.entity]
        frost_deadline = fight.frost[target.entity].frozen_until
        self.assertLess(flash_deadline * 16, frost_deadline)
        self.assertTrue(fight.is_stunned(target.owner))

        if cleanse:
            guard_angles = aim(fight._eye(zarya), point)
            run.step({zarya: (*guard_angles, 16)})
            self.assertIsNotNone(fight.zarya_bubbles.for_target(target.owner))
            self.assertNotIn(target.entity, fight.frost)
            self.assertEqual(fight.stuns[target.entity], flash_deadline)
        aim_at_mei = aim(fight._eye(target.owner), (x, y + 1, z + 4))
        enemy_health = fight.vitals_of(fight.player_target(mei))
        before = enemy_health.total
        while run.frame < flash_deadline - 1:
            run.step({pilot: (*aim_at_mei, FIRE)})
        self.assertTrue(fight.is_stunned(target.owner))
        self.assertEqual(enemy_health.total, before)
        run.step({})
        self.assertNotIn(target.entity, fight.stuns)
        self.assertEqual(fight.is_stunned(target.owner), not cleanse)
        if not cleanse:
            self.assertEqual(fight.frost[target.entity].frozen_until, frost_deadline)
            while fight.tick * 16 < frost_deadline:
                run.step({})
            self.assertNotIn(target.entity, fight.frost)
        self.assertFalse(fight.is_stunned(target.owner))
        run.step({pilot: (*aim_at_mei, FIRE)})
        for _ in range(12):
            run.step({})
        self.assertLess(enemy_health.total, before)

    def check_projected_frost_cleanse(self, pilot_form, frozen):
        run, pilot, mei, soldier, target, health = ana_tests.DvaAnaTests().scene(
            pilot_form=pilot_form, with_zarya=True,
        )
        match, fight = run.match, run.match.combat
        soldier.spawned = False
        health.heal(350)
        self.assertTrue(match.switch_hero(mei, find_hero("mei")))
        zarya = run.players[3]
        zarya.team = pilot.team
        self.assertTrue(match.switch_hero(zarya, find_hero("zarya")))
        x, y, z = target.feet
        for player, position in ((mei, (x, y, z + 4)), (zarya, (x - 4, y, z))):
            player.mover.position[:] = position
            player.mover.body = None
        for _ in range(30):
            run.step({})
        aim_at = (x, y + 1, z)
        mei_angles = aim(fight._eye(mei), aim_at)
        zarya_angles = aim(fight._eye(zarya), aim_at)
        for _ in range(120):
            run.step({mei: (*mei_angles, FIRE)})
            if target.entity in fight.frost and fight.is_stunned(target.owner) == frozen:
                break
        self.assertIn(target.entity, fight.frost)
        self.assertEqual(fight.is_stunned(target.owner), frozen)
        run.step({zarya: (*zarya_angles, 16), mei: (*mei_angles, FIRE)})
        bubble = fight.zarya_bubbles.for_target(target.owner)
        self.assertIsNotNone(bubble)
        self.assertNotIn(target.entity, fight.frost)
        self.assertFalse(fight.is_stunned(target.owner))
        if frozen:
            locks = fight.input_locks[target.entity]
            lock = next(lock for lock in locks if lock.effect == combat.FROST_SPRAY)
            self.assertEqual(lock.end, run.frame)
        before = health.total
        for _ in range(12):
            run.step({mei: (*mei_angles, FIRE)})
        self.assertLess(bubble.health, 200)
        self.assertEqual(health.total, before)
        self.assertNotIn(target.entity, fight.frost)
        self.assertFalse(fight.is_stunned(target.owner))

    def check_projected_barrier_control(self, pilot_form, flashbang, timing):
        run, pilot, caster, soldier, target, health = ana_tests.DvaAnaTests().scene(
            pilot_form=pilot_form, with_zarya=True,
        )
        fight = run.match.combat
        soldier.spawned = False
        zarya = run.players[3]
        zarya.team = pilot.team
        self.assertTrue(run.match.switch_hero(zarya, find_hero("zarya")))
        x, y, z = target.feet
        zarya.mover.position[:] = (x - 4, y, z)
        zarya.mover.body = None
        if flashbang:
            self.assertTrue(run.match.switch_hero(caster, heroes()[0x02E0000000000042]))
        caster.mover.position[:] = (x, y, z + 2.5)
        caster.mover.body = None
        a, b = target.segment()
        centre = tuple((left + right) * .5 for left, right in zip(a, b, strict=True))
        ally_angles = aim(fight._eye(zarya), centre)
        for _ in range(30):
            run.step({})
        before = health.total
        if timing != "after":
            run.step({zarya: (*ally_angles, 16)})
            bubble = fight.zarya_bubbles.for_target(target.owner)
            self.assertIsNotNone(bubble)
            self.assertEqual(bubble.health, 200)
            if timing == "break":
                bubble.health = 1
        enemy_angles = aim(fight._eye(caster), (x, y + 1, z))
        run.step({caster: (*enemy_angles, 16 if flashbang else 8)})
        for _ in range(55):
            if any(rocket.shooter is caster for rocket in fight.rockets):
                break
            run.step({caster: (*enemy_angles, 0)})
        self.assertTrue(any(rocket.shooter is caster for rocket in fight.rockets))
        for _ in range(12):
            run.step({})
            if timing == "after" and fight.is_stunned(target.owner):
                break
        raw = 25 if flashbang else 5
        if timing != "after":
            self.assertEqual(bubble.health, 200 - raw if timing == "before" else 0)
            self.assertIs(fight.zarya_bubbles.for_target(target.owner), bubble if bubble.health else None)
            self.assertEqual(health.total, before)
            self.assertFalse(fight.is_stunned(target.owner))
            return

        self.assertEqual(health.total, before - raw)
        table = fight.stuns if flashbang else fight.ana_sleeps
        status = table[target.entity]
        run.step({zarya: (*ally_angles, 16)})
        bubble = fight.zarya_bubbles.for_target(target.owner)
        self.assertIsNotNone(bubble)
        self.assertEqual(bubble.health, 200)
        self.assertEqual(table[target.entity], status)
        self.assertTrue(fight.is_stunned(target.owner))
        deadline = status if flashbang else status[1]
        run.frame = deadline - 2
        run.step({})
        self.assertTrue(fight.is_stunned(target.owner))
        run.step({})
        self.assertFalse(fight.is_stunned(target.owner))
        self.assertEqual(health.total, before - raw)

    def test_actual_fusion_and_pilot_projectile_spend_personal_barrier(self):
        self.enterContext(patch.object(body_entities, "ULT_SECONDS", 0))
        for pilot_form, expected in ((False, 22), (True, 14)):
            with self.subTest(pilot_form=pilot_form):
                run, pilot, defender, _, target, _ = ana_tests.DvaAnaTests().scene(pilot_form=pilot_form)
                fight = run.match.combat
                pilot.body_script.ult_seconds = 0
                charge = target.owner.body_script.component.entity_var(ULT_CHARGE)
                initial_charge = charge.value()
                self.assertTrue(run.match.switch_hero(defender, find_hero("zarya")))
                x, y, z = target.feet
                defender.mover.position[:] = (x, y, z + 4)
                defender.mover.body = None
                for _ in range(30):
                    run.step({})
                run.step({defender: (-32768, 0, 8)})
                bubble = fight.zarya_bubbles.for_target(defender)
                self.assertIsNotNone(bubble)
                body = fight.vitals_of(fight.player_target(defender))
                before = body.total
                angles = aim(fight._eye(target.owner), (x, y + .9, z + 4))
                run.step({pilot: (*angles, 1)})
                for _ in range(25):
                    run.step({})
                self.assertEqual(bubble.health, 200 - expected)
                self.assertEqual(body.total, before)
                self.assertEqual(charge.value(), initial_charge)
                self.assertFalse(pilot.client.messages(combat.HIT))
                self.assertGreater(defender.body_script.component.entity_var(87).value(), 0)
                for _ in range(150):
                    run.step({})
                    if fight.zarya_bubbles.for_target(defender) is None:
                        break
                self.assertIsNone(fight.zarya_bubbles.for_target(defender))
                run.step({pilot: (*angles, 1)})
                for _ in range(25):
                    run.step({})
                self.assertEqual(before - body.total, expected)
                self.assertAlmostEqual(charge.value(), (initial_charge or 0) + expected, places=4)

    def test_boosters_contact_respects_personal_barrier_even_on_break(self):
        self.enterContext(patch.object(body_entities, "ULT_SECONDS", 0))
        for barrier_health in (None, 5, 200):
            with self.subTest(barrier_health=barrier_health):
                run, pilot, defender, _, source, _ = ana_tests.DvaAnaTests().scene()
                fight = run.match.combat
                self.assertTrue(run.match.switch_hero(defender, find_hero("zarya")))
                x, y, z = source.feet
                defender.mover.position[:] = (x, y, z + 2)
                defender.mover.body = None
                for _ in range(35):
                    run.step({})
                health = fight.vitals_of(fight.player_target(defender))
                before = health.total
                charge = source.owner.body_script.component.entity_var(ULT_CHARGE)
                initial_charge = charge.value() or 0
                if barrier_health is not None:
                    run.step({defender: (-32768, 0, 8)})
                    bubble = fight.zarya_bubbles.for_target(defender)
                    self.assertIsNotNone(bubble)
                    bubble.health = barrier_health
                run.step({pilot: (0, 0, 8)})
                for _ in range(35):
                    run.step({pilot: (0, 0, 8)})
                    if barrier_health == 5 and bubble.health == 0:
                        break
                if barrier_health is None:
                    self.assertEqual(before - health.total, 10)
                    self.assertGreater(defender.mover.position[2], z + 2)
                    self.assertEqual(charge.value() - initial_charge, 10)
                else:
                    self.assertEqual(bubble.health, max(0, barrier_health - 10))
                    active = fight.zarya_bubbles.for_target(defender)
                    self.assertIs(active, bubble if barrier_health > 10 else None)
                    self.assertEqual(health.total, before)
                    self.assertEqual(defender.mover.position[2], z + 2)
                    self.assertEqual((charge.value() or 0) - initial_charge, 0)
                    self.assertFalse(pilot.client.messages(combat.HIT))
                    if barrier_health == 5:
                        # Present the now exposed target to the same active Boosters again.
                        mech = pilot.controlled_mech
                        activation = fight.booster_contacts[mech.entity][:2]
                        x, y, z = mech.position
                        defender.mover.position[:] = (x, y, z + 1.5)
                        defender.mover.body = None
                        for _ in range(20):
                            run.step({pilot: (0, 0, 8)})
                        self.assertGreater(mech.position[2], z + 1.5)
                        self.assertEqual(fight.booster_contacts[mech.entity][:2], activation)
                        self.assertEqual(health.total, before)
                        self.assertEqual(defender.mover.position[2], z + 1.5)
                        self.assertEqual(charge.value() or 0, initial_charge)
                        ability = next(i.state(0) for i in mech.body_script.component.instances.values()
                                       if i.graph.index == 0x047E)
                        for _ in range(700):
                            run.step({})
                            if not ability.flags & ability.RUNNING and ability.remaining() == 0:
                                break
                        self.assertFalse(ability.flags & ability.RUNNING)
                        self.assertEqual(ability.remaining(), 0)
                        self.assertNotIn(mech.entity, fight.booster_contacts)
                        x, y, z = mech.position
                        defender.mover.position[:] = (x, y, z + 2)
                        defender.mover.body = None
                        for _ in range(30):
                            run.step({pilot: (0, 0, 8)})
                            if health.total < before:
                                break
                        self.assertNotEqual(fight.booster_contacts[mech.entity][:2], activation)
                        self.assertEqual(before - health.total, 10)
                        self.assertGreater(defender.mover.velocity[2], 0)
                        self.assertEqual(charge.value() - initial_charge, 10)

    def test_micro_missile_that_breaks_personal_barrier_does_not_damage_body(self):
        run, pilot, defender, _, source, _ = ana_tests.DvaAnaTests().scene()
        fight = run.match.combat
        self.assertTrue(run.match.switch_hero(defender, find_hero("zarya")))
        x, y, z = source.feet
        defender.mover.position[:] = (x, y, z + 4)
        defender.mover.body = None
        self.enterContext(patch.object(body_entities, "ULT_SECONDS", 0))
        charge = source.owner.body_script.component.entity_var(ULT_CHARGE)
        initial_charge = charge.value()
        for _ in range(30):
            run.step({})
        run.step({defender: (-32768, 0, 8)})
        bubble = fight.zarya_bubbles.for_target(defender)
        self.assertIsNotNone(bubble)
        # Start near break so two missiles reach the same active barrier.
        bubble.health = 10
        health = fight.vitals_of(fight.player_target(defender))
        before = health.total
        run.step({pilot: (0, 0, 16)})
        for _ in range(45):
            run.step({})
            if bubble.health == 0:
                break
        self.assertEqual(bubble.health, 0)
        self.assertIsNone(fight.zarya_bubbles.for_target(defender))
        self.assertEqual(health.total, before)
        self.assertEqual(charge.value(), initial_charge)
        for _ in range(150):
            run.step({})
        self.assertEqual(before - health.total, 112)
        self.assertAlmostEqual(charge.value() - (initial_charge or 0), 112, places=4)
        self.assertEqual(len(pilot.client.messages(combat.HIT)), 32)

    def test_actual_self_destruct_breaks_personal_barrier_without_body_damage(self):
        run, pilot, defender, _, source, _ = ana_tests.DvaAnaTests().scene()
        fight = run.match.combat
        self.assertTrue(run.match.switch_hero(defender, find_hero("zarya")))
        x, y, z = source.feet
        defender.mover.position[:] = (x, y, z + 4)
        defender.mover.body = None
        for _ in range(30):
            run.step({})
        body = fight.vitals_of(fight.player_target(defender))
        before = body.total
        mech = pilot.controlled_mech
        component = mech.body_script.component
        component.set(ULT_CHARGE, component.entity_var(ULT_COST).value())
        run.step({pilot: (0, 0, 32)})
        run.step({})
        self.assertTrue(mech.detached_bomb)
        for _ in range(160):
            run.step({})
        run.step({defender: (-32768, 0, 8)})
        bubble = fight.zarya_bubbles.for_target(defender)
        self.assertIsNotNone(bubble)
        for _ in range(25):
            run.step({})
        self.assertEqual(bubble.health, 0)
        self.assertIsNone(fight.zarya_bubbles.for_target(defender))
        self.assertEqual(body.total, before)
        self.assertEqual(defender.mover.velocity[2], 0)
        self.assertFalse(pilot.client.messages(combat.HIT))

    def test_actual_projected_barrier_spends_health_then_expires_on_controlled_mech(self):
        run, pilot, enemy, _, target, health = ana_tests.DvaAnaTests().scene(with_zarya=True)
        fight = run.match.combat
        zarya = run.players[3]
        zarya.team = pilot.team
        self.assertTrue(run.match.switch_hero(zarya, find_hero("zarya")))
        self.assertTrue(run.match.switch_hero(enemy, find_hero("soldier")))
        x, y, z = target.feet
        for actor, position in ((zarya, (x - 4, y, z)), (enemy, (x, y, z + 5))):
            actor.mover.position[:] = position
            actor.mover.body = None
        a, b = target.segment()
        centre = tuple((left + right) * 0.5 for left, right in zip(a, b, strict=True))
        ally_yaw, ally_pitch = aim(fight._eye(zarya), centre)
        enemy_yaw, enemy_pitch = aim(fight._eye(enemy), centre)
        for _ in range(30):
            run.step({zarya: (ally_yaw, ally_pitch, 0)})
        run.step({zarya: (ally_yaw, ally_pitch, 16)})
        bubble = fight.zarya_bubbles.for_target(target.owner)
        self.assertIsNotNone(bubble)
        self.assertEqual(bubble.body, pilot.controlled_mech.entity)
        self.assertIsNone(fight.zarya_bubbles.for_target(pilot))
        before = health.total
        run.step({enemy: (enemy_yaw, enemy_pitch, 1)})
        self.assertEqual(bubble.health, 180)
        self.assertEqual(health.total, before)
        for _ in range(150):
            run.step({})
        self.assertIsNone(fight.zarya_bubbles.for_target(target.owner))
        run.step({enemy: (enemy_yaw, enemy_pitch, 1)})
        self.assertEqual(health.total, before - 20)


class DvaZaryaAssassinTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        manual_tests.ManualIncomingTests.setUpClass.__func__(cls)

    def test_autonomous_assassin_switches_from_bubbled_dva_to_unprotected_enemy(self):
        for pilot_form in (False, True):
            with self.subTest(pilot_form=pilot_form):
                run, pilot, ana, soldier, target, health = ana_tests.DvaAnaTests().scene(
                    pilot_form=pilot_form, with_zarya=True,
                )
                ana.spawned = soldier.spawned = False
                match = run.match
                fight = match.combat
                zarya = run.players[3]
                zarya.team = pilot.team
                self.assertTrue(match.switch_hero(zarya, find_hero("zarya")))
                x, y, z = target.feet
                zarya.mover.position[:] = (x - 4, y, z)
                zarya.mover.body = None
                soldier.mover.position[:] = (x + 1.5, y, z + 2)
                soldier.mover.body = None
                a, b = target.segment()
                centre = tuple((left + right) * 0.5 for left, right in zip(a, b, strict=True))
                yaw, pitch = aim(fight._eye(zarya), centre)
                for _ in range(30):
                    run.step({zarya: (yaw, pitch, 0)})
                bot = npc.spawn(match, self.by_name["Assassin"], (x, y, z + 2), team=1, autonomous=True)
                for _ in range(35):
                    run.step({})
                    if bot.entity in fight.pins:
                        break
                self.assertEqual(fight.pins.get(bot.entity), target.entity)
                self.assertEqual(bot.attack_target, pilot.entity)
                run.step({zarya: (yaw, pitch, 16)})
                bubble = fight.zarya_bubbles.for_target(target.owner)
                self.assertIsNotNone(bubble)
                self.assertNotIn(bot.entity, fight.pins)
                before = health.total
                soldier.spawned = True
                for _ in range(45):
                    run.step({})
                    if bot.entity in fight.pins:
                        break
                self.assertEqual(bot.attack_target, soldier.entity)
                self.assertEqual(fight.pins.get(bot.entity), soldier.body)
                self.assertEqual(health.total, before)
                self.assertEqual(bubble.health, 200)
                soldier.spawned = False
                for _ in range(5):
                    run.step({})
                self.assertIsNone(bot.attack_target)
                self.assertNotIn(bot.entity, fight.pins)
                for _ in range(180):
                    run.step({})
                    if bot.entity in fight.pins:
                        break
                self.assertEqual(bot.attack_target, pilot.entity)
                self.assertEqual(fight.pins.get(bot.entity), target.entity)

    def test_projected_barrier_releases_and_prevents_actual_assassin_pin(self):
        for pilot_form in (False, True):
            for already_pinned in (False, True):
                with self.subTest(pilot_form=pilot_form, already_pinned=already_pinned):
                    run, pilot, ana, soldier, target, health = ana_tests.DvaAnaTests().scene(
                        pilot_form=pilot_form, with_zarya=True,
                    )
                    ana.spawned = soldier.spawned = False
                    match = run.match
                    fight = match.combat
                    zarya = run.players[3]
                    zarya.team = pilot.team
                    self.assertTrue(match.switch_hero(zarya, find_hero("zarya")))
                    x, y, z = target.feet
                    zarya.mover.position[:] = (x - 4, y, z)
                    zarya.mover.body = None
                    a, b = target.segment()
                    centre = tuple((p + q) / 2 for p, q in zip(a, b, strict=True))
                    yaw, pitch = aim(fight._eye(zarya), centre)
                    for _ in range(30):
                        run.step({zarya: (yaw, pitch, 0)})
                    bot = npc.spawn(match, self.by_name["Assassin"], (x, y, z + 2), team=1)
                    if already_pinned:
                        npc_control.engage(match, bot, pilot)
                        for _ in range(30):
                            run.step({zarya: (yaw, pitch, 0)})
                            if fight.is_stunned(target.owner):
                                break
                        self.assertEqual(fight.pins.get(bot.entity), target.entity)
                    before = health.total
                    run.step({zarya: (yaw, pitch, 16)})
                    bubble = fight.zarya_bubbles.for_target(target.owner)
                    self.assertIsNotNone(bubble)
                    self.assertNotIn(bot.entity, fight.pins)
                    self.assertFalse(fight.is_stunned(target.owner))
                    if not already_pinned:
                        npc_control.engage(match, bot, pilot)
                    enemy = fight.vitals[bot.entity]
                    enemy_before = enemy.total
                    yaw, pitch = aim(fight._eye(target.owner), (x, y + 1, z + 2))
                    run.step({pilot: (yaw, pitch, 1)})
                    for _ in range(35):
                        run.step({})
                    self.assertLess(enemy.total, enemy_before)
                    self.assertEqual(health.total, before)
                    self.assertEqual(bubble.health, 200)
                    self.assertNotIn(bot.entity, fight.pins)
                    for _ in range(180):
                        run.step({})
                        if bot.entity in fight.pins:
                            break
                    self.assertEqual(fight.pins.get(bot.entity), target.entity)
