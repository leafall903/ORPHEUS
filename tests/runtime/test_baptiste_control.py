"""Actual enemy Flashbang input against Baptiste's reconstructed ability paths."""

import unittest
from unittest.mock import patch

import test_baptiste_combat as fixture
from test_game_combat import aim

from ow174.game.collision import World
from ow174.game.commands import Command
from ow174.game.content import heroes
from ow174.game.script.driver import ULT_CHARGE, ULT_COST


class BaptisteControlTests(unittest.TestCase):
    def test_static_wall_blocks_actual_flashbang_damage_and_control(self):
        run, player, enemy, fight, _ = self.arena()
        run.match.collision_world = World([
            (-100, 1, -100, -100, 1, 100, 100, 1, 100),
            (-100, 1, -100, 100, 1, 100, 100, 1, -100),
            (-10, -10, 1.5, 10, -10, 1.5, 0, 10, 1.5),
        ])
        with patch.object(fight.projectiles, "fired", return_value=None):
            for frame in range(80):
                self.step(run, player, enemy, cast=frame == 0)
                self.assertFalse(fight.is_stunned(player))
        self.assertEqual(fight.vitals[player.body].total, 200)

    def arena(self):
        args = fixture.BaptisteCombatTests().setup_fight()
        run, player, ally, enemy, fight, targets, _ = args
        run.match.switch_hero(enemy, heroes()[0x02E0000000000042])
        enemy.mover.position[:] = (0.0, 1.0, 3.0)
        ally.mover.position[:] = (20.0, 1.0, 4.0)
        for _ in range(20):
            run.step({})
        return run, player, enemy, fight, targets

    def step(self, run, player, enemy, *, cast=False, buttons=0, forward=0):
        yaw, pitch = aim(run.match.combat._eye(enemy), run.match.combat._eye(player))
        run.frame += 1
        run.match.update(run.frame * 0.016, run.frame)
        for actor in run.players:
            actor.client.queue_entities(run.match.remote_movements(actor))
            command = (Command(run.frame, yaw=yaw, pitch=pitch, buttons=16 if cast else 0)
                       if actor is enemy else Command(run.frame, buttons=buttons if actor is player else 0,
                                                     forward=forward if actor is player else 0))
            actor.take_commands([command])

    def flash(self, run, player, enemy, *, buttons=0):
        with patch.object(run.match.combat.projectiles, "fired", return_value=None):
            self.step(run, player, enemy, cast=True, buttons=buttons)
            for _ in range(50):
                if run.match.combat.is_stunned(player):
                    return
                self.step(run, player, enemy, buttons=buttons)
        self.fail("Actual enemy Flashbang did not reach Baptiste")

    def test_stun_stops_primary_and_movement_then_input_recovers(self):
        run, player, enemy, fight, _ = self.arena()
        self.flash(run, player, enemy)
        end = fight.stuns[player.body]
        position = tuple(player.mover.position)
        self.assertAlmostEqual(fight.vitals[player.body].total, 175)
        for _ in range(5):
            self.step(run, player, enemy, buttons=1, forward=127)
        self.assertEqual(tuple(player.mover.position), position)
        enemy_target = next(t for t in fight._targets() if t.owner is enemy)
        before = fight.vitals_of(enemy_target).total
        while run.frame < end - 1:
            self.step(run, player, enemy, buttons=1)
        self.assertEqual(fight.vitals_of(enemy_target).total, before)
        self.assertFalse(player.body_script.component.held)
        # Face enemy after exclusive deadline; actual primary reports resume.
        for _ in range(20):
            self.step(run, player, enemy, buttons=1)
        self.assertLess(fight.vitals_of(enemy_target).total, before)

    def test_matrix_preview_interrupt_keeps_charge_and_can_reopen_after_stun(self):
        run, player, enemy, fight, _ = self.arena()
        component = player.body_script.component
        component.set(ULT_CHARGE, component.vars[ULT_COST].value())
        run.step({player: (0, 8192, 32)})
        matrix = next(i for i in component.instances.values() if i.graph.index == 0x1902)
        self.assertTrue(matrix.state(9).active)
        self.flash(run, player, enemy)
        self.assertFalse(matrix.state(9).active)
        self.assertEqual(component.vars[ULT_CHARGE].value(), 2310)
        end = fight.stuns[player.body]
        while run.frame <= end:
            run.step({})
        run.step({player: (0, 8192, 32)})
        run.step({player: (0, 8192, 0)})
        run.step({player: (0, 8192, 1)})
        self.assertEqual(len(fight.baptiste.matrices), 1)
        self.assertEqual(component.vars[ULT_CHARGE].value(), 0)

    def test_existing_burst_hot_survives_control_but_new_ability_is_rejected(self):
        run, player, enemy, fight, _ = self.arena()
        own = next(t for t in fight._targets() if t.owner is player)
        fight.vitals_of(own).damage(100)
        for _ in range(20):
            run.step({player: (0, 0, 8)})
        self.assertTrue(fight.baptiste.regeneration)
        self.flash(run, player, enemy)
        start = fight.vitals_of(own).total
        end = fight.stuns[player.body]
        with patch.object(fight.projectiles, "fired", return_value=None):
            while run.frame < end - 1:
                self.step(run, player, enemy, buttons=16)
        self.assertGreater(fight.vitals_of(own).total, start)
        self.assertFalse(fight.deployables)
        self.assertFalse(any(rocket.deployment is not None for rocket in fight.rockets))

    def test_flashbang_aborts_burst_startup_without_delayed_healing(self):
        run, player, enemy, fight, _ = self.arena()
        ally = next(actor for actor in run.players if actor not in (player, enemy))
        ally.mover.position[:] = (4.0, 1.0, 0.0)
        ally.mover.body = None
        vitals = fight.vitals_of(next(target for target in fight._targets() if target.owner is ally))
        vitals.damage(180)
        burst = next(i for i in player.body_script.component.instances.values() if i.graph.index == 0x19A5)
        with patch.object(fight.projectiles, "fired", return_value=None):
            for frame in range(1, 97):
                self.step(run, player, enemy, cast=frame == 40, buttons=8 if frame == 34 else 0)
                if frame == 46:
                    self.assertTrue(fight.is_stunned(player))
            self.assertFalse(fight.is_stunned(player))
            self.assertFalse(burst.states.get(4) and burst.states[4].counter)
            self.assertEqual(burst.vars[61].value(), 0)
            self.assertEqual(vitals.total, 20)
            self.step(run, player, enemy, buttons=8)
            for _ in range(20):
                self.step(run, player, enemy)
        self.assertEqual(burst.states[4].counter, 1)
        self.assertGreater(vitals.total, 20)

    def test_existing_field_protects_health_without_granting_control_immunity(self):
        run, player, enemy, fight, _ = self.arena()
        with patch.object(fight.projectiles, "fired", return_value=None):
            for frame in range(60):
                run.step({player: (0, 8192, 16 if frame == 0 else 0)})
        actor, = fight.baptiste.fields.values()
        own = next(t for t in fight._targets() if t.owner is player)
        fight._damage(enemy, own, 10000, False, None, run.frame)
        self.assertAlmostEqual(fight.vitals_of(own).total, 20)
        self.flash(run, player, enemy)
        self.assertAlmostEqual(fight.vitals_of(own).total, 20)
        self.assertIn(actor.actor.entity, fight.deployables)
        self.assertTrue(fight.is_stunned(player))

    def test_full_boot_charge_cannot_jump_during_stun_and_crouch_can_charge_again(self):
        run, player, enemy, fight, _ = self.arena()
        for _ in range(80):
            run.step({player: (0, 0, 0x80)})
        boots = next(i for i in player.body_script.component.instances.values() if i.graph.index == 0x1983)
        self.assertEqual(boots.vars[154].value(), 1)
        self.flash(run, player, enemy, buttons=0x80)
        y = player.mover.position[1]
        end = fight.stuns[player.body]
        while run.frame < end - 1:
            self.step(run, player, enemy, buttons=0xC0)
        self.assertAlmostEqual(player.mover.position[1], y)
        while run.frame <= end:
            self.step(run, player, enemy)
        for _ in range(80):
            run.step({player: (0, 0, 0x80)})
        self.assertEqual(boots.vars[154].value(), 1)
        run.step({player: (0, 0, 0x40)})
        self.assertGreater(player.mover.velocity[1], 15)


if __name__ == "__main__":
    unittest.main()
