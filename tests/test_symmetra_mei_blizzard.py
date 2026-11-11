"""Actual Blizzard throw, area effects and Symmetra control on 104319."""

import os
import unittest
from dataclasses import replace
from unittest.mock import patch

import test_symmetra_barriers as barrier_tests
import test_symmetra_matrix as matrix_tests
import test_symmetra_mei as mei_tests
import test_symmetra_portals as portal_tests
from test_game_combat import aim
from test_symmetra_turrets import wall

from ow174.game import combat, cryo, deflection, matrix, projectile_tags, sigma_grasp, stats
from ow174.game.collision import World
from ow174.game.content import heroes
from ow174.game.script.driver import ULT_CHARGE, ULT_COST
from ow174.game.transcendence import active_state


class SymmetraBlizzardTests(unittest.TestCase):
    def cast(self, run, mei, *, full=True, point=(0, 1, 5)):
        fight = run.match.combat
        if full:
            mei.body_script.set_ult_charge(mei.body_script.component.entity_var(ULT_COST).value())
        angles = aim(fight._eye(mei), point)
        for frame in range(32):
            run.step({mei: (*angles, 32 if frame == 0 else 0)})

    def land(self, run):
        service = run.match.combat.blizzard
        for _ in range(100):
            if service.areas:
                break
            run.step({})
        [area] = service.areas.values()
        return area

    def test_actual_blizzard_charge_windup_flight_damage_freeze_and_expiry(self):
        fixture = mei_tests.SymmetraMeiWeaponTests()
        run, sym, mei = fixture.duel()
        fight = run.match.combat
        self.cast(run, mei, full=False)
        self.assertFalse(fight.blizzard.flights)
        self.assertFalse(fight.blizzard.areas)
        self.cast(run, mei)
        [flight] = fight.blizzard.flights
        self.assertAlmostEqual(flight.gravity, 9.8, places=6)
        self.assertEqual(mei.body_script.component.entity_var(ULT_CHARGE).value(), 0)
        area = self.land(run)
        self.assertEqual(area.ends - area.starts, 4250)
        self.assertFalse(fight.blizzard.flights)
        self.assertEqual(mei_tests.health(run, sym).total, 225)
        while run.frame * 16 <= area.starts:
            run.step({})
        self.assertLess(mei_tests.health(run, sym).total, 225)
        self.assertAlmostEqual(fight.frost(sym)[0], .5)
        observed_freeze = False
        while run.frame * 16 < area.ends:
            run.step({})
            observed_freeze |= fight.is_stunned(sym)
        self.assertTrue(observed_freeze)
        self.assertFalse(fight.blizzard.areas)
        self.assertAlmostEqual(mei_tests.health(run, sym).total, 140)
        for _ in range(85):
            run.step({})
        self.assertFalse(fight.is_stunned(sym))
        self.assertIsNone(fight.frost(sym))

    def test_live_area_respects_static_cover_and_team_on_later_ticks(self):
        run, sym, mei = mei_tests.SymmetraMeiWeaponTests().duel(distance=8)
        fight = run.match.combat
        self.cast(run, mei)
        area = self.land(run)
        at = (area.position[2] + sym.mover.position[2]) / 2
        cover = World([
            (-8, 0, at, 8, 0, at, 8, 5, at),
            (-8, 0, at, 8, 5, at, -8, 5, at),
            (-8, 0, at, 8, 5, at, 8, 0, at),
            (-8, 0, at, -8, 5, at, 8, 5, at),
        ])
        with patch.object(fight, "_collision", return_value=cover):
            for _ in range(32):
                run.step({})
        self.assertEqual(mei_tests.health(run, sym).total, 225)
        self.assertIsNone(fight.frost(sym))
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        sym.team = mei.team = 0
        for _ in range(16):
            run.step({})
        self.assertEqual(mei_tests.health(run, sym).total, 225)
        self.assertIsNone(fight.frost(sym))
        sym.team = 1 - mei.team
        run.step({})
        self.assertAlmostEqual(mei_tests.health(run, sym).total, 225 - 20 * combat.TICK)
        self.assertAlmostEqual(fight.frost(sym)[0], .5)

    def test_blizzard_and_spray_from_same_mei_keep_distinct_progress(self):
        fixture = mei_tests.SymmetraMeiWeaponTests()
        run, sym, mei = fixture.duel()
        fight = run.match.combat
        self.cast(run, mei)
        area = self.land(run)
        fixture.fire(run, sym, mei, .9, button=1)
        sources = fight.frost_sources[sym.body]
        self.assertEqual(set(sources), {(mei.body, 0), (mei.body, area.entity)})
        self.assertAlmostEqual(fight.frost(sym)[0], max(value[0] for value in sources.values()))

    def test_distant_enemy_outside_actual_area_remains_unaffected(self):
        run, sym, mei = mei_tests.SymmetraMeiWeaponTests().duel(distance=20)
        fight = run.match.combat
        self.cast(run, mei)
        area = self.land(run)
        self.assertGreater(sym.mover.position[2] - area.position[2], 11)
        while run.frame * 16 < area.ends:
            run.step({})
        self.assertEqual(mei_tests.health(run, sym).total, 225)
        self.assertIsNone(fight.frost(sym))

    def test_body_replacement_removes_live_area(self):
        run, sym, mei = mei_tests.SymmetraMeiWeaponTests().duel()
        fight = run.match.combat
        self.cast(run, mei)
        area = self.land(run)
        self.assertEqual(area.body, mei.body)
        self.assertTrue(run.match.switch_hero(mei, mei.hero))
        self.assertNotEqual(area.body, mei.body)
        self.assertFalse(fight.blizzard.areas)
        for _ in range(32):
            run.step({})
        self.assertEqual(mei_tests.health(run, sym).total, 225)
        self.assertIsNone(fight.frost(sym))

    def cast_toward_defender(self, run, mei, defender, button, *, facing=-32768):
        mei.body_script.set_ult_charge(mei.body_script.component.entity_var(ULT_COST).value())
        fight = run.match.combat
        angles = aim(fight._eye(mei), fight._eye(defender))
        for frame in range(32):
            run.step({mei: (*angles, 32 if frame == 0 else 0),
                      defender: (facing, 0, button if frame >= 27 else 0)})

    def test_actual_deflect_transfers_blizzard_and_damage_to_genji(self):
        if projectile_tags.CATALOG_SHA256 is None:
            self.skipTest("requires projectile tag catalog")
        run, sym, mei = mei_tests.SymmetraMeiWeaponTests().duel()
        fixture = barrier_tests.PhotonBarrierTests()
        genji = fixture.add_target(run, 3, (0, 1, 4))
        self.assertTrue(run.match.switch_hero(genji, heroes()[deflection.GENJI]))
        fixture.move(genji, (0, 1, 4))
        fixture.move(sym, (1, 1, -1))
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        sym.team = mei.team = 0
        genji.team = 1
        self.cast_toward_defender(run, mei, genji, 16)
        fight = run.match.combat
        [flight] = fight.blizzard.flights
        self.assertIsNotNone(active_state(genji, deflection.GENJI, 0x03A3, 10))
        lease = flight.shot
        for _ in range(100):
            if fight.blizzard.areas:
                break
            run.step({genji: (-32768, 0, 0)})
        [area] = fight.blizzard.areas.values()
        self.assertIs(flight.owner, genji)
        self.assertIs(area.owner, genji)
        self.assertEqual(area.body, genji.body)
        self.assertIsNotNone(lease.release_at)
        self.assertLess(area.position[2], 0)
        while run.frame * 16 <= area.starts + 450:
            run.step({})
        self.assertLess(mei_tests.health(run, sym).total, 225)
        self.assertIsNotNone(fight.frost(sym))
        self.assertEqual(mei_tests.health(run, genji).total, 200)
        self.assertGreater(genji.stats.value(stats.HERO_DAMAGE), 0)
        self.assertEqual(mei.stats.value(stats.HERO_DAMAGE), 0)

    def test_live_matrix_absorbs_blizzard_before_area_then_release_allows_landing(self):
        resources = matrix_tests.SymmetraMatrixTests()
        self.addCleanup(resources.doCleanups)
        resources.setUp()
        run, sym, mei = mei_tests.SymmetraMeiWeaponTests().duel()
        fixture = barrier_tests.PhotonBarrierTests()
        mech = fixture.add_target(run, 3, (0, 1, 4))
        self.assertTrue(run.match.switch_hero(mech, replace(heroes()[matrix.DVA], body=matrix.MECH)))
        fixture.move(mech, (0, 1, 4))
        fight = run.match.combat
        for _ in range(75):
            run.step({mech: (-32768, 0, 0)})
        self.cast_toward_defender(run, mei, mech, 2)
        [flight] = fight.blizzard.flights
        self.assertIsNotNone(active_state(mech, matrix.DVA, 0x02CC, 3))
        for _ in range(48):
            run.step({mech: (-32768, 0, 2)})
        self.assertFalse(fight.blizzard.flights)
        self.assertFalse(fight.blizzard.areas)
        self.assertLess(flight.flown, .2)
        self.assertIsNotNone(flight.shot.release_at)
        self.assertEqual(mei_tests.health(run, sym).total, 225)
        self.assertIsNone(fight.frost(sym))
        for _ in range(25):
            run.step({})
        self.assertIsNone(active_state(mech, matrix.DVA, 0x02CC, 3))
        self.cast_toward_defender(run, mei, mech, 0)
        area = self.land(run)
        self.assertIs(area.owner, mei)

        while run.frame * 16 <= area.starts + 160:
            run.step({})
        self.assertLess(mei_tests.health(run, sym).total, 225)
        self.assertIsNotNone(fight.frost(sym))

    def test_ally_and_rear_deflect_keep_original_blizzard_owner(self):
        if projectile_tags.CATALOG_SHA256 is None:
            self.skipTest("requires projectile tag catalog")
        for ally in (False, True):
            with self.subTest(ally=ally):
                run, sym, mei = mei_tests.SymmetraMeiWeaponTests().duel()
                fixture = barrier_tests.PhotonBarrierTests()
                genji = fixture.add_target(run, 3, (0, 1, 4))
                self.assertTrue(run.match.switch_hero(genji, heroes()[deflection.GENJI]))
                fixture.move(genji, (0, 1, 4))
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                mei.team = 0
                sym.team = 1
                genji.team = 0 if ally else 1
                facing = -32768 if ally else 0
                self.cast_toward_defender(run, mei, genji, 16, facing=facing)
                self.assertIsNotNone(active_state(genji, deflection.GENJI, 0x03A3, 10))
                service = run.match.combat.blizzard
                for _ in range(100):
                    if service.areas:
                        break
                    run.step({genji: (facing, 0, 0)})
                [area] = service.areas.values()
                self.assertIs(area.owner, mei)
                self.assertGreater(area.position[2], 4)

    def test_actual_grasp_absorbs_blizzard_without_creating_bonus_shields(self):
        if projectile_tags.CATALOG_SHA256 is None:
            self.skipTest("requires projectile tag catalog")
        run, sym, mei = mei_tests.SymmetraMeiWeaponTests().duel()
        fixture = barrier_tests.PhotonBarrierTests()
        sigma = fixture.add_target(run, 3, (0, 1, 4))
        self.assertTrue(run.match.switch_hero(sigma, heroes()[sigma_grasp.SIGMA]))
        fixture.move(sigma, (0, 1, 4))
        fight = run.match.combat
        self.cast_toward_defender(run, mei, sigma, 8)
        [flight] = fight.blizzard.flights
        state = active_state(sigma, sigma_grasp.SIGMA, sigma_grasp.GRAPH, sigma_grasp.STATE)
        self.assertIsNotNone(state)
        for _ in range(48):
            run.step({sigma: (-32768, 0, 0)})
        self.assertFalse(fight.blizzard.flights)
        self.assertFalse(fight.blizzard.areas)
        self.assertLess(flight.flown, .2)
        self.assertIsNotNone(flight.shot.release_at)
        self.assertEqual(state.instance.find_var(0, 21312).value(), 0)
        pool = mei_tests.health(run, sigma)
        for _ in range(110):
            run.step({})
        self.assertFalse(state.active)
        self.assertEqual(pool.total, 400)
        self.assertEqual(pool.bonus_shields, 0)
        self.assertEqual(mei_tests.health(run, sym).total, 225)
        self.assertIsNone(fight.frost(sym))

    def test_blizzard_does_not_change_absorbed_symmetra_orb_conversion(self):
        if projectile_tags.CATALOG_SHA256 is None:
            self.skipTest("requires projectile tag catalog")
        run, sym, mei = mei_tests.SymmetraMeiWeaponTests().duel()
        fixture = barrier_tests.PhotonBarrierTests()
        sigma = fixture.add_target(run, 3, (0, 1, 4))
        self.assertTrue(run.match.switch_hero(sigma, heroes()[sigma_grasp.SIGMA]))
        fixture.move(sigma, (0, 1, 4))
        fixture.move(sym, (.6, 1, 0))
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        sym.team = mei.team = 0
        sigma.team = 1
        fight = run.match.combat
        orb_aim = aim(fight._eye(sym), fight._eye(sigma))
        for _ in range(90):
            run.step({sym: (*orb_aim, 2)})
        mei.body_script.set_ult_charge(mei.body_script.component.entity_var(ULT_COST).value())
        blizzard_aim = aim(fight._eye(mei), fight._eye(sigma))
        with patch.object(fight.grasp, "absorb", wraps=fight.grasp.absorb) as absorb:
            for frame in range(75):
                run.step({mei: (*blizzard_aim, 32 if frame == 0 else 0),
                          sym: (*orb_aim, 2 if frame < 24 else 0),
                          sigma: (-32768, 0, 8 if frame == 23 else 0)})
        calls = absorb.call_args_list
        self.assertEqual(len(calls), 2)
        self.assertIs(calls[0].args[1], sym)
        self.assertIs(calls[1].args[1], mei)
        self.assertIsNone(calls[1].args[2])
        state = active_state(sigma, sigma_grasp.SIGMA, sigma_grasp.GRAPH, sigma_grasp.STATE)
        self.assertIsNotNone(state)
        self.assertAlmostEqual(state.instance.find_var(0, 21312).value(), 120)
        self.assertFalse(fight.blizzard.areas)
        for _ in range(120):
            if not state.active:
                break
            run.step({})
        self.assertFalse(state.active)
        self.assertAlmostEqual(mei_tests.health(run, sigma).bonus_shields, 72, places=4)

    def test_symmetra_lethal_orb_interrupts_windup_but_not_released_blizzard(self):
        for phase in ("windup", "flight", "area"):
            with self.subTest(phase=phase):
                run, sym, mei = mei_tests.SymmetraMeiWeaponTests().duel(distance=1.7)
                fight = run.match.combat
                orb_aim = aim(fight._eye(sym), (0, 1.9, 0))
                mei_pool = mei_tests.health(run, mei)
                for _ in range(180):
                    if mei_pool.total <= 100:
                        break
                    run.step({sym: (*orb_aim, 1)})
                self.assertGreater(mei_pool.total, 0)
                self.assertLessEqual(mei_pool.total, 100)
                hold = 0 if phase == "area" else 2
                if hold:
                    for _ in range(90):
                        run.step({sym: (*orb_aim, hold)})
                mei.body_script.set_ult_charge(mei.body_script.component.entity_var(ULT_COST).value())
                throw_aim = aim(fight._eye(mei), (0, 1, 5))
                run.step({sym: (*orb_aim, hold), mei: (*throw_aim, 32)})
                cast = next(i for i in mei.body_script.component.instances.values()
                            if i.graph.index == 0x064A)
                self.assertTrue(any(state.active and state.node.cls == "STUStatescriptStateWait"
                                    for state in cast.states.values()))
                if phase != "windup":
                    for _ in range(31):
                        run.step({sym: (*orb_aim, hold), mei: (*throw_aim, 0)})
                    [flight] = fight.blizzard.flights
                    self.assertIs(flight.owner, mei)
                if phase == "area":
                    for _ in range(100):
                        if fight.blizzard.areas:
                            break
                        run.step({})
                    [landed] = fight.blizzard.areas.values()
                    self.assertIs(landed.owner, mei)
                    for _ in range(90):
                        run.step({sym: (*orb_aim, 2)})
                before = mei_tests.health(run, sym).total
                if phase == "area":
                    self.assertLess(before, 225)
                run.step({sym: (*orb_aim, 0)})
                self.assertTrue(any(shot.shooter is sym for shot in fight.rockets))
                for _ in range(12):
                    if fight.is_dead(mei):
                        break
                    run.step({})
                self.assertTrue(fight.is_dead(mei))
                self.assertEqual(len(sym.client.messages(combat.KILL_NOTICE)), 1)
                if phase == "flight":
                    self.assertIn(flight, fight.blizzard.flights)
                    self.assertFalse(fight.blizzard.areas)
                for _ in range(100):
                    run.step({})
                if phase == "windup":
                    self.assertFalse(fight.blizzard.flights)
                    self.assertFalse(fight.blizzard.areas)
                    self.assertEqual(mei_tests.health(run, sym).total, before)
                    self.assertIsNone(fight.frost(sym))
                else:
                    [area] = fight.blizzard.areas.values()
                    self.assertIs(area.owner, mei)
                    self.assertLess(mei_tests.health(run, sym).total, before)
                    self.assertIsNotNone(fight.frost(sym))

    @unittest.skipUnless(os.environ.get("ORPHEUS_EXTRA_GRAPH_BUNDLES"), "requires sentry actor graph shard")
    def test_blizzard_destroys_enemy_sentry_and_its_slow_but_spares_allied_sentry(self):
        for allied in (False, True):
            with self.subTest(allied=allied):
                run, sym, mei = mei_tests.SymmetraMeiWeaponTests().duel()
                fixture = barrier_tests.PhotonBarrierTests()
                fixture.move(sym, (0, 1, 0))
                fixture.move(mei, (1.2, 1, 0))
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                sym.team, mei.team = 0, int(not allied)
                fight = run.match.combat
                with patch.object(fight, "_collision", return_value=wall()):
                    for _ in range(40):
                        run.step({sym: (0, 0, 8)})
                [sentry] = fight.sentries.sentries
                self.assertIsNotNone(sentry.attached_at)
                pool = mei_tests.health(run, sentry.bot)
                self.assertEqual(pool.total, 30)
                self.cast(run, mei)
                area = self.land(run)
                if not allied:
                    self.assertIsNotNone(sentry.slow)
                    self.assertEqual(sentry.target, mei.body)
                while run.frame * 16 <= area.starts + 1600:
                    run.step({})
                if allied:
                    self.assertIn(sentry, fight.sentries.sentries)
                    self.assertEqual(pool.total, 30)
                else:
                    self.assertEqual(pool.total, 0)
                    self.assertNotIn(sentry, fight.sentries.sentries)
                    self.assertNotIn(sentry.bot, run.match.bots)
                    self.assertFalse(mei.body_script.component.entity_var(23186).value())
                    self.assertFalse(any(who is sentry.bot for _, who in fight.respawns))

    @unittest.skipUnless(os.environ.get("ORPHEUS_EXTRA_GRAPH_BUNDLES"), "requires portal actor graph shard")
    def test_blizzard_damages_near_portal_shields_and_can_retire_pair(self):
        for weakened in (False, True):
            with self.subTest(weakened=weakened):
                fixture = portal_tests.TeleporterTests()
                run, sym, instance, pair = fixture.deploy()
                mei = run.players[1]
                self.assertTrue(run.match.switch_hero(mei, heroes()[cryo.HERO]))
                fixture.place(mei, (1, 0, -2))
                fixture.place(sym, (14, 0, 0))
                sym.team, mei.team = 0, 1
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                fight = run.match.combat
                far, near = pair.endpoints
                near_pool, far_pool = fight.vitals[near.entity], fight.vitals[far.entity]
                fixture.until(run, pair.ready_at)
                if weakened:
                    angles = aim(fight._eye(mei), (0, .85, 0))
                    for _ in range(3):
                        for frame in range(60):
                            run.step({mei: (*angles, 2 if frame < 20 else 0)})
                    self.assertAlmostEqual(near_pool.total, 75)
                    self.assertEqual(near_pool.now[combat.HEALTH_POOL], 50)
                self.cast(run, mei)
                area = self.land(run)
                while run.frame * 16 <= area.ends and pair in fight.portals.pairs:
                    run.step({})
                self.assertEqual(far_pool.total, 300)
                if weakened:
                    self.assertEqual(near_pool.total, 0)
                    self.assertNotIn(pair, fight.portals.pairs)
                    self.assertTrue(all(bot not in run.match.bots for bot in pair.endpoints))
                    self.assertGreater(instance.find_var(0, 61).value(), 9)
                else:
                    self.assertIn(pair, fight.portals.pairs)
                    self.assertEqual(near_pool.now[combat.HEALTH_POOL], 50)
                    self.assertAlmostEqual(near_pool.now[combat.SHIELDS_POOL], 165)

    @unittest.skipUnless(os.environ.get("ORPHEUS_EXTRA_GRAPH_BUNDLES"), "requires Photon Barrier actor shard")
    def test_blizzard_flight_and_area_cross_live_photon_barrier(self):
        for flight_crosses in (False, True):
            with self.subTest(flight_crosses=flight_crosses):
                fixture = barrier_tests.PhotonBarrierTests()
                run, sym, mei, barrier = fixture.deploy()
                self.assertTrue(run.match.switch_hero(mei, heroes()[cryo.HERO]))
                fight = run.match.combat
                plane = barrier.bot.position[2]
                self.assertEqual(barrier.normal, (0, 0, 1))
                fixture.move(mei, (0, 0, plane + 3))
                fixture.move(sym, (0, 0, plane - 2))
                sym.team, mei.team = 0, 1
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                for _ in range(6):
                    run.step({})
                point = (0, 0, plane - 6) if flight_crosses else (1, 0, plane + 3)
                self.cast(run, mei, point=point)
                [flight] = fight.blizzard.flights
                self.assertGreater(flight.position[2], plane)
                area = self.land(run)
                self.assertEqual(area.position[2] < plane, flight_crosses)
                target = next(t for t in fight._targets() if t.owner is sym)
                centre = (area.position[0], area.position[1] + .5, area.position[2])
                self.assertEqual(fight.barriers.occludes(mei, centre, target), not flight_crosses)
                self.assertIn(barrier, fight.barriers.walls)
                pool = fight.vitals[barrier.bot.entity]
                self.assertEqual(pool.total, 4000)
                froze = False
                while run.frame * 16 < area.ends:
                    run.step({})
                    froze |= fight.is_stunned(sym)
                self.assertTrue(froze)
                self.assertAlmostEqual(mei_tests.health(run, sym).total, 140)
                self.assertIn(barrier, fight.barriers.walls)
                self.assertEqual(pool.total, 4000)

    def test_nonlethal_flashbang_cancels_blizzard_windup_but_not_flight(self):
        for released in (False, True):
            with self.subTest(released=released):
                run, sym, mei = mei_tests.SymmetraMeiWeaponTests().duel()
                fixture = barrier_tests.PhotonBarrierTests()
                caster = fixture.add_target(run, 3, (0, 1, -2))
                self.assertTrue(run.match.switch_hero(caster, heroes()[0x02E0000000000042]))
                fixture.move(caster, (0, 1, -2))
                fight = run.match.combat
                for _ in range(20):
                    run.step({})
                if released:
                    self.cast(run, mei)
                    [flight] = fight.blizzard.flights
                else:
                    mei.body_script.set_ult_charge(mei.body_script.component.entity_var(ULT_COST).value())
                    angles = aim(fight._eye(mei), (0, 1, 5))
                    run.step({mei: (*angles, 32)})
                    graph = next(i for i in mei.body_script.component.instances.values()
                                 if i.graph.index == 0x064A)
                    self.assertTrue(any(state.active and state.node.cls == "STUStatescriptStateWait"
                                        for state in graph.states.values()))
                before = mei_tests.health(run, mei).total
                angles = aim(fight._eye(caster), (0, 2, 0))
                run.step({caster: (*angles, 16)})
                self.assertTrue(any(rocket.shooter is caster for rocket in fight.rockets))
                for _ in range(20):
                    if fight.is_stunned(mei):
                        break
                    run.step({})
                self.assertTrue(fight.is_stunned(mei))
                self.assertFalse(fight.is_dead(mei))
                self.assertAlmostEqual(before - mei_tests.health(run, mei).total, 25)
                if released:
                    self.assertIn(flight, fight.blizzard.flights)
                for _ in range(100):
                    run.step({})
                self.assertFalse(fight.is_stunned(mei))
                if released:
                    [area] = fight.blizzard.areas.values()
                    self.assertIs(area.owner, mei)
                    self.assertLess(mei_tests.health(run, sym).total, 225)
                    self.assertIsNotNone(fight.frost(sym))
                else:
                    self.assertFalse(fight.blizzard.flights)
                    self.assertFalse(fight.blizzard.areas)
                    self.assertEqual(mei_tests.health(run, sym).total, 225)
                    self.assertIsNone(fight.frost(sym))

    @unittest.skipUnless(os.environ.get("ORPHEUS_EXTRA_GRAPH_BUNDLES"), "requires Photon Barrier actor shard")
    def test_photon_barrier_protects_allied_mei_windup_from_actual_flashbang(self):
        fixture = barrier_tests.PhotonBarrierTests()
        run, sym, caster, barrier = fixture.deploy()
        self.assertTrue(run.match.switch_hero(caster, heroes()[0x02E0000000000042]))
        mei = fixture.add_target(run, 3, (0, 0, -.5))
        self.assertTrue(run.match.switch_hero(mei, heroes()[cryo.HERO]))
        fixture.move(mei, (0, 0, -.5))
        fixture.move(caster, (0, 0, 4))
        fixture.move(sym, (4, 0, 0))
        sym.team = mei.team = 0
        caster.team = 1
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        fight = run.match.combat
        for _ in range(20):
            run.step({})
        mei.body_script.set_ult_charge(mei.body_script.component.entity_var(ULT_COST).value())
        throw = aim(fight._eye(mei), (0, 0, 5))
        flash = aim(fight._eye(caster), (0, 1, -.5))
        caster_before = mei_tests.health(run, caster).total
        ally_before = mei_tests.health(run, sym).total
        run.step({mei: (*throw, 32)})
        run.step({caster: (*flash, 16)})
        self.assertTrue(any(rocket.shooter is caster for rocket in fight.rockets))
        for _ in range(30):
            run.step({})
            self.assertFalse(fight.is_stunned(mei))
        [flight] = fight.blizzard.flights
        self.assertIs(flight.owner, mei)
        self.assertEqual(mei_tests.health(run, mei).total, 250)
        self.assertAlmostEqual(fight.vitals[barrier.bot.entity].total, 3975)
        area = self.land(run)
        while run.frame * 16 <= area.starts + 350:
            run.step({})
        self.assertLess(mei_tests.health(run, caster).total, caster_before)
        self.assertIsNotNone(fight.frost(caster))
        self.assertEqual(mei_tests.health(run, sym).total, ally_before)
        self.assertIsNone(fight.frost(sym))

    def portal_in_blizzard(self):
        fixture = portal_tests.TeleporterTests()
        run, sym, _, pair = fixture.deploy()
        mei = run.players[1]
        self.assertTrue(run.match.switch_hero(mei, heroes()[cryo.HERO]))
        fixture.place(mei, (1, 0, -2))
        fixture.until(run, pair.ready_at)
        self.cast(run, mei)
        area = self.land(run)
        return run, sym, pair, area

    @unittest.skipUnless(os.environ.get("ORPHEUS_EXTRA_GRAPH_BUNDLES"), "requires portal actor graph shard")
    def test_portal_escape_stops_blizzard_and_return_starts_fresh_exposure(self):
        run, sym, pair, area = self.portal_in_blizzard()
        fight = run.match.combat
        far, near = pair.endpoints
        while run.frame * 16 < area.starts + 420:
            run.step({})
        self.assertGreater(fight.frost(sym)[0], .5)
        self.assertFalse(fight.is_stunned(sym))
        run.step({sym: (0, 0, 4)})
        self.assertAlmostEqual(sym.mover.position[2], far.position[2])
        after_exit = mei_tests.health(run, sym).total
        self.assertLess(after_exit, 225)
        for _ in range(73):
            run.step({})
        self.assertAlmostEqual(mei_tests.health(run, sym).total, after_exit)
        self.assertIsNone(fight.frost(sym))
        self.assertIn(area.entity, fight.blizzard.areas)
        run.step({sym: (0, 0, 4)})
        self.assertAlmostEqual(sym.mover.position[2], near.position[2])
        run.step({})
        self.assertAlmostEqual(after_exit - mei_tests.health(run, sym).total, .32)
        self.assertAlmostEqual(fight.frost(sym)[0], .5)

    @unittest.skipUnless(os.environ.get("ORPHEUS_EXTRA_GRAPH_BUNDLES"), "requires portal actor graph shard")
    def test_frozen_symmetra_cannot_use_live_portal_until_thaw(self):
        run, sym, pair, area = self.portal_in_blizzard()
        fight = run.match.combat
        far, near = pair.endpoints
        while run.frame * 16 < area.ends and not fight.is_stunned(sym):
            run.step({})
        self.assertTrue(fight.is_stunned(sym))
        run.step({sym: (0, 0, 4)})
        self.assertAlmostEqual(sym.mover.position[2], near.position[2])
        self.assertNotIn(sym.body, fight.portals.transits)
        while fight.is_stunned(sym):
            run.step({})
        self.assertIn(pair, fight.portals.pairs)
        run.step({sym: (0, 0, 4)})
        self.assertAlmostEqual(sym.mover.position[2], far.position[2])
