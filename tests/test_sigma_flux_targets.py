"""Executed Flux target arrays; reconstructed admission is separate from effect delivery."""

import math
import os
import unittest
from pathlib import Path
from unittest.mock import patch

import test_sigma_combat as fixtures
import test_sigma_flux_placement as placement

from ow174.game import npc
from ow174.game import sigma_flux as sigma
from ow174.game.body_vm_snapshot import export_component_vm, snapshot_digest
from ow174.game.commands import JUMP
from ow174.game.script import expr, graph, runtime


class SigmaFluxTargetsTests(unittest.TestCase):
    def fixture(self):
        run, player, enemy, health, instance = placement.SigmaFluxPlacementTests().arena()
        state = instance.node_state(instance.graph.node(37))
        instance.write((graph.INSTANCE, 2478), expr.Vec3(*enemy.mover.position))
        return run, player, enemy, health, instance, state

    def test_actual_charged_confirmation_bakes_enemy_body_before_authored_consumer(self):
        run, player, enemy, health, instance, state = self.fixture()
        placement.SigmaFluxPlacementTests().select(run, player, tuple(enemy.mover.position))
        for _ in range(150):
            run.step({})
        self.assertEqual(state.counter, 1)
        self.assertTrue(state.managed)
        self.assertEqual(instance.vars[375].value(), (expr.Entity(enemy.body),))
        self.assertEqual(instance.vars[59].value(), 3)  # nonempty array -> PlayScript -> stage3
        self.assertFalse(state.active)
        self.assertEqual(state.payload(), {"targets": ()})
        self.assertEqual(health.total, 150)
        effect, = (i for i in enemy.body_script.component.instances.values()
                   if i.graph.index == sigma.FLUX_EFFECT)
        self.assertEqual(effect.source_entity(), player.body)
        self.assertEqual(effect.vars[41].value(), (expr.Entity(player.body),))
        self.assertEqual(effect.node_state(effect.graph.node(12)).counter, 1)
        self.assertTrue(enemy.mover.airborne)
        self.assertGreater(enemy.mover.position[1], 3)
        sampled_descent = False
        for _ in range(340):
            run.step({})
            sample = effect.vars.get(12320)
            if sample is not None and effect.states[5].active and effect.states[5].current:
                previous, current = sample.value()
                if previous.y - current.y >= 0.1:
                    self.assertEqual(health.total, 150)
                    self.assertTrue(enemy.mover.airborne)
                    sampled_descent = True
            if health.total == 50:
                self.assertFalse(enemy.mover.airborne)
                break
        self.assertTrue(sampled_descent)
        self.assertEqual(health.total, 50)
        self.assertFalse(enemy.mover.airborne)
        for _ in range(50):
            run.step({})
        self.assertNotIn(effect.id, enemy.body_script.component.instances)
        self.assertEqual(health.total, 50)

    def test_flux_lift_waits_two_authored_frames_after_entry_damage(self):
        run, player, enemy, health, _instance, _state = self.fixture()
        start_y = enemy.mover.position[1]
        placement.SigmaFluxPlacementTests().select(run, player, tuple(enemy.mover.position))
        for _ in range(100):
            run.step({})
            if health.total == 150:
                break
        self.assertEqual(health.total, 150)
        effect, = (i for i in enemy.body_script.component.instances.values()
                   if i.graph.index == sigma.FLUX_EFFECT)
        wait = effect.states[21]
        self.assertTrue(wait.active)
        self.assertEqual(wait.evaluate(wait.node.field("m_3016B9A1"), watch=False), 2)
        self.assertEqual(enemy.mover.position[1], start_y)
        for _ in range(8):
            if not wait.active:
                break
            run.step({})
        self.assertFalse(wait.active)
        self.assertEqual(wait.ended_at - wait.start, 2 * effect.component.frame_ms)
        for _ in range(3):
            run.step({})
        self.assertGreater(enemy.mover.position[1], start_y)

    def test_flux_lift_caps_horizontal_impulse_without_capping_vertical_ascent(self):
        run, player, enemy, health, _instance, _state = self.fixture()
        placement.SigmaFluxPlacementTests().select(run, player, tuple(enemy.mover.position))
        for _ in range(110):
            run.step({})
            if enemy.mover.airborne:
                break
        self.assertEqual(health.total, 150)
        effect, = (i for i in enemy.body_script.component.instances.values()
                   if i.graph.index == sigma.FLUX_EFFECT)
        self.assertTrue(effect.states[3].active)
        self.assertGreater(enemy.mover.velocity[1], 10)
        enemy.mover.add_external_impulse((6.0, 0.0, 8.0))
        run.step({})
        vx, vy, vz = enemy.mover.velocity
        self.assertAlmostEqual(math.hypot(vx, vz), 3, delta=0.005)
        self.assertAlmostEqual(vz / vx, 4 / 3, delta=0.005)
        self.assertGreater(vy, 10)

    def test_flux_vertical_minimum_caps_ascent_but_preserves_faster_descent(self):
        for speed in (8.0, -8.0):
            with self.subTest(speed=speed):
                run, player, enemy, health, _instance, _state = self.fixture()
                component = enemy.body_script.component
                placement.SigmaFluxPlacementTests().select(run, player, tuple(enemy.mover.position))
                for frame in range(100):
                    # Jump during the marked window just before the lift hits.
                    run.step({enemy: (0, 0, JUMP)} if frame == 55 else {})
                    if health.total == 150:
                        break
                self.assertEqual(health.total, 150)
                self.assertTrue(enemy.mover.airborne)
                effect, = (i for i in component.instances.values() if i.graph.index == sigma.FLUX_EFFECT)
                self.assertTrue(effect.states[0].current)
                wait = effect.states[10]
                self.assertTrue(wait.active)
                deadline = wait.start + wait.ms
                while component.now + component.frame_ms < deadline:
                    run.step({})
                self.assertTrue(wait.active)
                self.assertTrue(enemy.mover.airborne)
                enemy.mover.velocity = (0.0, speed, 0.0)
                run.step({})
                self.assertFalse(wait.active)
                if speed > 0:
                    self.assertEqual(enemy.mover.velocity[1], 3.0)
                else:
                    self.assertLessEqual(enemy.mover.velocity[1], speed)

    def test_current_capsule_range_and_height_and_no_controller_alias(self):
        run, player, enemy, _health, _instance, state = self.fixture()
        world = player.body_script.world
        x, y, z = enemy.mover.position
        radius = next(t.radius for t in run.match.combat._targets() if t.owner is enemy)
        for offset, accepted in ((0, True), (7.5 + radius - .02, True),
                                 (7.5 + radius + .02, False)):
            with self.subTest(offset=offset):
                enemy.mover.position = [x + offset, y, z]
                self.assertEqual(sigma.flux_targets(world, state), (enemy.body,) if accepted else ())
        enemy.mover.position = [x, y + 20, z]
        self.assertEqual(sigma.flux_targets(world, state), ())

    def test_actual_flux_confirmation_excludes_cryo_freeze(self):
        from ow174.game.content import heroes

        run, player, mei, _, instance, state = self.fixture()
        position = list(mei.mover.position)
        run.match.switch_hero(mei, heroes()[0x02E00000000000DD])
        mei.mover.position = position
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is mei)
        health = fight.vitals_of(target)
        run.step({})
        run.step({mei: (0, 0, 0x08)})
        self.assertEqual(sigma.body_protection(mei), "cryo")
        self.assertFalse(sigma.flux_targets(player.body_script.world, state))
        placement.SigmaFluxPlacementTests().select(run, player, tuple(position))
        for _ in range(120):
            run.step({})
        self.assertEqual(state.counter, 1)
        self.assertEqual(instance.vars[375].value(), ())
        self.assertEqual(sigma.body_protection(mei), "cryo")
        receiver = mei.body_script.component
        self.assertFalse(any(i.graph.index == sigma.FLUX_EFFECT for i in receiver.instances.values()))
        self.assertEqual(health.total, 250)
        self.assertFalse(mei.mover.airborne)
        for _ in range(130):
            if not sigma.body_protection(mei):
                break
            run.step({})
        self.assertIsNone(sigma.body_protection(mei))
        self.assertEqual(health.total, 250)

    def test_actual_flux_confirmation_excludes_wraith_form_and_fade(self):
        from ow174.game.content import heroes

        for hero in (0x02E0000000000002, 0x02E00000000001A2):
            with self.subTest(hero=f"{hero:016X}"):
                run, player, phased, _, instance, state = self.fixture()
                position = tuple(phased.mover.position)
                run.match.switch_hero(phased, heroes()[hero])
                phased.mover.position = list(position)
                fight = run.match.combat
                health = fight.vitals_of(next(t for t in fight._targets() if t.owner is phased))
                yaw, pitch = placement.SigmaFluxPlacementTests().select(run, player, position, click=False)
                run.step({player: (yaw, pitch, 1)})
                for _ in range(40):
                    run.step({})
                self.assertEqual(state.counter, 0)
                run.step({phased: (0, 0, 0x08)})
                self.assertEqual(sigma.body_protection(phased), "phase")
                for _ in range(40):
                    if state.counter:
                        break
                    run.step({})
                self.assertEqual(state.counter, 1)
                self.assertEqual(instance.vars[375].value(), ())
                self.assertEqual(sigma.body_protection(phased), "phase")
                for _ in range(65):
                    run.step({})
                self.assertEqual(health.total, health.maximum)
                self.assertFalse(phased.mover.airborne)

    def test_protection_activated_during_flux_releases_target_before_slam(self):
        self.assert_protection_releases_flux_target(during_descent=False)

    def test_protection_during_flux_descent_prevents_slam_but_allows_later_hits(self):
        self.assert_protection_releases_flux_target(during_descent=True)

    def assert_protection_releases_flux_target(self, *, during_descent):
        from ow174.game.content import heroes

        for hero in (0x02E0000000000002, 0x02E00000000001A2, 0x02E00000000000DD):
            with self.subTest(hero=f"{hero:016X}"):
                run, player, target, _, _, _ = self.fixture()
                position = tuple(target.mover.position)
                run.match.switch_hero(target, heroes()[hero])
                target.mover.position = list(position)
                fight = run.match.combat
                health = fight.vitals_of(next(t for t in fight._targets() if t.owner is target))
                player.body_script.ult_seconds = 0
                placement.SigmaFluxPlacementTests().select(run, player, position)
                for _ in range(110):
                    if health.total < health.maximum:
                        break
                    run.step({})
                after_entry = health.maximum - 50
                self.assertEqual(health.total, after_entry)
                for _ in range(12):
                    run.step({})
                receiver = target.body_script.component
                effect, = (i for i in receiver.instances.values() if i.graph.index == sigma.FLUX_EFFECT)
                lift_flag = receiver.entity_var(22656, create=False)
                self.assertTrue(lift_flag.value())
                self.assertTrue(target.mover.airborne)
                self.assertGreater(target.mover.position[1], position[1] + 1)
                self.assertTrue(effect.states[16].active)

                if during_descent:
                    for _ in range(260):
                        run.step({})
                        if target.mover.velocity[1] < -1 and target.mover.position[1] < position[1] + 8:
                            break
                    self.assertTrue(target.mover.airborne)
                    self.assertLess(target.mover.velocity[1], -1)
                    self.assertGreater(target.mover.position[1], position[1] + 2)
                    self.assertIn(effect.id, receiver.instances)
                    self.assertFalse(effect.states[16].active)
                    self.assertEqual(health.total, after_entry)
                run.step({target: (0, 0, 0x08)})
                self.assertIsNotNone(sigma.body_protection(target))
                self.assertNotIn(effect.id, receiver.instances)
                self.assertFalse(lift_flag.value())
                self.assertTrue(target.mover.airborne)
                for _ in range(290):
                    run.step({})
                self.assertEqual(health.total, after_entry)
                self.assertFalse(target.mover.airborne)
                self.assertAlmostEqual(target.mover.position[1], position[1], delta=0.01)
                self.assertIsNone(sigma.body_protection(target))
                self.assertNotIn(effect.id, receiver.instances)
                self.assertEqual(player.body_script.component.entity_var(342).value(), 0)
                if during_descent:
                    fixtures.SigmaCombatTests().pair(run, player, target)
                    self.assertEqual(health.total, after_entry - 110)
                    self.assertEqual(player.body_script.component.entity_var(342).value(), 110)

    def test_flux_lift_preserves_grasp_until_its_normal_shield_grant(self):
        self.assert_grasp_grant_during_flux(lethal=False)

    def test_lethal_flux_slam_retires_grasp_shields_before_respawn(self):
        self.assert_grasp_grant_during_flux(lethal=True)

    def test_lethal_flux_entry_cancels_grasp_without_shield_grant(self):
        self.assert_grasp_grant_during_flux(lethal=True, lethal_entry=True)

    def assert_grasp_grant_during_flux(self, *, lethal, lethal_entry=False):
        from dataclasses import replace

        from test_game_combat import KANEZAKA_DM, Run, aim

        from ow174.game import combat
        from ow174.game.content import heroes

        run = Run(KANEZAKA_DM, ("Caster", "Target", "Soldier"))
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        caster, target, rifle = run.players
        caster.team, target.team, rifle.team = 0, 1, 0
        for player in (caster, target):
            run.match.switch_hero(player, heroes()[sigma.HERO])
        x, y, z = caster.mover.position
        target.mover.position = [x, y, z + 6]
        rifle.mover.position = [x + 6, y, z + 6]
        fight = run.match.combat
        health = fight.vitals_of(next(entry for entry in fight._targets() if entry.owner is target))
        if lethal:
            health.now[combat.HEALTH_POOL] = 50 if lethal_entry else 200
            health.now[combat.SHIELDS_POOL] = 0
        starting_health = health.total
        script = caster.body_script
        script.ult_seconds = 0
        script.set_ult_charge(script.component.entity_var(338).value())
        placement.SigmaFluxPlacementTests().select(run, caster, tuple(target.mover.position))
        grasp_aim = aim(fight._eye(target), fight._eye(rifle))
        rifle_aim = aim(fight._eye(rifle), (x, y + 0.9, z + 6))
        for _ in range(40):
            run.step({})
        run.step({target: (*grasp_aim, 0x08)})
        grasp = fixtures.SigmaCombatTests().ability(target, sigma.GRASP).instance
        run.step({target: (*grasp_aim, 0), rifle: (*rifle_aim, 1)})
        self.assertEqual(grasp.vars[21312].value(), 20)
        self.assertEqual(health.total, starting_health)
        if lethal_entry:
            body = target.body
            for _ in range(750):
                run.step({target: (*grasp_aim, 0)})
                self.assertNotIn(body, fight.grasp_grants)
                self.assertNotIn(body, fight.grasp_shields)
                self.assertFalse(health.temporary_shields)
                if target.body != body:
                    break
                self.assertFalse(target.mover.airborne)
            self.assertTrue(health.dead)
            self.assertEqual(health.killer, caster.body)
            self.assertFalse(grasp.states[1].active)
            self.assertNotEqual(target.body, body)
            new_target = next(entry for entry in fight._targets() if entry.owner is target)
            new_health = fight.vitals_of(new_target)
            self.assertEqual(new_health.total, 400)
            self.assertFalse(new_health.temporary_shields)
            self.assertEqual(script.component.entity_var(342).value(), 0)
            return
        for _ in range(95):
            run.step({target: (*grasp_aim, 0)})
            if target.mover.airborne:
                break
        self.assertTrue(target.mover.airborne)
        after_entry = starting_health - 50
        self.assertEqual(health.total, after_entry)
        self.assertNotIn(sigma.SHIELD_TAG, health.temporary_shields)
        self.assertTrue(grasp.states[1].active)
        for _ in range(120):
            run.step({target: (*grasp_aim, 0)})
            if not grasp.states[1].active:
                break
        self.assertFalse(grasp.states[1].active)
        self.assertEqual(health.temporary_shields[sigma.SHIELD_TAG].now, 12)
        self.assertEqual(health.total, after_entry + 12)
        body = target.body
        self.assertIn(body, fight.grasp_shields)
        for _ in range(300):
            run.step({})
            if health.total < after_entry:
                break
        self.assertFalse(target.mover.airborne)
        self.assertEqual(health.total, 0 if lethal else 156)
        self.assertEqual(script.component.entity_var(342).value(), 0)
        if lethal:
            self.assertTrue(health.dead)
            self.assertEqual(health.killer, caster.body)
            self.assertNotIn(body, fight.grasp_shields)
            self.assertNotIn(body, fight.grasp_grants)
            for _ in range(750):
                run.step({})
                if target.body != body and not fight.is_dead(target):
                    break
            self.assertNotEqual(target.body, body)
            new_target = next(entry for entry in fight._targets() if entry.owner is target)
            new_health = fight.vitals_of(new_target)
            self.assertEqual(new_health.total, 400)
            self.assertFalse(new_health.temporary_shields)
            self.assertNotIn(target.body, fight.grasp_shields)
            self.assertFalse(any(i.graph.index == sigma.FLUX_EFFECT
                                 for i in target.body_script.component.instances.values()))
        else:
            self.assertEqual(health.temporary_shields[sigma.SHIELD_TAG].now, 0)

    def test_flux_lift_during_accretion_windup_preserves_launch_and_cooldown(self):
        self.assert_accretion_from_flux_lift(hit=False)

    def test_airborne_accretion_hit_interrupts_flux_before_slam(self):
        self.assert_accretion_from_flux_lift(hit=True)

    def test_airborne_accretion_hit_after_descent_starts_preserves_slam(self):
        self.assert_accretion_from_flux_lift(hit=True, late=True)

    def assert_accretion_from_flux_lift(self, *, hit, late=False):
        from test_game_combat import aim

        from ow174.game.content import heroes
        from ow174.game.script import nodes

        run, caster, target, _, _, _ = self.fixture()
        floor = list(target.mover.position)
        run.match.switch_hero(target, heroes()[sigma.HERO])
        target.mover.position = floor
        fight = run.match.combat
        vitals = fight.vitals_of(next(entry for entry in fight._targets() if entry.owner is target))
        caster_vitals = fight.vitals_of(next(entry for entry in fight._targets() if entry.owner is caster))
        script = target.body_script
        script.ult_seconds = 0
        script.set_ult_charge(0)
        caster.body_script.ult_seconds = 0
        flux = fixtures.SigmaCombatTests().ability(caster, sigma.FLUX)
        placement.SigmaFluxPlacementTests().select(run, caster, tuple(floor))

        def aim_rock():
            x, y, z = fight._eye(caster)
            offset = 0.5 if late else 1.2 if hit else 0
            if not hit:
                x += 4
            return aim(fight._eye(target), (x, y - offset, z))

        for _ in range(134 if late else 40):
            run.step({})
        rock_aim = aim_rock()
        run.step({target: (*rock_aim, 0x10)})
        ability = fixtures.SigmaCombatTests().ability(target, sigma.ACCRETION)
        self.assertTrue(ability.flags & nodes.Ability.RUNNING)
        self.assertFalse(fight.rockets)
        for _ in range(85):
            rock_aim = aim_rock()
            run.step({target: (*rock_aim, 0)})
            if target.mover.airborne:
                break
        self.assertTrue(target.mover.airborne)
        self.assertEqual(vitals.total, 350)
        self.assertTrue(ability.flags & nodes.Ability.RUNNING)
        self.assertFalse(fight.rockets)
        for _ in range(65):
            rock_aim = aim_rock()
            run.step({target: (*rock_aim, 0)})
            if fight.rockets:
                break
        rock, = fight.rockets
        self.assertIs(rock.shooter, target)
        self.assertTrue(target.mover.airborne)
        self.assertGreater(rock.position[1], floor[1] + 1)
        for _ in range(40):
            run.step({})
            if rock not in fight.rockets:
                break
        self.assertNotIn(rock, fight.rockets)
        self.assertEqual(caster_vitals.total, 330 if hit else 400)
        self.assertEqual(fight.is_stunned(caster), hit)
        self.assertEqual(script.component.entity_var(342).value(), 70 if hit else 0)
        if hit:
            self.assertFalse(flux.flags & nodes.Ability.RUNNING)
            self.assertEqual(caster.body_script.component.entity_var(342).value(), 0)
            effects = [instance for instance in target.body_script.component.instances.values()
                       if instance.graph.index == sigma.FLUX_EFFECT]
            if late:
                effect, = effects
                self.assertFalse(effect.states[16].active)
                self.assertTrue(target.mover.airborne)
                self.assertLess(target.mover.velocity[1], 0)
            else:
                self.assertFalse(effects)
        for _ in range(90):
            run.step({})
            if ability.remaining() > 0 and not fight.rockets:
                break
        self.assertGreater(ability.remaining(), 2)
        run.step({target: (*rock_aim, 0x10)})
        for _ in range(65):
            run.step({target: (*rock_aim, 0)})
            self.assertFalse(fight.rockets)
        expected = 350 if hit and not late else 150
        for _ in range(270):
            if not target.mover.airborne and vitals.total == expected:
                break
            run.step({})
        self.assertFalse(target.mover.airborne)
        self.assertEqual(vitals.total, expected)
        if hit and not late:
            for _ in range(260):
                run.step({})
                self.assertGreaterEqual(vitals.total, expected)

    def test_allied_biotic_field_changes_flux_slam_survival(self):
        from dataclasses import replace

        from test_game_combat import KANEZAKA_DM, Run

        from ow174.game import combat, stats
        from ow174.game.content import heroes

        for heal in (False, True):
            with self.subTest(heal=heal):
                run = Run(KANEZAKA_DM, ("Sigma", "Target", "Healer"))
                run.match.bots.clear()
                run.match._pending_bot_scripts.clear()
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                caster, target, healer = run.players
                caster.team, target.team, healer.team = 0, 1, 1
                run.match.switch_hero(caster, heroes()[sigma.HERO])
                x, y, z = caster.mover.position
                target.mover.position = [x, y, z + 6]
                healer.mover.position = [x + 4, y, z + 6]
                fight = run.match.combat
                health = {entry.owner: fight.vitals_of(entry) for entry in fight._targets()}
                health[target].now[combat.HEALTH_POOL] = 145
                script = caster.body_script
                script.ult_seconds = 0
                script.set_ult_charge(script.component.entity_var(338).value())
                placement.SigmaFluxPlacementTests().select(run, caster, (x - 4, y, z + 6))
                if heal:
                    run.step({healer: (0, 0, 0x10)})  # Biotic Field
                for _ in range(130):
                    run.step({})
                    if target.mover.airborne:
                        break
                self.assertTrue(target.mover.airborne)
                self.assertAlmostEqual(health[target].total, 95 + healer.stats.value(stats.HEALING))
                self.assertEqual(health[healer].total, 200)
                if heal:
                    self.assertGreater(healer.stats.value(stats.HEALING), 5)
                for _ in range(300):
                    run.step({})
                    if not target.mover.airborne:
                        break
                self.assertFalse(target.mover.airborne)
                self.assertEqual(health[target].dead, not heal)
                if heal:
                    healed = healer.stats.value(stats.HEALING)
                    self.assertGreater(healed, 5)
                    self.assertAlmostEqual(health[target].total, healed - 5)
                else:
                    self.assertEqual(health[target].killer, caster.body)
                self.assertEqual(health[healer].total, 200)
                self.assertEqual(script.component.entity_var(342).value(), 0)

    def test_player_lift_hits_static_ceiling_and_still_slams_on_the_floor(self):
        from test_game_mover import box

        from ow174.game.collision import World

        run, player, enemy, health, _instance, _state = self.fixture()
        x, y, z = enemy.mover.position
        floor = [(-5000, y, -5000, -5000, y, 5000, 5000, y, 5000),
                 (-5000, y, -5000, 5000, y, 5000, 5000, y, -5000)]
        run.match.collision_world = World(floor + box(x - 2, x + 2, y + 3.5, y + 4, z - 2, z + 2))
        placement.SigmaFluxPlacementTests().select(run, player, (x, y, z))
        peak = y
        for _ in range(360):
            run.step({})
            peak = max(peak, enemy.mover.position[1])
        self.assertGreater(peak - y, 0.2)
        self.assertLess(peak - y, 2.0)
        self.assertAlmostEqual(enemy.mover.position[1], y, delta=0.01)
        self.assertFalse(enemy.mover.airborne)
        self.assertEqual(health.total, 50)

    def test_zarya_barriers_absorb_flux_damage_without_ending_the_lift(self):
        from dataclasses import replace

        from test_game_combat import KANEZAKA_DM, Run, aim

        from ow174.game.content import heroes

        for defense in ("entry", "break_entry", "personal_slam", "projected_slam", "break_projected_slam"):
            with self.subTest(defense=defense):
                entry_barrier = defense in ("entry", "break_entry")
                breaking = defense in ("break_entry", "break_projected_slam")
                run = Run(KANEZAKA_DM, ("Sigma", "Target", "Teammate"))
                run.match.bots.clear()
                run.match._pending_bot_scripts.clear()
                player, target, teammate = run.players
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                player.team, target.team, teammate.team = 0, 1, 1
                run.match.switch_hero(player, heroes()[sigma.HERO])
                caster = teammate if defense in ("projected_slam", "break_projected_slam") else target
                run.match.switch_hero(caster, heroes()[0x02E0000000000068])
                x, y, z = player.mover.position
                target.mover.position = [x, y, z + 6]
                teammate.mover.position = [x + 10, y, z + 6]
                fight = run.match.combat
                health = fight.vitals_of(next(t for t in fight._targets() if t.owner is target))
                maximum = health.maximum
                script = player.body_script
                script.ult_seconds = 0
                script.set_ult_charge(script.component.entity_var(338).value())
                placement.SigmaFluxPlacementTests().select(run, player, tuple(target.mover.position))
                receiver = target.body_script.component
                energy = caster.body_script.component.entity_var(87)
                if entry_barrier:
                    for _ in range(40):
                        run.step({})
                    run.step({caster: (0, 0, 0x08)})
                    bubble = sigma.particle_barrier(target)
                    self.assertIsNotNone(bubble)
                    if breaking:
                        health.particle_barriers[sigma.particle_barrier_key(bubble)] = 40
                    for _ in range(60):
                        if energy.value():
                            break
                        run.step({})
                    self.assertEqual(list(health.particle_barriers.values()), [] if breaking else [150])
                    self.assertEqual(health.total, maximum)
                    self.assertAlmostEqual(energy.value(), 8 if breaking else 10, places=5)
                    if breaking:
                        self.assertIsNone(sigma.particle_barrier(target))
                peak = y
                for _ in range(260):
                    run.step({})
                    height = target.mover.position[1]
                    peak = max(peak, height)
                    if peak > y + 10 and y + 2 < height < y + 8 and target.mover.velocity[1] < -1:
                        break
                self.assertGreater(peak, y + 10)
                self.assertTrue(target.mover.airborne)
                self.assertTrue(any(i.graph.index == sigma.FLUX_EFFECT for i in receiver.instances.values()))
                if entry_barrier:
                    self.assertIsNone(sigma.particle_barrier(target))
                else:
                    self.assertEqual(health.total, maximum - 50)
                    if caster is target:
                        run.step({caster: (0, 0, 0x08)})
                    else:
                        yaw, pitch = aim(fight._eye(caster), fight._eye(target))
                        run.step({caster: (yaw, pitch, 0x10)})
                    bubble = sigma.particle_barrier(target)
                    self.assertIsNotNone(bubble)
                    if breaking:
                        health.particle_barriers[sigma.particle_barrier_key(bubble)] = 40
                    self.assertTrue(target.mover.airborne)

                for _ in range(28):
                    run.step({})
                self.assertFalse(target.mover.airborne)
                self.assertAlmostEqual(target.mover.position[1], y, delta=0.01)
                if entry_barrier:
                    self.assertEqual(health.total, maximum / 2)
                else:
                    self.assertEqual(health.total, maximum - 50)
                    self.assertAlmostEqual(energy.value(), 8 if breaking else maximum / 10, places=5)
                    if caster is teammate and not breaking:
                        self.assertEqual(list(health.particle_barriers.values()), [100])
                    else:
                        self.assertIsNone(sigma.particle_barrier(target))
                self.assertEqual(script.component.entity_var(342).value(), 0)

    def test_fortify_before_flux_reduces_entry_and_during_flux_releases_lift(self):
        from ow174.game.content import heroes

        for early in (True, False):
            with self.subTest(early=early):
                run, player, orisa, _, _, _ = self.fixture()
                position = tuple(orisa.mover.position)
                run.match.switch_hero(orisa, heroes()[0x02E000000000013E])
                orisa.mover.position = list(position)
                fight = run.match.combat
                health = fight.vitals_of(next(t for t in fight._targets() if t.owner is orisa))
                receiver = orisa.body_script.component
                placement.SigmaFluxPlacementTests().select(run, player, position)
                if early:
                    for _ in range(40):
                        run.step({})
                else:
                    for _ in range(95):
                        if orisa.mover.position[1] > position[1] + 4:
                            break
                        run.step({})
                    self.assertEqual(health.maximum - health.total, 45)
                    self.assertTrue(orisa.mover.airborne)
                    self.assertTrue(any(
                        i.graph.index == sigma.FLUX_EFFECT for i in receiver.instances.values()
                    ))
                run.step({orisa: (0, 0, 0x08)})
                self.assertIsNotNone(sigma.fortify(orisa))
                peak = orisa.mover.position[1]
                for _ in range(215):
                    run.step({})
                    peak = max(peak, orisa.mover.position[1])
                    self.assertFalse(any(
                        i.graph.index == sigma.FLUX_EFFECT for i in receiver.instances.values()
                    ))
                self.assertFalse(orisa.mover.airborne)
                self.assertAlmostEqual(orisa.mover.position[1], position[1], delta=0.01)
                self.assertAlmostEqual(health.maximum - health.total, 25 if early else 45, places=5)
                if early:
                    self.assertAlmostEqual(peak, position[1], delta=0.01)
                else:
                    self.assertGreater(peak, position[1] + 4)
                    self.assertLess(peak, position[1] + 20)

    def test_replacing_caster_body_retires_live_target_effect(self):
        run, player, enemy, health, _instance, _state = self.fixture()
        placement.SigmaFluxPlacementTests().select(run, player, tuple(enemy.mover.position))
        for _ in range(100):
            run.step({})
        receiver = enemy.body_script.component
        effect, = (i for i in receiver.instances.values() if i.graph.index == sigma.FLUX_EFFECT)
        self.assertEqual(health.total, 150)
        source = player.body
        from ow174.game.content import SOLDIER, heroes
        run.match.switch_hero(player, heroes()[SOLDIER])
        self.assertNotEqual(player.body, source)
        run.step({})
        self.assertNotIn(effect.id, receiver.instances)
        self.assertEqual(health.total, 150)

    def test_caster_death_releases_lift_but_does_not_cancel_committed_descent(self):
        from test_game_combat import aim

        from ow174.game import combat

        for early in (True, False):
            with self.subTest(early=early):
                run, player, enemy, health, _, _ = self.fixture()
                fight = run.match.combat
                caster = fight.vitals_of(next(t for t in fight._targets() if t.owner is player))
                caster.now[combat.HEALTH_POOL] = 15
                caster.now[combat.SHIELDS_POOL] = 0
                placement.SigmaFluxPlacementTests().select(run, player, tuple(enemy.mover.position))
                receiver = enemy.body_script.component
                for _ in range(205):
                    run.step({})
                    if not enemy.mover.airborne:
                        continue
                    if early or enemy.mover.velocity[1] < -1:
                        break
                effect, = (i for i in receiver.instances.values() if i.graph.index == sigma.FLUX_EFFECT)
                self.assertEqual(effect.states[16].active, early)
                x, y, z = player.mover.position
                yaw, pitch = aim(fight._eye(enemy), (x, y + 0.9, z))
                run.step({enemy: (yaw, pitch, 1)})
                self.assertTrue(fight.is_dead(player))
                self.assertEqual(effect.id in receiver.instances, not early)
                for _ in range(210):
                    run.step({})
                self.assertFalse(enemy.mover.airborne)
                self.assertEqual(health.total, 150 if early else 50)

    def test_lethal_entry_hit_retires_effect_and_credits_caster(self):
        from ow174.game.combat import HEALTH_POOL

        run, player, enemy, health, _instance, _state = self.fixture()
        health.now[HEALTH_POOL] = 45
        receiver = enemy.body_script.component
        placement.SigmaFluxPlacementTests().select(run, player, tuple(enemy.mover.position))
        for _ in range(100):
            run.step({})
        self.assertTrue(health.dead)
        self.assertEqual(health.killer, player.body)
        self.assertFalse(any(i.graph.index == sigma.FLUX_EFFECT for i in receiver.instances.values()))

    def test_actual_empty_selection_clears_previous_array_and_takes_authored_miss_branch(self):
        run, player, enemy, health, instance, state = self.fixture()
        point = tuple(enemy.mover.position)
        enemy.mover.position[0] += 20
        instance.write((graph.INSTANCE, 375), (expr.Entity(enemy.body),))
        placement.SigmaFluxPlacementTests().select(run, player, point)
        for _ in range(150):
            run.step({})
        self.assertEqual(state.counter, 1)
        self.assertEqual(instance.vars[375].value(), ())
        self.assertEqual(instance.vars[59].value(), 4)
        self.assertEqual(health.total, 200)

    def test_team_ffa_dead_and_replaced_body_membership(self):
        run, player, enemy, _health, _instance, state = self.fixture()
        world = player.body_script.world
        enemy.team = player.team
        self.assertEqual(sigma.flux_targets(world, state), (enemy.body,))  # FFA
        match = run.match
        match.game_map = match.game_map.__class__(**{**match.game_map.__dict__, "free_for_all": False})
        self.assertEqual(sigma.flux_targets(world, state), ())
        enemy.team += 1
        old = enemy.body
        position = list(enemy.mover.position)
        from ow174.game.content import heroes
        match.switch_hero(enemy, heroes()[sigma.HERO])
        enemy.mover.position = position
        self.assertNotEqual(enemy.body, old)
        self.assertEqual(sigma.flux_targets(world, state), (enemy.body,))
        target = next(t for t in match.combat._targets() if t.owner is enemy)
        match.combat._damage(player, target, 10000, False, None, run.frame)
        self.assertEqual(sigma.flux_targets(world, state), ())

    def test_static_wall_blocks_a_confirmed_flux_from_the_ground_sight_point(self):
        run, player, enemy, health, instance, state = self.fixture()
        x, y, z = player.mover.position
        point = (x, y, z + 1)
        instance.write((graph.INSTANCE, 2478), expr.Vec3(*point))
        self.assertEqual(sigma.flux_targets(player.body_script.world, state), (enemy.body,))
        fixtures.SigmaCombatTests().wall(run, sideways=False, offset=3)
        self.assertEqual(sigma.flux_targets(player.body_script.world, state), ())

        placement.SigmaFluxPlacementTests().select(run, player, point)
        self.assertEqual(instance.vars[59].value(), 2)
        for _ in range(150):
            run.step({})
        self.assertEqual(state.counter, 1)
        self.assertEqual(instance.vars[375].value(), ())
        self.assertEqual(instance.vars[59].value(), 4)
        self.assertEqual(health.total, health.maximum)
        self.assertFalse(enemy.mover.airborne)
        self.assertFalse(any(effect.graph.index == sigma.FLUX_EFFECT
                             for effect in enemy.body_script.component.instances.values()))

    def test_unknown_queries_and_strict_mode_do_not_use_this_reconstruction(self):
        _run, player, _enemy, _health, instance, state = self.fixture()
        world = player.body_script.world
        instance.component.strict = True
        self.assertIs(sigma.flux_targets(world, state), NotImplemented)
        instance.component.strict = False
        world.hero = 0
        self.assertIs(sigma.flux_targets(world, state), NotImplemented)
        self.assertIs(runtime.World().tracked_targets(state), NotImplemented)

    def test_managed_target_storage_is_causal_in_read_only_vm_export(self):
        _run, _player, enemy, _health, instance, state = self.fixture()
        state.refresh()
        self.assertEqual(state.targets, (enemy.body,))
        component = instance.component
        before = snapshot_digest(export_component_vm(component))
        for field, value in (("targets", ()), ("managed", False)):
            with self.subTest(field=field), patch.object(state, field, value):
                self.assertNotEqual(before, snapshot_digest(export_component_vm(component)))
        self.assertEqual(before, snapshot_digest(export_component_vm(component)))

    @unittest.skipUnless(os.environ.get("ORPHEUS_NPC_CATALOG"), "requires external NPC catalog")
    def test_airborne_npc_lands_without_slam_after_source_replacement(self):
        from ow174.game.content import SOLDIER, heroes
        from ow174.game.physics_snapshot import snapshot_mover

        definitions = npc.load_catalog(Path(os.environ["ORPHEUS_NPC_CATALOG"]))
        trooper = next(d for d in definitions.values() if d.hero == 0x02E00000000001AC)
        run, player, enemy, _health, _instance, _state = self.fixture()
        run.match.players.remove(enemy)
        actor = npc.spawn(run.match, trooper, tuple(enemy.mover.position), team=1)
        start_y = actor.position[1]
        vitals = run.match.combat.vitals[actor.entity]
        placement.SigmaFluxPlacementTests().select(run, player, tuple(actor.position))
        for _ in range(100):
            run.step({})
        mover = actor.impulse_mover
        self.assertTrue(mover.airborne)
        before = snapshot_mover(mover)
        with patch.object(mover, "velocity", (0.0, -1.0, 0.0)):
            self.assertNotEqual(before, snapshot_mover(mover))
        self.assertEqual(before, snapshot_mover(mover))
        run.match.switch_hero(player, heroes()[SOLDIER])
        run.step({})
        receiver = actor.body_script.component
        self.assertFalse(any(i.graph.index == sigma.FLUX_EFFECT for i in receiver.instances.values()))
        for _ in range(300):
            run.step({})
        self.assertIsNone(actor.impulse_mover)
        self.assertAlmostEqual(actor.position[1], start_y, delta=0.01)
        self.assertEqual(vitals.total, 150)

    @unittest.skipUnless(os.environ.get("ORPHEUS_NPC_CATALOG"), "requires external NPC catalog")
    def test_all_five_actual_npc_bodies_follow_their_authored_flux_branches(self):
        definitions = npc.load_catalog(Path(os.environ["ORPHEUS_NPC_CATALOG"]))
        selected = set()
        for definition in definitions.values():
            with self.subTest(npc=definition.hero):
                run, player, enemy, _health, _instance, state = self.fixture()
                run.match.players.remove(enemy)
                actor = npc.spawn(run.match, definition, tuple(enemy.mover.position), team=1)
                self.assertEqual(sigma.flux_targets(player.body_script.world, state), (actor.entity,))
                vitals = run.match.combat.vitals[actor.entity]
                before = vitals.total
                start_y = peak = actor.position[1]
                placement.SigmaFluxPlacementTests().select(run, player, tuple(actor.position))
                for _ in range(150):
                    run.step({})
                    peak = max(peak, actor.position[1])
                receiver = actor.body_script.component
                effects = [i for i in receiver.instances.values() if i.graph.index == sigma.FLUX_EFFECT]
                heavy = definition.hero == 0x02E00000000001BA
                if heavy:  # Heavy's node 44 gate retires after the entry hit.
                    self.assertTrue(receiver.entity_var(6601, create=False).value())
                    self.assertFalse(effects)
                else:
                    effect, = effects
                    self.assertEqual(effect.vars[41].value(), (expr.Entity(player.body),))
                    self.assertEqual(effect.node_state(effect.graph.node(12)).counter, 1)
                    self.assertTrue(effect.states[0].current)
                    self.assertEqual(effect.states[10].counter, 1)
                    self.assertFalse(effect.states[10].active)
                    self.assertIsNotNone(actor.impulse_mover)
                    self.assertTrue(actor.impulse_mover.airborne)
                    self.assertGreater(actor.movement(run.frame).air_ticks, 0)
                self.assertEqual(before - vitals.total, 50)
                for _ in range(260):
                    run.step({})
                    peak = max(peak, actor.position[1])
                self.assertAlmostEqual(actor.position[1], start_y, delta=0.01)
                self.assertIsNone(actor.impulse_mover)
                self.assertFalse(any(i.graph.index == sigma.FLUX_EFFECT for i in receiver.instances.values()))
                if heavy:
                    self.assertEqual(peak, start_y)
                    self.assertEqual(before - vitals.total, 50)
                else:
                    self.assertGreater(peak - start_y, 1)
                    previous, current = effect.vars[12320].value()
                    self.assertGreater(previous.y, current.y)
                    self.assertEqual(before - vitals.total, 50 + vitals.maximum * 0.5)
                selected.add(definition.hero)
        self.assertEqual(len(selected), 5)


    @unittest.skipUnless(os.environ.get("ORPHEUS_NPC_CATALOG"), "requires external NPC catalog")
    def test_one_cast_delivers_flux_to_enemy_player_and_five_npcs(self):
        from dataclasses import replace

        from test_game_combat import KANEZAKA_DM, Run

        from ow174.game.content import heroes

        run = Run(KANEZAKA_DM, ("Sigma", "Enemy", "Ally", "Distant"))
        match = run.match
        match.bots.clear()
        match._pending_bot_scripts.clear()
        match.game_map = replace(match.game_map, free_for_all=False)
        caster, enemy, ally, distant = run.players
        caster.team, enemy.team, ally.team, distant.team = 0, 1, 0, 1
        match.switch_hero(caster, heroes()[sigma.HERO])
        x, y, z = caster.mover.position
        point = (x, y, z + 6)
        enemy.mover.position = [x, y, z + 7]
        ally.mover.position = [x + 4, y, z + 6]
        distant.mover.position = [x + 20, y, z + 6]
        definitions = npc.load_catalog(Path(os.environ["ORPHEUS_NPC_CATALOG"]))
        self.assertEqual(len(definitions), 5)
        positions = ((-4, -2), (-2, 2), (2, 2), (4, -2), (0, -3))
        actors = [
            (definition, npc.spawn(match, definition, (x + dx, y, z + 6 + dz), team=1))
            for definition, (dx, dz) in zip(definitions.values(), positions, strict=True)
        ]
        fight = match.combat
        health = {entry.owner: fight.vitals_of(entry) for entry in fight._targets() if entry.owner in run.players}
        targets = [(enemy.body, enemy.body_script.component, health[enemy], False)]
        targets += [(actor.entity, actor.body_script.component, fight.vitals[actor.entity],
                     definition.hero == 0x02E00000000001BA) for definition, actor in actors]
        initial = {entity: vitals.total for entity, _component, vitals, _heavy in targets}
        script = caster.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        instance = next(i for i in script.component.instances.values() if i.graph.index == sigma.FLUX)
        placement.SigmaFluxPlacementTests().select(run, caster, point)
        for _ in range(150):
            run.step({})
        self.assertCountEqual(instance.vars[375].value(), [expr.Entity(entity) for entity in initial])
        for entity, component, vitals, heavy in targets:
            effects = [effect for effect in component.instances.values() if effect.graph.index == sigma.FLUX_EFFECT]
            self.assertEqual(initial[entity] - vitals.total, 50, hex(entity))
            if heavy:
                self.assertFalse(effects)
            else:
                effect, = effects
                self.assertEqual(effect.source_entity(), caster.body)
                self.assertEqual(effect.vars[41].value(), (expr.Entity(caster.body),))
                self.assertEqual(effect.states[12].counter, 1)
        self.assertTrue(enemy.mover.airborne)
        self.assertEqual(sum(actor.impulse_mover is not None and actor.impulse_mover.airborne for _, actor in actors), 4)
        for _ in range(260):
            run.step({})
        for entity, component, vitals, heavy in targets:
            expected = 50 if heavy else 50 + vitals.maximum * 0.5
            self.assertEqual(initial[entity] - vitals.total, expected, hex(entity))
            self.assertFalse(any(effect.graph.index == sigma.FLUX_EFFECT for effect in component.instances.values()))
        self.assertFalse(enemy.mover.airborne)
        self.assertTrue(all(actor.impulse_mover is None for _, actor in actors))
        for untouched in (ally, distant):
            self.assertEqual(health[untouched].total, health[untouched].maximum)
            self.assertFalse(untouched.mover.airborne)
            self.assertFalse(any(effect.graph.index == sigma.FLUX_EFFECT
                                 for effect in untouched.body_script.component.instances.values()))


if __name__ == "__main__":
    unittest.main()
