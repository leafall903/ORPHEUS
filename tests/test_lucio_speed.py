"""Actual0267 ->07C7 speed states, authored modifiers and real player motion."""

import unittest
from dataclasses import replace

import test_lucio_combat as fixtures

from ow174.game import combat
from ow174.game.collision import World
from ow174.game.commands import Command
from ow174.game.content import heroes
from ow174.game.mover import f32, statescript_mods
from ow174.game.script import graph


@unittest.skipUnless(graph.graph(0x07C7), "requires actual07C7 controller")
class LucioSpeedTests(unittest.TestCase):
    def scenario(self):
        helper = fixtures.LucioCombatTests()
        run, source, enemy, ally = helper.scenario()
        enemy.mover.position[:] = (50, 1, 50)
        ally.mover.position[:] = (2, 1, 0)
        return helper, run, source, enemy, ally

    def step(self, run, *, buttons=None, moving=False):
        buttons = buttons or {}
        run.frame += 1
        run.match.update(run.frame * combat.TICK, run.frame)
        for player in run.players:
            player.client.queue_entities(run.match.remote_movements(player))
            forward = moving is True or (bool(moving) and player in moving)
            player.take_commands([Command(run.frame, buttons=buttons.get(player, 0),
                                          forward=127 if forward else 0)])

    def advance(self, run, frames, *, moving=False):
        for _ in range(frames):
            self.step(run, moving=moving)

    def scalar(self, owner):
        controllers = [i for i in owner.body_script.component.instances.values() if i.graph.index == 0x07C7]
        self.assertEqual(len(controllers), 1)
        controller = controllers[0]
        mods = statescript_mods(owner.body_script.component)
        self.assertEqual(mods[(controller.id, 9, 0)][:2], (0, 0))  # speed/add
        var = controller.vars.get(1002)
        value = var.value() if var is not None else 0.0  # no assignment before the initial wait
        self.assertEqual(mods[(controller.id, 9, 0)][2], value)
        return value

    def test_default_speed_drives_actual_self_ally_motion_and_excludes_enemy(self):
        _, run, source, enemy, ally = self.scenario()
        self.advance(run, 40)
        for actor in (source, ally):
            self.assertEqual(self.scalar(actor), 0.25)
        self.assertFalse(any(i.graph.index == 0x07C7 for i in enemy.body_script.component.instances.values()))
        self.advance(run, 30, moving=True)
        for actor in (source, ally):
            self.assertAlmostEqual(actor.mover.velocity[2], 5.5 * 1.25, delta=0.001)
        self.assertAlmostEqual(enemy.mover.velocity[2], 5.5, delta=0.001)

    def test_authored_point_two_second_delay_precedes_next_pose_speed_change(self):
        _, run, source, _, ally = self.scenario()
        self.advance(run, 12, moving=True)
        for actor in (source, ally):
            self.assertEqual(self.scalar(actor), 0)
            self.assertAlmostEqual(actor.mover.velocity[2], 5.5, delta=0.001)
        self.step(run, moving=True)
        for actor in (source, ally):
            self.assertEqual(self.scalar(actor), 0.25)
            self.assertAlmostEqual(actor.mover.velocity[2], 5.5, delta=0.001)
        self.step(run, moving=True)
        for actor in (source, ally):
            self.assertAlmostEqual(actor.mover.velocity[2], 5.5 * 1.25, delta=0.001)

    def test_amp_speed_reaches_authored_sixty_percent_and_returns_without_cooldown_bypass(self):
        _, run, source, _, ally = self.scenario()
        self.advance(run, 40)
        self.step(run, buttons={source: 16})
        self.advance(run, 45, moving=True)
        for actor in (source, ally):
            self.assertEqual(self.scalar(actor), f32(0.6))
            self.assertAlmostEqual(actor.mover.velocity[2], 5.5 * 1.6, delta=0.001)
        self.advance(run, 220, moving=True)
        for actor in (source, ally):
            self.assertEqual(self.scalar(actor), 0.25)
            self.assertAlmostEqual(actor.mover.velocity[2], 5.5 * 1.25, delta=0.001)
        for offset in range(40):
            self.step(run, buttons={source: 16 if offset % 2 else 0})
            self.assertEqual(self.scalar(ally), 0.25)

    def test_crossfade_healing_removes_speed_then_toggling_back_restores_it(self):
        _, run, source, _, ally = self.scenario()
        self.advance(run, 40)
        self.step(run, buttons={source: 8})
        self.advance(run, 40, moving=True)
        for actor in (source, ally):
            self.assertEqual(self.scalar(actor), 0)
            self.assertAlmostEqual(actor.mover.velocity[2], 5.5, delta=0.001)
        self.step(run, buttons={source: 8})
        self.advance(run, 40, moving=True)
        for actor in (source, ally):
            self.assertEqual(self.scalar(actor), 0.25)
            self.assertAlmostEqual(actor.mover.velocity[2], 5.5 * 1.25, delta=0.001)

    def test_multiple_sources_share_one_controller_and_choose_amp_without_stacking(self):
        _, run, source, other, ally = self.scenario()
        run.match.switch_hero(other, heroes()[fixtures.LUCIO])
        other.team = source.team
        other.mover.position[:] = (3, 1, 0)
        self.advance(run, 40)
        self.assertEqual(self.scalar(ally), 0.25)
        self.assertEqual(ally.body_script.component.entity_var(4734).value(), 2)
        self.step(run, buttons={source: 16, other: 16})
        self.advance(run, 45, moving=True)
        self.assertEqual(self.scalar(ally), f32(0.6))
        self.assertEqual(ally.body_script.component.entity_var(6591).value(), 2)
        self.assertAlmostEqual(ally.mover.velocity[2], 5.5 * 1.6, delta=0.001)

    def test_loss_of_range_or_source_retirement_clears_speed_without_removing_controller(self):
        for retire in (False, True):
            with self.subTest(retire=retire):
                _, run, source, _, ally = self.scenario()
                self.advance(run, 40)
                self.assertEqual(self.scalar(ally), 0.25)
                if retire:
                    run.match.switch_hero(source, heroes()[0x02E000000000006E])
                    source.mover.position[:] = (50, 1, -50)
                    self.advance(run, 30, moving={ally})
                else:
                    self.advance(run, 250, moving={ally})  # actually walk out of the aura
                self.assertEqual(self.scalar(ally), 0)
                self.assertAlmostEqual(ally.mover.velocity[2], 5.5, delta=0.001)

    def test_source_death_clears_normal_or_amp_speed_on_surviving_ally(self):
        for amp in (False, True):
            with self.subTest(amp=amp):
                _, run, source, enemy, ally = self.scenario()
                self.advance(run, 40)
                if amp:
                    self.step(run, buttons={source: 16})
                    self.advance(run, 45)
                self.assertEqual(self.scalar(ally), f32(0.6) if amp else 0.25)
                fight = run.match.combat
                target = next(t for t in fight._targets() if t.owner is source)
                fight._damage(enemy, target, 1000, False, None, run.frame)  # declared death precondition
                self.assertTrue(fight.is_dead(source))
                self.advance(run, 30, moving={ally})
                self.assertEqual(self.scalar(ally), 0)
                self.assertAlmostEqual(ally.mover.velocity[2], 5.5, delta=0.001)

    def test_wall_team_and_ffa_exclusion_do_not_grant_speed(self):
        for condition in ("wall", "enemy", "ffa"):
            with self.subTest(condition=condition):
                _, run, source, _, ally = self.scenario()
                ally.mover.position[:] = (0, 1, 4)
                if condition == "enemy":
                    ally.team = 1
                elif condition == "ffa":
                    run.match.game_map = replace(run.match.game_map, free_for_all=True)
                else:
                    run.match.collision_world = World([
                        (-50, 1, -50, -50, 1, 50, 50, 1, 50),
                        (-50, 1, -50, 50, 1, 50, 50, 1, -50),
                        (-5, 1, 2, 5, 1, 2, 5, 6, 2),
                        (-5, 1, 2, 5, 6, 2, -5, 6, 2),
                    ], name="explicit Crossfade speed wall")
                self.advance(run, 40)
                self.assertEqual(self.scalar(source), 0.25)
                self.assertFalse(any(i.graph.index == 0x07C7
                                     for i in ally.body_script.component.instances.values()))
