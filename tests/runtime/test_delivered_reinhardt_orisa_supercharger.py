"""Actual Orisa Supercharger against Reinhardt armour and Barrier Field."""

import os
import unittest
from pathlib import Path
from unittest.mock import patch

import test_delivered_reinhardt as reinhardt
import test_delivered_reinhardt_orisa_fusion as fixture
from test_game_combat import aim

from ow174.game.script.driver import ULT_CHARGE, ULT_COST
from ow174.game import orisa_supercharger
from ow174.paths import DATA_DIR

PROFILE = Path(os.environ.get("ORPHEUS_ORISA_SUPERCHARGER", DATA_DIR / "orisa_supercharger_174.json"))


def supercharger_flight(rocket):
    return rocket.source_graph == orisa_supercharger.GRAPH and rocket.spawn == orisa_supercharger.DEFINITION


@unittest.skipUnless(PROFILE.is_file(), "authored Supercharger profile required")
class ReinhardtSuperchargerTests(unittest.TestCase):
    place = staticmethod(fixture.ReinhardtFusionTests.place)
    health = fixture.ReinhardtFusionTests.health
    advance = fixture.ReinhardtFusionTests.advance
    shield = fixture.ReinhardtFusionTests.shield
    step = fixture.ReinhardtFusionTests.step

    def deploy(self, *, allied=False):
        self.enterContext(patch.dict(os.environ, {"ORPHEUS_ORISA_SUPERCHARGER": str(PROFILE)}))
        fixture.ReinhardtFusionTests.setUp(self)
        if allied:
            self.enemy.team = self.rein.team
            self.other.team = self.rein.team + 1
        self.place(self.enemy, (6, 1, 0))
        self.place(self.rein, (-5, 1, 0))
        self.place(self.other, (-12, 1, 0))
        self.step(12, fire=0)
        script = self.enemy.body_script
        script.ult_seconds = 0
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        self.step(fire=32, yaw=0)
        manager = self.fight.orisa.superchargers
        launched = False
        for _ in range(100):
            launched |= any(supercharger_flight(rocket) for rocket in self.fight.rockets)
            self.step(fire=0, yaw=0)
            if manager.active:
                break
        self.assertTrue(launched)
        [device] = manager.active.values()
        self.assertEqual(device.health, 200)
        self.assertEqual(device.script.graph.index, 0xD00)
        self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)
        x, y, z = device.position
        self.place(self.enemy, (x + 1.5, y, z - 2))
        self.place(self.rein, (x + 1.5, y, z - 8))
        self.step(8, fire=0)
        return device

    def test_actual_aura_scales_fusion_body_and_barrier_damage(self):
        device = self.deploy()
        manager = self.fight.orisa.superchargers
        self.assertEqual(manager.scale(self.enemy), 1.5)
        self.assertEqual(manager.scale(self.rein), 1)
        status = manager.buffs[(device.entity, self.enemy.body)]
        self.assertEqual(status.graph.index, 0xD01)
        self.assertEqual(status.vars[174].value(), .5)
        self.step()
        self.step(8, fire=0)
        self.assertEqual(self.health(self.rein), 488.5)
        self.step(15, fire=0, rein=2)
        self.step(rein=2)
        self.step(8, fire=0, rein=2)
        self.assertEqual(self.health(self.rein), 488.5)
        self.assertEqual(self.shield().current, 1583.5)

    def test_allied_fire_strike_damage_and_ultimate_credit(self):
        device = self.deploy(allied=True)
        x, y, z = device.position
        self.place(self.other, (x + 1.5, y, z + 2))
        script = self.rein.body_script
        script.ult_seconds = 0
        script.set_ult_charge(0)
        self.assertEqual(self.fight.orisa.superchargers.scale(self.rein), 1.5)
        self.assertEqual(self.fight.orisa.superchargers.scale(self.other), 1)
        ally_hp = self.health(self.enemy)
        self.step(fire=0, rein=16)
        charge = script.component.entity_var(ULT_CHARGE)
        for _ in range(60):
            before = charge.value()
            self.step(fire=0)
            if self.health(self.other) < 200:
                break
        self.assertEqual(self.health(self.other), 50)
        self.assertEqual(self.health(self.enemy), ally_hp)
        self.assertEqual(charge.value() - before, 150)

    def test_two_fire_strikes_destroy_enemy_device_and_clear_aura(self):
        device = self.deploy()
        manager = self.fight.orisa.superchargers
        x, y, z = device.position
        self.place(self.rein, (x, y, z - 6))
        yaw, pitch = aim(self.fight._eye(self.rein), (x, y + 1.1, z))

        def cast():
            self.run.step({self.rein: (yaw, pitch, 16)})
            for _ in range(65):
                self.run.step({self.rein: (yaw, pitch, 0)})

        cast()
        self.assertEqual(device.health, 100)
        self.assertEqual(manager.scale(self.enemy), 1.5)
        self.step(380, fire=0)
        self.assertLess(self.fight.tick, device.until)
        cast()
        self.assertLess(self.fight.tick, device.until)
        self.assertEqual(device.health, 0)
        self.assertNotIn(device.entity, manager.active)
        self.assertNotIn((device.entity, self.enemy.body), manager.buffs)
        self.assertEqual(manager.scale(self.enemy), 1)
        self.place(self.rein, (x + 1.5, y, z - 8))
        before = self.health(self.rein)
        self.step()
        self.step(8, fire=0)
        self.assertEqual(before - self.health(self.rein), 6)

    def test_reinhardt_charge_hits_enemy_supercharger_before_the_body(self):
        device = self.deploy()
        x, y, z = device.position
        self.place(self.rein, (x, y, z - 5))
        self.place(self.other, (x, y, z + 4))
        self.advance(4, buttons=0)
        before = self.health(self.other)
        self.advance(1, buttons=8)
        for _ in range(100):
            self.advance(1, buttons=0)
            if device.health < 200:
                break
        charge = next(i for i in self.rein.body_script.component.instances.values() if i.graph.index == 0x007D)
        self.assertEqual(device.health, 25)
        self.assertEqual(charge.states[58].counter, 1)
        self.assertFalse(charge.states[3].active)
        self.assertNotIn(self.rein.body, self.fight.charge_contacts)
        self.assertEqual(self.health(self.other), before)
        self.assertFalse(reinhardt.controlled(self.fight, self.other.body))
        self.assertGreater(charge.vars[61].value(), 9)
        self.assertEqual(charge.vars[6836].value(), ())
        self.advance(20, buttons=0)
        self.assertEqual(device.health, 25)

    def test_reinhardt_charge_skips_an_allied_supercharger(self):
        device = self.deploy(allied=True)
        x, y, z = device.position
        self.place(self.rein, (x, y, z - 5))
        self.place(self.other, (x, y, z + 4))
        self.advance(4, buttons=0)
        self.advance(1, buttons=8)
        for _ in range(100):
            self.advance(1, buttons=0)
            if reinhardt.pinned(self.fight, self.other.body):
                break
        self.assertEqual(device.health, 200)
        self.assertEqual(self.fight.charges[self.rein.body].pinned, self.other.body)

    def test_reinhardt_charge_cannot_damage_a_supercharger_through_cover(self):
        device = self.deploy()
        x, y, z = device.position
        self.place(self.rein, (x, y, z - 5))
        self.cover_at(x, z - 1)
        self.advance(4, buttons=0)
        self.advance(1, buttons=8)
        self.advance(90, buttons=0)
        charge = next(i for i in self.rein.body_script.component.instances.values() if i.graph.index == 0x007D)
        self.assertTrue(charge.states[56].counter)
        self.assertEqual(charge.state(58).counter, 0)
        self.assertFalse(charge.states[3].active)
        self.assertEqual(device.health, 200)
        self.assertLess(self.rein.mover.position[2], z - 1)

    def test_earthshatter_interrupts_supercharger_before_launch(self):
        self.enterContext(patch.dict(os.environ, {"ORPHEUS_ORISA_SUPERCHARGER": str(PROFILE)}))
        fixture.ReinhardtFusionTests.setUp(self)
        self.step(12, fire=0)
        orisa = self.enemy.body_script
        orisa.ult_seconds = 0
        orisa.set_ult_charge(orisa.component.entity_var(ULT_COST).value())
        self.rein.body_script.set_ult_charge(1540)
        self.step(fire=0, rein=32)
        self.step(12, fire=0)
        self.step(fire=32)
        cast = next(i for i in orisa.component.instances.values() if i.graph.index == 0x0CF5)
        self.assertTrue(cast.state(11).active)
        self.assertEqual(orisa.component.entity_var(ULT_CHARGE).value(), 0)
        knocked_down = False
        for _ in range(75):
            self.step(fire=0)
            knocked_down |= reinhardt.controlled(self.fight, self.enemy.body)
            self.assertFalse(any(supercharger_flight(rocket) for rocket in self.fight.rockets))
        self.assertTrue(knocked_down)
        self.assertFalse(cast.state(11).active)
        self.assertFalse(self.fight.orisa.superchargers.active)
        self.assertEqual(orisa.component.entity_var(ULT_CHARGE).value(), 0)

    def test_earthshatter_after_deploy_keeps_the_device_active(self):
        device = self.deploy()
        self.rein.body_script.set_ult_charge(1540)
        self.step(fire=0, rein=32)
        for _ in range(65):
            self.step(fire=0)
            if reinhardt.controlled(self.fight, self.enemy.body):
                break
        self.assertTrue(reinhardt.controlled(self.fight, self.enemy.body))
        self.assertIs(self.fight.orisa.superchargers.active[device.entity], device)
        self.assertEqual(device.health, 200)
        self.assertEqual(self.fight.orisa.superchargers.scale(self.enemy), 1.5)

    def cover_at(self, x, z):
        wall = reinhardt.World([
            (-50, 1, -50, -50, 1, 80, 50, 1, 80),
            (-50, 1, -50, 50, 1, 80, 50, 1, -50),
            (x - 3, 1, z, x - 3, 6, z, x + 3, 6, z),
            (x - 3, 1, z, x + 3, 6, z, x + 3, 1, z),
        ])
        self.match.collision_world = wall
        self.match.collision_provider.state = reinhardt.LoadState("ready", wall)
        for player in self.run.players:
            player.mover.use(world=wall)

    def test_hammer_hits_device_once_per_swing_and_clears_boost_on_destruction(self):
        device = self.deploy()
        x, y, z = device.position
        self.place(self.rein, (x, y, z - 2.5))
        self.place(self.enemy, (x + 7, y, z - 2))
        self.advance(20)
        self.assertEqual(device.health, 200)
        charge = self.rein.body_script.component.entity_var(ULT_CHARGE)
        for frame in range(28):
            before = charge.value()
            self.advance(1)
            if device.health < 200:
                self.assertEqual(charge.value() - before, 0)
                self.advance(27 - frame)
                break
        self.assertEqual(device.health, 115)
        self.assertEqual(self.fight.orisa.superchargers.scale(self.enemy), 1.5)
        self.advance(150)
        self.assertLess(self.fight.tick, device.until)
        self.assertEqual(device.health, 0)
        self.assertNotIn(device.entity, self.fight.orisa.superchargers.active)
        self.assertEqual(self.fight.orisa.superchargers.scale(self.enemy), 1)

    def test_melee_alias_respects_device_range_world_cover_and_team(self):
        for case in ("range", "world_cover", "ally"):
            with self.subTest(case=case):
                device = self.deploy(allied=case == "ally")
                x, y, z = device.position
                self.place(self.rein, (x, y, z - (7 if case == "range" else 2.5)))
                if case == "world_cover":
                    self.cover_at(x, z - 1)
                if case == "ally":
                    self.place(self.other, (x, y, z - .5))
                self.advance(48, buttons=0x800)
                self.assertEqual(device.health, 200)
                if case == "ally":
                    self.assertEqual(self.health(self.other), 72.5)

    def test_world_cover_removes_boost_before_next_fusion_hit(self):
        device = self.deploy()
        manager = self.fight.orisa.superchargers
        self.assertEqual(manager.scale(self.enemy), 1.5)
        x, _, z = device.position
        z -= 1
        self.cover_at(x, z)
        self.step(fire=0)
        self.assertEqual(manager.scale(self.enemy), 1)
        self.step()
        self.step(8, fire=0)
        self.assertEqual(self.health(self.rein), 494)

    def test_authored_lifetime_expires_without_new_input(self):
        device = self.deploy()
        tick = device.until - 1
        self.match.update(tick * .016, tick)
        self.assertEqual(self.fight.orisa.superchargers.scale(self.enemy), 1.5)
        tick += 1
        self.match.update(tick * .016, tick)
        self.assertFalse(self.fight.orisa.superchargers.active)
        self.assertFalse(self.fight.orisa.superchargers.buffs)
        self.assertEqual(self.fight.orisa.superchargers.scale(self.enemy), 1)
