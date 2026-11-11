"""Command-driven Halt engine reconstruction; no native physics equivalence claim."""

import unittest
from unittest.mock import patch

import test_orisa_combat as fixture

from ow174.game import orisa
from ow174.game.collision import World
from ow174.game.combat import HIT
from ow174.game.content import find_hero
from ow174.game.mover import Mover, mover_data
from ow174.game.props import PlacedDynamicSphere
from ow174.game.script import expr


class HaltTests(unittest.TestCase):
    def arena(self):
        return fixture.OrisaCombatTests().arena()

    def detonate(self, run, caster):
        for index in range(26):
            run.step({caster: (0, 0, 2 if index in (0, 25) else 0)})
        self.assertEqual(len(run.match.combat.orisa.orbs), 1)
        orb, = run.match.combat.orisa.orbs.values()
        self.assertEqual(orb.requested, run.match.tick)
        return orb

    def place(self, run, player, position):
        player.mover = Mover(position, world=run.match.collision_world, data=mover_data(player.hero.body))

    def wall(self, run, caster, *, side=False, doorway=False):
        x, y, z = caster.mover.position
        triangles = [(x - 30, y, z - 30, x - 30, y, z + 30, x + 30, y, z + 30),
                     (x - 30, y, z - 30, x + 30, y, z + 30, x + 30, y, z - 30)]
        if side:
            triangles += [(x + 1.5, y, z + 2, x + 1.5, y + 5, z + 2, x + 1.5, y + 5, z + 15),
                          (x + 1.5, y, z + 2, x + 1.5, y + 5, z + 15, x + 1.5, y, z + 15)]
        else:
            spans = ((-4, -0.2), (0.2, 4)) if doorway else ((-4, 4),)
            for low, high in spans:
                triangles += [(x + low, y, z + 6, x + high, y, z + 6, x + high, y + 5, z + 6),
                              (x + low, y, z + 6, x + high, y + 5, z + 6, x + low, y + 5, z + 6)]
        # Character contacts use oriented faces; this fixture wall has two sides.
        triangles += [face[:3] + face[6:] + face[3:6] for face in triangles[2:]]
        run.match.collision_world = World(triangles)

    def test_authored_explosion_wait_runs_with_or_without_targets_and_retires_with_orb(self):
        for case in ("enemy", "empty", "retired"):
            with self.subTest(case=case):
                run, caster, enemy = self.arena()
                if case == "empty":
                    x, y, z = caster.mover.position
                    self.place(run, enemy, (x + 15, y, z + 7.6))
                manager = run.match.combat.orisa
                requests = []
                with patch.object(run.match.collision_world, "dispatch_physics_explosion",
                                  requests.append, create=True):
                    orb = self.detonate(run, caster)
                    run.step({})
                    instance = orb.query.instance
                    wait = instance.node_state(instance.graph.node(19))
                    physics = instance.node_state(instance.graph.node(11))
                    self.assertTrue(wait.active)
                    self.assertEqual(wait.ms, 400)
                    self.assertFalse(physics.active)
                    self.assertEqual(manager.results[-1][1], () if case == "empty" else (enemy.body,))
                    while run.frame < orb.requested + 24:
                        run.step({})
                        self.assertFalse(physics.active)
                        self.assertEqual(physics.counter, 0)
                        self.assertFalse(requests)
                    if case == "retired":
                        self.assertTrue(run.match.switch_hero(caster, find_hero("Soldier: 76")))
                    for _ in range(20):
                        run.step({})
                    self.assertNotIn(orb.entity, manager.orbs)
                    self.assertTrue(instance.stopped)
                    self.assertFalse(physics.active)
                    self.assertEqual(physics.counter, 0 if case == "retired" else 1)
                    if case == "retired":
                        self.assertFalse(requests)
                    else:
                        self.assertEqual(physics.start - wait.start, 400)
                        request, = requests
                        self.assertEqual((request.entity, request.start_ms), (orb.entity, physics.start))
                        self.assertEqual(tuple(request.position), tuple(orb.query.origin))
                        self.assertEqual((request.strength, request.radius, request.parameter, request.interval),
                                         (-50, 7, 100, 0.5))

    def test_halt_impulses_are_written_only_to_eligible_bodies_in_its_match(self):
        run, caster, _ = self.arena()
        other, _, _ = self.arena()
        other.match.collision_world = run.match.collision_world
        orb = self.detonate(run, caster)
        run.step({})
        x, y, z = orb.query.origin
        near = PlacedDynamicSphere(1, 0x0400000000001567, 0, (x + 3, y, z), 0.01)
        outside = PlacedDynamicSphere(2, near.definition, 0, (x + 7.02, y, z), 0.01)
        below = PlacedDynamicSphere(3, near.definition, 0, near.centre, near.radius)
        scene = run.match.dynamic_spheres
        received = scene.register(near, inverse_mass=1)
        missed = scene.register(outside, inverse_mass=1)
        suppressed = scene.register(below, inverse_mass=0.25)
        inside = scene.register(PlacedDynamicSphere(4, near.definition, 0, (x, y + 0.001, z), 0.01),
                                inverse_mass=1)
        coincident = scene.register(PlacedDynamicSphere(5, near.definition, 0, (x, y, z), 0.01),
                                    inverse_mass=1)
        isolated = other.match.dynamic_spheres.register(near, inverse_mass=1)
        self.assertIs(other.match.collision_world, run.match.collision_world)
        received.velocity = isolated.velocity = (0.25, 0, 0)

        while run.frame < orb.requested + 24:
            run.step({})
        self.assertEqual(received.velocity, (0.25, 0, 0))
        for _ in range(20):
            run.step({})
        self.assertAlmostEqual(received.velocity[0], 0.25 - 0.15432097, places=6)
        self.assertEqual(received.velocity[1:], (0, 0))
        self.assertEqual(missed.velocity, (0, 0, 0))
        self.assertEqual(suppressed.velocity, (0, 0, 0))
        self.assertAlmostEqual(inside.velocity[1], -0.15432097, places=6)
        self.assertEqual((inside.velocity[0], inside.velocity[2]), (0, 0))
        self.assertAlmostEqual(coincident.velocity[0], -0.15432097, places=6)
        self.assertEqual(coincident.velocity[1:], (0, 0))
        self.assertEqual(isolated.velocity, (0.25, 0, 0))

    def test_second_press_routes_real_message_freezes_then_pulls_enemy_without_damage(self):
        run, caster, enemy = self.arena()
        orb = self.detonate(run, caster)
        fight = run.match.combat
        original = tuple(enemy.mover.position)
        centre = orb.rocket.position
        for _ in range(24):
            run.step({})
        self.assertEqual(tuple(enemy.mover.position), original)
        self.assertEqual(orb.rocket.position, centre)
        for _ in range(8):
            run.step({})
        self.assertGreater(enemy.mover.position[2], original[2] + 1)
        self.assertEqual(fight.orisa.results[-1], (orb.entity, (enemy.body,)))
        self.assertFalse(fight.orisa.orbs)
        target = next(t for t in fight._targets() if t.owner is enemy)
        self.assertEqual(fight.vitals_of(target).total, 200)
        self.assertGreater(caster.body_script.component.instances[5].state(2).remaining(), 5)

    def test_prearming_press_is_ignored_and_does_not_duplicate_orb(self):
        run, caster, _ = self.arena()
        for index in range(50):
            run.step({caster: (0, 0, 2 if index in (0, 15) else 0)})
        orb, = run.match.combat.orisa.orbs.values()
        self.assertIsNone(orb.requested)
        self.assertEqual(caster.body_script.component.instances[5].state(4).counter, 1)

    def test_expired_early_press_does_not_recast_when_orb_retires(self):
        run, caster, _ = self.arena()
        self.detonate(run, caster)
        for _ in range(100):
            run.step({})
        self.assertFalse(run.match.combat.orisa.orbs)
        self.assertEqual(caster.body_script.component.instances[5].state(4).counter, 1)

    def test_cooldown_rejects_repeated_presses_then_recovers(self):
        run, caster, _ = self.arena()
        self.detonate(run, caster)
        for _ in range(40):
            run.step({})
        instance = caster.body_script.component.instances[5]
        for index in range(120):
            run.step({caster: (0, 0, 2 if index % 3 == 0 else 0)})
        self.assertEqual(instance.state(4).counter, 1)
        self.assertFalse(run.match.combat.orisa.orbs)
        self.assertGreater(instance.state(2).remaining(), 3)
        for _ in range(300):
            run.step({})
        self.assertEqual(instance.state(2).remaining(), 0)
        run.step({caster: (0, 0, 2)})
        for _ in range(15):
            run.step({})
        self.assertEqual(instance.state(4).counter, 2)

    def test_foreign_body_cannot_detonate_or_retarget_orb(self):
        run, caster, enemy = self.arena()
        for index in range(25):
            run.step({caster: (0, 0, 2 if index == 0 else 0)})
        orb, = run.match.combat.orisa.orbs.values()
        self.assertFalse(run.match.combat.orisa.receive(orb.entity, orisa.DETONATE, enemy.body))
        self.assertIsNone(orb.requested)
        self.assertFalse(run.match.combat.orisa.receive(orb.entity, orisa.DETONATE + 1, caster.body))

    def test_fresh_press_in_authored_early_window_still_starts_after_cooldown(self):
        run, caster, _ = self.arena()
        self.detonate(run, caster)
        instance = caster.body_script.component.instances[5]
        ability = instance.state(2)
        for _ in range(40):
            run.step({})
        window = ability.evaluate(ability.node.field("m_246438AD"), watch=False)
        self.assertGreater(window, 0)
        for _ in range(500):
            if 0 < ability.remaining() < window / 2:
                break
            run.step({})
        self.assertGreater(ability.remaining(), 0)
        run.step({caster: (0, 0, 2)})
        self.assertEqual(instance.state(4).counter, 1)
        for _ in range(25):
            run.step({})
        self.assertEqual(instance.state(4).counter, 2)

    def test_allies_and_caster_are_not_pulled(self):
        run, caster, ally = self.arena()
        run.match.game_map = run.match.game_map.__class__(**{**run.match.game_map.__dict__,
                                                          "free_for_all": False})
        ally.team = caster.team
        original = tuple(ally.mover.position)
        self.detonate(run, caster)
        for _ in range(40):
            run.step({})
        self.assertEqual(tuple(ally.mover.position), original)
        self.assertEqual(run.match.combat.orisa.results[-1][1], ())

    def test_out_of_radius_enemy_is_not_pulled(self):
        run, caster, enemy = self.arena()
        x, y, z = caster.mover.position
        self.place(run, enemy, (x + 10, y, z + 8))
        original = tuple(enemy.mover.position)
        self.detonate(run, caster)
        for _ in range(40):
            run.step({})
        self.assertEqual(tuple(enemy.mover.position), original)
        self.assertEqual(run.match.combat.orisa.results[-1][1], ())

    def test_fortify_prevents_halt_acquisition(self):
        run, caster, enemy = self.arena()
        run.match.switch_hero(enemy, find_hero("Orisa"))
        x, y, z = caster.mover.position
        self.place(run, enemy, (x, y, z + 5))
        original = tuple(enemy.mover.position)
        for index in range(66):
            run.step({caster: (0, 0, 2 if index in (0, 25) else 0),
                      enemy: (0, 0, 8 if index == 0 else 0)})
        self.assertEqual(tuple(enemy.mover.position), original)
        self.assertEqual(run.match.combat.orisa.results[-1][1], ())

    def test_fortify_after_acquisition_cancels_remaining_pull(self):
        run, caster, enemy = self.arena()
        run.match.switch_hero(enemy, find_hero("Orisa"))
        x, y, z = caster.mover.position
        self.place(run, enemy, (x, y, z + 5))
        self.detonate(run, caster)
        for _ in range(25):
            run.step({})
        self.assertIn(enemy.body, run.match.combat.orisa.pulls)
        run.step({enemy: (0, 0, 8)})
        at = enemy.mover.position[2]
        for _ in range(5):
            run.step({})
        self.assertNotIn(enemy.body, run.match.combat.orisa.pulls)
        self.assertAlmostEqual(enemy.mover.position[2], at)

    def test_static_wall_blocks_halt_line_of_sight(self):
        run, caster, enemy = self.arena()
        self.wall(run, caster, side=True)
        x, y, z = caster.mover.position
        self.place(run, enemy, (x + 3, y, z + 6))
        original = tuple(enemy.mover.position)
        self.detonate(run, caster)
        for _ in range(40):
            run.step({})
        self.assertEqual(tuple(enemy.mover.position), original)
        self.assertEqual(run.match.combat.orisa.results[-1][1], ())

    def test_capsule_pull_is_blocked_by_narrow_visible_doorway(self):
        run, caster, enemy = self.arena()
        self.wall(run, caster, doorway=True)
        x, _, z = caster.mover.position
        self.detonate(run, caster)
        for _ in range(40):
            run.step({})
        self.assertIn(enemy.body, run.match.combat.orisa.results[-1][1])
        self.assertLess(enemy.mover.position[2], z + 6)
        self.assertAlmostEqual(enemy.mover.position[0], x, places=2)

    def test_death_and_body_replacement_retire_control(self):
        run, caster, enemy = self.arena()
        self.detonate(run, caster)
        for _ in range(25):
            run.step({})
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is enemy)
        fight._damage(caster, target, 1000, False, None, run.frame)
        run.step({})
        self.assertNotIn(enemy.body, fight.orisa.pulls)
        old = enemy.body
        run.match.switch_hero(enemy, find_hero("Orisa"))
        self.assertIsNone(fight.orisa.movement(enemy))
        self.assertNotIn(old, fight.orisa.pulls)

    def test_owned_handle_and_position_retire_with_source_body(self):
        run, caster, _ = self.arena()
        orb = self.detonate(run, caster)
        config = {"$": "STU_E72F6348", "m_entity": {"$": "STUConfigVarDynamic",
                  "m_identifier": 5875, "m_60DB8F99": 0}}
        instance = caster.body_script.component.instances[5]
        self.assertTrue(instance.evaluate(config))
        self.assertEqual(instance.vars[5875].value(), expr.Entity(orb.entity))
        self.assertEqual(tuple(run.match.script_body_position(orb.entity, caster.body)), orb.rocket.position)
        run.match.switch_hero(caster, find_hero("Soldier"))
        self.assertIsNone(run.match.script_body_position(orb.entity, caster.body))
        self.assertFalse(run.match.combat.orisa.orbs)

    def test_late_world_tick_does_not_start_a_fresh_expired_pull(self):
        run, caster, enemy = self.arena()
        self.detonate(run, caster)
        original = tuple(enemy.mover.position)
        at = run.frame + 1000
        run.match.update(at * 0.016, at)
        self.assertFalse(run.match.combat.orisa.orbs)
        self.assertFalse(run.match.combat.orisa.pulls)
        self.assertEqual(tuple(enemy.mover.position), original)

    def test_pulled_enemy_can_still_fire_and_damage_orisa_through_real_commands(self):
        run, caster, enemy = self.arena()
        self.detonate(run, caster)
        for _ in range(24):
            run.step({})
        for _ in range(24):
            run.step({enemy: (-32768, 0, 1)})
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is caster)
        self.assertIn(enemy.body, fight.orisa.pulls)
        self.assertGreater(fight.vitals_of(target).total, 0)
        self.assertLess(fight.vitals_of(target).total, 450)
        self.assertTrue(enemy.client.messages(HIT))


if __name__ == "__main__":
    unittest.main()
