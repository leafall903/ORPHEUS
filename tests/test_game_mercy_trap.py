"""104319 Steel Trap input, device arming and Mercy's receiver graph."""

import unittest

import test_game_mercy_staff
from test_game_mercy_resurrect import RESURRECT
from test_game_combat import aim
from test_game_mercy_blaster_critical import equip, shot
from test_game_mover import FLOOR, wall

from ow174.game import combat, junkrat_trap, phase_defenses
from ow174.game.collision import World
from ow174.game.commands import Command
from ow174.game.content import heroes
from ow174.game.script import expr, graph

JUNKRAT = 0x02E0000000000065
THROW = 0x10
SHIFT = 0x08


class MercyTrapTests(unittest.TestCase):
    def setUp(self):
        if graph.graph(junkrat_trap.RECEIVER) is None:
            self.skipTest("104319 Steel Trap receiver graph 09BC requires the external graph supplement")
        fixture = test_game_mercy_staff.MercyStaffTests()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        self.run, self.fight = fixture.run, fixture.fight
        self.mercy, self.junkrat, self.ally = fixture.mercy, fixture.ally, fixture.enemy
        self.run.match.switch_hero(self.junkrat, heroes()[JUNKRAT])
        self.junkrat.mover.position[:] = (0.0, 1.0, 4.7)
        self.junkrat.mover.body = None
        mercy = next(t for t in self.fight._targets() if t.owner is self.mercy)
        self.mercy_vitals = self.fight.vitals_of(mercy)

    def deploy(self):
        self.run.step({self.junkrat: (-32768, 0, THROW)})
        for _ in range(90):
            self.run.step({})
            host = self.fight.junkrat_traps
            if host is not None and host.flights:
                flight, = host.flights
                self.assertAlmostEqual(flight.gravity, 20.0)
                self.assertAlmostEqual(flight.velocity[2], -10.0)
                break
        else:
            self.fail("actual throw did not launch 11A9")
        for _ in range(90):
            self.run.step({})
            if host.devices:
                device, = host.devices.values()
                self.assertFalse(host.flights)
                self.assertAlmostEqual(device.position[1], 1.0)
                self.assertEqual(device.arms_ms, self.run.frame * 16 + 500)
                return host, device
        self.fail("trap did not land")

    def walk(self, count):
        for _ in range(count):
            self.run.frame += 1
            frame = self.run.frame
            self.run.match.update(frame * combat.TICK, frame)
            for player in self.run.players:
                player.take_commands([Command(frame, forward=127 if player is self.mercy else 0)])

    def test_steel_trap_arms_damages_and_roots_mercy_until_release(self):
        self.junkrat.team = 1 - self.mercy.team
        self.ally.team = self.mercy.team
        self.ally.mover.position[:] = (0, 1, 12)
        self.ally.mover.body = None
        host, device = self.deploy()
        while (self.run.frame + 1) * 16 < device.arms_ms:
            self.run.step({})
        self.assertEqual(self.mercy_vitals.total, 200)
        self.assertFalse(host.roots)
        self.run.step({})
        self.assertEqual(self.mercy_vitals.total, 120)
        self.assertIs(host.devices[self.junkrat.body], device)
        self.assertEqual(device.victim, self.mercy.body)
        root = host.roots[self.mercy.body, device.entity]
        instance = self.mercy.body_script.component.instances[root.instance]
        self.assertEqual(instance.graph.index, 0x09BC)
        self.assertTrue(instance.state(0).active)
        self.assertEqual(root.ends_ms, self.run.frame * 16 + 3000)
        pull = instance.state(instance.graph.node(6).state)
        self.assertEqual(pull.move_request(), (expr.Vec3(*device.position), 10.0))
        before_pull = self.mercy.mover.position[2]
        for _ in range(14):
            self.run.step({})
        self.assertLess(abs(self.mercy.mover.position[2] - device.position[2]),
                        abs(before_pull - device.position[2]))
        self.assertIsNone(pull.move_request())
        still = tuple(self.mercy.mover.position)
        self.walk(20)
        self.assertEqual(tuple(self.mercy.mover.position), still)
        guardian = next(i for i in instance.component.instances.values() if i.graph.index == 0x006E)
        self.press_guardian_angel()
        self.assertFalse(guardian.state(0).active)
        self.assertEqual(guardian.find_var(0, 61).value(), 0)
        while (self.run.frame + 1) * 16 < root.ends_ms:
            self.run.step({})
        self.assertIn(instance.id, instance.component.instances)
        self.run.step({})
        self.assertNotIn(instance.id, instance.component.instances)
        self.assertFalse(host.roots)
        self.assertFalse(host.devices)
        self.walk(20)
        self.assertGreater(self.mercy.mover.position[2], still[2] + 1.0)
        self.press_guardian_angel()
        self.assertTrue(guardian.state(0).active)

    def press_guardian_angel(self):
        x, y, z = self.ally.mover.position
        yaw, pitch = aim(self.fight._eye(self.mercy), (x, y + 1, z))
        self.run.step({self.mercy: (yaw, pitch, SHIFT)})

    def test_trap_ends_active_guardian_angel_and_starts_its_cooldown(self):
        self.junkrat.team = 1 - self.mercy.team
        self.ally.team = self.mercy.team
        self.ally.mover.position[:] = (0, 1, 12)
        self.ally.mover.body = None
        host, device = self.deploy()
        while (self.run.frame + 2) * 16 < device.arms_ms:
            self.run.step({})
        guardian = next(i for i in self.mercy.body_script.component.instances.values()
                        if i.graph.index == 0x006E)
        self.press_guardian_angel()
        self.assertTrue(guardian.state(0).active)
        self.press_guardian_angel()
        self.assertEqual(self.mercy_vitals.total, 120)
        self.assertIn((self.mercy.body, device.entity), host.roots)
        self.assertFalse(guardian.state(0).active)
        self.assertEqual(guardian.find_var(0, 61).value(), 1.5)

    def test_friendly_mercy_leaves_trap_armed_and_owner_replacement_retires_it(self):
        self.junkrat.team = self.mercy.team
        host, device = self.deploy()
        while self.run.frame * 16 < device.arms_ms + 64:
            self.run.step({})
        self.assertEqual(self.mercy_vitals.total, 200)
        self.assertFalse(host.roots)
        self.assertEqual(list(host.devices.values()), [device])
        self.run.match.switch_hero(self.junkrat, self.junkrat.hero)
        self.assertFalse(host.devices)

    def test_armed_trap_rejects_distance_cover_and_invulnerability(self):
        self.junkrat.team = 1 - self.mercy.team
        host, device = self.deploy()
        x, y, z = device.position
        self.mercy.mover.position[:] = (x + 3, y, z)
        self.mercy.mover.body = None
        while self.run.frame * 16 < device.arms_ms:
            self.run.step({})
        self.assertEqual(self.mercy_vitals.total, 200)
        self.assertIn(device, host.devices.values())

        match = self.run.match
        original = match.collision_world
        self.mercy.mover.position[:] = (x + 1, y, z)
        self.mercy.mover.body = None
        match.collision_world = World(FLOOR + wall(x + 0.5, z - 2, x + 0.5, z + 2))
        self.run.step({})
        self.assertEqual(self.mercy_vitals.total, 200)
        self.assertIn(device, host.devices.values())

        match.collision_world = original
        self.mercy_vitals.invulnerable_until = self.fight.now + 2.5 * combat.TICK
        for _ in range(2):
            self.run.step({})
            self.assertEqual(self.mercy_vitals.total, 200)
            self.assertIn(device, host.devices.values())
        self.run.step({})
        self.assertEqual(self.mercy_vitals.total, 120)
        self.assertEqual(device.victim, self.mercy.body)

    def test_armed_trap_catches_same_revived_mercy_on_first_unprotected_frame(self):
        self.junkrat.team = 1 - self.mercy.team
        caster = self.ally
        caster.team = self.mercy.team
        self.assertTrue(self.run.match.switch_hero(caster, self.mercy.hero))
        caster.mover.position[:] = (3, 1, 0)
        caster.mover.body = None

        body = self.mercy.body
        target = next(t for t in self.fight._targets() if t.owner is self.mercy)
        self.fight._damage(self.junkrat, target, 500, False, None, self.run.frame)
        self.assertTrue(self.fight.is_dead(self.mercy))
        x, y, z = self.mercy.mover.position
        yaw, pitch = aim(self.fight._eye(caster), (x, y + 1, z))
        self.run.step({caster: (yaw, pitch, RESURRECT)})
        for _ in range(120):
            if not self.fight.is_dead(self.mercy):
                break
            self.run.step({caster: (yaw, pitch, 0)})
        self.assertFalse(self.fight.is_dead(self.mercy))
        self.assertEqual(self.mercy.body, body)
        self.assertEqual(self.mercy_vitals.total, 200)
        protection_end = self.mercy_vitals.invulnerable_until
        self.assertGreater(protection_end, self.fight.now)

        host, device = self.deploy()
        while self.run.frame * 16 < device.arms_ms:
            self.run.step({})
        self.assertLess(self.fight.now, protection_end)
        while (self.run.frame + 1) * combat.TICK < protection_end:
            self.assertEqual(self.mercy_vitals.total, 200)
            self.assertIsNone(device.victim)
            self.assertFalse(host.roots)
            self.run.step({})
        self.assertLess(self.fight.now, protection_end)
        self.assertEqual(self.mercy_vitals.total, 200)
        self.assertIsNone(device.victim)
        self.assertFalse(host.roots)

        self.run.step({})
        self.assertGreaterEqual(self.fight.now, protection_end)
        self.assertEqual(self.mercy.body, body)
        self.assertEqual(device.victim, body)
        self.assertEqual(self.mercy_vitals.total, 120)
        root = host.roots[body, device.entity]
        instance = self.mercy.body_script.component.instances[root.instance]
        self.assertEqual(instance.graph.index, junkrat_trap.RECEIVER)
        self.assertTrue(instance.state(0).active)

    def test_trapped_mercy_can_shoot_the_device_and_end_root_early(self):
        self.junkrat.team = 1 - self.mercy.team
        equip(self.run, self.mercy)
        host, device = self.deploy()
        while self.run.frame * 16 < device.arms_ms:
            self.run.step({})
        self.assertEqual(self.mercy_vitals.total, 120)
        root = host.roots[self.mercy.body, device.entity]
        for health in (80, 60, 40, 20, 0):
            shot(self.run, self.mercy, device.position, junkrat_trap.HIT_RADIUS)
            self.assertEqual(device.health, health)
        self.assertLess(self.run.frame * 16, root.ends_ms)
        self.assertFalse(host.devices)
        self.assertFalse(host.roots)
        self.assertNotIn(root.instance, root.component.instances)
        still = self.mercy.mover.position[2]
        self.walk(20)
        self.assertGreater(self.mercy.mover.position[2], still + 1.0)

    def test_actual_orisa_fortify_uses_short_trap_branch(self):
        self.junkrat.team = 1 - self.mercy.team
        orisa = self.ally
        orisa.team = self.mercy.team
        self.run.match.switch_hero(orisa, heroes()[phase_defenses.ORISA])
        host, device = self.deploy()
        self.mercy.mover.position[0] = 5
        self.mercy.mover.body = None
        orisa.mover.position[:] = device.position
        orisa.mover.body = None
        target = next(t for t in self.fight._targets() if t.owner is orisa)
        vitals = self.fight.vitals_of(target)
        before = vitals.total
        self.run.step({orisa: (0, 0, SHIFT)})
        self.assertTrue(orisa.body_script.component.entity_var(9161).value())
        while self.run.frame * 16 < device.arms_ms:
            self.run.step({})
        self.assertAlmostEqual(before - vitals.total, 43.0, places=5)
        root = host.roots[orisa.body, device.entity]
        self.assertEqual(root.ends_ms, self.run.frame * 16 + 10)
        self.assertIn(root.instance, root.component.instances)
        self.run.step({})
        self.assertFalse(host.roots)
        self.assertFalse(host.devices)
        self.assertNotIn(root.instance, root.component.instances)

    def test_melee_spends_trap_health_only_on_visible_enemy_device(self):
        self.junkrat.team = 1 - self.mercy.team
        host, device = self.deploy()
        x, y, z = device.position
        self.mercy.mover.position[:] = (x + 1.5, y, z)
        self.mercy.mover.body = None
        yaw, pitch = aim(self.fight._eye(self.mercy), device.hit_centre())
        match = self.run.match
        original = match.collision_world
        cover = World(FLOOR + wall(x + 0.75, z - 2, x + 0.75, z + 2))
        for collision, team, health in (
            (cover, self.junkrat.team, 100),
            (original, self.mercy.team, 100),
            *((original, self.junkrat.team, remaining) for remaining in (70, 40, 10, 0)),
        ):
            match.collision_world = collision
            device.team = team
            self.run.step({self.mercy: (yaw, pitch, 0x800)})
            for _ in range(70):
                self.run.step({self.mercy: (yaw, pitch, 0)})
            self.assertEqual(device.health, health)
        self.assertFalse(host.devices)
        self.assertFalse(host.roots)
