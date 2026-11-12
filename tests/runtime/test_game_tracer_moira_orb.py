"""Actual Biotic Orb casts and effects in Tracer matches."""

from dataclasses import replace

import test_game_tracer_pulse_deflect as support_tests
from test_game_combat import aim
from test_game_mover import box
from test_game_tracer_moira import MoiraArena

from ow174.game import combat, deflection, dva, moira_orb, sigma_grasp, tracer
from ow174.game.collision import StaticCollisionProvider, World
from ow174.game.commands import FIRE
from ow174.game.content import find_hero
from ow174.game.mover import defined_mover_data
from ow174.game.script import graph


class TracerMoiraOrbTests(MoiraArena):
    def launch(self, *, healing=False, support=None, support_buttons=0):
        extra = {} if support is None else {support: (-32768, 0, support_buttons)}
        self.run.step({self.moira: (0, 0, 16), **extra})
        for _ in range(40):
            self.run.step({self.moira: (0, 0, 1 if healing else 2), **extra})
            if self.fight.moira_orbs:
                return self.fight.moira_orbs[-1]
        self.fail("Authored orb did not launch")

    def chamber(self, *, partition=False):
        x, y, z = self.moira.mover.position
        wall = (z + 1, z + 1.1) if partition else (z + 5, z + 5.1)
        world = World(box(x - 8, x + 8, y - 2, y, z - 8, z + 8)
                      + box(x - 5, x + 5, y, y + 5, *wall)
                      + box(x - 5, x + 5, y, y + 5, z - 1.1, z - 1))
        self.match.collision_provider = StaticCollisionProvider(world)
        self.match.collision_world = world
        for actor in self.run.players:
            actor.mover.use(world=world)

    def test_damage_orb_applies_aura_and_spends_actual_health_loss(self):
        orb = self.launch()
        self.assertFalse(orb.healing)
        self.assertEqual(orb.energy, 200)
        self.assertEqual(self.health.total, 150)
        self.assertFalse(self.fight.rockets)
        for _ in range(20):
            self.run.step({})
        self.assertAlmostEqual(self.health.total, 134, places=4)
        self.assertAlmostEqual(orb.energy, 184, places=4)
        self.assertAlmostEqual(orb.flown, 0.32, places=4)

    def test_healing_orb_heals_allied_tracer_and_spends_the_same_amount(self):
        self.match.game_map = replace(self.match.game_map, free_for_all=False)
        self.tracer.team = self.moira.team
        self.health.damage(100)
        orb = self.launch(healing=True)
        self.assertTrue(orb.healing)
        self.assertEqual(orb.energy, 300)
        for _ in range(20):
            self.run.step({})
        self.assertAlmostEqual(self.health.total, 70.8, places=4)
        self.assertAlmostEqual(orb.energy, 279.2, places=4)

    def test_both_orbs_bounce_and_partition_blocks_their_aura(self):
        for healing in (False, True):
            with self.subTest(healing=healing):
                self.setUp()
                self.match.game_map = replace(self.match.game_map, free_for_all=False)
                if healing:
                    self.tracer.team = self.moira.team
                self.health.damage(50)
                self.chamber(partition=True)
                orb = self.launch(healing=healing)
                for _ in range(45):
                    self.run.step({})
                self.assertGreater(orb.bounces, 0)
                self.assertEqual(self.health.total, 100)
                self.assertEqual(orb.energy, 300 if healing else 200)

    def test_recall_blocks_damage_and_orb_reacquires_after_playback(self):
        self.chamber()
        orb = self.launch()
        for _ in range(15):
            self.run.step({})
        self.assertLess(self.health.total, 150)
        self.cast(buttons=0, tracer_buttons=16)
        self.assertTrue(tracer.recalling(self.tracer))
        energy = orb.energy
        for _ in range(100):
            self.assertEqual(orb.energy, energy)
            self.run.step({})
            if not tracer.recalling(self.tracer):
                break
        self.assertFalse(tracer.recalling(self.tracer))
        self.assertEqual(self.health.total, 150)
        for _ in range(15):
            self.run.step({})
            if orb.energy < energy:
                break
        self.assertLess(orb.energy, energy)
        self.assertLess(self.health.total, 150)

    def test_damage_energy_exhausts_exactly_across_bounces(self):
        self.chamber()
        self.health.most[combat.HEALTH_POOL] = self.health.now[combat.HEALTH_POOL] = 1000
        orb = self.launch()
        for _ in range(450):
            self.run.step({})
            if orb not in self.fight.moira_orbs:
                break
        self.assertNotIn(orb, self.fight.moira_orbs)
        self.assertGreater(orb.bounces, 0)
        self.assertEqual(orb.energy, 0)
        self.assertAlmostEqual(self.health.total, 800, places=4)


    def test_actual_tracer_kill_does_not_cancel_a_released_damage_orb(self):
        self.moira_health.now[combat.HEALTH_POOL] = 6
        orb = self.launch()
        x, y, z = self.moira.mover.position
        facing = aim(self.fight._eye(self.tracer), (x, y + 1, z))
        self.run.step({self.tracer: (*facing, FIRE)})
        self.assertTrue(self.moira_health.dead)
        self.assertEqual(self.moira_health.killer, self.tracer.body)
        self.assertIn(orb, self.fight.moira_orbs)
        before = self.health.total
        for _ in range(10):
            self.run.step({})
        self.assertAlmostEqual(before - self.health.total, 8, places=4)

    def test_lifetime_and_duplicate_world_frame(self):
        x, y, z = self.moira.mover.position
        self.place(self.tracer, (x + 15, y, z))
        orb = self.launch()
        self.run.step({})
        state = orb.position, orb.left, orb.energy, self.health.total
        self.fight.update(self.fight.now, self.fight.tick, frames=False)
        self.assertEqual((orb.position, orb.left, orb.energy, self.health.total), state)
        for _ in range(450):
            self.run.step({})
        self.assertNotIn(orb, self.fight.moira_orbs)
        self.assertAlmostEqual(orb.flown, 7.0, places=4)
        self.assertEqual(orb.energy, 200)
        self.assertEqual(self.health.total, 150)

    def test_cancel_reuse_and_both_variants_share_the_cooldown(self):
        instance = next(i for i in self.moira.body_script.component.instances.values()
                        if i.graph.index == 0x10E4)
        for frame in range(70):
            self.cast(buttons=16 if frame in (0, 20) else 0)
        self.assertFalse(self.fight.moira_orbs)
        self.assertEqual(instance.state(0).remaining(), 0)
        damage = self.launch()
        volley = instance.state(11)
        healing_volley = instance.state(10)
        count = volley.counter
        healing_count = healing_volley.counter
        for _ in range(40):
            self.run.step({})
        self.assertGreater(instance.state(0).remaining(), 7)
        for frame in range(60):
            self.cast(buttons=(16, 1, 0)[frame % 3])
            self.assertEqual(volley.counter, count)
            self.assertEqual(healing_volley.counter, healing_count)
        for _ in range(450):
            self.run.step({})
        self.assertEqual(instance.state(0).remaining(), 0)
        healing = self.launch(healing=True)
        self.assertIsNot(damage, healing)
        self.assertTrue(healing.healing)
        self.assertEqual(volley.counter, count)
        self.assertEqual(healing_volley.counter, healing_count + 1)

    def test_deflected_damage_orb_switches_teams_and_hits_tracer(self):
        genji, _ = support_tests.PulseDeflectTests().support(self.run, self.tracer, hero=0x02E0000000000029)
        self.match.game_map = replace(self.match.game_map, free_for_all=False)
        self.tracer.team = self.moira.team = 0
        genji.team = 1
        x, y, z = self.moira.mover.position
        self.place(genji, (x, y, z + 3))
        self.place(self.tracer, (x, y, z - 1))
        for _ in range(12):
            self.run.step({})
        self.run.step({genji: (-32768, 0, 16)})
        orb = self.launch(support=genji)
        for _ in range(80):
            self.run.step({genji: (-32768, 0, 0)})
            if orb.shooter is genji:
                break
        self.assertIs(orb.shooter, genji)
        self.assertEqual(orb.body, genji.body)
        self.assertEqual(orb.team, genji.team)
        before = self.vitals(genji).total
        for _ in range(20):
            self.run.step({genji: (-32768, 0, 0)})
            if self.health.total < 150:
                break
        self.assertEqual(self.vitals(genji).total, before)
        self.assertLess(self.health.total, 150)


    def test_sigma_absorbs_actual_orb_before_it_can_damage_tracer(self):
        sigma, _ = support_tests.PulseDeflectTests().support(self.run, self.tracer, hero=sigma_grasp.HERO)
        self.match.game_map = replace(self.match.game_map, free_for_all=False)
        sigma.team = self.tracer.team = 0
        self.moira.team = 1
        x, y, z = self.moira.mover.position
        self.place(sigma, (x, y, z + 3))
        self.place(self.tracer, (x, y, z + 7))
        for _ in range(12):
            self.run.step({})
        self.run.step({sigma: (-32768, 0, 8)})
        orb = self.launch(support=sigma)
        instance = next(i for i in sigma.body_script.component.instances.values()
                        if i.graph.index == sigma_grasp.GRASP)
        self.assertTrue(instance.state(1).active)
        for _ in range(45):
            self.run.step({sigma: (-32768, 0, 0)})
            if orb not in self.fight.moira_orbs:
                break
        self.assertNotIn(orb, self.fight.moira_orbs)
        self.assertEqual(instance.vars[21312].value(), 100)
        self.assertGreater(orb.energy, 0)
        self.assertEqual(self.health.total, 150)

    def test_authored_launch_scalar_caps_healing_after_amplification(self):
        self.match.game_map = replace(self.match.game_map, free_for_all=False)
        self.tracer.team = self.moira.team
        self.health.damage(100)
        self.health.healing_boost_until = self.fight.tick + 100
        self.moira.body_script.component.set(12618, 0.001)
        orb = self.launch(healing=True)
        self.assertAlmostEqual(orb.energy, 0.3, places=5)
        for _ in range(20):
            self.run.step({})
            if orb not in self.fight.moira_orbs:
                break
        self.assertNotIn(orb, self.fight.moira_orbs)
        self.assertAlmostEqual(self.health.total, 50.3, places=5)


    def test_actual_matrix_consumes_both_orb_variants_before_aura_reaches_tracer(self):
        if (graph.entity_body(dva.MECH) is None or defined_mover_data(dva.MECH) is None
                or not all(deflection.absorbable(i) for i in (moira_orb.HEAL_ORB, moira_orb.DAMAGE_ORB))):
            self.skipTest("verified mech resources and orb target tags required")
        for healing in (False, True):
            with self.subTest(healing=healing):
                self.setUp()
                mech, _ = support_tests.PulseDeflectTests().support(self.run, self.tracer, hero=dva.HERO)
                self.assertTrue(self.match.switch_hero(mech, replace(find_hero("dva"), body=dva.MECH)))
                self.match.game_map = replace(self.match.game_map, free_for_all=False)
                self.moira.team, mech.team = 0, 1
                self.tracer.team = self.moira.team if healing else mech.team
                self.health.damage(50)
                x, y, z = self.moira.mover.position
                self.place(mech, (x, y, z + 15))
                self.place(self.tracer, (x, y, z + 19))
                for _ in range(20):
                    self.run.step({})
                mech_health = self.vitals(mech)
                before = mech_health.total
                orb = self.launch(healing=healing, support=mech, support_buttons=2)
                for _ in range(75):
                    self.run.step({mech: (-32768, 0, 2)})
                    if orb not in self.fight.moira_orbs:
                        break
                self.assertNotIn(orb, self.fight.moira_orbs)
                expected = 300 if healing else 200 - (before - mech_health.total)
                self.assertAlmostEqual(orb.energy, expected, places=5)
                self.assertGreater(orb.left, 0)
                self.assertEqual(self.health.total, 100)
