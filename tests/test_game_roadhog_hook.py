"""Real input/graph windup/cooldown, authoritative hook damage/control/movement.

Geometry and hook-engine phases are explicitly reconstructed, not client proof.
"""

import math
import unittest
from dataclasses import replace
from unittest.mock import patch

from test_game_combat import KANEZAKA_DM, Run

from ow174.game import combat
from ow174.game.collision import World
from ow174.game.commands import Command
from ow174.game.content import heroes
from ow174.game.script.driver import ULT_CHARGE, ULT_COST

ROADHOG = 0x02E0000000000040


class HookTests(unittest.TestCase):
    def session(self, distance=10.0, *, enemy_hog=False):
        run = Run(KANEZAKA_DM, ("Roadhog", "Enemy"))
        match = run.match
        match.bots.clear()
        match._pending_bot_scripts.clear()
        hog, enemy = run.players
        match.switch_hero(hog, heroes()[ROADHOG])
        if enemy_hog:
            match.switch_hero(enemy, heroes()[ROADHOG])
        x, y, z = hog.mover.position
        enemy.mover.position = [x, y, z + distance]
        fight = match.combat
        targets = {t.owner: t for t in fight._targets()}
        return run, hog, enemy, targets, fight.vitals_of(targets[enemy])

    def step(self, run, hog, enemy, *, shift=False, enemy_buttons=0, forward=0):
        run.frame += 1
        match = run.match
        match.update(run.frame * combat.TICK, run.frame)
        for player in run.players:
            player.client.queue_entities(match.remote_movements(player))
        hog.take_commands([Command(run.frame, buttons=8 if shift else 0)])
        enemy.take_commands([Command(run.frame, yaw=-32768, buttons=enemy_buttons, forward=forward)])

    def advance(self, run, hog, enemy, frames, **options):
        for _ in range(frames):
            self.step(run, hog, enemy, **options)

    def wall(self, hog, distance=4):
        x, y, z = hog.mover.position
        return World([
            (x - 5, y, z + distance, x + 5, y, z + distance, x + 5, y + 5, z + distance),
            (x - 5, y, z + distance, x + 5, y + 5, z + distance, x - 5, y + 5, z + distance),
        ])

    def test_windup_one_damage_pull_and_control_recovery(self):
        run, hog, enemy, _, vitals = self.session()
        self.step(run, hog, enemy, shift=True)
        self.advance(run, hog, enemy, 8)
        self.assertEqual(vitals.total, 200)
        hooks = run.match.combat.hooks
        for _ in range(40):
            self.step(run, hog, enemy)
            if hooks.controlled(enemy.body):
                break
        self.assertTrue(hooks.controlled(enemy.body))
        self.assertEqual(vitals.total, 170)
        own = run.match.combat.vitals_of(next(t for t in run.match.combat._targets() if t.owner is hog))
        before = own.total
        self.advance(run, hog, enemy, 10, enemy_buttons=1, forward=-127)
        self.assertEqual(own.total, before)  # Forced victim input cannot shoot the hook owner.
        self.assertLess(math.dist(enemy.mover.position, hog.mover.position), 10)
        self.advance(run, hog, enemy, 60)
        self.assertFalse(hooks.controlled(enemy.body))
        self.assertAlmostEqual(math.dist(enemy.mover.position, hog.mover.position), 3, places=2)
        self.assertEqual(vitals.total, 170)
        self.assertEqual(len(hog.client.messages(combat.HIT)), 1)

    def test_miss_ally_and_wall_leave_target_untouched(self):
        for mode in ("miss", "ally", "wall"):
            with self.subTest(mode=mode):
                run, hog, enemy, _, vitals = self.session()
                if mode == "miss":
                    enemy.mover.position[0] += 8
                if mode == "ally":
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    enemy.team = hog.team
                start = tuple(enemy.mover.position)
                fight = run.match.combat
                collision = self.wall(hog) if mode == "wall" else None
                with patch.object(fight, "_collision", return_value=collision):
                    self.step(run, hog, enemy, shift=True)
                    self.advance(run, hog, enemy, 100)
                self.assertEqual(vitals.total, 200)
                self.assertEqual(tuple(enemy.mover.position), start)
                self.assertEqual(hog.client.messages(combat.HIT), [])
                self.assertEqual(fight.hooks.flights, {})

    def test_exact_sweep_front_range_boundary(self):
        for delta in (-0.01, 0.01):
            with self.subTest(delta=delta):
                run, hog, enemy, targets, vitals = self.session()
                # The authored20m limit applies to the sweep front, not feet distance.
                enemy.mover.position[2] = hog.mover.position[2] + 20 + targets[enemy].radius + 0.5 + delta
                self.step(run, hog, enemy, shift=True)
                self.advance(run, hog, enemy, 100)
                self.assertEqual(vitals.total, 170 if delta < 0 else 200)

    def test_cooldown_repress_rejection_then_reuse(self):
        run, hog, enemy, _, vitals = self.session()
        self.step(run, hog, enemy, shift=True)
        self.advance(run, hog, enemy, 100)
        self.assertEqual(vitals.total, 170)
        instance = next(i for i in hog.body_script.component.instances.values() if i.graph.index == 0x01C8)
        ability = next(s for s in instance.states.values() if s.node.pos == 11)
        self.assertEqual(instance.find_var(0, 61).base, 8.0)
        self.assertGreater(ability.remaining(), 0)
        for _ in range(4):
            self.step(run, hog, enemy, shift=True)
            self.step(run, hog, enemy)
        self.advance(run, hog, enemy, 40)
        self.assertEqual(vitals.total, 170)
        self.advance(run, hog, enemy, 450)
        self.assertEqual(ability.remaining(), 0)
        self.step(run, hog, enemy, shift=True)
        self.advance(run, hog, enemy, 100)
        self.assertEqual(vitals.total, 140)

    def test_owner_death_releases_caught_target(self):
        run, hog, enemy, targets, vitals = self.session()
        self.step(run, hog, enemy, shift=True)
        hooks = run.match.combat.hooks
        for _ in range(50):
            self.step(run, hog, enemy)
            if hooks.controlled(enemy.body):
                break
        self.assertTrue(hooks.controlled(enemy.body))
        run.match.combat._damage(enemy, targets[hog], 2000, False, None, run.frame)
        self.step(run, hog, enemy)
        self.assertFalse(hooks.controlled(enemy.body))
        self.assertEqual(vitals.total, 170)

    def test_target_death_and_owner_switch_release_the_control(self):
        for mode in ("target_death", "owner_switch"):
            with self.subTest(mode=mode):
                run, hog, enemy, targets, vitals = self.session()
                self.step(run, hog, enemy, shift=True)
                hooks = run.match.combat.hooks
                for _ in range(50):
                    self.step(run, hog, enemy)
                    if hooks.controlled(enemy.body):
                        break
                self.assertTrue(hooks.controlled(enemy.body))
                if mode == "target_death":
                    run.match.combat._damage(hog, targets[enemy], 2000, False, None, run.frame)
                    self.step(run, hog, enemy)
                    self.assertTrue(vitals.dead)
                    self.assertEqual(vitals.total, 0)
                else:
                    run.match.switch_hero(hog, heroes()[0x02E000000000006E])
                self.assertFalse(hooks.controlled(enemy.body))
                self.assertEqual(hooks.flights, {})

    def test_new_wall_between_hook_owner_and_target_breaks_the_link(self):
        run, hog, enemy, _, vitals = self.session()
        self.step(run, hog, enemy, shift=True)
        hooks = run.match.combat.hooks
        for _ in range(50):
            self.step(run, hog, enemy)
            if hooks.controlled(enemy.body):
                break
        self.assertTrue(hooks.controlled(enemy.body))
        position = tuple(enemy.mover.position)
        with patch.object(run.match.combat, "_collision", return_value=self.wall(hog)):
            self.step(run, hog, enemy)
        self.assertFalse(hooks.controlled(enemy.body))
        self.assertEqual(tuple(enemy.mover.position), position)
        self.assertEqual(vitals.total, 170)

    def test_hook_interrupts_real_enemy_breather_healing(self):
        run, hog, enemy, _, vitals = self.session(enemy_hog=True)
        vitals.damage(400)
        run.match.combat._sync_body_health(enemy, vitals)
        self.step(run, hog, enemy, enemy_buttons=16)
        self.advance(run, hog, enemy, 20)
        self.step(run, hog, enemy, shift=True)
        hooks = run.match.combat.hooks
        for _ in range(45):
            self.step(run, hog, enemy)
            if hooks.controlled(enemy.body):
                break
        self.assertTrue(hooks.controlled(enemy.body))
        stopped = vitals.total
        self.advance(run, hog, enemy, 80)
        self.assertEqual(vitals.total, stopped)
        instance = next(i for i in enemy.body_script.component.instances.values() if i.graph.index == 0x0240)
        ability = next(s for s in instance.states.values() if s.node.pos == 1)
        self.assertFalse(ability.flags & 1)
        self.assertGreater(ability.remaining(), 0)

    def test_hook_interrupts_real_enemy_whole_hog(self):
        run, hog, enemy, _, _ = self.session(enemy_hog=True)
        component = enemy.body_script.component
        component.set(ULT_CHARGE, component.entity_var(ULT_COST).value())
        self.step(run, hog, enemy, enemy_buttons=32)
        ultimate, = enemy.body_script.ultimates
        self.assertTrue(ultimate.flags & 1)
        self.step(run, hog, enemy, shift=True)
        hooks = run.match.combat.hooks
        for _ in range(45):
            self.step(run, hog, enemy)
            if hooks.controlled(enemy.body):
                break
        self.assertTrue(hooks.controlled(enemy.body))
        self.assertFalse(ultimate.flags & 1)
        own = run.match.combat.vitals_of(next(t for t in run.match.combat._targets() if t.owner is hog))
        before = own.total
        self.advance(run, hog, enemy, 60)
        self.assertEqual(own.total, before)


if __name__ == "__main__":
    unittest.main()
