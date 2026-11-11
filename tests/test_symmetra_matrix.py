"""Actual orb release and beam against an explicitly prepared 0407 Mech."""

import os
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from test_game_combat import Run, aim
from test_symmetra_charge import charge, orb_after_pvp_respawn
from test_symmetra_combat import health
from test_symmetra_turrets import check_sentry_crosses_live_defense

from ow174.game import combat, matrix, mover, projectile_tags, stats, symmetra, symmetra_turrets
from ow174.game.content import heroes
from ow174.game.script import graph
from ow174.game.transcendence import active_state

_CACHED_GRAPH_LOOKUP = graph.graph  # NPC fixtures wrap the module lookup with their fallback.


def prepare_matrix_resources(test):
    for key, module, attr in (
        ("ORPHEUS_MATRIX_GRAPHS", graph, "GRAPHS_PATH"),
        ("ORPHEUS_MATRIX_MOVERS", mover, "MOVERS_PATH"),
    ):
        path = os.environ.get(key)
        if not path:
            test.skipTest("requires prepared 0407 graph and mover resources")
        test.enterContext(patch.object(module, attr, Path(path)))
    if projectile_tags.CATALOG_SHA256 is None:
        test.skipTest("requires explicit external projectile-tag catalog")
    for cache in (graph._data, graph._records, _CACHED_GRAPH_LOOKUP, mover._movers):
        cache.cache_clear()
        test.addCleanup(cache.cache_clear)


class SymmetraMatrixTests(unittest.TestCase):
    def setUp(self):
        prepare_matrix_resources(self)

    def scene(self):
        run = Run(names=("Symmetra", "Mech"))
        owner, mech = run.players
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        self.assertTrue(run.match.switch_hero(owner, heroes()[symmetra.HERO]))
        self.assertTrue(run.match.switch_hero(mech, replace(heroes()[matrix.DVA], body=matrix.MECH)))
        self.assertAlmostEqual(mech.mover.data.stand_radius, .7)
        self.assertEqual(health(run, mech).total, 600)
        owner.mover.position[:] = (0, 1, 0)
        mech.mover.position[:] = (0, 1, 6)
        mech.team = 1 - owner.team
        fight = run.match.combat

        def step(attack=0, defense=0, count=1):
            for _ in range(count):
                angles = aim(fight._eye(owner), (0, 2.3, 6))
                run.step({owner: (*angles, attack), mech: (-32768, 0, defense)})

        return run, owner, mech, step

    def test_orb_is_absorbed_without_splash_beam_passes_and_release_restores_orb_damage(self):
        run, owner, mech, step = self.scene()
        fight = run.match.combat
        owner.body_script.component.set(12918, 0)
        step(count=75)
        step(2, count=90)
        step(2, 2, count=4)
        self.assertIsNotNone(active_state(mech, matrix.DVA, 0x02CC, 3))
        step(defense=2)
        [orb] = fight.rockets
        self.assertTrue(projectile_tags.absorbable(orb.definition))
        before = health(run, mech).total
        with patch.object(fight, "_damage", wraps=fight._damage) as damage:
            step(defense=2, count=30)
        self.assertFalse(damage.call_args_list)
        self.assertNotIn(orb, fight.rockets)
        self.assertLess(orb.flown * orb.speed, 6 - mech.mover.data.stand_radius)
        self.assertEqual(health(run, mech).total, before)
        self.assertFalse(owner.client.messages(combat.HIT))
        self.assertEqual(charge(owner), 0)
        step(1, 2, count=5)
        self.assertLess(health(run, mech).total, before)
        self.assertAlmostEqual(charge(owner), before - health(run, mech).total, places=4)
        self.assertIsNotNone(active_state(mech, matrix.DVA, 0x02CC, 3))
        step(count=20)
        self.assertIsNone(active_state(mech, matrix.DVA, 0x02CC, 3))
        before = health(run, mech).total
        initial_charge = charge(owner)
        step(2, count=90)
        step(count=30)
        self.assertLess(health(run, mech).total, before)
        self.assertAlmostEqual(charge(owner) - initial_charge, before - health(run, mech).total, places=4)

    def test_actual_melee_credits_resolved_mech_armor_damage(self):
        run, owner, mech, step = self.scene()
        owner.body_script.component.set(12918, 0)
        mech.mover.position[:] = (0, 1, 1.5)
        step(attack=0x800)
        step(count=30)
        self.assertEqual(health(run, mech).total, 575)
        self.assertEqual(charge(owner), 25)

    def test_matrix_absorbs_the_orb_in_flight_at_natural_owner_respawn(self):
        for facing, absorbed in ((-32768, True), (0, False)):
            with self.subTest(facing=facing):
                mech_hero = replace(heroes()[matrix.DVA], body=matrix.MECH)
                run, owner, mech, orb = orb_after_pvp_respawn(self, mech_hero)
                fight = run.match.combat
                pool = health(run, mech)
                self.assertEqual(pool.total, 600)
                self.assertTrue(projectile_tags.absorbable(orb.definition))
                initial_charge = charge(owner)
                run.step({mech: (facing, 0, 2)})
                self.assertIsNotNone(matrix.active(mech))
                with patch.object(fight, "_damage", wraps=fight._damage) as damage:
                    for _ in range(55):
                        if orb not in fight.rockets:
                            break
                        run.step({mech: (facing, 0, 2)})
                self.assertNotIn(orb, fight.rockets)
                self.assertIsNotNone(matrix.active(mech))
                if absorbed:
                    damage.assert_not_called()
                    self.assertEqual(pool.total, 600)
                    self.assertEqual(charge(owner), initial_charge)
                    self.assertFalse(owner.client.messages(combat.HIT))
                    self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 0)
                else:
                    self.assertEqual(damage.call_count, 2)
                    self.assertEqual(pool.total, 490)
                    self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 110)

    def test_retained_orb_respects_matrix_minimum_hold_and_release(self):
        for held in (1, 35):
            with self.subTest(held=held):
                stepped = self.orb_impact_after_matrix_release(held, gap=False)
                self.assertEqual(self.orb_impact_after_matrix_release(held, gap=True), stepped)

    def orb_impact_after_matrix_release(self, held, *, gap):
        run, owner, mech, step = self.scene()
        fight = run.match.combat
        mech.mover.position[:] = (0, 1, 18)
        step(count=75)
        angles = aim(fight._eye(owner), (0, 2.25, 18))
        for _ in range(90):
            run.step({owner: (*angles, 2)})
        for _ in range(held):
            run.step({owner: (*angles, 2), mech: (-32768, 0, 2)})
        run.step({owner: (*angles, 0), mech: (-32768, 0, 2)})
        run.step({mech: (-32768, 0, 2)})
        [orb] = fight.rockets
        self.assertIs(orb.shooter, owner)
        self.assertEqual(orb.last_tick, run.frame)
        ray = owner, orb.definition, orb.position, orb.direction, 18
        contact, _ = matrix.trace(fight, *ray)
        self.assertGreater(contact, orb.speed * combat.TICK)
        distance_to_box = orb.flown * orb.speed + contact
        start_frame = run.frame
        if gap:
            run.frame += 48
            run.match.update(run.frame * combat.TICK, run.frame)
        else:
            step(count=48)
        pool = health(run, mech)
        self.assertIsNone(matrix.active(mech))
        self.assertNotIn(orb, fight.rockets)
        distance = orb.flown * orb.speed
        self.assertEqual(health(run, owner).total, 225)
        if held == 1:
            self.assertAlmostEqual(distance, distance_to_box)
            self.assertEqual(pool.total, 600)
            self.assertIsNone(pool.last_damage_at)
            return round(distance, 6), None
        self.assertGreater(distance, distance_to_box)
        self.assertEqual(pool.total, 490)
        return round(distance, 6), round(pool.last_damage_at / combat.TICK) - start_frame

    @unittest.skipUnless(os.environ.get("ORPHEUS_EXTRA_GRAPH_BUNDLES"), "requires the summon graph shard")
    def test_live_matrix_does_not_consume_sentry_launch(self):
        run, owner, mech, step = self.scene()
        owner.body_script.component.set(12918, 0)
        self.assertTrue(projectile_tags.has_tag(symmetra_turrets.BODY, projectile_tags.EXCLUDED_TAG))
        step(count=75)
        step(defense=2, count=4)
        check_sentry_crosses_live_defense(
            self, run, owner, mech, lambda attack=0: step(attack, 2),
            lambda: active_state(mech, matrix.DVA, 0x02CC, 3),
        )
        self.assertAlmostEqual(charge(owner), 600 - health(run, mech).total, places=4)
