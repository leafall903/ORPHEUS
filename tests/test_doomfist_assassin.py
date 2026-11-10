"""Actual1157 against Doomfist; declared manual target/contact/clock reconstruction."""

import os
import unittest

import test_doomfist_enforcer as enforcer_tests
import test_doomfist_pve as npc_tests
from test_doomfist_combat import DoomfistFixture
from test_doomfist_meteor import charge_by_neutral_commands
from test_game_combat import aim

from ow174.game import combat, defenses, npc, npc_control, stats
from ow174.game.commands import Command
from ow174.game.content import heroes
from ow174.game.script import nodes


@unittest.skipUnless(os.environ.get('ORPHEUS_NPC_DATA_DIR'), 'requires actual NPC bundle')
class AssassinIncomingTests(DoomfistFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        npc_tests.ActualNpcDamageTests.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        npc_tests.ActualNpcDamageTests.tearDownClass.__func__(cls)

    create_enemy = npc_tests.ActualNpcDamageTests.create_enemy
    damage_events = enforcer_tests.EnforcerIncomingTests.damage_events

    def enemy(self, *, team=1):
        definition = next(d for d in self.catalog.values() if d.name == 'Assassin')
        bot, _ = self.create_enemy(definition, team=team, distance=2)
        x, y, z = self.a.mover.position
        self.place_player(self.b, (x + 30, y, z))
        self.face(bot)
        own = self.fight.vitals_of(next(t for t in self.fight._targets() if t.owner is self.a))
        return bot, own

    def face(self, bot):
        self.a.mover.yaw, self.a.mover.pitch = aim(
            self.fight._eye(self.a), (bot.position[0], bot.position[1] + 1, bot.position[2]))

    def engage(self, bot):
        self.face(bot)
        self.assertEqual(npc_control.start(self.match, self.a), bot.entity)
        self.run.step({})
        self.assertEqual(self.fight.pins, {bot.entity: self.a.body})

    @unittest.skipUnless(os.environ.get('ORPHEUS_REINHARDT_COLLISION'), 'requires audited barrier geometry')
    def test_actual_pin_contact_bypasses_active_allied_shield_and_projectile_defenses(self):
        for hero, button, defense in (
            (0x02E0000000000007, 2, 'barrier'),
            (defenses.GENJI, 16, 'deflect'),
            (defenses.SIGMA, 8, 'grasp'),
        ):
            with self.subTest(defense=defense):
                bot, own = self.enemy()
                self.match.switch_hero(self.b, heroes()[hero])
                command = {self.b: (0, 0, button)}
                self.run.step(command)
                if defense == 'barrier':
                    host = self.fight.hand_cannon_barriers
                    for _ in range(400):
                        if host.sync(self.b).active:
                            break
                        self.run.step(command)
                    self.assertTrue(host.sync(self.b).active)
                self.face(bot)
                npc_control.start(self.match, self.a)
                x, y, z = self.a.mover.position
                if defense == 'barrier':
                    self.place_player(self.b, (x + 1.4, y, z + .2))
                else:
                    self.place_player(self.b, (x, y, z + 1))
                origin = (x, bot.position[1] + combat.eye_height(bot.body), bot.position[2])
                delta = tuple(a - b for a, b in zip(self.fight._eye(self.a), origin, strict=True))
                distance = sum(v*v for v in delta) ** .5
                heading = tuple(v / distance for v in delta)
                if defense == 'barrier':
                    host = self.fight.hand_cannon_barriers
                    shield = host.ray_contact(bot, bot.team, origin, heading, distance)
                    self.assertIsNotNone(shield)
                    pool = shield[2]
                    self.assertEqual(pool.current, 1600)
                else:
                    # The same line would intersect this defense if it carried
                    # a supported projectile or weapon ray.
                    self.assertIsNotNone(defenses.ray_interception(
                        self.fight, bot, bot.team, origin, heading, distance, absorbing=defense == 'grasp',
                    ))
                follow = command if defense == 'barrier' else {}
                self.run.step(follow)
                self.assertEqual(self.fight.pins, {bot.entity: self.a.body})
                self.assertEqual(own.total, 225)
                for _ in range(70):
                    self.run.step(follow)
                if defense == 'barrier':
                    self.assertTrue(pool.active)
                    self.assertEqual(pool.current, 1600)
                elif defense == 'grasp':
                    self.assertEqual(defenses.grasp(self.b).vars[21312].value(), 0)
                else:
                    self.assertEqual(self.b.stats.value(stats.HERO_DAMAGE), 0)
                self.assertLess(own.total, 225)
                npc_control.stop(self.match)

    def test_actual_pin_drains_uppercut_shields_before_permanent_health(self):
        bot, own = self.enemy()
        x, y, z = self.a.mover.position
        self.b.team = 1
        self.place_player(self.b, (x + 1.5, y, z))
        yaw, pitch = aim(self.fight._eye(self.a), (x + 1.5, y + 1, z))
        self.run.step({self.a: (yaw, pitch, 8)})
        self.assertEqual(self.vitals().total, 150)
        self.assertEqual(own.temporary_shields, 30)
        deadline = own.temporary_decay_at
        self.place_player(self.a, (x, y, z))
        self.place_player(self.b, (x + 30, y, z))
        self.engage(bot)
        self.assertEqual(sum(own.now.values()), 250)
        self.assertEqual(own.temporary_shields, 5)
        self.assertEqual(own.temporary_decay_at, deadline)
        self.settle(80)
        self.assertEqual(own.temporary_shields, 0)
        self.assertEqual(own.temporary_decay_at, deadline)
        self.assertLess(own.total, 250)
        self.assertEqual(self.fight.pins, {bot.entity: self.a.body})
        npc_control.stop(self.match)

    def test_actual_entry_delay_rate_stop_and_fresh_engagement(self):
        bot, own = self.enemy()
        with self.damage_events(bot) as events:
            self.engage(bot)
            self.assertEqual(own.total, 225)
            self.settle(30)
            self.assertEqual(own.total, 225)
            self.settle(70)
            self.assertLess(own.total, 225)
            self.assertEqual(events[0]['amount'], 25)
            # The authored one-second delay enters partway through a 16ms frame.
            self.assertAlmostEqual(events[1]['amount'], .7, places=5)
            self.assertTrue(all(abs(e['amount'] - 1.6) < 1e-5 for e in events[2:]), events)
            self.assertEqual(npc_control.stop(self.match), 1)
            stopped = own.total
            self.assertFalse(self.fight.pins)
            self.settle(40)
            self.assertEqual(own.total, stopped)
            self.engage(bot)
            self.assertAlmostEqual(own.total, stopped - 25, places=4)
            self.assertTrue(bot.body_script.component.instances)

    def test_actual_pin_rejects_primary_punch_uppercut_slam_and_releases_inputs(self):
        bot, own = self.enemy()
        self.engage(bot)
        before = tuple(self.a.mover.position)
        weapon = next(i for i in self.a.body_script.component.instances.values()
                      if i.graph.index == 0xD0A)
        for button in (1, 2, 8, 16):
            for _ in range(5):
                self.run.step({self.a: (0, 0, button)})
        for _ in range(10):
            self.run.frame += 1
            self.match.update(self.run.frame * combat.TICK, self.run.frame)
            self.a.take_commands([Command(self.run.frame, forward=127, buttons=1)])
            self.b.take_commands([Command(self.run.frame)])
        self.assertEqual(weapon.vars[53].value(), 4)

        self.assertFalse(self.fight.rockets)
        self.assertEqual(self.fight.vitals[bot.entity].total, 500)
        self.assertEqual(tuple(self.a.mover.position), before)
        self.assertTrue(self.fight.is_stunned(self.a))
        npc_control.stop(self.match)
        self.assertFalse(self.fight.is_stunned(self.a))
        self.face(bot)
        self.run.step({})
        self.run.step({self.a: (self.a.mover.yaw, self.a.mover.pitch, 1)})
        self.settle(10)
        self.assertEqual(weapon.vars[53].value(), 3)
        self.assertLess(self.fight.vitals[bot.entity].total, 500)
        self.assertEqual(own.total, 225)

    def test_actual_ally_punch_interrupts_source_and_releases_victim_immediately(self):
        bot, own = self.enemy()
        self.match.switch_hero(self.b, heroes()[0x02E000000000012F])
        x, y, z = bot.position
        self.place_player(self.b, (x, y, z + 2))
        yaw, pitch = aim(self.fight._eye(self.b), (x, y + .8, z))
        self.engage(bot)
        for _ in range(40):
            self.run.step({self.b: (yaw, pitch, 2)})
        self.run.step({self.b: (yaw, pitch, 0)})
        for _ in range(60):
            if self.fight.punches.controlled(bot.entity):
                break
            self.run.step({})
        self.assertTrue(self.fight.punches.controlled(bot.entity))
        self.assertNotIn(bot.entity, self.fight.pins)
        self.assertFalse(self.fight.is_stunned(self.a))
        stopped, checked = own.total, 0
        while self.fight.punches.controlled(bot.entity):
            self.run.step({})
            if self.fight.punches.controlled(bot.entity):
                self.assertEqual(own.total, stopped)
            checked += 1
        self.assertGreater(checked, 0)
        npc_control.stop(self.match)

    def test_actual_ally_flashbang_releases_pin_until_exclusive_control_deadline(self):
        bot, own = self.enemy()
        self.match.switch_hero(self.b, heroes()[0x02E0000000000042])
        x, y, z = bot.position
        self.place_player(self.b, (x + 2, y, z))
        self.engage(bot)
        yaw, pitch = aim(self.fight._eye(self.b), (x, y + .7, z))
        self.run.step({self.b: (yaw, pitch, 16)})
        for _ in range(60):
            if self.fight.is_stunned(bot):
                break
            self.run.step({})
        self.assertTrue(self.fight.is_stunned(bot))
        self.assertFalse(self.fight.pins)
        self.assertFalse(self.fight.is_stunned(self.a))
        stopped = own.total
        until = self.fight.stuns[bot.entity]
        while self.run.frame + 1 < until:
            self.run.step({})
            self.assertEqual(own.total, stopped)
            self.assertFalse(self.fight.pins)
        npc_control.stop(self.match)

    def test_actual_body_replacement_retires_old_pin_without_damaging_retired_pool(self):
        bot, own = self.enemy()
        self.engage(bot)
        old_body = self.a.body
        self.match.switch_hero(self.a, heroes()[0x02E0000000000042])
        self.assertNotEqual(self.a.body, old_body)
        self.assertNotIn(old_body, self.fight.pins.values())
        self.assertFalse(self.fight.is_stunned(self.a))
        npc_control.stop(self.match)
        self.settle(100)
        self.assertEqual(own.total, 225)
        self.assertFalse(self.fight.pins)

    def test_actual_meteor_protection_refuses_new_pin(self):
        bot, own = self.enemy()
        charge_by_neutral_commands(self)
        self.run.step({self.a: (0, 0, 32)})
        self.assertTrue(self.fight.protected(self.a.body))
        self.face(bot)
        npc_control.start(self.match, self.a)
        self.settle(180)
        self.assertTrue(self.fight.protected(self.a.body))
        self.assertEqual(own.total, 250)
        self.assertFalse(self.fight.pins)
        self.assertFalse(bot.attack_input)
        npc_control.stop(self.match)
        self.run.step({self.a: (0, 0, 1)})
        self.settle(150)
        cast = self.fight.meteors.casts[self.a.body]
        self.assertTrue(cast.finished)
        self.assertEqual(cast.hit, (bot.entity,))
        self.assertFalse(self.fight.protected(self.a.body))
        self.assertFalse(bot.dead)
        x, y, z = bot.position
        self.place_player(self.a, (x, y, z - 2))
        with self.damage_events(bot) as events:
            self.engage(bot)
        self.assertEqual([event["dealt"] for event in events], [25])
        self.assertTrue(self.fight.is_stunned(self.a))
        npc_control.stop(self.match)

    def test_actual_pin_resumes_after_meteor_impact_without_restart(self):
        bot, own = self.enemy()
        charge_by_neutral_commands(self)
        self.run.step({self.a: (0, 0, 32)})
        self.settle(70)
        self.run.step({self.a: (0, 0, 1)})
        cast = self.fight.meteors.casts[self.a.body]
        self.assertTrue(cast.committed)
        self.assertFalse(cast.impacted)
        self.face(bot)
        self.assertEqual(npc_control.start(self.match, self.a), bot.entity)
        component = bot.body_script.component
        button = npc_control.button(bot)

        with self.damage_events(bot) as events:
            for _ in range(90):
                if cast.impacted:
                    break
                self.assertTrue(self.fight.protected(self.a.body))
                self.assertTrue(bot.firing)
                self.assertFalse(bot.attack_input)
                self.assertNotIn(button, component.held)
                self.assertIsNone(component.world.manual_target)
                self.assertFalse(self.fight.pins)
                self.assertFalse(self.fight.is_stunned(self.a))
                self.assertEqual(own.total, 250)
                self.run.step({})
            self.assertTrue(cast.impacted)
            self.assertEqual(cast.hit, (bot.entity,))
            self.assertFalse(self.fight.protected(self.a.body))
            self.assertFalse(events)
            self.assertFalse(self.fight.pins)
            self.assertEqual(own.temporary_shields, 75)
            self.run.step({})
            self.assertEqual([event['dealt'] for event in events], [25])
        self.assertIn(button, component.held)
        self.assertEqual(component.world.manual_target, self.a.body)
        self.assertEqual(self.fight.pins, {bot.entity: self.a.body})
        self.assertTrue(self.fight.is_stunned(self.a))
        self.assertEqual(own.now[combat.HEALTH_POOL], 250)
        self.assertEqual(own.temporary_shields, 50)
        npc_control.stop(self.match)
        self.assertFalse(self.fight.pins)
        self.assertFalse(self.fight.is_stunned(self.a))

    def test_actual_pin_interrupts_charged_punch_without_late_damage_or_cooldown_bypass(self):
        bot, _ = self.enemy()
        for _ in range(30):
            self.run.step({self.a: (0, 0, 2)})
        punch = next(i for i in self.a.body_script.component.instances.values()
                     if i.graph.index == 0xD06)
        ability = punch.state(0)
        self.assertTrue(ability.flags & nodes.Ability.RUNNING)
        self.engage(bot)
        self.assertFalse(ability.flags & nodes.Ability.RUNNING)
        self.assertGreater(ability.remaining(), 0)
        self.assertNotIn(self.a.body, self.fight.punches.dashes)
        npc_control.stop(self.match)
        for _ in range(10):
            self.run.step({self.a: (0, 0, 2)})
        self.assertFalse(ability.flags & nodes.Ability.RUNNING)
        self.assertEqual(self.fight.vitals[bot.entity].total, 500)
        self.settle(300)
        self.assertEqual(ability.remaining(), 0)
        self.run.step({self.a: (0, 0, 2)})
        self.assertTrue(ability.flags & nodes.Ability.RUNNING)

    def test_actual_hand_cannon_source_death_releases_pin_without_health_fixture(self):
        bot, own = self.enemy()
        # Whittle the real500 pool using only ordinary primary commands before pin.
        for _ in range(1800):
            if self.fight.vitals[bot.entity].total <= 66:
                break
            self.face(bot)
            self.run.step({self.a: (self.a.mover.yaw, self.a.mover.pitch, 1)})
        self.assertFalse(bot.dead)
        self.assertLessEqual(self.fight.vitals[bot.entity].total, 66)
        self.match.switch_hero(self.b, heroes()[0x02E000000000012F])
        x, y, z = bot.position
        self.place_player(self.b, (x + 1.5, y, z))
        self.engage(bot)
        for _ in range(150):
            if bot.dead:
                break
            yaw, pitch = aim(self.fight._eye(self.b), (x, y + .7, z))
            self.run.step({self.b: (yaw, pitch, 1)})
        self.assertTrue(bot.dead)
        self.assertEqual(self.fight.vitals[bot.entity].killer, self.b.body)
        self.assertGreater(own.total, 0)
        self.assertFalse(self.fight.pins)
        stopped = own.total
        self.settle(100)
        self.assertEqual(own.total, stopped)

    def test_actual_wall_refuses_acquisition_and_releases_existing_pin(self):
        bot, own = self.enemy()
        npc_control.start(self.match, self.a)
        npc_tests.ActualNpcDamageTests.install_wall(self, present=True)
        self.settle(100)
        self.assertEqual(own.total, 250)
        self.assertFalse(self.fight.pins)
        npc_tests.ActualNpcDamageTests.install_wall(self, present=False)
        self.settle(2)
        self.assertEqual(own.total, 225)
        self.assertTrue(self.fight.pins)
        npc_tests.ActualNpcDamageTests.install_wall(self, present=True)
        self.settle(2)
        self.assertFalse(self.fight.pins)
        stopped = own.total
        self.settle(100)
        self.assertEqual(own.total, stopped)
        self.assertTrue(bot.body_script.component.instances)

    def test_actual_ally_range_and_faction_change_stop_pin(self):
        bot, own = self.enemy(team=0)
        with self.assertRaises(ValueError):
            npc_control.start(self.match, self.a)
        self.assertEqual(own.total, 250)
        bot.team = 1
        x, y, z = self.a.mover.position
        self.place_player(self.a, (x, y, z - 5))
        self.face(bot)
        npc_control.start(self.match, self.a)
        self.settle(100)
        self.assertEqual(own.total, 250)
        self.assertFalse(self.fight.pins)
        self.place_player(self.a, (x, y, z))
        self.settle(2)
        self.assertEqual(own.total, 225)
        self.assertTrue(self.fight.pins)
        bot.team = 0
        self.settle(2)
        self.assertFalse(self.fight.pins)
        stopped = own.total
        self.settle(100)
        self.assertEqual(own.total, stopped)

    def test_actual_source_removal_releases_pin_and_retires_world_references(self):
        bot, own = self.enemy()
        self.engage(bot)
        world = bot.body_script.component.world
        self.assertEqual(npc.remove_spawned(self.match), (bot.entity,))
        self.assertFalse(self.fight.pins)
        self.assertIsNone(world.match)
        self.assertIsNone(world.manual_target)
        self.assertFalse(self.fight.is_stunned(self.a))
        self.settle(100)
        self.assertEqual(own.total, 225)

    def test_actual_sustained_pin_kills_doomfist_then_normal_respawn_is_uncontrolled(self):
        bot, own = self.enemy()
        old_body = self.a.body
        self.engage(bot)
        for _ in range(500):
            if self.fight.is_dead(self.a):
                break
            self.run.step({})
        self.assertTrue(self.fight.is_dead(self.a))
        self.assertEqual(own.total, 0)
        self.assertEqual(own.killer, bot.entity)
        self.assertEqual(self.a.stats.value(stats.DEATHS), 1)
        self.assertFalse(self.fight.pins)
        npc_control.stop(self.match)
        deadline = next(t for t, player in self.fight.respawns if player is self.a)
        self.assertAlmostEqual(deadline - own.died_at, 10)
        while self.fight.now < deadline + combat.TICK:
            self.run.step({})
        self.assertNotEqual(self.a.body, old_body)
        self.assertFalse(self.fight.is_stunned(self.a))
        fresh = self.fight.vitals_of(next(t for t in self.fight._targets() if t.owner is self.a))
        self.assertEqual(fresh.total, 250)
        self.assertEqual(fresh.temporary_shields, 0)
        weapon = next(i for i in self.a.body_script.component.instances.values()
                      if i.graph.index == 0xD0A)
        self.assertEqual(weapon.vars[53].value(), 4)
