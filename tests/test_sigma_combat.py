"""Real Sigma input/graph reports through Combat; original-client acceptance is separate."""

import unittest
from dataclasses import replace
from math import hypot
from unittest.mock import patch

from test_game_combat import KANEZAKA_DM, Run, aim

from ow174.game import bots, combat, sigma, symmetra_beam
from ow174.game.collision import World
from ow174.game.commands import CROUCH, FIRE, SECONDARY, Command
from ow174.game.content import heroes
from ow174.game.script import expr, graph, nodes

SIGMA = 0x02E000000000023B


class SigmaCombatTests(unittest.TestCase):
    def ability(self, player, graph_index):
        instance = next(i for i in player.body_script.component.instances.values()
                        if i.graph.index == graph_index)
        return instance.state(0)

    def arena(self, *, offset=1.0, distance=22.0, game_map=KANEZAKA_DM):
        run = Run(game_map, ("Sigma", "Enemy"))
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        shooter, victim = run.players
        run.match.switch_hero(shooter, heroes()[SIGMA])
        x, y, z = shooter.mover.position
        victim.mover.position = [x + offset, y, z + distance]
        target = next(t for t in run.match.combat._targets() if t.owner is victim)
        return run, shooter, victim, run.match.combat.vitals_of(target)

    def pair(self, run, shooter, target=None):
        yaw, pitch = aim(run.match.combat._eye(shooter), run.match.combat._eye(target)) if target else (0, 0)
        run.step({shooter: (yaw, pitch, FIRE)})
        for _ in range(60):
            run.step({shooter: (yaw, pitch, 0)})

    def wall(self, run, *, sideways, offset, height=4):
        x, y, z = run.players[0].mover.position
        if sideways:
            a, b, c, d = ((x + offset, y, z + 20), (x + offset, y + height, z + 20),
                          (x + offset, y + height, z + 24), (x + offset, y, z + 24))
        else:
            a, b, c, d = ((x - 4, y, z + offset), (x + 4, y, z + offset),
                          (x + 4, y + height, z + offset), (x - 4, y + height, z + offset))
        floor = [(-5000, y, -5000, -5000, y, 5000, 5000, y, 5000),
                 (-5000, y, -5000, 5000, y, 5000, 5000, y, -5000)]
        run.match.collision_world = World([*floor, (*a, *b, *c), (*a, *c, *d)])
        for player in run.players:
            player.mover.use(world=run.match.collision_world)

    def test_authored_pair_airbursts_damage_a_player_beyond_the_direct_hit_capsule(self):
        run, shooter, enemy, vitals = self.arena()
        start_x = enemy.mover.position[0]
        self.pair(run, shooter)
        self.assertEqual(vitals.total, 140.0)  # two 30-point airbursts, no direct hit
        self.assertEqual(len(shooter.client.messages(combat.HIT)), 2)
        self.assertLess(enemy.mover.position[0], start_x)
        self.assertFalse(run.match.combat.rockets)
        for _ in range(30):
            run.step({})
        self.assertEqual(vitals.total, 140.0)  # no repeated expiry damage

    def test_airburst_distinguishes_missing_ring_multiplier_from_explicit_zero(self):
        from contextlib import ExitStack

        for multiplier, expected in ((None, 60), ({"$": "STUConfigVarFloat", "m_value": 0}, 0)):
            with self.subTest(multiplier=multiplier), ExitStack() as stack:
                weapon = graph.graph(0x187F)
                for position in (45, 56):
                    node = weapon.node(position)
                    blast = node.field("m_D2D11CE9")
                    inner, outer = blast["m_0D30E40C"]
                    modified = {**blast, "m_0D30E40C": [{**inner, "m_B399CACB": multiplier}, outer]}
                    stack.enter_context(patch.dict(node.fields, {"m_D2D11CE9": modified}))
                run, shooter, _enemy, vitals = self.arena(offset=1, distance=22)
                self.pair(run, shooter)
                self.assertEqual(vitals.maximum - vitals.total, expected)

    def test_direct_hits_get_25_plus_30_each_without_an_extra_expiry_explosion(self):
        run, shooter, enemy, vitals = self.arena(offset=0, distance=6)
        self.pair(run, shooter, target=enemy)
        self.assertEqual(vitals.total, 90.0)
        self.assertEqual(len(shooter.client.messages(combat.HIT)), 4)

    def test_hypersphere_pair_recovers_without_ammo_or_reload(self):
        from ow174.game.script.driver import EDGE_BITS

        # Bit 04 delivers both reload aliases in the 104319 command table.
        reload = 0x04
        self.assertEqual({button for bit, button in EDGE_BITS if bit == reload}, {30, 113})
        weapon = graph.graph(0x187F)
        volleys = [weapon.node(position) for position in (45, 56)]
        self.assertTrue(all(node.field("m_24E351A4") is None for node in volleys))
        self.assertIsNotNone(graph.graph(0x0254).node(25).field("m_24E351A4"))
        pair_delay = weapon.node(53).field("m_timeout")["m_value"]
        recovery = weapon.node(1).field("m_value")["m_value"]
        self.assertEqual((pair_delay, recovery), (0.2, 1.3))
        definitions = [
            int(node.field("m_projectileEntity")["m_entityDef"]["m_entityDef"], 16)
            for node in volleys
        ]
        self.assertEqual(definitions, [0x0400000000002D35, 0x04000000000032B4])

        baseline = None
        for mode in ("held", "held_with_reload", "repress", "repress_with_reload", "reload_only"):
            with self.subTest(mode=mode):
                run, shooter, _enemy, health = self.arena(offset=0, distance=100)
                instance = next(item for item in shooter.body_script.component.instances.values()
                                if item.graph.index == weapon.index)
                emitted = []
                for frame in range(500):
                    fire = mode.startswith("held") or (mode.startswith("repress") and frame % 2 == 0)
                    buttons = FIRE if fire else 0
                    if "reload" in mode and frame % 3 == 1:
                        buttons |= reload
                    run.step({shooter: (0, 0, buttons)})
                    for rocket in run.match.combat.rockets:
                        if not any(rocket is prior for _, prior in emitted):
                            emitted.append((frame, rocket))
                self.assertEqual(health.total, health.maximum)
                signature = [(frame, rocket.definition) for frame, rocket in emitted]
                counters = [instance.node_state(node).counter for node in volleys]
                if mode == "reload_only":
                    self.assertEqual((signature, counters), ([], [0, 0]))
                    continue
                self.assertEqual([definition for _, definition in signature], definitions * 6)
                self.assertEqual(counters, [6, 6])
                self.assertTrue(all(rocket.owner_body == shooter.body for _, rocket in emitted))
                for index, ((previous, _), (current, _)) in enumerate(zip(signature, signature[1:])):
                    expected = pair_delay if index % 2 == 0 else recovery
                    self.assertAlmostEqual((current - previous) * 0.016, expected, delta=0.016)
                if baseline is None:
                    baseline = signature
                else:
                    self.assertEqual(signature, baseline)

    def test_close_hypersphere_blasts_damage_sigma_without_self_impulse_or_charge(self):
        run, shooter, enemy, enemy_health = self.arena(offset=0, distance=1.5)
        fight = run.match.combat
        owner = next(target for target in fight._targets() if target.owner is shooter)
        health = fight.vitals_of(owner)
        script = shooter.body_script
        script.ult_seconds = 0
        script.set_ult_charge(0)
        with (
            patch.object(
                shooter.mover, "add_external_impulse", wraps=shooter.mover.add_external_impulse,
            ) as own,
            patch.object(
                enemy.mover, "add_external_impulse", wraps=enemy.mover.add_external_impulse,
            ) as hostile,
        ):
            self.pair(run, shooter, enemy)
        own.assert_not_called()
        self.assertEqual(hostile.call_count, 2)
        self.assertEqual(health.now[combat.SHIELDS_POOL], 85)
        self.assertEqual(health.now[combat.HEALTH_POOL], 300)
        self.assertEqual(enemy_health.total, 90)
        self.assertEqual(script.component.entity_var(342).value(), 110)
        self.assertEqual(len(shooter.client.messages(combat.HIT)), 4)

    def test_allied_projected_barrier_absorbs_hypersphere_self_damage(self):
        for breaking in (False, True):
            with self.subTest(breaking=breaking):
                run = Run(KANEZAKA_DM, ("Sigma", "Enemy", "Zarya"))
                run.match.bots.clear()
                run.match._pending_bot_scripts.clear()
                shooter, enemy, zarya = run.players
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                shooter.team, enemy.team, zarya.team = 0, 1, 0
                run.match.switch_hero(shooter, heroes()[SIGMA])
                run.match.switch_hero(zarya, heroes()[0x02E0000000000068])
                x, y, z = shooter.mover.position
                enemy.mover.position = [x, y, z + 1.5]
                zarya.mover.position = [x + 5, y, z]
                fight = run.match.combat
                health = {target.owner: fight.vitals_of(target) for target in fight._targets()}
                script = shooter.body_script
                script.ult_seconds = 0
                script.set_ult_charge(0)
                yaw, pitch = aim(fight._eye(zarya), fight._eye(shooter))
                run.step({zarya: (yaw, pitch, 0x10)})
                bubble = sigma.particle_barrier(shooter)
                self.assertIsNotNone(bubble)
                shields = health[shooter].particle_barriers
                if breaking:
                    shields[sigma.particle_barrier_key(bubble)] = 5
                self.pair(run, shooter, enemy)
                self.assertIs(sigma.particle_barrier(shooter), None if breaking else bubble)
                self.assertEqual(list(shields.values()), [] if breaking else [185])
                self.assertEqual(health[shooter].now[combat.SHIELDS_POOL], 92.5 if breaking else 100)
                self.assertEqual(health[shooter].now[combat.HEALTH_POOL], 300)
                self.assertEqual(health[enemy].total, 90)
                self.assertEqual(script.component.entity_var(342).value(), 110)
                self.assertEqual(zarya.body_script.component.entity_var(87).value(), 1 if breaking else 3)
                self.assertEqual(len(shooter.client.messages(combat.HIT)), 4)
                self.assertFalse(fight.rockets)

    def test_lethal_hypersphere_self_blast_cancels_second_sphere(self):
        run, shooter, enemy, enemy_health = self.arena(offset=0, distance=1.5)
        fight = run.match.combat
        owner = next(target for target in fight._targets() if target.owner is shooter)
        health = fight.vitals_of(owner)
        health.now[combat.SHIELDS_POOL] = 0
        health.now[combat.HEALTH_POOL] = 7.5
        script = shooter.body_script
        script.ult_seconds = 0
        script.set_ult_charge(0)
        self.pair(run, shooter, enemy)
        self.assertTrue(health.dead)
        self.assertEqual(health.killer, shooter.body)
        self.assertEqual(enemy_health.total, 145)
        self.assertEqual(script.component.entity_var(342).value(), 55)
        self.assertFalse(fight.rockets)

    def test_close_enemy_grasp_prevents_hypersphere_self_blast_until_it_ends(self):
        run, shooter, defender, _ = self.arena(offset=0, distance=1.5)
        position = list(defender.mover.position)
        run.match.switch_hero(defender, heroes()[SIGMA])
        defender.mover.position = position
        fight = run.match.combat
        health = {target.owner: fight.vitals_of(target) for target in fight._targets()}
        script = shooter.body_script
        script.ult_seconds = 0
        script.set_ult_charge(0)
        yaw, pitch = aim(fight._eye(defender), fight._eye(shooter))
        run.step({defender: (yaw, pitch, 0x08)})
        for frame in range(61):
            run.step({shooter: (0, 0, FIRE if frame == 0 else 0), defender: (yaw, pitch, 0)})
        grasp = self.ability(defender, sigma.GRASP).instance
        self.assertEqual(grasp.vars[21312].value(), 110)
        self.assertEqual(health[shooter].total, 400)
        self.assertEqual(health[defender].total, 400)
        self.assertEqual(script.component.entity_var(342).value(), 0)
        self.assertFalse(shooter.client.messages(combat.HIT))
        self.assertFalse(fight.rockets)

        for _ in range(65):
            run.step({})
        self.assertEqual(health[defender].temporary_shields[sigma.SHIELD_TAG].now, 66)
        self.pair(run, shooter, defender)
        self.assertEqual(health[shooter].total, 385)
        self.assertEqual(health[defender].total, 356)
        self.assertEqual(script.component.entity_var(342).value(), 44)
        self.assertEqual(len(shooter.client.messages(combat.HIT)), 4)
        self.assertFalse(fight.rockets)

    def test_ricochet_airbursts_can_damage_only_the_owner(self):
        run, shooter, _, enemy_health = self.arena(offset=9, distance=8)
        self.wall(run, sideways=False, offset=1.4)
        fight = run.match.combat
        owner = next(target for target in fight._targets() if target.owner is shooter)
        health = fight.vitals_of(owner)
        script = shooter.body_script
        script.ult_seconds = 0
        script.set_ult_charge(0)
        bounced = []
        with patch.object(
            shooter.mover, "add_external_impulse", wraps=shooter.mover.add_external_impulse,
        ) as own:
            for frame in range(90):
                run.step({shooter: (0, 0, FIRE if frame == 0 else 0)})
                for sphere in fight.rockets:
                    if sphere.velocity[2] < 0 and all(sphere is not other for other in bounced):
                        bounced.append(sphere)
        own.assert_not_called()
        self.assertEqual(len(bounced), 2)
        for sphere in bounced:
            self.assertLessEqual(sphere.left, 0)
        self.assertEqual(health.now[combat.SHIELDS_POOL], 85)
        self.assertEqual(health.now[combat.HEALTH_POOL], 300)
        self.assertEqual(enemy_health.total, 200)
        self.assertEqual(script.component.entity_var(342).value(), 0)
        self.assertFalse(shooter.client.messages(combat.HIT))
        self.assertFalse(fight.rockets)

    def test_hypersphere_self_blast_respects_splash_range_and_static_cover(self):
        for distance, cover in ((2.4, False), (2.4, True), (6, False)):
            with self.subTest(distance=distance, cover=cover):
                run, shooter, enemy, enemy_health = self.arena(offset=0, distance=distance)
                fight = run.match.combat
                owner = next(target for target in fight._targets() if target.owner is shooter)
                health = fight.vitals_of(owner)
                script = shooter.body_script
                script.ult_seconds = 0
                script.set_ult_charge(0)
                z = shooter.mover.position[2]
                yaw, pitch = aim(fight._eye(shooter), fight._eye(enemy))
                run.step({shooter: (yaw, pitch, FIRE)})
                for _ in range(35):
                    if any(rocket.position[2] > z + 1.2 for rocket in fight.rockets):
                        break
                    run.step({shooter: (yaw, pitch, 0)})
                sphere, = fight.rockets
                self.assertGreater(sphere.position[2], z + 1.2)
                self.assertEqual(enemy_health.total, 200)
                if cover:
                    # Isolate blast LOS by placing the wall behind the in-flight sphere.
                    self.wall(run, sideways=False, offset=1)
                for _ in range(30):
                    run.step({shooter: (yaw, pitch, 0)})
                    if sphere not in fight.rockets:
                        break
                self.assertNotIn(sphere, fight.rockets)
                self.assertEqual(enemy_health.total, 145)
                self.assertEqual(script.component.entity_var(342).value(), 55)
                if cover or distance == 6:
                    self.assertEqual(health.total, 400)
                else:
                    self.assertLess(health.total, 400)
                    self.assertGreater(health.total, 392.5)

    def test_hypersphere_final_wall_contact_retires_without_airburst(self):
        run, shooter, _, enemy_health = self.arena(offset=9, distance=8)
        self.wall(run, sideways=False, offset=1.4)
        fight = run.match.combat
        owner = next(target for target in fight._targets() if target.owner is shooter)
        owner_health = fight.vitals_of(owner)
        launched = {}
        with patch.object(fight, "_explode", wraps=fight._explode) as explode:
            for frame in range(65):
                run.step({shooter: (0, 0, FIRE if frame == 0 else 0)})
                for sphere in fight.rockets:
                    if sphere.definition not in launched:
                        self.assertEqual(sphere.bounces, 100)
                        sphere.bounces = 0
                        launched[sphere.definition] = sphere
        self.assertEqual(len(launched), 2)
        for sphere in launched.values():
            self.assertEqual(sphere.bounces, -1)
            self.assertGreater(sphere.left, 0)
            self.assertLess(sphere.velocity[2], 0)
        explode.assert_not_called()
        self.assertFalse(fight.rockets)
        self.assertEqual(owner_health.total, 400)
        self.assertEqual(enemy_health.total, 200)

    def test_hyperspheres_use_normalized_wall_bounce_and_fractional_gravity(self):
        from test_game_mover import box

        run, shooter, enemy, vitals = self.arena(offset=0, distance=-2)
        x, y, z = shooter.mover.position
        floor = [(-5000, y, -5000, -5000, y, 5000, 5000, y, 5000),
                 (-5000, y, -5000, 5000, y, 5000, 5000, y, -5000)]
        run.match.collision_world = World(floor + box(x - 4, x + 4, y, y + 4, z + 6, z + 7))
        fight = run.match.combat
        bounced = set()
        sweep = fight._sweep_projectile

        def observe(rocket, duration, velocity):
            budget = rocket.bounces
            remaining = sweep(rocket, duration, velocity)
            if budget == 100 and rocket.bounces == 99:
                self.assertGreater(remaining, 0)
                self.assertLess(remaining, duration)
                vx, vy, vz = velocity
                vy -= rocket.gravity * (duration - remaining)
                self.assertAlmostEqual(rocket.velocity[1], vy / hypot(vx, vy, vz), places=6)
                self.assertEqual(rocket.speed, 1.0)
                bounced.add(rocket.definition)
            return remaining

        with patch.object(fight, "_sweep_projectile", side_effect=observe):
            for frame in range(65):
                run.step({shooter: (0, 0, FIRE if frame == 0 else 0)})
                for rocket in fight.rockets:
                    if rocket.bounces == 99:
                        self.assertLess(rocket.velocity[2], 0)
                        self.assertLess(rocket.position[2], z + 5.95)
                        self.assertGreater(rocket.position[2], z + 5.4)
                        self.assertLess(rocket.left, 0.44)
        self.assertEqual(len(bounced), 2)
        self.assertEqual(vitals.total, 200)
        self.assertFalse(fight.rockets)
        self.assertFalse(shooter.client.messages(combat.HIT))
        self.assertLess(enemy.mover.position[2], z)

    def test_hypersphere_radius_turns_a_centre_ray_miss_into_a_direct_hit(self):
        run, shooter, enemy, vitals = self.arena(offset=0, distance=6)
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is enemy)
        enemy.mover.position[0] += target.radius + 0.1
        x, y, z = shooter.mover.position
        yaw, pitch = aim(fight._eye(shooter), (x, y + 1, z + 6))
        for frame in range(65):
            run.step({shooter: (yaw, pitch, FIRE if frame == 0 else 0)})
        self.assertEqual(vitals.total, 90)

    def test_airburst_does_not_hit_a_body_outside_the_explosion(self):
        run, shooter, _, vitals = self.arena(offset=4)
        self.pair(run, shooter)
        self.assertEqual(vitals.total, 200.0)

    def test_cryo_freeze_blocks_sigma_damage_impulse_stun_and_charge_until_cancel(self):
        run, shooter, mei, _ = self.arena(offset=0, distance=1.5)
        position = list(mei.mover.position)
        run.match.switch_hero(mei, heroes()[0x02E00000000000DD])
        mei.mover.position = position
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is mei)
        vitals = fight.vitals_of(target)
        yaw, pitch = aim(fight._eye(shooter), (position[0], position[1] + 0.9, position[2]))
        run.step({})
        run.step({mei: (0, 0, 0x08)})
        self.assertEqual(sigma.body_protection(mei), "cryo")
        script = shooter.body_script
        script.ult_seconds = 0
        script.set_ult_charge(0)
        with patch.object(fight, "_damage", wraps=fight._damage) as damage:
            for frame in range(151):
                buttons = {0: 0x800, 36: FIRE, 93: 0x10}.get(frame, 0)
                run.step({shooter: (yaw, pitch, buttons)})
                self.assertFalse(mei.mover.external_impulse)
                self.assertFalse(fight.is_stunned(mei))
        self.assertCountEqual([c.args[2] for c in damage.call_args_list if c.args[1].owner is mei],
                              [25, 30, 25, 30, 30, 40, 30])
        self.assertEqual(vitals.total, 250)
        self.assertEqual(script.component.entity_var(342).value(), 0)
        self.assertFalse(shooter.client.messages(combat.HIT))
        run.step({mei: (0, 0, 0x02)})
        for _ in range(20):
            run.step({})
        self.assertIsNone(sigma.body_protection(mei))
        self.pair(run, shooter, target=mei)
        self.assertEqual(vitals.total, 140)
        self.assertEqual(script.component.entity_var(342).value(), 110)

    def test_hyperspheres_pass_through_wraith_form_and_fade(self):
        for hero, graph_index in ((0x02E0000000000002, 0x0020), (0x02E00000000001A2, 0x0F7C)):
            with self.subTest(hero=f"{hero:016X}"):
                run = Run(KANEZAKA_DM, ("Sigma", "Phased", "Behind"))
                run.match.bots.clear()
                run.match._pending_bot_scripts.clear()
                shooter, phased, behind = run.players
                run.match.switch_hero(shooter, heroes()[SIGMA])
                run.match.switch_hero(phased, heroes()[hero])
                x, y, z = shooter.mover.position
                phased.mover.position = [x, y, z + 3]
                behind.mover.position = [x, y, z + 9]
                fight = run.match.combat
                health = {t.owner: fight.vitals_of(t) for t in fight._targets()}
                script = shooter.body_script
                script.ult_seconds = 0
                script.set_ult_charge(0)
                yaw, pitch = aim(fight._eye(shooter), (x, y + 1, z + 9))
                run.step({})
                run.step({phased: (0, 0, 0x08)})
                self.assertEqual(sigma.body_protection(phased), "phase")
                for frame in range(42):
                    run.step({shooter: (yaw, pitch, FIRE if frame == 0 else 0)})
                self.assertEqual(health[phased].total, health[phased].maximum)
                self.assertEqual(health[behind].total, 90)
                self.assertEqual(script.component.entity_var(342).value(), 110)
                self.assertEqual(len(shooter.client.messages(combat.HIT)), 4)
                expected_charge = 220
                if graph_index == 0x0020:
                    for frame in range(95):
                        run.step({shooter: (yaw, pitch, 0x10 if frame == 0 else 0)})
                    self.assertEqual(health[phased].total, health[phased].maximum)
                    self.assertFalse(fight.is_stunned(phased))
                    self.assertEqual(health[behind].total, 20)
                    self.assertTrue(fight.is_stunned(behind))
                    self.assertEqual(script.component.entity_var(342).value(), 180)
                    expected_charge += 70
                    self.assertEqual(sigma.body_protection(phased), "phase")
                    run.step({phased: (0, 0, 0x08)})
                    self.assertIsNone(sigma.body_protection(phased))
                for _ in range(65):
                    run.step({})
                self.assertIsNone(sigma.body_protection(phased))
                instance = next(i for i in phased.body_script.component.instances.values()
                                if i.graph.index == graph_index)
                self.assertGreater(instance.vars[61].value(), 0)
                run.step({phased: (0, 0, 0x08)})
                self.assertIsNone(sigma.body_protection(phased))
                self.pair(run, shooter, target=phased)
                self.assertEqual(health[phased].total, health[phased].maximum - 110)
                self.assertEqual(script.component.entity_var(342).value(), expected_charge)

    def test_protection_activated_after_accretion_launch_routes_the_impact(self):
        for hero, protection in ((0x02E00000000001A2, "phase"), (0x02E00000000000DD, "cryo")):
            with self.subTest(protection=protection):
                run = Run(KANEZAKA_DM, ("Sigma", "Protected", "Behind"))
                run.match.bots.clear()
                run.match._pending_bot_scripts.clear()
                shooter, target, behind = run.players
                run.match.switch_hero(shooter, heroes()[SIGMA])
                run.match.switch_hero(target, heroes()[hero])
                x, y, z = shooter.mover.position
                target.mover.position = [x, y, z + 6]
                behind.mover.position = [x, y, z + 9]
                fight = run.match.combat
                health = {entry.owner: fight.vitals_of(entry) for entry in fight._targets()}
                script = shooter.body_script
                script.ult_seconds = 0
                script.set_ult_charge(0)
                yaw, pitch = aim(fight._eye(shooter), (x, y + 1, z + 9))
                run.step({shooter: (yaw, pitch, 0x10)})
                for _ in range(65):
                    if fight.rockets:
                        break
                    run.step({shooter: (yaw, pitch, 0)})
                rock, = fight.rockets
                self.assertIs(rock.shooter, shooter)
                self.assertIsNone(sigma.body_protection(target))
                run.step({target: (0, 0, 0x08)})
                self.assertEqual(sigma.body_protection(target), protection)
                self.assertIn(rock, fight.rockets)
                for _ in range(40):
                    run.step({})
                    if rock not in fight.rockets:
                        break
                self.assertNotIn(rock, fight.rockets)
                self.assertEqual(health[target].total, health[target].maximum)
                self.assertFalse(fight.is_stunned(target))
                self.assertFalse(target.mover.external_impulse)
                expected = 70 if protection == "phase" else 0
                self.assertEqual(200 - health[behind].total, expected)
                self.assertEqual(fight.is_stunned(behind), protection == "phase")
                self.assertEqual(script.component.entity_var(342).value(), expected)
                ability = self.ability(shooter, sigma.ACCRETION)
                for _ in range(65):
                    if ability.remaining() > 0:
                        break
                    run.step({})
                self.assertGreater(ability.remaining(), 2)
                run.step({shooter: (yaw, pitch, 0x10)})
                for _ in range(65):
                    run.step({shooter: (yaw, pitch, 0)})
                    self.assertFalse(fight.rockets)
                self.assertEqual(script.component.entity_var(342).value(), expected)

    def test_projected_barrier_cast_during_accretion_flight_checks_ally_at_impact(self):
        for same_team in (False, True):
            with self.subTest(same_team=same_team):
                run = Run(KANEZAKA_DM, ("Sigma", "Target", "Zarya"))
                run.match.bots.clear()
                run.match._pending_bot_scripts.clear()
                shooter, target, zarya = run.players
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                shooter.team, target.team, zarya.team = 0, 1, int(same_team)
                run.match.switch_hero(shooter, heroes()[SIGMA])
                run.match.switch_hero(zarya, heroes()[0x02E0000000000068])
                x, y, z = shooter.mover.position
                target.mover.position = [x, y, z + 8]
                zarya.mover.position = [x + 5, y, z + 8]
                fight = run.match.combat
                health = fight.vitals_of(next(entry for entry in fight._targets() if entry.owner is target))
                script = shooter.body_script
                script.ult_seconds = 0
                script.set_ult_charge(0)
                aim_rock = aim(fight._eye(shooter), (x, y + 1, z + 8))
                aim_barrier = aim(fight._eye(zarya), fight._eye(target))
                run.step({shooter: (*aim_rock, 0x10)})
                for _ in range(65):
                    if fight.rockets:
                        break
                    run.step({shooter: (*aim_rock, 0)})
                rock, = fight.rockets
                self.assertIs(rock.shooter, shooter)
                self.assertIsNone(sigma.particle_barrier(target))
                run.step({zarya: (*aim_barrier, 0x10)})
                self.assertEqual(sigma.particle_barrier(target) is not None, same_team)
                self.assertIn(rock, fight.rockets)
                for _ in range(40):
                    run.step({})
                    if rock not in fight.rockets:
                        break
                self.assertNotIn(rock, fight.rockets)
                expected_damage = 0 if same_team else 70
                self.assertEqual(health.maximum - health.total, expected_damage)
                self.assertEqual(fight.is_stunned(target), not same_team)
                self.assertEqual(script.component.entity_var(342).value(), expected_damage)
                self.assertEqual(list(health.particle_barriers.values()), [130] if same_team else [])
                energy = zarya.body_script.component.entity_var(87).value() or 0
                self.assertAlmostEqual(energy, 14 if same_team else 0)

    def test_passive_shield_recovery_restarts_on_the_respawned_body(self):
        from math import ceil

        run, shooter, enemy, _ = self.arena(offset=0, distance=6)
        fight = run.match.combat
        old_body = shooter.body
        old = fight.vitals_of(next(target for target in fight._targets() if target.owner is shooter))
        x, y, z = shooter.mover.position
        facing = aim(fight._eye(enemy), (x, y + 0.9, z))
        for _ in range(180):
            run.step({enemy: (*facing, FIRE)})
            if old.dead:
                break
        self.assertTrue(old.dead)
        self.assertEqual(old.killer, enemy.body)
        self.assertEqual(old.total, 0)
        deadline, = (when for when, owner in fight.respawns if owner is shooter)
        for _ in range(215):
            run.step({})
        self.assertEqual(shooter.body, old_body)
        self.assertEqual(old.now[combat.SHIELDS_POOL], 0)

        run.frame = ceil(deadline / combat.TICK) - 1
        run.step({})
        self.assertNotEqual(shooter.body, old_body)
        self.assertNotIn(old_body, fight.vitals)
        respawned = fight.vitals_of(next(target for target in fight._targets() if target.owner is shooter))
        self.assertEqual(respawned.now[combat.HEALTH_POOL], 300)
        self.assertEqual(respawned.now[combat.SHIELDS_POOL], 100)
        self.assertFalse(respawned.dead)

        x, y, z = shooter.mover.position
        enemy.mover.position = [x, y, z + 6]
        facing = aim(fight._eye(enemy), (x, y + 0.9, z))
        run.step({enemy: (*facing, FIRE)})
        self.assertEqual(respawned.now[combat.SHIELDS_POOL], 80)
        for _ in range(187):
            run.step({})
        self.assertEqual(respawned.now[combat.SHIELDS_POOL], 80)
        for _ in range(25):
            run.step({})
        self.assertAlmostEqual(respawned.now[combat.SHIELDS_POOL], 91.76)
        self.assertEqual(respawned.now[combat.HEALTH_POOL], 300)
        self.assertEqual(old.total, 0)

    def test_enemy_rifle_interrupts_base_shield_recovery(self):
        run, sigma_player, enemy, _ = self.arena(offset=0, distance=6)
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is sigma_player)
        vitals = fight.vitals_of(target)
        feet = sigma_player.mover.position
        yaw, pitch = aim(fight._eye(enemy), (feet[0], feet[1] + 0.9, feet[2]))
        shield = combat.SHIELDS_POOL
        health = combat.HEALTH_POOL
        shield_max = vitals.most[shield]
        vitals.now[health] -= 20  # keep a separate deficit throughout the recovery
        health_before = vitals.now[health]

        run.step({enemy: (yaw, pitch, FIRE)})
        self.assertEqual(vitals.now[shield], shield_max - 20)
        for _ in range(187):
            run.step({})
        self.assertEqual(vitals.now[shield], shield_max - 20)
        for _ in range(25):
            run.step({})
        self.assertAlmostEqual(vitals.now[shield], shield_max - 8.24)

        run.step({enemy: (yaw, pitch, FIRE)})
        self.assertAlmostEqual(vitals.now[shield], shield_max - 27.76)
        for _ in range(187):
            run.step({})
        self.assertAlmostEqual(vitals.now[shield], shield_max - 27.76)
        for _ in range(70):
            run.step({})
        self.assertEqual(vitals.now[shield], shield_max)
        self.assertEqual(vitals.now[health], health_before)
        self.assertFalse(vitals.temporary_shields)

    def test_symmetra_primary_bypasses_active_grasp_and_stops_on_release(self):
        run, player, enemy, _ = self.arena(offset=0, distance=6)
        position = list(enemy.mover.position)
        self.assertTrue(run.match.switch_hero(enemy, heroes()[symmetra_beam.HERO]))
        enemy.mover.position = position
        fight = run.match.combat
        vitals = fight.vitals_of(next(t for t in fight._targets() if t.owner is player))
        yaw, pitch = aim(fight._eye(enemy), fight._eye(player))
        run.step({})
        weapon = next(i for i in enemy.body_script.component.instances.values()
                      if i.graph.index == symmetra_beam.WEAPON)
        for frame in range(30):
            run.step({player: (0, 0, 8 if frame == 0 else 0), enemy: (yaw, pitch, FIRE)})
        grasp = self.ability(player, sigma.GRASP).instance
        self.assertTrue(grasp.states[1].active)
        self.assertEqual(grasp.vars[21312].value(), 0)
        self.assertAlmostEqual(vitals.total, 400 - 30 * 60 * combat.TICK)
        self.assertEqual(weapon.find_var(0, 16627).value(), expr.Entity(player.body))
        self.assertTrue(weapon.states[86].active)
        after = vitals.total
        for _ in range(126):
            run.step({})
        self.assertAlmostEqual(vitals.total, after)
        self.assertFalse(vitals.temporary_shields)
        self.assertNotEqual(weapon.find_var(0, 16627).value(), expr.Entity(player.body))

    def launch_symmetra_orb(self, run, player, enemy, *, hold=90, grasp_yaw=None, miss=False):
        position = list(enemy.mover.position)
        self.assertTrue(run.match.switch_hero(enemy, heroes()[symmetra_beam.HERO]))
        enemy.mover.position = position
        fight = run.match.combat
        yaw, pitch = aim(fight._eye(enemy), fight._eye(player))
        if miss:
            yaw = yaw % 65536 - 32768
        run.step({})
        for _ in range(hold):
            run.step({enemy: (yaw, pitch, SECONDARY)})
            self.assertFalse(fight.rockets)
        commands = {enemy: (yaw, pitch, 0)}
        if grasp_yaw is not None:
            commands[player] = (grasp_yaw, 0, 8)
        run.step(commands)
        rocket, = fight.rockets
        self.assertEqual(rocket.definition, 0x04000000000026F1)
        return rocket

    def test_symmetra_orb_release_direct_hit_miss_cover_and_ally(self):
        for reason in ("hit", "short_charge", "miss", "cover", "team"):
            with self.subTest(reason=reason):
                run, player, enemy, _ = self.arena(offset=0, distance=6)
                fight = run.match.combat
                vitals = fight.vitals_of(next(t for t in fight._targets() if t.owner is player))
                if reason == "cover":
                    self.wall(run, sideways=False, offset=3)
                if reason == "team":
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    enemy.team = player.team
                short = reason == "short_charge"
                rocket = self.launch_symmetra_orb(run, player, enemy, hold=1 if short else 90,
                                                 miss=reason == "miss")
                amount = 6.81 if short else 60
                self.assertAlmostEqual(rocket.direct.amount, amount, places=5)
                self.assertAlmostEqual(rocket.blast.amount, amount, places=5)
                self.assertEqual(rocket.speed, 25)
                for _ in range(35):
                    run.step({})
                expected = amount * 2 if reason in ("hit", "short_charge") else 0
                self.assertAlmostEqual(vitals.total, 400 - expected, places=5)
                self.assertEqual(rocket in fight.rockets, reason in ("miss", "team"))

    def test_symmetra_orb_front_grasp_converts_and_rear_grasp_does_not(self):
        for front in (True, False):
            with self.subTest(front=front):
                run, player, enemy, _ = self.arena(offset=0, distance=6)
                fight = run.match.combat
                vitals = fight.vitals_of(next(t for t in fight._targets() if t.owner is player))
                facing = 0 if front else -32768
                rocket = self.launch_symmetra_orb(run, player, enemy, grasp_yaw=facing)
                grasp = self.ability(player, sigma.GRASP).instance
                for _ in range(35):
                    run.step({player: (facing, 0, 0)})
                self.assertNotIn(rocket, fight.rockets)
                self.assertTrue(grasp.states[1].active)
                self.assertEqual(grasp.vars[21312].value(), 120 if front else 0)
                self.assertEqual(vitals.total, 400 if front else 280)
                self.assertFalse(vitals.temporary_shields)
                self.assertEqual(bool(enemy.client.messages(combat.HIT)), not front)
                for _ in range(100):
                    run.step({})
                self.assertFalse(grasp.states[1].active)
                if front:
                    self.assertEqual(vitals.temporary_shields[sigma.SHIELD_TAG].now, 72)
                    self.assertEqual(vitals.total, 472)
                else:
                    self.assertFalse(vitals.temporary_shields)

    def test_symmetra_orb_lifetime_bounds_the_last_flight_segment(self):
        for distance, expected in ((624, 120), (628, 0)):
            with self.subTest(distance=distance):
                run, player, enemy, _ = self.arena(offset=0, distance=distance)
                fight = run.match.combat
                vitals = fight.vitals_of(next(t for t in fight._targets() if t.owner is player))
                orb = self.launch_symmetra_orb(run, player, enemy)
                self.assertEqual(orb.left, 25)
                run.step({})
                self.assertIn(orb, fight.rockets)
                expiry = run.frame + 1600
                run.match.update(expiry * combat.TICK, expiry, frames=False)
                self.assertNotIn(orb, fight.rockets)
                self.assertEqual(vitals.total, 400 - expected)
                self.assertLessEqual(orb.flown, 25)

    def test_lethal_symmetra_orb_behind_grasp_cleans_up_through_respawn(self):
        run, player, enemy, _ = self.arena(offset=0, distance=6)
        fight = run.match.combat
        vitals = fight.vitals_of(next(t for t in fight._targets() if t.owner is player))
        vitals.now[combat.HEALTH_POOL] = 65
        vitals.now[combat.SHIELDS_POOL] = 0
        old_body = player.body
        player.body_script.ult_seconds = 0
        player.body_script.set_ult_charge(73)
        self.launch_symmetra_orb(run, player, enemy, grasp_yaw=-32768)
        grasp = self.ability(player, sigma.GRASP).instance
        self.assertTrue(grasp.states[1].active)
        for _ in range(35):
            run.step({player: (-32768, 0, 0)})
        self.assertTrue(vitals.dead)
        self.assertEqual(vitals.killer, enemy.body)
        self.assertFalse(grasp.states[1].active)
        self.assertNotIn(old_body, fight.grasp_grants)
        self.assertFalse(vitals.temporary_shields)

        tick = run.frame + 650
        run.match.update(tick * combat.TICK, tick, frames=False)
        self.assertNotEqual(player.body, old_body)
        self.assertFalse(fight.is_dead(player))
        self.assertEqual(player.body_script.component.entity_var(342).value(), 73)
        self.assertFalse(fight.rockets)

    def test_symmetra_primary_respects_cover_range_team_and_aim(self):
        for reason in ("cover", "range", "team", "aim"):
            with self.subTest(reason=reason):
                run, player, enemy, _ = self.arena(offset=0, distance=14 if reason == "range" else 6)
                position = list(enemy.mover.position)
                self.assertTrue(run.match.switch_hero(enemy, heroes()[symmetra_beam.HERO]))
                enemy.mover.position = position
                fight = run.match.combat
                if reason == "cover":
                    self.wall(run, sideways=False, offset=3)
                if reason == "team":
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    enemy.team = player.team
                yaw, pitch = aim(fight._eye(enemy), fight._eye(player))
                if reason == "aim":
                    yaw = yaw % 65536 - 32768
                vitals = fight.vitals_of(next(t for t in fight._targets() if t.owner is player))
                run.step({})
                for _ in range(12):
                    run.step({enemy: (yaw, pitch, FIRE)})
                self.assertEqual(vitals.total, 400)

    def test_symmetra_primary_stands_over_cover_that_blocks_her_crouched_beam(self):
        run, player, enemy, _ = self.arena(offset=0, distance=6)
        position = list(enemy.mover.position)
        self.assertTrue(run.match.switch_hero(enemy, heroes()[symmetra_beam.HERO]))
        enemy.mover.position = position
        self.wall(run, sideways=False, offset=3, height=1.1)
        fight = run.match.combat
        vitals = fight.vitals_of(next(t for t in fight._targets() if t.owner is player))
        yaw, _ = aim(fight._eye(enemy), fight._eye(player))
        run.step({enemy: (yaw, 0, FIRE)})
        self.assertAlmostEqual(fight._eye(enemy)[1] - enemy.mover.position[1], 1.3)
        self.assertAlmostEqual(vitals.total, 399.04)
        for _ in range(20):
            run.step({enemy: (yaw, 0, CROUCH)})
        self.assertTrue(enemy.mover.crouched)
        self.assertAlmostEqual(fight._eye(enemy)[1] - enemy.mover.position[1], 0.91)
        for _ in range(5):
            run.step({enemy: (yaw, 0, CROUCH | FIRE)})
        self.assertAlmostEqual(vitals.total, 399.04)

    def test_accretion_interrupts_actual_symmetra_primary_until_stun_ends(self):
        run, player, enemy, _ = self.arena(offset=0, distance=6)
        position = list(enemy.mover.position)
        self.assertTrue(run.match.switch_hero(enemy, heroes()[symmetra_beam.HERO]))
        enemy.mover.position = position
        fight = run.match.combat
        own = fight.vitals_of(next(t for t in fight._targets() if t.owner is player))
        other = fight.vitals_of(next(t for t in fight._targets() if t.owner is enemy))
        fire_yaw, fire_pitch = aim(fight._eye(enemy), fight._eye(player))
        rock_yaw, rock_pitch = aim(fight._eye(player), fight._eye(enemy))
        run.step({})
        for _ in range(10):
            run.step({enemy: (fire_yaw, fire_pitch, FIRE)})
        self.assertAlmostEqual(own.total, 400 - 10 * 60 * combat.TICK)
        run.step({player: (rock_yaw, rock_pitch, 0x10), enemy: (fire_yaw, fire_pitch, FIRE)})
        for _ in range(75):
            if fight.is_stunned(enemy):
                break
            run.step({player: (rock_yaw, rock_pitch, 0), enemy: (fire_yaw, fire_pitch, FIRE)})
        self.assertTrue(fight.is_stunned(enemy))
        self.assertEqual(other.total, 225 - 70)
        held_before = own.total
        for _ in range(35):
            run.step({enemy: (fire_yaw, fire_pitch, FIRE)})
        self.assertTrue(fight.is_stunned(enemy))
        self.assertEqual(own.total, held_before)
        for _ in range(30):
            run.step({})
        self.assertFalse(fight.is_stunned(enemy))
        fire_yaw, fire_pitch = aim(fight._eye(enemy), fight._eye(player))
        run.step({enemy: (fire_yaw, fire_pitch, FIRE)})
        self.assertLess(own.total, held_before)

    def test_accretion_cancels_symmetra_orb_charge_until_a_fresh_press(self):
        run, player, enemy, _ = self.arena(offset=0, distance=6)
        position = list(enemy.mover.position)
        self.assertTrue(run.match.switch_hero(enemy, heroes()[symmetra_beam.HERO]))
        enemy.mover.position = position
        fight = run.match.combat
        own = fight.vitals_of(next(t for t in fight._targets() if t.owner is player))
        other = fight.vitals_of(next(t for t in fight._targets() if t.owner is enemy))
        orb_definition = 0x04000000000026F1

        def step(attack=SECONDARY, cast=0):
            fire = aim(fight._eye(enemy), fight._eye(player))
            rock = aim(fight._eye(player), fight._eye(enemy))
            run.step({player: (*rock, cast), enemy: (*fire, attack)})

        step(attack=0)
        for _ in range(20):
            step()
        step(cast=0x10)
        for _ in range(75):
            if fight.is_stunned(enemy):
                break
            step()
            self.assertFalse(any(r.definition == orb_definition for r in fight.rockets))
        self.assertTrue(fight.is_stunned(enemy))
        self.assertEqual(other.total, 155)
        for _ in range(20):
            step()
            self.assertFalse(fight.rockets)
        for _ in range(35):
            step(attack=0)
            self.assertFalse(fight.rockets)
        self.assertFalse(fight.is_stunned(enemy))
        self.assertEqual(own.total, 400)
        for _ in range(90):
            step()
        self.assertFalse(fight.rockets)
        step(attack=0)
        orb, = fight.rockets
        self.assertEqual(orb.definition, orb_definition)
        for _ in range(45):
            step(attack=0)
        self.assertEqual(own.total, 280)

    def test_helix_and_ordinary_shield_recovery_keep_the_same_order_during_a_tick_gap(self):
        for skipped in (False, True):
            with self.subTest(skipped=skipped):
                run, defender, enemy, _ = self.arena(offset=0, distance=7)
                fight = run.match.combat
                vitals = fight.vitals_of(next(t for t in fight._targets() if t.owner is defender))
                x, y, z = defender.mover.position
                yaw, pitch = aim(fight._eye(enemy), (x, y + 0.9, z))
                run.step({enemy: (yaw, pitch, FIRE)})
                first_hit = run.frame
                self.assertEqual(vitals.total, 380)
                owner_frame = defender.body_script.frame

                launch = first_hit + 180
                run.match.update(launch * combat.TICK, launch, frames=False)
                enemy.take_commands([Command(launch, yaw=yaw, pitch=pitch, buttons=SECONDARY, extra=0)])
                rocket, = fight.rockets
                run.match.update((launch + 1) * combat.TICK, launch + 1, frames=False)
                self.assertIn(rocket, fight.rockets)
                self.assertEqual(vitals.total, 380)

                end = first_hit + 194
                ticks = (end,) if skipped else range(launch + 2, end + 1)
                for tick in ticks:
                    run.match.update(tick * combat.TICK, tick, frames=False)
                self.assertNotIn(rocket, fight.rockets)
                self.assertAlmostEqual(vitals.now[combat.HEALTH_POOL], 260.24)
                self.assertEqual(vitals.now[combat.SHIELDS_POOL], 0)
                self.assertEqual(vitals.shield_recovery_tick, first_hit + 375.5)

                boundary = first_hit + 375
                run.match.update(boundary * combat.TICK, boundary, frames=False)
                self.assertEqual(vitals.now[combat.SHIELDS_POOL], 0)
                run.match.update((boundary + 1) * combat.TICK, boundary + 1, frames=False)
                self.assertAlmostEqual(vitals.now[combat.SHIELDS_POOL], 0.24)
                self.assertAlmostEqual(vitals.total, 260.48)
                capped = first_hit + 600
                run.match.update(capped * combat.TICK, capped, frames=False)
                self.assertEqual(vitals.now[combat.SHIELDS_POOL], 100)
                self.assertAlmostEqual(vitals.now[combat.HEALTH_POOL], 260.24)
                hud = defender.body_script.component.entity_var(1732).value()
                self.assertAlmostEqual(hud, vitals.total, places=4)
                self.assertIsNone(vitals.shield_recovery_tick)
                self.assertEqual(defender.body_script.frame, owner_frame)

    def test_zarya_particle_grenade_explodes_once_or_converts_to_grasp_shields(self):
        for absorbing in (False, True):
            with self.subTest(absorbing=absorbing):
                run, player, enemy, _ = self.arena(offset=0, distance=6)
                position = list(enemy.mover.position)
                run.match.switch_hero(enemy, heroes()[0x02E0000000000068])
                enemy.mover.position = position
                fight = run.match.combat
                vitals = fight.vitals_of(next(t for t in fight._targets() if t.owner is player))
                run.step({})
                run.step({player: (0, 0, 0x08 if absorbing else 0)})
                grasp = self.ability(player, sigma.GRASP).instance
                x, y, z = player.mover.position
                yaw, pitch = aim(fight._eye(enemy), (x, y + 1, z))
                run.step({enemy: (yaw, pitch, 0x02)})
                rocket, = fight.rockets
                self.assertAlmostEqual(rocket.gravity, 9.8, places=5)
                self.assertEqual(rocket.direct.amount, 0)
                self.assertEqual(rocket.blast.amount, 47)
                initial_y_velocity = rocket.velocity[1]
                run.step({})
                self.assertLess(rocket.velocity[1], initial_y_velocity)
                for _ in range(155):
                    run.step({})
                self.assertFalse(fight.rockets)
                if absorbing:
                    self.assertEqual(grasp.vars[21312].value(), 47)
                    self.assertAlmostEqual(vitals.temporary_shields[sigma.SHIELD_TAG].now, 28.2, places=5)
                    self.assertEqual(sum(vitals.now.values()), 400)
                    self.assertFalse(enemy.client.messages(combat.HIT))
                else:
                    self.assertEqual(vitals.total, 353)
                    self.assertEqual(len(enemy.client.messages(combat.HIT)), 1)

    def test_zarya_particle_barrier_blocks_accretion_control_and_charge(self):
        self.assert_particle_barrier_accretion(breaking=False)

    def test_accretion_breaking_particle_barrier_does_not_leak_damage_or_control(self):
        self.assert_particle_barrier_accretion(breaking=True)

    def assert_particle_barrier_accretion(self, *, breaking):
        run, shooter, zarya, _ = self.arena(offset=0, distance=6)
        position = list(zarya.mover.position)
        run.match.switch_hero(zarya, heroes()[0x02E0000000000068])
        zarya.mover.position = position
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is zarya)
        vitals = fight.vitals_of(target)
        script = shooter.body_script
        script.ult_seconds = 0
        script.set_ult_charge(0)
        run.step({})
        run.step({zarya: (0, 0, 0x08)})
        bubble = sigma.particle_barrier(zarya)
        self.assertIsNotNone(bubble)
        if breaking:
            vitals.particle_barriers[sigma.particle_barrier_key(bubble)] = 40
        yaw, pitch = aim(fight._eye(shooter), (position[0], position[1] + 1, position[2]))
        for frame in range(95):
            run.step({shooter: (yaw, pitch, 0x10 if frame == 0 else 0)})
            self.assertFalse(zarya.mover.external_impulse)
            self.assertFalse(fight.is_stunned(zarya))
        self.assertEqual(list(vitals.particle_barriers.values()), [] if breaking else [130])
        energy = zarya.body_script.component.entity_var(87).value()
        self.assertAlmostEqual(energy, 8 if breaking else 14, places=5)
        self.assertEqual(vitals.total, vitals.maximum)
        self.assertEqual(script.component.entity_var(342).value(), 0)
        if breaking:
            self.assertIsNone(sigma.particle_barrier(zarya))
            run.step({zarya: (0, 0, 0x08)})
            self.assertIsNone(sigma.particle_barrier(zarya))
        for _ in range(40):
            run.step({})
        self.assertIsNone(sigma.particle_barrier(zarya))
        self.assertFalse(vitals.particle_barriers)
        self.pair(run, shooter, target=zarya)
        self.assertEqual(vitals.total, vitals.maximum - 110)
        self.assertEqual(script.component.entity_var(342).value(), 110)

    def test_breaking_hypersphere_does_not_leak_splash_through_particle_barrier(self):
        run = Run(KANEZAKA_DM, ("Sigma", "Second Sigma", "Zarya"))
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        first, second, zarya = run.players
        for shooter in (first, second):
            run.match.switch_hero(shooter, heroes()[SIGMA])
            shooter.body_script.ult_seconds = 0
            shooter.body_script.set_ult_charge(0)
        run.match.switch_hero(zarya, heroes()[0x02E0000000000068])
        x, y, z = first.mover.position
        second.mover.position = [x + 2, y, z]
        zarya.mover.position = [x + 1, y, z + 6]
        fight = run.match.combat
        vitals = fight.vitals_of(next(t for t in fight._targets() if t.owner is zarya))
        run.step({})
        run.step({zarya: (0, 0, 0x08)})
        self.assertIsNotNone(sigma.particle_barrier(zarya))
        directions = {p: aim(fight._eye(p), fight._eye(zarya)) for p in (first, second)}
        for frame in range(61):
            run.step({p: (*direction, FIRE if frame == 0 else 0) for p, direction in directions.items()})
        self.assertFalse(vitals.particle_barriers)
        self.assertAlmostEqual(zarya.body_script.component.entity_var(87).value(), 40, places=5)
        self.assertIsNone(sigma.particle_barrier(zarya))
        self.assertEqual(vitals.total, vitals.maximum)
        self.assertFalse(zarya.mover.external_impulse)
        for shooter in (first, second):
            self.assertEqual(shooter.body_script.component.entity_var(342).value(), 0)
        run.step({zarya: (0, 0, 0x08)})
        self.assertIsNone(sigma.particle_barrier(zarya))

    def test_sigma_hits_charge_zarya_beam_and_energy_decays_after_grace(self):
        run, shooter, zarya, _ = self.arena(offset=0, distance=6)
        position = list(zarya.mover.position)
        run.match.switch_hero(zarya, heroes()[0x02E0000000000068])
        zarya.mover.position = position
        fight = run.match.combat
        health = {t.owner: fight.vitals_of(t) for t in fight._targets()}
        run.step({})
        run.step({zarya: (0, 0, 0x08)})
        yaw, pitch = aim(fight._eye(shooter), fight._eye(zarya))
        run.step({shooter: (yaw, pitch, FIRE)})
        component = zarya.body_script.component
        for _ in range(40):
            if (slot := component.entity_var(87, create=False)) and (slot.value() or 0) >= 22:
                break
            run.step({})
        self.assertAlmostEqual(component.entity_var(87).value(), 22, places=5)
        self.assertEqual(health[zarya].total, health[zarya].maximum)
        x, y, z = shooter.mover.position
        yaw, pitch = aim(fight._eye(zarya), (x, y + 0.9, z))
        for _ in range(30):
            run.step({zarya: (yaw, pitch, FIRE)})
        self.assertAlmostEqual(health[shooter].total, 400 - 46.032, places=4)
        self.assertAlmostEqual(component.entity_var(87).value(), 22, places=5)
        for _ in range(35):
            run.step({})
        self.assertLess(component.entity_var(87).value(), 22)
        self.assertGreater(component.entity_var(87).value(), 21)

    def test_projected_barrier_protects_selected_teammate_and_credits_zarya(self):
        run = Run(KANEZAKA_DM, ("Sigma", "Zarya", "Protected"))
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        shooter, zarya, protected = run.players
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        run.match.switch_hero(shooter, heroes()[SIGMA])
        run.match.switch_hero(zarya, heroes()[0x02E0000000000068])
        protected.team = zarya.team
        x, y, z = shooter.mover.position
        zarya.mover.position = [x + 6, y, z + 6]
        protected.mover.position = [x, y, z + 6]
        fight = run.match.combat
        health = fight.vitals_of(next(t for t in fight._targets() if t.owner is protected))
        script = shooter.body_script
        script.ult_seconds = 0
        script.set_ult_charge(0)
        hostile_yaw, hostile_pitch = aim(fight._eye(zarya), fight._eye(shooter))
        run.step({zarya: (hostile_yaw, hostile_pitch, 0)})
        run.step({zarya: (hostile_yaw, hostile_pitch, 0x10)})
        self.assertIsNone(sigma.particle_barrier(shooter))
        self.assertIsNone(sigma.particle_barrier(protected))
        protected.mover = protected.new_mover()
        protected.mover.position = [x - 32, y, z + 6]
        yaw, pitch = aim(fight._eye(zarya), fight._eye(protected))
        run.step({zarya: (yaw, pitch, 0)})
        run.step({zarya: (yaw, pitch, 0x10)})
        self.assertIsNone(sigma.particle_barrier(protected))
        original_world = run.match.collision_world
        zarya.mover = zarya.new_mover()
        protected.mover = protected.new_mover()
        zarya.mover.position = [x + 6, y, z + 22]
        protected.mover.position = [x, y, z + 22]
        self.wall(run, sideways=True, offset=3)
        yaw, pitch = aim(fight._eye(zarya), fight._eye(protected))
        run.step({zarya: (yaw, pitch, 0)})
        run.step({zarya: (yaw, pitch, 0x10)})
        self.assertIsNone(sigma.particle_barrier(protected))
        run.match.collision_world = original_world
        zarya.mover = zarya.new_mover()
        protected.mover = protected.new_mover()
        shooter.mover.use(world=original_world)
        zarya.mover.position = [x + 6, y, z + 6]
        protected.mover.position = [x, y, z + 6]
        yaw, pitch = aim(fight._eye(zarya), fight._eye(protected))
        run.step({zarya: (yaw, pitch, 0)})
        run.step({zarya: (yaw, pitch, 0x10)})
        bubble = sigma.particle_barrier(protected)
        self.assertIsNotNone(bubble)
        self.assertEqual(bubble.instance.component.entity, zarya.body)
        self.assertIsNone(sigma.particle_barrier(zarya))
        self.pair(run, shooter, target=protected)
        self.assertEqual(health.particle_barriers[sigma.particle_barrier_key(bubble)], 90)
        self.assertEqual(health.total, 200)
        self.assertEqual(script.component.entity_var(342).value(), 0)
        self.assertAlmostEqual(zarya.body_script.component.entity_var(87).value(), 22, places=5)
        self.assertFalse(protected.mover.external_impulse)

        old_body = protected.body
        run.match.switch_hero(protected, protected.hero)
        self.assertNotEqual(protected.body, old_body)
        self.assertIsNone(sigma.particle_barrier(protected))

    def test_overlapping_zarya_barriers_keep_separate_health_and_energy(self):
        run = Run(KANEZAKA_DM, ("Sigma", "Protected", "Caster", "Second", "Third"))
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        first, protected, caster, second, third = run.players
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        attackers = (first, second, third)
        for player in run.players:
            enemy = player in attackers
            player.team = 0 if enemy else 1
            run.match.switch_hero(player, heroes()[SIGMA if enemy else 0x02E0000000000068])
            player.body_script.ult_seconds = 0
            player.body_script.set_ult_charge(0)
        x, y, z = first.mover.position
        protected.mover.position = [x, y, z + 6]
        caster.mover.position = [x + 6, y, z + 6]
        second.mover.position = [x - 2, y, z]
        third.mover.position = [x + 2, y, z]
        fight = run.match.combat
        health = fight.vitals_of(next(t for t in fight._targets() if t.owner is protected))
        yaw, pitch = aim(fight._eye(caster), fight._eye(protected))
        run.step({caster: (yaw, pitch, 0)})
        run.step({caster: (yaw, pitch, 0x10)})
        projected = sigma.particle_barrier(protected)
        self.assertIsNotNone(projected)
        projected_key = sigma.particle_barrier_key(projected)
        yaw, pitch = aim(fight._eye(first), fight._eye(protected))
        run.step({first: (yaw, pitch, FIRE)})
        for _ in range(30):
            if projected_key in health.particle_barriers:
                break
            run.step({})
        self.assertEqual(health.particle_barriers[projected_key], 145)
        caster_energy = caster.body_script.component.entity_var(87)
        self.assertAlmostEqual(caster_energy.value(), 11, places=5)
        run.step({protected: (0, 0, 0x08)})
        personal = sigma.particle_barrier(protected)
        self.assertIsNot(personal, projected)
        personal_key = sigma.particle_barrier_key(personal)
        for _ in range(22):
            run.step({})
        self.assertEqual(health.particle_barriers, {projected_key: 145, personal_key: 145})

        yaw, pitch = aim(fight._eye(second), fight._eye(protected))
        run.step({second: (yaw, pitch, FIRE)})
        for _ in range(35):
            if health.particle_barriers.get(personal_key) == 35:
                break
            run.step({second: (yaw, pitch, 0)})
        self.assertEqual(health.particle_barriers, {projected_key: 145, personal_key: 35})
        yaw, pitch = aim(fight._eye(third), fight._eye(protected))
        run.step({third: (yaw, pitch, FIRE)})
        for _ in range(35):
            before = caster_energy.value()
            run.step({third: (yaw, pitch, 0)})
            if health.particle_barriers.get(projected_key) == 90:
                self.assertAlmostEqual(caster_energy.value() - before, 11, delta=0.03)
                break
        self.assertEqual(health.particle_barriers, {projected_key: 90})
        self.assertEqual(health.total, health.maximum)
        self.assertAlmostEqual(protected.body_script.component.entity_var(87).value(), 40, places=4)
        for player in attackers:
            self.assertEqual(player.body_script.component.entity_var(342).value(), 0)

    def test_zarya_beam_bypasses_grasp_but_respects_range_and_world_cover(self):
        for distance, wall, expected in ((6, False, 36), (17, False, 0), (6, True, 0)):
            with self.subTest(distance=distance, wall=wall):
                run, player, enemy, _ = self.arena(offset=0, distance=distance)
                position = list(enemy.mover.position)
                run.match.switch_hero(enemy, heroes()[0x02E0000000000068])
                enemy.mover.position = position
                fight = run.match.combat
                vitals = fight.vitals_of(next(t for t in fight._targets() if t.owner is player))
                if wall:
                    self.wall(run, sideways=False, offset=3)
                run.step({})
                run.step({player: (0, 0, 0x08)})
                grasp = self.ability(player, sigma.GRASP).instance
                self.assertTrue(grasp.states[1].active)
                x, y, z = player.mover.position
                yaw, pitch = aim(fight._eye(enemy), (x, y + 0.9, z))
                for _ in range(30):
                    run.step({enemy: (yaw, pitch, FIRE)})
                weapon = self.ability(enemy, 0x0241).instance
                self.assertTrue(weapon.states[10].active)
                self.assertLess(weapon.vars[53].value(), 100)
                self.assertTrue(grasp.states[1].active)
                self.assertAlmostEqual(vitals.total, 400 - expected)
                self.assertEqual(grasp.vars[21312].value(), 0)
                run.step({})
                self.assertFalse(weapon.states[10].active)
                self.assertAlmostEqual(vitals.total, 400 - expected)
                for _ in range(115):
                    run.step({})
                self.assertFalse(grasp.states[1].active)
                self.assertFalse(vitals.temporary_shields)

    def test_mei_spray_slow_requires_contact_and_expires_without_input(self):
        for mode in ("contact", "range", "wall", "ally", "grasp"):
            with self.subTest(mode=mode):
                run, player, mei, _ = self.arena(offset=0, distance=12 if mode == "range" else 6)
                position = list(mei.mover.position)
                run.match.switch_hero(mei, heroes()[0x02E00000000000DD])
                mei.mover.position = position
                fight = run.match.combat
                if mode == "wall":
                    self.wall(run, sideways=False, offset=3)
                if mode == "ally":
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    mei.team = player.team
                angles = aim(fight._eye(mei), fight._eye(player))
                run.step({mei: (*angles, FIRE), player: (0, 0, 0x08 if mode == "grasp" else 0)})
                for _ in range(24):
                    run.step({})
                if mode == "grasp":
                    self.assertAlmostEqual(fight.frost(player)[0], 0.2)
                    self.assertEqual(self.ability(player, sigma.GRASP).instance.vars[21312].value(), 0)
                    continue
                if mode != "contact":
                    self.assertIsNone(fight.frost(player))
                    continue
                self.assertAlmostEqual(fight.frost(player)[0], 0.2)
                start = player.mover.position[2]
                for _ in range(10):
                    run.frame += 1
                    run.match.update(run.frame * combat.TICK, run.frame)
                    player.take_commands([Command(run.frame, forward=127)])
                self.assertGreater(player.mover.position[2], start)
                self.assertAlmostEqual(
                    player.mover.values.run_forward, player.mover.data.run_forward * 0.8, places=6,
                )
                for _ in range(70):
                    run.frame += 1
                    run.match.update(run.frame * combat.TICK, run.frame)
                self.assertIsNone(fight.frost(player))
                run.step({})
                run.step({})
                self.assertEqual(player.mover.values.run_forward, player.mover.data.run_forward)

    def test_mei_spray_hits_front_body_once_before_rear_sigma(self):
        for defense in ("none", "wall", "grasp", "particle"):
            with self.subTest(defense=defense):
                run = Run(KANEZAKA_DM, ("Front", "Rear", "Mei"))
                match, fight = run.match, run.match.combat
                match.bots.clear()
                match._pending_bot_scripts.clear()
                front, rear, mei = run.players
                for player in (front, rear):
                    match.switch_hero(player, heroes()[SIGMA])
                if defense == "particle":
                    match.switch_hero(front, heroes()[0x02E0000000000068])
                match.switch_hero(mei, heroes()[0x02E00000000000DD])
                x, y, z = front.mover.position
                mei.mover.position = [x, y, z - 3]
                rear.mover.position = [x, y, z + 4]
                if defense == "wall":
                    self.wall(run, sideways=False, offset=2)
                angles = aim(fight._eye(mei), (x, y + 1.3, z))
                run.step({
                    front: (0, 0, 0x08 if defense == "particle" else 0),
                    rear: (-32768, 0, 0x08 if defense == "grasp" else 0),
                })
                front_health = fight.vitals_of(next(target for target in fight._targets() if target.owner is front))
                if defense == "particle":
                    self.assertIsNotNone(sigma.particle_barrier(front))
                run.step({mei: (*angles, FIRE)})
                for _ in range(30):
                    run.step({rear: (-32768, 0, 0)})
                if defense == "particle":
                    self.assertEqual(front_health.total, front_health.maximum)
                    self.assertEqual(list(front_health.particle_barriers.values()), [197.25])
                    self.assertIsNone(fight.frost(front))
                else:
                    self.assertEqual(front_health.total, front_health.maximum - 2.75)
                    self.assertAlmostEqual(fight.frost(front)[0], 0.2)
                if defense in ("none", "grasp"):
                    self.assertEqual(fight.vitals[rear.body].total, 397.25)
                    self.assertAlmostEqual(fight.frost(rear)[0], 0.2)
                else:
                    self.assertEqual(fight.vitals[rear.body].total, 400)
                    self.assertIsNone(fight.frost(rear))
                grasp = self.ability(rear, sigma.GRASP).instance
                absorbed = grasp.vars[21312].value() if 21312 in grasp.vars else 0
                self.assertEqual(absorbed, 0)

    def test_mei_spray_freezes_sigma_and_interrupts_flux_preview(self):
        from ow174.game import mei_frost

        run, player, mei, _ = self.arena(offset=0, distance=6)
        position = list(mei.mover.position)
        run.match.switch_hero(mei, heroes()[0x02E00000000000DD])
        mei.mover.position = position
        fight = run.match.combat
        yaw, pitch = aim(fight._eye(mei), fight._eye(player))
        for _ in range(100):
            run.step({mei: (yaw, pitch, FIRE)})
            frost = fight.frost(player)
            if frost is not None and frost[0] >= 0.8:
                break
        self.assertGreaterEqual(frost[0], 0.8)
        self.assertFalse(fight.is_stunned(player))
        script = player.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        run.step({mei: (yaw, pitch, FIRE), player: (0, 8192, 0x20)})
        ability = self.ability(player, sigma.FLUX)
        self.assertTrue(ability.flags & nodes.Ability.RUNNING)
        for _ in range(60):
            yaw, pitch = aim(fight._eye(mei), fight._eye(player))
            run.step({mei: (yaw, pitch, FIRE)})
            if fight.is_stunned(player):
                break
        frozen = fight.frost(player)
        self.assertTrue(fight.is_stunned(player), (frozen, player.mover.position))
        self.assertEqual(frozen[2] - frozen[1], mei_frost.FREEZE_MS)
        self.assertFalse(ability.flags & nodes.Ability.RUNNING)
        self.assertLess(script.component.entity_var(342).value(), 10)
        for _ in range(15):
            run.step({mei: (yaw, pitch, FIRE), player: (16384, 0, FIRE)})
            self.assertEqual(fight.frost(player), frozen)
            self.assertEqual(player.mover.yaw, frozen[3])
            self.assertFalse(any(rocket.shooter is player for rocket in fight.rockets))
        for _ in range(100):
            run.step({})
            if fight.frost(player) is None:
                break
        self.assertFalse(fight.is_stunned(player))
        self.assertEqual(script.component.entity_var(mei_frost.PROGRESS_VAR).value(), 0)
        self.assertFalse(any(rocket.shooter is player for rocket in fight.rockets))

    def test_mei_spray_freeze_interrupts_grasp_and_held_input_cannot_restart_it(self):
        run, player, mei, _ = self.arena(offset=0, distance=6)
        position = list(mei.mover.position)
        run.match.switch_hero(mei, heroes()[0x02E00000000000DD])
        mei.mover.position = position
        fight = run.match.combat
        angles = aim(fight._eye(mei), fight._eye(player))
        for _ in range(100):
            run.step({mei: (*angles, FIRE)})
            frost = fight.frost(player)
            if frost is not None and frost[0] >= 0.8:
                break
        self.assertGreaterEqual(frost[0], 0.8)
        run.step({mei: (*angles, FIRE), player: (0, 0, 0x08)})
        ability = self.ability(player, sigma.GRASP)
        grasp = ability.instance.states[1]
        self.assertTrue(grasp.active)
        for _ in range(60):
            run.step({mei: (*angles, FIRE), player: (0, 0, 0x08)})
            if fight.is_stunned(player):
                break
        self.assertTrue(fight.is_stunned(player))
        self.assertFalse(grasp.active)
        self.assertEqual(ability.instance.vars[21312].value(), 0)
        for _ in range(100):
            run.step({player: (0, 0, 0x08)})
            self.assertFalse(grasp.active)
        self.assertFalse(fight.is_stunned(player))
        self.assertGreater(ability.remaining(), 0)
        self.assertFalse(fight.grasp_grants)

    def test_tagged_torbjorn_turret_throw_hits_through_kinetic_grasp(self):
        run, player, torb, _ = self.arena(offset=0, distance=7)
        position = list(torb.mover.position)
        run.match.switch_hero(torb, heroes()[0x02E0000000000006])
        torb.mover.position = position
        fight = run.match.combat
        target = next(target for target in fight._targets() if target.owner is player)
        health = fight.vitals_of(target)
        angles = aim(fight._eye(torb), fight._eye(player))
        run.step({player: (0, 0, 0x08), torb: (*angles, 0x08)})
        grasp = self.ability(player, sigma.GRASP).instance
        self.assertTrue(grasp.states[1].active)
        for _ in range(20):
            run.step({torb: (*angles, 0)})
            if fight.rockets:
                break
        throw, = fight.rockets
        self.assertEqual(throw.definition, 0x0400000000002931)
        self.assertEqual(throw.direct.amount, 50)
        self.assertFalse(throw.absorbable)
        for _ in range(60):
            run.step({})
            if throw not in fight.rockets:
                break
        self.assertTrue(grasp.states[1].active)
        self.assertEqual(health.total, health.maximum - 50)
        self.assertEqual(grasp.vars[21312].value(), 0)
        self.assertFalse(health.temporary_shields)

    def test_torbjorn_throw_crosses_grasp_but_turret_fire_is_absorbed(self):
        from ow174.game import torbjorn_turret

        if graph.entity_body(torbjorn_turret.BODY) is None:
            self.skipTest("requires the external Torbjörn turret actor bundle")
        for mode in ("throw_grasp", "gun_grasp", "wall"):
            with self.subTest(mode=mode):
                run, player, torb, _ = self.arena(offset=.85, distance=5)
                fight = run.match.combat
                position = list(torb.mover.position)
                run.match.switch_hero(torb, heroes()[torbjorn_turret.HERO])
                torb.mover.position = position
                if mode == "wall":
                    self.wall(run, sideways=False, offset=2)
                run.step({torb: (-32768, 0, 0x08), player: (0, 0, 0x08 if mode == "throw_grasp" else 0)})
                crossed = False
                for _ in range(65):
                    for rocket in fight.rockets:
                        if rocket.definition == torbjorn_turret.THROW:
                            crossed |= sigma.grasp_intersection(
                                fight, torb, rocket.position, rocket.direction, rocket.speed * combat.TICK,
                            ) is not None
                    run.step({torb: (-32768, 0, 0)})
                    if fight.torbjorn_turrets:
                        break
                if mode == "wall":
                    self.assertFalse(fight.torbjorn_turrets)
                    self.assertFalse(fight.rockets)
                    continue
                self.assertEqual(crossed, mode == "throw_grasp")
                turret, = fight.torbjorn_turrets.values()
                self.assertIs(turret.owner, torb)
                self.assertEqual(torb.body_script.component.entity_var(679).value(),
                                 expr.Entity(turret.actor.entity))
                health = fight.vitals[turret.actor.entity]
                self.assertEqual(health.total, 75)
                self.assertEqual(health.maximum, 250)
                x, y, z = turret.actor.position
                yaw, pitch = aim(fight._eye(player), (x, y + torbjorn_turret.EYE, z))
                activated = False
                for _ in range(200):
                    grasp = mode == "gun_grasp" and not activated and turret.build_end - fight.now < .15
                    run.step({player: (yaw, pitch, 0x08 if grasp else 0)})
                    activated |= grasp
                    if turret.shots:
                        break
                self.assertEqual(turret.shots, 1)
                self.assertEqual(health.total, 250)
                self.assertGreaterEqual(fight.now, turret.build_end)
                self.assertEqual(fight.vitals[player.body].total, 400 if mode == "gun_grasp" else 386)
                absorbed = self.ability(player, sigma.GRASP).instance.vars[21312].value()
                self.assertEqual(absorbed, 14 if mode == "gun_grasp" else 0)
                if mode == "gun_grasp":
                    run.match.switch_hero(torb, heroes()[SIGMA])
                    for _ in range(135):
                        run.step({})
                    self.assertFalse(fight.torbjorn_turrets)
                    bonus = fight.vitals[player.body].temporary_shields[sigma.SHIELD_TAG]
                    self.assertAlmostEqual(bonus.now, 8.4, places=5)
                else:
                    for viewer in run.players:
                        self.enterContext(patch.object(viewer.client, "queue_bot_death", create=True))
                        self.enterContext(patch.object(viewer.client, "queue_bot_death_update", create=True))
                    script = player.body_script
                    script.ult_seconds = 0
                    script.set_ult_charge(0)
                    yaw, pitch = aim(fight._eye(player), (x, y + .5, z))
                    for remaining in (140, 30, 0):
                        run.step({player: (yaw, pitch, FIRE)})
                        for _ in range(90):
                            run.step({player: (yaw, pitch, 0)})
                            if not fight.torbjorn_turrets:
                                break
                        self.assertEqual(health.total, remaining)
                        self.assertEqual(script.component.entity_var(342).value(), 0)
                    self.assertFalse(fight.torbjorn_turrets)
                    self.assertNotIn(turret.actor, run.match.bots)
                    self.assertEqual(torb.body_script.component.entity_var(679).value(), expr.Entity(0))
                    instance = next(row for row in torb.body_script.component.instances.values()
                                    if row.graph.index == 0x16C5)
                    ability = instance.state(13)
                    self.assertGreater(ability.remaining(), 9.9)
                    run.step({torb: (-32768, 0, 0x08)})
                    for _ in range(20):
                        run.step({torb: (-32768, 0, 0)})
                        self.assertFalse(any(rocket.shooter is torb for rocket in fight.rockets))
                    self.assertFalse(fight.torbjorn_turrets)
                    for _ in range(90):
                        run.step({})
                    target = next(target for target in fight._targets() if target.owner is torb)
                    torb_health = fight.vitals_of(target)
                    before = torb_health.total
                    self.pair(run, player, torb)
                    damage = before - torb_health.total
                    self.assertGreater(damage, 0)
                    self.assertEqual(script.component.entity_var(342).value(), damage)

    def test_molten_core_grasp_intercepts_airborne_globule_but_not_landed_pool(self):
        from ow174.game import molten_core

        if graph.graph(molten_core.STATUS) is None:
            self.skipTest("requires the external Molten Core victim graph")
        for airborne_grasp in (False, True):
            with self.subTest(airborne_grasp=airborne_grasp):
                run, player, torb, _ = self.arena(
                    offset=0 if airborne_grasp else 1.35, distance=7 if airborne_grasp else 12.5,
                )
                position = list(torb.mover.position)
                run.match.switch_hero(torb, heroes()[0x02E0000000000006])
                torb.mover.position = position
                fight = run.match.combat
                script = torb.body_script
                script.set_ult_charge(script.component.entity_var(338).value())
                angles = aim(fight._eye(torb), fight._eye(player)) if airborne_grasp else (-32768, 0)
                run.step({torb: (*angles, 0x20)})
                for _ in range(56):
                    run.step({torb: (*angles, 0)})
                self.assertEqual(script.component.entity_var(342).value(), 0)
                run.step({torb: (*angles, FIRE), player: (0, 0, 0x08 if airborne_grasp else 0)})
                launch = None
                for _ in range(75):
                    run.step({torb: (*angles, 0)})
                    launch = next((r for r in fight.rockets if r.definition == 0x0400000000002902), launch)
                    if fight.molten_pools:
                        break
                self.assertIsNotNone(launch)
                self.assertEqual(launch.gravity, 30)
                health = fight.vitals[player.body]
                grasp = self.ability(player, sigma.GRASP).instance
                if airborne_grasp:
                    self.assertEqual(grasp.vars[21312].value(), 25)
                    for _ in range(75):
                        run.step({})
                    self.assertFalse(fight.molten_pools)
                    self.assertAlmostEqual(health.total, 415, places=5)
                    continue
                pool, = fight.molten_pools.values()
                run.step({player: (0, 0, 0x08)})
                for _ in range(30):
                    run.step({})
                self.assertEqual(health.total, 400)
                self.assertFalse(fight.molten_burns)
                run.step({})
                self.assertAlmostEqual(health.total, 398.08)
                self.assertTrue(pool.armed)
                self.assertTrue(grasp.states[1].active)
                self.assertEqual(grasp.vars[21312].value(), 0)
                burn_script, burn = fight.molten_burns[torb.entity, player.body]
                self.assertIs(burn_script, player.body_script)
                self.assertEqual(burn.graph.index, molten_core.STATUS)
                run.step({})
                self.assertAlmostEqual(health.total, 395.52)
                self.assertTrue(all(burn.states[index].active for index in (0, 1, 2, 7)))
                self.assertTrue(burn.graph.node(13).client_only)
                self.assertNotIn(3, burn.states)  # visual child is client-only
                x, y, z = player.mover.position
                player.spawn = ((x - 7, y, z), player.spawn[1])
                player.mover = player.new_mover()
                for _ in range(10):
                    run.step({})
                self.assertAlmostEqual(health.total, 395.52)
                self.assertFalse(fight.molten_burns)
                self.assertNotIn(burn.id, burn_script.component.instances)
                for _ in range(650):
                    if not fight.molten_pools:
                        break
                    run.step({})
                self.assertFalse(fight.molten_pools)

    def test_orisa_fusion_driver_contacts_grasp_and_world_cover(self):
        for defense in ("none", "grasp", "wall"):
            with self.subTest(defense=defense):
                run, player, orisa, _ = self.arena(offset=0, distance=8)
                position = list(orisa.mover.position)
                run.match.switch_hero(orisa, heroes()[0x02E000000000013E])
                orisa.mover.position = position
                fight = run.match.combat
                vitals = fight.vitals_of(next(t for t in fight._targets() if t.owner is player))
                if defense == "wall":
                    self.wall(run, sideways=False, offset=4)
                x, y, z = player.mover.position
                yaw, pitch = aim(fight._eye(orisa), (x, y + 1, z))
                run.step({})
                run.step({orisa: (yaw, pitch, FIRE)})
                rocket, = fight.rockets
                self.assertEqual((rocket.speed, rocket.direct.amount), (120, 11))
                for frame in range(159):
                    run.step({
                        orisa: (yaw, pitch, FIRE if frame < 19 else 0),
                        player: (0, 0, 0x08 if defense == "grasp" and frame == 0 else 0),
                    })
                self.assertFalse(fight.rockets)
                if defense == "none":
                    self.assertEqual(vitals.total, 356)
                    self.assertEqual(len(orisa.client.messages(combat.HIT)), 4)
                elif defense == "grasp":
                    grasp = self.ability(player, sigma.GRASP).instance
                    self.assertEqual(grasp.vars[21312].value(), 44)
                    self.assertAlmostEqual(vitals.temporary_shields[sigma.SHIELD_TAG].now, 26.4, places=5)
                    self.assertEqual(sum(vitals.now.values()), 400)
                    self.assertFalse(orisa.client.messages(combat.HIT))

                else:
                    self.assertEqual(vitals.total, 400)
                    self.assertFalse(orisa.client.messages(combat.HIT))

    def test_orisa_halt_manual_pull_or_grasp_interception(self):
        for absorbing in (False, True):
            with self.subTest(absorbing=absorbing):
                run, player, orisa, _ = self.arena(offset=0, distance=12)
                position = list(orisa.mover.position)
                run.match.switch_hero(orisa, heroes()[0x02E000000000013E])
                orisa.mover.position = position
                fight = run.match.combat
                vitals = fight.vitals_of(next(t for t in fight._targets() if t.owner is player))
                start = tuple(player.mover.position)
                yaw, pitch = aim(fight._eye(orisa), fight._eye(player))
                run.step({})
                run.step({player: (0, 0, 0x08 if absorbing else 0), orisa: (yaw, pitch, 0x02)})
                for _ in range(12):
                    run.step({orisa: (yaw, pitch, 0)})
                rocket, = fight.rockets
                self.assertEqual((rocket.speed, rocket.direct.amount), (25, 0))
                self.assertIsNotNone(rocket.halt_instance)
                for _ in range(18):
                    run.step({orisa: (yaw, pitch, 0)})
                run.step({orisa: (yaw, pitch, 0x02)})
                if absorbing:
                    self.assertFalse(fight.rockets)
                else:
                    self.assertIsNotNone(rocket.halt_at)
                for _ in range(130):
                    run.step({})
                self.assertFalse(fight.rockets)
                self.assertFalse(fight.halt_pulls)
                self.assertEqual(vitals.total, 400)
                if absorbing:
                    grasp = self.ability(player, sigma.GRASP).instance
                    self.assertEqual(grasp.vars[21312].value(), 0)
                    self.assertFalse(vitals.temporary_shields)
                    self.assertEqual(tuple(player.mover.position), start)
                else:
                    self.assertAlmostEqual(player.mover.position[2], rocket.position[2], delta=0.05)
                    self.assertGreater(player.mover.position[2] - start[2], 1)
                halt = self.ability(orisa, 0x0CCB).instance
                self.assertGreater(halt.vars[61].value(), 0)
                run.step({orisa: (yaw, pitch, 0x02)})
                for _ in range(12):
                    run.step({orisa: (yaw, pitch, 0)})
                self.assertFalse(fight.rockets)

    def test_airburst_cannot_damage_a_player_behind_a_side_wall(self):
        run, shooter, enemy, vitals = self.arena()
        self.wall(run, sideways=True, offset=0.5)
        for frame in range(61):
            run.step({shooter: (0, 0, FIRE if frame == 0 else 0)})
            self.assertFalse(enemy.mover.external_impulse)
        self.assertEqual(vitals.total, 200)

    def test_halt_automatically_activates_at_wall_then_slows_and_releases_sigma(self):
        run, player, orisa, _ = self.arena(offset=0, distance=12)
        position = list(orisa.mover.position)
        run.match.switch_hero(orisa, heroes()[0x02E000000000013E])
        orisa.mover.position = position
        self.wall(run, sideways=False, offset=-2)
        fight = run.match.combat
        vitals = fight.vitals_of(next(t for t in fight._targets() if t.owner is player))
        start = tuple(player.mover.position)
        normal_speed = player.mover.data.run_forward
        yaw, pitch = aim(fight._eye(orisa), fight._eye(player))
        run.step({})
        run.step({orisa: (yaw, pitch, 0x02)})
        for _ in range(55):
            run.step({orisa: (yaw, pitch, 0)})
            if fight.rockets and fight.rockets[0].halt_at is not None:
                break
        rocket, = fight.rockets
        self.assertEqual(rocket.halt_at - fight.tick, 25)
        self.assertAlmostEqual(rocket.position[2], start[2] - 2, delta=0.02)
        self.assertEqual(tuple(player.mover.position), start)
        for _ in range(24):
            run.step({})
        self.assertEqual(tuple(player.mover.position), start)
        self.assertFalse(fight.halt_slows)

        speeds = []
        for _ in range(70):
            run.step({})
            speeds.append(player.mover.values.run_forward)
        self.assertFalse(fight.rockets)
        self.assertFalse(fight.halt_pulls)
        self.assertFalse(fight.halt_slows)
        self.assertLess(player.mover.position[2], start[2] - 1)
        self.assertGreater(player.mover.position[2], start[2] - 2)
        self.assertAlmostEqual(min(speeds), normal_speed * 0.35, places=5)
        self.assertEqual(sum(speed < normal_speed for speed in speeds), 25)
        self.assertEqual(speeds[-1], normal_speed)
        self.assertEqual(vitals.total, 400)

    def test_direct_impact_explosion_cannot_damage_through_a_wall(self):
        run, shooter, enemy, vitals = self.arena(offset=0, distance=6)
        self.wall(run, sideways=False, offset=5)
        self.pair(run, shooter, target=enemy)
        self.assertEqual(vitals.total, 200)

    def test_quick_melee_cannot_damage_through_a_wall(self):
        run, shooter, _, vitals = self.arena(offset=0, distance=1)
        self.wall(run, sideways=False, offset=0.5)
        run.step({shooter: (0, 0, 0x800)})
        for _ in range(50):
            run.step({})
        self.assertEqual(vitals.total, 200)

    def test_airburst_preserves_friendly_team_semantics(self):
        run, shooter, victim, vitals = self.arena()
        run.match.game_map = run.match.game_map.__class__(
            **{**run.match.game_map.__dict__, "free_for_all": False}
        )
        victim.team = shooter.team
        self.pair(run, shooter)
        self.assertEqual(vitals.total, 200.0)

    def test_authored_pair_kills_a_bot_and_retains_its_opt_in_corpse(self):
        run, shooter, victim, _ = self.arena()
        bot = bots.Bot(bots.BOT_ENTITY, {
            "hero": "0x02E000000000016B", "team": victim.team,
            "position": tuple(victim.mover.position), "rotation": (0, 0, 0, 1), "graphs": [],
        }, {"name": "Training target", "body": "0x0400000000001403", "health": 50}, {})
        bot.retain_corpse = True
        run.match.bots.append(bot)
        victim.mover.position[0] += 20
        target = next(t for t in run.match.combat._targets() if t.owner is bot)
        vitals = run.match.combat.vitals_of(target)
        self.pair(run, shooter)
        self.assertTrue(bot.dead)
        self.assertTrue(vitals.dead)
        self.assertEqual(vitals.total, 0)
        self.assertFalse(any(who is bot for _, who in run.match.combat.respawns))
        for _ in range(220):
            run.step({})
        self.assertIn(bot, run.match.bots)
        self.assertNotIn(bot, [t.owner for t in run.match.combat._enemies(shooter)])

    def test_enemy_player_rifle_damages_sigmas_real_shield_pool(self):
        run, sigma, enemy, _ = self.arena(offset=0, distance=6)
        target = next(t for t in run.match.combat._targets() if t.owner is sigma)
        vitals = run.match.combat.vitals_of(target)
        self.assertEqual(vitals.total, 400)
        for _ in range(10):
            run.step({enemy: (-32768, 0, FIRE)})
        self.assertLess(vitals.total, 400)
        self.assertEqual(vitals.now[combat.HEALTH_POOL], 300)

    def test_airburst_is_opt_in_and_other_projectile_expiry_still_does_no_damage(self):
        run, shooter, _, vitals = self.arena()
        fight = run.match.combat
        rocket = combat.Rocket(shooter, shooter.team, fight._eye(shooter), (0, 0, 1),
                               50, 0.44, None, combat.HitDef(30, rings=((3, 1),)))
        fight.rockets.append(rocket)
        for _ in range(40):
            run.step({})
        self.assertEqual(vitals.total, 200)

    def test_airburst_after_a_world_tick_gap_matches_contiguous_flight(self):
        for skipped in (False, True):
            with self.subTest(skipped=skipped):
                run, shooter, _, vitals = self.arena()
                run.step({shooter: (0, 0, FIRE)})
                # Both authored volleys have fired; neither has reached expiry.
                for _ in range(20):
                    run.step({})
                self.assertEqual(vitals.total, 200)
                self.assertEqual(len(run.match.combat.rockets), 2)
                if skipped:
                    run.frame += 40
                    run.match.update(run.frame * combat.TICK, run.frame)
                    run.match.update(run.frame * combat.TICK, run.frame)
                else:
                    for _ in range(40):
                        run.step({})
                self.assertEqual(vitals.total, 140)
                self.assertFalse(run.match.combat.rockets)

    def test_authored_quick_melee_deals_thirty_once(self):
        run, shooter, _, vitals = self.arena(offset=0, distance=1)
        run.step({shooter: (0, 0, 0x800)})
        for _ in range(50):
            run.step({})
        self.assertEqual(vitals.total, 170)

    def test_authored_accretion_direct_hit_routes_the_control_request(self):
        run, shooter, enemy, vitals = self.arena(offset=0, distance=6)
        yaw, pitch = aim(run.match.combat._eye(shooter), run.match.combat._eye(enemy))
        start = tuple(enemy.mover.position)
        run.step({shooter: (yaw, pitch, 0x10)})
        for _ in range(100):
            run.step({shooter: (yaw, pitch, 0)})
            if vitals.total < 200:
                break
        self.assertEqual(vitals.total, 130)
        fight = run.match.combat
        deadline = fight.stuns[enemy.body]
        self.assertEqual(deadline - fight.tick, 50)
        self.assertTrue(enemy.mover.external_impulse)
        self.assertGreater(enemy.mover.velocity[2], 4.5)
        self.assertGreater(enemy.mover.position[1], start[1])
        from unittest.mock import patch

        from ow174.game.physics_snapshot import snapshot_mover

        captured = snapshot_mover(enemy.mover)
        with patch.object(enemy.mover, "external_impulse", False):
            self.assertNotEqual(captured, snapshot_mover(enemy.mover))
        self.assertEqual(captured, snapshot_mover(enemy.mover))
        self.assertTrue(fight.is_stunned(enemy, tick=deadline - 1))
        self.assertFalse(fight.is_stunned(enemy, tick=deadline))
        while run.frame <= deadline:
            run.step({})
        self.assertNotIn(enemy.body, fight.stuns)
        self.assertEqual(vitals.total, 130)
        self.assertGreater(enemy.mover.position[2] - start[2], 1)
        self.assertFalse(enemy.mover.external_impulse)

    def test_accretion_body_sweep_turns_a_graze_into_a_direct_hit(self):
        for offset, expected, controlled in ((0.9, 70, True), (1.65, 0, False)):
            with self.subTest(offset=offset):
                run, shooter, enemy, vitals = self.arena(offset=offset, distance=6)
                x, y, z = shooter.mover.position
                facing = aim(run.match.combat._eye(shooter), (x, y + 1.2, z + 6))
                run.step({shooter: (*facing, 0x10)})
                for _ in range(110):
                    run.step({})
                    if vitals.total < vitals.maximum:
                        break
                self.assertEqual(vitals.maximum - vitals.total, expected)
                self.assertEqual(run.match.combat.is_stunned(enemy), controlled)

    def test_accretion_radius_does_not_reach_a_body_behind_static_cover(self):
        run, shooter, enemy, health = self.arena(offset=0, distance=6)
        self.wall(run, sideways=False, offset=5.45)
        x, y, z = shooter.mover.position
        facing = aim(run.match.combat._eye(shooter), (x, y + 1.2, z + 6))
        run.step({shooter: (*facing, 0x10)})
        for _ in range(100):
            run.step({})
            self.assertFalse(run.match.combat.is_stunned(enemy))
        self.assertEqual(health.total, health.maximum)
        self.assertFalse(run.match.combat.rockets)

    def test_accretion_world_sweep_hits_only_the_nearer_wall_edge(self):
        for offset, blocked in ((0.2, True), (0.4, False)):
            with self.subTest(offset=offset):
                run, shooter, _enemy, health = self.arena(offset=0, distance=12)
                x, y, z = shooter.mover.position
                a, b, c, d = ((x + offset, y, z + 3), (x + offset, y + 4, z + 3),
                              (x + 2, y + 4, z + 3), (x + 2, y, z + 3))
                floor = [(-5000, y, -5000, -5000, y, 5000, 5000, y, 5000),
                         (-5000, y, -5000, 5000, y, 5000, 5000, y, -5000)]
                run.match.collision_world = World([*floor, (*a, *b, *c), (*a, *c, *d)])
                for player in run.players:
                    player.mover.use(world=run.match.collision_world)
                fight = run.match.combat
                run.step({shooter: (0, 0, 0x10)})
                for _ in range(65):
                    run.step({})
                    if fight.rockets:
                        break
                rock, = fight.rockets
                self.assertAlmostEqual(rock.wall_radius, 0.3)
                for _ in range(10):
                    run.step({})
                self.assertEqual(rock not in fight.rockets, blocked)
                self.assertEqual(health.total, health.maximum)

    def test_authored_accretion_launch_bias_and_gravity_drive_the_actual_projectile(self):
        run, shooter, _enemy, _vitals = self.arena(offset=12, distance=25)
        fight = run.match.combat
        run.step({shooter: (0, 0, 0x10)})
        for _ in range(100):
            if fight.rockets:
                break
            run.step({})
        rocket, = fight.rockets
        self.assertEqual(rocket.gravity, 18)
        self.assertAlmostEqual(rocket.speed, 37)
        self.assertGreater(rocket.velocity[1], 3.6)
        self.assertEqual(rocket.direct.knockback, (6, 3, ((3, 1, 1),)))
        self.assertEqual(rocket.blast.knockback[:2], (5, 1))
        y0 = rocket.position[1]
        vy = rocket.velocity[1]
        for frame in range(1, 20):
            run.step({})
            self.assertAlmostEqual(rocket.velocity[1], vy - frame * 18 * 0.016, places=5)
        self.assertLess(rocket.velocity[1], 0)
        self.assertGreater(rocket.position[1], y0)

    def test_fortify_blocks_accretion_control_until_its_actual_scope_ends(self):
        run, shooter, enemy, _ = self.arena(offset=0, distance=6)
        position = list(enemy.mover.position)
        run.match.switch_hero(enemy, heroes()[0x02E000000000013E])
        enemy.mover.position = position
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is enemy)
        vitals = fight.vitals_of(target)
        script = shooter.body_script
        script.ult_seconds = 0
        script.set_ult_charge(0)
        run.step({enemy: (-32768, 0, 0x08)})
        protection = enemy.body_script.component.entity_var(2547, create=False)
        self.assertTrue(protection.value())

        def contact():
            x, y, z = enemy.mover.position
            yaw, pitch = aim(fight._eye(shooter), (x, y + 1, z))
            before = vitals.total
            run.step({shooter: (yaw, pitch, 0x10)})
            for _ in range(100):
                run.step({shooter: (yaw, pitch, 0)})
                if vitals.total < before:
                    return
            self.fail("Accretion did not contact Orisa")

        contact()
        self.assertTrue(protection.value())
        self.assertFalse(fight.is_stunned(enemy))
        self.assertFalse(enemy.mover.external_impulse)
        self.assertAlmostEqual(vitals.maximum - vitals.total, 32, places=5)
        self.assertAlmostEqual(script.component.entity_var(342).value(), 32, places=5)
        ability = self.ability(shooter, 0x1BBB)
        for _ in range(300):
            if not protection.value():
                break
            run.step({})
        self.assertFalse(protection.value())
        self.assertGreater(ability.remaining(), 2)
        yaw, pitch = aim(fight._eye(shooter), fight._eye(enemy))
        run.step({shooter: (yaw, pitch, 0x10)})
        for _ in range(90):
            run.step({shooter: (yaw, pitch, 0)})
            self.assertFalse(any(rocket.shooter is shooter for rocket in fight.rockets))
        self.assertAlmostEqual(vitals.maximum - vitals.total, 32, places=5)
        self.assertAlmostEqual(script.component.entity_var(342).value(), 32, places=5)
        for _ in range(700):
            if not ability.remaining():
                break
            run.step({})
        self.assertEqual(ability.remaining(), 0)
        contact()
        self.assertEqual(fight.stuns[enemy.body] - fight.tick, 50)
        self.assertAlmostEqual(vitals.maximum - vitals.total, 92, places=5)
        self.assertAlmostEqual(script.component.entity_var(342).value(), 92, places=5)

    def test_accretion_respects_the_target_control_stack_priority(self):
        from ow174.game.script import runtime

        run, shooter, enemy, vitals = self.arena(offset=0, distance=6)
        component = enemy.body_script.component
        active = next(state for instance in component.instances.values()
                      for state in instance.states.values() if state.active)
        # A competing link on the real target VM, isolated from other hero effects.
        stack = component.entity_var(888)
        link = runtime.Link(active, 0, 20.0, False)
        runtime.insert_link(stack, link)
        fight = run.match.combat
        yaw, pitch = aim(fight._eye(shooter), fight._eye(enemy))
        try:
            run.step({shooter: (yaw, pitch, 0x10)})
            for _ in range(100):
                run.step({shooter: (yaw, pitch, 0)})
                if vitals.total < 200:
                    break
            self.assertEqual(vitals.total, 130)
            self.assertIn(link, stack.links)
            self.assertTrue(active.active)
            self.assertFalse(fight.is_stunned(enemy))
        finally:
            if link in stack.links:
                stack.links.remove(link)

    def test_equal_deadline_accretion_hits_only_interrupt_once(self):
        from dataclasses import replace
        from unittest.mock import patch

        run = Run(KANEZAKA_DM, ("Sigma", "Ally", "Enemy"))
        shooter, ally, enemy = run.players
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        shooter.team = ally.team = 0
        enemy.team = 1
        run.match.switch_hero(shooter, heroes()[SIGMA])
        run.match.switch_hero(ally, heroes()[SIGMA])
        x, y, z = shooter.mover.position
        ally.mover.position = [x, y, z]
        enemy.mover.position = [x, y, z + 6]
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is enemy)
        vitals = fight.vitals_of(target)
        yaw, pitch = aim(fight._eye(shooter), (x, y + 1, z + 6))
        with patch.object(fight, "_interrupt", wraps=fight._interrupt) as interrupt:
            run.step({shooter: (yaw, pitch, 0x10), ally: (yaw, pitch, 0x10)})
            for _ in range(100):
                run.step({shooter: (yaw, pitch, 0), ally: (yaw, pitch, 0)})
                if vitals.total < 100:
                    break
            self.assertEqual(vitals.total, 60)
            self.assertEqual(fight.stuns[enemy.body] - fight.tick, 50)
            self.assertEqual(interrupt.call_count, 1)

    def test_accretion_wall_splash_damages_but_does_not_route_direct_control(self):
        run, shooter, enemy, vitals = self.arena(offset=1.2, distance=6)
        self.wall(run, sideways=False, offset=6.8)
        run.step({shooter: (0, 0, 0x10)})
        for _ in range(100):
            run.step({})
            if vitals.total < 200:
                break
        self.assertEqual(vitals.total, 160)
        self.assertNotIn(enemy.body, run.match.combat.stuns)
        self.assertGreater(enemy.mover.velocity[0], 0)
        self.assertLess(enemy.mover.velocity[2], 0)

    def test_accretion_wall_splash_damages_and_pushes_its_owner_without_stun_or_charge(self):
        for distance in (1.25, 2.65, 6):
            with self.subTest(distance=distance):
                run, shooter, _, enemy_health = self.arena(offset=0, distance=12)
                self.wall(run, sideways=False, offset=distance)
                fight = run.match.combat
                health = fight.vitals_of(next(t for t in fight._targets() if t.owner is shooter))
                script = shooter.body_script
                script.ult_seconds = 0
                script.set_ult_charge(0)
                with patch.object(
                    shooter.mover, "add_external_impulse", wraps=shooter.mover.add_external_impulse,
                ) as impact:
                    run.step({shooter: (0, 0, 0x10)})
                    for _ in range(90):
                        run.step({})
                loss = 400 - health.total
                if distance == 6:
                    self.assertEqual(loss, 0)
                    impact.assert_not_called()
                else:
                    self.assertEqual(impact.call_count, 1)
                    self.assertLess(impact.call_args.args[0][2], 0)
                    if distance == 1.25:
                        self.assertEqual(loss, 20)
                        self.assertLess(impact.call_args.args[0][2], -2.5)
                    else:
                        self.assertGreater(loss, 6)
                        self.assertLess(loss, 20)
                self.assertEqual(enemy_health.total, 200)
                self.assertNotIn(shooter.body, fight.stuns)
                self.assertEqual(script.component.entity_var(342).value(), 0)
                self.assertFalse(shooter.client.messages(combat.HIT))

    def test_close_accretion_applies_enemy_control_and_only_enemy_damage_charge(self):
        run, shooter, enemy, enemy_health = self.arena(offset=0, distance=2.1)
        fight = run.match.combat
        health = fight.vitals_of(next(t for t in fight._targets() if t.owner is shooter))
        script = shooter.body_script
        script.ult_seconds = 0
        script.set_ult_charge(0)
        yaw, pitch = aim(fight._eye(shooter), tuple(enemy.mover.position))
        run.step({shooter: (yaw, pitch, 0x10)})
        for _ in range(65):
            run.step({shooter: (yaw, pitch, 0)})
            if enemy_health.total < 200:
                break
        self.assertEqual(enemy_health.total, 130)
        self.assertEqual(health.total, 380)
        self.assertTrue(fight.is_stunned(enemy))
        self.assertFalse(fight.is_stunned(shooter))
        self.assertLess(shooter.mover.velocity[2], 0)
        self.assertEqual(script.component.entity_var(342).value(), 70)
        self.assertEqual(len(shooter.client.messages(combat.HIT)), 2)

    def test_accretion_owner_impulse_does_not_require_nonzero_blast_damage(self):
        run, shooter, _, _ = self.arena(offset=0, distance=12)
        self.wall(run, sideways=False, offset=1.25)
        fight = run.match.combat
        health = fight.vitals_of(next(t for t in fight._targets() if t.owner is shooter))
        ability = self.ability(shooter, sigma.ACCRETION)
        ability.instance.write((graph.INSTANCE, 11863), 0.0)
        with patch.object(
            shooter.mover, "add_external_impulse", wraps=shooter.mover.add_external_impulse,
        ) as impact:
            run.step({shooter: (0, 0, 0x10)})
            for _ in range(65):
                run.step({})
                if fight.rockets:
                    break
            rock, = fight.rockets
            self.assertEqual(rock.blast.amount, 0)
            self.assertEqual(rock.blast.self_impulse_scale, 1)
            for _ in range(20):
                run.step({})
        impact.assert_called_once()
        self.assertEqual(health.total, 400)

    def test_static_cover_between_rock_and_owner_blocks_accretion_self_splash(self):
        run, shooter, enemy, enemy_health = self.arena(offset=0, distance=2.75)
        fight = run.match.combat
        health = fight.vitals_of(next(t for t in fight._targets() if t.owner is shooter))
        start = shooter.mover.position[2]
        yaw, pitch = aim(fight._eye(shooter), tuple(enemy.mover.position))
        run.step({shooter: (yaw, pitch, 0x10)})
        for _ in range(65):
            if any(rock.position[2] > start + 0.8 for rock in fight.rockets):
                break
            run.step({shooter: (yaw, pitch, 0)})
        rock, = fight.rockets
        self.assertGreater(rock.position[2], start + 0.8)
        self.assertEqual(enemy_health.total, 200)
        # Add cover behind the launched rock to isolate the splash visibility check.
        self.wall(run, sideways=False, offset=0.8)
        with patch.object(
            shooter.mover, "add_external_impulse", wraps=shooter.mover.add_external_impulse,
        ) as impact:
            for _ in range(10):
                run.step({})
                if rock not in fight.rockets:
                    break
        self.assertNotIn(rock, fight.rockets)
        self.assertEqual(enemy_health.total, 130)
        self.assertTrue(fight.is_stunned(enemy))
        self.assertEqual(health.total, 400)
        impact.assert_not_called()

    def test_allied_projected_barrier_absorbs_accretion_self_splash_and_impulse(self):
        for breaking in (False, True):
            with self.subTest(breaking=breaking):
                run = Run(KANEZAKA_DM, ("Sigma", "Zarya"))
                run.match.bots.clear()
                run.match._pending_bot_scripts.clear()
                shooter, zarya = run.players
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                shooter.team = zarya.team = 0
                run.match.switch_hero(shooter, heroes()[SIGMA])
                run.match.switch_hero(zarya, heroes()[0x02E0000000000068])
                x, y, z = shooter.mover.position
                zarya.mover.position = [x + 5, y, z]
                self.wall(run, sideways=False, offset=1.25)
                fight = run.match.combat
                health = fight.vitals_of(next(t for t in fight._targets() if t.owner is shooter))
                yaw, pitch = aim(fight._eye(zarya), fight._eye(shooter))
                run.step({zarya: (yaw, pitch, 0x10)})
                bubble = sigma.particle_barrier(shooter)
                self.assertIsNotNone(bubble)
                if breaking:
                    health.particle_barriers[sigma.particle_barrier_key(bubble)] = 15
                with patch.object(
                    shooter.mover, "add_external_impulse", wraps=shooter.mover.add_external_impulse,
                ) as impact:
                    run.step({shooter: (0, 0, 0x10)})
                    for _ in range(90):
                        run.step({})
                self.assertEqual(health.total, 400)
                self.assertEqual(list(health.particle_barriers.values()), [] if breaking else [180])
                self.assertEqual(zarya.body_script.component.entity_var(87).value(), 3 if breaking else 4)
                impact.assert_not_called()

    def test_lethal_accretion_self_splash_cleans_up_flight_without_self_impulse(self):
        run, shooter, _, _ = self.arena(offset=0, distance=12)
        self.wall(run, sideways=False, offset=1.25)
        fight = run.match.combat
        health = fight.vitals_of(next(t for t in fight._targets() if t.owner is shooter))
        health.now[combat.SHIELDS_POOL] = 0
        health.now[combat.HEALTH_POOL] = 20
        script = shooter.body_script
        script.ult_seconds = 0
        script.set_ult_charge(0)
        with patch.object(
            shooter.mover, "add_external_impulse", wraps=shooter.mover.add_external_impulse,
        ) as impact:
            run.step({shooter: (0, 0, 0x10)})
            for _ in range(90):
                run.step({})
                if health.dead:
                    break
        self.assertTrue(health.dead)
        self.assertEqual(health.killer, shooter.body)
        self.assertFalse(fight.is_stunned(shooter))
        impact.assert_not_called()
        self.assertFalse(fight.rockets)
        self.assertEqual(script.component.entity_var(342).value(), 0)

    def test_accretion_direct_impulse_is_clipped_by_static_collision(self):
        from test_game_mover import box

        run, shooter, enemy, vitals = self.arena(offset=0, distance=6)
        start = tuple(enemy.mover.position)
        x, y, z = start
        floor = [(-5000, y, -5000, -5000, y, 5000, 5000, y, 5000),
                 (-5000, y, -5000, 5000, y, 5000, 5000, y, -5000)]
        run.match.collision_world = World(floor + box(x - 4, x + 4, y, y + 4, z + 1, z + 2))
        yaw, pitch = aim(run.match.combat._eye(shooter), run.match.combat._eye(enemy))
        run.step({shooter: (yaw, pitch, 0x10)})
        peak = start[2]
        for _ in range(150):
            run.step({shooter: (yaw, pitch, 0)})
            peak = max(peak, enemy.mover.position[2])
        self.assertEqual(vitals.total, 130)
        self.assertGreater(peak - start[2], 0.3)
        self.assertLess(peak - start[2], 0.9)
        self.assertFalse(enemy.mover.external_impulse)

    def test_replacing_accretion_caster_retires_an_unleased_rock(self):
        from ow174.game.content import SOLDIER

        run, shooter, _enemy, _vitals = self.arena(offset=12, distance=25)
        fight = run.match.combat
        run.step({shooter: (0, 0, 0x10)})
        for _ in range(100):
            if fight.rockets:
                break
            run.step({})
        rocket, = fight.rockets
        self.assertEqual(rocket.accretion_source[0], shooter.body)
        self.assertIsNone(rocket.shot)
        run.match.switch_hero(shooter, heroes()[SOLDIER])
        self.assertNotIn(rocket, fight.rockets)

    def test_death_between_hypersphere_launches_preserves_only_first_sphere(self):
        self.assert_hypersphere_on_owner_death(lethal=False)

    def test_posthumous_hypersphere_kill_credits_original_sigma_body(self):
        self.assert_hypersphere_on_owner_death(lethal=True)

    def assert_hypersphere_on_owner_death(self, *, lethal):
        run, shooter, enemy, enemy_health = self.arena(offset=0, distance=8)
        fight = run.match.combat
        health = fight.vitals_of(next(entry for entry in fight._targets() if entry.owner is shooter))
        health.now[combat.HEALTH_POOL] = 20
        health.now[combat.SHIELDS_POOL] = 0
        if lethal:
            enemy_health.now[combat.HEALTH_POOL] = 55
        script = shooter.body_script
        script.ult_seconds = 0
        script.set_ult_charge(73)
        body = shooter.body
        forward = aim(fight._eye(shooter), fight._eye(enemy))
        x, y, z = shooter.mover.position
        reply = aim(fight._eye(enemy), (x, y + 0.8, z))
        run.step({shooter: (*forward, FIRE)})
        for _ in range(40):
            if fight.rockets:
                break
            run.step({shooter: (*forward, 0)})
        first, = fight.rockets
        self.assertEqual(first.owner_body, body)
        self.assertEqual(enemy_health.total, 55 if lethal else 200)
        run.step({enemy: (*reply, FIRE)})
        self.assertTrue(health.dead)
        self.assertEqual(health.killer, enemy.body)
        self.assertIn(first, fight.rockets)
        for _ in range(40):
            run.step({})
            self.assertTrue(all(rocket is first for rocket in fight.rockets))
            if first not in fight.rockets:
                break
        self.assertNotIn(first, fight.rockets)
        self.assertEqual(enemy_health.dead, lethal)
        if lethal:
            self.assertEqual(enemy_health.killer, body)
            self.assertGreater(enemy_health.death_tick, health.death_tick)
        for _ in range(750):
            run.step({})
            self.assertTrue(all(rocket is first for rocket in fight.rockets))
            if shooter.body != body and not fight.is_dead(shooter):
                break
        self.assertFalse(fight.rockets)
        self.assertNotEqual(shooter.body, body)
        self.assertEqual(enemy_health.total, 0 if lethal else 145)
        self.assertEqual(script.component.entity_var(342).value(), 73)
        self.assertAlmostEqual(shooter.body_script.component.entity_var(342).value(), 73, delta=0.1)

    def test_in_flight_accretion_hits_after_owner_death_without_awarding_charge(self):
        self.assert_accretion_on_owner_death(launched=True)

    def test_death_during_accretion_windup_cancels_launch_through_respawn(self):
        self.assert_accretion_on_owner_death(launched=False)

    def assert_accretion_on_owner_death(self, *, launched):
        from ow174.game.script import nodes

        run, shooter, enemy, enemy_health = self.arena(offset=0, distance=8)
        fight = run.match.combat
        target = next(entry for entry in fight._targets() if entry.owner is shooter)
        health = fight.vitals_of(target)
        health.now[combat.HEALTH_POOL] = 20
        health.now[combat.SHIELDS_POOL] = 0
        script = shooter.body_script
        script.ult_seconds = 0
        script.set_ult_charge(73)
        x, y, z = enemy.mover.position
        forward = aim(fight._eye(shooter), (x, y + 1, z))
        x, y, z = shooter.mover.position
        reply = aim(fight._eye(enemy), (x, y + 0.8, z))
        run.step({shooter: (*forward, 0x10)})
        self.assertTrue(self.ability(shooter, sigma.ACCRETION).flags & nodes.Ability.RUNNING)
        self.assertFalse(fight.rockets)
        old_body = shooter.body
        if launched:
            for _ in range(65):
                if fight.rockets:
                    break
                run.step({shooter: (*forward, 0)})
            rock, = fight.rockets
            self.assertEqual(rock.owner_body, old_body)
        self.assertEqual(enemy_health.total, 200)
        run.step({enemy: (*reply, FIRE)})
        self.assertTrue(health.dead)
        self.assertEqual(health.killer, enemy.body)
        if launched:
            self.assertIn(rock, fight.rockets)
            for _ in range(45):
                run.step({})
                if rock not in fight.rockets:
                    break
            self.assertNotIn(rock, fight.rockets)
            self.assertEqual(fight.stuns.get(enemy.body, fight.tick) - fight.tick, 50)
        self.assertEqual(enemy_health.total, 130 if launched else 200)
        self.assertEqual(script.component.entity_var(342).value(), 73)
        for _ in range(750):
            run.step({})
            self.assertFalse(fight.rockets)
            if shooter.body != old_body and not fight.is_dead(shooter):
                break
        self.assertNotEqual(shooter.body, old_body)
        self.assertFalse(fight.is_dead(shooter))
        self.assertEqual(enemy_health.total, 130 if launched else 200)
        self.assertAlmostEqual(shooter.body_script.component.entity_var(342).value(), 73, delta=0.1)

    def test_same_hero_replacement_retires_unleased_hypersphere_flight(self):
        run, shooter, _enemy, vitals = self.arena()
        fight = run.match.combat
        with patch.object(fight.projectiles, "lease_id", return_value=None):
            run.step({shooter: (0, 0, FIRE)})
            for _ in range(40):
                if fight.rockets:
                    break
                run.step({})
        rocket, = fight.rockets
        self.assertIsNone(rocket.shot)
        self.assertEqual(rocket.owner_body, shooter.body)
        old_body = shooter.body
        self.assertTrue(run.match.switch_hero(shooter, heroes()[SIGMA]))
        self.assertNotEqual(shooter.body, old_body)
        self.assertNotIn(rocket, fight.rockets)
        script = shooter.body_script
        script.ult_seconds = 0
        script.set_ult_charge(0)
        for _ in range(60):
            run.step({})
        self.assertEqual(vitals.total, 200)
        self.assertEqual(script.component.entity_var(342).value(), 0)

    def test_accretion_interrupts_opposing_grasp_without_granting_shields(self):
        run, shooter, enemy, _vitals = self.arena(offset=0, distance=6)
        position = list(enemy.mover.position)
        run.match.switch_hero(enemy, heroes()[SIGMA])
        enemy.mover.position = position
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is enemy)
        vitals = fight.vitals_of(target)
        yaw, pitch = aim(fight._eye(shooter), fight._eye(enemy))
        grasp = next(i for i in enemy.body_script.component.instances.values() if i.graph.index == 0x1D02)
        run.step({shooter: (yaw, pitch, 0x10), enemy: (-32768, 0, 0x08)})
        self.assertTrue(grasp.states[1].active)
        for _ in range(100):
            run.step({shooter: (yaw, pitch, 0), enemy: (-32768, 0, 0)})
            if fight.is_stunned(enemy):
                break
        self.assertTrue(fight.is_stunned(enemy))
        self.assertFalse(grasp.states[1].active)
        self.assertEqual(grasp.vars[21312].value(), 0)
        self.assertEqual(vitals.total, 330)

    def test_accretion_cooldown_rejects_repeated_presses_then_allows_a_second_real_hit(self):
        run, shooter, enemy, vitals = self.arena(offset=0, distance=6)
        yaw, pitch = aim(run.match.combat._eye(shooter), run.match.combat._eye(enemy))
        ability = self.ability(shooter, 0x1BBB)
        run.step({shooter: (yaw, pitch, 0x10)})
        for _ in range(100):
            run.step({shooter: (yaw, pitch, 0)})
        self.assertEqual(vitals.total, 130)
        self.assertFalse(ability.flags & nodes.Ability.RUNNING)
        self.assertGreater(ability.remaining(), 9)
        self.assertLess(ability.remaining(), 10)
        for frame in range(100):
            run.step({shooter: (yaw, pitch, 0x10 if frame % 2 == 0 else 0)})
            self.assertFalse(ability.flags & nodes.Ability.RUNNING)
        self.assertEqual(vitals.total, 130)
        for _ in range(550):
            run.step({shooter: (yaw, pitch, 0)})
        self.assertEqual(ability.remaining(), 0)
        run.step({shooter: (yaw, pitch, 0x10)})
        self.assertTrue(ability.flags & nodes.Ability.RUNNING)
        for _ in range(100):
            run.step({shooter: (yaw, pitch, 0)})
        self.assertEqual(vitals.total, 60)

    def test_grasp_authored_duration_and_cooldown_gate_without_claiming_absorption(self):
        run, shooter, _, _ = self.arena()
        ability = self.ability(shooter, 0x1D02)
        run.step({shooter: (0, 0, 0x08)})
        self.assertTrue(ability.flags & nodes.Ability.RUNNING)
        for _ in range(125):
            run.step({})
        self.assertGreater(ability.remaining(), 11.9)
        self.assertLessEqual(ability.remaining(), 12)
        for _ in range(40):
            run.step({})
        self.assertFalse(ability.flags & nodes.Ability.RUNNING)
        run.step({shooter: (0, 0, 0x08)})
        self.assertFalse(ability.flags & nodes.Ability.RUNNING)
        for _ in range(800):
            run.step({})
        self.assertEqual(ability.remaining(), 0)
        run.step({shooter: (0, 0, 0x08)})
        self.assertTrue(ability.flags & nodes.Ability.RUNNING)

    def test_flux_charge_gate_cost_and_authored_end_without_claiming_target_effects(self):
        run, shooter, _, vitals = self.arena()
        script = shooter.body_script
        ability = self.ability(shooter, 0x1E41)
        cost = script.component.entity_var(338).value()
        self.assertEqual(cost, 1960)
        run.step({shooter: (0, 0, 0x20)})
        self.assertFalse(ability.flags & nodes.Ability.RUNNING)
        for _ in range(20):
            run.step({})
        self.assertGreater(script.component.entity_var(342).value(), 0)
        self.assertLess(script.component.entity_var(342).value(), cost)
        script.set_ult_charge(cost)
        run.step({shooter: (0, 0, 0x20)})
        self.assertTrue(ability.flags & nodes.Ability.RUNNING)
        self.assertEqual(script.component.entity_var(342).value(), 0)
        for frame in range(600):
            run.step({shooter: (0, 0, 0x20 if frame % 2 == 0 else 0)})
        self.assertFalse(ability.flags & nodes.Ability.RUNNING)
        self.assertGreater(script.component.entity_var(342).value(), 0)
        self.assertLess(script.component.entity_var(342).value(), cost)
        self.assertEqual(vitals.total, 200)  # no selected target: not a Flux damage acceptance

    def test_death_ends_running_flux_and_rejects_further_activation(self):
        run, shooter, enemy, _ = self.arena()
        script = shooter.body_script
        ability = self.ability(shooter, 0x1E41)
        script.set_ult_charge(script.component.entity_var(338).value())
        run.step({shooter: (0, 0, 0x20)})
        self.assertTrue(ability.flags & nodes.Ability.RUNNING)
        target = next(t for t in run.match.combat._targets() if t.owner is shooter)
        run.match.combat._damage(enemy, target, 1000, False, None, run.frame)
        run.step({})
        self.assertTrue(run.match.combat.is_dead(shooter))
        self.assertFalse(ability.flags & nodes.Ability.RUNNING)
        run.step({shooter: (0, 0, 0x20)})
        self.assertFalse(ability.flags & nodes.Ability.RUNNING)


if __name__ == "__main__":
    unittest.main()
