"""Actual Ana Sleep Dart and damage-driven wake behavior against Tracer."""

import math
import unittest
from dataclasses import replace

import test_game_tracer_body_protection as fixtures
import test_game_tracer_pulse_deflect as support_tests
from test_game_combat import aim, in_world
from test_game_mover import box

from ow174.game import combat, deflection, stats, tracer
from ow174.game.collision import World
from ow174.game.commands import FIRE, Command
from ow174.game.content import SOLDIER, find_hero, heroes
from ow174.game.script.driver import ULT_CHARGE, ULT_COST


class TracerAnaSleepTests(unittest.TestCase):
    def setUp(self):
        self.case = fixtures.TracerBodyProtectionTests()
        self.case.arena(0x02E000000000013B, behind=False, distance=6)
        self.run, self.fight = self.case.run, self.case.fight
        self.player, self.ana = self.case.tracer, self.case.enemy
        self.health = self.case.health(self.player)
        x, y, z = self.player.mover.position
        self.angles = aim(self.fight._eye(self.ana), (x, y + 1.1, z))

    def step(self, *, ana=0, buttons=0, forward=0, yaw=0):
        self.run.frame += 1
        frame = self.run.frame
        self.run.match.update(frame * 0.016, frame)
        self.ana.take_commands([Command(frame, yaw=self.angles[0], pitch=self.angles[1], buttons=ana)])
        self.player.take_commands([Command(frame, buttons=buttons, forward=forward, yaw=yaw)])

    def launch(self):
        self.step(ana=8)
        for _ in range(45):
            if self.fight.rockets:
                rocket, = self.fight.rockets
                self.assertEqual(rocket.definition, combat.SLEEP_DART)
                self.assertEqual(rocket.speed, 60)
                return
            self.step()
        self.fail("actual Ana input did not launch Sleep Dart")

    def contact(self):
        before = self.health.total
        self.launch()
        for _ in range(24):
            if self.fight.is_stunned(self.player):
                break
            self.step()
        self.assertEqual(self.health.total, before - 5)
        self.assertTrue(self.fight.is_stunned(self.player))
        self.assertEqual(self.health.sleep_until - self.fight.tick, combat.SLEEP_TICKS)
        self.assertEqual(self.health.sleep_wake_after - self.fight.tick, combat.SLEEP_WAKE_TICKS)

    def test_actual_sleep_blocks_tracer_until_exclusive_deadline_then_allows_blink(self):
        self.contact()
        script = self.player.body_script
        script.ult_seconds = 0
        cost = script.component.entity_var(338).value()
        script.set_ult_charge(cost)
        blink = next(i for i in script.component.instances.values() if i.graph.index == 0x24)
        before = tuple(self.player.mover.position)
        deadline = self.health.sleep_until
        for _ in range(4):
            self.step(buttons=1 | 8 | 16 | 32 | 0x800, forward=127)
            self.assertEqual(tuple(self.player.mover.position), before)
            self.assertFalse(script.component.held)
        self.assertEqual(blink.vars[11].value(), 3)
        self.assertEqual(script.component.entity_var(342).value(), cost)
        self.assertFalse(self.fight.pulse_bombs.bombs)
        self.assertFalse(tracer.recalling(self.player))
        while self.fight.tick + 1 < deadline:
            self.step()
        self.assertTrue(self.fight.is_stunned(self.player))
        self.step(buttons=8)
        self.assertFalse(self.fight.is_stunned(self.player))
        self.assertEqual(blink.vars[11].value(), 2)
        self.assertGreater(self.player.mover.position[2] - before[2], 5)

    def test_actual_damage_ignores_early_wake_then_shortens_to_half_second(self):
        soldier, _ = support_tests.PulseDeflectTests().support(self.run, self.player)
        x, y, z = self.player.mover.position
        soldier.mover.position[:] = (x + 2, y, z)
        soldier.mover.body = None
        self.contact()
        angles = aim(self.fight._eye(soldier), (x, y + 0.9, z))
        full = self.health.sleep_until
        self.run.step({soldier: (*angles, 1)})
        self.assertLess(self.health.total, 145)
        self.assertEqual(self.health.sleep_until, full)
        while self.fight.tick < self.health.sleep_wake_after:
            self.run.step({})
        before = self.health.total
        self.run.step({soldier: (*angles, 1)})
        self.assertLess(self.health.total, before)
        self.assertEqual(self.health.sleep_until, self.fight.tick + combat.SLEEP_WAKE_TICKS)
        self.assertTrue(self.fight.is_stunned(self.player))
        while self.fight.tick < self.health.sleep_until:
            self.run.step({})
        self.assertFalse(self.fight.is_stunned(self.player))

    def test_tracer_attacks_follow_up_allied_dart_against_player(self):
        for attack, button, amount in (("primary", FIRE, 12), ("melee", 0x800, 30), ("pulse", 32, 5)):
            for lethal in (False, True):
                with self.subTest(attack=attack, lethal=lethal):
                    self.setUp()
                    self.check_tracer_attack_against_sleeping_soldier(attack, button, amount, lethal)

    def sleeping_soldier_scene(self):
        match = self.run.match
        match.game_map = replace(match.game_map, free_for_all=False)
        self.ana.team = self.player.team
        soldier, _ = support_tests.PulseDeflectTests().support(self.run, self.player)
        soldier.team = 1 - self.player.team
        self.player.body_script.ult_seconds = 0
        return soldier

    def sleep_soldier(self, soldier, health):
        x, y, z = soldier.mover.position
        self.ana.mover.position[:] = (x + 4, y, z)
        self.ana.mover.body = None
        angles = aim(self.fight._eye(self.ana), (x, y + 1, z))
        before = health.total
        self.run.step({self.ana: (*angles, 8)})
        for _ in range(50):
            if health.sleep_until:
                break
            self.run.step({self.ana: (*angles, 0)})
        self.assertEqual(health.total, before - 5)
        self.assertEqual(health.sleep_until, self.fight.tick + combat.SLEEP_TICKS)
        return health.sleep_until

    def check_tracer_attack_against_sleeping_soldier(self, attack, button, amount, lethal):
        soldier = self.sleeping_soldier_scene()
        x, y, z = self.player.mover.position
        soldier.mover.position[:] = (x, y, z + 1.5)
        soldier.mover.body = None
        health = self.case.health(soldier)
        if lethal:
            health.damage(200 - (5 + 12 + amount - 1))
            self.fight._sync_body_health(soldier, health)
        deadline = self.sleep_soldier(soldier, health)
        fire = aim(self.fight._eye(self.player), (x, y + 1, z + 1.5))
        reply = {soldier: (*aim(self.fight._eye(soldier), (x, y + 1, z)), FIRE)}
        script = self.player.body_script
        charge = script.component.entity_var(ULT_CHARGE)
        before = health.total
        self.run.step({self.player: (*fire, FIRE), **reply})
        self.assertEqual(health.total, before - 12)
        self.assertEqual(health.sleep_until, deadline)
        self.assertFalse(soldier.client.messages(combat.HIT))
        self.assertEqual(charge.value(), 12)
        while self.run.frame + 1 < health.sleep_wake_after:
            self.run.step({})

        if attack == "pulse":
            script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        self.run.step({self.player: (*fire, button)})
        bombs = self.fight.pulse_bombs.bombs
        if attack == "pulse":
            for _ in range(45):
                if bombs and bombs[0].attached == soldier.body:
                    break
                self.assertTrue(self.fight.is_stunned(soldier))
                self.run.step({})
            bomb, = bombs
            self.assertEqual(bomb.attached, soldier.body)
        damage = amount - int(lethal)
        self.assertEqual(health.total, before - 12 - damage)
        self.assertEqual(charge.value(), 0 if attack == "pulse" else 12 + damage)
        self.assertEqual(health.dead, lethal)
        if lethal:
            self.assertEqual((health.sleep_until, health.sleep_wake_after), (0, 0))
        else:
            shortened = self.fight.tick + combat.SLEEP_WAKE_TICKS
            self.assertEqual(health.sleep_until, shortened)
            self.assertLess(shortened, deadline)
            self.check_soldier_rifle_at_wake(soldier, reply, shortened)

        if attack == "pulse":
            self.run.step({self.player: (16384, 0, 8)})
            for _ in range(75):
                if not bombs:
                    break
                self.run.step({})
            self.assertFalse(bombs)
            self.assertTrue(health.dead)
            self.assertEqual(charge.value(), 0)
        self.assertEqual(health.killer, self.player.body if health.dead else None)
        self.assertEqual(len(soldier.client.messages(combat.DEATH_NOTICE)), int(health.dead))
        self.assertEqual(self.player.stats.value(stats.ELIMINATIONS), int(health.dead))

    def check_soldier_rifle_at_wake(self, soldier, reply, deadline):
        while self.run.frame + 1 < deadline - 1:
            self.run.step({})
            self.assertEqual(self.health.total, 150)
        self.run.step(reply)
        self.assertTrue(self.fight.is_stunned(soldier))
        self.assertEqual(self.health.total, 150)
        self.run.step(reply)
        self.assertEqual(self.fight.tick, deadline)
        self.assertFalse(self.fight.is_stunned(soldier))
        self.assertEqual(self.health.total, 130)

    def test_pulse_blast_obeys_sleep_wake_grace_on_player(self):
        for case in ("grace", "wake", "lethal"):
            with self.subTest(case=case):
                self.setUp()
                self.check_pulse_blast_against_sleeping_soldier(case)

    def check_pulse_blast_against_sleeping_soldier(self, case):
        soldier = self.sleeping_soldier_scene()
        match = self.run.match
        anchor = match.add_player(4, "Anchor", SOLDIER, soldier.team, False)
        in_world(match, anchor)
        self.assertTrue(match.switch_hero(anchor, heroes()[SOLDIER]))
        self.run.players.append(anchor)
        x, floor, z = self.player.mover.position
        soldier.mover.position[:] = (x + 12, floor, z)
        soldier.mover.body = None
        anchor.mover.position[:] = (x, floor, z + 5)
        anchor.mover.body = None
        bomb = self.case.launch_pulse()
        bombs = self.fight.pulse_bombs.bombs
        for _ in range(55):
            if bomb.stuck:
                break
            self.run.step({})
        self.assertEqual(bomb.attached, anchor.body)
        self.assertEqual(self.case.health(anchor).total, 195)
        x, y, z = bomb.position
        target = next(t for t in self.fight._targets() if t.owner is soldier)
        dy = max(floor + target.radius - y, y - (floor + target.height - target.radius), 0)
        lateral = math.sqrt((3.875 + target.radius) ** 2 - dy ** 2)
        soldier.mover.position[:] = (x + lateral, floor, z)
        soldier.mover.body = None
        health = self.case.health(soldier)
        if case == "lethal":
            health.damage(140)
            self.fight._sync_body_health(soldier, health)
        if case == "grace":
            for _ in range(45):
                if bomb.fuse_left <= 0.55:
                    break
                self.run.step({})
        deadline = self.sleep_soldier(soldier, health)
        wake_after = health.sleep_wake_after
        before = health.total
        self.assertIn(bomb, bombs)
        self.player.mover.position[:] = (x + lateral + 1.75, floor, z)
        self.player.mover.body = None
        while bomb in bombs and self.run.frame < deadline:
            self.assertEqual(health.total, before)
            self.assertEqual(health.sleep_until, deadline)
            self.run.step({})
        self.assertNotIn(bomb, bombs)
        self.assertLess(self.fight.tick, deadline)
        self.assertEqual(self.fight.tick < wake_after, case == "grace")
        hits = [n["+0x78"] for n in self.player.client.messages(combat.HIT)
                if n["+0x78"]["+0x0"]["+0x0"] == soldier.body]
        hit, = hits
        lethal = case == "lethal"
        self.assertEqual(hit["+0x8"], combat.KILLING if lethal else 0)
        self.assertEqual(health.dead, lethal)
        if lethal:
            self.assertEqual(health.killer, self.player.body)
            self.assertEqual((health.sleep_until, health.sleep_wake_after), (0, 0))
        else:
            self.assertAlmostEqual(before - health.total, 87.5, places=5)
            expected = deadline if case == "grace" else self.fight.tick + combat.SLEEP_WAKE_TICKS
            self.assertEqual(health.sleep_until, expected)
            px, py, pz = self.player.mover.position
            reply = {soldier: (*aim(self.fight._eye(soldier), (px, py + 1, pz)), FIRE)}
            self.check_soldier_rifle_at_wake(soldier, reply, expected)
        self.assertEqual(len(soldier.client.messages(combat.DEATH_NOTICE)), int(lethal))
        self.assertEqual(self.player.body_script.component.entity_var(ULT_CHARGE).value(), 0)

    def test_actual_sleep_interrupts_pulse_windup(self):
        self.launch()
        script = self.player.body_script
        script.ult_seconds = 0
        cost = script.component.entity_var(338).value()
        script.set_ult_charge(cost)
        self.step(buttons=32)
        self.assertEqual(script.component.entity_var(342).value(), 0)
        for _ in range(32):
            self.step()
            self.assertFalse(self.fight.pulse_bombs.bombs)
        self.assertTrue(self.fight.is_stunned(self.player))
        self.assertEqual(self.health.total, 145)
        self.assertEqual(script.component.entity_var(342).value(), 0)

        while self.fight.tick < self.health.sleep_until:
            self.step()
            self.assertFalse(self.fight.pulse_bombs.bombs)
        self.assertFalse(self.fight.is_stunned(self.player))
        bomb = self.case.launch_pulse()
        self.assertEqual(script.component.entity_var(342).value(), 0)
        for _ in range(120):
            self.step()
        self.assertNotIn(bomb, self.fight.pulse_bombs.bombs)
        self.assertTrue(self.case.vitals.dead)

    def test_actual_sleep_leaves_an_already_released_pulse_live(self):
        self.player.body_script.ult_seconds = 0
        bomb = self.case.launch_pulse()
        self.contact()
        self.assertIn(bomb, self.fight.pulse_bombs.bombs)
        for _ in range(24):
            if bomb.stuck:
                break
            self.step()
        self.assertEqual(bomb.attached, self.ana.body)
        self.assertEqual(self.case.vitals.total, self.case.original_health - 5)
        for _ in range(80):
            self.step()
        self.assertNotIn(bomb, self.fight.pulse_bombs.bombs)
        self.assertTrue(self.case.vitals.dead)
        self.assertTrue(self.fight.is_stunned(self.player))

    def defense(self, hero, button):
        self.run.match.game_map = replace(self.run.match.game_map, free_for_all=False)
        self.player.team, self.ana.team = 0, 1
        x, y, z = self.player.mover.position
        self.ana.mover.position[:] = (x, y, z + 12)
        self.ana.mover.body = None
        defender, _ = support_tests.PulseDeflectTests().support(self.run, self.player, hero=hero)
        if hero == 0x02E000000000007A:
            mech = replace(find_hero("dva"), body=0x0400000000000407)
            self.assertTrue(self.run.match.switch_hero(defender, mech))
        defender.team = self.player.team
        defender.mover.position[:] = (x, y, z + 3)
        defender.mover.body = None
        for _ in range(110 if hero == 0x02E000000000007A else 25):
            self.run.step({})
        angles = aim(self.fight._eye(self.ana), (x, y + 1.1, z))
        facing = aim(self.fight._eye(defender), (x, y + 1.1, z + 12))
        self.run.step({defender: (*facing, button)})
        seen = {}
        for frame in range(64):
            self.run.step({self.ana: (*angles, 8 if frame == 0 else 0), defender: (*facing, button)})
            for rocket in self.fight.rockets:
                seen[id(rocket)] = rocket
        rocket, = seen.values()
        self.assertEqual(rocket.definition, combat.SLEEP_DART)
        self.assertEqual(self.health.total, 150)
        self.assertEqual(self.health.sleep_until, 0)
        return defender, rocket

    def test_actual_winston_barrier_stops_dart_and_takes_five_damage(self):
        defender, _ = self.defense(0x02E0000000000009, 16)
        bubble = self.fight.winston_barriers.actors[defender.body]
        self.assertEqual(bubble.health, 695)
        self.assertEqual(self.case.health(defender).sleep_until, 0)

    @unittest.skipUnless(deflection.TAGS_PATH, "requires verified projectile tags")
    def test_actual_matrix_absorbs_sleep_dart(self):
        defender, _ = self.defense(0x02E000000000007A, 2)
        self.assertEqual(self.case.health(defender).total, 600)
        self.assertEqual(self.case.health(defender).sleep_until, 0)

    @unittest.skipUnless(deflection.TAGS_PATH, "requires verified projectile tags")
    def test_actual_deflect_sleeps_ana_instead_of_tracer(self):
        defender, rocket = self.defense(0x02E0000000000029, 16)
        self.assertIs(rocket.shooter, defender)
        self.assertEqual(self.case.health(self.ana).total, 195)
        self.assertTrue(self.fight.is_stunned(self.ana))
        self.assertEqual(self.case.health(defender).sleep_until, 0)

    def test_actual_early_recast_is_rejected_and_later_recast_applies_sleep(self):
        self.contact()
        first = self.fight.tick
        self.step(ana=8)
        for _ in range(50):
            self.step()
            self.assertFalse(self.fight.rockets)
        while self.fight.tick < first + 820:
            self.step()
        self.assertFalse(self.fight.is_stunned(self.player))
        self.contact()
        self.assertEqual(self.health.total, 140)

    def test_actual_lateral_blink_avoids_the_in_flight_dart(self):
        self.launch()
        x = self.player.mover.position[0]
        self.step(buttons=8, yaw=16384)
        self.assertGreater(self.player.mover.position[0] - x, 5)
        for _ in range(16):
            self.step()
        self.assertEqual(self.health.total, 150)
        self.assertEqual(self.health.sleep_until, 0)

    def test_actual_static_cover_stops_sleep_dart(self):
        x, y, z = self.player.mover.position
        floor = [(-1000, y, -1000, -1000, y, 1000, 1000, y, 1000),
                 (-1000, y, -1000, 1000, y, 1000, 1000, y, -1000)]
        self.run.match.collision_world = World(floor + box(x - 3, x + 3, y, y + 4, z + 3, z + 3.2))
        self.launch()
        for _ in range(16):
            self.step()
        self.assertFalse(self.fight.rockets)
        self.assertEqual(self.health.total, 150)
        self.assertEqual(self.health.sleep_until, 0)

    def test_actual_allied_dart_rejects_damage_and_control(self):
        self.run.match.game_map = replace(self.run.match.game_map, free_for_all=False)
        self.ana.team = self.player.team
        self.launch()
        for _ in range(20):
            self.step()
        self.assertEqual(self.health.total, 150)
        self.assertEqual(self.health.sleep_until, 0)

    def test_actual_lethal_dart_leaves_no_sleep_on_corpse(self):
        self.health.damage(146)
        self.launch()
        for _ in range(24):
            self.step()
            if self.health.dead:
                break
        self.assertTrue(self.health.dead)
        self.assertEqual(self.health.sleep_until, 0)
        self.assertFalse(self.fight.is_stunned(self.player))

    def test_body_replacement_clears_sleep_control(self):
        self.contact()
        old = self.player.body
        self.assertTrue(self.run.match.switch_hero(self.player, heroes()[SOLDIER]))
        self.assertNotEqual(self.player.body, old)
        self.assertFalse(self.fight.is_stunned(self.player))

    def test_actual_recall_rejects_sleep_dart(self):
        self.launch()
        self.step(buttons=16)
        self.assertTrue(tracer.recalling(self.player))
        for _ in range(15):
            self.step()
        self.assertTrue(tracer.recalling(self.player))
        self.assertEqual(self.health.total, 150)
        self.assertEqual(self.health.sleep_until, 0)


if __name__ == "__main__":
    unittest.main()
