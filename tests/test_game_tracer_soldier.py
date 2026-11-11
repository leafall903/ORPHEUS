"""Actual Soldier: 76 inputs against the lane's hosted Tracer."""

import unittest
from dataclasses import replace

import test_game_tracer as tracer_tests
import test_game_tracer_dva_mech as mech_tests
import test_game_tracer_pulse_deflect as support_tests
from test_game_combat import aim
from test_game_mover import box, wall

from ow174.game import combat, deflection, dva, projectiles, sigma_grasp, soldier_visor, stats, tracer
from ow174.game.collision import World
from ow174.game.commands import FIRE, SECONDARY, Command
from ow174.game.script import nodes
from ow174.game.script.driver import ULT_CHARGE, ULT_COST

FIELD = 16
BLINK = 8
RECALL = 16
SPRINT = 8


class TracerSoldierTests(unittest.TestCase):
    def setUp(self):
        self.run, self.tracer, self.soldier, self.soldier_health = tracer_tests.TracerPistolTests().fight()
        self.match = self.run.match
        self.match.game_map = replace(self.match.game_map, free_for_all=False)
        self.tracer.team, self.soldier.team = 0, 1
        self.fight = self.match.combat
        target = next(t for t in self.fight._targets() if t.owner is self.tracer)
        self.health = self.fight.vitals_of(target)
        self.step()

    def angles(self, source, target):
        x, y, z = target.mover.position
        return aim(self.fight._eye(source), (x, y + 1, z))

    def step(self, count=1, *, soldier=0, soldier_yaw=None, tracer_buttons=0, tracer_yaw=None):
        for _ in range(count):
            soldier_aim = self.angles(self.soldier, self.tracer) if soldier_yaw is None else (soldier_yaw, 0)
            tracer_aim = self.angles(self.tracer, self.soldier) if tracer_yaw is None else (tracer_yaw, 0)
            self.run.step({self.soldier: (*soldier_aim, soldier),
                           self.tracer: (*tracer_aim, tracer_buttons)})

    def launch(self):
        self.step(soldier=SECONDARY)
        rocket, = self.fight.rockets
        self.assertEqual(rocket.definition, combat.SOLDIER_HELIX)
        return rocket

    def defender(self, hero, offset, *, allied=False, body=None):
        defender, _ = support_tests.PulseDeflectTests().support(self.run, self.tracer, hero=hero)
        if body is not None:
            self.assertTrue(self.match.switch_hero(defender, replace(defender.hero, body=body)))
        defender.team = self.soldier.team if allied else self.tracer.team
        x, y, z = self.tracer.mover.position
        for actor, distance in ((self.soldier, 12), (defender, offset)):
            actor.mover.position[:] = (x, y, z + distance)
            actor.mover.body = None
        self.step(20)
        return defender

    def begin_visor(self, *, earned=False):
        script = self.soldier.body_script
        script.ult_seconds = 0
        visor = next(i for i in script.component.instances.values() if i.graph.index == soldier_visor.GRAPH)
        meter = script.component.entity_var(ULT_CHARGE)
        cost = script.component.entity_var(ULT_COST).value()
        if not earned:
            script.set_ult_charge(cost)
        self.assertEqual(meter.value(), cost)
        self.step(soldier=32)
        self.assertTrue(visor.state(0).flags & nodes.Ability.RUNNING)
        self.assertEqual(meter.value(), 0)
        self.step(74)
        self.assertFalse(visor.state(soldier_visor.SEARCH).active)
        self.step()
        search = visor.state(soldier_visor.SEARCH)
        self.assertTrue(search.active)
        return visor, search

    def sprint_frame(self, *, buttons=0, forward=127, tracer_buttons=0):
        self.run.frame += 1
        frame = self.run.frame
        self.match.update(frame * combat.TICK, frame)
        for actor in self.run.players:
            actor.client.queue_entities(self.match.remote_movements(actor))
        yaw, pitch = self.angles(self.tracer, self.soldier)
        self.tracer.take_commands([Command(frame, yaw=yaw, pitch=pitch, buttons=tracer_buttons)])
        self.soldier.take_commands([Command(frame, forward=forward, buttons=buttons)])

    def test_sprint_speed_survives_nonlethal_tracer_fire_and_stops_without_input(self):
        instances = self.soldier.body_script.component.instances.values()
        sprint = next(i for i in instances if i.graph.index == 0x0255)
        base = self.soldier.mover.data.run_forward
        self.sprint_frame(buttons=SPRINT, forward=0)
        self.assertFalse(sprint.state(1).flags & nodes.Ability.RUNNING)
        for _ in range(12):
            self.sprint_frame()
        self.assertAlmostEqual(self.soldier.mover.velocity[2], base)
        self.sprint_frame(buttons=SPRINT)
        for _ in range(10):
            self.sprint_frame()
        self.assertAlmostEqual(self.soldier.mover.velocity[2], 1.5 * base)
        self.sprint_frame(tracer_buttons=FIRE)
        self.assertEqual(self.soldier_health.total, 188)
        self.assertAlmostEqual(self.soldier.mover.velocity[2], 1.5 * base)
        self.assertTrue(sprint.state(1).flags & nodes.Ability.RUNNING)
        self.sprint_frame(forward=0)
        self.assertFalse(sprint.state(1).flags & nodes.Ability.RUNNING)
        for _ in range(10):
            self.sprint_frame()
        self.assertAlmostEqual(self.soldier.mover.velocity[2], base)

    def test_pulse_stays_on_sprinting_soldier_until_lethal_blast(self):
        script = self.tracer.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        self.step(tracer_buttons=32, tracer_yaw=0)
        for _ in range(50):
            self.step()
            if self.fight.pulse_bombs.bombs and self.fight.pulse_bombs.bombs[0].stuck:
                break
        bomb, = self.fight.pulse_bombs.bombs
        self.assertEqual(bomb.attached, self.soldier.body)
        self.assertEqual(self.soldier_health.total, 195)
        start = self.soldier.mover.position[2]
        self.sprint_frame(buttons=SPRINT)
        for _ in range(32):
            self.sprint_frame()
        self.assertAlmostEqual(self.soldier.mover.velocity[2], 1.5 * self.soldier.mover.data.run_forward)
        self.assertGreater(self.soldier.mover.position[2], start + 3)
        self.assertGreater(bomb.position[2], start + 3)
        self.assertEqual(bomb.attached, self.soldier.body)
        for _ in range(50):
            self.sprint_frame()
            if not self.fight.pulse_bombs.bombs:
                break
        self.assertFalse(self.fight.pulse_bombs.bombs)
        self.assertTrue(self.soldier_health.dead)
        self.assertEqual(self.soldier_health.killer, self.tracer.body)

    def test_visor_charge_is_earned_by_rifle_helix_and_biotic_field(self):
        script = self.soldier.body_script
        script.ult_seconds = 0
        meter = script.component.entity_var(ULT_CHARGE)
        cost = script.component.entity_var(ULT_COST).value()
        script.set_ult_charge(cost - 141)
        self.step(soldier=FIRE)
        self.assertEqual(self.health.total, 130)
        self.assertEqual(meter.value(), cost - 121)
        self.launch()
        self.assertEqual(meter.value(), cost - 121)
        self.step(15)
        self.assertEqual(self.health.total, 10)
        self.assertEqual(meter.value(), cost - 1)
        self.step(soldier=32)
        self.assertEqual(meter.value(), cost - 1)
        self.soldier.team = self.tracer.team
        x, y, z = self.tracer.mover.position
        self.soldier.mover.position[:] = (x, y, z + 1)
        self.soldier.mover.body = None
        self.step(soldier=FIELD)
        for _ in range(80):
            if meter.value() == cost:
                break
            self.step()
        self.assertGreater(self.health.total, 10)
        visor, _ = self.begin_visor(earned=True)
        self.soldier.team = 1 - self.tracer.team
        before = self.health.total
        self.step(soldier=FIRE)
        self.assertEqual(self.health.total, before - 20)
        self.assertEqual(meter.value(), 0)
        self.step(376)
        self.assertFalse(visor.state(0).flags & nodes.Ability.RUNNING)
        self.step(soldier=FIRE)
        self.assertEqual(self.health.total, before - 40)
        self.assertEqual(meter.value(), 20)

    def test_soldier_passive_uses_authored_five_points_per_second(self):
        meter = self.soldier.body_script.component.entity_var(ULT_CHARGE)
        self.assertEqual(meter.value(), 0)
        self.step(63)
        self.assertEqual(meter.value(), 5)
        self.step(63)
        self.assertEqual(meter.value(), 10)

    def test_visor_rifle_uses_authored_full_damage_inside_lock_range(self):
        self.tracer.mover.position[2] -= 35
        self.tracer.mover.body = None
        self.step(3)
        self.step(soldier=FIRE)
        ordinary = 150 - self.health.total
        self.assertGreater(ordinary, 0)
        self.assertLess(ordinary, 20)
        self.step(15)
        _, search = self.begin_visor()
        self.assertEqual(search.targets, (self.tracer.body,))
        before = self.health.total
        self.step(soldier=FIRE)
        self.assertEqual(self.health.total, before - 20)

    def test_visor_uses_authored_window_to_redirect_rifle_and_returns_to_manual_aim(self):
        yaw = -30000
        script = self.soldier.body_script
        script.ult_seconds = 0
        self.step(soldier=32)
        self.step(soldier=FIRE, soldier_yaw=yaw)
        self.assertEqual(self.health.total, 150)
        self.step(15)
        visor, search = self.begin_visor()
        self.step(soldier=FIRE, soldier_yaw=yaw)
        self.assertEqual(search.targets, (self.tracer.body,))
        self.assertEqual(self.health.total, 130)
        self.step(tracer_buttons=FIRE)
        self.assertEqual(self.soldier_health.total, 188)
        self.step(376)
        self.assertFalse(visor.state(0).flags & nodes.Ability.RUNNING)
        self.assertFalse(search.active)
        self.assertFalse(search.targets)
        self.step(soldier=FIRE, soldier_yaw=yaw)
        self.assertEqual(self.health.total, 130)
        self.step(soldier=32)
        self.assertFalse(visor.state(0).flags & nodes.Ability.RUNNING)

    def test_visor_search_rejects_cover_range_and_team(self):
        for rejected in ("cover", "range", "team"):
            with self.subTest(rejected=rejected):
                self.setUp()
                x, y, z = self.tracer.mover.position
                if rejected == "cover":
                    floor = box(x - 12, x + 12, y - 1, y, z - 12, z + 12)
                    self.match.collision_world = World(floor + wall(x - 5, z + 2.5, x + 5, z + 2.5, y, y + 5))
                elif rejected == "range":
                    self.tracer.mover.position[2] -= 42
                    self.tracer.mover.body = None
                else:
                    self.tracer.team = self.soldier.team
                _, search = self.begin_visor()
                self.assertFalse(search.targets)
                self.step(soldier=FIRE, soldier_yaw=-30000)
                self.assertEqual(self.health.total, 150)

    def test_blink_and_recall_break_visor_lock_then_rifle_can_reacquire(self):
        facing = -32768
        self.step(200)
        _, search = self.begin_visor()
        self.assertEqual(search.targets, (self.tracer.body,))
        self.step(soldier_yaw=facing, tracer_buttons=BLINK, tracer_yaw=16384)
        self.step(soldier=FIRE, soldier_yaw=facing)
        self.assertFalse(search.targets)
        self.assertEqual(self.health.total, 150)
        self.step(12, soldier_yaw=facing)
        self.step(soldier_yaw=facing, tracer_buttons=BLINK, tracer_yaw=-16384)
        self.step(soldier_yaw=facing)
        self.assertEqual(search.targets, (self.tracer.body,))
        self.step(soldier_yaw=facing, tracer_buttons=RECALL)
        self.step(25, soldier_yaw=facing)
        self.assertTrue(tracer.recalling(self.tracer))
        self.step(soldier=FIRE, soldier_yaw=facing)
        self.assertFalse(search.targets)
        self.assertEqual(self.health.total, 150)
        self.step(86, soldier_yaw=facing)
        self.assertFalse(tracer.recalling(self.tracer))
        self.assertEqual(search.targets, (self.tracer.body,))
        self.step(soldier=FIRE, soldier_yaw=-30000)
        self.assertEqual(self.health.total, 130)

    def test_visor_helix_keeps_manual_aim_and_the_variant_hits_a_real_dome(self):
        for barrier in (False, True):
            with self.subTest(barrier=barrier):
                self.setUp()
                if barrier:
                    winston = self.defender(0x02E0000000000009, -3.5)
                    self.run.step({winston: (0, 0, 16)})
                    self.step(8)
                    dome = self.fight.winston_barriers.actors[winston.body]
                _, search = self.begin_visor()
                self.assertIn(self.tracer.body, search.targets)
                self.step(soldier=SECONDARY, soldier_yaw=None if barrier else -30000)
                rocket, = self.fight.rockets
                self.assertEqual(rocket.definition, soldier_visor.VISOR_HELIX)
                if barrier:
                    self.step(30)
                    self.assertNotIn(rocket, self.fight.rockets)
                    self.assertEqual(dome.health, 580)
                else:
                    self.assertLess(rocket.direction[0], -0.2)
                    self.step(35)
                self.assertEqual(self.health.total, 150)

    def test_lethal_tracer_fire_stops_visor_and_clears_its_targets(self):
        visor, search = self.begin_visor()
        self.assertEqual(search.targets, (self.tracer.body,))
        self.soldier_health.damage(195)
        self.step(tracer_buttons=FIRE)
        self.assertTrue(self.soldier_health.dead)
        self.assertEqual(self.soldier_health.killer, self.tracer.body)
        self.assertFalse(search.active)
        self.assertFalse(search.targets)
        self.assertFalse(visor.state(0).flags & nodes.Ability.RUNNING)
        self.step(10, soldier=FIRE, soldier_yaw=-30000)
        self.assertEqual(self.health.total, 150)

    def test_lethal_rifle_and_helix_respect_pulse_release_boundary(self):
        for attack, wound in ((FIRE, 20), (SECONDARY, 100)):
            for released in (False, True):
                with self.subTest(attack=attack, released=released):
                    self.setUp()
                    self.check_lethal_soldier_pulse_boundary(attack, wound, released)

    def check_lethal_soldier_pulse_boundary(self, attack, wound, released):
        self.health.damage(self.health.total - wound)
        self.fight._sync_body_health(self.tracer, self.health)
        source = self.tracer.body
        script = self.tracer.body_script
        script.ult_seconds = 0
        charge = script.component.entity_var(ULT_CHARGE)
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        if not released and attack == SECONDARY:
            self.launch()
        self.step(tracer_buttons=32, tracer_yaw=0)
        self.assertEqual(charge.value(), 0)
        self.assertEqual(self.health.total, wound)
        bombs = self.fight.pulse_bombs.bombs
        self.assertFalse(bombs)
        if released:
            for _ in range(15):
                self.step()
                if bombs:
                    break
            bomb, = bombs
            self.assertEqual(bomb.source, source)
            self.assertFalse(bomb.stuck)
        if released or attack == FIRE:
            self.step(soldier=attack)
        for _ in range(14):
            if not released:
                self.assertFalse(bombs)
            if self.health.dead:
                break
            self.step()
        self.assertTrue(self.health.dead)
        self.assertEqual(self.health.killer, self.soldier.body)
        self.assertEqual(self.tracer.stats.value(stats.DEATHS), 1)
        self.assertFalse(self.soldier_health.dead)
        if released:
            self.assertEqual(bombs, [bomb])
            self.assertFalse(bomb.stuck)
            for _ in range(45):
                if bomb.stuck:
                    break
                self.step()
            self.assertEqual(bomb.attached, self.soldier.body)
            self.assertEqual(self.soldier_health.total, 195)
            self.assertGreater(bomb.fuse_left, 0)
        for _ in range(75):
            if not released:
                self.assertFalse(bombs)
            elif self.soldier_health.dead:
                break
            self.step()
        self.assertFalse(bombs)
        self.assertEqual(charge.value(), 0)
        self.assertEqual(self.soldier_health.total, 0 if released else 200)
        self.assertEqual(self.soldier_health.killer, source if released else None)
        self.assertEqual(self.tracer.stats.value(stats.ELIMINATIONS), int(released))

    def test_actual_helix_hits_once_tracer_returns_fire_and_cooldown_allows_reuse(self):
        rocket = self.launch()
        self.assertEqual(self.health.total, 150)
        self.step(14, soldier=SECONDARY)
        self.assertNotIn(rocket, self.fight.rockets)
        self.assertEqual(self.health.total, 30)
        hits = self.soldier.client.messages(combat.HIT)
        self.assertEqual([notice["+0x78"]["+0x4"] for notice in hits], [40, 80])
        self.step(tracer_buttons=FIRE)
        self.assertEqual(self.soldier_health.total, 188)
        self.step(soldier=SECONDARY)
        self.assertFalse(self.fight.rockets)
        self.step(370)
        self.launch()
        self.step(14)
        self.assertTrue(self.health.dead)
        self.assertEqual(self.health.killer, self.soldier.body)

    def test_blink_after_helix_launch_evades_both_damage_parts_and_returns_fire(self):
        x, _, _ = self.tracer.mover.position
        self.launch()
        self.step(tracer_buttons=BLINK, tracer_yaw=16384)
        self.assertGreater(self.tracer.mover.position[0], x + 7)
        self.step(25)
        self.assertEqual(self.health.total, 150)
        self.step(tracer_buttons=FIRE)
        self.assertEqual(self.soldier_health.total, 188)

    def test_active_recall_rejects_helix_and_preserves_tracers_return_fire(self):
        self.step(200)
        self.step(tracer_buttons=RECALL)
        for _ in range(30):
            if tracer.recalling(self.tracer):
                break
            self.step()
        self.assertTrue(tracer.recalling(self.tracer))
        self.launch()
        self.step(14)
        self.assertTrue(tracer.recalling(self.tracer))
        self.assertEqual(self.health.total, 150)
        self.step(100)
        self.assertFalse(tracer.recalling(self.tracer))
        self.step(tracer_buttons=FIRE)
        self.assertEqual(self.soldier_health.total, 188)

    def test_helix_wall_and_team_reject_damage(self):
        for blocked in ("wall", "team"):
            with self.subTest(blocked=blocked):
                self.setUp()
                if blocked == "wall":
                    x, y, z = self.tracer.mover.position
                    between = z + 2.5
                    floor = box(x - 12, x + 12, y - 1, y, z - 12, z + 12)
                    self.match.collision_world = World(floor + wall(x - 5, between, x + 5, between, y, y + 5))
                    self.step(2)
                else:
                    self.soldier.team = self.tracer.team
                self.launch()
                self.step(20)
                self.assertEqual(self.health.total, 150)
                if blocked == "wall":
                    self.assertFalse(self.fight.rockets)

    def test_actual_winston_dome_stops_helix_and_blocks_splash_to_tracer(self):
        for allied in (False, True):
            with self.subTest(allied_with_soldier=allied):
                self.setUp()
                winston = self.defender(0x02E0000000000009, -3.5, allied=allied)
                self.run.step({winston: (0, 0, 16)})
                self.step(8)
                bubble = self.fight.winston_barriers.actors[winston.body]
                self.assertEqual(bubble.health, 700)
                self.launch()
                self.step(30)
                self.assertFalse(self.fight.rockets)
                self.assertEqual(bubble.health, 700 if allied else 580)
                self.assertEqual(self.health.total, 30 if allied else 150)

    def test_actual_deflect_redirects_helix_and_grasp_absorbs_its_damage_budget(self):
        if not deflection.absorbable(combat.SOLDIER_HELIX):
            self.skipTest("requires extracted Helix projectile tags")
        for hero, button in ((0x02E0000000000029, 16), (sigma_grasp.HERO, 8)):
            with self.subTest(hero=hero):
                self.setUp()
                defender = self.defender(hero, 5)
                self.run.step({defender: (0, 0, button)})
                self.step(8)
                rocket = self.launch()
                self.step(40)
                self.assertFalse(self.fight.rockets)
                self.assertEqual(self.health.total, 150)
                if hero == sigma_grasp.HERO:
                    grasp = next(i for i in defender.body_script.component.instances.values()
                                 if i.graph.index == sigma_grasp.GRASP)
                    self.assertEqual(grasp.vars[21312].value(), 120)
                    self.assertEqual(self.soldier_health.total, 200)
                else:
                    self.assertIs(rocket.shooter, defender)
                    self.assertEqual(rocket.team, defender.team)
                    self.assertEqual(self.soldier_health.total, 80)

    def test_actual_matrix_absorbs_helix_before_it_reaches_tracer(self):
        mech_tests.TracerDvaMechTests.setUpClass()
        if not deflection.absorbable(combat.SOLDIER_HELIX):
            self.skipTest("requires extracted Helix projectile tags")
        mech = self.defender(dva.HERO, 5, body=dva.MECH)
        self.step(100)
        holding = {mech: (0, 0, SECONDARY)}
        for _ in range(8):
            self.run.step(holding)
        matrix = next(i for i in mech.body_script.component.instances.values() if i.graph.index == 0x02CC)
        self.assertTrue(matrix.state(3).active)
        incoming = self.angles(self.soldier, self.tracer)
        self.run.step({**holding, self.soldier: (*incoming, SECONDARY)})
        rocket, = self.fight.rockets
        for _ in range(30):
            self.run.step(holding)
        self.assertNotIn(rocket, self.fight.rockets)
        self.assertFalse(self.soldier.client.messages(combat.HIT))
        self.assertEqual(self.health.total, 150)
        target = next(t for t in self.fight._targets() if t.owner is mech)
        self.assertEqual(self.fight.vitals_of(target).total, 600)

    def test_biotic_field_heals_ally_tracer_until_blink_exits_then_resumes_and_expires(self):
        self.soldier.team = self.tracer.team
        x, y, z = self.tracer.mover.position
        self.soldier.mover.position[:] = (x, y, z + 1)
        self.soldier.mover.body = None
        self.health.damage(100)
        self.step(soldier=FIELD)
        self.assertFalse(self.fight.projectiles.shots)
        for _ in range(80):
            self.step()
            if self.health.total > 50:
                break
        self.assertGreater(self.health.total, 50)
        field, = self.fight.projectiles.shots
        self.assertIsNotNone(field.field_tick)
        before = self.health.total
        self.step(10)
        self.assertAlmostEqual(self.health.total - before, 35 * 10 * combat.TICK)
        self.step(tracer_buttons=BLINK, tracer_yaw=16384)
        self.assertGreater(self.tracer.mover.position[0], x + 7)
        before = self.health.total
        self.step(10)
        self.assertEqual(self.health.total, before)
        self.step(tracer_buttons=BLINK, tracer_yaw=-16384)
        self.assertAlmostEqual(self.tracer.mover.position[0], x, delta=0.01)
        self.step(10)
        self.assertAlmostEqual(self.health.total - before, 35 * 10 * combat.TICK)
        self.soldier.team = 1 - self.tracer.team
        before = self.health.total
        self.step(soldier=FIELD)
        self.step(55)
        self.assertEqual(self.health.total, before)
        self.assertEqual(self.fight.projectiles.shots, [field])
        self.soldier.team = self.tracer.team
        self.step(field.field_tick + projectiles.FIELD_TICKS - self.run.frame)
        self.assertFalse(self.fight.projectiles.shots)
        self.health.damage(20)
        before = self.health.total
        self.step(10)
        self.assertEqual(self.health.total, before)
        self.step(650)
        self.step(soldier=FIELD)
        for _ in range(80):
            self.step()
            if self.health.total > before:
                break
        self.assertGreater(self.health.total, before)
