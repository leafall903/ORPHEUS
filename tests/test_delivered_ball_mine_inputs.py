"""Original Minefield Q reload/death/replacement checks on canonical actors."""
import math
import unittest
from dataclasses import replace
from unittest.mock import patch
from test_delivered_ball_mine_factory import MineTests
from test_delivered_wrecking_ball_fireball import relocate
from test_game_combat import Run, KANEZAKA_DM, aim
from ow174.game import ball, ball_mines, combat, stats, world
from ow174.game.commands import Command
from ow174.game.content import heroes
from ow174.game.script import expr, graph, runtime
from ow174.game.script.driver import ULT_CHARGE, ULT_COST

class MineInputTests(unittest.TestCase):
    setUpClass = classmethod(MineTests.setUpClass.__func__)
    scenario = MineTests.scenario
    deploy = MineTests.deploy
    isolated = MineTests.isolated
    target = MineTests.target

    def test_minefield_interrupts_reload_before_fill_and_preserves_reload_exit(self):
        for automatic in (False, True):
            for filled in (False, True):
                with self.subTest(automatic=automatic, filled=filled):
                    run, player, _ = self.scenario()
                    weapon = ball.instance(player, 0x1514)
                    ammo = weapon.vars[53]
                    trigger = {player: (-32768, 0, 1)}
                    for _ in range(240 if automatic else 30):
                        run.step(trigger)
                        if automatic and ammo.value() == 0:
                            break
                    run.step({})
                    if not automatic:
                        run.step({player: (0, 0, 1024)})
                    wait = weapon.state(20 if filled else 21)
                    for _ in range(100):
                        if wait.active:
                            break
                        run.step({})
                    self.assertTrue(wait.active)
                    counter = wait.counter
                    initial_ammo = 80 if filled else (0 if automatic else 67)
                    self.assertEqual(ammo.value(), initial_ammo)

                    script = player.body_script
                    charge = script.component.entity_var(ULT_CHARGE)
                    script.set_ult_charge(script.component.entity_var(ULT_COST).value())
                    run.step({player: (-32768, 0, 32)})
                    self.assertEqual(charge.value(), 0)
                    self.assertEqual(wait.active, filled)
                    mines = run.match.combat.mines
                    for _ in range(20):
                        run.step(trigger)
                        self.assertFalse(weapon.state(9).active)
                        self.assertEqual(ammo.value(), initial_ammo)
                    self.assertEqual(len(mines.entities), 15)
                    self.assertEqual({key[2] for key in mines.seen}, {3, 8, 18})

                    if automatic and not filled:
                        for _ in range(100):
                            if wait.active:
                                break
                            run.step(trigger)
                        self.assertTrue(wait.active)
                        self.assertNotEqual(wait.counter, counter)
                        self.assertEqual(ammo.value(), 0)
                    elif filled:
                        self.assertTrue(wait.active)
                        self.assertEqual(wait.counter, counter)

                    for _ in range(200):
                        run.step(trigger)
                        if weapon.state(9).active:
                            break
                    self.assertTrue(weapon.state(9).active)
                    self.assertFalse(weapon.state(20).active)
                    self.assertFalse(weapon.state(21).active)
                    self.assertEqual(ammo.value(), 79 if automatic or filled else 66)
                    self.assertEqual(len(mines.entities), 15)
                    self.assertEqual(charge.value(), 0)

    def test_deployed_mines_arm_and_hit_after_actual_rifle_kills_ball(self):
        run, player, enemy = self.scenario()
        x, y, z = player.mover.position
        relocate(run, enemy, (x, y, z + 6))
        for _ in range(60):
            run.step({})
        fight = run.match.combat
        own = fight.vitals_of(self.target(run, player))
        angles = aim(fight._eye(enemy), (x, y + 1, z))
        for _ in range(500):
            run.step({enemy: (*angles, 1)})
            if own.total <= 40:
                break
        self.assertGreater(own.total, 0)
        self.assertLessEqual(own.total, 40)
        deployed = {mine.entity for mine in self.deploy(run, player, ticks=20)}
        self.assertEqual(len(deployed), 15)
        for _ in range(200):
            run.step({enemy: (*angles, 1)})
            if fight.is_dead(player):
                break
        self.assertTrue(fight.is_dead(player))
        self.assertEqual(own.killer, enemy.body)
        self.assertEqual(set(fight.mines.entities), deployed)

        relocate(run, enemy, (x + 100, y, z))
        for _ in range(200):
            run.step({})
        self.assertTrue(fight.is_dead(player))
        self.assertEqual(set(fight.mines.entities), deployed)
        mine = next(iter(fight.mines.entities.values()))
        self.assertTrue(mine.body_script.primary().states[28].active)
        for other in list(fight.mines.entities.values()):
            if other is not mine:
                fight.mines.remove(other)
        mx, my, mz = mine.position
        relocate(run, enemy, (mx, my, mz + 0.5))
        run.step({})
        self.assertTrue(mine.triggered)
        self.assertEqual(fight.vitals_of(self.target(run, enemy)).total, 70)
        self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), 0)

    def partial_minefield_death(self, reports):
        run, player, enemy = self.scenario()
        x, y, z = player.mover.position
        relocate(run, enemy, (x + 6, y, z))
        for _ in range(60):
            run.step({})
        fight = run.match.combat
        own = fight.vitals_of(self.target(run, player))
        angles = aim(fight._eye(enemy), (x, y + 1, z))
        for _ in range(650):
            run.step({enemy: (*angles, 1)})
            if own.total <= 20:
                break
        self.assertGreater(own.total, 0)
        self.assertLessEqual(own.total, 20)
        for _ in range(90):
            run.step({})
        script = player.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        run.step({player: (0, 0, 32)})
        for _ in range(15):
            if len(fight.mines.seen) >= reports:
                break
            run.step({})
        mines = tuple(fight.mines.entities.values())
        self.assertEqual(len(fight.mines.seen), reports)
        self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)

        run.step({enemy: (*angles, 1)})
        self.assertTrue(fight.is_dead(player))
        self.assertEqual(own.killer, enemy.body)
        self.assertEqual(len(fight.mines.seen), reports)
        relocate(run, enemy, (x + 100, y, z))
        return run, player, enemy, mines

    def test_actual_rifle_kill_preserves_only_released_minefield_reports(self):
        for reports, expected in ((0, 0), (1, 1), (2, 7), (3, 15)):
            with self.subTest(reports=reports):
                run, player, _, mines = self.partial_minefield_death(reports)
                self.assertEqual(len(mines), expected)
                fight = run.match.combat
                for _ in range(200):
                    run.step({player: (0, 0, 32)})
                self.assertTrue(fight.is_dead(player))
                self.assertEqual(len(fight.mines.seen), reports)
                self.assertEqual(set(fight.mines.entities), {mine.entity for mine in mines})
                self.assertTrue(all(mine.body_script.primary().states[28].active and mine.health == 50
                                    for mine in mines))
                self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), 0)

    def test_partial_minefield_death_to_natural_replacement_minefield(self):
        run, player, _, mines = self.partial_minefield_death(1)
        fight = run.match.combat
        old_body = player.body
        old_script = player.body_script
        old_charge = old_script.component.entity_var(ULT_CHARGE)
        for _ in range(60):
            run.step({player: (0, 0, 32)})
            self.assertTrue(fight.is_dead(player))
            self.assertEqual(old_charge.value(), 0)
            self.assertEqual(set(fight.mines.entities), {mines[0].entity})
            self.assertEqual(len(fight.mines.seen), 1)

        for _ in range(650):
            run.step({})
            if player.body != old_body:
                break
        self.assertNotEqual(player.body, old_body)
        self.assertTrue(old_script.retired)
        self.assertFalse(fight.is_dead(player))
        self.assertFalse(fight.mines.entities)
        script = player.body_script
        self.assertIsNone(script.ult_seconds)
        component = script.component
        charge = component.entity_var(ULT_CHARGE)
        cost = component.entity_var(ULT_COST).value()
        self.assertEqual((charge.value(), cost), (0, 1540))
        self.assertFalse(component.entity_var(842).value())

        run.step({player: (0, 0, 32)})
        first_tick = None
        for frame in range(2, 21001):
            run.step({})
            value = charge.value()
            if value and first_tick is None:
                first_tick = frame
                self.assertEqual(value, 5)
            if value == cost:
                break
        self.assertIn(first_tick, range(1, 64))
        self.assertEqual(charge.value(), cost)
        self.assertFalse(fight.mines.entities)
        self.assertEqual(old_charge.value(), 0)

        run.step({player: (0, 0, 32)})
        self.assertEqual(charge.value(), 0)
        self.assertTrue(component.entity_var(842).value())
        for _ in range(40):
            run.step({})
        self.assertEqual(len(fight.mines.entities), 15)
        self.assertTrue(all(mine.source == player.body and mine.owner is player
                            for mine in fight.mines.entities.values()))

    def test_respawn_retires_armed_mine_and_clears_it_for_both_viewers(self):
        run, player, enemy, mines = self.partial_minefield_death(1)
        mine, = mines
        fight = run.match.combat
        source = player.body
        for _ in range(200):
            run.step({})
        self.assertTrue(fight.is_dead(player))
        self.assertTrue(mine.body_script.primary().states[28].active)
        self.assertEqual(mine.health, 50)
        self.assertEqual(mine.source, source)
        self.assertIn(mine.entity, fight.mines.entities)
        viewers = [actor.client for actor in (player, enemy)]
        for viewer in viewers:
            self.assertEqual([item.op for item in viewer.entities
                              if item.entity == mine.entity].count(world.OP_CREATE), 1)
            viewer.entities.clear()

        for _ in range(650):
            run.step({})
            if player.body != source:
                break
            self.assertIn(mine.entity, fight.mines.entities)
        self.assertNotEqual(player.body, source)
        self.assertFalse(fight.is_dead(player))
        self.assertNotIn(mine.entity, fight.mines.entities)
        self.assertFalse(fight.mines.seen)

        x, y, z = mine.position
        enemy_vitals = fight.vitals_of(self.target(run, enemy))
        previous_health = enemy_vitals.total
        relocate(run, enemy, (x, y, z + 0.5))
        for _ in range(8):
            run.step({})
        self.assertEqual(enemy_vitals.total, previous_health)
        self.assertFalse(mine.triggered)
        self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), 0)
        for viewer in viewers:
            self.assertEqual([item.op for item in viewer.entities
                              if item.entity == mine.entity].count(world.OP_DESTROY), 1)
            self.assertFalse(any(item.entity == mine.entity and item.op == world.OP_CREATE
                                 for item in viewer.entities))
