"""Executed dynamite lifetime/damage checks; projectile poses use explicit test fixtures."""

import math
import unittest
from unittest.mock import patch

import test_ashe_combat as fixtures

from ow174.game import ashe, combat, world
from ow174.game.collision import World
from ow174.game.content import HEROES_PATH, SOLDIER, find_hero, heroes
from ow174.game.script import graph

BLAST_TAGS = (0x0D80000000004460, 0x0D8000000000445F, 0x0D8000000000460D, 0x0D80000000004D29)
BURN_TAGS = (0x0D80000000003177, 0x0D80000000004460, 0x0D8000000000445F,
             0x0D80000000004910, 0x0D80000000001628)


@unittest.skipUnless(HEROES_PATH.is_file() and graph.GRAPHS_PATH.is_file(), "requires external 104319 data")
class DynamiteTests(unittest.TestCase):
    fight = fixtures.AsheCombatTests.fight
    press = fixtures.AsheCombatTests.press

    def cast(self, team_game=False, *, distance=10):
        run, player, enemy = self.fight(distance=distance, team_game=team_game)
        for target in run.match.combat._targets():
            run.match.combat.vitals_of(target)
        self.press(run, player, 16)
        engine = run.match.combat.dynamite
        self.assertEqual(len(engine.grenades), 1)
        return run, player, enemy, engine, engine.grenades[0]

    def at_enemy(self, enemy, grenade):
        # Explicit static pose fixture; fuse/update/damage still use production code.
        grenade.position = (enemy.mover.position[0], enemy.mover.position[1] + 0.8,
                            enemy.mover.position[2])
        grenade.velocity = (0, 0, 0)

    def wait_fuse(self, run, grenade):
        while run.frame < grenade.fuse:
            run.step({})

    def test_throw_uses_curved_flight_and_can_miss(self):
        run, player, enemy, engine, grenade = self.cast(distance=40)
        throw = graph.graph(0x14FD).node(18)
        motion, = throw.field("m_projectileMotions")
        self.assertEqual(motion["$"], "STU_E1A5B579")
        gravity = motion["m_599AB8D7"]["m_value"]
        self.assertEqual(gravity, 8)
        previous_velocity = grenade.velocity
        run.step({})
        self.assertAlmostEqual(grenade.velocity[1] - previous_velocity[1], -gravity * combat.TICK)
        self.assertLess(grenade.velocity[1], 0)
        self.assertGreater(grenade.position[2], 0)
        self.assertEqual(grenade.fuse - grenade.born, math.ceil(ashe.FUSE / combat.TICK))
        self.wait_fuse(run, grenade)
        self.assertNotIn(grenade, engine.grenades)
        self.assertEqual(run.match.combat.vitals[enemy.body].total, 200)
        self.assertEqual(run.match.combat.vitals[player.body].total, 200)

    def test_fuse_blast_burn_and_expiry(self):
        run, player, enemy, engine, grenade = self.cast()
        self.at_enemy(enemy, grenade)
        while run.frame < grenade.fuse - 1:
            run.step({})
        self.assertEqual(run.match.combat.vitals[enemy.body].total, 200)
        run.step({})
        self.assertEqual(run.match.combat.vitals[enemy.body].total, 150)
        self.assertIn((player.body, enemy.body), engine.burns)
        for _ in range(math.ceil(ashe.BURN_SECONDS / combat.TICK)):
            run.step({})
        self.assertEqual(run.match.combat.vitals[enemy.body].total, 50)
        self.assertEqual(engine.burns, {})
        for _ in range(30):
            run.step({})
        self.assertEqual(run.match.combat.vitals[enemy.body].total, 50)
        shot = grenade.shot
        self.assertIsNotNone(shot)
        self.assertTrue(shot.created)
        observer, = shot.observers
        self.assertTrue(observer.launch_arrival.received)
        for client, ids in ((player.client, shot.created), (enemy.client, observer.record.ids)):
            destroys = [update for update in client.entities
                        if update.op == world.OP_DESTROY and update.entity in ids]
            self.assertCountEqual([update.entity for update in destroys], list(ids))
            self.assertTrue(all(update.ch2 is not None for update in destroys))

    def test_second_ashes_dynamite_replaces_burn_and_owns_new_pulses(self):
        play_burn = graph.graph(0x1502).node(21)
        self.assertEqual(play_burn.field("m_script")["m_graph"], "0x058000000000150F")
        self.assertEqual(play_burn.field("m_AA4DE426"), 3)

        for delay, first_pulses in ((40, 2), (0, 0)):
            with self.subTest(delay=delay):
                run = fixtures.Run(fixtures.KANEZAKA_DM, ("First Ashe", "Second Ashe", "Enemy"))
                first, second, enemy = run.players
                run.match.bots = []
                # Parallel trajectories fuse beside the target without capsule contact.
                for player, x in ((first, -1.2), (second, 1.2)):
                    run.match.switch_hero(player, heroes()[fixtures.ASHE])
                    player.mover.position[:] = [x, 0, 0]
                    player.body_script.ult_seconds = 0
                    player.body_script.set_ult_charge(0)
                self.assertTrue(run.match.switch_hero(enemy, find_hero("reaper")))
                enemy.mover.position[:] = [0, 0, 15.5]
                for _ in range(100):
                    run.step({})
                fight = run.match.combat
                target = next(item for item in fight._targets() if item.owner is enemy)
                vitals = fight.vitals_of(target)
                self.assertEqual(vitals.total, 250)

                for frame in range(delay + 20):
                    first_buttons = 16 if frame < 5 else 0
                    second_buttons = 16 if delay <= frame < delay + 5 else 0
                    run.step({first: (0, 0, first_buttons), second: (0, 0, second_buttons)})
                engine = fight.dynamite
                first_grenade, second_grenade = engine.grenades
                self.assertIs(first_grenade.shooter, first)
                self.assertIs(second_grenade.shooter, second)
                self.assertEqual(second_grenade.fuse - first_grenade.fuse, delay)
                if delay:
                    self.wait_fuse(run, first_grenade)
                    first_burn = engine.burns[(first.body, enemy.body)]
                    while run.frame < second_grenade.fuse - 1:
                        run.step({})
                    self.assertEqual(first_burn.pulses, first_pulses)
                    self.assertEqual(vitals.total, 200 - 5 * first_pulses)

                with patch.object(fight, "_damage", wraps=fight._damage) as damage:
                    self.wait_fuse(run, second_grenade)
                self.assertEqual([call.kwargs["hit_tags"] for call in damage.call_args_list],
                                 [BLAST_TAGS] * (1 if delay else 2))
                after_blasts = 150 - 5 * first_pulses
                self.assertEqual(vitals.total, after_blasts)
                self.assertEqual(set(engine.burns), {(second.body, enemy.body)})
                second_burn = engine.burns[(second.body, enemy.body)]
                self.assertEqual(second_burn.start, run.frame)
                while run.frame < second_burn.next_damage - 1:
                    run.step({})
                self.assertEqual(vitals.total, after_blasts)
                with patch.object(fight, "_damage", wraps=fight._damage) as damage:
                    run.step({})
                    self.assertEqual(vitals.total, after_blasts - 5)
                    while run.frame <= second_burn.until:
                        run.step({})
                self.assertEqual([call.kwargs["hit_tags"] for call in damage.call_args_list],
                                 [BURN_TAGS] * 20)
                self.assertTrue(all(call.args[0] is second for call in damage.call_args_list))
                self.assertEqual(second_burn.pulses, 20)
                self.assertEqual(vitals.total, after_blasts - 100)
                self.assertEqual(engine.burns, {})
                self.assertEqual(first.body_script.component.entity_var(342).value(), 50 + 5 * first_pulses)
                self.assertEqual(second.body_script.component.entity_var(342).value(), 150)

    def test_fuse_uses_the_throwers_ruleset_at_launch(self):
        # Spawn at the measured landing points of the untouched throws.
        for multiplier in (0.5, 1.5):
            with self.subTest(multiplier=multiplier):
                run, player, enemy = self.fight(distance=15.5)
                fight = run.match.combat
                engine = fight.dynamite
                world = player.body_script.component.world
                ruleset = world.ruleset
                with patch.object(world, "ruleset", side_effect=lambda key, scale=multiplier, base=ruleset:
                                  scale if key == 0x4402 else base(key)):
                    self.press(run, player, 16)
                grenade, = engine.grenades
                deadline = grenade.born + math.ceil((2 * multiplier) / combat.TICK)
                self.assertEqual(grenade.fuse, deadline)
                self.assertIsNotNone(grenade.shot)
                self.assertEqual(grenade.shot.bound, deadline - grenade.born)
                target = next(item for item in fight._targets() if item.owner is enemy)
                while run.frame < deadline - 1:
                    run.step({})
                    self.assertGreater(grenade.position[1], target.feet[1])
                self.assertIn(grenade, engine.grenades)
                self.assertEqual(fight.vitals_of(target).total, 200)
                run.step({})
                self.assertNotIn(grenade, engine.grenades)
                self.assertEqual(fight.vitals_of(target).total, 150)
                burn = engine.burns[(player.body, enemy.body)]
                self.assertEqual(burn.start, deadline)
                while run.frame < burn.next_damage - 1:
                    run.step({})
                self.assertEqual(fight.vitals_of(target).total, 150)
                run.step({})
                self.assertEqual(fight.vitals_of(target).total, 145)

    def test_floor_bounce_does_not_explode_or_restart_fuse(self):
        run, _player, enemy, engine, grenade = self.cast()
        grenade.position = (5, enemy.mover.position[1] + 0.15, 5)
        grenade.velocity = (0, -5, 0)
        deadline = grenade.fuse
        run.step({})
        self.assertAlmostEqual(grenade.velocity[1], 0.2 * (5 + 8 * combat.TICK), places=5)
        self.assertEqual(grenade.bounces, 1)
        self.assertEqual(grenade.fuse, deadline)
        self.assertIn(grenade, engine.grenades)
        self.assertEqual(run.match.combat.vitals[enemy.body].total, 200)

    def test_bounce_components_match_captured_native_vectors(self):
        bounce = graph.graph(0x14FD).node(18).field("m_E365DB20")
        for key in ("m_0D20DAFD", "m_2F054D7D", "m_A28234C2"):
            self.assertEqual(bounce[key]["m_value"], 0.2)
        diagonal = math.sqrt(0.5)
        for normal, expected in (
            ((0, 1, 0), (0.6, 0.8, 2.0)),
            ((0, 0, -1), (0.6, -4.0, -2.0)),
            ((0, diagonal, -diagonal), (0.6, 2.0, -0.8)),
        ):
            with self.subTest(normal=normal):
                velocity = ashe.dynamite_bounce_velocity((3, -4, 10), normal)
                for component, value in zip(velocity, expected, strict=True):
                    self.assertAlmostEqual(component, value, places=6)

    def test_world_sweep_bounces_real_throw_at_wall_edge(self):
        run, player, enemy = self.fight(distance=12)
        fight = run.match.combat
        wall = World([
            (0.06, -2, 6, 4, -2, 6, 4, 8, 6),
            (0.06, -2, 6, 4, 8, 6, 0.06, 8, 6),
        ])
        self.assertIsNone(wall.cast_ray(fight._eye(player), (0, 0, 1), 12))
        contact = graph.graph(0x14FD).node(18).field("m_6396149F")
        self.assertEqual(contact["m_1FFD8312"]["m_value"], ashe.WORLD_RADIUS)
        run.match.collision_world = wall

        self.press(run, player, 16)
        grenade, = fight.dynamite.grenades
        deadline = grenade.fuse
        for _ in range(24):
            if grenade.bounces:
                break
            run.step({})
        self.assertEqual(grenade.bounces, 1)
        self.assertLess(grenade.position[2], 6)
        self.assertLess(grenade.velocity[0], 0)
        self.assertEqual(grenade.fuse, deadline)
        self.assertEqual(fight.vitals[enemy.body].total, 200)
        self.assertEqual(fight.dynamite.burns, {})

    def test_live_throw_bounces_from_enemy_capsule_and_crosses_allied_capsule(self):
        for ally in (False, True):
            with self.subTest(ally=ally):
                run, player, other = self.fight(distance=5, team_game=ally)
                if ally:
                    other.team = player.team
                fight = run.match.combat
                target = next(item for item in fight._targets() if item.owner is other)
                vitals = fight.vitals_of(target)
                self.press(run, player, 16)
                grenade, = fight.dynamite.grenades
                deadline = grenade.fuse
                while run.frame < grenade.born + 18:
                    run.step({})
                self.assertEqual(grenade.bounces, 0 if ally else 1)
                self.assertEqual(grenade.velocity[2], 25.0 if ally else -5.0)
                self.assertEqual(grenade.fuse, deadline)
                self.assertEqual(vitals.total, 200)
                self.assertEqual(fight.dynamite.burns, {})
                self.wait_fuse(run, grenade)
                self.assertNotIn(grenade, fight.dynamite.grenades)
                self.assertEqual(vitals.total < 200, not ally)
                self.assertEqual((player.body, other.body) in fight.dynamite.burns, not ally)

    def test_own_viper_shot_detonates_dynamite_during_early_flight(self):
        run, player, enemy = self.fight(distance=3)
        fight = run.match.combat
        engine = fight.dynamite
        for _ in range(20):
            run.step({player: (0, 0, 16)})
            if engine.grenades:
                break
        grenade, = engine.grenades
        # The authored throw recovery outlasts this flight window. End it as
        # an explicit fixture so the Viper input can exercise the early ray.
        component = player.body_script.component
        throw = next(i for i in component.instances.values() if i.graph.index == 0x14FD)
        throw.states[4].stop()
        component.run_now()
        for _ in range(4):
            run.step({})
        yaw, pitch = fixtures.aim(fight._eye(player), grenade.position)
        with patch.object(fight, "_shoot", wraps=fight._shoot) as reports:
            run.step({player: (yaw, pitch, 1)})
            reports.assert_called_once()
            self.assertEqual(reports.call_args.args[2].instance.graph.index, 0x14FC)
        self.assertLess((run.frame - grenade.born) * combat.TICK, 0.3)
        self.assertNotIn(grenade, engine.grenades)
        self.assertEqual(fight.vitals[enemy.body].total, 150)
        self.assertEqual(engine.burns[(player.body, enemy.body)].start, run.frame)

    def test_non_owner_hitscan_does_not_trigger_owner_detonation(self):
        for ally in (False, True):
            with self.subTest(ally=ally):
                run, player, other, engine, grenade = self.cast(team_game=ally, distance=8)
                if ally:
                    other.team = player.team
                for _ in range(25):
                    run.step({})
                fight = run.match.combat
                eye = fight._eye(other)
                # Fix the target on the ray between the two bodies.
                grenade.position = (0, eye[1], 5)
                grenade.velocity = (0, 0, 0)
                yaw, pitch = fixtures.aim(eye, grenade.position)
                with patch.object(fight, "_shoot", wraps=fight._shoot) as reports:
                    run.step({other: (yaw, pitch, 1)})
                    reports.assert_called_once()
                    self.assertIs(reports.call_args.args[0], other)
                self.assertIn(grenade, engine.grenades)
                self.assertEqual(engine.burns, {})
                owner_health = fight.vitals[player.body].total
                if ally:
                    self.assertEqual(owner_health, 200)
                else:
                    self.assertLess(owner_health, 200)

    def test_viper_detonates_armed_grenade_and_stops_its_ray(self):
        # Spawn beyond the inner blast radius, before physics/history creation.
        run, player, enemy, engine, grenade = self.cast(distance=8)
        for _ in range(40):
            run.step({})
        grenade.position = (0, run.match.combat._eye(player)[1], 5)
        grenade.velocity = (0, 0, 0)
        self.press(run, player, 1, frames=1)
        self.assertNotIn(grenade, engine.grenades)
        self.assertLess(run.match.combat.vitals[enemy.body].total, 200)
        self.assertGreater(run.match.combat.vitals[enemy.body].total, 150)
        self.assertIn((player.body, enemy.body), engine.burns)

    def test_wall_blocks_shoot_detonation_and_explosion(self):
        run, player, enemy, engine, grenade = self.cast()
        for _ in range(40):
            run.step({})
        grenade.position = (0, run.match.combat._eye(player)[1], 5)
        grenade.velocity = (0, 0, 0)
        run.match.collision_world = World([
            (-10, -10, 4, 10, -10, 4, 10, 10, 4),
            (-10, -10, 4, 10, 10, 4, -10, 10, 4),
        ])
        self.press(run, player, 1, frames=1)
        self.assertIn(grenade, engine.grenades)
        enemy.mover.position[2] = 3
        engine.detonate(grenade)
        self.assertEqual(run.match.combat.vitals[enemy.body].total, 200)
        self.assertEqual(engine.burns, {})

    def test_ally_exclusion_and_half_self_damage_without_ultimate_award(self):
        run, player, ally, engine, grenade = self.cast(team_game=True)
        ally.team = player.team
        self.at_enemy(ally, grenade)
        player.mover.position[:] = ally.mover.position[:]
        player.body_script.ult_seconds = 0
        player.body_script.set_ult_charge(0)
        engine.detonate(grenade)
        self.assertEqual(run.match.combat.vitals[player.body].total, 175)
        self.assertEqual(run.match.combat.vitals[ally.body].total, 200)
        for _ in range(math.ceil(ashe.BURN_SECONDS / combat.TICK)):
            run.step({})
        self.assertEqual(run.match.combat.vitals[player.body].total, 125)
        self.assertEqual(player.body_script.component.entity_var(342).value(), 0)
        self.assertEqual(player.client.messages(combat.HIT), [])

    def test_radius_falloff_edge_and_outside(self):
        for offset, expected in ((0, 50), (5, 20), (5.01, 0)):
            with self.subTest(offset=offset):
                run, _player, enemy, engine, grenade = self.cast()
                target = next(item for item in run.match.combat._targets() if item.owner is enemy)
                grenade.position = (target.feet[0] + (target.radius + offset if offset else 0),
                                    target.feet[1] + 0.8, target.feet[2])
                engine.detonate(grenade)
                self.assertAlmostEqual(run.match.combat.vitals[enemy.body].total, 200 - expected)
                self.assertEqual(bool(engine.burns), expected > 0)

    def test_owner_death_does_not_cancel_thrown_grenade(self):
        run, player, enemy, engine, grenade = self.cast()
        self.at_enemy(enemy, grenade)
        target = next(item for item in run.match.combat._targets() if item.owner is player)
        run.match.combat._damage(enemy, target, 1000, False, None, run.frame)
        self.wait_fuse(run, grenade)
        self.assertTrue(run.match.combat.vitals[player.body].dead)
        self.assertEqual(run.match.combat.vitals[enemy.body].total, 150)
        self.assertEqual(len(engine.burns), 1)

    def test_hero_replacement_retires_owned_burn(self):
        run, player, enemy, engine, grenade = self.cast()
        self.at_enemy(enemy, grenade)
        engine.detonate(grenade)
        self.assertTrue(engine.burns)
        run.match.switch_hero(player, heroes()[SOLDIER])
        self.assertEqual(engine.burns, {})
        self.assertEqual(engine.grenades, [])

    def test_hero_replacement_cancels_active_throw_and_acknowledged_copies(self):
        run, player, enemy, engine, grenade = self.cast(distance=40)
        fight = run.match.combat
        shot = grenade.shot
        self.assertIsNotNone(shot)
        observer, = shot.observers
        for _ in range(36):
            run.step({})
            if shot.created and observer.launch_arrival.received:
                break
        self.assertTrue(shot.created)
        self.assertTrue(observer.launch_arrival.received)
        self.assertTrue(run.match.switch_hero(player, heroes()[SOLDIER]))
        self.assertNotIn(grenade, engine.grenades)
        self.assertNotIn(shot, fight.projectiles.shots)
        self.wait_fuse(run, grenade)
        for client, ids in ((player.client, shot.created), (enemy.client, observer.record.ids)):
            destroys = [update for update in client.entities
                        if update.op == world.OP_DESTROY and update.entity in ids]
            self.assertCountEqual([update.entity for update in destroys], list(ids))
            self.assertTrue(all(update.ch2 is None for update in destroys))
        self.assertEqual(engine.burns, {})

    def test_repeated_update_does_not_repeat_burn_damage(self):
        run, _player, enemy, engine, grenade = self.cast()
        self.at_enemy(enemy, grenade)
        engine.detonate(grenade)
        for _ in range(math.ceil(ashe.BURN_STEP / combat.TICK)):
            run.step({})
        self.assertEqual(run.match.combat.vitals[enemy.body].total, 145)
        for _ in range(5):
            engine.update(run.frame)
        self.assertEqual(run.match.combat.vitals[enemy.body].total, 145)

    def test_skipped_update_preserves_complete_burn_total_and_expiry(self):
        run, _player, enemy, engine, grenade = self.cast()
        self.at_enemy(enemy, grenade)
        engine.detonate(grenade)
        run.frame += math.ceil(ashe.BURN_SECONDS / combat.TICK)
        run.match.update(run.frame * combat.TICK, run.frame)
        self.assertEqual(run.match.combat.vitals[enemy.body].total, 50)
        self.assertEqual(engine.burns, {})


if __name__ == "__main__":
    unittest.main()
