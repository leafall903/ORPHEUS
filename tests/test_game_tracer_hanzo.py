"""Actual Hanzo bow inputs and Tracer defense interactions."""

import os
import unittest
from dataclasses import replace
from unittest.mock import patch

import test_game_tracer as tracer_tests
import test_game_tracer_orisa_barrier as orisa_tests
import test_game_tracer_pulse_deflect as support_tests
import test_game_tracer_winston_barrier as winston_tests
from test_game_combat import aim
from test_game_mover import box, wall

from ow174.game import (
    combat,
    deflection,
    dva,
    orisa_barrier,
    sigma_barrier,
    sigma_grasp,
    tracer,
    zarya_barrier,
)
from ow174.game.collision import World
from ow174.game.commands import FIRE, SECONDARY
from ow174.game.content import heroes

HANZO = 0x02E0000000000005
FULL_ARROW = 0x0400000000000144
FULL_SONIC = 0x0400000000000149
SHORT_SONIC = 0x040000000000020D
STORM_ARROW = 0x040000000000270F


class TracerHanzoTests(unittest.TestCase):
    def setUp(self):
        self.run, self.player, self.hanzo, _ = tracer_tests.TracerPistolTests().fight()
        match = self.run.match
        self.fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        self.assertTrue(match.switch_hero(self.hanzo, heroes()[HANZO]))
        self.hanzo.team = 1 - self.player.team
        self.x, self.y, self.z = self.player.mover.position
        self.move(self.hanzo, (self.x, self.y, self.z + 12))
        self.health = winston_tests.TracerWinstonBarrierTests.health(self.run, self.player)
        self.aim = aim(self.fight._eye(self.hanzo), (self.x, self.y + 1.0, self.z))

    move = staticmethod(winston_tests.TracerWinstonBarrierTests.move)

    def draw(self, commands=None):
        for _ in range(70):
            self.run.step({**(commands or {}), self.hanzo: (*self.aim, FIRE)})
        self.assertFalse(self.fight.rockets)

    def release(self, commands, *, definition=FULL_ARROW):
        self.run.step({**commands, self.hanzo: (*self.aim, 0)})
        arrow, = self.fight.rockets
        self.assertEqual(arrow.definition, definition)
        self.assertEqual((arrow.direct.amount, arrow.speed), (125, 110))
        return arrow

    def wait_contact(self, arrow, commands):
        for _ in range(24):
            if arrow not in self.fight.rockets:
                break
            self.run.step(commands)
        self.assertNotIn(arrow, self.fight.rockets)

    def test_actual_bow_releases_on_button_up_and_blink_or_recall_avoids_it(self):
        for gate in ("body", "blink", "recall"):
            with self.subTest(gate=gate):
                self.setUp()
                self.draw()
                arrow = self.release({})
                if gate == "blink":
                    self.run.step({self.player: (16384, 0, 8)})
                    self.assertAlmostEqual(self.player.mover.position[0] - self.x, 7.5, places=2)
                elif gate == "recall":
                    self.run.step({self.player: (0, 0, 16)})
                    self.assertTrue(tracer.recalling(self.player))
                for _ in range(15):
                    self.run.step({})
                self.assertEqual(self.health.total, 25 if gate == "body" else 150)
                if gate == "body":
                    self.assertNotIn(arrow, self.fight.rockets)
                self.assertEqual(len(self.hanzo.client.messages(combat.HIT)), int(gate == "body"))

    def test_actual_winston_dome_stops_full_draw_arrow(self):
        winston, _ = support_tests.PulseDeflectTests().support(self.run, self.player, hero=0x02E0000000000009)
        winston.team = self.player.team
        self.move(winston, (self.x + 2.5, self.y, self.z))
        self.draw()
        self.run.step({self.hanzo: (*self.aim, FIRE), winston: (0, 0, 16)})
        for _ in range(3):
            self.run.step({self.hanzo: (*self.aim, FIRE)})
        bubble = self.fight.winston_barriers.actors[winston.body]
        self.assertEqual(bubble.health, 700)
        arrow = self.release({})
        self.wait_contact(arrow, {})
        self.assertEqual(bubble.health, 575)
        self.assertEqual(self.health.total, 150)
        self.assertFalse(self.hanzo.client.messages(combat.HIT))

    def test_actual_winston_dome_stops_short_draw_arrow(self):
        winston, _ = support_tests.PulseDeflectTests().support(self.run, self.player, hero=0x02E0000000000009)
        winston.team = self.player.team
        self.move(winston, (self.x + 2.5, self.y, self.z))
        bubble = winston_tests.TracerWinstonBarrierTests().cast(self.run, winston)
        for _ in range(5):
            self.run.step({self.hanzo: (*self.aim, FIRE)})
        self.assertFalse(self.fight.rockets)
        self.run.step({self.hanzo: (*self.aim, 0)})
        arrow, = self.fight.rockets
        self.assertEqual(arrow.definition, 0x040000000000020B)
        self.assertTrue(0 < arrow.direct.amount < 125)
        self.wait_contact(arrow, {})
        self.assertAlmostEqual(bubble.health, 700 - arrow.direct.amount)
        self.assertEqual(self.health.total, 150)

    def test_actual_sonic_and_storm_arrows_hit_or_spend_dome(self):
        for definition, button in ((FULL_SONIC, 8), (SHORT_SONIC, 8), (STORM_ARROW, 16)):
            for covered in (False, True):
                with self.subTest(definition=definition, covered=covered):
                    self.setUp()
                    if definition == SHORT_SONIC:
                        self.move(self.hanzo, (self.x, self.y, self.z + 6))
                        self.aim = aim(self.fight._eye(self.hanzo), (self.x, self.y + 1, self.z))
                    dome = None
                    if covered:
                        winston, _ = support_tests.PulseDeflectTests().support(
                            self.run, self.player, hero=0x02E0000000000009,
                        )
                        winston.team = self.player.team
                        self.move(winston, (self.x + 2.5, self.y, self.z))
                        dome = winston_tests.TracerWinstonBarrierTests().cast(self.run, winston)
                    self.run.step({self.hanzo: (*self.aim, button)})
                    self.run.step({self.hanzo: (*self.aim, 0)})
                    if button == 8:
                        for _ in range(5 if definition == SHORT_SONIC else 70):
                            self.run.step({self.hanzo: (*self.aim, FIRE)})
                        self.assertFalse(self.fight.rockets)
                        self.run.step({self.hanzo: (*self.aim, 0)})
                    else:
                        for _ in range(24):
                            self.run.step({self.hanzo: (*self.aim, FIRE)})
                            if self.fight.rockets:
                                break
                    arrow, = self.fight.rockets
                    self.assertEqual(arrow.definition, definition)
                    damage = arrow.direct.amount
                    if definition == SHORT_SONIC:
                        self.assertTrue(0 < damage < 125)
                    else:
                        self.assertEqual(damage, 125 if definition == FULL_SONIC else 70)
                    self.wait_contact(arrow, {})
                    self.assertAlmostEqual(self.health.total, 150 if covered else 150 - damage)
                    if dome is not None:
                        self.assertAlmostEqual(dome.health, 700 - damage)
                    self.assertEqual(len(self.hanzo.client.messages(combat.HIT)), int(not covered))

    def test_sonic_spends_one_storm_arrow_and_cooldown_allows_fresh_volley(self):
        winston, _ = support_tests.PulseDeflectTests().support(
            self.run, self.player, hero=0x02E0000000000009,
        )
        winston.team = self.player.team
        self.move(winston, (self.x + 2.5, self.y, self.z))
        dome = winston_tests.TracerWinstonBarrierTests().cast(self.run, winston)
        instances = self.hanzo.body_script.component.instances.values()
        ability = next(i for i in instances if i.graph.index == 0x146A)
        activation = ability.state(0)
        cooldown = ability.vars[61]
        self.run.step({self.hanzo: (*self.aim, 24)})
        for _ in range(25):
            self.run.step({})
        self.assertTrue(activation.flags & activation.RUNNING)
        with patch.object(self.fight.projectiles, "fired", wraps=self.fight.projectiles.fired) as fired:
            for _ in range(140):
                self.run.step({self.hanzo: (*self.aim, FIRE)})
        self.assertEqual([call.args[3] for call in fired.call_args_list], [FULL_SONIC] + [STORM_ARROW] * 4)
        self.assertEqual(dome.health, 350)
        self.assertEqual(self.health.total, 150)
        self.assertGreater(cooldown.value(), 0)
        self.assertFalse(activation.flags & activation.RUNNING)
        self.run.step({self.hanzo: (16384, 0, 0)})
        self.run.step({self.hanzo: (16384, 0, 16)})
        self.assertFalse(activation.flags & activation.RUNNING)
        for _ in range(700):
            self.run.step({})
            if cooldown.value() == 0:
                break
        self.assertEqual(cooldown.value(), 0)
        self.assertFalse(activation.flags & activation.RUNNING)
        self.run.step({self.hanzo: (16384, 0, 16)})
        self.run.step({})
        with patch.object(self.fight.projectiles, "fired", wraps=self.fight.projectiles.fired) as fired:
            for _ in range(140):
                self.run.step({self.hanzo: (16384, 0, FIRE)})
        self.assertEqual([call.args[3] for call in fired.call_args_list], [STORM_ARROW] * 5)
        self.assertEqual(self.health.total, 150)

    def test_actual_full_bow_drops_from_its_launch_line(self):
        self.draw()
        arrow = self.release({})
        self.assertAlmostEqual(arrow.gravity, 9.8, places=5)
        origin, direction = arrow.position, arrow.direction
        for _ in range(4):
            self.run.step({})
        self.assertIn(arrow, self.fight.rockets)
        # Compare the live arrow against an undeflected straight launch line.
        linear_y = origin[1] + direction[1] * (arrow.position[2] - origin[2]) / direction[2]
        self.assertLess(arrow.position[1], linear_y - 0.01)

    def test_storm_ricochet_hits_tracer_and_respects_defenses(self):
        for gate in ("body", "lethal", "wall", "ally", "blink", "recall", "projected", "ordinary"):
            with self.subTest(gate=gate):
                self.setUp()
                self.check_storm_ricochet(gate)

    def test_combined_sonic_retains_storm_ricochet_and_defense_behavior(self):
        for gate in ("body", "lethal", "wall", "ally", "blink", "recall", "projected", "ordinary"):
            with self.subTest(gate=gate):
                self.setUp()
                self.check_storm_ricochet(gate, sonic=True)

    def check_storm_ricochet(self, gate, *, sonic=False):
        x, y, z = self.hanzo.mover.position
        triangles = box(x - 25, x + 25, y - 1, y, z - 25, z + 25)
        triangles += wall(x - 10, z + 4, x + 15, z + 4, y, y + 5)
        if gate == "wall":
            triangles += wall(x + 6, z - 5, x + 6, z + 8, y, y + 5)
        mesh = World(triangles)
        self.run.match.collision_world = mesh
        self.move(self.player, (x + 7, y, z + 1))
        if gate == "lethal":
            self.health.damage(90)
            self.fight._sync_body_health(self.player, self.health)
        if gate == "ally":
            self.player.team = self.hanzo.team
        if gate == "projected":
            zarya, _ = support_tests.PulseDeflectTests().support(
                self.run, self.player, hero=0x02E0000000000068,
            )
            zarya.team = self.player.team
            self.move(zarya, (x + 7, y, z - 3))
        for actor in self.run.players:
            actor.mover.use(world=mesh)
        for _ in range(20):
            self.run.step({})
        if gate == "projected":
            facing = aim(self.fight._eye(zarya), self.fight._eye(self.player))
            self.run.step({zarya: (*facing, 16)})
            pet = zarya_barrier.active(self.player)
            self.assertIsNotNone(pet)
        self.aim = aim(self.fight._eye(self.hanzo), (x + 4, y + 1.25, z + 4))
        buttons = (8 if sonic else 0) | (0 if gate == "ordinary" else 16)
        if buttons:
            self.run.step({self.hanzo: (*self.aim, buttons)})
            for _ in range(25 if sonic else 1):
                self.run.step({})
        if gate == "ordinary":
            self.draw()
            arrow = self.release({}, definition=FULL_SONIC if sonic else FULL_ARROW)
            self.assertEqual(arrow.bounces, 0)
            self.wait_contact(arrow, {})
            self.assertEqual(self.health.total, 150)
            return
        for _ in range(24):
            self.run.step({self.hanzo: (*self.aim, FIRE)})
            if self.fight.rockets:
                break
        arrow, = self.fight.rockets
        self.assertEqual(arrow.definition, FULL_SONIC if sonic else STORM_ARROW)
        self.assertEqual(arrow.direct.amount, 70)
        shot = arrow.shot
        for _ in range(12):
            self.run.step({})
            if arrow.direction[2] < 0 or arrow not in self.fight.rockets:
                break
        self.assertIn(arrow, self.fight.rockets)
        self.assertEqual(arrow.bounces, 0)
        self.assertGreater(arrow.direction[0], 0)
        self.assertLess(arrow.direction[2], 0)
        self.assertIs(arrow.shot, shot)
        self.assertIsNone(shot.release_at)
        self.assertFalse(self.fight.sonars)
        if gate == "blink":
            self.run.step({self.player: (0, 0, 8)})
            self.assertGreater(self.player.mover.position[2], z + 2)
        elif gate == "recall":
            self.run.step({self.player: (0, 0, 16)})
            self.assertTrue(tracer.recalling(self.player))
        for _ in range(24):
            self.run.step({})
        self.assertEqual(self.health.total, {"body": 80, "lethal": 0}.get(gate, 150))
        hits = self.hanzo.client.messages(combat.HIT)
        self.assertEqual(len(hits), int(gate in ("body", "lethal")))
        if gate == "lethal":
            self.assertEqual(self.health.killer, self.hanzo.body)
            self.assertEqual(hits[0]["+0x78"]["+0x8"], combat.KILLING)
        if gate in ("body", "lethal", "wall", "projected"):
            self.assertNotIn(arrow, self.fight.rockets)
        if gate == "projected":
            self.assertEqual(zarya_barrier.barrier_health(pet, self.health), 130)
            self.assertAlmostEqual(zarya.body_script.component.entity_var(87).value(), 14)

    def test_actual_full_draw_arrow_resolves_mesh_cover_team_and_break(self):
        for hero, asset in (
            (0x02E0000000000007, "ORPHEUS_REINHARDT_COLLISION"),
            (sigma_barrier.HERO, "ORPHEUS_SIGMA_BARRIER_MODEL"),
            (orisa_barrier.HERO, "ORPHEUS_ORISA_GEOMETRY"),
        ):
            for gate in ("mesh", "break", "ally", "wall"):
                with self.subTest(hero=hero, gate=gate):
                    if not os.environ.get(asset):
                        self.skipTest(f"requires {asset}")
                    self.setUp()
                    self.check_arrow_mesh(hero, gate)

    def check_arrow_mesh(self, hero, gate):
        owner, _ = support_tests.PulseDeflectTests().support(self.run, self.player, hero=hero)
        owner.team = self.hanzo.team if gate == "ally" else self.player.team
        first, pool, damage, held = self.deploy_arrow_mesh(owner, hero)
        opponent = self.player if gate == "ally" else self.hanzo
        ray = first(opponent, (self.x, self.y + 1.2, self.z + 40), (0, 0, -1), 40)
        self.assertIsNotNone(ray)
        crossing = self.z + 40 - ray[0]
        self.move(self.player, (self.x, self.y, crossing - 1.75))
        self.move(self.hanzo, (self.x, self.y, crossing + 10))
        self.aim = aim(self.fight._eye(self.hanzo), (self.x, self.y + 1, crossing - 1.75))
        if gate == "wall":
            x, y, z = self.x, self.y, crossing
            floor = box(x - 20, x + 20, y - 1, y, z - 20, z + 20)
            mesh = World(floor + wall(x - 5, z + 1.5, x + 5, z + 1.5, y, y + 4))
            self.run.match.collision_world = mesh
            for actor in self.run.players:
                actor.mover.use(world=mesh)
        if gate == "break":
            damage(pool() - 50)
        before = pool()
        defender_health = winston_tests.TracerWinstonBarrierTests.health(self.run, owner)
        owner_hp = defender_health.total
        self.draw(held)
        self.wait_contact(self.release(held), held)
        expected = {"mesh": before - 125, "break": 0, "ally": before, "wall": before}[gate]
        self.assertEqual(pool(), expected)
        self.assertEqual(defender_health.total, owner_hp)
        self.assertEqual(self.health.total, 25 if gate == "ally" else 150)
        self.assertEqual(len(self.hanzo.client.messages(combat.HIT)), int(gate == "ally"))
        if gate == "break":
            for _ in range(25):
                self.run.step(held)
            self.draw(held)
            self.wait_contact(self.release(held), held)
            self.assertEqual(self.health.total, 25)
            self.assertEqual(len(self.hanzo.client.messages(combat.HIT)), 1)

    def deploy_arrow_mesh(self, owner, hero):
        fight = self.fight
        if hero == orisa_barrier.HERO:
            self.move(self.hanzo, (self.x + 20, self.y, self.z + 12))
            fixture = orisa_tests.TracerOrisaBarrierTests()
            fixture.run, fixture.fight, fixture.tracer, fixture.orisa = self.run, fight, self.player, owner
            fixture.deploy()
            barrier = fixture.barrier
            self.move(owner, (self.x + 3, self.y, barrier.position[2] - 2))

            def weaken(amount):
                fight.orisa_barriers.damage(barrier, amount)

            return fight.orisa_barriers.first, lambda: barrier.health, weaken, {}

        self.move(owner, (self.x + 1.3, self.y, self.z + 0.5))
        for _ in range(30):
            self.run.step({})
        guard = {owner: (0, 0, SECONDARY)}
        for _ in range(8 if hero == sigma_barrier.HERO else 18):
            self.run.step(guard)
        if hero == sigma_barrier.HERO:
            for _ in range(27):
                self.run.step({})
            barrier, = fight.sigma_barriers.values()
            self.assertTrue(barrier.active and barrier.child_request_seen)

            def first(source, origin, direction, distance):
                return sigma_barrier.barrier_intersection(fight, source, origin, direction, distance)

            return first, lambda: barrier.vitals.total, barrier.vitals.damage, {}
        state = fight.reinhardt_barriers.states[owner.body]
        self.assertTrue(state.active)

        def weaken(amount):
            state.current -= amount

        return fight.reinhardt_barriers.first, lambda: state.current, weaken, guard

    def test_actual_full_draw_arrow_spends_projected_barrier_without_spill(self):
        for capacity in (200, 100):
            with self.subTest(capacity=capacity):
                self.setUp()
                self.check_arrow_projected_barrier(capacity)

    def check_arrow_projected_barrier(self, capacity):
        zarya, _ = support_tests.PulseDeflectTests().support(self.run, self.player, hero=0x02E0000000000068)
        zarya.team = self.player.team
        self.move(zarya, (self.x + 5, self.y, self.z))
        self.draw()
        facing = aim(self.fight._eye(zarya), self.fight._eye(self.player))
        self.run.step({self.hanzo: (*self.aim, FIRE), zarya: (*facing, 16)})
        pet = zarya_barrier.active(self.player)
        self.assertIsNotNone(pet)
        self.assertEqual(pet.instance.component.entity, zarya.body)
        self.assertEqual(zarya_barrier.barrier_health(pet, self.health), 200)
        self.health.particle_barriers[zarya_barrier.particle_barrier_key(pet)] = capacity
        self.wait_contact(self.release({}), {})
        self.assertEqual(self.health.total, 150)
        self.assertFalse(self.hanzo.client.messages(combat.HIT))
        self.assertAlmostEqual(zarya.body_script.component.entity_var(87).value(), min(capacity, 125) / 5)
        if capacity < 125:
            self.assertIsNone(zarya_barrier.active(self.player))
            self.draw()
            self.wait_contact(self.release({}), {})
            self.assertEqual(self.health.total, 25)
        else:
            self.assertEqual(zarya_barrier.barrier_health(pet, self.health), capacity - 125)

    @unittest.skipUnless(deflection.TAGS_PATH, "verified projectile tags required")
    def test_actual_arrow_is_reflected_or_absorbed(self):
        for defense, hero, button in (
            ("deflect", 0x02E0000000000029, 16),
            ("grasp", sigma_grasp.HERO, 8),
            ("matrix", dva.HERO, SECONDARY),
        ):
            with self.subTest(defense=defense):
                self.setUp()
                self.check_arrow_defense(defense, hero, button)

    def check_arrow_defense(self, defense, hero, button):
        defender, _ = support_tests.PulseDeflectTests().support(self.run, self.player, hero=hero)
        if defense == "matrix":
            assets = ("ORPHEUS_GRAPH_SUPPLEMENT", "ORPHEUS_MOVERS_SUPPLEMENT")
            if not all(os.environ.get(key) for key in assets):
                self.skipTest("requires extracted 0407 Matrix graphs and mover")
            self.assertTrue(self.run.match.switch_hero(defender, replace(heroes()[hero], body=dva.MECH)))
        defender.team = self.player.team
        self.move(defender, (self.x, self.y, self.z + 3))
        for _ in range(100 if defense == "matrix" else 30):
            self.run.step({})
        self.draw()
        facing = aim(self.fight._eye(defender), (self.x, self.y + 1, self.z + 12))
        defender_health = winston_tests.TracerWinstonBarrierTests.health(self.run, defender)
        before = defender_health.total
        self.run.step({self.hanzo: (*self.aim, FIRE), defender: (*facing, button)})
        held = {defender: (*facing, button if defense == "matrix" else 0)}
        arrow = self.release(held)
        self.wait_contact(arrow, held)
        self.assertEqual(self.health.total, 150)
        self.assertEqual(defender_health.total, before)
        self.assertFalse(self.hanzo.client.messages(combat.HIT))
        instances = defender.body_script.component.instances.values()
        if defense == "deflect":
            self.assertIs(arrow.shooter, defender)
            self.assertEqual(winston_tests.TracerWinstonBarrierTests.health(self.run, self.hanzo).total, 75)
        elif defense == "grasp":
            grasp = next(i for i in instances if i.graph.index == sigma_grasp.GRASP)
            self.assertEqual(grasp.vars[21312].value(), 125)
        else:
            matrix = next(i for i in instances if i.graph.index == 0x02CC)
            self.assertLess(matrix.vars[87].value(), 100)
