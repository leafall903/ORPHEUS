"""Actual Orisa Halt casts and Tracer responses under build 104319 graphs."""

import os
import unittest
from dataclasses import replace

import test_game_tracer as tracer_tests
import test_game_tracer_dva_mech as mech_tests
import test_game_tracer_pulse_deflect as support_tests
from test_game_combat import aim
from test_game_mover import box

from ow174.game import deflection, dva, halt, orisa_barrier, sigma_grasp, tracer
from ow174.game.collision import StaticCollisionProvider, World
from ow174.game.commands import SECONDARY
from ow174.game.content import SOLDIER, find_hero, heroes


@unittest.skipUnless(os.environ.get("ORPHEUS_ORISA_HALT"), "verified 104319 Halt supplement required")
class TracerOrisaHaltTests(unittest.TestCase):
    def setUp(self):
        self.run, self.tracer, self.orisa, _ = tracer_tests.TracerPistolTests().fight()
        self.match = self.run.match
        self.fight = self.match.combat
        self.halt = self.fight.halt
        self.assertTrue(self.match.switch_hero(self.orisa, heroes()[orisa_barrier.HERO]))
        x, y, z = self.tracer.mover.position
        self.origin = (x, y, z - 6)
        self.orisa.mover.position[:] = self.origin
        self.orisa.mover.body = None
        for _ in range(32):
            self.run.step({})
        self.vitals = self.fight.vitals_of(next(t for t in self.fight._targets() if t.owner is self.tracer))

    def launch(self, *, early_press=False):
        self.run.step({self.orisa: (0, 0, SECONDARY)})
        for frame in range(1, 25):
            self.run.step({self.orisa: (0, 0, SECONDARY if early_press and frame == 15 else 0)})
        orb, = self.halt.orbs.values()
        self.assertIsNone(orb.requested)
        self.assertEqual(orb.rocket.definition, halt.PROJECTILE)
        return orb

    def detonate(self, orb):
        self.run.step({self.orisa: (0, 0, SECONDARY)})
        self.assertEqual(orb.requested, self.fight.tick)

    def test_armed_second_input_freezes_orb_then_pulls_tracer_without_damage(self):
        start = tuple(self.tracer.mover.position)
        orb = self.launch(early_press=True)
        self.detonate(orb)
        centre = orb.rocket.position
        for _ in range(24):
            self.run.step({})
        self.assertEqual(orb.rocket.position, centre)
        self.assertEqual(tuple(self.tracer.mover.position), start)
        pull = self.halt.pulls[self.tracer.body]
        self.assertEqual(pull.script.graph.index, 0x0CCE)
        for _ in range(20):
            self.run.step({})
        self.assertGreater(self.tracer.mover.position[2], start[2] + 0.8)
        self.assertEqual(self.vitals.total, 150)
        for _ in range(90):
            self.run.step({})
        self.assertFalse(self.halt.orbs)
        self.assertFalse(self.halt.pulls)
        self.assertTrue(pull.script.stopped)

    def test_recall_cancels_existing_halt_before_pull_motion(self):
        orb = self.launch()
        self.detonate(orb)
        self.run.step({})
        self.assertIn(self.tracer.body, self.halt.pulls)
        before = tuple(self.tracer.mover.position)
        self.run.step({self.tracer: (0, 0, 16)})
        self.assertTrue(tracer.recalling(self.tracer))
        for _ in range(65):
            self.run.step({})
        self.assertFalse(self.halt.pulls)
        self.assertEqual(tuple(self.tracer.mover.position), before)
        self.assertEqual(self.vitals.total, 150)

    def test_blink_before_detonation_leaves_the_acquisition_radius(self):
        orb = self.launch()
        x = self.tracer.mover.position[0]
        self.run.step({self.tracer: (16384, 0, 8)})
        self.assertGreater(self.tracer.mover.position[0], x + 7)
        self.detonate(orb)
        after_blink = tuple(self.tracer.mover.position)
        for _ in range(80):
            self.run.step({})
            self.assertNotIn(self.tracer.body, self.halt.pulls)
        self.assertEqual(tuple(self.tracer.mover.position), after_blink)
        self.assertEqual(self.vitals.total, 150)

    def test_cooldown_rejects_fresh_input_then_allows_another_launch(self):
        orb = self.launch()
        self.detonate(orb)
        for _ in range(75):
            self.run.step({})
        self.assertFalse(self.halt.orbs)
        instances = self.orisa.body_script.component.instances.values()
        instance = next(i for i in instances if i.graph.index == 0x0CCB)
        ability = instance.states[2]
        self.assertGreater(ability.remaining(), 0)
        self.run.step({self.orisa: (0, 0, SECONDARY)})
        for _ in range(400):
            self.run.step({})
            self.assertFalse(self.halt.orbs)
            if not ability.remaining():
                break
        self.assertEqual(ability.remaining(), 0)
        second = self.launch()
        self.assertNotEqual(orb.entity, second.entity)

    def test_switching_caster_body_retires_unarmed_flight(self):
        orb = self.launch()
        self.assertTrue(self.match.switch_hero(self.orisa, heroes()[SOLDIER]))
        self.assertFalse(self.halt.orbs)
        self.assertNotIn(orb.rocket, self.fight.rockets)
        for _ in range(65):
            self.run.step({})
        self.assertFalse(self.halt.pulls)
        self.assertEqual(self.vitals.total, 150)

    @unittest.skipUnless(deflection.TAGS_PATH, "verified projectile tags required")
    def test_matrix_and_grasp_consume_halt_without_a_pull(self):
        mech_tests.TracerDvaMechTests.setUpClass()
        for hero, button in ((dva.HERO, SECONDARY), (sigma_grasp.HERO, 8)):
            with self.subTest(hero=hero):
                self.setUp()
                self.check_halt_absorption(hero, button)

    def check_halt_absorption(self, hero, button):
        defender, facing = self.defender(hero)
        self.run.step({defender: (*facing, button), self.orisa: (0, 0, SECONDARY)})
        seen = None
        for _ in range(65):
            self.run.step({defender: (*facing, button if hero == dva.HERO else 0)})
            if self.halt.orbs:
                seen, = self.halt.orbs.values()
            if seen is not None and seen.entity not in self.halt.orbs:
                break
        self.assertIsNotNone(seen)
        self.assertFalse(self.halt.orbs)
        self.assertFalse(self.fight.rockets)
        self.assertFalse(self.halt.pulls)
        self.assertEqual(self.vitals.total, 150)

    @unittest.skipUnless(deflection.TAGS_PATH, "verified projectile tags required")
    def test_deflect_transfers_the_halt_pull_to_genji_team(self):
        genji, facing = self.defender(0x02E0000000000029)
        x, y, z = self.origin
        world = World(box(x - 24, x + 24, y - 2, y, z - 24, z + 24)
                      + box(x - 8, x + 8, y, y + 5, z - 2.1, z - 2))
        self.match.collision_provider = StaticCollisionProvider(world)
        self.match.collision_world = world
        for player in self.run.players:
            player.mover.use(world=world)
        self.run.step({genji: (*facing, 16), self.orisa: (0, 0, SECONDARY)})
        orb = None
        for _ in range(65):
            self.run.step({genji: (*facing, 0)})
            if self.halt.orbs:
                orb, = self.halt.orbs.values()
            if orb is not None and orb.requested is not None:
                break
        self.assertIsNotNone(orb)
        self.assertIs(orb.rocket.shooter, genji)
        self.assertEqual(orb.rocket.halt_body, genji.body)
        self.assertEqual(orb.rocket.team, self.tracer.team)
        self.assertIsNotNone(orb.requested)
        self.run.step({})
        self.assertIn(self.orisa.body, self.halt.pulls)
        self.assertNotIn(self.tracer.body, self.halt.pulls)
        for _ in range(60):
            self.run.step({})
        self.assertLess(self.orisa.mover.position[2], z - 0.8)
        self.assertEqual(self.vitals.total, 150)

    def defender(self, hero):
        defender, _ = support_tests.PulseDeflectTests().support(self.run, self.tracer, hero=hero)
        if hero == dva.HERO:
            self.assertTrue(self.match.switch_hero(defender, replace(find_hero("dva"), body=dva.MECH)))
        self.match.game_map = replace(self.match.game_map, free_for_all=False)
        self.tracer.team = defender.team = 0
        self.orisa.team = 1
        x, y, z = self.origin
        defender.mover.position[:] = (x, y, z + 3)
        defender.mover.body = None
        for _ in range(110 if hero == dva.HERO else 30):
            self.run.step({})
        return defender, aim(self.fight._eye(defender), self.fight._eye(self.orisa))

    def test_ally_and_wall_exclude_actual_tracer_acquisition(self):
        for gate in ("ally", "wall"):
            with self.subTest(gate=gate):
                self.setUp()
                orb = self.launch()
                if gate == "ally":
                    self.match.game_map = replace(self.match.game_map, free_for_all=False)
                    self.tracer.team = self.orisa.team
                else:
                    x, y, z = self.origin
                    split = (self.tracer.mover.position[2] + orb.rocket.position[2]) / 2
                    world = World(box(x - 24, x + 24, y - 2, y, z - 24, z + 24)
                                  + box(x - 10, x + 10, y, y + 5, split - 0.1, split + 0.1))
                    self.match.collision_provider = StaticCollisionProvider(world)
                    self.match.collision_world = world
                    for player in self.run.players:
                        player.mover.use(world=world)
                self.detonate(orb)
                for _ in range(65):
                    self.run.step({})
                    self.assertNotIn(self.tracer.body, self.halt.pulls)
                self.assertEqual(self.vitals.total, 150)
