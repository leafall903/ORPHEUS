"""Actual Ana grenades and Soldier healing fields against Tracer."""

import unittest
from dataclasses import replace

import test_game_tracer_ana_sleep as sleep_tests
import test_game_tracer_pulse_deflect as support_tests
import test_game_tracer_winston_barrier as bubble_tests
from test_game_combat import aim
from test_game_mover import box

from ow174.game import combat, deflection, stats, tracer
from ow174.game.collision import World
from ow174.game.commands import FIRE
from ow174.game.content import find_hero


class TracerAnaGrenadeTests(unittest.TestCase):
    def setUp(self):
        self.case = sleep_tests.TracerAnaSleepTests()
        self.case.setUp()
        self.run, self.fight = self.case.run, self.case.fight
        self.player, self.ana = self.case.player, self.case.ana
        self.health = self.case.health

    def throw(self):
        self.case.step(ana=16)
        seen = []
        for _ in range(70):
            self.case.step()
            for rocket in self.fight.rockets:
                if not seen:
                    seen.append(rocket)
            if seen and seen[0] not in self.fight.rockets:
                break
        rocket, = seen
        self.assertEqual(rocket.definition, combat.ANA_GRENADE)
        self.assertAlmostEqual(rocket.gravity, 9.8, places=5)
        self.assertNotIn(rocket, self.fight.rockets)

    def soldier(self):
        self.run.match.game_map = replace(self.run.match.game_map, free_for_all=False)
        self.ana.team = 1 - self.player.team
        soldier, _ = support_tests.PulseDeflectTests().support(self.run, self.player)
        soldier.team = self.player.team
        x, y, z = self.player.mover.position
        soldier.mover.position[:] = (x + 2, y, z)
        soldier.mover.body = None
        return soldier

    def test_actual_enemy_grenade_blocks_soldier_field_then_healing_resumes(self):
        soldier = self.soldier()
        self.throw()
        self.assertEqual(self.health.total, 100)
        deadline = self.health.healing_block_until
        self.assertEqual(deadline - self.fight.tick, 250)
        self.run.step({soldier: (0, 0, 16)})
        while self.fight.tick + 1 < deadline:
            self.run.step({})
            self.assertEqual(self.health.total, 100)
        for _ in range(25):
            self.run.step({})
            if self.health.total > 100:
                break
        self.assertGreater(self.health.total, 100)
        self.assertGreater(soldier.stats.value(stats.HEALING), 0)

    def test_actual_ally_grenade_heals_then_boosts_soldier_field(self):
        deltas = []
        for boost in (False, True):
            with self.subTest(boost=boost):
                self.setUp()
                soldier = self.soldier()
                self.ana.team = self.player.team
                self.health.damage(140 if boost else 40)
                if boost:
                    self.throw()
                    self.assertEqual(self.health.healing_boost_until - self.fight.tick, 250)
                    self.assertEqual(self.ana.stats.value(stats.HEALING), 100)
                self.assertEqual(self.health.total, 110)
                self.assertEqual(self.health.healing_block_until, 0)
                self.run.step({soldier: (0, 0, 16)})
                for _ in range(80):
                    if self.health.total > 110:
                        break
                    self.run.step({})
                self.assertGreater(self.health.total, 110)
                deltas.append(self.health.total - 110)
        self.assertAlmostEqual(deltas[1], 1.5 * deltas[0], places=5)

    def test_actual_grenade_cooldown_rejects_early_press_and_allows_later_reuse(self):
        self.throw()
        first = self.fight.tick
        self.case.step(ana=16)
        for _ in range(50):
            self.case.step()
            self.assertFalse(self.fight.rockets)
        self.assertEqual(self.health.total, 100)
        while self.fight.tick < first + 700:
            self.case.step()
        self.throw()
        self.assertEqual(self.health.total, 50)
        self.assertEqual(self.health.healing_block_until - self.fight.tick, 250)

    def test_actual_static_cover_blocks_grenade_damage_and_status(self):
        x, y, z = self.player.mover.position
        floor = [(-1000, y, -1000, -1000, y, 1000, 1000, y, 1000),
                 (-1000, y, -1000, 1000, y, 1000, 1000, y, -1000)]
        self.run.match.collision_world = World(floor + box(x - 3, x + 3, y, y + 4, z + 2.5, z + 2.7))
        self.throw()
        self.assertEqual(self.health.total, 150)
        self.assertEqual(self.health.healing_block_until, 0)

    def test_actual_winston_barrier_intercepts_grenade_and_blocks_blast_status(self):
        self.run.match.game_map = replace(self.run.match.game_map, free_for_all=False)
        self.player.team, self.ana.team = 0, 1
        x, y, z = self.player.mover.position
        bubble_tests.TracerWinstonBarrierTests.move(self.ana, (x, y, z + 12))
        owner, _ = support_tests.PulseDeflectTests().support(self.run, self.player, hero=0x02E0000000000009)
        owner.team = self.player.team
        for _ in range(25):
            self.run.step({})
        bubble = bubble_tests.TracerWinstonBarrierTests().cast(self.run, owner)
        self.case.angles = aim(self.fight._eye(self.ana), (x, y + 1.1, z))
        self.throw()
        self.assertEqual(bubble.health, 650)
        self.assertEqual(self.health.total, 150)
        self.assertEqual(self.health.healing_block_until, 0)
        self.assertEqual(self.case.case.health(owner).healing_block_until, 0)

    @unittest.skipUnless(deflection.TAGS_PATH, "requires verified projectile tags")
    def test_actual_matrix_absorbs_grenade_before_damage_or_healing_modifiers(self):
        self.run.match.game_map = replace(self.run.match.game_map, free_for_all=False)
        self.player.team, self.ana.team = 0, 1
        x, y, z = self.player.mover.position
        bubble_tests.TracerWinstonBarrierTests.move(self.ana, (x, y, z + 12))
        defender, _ = support_tests.PulseDeflectTests().support(
            self.run, self.player, hero=0x02E000000000007A,
        )
        mech = replace(find_hero("dva"), body=0x0400000000000407)
        self.assertTrue(self.run.match.switch_hero(defender, mech))
        defender.team = self.player.team
        bubble_tests.TracerWinstonBarrierTests.move(defender, (x, y, z + 3))
        for _ in range(110):
            self.run.step({})
        facing = aim(self.fight._eye(defender), (x, y + 1.1, z + 12))
        angles = aim(self.fight._eye(self.ana), (x, y + 1.1, z))
        self.run.step({defender: (*facing, 2)})
        seen = False
        for frame in range(55):
            self.run.step({self.ana: (*angles, 16 if frame == 0 else 0), defender: (*facing, 2)})
            seen |= any(r.definition == combat.ANA_GRENADE for r in self.fight.rockets)
        self.assertTrue(seen)
        self.assertFalse(self.fight.rockets)
        self.assertEqual(self.health.total, 150)
        self.assertEqual(self.health.healing_block_until, 0)
        self.assertEqual(self.health.healing_boost_until, 0)
        health = self.case.case.health(defender)
        self.assertEqual((health.total, health.healing_block_until), (600, 0))

    def test_actual_recall_restores_health_and_clears_grenade_block(self):
        self.throw()
        self.assertEqual(self.health.total, 100)
        self.assertGreater(self.health.healing_block_until, self.fight.tick)
        self.case.step(buttons=16)
        self.assertTrue(tracer.recalling(self.player))
        for _ in range(100):
            self.case.step()
            if not tracer.recalling(self.player):
                break
        self.assertFalse(tracer.recalling(self.player))
        self.assertEqual(self.health.total, 150)
        self.assertEqual(self.health.healing_block_until, 0)

    def test_recall_clears_frost_on_entry_and_anti_heal_on_completion(self):
        mei, _ = support_tests.PulseDeflectTests().support(
            self.run, self.player, hero=find_hero("mei").guid,
        )
        x, y, z = self.player.mover.position
        mei.mover.position[:] = (x + 3, y, z)
        mei.mover.body = None
        aim_mei = aim(self.fight._eye(mei), (x, y + 1, z))
        self.throw()
        anti_heal_deadline = self.health.healing_block_until
        self.assertGreater(anti_heal_deadline, self.fight.tick)
        for _ in range(30):
            self.run.step({mei: (*aim_mei, FIRE)})
        frost = self.fight.frost_hits[self.player.body]
        self.assertGreater(frost[0], 0)
        self.assertEqual(frost[2], 0)
        damaged = self.health.total
        self.assertLess(damaged, 100)

        self.run.step({self.player: (0, 0, 16), mei: (*aim_mei, FIRE)})
        phases = set()
        script = self.player.body_script
        for _ in range(100):
            if not tracer.recalling(self.player):
                break
            phases.add(script.world.recall_session.state.node.pos)
            self.assertNotIn(self.player.body, self.fight.frost_hits)
            self.assertEqual(self.health.healing_block_until, anti_heal_deadline)
            self.assertEqual(self.health.total, damaged)
            self.run.step({mei: (*aim_mei, FIRE)})
        self.assertEqual(phases, {4, 1})
        self.assertFalse(tracer.recalling(self.player))
        self.assertLess(self.fight.tick, anti_heal_deadline)
        self.assertEqual((self.health.total, self.health.healing_block_until), (150, 0))
        self.assertNotIn(self.player.body, self.fight.frost_hits)

        for _ in range(18):
            if self.player.body in self.fight.frost_hits:
                break
            self.run.step({mei: (*aim_mei, FIRE)})
        new_frost = self.fight.frost_hits[self.player.body]
        self.assertLess(new_frost[0], frost[0])
        self.assertLess(self.health.total, 150)


    def test_recall_abort_preserves_actual_anti_heal_until_expiry(self):
        for phase in (4, 1):
            with self.subTest(recall_node=phase):
                self.setUp()
                soldier = self.soldier()
                self.throw()
                deadline = self.health.healing_block_until
                self.assertGreater(deadline, self.fight.tick)
                self.assertEqual(self.health.total, 100)
                self.run.step({soldier: (0, 0, 16), self.player: (0, 0, 16)})
                script = self.player.body_script
                for _ in range(80):
                    session = script.world.recall_session
                    if session.state.node.pos == phase:
                        break
                    self.run.step({})
                self.assertEqual(session.state.node.pos, phase)

                # Use the graph's abort path to isolate cancellation from death.
                recall = session.state.instance
                recall.state(16).stop()
                self.run.step({})
                self.assertFalse(tracer.recalling(self.player))
                self.assertGreater(recall.vars[61].value(), 11)
                self.assertEqual(self.health.healing_block_until, deadline)
                self.run.step({self.player: (0, 0, 16)})
                self.assertFalse(tracer.recalling(self.player))
                while self.fight.tick < deadline - 1:
                    self.assertEqual(self.health.total, 100)
                    self.assertEqual(soldier.stats.value(stats.HEALING), 0)
                    self.run.step({})
                for _ in range(25):
                    self.run.step({})
                    if self.health.total > 100:
                        break
                self.assertGreater(soldier.stats.value(stats.HEALING), 0)
                self.assertGreater(self.health.total, 100)
                self.assertLess(self.health.total, 150)


if __name__ == "__main__":
    unittest.main()
