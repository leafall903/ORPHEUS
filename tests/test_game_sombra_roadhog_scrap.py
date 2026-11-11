"""Live Scrap Gun launch, airburst, and defense checks in the Sombra lane."""

import unittest
from dataclasses import replace

import test_game_sombra_ana_rifle as rifle_fixture
import test_game_sombra_grenade as grenade_fixture
import test_game_sombra_matrix as matrix_fixture
import test_game_sombra_npc_projected as projected_fixture
import test_game_sombra_roadhog_hook as hook_fixture
from test_game_combat import aim

from ow174.game import combat, dva, reinhardt_barrier, roadhog, shot_guards
from ow174.game.server_snapshot import SCHEMA, SCOPE, _Graph, snapshot_digest
from ow174.game.world import HEALTH_POOL


class SombraRoadhogScrapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rifle_fixture.SombraAnaRifleTests.setUpClass.__func__(cls)

    @classmethod
    def restore_graphs(cls, path):
        rifle_fixture.SombraAnaRifleTests.restore_graphs(path)

    @staticmethod
    def ammo(hog):
        weapon = next(instance for instance in hog.body_script.component.instances.values()
                      if instance.graph.index == roadhog.SCRAP_GUN)
        return weapon.vars[53].value()

    @staticmethod
    def hits(hog):
        return [message["+0x78"] for message in hog.client.messages(combat.HIT)]

    def shoot(self, f, hog, facing, button, held=None):
        fight = f.run.match.combat
        commands = dict(held or {})
        before = self.ammo(hog)
        for frame in range(35):
            commands[hog] = (*facing, button if frame == 0 else 0)
            f.run.step(commands)
            if fight.rockets:
                self.assertEqual(self.ammo(hog), before - 1)
                return tuple(fight.rockets)
        self.fail("Scrap Gun did not launch")

    def test_primary_slug_and_fragments_hit_sombra_and_spend_one_round(self):
        for button, distance, count, damage in ((1, 2, 25, 165), (2, 4, 1, 50), (2, 12, 25, 165)):
            with self.subTest(button=button, distance=distance):
                f = hook_fixture.SombraRoadhogHookTests().arena(distance=distance)
                fight = f.run.match.combat
                hog = f.b
                facing = aim(fight._eye(hog), (0, 0.85, 0))
                ammo = self.ammo(hog)
                launched = self.shoot(f, hog, facing, button)
                self.assertEqual(len(launched), 25 if button == 1 else 1)
                if button == 2:
                    self.assertEqual(launched[0].definition, roadhog.SLUG)
                    self.assertEqual(len(launched[0].burst.directions), 25)
                f.press(0, 90)
                self.assertEqual(self.ammo(hog), ammo - 1)
                self.assertEqual(len(self.hits(hog)), count)
                self.assertAlmostEqual(200 - f.health(f.a), damage, places=4)
                self.assertFalse(fight.rockets)

    def test_all_five_actual_npcs_take_primary_slug_and_fragment_damage(self):
        fixture = projected_fixture.SombraNpcProjectedTests()
        fixture.catalog = self.catalog
        for key, definition in self.catalog.items():
            for button, distance in ((1, 2), (2, 4), (2, 12)):
                with self.subTest(npc=definition.name, button=button, distance=distance):
                    f, bot = fixture.arena(key)
                    hog, _ = fixture.add_attacker(f, bot, roadhog.HERO)
                    fight = f.run.match.combat
                    x, y, z = bot.position
                    hog.mover.position[:] = (x, y, z + distance)
                    hog.mover.body = None
                    f.press(0, 3)
                    before = fight.vitals[bot.entity].total
                    facing = aim(fight._eye(hog), (x, y + 0.85, z))
                    self.shoot(f, hog, facing, button)
                    f.press(0, 34)
                    hits = self.hits(hog)
                    self.assertTrue(all(hit["+0x0"]["+0x0"] == bot.entity for hit in hits))
                    loss = before - fight.vitals[bot.entity].total
                    if distance == 4:
                        self.assertEqual(len(hits), 1)
                        self.assertIn(loss, (45, 50))
                    else:
                        self.assertGreater(len(hits), 1)
                        self.assertGreater(loss, 6.6)

    def test_wall_before_expiry_stops_slug_and_wall_after_expiry_stops_pellets(self):
        for wall in (0.75, 4.0):
            with self.subTest(wall=wall):
                f = hook_fixture.SombraRoadhogHookTests().arena(distance=12)
                fight = f.run.match.combat
                f.geometry((-5, 0, wall, 5, 0, wall, 5, 4, wall),
                           (-5, 0, wall, 5, 4, wall, -5, 4, wall))
                facing = aim(fight._eye(f.b), (0, 0.85, 0))
                self.shoot(f, f.b, facing, 2)
                fragments = False
                for _ in range(30):
                    f.press(0)
                    fragments |= any(rocket.definition == roadhog.PELLET for rocket in fight.rockets)
                self.assertEqual(fragments, wall < 3)
                self.assertEqual(f.health(f.a), 200)
                self.assertFalse(self.hits(f.b))
                self.assertFalse(fight.rockets)

    def test_airburst_matches_contiguous_world_and_omitted_ticks(self):
        results = []
        for skip in (False, True):
            f = hook_fixture.SombraRoadhogHookTests().arena(distance=12)
            match = f.run.match
            fight = match.combat
            facing = aim(fight._eye(f.b), (0, 0.85, 0))
            slug, = self.shoot(f, f.b, facing, 2)
            f.press(0)
            self.assertGreater(slug.flown, 0)
            if skip:
                f.run.frame += 140
                match.update(f.run.frame * combat.TICK, f.run.frame)
                match.update(f.run.frame * combat.TICK, f.run.frame)
            else:
                f.press(0, 140)
            results.append((f.health(f.a), self.hits(f.b)))
            self.assertFalse(fight.rockets)
        self.assertEqual(*results)
        self.assertAlmostEqual(results[0][0], 35, places=4)

    def test_primary_pellet_falloff_and_hard_reach_on_the_same_sampled_ray(self):
        for distance in (23, 99, 103):
            with self.subTest(distance=distance):
                f = hook_fixture.SombraRoadhogHookTests().arena(distance=8)
                match = f.run.match
                fight = match.combat
                f.floor = [(-12, 0, -120, -12, 0, 120, 12, 0, 120),
                           (-12, 0, -120, 12, 0, 120, 12, 0, -120)]
                f.geometry()
                origin = fight._eye(f.b)
                pellets = self.shoot(f, f.b, aim(origin, (0, origin[1], 0)), 1)
                first = pellets[0]
                x, y, z = (p + distance * v for p, v in zip(first.position, first.direction, strict=True))
                # Align the stationary body with the emitted centre pellet. This isolates range from spread.
                f.a.mover.position[:] = (x, y - 0.85, z)
                f.a.mover.body = None
                for elapsed in (1, 90):
                    f.run.frame += elapsed
                    match.update(f.run.frame * combat.TICK, f.run.frame)
                damage = 200 - f.health(f.a)
                if distance == 23:
                    hits = self.hits(f.b)
                    self.assertTrue(hits)
                    weighted = len(hits) + sum(bool(hit["+0x8"] & combat.CRITICAL) for hit in hits)
                    self.assertGreater(damage / weighted, 4)
                    self.assertLess(damage / weighted, 4.5)
                elif distance == 99:
                    self.assertEqual(len(self.hits(f.b)), 1)
                    self.assertAlmostEqual(damage, 6.6 * 0.3, places=4)
                else:
                    self.assertFalse(self.hits(f.b))
                    self.assertEqual(damage, 0)
                self.assertFalse(fight.rockets)

    def test_projected_shield_consumes_each_launch_form_without_body_damage(self):
        for button, distance in ((1, 2), (2, 4), (2, 12)):
            with self.subTest(button=button, distance=distance):
                f = hook_fixture.SombraRoadhogHookTests().arena(distance=distance)
                fight = f.run.match.combat
                zarya = grenade_fixture.SombraGrenadeTests().join_ally(f, reinhardt_barrier.ZARYA, (5, 0, 0))
                f.run.step({zarya: (*aim(fight._eye(zarya), (0, 1, 0)), 16)})
                shield, = fight.barriers.values()
                self.shoot(f, f.b, aim(fight._eye(f.b), (0, 0.85, 0)), button)
                f.press(0, 35)
                self.assertLess(shield.vitals.total, 200)
                self.assertEqual(f.health(f.a), 200)
                self.assertFalse(self.hits(f.b))

    def test_projected_shield_breaks_to_earlier_pellets_then_later_pellets_hit(self):
        f = hook_fixture.SombraRoadhogHookTests().arena(distance=2)
        fight = f.run.match.combat
        zarya = grenade_fixture.SombraGrenadeTests().join_ally(f, reinhardt_barrier.ZARYA, (5, 0, 0))
        f.run.step({zarya: (*aim(fight._eye(zarya), (0, 1, 0)), 16)})
        shield, = fight.barriers.values()
        shield.vitals.now[HEALTH_POOL] = 10
        self.shoot(f, f.b, aim(fight._eye(f.b), (0, 0.85, 0)), 1)
        f.press(0, 20)
        self.assertNotIn(shield.entity, fight.barriers)
        self.assertEqual(shield.vitals.total, 0)
        self.assertEqual(len(self.hits(f.b)), 23)
        self.assertAlmostEqual(200 - f.health(f.a), 23 * 6.6, places=4)

    def test_ally_and_aim_miss_spend_one_round_without_damage(self):
        for ally in (False, True):
            with self.subTest(ally=ally):
                f = hook_fixture.SombraRoadhogHookTests().arena(distance=12, ally=ally)
                fight = f.run.match.combat
                facing = aim(fight._eye(f.b), (0, 0.85, 0)) if ally else (0, 0)
                ammo = self.ammo(f.b)
                self.shoot(f, f.b, facing, 2)
                f.press(0, 105)
                self.assertFalse(fight.rockets)
                self.assertFalse(self.hits(f.b))
                self.assertEqual(f.health(f.a), 200)
                self.assertEqual(self.ammo(f.b), ammo - 1)

    def test_release_and_reload_gate_then_accept_fresh_primary(self):
        f = hook_fixture.SombraRoadhogHookTests().arena(distance=4)
        fight = f.run.match.combat
        ammo = self.ammo(f.b)
        self.shoot(f, f.b, (0, 0), 1)
        f.press(0, 80)
        self.assertEqual(self.ammo(f.b), ammo - 1)
        f.run.step({f.b: (0, 0, 0x400)})
        for _ in range(5):
            f.run.step({f.b: (0, 0, 1)})
        self.assertEqual(self.ammo(f.b), ammo - 1)
        self.assertFalse(fight.rockets)
        f.press(0, 120)
        self.assertEqual(self.ammo(f.b), ammo)
        facing = aim(fight._eye(f.b), (0, 0.85, 0))
        self.shoot(f, f.b, facing, 1)
        f.press(0, 30)
        self.assertGreater(len(self.hits(f.b)), 1)

    def test_incoming_slug_interrupts_channel_and_hacked_roadhog_can_fire(self):
        f = hook_fixture.SombraRoadhogHookTests().arena(distance=5)
        fight = f.run.match.combat
        f.press(2, 12)
        self.assertTrue(f.weapon().vars[375].value())
        facing = aim(fight._eye(f.b), (0, 0.85, 0))
        sombra_facing = aim(fight._eye(f.a), (0, 1, 5))
        self.shoot(f, f.b, facing, 2, held={f.a: (*sombra_facing, 2)})
        for _ in range(12):
            f.run.step({f.a: (*sombra_facing, 2)})
            if f.health(f.a) < 200:
                break
        self.assertEqual(f.health(f.a), 150)
        f.press(0, 55)
        self.assertNotIn(f.b.body, fight.hacks)
        self.assertGreater(f.weapon().state(53).remaining(), 0)
        f.press(0, 100)
        for _ in range(80):
            f.press(2)
            if f.b.body in fight.hacks:
                break
        self.assertIn(f.b.body, fight.hacks)
        self.shoot(f, f.b, facing, 2)
        f.press(0, 12)
        self.assertIn(f.b.body, fight.hacks)
        self.assertEqual(f.health(f.a), 100)

    def test_lethal_slug_during_paid_emp_prevents_the_pulse(self):
        f = hook_fixture.SombraRoadhogHookTests().arena(distance=5)
        fight = f.run.match.combat
        f.health(f.a)
        vitals = fight.vitals[f.a.body]
        vitals.now[HEALTH_POOL] = 50
        f.a.body_script.set_ult_charge(1400)
        f.press(32)
        self.assertEqual(f.a.body_script.component.entity_var(342).value(), 0)
        self.shoot(f, f.b, aim(fight._eye(f.b), (0, 0.85, 0)), 2)
        f.press(0, 40)
        self.assertTrue(vitals.dead)
        self.assertEqual(vitals.killer, f.b.body)
        self.assertNotIn(f.b.body, fight.hacks)
        self.assertFalse(f.hack_applications())

    def test_genji_and_sigma_intercept_live_slug_and_fragments(self):
        for hero, button in ((shot_guards.GENJI, 16), (shot_guards.SIGMA, 8)):
            for fragment in (False, True):
                with self.subTest(hero=hero, fragment=fragment):
                    f = hook_fixture.SombraRoadhogHookTests().arena(distance=8)
                    fight = f.run.match.combat
                    f.a.mover.position[0] = 6
                    f.a.mover.body = None
                    z = -8 if fragment else 4
                    defender = grenade_fixture.SombraGrenadeTests().join_ally(f, hero, (0, 0, z))
                    facing = aim(fight._eye(f.b), (0, 1, z))
                    guard_facing = aim(fight._eye(defender), (0, 1, 8))
                    f.run.step({defender: (*guard_facing, button)})
                    held = {defender: (*guard_facing, 0)}
                    self.shoot(f, f.b, facing, 2, held=held)
                    fragments = False
                    for _ in range(45):
                        f.run.step(held)
                        fragments |= any(rocket.shooter is f.b and rocket.definition == roadhog.PELLET
                                         for rocket in fight.rockets)
                    self.assertEqual(fragments, fragment)
                    self.assertEqual(f.health(f.a), 200)
                    self.assertFalse(self.hits(f.b))
                    if hero == shot_guards.GENJI:
                        self.assertLess(f.health(f.b), 600)
                        self.assertTrue(all(hit["+0x0"]["+0x0"] == f.b.body for hit in self.hits(defender)))
                    else:
                        self.assertEqual(f.health(f.b), 600)
                        guard = shot_guards.active_guard(defender, fight.tick * combat.TICK * 1000)
                        absorbed = guard.instance.vars[21312].value()
                        if fragment:
                            self.assertGreater(absorbed, 0)
                            self.assertNotEqual(absorbed, 50)
                        else:
                            self.assertEqual(absorbed, 50)

    def test_active_airburst_is_snapshot_state_and_body_replacement_retires_it(self):
        f = hook_fixture.SombraRoadhogHookTests().arena(distance=12)
        fight = f.run.match.combat
        slug, = self.shoot(f, f.b, aim(fight._eye(f.b), (0, 0.85, 0)), 2)

        def digest():
            stored = _Graph({"owner": f.b}, None)
            roots = {"burst": stored.value(slug.burst)}
            return snapshot_digest({
                "schema": SCHEMA, "scope": SCOPE, "restorable": False,
                "roots": roots, "nodes": stored.finish(), "assets": [],
            })

        first = digest()
        self.assertEqual(digest(), first)
        slug.burst = replace(slug.burst, lifetime=slug.burst.lifetime / 2)
        self.assertNotEqual(digest(), first)
        self.assertTrue(f.run.match.switch_hero(f.b, f.b.hero))
        f.press(0, 35)
        self.assertFalse(fight.rockets)
        self.assertEqual(f.health(f.a), 200)


class SombraRoadhogScrapMatrixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        matrix_fixture.SombraMatrixTests.setUpClass()

    def test_owned_mech_matrix_consumes_primary_slug_and_fragments(self):
        for button, distance in ((1, 2), (2, 4), (2, 22)):
            with self.subTest(button=button, distance=distance):
                f, _ = matrix_fixture.SombraMatrixTests().pilot_arena()
                match = f.run.match
                fight = match.combat
                match.game_map = replace(match.game_map, free_for_all=False)
                f.a.team, f.b.team = 0, 1
                f.floor = [(-12, 0, -40, -12, 0, 40, 12, 0, 40),
                           (-12, 0, -40, 12, 0, 40, 12, 0, -40)]
                f.geometry()
                pilot = f.b.body_script
                pilot.set_ult_charge(pilot.component.entity_var(338).value())
                f.run.step({f.b: (0, 0, 32)})
                f.press(0, 120)
                mech = dva.controlled_mech(f.b)
                self.assertIsNotNone(mech)
                x, y, z = mech.position
                hog = grenade_fixture.SombraGrenadeTests().join_ally(f, roadhog.HERO, (x, y, z - distance))
                held = {f.b: (32768, 0, 2)}
                f.run.step(held)
                self.assertIsNotNone(shot_guards.active_guard(mech, fight.tick * combat.TICK * 1000))
                facing = aim(fight._eye(hog), (x, y + 0.85, z))
                fixture = SombraRoadhogScrapTests()
                fixture.shoot(f, hog, facing, button, held=held)
                for _ in range(33):
                    f.run.step(held)
                self.assertEqual(fight.vitals[mech.entity].total, 600)
                self.assertFalse(fixture.hits(hog))
                f.press(0, 85)
                self.assertIsNone(shot_guards.active_guard(mech, fight.tick * combat.TICK * 1000))
                fixture.shoot(f, hog, facing, button)
                f.press(0, 45)
                self.assertLess(fight.vitals[mech.entity].total, 600)
