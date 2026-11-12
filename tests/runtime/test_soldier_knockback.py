"""Resource-backed Helix impulse consumers with actual displacement.

These are reconstructed host physics, not native resistance/filter acceptance.
"""

import unittest

import test_soldier_helix_self as self_fixture
from test_game_combat import Run, aim

from ow174.game import combat, defenses
from ow174.game.collision import flat_world
from ow174.game.commands import Command
from ow174.game.content import heroes


class SoldierKnockbackTests(unittest.TestCase):
    scene = self_fixture.SoldierHelixSelfTests.scene
    target = self_fixture.SoldierHelixSelfTests.target
    pool = self_fixture.SoldierHelixSelfTests.pool
    launch = self_fixture.SoldierHelixSelfTests.launch
    land = self_fixture.SoldierHelixSelfTests.land

    def test_real_wall_splash_pushes_enemy_and_self_then_lands_and_stops(self):
        run, soldier, enemy = self.scene()
        enemy.mover.position[:] = [1.1, 0, .2]
        enemy.mover.body = None
        start = tuple(enemy.mover.position)
        rocket = self.launch(run, soldier)
        self.assertEqual((rocket.blast.horizontal_impulse, rocket.blast.vertical_impulse), (4, 6))
        self.land(run, rocket)
        for _ in range(20):
            run.step({})
        self.assertGreater(enemy.mover.position[0], start[0] + .3)
        self.assertLess(enemy.mover.position[2], start[2])
        self.assertGreater(enemy.mover.position[1], .2)
        self.assertLess(soldier.mover.position[2], -.3)
        self.assertGreater(soldier.mover.position[1], .2)
        for _ in range(200):
            run.step({})
        for actor in (soldier, enemy):
            self.assertFalse(actor.mover.airborne)
            self.assertAlmostEqual(actor.mover.position[1], 0, delta=.05)
            self.assertLess(sum(abs(v) for v in actor.mover.velocity), .01)

    def test_ally_and_static_wall_exclude_impulse(self):
        for case in ("ally", "wall"):
            with self.subTest(case=case):
                run, soldier, other = self.scene()
                other.mover.position[:] = [1.1, 0, .2 if case == "ally" else 1.2]
                other.mover.body = None
                if case == "ally":
                    other.team = soldier.team
                run.step({})  # settle the fixture pose on the mover's coordinate grid
                start = tuple(other.mover.position)
                self.land(run, self.launch(run, soldier))
                for _ in range(30):
                    run.step({})
                self.assertEqual(tuple(other.mover.position), start)
                self.assertEqual(self.pool(run, other).total, 200)

    def test_authored_force_multiplier_zero_or_double_changes_motion_without_changing_damage(self):
        for scale in (0, 2):
            with self.subTest(scale=scale):
                run, soldier, enemy = self.scene()
                enemy.mover.position[:] = [1.1, 0, .2]
                enemy.mover.body = None
                run.step({})
                start = tuple(enemy.mover.position)
                soldier.body_script.component.set(10626, scale)
                rocket = self.launch(run, soldier)
                self.assertEqual((rocket.blast.horizontal_impulse, rocket.blast.vertical_impulse),
                                 (4 * scale, 6 * scale))
                self.land(run, rocket)
                for _ in range(20):
                    run.step({})
                self.assertEqual(self.pool(run, enemy).total, 120)
                if scale == 0:
                    self.assertEqual(tuple(enemy.mover.position), start)
                else:
                    self.assertGreater(enemy.mover.position[0], start[0] + 1)
                    self.assertGreater(enemy.mover.position[1], start[1] + 1)

    def test_live_fortify_reduces_damage_and_rejects_scoped_impulse(self):
        run, soldier, other = self.scene()
        run.match.switch_hero(other, heroes()[defenses.ORISA])
        other.mover.position[:] = [1.3, 0, -.2]
        other.mover.body = None
        run.step({other: (0, 0, 8)})
        self.assertAlmostEqual(defenses.fortify_scale(other), .6)
        start, health = tuple(other.mover.position), self.pool(run, other).total
        self.land(run, self.launch(run, soldier))
        for _ in range(25):
            run.step({})
        self.assertEqual(tuple(other.mover.position), start)
        self.assertAlmostEqual(health - self.pool(run, other).total, 43, places=4)

    def test_actual_helix_force_moves_a_stunned_player_while_input_stays_suppressed(self):
        run = Run(names=("Soldier", "Victim", "McCree"))
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        run.match.collision_world = flat_world(0)
        soldier, victim, caster = run.players
        run.match.switch_hero(caster, heroes()[0x02E0000000000042])
        for player, position in zip(run.players, ((0, 0, -5), (0, 0, 3), (4, 0, 3)), strict=True):
            player.mover.position[:] = position
            player.mover.body = None
            player.body_script.ult_seconds = 0
        victim.team = 1 - soldier.team
        caster.team = soldier.team
        for _ in range(20):
            run.step({})
        angles = aim(run.match.combat._eye(caster), (0, 1, 3))
        for frame in range(40):
            run.step({caster: (*angles, 16 if frame == 0 else 0)})
            if run.match.combat.is_stunned(victim):
                break
        self.assertTrue(run.match.combat.is_stunned(victim))
        start = tuple(victim.mover.position)
        run.step({soldier: (0, 0, 2), victim: (0, 0, 0)})
        for _ in range(25):
            run.frame += 1
            run.match.update(run.frame * combat.TICK, run.frame)
            for player in run.players:
                player.take_commands([Command(run.frame, forward=127 if player is victim else 0)])
        self.assertTrue(run.match.combat.is_stunned(victim))
        self.assertFalse(victim.body_script.component.held)
        self.assertGreater(victim.mover.position[2], start[2] + .3)
        self.assertGreater(victim.mover.position[1], start[1] + .2)

    def test_transcendence_rejects_hp_damage_but_retains_solid_body_impulse(self):
        run, soldier, other = self.scene()
        run.match.switch_hero(other, heroes()[defenses.ZENYATTA])
        other.mover.position[:] = [1.1, 0, .2]
        other.mover.body = None
        script = other.body_script
        script.ult_seconds = 0
        run.step({})
        script.set_ult_charge(script.component.entity_var(338).value())
        run.step({other: (0, 0, 32)})
        self.assertTrue(defenses.invulnerable(other))
        start, health = tuple(other.mover.position), self.pool(run, other).total
        self.land(run, self.launch(run, soldier))
        for _ in range(20):
            run.step({})
        self.assertTrue(defenses.invulnerable(other))
        self.assertEqual(self.pool(run, other).total, health)
        self.assertGreater(other.mover.position[0], start[0] + .3)
        self.assertGreater(other.mover.position[1], start[1] + .2)
