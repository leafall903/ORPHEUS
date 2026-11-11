"""Opportunist eligibility in the server match snapshot."""

import math
import unittest
from dataclasses import replace

import test_game_sombra_combat as scenes
import test_game_sombra_npc as actual_npcs
import test_game_sombra_reaper_blossom as reaper
import test_game_sombra_shadow_step as shadow_step
import test_game_sombra_sigma as sigma
import test_game_sombra_tracer_recall as tracer_recall
import test_game_sombra_visibility as visibility

from ow174.game import npc, phase_defenses
from ow174.game.body_vm_snapshot import export_component_vm
from ow174.game.commands import FIRE
from ow174.game.content import SOLDIER
from ow174.game.world import SHIELDS_POOL


def contacts(fixture):
    row = next(row for row in fixture.run.match.snapshot()["players"] if row["name"] == fixture.a.name)
    return row.get("opportunist")


class SombraOpportunistTests(unittest.TestCase):
    def test_player_health_threshold_and_cover_are_independent(self):
        fixture = scenes.SombraCombatTests()
        fixture.setUp()
        fight = fixture.run.match.combat
        target = next(target for target in fight._targets() if target.owner is fixture.b)
        self.assertEqual(contacts(fixture), [])
        fixture.press(FIRE)
        self.assertEqual(contacts(fixture), [
            {"entity": target.entity, "outline": False, "health_bar": True},
        ])
        fixture.wall()
        self.assertEqual(contacts(fixture), [])
        vitals = fight.vitals_of(target)
        vitals.damage(vitals.total - 100)
        self.assertEqual(contacts(fixture), [
            {"entity": target.entity, "outline": True, "health_bar": False},
        ])
        vitals.heal(1)
        self.assertEqual(contacts(fixture), [])
        fixture.geometry()
        self.assertEqual(contacts(fixture), [
            {"entity": target.entity, "outline": False, "health_bar": True},
        ])
        vitals.heal(200)
        self.assertEqual(contacts(fixture), [])

    def test_allied_dead_and_replaced_bodies_leave_view(self):
        fixture = scenes.SombraCombatTests()
        fixture.setUp()
        match = fixture.run.match
        match.game_map = replace(match.game_map, free_for_all=False)
        target = next(target for target in match.combat._targets() if target.owner is fixture.b)
        vitals = match.combat.vitals_of(target)
        vitals.damage(120)
        self.assertTrue(contacts(fixture))
        enemy_team = fixture.b.team
        fixture.b.team = fixture.a.team
        self.assertEqual(contacts(fixture), [])
        fixture.b.team = enemy_team
        vitals.damage(vitals.total - 4)
        fixture.press(FIRE)
        self.assertTrue(vitals.dead)
        self.assertEqual(contacts(fixture), [])
        self.assertTrue(match.switch_hero(fixture.b, scenes.heroes()[SOLDIER]))
        self.assertEqual(contacts(fixture), [])
        self.assertTrue(match.switch_hero(fixture.a, scenes.heroes()[SOLDIER]))
        self.assertIsNone(contacts(fixture))

    def test_active_camo_uses_the_existing_proximity_gate(self):
        fixture = visibility.SombraVisibilityTests().scene(4)
        fight = fixture.run.match.combat
        target = next(target for target in fight._targets() if target.owner is fixture.b)
        fight.vitals_of(target).damage(120)
        self.assertEqual(contacts(fixture), [])
        visibility.move(fixture.b, 2)
        fixture.press(0, 4)
        self.assertEqual(contacts(fixture), [
            {"entity": target.entity, "outline": True, "health_bar": True},
        ])

    def test_snapshot_is_read_only_and_dead_viewer_loses_contacts(self):
        fixture = scenes.SombraCombatTests()
        fixture.setUp()
        match = fixture.run.match
        fight = match.combat
        targets = {target.entity: target for target in fight._targets()}
        vitals = fight.vitals_of(targets[fixture.b.body])
        vitals.damage(120)
        scripts = [p.body_script.component for p in (fixture.a, fixture.b)]
        before = [export_component_vm(component) for component in scripts]
        first = match.snapshot()
        self.assertEqual(match.snapshot(), first)
        self.assertTrue(contacts(fixture))
        self.assertTrue([export_component_vm(component) for component in scripts] == before)

        observer = fight.vitals_of(targets[fixture.a.body])
        observer.damage(observer.total - 4)
        fixture.press(FIRE, shooter=fixture.b, target=fixture.a)
        self.assertTrue(observer.dead)
        self.assertEqual(contacts(fixture), [])

    def test_health_eligibility_persists_during_reconstructed_body_silence(self):
        for button, frames in ((2, 82), (32, 50)):
            with self.subTest(button=button):
                f = scenes.SombraCombatTests()
                f.setUp()
                match = f.run.match
                self.assertTrue(match.switch_hero(f.b, scenes.heroes()[scenes.SOMBRA]))
                visibility.move(f.b, 1.5)
                f.press(0, 50)
                f.press(FIRE)
                before = contacts(f)
                self.assertEqual(before, [{"entity": f.b.body, "outline": False, "health_bar": True}])
                if button == 32:
                    f.b.body_script.set_ult_charge(1400)
                f.press(button, frames, shooter=f.b, target=f.a)
                effect, = match.combat.hacks[f.a.body]
                self.assertEqual(effect.application.source_entity(), f.b.body)
                self.assertEqual(contacts(f), before)

    def test_actual_wraith_and_fade_hide_low_health_contact_until_phase_ends(self):
        for hero in phase_defenses.PHASES:
            with self.subTest(hero=hero):
                f = scenes.SombraCombatTests()
                f.setUp()
                match = f.run.match
                self.assertTrue(match.switch_hero(f.b, scenes.heroes()[hero]))
                visibility.move(f.b, 3)
                f.press(0, 50)
                target = next(target for target in match.combat._targets() if target.owner is f.b)
                vitals = match.combat.vitals_of(target)
                vitals.damage(vitals.maximum / 2)
                contact = {"entity": target.entity, "outline": True, "health_bar": False}
                f.wall()
                self.assertEqual(contacts(f), [contact])

                f.press(8, shooter=f.b, target=f.a)
                self.assertTrue(phase_defenses.active(f.b))
                self.assertEqual(contacts(f), [])
                f.press(0, 210)
                self.assertFalse(phase_defenses.active(f.b))
                self.assertEqual(vitals.total, vitals.maximum / 2)
                self.assertEqual(contacts(f), [contact])
                f.geometry()
                contact["health_bar"] = True
                self.assertEqual(contacts(f), [contact])

    def test_actual_recall_hides_outline_then_uses_rewound_health(self):
        for rewind_wounded in (False, True):
            with self.subTest(rewind_wounded=rewind_wounded):
                f = tracer_recall.SombraTracerRecallTests().arena()
                if not rewind_wounded:
                    f.press(0, 205)
                for _ in range(60):
                    if f.health(f.b) <= 75:
                        break
                    f.press(FIRE)
                wounded = f.health(f.b)
                self.assertGreater(wounded, 0)
                self.assertLessEqual(wounded, 75)
                if rewind_wounded:
                    f.press(0, 205)

                f.wall()
                outline = [{"entity": f.b.body, "outline": True, "health_bar": False}]
                self.assertEqual(contacts(f), outline)
                tracer_recall.rewind(f)
                self.assertTrue(phase_defenses.active(f.b))
                self.assertEqual(contacts(f), [])
                tracer_recall.finish(f)
                self.assertFalse(phase_defenses.active(f.b))
                self.assertEqual(f.health(f.b), wounded if rewind_wounded else 150)
                self.assertEqual(contacts(f), outline if rewind_wounded else [])

    def test_actual_shadow_step_filters_outline_only_during_immunity(self):
        for world_only in (False, True):
            with self.subTest(world_only=world_only):
                setup = shadow_step.SombraShadowStepTests()
                f = setup.arena()
                for _ in range(100):
                    if f.health(f.b) <= 125:
                        break
                    f.press(FIRE)
                wounded = f.health(f.b)
                self.assertGreater(wounded, 0)
                self.assertLessEqual(wounded, 125)
                f.press(0)
                cast = setup.cast(f)
                f.geometry((-4, 0, 7, 4, 0, 7, 4, 4, 7),
                           (-4, 0, 7, 4, 4, 7, -4, 4, 7))
                outline = {"entity": f.b.body, "outline": True, "health_bar": False}
                self.assertEqual(contacts(f), [{**outline, "health_bar": True}])
                for tick, expected in (
                    (cast.immune - 1, [{**outline, "health_bar": True}]),
                    (cast.immune, []),
                    (cast.ends - 1, []),
                    (cast.ends, [outline]),
                ):
                    if world_only:
                        reaper.jump(f, tick)
                    else:
                        f.press(0, tick - f.run.frame)
                    self.assertEqual(contacts(f), expected, tick)
                self.assertLess(math.dist(f.b.mover.position, cast.point), 0.005)
                self.assertEqual(f.health(f.b), wounded)

    def test_sigma_shield_recovery_and_emp_change_outline_eligibility(self):
        f = sigma.SombraSigmaTests().arena(distance=4)
        for _ in range(180):
            if f.health(f.b) <= 200:
                break
            f.press(FIRE)
        wounded = f.health(f.b)
        self.assertGreater(wounded, 0)
        self.assertLessEqual(wounded, 200)
        vitals = f.run.match.combat.vitals[f.b.body]
        self.assertEqual(vitals.maximum, 400)
        self.assertEqual(vitals.now[SHIELDS_POOL], 0)
        f.press(0)
        f.wall()
        outline = {"entity": f.b.body, "outline": True, "health_bar": False}
        self.assertEqual(contacts(f), [outline])

        reaper.jump(f, f.run.frame + 250)
        self.assertGreater(vitals.total, 200)
        self.assertLess(vitals.total, vitals.maximum)
        self.assertEqual(contacts(f), [])
        f.geometry()
        self.assertEqual(contacts(f), [{**outline, "outline": False, "health_bar": True}])
        f.a.body_script.set_ult_charge(1400)
        f.press(0x20)
        f.press(0, 30)
        self.assertIn(f.b.body, f.run.match.combat.hacks)
        self.assertEqual(vitals.now[SHIELDS_POOL], 0)
        self.assertEqual(vitals.total, wounded)
        self.assertEqual(contacts(f), [{**outline, "health_bar": True}])


class SombraNpcOpportunistTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        actual_npcs.SombraActualNpcTests.setUpClass.__func__(cls)

    @classmethod
    def restore_graphs(cls, path):
        actual_npcs.SombraActualNpcTests.restore_graphs(path)

    def test_simultaneous_npcs_update_range_cover_threshold_and_team_independently(self):
        f = scenes.SombraCombatTests()
        f.setUp()
        match = f.run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        f.a.team = 0
        f.b.spawned = False

        bodies = []
        for offset, definition in enumerate(self.catalog.values(), -2):
            bot = npc.spawn(match, definition, (offset * 1.4, 0, 4), team=1)
            vitals = fight.vitals[bot.entity]
            threshold = vitals.maximum / 2
            vitals.damage(vitals.total - threshold - 1)
            bodies.append((bot, vitals, threshold))
        f.press(0, 5)
        expected = {bot.entity: {"entity": bot.entity, "outline": False, "health_bar": True}
                    for bot, _, _ in bodies}
        self.assertEqual(contacts(f), list(expected.values()))

        for bot, vitals, threshold in bodies:
            with self.subTest(npc=bot.name):
                actual_npcs.SombraActualNpcTests.press(f, bot, FIRE)
                self.assertEqual(vitals.total, threshold - 7)
                expected[bot.entity]["outline"] = True
                self.assertEqual(contacts(f), list(expected.values()))
                f.press(0, 10)

        # Include a target beyond weapon reach and behind the viewer.
        for (bot, _, _), z in zip(bodies, (2, 15, 35, 100, -140), strict=True):
            bot.position = (0, 0, z)
        self.assertEqual(contacts(f), list(expected.values()))

        walls = []
        for z in (-0.75, 0.75):
            walls.extend(((-4, 0, z, 4, 0, z, 4, 4, z), (-4, 0, z, 4, 4, z, -4, 4, z)))
        f.geometry(*walls)
        for row in expected.values():
            row["health_bar"] = False
        self.assertEqual(contacts(f), list(expected.values()))

        healed, vitals, threshold = bodies[0]
        vitals.heal(7)
        self.assertEqual(vitals.total, threshold)
        self.assertEqual(contacts(f), list(expected.values()))
        vitals.heal(1)
        self.assertEqual(vitals.total, threshold + 1)
        expected.pop(healed.entity)
        teammate, _, _ = bodies[1]
        teammate.team = f.a.team
        expected.pop(teammate.entity)
        self.assertEqual(contacts(f), list(expected.values()))

        f.geometry()
        for row in expected.values():
            row["health_bar"] = True
        healed_row = {"entity": healed.entity, "outline": False, "health_bar": True}
        self.assertEqual(contacts(f), [healed_row, *expected.values()])
        vitals.heal(vitals.maximum)
        self.assertEqual(contacts(f), list(expected.values()))

    def test_all_five_actual_npc_bodies_use_authoritative_pool_balance(self):
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                fixture, bot = actual_npcs.SombraActualNpcTests.make(self, key, distance=4)
                self.assertEqual(contacts(fixture), [])
                actual_npcs.SombraActualNpcTests.press(fixture, bot, FIRE)
                self.assertEqual(contacts(fixture), [
                    {"entity": bot.entity, "outline": False, "health_bar": True},
                ])
                vitals = fixture.run.match.combat.vitals[bot.entity]
                threshold = vitals.maximum / 2
                vitals.damage(vitals.total - threshold - 1)
                self.assertAlmostEqual(vitals.total, threshold + 1)
                self.assertEqual(contacts(fixture), [
                    {"entity": bot.entity, "outline": False, "health_bar": True},
                ])
                fixture.press(0, 10)
                actual_npcs.SombraActualNpcTests.press(fixture, bot, FIRE)
                self.assertLess(vitals.total, threshold)
                self.assertEqual(contacts(fixture), [
                    {"entity": bot.entity, "outline": True, "health_bar": True},
                ])
                fixture.wall()
                self.assertEqual(contacts(fixture), [
                    {"entity": bot.entity, "outline": True, "health_bar": False},
                ])
                fixture.geometry()
                vitals.damage(vitals.total - 4)
                fixture.press(0, 10)
                actual_npcs.SombraActualNpcTests.press(fixture, bot, FIRE)
                self.assertTrue(vitals.dead)
                self.assertEqual(contacts(fixture), [])
