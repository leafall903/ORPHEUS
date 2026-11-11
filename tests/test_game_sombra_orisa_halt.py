"""Original Orisa 0CCB inputs against Sombra and the five shipped NPC bodies."""

import unittest
from dataclasses import replace

import test_game_sombra_grenade as grenade_fixture
import test_game_sombra_matrix as matrix_fixture
import test_game_sombra_npc_projected as projected_fixture
import test_game_sombra_orisa_fortify as fortify_fixture
from test_game_combat import aim, in_world
from test_game_mover import box

from ow174.game import combat, dva, fortify, orisa_halt, reinhardt_barrier, shot_guards
from ow174.game.collision import World
from ow174.game.content import heroes
from ow174.game.server_snapshot import SCHEMA, SCOPE, _Graph, snapshot_digest


def place(player, position):
    player.mover.position[:] = position
    player.mover.body = None


def pulse(fixture, caster, offset):
    fixture.run.step({caster: (0, 0, 2 if offset in (0, 25) else 0)})


class SombraHaltTests(unittest.TestCase):
    def arena(self):
        fixture = fortify_fixture.SombraOrisaFortifyTests().arena(distance=8)
        place(fixture.b, (0, 0, 0))
        place(fixture.a, (1.2, 0, 5))
        fixture.press(0, 10)
        return fixture

    def cast(self, fixture, caster):
        halt = fixture.run.match.combat.halt
        for offset in range(25):
            pulse(fixture, caster, offset)
        orb, = halt.orbs.values()
        self.assertIsNone(orb.requested)
        pulse(fixture, caster, 25)
        self.assertEqual(orb.requested, fixture.run.frame)
        return orb

    def test_original_repress_moves_without_damage_and_replays_without_commands(self):
        endpoints = []
        for gap in (False, True):
            with self.subTest(gap=gap):
                fixture = self.arena()
                match = fixture.run.match
                halt = match.combat.halt
                before = tuple(fixture.a.mover.position)
                orb = self.cast(fixture, fixture.b)
                stopped = orb.position
                fixture.run.step({})
                pull = halt.pulls[fixture.a.body]
                self.assertEqual(pull.script.graph.index, orisa_halt.CONTROL)
                self.assertEqual(tuple(fixture.a.mover.position), before)
                if gap:
                    fixture.run.frame += 89
                    match.update(fixture.run.frame * combat.TICK, fixture.run.frame)
                else:
                    for _ in range(89):
                        fixture.run.step({})
                x, _, z = fixture.a.mover.position
                endpoints.append((x, z))
                self.assertLess(x, before[0])
                self.assertGreater(z, before[2] + 1)
                self.assertEqual(orb.position, stopped)
                self.assertEqual(fixture.health(fixture.a), 200)
                self.assertFalse(halt.orbs)
                self.assertFalse(halt.pulls)
                fixture.run.step({})
                self.assertGreater(orb.source.state(2).remaining(), 0)
        self.assertEqual(*endpoints)

    def test_team_range_and_cover_reject_acquisition(self):
        for condition in ("team", "range", "cover"):
            with self.subTest(condition=condition):
                fixture = self.arena()
                match = fixture.run.match
                if condition == "team":
                    match.game_map = replace(match.game_map, free_for_all=False)
                    fixture.a.team = fixture.b.team = 0
                elif condition == "range":
                    place(fixture.a, (12, 0, 5))
                else:
                    place(fixture.a, (2.2, 0, 5))
                self.cast(fixture, fixture.b)
                if condition == "cover":
                    match.collision_world = World(
                        box(-40, 40, -1, 0, -40, 40)
                        + box(0.5, 0.7, 0, 6, 2, 11),
                    )
                before = tuple(fixture.a.mover.position)
                for _ in range(70):
                    fixture.run.step({})
                    self.assertNotIn(fixture.a.body, match.combat.halt.pulls)
                self.assertEqual(tuple(fixture.a.mover.position[::2]), before[::2])
                self.assertEqual(fixture.health(fixture.a), 200)

    def test_wall_detonates_before_second_input(self):
        fixture = self.arena()
        match = fixture.run.match
        halt = match.combat.halt
        match.collision_world = World(
            box(-40, 40, -1, 0, -40, 40) + box(-2, 2, 0, 5, 6.5, 6.8),
        )
        for offset in range(25):
            pulse(fixture, fixture.b, offset)
        orb, = halt.orbs.values()
        self.assertIsNotNone(orb.requested)
        self.assertAlmostEqual(orb.position[2], 6.499, places=3)
        self.assertIn(fixture.a.body, halt.pulls)
        self.assertEqual(fixture.health(fixture.a), 200)

    def test_actual_hack_and_emp_block_fresh_halt(self):
        for button in (2, 32):
            with self.subTest(button=button):
                fixture = self.arena()
                fight = fixture.run.match.combat
                if button == 32:
                    fixture.a.body_script.set_ult_charge(1400)
                for offset in range(90):
                    fixture.press(button if button == 2 or offset == 0 else 0)
                    if fixture.b.body in fight.hacks:
                        break
                effect, = fight.hacks[fixture.b.body]
                self.assertEqual(effect.application.source_entity(), fixture.a.body)
                for offset in range(45):
                    pulse(fixture, fixture.b, offset)
                self.assertFalse(fight.halt.orbs)
                self.assertEqual(fixture.health(fixture.a), 200)

    def test_projected_barrier_prevents_acquisition_and_releases_existing_pull(self):
        for late in (False, True):
            with self.subTest(late=late):
                fixture = self.arena()
                fight = fixture.run.match.combat
                fixture.run.match.game_map = replace(fixture.run.match.game_map, free_for_all=False)
                fixture.a.team, fixture.b.team = 0, 1
                zarya = grenade_fixture.SombraGrenadeTests().join_ally(
                    fixture, reinhardt_barrier.ZARYA, (8, 0, 5),
                )
                if late:
                    self.cast(fixture, fixture.b)
                    for _ in range(40):
                        fixture.run.step({})
                        if fight.halt.moving(fixture.a.body):
                            break
                    self.assertTrue(fight.halt.moving(fixture.a.body))
                x, y, z = fixture.a.mover.position
                fixture.run.step({zarya: (*aim(fight._eye(zarya), (x, y + 1, z)), 16)})
                bubble, = fight.barriers.values()
                self.assertIs(bubble.protected, fixture.a)
                if not late:
                    self.cast(fixture, fixture.b)
                before = tuple(fixture.a.mover.position[::2])
                for _ in range(45):
                    fixture.run.step({})
                    self.assertNotIn(fixture.a.body, fight.halt.pulls)
                self.assertEqual(tuple(fixture.a.mover.position[::2]), before)
                self.assertEqual(bubble.vitals.total, 200)
                self.assertEqual(fixture.health(fixture.a), 200)

    def test_actual_translocator_recall_releases_active_pull(self):
        fixture = self.arena()
        fixture.floor = [(-100, 0, -10, -100, 0, 100, 100, 0, 100),
                         (-100, 0, -10, 100, 0, 100, 100, 0, -10)]
        fixture.geometry()
        fight = fixture.run.match.combat
        for _ in range(20):
            fixture.run.step({fixture.a: (16384, 0, 16)})
        fixture.press(0, 180)
        beacon, = fight.translocators.entries.values()
        self.assertTrue(beacon.settled)
        self.cast(fixture, fixture.b)
        for _ in range(40):
            fixture.run.step({})
            if fight.halt.moving(fixture.a.body):
                break
        self.assertTrue(fight.halt.moving(fixture.a.body))
        fixture.press(16)
        fixture.press(0, 40)
        self.assertFalse(fight.translocators.entries)
        self.assertNotIn(fixture.a.body, fight.halt.pulls)
        self.assertAlmostEqual(fixture.a.mover.position[0], beacon.position[0], places=2)
        self.assertAlmostEqual(fixture.a.mover.position[2], beacon.position[2], places=2)
        self.assertEqual(fixture.health(fixture.a), 200)

    def test_actual_genji_reflection_and_sigma_absorption(self):
        for hero_id, button in ((shot_guards.GENJI, 16), (shot_guards.SIGMA, 8)):
            with self.subTest(hero=hero_id):
                fixture = self.arena()
                match = fixture.run.match
                match.game_map = replace(match.game_map, free_for_all=False)
                fixture.a.team = fixture.b.team = 0
                hero = heroes()[hero_id]
                defender = match.add_player(3, hero.name, hero_id, 1, False)
                in_world(match, defender)
                fixture.run.players.append(defender)
                self.assertTrue(match.switch_hero(defender, hero))
                defender.team = 1
                place(defender, (0, 0, 5))
                place(fixture.a, (1.5, 0, -2))
                match.collision_world = World(
                    box(-40, 40, -1, 0, -40, 40) + box(-4, 4, 0, 6, -4, -3.8),
                )
                for _ in range(30):
                    fixture.run.step({defender: (-32768, 0, 0)})
                before = tuple(fixture.a.mover.position)
                fixture.run.step({fixture.b: (0, 0, 2), defender: (-32768, 0, button)})
                seen_orb = reflected = pulled = False
                halt = match.combat.halt
                for _ in range(95):
                    fixture.run.step({defender: (-32768, 0, 0)})
                    seen_orb |= bool(halt.orbs)
                    for orb in halt.orbs.values():
                        if orb.shooter is defender:
                            self.assertEqual(orb.team, defender.team)
                            self.assertEqual(orb.shooter_body, defender.body)
                            reflected = True
                    pulled |= fixture.a.body in halt.pulls
                    self.assertNotIn(defender.body, halt.pulls)
                self.assertTrue(seen_orb)
                self.assertEqual(reflected, hero_id == shot_guards.GENJI)
                self.assertEqual(pulled, reflected)
                if reflected:
                    self.assertLess(fixture.a.mover.position[2], before[2] - 0.5)
                else:
                    self.assertEqual(tuple(fixture.a.mover.position), before)
                self.assertFalse(halt.orbs)
                self.assertEqual(fixture.health(fixture.a), 200)

    def test_displacement_preserves_camo_and_does_not_interrupt_hack_or_paid_emp(self):
        for mode in ("camo", "hack", "emp"):
            with self.subTest(mode=mode):
                fixture = self.arena()
                fight = fixture.run.match.combat
                if mode == "camo":
                    fixture.press(8)
                    fixture.press(0, 60)
                    camo = fixture.camo().state(13)
                    self.assertTrue(camo.active)
                elif mode == "emp":
                    fixture.a.body_script.set_ult_charge(1400)
                self.cast(fixture, fixture.b)
                moved = False
                for _ in range(45):
                    fixture.press(2 if mode == "hack" else 0)
                    if fight.halt.moving(fixture.a.body):
                        moved = True
                        if mode == "hack":
                            self.assertTrue(fixture.weapon().state(53).active)
                        elif mode == "emp":
                            fixture.press(32)
                            break
                self.assertTrue(moved)
                self.assertEqual(fixture.health(fixture.a), 200)
                if mode == "camo":
                    self.assertTrue(camo.active)
                else:
                    fixture.press(2 if mode == "hack" else 0, 65)
                    effect, = fight.hacks[fixture.b.body]
                    self.assertEqual(effect.application.source_entity(), fixture.a.body)

    def test_original_post_pull_slow_survives_caster_departure_and_expires(self):
        fixture = self.arena()
        match = fixture.run.match
        halt = match.combat.halt
        self.cast(fixture, fixture.b)
        modifier = fixture.a.body_script.component.entity_var(23186)
        for _ in range(45):
            fixture.run.step({})
            if modifier.value():
                break
        self.assertAlmostEqual(modifier.value(), 0.65)
        pull = halt.pulls[fixture.a.body]
        wait = pull.script.states[pull.script.graph.node(5).state]
        match.leave(fixture.b)
        fixture.run.players.remove(fixture.b)
        fixture.run.step({})
        self.assertIs(halt.pulls[fixture.a.body], pull)
        self.assertAlmostEqual(fixture.a.mover.values.run_forward, 1.925, places=3)
        for _ in range(55):
            fixture.run.step({})
        self.assertEqual(wait.ended_at - wait.start, 650)
        self.assertFalse(modifier.value())
        self.assertNotIn(fixture.a.body, halt.pulls)
        self.assertAlmostEqual(fixture.a.mover.values.run_forward, 5.5)

    def test_pending_orb_and_pull_are_causal_state_and_replacement_retires_target(self):
        fixture = self.arena()
        match = fixture.run.match
        halt = match.combat.halt
        orb = self.cast(fixture, fixture.b)
        fixture.run.step({})
        old = fixture.a.body
        pull = halt.pulls[old]

        def digest():
            boundaries = {
                "combat": match.combat,
                "caster": fixture.b,
                "target": fixture.a,
                "caster_component": orb.source.component,
                "target_component": pull.script.component,
            }
            store = _Graph(boundaries, None)
            roots = {"halt": store.value(halt)}
            return snapshot_digest({
                "schema": SCHEMA, "scope": SCOPE, "restorable": False,
                "roots": roots, "nodes": store.finish(), "assets": [],
            })

        before = digest()
        self.assertEqual(digest(), before)
        orb.requested += 1
        self.assertNotEqual(digest(), before)
        orb.requested -= 1
        self.assertTrue(match.switch_hero(fixture.a, fixture.a.hero))
        self.assertTrue(pull.script.stopped)
        self.assertNotIn(old, halt.pulls)
        for _ in range(70):
            fixture.run.step({})
        self.assertNotIn(fixture.a.body, halt.pulls)
        self.assertEqual(fixture.health(fixture.a), 200)


class NpcHaltTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        projected_fixture.SombraNpcProjectedTests.setUpClass.__func__(cls)

    @classmethod
    def restore_graphs(cls, path):
        projected_fixture.SombraNpcProjectedTests.restore_graphs(path)

    def test_all_five_actual_npcs_resolve_displacement_and_immunity_without_damage(self):
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                fixture, bot = projected_fixture.SombraNpcProjectedTests.arena(self, key)
                fight = fixture.run.match.combat
                self.assertTrue(fixture.run.match.switch_hero(
                    fixture.a, projected_fixture.heroes()[fortify.HERO],
                ))
                place(fixture.a, (0, 0, 0))
                bot.position = (1.2, 0, 5)
                fixture.press(0, 25)
                before = bot.position
                health = fight.vitals[bot.entity].total
                SombraHaltTests().cast(fixture, fixture.a)
                for _ in range(89):
                    fixture.run.step({})
                if definition.name == "Heavy Assault":
                    self.assertEqual(bot.position, before)
                else:
                    self.assertLess(bot.position[0], before[0])
                    self.assertGreater(bot.position[2], before[2] + 1)
                self.assertEqual(fight.vitals[bot.entity].total, health)
                self.assertNotIn(bot.entity, fight.halt.pulls)


class OwnedMechHaltTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        matrix_fixture.SombraMatrixTests.setUpClass()

    def test_active_defense_matrix_consumes_halt_before_its_repress(self):
        fixture, _ = matrix_fixture.SombraMatrixTests().pilot_arena()
        match = fixture.run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        fixture.a.team, fixture.b.team = 0, 1
        fixture.floor = [(-12, 0, -40, -12, 0, 40, 12, 0, 40),
                         (-12, 0, -40, 12, 0, 40, 12, 0, -40)]
        fixture.geometry()
        pilot = fixture.b.body_script
        pilot.set_ult_charge(pilot.component.entity_var(338).value())
        fixture.run.step({fixture.b: (0, 0, 32)})
        fixture.press(0, 120)
        mech = dva.controlled_mech(fixture.b)
        self.assertIsNotNone(mech)
        x, y, z = mech.position
        caster = grenade_fixture.SombraGrenadeTests().join_ally(fixture, fortify.HERO, (x, y, z - 5))
        held = {fixture.b: (32768, 0, 2)}
        fixture.run.step(held)
        self.assertIsNotNone(shot_guards.active_guard(mech, fight.tick * combat.TICK * 1000))
        fixture.run.step({**held, caster: (0, 0, 2)})
        seen_orb = False
        for _ in range(60):
            fixture.run.step(held)
            seen_orb |= bool(fight.halt.orbs)
            self.assertFalse(fight.halt.pulls)
        self.assertTrue(seen_orb)
        self.assertFalse(fight.halt.orbs)
        self.assertEqual(fight.vitals[mech.entity].total, 600)
        self.assertEqual(mech.position, (x, y, z))
