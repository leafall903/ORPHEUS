"""Actual enemy Zarya weapon inputs against personal/projected barriers."""

import math
import unittest
from itertools import product
from unittest.mock import patch

from test_game_combat import FakeClient, Run, aim, in_world
from test_game_mover import box

from ow174.game import combat, dva, interception, orisa_halt, zarya
from ow174.game.collision import World
from ow174.game.commands import Command
from ow174.game.content import SOLDIER, heroes
from ow174.game.script import expr

ZARYA = 0x02E0000000000068


class ZaryaBarrierWeaponTests(unittest.TestCase):
    def encounter(self, projected, *, close=False, protect=True):
        run = Run(names=("Attacker", "Caster", "Ally"))
        attacker, caster, ally = run.players
        fight = run.match.combat
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        for player in (attacker, caster):
            self.assertTrue(run.match.switch_hero(player, heroes()[ZARYA]))
        caster.team = ally.team = 1
        attacker.team = 0
        attacker.mover.position[:] = [0, 1, 3.5 if close else 0]
        caster.mover.position[:] = [3 if projected else 0, 1, 5]
        ally.mover.position[:] = [0, 1, 5 if projected else 20]
        victim = ally if projected else caster
        run.step({caster: (-16384 if projected else 0, 0, (16 if projected else 8) if protect else 0)})
        instance = next(i for i in caster.body_script.component.instances.values()
                        if i.graph.index == (0x025C if projected else 0x0244))
        self.assertEqual(instance.state(10 if projected else 2).active, protect)
        return run, fight, attacker, caster, victim

    def test_sigma_accretion_damage_and_control_against_zarya_barriers(self):
        for projected, protect, wall in (
            (False, False, False), (False, False, True), (False, True, False), (True, True, False),
        ):
            with self.subTest(projected=projected, protect=protect, wall=wall):
                run, fight, attacker, caster, victim = self.encounter(projected, protect=protect)
                position = list(attacker.mover.position)
                self.assertTrue(run.match.switch_hero(attacker, heroes()[0x02E000000000023B]))
                attacker.mover.position[:] = position
                if wall:
                    run.match.collision_world = self.accretion_wall(6)
                run.step({})
                before = self.health(fight, victim)
                yaw, pitch = aim(fight._eye(attacker), fight._eye(victim))
                launched = False
                for frame in range(100):
                    run.step({attacker: (yaw, pitch, 16 if frame == 0 else 0)})
                    rock = next((r for r in fight.rockets if r.definition == 0x04000000000018B7), None)
                    if rock is not None:
                        if not launched:
                            self.assertEqual(rock.gravity, 18)
                            self.assertGreater(rock.direction[1], 0)
                        launched = True
                    elif launched:
                        break
                self.assertTrue(launched)
                self.assertIsNone(rock)
                if protect:
                    storage = fight.zarya_barriers.projected if projected else fight.zarya_barriers.active
                    self.assertEqual(storage[caster.body].remaining, 130)
                    self.assertAlmostEqual(self.energy(caster), 14)
                    self.assertEqual(self.health(fight, victim), before)
                    self.assertFalse(fight.is_stunned(victim))
                    self.assertFalse(victim.mover.external_impulse)
                else:
                    self.assertEqual(self.health(fight, victim), before - 70)
                    self.assertEqual(fight.stuns[victim.body] - fight.tick, 50)
                    self.assertTrue(fight.is_stunned(victim))
                    self.assertTrue(victim.mover.external_impulse)
                    self.assertGreater(victim.mover.velocity[2], 4.5)
                    start = tuple(victim.mover.position)
                    peak = start[1]
                    for _ in range(50):
                        run.step({})
                        peak = max(peak, victim.mover.position[1])
                    self.assertGreater(peak - start[1], 0.1)
                    distance = victim.mover.position[2] - start[2]
                    if wall:
                        self.assertGreater(distance, 0.3)
                        self.assertLess(distance, 0.9)
                    else:
                        self.assertGreater(distance, 1)
                    self.assertFalse(victim.mover.external_impulse)

    def test_sleep_dart_and_accretion_hit_bubble_edge_without_control(self):
        for projected in (False, True):
            for hero, button, absorbed in ((0x013B, 8, 5), (0x023B, 16, 70)):
                with self.subTest(projected=projected, hero=hero):
                    run, fight, attacker, caster, victim = self.encounter(projected, protect=False)
                    self.assertTrue(run.match.switch_hero(attacker, heroes()[0x02E0000000000000 | hero]))
                    attacker.mover.position[:] = [1, 1, 0]
                    attacker.mover.body = None
                    if projected:
                        caster.mover.position[:] = [10, 1, 5]
                        caster.mover.body = None
                    angles = aim(fight._eye(caster), fight._eye(victim)) if projected else (0, 0)
                    run.step({caster: (*angles, 16 if projected else 8)})
                    barriers = fight.zarya_barriers
                    self.assertTrue(barriers.protects(victim))
                    pool = (barriers.projected if projected else barriers.active)[caster.body]
                    before = self.health(fight, victim)
                    target = next(t for t in fight._targets() if t.owner is victim)
                    self.assertLess(target.radius, 1)
                    angles = aim(fight._eye(attacker), (1, 2, 5))
                    rocket = None
                    for frame in range(100):
                        run.step({attacker: (*angles, button if frame == 0 else 0)})
                        if fight.rockets:
                            rocket, = fight.rockets
                        elif rocket is not None:
                            break
                    self.assertIsNotNone(rocket)
                    self.assertNotIn(rocket, fight.rockets)
                    self.assertGreater(rocket.distance, 3)
                    self.assertLess(rocket.distance, 5)
                    self.assertEqual(pool.remaining, 200 - absorbed)
                    self.assertAlmostEqual(self.energy(caster), absorbed / 5)
                    self.assertEqual(self.health(fight, victim), before)
                    self.assertFalse(fight.is_stunned(victim))
                    self.assertFalse(victim.mover.external_impulse)
                    self.assertFalse(attacker.client.messages(combat.HIT))

    def test_sigma_hypersphere_pair_and_breaking_hit_against_zarya_barriers(self):
        for projected, protect, breaking in (
            (False, False, False), (False, True, False), (True, True, False),
            (False, True, True), (True, True, True),
        ):
            with self.subTest(projected=projected, protect=protect, breaking=breaking):
                run, fight, attacker, caster, victim = self.encounter(projected, protect=protect)
                self.assertTrue(run.match.switch_hero(attacker, heroes()[0x02E000000000023B]))
                attacker.mover.position[:] = [0, 1, 0]
                if breaking:
                    self.assertTrue(fight.zarya_barriers.intercept(victim, 165, attacker.body))
                before = self.health(fight, victim)
                angles = aim(fight._eye(attacker), fight._eye(victim))
                peak_energy = 0
                for frame in range(75):
                    run.step({attacker: (*angles, 1 if frame == 0 else 0)})
                    peak_energy = max(peak_energy, expr.to_float(self.energy(caster)))
                if protect:
                    self.assertAlmostEqual(peak_energy, 40 if breaking else 22)
                    victim_hits = [h for h in attacker.client.messages(combat.HIT)
                                   if h["+0x78"]["+0x0"]["+0x0"] == victim.body]
                    self.assertEqual(self.health(fight, victim), before - (55 if breaking else 0))
                    storage = fight.zarya_barriers.projected if projected else fight.zarya_barriers.active
                    if not breaking:
                        self.assertEqual(storage[caster.body].remaining, 90)
                        self.assertFalse(victim_hits)
                    else:
                        self.assertEqual(len(victim_hits), 2)
                else:
                    self.assertEqual(self.health(fight, victim), before - 110)

    def test_hyperspheres_hit_bubble_edge_and_recheck_after_break(self):
        for projected in (False, True):
            for breaking in (False, True):
                with self.subTest(projected=projected, breaking=breaking):
                    run = Run(names=("Sigma", "Protected", "Projector", "Rear"))
                    attacker, victim, projector, rear = run.players
                    match = run.match
                    fight = match.combat
                    match.bots.clear()
                    match._pending_bot_scripts.clear()
                    caster = projector if projected else victim
                    self.assertTrue(match.switch_hero(attacker, heroes()[0x02E000000000023B]))
                    self.assertTrue(match.switch_hero(caster, heroes()[ZARYA]))
                    for player, team, position in (
                        (attacker, 0, (1, 1, 0)), (victim, 1, (0, 1, 5)),
                        (projector, 1, (10, 1, 5)), (rear, 1, (1, 1, 9)),
                    ):
                        player.team = team
                        player.mover.position[:] = position
                        player.mover.body = None
                    angles = aim(fight._eye(caster), fight._eye(victim)) if projected else (0, 0)
                    run.step({caster: (*angles, 16 if projected else 8)})
                    barriers = fight.zarya_barriers
                    self.assertTrue(barriers.protects(victim))
                    pool = (barriers.projected if projected else barriers.active)[caster.body]
                    self.assertEqual(pool.remaining, 200)
                    if breaking:
                        pool.remaining = 35
                    before = self.health(fight, victim)
                    rear_before = self.health(fight, rear)
                    angles = aim(fight._eye(attacker), (1, 2, 9))
                    peak_energy = 0
                    launched = []
                    for frame in range(65):
                        run.step({attacker: (*angles, 1 if frame == 0 else 0)})
                        for rocket in fight.rockets:
                            if rocket not in launched:
                                launched.append(rocket)
                        peak_energy = max(peak_energy, expr.to_float(self.energy(caster)))
                    first, second = launched
                    self.assertGreater(first.distance, 3)
                    self.assertLess(first.distance, 5)
                    if breaking:
                        self.assertGreater(second.distance, 8)
                    else:
                        self.assertAlmostEqual(second.distance, first.distance, places=4)
                    self.assertEqual(pool.remaining, 0 if breaking else 90)
                    self.assertAlmostEqual(peak_energy, 7 if breaking else 22)
                    self.assertEqual(self.health(fight, victim), before)
                    self.assertEqual(self.health(fight, rear), rear_before - (55 if breaking else 0))

    def test_sigma_hypersphere_ricochet_hits_zarya_behind_the_caster(self):
        run, fight, attacker, _, victim = self.encounter(False, protect=False)
        self.assertTrue(run.match.switch_hero(attacker, heroes()[0x02E000000000023B]))
        attacker.mover.position[:] = [0, 1, 0]
        run.step({})
        victim.mover.position[:] = [0, 1, -2]
        run.match.collision_world = self.accretion_wall(6)
        before = self.health(fight, victim)
        bounced = False
        for frame in range(75):
            run.step({attacker: (0, 0, 1 if frame == 0 else 0)})
            for sphere in fight.rockets:
                if sphere.sphere_bounces == 99:
                    bounced = True
                    self.assertLess(sphere.velocity[2], 0)
        self.assertTrue(bounced)
        self.assertEqual(self.health(fight, victim), before - 110)

    def test_sigma_hyperspheres_burst_at_range_and_pull_zarya_once_per_sphere(self):
        run, fight, attacker, _, victim = self.encounter(False, protect=False)
        self.assertTrue(run.match.switch_hero(attacker, heroes()[0x02E000000000023B]))
        attacker.mover.position[:] = [0, 1, 0]
        run.step({})
        run.players[2].team = attacker.team
        victim.mover.position[:] = [1, 1, 22]
        victim.mover.body = None
        before = self.health(fight, victim)
        for frame in range(75):
            run.step({attacker: (0, 0, 1 if frame == 0 else 0)})
        self.assertEqual(self.health(fight, victim), before - 60)
        self.assertLess(victim.mover.position[0], 1)
        self.assertEqual(len(attacker.client.messages(combat.HIT)), 2)
        self.assertFalse(fight.rockets)
        for _ in range(25):
            run.step({})
        self.assertEqual(self.health(fight, victim), before - 60)

    def test_sigma_splash_hits_bubble_only_and_rechecks_capacity_for_each_burst(self):
        for button, projected, breaking in product((1, 16), (False, True), (False, True)):
            with self.subTest(button=button, projected=projected, breaking=breaking):
                run, fight, attacker, caster, victim = self.encounter(projected, protect=False)
                self.assertTrue(run.match.switch_hero(attacker, heroes()[0x02E000000000023B]))
                attacker.mover.position[:] = [0, 1, 0]
                attacker.mover.body = None
                victim.mover.position[:] = [3.75, 1, 22] if button == 1 else [3.25, 1, 5.5]
                victim.mover.body = None
                if projected:
                    caster.mover.position[:] = [9, 1, victim.mover.position[2]]
                    caster.mover.body = None
                else:
                    run.players[2].team = attacker.team
                angles = aim(fight._eye(caster), fight._eye(victim)) if projected else (0, 0)
                run.step({caster: (*angles, 16 if projected else 8)})
                barriers = fight.zarya_barriers
                self.assertTrue(barriers.protects(victim))
                pool = (barriers.projected if projected else barriers.active)[caster.body]
                capacity = 8 if breaking else 200
                pool.remaining = capacity
                if button == 16:
                    run.match.collision_world = self.accretion_wall(6)
                before = self.health(fight, victim)
                original = fight._explode
                bursts = []

                def observe(rocket, point, *, original=original, bursts=bursts, pool=pool,
                            fight=fight, victim=victim, **kwargs):
                    target = next(t for t in fight._targets() if t.owner is victim)
                    original(rocket, point, **kwargs)
                    bursts.append((rocket, point, target, pool.remaining))

                with patch.object(fight, "_explode", side_effect=observe):
                    for frame in range(75):
                        run.step({attacker: (0, 0, button if frame == 0 else 0)})
                self.assertEqual(len(bursts), 2 if button == 1 else 1)
                remaining = capacity
                for rocket, point, target, pool_after_burst in bursts:
                    a, b = target.segment()
                    body_distance = combat.segment_distance(point, a, b) - target.radius
                    self.assertEqual(rocket.blast.splash(body_distance), 0)
                    x, y, z = target.feet
                    centre = (x, y + target.height / 2, z)
                    edge_distance = math.dist(point, centre) - zarya.BUBBLE_RADIUS
                    amount = rocket.blast.amount * rocket.blast.splash(edge_distance)
                    self.assertGreater(amount, 8)
                    remaining = max(0, remaining - amount)
                    self.assertAlmostEqual(pool_after_burst, remaining, places=4)
                energy = expr.to_float(self.energy(caster))
                self.assertAlmostEqual(energy, (capacity - remaining) / 5, places=4)
                self.assertEqual(self.health(fight, victim), before)
                self.assertFalse(fight.is_stunned(victim))
                self.assertFalse(victim.mover.external_impulse)
                self.assertFalse(attacker.client.messages(combat.HIT))

    @staticmethod
    def accretion_wall(z):
        floor = [
            (-20, 1, -20, -20, 1, 20, 20, 1, 20),
            (-20, 1, -20, 20, 1, 20, 20, 1, -20),
        ]
        return World(floor + box(-4, 4, 1, 5, z, z + 1))

    def test_sigma_accretion_wall_splash_pushes_without_direct_control(self):
        run, fight, attacker, _, victim = self.encounter(False, protect=False)
        self.assertTrue(run.match.switch_hero(attacker, heroes()[0x02E000000000023B]))
        attacker.mover.position[:] = [0, 1, 0]
        victim.mover.position[:] = [1.2, 1, 5]
        run.match.collision_world = self.accretion_wall(5.8)
        before = self.health(fight, victim)
        for frame in range(100):
            run.step({attacker: (0, 0, 16 if frame == 0 else 0)})
            if self.health(fight, victim) < before:
                break
        self.assertEqual(self.health(fight, victim), before - 40)
        self.assertFalse(fight.is_stunned(victim))
        self.assertTrue(victim.mover.external_impulse)
        self.assertGreater(victim.mover.velocity[0], 0)
        self.assertLess(victim.mover.velocity[2], 0)

    @staticmethod
    def health(fight, owner):
        target = next(t for t in fight._targets() if t.owner is owner)
        return fight.vitals_of(target).total

    @staticmethod
    def energy(caster):
        return caster.body_script.component.entity_var(87).value()

    def weapon(self, projected, button, frames, *, energy=0):
        run, fight, attacker, caster, victim = self.encounter(projected, close=button == 0x800)
        before = self.health(fight, victim)
        attacker.body_script.component.entity_var(87).base = energy
        run.step({attacker: (0, 0, button)})
        for _ in range(frames - 1):
            run.step({attacker: (0, 0, button if button == 1 else 0)})
        self.assertEqual(self.health(fight, victim), before)
        self.assertFalse(attacker.client.messages(combat.HIT))
        energy_graph = next(i for i in caster.body_script.component.instances.values()
                            if i.graph.index == 0x0242)
        self.assertEqual(energy_graph.vars[392].value(), expr.Entity(attacker.body))
        storage = fight.zarya_barriers.projected if projected else fight.zarya_barriers.active
        return caster, storage[caster.body]

    def test_primary_60_absorbed_into_personal_and_projected_gives_12_energy(self):
        for projected in (False, True):
            with self.subTest(projected=projected):
                caster, barrier = self.weapon(projected, 1, 51)
                self.assertAlmostEqual(self.energy(caster), 12, places=4)
                self.assertAlmostEqual(barrier.remaining, 140, places=4)

    def test_full_energy_primary_136_absorbed_gives_27_2_energy(self):
        for projected in (False, True):
            with self.subTest(projected=projected):
                caster, barrier = self.weapon(projected, 1, 51, energy=100)
                self.assertAlmostEqual(self.energy(caster), 27.2, places=4)
                self.assertAlmostEqual(barrier.remaining, 64, places=4)

    def test_secondary_47_absorbed_into_both_barriers_gives_9_4_energy(self):
        for projected in (False, True):
            with self.subTest(projected=projected):
                caster, barrier = self.weapon(projected, 2, 60)
                self.assertAlmostEqual(self.energy(caster), 9.4, places=4)
                self.assertAlmostEqual(barrier.remaining, 153, places=4)

    def test_melee_30_absorbed_into_both_barriers_gives_6_energy(self):
        for projected in (False, True):
            with self.subTest(projected=projected):
                caster, barrier = self.weapon(projected, 0x800, 41)
                self.assertAlmostEqual(self.energy(caster), 6)
                self.assertAlmostEqual(barrier.remaining, 170)

    def test_quick_melee_hits_hostile_bubble_outside_body_reach(self):
        for projected in (False, True):
            for case in ("edge", "outside", "cover", "ally"):
                with self.subTest(projected=projected, case=case):
                    run, fight, attacker, caster, victim = self.encounter(
                        projected, close=True, protect=False,
                    )
                    attacker.body_script.ult_seconds = 0
                    attacker.mover.position[0] = 2.6 if case == "outside" else 1.8
                    attacker.mover.body = None
                    if projected:
                        caster.mover.position[:] = [8, 1, 5]
                        caster.mover.body = None
                    if case == "ally":
                        attacker.team = victim.team
                    angles = aim(fight._eye(caster), fight._eye(victim)) if projected else (0, 0)
                    run.step({caster: (*angles, 16 if projected else 8)})
                    barriers = fight.zarya_barriers
                    self.assertTrue(barriers.protects(victim))
                    pool = (barriers.projected if projected else barriers.active)[caster.body]
                    target = next(t for t in fight._targets() if t.owner is victim)
                    self.assertLess(target.radius + 1, attacker.mover.position[0])
                    before = self.health(fight, victim)
                    if case == "cover":
                        run.match.collision_world = self.accretion_wall(4.2)
                    for _ in range(41):
                        run.step({attacker: (0, 0, 0x800)})
                    absorbed = 30 if case == "edge" else 0
                    self.assertEqual(pool.remaining, 200 - absorbed)
                    self.assertAlmostEqual(expr.to_float(self.energy(caster)), absorbed / 5)
                    self.assertEqual(self.health(fight, victim), before)
                    self.assertEqual(attacker.body_script.component.entity_var(342).value(), 0)
                    self.assertFalse(attacker.client.messages(combat.HIT))

    def test_sigma_quick_melee_is_absorbed_by_either_barrier(self):
        for projected, protect in ((False, False), (False, True), (True, True)):
            with self.subTest(projected=projected, protect=protect):
                run, fight, attacker, caster, victim = self.encounter(projected, close=True, protect=protect)
                self.assertTrue(run.match.switch_hero(attacker, heroes()[0x02E000000000023B]))
                attacker.mover.position[:] = [0, 1, 3.5]
                before = self.health(fight, victim)
                angles = aim(fight._eye(attacker), fight._eye(victim))
                for frame in range(41):
                    run.step({attacker: (*angles, 0x800 if frame == 0 else 0)})
                self.assertEqual(self.health(fight, victim), before if protect else before - 30)
                if protect:
                    storage = fight.zarya_barriers.projected if projected else fight.zarya_barriers.active
                    self.assertEqual(storage[caster.body].remaining, 170)
                    self.assertAlmostEqual(self.energy(caster), 6)
                    self.assertFalse(attacker.client.messages(combat.HIT))
                else:
                    self.assertEqual(len(attacker.client.messages(combat.HIT)), 1)

    def test_orisa_halt_moves_exposed_target_but_either_barrier_blocks_pull(self):
        for projected, protect in ((False, False), (False, True), (True, True)):
            with self.subTest(projected=projected, protect=protect):
                run, fight, orisa, caster, victim = self.encounter(projected, protect=protect)
                self.assertTrue(run.match.switch_hero(orisa, heroes()[0x02E000000000013E]))
                orisa.mover.position[:] = [0, 1, 0]
                start = tuple(victim.mover.position)
                health = self.health(fight, victim)
                for frame in range(26):
                    run.step({orisa: (0, 0, 2 if frame in (0, 25) else 0)})
                orb, = fight.rockets
                self.assertIsNotNone(orb.halt_instance)
                self.assertGreater(orb.position[2], start[2])
                self.assertGreater(orb.halt_at, fight.tick)
                centre = orb.position
                for _ in range(20):
                    run.step({})
                self.assertEqual(orb.position, centre)
                self.assertEqual(tuple(victim.mover.position), start)
                for _ in range(12):
                    run.step({})
                self.assertFalse(fight.rockets)
                self.assertEqual(self.health(fight, victim), health)
                self.assertEqual(victim.body in fight.halt_pulls, not protect)
                if protect:
                    self.assertEqual(tuple(victim.mover.position), start)
                    self.assertEqual(expr.to_float(self.energy(caster)), 0)
                    storage = fight.zarya_barriers.projected if projected else fight.zarya_barriers.active
                    self.assertEqual(storage[caster.body].remaining, 200)
                else:
                    self.assertGreater(victim.mover.position[2] - start[2], 1)
                for _ in range(45):
                    run.step({})
                self.assertNotIn(victim.body, fight.halt_pulls)

    def test_sparse_halt_manual_recast_uses_admitted_tick_and_flight_position(self):
        for barrier, wall in ((None, False), ("personal", False), ("projected", False), (None, True)):
            with self.subTest(barrier=barrier, wall=wall):
                projected = barrier == "projected"
                results = []
                for sparse in (False, True):
                    run, fight, orisa, caster, victim = self.encounter(projected, protect=False)
                    match = run.match
                    self.assertTrue(match.switch_hero(orisa, heroes()[0x02E000000000013E]))
                    orisa.mover.position[:] = [0, 1, 0]
                    victim.mover.position[:] = [0, 1, 10.5 if wall else 9]
                    victim.mover.body = None
                    if wall:
                        match.collision_world = self.accretion_wall(9)
                    before = self.health(fight, victim)
                    for frame in range(25):
                        run.step({orisa: (0, 0, 2 if frame == 0 else 0)})
                    orb, = fight.rockets
                    self.assertIsNone(orb.halt_at)
                    trigger = fight.tick + 13
                    pulse = (fight.tick + 5 if wall else trigger) + 25
                    if not sparse:
                        for tick in range(fight.tick + 1, trigger + 1):
                            match.update(tick * combat.TICK, tick, frames=False)
                    match.tick = trigger
                    orisa.take_commands([Command(orisa.body_script.frame + 1, buttons=2)])

                    if barrier:
                        activation = pulse - 4
                        if not sparse:
                            for tick in range(trigger + 1, activation):
                                match.update(tick * combat.TICK, tick, frames=False)
                        match.tick = activation
                        yaw, pitch = aim(fight._eye(caster), fight._eye(victim)) if projected else (0, 0)
                        caster.take_commands([Command(activation + 500, yaw=yaw, pitch=pitch,
                                                      buttons=16 if projected else 8)])
                        self.assertTrue(fight.zarya_barriers.protects(victim))
                    if not sparse:
                        for tick in range(fight.tick + 1, pulse - 1):
                            match.update(tick * combat.TICK, tick, frames=False)
                    match.update((pulse - 1) * combat.TICK, pulse - 1, frames=False)
                    self.assertIn(orb, fight.rockets)
                    self.assertEqual(orb.halt_at, pulse)
                    self.assertAlmostEqual(orb.position[2], 8.99 if wall else 12.4)
                    self.assertNotIn(victim.body, fight.halt_pulls)
                    initial_z = victim.mover.position[2]
                    endpoint = pulse + combat.COMBAT_CATCH_UP + 7
                    if not sparse:
                        for tick in range(pulse, endpoint):
                            match.update(tick * combat.TICK, tick, frames=False)
                    match.update(endpoint * combat.TICK, endpoint, frames=False)
                    self.assertNotIn(orb, fight.rockets)
                    self.assertNotIn(victim.body, fight.halt_pulls)
                    results.append(victim.mover.position[2] - initial_z)
                    self.assertEqual(self.health(fight, victim), before)
                    if barrier:
                        barriers = fight.zarya_barriers
                        pool = barriers.projected if projected else barriers.active
                        self.assertEqual(pool[caster.body].remaining, 200)
                        self.assertEqual(expr.to_float(self.energy(caster)), 0)
                self.assertAlmostEqual(results[0], results[1])
                if barrier or wall:
                    self.assertEqual(results[0], 0)
                else:
                    self.assertGreater(results[0], 2)

    def test_graviton_handoff_to_halt_and_player_barrier_interruption(self):
        for barrier in (None, "personal", "projected"):
            with self.subTest(barrier=barrier):
                projected = barrier == "projected"
                run, fight, zarya, caster, victim = self.encounter(projected, protect=False)
                match = run.match
                orisa_hero = 0x02E000000000013E
                orisa = match.add_player(4, "Orisa", orisa_hero, zarya.team, False)
                in_world(match, orisa)
                orisa.client = FakeClient(orisa)
                run.players.append(orisa)
                self.assertTrue(match.switch_hero(orisa, heroes()[orisa_hero]))
                orisa.mover.position[:] = [2, 1, 0]
                orisa.mover.body = None
                zarya.mover.position[:] = [-3, 1, 0]
                zarya.mover.body = None
                if projected:
                    caster.mover.position[:] = [10, 1, 5]
                    caster.mover.body = None
                zarya.body_script.set_ult_charge(2100)
                angles = aim(fight._eye(zarya), (2, 1, 5))
                run.step({zarya: (*angles, 32)})
                gravitons = fight.zarya_gravitons
                for _ in range(80):
                    if gravitons.fields:
                        break
                    run.step({})
                field, = gravitons.fields
                before = self.health(fight, victim)
                while fight.tick < field.end_tick - 56:
                    run.step({})
                for frame in range(26):
                    run.step({orisa: (0, 0, 2 if frame in (0, 25) else 0)})
                orb, = fight.rockets
                self.assertLess(orb.halt_at, field.end_tick - 2)
                while fight.tick < orb.halt_at:
                    run.step({})
                goal, pull_end, effect_end = fight.halt_pulls[victim.body]
                self.assertIn(victim.body, gravitons.player_pulls)
                self.assertGreater(pull_end, field.end_tick)
                self.assertGreater(goal[2], victim.mover.position[2] + 1)

                pool = None
                if barrier:
                    angles = aim(fight._eye(caster), fight._eye(victim)) if projected else (0, 0)
                    run.step({caster: (*angles, 16 if projected else 8)})
                    barriers = fight.zarya_barriers
                    self.assertTrue(barriers.protects(victim))
                    storage = barriers.projected if projected else barriers.active
                    pool = storage[caster.body]
                    run.step({})
                    self.assertNotIn(victim.body, fight.halt_pulls)
                    self.assertIn(victim.body, gravitons.player_pulls)
                while fight.tick < field.end_tick - 1:
                    run.step({})
                    self.assertIn(victim.body, gravitons.player_pulls)
                z_before_expiry = victim.mover.position[2]
                health_before_expiry = self.health(fight, victim)
                run.step({})
                self.assertFalse(gravitons.fields)
                self.assertFalse(gravitons.player_pulls)
                if pool is None:
                    self.assertGreater(victim.mover.position[2], z_before_expiry + 0.2)
                    self.assertAlmostEqual(before - self.health(fight, victim), 20, places=4)
                    while fight.tick < pull_end + 2:
                        run.step({})
                    self.assertTrue(orisa_halt.slowing(fight, victim.body, fight.tick))
                    self.assertAlmostEqual(victim.mover.values.run_forward,
                                           victim.mover.data.run_forward * 0.35)
                else:
                    self.assertAlmostEqual(victim.mover.position[2], z_before_expiry, places=4)
                    self.assertEqual(self.health(fight, victim), health_before_expiry)
                    absorbed = 200 - pool.remaining
                    self.assertGreater(absorbed, 0)
                    self.assertAlmostEqual(before - self.health(fight, victim) + absorbed, 20, places=4)
                    self.assertAlmostEqual(self.energy(caster), absorbed / 5, places=4)
                while fight.tick < effect_end + 2:
                    run.step({})
                self.assertNotIn(victim.body, fight.halt_pulls)
                self.assertAlmostEqual(victim.mover.values.run_forward, victim.mover.data.run_forward)
                self.assertEqual(orisa.stats.values.get(combat.stats.HERO_DAMAGE, 0), 0)

    def test_orisa_halt_respects_static_cover_and_allies(self):
        for wall in (False, True):
            with self.subTest(wall=wall):
                run, fight, orisa, _, victim = self.encounter(False, protect=False)
                self.assertTrue(run.match.switch_hero(orisa, heroes()[0x02E000000000013E]))
                orisa.mover.position[:] = [0, 1, 0]
                if wall:
                    run.match.collision_world = self.accretion_wall(3)
                else:
                    victim.team = orisa.team
                start = tuple(victim.mover.position)
                for frame in range(95):
                    run.step({orisa: (0, 0, 2 if frame in (0, 25) else 0)})
                    self.assertNotIn(victim.body, fight.halt_pulls)
                self.assertFalse(fight.rockets)
                self.assertEqual(tuple(victim.mover.position), start)
                self.assertEqual(self.health(fight, victim), 400)
                instance = next(i for i in orisa.body_script.component.instances.values()
                                if i.graph.index == 0x0CCB)
                self.assertEqual(instance.state(4).counter, 1)
                self.assertGreater(instance.state(2).remaining(), 4)

    def test_orisa_halt_slow_expires_and_cooldown_rejects_repeated_presses(self):
        run, fight, orisa, _, victim = self.encounter(False, protect=False)
        self.assertTrue(run.match.switch_hero(orisa, heroes()[0x02E000000000013E]))
        orisa.mover.position[:] = [0, 1, 0]
        instance = next(i for i in orisa.body_script.component.instances.values() if i.graph.index == 0x0CCB)
        for frame in range(26):
            run.step({orisa: (0, 0, 2 if frame in (0, 15, 25) else 0)})
            if frame == 15:
                orb, = fight.rockets
                self.assertIsNone(orb.halt_at)
        orb, = fight.rockets
        self.assertIsNotNone(orb.halt_at)
        while fight.tick < orb.halt_at:
            run.step({})
        goal, pull_end, effect_end = fight.halt_pulls[victim.body]
        self.assertEqual((pull_end - orb.halt_at, effect_end - orb.halt_at), (16, 41))
        while fight.tick < pull_end + 2:
            run.step({})
        self.assertGreater(victim.mover.position[2], 6)
        self.assertLessEqual(victim.mover.position[2], goal[2] + 0.01)
        base_speed = victim.mover.data.run_forward
        self.assertAlmostEqual(victim.mover.values.run_forward, base_speed * 0.35, places=4)
        for frame in range(120):
            run.step({orisa: (0, 0, 2 if frame % 3 == 0 else 0)})
        self.assertNotIn(victim.body, fight.halt_pulls)
        self.assertAlmostEqual(victim.mover.values.run_forward, base_speed)
        self.assertFalse(fight.rockets)
        self.assertEqual(instance.state(4).counter, 1)
        self.assertGreater(instance.state(2).remaining(), 0)
        for _ in range(330):
            run.step({})
        self.assertEqual(instance.state(2).remaining(), 0)
        run.step({orisa: (0, 0, 2)})
        for _ in range(15):
            run.step({})
        replacement, = fight.rockets
        self.assertNotEqual(replacement.halt_entity, orb.halt_entity)
        self.assertEqual(instance.state(4).counter, 2)

    def test_zarya_barrier_releases_an_existing_halt(self):
        for projected in (False, True):
            with self.subTest(projected=projected):
                run, fight, orisa, caster, victim = self.encounter(projected, protect=False)
                self.assertTrue(run.match.switch_hero(orisa, heroes()[0x02E000000000013E]))
                orisa.mover.position[:] = [0, 1, 0]
                before = self.health(fight, victim)
                for frame in range(26):
                    run.step({orisa: (0, 0, 2 if frame in (0, 25) else 0)})
                orb, = fight.rockets
                while fight.tick < orb.halt_at:
                    run.step({})
                self.assertIn(victim.body, fight.halt_pulls)
                self.assertGreater(victim.mover.position[2], 5)
                angles = aim(fight._eye(caster), fight._eye(victim)) if projected else (0, 0)
                run.step({caster: (*angles, 16 if projected else 8)})
                stopped = tuple(victim.mover.position)
                for _ in range(8):
                    run.step({})
                self.assertNotIn(victim.body, fight.halt_pulls)
                self.assertEqual(tuple(victim.mover.position[::2]), stopped[::2])
                self.assertLess(victim.mover.position[1], stopped[1])
                self.assertEqual(self.health(fight, victim), before)
                self.assertEqual(expr.to_float(self.energy(caster)), 0)

    def test_sparse_halt_preserves_pull_before_barrier_admission(self):
        for projected, offset in product((False, True), (-2, 4)):
            with self.subTest(projected=projected, barrier_offset=offset):
                distances = []
                schedules = ("paced", "armed", "pull") if offset > 0 else ("paced", "armed")
                for schedule in schedules:
                    run, fight, orisa, caster, victim = self.encounter(projected, protect=False)
                    match = run.match
                    self.assertTrue(match.switch_hero(orisa, heroes()[0x02E000000000013E]))
                    orisa.mover.position[:] = [0, 1, 0]
                    before = self.health(fight, victim)
                    for frame in range(26):
                        run.step({orisa: (0, 0, 2 if frame in (0, 25) else 0)})
                    orb, = fight.rockets
                    initial_z = victim.mover.position[2]
                    activation = orb.halt_at + offset
                    endpoint = orb.halt_at + combat.COMBAT_CATCH_UP + 7
                    self.assertGreater(activation, fight.tick)
                    if schedule == "pull":
                        match.update(orb.halt_at * combat.TICK, orb.halt_at, frames=False)
                        self.assertIn(victim.body, fight.halt_pulls)
                    if schedule == "paced":
                        for tick in range(fight.tick + 1, activation):
                            match.update(tick * combat.TICK, tick, frames=False)
                        if offset > 0:
                            pulled_z = victim.mover.position[2]
                            match.update((activation - 1) * combat.TICK, activation - 1, frames=False)
                            self.assertEqual(victim.mover.position[2], pulled_z)

                    match.tick = activation
                    yaw, pitch = aim(fight._eye(caster), fight._eye(victim)) if projected else (0, 0)
                    caster.take_commands([Command(activation + 500, yaw=yaw, pitch=pitch,
                                                  buttons=16 if projected else 8)])
                    barriers = fight.zarya_barriers
                    self.assertTrue(barriers.protects(victim))
                    if schedule == "paced":
                        for tick in range(activation, endpoint):
                            match.update(tick * combat.TICK, tick, frames=False)
                    match.update(endpoint * combat.TICK, endpoint, frames=False)
                    distances.append(victim.mover.position[2] - initial_z)
                    match.update(endpoint * combat.TICK, endpoint, frames=False)
                    self.assertAlmostEqual(victim.mover.position[2] - initial_z, distances[-1])
                    self.assertNotIn(orb, fight.rockets)
                    self.assertNotIn(victim.body, fight.halt_pulls)
                    self.assertEqual(self.health(fight, victim), before)
                    pool = barriers.projected if projected else barriers.active
                    self.assertEqual(pool[caster.body].remaining, 200)
                    self.assertEqual(expr.to_float(self.energy(caster)), 0)
                if offset < 0:
                    self.assertAlmostEqual(distances[0], 0)
                else:
                    self.assertAlmostEqual(distances[0], 1.25390625)
                for distance in distances[1:]:
                    self.assertAlmostEqual(distance, distances[0], places=4)

    def test_either_barrier_ends_halt_slow_before_its_deadline(self):
        for projected in (False, True):
            with self.subTest(projected=projected):
                run, fight, orisa, caster, victim = self.encounter(projected, protect=False)
                self.assertTrue(run.match.switch_hero(orisa, heroes()[0x02E000000000013E]))
                orisa.mover.position[:] = [0, 1, 0]
                before = self.health(fight, victim)
                for frame in range(26):
                    run.step({orisa: (0, 0, 2 if frame in (0, 25) else 0)})
                orb, = fight.rockets
                while fight.tick < orb.halt_at:
                    run.step({})
                _, pull_end, effect_end = fight.halt_pulls[victim.body]
                while fight.tick < pull_end + 2:
                    run.step({})
                base_speed = victim.mover.data.run_forward
                self.assertAlmostEqual(victim.mover.values.run_forward, base_speed * 0.35, places=4)

                angles = aim(fight._eye(caster), fight._eye(victim)) if projected else (0, 0)
                run.step({caster: (*angles, 16 if projected else 8)})
                self.assertTrue(fight.zarya_barriers.protects(victim))
                run.step({})
                self.assertLess(fight.tick, effect_end)
                self.assertNotIn(victim.body, fight.halt_pulls)
                run.step({})
                self.assertAlmostEqual(victim.mover.values.run_forward, base_speed)
                self.assertEqual(self.health(fight, victim), before)
                storage = fight.zarya_barriers.projected if projected else fight.zarya_barriers.active
                self.assertEqual(storage[caster.body].remaining, 200)
                self.assertEqual(expr.to_float(self.energy(caster)), 0)

    def test_orisa_halt_body_replacement_retires_flight_or_pull(self):
        for source in (False, True):
            with self.subTest(source=source):
                run, fight, orisa, _, victim = self.encounter(False, protect=False)
                self.assertTrue(run.match.switch_hero(orisa, heroes()[0x02E000000000013E]))
                orisa.mover.position[:] = [0, 1, 0]
                for frame in range(26):
                    run.step({orisa: (0, 0, 2 if frame in (0, 25) else 0)})
                orb, = fight.rockets
                if not source:
                    while fight.tick < orb.halt_at:
                        run.step({})
                    self.assertIn(victim.body, fight.halt_pulls)
                replaced = orisa if source else victim
                old = replaced.body
                self.assertTrue(run.match.switch_hero(replaced, replaced.hero))
                self.assertNotEqual(old, replaced.body)
                self.assertNotIn(orb, fight.rockets)
                for _ in range(48):
                    run.step({})
                    self.assertFalse(fight.halt_pulls)
                self.assertEqual(self.health(fight, victim), 400)

    def test_incoming_genji_burst_and_fan_deal_or_absorb_three_hits(self):
        for button in (1, 2):
            for protect, projected in ((False, False), (True, False), (True, True)):
                with self.subTest(button=button, protect=protect, projected=projected):
                    run, fight, attacker, caster, victim = self.encounter(
                        projected, close=True, protect=protect,
                    )
                    position = tuple(attacker.mover.position)
                    self.assertTrue(run.match.switch_hero(attacker, heroes()[0x02E0000000000029]))
                    attacker.mover.position[:] = position
                    before = self.health(fight, victim)
                    x, y, z = victim.mover.position
                    yaw, pitch = aim(fight._eye(attacker), (x, y + 1, z))
                    run.step({attacker: (yaw, pitch, button)})
                    if button == 2:
                        self.assertEqual(len(fight.rockets), 3)
                    for _ in range(40):
                        run.step({attacker: (yaw, pitch, 0)})
                    self.assertEqual(self.health(fight, victim), before - (0 if protect else 87))
                    if protect:
                        pools = fight.zarya_barriers.projected if projected else fight.zarya_barriers.active
                        self.assertEqual(pools[caster.body].remaining, 113)
                        self.assertAlmostEqual(self.energy(caster), 17.4, places=4)
                        self.assertFalse(attacker.client.messages(combat.HIT))

    def test_genji_burst_and_fan_hit_bubble_edge_three_times(self):
        for projected in (False, True):
            for button in (1, 2):
                with self.subTest(projected=projected, button=button):
                    run, fight, genji, caster, victim = self.encounter(projected, close=True, protect=False)
                    self.assertTrue(run.match.switch_hero(genji, heroes()[0x02E0000000000029]))
                    genji.mover.position[:] = [1, 1, 2]
                    genji.mover.body = None
                    angles = aim(fight._eye(caster), fight._eye(victim)) if projected else (0, 0)
                    run.step({caster: (*angles, 16 if projected else 8)})
                    barriers = fight.zarya_barriers
                    self.assertTrue(barriers.protects(victim))
                    pool = (barriers.projected if projected else barriers.active)[caster.body]
                    before = self.health(fight, victim)
                    angles = aim(fight._eye(genji), (1, 2, 5))
                    launched = []
                    for frame in range(45):
                        run.step({genji: (*angles, button if frame == 0 else 0)})
                        for rocket in fight.rockets:
                            if rocket not in launched:
                                launched.append(rocket)
                    self.assertEqual(len(launched), 3)
                    self.assertFalse(fight.rockets)
                    for rocket in launched:
                        self.assertEqual(rocket.definition, combat.GENJI_SHURIKEN)
                        self.assertLess(rocket.distance, 3)
                    self.assertEqual(pool.remaining, 113)
                    self.assertAlmostEqual(self.energy(caster), 17.4, places=4)
                    self.assertEqual(self.health(fight, victim), before)
                    self.assertFalse(genji.client.messages(combat.HIT))

    @unittest.skipUnless(interception.eligible(combat.GENJI_SHURIKEN), "requires authored Shuriken tags")
    def test_reflected_shuriken_edge_uses_deflector_source_and_team(self):
        run = Run(names=("Source", "Deflector", "Zarya"))
        source, deflector, victim = run.players
        match = run.match
        fight = match.combat
        match.bots.clear()
        match._pending_bot_scripts.clear()
        for player, hero, team, position in (
            (source, 0x02E0000000000029, 0, (1, 1, 0)),
            (deflector, 0x02E0000000000029, 1, (1, 1, 3)),
            (victim, ZARYA, 0, (0, 1, -3)),
        ):
            self.assertTrue(match.switch_hero(player, heroes()[hero]))
            player.team = team
            player.mover.position[:] = position
            player.mover.body = None
        guard = {deflector: (-32768, 0, 0)}
        run.step({deflector: (-32768, 0, 16), victim: (0, 0, 8)})
        for _ in range(5):
            run.step(guard)
        run.step({**guard, source: (0, 0, 2)})
        launched = tuple(fight.rockets)
        self.assertEqual(len(launched), 3)
        source.mover.position[:] = [8, 1, 0]
        source.mover.body = None
        for _ in range(50):
            run.step(guard)
        self.assertFalse(fight.rockets)
        for rocket in launched:
            self.assertIs(rocket.shooter, deflector)
            self.assertEqual((rocket.team, rocket.source_entity), (deflector.team, deflector.body))
        pool = fight.zarya_barriers.active[victim.body]
        self.assertEqual(pool.remaining, 113)
        self.assertAlmostEqual(self.energy(victim), 17.4, places=4)
        energy = next(i for i in victim.body_script.component.instances.values() if i.graph.index == 0x0242)
        self.assertEqual(energy.vars[392].value(), expr.Entity(deflector.body))
        self.assertEqual(self.health(fight, victim), 400)
        self.assertFalse(deflector.client.messages(combat.HIT))

    def test_incoming_swift_strike_hits_once_or_is_absorbed(self):
        for protect, projected in ((False, False), (True, False), (True, True)):
            with self.subTest(protect=protect, projected=projected):
                run, fight, attacker, caster, victim = self.encounter(projected, protect=protect)
                self.assertTrue(run.match.switch_hero(attacker, heroes()[0x02E0000000000029]))
                attacker.mover.position[:] = [0, 1, 0]
                before = self.health(fight, victim)
                run.step({attacker: (0, 0, 8)})
                for _ in range(40):
                    run.step({})
                self.assertAlmostEqual(attacker.mover.position[2], 15)
                self.assertEqual(self.health(fight, victim), before - (0 if protect else 50))
                self.assertFalse(fight.genji_strikes)
                if protect:
                    pools = fight.zarya_barriers.projected if projected else fight.zarya_barriers.active
                    self.assertEqual(pools[caster.body].remaining, 150)
                    self.assertAlmostEqual(self.energy(caster), 10)
                    self.assertFalse(attacker.client.messages(combat.HIT))
                    self.assertNotIn(victim.body, fight.genji_assists)
                run.step({attacker: (0, 0, 8)})
                for _ in range(25):
                    run.step({})
                self.assertAlmostEqual(attacker.mover.position[2], 15)
                self.assertEqual(self.health(fight, victim), before - (0 if protect else 50))

    def test_swift_strike_sweeps_bubble_edge_once_and_retains_dash(self):
        for projected in (False, True):
            for case in ("edge", "break", "outside", "ally", "wall"):
                with self.subTest(projected=projected, case=case):
                    run, fight, genji, caster, victim = self.encounter(projected, protect=False)
                    self.assertTrue(run.match.switch_hero(genji, heroes()[0x02E0000000000029]))
                    x = 3.2 if case == "outside" else 2.25
                    genji.mover.position[:] = [x, 1, 0]
                    genji.mover.body = None
                    if projected:
                        caster.mover.position[:] = [9, 1, 5]
                        caster.mover.body = None
                    if case == "ally":
                        genji.team = victim.team
                    angles = aim(fight._eye(caster), fight._eye(victim)) if projected else (0, 0)
                    run.step({caster: (*angles, 16 if projected else 8)})
                    barriers = fight.zarya_barriers
                    self.assertTrue(barriers.protects(victim))
                    pool = (barriers.projected if projected else barriers.active)[caster.body]
                    target = next(t for t in fight._targets() if t.owner is victim)
                    self.assertLess(target.radius + 1.5, x)
                    before = self.health(fight, victim)
                    capacity = 25 if case == "break" else 200
                    pool.remaining = capacity
                    if case == "wall":
                        run.match.collision_world = self.accretion_wall(2)
                    run.step({genji: (0, 0, 8)})
                    if case == "break":
                        for _ in range(20):
                            if pool.remaining == 0:
                                break
                            run.step({})
                        self.assertEqual(pool.remaining, 0)
                        self.assertLess(genji.mover.position[2], 5)
                        victim.mover.position[0] = x
                        victim.mover.body = None
                    for _ in range(35):
                        run.step({})
                    absorbed = {"edge": 50, "break": 25}.get(case, 0)
                    self.assertEqual(pool.remaining, capacity - absorbed)
                    self.assertAlmostEqual(expr.to_float(self.energy(caster)), absorbed / 5)
                    self.assertEqual(self.health(fight, victim), before)
                    self.assertFalse(genji.client.messages(combat.HIT))
                    self.assertNotIn(victim.body, fight.genji_assists)
                    self.assertFalse(fight.genji_strikes)
                    if case == "wall":
                        self.assertLess(genji.mover.position[2], 2)
                    else:
                        self.assertAlmostEqual(genji.mover.position[2], 15)

    def test_incoming_swift_strike_elimination_or_recent_assist_resets_cooldown(self):
        for case in ("direct", "recent_assist", "expired_assist"):
            with self.subTest(case=case):
                assist = case != "direct"
                run, fight, genji, _, victim = self.encounter(False, protect=False)
                ally = run.players[2]
                ally.team = genji.team
                ally.mover.position[:] = [4, 1, 5]
                ally.mover.body = None
                self.assertTrue(run.match.switch_hero(genji, heroes()[0x02E0000000000029]))
                genji.mover.position[:] = [0, 1, 0]
                target = next(t for t in fight._targets() if t.owner is victim)
                fight.vitals_of(target).damage(325 if assist else 375)
                instance = next(i for i in genji.body_script.component.instances.values()
                                if i.graph.index == 0x01DB)
                run.step({genji: (0, 0, 8)})
                for _ in range(40):
                    run.step({})
                self.assertEqual(genji.mover.position[2], 15)
                if assist:
                    self.assertEqual(self.health(fight, victim), 25)
                    self.assertGreater(instance.state(0).remaining(), 7)
                    if case == "expired_assist":
                        _, hit_tick = fight.genji_assists[victim.body][genji.body]
                        while run.frame <= hit_tick + combat.GENJI_ASSIST_TICKS:
                            run.step({})
                        self.assertNotIn(victim.body, fight.genji_assists)
                    angles = aim(fight._eye(ally), (0, 2, 5))
                    for _ in range(90):
                        run.step({ally: (*angles, 1)})
                        if fight.is_dead(victim):
                            break
                self.assertTrue(fight.is_dead(victim))
                self.assertEqual(fight.vitals_of(target).killer, ally.body if assist else genji.body)
                if case == "expired_assist":
                    self.assertGreater(instance.state(0).remaining(), 0)
                else:
                    self.assertEqual(instance.state(0).remaining(), 0)
                self.assertNotIn(victim.body, fight.genji_assists)
                run.step({genji: (0, 0, 8)})
                for _ in range(30):
                    run.step({})
                self.assertEqual(genji.mover.position[2], 15 if case == "expired_assist" else 30)

    def test_incoming_swift_strike_respects_static_cover_and_allies(self):
        for wall in (False, True):
            with self.subTest(wall=wall):
                run, fight, attacker, _, victim = self.encounter(False, protect=False)
                self.assertTrue(run.match.switch_hero(attacker, heroes()[0x02E0000000000029]))
                attacker.mover.position[:] = [0, 1, 0]
                if wall:
                    run.match.collision_world = World([
                        (-10, 0, 3, 10, 10, 3, 10, 0, 3),
                        (-10, 0, 3, -10, 10, 3, 10, 10, 3),
                        (-10, 1, -10, -10, 1, 10, 10, 1, 10),
                        (-10, 1, -10, 10, 1, 10, 10, 1, -10),
                    ])
                else:
                    victim.team = attacker.team
                run.step({attacker: (0, 0, 8)})
                for _ in range(40):
                    run.step({})
                if wall:
                    self.assertGreater(attacker.mover.position[2], 0)
                    self.assertLess(attacker.mover.position[2], 3)
                else:
                    self.assertAlmostEqual(attacker.mover.position[2], 15)
                self.assertEqual(self.health(fight, victim), 400)
                self.assertFalse(attacker.client.messages(combat.HIT))

    def dragonblade(self, run, attacker):
        attacker.body_script.ult_seconds = 0
        component = attacker.body_script.component
        attacker.body_script.set_ult_charge(component.entity_var(338).value())
        for frame in range(110):
            run.step({attacker: (0, 0, 32 if frame == 1 else (1 if frame > 70 else 0))})
        ultimate = next(i for i in component.instances.values() if i.graph.index == 0x0317)
        return ultimate.state(3)

    def test_incoming_dragonblade_hits_once_or_is_absorbed(self):
        for protect, projected in ((False, False), (True, False), (True, True)):
            with self.subTest(protect=protect, projected=projected):
                run, fight, attacker, caster, victim = self.encounter(projected, protect=protect)
                self.assertTrue(run.match.switch_hero(attacker, heroes()[0x02E0000000000029]))
                attacker.mover.position[:] = [0, 1, 1]
                before = self.health(fight, victim)
                self.assertTrue(self.dragonblade(run, attacker).flags & 1)
                self.assertEqual(attacker.body_script.component.entity_var(342).value(), 0)
                self.assertEqual(self.health(fight, victim), before - (0 if protect else 110))
                if protect:
                    pools = fight.zarya_barriers.projected if projected else fight.zarya_barriers.active
                    self.assertEqual(pools[caster.body].remaining, 90)
                    self.assertAlmostEqual(self.energy(caster), 22)
                    hits = attacker.client.messages(combat.HIT)
                    self.assertEqual([m["+0x78"]["+0x0"]["+0x0"] for m in hits],
                                     [caster.body] if projected else [])
                else:
                    self.assertEqual(len(attacker.client.messages(combat.HIT)), 1)

    def test_incoming_dragonblade_respects_range_facing_cover_and_teams(self):
        for case in ("distant", "rear", "wall", "ally"):
            with self.subTest(case=case):
                run, fight, attacker, _, victim = self.encounter(False, protect=False)
                self.assertTrue(run.match.switch_hero(attacker, heroes()[0x02E0000000000029]))
                attacker.mover.position[:] = [0, 1, {"distant": -3, "rear": 9}.get(case, 1)]
                if case == "wall":
                    run.match.collision_world = World([
                        (-10, 0, 3, 10, 10, 3, 10, 0, 3),
                        (-10, 0, 3, -10, 10, 3, 10, 10, 3),
                        (-10, 1, -10, -10, 1, 10, 10, 1, 10),
                        (-10, 1, -10, 10, 1, 10, 10, 1, -10),
                    ])
                elif case == "ally":
                    victim.team = attacker.team
                self.assertTrue(self.dragonblade(run, attacker).flags & 1)
                self.assertEqual(self.health(fight, victim), 400)
                self.assertFalse(attacker.client.messages(combat.HIT))

    def test_dragonblade_hits_bubble_edge_without_expanding_facing_filter(self):
        for projected in (False, True):
            for case in ("edge", "break", "outside", "rear", "wall", "ally"):
                with self.subTest(projected=projected, case=case):
                    run, fight, genji, caster, victim = self.encounter(projected, protect=False)
                    self.assertTrue(run.match.switch_hero(genji, heroes()[0x02E0000000000029]))
                    z = {"outside": -1.8, "rear": 10.9}.get(case, -0.9)
                    genji.mover.position[:] = [0, 1, z]
                    genji.mover.body = None
                    if projected:
                        caster.mover.position[:] = [9, 1, 5]
                        caster.mover.body = None
                    if case == "ally":
                        genji.team = victim.team
                    angles = aim(fight._eye(caster), fight._eye(victim)) if projected else (0, 0)
                    run.step({caster: (*angles, 16 if projected else 8)})
                    barriers = fight.zarya_barriers
                    self.assertTrue(barriers.protects(victim))
                    pool = (barriers.projected if projected else barriers.active)[caster.body]
                    target = next(t for t in fight._targets() if t.owner is victim)
                    self.assertLess(5 + target.radius, abs(target.feet[2] - z))
                    before = self.health(fight, victim)
                    capacity = 80 if case == "break" else 200
                    pool.remaining = capacity
                    if case == "wall":
                        run.match.collision_world = self.accretion_wall(2)
                    self.assertTrue(self.dragonblade(run, genji).flags & 1)
                    absorbed = {"edge": 110, "break": 80}.get(case, 0)
                    self.assertEqual(pool.remaining, capacity - absorbed)
                    self.assertAlmostEqual(expr.to_float(self.energy(caster)), absorbed / 5)
                    self.assertEqual(self.health(fight, victim), before)
                    self.assertFalse(genji.client.messages(combat.HIT))
                    self.assertEqual(genji.body_script.component.entity_var(342).value(), 0)
                    if case == "break":
                        genji.mover.position[:] = [0, 1, 1]
                        genji.mover.body = None
                        run.step({})
                        for _ in range(100):
                            run.step({genji: (0, 0, 1)})
                            if self.health(fight, victim) < before:
                                break
                        self.assertEqual(self.health(fight, victim), before - 110)
                        self.assertEqual(len(genji.client.messages(combat.HIT)), 1)

    def test_incoming_dragonblade_requires_full_charge_and_expires(self):
        run, fight, attacker, _, victim = self.encounter(False, protect=False)
        self.assertTrue(run.match.switch_hero(attacker, heroes()[0x02E0000000000029]))
        attacker.mover.position[:] = [0, 1, 1]
        attacker.body_script.ult_seconds = 0
        component = attacker.body_script.component
        cost = component.entity_var(338).value()
        attacker.body_script.set_ult_charge(cost - 1)
        run.step({attacker: (0, 0, 32)})
        run.step({})
        self.assertEqual(component.entity_var(342).value(), cost - 1)
        blade = self.dragonblade(run, attacker)
        self.assertTrue(blade.flags & 1)
        self.assertEqual(self.health(fight, victim), 290)
        hits = len(attacker.client.messages(combat.HIT))
        for _ in range(400):
            run.step({})
            if not blade.flags & 1:
                break
        self.assertFalse(blade.flags & 1)
        self.assertEqual(len(attacker.client.messages(combat.HIT)), hits)
        self.assertGreaterEqual(self.health(fight, victim), 290)
        self.assertEqual(component.entity_var(342).value(), 0)

    def test_incoming_dragonblade_breaking_swing_is_absorbed_before_next_swing(self):
        run, fight, attacker, caster, victim = self.encounter(False, protect=False)
        self.assertTrue(run.match.switch_hero(attacker, heroes()[0x02E0000000000029]))
        attacker.mover.position[:] = [0, 1, 1]
        attacker.body_script.ult_seconds = 0
        component = attacker.body_script.component
        attacker.body_script.set_ult_charge(component.entity_var(338).value())
        for frame in range(220):
            run.step({attacker: (0, 0, 32 if frame == 1 else (1 if frame > 70 else 0)),
                      caster: (0, 0, 8 if frame == 65 else 0)})
            if self.health(fight, victim) < 400:
                break
        self.assertEqual(self.health(fight, victim), 290)
        self.assertAlmostEqual(self.energy(caster), 40, delta=0.1)
        hits = attacker.client.messages(combat.HIT)
        self.assertEqual([hit["+0x78"]["+0x4"] for hit in hits], [110])

    def nano_boost(self, run, ana, target):
        fight = run.match.combat
        ana.body_script.ult_seconds = 0
        ana.body_script.set_ult_charge(ana.body_script.component.entity_var(338).value())
        angles = aim(fight._eye(ana), fight._eye(target))
        run.step({ana: (*angles, 32)})
        for _ in range(25):
            run.step({ana: (*angles, 0)})
            if fight.offense_scale(target.body) > 1:
                break
        self.assertEqual(fight.offense_scale(target.body), 1.5)
        self.assertEqual(ana.body_script.component.entity_var(342).value(), 0)

    def test_actual_nano_heals_zarya_and_scales_outgoing_and_incoming_damage(self):
        run, fight, ana, zarya, _ = self.encounter(False, protect=False)
        enemy = run.players[2]
        self.assertTrue(run.match.switch_hero(ana, heroes()[0x02E000000000013B]))
        ana.team = zarya.team
        ana.mover.position[:] = [0, 1, 0]
        enemy.team = 1 - zarya.team
        enemy.mover.position[:] = [0, 1, 9]
        enemy.mover.body = None
        vitals = fight.vitals_of(next(t for t in fight._targets() if t.owner is zarya))
        vitals.damage(300)
        self.nano_boost(run, ana, zarya)
        self.assertEqual(vitals.total, 350)
        self.assertEqual(vitals.nano_until - fight.tick, 500)
        for _ in range(51):
            run.step({zarya: (0, 0, 1)})
        self.assertAlmostEqual(self.health(fight, enemy), 110)
        angles = aim(fight._eye(enemy), (0, 2, 5))
        run.step({enemy: (*angles, 1)})
        self.assertEqual(vitals.total, 340)
        run.frame = vitals.nano_until
        run.match.update(run.frame * combat.TICK, run.frame)
        self.assertEqual(fight.offense_scale(zarya.body), 1)
        older = run.frame - 1
        fight.update(older * combat.TICK, older, frames=False)
        self.assertEqual(fight.offense_scale(zarya.body), 1)

    def test_nano_and_secondary_resolve_in_world_order_across_a_gap(self):
        for offset, starting_health, expected in ((5, 100, 326.5), (25, 100, 303), (25, 30, 0)):
            with self.subTest(offset=offset, starting_health=starting_health):
                run, fight, ana, ally, _ = self.encounter(False, protect=False)
                match = run.match
                enemy = run.players[2]
                self.assertTrue(match.switch_hero(ana, heroes()[0x02E000000000013B]))
                self.assertTrue(match.switch_hero(enemy, heroes()[ZARYA]))
                ana.team = ally.team
                enemy.team = 1 - ally.team
                for player, pos in ((ana, (4, 1, 5)), (enemy, (0, 1, 0))):
                    player.mover.position[:] = pos
                    player.mover.body = None
                target = next(t for t in fight._targets() if t.owner is ally)
                vitals = fight.vitals_of(target)
                vitals.damage(vitals.total - starting_health)
                run.step({enemy: (0, 0, 2)})
                self.assertEqual(len(fight.rockets), 1)
                launched = run.frame
                activation = launched + offset
                match.tick = activation
                ana.body_script.ult_seconds = 0
                ana.body_script.set_ult_charge(ana.body_script.component.entity_var(338).value())
                yaw, pitch = aim(fight._eye(ana), fight._eye(ally))
                frame = activation + 500
                ana.take_commands([
                    Command(at, yaw=yaw, pitch=pitch, buttons=32 if at == frame else 0)
                    for at in range(frame, frame + 26)
                ])
                self.assertEqual(ana.body_script.component.entity_var(342).value(), 0)
                self.assertEqual(vitals.total, starting_health)
                end = launched + 40
                match.update(end * combat.TICK, end, frames=False)
                self.assertFalse(fight.rockets)
                self.assertAlmostEqual(vitals.total, expected)
                self.assertEqual(vitals.nano_until, activation + 500 if expected else 0)
                self.assertEqual(vitals.dead, expected == 0)

    def test_nano_boosted_secondary_retains_damage_after_nano_expires(self):
        run, fight, ana, zarya, _ = self.encounter(False, protect=False)
        victim = run.players[2]
        self.assertTrue(run.match.switch_hero(ana, heroes()[0x02E000000000013B]))
        ana.team = zarya.team
        ana.mover.position[:] = [0, 1, 0]
        victim.team = 1 - zarya.team
        victim.mover.position[:] = [0, 1, 12]
        victim.mover.body = None
        self.nano_boost(run, ana, zarya)
        run.frame = fight.vitals[zarya.body].nano_until - 2
        run.match.update(run.frame * combat.TICK, run.frame)
        run.step({zarya: (0, 0, 2)})
        rocket, = fight.rockets
        self.assertEqual(rocket.offense_scale, 1.5)
        self.assertAlmostEqual(rocket.blast.amount, 70.5)
        for _ in range(40):
            run.step({})
        self.assertEqual(fight.offense_scale(zarya.body), 1)
        self.assertAlmostEqual(self.health(fight, victim), 129.5)

    def nano_attack_scene(self, projected):
        run = Run(names=("Attacker", "Victim", "Projector", "Ana"))
        attacker, victim, projector, ana = run.players
        caster = projector if projected else victim
        fight = run.match.combat
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        for player in (attacker, caster):
            self.assertTrue(run.match.switch_hero(player, heroes()[ZARYA]))
        self.assertTrue(run.match.switch_hero(ana, heroes()[0x02E000000000013B]))
        attacker.team = ana.team = 0
        victim.team = projector.team = 1
        attacker.mover.position[:] = [0, 1, 0]
        ana.mover.position[:] = [0, 1, -3]
        victim.mover.position[:] = [0, 1, 5]
        projector.mover.position[:] = [3 if projected else 10, 1, 5]
        self.nano_boost(run, ana, attacker)
        return run, fight, attacker, victim, caster

    def test_nano_boosted_graviton_preserves_launch_scale_and_barrier_admission(self):
        for projected in (None, False, True):
            with self.subTest(projected=projected):
                run, fight, attacker, victim, caster = self.nano_attack_scene(projected)
                run.frame = fight.vitals[attacker.body].nano_until - 3
                run.match.update(run.frame * combat.TICK, run.frame)
                if projected is not None:
                    run.step({caster: (-16384 if projected else 0, 0, 16 if projected else 8)})
                before = self.health(fight, victim)
                attacker.body_script.ult_seconds = 0
                attacker.body_script.set_ult_charge(2100)
                run.step({attacker: (0, 0, 32)})
                for _ in range(30):
                    if fight.zarya_gravitons.fields:
                        break
                    run.step({})
                field, = fight.zarya_gravitons.fields
                self.assertEqual(fight.offense_scale(attacker.body), 1)
                self.assertEqual(field.offense_scale, 1.5)
                self.assertEqual(attacker.body_script.component.entity_var(342).value(), 0)
                after_impact = before if projected is not None else before - 7.5
                self.assertAlmostEqual(self.health(fight, victim), after_impact)

                for _ in range(20):
                    run.step({})
                barrier = None
                if projected is not None:
                    pools = fight.zarya_barriers.projected if projected else fight.zarya_barriers.active
                    barrier = pools[caster.body]
                    self.assertAlmostEqual(barrier.remaining, 190.1)
                    self.assertAlmostEqual(self.energy(caster), 1.98, places=4)
                    self.assertEqual(self.health(fight, victim), before)

                while run.frame < field.end_tick:
                    run.step({})
                self.assertFalse(fight.zarya_gravitons.fields)
                absorbed = 200 - barrier.remaining if barrier is not None else 0
                self.assertAlmostEqual(before - self.health(fight, victim), 37.5 - absorbed, places=4)

    def test_nano_boosted_beam_is_absorbed_by_both_zarya_barriers(self):
        for projected in (False, True):
            with self.subTest(projected=projected):
                run, fight, attacker, victim, caster = self.nano_attack_scene(projected)
                before = self.health(fight, victim)
                run.step({caster: (-16384 if projected else 0, 0, 16 if projected else 8)})
                for _ in range(51):
                    run.step({attacker: (0, 0, 1)})
                self.assertEqual(self.health(fight, victim), before)
                self.assertAlmostEqual(self.energy(caster), 18, places=4)
                pools = fight.zarya_barriers.projected if projected else fight.zarya_barriers.active
                self.assertAlmostEqual(pools[caster.body].remaining, 110, places=4)

    def test_actual_nano_rejects_enemy_and_out_of_range_ally_without_spending_charge(self):
        for allied in (False, True):
            with self.subTest(allied=allied):
                run, fight, ana, target, _ = self.encounter(False, protect=False)
                self.assertTrue(run.match.switch_hero(ana, heroes()[0x02E000000000013B]))
                ana.mover.position[:] = [0, 1, 0]
                if allied:
                    ana.team = target.team
                    target.mover.position[:] = [0, 1, 42]
                    target.mover.body = None
                    run.players[2].team = 1 - ana.team
                script = ana.body_script
                script.ult_seconds = 0
                cost = script.component.entity_var(338).value()
                script.set_ult_charge(cost)
                angles = aim(fight._eye(ana), fight._eye(target))
                run.step({ana: (*angles, 32)})
                for _ in range(25):
                    run.step({ana: (*angles, 0)})
                self.assertEqual(fight.offense_scale(target.body), 1)
                self.assertEqual(script.component.entity_var(342).value(), cost)

    def test_actual_nano_healing_obeys_grenade_status_without_losing_damage_boost(self):
        for blocked in (False, True):
            with self.subTest(blocked=blocked):
                run, fight, thrower, target, _ = self.encounter(False, protect=False)
                ana = run.players[2]
                for player, position in ((thrower, [0, 1, 0]), (ana, [3, 1, 5])):
                    self.assertTrue(run.match.switch_hero(player, heroes()[0x02E000000000013B]))
                    player.mover.position[:] = position
                if not blocked:
                    thrower.team = target.team
                vitals = fight.vitals_of(next(t for t in fight._targets() if t.owner is target))
                vitals.damage(300 if blocked else 390)
                self.throw_biotic_grenade(run, thrower, target)
                self.assertEqual(vitals.total, 50 if blocked else 110)
                self.nano_boost(run, ana, target)
                self.assertEqual(vitals.total, 50 if blocked else 400)
                self.assertEqual(fight.offense_scale(target.body), 1.5)

    def fire_ana_rifle(self, run, ana, victim, *, scoped):
        fight = run.match.combat
        x, y, z = victim.mover.position
        angles = aim(fight._eye(ana), (x, y + 1, z))
        if scoped:
            for _ in range(35):
                run.step({ana: (*angles, 2)})
        run.step({ana: (*angles, 3 if scoped else 1)})
        self.assertEqual(bool(fight.rockets), not scoped)
        for _ in range(8):
            run.step({ana: (*angles, 2 if scoped else 0)})

    def test_incoming_ana_rifle_doses_or_is_absorbed_on_contact(self):
        for scoped in (False, True):
            for protect, projected in ((False, False), (True, False), (True, True)):
                with self.subTest(scoped=scoped, protect=protect, projected=projected):
                    run, fight, ana, caster, victim = self.encounter(projected, protect=protect)
                    self.assertTrue(run.match.switch_hero(ana, heroes()[0x02E000000000013B]))
                    ana.mover.position[:] = [0, 1, 0]
                    before = self.health(fight, victim)
                    self.fire_ana_rifle(run, ana, victim, scoped=scoped)
                    if not protect:
                        self.assertGreater(self.health(fight, victim), before - 70)
                        self.assertLess(self.health(fight, victim), before)
                        self.assertTrue(fight.rifle_doses)
                        partial = self.health(fight, victim)
                        for tick in (run.frame, run.frame - 1, run.frame):
                            fight.update(tick * combat.TICK, tick, frames=False)
                            self.assertEqual(self.health(fight, victim), partial)
                    for _ in range(45):
                        run.step({})
                    self.assertEqual(self.health(fight, victim), before - (0 if protect else 70))
                    self.assertFalse(fight.rifle_doses)
                    if protect:
                        self.assertAlmostEqual(self.energy(caster), 14)
                        self.assertFalse(ana.client.messages(combat.HIT))

    def test_ana_rifle_retires_pending_damage_or_healing_after_recipient_team_change(self):
        for healing in (False, True):
            with self.subTest(healing=healing):
                run, fight, ana, _, victim = self.encounter(False, protect=False)
                self.assertTrue(run.match.switch_hero(ana, heroes()[0x02E000000000013B]))
                ana.mover.position[:] = (0, 1, 0)
                ana.mover.body = None
                if healing:
                    self.fire_ana_rifle(run, ana, victim, scoped=False)
                    for _ in range(56):
                        run.step({})
                    self.assertFalse(fight.rifle_doses)
                    self.assertEqual(self.health(fight, victim), 330)
                    ana.team = victim.team
                before = self.health(fight, victim)
                self.fire_ana_rifle(run, ana, victim, scoped=False)
                dose, = fight.rifle_doses
                self.assertEqual(dose.healing, healing)
                self.assertGreater(dose.applied, 0)
                self.assertLess(dose.applied, 70)
                at_switch = self.health(fight, victim)
                self.assertAlmostEqual(at_switch - before, dose.applied if healing else -dose.applied)
                victim.team = 1 - ana.team if healing else ana.team
                run.step({})
                self.assertFalse(fight.rifle_doses)
                for _ in range(40):
                    run.step({})
                self.assertEqual(self.health(fight, victim), at_switch)

    def test_friendly_ana_rifle_heals_through_personal_barrier(self):
        for scoped in (False, True):
            with self.subTest(scoped=scoped):
                run, fight, ana, caster, victim = self.encounter(False)
                self.assertTrue(run.match.switch_hero(ana, heroes()[0x02E000000000013B]))
                ana.mover.position[:] = [0, 1, 0]
                ana.team = caster.team
                vitals = fight.vitals_of(next(t for t in fight._targets() if t.owner is victim))
                vitals.damage(150)
                self.fire_ana_rifle(run, ana, victim, scoped=scoped)
                self.assertGreater(vitals.total, 250)
                self.assertLess(vitals.total, 320)
                for _ in range(45):
                    run.step({})
                self.assertEqual(vitals.total, 320)
                self.assertEqual(expr.to_float(self.energy(caster)), 0)
                self.assertFalse(ana.client.messages(combat.HIT))

    def test_friendly_rifle_does_not_take_credit_for_skipped_passive_shield_recovery(self):
        for sparse in (False, True):
            with self.subTest(sparse=sparse):
                run, fight, attacker, zarya, _ = self.encounter(False, protect=False)
                ana = run.players[2]
                self.assertTrue(run.match.switch_hero(ana, heroes()[0x02E000000000013B]))
                ana.mover.position[:] = [3, 1, 5]
                ana.mover.body = None
                for _ in range(51):
                    run.step({attacker: (0, 0, 1)})
                damaged = run.frame
                vitals = fight.vitals[zarya.body]
                self.assertAlmostEqual(vitals.now[combat.SHIELDS_POOL], 140)
                for _ in range(144):
                    run.step({})
                x, y, z = zarya.mover.position
                angles = aim(fight._eye(ana), (x, y + 1, z))
                for _ in range(35):
                    run.step({ana: (*angles, 2)})
                run.step({ana: (*angles, 3)})
                dose, = fight.rifle_doses
                self.assertTrue(dose.healing)
                self.assertEqual(dose.start_tick, damaged + 180)
                self.assertAlmostEqual(vitals.now[combat.SHIELDS_POOL], 140)

                if sparse:
                    run.frame = damaged + 280
                    run.match.update(run.frame * combat.TICK, run.frame)
                else:
                    while run.frame < damaged + 280:
                        run.step({})
                self.assertFalse(fight.rifle_doses)
                self.assertEqual(vitals.now[combat.SHIELDS_POOL], 200)
                self.assertEqual(vitals.now[combat.HEALTH_POOL], 200)
                # The rifle supplies 50.64 of the 60 missing shields and passive recovery supplies 9.36.
                self.assertAlmostEqual(ana.stats.values[combat.stats.HEALING], 50.64, places=4)

    def test_friendly_ana_rifle_respects_actual_grenade_block_and_boost(self):
        for blocked in (False, True):
            with self.subTest(blocked=blocked):
                run, fight, enemy, _, victim = self.encounter(False, protect=False)
                ally = run.players[2]
                for ana, position in ((enemy, [0, 1, 0]), (ally, [3, 1, 5])):
                    self.assertTrue(run.match.switch_hero(ana, heroes()[0x02E000000000013B]))
                    ana.mover.position[:] = position
                vitals = fight.vitals_of(next(t for t in fight._targets() if t.owner is victim))
                vitals.damage(250)
                if not blocked:
                    enemy.team = ally.team
                self.throw_biotic_grenade(run, enemy, victim)
                before = vitals.total
                for _ in range(25):
                    run.step({})
                self.fire_ana_rifle(run, ally, victim, scoped=False)
                for _ in range(45):
                    run.step({})
                self.assertAlmostEqual(vitals.total - before, 0 if blocked else 105)

    def test_ana_rifle_does_not_transfer_remaining_dose_to_replacement(self):
        run, fight, ana, _, victim = self.encounter(False, protect=False)
        self.assertTrue(run.match.switch_hero(ana, heroes()[0x02E000000000013B]))
        ana.mover.position[:] = [0, 1, 0]
        self.fire_ana_rifle(run, ana, victim, scoped=False)
        old = victim.body
        self.assertEqual([dose.target for dose in fight.rifle_doses], [old])
        self.assertTrue(run.match.switch_hero(victim, victim.hero))
        self.assertNotEqual(victim.body, old)
        self.assertFalse(fight.rifle_doses)
        for _ in range(45):
            run.step({})
        self.assertEqual(self.health(fight, victim), 400)

    def test_ana_projectile_and_late_barrier_keep_effect_order_across_gap(self):
        for button in (1, 16):
            with self.subTest(button=button):
                outcomes = []
                for sparse in (False, True):
                    run, fight, ana, caster, victim = self.encounter(False, protect=False)
                    match = run.match
                    self.assertTrue(match.switch_hero(ana, heroes()[0x02E000000000013B]))
                    ana.mover.position[:] = (0, 1, 0)
                    x, y, z = victim.mover.position
                    yaw, pitch = aim(fight._eye(ana), (x, y + 1, z))
                    for frame in range(35):
                        run.step({ana: (yaw, pitch, button if frame == 0 else 0)})
                        if fight.rockets:
                            break
                    rocket, = fight.rockets
                    self.assertEqual(rocket.flown, 0)
                    launch = run.frame
                    activation = launch + 20
                    end = launch + 80
                    if not sparse:
                        for tick in range(launch + 1, activation):
                            match.update(tick * combat.TICK, tick, frames=False)
                    match.tick = activation
                    caster.take_commands([Command(activation, buttons=8)])
                    for tick in (end,) if sparse else range(activation, end + 1):
                        match.update(tick * combat.TICK, tick, frames=False)
                    self.assertFalse(fight.rockets)
                    self.assertFalse(fight.rifle_doses)
                    self.assertTrue(fight.zarya_barriers.protects(victim))
                    pool = fight.zarya_barriers.active[caster.body]
                    vitals = fight.vitals[victim.body]
                    self.assertEqual(vitals.healing_block_until, 0)
                    outcomes.append((vitals.total, pool.remaining, expr.to_float(self.energy(caster))))
                if button == 16:
                    self.assertEqual(outcomes[0], (350, 200, 0))
                else:
                    self.assertGreater(outcomes[0][0], 330)
                    self.assertLess(outcomes[0][0], 400)
                    self.assertLess(outcomes[0][1], 200)
                for paced, sparse in zip(*outcomes, strict=True):
                    self.assertAlmostEqual(sparse, paced, places=4)

    def throw_biotic_grenade(self, run, ana, victim):
        fight = run.match.combat
        x, y, z = victim.mover.position
        angles = aim(fight._eye(ana), (x, y + 1, z))
        run.step({ana: (*angles, 16)})
        launched = False
        for _ in range(60):
            for rocket in fight.rockets:
                if rocket.definition == combat.ANA_GRENADE:
                    launched = True
                    self.assertAlmostEqual(rocket.gravity, 9.8, places=5)
            run.step({ana: (*angles, 0)})
            if launched and not fight.rockets:
                break
        self.assertTrue(launched)
        self.assertFalse(fight.rockets)

    def test_incoming_biotic_grenade_damage_and_antiheal_are_blocked_by_barriers(self):
        for protect, projected in ((False, False), (True, False), (True, True)):
            with self.subTest(protect=protect, projected=projected):
                run, fight, ana, caster, victim = self.encounter(projected, protect=protect)
                self.assertTrue(run.match.switch_hero(ana, heroes()[0x02E000000000013B]))
                ana.mover.position[:] = [0, 1, 0]
                before = self.health(fight, victim)
                self.throw_biotic_grenade(run, ana, victim)
                vitals = fight.vitals[victim.body]
                self.assertEqual(vitals.total, before - (0 if protect else 50))
                expected_deadline = 0 if protect else fight.tick + combat.BIOTIC_TICKS
                self.assertEqual(vitals.healing_block_until, expected_deadline)
                if protect:
                    self.assertAlmostEqual(self.energy(caster), 10)
                    hits = ana.client.messages(combat.HIT)
                    self.assertEqual([hit["+0x78"]["+0x0"]["+0x0"] for hit in hits],
                                     [caster.body] if projected else [])

    def test_light_gun_hits_bubble_edge_and_only_crits_exposed_head(self):
        cases = ("edge", "edge break", "head break", "ally", "outside", "wall")
        for projected, case in product((False, True), cases):
            with self.subTest(projected=projected, case=case):
                run, fight, pilot, caster, victim = self.encounter(projected, protect=False)
                match = run.match
                rear = match.add_player(4, "Rear", ZARYA, victim.team, False)
                in_world(match, rear)
                run.players.append(rear)
                for actor, definition in ((pilot, heroes()[dva.HERO]), (victim, heroes()[ZARYA]),
                                          (rear, heroes()[ZARYA])):
                    self.assertTrue(match.switch_hero(actor, definition))
                victim_x = {"head break": 0, "outside": 2}.get(case, 1)
                poses = ((pilot, (0, 1, 0)), (victim, (victim_x, 1, 4)), (rear, (0, 1, 7)))
                if projected:
                    poses += ((caster, (6, 1, 4)),)
                for actor, point in poses:
                    actor.mover.position[:] = point
                    actor.mover.body = None
                if case == "ally":
                    victim.team = caster.team = pilot.team
                for _ in range(100):
                    run.step({})
                angles = aim(fight._eye(caster), fight._eye(victim)) if projected else (0, 0)
                run.step({caster: (*angles, 16 if projected else 8)})
                barriers = fight.zarya_barriers
                self.assertTrue(barriers.protects(victim))
                pool = (barriers.projected if projected else barriers.active)[caster.body]
                capacity = 5 if case.endswith("break") else 200
                pool.remaining = capacity
                if case == "wall":
                    match.collision_world = self.accretion_wall(2)
                head = case == "head break"
                point = fight._eye(victim) if head else (0, 1.9, 7)
                angles = aim(fight._eye(pilot), point)
                run.step({pilot: (*angles, 1)})
                rocket, = fight.rockets
                self.assertEqual(rocket.definition, 0x0400000000001000)
                self.assertEqual(rocket.direct.amount, 14)
                if case in ("edge", "edge break"):
                    target = next(t for t in fight._targets() if t.owner is victim)
                    a, b = target.segment()
                    body = combat.ray_capsule(rocket.position, rocket.direction, a, b, target.radius)
                    self.assertIsNone(body)
                for _ in range(25):
                    run.step({})
                    if rocket not in fight.rockets:
                        break
                self.assertNotIn(rocket, fight.rockets)
                absorbed = {"edge": 14, "edge break": 5, "head break": 5}.get(case, 0)
                self.assertEqual(pool.remaining, capacity - absorbed)
                self.assertAlmostEqual(expr.to_float(self.energy(caster)), absorbed / 5)
                self.assertEqual(self.health(fight, victim), 400)
                self.assertEqual(self.health(fight, rear), 386 if case in ("ally", "outside") else 400)
                if not case.endswith("break"):
                    continue
                self.assertFalse(pilot.client.messages(combat.HIT))
                for _ in range(30):
                    run.step({})
                run.step({pilot: (*angles, 1)})
                for _ in range(20):
                    run.step({})
                self.assertEqual(self.health(fight, victim), 372 if head else 400)
                self.assertEqual(self.health(fight, rear), 400 if head else 386)
                hit, = pilot.client.messages(combat.HIT)
                self.assertEqual(bool(hit["+0x78"]["+0x8"] & combat.CRITICAL), head)

    def test_control_explosions_hit_bubble_surface_without_affecting_recipient(self):
        attacks = ((0x013B, 4.0, 50), (0x0042, 3.0, 25))
        cases = ("edge", "break", "outside", "wall", "ally")
        for (hero, radius, damage), projected, case in product(attacks, (False, True), cases):
            with self.subTest(hero=hero, projected=projected, case=case):
                run, fight, source, caster, victim = self.encounter(projected, protect=False)
                self.assertTrue(run.match.switch_hero(source, heroes()[0x02E0000000000000 | hero]))
                trigger = run.match.add_player(4, "Impact", heroes()[ZARYA].guid, 1, False)
                in_world(run.match, trigger)
                run.players.append(trigger)
                self.assertTrue(run.match.switch_hero(trigger, heroes()[ZARYA]))
                x = radius + (1.9 if case == "outside" else .8)
                poses = ((source, (0, 1, 0)), (trigger, (0, 1, 3)), (victim, (x, 1, 2.6)))
                if projected:
                    poses += ((caster, (9, 1, 2.6)),)
                for actor, point in poses:
                    actor.mover.position[:] = point
                    actor.mover.body = None
                if case == "ally":
                    victim.team = caster.team = source.team
                angles = aim(fight._eye(caster), fight._eye(victim)) if projected else (0, 0)
                run.step({caster: (*angles, 16 if projected else 8)})
                barriers = fight.zarya_barriers
                self.assertTrue(barriers.protects(victim))
                pool = (barriers.projected if projected else barriers.active)[caster.body]
                capacity = 8 if case == "break" else 200
                pool.remaining = capacity
                if case == "wall":
                    wall = x - 2
                    run.match.collision_world = World([
                        (-12, 1, -12, -12, 1, 12, 12, 1, 12), (-12, 1, -12, 12, 1, 12, 12, 1, -12),
                        (wall, 0, 0, wall, 0, 8, wall, 6, 8), (wall, 0, 0, wall, 6, 8, wall, 6, 0),
                    ])
                target = next(t for t in fight._targets() if t.owner is victim)
                vitals = fight.vitals_of(target)
                if case == "ally":
                    vitals.damage(30)
                before = vitals.total
                with patch.object(fight, "_explode", wraps=fight._explode) as explosion:
                    for frame in range(60):
                        run.step({source: (0, 0, 16 if frame == 0 else 0)})
                        if explosion.called:
                            break
                explosion.assert_called_once()
                rocket, point = explosion.call_args.args
                self.assertEqual(rocket.blast.amount, damage)
                self.assertIsNone(explosion.call_args.kwargs.get("excluded"))
                a, b = target.segment()
                self.assertGreater(combat.segment_distance(point, a, b) - target.radius, radius)
                centre = (x, target.feet[1] + target.height / 2, target.feet[2])
                distance = math.dist(point, centre) - zarya.BUBBLE_RADIUS
                self.assertEqual(distance > radius, case == "outside")
                absorbed = {"edge": damage, "break": 8}.get(case, 0)
                self.assertEqual(pool.remaining, capacity - absorbed)
                self.assertAlmostEqual(expr.to_float(self.energy(caster)), absorbed / 5)
                self.assertEqual(vitals.total, before)
                self.assertFalse(vitals.healing_block_until)
                self.assertFalse(vitals.healing_boost_until)
                self.assertFalse(fight.is_stunned(victim))
                self.assertEqual(self.health(fight, trigger), 400 - damage)
                self.assertEqual(fight.is_stunned(trigger), hero == 0x0042)

    def test_ana_biotic_projectiles_hit_enemy_bubble_edges_and_pass_allied_edges(self):
        for projected in (False, True):
            for grenade, allied in ((True, False), (True, True), (False, False), (False, True)):
                with self.subTest(projected=projected, grenade=grenade, allied=allied):
                    run = Run(names=("Ana", "Protected", "Projector", "Rear"))
                    ana, victim, projector, rear = run.players
                    match = run.match
                    fight = match.combat
                    match.bots.clear()
                    match._pending_bot_scripts.clear()
                    caster = projector if projected else victim
                    self.assertTrue(match.switch_hero(ana, heroes()[0x02E000000000013B]))
                    self.assertTrue(match.switch_hero(caster, heroes()[ZARYA]))
                    protected_team = 0 if allied else 1
                    for player, team, position in (
                        (ana, 0, (1, 1, 0)), (victim, protected_team, (0, 1, 5)),
                        (projector, protected_team, (10, 1, 5)), (rear, 1, (1, 1, 9)),
                    ):
                        player.team = team
                        player.mover.position[:] = position
                        player.mover.body = None
                    angles = aim(fight._eye(caster), fight._eye(victim)) if projected else (0, 0)
                    run.step({caster: (*angles, 16 if projected else 8)})
                    barriers = fight.zarya_barriers
                    self.assertTrue(barriers.protects(victim))
                    pool = (barriers.projected if projected else barriers.active)[caster.body]
                    before = self.health(fight, victim)
                    rear_before = self.health(fight, rear)
                    angles = aim(fight._eye(ana), (1, 2, 9))
                    run.step({ana: (*angles, 16 if grenade else 1)})
                    rocket, = fight.rockets
                    for _ in range(50):
                        run.step({})
                    self.assertNotIn(rocket, fight.rockets)
                    amount = 50 if grenade else 70
                    if allied:
                        self.assertGreater(rocket.distance, 8)
                        self.assertAlmostEqual(self.health(fight, rear), rear_before - amount, places=4)
                        self.assertEqual(pool.remaining, 200)
                        self.assertEqual(expr.to_float(self.energy(caster)), 0)
                    else:
                        self.assertGreater(rocket.distance, 3)
                        self.assertLess(rocket.distance, 5)
                        self.assertAlmostEqual(pool.remaining, 200 - amount, places=4)
                        self.assertAlmostEqual(self.energy(caster), amount / 5, places=4)
                        self.assertEqual(self.health(fight, rear), rear_before)
                        self.assertFalse(ana.client.messages(combat.HIT))
                    self.assertEqual(self.health(fight, victim), before)
                    self.assertEqual(fight.vitals[victim.body].healing_block_until, 0)
                    self.assertFalse(fight.rifle_doses)

    def test_antiheal_blocks_passive_shield_recovery_without_catchup(self):
        for skipped in (False, True):
            with self.subTest(skipped=skipped):
                run, fight, ana, _, zarya = self.encounter(False, protect=False)
                self.assertTrue(run.match.switch_hero(ana, heroes()[0x02E000000000013B]))
                ana.mover.position[:] = [0, 1, 0]
                self.throw_biotic_grenade(run, ana, zarya)
                vitals = fight.vitals[zarya.body]
                deadline = vitals.healing_block_until
                self.assertEqual(deadline - run.frame, combat.BIOTIC_TICKS)
                self.assertEqual(vitals.now[combat.SHIELDS_POOL], 150)

                if not skipped:
                    while run.frame < deadline - 1:
                        run.step({})
                        self.assertEqual(vitals.now[combat.SHIELDS_POOL], 150)
                    self.assertEqual(vitals.healing_block_until, deadline)
                run.frame = deadline
                run.match.update(deadline * combat.TICK, deadline)
                self.assertEqual(vitals.healing_block_until, 0)
                self.assertAlmostEqual(vitals.now[combat.SHIELDS_POOL], 150.48)
                for _ in range(20):
                    run.step({})
                self.assertAlmostEqual(vitals.now[combat.SHIELDS_POOL], 160.08)
                self.assertEqual(vitals.now[combat.HEALTH_POOL], 200)

    def test_either_barrier_cleanses_existing_antiheal_on_cast(self):
        from test_game_combat import in_world

        for projected in (False, True):
            for cast in (False, True):
                with self.subTest(projected=projected, cast=cast):
                    run, fight, enemy, caster, victim = self.encounter(projected, protect=False)
                    ana_guid = 0x02E000000000013B
                    ally = run.match.add_player(4, "Healer", ana_guid, caster.team, False)
                    in_world(run.match, ally)
                    run.players.append(ally)
                    for ana, position in ((enemy, [0, 1, 0]), (ally, [-4, 1, 5])):
                        self.assertTrue(run.match.switch_hero(ana, heroes()[ana_guid]))
                        ana.mover.position[:] = position
                        ana.mover.body = None
                    if projected:
                        caster.mover.position[:] = [6, 1, 5]
                        caster.mover.body = None
                    before = self.health(fight, victim)
                    self.throw_biotic_grenade(run, enemy, victim)
                    vitals = fight.vitals[victim.body]
                    self.assertEqual(vitals.total, before - 50)
                    deadline = vitals.healing_block_until
                    self.assertGreater(deadline, fight.tick)
                    buttons = (16 if projected else 8) if cast else 0
                    angles = aim(fight._eye(caster), fight._eye(victim)) if projected else (0, 0)
                    run.step({caster: (*angles, buttons)})
                    self.assertEqual(fight.zarya_barriers.protects(victim), cast)
                    self.assertEqual(vitals.healing_block_until, 0 if cast else deadline)
                    self.assertEqual(vitals.total, before - 50)
                    self.assertEqual(expr.to_float(self.energy(caster)), 0)
                    self.throw_biotic_grenade(run, ally, victim)
                    self.assertLess(fight.tick, deadline)
                    self.assertEqual(vitals.total, before if cast else before - 50)

    def test_queued_projection_cleanse_requires_caster_alive_at_activation(self):
        for lethal in (False, True):
            with self.subTest(lethal=lethal):
                run, fight, attacker, caster, victim = self.encounter(True, protect=False)
                match = run.match
                self.assertTrue(match.switch_hero(attacker, heroes()[0x02E000000000013B]))
                attacker.mover.position[:] = (0, 1, 0)
                caster.mover.position[:] = (6, 1, 5)
                caster.mover.body = None
                self.throw_biotic_grenade(run, attacker, victim)
                vitals = fight.vitals[victim.body]
                deadline = vitals.healing_block_until
                self.assertTrue(match.switch_hero(attacker, heroes()[ZARYA]))
                attacker.mover.position[:] = (6, 1, 0)
                if lethal:
                    pool = fight.vitals[caster.body]
                    pool.damage(pool.total - 1, armour_scalar=1.0)
                x, y, z = caster.mover.position
                yaw, pitch = aim(fight._eye(attacker), (x, y + 1, z))
                run.step({attacker: (yaw, pitch, 2)})
                run.step({})
                rocket, = fight.rockets
                self.assertEqual(rocket.last_tick, run.frame)

                activation = run.frame + 40
                match.tick = activation
                yaw, pitch = aim(fight._eye(caster), fight._eye(victim))
                caster.take_commands([Command(activation, yaw=yaw, pitch=pitch, buttons=16)])
                self.assertIn((caster.body, True), fight.zarya_barriers.expiries)
                self.assertLess(activation, deadline)
                match.update(activation * combat.TICK, activation, frames=False)
                self.assertFalse(fight.rockets)
                self.assertEqual(fight.is_dead(caster), lethal)
                if lethal:
                    self.assertLess(fight.vitals[caster.body].death_tick, activation)
                self.assertEqual(fight.zarya_barriers.protects(victim), not lethal)
                self.assertEqual(vitals.healing_block_until, deadline if lethal else 0)

    def test_queued_projection_cleanse_requires_recipient_still_allied(self):
        for changes_team in (False, True):
            with self.subTest(changes_team=changes_team):
                run, fight, ana, caster, victim = self.encounter(True, protect=False)
                match = run.match
                self.assertTrue(match.switch_hero(ana, heroes()[0x02E000000000013B]))
                ana.mover.position[:] = (0, 1, 0)
                caster.mover.position[:] = (6, 1, 5)
                caster.mover.body = None
                self.throw_biotic_grenade(run, ana, victim)
                vitals = fight.vitals[victim.body]
                deadline = vitals.healing_block_until
                activation = run.frame + 10
                self.assertGreater(deadline, activation)
                match.tick = activation
                yaw, pitch = aim(fight._eye(caster), fight._eye(victim))
                caster.take_commands([Command(activation, yaw=yaw, pitch=pitch, buttons=16)])
                self.assertIn((caster.body, True), fight.zarya_barriers.expiries)
                if changes_team:
                    victim.team = ana.team
                match.update(activation * combat.TICK, activation, frames=False)
                self.assertEqual(fight.zarya_barriers.protects(victim), not changes_team)
                self.assertEqual(vitals.healing_block_until, deadline if changes_team else 0)

    def test_friendly_biotic_grenade_heals_through_barrier_and_applies_boost(self):
        run, fight, ana, caster, victim = self.encounter(False)
        self.assertTrue(run.match.switch_hero(ana, heroes()[0x02E000000000013B]))
        ana.team = caster.team
        ana.mover.position[:] = [0, 1, 0]
        vitals = fight.vitals_of(next(t for t in fight._targets() if t.owner is victim))
        vitals.damage(150)
        self.throw_biotic_grenade(run, ana, victim)
        self.assertEqual(vitals.total, 350)
        self.assertEqual(vitals.healing_boost_until, fight.tick + combat.BIOTIC_TICKS)
        self.assertEqual(expr.to_float(self.energy(caster)), 0)
        fight.heal_area(ana, tuple(victim.mover.position), 1, 20)
        self.assertEqual(vitals.total, 380)

    def test_friendly_grenade_boosts_passive_shields_only_before_expiry(self):
        for skipped in (False, True):
            with self.subTest(skipped=skipped):
                run, fight, attacker, zarya, _ = self.encounter(False, protect=False)
                ana = run.players[2]
                self.assertTrue(run.match.switch_hero(ana, heroes()[0x02E000000000013B]))
                ana.mover.position[:] = [4, 1, 5]
                ana.mover.body = None
                attacker.body_script.component.set(87, 100)
                for _ in range(61):
                    run.step({attacker: (0, 0, 1)})
                damaged_tick = run.frame
                vitals = fight.vitals[zarya.body]
                self.assertAlmostEqual(vitals.now[combat.SHIELDS_POOL], 36.8, places=4)
                self.throw_biotic_grenade(run, ana, zarya)
                self.assertAlmostEqual(vitals.now[combat.SHIELDS_POOL], 136.8, places=4)
                expiry = vitals.healing_boost_until
                self.assertEqual(expiry, run.frame + combat.BIOTIC_TICKS)
                ready = damaged_tick + 188
                if not skipped:
                    while run.frame < ready - 1:
                        run.step({})
                    self.assertAlmostEqual(vitals.now[combat.SHIELDS_POOL], 136.8, places=4)
                    run.step({})
                    self.assertAlmostEqual(vitals.now[combat.SHIELDS_POOL], 137.16, places=4)
                    while run.frame < expiry - 1:
                        run.step({})
                run.frame = expiry
                run.match.update(expiry * combat.TICK, expiry)
                expected = 136.8 + (expiry - ready - 0.5) * 0.72 + 0.48
                self.assertAlmostEqual(vitals.now[combat.SHIELDS_POOL], expected, places=4)
                self.assertEqual(vitals.healing_boost_until, 0)
                run.step({})
                self.assertAlmostEqual(vitals.now[combat.SHIELDS_POOL], expected + 0.48, places=4)
                self.assertEqual(vitals.now[combat.HEALTH_POOL], 200)

    def test_biotic_grenade_static_wall_blocks_damage_healing_and_status(self):
        for allied in (False, True):
            with self.subTest(allied=allied):
                run, fight, ana, _, victim = self.encounter(False, protect=False)
                self.assertTrue(run.match.switch_hero(ana, heroes()[0x02E000000000013B]))
                ana.mover.position[:] = [0, 1, 0]
                if allied:
                    ana.team = victim.team
                run.match.collision_world = World([
                    (-10, 0, 3, 10, 10, 3, 10, 0, 3),
                    (-10, 0, 3, -10, 10, 3, 10, 10, 3),
                    (-10, 1, -10, -10, 1, 10, 10, 1, 10),
                    (-10, 1, -10, 10, 1, 10, 10, 1, -10),
                ])
                vitals = fight.vitals_of(next(t for t in fight._targets() if t.owner is victim))
                vitals.damage(150)
                self.throw_biotic_grenade(run, ana, victim)
                self.assertEqual(vitals.total, 250)
                self.assertEqual(vitals.healing_block_until, 0)
                self.assertEqual(vitals.healing_boost_until, 0)

    def test_antiheal_blocks_a_later_actual_friendly_grenade(self):
        run, fight, enemy, _, victim = self.encounter(False, protect=False)
        ally = run.players[2]
        for ana, position in ((enemy, [0, 1, 0]), (ally, [3, 1, 5])):
            self.assertTrue(run.match.switch_hero(ana, heroes()[0x02E000000000013B]))
            ana.mover.position[:] = position
        vitals = fight.vitals_of(next(t for t in fight._targets() if t.owner is victim))
        vitals.damage(100)
        self.throw_biotic_grenade(run, enemy, victim)
        self.assertEqual(vitals.total, 250)
        deadline = vitals.healing_block_until
        self.throw_biotic_grenade(run, ally, victim)
        self.assertLess(fight.tick, deadline)
        self.assertEqual(vitals.total, 250)
        self.assertEqual(vitals.healing_block_until, deadline)
        boost_deadline = vitals.healing_boost_until
        for tick, expected in ((deadline - 1, 0), (deadline, 15), (deadline - 1, 15),
                               (boost_deadline, 10), (boost_deadline - 1, 10)):
            with self.subTest(tick=tick, expected=expected):
                fight.update(tick * combat.TICK, tick, frames=False)
                before = vitals.total
                fight.heal_area(ally, tuple(victim.mover.position), 1, 10)
                self.assertAlmostEqual(vitals.total - before, expected)

    def test_passive_shields_resume_at_boosted_rate_after_antiheal(self):
        for mode in ("natural", "skipped", "cleansed"):
            with self.subTest(mode=mode):
                run, fight, enemy, zarya, _ = self.encounter(False, protect=False)
                ally = run.players[2]
                for ana, position in ((enemy, [0, 1, 0]), (ally, [3, 1, 5])):
                    self.assertTrue(run.match.switch_hero(ana, heroes()[0x02E000000000013B]))
                    ana.mover.position[:] = position
                    ana.mover.body = None
                self.throw_biotic_grenade(run, enemy, zarya)
                vitals = fight.vitals[zarya.body]
                blocked_until = vitals.healing_block_until
                for _ in range(30):
                    run.step({})
                self.throw_biotic_grenade(run, ally, zarya)
                boosted_until = vitals.healing_boost_until
                self.assertLess(run.frame, blocked_until)
                self.assertGreater(boosted_until, blocked_until)
                self.assertEqual(vitals.healing_block_until, blocked_until)
                self.assertEqual(vitals.now[combat.SHIELDS_POOL], 150)

                first_recovery = blocked_until
                if mode == "cleansed":
                    while run.frame < blocked_until - 11:
                        run.step({})
                    run.step({zarya: (0, 0, 8)})
                    self.assertEqual(vitals.healing_block_until, 0)
                    self.assertEqual(vitals.healing_boost_until, boosted_until)
                    self.assertTrue(fight.zarya_barriers.protects(zarya))
                    first_recovery = run.frame + 1

                if mode != "skipped":
                    while run.frame < first_recovery - 1:
                        run.step({})
                        self.assertEqual(vitals.now[combat.SHIELDS_POOL], 150)
                    self.assertEqual(vitals.now[combat.SHIELDS_POOL], 150)
                    run.step({})
                    self.assertEqual(vitals.healing_block_until, 0)
                    self.assertAlmostEqual(vitals.now[combat.SHIELDS_POOL], 150.72)
                    while run.frame < boosted_until - 1:
                        run.step({})

                run.frame = boosted_until + 5
                run.match.update(run.frame * combat.TICK, run.frame)
                expected = 150 + (boosted_until - first_recovery) * 0.72 + 6 * 0.48
                self.assertAlmostEqual(vitals.now[combat.SHIELDS_POOL], expected)
                self.assertEqual(vitals.now[combat.HEALTH_POOL], 200)
                self.assertEqual(vitals.healing_block_until, 0)
                self.assertEqual(vitals.healing_boost_until, 0)

    def test_barrier_cleanse_resumes_zarya_passive_shields_on_admitted_world_tick(self):
        for projected, sparse in product((False, True), repeat=2):
            with self.subTest(projected=projected, sparse=sparse):
                run, fight, ana, caster, victim = self.encounter(projected, protect=False)
                match = run.match
                if projected:
                    self.assertTrue(match.switch_hero(victim, heroes()[ZARYA]))
                    victim.mover.position[:] = [0, 1, 5]
                    victim.mover.body = None
                    caster.mover.position[:] = [6, 1, 5]
                    caster.mover.body = None
                self.assertTrue(match.switch_hero(ana, heroes()[0x02E000000000013B]))
                ana.mover.position[:] = [0, 1, 0]
                ana.mover.body = None
                self.throw_biotic_grenade(run, ana, victim)
                vitals = fight.vitals[victim.body]
                self.assertEqual(vitals.now[combat.SHIELDS_POOL], 150)
                deadline = vitals.healing_block_until
                activation = deadline - 40
                self.assertGreater(activation, run.frame)
                if not sparse:
                    for tick in range(run.frame + 1, activation):
                        match.update(tick * combat.TICK, tick, frames=False)
                self.assertEqual(vitals.now[combat.SHIELDS_POOL], 150)

                match.tick = activation
                yaw, pitch = aim(fight._eye(caster), fight._eye(victim)) if projected else (0, 0)
                caster.take_commands([Command(activation + 500, yaw=yaw, pitch=pitch,
                                              buttons=16 if projected else 8)])
                barriers = fight.zarya_barriers
                self.assertTrue(barriers.protects(victim))
                self.assertEqual(vitals.healing_block_until, deadline)
                endpoint = activation + 30
                if not sparse:
                    match.update(activation * combat.TICK, activation, frames=False)
                    self.assertEqual(vitals.healing_block_until, 0)
                    self.assertAlmostEqual(vitals.now[combat.SHIELDS_POOL], 150.48)
                    for tick in range(activation + 1, endpoint):
                        match.update(tick * combat.TICK, tick, frames=False)
                for _ in range(2):
                    match.update(endpoint * combat.TICK, endpoint, frames=False)
                    self.assertAlmostEqual(vitals.now[combat.SHIELDS_POOL], 164.88)
                self.assertEqual(vitals.now[combat.HEALTH_POOL], 200)
                self.assertEqual(vitals.healing_block_until, 0)
                pool = barriers.projected if projected else barriers.active
                self.assertEqual(pool[caster.body].remaining, 200)
                self.assertEqual(expr.to_float(self.energy(caster)), 0)

    def test_actual_rifle_dose_resumes_healing_only_for_unexpired_share_after_antiheal(self):
        run, fight, enemy, _, victim = self.encounter(False, protect=False)
        ally = run.players[2]
        for ana, position in ((enemy, [0, 1, 0]), (ally, [3, 1, 5])):
            self.assertTrue(run.match.switch_hero(ana, heroes()[0x02E000000000013B]))
            ana.mover.position[:] = position
        vitals = fight.vitals_of(next(t for t in fight._targets() if t.owner is victim))
        vitals.damage(250)
        self.throw_biotic_grenade(run, enemy, victim)
        deadline = vitals.healing_block_until
        run.frame = deadline - 12
        run.match.update(run.frame * combat.TICK, run.frame)
        before = vitals.now[combat.HEALTH_POOL]
        self.fire_ana_rifle(run, ally, victim, scoped=False)
        dose, = fight.rifle_doses
        self.assertLess(dose.start_tick, deadline)
        self.assertLess(run.frame, deadline)
        self.assertEqual(vitals.now[combat.HEALTH_POOL], before)
        while run.frame < deadline - 1:
            run.step({})
        self.assertEqual(vitals.now[combat.HEALTH_POOL], before)
        blocked = dose.applied
        self.assertGreater(blocked, 0)
        run.step({})
        self.assertGreater(vitals.now[combat.HEALTH_POOL], before)
        for _ in range(45):
            run.step({})
        self.assertFalse(fight.rifle_doses)
        self.assertAlmostEqual(vitals.now[combat.HEALTH_POOL] - before, 70 - blocked)

    def test_barriers_block_actual_graviton_impact_and_initial_field_damage(self):
        for projected in (False, True):
            with self.subTest(projected=projected):
                run, fight, attacker, caster, victim = self.encounter(projected)
                before = self.health(fight, victim)
                attacker.body_script.set_ult_charge(2100)
                initial_z = victim.mover.position[2]
                run.step({attacker: (0, 0, 0x20)})
                for _ in range(60):
                    run.step({})
                self.assertTrue(fight.zarya_gravitons.fields)
                self.assertEqual(self.health(fight, victim), before)
                self.assertLess(victim.mover.position[2], initial_z)
                self.assertGreater(self.energy(caster), 1)
                for _ in range(110):
                    run.step({})
                self.assertLess(self.health(fight, victim), before)

    def test_primary_admits_bubble_edge_before_body_with_cover_and_team_rules(self):
        for projected in (False, True):
            for case in ("edge", "outside", "ally", "cover", "near_body", "break"):
                with self.subTest(projected=projected, case=case):
                    run = Run(names=("Attacker", "Protected", "Projector", "Other Target"))
                    attacker, victim, projector, other = run.players
                    match = run.match
                    fight = match.combat
                    match.bots.clear()
                    match._pending_bot_scripts.clear()
                    caster = projector if projected else victim
                    for player in (attacker, caster):
                        self.assertTrue(match.switch_hero(player, heroes()[ZARYA]))
                    offset = 2 if case == "outside" else 1
                    protected_team = 0 if case == "ally" else 1
                    for player, team, position in (
                        (attacker, 0, (offset, 1, 0)),
                        (victim, protected_team, (0, 1, 5)),
                        (projector, protected_team, (4, 1, 5)),
                        (other, 1, (offset, 1, 2 if case == "near_body" else 8)),
                    ):
                        player.team = team
                        player.mover.position[:] = position
                        player.mover.body = None
                    angles = aim(fight._eye(caster), fight._eye(victim)) if projected else (0, 0)
                    run.step({caster: (*angles, 16 if projected else 8)})
                    barriers = fight.zarya_barriers
                    self.assertTrue(barriers.protects(victim))
                    pool = (barriers.projected if projected else barriers.active)[caster.body]
                    self.assertEqual(pool.remaining, 200)
                    before = self.health(fight, victim)
                    other_before = self.health(fight, other)
                    if case == "cover":
                        match.collision_world = self.accretion_wall(3)
                    beam = {attacker: (0, 0, 1)}
                    if case == "break":
                        attacker.body_script.component.entity_var(87).base = 100
                        for _ in range(90):
                            run.step(beam)
                            if pool.remaining == 0:
                                break
                        self.assertEqual(pool.remaining, 0)
                        self.assertEqual(self.health(fight, other), other_before)
                        self.assertAlmostEqual(self.energy(caster), 40, places=4)
                        run.step(beam)
                        self.assertAlmostEqual(other_before - self.health(fight, other), 2.72, places=4)
                    else:
                        for _ in range(51):
                            run.step(beam)
                        absorbed = 60 if case == "edge" else 0
                        damage = 60 if case in ("outside", "ally", "near_body") else 0
                        self.assertAlmostEqual(pool.remaining, 200 - absorbed, places=4)
                        self.assertAlmostEqual(other_before - self.health(fight, other), damage, places=4)
                        self.assertAlmostEqual(expr.to_float(self.energy(caster)), absorbed / 5, places=4)
                    self.assertEqual(self.health(fight, victim), before)

    def test_zarya_projectiles_impact_bubble_edge_before_rear_target(self):
        for projected in (False, True):
            for ultimate in (False, True):
                with self.subTest(projected=projected, ultimate=ultimate):
                    run, fight, attacker, caster, victim = self.encounter(projected, protect=False)
                    match = run.match
                    rear = match.add_player(4, "Rear", victim.hero.guid, victim.team, False)
                    in_world(match, rear)
                    rear.client = FakeClient(rear)
                    run.players.append(rear)
                    self.assertTrue(match.switch_hero(rear, heroes()[victim.hero.guid]))
                    rear.mover.position[:] = [1, 1, 9]
                    attacker.mover.position[:] = [1, 1, 0]
                    attacker.mover.body = None
                    if projected:
                        caster.mover.position[:] = [10, 1, 5]
                        caster.mover.body = None
                    angles = aim(fight._eye(caster), fight._eye(victim)) if projected else (0, 0)
                    run.step({caster: (*angles, 16 if projected else 8)})
                    barriers = fight.zarya_barriers
                    self.assertTrue(barriers.protects(victim))
                    pool = (barriers.projected if projected else barriers.active)[caster.body]
                    before = self.health(fight, victim)
                    rear_before = self.health(fight, rear)
                    if ultimate:
                        attacker.body_script.set_ult_charge(2100)
                    run.step({attacker: (0, 0, 32 if ultimate else 2)})
                    rocket, = fight.rockets
                    for _ in range(80):
                        if rocket not in fight.rockets:
                            break
                        run.step({})
                    self.assertNotIn(rocket, fight.rockets)
                    self.assertGreater(rocket.distance, 3)
                    self.assertLess(rocket.distance, 5)
                    absorbed = 5 if ultimate else 47
                    self.assertAlmostEqual(pool.remaining, 200 - absorbed, places=4)
                    self.assertAlmostEqual(self.energy(caster), absorbed / 5, places=4)
                    self.assertEqual(self.health(fight, victim), before)
                    self.assertEqual(self.health(fight, rear), rear_before)
                    self.assertFalse(attacker.client.messages(combat.HIT))
                    if ultimate:
                        field, = fight.zarya_gravitons.fields
                        self.assertIs(field.shooter, attacker)
                        self.assertAlmostEqual(field.position[0], 1, places=4)
                        self.assertLess(field.position[2], victim.mover.position[2])
                        self.assertEqual(attacker.body_script.component.entity_var(342).value(), 0)
                    else:
                        self.assertFalse(fight.zarya_gravitons.fields)

    def test_soldier_rifle_hits_hostile_bubble_surface_before_rear_body(self):
        for projected in (False, True):
            for case in ("edge", "head_height", "outside", "ally", "cover", "break"):
                with self.subTest(projected=projected, case=case):
                    run = Run(names=("Soldier", "Protected", "Projector", "Rear"))
                    attacker, victim, projector, rear = run.players
                    match = run.match
                    fight = match.combat
                    match.bots.clear()
                    match._pending_bot_scripts.clear()
                    caster = projector if projected else victim
                    self.assertTrue(match.switch_hero(caster, heroes()[ZARYA]))
                    offset = 0 if case == "head_height" else 2 if case == "outside" else 1
                    protected_team = 0 if case == "ally" else 1
                    for player, team, position in (
                        (attacker, 0, (offset, 1, 0)),
                        (victim, protected_team, (0, 1, 5)),
                        (projector, protected_team, (4, 1, 5)),
                        (rear, 1, (offset, 1, 9)),
                    ):
                        player.team = team
                        player.mover.position[:] = position
                        player.mover.body = None
                    angles = aim(fight._eye(caster), fight._eye(victim)) if projected else (0, 0)
                    run.step({caster: (*angles, 16 if projected else 8)})
                    barriers = fight.zarya_barriers
                    self.assertTrue(barriers.protects(victim))
                    pool = (barriers.projected if projected else barriers.active)[caster.body]
                    before = self.health(fight, victim)
                    rear_before = self.health(fight, rear)
                    if case == "cover":
                        match.collision_world = self.accretion_wall(3)
                    point = fight._eye(victim) if case == "head_height" else (offset, 2, 9)
                    angles = aim(fight._eye(attacker), point)
                    firing = {attacker: (*angles, 1)}
                    if case == "break":
                        for _ in range(90):
                            run.step(firing)
                            if pool.remaining == 0:
                                break
                        self.assertEqual(pool.remaining, 0)
                        self.assertEqual(self.health(fight, rear), rear_before)
                        self.assertAlmostEqual(self.energy(caster), 40, places=4)
                        for _ in range(12):
                            run.step(firing)
                            if self.health(fight, rear) < rear_before:
                                break
                        self.assertAlmostEqual(rear_before - self.health(fight, rear), 20, places=4)
                    else:
                        run.step(firing)
                        run.step({})
                        absorbed = 20 if case in ("edge", "head_height") else 0
                        damage = 20 if case in ("outside", "ally") else 0
                        self.assertAlmostEqual(pool.remaining, 200 - absorbed, places=4)
                        self.assertAlmostEqual(rear_before - self.health(fight, rear), damage, places=4)
                        self.assertAlmostEqual(expr.to_float(self.energy(caster)), absorbed / 5, places=4)
                    self.assertEqual(self.health(fight, victim), before)

    def test_actual_high_energy_beam_breaks_both_barriers_then_damages_body(self):
        for projected in (False, True):
            with self.subTest(projected=projected):
                run, fight, attacker, caster, victim = self.encounter(projected)
                before = self.health(fight, victim)
                attacker.body_script.component.entity_var(87).base = 100
                for _ in range(100):
                    run.step({attacker: (0, 0, 1)})
                self.assertLess(self.health(fight, victim), before)
                self.assertGreater(self.health(fight, victim), 0)
                self.assertAlmostEqual(self.energy(caster), 40, places=4)
                storage = fight.zarya_barriers.projected if projected else fight.zarya_barriers.active
                self.assertNotIn(caster.body, storage)
                instance = next(i for i in caster.body_script.component.instances.values()
                                if i.graph.index == (0x025C if projected else 0x0244))
                self.assertFalse(instance.state(10 if projected else 2).active)
                self.assertGreater(instance.vars[61].value(), 0)

    def test_overlapping_personal_and_projected_barriers_consume_distinct_beam_ticks(self):
        run, fight, attacker, caster, victim = self.encounter(True, protect=False)
        self.assertTrue(run.match.switch_hero(victim, heroes()[ZARYA]))
        victim.mover.position[:] = [0, 1, 5]
        victim.mover.body = None
        before = self.health(fight, victim)
        attacker.body_script.component.entity_var(87).base = 100
        run.step({victim: (0, 0, 8)})
        beam = {attacker: (0, 0, 1)}
        for _ in range(40):
            run.step(beam)
        personal = fight.zarya_barriers.active[victim.body]
        self.assertGreater(personal.remaining, 0)
        self.assertLess(personal.remaining, 200)

        run.step({**beam, caster: (-16384, 0, 16)})
        projection = next(i for i in caster.body_script.component.instances.values()
                          if i.graph.index == 0x025C)
        self.assertTrue(projection.state(10).active)
        for _ in range(60):
            self.assertNotIn(caster.body, fight.zarya_barriers.projected)
            if personal.remaining == 0:
                break
            run.step(beam)
        self.assertEqual(personal.remaining, 0)
        self.assertEqual(self.health(fight, victim), before)
        self.assertAlmostEqual(self.energy(victim), 40, places=4)
        self.assertEqual(expr.to_float(self.energy(caster)), 0)
        self.assertNotIn(caster.body, fight.zarya_barriers.projected)
        self.assertFalse(attacker.client.messages(combat.HIT))

        run.step(beam)
        projected = fight.zarya_barriers.projected[caster.body]
        self.assertEqual(projected.recipient, victim.body)
        self.assertAlmostEqual(projected.remaining, 197.28, places=4)
        self.assertAlmostEqual(self.energy(caster), 0.544, places=4)
        for _ in range(90):
            if projected.remaining == 0:
                break
            run.step(beam)
        self.assertEqual(projected.remaining, 0)
        self.assertEqual(self.health(fight, victim), before)
        self.assertAlmostEqual(self.energy(caster), 40, places=4)
        self.assertFalse(attacker.client.messages(combat.HIT))
        run.step(beam)
        self.assertAlmostEqual(self.health(fight, victim), before - 2.72, places=4)
        self.assertTrue(attacker.client.messages(combat.HIT))

    def test_same_frame_beams_break_personal_then_hit_projected_barrier(self):
        from test_game_combat import in_world

        run, fight, first, caster, victim = self.encounter(True, protect=False)
        self.assertTrue(run.match.switch_hero(victim, heroes()[ZARYA]))
        second = run.match.add_player(4, "Second attacker", ZARYA, first.team, False)
        in_world(run.match, second)
        run.players.append(second)
        self.assertTrue(run.match.switch_hero(second, heroes()[ZARYA]))
        for player, x, z in ((first, -1.25, 0), (second, 1.25, 0), (victim, 0, 5)):
            player.mover.position[:] = [x, 1, z]
            player.mover.body = None
        before = self.health(fight, victim)
        run.step({victim: (0, 0, 8), caster: (-16384, 0, 16)})
        self.assertTrue(fight.zarya_barriers.protects(victim))
        personal = fight.zarya_barriers.active[victim.body]
        beam = {player: (*aim(fight._eye(player), fight._eye(victim)), 1) for player in (first, second)}
        for _ in range(110):
            remaining_before_frame = personal.remaining
            run.step(beam)
            if caster.body in fight.zarya_barriers.projected:
                break

        projected = fight.zarya_barriers.projected[caster.body]
        self.assertAlmostEqual(remaining_before_frame, 0.8, places=4)
        self.assertEqual(personal.remaining, 0)
        self.assertAlmostEqual(projected.remaining, 198.8, places=4)
        self.assertAlmostEqual(self.energy(victim), 40, places=4)
        self.assertAlmostEqual(self.energy(caster), 0.24, places=4)
        run.step({})
        self.assertAlmostEqual(self.energy(caster), 0.24, places=4)
        self.assertAlmostEqual(projected.remaining, 198.8, places=4)
        for owner, source in ((victim, first), (caster, second)):
            energy = next(i for i in owner.body_script.component.instances.values()
                          if i.graph.index == 0x0242)
            self.assertEqual(energy.vars[392].value(), expr.Entity(source.body))
        self.assertEqual(self.health(fight, victim), before)
        self.assertFalse(first.client.messages(combat.HIT))
        self.assertFalse(second.client.messages(combat.HIT))

    def test_projected_barrier_reabsorbs_after_break_and_cooldown(self):
        run, fight, attacker, caster, victim = self.encounter(True)
        projection = next(i for i in caster.body_script.component.instances.values()
                          if i.graph.index == 0x025C)
        pet = projection.state(10)
        first = pet.counter
        attacker.body_script.component.set(87, 100)
        for _ in range(100):
            run.step({attacker: (0, 0, 1)})
        self.assertFalse(pet.active)
        self.assertAlmostEqual(self.energy(caster), 40, places=4)
        self.assertLess(self.health(fight, victim), 200)

        cast = {caster: (-16384, 0, 16)}
        run.step(cast)
        self.assertEqual(pet.counter, first)
        self.assertFalse(pet.active)
        for _ in range(560):
            run.step({})
            if expr.to_float(projection.vars[61].value()) <= 0:
                break
        self.assertLessEqual(expr.to_float(projection.vars[61].value()), 0)
        run.step(cast)
        self.assertTrue(pet.active)
        self.assertEqual(pet.counter, first + 1)

        attacker.body_script.component.set(87, 0)
        before = self.health(fight, victim)
        run.step({attacker: (0, 0, 2)})
        for _ in range(35):
            previous = self.energy(caster)
            run.step({})
            if caster.body in fight.zarya_barriers.projected:
                break
        self.assertEqual(self.health(fight, victim), before)
        self.assertEqual(fight.zarya_barriers.projected[caster.body].remaining, 153)
        self.assertAlmostEqual(self.energy(caster) - previous, 9.4, delta=0.04)

    def test_replacing_protected_ally_retires_pool_before_next_attack(self):
        run, fight, attacker, caster, victim = self.encounter(True)
        run.step({attacker: (0, 0, 2)})
        for _ in range(30):
            run.step({})
        storage = fight.zarya_barriers.projected
        self.assertEqual(storage[caster.body].remaining, 153)
        old = victim.body
        self.assertEqual(storage[caster.body].recipient, old)
        self.assertEqual(self.health(fight, victim), 200)

        position = tuple(victim.mover.position)
        self.assertTrue(run.match.switch_hero(victim, victim.hero))
        self.assertNotEqual(victim.body, old)
        self.assertNotIn(caster.body, storage)
        victim.mover.position[:] = position
        victim.mover.body = None
        for _ in range(20):
            run.step({attacker: (0, 0, 1)})
        self.assertLess(self.health(fight, victim), 200)
        self.assertNotIn(caster.body, storage)
        self.assertAlmostEqual(self.energy(caster), 9.4, places=4)

    def absorbed_secondary(self):
        run, fight, attacker, caster, victim = self.encounter(False)
        run.step({attacker: (0, 0, 2)})
        for _ in range(30):
            run.step({})
        self.assertAlmostEqual(self.energy(caster), 9.4, places=4)
        energy_graph = next(i for i in caster.body_script.component.instances.values()
                            if i.graph.index == 0x0242)
        wait = energy_graph.state(energy_graph.graph.node(7).state)
        return run, fight, attacker, caster, victim, wait

    def test_actual_barrier_cycles_cap_earned_energy_and_outgoing_beam(self):
        run, fight, attacker, caster, ally = self.encounter(True, protect=False)
        attacker.body_script.component.set(87, 100)
        personal = next(i for i in caster.body_script.component.instances.values()
                        if i.graph.index == 0x0244)
        self.assertEqual(expr.to_float(self.energy(caster)), 0)
        before = {target: self.health(fight, target) for target in (caster, ally)}

        for projected, recipient in ((False, caster), (True, ally), (False, caster)):
            if not projected:
                for _ in range(700):
                    if expr.to_float(personal.vars[61].value()) <= 0:
                        break
                    run.step({})
                self.assertLessEqual(expr.to_float(personal.vars[61].value()), 0)
            run.step({caster: (-16384, 0, 16) if projected else (0, 0, 8)})
            self.assertTrue(fight.zarya_barriers.protects(recipient))
            storage = fight.zarya_barriers.projected if projected else fight.zarya_barriers.active
            pool = storage[caster.body]
            self.assertEqual(pool.remaining, 200)
            energy_before = expr.to_float(self.energy(caster))
            angles = aim(fight._eye(attacker), fight._eye(recipient))
            for _ in range(90):
                run.step({attacker: (*angles, 1)})
                if pool.remaining == 0:
                    break
            self.assertEqual(pool.remaining, 0)
            self.assertEqual(self.health(fight, recipient), before[recipient])
            self.assertAlmostEqual(self.energy(caster), min(100, energy_before + 40), delta=0.05)

        self.assertGreater(energy_before + 40, 100)
        self.assertEqual(self.energy(caster), 100)
        self.assertFalse(attacker.client.messages(combat.HIT))
        enemy_before = self.health(fight, attacker)
        angles = aim(fight._eye(caster), fight._eye(attacker))
        for _ in range(51):
            run.step({caster: (*angles, 1)})
        self.assertEqual(self.energy(caster), 100)
        self.assertAlmostEqual(enemy_before - self.health(fight, attacker), 136, places=4)

    def test_one_caster_caps_same_frame_energy_and_capped_absorption_restarts_decay(self):
        for starting in (50, 95):
            with self.subTest(starting=starting):
                run, fight, first, caster, ally = self.encounter(True, protect=False)
                match = run.match
                second = match.add_player(4, "Second attacker", ZARYA, first.team, False)
                rifle = match.add_player(5, "Later attacker", SOLDIER, first.team, False)
                for actor in (second, rifle):
                    in_world(match, actor)
                    run.players.append(actor)
                for actor in (ally, second):
                    self.assertTrue(match.switch_hero(actor, heroes()[ZARYA]))
                self.assertTrue(match.switch_hero(rifle, heroes()[SOLDIER]))
                poses = (
                    (first, (0, 1, 0)), (second, (8, 1, 0)), (caster, (0, 1, 5)),
                    (ally, (8, 1, 5)), (rifle, (0, 1, -2)),
                )
                for actor, point in poses:
                    actor.mover.position[:] = point
                    actor.mover.body = None
                run.step({caster: (16384, 0, 8 | 16)})
                barriers = fight.zarya_barriers
                self.assertTrue(barriers.protects(caster))
                self.assertTrue(barriers.protects(ally))
                personal = barriers.active[caster.body]
                projection = barriers.projected[caster.body]
                self.assertEqual(projection.recipient, ally.body)
                script = caster.body_script
                script.component.set(87, starting)
                energy_graph = next(i for i in script.component.instances.values() if i.graph.index == 0x0242)
                wait = energy_graph.state(energy_graph.graph.node(7).state)
                run.step({first: (0, 0, 2), second: (0, 0, 2)})
                self.assertEqual(len(fight.rockets), 2)
                for _ in range(40):
                    run.step({})
                    if personal.remaining < 200 or projection.remaining < 200:
                        break
                self.assertEqual((personal.remaining, projection.remaining), (153, 153))
                self.assertAlmostEqual(self.energy(caster), min(100, starting + 18.8), places=4)
                self.assertEqual(energy_graph.vars[392].value(), expr.Entity(second.body))
                self.assertEqual(self.health(fight, caster), 400)
                self.assertEqual(self.health(fight, ally), 400)
                self.assertTrue(wait.active)
                if starting < 95:
                    continue

                first_deadline = wait.start + 1000
                for _ in range(25):
                    run.step({})
                angles = aim(fight._eye(rifle), fight._eye(caster))
                for _ in range(16):
                    run.step({rifle: (*angles, 1)})
                    if personal.remaining < 153:
                        break
                self.assertEqual((personal.remaining, projection.remaining), (133, 153))
                run.step({})
                self.assertEqual(self.energy(caster), 100)
                self.assertEqual(energy_graph.vars[392].value(), expr.Entity(rifle.body))
                self.assertGreater(wait.start + 1000, first_deadline)
                while script.component.now <= first_deadline + 100:
                    run.step({})
                    self.assertEqual(self.energy(caster), 100)
                while script.component.now < wait.start + 1200:
                    run.step({})
                self.assertLess(self.energy(caster), 100)
                self.assertEqual(self.health(fight, caster), 400)

    def test_energy_waits_one_second_then_decays_at_authored_1_8_per_second(self):
        run, _, _, caster, _, wait = self.absorbed_secondary()
        component = caster.body_script.component
        while component.now < wait.start + 900:
            run.step({})
        self.assertAlmostEqual(self.energy(caster), 9.4, places=4)
        while component.now < wait.start + 1200:
            run.step({})
        before, now = self.energy(caster), component.now
        self.assertLess(before, 9.4)
        for _ in range(63):
            run.step({})
        # The shared ChaseVar applies its native-style epsilon and float32 steps.
        self.assertAlmostEqual(before - self.energy(caster),
                               1.8 * (component.now - now) / 1000, delta=0.003)

    def test_melee_uses_admitted_world_tick_when_barrier_scheduler_is_behind(self):
        for projected, expired in product((False, True), repeat=2):
            with self.subTest(projected=projected, expired=expired):
                run, fight, attacker, caster, victim = self.encounter(projected, close=True)
                match = run.match
                expiry = fight.zarya_barriers.expiries[caster.body, projected]
                tick = expiry.tick + 5 if expired else fight.tick + 20
                before = self.health(fight, victim)
                match.tick = tick
                attacker.take_commands([Command(tick, buttons=0x800)])

                self.assertEqual(fight.tick, run.frame)
                self.assertEqual(self.health(fight, victim), before)
                self.assertEqual(expr.to_float(self.energy(caster)), 0)
                match.update(tick * combat.TICK, tick, frames=False)
                self.assertAlmostEqual(before - self.health(fight, victim), 30 if expired else 0)
                self.assertAlmostEqual(expr.to_float(self.energy(caster)), 0 if expired else 6)
                if not expired:
                    energy = next(i for i in caster.body_script.component.instances.values()
                                  if i.graph.index == 0x0242)
                    wait = energy.state(energy.graph.node(7).state)
                    self.assertEqual(wait.world_deadline, tick + 63)

    def test_scoped_rifle_uses_admitted_world_tick_after_projection_expires(self):
        run, fight, ana, caster, victim = self.encounter(True)
        match = run.match
        self.assertTrue(match.switch_hero(ana, heroes()[0x02E000000000013B]))
        ana.mover.position[:] = (0, 1, 0)
        expiry = fight.zarya_barriers.expiries[caster.body, True]
        tick = expiry.tick + 5
        match.tick = tick
        x, y, z = victim.mover.position
        yaw, pitch = aim(fight._eye(ana), (x, y + 1, z))
        ana.take_commands([
            Command(frame, yaw=yaw, pitch=pitch, buttons=3 if frame == tick else 2)
            for frame in range(tick - 30, tick + 1)
        ])
        self.assertEqual(fight.tick, run.frame)
        self.assertFalse(fight.rockets)
        dose, = fight.rifle_doses
        self.assertEqual((dose.target, dose.start_tick), (victim.body, tick))
        self.assertEqual(expr.to_float(self.energy(caster)), 0)
        before = self.health(fight, victim)
        match.update(tick * combat.TICK, tick, frames=False)
        self.assertEqual(self.health(fight, victim), before)
        end = tick + 40
        match.update(end * combat.TICK, end, frames=False)
        self.assertAlmostEqual(before - self.health(fight, victim), 70)

    def test_energy_decay_advances_on_world_ticks_without_owner_commands(self):
        outcomes = []
        for silent in (False, True):
            run, fight, _, caster, _, _ = self.absorbed_secondary()
            self.assertAlmostEqual(self.energy(caster), 9.4, places=4)
            tick = fight.tick + 180
            if silent:
                run.match.update(tick * combat.TICK, tick, frames=False)
            else:
                while run.frame < tick:
                    run.step({})
            outcomes.append(self.energy(caster))
        self.assertAlmostEqual(outcomes[0], 5.4, delta=0.06)
        self.assertAlmostEqual(outcomes[1], outcomes[0], delta=0.02)

    def test_future_neutral_input_does_not_decay_barrier_energy_early(self):
        run, fight, _, caster, _, _ = self.absorbed_secondary()
        tick = fight.tick
        caster.take_commands([Command(tick + 500)])
        self.assertAlmostEqual(self.energy(caster), 9.4, places=4)
        run.match.update((tick + 180) * combat.TICK, tick + 180, frames=False)
        self.assertAlmostEqual(self.energy(caster), 5.4, delta=0.06)

    def test_first_weapon_input_after_long_silence_uses_elapsed_energy_decay(self):
        for buttons in (1, 2):
            with self.subTest(buttons=buttons):
                run, fight, attacker, caster, _ = self.encounter(False)
                match = run.match
                self.assertTrue(fight.zarya_barriers.protects(caster))
                pool = fight.zarya_barriers.active[caster.body]
                start = run.frame
                launch = start + 110
                match.update(launch * combat.TICK, launch, frames=False)
                attacker.take_commands([Command(launch, buttons=2, extra=0)])
                self.assertEqual(len(fight.rockets), 1)
                for impact in range(launch + 1, launch + 15):
                    match.update(impact * combat.TICK, impact, frames=False)
                    if not fight.rockets:
                        break
                self.assertEqual(pool.remaining, 153)
                self.assertEqual(caster.body_script.frame, start)
                expired = start + 129
                match.update(expired * combat.TICK, expired, frames=False)
                self.assertFalse(fight.zarya_barriers.protects(caster))
                self.assertAlmostEqual(self.energy(caster), 9.4, places=4)

                resumed = impact + 430
                before = self.health(fight, attacker)
                match.update(resumed * combat.TICK, resumed, frames=False)
                yaw, pitch = aim(fight._eye(caster), fight._eye(attacker))
                caster.take_commands([Command(resumed, yaw=yaw, pitch=pitch, buttons=buttons, extra=0)])
                self.assertEqual(self.energy(caster), 0)
                if buttons == 1:
                    self.assertEqual(self.health(fight, attacker), before)
                    match.update((resumed + 1) * combat.TICK, resumed + 1, frames=False)
                    caster.take_commands([Command(resumed + 1, yaw=yaw, pitch=pitch, buttons=1, extra=0)])
                    self.assertAlmostEqual(before - self.health(fight, attacker), 1.2)
                else:
                    rocket, = fight.rockets
                    self.assertEqual(rocket.blast.amount, 47)

    def test_later_absorption_restarts_delay_and_pauses_existing_decay(self):
        run, _, attacker, caster, _, wait = self.absorbed_secondary()
        first_start = wait.start
        while caster.body_script.component.now < first_start + 1200:
            run.step({})
        before = self.energy(caster)
        self.assertLess(before, 9.4)
        run.step({attacker: (0, 0, 2)})
        before_hit = self.energy(caster)
        for _ in range(30):
            before_hit = self.energy(caster)
            run.step({})
            if wait.start > first_start:
                break
        self.assertGreater(wait.start, first_start)
        # Decay continues during the second projectile's flight; only its actual
        # absorption restarts the delay. Allow one final16ms chase step at impact.
        self.assertAlmostEqual(self.energy(caster) - before_hit, 9.4, delta=0.04)
        after = self.energy(caster)
        for _ in range(50):
            run.step({})
        self.assertEqual(self.energy(caster), after)

    def test_entity_flag_232_pauses_and_resumes_energy_decay(self):
        run, _, _, caster, _, wait = self.absorbed_secondary()
        graph = wait.instance
        component = caster.body_script.component
        # Exercise the authored consumer. The native owner of this input is unresolved.
        graph.write((expr.ENTITY, 232), True)
        while component.now < wait.start + 1200:
            run.step({})
        self.assertAlmostEqual(self.energy(caster), 9.4, places=4)

        graph.write((expr.ENTITY, 232), False)
        for _ in range(45):
            run.step({})
        self.assertLess(self.energy(caster), 9.4)

        graph.write((expr.ENTITY, 232), True)
        run.step({})
        paused = self.energy(caster)
        for _ in range(45):
            run.step({})
        self.assertEqual(self.energy(caster), paused)

        graph.write((expr.ENTITY, 232), False)
        for _ in range(45):
            run.step({})
        self.assertLess(self.energy(caster), paused)

    def test_energy_decay_clamps_at_zero_and_does_not_become_negative(self):
        run, _, _, caster, _, _ = self.absorbed_secondary()
        for _ in range(450):
            run.step({})
        self.assertEqual(self.energy(caster), 0)
        for _ in range(100):
            run.step({})
        self.assertEqual(self.energy(caster), 0)

    def test_death_and_body_replacement_discard_absorbed_energy_and_pool(self):
        run, fight, attacker, caster, victim, _ = self.absorbed_secondary()
        target = next(t for t in fight._targets() if t.owner is victim)
        fight._damage(attacker, target, 500, False, None, run.frame)
        fight._damage(attacker, target, 500, False, None, run.frame)
        self.assertTrue(fight.is_dead(caster))
        old = caster.body
        self.assertTrue(run.match.switch_hero(caster, heroes()[ZARYA]))
        self.assertNotEqual(caster.body, old)
        self.assertEqual(expr.to_float(self.energy(caster)), 0)
        self.assertNotIn(old, fight.zarya_barriers.active)


if __name__ == "__main__":
    unittest.main()
