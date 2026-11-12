"""Copied Soldier commands through real movement/projectile/health consumers.

Injected charge isolates copied ultimate execution. Native auto-targeting,
accelerated charging, transformation protection and presentation remain open.
"""
import unittest
from dataclasses import replace

import test_echo_duplicate as copies
import test_echo_movement as movement
from test_game_combat import aim
from test_game_mover import box, wall

from ow174.game import combat
from ow174.game.collision import World
from ow174.game.mover import Mover, mover_data


class EchoCopiedSoldierTests(unittest.TestCase):
    def session(self):
        fixture = copies.EchoDuplicateTests()
        run, player, enemy, target, angles = fixture.session()
        fixture.start(run, player, angles)
        player.body_script.ult_seconds = 0
        run.step({})  # actual Q-up on the copied body
        return run, player, enemy, target, angles

    @staticmethod
    def instance(player, graph):
        return next(i for i in player.body_script.component.instances.values()
                    if i.graph.index == graph)

    def visor(self, run, player, angles):
        copies.EchoDuplicateTests().charged(player)
        run.step({player: (*angles, 32)})
        for _ in range(85):
            run.step({})
        self.assertTrue(self.instance(player, 0x259).states[7].active)

    def test_copied_visor_acquires_off_axis_enemy_that_ordinary_rifle_misses(self):
        run, player, _, target, angles = self.session()
        offset = (angles[0] + 3641, angles[1])  #20 degrees; clear of the actual capsule
        for _ in range(26):
            run.step({player: (*offset, 1)})
        self.assertEqual(run.match.combat.vitals_of(target).total, 200)
        self.visor(run, player, angles)
        for _ in range(26):
            run.step({player: (*offset, 1)})
        self.assertEqual(run.match.combat.vitals_of(target).total, 120)

    def test_copied_q_does_not_cancel_duplicate_and_visor_ends_at_return(self):
        run, player, _, target, angles = self.session()
        fight = run.match.combat
        copy = fight.echo_duplicates.states[player.entity]
        script = player.body_script
        run.step({player: (*angles, 32)})
        self.assertEqual(player.body, copy.body)
        self.assertTrue(fight.echo_duplicates.active(player))
        self.assertFalse(self.instance(player, 0x259).states[0].flags & 1)
        run.step({})

        run.frame = int((copy.expires - 2.2) / combat.TICK)
        run.step({})
        self.visor(run, player, angles)
        self.assertEqual(player.body, copy.body)
        offset = (angles[0] + 3641, angles[1])
        for _ in range(20):
            run.step({player: (*offset, 1)})
        vitals = fight.vitals_of(target)
        self.assertLess(vitals.total, 200)
        self.assertTrue(self.instance(player, 0x259).states[7].active)

        for _ in range(120):
            if not fight.echo_duplicates.active(player):
                break
            run.step({})
        self.assertEqual(player.hero.guid, copies.ECHO)
        self.assertTrue(script.retired)
        before = vitals.total
        for _ in range(25):
            run.step({player: (*offset, 1)})
        self.assertEqual(vitals.total, before)

    def test_copied_visor_targeting_respects_team_real_wall_cone_range_and_death(self):
        for kind in ('ally', 'wall', 'cone', 'range', 'dead'):
            with self.subTest(kind=kind):
                run, player, enemy, target, angles = self.session()
                fight = run.match.combat
                self.visor(run, player, angles)
                offset = (angles[0] + 3641, angles[1])
                if kind == 'ally':
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    enemy.team = player.team
                elif kind == 'wall':
                    x, y, z = player.mover.position
                    run.match.collision_world = World(
                        box(x-50, x+50, y-1, y, z-50, z+50)
                        + wall(x-5, z+2, x+5, z+2, y0=y, y1=y+5))
                elif kind == 'cone':
                    offset = (angles[0] + 16384, 0)  #90 degrees
                elif kind == 'range':
                    x, y, z = player.mover.position
                    enemy.mover = Mover((x, y, z + 50), world=run.match.collision_world,
                                        data=mover_data(enemy.hero.body))
                else:
                    fight._damage(player, target, 1000, False, None, run.frame)
                for _ in range(20):
                    run.step({})
                if kind == 'range':
                    self.assertGreater(enemy.mover.position[2] - player.mover.position[2], 49)
                before = fight.vitals_of(target).total
                hits = len(player.client.messages(combat.HIT))
                for _ in range(26):
                    run.step({player: (*offset, 1)})
                self.assertEqual(fight.vitals_of(target).total, before)
                self.assertEqual(len(player.client.messages(combat.HIT)), hits)

    def test_copied_visor_keeps_helix_manually_aimed(self):
        run, player, _, target, angles = self.session()
        self.visor(run, player, angles)
        offset = (angles[0] + 3641, angles[1])
        run.step({player: (*offset, 2)})
        for _ in range(80):
            run.step({})
        self.assertGreater(self.instance(player, 0x254).vars[61].value(), 4)
        self.assertEqual(run.match.combat.vitals_of(target).total, 200)

    def test_copied_helix_deals_authored_contact_and_splash_then_rejects_cooldown(self):
        run, player, _, target, angles = self.session()
        fight = run.match.combat
        run.step({player: (*angles, 2)})
        for _ in range(20):
            run.step({})
        self.assertEqual(fight.vitals_of(target).total, 80)  # authored40+80
        self.assertEqual(player.body_script.component.entity_var(342).value(), 780)
        weapon = self.instance(player, 0x254)
        self.assertGreater(weapon.vars[61].value(), 5)
        for _ in range(3):
            run.step({player: (*angles, 2)})
        for _ in range(30):
            run.step({})
        self.assertEqual(fight.vitals_of(target).total, 80)
        self.assertFalse(fight.rockets)
        for _ in range(355):
            run.step({})
        self.assertEqual(weapon.vars[61].value(), 0)
        run.step({player: (*angles, 2)})
        for _ in range(20):
            run.step({})
        self.assertTrue(fight.vitals_of(target).dead)
        self.assertEqual(player.body_script.component.entity_var(342).value(), 1300)
        self.assertTrue(fight.echo_duplicates.active(player))

    def test_timed_return_retires_in_flight_copied_helix(self):
        run, player, _, target, angles = self.session()
        fight = run.match.combat
        copied_body = player.body
        expires = fight.echo_duplicates.states[player.entity].expires
        while expires - fight.now > 3 * combat.TICK:
            run.step({})
        run.step({player: (*angles, 2)})
        self.assertTrue(fight.echo_duplicates.active(player))
        rocket, = fight.rockets
        self.assertEqual(rocket.source_body, copied_body)
        self.assertIsNotNone(rocket.shot)
        self.assertEqual(fight.vitals_of(target).total, 200)
        for _ in range(4):
            run.step({})
        self.assertEqual(player.hero.guid, copies.ECHO)
        self.assertNotEqual(player.body, copied_body)
        self.assertFalse(fight.rockets)
        player.body_script.ult_seconds = 0
        returned_charge = player.body_script.component.entity_var(342).value()
        for _ in range(20):
            run.step({})
        self.assertEqual(fight.vitals_of(target).total, 200)
        self.assertEqual(player.body_script.component.entity_var(342).value(), returned_charge)
        self.assertEqual(player.client.messages(combat.HIT), [])

    def test_copied_sprint_changes_actual_speed_and_toggles_back_to_run(self):
        run, player, _, _, _ = self.session()
        step = movement.EchoMovementTests.step
        z = player.mover.position[2]
        step(run, player, buttons=8, forward=127)
        for _ in range(30):
            step(run, player, forward=127)
        self.assertAlmostEqual(player.mover.velocity[2], 8.25, delta=.01)
        self.assertGreater(player.mover.position[2] - z, 3)
        self.assertTrue(self.instance(player, 0x255).states[1].flags & 1)
        step(run, player, buttons=8, forward=127)
        for _ in range(12):
            step(run, player, forward=127)
        self.assertAlmostEqual(player.mover.velocity[2], 5.5, delta=.01)
        self.assertFalse(self.instance(player, 0x255).states[1].flags & 1)

    def test_copied_sprint_stops_for_real_weapon_fire(self):
        run, player, enemy, target, _ = self.session()
        step = movement.EchoMovementTests.step
        for _ in range(10):
            step(run, player, buttons=8, forward=127)
        self.assertTrue(self.instance(player, 0x255).states[1].flags & 1)
        angles = aim(run.match.combat._eye(player),
                     (enemy.mover.position[0], enemy.mover.position[1] + 1,
                      enemy.mover.position[2]))
        for _ in range(25):
            step(run, player, buttons=1, yaw=angles[0], pitch=angles[1])
        self.assertFalse(self.instance(player, 0x255).states[1].flags & 1)
        self.assertLess(run.match.combat.vitals_of(target).total, 200)

    def test_copied_visor_spends_charge_runs_fires_ends_and_needs_charge_again(self):
        run, player, _, target, angles = self.session()
        cost = copies.EchoDuplicateTests().charged(player)
        self.assertGreater(cost, 0)
        run.step({player: (*angles, 32)})
        visor = self.instance(player, 0x259).states[0]
        self.assertTrue(visor.flags & 1)
        self.assertEqual(player.body_script.component.entity_var(342).value(), 0)
        for _ in range(90):
            run.step({player: (*angles, 1)})
        self.assertLess(run.match.combat.vitals_of(target).total, 200)
        self.assertEqual(player.body_script.component.entity_var(342).value(), 0)
        for _ in range(370):
            run.step({})
        self.assertFalse(visor.flags & 1)
        self.assertTrue(run.match.combat.echo_duplicates.active(player))
        before = run.match.combat.vitals_of(target).total
        for _ in range(26):
            run.step({player: (angles[0] + 3641, angles[1], 1)})
        self.assertEqual(run.match.combat.vitals_of(target).total, before)
        run.step({player: (*angles, 32)})
        self.assertFalse(visor.flags & 1)
        for _ in range(12):
            run.step({player: (*angles, 1)})
        self.assertGreater(player.body_script.component.entity_var(342).value(), 0)
        self.assertTrue(player.client.messages(combat.HIT))
