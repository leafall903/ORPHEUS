"""Ball cannon against Genji's authored Deflect state via ordinary input."""

import math
import unittest
from dataclasses import replace

import test_wrecking_ball_combat as scenes
from test_game_combat import KANEZAKA_DM, PRACTICE_RANGE, Run, aim
from test_wrecking_ball_absorb import launch_grenade
from test_wrecking_ball_control import ANA
from test_wrecking_ball_fireball import relocate

from ow174.game import ball, combat, deflection, observers, stats
from ow174.game.collision import World
from ow174.game.commands import Command
from ow174.game.content import heroes
from ow174.game.script import expr
from ow174.game.script.decode import read_chunk
from ow174.game.script.driver import ULT_CHARGE, ULT_COST, BodyStream
from ow174.game.statescript import chunk


class BallDeflectTests(unittest.TestCase):
    def test_off_axis_return_respects_cover_team_and_total_path_distance(self):
        run = Run(KANEZAKA_DM, ('Ball', 'Genji', 'Soldier'))
        player, genji, soldier = run.players
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        player.team, genji.team, soldier.team = 0, 1, 0
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        run.match.switch_hero(player, heroes()[ball.HERO])
        run.match.switch_hero(genji, heroes()[deflection.GENJI])
        x, y, z = player.mover.position
        relocate(run, genji, (x, y, z + 5))
        relocate(run, soldier, (x + 2.94, y, z + 1))
        for owner in (player, genji):
            owner.body_script.ult_seconds = 0
            owner.body_script.set_ult_charge(0)
        for _ in range(60):
            run.step({})

        facing = 24576
        run.step({genji: (facing, 0, 16)})
        for _ in range(5):
            run.step({genji: (facing, 0, 0)})
        reflect = next(i for i in genji.body_script.component.instances.values() if i.graph.index == 0x03A3)
        fight = run.match.combat
        vitals = {target.owner: fight.vitals_of(target) for target in fight._targets()}
        charge = genji.body_script.component.entity_var(ULT_CHARGE)
        weapon = ball.instance(player, 0x1514)
        angles = aim(fight._eye(player), (x, y + 0.55, z + 5))
        clear = run.match.collision_world
        wall = z + 2.5
        a, b = (x + 0.9, y, wall), (x + 2.2, y, wall)
        c, d = (x + 2.2, y + 3, wall), (x + 0.9, y + 3, wall)
        run.match.collision_world = World([(*a, *b, *c), (*a, *c, *d)])

        # This wall lies to the side of the outbound shot, on the angled return.
        for ammo, cover, allied_to_genji in ((79, True, False), (78, False, True), (77, False, False)):
            with self.subTest(cover=cover, allied_to_genji=allied_to_genji):
                if not cover:
                    run.match.collision_world = clear
                soldier.team = genji.team if allied_to_genji else player.team
                for _ in range(12):
                    run.step({genji: (facing, 0, 0)})
                self.assertTrue(reflect.state(10).active)
                run.step({player: (*angles, 1), genji: (facing, 0, 0)})
                landed = not cover and not allied_to_genji
                self.assertEqual(weapon.vars[53].value(), ammo)
                self.assertEqual(vitals[player].total, 600)
                self.assertEqual(vitals[genji].total, 200)
                self.assertEqual(vitals[soldier].total, 195 if landed else 200)
                self.assertEqual(charge.value(), 5 if landed else 0)
                self.assertEqual(len(genji.client.messages(combat.HIT)), int(landed))
                self.assertFalse(player.client.messages(combat.HIT))

        # The return begins near z + 3.94 and travels diagonally at 45 degrees.
        # A 13 m return crosses the 15 m falloff threshold only with the outgoing leg.
        # A 98 m return exceeds the 100 m budget only with that same outgoing leg.
        for ammo, distance, minimum, maximum in ((76, 13, 4.2, 4.7), (75, 94, 1.49, 1.51), (74, 98, 0, 0)):
            with self.subTest(return_distance=distance):
                offset = distance / math.sqrt(2)
                relocate(run, soldier, (x + offset, y, z + 3.94 - offset))
                for _ in range(12):
                    run.step({genji: (facing, 0, 0)})
                self.assertTrue(reflect.state(10).active)
                health, previous_charge = vitals[soldier].total, charge.value()
                hits = len(genji.client.messages(combat.HIT))
                run.step({player: (*angles, 1), genji: (facing, 0, 0)})
                damage = health - vitals[soldier].total
                self.assertGreaterEqual(damage, minimum)
                self.assertLessEqual(damage, maximum)
                self.assertAlmostEqual(charge.value() - previous_charge, damage, places=5)
                self.assertEqual(len(genji.client.messages(combat.HIT)) - hits, int(damage > 0))
                self.assertEqual(weapon.vars[53].value(), ammo)

    def test_matching_quick_melee_tags_block_front_and_hit_rear(self):
        for frontal in (False, True):
            with self.subTest(frontal=frontal):
                run, player, genji, _, own = scenes.BallCombatTests().scenario(distance=1.5)
                run.match.switch_hero(genji, heroes()[deflection.GENJI])
                x, y, z = player.mover.position
                genji.mover.position[:] = [x, y, z + 1.5]
                for _ in range(40):
                    run.step({})
                facing = -32768 if frontal else 0
                run.step({genji: (facing, 0, 16)})
                for _ in range(5):
                    run.step({genji: (facing, 0, 0)})
                fight = run.match.combat
                target = next(t for t in fight._targets() if t.owner is genji)
                angles = aim(fight._eye(player), (x, y + 1, z + 1.5))
                for frame in range(40):
                    run.step({player: (*angles, 0x800 if frame == 0 else 0), genji: (facing, 0, 0)})
                self.assertEqual(fight.vitals_of(target).total, 200 if frontal else 170)
                self.assertEqual(own.total, 600)
                self.assertEqual(len(genji.client.messages(combat.HIT)), 0)

    def test_reflected_cannon_awards_genji_effective_armour_damage_and_caps_charge(self):
        for capped in (False, True):
            with self.subTest(capped=capped):
                run, player, genji, _, own = scenes.BallCombatTests().scenario(distance=4)
                position = tuple(genji.mover.position)
                run.match.switch_hero(genji, heroes()[deflection.GENJI])
                genji.mover.position[:] = position
                for _ in range(40):
                    run.step({})
                script = genji.body_script
                script.ult_seconds = 0
                player.body_script.ult_seconds = 0
                player.body_script.set_ult_charge(0)
                cost = script.component.entity_var(ULT_COST).value()
                self.assertGreater(cost, 2.5)
                script.set_ult_charge(cost - 1 if capped else 0)
                run.step({genji: (-32768, 0, 16)})
                for _ in range(5):
                    run.step({genji: (-32768, 0, 0)})
                fight = run.match.combat
                x, y, z = genji.mover.position
                angles = aim(fight._eye(player), (x, y + 0.55, z))
                run.step({player: (*angles, 1), genji: (-32768, 0, 0)})

                self.assertEqual(own.total, 597.5)
                self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), cost if capped else 2.5)
                self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), 0)
                self.assertEqual(len(genji.client.messages(combat.HIT)), 1)
                self.assertFalse(player.client.messages(combat.HIT))

    def nano_deflect_scene(self, names, placements):
        run = Run(replace(PRACTICE_RANGE, team_sizes=(6, 6)), names)
        match = run.match
        match.bots.clear()
        match._pending_bot_scripts.clear()
        x, y, z = run.players[0].mover.position
        for actor, (hero, team, dx, dz) in zip(run.players, placements, strict=True):
            actor.team = team
            self.assertTrue(match.switch_hero(actor, heroes()[hero]))
            relocate(run, actor, (x + dx, y, z + dz))
            actor.body_script.ult_seconds = 0
            actor.body_script.set_ult_charge(0)
        for _ in range(40):
            run.step({})
        return run

    def cast_nano(self, run, pairs):
        fight = run.match.combat
        casts = {}
        for caster, target in pairs:
            script = caster.body_script
            script.set_ult_charge(script.component.entity_var(ULT_COST).value())
            tx, ty, tz = target.mover.position
            casts[caster] = (*aim(fight._eye(caster), (tx, ty + 0.8, tz)), 32)
        run.step(casts)
        for _ in range(13):
            run.step({})

    def test_nano_cannon_reflection_uses_genji_offense_and_ball_defense(self):
        for ball_nano, genji_nano, damage in (
            (False, False, 5), (True, False, 2.5), (False, True, 7.5), (True, True, 3.75),
        ):
            for adaptive in (False, True):
                with self.subTest(ball_nano=ball_nano, genji_nano=genji_nano, adaptive=adaptive):
                    run = self.nano_deflect_scene(('Ball', 'Genji', 'Ana', 'Enemy Ana'), (
                        (ball.HERO, 0, 0, 0), (deflection.GENJI, 1, 0, 4),
                        (ANA, 0, -5, 0), (ANA, 1, 5, 4),
                    ))
                    player, genji, ana, enemy_ana = run.players
                    match = run.match
                    x, y, z = player.mover.position
                    fight = match.combat
                    vitals = {target.owner: fight.vitals_of(target) for target in fight._targets()}
                    own, defender = vitals[player], vitals[genji]
                    self.cast_nano(run, ((caster, target) for enabled, caster, target in (
                        (ball_nano, ana, player), (genji_nano, enemy_ana, genji),
                    ) if enabled))
                    self.assertEqual(own.nano_until > run.frame, ball_nano)
                    self.assertEqual(defender.nano_until > run.frame, genji_nano)
                    deadline = max(own.nano_until, defender.nano_until)
                    if deadline:
                        run.frame = deadline - 14
                        match.update(run.frame * combat.TICK, run.frame, frames=False)
                    if adaptive:
                        run.step({player: (0, 0, 16)})
                        self.assertGreater(own.temporary_shields, damage)
                    shield = own.temporary_shields
                    run.step({genji: (-32768, 0, 16)})
                    for _ in range(5):
                        run.step({genji: (-32768, 0, 0)})
                    guard = next(i for i in genji.body_script.component.instances.values()
                                 if i.graph.index == 0x03A3)
                    self.assertTrue(guard.state(10).active)
                    angles = aim(fight._eye(player), (x, y + 0.55, z + 4))
                    run.step({player: (*angles, 1), genji: (-32768, 0, 0)})
                    dealt = damage if adaptive else damage / 2
                    self.assertEqual(own.total, 600 + shield - dealt)
                    self.assertEqual(own.temporary_shields, shield - dealt if adaptive else 0)
                    self.assertEqual(defender.total, 200)
                    self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), dealt)
                    charge = genji.body_script.component.entity_var(ULT_CHARGE)
                    self.assertEqual(charge.value(), 0 if adaptive else dealt)
                    self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), 0)
                    hit, = genji.client.messages(combat.HIT)
                    self.assertEqual(hit['+0x78']['+0x0']['+0x0'], player.body)
                    self.assertFalse(player.client.messages(combat.HIT))
                    if deadline:
                        for _ in range(14):
                            run.step({genji: (-32768, 0, 0)})
                        self.assertGreaterEqual(run.frame, deadline)
                        self.assertTrue(guard.state(10).active)
                        run.step({player: (*angles, 1), genji: (-32768, 0, 0)})
                        baseline = 5 if adaptive else 2.5
                        self.assertEqual(own.total, 600 + shield - dealt - baseline)
                        self.assertEqual(charge.value(), 0 if adaptive else dealt + baseline)

    def test_nano_grenade_reflection_replaces_launch_scalar_with_genjis(self):
        for original_nano in (False, True):
            with self.subTest(original_nano=original_nano):
                run = self.nano_deflect_scene(('Ball', 'Genji', 'Thrower', 'Ana', 'Enemy Ana'), (
                    (ball.HERO, 0, 0, 0), (deflection.GENJI, 1, 0, 3),
                    (ANA, 0, 0, 8), (ANA, 0, -6, 8), (ANA, 1, 6, 3),
                ))
                player, genji, thrower, ana, enemy_ana = run.players
                fight = run.match.combat
                self.cast_nano(run, ((ana, thrower),) if original_nano else ((enemy_ana, genji),))
                vitals = {target.owner: fight.vitals_of(target) for target in fight._targets()}
                self.assertEqual(vitals[thrower].nano_until > run.frame, original_nano)
                self.assertEqual(vitals[genji].nano_until > run.frame, not original_nano)
                self.assertEqual(vitals[player].nano_until, 0)
                run.step({genji: (0, 0, 16)})
                grenade = launch_grenade(self, run, genji, thrower, {}, effect=combat.ANA_GRENADE_EFFECT)
                self.assertEqual(grenade.source_scale, 1.5 if original_nano else 1)
                for _ in range(24):
                    run.step({})
                    if grenade.shooter is genji or grenade not in fight.rockets:
                        break
                self.assertIs(grenade.shooter, genji)
                self.assertIn(grenade, fight.rockets)
                self.assertEqual(grenade.source_scale, 1 if original_nano else 1.5)
                x, y, z = thrower.mover.position
                relocate(run, thrower, (x - 10, y, z))
                relocate(run, player, (x, y, z))
                for _ in range(35):
                    run.step({})
                    if grenade not in fight.rockets:
                        break
                self.assertNotIn(grenade, fight.rockets)
                damage = 45 if original_nano else 70
                self.assertEqual(vitals[player].total, 600 - damage)
                self.assertTrue(vitals[player].healing_block)
                self.assertEqual(genji.body_script.component.entity_var(ULT_CHARGE).value(), damage)
                self.assertEqual(thrower.stats.value(stats.HERO_DAMAGE), 0)

    def test_lethal_return_credits_genji_and_retires_ball_weapon_at_respawn(self):
        run, player, genji, _, own = scenes.BallCombatTests().scenario(distance=4)
        position = tuple(genji.mover.position)
        run.match.switch_hero(genji, heroes()[deflection.GENJI])
        genji.mover.position[:] = position
        for owner in (player, genji):
            owner.body_script.ult_seconds = 0
            owner.body_script.set_ult_charge(0)
        for _ in range(40):
            run.step({})

        fight = run.match.combat
        script = player.body_script
        weapon = ball.instance(player, 0x1514)
        old_body = player.body
        genji_charge = genji.body_script.component.entity_var(ULT_CHARGE)
        ball_charge = script.component.entity_var(ULT_CHARGE)
        # Stage a living victim with four health. Only the returned round can kill.
        own.damage(own.total - 4 + combat.ARMOUR_BLOCK)
        fight._sync_body_health(player, own)
        self.assertEqual(own.total, 4)
        self.assertFalse(fight.is_dead(player))
        run.step({genji: (-32768, 0, 16)})
        for _ in range(5):
            run.step({genji: (-32768, 0, 0)})
        reflect = next(i for i in genji.body_script.component.instances.values() if i.graph.index == 0x03A3)
        self.assertTrue(reflect.state(10).active)
        target = next(t for t in fight._targets() if t.owner is genji)
        genji_vitals = fight.vitals_of(target)
        angles = aim(fight._eye(player), (position[0], position[1] + 0.55, position[2]))
        stream = BodyStream(old_body)
        stream.base = observers.snapshot(script)
        known = {index: value[0] for index, value in stream.base.instances.items()}
        run.step({player: (*angles, 1), genji: (-32768, 0, 0)})

        self.assertTrue(fight.is_dead(player))
        self.assertEqual(own.killer, genji.body)
        self.assertEqual(own.death_tick, fight.tick)
        self.assertTrue(script.component.owner_dead)
        self.assertEqual(weapon.vars[53].value(), 79)
        self.assertEqual(genji_vitals.total, 200)
        self.assertEqual(genji_charge.value(), 4)
        self.assertEqual(ball_charge.value(), 0)
        self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), 4)
        self.assertEqual(genji.stats.value(stats.ELIMINATIONS), 1)
        self.assertEqual(player.stats.value(stats.DEATHS), 1)
        self.assertEqual(player.stats.value(stats.ELIMINATIONS), 0)
        hit, = genji.client.messages(combat.HIT)
        kill, = genji.client.messages(combat.KILL_NOTICE)
        death, = player.client.messages(combat.DEATH_NOTICE)
        self.assertEqual(hit["+0x78"], {"+0x0": {"+0x0": old_body}, "+0x4": 4, "+0x8": combat.KILLING})
        self.assertEqual(kill["+0x84"], {"+0x0": old_body})
        self.assertEqual(death, {"+0x78": combat.KILLED_BY, "+0x7C": {"+0x0": genji.body}})
        self.assertFalse(player.client.messages(combat.HIT))
        self.assertFalse(player.client.messages(combat.KILL_NOTICE))
        delta, _ = observers.delta(script, stream, observers.snapshot(script))
        wire = chunk(delta, script.frame, 0)
        decoded = read_chunk(wire.getvalue(), wire.count, known)
        feed = decoded.instances[next(index for index, graph in known.items() if graph == 0x01CF)]
        self.assertEqual(feed.states[108], (True, {"fresh": True, "count": 1}))
        self.assertEqual(feed.vars[392], (expr.Entity(genji.body), ()))
        self.assertEqual(feed.vars[1821], (False, ()))

        for _ in range(20):
            run.step({player: (*angles, 1)})
            self.assertFalse(weapon.state(9).active)
            self.assertEqual(weapon.vars[53].value(), 79)
        for _ in range(640):
            run.step({})
            if player.body_script is not script:
                break
        self.assertTrue(script.retired)
        self.assertNotEqual(player.body, old_body)
        self.assertFalse(fight.is_dead(player))
        replacement = ball.instance(player, 0x1514)
        self.assertEqual(replacement.vars[53].value(), 80)
        self.assertEqual(genji.stats.value(stats.ELIMINATIONS), 1)
        self.assertEqual(player.stats.value(stats.DEATHS), 1)
        self.assertFalse(reflect.state(10).active)
        angles = aim(fight._eye(player), (position[0], position[1] + 0.55, position[2]))
        for _ in range(80):
            run.step({player: (*angles, 1)})
            if genji_vitals.total < 200:
                break
        self.assertEqual(genji_vitals.total, 195)
        self.assertEqual(replacement.vars[53].value(), 79)

    def test_deflect_world_expiry_admits_cannon_and_melee_without_genji_commands(self):
        for attack, jump in ((1, False), (1, True), (0x800, True)):
            for timing in ('before', 'expiry'):
                with self.subTest(attack=attack, jump=jump, timing=timing):
                    self.check_commandless_deflect_expiry(attack, jump, timing)
        with self.subTest(attack='cannon', jump=True, timing='late'):
            self.check_commandless_deflect_expiry(1, True, 'late')

    def check_commandless_deflect_expiry(self, attack, jump, timing):
        distance = 4 if attack == 1 else 1.5
        run, player, genji, _, own = scenes.BallCombatTests().scenario(distance=distance)
        match = run.match
        fight = match.combat
        self.assertTrue(match.switch_hero(genji, heroes()[deflection.GENJI]))
        x, y, z = player.mover.position
        relocate(run, genji, (x, y, z + distance))
        for _ in range(40):
            run.step({})
        for actor in (player, genji):
            actor.body_script.ult_seconds = 0
            actor.body_script.set_ult_charge(0)
        if timing == 'late':
            run.step({player: (0, 0, 16)})
            activation = run.frame + 1
            run.frame = activation + 126
            match.update(run.frame * combat.TICK, run.frame, frames=False)
            genji.take_commands([Command(activation, yaw=-32768, buttons=16)])
        else:
            run.step({player: (0, 0, 16), genji: (-32768, 0, 16)})
        source = genji.body_script
        graph = next(i for i in source.component.instances.values() if i.graph.index == 0x03A3)
        self.assertTrue(graph.state(10).active)
        self.assertTrue(graph.state(59).active)
        timer = graph.state(1)
        self.assertEqual(timer.ms, 2000)
        deadline = (timer.start + timer.ms) // source.frame_ms
        frozen = source.frame, source.component.now
        before = timing == 'before'
        endpoint = max(run.frame, deadline - int(before))
        if not jump:
            for tick in range(run.frame + 1, endpoint):
                match.update(tick * combat.TICK, tick, frames=False)
        match.update(endpoint * combat.TICK, endpoint, frames=False)
        angles = aim(fight._eye(player), (x, y + 0.85, z + distance))
        player.take_commands([Command(endpoint, yaw=angles[0], pitch=angles[1], buttons=attack)])
        self.assertEqual((source.frame, source.component.now), frozen)
        self.assertTrue(graph.state(10).active)
        target = next(t for t in fight._targets() if t.owner is genji)
        damage = 0 if before else (5 if attack == 1 else 30)
        self.assertEqual(fight.vitals_of(target).total, 200 - damage)
        self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), damage)
        self.assertEqual(own.temporary_shields, 170 if before and attack == 1 else 175)

    def test_cannon_reflects_frontally_and_respects_rear_expiry_and_cover(self):
        for condition in ('front', 'rear', 'expired', 'wall'):
            with self.subTest(condition=condition):
                run, player, genji, _, own = scenes.BallCombatTests().scenario(distance=4)
                run.match.switch_hero(genji, heroes()[deflection.GENJI])
                x, y, z = player.mover.position
                genji.mover.position[:] = [x, y, z + 4]
                fight = run.match.combat
                for _ in range(40):
                    run.step({})
                facing = 0 if condition == 'rear' else -32768
                run.step({player: (0, 0, 16), genji: (facing, 0, 16)})
                for _ in range(150 if condition == 'expired' else 5):
                    run.step({genji: (facing, 0, 0)})
                graph = next(i for i in genji.body_script.component.instances.values()
                             if i.graph.index == 0x03A3)
                self.assertEqual(graph.states[10].active, condition != 'expired')
                target = next(t for t in fight._targets() if t.owner is genji)
                x, y, z = player.mover.position
                if condition == 'wall':
                    wall = z + 2
                    run.match.collision_world = World([
                        (x - 3, y, wall, x + 3, y, wall, x + 3, y + 4, wall),
                        (x - 3, y, wall, x + 3, y + 4, wall, x - 3, y + 4, wall),
                    ])
                angles = aim(fight._eye(player), (target.feet[0], target.feet[1] + 0.55, target.feet[2]))
                script = player.body_script
                script.ult_seconds = 0
                script.set_ult_charge(0)
                genji.body_script.ult_seconds = 0
                genji.body_script.set_ult_charge(0)
                run.step({player: (*angles, 1), genji: (facing, 0, 0)})
                self.assertEqual(genji.body_script.component.entity_var(ULT_CHARGE).value(), 0)
                self.assertEqual(ball.instance(player, 0x1514).vars[53].value(), 79)
                direct = condition in ('rear', 'expired')
                self.assertEqual(fight.vitals_of(target).total, 195 if direct else 200)
                self.assertEqual(own.temporary_shields, 170 if condition == 'front' else 175)
                self.assertEqual(len(genji.client.messages(combat.HIT)), int(condition == 'front'))
                self.assertEqual(len(player.client.messages(combat.HIT)), int(direct))
                self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 5 if direct else 0)
