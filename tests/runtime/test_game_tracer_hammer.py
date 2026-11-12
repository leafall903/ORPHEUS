"""Actual 0048 hammer timing and reconstructed sweep against Tracer."""
import unittest
from dataclasses import replace

import test_game_tracer as tracer_tests
import test_game_tracer_dva_mech as mech_tests
import test_game_tracer_pulse_deflect as support_tests
import test_game_tracer_winston_barrier as barrier_tests
from test_game_combat import Run, aim
from test_game_mover import box

from ow174.game import combat, deflection, tracer
from ow174.game.collision import StaticCollisionProvider, World
from ow174.game.commands import FIRE, JUMP, Command
from ow174.game.content import find_hero, heroes

REINHARDT = 0x02E0000000000007


class TracerHammerTests(unittest.TestCase):
    def setUp(self):
        self.run, self.target, self.rein, _ = tracer_tests.TracerPistolTests().fight()
        self.match = self.run.match
        self.match.switch_hero(self.rein, heroes()[REINHARDT])
        self.fight = self.match.combat
        self.vitals = self.fight.vitals_of(next(t for t in self.fight._targets() if t.owner is self.target))
        self.place(3)
        for _ in range(30):
            self.run.step({})

    def place(self, distance):
        x, y, z = self.target.mover.position
        self.rein.mover.position = [x, y, z + distance]
        self.rein.mover.body = None

    def swing(self, frames, buttons=FIRE, yaw=-32768):
        for _ in range(frames):
            self.run.step({self.rein: (yaw, 0, buttons)})

    def test_actual_windup_and_overlapping_queries_damage_once(self):
        self.swing(20)
        self.assertEqual(self.vitals.total, 150)
        self.swing(20)
        self.assertEqual(self.vitals.total, 65)
        self.fight.command(self.rein, Command(self.run.frame, yaw=-32768, buttons=FIRE))
        self.swing(12)
        self.assertEqual(self.vitals.total, 65)
        self.assertEqual([hit["+0x78"]["+0x4"] for hit in self.rein.client.messages(combat.HIT)], [85])

    def test_fire_strike_releases_then_pierces_two_bodies_and_respects_recall(self):
        for recall in (False, True):
            with self.subTest(recall=recall):
                self.setUp()
                self.place(6)
                x, y, z = self.target.mover.position
                rear, _ = support_tests.PulseDeflectTests().support(self.run, self.target)
                rear.mover.position[:] = (x, y, z - 3)
                rear.mover.body = None
                rear_target = next(t for t in self.fight._targets() if t.owner is rear)
                rear_health = self.fight.vitals_of(rear_target)
                self.target.mover.position[0] += 0.9
                self.target.mover.body = None
                self.swing(30, buttons=16)
                self.assertFalse(self.fight.rockets)
                self.assertEqual(self.vitals.total, 150)
                self.swing(4, buttons=0)
                strike, = self.fight.rockets
                self.assertEqual(strike.definition, combat.FIRE_STRIKE)
                self.assertEqual(strike.speed, 25)
                self.assertAlmostEqual(strike.body_radius, 0.85)
                if recall:
                    self.run.step({self.target: (0, 0, 16)})
                    self.assertTrue(tracer.recalling(self.target))
                self.swing(65, buttons=0)
                self.assertEqual(self.vitals.total, 150 if recall else 50)
                self.assertEqual(rear_health.total, 100)
                expected = {rear.body} if recall else {self.target.body, rear.body}
                self.assertEqual(strike.hit_entities, expected)
                self.swing(35, buttons=16)
                self.assertEqual(self.vitals.total, 150 if recall else 50)
                self.assertEqual(rear_health.total, 100)
                self.assertFalse(any(shot is not strike for shot in self.fight.rockets))
                self.assertIsNotNone(self.rein.body_script)

    def test_fire_strike_stops_at_static_cover_and_passes_allied_tracer(self):
        for covered in (False, True):
            with self.subTest(covered=covered):
                self.setUp()
                self.place(6)
                x, y, z = self.target.mover.position
                rear, _ = support_tests.PulseDeflectTests().support(self.run, self.target)
                rear.mover.position[:] = (x, y, z - 4)
                rear.mover.body = None
                self.match.game_map = replace(self.match.game_map, free_for_all=False)
                self.rein.team, rear.team = 0, 1
                self.target.team = 1 if covered else self.rein.team
                if covered:
                    world = World(box(x - 20, x + 20, y - 2, y, z - 20, z + 20)
                                  + box(x - 5, x + 5, y, y + 5, z + 2, z + 2.1))
                    self.match.collision_provider = StaticCollisionProvider(world)
                    self.match.collision_world = world
                    for player in self.run.players:
                        player.mover.use(world=world)
                health = barrier_tests.TracerWinstonBarrierTests.health
                self.swing(34, buttons=16)
                strike, = self.fight.rockets
                self.assertEqual(strike.definition, combat.FIRE_STRIKE)
                self.swing(60, buttons=0)
                self.assertEqual(self.vitals.total, 150)
                self.assertEqual(health(self.run, rear).total, 200 if covered else 100)
                self.assertEqual(strike.hit_entities, set() if covered else {rear.body})
                if covered:
                    self.assertNotIn(strike, self.fight.rockets)

    def test_actual_pistol_kill_cancels_fire_strike_only_before_release(self):
        for released in (False, True):
            with self.subTest(released=released):
                self.setUp()
                self.place(6)
                target = next(t for t in self.fight._targets() if t.owner is self.rein)
                health = self.fight.vitals_of(target)
                # Seed a wounded caster so the actual two-pistol input is lethal.
                health.now.update({combat.HEALTH_POOL: 8, combat.ARMOUR_POOL: 0, combat.SHIELDS_POOL: 0})
                self.swing(34 if released else 12, buttons=16)
                strike = self.fight.rockets[0] if released else None
                self.assertEqual(len(self.fight.rockets), int(released))
                self.assertEqual(self.vitals.total, 150)
                x, y, z = self.rein.mover.position
                facing = aim(self.fight._eye(self.target), (x, y + 1, z))
                self.run.step({self.target: (*facing, FIRE)})
                self.assertTrue(health.dead)
                self.assertEqual(len(self.target.client.messages(combat.KILL_NOTICE)), 1)
                self.swing(80, buttons=0)
                self.assertEqual(self.vitals.total, 50 if released else 150)
                if released:
                    self.assertEqual(strike.hit_entities, {self.target.body})
                    self.assertEqual(len(self.rein.client.messages(combat.HIT)), 1)
                else:
                    self.assertFalse(self.fight.rockets)

    def test_fire_strike_cooldown_rejects_early_press_and_allows_fresh_lethal_cast(self):
        self.place(6)
        component = self.rein.body_script.component
        graph = next(i for i in component.instances.values() if i.graph.index == 0xE2)
        ability = graph.state(9)
        self.swing(34, buttons=16)
        first, = self.fight.rockets
        self.swing(60, buttons=0)
        self.assertEqual(self.vitals.total, 50)
        self.assertGreater(ability.remaining(), 1)
        self.swing(1, buttons=16)
        for _ in range(450):
            self.assertFalse(any(rocket is not first for rocket in self.fight.rockets))
            if ability.remaining() == 0:
                break
            self.swing(1, buttons=0)
        self.assertEqual(ability.remaining(), 0)
        self.assertFalse(self.fight.rockets)
        self.assertEqual(self.vitals.total, 50)

        self.swing(34, buttons=16)
        second, = self.fight.rockets
        self.assertIsNot(first, second)
        self.swing(60, buttons=0)
        self.assertTrue(self.vitals.dead)
        self.assertEqual(len(self.rein.client.messages(combat.KILL_NOTICE)), 1)

    def defend_fire_strike(self, hero, button):
        self.match.game_map = replace(self.match.game_map, free_for_all=False)
        self.target.team, self.rein.team = 0, 1
        self.place(12)
        defender, _ = support_tests.PulseDeflectTests().support(self.run, self.target, hero=hero)
        if hero == 0x02E000000000007A:
            mech_tests.TracerDvaMechTests.setUpClass()
            self.assertTrue(self.match.switch_hero(defender, replace(find_hero("dva"), body=mech_tests.MECH)))
        defender.team = self.target.team
        x, y, z = self.target.mover.position
        defender.mover.position[:] = (x, y, z + 4)
        defender.mover.body = None
        for _ in range(110 if hero == 0x02E000000000007A else 25):
            self.run.step({})
        self.swing(34, buttons=16)
        strike, = self.fight.rockets
        self.assertIs(strike.shooter, self.rein)
        defender_target = next(t for t in self.fight._targets() if t.owner is defender)
        health = self.fight.vitals_of(defender_target)
        before = health.total
        for frame in range(65):
            self.run.step({defender: (0, 0, button if frame == 0 or button == 2 else 0)})
        self.assertEqual(self.vitals.total, 150)
        self.assertEqual(health.total, before)
        self.assertFalse(self.rein.client.messages(combat.HIT))
        return defender, strike

    @unittest.skipUnless(deflection.TAGS_PATH, "requires verified projectile definition tags")
    def test_actual_deflect_returns_fire_strike_to_reinhardt(self):
        genji, strike = self.defend_fire_strike(0x02E0000000000029, 16)
        self.assertIs(strike.shooter, genji)
        self.assertEqual(strike.team, genji.team)
        self.assertIsNone(strike.shot)
        self.assertEqual(strike.hit_entities, {self.rein.body})
        target = next(t for t in self.fight._targets() if t.owner is self.rein)
        self.assertEqual(self.fight.vitals_of(target).total, 405)
        self.assertEqual(len(genji.client.messages(combat.HIT)), 1)

    @unittest.skipUnless(deflection.TAGS_PATH, "requires verified projectile definition tags")
    def test_actual_matrix_absorbs_fire_strike_before_tracer(self):
        _, strike = self.defend_fire_strike(0x02E000000000007A, 2)
        self.assertNotIn(strike, self.fight.rockets)
        self.assertFalse(strike.hit_entities)

    @unittest.skipUnless(deflection.TAGS_PATH, "requires verified projectile definition tags")
    def test_actual_grasp_absorbs_fire_strike_before_tracer(self):
        sigma, strike = self.defend_fire_strike(0x02E000000000023B, 8)
        self.assertNotIn(strike, self.fight.rockets)
        self.assertFalse(strike.hit_entities)
        grasp = next(i for i in sigma.body_script.component.instances.values() if i.graph.index == 0x1D02)
        self.assertEqual(grasp.vars[21312].value(), 100)

    @unittest.skipUnless(deflection.TAGS_PATH, "requires verified projectile definition tags")
    def test_two_deflects_keep_fire_strike_lifetime_and_body_hit_history(self):
        run = Run(names=("Reinhardt", "First Genji", "Second Genji", "Tracer", "First target"))
        match = run.match
        fight = match.combat
        match.bots.clear()
        match._pending_bot_scripts.clear()
        match.game_map = replace(match.game_map, free_for_all=False)
        caster, first, second, target, first_target = run.players
        for actor, hero in ((caster, REINHARDT), (first, 0x02E0000000000029),
                            (second, 0x02E0000000000029), (target, tracer_tests.TRACER)):
            self.assertTrue(match.switch_hero(actor, heroes()[hero]))
        caster.team = second.team = 0
        first.team = target.team = first_target.team = 1
        for actor, z in zip(run.players, (-10, 5, -4, 9, 0), strict=True):
            barrier_tests.TracerWinstonBarrierTests.move(actor, (0, 1, z))
        for _ in range(25):
            run.step({})

        for frame in range(34):
            run.step({caster: (0, 0, 16 if frame == 0 else 0)})
        strike, = fight.rockets
        self.assertEqual(strike.definition, combat.FIRE_STRIKE)
        lifetime = strike.left + strike.flown
        run.step({first: (-32768, 0, 16)})
        for _ in range(55):
            if strike.shooter is first:
                break
            run.step({first: (-32768, 0, 0)})
        self.assertIs(strike.shooter, first)
        self.assertEqual(strike.team, first.team)
        self.assertEqual(strike.hit_entities, {first_target.body})
        self.assertAlmostEqual(strike.left + strike.flown, lifetime)
        left_after_first = strike.left

        run.step({second: (0, 0, 16)})
        for _ in range(55):
            if strike.shooter is second:
                break
            run.step({})
        self.assertIs(strike.shooter, second)
        self.assertEqual(strike.team, second.team)
        self.assertLess(strike.left, left_after_first)
        self.assertAlmostEqual(strike.left + strike.flown, lifetime)
        self.assertEqual(strike.hit_entities, {first_target.body})

        for _ in range(260):
            if strike not in fight.rockets:
                break
            run.step({})
        self.assertNotIn(strike, fight.rockets)
        self.assertAlmostEqual(strike.flown, lifetime)
        self.assertEqual(strike.hit_entities, {first_target.body, first.body, target.body})
        health = barrier_tests.TracerWinstonBarrierTests.health
        self.assertEqual([health(run, actor).total for actor in run.players], [500, 100, 200, 50, 100])
        hit_counts = [len(actor.client.messages(combat.HIT)) for actor in (caster, first, second)]
        self.assertEqual(hit_counts, [1, 0, 2])

    def test_grounded_earthshatter_damage_stun_and_target_gates(self):
        for gate in ("hit", "close", "recall", "ally", "range", "wall"):
            with self.subTest(gate=gate):
                self.setUp()
                self.place({"close": 3, "range": 22}.get(gate, 6))
                if gate == "ally":
                    self.match.game_map = replace(self.match.game_map, free_for_all=False)
                    self.rein.team = self.target.team
                if gate == "wall":
                    x, y, z = self.target.mover.position
                    world = World(box(x - 30, x + 30, y - 2, y, z - 30, z + 30)
                                  + box(x - 5, x + 5, y, y + 4, z + 2, z + 2.1))
                    self.match.collision_provider = StaticCollisionProvider(world)
                    self.match.collision_world = world
                    for actor in self.run.players:
                        actor.mover.use(world=world)
                script = self.rein.body_script
                script.ult_seconds = 0
                self.swing(35, buttons=32)
                self.assertFalse(self.fight.earthshatters.waves)
                self.assertEqual(self.vitals.total, 150)
                self.swing(1, buttons=0)
                script.set_ult_charge(script.component.entity_var(338).value())
                self.swing(1, buttons=32)
                self.assertEqual(script.component.entity_var(342).value(), 0)
                if gate == "recall":
                    self.run.step({self.target: (0, 0, 16)})
                    self.assertTrue(tracer.recalling(self.target))
                self.swing(25, buttons=0)
                self.assertEqual(self.vitals.total, 150)
                self.swing(10, buttons=0)
                wave, = self.fight.earthshatters.waves.values()
                self.assertEqual((wave.speed, wave.reach), (40, 20))
                self.assertEqual((wave.direct.amount, wave.near.amount), (50, 250))
                self.swing(40, buttons=0)
                self.assertFalse(self.fight.earthshatters.waves)
                self.assertEqual(self.vitals.total, {"hit": 100, "close": 0}.get(gate, 150))
                self.assertEqual(self.fight.is_stunned(self.target), gate == "hit")
                if gate == "hit":
                    deadline = self.fight.stuns[self.target.body]
                    self.run.step({self.target: (0, 0, 16 | 8 | FIRE)})
                    self.assertFalse(tracer.recalling(self.target))
                    self.assertFalse(self.target.client.messages(combat.HIT))
                    while self.fight.tick < deadline:
                        self.swing(1, buttons=0)
                    self.assertFalse(self.fight.is_stunned(self.target))
                    self.run.step({self.target: (0, 0, 16)})
                    self.assertTrue(tracer.recalling(self.target))

    def test_earthshatter_crosses_small_steps_but_stops_at_discontinuities(self):
        for rise, gap, ramp, reaches in (
            (0.5, 0, 0, True),
            (-0.5, 0, 0, True),
            (3, 0, 0, False),
            (-3, 0, 0, False),
            (0, 3, 0, False),
            (1.5, 0, 2, True),
            (1.5, 0, 0.75, False),
        ):
            with self.subTest(rise=rise, gap=gap, ramp=ramp):
                self.setUp()
                self.check_earthshatter_terrain(rise, gap, ramp, reaches)

    def check_earthshatter_terrain(self, rise, gap, ramp, reaches):
        self.place(10)
        x, y, z = self.target.mover.position
        far_level = y + rise
        near_edge, far_edge = z + 5 + ramp, z + 5 - gap
        left, right = x - 30, x + 30
        triangles = (
            box(left, right, y - 6, y, near_edge, z + 32)
            + box(left, right, y - 6, far_level, z - 26, far_edge)
        )
        if ramp:
            triangles.extend((
                (left, y, near_edge, right, y, near_edge, right, far_level, far_edge),
                (left, y, near_edge, right, far_level, far_edge, left, far_level, far_edge),
            ))
        world = World(triangles)
        self.match.collision_world = world
        self.match.collision_provider = StaticCollisionProvider(world)
        for actor in self.run.players:
            actor.mover.use(world=world)
        self.target.mover.position[1] = far_level
        self.target.mover.body = None
        self.swing(16, buttons=0)
        self.assertFalse(self.target.mover.airborne)
        self.assertAlmostEqual(self.target.mover.position[1], far_level)

        script = self.rein.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        self.swing(1, buttons=32)
        self.assertEqual(script.component.entity_var(342).value(), 0)
        for _ in range(75):
            self.swing(1, buttons=0)
            wave = self.fight.earthshatters.waves.get(self.rein.body)
            if wave is not None and 7 < wave.travelled < 9:
                break

        self.assertIsNotNone(wave)
        self.assertGreater(wave.travelled, 7)
        centre = len(wave.fronts) // 2
        if reaches:
            self.assertIsNotNone(wave.fronts[centre])
            self.assertAlmostEqual(wave.fronts[centre][1], far_level)
        else:
            self.assertIsNone(wave.fronts[centre])
        self.assertEqual(self.vitals.total, 150)
        self.swing(40, buttons=0)
        self.assertEqual(self.vitals.total, 100 if reaches else 150)
        self.assertEqual(self.fight.is_stunned(self.target), reaches)
        self.assertEqual(self.target.body in wave.hit_bodies, reaches)
        self.assertNotIn(self.rein.body, self.fight.earthshatters.waves)

    def test_tracer_blink_into_earthshatter_path_before_or_after_front_passes(self):
        for late in (False, True):
            with self.subTest(late=late):
                self.setUp()
                self.place(10)
                x, _, _ = self.target.mover.position
                self.target.mover.position[0] += 7.5
                self.target.mover.body = None
                self.swing(8, buttons=0)
                script = self.rein.body_script
                script.set_ult_charge(script.component.entity_var(338).value())
                self.swing(1, buttons=32)
                threshold = 12 if late else 6
                for _ in range(68):
                    self.swing(1, buttons=0)
                    wave = self.fight.earthshatters.waves.get(self.rein.body)
                    if wave is not None and wave.travelled >= threshold:
                        break

                self.assertIsNotNone(wave)
                self.assertGreaterEqual(wave.travelled, threshold)
                self.assertEqual(self.vitals.total, 150)
                self.run.step({self.target: (-16384, 0, 8), self.rein: (-32768, 0, 0)})
                self.assertAlmostEqual(self.target.mover.position[0], x, delta=0.6)
                self.swing(42, buttons=0)
                self.assertEqual(self.vitals.total, 150 if late else 100)
                self.assertEqual(self.fight.is_stunned(self.target), not late)
                self.assertEqual(self.target.body in wave.hit_bodies, not late)
                self.assertNotIn(self.rein.body, self.fight.earthshatters.waves)

    def test_airborne_earthshatter_spends_charge_and_releases_after_landing(self):
        for press_frame, fall_height in ((3, 0), (25, 0), (3, 6)):
            with self.subTest(press_frame=press_frame, fall_height=fall_height):
                self.setUp()
                self.check_airborne_earthshatter(press_frame, fall_height)

    def check_airborne_earthshatter(self, press_frame, fall_height):
        self.place(6)
        script = self.rein.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        self.rein.mover.position[1] += fall_height
        self.swing(1, buttons=0 if fall_height else JUMP)
        self.swing(press_frame - 1, buttons=0)
        self.assertTrue(self.rein.mover.airborne)
        if fall_height:
            self.assertGreater(self.rein.mover.position[1] - self.target.mover.position[1], 1.5)
            self.assertLess(self.rein.mover.velocity[1], 0)
        self.swing(1, buttons=32)
        self.assertEqual(script.component.entity_var(342).value(), 0)
        instance = next(i for i in script.component.instances.values() if i.graph.index == 0x00BE)
        waiting = instance.states[8]
        self.assertTrue(waiting.active)
        self.assertFalse(self.fight.earthshatters.waves)

        landed_at = None
        wave = None
        for _ in range(105):
            # Movement is sampled before that command advances the script clock.
            pose_sample_at = script.component.now
            self.swing(1, buttons=0)
            if landed_at is None and not self.rein.mover.airborne:
                landed_at = pose_sample_at
            wave = self.fight.earthshatters.waves.get(self.rein.body)
            if wave is not None:
                break
            self.assertEqual(self.vitals.total, 150)
            self.assertFalse(self.fight.is_stunned(self.target))

        self.assertIsNotNone(landed_at)
        self.assertIsNotNone(wave)
        self.assertFalse(waiting.active)
        self.assertGreaterEqual(waiting.ended_at, landed_at)
        self.assertGreaterEqual(waiting.ended_at - waiting.start, 383)
        self.assertEqual(landed_at - waiting.start < 383, press_frame == 25)
        self.assertFalse(self.rein.mover.airborne)
        self.assertAlmostEqual(wave.origin[1], self.rein.mover.position[1])
        self.swing(40, buttons=0)
        self.assertFalse(self.fight.earthshatters.waves)
        self.assertEqual(self.vitals.total, 100)
        self.assertTrue(self.fight.is_stunned(self.target))
        self.assertLess(script.component.entity_var(342).value(), script.component.entity_var(338).value())

    def test_pistol_kill_cancels_airborne_earthshatter_only_before_release(self):
        for phase in ("airborne", "landed", "released"):
            with self.subTest(phase=phase):
                self.setUp()
                self.check_airborne_earthshatter_pistol_kill(phase)

    def check_airborne_earthshatter_pistol_kill(self, phase):
        self.place(6)
        caster = next(t for t in self.fight._targets() if t.owner is self.rein)
        health = self.fight.vitals_of(caster)
        health.now.update({combat.HEALTH_POOL: 8, combat.ARMOUR_POOL: 0, combat.SHIELDS_POOL: 0})
        script = self.rein.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        self.swing(1, buttons=JUMP)
        self.swing(24 if phase == "landed" else 12, buttons=0)
        self.swing(1, buttons=32)
        self.assertTrue(self.rein.mover.airborne)
        self.assertEqual(script.component.entity_var(342).value(), 0)
        if phase == "landed":
            for _ in range(42):
                self.swing(1, buttons=0)
                if not self.rein.mover.airborne:
                    break
            self.assertFalse(self.rein.mover.airborne)
            instance = next(i for i in script.component.instances.values() if i.graph.index == 0x00BE)
            self.assertTrue(instance.states[8].active)
        released = phase == "released"
        if released:
            for _ in range(105):
                self.swing(1, buttons=0)
                if self.fight.earthshatters.waves:
                    break
        self.assertEqual(bool(self.fight.earthshatters.waves), released)
        self.assertEqual(self.vitals.total, 150)
        x, y, z = self.rein.mover.position
        facing = aim(self.fight._eye(self.target), (x, y + 1, z))
        self.run.step({self.target: (*facing, FIRE)})
        self.assertTrue(health.dead)
        self.assertEqual(len(self.target.client.messages(combat.KILL_NOTICE)), 1)

        for _ in range(105):
            self.swing(1, buttons=0)
            if not released:
                self.assertFalse(self.fight.earthshatters.waves)
        self.assertFalse(self.fight.earthshatters.waves)
        self.assertEqual(self.vitals.total, 100 if released else 150)
        self.assertEqual(self.fight.is_stunned(self.target), released)
        self.assertLess(script.component.entity_var(342).value(), script.component.entity_var(338).value())

    def test_earthshatter_stun_interrupts_unreleased_pulse_without_refund(self):
        self.place(6)
        script = self.rein.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        self.swing(1, buttons=32)
        for _ in range(50):
            wave = self.fight.earthshatters.waves.get(self.rein.body)
            if wave is not None and wave.travelled >= 3.2:
                break
            self.swing(1, buttons=0)
        self.assertIsNotNone(wave)
        self.assertLess(wave.travelled, 5)
        self.assertEqual(self.vitals.total, 150)
        pulse_script = self.target.body_script
        pulse_script.ult_seconds = 0
        pulse_script.set_ult_charge(pulse_script.component.entity_var(338).value())
        self.run.step({self.target: (0, 0, 32)})
        self.assertEqual(pulse_script.component.entity_var(342).value(), 0)
        self.assertFalse(self.fight.pulse_bombs.bombs)
        for _ in range(9):
            if self.fight.is_stunned(self.target):
                break
            self.swing(1, buttons=0)
            self.assertFalse(self.fight.pulse_bombs.bombs)
        self.assertTrue(self.fight.is_stunned(self.target))
        self.assertEqual(self.vitals.total, 100)
        deadline = self.fight.stuns[self.target.body]
        while self.fight.tick <= deadline:
            self.swing(1, buttons=0)
            self.assertFalse(self.fight.pulse_bombs.bombs)
        self.assertEqual(pulse_script.component.entity_var(342).value(), 0)

    def test_earthshatter_stun_does_not_cancel_an_attached_pulse(self):
        self.place(6)
        pulse_script = self.target.body_script
        pulse_script.ult_seconds = 0
        pulse_script.set_ult_charge(pulse_script.component.entity_var(338).value())
        self.run.step({self.target: (0, 0, 32)})
        pulse = self.fight.pulse_bombs
        for _ in range(60):
            if pulse.bombs and pulse.bombs[0].stuck:
                break
            self.swing(1, buttons=0)
        bomb, = pulse.bombs
        self.assertEqual(bomb.attached, self.rein.body)
        target = next(t for t in self.fight._targets() if t.owner is self.rein)
        health = self.fight.vitals_of(target)
        after_contact = health.total
        script = self.rein.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        self.swing(1, buttons=32)
        for _ in range(55):
            if self.fight.is_stunned(self.target):
                break
            self.swing(1, buttons=0)
        self.assertTrue(self.fight.is_stunned(self.target))
        self.assertEqual(self.vitals.total, 100)
        self.assertIn(bomb, pulse.bombs)
        self.assertEqual(bomb.attached, self.rein.body)
        for _ in range(70):
            if bomb not in pulse.bombs:
                break
            self.swing(1, buttons=0)
        self.assertNotIn(bomb, pulse.bombs)
        self.assertEqual(health.total, after_contact - 345)
        self.assertTrue(self.fight.is_stunned(self.target))
        self.assertEqual(pulse_script.component.entity_var(342).value(), 0)

    def test_actual_hammer_rejects_rear_range_ally_and_wall(self):
        for reason in ("rear", "range", "ally", "wall"):
            with self.subTest(reason=reason):
                self.setUp()
                if reason == "range":
                    self.place(9)
                elif reason == "ally":
                    self.match.game_map = replace(self.match.game_map, free_for_all=False)
                    self.rein.team = self.target.team
                elif reason == "wall":
                    x, y, z = self.target.mover.position
                    world = World(box(x - 20, x + 20, y - 2, y, z - 20, z + 20)
                                  + box(x - 5, x + 5, y, y + 5, z + 1, z + 1.1))
                    self.match.collision_provider = StaticCollisionProvider(world)
                    self.match.collision_world = world
                    for player in self.run.players:
                        player.mover.use(world=world)
                self.swing(40, yaw=0 if reason == "rear" else -32768)
                self.assertEqual(self.vitals.total, 150)
                self.assertFalse(self.rein.client.messages(combat.HIT))

    def test_recall_protects_then_actual_melee_alias_damages(self):
        self.run.step({self.target: (0, 0, 16)})
        self.assertTrue(tracer.recalling(self.target))
        self.swing(40)
        self.assertEqual(self.vitals.total, 150)
        self.swing(65, buttons=0)
        self.assertFalse(tracer.recalling(self.target))
        self.swing(40, buttons=0x800)
        self.assertEqual(self.vitals.total, 65)

    def test_overlapping_hammer_queries_damage_enemy_bubble_once(self):
        fixture = barrier_tests.TracerWinstonBarrierTests()
        run, target, owner, shooter = fixture.arena()
        run.match.switch_hero(shooter, heroes()[REINHARDT])
        shooter.team, target.team = 0, owner.team
        fixture.move(target, (0, 1, 2))
        fixture.move(shooter, (0, 1, 7))
        for _ in range(30):
            run.step({})
        bubble = fixture.cast(run, owner)
        for _ in range(40):
            run.step({shooter: (-32768, 0, FIRE)})
        self.assertEqual(bubble.health, 615)
