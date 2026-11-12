"""Soldier lane: actual pool changes through authored input and shared combat.

NPC fixtures use the training-bot body, not the five unintegrated PvE actors.
These are server witnesses and do not establish original-client acceptance.
"""

import math
import unittest

from unittest.mock import patch

from test_game_combat import Run, aim

from ow174.game import bots, combat, defenses, projectiles, stats
from ow174.game.collision import World, flat_world
from ow174.game.commands import Command
from ow174.game.content import SOLDIER
from ow174.game.script import expr
from ow174.game.script.decode import ClientBody
from ow174.game.script.driver import OWNER_HEALTH, BodyStream


class SoldierCombatLaneTests(unittest.TestCase):
    def arena(self):
        run = Run(names=("Soldier", "Other"))
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        soldier, other = run.players
        soldier.mover.position = (0.0, 0.0, -3.0)
        other.mover.position = (1.0, 0.0, 1.0)
        other.team = 1 - soldier.team
        return run, soldier, other

    def add_bot(self, run, team, position):
        bot = bots.Bot(bots.BOT_ENTITY + len(run.match.bots), {
            "hero": f"0x{SOLDIER:X}", "team": team, "position": position,
            "rotation": (0, 0, 0, 1), "graphs": [],
        }, {"name": "Generic NPC witness", "body": "0x0400000000001403", "health": 200}, {})
        bot.retain_corpse = True
        run.match.bots.append(bot)
        return bot

    def vitals(self, run, owner):
        fight = run.match.combat
        return fight.vitals_of(next(target for target in fight._targets() if target.owner is owner))

    def launch_helix(self, run, soldier):
        frame = soldier.body_script.frame + 1
        soldier.body_script.command(frame, 2)
        run.match.combat.command(soldier, Command(frame, buttons=2))
        rocket, = run.match.combat.rockets
        self.assertEqual((rocket.direct.amount, rocket.blast.amount), (40, 80))
        return rocket

    def fly(self, run, rocket):
        fight = run.match.combat
        for _ in range(200):
            if rocket not in fight.rockets:
                return
            fight.tick += 1
            fight._fly(rocket)
        self.fail("projectile did not terminate")

    def input(self, run, soldier, buttons=0, yaw=0, pitch=0):
        """Run authored input, consume its actual reports and advance shared effects."""
        frame = soldier.body_script.frame + 1
        fight = run.match.combat
        soldier.body_script.command(frame, buttons)
        fight.command(soldier, Command(frame, yaw=yaw, pitch=pitch, buttons=buttons))
        fight.update(frame * combat.TICK, frame, frames=False)

    def test_sprint_ends_during_missing_input_and_combat_recovers(self):
        def send(run, soldier, buttons=0, yaw=-32768, pitch=0, forward=127):
            run.frame += 1
            run.match.update(run.frame * combat.TICK, run.frame)
            command = Command(run.frame, yaw=yaw, pitch=pitch, buttons=buttons, forward=forward)
            soldier.take_commands([command])

        for jump in (False, True):
            with self.subTest(jump=jump):
                run, soldier, enemy = self.arena()
                run.match.collision_world = flat_world(0)
                for player in run.players:
                    player.body_script.ult_seconds = 0
                    player.mover.position = list(player.mover.position)
                    player.mover.body = None

                send(run, soldier, 8)
                for _ in range(11):
                    send(run, soldier)
                self.assertTrue(defenses.soldier_sprint_running(soldier))
                self.assertAlmostEqual(math.hypot(*soldier.mover.velocity[::2]), 8.25, delta=.01)
                position = tuple(soldier.mover.position)

                end = run.frame + 300
                for tick in ((end,) if jump else range(run.frame + 1, end + 1)):
                    run.match.update(tick * combat.TICK, tick)
                self.assertFalse(defenses.soldier_sprint_running(soldier))
                self.assertEqual(tuple(soldier.mover.position), position)

                run.frame = end
                for _ in range(8):
                    send(run, soldier)
                self.assertAlmostEqual(math.hypot(*soldier.mover.velocity[::2]), 5.5, delta=.01)
                x, y, z = enemy.mover.position
                yaw, pitch = aim(run.match.combat._eye(soldier), (x, y + .8, z))
                before = self.vitals(run, enemy).total
                send(run, soldier, 1, yaw, pitch, forward=0)
                self.assertEqual(self.vitals(run, enemy).total, before - 20)

                send(run, soldier, 8)
                for _ in range(11):
                    send(run, soldier)
                self.assertTrue(defenses.soldier_sprint_running(soldier))
                self.assertAlmostEqual(math.hypot(*soldier.mover.velocity[::2]), 8.25, delta=.01)

    def test_melee_hit_miss_range_team_and_static_wall(self):
        for z, ally, wall, expected in (
            (-1.5, False, False, 170), (-1.5, True, False, 200),
            (2.0, False, False, 200), (-1.5, False, True, 200),
        ):
            with self.subTest(z=z, ally=ally, wall=wall):
                run, soldier, other = self.arena()
                other.mover.position = (0.0, 0.0, z)
                if ally:
                    other.team = soldier.team
                if wall:
                    run.match.collision_world = World([
                        (-10, -10, -2, 10, -10, -2, 10, 10, -2),
                        (-10, -10, -2, 10, 10, -2, -10, 10, -2),
                    ])
                pool = self.vitals(run, other)
                self.input(run, soldier, 0x800)
                for _ in range(40):
                    self.input(run, soldier)
                self.assertEqual(pool.total, expected)

    def test_melee_cooldown_rejects_repress_until_recovered(self):
        run, soldier, other = self.arena()
        other.mover.position = (0.0, 0.0, -1.5)
        pool = self.vitals(run, other)
        self.input(run, soldier, 0x800)
        self.input(run, soldier)
        self.input(run, soldier, 0x800)
        for _ in range(40):
            self.input(run, soldier)
        self.assertEqual(pool.total, 170)
        for _ in range(30):
            self.input(run, soldier)
        self.input(run, soldier, 0x800)
        for _ in range(40):
            self.input(run, soldier)
        self.assertEqual(pool.total, 140)

    def test_helix_cooldown_repress_cannot_damage_until_six_seconds(self):
        run, soldier, other = self.arena()
        other.mover.position = (0.0, 0.0, 1.0)
        pool = self.vitals(run, other)
        self.input(run, soldier, 2)
        for _ in range(20):
            self.input(run, soldier)
        self.assertEqual(pool.total, 80)
        pool.heal(120)
        self.input(run, soldier, 2)
        for _ in range(20):
            self.input(run, soldier)
        self.assertEqual(pool.total, 200)
        for _ in range(360):
            self.input(run, soldier)
        self.input(run, soldier, 2)
        for _ in range(20):
            self.input(run, soldier)
        self.assertEqual(pool.total, 80)

    def test_helix_and_melee_cooldowns_finish_without_owner_commands(self):
        for button, graph_id, index, z, damage, gap in (
            (2, 0x0254, 31, 1, 120, 400),
            (0x800, 0x0043, 6, -1.5, 30, 80),
        ):
            for jump in (False, True):
                with self.subTest(button=button, jump=jump):
                    run, soldier, enemy = self.arena()
                    run.match.collision_world = flat_world(0)
                    soldier.mover.position = [0, 0, -3]
                    enemy.mover.position = [0, 0, z]
                    for player in run.players:
                        player.body_script.ult_seconds = 0
                        player.mover.body = None
                    instances = soldier.body_script.component.instances.values()
                    cooldown = next(i.states[index] for i in instances if i.graph.index == graph_id)
                    angles = aim(run.match.combat._eye(soldier), (0, .8, z))
                    run.step({soldier: (*angles, button)})
                    for _ in range(25):
                        run.step({})
                    target = self.vitals(run, enemy)
                    self.assertEqual(target.total, 200 - damage)
                    remaining = cooldown.remaining()
                    self.assertGreater(remaining, 0)

                    run.step({soldier: (*angles, button)})
                    end = run.frame + gap
                    for tick in ((end,) if jump else range(run.frame + 1, end + 1)):
                        run.match.update(tick * combat.TICK, tick)
                    self.assertEqual(target.total, 200 - damage)
                    self.assertEqual(cooldown.remaining(), 0)
                    run.frame = end
                    # Reuse the first contact geometry after the target's knockback.
                    enemy.mover.position = [0, 0, z]
                    enemy.mover.body = None

                    run.step({soldier: (*angles, button)})
                    for _ in range(25):
                        run.step({})
                    self.assertEqual(target.total, max(0, 200 - 2 * damage))

    def test_field_volley_uses_authored_pose_offset_and_body_direction(self):
        for yaw, pitch, offset in ((0, 4096, (.2, 1, .3)), (16384, -4096, (.3, 1, -.2))):
            with self.subTest(yaw=yaw, pitch=pitch):
                run, soldier, enemy = self.arena()
                enemy.mover.position = list(enemy.mover.position)
                soldier.mover.position = [2, 0, -3]
                soldier.mover.body = None
                run.step({soldier: (yaw, pitch, 0x10)})
                for _ in range(40):
                    if run.match.combat.rockets:
                        break
                    run.step({soldier: (yaw, pitch, 0)})
                rocket, = run.match.combat.rockets
                self.assertEqual(rocket.shot.spawn, projectiles.FIELD_ENTITY)
                self.assertEqual(rocket.flown, 0)
                expected = tuple(a + b for a, b in zip(rocket.shot.where, offset, strict=True))
                for actual, want in zip(rocket.position, expected, strict=True):
                    self.assertAlmostEqual(actual, want)
                expected_direction = (1, -5, 0) if yaw else (0, -5, 1)
                for actual, want in zip(rocket.direction, expected_direction, strict=True):
                    self.assertAlmostEqual(actual, want / math.sqrt(26))
                self.assertEqual(rocket.speed, 50)

    def test_field_gravity_keeps_sweep_path_across_a_world_frame_gap(self):
        observations = []
        for jump in (False, True):
            with self.subTest(jump=jump):
                run, soldier, _ = self.arena()
                fight = run.match.combat
                soldier.mover.position = (0, 8, -3)
                self.input(run, soldier, 0x10)
                for _ in range(40):
                    if fight.rockets:
                        break
                    self.input(run, soldier)
                rocket, = fight.rockets
                self.assertEqual(rocket.shot.spawn, projectiles.FIELD_ENTITY)
                self.assertEqual(rocket.gravity, expr.f32(9.8))
                self.assertAlmostEqual(rocket.left + rocket.flown, 10)
                self.assertEqual(rocket.range_limit, 150)
                start_tick = fight.tick
                expected = list(rocket.position)
                velocity = [rocket.speed * axis for axis in rocket.direction]
                expected_distance = rocket.distance
                initial_flown = rocket.flown
                for _ in range(3):
                    velocity[1] -= expr.f32(9.8) * combat.TICK
                    expected = [p + v * combat.TICK for p, v in zip(expected, velocity, strict=True)]
                    expected_distance += math.hypot(*velocity) * combat.TICK
                for tick in ((start_tick + 3,) if jump else range(start_tick + 1, start_tick + 4)):
                    run.match.update(tick * combat.TICK, tick, frames=False)
                self.assertIn(rocket, fight.rockets)
                for actual, wanted in zip(rocket.position, expected, strict=True):
                    self.assertAlmostEqual(actual, wanted)
                self.assertAlmostEqual(rocket.distance, expected_distance)
                self.assertAlmostEqual(rocket.flown, initial_flown + 3 * combat.TICK)
                for tick in ((start_tick + 30,) if jump else range(start_tick + 4, start_tick + 31)):
                    run.match.update(tick * combat.TICK, tick, frames=False)
                self.assertNotIn(rocket, fight.rockets)
                observations.append((rocket.last_tick, rocket.distance, rocket.flown, rocket.shot.field_tick))
        self.assertEqual(len(observations), 2)
        self.assertEqual(*observations)

    def test_field_canister_rebounds_within_the_tick_and_stops_on_tenth_contact(self):
        wall = -2.62
        for previous_rebounds in (0, 8, 9):
            with self.subTest(previous_rebounds=previous_rebounds):
                run, soldier, enemy = self.arena()
                for player in (soldier, enemy):
                    player.mover.position = list(player.mover.position)
                    player.mover.body = None
                run.match.collision_world = World([
                    (-20, 0, -20, -20, 0, 20, 20, 0, 20),
                    (-20, 0, -20, 20, 0, 20, 20, 0, -20),
                    (-2, 0, wall, 2, 0, wall, 2, 4, wall),
                    (-2, 0, wall, 2, 4, wall, -2, 4, wall),
                ])
                fight = run.match.combat
                run.step({soldier: (0, 0, 0x10)})
                for _ in range(40):
                    if fight.rockets:
                        break
                    run.step({})
                rocket, = fight.rockets
                self.assertEqual(rocket.flown, 0)
                self.assertEqual(rocket.rebound_limit, 9)
                self.assertEqual(rocket.rebound_scale, expr.f32(.1))
                rocket.rebounds = previous_rebounds
                origin = rocket.position
                vx, vy, vz = (axis * rocket.speed for axis in rocket.direction)
                end_y = vy - expr.f32(9.8) * combat.TICK
                swept_speed = math.hypot(vx, end_y, vz)
                impact_time = (wall - origin[2]) / vz
                self.assertGreater(impact_time, 0)
                self.assertLess(impact_time, combat.TICK)
                travelled = swept_speed * impact_time
                run.step({})
                self.assertEqual(rocket.rebounds, previous_rebounds + 1)
                if previous_rebounds == 9:
                    self.assertNotIn(rocket, fight.rockets)
                    self.assertAlmostEqual(rocket.flown, impact_time)
                    self.assertAlmostEqual(rocket.distance, travelled)
                    continue
                self.assertIn(rocket, fight.rockets)
                self.assertAlmostEqual(rocket.flown, combat.TICK)
                bounced_z = -expr.f32(.1) * vz
                remainder = combat.TICK - impact_time
                expected_z = wall - 0.005 * vz / swept_speed - 0.001 + bounced_z * remainder
                self.assertAlmostEqual(rocket.position[2], expected_z)
                self.assertAlmostEqual(rocket.direction[2] * rocket.speed, bounced_z)
                self.assertAlmostEqual(rocket.direction[1] * rocket.speed, end_y)
                self.assertAlmostEqual(rocket.distance, travelled + math.hypot(end_y, bounced_z) * remainder)

    def test_field_flight_uses_authored_range_before_lifetime_in_clear_air(self):
        run, soldier, _ = self.arena()
        fight = run.match.combat
        run.match.collision_world = flat_world(-1000)
        self.input(run, soldier, 0x10)
        for _ in range(40):
            if fight.rockets:
                break
            self.input(run, soldier)
        rocket, = fight.rockets
        end = fight.tick + 250
        run.match.update(end * combat.TICK, end, frames=False)
        self.assertNotIn(rocket, fight.rockets)
        self.assertLess(rocket.last_tick, end)
        self.assertAlmostEqual(rocket.distance, 150)
        self.assertGreater(rocket.left, 7)
        self.assertLess(rocket.flown, 3)

    def test_field_waits_for_ground_contact_and_heals_at_the_impact_point(self):
        results = {}
        for ground, stepped in ((0, False), (0, True), (-6, False)):
            with self.subTest(ground=ground, stepped=stepped):
                run, soldier, _ = self.arena()
                fight = run.match.combat
                run.match.collision_world = flat_world(ground)
                soldier.mover.position = (0, 16, -3)
                self.input(run, soldier, 0x10)
                for _ in range(40):
                    if fight.rockets:
                        break
                    self.input(run, soldier)
                rocket, = fight.rockets
                shot = rocket.shot
                initial_deadline = shot.field_due
                launch_origin = shot.where
                self.assertTrue(shot.field_airborne)
                self.assertGreater(launch_origin[1], ground + 10)
                pool = self.vitals(run, soldier)
                pool.damage(20)
                forward = math.copysign(1, rocket.direction[2])
                soldier.mover.position = (0, ground, launch_origin[2] + forward * 3.5)
                run.match.update(initial_deadline * combat.TICK, initial_deadline, frames=False)
                self.assertIn(rocket, fight.rockets)
                self.assertIsNone(shot.field_tick)
                self.assertFalse(shot.field_ids)
                self.assertEqual(pool.total, 180)

                end = initial_deadline + 45
                ticks = range(initial_deadline + 1, end + 1) if stepped else (end,)
                for tick in ticks:
                    run.match.update(tick * combat.TICK, tick, frames=False)
                self.assertNotIn(rocket, fight.rockets)
                self.assertFalse(shot.field_airborne)
                self.assertEqual(shot.field_tick, rocket.last_tick)
                self.assertGreater(shot.field_tick, initial_deadline)
                self.assertAlmostEqual(shot.where[1], ground)
                forward_distance = forward * (shot.where[2] - launch_origin[2])
                self.assertGreater(forward_distance, 2)
                self.assertGreater(pool.total, 180)
                results[ground, stepped] = (shot.field_tick - initial_deadline, forward_distance, pool.total)
        self.assertEqual(len(results), 3)
        self.assertEqual(results[0, True][:2], results[0, False][:2])
        self.assertAlmostEqual(results[0, True][2], results[0, False][2], places=2)
        self.assertGreater(results[-6, False][0], results[0, False][0])
        self.assertGreater(results[-6, False][1], results[0, False][1])

    def test_field_cooldown_finishes_without_owner_commands(self):
        for jump in (False, True):
            with self.subTest(jump=jump):
                run, soldier, enemy = self.arena()
                run.match.collision_world = flat_world(0)
                soldier.mover.position = [0, 0, -3]
                enemy.mover.position = [0, 0, 1]
                for player in run.players:
                    player.body_script.ult_seconds = 0
                    player.mover.body = None
                instances = soldier.body_script.component.instances.values()
                cooldown = next(i.states[0] for i in instances if i.graph.index == 0x0257)
                angles = aim(run.match.combat._eye(enemy), (0, .8, -3))
                own = self.vitals(run, soldier)
                run.step({enemy: (*angles, 1)})
                self.assertEqual(own.total, 180)
                run.step({soldier: (0, 0, 16)})
                for _ in range(100):
                    run.step({})
                self.assertEqual(own.total, 200)
                self.assertGreater(cooldown.remaining(), 0)
                first, = run.match.combat.projectiles.shots

                run.step({soldier: (0, 0, 16)})
                end = run.frame + 1000
                for tick in ((end,) if jump else range(run.frame + 1, end + 1)):
                    run.match.update(tick * combat.TICK, tick)
                self.assertEqual(cooldown.remaining(), 0)
                self.assertFalse(run.match.combat.projectiles.shots)
                run.frame = end

                run.step({enemy: (*angles, 1)})
                self.assertEqual(own.total, 180)
                run.step({soldier: (0, 0, 16)})
                for _ in range(100):
                    run.step({})
                self.assertEqual(own.total, 200)
                second, = run.match.combat.projectiles.shots
                self.assertIsNot(first, second)

    def test_authored_field_heals_over_time_expires_and_rejects_cooldown_repress(self):
        run, soldier, other = self.arena()
        run.match.collision_world = flat_world(0.0)
        other.team = soldier.team
        other.mover.position = soldier.mover.position
        own, ally = self.vitals(run, soldier), self.vitals(run, other)
        own.damage(199)
        ally.damage(199)
        self.input(run, soldier, 0x10)
        for _ in range(120):
            self.input(run, soldier)
        self.assertGreater(own.total, 1)
        self.assertGreater(ally.total, 1)
        self.assertEqual(own.total, ally.total)
        self.input(run, soldier, 0x10)  # still cooling down; no second field
        for _ in range(330):
            self.input(run, soldier)
        self.assertFalse(run.match.combat.projectiles.shots)
        self.assertLess(own.total, 200)  # one field cannot restore 199 HP
        self.assertAlmostEqual(own.total, 175.72, places=5)  # 312 healing ticks at 35 HP/s
        stopped = own.total
        for _ in range(40):
            self.input(run, soldier)
        self.assertEqual(own.total, stopped)
        for _ in range(510):
            self.input(run, soldier)
        self.input(run, soldier, 0x10)
        for _ in range(120):
            self.input(run, soldier)
        self.assertGreater(own.total, stopped)

    def test_field_heals_before_enemy_helix_impact_during_a_world_frame_gap(self):
        outcomes = []
        for skip in (False, True):
            with self.subTest(skip=skip):
                run, soldier, enemy = self.arena()
                run.match.collision_world = flat_world(0.0)
                for actor, position in ((soldier, [0, 0, -3]), (enemy, [0, 0, 17])):
                    actor.mover.position = position
                    actor.mover.body = None
                soldier.body_script.ult_seconds = 0
                fight = run.match.combat
                pool = self.vitals(run, soldier)
                run.step({soldier: (0, 0, 16)})
                for _ in range(85):
                    if any(shot.field_tick is not None for shot in fight.projectiles.shots):
                        break
                    run.step({})
                field, = fight.projectiles.shots
                self.assertEqual(field.field_tick, run.frame)
                pool.damage(88)

                yaw, pitch = aim(fight._eye(enemy), (0, 1.1, -3))
                run.step({enemy: (yaw, pitch, 2)})
                for _ in range(20):
                    missile = next((rocket for rocket in fight.rockets if rocket.shooter is enemy), None)
                    if missile is not None and missile.last_tick is not None:
                        break
                    run.step({})
                else:
                    self.fail("Enemy Helix did not enter flight")

                if skip:
                    endpoint = run.frame + 24
                    run.match.update(endpoint * combat.TICK, endpoint)
                else:
                    for _ in range(24):
                        run.step({})
                self.assertNotIn(missile, fight.rockets)
                self.assertFalse(pool.dead)
                self.assertEqual(enemy.stats.value(stats.HERO_DAMAGE), 120)
                self.assertAlmostEqual(pool.total, 6.56, places=5)
                outcomes.append((pool.total, soldier.stats.value(stats.HEALING), pool.ult_healing_deficit))
        self.assertEqual(len(outcomes), 2)
        for actual, expected in zip(*outcomes, strict=True):
            self.assertAlmostEqual(actual, expected, places=5)

    def test_overlapping_fields_share_healing_credit_across_a_world_frame_gap(self):
        for skip in (False, True):
            with self.subTest(skip=skip):
                run = Run(names=("First", "Second", "Recipient", "Enemy"))
                run.match.bots.clear()
                run.match._pending_bot_scripts.clear()
                run.match.collision_world = flat_world(0.0)
                first, second, recipient, enemy = run.players
                for index, (actor, position) in enumerate(zip(
                    run.players, ([-1, 0, -3], [1, 0, -3], [0, 0, 0], [0, 0, 6]), strict=True,
                )):
                    actor.team = int(index == 3)
                    actor.mover.position = position
                    actor.mover.body = None
                    actor.body_script.ult_seconds = 0
                fight = run.match.combat
                pool = self.vitals(run, recipient)
                run.step({first: (0, 0, 16), second: (0, 0, 16)})
                for _ in range(80):
                    fields = fight.projectiles.shots
                    if len(fields) == 2 and all(shot.field_tick is not None for shot in fields):
                        break
                    run.step({})
                self.assertEqual([shot.player for shot in fields], [first, second])
                self.assertEqual([shot.field_tick for shot in fields], [run.frame, run.frame])
                for _ in range(240):
                    if not fight.rockets:
                        break
                    run.step({})
                self.assertFalse(fight.rockets)
                self.assertEqual(len(fight.projectiles.shots), 2)
                yaw, pitch = aim(fight._eye(enemy), (0, 1.0, 0))
                run.step({enemy: (yaw, pitch, 1)})
                self.assertEqual(pool.total, 180)
                self.assertEqual(pool.ult_healing_deficit, 20)
                if skip:
                    endpoint = run.frame + 24
                    run.match.update(endpoint * combat.TICK, endpoint)
                else:
                    for _ in range(24):
                        run.step({})
                self.assertEqual(pool.total, 200)
                self.assertEqual(recipient.body_script.component.vars[OWNER_HEALTH].value(), pool.total)
                self.assertAlmostEqual(pool.ult_healing_deficit, 0, places=5)
                for caster, expected in ((first, 10.08), (second, 9.92)):
                    self.assertAlmostEqual(caster.stats.value(stats.HEALING), expected, places=5)
                    self.assertAlmostEqual(caster.body_script.component.vars[342].value(), expected, places=5)

    def start_visor(self, run, soldier):
        script = soldier.body_script
        script.ult_seconds = 0  # isolate active-use tests from the existing timed surrogate
        script.set_ult_charge(script.component.vars[338].value())
        self.input(run, soldier, 0x20)
        self.assertEqual(script.component.vars[342].value(), 0)
        for _ in range(80):
            self.input(run, soldier)

    def test_visor_acquires_off_axis_enemy_player_and_npc_only_while_active(self):
        for npc in (False, True):
            with self.subTest(npc=npc):
                run, soldier, other = self.arena()
                other.mover.position = (3.0, 0.0, 3.0)
                victim = other
                if npc:
                    other.spawned = False
                    victim = self.add_bot(run, other.team, other.mover.position)
                pool = self.vitals(run, victim)
                self.input(run, soldier, 1)  # unassisted ray misses
                self.input(run, soldier)
                self.assertEqual(pool.total, 200)
                self.start_visor(run, soldier)
                self.input(run, soldier, 1)
                self.assertEqual(pool.total, 180)
                for _ in range(400):
                    self.input(run, soldier)
                self.input(run, soldier, 1)
                self.assertEqual(pool.total, 180)  # expired: the same ray misses again

    def test_visor_supplied_ranked_targets_update_and_clear_in_owner_frames(self):
        run, soldier, other = self.arena()
        bot = self.add_bot(run, other.team, (2, 0, 4))
        script = soldier.body_script
        world_type = type(script.world)
        track_targets = world_type.track_targets
        ranked = ()

        def supply(world, state):
            if world is script.world and state.instance.graph.index == 0x0259 and state.index == 7:
                return ranked
            return track_targets(world, state)

        client = ClientBody.for_hero(SOLDIER)
        stream = BodyStream(soldier.body)
        update = stream.full(*script.tree_frame())
        client.apply(update)
        stream.arrived(update.chunk_last)

        def transmit():
            payload, keys, snapshot = script.delta(stream.base, stream.touched(), script.frame, False)
            update = stream.delta(payload, snapshot, keys, script.frame)
            frame = client.apply(update)
            stream.arrived(update.chunk_last)
            return frame

        with patch.object(world_type, "track_targets", supply):
            self.start_visor(run, soldier)
            visor = next(item for item in script.component.instances.values() if item.graph.index == 0x0259)
            search = visor.states[7]
            self.assertTrue(search.active)
            self.assertEqual(search.targets, ())
            transmit()
            self.assertEqual(client.vars[(0, 2337)], ())
            self.assertIsNone(client.vars.get((0, 3544)))

            player, npc = expr.Entity(other.body), expr.Entity(bot.entity)
            for next_ranking in ((player, npc), (npc, player), (), (player,)):
                with self.subTest(ranked=next_ranking):
                    ranked = next_ranking
                    for _ in range(3):
                        self.input(run, soldier)
                    expected = ranked[0] if ranked else None
                    self.assertEqual(visor.find_var(1, 2337).value(), ranked)
                    self.assertEqual(visor.find_var(1, 3544).value(), expected)
                    frame = transmit()
                    self.assertTrue(frame.owner)
                    self.assertEqual(client.vars[(0, 2337)], ranked)
                    self.assertEqual(client.vars[(0, 3544)], expected)
                    self.assertEqual(frame.instances[visor.id].states[7],
                                     (True, {"targets": tuple((target.id, 0) for target in ranked)}))

            self.input(run, soldier)
            unchanged = transmit()
            self.assertNotIn(2337, unchanged.entity_vars)
            self.assertNotIn(3544, unchanged.entity_vars)

            for _ in range(400):
                self.input(run, soldier)
            self.assertFalse(search.active)
            transmit()
            self.assertIsNone(client.vars[(0, 2337)])
            self.assertIsNone(client.vars[(0, 3544)])

    def test_visor_rejects_allied_dead_occluded_out_of_range_and_outside_cone(self):
        for case in ("ally", "dead", "wall", "range", "cone"):
            with self.subTest(case=case):
                run, soldier, other = self.arena()
                other.mover.position = (3.0, 0.0, 3.0)
                if case == "ally":
                    other.team = soldier.team
                elif case == "range":
                    other.mover.position = (3.0, 0.0, 50.0)
                elif case == "cone":
                    other.mover.position = (10.0, 0.0, -1.0)
                elif case == "wall":
                    run.match.collision_world = World([
                        (-10, -10, 0, 10, -10, 0, 10, 10, 0),
                        (-10, -10, 0, 10, 10, 0, -10, 10, 0),
                    ])
                pool = self.vitals(run, other)
                if case == "dead":
                    pool.died_at = run.match.combat.now
                self.start_visor(run, soldier)
                self.input(run, soldier, 1)
                self.assertEqual(pool.total, 200)

    def test_visor_cannot_acquire_without_full_charge_or_before_activation_delay(self):
        run, soldier, other = self.arena()
        other.mover.position = (3.0, 0.0, 3.0)
        pool = self.vitals(run, other)
        script = soldier.body_script
        script.ult_seconds = 0
        script.set_ult_charge(0)
        self.input(run, soldier, 0x20)
        self.input(run, soldier, 1)
        self.assertEqual(pool.total, 200)
        self.input(run, soldier)
        script.set_ult_charge(script.component.vars[338].value())
        self.input(run, soldier, 0x20)
        self.input(run, soldier, 1)
        self.assertEqual(pool.total, 200)
        for _ in range(80):
            self.input(run, soldier)
        self.input(run, soldier, 1)
        self.assertEqual(pool.total, 180)

    def test_actual_visor_aims_at_exposed_capsule_when_center_is_covered(self):
        for cover_height, damage in ((1.35, 20), (2.5, 0)):
            with self.subTest(cover_height=cover_height):
                run, soldier, other = self.arena()
                soldier.mover.position = [0, 0, -3]
                other.mover.position = [1.5, 0, 3]
                for player in run.players:
                    player.mover.body = None
                wall = [
                    (.3, 0, 0, 1.2, 0, 0, 1.2, cover_height, 0),
                    (.3, 0, 0, 1.2, cover_height, 0, .3, cover_height, 0),
                ]
                floor = [
                    (-20, 0, -20, -20, 0, 20, 20, 0, 20),
                    (-20, 0, -20, 20, 0, 20, 20, 0, -20),
                ]
                run.match.collision_world = World(floor + wall + [
                    (*a[0:3], *a[6:9], *a[3:6]) for a in wall
                ])
                script = soldier.body_script
                script.ult_seconds = 0
                script.set_ult_charge(script.component.vars[338].value())
                run.step({soldier: (0, 0, 0x20)})
                for _ in range(80):
                    run.step({soldier: (0, 0, 0)})
                pool = self.vitals(run, other)
                origin = run.match.combat._eye(soldier)
                centre = (other.mover.position[0], 1, other.mover.position[2])
                yaw, pitch = aim(origin, centre)
                self.assertIsNotNone(combat.ray_world(
                    run.match.collision_world, origin, combat.aim(yaw, pitch), math.dist(origin, centre),
                ))
                run.step({soldier: (0, 0, 1)})
                self.assertEqual(pool.total, 200 - damage)
                self.assertEqual(script.component.vars[342].value(), 0)

    def test_death_during_visor_prevents_rifle_helix_melee_and_field_inputs(self):
        run, soldier, other = self.arena()
        other.mover.position = (0.0, 0.0, -1.5)
        self.start_visor(run, soldier)
        fight = run.match.combat
        victim = next(target for target in fight._targets() if target.owner is soldier)
        fight._damage(other, victim, 200, False, None, fight.tick)
        self.assertTrue(fight.is_dead(soldier))
        pool = self.vitals(run, other)
        self.input(run, soldier, 1 | 2 | 0x800 | 0x10)
        for _ in range(80):
            self.input(run, soldier)
        self.assertEqual(pool.total, 200)
        self.assertFalse(fight.rockets)
        self.assertFalse(fight.projectiles.shots)

    def test_rifle_body_critical_far_falloff_miss_ally_and_wall_change_real_pools(self):
        for case, z, expected in (("body", 1.0, 180), ("head", 1.0, 160), ("far", 57.0, 194),
                                  ("far head", 57.0, 188), ("inside max range", 177.25, 194),
                                  ("outside max range", 177.75, 200), ("miss", 1.0, 200),
                                  ("ally", 1.0, 200), ("wall", 1.0, 200)):
            with self.subTest(case=case):
                run, soldier, other = self.arena()
                other.mover.position = (0.0, 0.0, z)
                run.match.collision_world = flat_world(0.0)
                if case == "ally":
                    other.team = soldier.team
                if case == "wall":
                    run.match.collision_world = World([
                        (-10, -10, 0, 10, -10, 0, 10, 10, 0),
                        (-10, -10, 0, 10, 10, 0, -10, 10, 0),
                    ])
                height = 1.55 if case in ("head", "far head") else 1.0
                yaw, pitch = aim(run.match.combat._eye(soldier),
                                 (0.0, height, other.mover.position[2]))
                if case == "miss":
                    yaw = 16384
                pool = self.vitals(run, other)
                self.input(run, soldier, 1, yaw, pitch)
                self.assertAlmostEqual(pool.total, expected, places=5)

    def test_rifle_empty_magazine_and_reload_prevent_extra_damage(self):
        run, soldier, other = self.arena()
        other.mover.position = (3.0, 0.0, 3.0)  # deliberate miss while draining ammo
        pool = self.vitals(run, other)
        rifle = soldier.body_script.component.instances[11]
        for _ in range(204):
            self.input(run, soldier, 1)
        self.assertEqual(rifle.vars[53].value(), 0)
        yaw, pitch = aim(run.match.combat._eye(soldier), (3.0, 1.0, 3.0))
        self.input(run, soldier)
        self.input(run, soldier, 1, yaw, pitch)
        self.assertEqual(pool.total, 200)  # no ammunition, despite an aimed press
        self.input(run, soldier, 4)  # reload
        for _ in range(150):
            self.input(run, soldier)
        self.assertEqual(rifle.vars[53].value(), 30)
        self.input(run, soldier, 1, yaw, pitch)
        self.assertEqual(pool.total, 180)
        self.assertEqual(rifle.vars[53].value(), 29)

    def test_admitted_rifle_reload_completes_without_owner_commands(self):
        for jump in (False, True):
            with self.subTest(jump=jump):
                run, soldier, enemy = self.arena()
                run.match.collision_world = flat_world(0)
                for player in run.players:
                    player.body_script.ult_seconds = 0
                    player.mover.position = list(player.mover.position)
                    player.mover.body = None
                rifle = soldier.body_script.component.instances[11]
                ammo, reload = rifle.vars[53], rifle.vars[51]
                run.step({soldier: (16384, 0, 1)})
                self.assertEqual(ammo.value(), 29)
                for _ in range(40):
                    run.step({})
                run.step({soldier: (0, 0, 0x400)})
                self.assertTrue(reload.value())
                start = run.frame

                previous = start
                for end, rounds in ((start + 20, 29), (start + 300, 30)):
                    for tick in ((end,) if jump else range(previous + 1, end + 1)):
                        run.match.update(tick * combat.TICK, tick)
                    self.assertEqual(ammo.value(), rounds)
                    self.assertEqual(bool(reload.value()), rounds == 29)
                    previous = end
                self.assertEqual(self.vitals(run, enemy).total, 200)

                run.frame = previous
                x, y, z = enemy.mover.position
                yaw, pitch = aim(run.match.combat._eye(soldier), (x, y + .8, z))
                run.step({soldier: (yaw, pitch, 1)})
                self.assertEqual(self.vitals(run, enemy).total, 180)
                self.assertEqual(ammo.value(), 29)

    def test_authored_field_cannot_heal_allies_through_a_static_wall_or_beyond_radius(self):
        for case in ("visible", "wall", "range"):
            with self.subTest(case=case):
                run, soldier, other = self.arena()
                run.match.collision_world = flat_world(0.0)
                other.team = soldier.team
                other.mover.position = (0.0, 0.0, 1.0 if case != "range" else 5.0)
                if case == "wall":
                    run.match.collision_world = World([
                        (-10, -10, -1, 10, -10, -1, 10, 10, -1),
                        (-10, -10, -1, 10, 10, -1, -10, 10, -1),
                    ])
                pool = self.vitals(run, other)
                pool.damage(100)
                self.input(run, soldier, 0x10)
                for _ in range(120):
                    self.input(run, soldier)
                self.assertEqual(pool.total > 100, case == "visible")

    def test_field_cast_death_before_throw_cannot_deploy_or_heal(self):
        run, soldier, other = self.arena()
        other.team = soldier.team
        other.mover.position = soldier.mover.position
        pool = self.vitals(run, other)
        pool.damage(100)
        self.input(run, soldier, 0x10)
        victim = next(target for target in run.match.combat._targets() if target.owner is soldier)
        run.match.combat._damage(soldier, victim, 200, False, None, run.match.combat.tick)
        self.assertTrue(run.match.combat.is_dead(soldier))
        for _ in range(100):
            self.input(run, soldier)
        self.assertEqual(pool.total, 100)
        self.assertFalse(run.match.combat.projectiles.shots)

    def test_wall_impact_keeps_front_splash_and_blocks_rear_players_and_npcs(self):
        for npc in (False, True):
            for side, damaged in ((-1.0, True), (1.0, False)):
                with self.subTest(npc=npc, side=side):
                    run, soldier, other = self.arena()
                    other.mover.position = (1.0, 0.0, side)
                    victim = other
                    if npc:
                        other.spawned = False
                        victim = self.add_bot(run, other.team, (1.0, 0.0, side))
                    # Actual static triangles; no mocked ray or damage resolver.
                    run.match.collision_world = World([
                        (-10, -10, 0, 10, -10, 0, 10, 10, 0),
                        (-10, -10, 0, 10, 10, 0, -10, 10, 0),
                    ])
                    vitals = self.vitals(run, victim)
                    rocket = self.launch_helix(run, soldier)
                    self.fly(run, rocket)
                    self.assertEqual(vitals.total < 200, damaged)
                    if damaged:
                        self.assertGreaterEqual(vitals.total, 120)  # splash only
                    else:
                        self.assertEqual(vitals.total, 200)
                        self.assertEqual(soldier.client.messages(combat.HIT), [])

    def test_unobstructed_helix_direct_and_splash_remain_120(self):
        run, soldier, other = self.arena()
        other.mover.position = (0.0, 0.0, 1.0)
        vitals = self.vitals(run, other)
        rocket = self.launch_helix(run, soldier)
        self.fly(run, rocket)
        self.assertEqual(vitals.total, 80)

    def test_direct_impact_does_not_splash_a_second_target_through_wall(self):
        run, soldier, other = self.arena()
        other.mover.position = (0.0, 0.0, -1.0)
        behind = self.add_bot(run, other.team, (1.0, 0.0, 1.0))
        run.match.collision_world = World([
            (-10, -10, 0, 10, -10, 0, 10, 10, 0),
            (-10, -10, 0, 10, 10, 0, -10, 10, 0),
        ])
        front_health = self.vitals(run, other)
        rear_health = self.vitals(run, behind)
        self.fly(run, self.launch_helix(run, soldier))
        self.assertEqual(front_health.total, 80)
        self.assertEqual(rear_health.total, 200)

    def test_field_heals_self_and_allied_player_and_npc_but_not_enemies_or_dead(self):
        run, soldier, other = self.arena()
        other.team = soldier.team
        other.mover.position = (0.0, 0.0, -3.0)
        ally = self.add_bot(run, soldier.team, other.mover.position)
        enemy = self.add_bot(run, 1 - soldier.team, other.mover.position)
        dead = self.add_bot(run, soldier.team, other.mover.position)
        owners = (soldier, other, ally, enemy, dead)
        pools = [self.vitals(run, owner) for owner in owners]
        for pool in pools:
            pool.damage(100)
        pools[-1].damage(100)
        pools[-1].died_at = run.match.combat.now
        run.match.combat.heal_area(soldier, soldier.mover.position, 3.0, 35.0)
        self.assertEqual([pool.total for pool in pools], [135, 135, 135, 100, 0])
        self.assertEqual(soldier.stats.value(stats.HEALING), 105)

    def test_enemy_player_and_npc_rifle_damage_soldier_from_authored_volley(self):
        for npc in (False, True):
            with self.subTest(npc=npc):
                run, soldier, other = self.arena()
                other.mover.position = (0.0, 0.0, -7.0)
                fight = run.match.combat
                vitals = self.vitals(run, soldier)
                yaw, pitch = aim(fight._eye(other), (0.0, 1.0, -3.0))
                frame = other.body_script.frame + 1
                other.body_script.command(frame, 1)
                reports = list(other.body_script.component.shots)
                self.assertTrue(reports)
                source = other
                if npc:
                    # Exercise the NPC source branch with the same authored rifle;
                    # this does not claim the five enemies' own weapons or AI.
                    source = self.add_bot(run, other.team, other.mover.position)
                    source.yaw = yaw
                state, number, when, activation = reports[0]
                command = None if npc else Command(frame, yaw=yaw, pitch=pitch)
                fight._shoot(source, command, state, number, when,
                             activation=activation)
                self.assertLess(vitals.total, 200)
                self.assertEqual(vitals.total, 160 if npc else 180)


if __name__ == "__main__":
    unittest.main()
