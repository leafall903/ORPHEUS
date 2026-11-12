"""Actual Tracer primary fire against Reinhardt's authored 0095 barrier input."""
import os
import unittest
from dataclasses import replace

import test_game_tracer_hammer as hammer_tests
import test_game_tracer_pulse_deflect as support_tests
from test_game_combat import aim
from test_game_mover import box

from ow174.game import combat, stats
from ow174.game.collision import StaticCollisionProvider, World
from ow174.game.commands import FIRE, SECONDARY, Command
from ow174.game.script.driver import ULT_COST


@unittest.skipUnless(os.environ.get("ORPHEUS_REINHARDT_COLLISION"), "external barrier mesh required")
class TracerReinhardtBarrierTests(unittest.TestCase):
    def setUp(self):
        fixture = hammer_tests.TracerHammerTests()
        fixture.setUp()
        self.run, self.rein, self.tracer = fixture.run, fixture.rein, fixture.target
        self.fight = fixture.fight
        self.manager = self.fight.reinhardt_barriers
        self.vitals = self.fight.vitals_of(next(t for t in self.fight._targets() if t.owner is self.rein))
        for _ in range(18):
            self.run.step({self.rein: (-32768, 0, 2)})
        self.state = self.manager.states[self.rein.body]
        self.assertTrue(self.state.active)

    def shoot(self, barrier=2):
        x, y, z = self.rein.mover.position
        yaw, pitch = aim(self.fight._eye(self.tracer), (x, y + 1, z))
        self.run.step({self.rein: (-32768, 0, barrier), self.tracer: (yaw, pitch, FIRE)})

    def test_front_pistols_damage_only_the_actual_raised_barrier(self):
        self.assertEqual(self.state.current, 1600)
        self.shoot()
        self.assertEqual(self.vitals.total, 500)
        self.assertEqual(self.state.current, 1588)
        self.assertEqual(self.manager.instance(self.rein).find_var(0, 586).value(), 1588)
        self.assertEqual(self.tracer.client.messages(combat.HIT), [])
        self.assertEqual(self.tracer.stats.value(stats.HERO_DAMAGE), 0)

    def cover(self):
        x, y, z = self.tracer.mover.position
        mesh = World(box(x - 8, x + 8, y - 2, y, z - 8, z + 8)
                     + box(x - 4, x + 4, y, y + 4, z + 0.7, z + 0.8))
        match = self.run.match
        match.collision_provider = StaticCollisionProvider(mesh)
        match.collision_world = mesh
        for actor in self.run.players:
            actor.mover.use(world=mesh)

    def test_static_wall_wins_before_the_raised_barrier(self):
        self.cover()
        self.shoot()
        self.assertEqual(self.state.current, 1600)
        self.assertEqual(self.vitals.total, 500)
        self.assertEqual(self.tracer.client.messages(combat.HIT), [])

    def test_fire_strike_crosses_intact_or_broken_barrier_but_stops_at_static_cover(self):
        for initial_shield, covered in ((1600, False), (40, False), (1600, True)):
            with self.subTest(shield=initial_shield, covered=covered):
                self.setUp()
                match = self.run.match
                match.game_map = replace(match.game_map, free_for_all=False)
                caster, _ = support_tests.PulseDeflectTests().support(
                    self.run, self.tracer, hero=hammer_tests.REINHARDT,
                )
                self.tracer.team = self.rein.team = 0
                caster.team = 1
                x, y, z = self.tracer.mover.position
                caster.mover.position[:] = (x, y, z - 6)
                self.tracer.mover.position[:] = (x, y, z + 7)
                caster.mover.body = self.tracer.mover.body = None
                self.state.current = initial_shield
                if covered:
                    mesh = World(box(x - 20, x + 20, y - 2, y, z - 20, z + 20)
                                 + box(x - 5, x + 5, y, y + 4, z - 2, z - 1.9))
                    match.collision_provider = StaticCollisionProvider(mesh)
                    match.collision_world = mesh
                    for actor in self.run.players:
                        actor.mover.use(world=mesh)
                target = next(t for t in self.fight._targets() if t.owner is self.tracer)
                health = self.fight.vitals_of(target)
                for frame in range(34):
                    self.run.step({self.rein: (-32768, 0, 2), caster: (0, 0, 16 if frame == 0 else 0)})
                strike, = self.fight.rockets
                self.assertEqual(strike.definition, combat.FIRE_STRIKE)
                for _ in range(55):
                    self.run.step({self.rein: (-32768, 0, 2)})
                shield_after = initial_shield if covered else max(0, initial_shield - 100)
                self.assertEqual(self.state.current, shield_after)
                self.assertEqual(self.vitals.total, 500 if covered else 405)
                self.assertEqual(health.total, 150 if covered else 50)
                self.assertEqual(strike.hit_barriers, set() if covered else {self.rein.body})
                expected_bodies = set() if covered else {self.rein.body, self.tracer.body}
                self.assertEqual(strike.hit_entities, expected_bodies)
                if covered:
                    self.assertNotIn(strike, self.fight.rockets)

    def test_earthshatter_stops_at_intact_and_breaking_barrier(self):
        for shield in (1600, 40):
            with self.subTest(shield=shield):
                self.setUp()
                match = self.run.match
                match.game_map = replace(match.game_map, free_for_all=False)
                caster, _ = support_tests.PulseDeflectTests().support(
                    self.run, self.tracer, hero=hammer_tests.REINHARDT,
                )
                self.tracer.team = self.rein.team = 0
                caster.team = 1
                x, y, z = self.tracer.mover.position
                caster.mover.position[:] = (x, y, z - 6)
                self.tracer.mover.position[:] = (x, y, z + 7)
                caster.mover.body = self.tracer.mover.body = None
                self.state.current = shield
                script = caster.body_script
                script.set_ult_charge(script.component.entity_var(ULT_COST).value())
                for frame in range(72):
                    self.run.step({self.rein: (-32768, 0, 2), caster: (0, 0, 32 if frame == 0 else 0)})
                target = next(t for t in self.fight._targets() if t.owner is self.tracer)
                self.assertEqual(self.state.current, max(0, shield - 50))
                self.assertEqual(self.vitals.total, 500)
                self.assertEqual(self.fight.vitals_of(target).total, 150)
                self.assertFalse(self.fight.is_stunned(self.tracer))
                self.assertFalse(self.fight.earthshatters.waves)

    def test_earthshatter_resolves_when_raised_barrier_strafes_into_path(self):
        for late in (False, True):
            with self.subTest(late=late):
                self.setUp()
                self.check_earthshatter_strafe(late)

    def check_earthshatter_strafe(self, late):
        run = self.run
        match = run.match
        match.game_map = replace(match.game_map, free_for_all=False)
        caster, _ = support_tests.PulseDeflectTests().support(
            run, self.tracer, hero=hammer_tests.REINHARDT,
        )
        self.tracer.team = self.rein.team = 0
        caster.team = 1
        x, y, z = self.tracer.mover.position
        caster.mover.position[:] = (x, y, z - 4)
        self.rein.mover.position[:] = (x + 4.3, y, z)
        self.tracer.mover.position[:] = (x, y, z + 5)
        caster.mover.body = self.rein.mover.body = self.tracer.mover.body = None
        target = next(t for t in self.fight._targets() if t.owner is self.tracer)
        health = self.fight.vitals_of(target)
        ray_start = (x, y + 0.1, z - 4)
        self.assertIsNone(self.manager.first(caster, ray_start, (0, 0, 1), 9))
        script = caster.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())

        wave = None
        central_contact = False
        previous_x = self.rein.mover.position[0]
        for index in range(86):
            run.frame += 1
            tick = run.frame
            match.update(tick * combat.TICK, tick)
            wave = self.fight.earthshatters.waves.get(caster.body) or wave
            current_x = self.rein.mover.position[0]
            if wave and not central_contact and wave.fronts[len(wave.fronts) // 2] is None:
                self.assertLess(current_x, previous_x)
                self.assertTrue(self.state.active)
                central_contact = True
            previous_x = current_x
            for actor in run.players:
                actor.client.queue_entities(match.remote_movements(actor))
            caster.take_commands([Command(tick, buttons=32 if index == 0 else 0)])
            self.tracer.take_commands([Command(tick)])
            strafe = health.total < 150 if late else index < 49
            right = -127 if strafe else 0
            self.rein.take_commands([Command(tick, right=right, yaw=-32768, buttons=SECONDARY)])

        self.assertIsNotNone(wave)
        self.assertTrue(self.state.active)
        self.assertLess(self.rein.mover.position[0], x + 4.3)
        self.assertIsNotNone(self.manager.first(caster, ray_start, (0, 0, 1), 9))
        # The late shield can still catch off-axis fronts. The central front
        # has already passed the shield's plane by the time it enters its path.
        self.assertEqual(self.state.current, 1550)
        self.assertIn(self.rein.body, wave.hit_barriers)
        self.assertEqual(health.total, 100 if late else 150)
        self.assertEqual(self.fight.is_stunned(self.tracer), late)
        self.assertEqual(central_contact, not late)
        self.assertNotIn(caster.body, self.fight.earthshatters.waves)

    def test_release_waits_for_regeneration_then_raise_intercepts_again(self):
        self.shoot()
        self.run.step({})
        self.assertFalse(self.state.active)
        self.assertGreater(self.state.regen_after, self.fight.now)
        self.assertEqual(self.state.current, 1588)
        for _ in range(300):
            if self.state.current == 1600:
                break
            self.run.step({})
        self.assertEqual(self.state.current, 1600)
        for _ in range(18):
            self.run.step({self.rein: (-32768, 0, 2)})
        self.assertTrue(self.state.active)
        self.shoot()
        self.assertEqual(self.state.current, 1588)

    def test_rear_pistols_hit_owner_before_barrier(self):
        x, y, z = self.rein.mover.position
        self.tracer.mover.position = [x, y, z + 3]
        self.tracer.mover.body = None
        for _ in range(2):
            self.run.step({self.rein: (-32768, 0, 2)})
        self.shoot()
        self.assertEqual(self.vitals.total, 494)
        self.assertEqual(self.state.current, 1600)
        self.assertEqual(len(self.tracer.client.messages(combat.HIT)), 2)

    def test_first_pellet_breaks_barrier_second_reaches_armour(self):
        self.state.current = 6
        self.shoot()
        self.assertFalse(self.state.active)
        self.assertEqual(self.state.current, 0)
        self.assertEqual(self.vitals.total, 497)
        self.assertEqual(len(self.tracer.client.messages(combat.HIT)), 1)
        for _ in range(10):
            self.run.step({self.rein: (-32768, 0, 2)})
        self.assertFalse(self.state.active)
        self.assertGreater(self.state.broken_until, self.fight.now)


    def melee(self, distance):
        x, y, z = self.tracer.mover.position
        self.rein.mover.position = [x, y, z + distance]
        self.rein.mover.body = None
        self.run.step({self.rein: (-32768, 0, 2)})
        for frame in range(40):
            self.run.step({self.rein: (-32768, 0, 2),
                           self.tracer: (0, 0, 0x800 if frame == 0 else 0)})

    def test_melee_contacts_mesh_once_without_reaching_the_body_at_four_metres(self):
        self.melee(4)
        self.assertEqual(self.state.current, 1570)
        self.assertEqual(self.vitals.total, 500)
        self.assertFalse(self.tracer.client.messages(combat.HIT))
        self.assertEqual(self.tracer.stats.value(stats.HERO_DAMAGE), 0)

    def test_melee_can_reach_the_shield_and_body_in_one_activation(self):
        self.melee(3)
        self.assertEqual(self.state.current, 1570)
        self.assertEqual(self.vitals.total, 475)
        self.assertEqual(len(self.tracer.client.messages(combat.HIT)), 1)

    def test_melee_shield_contact_obeys_wall_and_team(self):
        for condition in ("wall", "ally"):
            with self.subTest(condition=condition):
                self.setUp()
                match = self.run.match
                if condition == "wall":
                    self.cover()
                else:
                    match.game_map = replace(match.game_map, free_for_all=False)
                    self.rein.team = self.tracer.team
                self.melee(3)
                self.assertEqual(self.state.current, 1600)
                self.assertEqual(self.vitals.total, 500)


    def launch_pulse(self, pitch=0):
        script = self.tracer.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        manager = self.fight.pulse_bombs
        for index in range(40):
            self.run.step({self.rein: (-32768, 0, 2),
                           self.tracer: (0, pitch, 32 if index == 0 else 0)})
            if manager.bombs and manager.bombs[0].stuck:
                return manager.bombs[0]
        self.fail("actual Pulse did not reach the raised shield")

    def test_batched_reinhardt_shield_uses_the_contact_frame(self):
        for lower_after in (False, True):
            for batched in (False, True):
                with self.subTest(lower_after=lower_after, batched=batched):
                    self.setUp()
                    x, y, z = self.tracer.mover.position
                    self.rein.mover.position = [x, y, z + 5]
                    self.rein.mover.body = None
                    initial_yaw = -32768 if lower_after else 0
                    script = self.tracer.body_script
                    script.set_ult_charge(script.component.entity_var(ULT_COST).value())
                    for offset in range(20):
                        self.run.step({self.rein: (initial_yaw, 0, SECONDARY),
                                       self.tracer: (0, 0, 32 if offset == 0 else 0)})
                        if self.fight.pulse_bombs.bombs:
                            break
                    bomb, = self.fight.pulse_bombs.bombs
                    for _ in range(3):
                        self.run.step({self.rein: (initial_yaw, 0, SECONDARY)})
                    self.assertFalse(bomb.stuck)
                    commands = [
                        Command(self.run.frame + offset, yaw=initial_yaw if offset < 22 else -32768,
                                buttons=0 if lower_after and offset >= 22 else SECONDARY)
                        for offset in range(1, 31)
                    ]
                    if batched:
                        self.rein.take_commands(commands)
                        self.run.frame += len(commands)
                        self.run.match.update(self.run.frame * combat.TICK, self.run.frame)
                    else:
                        for command in commands:
                            self.run.step({self.rein: (command.yaw, command.pitch, command.buttons)})
                    self.assertTrue(bomb.stuck)
                    self.assertIsNone(bomb.attached_shield)
                    self.assertEqual(bomb.attached, None if lower_after else self.rein.body)
                    self.assertEqual(self.state.current, 1595 if lower_after else 1600)
                    self.assertEqual(self.vitals.total, 500 if lower_after else 497.5)

    def test_pulse_sticks_to_raised_shield_follows_pose_and_blast_spends_shield_once(self):
        bomb = self.launch_pulse()
        self.assertEqual(bomb.attached_shield, self.rein.body)
        self.assertIsNone(bomb.attached)
        self.assertEqual(self.state.current, 1595)
        self.assertEqual(self.vitals.total, 500)
        before = bomb.position
        self.rein.mover.position[0] += 1
        self.run.step({self.rein: (-32768, 0, 2)})
        self.assertAlmostEqual(bomb.position[0] - before[0], 1)
        for _ in range(70):
            self.run.step({self.rein: (-32768, 0, 2)})
        self.assertFalse(self.fight.pulse_bombs.bombs)
        self.assertEqual(self.state.current, 1245)
        self.assertEqual(self.vitals.total, 500)

    def test_lowering_shield_detaches_pulse_and_exposes_the_body_to_the_blast(self):
        bomb = self.launch_pulse()
        self.assertEqual(bomb.attached_shield, self.rein.body)
        self.run.step({})
        self.run.step({})
        self.assertIsNone(bomb.attached_shield)
        self.assertTrue(bomb.stuck)
        for _ in range(70):
            self.run.step({})
        self.assertFalse(self.fight.pulse_bombs.bombs)
        self.assertLess(self.vitals.total, 500)


    def test_ground_pulse_damages_the_nearby_shield_without_attachment(self):
        bomb = self.launch_pulse(pitch=8192)
        self.assertIsNone(bomb.attached_shield)
        self.assertIsNone(bomb.attached)
        self.assertEqual(self.state.current, 1600)
        for _ in range(70):
            self.run.step({self.rein: (-32768, 0, 2)})
        self.assertFalse(self.fight.pulse_bombs.bombs)
        self.assertEqual(self.state.current, 1250)
        self.assertEqual(self.vitals.total, 500)

    def test_wall_contact_precedes_shield_and_blocks_the_blast(self):
        self.cover()
        bomb = self.launch_pulse()
        self.assertIsNone(bomb.attached_shield)
        self.assertIsNone(bomb.attached)
        for _ in range(70):
            self.run.step({self.rein: (-32768, 0, 2)})
        self.assertFalse(self.fight.pulse_bombs.bombs)
        self.assertEqual(self.state.current, 1600)
        self.assertEqual(self.vitals.total, 500)
