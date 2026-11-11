"""Actual Zarya Graviton Surge commands against Reinhardt."""

import math
import unittest

import test_game_reinhardt as fixtures
import test_reinhardt_zarya_weapons as zarya_fixture
from test_game_combat import aim

from ow174.game import (
    combat,
    defenses,
    deflection,
    graviton,
    hook,
    npc,
    npc_control,
    sigma_grasp,
    stats,
    zarya_barrier,
)
from ow174.game.content import heroes


class ReinhardtGravitonTests(unittest.TestCase):
    def arena(self):
        test = zarya_fixture.ReinhardtZaryaWeaponsTests()
        self.addCleanup(test.doCleanups)
        return test.arena()

    def cast(self, case, point, *, rein=0):
        script = case.enemy.body_script
        cost = script.component.entity_var(338).value()
        self.assertEqual(cost, 2100)
        script.set_ult_charge(cost)
        angles = aim(case.fight._eye(case.enemy), point)
        case.run.step({case.enemy: (*angles, 32), case.rein: (0, 0, rein)})
        self.assertEqual(script.component.entity_var(342).value(), 0)
        for _ in range(65):
            if case.fight.gravitons.fields:
                return case.fight.gravitons.fields[0]
            case.run.step({case.enemy: (*angles, 0), case.rein: (0, 0, rein)})
        self.fail('actual Graviton projectile did not form an area')

    def test_actual_ground_landing_pulls_and_denies_charge_until_expiry(self):
        case = self.arena()
        field = self.cast(case, (2.5, 1, 1))
        start = tuple(case.rein.mover.position)
        self.assertEqual(field.end_tick - field.last_tick, 250)
        self.assertEqual(case.victim.total, 500)
        case.advance(50, rein=8)
        self.assertIn(case.rein.body, case.fight.gravitons.pulled)
        self.assertNotIn(case.rein.body, case.fight.charge_drives)
        self.assertGreater(case.rein.mover.position[0], start[0] + .5)
        self.assertAlmostEqual(case.victim.total, 496)
        pose = tuple(case.rein.mover.position)
        case.fight.gravitons.update(case.run.frame)
        self.assertIn(case.rein.body, case.fight.gravitons.pulled)
        self.assertEqual(tuple(case.rein.mover.position), pose)
        self.assertAlmostEqual(case.victim.total, 496)
        case.advance(field.end_tick - case.run.frame)
        self.assertFalse(case.fight.gravitons.fields)
        self.assertNotIn(case.rein.body, case.fight.gravitons.pulled)
        self.assertAlmostEqual(case.victim.total, 480)
        self.assertEqual(case.victim.now[combat.HEALTH_POOL], 300)
        case.advance(1, rein=8)
        case.advance(45)
        self.assertIn(case.rein.body, case.fight.charge_drives)

    def test_actual_graviton_damage_survives_hook_and_pull_resumes_after_release(self):
        case = self.arena()
        self.assertTrue(case.match.switch_hero(case.other, heroes()[hook.ROADHOG]))
        case.other.team = case.enemy.team
        case.place(case.other, (0, 1, 8))
        case.advance(8)
        field = self.cast(case, (2.5, 1, 1))
        case.advance(5)
        fight = case.fight
        self.assertIn(case.rein.body, fight.gravitons.pulled)
        for offset in range(55):
            x, y, z = case.rein.mover.position
            angles = aim(fight._eye(case.other), (x, y + 1.3, z))
            case.run.step({case.other: (*angles, 8 if offset == 0 else 0)})
            if fight.hooks.pulling(case.rein.body):
                break
        self.assertTrue(fight.hooks.pulling(case.rein.body))
        position = tuple(case.rein.mover.position)
        before = case.victim.total
        case.advance(4)
        self.assertTrue(fight.hooks.pulling(case.rein.body))
        self.assertNotIn(case.rein.body, fight.gravitons.pulled)
        self.assertIn(field, fight.gravitons.fields)
        self.assertGreater(case.rein.mover.position[2], position[2])
        self.assertAlmostEqual(case.victim.total, before - .32, places=4)

        case.match.leave(case.other)
        case.run.players.remove(case.other)
        self.assertFalse(fight.hooks.flights)
        case.advance(5)
        self.assertIn(field, fight.gravitons.fields)
        self.assertIn(case.rein.body, fight.gravitons.pulled)
        self.assertFalse(fight.controlled(case.rein.body))

    def test_elevated_wall_impact_lifts_reinhardt_and_release_restores_gravity(self):
        case = self.arena()
        fixtures.charge_wall(case, -2)
        field = self.cast(case, (0, 5, -2))
        self.assertGreater(field.position[1], 4)
        case.advance(40)
        self.assertIn(case.rein.body, case.fight.gravitons.pulled)
        self.assertGreater(case.rein.mover.position[1], 1.5)
        case.rein.team = case.enemy.team
        case.advance(70)
        self.assertNotIn(case.rein.body, case.fight.gravitons.pulled)
        self.assertAlmostEqual(case.rein.mover.position[1], 1, delta=.05)

    def test_actual_control_and_damage_immunity_in_existing_field(self):
        for hero, button, damage in (
            (defenses.ORISA, 8, .96), (defenses.MEI, 8, 0), (defenses.ZENYATTA, 32, 0),
        ):
            with self.subTest(hero=hero):
                case = self.arena()
                self.assertTrue(case.match.switch_hero(case.rein, heroes()[hero]))
                case.place(case.rein, (0, 1, 0))
                case.advance(12)
                self.cast(case, (2.5, 1, 1))
                if button == 32:
                    script = case.rein.body_script
                    script.set_ult_charge(script.component.entity_var(338).value())
                case.advance(1, rein=button)
                self.assertTrue(defenses.control_immune(case.rein))
                vitals = case.fight.vitals_of(case.fight._target_entity(case.rein.body))
                before = vitals.total
                pose = tuple(case.rein.mover.position)
                case.advance(20)
                self.assertNotIn(case.rein.body, case.fight.gravitons.pulled)
                self.assertAlmostEqual(vitals.total, before - damage, places=4)
                self.assertAlmostEqual(math.dist(case.rein.mover.position, pose), 0, delta=.05)

    def test_actual_cryo_cancel_allows_same_field_to_reacquire(self):
        case = self.arena()
        self.assertTrue(case.match.switch_hero(case.rein, heroes()[defenses.MEI]))
        case.place(case.rein, (0, 1, 0))
        case.advance(12)
        field = self.cast(case, (2.5, 1, 1))
        case.advance(1, rein=8)
        case.advance(35)
        self.assertTrue(defenses.control_immune(case.rein))
        self.assertNotIn(case.rein.body, case.fight.gravitons.pulled)
        vitals = case.fight.vitals_of(case.fight._target_entity(case.rein.body))
        before = vitals.total
        case.advance(1, rein=2)
        case.advance(25)
        self.assertFalse(defenses.control_immune(case.rein))
        self.assertIn(field, case.fight.gravitons.fields)
        self.assertIn(case.rein.body, case.fight.gravitons.pulled)
        self.assertLess(vitals.total, before)

    def test_actual_projectile_range_expiry_in_open_air_creates_no_area(self):
        case = self.arena()
        script = case.enemy.body_script
        script.set_ult_charge(2100)
        angles = aim(case.fight._eye(case.enemy), (0, 50, 6))
        case.run.step({case.enemy: (*angles, 32)})
        rocket, = case.fight.rockets
        self.assertEqual(rocket.definition, graviton.DEFINITION)
        self.assertEqual(rocket.left, 4)
        self.assertEqual(script.component.entity_var(342).value(), 0)
        for _ in range(260):
            case.advance()
            self.assertFalse(case.fight.gravitons.fields)
        self.assertNotIn(rocket, case.fight.rockets)
        self.assertEqual(case.victim.total, 500)

    @unittest.skipUnless(fixtures.BARRIER_DATA.is_file(), 'prepared Barrier Field required')
    def test_barrier_stops_projectile_damage_but_not_the_landed_area(self):
        case = self.arena()
        case.advance(20, rein=2)
        field = self.cast(case, (0, 2.4, 0), rein=2)
        barrier = case.fight.barriers[case.rein.body]
        self.assertEqual(barrier.current, 1595)
        self.assertEqual(case.victim.total, 500)
        case.advance(40, rein=2)
        self.assertTrue(barrier.active)
        self.assertEqual(barrier.current, 1595)
        self.assertAlmostEqual(case.victim.total, 496.8)
        self.assertIn(field, case.fight.gravitons.fields)

    def test_static_wall_allied_team_and_range_deny_area_damage_and_pull(self):
        for reason in ('wall', 'ally', 'range'):
            with self.subTest(reason=reason):
                case = self.arena()
                if reason == 'wall':
                    fixtures.charge_wall(case, 2)
                elif reason == 'ally':
                    case.enemy.team = case.rein.team
                field = self.cast(case, (18, 2.5, 0) if reason == 'range' else (0, 1, 0))
                if reason == 'range':
                    self.assertGreater(math.hypot(field.position[0], field.position[2]), 7)
                case.advance(90)
                self.assertEqual(case.victim.total, 500)
                self.assertNotIn(case.rein.body, case.fight.gravitons.pulled)

    def test_source_body_replacement_retires_landed_area(self):
        case = self.arena()
        self.cast(case, (2.5, 1, 1))
        case.advance(15)
        before = case.victim.total
        self.assertLess(before, 500)
        self.assertTrue(case.match.switch_hero(case.enemy, heroes()[combat.REINHARDT]))
        self.assertFalse(case.fight.gravitons.fields)
        case.advance(90)
        self.assertEqual(case.victim.total, before)
        self.assertNotIn(case.rein.body, case.fight.gravitons.pulled)

    def test_area_cancels_active_charge_but_allows_fire_strike(self):
        case = self.arena()
        case.place(case.enemy, (3, 1, 6))
        case.advance(1, rein=8)
        for _ in range(50):
            case.advance()
            if case.rein.body in case.fight.charge_drives:
                break
        self.assertIn(case.rein.body, case.fight.charge_drives)
        self.cast(case, (0, 1, 3))
        case.advance(5)
        self.assertIn(case.rein.body, case.fight.gravitons.pulled)
        self.assertNotIn(case.rein.body, case.fight.charge_drives)
        self.assertFalse(case.fight.pinned(case.enemy.body))
        case.advance(1, rein=16)
        for _ in range(45):
            case.advance()
            if any(r.shooter is case.rein and r.penetrating for r in case.fight.rockets):
                break
        self.assertTrue(any(r.shooter is case.rein and r.penetrating for r in case.fight.rockets))
        self.assertIn(case.rein.body, case.fight.gravitons.pulled)

    def test_area_survives_actual_caster_death_and_keeps_damaging(self):
        case = self.arena()
        field = self.cast(case, (2.5, 1, 1))
        enemy = case.fight.vitals_of(case.fight._target_entity(case.enemy.body))
        enemy.damage(390)
        shot = aim(case.fight._eye(case.other), (0, 2.1, 6))
        case.run.step({case.other: (*shot, 1)})
        self.assertTrue(enemy.dead)
        before = case.victim.total
        case.advance(30)
        self.assertIn(field, case.fight.gravitons.fields)
        self.assertAlmostEqual(case.victim.total, before - 2.4)

    def test_actual_deflect_redirects_ballistic_flight_and_owns_landed_area(self):
        case = self.arena()
        case.enemy.team = case.rein.team
        case.other.team = 1
        self.assertTrue(case.match.switch_hero(case.other, heroes()[deflection.GENJI]))
        case.place(case.enemy, (0, 1, 0))
        case.place(case.other, (0, 1, 6))
        case.place(case.rein, (2, 1, 2))
        case.advance(15)
        outward = aim(case.fight._eye(case.enemy), (0, 2.6, 6))
        redirect = aim(case.fight._eye(case.other), (2, 2.3, 2))
        case.run.step({case.other: (*redirect, 16)})
        for _ in range(8):
            case.run.step({case.other: (*redirect, 0)})
        case.enemy.body_script.set_ult_charge(2100)
        case.run.step({case.enemy: (*outward, 32), case.other: (*redirect, 0)})
        reflected = None
        for _ in range(65):
            case.run.step({case.other: (*redirect, 0)})
            if case.fight.gravitons.fields:
                break
            reflected = next((r for r in case.fight.rockets if r.reflected), reflected)
        self.assertIsNotNone(reflected)
        self.assertEqual(reflected.definition, graviton.DEFINITION)
        self.assertIs(reflected.shooter, case.other)
        self.assertGreater(reflected.velocity[0], 0)
        self.assertLess(reflected.velocity[2], 0)
        self.assertAlmostEqual(reflected.gravity, 9.8, places=5)
        self.assertIsNone(reflected.charge_origin)
        field, = case.fight.gravitons.fields
        self.assertIs(field.shooter, case.other)
        self.assertEqual(field.body, case.other.body)
        self.assertEqual(field.team, case.other.team)
        self.assertEqual(case.enemy.stats.value(stats.HERO_DAMAGE), 0)
        before = case.victim.total
        self.assertEqual(before, 497.5)
        self.assertEqual(case.other.stats.value(stats.HERO_DAMAGE), 2.5)
        case.advance(30)
        self.assertAlmostEqual(case.victim.total, before - 2.4)
        self.assertIn(case.rein.body, case.fight.gravitons.pulled)
        genji = case.fight.vitals_of(case.fight._target_entity(case.other.body))
        self.assertEqual(genji.total, 200)

    def test_actual_grasp_consumes_projectile_without_forming_area(self):
        case = self.arena()
        self.assertTrue(case.match.switch_hero(case.other, heroes()[sigma_grasp.SIGMA]))
        case.place(case.other, (0, 1, 3))
        case.advance(15)
        case.run.step({case.other: (0, 0, 8)})
        case.advance(10)
        angles = aim(case.fight._eye(case.enemy), (0, 2.2, 3))
        case.enemy.body_script.set_ult_charge(2100)
        case.run.step({case.enemy: (*angles, 32)})
        self.assertTrue(any(r.definition == graviton.DEFINITION for r in case.fight.rockets))
        case.advance(60)
        self.assertFalse(case.fight.rockets)
        self.assertFalse(case.fight.gravitons.fields)
        self.assertFalse(case.fight.gravitons.pulled)
        self.assertEqual(case.victim.total, 500)
        grasp = next(i for i in case.other.body_script.component.instances.values()
                     if i.graph.index == sigma_grasp.GRAPH)
        self.assertEqual(grasp.vars[21312].value(), 5)
        case.advance(80)
        sigma = case.fight.vitals_of(case.fight._target_entity(case.other.body))
        self.assertAlmostEqual(sigma.grasp_shields, 3, places=4)

    def test_actual_projected_barrier_suspends_pull_and_absorbs_field_damage(self):
        case = self.arena()
        self.assertTrue(case.match.switch_hero(case.other, heroes()[zarya_barrier.HERO]))
        case.place(case.other, (-3, 1, -6))
        case.advance(15)
        field = self.cast(case, (2.5, 1, 1))
        case.advance(10)
        self.assertIn(case.rein.body, case.fight.gravitons.pulled)
        x, y, z = case.rein.mover.position
        angles = aim(case.fight._eye(case.other), (x, y + 1, z))
        case.run.step({case.other: (*angles, 16)})
        bubble = case.fight.zarya_bubbles[case.rein.body]
        before = case.victim.total
        case.advance(30)
        self.assertNotIn(case.rein.body, case.fight.gravitons.pulled)
        self.assertEqual(case.victim.total, before)
        self.assertAlmostEqual(bubble.health, 197.6)
        case.advance(112)
        self.assertIn(field, case.fight.gravitons.fields)
        self.assertIn(case.rein.body, case.fight.gravitons.pulled)
        self.assertLess(case.victim.total, before)


@unittest.skipUnless((fixtures.NPC_DATA / "pve_npcs_174.json").is_file() and fixtures.NPC_MOVERS.is_file(),
                     "authorized Talon graph and mover inputs required")
class GravitonTalonTests(unittest.TestCase):
    scenario = fixtures.TalonInteractionTests.scenario
    place = staticmethod(fixtures.HammerTests.place)

    @classmethod
    def setUpClass(cls):
        fixtures.TalonInteractionTests.setUpClass()
        cls.catalog = fixtures.TalonInteractionTests.catalog

    @classmethod
    def tearDownClass(cls):
        fixtures.TalonInteractionTests.tearDownClass()

    def arena(self, definition):
        actor, _, health = self.scenario(definition, (0, 1, 3))
        self.match.game_map = self.match.game_map.__class__(
            **{**self.match.game_map.__dict__, 'free_for_all': False})
        self.rein.team = self.enemy.team = self.other.team = 0
        self.assertTrue(self.match.switch_hero(self.enemy, heroes()[zarya_barrier.HERO]))
        self.place(self.enemy, (-4, 1, 6))
        self.fight = self.match.combat
        for _ in range(15):
            self.run.step({})
        return actor, health

    def cast(self, point):
        angles = aim(self.fight._eye(self.enemy), point)
        self.enemy.body_script.set_ult_charge(2100)
        self.run.step({self.enemy: (*angles, 32)})
        for _ in range(50):
            if self.fight.gravitons.fields:
                return self.fight.gravitons.fields[0]
            self.run.step({})
        self.fail("actual 08CE did not land")

    def test_actual_field_pulls_and_damages_each_talon_body_then_releases(self):
        self.assertEqual(len(self.catalog), 5)
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                actor, health = self.arena(definition)
                field = self.cast((2, 1, 3))
                before, start = health.total, actor.position
                for _ in range(20):
                    self.run.step({})
                self.assertAlmostEqual(health.total, before - 1.6, places=5)
                self.assertIn(actor.entity, self.fight.gravitons.pulled)
                self.assertIn(actor.entity, self.fight.gravitons.npc_movers)
                mover = self.fight.gravitons.npc_movers[actor.entity]
                self.assertEqual(actor.body_script.component.world.movement_flags(actor.entity), mover.flags)
                self.assertGreater(math.dist(start, actor.position), .2)
                self.assertLessEqual(math.dist(start, actor.position), .97)
                self.assertLess(math.dist(actor.position, field.position), math.dist(start, field.position))
                self.assertEqual(actor.last_tick, self.run.frame)
                actor.team = self.enemy.team
                pose, health_before = actor.position, health.total
                self.run.step({})
                self.assertEqual(actor.position, pose)
                self.assertEqual(actor.velocity, (0, 0, 0))
                self.assertEqual(health.total, health_before)
                self.assertNotIn(actor.entity, self.fight.gravitons.pulled)
                self.assertNotIn(actor.entity, self.fight.gravitons.npc_movers)
                self.assertEqual(
                    actor.body_script.component.world.movement_flags(actor.entity), actor.movement_flags,
                )

    def test_removed_pulled_npc_retires_physics_without_ending_field(self):
        actor, _ = self.arena(next(iter(self.catalog.values())))
        field = self.cast((2, 1, 3))
        for _ in range(12):
            self.run.step({})
        self.assertIn(actor.entity, self.fight.gravitons.npc_movers)
        self.assertEqual(npc.remove_spawned(self.match), (actor.entity,))
        self.assertNotIn(actor.entity, self.fight.gravitons.pulled)
        self.assertNotIn(actor.entity, self.fight.gravitons.npc_movers)
        self.run.step({})
        self.assertIn(field, self.fight.gravitons.fields)

    def test_elevated_field_lifts_trooper_then_releases_into_grounded_motion(self):
        actor, _ = self.arena(next(d for d in self.catalog.values() if d.name == "Trooper"))
        fixtures.charge_wall(self, 0)
        field = self.cast((0, 5, 0))
        self.assertGreater(field.position[1], 4)
        for _ in range(40):
            self.run.step({})
        self.assertIn(actor.entity, self.fight.gravitons.pulled)
        self.assertGreater(actor.position[1], 1.5)
        actor.team = self.enemy.team
        self.run.step({})
        self.assertNotIn(actor.entity, self.fight.gravitons.pulled)
        self.assertIn(actor.entity, self.fight.knockbacks)
        for _ in range(100):
            self.run.step({})
        self.assertNotIn(actor.entity, self.fight.knockbacks)
        self.assertAlmostEqual(actor.position[1], 1, delta=.05)

    def test_reinhardt_capture_keeps_pose_ownership_inside_actual_field(self):
        actor, health = self.arena(next(d for d in self.catalog.values() if d.name == "Trooper"))
        self.cast((0, 1, 6))
        self.run.step({})
        self.assertIn(actor.entity, self.fight.gravitons.pulled)
        self.run.step({self.rein: (0, 0, 8)})
        for _ in range(80):
            self.run.step({})
            if self.fight.pinned(actor.entity):
                break
        self.assertTrue(self.fight.pinned(actor.entity))
        before = health.total
        self.run.step({})
        self.assertTrue(self.fight.pinned(actor.entity))
        self.assertNotIn(actor.entity, self.fight.gravitons.pulled)
        self.assertNotIn(actor.entity, self.fight.gravitons.npc_movers)
        self.assertAlmostEqual(health.total, before - .08)

    def test_graviton_stops_actual_heavy_charge_and_denies_restart_during_pull(self):
        actor, _ = self.arena(next(d for d in self.catalog.values() if d.name == "Heavy Assault"))
        self.rein.mover.yaw, self.rein.mover.pitch = aim(self.fight._eye(self.rein), (0, 2, 3))
        self.assertEqual(npc_control.start(self.match, self.rein, action="charge"), actor.entity)
        for _ in range(70):
            self.run.step({})
            if actor.entity in self.fight.charge_drives:
                break
        self.assertIn(actor.entity, self.fight.charge_drives)
        self.cast((0, 1, 1))
        for _ in range(10):
            self.run.step({})
            if actor.entity in self.fight.gravitons.pulled:
                break
        self.assertIn(actor.entity, self.fight.gravitons.pulled)
        self.assertNotIn(actor.entity, self.fight.charge_drives)
        self.assertFalse(self.fight.pinned(self.rein.body))
        self.assertFalse(actor.attack_input)
        for _ in range(40):
            self.run.step({})
        self.assertNotIn(actor.entity, self.fight.charge_drives)
        self.assertFalse(actor.attack_input)

    def test_route_timer_pauses_under_pull_and_resumes_without_catch_up(self):
        actor, _ = self.arena(next(iter(self.catalog.values())))
        self.cast((2, 1, 3))
        self.run.step({})
        self.assertIn(actor.entity, self.fight.gravitons.pulled)
        actor.route = [(0, 1, -10)]
        actor.wait = .25
        for _ in range(30):
            self.run.step({})
        self.assertEqual(actor.wait, .25)
        self.assertIsNone(actor.target)
        actor.team = self.enemy.team
        self.run.step({})
        self.run.step({})
        self.assertGreater(actor.wait, .22)
        self.assertLess(actor.wait, .25)
        self.assertIsNone(actor.target)
        for _ in range(30):
            self.run.step({})
        self.assertEqual(actor.target, actor.route[0])
        self.assertNotIn(actor.entity, self.fight.gravitons.npc_movers)

    def test_actual_hammer_kill_retires_pulled_npc_physics(self):
        actor, health = self.arena(next(d for d in self.catalog.values() if d.name == "Trooper"))
        field = self.cast((2, 1, 3))
        self.run.step({})
        self.assertIn(actor.entity, self.fight.gravitons.npc_movers)
        health.now.update({combat.HEALTH_POOL: 60, combat.ARMOUR_POOL: 0, combat.SHIELDS_POOL: 0})
        angles = aim(self.fight._eye(self.rein), (actor.position[0], 2, actor.position[2]))
        self.run.step({self.rein: (*angles, 1)})
        for _ in range(45):
            self.run.step({self.rein: (*angles, 0)})
            if actor.dead:
                break
        self.assertTrue(actor.dead)
        self.assertNotIn(actor.entity, self.fight.gravitons.npc_movers)
        self.assertNotIn(actor.entity, self.fight.gravitons.pulled)
        self.assertIn(field, self.fight.gravitons.fields)

    def test_pulled_trooper_can_fire_actual_primary_at_reinhardt(self):
        actor, _ = self.arena(next(d for d in self.catalog.values() if d.name == "Trooper"))
        self.cast((2, 1, 3))
        self.run.step({})
        self.assertIn(actor.entity, self.fight.gravitons.pulled)
        x, y, z = actor.position
        self.rein.mover.yaw, self.rein.mover.pitch = aim(self.fight._eye(self.rein), (x, y + 1, z))
        self.assertEqual(npc_control.start(self.match, self.rein), actor.entity)
        victim = self.fight.vitals_of(self.fight._target_entity(self.rein.body))
        for _ in range(120):
            self.run.step({})
            if victim.total < 500:
                break
        self.assertEqual(victim.total, 485)
        self.assertIn(actor.entity, self.fight.gravitons.pulled)
        self.assertTrue(actor.attack_input)
