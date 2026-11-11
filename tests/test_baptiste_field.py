"""Actual E input -> ballistic deployment -> damageable field ->1763 protections.

The actor proxy/rising pose and world target policy are reconstructed. Entity
encoding is not an original-client acceptance result.
"""

import unittest
from unittest.mock import patch

import test_baptiste_combat as combat_fixture
from test_game_combat import KANEZAKA_DM, aim

from ow174.game import stats, world
from ow174.game.collision import World, flat_world
from ow174.game.commands import Command
from ow174.game.content import SOLDIER, heroes


class BaptisteFieldTests(unittest.TestCase):
    def test_model_capsule_replaces_movement_proxy_for_actual_weapon_hits(self):
        run, caster, _, enemy, fight, _, _ = self.make()
        actor = self.cast(run, caster, fight)
        target = next(item for item in fight._targets() if item.entity == actor.entity)
        self.assertAlmostEqual(target.radius, 0.46099997)
        a, b = target.segment()
        self.assertAlmostEqual(a[1] - actor.position[1], 0.18003005)
        self.assertAlmostEqual(b[1] - actor.position[1], 0.08403006)
        # Off-axis shot0.3m from center misses the old0.05 movement proxy but
        # intersects the authenticated model capsule.0.6m lies outside it.
        weapon = next(item for item in enemy.body_script.component.instances.values()
                      if item.graph.index == 0x0254)
        for lateral, damage in ((0.6, 0), (0.3, 20)):
            yaw, pitch = aim(fight._eye(enemy), (actor.position[0] + lateral,
                                               actor.position[1] + 0.13, actor.position[2]))
            before = fight.vitals[actor.entity].total
            fight._shoot(enemy, Command(run.frame, yaw=yaw, pitch=pitch), weapon.state(10),
                         0, run.frame * 16, activation=(1, 1))
            self.assertAlmostEqual(before - fight.vitals[actor.entity].total, damage)
        # Retained-pose reconstruction must preserve custom endpoint offsets.
        fight.history[actor.entity] = [(fight.tick, actor.position, actor.height, actor.height / 2)]
        self.assertEqual(fight.seen(target, fight.tick).segment(), target.segment())

    def make(self, game_map=None):
        fixture = combat_fixture.BaptisteCombatTests()
        return fixture.setup_fight() if game_map is None else fixture.setup_fight(game_map)

    def cast(self, run, caster, fight, frames=60):
        with patch.object(fight.projectiles, "fired", return_value=None):
            for frame in range(frames):
                run.step({caster: (0, 8192, 16 if frame == 0 else 0)})
        self.assertEqual(len(fight.deployables), 1)
        return next(iter(fight.deployables.values()))

    def test_e_input_deploys_actual_body_with_arming_and_lifetime(self):
        run, caster, ally, enemy, fight, targets, _ = self.make()
        with patch.object(fight.projectiles, "fired", return_value=None):
            for frame in range(10):
                run.step({caster: (0, 8192, 16 if frame == 0 else 0)})
        actor, = fight.deployables.values()
        self.assertEqual(actor.definition, 0x04000000000029A9)
        self.assertEqual(fight.vitals[actor.entity].total, 150)
        self.assertEqual(fight.baptiste.fields[actor.entity].protections, {})
        create = next(item for item in caster.client.entities
                      if item.entity == actor.entity and item.op == world.OP_CREATE)
        self.assertGreater(create.build(0).count, 0)
        for _ in range(50):
            run.step({})
        fight._damage(enemy, targets[caster], 1000, False, None, run.frame)
        fight._damage(enemy, targets[ally], 1000, False, None, run.frame)
        self.assertEqual(fight.vitals_of(targets[caster]).total, 20)
        self.assertEqual(fight.vitals_of(targets[ally]).total, 20)
        self.assertFalse(any(key[0] == enemy.body for key in fight.baptiste.field_bindings))
        for _ in range(320):
            run.step({})
        self.assertNotIn(actor.entity, fight.deployables)
        self.assertEqual(fight.baptiste.field_bindings, {})
        fight._damage(enemy, targets[caster], 100, False, None, run.frame)
        self.assertTrue(fight.is_dead(caster))

    def test_field_wall_rebounds_with_zero_and_signed_gravity(self):
        for modifier, gravity in ((0, 0), (0.5, 10), (-1, -20)):
            with self.subTest(modifier=modifier):
                run, caster, ally, enemy, fight, _, _ = self.make()
                for player in run.players:
                    player.mover.body = None
                ally.mover.position[0] = enemy.mover.position[0] = 20
                run.match.collision_world = World([
                    (-20, -10, 8, 20, -10, 8, 0, 20, 8),
                ])
                caster.body_script.component.set(9573, modifier)
                with patch.object(fight.projectiles, "fired", return_value=None):
                    for frame in range(20):
                        run.step({caster: (0, 0, 16 if frame == 0 else 0)})
                        if fight.rockets:
                            break
                    rocket, = fight.rockets
                    self.assertEqual(rocket.gravity, gravity)
                    self.assertEqual(rocket.deployment.gravity, gravity)
                    self.assertEqual(rocket.deployment.bounce_limit, 50)
                    self.assertAlmostEqual(rocket.deployment.bounce_scale, 0.1)
                    for _ in range(16):
                        run.step({})
                        if rocket.field_bounces:
                            break
                self.assertEqual(rocket.field_bounces, 1)
                self.assertEqual(fight.rockets, [rocket])
                self.assertFalse(fight.deployables)
                self.assertGreater(rocket.position[2], 7.5)
                self.assertLess(rocket.position[2], 8)
                self.assertLess(rocket.direction[2], 0)

    def test_field_wall_over_bounce_limit_retires_without_deploying(self):
        run, caster, ally, enemy, fight, _, _ = self.make()
        for player in run.players:
            player.mover.body = None
        ally.mover.position[0] = enemy.mover.position[0] = 20
        run.match.collision_world = flat_world(-10000)
        run.step({caster: (0, 0, 16)})
        rocket, = fight.rockets
        rocket.field_bounces = rocket.deployment.bounce_limit
        run.match.collision_world = World([(-20, -10, 0.5, 20, -10, 0.5, 0, 20, 0.5)])
        run.step({})
        self.assertEqual(rocket.field_bounces, 51)
        self.assertFalse(fight.rockets)
        self.assertFalse(fight.deployables)

    def test_field_auxiliary_sweep_respects_range_and_lifetime(self):
        for remaining, reach, deploys in (
            (60.0, 0.5, False),
            (60.0, 0.8, True),
            (0.0078125, 500.0, False),
            (0.013671875, 500.0, True),
        ):
            with self.subTest(remaining=remaining, reach=reach):
                run, caster, ally, enemy, fight, _, _ = self.make()
                for player in run.players:
                    player.mover.body = None
                ally.mover.position[0] = enemy.mover.position[0] = 100
                run.match.collision_world = flat_world(-10000)
                run.step({caster: (0, 8192, 16)})
                rocket, = fight.rockets
                self.assertAlmostEqual(rocket.deployment.sweep_radius, 0.15)
                rocket.position = (0.0, 1.665, 0.0)
                rocket.left, rocket.range_left = remaining, reach
                run.match.collision_world = flat_world(1.0)
                run.step({})
                self.assertFalse(fight.rockets)
                self.assertEqual(len(fight.deployables), int(deploys))
                if deploys:
                    actor, = fight.deployables.values()
                    self.assertEqual(actor.definition, 0x04000000000029A9)
                    self.assertEqual(fight.vitals[actor.entity].total, 150)

    def test_field_canister_range_expiry_does_not_deploy(self):
        run, caster, ally, enemy, fight, _, _ = self.make()
        ally.mover.position[0] = enemy.mover.position[0] = 100
        run.match.collision_world = flat_world(-10000)
        with patch.object(fight.projectiles, "fired", return_value=None):
            for frame in range(20):
                run.step({caster: (0, 0, 16 if frame == 0 else 0)})
                if fight.rockets:
                    break
        rocket, = fight.rockets
        self.assertEqual((rocket.range_left, rocket.left), (500, 60))
        fight.tick += 4000
        rocket.last_tick = fight.tick - 4000
        fight._fly(rocket)
        self.assertNotIn(rocket, fight.rockets)
        self.assertEqual(fight.deployables, {})
        self.assertEqual(fight.baptiste.fields, {})
        self.assertAlmostEqual(rocket.range_left, 0)
        self.assertGreater(rocket.left, 50)

    def test_enemy_actual_weapon_destroys_field_and_removes_protection(self):
        run, caster, _, enemy, fight, targets, _ = self.make()
        actor = self.cast(run, caster, fight)
        yaw, pitch = aim(fight._eye(enemy), (actor.position[0], actor.position[1] + 0.5, actor.position[2]))
        for _ in range(100):
            run.step({enemy: (yaw, pitch, 1)})
            if actor.entity not in fight.deployables:
                break
        self.assertNotIn(actor.entity, fight.deployables)
        self.assertEqual(fight.baptiste.field_bindings, {})
        self.assertNotIn(stats.ELIMINATIONS, enemy.stats.values)
        self.assertNotIn(stats.HERO_DAMAGE, enemy.stats.values)
        fight._damage(enemy, targets[caster], 1000, False, None, run.frame)
        self.assertTrue(fight.is_dead(caster))

    def test_allied_fire_and_healing_do_not_damage_or_repair_field(self):
        run, caster, ally, enemy, fight, _, _ = self.make()
        actor = self.cast(run, caster, fight)
        target = next(item for item in fight._targets() if item.entity == actor.entity)
        fight._damage(enemy, target, 20, False, None, run.frame)
        yaw, pitch = aim(fight._eye(ally), (actor.position[0], actor.position[1] + 0.5, actor.position[2]))
        for _ in range(30):
            run.step({ally: (yaw, pitch, 1)})
        self.assertEqual(fight.vitals[actor.entity].total, 130)
        for frame in range(80):
            run.step({caster: (0, 0, 8 if frame == 0 else 0)})
        self.assertEqual(fight.vitals[actor.entity].total, 130)

    def test_field_current_range_and_solid_los_are_rechecked_before_damage(self):
        for wall in (False, True):
            with self.subTest(wall=wall):
                run, caster, ally, enemy, fight, _, _ = self.make()
                actor = self.cast(run, caster, fight)
                if wall:
                    run.match.collision_world = World([
                        (-100, -100, 3, 100, -100, 3, 0, 100, 3),
                    ])
                else:
                    ally.mover.position = [0.0, 1.0, 20.0]
                # No scheduler refresh is needed for a stale-zone protection to
                # stop blocking damage after the body moves or LOS changes.
                current = next(item for item in fight._targets() if item.owner is ally)
                fight._damage(enemy, current, 1000, False, None, run.frame)
                self.assertTrue(fight.is_dead(ally))
                self.assertIn(actor.entity, fight.deployables)
                self.assertFalse(fight.is_dead(caster))

    def test_field_owner_death_does_not_resurrect_and_other_recipients_stay_protected(self):
        run, caster, ally, enemy, fight, targets, _ = self.make()
        actor = self.cast(run, caster, fight)
        # Outside the sphere, death is real. An existing field does not globally
        # grant the owner invulnerability or resurrect that corpse.
        caster.mover.position = [0.0, 1.0, 30.0]
        current = next(item for item in fight._targets() if item.owner is caster)
        fight._damage(enemy, current, 1000, False, None, run.frame)
        self.assertTrue(fight.is_dead(caster))
        for _ in range(30):
            run.step({})
        self.assertIn(actor.entity, fight.deployables)
        fight._damage(enemy, targets[ally], 1000, False, None, run.frame)
        self.assertEqual(fight.vitals_of(targets[ally]).total, 20)
        self.assertTrue(fight.is_dead(caster))

    def test_existing_field_survives_owner_respawn_until_its_own_expiry(self):
        run, caster, ally, enemy, fight, targets, _ = self.make()
        actor = self.cast(run, caster, fight)
        old_body = caster.body
        caster.mover.position = [0.0, 1.0, 30.0]
        current = next(item for item in fight._targets() if item.owner is caster)
        fight._damage(enemy, current, 1000, False, None, run.frame)
        for _ in range(210):
            run.step({})
        self.assertNotEqual(caster.body, old_body)
        self.assertIn(actor.entity, fight.deployables)
        fight._damage(enemy, targets[ally], 1000, False, None, run.frame)
        self.assertEqual(fight.vitals_of(targets[ally]).total, 20)
        self.assertFalse(fight.is_dead(caster))

    def test_field_switch_cleanup_and_duplicate_press_cannot_create_extra_actors(self):
        run, caster, _, _, fight, _, _ = self.make()
        actor = self.cast(run, caster, fight)
        for frame in range(60):
            run.step({caster: (0, 8192, 16 if frame % 2 == 0 else 0)})
        self.assertEqual(list(fight.deployables), [actor.entity])
        run.match.switch_hero(caster, heroes()[SOLDIER])
        run.step({})
        self.assertEqual(fight.deployables, {})
        self.assertEqual(fight.baptiste.field_bindings, {})

    def test_ffa_field_protects_only_caster(self):
        run, caster, ally, enemy, fight, targets, _ = self.make(KANEZAKA_DM)
        self.cast(run, caster, fight)
        fight._damage(enemy, targets[caster], 1000, False, None, run.frame)
        fight._damage(enemy, targets[ally], 1000, False, None, run.frame)
        self.assertEqual(fight.vitals_of(targets[caster]).total, 20)
        self.assertTrue(fight.is_dead(ally))

    def test_field_cooldown_recovery_releases_a_fresh_actor(self):
        run, caster, _, _, fight, _, _ = self.make()
        first = self.cast(run, caster, fight)
        for _ in range(1600):
            run.step({})
        self.assertEqual(fight.deployables, {})
        second = self.cast(run, caster, fight)
        self.assertGreater(second.entity, first.entity)
        self.assertNotEqual(second.entity, first.entity)

    def test_late_viewer_gets_current_field_and_precreation_view_cannot_hit_it(self):
        run, caster, ally, enemy, fight, _, _ = self.make()
        actor = self.cast(run, caster, fight)
        initial = run.match.spawn_updates(ally)
        self.assertTrue(any(item.entity == actor.entity and item.op == world.OP_CREATE for item in initial))
        rifle = next(item for item in enemy.body_script.component.instances.values()
                     if item.graph.index == 0x0254)
        state = rifle.state(10)
        yaw, pitch = aim(fight._eye(enemy), (actor.position[0], actor.position[1] + 0.5, actor.position[2]))
        # This command's view predates the actor's authoritative creation boundary.
        command = Command(actor.created_tick - 1, yaw=yaw, pitch=pitch)
        fight._shoot(enemy, command, state, 0, (actor.created_tick - 1) * 16, activation=(1, 1))
        self.assertEqual(fight.vitals[actor.entity].total, 150)


if __name__ == "__main__":
    unittest.main()
