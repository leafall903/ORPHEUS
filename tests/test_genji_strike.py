"""Actual command/mover/damage integration for reconstructed Genji Swift Strike."""

import unittest

from test_game_combat import aim
from test_game_mover import FLOOR, wall
from test_genji_combat import GENJI, GenjiFixture

from ow174.game import combat
from ow174.game.collision import World
from ow174.game.commands import Command
from ow174.game.content import heroes
from ow174.game.script import runtime


class GenjiStrikeTests(GenjiFixture):
    def strike(self, run, player, frames=40, presses=(0,)):
        for frame in range(frames):
            run.step({player: (0, 0, 8 if frame in presses else 0)})

    def instance(self, player):
        return next(i for i in player.body_script.component.instances.values() if i.graph.index == 0x01DB)

    def test_normal_shift_moves_fifteen_metres_and_hits_once(self):
        run, player = self.spawn(4)
        self.strike(run, player)
        self.assertEqual(player.mover.position, [0, 1, 15])
        self.assertEqual(self.health(run, run.players[1]), 150)
        self.assertFalse(self.instance(player).state(1).active)
        self.assertGreater(self.instance(player).state(0).var.value(), 7)

    def test_strike_hits_through_frontal_deflect_and_resets_on_kill(self):
        for lethal in (False, True):
            with self.subTest(lethal=lethal):
                run, attacker = self.spawn(4)
                defender = run.players[1]
                run.match.switch_hero(defender, heroes()[GENJI])
                defender.mover.position[:] = [0, 1, 4]
                if lethal:
                    yaw, pitch = aim(run.match.combat._eye(attacker), (0, 1.65, 4))
                    for frame in range(120):
                        run.step({attacker: (yaw, pitch, 1 if frame in (0, 60) else 0)})
                before = self.health(run, defender)
                self.assertEqual(before, 26 if lethal else 200)
                hits_before = len(attacker.client.messages(combat.HIT))
                deflect = next(i for i in defender.body_script.component.instances.values()
                               if i.graph.index == 0x03A3).state(10)
                run.step({defender: (-32768, 0, 16)})
                self.assertTrue(deflect.active)
                for frame in range(40):
                    run.step({attacker: (0, 0, 8 if frame == 0 else 0),
                              defender: (-32768, 0, 0)})
                self.assertEqual(attacker.mover.position, [0, 1, 15])
                self.assertEqual(self.health(run, attacker), 200)
                self.assertEqual(self.health(run, defender), 0 if lethal else 150)
                hits = attacker.client.messages(combat.HIT)[hits_before:]
                self.assertEqual([hit["+0x78"]["+0x4"] for hit in hits], [min(50, before)])
                self.assertFalse(defender.client.messages(combat.HIT))
                cooldown = self.instance(attacker).state(0).var.value()
                if lethal:
                    self.assertEqual(cooldown, 0)
                    self.strike(run, attacker)
                    self.assertEqual(attacker.mover.position, [0, 1, 30])
                else:
                    self.assertTrue(deflect.active)
                    self.assertGreater(cooldown, 7)

    def test_strike_hits_through_kinetic_grasp_without_granting_shields(self):
        run, player = self.spawn(4)
        sigma = run.players[1]
        run.match.switch_hero(sigma, heroes()[0x02E000000000023B])
        sigma.mover.position[:] = [0, 1, 4]
        grasp = next(i for i in sigma.body_script.component.instances.values() if i.graph.index == 0x1D02)
        run.step({sigma: (-32768, 0, 8)})
        self.strike(run, player)
        self.assertTrue(grasp.state(1).active)
        self.assertEqual(grasp.vars[21312].value(), 0)
        self.assertEqual(player.mover.position, [0, 1, 15])
        self.assertGreater(self.instance(player).state(0).var.value(), 7)
        self.assertEqual(self.health(run, sigma), 350)
        hits = player.client.messages(combat.HIT)
        self.assertEqual([hit["+0x78"]["+0x4"] for hit in hits], [50])
        for _ in range(110):
            run.step({})
        self.assertFalse(grasp.state(1).active)
        self.assertEqual(self.health(run, sigma), 350)
        self.assertFalse(any(i.graph.index == 0x1D03 for i in sigma.body_script.component.instances.values()))

    def test_idle_modify_health_states_do_not_damage(self):
        run, player = self.spawn(0.8)
        self.strike(run, player, presses=())
        self.assertEqual(self.health(run, run.players[1]), 200)

    def test_distant_and_side_targets_miss(self):
        for x, z in ((0, 20), (5, 4), (0, -4)):
            with self.subTest(x=x, z=z):
                run, player = self.spawn(z)
                run.players[1].mover.position[0] = x
                self.strike(run, player)
                self.assertEqual(self.health(run, run.players[1]), 200)

    def test_strike_hits_multiple_players_along_path(self):
        run, player = self.spawn(4, ("Genji", "A", "B"))
        run.players[2].mover.position[2] = 10
        self.strike(run, player)
        self.assertEqual([self.health(run, p) for p in run.players[1:]], [150, 150])

    def test_strike_stops_at_wall_and_does_not_damage_through_it(self):
        run, player = self.spawn(3)
        run.match.collision_world = World(FLOOR + wall(-5, 2, 5, 2))
        self.strike(run, player)
        self.assertLess(player.mover.position[2], 2)
        self.assertEqual(self.health(run, run.players[1]), 200)

    def test_strike_does_not_hit_ally(self):
        run, player = self.spawn(4)
        run.match.game_map = run.match.game_map.__class__(
            **{**run.match.game_map.__dict__, "free_for_all": False}
        )
        run.players[1].team = player.team
        self.strike(run, player)
        self.assertEqual(self.health(run, run.players[1]), 200)

    def test_cooldown_rejects_early_press_and_expires(self):
        run, player = self.spawn(20)
        self.strike(run, player, frames=80, presses=(0, 40))
        instance = self.instance(player)
        self.assertEqual(instance.state(1).counter, 1)
        self.strike(run, player, frames=480, presses=())
        self.assertEqual(instance.state(1).counter, 1)
        self.assertEqual(instance.state(0).var.value(), 0)
        self.strike(run, player)
        self.assertEqual(instance.state(1).counter, 2)
        self.assertEqual(player.mover.position, [0, 1, 30])

    def test_expired_early_press_cannot_wait_forever(self):
        run, player = self.spawn(20)
        self.strike(run, player, frames=560, presses=(0, 40, 500))
        self.assertEqual(self.instance(player).state(1).counter, 1)
        self.assertEqual(player.mover.position, [0, 1, 15])

    def test_cancel_stops_movement_and_future_damage(self):
        run, player = self.spawn(10)
        self.strike(run, player, frames=4)
        movement = self.instance(player).state(1)
        movement.request(runtime.ABORT)
        movement.instance.component.run_now()
        position = tuple(player.mover.position)
        self.strike(run, player, frames=40, presses=())
        self.assertEqual(tuple(player.mover.position), position)
        self.assertEqual(self.health(run, run.players[1]), 200)

    def test_duplicate_command_cannot_damage_twice(self):
        run, player = self.spawn(4)
        self.strike(run, player)
        player.take_commands([Command(run.frame, buttons=8)])
        self.assertEqual(self.health(run, run.players[1]), 150)


if __name__ == "__main__":
    unittest.main()
