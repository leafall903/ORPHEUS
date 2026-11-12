"""Actual Mercy beam and launch-time boost on Zen projectiles."""
import os
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import test_zenyatta_absorb as absorb_tests
import test_zenyatta_deflect as deflect_tests
import test_zenyatta_nano as nano_tests
from test_game_combat import Run, aim, in_world

from ow174.game import combat, dva, matrix, mercy_boost, stats
from ow174.game.barriers import PROJECTED, ZARYA
from ow174.game.commands import FIRE, SECONDARY
from ow174.game.content import heroes
from ow174.game.script import graph
from ow174.game.zenyatta import HERO

RUNTIME_GRAPHS = graph.GRAPHS_PATH
EFFECTS = Path(r"D:\overwatch 1.7.4\verification\hero-lanes\0004\research\game_graphs_effects_174.json.gz")


def clear_graphs():
    for name in ("_data", "_records", "graph"):
        getattr(graph, name).cache_clear()


class MercyZen(unittest.TestCase):
    def setUp(self):
        self.addCleanup(clear_graphs)
        self.enterContext(patch.object(graph, "GRAPHS_PATH", EFFECTS))
        clear_graphs()
        self.run = Run(names=("Mercy", "Zen", "Enemy"))
        self.run.match.bots.clear()
        self.mercy, self.zen, self.enemy = self.run.players
        self.assertTrue(self.run.match.switch_hero(self.mercy, heroes()[mercy_boost.HERO]))
        self.assertTrue(self.run.match.switch_hero(self.zen, heroes()[HERO]))
        self.mercy.team = self.zen.team = 0
        self.enemy.team = 1
        x, y, z = self.mercy.mover.position
        for actor, distance in ((self.zen, 4), (self.enemy, 27)):
            actor.mover.position = [x, y, z+distance]
            actor.mover.body = None
        self.fight = self.run.match.combat
        self.staff = aim(self.fight._eye(self.mercy), (x, y+1, z+4))
        self.shot = aim(self.fight._eye(self.zen), (x, y+1, z+27))
        for _ in range(30):
            self.step()

    def step(self, beam=0, weapon=0):
        self.run.step({self.mercy: (*self.staff, beam), self.zen: (*self.shot, weapon)})

    def cast_nano_on_zen(self):
        ana = self.run.match.add_player(4, "Ana", nano_tests.ANA, self.zen.team, False)
        in_world(self.run.match, ana)
        self.assertTrue(self.run.match.switch_hero(ana, heroes()[nano_tests.ANA]))
        ana.team = self.zen.team
        self.run.players.append(ana)
        x, y, z = self.zen.mover.position
        ana.mover.position = [x + 5, y, z]
        ana.mover.body = None
        for _ in range(25):
            self.step()
        nano_tests.ZenyattaNanoTests().cast(self.run, ana, self.zen)
        expiry = self.fight.vitals[self.zen.body].nano_until
        self.assertGreater(expiry, self.fight.now)
        return expiry

    def test_actual_nano_beam_and_late_discord_stack_on_primary_with_split_credit(self):
        self.cast_nano_on_zen()
        for actor in (self.zen, self.mercy):
            actor.body_script.ult_seconds = 0
        self.mercy.body_script.component.set(12918, 0.0)
        zen_charge = self.zen.body_script.component.entity_var(342)
        mercy_charge = self.mercy.body_script.component.entity_var(342)
        for _ in range(28):
            self.step(SECONDARY)
        self.step(SECONDARY, FIRE)
        rocket, = self.fight.rockets
        self.assertAlmostEqual(rocket.direct.amount, 93.6, places=4)
        self.assertIsNone(self.fight.zenyatta.owners[self.zen.body].discord)
        before = zen_charge.value(), mercy_charge.value()
        self.step(0, 0x08)
        self.assertIn(rocket, self.fight.rockets)
        self.assertEqual(self.fight.zenyatta.owners[self.zen.body].discord.target, self.enemy.body)
        for _ in range(65):
            self.step()
        self.assertAlmostEqual(self.fight.vitals[self.enemy.body].total, 83, places=4)
        hit, = self.zen.client.messages(combat.HIT)
        self.assertEqual(hit["+0x78"]["+0x4"], 117)
        self.assertAlmostEqual(zen_charge.value() - before[0], 90, places=4)
        self.assertAlmostEqual(mercy_charge.value() - before[1], 27, places=4)

    def test_actual_triple_boost_orb_hits_late_projection_without_body_credit(self):
        self.cast_nano_on_zen()
        zarya = self.run.match.add_player(5, "Zarya", ZARYA, self.enemy.team, False)
        in_world(self.run.match, zarya)
        self.assertTrue(self.run.match.switch_hero(zarya, heroes()[ZARYA]))
        zarya.team = self.enemy.team
        self.run.players.append(zarya)
        x, y, z = self.enemy.mover.position
        zarya.mover.position = [x + 4, y, z]
        zarya.mover.body = None
        for actor in (self.zen, self.mercy):
            actor.body_script.ult_seconds = 0
        self.mercy.body_script.component.set(12918, 0.0)
        for _ in range(28):
            self.step(SECONDARY)
        self.step(SECONDARY, 0x08)
        owner = self.fight.zenyatta.owners[self.zen.body]
        self.assertEqual(owner.discord.target, self.enemy.body)
        self.step(SECONDARY, FIRE)
        rocket, = self.fight.rockets
        self.assertAlmostEqual(rocket.direct.amount, 93.6, places=4)
        charge = [actor.body_script.component.entity_var(342) for actor in (self.zen, self.mercy)]
        before = [slot.value() for slot in charge]
        projection = aim(self.fight._eye(zarya), (x, y + 1.5, z))
        self.run.step({zarya: (*projection, 0x10)})
        shield = self.fight.barriers.bubbles[zarya.body, PROJECTED]
        self.assertEqual(shield.target, self.enemy.body)
        self.assertIsNone(owner.discord)
        self.assertIn(rocket, self.fight.rockets)
        for _ in range(65):
            self.step()
        self.assertAlmostEqual(shield.hp, 106.4, places=4)
        self.assertEqual(self.fight.vitals[self.enemy.body].total, 200)
        self.assertFalse(self.zen.client.messages(combat.HIT))
        self.assertEqual([slot.value() for slot in charge], before)

    def test_actual_charged_orbs_sample_nano_expiry_individually_with_beam_and_discord(self):
        expiry = self.cast_nano_on_zen()
        hp = self.fight.vitals[self.enemy.body] = combat.Vitals(self.enemy.body, 1000)
        for actor in (self.zen, self.mercy):
            actor.body_script.ult_seconds = 0
        self.mercy.body_script.component.set(12918, 0.0)
        while self.fight.now < expiry - 2.3:
            self.step()
        self.step(SECONDARY, 0x08)
        for _ in range(130):
            self.step(SECONDARY, SECONDARY)
        self.assertEqual(self.fight.zenyatta.owners[self.zen.body].discord.target, self.enemy.body)
        self.assertAlmostEqual(mercy_boost.bonus(self.fight, self.zen), .3, places=5)
        zen_charge = self.zen.body_script.component.entity_var(342)
        mercy_charge = self.mercy.body_script.component.entity_var(342)
        before = zen_charge.value(), mercy_charge.value()
        launches = []
        expected_hits = []
        for _ in range(85):
            self.step(SECONDARY)
            for rocket in self.fight.rockets:
                if rocket.shooter is not self.zen or any(old is rocket for old in launches):
                    continue
                launches.append(rocket)
                boosted = self.fight.now < expiry
                self.assertAlmostEqual(rocket.direct.amount, 93.6 if boosted else 62.4, places=4)
                expected_hits.append(117 if boosted else 78)
        self.assertEqual(len(launches), 5)
        self.assertIn(117, expected_hits)
        self.assertIn(78, expected_hits)
        self.assertEqual([hit["+0x78"]["+0x4"] for hit in self.zen.client.messages(combat.HIT)], expected_hits)
        self.assertAlmostEqual(hp.total, 1000 - sum(expected_hits), places=3)
        weapon = next(i for i in self.zen.body_script.component.instances.values() if i.graph.index == 0x0164)
        self.assertEqual(weapon.vars[53].value(), 15)
        self.assertAlmostEqual(zen_charge.value() - before[0], sum(expected_hits) / 1.3, places=3)
        self.assertAlmostEqual(mercy_charge.value() - before[1], sum(expected_hits) * .3 / 1.3, places=3)

    def test_actual_deflect_retains_launch_boost_but_uses_new_credit_and_victim_status(self):
        for case in ("nano", "expired", "ultimate", "lethal"):
            with self.subTest(case=case):
                if case != "nano":
                    self.setUp()
                genji = self.enemy
                self.assertTrue(self.run.match.switch_hero(genji, heroes()[0x02E0000000000029]))
                x, y, z = self.zen.mover.position
                genji.mover.position = [x, y, z + 8]
                genji.mover.body = None
                self.shot = aim(self.fight._eye(self.zen), (x, y + 1, z + 8))
                expiry = self.cast_nano_on_zen()

                marker = self.run.match.add_player(5, "Enemy Zen", HERO, genji.team, False)
                in_world(self.run.match, marker)
                self.assertTrue(self.run.match.switch_hero(marker, heroes()[HERO]))
                marker.team = genji.team
                self.run.players.append(marker)
                marker.mover.position = [x + 5, y, z + 3]
                marker.mover.body = None
                for actor in (self.zen, self.mercy):
                    actor.body_script.ult_seconds = 0
                self.mercy.body_script.component.set(12918, 0.0)
                for _ in range(28):
                    self.step(SECONDARY)
                marker_aim = aim(self.fight._eye(marker), (x, y + combat.EYE, z))
                self.run.step({marker: (*marker_aim, 0x08), self.mercy: (*self.staff, SECONDARY)})
                self.assertEqual(self.fight.zenyatta.owners[marker.body].discord.target, self.zen.body)
                if case == "expired":
                    while self.fight.now < expiry - .09:
                        self.step(SECONDARY)
                if case == "lethal":
                    self.fight.vitals[self.zen.body].damage(150)
                charges = [actor.body_script.component.entity_var(342) for actor in (self.zen, self.mercy)]
                before = [slot.value() for slot in charges]
                guard = {genji: (-32768, 0, 0x10)}
                self.run.step({**guard, self.mercy: (*self.staff, SECONDARY), self.zen: (*self.shot, FIRE)})
                rocket, = self.fight.rockets
                self.assertLess(self.fight.now, expiry)
                self.assertAlmostEqual(rocket.direct.amount, 93.6, places=4)
                self.assertIs(rocket.direct.boost_credit[0], self.mercy.body_script)
                for _ in range(12):
                    self.run.step(guard)
                    if rocket.shooter is genji:
                        break
                self.assertIs(rocket.shooter, genji)
                self.assertIn(rocket, self.fight.rockets)
                self.assertAlmostEqual(rocket.direct.amount, 93.6, places=4)
                self.assertIsNone(rocket.direct.boost_credit)
                if case == "ultimate":
                    self.zen.body_script.set_ult_charge(self.zen.body_script.component.entity_var(338).value())
                    self.run.step({**guard, self.zen: (*self.shot, 0x20)})
                    self.assertTrue(self.fight.zenyatta.ultimate(self.zen))
                    before[0] = charges[0].value()
                for _ in range(20):
                    self.run.step(guard)
                    if rocket not in self.fight.rockets:
                        break
                self.assertNotIn(rocket, self.fight.rockets)
                self.assertEqual(self.fight.now >= expiry, case == "expired")
                self.assertFalse(self.zen.client.messages(combat.HIT))
                self.assertEqual(self.fight.vitals[genji.body].total, 200)
                expected = {"nano": 58.5, "expired": 117, "ultimate": 0, "lethal": 50}[case]
                starting_hp = 50 if case == "lethal" else 200
                self.assertAlmostEqual(self.fight.vitals[self.zen.body].total, starting_hp - expected, places=4)
                self.assertAlmostEqual(charges[0].value() - before[0], 0, places=4)
                self.assertAlmostEqual(charges[1].value() - before[1], 0, places=4)
                self.assertAlmostEqual(genji.stats.value(stats.HERO_DAMAGE), expected, places=4)
                hits = genji.client.messages(combat.HIT)
                if expected:
                    hit, = hits
                    self.assertEqual(hit["+0x78"]["+0x0"]["+0x0"], self.zen.body)
                    self.assertEqual(hit["+0x78"]["+0x8"], combat.KILLING if case == "lethal" else 0)
                    if case == "lethal":
                        self.assertEqual(self.fight.vitals[self.zen.body].killer, genji.body)
                else:
                    self.assertFalse(hits)

    def test_actual_grasp_converts_boosted_reflected_orb_without_damage_credit(self):
        grasp = absorb_tests.ZenyattaAbsorbTests()
        for active in (False, True):
            with self.subTest(grasp=active):
                if active:
                    self.setUp()
                match = self.run.match
                match.game_map = replace(match.game_map, free_for_all=False)
                genji = self.enemy
                self.assertTrue(match.switch_hero(genji, heroes()[0x02E0000000000029]))
                sigma_hero = int(graph.bodies()[0x0400000000002AB1]["hero"], 16)
                sigma = match.add_player(5, "Sigma", sigma_hero, self.zen.team, False)
                in_world(match, sigma)
                self.assertTrue(match.switch_hero(sigma, heroes()[sigma_hero]))
                sigma.team = self.zen.team
                self.run.players.append(sigma)
                x, y, z = self.zen.mover.position
                genji.mover.position = [x, y, z + 13]
                sigma.mover.position = [x, y, z + 3]
                genji.mover.body = sigma.mover.body = None
                self.shot = aim(self.fight._eye(self.zen), (x, y + 1, z + 13))
                self.cast_nano_on_zen()
                for actor in (self.zen, self.mercy):
                    actor.body_script.ult_seconds = 0
                self.mercy.body_script.component.set(12918, 0.0)
                charges = [actor.body_script.component.entity_var(342) for actor in (self.zen, self.mercy)]
                before = [slot.value() for slot in charges]
                guard = {
                    genji: (*aim(self.fight._eye(genji), (x, y + 1, z + 3)), 0x10),
                    sigma: (0, 0, 0x08 if active else 0),
                }
                beam = {self.mercy: (*self.staff, SECONDARY)}
                for _ in range(22):
                    self.run.step(beam)
                for _ in range(8):
                    self.run.step({**beam, **guard})
                self.assertEqual(grasp.active(sigma), active)
                self.run.step({**beam, **guard, self.zen: (*self.shot, FIRE)})
                orb, = self.fight.rockets
                self.assertAlmostEqual(orb.direct.amount, 93.6, places=4)
                self.assertIs(orb.direct.boost_credit[0], self.mercy.body_script)
                for _ in range(35):
                    self.run.step(guard)
                    if orb not in self.fight.rockets:
                        break
                self.assertIs(orb.shooter, genji)
                self.assertIsNone(orb.direct.boost_credit)
                self.assertNotIn(orb, self.fight.rockets)
                self.assertEqual(grasp.vitals(self.run, self.zen).total, 200)
                self.assertEqual(grasp.vitals(self.run, genji).total, 200)
                self.assertFalse(self.zen.client.messages(combat.HIT))
                self.assertEqual([slot.value() for slot in charges], before)
                hp = grasp.vitals(self.run, sigma)
                hits = genji.client.messages(combat.HIT)
                absorbed = grasp.instance(sigma).find_var(0, 21312).value()
                if active:
                    self.assertAlmostEqual(absorbed, 93.6, places=4)
                    self.assertEqual(hp.total, 400)
                    self.assertFalse(hits)
                    self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), 0)
                else:
                    self.assertIsNone(absorbed)
                    self.assertAlmostEqual(hp.total, 306.4, places=4)
                    hit, = hits
                    self.assertEqual(hit["+0x78"]["+0x0"]["+0x0"], sigma.body)
                    self.assertAlmostEqual(genji.stats.value(stats.HERO_DAMAGE), 93.6, places=4)
                for _ in range(110):
                    self.run.step({})
                self.assertFalse(grasp.active(sigma))
                self.assertAlmostEqual(hp.bonus_shields, 56.16 if active else 0, places=4)

    @unittest.skipUnless(os.environ.get("ORPHEUS_DVA_RUNTIME") and matrix.TAGS_PATH,
                         "requires the prepared 0407 runtime and Matrix projectile tag catalog")
    def test_boosted_charged_return_samples_friendly_matrix_release_per_orb(self):
        match = self.run.match
        match.game_map = replace(match.game_map, free_for_all=False)
        genji = self.enemy
        self.assertTrue(match.switch_hero(genji, heroes()[0x02E0000000000029]))
        # The effects dump binds Mercy; the prepared runtime adds the authored 0407 mech.
        effects = graph._data()
        with patch.object(graph, "GRAPHS_PATH", RUNTIME_GRAPHS):
            clear_graphs()
            runtime = graph._data()
        clear_graphs()
        records = {record["index"]: record for record in runtime["graphs"]}
        records.update((record["index"], record) for record in effects["graphs"])
        catalog = {
            **effects,
            "graphs": list(records.values()),
            "entities": {**runtime.get("entities", {}), **effects.get("entities", {})},
        }
        self.enterContext(patch.object(graph, "_data", return_value=catalog))
        mech = match.add_player(5, "D.Va", dva.HERO, self.zen.team, False)
        in_world(match, mech)
        self.assertTrue(match.switch_hero(mech, replace(heroes()[dva.HERO], body=dva.MECH)))
        mech.team = self.zen.team
        self.run.players.append(mech)
        x, y, z = self.zen.mover.position
        genji.mover.position = [x, y, z + 24]
        mech.mover.position = [x, y, z + 3]
        genji.mover.body = mech.mover.body = None
        self.shot = aim(self.fight._eye(self.zen), (x, y + 1, z + 24))
        expiry = self.cast_nano_on_zen()
        for actor in (self.zen, self.mercy):
            actor.body_script.ult_seconds = 0
        self.mercy.body_script.component.set(12918, 0.0)
        for _ in range(120):
            self.step(SECONDARY, SECONDARY)
        guard = {
            genji: (*aim(self.fight._eye(genji), (x, y + 1, z + 3)), 0x10),
            mech: (0, 0, SECONDARY),
            self.mercy: (*self.staff, SECONDARY),
        }
        for _ in range(10):
            self.run.step({**guard, self.zen: (*self.shot, SECONDARY)})
        instance = next(i for i in mech.body_script.component.instances.values() if i.graph.index == 0x02CC)
        self.assertTrue(instance.state(3).active)
        hp = self.fight.vitals[mech.body]
        self.assertEqual(hp.total, 600)
        charges = [actor.body_script.component.entity_var(342) for actor in (self.zen, self.mercy)]
        before = [slot.value() for slot in charges]
        launches = []
        released = False
        for _ in range(100):
            self.run.step(guard)
            for orb in self.fight.rockets:
                if all(orb is not launch for launch in launches):
                    self.assertIs(orb.shooter, self.zen)
                    self.assertEqual(orb.definition, 0x0400000000000261)
                    self.assertAlmostEqual(orb.direct.amount, 93.6, places=4)
                    launches.append(orb)
            retired = [orb for orb in launches if orb not in self.fight.rockets]
            if not released and len(retired) == 2:
                self.assertTrue(instance.state(3).active)
                self.assertTrue(self.fight.rockets)
                self.assertTrue(all(orb.shooter is genji for orb in retired))
                self.assertEqual(hp.total, 600)
                self.assertFalse(genji.client.messages(combat.HIT))
                guard[mech] = (0, 0, 0)
                released = True
            if len(retired) == 5:
                break
        self.assertTrue(released)
        self.assertLess(self.fight.now, expiry)
        self.assertEqual(len(launches), 5)
        self.assertTrue(all(orb.shooter is genji and orb.direct.boost_credit is None for orb in launches))
        self.assertFalse(self.fight.rockets)
        self.assertFalse(self.fight.projectiles.shots)
        self.assertFalse(instance.state(3).active)
        self.assertAlmostEqual(hp.total, 600 - 3 * 88.6, places=4)
        self.assertAlmostEqual(genji.stats.value(stats.HERO_DAMAGE), 3 * 88.6, places=4)
        hits = [notice["+0x78"] for notice in genji.client.messages(combat.HIT)]
        self.assertEqual([(hit["+0x0"]["+0x0"], hit["+0x4"], hit["+0x8"]) for hit in hits],
                         [(mech.body, 89, 0)] * 3)
        self.assertFalse(self.zen.client.messages(combat.HIT))
        self.assertEqual([slot.value() for slot in charges], before)
        weapon = next(i for i in self.zen.body_script.component.instances.values() if i.graph.index == 0x0164)
        self.assertEqual(weapon.vars[53].value(), 15)

    def test_beam_and_discord_combine_on_orb_after_release(self):
        for _ in range(28):
            self.step(SECONDARY)
        self.assertAlmostEqual(mercy_boost.bonus(self.fight, self.zen), .3, places=5)
        self.step(SECONDARY, 8)
        self.assertEqual(self.fight.zenyatta.owners[self.zen.body].discord.target, self.enemy.body)
        self.step(SECONDARY, FIRE)
        rocket, = self.fight.rockets
        self.assertAlmostEqual(rocket.direct.amount, 62.4, places=4)
        for _ in range(50):
            self.step()
        self.assertEqual(mercy_boost.bonus(self.fight, self.zen), 0)
        self.assertAlmostEqual(self.fight.vitals[self.enemy.body].total, 122, places=4)

    def test_beam_attached_during_flight_does_not_rescale_existing_orb(self):
        self.step(weapon=FIRE)
        rocket, = self.fight.rockets
        self.assertEqual(rocket.direct.amount, 48)
        for _ in range(25):
            self.step(SECONDARY)
            if mercy_boost.bonus(self.fight, self.zen):
                break
        self.assertIn(rocket, self.fight.rockets)
        self.assertAlmostEqual(mercy_boost.bonus(self.fight, self.zen), .3, places=5)
        for _ in range(40):
            self.step(SECONDARY)
        self.assertEqual(self.fight.vitals[self.enemy.body].total, 152)

    def test_full_charged_volley_samples_beam_for_each_orb(self):
        health = self.fight.vitals[self.enemy.body] = combat.Vitals(self.enemy.body, 1000)
        self.step(SECONDARY, 8)
        for _ in range(130):
            self.step(SECONDARY, SECONDARY)
        self.assertEqual(health.total, 1000)
        for _ in range(75):
            self.step(SECONDARY)
        self.assertAlmostEqual(health.total, 610, places=3)
        hits = self.zen.client.messages(combat.HIT)
        self.assertEqual([hit["+0x78"]["+0x4"] for hit in hits], [78]*5)

    def test_actual_melee_combines_beam_and_discord_without_critical(self):
        x, y, z = self.zen.mover.position
        self.enemy.mover.position = [x, y, z+1.5]
        self.enemy.mover.body = None
        self.shot = (0, 0)
        for _ in range(28):
            self.step(SECONDARY)
        self.step(SECONDARY, 8)
        for index in range(35):
            self.step(SECONDARY, 0x800 if index == 0 else 0)
        hp = self.fight.vitals[self.enemy.body]
        self.assertAlmostEqual(hp.total, 151.25, places=4)
        hit, = self.zen.client.messages(combat.HIT)
        self.assertEqual(hit["+0x78"]["+0x8"], 0)

    def test_boosted_melee_samples_deflect_before_projection_then_reacquires_discord(self):
        for front in (True, False):
            with self.subTest(front=front):
                if not front:
                    self.setUp()
                genji = self.enemy
                self.assertTrue(self.run.match.switch_hero(genji, heroes()[0x02E0000000000029]))
                x, y, z = self.zen.mover.position
                genji.mover.position = [x, y, z + 1.5]
                genji.mover.body = None
                self.shot = aim(self.fight._eye(self.zen), (x, y + combat.EYE, z + 1.5))
                expiry = self.cast_nano_on_zen()
                match = self.run.match
                zarya = match.add_player(5, "Zarya", ZARYA, genji.team, False)
                in_world(match, zarya)
                self.assertTrue(match.switch_hero(zarya, heroes()[ZARYA]))
                zarya.team = genji.team
                self.run.players.append(zarya)
                zarya.mover.position = [x + 4, y, z + 1.5]
                zarya.mover.body = None
                for actor in (self.zen, self.mercy):
                    actor.body_script.ult_seconds = 0
                self.mercy.body_script.component.set(12918, 0.0)
                for _ in range(28):
                    self.step(SECONDARY)
                self.step(SECONDARY, 0x08)
                owner = self.fight.zenyatta.owner(self.zen)
                self.assertEqual(owner.discord.target, genji.body)
                beam = {self.mercy: (*self.staff, SECONDARY)}
                guard = {**beam, genji: (-32768 if front else 0, 0, 0x10)}
                project = aim(self.fight._eye(zarya), (x, y + 1.5, z + 1.5))
                self.run.step({**guard, zarya: (*project, 0x10)})
                key = zarya.body, PROJECTED
                bubble = self.fight.barriers.bubbles[key]
                self.assertEqual((bubble.target, bubble.hp), (genji.body, 200))
                self.assertIsNone(owner.discord)
                self.assertTrue(deflect_tests.ZenyattaDeflectTests().active(genji))
                charge = [actor.body_script.component.entity_var(342) for actor in (self.zen, self.mercy)]
                before = [slot.value() for slot in charge]

                self.run.step({**guard, self.zen: (*self.shot, 0x800)})
                for _ in range(24):
                    self.run.step(guard)
                self.assertAlmostEqual(bubble.hp, 200 if front else 141.5)
                hp = self.fight.vitals[genji.body]
                self.assertEqual(hp.total, 200)
                self.assertFalse(self.zen.client.messages(combat.HIT))
                self.assertFalse(genji.client.messages(combat.HIT))
                self.assertEqual([slot.value() for slot in charge], before)

                for _ in range(145):
                    self.step(SECONDARY)
                self.assertNotIn(key, self.fight.barriers.bubbles)
                self.assertFalse(deflect_tests.ZenyattaDeflectTests().active(genji))
                self.assertLess(self.fight.now, expiry)
                self.assertIsNone(owner.discord)
                self.step(SECONDARY, 0x08)
                self.assertEqual(owner.discord.target, genji.body)
                self.step(SECONDARY, 0x800)
                for _ in range(24):
                    self.step(SECONDARY)
                self.assertAlmostEqual(hp.total, 126.875)
                hit, = self.zen.client.messages(combat.HIT)
                self.assertEqual(hit["+0x78"]["+0x0"]["+0x0"], genji.body)
                self.assertEqual(hit["+0x78"]["+0x8"], 0)
                self.assertAlmostEqual(charge[0].value() - before[0], 56.25, places=4)
                self.assertAlmostEqual(charge[1].value() - before[1], 16.875, places=4)

    def test_cover_breaks_actual_beam_before_next_orb(self):
        from test_game_mover import box, wall

        from ow174.game.collision import World

        for _ in range(28):
            self.step(SECONDARY)
        self.assertAlmostEqual(mercy_boost.bonus(self.fight, self.zen), .3, places=5)
        x, y, z = self.mercy.mover.position
        self.run.match.collision_world = World(
            box(x-35, x+35, y-2, y, z-35, z+35) + wall(x-6, z+2, x+6, z+2, y, y+5)
        )
        for _ in range(12):
            self.step(SECONDARY)
        self.assertEqual(mercy_boost.bonus(self.fight, self.zen), 0)
        self.step(SECONDARY, FIRE)
        for _ in range(40):
            self.step(SECONDARY)
        self.assertEqual(self.fight.vitals[self.enemy.body].total, 152)

    def test_actual_boost_awards_only_extra_damage_to_launch_caster(self):
        for actor in (self.zen, self.mercy):
            actor.body_script.ult_seconds = 0
        mercy_charge = self.mercy.body_script.component.entity_var(342)
        zen_charge = self.zen.body_script.component.entity_var(342)
        self.mercy.body_script.component.set(12918, 0.0)
        for _ in range(28):
            self.step(SECONDARY)
        self.step(SECONDARY, 8)
        self.step(SECONDARY, FIRE)
        projectile, = self.fight.rockets
        self.assertIs(projectile.direct.boost_credit[0], self.mercy.body_script)
        before = mercy_charge.value(), zen_charge.value()
        for _ in range(60):
            self.step()
        self.assertAlmostEqual(mercy_charge.value()-before[0], 18, places=4)
        self.assertAlmostEqual(zen_charge.value()-before[1], 60, places=4)

    def test_overkill_and_replaced_booster_cannot_claim_extra_charge(self):
        for mode, remaining, expected in (("overkill", 40, 0), ("partial", 55, 7), ("replace", 200, 0)):
            with self.subTest(mode=mode):
                self.setUp()
                health = self.fight.vitals[self.enemy.body]
                health.damage(200-remaining)
                original = self.mercy.body_script
                original.ult_seconds = 0
                original.component.set(12918, 0.0)
                for _ in range(28):
                    self.step(SECONDARY)
                self.step(SECONDARY, FIRE)
                if mode == "replace":
                    self.assertTrue(self.run.match.switch_hero(self.mercy, self.mercy.hero))
                    self.assertTrue(original.retired)
                    self.mercy.body_script.ult_seconds = 0
                    self.mercy.body_script.component.set(12918, 0.0)
                slot = self.mercy.body_script.component.entity_var(342)
                before = slot.value()
                for _ in range(65):
                    self.step()
                self.assertAlmostEqual(slot.value()-before, expected, places=4)
                self.assertAlmostEqual(health.total, 137.6 if mode == "replace" else 0, places=4)
