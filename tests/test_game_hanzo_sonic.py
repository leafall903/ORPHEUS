"""Actual Sonic input, impact, authored reveal stack and reconstructed search/attachment."""

import math
import os
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import test_game_hanzo as hero_tests
import test_game_hanzo_npc as npc_tests
import test_server_snapshot as snapshot_tests
from test_game_combat import KANEZAKA_DM, Run, aim, in_world
from test_game_mover import box, wall

from ow174.game import bodytrace, combat, movelog, npc, sonic
from ow174.game.collision import World
from ow174.game.content import SOLDIER, heroes
from ow174.game.script import expr
from ow174.game.server_snapshot import snapshot_digest


def profile():
    path = os.environ.get("ORPHEUS_HANZO_SONIC_PROFILE")
    if not path:
        raise unittest.SkipTest("requires explicitly extracted Sonic profile")
    return sonic.load_profile(Path(path), reconstructed_geometry=True)


class SonicPlayerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.profile = profile()

    def setUp(self):
        for module in (bodytrace, movelog):
            self.enterContext(patch.object(module, "ENABLED", False))

    def arena(self):
        run, hero, target = hero_tests.HanzoCombatTests().arena()
        run.match.combat.configure_sonic(self.profile)
        return run, hero, target

    @staticmethod
    def fire(run, hero, target, frames=70):
        point = target.mover.position if hasattr(target, "mover") else target.position
        yaw, pitch = aim(run.match.combat._eye(hero), (point[0], point[1] + 1, point[2]))
        run.step({hero: (yaw, pitch, 8)})
        run.step({})
        for _ in range(frames):
            run.step({hero: (yaw, pitch, 1)})
        run.step({hero: (yaw, pitch, 0)})
        for _ in range(25):
            run.step({})

    @staticmethod
    def flagged(actor):
        return expr.truthy(actor.body_script.component.entity_var(7982).value())

    def test_full_and_short_draw_impact_execute_real_reveal_stack(self):
        for frames, definition in ((70, sonic.DEFINITIONS[0]), (5, sonic.DEFINITIONS[1])):
            with self.subTest(frames=frames):
                run, hero, target = self.arena()
                fight = run.match.combat
                with patch.object(fight.projectiles, "fired", wraps=fight.projectiles.fired) as shots:
                    self.fire(run, hero, target, frames)
                self.assertEqual([c.args[3] for c in shots.call_args_list], [definition])
                self.assertEqual(len(fight.sonars), 1)
                self.assertTrue(self.flagged(target))
                self.assertTrue(fight.revealed_to(hero, target))
                self.assertFalse(fight.revealed_to(target, target))
                self.assertLess(fight.vitals[target.body].total, 200)
                instance = next(i for i in target.body_script.component.instances.values()
                                if i.graph.index == 0x10E)
                self.assertTrue(instance.state(instance.graph.node(9).state).active)

    def test_opposing_hanzo_sonics_and_lethal_bows_keep_independent_sources(self):
        run, first, second = hero_tests.HanzoCombatTests().arena(distance=16)
        match = run.match
        match.game_map = replace(match.game_map, free_for_all=False)
        fight = match.combat
        fight.configure_sonic(self.profile)
        position = tuple(second.mover.position)
        self.assertTrue(match.switch_hero(second, heroes()[sonic.HANZO]))
        first.team, second.team = 0, 1
        second.mover.position[:] = position
        second.mover.body = None
        for _ in range(20):
            run.step({})
        pairs = ((first, second), (second, first))
        actors = (first, second)
        angles = {}
        for shooter, target in pairs:
            x, y, z = target.mover.position
            angles[shooter] = aim(fight._eye(shooter), (x, y + 1, z))
        cooldowns = [next(i for i in actor.body_script.component.instances.values()
                          if i.graph.index == 0x00C1).vars[61] for actor in actors]
        charges = [actor.body_script.component.entity_var(combat.ULT_CHARGE) for actor in actors]
        clocks = [(actor.body_script.frame, actor.body_script.passive_charge_ms, charge.value())
                  for actor, charge in zip(actors, charges, strict=True)]

        def step(buttons=0):
            run.step({actor: (*angles[actor], buttons) for actor in actors})

        step(8)
        step()
        for _ in range(70):
            step(1)
        step()
        self.assertCountEqual([(arrow.source_entity, arrow.shot.definition) for arrow in fight.rockets],
                              [(actor.body, sonic.DEFINITIONS[0]) for actor in actors])
        for _ in range(20):
            step()
        self.assertFalse(fight.rockets)
        self.assertCountEqual([(beacon.source_entity, beacon.attached) for beacon in fight.sonars],
                              [(shooter.body, target.body) for shooter, target in pairs])
        for shooter, target in pairs:
            self.assertTrue(fight.revealed_to(shooter, target))
            self.assertFalse(fight.revealed_to(shooter, shooter))
        for actor, (frame, passive_ms, before), charge, cooldown in zip(actors, clocks, charges, cooldowns, strict=True):
            elapsed_ms = (actor.body_script.frame - frame) * actor.body_script.frame_ms
            passive = (passive_ms + elapsed_ms) // 1000 * 5
            self.assertEqual(charge.value(), before + passive + 125)
            self.assertGreater(cooldown.value(), 11.5)
        self.assertEqual([fight.vitals[actor.body].total for actor in actors], [75, 75])

        for _ in range(70):
            step(1)
        step()
        self.assertCountEqual([arrow.source_entity for arrow in fight.rockets], [actor.body for actor in actors])
        self.assertTrue(all(arrow.sonar is None for arrow in fight.rockets))
        for _ in range(20):
            step()
            if all(fight.vitals[actor.body].dead for actor in actors):
                break
        self.assertFalse(fight.rockets)
        for shooter, target in pairs:
            pool = fight.vitals[target.body]
            self.assertTrue(pool.dead)
            self.assertEqual(pool.killer, shooter.body)
            hits = [hit["+0x78"] for hit in shooter.client.messages(combat.HIT)]
            self.assertEqual([(hit["+0x0"]["+0x0"], hit["+0x4"]) for hit in hits],
                             [(target.body, 125), (target.body, 75)])
            self.assertTrue(hits[-1]["+0x8"] & combat.KILLING)
        self.assertEqual(fight.vitals[first.body].death_tick, fight.vitals[second.body].death_tick)
        extra_charge = [charge.value() - before - 125 for charge, (_, _, before) in zip(charges, clocks, strict=True)]
        self.assertEqual(abs(extra_charge[0] - extra_charge[1]), 75)
        frozen = [charge.value() for charge in charges]
        for _ in range(10):
            step()
        self.assertEqual([charge.value() for charge in charges], frozen)

    def test_sonic_attaches_to_fortified_orisa_and_outlasts_fortify(self):
        run, hero, orisa = self.arena()
        match = run.match
        fight = match.combat
        position = tuple(orisa.mover.position)
        self.assertTrue(match.switch_hero(orisa, heroes()[0x02E000000000013E]))
        orisa.mover.position[:] = position
        orisa.mover.body = None
        for _ in range(25):
            run.step({})
        run.step({orisa: (0, 0, 8)})
        fortify = combat._active_fortify(orisa)
        self.assertIsNotNone(fortify)
        with patch.object(fight.projectiles, "fired", wraps=fight.projectiles.fired) as shots:
            self.fire(run, hero, orisa)
        self.assertEqual([call.args[3] for call in shots.call_args_list], [sonic.DEFINITIONS[0]])
        self.assertTrue(fortify.active)
        self.assertAlmostEqual(fight.vitals[orisa.body].total, 380, places=4)
        beacon, = fight.sonars
        self.assertEqual((beacon.source_entity, beacon.attached), (hero.body, orisa.body))
        self.assertTrue(self.flagged(orisa))
        self.assertTrue(fight.revealed_to(hero, orisa))
        for _ in range(260):
            if not fortify.active:
                break
            run.step({})
        self.assertFalse(fortify.active)
        self.assertEqual(fight.sonars, [beacon])
        self.assertEqual(beacon.attached, orisa.body)
        self.assertTrue(fight.revealed_to(hero, orisa))
        self.assertAlmostEqual(fight.vitals[orisa.body].total, 380, places=4)

    def test_static_wall_impact_reveals_enemy_beyond_wall_without_direct_damage(self):
        run, hero, target = self.arena()
        x, y, z = hero.mover.position
        run.match.collision_world = World(box(x - 20, x + 20, y - 1, y, z - 20, z + 20)
                                          + wall(x - 5, z + 3, x + 5, z + 3, y0=y, y1=y + 6))
        self.fire(run, hero, target)
        fight = run.match.combat
        beacon, = fight.sonars
        self.assertIsNone(beacon.attached)
        self.assertEqual(fight.vitals[target.body].total, 200)
        self.assertTrue(fight.revealed_to(hero, target))
        self.assertTrue(self.flagged(target))

    def test_team_projection_and_capsule_range_boundary_refresh(self):
        run, hero, target = self.arena()
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        ally = run.match.add_player(99, "Observer ally", SOLDIER, hero.team, False)
        in_world(run.match, ally)
        run.match.switch_hero(ally, heroes()[SOLDIER])
        ally.mover.position[:] = target.mover.position
        ally.mover.position[0] += 2
        self.fire(run, hero, target)
        fight = run.match.combat
        beacon, = fight.sonars
        self.assertTrue(fight.revealed_to(ally, target))
        self.assertFalse(fight.revealed_to(target, target))
        self.assertFalse(self.flagged(ally))
        # Freeze the body's attachment before independently testing target range.
        beacon.attached = None
        bx, by, bz = beacon.position
        for distance, expected in ((9.4, True), (9.6, False), (8, True)):
            target.mover.position[:] = (bx + distance, by - 1, bz)
            target.mover.body = None
            run.step({})
            self.assertEqual(fight.revealed_to(hero, target), expected)
            self.assertEqual(self.flagged(target), expected)

    def test_body_attachment_moves_then_missing_body_freezes_last_point(self):
        run, hero, target = self.arena()
        self.fire(run, hero, target)
        fight = run.match.combat
        beacon, = fight.sonars
        self.assertEqual(beacon.attached, target.body)
        before = beacon.position
        target.mover.position[0] += 12
        target.mover.body = None
        run.step({})
        self.assertAlmostEqual(beacon.position[0] - before[0], 12)
        self.assertTrue(fight.revealed_to(hero, target))
        point = beacon.position
        run.match.switch_hero(target, heroes()[SOLDIER])
        run.step({})
        self.assertIsNone(beacon.attached)
        self.assertEqual(beacon.position, point)

    def test_expiry_and_source_death_leave_cleanup_restore_owned_flag(self):
        for leave in (False, True):
            with self.subTest(leave=leave):
                run, hero, target = self.arena()
                self.fire(run, hero, target)
                fight = run.match.combat
                beacon, = fight.sonars
                victim = next(t for t in fight._targets() if t.owner is hero)
                fight._damage(target, victim, 200, False, None, run.frame)
                run.step({})
                self.assertTrue(fight.revealed_to(hero, target))
                if leave:
                    hero.gone = True
                    run.step({})
                else:
                    tick = math.ceil(beacon.born + 6 / 0.016)
                    fight.match.tick = tick
                    fight.update(tick * 0.016, tick, frames=False)
                self.assertFalse(fight.sonars)
                self.assertFalse(self.flagged(target))

    def test_short_sonic_source_switch_retires_the_arrow_but_preserves_a_deployed_beacon(self):
        for deployed in (False, True):
            with self.subTest(deployed=deployed):
                run, hero, target = self.arena()
                match = run.match
                fight = match.combat
                old_body, old_script = hero.body, hero.body_script
                x, y, z = target.mover.position
                angles = aim(fight._eye(hero), (x, y + 0.85, z))
                run.step({hero: (*angles, 8)})
                run.step({})
                for _ in range(5):
                    run.step({hero: (*angles, 1)})
                run.step({hero: (*angles, 0)})
                arrow, = fight.rockets
                self.assertIsNotNone(arrow.shot)
                self.assertEqual(arrow.shot.definition, sonic.DEFINITIONS[1])
                self.assertEqual(arrow.shot.body, old_body)
                shot = arrow.shot
                pool = fight.vitals_of(next(t for t in fight._targets() if t.owner is target))
                self.assertEqual(pool.total, 200)

                if deployed:
                    for _ in range(25):
                        run.step({})
                        if fight.sonars:
                            break
                    beacon, = fight.sonars
                    self.assertEqual(beacon.attached, target.body)
                    self.assertAlmostEqual(pool.total, 200 - arrow.direct.amount)
                    self.assertTrue(self.flagged(target))
                else:
                    self.assertFalse(fight.sonars)
                self.assertTrue(match.switch_hero(hero, heroes()[SOLDIER]))
                self.assertNotEqual(hero.body, old_body)
                self.assertTrue(old_script.retired)
                for _ in range(25):
                    run.step({})
                self.assertNotIn(arrow, fight.rockets)
                self.assertNotIn(shot, fight.projectiles.shots)

                if deployed:
                    self.assertEqual(fight.sonars, [beacon])
                    self.assertEqual(beacon.source_entity, old_body)
                    self.assertEqual(beacon.attached, target.body)
                    self.assertTrue(fight.revealed_to(hero, target))
                    self.assertTrue(self.flagged(target))
                    tick = math.ceil(beacon.born + beacon.profile.duration / combat.TICK) + 1
                    match.update(tick * combat.TICK, tick)
                    self.assertFalse(fight.sonars)
                    self.assertFalse(self.flagged(target))
                else:
                    self.assertEqual(pool.total, 200)
                    self.assertFalse(fight.sonars)
                    self.assertFalse(self.flagged(target))

    def test_overlap_uses_independent_authored_stacks_and_does_not_erase_base(self):
        run = Run(replace(KANEZAKA_DM, free_for_all=False), ("A", "B", "Target"))
        a, b, target = run.players
        for hero in (a, b):
            run.match.switch_hero(hero, heroes()[sonic.HANZO])
            hero.team = 0
        tank = next(h for h in heroes().values() if h.name == "Roadhog")
        run.match.switch_hero(target, tank)
        target.team = 1
        x, y, z = a.mover.position
        b.mover.position[:] = (x + 1.5, y, z)
        target.mover.position[:] = (x, y, z + 5)
        run.match.bots.clear()
        run.match.combat.configure_sonic(self.profile)
        self.fire(run, a, target)
        self.fire(run, b, target)
        fight = run.match.combat
        first, second = sorted(fight.sonars, key=lambda value: value.born)
        self.assertEqual(len(first.statuses), 1)
        self.assertEqual(len(second.statuses), 1)
        self.assertIs(first.statuses[target.body].graph, second.statuses[target.body].graph)
        tick = math.ceil(first.born + 6 / 0.016)
        fight.match.tick = tick
        fight.update(tick * 0.016, tick, frames=False)
        self.assertEqual(len(fight.sonars), 1)
        self.assertTrue(self.flagged(target))
        target.body_script.component.set(7982, True)  # independent pre-existing reveal provider/base
        tick = math.ceil(second.born + 6 / 0.016)
        fight.match.tick = tick
        fight.update(tick * 0.016, tick, frames=False)
        self.assertFalse(fight.sonars)
        self.assertTrue(self.flagged(target))

    def test_profile_and_beacon_lifetime_change_stored_state(self):
        case = snapshot_tests.ServerSnapshotTests()
        session, _, players = case.scene(two=True)
        fight = players[0].match.combat
        before = snapshot_digest(case.capture(session))
        fight.configure_sonic(self.profile)
        configured = snapshot_digest(case.capture(session))
        self.assertNotEqual(before, configured)
        source = players[0]
        target = players[1]
        target.team = source.team + 1
        beacon = sonic.Beacon(source, source.body, source.team, fight._eye(target), None,
                              (0, 0, 0), source.match.tick, self.profile)
        fight.sonars.append(beacon)
        self.assertTrue(sonic.advance(fight, beacon, source.match.tick))
        self.assertTrue(self.flagged(target))
        active = snapshot_digest(case.capture(session))
        beacon.born += 1
        self.assertNotEqual(active, snapshot_digest(case.capture(session)))
        sonic.remove(beacon, target.body)
        self.assertFalse(self.flagged(target))
        self.assertNotEqual(active, snapshot_digest(case.capture(session)))

    def launch_empty(self, pitch, *, held=70, definition=0x0400000000000149):
        run, hero, target = self.arena()
        fight = run.match.combat
        run.match.collision_world = World([], name="declared empty Sonic expiry fixture")
        target.spawned = False
        run.step({hero: (0, pitch, 8)})
        run.step({})
        for _ in range(held):
            run.step({hero: (0, pitch, 1)})
        with patch.object(fight.projectiles, "fired", wraps=fight.projectiles.fired) as fired:
            run.step({hero: (0, pitch, 0)})
        fired.assert_called_once()
        self.assertEqual(fired.call_args.args[3], definition)
        arrow, = fight.rockets
        self.assertIs(arrow.sonar, self.profile)
        return run, hero, target, arrow

    def test_unimpacted_range_or_lifetime_expiry_does_not_create_a_sonar_field(self):
        for pitch in (0, -16384):
            with self.subTest(pitch=pitch):
                run, hero, target, arrow = self.launch_empty(pitch)
                fight = run.match.combat
                run.step({})
                tick = run.frame + 2000
                fight.match.tick = tick
                fight.update(tick * combat.TICK, tick, frames=False)
                self.assertFalse(fight.rockets)
                if pitch:
                    self.assertAlmostEqual(arrow.flown, 20)
                    self.assertLess(arrow.distance_flown, 1000)
                else:
                    self.assertLess(arrow.flown, 20)
                    self.assertAlmostEqual(arrow.distance_flown, 1000)
                self.assertFalse(fight.sonars)

                x, y, z = arrow.position
                target.mover.position[:] = (x + 2, y - 1, z)
                target.mover.body = None
                target.spawned = True
                fight.match.tick = tick + 1
                fight.update((tick + 1) * combat.TICK, tick + 1, frames=False)
                self.assertFalse(self.flagged(target))
                self.assertFalse(fight.revealed_to(hero, target))
                self.assertFalse(hero.client.messages(combat.HIT))

    def test_sonic_contact_before_path_or_lifetime_limit_deploys_only_on_hit(self):
        for pitch in (0, -16384):
            with self.subTest(pitch=pitch):
                self.verify_terminal_contact(pitch)

    def test_short_bow_and_sonic_straddle_the_ballistic_path_limit(self):
        from test_game_hanzo_bow import HanzoBowTests

        def ordinary(pitch):
            run, _, arrow = HanzoBowTests().empty_flight(pitch, held=5)
            self.assertEqual(arrow.shot.definition, 0x040000000000020B)
            return run, *run.players, arrow

        def sonic_arrow(pitch):
            return self.launch_empty(pitch, held=5, definition=0x040000000000020D)

        for pitch in (0, -16384):
            for launch, definition, deploys in ((ordinary, 0x040000000000020B, False),
                                                (sonic_arrow, 0x040000000000020D, True)):
                with self.subTest(pitch=pitch, definition=hex(definition)):
                    self.verify_terminal_contact(pitch, launch=launch, expected_amount=25 + 100 * 1.375 * 0.079,
                                                 deploys=deploys, lifetime=False)

    def verify_terminal_contact(self, pitch, *, launch=None, expected_amount=125,
                                deploys=True, lifetime=None):
        launch = launch or self.launch_empty
        lifetime = bool(pitch) if lifetime is None else lifetime
        baseline, _, _, arrow = launch(pitch)
        self.assertEqual((arrow.range_left, arrow.left), (1000, 20))
        amount = arrow.direct.amount
        self.assertAlmostEqual(amount, expected_amount, delta=0.12)
        initial_velocity = tuple(axis * arrow.speed for axis in arrow.direction)
        baseline.step({})
        tick = baseline.frame + 2000
        baseline.match.tick = tick
        baseline.match.combat.update(tick * combat.TICK, tick, frames=False)
        if lifetime:
            self.assertAlmostEqual(arrow.flown, 20)
        else:
            self.assertAlmostEqual(arrow.distance_flown, 1000)
            self.assertLess(arrow.flown, 20)
        terminal_time = arrow.flown
        endpoint = arrow.position
        vx, vy, vz = initial_velocity
        tangent = (vx, vy - arrow.gravity * terminal_time, vz)
        speed = math.hypot(*tangent)
        tangent = tuple(axis / speed for axis in tangent)

        for offset, dealt in ((-2, 2 * amount), (2, 0)):
            with self.subTest(offset=offset):
                run, hero, target, arrow = launch(pitch)
                fight = run.match.combat
                run.step({})
                tick = run.frame + math.floor((terminal_time - 1) / combat.TICK)
                fight.match.tick = tick
                fight.update(tick * combat.TICK, tick, frames=False)
                self.assertTrue(terminal_time - 1.1 < arrow.flown < terminal_time)
                x, y, z = (point + offset * axis for point, axis in zip(endpoint, tangent, strict=True))
                target.mover.position[:] = (x, y - 1, z)
                target.mover.body = None
                target.spawned = True
                vitals = fight.vitals[target.body] = combat.Vitals(target.body, 500)
                for frame in range(tick + 1, tick + 70):
                    fight.match.tick = frame
                    fight.update(frame * combat.TICK, frame, frames=False)
                    if not fight.rockets:
                        break
                self.assertFalse(fight.rockets)
                self.assertEqual(vitals.total, 500 - dealt)
                if not dealt:
                    for actual, expected in zip(arrow.position, endpoint, strict=True):
                        self.assertAlmostEqual(actual, expected)
                    self.assertAlmostEqual(arrow.flown, terminal_time)
                hits = hero.client.messages(combat.HIT)
                self.assertEqual([hit["+0x78"]["+0x4"] for hit in hits], [round(dealt)] if dealt else [])
                self.assertTrue(all(hit["+0x78"]["+0x8"] & combat.CRITICAL for hit in hits))
                self.assertEqual(self.flagged(target), bool(dealt and deploys))
                self.assertEqual(fight.revealed_to(hero, target), bool(dealt and deploys))
                if dealt and deploys:
                    beacon, = fight.sonars
                    self.assertEqual(beacon.attached, target.body)
                else:
                    self.assertFalse(fight.sonars)


class SonicCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.profile = profile()
        npc_tests.HanzoCatalogNpcTests.setUpClass()
        cls.catalog = npc_tests.HanzoCatalogNpcTests.catalog

    @classmethod
    def tearDownClass(cls):
        npc_tests.HanzoCatalogNpcTests.tearDownClass()

    def setUp(self):
        for module in (bodytrace, movelog):
            self.enterContext(patch.object(module, "ENABLED", False))

    def test_actual_sonic_impacts_reveal_all_five_actual_npc_bodies(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, hero, target = npc_tests.HanzoCatalogNpcTests().arena(definition, "sonic", "hit")
                fight = run.match.combat
                fight.configure_sonic(self.profile)
                SonicPlayerTests.fire(run, hero, target)
                self.assertTrue(fight.revealed_to(hero, target))
                self.assertTrue(SonicPlayerTests.flagged(target))
                self.assertEqual(fight.vitals[target.entity].total,
                                 definition.health + definition.shields - 125)

    def test_wall_sonic_reveals_each_hostile_talon_body_until_range_exit_or_expiry(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, hero, target = npc_tests.HanzoCatalogNpcTests().arena(definition, "sonic", "wall")
                match = run.match
                self.addCleanup(npc.remove_spawned, match)
                match.game_map = replace(match.game_map, free_for_all=False)
                x, y, z = target.position
                ally = npc.spawn(match, definition, (x + 2, y, z), team=hero.team)
                fight = match.combat
                fight.configure_sonic(self.profile)
                SonicPlayerTests.fire(run, hero, target)
                beacon, = fight.sonars
                self.assertIsNone(beacon.attached)
                self.assertAlmostEqual(beacon.position[2], z - 2.5, places=4)
                self.assertEqual(set(beacon.statuses), {target.entity})
                self.assertTrue(fight.revealed_to(hero, target))
                self.assertTrue(SonicPlayerTests.flagged(target))
                self.assertFalse(fight.revealed_to(hero, ally))
                self.assertFalse(SonicPlayerTests.flagged(ally))
                self.assertFalse(hero.client.messages(combat.HIT))
                full_hp = definition.health + definition.armour + definition.shields
                self.assertEqual(fight.vitals[target.entity].total, full_hp)
                self.assertEqual(fight.vitals[ally.entity].total, full_hp)

                target.position = (x + 20, y, z)
                run.step({})
                self.assertNotIn(target.entity, beacon.statuses)
                self.assertFalse(SonicPlayerTests.flagged(target))
                target.position = (x, y, z)
                run.step({})
                self.assertIn(target.entity, beacon.statuses)
                self.assertTrue(SonicPlayerTests.flagged(target))

                expiry = math.ceil(beacon.born + beacon.profile.duration / combat.TICK)
                run.frame = expiry - 1
                match.update(run.frame * combat.TICK, run.frame)
                self.assertTrue(fight.revealed_to(hero, target))
                run.step({})
                self.assertFalse(fight.sonars)
                self.assertFalse(beacon.statuses)
                self.assertFalse(fight.revealed_to(hero, target))
                self.assertFalse(SonicPlayerTests.flagged(target))

    def test_attached_sonic_clears_dead_talon_status_and_freezes_when_corpse_is_removed(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, hero, target = npc_tests.HanzoCatalogNpcTests().arena(definition, "sonic", "hit")
                match = run.match
                self.addCleanup(npc.remove_spawned, match)
                fight = match.combat
                fight.configure_sonic(self.profile)
                SonicPlayerTests.fire(run, hero, target)
                beacon, = fight.sonars
                self.assertEqual(beacon.attached, target.entity)
                self.assertTrue(SonicPlayerTests.flagged(target))

                pool = fight.vitals[target.entity]
                pool.damage(max(0, pool.total - 100))
                x, y, z = target.position
                yaw, pitch = aim(fight._eye(hero), (x, y + 1, z))
                for _ in range(70):
                    run.step({hero: (yaw, pitch, 1)})
                run.step({hero: (yaw, pitch, 0)})
                for _ in range(15):
                    run.step({})
                    if pool.dead:
                        break
                self.assertTrue(pool.dead)
                self.assertEqual(pool.killer, hero.body)
                self.assertIn(target, match.bots)
                self.assertEqual(beacon.attached, target.entity)
                self.assertNotIn(target.entity, beacon.statuses)
                self.assertFalse(fight.revealed_to(hero, target))
                self.assertFalse(SonicPlayerTests.flagged(target))

                point = beacon.position
                self.assertEqual(npc.remove_spawned(match), (target.entity,))
                replacement = npc.spawn(match, definition, (x + 2, y, z), team=target.team)
                self.assertNotEqual(replacement.entity, target.entity)
                run.step({})
                self.assertIsNone(beacon.attached)
                self.assertEqual(beacon.position, point)
                self.assertEqual(set(beacon.statuses), {replacement.entity})
                self.assertTrue(SonicPlayerTests.flagged(replacement))
