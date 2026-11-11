"""Actual1157 input/control against Widow; manual contact/rate policy is reconstructed."""

import os
import unittest

from test_game_combat import aim
from test_game_mover import box
from test_widowmaker_npcs import _ActualNpcMatch

from ow174.game import combat, npc, npc_control, zarya
from ow174.game.collision import World
from ow174.game.commands import Command
from ow174.game.content import heroes


@unittest.skipUnless(os.environ.get('ORPHEUS_NPC_TEST_DATA'), 'requires actual NPC bundle')
class WidowmakerNpcPinTests(_ActualNpcMatch, unittest.TestCase):
    def test_actual_attacker_outside_manual_contact_releases_input(self):
        self.b.spawned = False
        bot = self.spawn(self.catalog['4'], (0, 0, 3.1))
        fight = self.run.match.combat
        self.a.mover.yaw, self.a.mover.pitch = aim(fight._eye(self.a), (0, 1, 3.1))
        npc_control.start(self.run.match, self.a)
        for _ in range(100):
            self.run.step({})
        self.assertEqual(self.health(self.a), 175)
        self.assertEqual(fight.pins, {})
        self.assertNotIn(36, bot.body_script.component.held)

    def test_new_wall_breaks_actual_pin_before_later_damage(self):
        bot = self.actor()
        self.start_pin(bot)
        before = self.health(self.a)
        self.run.match.collision_world = World([
            (-50, 0, -50, -50, 0, 50, 50, 0, 50),
            (-50, 0, -50, 50, 0, 50, 50, 0, -50),
            (-5, 0, 1.25, 5, 0, 1.25, 5, 3, 1.25),
            (-5, 0, 1.25, 5, 3, 1.25, -5, 3, 1.25),
        ])
        for _ in range(30):
            self.run.step({})
        self.assertEqual(self.health(self.a), before)
        self.assertFalse(self.run.match.combat.is_stunned(self.a))
        self.assertNotIn(bot.entity, self.run.match.combat.pins)

    def actor(self):
        self.b.spawned = False
        return self.spawn(self.catalog['4'], (0, 0, 2.5))

    def start_pin(self, bot):
        fight = self.run.match.combat
        self.a.mover.yaw, self.a.mover.pitch = aim(fight._eye(self.a), (0, 1, 2.5))
        self.assertEqual(npc_control.start(self.run.match, self.a), bot.entity)
        for _ in range(100):
            self.run.step({})
            if fight.pins.get(bot.entity) == self.a.body:
                break
        self.assertEqual(fight.pins.get(bot.entity), self.a.body)
        self.assertTrue(fight.is_stunned(self.a))

    def input_tick(self, buttons=0, forward=0):
        self.run.frame += 1
        self.run.match.update(self.run.frame * 0.016, self.run.frame)
        self.a.take_commands([Command(self.run.frame, buttons=buttons, forward=forward, extra=0)])

    def skill_counts(self):
        return {i.graph.index: i.state(0).counter
                for i in self.a.body_script.component.instances.values()
                if i.graph.index in (0x5A, 0x56, 0x67)}

    def test_actual_pin_blocks_actions_and_stop_restores_input(self):
        bot = self.actor()
        script = self.a.body_script
        script.ult_seconds = 0
        cost = script.component.vars[338].value()
        script.set_ult_charge(cost)  # charge precondition; no claim about native gain policy
        self.start_pin(bot)
        weapon = next(i for i in script.component.instances.values() if i.graph.index == 0x1B)
        ammo, counts = weapon.vars[53].value(), self.skill_counts()
        position = tuple(self.a.mover.position)
        for _ in range(20):
            self.input_tick(1 | 8 | 16 | 32 | 0x800, 127)
            self.assertEqual(weapon.vars[53].value(), ammo)
            self.assertEqual(self.skill_counts(), counts)
            self.assertEqual(tuple(self.a.mover.position), position)
            self.assertEqual(script.component.vars[342].value(), cost)
        self.assertEqual(npc_control.stop(self.run.match), 1)
        self.assertFalse(self.run.match.combat.is_stunned(self.a))
        self.input_tick(0, 127)
        self.assertGreater(self.a.mover.position[2], position[2])
        self.input_tick(1)
        self.assertLess(weapon.vars[53].value(), ammo)

    def test_actual_assassin_pin_interrupts_active_grapple(self):
        bot = self.actor()
        world = self.run.match.collision_world = World(
            box(-20, 20, -1, 0, -20, 25) + box(-8, 8, 0, 6, 14, 14.4),
        )
        self.a.mover.use(world=world)
        hook = next(i for i in self.a.body_script.component.instances.values() if i.graph.index == 0x005A)
        self.input_tick(8)
        for _ in range(80):
            if hook.state(1).active:
                break
            self.run.step({})
        self.assertTrue(hook.state(1).active)
        self.assertLess(self.a.mover.position[2], 3)
        self.start_pin(bot)
        self.assertFalse(hook.state(1).active)
        self.assertGreater(hook.vars[61].value(), 8)
        position = tuple(self.a.mover.position)
        self.assertEqual(npc_control.stop(self.run.match), 1)
        for _ in range(35):
            self.run.step({})
        self.assertEqual(tuple(self.a.mover.position), position)
        interrupted_cast = hook.state(0).counter
        self.input_tick(8)
        for _ in range(30):
            self.run.step({})
        self.assertEqual(hook.state(0).counter, interrupted_cast)
        for _ in range(800):
            self.run.step({})
        self.input_tick(8)
        for _ in range(110):
            self.run.step({})
        self.assertGreater(hook.state(0).counter, interrupted_cast)
        self.assertGreater(self.a.mover.position[2], 13)
        self.assertLess(self.a.mover.position[2], 14)

    def test_assassin_pin_cancels_venom_windup_before_launch(self):
        bot = self.actor()
        match = self.run.match
        venom = match.combat.venom
        ability = next(i for i in self.a.body_script.component.instances.values() if i.graph.index == 0x56)
        self.press(16)
        self.assertTrue(ability.state(6).flags & ability.state(6).RUNNING)
        self.assertTrue(ability.state(2).active)
        self.assertEqual(venom.flights, [])

        self.start_pin(bot)
        self.assertFalse(ability.state(6).flags & ability.state(6).RUNNING)
        self.assertFalse(ability.state(2).active)
        self.assertEqual(npc_control.stop(match), 1)
        self.press(0, 30)
        self.assertEqual(ability.state(0).counter, 0)
        self.assertEqual(venom.flights, [])
        self.assertEqual(venom.mines, [])
        self.assertEqual(venom.poison, [])

        self.press(16)
        self.press(0, 30)
        self.assertEqual(ability.state(0).counter, 1)
        self.assertEqual(len(venom.mines), 1)

    def test_assassin_pin_after_mine_launch_preserves_flight_and_cooldown(self):
        bot = self.actor()
        match = self.run.match
        venom = match.combat.venom
        ability = next(i for i in self.a.body_script.component.instances.values() if i.graph.index == 0x56)
        self.run.step({self.a: (-32768, -4096, 16)})
        for _ in range(30):
            self.run.step({self.a: (-32768, -4096, 0)})
            if venom.flights:
                break
        self.assertEqual(len(venom.flights), 1)
        flight = venom.flights[0]
        self.assertTrue(ability.state(6).flags & ability.state(6).RUNNING)
        self.assertEqual(ability.state(0).counter, 1)

        self.start_pin(bot)
        self.assertFalse(ability.state(6).flags & ability.state(6).RUNNING)
        self.assertEqual(venom.flights, [flight])
        self.assertEqual(npc_control.stop(match), 1)
        self.press(0, 90)
        self.assertEqual(venom.flights, [])
        self.assertEqual(len(venom.mines), 1)
        deployed = venom.mines[0]

        self.press(16)
        self.press(0, 40)
        self.assertEqual(ability.state(0).counter, 1)
        self.assertEqual(venom.mines, [deployed])
        self.press(0, 1000)
        self.press(16)
        self.press(0, 30)
        self.assertEqual(ability.state(0).counter, 2)
        self.assertEqual(len(venom.mines), 1)
        self.assertGreater(venom.mines[0].number, deployed.number)

    def test_committed_infra_survives_assassin_pin_until_lethal_damage(self):
        bot = self.actor()
        match = self.run.match
        fight = match.combat
        script = self.a.body_script
        script.ult_seconds = 0
        cost = script.component.vars[338].value()
        script.set_ult_charge(cost)
        self.press(32)
        charge = script.component.vars[342]
        self.assertEqual(charge.value(), 0)
        self.assertIsNotNone(script.infra_window())
        self.assertFalse(fight.revealed_to(self.a, bot.entity))

        self.start_pin(bot)
        self.assertFalse(fight.revealed_to(self.a, bot.entity))
        for _ in range(45):
            self.run.step({})
            if fight.revealed_to(self.a, bot.entity):
                break
        self.assertTrue(fight.revealed_to(self.a, bot.entity))
        self.assertEqual(fight.pins.get(bot.entity), self.a.body)
        self.assertEqual(charge.value(), 0)
        for _ in range(180):
            self.run.step({})
            if fight.is_dead(self.a):
                break
            self.assertTrue(fight.revealed_to(self.a, bot.entity))
        self.assertTrue(fight.is_dead(self.a))
        self.assertFalse(fight.revealed_to(self.a, bot.entity))
        self.assertEqual(charge.value(), 0)

    def test_projected_barrier_absorbs_assassin_damage_while_pin_remains_active(self):
        bot = self.spawn(self.catalog['4'], (0, 0, 2.5))
        match = self.run.match
        self.assertTrue(match.switch_hero(self.b, heroes()[zarya.HERO]))
        self.b.mover.position[:] = (-2, 0, 0)
        self.b.mover.body = None
        fight = match.combat
        protecting = aim(fight._eye(self.b), fight._eye(self.a))
        self.run.step({self.b: (*protecting, 0x10)})
        barrier = next(i for i in self.b.body_script.component.instances.values()
                       if i.graph.index == 0x25C)
        self.assertTrue(barrier.state(10).active)
        before = self.health(self.a)

        self.start_pin(bot)
        for _ in range(100):
            pool = fight.zarya_barriers.projected.get(self.b.body)
            if pool is not None and pool.remaining < 200:
                break
            self.run.step({})
        self.assertIsNotNone(pool)
        self.assertLess(pool.remaining, 200)
        self.assertEqual(self.health(self.a), before)
        self.assertEqual(fight.pins.get(bot.entity), self.a.body)
        self.assertTrue(fight.is_stunned(self.a))

        for _ in range(180):
            self.run.step({})
            if not barrier.state(10).active and self.health(self.a) < before:
                break
        self.assertFalse(barrier.state(10).active)
        self.assertLess(self.health(self.a), before)

    def test_actual_poison_continues_while_widow_is_pinned(self):
        bot = self.actor()
        self.a.body_script.ult_seconds = 0
        self.press(16)
        self.press(0, 120)
        fight = self.run.match.combat
        self.assertEqual(len(fight.venom.poison), 1)
        self.start_pin(bot)
        health = fight.vitals[bot.entity].total
        charge = self.a.body_script.component.vars[342].value()
        for _ in range(30):
            self.input_tick()
        self.assertLess(fight.vitals[bot.entity].total, health)
        self.assertGreater(self.a.body_script.component.vars[342].value(), charge)
        self.assertEqual(fight.pins.get(bot.entity), self.a.body)

    def test_deployed_mine_poison_survives_owner_death_until_body_replacement(self):
        bot = self.actor()
        match = self.run.match
        fight = match.combat
        script = self.a.body_script
        script.ult_seconds = 0
        script.set_ult_charge(0)
        old_body = self.a.body
        self.run.step({self.a: (-32768, 0, 16)})
        for _ in range(130):
            self.run.step({self.a: (-32768, 0, 0)})
        self.assertEqual(len(fight.venom.mines), 1)
        mine = fight.venom.mines[0]
        self.assertLess(mine.position[2], 0)
        self.assertEqual(fight.venom.poison, [])

        self.start_pin(bot)
        for _ in range(180):
            self.run.step({})
            if fight.is_dead(self.a):
                break
        self.assertTrue(fight.is_dead(self.a))
        self.assertEqual(fight.venom.mines, [mine])
        deadline_ticks = round(combat.respawn_seconds(match) / combat.TICK)
        for _ in range(deadline_ticks - 60):
            self.run.step({})
        self.assertEqual(self.a.body, old_body)
        self.assertTrue(fight.is_dead(self.a))

        x, _, z = mine.position
        bot.position = (x, 0, z)
        before = self.health(bot)
        for _ in range(30):
            self.run.step({})
        self.assertEqual(fight.venom.mines, [])
        self.assertTrue(fight.venom.poisoned_by(self.a, bot.entity))
        self.assertTrue(fight.revealed_to(self.a, bot.entity))
        self.assertLess(self.health(bot), before)
        self.assertEqual(script.component.vars[342].value(), 0)

        for _ in range(32):
            self.run.step({})
            if self.a.body != old_body:
                break
        self.assertNotEqual(self.a.body, old_body)
        self.assertFalse(fight.is_dead(self.a))
        self.assertEqual(fight.venom.poison, [])
        self.assertFalse(fight.revealed_to(self.a, bot.entity))
        after = self.health(bot)
        for _ in range(30):
            self.run.step({})
        self.assertEqual(self.health(bot), after)

    def test_actual_flashbang_releases_pin_and_explicit_stop_prevents_repin(self):
        for stop in (False, True):
            with self.subTest(stop=stop):
                self.setUp()
                bot = self.actor()
                self.b.spawned = True
                self.assertTrue(self.run.match.switch_hero(self.b, heroes()[0x02E0000000000042]))
                self.b.mover.position[:] = (1, 0, -2)
                self.b.mover.body = None
                self.b.mover.history.clear()
                fight = self.run.match.combat
                fight.history.pop(self.b.body, None)
                self.start_pin(bot)
                direction = aim(fight._eye(self.b), (0, 1, 2.5))
                self.run.step({self.b: (*direction, 16)})
                for _ in range(50):
                    if fight.is_stunned(bot):
                        break
                    self.run.step({})
                self.assertTrue(fight.is_stunned(bot))
                self.assertNotIn(bot.entity, fight.pins)
                self.assertFalse(fight.is_stunned(self.a))
                if stop:
                    npc_control.stop(self.run.match)
                deadline = fight.stuns[bot.entity]
                while self.run.frame <= deadline:
                    self.run.step({})
                found = False
                for _ in range(100):
                    self.run.step({})
                    found |= bot.entity in fight.pins
                    if found:
                        break
                self.assertEqual(found, not stop)

    def test_actual_attacker_removal_releases_pin_and_world_references(self):
        bot = self.actor()
        self.start_pin(bot)
        world = bot.body_script.component.world
        self.assertEqual(npc.remove_spawned(self.run.match), (bot.entity,))
        self.assertFalse(self.run.match.combat.is_stunned(self.a))
        self.assertEqual(self.run.match.combat.pins, {})
        self.assertIsNone(world.match)
        self.assertIsNone(world.manual_target)

    def test_poison_killing_actual_attacker_releases_pin(self):
        bot = self.actor()
        self.a.body_script.ult_seconds = 0
        fight = self.run.match.combat
        direction = aim(fight._eye(self.a), (0, 0.7, 2.5))
        for _ in range(3):
            for _ in range(100):
                self.run.step({self.a: (*direction, 2)})
            self.run.step({self.a: (*direction, 3)})
        for _ in range(50):
            self.run.step({self.a: (*direction, 0)})
        for _ in range(300):
            self.run.step({self.a: (*direction, 1)})
            if fight.vitals[bot.entity].total <= 10:
                break
        for _ in range(20):
            self.run.step({self.a: (*direction, 0)})
        self.assertEqual(fight.vitals[bot.entity].total, 10)
        self.press(16)
        for _ in range(200):
            self.press(0)  # keep aim through the authored delayed mine volley
            if fight.venom.poison:
                break
        self.assertEqual(len(fight.venom.poison), 1)
        self.start_pin(bot)
        for _ in range(100):
            self.run.step({})
            if bot.dead:
                break
        self.assertTrue(bot.dead)
        self.assertTrue(bot in self.run.match.bots)
        self.assertGreater(self.health(self.a), 0)
        self.assertFalse(fight.is_stunned(self.a))
        self.assertNotIn(bot.entity, fight.pins)
        self.assertEqual(fight.vitals[bot.entity].killer, self.a.body)
