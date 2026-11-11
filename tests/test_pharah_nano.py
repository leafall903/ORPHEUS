"""Actual Ana ultimate input with the extracted 0269.053 target graph."""

import math
import os
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import test_pharah_ana_grenade as grenade
import test_pharah_defenses as defense
import test_pharah_discord as discord
import test_pharah_fortify as fortify
import test_pharah_pve as pve
import test_pharah_zarya_barrier as bubbles
from test_game_combat import KANEZAKA_DM, Run, aim
from test_game_mover import FLOOR, box, wall

from ow174.game import ana_nano, combat, heavy_charge, npc, npc_control, sigma_flux, sigma_grasp, zarya_barrier
from ow174.game.collision import World
from ow174.game.commands import FIRE, JUMP, SECONDARY
from ow174.game.content import heroes
from ow174.game.script import graph


class PharahNanoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        bundle = os.environ.get("ORPHEUS_NANO_GRAPH_BUNDLE")
        if bundle:
            mount = patch.object(graph, "GRAPHS_PATH", Path(bundle))
            mount.start()
            cls.addClassCleanup(mount.stop)
            for cached in (graph._data, graph._records, graph.graph):
                cached.cache_clear()
                cls.addClassCleanup(cached.cache_clear)
        if graph.graph(ana_nano.STATUS) is None:
            raise unittest.SkipTest("Supply extracted Nano Boost target graph 0B9F")

    def arena(self):
        fixture = defense.PharahDefenseTests()
        run, pharah, ana, enemy = fixture.encounter("Ana")
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        pharah.team = ana.team = 0
        enemy.team = 1
        pharah.mover.position[2], ana.mover.position[2] = 8, 0
        pharah.mover.body = ana.mover.body = None
        script = ana.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        return fixture, run, pharah, ana, enemy

    def cast(self, fixture, run, pharah, ana):
        health = fixture.health(run, pharah)
        health.damage(150)
        charge = ana.body_script.component.entity_var(342)
        fight = run.match.combat
        run.step({ana: (0, 0, 32)})
        self.assertEqual(charge.value(), 0)
        self.assertEqual(health.total, 50)
        for _ in range(16):
            run.step({})
            if pharah.body in fight.nano_buffs:
                break
        self.assertEqual(health.total, 200)
        deadline, incoming, outgoing = fight.nano_buffs[pharah.body]
        self.assertEqual(deadline - fight.tick, 500)
        self.assertEqual((incoming, outgoing), (.5, 1.5))
        return deadline

    def test_actual_nano_boosts_primary_and_expires_at_original_deadline(self):
        fixture, run, pharah, ana, enemy = self.arena()
        hog = next(hero for hero in heroes().values() if hero.name == "Roadhog")
        self.assertTrue(run.match.switch_hero(enemy, hog))
        enemy.mover.position = [0, pharah.mover.position[1], 14]
        enemy.mover.body = None
        for _ in range(4):
            run.step({})
        deadline = self.cast(fixture, run, pharah, ana)
        vitals = fixture.health(run, enemy)
        before = vitals.total
        charge = pharah.body_script.component.entity_var(342)
        fixture.fire(run, pharah)
        self.assertEqual(vitals.total, before - 180)
        self.assertEqual(charge.value(), 180)
        fight = run.match.combat
        while run.frame < deadline - 4:
            run.step({})
        run.step({pharah: (0, 0, FIRE)})
        while run.frame < deadline:
            run.step({})
        self.assertNotIn(pharah.body, fight.nano_buffs)
        self.assertTrue(any(rocket.shooter is pharah for rocket in fight.rockets))
        for _ in range(40):
            run.step({})
        self.assertEqual(vitals.total, before - 300)
        self.assertEqual(charge.value(), 300)

    def test_actual_flux_uses_distinct_entry_and_slam_nano_rules(self):
        if graph.graph(sigma_flux.FLUX_EFFECT) is None:
            self.skipTest("Supply the extracted Gravitic Flux target graph")
        for boosted, entry, slam in ((None, 50, 100), ("caster", 75, 100), ("recipient", 25, 50)):
            with self.subTest(boosted=boosted):
                self.assert_flux_nano_damage(boosted, entry, slam)

    def flux_arena(self, boosted=None):
        if graph.graph(sigma_flux.FLUX_EFFECT) is None:
            self.skipTest("Supply the extracted Gravitic Flux target graph")
        fixture, run, pharah, ana, sigma = self.arena()
        match = run.match
        self.assertTrue(match.switch_hero(sigma, heroes()[sigma_flux.HERO]))
        floor = pharah.mover.position[1]
        sigma.mover.position[:] = (0, floor, 2)
        sigma.mover.body = None
        if boosted == "caster":
            ana.team = sigma.team
            ana.mover.position[2] = -4
        elif boosted is None:
            ana.mover.position[0] = 30
        ana.mover.body = None
        for _ in range(5):
            run.step({})
        return fixture, run, pharah, ana, sigma

    def assert_flux_nano_damage(self, boosted, entry, slam):
        fixture, run, pharah, ana, sigma = self.flux_arena(boosted)
        match = run.match
        floor = pharah.mover.position[1]
        if boosted == "recipient":
            self.cast(fixture, run, pharah, ana)
        elif boosted == "caster":
            yaw, pitch = aim(match.combat._eye(ana), match.combat._eye(sigma))
            run.step({ana: (yaw, pitch, 32)})
            for _ in range(16):
                if sigma.body in match.combat.nano_buffs:
                    break
                run.step({})
            self.assertIn(sigma.body, match.combat.nano_buffs)

        health = fixture.health(run, pharah)
        self.confirm_flux(run, sigma, pharah)

        peak = floor
        damage_steps = []
        for _ in range(400):
            before = health.total
            run.step({})
            peak = max(peak, pharah.mover.position[1])
            if health.total < before:
                damage_steps.append((before - health.total, pharah.mover.airborne))
            if len(damage_steps) == 2:
                break
        self.assertGreater(peak, floor + 2)
        self.assertEqual(damage_steps, [(entry, False), (slam, False)])
        for _ in range(50):
            run.step({})
        self.assertEqual(health.total, 200 - entry - slam)
        self.assertFalse(any(instance.graph.index == sigma_flux.FLUX_EFFECT
                             for instance in pharah.body_script.component.instances.values()))
        self.assertIsNone(sigma.body_script.component.fault)
        self.assertIsNone(pharah.body_script.component.fault)

    def confirm_flux(self, run, sigma, target, *, point=None, confirm=True):
        script = sigma.body_script
        charge = script.component.entity_var(342)
        script.set_ult_charge(script.component.entity_var(338).value())
        if point is None:
            point = tuple(target.mover.position)
        for frame in range(81):
            yaw, pitch = aim(run.match.combat._eye(sigma), point)
            buttons = 32 if frame == 0 else FIRE if frame == 80 and confirm else 0
            run.step({sigma: (yaw, pitch, buttons)})
            if frame == 0:
                self.assertEqual(charge.value(), 0)

    def flux_lock(self, run, pharah):
        component = pharah.body_script.component
        for _ in range(120):
            slot = component.entity_var(5995, create=False)
            if slot is not None and slot.value():
                return slot
            run.step({})
        self.fail("Flux did not install the authored movement lock")

    def test_flux_rejects_jump_jet_until_landing_and_fresh_press(self):
        fixture, run, pharah, _, sigma = self.flux_arena()
        self.confirm_flux(run, sigma, pharah)
        lock = self.flux_lock(run, pharah)
        jet = next(i for i in pharah.body_script.component.instances.values() if i.graph.index == 0x27)
        cooldown = jet.vars[61]
        for frame in range(20):
            run.step({pharah: (0, 0, 8 if frame in (0, 10) else 0)})
            self.assertTrue(lock.value())
            self.assertEqual(cooldown.value(), 0)
        self.assertTrue(pharah.mover.airborne)
        for _ in range(360):
            run.step({})
            if not lock.value() and not pharah.mover.airborne:
                break
        else:
            self.fail("Flux never released its target")
        self.assertEqual(fixture.health(run, pharah).total, 50)
        self.assertEqual(cooldown.value(), 0)
        run.step({pharah: (0, 0, 8)})
        self.assertGreater(cooldown.value(), 9.9)
        self.assertGreater(pharah.mover.velocity[1], 0)

    def test_flux_defers_both_held_hover_bindings_until_release(self):
        for buttons in (JUMP, SECONDARY):
            with self.subTest(buttons=buttons):
                fixture, run, pharah, _, sigma = self.flux_arena()
                self.confirm_flux(run, sigma, pharah)
                lock = self.flux_lock(run, pharah)
                component = pharah.body_script.component
                hover = next(i for i in component.instances.values() if i.graph.index == 0x25)
                fuel = hover.vars[87].value()
                for _ in range(200):
                    run.step({pharah: (0, 0, buttons)})
                    thrust = any(state.active and state.node.cls == "STU_A9C4FF83"
                                 for state in hover.states.values())
                    if not lock.value():
                        self.assertTrue(thrust)
                        break
                    self.assertEqual(hover.vars[87].value(), fuel)
                    self.assertFalse(thrust)
                else:
                    self.fail("Flux did not release the held hover input")
                self.assertEqual(fixture.health(run, pharah).total, 50)

    def test_barrage_can_start_during_flux_and_keeps_firing_through_slam(self):
        fixture, run, pharah, _, sigma = self.flux_arena()
        script = pharah.body_script
        component = script.component
        script.set_ult_charge(component.entity_var(338).value())
        self.confirm_flux(run, sigma, pharah)
        lock = self.flux_lock(run, pharah)
        run.step({pharah: (0, 0, 32)})
        self.assertEqual(component.entity_var(342).value(), 0)
        health = fixture.health(run, pharah)
        saw_rocket = False
        for _ in range(200):
            run.step({})
            saw_rocket |= any(rocket.shooter is pharah for rocket in run.match.combat.rockets)
            if health.total == 50:
                break
        else:
            self.fail("Flux did not land while Barrage was active")
        self.assertTrue(saw_rocket)
        self.assertTrue(script.ultimate_running())
        self.assertFalse(pharah.mover.airborne)
        for _ in range(24):
            run.step({})
            if not lock.value():
                break
        self.assertFalse(lock.value())

    def test_lethal_pharah_rocket_releases_flux_only_before_descent(self):
        for shot_frame, committed in ((0, False), (105, True)):
            with self.subTest(committed=committed):
                fixture, run, pharah, _, sigma = self.flux_arena()
                self.confirm_flux(run, sigma, pharah)
                lock = self.flux_lock(run, pharah)
                component = pharah.body_script.component
                effect = next(i for i in component.instances.values() if i.graph.index == sigma_flux.FLUX_EFFECT)
                sigma_health = fixture.health(run, sigma)
                sigma_health.damage(sigma_health.total - 1)
                pharah_health = fixture.health(run, pharah)
                self.assertEqual(pharah_health.total, 150)

                for frame in range(160):
                    if frame == shot_frame:
                        fight = run.match.combat
                        yaw, pitch = aim(fight._eye(pharah), fight._eye(sigma))
                        inputs = {pharah: (yaw, pitch, FIRE)}
                    else:
                        inputs = {}
                    lift = effect.states.get(16)
                    descending = lift is not None and lift.counter > 0 and not lift.active
                    run.step(inputs)
                    if sigma_health.dead:
                        self.assertEqual(descending, committed)
                        self.assertEqual(bool(lock.value()), committed)
                        self.assertTrue(pharah.mover.airborne)
                        break
                else:
                    self.fail("Pharah did not kill the Flux caster with her rocket")

                for _ in range(360):
                    run.step({})
                    if not lock.value() and not pharah.mover.airborne:
                        break
                self.assertFalse(lock.value())
                self.assertFalse(pharah.mover.airborne)
                self.assertEqual(pharah_health.total, 50 if committed else 150)

    def test_projected_zarya_barrier_absorbs_flux_entry_or_slam_without_ending_lift(self):
        for phase, durability, loss, final_health in (
            ("entry", 200, 50, 100), ("slam", 200, 100, 150), ("slam", 40, 40, 150),
        ):
            with self.subTest(phase=phase, durability=durability):
                self.assert_projected_flux_barrier(phase, durability, loss, final_health)

    def assert_projected_flux_barrier(self, phase, durability, loss, final_health):
        fixture, run, pharah, zarya, sigma = self.flux_arena()
        match = run.match
        fight = match.combat
        self.assertTrue(match.switch_hero(zarya, heroes()[zarya_barrier.HERO]))
        floor = pharah.mover.position[1]
        zarya.mover.position[:] = (10, floor, pharah.mover.position[2])
        zarya.mover.body = None
        for _ in range(4):
            run.step({})
        self.confirm_flux(run, sigma, pharah)
        health = fixture.health(run, pharah)
        if phase == "entry":
            for _ in range(40):
                run.step({})
            self.assertEqual(health.total, 200)
        else:
            lock = self.flux_lock(run, pharah)
            for _ in range(190):
                run.step({})
                if pharah.mover.position[1] > floor + 2 and pharah.mover.velocity[1] < -10:
                    break
            else:
                self.fail("Flux never committed its descent")
            self.assertTrue(lock.value())
            self.assertEqual(health.total, 150)

        yaw, pitch = aim(fight._eye(zarya), fight._eye(pharah))
        run.step({zarya: (yaw, pitch, 16)})
        bubble = zarya_barrier.attached_to(fight, pharah.body)
        self.assertIsNotNone(bubble)
        self.assertEqual(bubble.health, 200)
        bubble.health = durability
        energy = zarya.body_script.component.entity_var(87)
        for _ in range(75):
            run.step({})
            if bubble.health < durability:
                break
        self.assertEqual(bubble.health, durability - loss)
        self.assertAlmostEqual(energy.value(), loss / 5, places=4)
        self.assertEqual(health.total, 200 if phase == "entry" else 150)
        if phase == "entry":
            self.assertTrue(self.flux_lock(run, pharah).value())
            for _ in range(30):
                if pharah.mover.airborne:
                    break
                run.step({})
            self.assertTrue(pharah.mover.airborne)
        elif durability == loss:
            self.assertIsNone(zarya_barrier.attached_to(fight, pharah.body))
        for _ in range(200):
            run.step({})
        self.assertFalse(pharah.mover.airborne)
        self.assertEqual(health.total, final_health)

    def test_experimental_barrier_does_not_intercept_flux_against_pharah(self):
        fixture, run, pharah, ally, sigma = self.flux_arena()
        match = run.match
        fight = match.combat
        if fight.sigma_geometry is None:
            self.skipTest("Supply the extracted Experimental Barrier model")
        self.assertTrue(match.switch_hero(ally, heroes()[sigma_flux.HERO]))
        ally.mover.position[:] = (0, pharah.mover.position[1], 10)
        ally.mover.body = None
        for _ in range(4):
            run.step({})
        for frame in range(15):
            run.step({ally: (-32768, 0, SECONDARY if frame < 8 else 0)})
        shield, = (barrier for barrier in fight.sigma_barriers.values() if barrier.active)
        origin, target = fight._eye(sigma), fight._eye(pharah)
        distance = math.dist(origin, target)
        direction = tuple((b - a) / distance for a, b in zip(origin, target, strict=True))
        self.assertIsNotNone(shield.ray(origin, direction, distance))

        self.confirm_flux(run, sigma, pharah)
        lock = self.flux_lock(run, pharah)
        health = fixture.health(run, pharah)
        self.assertEqual(health.total, 150)
        for _ in range(210):
            run.step({})
            if not lock.value():
                break
        self.assertFalse(lock.value())
        self.assertEqual(health.total, 50)
        self.assertTrue(shield.active)
        self.assertEqual(shield.vitals.total, 700)

    def test_immortality_field_clamps_flux_slam_after_landing(self):
        for protected in (False, True):
            with self.subTest(protected=protected):
                self.assert_flux_immortality_floor(protected)

    def assert_flux_immortality_floor(self, protected):
        fixture, run, pharah, ally, sigma = self.flux_arena()
        match = run.match
        fight = match.combat
        health = fixture.health(run, pharah)
        health.damage(health.total - 120)
        if protected:
            baptiste = next(hero for hero in heroes().values() if hero.name == "Baptiste")
            self.assertTrue(match.switch_hero(ally, baptiste))
            _, y, z = pharah.mover.position
            ally.mover.position[:] = (8, y, z)
            ally.mover.body = None
            for _ in range(4):
                run.step({})
            yaw, pitch = aim(fight._eye(ally), tuple(pharah.mover.position))
            run.step({ally: (yaw, pitch, 16)})
            for _ in range(100):
                run.step({})
                ward = fight.baptiste_fields.get(ally.body)
                if ward is not None and pharah.body in ward.bindings:
                    break
            else:
                self.fail("Pharah never entered the actual Immortality Field")

        self.confirm_flux(run, sigma, pharah)
        self.flux_lock(run, pharah)
        self.assertEqual(health.total, 70)
        for _ in range(210):
            run.step({})
            if health.total < 70:
                break
        self.assertEqual(health.total, 20 if protected else 0)
        self.assertEqual(health.dead, not protected)
        if protected:
            self.assertFalse(pharah.mover.airborne)
            self.assertIn(pharah.body, ward.bindings)
            self.assertEqual(ward.health, 150)

    def test_flux_selection_excludes_cover_allies_range_and_unconfirmed_cast(self):
        for condition in ("clear", "wall", "ally", "range", "unconfirmed"):
            with self.subTest(condition=condition):
                self.assert_flux_selection(condition)

    def assert_flux_selection(self, condition):
        fixture, run, pharah, _, sigma = self.flux_arena()
        floor = pharah.mover.position[1]
        if condition == "wall":
            world = World(FLOOR + box(-20, 20, floor, floor + 25, 5, 5.1))
            run.match.collision_world = world
            for player in run.players:
                player.mover.use(world=world)
        if condition == "ally":
            pharah.team = sigma.team
        if condition == "range":
            pharah.mover.position[2] = 20
            pharah.mover.body = None

        script = sigma.body_script
        script.ult_seconds = 0
        instance = next(i for i in script.component.instances.values() if i.graph.index == sigma_flux.FLUX)
        confirmed = condition != "unconfirmed"
        point = (0, floor, 4)
        self.confirm_flux(run, sigma, pharah, point=point, confirm=confirmed)
        self.assertEqual(instance.vars[59].value(), 2 if confirmed else 1)
        self.assertTrue(instance.vars[1002].value())
        for actual, expected in zip(instance.vars[2478].value(), point, strict=True):
            self.assertAlmostEqual(actual, expected, places=3)
        had_effect = False
        peak = floor
        for _ in range(550 if not confirmed else 240):
            run.step({})
            had_effect |= any(i.graph.index == sigma_flux.FLUX_EFFECT
                              for i in pharah.body_script.component.instances.values())
            peak = max(peak, pharah.mover.position[1])
        selected = condition == "clear"
        self.assertEqual(had_effect, selected)
        self.assertEqual(fixture.health(run, pharah).total, 50 if selected else 200)
        if selected:
            self.assertGreater(peak, floor + 2)
        else:
            self.assertEqual(peak, floor)
        if not confirmed:
            self.assertFalse(script.ultimate_running())
            self.assertEqual(script.component.entity_var(342).value(), 0)

    def test_nano_damage_dispatch_rejects_authored_outgoing_tag_only(self):
        definition = graph.graph(0x1DBC)
        if definition is None:
            self.skipTest("Supply the extracted Gravitic Flux target graph")
        fixture, run, pharah, ana, enemy = self.arena()
        self.cast(fixture, run, pharah, ana)
        fight = run.match.combat
        instance = pharah.body_script.component.create(definition, init=False)
        node = definition.node(14)
        tags = combat.damage_tags(instance.node_state(node), node.field("m_modifyHealth"))
        denied = 0x0D80000000003FCE
        self.assertIn(denied, tags)
        control = tuple(tag for tag in tags if tag != denied)
        for source, victim, event_tags, expected in (
            (pharah, enemy, tags, 40),
            (pharah, enemy, control, 60),
            (enemy, pharah, tags, 20),
        ):
            with self.subTest(source=source.hero.name, expected=expected):
                target = next(target for target in fight._enemies(source) if target.owner is victim)
                health = fight.vitals_of(target)
                before = health.total
                # Exercise the dispatcher with the extracted event tags.
                fight._damage(source, target, 40, False, None, run.frame, tags=event_tags)
                self.assertEqual(before - health.total, expected)

    def test_actual_nano_applied_in_flight_boosts_primary_at_impact(self):
        fixture, run, pharah, ana, enemy = self.arena()
        enemy.mover.position[2] = 28
        enemy.mover.body = None
        fight = run.match.combat
        vitals = fixture.health(run, enemy)
        before = vitals.total
        run.step({pharah: (0, 0, FIRE)})
        rocket, = fight.rockets
        self.assertNotIn(pharah.body, fight.nano_buffs)
        self.cast(fixture, run, pharah, ana)
        self.assertIs(fight.rockets[0], rocket)
        self.assertEqual(vitals.total, before)
        for _ in range(55):
            run.step({})
        self.assertEqual(vitals.total, before - 180)
        self.assertEqual(pharah.body_script.component.entity_var(342).value(), 180)

    def test_actual_nano_scales_nearby_splash_on_brigitte_shield(self):
        losses = []
        for boosted in (False, True):
            fixture, run, pharah, ana, brigitte = self.arena()
            hero = next(hero for hero in heroes().values() if hero.name == "Brigitte")
            self.assertTrue(run.match.switch_hero(brigitte, hero))
            pharah.mover.position[0] = 1.25
            brigitte.mover.position = [0, pharah.mover.position[1], 16]
            pharah.mover.body = brigitte.mover.body = None
            if boosted:
                self.cast(fixture, run, pharah, ana)
            else:
                for _ in range(10):
                    run.step({})
            fight = run.match.combat
            facing = {brigitte: (-32768, 0, SECONDARY)}
            for _ in range(5):
                run.step(facing)
            shield = fight.brigitte_barriers[brigitte.body]
            self.assertTrue(shield.raised())
            before = fixture.health(run, brigitte).total
            y = pharah.mover.position[1]
            yaw, pitch = aim(fight._eye(pharah), (1.25, y, 15))
            run.step({**facing, pharah: (yaw, pitch, FIRE)})
            for _ in range(25):
                run.step(facing)
            losses.append(250 - shield.health)
            self.assertEqual(fixture.health(run, brigitte).total, before)
            self.assertEqual(pharah.body_script.component.entity_var(342).value(), 0)
        self.assertGreater(losses[0], 0)
        self.assertLess(losses[0], 120)
        self.assertAlmostEqual(losses[1], losses[0] * 1.5, places=4)

    def test_actual_nano_scales_primary_and_melee_damage_to_mei_wall(self):
        fixture, run, pharah, ana, mei = self.arena()
        hero = next(hero for hero in heroes().values() if hero.name == "Mei")
        self.assertTrue(run.match.switch_hero(mei, hero))
        mei.mover.position = [0, pharah.mover.position[1], 16]
        mei.mover.body = None
        self.cast(fixture, run, pharah, ana)
        fight = run.match.combat
        facing = (-32768, 9500)
        run.step({mei: (*facing, 16)})
        run.step({mei: (*facing, 0)})
        run.step({mei: (*facing, FIRE)})
        pieces = list(fight.walls.pieces.values())
        self.assertEqual(len(pieces), 5)
        centre = pieces[2]
        self.assertEqual(centre.health, 400)
        vitals = fixture.health(run, mei)
        before = vitals.total
        fixture.fire(run, pharah)
        self.assertEqual(centre.health, 220)
        self.assertEqual(vitals.total, before)
        pharah.mover.position[2] = centre.feet[2] - 1
        pharah.mover.body = None
        run.step({pharah: (0, 0, 0x800)})
        for _ in range(15):
            run.step({})
        self.assertEqual(centre.health, 175)
        self.assertEqual(vitals.total, before)
        self.assertEqual(pharah.body_script.component.entity_var(342).value(), 0)

    def test_actual_nano_primary_breaks_baptiste_device_in_one_hit(self):
        fixture, run, pharah, ana, baptiste = self.arena()
        hero = next(hero for hero in heroes().values() if hero.name == "Baptiste")
        self.assertTrue(run.match.switch_hero(baptiste, hero))
        baptiste.mover.position = [0, pharah.mover.position[1], 16]
        baptiste.mover.body = None
        self.cast(fixture, run, pharah, ana)
        fight = run.match.combat
        run.step({baptiste: (16384, 3800, 16)})
        for _ in range(100):
            run.step({})
            ward = fight.baptiste_fields.get(baptiste.body)
            if ward is not None and baptiste.body in ward.bindings:
                break
        ward = fight.baptiste_fields[baptiste.body]
        self.assertIn(baptiste.body, ward.bindings)
        self.assertEqual(ward.health, 150)
        vitals = fixture.health(run, baptiste)
        before = vitals.total
        yaw, pitch = aim(fight._eye(pharah), ward.segment()[0])
        run.step({pharah: (yaw, pitch, FIRE)})
        for _ in range(55):
            run.step({})
            if baptiste.body not in fight.baptiste_fields:
                break
        self.assertEqual(ward.health, 0)
        self.assertNotIn(baptiste.body, fight.baptiste_fields)
        self.assertEqual(vitals.total, before)
        self.assertEqual(pharah.body_script.component.entity_var(342).value(), 0)

    def npc_catalog(self):
        bundle = os.environ.get("ORPHEUS_NANO_NPC_GRAPH_BUNDLE")
        catalog = os.environ.get("ORPHEUS_NPC_DATA_DIR")
        if not bundle or not catalog:
            self.skipTest("Supply the combined Nano and five-NPC graph bundle and catalog")
        mount = patch.object(graph, "GRAPHS_PATH", Path(bundle))
        mount.start()
        self.addCleanup(pve.PharahPveTests.clear_graphs)
        self.addCleanup(mount.stop)
        pve.PharahPveTests.clear_graphs()
        definitions = npc.load_catalog(Path(catalog) / "pve_npcs_174.json")
        self.assertEqual({d.name for d in definitions.values()},
                         {"Trooper", "Enforcer", "Sniper", "Assassin", "Heavy Assault"})
        return definitions

    def test_actual_nano_boosts_primary_against_five_authored_npcs(self):
        definitions = self.npc_catalog()
        return_damage = {
            "Trooper": 10, "Enforcer": 25, "Sniper": 50, "Assassin": 12.5, "Heavy Assault": 2.5,
        }
        for definition in definitions.values():
            with self.subTest(enemy=definition.name):
                fixture, run, pharah, ana, spare = self.arena()
                spare.mover.position[0] = 30
                spare.mover.body = None
                for player in run.players:
                    player.client = pve.NpcClient(player)
                distance = 1.8 if definition.name == "Assassin" else 8
                position = (0, pharah.mover.position[1], pharah.mover.position[2] + distance)
                bot = npc.spawn(run.match, definition, position, team=1)
                self.assertIsNotNone(bot.body_script)
                deadline = self.cast(fixture, run, pharah, ana)
                bot_health = fixture.health(run, bot)
                before = bot_health.total
                yaw, pitch = pve.PharahPveTests.aim_at_body(run, pharah, bot)
                pharah.mover.yaw, pharah.mover.pitch = yaw, pitch
                self.assertEqual(npc_control.start(run.match, pharah), bot.entity)
                if definition.name == "Enforcer":
                    self.assertEqual(npc_control.enforcer_walk(run.match, pharah), bot.entity)
                fight = run.match.combat
                health = fixture.health(run, pharah)
                for _ in range(140):
                    run.step({})
                    if health.total < 200:
                        break
                self.assertEqual(health.total, 200 - return_damage[definition.name])
                if definition.name == "Assassin":
                    self.assertTrue(fight.is_pinned(pharah))
                    run.step({pharah: (yaw, pitch, FIRE)})
                    self.assertFalse(any(rocket.shooter is pharah for rocket in fight.rockets))
                    self.assertEqual(bot_health.total, before)
                self.assertEqual(npc_control.stop(run.match), 1)
                run.step({})
                self.assertFalse(fight.is_pinned(pharah))
                yaw, pitch = pve.PharahPveTests.aim_at_body(run, pharah, bot)
                run.step({pharah: (yaw, pitch, FIRE)})
                for _ in range(60):
                    run.step({})
                self.assertLess(run.match.combat.tick, deadline)
                self.assertEqual(bot_health.total, before - 180)
                self.assertEqual(pharah.body_script.component.entity_var(342).value(), 180)

    def test_actual_nano_halves_heavy_charge_damage(self):
        heavy = next(d for d in self.npc_catalog().values() if d.name == "Heavy Assault")
        for contact in ("graze", "impact", "crush"):
            with self.subTest(contact=contact):
                fixture, run, pharah, ana, spare = self.arena()
                spare.mover.position[0] = 30
                spare.mover.body = None
                for player in run.players:
                    player.client = pve.NpcClient(player)
                y = pharah.mover.position[1]
                bot = npc.spawn(run.match, heavy, (0, y, 17), team=1)
                self.cast(fixture, run, pharah, ana)
                facing = pve.PharahPveTests.aim_at_body(run, pharah, bot)
                pharah.mover.yaw, pharah.mover.pitch = facing
                self.assertEqual(npc_control.start(run.match, pharah, charge=True), bot.entity)
                instance = heavy_charge.charge(bot)
                for _ in range(80):
                    run.step({})
                    if heavy_charge.travelling(instance):
                        break
                self.assertTrue(heavy_charge.travelling(instance))
                positions = {"graze": (2, y, 15), "impact": (0, y, 16.3), "crush": (0, y, 15)}
                pharah.mover.position[:] = positions[contact]
                pharah.mover.body = None
                fight = run.match.combat
                health = fixture.health(run, pharah)
                if contact == "crush":
                    run.match.collision_world = World(FLOOR + wall(-8, 11, 8, 11))
                    for _ in range(12):
                        run.step({})
                        if fight.is_pinned(pharah):
                            break
                    self.assertTrue(fight.is_pinned(pharah))
                    _, victim_effect = fight.heavy_pins[bot.entity]
                    self.assertEqual(health.total, 200)
                    base = instance.find_var(0, 476).value()
                    self.assertGreaterEqual(base, 200)
                    for _ in range(40):
                        run.step({})
                        if not fight.is_pinned(pharah):
                            break
                    self.assertEqual(health.total, max(0, 200 - base / 2))
                    self.assertNotIn(bot.entity, fight.heavy_pins)
                    self.assertNotIn(victim_effect.id, victim_effect.component.instances)
                    self.assertEqual(heavy_charge.phase(instance), 0)
                    continue
                for _ in range(25):
                    run.step({})
                    if health.total < 200:
                        break
                self.assertEqual(health.total, 112.5 if contact == "impact" else 175)
                self.assertIn(pharah.body, fight.nano_buffs)
                if contact == "impact":
                    self.assertEqual(fight.stuns[pharah.body] - run.frame, 125)
                    self.assertEqual(heavy_charge.phase(instance), 0)
                    run.step({pharah: (0, 0, 8)})
                    self.assertNotEqual(pharah.mover.velocity[1], 20)
                    while run.frame < fight.stuns[pharah.body] - 1:
                        run.step({})
                    run.step({pharah: (0, 0, 8)})
                    self.assertEqual(pharah.mover.velocity[1], 20)
                else:
                    self.assertEqual(fight.heavy_grazes[bot.entity], {pharah.body})
                    self.assertTrue(heavy_charge.travelling(instance))
                    self.assertFalse(fight.is_stunned(pharah))
                    for _ in range(10):
                        run.step({})
                    self.assertEqual(health.total, 175)
                    self.assertGreater(pharah.mover.position[0], 2)

    def test_actual_nano_scales_primary_damage_to_winston_barrier(self):
        fixture, run, pharah, ana, winston = self.arena()
        hero = next(hero for hero in heroes().values() if hero.name == "Winston")
        self.assertTrue(run.match.switch_hero(winston, hero))
        winston.mover.position = [0, pharah.mover.position[1], 18]
        winston.mover.body = None
        self.cast(fixture, run, pharah, ana)
        run.step({winston: (0, 0, 16)})
        for _ in range(3):
            run.step({})
        bubble = run.match.combat.winston_bubbles[winston.body]
        self.assertEqual(bubble.health, 700)
        vitals = fixture.health(run, winston)
        before = vitals.total
        fixture.fire(run, pharah)
        self.assertEqual(bubble.health, 520)
        self.assertEqual(vitals.total, before)
        self.assertEqual(pharah.body_script.component.entity_var(342).value(), 0)

    def test_actual_nano_scales_zarya_personal_barrier_damage_and_energy(self):
        fixture, run, pharah, ana, zarya = self.arena()
        hero = next(hero for hero in heroes().values() if hero.name == "Zarya")
        self.assertTrue(run.match.switch_hero(zarya, hero))
        zarya.mover.position = [0, pharah.mover.position[1], 16]
        zarya.mover.body = None
        self.cast(fixture, run, pharah, ana)
        fight = run.match.combat
        run.step({zarya: (0, 0, 8)})
        for _ in range(3):
            run.step({})
        bubble = fight.zarya_bubbles[zarya.body, zarya_barrier.PERSONAL]
        self.assertEqual(bubble.health, 200)
        vitals = fixture.health(run, zarya)
        before = vitals.total
        run.step({pharah: (0, 0, FIRE)})
        for _ in range(40):
            if bubble.health < 200:
                break
            run.step({})
        self.assertEqual(bubble.health, 20)
        self.assertEqual(vitals.total, before)
        self.assertAlmostEqual(zarya.body_script.component.entity_var(87).value(), 36, places=4)
        self.assertEqual(pharah.body_script.component.entity_var(342).value(), 0)

    def test_actual_nano_scales_melee_against_personal_and_projected_barriers(self):
        fixture = defense.PharahDefenseTests()
        roster = {hero.name: hero for hero in heroes().values()}
        for projected in (False, True):
            with self.subTest(projected=projected):
                run = Run(KANEZAKA_DM, ("Pharah", "Ana", "Zarya", "Ally"))
                pharah, ana, zarya, ally = run.players
                match = run.match
                match.game_map = replace(match.game_map, free_for_all=False)
                match.bots.clear()
                match._pending_bot_scripts.clear()
                for player, name in ((pharah, "Pharah"), (ana, "Ana"), (zarya, "Zarya")):
                    self.assertTrue(match.switch_hero(player, roster[name]))
                pharah.team = ana.team = 0
                zarya.team = ally.team = 1
                y = pharah.mover.position[1]
                pharah.mover.position = [0, y, 8]
                ana.mover.position = [0, y, 0]
                zarya.mover.position = [4.5 if projected else 0, y, 9.8]
                ally.mover.position = [0 if projected else 20, y, 9.8]
                for player in run.players:
                    player.mover.body = None
                    player.body_script.ult_seconds = 0
                    player.body_script.set_ult_charge(0)
                for _ in range(30):
                    run.step({})
                script = ana.body_script
                script.set_ult_charge(script.component.entity_var(338).value())
                self.cast(fixture, run, pharah, ana)
                target = ally if projected else zarya
                bubble = bubbles.ZaryaProjectileTests().deploy(run, zarya, target, projected)
                vitals = fixture.health(run, target)
                before = vitals.total
                run.step({pharah: (0, 0, 0x800)})
                self.assertEqual(bubble.health, 155)
                self.assertEqual(vitals.total, before)
                self.assertAlmostEqual(zarya.body_script.component.entity_var(87).value(), 9, places=4)
                self.assertEqual(pharah.body_script.component.entity_var(342).value(), 0)
                self.assertFalse(pharah.client.messages(25001))

    def test_actual_grasp_consumes_boosted_primary_without_damage_or_charge(self):
        fixture, run, pharah, ana, sigma = self.arena()
        hero = next(hero for hero in heroes().values() if hero.name == "Sigma")
        self.assertTrue(run.match.switch_hero(sigma, hero))
        sigma.mover.position = [0, pharah.mover.position[1], 14]
        sigma.mover.body = None
        deadline = self.cast(fixture, run, pharah, ana)
        fight = run.match.combat
        vitals = fixture.health(run, sigma)
        before = vitals.total
        run.step({sigma: (-32768, 0, 8)})
        for _ in range(5):
            run.step({sigma: (-32768, 0, 0)})
        run.step({pharah: (0, 0, FIRE), sigma: (-32768, 0, 0)})
        self.assertTrue(any(rocket.shooter is pharah for rocket in fight.rockets))
        for _ in range(40):
            run.step({sigma: (-32768, 0, 0)})
        self.assertFalse(fight.rockets)
        self.assertLess(fight.tick, deadline)
        self.assertEqual(vitals.total, before)
        self.assertEqual(fixture.health(run, pharah).total, 200)
        self.assertEqual(pharah.body_script.component.entity_var(342).value(), 0)
        instance = next(i for i in sigma.body_script.component.instances.values()
                        if i.graph.index == sigma_grasp.GRASP)
        # The local interception adapter feeds the raw payload to the authored accumulator.
        self.assertEqual(instance.vars[21312].value(), 120)
        for _ in range(160):
            if sigma.body in fight.grasp_shields:
                break
            run.step({sigma: (-32768, 0, 0)})
        self.assertEqual(vitals.temporary_shields[sigma_grasp.SHIELD_TAG].now, 72)
        self.assertEqual(vitals.total, before + 72)

    def test_actual_deflect_changes_outgoing_source_and_keeps_nano_incoming(self):
        fixture, run, pharah, ana, genji = self.arena()
        hero = next(hero for hero in heroes().values() if hero.name == "Genji")
        self.assertTrue(run.match.switch_hero(genji, hero))
        genji.mover.position = [0, pharah.mover.position[1], 14]
        genji.mover.body = None
        self.cast(fixture, run, pharah, ana)
        vitals = fixture.health(run, genji)
        before = vitals.total
        run.step({genji: (-32768, 0, 16)})
        for _ in range(5):
            run.step({genji: (-32768, 0, 0)})
        run.step({pharah: (0, 0, FIRE), genji: (-32768, 0, 0)})
        for _ in range(60):
            run.step({genji: (-32768, 0, 0)})
        self.assertEqual(vitals.total, before)
        self.assertEqual(fixture.health(run, pharah).total, 140)
        self.assertEqual(pharah.body_script.component.entity_var(342).value(), 0)
        self.assertEqual([h["+0x78"]["+0x4"] for h in genji.client.messages(25001)], [20, 40])

    def test_actual_nano_reduces_incoming_rifle_damage_and_retires_with_body(self):
        fixture, run, pharah, ana, enemy = self.arena()
        self.cast(fixture, run, pharah, ana)
        aim = fixture.aim_at_body(run.match.combat, enemy, pharah)
        run.step({enemy: (*aim, FIRE)})
        for _ in range(15):
            run.step({})
        self.assertEqual(fixture.health(run, pharah).total, 190)
        retired = pharah.body
        position = list(pharah.mover.position)
        self.assertTrue(run.match.switch_hero(pharah, pharah.hero))
        self.assertNotEqual(pharah.body, retired)
        self.assertNotIn(retired, run.match.combat.nano_buffs)
        pharah.mover.position = position
        pharah.mover.body = None
        for _ in range(22):
            run.step({})
        aim = fixture.aim_at_body(run.match.combat, enemy, pharah)
        run.step({enemy: (*aim, FIRE)})
        for _ in range(15):
            run.step({})
        self.assertEqual(fixture.health(run, pharah).total, 180)
        self.assertNotIn(pharah.body, run.match.combat.nano_buffs)

    def test_actual_nano_discord_and_fortify_compose_before_armor(self):
        fixture = defense.PharahDefenseTests()
        roster = {hero.name: hero for hero in heroes().values()}
        run = Run(KANEZAKA_DM, ("Pharah", "Ana", "Zenyatta", "Orisa"))
        pharah, ana, zen, orisa = run.players
        match = run.match
        match.game_map = replace(match.game_map, free_for_all=False)
        match.bots.clear()
        match._pending_bot_scripts.clear()
        y = pharah.mover.position[1]
        for player, name, team, x, z in (
            (pharah, "Pharah", 0, 0, 8), (ana, "Ana", 0, 0, 0),
            (zen, "Zenyatta", 0, 3, 10), (orisa, "Orisa", 1, 0, 16),
        ):
            self.assertTrue(match.switch_hero(player, roster[name]))
            player.team = team
            player.mover.position = [x, y, z]
            player.mover.body = None
            player.body_script.ult_seconds = 0
            player.body_script.set_ult_charge(0)
        for _ in range(30):
            run.step({})
        script = ana.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        self.cast(fixture, run, pharah, ana)
        orb = discord.PharahDiscordTests().cast(run, zen, orisa)
        self.assertEqual(orb.target, orisa.body)
        fortify.PharahFortifyTests().fortify(run, orisa)
        vitals = fixture.health(run, orisa)
        before = vitals.total
        fixture.fire(run, pharah)
        self.assertAlmostEqual(before - vitals.total, 125, places=4)
        self.assertAlmostEqual(pharah.body_script.component.entity_var(342).value(), 125, places=4)

    def test_actual_nano_keeps_primary_self_damage_and_no_charge(self):
        losses = []
        for boosted in (False, True):
            with self.subTest(boosted=boosted):
                fixture, run, pharah, ana, enemy = self.arena()
                enemy.mover.position[2] = 16
                enemy.mover.body = None
                if boosted:
                    self.cast(fixture, run, pharah, ana)
                else:
                    for _ in range(10):
                        run.step({})
                fight = run.match.combat
                health = fixture.health(run, pharah)
                before = health.total
                x, y, z = pharah.mover.position
                yaw, pitch = aim(fight._eye(pharah), (x, y, z + .8))
                run.step({pharah: (yaw, pitch, FIRE)})
                for _ in range(40):
                    run.step({})
                losses.append(before - health.total)
                self.assertEqual(fixture.health(run, enemy).total, 200)
                self.assertEqual(pharah.body_script.component.entity_var(342).value(), 0)
                self.assertFalse(pharah.client.messages(25001))
        self.assertGreater(losses[0], 0)
        self.assertAlmostEqual(losses[0], losses[1], places=5)

    def test_actual_nano_keeps_concussion_impulse_and_zero_damage(self):
        displacements = []
        for boosted in (False, True):
            with self.subTest(boosted=boosted):
                fixture, run, pharah, ana, enemy = self.arena()
                enemy.mover.position[2] = 16
                enemy.mover.body = None
                if boosted:
                    self.cast(fixture, run, pharah, ana)
                else:
                    for _ in range(10):
                        run.step({})
                old_self, old_enemy = pharah.mover.position[2], enemy.mover.position[2]
                run.step({pharah: (0, 0, 16)})
                for _ in range(40):
                    run.step({})
                displacements.append((pharah.mover.position[2] - old_self,
                                      enemy.mover.position[2] - old_enemy))
                self.assertEqual(fixture.health(run, enemy).total, 200)
                self.assertEqual(fixture.health(run, pharah).total, 200)
                self.assertEqual(pharah.body_script.component.entity_var(342).value(), 0)
                self.assertFalse(pharah.client.messages(25001))
        self.assertLess(displacements[0][0], -3)
        self.assertGreater(displacements[0][1], 3)
        for normal, boosted in zip(*displacements, strict=True):
            self.assertAlmostEqual(boosted, normal, places=5)

    def test_actual_nano_boosts_barrage_damage_to_winston_barrier_without_charge(self):
        fixture, run, pharah, ana, winston = self.arena()
        hero = next(hero for hero in heroes().values() if hero.name == "Winston")
        self.assertTrue(run.match.switch_hero(winston, hero))
        winston.mover.position = [0, pharah.mover.position[1], 18]
        winston.mover.body = None
        self.cast(fixture, run, pharah, ana)
        fight = run.match.combat
        run.step({winston: (0, 0, 16)})
        for _ in range(3):
            run.step({})
        bubble = fight.winston_bubbles[winston.body]
        self.assertEqual(bubble.health, 700)
        vitals = fixture.health(run, winston)
        before = vitals.total
        script = pharah.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        run.step({pharah: (0, 0, 32)})
        self.assertTrue(script.ultimate_running())
        for _ in range(35):
            if bubble.health < 700:
                break
            run.step({})
        self.assertEqual(bubble.health, 640)
        self.assertEqual(vitals.total, before)
        self.assertEqual(script.component.entity_var(342).value(), 0)

    def test_actual_nano_boosts_melee_and_barrage_without_barrage_charge(self):
        fixture, run, pharah, ana, enemy = self.arena()
        enemy.mover.position[2] = 9.8
        enemy.mover.body = None
        self.cast(fixture, run, pharah, ana)
        health = fixture.health(run, enemy)
        script = pharah.body_script
        run.step({pharah: (0, 0, 0x800)})
        self.assertEqual(health.total, 155)
        self.assertEqual(script.component.entity_var(342).value(), 45)
        for _ in range(70):
            run.step({})
        script.set_ult_charge(script.component.entity_var(338).value())
        aim = fixture.aim_at_body(run.match.combat, pharah, enemy)
        run.step({pharah: (*aim, 32)})
        self.assertTrue(script.ultimate_running())
        for _ in range(18):
            if health.total < 155:
                break
            run.step({pharah: (*aim, 0)})
        self.assertEqual(health.total, 95)
        self.assertEqual(script.component.entity_var(342).value(), 0)

    def test_actual_enemy_grenade_blocks_nano_heal_without_blocking_damage_modifiers(self):
        fixture, run, pharah, ana, enemy_ana = self.arena()
        self.assertTrue(run.match.switch_hero(enemy_ana, ana.hero))
        enemy_ana.mover.position = [0, pharah.mover.position[1], 16]
        enemy_ana.mover.body = None
        for _ in range(4):
            run.step({})
        grenade.PharahGrenadeTests().throw(run, enemy_ana, pharah)
        health = fixture.health(run, pharah)
        self.assertEqual(health.total, 150)
        (_, blocked_until), = health.healing_blocks
        fight = run.match.combat
        run.step({ana: (0, 0, 32)})
        for _ in range(16):
            run.step({})
            if pharah.body in fight.nano_buffs:
                break
        self.assertEqual(ana.body_script.component.entity_var(342).value(), 0)
        self.assertLess(fight.tick, blocked_until)
        self.assertEqual(fight.nano_buffs[pharah.body][1:], (.5, 1.5))
        self.assertEqual(health.total, 150)
        fixture.fire(run, pharah)
        self.assertEqual(fixture.health(run, enemy_ana).total, 20)
        self.assertEqual(pharah.body_script.component.entity_var(342).value(), 180)
        while fight.tick < blocked_until:
            run.step({})
        self.assertEqual(health.total, 150)
        self.assertIn(pharah.body, fight.nano_buffs)

    def test_actual_nano_windup_rechecks_recipient_identity_and_team(self):
        for change in ("death", "replacement", "team"):
            with self.subTest(change=change):
                fixture, run, pharah, ana, enemy = self.arena()
                match = run.match
                fight = match.combat
                health = fixture.health(run, pharah)
                health.damage(190)
                original = pharah.body
                charge = ana.body_script.component.entity_var(342)
                run.step({ana: (0, 0, 32)})
                self.assertEqual(charge.value(), 0)
                self.assertNotIn(original, fight.nano_buffs)
                expected_health = 0
                if change == "replacement":
                    position = list(pharah.mover.position)
                    self.assertTrue(match.switch_hero(pharah, pharah.hero))
                    self.assertNotEqual(pharah.body, original)
                    pharah.mover.position = position
                    pharah.mover.body = None
                    health = fixture.health(run, pharah)
                    health.damage(150)
                    expected_health = 50
                elif change == "team":
                    pharah.team = enemy.team
                    expected_health = 10
                else:
                    facing = fixture.aim_at_body(fight, enemy, pharah)
                    run.step({enemy: (*facing, FIRE)})
                for _ in range(18):
                    run.step({})
                self.assertNotIn(original, fight.nano_buffs)
                self.assertNotIn(pharah.body, fight.nano_buffs)
                self.assertEqual(health.total, expected_health)
                self.assertEqual(charge.value(), 0)

    def test_flashbang_during_actual_nano_windup_prevents_target_effect(self):
        fixture, run, pharah, ana, enemy = self.arena()
        cassidy = heroes()[0x02E0000000000042]
        self.assertTrue(run.match.switch_hero(enemy, cassidy))
        enemy.mover.position = [1.8, ana.mover.position[1], 0]
        enemy.mover.body = None
        for _ in range(8):
            run.step({})
        fight = run.match.combat
        health = fixture.health(run, pharah)
        health.damage(150)
        aim = fixture.aim_at_body(fight, enemy, ana)
        run.step({ana: (0, 0, 32), enemy: (*aim, 16)})
        self.assertEqual(ana.body_script.component.entity_var(342).value(), 0)
        for _ in range(18):
            run.step({})
            self.assertEqual(health.total, 50)
            self.assertNotIn(pharah.body, fight.nano_buffs)
        self.assertTrue(fight.is_stunned(ana))

    def test_actual_nano_obeys_authored_range_at_acquisition(self):
        for distance in (39.99, 40.01):
            with self.subTest(distance=distance):
                fixture, run, pharah, ana, enemy = self.arena()
                pharah.mover.position[2] = distance
                enemy.mover.position[0] = 30
                pharah.mover.body = enemy.mover.body = None
                for _ in range(4):
                    run.step({})
                if distance < 40:
                    self.cast(fixture, run, pharah, ana)
                else:
                    charge = ana.body_script.component.entity_var(342)
                    before = charge.value()
                    run.step({ana: (0, 0, 32)})
                    for _ in range(18):
                        run.step({})
                    self.assertEqual(charge.value(), before)
                    self.assertNotIn(pharah.body, run.match.combat.nano_buffs)

    def test_actual_nano_keeps_charge_without_a_visible_eligible_ally(self):
        for mode in ("enemy", "cover", "range", "dead"):
            with self.subTest(mode=mode):
                fixture, run, pharah, ana, enemy = self.arena()
                fight = run.match.combat
                if mode == "dead":
                    health = fixture.health(run, pharah)
                    health.damage(190)
                    facing = fixture.aim_at_body(fight, enemy, pharah)
                    run.step({enemy: (*facing, FIRE)})
                    self.assertTrue(health.dead)
                elif mode == "enemy":
                    pharah.team = 1
                elif mode == "range":
                    pharah.mover.position[2] = 41
                    pharah.mover.body = None
                y = pharah.mover.position[1]
                wall = World([(-4, y, 4, 4, y + 5, 4, 4, y, 4),
                              (-4, y, 4, -4, y + 5, 4, 4, y + 5, 4)])
                charge = ana.body_script.component.entity_var(342)
                before = charge.value()
                world = wall if mode == "cover" else fight._collision()
                with patch.object(fight, "_collision", return_value=world):
                    run.step({ana: (0, 0, 32)})
                    for _ in range(18):
                        run.step({})
                self.assertEqual(charge.value(), before)
                self.assertFalse(fight.nano_buffs)
