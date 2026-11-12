"""Dragonblade commands execute authored charge gates and sword target windows."""

import unittest

from test_game_mover import FLOOR, wall
from test_genji_combat import GENJI, GenjiFixture

from ow174.game import combat
from ow174.game.collision import World
from ow174.game.content import heroes


class GenjiBladeTests(GenjiFixture):
    def run_blade(self, run, player, frames=110, *, charge=True):
        component = player.body_script.component
        if charge:
            player.body_script.set_ult_charge(component.entity_var(338).value())
        for frame in range(frames):
            run.step({player: (0, 0, 32 if frame == 1 else (1 if frame > 70 else 0))})

    def ultimate(self, player):
        instance = next(i for i in player.body_script.component.instances.values() if i.graph.index == 0x0317)
        return instance.state(3)

    def test_full_charge_is_consumed_and_swing_deals_authored_110_once(self):
        run, player = self.spawn(4)
        self.run_blade(run, player)
        self.assertEqual(self.health(run, run.players[1]), 90)
        self.assertEqual(player.body_script.component.entity_var(342).value(), 0)
        self.assertTrue(self.ultimate(player).flags & 1)

    def test_empty_charge_rejects_ultimate(self):
        run, player = self.spawn(4)
        self.run_blade(run, player, frames=40, charge=False)
        self.assertFalse(self.ultimate(player).flags & 1)

    def test_repeated_swings_kill_and_do_not_hit_dead_body(self):
        run, player = self.spawn(4)
        self.run_blade(run, player, frames=170)
        enemy = run.players[1]
        self.assertTrue(run.match.combat.is_dead(enemy))
        self.assertEqual(self.health(run, enemy), 0)
        self.assertEqual(len(player.client.messages(25001)), 2)

    def test_sword_misses_distant_or_rear_enemy(self):
        for distance in (8, -4):
            with self.subTest(distance=distance):
                run, player = self.spawn(distance)
                self.run_blade(run, player)
                self.assertEqual(self.health(run, run.players[1]), 200)

    def test_sword_static_wall_blocks_damage(self):
        run, player = self.spawn(4)
        run.match.collision_world = World(FLOOR + wall(-5, 2, 5, 2))
        self.run_blade(run, player)
        self.assertEqual(self.health(run, run.players[1]), 200)

    def test_sword_respects_deflect_facing_and_expiry(self):
        for case, yaw, deflect_frame, damage in (
            ("front", -32768, 140, 0),
            ("rear", 0, 140, 110),
            ("expired", -32768, 0, 110),
        ):
            with self.subTest(case=case):
                run, attacker = self.spawn(4)
                defender = run.players[1]
                run.match.switch_hero(defender, heroes()[GENJI])
                defender.mover.position[:] = [0, 1, 4]
                component = attacker.body_script.component
                attacker.body_script.set_ult_charge(component.entity_var(338).value())
                deflect = next(i for i in defender.body_script.component.instances.values()
                               if i.graph.index == 0x03A3).state(10)
                for frame in range(190):
                    run.step({
                        attacker: (0, 0, 32 if frame == 0 else 1 if frame == 150 else 0),
                        defender: (yaw, 0, 16 if frame == deflect_frame else 0),
                    })
                    if frame == 150:
                        self.assertEqual(deflect.counter, 1)
                        self.assertEqual(deflect.active, case != "expired")
                self.assertEqual(self.health(run, defender), 200 - damage)
                self.assertEqual(self.health(run, attacker), 200)
                hits = attacker.client.messages(combat.HIT)
                self.assertEqual([hit["+0x78"]["+0x4"] for hit in hits], [damage] if damage else [])
                self.assertFalse(defender.client.messages(combat.HIT))

    def test_sword_hits_through_kinetic_grasp_without_granting_shields(self):
        run, player = self.spawn(4)
        sigma = run.players[1]
        run.match.switch_hero(sigma, heroes()[0x02E000000000023B])
        sigma.mover.position[:] = [0, 1, 4]
        component = player.body_script.component
        player.body_script.set_ult_charge(component.entity_var(338).value())
        grasp = next(i for i in sigma.body_script.component.instances.values() if i.graph.index == 0x1D02)
        for frame in range(190):
            run.step({
                player: (0, 0, 32 if frame == 0 else 1 if frame == 150 else 0),
                sigma: (-32768, 0, 8 if frame == 140 else 0),
            })
        self.assertTrue(grasp.state(1).active)
        self.assertEqual(grasp.vars[21312].value(), 0)
        self.assertAlmostEqual(self.health(run, sigma), 290)
        hits = player.client.messages(combat.HIT)
        self.assertEqual([hit["+0x78"]["+0x4"] for hit in hits], [110])
        for _ in range(110):
            run.step({})
        self.assertFalse(grasp.state(1).active)
        self.assertAlmostEqual(self.health(run, sigma), 290)
        self.assertFalse(any(i.graph.index == 0x1D03 for i in sigma.body_script.component.instances.values()))

    def test_sword_hits_multiple_enemies(self):
        run, player = self.spawn(4, ("Genji", "A", "B"))
        run.players[2].mover.position[0] = 1
        self.run_blade(run, player)
        self.assertEqual([self.health(run, p) for p in run.players[1:]], [90, 90])

    def test_sword_does_not_hit_ally(self):
        run, player = self.spawn(4)
        run.match.game_map = run.match.game_map.__class__(
            **{**run.match.game_map.__dict__, "free_for_all": False}
        )
        run.players[1].team = player.team
        self.run_blade(run, player)
        self.assertEqual(self.health(run, run.players[1]), 200)

    def test_ultimate_ends_and_shuriken_returns(self):
        run, player = self.spawn(10)
        self.run_blade(run, player, frames=530)
        self.assertFalse(self.ultimate(player).flags & 1)
        weapon = next(i for i in player.body_script.component.instances.values() if i.graph.index == 0x0304)
        self.assertGreater(weapon.state(4).counter, 0)


if __name__ == "__main__":
    unittest.main()
