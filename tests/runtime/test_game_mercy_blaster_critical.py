"""Delivered Mercy projectile critical, capsule, wall and range checks."""
import unittest
from dataclasses import replace
from unittest.mock import patch
import test_game_mercy_staff as staff
from test_game_combat import aim
from test_game_mover import FLOOR, wall
from ow174.game import combat, stats
from ow174.game.collision import World
from ow174.game.commands import FIRE, Command

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
        # The combined host also sweeps the authored projectile extent.
        # 100.6 is its contact edge; 100.8 puts the entire shape beyond range.
        for distance, expected in ((100.4, 20), (100.8, 0)):
            with self.subTest(target_distance=distance):
                f, target = self.fixture(distance)
                self.assertAlmostEqual(target.feet[2], distance, places=2)
                vitals = f.fight.vitals_of(target)
                before = vitals.total
                shot(f.run, f.mercy, target.feet, 0.8, frames=132)
                self.assertEqual(before - vitals.total, expected)
                self.assertFalse(f.fight.rockets)
