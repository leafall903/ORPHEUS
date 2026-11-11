"""Actual Baptiste hit definitions through server combat; no client acceptance claim."""

import math
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from test_game_combat import KANEZAKA_DM, Run

from ow174.game import barrier, bots, combat, stats
from ow174.game.collision import World, flat_world
from ow174.game.commands import Command
from ow174.game.content import PRACTICE_RANGE, heroes
from ow174.game.script import graph
from ow174.game.script.driver import ULT_CHARGE, ULT_COST, BodyScript

BAPTISTE = 0x02E0000000000221


class BaptisteCombatTests(unittest.TestCase):
    def test_actual_earthshatter_wave_hits_and_control_expires(self):
        run, baptiste, ally, reinhardt, fight, targets, _ = self.setup_fight()
        run.match.switch_hero(reinhardt, heroes()[barrier.REINHARDT])
        run.match.collision_world = flat_world(1.0)
        for player, point in ((baptiste, (0, 1, 8)), (ally, (20, 1, 4)), (reinhardt, (0, 1, 0))):
            player.mover.position[:] = point
            player.mover.body = None
        script = reinhardt.body_script
        health = fight.vitals_of(targets[baptiste])
        rein_health = next(fight.vitals_of(t) for t in fight._targets() if t.owner is reinhardt)
        rein_initial = rein_health.total
        for frame in range(1, 247):
            if frame == 22:
                script.set_ult_charge(script.component.vars[ULT_COST].value())
            run.step({reinhardt: (0, 0, 32 if frame == 22 else 0),
                      baptiste: (-32768, 0, 1 if frame in (70, 220) else 0)})
            if frame == 62:
                self.assertTrue(fight.is_stunned(baptiste))
                self.assertEqual(health.total, 150)
            elif frame == 100:
                self.assertFalse(fight.shatters)
                self.assertEqual(rein_health.total, rein_initial)
            elif frame == 219:
                self.assertFalse(fight.is_stunned(baptiste))
        self.assertLess(rein_health.total, rein_initial)
        self.assertEqual(health.total, 150)

    def test_actual_fire_strike_hits_both_enemies_once(self):
        run, baptiste, front, reinhardt, fight, targets, _ = self.setup_fight()
        run.match.switch_hero(reinhardt, heroes()[barrier.REINHARDT])
        run.match.collision_world = flat_world(1.0)
        for player, distance in ((baptiste, 0), (front, 4), (reinhardt, 8)):
            player.mover.position[:] = (0.0, 1.0, float(distance))
            player.mover.body = None
        victims = [fight.vitals_of(targets[player]) for player in (front, baptiste)]
        initial = [vitals.total for vitals in victims]
        for frame in range(1, 125):
            run.step({reinhardt: (-32768, 0, 16 if frame == 22 else 0)})
        self.assertEqual([before - vitals.total for before, vitals in zip(initial, victims, strict=True)],
                         [100.0, 100.0])

    def test_authored_primary_falloff_boundaries_and_midpoint(self):
        for distance, expected in ((25, 24), (35, 15.6), (45, 7.2), (55, 7.2)):
            with self.subTest(distance=distance):
                run, shooter, ally, enemy, fight, targets, weapon = self.setup_fight()
                ally.mover.position[0] = 20
                target = targets[enemy]
                # Put the horizontal ray through the capsule's cylindrical middle.
                # Its front surface is then exactly the requested ray distance.
                eye = fight._eye(shooter)[1]
                enemy.mover.position[:] = (0.0, eye - target.height / 2, distance + target.radius)
                primary = weapon.state(6)
                definition = combat.hit_def(primary, primary.node.field("m_6396149F"))
                self.assertEqual((definition.falloff_start, definition.falloff_end), (25, 45))
                self.assertAlmostEqual(definition.falloff_floor, 0.3)
                before = fight.vitals_of(target).total
                self.fire(run, shooter, fight, weapon, 6)
                self.assertAlmostEqual(before - fight.vitals_of(target).total, expected, places=5)

    def setup_fight(self, game_map=PRACTICE_RANGE):
        run = Run(game_map, ("Baptiste", "Ally", "Enemy"))
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        shooter, ally, enemy = run.players
        run.match.switch_hero(shooter, heroes()[BAPTISTE])
        shooter.team = ally.team = 0
        enemy.team = 1
        for actor, z in ((shooter, 0), (ally, 4), (enemy, 8)):
            actor.mover.position = [0.0, 1.0, float(z)]
        fight = run.match.combat
        targets = {target.owner: target for target in fight._targets()}
        for target in targets.values():
            fight.vitals_of(target)
        weapon = next(item for item in shooter.body_script.component.instances.values()
                      if item.graph.index == 0x1932)
        return run, shooter, ally, enemy, fight, targets, weapon

    def fire(self, run, shooter, fight, weapon, state_index, pitch=0):
        state = weapon.state(state_index)
        with patch.object(fight.projectiles, "fired", return_value=None):
            fight._shoot(shooter, Command(run.frame, yaw=0, pitch=pitch), state, 0,
                         run.frame * 16, activation=(1, 1))
        # Exercise the swept projectile and impact path, rather than calling heal directly.
        for _ in range(15):
            fight.tick += 1
            for rocket in list(fight.rockets):
                fight._fly(rocket)

    def test_actual_primary_damages_enemy_and_passes_teammate(self):
        run, shooter, ally, enemy, fight, targets, weapon = self.setup_fight()
        primary = weapon.state(6)
        definition = combat.hit_def(primary, primary.node.field("m_6396149F"))
        self.assertFalse(definition.healing)
        self.assertEqual(definition.amount, 24)
        self.fire(run, shooter, fight, weapon, 6)
        self.assertEqual(fight.vitals_of(targets[enemy]).total, 176)
        self.assertEqual(fight.vitals_of(targets[ally]).total, 200)
        self.assertEqual(shooter.stats.values[stats.HERO_DAMAGE], 24)

    def test_actual_secondary_heals_living_ally_without_enemy_damage(self):
        run, shooter, ally, enemy, fight, targets, weapon = self.setup_fight()
        secondary = weapon.state(26)
        definition = combat.hit_def(secondary, secondary.node.field("m_6396149F"))
        self.assertTrue(definition.healing)
        self.assertEqual(definition.amount, 20)
        fight.vitals_of(targets[ally]).damage(90)
        fight.vitals_of(targets[enemy]).damage(90)
        self.fire(run, shooter, fight, weapon, 26)
        self.assertEqual(fight.vitals_of(targets[ally]).total, 180)
        self.assertEqual(fight.vitals_of(targets[enemy]).total, 110)
        self.assertEqual(shooter.stats.values[stats.HEALING], 70)
        self.assertNotIn(stats.HERO_DAMAGE, shooter.stats.values)
        self.assertEqual(shooter.client.messages(combat.HIT), [])

    def test_secondary_caps_effective_healing_and_does_not_heal_self(self):
        run, shooter, ally, _, fight, targets, weapon = self.setup_fight()
        fight.vitals_of(targets[ally]).damage(5)
        fight.vitals_of(targets[shooter]).damage(50)
        self.fire(run, shooter, fight, weapon, 26)
        self.assertEqual(fight.vitals_of(targets[ally]).total, 200)
        self.assertEqual(fight.vitals_of(targets[shooter]).total, 150)
        self.assertEqual(shooter.stats.values[stats.HEALING], 5)

    def test_secondary_does_not_resurrect_or_heal_ffa_opponents(self):
        for free_for_all in (False, True):
            with self.subTest(free_for_all=free_for_all):
                args = self.setup_fight(KANEZAKA_DM if free_for_all else PRACTICE_RANGE)
                run, shooter, ally, enemy, fight, targets, weapon = args
                vitals = fight.vitals_of(targets[ally])
                vitals.damage(200)
                vitals.died_at = 1.0
                fight.vitals_of(targets[enemy]).damage(80)
                self.fire(run, shooter, fight, weapon, 26)
                self.assertEqual(vitals.total, 0)
                self.assertEqual(fight.vitals_of(targets[enemy]).total, 120)
                self.assertNotIn(stats.HEALING, shooter.stats.values)

    def test_inflight_healing_rechecks_changed_affiliation(self):
        run, shooter, ally, _, fight, targets, weapon = self.setup_fight()
        fight.vitals_of(targets[ally]).damage(90)
        secondary = weapon.state(26)
        definition = combat.hit_def(secondary, secondary.node.field("m_6396149F"))
        ally.team = 1
        fight._apply_hit(shooter, targets[ally], definition, 20, False, run.frame)
        self.assertEqual(fight.vitals_of(targets[ally]).total, 110)

    def test_live_body_input_primary_and_incoming_player_fire(self):
        run, shooter, ally, enemy, fight, targets, _ = self.setup_fight()
        ally.mover.position = [20.0, 1.0, 4.0]
        # Run ordinary command -> BodyScript volley reports -> Combat, including
        # simultaneous incoming Soldier fire. This remains an offline match.
        for _ in range(22):
            run.step({shooter: (0, 0, 1), enemy: (-32768, 0, 1)})
        self.assertLess(fight.vitals_of(targets[enemy]).total, 200)
        self.assertLess(fight.vitals_of(targets[shooter]).total, 200)
        self.assertGreater(shooter.stats.values.get(stats.HERO_DAMAGE, 0), 0)

    def test_primary_cone_override_changes_later_hits_without_changing_burst_cost(self):
        run, shooter, ally, enemy, fight, targets, weapon = self.setup_fight()
        for actor in run.players:
            actor.mover.body = None
        ally.mover.position[0] = 20
        # Keep the authored lower bound at zero. The first shot precedes the first ramp increment.
        weapon.write((0, 230), 90.0)
        ammo = weapon.vars[53].value()
        run.step({shooter: (0, 0, 1)})
        self.assertEqual(fight.vitals_of(targets[enemy]).total, 176)
        for _ in range(25):
            run.step({shooter: (0, 0, 0)})
        self.assertEqual(ammo - weapon.vars[53].value(), 3)
        self.assertEqual(fight.vitals_of(targets[enemy]).total, 176)
        self.assertEqual(shooter.stats.values.get(stats.HERO_DAMAGE, 0), 24)

    def test_live_body_input_secondary_uses_healing_path(self):
        run, shooter, ally, enemy, fight, targets, _ = self.setup_fight()
        fight.vitals_of(targets[ally]).damage(150)
        with patch.object(fight.projectiles, "fired", return_value=None):
            for _ in range(40):
                run.step({shooter: (0, 0, 2)})
        self.assertGreater(fight.vitals_of(targets[ally]).total, 50)
        self.assertEqual(fight.vitals_of(targets[enemy]).total, 200)
        self.assertGreater(shooter.stats.values.get(stats.HEALING, 0), 0)

    def test_live_grenade_motion_scale_changes_flight_and_direct_heal(self):
        for modifier, offset, gravity, expected_heal in ((None, 0.01, 20, 50), (0, 0.0, 0, 70)):
            with self.subTest(modifier=modifier):
                run, shooter, ally, enemy, fight, targets, weapon = self.setup_fight()
                for actor in run.players:
                    actor.mover.body = None
                ally.mover.position[:] = (0, 2.30, 8)
                enemy.mover.position[0] = 20
                run.match.collision_world = World([
                    (-20, -10, 10, 20, -10, 10, 0, 20, 10),
                ])
                fight.vitals_of(targets[ally]).damage(100)
                if modifier is not None:
                    weapon.component.set(9573, modifier)
                with patch.object(fight.projectiles, "fired", return_value=None) as fired:
                    run.step({shooter: (0, 0, 2)})
                    rocket, = fight.rockets
                    self.assertEqual(rocket.gravity, gravity)
                    for _ in range(25):
                        # Hold the elevated fixture pose through the world sweep.
                        ally.mover.position[:] = (0, 2.30, 8)
                        run.step({shooter: (0, 0, 0)})
                fired.assert_called_once()
                direction = fired.call_args.args[6]
                self.assertAlmostEqual(weapon.vars[7396].value(), offset)
                self.assertAlmostEqual(direction[1] / direction[2], offset)
                self.assertEqual(fight.vitals_of(targets[ally]).total, 100 + expected_heal)
                self.assertEqual(shooter.stats.values.get(stats.HEALING), expected_heal)
                self.assertEqual(fight.vitals_of(targets[enemy]).total, 200)

    def test_actual_weapon_health_operations_on_bot_targets(self):
        run, shooter, ally, enemy, fight, _, weapon = self.setup_fight()
        ally.mover.position = enemy.mover.position = [20.0, 1.0, 8.0]
        bot = bots.make(PRACTICE_RANGE.map_guid)[0]
        bot.team, bot.position = shooter.team, (0.0, 1.0, 4.0)
        run.match.bots.append(bot)
        target = next(item for item in fight._targets() if item.owner is bot)
        vitals = fight.vitals_of(target)
        vitals.damage(90)
        before = vitals.total
        self.fire(run, shooter, fight, weapon, 26)
        self.assertEqual(vitals.total, before + 70)
        bot.team = 1
        self.fire(run, shooter, fight, weapon, 6)
        self.assertEqual(vitals.total, before + 70 - 24)

    def test_grenade_ground_impact_heals_nearby_ally_without_direct_hit_or_self_healing(self):
        run, shooter, ally, enemy, fight, targets, weapon = self.setup_fight()
        ally.mover.position = [1.0, 1.0, 2.5]
        for actor in (shooter, ally, enemy):
            fight.vitals_of(targets[actor]).damage(90)
        self.fire(run, shooter, fight, weapon, 26, pitch=8192)
        self.assertEqual(fight.vitals_of(targets[ally]).total, 160)
        self.assertEqual(fight.vitals_of(targets[shooter]).total, 110)
        self.assertEqual(fight.vitals_of(targets[enemy]).total, 110)
        self.assertEqual(shooter.stats.values[stats.HEALING], 50)

    def test_grenade_arc_preserves_motion_when_scheduler_skips_frames(self):
        positions = []
        for skipped in (False, True):
            run, shooter, ally, enemy, fight, _, weapon = self.setup_fight()
            ally.mover.position = enemy.mover.position = [20.0, 1.0, 30.0]
            state = weapon.state(26)
            with patch.object(fight.projectiles, "fired", return_value=None):
                fight._shoot(shooter, Command(run.frame), state, 0, run.frame * 16, activation=(1, 1))
            rocket, = fight.rockets
            origin = rocket.position
            self.assertEqual(rocket.gravity, 20)
            self.assertEqual(rocket.blast.rings, ((3.0, 1.0),))
            self.assertEqual(rocket.blast.amount, 50)
            fight.tick = run.frame + 1
            fight._fly(rocket)
            for delta in ([11] if skipped else range(2, 12)):
                fight.tick = run.frame + delta
                fight._fly(rocket)
            positions.append(rocket.position)
            steps = 11
            dt = 0.016
            elapsed = steps * dt
            forward_speed = 60 / math.hypot(1, 0.01)
            expected_y = origin[1] + forward_speed * 0.01 * elapsed - 20 * dt**2 * sum(range(1, steps + 1))
            self.assertAlmostEqual(rocket.position[1], expected_y)
            self.assertAlmostEqual(rocket.position[2], forward_speed * elapsed)
        for a, b in zip(*positions, strict=True):
            self.assertAlmostEqual(a, b)

    def test_actual_grenade_separates_path_range_from_lifetime(self):
        for expiry in ("range", "time"):
            with self.subTest(expiry=expiry):
                run, shooter, ally, enemy, fight, _, weapon = self.setup_fight()
                ally.mover.position[0] = enemy.mover.position[0] = 100
                run.match.collision_world = flat_world(-10000)
                if expiry == "time":
                    weapon.component.set(9573, 0)
                    weapon.write((0, 18033), 1)
                run.step({shooter: (0, 0, 2)})
                rocket, = fight.rockets
                self.assertEqual((rocket.range_left, rocket.left), (500, 60))
                shot = rocket.shot
                self.assertIsNotNone(shot)
                alias = 0xA9000123
                shot.created[alias] = SimpleNamespace(received=True)
                origin = rocket.position
                vx, vy, vz = (axis * rocket.speed for axis in rocket.direction)
                if expiry == "range":
                    distance = 500
                    expected = list(origin)
                    for _ in range(1000):
                        vy -= 20 * 0.016
                        step = math.hypot(vx, vy, vz) * 0.016
                        fraction = min(1.0, distance / step)
                        for axis, velocity in enumerate((vx, vy, vz)):
                            expected[axis] += velocity * 0.016 * fraction
                        distance -= step
                        if distance <= 0:
                            break
                else:
                    expected = [origin[0], origin[1], origin[2] + 60]
                fight.tick += 4000
                rocket.last_tick = fight.tick - 4000
                fight._fly(rocket)
                self.assertNotIn(rocket, fight.rockets)
                self.assertNotIn(shot, fight.projectiles.shots)
                self.assertIsNone(shot.release_at)
                updates = [update for update in shooter.client.entities if update.entity == alias]
                self.assertEqual(len(updates), 1)
                self.assertEqual(updates[0].op, combat.OP_DESTROY)
                self.assertIsNone(updates[0].ch2)
                self.assertEqual(fight.deployables, {})
                self.assertEqual(shooter.stats.values.get(stats.HEALING, 0), 0)
                for actual, value in zip(rocket.position, expected, strict=True):
                    self.assertAlmostEqual(actual, value, places=6)
                if expiry == "range":
                    self.assertAlmostEqual(rocket.range_left, 0)
                    self.assertGreater(rocket.left, 50)
                else:
                    self.assertAlmostEqual(rocket.left, 0)
                    self.assertAlmostEqual(rocket.range_left, 440)

    def test_configured_healing_ring_never_damages_enemy(self):
        _, shooter, ally, enemy, fight, targets, _ = self.setup_fight()
        for actor in (ally, enemy):
            fight.vitals_of(targets[actor]).damage(90)
        blast = combat.HitDef(20, rings=((10.0, 1.0),), healing=True)
        rocket = combat.Rocket(shooter, shooter.team, fight._eye(shooter),
                               (0.0, 0.0, 1.0), 100, 1.0, None, blast)
        fight.rockets.append(rocket)
        for _ in range(8):
            fight.tick += 1
            if rocket in fight.rockets:
                fight._fly(rocket)
        self.assertNotIn(rocket, fight.rockets)
        self.assertEqual(fight.vitals_of(targets[ally]).total, 130)
        self.assertEqual(fight.vitals_of(targets[enemy]).total, 110)

    def test_primary_miss_and_authored_maximum_range_do_not_damage(self):
        for position in ([10.0, 1.0, 8.0], [0.0, 1.0, 110.0]):
            with self.subTest(position=position):
                run, shooter, _, enemy, fight, targets, weapon = self.setup_fight()
                enemy.mover.position = position
                self.fire(run, shooter, fight, weapon, 6)
                self.assertEqual(fight.vitals_of(targets[enemy]).total, 200)

    def test_static_wall_occludes_primary_and_secondary_direct(self):
        for state in (6, 26):
            with self.subTest(state=state):
                run, shooter, ally, enemy, fight, targets, weapon = self.setup_fight()
                fight.vitals_of(targets[ally]).damage(90)
                run.match.collision_world = World([(-10, -10, 2, 10, -10, 2, 0, 10, 2)])
                self.fire(run, shooter, fight, weapon, state)
                self.assertEqual(fight.vitals_of(targets[ally]).total, 110)
                self.assertEqual(fight.vitals_of(targets[enemy]).total, 200)

    def test_regenerative_burst_actual_input_heals_self_and_ally_over_five_seconds(self):
        run, shooter, ally, enemy, fight, targets, _ = self.setup_fight()
        for actor in (shooter, ally, enemy):
            fight.vitals_of(targets[actor]).damage(180)
        for _ in range(20):
            run.step({shooter: (0, 0, 8)})
        self.assertEqual(len(fight.baptiste.regeneration), 2)
        for _ in range(350):
            run.step({})
        self.assertAlmostEqual(fight.vitals_of(targets[shooter]).total, 170)
        self.assertAlmostEqual(fight.vitals_of(targets[ally]).total, 95)
        self.assertEqual(fight.vitals_of(targets[enemy]).total, 20)
        self.assertAlmostEqual(shooter.stats.values[stats.HEALING], 225)
        self.assertEqual(fight.baptiste.regeneration, [])
        for _ in range(20):
            run.step({})
        self.assertAlmostEqual(fight.vitals_of(targets[ally]).total, 95)

    def test_regenerative_burst_range_and_world_los(self):
        for wall in (False, True):
            with self.subTest(wall=wall):
                run, shooter, ally, _, fight, targets, _ = self.setup_fight()
                fight.vitals_of(targets[ally]).damage(180)
                if wall:
                    run.match.collision_world = World([(-10, -10, 2, 10, -10, 2, 0, 10, 2)])
                else:
                    ally.mover.position = [0.0, 1.0, 11.0]
                # Consume actual BodyScript commands without movement integration,
                # preserving the explicit ray-test wall and target positions.
                for _ in range(20):
                    shooter.take_commands([Command(shooter.body_script.frame + 1, buttons=8)])
                self.assertTrue(fight.baptiste.regeneration)
                self.assertTrue(all(item.recipient is shooter for item in fight.baptiste.regeneration))
                fight.baptiste.update(shooter.body_script.frame + 400)
                self.assertEqual(fight.vitals_of(targets[ally]).total, 20)

    def test_regenerative_burst_repeated_press_cannot_duplicate_healing_during_cooldown(self):
        run, shooter, ally, _, fight, targets, _ = self.setup_fight()
        for actor in (shooter, ally):
            fight.vitals_of(targets[actor]).damage(180)
        for frame in range(370):
            run.step({shooter: (0, 0, 8 if frame % 10 == 0 else 0)})
        self.assertAlmostEqual(fight.vitals_of(targets[ally]).total, 95)
        self.assertAlmostEqual(fight.vitals_of(targets[shooter]).total, 170)
        self.assertAlmostEqual(shooter.stats.values[stats.HEALING], 225)

    def test_regeneration_stops_for_dead_recipient_and_retired_body(self):
        run, shooter, ally, enemy, fight, targets, _ = self.setup_fight()
        fight.vitals_of(targets[ally]).damage(180)
        for _ in range(20):
            run.step({shooter: (0, 0, 8)})
        vitals = fight.vitals_of(targets[ally])
        fight._damage(enemy, targets[ally], 200, False, None, run.frame)
        self.assertTrue(vitals.dead)
        run.step({})
        self.assertEqual(vitals.total, 0)
        self.assertFalse(any(item.recipient is ally for item in fight.baptiste.regeneration))
        run.match.switch_hero(shooter, heroes()[BAPTISTE])
        self.assertEqual(fight.baptiste.regeneration, [])

    def test_quick_melee_actual_input_hits_enemy_once_and_excludes_ally(self):
        run, shooter, ally, enemy, fight, targets, _ = self.setup_fight()
        ally.mover.position = enemy.mover.position = [0.0, 1.0, 1.5]
        for frame in range(40):
            run.step({shooter: (0, 0, 0x800 if frame == 0 else 0)})
        self.assertEqual(fight.vitals_of(targets[enemy]).total, 170)
        self.assertEqual(fight.vitals_of(targets[ally]).total, 200)

    def test_quick_melee_miss_and_solid_wall_do_not_damage(self):
        for wall in (False, True):
            with self.subTest(wall=wall):
                run, shooter, _, enemy, fight, targets, _ = self.setup_fight()
                enemy.mover.position = [0.0, 1.0, 1.5 if wall else 8.0]
                if wall:
                    run.match.collision_world = World([(-10, -10, 0.75, 10, -10, 0.75, 0, 10, 0.75)])
                shooter.take_commands([Command(run.frame + 1, buttons=0x800)])
                self.assertEqual(fight.vitals_of(targets[enemy]).total, 200)

    def test_actual_immortality_protection_state_clamps_damage_and_retires(self):
        run, shooter, _, enemy, fight, targets, _ = self.setup_fight()
        script = enemy.body_script
        protection = script.component.play_loaded_graph(graph.graph(0x1763), source=shooter.body,
                                                       instigator=shooter.body, policy=1)
        script.component.run_now()
        self.assertTrue(protection.states[0].active)
        self.assertEqual(script.component.vars[19452].value(), 20)
        fight._damage(shooter, targets[enemy], 1000, False, None, run.frame)
        self.assertEqual(fight.vitals_of(targets[enemy]).total, 20)
        self.assertFalse(fight.is_dead(enemy))
        fight._damage(shooter, targets[enemy], 1000, False, None, run.frame)
        self.assertEqual(fight.vitals_of(targets[enemy]).total, 20)
        self.assertEqual(shooter.stats.values[stats.HERO_DAMAGE], 180)
        script.component.destroy(protection)
        fight._damage(shooter, targets[enemy], 20, False, None, run.frame)
        self.assertTrue(fight.is_dead(enemy))

    def test_protection_preserves_normal_shield_armour_order_before_health_floor(self):
        run, shooter, _, enemy, fight, targets, _ = self.setup_fight()
        vitals = combat.Vitals(enemy.body, 200, armour=50, shields=50)
        fight.vitals[enemy.body] = vitals
        fight._sync_body_health(enemy, vitals)
        protection = enemy.body_script.component.play_loaded_graph(
            graph.graph(0x1763), source=shooter.body, instigator=shooter.body, policy=1,
        )
        enemy.body_script.component.run_now()
        self.assertEqual(enemy.body_script.component.vars[19452].value(), 30)
        fight._damage(shooter, targets[enemy], 25, False, None, run.frame)
        self.assertEqual((vitals.now[2], vitals.now[1], vitals.now[0]), (25, 50, 200))
        fight._damage(shooter, targets[enemy], 1000, False, None, run.frame)
        self.assertEqual((vitals.now[2], vitals.now[1], vitals.now[0]), (0, 0, 30))
        enemy.body_script.component.destroy(protection)

    def test_actual_protection_entry_top_up_is_conditional_and_cannot_resurrect(self):
        run, shooter, _, enemy, fight, targets, _ = self.setup_fight()
        vitals = fight.vitals_of(targets[enemy])
        vitals.damage(195)
        fight._sync_body_health(enemy, vitals)
        component = enemy.body_script.component
        component.set(8202, True)  # Actual1763 condition explicitly gates its ModifyHealth State.
        protection = component.play_loaded_graph(graph.graph(0x1763), source=shooter.body,
                                                instigator=shooter.body, policy=1)
        component.run_now()
        fight.baptiste.update(run.frame)
        self.assertEqual(vitals.total, 5)
        component.set(8202, False)
        component.run_now()
        fight.baptiste.update(run.frame + 1)
        self.assertEqual(vitals.total, 20)
        component.destroy(protection)
        fight._damage(shooter, targets[enemy], 1000, False, None, run.frame)
        self.assertTrue(vitals.dead)
        fight.baptiste.update(run.frame + 2)
        self.assertEqual(vitals.total, 0)

    def test_boots_actual_crouch_input_drives_authored_charge_mod_and_jump_height(self):
        outcomes = []
        for charging_frames in (0, 10, 80):
            run, player, _, _, _, _, _ = self.setup_fight()
            boots = next(item for item in player.body_script.component.instances.values()
                         if item.graph.index == 0x1983)
            for _ in range(charging_frames):
                run.step({player: (0, 0, 0x80)})
            if charging_frames == 80:
                self.assertEqual(boots.vars[154].value(), 1)
                self.assertEqual(boots.states[8].payload(), {"value": 64})
            run.step({player: (0, 0, 0x40)})
            initial_speed = player.mover.velocity[1]
            peak = player.mover.position[1]
            for _ in range(140):
                run.step({})
                peak = max(peak, player.mover.position[1])
            outcomes.append((initial_speed, peak))
        self.assertLess(outcomes[0][0], outcomes[1][0])
        self.assertLess(outcomes[1][0], outcomes[2][0])
        self.assertGreater(outcomes[2][0], outcomes[0][0] + 10)
        self.assertGreater(outcomes[2][1], 9.5)
        self.assertLess(outcomes[0][1], 2.5)

    def test_boots_release_expiry_and_authored_ability_gate_remove_boost(self):
        for blocked in (False, True):
            with self.subTest(blocked=blocked):
                run, player, _, _, _, _, _ = self.setup_fight()
                for _ in range(80):
                    run.step({player: (0, 0, 0x80)})
                if blocked:
                    player.body_script.component.set(5995, True)  # Actual1983/node45 gate input.
                for _ in range(3 if blocked else 70):
                    run.step({})
                run.step({player: (0, 0, 0x40)})
                self.assertLess(player.mover.velocity[1], 6)

    def test_boots_body_retirement_and_death_cannot_preserve_an_executable_jump(self):
        run, player, _, enemy, fight, targets, _ = self.setup_fight()
        for _ in range(80):
            run.step({player: (0, 0, 0x80)})
        fight._damage(enemy, targets[player], 10000, False, None, run.frame)
        y = player.mover.position[1]
        for _ in range(20):
            run.step({player: (0, 0, 0xC0)})
        self.assertLessEqual(player.mover.position[1], y + 0.1)
        old_body = player.body
        run.match.switch_hero(player, heroes()[BAPTISTE])
        self.assertNotEqual(player.body, old_body)
        player.mover.position = [0.0, 1.0, 0.0]
        run.step({player: (0, 0, 0x40)})
        self.assertLess(player.mover.velocity[1], 6)

    def test_boots_boosted_jump_does_not_pass_through_static_ceiling(self):
        run, player, _, _, _, _, _ = self.setup_fight()
        room = World([
            (-100, 1, -100, -100, 1, 100, 100, 1, 100),
            (-100, 1, -100, 100, 1, 100, 100, 1, -100),
            (-100, 4, -100, 100, 4, 100, -100, 4, 100),
            (-100, 4, -100, 100, 4, -100, 100, 4, 100),
        ])
        run.match.collision_world = room
        player.mover.use(world=room)
        for _ in range(80):
            run.step({player: (0, 0, 0x80)})
        run.step({player: (0, 0, 0x40)})
        peak = player.mover.position[1]
        for _ in range(80):
            run.step({})
            peak = max(peak, player.mover.position[1])
        self.assertLess(peak, 3)

    def test_applied_regeneration_outlives_caster_death_and_respawn(self):
        run, caster, ally, enemy, fight, targets, _ = self.setup_fight()
        for actor in (caster, ally):
            fight.vitals_of(targets[actor]).damage(180)
        for _ in range(20):
            run.step({caster: (0, 0, 8)})
        old_body = caster.body
        fight._damage(enemy, targets[caster], 1000, False, None, run.frame)
        for _ in range(350):
            run.step({})
        self.assertNotEqual(caster.body, old_body)
        self.assertAlmostEqual(fight.vitals_of(targets[ally]).total, 95)
        self.assertEqual(fight.baptiste.regeneration, [])


class BaptisteStateTests(unittest.TestCase):
    """Executed control/resource evidence; this does not prove missing world effects."""

    def make_body(self):
        return BodyScript(BAPTISTE, 0xA0000101, 1000, ult_seconds=0)

    def instance(self, body, graph):
        return next(item for item in body.component.instances.values() if item.graph.index == graph)

    def advance(self, body, count, buttons=0):
        shots = []
        for _ in range(count):
            body.command(body.frame + 1, buttons)
            shots.extend((state.instance.graph.index, state.index, time)
                         for state, _, time, _ in body.component.shots)
            body.component.shots.clear()
        return shots

    def test_primary_burst_spacing_and_ammunition_exhaustion_reload(self):
        body = self.make_body()
        weapon = self.instance(body, 0x1932)
        shots = self.advance(body, 550, 1)
        self.assertEqual(len(shots), 45)
        self.assertTrue(all((graph, state) == (0x1932, 6) for graph, state, _ in shots))
        self.assertEqual([time - shots[0][2] for _, _, time in shots[:3]], [0, 100, 200])
        self.assertEqual(weapon.vars[53].value(), 0)
        self.advance(body, 1, 0x400)
        self.advance(body, 120)
        self.assertEqual(weapon.vars[53].value(), 45)
        self.assertEqual(len(self.advance(body, 15, 1)), 3)

    def test_secondary_cooldown_rejects_early_click_and_recovers(self):
        body = self.make_body()
        weapon = self.instance(body, 0x1932)
        self.assertEqual(len(self.advance(body, 1, 2)), 1)
        self.assertEqual(weapon.vars[18036].value(), 9)
        self.advance(body, 1)
        self.assertEqual(self.advance(body, 1, 2), [])
        self.assertEqual(weapon.vars[18036].value(), 9)
        self.advance(body, 65)
        self.assertEqual(len(self.advance(body, 1, 2)), 1)
        self.assertEqual(weapon.vars[18036].value(), 8)

    def test_regenerative_burst_cooldown_rejects_repeated_press_and_expires(self):
        body = self.make_body()
        burst = self.instance(body, 0x19A5)
        self.advance(body, 20, 8)
        tracker = burst.states[4]
        first = tracker.counter
        self.assertGreater(first, 0)
        self.assertGreater(burst.vars[61].value(), 12)
        self.advance(body, 1)
        self.advance(body, 1, 8)
        self.assertEqual(tracker.counter, first)
        self.advance(body, 850)
        self.assertEqual(burst.vars[61].value(), 0)
        self.advance(body, 20, 8)
        self.assertGreater(tracker.counter, first)

    def test_immortality_throw_cooldown_rejects_repeat_then_recovers(self):
        body = self.make_body()
        field = self.instance(body, 0x1761)
        self.assertEqual(len(self.advance(body, 1, 16)), 1)
        self.advance(body, 45)
        self.assertGreater(field.vars[61].value(), 24)
        self.assertEqual(self.advance(body, 1, 16), [])
        self.advance(body, 1600)
        self.assertEqual(field.vars[61].value(), 0)
        self.assertEqual(len(self.advance(body, 1, 16)), 1)

    def test_ultimate_activation_requires_effective_authored_cost(self):
        body = self.make_body()
        matrix = self.instance(body, 0x1902)
        self.advance(body, 1, 32)
        self.assertEqual(matrix.states[5].flags & 1, 0)
        self.advance(body, 1)
        cost = body.component.vars[ULT_COST].value()
        self.assertEqual(cost, 2310)
        body.component.set(ULT_CHARGE, cost - 1)
        self.advance(body, 1, 32)
        self.assertEqual(matrix.states[5].flags & 1, 0)
        self.advance(body, 1)
        body.component.set(ULT_CHARGE, cost)
        self.advance(body, 1, 32)
        self.assertEqual(matrix.states[5].flags & 1, 1)

    def test_melee_cooldown_rejects_early_press_and_recovers(self):
        body = self.make_body()
        melee = self.instance(body, 0x0043)
        self.advance(body, 1, 0x800)
        first = melee.states[4].counter
        self.assertGreater(first, 0)
        self.assertGreater(melee.vars[61].value(), 0.9)
        self.advance(body, 1)
        self.advance(body, 1, 0x800)
        self.assertEqual(melee.states[4].counter, first)
        self.advance(body, 70)
        self.assertEqual(melee.vars[61].value(), 0)
        self.advance(body, 1, 0x800)
        self.assertGreater(melee.states[4].counter, first)


if __name__ == "__main__":
    unittest.main()
