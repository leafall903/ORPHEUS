"""Real Whole Hog casts against Sombra and the Retribution roster."""

import math
import unittest

import test_game_sombra_ana_rifle as rifle_fixture
import test_game_sombra_grenade as grenade_fixture
import test_game_sombra_npc_projected as projected_fixture
import test_game_sombra_roadhog_hook as hook_fixture
from test_game_combat import aim

from ow174.game import combat, hook, reinhardt_barrier, shot_guards, translocators
from ow174.game.script.driver import ULT_CHARGE, ULT_COST
from ow174.game.server_snapshot import SCHEMA, SCOPE, _Graph, snapshot_digest
from ow174.game.world import HEALTH_POOL


class SombraWholeHogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rifle_fixture.SombraAnaRifleTests.setUpClass.__func__(cls)

    @classmethod
    def restore_graphs(cls, path):
        rifle_fixture.SombraAnaRifleTests.restore_graphs(path)

    @staticmethod
    def arena():
        return hook_fixture.SombraRoadhogHookTests().arena()

    @staticmethod
    def begin(f, hog, facing):
        script = hog.body_script
        script.ult_seconds = 0
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        f.run.step({hog: (*facing, 32)})

    def contact(self, f, hog, facing, entity):
        fight = f.run.match.combat
        self.begin(f, hog, facing)
        for _ in range(75):
            f.run.step({hog: (*facing, 0)})
            if entity in fight.impulses.live:
                return fight.impulses.live[entity]
        self.fail(f"Whole Hog did not displace entity {entity}")

    def test_body_hit_and_motion_match_contiguous_and_omitted_frames(self):
        final_positions = []
        for catchup in (False, True):
            with self.subTest(catchup=catchup):
                f = self.arena()
                match = f.run.match
                fight = match.combat
                start = tuple(f.a.mover.position)
                facing = aim(fight._eye(f.b), (0, 1, 0))
                motion = self.contact(f, f.b, facing, f.a.body)
                self.assertEqual(f.health(f.a), 193)
                self.assertAlmostEqual(math.hypot(motion.velocity[0], motion.velocity[2]), 10, places=5)
                self.assertAlmostEqual(motion.velocity[1], 1.8)
                self.assertEqual(f.a.movement().velocity, motion.velocity)
                self.assertLess(f.b.body_script.component.entity_var(ULT_CHARGE).value(), 10)
                f.press(0)
                self.assertGreater(f.a.mover.position[1], start[1])
                self.assertLess(f.a.mover.position[2], start[2])
                if catchup:
                    f.run.frame += 90
                    match.update(f.run.frame * combat.TICK, f.run.frame)
                else:
                    f.press(0, 90)
                self.assertNotIn(f.a.body, fight.impulses.live)
                self.assertLess(f.a.mover.position[2], start[2] - 0.3)
                self.assertAlmostEqual(f.a.mover.position[1], start[1], delta=0.03)
                final_positions.append(tuple(f.a.mover.position))
        if len(final_positions) == 2:
            self.assertLess(math.dist(*final_positions), 0.03)

    def test_whole_hog_damage_and_displacement_reach_all_five_npcs(self):
        fixture = projected_fixture.SombraNpcProjectedTests()
        fixture.catalog = self.catalog
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                f, bot = fixture.arena(key)
                hog, facing = fixture.add_attacker(f, bot, hook.HERO)
                fight = f.run.match.combat
                vitals = fight.vitals[bot.entity]
                before = vitals.total
                self.contact(f, hog, facing, bot.entity)
                if key == "5":
                    hits = [message["+0x78"] for message in hog.client.messages(combat.HIT)]
                    critical_count = sum(bool(hit["+0x8"] & combat.CRITICAL) for hit in hits)
                    self.assertEqual((len(hits), critical_count), (4, 2))
                    self.assertEqual(before - vitals.total, 7 * (len(hits) + critical_count))
                else:
                    self.assertIn(before - vitals.total, (3.5, 7))
                start = bot.position
                f.press(0, 12)
                self.assertLess(bot.position[2], start[2] - 0.3)
                f.press(0, 78)
                self.assertNotIn(bot.entity, fight.impulses.live)

    def test_projected_barrier_takes_damage_without_transmitting_force(self):
        f = self.arena()
        fight = f.run.match.combat
        zarya = grenade_fixture.SombraGrenadeTests().join_ally(f, reinhardt_barrier.ZARYA, (5, 0, 0))
        f.run.step({zarya: (*aim(fight._eye(zarya), (0, 1, 0)), 16)})
        shield, = fight.barriers.values()
        self.assertIs(shield.protected, f.a)
        before = shield.vitals.total
        facing = aim(fight._eye(f.b), (0, 1, 0))
        self.begin(f, f.b, facing)
        for _ in range(65):
            f.run.step({f.b: (*facing, 0)})
        self.assertLess(shield.vitals.total, before)
        self.assertEqual(f.health(f.a), 200)
        self.assertNotIn(f.a.body, fight.impulses.live)

    def test_later_pellets_cross_a_broken_bubble_in_the_same_volley(self):
        f = hook_fixture.SombraRoadhogHookTests().arena(distance=1.5)
        fight = f.run.match.combat
        zarya = grenade_fixture.SombraGrenadeTests().join_ally(f, reinhardt_barrier.ZARYA, (5, 0, 0))
        f.run.step({zarya: (*aim(fight._eye(zarya), (0, 1, 0)), 16)})
        shield, = fight.barriers.values()
        shield.vitals.now[HEALTH_POOL] = 1
        facing = aim(fight._eye(f.b), (0, 1, 0))
        motion = self.contact(f, f.b, facing, f.a.body)
        self.assertNotIn(shield.entity, fight.barriers)
        self.assertEqual(shield.vitals.total, 0)
        self.assertGreater(200 - f.health(f.a), 7)
        self.assertAlmostEqual((200 - f.health(f.a)) % 7, 0)
        self.assertAlmostEqual(motion.velocity[1], 1.8)
        volleys = [state for instance in f.b.body_script.component.instances.values()
                   for state in instance.states.values()
                   if (instance.graph.index, state.node.pos) == (0x01C6, 33) and state.active]
        volley, = volleys
        self.assertEqual(volley.reported, 1)

    def test_actual_head_and_torso_pellets_reconcile_with_damage_and_hit_flags(self):
        for head in (False, True):
            with self.subTest(head=head):
                f = hook_fixture.SombraRoadhogHookTests().arena(distance=1.8)
                fight = f.run.match.combat
                facing = aim(fight._eye(f.b), (0, 1.55 if head else 0.35, 0))
                self.contact(f, f.b, facing, f.a.body)
                hits = [message["+0x78"] for message in f.b.client.messages(combat.HIT)]
                self.assertTrue(hits)
                self.assertTrue(all(hit["+0x0"]["+0x0"] == f.a.body for hit in hits))
                critical_count = sum(bool(hit["+0x8"] & combat.CRITICAL) for hit in hits)
                self.assertEqual(critical_count > 0, head)
                self.assertEqual(200 - f.health(f.a), 7 * (len(hits) + critical_count))

    def test_ally_and_authored_range_boundary(self):
        for distance, ally in ((8, True), (99, False), (103, False)):
            with self.subTest(distance=distance, ally=ally):
                f = hook_fixture.SombraRoadhogHookTests().arena(ally=ally)
                fight = f.run.match.combat
                f.floor = [(-10, 0, -10, -10, 0, 120, 10, 0, 120),
                           (-10, 0, -10, 10, 0, 120, 10, 0, -10)]
                f.b.mover.position[:] = (0, 0, distance)
                f.geometry()
                f.press(0, 3)
                # The distant cases use the same calibrated first pellet on either side of reach.
                facing = aim(fight._eye(f.b), (0, 1, 0)) if ally else (-32441, -198)
                if distance == 99:
                    motion = self.contact(f, f.b, facing, f.a.body)
                    self.assertLess(f.health(f.a), 200)
                    self.assertLess(math.hypot(motion.velocity[0], motion.velocity[2]), 2)
                else:
                    self.begin(f, f.b, facing)
                    for _ in range(75):
                        f.run.step({f.b: (*facing, 0)})
                    self.assertEqual(f.health(f.a), 200)
                    self.assertNotIn(f.a.body, fight.impulses.live)
                self.assertLess(f.b.body_script.component.entity_var(ULT_CHARGE).value(), 10)

    def test_static_cover_and_rear_wall_resolve_before_and_after_contact(self):
        for covered in (False, True):
            with self.subTest(covered=covered):
                f = self.arena()
                fight = f.run.match.combat
                z = 0.75 if covered else -0.75
                f.geometry((-3, 0, z, 3, 0, z, 3, 4, z), (-3, 0, z, 3, 4, z, -3, 4, z))
                facing = aim(fight._eye(f.b), (0, 1, 0))
                if covered:
                    self.begin(f, f.b, facing)
                    for _ in range(65):
                        f.run.step({f.b: (*facing, 0)})
                    self.assertEqual(f.health(f.a), 200)
                else:
                    self.contact(f, f.b, facing, f.a.body)
                    f.press(0, 65)
                    self.assertEqual(f.health(f.a), 193)
                    self.assertGreater(f.a.mover.position[2], z)
                    self.assertLess(f.a.mover.position[2], -0.1)
                self.assertNotIn(f.a.body, fight.impulses.live)

    def test_genji_returns_force_and_sigma_absorbs_it(self):
        for hero, button in ((shot_guards.GENJI, 16), (shot_guards.SIGMA, 8)):
            with self.subTest(hero=hero):
                f = self.arena()
                fight = f.run.match.combat
                guard_owner = grenade_fixture.SombraGrenadeTests().join_ally(f, hero, (0, 0, 3))
                facing = aim(fight._eye(f.b), (0, 1, 0))
                guard_facing = aim(fight._eye(guard_owner), (0, 1, 8))
                self.begin(f, f.b, facing)
                for frame in range(60):
                    f.run.step({f.b: (*facing, 0), guard_owner: (*guard_facing, button if frame == 8 else 0)})
                    guard = shot_guards.active_guard(guard_owner, fight.tick * combat.TICK * 1000)
                    absorbed = guard.instance.vars.get(21312) if guard is not None else None
                    if f.b.body in fight.impulses.live or (absorbed is not None and absorbed.value() > 0):
                        break
                self.assertEqual(f.health(f.a), 200)
                self.assertNotIn(f.a.body, fight.impulses.live)
                self.assertNotIn(guard_owner.body, fight.impulses.live)
                if hero == shot_guards.GENJI:
                    self.assertLess(f.health(f.b), 600)
                    self.assertGreater(fight.impulses.live[f.b.body].velocity[2], 0)
                    f.press(0, 6)
                    self.assertGreater(f.b.mover.position[2], 8.3)
                else:
                    self.assertIsNotNone(guard)
                    self.assertGreater(guard.instance.vars[21312].value(), 0)
                    self.assertEqual(f.health(f.b), 600)
                    self.assertNotIn(f.b.body, fight.impulses.live)

    def test_paid_whole_hog_stops_on_actual_emp(self):
        f = self.arena()
        fight = f.run.match.combat
        facing = aim(fight._eye(f.b), (0, 1, 0))
        self.contact(f, f.b, facing, f.a.body)
        f.a.body_script.set_ult_charge(1400)
        f.run.step({f.a: (0, 0, 32), f.b: (*facing, 0)})
        for _ in range(40):
            if f.b.body in fight.hacks:
                break
            f.run.step({f.b: (*facing, 0)})
        self.assertIn(f.b.body, fight.hacks)
        stopped = f.health(f.a)
        for _ in range(80):
            f.run.step({f.b: (*facing, 0)})
        self.assertEqual(f.health(f.a), stopped)
        self.assertLess(f.b.body_script.component.entity_var(ULT_CHARGE).value(), 10)

    def test_ultimate_refuses_undercharge_and_expires_after_paid_cast(self):
        f = self.arena()
        script = f.b.body_script
        script.ult_seconds = 0
        ultimate, = script.ultimates
        charge = script.component.entity_var(ULT_CHARGE)
        cost = script.component.entity_var(ULT_COST).value()
        script.set_ult_charge(cost - 1)
        f.run.step({f.b: (0, 0, 32)})
        self.assertFalse(ultimate.flags & ultimate.RUNNING)
        self.assertEqual(charge.value(), cost - 1)
        f.press(0)
        self.begin(f, f.b, (0, 0))
        f.press(0, 60)
        self.assertTrue(ultimate.flags & ultimate.RUNNING)
        self.assertEqual(charge.value(), 0)
        f.press(0, 390)
        self.assertFalse(ultimate.flags & ultimate.RUNNING)
        self.assertEqual(charge.value(), 0)
        f.run.step({f.b: (0, 0, 32)})
        self.assertFalse(ultimate.flags & ultimate.RUNNING)
        self.assertEqual(f.health(f.a), 200)

    def test_actual_hit_exits_camo(self):
        f = self.arena()
        fight = f.run.match.combat
        f.press(8)
        f.press(0, 60)
        camo = f.camo().state(13)
        self.assertTrue(camo.flags & camo.RUNNING)
        self.contact(f, f.b, aim(fight._eye(f.b), (0, 1, 0)), f.a.body)
        f.press(0, 65)
        self.assertEqual(f.health(f.a), 193)
        self.assertFalse(camo.flags & camo.RUNNING)
        self.assertGreater(camo.remaining(), 0)

    def test_actual_recall_clears_displacement(self):
        if not translocators.DATA_PATH.is_file():
            self.skipTest("requires the extracted Translocator entity definition")
        f = self.arena()
        fight = f.run.match.combat
        f.floor = [(-100, 0, -10, -100, 0, 100, 100, 0, 100),
                   (-100, 0, -10, 100, 0, 100, 100, 0, -10)]
        f.geometry()
        for _ in range(20):
            f.run.step({f.a: (16384, 0, 16)})
        f.press(0, 180)
        beacon, = fight.translocators.entries.values()
        self.assertTrue(beacon.settled)
        self.contact(f, f.b, aim(fight._eye(f.b), (0, 1, 0)), f.a.body)
        f.press(0, 2)
        self.assertIn(f.a.body, fight.impulses.live)
        f.press(16)
        f.press(0, 20)
        self.assertNotIn(beacon.entity, fight.translocators.entries)
        self.assertNotIn(f.a.body, fight.impulses.live)
        self.assertAlmostEqual(f.a.mover.position[0], beacon.position[0], delta=0.15)
        self.assertAlmostEqual(f.a.mover.position[2], beacon.position[2], delta=0.15)

    def test_lethal_hit_never_moves_body_and_replacement_retires_motion(self):
        for lethal in (False, True):
            with self.subTest(lethal=lethal):
                f = self.arena()
                match = f.run.match
                fight = match.combat
                body = f.a.body
                facing = aim(fight._eye(f.b), (0, 1, 0))
                if lethal:
                    target = rifle_fixture.SombraAnaRifleTests.target(fight, f.a)
                    vitals = fight.vitals_of(target)
                    vitals.now[HEALTH_POOL] = 7
                    self.begin(f, f.b, facing)
                    for _ in range(65):
                        f.run.step({f.b: (*facing, 0)})
                        if vitals.dead:
                            break
                    self.assertTrue(vitals.dead)
                    self.assertEqual(vitals.killer, f.b.body)
                    self.assertEqual(f.a.mover.position, [0, 0, 0])
                else:
                    self.contact(f, f.b, facing, body)
                    self.assertTrue(match.switch_hero(f.a, f.a.hero))
                    self.assertNotEqual(f.a.body, body)
                self.assertNotIn(body, fight.impulses.live)

    def test_snapshot_tracks_pending_displacement(self):
        f = self.arena()
        fight = f.run.match.combat
        motion = self.contact(f, f.b, aim(fight._eye(f.b), (0, 1, 0)), f.a.body)

        def digest():
            stored = _Graph({"fight": fight, "owner": f.a}, None)
            roots = {"impulses": stored.value(fight.impulses)}
            return snapshot_digest({
                "schema": SCHEMA, "scope": SCOPE, "restorable": False,
                "roots": roots, "nodes": stored.finish(), "assets": [],
            })

        first = digest()
        self.assertEqual(digest(), first)
        motion.velocity = (0.0, 0.0, 0.0)
        self.assertNotEqual(digest(), first)
