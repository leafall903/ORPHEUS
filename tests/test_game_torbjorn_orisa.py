"""Torbjorn combat against Orisa's authored thrown barrier."""

import math
import os
import unittest
from dataclasses import replace
from unittest.mock import patch

import test_game_torbjorn_defenses as defenses
from test_game_combat import aim
from test_game_mover import box, wall

from ow174.game import combat, fortify, sigma_grasp, stats
from ow174.game.collision import World
from ow174.game.commands import Command
from ow174.game.content import SOLDIER, heroes
from ow174.game.script import expr


@unittest.skipUnless(os.environ.get("ORPHEUS_ORISA_COLLISION")
                     and os.environ.get("ORPHEUS_TORBJORN_GRAPH_BUNDLE")
                     and os.environ.get("ORPHEUS_PROJECTILE_TAGS"), "needs prepared Orisa and hero data")
class TorbjornOrisaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        defenses.TorbjornDefenseTests.setUpClass()
        cls.addClassCleanup(defenses.TorbjornDefenseTests.tearDownClass)

    def arena(self):
        fixture = defenses.TorbjornDefenseTests()
        run, torb, orisa, other = fixture.arena("Orisa", 8)
        x, y, z = torb.mover.position
        fixture.place(other, (x + 100, y, z + 100))
        return fixture, run, torb, orisa, other

    def deploy(self, run, orisa):
        service = run.match.combat.orisa_barriers
        run.step({orisa: (-32768, 8192, 16)})
        flight = None
        for _ in range(40):
            run.step({orisa: (-32768, 8192, 0)})
            flight = service.flights.get(orisa.body, flight)
            if orisa.body in service.actors:
                break
        self.assertIsNotNone(flight)
        self.assertAlmostEqual(flight.gravity, 9.8, places=5)
        actor = service.actors[orisa.body]
        self.assertEqual(actor.health, 600)
        self.assertLess(actor.position[2], orisa.mover.position[2])
        return actor

    @unittest.skipUnless(os.environ.get("ORPHEUS_ORISA_SUPERCHARGER"), "needs authored Supercharger data")
    def test_supercharger_actual_deployment_boosts_allied_rivet_then_retires(self):
        fixture, run, torb, orisa, enemy = self.arena()
        match = run.match
        fight = match.combat
        service = fight.superchargers
        match.game_map = replace(match.game_map, free_for_all=False)
        orisa.team = torb.team
        enemy.team = 1 - torb.team
        x, y, z = torb.mover.position
        fixture.place(orisa, (x - 3, y, z))
        fixture.place(enemy, (x, y, z + 5))
        fixture.advance(run, torb, orisa, 35)
        script = orisa.body_script
        charge = script.component.entity_var(342)
        cost = script.component.entity_var(338).value()
        script.set_ult_charge(cost - 1)
        run.step({orisa: (0, 0, 32)})
        fixture.advance(run, torb, orisa, 45)
        self.assertEqual(charge.value(), cost - 1)
        self.assertFalse(service.active)
        script.set_ult_charge(cost)
        run.step({orisa: (0, 0, 32)})
        self.assertEqual(charge.value(), 0)
        for _ in range(75):
            if service.active:
                break
            run.step({})
        device, = service.active.values()
        self.assertEqual(device.health, 200)
        self.assertEqual(device.until - run.frame, 938)
        self.assertEqual(device.script.graph.index, 0x0D00)
        self.assertTrue(device.script.state(1).active)
        status = service.buffs[(device.entity, torb.body)]
        self.assertEqual(status.graph.index, 0x0D01)
        self.assertTrue(status.state(3).active)
        self.assertEqual(service.scale(torb), 1.5)
        self.assertEqual(service.scale(enemy), 1)
        hp = fixture.health(run, enemy)
        self.assert_rivet_damage(fixture, run, torb, enemy, hp, 105)
        self.assertEqual(torb.body_script.component.entity_var(342).value(), 105)

        fixture.place(torb, (x + 28, y, z))
        run.step({})
        self.assertEqual(service.scale(torb), 1)
        self.assertNotIn(status.id, torb.body_script.component.instances)
        fixture.place(torb, (x, y, z))
        run.step({})
        self.assertEqual(service.scale(torb), 1.5)
        for _ in range(940):
            if not service.active:
                break
            run.step({})
        self.assertEqual(run.frame, device.until)
        self.assertFalse(service.active)
        self.assertFalse(service.buffs)
        self.assertEqual(service.scale(torb), 1)
        self.assert_rivet_damage(fixture, run, torb, enemy, hp, 70)
        self.assertEqual(torb.body_script.component.entity_var(342).value(), 175)

    @unittest.skipUnless(os.environ.get("ORPHEUS_ORISA_SUPERCHARGER"), "needs authored Supercharger data")
    def test_supercharger_rivet_destruction_ends_boost_without_torbjorn_charge(self):
        fixture, run, torb, orisa, other = self.arena()
        match = run.match
        fight = match.combat
        service = fight.superchargers
        match.game_map = replace(match.game_map, free_for_all=False)
        orisa.team = other.team = 1 - torb.team
        script = orisa.body_script
        charge = script.component.entity_var(342)
        script.set_ult_charge(script.component.entity_var(338).value())
        run.step({orisa: (0, 0, 32)})
        for _ in range(75):
            if service.active:
                break
            run.step({})
        device, = service.active.values()
        self.assertEqual(service.scale(orisa), 1.5)
        self.assertEqual(service.scale(torb), 1)
        self.assertTrue(script.component.entity_var(842).value())
        tx, ty, tz = device.position
        fixture.place(torb, (tx, ty, tz + 5))
        fixture.place(orisa, (tx + 3, ty, tz + 3))
        fixture.advance(run, torb, orisa, 15)
        hp = fixture.health(run, torb)
        run.step({torb: (0, 0, 16)})
        aim_at_torb = fixture.body_aim(run, orisa, torb)
        run.step({orisa: (*aim_at_torb, 1)})
        fixture.advance(run, torb, orisa, 45)
        self.assertEqual(hp.over_armour.amount, 88.5)
        self.assertEqual(orisa.stats.value(stats.HERO_DAMAGE), 11.5)
        self.assertEqual(charge.value(), 0)

        direction = aim(fight._eye(torb), (tx, ty + .5, tz))
        for expected in (130, 60, 0):
            run.step({torb: (*direction, 1)})
            fixture.advance(run, torb, orisa, 45)
            self.assertEqual(device.health, expected)
            self.assertEqual(torb.stats.value(stats.HERO_DAMAGE), 0)
            self.assertEqual(torb.body_script.component.entity_var(342).value(), 0)
        self.assertFalse(service.active)
        self.assertFalse(service.buffs)
        self.assertFalse(script.component.entity_var(842).value())
        self.assertEqual(service.scale(orisa), 1)
        run.step({orisa: (*aim_at_torb, 1)})
        fixture.advance(run, torb, orisa, 20)
        self.assertEqual(hp.over_armour.amount, 82.5)
        self.assertEqual(orisa.stats.value(stats.HERO_DAMAGE), 17.5)
        self.assertEqual(charge.value(), 6)

    @unittest.skipUnless(os.environ.get("ORPHEUS_ORISA_SUPERCHARGER"), "needs authored Supercharger data")
    def test_supercharger_player_aura_keeps_turret_damage_separate(self):
        fixture, run, torb, orisa, enemy = self.arena()
        match = run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        orisa.team = torb.team
        enemy.team = 1 - torb.team
        x, y, z = torb.mover.position
        fixture.place(orisa, (x - 3, y, z))
        run.step({torb: (0, 4000, 8)})
        fixture.advance(run, torb, orisa, 225)
        turret = fight.turrets[torb.entity]
        self.assertEqual(turret.shots, 0)
        orisa.body_script.set_ult_charge(orisa.body_script.component.entity_var(338).value())
        run.step({orisa: (0, 0, 32)})
        for _ in range(75):
            if fight.superchargers.active:
                break
            run.step({})
        self.assertEqual(fight.superchargers.scale(torb), 1.5)
        self.assertEqual(fight.superchargers.scale(turret.actor), 1)
        device, = fight.superchargers.active.values()
        self.assertNotIn((device.entity, turret.actor.entity), fight.superchargers.buffs)
        tx, ty, tz = turret.actor.position
        fixture.place(enemy, (tx, ty, tz + 6))
        hp = fixture.health(run, enemy)
        for _ in range(25):
            if turret.shots:
                break
            run.step({})
        self.assertEqual(turret.shots, 1)
        self.assertEqual(hp.total, 186)
        self.assertEqual(torb.stats.value(stats.HERO_DAMAGE), 14)

    @unittest.skipUnless(os.environ.get("ORPHEUS_ORISA_SUPERCHARGER"), "needs authored Supercharger data")
    def test_supercharger_device_is_passive_snapshot_state(self):
        from test_server_snapshot import ServerSnapshotTests

        from ow174.game.server_snapshot import snapshot_digest

        case = ServerSnapshotTests()
        session, peers, players = case.scene()
        orisa = players[0]
        session.apply("switch_hero", {"account": orisa.account_lo, "hero": 0x02E000000000013E})
        peers[0].commands(orisa.body_script.frame + 1, 25, 0)
        script = orisa.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        peers[0].commands(script.frame + 1, 1, 32)
        peers[0].commands(script.frame + 1, 75, 0)
        fight = orisa.match.combat
        for _ in range(45):
            if fight.superchargers.active:
                break
            fight.update(fight.now + combat.TICK, fight.tick + 1, frames=False)
        device, = fight.superchargers.active.values()
        digest = snapshot_digest(case.capture(session))
        self.assertEqual(digest, snapshot_digest(case.capture(session)))
        device.health -= 1
        self.assertNotEqual(digest, snapshot_digest(case.capture(session)))

    @unittest.skipUnless(os.environ.get("ORPHEUS_ORISA_SUPERCHARGER"), "needs authored Supercharger data")
    def test_supercharger_lethal_rivet_respects_windup_flight_and_deployment(self):
        for phase in ("windup", "flight", "landed"):
            with self.subTest(phase=phase):
                fixture, run, torb, orisa, ally = self.arena()
                match = run.match
                fight = match.combat
                service = fight.superchargers
                match.game_map = replace(match.game_map, free_for_all=False)
                orisa.team = ally.team = 1 - torb.team
                x, y, z = orisa.mover.position
                fixture.place(torb, (x, y, z - 3))
                fixture.place(ally, (x + 4, y, z))
                fixture.advance(run, torb, orisa, 35)
                hp = fixture.health(run, orisa)
                script = orisa.body_script
                instance = next(i for i in script.component.instances.values() if i.graph.index == 0x0CF5)
                windup = instance.node_state(instance.graph.node(30))
                charge = script.component.entity_var(342)
                script.set_ult_charge(script.component.entity_var(338).value())
                run.step({orisa: (0, 0, 32)})
                self.assertEqual(charge.value(), 0)
                self.assertTrue(windup.active)
                if phase != "windup":
                    for _ in range(75):
                        if (phase == "flight" and fight.rockets) or (phase == "landed" and service.active):
                            break
                        run.step({})
                if phase == "flight":
                    launched, = fight.rockets
                    self.assertIs(launched.shooter, orisa)
                    self.assertFalse(service.active)
                if phase == "landed":
                    deployed, = service.active.values()
                    self.assertEqual(service.scale(ally), 1.5)
                hp.damage(hp.total - 1, armour_reduction=False)
                fight._sync_body_health(orisa, hp)
                self.assert_rivet_damage(fixture, run, torb, orisa, hp, 1)
                self.assertTrue(hp.dead)
                self.assertEqual(hp.killer, torb.body)
                self.assertEqual(torb.stats.value(stats.ELIMINATIONS), 1)
                self.assertEqual(charge.value(), 0)
                if phase == "flight":
                    self.assertIn(launched, fight.rockets)
                    self.assertFalse(service.active)
                if phase == "landed":
                    self.assertEqual(list(service.active.values()), [deployed])
                if phase == "windup":
                    self.assertFalse(windup.active)
                for _ in range(65):
                    run.step({orisa: (0, 0, 32)})
                    if phase == "windup":
                        self.assertFalse(service.active)
                        self.assertFalse(fight.rockets)
                if phase != "windup":
                    device, = service.active.values()
                    self.assertEqual(device.source_body, orisa.body)
                    self.assertEqual(device.health, 200)
                    self.assertEqual(service.scale(orisa), 1)
                    self.assertEqual(service.scale(ally), 1.5)
                    torb_hp = fixture.health(run, torb)
                    run.step({torb: (0, 0, 16)})
                    tx, ty, tz = torb.mover.position
                    direction = aim(fight._eye(ally), (tx, ty + .55, tz))
                    run.step({ally: (*direction, 1)})
                    fixture.advance(run, torb, orisa, 15)
                    self.assertEqual(torb_hp.over_armour.amount, 75)
                    self.assertEqual(ally.stats.value(stats.HERO_DAMAGE), 25)

    @unittest.skipUnless(os.environ.get("ORPHEUS_ORISA_SUPERCHARGER"), "needs authored Supercharger data")
    def test_supercharger_flashbang_cancels_windup_but_not_deployed_device(self):
        for landed in (False, True):
            with self.subTest(landed=landed):
                fixture, run, torb, orisa, cassidy = self.arena()
                match = run.match
                fight = match.combat
                service = fight.superchargers
                match.game_map = replace(match.game_map, free_for_all=False)
                self.assertTrue(match.switch_hero(cassidy, heroes()[0x02E0000000000042]))
                cassidy.team = torb.team
                orisa.team = 1 - torb.team
                x, y, z = orisa.mover.position
                fixture.place(cassidy, (x + 3, y, z))
                fixture.advance(run, torb, orisa, 35)
                script = orisa.body_script
                script.set_ult_charge(script.component.entity_var(338).value())
                instance = next(i for i in script.component.instances.values() if i.graph.index == 0x0CF5)
                windup = instance.node_state(instance.graph.node(30))
                run.step({orisa: (0, 0, 32)})
                self.assertTrue(windup.active)
                if landed:
                    for _ in range(75):
                        if service.active:
                            break
                        run.step({})
                    device, = service.active.values()
                direction = fixture.body_aim(run, cassidy, orisa)
                run.step({cassidy: (*direction, 16)})
                for _ in range(35):
                    if fight.is_stunned(orisa):
                        break
                    run.step({})
                self.assertTrue(fight.is_stunned(orisa))
                self.assertFalse(windup.active)
                self.assertEqual(script.component.entity_var(342).value(), 0)
                self.assertEqual(cassidy.stats.value(stats.HERO_DAMAGE), 20)
                fixture.advance(run, torb, orisa, 70)
                self.assertFalse(fight.rockets)
                self.assertEqual(list(service.active.values()), [device] if landed else [])
                self.assertEqual(service.scale(orisa), 1.5 if landed else 1)

    @unittest.skipUnless(os.environ.get("ORPHEUS_ORISA_SUPERCHARGER"), "needs authored Supercharger data")
    def test_supercharger_switch_cancels_uncommitted_cast_and_keeps_landed_aura(self):
        for phase in ("windup", "flight", "landed"):
            with self.subTest(phase=phase):
                fixture, run, torb, orisa, ally = self.arena()
                match = run.match
                fight = match.combat
                service = fight.superchargers
                match.game_map = replace(match.game_map, free_for_all=False)
                orisa.team = ally.team = 1 - torb.team
                x, y, z = orisa.mover.position
                fixture.place(ally, (x + 4, y, z))
                script = orisa.body_script
                body = orisa.body
                script.set_ult_charge(script.component.entity_var(338).value())
                run.step({orisa: (0, 0, 32)})
                if phase != "windup":
                    for _ in range(75):
                        if (phase == "flight" and fight.rockets) or (phase == "landed" and service.active):
                            break
                        run.step({})
                self.assertEqual(bool(fight.rockets), phase == "flight")
                self.assertEqual(bool(service.active), phase == "landed")
                before = list(service.active.values())
                self.assertTrue(match.switch_hero(orisa, heroes()[SOLDIER]))
                self.assertNotEqual(orisa.body, body)
                self.assertTrue(script.retired)
                self.assertFalse(fight.rockets)
                fixture.advance(run, torb, orisa, 65)
                self.assertFalse(fight.rockets)
                self.assertEqual(list(service.active.values()), before)
                self.assertEqual(service.scale(ally), 1.5 if phase == "landed" else 1)
                self.assertFalse(orisa.body_script.component.entity_var(842).value())

    @unittest.skipUnless(os.environ.get("ORPHEUS_ORISA_SUPERCHARGER"), "needs authored Supercharger data")
    def test_supercharger_rebinds_after_respawn_until_torbjorn_destroys_it(self):
        fixture, run, torb, orisa, ally = self.arena()
        match = run.match
        fight = match.combat
        service = fight.superchargers
        match.game_map = replace(match.game_map, free_for_all=False)
        orisa.team = ally.team = 1 - torb.team
        x, y, z = orisa.mover.position
        fixture.place(ally, (x + 5, y, z))
        old_body, old_script = orisa.body, orisa.body_script
        old_script.set_ult_charge(old_script.component.entity_var(338).value())
        run.step({orisa: (0, 0, 32)})
        for _ in range(75):
            if service.active:
                break
            run.step({})
        device, = service.active.values()
        self.assertEqual(service.scale(ally), 1.5)
        self.assertTrue(old_script.component.entity_var(842).value())
        hp = fixture.health(run, orisa)
        hp.damage(hp.total - 1, armour_reduction=False)
        fight._sync_body_health(orisa, hp)
        self.assert_rivet_damage(fixture, run, torb, orisa, hp, 1)
        self.assertTrue(hp.dead)
        self.assertEqual(hp.killer, torb.body)
        for _ in range(660):
            if orisa.body != old_body:
                break
            run.step({})
        self.assertNotEqual(orisa.body, old_body)
        self.assertTrue(old_script.retired)
        self.assertLess(run.frame, device.until)
        self.assertEqual(list(service.active.values()), [device])
        self.assertEqual(device.source_body, old_body)
        script = orisa.body_script
        source = next(i for i in script.component.instances.values() if i.graph.index == 0x0CF5)
        self.assertEqual(source.vars[40].value(), expr.Entity(device.entity))
        self.assertTrue(script.component.entity_var(842).value())
        script.ult_seconds = 0
        script.set_ult_charge(0)
        charge = script.component.entity_var(342)
        self.assertEqual(fixture.health(run, orisa).total, 450)
        tx, ty, tz = device.position
        fixture.place(torb, (tx, ty, tz + 5))
        fixture.place(orisa, (tx + 3, ty, tz + 3))
        fixture.advance(run, torb, orisa, 15)
        self.assertEqual(service.scale(orisa), 1.5)
        self.assertNotIn((device.entity, old_body), service.buffs)
        self.assertIn((device.entity, orisa.body), service.buffs)
        torb_hp = fixture.health(run, torb)
        run.step({torb: (0, 0, 16)})
        direction = fixture.body_aim(run, orisa, torb)
        run.step({orisa: (*direction, 1)})
        fixture.advance(run, torb, orisa, 45)
        self.assertEqual(torb_hp.over_armour.amount, 88.5)
        self.assertEqual(orisa.stats.value(stats.HERO_DAMAGE), 11.5)
        self.assertEqual(charge.value(), 0)
        prior = torb.body_script.component.entity_var(342).value()
        at_device = aim(fight._eye(torb), (tx, ty + .5, tz))
        for remaining in (130, 60, 0):
            run.step({torb: (*at_device, 1)})
            fixture.advance(run, torb, orisa, 45)
            self.assertEqual(device.health, remaining)
            self.assertEqual(torb.body_script.component.entity_var(342).value(), prior)
        self.assertFalse(service.active)
        self.assertFalse(service.buffs)
        self.assertFalse(script.component.entity_var(842).value())
        run.step({orisa: (*direction, 1)})
        fixture.advance(run, torb, orisa, 20)
        self.assertEqual(torb_hp.over_armour.amount, 82.5)
        self.assertEqual(orisa.stats.value(stats.HERO_DAMAGE), 17.5)
        self.assertEqual(charge.value(), 6)

    def boosted_molten_fixture(self, enemy_hero):
        fixture, run, torb, orisa, enemy = self.arena()
        match = run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        self.assertTrue(match.switch_hero(enemy, enemy_hero))
        enemy.body_script.ult_seconds = 0
        enemy.team = 1 - torb.team
        orisa.team = torb.team
        x, y, z = torb.mover.position
        fixture.place(orisa, (x - 3, y, z))
        fixture.place(enemy, (x + 100, y, z + 100))
        fixture.advance(run, torb, orisa, 35)
        torb.body_script.set_ult_charge(torb.body_script.component.entity_var(338).value())
        run.step({torb: (0, 4000, 32)})
        fixture.advance(run, torb, orisa, 45)
        run.step({torb: (0, 4000, 1)})
        for _ in range(100):
            if any(pool.phase == 1 for pool in fight.molten_pools.values()):
                break
            run.step({})
        pool, = fight.molten_pools.values()
        self.assertEqual(pool.phase, 1)
        self.assertIs(pool.owner, torb)
        px, py, pz = pool.position
        fixture.place(orisa, (px - 3, py, pz - 1))
        orisa.body_script.set_ult_charge(orisa.body_script.component.entity_var(338).value())
        run.step({orisa: (0, 0, 32)})
        for _ in range(75):
            if fight.superchargers.active:
                break
            run.step({})
        device, = fight.superchargers.active.values()
        self.assertEqual(fight.superchargers.scale(torb), 1.5)
        self.assertEqual(fight.superchargers.scale(enemy), 1)
        return fixture, run, torb, enemy, pool, device

    @unittest.skipUnless(os.environ.get("ORPHEUS_ORISA_SUPERCHARGER"), "needs authored Supercharger data")
    def test_supercharger_destruction_precedes_molten_damage_on_the_same_tick(self):
        torbjorn = heroes()[0x02E0000000000006]
        fixture, run, torb, enemy, pool, device = self.boosted_molten_fixture(torbjorn)
        fight = run.match.combat
        fixture.place(enemy, pool.position)
        hp = fixture.health(run, enemy)
        self.assertEqual(hp.total, 250)
        run.step({})
        self.assertAlmostEqual(hp.total, 244)
        self.assertAlmostEqual(torb.stats.value(stats.HERO_DAMAGE), 6)
        self.assertEqual(torb.body_script.component.entity_var(342).value(), 0)
        device.health = 1
        dx, dy, dz = device.position
        direction = aim(fight._eye(enemy), (dx, dy + .5, dz))
        run.step({enemy: (*direction, 1)})
        self.assertAlmostEqual(hp.total, 238)
        for _ in range(25):
            before = hp.total
            credit = torb.stats.value(stats.HERO_DAMAGE)
            run.step({})
            if device.health == 0:
                break
            self.assertAlmostEqual(before - hp.total, 6)
        self.assertEqual(device.health, 0)
        self.assertFalse(fight.superchargers.active)
        self.assertAlmostEqual(before - hp.total, 4)
        self.assertAlmostEqual(torb.stats.value(stats.HERO_DAMAGE) - credit, 4)
        self.assertEqual(torb.body_script.component.entity_var(342).value(), 0)
        self.assertEqual(enemy.body_script.component.entity_var(342).value(), 0)
        self.assertIs(fight.molten_pools[pool.entity], pool)

    @unittest.skipUnless(os.environ.get("ORPHEUS_ORISA_SUPERCHARGER"), "needs authored Supercharger data")
    def test_boosted_molten_splits_fortified_armor_then_loses_aura_on_device_break(self):
        fixture, run, torb, orisa, pool, device = self.boosted_molten_fixture(heroes()[fortify.HERO])
        fight = run.match.combat
        service = fight.superchargers
        torb.body_script.ult_seconds = 0
        charge = torb.body_script.component.entity_var(342)
        hp = fixture.health(run, orisa)
        hp.damage(hp.now[combat.ARMOUR_POOL] - 1.8, armour_reduction=False)
        fight._sync_body_health(orisa, hp)
        health = hp.now[combat.HEALTH_POOL]
        run.step({orisa: (0, 0, 8)})
        defense = fortify.state_for(orisa)
        self.assertIsNotNone(defense)
        self.assertEqual(service.scale(torb), 1.5)
        self.assertEqual(service.scale(orisa), 1)
        fixture.place(orisa, pool.position)
        run.step({})
        key = torb.entity, orisa.body
        burn = fight.molten_burns[key]
        self.assertAlmostEqual(hp.now[combat.ARMOUR_POOL], 0, places=5)
        self.assertAlmostEqual(health - hp.now[combat.HEALTH_POOL], 1.152, places=5)
        self.assertAlmostEqual(torb.stats.value(stats.HERO_DAMAGE), 2.952, places=5)
        self.assertEqual(len(torb.client.messages(combat.HIT)), 2)
        self.assertEqual(charge.value(), 0)

        device.health = 1
        dx, dy, dz = device.position
        direction = aim(fight._eye(orisa), (dx, dy + .5, dz))
        before = hp.total
        run.step({orisa: (*direction, 1)})
        self.assertAlmostEqual(before - hp.total, 2.304, places=5)
        for _ in range(25):
            before = hp.total
            credit = torb.stats.value(stats.HERO_DAMAGE)
            run.step({})
            self.assertIs(fortify.state_for(orisa), defense)
            self.assertIs(fight.molten_burns[key], burn)
            if not service.active:
                break
            self.assertAlmostEqual(before - hp.total, 2.304, places=5)
        self.assertEqual(device.health, 0)
        self.assertEqual(service.scale(torb), 1)
        self.assertAlmostEqual(before - hp.total, 1.536, places=5)
        self.assertAlmostEqual(torb.stats.value(stats.HERO_DAMAGE) - credit, 1.536, places=5)
        self.assertAlmostEqual(torb.stats.value(stats.HERO_DAMAGE), health - hp.total + 1.8, places=5)
        self.assertIs(fight.molten_pools[pool.entity], pool)
        self.assertEqual(charge.value(), 0)

    @unittest.skipUnless(os.environ.get("ORPHEUS_ORISA_SUPERCHARGER"), "needs authored Supercharger data")
    def test_supercharger_boosts_molten_against_attached_barrier_without_spill(self):
        zarya = next(hero for hero in heroes().values() if hero.name == "Zarya")
        fixture, run, torb, enemy, pool, _ = self.boosted_molten_fixture(zarya)
        fight = run.match.combat
        hp = fixture.health(run, enemy)
        health = hp.total
        run.step({enemy: (0, 0, 8)})
        bubble = fight.bubbles.for_target(enemy)
        self.assertIsNotNone(bubble)
        self.assertEqual(bubble.health, 200)
        fixture.place(enemy, pool.position)
        run.step({})
        self.assertAlmostEqual(bubble.health, 196.16)
        self.assertEqual(hp.total, health)
        self.assertEqual(torb.stats.value(stats.HERO_DAMAGE), 0)
        bubble.health = 2
        run.step({})
        self.assertIsNone(fight.bubbles.for_target(enemy))
        self.assertEqual(hp.total, health)
        self.assertEqual(torb.stats.value(stats.HERO_DAMAGE), 0)
        run.step({})
        self.assertAlmostEqual(hp.total, health - 3.84)
        self.assertAlmostEqual(torb.stats.value(stats.HERO_DAMAGE), 3.84)
        self.assertEqual(torb.body_script.component.entity_var(342).value(), 0)

    @unittest.skipUnless(os.environ.get("ORPHEUS_ORISA_SUPERCHARGER"), "needs authored Supercharger data")
    def test_supercharger_world_cover_and_hostile_mesh_block_rivet_boost_until_clear(self):
        for obstacle in ("world", "mesh"):
            with self.subTest(obstacle=obstacle):
                fixture, run, torb, caster, enemy = self.arena()
                match = run.match
                fight = match.combat
                service = fight.superchargers
                match.game_map = replace(match.game_map, free_for_all=False)
                caster.team = torb.team
                self.assertTrue(match.switch_hero(enemy, caster.hero))
                enemy.team = 1 - torb.team
                x, y, z = torb.mover.position
                fixture.place(caster, (x, y, z))
                fixture.place(enemy, (x + 30, y, z + 30))
                fixture.advance(run, torb, caster, 35)
                script = caster.body_script
                script.set_ult_charge(script.component.entity_var(338).value())
                run.step({caster: (0, 0, 32)})
                for _ in range(75):
                    if service.active:
                        break
                    run.step({})
                device, = service.active.values()
                self.assertEqual(service.scale(torb), 1.5)
                dx, dy, dz = device.position
                floor = match.collision_world
                if obstacle == "world":
                    torb_z = dz + 5
                    match.collision_world = World(
                        box(dx - 30, dx + 30, dy - 1, dy, dz - 30, dz + 30)
                        + wall(dx - 4, dz + 2, dx + 4, dz + 2, y0=dy, y1=dy + 4)
                    )
                else:
                    fixture.place(enemy, (dx, dy, dz + 9))
                    shield = self.deploy(run, enemy)
                    torb_z = shield.position[2] + 2
                    self.assertGreater(shield.position[2], dz)
                fixture.place(torb, (dx, dy, torb_z))
                fixture.place(enemy, (dx, dy, torb_z + 5))
                fixture.advance(run, torb, enemy, 4)
                self.assertEqual(service.scale(torb), 1)
                self.assertNotIn((device.entity, torb.body), service.buffs)
                hp = fixture.health(run, enemy)
                self.assert_rivet_damage(fixture, run, torb, enemy, hp, 65)
                fixture.advance(run, torb, enemy, 40)
                if obstacle == "world":
                    match.collision_world = floor
                    run.step({})
                else:
                    sx, sy, sz = shield.position
                    direction = aim(fight._eye(torb), (sx, sy + 1, sz))
                    for shot in range(9):
                        self.assertEqual(service.scale(torb), 1)
                        run.step({torb: (*direction, 1)})
                        fixture.advance(run, torb, enemy, 40)
                        self.assertEqual(shield.health, max(0, 600 - 70 * (shot + 1)))
                    self.assertFalse(shield.active())
                    self.assertEqual(torb.stats.value(stats.HERO_DAMAGE), 65)
                    self.assertEqual(torb.body_script.component.entity_var(342).value(), 65)
                self.assertIs(service.active[device.entity], device)
                self.assertEqual(service.scale(torb), 1.5)
                self.assert_rivet_damage(fixture, run, torb, enemy, hp, 100)
                self.assertEqual(torb.body_script.component.entity_var(342).value(), 165)

    @unittest.skipUnless(os.environ.get("ORPHEUS_ORISA_SUPERCHARGER"), "needs authored Supercharger data")
    def test_reflected_rivet_and_pellets_use_genji_supercharger_and_charge(self):
        for button, ordinary, boosted in ((1, 65, 100), (2, 75, 137.5)):
            for boost_genji in (False, True):
                with self.subTest(button=button, boost_genji=boost_genji):
                    fixture = defenses.TorbjornDefenseTests()
                    run, torb, genji, orisa = fixture.arena("Genji")
                    match = run.match
                    fight = match.combat
                    service = fight.superchargers
                    match.game_map = replace(match.game_map, free_for_all=False)
                    genji.team = 1 - torb.team
                    self.assertTrue(match.switch_hero(orisa, heroes()[0x02E000000000013E]))
                    orisa.team = genji.team if boost_genji else torb.team
                    x, y, z = torb.mover.position
                    fixture.place(orisa, (x - 5, y, z))
                    fixture.advance(run, torb, genji, 35)
                    script = orisa.body_script
                    script.set_ult_charge(script.component.entity_var(338).value())
                    run.step({orisa: (0, 0, 32)})
                    for _ in range(75):
                        if service.active:
                            break
                        run.step({})
                    self.assertEqual(len(service.active), 1)
                    self.assertEqual(service.scale(torb), 1 if boost_genji else 1.5)
                    self.assertEqual(service.scale(genji), 1.5 if boost_genji else 1)

                    fixture.step(run, torb, genji, fire=16)
                    fixture.advance(run, torb, genji, 60)
                    hp = fixture.health(run, torb)
                    self.assertEqual(hp.over_armour.amount, 100)
                    base = dict(hp.now)
                    fixture.cast(run, torb, genji)
                    fixture.step(run, torb, genji, fire=button)
                    fixture.advance(run, torb, genji, 20)

                    expected = boosted if boost_genji else ordinary
                    self.assertEqual(hp.total, 350 - expected)
                    self.assertEqual(hp.over_armour.amount, max(0, 100 - expected))
                    self.assertEqual(hp.now[combat.ARMOUR_POOL],
                                     base[combat.ARMOUR_POOL] - max(0, expected - 100))
                    self.assertEqual(fixture.health(run, genji).total, 200)
                    self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), expected)
                    self.assertEqual(genji.body_script.component.entity_var(342).value(), expected)
                    self.assertEqual(torb.stats.value(stats.HERO_DAMAGE), 0)
                    self.assertEqual(torb.body_script.component.entity_var(342).value(), 0)

    @unittest.skipUnless(os.environ.get("ORPHEUS_ORISA_SUPERCHARGER"), "needs authored Supercharger data")
    def test_inflight_rivet_uses_supercharger_at_impact_after_aura_crossing(self):
        for enter_aura in (False, True):
            with self.subTest(enter_aura=enter_aura):
                fixture, run, torb, orisa, enemy = self.arena()
                match = run.match
                fight = match.combat
                service = fight.superchargers
                match.game_map = replace(match.game_map, free_for_all=False)
                orisa.team = torb.team
                enemy.team = 1 - torb.team
                x, y, z = torb.mover.position
                fixture.place(orisa, (x, y, z))
                fixture.advance(run, torb, orisa, 35)
                script = orisa.body_script
                script.set_ult_charge(script.component.entity_var(338).value())
                run.step({orisa: (0, 0, 32)})
                for _ in range(75):
                    if service.active:
                        break
                    run.step({})
                device, = service.active.values()
                dx, dy, dz = device.position
                inside = (dx + 5, dy, dz + 5)
                outside = (dx + 29, dy, dz + 5)
                start, end = (outside, inside) if enter_aura else (inside, outside)
                fixture.place(torb, start)
                fixture.place(enemy, (start[0], dy, start[2] + 15))
                fixture.advance(run, torb, enemy, 4)
                self.assertEqual(service.scale(torb), 1 if enter_aura else 1.5)
                hp = fixture.health(run, enemy)
                before = hp.total
                fixture.step(run, torb, enemy, fire=1)
                rocket, = fight.rockets
                self.assertIs(rocket.shooter, torb)
                self.assertEqual(hp.total, before)

                fixture.place(torb, end)
                run.step({})
                self.assertIn(rocket, fight.rockets)
                self.assertEqual(service.scale(torb), 1.5 if enter_aura else 1)
                fixture.advance(run, torb, enemy, 25)
                damage = 105 if enter_aura else 70
                self.assertNotIn(rocket, fight.rockets)
                self.assertEqual(before - hp.total, damage)
                self.assertEqual(torb.stats.value(stats.HERO_DAMAGE), damage)
                self.assertEqual(torb.body_script.component.entity_var(342).value(), damage)


    @unittest.skipUnless(os.environ.get("ORPHEUS_ORISA_SUPERCHARGER"), "needs authored Supercharger data")
    def test_live_supercharger_and_fortify_combine_at_airborne_impact(self):
        cases = (("rivet", 1, 1, 37, 58), ("secondary", 2, 10, 37.5, 62.5), ("globule", 1, 1, 10, 17.5))
        for weapon, button, count, fortified, boosted in cases:
            for enter_aura in (False, True):
                with self.subTest(weapon=weapon, enter_aura=enter_aura):
                    fixture, run, torb, orisa, enemy = self.arena()
                    match = run.match
                    fight = match.combat
                    service = fight.superchargers
                    match.game_map = replace(match.game_map, free_for_all=False)
                    self.assertTrue(match.switch_hero(enemy, heroes()[fortify.HERO]))
                    orisa.team = torb.team
                    enemy.team = 1 - torb.team
                    x, y, z = torb.mover.position
                    fixture.place(orisa, (x, y, z))
                    fixture.advance(run, torb, orisa, 35)
                    script = orisa.body_script
                    script.set_ult_charge(script.component.entity_var(338).value())
                    run.step({orisa: (0, 0, 32)})
                    for _ in range(75):
                        if service.active:
                            break
                        run.step({})
                    device, = service.active.values()
                    dx, dy, dz = device.position
                    inside = (dx + 5, dy, dz + 5)
                    outside = (dx + 29, dy, dz + 5)
                    start, end = (outside, inside) if enter_aura else (inside, outside)
                    fixture.place(torb, start)
                    fixture.place(enemy, (start[0], dy, start[2] + 4))
                    fixture.advance(run, torb, enemy, 4)
                    self.assertEqual(service.scale(torb), 1 if enter_aura else 1.5)
                    torb.body_script.ult_seconds = 0
                    torb.body_script.set_ult_charge(10000 if weapon == "globule" else 0)
                    if weapon == "globule":
                        fixture.step(run, torb, enemy, fire=32)
                        fixture.advance(run, torb, enemy, 45)
                    hp = fixture.health(run, enemy)
                    before = hp.total
                    self.assertIsNone(fortify.state_for(enemy))
                    fixture.step(run, torb, enemy, fire=button)
                    volley = tuple(fight.rockets)
                    self.assertEqual(len(volley), count)
                    self.assertTrue(all(shot.shooter is torb and shot.distance == 0 for shot in volley))
                    fixture.place(torb, end)
                    run.step({enemy: (0, 0, 8)})
                    self.assertEqual(tuple(fight.rockets), volley)
                    self.assertEqual(service.scale(torb), 1.5 if enter_aura else 1)
                    self.assertIsNotNone(fortify.state_for(enemy))
                    self.assertEqual(service.scale(enemy), 1)
                    fixture.advance(run, torb, enemy, 20)

                    damage = boosted if enter_aura else fortified
                    self.assertAlmostEqual(before - hp.total, damage, places=5)
                    self.assertAlmostEqual(torb.stats.value(stats.HERO_DAMAGE), damage, places=5)
                    charge = torb.body_script.component.entity_var(342).value()
                    self.assertAlmostEqual(charge, 0 if weapon == "globule" else damage, places=5)
                    self.assertEqual((torb.entity, enemy.body) in fight.torbjorn_marks, weapon == "rivet")
                    self.assertEqual(device.health, 200)
                    self.assertFalse(fight.rockets or fight.molten_pools or fight.molten_burns)

    def test_fortify_reduces_rivet_until_expiry(self):
        self.check_fortify_weapon(None, 65, False)

    def test_fortify_denies_actual_cassidy_critical_until_expiry(self):
        self.check_fortify_weapon(0x02E0000000000042, 135, True)

    def check_fortify_weapon(self, attacker_hero, normal_damage, normal_critical):
        fixture, run, attacker, orisa, _ = self.arena()
        if attacker_hero is not None:
            self.assertTrue(run.match.switch_hero(attacker, heroes()[attacker_hero]))
            fixture.advance(run, attacker, orisa, 35)
        fight = run.match.combat
        hp = fixture.health(run, orisa)
        instance = next(i for i in orisa.body_script.component.instances.values() if i.graph.index == 0x0CF4)
        run.step({orisa: (0, 0, 8)})
        self.assertTrue(instance.state(4).active)
        x, y, z = attacker.mover.position
        fixture.place(orisa, (x, y, z + 4))
        ox, oy, oz = fight._eye(orisa)
        direction = aim(fight._eye(attacker), (ox, oy + .1, oz))
        for expected, critical in ((37, False), (normal_damage, normal_critical)):
            before = hp.total
            credit = attacker.stats.value(stats.HERO_DAMAGE)
            run.step({attacker: (*direction, 1)})
            for _ in range(40):
                if hp.total < before:
                    break
                run.step({})
            self.assertAlmostEqual(hp.total, before - expected, places=5)
            self.assertAlmostEqual(attacker.stats.value(stats.HERO_DAMAGE), credit + expected, places=5)
            hit = attacker.client.messages(combat.HIT)[-1]["+0x78"]
            self.assertEqual(bool(hit["+0x8"] & combat.CRITICAL), critical)
            for _ in range(270):
                if not instance.state(4).active:
                    break
                run.step({})
            self.assertFalse(instance.state(4).active)

    def test_fortify_cooldown_rejects_retry_and_restores_rivet_mitigation(self):
        fixture, run, torb, orisa, _ = self.arena()
        instance = next(i for i in orisa.body_script.component.instances.values() if i.graph.index == 0x0CF4)
        ability = instance.state(0)
        defense = instance.state(4)
        run.step({orisa: (0, 0, 8)})
        self.assertTrue(defense.active)
        activation = defense.counter
        for _ in range(270):
            if not defense.active:
                break
            run.step({})
        self.assertFalse(defense.active)
        self.assertGreater(ability.remaining(), 9)
        for _ in range(5):
            remaining = ability.remaining()
            run.step({orisa: (0, 0, 8)})
            run.step({})
            self.assertEqual(defense.counter, activation)
            self.assertFalse(defense.active)
            self.assertAlmostEqual(ability.remaining(), remaining - .032, places=4)
        hp = fixture.health(run, orisa)
        self.assert_rivet_damage(fixture, run, torb, orisa, hp, 65)
        for _ in range(660):
            if ability.remaining() <= 0:
                break
            run.step({})
        self.assertEqual(ability.remaining(), 0)
        run.step({orisa: (0, 0, 8)})
        self.assertTrue(defense.active)
        self.assertGreater(defense.counter, activation)
        self.assert_rivet_damage(fixture, run, torb, orisa, hp, 37)

    def test_fortify_replacement_cannot_bypass_original_cooldown(self):
        for replacement in ("active_switch", "cooling_switch", "reselect"):
            with self.subTest(replacement=replacement):
                fixture, run, torb, orisa, control = self.arena()
                match = run.match
                orisa_hero = orisa.hero
                pose = tuple(orisa.mover.position)
                self.assertTrue(match.switch_hero(control, orisa_hero))
                x, y, z = pose
                fixture.place(control, (x + 20, y, z + 20))
                fixture.advance(run, torb, orisa, 35)
                run.step({orisa: (0, 0, 8), control: (0, 0, 8)})
                self.assertIsNotNone(fortify.state_for(orisa))
                baseline = fortify.state_for(control).instance.state(0)
                for _ in range(25):
                    run.step({})
                if replacement == "cooling_switch":
                    for _ in range(250):
                        if baseline.remaining() > 0:
                            break
                        run.step({})
                    self.assertGreater(baseline.remaining(), 9)

                old_body = orisa.body
                if replacement != "reselect":
                    self.assertTrue(match.switch_hero(orisa, heroes()[SOLDIER]))
                    self.assertIsNone(fortify.state_for(orisa))
                    for _ in range(50):
                        run.step({})
                self.assertTrue(match.switch_hero(orisa, orisa_hero))
                self.assertNotEqual(orisa.body, old_body)
                self.assertIsNone(fortify.state_for(orisa))
                instance = next(i for i in orisa.body_script.component.instances.values()
                                if i.graph.index == 0x0CF4)
                ability = instance.state(0)
                self.assertGreater(ability.remaining(), 8)
                fixture.place(orisa, pose)
                for _ in range(5):
                    run.step({orisa: (0, 0, 8)})
                    self.assertIsNone(fortify.state_for(orisa))
                    run.step({})
                hp = fixture.health(run, orisa)
                self.assert_rivet_damage(fixture, run, torb, orisa, hp, 65)

                for _ in range(275):
                    if baseline.remaining() > 0:
                        break
                    run.step({})
                self.assertGreater(baseline.remaining(), 0)
                self.assertAlmostEqual(ability.remaining(), baseline.remaining(), delta=.0161)
                for _ in range(650):
                    if max(ability.remaining(), baseline.remaining()) <= 0:
                        break
                    run.step({})
                    self.assertAlmostEqual(ability.remaining(), baseline.remaining(), delta=.0161)
                self.assertEqual(ability.remaining(), 0)
                self.assertEqual(baseline.remaining(), 0)
                run.step({orisa: (0, 0, 8), control: (0, 0, 8)})
                self.assertIsNotNone(fortify.state_for(orisa))
                self.assertIsNotNone(fortify.state_for(control))
                self.assert_rivet_damage(fixture, run, torb, orisa, hp, 37)


    def test_fortify_death_and_body_replacement_retire_mitigation(self):
        for ending in ("death", "reselect", "switch"):
            with self.subTest(ending=ending):
                fixture, run, torb, orisa, _ = self.arena()
                match = run.match
                fight = match.combat
                old_body, old_script = orisa.body, orisa.body_script
                old_pose = tuple(orisa.mover.position)
                instance = next(i for i in old_script.component.instances.values() if i.graph.index == 0x0CF4)
                run.step({orisa: (0, 0, 8)})
                self.assertTrue(instance.state(4).active)
                if ending == "death":
                    hp = fixture.health(run, orisa)
                    hp.damage(hp.total - 1, armour_reduction=False)
                    fight._sync_body_health(orisa, hp)
                    self.assert_rivet_damage(fixture, run, torb, orisa, hp, 1)
                    self.assertTrue(hp.dead)
                    self.assertEqual(hp.killer, torb.body)
                    self.assertEqual(torb.stats.value(stats.ELIMINATIONS), 1)
                    self.assertTrue(old_script.component.owner_dead)
                    for frame in range(24):
                        run.step({orisa: (0, 0, 8 if frame % 2 == 0 else 0)})
                        self.assertFalse(instance.state(4).active)
                        self.assertTrue(hp.dead)
                    for _ in range(660):
                        if orisa.body != old_body:
                            break
                        run.step({})
                else:
                    hero = orisa.hero if ending == "reselect" else heroes()[SOLDIER]
                    self.assertTrue(match.switch_hero(orisa, hero))

                self.assertNotEqual(orisa.body, old_body)
                self.assertTrue(old_script.retired)
                self.assertIsNone(fortify.state_for(orisa))
                fixture.place(orisa, old_pose)
                fixture.advance(run, torb, orisa, 40)
                hp = fixture.health(run, orisa)
                switched = ending == "switch"
                self.assertEqual(hp.total, 200 if switched else 450)
                self.assert_rivet_damage(fixture, run, torb, orisa, hp, 70 if switched else 65)
                if ending == "death":
                    run.step({orisa: (0, 0, 8)})
                    self.assertIsNotNone(fortify.state_for(orisa))
                    fixture.advance(run, torb, orisa, 40)
                    self.assert_rivet_damage(fixture, run, torb, orisa, hp, 37)

    def assert_rivet_damage(self, fixture, run, torb, target, hp, expected):
        before = hp.total
        credit = torb.stats.value(stats.HERO_DAMAGE)
        fixture.step(run, torb, target, fire=1)
        for _ in range(40):
            if hp.total < before:
                break
            run.step({})
        self.assertAlmostEqual(before - hp.total, expected, places=5)
        self.assertAlmostEqual(torb.stats.value(stats.HERO_DAMAGE) - credit, expected, places=5)

    def test_fortified_fusion_driver_trades_rivet_across_overload_expiry_and_exhaustion(self):
        for expires in (True, False):
            with self.subTest(expires=expires):
                fixture, run, torb, orisa, other = self.arena()
                match = run.match
                fight = match.combat
                match.game_map = replace(match.game_map, free_for_all=False)
                orisa.team = other.team = 1 - torb.team
                hp = fixture.health(run, torb)
                orisa_hp = fixture.health(run, orisa)
                base = dict(hp.now)
                rifle = next(i for i in orisa.body_script.component.instances.values()
                             if i.graph.index == 0x0BD4)
                ammo = rifle.vars[53]
                charge = orisa.body_script.component.entity_var(342)
                self.assertEqual(ammo.value(), 150)
                fixture.step(run, torb, orisa, fire=16)
                grant = hp.over_armour.source
                fixture.advance(run, torb, orisa, 260 if expires else 45)
                fixture.step(run, torb, orisa, defense=8)
                self.assertIsNotNone(fortify.state_for(orisa))
                fixture.step(run, torb, orisa, fire=1, defense=1)
                for _ in range(25):
                    if torb.stats.value(stats.HERO_DAMAGE) and orisa.stats.value(stats.HERO_DAMAGE):
                        break
                    fixture.step(run, torb, orisa, defense=1)
                self.assertAlmostEqual(orisa_hp.total, 413, places=5)
                self.assertAlmostEqual(torb.stats.value(stats.HERO_DAMAGE), 37, places=5)
                self.assertAlmostEqual(torb.body_script.component.entity_var(342).value(), 37, places=5)
                self.assertGreater(orisa.stats.value(stats.HERO_DAMAGE), 0)
                self.assertLess(ammo.value(), 150)

                for _ in range(200):
                    previous = hp.over_armour.amount
                    credit = orisa.stats.value(stats.HERO_DAMAGE)
                    self.assertEqual(hp.now, base)
                    fixture.step(run, torb, orisa, defense=1)
                    dealt = orisa.stats.value(stats.HERO_DAMAGE) - credit
                    if hp.over_armour.amount == 0:
                        break
                    self.assertEqual(hp.over_armour.source, grant)
                    self.assertEqual(previous - hp.over_armour.amount, dealt)
                    self.assertIn(dealt, (0, 6))
                else:
                    self.fail("Fusion Driver never crossed the Overload boundary")
                self.assertGreater(previous, 0)
                if expires:
                    self.assertIsNone(hp.over_armour.source)
                    self.assertEqual(hp.now, base)
                    self.assertLess(previous, 100)
                else:
                    self.assertEqual(hp.over_armour.source, grant)
                    self.assertEqual(dealt, 6)
                    self.assertEqual(hp.now[combat.ARMOUR_POOL], base[combat.ARMOUR_POOL] - (6 - previous))
                self.assertEqual(hp.now[combat.HEALTH_POOL], base[combat.HEALTH_POOL])
                self.assertAlmostEqual(charge.value(), orisa.stats.value(stats.HERO_DAMAGE), places=5)

                armor = hp.now[combat.ARMOUR_POOL]
                for _ in range(25):
                    fixture.step(run, torb, orisa, defense=1)
                    if hp.now[combat.ARMOUR_POOL] < armor:
                        break
                self.assertEqual(hp.now[combat.ARMOUR_POOL], armor - 6)
                self.assertAlmostEqual(charge.value(), orisa.stats.value(stats.HERO_DAMAGE), places=5)
                held_ammo = ammo.value()
                fixture.advance(run, torb, orisa, 20)
                self.assertEqual(ammo.value(), held_ammo)
                self.assertFalse(fight.rockets)
                earned = charge.value()
                old_body = orisa.body
                self.assertTrue(match.switch_hero(orisa, orisa.hero))
                self.assertNotEqual(orisa.body, old_body)
                self.assertEqual(orisa.body_script.component.entity_var(342).value(), earned)

    def test_rivet_and_landed_molten_kill_orisa_without_erasing_committed_fusion_round(self):
        for lethal in ("rivet", "molten"):
            with self.subTest(lethal=lethal):
                fixture, run, torb, orisa, _ = self.arena()
                fight = run.match.combat
                x, y, z = torb.mover.position
                pool = None
                if lethal == "molten":
                    fixture.place(orisa, (x, y, z + 100))
                    torb.body_script.set_ult_charge(torb.body_script.component.entity_var(338).value())
                    run.step({torb: (0, 4000, 32)})
                    fixture.advance(run, torb, orisa, 45)
                    run.step({torb: (0, 4000, 1)})
                    for _ in range(100):
                        if any(item.phase == 1 for item in fight.molten_pools.values()):
                            break
                        run.step({})
                    pool, = fight.molten_pools.values()
                    self.assertEqual(pool.phase, 1)
                    self.assertIs(pool.owner, torb)
                    px, py, pz = pool.position
                    fixture.place(orisa, (px + 6, py, pz))
                else:
                    fixture.place(orisa, (x, y, z + 16))

                hp = fixture.health(run, orisa)
                torb_hp = fixture.health(run, torb)
                hp.damage(hp.total - 1, armour_reduction=False)
                fight._sync_body_health(orisa, hp)
                fixture.advance(run, torb, orisa, 4)
                self.assertEqual(hp.total, 1)
                body, script = orisa.body, orisa.body_script
                rifle = next(i for i in script.component.instances.values() if i.graph.index == 0x0BD4)
                ammo = rifle.vars[53]
                direction = fixture.body_aim(run, orisa, torb)
                if lethal == "rivet":
                    fixture.step(run, torb, orisa, fire=1)
                    rivet, = fight.rockets
                    self.assertIs(rivet.shooter, torb)
                    for _ in range(25):
                        if rivet.distance >= 11:
                            break
                        run.step({})
                    self.assertIn(rivet, fight.rockets)
                run.step({orisa: (*direction, 1)})
                round_, = [rocket for rocket in fight.rockets if rocket.shooter is orisa]
                self.assertEqual(ammo.value(), 149)
                self.assertEqual(hp.total, 1)
                if pool is not None:
                    fixture.place(orisa, pool.position)

                with patch.object(fight, "_shoot", wraps=fight._shoot) as volleys:
                    for _ in range(12):
                        run.step({orisa: (*direction, 1)})
                        if hp.dead:
                            break
                    self.assertTrue(hp.dead)
                    self.assertEqual(hp.killer, torb.body)
                    self.assertTrue(script.component.owner_dead)
                    self.assertIn(round_, fight.rockets)
                    self.assertEqual(torb_hp.total, 250)
                    for _ in range(35):
                        run.step({orisa: (*direction, 1)})
                    self.assertFalse(any(call.args[0] is orisa for call in volleys.call_args_list))
                self.assertNotIn(round_, fight.rockets)
                self.assertEqual(torb_hp.total, 244)
                self.assertEqual(orisa.stats.value(stats.HERO_DAMAGE), 6)
                self.assertEqual(torb.stats.value(stats.HERO_DAMAGE), 1)
                self.assertEqual(torb.stats.value(stats.ELIMINATIONS), 1)
                charge = torb.body_script.component.entity_var(342)
                self.assertEqual(charge.value(), 0 if pool is not None else 1)
                if pool is not None:
                    self.assertIs(fight.molten_pools[pool.entity], pool)

                for _ in range(660):
                    if orisa.body != body:
                        break
                    run.step({})
                self.assertNotEqual(orisa.body, body)
                self.assertTrue(script.retired)
                self.assertEqual(fixture.health(run, orisa).total, 450)
                fixture.place(orisa, (x, y, z + 5))
                fixture.advance(run, torb, orisa, 4)
                run.step({orisa: (*fixture.body_aim(run, orisa, torb), 1)})
                fresh, = [rocket for rocket in fight.rockets if rocket.shooter is orisa]
                self.assertIsNot(fresh, round_)
                fixture.advance(run, torb, orisa, 25)
                self.assertEqual(torb_hp.total, 238)
                self.assertEqual(orisa.stats.value(stats.HERO_DAMAGE), 12)


    def test_flashbang_interrupts_fusion_driver_unless_fortified(self):
        for fortified in (False, True):
            with self.subTest(fortified=fortified):
                fixture, run, torb, orisa, cassidy = self.arena()
                match = run.match
                fight = match.combat
                match.game_map = replace(match.game_map, free_for_all=False)
                self.assertTrue(match.switch_hero(cassidy, heroes()[0x02E0000000000042]))
                cassidy.team = torb.team
                orisa.team = 1 - torb.team
                x, y, z = torb.mover.position
                fixture.place(orisa, (x, y, z + 16))
                fixture.place(cassidy, (x + 2.5, y, z + 16))
                fixture.advance(run, torb, orisa, 35)
                rifle = next(i for i in orisa.body_script.component.instances.values()
                             if i.graph.index == 0x0BD4)
                ammo = rifle.vars[53]
                hp = fixture.health(run, orisa)
                torb_hp = fixture.health(run, torb)
                if fortified:
                    run.step({orisa: (0, 0, 8)})
                    self.assertIsNotNone(fortify.state_for(orisa))
                rifle_aim = fixture.body_aim(run, orisa, torb)
                flash_aim = fixture.body_aim(run, cassidy, orisa)
                run.step({orisa: (*rifle_aim, 1), cassidy: (*flash_aim, 16)})
                first, = [rocket for rocket in fight.rockets if rocket.shooter is orisa]
                self.assertEqual(ammo.value(), 149)
                for _ in range(35):
                    if hp.total < 450:
                        break
                    run.step({orisa: (*rifle_aim, 1)})
                self.assertAlmostEqual(450 - hp.total, 10 if fortified else 20, places=5)
                self.assertEqual(fight.is_stunned(orisa), not fortified)
                self.assertIn(first, fight.rockets)
                self.assertEqual(torb_hp.total, 250)
                remaining = ammo.value()
                for _ in range(14):
                    run.step({orisa: (*rifle_aim, 1)})
                self.assertNotIn(first, fight.rockets)
                if fortified:
                    self.assertLess(ammo.value(), remaining)
                    self.assertGreaterEqual(250 - torb_hp.total, 6)
                    self.assertFalse(fight.is_stunned(orisa))
                else:
                    self.assertEqual(ammo.value(), remaining)
                    self.assertEqual(250 - torb_hp.total, (150 - remaining) * 6)
                    for _ in range(110):
                        if not fight.is_stunned(orisa):
                            break
                        run.step({})
                    self.assertFalse(fight.is_stunned(orisa))
                    before = torb_hp.total
                    run.step({orisa: (*rifle_aim, 1)})
                    self.assertEqual(ammo.value(), remaining - 1)
                    fixture.advance(run, torb, orisa, 25)
                    self.assertEqual(before - torb_hp.total, 6)


    def test_fusion_driver_deflect_and_grasp_protect_overload_then_release_lane(self):
        for name in ("Genji", "Sigma"):
            with self.subTest(protector=name):
                fixture, run, torb, orisa, protector = self.arena()
                match = run.match
                fight = match.combat
                match.game_map = replace(match.game_map, free_for_all=False)
                hero = next(hero for hero in heroes().values() if hero.name == name)
                self.assertTrue(match.switch_hero(protector, hero))
                orisa.team = 1 - torb.team
                protector.team = torb.team
                protector.body_script.ult_seconds = 0
                x, y, z = torb.mover.position
                fixture.place(orisa, (x, y, z + 5))
                offset = .7 if name == "Genji" else 1.25
                fixture.place(protector, (x - offset, y, z + 1.25))
                fixture.advance(run, torb, orisa, 35)
                torb_hp = fixture.health(run, torb)
                orisa_hp = fixture.health(run, orisa)
                protector_hp = fixture.health(run, protector)
                base = dict(torb_hp.now)
                defender_health = protector_hp.total
                rifle = next(i for i in orisa.body_script.component.instances.values()
                             if i.graph.index == 0x0BD4)
                ammo = rifle.vars[53]
                charge = orisa.body_script.component.entity_var(342)
                run.step({torb: (0, 0, 16), orisa: (0, 0, 8)})
                self.assertIsNotNone(fortify.state_for(orisa))
                grant = torb_hp.over_armour.source
                back = fixture.body_aim(run, protector, orisa)
                fixture.cast(run, torb, protector, defender_aim=back)
                direction = fixture.body_aim(run, orisa, torb)
                with patch.object(fight, "_landed", wraps=fight._landed) as landed:
                    run.step({orisa: (*direction, 1), protector: (*back, 0)})
                    rocket, = fight.rockets
                    for _ in range(20):
                        run.step({protector: (*back, 0)})
                    landed.assert_any_call(rocket, intercepted=True)
                self.assertEqual(ammo.value(), 149)
                self.assertEqual(charge.value(), 0)
                self.assertEqual(orisa.stats.value(stats.HERO_DAMAGE), 0)
                self.assertEqual(torb_hp.over_armour.amount, 100)
                self.assertEqual(protector_hp.total, defender_health)
                if name == "Genji":
                    self.assertAlmostEqual(orisa_hp.total, 450 - 3.3, places=5)
                    self.assertAlmostEqual(protector.stats.value(stats.HERO_DAMAGE), 3.3, places=5)
                    reflected_charge = protector.body_script.component.entity_var(342).value()
                    self.assertAlmostEqual(reflected_charge, 3.3, places=5)
                else:
                    grasp = next(i for i in protector.body_script.component.instances.values()
                                 if i.graph.index == sigma_grasp.GRASP)
                    self.assertEqual(grasp.vars[21312].value(), 11)
                    self.assertEqual(orisa_hp.total, 450)
                    self.assertEqual(protector.stats.value(stats.HERO_DAMAGE), 0)

                fixture.place(protector, (x + 10, y, z))
                run.step({orisa: (*direction, 1)})
                fixture.advance(run, torb, orisa, 20)
                self.assertEqual(ammo.value(), 148)
                self.assertEqual(torb_hp.over_armour.amount, 94)
                self.assertEqual(torb_hp.over_armour.source, grant)
                self.assertEqual(torb_hp.now, base)
                self.assertEqual(charge.value(), 6)
                self.assertEqual(orisa.stats.value(stats.HERO_DAMAGE), 6)
                self.assertIsNotNone(fortify.state_for(orisa))
                self.assert_rivet_damage(fixture, run, torb, orisa, orisa_hp, 37)
                if name == "Sigma":
                    for _ in range(150):
                        if sigma_grasp.SHIELD_TAG in protector_hp.temporary_shields:
                            break
                        run.step({})
                    shield = protector_hp.temporary_shields[sigma_grasp.SHIELD_TAG]
                    self.assertAlmostEqual(shield.now, 6.6, places=5)

    def test_fusion_driver_breaks_projection_without_spill_then_spends_overload(self):
        fixture, run, torb, orisa, projector = self.arena()
        match = run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        orisa.team = 1 - torb.team
        zarya = next(hero for hero in heroes().values() if hero.name == "Zarya")
        self.assertTrue(match.switch_hero(projector, zarya))
        projector.team = torb.team
        x, y, z = torb.mover.position
        fixture.place(projector, (x - 5, y, z))
        fixture.advance(run, torb, orisa, 35)
        hp = fixture.health(run, torb)
        orisa_hp = fixture.health(run, orisa)
        base = dict(hp.now)
        rifle = next(i for i in orisa.body_script.component.instances.values() if i.graph.index == 0x0BD4)
        ammo = rifle.vars[53]
        charge = orisa.body_script.component.entity_var(342)
        fixture.step(run, torb, orisa, fire=16, defense=8)
        grant = hp.over_armour.source
        fixture.advance(run, torb, orisa, 45)
        run.step({projector: (*fixture.body_aim(run, projector, torb), 16)})
        bubble = fight.bubbles.for_target(torb)
        self.assertIsNotNone(bubble)
        self.assertIs(bubble.caster, projector)
        self.assertEqual(bubble.health, 200)
        aim_at_torb = fixture.body_aim(run, orisa, torb)
        held = {orisa: (*aim_at_torb, 1)}
        with patch.object(fight.bubbles, "damage", wraps=fight.bubbles.damage) as shield_hits:
            run.step({**held, torb: (*fixture.body_aim(run, torb, orisa), 1)})
            for _ in range(140):
                self.assertEqual(hp.over_armour.amount, 100)
                self.assertEqual(hp.now, base)
                self.assertEqual(charge.value(), 0)
                self.assertEqual(orisa.stats.value(stats.HERO_DAMAGE), 0)
                self.assertEqual(bubble.health, max(0, 200 - 11 * shield_hits.call_count))
                if fight.bubbles.for_target(torb) is None:
                    break
                run.step(held)
        self.assertEqual(bubble.health, 0)
        self.assertEqual(shield_hits.call_count, 19)
        self.assertTrue(all(call.args == (bubble, 11, orisa) for call in shield_hits.call_args_list))
        self.assertEqual(ammo.value(), 131)
        self.assertEqual(hp.over_armour.source, grant)
        self.assertAlmostEqual(orisa_hp.total, 413, places=5)
        self.assertAlmostEqual(torb.stats.value(stats.HERO_DAMAGE), 37, places=5)
        energy = projector.body_script.component.entity_var(87).value()
        self.assertAlmostEqual(energy, 40, places=4)

        for _ in range(25):
            if hp.over_armour.amount < 100:
                break
            run.step(held)
        self.assertEqual(ammo.value(), 130)
        self.assertEqual(hp.over_armour.source, grant)
        self.assertEqual(hp.over_armour.amount, 94)
        self.assertEqual(hp.now, base)
        self.assertEqual(charge.value(), 6)
        self.assertEqual(orisa.stats.value(stats.HERO_DAMAGE), 6)

    def test_fusion_driver_spends_ammo_without_damage_on_miss_team_or_cover(self):
        for obstacle in ("miss", "team", "cover"):
            with self.subTest(obstacle=obstacle):
                fixture, run, torb, orisa, _ = self.arena()
                match = run.match
                match.game_map = replace(match.game_map, free_for_all=False)
                orisa.team = torb.team if obstacle == "team" else 1 - torb.team
                x, y, z = torb.mover.position
                if obstacle == "cover":
                    match.collision_world = World(
                        box(x - 25, x + 25, y - 1, y, z - 25, z + 25)
                        + wall(x - 4, z + 4, x + 4, z + 4, y0=y, y1=y + 4)
                    )
                hp = fixture.health(run, torb)
                rifle = next(i for i in orisa.body_script.component.instances.values()
                             if i.graph.index == 0x0BD4)
                direction = (16384, 0) if obstacle == "miss" else fixture.body_aim(run, orisa, torb)
                run.step({torb: (0, 0, 16), orisa: (*direction, 1)})
                fixture.advance(run, torb, orisa, 40)
                self.assertEqual(rifle.vars[53].value(), 149)
                self.assertEqual(hp.total, 350)
                self.assertEqual(orisa.stats.value(stats.HERO_DAMAGE), 0)
                self.assertEqual(orisa.body_script.component.entity_var(342).value(), 0)

    def test_fusion_driver_checks_terminal_travel_against_overload_capsule(self):
        for within_travel in (True, False):
            with self.subTest(within_travel=within_travel):
                fixture, run, torb, orisa, other = self.arena()
                match = run.match
                fight = match.combat
                match.game_map = replace(match.game_map, free_for_all=False)
                orisa.team = other.team = 1 - torb.team
                x, y, z = torb.mover.position
                match.collision_world = World(box(x - 50, x + 50, y - 1, y, z - 50, z + 2450))
                fixture.place(orisa, (x, y, z))
                fixture.place(other, (x + 40, y, z + 40))
                target = next(target for target in fight._targets() if target.owner is torb)
                contact_margin = -.25 if within_travel else .25
                fixture.place(torb, (x, y, z + 2400 + target.radius + contact_margin))
                fixture.advance(run, torb, orisa, 3)
                hp = fixture.health(run, torb)
                base = dict(hp.now)
                rifle = next(i for i in orisa.body_script.component.instances.values()
                             if i.graph.index == 0x0BD4)
                direction = fixture.body_aim(run, orisa, torb)
                run.step({orisa: (*direction, 1)})
                rocket, = fight.rockets
                launch_tick = run.frame
                self.assertIs(rocket.shooter, orisa)
                self.assertEqual((rocket.speed, rocket.left, rocket.gravity, rocket.direct.amount),
                                 (120, 20, 0, 11))
                self.assertEqual(rifle.vars[53].value(), 149)
                target = next(target for target in fight._targets() if target.owner is torb)
                lower, upper = target.segment()
                distance = combat.ray_capsule(rocket.position, rocket.direction, lower, upper, target.radius)
                self.assertIsNotNone(distance)
                self.assertAlmostEqual(distance, 2400 + contact_margin, delta=.05)
                with patch.object(fight, "_expire_rocket", wraps=fight._expire_rocket) as expire:
                    for _ in range(1255):
                        if rocket.left <= .5 and hp.over_armour.source is None:
                            run.step({torb: (0, 0, 16)})
                            self.assertEqual(hp.over_armour.amount, 100)
                        else:
                            run.step({})
                        if rocket not in fight.rockets:
                            break
                    self.assertNotIn(rocket, fight.rockets)
                    self.assertEqual(run.frame - launch_tick, 1250)
                    if within_travel:
                        expire.assert_not_called()
                        self.assertAlmostEqual(rocket.distance, distance, delta=.05)
                    else:
                        expire.assert_called_once_with(rocket)
                        self.assertAlmostEqual(rocket.distance, 2400, places=5)
                damage = 6 if within_travel else 0
                self.assertEqual(hp.over_armour.amount, 100 - damage)
                self.assertEqual(hp.now, base)
                self.assertEqual(orisa.stats.value(stats.HERO_DAMAGE), damage)
                self.assertEqual(orisa.body_script.component.entity_var(342).value(), damage)
                self.assertEqual(rifle.vars[53].value(), 149)

    def test_fusion_driver_reload_denies_fire_then_hits_overload_after_counter_rivet(self):
        for automatic in (False, True):
            with self.subTest(automatic=automatic):
                fixture, run, torb, orisa, _ = self.arena()
                fight = run.match.combat
                rifle = next(i for i in orisa.body_script.component.instances.values()
                             if i.graph.index == 0x0BD4)
                ammo = rifle.vars[53]
                reload = rifle.state(19)
                self.assertEqual(ammo.value(), 150)
                self.assertEqual(reload.counter, 0)
                hp = fixture.health(run, torb)
                orisa_hp = fixture.health(run, orisa)
                base = dict(hp.now)
                miss = (16384, 0, 1)
                with patch.object(fight, "_shoot", wraps=fight._shoot) as shoot:
                    if automatic:
                        for _ in range(1050):
                            if ammo.value() == 0 and reload.counter:
                                break
                            run.step({orisa: miss})
                    else:
                        run.step({orisa: miss})
                        fixture.advance(run, torb, orisa, 20)
                        run.step({orisa: (0, 0, 0x400)})
                    spent = 150 if automatic else 1
                    self.assertEqual(ammo.value(), 150 - spent)
                    self.assertTrue(reload.active)
                    self.assertEqual(reload.counter, 1)
                    self.assertEqual(sum(call.args[0] is orisa for call in shoot.call_args_list), spent)
                    self.assertEqual(orisa.stats.value(stats.HERO_DAMAGE), 0)
                    direction = fixture.body_aim(run, orisa, torb)
                    held = {orisa: (*direction, 1)}
                    run.step({**held, torb: (0, 0, 16)})
                    self.assertEqual(hp.over_armour.amount, 100)
                    for _ in range(45):
                        run.step(held)
                        self.assertEqual(ammo.value(), 150 - spent)
                    run.step({**held, torb: (*fixture.body_aim(run, torb, orisa), 1)})
                    for _ in range(25):
                        if orisa_hp.total < 450:
                            break
                        run.step(held)
                    self.assertEqual(orisa_hp.total, 385)
                    self.assertEqual(torb.stats.value(stats.HERO_DAMAGE), 65)
                    self.assertTrue(reload.active)
                    self.assertEqual(hp.total, 350)
                    self.assertEqual(sum(call.args[0] is orisa for call in shoot.call_args_list), spent)

                    for _ in range(210):
                        if ammo.value() == 150:
                            break
                        run.step(held)
                    self.assertEqual(ammo.value(), 150)
                    self.assertEqual(hp.total, 350)
                    self.assertEqual(sum(call.args[0] is orisa for call in shoot.call_args_list), spent)
                    for _ in range(70):
                        if ammo.value() < 150:
                            break
                        run.step(held)
                    self.assertEqual(ammo.value(), 149)
                    self.assertFalse(reload.active)
                    self.assertEqual(sum(call.args[0] is orisa for call in shoot.call_args_list), spent + 1)
                    fixture.advance(run, torb, orisa, 20)
                    self.assertEqual(ammo.value(), 149)
                    self.assertEqual(sum(call.args[0] is orisa for call in shoot.call_args_list), spent + 1)
                    self.assertEqual(hp.over_armour.amount, 94)
                    self.assertEqual(hp.now, base)
                    self.assertEqual(orisa.stats.value(stats.HERO_DAMAGE), 6)
                    self.assertEqual(orisa.body_script.component.entity_var(342).value(), 6)

    def test_fortify_mitigates_secondary_melee_hammer_and_turret_charge(self):
        for attack, button, damage in (("secondary", 2, 37.5), ("melee", 0x800, 13),
                                       ("hammer", 1, 28), ("turret", 0, 4.2)):
            with self.subTest(attack=attack):
                fixture, run, torb, orisa, _ = self.arena()
                fight = run.match.combat
                x, y, z = torb.mover.position
                if attack == "turret":
                    fixture.place(orisa, (x, y, z + 100))
                    run.step({torb: (0, 4000, 8)})
                    fixture.advance(run, torb, orisa, 225)
                    turret = fight.turrets[torb.entity]
                    tx, ty, tz = turret.actor.position
                    run.step({orisa: (0, 0, 8)})
                    fixture.place(orisa, (tx, ty, tz + 5))
                    self.assertEqual(turret.shots, 0)
                else:
                    fixture.place(orisa, (x, y, z + 1.5))
                if attack == "hammer":
                    run.frame += 1
                    torb.take_commands([Command(run.frame, action=2)])
                    fixture.advance(run, torb, orisa, 45)
                hp = fixture.health(run, orisa)
                before = hp.total
                charge = torb.body_script.component.entity_var(342)
                self.assertEqual(charge.value(), 0)
                if button:
                    run.step({orisa: (0, 0, 8)})
                    fixture.step(run, torb, orisa, fire=button)
                for _ in range(45):
                    if hp.total < before:
                        break
                    run.step({})
                self.assertAlmostEqual(before - hp.total, damage, places=5)
                self.assertAlmostEqual(torb.stats.value(stats.HERO_DAMAGE), damage, places=5)
                self.assertAlmostEqual(charge.value(), damage, places=5)
                if attack == "turret":
                    self.assertEqual(turret.shots, 1)

    def test_airborne_attacks_use_fortify_at_each_impact_across_expiry(self):
        expiry = 249
        cases = (
            ("rivet", 1, ((7, 1, 37, 65),)),
            ("secondary", 2, ((4, 9, 3.75, 7.5), (5, 1, 3.75, 7.5))),
            ("globule", 1, ((12, 1, 10, 20),)),
        )
        for weapon, button, contacts in cases:
            for offset in (-1, 0, 1):
                with self.subTest(weapon=weapon, first_impact_offset=offset):
                    fixture, run, torb, orisa, _ = self.arena()
                    fight = run.match.combat
                    hp = fixture.health(run, orisa)
                    base_health = hp.now[combat.HEALTH_POOL]
                    base_armor = hp.now[combat.ARMOUR_POOL]
                    torb.body_script.ult_seconds = 0
                    torb.body_script.set_ult_charge(0)
                    charge = torb.body_script.component.entity_var(342)
                    run.step({orisa: (0, 0, 8)})
                    self.assertIsNotNone(fortify.state_for(orisa))

                    launch = expiry + offset - contacts[0][0]
                    if weapon == "globule":
                        fixture.advance(run, torb, orisa, launch - 47)
                        torb.body_script.set_ult_charge(10000)
                        fixture.step(run, torb, orisa, fire=32)
                        fixture.advance(run, torb, orisa, 45)
                    else:
                        fixture.advance(run, torb, orisa, launch - 1)
                    fixture.step(run, torb, orisa, fire=button)
                    volley = set(fight.rockets)
                    self.assertEqual(len(volley), sum(count for _, count, _, _ in contacts))
                    self.assertTrue(all(shot.shooter is torb for shot in volley))
                    self.assertTrue(all(shot.distance == 0 for shot in volley))

                    damage = 0
                    remaining = len(volley)
                    for frame in range(launch + 1, expiry + 4):
                        fixture.step(run, torb, orisa)
                        for travel, count, mitigated, exposed in contacts:
                            if frame == launch + travel:
                                damage += count * (mitigated if frame <= expiry else exposed)
                                remaining -= count
                        self.assertEqual(fortify.state_for(orisa) is not None, frame < expiry)
                        self.assertEqual(len(fight.rockets), remaining)
                        self.assertTrue(all(shot in volley for shot in fight.rockets))
                        self.assertAlmostEqual(hp.now[combat.ARMOUR_POOL], base_armor - damage, places=5)
                        self.assertEqual(hp.now[combat.HEALTH_POOL], base_health)
                        self.assertAlmostEqual(torb.stats.value(stats.HERO_DAMAGE), damage, places=5)
                        self.assertAlmostEqual(charge.value(), 0 if weapon == "globule" else damage, places=5)
                    self.assertFalse(fight.rockets or fight.molten_pools or fight.molten_burns)
                    self.assertEqual((torb.entity, orisa.body) in fight.torbjorn_marks, weapon == "rivet")

    def test_fortified_orisa_trades_turret_fire_through_hammer_repair_and_destruction(self):
        fixture, run, torb, orisa, _ = self.arena()
        fight = run.match.combat
        x, y, z = torb.mover.position
        fixture.place(orisa, (x, y, z + 100))
        run.step({torb: (0, 4000, 8)})
        fixture.advance(run, torb, orisa, 225)
        turret = fight.turrets[torb.entity]
        hp = fight.vitals[turret.actor.entity]
        orisa_hp = fixture.health(run, orisa)
        owner_hp = fixture.health(run, torb)
        self.assertEqual(hp.total, 250)
        self.assertEqual(turret.shots, 0)
        tx, ty, tz = turret.actor.position
        fixture.place(torb, (tx - 1.25, ty, tz))
        run.frame += 1
        torb.take_commands([Command(run.frame, action=2)])
        fixture.advance(run, torb, orisa, 45)
        run.step({orisa: (0, 0, 8)})
        fixture.place(orisa, (tx, ty, tz + 5))
        rifle_aim = fixture.body_aim(run, orisa, turret.actor)
        for _ in range(65):
            run.step({orisa: (*rifle_aim, 1)})
            if hp.total <= 195:
                break
        self.assertIsNotNone(fortify.state_for(orisa))
        self.assertLessEqual(hp.total, 195)
        self.assertAlmostEqual(450 - orisa_hp.total, turret.shots * 4.2, places=4)
        self.assertGreater(turret.shots, 0)
        fixture.advance(run, torb, orisa, 20)

        before_repair = hp.total
        repair_aim = aim(fight._eye(torb), (tx, ty + .5, tz))
        run.step({torb: (*repair_aim, 1)})
        for _ in range(45):
            if hp.total > before_repair:
                break
            run.step({})
        self.assertIsNotNone(fortify.state_for(orisa))
        self.assertEqual(hp.total - before_repair, 50)
        self.assertIs(fight.turrets[torb.entity], turret)
        self.assertAlmostEqual(orisa.stats.value(stats.HERO_DAMAGE), 250 - before_repair, places=5)
        self.assertEqual(owner_hp.total, 250)

        for _ in range(275):
            if fortify.state_for(orisa) is None:
                break
            run.step({})
        self.assertIsNone(fortify.state_for(orisa))
        before_shot = orisa_hp.total
        for _ in range(30):
            run.step({})
            if orisa_hp.total < before_shot:
                break
        self.assertAlmostEqual(before_shot - orisa_hp.total, 9, places=5)

        for _ in range(240):
            run.step({orisa: (*rifle_aim, 1)})
            if hp.dead:
                break
        self.assertTrue(hp.dead)
        self.assertEqual(hp.killer, orisa.body)
        self.assertNotIn(torb.entity, fight.turrets)
        self.assertAlmostEqual(orisa.stats.value(stats.HERO_DAMAGE), 300, places=5)
        self.assertAlmostEqual(torb.stats.value(stats.HERO_DAMAGE), 450 - orisa_hp.total, places=4)
        charge = torb.body_script.component.entity_var(342)
        self.assertAlmostEqual(charge.value(), 450 - orisa_hp.total, places=3)
        ability = next(i for i in torb.body_script.component.instances.values()
                       if i.graph.index == 0x16C5).state(13)
        self.assertGreater(ability.remaining(), 9.9)
        shots = turret.shots
        run.step({torb: (0, 4000, 8)})
        fixture.advance(run, torb, orisa, 35)
        self.assertNotIn(torb.entity, fight.turrets)
        self.assertEqual(turret.shots, shots)


    def test_fortified_molten_crosses_armor_in_two_health_events_without_charge(self):
        fixture, run, torb, orisa, _ = self.arena()
        fight = run.match.combat
        x, y, z = torb.mover.position
        fixture.place(orisa, (x, y, z + 100))
        torb.body_script.set_ult_charge(torb.body_script.component.entity_var(338).value())
        run.step({torb: (0, 4000, 32)})
        fixture.advance(run, torb, orisa, 45)
        run.step({torb: (0, 4000, 1)})
        for _ in range(100):
            if any(pool.phase == 1 for pool in fight.molten_pools.values()):
                break
            run.step({})
        pool, = fight.molten_pools.values()
        self.assertIs(pool.owner, torb)
        self.assertEqual(pool.phase, 1)
        hp = fixture.health(run, orisa)
        hp.damage(hp.now[combat.ARMOUR_POOL] - .9, armour_reduction=False)
        fight._sync_body_health(orisa, hp)
        before = hp.total
        run.step({orisa: (0, 0, 8)})
        fixture.place(orisa, pool.position)
        hits = len(torb.client.messages(combat.HIT))
        run.step({})
        self.assertAlmostEqual(hp.now[combat.ARMOUR_POOL], 0)
        self.assertAlmostEqual(before - hp.total, 1.86, places=5)
        self.assertAlmostEqual(torb.stats.value(stats.HERO_DAMAGE), 1.86, places=5)
        self.assertEqual(torb.body_script.component.entity_var(342).value(), 0)
        self.assertEqual(len(torb.client.messages(combat.HIT)) - hits, 2)

    def test_landed_molten_keeps_same_burn_across_fortify_expiry(self):
        for split_armor in (False, True):
            with self.subTest(split_armor=split_armor):
                fixture, run, torb, orisa, _ = self.arena()
                fight = run.match.combat
                x, y, z = torb.mover.position
                fixture.place(orisa, (x, y, z + 100))
                torb.body_script.ult_seconds = 0
                torb.body_script.set_ult_charge(torb.body_script.component.entity_var(338).value())
                run.step({torb: (0, 4000, 32)})
                fixture.advance(run, torb, orisa, 45)
                run.step({torb: (0, 4000, 1)})
                for _ in range(100):
                    if any(pool.phase == 1 for pool in fight.molten_pools.values()):
                        break
                    run.step({})
                pool, = fight.molten_pools.values()
                self.assertIs(pool.owner, torb)
                self.assertEqual(pool.phase, 1)
                hp = fixture.health(run, orisa)
                run.step({orisa: (0, 0, 8)})
                cast = run.frame
                self.assertIsNotNone(fortify.state_for(orisa))
                fixture.advance(run, torb, orisa, 246)
                self.assertEqual(torb.stats.value(stats.HERO_DAMAGE), 0)
                fixture.place(orisa, pool.position)
                run.step({})
                key = torb.entity, orisa.body
                burn = fight.molten_burns[key]
                before = hp.total
                run.step({})
                self.assertEqual(run.frame, cast + 248)
                self.assertIsNotNone(fortify.state_for(orisa))
                self.assertAlmostEqual(before - hp.total, 2.4, places=5)
                if split_armor:
                    hp.damage(hp.now[combat.ARMOUR_POOL] - 3.4, armour_reduction=False)
                    fight._sync_body_health(orisa, hp)
                armor = hp.now[combat.ARMOUR_POOL]
                health = hp.now[combat.HEALTH_POOL]
                credit = torb.stats.value(stats.HERO_DAMAGE)
                hits = len(torb.client.messages(combat.HIT))

                final_losses = (3.4, 1.92, 3) if split_armor else (6.4, 0, 2)
                for armor_loss, health_loss, events in ((2.4, 0, 1), final_losses):
                    run.step({})
                    self.assertIsNone(fortify.state_for(orisa))
                    self.assertIs(fight.molten_burns[key], burn)
                    self.assertEqual(list(fight.molten_pools.values()), [pool])
                    self.assertAlmostEqual(hp.now[combat.ARMOUR_POOL], armor - armor_loss, places=5)
                    self.assertAlmostEqual(hp.now[combat.HEALTH_POOL], health - health_loss, places=5)
                    self.assertAlmostEqual(torb.stats.value(stats.HERO_DAMAGE) - credit,
                                           armor_loss + health_loss, places=5)
                    self.assertEqual(len(torb.client.messages(combat.HIT)) - hits, events)
                    self.assertEqual(torb.body_script.component.entity_var(342).value(), 0)

    def test_fortify_rejects_graviton_pull_but_reduced_damage_continues(self):
        for mid_pull in (False, True):
            with self.subTest(mid_pull=mid_pull):
                fixture, run, torb, orisa, zarya = self.arena()
                match = run.match
                fight = match.combat
                self.assertTrue(match.switch_hero(
                    zarya, next(hero for hero in heroes().values() if hero.name == "Zarya")))
                match.game_map = replace(match.game_map, free_for_all=False)
                zarya.team = torb.team
                orisa.team = 1 - zarya.team
                x, y, z = orisa.mover.position
                fixture.place(zarya, (x, y, z + 5))
                fixture.place(torb, (x + 30, y, z))
                fixture.advance(run, torb, orisa, 35)
                hp = fixture.health(run, orisa)
                instance = next(i for i in orisa.body_script.component.instances.values()
                                if i.graph.index == 0x0CF4)
                if not mid_pull:
                    run.step({orisa: (0, 0, 8)})
                    fixture.advance(run, torb, orisa, 220)
                    self.assertTrue(instance.state(4).active)
                zarya.body_script.set_ult_charge(zarya.body_script.component.entity_var(338).value())
                run.step({zarya: (*fixture.body_aim(run, zarya, orisa), 32)})
                for _ in range(45):
                    if fight.gravitons.fields:
                        break
                    run.step({})
                field, = fight.gravitons.fields
                self.assertAlmostEqual(zarya.stats.value(stats.HERO_DAMAGE), 2.5 if mid_pull else 1.5)
                if mid_pull:
                    run.step({})
                    self.assertIn(orisa.body, fight.gravitons.pulled)
                    run.step({orisa: (0, 0, 8)})
                    self.assertNotIn(orisa.body, fight.gravitons.pulled)
                    self.assertEqual(orisa.mover.velocity, (0, 0, 0))
                self.assertTrue(instance.state(4).active)
                pose = tuple(orisa.mover.position)
                before = hp.total
                credit = zarya.stats.value(stats.HERO_DAMAGE)
                for _ in range(8):
                    run.step({})
                    self.assertNotIn(orisa.body, fight.gravitons.pulled)
                    self.assertEqual(tuple(orisa.mover.position), pose)
                self.assertAlmostEqual(before - hp.total, 8 * .048)
                self.assertAlmostEqual(zarya.stats.value(stats.HERO_DAMAGE) - credit, 8 * .048)
                if not mid_pull:
                    while instance.state(4).active and run.frame < field.end_tick:
                        run.step({})
                    self.assertLess(run.frame, field.end_tick)
                    before = hp.total
                    run.step({})
                    self.assertIn(orisa.body, fight.gravitons.pulled)
                    self.assertAlmostEqual(before - hp.total, .08)

    def test_actual_throw_pool_and_primary_secondary_contacts(self):
        fixture, run, torb, orisa, _ = self.arena()
        shield = self.deploy(run, orisa)
        self.assertEqual(expr.to_float(shield.component.entity_var(567).value()), 20)
        hp = fixture.health(run, orisa)
        before = hp.total
        for button, capacity in ((1, 530), (2, 405)):
            fixture.step(run, torb, orisa, fire=button)
            fixture.advance(run, torb, orisa, 44)
            self.assertEqual(shield.health, capacity)
            self.assertEqual(hp.total, before)
        self.assertEqual(torb.stats.value(stats.HERO_DAMAGE), 0)
        self.assertEqual(torb.body_script.component.entity_var(342).value(), 0)

    def test_projectile_contacts_on_orisa_barrier_landing_frame(self):
        cases = (
            ("rivet", 1, 1, 8, 70, 65),
            ("secondary", 2, 10, 10, 125, 75),
            ("globule", 1, 1, 3, 25, 20),
        )
        landing_frame = 14
        for weapon, button, count, fire_on_landing_path, shield_damage, armored_damage in cases:
            for lands_in_path in (False, True):
                with self.subTest(weapon=weapon, lands_in_path=lands_in_path):
                    fixture, run, torb, orisa, _ = self.arena()
                    fight = run.match.combat
                    service = fight.orisa_barriers
                    if weapon == "globule":
                        torb.body_script.set_ult_charge(10000)
                        fixture.step(run, torb, orisa, fire=32)
                        fixture.advance(run, torb, orisa, 45)
                    hp = fixture.health(run, orisa)
                    before = hp.total
                    charge = torb.body_script.component.entity_var(342)
                    fire_frame = fire_on_landing_path if lands_in_path else fire_on_landing_path - 1
                    volley = ()
                    for frame in range(landing_frame + 1):
                        fire = button if frame == fire_frame else 0
                        run.step({orisa: (-32768, 8192, 16 if frame == 0 else 0),
                                  torb: (*fixture.body_aim(run, torb, orisa), fire)})
                        self.assertEqual(orisa.body in service.flights, 8 <= frame < landing_frame)
                        self.assertEqual(orisa.body in service.actors, frame == landing_frame)
                        if frame == fire_frame:
                            volley = tuple(fight.rockets)
                            self.assertEqual(len(volley), count)
                            self.assertTrue(all(shot.shooter is torb for shot in volley))
                            self.assertTrue(all(shot.distance == 0 for shot in volley))
                        if frame == landing_frame - 1:
                            survivors = count if lands_in_path else 1
                            self.assertEqual(len(fight.rockets), survivors)
                            self.assertTrue(all(shot in volley for shot in fight.rockets))
                            early = armored_damage - 7.5 if weapon == "secondary" and not lands_in_path else 0
                            self.assertEqual(hp.total, before - early)

                    shield = service.actors[orisa.body]
                    self.assertTrue(shield.active())
                    self.assertEqual(shield.health, 600 - shield_damage if lands_in_path else 600)
                    direct = 0 if lands_in_path else armored_damage
                    self.assertEqual(hp.total, before - direct)
                    self.assertEqual(torb.stats.value(stats.HERO_DAMAGE), direct)
                    self.assertEqual(charge.value(), 0 if weapon == "globule" else direct)
                    self.assertEqual((torb.entity, orisa.body) in fight.torbjorn_marks,
                                     weapon == "rivet" and not lands_in_path)
                    self.assertFalse(fight.rockets or fight.molten_pools or fight.molten_burns)

    def test_airborne_shots_cross_orisa_barrier_at_natural_expiry(self):
        cases = (("rivet", 1, 1, 6, 70, 65), ("secondary", 2, 10, 4, 125, 75), ("globule", 1, 1, 11, 25, 20))
        for weapon, button, count, travel, shield_damage, body_damage in cases:
            for expires in (False, True):
                with self.subTest(weapon=weapon, expires=expires):
                    fixture, run, torb, orisa, _ = self.arena()
                    fight = run.match.combat
                    actors = fight.orisa_barriers.actors
                    shield = self.deploy(run, orisa)
                    expiry = run.frame + 1249
                    crossing = expiry if expires else expiry - 1
                    launch = crossing - travel
                    if weapon == "globule":
                        while run.frame < launch - 47:
                            run.step({})
                        torb.body_script.set_ult_charge(10000)
                        fixture.step(run, torb, orisa, fire=32)
                        fixture.advance(run, torb, orisa, 45)
                    while run.frame < launch - 1:
                        run.step({})
                    hp = fixture.health(run, orisa)
                    before = hp.total
                    charge = torb.body_script.component.entity_var(342)
                    fixture.step(run, torb, orisa, fire=button)
                    volley = tuple(fight.rockets)
                    self.assertEqual(len(volley), count)
                    self.assertTrue(all(shot.shooter is torb and shot.distance == 0 for shot in volley))
                    fixture.advance(run, torb, orisa, travel - 1)
                    self.assertEqual(run.frame, crossing - 1)
                    self.assertTrue(shield.active())
                    self.assertEqual(shield.health, 600)
                    self.assertEqual(tuple(fight.rockets), volley)

                    fixture.step(run, torb, orisa)
                    self.assertEqual(shield.active(), not expires)
                    self.assertEqual(shield.health, 600 if expires else 600 - shield_damage)
                    early_damage = 67.5 if weapon == "secondary" and expires else 0
                    self.assertEqual(hp.total, before - early_damage)
                    if early_damage:
                        survivor, = fight.rockets
                        self.assertIn(survivor, volley)
                    else:
                        self.assertEqual(tuple(fight.rockets), volley if expires else ())
                    if not expires:
                        fixture.step(run, torb, orisa)
                    self.assertEqual(run.frame, expiry)
                    self.assertIs(actors[orisa.body], shield)
                    self.assertFalse(shield.active() or shield.retired)

                    fixture.advance(run, torb, orisa, 15)
                    direct = body_damage if expires else 0
                    self.assertEqual(hp.total, before - direct)
                    self.assertEqual(torb.stats.value(stats.HERO_DAMAGE), direct)
                    self.assertEqual(charge.value(), 0 if weapon == "globule" else direct)
                    self.assertEqual((torb.entity, orisa.body) in fight.torbjorn_marks,
                                     weapon == "rivet" and expires)
                    self.assertFalse(fight.rockets or fight.molten_pools or fight.molten_burns)
                    fixture.advance(run, torb, orisa, 36)
                    self.assertNotIn(orisa.body, actors)
                    self.assertTrue(shield.retired)

    def test_airborne_shots_settle_on_one_orisa_barrier_during_replacement(self):
        cases = (("rivet", 1, 1, 6, 70), ("secondary", 2, 10, 4, 125), ("globule", 1, 1, 11, 25))
        for weapon, button, count, travel, damage in cases:
            for hits_new in (False, True):
                with self.subTest(weapon=weapon, hits_new=hits_new):
                    fixture, run, torb, orisa, _ = self.arena()
                    fight = run.match.combat
                    service = fight.orisa_barriers
                    first = self.deploy(run, orisa)
                    source = next(i for i in orisa.body_script.component.instances.values()
                                  if i.graph.index == 0xBD7)
                    for _ in range(1000):
                        if source.states[3].remaining() <= 0:
                            break
                        run.step({})
                    self.assertEqual(source.states[3].remaining(), 0)
                    if weapon == "globule":
                        torb.body_script.set_ult_charge(10000)
                        fixture.step(run, torb, orisa, fire=32)
                        fixture.advance(run, torb, orisa, 45)
                    hp = fixture.health(run, orisa)
                    before = hp.total

                    landing = 14
                    contact = landing if hits_new else landing - 1
                    launch = contact - travel
                    volley = ()
                    for frame in range(landing + 1):
                        fire = button if frame == launch else 0
                        run.step({orisa: (-32768, 8192, 16 if frame == 0 else 0),
                                  torb: (*fixture.body_aim(run, torb, orisa), fire)})
                        self.assertEqual(orisa.body in service.flights, 8 <= frame < landing)
                        if frame == launch:
                            volley = tuple(fight.rockets)
                            self.assertEqual(len(volley), count)
                            self.assertTrue(all(shot.shooter is torb for shot in volley))
                            self.assertTrue(all(shot.distance == 0 for shot in volley))
                        if frame == contact - 1:
                            self.assertEqual(tuple(fight.rockets), volley)
                            self.assertEqual(first.health, 600)
                        if frame < landing:
                            self.assertIs(service.actors[orisa.body], first)
                            self.assertTrue(first.active())
                        if frame == contact:
                            self.assertFalse(fight.rockets)

                    new = service.actors[orisa.body]
                    self.assertNotEqual(new.body, first.body)
                    self.assertEqual(new.position, first.position)
                    self.assertTrue(first.retired)
                    self.assertFalse(first.component.instances)
                    self.assertTrue(new.active())
                    self.assertEqual(first.health, 600 if hits_new else 600 - damage)
                    self.assertEqual(new.health, 600 - damage if hits_new else 600)
                    self.assertEqual(hp.total, before)
                    self.assertEqual(torb.stats.value(stats.HERO_DAMAGE), 0)
                    self.assertEqual(torb.body_script.component.entity_var(342).value(), 0)
                    self.assertNotIn((torb.entity, orisa.body), fight.torbjorn_marks)
                    self.assertFalse(fight.molten_pools or fight.molten_burns)

    def test_breaking_rivet_spends_hit_and_next_reaches_orisa(self):
        fixture, run, torb, orisa, _ = self.arena()
        shield = self.deploy(run, orisa)
        hp = fixture.health(run, orisa)
        before = hp.total
        for shot in range(9):
            fixture.step(run, torb, orisa, fire=1)
            fixture.advance(run, torb, orisa, 40)
            self.assertEqual(shield.health, max(0, 600 - (shot + 1) * 70))
            self.assertEqual(hp.total, before)
        self.assertFalse(shield.active())
        fixture.step(run, torb, orisa, fire=1)
        fixture.advance(run, torb, orisa, 30)
        self.assertEqual(hp.total, before - 65)

    def test_friendly_fire_crosses_landed_mesh(self):
        fixture, run, torb, orisa, enemy = self.arena()
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        orisa.team = torb.team
        enemy.team = 1 - torb.team
        fixture.place(enemy, tuple(orisa.mover.position))
        x, y, z = orisa.mover.position
        fixture.place(orisa, (x, y, z + 1))
        shield = self.deploy(run, orisa)
        hp = fixture.health(run, enemy)
        before = hp.total
        fixture.step(run, torb, enemy, fire=1)
        fixture.advance(run, torb, enemy, 30)
        self.assertEqual(hp.total, before - 70)
        self.assertEqual(shield.health, 600)

    def test_native_chase_and_tail_retire_landed_mesh(self):
        _, run, _, orisa, _ = self.arena()
        shield = self.deploy(run, orisa)
        fight = run.match.combat
        start = run.frame
        fight.update((start+1200)*combat.TICK, start+1200, frames=False)
        self.assertTrue(shield.active())
        fight.update((start+1300)*combat.TICK, start+1300, frames=False)
        self.assertNotIn(orisa.body, fight.orisa_barriers.actors)

    def test_globule_contact_consumes_shot_without_pool(self):
        fixture, run, torb, orisa, _ = self.arena()
        shield = self.deploy(run, orisa)
        torb.body_script.set_ult_charge(10000)
        fixture.step(run, torb, orisa, fire=32)
        fixture.advance(run, torb, orisa, 45)
        fixture.step(run, torb, orisa, fire=1)
        fixture.advance(run, torb, orisa, 30)
        self.assertEqual(shield.health, 575)
        self.assertFalse(run.match.combat.molten_pools)

    def test_actual_melee_and_hammer_hit_landed_mesh_once(self):
        for button, damage in ((0x800, 30), (1, 55)):
            with self.subTest(button=button):
                fixture, run, torb, orisa, _ = self.arena()
                shield = self.deploy(run, orisa)
                x, y, z = shield.position
                fixture.place(torb, (x, y, z - 1.7))
                if button == 1:
                    run.frame += 1
                    torb.take_commands([Command(run.frame, action=2)])
                fixture.advance(run, torb, orisa, 45)
                fixture.step(run, torb, orisa, fire=button)
                fixture.advance(run, torb, orisa, 45)
                self.assertEqual(shield.health, 600 - damage)

    def test_allied_landed_mesh_consumes_flashbang_and_protects_torbjorn(self):
        fixture, run, torb, orisa, caster = self.arena()
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        orisa.team = torb.team
        caster.team = 1 - torb.team
        run.match.switch_hero(caster, heroes()[0x02E0000000000042])
        shield = self.deploy(run, orisa)
        x, y, z = shield.position
        fixture.place(caster, (x, y, z - 3))
        fixture.place(torb, (x, y, z + 1.5))
        fixture.advance(run, torb, orisa, 30)
        yaw, pitch = fixture.body_aim(run, caster, torb)
        run.step({caster: (yaw, pitch, 16)})
        fight = run.match.combat
        for _ in range(40):
            run.step({})
            self.assertFalse(fight.is_stunned(torb))
        self.assertEqual(shield.health, 575)
        self.assertEqual(fixture.health(run, torb).total, 250)

    def test_landed_actor_passive_component_capture(self):
        from ow174.game.body_vm_snapshot import export_component_vm, snapshot_digest

        _, run, _, orisa, _ = self.arena()
        shield = self.deploy(run, orisa)
        first = snapshot_digest(export_component_vm(shield.component))
        self.assertEqual(first, snapshot_digest(export_component_vm(shield.component)))
        run.match.combat.orisa_barriers.damage(shield, 1)
        self.assertNotEqual(first, snapshot_digest(export_component_vm(shield.component)))

    def test_early_throw_is_rejected_and_ready_throw_replaces_old_actor(self):
        _, run, _, orisa, _ = self.arena()
        first = self.deploy(run, orisa)
        service = run.match.combat.orisa_barriers
        source = next(i for i in orisa.body_script.component.instances.values() if i.graph.index == 0xBD7)
        ability = source.states[3]
        self.assertGreater(ability.remaining(), 0)
        run.step({orisa: (-32768, 8192, 16)})
        for _ in range(25):
            run.step({})
        self.assertFalse(service.flights)
        self.assertIs(service.actors[orisa.body], first)
        for _ in range(1000):
            if ability.remaining() <= 0:
                break
            run.step({})
        self.assertEqual(ability.remaining(), 0)
        run.step({orisa: (-32768, 8192, 16)})
        for _ in range(40):
            run.step({orisa: (-32768, 8192, 0)})
            if service.actors[orisa.body] is not first:
                break
        new = service.actors[orisa.body]
        self.assertNotEqual(new.body, first.body)
        self.assertFalse(first.active())
        self.assertEqual(new.health, 600)

    @unittest.skipUnless(os.environ.get("ORPHEUS_ORISA_HALT"), "needs authored Halt data")
    def test_halt_crosses_hostile_orisa_mesh_that_still_blocks_rivets(self):
        for flight_crosses in (False, True):
            with self.subTest(flight_crosses=flight_crosses):
                fixture, run, torb, caster, shield_owner = self.arena()
                match = run.match
                fight = match.combat
                self.assertTrue(match.game_map.free_for_all)
                self.assertTrue(match.switch_hero(shield_owner, caster.hero))
                x, y, z = caster.mover.position
                fixture.place(shield_owner, (x, y, z + 9))
                fixture.advance(run, torb, caster, 35)
                shield = self.deploy(run, shield_owner)
                x, y, z = shield.position
                fixture.place(shield_owner, (x + 12, y, z))
                fixture.place(caster, (x, y, z + (4 if flight_crosses else 10)))
                fixture.place(torb, (x, y, z - (6 if flight_crosses else 2.5)))
                fixture.advance(run, torb, caster, 3)
                torb_hp = fixture.health(run, torb)
                caster_hp = fixture.health(run, caster)
                start = tuple(torb.mover.position)

                launch = None
                for frame in range(26):
                    run.step({caster: (-32768, 0, 2 if frame in (0, 25) else 0)})
                    if launch is None and fight.halt.orbs:
                        launch = next(iter(fight.halt.orbs.values())).position
                orb, = fight.halt.orbs.values()
                self.assertIsNotNone(launch)
                self.assertIsNotNone(orb.requested)
                if flight_crosses:
                    ray_start, ray_end = launch, orb.position
                else:
                    target = next(item for item in fight._targets() if item.owner is torb)
                    a, b = target.segment()
                    ray_start = orb.position
                    ray_end = (a[0], max(a[1], min(b[1], ray_start[1])), a[2])
                distance = math.dist(ray_start, ray_end)
                direction = tuple((b - a) / distance for a, b in zip(ray_start, ray_end, strict=True))
                hit = fight.orisa_barriers.trace(caster, ray_start, direction, distance)
                self.assertIsNotNone(hit)
                self.assertIs(hit[1], shield)
                run.step({})
                self.assertTrue(orb.pulled)
                self.assertIn(torb.body, fight.halt.pulls)
                self.assertEqual(shield.health, 600)
                self.assertEqual(torb_hp.total, 250)
                self.assertEqual(tuple(torb.mover.position), start)

                run.step({torb: (*fixture.body_aim(run, torb, caster), 1)})
                for _ in range(15):
                    if shield.health < 600:
                        break
                    run.step({})
                self.assertEqual(shield.health, 530)
                self.assertEqual(caster_hp.total, 450)
                self.assertEqual(torb.stats.value(stats.HERO_DAMAGE), 0)
                self.assertEqual(torb.body_script.component.entity_var(342).value(), 0)
                for _ in range(90):
                    if torb.body not in fight.halt.pulls:
                        break
                    run.step({})
                self.assertFalse(fight.halt.pulls)
                self.assertGreater(torb.mover.position[2], start[2] + .5)
                self.assertEqual(torb_hp.total, 250)
                self.assertEqual(shield.health, 530)


    def test_actual_deployed_turret_debits_landed_mesh(self):
        fixture, run, torb, orisa, _ = self.arena()
        x, y, z = orisa.mover.position
        fixture.place(orisa, (x, y, z + 30))
        run.step({torb: (0, 4000, 8)})
        fixture.advance(run, torb, orisa, 100)
        turret, = run.match.combat.turrets.values()
        x, y, z = turret.actor.position
        fixture.place(orisa, (x, y, z + 8))
        shield = self.deploy(run, orisa)
        hp = fixture.health(run, orisa)
        before = hp.total
        self.assertEqual(turret.shots, 0)
        for _ in range(800):
            run.step({})
            self.assertEqual(hp.total, before)
            self.assertEqual(shield.health, max(0, 600 - turret.shots * 14))
            self.assertEqual(torb.stats.value(stats.HERO_DAMAGE), 0)
            if not shield.active():
                break
        self.assertFalse(shield.active())
        self.assertEqual(shield.health, 0)
        self.assertEqual(turret.shots, 43)
        for _ in range(30):
            if hp.total < before:
                break
            run.step({})
        self.assertEqual(turret.shots, 44)
        self.assertEqual(hp.total, before - 9)
        self.assertEqual(torb.stats.value(stats.HERO_DAMAGE), 9)
        self.assertEqual(torb.body_script.component.entity_var(342).value(), 9)
