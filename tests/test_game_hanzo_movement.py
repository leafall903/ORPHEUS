"""Real command-path Lunge with an explicitly reconstructed mover bridge."""

import math
import unittest
from dataclasses import replace
from unittest.mock import patch

import test_game_hanzo as hero_tests
from test_game_combat import aim
from test_game_mover import box, wall

from ow174.game import bodytrace, combat, hanzo_control, hanzo_movement, movelog, projectiles
from ow174.game.collision import World
from ow174.game.commands import JUMP, Command
from ow174.game.content import SOLDIER, heroes
from ow174.game.script import runtime


class HanzoLungeTests(unittest.TestCase):
    def setUp(self):
        for module in (bodytrace, movelog):
            self.enterContext(patch.object(module, "ENABLED", False))

    def arena(self):
        run, hero, victim = hero_tests.HanzoCombatTests().arena()
        instance = next(i for i in hero.body_script.component.instances.values()
                        if i.graph.index == 0x010C)
        return run, hero, victim, instance

    @staticmethod
    def step(run, hero, **values):
        run.frame += 1
        run.match.update(run.frame * 0.016, run.frame)
        for player in run.players:
            player.take_commands([Command(run.frame, **(values if player is hero else {}))])

    def arm(self, run, hero):
        self.step(run, hero, buttons=JUMP)
        for _ in range(14):
            self.step(run, hero)

    def test_authored_direction_speed_and_actual_displacement(self):
        cases = ((0, 0, 0, (0, 15)), (16384, 0, 0, (15, 0)),
                 (0, 0, 127, (-15, 0)), (0, -127, 0, (0, -15)),
                 (0, 127, 127, (-15 / math.sqrt(2), 15 / math.sqrt(2))))
        for yaw, forward, right, expected in cases:
            with self.subTest(yaw=yaw, forward=forward, right=right):
                run, hero, _, instance = self.arena()
                self.arm(run, hero)
                before = tuple(hero.mover.position)
                self.step(run, hero, buttons=JUMP, yaw=yaw, pitch=-15000,
                          forward=forward, right=right)
                vx, vy, vz = hero.mover.velocity
                self.assertAlmostEqual(vx, expected[0], places=4)
                self.assertAlmostEqual(vz, expected[1], places=4)
                self.assertEqual(vy, 4.5)
                self.assertTrue(instance.vars[1843].value())
                self.assertFalse(instance.vars[9570].value())
                self.assertEqual(hero.mover.history[-1].velocity, hero.mover.velocity)
                start = tuple(hero.mover.position)
                self.step(run, hero, yaw=yaw)
                moved = tuple(b - a for a, b in zip(start, hero.mover.position, strict=True))
                self.assertGreater(moved[0] * expected[0] + moved[2] * expected[1], 3)
                self.assertGreater(hero.mover.position[1], before[1])

    def test_lunge_preserves_full_draw_and_arrow_launches_from_moved_body(self):
        self.verify_lunge_held_draw()

    def test_lunge_preserves_sonic_until_release_from_moved_body(self):
        self.verify_lunge_held_draw(sonic_prepared=True)

    def verify_lunge_held_draw(self, *, sonic_prepared=False):
        run, hero, victim, _ = self.arena()
        fight = run.match.combat
        if sonic_prepared:
            import test_game_hanzo_sonic as sonic_tests

            fight.configure_sonic(sonic_tests.profile())
            self.step(run, hero, buttons=8)
            self.step(run, hero)
            instance = next(i for i in hero.body_script.component.instances.values() if i.graph.index == 0xC1)
            selected = instance.component.entity_var(627)
            self.assertEqual(selected.value(), 1)
        start_x = hero.mover.position[0]
        for _ in range(70):
            self.step(run, hero, buttons=1)
        self.step(run, hero, buttons=JUMP | 1)
        for _ in range(14):
            self.step(run, hero, buttons=1)
        self.step(run, hero, buttons=JUMP | 1, yaw=16384)
        self.assertEqual(hero.mover.velocity[:2], (15, 4.5))
        self.assertAlmostEqual(hero.mover.velocity[2], 0)
        for _ in range(6):
            self.step(run, hero, buttons=1, yaw=16384)
        self.assertFalse(fight.rockets)
        if sonic_prepared:
            self.assertEqual(selected.value(), 1)
            self.assertEqual(instance.vars[61].value(), 0)
        self.assertGreater(hero.mover.position[0] - start_x, 1)

        x, y, z = victim.mover.position
        yaw, pitch = aim(fight._eye(hero), (x, y + 1, z))
        self.step(run, hero, yaw=yaw, pitch=pitch)
        arrow, = fight.rockets
        self.assertIs(arrow.shooter, hero)
        self.assertEqual(arrow.source_entity, hero.body)
        self.assertEqual(arrow.direct.amount, 125)
        self.assertEqual(arrow.speed, 110)
        if sonic_prepared:
            self.assertEqual(arrow.shot.definition, 0x0400000000000149)
            self.assertEqual(selected.value(), 0)
            self.assertGreater(instance.vars[61].value(), 11.9)
        self.assertGreater(arrow.position[0] - start_x, 1)
        self.assertAlmostEqual(arrow.position[0], fight._eye(hero)[0], delta=0.3)
        for _ in range(15):
            self.step(run, hero)
        self.assertEqual(fight.vitals[victim.body].total, 75)
        self.assertEqual(len(hero.client.messages(combat.HIT)), 1)
        if sonic_prepared:
            beacon, = fight.sonars
            self.assertEqual(beacon.attached, victim.body)
            self.assertTrue(fight.revealed_to(hero, victim))

    def test_lunge_allows_storm_volley_and_normal_cooldown(self):
        run, hero, victim, _ = self.arena()
        fight = run.match.combat
        storm = next(i for i in hero.body_script.component.instances.values() if i.graph.index == 0x146A)
        self.step(run, hero, buttons=16)
        self.step(run, hero)
        self.arm(run, hero)
        start_x = hero.mover.position[0]
        self.step(run, hero, buttons=JUMP, yaw=16384)
        self.assertAlmostEqual(hero.mover.velocity[0], 15)
        self.assertTrue(storm.states[0].flags & 1)
        self.assertEqual(storm.find_var(0, 61).value(), 0)

        x, y, z = victim.mover.position
        first = None
        with patch.object(fight.projectiles, "fired", wraps=fight.projectiles.fired) as fired:
            for _ in range(100):
                yaw, pitch = aim(fight._eye(hero), (x, y + 1, z))
                self.step(run, hero, buttons=1, yaw=yaw, pitch=pitch)
                if first is None and fight.rockets:
                    first, = fight.rockets
                    self.assertTrue(hero.mover.airborne)
                    self.assertGreater(first.position[0], start_x)
                    self.assertEqual(first.direct.amount, 70)
            self.assertEqual([call.args[3] for call in fired.call_args_list], [0x040000000000270F] * 5)

        self.assertIsNotNone(first)
        self.assertGreater(storm.find_var(0, 61).value(), 9)
        self.assertEqual([hit["+0x78"]["+0x4"] for hit in hero.client.messages(combat.HIT)], [70, 70, 60])
        self.assertEqual(fight.vitals[victim.body].killer, hero.body)

    def test_dragonstrike_can_start_airborne_after_lunge_and_launch_from_moved_body(self):
        from test_game_hanzo_dragons import prepared_profile

        run, hero, victim, _ = self.arena()
        fight = run.match.combat
        fight.configure_dragonstrike(prepared_profile())
        body = hero.body_script
        body.set_ult_charge(1680)
        charge = body.component.entity_var(combat.ULT_CHARGE)
        start_x = hero.mover.position[0]
        x, y, z = victim.mover.position
        victim.mover.position[:] = (x + 4, y, z)
        victim.mover.body = None
        point = (x + 4, y + 1, z)

        self.arm(run, hero)
        self.step(run, hero, buttons=JUMP, yaw=16384)
        self.assertAlmostEqual(hero.mover.velocity[0], 15)
        for _ in range(6):
            self.step(run, hero)
        self.assertTrue(hero.mover.airborne)
        self.assertGreater(hero.mover.position[0] - start_x, 1)
        yaw, pitch = aim(fight._eye(hero), point)
        self.step(run, hero, buttons=32, yaw=yaw, pitch=pitch)
        self.assertTrue(hanzo_control.casting(body))
        self.assertEqual(charge.value(), 0)
        self.assertFalse(fight.rockets)

        for _ in range(100):
            yaw, pitch = aim(fight._eye(hero), point)
            self.step(run, hero, yaw=yaw, pitch=pitch)
            if fight.rockets:
                break
        arrow, = fight.rockets
        self.assertEqual(arrow.direct.amount, 125)
        self.assertGreater(arrow.position[0] - start_x, 1)
        self.assertEqual(arrow.source_entity, hero.body)
        for _ in range(50):
            if fight.dragons:
                break
            self.step(run, hero)
        flight, = fight.dragons
        self.assertEqual(flight.source_entity, hero.body)
        self.assertEqual(fight.vitals[victim.body].total, 75)

    def test_quick_melee_during_lunge_uses_moved_body_and_does_not_repeat(self):
        run, hero, victim, _ = self.arena()
        fight = run.match.combat
        x, y, z = hero.mover.position
        victim.mover.position[:] = (x, y, z + 3.5)
        victim.mover.body = None
        point = (x, y + 1, z + 3.5)
        target = next(t for t in fight._targets() if t.owner is victim)
        pool = fight.vitals_of(target)

        self.arm(run, hero)
        self.step(run, hero, buttons=JUMP)
        self.assertEqual(hero.mover.velocity, (0, 4.5, 15))
        for _ in range(7):
            self.step(run, hero)
        self.assertTrue(hero.mover.airborne)
        self.assertGreater(hero.mover.position[2] - z, 1.5)
        self.assertGreater(hero.mover.velocity[2], 0)
        yaw, pitch = aim(fight._eye(hero), point)
        self.step(run, hero, buttons=0x800, yaw=yaw, pitch=pitch)
        self.assertEqual(pool.total, 170)
        for _ in range(80):
            yaw, pitch = aim(fight._eye(hero), point)
            self.step(run, hero, buttons=0x800, yaw=yaw, pitch=pitch)
        self.assertEqual(pool.total, 170)
        self.assertEqual([hit["+0x78"]["+0x4"] for hit in hero.client.messages(combat.HIT)], [30])
        self.assertFalse(hero.mover.airborne)
        x, y, z = hero.mover.position
        victim.mover.position[:] = (x, y, z + 1.5)
        victim.mover.body = None
        yaw, pitch = aim(fight._eye(hero), (x, y + 1, z + 1.5))
        for _ in range(8):
            self.step(run, hero, buttons=0x800, yaw=yaw, pitch=pitch)
        self.assertEqual(pool.total, 170)
        self.step(run, hero, yaw=yaw, pitch=pitch)
        self.step(run, hero, buttons=0x800, yaw=yaw, pitch=pitch)
        self.assertEqual(pool.total, 140)
        self.assertEqual([hit["+0x78"]["+0x4"] for hit in hero.client.messages(combat.HIT)], [30, 30])
        self.assertFalse(fight.rockets)

    def test_side_lunge_evades_actual_enemy_rifle_aimed_at_jump_lane(self):
        for lunge, tracked, health, dealt in (
            (False, False, 200, 20), (True, False, 200, 0),
            (True, True, 200, 20), (True, True, 20, 20),
        ):
            with self.subTest(lunge=lunge, tracked=tracked, health=health):
                run, hero, soldier, instance = self.arena()
                fight = run.match.combat
                x, y, z = hero.mover.position
                target = next(t for t in fight._targets() if t.owner is hero)
                pool = fight.vitals_of(target)
                pool.damage(200 - health)
                old_body, old_script = hero.body, hero.body_script
                self.arm(run, hero)
                self.step(run, hero, buttons=JUMP if lunge else 0, yaw=16384)
                for _ in range(10):
                    self.step(run, hero)
                self.assertTrue(hero.mover.airborne)
                if lunge:
                    self.assertGreater(hero.mover.position[0] - x, 2)
                else:
                    self.assertAlmostEqual(hero.mover.position[0], x)
                point = (x, y + 1.6, z)
                if tracked:
                    px, py, pz = hero.mover.position
                    point = (px, py + 1, pz)
                angles = aim(fight._eye(soldier), point)
                with patch.object(fight, "_hitscan", wraps=fight._hitscan) as shot:
                    for _ in range(10):
                        run.step({soldier: (*angles, 1)})
                        if shot.called:
                            break
                shot.assert_called_once()
                self.assertIs(shot.call_args.args[0], soldier)
                self.assertEqual(pool.total, health - dealt)
                hits = soldier.client.messages(combat.HIT)
                self.assertEqual([hit["+0x78"]["+0x4"] for hit in hits], [dealt] if dealt else [])
                if pool.dead:
                    self.assertEqual(pool.killer, soldier.body)
                    self.assertGreater(instance.vars[61].value(), 0)
                    dead_pose = tuple(hero.mover.position)
                    for _ in range(5):
                        self.step(run, hero, buttons=JUMP)
                    self.assertEqual(tuple(hero.mover.position), dead_pose)

                    run.frame += math.ceil(combat.respawn_seconds(run.match) / combat.TICK) + 2
                    run.match.update(run.frame * combat.TICK, run.frame)
                    self.assertNotEqual(hero.body, old_body)
                    self.assertTrue(old_script.retired)
                    hero.mover.position[:] = (x, y, z)
                    hero.mover.body = None
                    self.step(run, hero)
                    fresh = next(i for i in hero.body_script.component.instances.values() if i.graph.index == 0x010C)
                    self.assertEqual(fresh.vars[61].value(), 0)
                    self.assertEqual(hero.mover.velocity[0], 0)
                    self.arm(run, hero)
                    self.step(run, hero, buttons=JUMP, yaw=16384)
                    self.assertAlmostEqual(hero.mover.velocity[0], 15)

    def test_lunge_can_avoid_helix_direct_hit_and_then_its_wall_splash(self):
        point = None
        for lead, expected in ((None, "direct"), (0, "splash"), (14, "clear")):
            with self.subTest(lead=lead):
                run, hero, soldier, _ = self.arena()
                fight = run.match.combat
                x, y, z = hero.mover.position
                run.match.collision_world = World(
                    box(x - 20, x + 20, y - 1, y, z - 20, z + 20)
                    + wall(x - 8, z - 0.7, x + 8, z - 0.7, y0=y, y1=y + 8),
                )
                target = next(t for t in fight._targets() if t.owner is hero)
                pool = fight.vitals_of(target)
                self.arm(run, hero)
                if lead is None:
                    self.step(run, hero)
                    point = (x, hero.mover.position[1] + 1, z)
                else:
                    self.step(run, hero, buttons=JUMP, yaw=16384)
                    self.assertAlmostEqual(hero.mover.velocity[0], 15)
                    for _ in range(lead):
                        self.step(run, hero)
                angles = aim(fight._eye(soldier), point)
                run.step({soldier: (*angles, 2)})
                rocket, = fight.rockets
                self.assertIs(rocket.shooter, soldier)
                self.assertEqual(rocket.shot.definition, 0x040000000000072C)
                self.assertEqual((rocket.direct.amount, rocket.blast.amount), (40, 80))
                for _ in range(30):
                    if rocket not in fight.rockets:
                        break
                    run.step({})
                self.assertFalse(rocket in fight.rockets)
                hits = [hit["+0x78"]["+0x4"] for hit in soldier.client.messages(combat.HIT)]
                if expected == "direct":
                    self.assertEqual(hits, [40, 80])
                    self.assertEqual(pool.total, 80)
                else:
                    self.assertAlmostEqual(rocket.position[2], z - 0.7, places=4)
                    if expected == "splash":
                        loss, = hits
                        self.assertGreater(loss, 0)
                        self.assertLess(loss, 80)
                        self.assertAlmostEqual(pool.total, 200 - loss, delta=0.5)
                    else:
                        self.assertFalse(hits)
                        self.assertEqual(pool.total, 200)

    def test_lunge_exits_allied_biotic_field_and_hostile_field_never_heals(self):
        for allied in (False, True):
            with self.subTest(allied=allied):
                run, hero, soldier, _ = self.arena()
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                soldier.team = hero.team if allied else 1 - hero.team
                fight = run.match.combat
                x, y, z = origin = tuple(hero.mover.position)
                soldier.mover.position[:] = (x - 1, y, z)
                soldier.mover.body = None
                target = next(t for t in fight._targets() if t.owner is hero)
                pool = fight.vitals_of(target)
                pool.damage(160)
                run.step({soldier: (0, 0, 16)})
                for _ in range(100):
                    run.step({})
                    if any(shot.field_tick is not None for shot in fight.projectiles.shots):
                        break
                field, = [shot for shot in fight.projectiles.shots if shot.field_tick is not None]
                self.assertIs(field.player, soldier)
                self.assertEqual(field.definition, 0x040000000000072D)
                self.assertEqual(pool.total, 40)
                dose = projectiles.HEAL_PER_SECOND * combat.TICK
                for _ in range(4):
                    self.step(run, hero)
                self.assertAlmostEqual(pool.total, 40 + (4 * dose if allied else 0))

                self.arm(run, hero)
                self.step(run, hero, buttons=JUMP)
                self.assertEqual(hero.mover.velocity, (0, 4.5, 15))
                for _ in range(45):
                    self.step(run, hero)
                self.assertGreater(hero.mover.position[2] - z, projectiles.HEAL_RADIUS + 0.5)
                outside = pool.total
                if allied:
                    self.assertGreater(outside, 40 + 4 * dose)
                else:
                    self.assertEqual(outside, 40)
                for _ in range(8):
                    self.step(run, hero)
                self.assertEqual(pool.total, outside)

                hero.mover.position[:] = origin
                hero.mover.body = None
                self.step(run, hero)
                self.assertLess(run.frame - field.field_tick, projectiles.FIELD_TICKS)
                self.assertAlmostEqual(pool.total, outside + (dose if allied else 0))

    def test_held_ground_jump_and_early_repress_do_not_lunge(self):
        for held in (False, True):
            with self.subTest(held=held):
                run, hero, _, instance = self.arena()
                self.step(run, hero, buttons=JUMP)
                for frame in range(20):
                    self.step(run, hero, buttons=JUMP if held or frame == 1 else 0)
                self.assertNotIn(1790, instance.vars)
                self.assertEqual(hero.mover.velocity[0::2], (0.0, 0.0))
                self.assertEqual(instance.vars[61].value(), 0)
                self.step(run, hero)
                self.step(run, hero, buttons=JUMP)
                self.assertEqual(hero.mover.velocity, (0.0, 4.5, 15.0))

    def test_cooldown_rejects_early_edges_then_fresh_airborne_press_reuses(self):
        run, hero, _, instance = self.arena()
        world = hero.body_script.world
        with patch.object(world, "velocity_action", wraps=world.velocity_action) as launch:
            self.arm(run, hero)
            self.step(run, hero, buttons=JUMP)
            for _ in range(8):
                self.step(run, hero)
            self.assertGreater(instance.vars[61].value(), 4.9)
            self.assertLessEqual(instance.vars[61].value(), 5)
            for frame in range(90):
                self.step(run, hero, buttons=JUMP if frame % 5 == 0 else 0)
            self.assertEqual(launch.call_count, 1)
            for _ in range(400):
                if instance.vars[61].value() <= 0:
                    break
                self.step(run, hero)
            self.assertEqual(instance.vars[61].value(), 0)
            self.assertEqual(launch.call_count, 1)
            # Ensure a fresh grounded take-off instead of reusing an expired edge.
            for _ in range(80):
                self.step(run, hero)
                if not hero.mover.flags_word & hanzo_movement.IN_AIR:
                    break
            self.arm(run, hero)
            self.step(run, hero, buttons=JUMP)
            self.assertEqual(launch.call_count, 2)
            self.assertEqual(hero.mover.velocity, (0.0, 4.5, 15.0))

    def test_launched_body_collides_with_static_wall_and_returns_to_ground(self):
        run, hero, victim, _ = self.arena()
        victim.spawned = False
        x, y, z = hero.mover.position
        run.match.collision_world = World(
            box(x - 20, x + 20, y - 1, y, z - 20, z + 20)
            + wall(x - 5, z + 2, x + 5, z + 2, y0=y, y1=y + 8),
            name="declared Lunge floor and wall fixture",
        )
        self.arm(run, hero)
        self.step(run, hero, buttons=JUMP)
        for _ in range(100):
            self.step(run, hero)
        self.assertGreater(hero.mover.position[2], z + 1)
        self.assertLess(hero.mover.position[2], z + 1.51)
        self.assertAlmostEqual(hero.mover.position[1], y, places=2)
        self.assertFalse(hero.mover.flags_word & hanzo_movement.IN_AIR)
        self.assertEqual(hero.mover.velocity, (0.0, 0.0, 0.0))

    def test_launch_uses_authored_ruleset_scalar(self):
        run, hero, _, _ = self.arena()
        world = hero.body_script.world
        ruleset = world.ruleset
        with patch.object(world, "ruleset", side_effect=lambda key: 0.5 if key == 14960 else ruleset(key)):
            self.arm(run, hero)
            self.step(run, hero, buttons=JUMP)
        self.assertEqual(hero.mover.velocity, (0.0, 2.25, 7.5))

    def test_dead_hanzo_cannot_launch_and_soldier_does_not_gain_lunge(self):
        run, hero, victim, _ = self.arena()
        self.arm(run, hero)
        target = next(t for t in run.match.combat._targets() if t.owner is hero)
        run.match.combat._damage(victim, target, 200, False, None, run.frame)
        self.assertTrue(run.match.combat.is_dead(hero))
        pose = tuple(hero.mover.position)
        self.step(run, hero, buttons=JUMP)
        self.assertEqual(tuple(hero.mover.position), pose)
        self.assertNotEqual(hero.mover.velocity, (0, 4.5, 15))
        run, hero, _, _ = self.arena()
        run.match.switch_hero(hero, heroes()[SOLDIER])
        self.arm(run, hero)
        self.step(run, hero, buttons=JUMP)
        self.assertEqual(hero.mover.velocity[0::2], (0.0, 0.0))

    def test_strict_mode_refuses_reconstructed_launch_and_unknown_rows_defer(self):
        _run, hero, _, instance = self.arena()
        node = next(node for node in instance.graph.nodes if node.pos == 82)
        velocity = hero.mover.velocity
        with patch.dict(node.fields, {"m_BC5E91CF": [{"$": "STU_UNKNOWN_VELOCITY"}]}):
            self.assertIs(hero.body_script.world.velocity_action(instance, node), NotImplemented)
        instance.component.strict = True
        with self.assertRaisesRegex(runtime.UnsupportedScriptError, "reconstructed Hanzo launch"):
            instance.run_node(node, None)
        self.assertEqual(hero.mover.velocity, velocity)
