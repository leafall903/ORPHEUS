"""Roadhog's real Breather cast versus Sombra combat and world timing."""

import math
import unittest

import test_game_sombra_ana_rifle as rifle_fixture
import test_game_sombra_grenade as grenade_fixture
import test_game_sombra_nano as nano_fixture
import test_game_sombra_npc as npc_fixture
import test_game_sombra_roadhog_hook as hook_fixture
from test_game_combat import SOLDIER, aim

from ow174.game import combat, npc_control, roadhog, stats
from ow174.game.commands import FIRE, Command
from ow174.game.content import heroes
from ow174.game.script.driver import ULT_CHARGE
from ow174.game.server_snapshot import SCHEMA, SCOPE, _Graph, snapshot_digest
from ow174.game.world import HEALTH_POOL


class SombraRoadhogBreatherTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rifle_fixture.SombraAnaRifleTests.setUpClass.__func__(cls)

    @classmethod
    def restore_graphs(cls, path):
        rifle_fixture.SombraAnaRifleTests.restore_graphs(path)

    def arena(self, health=200):
        f = hook_fixture.SombraRoadhogHookTests().arena()
        fight = f.run.match.combat
        target = rifle_fixture.SombraAnaRifleTests.target(fight, f.b)
        vitals = fight.vitals_of(target)
        vitals.now[HEALTH_POOL] = health
        return f, fight, target, vitals

    def cast(self, f, fight):
        f.press(16, shooter=f.b, target=f.a)
        return fight.breathers[f.b.body]

    def test_authored_heal_windup_amount_and_incoming_damage_boundary(self):
        f, fight, target, vitals = self.arena()
        f.press(0)
        f.a.body_script.set_ult_charge(0)
        state = self.cast(f, fight)
        cast = f.run.frame
        self.assertEqual(state.healing_start_ms, cast * roadhog.FRAME_MS + 500)
        self.assertEqual(state.end_ms, cast * roadhog.FRAME_MS + 2000)
        self.assertEqual(state.total_healing, 300)
        self.assertEqual(roadhog.incoming_scale(fight, target), 0.5)
        charge = f.a.body_script.component.entity_var(ULT_CHARGE)
        initial_charge = charge.value()
        f.press(FIRE)
        self.assertEqual(vitals.total, 196)
        self.assertEqual(charge.value(), initial_charge + 3)
        f.press(0, 29)
        self.assertEqual(vitals.total, 196)
        f.press(0)
        self.assertAlmostEqual(vitals.total, 198.4, places=3)
        f.press(0, 94)
        self.assertEqual(vitals.total, 496)
        self.assertEqual(f.b.stats.value(stats.HEALING), 300)
        self.assertEqual(roadhog.incoming_scale(fight, target), 1)
        f.press(FIRE)
        self.assertEqual(vitals.total, 488)
        self.assertEqual(charge.value(), initial_charge + 9)
        self.assertEqual(f.health(f.a), 200)

    def test_heals_through_world_gap_once_and_respects_effective_pool_cap(self):
        for catchup in (False, True):
            for health in (200, 550):
                with self.subTest(catchup=catchup, health=health):
                    f, fight, target, vitals = self.arena(health)
                    self.cast(f, fight)
                    if catchup:
                        f.run.frame += 180
                        f.run.match.update(f.run.frame * combat.TICK, f.run.frame)
                        f.run.match.update(f.run.frame * combat.TICK, f.run.frame)
                    else:
                        f.press(0, 180)
                    self.assertEqual(vitals.total, min(600, health + 300))
                    self.assertEqual(roadhog.incoming_scale(fight, target), 1)
                    f.press(0, 12)
                    self.assertEqual(vitals.total, min(600, health + 300))
                    self.assertEqual(f.b.stats.value(stats.HEALING), min(600 - health, 300))
                    self.assertNotIn(f.b.body, fight.breathers)

    def test_sombra_hack_and_emp_end_active_heal_and_mitigation(self):
        for button in (2, 32):
            with self.subTest(button=button):
                f, fight, target, vitals = self.arena()
                state = self.cast(f, fight)
                f.press(0, 45)
                self.assertGreater(vitals.total, 200)
                if button == 32:
                    f.a.body_script.set_ult_charge(1400)
                for offset in range(65):
                    f.press(button if button == 2 or offset == 0 else 0)
                    if f.b.body in fight.hacks:
                        break
                effect, = fight.hacks[f.b.body]
                self.assertEqual(effect.application.source_entity(), f.a.body)
                self.assertTrue(state.cancelled)
                self.assertEqual(roadhog.incoming_scale(fight, target), 1)
                stopped = vitals.total
                self.assertLess(stopped, 500)
                f.press(0, 80)
                self.assertAlmostEqual(vitals.total, stopped)
                f.press(FIRE)
                self.assertAlmostEqual(vitals.total, stopped - 8)
                f.press(16, shooter=f.b, target=f.a)
                f.press(0, 125)
                self.assertAlmostEqual(vitals.total, stopped - 8)

    def test_antiheal_boundary_uses_each_tick_even_when_world_frames_are_omitted(self):
        for catchup in (False, True):
            with self.subTest(catchup=catchup):
                f, fight, target, vitals = self.arena()
                scene = grenade_fixture.SombraGrenadeTests()
                ana = scene.join_ally(f, grenade_fixture.ANA, (4, 0, 8))
                scene.throw(f.run, ana, f.b)
                deadline = vitals.healing_block_until
                self.assertEqual(vitals.total, 150)
                f.press(0, deadline - 64 - f.run.frame - 1)
                state = self.cast(f, fight)
                denied = state.cumulative_healing(deadline * roadhog.FRAME_MS)
                self.assertGreater(denied, 0)
                if catchup:
                    f.run.frame += 170
                    f.run.match.update(f.run.frame * combat.TICK, f.run.frame)
                else:
                    f.press(0, 170)
                self.assertAlmostEqual(vitals.total, 450 - denied)
                self.assertEqual(roadhog.incoming_scale(fight, target), 1)

    def test_authored_cooldown_refuses_recast_then_allows_fresh_breather(self):
        f, fight, _, vitals = self.arena(100)
        state = self.cast(f, fight)
        f.press(0, 130)
        self.assertEqual(vitals.total, 400)
        root = next(i for i in f.b.body_script.component.instances.values()
                    if i.graph.index == roadhog.BREATHER)
        ability = root.states[0]
        self.assertEqual(root.find_var(0, 61).base, 8)
        self.assertGreater(ability.remaining(), 0)
        f.press(16, shooter=f.b, target=f.a)
        f.press(0, 10)
        self.assertNotIn(f.b.body, fight.breathers)
        f.press(0, math.ceil(ability.remaining() / combat.TICK) + 2)
        fresh = self.cast(f, fight)
        self.assertNotEqual(state.activation, fresh.activation)
        f.press(0, 130)
        self.assertEqual(vitals.total, 600)
        self.assertEqual(f.b.stats.value(stats.HEALING), 500)

    def test_world_only_breather_preserves_cancel_and_cooldown_boundary(self):
        for emp in (False, True):
            samples = []
            for gap in (False, True):
                with self.subTest(emp=emp, gap=gap):
                    f, fight, target, vitals = self.arena(100)
                    state = self.cast(f, fight)
                    root = next(i for i in f.b.body_script.component.instances.values()
                                if i.graph.index == roadhog.BREATHER)
                    ability = root.states[0]
                    stopped = state.end_ms // roadhog.FRAME_MS
                    if emp:
                        f.press(0, 45)
                        f.a.body_script.set_ult_charge(1400)
                        f.press(32)
                        stopped = f.run.frame + 21
                    sample = stopped + 55
                    match = f.run.match
                    if gap:
                        match.update(sample * combat.TICK, sample)
                    else:
                        f.press(0, sample - f.run.frame - 1)
                    self.assertEqual(state.cancelled, emp)
                    self.assertEqual(roadhog.incoming_scale(fight, target), 1)
                    self.assertNotIn(f.b.body, fight.breathers)
                    self.assertEqual(ability.last // roadhog.FRAME_MS, stopped)
                    self.assertGreater(ability.remaining(), 0)
                    healed = vitals.total
                    self.assertEqual(healed, 217.599609375 if emp else 400)
                    self.assertEqual(f.b.stats.value(stats.HEALING), healed - 100)
                    samples.append(ability.remaining())

                    ready = stopped + 500
                    if gap:
                        match.update(ready * combat.TICK, ready)
                        mover = f.b.mover
                        f.b.take_commands([Command(ready, yaw=mover.yaw, pitch=mover.pitch, buttons=16)])
                    else:
                        f.press(0, ready - f.run.frame - 1)
                        f.press(16, shooter=f.b, target=f.a)
                    fresh = fight.breathers[f.b.body]
                    self.assertNotEqual(fresh.activation, state.activation)
                    self.assertEqual(roadhog.incoming_scale(fight, target), 0.5)
                    self.assertEqual(vitals.total, healed)
            self.assertEqual(samples[:1], samples[1:])

    def test_real_helix_uses_impact_side_of_expiry_with_or_without_world_gap(self):
        for launch_age in (100, 122):
            for catchup in (False, True):
                with self.subTest(launch_age=launch_age, catchup=catchup):
                    f, fight, _, vitals = self.arena()
                    scene = grenade_fixture.SombraGrenadeTests()
                    soldier = scene.join_ally(f, SOLDIER, (0, 0, 0))
                    state = self.cast(f, fight)
                    f.press(0, launch_age - 1)
                    f.run.step({soldier: (*aim(fight._eye(soldier), (0, 1, 8)), 2)})
                    rocket, = fight.rockets
                    f.press(0)
                    if catchup:
                        f.run.frame += 60
                        f.run.match.update(f.run.frame * combat.TICK, f.run.frame)
                    else:
                        f.press(0, 60)
                    self.assertNotIn(rocket, fight.rockets)
                    self.assertGreater(f.run.frame * roadhog.FRAME_MS, state.end_ms)
                    self.assertEqual(vitals.total, 440 if launch_age == 100 else 380)

    def test_nano_and_breather_mitigation_compose_on_actual_sombra_pistol(self):
        f, fight, target, vitals = self.arena(100)
        scene = grenade_fixture.SombraGrenadeTests()
        ana = scene.join_ally(f, grenade_fixture.ANA, (4, 0, 8))
        ana.team = f.b.team
        nano_fixture.SombraNanoTests.cast(f.run, ana, target)
        f.press(0, 12)
        self.assertEqual(vitals.total, 350)
        self.cast(f, fight)
        f.press(FIRE)
        self.assertEqual(vitals.total, 348)
        f.press(0, 130)
        self.assertGreater(vitals.nano_until, f.run.frame)
        f.press(FIRE)
        self.assertEqual(vitals.total, 596)

    def test_actual_lethal_shot_and_replacement_retire_remaining_breather(self):
        for lethal in (False, True):
            with self.subTest(lethal=lethal):
                f, fight, _, vitals = self.arena(4 if lethal else 200)
                old = f.b.body
                self.cast(f, fight)
                if lethal:
                    f.press(FIRE)
                    self.assertTrue(vitals.dead)
                    self.assertEqual(vitals.killer, f.a.body)
                else:
                    self.assertTrue(f.run.match.switch_hero(f.b, f.b.hero))
                    self.assertNotEqual(f.b.body, old)
                self.assertNotIn(old, fight.breathers)
                f.press(0, 135)
                self.assertEqual(f.health(f.b), 0 if lethal else 600)

    def test_active_breather_snapshot_captures_delivered_clock(self):
        f, fight, _, _ = self.arena()
        state = self.cast(f, fight)
        f.press(0, 50)

        def digest():
            stored = _Graph({"owner": f.b}, None)
            roots = {"breathers": stored.value(fight.breathers)}
            return snapshot_digest({
                "schema": SCHEMA, "scope": SCOPE, "restorable": False,
                "roots": roots, "nodes": stored.finish(), "assets": [],
            })

        first = digest()
        self.assertEqual(digest(), first)
        state.last_ms -= roadhog.FRAME_MS
        self.assertNotEqual(digest(), first)

    def test_actual_attacks_from_each_npc_use_breather_reduction(self):
        fixture = npc_fixture.SombraActualNpcTests()
        fixture.catalog = self.catalog
        for key, nominal in (("1", 20), ("2", 2), ("3", 100), ("4", 25), ("5", 5)):
            with self.subTest(npc=self.catalog[key].name):
                if key == "4":
                    f, bot = fixture.make(key, distance=1.5)
                else:
                    f, bot = fixture.weapon_impact_arena(key)
                match = f.run.match
                fight = match.combat
                self.assertTrue(match.switch_hero(f.a, heroes()[roadhog.HERO]))
                f.a.mover.position[:] = (0, 0, -5 if key == "3" else 0)
                f.a.mover.body = None
                f.b.spawned = False
                f.press(0, 45)
                f.a.body_script.ult_seconds = 0
                f.a.body_script.set_ult_charge(0)
                target = rifle_fixture.SombraAnaRifleTests.target(fight, f.a)
                vitals = fight.vitals_of(target)
                vitals.now[HEALTH_POOL] = 200
                x, y, z = bot.position
                facing = aim(fight._eye(f.a), (x, y + 1, z))
                f.run.step({f.a: (*facing, 0)})
                if key == "3":
                    self.assertEqual(npc_control.start(match, f.a), bot.entity)
                    for _ in range(330):
                        if fight.rockets:
                            break
                        f.run.step({f.a: (*facing, 0)})
                    self.assertTrue(any(rocket.shooter is bot for rocket in fight.rockets))
                    self.assertEqual(vitals.total, 200)
                f.run.step({f.a: (*facing, 16)})
                self.assertEqual(roadhog.incoming_scale(fight, target), 0.5)
                if key != "3":
                    self.assertEqual(npc_control.start(match, f.a), bot.entity)
                loss = 0
                for _ in range(115):
                    before = vitals.total
                    healed_before = f.a.stats.value(stats.HEALING)
                    f.run.step({f.a: (*facing, 0)})
                    healed = f.a.stats.value(stats.HEALING) - healed_before
                    loss = before + healed - vitals.total
                    if loss > combat.EPSILON:
                        break
                self.assertAlmostEqual(loss, nominal / 2)
                charge = f.a.body_script.component.entity_var(ULT_CHARGE).value()
                self.assertAlmostEqual(vitals.ult_healing_deficit + charge, loss, places=4)
                if key == "4":
                    self.assertEqual(fight.pins.get(bot.entity), f.a.body)
                    self.assertEqual(roadhog.incoming_scale(fight, target), 1)
                npc_control.stop(match)

    def test_actual_allied_grenade_amplifies_breather_healing(self):
        f, fight, _, vitals = self.arena(1)
        scene = grenade_fixture.SombraGrenadeTests()
        ana = scene.join_ally(f, grenade_fixture.ANA, (4, 0, 8))
        ana.team = f.b.team
        scene.throw(f.run, ana, f.b)
        self.assertEqual(vitals.total, 101)
        self.assertGreater(vitals.healing_boost_until, fight.tick + 125)
        self.cast(f, fight)
        f.press(0, 130)
        self.assertEqual(vitals.total, 551)
        self.assertEqual(f.b.stats.value(stats.HEALING), 450)

    def test_actual_hook_contact_is_reduced_then_interrupts_breather(self):
        f, fight, target, vitals = self.arena()
        scene = grenade_fixture.SombraGrenadeTests()
        hog = scene.join_ally(f, roadhog.HERO, (0, 0, 0))
        state = self.cast(f, fight)
        f.press(0, 45)
        flight = hook_fixture.SombraRoadhogHookTests().launch(f, hog, (0, 1, 8))
        loss = 0
        for _ in range(25):
            before = vitals.total
            f.press(0)
            if flight.target is not None:
                loss = before - vitals.total
                break
        self.assertIs(flight.target.owner, f.b)
        self.assertEqual(loss, 15)
        self.assertTrue(state.cancelled)
        self.assertEqual(roadhog.incoming_scale(fight, target), 1)
        stopped = vitals.total
        f.press(0, 135)
        self.assertEqual(vitals.total, stopped)

    def test_fresh_breather_after_world_only_gap_across_entire_cooldown(self):
        f, fight, _, vitals = self.arena(100)
        first = self.cast(f, fight)
        f.run.frame += math.ceil(10 / combat.TICK) + 3
        f.run.match.update(f.run.frame * combat.TICK, f.run.frame)
        self.assertEqual(vitals.total, 400)
        fresh = self.cast(f, fight)
        self.assertNotEqual(fresh.activation, first.activation)
        f.press(0, 130)
        self.assertEqual(vitals.total, 600)
        self.assertEqual(f.b.stats.value(stats.HEALING), 500)

    def test_hack_without_roadhog_commands_starts_cooldown_at_interruption(self):
        f, fight, _, vitals = self.arena()
        state = self.cast(f, fight)
        f.press(0, 45)
        match = f.run.match
        yaw, pitch = aim(fight._eye(f.a), (0, 1, 8))
        for _ in range(65):
            f.run.frame += 1
            match.update(f.run.frame * combat.TICK, f.run.frame)
            f.a.take_commands([Command(f.run.frame, yaw=yaw, pitch=pitch, buttons=2, extra=0)])
            if f.b.body in fight.hacks:
                break
        self.assertIn(f.b.body, fight.hacks)
        self.assertTrue(state.cancelled)
        root = next(i for i in f.b.body_script.component.instances.values()
                    if i.graph.index == roadhog.BREATHER)
        self.assertFalse(root.states[1].active)
        self.assertGreater(root.states[0].remaining(), 0)
        before = vitals.total
        f.run.frame += 630
        match.update(f.run.frame * combat.TICK, f.run.frame)
        self.assertEqual(vitals.total, before)
        fresh = self.cast(f, fight)
        self.assertNotEqual(fresh.activation, state.activation)
        f.press(0, 130)
        self.assertEqual(vitals.total, min(600, before + 300))
