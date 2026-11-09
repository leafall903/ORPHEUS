"""Actual Echo command -> volley -> projectile -> health regressions.

Ring arithmetic is resource-backed with reconstructed event seed identity. Five PvE actor classes
are deliberately not substituted with training bots; they require parent actor inputs.
"""
import math
import unittest
from dataclasses import replace
from unittest.mock import patch

from test_game_combat import KANEZAKA_DM, Run, aim
from test_game_mover import box, wall

from ow174.game import combat
from ow174.game.collision import World
from ow174.game.content import PRACTICE_RANGE, heroes
from ow174.game.mover import Mover, mover_data

ECHO = 0x02E0000000000206


class EchoPrimaryTests(unittest.TestCase):
    def session(self, npc=False, distance=5.0):
        run = Run(PRACTICE_RANGE if npc else KANEZAKA_DM,
                  ("Echo",) if npc else ("Echo", "Enemy"))
        shooter = run.players[0]
        run.match.switch_hero(shooter, heroes()[ECHO])
        fight = run.match.combat
        if npc:
            bot = next(bot for bot in run.match.bots if bot.team != shooter.team)
            bot.route = []
            bot.firing = False
            bot.retain_corpse = True
            run.match.bots[:] = [bot]
            victim = bot
        else:
            victim = run.players[1]
            run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        x, y, z = shooter.mover.position
        location = (x, y, z + distance)
        if npc:
            victim.position = location
        else:
            victim.mover.position = list(location)
        for _ in range(30):
            run.step({})
        target = next(t for t in fight._targets() if t.owner is victim)
        yaw, pitch = aim(fight._eye(shooter), (x, y + 1.0, z + distance))
        return run, shooter, victim, target, (yaw, pitch)

    def fire(self, run, shooter, angles, frames=12):
        run.step({shooter: (*angles, 1)})
        for _ in range(frames):
            run.step({shooter: (*angles, 0)})

    def test_single_round_hits_player_three_times_and_consumes_one_ammo(self):
        run, shooter, _, target, angles = self.session()
        fight = run.match.combat
        before = fight.vitals_of(target).total
        self.fire(run, shooter, angles)
        self.assertEqual(fight.vitals_of(target).total, before - 51)
        self.assertEqual(len(shooter.client.messages(combat.HIT)), 3)
        weapon = next(i for i in shooter.body_script.component.instances.values() if i.graph.index == 0x19E5)
        self.assertEqual(weapon.vars[53].value(), 11)

    def test_tri_shot_head_contacts_double_damage_and_set_critical_flags(self):
        for head, override in ((False, None), (True, None), (True, 1), (True, 1.5)):
            with self.subTest(head=head, override=override):
                run, shooter, _, target, _ = self.session(distance=2)
                fight = run.match.combat
                x, y, z = target.feet
                height = target.eye - .05 if head else 1
                angles = aim(fight._eye(shooter), (x, y + height, z))
                weapon = next(i for i in shooter.body_script.component.instances.values()
                              if i.graph.index == 0x19E5)
                config = weapon.graph.states[3].field("m_6396149F")
                self.assertIsNone(config.get("m_A872C70E"))
                if override is None:
                    run.step({shooter: (*angles, 1)})
                else:
                    with patch.dict(config, m_A872C70E={"$": "STUConfigVarFloat", "m_value": override}):
                        run.step({shooter: (*angles, 1)})
                self.assertEqual(len(fight.rockets), 3)
                a, b = target.segment()
                for rocket in fight.rockets:
                    contact = combat.ray_capsule(rocket.position, rocket.direction, a, b, target.radius)
                    self.assertIsNotNone(contact)
                    hit_y = rocket.position[1] + contact * rocket.direction[1]
                    self.assertEqual(hit_y >= y + target.eye - combat.HEAD_BELOW, head)
                    self.assertEqual(rocket.direct.amount, 17)
                for _ in range(12):
                    run.step({})
                multiplier = (2 if override is None else override) if head else 1
                self.assertEqual(fight.vitals_of(target).total, 200 - 51 * multiplier)
                notices = shooter.client.messages(combat.HIT)
                self.assertEqual(len(notices), 3)
                for notice in notices:
                    self.assertEqual(bool(notice["+0x78"]["+0x8"] & combat.CRITICAL), multiplier > 1)

    def test_single_round_hits_training_npc_three_times(self):
        run, shooter, _, target, angles = self.session(npc=True)
        fight = run.match.combat
        self.fire(run, shooter, angles)
        self.assertEqual(fight.vitals_of(target).total, 149)
        self.assertEqual(len(shooter.client.messages(combat.HIT)), 3)

    def test_tri_shot_release_and_early_repress_respect_fire_interval(self):
        for hold in (False, True):
            with self.subTest(hold=hold):
                run, shooter, _, _, _ = self.session(distance=95)
                weapon = next(i for i in shooter.body_script.component.instances.values()
                              if i.graph.index == 0x19E5)
                ammo = weapon.vars[53]
                fired = []
                for frame in range(50):
                    pressed = frame == 0 or frame == 2 or (hold and frame > 2)
                    before = ammo.value()
                    run.step({shooter: (0, 0, int(pressed))})
                    if ammo.value() != before:
                        self.assertEqual(before - ammo.value(), 1)
                        fired.append(frame)
                    if frame == 0:
                        self.assertGreater(weapon.vars[173].value(), 0)
                    if frame == 2:
                        self.assertEqual(ammo.value(), 11)
                self.assertEqual(fired, [0, 20, 41] if hold else [0])

    def test_independent_rays_can_miss_a_distant_capsule(self):
        run, shooter, _, target, angles = self.session(distance=95)
        fight = run.match.combat
        self.fire(run, shooter, angles, frames=85)
        self.assertEqual(fight.vitals_of(target).total, 200)
        self.assertFalse(fight.rockets)

    def test_tri_shot_hits_before_and_retires_at_authored_range(self):
        for beyond in (False, True):
            with self.subTest(beyond=beyond):
                run, shooter, enemy, target, _ = self.session(distance=95)
                fight = run.match.combat
                origin = fight._eye(shooter)
                run.step({shooter: (0, 164, 1)})
                self.assertEqual(len(fight.rockets), 3)
                rocket = max(fight.rockets, key=lambda shot: shot.direction[1])
                weapon = next(i for i in shooter.body_script.component.instances.values()
                              if i.graph.index == 0x19E5)
                launch = weapon.states[3]
                reach = launch.evaluate(launch.node.field('m_FEC435C6'), watch=False)
                self.assertEqual(reach, 100)
                self.assertEqual(rocket.speed, 75)

                # Stage the target on an emitted ray so spread cannot explain the range miss.
                distance = reach + (1.5 if beyond else -1.5)
                x, _, z = (a + b * distance for a, b in zip(origin, rocket.direction, strict=True))
                enemy.mover = Mover((x, target.feet[1], z), world=run.match.collision_world,
                                    data=mover_data(enemy.hero.body))
                shown = next(t for t in fight._targets() if t.owner is enemy)
                a, b = shown.segment()
                contact = combat.ray_capsule(origin, rocket.direction, a, b, shown.radius)
                self.assertIsNotNone(contact)
                self.assertEqual(contact > reach, beyond)
                self.assertGreaterEqual(origin[1] + contact * rocket.direction[1],
                                        shown.feet[1] + shown.eye - combat.HEAD_BELOW)
                for _ in range(95):
                    run.step({})
                self.assertEqual(fight.vitals_of(target).total, 200 if beyond else 166)
                self.assertFalse(fight.rockets)
                if beyond:
                    self.assertAlmostEqual(math.dist(origin, rocket.position), reach, places=5)

    def test_world_occlusion_stops_all_three(self):
        run, shooter, _, target, angles = self.session()
        fight = run.match.combat
        vitals = fight.vitals_of(target)
        clear_world = run.match.collision_world
        x, y, z = shooter.mover.position
        run.match.collision_world = World(
            box(x-10, x+10, y-1, y, z-10, z+10)
            + wall(x-4, z+2, x+4, z+2, y0=y, y1=y+5)
        )
        weapon = next(i for i in shooter.body_script.component.instances.values() if i.graph.index == 0x19E5)
        self.fire(run, shooter, angles, frames=25)
        self.assertEqual(weapon.vars[53].value(), 11)
        self.assertEqual(vitals.total, 200)
        self.assertEqual(shooter.client.messages(combat.HIT), [])
        self.assertFalse(fight.rockets)
        run.match.collision_world = clear_world
        self.fire(run, shooter, angles, frames=25)
        self.assertEqual(weapon.vars[53].value(), 10)
        self.assertEqual(vitals.total, 149)
        self.assertEqual(len(shooter.client.messages(combat.HIT)), 3)

    def test_tri_shot_filters_teammates_and_hits_after_team_change(self):
        for npc in (False, True):
            with self.subTest(npc=npc):
                run, shooter, victim, target, angles = self.session(npc=npc)
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                victim.team = shooter.team
                self.fire(run, shooter, angles, frames=25)
                vitals = run.match.combat.vitals_of(target)
                self.assertEqual(vitals.total, 200)
                self.assertEqual(shooter.client.messages(combat.HIT), [])
                victim.team = 1 - shooter.team
                self.fire(run, shooter, angles, frames=25)
                self.assertEqual(vitals.total, 149)
                self.assertEqual(len(shooter.client.messages(combat.HIT)), 3)

    def test_lethal_round_retains_manual_npc_corpse(self):
        run, shooter, victim, target, angles = self.session(npc=True)
        fight = run.match.combat
        with patch.object(combat.graph, 'entity_body', return_value=None):
            fight._damage(shooter, target, 170, False, None, run.frame)
            self.fire(run, shooter, angles)
            self.assertTrue(victim.dead)
            self.assertEqual(fight.vitals_of(target).total, 0)
            for _ in range(210):
                run.step({})
        self.assertIn(victim, run.match.bots)
        self.assertFalse(any(who is victim for _, who in fight.respawns))

    def test_dead_echo_cannot_start_another_volley(self):
        run, shooter, enemy, target, angles = self.session()
        fight = run.match.combat
        echo_target = next(t for t in fight._targets() if t.owner is shooter)
        fight._damage(enemy, echo_target, 1000, False, None, run.frame)
        self.fire(run, shooter, angles)
        self.assertEqual(fight.vitals_of(target).total, 200)
        self.assertFalse(fight.rockets)

    def test_primary_damages_real_hero_shields_and_armour_pools(self):
        # Zarya: native shield pools. Reinhardt: native armour pools.
        for hero, loss in ((0x02E0000000000068, 51), (0x02E0000000000007, 36)):
            with self.subTest(hero=hex(hero)):
                run, shooter, enemy, _, angles = self.session()
                location = list(enemy.mover.position)
                run.match.switch_hero(enemy, heroes()[hero])
                enemy.mover.position = location
                for _ in range(5):
                    run.step({})
                fight = run.match.combat
                target = next(t for t in fight._targets() if t.owner is enemy)
                before = fight.vitals_of(target).total
                self.fire(run, shooter, angles)
                self.assertAlmostEqual(before - fight.vitals_of(target).total, loss)

    def test_enemy_player_weapon_can_damage_and_kill_echo(self):
        run, shooter, enemy, target, angles = self.session()
        fight = run.match.combat
        x, y, z = shooter.mover.position
        yaw, pitch = aim(fight._eye(enemy), (x, y + 1.0, z))
        echo = next(t for t in fight._targets() if t.owner is shooter)
        for _ in range(160):
            run.step({enemy: (yaw, pitch, 1)})
            if fight.is_dead(shooter):
                break
        self.assertTrue(fight.is_dead(shooter))
        self.assertEqual(fight.vitals_of(echo).total, 0)
        self.assertTrue(shooter.client.messages(combat.DEATH_NOTICE))
        self.assertTrue(enemy.client.messages(combat.KILL_NOTICE))
        weapon = next(i for i in shooter.body_script.component.instances.values() if i.graph.index == 0x19E5)
        ammo = weapon.vars[53].value()
        for _ in range(25):
            run.step({shooter: (*angles, 1)})
        self.assertEqual(weapon.vars[53].value(), ammo)
        self.assertEqual(fight.vitals_of(target).total, 200)
        self.assertFalse(fight.rockets)

    def test_lethal_rifle_stops_new_volley_but_launched_tri_shot_still_hits(self):
        run, shooter, enemy, target, angles = self.session()
        fight = run.match.combat
        x, y, z = shooter.mover.position
        incoming = aim(fight._eye(enemy), (x, y + 1.0, z))
        echo = next(t for t in fight._targets() if t.owner is shooter)
        for _ in range(120):
            run.step({enemy: (*incoming, 1)})
            if fight.vitals_of(echo).total == 20:
                break
        self.assertEqual(fight.vitals_of(echo).total, 20)
        for _ in range(10):
            run.step({})

        weapon = next(i for i in shooter.body_script.component.instances.values()
                      if i.graph.index == 0x19E5)
        ammo = weapon.vars[53]
        initial_ammo = ammo.value()
        run.step({shooter: (*angles, 1), enemy: (*incoming, 1)})
        self.assertTrue(fight.is_dead(shooter))
        self.assertTrue(enemy.client.messages(combat.KILL_NOTICE))
        self.assertEqual(len(fight.rockets), 3)
        self.assertEqual(fight.vitals_of(target).total, 200)
        for _ in range(25):
            run.step({shooter: (*angles, 1)})
        self.assertFalse(fight.rockets)
        self.assertEqual(ammo.value(), initial_ammo - 1)
        self.assertEqual(fight.vitals_of(target).total, 149)
        self.assertEqual(len(shooter.client.messages(combat.HIT)), 3)
        self.assertFalse(fight.is_stunned(enemy))

    def test_vertical_aim_has_three_distinct_normalized_rays(self):
        run, shooter, _, _, angles = self.session()
        run.step({shooter: (*angles, 1)})
        weapon = next(i for i in shooter.body_script.component.instances.values() if i.graph.index == 0x19E5)
        for direction in ((0, 1, 0), (0, -1, 0), (0, 0, 1)):
            state = weapon.states[3]
            rays = combat.projectile_directions(state, direction, 0, (state.counter, state.start))
            self.assertEqual(len(set(rays)), 3)
            for ray in rays:
                self.assertAlmostEqual(sum(v*v for v in ray), 1)

    def test_actual_successive_volleys_rotate_the_same_aim_ring(self):
        run, shooter, _, _, _ = self.session(distance=95)
        volleys = []
        generate = combat.projectile_directions

        def capture(state, forward, shot, activation):
            rays = generate(state, forward, shot, activation)
            volleys.append(rays)
            return rays

        with patch.object(combat, "projectile_directions", side_effect=capture):
            for _ in range(70):
                run.step({shooter: (0, 0, 1)})
                if len(volleys) == 2:
                    break
        self.assertEqual(len(volleys), 2)
        self.assertFalse(set(volleys[0]) & set(volleys[1]))
        for volley in volleys:
            self.assertEqual(len(set(volley)), 3)
            for x, y, z in volley:
                self.assertAlmostEqual(math.degrees(math.atan2(math.hypot(x, y), z)), .84, places=5)


class EchoBeamTests(unittest.TestCase):
    session = EchoPrimaryTests.session
    # Keep setup helper, without rerunning the primary cases in this class.
    def start_beam(self, run, shooter, angles):
        for _ in range(20):
            run.step({shooter: (*angles, 16)})
        instance = next(i for i in shooter.body_script.component.instances.values()
                        if i.graph.index == 0x1FBD)
        self.assertTrue(instance.states[17].active)
        return instance

    def test_beam_applies_authored_base_rate_then_low_health_multiplier(self):
        run, shooter, enemy, target, angles = self.session()
        fight = run.match.combat
        instance = self.start_beam(run, shooter, angles)
        rate = instance.vars[11984].value()
        multiplier = instance.vars[22665].value()
        self.assertEqual((rate, multiplier), (50, 4))
        modify = instance.states[17].node.field("m_modifyHealth")
        self.assertIsNone(modify.get("m_277A4ECA"))
        per_tick = rate * combat.TICK
        vitals = fight.vitals_of(target)
        before = vitals.total
        run.step({shooter: (*angles, 16)})
        self.assertAlmostEqual(before - vitals.total, per_tick)
        vitals.now[combat.HEALTH_POOL] = 100.0
        run.step({shooter: (*angles, 16)})
        self.assertAlmostEqual(vitals.total, 100 - per_tick)  # Exactly 50% uses the base rate.
        before = vitals.total
        run.step({shooter: (*angles, 16)})
        self.assertAlmostEqual(before - vitals.total, per_tick * multiplier)
        self.assertFalse(fight.is_stunned(enemy))

    def test_actual_beam_crosses_half_health_during_the_same_channel(self):
        run, shooter, _, target, angles = self.session(distance=2)
        vitals = run.match.combat.vitals_of(target)
        run.step({shooter: (*angles, 0x800)})
        for _ in range(70):
            run.step({shooter: (*angles, 0)})
        self.assertEqual(vitals.total, 170)
        run.step({shooter: (*angles, 1)})
        for _ in range(30):
            run.step({shooter: (*angles, 0)})
        self.assertEqual(vitals.total, 119)

        beam = self.start_beam(run, shooter, angles).states[17]
        activation = beam.counter
        self.assertGreater(vitals.total, 100)
        for _ in range(30):
            if vitals.total < 100:
                break
            before = vitals.total
            run.step({shooter: (*angles, 0)})
            self.assertAlmostEqual(before - vitals.total, .8)
        self.assertLess(vitals.total, 100)
        before = vitals.total
        run.step({shooter: (*angles, 0)})
        self.assertAlmostEqual(before - vitals.total, 3.2)
        self.assertTrue(beam.active)
        self.assertEqual(beam.counter, activation)

    def test_real_biotic_field_heals_above_beam_threshold_during_channel(self):
        run, shooter, enemy, target, angles = self.session()
        fight = run.match.combat
        vitals = fight.vitals_of(target)
        for _ in range(2):
            EchoPrimaryTests.fire(self, run, shooter, angles, frames=25)
        self.assertEqual(vitals.total, 98)

        # Rotate a half-turn and wrap into the signed command yaw range.
        miss = (angles[0] % 65536 - 32768, angles[1])
        beam = self.start_beam(run, shooter, miss).states[17]
        activation = beam.counter
        self.assertEqual(vitals.total, 98)
        run.step({shooter: (*angles, 0), enemy: (0, 16000, 16)})
        self.assertAlmostEqual(vitals.total, 94.8)
        self.assertEqual(shooter.client.messages(combat.HIT)[-1]["+0x78"]["+0x4"], 3)
        for _ in range(70):
            run.step({shooter: (*miss, 0)})
            self.assertTrue(beam.active)
            if vitals.total > 101:
                break
        self.assertGreater(vitals.total, 101)
        notices = len(shooter.client.messages(combat.HIT))
        run.step({shooter: (*angles, 0)})
        hits = shooter.client.messages(combat.HIT)
        self.assertEqual(len(hits), notices + 1)
        self.assertEqual(hits[-1]["+0x78"]["+0x4"], 1)
        self.assertTrue(beam.active)
        self.assertEqual(beam.counter, activation)

    def test_actual_beam_reduces_armor_damage_and_passes_remainder_to_health(self):
        run, shooter, enemy, _, angles = self.session()
        run.match.switch_hero(enemy, heroes()[0x02E0000000000007], preserve_pose=True)
        fight = run.match.combat
        for _ in range(10):
            run.step({})
        target = next(t for t in fight._targets() if t.owner is enemy)
        vitals = fight.vitals_of(target)
        self.start_beam(run, shooter, angles)
        before = vitals.now[combat.ARMOUR_POOL]
        run.step({shooter: (*angles, 16)})
        self.assertAlmostEqual(before - vitals.now[combat.ARMOUR_POOL], .56)
        vitals.now[combat.ARMOUR_POOL] = .28  # Isolate the pool transition on the next real hit.
        before = vitals.now[combat.HEALTH_POOL]
        run.step({shooter: (*angles, 16)})
        self.assertEqual(vitals.now[combat.ARMOUR_POOL], 0)
        self.assertAlmostEqual(before - vitals.now[combat.HEALTH_POOL], .4)

    def test_beam_damages_npc_and_stops_at_authored_range(self):
        for distance, expected in ((5, True), (20, False)):
            with self.subTest(distance=distance):
                run, shooter, _, target, angles = self.session(npc=True, distance=distance)
                self.start_beam(run, shooter, angles)
                self.assertEqual(run.match.combat.vitals_of(target).total < 200, expected)

    def test_beam_hits_only_when_expanded_capsule_contact_is_within_authored_range(self):
        for beyond in (False, True):
            with self.subTest(beyond=beyond):
                run, shooter, enemy, target, _ = self.session(distance=16)
                fight = run.match.combat
                instance = next(i for i in shooter.body_script.component.instances.values()
                                if i.graph.index == 0x1FBD)
                reach = instance.vars[1648].value()
                self.assertEqual(reach, 16)
                x, y, z = shooter.mover.position
                distance = reach + target.radius + .2 + (.125 if beyond else -.125)
                enemy.mover = Mover((x, y, z + distance), world=run.match.collision_world,
                                    data=mover_data(enemy.hero.body))
                for _ in range(5):
                    run.step({})
                target = next(t for t in fight._targets() if t.owner is enemy)
                a, b = target.segment()
                centre = tuple((lo + hi) / 2 for lo, hi in zip(a, b, strict=True))
                origin = fight._eye(shooter)
                angles = aim(origin, centre)
                trace = self.start_beam(run, shooter, angles).states[10]
                radius = trace.evaluate(trace.node.field("m_57DAE1B5"), watch=False)
                self.assertAlmostEqual(radius, .2)
                contact = combat.ray_capsule(origin, combat.aim(*angles), a, b, target.radius + radius)
                self.assertIsNotNone(contact)
                self.assertEqual(contact > reach, beyond)
                self.assertAlmostEqual(contact, reach + (.125 if beyond else -.125), delta=.03)
                self.assertEqual(fight.vitals_of(target).total < 200, not beyond)

    def test_actual_beam_grazes_within_authored_trace_radius_only(self):
        run, shooter, _, target, _ = self.session()
        fight = run.match.combat
        origin = fight._eye(shooter)
        x, y, z = target.feet
        graze = aim(origin, (x + target.radius + .1, y+1, z))
        miss = aim(origin, (x + target.radius + .3, y+1, z))
        a, b = target.segment()
        self.assertIsNone(combat.ray_capsule(origin, combat.aim(*graze), a, b, target.radius))
        vitals = fight.vitals_of(target)
        before = vitals.total
        self.start_beam(run, shooter, graze)
        self.assertLess(vitals.total, before)
        before = vitals.total
        for _ in range(5):
            run.step({shooter: (*miss, 0)})
        self.assertEqual(vitals.total, before)

    def test_beam_does_not_hit_allies_or_shoot_through_walls(self):
        for blocked in (True, False):
            with self.subTest(blocked=blocked):
                run, shooter, victim, target, angles = self.session(npc=True)
                fight = run.match.combat
                vitals = fight.vitals_of(target)
                clear_world = run.match.collision_world
                if blocked:
                    x, y, z = shooter.mover.position
                    run.match.collision_world = World(
                        box(x-10, x+10, y-1, y, z-10, z+10)
                        + wall(x-4, z+2, x+4, z+2, y0=y, y1=y+5)
                    )
                else:
                    victim.team = shooter.team
                self.start_beam(run, shooter, angles)
                self.assertEqual(vitals.total, 200)
                run.match.collision_world = clear_world
                victim.team = 1 - shooter.team
                run.step({shooter: (*angles, 0)})
                self.assertAlmostEqual(vitals.total, 199.2)

    def test_beam_does_not_multiply_damage_on_repeated_command(self):
        from ow174.game.commands import Command
        run, shooter, _, target, angles = self.session()
        fight = run.match.combat
        self.start_beam(run, shooter, angles)
        before = fight.vitals_of(target).total
        command = Command(run.frame, yaw=angles[0], pitch=angles[1], buttons=16)
        for _ in range(5):
            fight.command(shooter, command)
        self.assertEqual(fight.vitals_of(target).total, before)

    def test_beam_tap_release_early_press_cancel_and_reuse(self):
        run, shooter, _, target, angles = self.session()
        fight = run.match.combat
        vitals = fight.vitals_of(target)
        instances = shooter.body_script.component.instances.values()
        ammo = next(i for i in instances if i.graph.index == 0x19E5).vars[53]
        initial_ammo = ammo.value()
        run.step({shooter: (*angles, 16)})
        run.step({shooter: (*angles, 0)})
        run.step({shooter: (*angles, 16)})  # Before the authored cancel listener opens.
        for _ in range(25):
            run.step({shooter: (*angles, 0)})
        instance = next(i for i in instances if i.graph.index == 0x1FBD)
        self.assertTrue(instance.states[26].active)
        self.assertTrue(instance.states[17].active)
        before = vitals.total
        run.step({shooter: (*angles, 0)})
        self.assertAlmostEqual(before - vitals.total, .8)
        self.assertEqual(ammo.value(), initial_ammo)
        self.assertFalse(fight.rockets)

        before = vitals.total
        run.step({shooter: (*angles, 16)})
        self.assertFalse(instance.states[17].active)
        self.assertEqual(vitals.total, before)
        self.assertEqual(ammo.value(), initial_ammo)
        run.step({shooter: (*angles, 0)})
        run.step({shooter: (*angles, 16)})
        for _ in range(25):
            run.step({shooter: (*angles, 0)})
        self.assertFalse(instance.states[17].active)
        self.assertEqual(vitals.total, before)

        ability = instance.states[0]
        for _ in range(650):
            if ability.remaining() == 0:
                break
            run.step({shooter: (*angles, 0)})
        self.assertEqual(ability.remaining(), 0)
        run.step({shooter: (*angles, 16)})
        for _ in range(25):
            run.step({shooter: (*angles, 0)})
        self.assertTrue(instance.states[17].active)
        self.assertLess(vitals.total, before)
        self.assertEqual(ammo.value(), initial_ammo)

    def test_beam_stops_when_authored_damage_state_ends(self):
        run, shooter, _, target, angles = self.session()
        fight = run.match.combat
        run.step({shooter: (*angles, 16)})
        instance = next(i for i in shooter.body_script.component.instances.values()
                        if i.graph.index == 0x1FBD)
        for _ in range(20):
            beam = instance.states.get(17)
            if beam is not None and beam.active:
                break
            run.step({shooter: (*angles, 16)})
        self.assertIsNotNone(beam)
        self.assertTrue(beam.active)
        started = run.frame
        duration = instance.vars[567].value()
        cooldown_seconds = instance.vars[472].value()
        self.assertEqual(duration, 2)
        self.assertEqual(cooldown_seconds, 8)
        cooldown = instance.vars[61]
        for _ in range(150):
            self.assertEqual(cooldown.value(), 0)
            run.step({shooter: (-32768, 0, 16)})
            if not beam.active:
                break
        self.assertFalse(beam.active)
        self.assertAlmostEqual((run.frame - started) * combat.TICK, duration, delta=combat.TICK)
        before = fight.vitals_of(target).total
        for _ in range(25):
            run.step({shooter: (*angles, 16)})
            self.assertFalse(beam.active)
            if cooldown.value() > 0:
                break
        self.assertEqual(fight.vitals_of(target).total, before)
        self.assertAlmostEqual(cooldown.value(), cooldown_seconds, delta=combat.TICK)

    def test_enemy_rifle_death_interrupts_active_beam_damage(self):
        run, shooter, enemy, target, angles = self.session()
        fight = run.match.combat
        x, y, z = shooter.mover.position
        incoming = aim(fight._eye(enemy), (x, y + 1.0, z))
        echo = next(t for t in fight._targets() if t.owner is shooter)
        for _ in range(120):
            run.step({enemy: (*incoming, 1)})
            if fight.vitals_of(echo).total == 20:
                break
        self.assertEqual(fight.vitals_of(echo).total, 20)

        self.start_beam(run, shooter, angles)
        self.assertLess(fight.vitals_of(target).total, 200)
        for _ in range(25):
            run.step({shooter: (*angles, 16), enemy: (*incoming, 1)})
            if fight.is_dead(shooter):
                break
        self.assertTrue(fight.is_dead(shooter))
        self.assertTrue(enemy.client.messages(combat.KILL_NOTICE))
        after_death = fight.vitals_of(target).total
        for _ in range(15):
            run.step({shooter: (*angles, 16)})
        self.assertEqual(fight.vitals_of(target).total, after_death)


class EchoCooldownTests(unittest.TestCase):
    session = EchoPrimaryTests.session

    def test_real_ability_graphs_reject_during_cooldown_and_restart_after(self):
        for graph_index, button, ability_index in ((0x1BB6, 8, 0), (0x1C36, 2, 5), (0x1FBD, 16, 0)):
            with self.subTest(graph=hex(graph_index)):
                run, shooter, _, _, _ = self.session()
                instance = next(i for i in shooter.body_script.component.instances.values()
                                if i.graph.index == graph_index)
                # Aim away so this tests the cooldown even without a successful hit.
                run.step({shooter: (-32768, 0, button)})
                self.assertTrue(instance.states[ability_index].flags & 1)
                for _ in range(260):
                    run.step({})
                    if instance.vars[61].value() > 0:
                        break
                self.assertGreater(instance.vars[61].value(), 0)
                self.assertFalse(instance.states[ability_index].flags & 1)
                before = instance.vars[61].value()
                for _ in range(3):
                    run.step({shooter: (-32768, 0, button)})
                    self.assertFalse(instance.states[ability_index].flags & 1)
                    run.step({})
                self.assertLess(instance.vars[61].value(), before)
                for _ in range(700):
                    run.step({})
                    if instance.vars[61].value() == 0:
                        break
                self.assertEqual(instance.vars[61].value(), 0)
                run.step({shooter: (-32768, 0, button)})
                self.assertTrue(instance.states[ability_index].flags & 1)

    def test_beam_cooldown_rejects_health_damage_then_recovers(self):
        run, shooter, _, target, angles = self.session()
        fight = run.match.combat
        instance = next(i for i in shooter.body_script.component.instances.values()
                        if i.graph.index == 0x1FBD)
        run.step({shooter: (-32768, 0, 16)})
        for _ in range(260):
            run.step({})
            if instance.vars[61].value() > 0:
                break
        before = fight.vitals_of(target).total
        beam = instance.states[17]
        activation = beam.counter
        cooldown = instance.vars[61]
        for frame in range(40):
            run.step({shooter: (*angles, 16 if frame % 2 else 0)})
        self.assertEqual(fight.vitals_of(target).total, before)
        for _ in range(700):
            if cooldown.value() <= 2 * combat.TICK:
                break
            run.step({})
        self.assertGreater(cooldown.value(), combat.TICK)
        self.assertLessEqual(cooldown.value(), 2 * combat.TICK)
        run.step({shooter: (*angles, 16)})
        self.assertGreater(cooldown.value(), 0)
        self.assertEqual(beam.counter, activation)
        self.assertEqual(fight.vitals_of(target).total, before)
        for _ in range(3):
            run.step({shooter: (*angles, 0)})
            if cooldown.value() == 0:
                break
        self.assertEqual(cooldown.value(), 0)
        for _ in range(20):
            run.step({shooter: (*angles, 16)})
        self.assertEqual(beam.counter, activation + 1)
        self.assertLess(fight.vitals_of(target).total, before)

    def test_primary_clip_blocks_further_damage_until_authored_reload(self):
        run, shooter, _, _target, angles = self.session(distance=95)
        weapon = next(i for i in shooter.body_script.component.instances.values()
                      if i.graph.index == 0x19E5)
        reports = []
        original = run.match.combat._shoot
        def observe(*args, **kwargs):
            reports.append((args[1].frame, args[3]))
            return original(*args, **kwargs)
        with patch.object(run.match.combat, '_shoot', side_effect=observe):
            for _ in range(245):
                run.step({shooter: (*angles, 1)})
        self.assertEqual(len(reports), 12)
        self.assertEqual(weapon.vars[53].value(), 0)
        # Reload is 0x400. A release separates the fire edges.
        run.step({})
        for _ in range(110):
            run.step({shooter: (*angles, 0x400)})
        self.assertEqual(weapon.vars[53].value(), 12)


class EchoMeleeTests(unittest.TestCase):
    session = EchoPrimaryTests.session

    def test_melee_samples_targets_once_and_has_no_delayed_hit_after_release(self):
        run, shooter, enemy, target, angles = self.session(distance=2)
        fight = run.match.combat
        vitals = fight.vitals_of(target)
        melee = next(i for i in shooter.body_script.component.instances.values()
                     if i.graph.index == 0x43)
        recovery = melee.states[6]
        x, y, z = shooter.mover.position
        miss = aim(fight._eye(shooter), (x + 2, y + 1, z))
        run.step({shooter: (*miss, 0x800)})
        strike = melee.states[4]
        self.assertEqual(strike.counter, 1)
        self.assertEqual(vitals.total, 200)
        self.assertGreater(recovery.remaining(), 0)

        # Aim at the enemy immediately after the missed activation.
        for _ in range(100):
            run.step({shooter: (*angles, 0)})
            if recovery.remaining() == 0:
                break
        self.assertEqual(recovery.remaining(), 0)
        self.assertFalse(strike.active)
        self.assertEqual(strike.counter, 1)
        self.assertEqual(vitals.total, 200)
        self.assertEqual(shooter.client.messages(combat.HIT), [])

        run.step({shooter: (*angles, 0x800)})
        self.assertEqual(strike.counter, 2)
        self.assertEqual(vitals.total, 170)
        self.assertFalse(fight.is_stunned(enemy))
        self.assertFalse(fight.is_dead(enemy))
        for _ in range(25):
            run.step({shooter: (*angles, 0)})
        self.assertEqual(vitals.total, 170)
        self.assertEqual(len(shooter.client.messages(combat.HIT)), 1)

    def test_actual_melee_hits_player_and_training_npc_for_thirty(self):
        for npc in (False, True):
            with self.subTest(npc=npc):
                run, shooter, _, target, angles = self.session(npc=npc, distance=2)
                run.step({shooter: (*angles, 0x800)})
                for _ in range(25):
                    run.step({shooter: (*angles, 0)})
                self.assertEqual(run.match.combat.vitals_of(target).total, 170)

    def test_melee_reaches_only_inside_the_authored_sphere(self):
        for outside in (False, True):
            with self.subTest(outside=outside):
                run, shooter, enemy, _, _ = self.session(distance=2)
                fight = run.match.combat
                melee = next(i for i in shooter.body_script.component.instances.values()
                             if i.graph.index == 0x43)
                track = melee.graph.states[5]
                reach = track.field("m_05BE62DD")["m_expression"]["m_D99EF254"][1]
                radius, = track.field("m_radius")["m_expression"]["m_D99EF254"]
                self.assertEqual((reach, radius), (1.5, 1))
                target = next(t for t in fight._targets() if t.owner is enemy)
                distance = reach + radius + target.radius + (.125 if outside else -.125)
                x, y, z = shooter.mover.position
                enemy.mover = Mover((x, y, z + distance), world=run.match.collision_world,
                                    data=mover_data(enemy.hero.body))
                for _ in range(5):
                    run.step({})
                target = next(t for t in fight._targets() if t.owner is enemy)
                origin = fight._eye(shooter)
                a, b = target.segment()
                angles = aim(origin, (a[0], (a[1] + b[1]) / 2, a[2]))
                centre = tuple(p + reach * d for p, d in zip(origin, combat.aim(*angles), strict=True))
                gap = combat.segment_distance(centre, a, b) - target.radius
                self.assertEqual(gap > radius, outside)
                self.assertAlmostEqual(gap, radius + (.125 if outside else -.125), delta=.05)
                run.step({shooter: (*angles, 0x800)})
                strike = melee.states[4]
                modify = strike.node.field("m_modifyHealth")
                self.assertEqual(strike.evaluate(modify["m_amount"], watch=False), 30)
                self.assertIsNone(modify.get("m_277A4ECA"))
                self.assertEqual(fight.vitals_of(target).total, 200 if outside else 170)
                self.assertFalse(fight.is_stunned(enemy))

    def test_melee_misses_outside_reach_and_behind_static_wall(self):
        for distance, blocked in ((4, False), (2, True)):
            with self.subTest(distance=distance, blocked=blocked):
                run, shooter, _, target, angles = self.session(distance=distance)
                if blocked:
                    x, y, z = shooter.mover.position
                    run.match.collision_world = World(
                        box(x-10, x+10, y-1, y, z-10, z+10)
                        + wall(x-4, z+1, x+4, z+1, y0=y, y1=y+5)
                    )
                run.step({shooter: (*angles, 0x800)})
                for _ in range(25):
                    run.step({shooter: (*angles, 0)})
                self.assertEqual(run.match.combat.vitals_of(target).total, 200)

    def test_same_frame_melee_and_lethal_rifle_follow_command_order(self):
        for rifle_first in (False, True):
            with self.subTest(rifle_first=rifle_first):
                run, shooter, enemy, target, angles = self.session(distance=2)
                fight = run.match.combat
                x, y, z = shooter.mover.position
                incoming = aim(fight._eye(enemy), (x, y + 1, z))
                echo = next(t for t in fight._targets() if t.owner is shooter)
                for _ in range(120):
                    run.step({enemy: (*incoming, 1)})
                    if fight.vitals_of(echo).total == 20:
                        break
                self.assertEqual(fight.vitals_of(echo).total, 20)
                for _ in range(10):
                    run.step({})
                if rifle_first:
                    run.players.reverse()
                run.step({shooter: (*angles, 0x800), enemy: (*incoming, 1)})
                self.assertTrue(fight.is_dead(shooter))
                self.assertTrue(enemy.client.messages(combat.KILL_NOTICE))
                expected = 200 if rifle_first else 170
                self.assertEqual(fight.vitals_of(target).total, expected)
                for _ in range(25):
                    run.step({shooter: (*angles, 0x800)})
                self.assertEqual(fight.vitals_of(target).total, expected)

    def test_melee_rejects_ally_and_cooldown_then_can_hit_again(self):
        run, shooter, ally, target, angles = self.session(npc=True, distance=2)
        instances = shooter.body_script.component.instances.values()
        melee = next(i for i in instances if i.graph.index == 0x43).states[6]
        ammo = next(i for i in instances if i.graph.index == 0x19E5).vars[53]
        initial_ammo = ammo.value()
        vitals = run.match.combat.vitals_of(target)
        ally.team = shooter.team
        run.step({shooter: (*angles, 0x800)})
        self.assertGreater(melee.remaining(), 0)
        for _ in range(25):
            run.step({shooter: (*angles, 0)})
        self.assertEqual(vitals.total, 200)
        ally.team = 1 - shooter.team
        previous = melee.remaining()
        self.assertGreater(previous, 0)
        run.step({shooter: (*angles, 0x800)})
        self.assertLess(melee.remaining(), previous)
        for _ in range(25):
            run.step({shooter: (*angles, 0)})
        self.assertEqual(vitals.total, 200)
        for _ in range(70):
            if melee.remaining() == 0:
                break
            run.step({})
        self.assertEqual(melee.remaining(), 0)
        self.assertEqual(vitals.total, 200)
        run.step({shooter: (*angles, 0x800)})
        self.assertEqual(vitals.total, 170)
        for _ in range(25):
            run.step({shooter: (*angles, 0)})
        self.assertEqual(vitals.total, 170)
        self.assertEqual(ammo.value(), initial_ammo)


class EchoStickyTests(unittest.TestCase):
    session = EchoPrimaryTests.session

    def launch(self, run, shooter, angles, frames=40):
        run.step({shooter: (*angles, 2)})
        for _ in range(frames):
            run.step({shooter: (*angles, 0)})

    def test_six_contacts_then_delayed_blast_against_player_and_training_npc(self):
        for npc in (False, True):
            with self.subTest(npc=npc):
                run, shooter, victim, target, angles = self.session(npc=npc)
                fight = run.match.combat
                self.launch(run, shooter, angles)
                self.assertEqual(len(fight.rockets), 6)
                self.assertTrue(all(r.attached_entity == target.entity for r in fight.rockets))
                for rocket in fight.rockets:
                    self.assertEqual(rocket.direct.amount, 5)
                    self.assertEqual(rocket.blast.amount, 25)
                    self.assertIsNone(rocket.direct.effect)
                    self.assertIsNone(rocket.blast.effect)
                self.assertEqual(fight.vitals_of(target).total, 170)
                self.assertFalse(fight.is_stunned(victim))
                for _ in range(90):
                    run.step({})
                self.assertEqual(fight.vitals_of(target).total, 20)  # plus 6*25
                self.assertFalse(fight.rockets)
                self.assertFalse(fight.is_stunned(victim))

    def test_sticky_follows_current_body_and_detonates_at_its_new_position(self):
        run, shooter, victim, target, angles = self.session()
        fight = run.match.combat
        self.launch(run, shooter, angles)
        from ow174.game.commands import Command
        original_x = victim.mover.position[0]
        for _ in range(90):
            run.frame += 1
            run.match.update(run.frame * combat.TICK, run.frame)
            shooter.take_commands([Command(run.frame)])
            victim.take_commands([Command(run.frame, right=127)])
            self.assertTrue(all(abs(r.position[0] - victim.mover.position[0]) < 1 for r in fight.rockets))
        self.assertGreater(abs(victim.mover.position[0] - original_x), 5)
        self.assertEqual(fight.vitals_of(target).total, 20)

    def test_sticky_cooldown_rejects_launch_and_allows_fresh_lethal_volley(self):
        run, shooter, _, target, angles = self.session()
        fight = run.match.combat
        vitals = fight.vitals_of(target)
        instances = shooter.body_script.component.instances.values()
        instance = next(i for i in instances if i.graph.index == 0x1C36)
        cooldown = instance.vars[61]
        ammo = next(i for i in instances if i.graph.index == 0x19E5).vars[53]
        initial_ammo = ammo.value()
        cooldown_seconds = instance.vars[472].value()
        self.assertEqual(cooldown_seconds, 6)
        run.step({shooter: (*angles, 2)})
        self.assertEqual(cooldown.value(), 0)
        observed_start = False
        for _ in range(130):
            run.step({shooter: (*angles, 0)})
            if not observed_start and cooldown.value() > 0:
                self.assertAlmostEqual(cooldown.value(), cooldown_seconds, delta=combat.TICK)
                observed_start = True
        self.assertTrue(observed_start)
        self.assertEqual(vitals.total, 20)
        self.assertFalse(fight.rockets)
        self.assertGreater(cooldown.value(), 0)

        self.launch(run, shooter, angles)
        self.assertFalse(fight.rockets)
        self.assertEqual(vitals.total, 20)
        self.assertGreater(cooldown.value(), 0)
        for _ in range(700):
            if cooldown.value() <= 2 * combat.TICK:
                break
            run.step({})
        before = cooldown.value()
        self.assertGreater(before, combat.TICK)
        self.assertLessEqual(before, 2 * combat.TICK)
        run.step({shooter: (*angles, 2)})
        self.assertGreater(cooldown.value(), 0)
        self.assertLess(cooldown.value(), before)
        self.assertFalse(fight.rockets)
        self.assertEqual(vitals.total, 20)
        for _ in range(3):
            run.step({shooter: (*angles, 0)})
            if cooldown.value() == 0:
                break
        self.assertEqual(cooldown.value(), 0)

        self.launch(run, shooter, angles)
        self.assertTrue(vitals.dead)
        self.assertEqual(vitals.total, 0)
        self.assertEqual(ammo.value(), initial_ammo)

    def test_sticky_death_stops_volley_but_armed_bombs_keep_their_fuse(self):
        run, shooter, enemy, target, angles = self.session()
        fight = run.match.combat
        x, y, z = shooter.mover.position
        incoming = aim(fight._eye(enemy), (x, y + 1.0, z))
        echo = next(t for t in fight._targets() if t.owner is shooter)
        for _ in range(120):
            run.step({enemy: (*incoming, 1)})
            if fight.vitals_of(echo).total == 20:
                break
        self.assertEqual(fight.vitals_of(echo).total, 20)

        run.step({shooter: (*angles, 2)})
        for _ in range(20):
            run.step({shooter: (*angles, 0)})
            if any(rocket.attached_entity == target.entity for rocket in fight.rockets):
                break
        self.assertTrue(any(rocket.attached_entity == target.entity for rocket in fight.rockets))
        for _ in range(10):
            run.step({enemy: (*incoming, 1)})
            if fight.is_dead(shooter):
                break
        self.assertTrue(fight.is_dead(shooter))
        launched = len(fight.rockets)
        self.assertGreater(launched, 0)
        self.assertLess(launched, 6)
        for _ in range(110):
            run.step({shooter: (*angles, 2)})
        self.assertFalse(fight.rockets)
        self.assertEqual(fight.vitals_of(target).total, 200 - launched * 30)

    def test_sticky_miss_and_friendly_filter(self):
        for friendly in (False, True):
            run, shooter, victim, target, angles = self.session(npc=True)
            if friendly:
                victim.team = shooter.team
            else:
                angles = (-32768, 0)
            self.launch(run, shooter, angles)
            for _ in range(120):
                run.step({})
            self.assertEqual(run.match.combat.vitals_of(target).total, 200)

    def test_unattached_sticky_bombs_retire_at_authored_flight_lifetime(self):
        run, shooter, _, target, _ = self.session()
        fight = run.match.combat
        x, y, z = shooter.mover.position
        run.match.collision_world = World(box(x-2, x+2, y-1, y, z-2, z+2))
        self.launch(run, shooter, (-32768, 0))
        rockets = tuple(fight.rockets)
        self.assertEqual(len(rockets), 6)
        launch = next(i for i in shooter.body_script.component.instances.values()
                      if i.graph.index == 0x1C36).states[0]
        lifetime = launch.evaluate(launch.node.field('m_projectileLifetime'), watch=False)
        self.assertEqual(lifetime, 100)
        self.assertTrue(all(r.fuse_left is None for r in rockets))

        ticks_before_expiry = math.floor(min(r.left for r in rockets) / combat.TICK) - 1
        run.frame += ticks_before_expiry
        run.match.update(run.frame * combat.TICK, run.frame)
        self.assertEqual(len(fight.rockets), 6)
        self.assertTrue(all(0 < r.left < 1 for r in rockets))
        self.assertTrue(all(r.fuse_left is None for r in rockets))
        run.frame += math.ceil(max(r.left for r in rockets) / combat.TICK) + 1
        run.match.update(run.frame * combat.TICK, run.frame)
        self.assertFalse(fight.rockets)
        for rocket in rockets:
            self.assertAlmostEqual(rocket.flown, lifetime)
        self.assertEqual(fight.vitals_of(target).total, 200)
        self.assertEqual(shooter.client.messages(combat.HIT), [])

    def test_surface_attachment_has_no_contact_damage_to_enemy_behind_wall(self):
        run, shooter, _, target, angles = self.session(distance=1.5)
        fight = run.match.combat
        x, y, z = shooter.mover.position
        run.match.collision_world = World(
            box(x-10, x+10, y-1, y, z-10, z+10)
            + wall(x-4, z+.65, x+4, z+.65, y0=y, y1=y+5)
        )
        self.launch(run, shooter, angles)
        self.assertEqual(len(fight.rockets), 6)
        self.assertTrue(all(r.attached_entity is None and r.fuse_left is not None
                            for r in fight.rockets))
        self.assertEqual(fight.vitals_of(target).total, 200)
        for _ in range(90):
            run.step({})
        self.assertEqual(fight.vitals_of(target).total, 200)
        self.assertFalse(fight.rockets)
        self.assertEqual(shooter.client.messages(combat.HIT), [])

    def test_surface_sticky_blast_filters_allied_player(self):
        for friendly in (True, False):
            with self.subTest(friendly=friendly):
                run, shooter, victim, _, _ = self.session()
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                victim.team = shooter.team if friendly else 1 - shooter.team
                x, y, z = shooter.mover.position
                geometry = World(box(x-10, x+10, y-1, y, z-10, z+10)
                                 + wall(x-4, z+2, x+4, z+2, y0=y, y1=y+5))
                run.match.collision_world = geometry
                victim.mover = Mover((x+1, y, z+1.5), world=geometry,
                                     data=mover_data(victim.hero.body))
                self.launch(run, shooter, (0, 0))
                fight = run.match.combat
                target = next(t for t in fight._targets() if t.owner is victim)
                vitals = fight.vitals_of(target)
                self.assertEqual(vitals.total, 200)
                self.assertEqual(len(fight.rockets), 6)
                self.assertTrue(all(r.attached_entity is None and r.fuse_left is not None
                                    for r in fight.rockets))
                for _ in range(90):
                    run.step({})
                self.assertFalse(fight.rockets)
                self.assertEqual(vitals.total < 200, not friendly)
                self.assertEqual(bool(shooter.client.messages(combat.HIT)), not friendly)

    def test_surface_sticky_blast_respects_authored_two_metre_radius(self):
        for outside in (False, True):
            with self.subTest(outside=outside):
                run, shooter, victim, target, _ = self.session()
                fight = run.match.combat
                x, y, z = shooter.mover.position
                geometry = World(box(x-10, x+10, y-1, y, z-10, z+10)
                                 + wall(x-4, z+2, x+4, z+2, y0=y, y1=y+5))
                run.match.collision_world = geometry
                self.launch(run, shooter, (0, 0))
                rockets = tuple(fight.rockets)
                self.assertEqual(len(rockets), 6)
                self.assertTrue(all(r.attached_entity is None and r.fuse_left is not None
                                    for r in rockets))
                launch = next(i for i in shooter.body_script.component.instances.values()
                              if i.graph.index == 0x1C36).states[0]
                self.assertEqual(launch.evaluate(launch.node.field("m_FEC435C6"), watch=False), 0)
                self.assertTrue(all(r.blast.rings == ((2, 1),) for r in rockets))
                offset = 2 + target.radius + (.25 if outside else -.25)
                victim.mover = Mover((x + offset, y, z + 1.8), world=geometry,
                                     data=mover_data(victim.hero.body))
                run.step({})
                target = next(t for t in fight._targets() if t.owner is victim)
                a, b = target.segment()
                for rocket in rockets:
                    gap = combat.segment_distance(rocket.position, a, b) - target.radius
                    self.assertEqual(gap > 2, outside)
                    self.assertAlmostEqual(gap, 2, delta=.4)
                for _ in range(90):
                    run.step({})
                self.assertFalse(fight.rockets)
                self.assertEqual(fight.vitals_of(target).total, 200 if outside else 50)

    def test_sticky_flight_drops_below_a_linear_trajectory(self):
        run, shooter, _, _, _ = self.session(distance=95)
        fight = run.match.combat
        origin = fight._eye(shooter)
        self.launch(run, shooter, (0, 0), frames=15)
        rocket = fight.rockets[0]
        self.assertIsNone(rocket.fuse_left)
        self.assertLess(rocket.position[1], origin[1])
        self.assertAlmostEqual(rocket.position[1], origin[1] - .5 * 9.8 * rocket.flown**2, places=5)

    def test_ballistic_catchup_and_attached_fuse_match_contiguous_ticks(self):
        for attached, advances in ((False, 8), (True, 70)):
            results = []
            for skipped in (False, True):
                run, shooter, _, target, angles = self.session(distance=5 if attached else 95)
                fight = run.match.combat
                self.launch(run, shooter, angles if attached else (0, 0), frames=40 if attached else 15)
                # Admit the most recent input projectile before replaying a scheduler gap.
                # A last_tick=None projectile intentionally starts only at the endpoint.
                run.step({})
                endpoint = run.frame
                for advance in ((advances,) if skipped else range(1, advances + 1)):
                    tick = endpoint + advance
                    run.match.update(tick * combat.TICK, tick)
                results.append((fight.vitals_of(target).total,
                                [(r.position, r.fuse_left) for r in fight.rockets]))
            self.assertEqual(results[0], results[1])

    def test_retired_anchor_is_not_replaced_by_a_new_body(self):
        run, shooter, victim, target, angles = self.session()
        fight = run.match.combat
        self.launch(run, shooter, angles)
        old_entity = target.entity
        old_points = [r.position for r in fight.rockets]
        run.match.switch_hero(victim, heroes()[ECHO])
        victim.mover.position[0] += 20
        run.step({})
        self.assertNotEqual(victim.body, old_entity)
        self.assertEqual([r.position for r in fight.rockets], old_points)


if __name__ == '__main__':
    unittest.main()
