"""Actual Supercharger inputs and Sombra's static responses."""

import unittest
from dataclasses import replace

import test_game_sombra_assassin as assassin_fixture
import test_game_sombra_npc as npc_fixture
import test_game_sombra_orisa_fortify as fortify_fixture
from test_game_combat import aim
from test_game_sombra_orisa_halt import place

from ow174.game import combat, fortify, npc_control, orisa_supercharger, stats
from ow174.game.commands import FIRE
from ow174.game.content import heroes
from ow174.game.script.driver import ULT_CHARGE, ULT_COST
from ow174.game.script.runtime import send_instance_message
from ow174.game.server_snapshot import SCHEMA, SCOPE, _Graph, snapshot_digest
from ow174.game.world import ARMOUR_POOL, HEALTH_POOL


def meter(player):
    return player.body_script.component.entity_var(ULT_CHARGE).value()


def deploy(test, fixture, *, fill=True):
    match = fixture.run.match
    manager = match.combat.superchargers
    caster = fixture.b
    if fill:
        script = caster.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
    fixture.run.step({caster: (0, 0, 32)})
    for _ in range(90):
        if manager.active:
            break
        fixture.run.step({})
    device, = manager.active.values()
    test.assertIs(device.owner, caster)
    test.assertEqual(device.source_body, caster.body)
    test.assertEqual(device.script.graph.index, orisa_supercharger.DEVICE)
    test.assertEqual(device.vitals.total, 200)
    return device


class SombraSuperchargerTests(unittest.TestCase):
    def arena(self, *, allied=False):
        fixture = fortify_fixture.SombraOrisaFortifyTests().arena(distance=8)
        match = fixture.run.match
        match.game_map = replace(match.game_map, free_for_all=False)
        fixture.a.team, fixture.b.team = 0, 0 if allied else 1
        place(fixture.b, (0, 0, 0))
        place(fixture.a, (1.2, 0, 5))
        fixture.press(0, 15)
        return fixture

    def test_original_deploy_cost_boost_timer_and_charge_recovery(self):
        for gap in (False, True):
            with self.subTest(gap=gap):
                fixture = self.arena(allied=True)
                fight = fixture.run.match.combat
                caster = fixture.b
                seconds = caster.body_script.ult_seconds
                caster.body_script.ult_seconds = 0
                caster.body_script.set_ult_charge(1679)
                fixture.run.step({caster: (0, 0, 32)})
                self.assertFalse(fight.superchargers.flights)
                self.assertFalse(fight.superchargers.active)
                fixture.press(0, 60)
                self.assertEqual(meter(caster), 1679)
                device = deploy(self, fixture)
                caster.body_script.ult_seconds = seconds
                self.assertEqual(device.end_tick - fixture.run.frame, 938)
                self.assertEqual(fight.outgoing_scale(fixture.a.body), 1.5)
                self.assertEqual(fight.outgoing_scale(caster.body), 1.5)
                status = fight.superchargers.buffs[(device.entity, fixture.a.body)]
                self.assertEqual(status.graph.index, orisa_supercharger.BOOST)
                self.assertEqual(status.vars[174].value(), 0.5)
                self.assertTrue(caster.body_script.component.entity_var(842).value())
                locked = meter(caster)
                self.assertEqual(locked, 0)
                fixture.press(0, 80)
                self.assertEqual(meter(caster), locked)
                if gap:
                    fixture.run.frame = device.end_tick
                    fixture.run.match.update(fixture.run.frame * combat.TICK, fixture.run.frame)
                else:
                    fixture.press(0, device.end_tick - fixture.run.frame)
                self.assertFalse(fight.superchargers.active)
                self.assertTrue(status.stopped)
                self.assertEqual(fight.outgoing_scale(fixture.a.body), 1)
                self.assertFalse(caster.body_script.component.entity_var(842).value())
                fixture.press(0, 70)
                self.assertGreater(meter(caster), locked)

    def test_team_range_cover_and_reacquisition(self):
        fixture = self.arena(allied=True)
        fight = fixture.run.match.combat
        device = deploy(self, fixture)
        x, _, z = device.position
        near = (x, 0, z + 5)
        place(fixture.a, near)
        fixture.press(0)
        self.assertEqual(fight.outgoing_scale(fixture.a.body), 1.5)
        fixture.geometry((-4, 0, z + 2, 4, 0, z + 2, 4, 4, z + 2),
                         (-4, 0, z + 2, 4, 4, z + 2, -4, 4, z + 2))
        fixture.press(0)
        self.assertEqual(fight.outgoing_scale(fixture.a.body), 1)
        fixture.geometry()
        fixture.press(0)
        self.assertEqual(fight.outgoing_scale(fixture.a.body), 1.5)
        place(fixture.a, (x, 0, z + 26))
        fixture.press(0)
        self.assertEqual(fight.outgoing_scale(fixture.a.body), 1)
        place(fixture.a, near)
        fixture.a.team = 1
        fixture.press(0)
        self.assertEqual(fight.outgoing_scale(fixture.a.body), 1)
        fixture.a.team = 0
        fixture.press(0)
        self.assertEqual(fight.outgoing_scale(fixture.a.body), 1.5)

    def test_enemy_pistol_and_melee_damage_device_without_hero_credit(self):
        fixture = self.arena()
        fight = fixture.run.match.combat
        device = deploy(self, fixture)
        x, y, z = device.position
        place(fixture.a, (x, y, z + 1.6))
        place(fixture.b, (8, 0, 0))
        fixture.press(0, 4)
        direction = aim(fight._eye(fixture.a), (x, y + 0.6, z))
        fixture.run.step({fixture.a: (*direction, FIRE)})
        self.assertEqual(device.vitals.total, 192)
        for offset in range(38):
            fixture.run.step({fixture.a: (*direction, 0x800 if offset == 0 else 0)})
        self.assertEqual(device.vitals.total, 162)
        self.assertEqual(fight.outgoing_scale(fixture.a.body), 1)
        self.assertEqual(fixture.a.stats.value(stats.HERO_DAMAGE), 0)
        self.assertEqual(meter(fixture.a), 0)
        for _ in range(220):
            fixture.run.step({fixture.a: (*direction, FIRE)})
            if device.entity not in fight.superchargers.active:
                break
        self.assertNotIn(device.entity, fight.superchargers.active)
        self.assertEqual(fight.outgoing_scale(fixture.b.body), 1)
        self.assertFalse(fixture.b.body_script.component.entity_var(842).value())
        self.assertEqual(fixture.a.stats.value(stats.ELIMINATIONS), 0)

    def test_hack_and_emp_do_not_target_device_and_emp_silences_caster(self):
        fixture = self.arena()
        fight = fixture.run.match.combat
        device = deploy(self, fixture)
        x, y, z = device.position
        place(fixture.a, (x, y, z + 3))
        place(fixture.b, (6, 0, 3))
        for _ in range(55):
            fixture.run.step({fixture.a: (*aim(fight._eye(fixture.a), (x, y + 0.6, z)), 2)})
        self.assertFalse(fight.hacks)
        self.assertEqual(fixture.weapon().state(53).remaining(), 0)
        fixture.press(0)
        fixture.a.body_script.set_ult_charge(1400)
        fixture.press(32)
        fixture.press(0, 55)
        self.assertIn(fixture.b.body, fight.hacks)
        self.assertNotIn(device.entity, fight.hacks)
        self.assertEqual(device.vitals.total, 200)
        self.assertEqual(fight.outgoing_scale(fixture.b.body), 1.5)

    def test_actual_emp_interrupts_paid_windup_without_stale_throw(self):
        fixture = self.arena()
        fight = fixture.run.match.combat
        fixture.b.body_script.set_ult_charge(1680)
        fixture.a.body_script.set_ult_charge(1400)
        fixture.press(32)
        fixture.run.step({fixture.b: (0, 0, 32)})
        self.assertLess(meter(fixture.b), 1680)
        fixture.press(0, 100)
        self.assertIn(fixture.b.body, fight.hacks)
        self.assertFalse(fight.superchargers.flights)
        self.assertFalse(fight.superchargers.active)

    def test_actual_hack_cancels_windup_and_silence_denies_fresh_cast(self):
        fixture = self.arena()
        fight = fixture.run.match.combat
        caster = fixture.b
        caster.body_script.ult_seconds = 0
        caster.body_script.set_ult_charge(1680)
        x, y, z = caster.mover.position
        facing = aim(fight._eye(fixture.a), (x, y + 1, z))
        for offset in range(90):
            fixture.run.step({fixture.a: (*facing, 2), caster: (0, 0, 32 if offset == 25 else 0)})
        self.assertIn(caster.body, fight.hacks)
        self.assertEqual(meter(caster), 0)
        self.assertFalse(fight.superchargers.flights)
        self.assertFalse(fight.superchargers.active)
        caster.body_script.set_ult_charge(1680)
        fixture.run.step({caster: (0, 0, 32)})
        fixture.press(0, 65)
        self.assertEqual(meter(caster), 1680)
        self.assertFalse(fight.superchargers.flights)
        self.assertFalse(fight.superchargers.active)

    def test_lethal_sombra_hit_retires_in_flight_throw_before_landing(self):
        fixture = self.arena()
        fight = fixture.run.match.combat
        caster = fixture.b
        caster.body_script.set_ult_charge(1680)
        fixture.run.step({caster: (0, 0, 32)})
        for _ in range(40):
            if fight.superchargers.flights:
                break
            fixture.run.step({})
        self.assertIn(caster.body, fight.superchargers.flights)
        fixture.health(caster)
        vitals = fight.vitals[caster.body]
        vitals.now[ARMOUR_POOL] = 0
        vitals.now[HEALTH_POOL] = 1
        fixture.press(FIRE)
        self.assertTrue(vitals.dead)
        self.assertEqual(vitals.killer, fixture.a.body)
        fixture.press(0, 85)
        self.assertFalse(fight.superchargers.flights)
        self.assertFalse(fight.superchargers.active)

    def test_actual_fusion_driver_earns_sombra_damage_and_freezes_while_deployed(self):
        fixture = self.arena()
        fight = fixture.run.match.combat
        caster = fixture.b
        caster.body_script.ult_seconds = 0
        caster.body_script.set_ult_charge(0)
        facing = aim(fight._eye(caster), (1.2, 1.0, 5))
        fixture.run.step({caster: (*facing, FIRE)})
        fixture.press(0, 12)
        self.assertEqual(fixture.health(fixture.a), 189)
        self.assertEqual(meter(caster), 11)
        deploy(self, fixture)
        fixture.press(0, 45)
        fixture.run.step({caster: (*facing, FIRE)})
        fixture.press(0, 12)
        self.assertAlmostEqual(fixture.health(fixture.a), 172.5)
        self.assertEqual(meter(caster), 0)

    def test_deployed_status_survives_caster_replacement_and_remains_causal(self):
        fixture = self.arena(allied=True)
        match = fixture.run.match
        manager = match.combat.superchargers
        device = deploy(self, fixture)
        old = fixture.b.body
        self.assertTrue(match.switch_hero(fixture.b, fixture.b.hero))
        self.assertNotEqual(fixture.b.body, old)
        fixture.press(0, 25)
        self.assertIs(manager.active[device.entity], device)
        self.assertEqual(device.source_body, old)
        self.assertNotIn((device.entity, old), manager.buffs)
        self.assertEqual(match.combat.outgoing_scale(fixture.a.body), 1.5)
        self.assertTrue(fixture.b.body_script.component.entity_var(842).value())

        def digest():
            boundaries = {
                "combat": match.combat,
                "owner": fixture.b,
                "device_vm": device.component,
                "sombra_vm": fixture.a.body_script.component,
                "owner_vm": fixture.b.body_script.component,
            }
            store = _Graph(boundaries, None)
            roots = {"superchargers": store.value(manager)}
            return snapshot_digest({
                "schema": SCHEMA, "scope": SCOPE, "restorable": False,
                "roots": roots, "nodes": store.finish(), "assets": [],
            })

        before = digest()
        self.assertEqual(digest(), before)
        device.end_tick += 1
        self.assertNotEqual(digest(), before)


class NpcSuperchargerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        npc_fixture.SombraActualNpcTests.setUpClass.__func__(cls)

    @classmethod
    def restore_graphs(cls, path):
        npc_fixture.SombraActualNpcTests.restore_graphs(path)

    def arena(self, key, *, allied, passive=True):
        fixture, bot = npc_fixture.SombraActualNpcTests.make(self, key, distance=4, team=1)
        match = fixture.run.match
        match.game_map = replace(match.game_map, free_for_all=False)
        fixture.b.spawned = True
        self.assertTrue(match.switch_hero(fixture.b, heroes()[fortify.HERO]))
        if not passive:
            fixture.b.body_script.ult_seconds = 0
        fixture.a.team, fixture.b.team = 0, 0 if allied else 1
        place(fixture.a, (0, 0, 5))
        place(fixture.b, (5, 0, 0))
        bot.position = (0, 0, 7)
        fixture.press(0, 45)
        return fixture, bot

    def test_actual_boosted_sombra_pistol_hits_each_actual_enemy_npc(self):
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                fixture, bot = self.arena(key, allied=True)
                fight = fixture.run.match.combat
                device = deploy(self, fixture)
                self.assertEqual(fight.outgoing_scale(bot.entity), 1)
                self.assertEqual(fight.outgoing_scale(fixture.a.body), 1.5)
                before = fight.vitals[bot.entity].total
                npc_fixture.SombraActualNpcTests.press(fixture, bot, FIRE)
                self.assertEqual(before - fight.vitals[bot.entity].total, 12)
                self.assertEqual(device.vitals.total, 200)

    def test_actual_boosted_npc_weapons_hit_enemy_sombra(self):
        for key in ("1", "2", "3", "5"):
            with self.subTest(npc=self.catalog[key].name):
                damage = []
                for boost in (False, True):
                    fixture, bot = self.arena(key, allied=False)
                    fight = fixture.run.match.combat
                    if boost:
                        deploy(self, fixture)
                        self.assertEqual(fight.outgoing_scale(bot.entity), 1.5)
                    if key == "3":
                        special = next(i for i in bot.body_script.component.instances.values()
                                       if i.graph.index == 0x142C)
                        send_instance_message(special, 0x0240000000000A45, {}, bot.entity)
                    npc_fixture.SombraActualNpcTests.press(fixture, bot, 0)
                    self.assertEqual(npc_control.start(fixture.run.match, fixture.a), bot.entity)
                    for _ in range(380):
                        fixture.press(0)
                        health = fixture.health(fixture.a)
                        if health < 200:
                            break
                    self.assertLess(health, 200)
                    damage.append(200 - health)
                self.assertAlmostEqual(damage[1], damage[0] * 1.5, places=3)

    def test_actual_boosted_assassin_pin_hits_enemy_sombra(self):
        fixture, bot = self.arena("4", allied=False)
        fight = fixture.run.match.combat
        deploy(self, fixture)
        self.assertEqual(fight.outgoing_scale(bot.entity), 1.5)
        helper = assassin_fixture.SombraAssassinTests()
        helper.engage(fixture, bot)
        self.assertAlmostEqual(fixture.health(fixture.a), 162.5)
        root = helper.pin_root(bot)
        for _ in range(95):
            helper.step(fixture, bot)
            if root.node_state(root.graph.node(15)).active:
                break
        self.assertTrue(root.node_state(root.graph.node(15)).active)
        before = fixture.health(fixture.a)
        for _ in range(10):
            helper.step(fixture, bot)
        self.assertAlmostEqual(before - fixture.health(fixture.a), 24, places=4)

    def test_fusion_driver_effective_damage_earns_charge_on_every_actual_npc(self):
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                fixture, bot = self.arena(key, allied=True, passive=False)
                fight = fixture.run.match.combat
                bot.position = (5, 0, 4)
                before = fight.vitals[bot.entity].total
                self.assertEqual(meter(fixture.b), 0)
                facing = aim(fight._eye(fixture.b), (5, 1, 4))
                fixture.run.step({fixture.b: (*facing, FIRE)})
                fixture.press(0, 12)
                self.assertEqual(before - fight.vitals[bot.entity].total, 11)
                self.assertEqual(meter(fixture.b), 11)

    def test_unmodified_heavy_assault_supplies_full_earned_supercharger(self):
        fixture, bot = self.arena("5", allied=True, passive=False)
        fight = fixture.run.match.combat
        bot.position = (5, 0, 4)
        self.assertEqual(fight.vitals[bot.entity].total, 4000)
        caster = fixture.b
        self.assertEqual(meter(caster), 0)
        facing = aim(fight._eye(caster), (5, 1, 4))
        for _ in range(2500):
            fixture.run.step({caster: (*facing, FIRE)})
            if meter(caster) >= 1680:
                break
        self.assertEqual(meter(caster), 1680)
        self.assertFalse(bot.dead)
        rifle = next(i for i in caster.body_script.component.instances.values() if i.graph.index == 0xBD4)
        self.assertGreater(rifle.state(19).counter, 0)
        deploy(self, fixture, fill=False)
        self.assertEqual(meter(caster), 0)
