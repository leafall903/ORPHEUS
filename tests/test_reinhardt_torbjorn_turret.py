"""Actual turret deployment against Reinhardt's static combat paths."""

import os
import unittest
from dataclasses import replace
from unittest.mock import patch

import test_game_reinhardt as fixtures
import test_reinhardt_orisa_supercharger as supercharger_fixture
from test_game_combat import aim

from ow174.game import torbjorn_turret
from ow174.game.combat import DEATH_NOTICE, TICK
from ow174.game.commands import Command
from ow174.game.content import find_hero
from ow174.game.script import expr


class ReinhardtTurretTests(unittest.TestCase):
    advance = fixtures.HammerTests.advance
    place = staticmethod(fixtures.HammerTests.place)
    health = fixtures.HammerTests.health
    cover_at = supercharger_fixture.ReinhardtSuperchargerTests.cover_at

    def deploy(self, *, friendly=False):
        self.enterContext(patch.dict(os.environ, {"ORPHEUS_REINHARDT_COLLISION": str(fixtures.BARRIER_DATA)}))
        fixtures.HammerTests.setUp(self)
        self.match.game_map = replace(self.match.game_map, free_for_all=False)
        self.assertTrue(self.match.switch_hero(self.enemy, find_hero("Torbjorn")))
        self.rein.team = 0
        self.enemy.team = self.other.team = 0 if friendly else 1
        self.place(self.rein, (12, 1, -8))
        self.place(self.enemy, (6, 1, 0))
        self.place(self.other, (20, 1, -8))
        self.advance(20, buttons=0)
        self.run.step({self.enemy: (0, 0, 8)})
        self.fight = self.match.combat
        manager = self.fight.torbjorn_turrets
        for _ in range(95):
            self.advance(1, buttons=0)
            device = manager.devices.get(self.enemy.body)
            if device is not None:
                break
        self.assertIsNotNone(device)
        self.assertEqual(device.health, 250)
        self.assertEqual(device.instance.find_var(*device.output).value(), expr.Entity(device.body))
        self.assertIs(manager.entity_var(self.enemy.body, device.body, 961), True)
        return device

    def test_charge_hits_hostile_turret_before_the_player_behind(self):
        device = self.deploy()
        x, y, z = device.position
        self.place(self.rein, (x, y, z - 5))
        self.place(self.other, (x, y, z + 4))
        self.advance(4, buttons=0)
        before = self.health(self.other)
        self.advance(1, buttons=8)
        for _ in range(100):
            self.advance(1, buttons=0)
            if device.health < 250:
                break
        self.assertEqual(device.health, 75)
        charge = next(i for i in self.rein.body_script.component.instances.values() if i.graph.index == 0x007D)
        self.assertEqual(charge.states[58].counter, 1)
        self.assertEqual(charge.vars[59].value(), 2)
        self.assertEqual(charge.vars[6836].value(), ())
        self.assertGreater(charge.vars[61].value(), 9)
        self.assertFalse(self.fight.pinned(self.other.body))
        self.assertEqual(self.health(self.other), before)
        self.place(self.rein, (x, y, z - 5))
        yaw, pitch = aim(self.fight._eye(self.rein), (x, y + 1, z))
        self.advance(25, buttons=0, yaw=yaw, pitch=pitch)
        self.assertEqual(charge.vars[59].value(), 2)
        self.advance(20, buttons=0, yaw=yaw, pitch=pitch)
        self.assertEqual(charge.vars[59].value(), 0)
        self.advance(1, buttons=16, yaw=yaw, pitch=pitch)
        self.advance(55, buttons=0, yaw=yaw, pitch=pitch)
        self.assertEqual(device.health, 0)
        self.assertNotIn(device.source, self.fight.torbjorn_turrets.devices)
        self.assertEqual(device.instance.find_var(*device.output).value(), expr.Entity(0))

    def test_charge_passes_friendly_turret_and_pins_hostile_player(self):
        device = self.deploy(friendly=True)
        x, y, z = device.position
        self.other.team = 1
        self.place(self.rein, (x, y, z - 5))
        self.place(self.other, (x, y, z + 4))
        self.advance(1, buttons=8)
        for _ in range(95):
            self.advance(1, buttons=0)
            if self.fight.pinned(self.other.body):
                break
        self.assertTrue(self.fight.pinned(self.other.body))
        self.assertEqual(device.health, 250)

    def test_static_cover_stops_charge_before_hostile_turret(self):
        device = self.deploy()
        x, y, z = device.position
        self.place(self.rein, (x, y, z - 5))
        self.cover_at(x, z - 1)
        self.advance(1, buttons=8)
        self.advance(85, buttons=0)
        self.assertEqual(device.health, 250)
        self.assertFalse(self.fight.charge_drives)
        self.assertLess(self.rein.mover.position[2], z - 1)

    def test_hammer_hits_once_and_switching_owner_retires_the_turret(self):
        device = self.deploy()
        x, y, z = device.position
        self.place(self.rein, (x, y, z - 2))
        yaw, pitch = aim(self.fight._eye(self.rein), device.centre())
        self.advance(43, buttons=1, yaw=yaw, pitch=pitch)
        self.assertEqual(device.health, 165)
        self.assertTrue(self.match.switch_hero(self.enemy, find_hero("Soldier: 76")))
        self.assertNotIn(device.source, self.fight.torbjorn_turrets.devices)

    def test_actual_forge_hammer_repairs_rocket_hammer_damage_after_construction(self):
        device = self.deploy()
        x, y, z = device.position
        self.place(self.rein, (x, y, z - 2))
        yaw, pitch = aim(self.fight._eye(self.rein), device.centre())
        self.advance(43, buttons=1, yaw=yaw, pitch=pitch)
        self.assertEqual(device.health, 165)
        self.place(self.rein, (x, y, z - 42))
        self.place(self.enemy, (x, y, z - 1))
        self.run.frame += 1
        self.match.update(self.run.frame * TICK, self.run.frame)
        for player in self.run.players:
            player.take_commands([Command(self.run.frame, action=2 if player is self.enemy else 0)])
        self.advance(8, buttons=0)

        def swing(frames=54):
            self.run.step({self.enemy: (0, 0, 1)})
            self.advance(frames, buttons=0)

        swing(32)
        self.assertEqual(device.health, 165)
        self.assertLess(self.fight.now, device.ready)
        while self.fight.now < device.ready:
            self.advance(1, buttons=0)
        self.place(self.enemy, (x, y, z - 2))
        swing()
        self.assertEqual(device.health, 165)
        self.place(self.enemy, (x, y, z - 1))
        self.cover_at(x, z - 0.5)
        swing()
        self.assertEqual(device.health, 165)
        fixtures.charge_wall(self, z=40)
        world = self.enemy.body_script.world
        with patch.object(world, "send_message", wraps=world.send_message) as sent:
            swing()
            self.assertEqual(device.health, 215)
            swing()
            self.assertEqual(device.health, 250)
            swing()
            self.assertEqual(device.health, 250)
        messages = (torbjorn_turret.REPAIR, torbjorn_turret.REPAIR, torbjorn_turret.REPAIR_FULL)
        self.assertEqual([call.args for call in sent.call_args_list if call.args[1] in messages],
                         [(device.body, message, {}, self.enemy.body) for message in messages])

    def test_turret_fires_after_construction_and_barrier_blocks_rounds(self):
        device = self.deploy()
        x, y, z = device.position
        self.place(self.rein, (x, y, z - 5))
        while self.fight.now + 0.016 < device.ready:
            self.advance(1, buttons=2)
        self.assertEqual(self.health(self.rein), 500)
        barrier = self.fight.barriers[self.rein.body]
        self.assertEqual(barrier.current, 1600)
        for _ in range(30):
            self.advance(1, buttons=2)
            if barrier.current < 1600:
                break
        self.assertEqual(barrier.current, 1586)
        self.assertEqual(self.health(self.rein), 500)
        vitals = self.fight.vitals_of(self.fight._target_entity(self.rein.body))
        self.assertIsNone(vitals.last_damage_source)
        for _ in range(24):
            self.advance(1, buttons=0)
            if self.health(self.rein) < 500:
                break
        self.assertEqual(self.health(self.rein), 491)
        self.assertEqual(vitals.last_damage_source, device.body)

    def test_turret_lethal_round_records_device_and_keeps_owner_for_notices(self):
        device = self.deploy()
        x, y, z = device.position
        self.place(self.rein, (x, y, z - 5))
        vitals = self.fight.vitals_of(self.fight._target_entity(self.rein.body))
        for _ in range(800):
            self.advance(1, buttons=0)
            if vitals.dead:
                break
        self.assertTrue(vitals.dead)
        self.assertEqual(vitals.last_damage_source, device.body)
        self.assertEqual(vitals.killer, self.enemy.body)
        [notice] = self.rein.client.messages(DEATH_NOTICE)
        self.assertEqual(notice["+0x7C"]["+0x0"], self.enemy.body)

    def test_actual_dismantle_requires_ready_turret_and_clears_its_handle(self):
        device = self.deploy()
        manager = self.fight.torbjorn_turrets
        for _ in range(120):
            if self.fight.now >= device.ready:
                break
            self.run.step({self.enemy: (0, 0, 4)})
        self.assertIs(manager.devices[self.enemy.body], device)
        self.assertEqual(device.health, 250)
        self.run.step({})
        self.run.step({self.enemy: (0, 0, 4)})
        self.assertNotIn(device.source, manager.devices)
        self.assertEqual(device.health, 0)
        self.assertEqual(device.instance.find_var(*device.output).value(), expr.Entity(0))
