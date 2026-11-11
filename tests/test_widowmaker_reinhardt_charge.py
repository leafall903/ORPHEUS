"""Actual Reinhardt Charge against Widowmaker in build 104319."""

import unittest

from test_game_combat import aim, in_world
from test_widowmaker_barriers import _ProjectedBarrierMatch
from test_widowmaker_combat import FIRE, _WidowmakerMatch
from test_widowmaker_sleep import ANA

from ow174.game import earthshatter, server_snapshot
from ow174.game.collision import World
from ow174.game.content import SOLDIER, heroes
from ow174.game.script.expr import Entity


class WidowmakerReinhardtChargeTests(_WidowmakerMatch, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.assertTrue(self.run.match.switch_hero(self.b, heroes()[earthshatter.HERO]))
        self.a.mover.position[:] = (0, 0, 4)
        self.b.mover.position[:] = (0, 0, 0)
        self.a.mover.body = self.b.mover.body = None
        self.fight = self.run.match.combat

    def wall(self, z):
        self.run.match.collision_world = World([
            (-40, 0, -40, -40, 0, 40, 40, 0, 40),
            (-40, 0, -40, 40, 0, 40, 40, 0, -40),
            (-5, 0, z, 5, 0, z, 5, 4, z),
            (-5, 0, z, 5, 4, z, -5, 4, z),
        ])

    def capture(self):
        self.run.step({self.b: (0, 0, 8)})
        for _ in range(85):
            self.run.step({})
            if self.fight.charges.controlled(self.a.body):
                return
        instance = next(i for i in self.b.body_script.component.instances.values() if i.graph.index == 0x7D)
        self.fail(str((self.b.mover.position, self.a.mover.position, instance.state(3).active,
                       [(k, instance.find_var(0, k).value()) for k in (7726, 9149, 11861)])))

    def test_actual_capture_moves_widow_blocks_fire_and_wall_impact_kills(self):
        self.wall(13)
        self.capture()
        initial = self.a.mover.position[2]
        weapon = next(i for i in self.a.body_script.component.instances.values() if i.graph.index == 0x1B)
        ammo = weapon.vars[53].value()
        self.press(FIRE, 4)
        self.assertEqual(weapon.vars[53].value(), ammo)
        self.assertGreater(self.a.mover.position[2], initial)
        for _ in range(80):
            self.run.step({})
            if self.fight.is_dead(self.a):
                break
        self.assertTrue(self.fight.is_dead(self.a))
        self.assertFalse(self.fight.charges.live)
        self.assertLess(self.a.mover.position[2], 13)

    def test_replacing_caster_releases_without_impact_damage(self):
        self.capture()
        self.assertEqual(self.health(self.a), 175)
        self.assertTrue(self.run.match.switch_hero(self.b, heroes()[SOLDIER]))
        self.assertFalse(self.fight.charges.live)
        self.assertFalse(self.fight.is_stunned(self.a))
        self.assertEqual(self.health(self.a), 175)


    def test_actual_caster_death_releases_captured_widow(self):
        ally = self.run.match.add_player(3, 'Rescuer', SOLDIER, self.a.team, False)
        in_world(self.run.match, ally)
        self.assertTrue(self.run.match.switch_hero(ally, heroes()[SOLDIER]))
        ally.mover.position[:] = (-3, 0, 0)
        ally.mover.body = None
        self.run.players.append(ally)
        self.capture()
        caster = self.fight.vitals_of(self.target(self.b))
        caster.damage(caster.total - 1)
        caster.damage(caster.total - 1)
        self.assertEqual(caster.total, 1)
        self.press(FIRE, attacker=ally, defender=self.b)
        self.assertTrue(caster.dead)
        self.assertFalse(self.fight.charges.live)
        self.assertFalse(self.fight.is_stunned(self.a))
        self.assertEqual(self.health(self.a), 175)

    def test_actual_sleep_interrupts_charge_and_releases_widow_without_resuming(self):
        ally = self.run.match.add_player(3, 'Ana', ANA, self.a.team, False)
        in_world(self.run.match, ally)
        self.assertTrue(self.run.match.switch_hero(ally, heroes()[ANA]))
        ally.mover.position[:] = (0, 0, 17)
        ally.mover.body = None
        self.run.players.append(ally)
        self.capture()
        self.press(8, attacker=ally, defender=self.b, height=1.2)
        for _ in range(48):
            self.press(0, attacker=ally, defender=self.b, height=1.2)
            if self.b.body in self.fight.sleeps:
                break
        self.assertIn(self.b.body, self.fight.sleeps)
        self.assertFalse(self.fight.charges.live)
        self.assertFalse(self.fight.is_stunned(self.a))
        self.assertEqual(self.health(self.a), 175)
        instance = next(i for i in self.b.body_script.component.instances.values() if i.graph.index == 0x7D)
        self.assertFalse(instance.state(3).active)
        self.assertGreater(instance.state(46).remaining(), 0)
        for _ in range(320):
            self.run.step({})
        self.assertFalse(self.fight.is_stunned(self.b))
        self.assertFalse(self.fight.charges.live)
        self.assertFalse(instance.state(3).active)

    def test_chain_hook_does_not_take_over_actual_charge_capture(self):
        roadhog = 0x02E0000000000040
        other = self.run.match.add_player(3, 'Roadhog', roadhog, self.b.team, False)
        in_world(self.run.match, other)
        self.assertTrue(self.run.match.switch_hero(other, heroes()[roadhog]))
        other.mover.position[:] = (0, 0, 16)
        other.mover.body = None
        self.run.players.append(other)
        self.capture()
        self.run.step({other: (-32768, 0, 8)})
        launched = False
        for _ in range(40):
            self.run.step({other: (-32768, 0, 0)})
            launched |= bool(self.fight.hooks.flights)
            self.assertFalse(self.fight.hooks.controlled(self.a.body))
        self.assertTrue(launched)
        self.assertTrue(self.fight.charges.controlled(self.a.body))
        self.assertEqual(self.health(self.a), 175)

    def test_wall_and_allies_reject_actual_capture(self):
        for mode in ('wall', 'ally'):
            with self.subTest(mode=mode):
                self.setUp()
                if mode == 'wall':
                    self.wall(2)
                else:
                    self.a.team = self.b.team
                self.run.step({self.b: (0, 0, 8)})
                for _ in range(95):
                    self.run.step({})
                    self.assertFalse(self.fight.charges.controlled(self.a.body))
                self.assertEqual(self.health(self.a), 175)
                self.assertEqual(tuple(self.a.mover.position), (0, 0, 4))

    def test_active_capture_is_stored_and_timeout_releases_without_wall_damage(self):
        self.capture()
        manager = self.fight.charges
        run, = manager.live.values()

        def capture():
            storage = server_snapshot._Graph({
                'combat': self.fight, 'caster': self.b, 'graph': run.instance,
            }, server_snapshot.WorldRegistry())
            root = storage.value(manager)
            return root, storage.finish()

        first = capture()
        run.heading += 1
        self.assertNotEqual(capture(), first)
        run.heading -= 1
        for _ in range(250):
            self.run.step({})
        self.assertFalse(manager.live)
        self.assertFalse(self.fight.is_stunned(self.a))
        self.assertEqual(self.health(self.a), 175)


class WidowmakerReinhardtChargeBarrierTests(_ProjectedBarrierMatch, unittest.TestCase):
    def test_projected_barrier_blocks_charge_capture_and_absorbs_bump(self):
        self.assertTrue(self.run.match.switch_hero(self.b, heroes()[earthshatter.HERO]))
        self.a.mover.position[:] = (0, 0, 4)
        self.b.mover.position[:] = (0, 0, 0)
        self.c.mover.position[:] = (-2, 0, 4)
        for player in self.run.players:
            player.mover.body = None
        fight = self.run.match.combat
        protecting = aim(fight._eye(self.c), fight._eye(self.a))
        self.run.step({self.c: (*protecting, 0x10)})
        self.assertEqual(self.barrier().vars[536].value(), Entity(self.a.body))
        self.run.step({self.b: (0, 0, 8)})
        for _ in range(95):
            self.run.step({})
            self.assertFalse(fight.charges.controlled(self.a.body))
        self.assertEqual(fight.zarya_barriers.projected[self.c.body].remaining, 150)
        self.assertEqual(self.health(self.a), 175)
        self.assertEqual(tuple(self.a.mover.position), (0, 0, 4))
