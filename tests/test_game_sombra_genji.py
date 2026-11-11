"""Actual Genji shuriken, Swift Strike and Dragonblade against Sombra."""

import math
import unittest
from dataclasses import replace

import test_game_sombra_combat as scenes
import test_game_sombra_grenade as grenades
import test_game_sombra_npc as npc_fixture
from test_game_combat import aim
from test_game_sombra_orisa_halt import place
from test_game_sombra_reaper_blossom import charge

from ow174.game import combat, reinhardt_barrier, shot_guards
from ow174.game import genji_combat as genji
from ow174.game.commands import FIRE, JUMP, Command
from ow174.game.content import SOLDIER, heroes
from ow174.game.script.driver import ULT_COST
from ow174.game.server_snapshot import SCHEMA, SCOPE, _Graph, snapshot_digest
from ow174.game.world import HEALTH_POOL


def prepare(fixture, distance=4):
    match = fixture.run.match
    fixture.b.spawned = True
    assert match.switch_hero(fixture.b, heroes()[genji.HERO])
    fixture.floor = [(-15, 0, -32, -15, 0, 110, 15, 0, 110),
                     (-15, 0, -32, 15, 0, 110, 15, 0, -32)]
    fixture.geometry()
    place(fixture.a, (0, 0, 0))
    place(fixture.b, (0, 0, distance))
    for actor in (fixture.a, fixture.b):
        actor.body_script.ult_seconds = 0
        actor.body_script.set_ult_charge(0)
    fixture.press(0, 50)
    return fixture


def step(fixture, angle=(-32768, 0), button=0, frames=1):
    for _ in range(frames):
        fixture.run.step({fixture.b: (*angle, button)})


def root(fixture, graph):
    return next(i for i in fixture.b.body_script.component.instances.values() if i.graph.index == graph)


def hits(fixture):
    return [row["+0x78"]["+0x4"] for row in fixture.b.client.messages(combat.HIT)]


def motion(fixture, *, angle=(-32768, 0), buttons=0, forward=0):
    run = fixture.run
    run.frame += 1
    match = run.match
    match.update(run.frame * combat.TICK, run.frame)
    for player in run.players:
        player.client.queue_entities(match.remote_movements(player))
        if player is fixture.b:
            command = Command(run.frame, yaw=angle[0], pitch=angle[1], buttons=buttons, forward=forward)
        else:
            command = Command(run.frame)
        player.take_commands([command])


def draw(test, fixture, angle=(-32768, 0), *, seed=True):
    script = fixture.b.body_script
    test.assertEqual(script.component.entity_var(ULT_COST).value(), 1932)
    if seed:
        script.set_ult_charge(1932)
    step(fixture, angle, 32)
    test.assertEqual(charge(fixture.b), 0)
    step(fixture, angle, frames=76)
    return root(fixture, genji.BLADE).state(3)


class SombraGenjiTests(unittest.TestCase):
    def arena(self, distance=4):
        fixture = scenes.SombraCombatTests()
        fixture.setUp()
        return prepare(fixture, distance)

    def test_actual_primary_burst_and_three_fan_angles_deal_87(self):
        for button in (1, 2):
            with self.subTest(button=button):
                fixture = self.arena()
                fight = fixture.run.match.combat
                face = aim(fight._eye(fixture.b), (0, 1, 0))
                step(fixture, face, button)
                if button == 2:
                    self.assertEqual(len(fight.rockets), 3)
                    left, centre, right = [r.direction for r in fight.rockets]
                    self.assertAlmostEqual(math.dist(left, centre), math.dist(centre, right), places=6)
                    self.assertNotEqual(left, right)
                    self.assertTrue(all(r.definition == genji.SHURIKEN for r in fight.rockets))
                step(fixture, face, frames=32)
                self.assertEqual(hits(fixture), [29, 29, 29])
                self.assertEqual(fixture.health(fixture.a), 113)
                self.assertEqual(charge(fixture.b), 87)

    def test_fan_critical_and_rear_projected_barrier(self):
        fixture = self.arena(distance=1.5)
        fight = fixture.run.match.combat
        face = aim(fight._eye(fixture.b), (0, 1.56, 0))
        step(fixture, face, 2)
        step(fixture, face, frames=9)
        self.assertGreaterEqual(max(hits(fixture)), 58)
        fixture = self.arena()
        match = fixture.run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        fixture.a.team, fixture.b.team = 0, 1
        zarya = grenades.SombraGrenadeTests().join_ally(fixture, reinhardt_barrier.ZARYA, (8, 0, 0))
        face = aim(fight._eye(zarya), (0, 1, 0))
        fixture.run.step({zarya: (*face, 16)})
        bubble, = fight.barriers.values()
        face = aim(fight._eye(fixture.b), (0, 1, 0))
        step(fixture, face, 2)
        step(fixture, face, frames=15)
        self.assertEqual(bubble.vitals.total, 113)
        self.assertEqual(fixture.health(fixture.a), 200)
        self.assertEqual(charge(fixture.b), 0)

    def test_actual_strike_moves_15_and_hits_once_then_refuses_cooldown(self):
        fixture = self.arena()
        fight = fixture.run.match.combat
        step(fixture, button=8)
        move = root(fixture, genji.STRIKE).state(1)
        point, speed = move.move_request()
        self.assertAlmostEqual(math.dist(tuple(point), tuple(fixture.b.mover.position)), 15)
        self.assertEqual(speed, 50)
        step(fixture, frames=24)
        self.assertAlmostEqual(fixture.b.mover.position[2], -11, places=2)
        self.assertEqual(hits(fixture), [50])
        self.assertEqual(charge(fixture.b), 50)
        counter = move.counter
        step(fixture, button=8, frames=12)
        self.assertEqual(move.counter, counter)
        step(fixture, frames=520)
        step(fixture, (0, 0), 8)
        self.assertGreater(move.counter, counter)
        self.assertIsNotNone(move.move_request())
        self.assertIn(fixture.b.body, fight.genji.dashes)

    def test_actual_strike_refuses_cover_team_and_rear(self):
        for gate in ("wall", "team", "rear"):
            with self.subTest(gate=gate):
                fixture = self.arena()
                match = fixture.run.match
                if gate == "wall":
                    fixture.geometry((-3, 0, 2, 3, 0, 2, 3, 4, 2),
                                     (-3, 0, 2, 3, 4, 2, -3, 4, 2))
                elif gate == "team":
                    match.game_map = replace(match.game_map, free_for_all=False)
                    fixture.a.team = fixture.b.team
                angle = (0, 0) if gate == "rear" else (-32768, 0)
                step(fixture, angle, 8)
                step(fixture, angle, frames=28)
                self.assertEqual(fixture.health(fixture.a), 200)
                self.assertFalse(hits(fixture))
                if gate == "wall":
                    self.assertGreater(fixture.b.mover.position[2], 2)

    def test_actual_strike_elimination_resets_cooldown_and_genji_can_dash_again(self):
        fixture = self.arena()
        fight = fixture.run.match.combat
        fixture.health(fixture.a)
        fight.vitals[fixture.a.body].now[HEALTH_POOL] = 45
        step(fixture, button=8)
        step(fixture, frames=27)
        self.assertTrue(fight.is_dead(fixture.a))
        self.assertEqual(fight.vitals[fixture.a.body].killer, fixture.b.body)
        self.assertEqual(charge(fixture.b), 45)
        move = root(fixture, genji.STRIKE).state(1)
        count = move.counter
        step(fixture, (0, 0), 8)
        self.assertGreater(move.counter, count)

    def test_recent_actual_strike_contribution_resets_on_sombra_final_blow(self):
        fixture = self.arena()
        fight = fixture.run.match.combat
        match = fixture.run.match
        match.game_map = replace(match.game_map, free_for_all=False)
        fixture.a.team = fixture.b.team = 0
        place(fixture.a, (4, 0, 0))
        enemy = grenades.SombraGrenadeTests().join_ally(fixture, SOLDIER, (0, 0, 0))
        enemy.team = 1
        fixture.health(enemy)
        fight.vitals[enemy.body].now[HEALTH_POOL] = 58
        step(fixture, button=8)
        step(fixture, frames=26)
        self.assertEqual(fixture.health(enemy), 8)
        move = root(fixture, genji.STRIKE).state(1)
        count = move.counter
        fixture.press(FIRE, target=enemy)
        self.assertTrue(fight.is_dead(enemy))
        self.assertEqual(fight.vitals[enemy.body].killer, fixture.a.body)
        step(fixture, (0, 0), 8)
        self.assertGreater(move.counter, count)

    def test_actual_emp_interrupts_dash_in_motion_without_resuming(self):
        fixture = self.arena()
        fight = fixture.run.match.combat
        fixture.a.body_script.set_ult_charge(1400)
        fixture.press(32)
        fixture.press(0, 5)
        step(fixture, (0, 0), 8)
        move = root(fixture, genji.STRIKE).state(1)
        self.assertTrue(move.active)
        for _ in range(30):
            step(fixture, (0, 0))
            if fixture.b.body in fight.hacks:
                break
        effect, = fight.hacks[fixture.b.body]
        self.assertGreater(fixture.b.mover.position[2], 4)
        self.assertLess(fixture.b.mover.position[2], 19)
        self.assertFalse(move.active)
        stop = tuple(fixture.b.mover.position)
        step(fixture, (0, 0), frames=effect.until_tick - fixture.run.frame + 10)
        self.assertEqual(tuple(fixture.b.mover.position), stop)

    def test_actual_blade_cost_single_swing_timer_and_primary_returns(self):
        fixture = self.arena()
        fixture.b.body_script.set_ult_charge(1931)
        step(fixture, button=32)
        step(fixture, frames=5)
        self.assertEqual(charge(fixture.b), 1931)
        ultimate = draw(self, fixture)
        self.assertTrue(ultimate.flags & ultimate.RUNNING)
        step(fixture, button=FIRE)
        step(fixture, frames=42)
        self.assertEqual(hits(fixture), [110])
        self.assertEqual(fixture.health(fixture.a), 90)
        self.assertEqual(charge(fixture.b), 0)
        step(fixture, frames=410)
        self.assertFalse(ultimate.flags & ultimate.RUNNING)
        face = aim(fixture.run.match.combat._eye(fixture.b), (0, 1, 0))
        step(fixture, face, FIRE)
        step(fixture, face, frames=32)
        self.assertEqual(hits(fixture)[-3:], [29, 29, 29])
        self.assertEqual(fixture.health(fixture.a), 3)

    def test_actual_blade_refuses_cover_range_and_team(self):
        for gate in ("wall", "range", "team"):
            with self.subTest(gate=gate):
                fixture = self.arena(distance=7 if gate == "range" else 4)
                match = fixture.run.match
                if gate == "wall":
                    fixture.geometry((-3, 0, 2, 3, 0, 2, 3, 4, 2),
                                     (-3, 0, 2, 3, 4, 2, -3, 4, 2))
                elif gate == "team":
                    match.game_map = replace(match.game_map, free_for_all=False)
                    fixture.a.team = fixture.b.team
                draw(self, fixture)
                step(fixture, button=1)
                step(fixture, frames=45)
                self.assertEqual(fixture.health(fixture.a), 200)
                self.assertEqual(charge(fixture.b), 0)

    def test_actual_hack_denies_fresh_dash_and_blade_but_leaves_shuriken(self):
        fixture = self.arena()
        fight = fixture.run.match.combat
        for _ in range(70):
            fixture.press(2)
            if fixture.b.body in fight.hacks:
                break
        self.assertIn(fixture.b.body, fight.hacks)
        fixture.b.body_script.set_ult_charge(1932)
        step(fixture, button=8 | 32)
        step(fixture, frames=16)
        self.assertEqual(charge(fixture.b), 1932)
        self.assertEqual(tuple(fixture.b.mover.position), (0, 0, 4))
        step(fixture, aim(fight._eye(fixture.b), (0, 1, 0)), 2)
        step(fixture, frames=20)
        self.assertEqual(fixture.health(fixture.a), 113)

    def test_actual_hack_preserves_drawn_blade_and_blade_can_still_hit(self):
        fixture = self.arena()
        fight = fixture.run.match.combat
        ultimate = draw(self, fixture)
        for _ in range(70):
            fixture.press(2)
            if fixture.b.body in fight.hacks:
                break
        self.assertIn(fixture.b.body, fight.hacks)
        self.assertTrue(ultimate.flags & ultimate.RUNNING)
        step(fixture, button=FIRE)
        step(fixture, frames=30)
        self.assertEqual(fixture.health(fixture.a), 90)
        self.assertEqual(charge(fixture.b), 0)

    def test_actual_sword_deflect_blocks_front_and_does_not_return_damage(self):
        fixture = self.arena()
        fight = fixture.run.match.combat
        match = fixture.run.match
        match.game_map = replace(match.game_map, free_for_all=False)
        fixture.a.team, fixture.b.team = 0, 1
        place(fixture.a, (0, 0, 6))
        defender = grenades.SombraGrenadeTests().join_ally(fixture, genji.HERO, (0, 0, 1))
        draw(self, fixture)
        fixture.run.step({defender: (0, 0, 16)})
        fixture.press(0, 4)
        self.assertIsNotNone(shot_guards.active_guard(defender, fight.tick * 16))
        step(fixture, button=1)
        step(fixture, frames=38)
        self.assertEqual(fixture.health(defender), 200)
        self.assertEqual(fixture.health(fixture.b), 200)
        self.assertFalse(hits(fixture))

    def test_actual_sigma_grasp_absorbs_fan_but_blade_reaches_sigma(self):
        for blade in (False, True):
            with self.subTest(blade=blade):
                fixture = self.arena()
                fight = fixture.run.match.combat
                match = fixture.run.match
                match.game_map = replace(match.game_map, free_for_all=False)
                fixture.a.team, fixture.b.team = 0, 1
                place(fixture.a, (8, 0, 0))
                sigma = grenades.SombraGrenadeTests().join_ally(
                    fixture, shot_guards.SIGMA, (0, 0, 1),
                )
                if blade:
                    draw(self, fixture)
                fixture.run.step({sigma: (0, 0, 8)})
                fixture.press(0, 4)
                guard = shot_guards.active_guard(sigma, fight.tick * 16)
                self.assertIsNotNone(guard)
                facing = aim(fight._eye(fixture.b), (0, 1, 1))
                step(fixture, facing, 1 if blade else 2)
                step(fixture, facing, frames=32)
                self.assertEqual(fixture.health(sigma), 290 if blade else 400)
                self.assertEqual(guard.instance.vars[21312].value(), 0 if blade else 87)

    def test_flashbang_pauses_sword_input_without_ending_drawn_weapon(self):
        fixture = self.arena()
        fight = fixture.run.match.combat
        match = fixture.run.match
        match.game_map = replace(match.game_map, free_for_all=False)
        fixture.a.team, fixture.b.team = 0, 1
        cassidy = grenades.SombraGrenadeTests().join_ally(fixture, 0x02E0000000000042, (2, 0, 3))
        ultimate = draw(self, fixture)
        face = aim(fight._eye(cassidy), (0, 1, 4))
        fixture.run.step({cassidy: (*face, 16)})
        for _ in range(44):
            if fight.is_stunned(fixture.b):
                break
            fixture.press(0)
        self.assertTrue(fight.is_stunned(fixture.b))
        self.assertTrue(ultimate.flags & ultimate.RUNNING)
        step(fixture, button=1, frames=20)
        self.assertEqual(fixture.health(fixture.a), 200)
        step(fixture, frames=50)
        step(fixture, button=1)
        step(fixture, frames=33)
        self.assertEqual(fixture.health(fixture.a), 90)

    def test_pending_dash_victims_are_causal_snapshot_state(self):
        fixture = self.arena()
        fight = fixture.run.match.combat
        step(fixture, button=8)
        record = fight.genji.dashes[fixture.b.body]

        def digest():
            graph = _Graph({"fight": fight}, None)
            roots = {"genji": graph.value(fight.genji)}
            return snapshot_digest({"schema": SCHEMA, "scope": SCOPE, "restorable": False,
                                    "roots": roots, "nodes": graph.finish(), "assets": []})

        before = digest()
        record.victims.add(fixture.a.body)
        self.assertNotEqual(before, digest())
        record.victims.clear()
        self.assertEqual(before, digest())
        self.assertTrue(fixture.run.match.switch_hero(fixture.b, heroes()[SOLDIER]))
        self.assertFalse(fight.genji.dashes)

    def test_double_jump_remains_available_during_sombra_hack(self):
        fixture = self.arena()
        fight = fixture.run.match.combat
        for _ in range(70):
            fixture.press(2)
            if fixture.b.body in fight.hacks:
                break
        self.assertIn(fixture.b.body, fight.hacks)
        for index in range(8):
            motion(fixture, buttons=JUMP if index == 0 else 0)
        self.assertTrue(fixture.b.mover.airborne)
        before = fixture.b.mover.position[1]
        motion(fixture, buttons=JUMP)
        self.assertTrue(fixture.b.genji_mobility.air_jump_used)
        self.assertGreater(fixture.b.mover.position[1], before)
        self.assertGreater(fixture.b.mover.velocity[1], 5)
        for _ in range(75):
            motion(fixture, buttons=JUMP)
        self.assertFalse(fixture.b.genji_mobility.air_jump_used)

    def test_actual_climb_opens_shuriken_line_over_low_cover(self):
        fixture = self.arena()
        fight = fixture.run.match.combat
        fixture.geometry((-3, 0, 3.4, 3, 0, 3.4, 3, 2.6, 3.4),
                         (-3, 0, 3.4, 3, 2.6, 3.4, -3, 2.6, 3.4))
        face = aim(fight._eye(fixture.b), (0, 1, 0))
        step(fixture, face, FIRE)
        step(fixture, face, frames=48)
        self.assertEqual(fixture.health(fixture.a), 200)
        for _ in range(25):
            motion(fixture, buttons=JUMP, forward=127)
            if fixture.b.genji_mobility.climbing and fixture.b.mover.position[1] >= 1.55:
                break
        self.assertTrue(fixture.b.genji_mobility.climbing)
        self.assertGreaterEqual(fixture.b.mover.position[1], 1.55)
        for index in range(28):
            face = aim(fight._eye(fixture.b), (0, 1, 0))
            motion(fixture, angle=face, buttons=JUMP | (FIRE if index == 0 else 0), forward=127)
        self.assertLess(fixture.health(fixture.a), 200)


class NpcGenjiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        npc_fixture.SombraActualNpcTests.setUpClass.__func__(cls)

    @classmethod
    def restore_graphs(cls, path):
        npc_fixture.SombraActualNpcTests.restore_graphs(path)

    def arena(self, key):
        fixture, bot = npc_fixture.SombraActualNpcTests.make(self, key, distance=4)
        prepare(fixture)
        bot.position = (0, 0, 1)
        place(fixture.a, (7, 0, 0))
        fixture.press(0, 5)
        return fixture, bot

    def test_actual_primary_fan_strike_and_blade_hit_all_five_npc_classes(self):
        for key, definition in self.catalog.items():
            for mode in ("primary", "fan", "strike", "blade"):
                with self.subTest(npc=definition.name, mode=mode):
                    fixture, bot = self.arena(key)
                    fight = fixture.run.match.combat
                    vitals = fight.vitals[bot.entity]
                    before = vitals.total
                    angle = aim(fight._eye(fixture.b), (0, .95, 1))
                    if mode == "blade":
                        draw(self, fixture)
                        step(fixture, button=1)
                    elif mode == "strike":
                        step(fixture, button=8)
                    else:
                        step(fixture, angle, 1 if mode == "primary" else 2)
                    step(fixture, angle if mode in ("primary", "fan") else (-32768, 0), frames=34)
                    self.assertLess(vitals.total, before)
                    expected = 0 if mode == "blade" else before - vitals.total
                    self.assertAlmostEqual(charge(fixture.b), expected, places=4)

    def test_actual_primary_earns_and_spends_1932_against_unmodified_heavy(self):
        fixture, bot = self.arena("5")
        fight = fixture.run.match.combat
        vitals = fight.vitals[bot.entity]
        self.assertEqual(vitals.total, 4000)
        angle = aim(fight._eye(fixture.b), (0, 1.15, 1))
        for _ in range(2000):
            step(fixture, angle, FIRE)
            if charge(fixture.b) == 1932:
                break
        self.assertEqual(charge(fixture.b), 1932)
        self.assertLessEqual(vitals.total, 2068)
        step(fixture, angle)
        draw(self, fixture, seed=False)
        before = vitals.total
        step(fixture, button=1)
        step(fixture, frames=30)
        self.assertLess(vitals.total, before)
        self.assertEqual(charge(fixture.b), 0)
