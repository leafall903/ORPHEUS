"""Real Flashbang input and D.Va interruption; resource policy is reconstructed."""

import math
import unittest
from dataclasses import replace
from unittest.mock import patch

from test_bots import make_bot
from test_game_combat import KANEZAKA_DM, Run, aim, dva_pilot_input
from test_game_mover import FLOOR
from test_game_mover import wall as collision_wall

from ow174.game import combat
from ow174.game.collision import World
from ow174.game.commands import Command
from ow174.game.content import find_hero, heroes
from ow174.game.mover import defined_mover_data
from ow174.game.script import graph, nodes

MECH = 0x0400000000000407


class DvaControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if graph.entity_body(MECH) is None or defined_mover_data(MECH) is None:
            raise unittest.SkipTest("requires actual0407 graph/mover inputs; control acceptance OPEN")

    def scenario(self, mech, case="hit"):
        run = Run(KANEZAKA_DM, ("McCree", "DVa"))
        match = run.match
        match.bots.clear()
        match._pending_bot_scripts.clear()
        caster, target = run.players
        self.assertTrue(match.switch_hero(caster, heroes()[0x02E0000000000042]))
        hero = find_hero("dva")
        if mech:
            hero = replace(hero, body=MECH)
        self.assertTrue(match.switch_hero(target, hero))
        dva_pilot_input(target)
        caster.mover.position = [0, 1, 0]
        target.mover.position = [0, 1, 20 if case == "range" else 2]
        for player in run.players:
            player.mover.body = None
        if case == "ally":
            match.game_map = replace(match.game_map, free_for_all=False)
            caster.team = target.team
        if case == "wall":
            match.collision_world = World(FLOOR + collision_wall(-10, 1, 10, 1))
            for player in run.players:
                player.mover.use(world=match.collision_world)
        for _ in range(100):
            run.step({})
        return run, caster, target

    def vitals(self, run, player):
        body = next(b for b in run.match.combat._targets() if b.owner is player)
        return run.match.combat.vitals_of(body)

    def step(self, run, caster, target, *, cast=0, buttons=0, forward=0, facing=-32768):
        run.frame += 1
        match = run.match
        match.update(run.frame * combat.TICK, run.frame)
        point = target.mover.position
        yaw, pitch = aim(match.combat._eye(caster), (point[0], point[1] + 1, point[2]))
        for player in run.players:
            player.client.queue_entities(match.remote_movements(player))
            if player is caster:
                command = Command(run.frame, yaw=yaw, pitch=pitch, buttons=cast)
            else:
                command = Command(run.frame, yaw=facing, buttons=buttons, forward=forward)
            old = tuple(target.mover.position)
            stunned_before_pose = match.combat.is_stunned(target)
            player.take_commands([command])
            if player is target and stunned_before_pose:
                # Includes the first pose after impact, not just settled frames.
                self.assertEqual(tuple(target.mover.position[0::2]), old[0::2])

    def cast(self, run, caster, target, *, buttons=0, forward=0, facing=-32768):
        self.step(run, caster, target, cast=16, buttons=buttons, forward=forward, facing=facing)
        for _ in range(60):
            if run.match.combat.is_stunned(target):
                return
            self.step(run, caster, target, buttons=buttons, forward=forward, facing=facing)
        self.fail("actual Flashbang input failed to stun D.Va")

    def test_both_forms_take_authored_damage_and_lose_control_until_expiry_then_recover(self):
        for mech in (False, True):
            with self.subTest(mech=mech):
                run, caster, target = self.scenario(mech)
                vitals = self.vitals(run, target)
                before = vitals.total
                self.cast(run, caster, target)
                self.assertEqual(vitals.total, before - (20 if mech else 25))
                fight = run.match.combat
                end = fight.stuns[target.body]
                self.assertEqual(end - fight.tick, 50)
                enemy_before = self.vitals(run, caster).total
                while run.frame < end - 1:
                    self.step(run, caster, target, buttons=1 | 2 | 8 | 16 | 0x800 | 0x40, forward=127)
                    self.assertFalse(target.body_script.component.held)
                    self.assertEqual(self.vitals(run, caster).total, enemy_before)
                old = tuple(target.mover.position)
                for _ in range(30):
                    self.step(run, caster, target, buttons=1, forward=127)
                self.assertFalse(fight.is_stunned(target))
                self.assertNotEqual(tuple(target.mover.position), old)
                self.assertLess(self.vitals(run, caster).total, enemy_before)

    def test_matrix_absorbs_grenade_without_damage_or_control(self):
        run, caster, target = self.scenario(True)
        matrix = next(i for i in target.body_script.component.instances.values() if i.graph.index == 0x02CC)
        for _ in range(3):
            self.step(run, caster, target, buttons=2)
        self.assertTrue(matrix.state(3).active)
        with patch.object(run.match.combat.projectiles, "absorbed",
                          wraps=run.match.combat.projectiles.absorbed) as absorbed:
            self.step(run, caster, target, cast=16, buttons=2)
            for _ in range(45):
                self.step(run, caster, target, buttons=2)
            self.assertEqual(absorbed.call_count, 1)
        self.assertEqual(self.vitals(run, target).total, 600)
        self.assertFalse(run.match.combat.is_stunned(target))
        self.assertTrue(matrix.state(3).active)

    def test_unabsorbed_hit_interrupts_boosters_before_any_extra_forced_throttle_pose(self):
        run, caster, target = self.scenario(True)
        booster = next(i for i in target.body_script.component.instances.values() if i.graph.index == 0x047E)
        for _ in range(3):
            self.step(run, caster, target, buttons=8)
        self.assertTrue(booster.state(2).active)
        self.assertGreater(abs(target.mover.velocity[2]), 0)
        self.cast(run, caster, target, buttons=8)
        self.assertFalse(booster.state(0).flags & nodes.Ability.RUNNING)
        self.assertFalse(booster.state(2).active)
        for _ in range(20):
            self.step(run, caster, target, buttons=8, forward=127)
            self.assertEqual(target.mover.velocity[0::2], (0, 0))
            self.assertFalse(booster.state(2).active)
        self.assertGreater(booster.vars[61].value(), 0)

    def test_rear_hit_cancels_active_matrix_stops_drain_and_allows_later_reuse(self):
        run, caster, target = self.scenario(True)
        matrix = next(i for i in target.body_script.component.instances.values() if i.graph.index == 0x02CC)
        for _ in range(40):
            self.step(run, caster, target, buttons=2, facing=0)
        self.assertTrue(matrix.state(3).active)
        self.cast(run, caster, target, buttons=2, facing=0)
        self.assertFalse(matrix.state(3).active)
        self.assertFalse(matrix.state(0).flags & nodes.Ability.RUNNING)
        self.assertGreater(matrix.vars[61].value(), 0)
        spent = matrix.vars[87].value()
        for _ in range(30):
            self.step(run, caster, target, buttons=2, facing=0)
            self.assertFalse(matrix.state(3).active)
            self.assertGreaterEqual(matrix.vars[87].value(), spent)
        for _ in range(45):
            self.step(run, caster, target)
        self.assertFalse(run.match.combat.is_stunned(target))
        self.step(run, caster, target, buttons=2)
        self.assertTrue(matrix.state(3).active)

    def test_stun_interrupts_micro_burst_without_new_launches_or_cooldown_bypass(self):
        run, caster, target = self.scenario(True)
        missiles = next(i for i in target.body_script.component.instances.values() if i.graph.index == 0x1128)
        for _ in range(20):
            self.step(run, caster, target, buttons=16)
        self.assertTrue(missiles.state(0).flags & nodes.Ability.RUNNING)
        self.assertTrue(missiles.state(5).active)
        self.assertGreater(missiles.state(5).volleys, 0)
        self.cast(run, caster, target, buttons=16)
        self.assertFalse(missiles.state(0).flags & nodes.Ability.RUNNING)
        self.assertGreater(missiles.vars[61].value(), 0)
        with patch.object(run.match.combat.projectiles, "fired",
                          wraps=run.match.combat.projectiles.fired) as fired:
            for _ in range(70):
                self.step(run, caster, target, buttons=16)
            self.assertEqual(fired.call_count, 0)
        self.assertFalse(missiles.state(0).flags & nodes.Ability.RUNNING)

    def test_wall_range_and_team_block_damage_and_stun_in_both_forms(self):
        for mech in (False, True):
            for case in ("wall", "range", "ally"):
                with self.subTest(mech=mech, case=case):
                    run, caster, target = self.scenario(mech, case)
                    before = self.vitals(run, target).total
                    self.step(run, caster, target, cast=16)
                    for _ in range(90):
                        self.step(run, caster, target)
                        self.assertFalse(run.match.combat.is_stunned(target))
                    self.assertEqual(self.vitals(run, target).total, before)

    def test_authored_duration_mode_branch_refresh_and_body_replacement_cleanup(self):
        for mech in (False, True):
            with self.subTest(mech=mech):
                run, caster, target = self.scenario(mech)
                fight = run.match.combat
                values = dict(graph.mode_variables(run.match.game_map.mode_guid) or {})
                values[combat.FLASHBANG_MODE_FLAG] = True
                with patch.object(graph, "mode_variables", return_value=values):
                    self.cast(run, caster, target)
                    self.assertEqual(fight.stuns[target.body] - fight.tick, math.ceil(1.7 / combat.TICK))
                    old = fight.stuns[target.body]
                    for _ in range(10):
                        self.step(run, caster, target)
                    body = next(b for b in fight._targets() if b.owner is target)
                    fight._hit_effect(body, combat.FLASHBANG_EFFECT)
                    self.assertEqual(fight.stuns[target.body], old + 10)
                retired = target.body
                self.assertTrue(run.match.switch_hero(target, target.hero))
                self.assertNotIn(retired, fight.stuns)
                self.assertFalse(fight.is_stunned(target))

    def test_paused_route_keeps_target_and_resumes_without_stun_time_catchup(self):
        def walk(steps):
            actor = make_bot([(0, 0, 0), (0, 0, 30)], index=0)
            actor.wait = 0
            for tick in range(1, 11):
                actor.step(tick, combat.TICK)
            old, target = actor.position, actor.target
            actor.step(20, combat.TICK, paused_until=31)
            self.assertEqual(actor.position, old)
            self.assertEqual(actor.target, target)
            for tick in steps:
                actor.step(tick, combat.TICK, paused_until=31)
            return actor.position, actor.velocity, actor.target

        self.assertEqual(walk(range(21, 41)), walk([40]))


if __name__ == "__main__":
    unittest.main()
