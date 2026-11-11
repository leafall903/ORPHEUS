"""Actual Tracer and Mercy inputs through the 104319 staff graphs."""

import math
import unittest
from dataclasses import replace

import test_game_tracer as tracer_tests
import test_game_tracer_pulse_deflect as support_tests
from test_game_combat import aim, in_world
from test_game_mover import FLOOR, box, wall

from ow174.game import combat, mercy, mercy_effects, stats
from ow174.game.body_vm_snapshot import export_component_vm, snapshot_digest
from ow174.game.collision import World
from ow174.game.commands import CROUCH, FIRE, JUMP, SECONDARY, Command
from ow174.game.content import SOLDIER, heroes
from ow174.game.script import expr, graph, nodes


class TracerMercyTests(unittest.TestCase):
    def setUp(self):
        self.run, self.tracer, self.enemy, self.enemy_health = tracer_tests.TracerPistolTests().fight()
        self.match = self.run.match
        self.fight = self.match.combat
        self.match.game_map = replace(self.match.game_map, free_for_all=False)
        self.mercy, _ = support_tests.PulseDeflectTests().support(
            self.run, self.tracer, hero=mercy.HERO)
        self.tracer.team = self.mercy.team = 0
        self.enemy.team = 1
        x, y, z = self.tracer.mover.position
        self.place(self.mercy, (x, y, z - 4))
        self.step(25)
        target = next(t for t in self.fight._targets() if t.owner is self.tracer)
        self.health = self.fight.vitals_of(target)
        self.staff = next(i for i in self.mercy.body_script.component.instances.values()
                          if i.graph.index == mercy.STAFF)

    @staticmethod
    def place(actor, position):
        actor.mover.position[:] = position
        actor.mover.body = None

    def aim_at(self, source, target):
        x, y, z = target.mover.position
        return aim(self.fight._eye(source), (x, y + 1, z))

    def step(self, frames=1, *, staff=0, action=0, target=None):
        for _ in range(frames):
            self.run.step({self.mercy: (*self.aim_at(self.mercy, target or self.tracer), staff),
                           self.tracer: (*self.aim_at(self.tracer, self.enemy), action)})

    def test_heal_selects_injured_tracer_then_releases_and_caps_at_real_health(self):
        self.health.damage(44)
        self.step(25, staff=FIRE)
        hold = self.staff.state(mercy.HOLD)
        self.assertTrue(hold.active)
        self.assertEqual(hold.targets, (self.tracer.body,))
        self.assertEqual(self.staff.find_var(expr.INSTANCE, 468).value(), (expr.Entity(self.tracer.body),))
        self.assertAlmostEqual(self.health.total, 128, places=4)
        self.assertAlmostEqual(self.mercy.stats.value(stats.HEALING), 22, places=4)
        self.step(5)
        self.assertAlmostEqual(self.health.total, 128, places=4)
        self.step(35, staff=FIRE)
        self.assertEqual(self.health.total, 150)
        self.assertAlmostEqual(self.mercy.stats.value(stats.HEALING), 44, places=4)
        self.assertEqual(self.tracer.body_script.component.entity_var(253).value(), 150)

    def test_heal_excludes_enemies_antiheal_and_lost_line_of_sight(self):
        self.health.damage(55)
        self.enemy_health.damage(55)
        self.tracer.team = self.enemy.team
        self.step(10, staff=FIRE)
        self.assertEqual(self.health.total, 95)
        self.assertEqual(self.enemy_health.total, 145)
        self.tracer.team = self.mercy.team
        self.step(5)
        self.health.healing_block_until = self.run.frame + 30
        self.step(10, staff=FIRE)
        self.assertEqual(self.health.total, 95)
        self.health.healing_block_until = 0
        self.step(10, staff=FIRE)
        self.assertGreater(self.health.total, 95)
        before = self.health.total
        x, y, z = self.tracer.mover.position
        self.match.collision_world = World(FLOOR + wall(x - 3, z - 2, x + 3, z - 2, y - 1, y + 5))
        self.step(12, staff=FIRE)
        self.assertEqual(self.health.total, before)
        self.assertFalse(self.staff.state(mercy.HOLD).targets)

    def test_boost_scales_actual_pistol_pair_and_release_restores_base_damage(self):
        if graph.graph(0x006C) is None:
            self.skipTest("requires extracted graph 006C")
        self.step(12, staff=2)
        self.assertEqual(self.staff.state(mercy.HOLD).targets, (self.tracer.body,))
        self.step(staff=2, action=FIRE)
        self.assertAlmostEqual(self.enemy_health.total, 184.4, places=4)
        self.assertAlmostEqual(self.tracer.stats.value(stats.HERO_DAMAGE), 15.6, places=4)
        self.assertEqual(self.mercy.stats.value(stats.HERO_DAMAGE), 0)
        self.step(12)
        self.step(action=FIRE)
        self.assertAlmostEqual(self.enemy_health.total, 172.4, places=4)

    def test_boost_scales_actual_quick_melee_and_stops_outside_staff_range(self):
        if graph.graph(0x006C) is None:
            self.skipTest("requires extracted graph 006C")
        x, y, z = self.tracer.mover.position
        self.place(self.enemy, (x, y, z + 1.5))
        self.step(12, staff=2)
        self.step(staff=2, action=0x800)
        self.step(18, staff=2)
        self.assertAlmostEqual(self.enemy_health.total, 161, places=4)
        self.place(self.mercy, (x, y, z - 30))
        self.step(65, staff=2)
        self.step(staff=2, action=0x800)
        self.step(18, staff=2)
        self.assertAlmostEqual(self.enemy_health.total, 131, places=4)


    def test_pulse_keeps_launch_boost_after_mercy_releases(self):
        if graph.graph(0x006C) is None:
            self.skipTest("requires extracted graph 006C")
        self.fight.vitals[self.enemy.body] = pool = combat.Vitals(self.enemy.body, 1000)
        script = self.tracer.body_script
        script.ult_seconds = 0
        script.set_ult_charge(script.component.entity_var(338).value())
        self.step(12, staff=2)
        self.step(staff=2, action=32)
        for _ in range(20):
            if self.fight.pulse_bombs.bombs:
                break
            self.step(staff=2)
        self.assertEqual(len(self.fight.pulse_bombs.bombs), 1)
        bomb = self.fight.pulse_bombs.bombs[0]
        self.assertAlmostEqual(bomb.blast.amount, 455, places=4)
        self.step(90)
        self.assertFalse(self.staff.state(mercy.HOLD).active)
        self.assertFalse(self.fight.pulse_bombs.bombs)
        self.assertAlmostEqual(pool.total, 538.5, places=4)

    def test_hostile_blaster_and_tracer_pistol_exchange_actual_damage(self):
        self.mercy.team = self.enemy.team
        self.step(30)
        self.run.frame += 1
        self.match.update(self.run.frame * combat.TICK, self.run.frame)
        self.mercy.take_commands([Command(self.run.frame, action=2)])
        self.step(35)
        self.step(20, staff=FIRE)
        self.step(15)
        self.assertLess(self.health.total, 150)
        self.assertEqual(self.mercy.stats.value(stats.HERO_DAMAGE), 150 - self.health.total)
        tracer_tests.TracerPistolTests().fire(self.run, self.tracer, self.mercy)
        target = next(t for t in self.fight._targets() if t.owner is self.mercy)
        self.assertEqual(self.fight.vitals_of(target).total, 188)

    def guardian_setup(self):
        x, y, z = self.tracer.mover.position
        self.place(self.mercy, (x, y, z - 12))
        self.step(3)
        return next(i for i in self.mercy.body_script.component.instances.values()
                    if i.graph.index == mercy.GUARDIAN)

    def test_guardian_flies_to_actual_tracer_and_enters_cooldown(self):
        guardian = self.guardian_setup()
        origin = self.mercy.mover.position[2]
        self.step(staff=8)
        move = guardian.state(0)
        self.assertTrue(move.active)
        destination, speed = move.move_request()
        self.assertEqual(speed, 17)
        self.assertGreater(destination.z, origin + 7)
        self.step(45, staff=8)
        self.assertFalse(move.active)
        self.assertAlmostEqual(self.mercy.mover.position[2], destination.z, delta=0.02)
        cooldown = guardian.find_var(expr.INSTANCE, 61)
        self.assertGreater(cooldown.value(), 1)
        x, y, z = self.tracer.mover.position
        self.place(self.tracer, (x, y, z + 6))
        self.step()
        self.step(10, staff=8)
        self.assertFalse(move.active)
        self.step(100)
        self.assertEqual(cooldown.value(), 0)
        self.step(6, staff=8)
        self.assertTrue(move.active)
        self.assertGreater(self.mercy.mover.position[2], destination.z + 0.5)

    def test_guardian_rejects_hostile_tracer_range_and_static_wall(self):
        guardian = self.guardian_setup()
        origin = tuple(self.mercy.mover.position)
        x, y, z = self.tracer.mover.position
        self.tracer.team = self.enemy.team
        self.step(10, staff=8)
        self.assertFalse(guardian.state(0).active)
        self.step()
        self.tracer.team = self.mercy.team
        self.place(self.tracer, (x, y, z + 40))
        self.step(10, staff=8)
        self.assertFalse(guardian.state(0).active)
        self.step()
        self.place(self.tracer, (x, y, z))
        self.match.collision_world = World(FLOOR + wall(x - 3, z - 6, x + 3, z - 6, y - 1, y + 5))
        self.step(10, staff=8)
        self.assertFalse(guardian.state(0).active)
        self.assertEqual(self.mercy.mover.position[::2], list(origin[::2]))
        self.assertEqual(guardian.find_var(expr.INSTANCE, 61).value(), 0)

    def test_guardian_keeps_cast_destination_when_tracer_blinks_and_release_cancels(self):
        guardian = self.guardian_setup()
        self.step(4, staff=8)
        move = guardian.state(0)
        destination, _ = move.move_request()
        before = self.tracer.mover.position[2]
        self.step(staff=8, action=2)
        self.assertAlmostEqual(self.tracer.mover.position[2] - before, 7.5, delta=0.1)
        self.assertEqual(move.move_request()[0], destination)
        self.step()
        position = tuple(self.mercy.mover.position)
        self.assertFalse(move.active)
        self.assertGreater(guardian.find_var(expr.INSTANCE, 61).value(), 1.4)
        self.step(8)
        self.assertEqual(self.mercy.mover.position[::2], list(position[::2]))

    def test_guardian_selects_tracer_corpse_without_reviving_her(self):
        guardian = self.guardian_setup()
        self.health.damage(self.health.total - 10)
        self.run.step({self.enemy: (*self.aim_at(self.enemy, self.tracer), FIRE)})
        self.assertTrue(self.fight.is_dead(self.tracer))
        origin = self.mercy.mover.position[2]
        self.step(8, staff=8)
        self.assertTrue(guardian.state(0).active)
        self.assertGreater(self.mercy.mover.position[2], origin + 1)
        self.assertTrue(self.fight.is_dead(self.tracer))
        self.assertEqual(self.health.total, 0)


    def test_actual_hostile_fire_kills_mercy_during_guardian_flight(self):
        guardian = self.guardian_setup()
        target = next(t for t in self.fight._targets() if t.owner is self.mercy)
        vitals = self.fight.vitals_of(target)
        vitals.damage(vitals.total - 10)
        self.step(4, staff=8)
        self.assertTrue(guardian.state(0).active)
        x, y, z = self.mercy.mover.position
        self.place(self.enemy, (x + 4, y, z))
        self.run.step({self.enemy: (*self.aim_at(self.enemy, self.mercy), FIRE),
                       self.mercy: (*self.aim_at(self.mercy, self.tracer), 8)})
        self.assertTrue(self.fight.is_dead(self.mercy))
        self.assertEqual(vitals.killer, self.enemy.body)
        position = tuple(self.mercy.mover.position)
        self.step(10, staff=8)
        self.assertFalse(guardian.state(0).active)
        self.assertEqual(tuple(self.mercy.mover.position), position)
        self.assertGreater(guardian.find_var(expr.INSTANCE, 61).value(), 0)

    def resurrect_setup(self):
        x, y, z = self.tracer.mover.position
        self.place(self.mercy, (x, y, z - 3))
        self.step(3)
        instance = next(i for i in self.mercy.body_script.component.instances.values()
                        if i.graph.index == mercy.RESURRECT)
        self.health.damage(self.health.total - 10)
        self.run.step({self.enemy: (*self.aim_at(self.enemy, self.tracer), FIRE)})
        self.assertTrue(self.fight.is_dead(self.tracer))
        self.step(3)
        return instance

    def test_actual_resurrect_revives_same_tracer_body_and_releases_attack_lock(self):
        instance = self.resurrect_setup()
        body = self.tracer.body
        self.step(staff=16)
        self.step(100, action=FIRE)
        self.assertTrue(self.fight.is_dead(self.tracer))
        cooldown = instance.find_var(expr.INSTANCE, 61)
        self.assertEqual(cooldown.value(), 0)
        for _ in range(20):
            self.step(action=FIRE)
            if not self.fight.is_dead(self.tracer):
                break
        self.assertFalse(self.fight.is_dead(self.tracer))
        self.assertEqual(self.tracer.body, body)
        self.assertEqual(self.health.total, 150)
        self.assertFalse(any(p is self.tracer for _, p in self.fight.respawns))
        self.assertGreater(cooldown.value(), 29)
        self.assertEqual(self.mercy.stats.value(0x086000000000026A), 1)

        self.step(80, action=FIRE)
        self.assertEqual(self.enemy_health.total, 200)
        self.run.step({self.enemy: (*self.aim_at(self.enemy, self.tracer), FIRE)})
        self.assertEqual(self.health.total, 150)
        self.step(65)
        self.step(action=FIRE)
        self.assertEqual(self.enemy_health.total, 188)
        self.run.step({self.enemy: (*self.aim_at(self.enemy, self.tracer), FIRE)})
        self.assertEqual(self.health.total, 130)

    def test_actual_resurrect_cast_fails_if_enemy_kills_mercy_before_completion(self):
        instance = self.resurrect_setup()
        self.step(staff=16)
        self.step(30)
        x, y, z = self.mercy.mover.position
        self.place(self.enemy, (x + 4, y, z))
        target = next(t for t in self.fight._targets() if t.owner is self.mercy)
        vitals = self.fight.vitals_of(target)
        vitals.damage(vitals.total - 10)
        self.run.step({self.enemy: (*self.aim_at(self.enemy, self.mercy), FIRE)})
        self.assertTrue(self.fight.is_dead(self.mercy))
        self.step(100)
        self.assertTrue(self.fight.is_dead(self.tracer))
        self.assertEqual(self.health.total, 0)
        self.assertGreater(instance.find_var(expr.INSTANCE, 61).value(), 28)
        self.assertEqual(self.mercy.stats.value(0x086000000000026A), 0)


    def test_resurrect_range_exit_cancels_the_cast(self):
        instance = self.resurrect_setup()
        self.step(staff=16)
        self.assertTrue(instance.state(19).flags & nodes.Ability.RUNNING)
        self.step(20)
        x, y, z = self.mercy.mover.position
        self.place(self.mercy, (x + 20, y, z))
        self.step(100)
        self.assertFalse(instance.state(19).flags & nodes.Ability.RUNNING)
        self.assertTrue(self.fight.is_dead(self.tracer))
        self.assertEqual(self.mercy.stats.value(0x086000000000026A), 0)

    def test_resurrect_rejects_living_tracer_and_hostile_corpse_without_cooldown(self):
        x, y, z = self.tracer.mover.position
        self.place(self.mercy, (x, y, z - 3))
        self.step(3)
        instance = next(i for i in self.mercy.body_script.component.instances.values()
                        if i.graph.index == mercy.RESURRECT)
        self.step(staff=16)
        self.step(4)
        self.assertFalse(instance.state(19).flags & nodes.Ability.RUNNING)
        self.assertEqual(instance.find_var(expr.INSTANCE, 61).value(), 0)
        self.mercy.team = self.enemy.team
        self.health.damage(self.health.total - 10)
        self.run.step({self.enemy: (*self.aim_at(self.enemy, self.tracer), FIRE)})
        self.assertTrue(self.fight.is_dead(self.tracer))
        self.step(3)
        self.step(staff=16)
        self.step(120)
        self.assertTrue(self.fight.is_dead(self.tracer))
        self.assertEqual(instance.find_var(expr.INSTANCE, 61).value(), 0)

    def valkyrie(self):
        script = self.mercy.body_script
        instance = next(i for i in script.component.instances.values() if i.graph.index == 0x10DA)
        return script, instance

    def test_actual_pulse_kills_hostile_mercy_and_ends_valkyrie(self):
        self.mercy.team = self.enemy.team
        script, instance = self.valkyrie()
        state = instance.state(9)
        cost = script.component.entity_var(338).value()
        self.assertEqual(cost, 1820)
        script.set_ult_charge(cost - 1)
        self.step(staff=32)
        self.assertFalse(state.flags & nodes.Ability.RUNNING)
        self.assertEqual(script.component.entity_var(342).value(), cost - 1)
        self.step()
        script.set_ult_charge(cost)
        self.step(staff=32)
        self.assertTrue(state.flags & nodes.Ability.RUNNING)
        self.assertEqual(script.component.entity_var(342).value(), 0)
        initial_y = self.mercy.mover.position[1]
        self.fly(15, buttons=JUMP)
        self.fly(25, pitch=-16000)
        self.assertGreater(self.mercy.mover.position[1], initial_y + 2)
        tracer_script = self.tracer.body_script
        tracer_script.set_ult_charge(tracer_script.component.entity_var(338).value())
        pulse_aim = self.aim_at(self.tracer, self.mercy)
        self.run.step({self.tracer: (*pulse_aim, 32)})
        for _ in range(20):
            if self.fight.pulse_bombs.bombs:
                break
            self.run.step({self.tracer: (*pulse_aim, 0)})
        self.assertEqual(len(self.fight.pulse_bombs.bombs), 1)
        bomb = self.fight.pulse_bombs.bombs[0]
        for _ in range(20):
            if bomb.stuck:
                break
            self.fly(1)
        self.assertEqual(bomb.attached, self.mercy.body)
        before = bomb.position
        self.fly(20, right=127)
        self.assertEqual(bomb.attached, self.mercy.body)
        self.assertGreater(math.dist(bomb.position, before), 0.5)
        self.step(65)
        target = next(t for t in self.fight._targets() if t.owner is self.mercy)
        health = self.fight.vitals_of(target)
        self.assertTrue(health.dead)
        self.assertEqual(health.killer, self.tracer.body)
        self.assertFalse(state.flags & nodes.Ability.RUNNING)
        self.assertFalse(script.component.entity_var(842).value())
        self.assertFalse(self.fight.pulse_bombs.bombs)

    def test_valkyrie_release_keeps_authored_window_then_resumes_passive_charge(self):
        script, instance = self.valkyrie()
        script.ult_seconds = 60.0
        state = instance.state(9)
        script.set_ult_charge(script.component.entity_var(338).value())
        self.step(staff=32)
        self.fly(40, buttons=JUMP)
        self.fly(32, pitch=-16000)
        self.fly(848)
        high = self.mercy.mover.position[1]
        self.assertGreater(high, 4)
        self.assertTrue(state.flags & nodes.Ability.RUNNING)
        self.assertEqual(script.component.entity_var(342).value(), 0)
        self.assertTrue(script.component.entity_var(842).value())
        self.step(32)
        self.assertFalse(state.flags & nodes.Ability.RUNNING)
        self.assertEqual(instance.find_var(expr.INSTANCE, 215).value(), 0)
        self.assertFalse(script.component.entity_var(842).value())
        self.assertGreater(script.component.entity_var(342).value(), 0)


    def fly(self, frames, *, buttons=0, forward=0, right=0, pitch=0, yaw=0):
        for _ in range(frames):
            self.run.frame += 1
            frame = self.run.frame
            self.match.update(frame * combat.TICK, frame)
            for actor in self.run.players:
                actor.client.queue_entities(self.match.remote_movements(actor))
                command = (Command(frame, buttons=buttons, forward=forward, right=right,
                                   pitch=pitch, yaw=yaw) if actor is self.mercy else Command(frame))
                actor.take_commands([command])

    def start_flight(self):
        script, instance = self.valkyrie()
        script.set_ult_charge(script.component.entity_var(338).value())
        self.fly(1, buttons=32)
        self.assertTrue(instance.state(9).flags & nodes.Ability.RUNNING)
        return instance

    def test_valkyrie_pitch_and_vertical_inputs_fly_with_authored_speed(self):
        x, y, z = self.mercy.mover.position
        self.fly(40, buttons=JUMP)
        self.assertLess(self.mercy.mover.position[1], y + 1)
        self.fly(30)
        self.start_flight()
        self.fly(60, forward=127, pitch=-8192)
        tx, ty, tz = self.mercy.mover.position
        self.assertAlmostEqual(tx, x, delta=0.02)
        self.assertGreater(ty, y + 4)
        self.assertGreater(tz, z + 4)
        vx, vy, vz = self.mercy.mover.velocity
        self.assertAlmostEqual(vy, vz, delta=0.02)
        self.assertAlmostEqual(math.hypot(vx, vy, vz), 9, delta=0.02)
        self.fly(36, buttons=CROUCH)
        self.assertLess(self.mercy.mover.position[1], ty - 1)
        self.assertLess(self.mercy.mover.velocity[1], -8.9)
        self.assertFalse(self.mercy.mover.crouched)
        self.assertEqual(self.mercy.mover.values.accel_air, 8)

    def test_valkyrie_flight_respects_wall_ceiling_and_floor(self):
        x, y, z = self.mercy.mover.position
        floor = box(x - 6, x + 6, y - 1, y, z - 6, z + 6)
        ceiling = box(x - 3, x + 3, y + 5, y + 6, z - 3, z + 3)
        self.match.collision_world = World(floor + wall(x - 3, z + 2, x + 3, z + 2, y, y + 6) + ceiling)
        self.start_flight()
        self.fly(40, forward=127)
        self.assertGreater(self.mercy.mover.position[2], z + 1)
        self.assertLess(self.mercy.mover.position[2], z + 1.7)
        self.assertAlmostEqual(self.mercy.mover.velocity[2], 0, delta=0.02)
        self.fly(50, buttons=JUMP)
        self.assertGreater(self.mercy.mover.position[1], y + 2)
        self.assertLess(self.mercy.mover.position[1], y + 4)
        self.assertAlmostEqual(self.mercy.mover.velocity[1], 0, delta=0.02)
        self.fly(65, buttons=CROUCH)
        self.assertGreaterEqual(self.mercy.mover.body.position[1], y - 0.02)
        self.assertAlmostEqual(self.mercy.mover.velocity[1], 0, delta=0.02)

    def test_tracer_pistol_tracks_airborne_mercy_and_takes_blaster_return_fire(self):
        self.mercy.team = self.enemy.team
        ground = tuple(self.mercy.mover.position)
        self.start_flight()
        self.fly(27, buttons=JUMP)
        self.fly(32)
        self.assertGreater(self.mercy.mover.position[1], ground[1] + 2)
        target = next(t for t in self.fight._targets() if t.owner is self.mercy)
        health = self.fight.vitals_of(target)
        yaw, pitch = aim(self.fight._eye(self.tracer), (ground[0], ground[1] + 1, ground[2]))
        self.run.step({self.tracer: (yaw, pitch, FIRE)})
        self.assertEqual(health.total, 200)
        self.fly(15)
        self.run.step({self.tracer: (*self.aim_at(self.tracer, self.mercy), FIRE)})
        self.assertEqual(health.total, 188)

        self.run.frame += 1
        self.match.update(self.run.frame * combat.TICK, self.run.frame)
        self.mercy.take_commands([Command(self.run.frame, action=2)])
        self.step(35)
        before = self.health.total
        self.step(20, staff=FIRE)
        self.step(16)
        self.assertLess(self.health.total, before)
        blaster = next(i for i in self.mercy.body_script.component.instances.values()
                       if i.graph.index == 0x006D)
        self.assertEqual(blaster.find_var(expr.INSTANCE, 53).value(), 20)
        self.assertGreater(self.mercy.mover.position[1], ground[1] + 2)


    def chain_anchor(self):
        if graph.effect_script(mercy_effects.CHAIN_HEAL) is None:
            self.skipTest("requires extracted Mercy effect bindings")
        anchor = self.match.add_player(4, "Anchor", SOLDIER, 0, False)
        in_world(self.match, anchor)
        self.run.players.append(anchor)
        self.assertTrue(self.match.switch_hero(anchor, heroes()[SOLDIER]))
        anchor.team = self.mercy.team
        x, y, z = self.tracer.mover.position
        self.place(anchor, (x + 3, y, z))
        self.step(15, target=anchor)
        return anchor

    def test_valkyrie_heals_tracer_through_10dc_and_release_removes_children(self):
        anchor = self.chain_anchor()
        anchor_health = self.fight.vitals_of(mercy.body_target(self.fight, anchor.body))
        anchor_health.damage(80)
        self.health.damage(80)
        self.step(15, staff=FIRE, target=anchor)
        self.assertEqual(self.health.total, 70)
        self.step(3, target=anchor)
        self.start_flight()
        self.step(45, target=anchor)
        self.step(50, staff=FIRE, target=anchor)
        self.assertAlmostEqual(self.health.total, 118, places=4)
        self.assertAlmostEqual(anchor_health.total, 181.2, places=4)
        attachments = self.fight.mercy_effects.attachments.values()
        child, = (item for item in attachments if item.definition == mercy_effects.CHAIN_HEAL)
        self.assertEqual(child.instance.graph.index, 0x10DC)
        self.assertEqual(child.target, self.tracer.body)
        self.assertEqual(child.instance.find_var(expr.INSTANCE, 40).value(), expr.Entity(anchor.body))
        self.assertEqual(child.instance.find_var(expr.INSTANCE, 42).value(), expr.Entity(self.mercy.body))
        self.assertEqual(child.instance.find_var(expr.INSTANCE, 499).value(), 60)
        first = snapshot_digest(export_component_vm(child.component))
        self.assertEqual(snapshot_digest(export_component_vm(child.component)), first)
        self.step(10, target=anchor)
        self.assertAlmostEqual(self.health.total, 118, places=4)
        self.assertFalse(self.fight.mercy_effects.attachments)

    def test_valkyrie_boosts_tracer_through_10dd_until_anchor_loses_los(self):
        anchor = self.chain_anchor()
        self.start_flight()
        self.step(45, target=anchor)
        self.step(12, staff=SECONDARY, target=anchor)
        self.step(staff=SECONDARY, action=FIRE, target=anchor)
        self.assertAlmostEqual(self.enemy_health.total, 184.4, places=4)
        child, = (item for item in self.fight.mercy_effects.attachments.values()
                  if item.definition == mercy_effects.CHAIN_BOOST)
        self.assertEqual(child.instance.graph.index, 0x10DD)
        self.assertEqual(child.target, self.tracer.body)
        self.assertTrue(child.instance.state(4).active)
        x, y, z = self.tracer.mover.position
        floor = box(x - 10, x + 10, y - 1, y, z - 10, z + 10)
        self.match.collision_world = World(floor + wall(x + 1.5, z - 0.8, x + 1.5, z + 0.8, y, y + 5))
        self.step(12, staff=SECONDARY, target=anchor)
        self.step(staff=SECONDARY, action=FIRE, target=anchor)
        self.assertAlmostEqual(self.enemy_health.total, 172.4, places=4)
        self.assertEqual({item.definition for item in self.fight.mercy_effects.attachments.values()},
                         {mercy_effects.BOOST})


    def test_tracer_blinks_out_of_chain_heal_range_and_can_reenter(self):
        anchor = self.chain_anchor()
        x, y, z = self.tracer.mover.position
        self.place(self.tracer, (x - 6, y, z))
        self.health.damage(60)
        self.start_flight()
        self.step(45, target=anchor)
        self.step(10, staff=FIRE, target=anchor)
        self.assertAlmostEqual(self.health.total, 99.6, places=4)
        beam = (*self.aim_at(self.mercy, anchor), FIRE)
        self.run.step({self.mercy: beam, self.tracer: (-16384, 0, SECONDARY)})
        self.assertAlmostEqual(self.tracer.mover.position[0], x - 13.5, delta=0.05)
        before = self.health.total
        self.step(12, staff=FIRE, target=anchor)
        self.assertEqual(self.health.total, before)
        self.assertEqual({item.definition for item in self.fight.mercy_effects.attachments.values()},
                         {mercy_effects.HEAL})
        self.run.step({self.mercy: beam, self.tracer: (16384, 0, SECONDARY)})
        self.assertAlmostEqual(self.tracer.mover.position[0], x - 6, delta=0.05)
        self.assertAlmostEqual(self.health.total, before + 0.96, places=4)
        self.step(10, staff=FIRE, target=anchor)
        self.assertAlmostEqual(self.health.total, before + 10.56, places=4)

    def test_chain_pulse_keeps_launch_boost_after_staff_release(self):
        anchor = self.chain_anchor()
        pool = combat.Vitals(self.enemy.body, 1000)
        self.fight.vitals[self.enemy.body] = pool
        script = self.tracer.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        self.start_flight()
        self.step(45, target=anchor)
        self.step(12, staff=SECONDARY, target=anchor)
        self.step(staff=SECONDARY, target=anchor, action=32)
        for _ in range(20):
            if self.fight.pulse_bombs.bombs:
                break
            self.step(staff=SECONDARY, target=anchor)
        self.assertEqual(len(self.fight.pulse_bombs.bombs), 1)
        self.assertAlmostEqual(self.fight.pulse_bombs.bombs[0].blast.amount, 455, places=4)
        self.step(90, target=anchor)
        self.assertFalse(self.fight.mercy_effects.attachments)
        self.assertAlmostEqual(pool.total, 538.5, places=4)

    def test_idle_valkyrie_expiry_retires_chain_before_the_next_tracer_shot(self):
        anchor = self.chain_anchor()
        self.start_flight()
        self.step(45, target=anchor)
        self.step(12, staff=SECONDARY, target=anchor)
        self.assertEqual({item.definition for item in self.fight.mercy_effects.attachments.values()},
                         {mercy_effects.BOOST, mercy_effects.CHAIN_BOOST})
        owner_frame = self.mercy.body_script.frame
        self.run.frame += 950
        self.match.update(self.run.frame * combat.TICK, self.run.frame)
        self.assertEqual(self.mercy.body_script.frame, owner_frame)
        self.assertEqual({item.definition for item in self.fight.mercy_effects.attachments.values()},
                         {mercy_effects.BOOST})
        yaw, pitch = self.aim_at(self.tracer, self.enemy)
        self.tracer.take_commands([Command(self.run.frame, yaw=yaw, pitch=pitch, buttons=FIRE)])
        self.assertEqual(self.enemy_health.total, 188)
