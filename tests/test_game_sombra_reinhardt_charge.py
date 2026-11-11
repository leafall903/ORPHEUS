"""Actual 007D Charge against Sombra and prepared Talon bodies."""

import math
import unittest
from dataclasses import replace

import test_game_sombra_combat as scenes
import test_game_sombra_grenade as grenades
import test_game_sombra_npc as npc_fixture
from test_game_combat import aim
from test_game_sombra_orisa_halt import place
from test_game_sombra_reaper_blossom import charge
from test_game_sombra_reinhardt import cast, prepare

from ow174.game import combat, fortify, heavy_assault, mei, reinhardt_barrier, reinhardt_charge
from ow174.game.content import heroes
from ow174.game.server_snapshot import SCHEMA, SCOPE, _Graph, snapshot_digest
from ow174.game.world import ARMOUR_POOL, HEALTH_POOL, SHIELDS_POOL

FORWARD = (0, 1.3, -65)


def surface(f, wall=None):
    f.floor = [(-20, 0, -68, -20, 0, 106, 20, 0, 106),
               (-20, 0, -68, 20, 0, 106, 20, 0, -68)]
    if wall is None:
        f.geometry()
    else:
        f.geometry((-9, 0, wall, 9, 0, wall, 9, 5, wall), (-9, 0, wall, 9, 5, wall, -9, 5, wall))


def step(f, buttons=0, frames=1):
    cast(f, buttons, FORWARD, frames)


class SombraReinhardtChargeTests(unittest.TestCase):
    def arena(self, distance=6, wall=None):
        f = scenes.SombraCombatTests()
        f.setUp()
        prepare(f, distance)
        surface(f, wall)
        return f

    def test_actual_pin_carries_sombra_then_wall_impact_kills_and_starts_cooldown(self):
        f = self.arena(wall=-4)
        fight = f.run.match.combat
        step(f, 8, 37)
        self.assertEqual(tuple(f.b.mover.position), (0, 0, 6))
        step(f, frames=12)
        self.assertAlmostEqual((6 - f.b.mover.position[2]) / (.016 * 11), 16.5, delta=.03)
        step(f, frames=7)
        self.assertEqual(fight.pins[f.b.body], f.a.body)
        self.assertEqual(f.health(f.a), 200)
        f.press(1, 5)
        self.assertFalse(f.a.client.messages(combat.HIT))
        step(f, frames=40)
        self.assertTrue(fight.is_dead(f.a))
        self.assertEqual(fight.vitals[f.a.body].killer, f.b.body)
        self.assertEqual(charge(f.b), 200)
        self.assertFalse(fight.rein_charges)
        self.assertFalse(fight.pins)
        self.assertGreater(reinhardt_charge.instance_for(f.b).state(46).remaining(), 9)
        after = tuple(f.b.mover.position)
        step(f, 8, 63)
        self.assertEqual(tuple(f.b.mover.position), after)

    def test_silence_and_wall_cooldown_refuse_press_then_allow_a_new_charge(self):
        f = self.arena(wall=2)
        # Establish Hack from the same side of the wall as the caster.
        place(f.a, (2.5, 0, 4))
        fight = f.run.match.combat
        f.press(2, 75)
        self.assertIn(f.b.body, fight.hacks)
        place(f.a, (9, 0, 0))
        step(f, 8, 62)
        self.assertEqual(f.b.mover.position[2], 6)
        self.assertFalse(fight.rein_charges)
        step(f, frames=300)
        self.assertNotIn(f.b.body, fight.hacks)
        step(f, 8, 71)
        stopped = f.b.mover.position[2]
        self.assertLess(stopped, 6)
        self.assertGreater(stopped, 2)
        self.assertFalse(fight.rein_charges)
        surface(f)
        step(f, 8, 60)
        self.assertEqual(f.b.mover.position[2], stopped)
        step(f, frames=610)
        step(f, 8, 56)
        self.assertLess(f.b.mover.position[2], stopped - 2)
        self.assertIn(f.b.body, fight.rein_charges)

    def test_actual_side_bump_damages_once_and_does_not_pin(self):
        f = self.arena()
        fight = f.run.match.combat
        place(f.a, (1.55, 0, 0))
        step(f, 8, 68)
        self.assertEqual(f.health(f.a), 150)
        self.assertEqual(charge(f.b), 50)
        self.assertGreater(f.a.mover.position[1], 0.5)
        self.assertGreater(f.a.mover.position[0], 1.55)
        self.assertFalse(fight.pins)
        step(f, frames=35)
        self.assertEqual(f.health(f.a), 150)

    def test_wall_in_front_prevents_capture_and_allied_target_is_excluded(self):
        for cover in (True, False):
            with self.subTest(cover=cover):
                f = self.arena(wall=2 if cover else None)
                if not cover:
                    match = f.run.match
                    match.game_map = replace(match.game_map, free_for_all=False)
                    f.a.team = f.b.team
                step(f, 8, 76)
                self.assertEqual(f.health(f.a), 200)
                self.assertFalse(f.run.match.combat.pins)
                self.assertEqual(charge(f.b), 0)
                self.assertEqual(tuple(f.a.mover.position), (0, 0, 0))
                if cover:
                    self.assertGreater(f.b.mover.position[2], 2)
                    self.assertFalse(f.run.match.combat.rein_charges)

    def test_unobstructed_charge_naturally_releases_the_pin_without_wall_damage(self):
        f = self.arena()
        fight = f.run.match.combat
        step(f, 8, 216)
        self.assertEqual(fight.pins[f.b.body], f.a.body)
        self.assertLess(f.a.mover.position[2], -39)
        step(f, frames=19)
        self.assertFalse(fight.pins)
        self.assertFalse(fight.rein_charges)
        self.assertEqual(f.health(f.a), 200)
        self.assertEqual(charge(f.b), 0)
        self.assertGreater(reinhardt_charge.instance_for(f.b).state(46).remaining(), 9)

    def test_actual_charge_uses_the_authored_steering_rate(self):
        f = self.arena()
        place(f.a, (9, 0, 0))
        fight = f.run.match.combat
        step(f, 8, 45)
        start = fight.rein_charges[f.b.body].heading
        for _ in range(20):
            f.run.step({f.b: (16384, 0, 0)})
        angle = abs(fight.rein_charges[f.b.body].heading - start)
        self.assertAlmostEqual(angle, math.radians(30) * combat.TICK * 20, places=5)
        self.assertEqual(f.health(f.a), 200)

    def test_actual_hack_and_emp_stop_a_moving_charge_and_start_cooldown(self):
        for button in (2, 32):
            with self.subTest(button=button):
                f = self.arena(distance=11)
                fight = f.run.match.combat
                if button == 32:
                    f.a.body_script.set_ult_charge(1400)
                forward = aim(fight._eye(f.b), FORWARD)
                opposite = aim(fight._eye(f.a), (0, 1.3, 11))
                for frame in range(95):
                    hack = (2 if button == 2 else 32 if frame == 43 else 0)
                    f.run.step({f.a: (*opposite, hack), f.b: (*forward, 8 if frame == 0 else 0)})
                self.assertIn(f.b.body, fight.hacks)
                self.assertLess(f.b.mover.position[2], 11)
                self.assertGreater(f.b.mover.position[2], 1)
                self.assertFalse(fight.pins)
                self.assertFalse(fight.rein_charges)
                self.assertEqual(f.health(f.a), 200)
                self.assertGreater(reinhardt_charge.instance_for(f.b).state(46).remaining(), 8.5)

    def test_allied_actual_emp_frees_sombra_before_wall_impact(self):
        f = self.arena(wall=-12)
        match = f.run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        f.a.team, f.b.team = 0, 1
        support = grenades.SombraGrenadeTests().join_ally(f, f.a.hero.guid, (6, 0, -1))
        support.body_script.set_ult_charge(1400)
        step(f, 8, 56)
        self.assertEqual(fight.pins[f.b.body], f.a.body)
        face = aim(fight._eye(support), fight._eye(f.b))
        attack = aim(fight._eye(f.b), FORWARD)
        for frame in range(55):
            f.run.step({support: (*face, 32 if frame == 0 else 0), f.b: (*attack, 0)})
        self.assertIn(f.b.body, fight.hacks)
        self.assertFalse(fight.pins)
        self.assertFalse(fight.rein_charges)
        self.assertEqual(f.health(f.a), 200)
        self.assertEqual(charge(support), 0)
        self.assertEqual(charge(f.b), 0)

    def test_projected_barrier_absorbs_wall_impact_without_spill(self):
        f = self.arena(wall=-4)
        match = f.run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        f.a.team, f.b.team = 0, 1
        zarya = grenades.SombraGrenadeTests().join_ally(f, reinhardt_barrier.ZARYA, (7, 0, 0))
        face = aim(fight._eye(zarya), (0, 1.2, 0))
        f.run.step({zarya: (*face, 16)})
        bubble, = (s for s in fight.barriers.values() if s.owner is zarya)
        step(f, 8, 82)
        self.assertEqual(bubble.vitals.total, 0)
        self.assertEqual(f.health(f.a), 200)
        self.assertEqual(charge(f.b), 0)
        self.assertFalse(fight.pins)

    def test_fortify_and_cryo_actual_inputs_stop_charge_without_damage(self):
        protections = (fortify.HERO, mei.HERO)
        for hero in protections:
            with self.subTest(hero=hero):
                f = self.arena()
                fight = f.run.match.combat
                self.assertTrue(f.run.match.switch_hero(f.a, heroes()[hero]))
                place(f.a, (0, 0, 0))
                f.press(0, 55)
                before = f.health(f.a)
                f.press(8, 5)
                self.assertTrue(fortify.active(fight, f.a) or mei.protected(f.a))
                step(f, 8, 77)
                self.assertEqual(f.health(f.a), before)
                self.assertFalse(fight.pins)
                self.assertFalse(fight.rein_charges)
                self.assertGreater(reinhardt_charge.instance_for(f.b).state(46).remaining(), 9)

    def test_actual_translocator_escapes_before_contact_but_pin_denies_recall(self):
        for captured in (False, True):
            with self.subTest(captured=captured):
                f = self.arena(distance=6 if captured else 11)
                fight = f.run.match.combat
                yaw, pitch = aim(fight._eye(f.a), (10, .2, 0))
                for index in range(24):
                    f.run.step({f.a: (yaw, pitch, 16 if index == 0 else 0)})
                f.press(0, 140)
                beacon, = fight.translocators.entries.values()
                self.assertTrue(beacon.settled)
                self.assertGreater(beacon.position[0], 4)
                step(f, 8, 56 if captured else 43)
                self.assertEqual(fight.pins.get(f.b.body) == f.a.body, captured)
                f.press(16, 20)
                if captured:
                    self.assertIn(beacon.entity, fight.translocators.entries)
                    self.assertEqual(fight.pins[f.b.body], f.a.body)
                    step(f, frames=178)
                    f.press(0, 2)
                    f.press(16, 20)
                self.assertNotIn(beacon.entity, fight.translocators.entries)
                self.assertGreater(f.a.mover.position[0], 4)
                self.assertEqual(f.health(f.a), 200)
                self.assertFalse(fight.pins)

    def test_active_charge_pin_and_contact_history_change_stored_digest(self):
        f = self.arena()
        fight = f.run.match.combat
        step(f, 8, 56)
        contact = fight.rein_charges[f.b.body]
        self.assertEqual(contact.pinned, f.a.body)

        def digest():
            stored = _Graph({"match": f.run.match, "carrier": f.b, "recipient": f.a}, None)
            roots = {"charge": stored.value(fight.rein_charges), "pins": stored.value(fight.pins)}
            return snapshot_digest({"schema": SCHEMA, "scope": SCOPE, "restorable": False,
                                    "roots": roots, "nodes": stored.finish(), "assets": []})

        original = digest()
        self.assertEqual(digest(), original)
        contact.heading += .02
        self.assertNotEqual(digest(), original)

    def test_actual_enemy_lethal_shot_during_pin_releases_sombra(self):
        f = self.arena(wall=-4)
        fight = f.run.match.combat
        other = grenades.SombraGrenadeTests().join_ally(f, f.a.hero.guid, (5, 0, 1))
        step(f, 8, 56)
        self.assertEqual(fight.pins[f.b.body], f.a.body)
        f.health(f.b)
        vitals = fight.vitals[f.b.body]
        vitals.now[ARMOUR_POOL], vitals.now[SHIELDS_POOL], vitals.now[HEALTH_POOL] = 0, 0, 8
        angle = aim(fight._eye(other), fight._eye(f.b))
        f.run.step({other: (*angle, 1)})
        self.assertTrue(fight.is_dead(f.b))
        self.assertFalse(fight.pins)
        self.assertFalse(fight.rein_charges)
        self.assertEqual(f.health(f.a), 200)

    def test_actual_opposing_charges_knock_both_reinhardts_down(self):
        f = self.arena(distance=10)
        match = f.run.match
        fight = match.combat
        self.assertTrue(match.switch_hero(f.a, heroes()[reinhardt_charge.HERO]))
        place(f.a, (0, 0, 0))
        f.press(0, 55)
        before = f.health(f.a), f.health(f.b)
        for frame in range(77):
            f.run.step({f.a: (0, 0, 8 if frame == 0 else 0),
                        f.b: (-32768, 0, 8 if frame == 0 else 0)})
        self.assertTrue(fight.is_stunned(f.a))
        self.assertTrue(fight.is_stunned(f.b))
        self.assertEqual((f.health(f.a), f.health(f.b)), before)
        self.assertFalse(fight.pins)
        self.assertFalse(fight.rein_charges)

    def test_world_only_gap_matches_wall_impact_during_windup_and_transport(self):
        results = []
        for frames in (1, 56, 110):
            with self.subTest(frames=frames):
                f = self.arena(wall=-4)
                fight = f.run.match.combat
                step(f, 8, frames)
                if frames < 110:
                    f.run.frame += 110 - frames
                    f.run.match.update(f.run.frame * combat.TICK, f.run.frame, frames=False)
                self.assertFalse(fight.rein_charges)
                self.assertFalse(fight.pins)
                self.assertEqual(charge(f.b), 200)
                results.append((tuple(f.b.mover.position), tuple(f.a.mover.position),
                                fight.vitals[f.a.body].death_tick))
        self.assertEqual(results, [results[0]] * len(results))


class NpcReinhardtChargeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        npc_fixture.SombraActualNpcTests.setUpClass.__func__(cls)

    @classmethod
    def restore_graphs(cls, path):
        npc_fixture.SombraActualNpcTests.restore_graphs(path)

    def test_actual_heavy_assault_charge_counters_reinhardt(self):
        f, bot = npc_fixture.SombraActualNpcTests.make(self, "5")
        prepare(f, 14)
        surface(f)
        place(f.a, (8, 0, 0))
        bot.position, bot.yaw = (0, 0, 0), 0
        fight = f.run.match.combat
        before = f.health(f.b), fight.vitals[bot.entity].total
        component = bot.body_script.component
        special = next(i for i in component.instances.values() if i.graph.index == heavy_assault.GRAPH)
        component.edges([(35, True)])
        step(f, 8, 93)
        self.assertTrue(fight.is_stunned(f.b))
        self.assertTrue(fight.is_stunned(bot))
        self.assertEqual((f.health(f.b), fight.vitals[bot.entity].total), before)
        self.assertEqual(special.vars[59].value(), 0)
        self.assertFalse(fight.pins)
        self.assertFalse(fight.rein_charges)

    def test_actual_charge_transports_and_hits_all_five_npc_bodies(self):
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                f, bot = npc_fixture.SombraActualNpcTests.make(self, key)
                prepare(f, 6)
                surface(f, -4)
                place(f.a, (8, 0, 0))
                bot.position = (0, 0, 0)
                fight = f.run.match.combat
                before = fight.vitals[bot.entity].total
                step(f, 8)
                captured = False
                for _ in range(100):
                    step(f)
                    captured |= fight.pins.get(f.b.body) == bot.entity
                self.assertTrue(captured)
                self.assertLess(bot.position[2], -1)
                self.assertLess(fight.vitals[bot.entity].total, before)
                self.assertAlmostEqual(charge(f.b), before - fight.vitals[bot.entity].total, places=4)
                self.assertFalse(fight.pins)
