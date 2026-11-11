"""Actual Orisa Fortify interactions in the Sombra static match."""

import math
import unittest

import test_game_sombra_combat as scene
import test_game_sombra_grenade as grenade_fixture
import test_game_sombra_sleep as sleep_fixture
from test_game_combat import SOLDIER, aim

from ow174.game import combat, fortify, hook, reinhardt_barrier
from ow174.game.commands import FIRE, Command
from ow174.game.script.driver import ULT_CHARGE, ULT_COST
from ow174.game.server_snapshot import SCHEMA, SCOPE, _Graph, snapshot_digest
from ow174.game.world import ARMOUR_POOL, HEALTH_POOL


class SombraOrisaFortifyTests(unittest.TestCase):
    def arena(self, *, distance=1.5):
        f = scene.SombraCombatTests()
        f.setUp()
        self.assertTrue(f.run.match.switch_hero(f.b, scene.heroes()[fortify.HERO]))
        f.b.mover.position[:] = (0, 0, distance)
        f.b.mover.body = None
        f.a.body_script.ult_seconds = 0
        f.press(0, 45)
        f.a.body_script.set_ult_charge(0)
        return f

    def cast(self, f):
        fight = f.run.match.combat
        f.press(8, shooter=f.b, target=f.a)
        self.assertTrue(fortify.active(fight, f.b))
        self.assertAlmostEqual(fortify.damage_scale(fight, f.b), 0.6)
        return fight.fortifies[f.b.body]

    def test_pistol_head_and_torso_and_quick_melee_resolve_armor_and_charge(self):
        for fortified in (False, True):
            for height, button, normal, reduced in ((0.9, FIRE, 4, 2.4), (1.55, FIRE, 11, 2.4),
                                                    (1.0, scene.MELEE, 25, 13)):
                with self.subTest(fortified=fortified, button=button, height=height):
                    f = self.arena()
                    fight = f.run.match.combat
                    if fortified:
                        self.cast(f)
                    before = f.health(f.b)
                    facing = aim(fight._eye(f.a), (0, height, 1.5))
                    f.run.step({f.a: (*facing, button)})
                    f.press(0, 22)
                    expected = reduced if fortified else normal
                    self.assertAlmostEqual(before - f.health(f.b), expected, places=4)
                    hit, = f.a.client.messages(combat.HIT)
                    flags = hit["+0x78"]["+0x8"]
                    self.assertEqual(bool(flags & combat.CRITICAL), height == 1.55 and not fortified)
                    charge = f.a.body_script.component.entity_var(ULT_CHARGE).value()
                    self.assertAlmostEqual(charge, expected, places=4)

    def test_hack_and_emp_end_fortify_and_deny_reentry_during_silence(self):
        for button in (2, 32):
            with self.subTest(button=button):
                f = self.arena()
                fight = f.run.match.combat
                clock, _, _ = self.cast(f)
                if button == 32:
                    f.a.body_script.set_ult_charge(1400)
                for offset in range(90):
                    f.press(button if button == 2 or offset == 0 else 0)
                    if f.b.body in fight.hacks:
                        break
                effect, = fight.hacks[f.b.body]
                self.assertEqual(effect.application.source_entity(), f.a.body)
                self.assertFalse(clock.active)
                self.assertFalse(fortify.active(fight, f.b))
                self.assertEqual(fortify.damage_scale(fight, f.b), 1)
                f.press(8, shooter=f.b, target=f.a)
                self.assertFalse(clock.active)
                f.press(0, 45)
                self.assertIn(f.b.body, fight.hacks)
                before = f.health(f.b)
                f.press(FIRE)
                self.assertEqual(before - f.health(f.b), 4)

    def test_hack_removes_fortify_for_same_frame_rifle_with_omitted_owner_input(self):
        for idle in (False, True):
            with self.subTest(idle=idle):
                f = self.arena()
                match = f.run.match
                fight = match.combat
                soldier = grenade_fixture.SombraGrenadeTests().join_ally(f, SOLDIER, (3, 0, 0))
                clock, _, _ = self.cast(f)
                hack_yaw, hack_pitch = aim(fight._eye(f.a), (0, 1, 1.5))
                shot_yaw, shot_pitch = aim(fight._eye(soldier), (0, 1, 1.5))
                for _ in range(90):
                    f.run.frame += 1
                    at = f.run.frame
                    match.update(at * combat.TICK, at)
                    f.a.take_commands([Command(at, yaw=hack_yaw, pitch=hack_pitch, buttons=2)])
                    if not idle:
                        f.b.take_commands([Command(at)])
                    if f.b.body in fight.hacks:
                        break
                    soldier.take_commands([Command(at)])
                self.assertIn(f.b.body, fight.hacks)
                self.assertFalse(fortify.active(fight, f.b))
                before = f.health(f.b)
                soldier.take_commands([Command(at, yaw=shot_yaw, pitch=shot_pitch, buttons=FIRE)])
                self.assertEqual(before - f.health(f.b), 15)
                if idle:
                    self.assertEqual(f.b.body_script.frame, at - 1)
                    match.update((at + 41) * combat.TICK, at + 41)
                else:
                    f.press(0, 40)
                ability = clock.instance.state(0)
                self.assertFalse(clock.active)
                self.assertEqual(ability.last // f.b.body_script.frame_ms, at)
                ready = at + 625
                if idle:
                    match.update(ready * combat.TICK, ready)
                    self.assertGreater(ability.remaining(), 0)
                    mover = f.b.mover
                    f.b.take_commands([Command(ready, yaw=mover.yaw, pitch=mover.pitch, buttons=8)])
                else:
                    f.press(0, ready - f.run.frame - 1)
                    self.assertGreater(ability.remaining(), 0)
                    self.cast(f)
                self.assertTrue(fortify.active(fight, f.b))
                self.assertEqual(clock.counter, 2)

    def test_actual_fusion_driver_interrupts_camo_and_hack_and_gates_paid_emp(self):
        for mode in ("camo", "hack", "emp_survives", "emp_lethal"):
            with self.subTest(mode=mode):
                f = self.arena(distance=4)
                fight = f.run.match.combat
                camo = f.camo().state(13)
                channel = f.weapon().state(53)
                held = 0
                if mode == "camo":
                    f.press(8)
                    f.press(0, 60)
                    self.assertTrue(camo.flags & camo.RUNNING)
                elif mode == "hack":
                    f.press(2, 12)
                    self.assertTrue(channel.flags & channel.RUNNING)
                    held = 2
                else:
                    f.health(f.a)
                    fight.vitals[f.a.body].now[HEALTH_POOL] = 11 if mode == "emp_lethal" else 12
                    f.a.body_script.set_ult_charge(1400)
                    f.press(32)
                    self.assertEqual(f.a.body_script.component.entity_var(ULT_CHARGE).value(), 0)
                before = f.health(f.a)
                left = aim(fight._eye(f.a), (0, 1, 4))
                right = aim(fight._eye(f.b), (0, 1, 0))
                f.run.step({f.a: (*left, held), f.b: (*right, FIRE)})
                rocket, = fight.rockets
                self.assertIs(rocket.shooter, f.b)
                self.assertEqual((rocket.direct.amount, rocket.speed), (11, 120))
                f.press(held, 55)
                self.assertEqual(f.health(f.a), before - 11)
                self.assertFalse(camo.flags & camo.RUNNING)
                self.assertFalse(channel.flags & channel.RUNNING)
                self.assertEqual(f.b.body in fight.hacks, mode == "emp_survives")
                if mode == "emp_lethal":
                    self.assertTrue(fight.is_dead(f.a))
                    self.assertEqual(fight.vitals[f.a.body].killer, f.b.body)

    def test_fortify_expires_and_starts_cooldown_with_or_without_owner_input(self):
        for skipped in (False, True):
            with self.subTest(skipped=skipped):
                f = self.arena()
                fight = f.run.match.combat
                clock, _, deadline = self.cast(f)
                start = f.run.frame
                self.assertGreater(deadline - start, 240)
                self.assertLessEqual(deadline - start, 251)
                if skipped:
                    f.run.frame = deadline + 2
                    f.run.match.update(f.run.frame * combat.TICK, f.run.frame)
                else:
                    f.press(0, deadline + 2 - start)
                self.assertFalse(clock.active)
                self.assertNotIn(f.b.body, fight.fortifies)
                ability = clock.instance.state(0)
                self.assertEqual(ability.last // 16, deadline)
                before = f.health(f.b)
                f.press(FIRE)
                self.assertEqual(before - f.health(f.b), 4)
                self.assertGreater(ability.remaining(), 0)
                self.assertLessEqual(ability.remaining(), 10)
                f.press(8, shooter=f.b, target=f.a)
                self.assertFalse(clock.active)
                f.press(0, math.ceil(ability.remaining() / combat.TICK) + 4)
                self.cast(f)
                self.assertEqual(clock.counter, 2)

    def test_world_only_fortify_cooldown_matches_expiry_or_paid_emp(self):
        for emp in (False, True):
            samples = []
            for gap in (False, True):
                with self.subTest(emp=emp, gap=gap):
                    f = self.arena()
                    match = f.run.match
                    fight = match.combat
                    clock, _, stopped = self.cast(f)
                    ability = clock.instance.state(0)
                    if emp:
                        f.a.body_script.set_ult_charge(1400)
                        f.press(32)
                        stopped = f.run.frame + 21
                    sample = stopped + 2
                    if gap:
                        match.update((sample + 1) * combat.TICK, sample + 1)
                    else:
                        f.press(0, sample - f.run.frame)
                    self.assertFalse(fortify.active(fight, f.b))
                    self.assertEqual(ability.last // 16, stopped)
                    self.assertGreater(ability.remaining(), 0)
                    samples.append(ability.remaining())
                    self.assertEqual(f.b.body in fight.hacks, emp)
                    ready = stopped + 625
                    if gap:
                        match.update(ready * combat.TICK, ready)
                        mover = f.b.mover
                        f.b.take_commands([Command(ready, yaw=mover.yaw, pitch=mover.pitch, buttons=8)])
                    else:
                        f.press(0, ready - f.run.frame - 1)
                        self.cast(f)
                    self.assertTrue(fortify.active(fight, f.b))
                    self.assertEqual(clock.counter, 2)
            self.assertEqual(samples[:1], samples[1:])

    def test_actual_helix_uses_impact_side_of_expiry_in_contiguous_and_skipped_world_frames(self):
        for launch_lead, expected in ((14, 62), (5, 110)):
            for skipped in (False, True):
                with self.subTest(launch_lead=launch_lead, skipped=skipped):
                    f = self.arena(distance=8)
                    match = f.run.match
                    fight = match.combat
                    soldier = grenade_fixture.SombraGrenadeTests().join_ally(f, SOLDIER, (0, 0, 0))
                    f.a.mover.position[0] = 4
                    f.a.mover.body = None
                    _, _, deadline = self.cast(f)
                    f.press(0, deadline - launch_lead - f.run.frame - 1)
                    before = f.health(f.b)
                    f.run.step({soldier: (*aim(fight._eye(soldier), (0, 1, 8)), 2)})
                    rocket, = fight.rockets
                    f.press(0)
                    self.assertIn(rocket, fight.rockets)
                    if skipped:
                        f.run.frame += 60
                        match.update(f.run.frame * combat.TICK, f.run.frame)
                    else:
                        f.press(0, 60)
                    self.assertNotIn(rocket, fight.rockets)
                    self.assertAlmostEqual(before - f.health(f.b), expected, places=3)

    def test_actual_sleep_and_chain_hook_deal_damage_without_control(self):
        for kind in ("sleep", "hook"):
            with self.subTest(kind=kind):
                f = self.arena(distance=6)
                fight = f.run.match.combat
                hero = grenade_fixture.ANA if kind == "sleep" else hook.HERO
                attacker = grenade_fixture.SombraGrenadeTests().join_ally(f, hero, (0, 0, 2))
                self.cast(f)
                before = f.health(f.b)
                position = tuple(f.b.mover.position)
                facing = aim(fight._eye(attacker), (0, 1, 6))
                f.run.step({attacker: (*facing, sleep_fixture.DART if kind == "sleep" else 8)})
                for _ in range(45):
                    f.run.step({attacker: (*facing, 0)})
                    self.assertFalse(fight.is_stunned(f.b))
                    self.assertFalse(fight.hooks.controlled(f.b.body))
                self.assertAlmostEqual(before - f.health(f.b), 1.5 if kind == "sleep" else 13, places=4)
                self.assertEqual(tuple(f.b.mover.position), position)
                self.assertTrue(fortify.active(fight, f.b))

    def test_actual_whole_hog_and_graviton_damage_do_not_move_fortified_body(self):
        for graviton in (False, True):
            with self.subTest(graviton=graviton):
                f = self.arena(distance=4)
                fight = f.run.match.combat
                hero = reinhardt_barrier.ZARYA if graviton else hook.HERO
                attacker = grenade_fixture.SombraGrenadeTests().join_ally(f, hero, (0, 0, 0))
                f.a.mover.position[0] = 4
                f.a.mover.body = None
                self.cast(f)
                position = tuple(f.b.mover.position)
                before = f.health(f.b)
                script = attacker.body_script
                script.set_ult_charge(script.component.entity_var(ULT_COST).value())
                facing = aim(fight._eye(attacker), (0, 1, 4))
                f.run.step({attacker: (*facing, 32)})
                for _ in range(80):
                    f.run.step({attacker: (*facing, 0)})
                    self.assertNotIn(f.b.body, fight.impulses.live)
                    self.assertNotIn(f.b.body, fight.gravitons.player_pulls)
                self.assertLess(f.health(f.b), before)
                if graviton:
                    self.assertTrue(fight.gravitons.fields)
                self.assertEqual(tuple(f.b.mover.position), position)

    def test_death_and_body_replacement_retire_stored_fortify(self):
        for lethal in (False, True):
            with self.subTest(lethal=lethal):
                f = self.arena()
                fight = f.run.match.combat
                old = f.b.body
                script = f.b.body_script
                clock, activation, deadline = self.cast(f)

                def digest(fight, clock):
                    stored = _Graph({"clock": clock}, None)
                    roots = {"fortify": stored.value(fight.fortifies)}
                    return snapshot_digest({
                        "schema": SCHEMA, "scope": SCOPE, "restorable": False,
                        "roots": roots, "nodes": stored.finish(), "assets": [],
                    })

                first = digest(fight, clock)
                self.assertEqual(digest(fight, clock), first)
                fight.fortifies[old] = clock, activation, deadline + 1
                self.assertNotEqual(digest(fight, clock), first)
                fight.fortifies[old] = clock, activation, deadline
                if lethal:
                    f.health(f.b)
                    vitals = fight.vitals[old]
                    vitals.now[ARMOUR_POOL] = 0
                    vitals.now[HEALTH_POOL] = 1
                    f.press(FIRE)
                    self.assertTrue(vitals.dead)
                    self.assertEqual(vitals.killer, f.a.body)
                else:
                    self.assertTrue(f.run.match.switch_hero(f.b, f.b.hero))
                    self.assertTrue(script.retired)
                self.assertNotIn(old, fight.fortifies)
                self.assertFalse(fortify.active(fight, f.b))
