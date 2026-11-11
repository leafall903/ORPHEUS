"""Actual Mei weapon and Cryo inputs against Sombra and Talon bodies."""

import math
import unittest
from dataclasses import replace

import test_game_sombra_combat as scene
import test_game_sombra_grenade as grenade_fixture
import test_game_sombra_npc as npc_fixture
from test_game_combat import aim
from test_game_sombra_orisa_halt import place

from ow174.game import combat, mei, npc_control, reinhardt_barrier
from ow174.game.commands import FIRE, Command
from ow174.game.script.runtime import send_instance_message
from ow174.game.server_snapshot import SCHEMA, SCOPE, _Graph, snapshot_digest

MEI = 0x02E00000000000DD


def face(fixture, actor, target, height=0.9):
    mover = target.mover
    x, y, z = mover.position
    return aim(fixture.run.match.combat._eye(actor), (x, y + height, z))


class SombraMeiTests(unittest.TestCase):
    def arena(self, *, distance=3):
        fixture = scene.SombraCombatTests()
        fixture.setUp()
        self.assertTrue(fixture.run.match.switch_hero(fixture.b, scene.heroes()[MEI]))
        place(fixture.a, (0, 0, 0))
        place(fixture.b, (0, 0, distance))
        fixture.a.body_script.ult_seconds = 0
        fixture.a.body_script.set_ult_charge(0)
        fixture.press(0, 45)
        return fixture

    def spray(self, fixture, *, held=1, tail=20):
        facing = face(fixture, fixture.b, fixture.a)
        for frame in range(held + tail):
            fixture.run.step({fixture.b: (*facing, FIRE if frame < held else 0)})

    def pose(self, fixture, *, forward=0, buttons=0, yaw=0):
        run = fixture.run
        run.frame += 1
        run.match.update(run.frame * combat.TICK, run.frame)
        fixture.a.take_commands([Command(run.frame, forward=forward, yaw=yaw, buttons=buttons)])
        fixture.b.take_commands([Command(run.frame)])

    def test_real_spray_slows_and_full_freeze_locks_sombra_until_thaw(self):
        fixture = self.arena()
        fight = fixture.run.match.combat
        self.spray(fixture)
        self.assertEqual(fixture.health(fixture.a), 197.25)
        self.assertAlmostEqual(fight.mei.frost(fixture.a.body)[0], 0.2)
        self.pose(fixture, forward=127)
        self.assertAlmostEqual(fixture.a.mover.values.run_forward, 4.4, places=4)
        facing = face(fixture, fixture.b, fixture.a)
        for _ in range(115):
            fixture.run.step({fixture.b: (*facing, FIRE)})
            sample = fight.mei.frost(fixture.a.body)
            if sample is not None and sample[2]:
                break
        self.assertTrue(fight.is_stunned(fixture.a))
        progress, touched, until, yaw, pitch = sample
        self.assertEqual((progress, until - touched), (1, 1300))
        before = tuple(fixture.a.mover.position)
        ammo = fixture.weapon().vars[53].value()
        fixture.a.body_script.set_ult_charge(1400)
        for _ in range(20):
            self.pose(fixture, forward=127, yaw=12345, buttons=FIRE | 2 | 8 | 16 | 32)
        self.assertEqual(tuple(fixture.a.mover.position), before)
        self.assertEqual((fixture.a.mover.yaw, fixture.a.mover.pitch), (yaw, pitch))
        self.assertEqual(fixture.weapon().vars[53].value(), ammo)
        self.assertEqual(fixture.a.body_script.component.entity_var(342).value(), 1400)
        self.assertFalse(fight.translocators.entries)
        while fixture.run.frame < math.ceil(until / 16):
            self.pose(fixture)
        self.assertFalse(fight.is_stunned(fixture.a))
        self.pose(fixture, forward=127)
        self.assertGreater(fixture.a.mover.position[2], before[2])
        self.assertAlmostEqual(fixture.a.mover.values.run_forward, 5.5)

    def test_actual_spray_damage_ends_camo(self):
        fixture = self.arena()
        fixture.press(8)
        fixture.press(0, 60)
        camo = fixture.camo().state(13)
        self.assertTrue(camo.flags & camo.RUNNING)
        self.spray(fixture, tail=70)
        self.assertEqual(fixture.health(fixture.a), 197.25)
        self.assertFalse(camo.flags & camo.RUNNING)
        self.assertGreater(camo.remaining(), 0)

    def test_frozen_sombra_cannot_recall_existing_beacon_until_thaw(self):
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
        facing = face(fixture, fixture.b, fixture.a)
        for _ in range(115):
            fixture.run.step({fixture.b: (*facing, FIRE)})
            if fight.is_stunned(fixture.a):
                break
        self.assertTrue(fight.is_stunned(fixture.a))
        fixture.press(16)
        fixture.press(0, 12)
        self.assertIn(beacon.entity, fight.translocators.entries)
        until = fight.control_deadline(fixture.a.body)
        fixture.press(0, until - fixture.run.frame + 3)
        self.assertFalse(fight.is_stunned(fixture.a))
        fixture.press(16)
        fixture.press(0, 45)
        self.assertFalse(fight.translocators.entries)
        self.assertAlmostEqual(fixture.a.mover.position[0], beacon.position[0], places=2)
        self.assertAlmostEqual(fixture.a.mover.position[2], beacon.position[2], places=2)

    def test_spray_refuses_team_cover_and_range_and_frost_expires_without_commands(self):
        for mode in ("team", "cover", "range", "clear"):
            with self.subTest(mode=mode):
                fixture = self.arena(distance=15 if mode == "range" else 3)
                match = fixture.run.match
                fight = match.combat
                if mode == "team":
                    match.game_map = replace(match.game_map, free_for_all=False)
                    fixture.a.team = fixture.b.team = 0
                if mode == "cover":
                    fixture.geometry((-3, 0, 1.5, 3, 0, 1.5, 3, 4, 1.5),
                                     (-3, 0, 1.5, 3, 4, 1.5, -3, 4, 1.5))
                self.spray(fixture)
                self.assertEqual(fixture.health(fixture.a), 197.25 if mode == "clear" else 200)
                self.assertEqual(fight.mei.frost(fixture.a.body) is not None, mode == "clear")
                fixture.run.frame += 90
                match.update(fixture.run.frame * combat.TICK, fixture.run.frame)
                self.assertIsNone(fight.mei.frost(fixture.a.body))
                if mode == "clear":
                    self.assertFalse(fixture.a.body_script.component.entity_var(2427).value())

    def test_projected_barrier_cleanses_existing_frost_and_stops_the_next_spray(self):
        fixture = self.arena()
        match = fixture.run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        fixture.a.team, fixture.b.team = 0, 1
        zarya = grenade_fixture.SombraGrenadeTests().join_ally(
            fixture, reinhardt_barrier.ZARYA, (4, 0, 0),
        )
        self.spray(fixture)
        self.assertIsNotNone(fight.mei.frost(fixture.a.body))
        fixture.run.step({zarya: (*face(fixture, zarya, fixture.a), 16)})
        bubble, = fight.barriers.values()
        self.assertIs(bubble.protected, fixture.a)
        self.spray(fixture)
        self.assertIsNone(fight.mei.frost(fixture.a.body))
        self.assertEqual(fixture.health(fixture.a), 197.25)
        self.assertEqual(bubble.vitals.total, 197.25)

    def test_airborne_spray_has_same_impact_tick_across_world_gap(self):
        outcomes = []
        for gap in (False, True):
            fixture = self.arena()
            match = fixture.run.match
            fight = match.combat
            direction = face(fixture, fixture.b, fixture.a)
            fixture.run.step({fixture.b: (*direction, FIRE)})
            spray, = fight.rockets
            self.assertEqual(spray.spray_limit, 99)
            if gap:
                fixture.run.frame += 20
                match.update(fixture.run.frame * combat.TICK, fixture.run.frame)
            else:
                for _ in range(20):
                    fixture.run.step({})
            self.assertFalse(fight.rockets)
            outcomes.append((fixture.health(fixture.a), fight.mei.frost(fixture.a.body)))
        self.assertEqual(*outcomes)
        self.assertEqual(outcomes[0][0], 197.25)

    def test_actual_cryo_heals_and_blocks_pistol_melee_hack_and_emp(self):
        fixture = self.arena(distance=1.5)
        fight = fixture.run.match.combat
        for _ in range(180):
            if fixture.health(fixture.b) <= 45:
                break
            fixture.press(FIRE)
        before = fixture.health(fixture.b)
        self.assertGreater(before, 0)
        self.assertLessEqual(before, 45)
        count = len(fixture.a.client.messages(combat.HIT))
        fixture.run.step({fixture.b: (*face(fixture, fixture.b, fixture.a), 8)})
        self.assertTrue(mei.protected(fixture.b))
        fixture.press(FIRE, 20)
        fixture.press(0x800)
        fixture.press(0, 28)
        fixture.press(2, 50)
        fixture.press(0)
        fixture.a.body_script.set_ult_charge(1400)
        fixture.press(32)
        fixture.press(0, 55)
        self.assertEqual(len(fixture.a.client.messages(combat.HIT)), count)
        self.assertNotIn(fixture.b.body, fight.hacks)
        fixture.press(0, 140)
        self.assertFalse(mei.protected(fixture.b))
        self.assertAlmostEqual(fixture.health(fixture.b), before + 200, places=4)
        fixture.press(FIRE)
        self.assertLess(fixture.health(fixture.b), before + 200)

    def test_actual_hack_denies_cryo_then_fresh_cast_can_cancel(self):
        fixture = self.arena()
        fight = fixture.run.match.combat
        for _ in range(90):
            fixture.press(2)
            if fixture.b.body in fight.hacks:
                break
        self.assertIn(fixture.b.body, fight.hacks)
        fixture.press(8, shooter=fixture.b, target=fixture.a)
        fixture.press(0, 20)
        self.assertFalse(mei.protected(fixture.b))
        until = max(item.until_tick for item in fight.hacks[fixture.b.body])
        fixture.press(0, until - fixture.run.frame + 3)
        fixture.press(8, shooter=fixture.b, target=fixture.a)
        self.assertTrue(mei.protected(fixture.b))
        fixture.press(0, 40)
        fixture.press(2, shooter=fixture.b, target=fixture.a)
        fixture.press(0, 4)
        self.assertFalse(mei.protected(fixture.b))
        self.assertGreater(mei.cryo_instance(fixture.b).vars[61].value(), 0)
        fixture.press(8, shooter=fixture.b, target=fixture.a)
        self.assertFalse(mei.protected(fixture.b))

    def test_world_only_cryo_matches_contiguous_healing_and_expiry(self):
        outcomes = []
        for gap in (False, True):
            fixture = self.arena()
            match = fixture.run.match
            fight = match.combat
            fixture.press(FIRE, 30)
            self.assertLess(fixture.health(fixture.b), 250)
            fixture.press(8, shooter=fixture.b, target=fixture.a)
            cycle = fight.mei.cryos[fixture.b.body]
            deadline = cycle.deadline
            if gap:
                fixture.run.frame = deadline
                match.update(deadline * combat.TICK, deadline)
            else:
                fixture.press(0, deadline - fixture.run.frame)
            self.assertFalse(mei.protected(fixture.b))
            self.assertNotIn(fixture.b.body, fight.mei.cryos)
            fixture.run.step({})
            instance = mei.cryo_instance(fixture.b)
            outcomes.append((fixture.health(fixture.b), instance.vars[61].value(), cycle.accounted))
        self.assertEqual(*outcomes)
        self.assertEqual(outcomes[0][2], 200)

    def test_world_only_cryo_preserves_cancel_cooldown_and_recast(self):
        def press_at(f, button, frame, gap):
            if gap:
                f.run.match.update(frame * combat.TICK, frame)
                yaw, pitch = face(f, f.b, f.a)
                f.b.take_commands([Command(frame, yaw=yaw, pitch=pitch, buttons=button)])
                f.run.frame = frame
            else:
                f.press(0, frame - f.run.frame - 1)
                f.press(button, shooter=f.b, target=f.a)

        for cancel in (False, True):
            runs = []
            for gap in (False, True):
                with self.subTest(cancel=cancel, gap=gap):
                    f = self.arena()
                    match = f.run.match
                    fight = match.combat
                    f.press(FIRE, 30)
                    injured = f.health(f.b)
                    self.assertLess(injured, 250)
                    f.press(8, shooter=f.b, target=f.a)
                    self.assertTrue(mei.protected(f.b))
                    root = mei.cryo_instance(f.b)
                    start = f.run.frame
                    cycle = fight.mei.cryos[f.b.body]

                    if cancel:
                        press_at(f, 2, start + 45, gap)
                    press_at(f, 8, start + 300, gap)
                    self.assertFalse(mei.protected(f.b))
                    self.assertNotIn(f.b.body, fight.mei.cryos)
                    self.assertGreater(root.vars[61].value(), 0)
                    healed = f.health(f.b)
                    self.assertGreater(healed, injured)
                    self.assertEqual(healed == 250, not cancel)
                    self.assertEqual(cycle.accounted == 200, not cancel)
                    remaining = root.vars[61].value()

                    f.press(FIRE, 12)
                    damaged = f.health(f.b)
                    self.assertLess(damaged, healed)
                    press_at(f, 8, start + 1010, gap)
                    self.assertTrue(mei.protected(f.b))
                    self.assertIsNot(fight.mei.cryos[f.b.body], cycle)
                    if gap:
                        end = f.run.frame + 100
                        match.update(end * combat.TICK, end)
                    else:
                        f.press(0, 100)
                    self.assertGreater(f.health(f.b), damaged)
                    runs.append((healed, remaining, cycle.accounted, damaged, f.health(f.b)))
            self.assertEqual(runs[:1], runs[1:])

    def test_cryo_and_frost_are_causal_and_replacement_retires_old_effects(self):
        fixture = self.arena()
        match = fixture.run.match
        fight = match.combat
        self.spray(fixture)
        fixture.press(8, shooter=fixture.b, target=fixture.a)
        old_target, old_caster = fixture.a.body, fixture.b.body
        cycle = fight.mei.cryos[old_caster]

        def digest():
            store = _Graph({
                "combat": fight, "caster": fixture.b, "caster_vm": cycle.clock.instance.component,
            }, None)
            roots = {"mei": store.value(fight.mei)}
            return snapshot_digest({
                "schema": SCHEMA, "scope": SCOPE, "restorable": False,
                "roots": roots, "nodes": store.finish(), "assets": [],
            })

        before = digest()
        self.assertEqual(digest(), before)
        cycle.deadline += 1
        self.assertNotEqual(digest(), before)
        self.assertTrue(match.switch_hero(fixture.a, fixture.a.hero))
        self.assertTrue(match.switch_hero(fixture.b, fixture.b.hero))
        self.assertNotIn(old_target, fight.mei.hits)
        self.assertNotIn(old_caster, fight.mei.cryos)
        self.assertIsNone(fight.mei.frost(fixture.a.body))
        self.assertFalse(mei.protected(fixture.b))


class NpcMeiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        npc_fixture.SombraActualNpcTests.setUpClass.__func__(cls)

    @classmethod
    def restore_graphs(cls, path):
        npc_fixture.SombraActualNpcTests.restore_graphs(path)

    def test_spray_penetrates_front_sombra_and_hits_actual_npc_behind(self):
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                fixture, bot = npc_fixture.SombraActualNpcTests.make(self, key, distance=2)
                fight = fixture.run.match.combat
                fixture.b.spawned = True
                self.assertTrue(fixture.run.match.switch_hero(fixture.b, scene.heroes()[MEI]))
                place(fixture.a, (0, 0, 0))
                place(fixture.b, (0, 0, -2))
                bot.position = (0, 0, 2)
                fixture.press(0, 50)
                before = fight.vitals[bot.entity].total
                SombraMeiTests().spray(fixture, tail=30)
                self.assertEqual(fixture.health(fixture.a), 197.25)
                self.assertEqual(fight.vitals[bot.entity].total, before - 2.75)
                self.assertAlmostEqual(fight.mei.frost(bot.entity)[0], 0.2)

    def test_cryo_blocks_actual_npc_weapons_and_assassin_pin_then_exposes_mei(self):
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                fixture, bot = npc_fixture.SombraActualNpcTests.make(self, key, distance=2)
                match = fixture.run.match
                fight = match.combat
                self.assertTrue(match.switch_hero(fixture.a, scene.heroes()[MEI]))
                place(fixture.a, (0, 0, 0))
                fixture.press(0, 45)
                if key == "3":
                    special = next(i for i in bot.body_script.component.instances.values()
                                   if i.graph.index == 0x142C)
                    send_instance_message(special, 0x0240000000000A45, {}, bot.entity)
                npc_fixture.SombraActualNpcTests.press(fixture, bot, 8)
                self.assertTrue(mei.protected(fixture.a))
                self.assertEqual(npc_control.start(match, fixture.a), bot.entity)
                npc_fixture.SombraActualNpcTests.press(fixture, bot, 0, 120)
                self.assertEqual(fixture.health(fixture.a), 250)
                self.assertNotIn(bot.entity, fight.pins)
                npc_fixture.SombraActualNpcTests.press(fixture, bot, 2)
                self.assertFalse(mei.protected(fixture.a))
                if key == "4":
                    npc_control.stop(match)
                    npc_fixture.SombraActualNpcTests.press(fixture, bot, 0)
                    self.assertEqual(npc_control.start(match, fixture.a), bot.entity)
                for _ in range(230):
                    npc_fixture.SombraActualNpcTests.press(fixture, bot, 0)
                    if fixture.health(fixture.a) < 250:
                        break
                self.assertLess(fixture.health(fixture.a), 250)
