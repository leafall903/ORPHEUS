"""Actual Ana and Baptiste commands against Immortality Field on 104319."""

import unittest
from unittest.mock import patch

import test_game_ana as fixture
from test_game_combat import Run, aim

from ow174.game import stats
from ow174.game.content import find_hero
from ow174.game.script import runtime


class AnaBaptisteFieldTests(unittest.TestCase):
    @staticmethod
    def place(player, position):
        player.spawn = position, player.spawn[1]
        player.mover = player.new_mover()

    def arena(self, *, enemy=True):
        run, ana, bap, _ = fixture.AnaRifleTests().setup_fight(enemy=enemy)
        self.assertTrue(run.match.switch_hero(bap, find_hero('Baptiste')))
        x, y, z = ana.mover.position
        bap.mover.position[:] = (x, y, z + 6)
        for _ in range(30):
            run.step({})
        fight = run.match.combat
        vitals = fight.vitals_of(next(t for t in fight._targets() if t.owner is bap))
        return run, ana, bap, vitals

    def deploy(self, run, bap, *, point=None):
        fight = run.match.combat
        fields = fight.baptiste_fields
        x, y, z = bap.mover.position
        direction = aim(fight._eye(bap), point or (x + 2, y, z - 0.5))
        run.step({bap: (*direction, 16)})
        flight = None
        for _ in range(95):
            run.step({bap: (*direction, 0)})
            flight = fields.flights.get(bap.body) or flight
            device = fields.devices.get(bap.body)
            if device is not None:
                break
        self.assertIsNotNone(flight)
        self.assertIsNotNone(device)
        self.assertNotIn(bap.body, fields.flights)
        self.assertEqual(flight.gravity, 20)
        self.assertNotEqual(flight.body, device.body)
        self.assertEqual(device.health, 150)
        instance, = device.component.instances.values()
        self.assertEqual(instance.graph.index, 0x1762)
        self.assertEqual(instance.vars[1165].value(), 10)
        return device

    def fire(self, run, ana, point, button, *, scoped=False, frames=58):
        direction = aim(run.match.combat._eye(ana), point)
        held = 2 if scoped else 0
        for _ in range(35 if scoped else 0):
            run.step({ana: (*direction, held)})
        run.step({ana: (*direction, button | held)})
        for _ in range(frames):
            run.step({ana: (*direction, held)})

    def test_rifles_stop_at_field_floor_and_only_credit_effective_damage(self):
        for scoped in (False, True):
            with self.subTest(scoped=scoped):
                run, ana, bap, vitals = self.arena()
                device = self.deploy(run, bap)
                vitals.damage(160)
                x, y, z = bap.mover.position
                self.fire(run, ana, (x, y + 1, z), 1, scoped=scoped)
                self.assertEqual(vitals.total, 20)
                self.assertFalse(vitals.dead)
                self.assertEqual(device.health, 150)
                self.assertEqual(ana.stats.value(stats.HERO_DAMAGE), 20)

    def test_grenade_keeps_floor_but_blocks_field_healing(self):
        run, ana, bap, vitals = self.arena()
        self.deploy(run, bap)
        fight = run.match.combat
        vitals.damage(160)
        x, y, z = bap.mover.position
        self.fire(run, ana, (x, y + 1, z), 16)
        self.assertEqual(vitals.total, 20)
        self.assertGreater(vitals.healing_block_until, fight.now)
        vitals.damage(10)  # Arrange a below-floor recipient while the grenade is active.
        for _ in range(20):
            run.step({})
        self.assertEqual(vitals.total, 10)
        self.assertFalse(vitals.dead)

    def test_field_heals_below_floor_and_allows_dart_status_at_floor(self):
        run, ana, bap, vitals = self.arena()
        vitals.damage(190)
        self.deploy(run, bap)
        fight = run.match.combat
        self.assertEqual(vitals.total, 20)
        x, y, z = bap.mover.position
        self.fire(run, ana, (x, y + 1, z), 8)
        self.assertTrue(fight.is_asleep(bap))
        self.assertEqual(vitals.total, 20)
        self.assertEqual(ana.stats.value(stats.HERO_DAMAGE), 0)

    def test_ana_attacks_damage_hostile_device_and_ignore_friendly_device(self):
        cases = ((1, False, 70), (1, True, 70), (8, False, 5), (16, False, 50), (0x800, False, 30))
        for enemy in (True, False):
            for button, scoped, amount in cases:
                with self.subTest(enemy=enemy, button=button, scoped=scoped):
                    run, ana, bap, _ = self.arena(enemy=enemy)
                    device = self.deploy(run, bap)
                    if button == 0x800:
                        x, y, z = device.position
                        self.place(ana, (x, y, z - 1.35))
                        for _ in range(15):
                            run.step({})
                    self.fire(run, ana, device.centre(), button, scoped=scoped)
                    self.assertEqual(device.health, 150 - amount if enemy else 150)

    def test_destroying_device_or_field_expiry_removes_protection(self):
        for ending in ("destroyed", "expired", "replacement"):
            with self.subTest(ending=ending):
                run, ana, bap, vitals = self.arena()
                device = self.deploy(run, bap)
                vitals.damage(180)
                fight = run.match.combat
                if ending == "destroyed":
                    device.health = 50
                    self.fire(run, ana, device.centre(), 1, scoped=True)
                    self.assertEqual(device.health, 0)
                elif ending == "replacement":
                    self.assertTrue(run.match.switch_hero(bap, find_hero('Soldier: 76')))
                    for _ in range(15):
                        run.step({})
                    target = next(target for target in fight._targets() if target.owner is bap)
                    vitals = fight.vitals_of(target)
                    vitals.damage(180)
                else:
                    for _ in range(380):
                        run.step({})
                        if device.component.world.retired:
                            break
                self.assertNotIn(device, fight.baptiste_fields.devices.values())
                self.assertTrue(device.component.world.retired)
                self.assertFalse(device.component.instances)
                x, y, z = bap.mover.position
                self.fire(run, ana, (x, y + 1, z), 1)
                self.assertTrue(vitals.dead)

    def test_field_range_and_static_los_gate_body_protection(self):
        for cover, offset in ((False, 9), (False, 11), (True, 7)):
            with self.subTest(cover=cover, offset=offset):
                run, ana, bap, vitals = self.arena()
                device = self.deploy(run, bap)
                x, y, z = device.position
                self.place(ana, (x, y, z + offset - 2))
                self.place(bap, (x, y, z + offset))
                if cover:
                    fixture.set_static_wall(run, ana, -1)
                for _ in range(15):
                    run.step({})
                vitals.damage(160)
                self.fire(run, ana, (x, y + 1, z + offset), 1)
                if not cover and offset < 10:
                    self.assertEqual(vitals.total, 20)
                    self.assertFalse(vitals.dead)
                else:
                    self.assertTrue(vitals.dead)

    def test_ana_receives_floor_healing_only_from_allied_field(self):
        for enemy in (False, True):
            with self.subTest(enemy=enemy):
                run, ana, bap, _ = self.arena(enemy=enemy)
                fight = run.match.combat
                target = next(target for target in fight._targets() if target.owner is ana)
                vitals = fight.vitals_of(target)
                vitals.damage(190)
                self.deploy(run, bap)
                self.assertEqual(vitals.total, 10 if enemy else 20)

    def test_allied_field_preserves_ana_against_actual_enemy_rifle(self):
        run = Run(fixture.TEAM_MAP, ("Ana", "Baptiste", "Soldier"))
        ana, bap, soldier = run.players
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        self.assertTrue(run.match.switch_hero(ana, find_hero('Ana')))
        self.assertTrue(run.match.switch_hero(bap, find_hero('Baptiste')))
        ana.team, bap.team, soldier.team = 0, 0, 1
        x, y, z = ana.mover.position
        self.place(bap, (x + 2, y, z + 2))
        self.place(soldier, (x, y, z + 6))
        for _ in range(30):
            run.step({})
        fight = run.match.combat
        target = next(target for target in fight._targets() if target.owner is ana)
        vitals = fight.vitals_of(target)
        vitals.damage(175)
        device = self.deploy(run, bap)
        self.fire(run, soldier, (x, y + 1, z), 1)
        self.assertEqual(vitals.total, 20)
        self.assertEqual(soldier.stats.value(stats.HERO_DAMAGE), 5)
        for _ in range(380):
            run.step({})
            if device.component.world.retired:
                break
        self.assertTrue(device.component.world.retired)
        self.fire(run, soldier, (x, y + 1, z), 1)
        self.assertTrue(vitals.dead)

    def test_grenade_destroying_device_preserves_floor_for_its_own_blast(self):
        run, ana, bap, vitals = self.arena()
        device = self.deploy(run, bap)
        device.health = 40
        vitals.damage(160)
        self.fire(run, ana, device.centre(), 16)
        self.assertEqual(device.health, 0)
        self.assertTrue(device.component.world.retired)
        self.assertEqual(vitals.total, 20)
        self.assertGreater(vitals.healing_block_until, run.match.combat.now)
        x, y, z = bap.mover.position
        self.fire(run, ana, (x, y + 1, z), 1)
        self.assertTrue(vitals.dead)

    def wall_covers_device(self):
        run = Run(fixture.TEAM_MAP, ("Ana", "Baptiste", "Mei"))
        ana, bap, mei = run.players
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        for player, name, team in ((ana, "Ana", 0), (bap, "Baptiste", 1), (mei, "Mei", 1)):
            self.assertTrue(run.match.switch_hero(player, find_hero(name)))
            player.team = team
        x, y, z = ana.mover.position
        self.place(bap, (x, y, z + 9))
        self.place(mei, (x, y, z + 8))
        for _ in range(30):
            run.step({})
        fight = run.match.combat
        device = self.deploy(run, bap, point=(x, y, z + 6))
        placement = aim(fight._eye(mei), (x, y, z + 4))
        for button in (16, 0, 1, 0):
            run.step({mei: (*placement, button)})
        pillars = tuple(fight.ice_wall.pieces.values())
        self.assertEqual(len(pillars), 5)

        self.fire(run, ana, device.centre(), 1, scoped=True, frames=20)
        centre, = (piece for piece in pillars if piece.health != 400)
        self.assertEqual(centre.health, 330)
        self.assertEqual(device.health, 150)
        return run, ana, mei, device, centre, placement

    def test_enemy_ice_wall_covers_immortality_device_until_recalled(self):
        run, ana, mei, device, centre, placement = self.wall_covers_device()
        fight = run.match.combat
        self.fire(run, ana, device.centre(), 16, frames=50)
        self.assertEqual(centre.health, 280)
        self.assertEqual(device.health, 150)
        self.assertFalse(device.component.world.retired)

        run.step({mei: (*placement, 16)})
        self.assertFalse(fight.ice_wall.pieces)
        self.fire(run, ana, device.centre(), 8, frames=50)
        self.assertEqual(device.health, 145)

    def test_breaking_ice_wall_still_covers_immortality_device_from_that_grenade(self):
        run, ana, _, device, centre, _ = self.wall_covers_device()
        fight = run.match.combat
        centre.health = 40
        self.fire(run, ana, device.centre(), 16, frames=50)
        self.assertEqual(centre.health, 0)
        self.assertNotIn(centre.entity, fight.ice_wall.pieces)
        self.assertEqual(len(fight.ice_wall.pieces), 4)
        self.assertEqual(device.health, 150)
        self.fire(run, ana, device.centre(), 8, frames=50)
        self.assertEqual(device.health, 145)

    def test_flight_and_landed_device_are_causal_snapshot_state(self):
        import test_server_snapshot as stored

        case = stored.ServerSnapshotTests()
        session, peers, players = case.scene(two=True)
        bap = players[1]
        peers[1].message(21616, stored.pick(bap.entity, find_hero('Baptiste').guid), now=100.8)
        peers[1].commands(bap.body_script.frame + 1, 40, 16)
        fight = bap.match.combat
        flight = fight.baptiste_fields.flights[bap.body]
        with patch.object(runtime.Var, "value", side_effect=AssertionError("snapshot evaluated a variable")):
            before = stored.snapshot_digest(case.capture(session))
            flight.remaining -= 0.01
            self.assertNotEqual(before, stored.snapshot_digest(case.capture(session)))
        fight.update(fight.now + 80 * 0.016, fight.tick + 80, frames=False)
        device = fight.baptiste_fields.devices[bap.body]
        self.assertNotIn(bap.body, fight.baptiste_fields.flights)
        self.assertNotEqual(flight.body, device.body)
        with patch.object(runtime.Var, "value", side_effect=AssertionError("snapshot evaluated a variable")):
            landed = stored.snapshot_digest(case.capture(session))
            device.health -= 5
            damaged = stored.snapshot_digest(case.capture(session))
        self.assertNotEqual(before, landed)
        self.assertNotEqual(landed, damaged)
