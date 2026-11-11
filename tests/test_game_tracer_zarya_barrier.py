"""Actual Tracer attacks against Zarya personal and projected barriers."""

import unittest
from dataclasses import replace

import test_game_tracer as tracer_tests
import test_game_tracer_pulse_deflect as pulse_tests
import test_game_tracer_sigma_accretion as accretion_tests
from test_game_combat import aim
from test_game_mover import box

from ow174.game import combat, zarya_barrier
from ow174.game.collision import World
from ow174.game.commands import FIRE, Command
from ow174.game.content import heroes
from ow174.game.script.driver import ULT_COST

ZARYA = 0x02E0000000000068


class TracerZaryaBarrierTests(unittest.TestCase):
    def setUp(self):
        self.fixture = tracer_tests.TracerPistolTests()
        self.run, self.tracer, self.zarya, _ = self.fixture.fight()
        self.fight = self.run.match.combat
        self.assertTrue(self.run.match.switch_hero(self.zarya, heroes()[ZARYA]))
        self.place(self.zarya, 0, 3)
        self.target = self.zarya
        self.vitals = self.health(self.target)
        for _ in range(30):
            self.run.step({})

    def place(self, player, dx, dz):
        x, y, z = self.tracer.mover.position
        player.mover.position = [x + dx, y, z + dz]
        player.mover.body = None

    def health(self, player):
        return self.fight.vitals_of(next(t for t in self.fight._targets() if t.owner is player))

    def energy(self):
        return self.zarya.body_script.component.entity_var(87).value() or 0

    def activate(self, buttons=8, facing=(-32768, 0)):
        self.run.step({self.zarya: (*facing, 0)})
        self.run.step({self.zarya: (*facing, buttons)})
        pet = zarya_barrier.active(self.target)
        self.assertIsNotNone(pet)
        self.assertEqual(zarya_barrier.barrier_health(pet, self.vitals), 200)
        return pet

    def test_personal_barrier_spends_pistol_damage_and_credits_energy(self):
        pet = self.activate()
        self.fixture.fire(self.run, self.tracer, self.zarya)
        self.assertEqual(self.vitals.total, 400)
        self.assertEqual(zarya_barrier.barrier_health(pet, self.vitals), 188)
        self.assertAlmostEqual(self.energy(), 2.4, places=5)
        self.assertFalse(self.tracer.client.messages(combat.HIT))
        for _ in range(135):
            self.run.step({})
        self.assertIsNone(zarya_barrier.active(self.zarya))
        self.assertEqual(self.vitals.particle_barriers, {})
        self.fixture.fire(self.run, self.tracer, self.zarya)
        self.assertEqual(self.vitals.total, 388)
        self.run.step({self.zarya: (0, 0, 8)})
        self.assertIsNone(zarya_barrier.active(self.zarya))
        for _ in range(650):
            self.run.step({})
        self.activate()
        self.fixture.fire(self.run, self.tracer, self.zarya)
        self.assertEqual(self.vitals.total, 388)

    def test_first_pellet_breaks_barrier_second_hits_body(self):
        pet = self.activate()
        self.vitals.particle_barriers[zarya_barrier.particle_barrier_key(pet)] = 6
        self.fixture.fire(self.run, self.tracer, self.zarya)
        self.assertIsNone(zarya_barrier.active(self.zarya))
        self.assertEqual(self.vitals.total, 394)
        self.assertAlmostEqual(self.energy(), 1.2, places=5)
        self.assertEqual(len(self.tracer.client.messages(combat.HIT)), 1)

    def test_projected_barrier_chooses_ally_and_credits_caster(self):
        self.run.match.game_map = replace(self.run.match.game_map, free_for_all=False)
        self.tracer.team, self.zarya.team = 0, 1
        ally, _ = pulse_tests.PulseDeflectTests().support(self.run, self.zarya)
        ally.team = self.zarya.team
        self.place(self.zarya, 6, 4)
        self.place(ally, 0, 4)
        self.target, self.vitals = ally, self.health(ally)
        for _ in range(30):
            self.run.step({})
        facing = aim(self.fight._eye(self.zarya), self.fight._eye(ally))
        pet = self.activate(16, facing)
        self.assertEqual(pet.instance.component.entity, self.zarya.body)
        self.assertIsNone(zarya_barrier.active(self.zarya))
        self.fixture.fire(self.run, self.tracer, ally)
        self.assertEqual(self.vitals.total, 200)
        self.assertEqual(zarya_barrier.barrier_health(pet, self.vitals), 188)
        self.assertAlmostEqual(self.energy(), 2.4, places=5)
        self.assertTrue(self.run.match.switch_hero(ally, ally.hero))
        self.assertIsNone(zarya_barrier.active(ally))

    def test_pulse_contact_and_fuse_spend_barrier_without_body_spill(self):
        pet = self.activate()
        script = self.tracer.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        self.run.step({self.tracer: (0, 0, 32)})
        bombs = self.fight.pulse_bombs.bombs
        for _ in range(55):
            if bombs and bombs[0].stuck:
                break
            self.run.step({})
        self.assertTrue(bombs and bombs[0].stuck)
        self.assertEqual(bombs[0].attached_particle, zarya_barrier.particle_barrier_key(pet))
        self.assertEqual(zarya_barrier.barrier_health(pet, self.vitals), 195)
        self.assertEqual(self.vitals.total, 400)
        for _ in range(68):
            self.run.step({})
        self.assertFalse(bombs)
        self.assertIsNone(zarya_barrier.active(self.zarya))
        self.assertEqual(self.vitals.total, 400)
        self.assertAlmostEqual(self.energy(), 40, delta=0.02)

    def test_melee_spends_barrier_without_body_damage(self):
        self.place(self.zarya, 0, 2)
        pet = self.activate()
        self.run.step({self.tracer: (0, 0, 0x800)})
        for _ in range(28):
            self.run.step({})
        self.assertEqual(self.vitals.total, 400)
        self.assertEqual(zarya_barrier.barrier_health(pet, self.vitals), 170)
        self.assertAlmostEqual(self.energy(), 6, places=5)
        tracer_health = self.health(self.tracer)
        yaw, pitch = aim(self.fight._eye(self.zarya), self.fight._eye(self.tracer))
        for _ in range(10):
            self.run.step({self.zarya: (yaw, pitch, FIRE)})
        self.assertAlmostEqual(tracer_health.total, 150 - 12.912, places=4)

    def test_projected_barrier_rejects_enemy_range_and_wall(self):
        self.run.match.game_map = replace(self.run.match.game_map, free_for_all=False)
        self.tracer.team, self.zarya.team = 0, 1
        ally, _ = pulse_tests.PulseDeflectTests().support(self.run, self.zarya)
        ally.team = self.zarya.team
        self.place(self.zarya, 6, 4)
        self.place(ally, 0, 4)
        for _ in range(25):
            self.run.step({})

        def attempt(target):
            facing = aim(self.fight._eye(self.zarya), self.fight._eye(target))
            self.run.step({self.zarya: (*facing, 0)})
            self.run.step({self.zarya: (*facing, 16)})

        attempt(self.tracer)
        self.assertIsNone(zarya_barrier.active(self.tracer))
        self.assertIsNone(zarya_barrier.active(ally))
        self.place(ally, -32, 4)
        attempt(ally)
        self.assertIsNone(zarya_barrier.active(ally))
        self.place(ally, 0, 4)
        original = self.run.match.collision_world
        x, y, z = self.tracer.mover.position
        floor = [(-5000, y, -5000, -5000, y, 5000, 5000, y, 5000),
                 (-5000, y, -5000, 5000, y, 5000, 5000, y, -5000)]
        world = World(floor + box(x + 2.9, x + 3.1, y, y + 4, z + 1, z + 7))
        self.run.match.collision_world = world
        for player in self.run.players:
            player.mover.use(world=world)
        attempt(ally)
        self.assertIsNone(zarya_barrier.active(ally))
        self.run.match.collision_world = original
        for player in self.run.players:
            player.mover.use(world=original)
        attempt(ally)
        self.assertIsNotNone(zarya_barrier.active(ally))

    def test_pulse_contact_breaks_weak_barrier_and_detaches_without_resetting_fuse(self):
        pet = self.activate()
        self.vitals.particle_barriers[zarya_barrier.particle_barrier_key(pet)] = 5
        script = self.tracer.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        self.run.step({self.tracer: (0, 0, 32)})
        bombs = self.fight.pulse_bombs.bombs
        for _ in range(55):
            if bombs and bombs[0].stuck:
                break
            self.run.step({})
        self.assertTrue(bombs and bombs[0].stuck)
        self.assertIsNone(zarya_barrier.active(self.zarya))
        self.assertEqual(self.vitals.total, 400)
        bomb, = bombs
        position = bomb.position
        before = bomb.fuse_left
        for _ in range(65):
            self.run.frame += 1
            frame = self.run.frame
            self.run.match.update(frame * 0.016, frame)
            self.zarya.take_commands([Command(frame, yaw=0, forward=127)])
            self.tracer.take_commands([Command(frame)])
        self.assertFalse(bombs)
        self.assertIsNone(bomb.attached)
        self.assertIsNone(bomb.attached_particle)
        self.assertEqual(bomb.position, position)
        self.assertLess(bomb.fuse_left, before)
        self.assertEqual(self.vitals.total, 400)

    def test_earthshatter_spends_projected_barrier_without_stunning_tracer(self):
        for distance, capacity in ((7, 200), (7, 30), (3, 200)):
            with self.subTest(distance=distance, capacity=capacity):
                self.setUp()
                self.check_earthshatter_projected_barrier(distance, capacity)

    def check_earthshatter_projected_barrier(self, distance, capacity):
        self.run.match.game_map = replace(self.run.match.game_map, free_for_all=False)
        self.tracer.team = self.zarya.team = 0
        caster, _ = pulse_tests.PulseDeflectTests().support(
            self.run, self.tracer, hero=0x02E0000000000007,
        )
        caster.team = 1
        self.place(caster, 0, -distance)
        self.place(self.zarya, 6, 0)
        self.target = self.tracer
        self.vitals = self.health(self.tracer)
        facing = aim(self.fight._eye(self.zarya), self.fight._eye(self.tracer))
        pet = self.activate(16, facing)
        self.vitals.particle_barriers[zarya_barrier.particle_barrier_key(pet)] = capacity
        script = caster.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        wave = None
        for index in range(72):
            self.run.step({caster: (0, 0, 32 if index == 0 else 0)})
            wave = self.fight.earthshatters.waves.get(caster.body) or wave

        self.assertIsNotNone(wave)
        self.assertIn(self.tracer.body, wave.hit_bodies)
        self.assertNotIn(caster.body, self.fight.earthshatters.waves)
        self.assertEqual(self.vitals.total, 150)
        self.assertFalse(self.fight.is_stunned(self.tracer))
        damage = 250 if distance == 3 else 50
        if capacity > damage:
            self.assertIs(zarya_barrier.active(self.tracer), pet)
            self.assertEqual(zarya_barrier.barrier_health(pet, self.vitals), capacity - damage)
        else:
            self.assertIsNone(zarya_barrier.active(self.tracer))
        self.assertAlmostEqual(self.energy(), min(capacity, damage) * 0.2, places=5)

    def test_projected_barrier_blocks_a_breaking_accretion_and_its_stun(self):
        case = accretion_tests.TracerSigmaAccretionTests()
        case.setUp()
        case.run.match.game_map = replace(case.run.match.game_map, free_for_all=False)
        case.tracer.team, case.sigma.team = 0, 1
        zarya, _ = pulse_tests.PulseDeflectTests().support(case.run, case.tracer, hero=ZARYA)
        zarya.team = case.tracer.team
        x, y, z = case.tracer.mover.position
        zarya.mover.position = [x + 6, y, z]
        zarya.mover.body = None
        for _ in range(30):
            case.step()
        facing = aim(case.fight._eye(zarya), case.fight._eye(case.tracer))
        case.run.step({zarya: (*facing, 0)})
        case.run.step({zarya: (*facing, 16)})
        pet = zarya_barrier.active(case.tracer)
        self.assertIsNotNone(pet)
        case.vitals.particle_barriers[zarya_barrier.particle_barrier_key(pet)] = 30
        case.launch()
        for _ in range(42):
            if not case.fight.rockets:
                break
            case.step()
        self.assertFalse(case.fight.rockets)
        self.assertIsNone(zarya_barrier.active(case.tracer))
        self.assertEqual(case.vitals.total, 150)
        self.assertNotIn(case.tracer.body, case.fight.stuns)
        self.assertFalse(case.tracer.mover.external_impulse)
        self.assertAlmostEqual(zarya.body_script.component.entity_var(87).value(), 6, places=5)
