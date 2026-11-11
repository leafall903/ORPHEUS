"""Actual Sigma Flux inputs and Hanzo interactions."""
import unittest
from dataclasses import replace
from unittest.mock import patch

from test_game_combat import aim, in_world
from test_game_hanzo_dragons import prepared_profile
from test_game_hanzo_sonic import profile
from test_game_mover import box
import test_game_hanzo_sigma_accretion as accretion_tests
from ow174.game import combat, hanzo_control, sonic, zarya_barrier
from ow174.game.collision import World
from ow174.game.commands import CROUCH, JUMP
from ow174.game.content import SOLDIER, heroes
from ow174.game.script import nodes


class HanzoFluxTests(unittest.TestCase):
    def cast(self, run, hanzo, sigma, point, *, draw=False):
        fight = run.match.combat
        script = sigma.body_script
        script.ult_seconds = 0
        self.assertEqual(script.component.entity_var(338).value(), 1960)
        script.set_ult_charge(1960)
        flux = next(i for i in script.component.instances.values() if i.graph.index == 0x1E41)
        for frame in range(81):
            yaw, pitch = aim(fight._eye(sigma), point)
            commands = {sigma: (yaw, pitch, 32 if frame == 0 else 1 if frame == 80 else 0)}
            if draw:
                commands[hanzo] = (-32768, 0, 1)
            run.step(commands)
        self.assertEqual(script.component.entity_var(342).value(), 0)
        self.assertEqual(flux.vars[59].value(), 2)
        placed = flux.vars[2478].value()
        for actual, expected in zip(placed, point, strict=True):
            self.assertAlmostEqual(actual, expected, delta=0.005)
        return flux

    def test_hostile_hanzo_receives_entry_lift_and_landing_damage(self):
        run, hanzo, sigma = accretion_tests.HanzoAccretionTests().arena()
        fight = run.match.combat
        pool = fight.vitals_of(next(t for t in fight._targets() if t.owner is hanzo))
        flux = self.cast(run, hanzo, sigma, tuple(hanzo.mover.position), draw=True)
        for _ in range(200):
            run.step({hanzo: (-32768, 0, 1)})
            if hanzo.mover.position[1] > 3:
                break
        self.assertEqual(pool.total, 150)
        self.assertGreater(hanzo.mover.position[1], 3)
        self.assertEqual(flux.vars[59].value(), 3)
        effect, = [i for i in hanzo.body_script.component.instances.values() if i.graph.index == 0x1DBC]
        self.assertEqual(effect.source_entity(), sigma.body)
        x, y, z = sigma.mover.position
        yaw, pitch = aim(fight._eye(hanzo), (x, y + 0.9, z))
        run.step({hanzo: (yaw, pitch, 0)})
        arrow, = fight.rockets
        self.assertEqual(arrow.direct.amount, 125)
        self.assertGreater(arrow.position[1], 3)
        for _ in range(430):
            run.step({})
            if pool.total < 150:
                break
        self.assertEqual(pool.total, 50)
        self.assertFalse(hanzo.mover.airborne)
        for _ in range(60):
            run.step({})
        self.assertNotIn(effect.id, hanzo.body_script.component.instances)
        self.assertEqual(pool.total, 50)
        self.assertEqual(fight.vitals[sigma.body].total, 275)

    def test_ally_wall_and_outside_authored_volume_are_excluded(self):
        for name, options, point in (
            ("ally", {"allied": True}, (0, 0, 6)),
            ("wall", {"wall_z": 4}, (0, 0, 3.5)),
            ("outside", {}, (0, 0, 18)),
        ):
            with self.subTest(name=name):
                run, hanzo, sigma = accretion_tests.HanzoAccretionTests().arena(**options)
                fight = run.match.combat
                pool = fight.vitals_of(next(t for t in fight._targets() if t.owner is hanzo))
                flux = self.cast(run, hanzo, sigma, point)
                for _ in range(210):
                    run.step({})
                self.assertEqual(flux.vars[59].value(), 4)
                self.assertEqual(pool.total, 200)
                self.assertFalse(hanzo.mover.airborne)
                self.assertFalse(any(i.graph.index == 0x1DBC for i in hanzo.body_script.component.instances.values()))


    def test_caster_moves_vertically_while_selecting_then_lands_after_timeout(self):
        run, hanzo, sigma = accretion_tests.HanzoAccretionTests().arena()
        fight = run.match.combat
        script = sigma.body_script
        script.ult_seconds = 0
        script.set_ult_charge(script.component.entity_var(338).value())
        flux = next(i for i in script.component.instances.values() if i.graph.index == 0x1E41)
        yaw, pitch = aim(fight._eye(sigma), tuple(hanzo.mover.position))
        run.step({sigma: (yaw, pitch, 32)})
        for _ in range(80):
            run.step({})
        self.assertTrue(flux.states[13].active)
        self.assertEqual(flux.vars[59].value(), 1)
        self.assertEqual(sigma.mover.gravity_scale, 0)
        initial = sigma.mover.position[1]
        self.assertGreater(initial, 2)
        for _ in range(10):
            run.step({sigma: (0, 0, JUMP)})
        elevated = sigma.mover.position[1]
        self.assertGreater(elevated - initial, 1)
        for _ in range(10):
            run.step({})
        self.assertEqual(sigma.mover.position[1], elevated)
        for _ in range(10):
            run.step({sigma: (0, 0, CROUCH)})
        self.assertAlmostEqual(sigma.mover.position[1], initial, places=2)
        for _ in range(450):
            run.step({})
        self.assertFalse(flux.states[0].flags & nodes.Ability.RUNNING)
        self.assertFalse(sigma.mover.airborne)
        self.assertAlmostEqual(sigma.mover.position[1], 0, places=3)
        target = next(t for t in fight._targets() if t.owner is hanzo)
        self.assertEqual(fight.vitals_of(target).total, 200)

    def test_bow_kills_caster_before_or_after_flux_descent_commit(self):
        for early in (True, False):
            with self.subTest(early=early):
                run, hanzo, sigma = accretion_tests.HanzoAccretionTests().arena()
                fight = run.match.combat
                targets = {t.owner: t for t in fight._targets()}
                health = fight.vitals_of(targets[hanzo])
                caster = fight.vitals_of(targets[sigma])
                caster.damage(caster.total - 20)
                self.cast(run, hanzo, sigma, tuple(hanzo.mover.position), draw=True)
                receiver = hanzo.body_script.component
                for _ in range(245):
                    run.step({hanzo: (-32768, 0, 1)})
                    if hanzo.mover.position[1] > 3 and (early or hanzo.mover.velocity[1] < -1):
                        break
                effect, = [i for i in receiver.instances.values() if i.graph.index == 0x1DBC]
                self.assertEqual(effect.states[16].active, early)
                self.assertEqual(health.total, 150)
                yaw, pitch = aim(fight._eye(hanzo), fight._eye(sigma))
                run.step({hanzo: (yaw, pitch, 0)})
                arrow, = fight.rockets
                self.assertEqual(arrow.direct.amount, 125)
                for _ in range(20):
                    run.step({})
                    if caster.dead:
                        break
                self.assertTrue(caster.dead)
                self.assertEqual(caster.killer, hanzo.body)
                self.assertEqual(effect.id in receiver.instances, not early)
                for _ in range(250):
                    run.step({})
                self.assertFalse(hanzo.mover.airborne)
                self.assertEqual(health.total, 150 if early else 50)
                self.assertNotIn(effect.id, receiver.instances)


    def test_projected_barrier_absorbs_flux_entry_or_slam_without_canceling_lift(self):
        for early in (True, False):
            with self.subTest(early=early):
                run, hanzo, sigma = accretion_tests.HanzoAccretionTests().arena()
                match = run.match
                fight = match.combat
                pool = fight.vitals_of(next(t for t in fight._targets() if t.owner is hanzo))
                ally = match.add_player(3, "Zarya", zarya_barrier.HERO, hanzo.team, False)
                in_world(match, ally)
                run.players.append(ally)
                self.assertTrue(match.switch_hero(ally, heroes()[zarya_barrier.HERO]))
                ally.team = hanzo.team
                ally.mover.position[:] = (13, 0, 6)
                ally.mover.body = None
                self.cast(run, hanzo, sigma, tuple(hanzo.mover.position))
                if early:
                    for _ in range(52):
                        run.step({})
                    self.assertEqual(pool.total, 200)
                else:
                    for _ in range(370):
                        run.step({})
                        if (hanzo.mover.airborne and hanzo.mover.position[1] < 4
                                and hanzo.mover.velocity[1] < -1):
                            break
                    self.assertEqual(pool.total, 150)
                    self.assertTrue(hanzo.mover.airborne)
                angles = aim(fight._eye(ally), fight._eye(hanzo))
                run.step({ally: (*angles, 16)})
                service = fight.zarya_barriers
                self.assertEqual(service.projected[ally.body][1:], (200, hanzo.body))
                expected = 150 if early else 100
                for _ in range(120):
                    if service.projected.get(ally.body, (0, 0))[1] == expected:
                        break
                    run.step({})
                self.assertEqual(service.projected[ally.body][1:], (expected, hanzo.body))
                self.assertEqual(pool.total, 200 if early else 150)
                if early:
                    for _ in range(70):
                        run.step({})
                        if hanzo.mover.position[1] > 3:
                            break
                    self.assertGreater(hanzo.mover.position[1], 3)
                else:
                    self.assertFalse(hanzo.mover.airborne)

    def test_lethal_entry_or_landing_retires_effect_before_respawn(self):
        for entry in (True, False):
            with self.subTest(entry=entry):
                run, hanzo, sigma = accretion_tests.HanzoAccretionTests().arena()
                fight = run.match.combat
                pool = fight.vitals_of(next(t for t in fight._targets() if t.owner is hanzo))
                pool.damage(155 if entry else 80)
                old = hanzo.body
                receiver = hanzo.body_script.component
                self.cast(run, hanzo, sigma, tuple(hanzo.mover.position))
                peak = 0.0
                with patch.object(combat, "respawn_seconds", return_value=60 * combat.TICK):
                    for _ in range(480):
                        run.step({})
                        peak = max(peak, hanzo.mover.position[1])
                        if pool.dead:
                            break
                    self.assertTrue(pool.dead)
                    self.assertEqual(pool.killer, sigma.body)
                    if entry:
                        self.assertEqual(peak, 0)
                    else:
                        self.assertGreater(peak, 3)
                        self.assertFalse(hanzo.mover.airborne)
                    self.assertFalse(any(i.graph.index == 0x1DBC for i in receiver.instances.values()))
                    for _ in range(80):
                        if hanzo.body != old:
                            break
                        run.step({})
                self.assertNotEqual(hanzo.body, old)
                self.assertFalse(any(i.graph.index == 0x1DBC for i in hanzo.body_script.component.instances.values()))
                fresh = next(t for t in fight._targets() if t.owner is hanzo)
                self.assertEqual(fight.vitals_of(fresh).total, 200)

    def wait_for_lift(self, run, hanzo, *, buttons=0):
        fight = run.match.combat
        for _ in range(200):
            run.step({hanzo: (-32768, 0, buttons)})
            if hanzo.mover.position[1] > 3:
                break
        self.assertGreater(hanzo.mover.position[1], 3)
        self.assertEqual(fight.vitals[hanzo.body].total, 150)
        self.assertFalse(fight.is_stunned(hanzo))

    def test_hero_switch_retires_flux_on_the_old_hanzo_body(self):
        for descending in (False, True):
            with self.subTest(descending=descending):
                run, hanzo, sigma = accretion_tests.HanzoAccretionTests().arena()
                match = run.match
                fight = match.combat
                self.cast(run, hanzo, sigma, tuple(hanzo.mover.position))
                self.wait_for_lift(run, hanzo)
                if descending:
                    for _ in range(200):
                        run.step({})
                        if hanzo.mover.velocity[1] < -10:
                            break
                    self.assertLess(hanzo.mover.velocity[1], -10)
                self.assertEqual(fight.vitals[hanzo.body].total, 150)
                old_body = hanzo.body
                old_script = hanzo.body_script
                self.assertTrue(any(i.graph.index == 0x1DBC for i in old_script.component.instances.values()))
                self.assertTrue(match.switch_hero(hanzo, heroes()[SOLDIER]))
                self.assertTrue(old_script.retired)
                self.assertIsNone(old_script.world.match)
                self.assertNotEqual(hanzo.body, old_body)
                self.assertNotIn(old_body, fight.vitals)
                old_frame = old_script.frame
                for _ in range(260):
                    run.step({})
                self.assertEqual(old_script.frame, old_frame)
                self.assertFalse(hanzo.mover.airborne)
                self.assertFalse(any(i.graph.index == 0x1DBC for i in hanzo.body_script.component.instances.values()))
                fresh = next(t for t in fight._targets() if t.owner is hanzo)
                self.assertEqual(fight.vitals_of(fresh).total, 200)
                self.assertFalse(fight.is_stunned(hanzo))
                run.step({hanzo: (0, 0, JUMP)})
                self.assertGreater(hanzo.mover.velocity[1], 4)

    def test_flux_effect_cannot_rebind_to_a_replacement_caster(self):
        for descending in (False, True):
            with self.subTest(descending=descending):
                run, hanzo, sigma = accretion_tests.HanzoAccretionTests().arena()
                match = run.match
                fight = match.combat
                self.cast(run, hanzo, sigma, tuple(hanzo.mover.position))
                self.wait_for_lift(run, hanzo)
                if descending:
                    for _ in range(200):
                        run.step({})
                        if hanzo.mover.velocity[1] < -10:
                            break
                    self.assertLess(hanzo.mover.velocity[1], -10)
                pool = fight.vitals[hanzo.body]
                self.assertEqual(pool.total, 150)
                old_body, sigma_hero = sigma.body, sigma.hero
                component = hanzo.body_script.component
                effect, = [i for i in component.instances.values() if i.graph.index == 0x1DBC]
                self.assertEqual(effect.source_entity(), old_body)
                self.assertTrue(match.switch_hero(sigma, heroes()[SOLDIER]))
                self.assertTrue(match.switch_hero(sigma, sigma_hero))
                self.assertNotEqual(sigma.body, old_body)
                for _ in range(400):
                    run.step({})
                    if not hanzo.mover.airborne and effect.id not in component.instances:
                        break
                self.assertNotIn(effect.id, component.instances)
                self.assertFalse(hanzo.mover.airborne)
                self.assertEqual(pool.total, 150)

    def test_prepared_sonic_fires_from_lifted_pose_and_reveals_sigma(self):
        run, hanzo, sigma = accretion_tests.HanzoAccretionTests().arena()
        fight = run.match.combat
        fight.configure_sonic(profile())
        component = hanzo.body_script.component
        selected = component.entity_var(627)
        ability = next(i for i in component.instances.values() if i.graph.index == 0x00C1)
        run.step({hanzo: (-32768, 0, 8)})
        run.step({})
        self.cast(run, hanzo, sigma, tuple(hanzo.mover.position), draw=True)
        self.wait_for_lift(run, hanzo, buttons=1)
        self.assertEqual(selected.value(), 1)
        self.assertEqual(ability.vars[61].value(), 0)
        x, y, z = sigma.mover.position
        angles = aim(fight._eye(hanzo), (x, y + 0.9, z))
        run.step({hanzo: (*angles, 0)})
        arrow, = fight.rockets
        self.assertIn(arrow.shot.definition, sonic.DEFINITIONS)
        self.assertGreater(arrow.position[1], 3)
        self.assertEqual(arrow.direct.amount, 125)
        self.assertGreater(ability.vars[61].value(), 11.9)
        for _ in range(25):
            run.step({})
            if fight.sonars:
                break
        beacon, = fight.sonars
        self.assertEqual((beacon.source_entity, beacon.attached), (hanzo.body, sigma.body))
        self.assertEqual(fight.vitals[sigma.body].total, 275)
        self.assertTrue(fight.revealed_to(hanzo, sigma))

    def test_all_five_storm_arrows_fire_during_lift_and_start_cooldown(self):
        run, hanzo, sigma = accretion_tests.HanzoAccretionTests().arena()
        fight = run.match.combat
        self.cast(run, hanzo, sigma, tuple(hanzo.mover.position))
        self.wait_for_lift(run, hanzo)
        storm = next(i for i in hanzo.body_script.component.instances.values() if i.graph.index == 0x146A)
        with patch.object(fight.projectiles, "fired", wraps=fight.projectiles.fired) as fired:
            x, y, z = sigma.mover.position
            angles = aim(fight._eye(hanzo), (x, y + 0.35, z))
            run.step({hanzo: (*angles, 16)})
            run.step({})
            for _ in range(140):
                x, y, z = sigma.mover.position
                angles = aim(fight._eye(hanzo), (x, y + 0.35, z))
                run.step({hanzo: (*angles, 1)})
                if fired.call_count == 5:
                    break
            shots = [call.args for call in fired.call_args_list]
            self.assertEqual([shot[3] for shot in shots], [0x040000000000270F] * 5)
            self.assertTrue(all(shot[0] is hanzo and shot[5][1] > 3 for shot in shots))
            for _ in range(24):
                run.step({})
            self.assertEqual(fired.call_count, 5)
        self.assertEqual(storm.find_var(0, 215).value(), 0)
        self.assertGreater(storm.find_var(0, 61).value(), 9)
        self.assertEqual(fight.vitals[sigma.body].total, 50)
        self.assertEqual([(row["+0x78"]["+0x4"], row["+0x78"]["+0x8"])
                          for row in hanzo.client.messages(combat.HIT)], [(70, 0)] * 5)

    def test_dragonstrike_can_start_and_launch_during_flux_lift(self):
        run, hanzo, sigma = accretion_tests.HanzoAccretionTests().arena()
        fight = run.match.combat
        fight.configure_dragonstrike(prepared_profile())
        self.cast(run, hanzo, sigma, tuple(hanzo.mover.position))
        self.wait_for_lift(run, hanzo)
        script = hanzo.body_script
        script.ult_seconds = 0
        script.set_ult_charge(1680)
        angles = aim(fight._eye(hanzo), fight._eye(sigma))
        run.step({hanzo: (*angles, 32)})
        self.assertTrue(hanzo_control.casting(script))
        self.assertEqual(script.component.entity_var(342).value(), 0)
        for _ in range(100):
            angles = aim(fight._eye(hanzo), fight._eye(sigma))
            run.step({hanzo: (*angles, 0)})
            if fight.rockets:
                break
        arrow, = fight.rockets
        self.assertIsNotNone(arrow.continuation)
        self.assertEqual(arrow.source_entity, hanzo.body)
        self.assertGreater(arrow.position[1], 3)
        self.assertTrue(hanzo.mover.airborne)
        for _ in range(110):
            run.step({})
            if fight.dragons:
                break
        flight, = fight.dragons
        self.assertEqual(flight.source_entity, hanzo.body)
        self.assertLess(fight.vitals[sigma.body].total, 400)

    def test_lethal_flux_landing_retires_sonic_selection_and_storm_volley(self):
        sonic_profile = profile()
        landing = None
        for stage in ("baseline", "prepared_sonic", "released_sonic", "released_storm"):
            with self.subTest(stage=stage):
                run, hanzo, sigma = accretion_tests.HanzoAccretionTests().arena()
                match = run.match
                fight = match.combat
                fight.configure_sonic(sonic_profile)
                soldier = match.add_player(3, "Downrange Soldier", SOLDIER, sigma.team, False)
                in_world(match, soldier)
                run.players.append(soldier)
                self.assertTrue(match.switch_hero(soldier, heroes()[SOLDIER]))
                soldier.team = sigma.team
                soldier.mover.position[:] = (0, 0, 31)
                soldier.mover.body = None
                targets = {t.owner: t for t in fight._targets()}
                hanzo_pool = fight.vitals_of(targets[hanzo])
                soldier_pool = fight.vitals_of(targets[soldier])
                hanzo_pool.damage(80)
                if stage == "released_storm":
                    soldier_pool.damage(130)
                soldier_health = soldier_pool.total
                old_body, script = hanzo.body, hanzo.body_script
                selected = script.component.entity_var(627)
                storm = next(i for i in script.component.instances.values() if i.graph.index == 0x146A)
                if stage in ("prepared_sonic", "released_sonic"):
                    run.step({hanzo: (0, 0, 8)})
                    run.step({})
                    self.assertEqual(selected.value(), 1)
                self.cast(run, hanzo, sigma, tuple(hanzo.mover.position))

                arrow = None
                with patch.object(fight.projectiles, "fired", wraps=fight.projectiles.fired) as fired:
                    for frame in range(1, 480):
                        button = 0
                        if stage == "prepared_sonic" and frame >= landing - 90:
                            button = 1
                        elif stage == "released_sonic" and landing - 90 <= frame < landing - 3:
                            button = 1
                        elif stage == "released_storm":
                            if frame == landing - 90:
                                button = 16
                            elif frame >= landing - 3:
                                button = 1
                        angles = aim(fight._eye(hanzo), fight._eye(soldier))
                        run.step({hanzo: (*angles, button)})
                        if stage == "prepared_sonic" and frame == landing - 1:
                            self.assertEqual(selected.value(), 1)
                        if stage == "released_storm" and frame == landing - 1:
                            remaining = storm.find_var(0, 215).value()
                            self.assertGreater(remaining, 3)
                        if fight.rockets and arrow is None:
                            arrow, = fight.rockets
                        if hanzo_pool.dead:
                            break
                    self.assertTrue(hanzo_pool.dead)
                    self.assertEqual(hanzo_pool.killer, sigma.body)
                    self.assertEqual(soldier_pool.total, soldier_health)
                    self.assertFalse(fight.sonars)
                    if stage == "baseline":
                        landing = frame
                        continue
                    self.assertEqual(frame, landing)
                    if stage == "prepared_sonic":
                        self.assertIsNone(arrow)
                        fired.assert_not_called()
                    else:
                        self.assertIsNotNone(arrow)
                        self.assertIn(arrow, fight.rockets)
                        self.assertEqual(arrow.source_entity, old_body)
                        self.assertEqual(arrow.direct.amount, 125 if stage == "released_sonic" else 70)
                        self.assertIs(arrow.sonar, sonic_profile if stage == "released_sonic" else None)
                        fired.assert_called_once()

                    charge = script.component.entity_var(342)
                    charge_at_death = charge.value()
                    for _ in range(55):
                        run.step({hanzo: (0, 0, 1 if stage == "released_storm" else 0)})
                    self.assertFalse(fight.rockets)
                    self.assertEqual(charge.value(), charge_at_death)
                    if stage == "released_sonic":
                        self.assertEqual(soldier_pool.total, 75)
                        beacon, = fight.sonars
                        self.assertEqual((beacon.source_entity, beacon.attached), (old_body, soldier.body))
                        self.assertTrue(fight.revealed_to(hanzo, soldier))
                        fired.assert_called_once()
                    elif stage == "released_storm":
                        self.assertTrue(soldier_pool.dead)
                        self.assertEqual(soldier_pool.killer, old_body)
                        self.assertGreater(soldier_pool.death_tick, hanzo_pool.death_tick)
                        self.assertFalse(fight.sonars)
                        fired.assert_called_once()
                    else:
                        self.assertEqual(soldier_pool.total, soldier_health)
                        self.assertFalse(fight.sonars)
                        fired.assert_not_called()


    def test_lethal_flux_landing_cancels_only_uncommitted_dragonstrike(self):
        dragon_profile = prepared_profile()
        landing = None
        cast_leads = {"baseline": None, "windup": 55, "arrow": 65}
        for stage in ("baseline", "windup", "arrow", "dragon"):
            lead = cast_leads[stage]
            with self.subTest(stage=stage):
                run, hanzo, sigma = accretion_tests.HanzoAccretionTests().arena()
                match = run.match
                fight = match.combat
                fight.configure_dragonstrike(dragon_profile)
                soldier = match.add_player(3, "Downrange Soldier", SOLDIER, sigma.team, False)
                in_world(match, soldier)
                run.players.append(soldier)
                self.assertTrue(match.switch_hero(soldier, heroes()[SOLDIER]))
                soldier.team = sigma.team
                soldier.mover.position[:] = (0, 0, 36)
                soldier.mover.body = None
                targets = {t.owner: t for t in fight._targets()}
                hanzo_pool = fight.vitals_of(targets[hanzo])
                soldier_pool = fight.vitals_of(targets[soldier])
                hanzo_pool.damage(80)
                old_body, script = hanzo.body, hanzo.body_script
                script.ult_seconds = 0
                script.set_ult_charge(1680)
                charge = script.component.entity_var(342)
                self.cast(run, hanzo, sigma, tuple(hanzo.mover.position))
                cast_at = None if lead is None else landing - lead
                arrow = None
                for frame in range(1, 480):
                    angles = aim(fight._eye(hanzo), fight._eye(soldier))
                    before_arrow = arrow is not None and arrow in fight.rockets
                    before_dragon = next(iter(fight.dragons), None)
                    run.step({hanzo: (*angles, 32 if frame == cast_at else 0)})
                    if frame == cast_at:
                        self.assertEqual(hanzo_pool.total, 70)
                        self.assertTrue(hanzo.mover.airborne)
                        self.assertTrue(hanzo_control.casting(script))
                        self.assertEqual(charge.value(), 0)
                    if arrow is None:
                        arrow = next((r for r in fight.rockets if r.continuation is dragon_profile), None)
                    if hanzo_pool.dead:
                        break
                self.assertTrue(hanzo_pool.dead)
                self.assertEqual(hanzo_pool.killer, sigma.body)
                self.assertFalse(hanzo.mover.airborne)
                self.assertFalse(any(i.graph.index == 0x1DBC for i in script.component.instances.values()))
                self.assertEqual(soldier_pool.total, 200)
                if stage == "baseline":
                    landing = frame
                    continue

                self.assertEqual(frame, landing)
                if stage == "windup":
                    self.assertIsNone(arrow)
                    self.assertFalse(fight.dragons)
                    self.assertFalse(hanzo_control.casting(script))
                    self.assertEqual(charge.value(), 0)
                    for _ in range(95):
                        run.step({})
                        self.assertFalse(fight.rockets)
                        self.assertFalse(fight.dragons)
                    self.assertEqual(soldier_pool.total, 200)
                    continue

                self.assertIsNotNone(arrow)
                self.assertEqual(arrow.source_entity, old_body)
                self.assertEqual(before_arrow, stage == "arrow")
                self.assertEqual(before_dragon is not None, stage == "dragon")
                if before_arrow:
                    self.assertIn(arrow, fight.rockets)
                elif before_dragon is not None:
                    self.assertIn(before_dragon, fight.dragons)
                charge_at_death = charge.value()
                for follow in range(1, 191):
                    run.step({})
                    if stage == "arrow" and fight.dragons and "dragon" not in cast_leads:
                        cast_leads["dragon"] = frame + follow - cast_at + 8
                    if soldier_pool.total < 200:
                        break
                flight, = fight.dragons
                self.assertEqual(flight.source_entity, old_body)
                if before_dragon is not None:
                    self.assertIs(flight, before_dragon)
                self.assertNotIn(arrow, fight.rockets)
                self.assertNotIn(soldier.body, arrow.hit_entities)
                self.assertLess(soldier_pool.total, 200)
                self.assertEqual(charge.value(), charge_at_death)


    def test_charge_gate_and_invalid_sky_click_require_fresh_ground_confirmation(self):
        run, hanzo, sigma = accretion_tests.HanzoAccretionTests().arena()
        fight = run.match.combat
        script = sigma.body_script
        script.ult_seconds = 0
        flux = next(i for i in script.component.instances.values() if i.graph.index == 0x1E41)
        script.set_ult_charge(1959)
        run.step({sigma: (0, -8192, 32)})
        self.assertFalse(flux.states[0].flags & nodes.Ability.RUNNING)
        self.assertEqual(script.component.entity_var(342).value(), 1959)
        run.step({})
        script.set_ult_charge(1960)
        run.step({sigma: (0, -8192, 32)})
        for _ in range(79):
            run.step({sigma: (0, -8192, 0)})
        run.step({sigma: (0, -8192, 1)})
        self.assertFalse(flux.vars[1002].value())
        self.assertEqual(flux.vars[59].value(), 1)
        self.assertEqual(script.component.entity_var(342).value(), 0)
        yaw, pitch = aim(fight._eye(sigma), tuple(hanzo.mover.position))
        run.step({sigma: (yaw, pitch, 0)})
        self.assertEqual(flux.vars[59].value(), 1)
        self.assertTrue(flux.vars[1002].value())
        run.step({sigma: (yaw, pitch, 1)})
        self.assertEqual(flux.vars[59].value(), 2)
        self.wait_for_lift(run, hanzo)

    def test_low_static_ceiling_caps_lift_and_landing_still_applies_slam(self):
        run, hanzo, sigma = accretion_tests.HanzoAccretionTests().arena()
        fight = run.match.combat
        world = World(box(-30, 30, -1, 0, -30, 30) + box(-2, 2, 4.5, 4.7, 4.6, 7.4))
        run.match.collision_world = world
        for player in run.players:
            player.mover.use(world=world)
        pool = fight.vitals_of(next(t for t in fight._targets() if t.owner is hanzo))
        self.cast(run, hanzo, sigma, tuple(hanzo.mover.position))
        peak = 0
        touched_lift = False
        for _ in range(480):
            run.step({})
            peak = max(peak, hanzo.mover.position[1])
            if pool.total == 150 and hanzo.mover.airborne:
                touched_lift = True
            if pool.total < 150:
                break
        self.assertTrue(touched_lift)
        self.assertGreater(peak, 2)
        self.assertLess(peak, 4.5)
        self.assertEqual(pool.total, 50)
        self.assertFalse(hanzo.mover.airborne)

    def test_lifted_hanzo_can_melee_another_lifted_enemy_before_both_land(self):
        run, hanzo, sigma = accretion_tests.HanzoAccretionTests().arena()
        match = run.match
        match.game_map = replace(match.game_map, free_for_all=True)
        fight = match.combat
        other = match.add_player(3, "Nearby Soldier", SOLDIER, 1, False)
        in_world(match, other)
        run.players.append(other)
        self.assertTrue(match.switch_hero(other, heroes()[SOLDIER]))
        other.mover.position[:] = (1.5, 0, 6)
        other.mover.body = None
        targets = {t.owner: t for t in fight._targets()}
        victim = fight.vitals_of(targets[other])
        pool = fight.vitals_of(targets[hanzo])
        self.cast(run, hanzo, sigma, tuple(hanzo.mover.position))
        self.wait_for_lift(run, hanzo)
        self.assertTrue(other.mover.airborne)
        self.assertEqual(victim.total, 150)
        for _ in range(22):
            angles = aim(fight._eye(hanzo), fight._eye(other))
            run.step({hanzo: (*angles, 0x800)})
        self.assertEqual(victim.total, 120)
        self.assertEqual([row["+0x78"]["+0x4"] for row in hanzo.client.messages(combat.HIT)], [30])
        self.assertEqual(pool.total, 150)
        for _ in range(400):
            run.step({})
            if pool.total == 50 and victim.total == 20:
                break
        self.assertEqual((pool.total, victim.total), (50, 20))
        self.assertFalse(hanzo.mover.airborne or other.mover.airborne)

    def test_flux_takes_over_active_wall_climb_without_spending_more_budget(self):
        run, hanzo, sigma = accretion_tests.HanzoAccretionTests().arena(wall_z=6.7)
        fight = run.match.combat
        pool = fight.vitals_of(next(t for t in fight._targets() if t.owner is hanzo))
        self.cast(run, hanzo, sigma, tuple(hanzo.mover.position))
        for _ in range(48):
            run.step({})
        held = {hanzo: (0, 0, JUMP)}
        run.step(held)
        run.step(held)
        climb = hanzo.body_script.world.hanzo_climb
        component = hanzo.body_script.component
        self.assertTrue(climb.active)
        for _ in range(30):
            run.step(held)
            if pool.total == 150:
                break
        self.assertEqual(pool.total, 150)
        self.assertFalse(fight.is_stunned(hanzo))
        spent = climb.elapsed
        height = hanzo.mover.position[1]
        for _ in range(16):
            run.step(held)
            self.assertFalse(climb.active)
            self.assertFalse(component.entity_var(1204).value())
            self.assertEqual(climb.elapsed, spent)
        self.assertGreater(hanzo.mover.position[1] - height, 3.5)
        for _ in range(220):
            run.step(held)
            self.assertFalse(climb.active)
            if pool.total == 50:
                break
        self.assertEqual(pool.total, 50)
        self.assertFalse(hanzo.mover.airborne)
        for _ in range(100):
            run.step({})
            if not any(i.graph.index == 0x1DBC for i in component.instances.values()):
                break
        self.assertFalse(any(i.graph.index == 0x1DBC for i in component.instances.values()))
        run.step(held)
        run.step(held)
        self.assertTrue(climb.active)
        self.assertAlmostEqual(climb.elapsed, combat.TICK)

    def test_flux_cancels_pending_mantle_and_keeps_its_vertical_lift(self):
        from test_game_hanzo_movement import HanzoLungeTests

        run, hanzo, sigma = accretion_tests.HanzoAccretionTests().arena()
        fight = run.match.combat
        x, y, z = hanzo.mover.position
        run.match.collision_world = World(
            box(x - 20, x + 20, y - 1, y, z - 20, z + 20)
            + box(x - 5, x + 5, y, y + 1.9, z + 0.7, z + 8),
        )
        pool = fight.vitals_of(next(t for t in fight._targets() if t.owner is hanzo))
        self.cast(run, hanzo, sigma, tuple(hanzo.mover.position))
        for _ in range(56):
            run.step({})
        for _ in range(2):
            HanzoLungeTests.step(run, hanzo, buttons=JUMP, forward=127)
        progress = hanzo.body_script.world.hanzo_climb
        self.assertIsNotNone(progress.mantle)
        for _ in range(24):
            HanzoLungeTests.step(run, hanzo, buttons=JUMP, forward=127)
            if pool.total == 150:
                break
        self.assertEqual(pool.total, 150)
        for _ in range(18):
            HanzoLungeTests.step(run, hanzo, buttons=JUMP, forward=127)
            self.assertIsNone(progress.mantle)
        self.assertFalse(fight.is_stunned(hanzo))
        self.assertGreater(hanzo.mover.position[1], y + 4)
        self.assertLess(hanzo.mover.position[2], z + 0.7)
        for _ in range(230):
            run.step({})
            if pool.total == 50:
                break
        self.assertEqual(pool.total, 50)
        self.assertFalse(hanzo.mover.airborne)

    def test_flux_cancels_high_ledge_handoff_and_keeps_its_vertical_lift(self):
        from test_game_hanzo_movement import HanzoLungeTests

        run, hanzo, sigma = accretion_tests.HanzoAccretionTests().arena()
        fight = run.match.combat
        x, y, z = hanzo.mover.position
        run.match.collision_world = World(
            box(x - 20, x + 20, y - 1, y, z - 20, z + 20)
            + box(x - 5, x + 5, y, y + 3, z + 0.7, z + 8),
        )
        hanzo.mover.use(world=run.match.collision_world)
        pool = fight.vitals_of(next(t for t in fight._targets() if t.owner is hanzo))
        self.cast(run, hanzo, sigma, tuple(hanzo.mover.position))
        for _ in range(35):
            run.step({})
        progress = hanzo.body_script.world.hanzo_climb
        climbed = False
        for _ in range(40):
            route_at_contact = progress.mantle
            height_at_contact = hanzo.mover.position[1]
            climbed = climbed or progress.active
            HanzoLungeTests.step(run, hanzo, buttons=JUMP, forward=127)
            if pool.total < 200:
                break
        self.assertEqual(pool.total, 150)
        self.assertTrue(climbed)
        self.assertIsNotNone(route_at_contact)
        self.assertGreater(progress.elapsed, 0)
        spent = progress.elapsed
        component = hanzo.body_script.component
        effect, = [i for i in component.instances.values() if i.graph.index == 0x1DBC]
        self.assertEqual(effect.source_entity(), sigma.body)
        self.assertIsNone(progress.mantle)
        self.assertFalse(hanzo.mover.driven)
        self.assertFalse(fight.is_stunned(hanzo))
        peak_lift = hanzo.mover.velocity[1]
        for _ in range(18):
            HanzoLungeTests.step(run, hanzo, buttons=JUMP, forward=127)
            self.assertIsNone(progress.mantle)
            self.assertFalse(progress.active)
            self.assertFalse(component.entity_var(1204).value())
            self.assertEqual(progress.elapsed, spent)
            peak_lift = max(peak_lift, hanzo.mover.velocity[1])
        self.assertAlmostEqual(peak_lift, 18, delta=0.01)
        self.assertGreater(hanzo.mover.position[1], height_at_contact + 3.5)
        self.assertLess(hanzo.mover.position[2], z + 0.7)
        for _ in range(230):
            HanzoLungeTests.step(run, hanzo, buttons=JUMP, forward=127)
            self.assertIsNone(progress.mantle)
            if pool.total == 50:
                break
        self.assertEqual(pool.total, 50)
        self.assertFalse(hanzo.mover.airborne)
        self.assertAlmostEqual(hanzo.mover.position[1], y + 3, delta=0.01)

    def test_flux_stops_committed_lunge_without_refunding_cooldown(self):
        run, hanzo, sigma = accretion_tests.HanzoAccretionTests().arena()
        fight = run.match.combat
        pool = fight.vitals_of(next(t for t in fight._targets() if t.owner is hanzo))
        self.cast(run, hanzo, sigma, tuple(hanzo.mover.position))
        movement = next(i for i in hanzo.body_script.component.instances.values() if i.graph.index == 0x010C)
        cooldown = movement.vars[61]
        for _ in range(37):
            run.step({})
        jump = {hanzo: (16384, 0, JUMP)}
        run.step(jump)
        for _ in range(14):
            run.step({})
        run.step(jump)
        self.assertGreater(hanzo.mover.velocity[0], 14)
        for _ in range(30):
            run.step({})
            if pool.total == 150:
                break
        self.assertEqual(pool.total, 150)
        self.assertEqual(hanzo.mover.velocity[0], 0)
        self.assertGreater(cooldown.value(), 4.8)
        x = hanzo.mover.position[0]
        for _ in range(250):
            run.step({})
            self.assertEqual(hanzo.mover.position[0], x)
            if pool.total == 50:
                break
        self.assertEqual(pool.total, 50)
        self.assertGreater(cooldown.value(), 2)
        self.assertFalse(hanzo.mover.airborne)
        component = hanzo.body_script.component
        for _ in range(100):
            run.step({})
            if not any(i.graph.index == 0x1DBC for i in component.instances.values()):
                break
        self.assertFalse(any(i.graph.index == 0x1DBC for i in component.instances.values()))
        run.step(jump)
        for _ in range(14):
            run.step({})
        self.assertTrue(hanzo.mover.airborne)
        run.step(jump)
        self.assertEqual(hanzo.mover.velocity[0], 0)
        self.assertGreater(cooldown.value(), 0)
        for _ in range(350):
            run.step({})
            if cooldown.value() == 0 and not hanzo.mover.airborne:
                break
        self.assertEqual(cooldown.value(), 0)
        self.assertFalse(hanzo.mover.airborne)
        run.step(jump)
        for _ in range(14):
            run.step({})
        run.step(jump)
        self.assertGreater(hanzo.mover.velocity[0], 14)

    def test_jump_while_lifted_does_not_launch_or_spend_lunge(self):
        for prior_jump in (False, True):
            with self.subTest(prior_jump=prior_jump):
                run, hanzo, sigma = accretion_tests.HanzoAccretionTests().arena()
                fight = run.match.combat
                self.cast(run, hanzo, sigma, tuple(hanzo.mover.position))
                if prior_jump:
                    for _ in range(52):
                        run.step({})
                    run.step({hanzo: (0, 0, JUMP)})
                    self.assertTrue(hanzo.mover.airborne)
                self.wait_for_lift(run, hanzo)
                movement = next(i for i in hanzo.body_script.component.instances.values() if i.graph.index == 0x010C)
                world = hanzo.body_script.world
                with patch.object(world, "velocity_action", wraps=world.velocity_action) as actions:
                    def lunge_count():
                        return sum(call.args[0] is movement and call.args[1].pos == 82
                                   for call in actions.call_args_list)

                    x = hanzo.mover.position[0]
                    run.step({hanzo: (16384, 0, JUMP)})
                    for _ in range(15):
                        run.step({})
                    self.assertEqual(lunge_count(), 0)
                    self.assertEqual(movement.vars[61].value(), 0)
                    self.assertEqual(hanzo.mover.position[0], x)
                    # Once the entry jump modifier has cleared, the authored body graph
                    # still rejects a fresh jump edge during the hold and descent.
                    for _ in range(25):
                        run.step({})
                    run.step({hanzo: (16384, 0, JUMP)})
                    self.assertEqual(lunge_count(), 0)
                    self.assertEqual(movement.vars[61].value(), 0)
                    self.assertEqual(hanzo.mover.position[0], x)
                    for _ in range(180):
                        run.step({})
                        if hanzo.mover.velocity[1] < -10:
                            break
                    self.assertLess(hanzo.mover.velocity[1], -10)
                    run.step({hanzo: (16384, 0, JUMP)})
                    self.assertEqual(lunge_count(), 0)
                    self.assertEqual(movement.vars[61].value(), 0)
                    self.assertEqual(hanzo.mover.position[0], x)
                    for _ in range(400):
                        run.step({})
                        if fight.vitals[hanzo.body].total == 50:
                            break
                    self.assertEqual(fight.vitals[hanzo.body].total, 50)
                    self.assertFalse(hanzo.mover.airborne)
                    component = hanzo.body_script.component
                    for _ in range(100):
                        if not any(i.graph.index == 0x1DBC for i in component.instances.values()):
                            break
                        run.step({})
                    self.assertFalse(any(i.graph.index == 0x1DBC for i in component.instances.values()))
                    run.step({hanzo: (16384, 0, JUMP)})
                    self.assertTrue(hanzo.mover.airborne)
                    for _ in range(14):
                        run.step({})
                    run.step({hanzo: (16384, 0, JUMP)})
                    self.assertEqual(lunge_count(), 1)
                    self.assertGreater(hanzo.mover.velocity[0], 14)
