"""Actual 0044 Death Blossom inputs and cross-hero world damage."""

import math
import unittest
from dataclasses import replace

import test_game_sombra_combat as scene
import test_game_sombra_grenade as grenade_fixture
import test_game_sombra_matrix as matrix_fixture
import test_game_sombra_mei_wall as wall_fixture
import test_game_sombra_npc as npc_fixture
import test_game_sombra_orisa_barrier as barrier_fixture
from test_game_combat import aim
from test_game_sombra_orisa_halt import place

from ow174.game import combat, mei, reaper_blossom, reinhardt_barrier, shot_guards
from ow174.game.commands import FIRE
from ow174.game.content import heroes
from ow174.game.script.driver import ULT_CHARGE, ULT_COST
from ow174.game.server_snapshot import SCHEMA, SCOPE, _Graph, snapshot_digest
from ow174.game.world import HEALTH_POOL


def prepare(fixture, distance=3):
    match = fixture.run.match
    fixture.b.spawned = True
    assert match.switch_hero(fixture.b, heroes()[reaper_blossom.HERO])
    place(fixture.a, (0, 0, 0))
    place(fixture.b, (0, 0, distance))
    for player in fixture.a, fixture.b:
        player.body_script.ult_seconds = 0
    fixture.press(0, 50)
    return fixture


def charge(player):
    return player.body_script.component.entity_var(ULT_CHARGE).value()


def jump(fixture, tick):
    fixture.run.frame = tick
    fixture.run.match.update(tick * combat.TICK, tick)


class SombraBlossomTests(unittest.TestCase):
    def arena(self, distance=3):
        fixture = scene.SombraCombatTests()
        fixture.setUp()
        return prepare(fixture, distance)

    def launch(self, fixture):
        script = fixture.b.body_script
        self.assertEqual(script.component.entity_var(ULT_COST).value(), 2100)
        script.set_ult_charge(2100)
        fixture.press(32, shooter=fixture.b, target=fixture.a)
        self.assertEqual(charge(fixture.b), 0)
        self.assertTrue(reaper_blossom.wait_state(fixture.b))
        return fixture.run.match.combat.blossom.channels[fixture.b.body]

    def test_actual_paid_pulses_match_world_only_catchup_and_reaping(self):
        for gap in (False, True):
            with self.subTest(gap=gap):
                fixture = self.arena()
                fight = fixture.run.match.combat
                target = {item.owner: item for item in fight._targets()}
                enemy = fight.vitals_of(target[fixture.a])
                owner = fight.vitals_of(target[fixture.b])
                enemy.most[HEALTH_POOL] = enemy.now[HEALTH_POOL] = 1000
                owner.damage(220)
                fixture.b.body_script.set_ult_charge(2099)
                fixture.press(32, shooter=fixture.b, target=fixture.a)
                self.assertEqual(charge(fixture.b), 2099)
                self.assertEqual(enemy.total, 1000)
                fixture.press(0)
                channel = self.launch(fixture)
                self.assertEqual(enemy.total, 966)
                self.assertAlmostEqual(owner.total, 41.9, places=4)
                endpoint = math.ceil(channel.ends / 16) + 8
                if gap:
                    jump(fixture, endpoint)
                else:
                    fixture.press(0, endpoint - fixture.run.frame)
                self.assertEqual(enemy.total, 490)
                self.assertAlmostEqual(owner.total, 208.5, places=4)
                self.assertEqual(charge(fixture.b), 0)
                self.assertFalse(channel.due)

    def test_actual_lethal_blossom_reports_killer_and_stops_damage(self):
        fixture = self.arena()
        fight = fixture.run.match.combat
        channel = self.launch(fixture)
        jump(fixture, math.ceil(channel.ends / 16) + 1)
        enemy = fight.vitals[fixture.a.body]
        self.assertTrue(enemy.dead)
        self.assertEqual(enemy.killer, fixture.b.body)
        self.assertEqual(len(fixture.b.client.messages(combat.HIT)), 6)

    def test_actual_projected_barrier_absorbs_without_spillover(self):
        fixture = self.arena()
        match = fixture.run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        fixture.a.team, fixture.b.team = 0, 1
        zarya = grenade_fixture.SombraGrenadeTests().join_ally(
            fixture, reinhardt_barrier.ZARYA, (9, 0, 0),
        )
        facing = aim(fight._eye(zarya), (0, 1, 0))
        fixture.run.step({zarya: (*facing, 16)})
        barrier, = fight.barriers.values()
        self.assertIs(barrier.protected, fixture.a)
        self.launch(fixture)
        self.assertEqual(barrier.vitals.total, 166)
        self.assertEqual(fixture.health(fixture.a), 200)
        fixture.press(0, 63)
        self.assertEqual(barrier.vitals.total, 0)
        self.assertEqual(fixture.health(fixture.a), 200)
        fixture.press(0, 12)
        self.assertEqual(fixture.health(fixture.a), 166)

    def test_actual_genji_and_sigma_guards_do_not_intercept_pulses(self):
        for hero, button in ((shot_guards.GENJI, 16), (shot_guards.SIGMA, 8)):
            with self.subTest(hero=hero):
                fixture = self.arena()
                match = fixture.run.match
                fight = match.combat
                match.game_map = replace(match.game_map, free_for_all=False)
                fixture.a.team, fixture.b.team = 0, 1
                guard = grenade_fixture.SombraGrenadeTests().join_ally(fixture, hero, (0, 0, 1.5))
                fixture.press(0, 45)
                facing = aim(fight._eye(guard), (0, 1, 3))
                fixture.run.step({guard: (*facing, button)})
                fixture.press(0, 4)
                self.assertIsNotNone(shot_guards.active_guard(guard, fight.tick * 16))
                before = fixture.health(guard)
                self.launch(fixture)
                self.assertEqual(fixture.health(guard), before - 34)
                self.assertEqual(fixture.health(fixture.a), 166)
                self.assertEqual(fixture.health(fixture.b), 250)

    def test_actual_melee_earns_charge_and_reaping_while_hacked(self):
        fixture = self.arena(distance=1.5)
        fight = fixture.run.match.combat
        enemy = next(item for item in fight._targets() if item.owner is fixture.b)
        owner = fight.vitals_of(enemy)
        owner.damage(100)
        for _ in range(70):
            fixture.press(2)
            if fixture.b.body in fight.hacks:
                break
        self.assertIn(fixture.b.body, fight.hacks)
        fixture.b.body_script.set_ult_charge(0)
        fixture.press(scene.MELEE, shooter=fixture.b, target=fixture.a)
        fixture.press(0, 35)
        self.assertEqual(fixture.health(fixture.a), 170)
        self.assertEqual(charge(fixture.b), 30)
        self.assertAlmostEqual(owner.total, 160.5, places=4)

    def test_actual_hellfire_resolves_20_pellets_ammo_and_contact_gates(self):
        for gate in ("hit", "cover", "team", "miss", "range"):
            with self.subTest(gate=gate):
                fixture = self.arena()
                match = fixture.run.match
                fight = match.combat
                place(fixture.b, (0, 0, 1.5))
                if gate == "range":
                    fixture.floor = [(-10, 0, -10, -10, 0, 120, 10, 0, 120),
                                     (-10, 0, -10, 10, 0, 120, 10, 0, -10)]
                    fixture.geometry()
                    place(fixture.b, (0, 0, 105))
                fixture.press(0, 2)
                fixture.b.body_script.set_ult_charge(0)
                weapon = next(i for i in fixture.b.body_script.component.instances.values()
                              if i.graph.index == reaper_blossom.SHOTGUN)
                if gate == "cover":
                    fixture.wall()
                if gate == "team":
                    match.game_map = replace(match.game_map, free_for_all=False)
                    fixture.a.team = fixture.b.team
                facing = aim(fight._eye(fixture.b), (0, 1, 0))
                if gate == "miss":
                    facing = (0, 0)
                fixture.run.step({fixture.b: (*facing, FIRE)})
                fixture.press(0, 5)
                hits = fixture.b.client.messages(combat.HIT)
                self.assertEqual(weapon.vars[53].value(), 7)
                self.assertEqual(len(hits), 20 if gate == "hit" else 0)
                self.assertEqual(fixture.health(fixture.a), 80 if gate == "hit" else 200)
                self.assertEqual(charge(fixture.b), 120 if gate == "hit" else 0)

    def test_range_cover_and_team_with_late_acquisition(self):
        for gate in ("range", "cover", "team"):
            with self.subTest(gate=gate):
                fixture = self.arena(distance=10 if gate == "range" else 3)
                match = fixture.run.match
                if gate == "cover":
                    fixture.geometry((-3, 0, 2, 3, 0, 2, 3, 4, 2),
                                     (-3, 0, 2, 3, 4, 2, -3, 4, 2))
                elif gate == "team":
                    match.game_map = replace(match.game_map, free_for_all=False)
                    fixture.a.team = fixture.b.team
                self.launch(fixture)
                fixture.press(0, 25)
                self.assertEqual(fixture.health(fixture.a), 200)
                if gate == "range":
                    place(fixture.a, (0, 0, 7))
                elif gate == "cover":
                    fixture.geometry()
                else:
                    fixture.a.team = 1 - fixture.b.team
                fixture.press(0)
                self.assertEqual(fixture.health(fixture.a), 166)
                fixture.press(0, 5)
                self.assertEqual(fixture.health(fixture.a), 166)

    def test_actual_hack_and_emp_cancel_channel_without_refund(self):
        for button in (2, 32):
            with self.subTest(button=button):
                fixture = self.arena(distance=10)
                fight = fixture.run.match.combat
                self.launch(fixture)
                if button == 32:
                    fixture.a.body_script.set_ult_charge(1400)
                for offset in range(80):
                    fixture.press(button if button == 2 or offset == 0 else 0)
                    if fixture.b.body in fight.hacks:
                        break
                self.assertIn(fixture.b.body, fight.hacks)
                self.assertIsNone(reaper_blossom.wait_state(fixture.b))
                self.assertNotIn(fixture.b.body, fight.blossom.channels)
                place(fixture.a, (0, 0, 3))
                fixture.press(0, 50)
                self.assertEqual(fixture.health(fixture.a), 200)
                self.assertEqual(charge(fixture.b), 0)

    def test_first_pulse_breaks_camo_and_interrupts_hack(self):
        for button in (8, 2):
            with self.subTest(button=button):
                fixture = self.arena()
                fight = fixture.run.match.combat
                if button == 2:
                    fixture.press(2, 10)
                else:
                    fixture.press(8)
                    fixture.press(0, 60)
                camo = fixture.camo().state(13)
                self.assertTrue(fixture.weapon().vars[375].value() if button == 2
                                else camo.flags & camo.RUNNING)
                fixture.b.body_script.set_ult_charge(2100)
                first = aim(fight._eye(fixture.a), (0, 1, 3))
                second = aim(fight._eye(fixture.b), (0, 1, 0))
                fixture.run.step({fixture.a: (*first, button & 2), fixture.b: (*second, 32)})
                fixture.press(button & 2, 2)
                self.assertEqual(fixture.health(fixture.a), 166)
                self.assertFalse(fixture.hack_applications())
                if button == 2:
                    self.assertGreater(fixture.weapon().state(53).remaining(), 1.9)
                else:
                    place(fixture.b, (0, 0, 10))
                    fixture.press(0, 65)
                    self.assertEqual(fixture.health(fixture.a), 166)
                    self.assertFalse(camo.flags & camo.RUNNING)
                    self.assertGreater(camo.remaining(), 0)

    def test_passive_resumes_after_authored_channel_without_awarding_pulses(self):
        fixture = self.arena(distance=10)
        fixture.b.body_script.ult_seconds = 60
        fixture.b.body_script.set_ult_charge(0)
        fixture.press(0, 70)
        self.assertEqual(charge(fixture.b), 5)
        channel = self.launch(fixture)
        endpoint = math.ceil(channel.ends / 16)
        fixture.press(0, endpoint - fixture.run.frame)
        self.assertEqual(charge(fixture.b), 0)
        for _ in range(120):
            fixture.press(0)
            if charge(fixture.b):
                break
        self.assertEqual(charge(fixture.b), 5)

    def test_real_sombra_pistol_lethal_ends_channel_and_snapshot_tracks_due(self):
        fixture = self.arena()
        fight = fixture.run.match.combat
        channel = self.launch(fixture)

        def digest():
            store = _Graph({"combat": fight, "caster": fixture.b}, None)
            roots = {"blossom": store.value(fight.blossom)}
            return snapshot_digest({
                "schema": SCHEMA, "scope": SCOPE, "restorable": False,
                "roots": roots, "nodes": store.finish(), "assets": [],
            })

        initial = digest()
        self.assertEqual(initial, digest())
        channel.due[fixture.a.body] += 1
        self.assertNotEqual(initial, digest())
        fixture.press(FIRE)
        self.assertIn(fixture.b.body, fight.blossom.channels)
        fight.vitals[fixture.b.body].now[HEALTH_POOL] = 8
        fixture.press(FIRE, 10)
        self.assertTrue(fight.is_dead(fixture.b))
        self.assertEqual(fight.vitals[fixture.b.body].killer, fixture.a.body)
        self.assertNotIn(fixture.b.body, fight.blossom.channels)
        self.assertEqual(fixture.health(fixture.a), 166)
        fixture.press(0, 100)
        self.assertEqual(fixture.health(fixture.a), 166)

    def test_actual_ice_wall_blocks_blossom_and_takes_pulses(self):
        fixture = self.arena(distance=6)
        match = fixture.run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        fixture.a.team, fixture.b.team = 0, 1
        caster = grenade_fixture.SombraGrenadeTests().join_ally(fixture, mei.HERO, (0, 0, 0))
        fixture.press(0, 50)
        # deploy() addresses fixture.b as the caster. Preserve the actual Reaper
        # and normal input harness for the release that follows.
        reaper = fixture.b
        fixture.b = caster
        wall_fixture.deploy(fixture)
        fixture.b = reaper
        piece = next(piece for piece in fight.walls.pieces.values() if abs(piece.feet[0]) < .2)
        self.launch(fixture)
        self.assertEqual(piece.health, 366)
        self.assertEqual(fixture.health(fixture.a), 200)
        fixture.press(0, 13)
        self.assertEqual(piece.health, 332)
        self.assertEqual(fixture.health(fixture.a), 200)


class NpcBlossomTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        npc_fixture.SombraActualNpcTests.setUpClass.__func__(cls)

    @classmethod
    def restore_graphs(cls, path):
        npc_fixture.SombraActualNpcTests.restore_graphs(path)

    def test_actual_pulses_on_all_five_npc_bodies(self):
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                fixture, bot = npc_fixture.SombraActualNpcTests.make(self, key, distance=3)
                prepare(fixture)
                bot.position = (3, 0, 1)
                fight = fixture.run.match.combat
                before = fight.vitals[bot.entity].total
                SombraBlossomTests().launch(fixture)
                self.assertLess(fight.vitals[bot.entity].total, before)
                self.assertEqual(fixture.health(fixture.a), 166)
                fixture.press(0, 14)
                self.assertLess(fight.vitals[bot.entity].total, before - 34)
                self.assertEqual(fixture.health(fixture.a), 132)

    def test_actual_hellfire_earns_and_spends_2100_on_unmodified_heavy(self):
        fixture, bot = npc_fixture.SombraActualNpcTests.make(self, "5", distance=3)
        prepare(fixture)
        place(fixture.a, (3, 0, 1))
        bot.position = (0, 0, 1)
        fight = fixture.run.match.combat
        owner = fixture.b
        heavy = fight.vitals[bot.entity]
        self.assertEqual(heavy.total, 4000)
        owner.body_script.set_ult_charge(0)
        facing = aim(fight._eye(owner), (0, 1.15, 1))
        weapon = next(i for i in owner.body_script.component.instances.values()
                      if i.graph.index == reaper_blossom.SHOTGUN)
        for _ in range(1900):
            fixture.run.step({owner: (*facing, FIRE)})
            if charge(owner) >= 2100:
                break
        self.assertEqual(charge(owner), 2100)
        self.assertGreater(weapon.state(7).counter, 0)
        before = heavy.total
        self.assertLessEqual(before, 1900)
        fixture.press(0)
        fixture.run.step({owner: (*facing, 32)})
        self.assertEqual(charge(owner), 0)
        self.assertTrue(reaper_blossom.wait_state(owner))
        self.assertLess(heavy.total, before)


class OwnedMechBlossomTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        matrix_fixture.SombraMatrixTests.setUpClass()

    def test_active_owned_matrix_does_not_intercept_radial_damage(self):
        from ow174.game import dva

        fixture, _ = matrix_fixture.SombraMatrixTests().pilot_arena()
        match = fixture.run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        fixture.a.team, fixture.b.team = 0, 1
        pilot = fixture.b
        pilot.body_script.set_ult_charge(pilot.body_script.component.entity_var(ULT_COST).value())
        fixture.run.step({pilot: (0, 0, 32)})
        fixture.press(0, 120)
        mech = dva.controlled_mech(pilot)
        self.assertIsNotNone(mech)
        x, y, z = mech.position
        owner = grenade_fixture.SombraGrenadeTests().join_ally(
            fixture, reaper_blossom.HERO, (x, y, z - 3),
        )
        owner.body_script.ult_seconds = 0
        owner.body_script.set_ult_charge(2100)
        fixture.press(0, 50)
        for _ in range(5):
            fixture.run.step({pilot: (32768, 0, 2)})
        self.assertIsNotNone(shot_guards.active_guard(mech, fight.tick * 16))
        fixture.run.step({pilot: (32768, 0, 2), owner: (0, 0, 32)})
        self.assertEqual(charge(owner), 0)
        self.assertEqual(fight.vitals[mech.entity].total, 571)


class FixedMeshBlossomTests(barrier_fixture.BarrierScene, unittest.TestCase):
    def test_actual_orisa_mesh_takes_one_pulse_for_two_bodies_and_when_standalone(self):
        fixture = SombraBlossomTests().arena(distance=6)
        match = fixture.run.match
        match.game_map = replace(match.game_map, free_for_all=False)
        fixture.a.team, fixture.b.team = 0, 1
        orisa = grenade_fixture.SombraGrenadeTests().join_ally(
            fixture, 0x02E000000000013E, (0, 0, -2),
        )
        fixture.press(0, 45)
        self.throw(fixture, orisa)
        shield = self.land(fixture, orisa)
        before = fixture.health(orisa)
        SombraBlossomTests().launch(fixture)
        self.assertEqual(shield.vitals.total, 566)
        self.assertEqual(fixture.health(fixture.a), 200)
        self.assertEqual(fixture.health(orisa), before)
        place(fixture.a, (0, 0, -8))
        place(orisa, (0, 0, -6))
        fixture.press(0, 13)
        self.assertEqual(shield.vitals.total, 532)
