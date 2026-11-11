"""Actual Steel Trap input and Torbjorn combat while rooted."""

import math
import os
from dataclasses import replace
import unittest
from pathlib import Path
from unittest.mock import patch

from test_game_combat import aim
from test_game_mover import box
import test_game_torbjorn_bubbles as bubble_tests
import test_game_torbjorn_defenses as defense_tests
import test_game_torbjorn_phases as phase_tests
import test_game_torbjorn_matrix as matrix_tests
from ow174.game import combat, hero_torbjorn, npc, npc_control, projectile_defenses, sigma_grasp, stats
from ow174.game.collision import World
from ow174.game.commands import Command, JUMP
from ow174.game.content import heroes
from ow174.game.script import expr, graph, nodes


@unittest.skipUnless(os.environ.get("ORPHEUS_STEEL_TRAP_GRAPH_BUNDLE"),
                     "needs the prepared Steel Trap graph bundle")
class TorbjornSteelTrapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with patch.dict(os.environ, {
            "ORPHEUS_TORBJORN_GRAPH_BUNDLE": os.environ["ORPHEUS_STEEL_TRAP_GRAPH_BUNDLE"],
        }):
            phase_tests.TorbjornPhaseTests.setUpClass()
        cls.addClassCleanup(phase_tests.TorbjornPhaseTests.tearDownClass)
        if graph.graph(0x09BC) is None:
            raise ValueError("missing Steel Trap receiver")

    def arena(self, distance=4.7):
        fixture = phase_tests.TorbjornPhaseTests()
        return fixture.arena("Junkrat", distance=distance, behind_distance=100)

    def land(self, run, junkrat):
        fight = run.match.combat
        run.step({junkrat: (-32768, 0, 16)})
        for _ in range(80):
            run.step({})
            trap = fight.steel_traps.devices.get(junkrat.body)
            if trap is not None and trap.armed_at is not None:
                self.assertEqual(trap.armed_at, run.frame + 32)
                return trap
        self.fail("actual Steel Trap did not land")

    def capture(self, run, torb, trap):
        while run.frame < trap.armed_at:
            run.step({})
        self.assertEqual(trap.victim, torb.body)
        self.assertEqual(trap.release_tick, run.frame + 188)
        self.assertEqual(trap.status.graph.index, 0x09BC)
        move = trap.status.state(trap.status.graph.node(6).state)
        self.assertEqual(move.move_request(), (expr.Vec3(*trap.position), 10.0))
        before = abs(torb.mover.position[2] - trap.position[2])
        phase_tests.TorbjornPhaseTests.advance(run, 15)
        self.assertLess(abs(torb.mover.position[2] - trap.position[2]), before)
        self.assertIsNone(move.move_request())

    def walk(self, run, torb, buttons=0):
        run.frame += 1
        run.match.update(run.frame * combat.TICK, run.frame)
        torb.take_commands([Command(run.frame, forward=127, buttons=buttons)])

    def equip_hammer(self, run, torb):
        run.frame += 1
        torb.take_commands([Command(run.frame, action=2)])
        phase_tests.TorbjornPhaseTests.advance(run, 45)

    def test_armed_trap_checks_capsule_boundary_and_live_cover(self):
        run, torb, junkrat, _ = self.arena()
        fight = run.match.combat
        hp = phase_tests.TorbjornPhaseTests.health(run, torb)
        trap = self.land(run, junkrat)
        x, _, z = trap.position
        y = torb.mover.position[1]
        open_world = run.match.collision_world
        place = defense_tests.TorbjornDefenseTests.place
        place(junkrat, (x + 50, y, z + 50))
        self.assertEqual(trap.radius, 1)

        def target():
            return next(item for item in fight._targets() if item.entity == torb.body)

        def place_at_clearance(clearance):
            capsule = target()
            a, b = capsule.segment((x, y, z))
            height = combat.segment_distance(trap.position, a, b)
            radius = trap.radius + capsule.radius + clearance
            distance = math.sqrt(radius ** 2 - height ** 2)
            place(torb, (x + distance, y, z))
            return distance

        def assert_geometry(clearance, visible):
            capsule = target()
            a, b = capsule.segment()
            distance = combat.segment_distance(trap.position, a, b) - capsule.radius
            self.assertAlmostEqual(distance - trap.radius, clearance, delta=.003)
            self.assertEqual(
                combat.capsule_visible(fight._collision(), trap.centre(), a, b, capsule.radius),
                visible,
            )

        place_at_clearance(.01)
        while run.frame < trap.armed_at:
            run.step({})
        assert_geometry(.01, True)
        self.assertIsNone(trap.victim)
        self.assertEqual(hp.total, 250)

        distance = place_at_clearance(-.01)
        wall = x + distance / 3
        run.match.collision_world = World(
            box(x - 15, x + 15, y - 1, y, z - 15, z + 15)
            + box(wall, wall + .025, y, y + 3, z - 2, z + 2)
        )
        run.step({})
        assert_geometry(-.01, False)
        self.assertIsNone(trap.victim)
        self.assertEqual(hp.total, 250)
        self.assertEqual(junkrat.stats.value(stats.HERO_DAMAGE), 0)

        run.match.collision_world = open_world
        run.step({})
        self.assertEqual(trap.victim, torb.body)
        self.assertEqual(hp.total, 175)
        self.assertEqual(trap.status.graph.index, 0x09BC)
        self.assertEqual(trap.release_tick, run.frame + 188)
        self.assertEqual(junkrat.stats.value(stats.HERO_DAMAGE), 75)

    def test_five_npc_capture_uses_live_team_then_keeps_committed_root(self):
        catalog = npc.load_catalog(Path(os.environ["ORPHEUS_NPC_CATALOG_DIR"]) / "pve_npcs_174.json")
        self.assertEqual({definition.name for definition in catalog.values()},
                         {"Trooper", "Enforcer", "Sniper", "Assassin", "Heavy Assault"})
        for definition in catalog.values():
            with self.subTest(npc=definition.name):
                run, torb, junkrat, _ = self.arena()
                match = run.match
                fight = match.combat
                match.game_map = replace(match.game_map, free_for_all=False)
                junkrat.team = torb.team
                for viewer in match.players:
                    viewer.client.bot_stream = lambda *args: None
                trap = self.land(run, junkrat)
                x, _, z = trap.position
                y = torb.mover.position[1]
                place = defense_tests.TorbjornDefenseTests.place
                place(torb, (x + 4, y, z))
                place(junkrat, (x + 50, y, z + 50))
                bot = npc.spawn(match, definition, (x, y, z), team=1 - junkrat.team)
                hp = fight.vitals[bot.entity]
                initial = hp.total
                while run.frame < trap.armed_at - 1:
                    run.step({})
                self.assertIsNone(trap.victim)

                bot.team = junkrat.team
                for _ in range(4):
                    run.step({})
                    self.assertIsNone(trap.victim)
                    self.assertIsNone(trap.status)
                    self.assertEqual(hp.total, initial)
                    self.assertIs(fight.steel_traps.devices[junkrat.body], trap)
                self.assertEqual(junkrat.stats.value(stats.HERO_DAMAGE), 0)

                bot.team = 1 - junkrat.team
                run.step({})
                self.assertEqual(trap.victim, bot.entity)
                self.assertEqual(hp.total, initial - 80)
                self.assertEqual(trap.status.graph.index, 0x09BC)
                self.assertEqual(trap.release_tick, run.frame + 188)
                self.assertEqual(junkrat.stats.value(stats.HERO_DAMAGE), 80)

                bot.team = junkrat.team
                while run.frame < trap.release_tick - 1:
                    run.step({})
                    self.assertEqual(trap.victim, bot.entity)
                    self.assertFalse(trap.status.stopped)
                    self.assertEqual(hp.total, initial - 80)
                run.step({})
                self.assertEqual(run.frame, trap.release_tick)
                self.assertTrue(trap.status.stopped)
                self.assertNotIn(junkrat.body, fight.steel_traps.devices)

    def test_destroyed_trap_keeps_cast_cooldown_and_fresh_trap_recaptures(self):
        run, torb, junkrat, _ = self.arena()
        fight = run.match.combat
        hp = phase_tests.TorbjornPhaseTests.health(run, torb)
        start = tuple(torb.mover.position)
        first = self.land(run, junkrat)
        self.capture(run, torb, first)
        self.assertEqual(hp.total, 175)
        ability, = (state for state in first.instance.states.values() if isinstance(state, nodes.Ability))
        direction = aim(fight._eye(torb), first.centre())
        rifle = next(i for i in torb.body_script.component.instances.values() if i.graph.index == 0x1683)
        charge = torb.body_script.component.entity_var(342).value()
        run.step({torb: (*direction, 2)})
        for _ in range(48):
            if first.health == 0:
                break
            run.step({torb: (*direction, 0)})
        self.assertEqual(first.health, 0)
        self.assertEqual(rifle.vars[53].value(), 16)
        self.assertEqual(torb.stats.value(stats.HERO_DAMAGE), 0)
        self.assertEqual(torb.body_script.component.entity_var(342).value(), charge)
        self.assertTrue(first.status.stopped)
        self.assertFalse(fight.steel_traps.devices)
        self.assertEqual(fight.steel_traps.root_until(torb.body), 0)

        remaining = ability.remaining()
        self.assertGreater(remaining, .5)
        run.step({junkrat: (-32768, 0, 16)})
        self.assertGreater(ability.remaining(), 0)
        self.assertLess(ability.remaining(), remaining)
        self.assertFalse(fight.steel_traps.devices)
        for frame in range(800):
            if ability.remaining() <= 0:
                break
            # Keep repeated presses outside the late input buffer.
            buttons = 16 if frame % 31 == 29 and ability.remaining() > .5 else 0
            run.step({junkrat: (-32768, 0, buttons)})
            self.assertFalse(fight.steel_traps.devices)
        self.assertEqual(ability.remaining(), 0)
        ready = run.frame

        defense_tests.TorbjornDefenseTests.place(torb, start)
        second = self.land(run, junkrat)
        self.assertNotEqual(second.body, first.body)
        self.assertGreaterEqual(second.launch_tick, ready)
        self.capture(run, torb, second)
        self.assertIsNot(second.status, first.status)
        self.assertTrue(first.status.stopped)
        self.assertEqual(hp.total, 95)

    def test_overload_recovers_while_rooted_without_shortening_trap(self):
        run, torb, junkrat, _ = self.arena()
        fight = run.match.combat
        hp = phase_tests.TorbjornPhaseTests.health(run, torb)
        run.step({torb: (0, 0, 16)})
        first = hp.over_armour.source
        self.assertIsNotNone(first)
        self.assertEqual(hp.over_armour.amount, 100)
        for _ in range(1100):
            if hero_torbjorn.overload_cooldown_remaining(torb) <= 1.70:
                break
            run.step({})
        self.assertIsNone(hp.over_armour.source)
        trap = self.land(run, junkrat)
        self.capture(run, torb, trap)
        self.assertEqual(hp.total, 175)
        deadline = trap.release_tick
        fixed = tuple(torb.mover.position)
        remaining = hero_torbjorn.overload_cooldown_remaining(torb)
        self.assertGreater(remaining, .17)
        self.assertLess(remaining, 1)
        self.walk(run, torb, 16 | JUMP)
        self.assertIsNone(hp.over_armour.source)
        self.assertEqual(tuple(torb.mover.position), fixed)
        while hero_torbjorn.overload_cooldown_remaining(torb) > 0:
            self.walk(run, torb, JUMP)
            self.assertEqual(tuple(torb.mover.position), fixed)
            self.assertIsNone(hp.over_armour.source)
            self.assertEqual(fight.steel_traps.root_until(torb.body), deadline)

        self.walk(run, torb, 16 | JUMP)
        self.assertEqual(hp.over_armour.source, (first[0], first[1] + 1))
        self.assertEqual(hp.over_armour.amount, 100)
        self.assertEqual(hp.total, 275)
        self.assertEqual(tuple(torb.mover.position), fixed)
        while run.frame < deadline - 2:
            self.walk(run, torb, JUMP)
            self.assertEqual(tuple(torb.mover.position), fixed)
            self.assertEqual(fight.steel_traps.root_until(torb.body), deadline)
            self.assertEqual(hp.over_armour.amount, 100)
        self.walk(run, torb)
        self.assertEqual(tuple(torb.mover.position), fixed)
        run.step({})
        self.assertEqual(run.frame, deadline)
        self.assertTrue(trap.status.stopped)
        self.assertEqual(fight.steel_traps.root_until(torb.body), 0)
        self.walk(run, torb, JUMP)
        self.assertGreater(torb.mover.position[1], fixed[1])
        self.assertEqual(hp.total, 275)
        for _ in range(350):
            if hp.over_armour.source is None:
                break
            run.step({})
        self.assertIsNone(hp.over_armour.source)
        self.assertEqual(hp.total, 175)

    def test_trap_during_molten_start_preserves_firing_and_burn_through_release(self):
        run, torb, junkrat, _ = self.arena()
        fight = run.match.combat
        player_hp = phase_tests.TorbjornPhaseTests.health(run, torb)
        enemy_hp = phase_tests.TorbjornPhaseTests.health(run, junkrat)
        trap = self.land(run, junkrat)
        while run.frame < trap.armed_at - 3:
            run.step({})
        torb.body_script.set_ult_charge(10000)
        run.step({torb: (0, 4000, 32)})
        pressed = run.frame
        self.assertEqual(pressed, trap.armed_at - 2)
        self.assertEqual(torb.body_script.component.entity_var(342).value(), 0)
        self.assertFalse(fight.rockets)
        self.capture(run, torb, trap)
        self.assertEqual(player_hp.total, 175)
        deadline = trap.release_tick
        while run.frame < pressed + 45:
            run.step({})
        x, y, z = torb.mover.position
        place = defense_tests.TorbjornDefenseTests.place
        place(junkrat, (x + 100, y, z + 100))
        run.step({torb: (0, 4000, 1)})
        self.assertEqual(len(fight.rockets), 1)
        self.assertFalse(fight.molten_pools)
        self.assertEqual(fight.steel_traps.root_until(torb.body), deadline)
        for _ in range(75):
            run.step({})
            if fight.molten_pools:
                break
        pool, = fight.molten_pools.values()
        self.assertIs(pool.owner, torb)
        self.assertEqual(trap.health, 100)
        self.assertEqual(fight.steel_traps.root_until(torb.body), deadline)

        while run.frame < deadline - 8:
            run.step({})
        place(junkrat, tuple(pool.position))
        while run.frame < deadline - 1:
            run.step({})
        key = (torb.entity, junkrat.body)
        burn = fight.molten_burns[key]
        before = enemy_hp.total
        self.assertLess(before, 200)
        run.step({})
        self.assertEqual(run.frame, deadline)
        self.assertTrue(trap.status.stopped)
        self.assertEqual(fight.steel_traps.root_until(torb.body), 0)
        self.assertIs(fight.molten_burns[key], burn)
        self.assertIn(pool, fight.molten_pools.values())
        self.assertAlmostEqual(before - enemy_hp.total, 2.56)
        phase_tests.TorbjornPhaseTests.advance(run, 3)
        self.assertIs(fight.molten_burns[key], burn)
        self.assertAlmostEqual(before - enemy_hp.total, 4 * 2.56)
        self.assertEqual(player_hp.total, 175)
        self.assertAlmostEqual(torb.stats.value(stats.HERO_DAMAGE), 200 - enemy_hp.total)

    def test_actual_root_spends_overload_and_releases_on_deadline_or_rivets(self):
        for destroy in (False, True):
            with self.subTest(destroy=destroy):
                run, torb, junkrat, _ = self.arena()
                fight = run.match.combat
                hp = phase_tests.TorbjornPhaseTests.health(run, torb)
                run.step({torb: (0, 0, 16)})
                trap = self.land(run, junkrat)
                self.capture(run, torb, trap)
                self.assertEqual(hp.total, 275)
                self.assertEqual(hp.over_armour.amount, 25)
                held = tuple(torb.mover.position)
                for _ in range(8):
                    self.walk(run, torb, JUMP)
                self.assertEqual(tuple(torb.mover.position), held)
                if destroy:
                    direction = aim(fight._eye(torb), trap.centre())
                    for remaining in (30, 0):
                        run.step({torb: (*direction, 1)})
                        for _ in range(48):
                            if trap.health == remaining:
                                break
                            run.step({torb: (*direction, 0)})
                        self.assertEqual(trap.health, remaining)
                        if remaining:
                            phase_tests.TorbjornPhaseTests.advance(run, 39, {torb: (*direction, 0)})
                    self.assertLess(run.frame, trap.release_tick)
                else:
                    while run.frame < trap.release_tick - 1:
                        run.step({})
                    self.assertEqual(fight.steel_traps.root_until(torb.body), trap.release_tick)
                    run.step({})
                    self.assertEqual(run.frame, trap.release_tick)
                self.assertFalse(fight.steel_traps.devices)
                self.assertTrue(trap.status.stopped)
                self.walk(run, torb, JUMP)
                self.assertGreater(torb.mover.position[1], held[1])

    def test_secondary_melee_hammer_and_molten_hit_the_trap_while_rooted(self):
        for mode, button, health in (("secondary", 2, 0), ("melee", 0x800, 70),
                                     ("hammer", 1, 45), ("molten", 1, 75)):
            with self.subTest(mode=mode):
                run, torb, junkrat, _ = self.arena()
                fight = run.match.combat
                if mode == "hammer":
                    self.equip_hammer(run, torb)
                trap = self.land(run, junkrat)
                self.capture(run, torb, trap)
                if mode == "molten":
                    torb.body_script.set_ult_charge(10000)
                    run.step({torb: (0, 0, 32)})
                    phase_tests.TorbjornPhaseTests.advance(run, 46)
                x, y, z = torb.mover.position
                origin = (x, y + 1.2, z) if mode == "hammer" else fight._eye(torb)
                direction = aim(origin, trap.centre())
                rifle = next(i for i in torb.body_script.component.instances.values() if i.graph.index == 0x1683)
                ammo = rifle.vars[53]
                with patch.object(fight, "_shoot", wraps=fight._shoot) as shoot:
                    run.step({torb: (*direction, button)})
                    self.assertEqual(len(fight.rockets), {"secondary": 10, "molten": 1}.get(mode, 0))
                    phase_tests.TorbjornPhaseTests.advance(run, 34, {torb: (*direction, 0)})
                self.assertEqual(shoot.call_count, 1 if mode in ("secondary", "molten") else 0)
                self.assertEqual(ammo.value(), 16 if mode == "secondary" else 18)
                self.assertEqual(trap.health, health)
                self.assertEqual(fight.steel_traps.root_until(torb.body), trap.release_tick if health else 0)
                self.assertEqual(torb.stats.value(stats.HERO_DAMAGE), 0)
                self.assertEqual(phase_tests.TorbjornPhaseTests.health(run, junkrat).total, 200)
                self.assertFalse(fight.molten_pools)

    def test_projected_barrier_absorbs_capture_and_prevents_root(self):
        run, torb, junkrat, zarya = self.arena()
        match = run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        self.assertTrue(match.switch_hero(zarya, heroes()[0x02E0000000000068]))
        zarya.team = torb.team
        junkrat.team = 1 - torb.team
        fixture = defense_tests.TorbjornDefenseTests()
        x, y, z = torb.mover.position
        fixture.place(zarya, (x + 5, y, z))
        phase_tests.TorbjornPhaseTests.advance(run, 35)
        trap = self.land(run, junkrat)
        fixture.place(torb, (x + 3, y, z))
        while run.frame < trap.armed_at - 1:
            run.step({})
        bubble = bubble_tests.TorbjornBubbleTests().cast(fixture, run, zarya, torb)
        self.assertIsNone(trap.victim)
        fixture.place(torb, (x, y, z))
        run.step({})
        self.assertEqual(bubble.health, 120)
        self.assertEqual(phase_tests.TorbjornPhaseTests.health(run, torb).total, 250)
        self.assertFalse(fight.steel_traps.devices)
        self.assertEqual(fight.steel_traps.root_until(torb.body), 0)

    def test_same_rooted_hammer_swing_repairs_turret_and_hits_trap_once(self):
        run, torb, junkrat, _ = self.arena(distance=100)
        fight = run.match.combat
        run.step({torb: (0, 4000, 8)})
        phase_tests.TorbjornPhaseTests.advance(run, 225)
        turret = fight.turrets[torb.entity]
        hp = fight.vitals[turret.actor.entity]
        self.assertEqual(hp.total, 250)
        hp.damage(80)
        fight._sync_body_health(turret.actor, hp)
        x, y, z = turret.actor.position
        defense_tests.TorbjornDefenseTests.place(torb, (x - 1.5, y, z))
        self.equip_hammer(run, torb)
        defense_tests.TorbjornDefenseTests.place(junkrat, (x - 1.5, y, z + 4.7))
        trap = self.land(run, junkrat)
        defense_tests.TorbjornDefenseTests.place(junkrat, (x - 1.5, y, z + 100))
        self.capture(run, torb, trap)
        self.assertEqual(hp.total, 170)
        x, y, z = torb.mover.position
        direction = aim((x, y + 1.2, z), trap.centre())
        shots = turret.shots
        charge = torb.body_script.component.entity_var(342).value()
        run.step({torb: (*direction, 1)})
        phase_tests.TorbjornPhaseTests.advance(run, 35, {torb: (*direction, 0)})
        self.assertEqual(trap.health, 45)
        self.assertEqual(hp.total, 220)
        self.assertEqual(turret.shots, shots)
        self.assertEqual(fight.steel_traps.root_until(torb.body), trap.release_tick)
        self.assertEqual(torb.body_script.component.entity_var(342).value(), charge)

    def test_trapped_npcs_attack_and_lethal_rivet_or_removal_clears_capture(self):
        catalog = npc.load_catalog(Path(os.environ["ORPHEUS_NPC_CATALOG_DIR"]) / "pve_npcs_174.json")
        incoming = {"Trooper": 15, "Enforcer": 25, "Sniper": 95, "Assassin": 20, "Heavy Assault": 2.5}
        self.assertEqual({definition.name for definition in catalog.values()}, incoming.keys())
        for definition in catalog.values():
            for lethal in (False, True):
                with self.subTest(npc=definition.name, lethal=lethal):
                    run, torb, junkrat, _ = self.arena()
                    match = run.match
                    fight = match.combat
                    match.game_map = replace(match.game_map, free_for_all=False)
                    junkrat.team = torb.team
                    for viewer in match.players:
                        viewer.client.bot_stream = lambda *args: None
                    trap = self.land(run, junkrat)
                    x, _, z = trap.position
                    y = torb.mover.position[1]
                    defense_tests.TorbjornDefenseTests.place(torb, (x + 2.2, y, z))
                    defense_tests.TorbjornDefenseTests.place(junkrat, (x + 50, y, z + 50))
                    bot = npc.spawn(match, definition, (x, y, z), team=1 - torb.team)
                    bot_hp = fight.vitals[bot.entity]
                    initial = bot_hp.total
                    while run.frame < trap.armed_at:
                        run.step({})
                    self.assertEqual(bot_hp.total, initial - 80)
                    self.assertEqual(trap.victim, bot.entity)
                    self.assertEqual(trap.status.graph.index, 0x09BC)

                    if lethal:
                        self.kill_attacking_npc(run, torb, bot, bot_hp, trap, incoming[definition.name])
                    else:
                        self.assertEqual(npc.remove_spawned(match, entities={bot.entity}), (bot.entity,))
                    self.assertNotIn(trap.source, fight.steel_traps.devices)
                    self.assertTrue(trap.status.stopped)

    def kill_attacking_npc(self, run, torb, bot, bot_hp, trap, damage):
        fight = run.match.combat
        fixture = phase_tests.TorbjornPhaseTests()
        torb_hp = fixture.health(run, torb)
        initial = torb_hp.total
        direction = fixture.body_aim(run, torb, bot)
        run.step({torb: (*direction, 0)})
        self.assertEqual(npc_control.start(run.match, torb), bot.entity)
        for _ in range(155):
            run.step({})
            if torb_hp.total < initial:
                break
        self.assertEqual(torb_hp.total, initial - damage)
        self.assertEqual(trap.victim, bot.entity)
        self.assertLess(run.frame, trap.release_tick)
        if bot.name == "Assassin":
            self.assertEqual(fight.pins.get(bot.entity), torb.body)
            x, y, z = torb.mover.position
            defense_tests.TorbjornDefenseTests.place(torb, (x + 2, y, z))
            run.step({})
            self.assertFalse(fight.is_stunned(torb))

        bot_hp.now[combat.HEALTH_POOL] = 1
        bot_hp.now[combat.ARMOUR_POOL] = 0
        bot_hp.now[combat.SHIELDS_POOL] = 0
        fight._sync_body_health(bot, bot_hp)
        direction = fixture.body_aim(run, torb, bot)
        run.step({torb: (*direction, 1)})
        for _ in range(25):
            if bot.dead:
                break
            run.step({torb: (*direction, 0)})
        self.assertTrue(bot.dead)
        self.assertEqual(bot_hp.killer, torb.body)
        self.assertEqual(bot_hp.death_tick, run.frame)
        self.assertEqual(torb.stats.value(stats.HERO_DAMAGE), 1)
        self.assertEqual(torb.stats.value(stats.ELIMINATIONS), 1)
        self.assertFalse(bot.attack_input)

    def test_lethal_capture_and_rooted_death_clear_status_before_respawn(self):
        for lethal_capture in (True, False):
            with self.subTest(lethal_capture=lethal_capture):
                run, torb, junkrat, _ = self.arena()
                fight = run.match.combat
                fixture = phase_tests.TorbjornPhaseTests
                old_body = torb.body
                hp = fixture.health(run, torb)
                initial_health = 60 if lethal_capture else 100
                hp.now[combat.HEALTH_POOL] = initial_health
                hp.now[combat.ARMOUR_POOL] = 0
                fight._sync_body_health(torb, hp)
                trap = self.land(run, junkrat)
                x, _, z = trap.position
                y = torb.mover.position[1]
                place = defense_tests.TorbjornDefenseTests.place
                if lethal_capture:
                    while run.frame < trap.armed_at:
                        run.step({})
                    self.assertIsNone(trap.status)
                    self.assertIsNone(trap.victim)
                else:
                    self.capture(run, torb, trap)
                    self.assertEqual(hp.total, 20)
                    self.assertFalse(trap.status.stopped)
                    tx, ty, tz = torb.mover.position
                    place(junkrat, (tx + 1.5, ty, tz))
                    direction = fixture.body_aim(run, junkrat, torb)
                    run.step({junkrat: (*direction, 0x800)})
                    for _ in range(30):
                        if hp.dead:
                            break
                        run.step({junkrat: (*direction, 0)})
                    self.assertTrue(trap.status.stopped)
                    self.assertLess(run.frame, trap.release_tick)

                self.assertTrue(hp.dead)
                self.assertEqual(hp.killer, junkrat.body)
                self.assertEqual(hp.death_tick, run.frame)
                self.assertFalse(fight.steel_traps.devices)
                self.assertEqual(fight.steel_traps.root_until(old_body), 0)
                self.assertEqual(junkrat.stats.value(stats.HERO_DAMAGE), initial_health)
                self.assertEqual(junkrat.stats.value(stats.ELIMINATIONS), 1)

                for _ in range(750):
                    if torb.body != old_body:
                        break
                    run.step({})
                self.assertNotEqual(torb.body, old_body)
                self.assertFalse(fight.is_dead(torb))
                fresh = fixture.health(run, torb)
                self.assertIsNot(fresh, hp)
                self.assertEqual(fresh.total, 250)
                self.assertEqual(fight.steel_traps.root_until(torb.body), 0)
                place(torb, (x - 4, y, z))
                place(junkrat, (x + 4, y, z))
                fixture.advance(run, 35)
                before_y = torb.mover.position[1]
                self.walk(run, torb, JUMP)
                self.assertGreater(torb.mover.position[1], before_y)

                enemy_hp = fixture.health(run, junkrat)
                direction = fixture.body_aim(run, torb, junkrat)
                run.step({torb: (*direction, 1)})
                for _ in range(25):
                    if enemy_hp.total < 200:
                        break
                    run.step({torb: (*direction, 0)})
                self.assertEqual(enemy_hp.total, 130)
                self.assertEqual(torb.stats.value(stats.HERO_DAMAGE), 70)

    def test_caster_death_during_flight_or_root_keeps_the_committed_trap(self):
        for captured_before_death in (False, True):
            with self.subTest(captured_before_death=captured_before_death):
                run, torb, junkrat, _ = self.arena()
                fight = run.match.combat
                hp = phase_tests.TorbjornPhaseTests.health(run, torb)
                old_body = junkrat.body
                if captured_before_death:
                    trap = self.land(run, junkrat)
                    self.capture(run, torb, trap)
                    self.assertEqual(hp.total, 175)
                else:
                    run.step({junkrat: (-32768, 0, 16)})
                    for _ in range(20):
                        trap = fight.steel_traps.devices.get(old_body)
                        if trap is not None:
                            break
                        run.step({})
                    self.assertIsNotNone(trap)
                    self.assertIsNone(trap.armed_at)
                    self.assertEqual(hp.total, 250)

                source_hp = phase_tests.TorbjornPhaseTests.health(run, junkrat)
                source_hp.now[combat.HEALTH_POOL] = 1
                fight._sync_body_health(junkrat, source_hp)
                direction = phase_tests.TorbjornPhaseTests.body_aim(run, torb, junkrat)
                run.step({torb: (*direction, 1)})
                for _ in range(25):
                    if source_hp.dead:
                        break
                    run.step({torb: (*direction, 0)})
                self.assertTrue(source_hp.dead)
                self.assertEqual(source_hp.killer, torb.body)
                self.assertEqual(source_hp.death_tick, run.frame)
                self.assertEqual(torb.stats.value(stats.HERO_DAMAGE), 1)
                self.assertEqual(torb.stats.value(stats.ELIMINATIONS), 1)
                self.assertIs(fight.steel_traps.devices[old_body], trap)

                if captured_before_death:
                    self.assertEqual(trap.victim, torb.body)
                else:
                    self.assertIsNone(trap.armed_at)
                    for _ in range(100):
                        if trap.victim is not None:
                            break
                        run.step({junkrat: (-32768, 0, 16)})
                    self.assertEqual(trap.victim, torb.body)
                self.assertEqual(hp.total, 175)
                self.assertEqual(junkrat.stats.value(stats.HERO_DAMAGE), 75)
                self.assertIs(trap.owner, junkrat)
                self.assertIs(fight.steel_traps.devices[old_body], trap)
                release = trap.release_tick
                for _ in range(6):
                    run.step({junkrat: (-32768, 0, 16)})
                    self.assertIs(fight.steel_traps.devices[old_body], trap)
                    self.assertEqual(trap.release_tick, release)
                while run.frame < release - 1:
                    run.step({})
                self.assertFalse(trap.status.stopped)
                self.assertEqual(fight.steel_traps.root_until(torb.body), release)
                run.step({})
                self.assertEqual(run.frame, release)
                self.assertTrue(trap.status.stopped)
                self.assertFalse(fight.steel_traps.devices)
                self.assertTrue(fight.is_dead(junkrat))

                respawn_at, = (when for when, player in fight.respawns if player is junkrat)
                run.frame = math.ceil(respawn_at / combat.TICK) - 1
                run.step({})
                self.assertNotEqual(junkrat.body, old_body)
                self.assertFalse(fight.is_dead(junkrat))
                x, y, z = torb.mover.position
                defense_tests.TorbjornDefenseTests.place(junkrat, (x, y, z + 4.7))
                phase_tests.TorbjornPhaseTests.advance(run, 35)
                fresh = self.land(run, junkrat)
                self.assertNotEqual(fresh.body, trap.body)
                self.assertEqual(fresh.source, junkrat.body)
                self.assertEqual(fresh.health, 100)

    def test_turret_requires_a_body_target_then_breaks_trap_in_its_firing_lane(self):
        run, torb, junkrat, _ = self.arena(distance=100)
        fight = run.match.combat
        run.step({torb: (0, 4000, 8)})
        phase_tests.TorbjornPhaseTests.advance(run, 225)
        turret = fight.turrets[torb.entity]
        x, y, z = turret.actor.position
        # Elevate the actual trap into the ray from the turret to the target.
        run.match.collision_world = World(
            box(x - 15, x + 15, y - 1, y, z - 15, z + 15)
            + box(x - 1, x + 1, y, y + .60, z + 3.5, z + 6.2)
        )
        place = defense_tests.TorbjornDefenseTests.place
        place(junkrat, (x, y, z + 8))
        run.step({junkrat: (-32768, 0, 16)})
        for _ in range(25):
            trap = fight.steel_traps.devices.get(junkrat.body)
            if trap is not None:
                break
            run.step({})
        self.assertIsNotNone(trap)
        place(junkrat, (x + 100, y, z + 100))
        for _ in range(100):
            run.step({})
            if trap.armed_at is not None and run.frame >= trap.armed_at:
                break
        self.assertIsNotNone(trap.armed_at)
        self.assertAlmostEqual(trap.position[1], y + .6, delta=.001)
        self.assertIsNone(trap.victim)
        shots = turret.shots
        phase_tests.TorbjornPhaseTests.advance(run, 25)
        self.assertEqual(turret.shots, shots)
        self.assertIsNone(turret.target)
        self.assertEqual(trap.health, 100)

        hp = phase_tests.TorbjornPhaseTests.health(run, junkrat)
        before = hp.total
        credit = torb.stats.value(stats.HERO_DAMAGE)
        place(junkrat, (x, y, z + 8))
        for remaining in (86, 72, 58, 44, 30, 16, 2, 0):
            for _ in range(24):
                run.step({})
                if turret.shots > shots:
                    break
            shots += 1
            self.assertEqual(turret.shots, shots)
            self.assertEqual(turret.target, junkrat.body)
            self.assertEqual(trap.health, remaining)
            self.assertEqual(hp.total, before)
            self.assertEqual(torb.stats.value(stats.HERO_DAMAGE), credit)
        self.assertFalse(fight.steel_traps.devices)

        for _ in range(24):
            run.step({})
            if turret.shots > shots:
                break
        self.assertEqual(turret.shots, shots + 1)
        self.assertEqual(hp.total, before - 14)
        self.assertEqual(torb.stats.value(stats.HERO_DAMAGE), credit + 14)


@unittest.skipUnless(all(os.environ.get(key) for key in (
    "ORPHEUS_STEEL_TRAP_GRAPH_BUNDLE", "ORPHEUS_PROJECTILE_TAGS",
    "ORPHEUS_DVA_MECH_DATA", "ORPHEUS_DVA_MECH_MOVERS",
)), "needs trap, projectile tags and mech inputs")
class TorbjornSteelTrapGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with patch.dict(os.environ, {
            "ORPHEUS_TORBJORN_GRAPH_BUNDLE": os.environ["ORPHEUS_STEEL_TRAP_GRAPH_BUNDLE"],
        }):
            matrix_tests.TorbjornMatrixTests.setUpClass()
        cls.addClassCleanup(matrix_tests.TorbjornMatrixTests.doClassCleanups)

    def test_excluded_throw_crosses_live_deflect_grasp_and_matrix(self):
        trap_definition = 0x04000000000011A9
        rivet_definition = 0x0400000000002A66
        self.assertIn(projectile_defenses.EXCLUDED_TAG, projectile_defenses.tags()[trap_definition])
        self.assertTrue(projectile_defenses.eligible(rivet_definition))
        for hero_id, button, mode in ((0x02E0000000000029, 16, 0), (0x02E000000000023B, 8, 1),
                                      (projectile_defenses.MATRIX_HERO, 2, 2)):
            with self.subTest(hero=hero_id):
                self.exercise_front_gate(hero_id, button, mode, trap_definition, rivet_definition)

    def exercise_front_gate(self, hero_id, button, mode, trap_definition, rivet_definition):
        fixture = phase_tests.TorbjornPhaseTests()
        run, torb, junkrat, defender = fixture.arena("Junkrat", 4.7, 100)
        match = run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        defender.team = torb.team
        junkrat.team = 1 - torb.team
        hero = heroes()[hero_id]
        if mode == 2:
            hero = replace(hero, body=projectile_defenses.MATRIX_BODY)
        self.assertTrue(match.switch_hero(defender, hero))
        x, y, z = torb.mover.position
        defense_tests.TorbjornDefenseTests.place(defender, (x, y, z + 3.3))
        fixture.advance(run, 70)
        instances = {instance.graph.index: instance for instance in defender.body_script.component.instances.values()}
        if mode == 2:
            instances[0x0276].state(30).end(False)
        graph_index = projectile_defenses.DEFENSES[hero_id][0]
        instance = instances[graph_index]
        hold = button if mode == 2 else 0
        run.step({defender: (0, 0, button)})
        fixture.advance(run, 2, {defender: (0, 0, hold)})
        active = {state for state in instance.states.values()
                  if state.active and state.node.cls == "STUStatescriptStateDeflectProjectiles"}
        self.assertTrue(active)
        before = fixture.health(run, defender).total
        run.step({junkrat: (-32768, 0, 16), defender: (0, 0, hold)})

        crossed = False
        trap = None
        for _ in range(90):
            run.step({defender: (0, 0, hold)})
            trap = fight.steel_traps.devices.get(junkrat.body)
            if trap is None:
                continue
            if trap.armed_at is None:
                vx, vy, vz = trap.velocity
                velocity = (vx, vy - trap.gravity * combat.TICK, vz)
                speed = math.hypot(*velocity)
                direction = tuple(axis / speed for axis in velocity)
                args = (trap.position, direction, speed * combat.TICK, fight._enemies(junkrat))
                control = projectile_defenses.projectile_gate(*args, rivet_definition, collision=match.collision_world)
                self.assertIsNone(projectile_defenses.projectile_gate(
                    *args, trap_definition, collision=match.collision_world))
                if control is not None:
                    self.assertIs(control[1].owner, defender)
                    self.assertEqual(control[2], mode)
                    self.assertIn(control[4], active)
                    crossed = True
            if trap.victim is not None:
                break

        self.assertTrue(crossed)
        self.assertIsNotNone(trap)
        self.assertIs(trap.owner, junkrat)
        self.assertEqual(trap.victim, torb.body)
        self.assertEqual(fixture.health(run, torb).total, 175)
        self.assertEqual(fixture.health(run, defender).total, before)
        if mode == 1:
            absorbed = instance.vars.get(6584)
            if absorbed is not None:
                self.assertEqual(absorbed.value(), 0)
            fixture.advance(run, 150)
            self.assertNotIn(sigma_grasp.SHIELD_TAG, fixture.health(run, defender).temporary_shields)
