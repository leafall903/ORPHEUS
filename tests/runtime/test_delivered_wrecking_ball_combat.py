"""104319 Ball combat reconstruction. These are server tests, not client acceptance."""

import math
import struct
import unittest

from test_game_combat import KANEZAKA_DM, Run, aim

from ow174.game import combat, mover, server_snapshot, world
from ow174.game.collision import World
from ow174.game.commands import Command
from ow174.game.content import heroes
from ow174.game.script import nodes
from ow174.game.script.driver import HUD_HEALTH, HUD_MAX_HEALTH, ULT_CHARGE, ULT_COST

BALL = 0x02E00000000001CA


class TemporaryShieldTests(unittest.TestCase):
    def test_stored_state_tracks_shield_damage_and_activation_identity(self):
        vitals = combat.Vitals(1, 500, 100)

        def digest():
            stored = server_snapshot._Graph({}, None)
            root = stored.value(vitals)
            return server_snapshot.snapshot_digest({
                'schema': server_snapshot.SCHEMA, 'scope': server_snapshot.SCOPE,
                'restorable': False, 'roots': {'vitals': root}, 'nodes': stored.finish(), 'assets': [],
            })

        initial = digest()
        vitals.set_temporary_shields(175)
        shielded = digest()
        self.assertNotEqual(initial, shielded)
        vitals.damage(50)
        damaged = digest()
        self.assertNotEqual(shielded, damaged)
        vitals.adaptive_activation = (10, 1)
        adaptive = digest()
        self.assertNotEqual(damaged, adaptive)
        vitals.adaptive_until = 1602
        self.assertNotEqual(adaptive, digest())
        vitals.adaptive_until = 0
        self.assertEqual(adaptive, digest())
        vitals.grasp_activation = (11, 1)
        grasp = digest()
        self.assertNotEqual(adaptive, grasp)
        vitals.grasp_decay = 1600
        self.assertNotEqual(grasp, digest())
        vitals.grasp_decay = None
        self.assertEqual(grasp, digest())

    def test_shields_absorb_before_armour_and_cannot_be_healed(self):
        vitals = combat.Vitals(1, 500, 100)
        vitals.set_temporary_shields(175)
        self.assertEqual(vitals.damage(200), 195)  # shield 175, armour mitigates remaining 25 to 20
        self.assertEqual((vitals.total, vitals.now[world.ARMOUR_POOL]), (580, 80))
        self.assertEqual(vitals.heal(500), 20)
        self.assertEqual((vitals.total, vitals.maximum), (600, 775))
        self.assertEqual(vitals.temporary_shields, 0)

    def test_permanent_shields_and_expiry_are_separate(self):
        vitals = combat.Vitals(1, 200, shields=100)
        vitals.set_temporary_shields(100)
        vitals.damage(75)
        self.assertEqual(vitals.now[world.SHIELDS_POOL], 100)
        vitals.set_temporary_shields(0)
        parts = vitals.component(vitals.ids)[51]
        permanent = struct.unpack('<IffI4B', parts[world.SHIELDS_POOL * world.POOLS])
        temporary = struct.unpack('<IffI4B', parts[world.SHIELDS_POOL * world.POOLS + 1])
        self.assertNotEqual(permanent[0], temporary[0])
        self.assertEqual(temporary[1:3], (0, 0))
        self.assertEqual(vitals.total, 300)


class BallCombatTests(unittest.TestCase):
    def scenario(self, *, npc=False, ally=False, distance=6):
        run = Run(KANEZAKA_DM, ('Ball', 'Enemy'))
        ball, enemy = run.players
        run.match.switch_hero(ball, heroes()[BALL])
        x, y, z = ball.mover.position
        enemy.mover.position = [x, y, z + distance]
        chosen = next(b for b in run.match.bots if b.team != ball.team) if npc else None
        run.match.bots[:] = [chosen] if npc else []
        run.match._pending_bot_scripts.clear()
        if chosen is not None:
            chosen.position = (x, y, z + distance)
            chosen.route = []
            chosen.firing = False
            enemy.mover.position = [x + 100, y, z]
        if ally:
            run.match.game_map = run.match.game_map.__class__(
                **{**run.match.game_map.__dict__, 'free_for_all': False},
            )
            (chosen if npc else enemy).team = ball.team
        for _ in range(40):
            run.step({})
        fight = run.match.combat
        owner = chosen if npc else enemy
        target = next(t for t in fight._targets() if t.owner is owner)
        own = next(t for t in fight._targets() if t.owner is ball)
        return run, ball, enemy, target, fight.vitals_of(own)

    def activate(self, run, ball):
        run.step({ball: (0, 0, 16)})

    def test_input_grants_shield_for_enemy_players_and_npcs_once(self):
        for npc in (False, True):
            with self.subTest(npc=npc):
                run, ball, _, _, vitals = self.scenario(npc=npc)
                self.activate(run, ball)
                self.assertEqual(vitals.temporary_shields, 175)
                self.assertEqual(ball.body_script.component.entity_var(HUD_HEALTH).value(), 775)
                self.assertEqual(ball.body_script.component.entity_var(HUD_MAX_HEALTH).value(), 775)
                vitals.damage(50)
                for _ in range(10):
                    run.step({ball: (0, 0, 16)})
                self.assertEqual(vitals.temporary_shields, 125)

    def test_allies_and_dead_targets_do_not_increase_shields(self):
        for npc in (False, True):
            run, ball, _, _, vitals = self.scenario(npc=npc, ally=True)
            self.activate(run, ball)
            self.assertEqual(vitals.temporary_shields, 100)
        run, ball, _, target, vitals = self.scenario(npc=True)
        fight = run.match.combat
        fight._damage(ball, target, 10000, False, None, run.frame)
        self.activate(run, ball)
        self.assertEqual(vitals.temporary_shields, 100)

    def test_authored_expiry_removes_shields_and_updates_hud(self):
        run, ball, _, _, vitals = self.scenario()
        self.activate(run, ball)
        self.assertEqual(vitals.temporary_shields, 175)
        for _ in range(565):
            run.step({})
        self.assertEqual((vitals.temporary_shields, vitals.maximum, vitals.total), (0, 600, 600))
        self.assertEqual(ball.body_script.component.entity_var(HUD_MAX_HEALTH).value(), 600)

    def test_adaptive_shield_expires_on_world_tick_without_ball_commands(self):
        run, player, soldier, _, own = self.scenario()
        self.activate(run, player)
        match = run.match
        fight = match.combat
        script = player.body_script
        start = run.frame
        expiry = start + 562
        frozen = script.frame, script.component.now
        x, y, z = player.mover.position
        rifle = aim(fight._eye(soldier), (x, y + 0.85, z))

        def update(frame, buttons):
            run.frame = frame
            match.update(frame * combat.TICK, frame, frames=False)
            soldier.take_commands([Command(frame, yaw=rifle[0], pitch=rifle[1], buttons=buttons)])

        update(expiry - 10, 1)
        self.assertEqual(own.temporary_shields, 155)
        self.assertEqual(own.total, 755)
        update(expiry - 9, 0)
        update(expiry - 1, 0)
        self.assertEqual(own.temporary_shields, 155)
        update(expiry, 0)
        self.assertEqual((script.frame, script.component.now), frozen)
        self.assertEqual((own.temporary_shields, own.maximum, own.total), (0, 600, 600))
        self.assertEqual(script.component.entity_var(HUD_MAX_HEALTH).value(), 600)
        for frame in range(expiry + 1, expiry + 10):
            update(frame, 1)
            if own.total < 600:
                break
        self.assertEqual(own.total, 585)
        player.take_commands([Command(expiry)])
        self.assertEqual(own.total, 585)

    def test_late_adaptive_shield_cast_cannot_restore_expired_pool(self):
        run, player, _, _, own = self.scenario()
        start = run.frame + 1
        run.frame = start + 565
        run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        player.take_commands([Command(start, buttons=16)])
        self.assertIsNotNone(own.adaptive_activation)
        self.assertEqual((own.temporary_shields, own.maximum, own.total), (0, 600, 600))
        player.take_commands([Command(start + 1, buttons=16), Command(run.frame)])
        self.assertEqual((own.temporary_shields, own.maximum, own.total), (0, 600, 600))

    def test_cooldown_rejects_repeated_presses_until_reuse_is_ready(self):
        run, ball, _, _, vitals = self.scenario()
        self.activate(run, ball)
        instance = next(i for i in ball.body_script.component.instances.values() if i.graph.index == 0x1566)
        ability = instance.states[0]
        self.assertAlmostEqual(ability.remaining(), 15, delta=0.02)
        vitals.damage(50)
        for tick in range(1, 937):
            run.step({ball: (0, 0, 16 if tick % 2 == 0 else 0)})
            self.assertEqual(instance.states[6].counter, 1)
        self.assertEqual(vitals.temporary_shields, 0)
        run.step({ball: (0, 0, 0)})
        run.step({ball: (0, 0, 16)})
        self.assertEqual(instance.states[6].counter, 2)
        self.assertEqual(vitals.temporary_shields, 175)
        self.assertGreater(ability.remaining(), 14.9)

    def test_radius_excludes_distant_enemy_and_counts_two_near_enemies(self):
        run, ball, _, _, vitals = self.scenario(distance=36)
        self.activate(run, ball)
        self.assertEqual(vitals.temporary_shields, 100)
        run, ball, _, _, vitals = self.scenario()
        bot = next(b for b in Run().match.bots if b.team != ball.team)
        bot.position = tuple(ball.mover.position)
        bot.route = []
        bot.firing = False
        run.match.bots.append(bot)
        self.activate(run, ball)
        self.assertEqual(vitals.temporary_shields, 250)

    def test_adaptive_shield_uses_eye_origin_at_vertical_range_boundary(self):
        for above, expected in ((True, 175), (False, 100)):
            with self.subTest(enemy_above=above):
                run, player, enemy, _, vitals = self.scenario(distance=0.5)
                elevated = enemy if above else player
                x, y, z = elevated.mover.position
                elevated.mover = mover.Mover((x, y + 9, z), world=run.match.collision_world,
                                             data=mover.mover_data(elevated.hero.body))
                self.activate(run, player)
                self.assertGreater(elevated.mover.position[1], y + 8)
                self.assertEqual(vitals.temporary_shields, expected)

    def test_adaptive_shield_counts_enemy_behind_static_cover(self):
        run, player, _, target, vitals = self.scenario()
        fight = run.match.combat
        x, y, z = player.mover.position
        wall = z + 3
        run.match.collision_world = World([
            (x - 3, y - 1, wall, x + 3, y - 1, wall, x + 3, y + 4, wall),
            (x - 3, y - 1, wall, x + 3, y + 4, wall, x - 3, y + 4, wall),
        ])
        health = fight.vitals_of(target).total
        angles = aim(fight._eye(player), (target.feet[0], target.feet[1] + 1, target.feet[2]))
        run.step({player: (*angles, 1)})
        weapon = next(i for i in player.body_script.component.instances.values() if i.graph.index == 0x1514)
        self.assertEqual(weapon.vars[53].value(), 79)
        self.assertEqual(fight.vitals_of(target).total, health)
        self.assertFalse(player.client.messages(combat.HIT))

        self.activate(run, player)
        self.assertEqual(vitals.temporary_shields, 175)

    def test_enemy_rifle_hits_shield_then_armour_and_death_stops_ball_fire(self):
        run, ball, enemy, _, vitals = self.scenario()
        self.activate(run, ball)
        fight = run.match.combat
        angles = aim(fight._eye(enemy), (ball.mover.position[0], ball.mover.position[1] + 1,
                                      ball.mover.position[2]))
        for _ in range(55):
            run.step({enemy: (*angles, 1)})
        self.assertLess(vitals.temporary_shields, 175)
        own = next(t for t in fight._targets() if t.owner is ball)
        fight._damage(enemy, own, 10000, False, None, run.frame)
        self.assertTrue(vitals.dead)
        self.assertEqual(vitals.total, 0)
        before = len(ball.client.messages(combat.HIT))
        for _ in range(20):
            run.step({ball: (0, 0, 1 | 16)})
        self.assertEqual(len(ball.client.messages(combat.HIT)), before)

    def test_quad_cannons_deal_damage_to_enemy_player_and_npc(self):
        for npc in (False, True):
            with self.subTest(npc=npc):
                run, ball, _, target, _ = self.scenario(npc=npc)
                fight = run.match.combat
                vitals = fight.vitals_of(target)
                before = vitals.total
                angles = aim(fight._eye(ball), (target.feet[0], target.feet[1] + 1, target.feet[2]))
                for _ in range(20):
                    run.step({ball: (*angles, 1)})
                self.assertLess(vitals.total, before)
                self.assertTrue(ball.client.messages(combat.HIT))

    def test_quad_cannon_misses_spend_ammo_release_stops_and_reload_restores(self):
        run, ball, _, target, _ = self.scenario()
        fight = run.match.combat
        vitals = fight.vitals_of(target)
        weapon = next(i for i in ball.body_script.component.instances.values() if i.graph.index == 0x1514)
        self.assertEqual(weapon.vars[53].value(), 80)
        for _ in range(30):
            run.step({ball: (-32768, 0, 1)})
        self.assertEqual(weapon.vars[53].value(), 68)
        self.assertEqual(vitals.total, 200)
        self.assertFalse(ball.client.messages(combat.HIT))
        run.step({})
        # Native volley OnEnd reports through the release timestamp (inclusive).
        released_ammo = weapon.vars[53].value()
        self.assertEqual(released_ammo, 67)
        self.assertFalse(weapon.states[9].active)
        for _ in range(10):
            run.step({})
        self.assertEqual(weapon.vars[53].value(), released_ammo)
        run.step({ball: (0, 0, 1024)})
        for _ in range(130):
            run.step({})
        self.assertEqual(weapon.vars[53].value(), 80)

    def test_quad_cannons_do_not_damage_allied_players_or_npcs(self):
        for npc in (False, True):
            with self.subTest(npc=npc):
                run, ball, _, target, _ = self.scenario(npc=npc, ally=True)
                fight = run.match.combat
                vitals = fight.vitals_of(target)
                before = vitals.total
                angles = aim(fight._eye(ball), (target.feet[0], target.feet[1] + 1, target.feet[2]))
                for _ in range(20):
                    run.step({ball: (*angles, 1)})
                self.assertEqual(vitals.total, before)
                self.assertFalse(ball.client.messages(combat.HIT))

    def test_quick_melee_damages_enemy_player_and_npc_once(self):
        for npc in (False, True):
            with self.subTest(npc=npc):
                run, ball, _, target, _ = self.scenario(npc=npc, distance=1.5)
                x, y, z = ball.mover.position
                fight = run.match.combat
                vitals = fight.vitals_of(target)
                before = vitals.total
                angles = aim(fight._eye(ball), (x, y + 1, z + 1.5))
                run.step({ball: (*angles, 0x800)})
                for _ in range(40):
                    run.step({ball: (*angles, 0)})
                self.assertEqual(vitals.total, before - 30)

    def test_melee_player_capsule_range_and_reuse(self):
        for inside in (False, True):
            with self.subTest(inside=inside):
                run, ball, enemy, target, _ = self.scenario()
                fight = run.match.combat
                vitals = fight.vitals_of(target)
                script = ball.body_script
                script.ult_seconds = 0
                before = vitals.total
                x, eye_y, z = fight._eye(ball)
                lower, upper = target.segment()
                vertical_gap = max(lower[1] - eye_y, eye_y - upper[1], 0)
                # Horizontal aim places the authored 1 m search sphere 1.5 m forward.
                boundary = 1.5 + math.sqrt((1 + target.radius) ** 2 - vertical_gap ** 2)
                distance = boundary + (-0.01 if inside else 0.01)
                enemy.mover = mover.Mover((x, target.feet[1], z + distance),
                                          world=run.match.collision_world,
                                          data=mover.mover_data(enemy.hero.body))
                for _ in range(8):
                    run.step({})

                run.step({ball: (0, 0, 0x800)})
                for _ in range(40):
                    run.step({})
                    if vitals.total < before:
                        break
                damage = 30 if inside else 0
                self.assertEqual(vitals.total, before - damage)
                self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), damage)
                self.assertEqual(len(ball.client.messages(combat.HIT)), int(inside))
                if inside:
                    run.step({ball: (0, 0, 0x800)})
                    for _ in range(80):
                        run.step({})
                    self.assertEqual(vitals.total, before - 30)
                    run.step({ball: (0, 0, 0x800)})
                    for _ in range(40):
                        run.step({})
                    self.assertEqual(vitals.total, before - 60)
                    self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 60)
                    self.assertEqual(len(ball.client.messages(combat.HIT)), 2)

    def test_lethal_rifle_before_same_tick_melee_blocks_swing_until_respawn(self):
        run, ball, enemy, target, own = self.scenario(distance=1.5)
        fight = run.match.combat
        source = ball.body
        vitals = fight.vitals_of(target)
        before = vitals.total
        x, y, z = ball.mover.position
        rifle = aim(fight._eye(enemy), (x, y + 1, z))
        for _ in range(650):
            run.step({enemy: (*rifle, 1)})
            if own.total <= 20:
                break
        self.assertGreater(own.total, 0)
        self.assertLessEqual(own.total, 20)
        for _ in range(90):
            run.step({})

        melee = aim(fight._eye(ball), (target.feet[0], target.feet[1] + 1, target.feet[2]))
        # Deliver the lethal shot before Ball's melee command for the same tick.
        run.players.reverse()
        run.step({enemy: (*rifle, 1), ball: (*melee, 0x800)})
        self.assertTrue(own.dead)
        self.assertEqual(own.killer, enemy.body)
        for _ in range(650):
            run.step({})
            if ball.body != source:
                break
        self.assertNotEqual(ball.body, source)
        self.assertFalse(fight.is_dead(ball))
        self.assertNotIn(source, fight.melee_seen)
        x, y, z = ball.mover.position
        enemy.mover = mover.Mover((x, y, z + 1.5), world=run.match.collision_world,
                                  data=mover.mover_data(enemy.hero.body))
        for _ in range(40):
            run.step({})
        self.assertEqual(vitals.total, before)
        self.assertFalse(ball.client.messages(combat.HIT))
        ball.body_script.ult_seconds = 0
        ball.body_script.set_ult_charge(0)
        melee = aim(fight._eye(ball), (x, y + 1, z + 1.5))
        run.step({ball: (*melee, 0x800)})
        for _ in range(40):
            run.step({})
        self.assertEqual(vitals.total, before - 30)
        self.assertEqual(ball.body_script.component.entity_var(ULT_CHARGE).value(), 30)
        self.assertEqual(len(ball.client.messages(combat.HIT)), 1)

    def test_same_tick_lethal_melee_and_rifle_follow_command_order(self):
        for rifle_first in (False, True):
            with self.subTest(rifle_first=rifle_first):
                run, ball, enemy, target, own = self.scenario(distance=1.5)
                fight = run.match.combat
                enemy_health = fight.vitals_of(target)
                enemy_health.damage(enemy_health.total - 20)
                own.damage(own.total - 15)  # Armour absorbs five points of the setup damage.
                self.assertEqual((own.total, enemy_health.total), (20, 20))
                script = ball.body_script
                script.ult_seconds = 0
                script.set_ult_charge(0)
                ball_body, enemy_body = ball.body, enemy.body
                x, y, z = ball.mover.position
                rifle = aim(fight._eye(enemy), (x, y + 1, z))
                melee = aim(fight._eye(ball), (target.feet[0], target.feet[1] + 1, target.feet[2]))
                if rifle_first:
                    run.players.reverse()

                run.step({ball: (*melee, 0x800), enemy: (*rifle, 1)})

                self.assertEqual((own.total, enemy_health.total), (0, 20) if rifle_first else (20, 0))
                self.assertEqual((own.killer, enemy_health.killer),
                                 (enemy_body, None) if rifle_first else (None, ball_body))
                self.assertEqual(len(ball.client.messages(combat.HIT)), int(not rifle_first))
                self.assertEqual(len(enemy.client.messages(combat.HIT)), int(rifle_first))
                self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0 if rifle_first else 20)

    def test_switch_retires_shield_and_activation(self):
        run, ball, _, _, vitals = self.scenario()
        self.activate(run, ball)
        old_body = ball.body
        self.assertEqual(vitals.temporary_shields, 175)
        run.match.switch_hero(ball, heroes()[BALL])
        self.assertNotIn(old_body, run.match.combat.vitals)

    def test_cannon_and_melee_earn_charge_from_actual_damage(self):
        for button, damage in ((1, 5), (0x800, 30)):
            with self.subTest(button=button):
                run, ball, _, target, _ = self.scenario(distance=1.5)
                script = ball.body_script
                script.ult_seconds = 0  # Isolate earned points from the existing passive placeholder.
                script.set_ult_charge(0)
                fight = run.match.combat
                angles = aim(fight._eye(ball), (target.feet[0], target.feet[1] + 1, target.feet[2]))
                run.step({ball: (*angles, button)})
                for _ in range(40):
                    run.step({ball: (*angles, 0)})
                self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), damage)

    def test_misses_and_allies_do_not_earn_charge(self):
        for ally in (False, True):
            run, ball, _, target, _ = self.scenario(ally=ally)
            script = ball.body_script
            script.ult_seconds = 0
            script.set_ult_charge(0)
            angles = aim(run.match.combat._eye(ball),
                         (target.feet[0], target.feet[1] + 1, target.feet[2])) if ally else (-32768, 0)
            for _ in range(20):
                run.step({ball: (*angles, 1)})
            self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)

    def test_temporary_shield_damage_does_not_earn_charge_but_armour_loss_does(self):
        run, ball, enemy, _, _ = self.scenario()
        run.match.switch_hero(enemy, heroes()[BALL])
        target = next(t for t in run.match.combat._targets() if t.owner is enemy)
        vitals = run.match.combat.vitals_of(target)
        vitals.set_temporary_shields(175)
        ball.body_script.ult_seconds = 0
        ball.body_script.set_ult_charge(0)
        run.match.combat._damage(ball, target, 200, False, None, run.frame)
        self.assertEqual(vitals.total, 580)  # shield175 plus armour20, after armour mitigation
        self.assertEqual(ball.body_script.component.entity_var(ULT_CHARGE).value(), 20)

    def test_minefield_damage_and_overkill_do_not_create_unearned_charge(self):
        run, ball, _, target, _ = self.scenario()
        fight = run.match.combat
        script = ball.body_script
        script.ult_seconds = 0
        script.set_ult_charge(0)
        fight._damage(ball, target, 50, False, 0x0B900000000001ED, run.frame)
        self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)
        fight._damage(ball, target, 10000, False, None, run.frame)
        self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 150)

    def test_authored_ultimate_cost_rejects_early_cast_then_consumes_full_charge(self):
        run, ball, _, _, _ = self.scenario()
        script = ball.body_script
        script.ult_seconds = 0
        cost = script.component.entity_var(ULT_COST).value()
        self.assertEqual(cost, 1540)
        script.set_ult_charge(0)
        script.add_ult_charge(cost - 1)
        run.step({ball: (0, 0, 32)})
        self.assertFalse(any(s.flags & nodes.Ability.RUNNING for s in script.ultimates))
        self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), cost - 1)
        run.step({})
        script.add_ult_charge(10000)
        self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), cost)
        run.step({ball: (0, 0, 32)})
        self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)
        self.assertTrue(any(s.flags & nodes.Ability.RUNNING for s in script.ultimates))
        self.assertEqual(script.add_ult_charge(100), 0)
        for _ in range(80):
            run.step({})
        self.assertFalse(any(s.flags & nodes.Ability.RUNNING for s in script.ultimates))
        self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)


if __name__ == '__main__':
    unittest.main()
