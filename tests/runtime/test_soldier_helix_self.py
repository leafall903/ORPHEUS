"""Actual Helix input, scoped self splash and body lifetime/credit invariants.

The .5 self-scalar meaning is reconstructed; static capsules are not retail hitboxes.
"""

import math
import unittest

from test_game_combat import Run, aim

from ow174.game import combat, stats
from ow174.game.collision import World, flat_world
from ow174.game.script import nodes


class SoldierHelixSelfTests(unittest.TestCase):
    def scene(self, wall_z=.6):
        run = Run(names=("Soldier", "Other"))
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        run.match.collision_world = flat_world(0.0)
        soldier, other = run.players
        soldier.mover.position[:] = [0.0, 0.0, 0.0]
        other.mover.position[:] = [20.0, 0.0, 10.0]
        other.team = 1 - soldier.team
        for player in run.players:
            player.body_script.ult_seconds = 0
        for _ in range(20):
            run.step({})
        if wall_z is not None:
            run.match.collision_world = World([
                (-100, 0, -100, -100, 0, 200, 100, 0, 200),
                (-100, 0, -100, 100, 0, 200, 100, 0, -100),
                (-10, -10, wall_z, 10, -10, wall_z, 10, 10, wall_z),
                (-10, -10, wall_z, 10, 10, wall_z, -10, 10, wall_z),
            ])
        return run, soldier, other

    def target(self, run, owner):
        return next(t for t in run.match.combat._targets() if t.owner is owner)

    def pool(self, run, owner):
        return run.match.combat.vitals_of(self.target(run, owner))

    def launch(self, run, soldier):
        run.step({soldier: (0, 0, 2)})
        rocket, = [r for r in run.match.combat.rockets if r.shooter is soldier]
        self.assertEqual(rocket.blast.self_scale, .5)
        self.assertEqual(rocket.source_entity, soldier.body)
        return rocket

    def land(self, run, rocket):
        for _ in range(200):
            if rocket not in run.match.combat.rockets:
                return
            run.step({})
        self.fail("actual Helix did not terminate")

    def test_close_wall_self_splash_uses_authored_half_scale_and_ring_distance(self):
        for wall in (.6, 2.0, 3.0, 4.5):
            with self.subTest(wall=wall):
                run, soldier, _ = self.scene(wall)
                target = self.target(run, soldier)
                delta_y = max(0, run.match.combat._eye(soldier)[1]
                              - (target.feet[1] + target.height - target.radius))
                distance = max(0, math.hypot(wall, delta_y) - target.radius)
                expected = (40 if distance <= 1 else 40 * (1 - (distance - 1) / 4)
                            if distance <= 3 else 0)
                rocket = self.launch(run, soldier)
                self.land(run, rocket)
                pool = self.pool(run, soldier)
                self.assertAlmostEqual(pool.total, 200 - expected, places=4)
                self.assertEqual(pool.ult_healing_deficit, 0)
                self.assertFalse(soldier.client.messages(combat.HIT))
                self.assertEqual(soldier.stats.value(stats.HERO_DAMAGE), 0)
                self.assertEqual(soldier.body_script.component.entity_var(342).value(), 0)

    def test_self_damage_and_ally_exclusion_do_not_pollute_enemy_damage_or_charge(self):
        for ally in (False, True):
            with self.subTest(ally=ally):
                run, soldier, other = self.scene()
                other.mover.position[:] = [1.1, 0, .2]
                other.mover.body = None
                if ally:
                    other.team = soldier.team
                self.land(run, self.launch(run, soldier))
                self.assertEqual(self.pool(run, soldier).total, 160)
                self.assertEqual(self.pool(run, other).total, 200 if ally else 120)
                expected = 0 if ally else 80
                self.assertEqual(soldier.stats.value(stats.HERO_DAMAGE), expected)
                self.assertEqual(soldier.body_script.component.entity_var(342).value(), expected)
                self.assertEqual(len(soldier.client.messages(combat.HIT)), 0 if ally else 1)

    def test_field_heals_actual_helix_self_injury_without_generating_healing_charge(self):
        run, soldier, _ = self.scene()
        self.land(run, self.launch(run, soldier))
        self.assertEqual(self.pool(run, soldier).total, 160)
        run.step({soldier: (0, 0, 16)})
        for _ in range(200):
            run.step({})
        self.assertEqual(self.pool(run, soldier).total, 200)
        self.assertEqual(soldier.body_script.component.entity_var(342).value(), 0)

    def test_self_death_during_normal_or_visor_life_uses_suicide_and_respawn_without_credit(self):
        for visor in (False, True):
            with self.subTest(visor=visor):
                run, soldier, _ = self.scene()
                script = soldier.body_script
                if visor:
                    script.set_ult_charge(script.component.entity_var(338).value())
                    run.step({soldier: (0, 0, 32)})
                    for _ in range(80):
                        run.step({})
                    self.assertTrue(any(s.flags & nodes.Ability.RUNNING for s in script.ultimates))
                self.pool(run, soldier).damage(170)  # fixture injury, not an incoming weapon witness
                body = soldier.body
                self.land(run, self.launch(run, soldier))
                self.assertTrue(run.match.combat.is_dead(soldier))
                self.assertTrue(script.component.owner_dead)
                self.assertEqual(soldier.stats.value(stats.DEATHS), 1)
                self.assertEqual(soldier.stats.value(stats.ELIMINATIONS), 0)
                self.assertEqual(soldier.stats.value(stats.HERO_DAMAGE), 0)
                self.assertFalse(soldier.client.messages(combat.HIT))
                self.assertEqual(soldier.client.messages(combat.DEATH_NOTICE)[-1]['+0x78'], 0)
                self.assertEqual(script.component.entity_var(342).value(), 0)
                for _ in range(220):
                    run.step({})
                self.assertNotEqual(soldier.body, body)
                self.assertEqual(self.pool(run, soldier).total, 200)

    def test_source_body_replacement_retires_the_actual_predicted_rocket(self):
        run, soldier, _ = self.scene(2.0)
        rocket = self.launch(run, soldier)
        body = soldier.body
        self.assertIsNotNone(rocket.shot)
        self.assertTrue(run.match.switch_hero(soldier, soldier.hero))
        soldier.mover.position[:] = [0, 0, 0]
        soldier.mover.body = None
        for _ in range(10):
            run.step({})
        self.assertNotEqual(soldier.body, body)
        self.assertNotIn(rocket, run.match.combat.rockets)
        self.assertEqual(rocket.source_entity, body)
        self.assertEqual(self.pool(run, soldier).total, 200)

    def test_flying_helix_hits_after_actual_enemy_rifle_kills_caster_without_new_charge(self):
        run, soldier, other = self.scene(None)
        other.mover.position[:] = [0, 0, 30]
        other.mover.body = None
        self.pool(run, soldier).damage(170)  # fixture injury; incoming rifle supplies the lethal hit
        yaw, pitch = aim(run.match.combat._eye(other), run.match.combat._eye(soldier))
        run.step({soldier: (0, 0, 2), other: (yaw, pitch, 1)})
        self.assertTrue(run.match.combat.is_dead(soldier))
        rocket, = [r for r in run.match.combat.rockets if r.shooter is soldier]
        self.land(run, rocket)
        self.assertTrue(run.match.combat.is_dead(soldier))
        self.assertEqual(self.pool(run, soldier).total, 0)
        self.assertEqual(self.pool(run, other).total, 80)
        self.assertEqual(soldier.stats.value(stats.HERO_DAMAGE), 120)
        self.assertEqual(soldier.body_script.component.entity_var(342).value(), 0)

    def test_self_splash_cannot_cross_impact_wall_after_owner_reposition(self):
        run, soldier, _ = self.scene()
        rocket = self.launch(run, soldier)
        # A static pose fixture behind the impact plane, not a native teleport claim.
        soldier.mover.position[:] = [0, 0, 1.6]
        soldier.mover.body = None
        self.land(run, rocket)
        self.assertEqual(self.pool(run, soldier).total, 200)

    def test_range_expiry_does_not_explode_against_a_body_just_beyond_authored_range(self):
        run, soldier, other = self.scene(None)
        other.mover.position[:] = [0, 0, 102]
        other.mover.body = None
        rocket = self.launch(run, soldier)
        self.assertEqual(rocket.left, 2)  # authored100m at50m/s caps the5s lifetime
        self.land(run, rocket)
        self.assertAlmostEqual(rocket.flown, 2)
        self.assertEqual(self.pool(run, other).total, 200)
        self.assertEqual(self.pool(run, soldier).total, 200)
