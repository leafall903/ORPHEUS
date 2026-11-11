"""Actual 157C attacks against Reinhardt's raised mesh and barrier pool."""

import os
import unittest
from unittest.mock import patch

from test_game_combat import aim, in_world
from test_symmetra_combat import arena, health
from test_symmetra_turrets import wall

from ow174.game import barrier_mesh, combat, projectile_tags, reinhardt_barrier, stats, symmetra
from ow174.game.collision import World
from ow174.game.content import heroes


@unittest.skipIf(barrier_mesh.DESCRIPTOR_SHA256 is None, "requires the build 104319 shield mesh")
class SymmetraReinhardtTests(unittest.TestCase):
    def test_body_impact_spends_raised_mesh_via_splash_only(self):
        run, owner, rein, shield, _ = self.scene()
        soldier = 0x02E000000000006E
        front = run.match.add_player(3, "Front", soldier, 0, False)
        in_world(run.match, front)
        run.players.append(front)
        self.assertTrue(run.match.switch_hero(front, heroes()[soldier]))
        front.mover.position[:] = (0, 1, 4)
        before = health(run, front).total
        angles = aim(run.match.combat._eye(owner), (0, 2, 4))
        for frame in range(115):
            run.step({owner: (*angles, 2 if frame < 90 else 0), rein: (-32768, 0, 2)})
        self.assertAlmostEqual(before - health(run, front).total, 120, places=4)
        self.assertGreater(shield.current, 1480)
        self.assertLess(shield.current, 1600)
        self.assertEqual(health(run, rein).total, 500)

    def scene(self, facing=-32768):
        run, owner, rein = arena()
        self.assertTrue(run.match.switch_hero(rein, heroes()[reinhardt_barrier.HERO]))
        rein.mover.position[:] = (0, 1, 6)
        owner.body_script.component.set(12918, 0)
        fight = run.match.combat

        def step(attack=0, defense=2, count=1):
            for _ in range(count):
                x, y, z = rein.mover.position
                angles = aim(fight._eye(owner), (x, y + 1, z))
                run.step({owner: (*angles, attack), rein: (facing, 0, defense)})

        step(defense=0, count=20)
        step(count=10)
        shield = fight.reinhardt_barriers.states[rein.body]
        self.assertTrue(shield.active)
        self.assertEqual(shield.maximum, 1600)
        return run, owner, rein, shield, step

    def test_beam_and_orb_damage_only_active_barrier(self):
        run, owner, rein, shield, step = self.scene()
        step(1, count=5)
        self.assertAlmostEqual(shield.current, 1600 - 5 * 60 * .016, places=3)
        step()
        step(2, count=90)
        step()
        self.assertEqual(len(run.match.combat.rockets), 1)
        step(count=25)
        self.assertAlmostEqual(shield.current, 1480 - 5 * 60 * .016, places=3)
        self.assertEqual(health(run, rein).total, 500)
        self.assertFalse(owner.client.messages(combat.HIT))
        self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 0)
        self.assertEqual(owner.body_script.component.entity_var(342).value(), 0)
        instance = run.match.combat.reinhardt_barriers.instance(rein)
        self.assertEqual(instance.find_var(0, 586).value(), shield.current)

    def test_lowered_barrier_regenerates_after_delay_and_beam_hits_body(self):
        run, _owner, rein, shield, step = self.scene()
        step(2, count=90)
        step(count=25)
        self.assertAlmostEqual(shield.current, 1480)
        step(defense=0, count=5)
        self.assertFalse(shield.active)
        step(1, 0, count=5)
        self.assertLess(health(run, rein).total, 500)
        step(defense=0, count=100)
        self.assertEqual(shield.current, 1480)
        step(defense=0, count=35)
        self.assertGreater(shield.current, 1480)
        step(count=10)
        self.assertTrue(shield.active)
        recovered = shield.current
        step(count=20)
        self.assertEqual(shield.current, recovered)

    def test_rear_body_and_nearer_world_cover_take_precedence(self):
        for cover in (False, True):
            with self.subTest(cover=cover):
                run, _owner, rein, shield, step = self.scene(-32768 if cover else 0)
                if cover:
                    wall = World([(-5, 0, 2, 5, 0, 2, 5, 8, 2),
                                  (-5, 0, 2, 5, 8, 2, -5, 8, 2)])
                    with patch.object(run.match.combat, "_collision", return_value=wall):
                        step(1, count=5)
                else:
                    step(1, count=5)
                self.assertEqual(shield.current, 1600)
                if cover:
                    self.assertEqual(health(run, rein).total, 500)
                else:
                    self.assertLess(health(run, rein).total, 500)

    def test_melee_overlaps_mesh_and_still_hits_armoured_body(self):
        run, _owner, rein, shield, step = self.scene()
        rein.mover.position[2] = 2
        step(0x800)
        self.assertAlmostEqual(shield.current, 1570)
        self.assertAlmostEqual(health(run, rein).total, 475)

    def test_breaking_orb_does_not_spill_to_body_and_following_beam_hits(self):
        run, _owner, rein, shield, step = self.scene()
        # Explicit pre-damaged pool to exercise the exact breaking hit.
        shield.current = 100
        step(2, count=90)
        step(count=25)
        self.assertEqual(shield.current, 0)
        self.assertFalse(shield.active)
        self.assertEqual(health(run, rein).total, 500)
        self.assertGreater(shield.broken_until, run.match.combat.now)
        step(1, count=5)
        self.assertLess(health(run, rein).total, 500)
        self.assertFalse(shield.active)

    @unittest.skipUnless(os.environ.get("ORPHEUS_EXTRA_GRAPH_BUNDLES"), "requires the summon graph shard")
    @unittest.skipUnless(os.environ.get("ORPHEUS_EXTRA_GRAPH_BUNDLES"), "requires the summon graph shard")
    def test_sentry_flies_through_raised_mesh_but_its_beam_is_occluded_until_release(self):
        run, owner, rein, shield, step = self.scene(facing=0)
        fight = run.match.combat
        rein.mover.position[:] = (0, 1, 1.2)
        rein.mover.body = None
        with patch.object(fight, "_collision", return_value=wall()):
            for _ in range(40):
                run.step({owner: (0, 0, 8), rein: (0, 0, 2)})
                if fight.sentries.sentries:
                    break
            [sentry] = fight.sentries.sentries
            self.assertIsNone(sentry.attached_at)
            _, crossed = fight.reinhardt_barriers.trace(
                sentry.bot, sentry.bot.position, sentry.direction, 3)
            self.assertIs(crossed, rein)
            step(count=90)
            self.assertIsNotNone(sentry.attached_at)
            self.assertAlmostEqual(sentry.bot.position[2], 2.99)
            self.assertIs(sentry.owner, owner)
            self.assertEqual(health(run, sentry.bot).total, 30)
            self.assertTrue(shield.active)
            self.assertEqual(shield.current, 1600)
            self.assertIsNone(sentry.target)
            self.assertIsNone(sentry.slow)
            self.assertEqual(health(run, rein).total, 500)
            step(defense=0, count=20)
            self.assertFalse(shield.active)
            self.assertEqual(sentry.target, rein.body)
            self.assertIsNotNone(sentry.slow)
            self.assertLess(health(run, rein).total, 500)

    @unittest.skipIf(projectile_tags.CATALOG_SHA256 is None, "requires authored shield tags")
    def test_beam_recovers_ammo_at_authored_rate_caps_and_stops_on_cover(self):
        run, owner, rein, shield, step = self.scene()
        instance = next(i for i in owner.body_script.component.instances.values()
                        if i.graph.index == symmetra.WEAPON)
        ammo = instance.find_var(0, 53)
        maximum = instance.find_var(0, 198).value()
        step(2, count=90)
        step(count=30)
        self.assertLess(ammo.value(), maximum)
        step(1, count=5)
        self.assertTrue(instance.state(95).active)
        self.assertEqual(instance.find_var(0, 536).value().id, rein.body)
        before = ammo.value()
        step(1, count=25)
        self.assertAlmostEqual(ammo.value() - before, 25 * .016 * 7, places=3)
        for _ in range(200):
            if ammo.value() >= maximum:
                break
            step(1)
        self.assertEqual(ammo.value(), maximum)
        step(1, count=10)
        self.assertEqual(ammo.value(), maximum)
        self.assertLess(shield.current, 1480)
        wall = World([(-5, 0, 2, 5, 0, 2, 5, 8, 2),
                      (-5, 0, 2, 5, 8, 2, -5, 8, 2)])
        with patch.object(run.match.combat, "_collision", return_value=wall):
            step(1, count=20)
        self.assertFalse(instance.state(95).active)
        self.assertIsNone(owner.body_script.world.beam_barrier)
        self.assertLess(ammo.value(), maximum)
        self.assertEqual(health(run, rein).total, 500)
        step(1, count=5)
        self.assertTrue(instance.state(95).active)
        step(1, 0, count=10)
        self.assertFalse(instance.state(95).active)
        self.assertIsNone(owner.body_script.world.beam_barrier)
        self.assertEqual(instance.find_var(0, 536).value().id, rein.body)
        before = ammo.value()
        step(1, 0, count=20)
        self.assertLess(ammo.value(), before)
        self.assertLess(health(run, rein).total, 500)
