"""Actual Torbjorn deployment and Ana combat against the reconstructed turret."""

import unittest
from unittest.mock import patch

import test_game_ana as fixture
import test_game_ana_baptiste_field as field_fixture
from test_game_combat import aim

from ow174.game import torbjorn_turret
from ow174.game.combat import TICK
from ow174.game.commands import Command
from ow174.game.content import find_hero
from ow174.game.script import expr


class AnaTorbjornTurretTests(unittest.TestCase):
    def arena(self, *, enemy=True):
        run, ana, torbjorn, _ = fixture.AnaRifleTests().setup_fight(enemy=enemy)
        self.assertTrue(run.match.switch_hero(torbjorn, find_hero("Torbjorn")))
        x, y, z = ana.mover.position
        torbjorn.mover.position[:] = x + 4, y, z + 5
        for _ in range(25):
            run.step({})
        fight = run.match.combat
        vitals = fight.vitals_of(next(target for target in fight._targets() if target.owner is ana))
        return run, ana, torbjorn, vitals

    def deploy(self, run, torbjorn):
        fight = run.match.combat
        run.step({torbjorn: (0, 0, 8)})
        for _ in range(95):
            run.step({})
            device = fight.torbjorn_turrets.devices.get(torbjorn.body)
            if device is not None:
                self.assertEqual(device.health, 250)
                self.assertEqual(device.instance.find_var(*device.output).value(), expr.Entity(device.body))
                return device
        self.fail("actual deployment did not land")

    def test_hostile_deployment_builds_and_fires_on_ana(self):
        run, ana, torbjorn, vitals = self.arena()
        device = self.deploy(run, torbjorn)
        fight = run.match.combat
        first = device.next_shot
        while fight.now + 0.016 < first:
            run.step({})
        self.assertEqual(vitals.total, 200)
        while fight.now < first:
            run.step({})
        self.assertEqual(vitals.total, 186)
        run.match.update(fight.now, fight.tick, frames=False)
        self.assertEqual(vitals.total, 186)
        while vitals.total == 186 and fight.now < first + 0.3:
            run.step({})
        self.assertEqual(vitals.total, 172)
        while vitals.total > 144 and fight.now < first + 0.8:
            run.step({})
        self.assertEqual(vitals.total, 144)
        self.assertLessEqual(fight.now - first - 0.75, TICK)
        self.assertIs(fight.vitals[ana.body], vitals)

    def test_friendly_turret_neither_fires_at_ana_nor_takes_her_rifle(self):
        run, ana, torbjorn, vitals = self.arena(enemy=False)
        device = self.deploy(run, torbjorn)
        field_fixture.AnaBaptisteFieldTests().fire(run, ana, device.centre(), 1, scoped=True, frames=100)
        self.assertEqual(device.health, 250)
        self.assertEqual(vitals.total, 200)

    def test_lethal_turret_round_attributes_the_death_to_the_device(self):
        run, _, torbjorn, vitals = self.arena()
        device = self.deploy(run, torbjorn)
        fight = run.match.combat
        vitals.damage(193)
        while fight.now < device.next_shot and not vitals.dead:
            run.step({})
        self.assertTrue(vitals.dead)
        self.assertEqual(vitals.killer, device.body)

    def test_ana_weapon_contacts_damage_the_hostile_turret(self):
        for button, scoped, amount in ((1, False, 70), (1, True, 70), (8, False, 5),
                                       (16, False, 50), (0x800, False, 30)):
            with self.subTest(button=button, scoped=scoped):
                run, ana, torbjorn, _ = self.arena()
                device = self.deploy(run, torbjorn)
                if button == 0x800:
                    x, _, z = device.position
                    field_fixture.AnaBaptisteFieldTests.place(ana, (x, ana.mover.position[1], z - 1.3))
                field_fixture.AnaBaptisteFieldTests().fire(run, ana, device.centre(), button, scoped=scoped)
                self.assertEqual(device.health, 250 - amount)

    def test_acquisition_rejects_static_cover_and_out_of_range_then_fires(self):
        run, ana, torbjorn, vitals = self.arena()
        device = self.deploy(run, torbjorn)
        fight = run.match.combat
        place = field_fixture.AnaBaptisteFieldTests.place
        x, y, z = device.position
        place(ana, (x, y, z - 42))
        while fight.now < device.next_shot + 0.1:
            run.step({})
        self.assertEqual(vitals.total, 200)
        place(ana, (x, y, z - 5))
        world = run.match.collision_world
        fixture.set_static_wall(run, ana, 2.5)
        for _ in range(20):
            run.step({})
        self.assertEqual(vitals.total, 200)
        run.match.collision_world = world
        run.step({})
        self.assertEqual(vitals.total, 186)

    def test_rifle_destruction_clears_the_slot_and_stops_fire(self):
        run, ana, torbjorn, vitals = self.arena()
        device = self.deploy(run, torbjorn)
        fight = run.match.combat
        direction = aim(fight._eye(ana), device.centre())
        for _ in range(260):
            run.step({ana: (*direction, 1)})
            if torbjorn.body not in fight.torbjorn_turrets.devices:
                break
        self.assertEqual(device.health, 0)
        self.assertEqual(device.instance.find_var(*device.output).value(), expr.Entity(0))
        before = vitals.total
        for _ in range(34):
            run.step({})
        self.assertEqual(vitals.total, before)

    def test_actual_dismantle_command_rejects_construction_and_stops_ready_turret(self):
        run, ana, torbjorn, vitals = self.arena()
        fight = run.match.combat
        manager = fight.torbjorn_turrets
        run.step({torbjorn: (0, 0, 4)})
        run.step({})
        self.assertFalse(manager.devices)
        device = self.deploy(run, torbjorn)
        component = torbjorn.body_script.component
        published = component.entity_var(torbjorn_turret.PUBLISHED_BODY)
        self.assertEqual(published.value(), expr.Entity(device.body))

        with patch.object(component.world, "send_message", wraps=component.world.send_message) as sends:
            while fight.now < device.ready:
                run.step({torbjorn: (0, 0, 4)})
            self.assertIs(manager.devices[torbjorn.body], device)
            self.assertEqual(device.health, 250)
            self.assertEqual(vitals.total, 186)
            self.assertFalse(any(call.args[1] == torbjorn_turret.DISMANTLE for call in sends.call_args_list))
            self.assertFalse(manager.receive(device.body, torbjorn_turret.DISMANTLE, ana.body))

            run.step({})
            run.step({torbjorn: (0, 0, 4)})
            dismantle, = [call.args for call in sends.call_args_list
                          if call.args[1] == torbjorn_turret.DISMANTLE]
            self.assertEqual(dismantle, (device.body, torbjorn_turret.DISMANTLE, {}, torbjorn.body))

        self.assertNotIn(torbjorn.body, manager.devices)
        self.assertEqual(device.health, 0)
        self.assertEqual(published.value(), expr.Entity(0))
        self.assertEqual(device.instance.find_var(*device.output).value(), expr.Entity(0))
        for _ in range(34):
            run.step({})
        self.assertEqual(vitals.total, 186)

    def test_actual_hammer_repairs_ana_rifle_damage_with_range_cover_and_health_gates(self):
        run, ana, torbjorn, _ = self.arena()
        device = self.deploy(run, torbjorn)
        field = field_fixture.AnaBaptisteFieldTests()
        field.fire(run, ana, device.centre(), 1, frames=20)
        self.assertEqual(device.health, 180)
        x, y, z = device.position
        field.place(ana, (x, y, z - 42))
        field.place(torbjorn, (x, y, z - 1))

        run.frame += 1
        run.match.update(run.frame * TICK, run.frame)
        for player in run.players:
            player.take_commands([Command(run.frame, action=2 if player is torbjorn else 0)])
        for _ in range(10):
            run.step({})

        def swing():
            run.step({torbjorn: (0, 0, 1)})
            for _ in range(24):
                run.step({})
            before_contact = device.health
            for _ in range(31):
                run.step({})
            return before_contact

        world = torbjorn.body_script.component.world
        with patch.object(world, "send_message", wraps=world.send_message) as sends:
            self.assertEqual(swing(), 180)
            self.assertLess(run.match.combat.now, device.ready)
            self.assertEqual(device.health, 180)
            while run.match.combat.now < device.ready:
                run.step({})
            field.place(torbjorn, (x, y, z - 2))
            self.assertEqual(swing(), 180)
            self.assertEqual(device.health, 180)
            field.place(torbjorn, (x, y, z - 1))
            collision = run.match.collision_world
            fixture.set_static_wall(run, torbjorn, 0.5)
            self.assertEqual(swing(), 180)
            self.assertEqual(device.health, 180)
            run.match.collision_world = collision
            self.assertEqual(swing(), 180)
            self.assertEqual(device.health, 230)
            self.assertEqual(swing(), 230)
            self.assertEqual(device.health, 250)
            self.assertEqual(swing(), 250)
            self.assertEqual(device.health, 250)

        repair, full = torbjorn_turret.REPAIR, torbjorn_turret.REPAIR_FULL
        self.assertEqual([call.args for call in sends.call_args_list if call.args[1] in (repair, full)],
                         [(device.body, message, {}, torbjorn.body) for message in (repair, repair, full)])

    def test_body_replacement_retires_the_turret_and_ends_damage(self):
        run, _, torbjorn, vitals = self.arena()
        device = self.deploy(run, torbjorn)
        fight = run.match.combat
        old = torbjorn.body
        slot = device.instance.find_var(*device.output)
        self.assertTrue(run.match.switch_hero(torbjorn, find_hero("Soldier: 76")))
        self.assertNotIn(old, fight.torbjorn_turrets.devices)
        self.assertEqual(slot.value(), expr.Entity(0))
        while fight.now < device.next_shot + 0.5:
            run.step({})
        self.assertEqual(vitals.total, 200)

    def test_replacement_retires_the_turret_carrier_without_a_projectile_lease(self):
        run, _, torbjorn, _ = self.arena()
        fight = run.match.combat
        with patch.object(fight.projectiles, "fired", return_value=None):
            run.step({torbjorn: (0, 0, 8)})
            for _ in range(16):
                run.step({})
                if fight.rockets:
                    break
        carrier, = fight.rockets
        self.assertIsNotNone(carrier.turret_instance)
        self.assertIsNone(carrier.shot)
        self.assertTrue(run.match.switch_hero(torbjorn, find_hero("Soldier: 76")))
        self.assertFalse(fight.rockets)
        self.assertFalse(fight.torbjorn_turrets.devices)

    def test_landed_health_and_shot_deadline_enter_the_server_snapshot(self):
        import test_server_snapshot as stored

        case = stored.ServerSnapshotTests()
        session, peers, players = case.scene(two=True)
        torbjorn = players[1]
        peers[1].message(21616, stored.pick(torbjorn.entity, find_hero("Torbjorn").guid), now=100.8)
        peers[1].commands(torbjorn.body_script.frame + 1, 40, 8)
        fight = torbjorn.match.combat
        carrier, = (rocket for rocket in fight.rockets if rocket.turret_instance is not None)
        self.assertEqual(carrier.source_entity, torbjorn.body)
        fight.update(fight.now + TICK, fight.tick + 1, frames=False)
        fight.update(fight.now + 65 * TICK, fight.tick + 65, frames=False)
        device = fight.torbjorn_turrets.devices[torbjorn.body]
        before = stored.snapshot_digest(case.capture(session))
        device.health -= 5
        after = stored.snapshot_digest(case.capture(session))
        self.assertNotEqual(before, after)
        device.ready += TICK
        self.assertNotEqual(after, stored.snapshot_digest(case.capture(session)))
        device.next_shot += TICK
        self.assertNotEqual(after, stored.snapshot_digest(case.capture(session)))
