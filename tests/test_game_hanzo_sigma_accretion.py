"""Actual Sigma Accretion input and Hanzo's hosted control and impulse paths."""

import os
import unittest
from dataclasses import replace
from unittest.mock import patch

from test_game_combat import Run, aim, in_world
from test_game_hanzo_dragons import prepared_profile
from test_game_hanzo_sonic import profile
from test_game_mover import box, wall

from ow174.game import combat, hanzo_control, sigma_accretion, sonic, zarya_barrier
from ow174.game.collision import World, flat_world
from ow174.game.commands import JUMP
from ow174.game.content import SOLDIER, heroes
from ow174.game.physics_snapshot import snapshot_mover


def torso(player):
    x, y, z = player.mover.position
    return x, y + 0.85, z


class HanzoAccretionTests(unittest.TestCase):
    def arena(self, *, feet=(0, 0, 6), wall_z=None, allied=False):
        run = Run(names=("Hanzo", "Sigma"))
        match = run.match
        match.bots.clear()
        match._pending_bot_scripts.clear()
        match.game_map = replace(match.game_map, free_for_all=False)
        match.collision_world = (flat_world(0) if wall_z is None else
                                 World(box(-30, 30, -1, 0, -30, 30) + wall(-8, wall_z, 8, wall_z)))
        hanzo, sigma = run.players
        for player, hero, team, position in ((hanzo, 0x02E0000000000005, 0, feet),
                                             (sigma, sigma_accretion.HERO, 0 if allied else 1, (0, 0, 0))):
            self.assertTrue(match.switch_hero(player, heroes()[hero]))
            player.team = team
            player.mover.position[:] = position
            player.mover.body = None
        for _ in range(20):
            run.step({})
        return run, hanzo, sigma

    def launch_rock(self, run, sigma, point, *, held=None):
        fight = run.match.combat
        yaw, pitch = aim(fight._eye(sigma), point)
        held = held or {}
        run.step({**held, sigma: (yaw, pitch, 16)})
        for _ in range(100):
            run.step({**held, sigma: (yaw, pitch, 0)})
            rock = next((r for r in fight.rockets if r.accretion_instance is not None), None)
            if rock is not None:
                break
        self.assertIsNotNone(rock)
        self.assertEqual((rock.speed, rock.gravity, rock.direct.amount, rock.blast.amount), (37, 18, 30, 40))
        self.assertGreater(rock.direction[1], combat.aim(yaw, pitch)[1])
        self.assertEqual(rock.direct.knockback, (6, 3, ((3, 1, 1),)))
        return rock

    def follow_rock(self, run, rock, held=None):
        fight = run.match.combat
        for _ in range(60):
            if rock not in fight.rockets:
                break
            run.step(held or {})
        self.assertNotIn(rock, fight.rockets)

    def attack(self, run, hanzo, sigma, point, *, after_launch=None):
        bow = {hanzo: (-32768, 0, 1)}
        for _ in range(70):
            run.step(bow)
        rock = self.launch_rock(run, sigma, point, held=bow)
        if after_launch is not None:
            after_launch(bow)
        self.follow_rock(run, rock, bow)
        self.assertFalse(run.match.combat.rockets)
        return rock

    def test_direct_rock_displaces_and_interrupts_held_bow_until_control_ends(self):
        run, hanzo, sigma = self.arena()
        fight = run.match.combat
        self.attack(run, hanzo, sigma, fight._eye(hanzo))
        self.assertEqual(fight.vitals[hanzo.body].total, 130)
        deadline = fight.stuns[hanzo.body]
        self.assertEqual(deadline - fight.tick, 50)
        self.assertTrue(fight.is_stunned(hanzo, tick=deadline - 1))
        self.assertFalse(fight.is_stunned(hanzo, tick=deadline))
        self.assertTrue(hanzo.mover.external_impulse)
        snapshot = snapshot_mover(hanzo.mover)
        with patch.object(hanzo.mover, "external_impulse", False):
            self.assertNotEqual(snapshot_mover(hanzo.mover), snapshot)
        self.assertGreater(hanzo.mover.velocity[2], 4.5)
        self.assertGreater(hanzo.mover.position[1], 0)
        run.step({hanzo: (-32768, 0, 0)})
        self.assertFalse(fight.rockets)
        while run.frame < deadline:
            run.step({})
        self.assertFalse(fight.is_stunned(hanzo))
        self.assertGreater(hanzo.mover.position[2], 7)
        self.assertFalse(hanzo.mover.external_impulse)
        aim_back = aim(fight._eye(hanzo), torso(sigma))
        for _ in range(70):
            run.step({hanzo: (*aim_back, 1)})
        run.step({hanzo: (*aim_back, 0)})
        arrow, = fight.rockets
        self.assertEqual(arrow.direct.amount, 125)
        for _ in range(20):
            run.step({})
        self.assertEqual(fight.vitals[sigma.body].total, 275)

    def test_wall_splash_pushes_hanzo_without_cancelling_draw(self):
        run, hanzo, sigma = self.arena(feet=(1.3, 0, 3.6), wall_z=4)
        fight = run.match.combat
        pool = fight.vitals_of(next(t for t in fight._targets() if t.owner is hanzo))
        rock = self.attack(run, hanzo, sigma, (0, 1, 4))
        self.assertAlmostEqual(rock.position[2], 4, places=5)
        self.assertGreater(pool.total, 130)
        self.assertLess(pool.total, 200)
        self.assertGreater(hanzo.mover.velocity[0], 0)
        self.assertFalse(fight.is_stunned(hanzo))
        run.step({hanzo: (-32768, 0, 0)})
        arrow, = fight.rockets
        self.assertEqual(arrow.direct.amount, 125)

    def test_wall_occlusion_and_allied_contact_preserve_draw_and_health(self):
        for wall_z, allied in ((4.5, False), (None, True)):
            with self.subTest(wall=wall_z, allied=allied):
                run, hanzo, sigma = self.arena(wall_z=wall_z, allied=allied)
                fight = run.match.combat
                pool = fight.vitals_of(next(t for t in fight._targets() if t.owner is hanzo))
                self.attack(run, hanzo, sigma, fight._eye(hanzo))
                self.assertEqual(pool.total, 200)
                self.assertFalse(fight.is_stunned(hanzo))
                self.assertFalse(hanzo.mover.external_impulse)
                run.step({hanzo: (-32768, 0, 0)})
                arrow, = fight.rockets
                self.assertEqual(arrow.direct.amount, 125)

    def test_projected_barrier_cast_in_flight_prevents_damage_and_control_even_on_break(self):
        for initial in (200, 60):
            with self.subTest(initial=initial):
                run, hanzo, sigma = self.arena()
                match = run.match
                fight = match.combat
                ally = match.add_player(3, "Zarya", zarya_barrier.HERO, hanzo.team, False)
                in_world(match, ally)
                run.players.append(ally)
                self.assertTrue(match.switch_hero(ally, heroes()[zarya_barrier.HERO]))
                ally.team = hanzo.team
                ally.mover.position[:] = (2, 0, 6)
                ally.mover.body = None
                service = fight.zarya_barriers
                pool = fight.vitals_of(next(t for t in fight._targets() if t.owner is hanzo))

                def project(bow):
                    cast = aim(fight._eye(ally), fight._eye(hanzo))
                    run.step({**bow, ally: (*cast, 16)})
                    self.assertEqual(service.projected[ally.body][1:], (200, hanzo.body))
                    if initial < 200:
                        service.damage(hanzo, 200 - initial, sigma.body)
                        self.assertEqual(service.projected[ally.body][1], initial)

                self.attack(run, hanzo, sigma, fight._eye(hanzo), after_launch=project)
                shield = service.projected.get(ally.body)
                if initial == 200:
                    self.assertEqual(shield[1:], (130, hanzo.body))
                else:
                    self.assertTrue(shield is None or shield[1] == 0)
                self.assertEqual(pool.total, 200)
                self.assertFalse(fight.is_stunned(hanzo))
                self.assertFalse(hanzo.mover.external_impulse)
                run.step({hanzo: (-32768, 0, 0)})
                arrow, = fight.rockets
                self.assertEqual(arrow.direct.amount, 125)

    def test_lethal_direct_rock_retires_the_draw_without_control_on_replacement(self):
        run, hanzo, sigma = self.arena()
        fight = run.match.combat
        body = hanzo.body
        pool = fight.vitals_of(next(t for t in fight._targets() if t.owner is hanzo))
        pool.damage(180)
        with patch.object(combat, "respawn_seconds", return_value=40 * combat.TICK):
            self.attack(run, hanzo, sigma, fight._eye(hanzo))
            self.assertTrue(pool.dead)
            self.assertEqual(pool.killer, sigma.body)
            self.assertNotIn(body, fight.stuns)
            self.assertFalse(hanzo.mover.external_impulse)
            for _ in range(50):
                if hanzo.body != body:
                    break
                run.step({})
        self.assertNotEqual(hanzo.body, body)
        self.assertFalse(fight.rockets)
        self.assertFalse(fight.is_stunned(hanzo))
        self.assertFalse(hanzo.mover.external_impulse)
        fresh = next(t for t in fight._targets() if t.owner is hanzo)
        self.assertEqual(fight.vitals_of(fresh).total, 200)

    def test_rock_interrupt_preserves_prepared_sonic_until_fresh_release(self):
        run, hanzo, sigma = self.arena()
        fight = run.match.combat
        fight.configure_sonic(profile())
        component = hanzo.body_script.component
        selected = component.entity_var(627)
        ability = next(i for i in component.instances.values() if i.graph.index == 0x00C1)
        run.step({hanzo: (-32768, 0, 8)})
        run.step({})
        self.assertEqual(selected.value(), 1)
        self.attack(run, hanzo, sigma, fight._eye(hanzo))
        self.assertTrue(fight.is_stunned(hanzo))
        deadline = fight.stuns[hanzo.body]
        while run.frame < deadline:
            run.step({})
        self.assertFalse(fight.rockets)
        self.assertFalse(fight.sonars)
        self.assertEqual(selected.value(), 1)
        self.assertEqual(ability.vars[61].value(), 0)
        angles = aim(fight._eye(hanzo), torso(sigma))
        for _ in range(70):
            run.step({hanzo: (*angles, 1)})
        run.step({hanzo: (*angles, 0)})
        arrow, = fight.rockets
        self.assertIn(arrow.shot.definition, sonic.DEFINITIONS)
        self.assertGreater(ability.vars[61].value(), 11.9)
        for _ in range(20):
            run.step({})
        self.assertEqual(fight.vitals[sigma.body].total, 275)
        beacon, = fight.sonars
        self.assertEqual((beacon.source_entity, beacon.attached), (hanzo.body, sigma.body))

    def test_rock_during_paid_dragon_windup_cancels_without_refund_or_stale_launch(self):
        run, hanzo, sigma = self.arena()
        fight = run.match.combat
        fight.configure_dragonstrike(prepared_profile())
        rock = self.launch_rock(run, sigma, fight._eye(hanzo))
        script = hanzo.body_script
        script.ult_seconds = 0
        script.set_ult_charge(1680)
        run.step({hanzo: (-32768, 0, 32)})
        self.assertTrue(hanzo_control.casting(script))
        self.assertEqual(script.component.entity_var(342).value(), 0)
        self.follow_rock(run, rock)
        self.assertEqual(fight.vitals[hanzo.body].total, 130)
        self.assertTrue(fight.is_stunned(hanzo))
        self.assertFalse(hanzo_control.casting(script))
        for _ in range(140):
            run.step({})
            self.assertFalse(fight.rockets)
            self.assertFalse(fight.dragons)
        self.assertEqual(script.component.entity_var(342).value(), 0)
        script.set_ult_charge(1680)
        angles = aim(fight._eye(hanzo), fight._eye(sigma))
        run.step({hanzo: (*angles, 32)})
        for _ in range(100):
            run.step({})
            if fight.rockets:
                break
        arrow, = fight.rockets
        self.assertIsNotNone(arrow.continuation)
        self.assertEqual(arrow.source_entity, hanzo.body)

    def test_rock_after_first_storm_arrow_spends_volley_and_gates_early_retry(self):
        run, hanzo, sigma = self.arena()
        fight = run.match.combat
        storm = next(i for i in hanzo.body_script.component.instances.values() if i.graph.index == 0x146A)
        ability = storm.states[0]
        sigma_angles = aim(fight._eye(sigma), fight._eye(hanzo))
        hanzo_angles = aim(fight._eye(hanzo), torso(sigma))
        with patch.object(fight.projectiles, "fired", wraps=fight.projectiles.fired) as fired:
            def storm_shots():
                return [call.args[3] for call in fired.call_args_list if call.args[0] is hanzo]

            run.step({sigma: (*sigma_angles, 16)})
            for _ in range(28):
                run.step({sigma: (*sigma_angles, 0)})
            run.step({hanzo: (*hanzo_angles, 16)})
            run.step({})
            for _ in range(40):
                run.step({hanzo: (*hanzo_angles, 1)})
                if fight.is_stunned(hanzo):
                    break
            self.assertTrue(fight.is_stunned(hanzo))
            projectile = 0x040000000000270F
            self.assertEqual(storm_shots(), [projectile])
            self.assertFalse(ability.flags & 1)
            self.assertEqual(storm.find_var(0, 215).value(), 0)
            self.assertGreater(storm.find_var(0, 61).value(), 9.9)
            deadline = fight.stuns[hanzo.body]
            while run.frame < deadline:
                run.step({})
            self.assertEqual(fight.vitals[sigma.body].total, 330)
            self.assertEqual(fight.vitals[hanzo.body].total, 130)
            run.step({hanzo: (*hanzo_angles, 16)})
            run.step({})
            self.assertFalse(ability.flags & 1)
            for _ in range(650):
                if storm.find_var(0, 61).value() == 0:
                    break
                run.step({})
            self.assertEqual(storm.find_var(0, 61).value(), 0)
            self.assertFalse(ability.flags & 1)
            self.assertEqual(storm_shots(), [projectile])
            angles = aim(fight._eye(hanzo), torso(sigma))
            run.step({hanzo: (*angles, 16)})
            self.assertTrue(ability.flags & 1)
            for _ in range(50):
                run.step({hanzo: (*angles, 1)})
                if len(storm_shots()) == 2:
                    break
            self.assertEqual(storm_shots(), [projectile, projectile])
            for _ in range(10):
                run.step({})
            self.assertEqual(fight.vitals[sigma.body].total, 260)

    def test_rock_interrupts_active_wall_climb_and_collision_contains_displacement(self):
        run, hanzo, sigma = self.arena(wall_z=6.7)
        fight = run.match.combat
        rock = self.launch_rock(run, sigma, fight._eye(hanzo))
        for _ in range(5):
            run.step({})
        jump = {hanzo: (0, 0, JUMP)}
        run.step(jump)
        run.step(jump)
        climb = hanzo.body_script.world.hanzo_climb
        self.assertTrue(climb.active)
        active_at_impact = []
        interrupt = fight._interrupt

        def observe(target):
            if target.owner is hanzo:
                active_at_impact.append(climb.active)
            interrupt(target)

        with patch.object(fight, "_interrupt", observe):
            self.follow_rock(run, rock, jump)
        self.assertEqual(active_at_impact, [True])
        self.assertEqual(fight.vitals[hanzo.body].total, 130)
        self.assertFalse(climb.active)
        self.assertFalse(hanzo.body_script.component.entity_var(1204).value())
        self.assertTrue(fight.is_stunned(hanzo))
        self.assertTrue(hanzo.mover.external_impulse)
        for _ in range(8):
            run.step(jump)
            self.assertFalse(climb.active)
            self.assertLess(hanzo.mover.position[2], 6.7)

    def test_accretion_interrupts_mantle_and_preserves_the_external_impulse(self):
        from test_game_hanzo_movement import HanzoLungeTests

        run, hanzo, sigma = self.arena()
        x, y, z = hanzo.mover.position
        run.match.collision_world = World(
            box(x - 20, x + 20, y - 1, y, z - 20, z + 20)
            + box(x - 5, x + 5, y, y + 1.9, z + 0.7, z + 8),
        )
        fight = run.match.combat
        rock = self.launch_rock(run, sigma, fight._eye(hanzo))
        for _ in range(5):
            run.step({})
        for _ in range(2):
            HanzoLungeTests.step(run, hanzo, buttons=JUMP, forward=127)
        progress = hanzo.body_script.world.hanzo_climb
        self.assertIsNotNone(progress.mantle)
        active_at_impact = []
        interrupt = fight._interrupt

        def observe(target):
            if target.owner is hanzo:
                active_at_impact.append(progress.mantle is not None)
            interrupt(target)

        with patch.object(fight, "_interrupt", observe):
            self.follow_rock(run, rock, {hanzo: (0, 0, JUMP)})
        self.assertEqual(active_at_impact, [True])
        self.assertEqual(fight.vitals[hanzo.body].total, 130)
        self.assertTrue(fight.is_stunned(hanzo))
        self.assertIsNone(progress.mantle)
        self.assertTrue(hanzo.mover.external_impulse)
        self.assertGreater(hanzo.mover.velocity[2], 0)
        for _ in range(8):
            run.step({hanzo: (0, 0, JUMP)})
            self.assertIsNone(progress.mantle)
            self.assertLess(hanzo.mover.position[2], z + 0.7)

    def test_accretion_contact_after_high_ledge_handoff(self):
        from test_game_hanzo_movement import HanzoLungeTests

        for shield_hp in (None, 200, 60):
            with self.subTest(shield_hp=shield_hp):
                run, hanzo, sigma = self.arena(feet=(0, 0, 18))
                match = run.match
                fight = match.combat
                x, y, z = hanzo.mover.position
                match.collision_world = World(
                    box(x - 20, x + 20, y - 1, y, -25, z + 20)
                    + box(x - 5, x + 5, y, y + 3, z + 0.7, z + 8),
                )
                hanzo.mover.use(world=match.collision_world)
                if shield_hp is not None:
                    ally = match.add_player(3, "Zarya", zarya_barrier.HERO, hanzo.team, False)
                    in_world(match, ally)
                    run.players.append(ally)
                    self.assertTrue(match.switch_hero(ally, heroes()[zarya_barrier.HERO]))
                    ally.team = hanzo.team
                    ally.mover.position[:] = (x + 2, y, z)
                    ally.mover.body = None
                    cast = aim(fight._eye(ally), fight._eye(hanzo))
                    run.step({ally: (*cast, 16)})
                    barriers = fight.zarya_barriers
                    self.assertEqual(barriers.projected[ally.body][1:], (200, hanzo.body))
                    if shield_hp < 200:
                        barriers.damage(hanzo, 200 - shield_hp, sigma.body)

                rock = self.launch_rock(run, sigma, (x, y + 4.1, z))
                progress = hanzo.body_script.world.hanzo_climb
                climbed = False
                for _ in range(40):
                    route_at_contact = progress.mantle
                    climbed = climbed or progress.active
                    HanzoLungeTests.step(run, hanzo, buttons=JUMP, forward=127)
                    if rock not in fight.rockets:
                        break
                self.assertNotIn(rock, fight.rockets)
                self.assertTrue(climbed)
                self.assertIsNotNone(route_at_contact)
                self.assertGreater(progress.elapsed, 0)

                pool = fight.vitals[hanzo.body]
                if shield_hp is None:
                    self.assertEqual(pool.total, 130)
                    self.assertEqual(fight.stuns[hanzo.body] - fight.tick, 50)
                    self.assertIsNone(progress.mantle)
                    self.assertFalse(hanzo.mover.driven)
                    self.assertTrue(hanzo.mover.external_impulse)
                    self.assertGreater(hanzo.mover.velocity[2], 5)
                    self.assertGreater(hanzo.mover.velocity[1], 2)
                    impact_z = hanzo.mover.position[2]
                    for _ in range(8):
                        HanzoLungeTests.step(run, hanzo, buttons=JUMP, forward=127)
                        self.assertIsNone(progress.mantle)
                        self.assertFalse(hanzo.mover.driven)
                        self.assertTrue(fight.is_stunned(hanzo))
                    self.assertGreater(hanzo.mover.position[2], impact_z)
                else:
                    self.assertEqual(barriers.projected.get(ally.body, (None, 0))[1],
                                     max(0, shield_hp - 70))
                    self.assertIsNotNone(progress.mantle)
                    self.assertFalse(fight.is_stunned(hanzo))
                    self.assertFalse(hanzo.mover.external_impulse)
                    for _ in range(38):
                        HanzoLungeTests.step(run, hanzo, buttons=JUMP, forward=127)
                        if progress.mantle is None:
                            break
                    self.assertIsNone(progress.mantle)
                    self.assertFalse(hanzo.mover.airborne)
                    self.assertAlmostEqual(hanzo.mover.position[1], y + 3, delta=0.01)
                    self.assertGreater(hanzo.mover.position[2], z + 1)
                    self.assertEqual(pool.total, 200)

    def test_rock_hits_after_lunge_and_does_not_refund_its_cooldown(self):
        run, hanzo, sigma = self.arena()
        fight = run.match.combat
        aim_rock = aim(fight._eye(sigma), (0, 2.1, 6))
        world = hanzo.body_script.world
        movement = next(i for i in hanzo.body_script.component.instances.values() if i.graph.index == 0x010C)
        cooldown = movement.vars[61]
        jump = {hanzo: (-32768, 0, JUMP)}
        with patch.object(world, "velocity_action", wraps=world.velocity_action) as launches:
            run.step({sigma: (*aim_rock, 16)})
            for _ in range(30):
                run.step({sigma: (*aim_rock, 0)})
            run.step(jump)
            for _ in range(14):
                run.step({})
            self.assertTrue(fight.rockets)
            run.step(jump)
            self.assertEqual(launches.call_count, 1)
            self.assertLess(hanzo.mover.velocity[2], -14)
            for _ in range(25):
                if fight.is_stunned(hanzo):
                    break
                run.step({})
            self.assertTrue(fight.is_stunned(hanzo))
            self.assertEqual(fight.vitals[hanzo.body].total, 130)
            self.assertTrue(hanzo.mover.external_impulse)
            for _ in range(8):
                run.step(jump)
            self.assertGreater(cooldown.value(), 4.8)
            self.assertLess(cooldown.value(), 5)
            self.assertEqual(launches.call_count, 1)
            for _ in range(400):
                if cooldown.value() == 0:
                    break
                run.step({})
            self.assertEqual(cooldown.value(), 0)
            self.assertFalse(fight.is_stunned(hanzo))
            run.step(jump)
            for _ in range(14):
                run.step({})
            run.step(jump)
            self.assertEqual(launches.call_count, 2)
            self.assertLess(hanzo.mover.velocity[2], -14)

    def test_hanzo_kill_during_rock_windup_or_flight_preserves_committed_source(self):
        for launched in (False, True):
            with self.subTest(launched=launched):
                run, hanzo, sigma = self.arena()
                fight = run.match.combat
                targets = {t.owner: t for t in fight._targets()}
                sigma_pool = fight.vitals_of(targets[sigma])
                hanzo_pool = fight.vitals_of(targets[hanzo])
                sigma_pool.damage(sigma_pool.total - 20)
                original = sigma.body
                sigma.body_script.ult_seconds = 0
                sigma.body_script.set_ult_charge(73)
                charge = sigma.body_script.component.entity_var(342)
                bow_angles = aim(fight._eye(hanzo), fight._eye(sigma))
                bow = {hanzo: (*bow_angles, 1)}
                for _ in range(70):
                    run.step(bow)
                rock_angles = aim(fight._eye(sigma), fight._eye(hanzo))
                if launched:
                    rock = self.launch_rock(run, sigma, fight._eye(hanzo), held=bow)
                    self.assertEqual(rock.source_entity, original)
                else:
                    run.step({**bow, sigma: (*rock_angles, 16)})
                    for _ in range(10):
                        run.step(bow)
                    self.assertFalse(fight.rockets)

                with patch.object(combat, "respawn_seconds", return_value=80 * combat.TICK):
                    run.step({hanzo: (*bow_angles, 0)})
                    for _ in range(8):
                        run.step({})
                        if sigma_pool.dead:
                            break
                    self.assertTrue(sigma_pool.dead)
                    self.assertEqual(sigma_pool.killer, hanzo.body)
                    self.assertEqual(hanzo_pool.total, 200)
                    if launched:
                        self.assertIn(rock, fight.rockets)
                        self.follow_rock(run, rock)
                        self.assertEqual(hanzo_pool.total, 130)
                        self.assertEqual(fight.stuns[hanzo.body] - fight.tick, 50)
                        self.assertTrue(hanzo.mover.external_impulse)
                    self.assertEqual(charge.value(), 73)
                    for _ in range(90):
                        if sigma.body != original:
                            break
                        run.step({})
                        self.assertFalse(fight.rockets)
                self.assertNotEqual(sigma.body, original)
                self.assertEqual(hanzo_pool.total, 130 if launched else 200)

    def downrange_soldier(self, run, team):
        match = run.match
        target = match.add_player(3, "Downrange Soldier", SOLDIER, team, False)
        in_world(match, target)
        run.players.append(target)
        self.assertTrue(match.switch_hero(target, heroes()[SOLDIER]))
        target.team = team
        target.mover.position[:] = (0, 0, 30)
        target.mover.body = None
        return target

    def test_committed_bow_and_sonic_survive_lethal_rock_with_original_source(self):
        for sonar in (False, True):
            with self.subTest(sonar=sonar):
                run, hanzo, sigma = self.arena()
                fight = run.match.combat
                target = self.downrange_soldier(run, sigma.team)
                old = hanzo.body
                pool = fight.vitals_of(next(t for t in fight._targets() if t.owner is hanzo))
                pool.damage(180)
                if sonar:
                    fight.configure_sonic(profile())
                    run.step({hanzo: (0, 0, 8)})
                    run.step({})
                target_angles = aim(fight._eye(hanzo), fight._eye(target))
                bow = {hanzo: (*target_angles, 1)}
                for _ in range(70):
                    run.step(bow)
                rock = self.launch_rock(run, sigma, fight._eye(hanzo), held=bow)
                for _ in range(7):
                    run.step(bow)
                self.assertIn(rock, fight.rockets)
                run.step({hanzo: (*target_angles, 0)})
                arrow, = [r for r in fight.rockets if r is not rock]
                self.assertEqual(arrow.source_entity, old)
                self.assertEqual(arrow.direct.amount, 125)
                self.assertEqual(arrow.sonar is not None, sonar)
                self.follow_rock(run, rock)
                self.assertTrue(pool.dead)
                self.assertEqual(pool.killer, sigma.body)
                self.assertIn(arrow, fight.rockets)
                self.follow_rock(run, arrow)
                self.assertEqual(fight.vitals[target.body].total, 75)
                if sonar:
                    beacon, = fight.sonars
                    self.assertEqual((beacon.source_entity, beacon.attached), (old, target.body))
                else:
                    self.assertFalse(fight.sonars)

    def test_lethal_rock_preserves_dragonstrike_after_arrow_launch_and_transformation(self):
        for already_transformed in (False, True):
            with self.subTest(already_transformed=already_transformed):
                run, hanzo, sigma = self.arena()
                fight = run.match.combat
                target = self.downrange_soldier(run, sigma.team)
                fight.configure_dragonstrike(prepared_profile())
                old = hanzo.body
                targets = {t.owner: t for t in fight._targets()}
                pool = fight.vitals_of(targets[hanzo])
                victim = fight.vitals_of(targets[target])
                pool.damage(180)
                script = hanzo.body_script
                script.ult_seconds = 0
                script.set_ult_charge(1680)
                angles = aim(fight._eye(sigma), fight._eye(hanzo))
                delay = 50 if already_transformed else 16
                arrow = flight = None
                run.step({hanzo: (0, 0, 32)})
                for frame in range(1, 130):
                    run.step({sigma: (*angles, 16)} if frame == delay else {})
                    if arrow is None:
                        arrow = next((r for r in fight.rockets if r.continuation is not None), None)
                    if fight.dragons and flight is None:
                        flight, = fight.dragons
                    if pool.dead:
                        break
                self.assertTrue(pool.dead)
                self.assertEqual(pool.killer, sigma.body)
                self.assertIsNotNone(arrow)
                self.assertEqual(arrow.source_entity, old)
                self.assertEqual(flight is not None, already_transformed)
                self.assertEqual(arrow in fight.rockets, not already_transformed)
                self.assertEqual(script.component.entity_var(342).value(), 0)
                self.assertEqual(victim.total, 200)
                for _ in range(140):
                    run.step({})
                    if victim.total < 200:
                        break
                self.assertLess(victim.total, 200)
                active, = fight.dragons
                self.assertEqual(active.source_entity, old)
                self.assertIs(active.shooter, hanzo)
                if flight is not None:
                    self.assertIs(active, flight)

    def test_inflight_rock_control_deadline_agrees_across_gap_without_barrier_asset(self):
        outcomes = []
        with patch.dict(os.environ, {"ORPHEUS_SIGMA_BARRIER_MODEL": ""}):
            for skipped in (False, True):
                with self.subTest(skipped=skipped):
                    run, hanzo, sigma = self.arena()
                    fight = run.match.combat
                    self.assertIsNone(fight.sigma_barriers.geometry)
                    rock = self.launch_rock(run, sigma, fight._eye(hanzo))
                    run.step({})
                    self.assertEqual(rock.last_tick, run.frame)
                    start = run.frame
                    for delta in ((40,) if skipped else (1,) * 40):
                        run.frame += delta
                        run.match.update(run.frame * combat.TICK, run.frame)
                    self.assertFalse(fight.rockets)
                    self.assertEqual(fight.vitals[hanzo.body].total, 130)
                    self.assertEqual(fight.stuns[hanzo.body] - start, 59)
                    self.assertTrue(hanzo.mover.external_impulse)
                    outcomes.append((rock.position, hanzo.mover.velocity))
        for continuous, jumped in zip(*outcomes, strict=True):
            for a, b in zip(continuous, jumped, strict=True):
                self.assertAlmostEqual(a, b, places=6)
