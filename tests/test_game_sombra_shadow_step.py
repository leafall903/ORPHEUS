"""Actual Shadow Step inputs against Sombra and the target-build placement graph."""

import math
import unittest
from dataclasses import replace

import test_game_sombra_combat as sombra_fixture
import test_game_sombra_grenade as grenade_fixture
import test_game_sombra_npc as npc_fixture
import test_game_sombra_reaper_blossom as reaper_fixture
from test_game_combat import aim
from test_game_sombra_orisa_halt import place

from ow174.game import combat, phase_defenses, shadow_step
from ow174.game.commands import FIRE
from ow174.game.content import SOLDIER, heroes
from ow174.game.server_snapshot import SCHEMA, SCOPE, _Graph, snapshot_digest
from ow174.game.world import HEALTH_POOL


class SombraShadowStepTests(unittest.TestCase):
    def arena(self):
        return reaper_fixture.SombraBlossomTests().arena(4)

    def step(self, fixture, button, point=(2, 0, 10), *, sombra=0):
        fight = fixture.run.match.combat
        face = aim(fight._eye(fixture.b), point)
        x, y, z = fixture.b.mover.position
        enemy = aim(fight._eye(fixture.a), (x, y + 1, z))
        fixture.run.step({fixture.b: (*face, button), fixture.a: (*enemy, sombra)})

    def preview(self, fixture, point=(2, 0, 10)):
        self.step(fixture, 16, point)
        self.step(fixture, 0, point)
        root = shadow_step.root(fixture.b)
        self.assertTrue(root.state(0).active)
        return root

    def cast(self, fixture, point=(2, 0, 10)):
        root = self.preview(fixture, point)
        self.assertTrue(root.vars[983].value())
        self.step(fixture, FIRE, point)
        self.assertTrue(root.state(32).active)
        return fixture.run.match.combat.shadow.casts[fixture.b.body]

    def test_actual_teleport_locks_destination_and_matches_world_only_gap(self):
        for gap in (False, True):
            with self.subTest(gap=gap):
                fixture = self.arena()
                fight = fixture.run.match.combat
                start = tuple(fixture.b.mover.position)
                cast = self.cast(fixture)
                self.assertFalse(phase_defenses.active(fixture.b))
                self.assertEqual(cast.immune - fixture.run.frame, math.ceil(1000 / 16))
                self.assertEqual(cast.ends - fixture.run.frame, math.ceil(1500 / 16))
                if gap:
                    reaper_fixture.jump(fixture, cast.immune - 1)
                else:
                    for _ in range(cast.immune - 1 - fixture.run.frame):
                        self.step(fixture, 0, (-4, 0, 3))
                self.assertFalse(phase_defenses.active(fixture.b))
                self.assertEqual(tuple(fixture.b.mover.position), start)
                if gap:
                    reaper_fixture.jump(fixture, cast.immune)
                else:
                    self.step(fixture, 0, (-4, 0, 3))
                self.assertTrue(phase_defenses.active(fixture.b))
                self.assertEqual(tuple(fixture.b.mover.position), start)
                if gap:
                    reaper_fixture.jump(fixture, cast.ends)
                else:
                    fixture.press(0, cast.ends - fixture.run.frame)
                self.assertFalse(phase_defenses.active(fixture.b))
                self.assertLess(math.dist(tuple(fixture.b.mover.position), (2, 0, 10)), .005)
                self.assertNotIn(fixture.b.body, fight.shadow.casts)
                self.assertFalse(fixture.b.client.messages(combat.HIT))
                root = shadow_step.root(fixture.b)
                fixture.press(0, 5)
                self.step(fixture, 16, (4, 0, 4))
                self.assertFalse(root.state(0).active)
                fixture.press(0, 42)
                self.assertGreater(root.state(26).remaining(), 9)
                self.step(fixture, 16, (4, 0, 4))
                self.assertFalse(root.state(0).active)
                fixture.press(0, 635)
                self.preview(fixture, (4, 0, 4))

    def test_cancel_and_invalid_range_cover_or_occupied_destination(self):
        for gate in ("range", "cover", "body"):
            with self.subTest(gate=gate):
                fixture = self.arena()
                fight = fixture.run.match.combat
                point = (0, 0, 55 if gate == "range" else 10)
                if gate == "cover":
                    fixture.geometry((-3, 0, 7, 3, 0, 7, 3, 4, 7),
                                     (-3, 0, 7, 3, 4, 7, -3, 4, 7))
                if gate == "body":
                    place(fixture.a, point)
                root = self.preview(fixture, point)
                self.assertFalse(root.vars[983].value())
                self.step(fixture, FIRE, point)
                self.assertNotIn(fixture.b.body, fight.shadow.casts)
                self.assertTrue(root.state(0).active)
                self.step(fixture, 0, point)
                self.step(fixture, 2, point)
                self.assertFalse(root.state(0).active)
                self.assertEqual(root.state(26).remaining(), 0)
                self.assertFalse(fixture.b.client.messages(combat.HIT))
                self.preview(fixture, (-5, 0, 3))

    def test_destination_occupied_during_cast_refuses_teleport(self):
        fixture = self.arena()
        start = tuple(fixture.b.mover.position)
        cast = self.cast(fixture)
        place(fixture.a, cast.point)
        reaper_fixture.jump(fixture, cast.ends + 5)
        self.assertEqual(tuple(fixture.b.mover.position), start)
        fixture.press(0, 42)
        self.assertGreater(shadow_step.root(fixture.b).state(26).remaining(), 9)

    def test_first_fresh_press_after_long_world_only_gap_can_select_again(self):
        fixture = self.arena()
        cast = self.cast(fixture)
        root = shadow_step.root(fixture.b)
        reaper_fixture.jump(fixture, cast.ends + 850)
        self.assertLess(math.dist(tuple(fixture.b.mover.position), (2, 0, 10)), .005)
        self.step(fixture, 16, (4, 0, 4))
        self.assertEqual(root.state(26).remaining(), 0)
        self.assertTrue(root.state(0).active)

    def test_actual_hack_and_emp_interrupt_vulnerable_cast(self):
        for button in (2, 32):
            with self.subTest(button=button):
                fixture = self.arena()
                fight = fixture.run.match.combat
                start = tuple(fixture.b.mover.position)
                cast = self.cast(fixture)
                if button == 32:
                    fixture.a.body_script.set_ult_charge(1400)
                for index in range(59):
                    fixture.press(button if button == 2 or index == 0 else 0)
                    if fixture.b.body in fight.hacks:
                        break
                self.assertIn(fixture.b.body, fight.hacks)
                self.assertLess(fixture.run.frame, cast.immune)
                self.assertFalse(shadow_step.root(fixture.b).state(32).active)
                self.assertNotIn(fixture.b.body, fight.shadow.casts)
                self.assertGreater(shadow_step.root(fixture.b).state(26).remaining(), 9)
                reaper_fixture.jump(fixture, cast.ends + 20)
                self.assertEqual(tuple(fixture.b.mover.position), start)

    def test_actual_hack_cancels_preview_and_silence_blocks_a_fresh_attempt(self):
        fixture = self.arena()
        fight = fixture.run.match.combat
        root = self.preview(fixture)
        for _ in range(65):
            fixture.press(2)
            if fixture.b.body in fight.hacks:
                break
        application, = fight.hacks[fixture.b.body]
        self.assertFalse(root.state(0).active)
        self.assertEqual(root.state(26).remaining(), 0)
        self.step(fixture, 16)
        self.assertFalse(root.state(0).active)
        fixture.press(0, application.until_tick - fixture.run.frame + 1)
        self.preview(fixture)

    def test_immunity_rejects_pistol_hack_and_emp_then_pistol_reacquires(self):
        for button in (FIRE, 2, 32):
            with self.subTest(button=button):
                fixture = self.arena()
                fight = fixture.run.match.combat
                cast = self.cast(fixture)
                # Start EMP early so its actual radial pulse is inside immunity.
                if button == 32:
                    fixture.a.body_script.set_ult_charge(1400)
                    emp = next(i for i in fixture.a.body_script.component.instances.values()
                               if i.graph.index == 0x0B31)
                    pulse = emp.node_state(emp.graph.node(40))
                    fixture.press(0, cast.immune - fixture.run.frame - 12)
                    fixture.press(32)
                    fixture.press(0, 25)
                else:
                    fixture.press(0, cast.immune - fixture.run.frame)
                    fixture.press(button, 12)
                self.assertTrue(phase_defenses.active(fixture.b))
                self.assertEqual(fixture.health(fixture.b), 250)
                self.assertNotIn(fixture.b.body, fight.hacks)
                self.assertFalse(fixture.a.client.messages(combat.HIT))
                if button == 32:
                    self.assertEqual(fixture.a.body_script.component.entity_var(342).value(), 0)
                    self.assertEqual(pulse.counter, 1)
                fixture.press(0, cast.ends - fixture.run.frame + 6)
                self.assertFalse(phase_defenses.active(fixture.b))
                fixture.press(FIRE)
                self.assertEqual(fixture.health(fixture.b), 242)

    def test_actual_quick_melee_hits_before_and_after_shadow_step_immunity(self):
        for immune in (False, True):
            with self.subTest(immune=immune):
                fixture = self.arena()
                fight = fixture.run.match.combat
                cast = self.cast(fixture)
                place(fixture.a, (0, 0, 2.5))
                if immune:
                    reaper_fixture.jump(fixture, cast.immune)
                fixture.press(sombra_fixture.MELEE)
                fixture.press(0, 19)
                self.assertEqual(phase_defenses.active(fixture.b), immune)
                self.assertEqual(fixture.health(fixture.b), 250 if immune else 220)
                self.assertIs(fight.shadow.casts[fixture.b.body], cast)

                reaper_fixture.jump(fixture, cast.ends + 60)
                self.assertFalse(phase_defenses.active(fixture.b))
                self.assertLess(math.dist(fixture.b.mover.position, cast.point), 0.005)
                place(fixture.a, (cast.point[0], 0, cast.point[2] - 1.5))
                fixture.press(sombra_fixture.MELEE)
                fixture.press(0, 19)
                self.assertEqual(fixture.health(fixture.b), 220 if immune else 190)

    def test_nonlethal_hit_continues_and_lethal_hit_retires_confirmed_cast(self):
        fixture = self.arena()
        fight = fixture.run.match.combat
        cast = self.cast(fixture)
        fixture.press(FIRE)
        self.assertEqual(fixture.health(fixture.b), 242)
        self.assertIn(fixture.b.body, fight.shadow.casts)
        fight.vitals[fixture.b.body].now[HEALTH_POOL] = 8
        fixture.press(FIRE, 10)
        self.assertTrue(fight.is_dead(fixture.b))
        self.assertEqual(fight.vitals[fixture.b.body].killer, fixture.a.body)
        reaper_fixture.jump(fixture, cast.ends + 1)
        self.assertNotIn(fixture.b.body, fight.shadow.casts)
        self.assertLess(math.dist(tuple(fixture.b.mover.position), (0, 0, 4)), .005)

    def test_actual_flashbang_and_sleep_control_respect_shadow_immunity(self):
        for hero, button, damage in ((0x02E0000000000042, 16, 25), (0x02E000000000013B, 8, 5)):
            for immune in (False, True):
                with self.subTest(hero=hero, immune=immune):
                    fixture = self.arena()
                    match = fixture.run.match
                    fight = match.combat
                    match.game_map = replace(match.game_map, free_for_all=False)
                    fixture.a.team, fixture.b.team = 0, 1
                    caster = grenade_fixture.SombraGrenadeTests().join_ally(
                        fixture, hero, (0, 0, 1.5),
                    )
                    cast = self.cast(fixture)
                    start = tuple(fixture.b.mover.position)
                    if immune:
                        fixture.press(0, cast.immune - fixture.run.frame)
                    angle = aim(fight._eye(caster), (0, 1, 4))
                    fixture.run.step({caster: (*angle, button)})
                    launched = any(rocket.shooter is caster for rocket in fight.rockets)
                    for _ in range(45):
                        if fight.is_stunned(fixture.b) or fixture.run.frame >= cast.ends + 5:
                            break
                        fixture.run.step({caster: (*angle, 0)})
                        launched |= any(rocket.shooter is caster for rocket in fight.rockets)
                    self.assertTrue(launched)
                    self.assertEqual(fight.is_stunned(fixture.b), not immune)
                    self.assertEqual(fixture.health(fixture.b), 250 if immune else 250 - damage)
                    if immune:
                        self.assertLess(math.dist(tuple(fixture.b.mover.position), (2, 0, 10)), .005)
                    else:
                        self.assertNotIn(fixture.b.body, fight.shadow.casts)
                        self.assertEqual(tuple(fixture.b.mover.position), start)

    def test_snapshot_captures_pending_point_and_replacement_retires_it(self):
        fixture = self.arena()
        match = fixture.run.match
        fight = match.combat
        cast = self.cast(fixture)
        old = fixture.b.body

        def digest():
            graph = _Graph({"combat": fight}, None)
            roots = {"shadow": graph.value(fight.shadow)}
            return snapshot_digest({"schema": SCHEMA, "scope": SCOPE, "restorable": False,
                                    "roots": roots, "nodes": graph.finish(), "assets": []})

        before = digest()
        cast.point = (3, 0, 10)
        self.assertNotEqual(before, digest())
        self.assertTrue(match.switch_hero(fixture.b, heroes()[SOLDIER]))
        self.assertNotIn(old, fight.shadow.casts)
        reaper_fixture.jump(fixture, cast.ends + 5)
        self.assertFalse(phase_defenses.active(fixture.b))


class NpcShadowStepTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        npc_fixture.SombraActualNpcTests.setUpClass.__func__(cls)

    @classmethod
    def restore_graphs(cls, path):
        npc_fixture.SombraActualNpcTests.restore_graphs(path)

    def test_actual_all_five_npc_bodies_occupy_landing_then_allow_it_when_clear(self):
        inputs = SombraShadowStepTests()
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                fixture, bot = npc_fixture.SombraActualNpcTests.make(self, key, distance=6)
                reaper_fixture.prepare(fixture, 4)
                point = (2, 0, 10)
                bot.position = point
                root = inputs.preview(fixture, point)
                self.assertFalse(root.vars[983].value())
                inputs.step(fixture, FIRE, point)
                self.assertTrue(root.state(0).active)
                self.assertFalse(root.state(32).active)
                bot.position = (6, 0, 1)
                inputs.step(fixture, 0, point)
                self.assertTrue(root.vars[983].value())
                inputs.step(fixture, FIRE, point)
                fight = fixture.run.match.combat
                cast = fight.shadow.casts[fixture.b.body]
                reaper_fixture.jump(fixture, cast.ends + 1)
                self.assertLess(math.dist(tuple(fixture.b.mover.position), point), .005)
