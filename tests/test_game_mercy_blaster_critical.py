"""Actual Mercy projectile impacts under the declared server capsule model."""

import unittest
from dataclasses import replace
from unittest.mock import patch

import test_game_mercy_boost as boost
import test_game_mercy_npc_geometry as geometry
import test_game_mercy_staff as staff
from test_game_combat import aim
from test_game_mover import FLOOR, wall

from ow174.game import combat, stats
from ow174.game.collision import World
from ow174.game.commands import FIRE, SECONDARY, Command
from ow174.game.content import heroes
from ow174.game.script.driver import ULT_CHARGE


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


class MercyBlasterCriticalTests(unittest.TestCase):
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
        return f, target

    def test_body_then_head_changes_actual_health_damage_stat_and_hit_marker(self):
        f, target = self.fixture()
        vitals = f.fight.vitals_of(target)
        before = vitals.total
        shot(f.run, f.mercy, target.feet, 0.8)
        self.assertEqual(before - vitals.total, 20)
        shot(f.run, f.mercy, target.feet, target.eye)
        self.assertEqual(before - vitals.total, 60)
        self.assertEqual(f.mercy.stats.value(stats.HERO_DAMAGE), 60)
        hits = [message['+0x78'] for message in f.mercy.client.messages(combat.HIT)]
        self.assertEqual([(h['+0x4'], h['+0x8']) for h in hits], [(20, 0), (40, combat.CRITICAL)])

    def test_high_aim_does_not_damage_through_a_wall_or_above_the_capsule(self):
        f, target = self.fixture()
        vitals = f.fight.vitals_of(target)
        before = vitals.total
        f.match.collision_world = World(FLOOR + wall(-6, 3, 6, 3))
        shot(f.run, f.mercy, target.feet, target.eye)
        self.assertEqual(vitals.total, before)
        f.match.collision_world = World(FLOOR)
        shot(f.run, f.mercy, target.feet, target.height + 0.5)
        self.assertEqual(vitals.total, before)
        self.assertFalse(f.mercy.client.messages(combat.HIT))
        weapon = next(i for i in f.mercy.body_script.component.instances.values() if i.graph.index == 0x006D)
        self.assertEqual(weapon.find_var(0, 53).value(), 18)

    def test_projectile_reaches_capsule_surface_inside_but_not_beyond_authored_100m(self):
        for distance, expected in ((100.4, 20), (100.6, 0)):
            with self.subTest(target_distance=distance):
                f, target = self.fixture(distance)
                self.assertAlmostEqual(target.feet[2], distance, places=2)
                vitals = f.fight.vitals_of(target)
                before = vitals.total
                shot(f.run, f.mercy, target.feet, 0.8, frames=132)
                self.assertEqual(before - vitals.total, expected)
                self.assertFalse(f.fight.rockets)

    @unittest.skipUnless(boost.EFFECT_BUNDLE.is_file(), "external extracted effect pack required")
    def test_critical_uses_launch_boost_after_beam_release(self):
        b = boost.MercyBoostTests()
        b.setUp()
        self.addCleanup(b.doCleanups)
        b.match.switch_hero(b.ally, heroes()[staff.MERCY])
        equip(b.run, b.mercy)
        attacker, caster = (p.body_script.component for p in (b.mercy, b.ally))
        for component in (attacker, caster):
            component.set(12918, 0.0)
        attack_charge, boost_charge = (c.entity_var(ULT_CHARGE) for c in (attacker, caster))
        attack_before, boost_before = attack_charge.value(), boost_charge.value()
        x, y, z = b.mercy.mover.position
        beam_yaw, beam_pitch = aim(b.fight._eye(b.ally), (x, y + 1, z))
        beam = (beam_yaw, beam_pitch, SECONDARY)
        for _ in range(10):
            b.run.step({b.ally: beam})
        self.assertAlmostEqual(b.fight.damage_scale(b.mercy), 1.3)
        target = next(t for t in b.fight._targets() if t.owner is b.enemy)
        vitals = b.fight.vitals_of(target)
        before = vitals.total
        x, y, z = target.feet
        yaw, pitch = aim(b.fight._eye(b.mercy), (x, y + target.eye, z))
        b.run.step({b.mercy: (yaw, pitch, FIRE), b.ally: beam})
        for _ in range(25):
            b.run.step({})
        self.assertEqual(b.fight.damage_scale(b.mercy), 1)
        self.assertAlmostEqual(before - vitals.total, 52, places=4)
        self.assertAlmostEqual(b.mercy.stats.value(stats.HERO_DAMAGE), 52, places=4)
        self.assertEqual(b.ally.stats.value(stats.HERO_DAMAGE), 0)
        self.assertAlmostEqual(attack_charge.value() - attack_before, 40, places=4)
        self.assertAlmostEqual(boost_charge.value() - boost_before, 12, places=4)
        original = next(i for i in attacker.instances.values() if i.graph.index == 0x0B8B)
        self.assertAlmostEqual(original.find_var(0, 16102).value(), 12, places=4)

    @unittest.skipUnless(geometry.NPC_MOVERS.is_file(), "prepared NPC mover input required")
    def test_body_and_high_capsule_impacts_on_each_prepared_talon_body(self):
        f = geometry.MercyNpcGeometryTests()
        f.setUp()
        self.addCleanup(f.doCleanups)
        npc_fixture = f.npc_fixture()
        for definition in npc_fixture.catalog.values():
            with self.subTest(npc=definition.name):
                run, mercy, _, target = npc_fixture.spawn(definition, 1)
                equip(run, mercy)
                vitals = run.match.combat.vitals_of(target)
                before = vitals.total
                shot(run, mercy, target.feet, 0.8)
                self.assertEqual(before - vitals.total, 20)
                shot(run, mercy, target.feet, min(target.eye, target.height - 0.08))
                self.assertEqual(before - vitals.total, 60)
