"""Matrix combat reconstruction using authored0407 and external projectile tags."""

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import test_game_dva_ana as ana_tests
import test_game_dva_manual_npcs as manual_tests
import test_game_dva_mech as mech_tests
from test_game_combat import aim

from ow174.game import body_entities, bots, combat, deflection, npc, npc_control, projectiles
from ow174.game.collision import World
from ow174.game.combat import HitDef, Rocket, Target, Vitals
from ow174.game.commands import Command
from ow174.game.content import find_hero, heroes
from ow174.game.script.driver import ULT_CHARGE, ULT_COST
from tools.extract_projectile_tags import entity_tags

RIFLE = 0x04000000000003D4
HELIX = 0x040000000000072C
FROST = 0x04000000000008BE


class InputTests(unittest.TestCase):
    def test_tag_catalog_rejects_wrong_build_alias_and_wrong_tag_type(self):
        data = {"version": 1, "build": deflection.BUILD, "entities": {"0x04000000000003D4": []}}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "tags.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            self.assertEqual(deflection.read_tags(path), {RIFLE: frozenset()})
            for field, value in (("build", "other"), ("entities", {"03D4": []}),
                                 ("entities", {"0x04000000000003D4": ["0x0400000000000145"]})):
                bad = {**data, field: value}
                path.write_text(json.dumps(bad), encoding="utf-8")
                with self.assertRaises(ValueError):
                    deflection.read_tags(path)

    def test_extractor_resolves_shared_array_and_ignores_explicit_null_tag(self):
        xml = '''<STUEntityDefinition xmlns:d="https://yretenai.com/dragonml/v1">
          <d:map d:name="m_componentMap">
          <STUTargetTagComponent><d:ref d:name="m_targetTags" d:ref="tags"/></STUTargetTagComponent>
          </d:map>
          <d:array d:id="tags"><d:ref GUID="000000000145.06C"/>
          <d:ref GUID="000000000000.001"/></d:array>
          <child><STUTargetTagComponent><d:array d:name="m_targetTags">
          <d:ref GUID="0000000002F9.06C"/></d:array></STUTargetTagComponent></child>
          </STUEntityDefinition>'''
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "entity.xml"
            path.write_text(xml, encoding="utf-8")
            self.assertEqual(entity_tags(path), ["0x0D60000000000145"])

    def test_unknown_and_excluded_tags_do_not_absorb(self):
        with patch.object(deflection, "tags", return_value={RIFLE: frozenset(),
                                                           FROST: {deflection.EXCLUDED_TAG}}):
            self.assertTrue(deflection.absorbable(RIFLE))
            self.assertFalse(deflection.absorbable(FROST))
            self.assertFalse(deflection.absorbable(None))
            self.assertFalse(deflection.absorbable(123))

    def test_box_sweep_parallel_inside_outside_and_finite_reach(self):
        low, high = (-1, -1, 1), (1, 1, 10)
        self.assertEqual(deflection.ray_box((0, 0, 15), (0, 0, -1), low, high, 20), 5)
        self.assertEqual(deflection.ray_box((0, 0, 5), (0, 0, -1), low, high, 20), 0)
        self.assertIsNone(deflection.ray_box((2, 0, 15), (0, 0, -1), low, high, 20))
        self.assertIsNone(deflection.ray_box((0, 0, 15), (0, 0, 1), low, high, 20))
        self.assertIsNone(deflection.ray_box((0, 0, 15), (0, 0, -1), low, high, 4))

    def test_box_sweep_parallel_threshold_depends_on_segment_displacement(self):
        lower, upper = (-0.001, -1, -1), (0.001, 1, 1)
        origin = (-0.00100003, 0, 0)
        direction = (1e-7, 0, 1)
        self.assertIsNone(deflection.ray_box(origin, direction, lower, upper, 0.5))
        self.assertAlmostEqual(deflection.ray_box(origin, direction, lower, upper, 2), 0.3)


class MatrixCombatTests(unittest.TestCase):
    scenario = mech_tests.AuthoredMechTests.scenario
    step = mech_tests.AuthoredMechTests.step
    matrix = mech_tests.AuthoredMechTests.matrix

    @classmethod
    def setUpClass(cls):
        mech_tests.AuthoredMechTests.setUpClass()
        if any(key not in deflection.tags() for key in (RIFLE, HELIX, FROST)):
            raise unittest.SkipTest("requires external003 projectile tags; Matrix combat acceptance OPEN")

    def prepared(self, enabled=True, distance=15):
        run, mech, enemy, _ = self.scenario()
        x, y, _ = enemy.mover.position
        enemy.mover.position = [x, y, distance]
        enemy.mover.body = None  # external fixture placement must also rebind the cached physics body
        self.step(run, mech, 2, 2 if enabled else 0)
        self.assertAlmostEqual(enemy.mover.position[2], distance, delta=0.01)
        target = next(t for t in run.match.combat._targets() if t.owner is mech)
        return run, mech, enemy, run.match.combat.vitals_of(target)

    def test_authored_rifle_is_absorbed_but_release_reopens_damage(self):
        run, mech, enemy, vitals = self.prepared()
        for _ in range(40):
            run.step({mech: (0, 0, 2), enemy: (-32768, 0, 1)})
        self.assertEqual(vitals.total, 600)
        self.assertEqual(enemy.client.messages(25001), [])
        for _ in range(20):
            run.step({enemy: (-32768, 0, 1)})
        self.assertLess(vitals.total, 600)

    def genji_fixture(self, *, pilot_form=False, distance=4):
        from test_game_combat import dva_pilot_input
        from test_game_dva_created_body import CreatedBodyTests

        from ow174.game.content import find_hero

        run, pilot, genji, _, mech = CreatedBodyTests().scenario()
        match = run.match
        self.assertTrue(match.switch_hero(genji, find_hero("genji")))
        if pilot_form:
            dva_pilot_input(pilot)
            pilot.mover.position[0] += 10
            pilot.mover.body = None
        x, y, z = pilot.mover.position if pilot_form else mech.position
        genji.mover.position = [x, y, z + distance]
        genji.mover.body = None
        for _ in range(100):
            run.step({})
        fight = match.combat
        graph = next(i for i in genji.body_script.component.instances.values() if i.graph.index == 0x3A3)
        for _ in range(3):
            run.step({genji: (-32768, 0, 16)})
        self.assertTrue(graph.state(10).active)
        return run, pilot, genji, fight.vitals_of(fight.player_target(pilot)), fight.vitals_of(
            fight.player_target(genji))

    def test_light_gun_radius_reflects_only_inside_expanded_genji_edge(self):
        self.enterContext(patch.object(body_entities, "ULT_SECONDS", 0))
        for offset, reflects in ((0.9, True), (1.05, False)):
            with self.subTest(offset=offset):
                run, pilot, genji, pilot_health, genji_health = self.genji_fixture(pilot_form=True)
                pilot.body_script.ult_seconds = 0
                pilot.mover.position[0] = genji.mover.position[0] + offset
                pilot.mover.body = None
                fight = run.match.combat
                run.step({genji: (-32768, 0, 16), pilot: (0, 0, 1)})
                shot, = fight.rockets
                self.assertEqual(shot.collision_radius, 0.25)
                for _ in range(14):
                    run.step({genji: (-32768, 0, 16), pilot: (0, 0, 0)})
                    if shot.shooter is genji:
                        break
                self.assertIs(shot.shooter, genji if reflects else pilot)
                self.assertEqual(genji_health.total, 200)
                self.assertEqual(pilot_health.total, 150)

    def test_genji_deflect_returns_controlled_mech_fusion_and_missiles(self):
        self.enterContext(patch.object(body_entities, "ULT_SECONDS", 0))
        for buttons in (1, 16):
            with self.subTest(buttons=buttons):
                run, pilot, genji, mech_health, genji_health = self.genji_fixture()
                charge = pilot.controlled_mech.body_script.component.entity_var(ULT_CHARGE)
                initial_charge = charge.value()
                run.step({genji: (-32768, 0, 16), pilot: (0, 0, buttons)})
                for _ in range(45):
                    run.step({genji: (-32768, 0, 16)})
                self.assertEqual(genji_health.total, 200)
                self.assertLess(mech_health.total, 600)
                self.assertTrue(genji.client.messages(25001))
                self.assertEqual(charge.value(), initial_charge)
                self.assertFalse(pilot.client.messages(25001))

    def test_genji_deflect_returns_pistol_and_matrix_absorbs_reflected_missiles(self):
        self.enterContext(patch.object(body_entities, "ULT_SECONDS", 0))
        run, pilot, genji, pilot_health, genji_health = self.genji_fixture(pilot_form=True)
        pilot.body_script.ult_seconds = 0
        charge = pilot.body_script.component.entity_var(ULT_CHARGE)
        initial_charge = charge.value()
        run.step({genji: (-32768, 0, 16), pilot: (0, 0, 1)})
        for _ in range(45):
            run.step({genji: (-32768, 0, 16)})
        self.assertEqual(pilot_health.total, 122)
        self.assertEqual(genji_health.total, 200)
        self.assertEqual(charge.value(), initial_charge)
        hit, = genji.client.messages(combat.HIT)
        self.assertEqual((hit["+0x78"]["+0x4"], hit["+0x78"]["+0x8"]), (28, combat.CRITICAL))
        self.assertFalse(pilot.client.messages(25001))

        run, pilot, genji, mech_health, genji_health = self.genji_fixture()
        charge = pilot.controlled_mech.body_script.component.entity_var(ULT_CHARGE)
        initial_charge = charge.value()
        fight = run.match.combat
        reflected, absorbed = [], []
        find_reflector, find_matrix = fight._genji_hit, fight._matrix_hit

        def reflect(*args):
            result = find_reflector(*args)
            if result is not None:
                reflected.append(result[1])
            return result

        def absorb(*args):
            result = find_matrix(*args)
            if result is not None:
                absorbed.append(args[0])
            return result

        with patch.object(fight, "_genji_hit", side_effect=reflect), patch.object(
            fight, "_matrix_hit", side_effect=absorb,
        ):
            run.step({genji: (-32768, 0, 16), pilot: (0, 0, 16)})
            for _ in range(70):
                run.step({genji: (-32768, 0, 16), pilot: (0, 0, 2)})
        self.assertIn(genji, reflected)
        self.assertIn(genji, absorbed)
        self.assertEqual(mech_health.total, 600)
        self.assertEqual(genji_health.total, 200)
        self.assertEqual(charge.value(), initial_charge)
        self.assertFalse(genji.client.messages(25001))
        self.assertFalse(pilot.client.messages(25001))

    def test_allied_genji_reflects_enforcer_with_falloff_over_both_flight_legs(self):
        fixture = manual_tests.ManualIncomingTests
        fixture.setUpClass()
        for pilot_form in (False, True):
            for distance in (11, 15):
                with self.subTest(pilot_form=pilot_form, distance=distance):
                    self.check_enforcer_reflection(fixture.by_name["Enforcer"], pilot_form, distance)

    def check_enforcer_reflection(self, definition, pilot_form, distance):
        run, pilot, genji, other, target, health = ana_tests.DvaAnaTests().scene(pilot_form=pilot_form)
        match, fight = run.match, run.match.combat
        other.spawned = False
        genji.team = pilot.team
        self.assertTrue(match.switch_hero(genji, find_hero("genji")))
        x, y, z = target.feet
        genji.mover.position[:] = (x, y, z + 3)
        genji.mover.body = None
        for _ in range(30):
            run.step({})
        bot = npc.spawn(match, definition, (x, y, z + distance), team=1)
        angles = aim(fight._eye(genji), (x, y + 1.0, z + distance))
        npc_control.engage(match, bot, pilot)
        for _ in range(50):
            run.step({genji: (*angles, 0)})
        run.step({genji: (*angles, 16)})
        for _ in range(40):
            run.step({genji: (*angles, 0)})
            if fight.rockets:
                break
        volley = tuple(fight.rockets)
        self.assertEqual(len(volley), 25)
        bot_target = next(t for t in fight._targets() if t.owner is bot)
        a, b = bot_target.segment()
        reflected = 0
        expected = []
        for shot in volley:
            hit = fight._genji_hit(bot, shot.position, shot.direction, 100, shot.definition)
            if hit is None:
                continue
            outbound, reflector, forward, _ = hit
            self.assertIs(reflector, genji)
            reflected += 1
            point = tuple(p + v * outbound for p, v in zip(shot.position, shot.direction, strict=True))
            inbound = combat.ray_capsule(point, forward, a, b, bot_target.radius)
            if inbound is None:
                continue
            travel = outbound + inbound
            if distance == 11:
                self.assertTrue(8 < travel < 15)
            else:
                self.assertGreaterEqual(travel, 15)
            expected.append(2 * (1 - .8 * min(1, (travel - 8) / 7)))
        if distance == 11:
            self.assertEqual(len(expected), reflected)
            self.assertEqual(reflected, 25)
        else:
            self.assertTrue(0 < len(expected) < reflected < 25)
        npc_control.stop(match)
        pilot_before = health.total
        genji_health = fight.vitals_of(fight.player_target(genji))
        genji_before = genji_health.total
        bot_health = fight.vitals_of(bot_target)
        bot_before = bot_health.total
        with patch.object(fight, "_damage", wraps=fight._damage) as damage:
            for _ in range(25):
                run.step({genji: (*angles, 0)})
        self.assertEqual(sum(shot.shooter is genji for shot in volley), reflected)
        self.assertEqual(damage.call_count, len(expected))
        self.assertTrue(all(call.args[0] is genji and call.args[1].owner is bot
                            for call in damage.call_args_list))
        amounts = sorted(call.args[2] for call in damage.call_args_list)
        for actual, wanted in zip(amounts, sorted(expected), strict=True):
            self.assertAlmostEqual(actual, wanted, places=6)
        self.assertAlmostEqual(bot_before - bot_health.total, sum(expected), places=5)
        self.assertEqual(health.total, pilot_before)
        self.assertEqual(genji_health.total, genji_before)

    def test_genji_deflect_blocks_quick_melee_from_either_dva_form(self):
        from test_game_combat import aim

        for pilot_form in (False, True):
            for facing, active in ((True, True), (False, True), (True, False)):
                with self.subTest(pilot_form=pilot_form, facing=facing, active=active):
                    run, pilot, genji, attacker_health, genji_health = self.genji_fixture(
                        pilot_form=pilot_form, distance=1.8,
                    )
                    fight = run.match.combat
                    source = pilot if pilot_form else pilot.controlled_mech
                    gx, gy, gz = genji.mover.position
                    yaw, pitch = aim(fight._eye(source), (gx, gy + 1, gz))
                    gyaw = -32768 if facing else 0
                    run.step({genji: (gyaw, 0, 0)})
                    self.assertEqual(genji.mover.yaw, gyaw)
                    if not active:
                        for _ in range(160):
                            run.step({genji: (gyaw, 0, 0)})
                    instances = genji.body_script.component.instances.values()
                    graph = next(i for i in instances if i.graph.index == 0x3A3)
                    self.assertEqual(graph.state(10).active, active)
                    before = attacker_health.total
                    run.step({genji: (gyaw, 0, 0), pilot: (yaw, pitch, 0x800)})
                    for _ in range(40):
                        run.step({genji: (gyaw, 0, 0), pilot: (yaw, pitch, 0)})
                    self.assertEqual(genji_health.total, 200 if facing and active else 170)
                    self.assertEqual(attacker_health.total, before)
                    self.assertFalse(genji.client.messages(25001))

    def test_call_mech_landing_bypasses_active_matrix_and_deflect(self):
        from test_game_dva_created_body import CreatedBodyTests

        from ow174.game.content import find_hero

        for hero, graph_id, state_id, button in (("dva", 0x2CC, 3, 2), ("genji", 0x3A3, 10, 16)):
            with self.subTest(hero=hero):
                run, pilot, defender, call_mech, old = CreatedBodyTests().ready_call_mech()
                match = run.match
                fight = match.combat
                self.assertTrue(match.switch_hero(defender, find_hero(hero)))
                for _ in range(100):
                    run.step({})
                run.step({pilot: (0, 0, 32)})
                for frame in range(24):
                    run.step({defender: (-32768, 0, button if frame >= 12 else 0)})
                replacement, = [bot for bot in match.bots if bot.owner == pilot.entity]
                self.assertIsNot(replacement, old)
                x, y, z = replacement.position
                actor = defender.controlled_mech or defender
                position = (x, y, z + 2)
                if defender.controlled_mech is not None:
                    actor.position = position
                    actor.control_mover.position[:] = position
                    actor.control_mover.body = None
                else:
                    actor.mover.position[:] = position
                    actor.mover.body = None
                target = fight.player_target(defender)
                # Let Genji survive the landing so the active Deflect and knockback remain observable.
                if hero == "genji":
                    fight.vitals[target.entity] = Vitals(target.entity, 500)
                    fight._sync_body_health(actor, fight.vitals[target.entity])
                health = fight.vitals_of(target)
                before = health.total
                defense = next(i for i in actor.body_script.component.instances.values()
                               if i.graph.index == graph_id)
                for _ in range(45):
                    run.step({defender: (-32768, 0, button)})
                    if call_mech.state(20).counter:
                        break
                self.assertEqual(call_mech.state(20).counter, 1)
                self.assertTrue(defense.state(state_id).active)
                self.assertEqual(before - health.total, 245 if hero == "dva" else 250)
                mover = actor.control_mover if defender.controlled_mech is not None else actor.mover
                self.assertGreater(mover.velocity[2], 20)
                self.assertAlmostEqual(mover.velocity[0], 0)
                self.assertGreater(mover.velocity[1], 2)
                self.assertFalse(defender.client.messages(25001))

    def test_genji_deflect_facing_and_expiry_gate_reflection(self):
        for expired in (False, True):
            with self.subTest(expired=expired):
                run, pilot, genji, mech_health, genji_health = self.genji_fixture()
                instances = genji.body_script.component.instances.values()
                graph = next(i for i in instances if i.graph.index == 0x3A3)
                if expired:
                    for _ in range(160):
                        run.step({genji: (-32768, 0, 0)})
                else:
                    run.step({genji: (0, 0, 16)})
                self.assertEqual(graph.state(10).active, not expired)
                run.step({genji: (-32768 if expired else 0, 0, 0), pilot: (0, 0, 1)})
                self.assertLess(genji_health.total, 200)
                self.assertEqual(mech_health.total, 600)

    def test_genji_deflect_respects_wall_and_team_ordering(self):
        from test_game_mover import box, wall

        for covered in (False, True):
            with self.subTest(covered=covered):
                run, pilot, genji, mech_health, genji_health = self.genji_fixture()
                match = run.match
                if covered:
                    x, y, z = pilot.controlled_mech.position
                    match.collision_world = World(
                        box(x - 10, x + 10, 0, y, z - 10, z + 10)
                        + wall(x - 5, z + 2, x + 5, z + 2)
                    )
                else:
                    match.game_map = replace(match.game_map, free_for_all=False)
                    genji.team = pilot.team
                run.step({genji: (-32768, 0, 16), pilot: (0, 0, 1)})
                self.assertEqual(mech_health.total, 600)
                self.assertEqual(genji_health.total, 200)

    def test_authored_helix_is_removed_without_direct_or_splash_damage(self):
        run, mech, enemy, vitals = self.prepared()
        with patch.object(run.match.combat.projectiles, "absorbed",
                          wraps=run.match.combat.projectiles.absorbed) as absorbed:
            for frame in range(45):
                run.step({mech: (0, 0, 2), enemy: (-32768, 0, 2 if frame == 0 else 0)})
            self.assertEqual(absorbed.call_count, 1)
        self.assertEqual(vitals.total, 600)
        self.assertEqual(run.match.combat.rockets, [])
        self.assertEqual(run.match.combat.projectiles.shots, [])
        self.assertEqual(enemy.client.messages(25001), [])

    def controlled(self):
        from test_game_dva_created_body import CreatedBodyTests

        run, pilot, enemy, _, mech = CreatedBodyTests().scenario()
        fight = run.match.combat
        x, y, z = mech.position
        enemy.mover.position = [x, y, z + 9]
        enemy.mover.body = None
        for _ in range(100):
            run.step({})
        matrix = next(i for i in mech.body_script.component.instances.values() if i.graph.index == 0x2CC)
        vitals = fight.vitals_of(fight.player_target(pilot))
        return run, pilot, enemy, matrix, vitals

    def test_initial_controlled_mech_absorbs_rifle_and_helix_until_release(self):
        run, pilot, enemy, matrix, vitals = self.controlled()
        fight = run.match.combat
        for _ in range(15):
            run.step({pilot: (0, 0, 2), enemy: (-32768, 0, 1)})
        self.assertTrue(matrix.state(3).active)
        self.assertEqual(vitals.total, 600)
        with patch.object(fight.projectiles, "absorbed", wraps=fight.projectiles.absorbed) as absorbed:
            for frame in range(45):
                run.step({pilot: (0, 0, 2), enemy: (-32768, 0, 2 if frame == 3 else 0)})
            self.assertEqual(absorbed.call_count, 1)
        self.assertTrue(matrix.state(3).active)
        self.assertEqual(vitals.total, 600)
        self.assertFalse(fight.rockets)
        self.assertEqual(enemy.client.messages(25001), [])
        for _ in range(25):
            run.step({enemy: (-32768, 0, 1)})
        self.assertFalse(matrix.state(3).active)
        self.assertLess(vitals.total, 600)

    def test_matrix_stops_intercepting_on_demech_or_self_destruct(self):
        for self_destruct in (False, True):
            with self.subTest(self_destruct=self_destruct):
                self.check_matrix_return_to_pilot(self_destruct)

    def check_matrix_return_to_pilot(self, self_destruct):
        run, pilot, enemy, matrix, mech_health = self.controlled()
        fight = run.match.combat
        mech = pilot.controlled_mech
        for _ in range(15):
            run.step({pilot: (0, 0, 2), enemy: (-32768, 0, 1)})
        self.assertTrue(matrix.state(3).active)
        self.assertEqual(mech_health.total, 600)
        self.assertFalse(enemy.client.messages(25001))
        x, y, z = mech.position
        if self_destruct:
            component = mech.body_script.component
            component.set(ULT_CHARGE, component.entity_var(ULT_COST).value())
            run.step({pilot: (0, 0, 2 | 32)})
        else:
            fight.vitals[mech.entity] = Vitals(mech.entity, 5)
            enemy.mover.position[:] = (x, y, z - 3)
            enemy.mover.body = None
            run.step({pilot: (0, 0, 2)})
            for _ in range(12):
                run.step({pilot: (0, 0, 2), enemy: (0, 0, 1)})
                if mech.dead:
                    break
            self.assertTrue(mech.dead)

        for _ in range(8):
            run.step({pilot: (0, 0, 2)})
            if pilot.controlled_mech is None:
                break
        self.assertIsNone(pilot.controlled_mech)
        self.assertEqual(mech.detached_bomb, self_destruct)
        target = fight.player_target(pilot)
        self.assertIs(target.owner, pilot)
        health = fight.vitals_of(target)
        self.assertEqual(health.total, 150)
        x, y, z = target.feet
        enemy.mover.position[:] = (x, y, z + 4)
        enemy.mover.body = None
        for _ in range(20):
            run.step({pilot: (0, 0, 2), enemy: (-32768, 0, 1)})
            if health.total < 150:
                break
        self.assertEqual(health.total, 110)  # 20-damage rifle critical at eye height.
        hit = enemy.client.messages(25001)[-1]
        self.assertEqual(hit["+0x78"]["+0x0"]["+0x0"], target.entity)

    def test_call_mech_replacement_starts_with_independent_matrix_resource(self):
        run, pilot, enemy, old_matrix, _ = self.controlled()
        match = run.match
        fight = match.combat
        old = pilot.controlled_mech
        for _ in range(160):
            run.step({pilot: (0, 0, 2)})
            if old_matrix.vars[87].value() == 0:
                break
        self.assertEqual(old_matrix.vars[87].value(), 0)
        x, y, z = old.position
        enemy.mover.position[:] = (x, y, z + 25)
        enemy.mover.body = None
        component = old.body_script.component
        component.set(ULT_CHARGE, component.entity_var(ULT_COST).value())
        run.step({pilot: (0, 0, 32)})
        run.step({})
        self.assertTrue(old.detached_bomb)
        self.assertIsNone(pilot.controlled_mech)

        for _ in range(235):
            run.step({})
            if old not in match.bots:
                break
        self.assertNotIn(old, match.bots)
        self.assertIsNone(old.body_script)
        run.step({pilot: (0, 0, 32)})
        for _ in range(120):
            run.step({})
            if pilot.controlled_mech is not None:
                break
        replacement = pilot.controlled_mech
        self.assertIsNotNone(replacement)
        self.assertNotEqual(replacement.entity, old.entity)
        instances = replacement.body_script.component.instances.values()
        matrix = next(i for i in instances if i.graph.index == 0x2CC)
        self.assertIsNot(matrix.component, old_matrix.component)
        self.assertEqual(matrix.vars[87].value(), 100)
        self.assertFalse(matrix.state(3).active)

        x, y, z = replacement.position
        enemy.mover.position[:] = (x, y, z + 9)
        enemy.mover.body = None
        health = fight.vitals_of(fight.player_target(pilot))
        for _ in range(15):
            run.step({pilot: (0, 0, 2), enemy: (-32768, 0, 1)})
        self.assertTrue(matrix.state(3).active)
        self.assertLess(matrix.vars[87].value(), 100)
        with patch.object(fight.projectiles, "absorbed", wraps=fight.projectiles.absorbed) as absorbed:
            for frame in range(40):
                run.step({pilot: (0, 0, 2), enemy: (-32768, 0, 2 if frame == 3 else 0)})
            self.assertEqual(absorbed.call_count, 1)
        self.assertEqual(health.total, 600)
        self.assertFalse(enemy.client.messages(25001))
        for _ in range(30):
            run.step({enemy: (-32768, 0, 1)})
            if health.total < 600:
                break
        self.assertFalse(matrix.state(3).active)
        self.assertLess(health.total, 600)

    def test_initial_controlled_mech_matrix_depletes_and_recovers_on_its_own_clock(self):
        run, pilot, enemy, matrix, vitals = self.controlled()
        self.assertEqual(matrix.vars[87].value(), 100)
        for _ in range(100):
            run.step({pilot: (0, 0, 2), enemy: (-32768, 0, 1)})
        self.assertEqual(vitals.total, 600)
        for _ in range(50):
            run.step({pilot: (0, 0, 2), enemy: (-32768, 0, 1)})
        self.assertFalse(matrix.state(3).active)
        self.assertEqual(matrix.vars[87].value(), 0)
        self.assertLess(vitals.total, 600)
        for _ in range(41):
            run.step({})
        self.assertLess(matrix.vars[87].value(), 10)
        run.step({pilot: (0, 0, 2)})
        self.assertFalse(matrix.state(3).active)
        for _ in range(80):
            run.step({})
        run.step({pilot: (0, 0, 2)})
        self.assertTrue(matrix.state(3).active)

    def test_matrix_stops_intercepting_at_exhaustion_during_command_gap(self):
        run, pilot, enemy, matrix, health = self.controlled()
        fight = run.match.combat
        mech = pilot.controlled_mech
        for _ in range(123):
            run.step({pilot: (0, 0, 2)})
        volume = matrix.state(3)
        self.assertTrue(volume.active)
        self.assertGreater(matrix.vars[87].value(), 0)
        self.assertLess(matrix.vars[87].value(), 2)
        last_pose = matrix.component.now
        x, y, z = mech.position
        yaw, pitch = aim(fight._eye(enemy), (x, y + 1, z))
        before = health.total
        with patch.object(fight, "_shoot", wraps=fight._shoot) as shoot:
            for offset in range(1, 20):
                run.frame += 1
                run.match.update(run.frame * combat.TICK, run.frame, frames=False)
                enemy.take_commands([Command(run.frame, yaw=yaw, pitch=pitch, buttons=1)])
                if offset == 1:
                    self.assertEqual(shoot.call_count, 1)
                    self.assertEqual(health.total, before)
        self.assertGreater(shoot.call_count, 1)
        self.assertLess(health.total, before)
        after_rifle = health.total
        run.frame += 1
        run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        enemy.take_commands([Command(run.frame, yaw=yaw, pitch=pitch, buttons=2)])
        self.assertTrue(any(rocket.shooter is enemy for rocket in fight.rockets))
        for _ in range(12):
            run.frame += 1
            run.match.update(run.frame * combat.TICK, run.frame, frames=False)
            if health.total < after_rifle:
                break
        self.assertLess(health.total, after_rifle)
        self.assertEqual(matrix.component.now, last_pose)
        pilot.take_commands([Command(run.frame, buttons=2)])
        self.assertFalse(volume.active)
        self.assertEqual(matrix.vars[87].value(), 0)
        self.assertEqual(volume.counter, 1)

    def test_matrix_stops_intercepting_at_release_during_command_gap(self):
        for held in (False, True):
            with self.subTest(held=held):
                self.check_matrix_release_during_command_gap(held)

    def check_matrix_release_during_command_gap(self, held):
        run, pilot, enemy, matrix, health = self.controlled()
        fight = run.match.combat
        mech = pilot.controlled_mech
        for _ in range(34 if held else 1):
            run.step({pilot: (0, 0, 2)})
        if not held:
            run.step({})
        ability, volume, tap = (matrix.state(index) for index in (0, 3, 26))
        self.assertTrue(volume.active)
        self.assertEqual(tap.active, not held)
        last_pose = matrix.component.now
        frame_ms = matrix.component.frame_ms
        close = last_pose + 32 * frame_ms + 1 if held else tap.start + tap.ms
        x, y, z = mech.position
        yaw, pitch = aim(fight._eye(enemy), (x, y + 1, z))
        protected = exposed = 0
        with patch.object(fight, "_shoot", wraps=fight._shoot) as shoot:
            for offset in range(1, 48):
                run.frame += 1
                run.match.update(run.frame * combat.TICK, run.frame, frames=False)
                before = health.total
                reports = shoot.call_count
                enemy.take_commands([Command(run.frame, yaw=yaw, pitch=pitch, buttons=int(offset >= 25))])
                if shoot.call_count == reports:
                    continue
                phase = (run.frame + mech.clock_offset) * frame_ms - 1
                if phase < close:
                    self.assertEqual(health.total, before)
                    protected += 1
                else:
                    self.assertLess(health.total, before)
                    exposed += 1
                    break
        self.assertGreater(protected, 0)
        self.assertEqual(exposed, 1)
        self.assertEqual(matrix.component.now, last_pose)
        pilot.take_commands([Command(run.frame)])
        self.assertFalse(volume.active)
        self.assertEqual(ability.last, close)
        self.assertGreater(matrix.vars[87].value(), 0)

    def test_flashbang_after_projected_matrix_close_preserves_natural_timers(self):
        for reason in ("exhaustion", "released tap", "hold window"):
            with self.subTest(reason=reason):
                continuous = self.check_flashbang_after_matrix_close(reason, gap=False)
                missing = self.check_flashbang_after_matrix_close(reason, gap=True)
                self.assertEqual(missing[:2], continuous[:2])
                for expected, actual in zip(continuous[2:], missing[2:], strict=True):
                    self.assertAlmostEqual(actual, expected, delta=.002)

    def test_flashbang_after_long_matrix_gap_preserves_natural_timers(self):
        reason = "hold window"
        continuous = self.check_flashbang_after_matrix_close(reason, gap=False, wait=68)
        missing = self.check_flashbang_after_matrix_close(reason, gap=True, wait=68)
        self.assertEqual(missing[:2], continuous[:2])
        for expected, actual in zip(continuous[2:], missing[2:], strict=True):
            self.assertAlmostEqual(actual, expected, delta=.002)

    def test_matrix_gap_beyond_history_keeps_recharge_anchored_by_flashbang(self):
        reason = "hold window"
        continuous = self.check_flashbang_after_matrix_close(reason, gap=False, wait=68, resume=350)
        missing = self.check_flashbang_after_matrix_close(reason, gap=True, wait=68, resume=350)
        self.assertEqual(missing[:2], continuous[:2])
        for expected, actual in zip(continuous[2:], missing[2:], strict=True):
            self.assertAlmostEqual(actual, expected, delta=.003)

    def check_flashbang_after_matrix_close(self, reason, *, gap, wait=None, resume=None):
        run, pilot, enemy, matrix, health = self.controlled()
        match, mech = run.match, pilot.controlled_mech
        fight = match.combat
        self.assertTrue(match.switch_hero(enemy, heroes()[0x02E0000000000042]))
        x, y, z = mech.position
        enemy.mover.position[:] = (x, y, z + 2)
        enemy.mover.body = None
        for _ in range(30):
            run.step({})
        held_frames = {"exhaustion": 123, "released tap": 1, "hold window": 34}[reason]
        for _ in range(held_frames):
            run.step({pilot: (0, 0, 2)})
        if reason == "released tap":
            run.step({})
        self.assertTrue(matrix.state(3).active)
        anchor = run.frame

        def step_world():
            run.frame += 1
            match.update(run.frame * combat.TICK, run.frame, frames=False)
            if not gap and not fight.is_stunned(mech):
                held = reason != "released tap" and run.frame - anchor <= 32
                pilot.take_commands([Command(run.frame, buttons=2 if held else 0)])

        if wait is None:
            wait = 18 if reason == "exhaustion" else 36
        for _ in range(wait):
            step_world()
        self.assertEqual(matrix.state(3).active, gap)
        yaw, pitch = aim(fight._eye(enemy), (x, y + 1, z))
        enemy.take_commands([Command(run.frame, yaw=yaw, pitch=pitch, buttons=16)])
        for _ in range(15):
            step_world()
            if fight.is_stunned(mech):
                break
        self.assertTrue(fight.is_stunned(mech))
        self.assertEqual(health.total, 580)
        self.assertFalse(matrix.state(3).active)
        cooldown = matrix.state(0).last
        recharge = matrix.state(21)
        self.assertTrue(recharge.active)
        recharge_start = recharge.start
        at_hit = matrix.vars[87].value()
        stun_end = fight.stuns[mech.entity]
        finish = stun_end if resume is None else anchor + resume
        while run.frame < finish - 1:
            if resume is not None and not gap:
                run.step({})
            else:
                run.frame += 1
                match.update(run.frame * combat.TICK, run.frame, frames=False)
        run.step({})
        self.assertEqual(matrix.state(0).last, cooldown)
        self.assertFalse(matrix.state(3).active)
        return cooldown, recharge_start, at_hit, matrix.vars[87].value()

    def test_matrix_long_gap_recovery_preserves_held_drain_and_recharge(self):
        for held_frames in (34, 123):
            with self.subTest(held_frames=held_frames):
                continuous = self.check_matrix_long_gap_recovery(held_frames, gap=False)
                missing = self.check_matrix_long_gap_recovery(held_frames, gap=True)
                self.assertEqual(missing[:2], continuous[:2])
                self.assertAlmostEqual(missing[2], continuous[2], delta=.002)

    def test_matrix_gap_beyond_retained_history_preserves_natural_timers(self):
        for held_frames in (1, 34, 123):
            with self.subTest(held_frames=held_frames):
                continuous = self.check_matrix_long_gap_recovery(held_frames, gap=False, frames=350)
                missing = self.check_matrix_long_gap_recovery(held_frames, gap=True, frames=350)
                self.assertEqual(missing[:2], continuous[:2])
                self.assertAlmostEqual(missing[2], continuous[2], delta=.003)

    def check_matrix_long_gap_recovery(self, held_frames, *, gap, frames=100):
        run, pilot, _, matrix, _ = self.controlled()
        for _ in range(held_frames):
            run.step({pilot: (0, 0, 2)})
        if held_frames == 1:
            run.step({})
        self.assertTrue(matrix.state(3).active)
        for offset in range(1, frames + 1):
            if gap:
                run.frame += 1
                run.match.update(run.frame * combat.TICK, run.frame, frames=False)
            else:
                run.step({pilot: (0, 0, 2 if held_frames != 1 and offset <= 32 else 0)})
        if gap:
            pilot.take_commands([Command(run.frame)])
        self.assertFalse(matrix.state(3).active)
        self.assertEqual(matrix.state(3).counter, 1)
        return matrix.state(0).last, matrix.state(21).start, matrix.vars[87].value()

    def test_controlled_matrix_tap_and_held_repress_use_authored_deadlines(self):
        for keep_repress in (False, True):
            with self.subTest(keep_repress=keep_repress):
                run, pilot, enemy, matrix, _ = self.controlled()
                fight = run.match.combat
                target = fight.player_target(pilot)
                health = fight.vitals[target.entity] = Vitals(target.entity, 600)
                x, y, z = target.feet
                yaw, pitch = aim(fight._eye(enemy), (x, y + 1.0, z))
                run.step({pilot: (0, 0, 2)})
                wait = matrix.state(26)
                self.assertAlmostEqual(wait.evaluate(wait.node.field("m_timeout"), watch=False), .5)
                self.assertTrue(matrix.state(3).active)
                for offset in range(1, 95):
                    held = offset == 34 or (keep_repress and offset > 34)
                    shoot = offset in (1, 36, 94)
                    run.step({pilot: (0, 0, 2 if held else 0), enemy: (yaw, pitch, int(shoot))})
                    if offset in (1, 30):
                        self.assertTrue(matrix.state(3).active)
                        self.assertEqual(health.total, 600)
                    elif offset == 31:
                        self.assertFalse(matrix.state(3).active)
                        self.assertAlmostEqual(matrix.vars[87].value(), 75.2, delta=.01)
                        self.assertGreater(matrix.vars[61].value(), .98)
                    elif offset == 36:
                        self.assertFalse(matrix.state(3).active)
                        self.assertEqual(health.total, 580)
                    elif offset == 92:
                        self.assertFalse(matrix.state(3).active)
                        self.assertGreater(matrix.vars[61].value(), 0)
                    elif offset == 93:
                        self.assertEqual(matrix.vars[61].value(), 0)
                        self.assertEqual(matrix.state(3).active, keep_repress)
                self.assertEqual(health.total, 580 if keep_repress else 560)

    def test_controlled_matrix_recharge_crosses_threshold_with_distinct_button_edges(self):
        run, pilot, _, matrix, _ = self.controlled()
        volume = matrix.state(3)
        for _ in range(126):
            run.step({pilot: (0, 0, 2)})
        self.assertFalse(volume.active)
        self.assertEqual(matrix.vars[87].value(), 0)
        initial = volume.counter
        self.assertTrue(matrix.vars[416].value())
        for _ in range(120):
            run.step({pilot: (0, 0, 2)})
            self.assertFalse(volume.active)
            if matrix.vars[87].value() >= 10:
                break
            self.assertTrue(matrix.vars[416].value())
        self.assertGreaterEqual(matrix.vars[87].value(), 10)
        self.assertLess(matrix.vars[87].value(), 10.3)
        self.assertFalse(matrix.vars[416].value())
        self.assertEqual(volume.counter, initial)
        run.step({})
        run.step({pilot: (0, 0, 2)})
        self.assertTrue(volume.active)
        self.assertEqual(volume.counter, initial + 1)

    def test_rear_flashbang_interrupts_controlled_matrix_during_tap_minimum(self):
        run, pilot, enemy, matrix, health = self.controlled()
        match, fight = run.match, run.match.combat
        mech = pilot.controlled_mech
        self.assertTrue(match.switch_hero(enemy, heroes()[0x02E0000000000042]))
        x, y, z = mech.position
        enemy.mover.position[:] = (x, y, z - 2)
        enemy.mover.body = None
        for _ in range(30):
            run.step({})
        yaw, pitch = aim(fight._eye(enemy), (x, y + 1, z))
        run.step({pilot: (0, 0, 2), enemy: (yaw, pitch, 16)})
        self.assertTrue(matrix.state(3).active)
        wait = matrix.state(26)
        self.assertTrue(wait.active)
        for _ in range(20):
            before_impact = matrix.component.now
            run.step({})
            if fight.is_stunned(mech):
                break
        self.assertTrue(fight.is_stunned(mech))
        self.assertLess(matrix.component.now - wait.start, 500)
        self.assertFalse(wait.active)
        self.assertFalse(matrix.state(3).active)
        self.assertEqual(health.total, 580)
        ability, volume = matrix.state(0), matrix.state(3)
        self.assertEqual(ability.cur, matrix.vars[472].value())
        self.assertEqual(ability.cur, 1)
        self.assertEqual(ability.last, before_impact)
        ready = ability.last + 1000
        stun_end = fight.stuns[mech.entity]
        while run.frame < stun_end:
            run.step({})
        self.assertFalse(fight.is_stunned(mech))
        self.assertGreater(ability.remaining(), 0)
        enemy.mover.position[:] = (x, y, z + 2)
        enemy.mover.body = None
        run.step({})
        yaw, pitch = aim(fight._eye(enemy), (x, y + 1, z))
        run.step({pilot: (0, 0, 2), enemy: (yaw, pitch, 1)})
        self.assertFalse(volume.active)
        self.assertLess(health.total, 580)
        while matrix.component.now < ready:
            run.step({pilot: (0, 0, 2)})
            self.assertEqual(ability.last, before_impact)
            self.assertEqual(volume.active, matrix.component.now >= ready)
        self.assertEqual(volume.counter, 2)
        self.assertEqual(volume.start, ready)
        exposed_health = health.total
        for _ in range(29):
            run.step({pilot: (0, 0, 2)})
        with patch.object(fight, "_shoot", wraps=fight._shoot) as shoot:
            run.step({pilot: (0, 0, 2), enemy: (yaw, pitch, 1)})
        self.assertTrue(any(call.args[0] is enemy for call in shoot.call_args_list))
        self.assertTrue(volume.active)
        self.assertEqual(health.total, exposed_health)

    def test_matrix_interruption_cooldown_uses_impact_during_command_gap(self):
        for past_tap in (False, True):
            with self.subTest(past_tap=past_tap):
                continuous = self.check_matrix_interruption_during_command_gap(past_tap, gap=False)
                missing = self.check_matrix_interruption_during_command_gap(past_tap, gap=True)
                for expected, actual in zip(continuous, missing, strict=True):
                    self.assertAlmostEqual(actual, expected, delta=.002)

    def check_matrix_interruption_during_command_gap(self, past_tap, *, gap):
        run, pilot, enemy, matrix, _ = self.controlled()
        match, mech = run.match, pilot.controlled_mech
        fight = match.combat
        self.assertTrue(match.switch_hero(enemy, heroes()[0x02E0000000000042]))
        x, y, z = mech.position
        enemy.mover.position[:] = (x, y, z - 2)
        enemy.mover.body = None
        for _ in range(30):
            run.step({})
        for _ in range(34 if past_tap else 1):
            run.step({pilot: (0, 0, 2)})
        ability, volume = matrix.state(0), matrix.state(3)
        self.assertTrue(volume.active)
        self.assertEqual(matrix.state(26).active, not past_tap)
        last_pose = matrix.component.now
        offset = mech.clock_offset

        def step_world():
            run.frame += 1
            match.update(run.frame * combat.TICK, run.frame, frames=False)
            if not gap and not fight.is_stunned(mech):
                pilot.take_commands([Command(run.frame, buttons=2)])

        for _ in range(24):
            step_world()
        yaw, pitch = aim(fight._eye(enemy), (x, y + 1, z))
        enemy.take_commands([Command(run.frame, yaw=yaw, pitch=pitch, buttons=16)])
        with patch.object(matrix, "run_node", wraps=matrix.run_node) as execute:
            for _ in range(15):
                step_world()
                if fight.is_stunned(mech):
                    break
        actions = {call.args[0].pos for call in execute.call_args_list}
        self.assertIn(68 if past_tap else 97, actions)
        self.assertTrue(fight.is_stunned(mech))
        self.assertFalse(volume.active)
        impact = (run.frame + offset) * matrix.component.frame_ms - 1
        self.assertEqual(matrix.component.now, last_pose if gap else impact)
        self.assertEqual(ability.last, impact)
        self.assertEqual(ability.remaining(), 1)
        at_impact = matrix.vars[87].value()
        recharge = matrix.state(21)
        self.assertTrue(recharge.active)
        self.assertEqual(recharge.ms, 750)
        if gap:
            pilot.take_commands([Command(run.frame - 8, buttons=2)])
            self.assertFalse(volume.active)
            self.assertEqual(matrix.vars[87].value(), at_impact)
            self.assertEqual(recharge.start, impact)
        stun_end = fight.stuns[mech.entity]
        while run.frame < stun_end - 1:
            run.frame += 1
            match.update(run.frame * combat.TICK, run.frame, frames=False)
        run.step({pilot: (0, 0, 2)})
        self.assertFalse(fight.is_stunned(mech))
        self.assertFalse(volume.active)
        self.assertGreater(ability.remaining(), 0)
        at_stun_end = matrix.vars[87].value()
        for _ in range(20):
            if volume.active:
                break
            run.step({pilot: (0, 0, 2)})
        self.assertTrue(volume.active)
        self.assertEqual(volume.start, impact + 1000)
        return at_impact, recharge.start - impact, at_stun_end

    def test_controlled_matrix_uses_pilot_ruleset_for_admission(self):
        run, pilot, enemy, matrix, health = self.controlled()
        parent = pilot.body_script.component
        fight = run.match.combat
        x, y, z = fight.player_target(pilot).feet
        yaw, pitch = aim(fight._eye(enemy), (x, y + 1, z))
        parent.set(9743, False)
        run.step({pilot: (0, 0, 2), enemy: (yaw, pitch, 1)})
        self.assertFalse(matrix.state(3).active)
        self.assertEqual(matrix.vars[87].value(), 100)
        self.assertEqual(health.total, 585)
        for _ in range(15):
            run.step({})
        parent.set(9743, True)
        run.step({pilot: (0, 0, 2), enemy: (yaw, pitch, 1)})
        self.assertTrue(matrix.state(3).active)
        self.assertEqual(health.total, 585)
        parent.set(9743, False)
        run.step({pilot: (0, 0, 2)})
        self.assertTrue(matrix.state(3).active)
        self.assertFalse(matrix.vars[4993].value())
        for _ in range(100):
            run.step({})
        self.assertFalse(matrix.state(3).active)
        run.step({pilot: (0, 0, 2)})
        self.assertFalse(matrix.state(3).active)

    def test_rocket_hits_when_matrix_is_off_or_aims_away(self):
        for enabled in (False, True):
            run, mech, enemy, vitals = self.prepared(enabled)
            for frame in range(45):
                run.step({mech: (32768 if enabled else 0, 0, 2 if enabled else 0),
                          enemy: (32768, 0, 2 if frame == 0 else 0)})
            self.assertLess(vitals.total, 600)

    def test_exhaustion_stops_intercepting_held_incoming_rifle_fire(self):
        run, mech, enemy, vitals = self.prepared()
        for _ in range(100):
            run.step({mech: (0, 0, 2), enemy: (-32768, 0, 1)})
        self.assertEqual(vitals.total, 600)
        for _ in range(45):
            run.step({mech: (0, 0, 2), enemy: (-32768, 0, 1)})
        self.assertFalse(self.matrix(mech).state(3).active)
        self.assertLess(vitals.total, 600)

    def test_early_release_keeps_authored_minimum_hold_then_stops(self):
        run, mech, enemy, vitals = self.prepared()
        for _ in range(10):
            run.step({enemy: (-32768, 0, 1)})
        self.assertTrue(self.matrix(mech).state(3).active)
        self.assertEqual(vitals.total, 600)
        for _ in range(35):
            run.step({enemy: (-32768, 0, 1)})
        self.assertFalse(self.matrix(mech).state(3).active)
        self.assertLess(vitals.total, 600)

    def test_live_genji_and_sigma_boxes_reject_contact_across_static_cover(self):
        import math

        import test_game_dva_sigma as sigma_tests
        from test_game_mover import box, wall

        for hero in ("genji", "sigma"):
            with self.subTest(hero=hero):
                if hero == "genji":
                    run, pilot, defender, _, _ = self.genji_fixture()
                    contact = run.match.combat._genji_hit
                else:
                    run, pilot, defender, _, _, _, _ = sigma_tests.DvaSigmaTests().scene()
                    contact = run.match.combat._absorb_contact
                fight = run.match.combat
                shooter = pilot.controlled_mech
                origin, anchor = fight._eye(shooter), fight._eye(defender)
                length = math.dist(origin, anchor)
                direction = tuple((end - start) / length for start, end in zip(origin, anchor, strict=True))
                open_hit = contact(shooter, origin, direction, length, RIFLE)
                self.assertIsNotNone(open_hit)
                hit_z = origin[2] + direction[2] * open_hit[0]
                self.assertLess(hit_z, anchor[2])
                x, y, z = shooter.position
                wall_z = (hit_z + anchor[2]) / 2
                run.match.collision_world = World(
                    box(x - 10, x + 10, 0, y, z - 10, z + 10)
                    + wall(x - 5, wall_z, x + 5, wall_z)
                )
                self.assertIsNone(contact(shooter, origin, direction, length, RIFLE))

    def test_cover_between_matrix_and_contact_preserves_actual_rifle_and_helix_hits(self):
        import test_game_dva_ana as ana_tests
        from test_game_combat import aim
        from test_game_mover import box, wall

        from ow174.game.content import find_hero

        for cover in (False, True):
            for button in (1, 2):
                with self.subTest(cover=cover, button=button):
                    run, pilot, enemy, ally, target, mech_health = ana_tests.DvaAnaTests().scene()
                    fight = run.match.combat
                    self.assertTrue(run.match.switch_hero(enemy, find_hero("soldier")))
                    x, y, z = target.feet
                    for actor, offset in ((enemy, 12), (ally, 4)):
                        actor.mover.position[:] = (x, y, z + offset)
                        actor.mover.body = None
                    if cover:
                        run.match.collision_world = World(
                            box(x - 12, x + 12, 0, y, z - 10, z + 16)
                            + wall(x - 6, z + 2, x + 6, z + 2)
                        )
                    for _ in range(40):
                        run.step({})
                    for _ in range(5):
                        run.step({pilot: (0, 0, 2)})
                    actual = fight.player_target(ally)
                    health = fight.vitals_of(actual)
                    gx, gy, gz = actual.feet
                    yaw, pitch = aim(fight._eye(enemy), (gx, gy + 1.3, gz))
                    before_mech = mech_health.total
                    before = health.total
                    run.step({pilot: (0, 0, 2), enemy: (yaw, pitch, button)})
                    for _ in range(45):
                        run.step({pilot: (0, 0, 2), enemy: (yaw, pitch, 0)})
                    self.assertEqual(mech_health.total, before_mech)
                    self.assertEqual(health.total, before - ((120 if button == 2 else 20) if cover else 0))

    def test_world_or_nearer_body_wins_before_matrix_volume(self):
        for wall in (False, True):
            run, mech, enemy, vitals = self.prepared()
            fight = run.match.combat
            origin = (0, fight._eye(mech)[1], 15)
            bot = bots.Bot(0xA123, {
                "hero": "0x02E00000000001AC", "team": 7,
                "position": (0, 1, 13), "rotation": (0, 0, 0, 1), "graphs": [],
            }, {"name": "Collision fixture", "body": "0x0400000000001403", "health": 200}, {})
            extra = Target(bot.entity, bot.team, (0, 1, 13), 2, 0.5, 1.55, bot)
            extra_vitals = Vitals(extra.entity, 200)
            fight.vitals[extra.entity] = extra_vitals
            targets = fight._targets()
            if wall:
                run.match.collision_world = World([
                    (-10, -10, 12, 10, -10, 12, 10, 10, 12),
                    (-10, -10, 12, 10, 10, 12, -10, 10, 12),
                ])
            else:
                run.match.bots.append(bot)  # a live world body, also admitted by the explicit target list.
                targets.append(extra)
            rocket = Rocket(enemy, enemy.team, origin, (0, 0, -1), 1000, 1,
                            HitDef(amount=20), None, definition=HELIX)
            fight.rockets.append(rocket)
            with patch.object(fight, "_targets", return_value=targets), \
                    patch.object(fight, "_landed", wraps=fight._landed) as landed:
                fight._fly(rocket)
                landed.assert_called_once_with(rocket)
            self.assertEqual(vitals.total, 600)
            self.assertEqual(extra_vitals.total, 200 if wall else 180)

    def test_authored_boxes_rotate_with_aim_and_keep_width_height_range(self):
        run, mech, _, _ = self.prepared()
        anchor = run.match.combat._eye(mech)
        def distance(origin, forward):
            return deflection.matrix_distance(mech.body_script, origin, (0, 0, -1), 15, anchor, forward)
        self.assertAlmostEqual(distance((0, anchor[1], 15), (0, 0, 1)), 4.75)
        self.assertIsNone(distance((1.26, anchor[1], 15), (0, 0, 1)))
        self.assertIsNone(distance((0, anchor[1] + 1.01, 15), (0, 0, 1)))
        self.assertIsNone(distance((0, anchor[1], 15), (0, 0, -1)))
        self.assertAlmostEqual(deflection.matrix_distance(
            mech.body_script, (15, anchor[1], 0), (-1, 0, 0), 30, anchor, (1, 0, 0)), 4.75)
        self.assertAlmostEqual(deflection.matrix_distance(
            mech.body_script, (0, anchor[1] + 15, 0), (0, -1, 0), 30, anchor, (0, 1, 0)), 4.75)

    def test_matrix_normalizes_reversed_box_endpoints_before_contact(self):
        run, mech, _, _ = self.prepared()
        state = self.matrix(mech).state(3)
        evaluate = state.evaluate
        swapped = {}
        for box in state.node.field("m_boxes"):
            first = box["m_CBA118B2"]
            second = box["m_AEF67365"]
            swapped[id(first)] = second
            swapped[id(second)] = first
        anchor = run.match.combat._eye(mech)
        with patch.object(state, "evaluate", side_effect=lambda cfg, **kw: evaluate(
            swapped.get(id(cfg), cfg), **kw,
        )):
            hit = deflection.matrix_distance(
                mech.body_script, (0, anchor[1], 15), (0, 0, -1), 15, anchor, (0, 0, 1),
            )
            miss = deflection.matrix_distance(
                mech.body_script, (1.26, anchor[1], 15), (0, 0, -1), 15, anchor, (0, 0, 1),
            )
        self.assertAlmostEqual(hit, 4.75)
        self.assertIsNone(miss)

    def test_live_matrix_only_absorbs_enemy_micro_missiles(self):
        from test_game_combat import in_world
        from test_game_dva_created_body import CreatedBodyTests

        from ow174.game.content import find_hero

        self.enterContext(patch.object(body_entities, "ULT_SECONDS", 0))
        for mode in ("enemy", "ally", "deplete"):
            with self.subTest(mode=mode):
                allied = mode == "ally"
                run, pilot, defender, _, shooter = CreatedBodyTests().scenario()
                match = run.match
                fight = match.combat
                match.game_map = replace(match.game_map, free_for_all=False)
                defender.team = pilot.team if allied else 1
                soldier = defender.hero
                enemy = match.add_player(3, "Enemy", soldier.guid, 1, False)
                in_world(match, enemy)
                run.players.append(enemy)
                self.assertTrue(match.switch_hero(enemy, soldier))
                self.assertTrue(match.switch_hero(defender, find_hero("dva")))
                blocker = defender.controlled_mech
                x, y, z = shooter.position
                blocker.position = (x, y, z + 3)
                blocker.control_mover.position[:] = blocker.position
                blocker.control_mover.body = None
                enemy.mover.position[:] = (x, y, z + 6)
                enemy.mover.body = None
                for _ in range(100):
                    run.step({})
                health = fight.vitals_of(fight.player_target(enemy))
                charge = shooter.body_script.component.entity_var(ULT_CHARGE)
                initial_charge = charge.value() or 0
                matrix = next(i for i in blocker.body_script.component.instances.values()
                              if i.graph.index == 0x2CC)
                for _ in range(75 if mode == "deplete" else 5):
                    run.step({defender: (-32768, 0, 2)})
                self.assertTrue(matrix.state(3).active)
                absorbed = []
                find_matrix = fight._matrix_hit

                def record(*args, find_matrix=find_matrix, shooter=shooter, absorbed=absorbed):
                    result = find_matrix(*args)
                    if result is not None and args[0] is shooter:
                        absorbed.append(result)
                    return result

                with patch.object(fight, "_matrix_hit", side_effect=record):
                    run.step({pilot: (0, 0, 16), defender: (-32768, 0, 2)})
                    for _ in range(160):
                        run.step({defender: (-32768, 0, 2)})
                blocker_health = fight.vitals_of(fight.player_target(defender))
                gained = (charge.value() or 0) - initial_charge
                if mode == "deplete":
                    self.assertGreater(len(absorbed), 0)
                    self.assertLess(len(absorbed), 18)
                    self.assertFalse(matrix.state(3).active)
                    self.assertEqual(health.total, 200)
                    self.assertLess(blocker_health.total, 600)
                    self.assertEqual(gained, 600 - blocker_health.total)
                    self.assertEqual(gained, 3.5 * (18 - len(absorbed)))
                else:
                    self.assertEqual(len(absorbed), 0 if allied else 18)
                    self.assertEqual(blocker_health.total, 600)
                    self.assertEqual(health.total, 74 if allied else 200)
                    self.assertEqual(gained, 126 if allied else 0)

    def test_allied_matrix_cannot_absorb_shot_and_dead_matrix_is_ignored(self):
        run, mech, enemy, vitals = self.prepared()
        fight = run.match.combat
        origin = (0, fight._eye(mech)[1], 15)
        self.assertIsNotNone(fight._matrix_hit(enemy, origin, (0, 0, -1), 20, RIFLE))
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        enemy.team = mech.team
        self.assertIsNone(fight._matrix_hit(enemy, origin, (0, 0, -1), 20, RIFLE))
        enemy.team += 1
        vitals.died_at = fight.now
        self.assertIsNone(fight._matrix_hit(enemy, origin, (0, 0, -1), 20, RIFLE))

    def test_actual_mei_spray_bypasses_matrix_and_respects_its_range(self):
        for pilot_form, distance in ((False, 5), (True, 5), (False, 12), (True, 12)):
            with self.subTest(pilot_form=pilot_form, distance=distance):
                run, pilot, mei, other, target, health = ana_tests.DvaAnaTests().scene(pilot_form=pilot_form)
                fight = run.match.combat
                other.spawned = False
                self.assertTrue(run.match.switch_hero(mei, find_hero("mei")))
                health.heal(350)
                x, y, z = target.feet
                mei.mover.position[:] = (x, y, z + distance)
                mei.mover.body = None
                for _ in range(30):
                    run.step({})
                angles = aim(fight._eye(mei), (x, y + 1, z))
                matrix = None
                if not pilot_form:
                    matrix = next(i for i in target.owner.body_script.component.instances.values()
                                  if i.graph.index == 0x02CC)
                    run.step({pilot: (0, 0, 2)})
                    self.assertTrue(matrix.state(3).active)
                held = {mei: (*angles, 1)}
                if matrix is not None:
                    held[pilot] = (0, 0, 2)
                before = health.total
                with patch.object(fight, "_shoot", wraps=fight._shoot) as shots:
                    for _ in range(36):
                        run.step(held)
                emitted = [call.args[2] for call in shots.call_args_list if call.args[0] is mei]
                self.assertTrue(emitted)
                self.assertTrue(all(state.instance.graph.index == 0x05E6 and state.index == 13
                                    for state in emitted))
                self.assertIn(deflection.EXCLUDED_TAG, deflection.tags()[FROST])
                if distance < 12:
                    hits = mei.client.messages(combat.HIT)
                    self.assertTrue(hits)
                    self.assertAlmostEqual(before - health.total, len(hits) * (2.75 if pilot_form else 1.375))
                else:
                    self.assertEqual(health.total, before)
                if matrix is not None:
                    self.assertTrue(matrix.state(3).active)

    def test_actual_mei_spray_pierces_matrix_mech_once_and_stops_at_cover(self):
        from test_game_mover import box

        for covered in (False, True):
            with self.subTest(covered=covered):
                run, pilot, mei, teammate, target, health = ana_tests.DvaAnaTests().scene()
                match, fight = run.match, run.match.combat
                self.assertTrue(match.switch_hero(mei, find_hero("mei")))
                x, y, z = target.feet
                mei.mover.position[:] = (x, y, z + 4)
                teammate.mover.position[:] = (x, y, z - 2)
                mei.mover.body = teammate.mover.body = None
                if covered:
                    match.collision_world = World(box(x - 15, x + 15, y - 1, y, z - 15, z + 15)
                                                  + box(x - 3, x + 3, y, y + 3, z - 1.2, z - 1.1))
                for _ in range(30):
                    run.step({})
                friendly_health = fight.vitals_of(fight.player_target(teammate))
                initial = health.total, friendly_health.total
                angles = aim(fight._eye(mei), (x, y + 1, z))
                run.step({pilot: (0, 0, 2)})
                run.step({pilot: (0, 0, 2), mei: (*angles, 1)})
                for _ in range(20):
                    if fight.rockets:
                        break
                    run.step({pilot: (0, 0, 2), mei: (*angles, 1)})
                shot, = fight.rockets
                self.assertEqual((shot.definition, shot.target_limit), (FROST, 99))
                for _ in range(22):
                    run.step({pilot: (0, 0, 2), mei: (*angles, 0)})
                self.assertFalse(fight.rockets)
                expected = {target.entity} if covered else {target.entity, teammate.body}
                self.assertEqual(shot.hit_entities, expected)
                self.assertAlmostEqual(initial[0] - health.total, 2.75)
                self.assertAlmostEqual(initial[1] - friendly_health.total, 0 if covered else 2.75)

    def test_actual_mei_spray_slows_freezes_and_releases_both_dva_forms(self):
        for pilot_form in (False, True):
            with self.subTest(pilot_form=pilot_form):
                self.check_mei_spray_freeze(pilot_form)

    def check_mei_spray_freeze(self, pilot_form):
        from ow174.game.commands import Command

        run, pilot, mei, other, target, health = ana_tests.DvaAnaTests().scene(pilot_form=pilot_form)
        match, fight = run.match, run.match.combat
        other.spawned = False
        self.assertTrue(match.switch_hero(mei, find_hero("mei")))
        health.heal(350)
        x, y, z = target.feet
        mei.mover.position[:] = (x, y, z + 4)
        mei.mover.body = None
        for _ in range(30):
            run.step({})
        angles = aim(fight._eye(mei), (x, y + 1, z))
        actor = target.owner
        active_mover = pilot.mover if pilot_form else actor.control_mover
        matrix = None if pilot_form else next(
            i for i in actor.body_script.component.instances.values() if i.graph.index == 0x02CC
        )
        held = {mei: (*angles, 1), pilot: (1024, 512, 0 if pilot_form else 2)}
        for _ in range(25):
            run.step(held)
            if target.entity in fight.frost:
                break
        frost = fight.frost[target.entity]
        self.assertAlmostEqual(frost.progress(fight.tick * 16), .2)
        run.step(held)
        self.assertAlmostEqual(active_mover.values.run_forward, 4.4, places=5)
        if matrix is not None:
            self.assertTrue(matrix.state(3).active)
        for _ in range(110):
            run.step(held)
            if fight.is_stunned(actor):
                break
        self.assertTrue(fight.is_stunned(actor))
        self.assertEqual(len(mei.client.messages(combat.HIT)), 30)
        self.assertEqual(frost.frozen_until, fight.tick * 16 + 1300)
        locked_aim = frost.frozen_aim
        self.assertEqual((active_mover.yaw, active_mover.pitch), locked_aim)
        if matrix is not None:
            self.assertFalse(matrix.state(3).active)
        deadline = frost.frozen_until
        enemy_health = fight.vitals_of(fight.player_target(mei))
        before = enemy_health.total
        for _ in range(12):
            run.step({mei: (*angles, 1), pilot: (0, 0, 1)})
        self.assertEqual(frost.frozen_until, deadline)
        self.assertEqual(enemy_health.total, before)
        position = tuple(active_mover.position[0::2])
        run.frame += 1
        match.update(run.frame * combat.TICK, run.frame, frames=False)
        pilot.take_commands([Command(run.frame, buttons=1, forward=127, yaw=8192, pitch=4096)])
        self.assertEqual(tuple(active_mover.position[0::2]), position)
        self.assertEqual((active_mover.yaw, active_mover.pitch), locked_aim)
        self.assertEqual(enemy_health.total, before)
        for _ in range(100):
            if fight.tick * 16 >= deadline:
                break
            run.step({})
        self.assertNotIn(target.entity, fight.frost)
        self.assertFalse(fight.is_stunned(actor))
        run.step({})
        self.assertAlmostEqual(active_mover.values.run_forward, 5.5)
        run.step({pilot: (8192, 4096, 0)})
        self.assertEqual((active_mover.yaw, active_mover.pitch), (8192, 4096))
        aim_at_mei = aim(fight._eye(actor), (x, y + 1, z + 4))
        run.step({pilot: (*aim_at_mei, 1)})
        for _ in range(12):
            run.step({})
        self.assertLess(enemy_health.total, before)

    def test_lethal_rifle_clears_frost_before_pilot_return_and_call_mech(self):
        run, pilot, mei, rifle, target, health = ana_tests.DvaAnaTests().scene()
        match, fight = run.match, run.match.combat
        mech = target.owner
        self.assertTrue(match.switch_hero(mei, find_hero("mei")))
        rifle.team = mei.team
        x, y, z = target.feet
        for player, position in ((mei, (x, y, z + 4)), (rifle, (x + 5, y, z))):
            player.mover.position[:] = position
            player.mover.body = None
        for _ in range(30):
            run.step({})
        angles = aim(fight._eye(mei), (x, y + 1, z))
        for _ in range(120):
            run.step({mei: (*angles, 1)})
            if fight.is_stunned(mech):
                break
        self.assertTrue(fight.is_stunned(mech))
        for _ in range(20):
            run.step({})
        self.assertFalse(fight.rockets)
        self.assertTrue(fight.is_stunned(mech))
        health.damage(health.total - 5)
        angles = aim(fight._eye(rifle), (x, y + 1, z))
        run.step({rifle: (*angles, 1)})
        self.assertTrue(mech.dead)
        self.assertNotIn(mech.entity, fight.frost)
        run.step({})
        self.assertIsNone(pilot.controlled_mech)
        self.assertIs(fight.player_target(pilot).owner, pilot)
        self.assertEqual(fight.vitals_of(fight.player_target(pilot)).total, 150)
        self.assertNotIn(pilot.body, fight.frost)
        self.assertFalse(fight.is_stunned(pilot))

        mei.spawned = rifle.spawned = False
        for _ in range(100):
            run.step({})
        script = pilot.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        run.step({pilot: (8192, 4096, 32)})
        for _ in range(120):
            if pilot.controlled_mech is not None:
                break
            run.step({})
        replacement = pilot.controlled_mech
        self.assertIsNotNone(replacement)
        self.assertIsNot(replacement, mech)
        self.assertNotIn(replacement.entity, fight.frost)
        self.assertFalse(fight.is_stunned(replacement))
        self.assertEqual(fight.vitals_of(fight.player_target(pilot)).total, 600)

    def test_mei_spray_uses_source_frost_minimum_rate_and_freeze_duration(self):
        for pilot_form in (False, True):
            with self.subTest(pilot_form=pilot_form):
                run, _, mei, other, target, health = ana_tests.DvaAnaTests().scene(pilot_form=pilot_form)
                fight = run.match.combat
                other.spawned = False
                health.heal(350)
                self.assertTrue(run.match.switch_hero(mei, find_hero("mei")))
                component = mei.body_script.component
                component.set(combat.FROST_MINIMUM, .6)
                component.set(combat.FROST_RATE, 2)
                component.set(combat.FROST_DURATION, .5)
                x, y, z = target.feet
                mei.mover.position[:] = (x, y, z + 4)
                mei.mover.body = None
                for _ in range(30):
                    run.step({})
                angles = aim(fight._eye(mei), (x, y + 1, z))
                for _ in range(35):
                    run.step({mei: (*angles, 1)})
                    if target.entity in fight.frost:
                        break
                frost = fight.frost[target.entity]
                self.assertAlmostEqual(frost.progress(fight.tick * 16), .6)
                for _ in range(50):
                    run.step({mei: (*angles, 1)})
                    if fight.is_stunned(target.owner):
                        break
                self.assertTrue(fight.is_stunned(target.owner))
                self.assertEqual(len(mei.client.messages(combat.HIT)), 9)
                self.assertEqual(frost.frozen_until - fight.tick * 16, 650)
                deadline = frost.frozen_until
                for _ in range(50):
                    if fight.tick * 16 >= deadline:
                        break
                    run.step({})
                self.assertFalse(fight.is_stunned(target.owner))
                self.assertNotIn(target.entity, fight.frost)

    def test_frost_blocks_full_charge_until_thaw_and_leaves_detached_fuse_running(self):
        for pilot_form, detached in ((False, False), (True, False), (True, True)):
            with self.subTest(pilot_form=pilot_form, detached=detached):
                self.check_frost_ultimate(pilot_form, detached)

    def test_frost_stops_boosters_and_preserves_the_authored_cooldown(self):
        run, pilot, mei, _, mech, _ = self.mei_interruption_scene()
        fight = run.match.combat
        angles = aim(fight._eye(mei), fight._eye(mech))
        run.step({mei: (*angles, 1)})
        self.assertTrue(any(rocket.shooter is mei for rocket in fight.rockets))
        graph = next(i for i in mech.body_script.component.instances.values() if i.graph.index == 0x047E)
        ability, flight, post_end = (graph.state(index) for index in (0, 2, 5))
        boost = {pilot: (16384, 0, 8)}
        run.step(boost)
        self.assertTrue(flight.active)
        for _ in range(16):
            run.step(boost)
            if fight.is_stunned(mech):
                break
        self.assertTrue(fight.is_stunned(mech))
        self.assertFalse(flight.active)
        self.assertTrue(post_end.active)
        self.assertEqual(ability.remaining(), 0)
        cooldown_start = post_end.start + 300
        for _ in range(24):
            run.step({})
            self.assertFalse(flight.active)
            self.assertEqual(mech.control_mover.velocity[0::2], (0, 0))
        self.assertEqual(ability.last, cooldown_start)
        self.assertEqual(ability.cur, 4)
        deadline = fight.frost[mech.entity].frozen_until
        while fight.tick * 16 < deadline:
            run.step({})
        self.assertFalse(fight.is_stunned(mech))
        run.step(boost)
        self.assertGreater(ability.remaining(), 0)
        self.assertEqual(flight.counter, 1)
        while ability.remaining() > 0:
            run.step({})
        run.step(boost)
        self.assertEqual(flight.counter, 2)
        self.assertTrue(flight.active)

    def test_frost_interrupts_micro_missiles_on_either_side_of_first_launch(self):
        for launched in (False, True):
            with self.subTest(launched=launched):
                self.check_frost_missiles(launched)

    def check_frost_missiles(self, launched):
        run, pilot, mei, enemy, mech, _ = self.mei_interruption_scene()
        fight = run.match.combat
        graph = next(i for i in mech.body_script.component.instances.values() if i.graph.index == 0x1128)
        ability, volley, windup = (graph.state(index) for index in (0, 5, 7))
        x, y, z = enemy.mover.position
        angles = aim(fight._eye(mech), (x, y + 1, z))
        frost_angles = aim(fight._eye(mei), fight._eye(mech))
        health = fight.vitals_of(fight.player_target(enemy))
        before = health.total
        if launched:
            run.step({pilot: (*angles, 16)})
            for _ in range(35):
                if any(rocket.shooter is mech for rocket in fight.rockets):
                    break
                run.step({})
            self.assertTrue(volley.active)
            run.step({mei: (*frost_angles, 1)})
        else:
            run.step({mei: (*frost_angles, 1)})
            run.step({pilot: (*angles, 16)})
            self.assertTrue(windup.active)
        for _ in range(16):
            run.step({})
            if fight.is_stunned(mech):
                break
        self.assertTrue(fight.is_stunned(mech))
        self.assertFalse(volley.active)
        self.assertFalse(windup.active)
        self.assertEqual(ability.remaining() > 0, launched)
        in_flight = [rocket for rocket in fight.rockets if rocket.shooter is mech]
        self.assertEqual(bool(in_flight), launched)
        for _ in range(30):
            run.step({pilot: (*angles, 16)})
            self.assertFalse(volley.active)
        self.assertEqual(before - health.total, 7 * len(in_flight))
        self.assertFalse(any(rocket.shooter is mech for rocket in fight.rockets))
        deadline = fight.frost[mech.entity].frozen_until
        while fight.tick * 16 < deadline:
            run.step({})
        self.assertFalse(fight.is_stunned(mech))
        run.step({pilot: (*angles, 16)})
        self.assertEqual(windup.active, not launched)
        if launched:
            self.assertGreater(ability.remaining(), 0)
            run.frame += int(ability.remaining() / combat.TICK) + 1
            run.match.update(run.frame * combat.TICK, run.frame, frames=False)
            run.step({})
            self.assertEqual(ability.remaining(), 0)
            self.assertFalse(windup.active)
            run.step({pilot: (*angles, 16)})
        self.assertTrue(windup.active)
        self.assertEqual(windup.counter, 2)

    def mei_interruption_scene(self):
        run, pilot, mei, enemy, target, health = ana_tests.DvaAnaTests().scene()
        self.assertTrue(run.match.switch_hero(mei, find_hero("mei")))
        mei.body_script.component.set(combat.FROST_MINIMUM, 1.0)
        enemy.team = mei.team
        x, y, z = target.feet
        for actor, position in ((mei, (x + 4, y, z)), (enemy, (x, y, z + 15))):
            actor.mover.position[:] = position
            actor.mover.body = None
        for _ in range(30):
            run.step({})
        return run, pilot, mei, enemy, target.owner, health

    def check_frost_ultimate(self, pilot_form, detached):
        run, pilot, mei, enemy, target, health = ana_tests.DvaAnaTests().scene(
            pilot_form=pilot_form and not detached,
        )
        match, fight = run.match, run.match.combat
        self.assertTrue(match.switch_hero(mei, find_hero("mei")))
        health.heal(350)
        mech = pilot.controlled_mech
        cast_frame = None
        if detached:
            x, y, z = target.feet
            enemy.team = mei.team
            enemy.mover.position[:] = (x - 2, y, z)
            enemy.mover.body = None
            blast_health = fight.vitals[enemy.body] = Vitals(enemy.body, 5000)
            mei.mover.position[:] = (x, y, z + 4)
            mei.mover.body = None
            for _ in range(30):
                run.step({})
            angles = aim(fight._eye(mei), (x, y + 1, z))
            for _ in range(35):
                run.step({mei: (*angles, 1)})
                if mech.entity in fight.frost:
                    break
            for _ in range(18):
                run.step({})
            self.assertFalse(fight.rockets)
            self.assertIn(mech.entity, fight.frost)
            self.assertFalse(fight.is_stunned(mech))
            component = mech.body_script.component
            component.set(ULT_CHARGE, component.entity_var(ULT_COST).value())
            run.step({pilot: (0, 0, 32)})
            cast_frame = run.frame
            run.step({})
            self.assertTrue(mech.detached_bomb)
            self.assertIsNone(pilot.controlled_mech)
            self.assertNotIn(mech.entity, fight.frost)
            self.assertNotIn(pilot.body, fight.frost)
            pilot.mover.position[0] += 24
            pilot.mover.body = None
            for _ in range(45):
                run.step({})
            target = fight.player_target(pilot)
        else:
            enemy.spawned = False
        actor = target.owner
        component = actor.body_script.component
        charge = component.entity_var(ULT_CHARGE)
        cost = component.entity_var(ULT_COST).value()
        if not detached:
            component.set(ULT_CHARGE, cost)
        x, y, z = target.feet
        mei.mover.position[:] = (x, y, z + 4)
        mei.mover.body = None
        for _ in range(30):
            run.step({})
        angles = aim(fight._eye(mei), (x, y + 1, z))
        for _ in range(125):
            run.step({mei: (*angles, 1)})
            if fight.is_stunned(actor):
                break
        self.assertTrue(fight.is_stunned(actor))
        frost = fight.frost[target.entity]
        if detached:
            self.assertEqual(charge.value(), 0)
            while run.frame < cast_frame + 186:
                run.step({pilot: (0, 0, 32)})
                self.assertEqual(blast_health.total, 5000)
                self.assertEqual(charge.value(), 0)
            run.step({pilot: (0, 0, 32)})
            self.assertEqual(blast_health.total, 4000)
            self.assertEqual(charge.value(), cost)
            self.assertTrue(fight.is_stunned(pilot))
        present = tuple(bot for bot in match.bots if not bot.detached_bomb)
        for _ in range(5):
            run.step({pilot: (0, 0, 32)})
            self.assertEqual(tuple(bot for bot in match.bots if not bot.detached_bomb), present)
            self.assertEqual(charge.value(), cost)
            self.assertIs(fight.player_target(pilot).owner, actor)
        mei.spawned = False
        while fight.tick * 16 < frost.frozen_until:
            run.step({})
        self.assertFalse(fight.is_stunned(actor))
        self.assertEqual(charge.value(), cost)
        run.step({pilot: (0, 0, 32)})
        run.step({})
        self.assertEqual(charge.value(), 0)
        if pilot_form:
            for _ in range(120):
                if pilot.controlled_mech is not None:
                    break
                run.step({})
            self.assertIsNotNone(pilot.controlled_mech)
            self.assertIsNot(pilot.controlled_mech, mech)
        else:
            self.assertTrue(mech.detached_bomb)
            self.assertIsNone(pilot.controlled_mech)

    def test_two_actual_mei_sprays_stack_only_when_configured(self):
        for pilot_form in (False, True):
            for stacking in (False, True):
                with self.subTest(pilot_form=pilot_form, stacking=stacking):
                    self.check_two_mei_sprays(pilot_form, stacking)

    def check_two_mei_sprays(self, pilot_form, stacking):
        run, _, first, second, target, health = ana_tests.DvaAnaTests().scene(pilot_form=pilot_form)
        fight = run.match.combat
        health.heal(350)
        second.team = first.team
        x, y, z = target.feet
        for shooter, offset in ((first, -1), (second, 1)):
            self.assertTrue(run.match.switch_hero(shooter, find_hero("mei")))
            shooter.body_script.component.set(combat.FROST_MINIMUM, .55)
            shooter.body_script.component.set(combat.FROST_STACKING, stacking)
            shooter.mover.position[:] = (x + offset, y, z + 4)
            shooter.mover.body = None
        for _ in range(30):
            run.step({})
        commands = {shooter: (*aim(fight._eye(shooter), (x, y + 1, z)), 1) for shooter in (first, second)}
        for _ in range(35):
            run.step(commands)
            frost = fight.frost.get(target.entity)
            if frost is not None and len(frost.sources) == 2:
                break
        self.assertEqual(set(frost.sources), {first.body, second.body})
        self.assertEqual([len(shooter.client.messages(combat.HIT)) for shooter in (first, second)], [1, 1])
        self.assertAlmostEqual(frost.progress(fight.tick * 16), 1.0 if stacking else .55)
        self.assertEqual(fight.is_stunned(target.owner), stacking)
        if stacking:
            self.assertEqual(frost.frozen_until - fight.tick * 16, 1300)

    def test_excluded_projectile_and_melee_bypass_matrix(self):
        run, mech, enemy, vitals = self.prepared()
        fight = run.match.combat
        self.assertTrue(self.matrix(mech).state(3).active)
        rocket = Rocket(enemy, enemy.team, (0, fight._eye(mech)[1], 2), (0, 0, -1),
                        200, 1, HitDef(amount=20), None, definition=FROST)
        fight.rockets.append(rocket)
        fight._fly(rocket)
        self.assertLess(vitals.total, 600)
        before = vitals.total
        enemy.mover.position = [0, 1, 1]
        enemy.mover.body = None
        for _ in range(25):
            run.step({mech: (0, 0, 2), enemy: (-32768, 0, 0x800)})
        self.assertLess(vitals.total, before)

    def test_absorption_destroys_confirmed_copy_without_explosion_release(self):
        run, _, enemy, _ = self.prepared()
        shot = projectiles.Shot(enemy, enemy.body, HELIX, run.frame, 1, 1, (1, 1))
        arrival = projectiles._ProjectileArrival(received=True)
        shot.created[0xA0001234] = arrival
        run.match.combat.projectiles.shots.append(shot)
        enemy.client.entities.clear()
        run.match.combat.projectiles.absorbed(shot)
        self.assertNotIn(shot, run.match.combat.projectiles.shots)
        [destroy] = enemy.client.entities
        self.assertEqual(destroy.entity, 0xA0001234)
        self.assertIsNone(destroy.ch2)


if __name__ == "__main__":
    unittest.main()
