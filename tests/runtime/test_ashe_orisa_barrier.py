"""Live Orisa Protective Barrier interactions with Ashe and B.O.B."""

import math
import os
import unittest
from pathlib import Path

import test_ashe_bob as bob_fixtures
import test_ashe_bob_charge as charge_fixtures
import test_ashe_combat as fixtures
import test_ashe_controls as controls
import test_ashe_emp as emp_fixtures
from test_ashe_impulses import wall_scene
from test_game_combat import aim

from ow174.game import combat, sombra_hack
from ow174.game.content import HEROES_PATH, find_hero
from ow174.game.script import graph


@unittest.skipUnless(HEROES_PATH.is_file() and graph.GRAPHS_PATH.is_file()
                     and os.environ.get("ORPHEUS_ORISA_COLLISION"), "requires 104319 Orisa barrier mesh")
class AsheOrisaBarrierTests(unittest.TestCase):
    fight = fixtures.AsheCombatTests.fight

    def deploy(self, run, orisa, z):
        self.assertTrue(run.match.switch_hero(orisa, find_hero("orisa")))
        orisa.mover.position[:] = (0, 1, z)
        orisa.mover.body = None
        for _ in range(40):
            run.step({})
        manager = run.match.combat.orisa_barriers
        run.step({orisa: (-32768, 8192, 16)})
        flight = None
        for _ in range(65):
            flight = manager.flights.get(orisa.body, flight)
            if orisa.body in manager.actors:
                break
            run.step({orisa: (-32768, 8192, 0)})
        self.assertIsNotNone(flight)
        self.assertAlmostEqual(flight.gravity, 9.8, places=6)
        shield = manager.actors[orisa.body]
        self.assertEqual(shield.health, 600)
        self.assertTrue(shield.active())
        self.assertLess(shield.position[2], orisa.mover.position[2])
        return shield

    def test_real_viper_hip_ads_and_coach_hit_orisa_mesh_before_body(self):
        run, ashe, orisa = self.fight(distance=5)
        shield = self.deploy(run, orisa, 5)
        fight = run.match.combat
        yaw, pitch = aim(fight._eye(ashe), fight._eye(orisa))
        run.step({ashe: (yaw, pitch, 1)})
        self.assertEqual(shield.health, 560)
        for _ in range(50):
            run.step({ashe: (yaw, pitch, 2)})
        run.step({ashe: (yaw, pitch, 3)})
        self.assertEqual(shield.health, 485)
        for _ in range(40):
            run.step({})
        ashe.mover.position[:] = (0, 1, 2)
        ashe.mover.body = None
        for _ in range(10):
            run.step({})
        yaw, pitch = aim(fight._eye(ashe), fight._eye(orisa))
        run.step({ashe: (yaw, pitch, 8)})
        for _ in range(15):
            run.step({})
        self.assertEqual(shield.health, 395)
        target = next(item for item in fight._targets() if item.owner is orisa)
        self.assertEqual(fight.vitals_of(target).total, 450)
        self.assertFalse(ashe.client.messages(combat.HIT))

    def test_untouched_dynamite_fuses_in_front_and_damages_only_the_barrier(self):
        run, ashe, orisa = self.fight(distance=5)
        shield = self.deploy(run, orisa, 5)
        fight = run.match.combat
        for i in range(20):
            run.step({ashe: (0, 13000, 16 if i == 0 else 0)})
        grenade, = fight.dynamite.grenades
        while run.frame < grenade.fuse - 1:
            run.step({})
        self.assertLess(grenade.position[2], shield.position[2])
        self.assertEqual(shield.health, 600)
        run.step({})
        self.assertNotIn(grenade, fight.dynamite.grenades)
        self.assertEqual(shield.health, 550)
        self.assertEqual(fight.vitals[orisa.body].total, 450)
        self.assertNotIn((ashe.body, orisa.body), fight.dynamite.burns)

    def test_emp_breaks_mesh_while_owner_is_out_of_range_and_terrain_blocks_it(self):
        for covered in (False, True):
            with self.subTest(covered=covered):
                run, sombra, orisa = self.fight(distance=5)
                self.assertTrue(run.match.switch_hero(sombra, find_hero("sombra")))
                sombra.mover.position[:] = (0, 1, 0)
                sombra.mover.body = None
                shield = self.deploy(run, orisa, 5)
                orisa.mover.position[:] = (0, 1, 27)
                orisa.mover.body = None
                if covered:
                    run.match.collision_world = wall_scene(1.5)
                emp_fixtures.AsheEmpTests().pulse(run, sombra)
                self.assertEqual(shield.health, 600 if covered else 0)
                self.assertFalse(sombra_hack.locked(orisa))

    @unittest.skipUnless(Path(os.environ.get("ORPHEUS_MOVER_EXTENSION", ".")).is_file(),
                         "requires B.O.B. phase mover extension")
    def test_bob_gun_hits_landed_barrier_from_outside(self):
        run, _ashe, orisa, actor = bob_fixtures.BobTests().cast(distance=9)
        shield = self.deploy(run, orisa, 8)
        fight = run.match.combat
        self.assertLess(actor.bot.position[2], shield.position[2])
        before = shield.health
        health = fight.vitals[orisa.body].total
        shots = actor.shots
        for _ in range(24):
            run.step({})
        self.assertGreater(actor.shots, shots)
        self.assertEqual(shield.health, before - (actor.shots - shots) * 14)
        self.assertEqual(fight.vitals[orisa.body].total, health)

    @unittest.skipUnless(Path(os.environ.get("ORPHEUS_MOVER_EXTENSION", ".")).is_file(),
                         "requires B.O.B. phase mover extension")
    def test_bob_crosses_landed_barrier_and_uppercuts_orisa_without_spending_pool(self):
        run, ashe, orisa = self.fight(distance=8)
        shield = self.deploy(run, orisa, 8)
        fight = run.match.combat
        target = next(item for item in fight._targets() if item.owner is orisa)
        vitals = fight.vitals_of(target)
        self.assertEqual(vitals.total, 450)
        ashe.body_script.set_ult_charge(2240)
        charge = charge_fixtures.BobChargeTests()
        actor = charge.begin(run, ashe)
        x, y, z = actor.bot.position
        hit = fight.orisa_barriers.intersect(shield, (x, y + 1, z), (0, 0, 1), 6)
        self.assertIsNotNone(hit)
        self.assertGreater(hit, 0)

        charge.stop(run, actor)
        self.assertEqual(actor.stopped_reason, "enemy")
        self.assertEqual(actor.uppercut_target, orisa.body)
        self.assertGreater(actor.bot.position[2], z + hit)
        self.assertEqual(shield.health, 600)
        self.assertEqual(vitals.total, 335)
        self.assertEqual(actor.shots, 0)
        self.assertIn(orisa.body, fight.impulses.pending)

    def test_landed_barrier_expires_on_world_clock_without_owner_commands(self):
        run, _ashe, orisa = self.fight(distance=5)
        shield = self.deploy(run, orisa, 5)
        manager = run.match.combat.orisa_barriers
        start = shield.component.frame
        run.frame = start + 1248
        run.match.update(run.frame * combat.TICK, run.frame)
        self.assertTrue(shield.active())
        run.frame = start + 1251
        run.match.update(run.frame * combat.TICK, run.frame)
        self.assertFalse(shield.active())
        self.assertIn(orisa.body, manager.actors)
        run.frame = start + 1281
        run.match.update(run.frame * combat.TICK, run.frame)
        self.assertNotIn(orisa.body, manager.actors)
        self.assertEqual(shield.health, 600)

    def test_friendly_barrier_allows_real_viper_hit_on_enemy_behind_it(self):
        run, ashe, orisa, enemy = controls.AsheBarrierTests().projected_arena()
        orisa.team = ashe.team
        enemy.team = 1 - ashe.team
        ashe.mover.position[:] = (0, 1, 0)
        ashe.mover.body = None
        enemy.mover.position[:] = (0, 1, 7)
        enemy.mover.body = None
        shield = self.deploy(run, orisa, 5)
        fight = run.match.combat
        target = next(item for item in fight._targets() if item.owner is enemy)
        before = fight.vitals_of(target).total
        yaw, pitch = aim(fight._eye(ashe), fight._eye(enemy))
        run.step({ashe: (yaw, pitch, 1)})
        self.assertLess(fight.vitals_of(target).total, before)
        self.assertEqual(shield.health, 600)

    def test_quick_melee_damages_only_visible_enemy_mesh_within_reach(self):
        for case in ("visible", "both", "terrain", "range", "ally"):
            with self.subTest(case=case):
                run, ashe, orisa = self.fight(distance=5, team_game=case == "ally")
                if case == "ally":
                    orisa.team = ashe.team
                shield = self.deploy(run, orisa, 5)
                fight = run.match.combat
                z = {"range": 0, "both": 2}.get(case, 1.7)
                ashe.mover.position[:] = (0, 1, z)
                ashe.mover.body = None
                if case == "terrain":
                    run.match.collision_world = wall_scene(2.75)
                for _ in range(15):
                    run.step({})
                target = next(item for item in fight._targets() if item.owner is orisa)
                yaw, pitch = aim(fight._eye(ashe), (0, 2, 5))
                run.step({ashe: (yaw, pitch, 0x800)})
                for _ in range(32):
                    run.step({ashe: (yaw, pitch, 0)})
                self.assertEqual(shield.health, 570 if case in ("visible", "both") else 600)
                self.assertEqual(fight.vitals_of(target).total, 425 if case == "both" else 450)

    def test_airborne_barrier_crosses_active_projectile_guards_and_deploys(self):
        for name, index, node, button in (
            ("genji", 0x03A3, 18, 16),
            ("sigma", 0x1D02, 7, 8),
            ("matrix", 0x02CC, 9, 2),
        ):
            with self.subTest(guard=name):
                if name == "matrix":
                    run, guard, orisa, _matrix = controls.AsheMatrixTests().matrix_arena(distance=6)
                else:
                    run, guard, orisa = self.fight(distance=6)
                    self.assertTrue(run.match.switch_hero(guard, find_hero(name)))
                self.assertTrue(run.match.switch_hero(orisa, find_hero("orisa")))
                for player, z in ((guard, 0), (orisa, 6)):
                    player.mover.position[:] = (0, 1, z)
                    player.mover.body = None
                for _ in range(40):
                    run.step({})
                fight = run.match.combat
                manager = fight.orisa_barriers
                config = graph.graph(index).node(node).field("m_filter")
                self.assertEqual(config["m_27CF71D5"], 2)
                self.assertEqual(config["m_8B2A4DF3"]["m_7D7C86A1"], "0x0D60000000000145")
                facing = aim(fight._eye(guard), fight._eye(orisa))
                launch = aim(fight._eye(orisa), fight._eye(guard))
                crossed = False
                for frame in range(72):
                    run.step({
                        guard: (*facing, button if name == "matrix" or frame == 0 else 0),
                        orisa: (*launch, 16 if frame == 1 else 0),
                    })
                    if manager.actors:
                        break
                    flight = manager.flights.get(orisa.body)
                    if flight is None:
                        continue
                    vx, vy, vz = flight.velocity
                    delta = (vx * combat.TICK, (vy - flight.gravity * combat.TICK / 2) * combat.TICK,
                             vz * combat.TICK)
                    reach = math.hypot(*delta)
                    direction = tuple(axis / reach for axis in delta)
                    target = next(item for item in fight._targets() if item.owner is guard)
                    if fight._projectile_guard(target, flight.position, direction, reach) is not None:
                        crossed = True
                self.assertTrue(crossed, "flight never entered the live guard geometry")
                shield, = manager.actors.values()
                self.assertIs(shield.owner, orisa)
                self.assertLess(shield.position[2], guard.mover.position[2])
                self.assertEqual(shield.health, 600)
                self.assertTrue(shield.active())
