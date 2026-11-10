"""Actual grenade flight/airburst and storage of new projectile motion state."""

import unittest
import math

import test_mccree_combat as scenes

from ow174.game import combat
from ow174.game.server_snapshot import SCHEMA, SCOPE, _Graph, snapshot_digest


class FlashbangFlightTests(unittest.TestCase):
    def launch(self):
        run, caster, target = scenes.McCreeMeleeTests().arena(distance=20)
        for count in range(50):
            run.step({caster: (0, 0, 16 if count == 0 else 0)})
            if run.match.combat.rockets:
                return run, caster, target, run.match.combat.rockets[0]
        self.fail('Flashbang input did not produce a projectile')

    def test_actual_flashbang_resolves_authored_gravity_and_falls(self):
        run, _, _, rocket = self.launch()
        self.assertEqual(rocket.gravity, 20)
        initial_y = 30 * 0.05 / math.sqrt(1 + 0.05**2)
        initial_z = 30 / math.sqrt(1 + 0.05**2)
        self.assertAlmostEqual(rocket.velocity[1], initial_y, delta=1e-6)
        self.assertAlmostEqual(rocket.velocity[2], initial_z, delta=1e-5)
        self.assertTrue(rocket.detonate_on_expiry)
        self.assertAlmostEqual(rocket.left, 7/30)
        start = rocket.position
        for _ in range(12):
            run.step({})
        self.assertAlmostEqual(rocket.velocity[1], initial_y - 3.84, delta=1e-5)
        self.assertLess(rocket.position[1], start[1] - 0.10)
        self.assertAlmostEqual(rocket.position[2] - start[2], initial_z * 12 * combat.TICK, delta=1e-5)

    def airburst(self, *, npc=False, lateral=2, ally=False, wall=False):
        run, caster, target = scenes.McCreeMeleeTests().arena(
            npc=npc, distance=7, ally=ally, wall=wall)
        if npc:
            target.position = lateral, target.position[1], target.position[2]
        else:
            target.mover.position[0] = lateral
            target.mover.body = None  # rebuild the collision body at the fixture's new position
        fight = run.match.combat
        body = next(t for t in fight._targets() if t.owner is target)
        health = fight.vitals_of(body)
        before = health.total
        triggered = False
        for count in range(65):
            run.step({caster: (0, 0, 16 if count == 0 else 0)})
            if health.total < before:
                self.assertEqual(before - health.total, 25)
                self.assertTrue(fight.is_stunned(target))
                triggered = True
                break
        after = health.total
        for _ in range(100):
            run.step({})
        self.assertEqual(health.total, after)
        return triggered, before - health.total

    def test_range_expiry_airburst_hits_off_axis_player_and_npc_once(self):
        for npc in (False, True):
            with self.subTest(npc=npc):
                self.assertEqual(self.airburst(npc=npc), (True, 25))

    def test_airburst_rejects_allies_outside_radius_and_walls(self):
        for npc in (False, True):
            for options in ({'ally': True}, {'lateral': 5}, {'wall': True}):
                with self.subTest(npc=npc, options=options):
                    self.assertEqual(self.airburst(npc=npc, **options), (False, 0))

    def test_admitted_accelerated_projectile_matches_contiguous_world_ticks(self):
        rows = []
        for advances in (range(1, 9), (8,)):
            run, _, _, rocket = self.launch()
            run.step({})  # admit this new player projectile before the skipped world interval
            start = run.frame
            for advance in advances:
                tick = start + advance
                run.match.update(tick * combat.TICK, tick)
            rows.append((rocket.position, rocket.velocity, rocket.left, rocket.flown))
        self.assertEqual(*rows)

    def test_projectile_gravity_velocity_and_expiry_flag_are_causal_storage(self):
        shooter = object()
        rocket = combat.Rocket(shooter, 0, (0, 1, 0), (0, 0, 1), 30, 7/30, None, None)

        def digest():
            graph = _Graph({'shooter': shooter}, {})
            root = graph.value(rocket)
            return snapshot_digest({'schema': SCHEMA, 'scope': SCOPE, 'restorable': False,
                                    'roots': {'projectile': root}, 'nodes': graph.finish(), 'assets': {}})

        previous = digest()
        for name, value in (('gravity', 20.0), ('velocity', (0, -1, 30)), ('detonate_on_expiry', True)):
            setattr(rocket, name, value)
            changed = digest()
            self.assertNotEqual(previous, changed, name)
            previous = changed
