"""Mei weapon commands against Symmetra in the 104319 combat host."""

import os
import unittest
from dataclasses import replace

import test_symmetra_barriers as barrier_tests
from test_game_combat import aim
from test_symmetra_combat import arena, health
from test_symmetra_turrets import wall

from ow174.game import combat, cryo, mei_frost, zarya_barriers
from ow174.game.commands import Command
from ow174.game.content import heroes
from ow174.game.script import nodes


class SymmetraMeiWeaponTests(unittest.TestCase):
    def duel(self, distance=5):
        run, sym, mei = arena()
        self.assertTrue(run.match.switch_hero(mei, heroes()[cryo.HERO]))
        mei.mover.position[:] = (0, 1, 0)
        sym.mover.position[:] = (0, 1, distance)
        for _ in range(30):
            run.step({})
        return run, sym, mei

    def fire(self, run, sym, mei, height, *, horizontal=0, button=2, frames=45):
        fight = run.match.combat
        x, y, z = sym.mover.position
        angles = aim(fight._eye(mei), (x + horizontal, y + height, z))
        in_flight = False
        for frame in range(frames):
            run.step({mei: (*angles, button if frame < 20 else 0)})
            in_flight |= any(shot.shooter is mei for shot in fight.rockets)
        if button == 2:
            self.assertTrue(in_flight)

    def test_icicle_body_and_head_consume_real_symmetra_pools(self):
        for height, damage, flags in ((.9, 75, 0), (1.55, 150, combat.CRITICAL)):
            with self.subTest(height=height):
                run, sym, mei = self.duel()
                vitals = health(run, sym)
                self.assertEqual(vitals.total, 225)
                self.assertEqual(vitals.now[combat.SHIELDS_POOL], 125)
                self.fire(run, sym, mei, height)
                self.assertEqual(vitals.total, 225 - damage)
                self.assertEqual(vitals.now[combat.SHIELDS_POOL], max(0, 125 - damage))
                self.assertEqual(vitals.now[combat.HEALTH_POOL], min(100, 225 - damage))
                [hit] = mei.client.messages(combat.HIT)
                self.assertEqual(hit["+0x78"]["+0x4"], damage)
                self.assertEqual(hit["+0x78"]["+0x8"], flags)

    def test_icicle_miss_ally_and_static_wall_preserve_pools(self):
        for case in ("miss", "ally", "wall"):
            with self.subTest(case=case):
                run, sym, mei = self.duel()
                if case == "ally":
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    sym.team = mei.team
                if case == "wall":
                    run.match.collision_world = wall()
                self.fire(run, sym, mei, .9, horizontal=4 if case == "miss" else 0)
                self.assertEqual(health(run, sym).total, 225)
                self.assertFalse(mei.client.messages(combat.HIT))

    def test_spray_deals_authored_noncritical_damage(self):
        run, sym, mei = self.duel()
        self.fire(run, sym, mei, 1.55, button=1)
        hits = mei.client.messages(combat.HIT)
        self.assertTrue(hits)
        vitals = health(run, sym)
        self.assertAlmostEqual(vitals.now[combat.HEALTH_POOL], 100)
        self.assertAlmostEqual(vitals.now[combat.SHIELDS_POOL], 125 - len(hits) * 2.75)
        self.assertTrue(all(hit["+0x78"]["+0x8"] == 0 for hit in hits))

    @unittest.skipUnless(os.environ.get("ORPHEUS_EXTRA_GRAPH_BUNDLES"), "requires actor graph shard")
    def test_icicle_damages_live_photon_barrier_and_stops_before_symmetra(self):
        fixture = barrier_tests.PhotonBarrierTests()
        run, sym, mei, barrier = fixture.deploy()
        self.assertTrue(run.match.switch_hero(mei, heroes()[cryo.HERO]))
        fixture.move(mei, (0, 0, 6))
        before = health(run, sym).total
        self.fire(run, sym, mei, .9)
        self.assertEqual(run.match.combat.vitals[barrier.bot.entity].total, 3925)
        self.assertEqual(health(run, sym).total, before)
        self.assertFalse(any(shot.shooter is mei for shot in run.match.combat.rockets))

    def test_two_actual_headshots_kill_and_publish_once(self):
        run, sym, mei = self.duel()
        self.fire(run, sym, mei, 1.55)
        for _ in range(30):
            run.step({})
        self.fire(run, sym, mei, 1.55)
        self.assertTrue(run.match.combat.is_dead(sym))
        self.assertEqual(health(run, sym).total, 0)
        self.assertEqual(len(mei.client.messages(combat.KILL_NOTICE)), 1)
        self.assertEqual(len(sym.client.messages(combat.DEATH_NOTICE)), 1)
        self.assertEqual([hit["+0x78"]["+0x8"] for hit in mei.client.messages(combat.HIT)],
                         [combat.CRITICAL, combat.CRITICAL | combat.KILLING])

    def test_spray_range_wall_and_team_filter_damage(self):
        for case in ("range", "wall", "ally"):
            with self.subTest(case=case):
                run, sym, mei = self.duel(distance=12 if case == "range" else 5)
                if case == "wall":
                    run.match.collision_world = wall()
                elif case == "ally":
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    sym.team = mei.team
                self.fire(run, sym, mei, .9, button=1)
                self.assertEqual(health(run, sym).total, 225)
                self.assertFalse(mei.client.messages(combat.HIT))
                self.assertIsNone(run.match.combat.frost(sym))

    def test_icicle_spends_ammo_and_expires_at_authored_range(self):
        for distance, damage in ((99, 75), (101, 0)):
            with self.subTest(distance=distance):
                run, sym, mei = self.duel(distance)
                self.assertAlmostEqual(sym.mover.position[2] - mei.mover.position[2], distance)
                weapon = next(i for i in mei.body_script.component.instances.values()
                              if i.graph.index == 0x05E6)
                self.assertEqual(weapon.vars[53].value(), 120)
                self.fire(run, sym, mei, .9, frames=100)
                self.assertEqual(weapon.vars[53].value(), 110)
                self.assertEqual(health(run, sym).total, 225 - damage)
                self.assertEqual(len(mei.client.messages(combat.HIT)), int(damage > 0))
                self.assertFalse(any(shot.shooter is mei for shot in run.match.combat.rockets))

    def test_actual_spray_slow_changes_movement_and_expires_without_input(self):
        run, sym, mei = self.duel()
        fight = run.match.combat
        x, y, z = sym.mover.position
        angles = aim(fight._eye(mei), (x, y + .9, z))
        run.step({mei: (*angles, 1)})
        for _ in range(16):
            run.step({})
        self.assertEqual(len(mei.client.messages(combat.HIT)), 1)
        self.assertAlmostEqual(fight.frost(sym)[0], .2)
        self.assertAlmostEqual(sym.body_script.component.entity_var(2427).value(), .2)
        self.move_sym(run, sym, count=10)
        self.assertAlmostEqual(sym.mover.values.run_forward, sym.mover.data.run_forward * .8, places=6)
        self.assertGreater(sym.mover.position[2], z)
        for _ in range(70):
            run.frame += 1
            run.match.update(run.frame * combat.TICK, run.frame)
        self.assertIsNone(fight.frost(sym))
        self.assertEqual(sym.body_script.component.entity_var(2427).value(), 0)
        self.move_sym(run, sym, count=2)
        self.assertEqual(sym.mover.values.run_forward, sym.mover.data.run_forward)

    def move_sym(self, run, sym, *, count, buttons=0):
        for _ in range(count):
            run.frame += 1
            run.match.update(run.frame * combat.TICK, run.frame)
            sym.take_commands([Command(run.frame, forward=127, yaw=12345, buttons=buttons)])

    def test_actual_spray_freezes_interrupts_charge_and_restores_control(self):
        run, sym, mei = self.duel()
        fight = run.match.combat
        angles = aim(fight._eye(mei), (0, 1.9, 5))
        charge_observed = False
        for _ in range(130):
            charge_observed |= 29 in sym.body_script.component.held
            sample = fight.frost(sym)
            if sample is not None and sample[2]:
                break
            charging = sample is not None and sample[0] >= .8
            run.step({mei: (*angles, 1), sym: (0, 0, 2 if charging else 0)})
        sample = fight.frost(sym)
        self.assertTrue(charge_observed)
        self.assertIsNotNone(sample)
        self.assertEqual(sample[0], 1)
        self.assertEqual(sample[2] - sample[1], mei_frost.FREEZE_MS)
        self.assertEqual(sym.body_script.component.entity_var(2427).value(), 1)
        self.assertFalse(any(rocket.shooter is sym for rocket in fight.rockets))
        self.assertFalse(sym.body_script.component.held)
        before = sym.mover.position[:]
        enemy_health = health(run, mei).total
        self.move_sym(run, sym, count=20, buttons=1 | 2 | 8 | 16 | 32 | 0x800)
        self.assertEqual(sym.mover.position[:], before)
        self.assertEqual(sym.mover.yaw, sample[3])
        self.assertEqual(health(run, mei).total, enemy_health)
        self.assertFalse(fight.sentries.sentries)
        self.assertFalse(fight.portals.pairs)
        for _ in range(85):
            run.step({})
        self.assertIsNone(fight.frost(sym))
        self.move_sym(run, sym, count=16)
        self.assertNotEqual(sym.mover.position[:], before)

    @unittest.skipUnless(os.environ.get("ORPHEUS_EXTRA_GRAPH_BUNDLES"), "requires actor graph shard")
    def test_actual_photon_barrier_keeps_spray_frost_off_symmetra(self):
        fixture = barrier_tests.PhotonBarrierTests()
        run, sym, mei, barrier = fixture.deploy()
        self.assertTrue(run.match.switch_hero(mei, heroes()[cryo.HERO]))
        fixture.move(mei, (0, 0, 6))
        before = health(run, sym).total
        self.fire(run, sym, mei, .9, button=1)
        self.assertLess(run.match.combat.vitals[barrier.bot.entity].total, 4000)
        self.assertEqual(health(run, sym).total, before)
        self.assertIsNone(run.match.combat.frost(sym))

    def test_spray_freeze_interrupts_live_teleporter_preview_and_allows_reentry(self):
        run, sym, mei = self.duel()
        fight = run.match.combat
        angles = aim(fight._eye(mei), (0, 1.9, 5))
        placement = next(i for i in sym.body_script.component.instances.values()
                         if i.graph.index == 0x1571).state(23)
        preview_observed = False
        for _ in range(130):
            sample = fight.frost(sym)
            if sample is not None and sample[2]:
                break
            placing = sample is not None and sample[0] >= .7
            run.step({mei: (*angles, 1), sym: (0, 8192, 16 if placing else 0)})
            preview_observed |= bool(placement.flags & nodes.Ability.RUNNING)
        self.assertTrue(preview_observed)
        self.assertTrue(fight.is_stunned(sym))
        self.assertFalse(placement.flags & nodes.Ability.RUNNING)
        self.assertFalse(fight.portals.pairs)
        for _ in range(90):
            run.step({})
        self.assertFalse(fight.is_stunned(sym))
        run.step({sym: (0, 8192, 16)})
        self.assertTrue(placement.flags & nodes.Ability.RUNNING)

    def test_one_spray_pierces_two_bodies_once_each(self):
        run, sym, mei = self.duel()
        front = barrier_tests.PhotonBarrierTests().add_target(run, 3, (0, 1, 3))
        fight = run.match.combat
        angles = aim(fight._eye(mei), (0, 1.9, 5))
        run.step({mei: (*angles, 1)})
        for _ in range(24):
            run.step({})
        self.assertAlmostEqual(health(run, front).total, 197.25)
        self.assertAlmostEqual(health(run, sym).total, 222.25)
        self.assertAlmostEqual(fight.frost(front)[0], .2)
        self.assertAlmostEqual(fight.frost(sym)[0], .2)
        self.assertEqual(len(mei.client.messages(combat.HIT)), 2)
        self.assertFalse(any(shot.shooter is mei for shot in fight.rockets))

    def test_piercing_spray_stops_at_static_cover_after_front_hit(self):
        run, sym, mei = self.duel()
        front = barrier_tests.PhotonBarrierTests().add_target(run, 3, (0, 1, 2))
        run.match.collision_world = wall()
        fight = run.match.combat
        self.fire(run, sym, mei, .9, button=1)
        self.assertLess(health(run, front).total, 200)
        self.assertIsNotNone(fight.frost(front))
        self.assertEqual(health(run, sym).total, 225)
        self.assertIsNone(fight.frost(sym))

    def test_particle_barrier_stops_piercing_spray_before_rear_body(self):
        run, sym, mei = self.duel()
        fixture = barrier_tests.PhotonBarrierTests()
        front = fixture.add_target(run, 3, (0, 1, 3))
        self.assertTrue(run.match.switch_hero(front, heroes()[zarya_barriers.HERO]))
        fixture.move(front, (0, 1, 3))
        fight = run.match.combat
        before = health(run, front).total
        run.step({front: (0, 0, 8)})
        _, _, bubble = fight.zarya_barriers.contact(front)
        self.fire(run, sym, mei, .9, button=1)
        self.assertLess(bubble.remaining, 200)
        self.assertEqual(health(run, front).total, before)
        self.assertEqual(health(run, sym).total, 225)
        self.assertIsNone(fight.frost(front))
        self.assertIsNone(fight.frost(sym))
        self.assertFalse(mei.client.messages(combat.HIT))

    def two_mei(self):
        run, sym, first = self.duel()
        fixture = barrier_tests.PhotonBarrierTests()
        second = fixture.add_target(run, 3, (1, 1, 0))
        self.assertTrue(run.match.switch_hero(second, heroes()[cryo.HERO]))
        fixture.move(first, (-1, 1, 0))
        fixture.move(second, (1, 1, 0))
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        first.team = second.team = 0
        sym.team = 1
        for _ in range(12):
            run.step({})
        return run, sym, first, second

    def spray_from(self, run, sym, sources, frames):
        x, y, z = sym.mover.position
        fight = run.match.combat
        commands = {actor: (*aim(fight._eye(actor), (x, y + .9, z)), 1) for actor in sources}
        for _ in range(frames):
            run.step(commands)
        for _ in range(10):
            run.step({})

    def test_two_actual_sprays_use_highest_independent_progress(self):
        run, sym, first, second = self.two_mei()
        self.spray_from(run, sym, (first, second), 60)
        counts = [len(actor.client.messages(combat.HIT)) for actor in (first, second)]
        self.assertTrue(all(10 <= count < 29 for count in counts), counts)
        expected = .2 + (max(counts) - 1) * .0285
        self.assertAlmostEqual(run.match.combat.frost(sym)[0], expected, places=5)
        self.assertFalse(run.match.combat.is_stunned(sym))
        self.assertAlmostEqual(health(run, sym).total, 225 - sum(counts) * 2.75)

    def test_expired_stronger_spray_falls_back_to_fresh_weaker_source(self):
        run, sym, first, second = self.two_mei()
        fight = run.match.combat
        self.spray_from(run, sym, (first,), 24)
        strong = fight.frost(sym)[0]
        self.assertGreater(strong, .2)
        for _ in range(25):
            run.step({})
        self.spray_from(run, sym, (second,), 1)
        self.assertEqual(len(second.client.messages(combat.HIT)), 1)
        self.assertAlmostEqual(fight.frost(sym)[0], strong)
        for _ in range(35):
            run.frame += 1
            run.match.update(run.frame * combat.TICK, run.frame)
        self.assertAlmostEqual(fight.frost(sym)[0], .2)
        self.assertAlmostEqual(sym.body_script.component.entity_var(2427).value(), .2)
        self.assertEqual(set(fight.frost_sources[sym.body]), {(second.body, 0)})

    def test_explicit_stacking_freezes_and_later_hits_do_not_refresh_deadline(self):
        run, sym, first, second = self.two_mei()
        fight = run.match.combat
        for actor in (first, second):
            actor.body_script.component.set(mei_frost.STACKING_VAR, True)
        self.spray_from(run, sym, (first, second), 60)
        self.assertTrue(fight.is_stunned(sym))
        freeze = fight.frost(sym)
        self.assertEqual(freeze[2] - freeze[1], mei_frost.FREEZE_MS)
        before = len(first.client.messages(combat.HIT))
        self.spray_from(run, sym, (first,), 5)
        self.assertGreater(len(first.client.messages(combat.HIT)), before)
        self.assertEqual(fight.frost(sym), freeze)
        for _ in range(90):
            run.step({})
        self.assertIsNone(fight.frost(sym))
        self.assertNotIn(sym.body, fight.frost_sources)
        self.spray_from(run, sym, (second,), 1)
        self.assertAlmostEqual(fight.frost(sym)[0], .2)
