"""Delivered Mercy health-event awards in the combined Python server."""
import unittest
from dataclasses import replace
from unittest.mock import patch
import test_baptiste_pve as pve
import test_game_mercy_staff as staff
from test_game_combat import aim
from ow174.game import combat
from ow174.game.body_vm_snapshot import export_body_vm, snapshot_digest
from ow174.game.commands import FIRE, Command
from ow174.game.content import heroes
from ow174.game.script import expr
from ow174.game.script.driver import ULT_CHARGE, ULT_COST

def equip(run, mercy):
    for _ in range(35):
        run.step({})
    run.frame += 1
    run.match.update(run.frame * combat.TICK, run.frame)
    mercy.take_commands([Command(run.frame, action=2)])
    for _ in range(35):
        run.step({})


def shot(run, mercy, feet, height, frames=25):
    x, y, z = feet
    yaw, pitch = aim(run.match.combat._eye(mercy), (x, y + height, z))
    run.step({mercy: (yaw, pitch, FIRE)})
    for _ in range(frames):
        run.step({})


class MercyDamageChargeTests(unittest.TestCase):
    def fixture(self, distance=None):
        f = staff.MercyStaffTests()
        scene = staff.SCENE
        if distance is not None:
            enemy = replace(scene.spawns[2], position=(0, 1, distance))
            scene = replace(scene, spawns=(*scene.spawns[:2], enemy))
        with patch.object(staff, "SCENE", scene):
            f.setUp()
        self.addCleanup(f.doCleanups)
        equip(f.run, f.mercy)
        target = next(t for t in f.fight._targets() if t.owner is f.enemy)
        c = f.mercy.body_script.component
        c.set(12918, 0.0)
        original = next(i for i in c.instances.values() if i.graph.index == 0x0B8B)
        return f, target, c, c.entity_var(ULT_CHARGE), original

    def test_real_body_and_critical_hits_award_actual_damage_with_authored_tags(self):
        f, target, _, charge, original = self.fixture()
        vitals = f.fight.vitals_of(target)
        before = charge.value()
        shot(f.run, f.mercy, target.feet, 0.8)
        self.assertEqual(vitals.total, 180)
        self.assertEqual(charge.value() - before, 20)
        self.assertEqual(original.find_var(0, 393).value(), expr.Entity(target.entity))
        self.assertEqual(original.find_var(0, 510).value(), tuple(
            expr.Asset(0x0D80000000000000 | tag) for tag in (0x3BF, 0x9A6, 0x1E1E, 0x2D67, 0x4D29)
        ))
        shot(f.run, f.mercy, target.feet, target.eye)
        self.assertEqual(vitals.total, 140)
        self.assertEqual(charge.value() - before, 60)


    def test_lethal_impact_awards_only_remaining_pool(self):
        f, target, _, charge, _ = self.fixture()
        vitals = f.fight.vitals_of(target)
        vitals.damage(193)  # injury setup, outside the charge-producing combat path
        before = charge.value()
        shot(f.run, f.mercy, target.feet, 0.8)
        self.assertTrue(vitals.dead)
        self.assertEqual(charge.value() - before, 7)


    def test_actual_melee_damage_uses_authored_multiplier_and_cost_cap(self):
        f, target, c, charge, _ = self.fixture(1.5)
        x, y, z = target.feet
        yaw, pitch = aim(f.fight._eye(f.mercy), (x, y + 0.8, z))
        vitals = f.fight.vitals_of(target)
        c.set(13022, 0.5)
        cost = c.entity_var(ULT_COST).value()
        f.mercy.body_script.set_ult_charge(cost - 18)
        f.run.step({f.mercy: (yaw, pitch, 0x800)})
        for _ in range(40):
            f.run.step({})
        self.assertEqual(vitals.total, 170)
        self.assertEqual(charge.value(), cost - 3)
        shot(f.run, f.mercy, target.feet, 0.8)
        self.assertEqual(charge.value(), cost)


    def test_miss_and_invulnerable_impact_grant_no_damage_award(self):
        f, target, _, charge, _ = self.fixture()
        vitals = f.fight.vitals_of(target)
        before = charge.value()
        shot(f.run, f.mercy, target.feet, target.height + 0.5)
        vitals.invulnerable_until = f.fight.now + 10
        shot(f.run, f.mercy, target.feet, target.eye)
        self.assertEqual(vitals.total, 200)
        self.assertEqual(charge.value(), before)


    def test_pending_projectile_retires_with_launch_body_without_granting_charge(self):
        f, target, _, _, _ = self.fixture()
        old_body = f.mercy.body
        x, y, z = target.feet
        yaw, pitch = aim(f.fight._eye(f.mercy), (x, y + 0.8, z))
        f.run.step({f.mercy: (yaw, pitch, FIRE)})
        rocket, = f.fight.rockets
        self.assertEqual(rocket.damage_charge[0], old_body)
        f.match.switch_hero(f.mercy, f.mercy.hero)
        self.assertNotEqual(f.mercy.body, old_body)
        self.assertNotIn(rocket, f.fight.rockets)
        c = f.mercy.body_script.component
        c.set(12918, 0.0)
        for _ in range(25):
            f.run.step({})
        self.assertEqual(f.fight.vitals_of(target).total, 200)
        self.assertEqual(c.entity_var(ULT_CHARGE).value(), 0)


    def test_late_ultimate_input_cannot_spend_projectile_charge_before_impact(self):
        f, target, component, charge, _ = self.fixture(12)
        script = f.mercy.body_script
        cost = component.entity_var(ULT_COST).value()
        script.set_ult_charge(cost - 20)
        x, y, z = target.feet
        direction = aim(f.fight._eye(f.mercy), (x, y + 0.8, z))
        f.run.step({f.mercy: (*direction, FIRE)})
        launch = f.run.frame
        self.assertTrue(f.fight.rockets)
        for _ in range(30):
            f.run.frame += 1
            f.match.update(f.run.frame * 0.016, f.run.frame)
            if charge.value() == cost:
                break
        self.assertEqual(f.fight.vitals_of(target).total, 180)
        self.assertEqual(charge.value(), cost)
        self.assertEqual(script.frame, launch)
        self.assertGreater(f.run.frame, launch + 1)
        credit_time = f.run.frame * script.frame_ms
        self.assertEqual(script.world.pending_ult_credits, ((credit_time, 20),))
        stored = snapshot_digest(export_body_vm(script))
        with patch.object(script.world, "pending_ult_credits", ((credit_time + 1, 20),)):
            self.assertNotEqual(snapshot_digest(export_body_vm(script)), stored)

        f.mercy.take_commands([Command(f.run.frame - 1, buttons=0x20)])
        self.assertEqual(charge.value(), cost)
        self.assertFalse(component.entity_var(842).value())
        f.mercy.take_commands([Command(f.run.frame, buttons=0x20)])
        self.assertEqual(charge.value(), 0)
        self.assertTrue(component.entity_var(842).value())
        self.assertFalse(hasattr(script.world, "pending_ult_credits"))


    def test_real_valkyrie_suspends_damage_awards_without_blocking_impact(self):
        f, target, c, charge, _ = self.fixture()
        f.mercy.body_script.set_ult_charge(c.entity_var(ULT_COST).value())
        f.run.step({f.mercy: (0, 0, 0x20)})
        for _ in range(35):
            f.run.step({})
        self.assertTrue(c.entity_var(842).value())
        self.assertEqual(charge.value(), 0)
        shot(f.run, f.mercy, target.feet, 0.8)
        self.assertEqual(f.fight.vitals_of(target).total, 180)
        self.assertEqual(charge.value(), 0)

    @unittest.skipUnless(pve.DATA, "prepared five-NPC inputs required")
    def test_neutral_actual_damage_awards_on_all_five_prepared_talon_bodies(self):
        pve.BaptistePveTests.setUpClass()
        self.addCleanup(pve.BaptistePveTests.tearDownClass)
        fixture = pve.BaptistePveTests()
        for definition in fixture.catalog.values():
            with self.subTest(npc=definition.name):
                run, mercy, bot, fight, vitals = fixture.make(definition, team=1, distance=8)
                run.match.switch_hero(mercy, heroes()[staff.MERCY])
                mercy.mover.position[:] = (0, 1, 0)
                mercy.mover.body = None
                equip(run, mercy)
                component = mercy.body_script.component
                component.set(12918, 0.0)
                slot = component.entity_var(ULT_CHARGE)
                before, health = slot.value(), vitals.total
                shot(run, mercy, bot.position, 0.8)
                self.assertEqual(health - vitals.total, 20)
                self.assertEqual(slot.value() - before, 20)
