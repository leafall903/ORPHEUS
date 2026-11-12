"""Actual Flashbang input against Soldier's authored skill lifetimes.

The imported stun host is reconstructed, not native status replication.
"""

import math
import unittest
from unittest.mock import patch

import test_mccree_flashbang as flash
import test_soldier_combat_lane as lane
from test_game_combat import aim, in_world

from ow174.game import combat, defenses, projectiles, stats, winston_barrier, zarya_barriers as zarya_barrier
from ow174.game.collision import World
from ow174.game.commands import Command
from ow174.game.content import heroes
from ow174.game.script import nodes


class SoldierControlTests(unittest.TestCase):
    arena = flash.FlashbangTests.arena
    health = flash.FlashbangTests.health
    step = flash.FlashbangTests.step
    cast = flash.FlashbangTests.cast
    add_bot = lane.SoldierCombatLaneTests.add_bot

    def scene(self):
        run, caster, soldier = self.arena(distance=3)
        for player in run.players:
            player.body_script.ult_seconds = 0
        return run, caster, soldier

    def fields(self, run, soldier):
        return [s for s in run.match.combat.projectiles.shots
                if s.player is soldier and s.spawn == projectiles.FIELD_ENTITY]

    def sleep_scene(self):
        run, ana, soldier = self.scene()
        self.assertTrue(run.match.switch_hero(ana, heroes()[0x02E000000000013B]))
        floor = soldier.mover.position[1]
        ana.mover.position = [0, floor, 0]
        ana.body_script.ult_seconds = 0
        run.step({})
        return run, ana, soldier

    def phase_line(self, hero_guid, front_z):
        run, ana, soldier = self.sleep_scene()
        floor = soldier.mover.position[1]
        soldier.mover.position = [0, floor, 7]
        soldier.mover.body = None
        hero = heroes()[hero_guid]
        front = run.match.add_player(3, "Front", hero.guid, soldier.team, False)
        in_world(run.match, front)
        run.players.append(front)
        self.assertTrue(run.match.switch_hero(front, hero))
        front.mover.position = [0, floor, front_z]
        front.mover.body = None
        front.body_script.ult_seconds = 0
        run.step({})
        return run, ana, front, soldier

    def enter_front_phase(self, run, front, button):
        if front.hero.guid == defenses.ZENYATTA:
            script = front.body_script
            script.set_ult_charge(script.component.entity_var(338).value())
        if button:
            run.step({front: (0, 0, button)})
        self.assertEqual(defenses.invulnerable(front), bool(button))

    def grenade_at_feet(self, run, ana, soldier):
        fight = run.match.combat
        previous = fight.heal_modifiers.get(soldier.body)
        deadlines = None if previous is None else (previous.anti_until, previous.boost_until)
        yaw, pitch = aim(fight._eye(ana), tuple(soldier.mover.position))
        run.step({ana: (yaw, pitch, 16)})
        for _ in range(35):
            modifier = fight.heal_modifiers.get(soldier.body)
            if modifier is not None and (modifier.anti_until, modifier.boost_until) != deadlines:
                return modifier
            run.step({ana: (yaw, pitch, 0)})
        self.fail("Actual Ana grenade did not apply a healing modifier")

    def start_field(self, run, soldier):
        run.step({soldier: (0, 0, 16)})
        for _ in range(80):
            for shot in self.fields(run, soldier):
                if shot.field_tick is not None:
                    return
            run.step({})
        self.fail("Actual Biotic Field did not deploy")

    def fire_ana_rifle(self, run, ana, soldier, *, scoped):
        x, y, z = soldier.mover.position
        angles = aim(run.match.combat._eye(ana), (x, y + 1, z))
        if scoped:
            for _ in range(35):
                run.step({ana: (*angles, 2)})
        run.step({ana: (*angles, 3 if scoped else 1)})
        self.assertEqual(bool(run.match.combat.rockets), not scoped)
        for _ in range(8):
            run.step({ana: (*angles, 2 if scoped else 0)})

    def test_actual_ana_rifle_delivers_hostile_and_friendly_doses(self):
        for scoped in (False, True):
            for jump in (False, True):
                with self.subTest(scoped=scoped, jump=jump):
                    run, ana, soldier = self.sleep_scene()
                    fight = run.match.combat
                    self.fire_ana_rifle(run, ana, soldier, scoped=scoped)
                    self.assertEqual(len(fight.biotic_doses), 1)
                    self.assertGreater(self.health(run, soldier), 130)
                    self.assertLess(self.health(run, soldier), 200)
                    if jump:
                        run.frame += 80
                        run.match.update(run.frame * combat.TICK, run.frame)
                    else:
                        for _ in range(80):
                            run.step({})
                    self.assertEqual(self.health(run, soldier), 130)
                    self.assertFalse(fight.biotic_doses)
                    ana.team = soldier.team
                    self.fire_ana_rifle(run, ana, soldier, scoped=scoped)
                    self.assertEqual(len(fight.biotic_doses), 1)
                    self.assertGreater(self.health(run, soldier), 130)
                    self.assertLess(self.health(run, soldier), 200)
                    for _ in range(45):
                        run.step({})
                    self.assertEqual(self.health(run, soldier), 200)
                    self.assertEqual(ana.stats.value(stats.HEALING), 70)
                    self.assertFalse(fight.biotic_doses)

    def test_friendly_ana_rifle_does_not_restore_blocked_dose_after_antiheal(self):
        for jump in (False, True):
            with self.subTest(jump=jump):
                run, enemy, soldier = self.sleep_scene()
                fight = run.match.combat
                ally = run.match.add_player(3, "Friendly Ana", enemy.hero.guid, soldier.team, False)
                in_world(run.match, ally)
                run.players.append(ally)
                self.assertTrue(run.match.switch_hero(ally, enemy.hero))
                ally.mover.position = [2, soldier.mover.position[1], 1]
                run.step({})
                deadline = self.grenade_at_feet(run, enemy, soldier).anti_until
                run.frame = deadline - 15
                run.match.update(run.frame * combat.TICK, run.frame)
                self.fire_ana_rifle(run, ally, soldier, scoped=False)
                dose, = fight.biotic_doses
                self.assertLess(run.frame, deadline)
                self.assertEqual(self.health(run, soldier), 140)
                elapsed_before_expiry = (deadline - 1 - dose.start) * combat.TICK
                blocked = round(70 * elapsed_before_expiry / combat.ANA_RIFLE_DURATION * 1024) / 1024
                if jump:
                    run.frame += 45
                    run.match.update(run.frame * combat.TICK, run.frame)
                else:
                    for _ in range(45):
                        run.step({})
                self.assertFalse(fight.biotic_doses)
                self.assertAlmostEqual(self.health(run, soldier), 140 + 70 - blocked)
                self.assertAlmostEqual(ally.stats.value(stats.HEALING), 70 - blocked)

    def test_ana_rifle_projected_barrier_blocks_dose_even_on_break(self):
        for scoped in (False, True):
            for breaking in (False, True):
                with self.subTest(scoped=scoped, breaking=breaking):
                    run, ana, soldier, zarya, rifle = self.projected_scene(with_rifle=True)
                    self.assertTrue(run.match.switch_hero(ana, heroes()[0x02E000000000013B]))
                    ana.mover.position = [0, soldier.mover.position[1], 0]
                    run.step({})
                    self.project(run, soldier, zarya)
                    fight = run.match.combat
                    shields = fight.zarya_barriers.projected
                    if breaking:
                        x, y, z = soldier.mover.position
                        angles = aim(fight._eye(rifle), (x, y + 1, z))
                        for _ in range(100):
                            run.step({rifle: (*angles, 1)})
                            if shields[zarya.body][1] == 20:
                                break
                        self.assertEqual(shields[zarya.body][1], 20)
                    self.fire_ana_rifle(run, ana, soldier, scoped=scoped)
                    self.assertFalse(fight.biotic_doses)
                    if breaking:
                        self.assertNotIn(zarya.body, shields)
                    else:
                        self.assertEqual(shields[zarya.body][1], 130)
                    self.assertEqual(self.health(run, soldier), 200)
                    self.assertEqual(ana.stats.value(stats.HERO_DAMAGE), 0)

    def test_ana_rifle_dose_keeps_original_source_and_target_body(self):
        for replace_recipient in (False, True):
            with self.subTest(replace_recipient=replace_recipient):
                run, ana, soldier = self.sleep_scene()
                fight = run.match.combat
                for health in (130, 60):
                    self.fire_ana_rifle(run, ana, soldier, scoped=False)
                    for _ in range(50):
                        run.step({})
                    self.assertEqual(self.health(run, soldier), health)
                self.fire_ana_rifle(run, ana, soldier, scoped=False)
                dose, = fight.biotic_doses
                old_source, old_target = ana.body, soldier.body
                self.assertEqual((dose.source, dose.target), (old_source, old_target))
                if replace_recipient:
                    self.assertTrue(run.match.switch_hero(soldier, soldier.hero))
                    self.assertNotEqual(soldier.body, old_target)
                    self.assertFalse(fight.biotic_doses)
                else:
                    self.assertTrue(run.match.switch_hero(ana, soldier.hero))
                    self.assertNotEqual(ana.body, old_source)
                    self.assertEqual(fight.biotic_doses, [dose])
                for _ in range(45):
                    run.step({})
                self.assertFalse(fight.biotic_doses)
                if replace_recipient:
                    self.assertEqual(self.health(run, soldier), 200)
                else:
                    self.assertTrue(fight.is_dead(soldier))
                    self.assertEqual(fight.vitals[old_target].killer, old_source)

    def test_ana_antigrenade_suspends_deployed_field_until_world_expiry(self):
        for jump in (False, True):
            with self.subTest(jump=jump):
                run, ana, soldier = self.sleep_scene()
                self.start_field(run, soldier)
                modifier = self.grenade_at_feet(run, ana, soldier)
                self.assertEqual(self.health(run, soldier), 140)
                self.assertEqual(modifier.anti_until - run.frame, 250)
                for _ in range(16):
                    run.step({})
                self.assertEqual(self.health(run, soldier), 140)
                self.assertEqual(soldier.body_script.component.entity_var(342).value(), 0)
                if jump:
                    run.frame = modifier.anti_until + 3
                    run.match.update(run.frame * combat.TICK, run.frame)
                else:
                    run.frame = modifier.anti_until - 1
                    run.match.update(run.frame * combat.TICK, run.frame)
                    self.assertEqual(self.health(run, soldier), 140)
                    for _ in range(4):
                        run.step({})
                self.assertAlmostEqual(self.health(run, soldier), 140 + 4 * 35 * combat.TICK)
                self.assertNotIn(soldier.body, run.match.combat.heal_modifiers)

    def test_ana_grenade_routes_around_phase_and_suspends_soldier_field(self):
        for hero_guid, button in (
            (defenses.REAPER, 0),
            (defenses.REAPER, 8),
            (defenses.MEI, 8),
            (defenses.ZENYATTA, 0x20),
        ):
            with self.subTest(hero=hero_guid, button=button):
                run, ana, front, soldier = self.phase_line(hero_guid, 1.5)
                fight = run.match.combat
                self.start_field(run, soldier)
                before = self.health(run, front)
                self.enter_front_phase(run, front, button)
                passes = bool(button) and hero_guid == defenses.REAPER
                self.assertEqual(defenses.intangible(front), passes)

                angles = aim(fight._eye(ana), tuple(soldier.mover.position))
                run.step({ana: (*angles, 16)})
                launched = False
                for _ in range(40):
                    flying = any(rocket.blast is not None and rocket.blast.effect == combat.ANA_GRENADE_EFFECT
                                 for rocket in fight.rockets)
                    if launched and not flying:
                        break
                    launched |= flying
                    run.step({})
                else:
                    self.fail("Actual Ana grenade did not complete flight")
                self.assertEqual(defenses.invulnerable(front), bool(button))
                self.assertEqual(self.health(run, front), before - (0 if button else 60))
                self.assertEqual(self.health(run, soldier), 140 if passes else 200)
                expected = {soldier.body} if passes else ({front.body} if not button else set())
                self.assertEqual(set(fight.heal_modifiers), expected)
                if passes:
                    modifier = fight.heal_modifiers[soldier.body]
                    for _ in range(10):
                        run.step({})
                    self.assertEqual(self.health(run, soldier), 140)
                    self.assertEqual(soldier.stats.value(stats.HEALING), 0)
                    run.frame = modifier.anti_until + 3
                    run.match.update(run.frame * combat.TICK, run.frame)
                    self.assertAlmostEqual(self.health(run, soldier), 140 + 4 * 35 * combat.TICK)

    def test_enemy_ana_grenade_refresh_and_friendly_boost_overlap_during_field(self):
        run, ana, soldier = self.sleep_scene()
        floor = soldier.mover.position[1]
        second = run.match.add_player(3, "Enemy Ana", ana.hero.guid, ana.team, False)
        friendly = run.match.add_player(4, "Friendly Ana", ana.hero.guid, soldier.team, False)
        for caster, position in ((second, [-2, floor, 1]), (friendly, [2, floor, 1])):
            in_world(run.match, caster)
            run.players.append(caster)
            self.assertTrue(run.match.switch_hero(caster, ana.hero))
            caster.mover.position = position
        run.step({})
        self.start_field(run, soldier)
        first = self.grenade_at_feet(run, ana, soldier)
        first_start, first_end = first.anti_from, first.anti_until
        refreshed = self.grenade_at_feet(run, second, soldier)
        refreshed_end = refreshed.anti_until
        self.assertEqual(refreshed.anti_from, first_start)
        self.assertGreater(refreshed_end, first_end)
        self.assertEqual(refreshed_end - run.frame, 250)
        self.assertEqual(self.health(run, soldier), 80)
        overlap = self.grenade_at_feet(run, friendly, soldier)
        self.assertLess(overlap.boost_from, refreshed_end)
        self.assertGreater(overlap.boost_until, refreshed_end)
        self.assertEqual(self.health(run, soldier), 80)
        self.assertEqual(friendly.stats.value(stats.HEALING), 0)
        run.frame = first_end
        run.match.update(run.frame * combat.TICK, run.frame)
        self.assertEqual(self.health(run, soldier), 80)
        run.frame = refreshed_end - 1
        run.match.update(run.frame * combat.TICK, run.frame)
        self.assertEqual(self.health(run, soldier), 80)
        run.step({})
        self.assertAlmostEqual(self.health(run, soldier), 80 + 35 * combat.TICK * 1.5)

    def test_ana_antiheal_survives_caster_switch_but_clears_with_soldier_body(self):
        for killed in (False, True):
            with self.subTest(killed=killed):
                run, ana, soldier = self.sleep_scene()
                fight = run.match.combat
                self.start_field(run, soldier)
                modifier = self.grenade_at_feet(run, ana, soldier)
                old_body = soldier.body
                ana_position = ana.mover.position.copy()
                self.assertTrue(run.match.switch_hero(ana, soldier.hero))
                ana.mover.position = ana_position
                ana.mover.body = None
                for _ in range(5):
                    run.step({})
                self.assertIs(fight.heal_modifiers[old_body], modifier)
                self.assertEqual(self.health(run, soldier), 140)
                if killed:
                    x, y, z = soldier.mover.position
                    angles = aim(fight._eye(ana), (x, y + 1, z))
                    for _ in range(75):
                        run.step({ana: (*angles, 1)})
                        if fight.is_dead(soldier):
                            break
                    self.assertTrue(fight.is_dead(soldier))
                    self.assertLess(run.frame, modifier.anti_until)
                    self.assertNotIn(old_body, fight.heal_modifiers)
                    due, = [due for due, player in fight.respawns if player is soldier]
                    run.frame = math.ceil(due / combat.TICK) + 1
                    run.match.update(run.frame * combat.TICK, run.frame)
                else:
                    self.assertTrue(run.match.switch_hero(soldier, soldier.hero))
                    self.assertLess(run.frame, modifier.anti_until)
                self.assertNotEqual(soldier.body, old_body)
                self.assertNotIn(old_body, fight.heal_modifiers)
                self.assertNotIn(soldier.body, fight.heal_modifiers)
                self.assertEqual(self.health(run, soldier), 200)

    def test_ana_grenade_impact_inside_field_catch_up_preserves_healing_order(self):
        outcomes = []
        for jump in (False, True):
            with self.subTest(jump=jump):
                run, ana, soldier = self.sleep_scene()
                self.start_field(run, soldier)
                fight = run.match.combat
                yaw, pitch = aim(fight._eye(ana), tuple(soldier.mover.position))
                run.step({ana: (yaw, pitch, 16)})
                for _ in range(20):
                    if any(rocket.last_tick is not None and rocket.blast is not None
                           and rocket.blast.effect == combat.ANA_GRENADE_EFFECT for rocket in fight.rockets):
                        break
                    run.step({ana: (yaw, pitch, 0)})
                else:
                    self.fail("Actual Ana grenade did not enter flight")
                self.assertNotIn(soldier.body, fight.heal_modifiers)
                start = run.frame
                if jump:
                    run.frame += 20
                    run.match.update(run.frame * combat.TICK, run.frame)
                else:
                    for _ in range(20):
                        run.step({})
                modifier = fight.heal_modifiers[soldier.body]
                self.assertGreater(modifier.anti_from, start + 1)
                self.assertLess(modifier.anti_from, run.frame)
                self.assertEqual(self.health(run, soldier), 140)
                self.assertEqual(soldier.stats.value(stats.HEALING), 0)
                outcomes.append((modifier.anti_from, modifier.anti_until))
        if len(outcomes) == 2:
            self.assertEqual(*outcomes)

    def test_ana_grenade_splash_respects_world_occlusion_and_range(self):
        for case in ("clear", "wall", "range"):
            with self.subTest(case=case):
                run, ana, soldier = self.sleep_scene()
                floor = soldier.mover.position[1]
                position = [8 if case == "range" else 2, floor, 2]
                soldier.mover.position = position.copy()
                soldier.mover.body = None
                if case == "wall":
                    wall = [
                        (1, floor, -5, 1, floor, 5, 1, floor + 4, 5),
                        (1, floor, -5, 1, floor + 4, 5, 1, floor + 4, -5),
                    ]
                    ground = [
                        (-50, floor, -50, -50, floor, 50, 50, floor, 50),
                        (-50, floor, -50, 50, floor, 50, 50, floor, -50),
                    ]
                    run.match.collision_world = World(ground + wall + [
                        (*triangle[:3], *triangle[6:], *triangle[3:6]) for triangle in wall
                    ])
                    for player in run.players:
                        player.mover.use(world=run.match.collision_world)
                run.step({})
                fight = run.match.combat
                angles = aim(fight._eye(ana), (0, floor, 2))
                run.step({ana: (*angles, 16)})
                flew = False
                for _ in range(35):
                    run.step({ana: (*angles, 0)})
                    if any(rocket.blast is not None and rocket.blast.effect == combat.ANA_GRENADE_EFFECT
                           for rocket in fight.rockets):
                        flew = True
                    elif flew:
                        break
                else:
                    self.fail("Actual grenade did not complete flight")
                self.assertEqual(soldier.mover.position, position)
                self.assertEqual(self.health(run, soldier), 140 if case == "clear" else 200)
                self.assertEqual(soldier.body in fight.heal_modifiers, case == "clear")

    def test_projected_barrier_blocks_grenade_antiheal_even_when_it_breaks(self):
        for breaking in (False, True):
            with self.subTest(breaking=breaking):
                run, ana, soldier, zarya, rifle = self.projected_scene(with_rifle=True)
                self.assertTrue(run.match.switch_hero(ana, heroes()[0x02E000000000013B]))
                x, floor, z = soldier.mover.position
                ana.mover.position = [0, floor, 0]
                fight = run.match.combat
                rifle_angles = aim(fight._eye(rifle), (x, floor + 1, z))
                for _ in range(45):
                    run.step({rifle: (*rifle_angles, 1)})
                    if self.health(run, soldier) <= 120:
                        break
                self.assertEqual(self.health(run, soldier), 120)
                self.start_field(run, soldier)
                self.project(run, soldier, zarya)
                shields = fight.zarya_barriers.projected
                if breaking:
                    for _ in range(100):
                        run.step({rifle: (*rifle_angles, 1)})
                        if shields[zarya.body][1] == 20:
                            break
                    self.assertEqual(shields[zarya.body][1], 20)
                before = self.health(run, soldier)
                barrier_hp = shields[zarya.body][1]
                start = run.frame
                angles = aim(fight._eye(ana), (x, floor + 1, z))
                run.step({ana: (*angles, 16)})
                for _ in range(35):
                    run.step({ana: (*angles, 0)})
                    if zarya.body not in shields or shields[zarya.body][1] < barrier_hp:
                        break
                if breaking:
                    self.assertNotIn(zarya.body, shields)
                else:
                    self.assertEqual(shields[zarya.body][1], 150)
                self.assertNotIn(soldier.body, fight.heal_modifiers)
                expected = before + (run.frame - start) * 35 * combat.TICK
                self.assertAlmostEqual(self.health(run, soldier), expected)
                self.assertTrue(self.fields(run, soldier))

    def test_friendly_ana_grenade_heals_and_boosts_soldier_field(self):
        run, ana, soldier = self.sleep_scene()
        ana.team = soldier.team
        enemy = run.match.add_player(3, "Rifle", soldier.hero.guid, 1 - soldier.team, False)
        in_world(run.match, enemy)
        run.players.append(enemy)
        self.assertTrue(run.match.switch_hero(enemy, soldier.hero))
        floor = soldier.mover.position[1]
        enemy.mover.position = [-3, floor, 1]
        run.step({})
        yaw, pitch = aim(run.match.combat._eye(enemy), (0, floor + .8, 3))
        for _ in range(75):
            run.step({enemy: (yaw, pitch, 1)})
            if self.health(run, soldier) <= 60:
                break
        self.assertEqual(self.health(run, soldier), 60)
        self.start_field(run, soldier)
        modifier = self.grenade_at_feet(run, ana, soldier)
        self.assertEqual(ana.stats.value(stats.HEALING), 100)
        self.assertGreater(self.health(run, soldier), 150)
        while run.frame < modifier.boost_from - 1:
            run.step({})
        before = self.health(run, soldier)
        run.step({})
        self.assertAlmostEqual(self.health(run, soldier) - before, 35 * combat.TICK * 1.5)

    def dart(self, run, ana, soldier, *, target_buttons=0):
        self.step(run, ana, soldier, cast=8, target_buttons=target_buttons)
        for _ in range(50):
            if soldier.body in run.match.combat.sleeps:
                return
            self.step(run, ana, soldier)
        self.fail("Actual Ana dart did not apply sleep")

    def test_sleep_dart_phase_contact_and_soldier_field_recovery(self):
        for hero_guid, button in (
            (defenses.REAPER, 0),
            (defenses.REAPER, 8),
            (defenses.MEI, 8),
            (defenses.ZENYATTA, 0x20),
        ):
            with self.subTest(hero=hero_guid, button=button):
                run, ana, front, soldier = self.phase_line(hero_guid, 3)
                fight = run.match.combat
                before = self.health(run, front)
                self.enter_front_phase(run, front, button)
                x, y, z = soldier.mover.position
                angles = aim(fight._eye(ana), (x, y + .8, z))
                run.step({ana: (*angles, 8)})
                for _ in range(40):
                    dart = next((rocket for rocket in fight.rockets
                                 if rocket.direct and rocket.direct.effect == combat.SLEEP_EFFECT), None)
                    if dart is not None:
                        break
                    run.step({})
                self.assertIsNotNone(dart)
                self.assertFalse(fight.sleeps)
                field_script = next(i for i in soldier.body_script.component.instances.values()
                                    if i.graph.index == 0x0257)
                ability = field_script.state(field_script.graph.node(2).state)
                windup = field_script.state(field_script.graph.node(8).state)
                run.step({soldier: (0, 0, 16)})
                self.assertTrue(windup.active)
                self.assertIn(dart, fight.rockets)
                for _ in range(20):
                    if dart not in fight.rockets:
                        break
                    run.step({})
                self.assertNotIn(dart, fight.rockets)
                self.assertEqual(defenses.invulnerable(front), bool(button))

                passes = bool(button) and hero_guid == defenses.REAPER
                expected = {soldier.body} if passes else ({front.body} if not button else set())
                self.assertEqual(set(fight.sleeps), expected)
                self.assertEqual(self.health(run, front), before - (0 if button else 5))
                self.assertEqual(self.health(run, soldier), 195 if passes else 200)
                for _ in range(40):
                    run.step({})
                self.assertFalse(windup.active)
                if passes:
                    self.assertFalse(self.fields(run, soldier))
                    self.assertEqual(ability.remaining(), 0)
                    run.frame = fight.sleeps[soldier.body].end - 1
                    run.match.update(run.frame * combat.TICK, run.frame)
                    run.step({})
                    self.assertFalse(fight.is_controlled(soldier))
                    self.start_field(run, soldier)
                    run.step({})
                    self.assertAlmostEqual(self.health(run, soldier), 195 + 35 * combat.TICK)
                    self.assertGreater(ability.remaining(), 0)
                else:
                    field, = self.fields(run, soldier)
                    self.assertIsNotNone(field.field_tick)

    def test_actual_sleep_dart_gates_new_reload_and_preserves_admitted_reload(self):
        for admitted in (False, True):
            with self.subTest(admitted=admitted):
                run, ana, soldier = self.sleep_scene()
                fight = run.match.combat
                rifle = soldier.body_script.component.instances[11]
                ammo = rifle.vars[53]
                run.step({soldier: (16384, 0, 1)})
                self.assertEqual(ammo.value(), 29)
                for _ in range(40):
                    run.step({})
                self.dart(run, ana, soldier, target_buttons=0x400 if admitted else 0)
                self.assertEqual(bool(rifle.vars[51].value()), admitted)
                for _ in range(100):
                    self.step(run, ana, soldier, target_buttons=0x400 | 1)
                self.assertTrue(fight.is_controlled(soldier))
                self.assertEqual(ammo.value(), 30 if admitted else 29)
                self.assertEqual(self.health(run, ana), 200)
                deadline = fight.sleeps[soldier.body].end
                run.frame = deadline - 1
                run.match.update(run.frame * combat.TICK, run.frame)
                run.step({})
                self.assertFalse(fight.is_controlled(soldier))
                self.assertFalse(rifle.vars[51].value())
                if not admitted:
                    run.step({soldier: (0, 0, 0x400)})
                    self.assertTrue(rifle.vars[51].value())
                    for _ in range(100):
                        run.step({})
                self.assertEqual(ammo.value(), 30)
                self.step(run, ana, soldier, target_buttons=1)
                self.assertEqual(ammo.value(), 29)
                self.assertLess(self.health(run, ana), 200)

    def test_actual_sleep_dart_cancels_field_suppresses_input_and_expires(self):
        run, ana, soldier = self.sleep_scene()
        fight = run.match.combat
        self.dart(run, ana, soldier, target_buttons=16)
        self.assertEqual(self.health(run, soldier), 195)
        sleep = fight.sleeps[soldier.body]
        self.assertEqual(sleep.end - sleep.start, math.ceil(5 / combat.TICK))
        self.assertNotIn(soldier.body, fight.stuns)
        ammo = soldier.body_script.component.instances[11].vars[53].value()
        position = tuple(soldier.mover.position)
        for _ in range(55):
            self.step(run, ana, soldier, target_buttons=1 | 8, forward=127)
        self.assertEqual(tuple(soldier.mover.position), position)
        self.assertEqual(soldier.body_script.component.instances[11].vars[53].value(), ammo)
        self.assertFalse(self.fields(run, soldier))
        run.frame = sleep.end - 1
        run.match.update(run.frame * combat.TICK, run.frame)
        self.assertTrue(fight.is_controlled(soldier))
        before = self.health(run, ana)
        self.step(run, ana, soldier, target_buttons=1)
        self.assertNotIn(soldier.body, fight.sleeps)
        self.assertLess(self.health(run, ana), before)

    def test_sleep_cancels_admitted_field_during_missing_owner_commands(self):
        for jump in (False, True):
            with self.subTest(jump=jump):
                run, ana, soldier = self.sleep_scene()
                fight = run.match.combat
                instances = soldier.body_script.component.instances.values()
                field = next(i for i in instances if i.graph.index == 0x0257)
                ability = field.state(field.graph.node(2).state)
                windup = field.state(field.graph.node(8).state)
                x, y, z = soldier.mover.position
                angles = aim(fight._eye(ana), (x, y + .8, z))
                run.step({ana: (*angles, 8)})
                for _ in range(40):
                    if fight.rockets:
                        break
                    run.step({ana: (*angles, 0)})
                self.assertTrue(fight.rockets)
                run.step({soldier: (0, 0, 16)})
                self.assertTrue(windup.active)
                self.assertTrue(fight.rockets)
                self.assertFalse(self.fields(run, soldier))
                last_pose = soldier.mover.last_command

                first = run.frame + 1
                run.match.update(first * combat.TICK, first)
                end = first + 40
                for tick in ((end,) if jump else range(first + 1, end + 1)):
                    run.match.update(tick * combat.TICK, tick)
                self.assertIs(soldier.mover.last_command, last_pose)
                self.assertEqual(self.health(run, soldier), 195)
                self.assertTrue(fight.is_controlled(soldier))
                self.assertFalse(windup.active)
                self.assertEqual(ability.remaining(), 0)
                self.assertFalse(self.fields(run, soldier))

                run.frame = fight.sleeps[soldier.body].end
                run.match.update(run.frame * combat.TICK, run.frame)
                self.assertFalse(fight.is_controlled(soldier))
                self.assertFalse(self.fields(run, soldier))
                self.start_field(run, soldier)
                self.assertEqual(len(self.fields(run, soldier)), 1)
                self.assertGreater(ability.remaining(), 0)

    def test_first_sleep_dart_flight_tick_precedes_field_release_in_a_gap(self):
        impact_tick = None
        for jump in (False, True):
            with self.subTest(jump=jump):
                run, ana, soldier = self.sleep_scene()
                fight = run.match.combat
                x, y, z = soldier.mover.position
                yaw, pitch = aim(fight._eye(ana), (x, y + .8, z))
                run.step({ana: (yaw, pitch, 8)})
                for _ in range(40):
                    if fight.rockets:
                        break
                    run.frame += 1
                    run.match.update(run.frame * combat.TICK, run.frame)
                    ana.take_commands([Command(run.frame, yaw=yaw, pitch=pitch)])
                dart, = fight.rockets
                self.assertEqual(dart.flown, 0)
                soldier.take_commands([Command(run.frame, buttons=16)])
                self.assertTrue(defenses.soldier_field_windup(soldier))
                self.assertFalse(self.fields(run, soldier))

                end = run.frame + 40
                for tick in ((end,) if jump else range(run.frame + 1, end + 1)):
                    run.match.update(tick * combat.TICK, tick)
                self.assertEqual(self.health(run, soldier), 195)
                self.assertTrue(fight.is_controlled(soldier))
                if impact_tick is None:
                    impact_tick = fight.sleeps[soldier.body].start
                self.assertEqual(fight.sleeps[soldier.body].start, impact_tick)
                self.assertFalse(self.fields(run, soldier))

    def test_admitted_sleep_dart_hits_on_time_without_ana_input(self):
        baseline = None
        for mode in ("posed", "idle", "jump"):
            with self.subTest(mode=mode):
                run, ana, soldier = self.sleep_scene()
                fight = run.match.combat
                x, y, z = soldier.mover.position
                angles = aim(fight._eye(ana), (x, y + .8, z))
                run.step({ana: (*angles, 8), soldier: (0, 0, 16)})
                last_pose = ana.mover.last_command
                self.assertTrue(defenses.soldier_field_windup(soldier))
                self.assertFalse(fight.rockets)
                end = run.frame + 90
                for tick in ((end,) if mode == "jump" else range(run.frame + 1, end + 1)):
                    run.match.update(tick * combat.TICK, tick)
                    if mode == "posed":
                        ana.take_commands([Command(tick, yaw=angles[0], pitch=angles[1])])
                if mode != "posed":
                    self.assertIs(ana.mover.last_command, last_pose)
                self.assertEqual(self.health(run, soldier), 195)
                self.assertFalse(fight.rockets)
                self.assertFalse(defenses.soldier_field_windup(soldier))
                self.assertFalse(self.fields(run, soldier))
                sleep = fight.sleeps[soldier.body]
                contact = (sleep.start, sleep.end)
                if mode == "posed":
                    baseline = contact
                else:
                    self.assertEqual(contact, baseline)

    def test_soldier_rifle_kills_ana_before_or_after_sleep_dart_release(self):
        for launched in (False, True):
            with self.subTest(launched=launched):
                run, ana, soldier = self.sleep_scene()
                fight = run.match.combat
                target = next(target for target in fight._targets() if target.owner is ana)
                pool = fight.vitals_of(target)
                pool.damage(pool.total - 20)
                source = ana.body
                x, y, z = soldier.mover.position
                dart_angles = aim(fight._eye(ana), (x, y + .8, z))
                run.step({ana: (*dart_angles, 8)})
                windup = defenses.ana_sleep_dart_windup(ana)
                self.assertIsNotNone(windup)
                self.assertFalse(fight.rockets)
                for _ in range(40 if launched else 1):
                    run.frame += 1
                    run.match.update(run.frame * combat.TICK, run.frame)
                    if fight.rockets:
                        break
                dart = None
                if launched:
                    dart, = fight.rockets
                    self.assertEqual(dart.direct.effect, combat.SLEEP_EFFECT)
                    self.assertEqual(dart.source_entity, source)
                else:
                    self.assertTrue(windup.active)
                    self.assertFalse(fight.rockets)
                x, y, z = ana.mover.position
                yaw, pitch = aim(fight._eye(soldier), (x, y + .8, z))
                soldier.take_commands([Command(run.frame, yaw=yaw, pitch=pitch, buttons=1)])
                self.assertTrue(fight.is_dead(ana))
                self.assertEqual(pool.killer, soldier.body)
                self.assertEqual(self.health(run, soldier), 200)
                if launched:
                    self.assertIn(dart, fight.rockets)
                    self.assertEqual(dart.source_entity, source)
                self.assertFalse(windup.active)
                end = run.frame + 40
                run.match.update(end * combat.TICK, end)
                self.assertFalse(fight.rockets)
                self.assertEqual(self.health(run, soldier), 195 if launched else 200)
                self.assertEqual(soldier.body in fight.sleeps, launched)

    def test_sleep_dart_misses_cover_pose_team_and_lifetime_then_recovers(self):
        for gate in ("cover", "pose", "team", "lifetime"):
            with self.subTest(gate=gate):
                run, ana, soldier = self.sleep_scene()
                fight = run.match.combat
                x, floor, z = soldier.mover.position
                angles = aim(fight._eye(ana), (x, floor + .8, z))
                cast_angles = (0, 0) if gate == "lifetime" else angles
                run.step({ana: (*cast_angles, 8)})
                for _ in range(40):
                    if fight.rockets:
                        break
                    run.frame += 1
                    run.match.update(run.frame * combat.TICK, run.frame)
                dart, = fight.rockets
                self.assertEqual(dart.direct.effect, combat.SLEEP_EFFECT)
                self.assertEqual(self.health(run, soldier), 200)
                start_z, start_distance = dart.position[2], dart.distance
                world, team = run.match.collision_world, soldier.team
                if gate == "cover":
                    wall = [
                        (-4, floor, 2, 4, floor, 2, 4, floor + 4, 2),
                        (-4, floor, 2, 4, floor + 4, 2, -4, floor + 4, 2),
                    ]
                    run.match.collision_world = World(wall + [
                        (*triangle[:3], *triangle[6:], *triangle[3:6]) for triangle in wall
                    ])
                elif gate == "pose":
                    soldier.mover.position = [x + 3, floor, z]
                elif gate == "team":
                    soldier.team = ana.team
                else:
                    soldier.mover.position = [x, floor, dart.position[2] + dart.speed * dart.left + 3]
                soldier.mover.body = None
                run.frame += math.ceil(dart.left / combat.TICK) + 2
                run.match.update(run.frame * combat.TICK, run.frame)
                self.assertNotIn(dart, fight.rockets)
                self.assertEqual(self.health(run, soldier), 200)
                self.assertNotIn(soldier.body, fight.sleeps)
                if gate == "cover":
                    contact_z = start_z + (dart.distance - start_distance) * dart.direction[2]
                    self.assertAlmostEqual(contact_z, 2)
                if gate == "lifetime":
                    self.assertAlmostEqual(dart.flown, 5)
                    self.assertAlmostEqual(dart.distance, dart.speed * dart.flown)

                run.match.collision_world = world
                soldier.team = team
                soldier.mover.position = [x, floor, z]
                soldier.mover.body = None
                weapon = next(i for i in ana.body_script.component.instances.values()
                              if i.graph.index == 0x0BDD)
                ability = weapon.states[weapon.graph.node(6).state]
                run.step({ana: (*angles, 8)})
                self.assertGreater(ability.remaining(), 0)
                self.assertIsNone(defenses.ana_sleep_dart_windup(ana))
                run.step({ana: (*angles, 0)})
                run.frame += math.ceil(ability.remaining() / combat.TICK) + 2
                run.match.update(run.frame * combat.TICK, run.frame)
                self.assertEqual(self.health(run, soldier), 200)
                self.assertNotIn(soldier.body, fight.sleeps)
                self.dart(run, ana, soldier)
                self.assertEqual(self.health(run, soldier), 195)

    def test_actual_rifle_wakes_sleeping_soldier_after_grace_without_extending_wake(self):
        run, ana, soldier = self.sleep_scene()
        attacker = run.match.add_player(3, "Rifle", soldier.hero.guid, ana.team, False)
        in_world(run.match, attacker)
        run.players.append(attacker)
        self.assertTrue(run.match.switch_hero(attacker, soldier.hero))
        attacker.mover.position = [-3, soldier.mover.position[1], 1]
        self.dart(run, ana, soldier)
        fight = run.match.combat
        sleep = fight.sleeps[soldier.body]
        original = sleep.end
        x, y, z = soldier.mover.position
        yaw, pitch = aim(fight._eye(attacker), (x, y + .8, z))
        run.step({attacker: (yaw, pitch, 1)})
        self.assertEqual(self.health(run, soldier), 175)
        self.assertEqual(sleep.end, original)
        self.assertFalse(sleep.waking)
        while run.frame < sleep.start + 32:
            run.step({})
        run.step({attacker: (yaw, pitch, 1)})
        self.assertEqual(self.health(run, soldier), 155)
        self.assertTrue(sleep.waking)
        deadline = sleep.end
        self.assertEqual(deadline - run.frame, math.ceil(.5 / combat.TICK))
        for _ in range(12):
            run.step({attacker: (yaw, pitch, 1)})
        self.assertLess(self.health(run, soldier), 155)
        self.assertEqual(sleep.end, deadline)
        run.frame = deadline
        run.match.update(run.frame * combat.TICK, run.frame)
        self.assertFalse(fight.is_controlled(soldier))

    def test_actual_sleep_dart_interrupts_visor_startup_and_fresh_charge_rearms_it(self):
        for startup_frames in (0, 45):
            with self.subTest(startup_frames=startup_frames):
                run, ana, soldier = self.sleep_scene()
                fight = run.match.combat
                script = soldier.body_script
                component = script.component
                visor = next(i for i in component.instances.values() if i.graph.index == 0x0259)
                cost = component.entity_var(338).value()
                charge = component.entity_var(342)
                script.set_ult_charge(cost)
                self.step(run, ana, soldier, target_buttons=0x20)
                windup, countdown = visor.states[visor.graph.node(91).state], visor.state(6)
                self.assertTrue(windup.active)
                self.assertEqual(charge.value(), 0)
                for _ in range(startup_frames):
                    self.step(run, ana, soldier)
                self.assertTrue(windup.active)
                self.dart(run, ana, soldier)
                self.assertEqual(self.health(run, soldier), 195)
                self.assertTrue(fight.is_controlled(soldier))
                self.assertFalse(windup.active)
                self.assertEqual(countdown.counter, 0)
                self.assertEqual(charge.value(), 0)

                for _ in range(85):
                    self.step(run, ana, soldier, target_buttons=0x21)
                self.assertTrue(fight.is_controlled(soldier))
                self.assertFalse(windup.active)
                self.assertEqual(countdown.counter, 0)
                self.assertEqual(self.health(run, ana), 200)
                sleep = fight.sleeps[soldier.body]
                run.frame = sleep.end - 1
                run.match.update(run.frame * combat.TICK, run.frame)
                self.assertTrue(fight.is_controlled(soldier))
                self.step(run, ana, soldier, target_buttons=0x20)
                self.assertFalse(fight.is_controlled(soldier))
                self.step(run, ana, soldier)
                self.step(run, ana, soldier, target_buttons=0x20)
                self.assertFalse(windup.active)
                self.assertEqual(charge.value(), 0)

                x, y, z = ana.mover.position
                yaw, pitch = aim(fight._eye(soldier), (x, y + 0.8, z))
                off_axis = ((yaw + 4000 + 32768) % 65536 - 32768, pitch)
                run.step({soldier: (*off_axis, 1)})
                self.assertEqual(self.health(run, ana), 200)
                script.set_ult_charge(cost - 20)  # Only the last body hit earns the threshold points.
                for _ in range(10):
                    run.step({soldier: (yaw, pitch, 0)})
                run.step({soldier: (yaw, pitch, 1)})
                self.assertEqual(self.health(run, ana), 180)
                self.assertEqual(charge.value(), cost)
                run.step({soldier: (*off_axis, 0x20)})
                self.assertTrue(windup.active)
                for _ in range(80):
                    run.step({soldier: (*off_axis, 0)})
                self.assertTrue(countdown.active)
                self.assertEqual(countdown.counter, 1)
                run.step({soldier: (*off_axis, 1)})
                self.assertEqual(self.health(run, ana), 160)
                self.assertEqual(charge.value(), 0)

    def test_sleep_rejects_fully_charged_visor_until_fresh_input_after_wake(self):
        run, ana, soldier = self.sleep_scene()
        fight = run.match.combat
        script = soldier.body_script
        component = script.component
        visor = next(i for i in component.instances.values() if i.graph.index == 0x0259)
        windup, countdown = visor.state(visor.graph.node(91).state), visor.state(6)
        cost = component.entity_var(338).value()
        charge = component.entity_var(342)
        script.set_ult_charge(cost)
        self.dart(run, ana, soldier)
        sleep = fight.sleeps[soldier.body]
        for _ in range(85):
            self.step(run, ana, soldier, target_buttons=0x20)
        self.assertTrue(fight.is_controlled(soldier))
        self.assertFalse(windup.active)
        self.assertEqual(countdown.counter, 0)
        self.assertEqual(charge.value(), cost)
        self.step(run, ana, soldier)
        run.frame = sleep.end
        run.match.update(run.frame * combat.TICK, run.frame)
        self.assertFalse(fight.is_controlled(soldier))
        self.assertFalse(windup.active)
        self.assertEqual(charge.value(), cost)
        self.step(run, ana, soldier, target_buttons=0x20)
        self.assertTrue(windup.active)
        self.assertEqual(charge.value(), 0)

    def test_sleep_keeps_active_visor_clock_and_recipient_replacement_clears_control(self):
        run, ana, soldier = self.sleep_scene()
        script = soldier.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        run.step({soldier: (0, 0, 0x20)})
        for _ in range(80):
            run.step({})
        visor = next(state for instance in script.component.instances.values()
                     if instance.graph.index == 0x0259
                     for state in instance.states.values()
                     if isinstance(state, nodes.TrackTargets) and state.active)
        start = visor.start
        self.dart(run, ana, soldier)
        self.assertTrue(visor.active)
        self.assertEqual(visor.start, start)
        fight = run.match.combat
        self.assertTrue(run.match.switch_hero(ana, soldier.hero))
        self.assertTrue(fight.is_controlled(soldier))
        old = soldier.body
        self.assertTrue(run.match.switch_hero(soldier, soldier.hero))
        self.assertNotIn(old, fight.sleeps)
        self.assertFalse(fight.is_controlled(soldier))

    def test_breaking_sleep_dart_does_not_sleep_soldier_through_projection(self):
        run, ana, soldier, zarya, _ = self.projected_scene()
        self.assertTrue(run.match.switch_hero(ana, heroes()[0x02E000000000013B]))
        ana.mover.position = [0, soldier.mover.position[1], 0]
        run.step({})
        self.project(run, soldier, zarya)
        fight = run.match.combat
        shields = fight.zarya_barriers.projected
        stamp, _, recipient = shields[zarya.body]
        shields[zarya.body] = stamp, 4, recipient
        self.step(run, ana, soldier, cast=8)
        for _ in range(45):
            self.step(run, ana, soldier)
        self.assertNotIn(zarya.body, shields)
        self.assertEqual(self.health(run, soldier), 200)
        self.assertNotIn(soldier.body, fight.sleeps)
        self.assertFalse(ana.client.messages(combat.HIT))

    def orisa_scene(self):
        run, caster, soldier = self.scene()
        manager = run.match.combat.orisa_barriers
        if manager.geometry is None:
            self.skipTest("original Orisa collision descriptor is required")
        orisa = run.match.add_player(3, "Orisa", defenses.ORISA, soldier.team, False)
        in_world(run.match, orisa)
        run.players.append(orisa)
        self.assertTrue(run.match.switch_hero(orisa, heroes()[defenses.ORISA]))
        orisa.mover.position = [1.2, soldier.mover.position[1], 3]
        run.step({})
        run.step({orisa: (-32768, 8192, 16)})
        for _ in range(60):
            if manager.actors:
                break
            run.step({orisa: (-32768, 8192, 0)})
        shield, = manager.actors.values()
        self.assertEqual(shield.health, 600)
        return run, caster, soldier, shield

    def test_orisa_mesh_blocks_flashbang_even_when_the_throw_breaks_it(self):
        for breaking in (False, True):
            with self.subTest(breaking=breaking):
                run, caster, soldier, shield = self.orisa_scene()
                if breaking:
                    shield.health = 20
                self.step(run, caster, soldier, cast=16, target_buttons=16)
                for _ in range(85):
                    run.step({})
                self.assertEqual(shield.health, 0 if breaking else 575)
                self.assertEqual(shield.active(), not breaking)
                self.assertEqual(self.health(run, soldier), 200)
                self.assertFalse(caster.client.messages(combat.HIT))
                self.assertNotIn(soldier.body, run.match.combat.stuns)
                field, = self.fields(run, soldier)
                self.assertIsNotNone(field.field_tick)

    def test_flashbang_on_protected_side_of_orisa_mesh_interrupts_field(self):
        run, caster, soldier, shield = self.orisa_scene()
        caster.mover.position = [0, soldier.mover.position[1], 5.5]
        caster.mover.body = None
        run.step({})
        self.cast(run, caster, soldier, target_buttons=16)
        self.assertEqual(self.health(run, soldier), 175)
        self.assertEqual(run.match.combat.stuns[soldier.body] - run.frame, 50)
        self.assertTrue(shield.active())
        for _ in range(85):
            run.step({})
        self.assertFalse(self.fields(run, soldier))

    def test_ground_flashbang_spends_orisa_mesh_hp_without_crossing_control(self):
        run, caster, soldier, shield = self.orisa_scene()
        fight = run.match.combat
        floor = soldier.mover.position[1]
        exposed = run.match.add_player(4, "Outside Soldier", soldier.hero.guid, soldier.team, False)
        in_world(run.match, exposed)
        run.players.append(exposed)
        self.assertTrue(run.match.switch_hero(exposed, soldier.hero))
        exposed.mover.position = [-1, floor, shield.position[2] - 0.5]
        run.step({})
        angles = aim(fight._eye(caster), (0, floor, shield.position[2] - 0.5))
        manager = fight.orisa_barriers
        with patch.object(manager, "splash", wraps=manager.splash) as splash:
            run.step({caster: (*angles, 16)})
            for _ in range(30):
                run.step({})
                if splash.called:
                    break
        splash.assert_called_once()
        self.assertIsNone(splash.call_args.args[4])
        self.assertEqual(shield.health, 575)
        self.assertEqual(self.health(run, soldier), 200)
        self.assertNotIn(soldier.body, fight.stuns)
        self.assertEqual(self.health(run, exposed), 175)
        self.assertEqual(fight.stuns[exposed.body] - run.frame, 50)

    def test_flashbang_cancels_unreleased_orisa_throw_but_preserves_flight(self):
        for flash_offset, released in ((4, False), (6, False), (8, True)):
            for jump in (False, True):
                with self.subTest(flash_offset=flash_offset, jump=jump):
                    run, caster, soldier = self.scene()
                    fight = run.match.combat
                    service = fight.orisa_barriers
                    if service.geometry is None:
                        self.skipTest("original Orisa collision descriptor is required")
                    floor = soldier.mover.position[1]
                    soldier.mover.position = [6, floor, 3]
                    soldier.mover.body = None
                    caster.mover.position = [0, floor, 4.5]
                    caster.mover.body = None
                    orisa = run.match.add_player(3, "Orisa", defenses.ORISA, soldier.team, False)
                    in_world(run.match, orisa)
                    run.players.append(orisa)
                    self.assertTrue(run.match.switch_hero(orisa, heroes()[defenses.ORISA]))
                    orisa.mover.position = [0, floor, 3]
                    run.step({})
                    run.step({orisa: (-32768, 8192, 16)})
                    self.assertTrue(service.pending(orisa))
                    throw = run.frame + flash_offset
                    run.match.update(throw * combat.TICK, throw)
                    yaw, pitch = aim(fight._eye(caster), (0, floor + 1, 3))
                    caster.take_commands([Command(throw, yaw=yaw, pitch=pitch, buttons=16)])
                    run.match.update((throw + 1) * combat.TICK, throw + 1)
                    rocket, = fight.rockets
                    self.assertEqual(rocket.last_tick, throw + 1)
                    self.assertEqual(bool(service.flights), released)
                    self.assertFalse(service.actors)
                    before = self.health(run, orisa)
                    end = throw + 30
                    with patch.object(service, "land", wraps=service.land) as land:
                        for tick in ((end,) if jump else range(throw + 2, end + 1)):
                            run.match.update(tick * combat.TICK, tick)
                    self.assertEqual(self.health(run, orisa), before - 20)
                    impact = fight.stuns[orisa.body] - 50
                    self.assertFalse(service.pending(orisa))
                    self.assertFalse(service.flights)
                    if released:
                        land.assert_called_once()
                        self.assertGreater(land.call_args.args[2], impact)
                        self.assertEqual(service.actors[orisa.body].health, 600)
                    else:
                        land.assert_not_called()
                        self.assertFalse(service.actors)
                        ready = end + 650
                        run.match.update(ready * combat.TICK, ready)
                        self.assertFalse(service.actors)
                        orisa.take_commands([Command(ready, yaw=-32768, pitch=8192, buttons=16)])
                        self.assertTrue(service.pending(orisa))
                        run.match.update((ready + 60) * combat.TICK, ready + 60)
                        self.assertEqual(service.actors[orisa.body].health, 600)

    def winston_scene(self, *, inside=False):
        run, caster, soldier = self.scene()
        winston = run.match.add_player(3, "Winston", winston_barrier.HERO, soldier.team, False)
        in_world(run.match, winston)
        run.players.append(winston)
        self.assertTrue(run.match.switch_hero(winston, heroes()[winston_barrier.HERO]))
        floor = soldier.mover.position[1]
        winston.mover.position = [1, floor, 1.5] if inside else [2, floor, 6]
        run.step({})
        run.step({winston: (0, 0, 16)})
        manager = run.match.combat.winston_barriers
        for _ in range(45):
            if manager.actors:
                break
            run.step({})
        dome, = manager.actors.values()
        self.assertEqual(dome.health, 700)
        return run, caster, soldier, dome

    def test_winston_dome_blocks_flashbang_crossing_shell_even_on_break(self):
        for breaking in (False, True):
            with self.subTest(breaking=breaking):
                run, caster, soldier, dome = self.winston_scene()
                if breaking:
                    # Isolate the last 20 HP; actual full-pool exhaustion has a separate duel.
                    dome.health = 20
                self.step(run, caster, soldier, cast=16, target_buttons=16)
                for _ in range(85):
                    run.step({})
                self.assertEqual(dome.health, 0 if breaking else 675)
                self.assertEqual(dome.retired, breaking)
                self.assertEqual(self.health(run, soldier), 200)
                self.assertFalse(caster.client.messages(combat.HIT))
                self.assertNotIn(soldier.body, run.match.combat.stuns)
                field, = self.fields(run, soldier)
                self.assertIsNotNone(field.field_tick)

    def test_flashbang_wholly_inside_winston_dome_stuns_soldier(self):
        run, caster, soldier, dome = self.winston_scene(inside=True)
        self.cast(run, caster, soldier, target_buttons=16)
        self.assertEqual(self.health(run, soldier), 175)
        self.assertEqual(dome.health, 700)
        self.assertEqual(run.match.combat.stuns[soldier.body] - run.frame, 50)
        for _ in range(85):
            run.step({})
        self.assertFalse(self.fields(run, soldier))

    def test_field_windup_without_owner_input_respects_mesh_and_dome_flashbang_contacts(self):
        for barrier in ("orisa", "winston"):
            for contact in ("blocked", "break", "same_side"):
                opening = None
                for jump in (False, True):
                    with self.subTest(barrier=barrier, contact=contact, jump=jump):
                        same_side = contact == "same_side"
                        if barrier == "orisa":
                            run, caster, soldier, shield = self.orisa_scene()
                            if same_side:
                                caster.mover.position = [0, soldier.mover.position[1], 5.5]
                                caster.mover.body = None
                                run.step({})
                        else:
                            run, caster, soldier, shield = self.winston_scene(inside=same_side)
                        if contact == "break":
                            shield.health = 20
                        before_shield = shield.health
                        fight = run.match.combat
                        pool = lane.SoldierCombatLaneTests.vitals(self, run, soldier)
                        pool.damage(50)
                        self.step(run, caster, soldier, cast=16, target_buttons=16)
                        self.assertTrue(defenses.soldier_field_windup(soldier))
                        last_pose = soldier.mover.last_command
                        x, y, z = soldier.mover.position
                        angles = aim(fight._eye(caster), (x, y + 1, z))
                        for _ in range(35):
                            if fight.rockets:
                                break
                            run.frame += 1
                            run.match.update(run.frame * combat.TICK, run.frame)
                            caster.take_commands([Command(run.frame, yaw=angles[0], pitch=angles[1])])
                        self.assertTrue(fight.rockets)
                        first = run.frame + 1
                        run.match.update(first * combat.TICK, first)
                        end = first + 38
                        for tick in ((end,) if jump else range(first + 1, end + 1)):
                            run.match.update(tick * combat.TICK, tick)
                        run.frame = end
                        self.assertIs(soldier.mover.last_command, last_pose)
                        self.assertEqual(fight.is_stunned(soldier), same_side)
                        if same_side:
                            # Same-side Orisa splash overlaps the mesh. The interior Winston blast
                            # reaches Soldier without reaching the dome surface.
                            self.assertEqual(shield.health, before_shield - (25 if barrier == "orisa" else 0))
                            self.assertEqual(pool.total, 125)
                            self.assertFalse(self.fields(run, soldier))
                            run.frame = fight.stuns[soldier.body]
                            run.match.update(run.frame * combat.TICK, run.frame)
                            self.start_field(run, soldier)
                            self.assertEqual(len(self.fields(run, soldier)), 1)
                        else:
                            self.assertEqual(shield.health, max(0, before_shield - 25))
                            field, = self.fields(run, soldier)
                            self.assertIsNotNone(field.field_tick)
                            if opening is None:
                                opening = field.field_tick
                            self.assertEqual(field.field_tick, opening)
                            self.assertAlmostEqual(
                                pool.total, 150 + (end - field.field_tick) * 35 * combat.TICK,
                            )

    def test_ground_flashbang_outside_winston_dome_blocks_control_across_shell(self):
        run, caster, soldier, dome = self.winston_scene()
        fight = run.match.combat
        floor = soldier.mover.position[1]
        exposed = run.match.add_player(4, "Outside Soldier", soldier.hero.guid, soldier.team, False)
        in_world(run.match, exposed)
        run.players.append(exposed)
        self.assertTrue(run.match.switch_hero(exposed, soldier.hero))
        exposed.mover.position = [-1.5, floor, 2]
        run.step({})
        angles = aim(fight._eye(caster), (-0.8, floor, 1.55))
        manager = fight.winston_barriers
        with patch.object(manager, "splash", wraps=manager.splash) as splash:
            run.step({caster: (*angles, 16)})
            for _ in range(30):
                run.step({})
                if splash.called:
                    break
        splash.assert_called_once()
        self.assertIsNone(splash.call_args.args[4])
        self.assertEqual(dome.health, 675)
        self.assertEqual(self.health(run, soldier), 200)
        self.assertNotIn(soldier.body, fight.stuns)
        self.assertEqual(self.health(run, exposed), 175)
        self.assertEqual(fight.stuns[exposed.body] - run.frame, 50)

    def projected_scene(self, *, with_rifle=False):
        run, caster, soldier = self.scene()
        zarya = run.match.add_player(3, "Zarya", zarya_barrier.HERO, soldier.team, False)
        in_world(run.match, zarya)
        run.players.append(zarya)
        self.assertTrue(run.match.switch_hero(zarya, heroes()[zarya_barrier.HERO]))
        floor = soldier.mover.position[1]
        zarya.mover.position = [6, floor, 3]
        rifle = None
        if with_rifle:
            rifle = run.match.add_player(4, "Rifle", soldier.hero.guid, caster.team, False)
            in_world(run.match, rifle)
            run.players.append(rifle)
            self.assertTrue(run.match.switch_hero(rifle, soldier.hero))
            rifle.mover.position = [-4, floor, 3]
        run.step({})
        return run, caster, soldier, zarya, rifle

    def project(self, run, soldier, zarya):
        fight = run.match.combat
        x, y, z = soldier.mover.position
        angles = aim(fight._eye(zarya), (x, y + 1, z))
        run.step({zarya: (*angles, 16)})
        self.assertEqual(fight.zarya_barriers.projected[zarya.body][1:], (200, soldier.body))

    def test_projected_barrier_consumes_flashbang_damage_and_stun_even_on_break(self):
        for breaking in (False, True):
            with self.subTest(breaking=breaking):
                run, caster, soldier, zarya, rifle = self.projected_scene(with_rifle=breaking)
                self.project(run, soldier, zarya)
                fight = run.match.combat
                barriers = fight.zarya_barriers.projected
                if breaking:
                    x, y, z = soldier.mover.position
                    angles = aim(fight._eye(rifle), (x, y + 1, z))
                    for _ in range(100):
                        run.step({rifle: (*angles, 1)})
                        if barriers[zarya.body][1] == 20:
                            break
                    self.assertEqual(barriers[zarya.body][1], 20)
                self.step(run, caster, soldier, cast=16)
                for _ in range(30):
                    run.step({})
                self.assertFalse(fight.is_stunned(soldier))
                self.assertEqual(self.health(run, soldier), 200)
                self.assertFalse(caster.client.messages(combat.HIT))
                energy = zarya.body_script.component.entity_var(87).value()
                self.assertAlmostEqual(energy, 40 if breaking else 5)
                if breaking:
                    self.assertNotIn(zarya.body, barriers)
                else:
                    self.assertEqual(barriers[zarya.body][1], 175)
                before = self.health(run, caster)
                self.step(run, caster, soldier, target_buttons=1)
                self.assertLess(self.health(run, caster), before)

    def test_projecting_onto_stunned_soldier_preserves_original_deadline(self):
        run, caster, soldier, zarya, rifle = self.projected_scene(with_rifle=True)
        self.cast(run, caster, soldier)
        fight = run.match.combat
        deadline = fight.stuns[soldier.body]
        self.project(run, soldier, zarya)
        self.assertFalse(fight.is_stunned(zarya))
        x, y, z = soldier.mover.position
        angles = aim(fight._eye(rifle), (x, y + 1, z))
        run.step({rifle: (*angles, 1)})
        self.assertEqual(fight.zarya_barriers.projected[zarya.body][1], 180)
        self.assertEqual(self.health(run, soldier), 175)
        self.assertEqual(fight.stuns[soldier.body], deadline)
        while run.frame < deadline:
            run.step({})
        self.assertFalse(fight.is_stunned(soldier))
        self.assertEqual(fight.zarya_barriers.projected[zarya.body][1], 180)

    def test_ground_flashbang_is_absorbed_without_direct_sphere_contact(self):
        run, caster, soldier, zarya, _ = self.projected_scene()
        self.project(run, soldier, zarya)
        fight = run.match.combat
        floor = soldier.mover.position[1]
        angles = aim(fight._eye(caster), (2.2, floor, 2.6))
        bubbles = fight.zarya_barriers
        with patch.object(bubbles, "splash", wraps=bubbles.splash) as splash:
            run.step({caster: (*angles, 16)})
            for _ in range(30):
                run.step({})
                if splash.called:
                    break
        splash.assert_called_once()
        cover, impact_owner, _ = splash.call_args.args[4:]
        self.assertIn(soldier, cover)
        self.assertIsNone(impact_owner)
        self.assertEqual(bubbles.projected[zarya.body][1], 175)
        self.assertEqual(self.health(run, soldier), 200)
        self.assertFalse(fight.is_stunned(soldier))

    def test_projected_barrier_preserves_field_windup_through_flashbang(self):
        run, caster, soldier, zarya, _ = self.projected_scene()
        self.project(run, soldier, zarya)
        self.step(run, caster, soldier, cast=16, target_buttons=16)
        self.assertFalse(self.fields(run, soldier))
        for _ in range(85):
            run.step({})
        field, = self.fields(run, soldier)
        self.assertIsNotNone(field.field_tick)
        self.assertEqual(run.match.combat.zarya_barriers.projected[zarya.body][1], 175)
        self.assertEqual(self.health(run, soldier), 200)

    def test_flashbang_gates_new_reload_and_preserves_admitted_reload(self):
        for admitted in (False, True):
            with self.subTest(admitted=admitted):
                run, caster, soldier = self.scene()
                fight = run.match.combat
                caster_health = self.health(run, caster)
                rifle = soldier.body_script.component.instances[11]
                ammo = rifle.vars[53]
                run.step({soldier: (16384, 0, 1)})
                self.assertEqual(ammo.value(), 29)
                for _ in range(40):
                    run.step({})
                if admitted:
                    run.step({soldier: (0, 0, 0x400)})
                    self.assertTrue(rifle.vars[51].value())
                for _ in range(20):
                    run.step({})
                self.cast(run, caster, soldier)
                end = fight.stuns[soldier.body]
                self.assertEqual(bool(rifle.vars[51].value()), admitted)
                while run.frame < end - 1:
                    self.step(run, caster, soldier, target_buttons=0x400 | 1)
                self.assertEqual(ammo.value(), 30 if admitted else 29)
                self.assertEqual(self.health(run, caster), caster_health)
                run.step({})
                self.assertFalse(fight.is_controlled(soldier))
                if not admitted:
                    self.assertFalse(rifle.vars[51].value())
                    run.step({soldier: (0, 0, 0x400)})
                    self.assertTrue(rifle.vars[51].value())
                for _ in range(100):
                    run.step({})
                self.assertFalse(rifle.vars[51].value())
                self.assertEqual(ammo.value(), 30)
                self.step(run, caster, soldier, target_buttons=1)
                self.assertEqual(ammo.value(), 29)
                self.assertLess(self.health(run, caster), caster_health)

    def test_sprint_interrupts_rejects_held_inputs_then_firing_and_movement_recover(self):
        run, caster, soldier = self.scene()
        for _ in range(4):
            self.step(run, caster, soldier, target_buttons=8, forward=127)
        sprint = next(s for i in soldier.body_script.component.instances.values()
                      for s in i.states.values() if isinstance(s, nodes.Ability)
                      and s.buttons()[0] == 35)
        self.assertTrue(sprint.flags & nodes.Ability.RUNNING)
        self.cast(run, caster, soldier, target_buttons=8, forward=127)
        fight = run.match.combat
        self.assertFalse(sprint.flags & nodes.Ability.RUNNING)
        position, before = tuple(soldier.mover.position), self.health(run, caster)
        ammo = soldier.body_script.component.instances[11].vars[53].value()
        end = fight.stuns[soldier.body]
        while run.frame < end - 1:
            self.step(run, caster, soldier, target_buttons=1 | 8 | 16 | 32, forward=127)
            self.assertEqual(tuple(soldier.mover.position), position)
            self.assertEqual(self.health(run, caster), before)
            self.assertEqual(soldier.body_script.component.instances[11].vars[53].value(), ammo)
            self.assertFalse(soldier.body_script.component.held)
        for _ in range(15):
            self.step(run, caster, soldier, target_buttons=1, forward=127)
        self.assertNotEqual(tuple(soldier.mover.position), position)
        self.assertLess(self.health(run, caster), before)
        self.assertLess(soldier.body_script.component.instances[11].vars[53].value(), ammo)
        self.assertAlmostEqual(math.hypot(*soldier.mover.velocity[::2]), 5.5, delta=0.01)

    def test_actual_flashbang_and_sleep_dart_against_sprint_and_projected_barrier(self):
        for sleep, button, damage in ((False, 16, 25), (True, 8, 5)):
            for shield_hp in (0, 200, damage - 1):
                with self.subTest(sleep_dart=sleep, shield_hp=shield_hp):
                    run, caster, soldier, zarya, _ = self.projected_scene()
                    x, floor, _ = soldier.mover.position
                    if sleep:
                        self.assertTrue(run.match.switch_hero(caster, heroes()[0x02E000000000013B]))
                        caster.mover.position = [0, floor, 0]
                        caster.mover.body = None
                    soldier.mover.position = [x, floor, 6]
                    soldier.mover.body = None
                    run.step({})
                    fight = run.match.combat
                    shields = fight.zarya_barriers.projected
                    if shield_hp:
                        self.project(run, soldier, zarya)
                        if shield_hp < 200:
                            stamp, _, recipient = shields[zarya.body]
                            shields[zarya.body] = stamp, shield_hp, recipient
                    self.step(run, caster, soldier, target_buttons=8, forward=127)
                    for _ in range(10):
                        self.step(run, caster, soldier, forward=127)
                    component = soldier.body_script.component
                    sprint = next(
                        state for instance in component.instances.values() for state in instance.states.values()
                        if isinstance(state, nodes.Ability) and state.buttons()[0] == 35
                    )
                    self.assertTrue(sprint.flags & nodes.Ability.RUNNING)
                    self.assertAlmostEqual(math.hypot(*soldier.mover.velocity[::2]), 8.25, delta=.01)
                    self.step(run, caster, soldier, cast=button, forward=127)
                    for _ in range(45):
                        if shield_hp:
                            self.assertFalse(fight.is_controlled(soldier))
                            self.assertTrue(sprint.flags & nodes.Ability.RUNNING)
                            self.assertAlmostEqual(math.hypot(*soldier.mover.velocity[::2]), 8.25, delta=.01)
                            remaining = shields.get(zarya.body, (None, 0))[1]
                            if remaining < shield_hp:
                                break
                        elif fight.is_controlled(soldier):
                            break
                        self.step(run, caster, soldier, forward=127)
                    if shield_hp:
                        self.assertEqual(remaining, max(0, shield_hp - damage))
                        self.assertEqual(self.health(run, soldier), 200)
                        continue
                    self.assertEqual(self.health(run, soldier), 200 - damage)
                    self.assertFalse(sprint.flags & nodes.Ability.RUNNING)
                    deadline = fight.sleeps[soldier.body].end if sleep else fight.stuns[soldier.body]
                    position = tuple(soldier.mover.position)
                    ammo = component.instances[11].vars[53]
                    rounds = ammo.value()
                    while run.frame < deadline - 1:
                        self.step(run, caster, soldier, target_buttons=8 | 1, forward=127)
                        self.assertTrue(fight.is_controlled(soldier))
                        self.assertFalse(sprint.flags & nodes.Ability.RUNNING)
                        self.assertEqual(ammo.value(), rounds)
                        self.assertEqual(tuple(soldier.mover.position), position)
                    # Walk and sprint away from the caster so body collision cannot cap the speed.
                    for _ in range(15):
                        self.step(run, caster, soldier, forward=127, target_angles=(0, 0))
                    self.assertFalse(fight.is_controlled(soldier))
                    self.assertFalse(sprint.flags & nodes.Ability.RUNNING)
                    self.assertAlmostEqual(math.hypot(*soldier.mover.velocity[::2]), 5.5, delta=.01)
                    self.step(run, caster, soldier, target_buttons=8, forward=127, target_angles=(0, 0))
                    for _ in range(10):
                        self.step(run, caster, soldier, forward=127, target_angles=(0, 0))
                    self.assertTrue(sprint.flags & nodes.Ability.RUNNING)
                    self.assertAlmostEqual(math.hypot(*soldier.mover.velocity[::2]), 8.25, delta=.01)

    def test_interrupted_field_windup_recovers_and_only_a_launch_starts_cooldown(self):
        run, caster, soldier = self.scene()
        component = soldier.body_script.component
        field = next(i for i in component.instances.values() if i.graph.index == 0x0257)
        ability = field.state(field.graph.node(2).state)
        launch = field.state(field.graph.node(8).state)
        self.step(run, caster, soldier, cast=16, target_buttons=16)
        self.assertTrue(launch.active)
        self.assertEqual(ability.remaining(), 0)
        for _ in range(50):
            if run.match.combat.is_stunned(soldier):
                break
            self.step(run, caster, soldier)
        self.assertTrue(run.match.combat.is_stunned(soldier))
        self.assertFalse(launch.active)
        self.assertEqual(ability.remaining(), 0)
        for _ in range(120):
            self.step(run, caster, soldier)
        self.assertFalse(self.fields(run, soldier))

        self.step(run, caster, soldier, target_buttons=16)
        for _ in range(80):
            self.step(run, caster, soldier)
        first, = self.fields(run, soldier)
        self.assertIsNotNone(first.field_tick)
        self.assertGreater(ability.remaining(), 0)
        self.step(run, caster, soldier, target_buttons=16)
        self.assertFalse(launch.active)
        for _ in range(80):
            self.step(run, caster, soldier)
        self.assertEqual(self.fields(run, soldier), [first])
        for _ in range(1000):
            if ability.remaining() == 0:
                break
            self.step(run, caster, soldier)
        self.assertEqual(ability.remaining(), 0)
        self.step(run, caster, soldier, target_buttons=16)
        for _ in range(80):
            self.step(run, caster, soldier)
        second, = self.fields(run, soldier)
        self.assertIsNot(second, first)
        self.assertIsNotNone(second.field_tick)

    def test_already_deployed_field_keeps_healing_through_stun(self):
        run, caster, soldier = self.scene()
        self.step(run, caster, soldier, target_buttons=16)
        for _ in range(80):
            self.step(run, caster, soldier)
        self.assertTrue(self.fields(run, soldier))
        self.cast(run, caster, soldier)
        before = self.health(run, soldier)
        end = run.match.combat.stuns[soldier.body]
        while run.frame < end - 1:
            self.step(run, caster, soldier)
        self.assertTrue(self.fields(run, soldier))
        self.assertGreater(self.health(run, soldier), before)

    def test_active_visor_keeps_original_expiry_but_cannot_fire_during_stun(self):
        run, caster, soldier = self.scene()
        script = soldier.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        self.step(run, caster, soldier, target_buttons=32)
        for _ in range(80):
            self.step(run, caster, soldier)
        self.assertTrue(any(s.flags & nodes.Ability.RUNNING for s in script.ultimates))
        wait = next(i for i in script.component.instances.values()
                    if i.graph.index == 0x0259).states[6]  # authored #215 countdown from #567=6
        self.assertTrue(wait.active)
        deadline, counter = wait.start + 6000, wait.counter
        self.cast(run, caster, soldier)
        self.assertTrue(any(s.flags & nodes.Ability.RUNNING for s in script.ultimates))
        before = self.health(run, caster)
        end = run.match.combat.stuns[soldier.body]
        while run.frame < end - 1:
            self.step(run, caster, soldier, target_buttons=1 | 32)
            self.assertEqual(self.health(run, caster), before)
        self.assertEqual((wait.start + 6000, wait.counter), (deadline, counter))
        for _ in range(10):
            self.step(run, caster, soldier, target_buttons=1)
        self.assertLess(self.health(run, caster), before)
        self.assertTrue(wait.active)
        while script.component.now < deadline:
            self.step(run, caster, soldier)
        self.assertFalse(wait.active)
        self.assertFalse(any(s.flags & nodes.Ability.RUNNING for s in script.ultimates))
        self.assertEqual(script.component.entity_var(342).value(), 0)

    def test_actual_flashbang_cancels_visor_windup_and_only_new_charge_rearms_it(self):
        for startup_frames in (0, 67):
            with self.subTest(startup_frames=startup_frames):
                run, caster, soldier = self.scene()
                script = soldier.body_script
                component = script.component
                visor = next(i for i in component.instances.values() if i.graph.index == 0x0259)
                cost = component.entity_var(338).value()
                charge = component.entity_var(342)
                script.set_ult_charge(cost)
                self.step(run, caster, soldier, target_buttons=32)
                windup = visor.states[43]  # 0259/node91, authored 1.2-second wait
                countdown = visor.state(6)
                self.assertTrue(windup.active)
                self.assertEqual(charge.value(), 0)
                for _ in range(startup_frames):
                    self.step(run, caster, soldier)
                self.cast(run, caster, soldier)
                self.assertFalse(windup.active)
                self.assertEqual(countdown.counter, 0)
                self.assertEqual(charge.value(), 0)
                for _ in range(85):
                    self.step(run, caster, soldier, target_buttons=32)
                self.step(run, caster, soldier)
                self.step(run, caster, soldier, target_buttons=32)
                self.assertFalse(windup.active)
                self.assertEqual(countdown.counter, 0)
                self.assertEqual(charge.value(), 0)

                origin = run.match.combat._eye(soldier)
                x, y, z = caster.mover.position
                yaw, pitch = aim(origin, (x, y + .8, z))
                off_axis = ((yaw + 4000 + 32768) % 65536 - 32768, pitch)
                before = self.health(run, caster)
                run.step({soldier: (*off_axis, 1)})
                self.assertEqual(self.health(run, caster), before)
                script.set_ult_charge(cost - 20)  # one actual body hit supplies the remainder
                for _ in range(10):
                    run.step({soldier: (yaw, pitch, 0)})
                run.step({soldier: (yaw, pitch, 1)})
                self.assertEqual(self.health(run, caster), before - 20)
                self.assertEqual(charge.value(), cost)
                run.step({soldier: (*off_axis, 32)})
                self.assertTrue(windup.active)
                self.assertEqual(charge.value(), 0)
                for _ in range(80):
                    run.step({soldier: (*off_axis, 0)})
                self.assertTrue(countdown.active)
                run.step({soldier: (*off_axis, 1)})
                self.assertEqual(self.health(run, caster), before - 40)
                self.assertEqual(charge.value(), 0)

    def test_actual_lethal_flashbang_ends_visor_before_and_after_startup(self):
        for active in (False, True):
            with self.subTest(active=active):
                run, caster, soldier = self.scene()
                fight = run.match.combat
                old_body, script = soldier.body, soldier.body_script
                visor = next(i for i in script.component.instances.values() if i.graph.index == 0x0259)
                target = next(t for t in fight._targets() if t.owner is soldier)
                pool = fight.vitals_of(target)
                pool.damage(180)  # setup only; actual Flashbang deals the lethal damage
                script.set_ult_charge(script.component.entity_var(338).value())
                self.step(run, caster, soldier, target_buttons=32)
                if active:
                    for _ in range(80):
                        self.step(run, caster, soldier)
                windup, countdown = visor.states[43], visor.state(6)
                self.assertEqual((windup.active, countdown.active), (not active, active))
                self.step(run, caster, soldier, cast=16)
                for _ in range(30):
                    if fight.is_dead(soldier):
                        break
                    self.step(run, caster, soldier)
                self.assertTrue(fight.is_dead(soldier))
                self.assertEqual(pool.killer, caster.body)
                self.assertFalse(windup.active or countdown.active)
                before = self.health(run, caster)
                for _ in range(100):
                    self.step(run, caster, soldier, target_buttons=1 | 32)
                self.assertEqual(self.health(run, caster), before)
                self.assertFalse(windup.active or countdown.active)
                self.assertEqual(countdown.counter, int(active))
                for _ in range(round(combat.respawn_seconds(run.match) / combat.TICK)):
                    self.step(run, caster, soldier)
                    if soldier.body != old_body:
                        break
                self.assertNotEqual(soldier.body, old_body)
                self.assertTrue(script.retired)
                self.assertFalse(fight.is_dead(soldier))
                self.assertEqual(soldier.body_script.component.entity_var(342).value(), 0)

    def test_helix_already_in_flight_keeps_damage_and_credit_after_owner_is_stunned(self):
        run, caster, soldier = self.scene()
        x, y, z = soldier.mover.position
        bot = self.add_bot(run, caster.team, (x, y, z + 30))
        run.frame += 1
        match = run.match
        match.update(run.frame * combat.TICK, run.frame)
        for player in run.players:
            player.client.queue_entities(match.remote_movements(player))
        angles = aim(match.combat._eye(caster), (x, y + 1, z))
        caster.take_commands([Command(run.frame, yaw=angles[0], pitch=angles[1], buttons=16)])
        angles = aim(match.combat._eye(soldier), (x, y + 1, z + 30))
        soldier.take_commands([Command(run.frame, yaw=angles[0], pitch=angles[1], buttons=2)])
        helix, = [r for r in match.combat.rockets if r.shooter is soldier]
        for _ in range(40):
            if match.combat.is_stunned(soldier):
                break
            self.step(run, caster, soldier)
        self.assertTrue(match.combat.is_stunned(soldier))
        self.assertIn(helix, match.combat.rockets)
        while helix in match.combat.rockets:
            self.step(run, caster, soldier)
        self.assertTrue(match.combat.is_stunned(soldier))
        self.assertEqual(self.health(run, bot), 80)
        self.assertAlmostEqual(soldier.body_script.component.entity_var(342).value(), 120)

    def test_shared_control_does_not_cancel_live_fortify_or_transcendence(self):
        for hero in (defenses.ORISA, defenses.ZENYATTA):
            with self.subTest(hero=hero):
                run, caster, other = self.scene()
                position = list(other.mover.position)
                run.match.switch_hero(other, heroes()[hero])
                other.mover.position[:] = position
                other.body_script.ult_seconds = 0
                for _ in range(3):
                    self.step(run, caster, other)
                if hero == defenses.ZENYATTA:
                    script = other.body_script
                    script.set_ult_charge(script.component.entity_var(338).value())
                self.step(run, caster, other, target_buttons=8 if hero == defenses.ORISA else 32)
                before = self.health(run, other)
                self.step(run, caster, other, cast=16)
                for _ in range(60):
                    self.step(run, caster, other)
                    self.assertFalse(run.match.combat.is_stunned(other))
                expected = 10 if hero == defenses.ORISA else 0
                self.assertAlmostEqual(before - self.health(run, other), expected, places=4)
                if hero == defenses.ORISA:
                    self.assertAlmostEqual(defenses.fortify_scale(other), .6)
                else:
                    self.assertTrue(defenses.invulnerable(other))
                for _ in range(700):
                    self.step(run, caster, other)
                self.cast(run, caster, other)
                self.assertTrue(run.match.combat.is_stunned(other))
