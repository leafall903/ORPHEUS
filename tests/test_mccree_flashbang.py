"""Resource-backed Flashbang control reconstruction through real player input."""

import math
import unittest
from unittest.mock import patch

import test_mccree_combat as melee_tests
from test_bots import make_bot
from test_game_combat import aim

from ow174.game import combat, npc
from ow174.game.commands import Command
from ow174.game.content import heroes
from ow174.game.script import graph, nodes


class FlashbangTests(unittest.TestCase):
    arena = melee_tests.McCreeMeleeTests.arena
    health = melee_tests.McCreeMeleeTests.health

    @staticmethod
    def aim_at_caster(fight, player, caster):
        return aim(fight._eye(player), fight._eye(caster))

    def step(self, run, caster, target, *, cast=0, target_buttons=0, forward=0):
        run.frame += 1
        match = run.match
        match.update(run.frame * combat.TICK, run.frame)
        point = target.mover.position if hasattr(target, "mover") else target.position
        yaw, pitch = aim(match.combat._eye(caster), (point[0], point[1] + 1, point[2]))
        for player in run.players:
            player.client.queue_entities(match.remote_movements(player))
            if player is caster:
                command = Command(run.frame, yaw=yaw, pitch=pitch, buttons=cast)
            else:
                y, p = self.aim_at_caster(match.combat, player, caster)
                command = Command(run.frame, yaw=y, pitch=p, buttons=target_buttons, forward=forward)
            player.take_commands([command])

    def cast(self, run, caster, target, *, target_buttons=0, forward=0):
        self.step(run, caster, target, cast=16, target_buttons=target_buttons, forward=forward)
        for _ in range(50):
            if run.match.combat.is_stunned(target):
                return
            self.step(run, caster, target, target_buttons=target_buttons, forward=forward)
        self.fail("Actual Flashbang input did not apply control")

    def test_actual_flashbang_damages_and_stuns_player_and_npc_until_exclusive_deadline(self):
        for is_npc in (False, True):
            with self.subTest(npc=is_npc):
                run, caster, target = self.arena(npc=is_npc)
                before = self.health(run, target)
                self.cast(run, caster, target)
                fight = run.match.combat
                entity = target.entity if is_npc else target.body
                self.assertEqual(self.health(run, target), before - 25)
                end = fight.stuns[entity]
                self.assertEqual(end - fight.tick, 50)
                self.assertTrue(fight.is_stunned(target, tick=end - 1))
                self.assertFalse(fight.is_stunned(target, tick=end))
                while run.frame < end:
                    self.step(run, caster, target)
                self.assertNotIn(entity, fight.stuns)

    def test_orisa_fortify_blocks_stun_but_keeps_flashbang_damage(self):
        for active, damage in ((True, 10), (False, 20)):
            with self.subTest(active=active):
                run, caster, orisa = self.arena(distance=3)
                position = tuple(orisa.mover.position)
                self.assertTrue(run.match.switch_hero(orisa, heroes()[0x02E000000000013E]))
                orisa.mover.position[:] = position
                orisa.mover.body = None
                for _ in range(25):
                    self.step(run, caster, orisa)
                self.step(run, caster, orisa, target_buttons=8)
                instance = next(i for i in orisa.body_script.component.instances.values()
                                if i.graph.index == 0x0CF4)
                state = instance.states[4]
                self.assertTrue(state.active)
                if not active:
                    for _ in range(260):
                        if not state.active:
                            break
                        self.step(run, caster, orisa)
                    self.assertFalse(state.active)
                before = self.health(run, orisa)
                self.step(run, caster, orisa, cast=16)
                for _ in range(50):
                    if self.health(run, orisa) < before:
                        break
                    self.step(run, caster, orisa)
                self.assertAlmostEqual(self.health(run, orisa), before - damage, places=4)
                self.assertEqual(state.active, active)
                self.assertEqual(run.match.combat.is_stunned(orisa), not active)

    def test_player_cannot_fire_move_or_start_sprint_then_recovers(self):
        run, caster, target = self.arena(distance=3)
        self.cast(run, caster, target)
        before = self.health(run, caster)
        position = tuple(target.mover.position)
        end = run.match.combat.stuns[target.body]
        while run.frame < end - 1:
            self.step(run, caster, target, target_buttons=1 | 8 | 0x40, forward=127)
            self.assertEqual(tuple(target.mover.position), position)
            self.assertEqual(self.health(run, caster), before)
            self.assertFalse(target.body_script.component.held)
        for _ in range(30):
            self.step(run, caster, target, target_buttons=1, forward=127)
        self.assertNotEqual(tuple(target.mover.position), position)
        self.assertLess(self.health(run, caster), before)

    def test_existing_sprint_channel_is_interrupted(self):
        run, caster, target = self.arena(distance=3)
        for _ in range(3):
            self.step(run, caster, target, target_buttons=8, forward=127)
        sprint = next(s for i in target.body_script.component.instances.values()
                      for s in i.states.values() if isinstance(s, nodes.Ability) and s.buttons()[0] == 35)
        self.assertTrue(sprint.flags & nodes.Ability.RUNNING)
        self.cast(run, caster, target, target_buttons=8, forward=127)
        self.assertFalse(sprint.flags & nodes.Ability.RUNNING)
        for _ in range(20):
            self.step(run, caster, target, target_buttons=8)
            self.assertFalse(sprint.flags & nodes.Ability.RUNNING)

    def test_wall_allies_and_distant_targets_receive_neither_damage_nor_control(self):
        for is_npc in (False, True):
            for options in ({"wall": True}, {"ally": True}, {"distance": 20}):
                with self.subTest(npc=is_npc, options=options):
                    run, caster, target = self.arena(npc=is_npc, **options)
                    before = self.health(run, target)
                    self.step(run, caster, target, cast=16)
                    for _ in range(100):
                        self.step(run, caster, target)
                        self.assertFalse(run.match.combat.is_stunned(target))
                    self.assertEqual(self.health(run, target), before)

    def test_duration_mode_branch_and_repeat_hit_refresh_are_bounded(self):
        run, caster, target = self.arena()
        fight = run.match.combat
        values = dict(graph.mode_variables(run.match.game_map.mode_guid) or {})
        values[combat.FLASHBANG_MODE_FLAG] = True
        with patch.object(graph, "mode_variables", return_value=values):
            self.cast(run, caster, target)
            self.assertEqual(fight.stuns[target.body] - fight.tick, math.ceil(1.7 / combat.TICK))
            old = fight.stuns[target.body]
            for _ in range(10):
                self.step(run, caster, target)
            body = next(t for t in fight._targets() if t.owner is target)
            fight._hit_effect(body, combat.FLASHBANG_EFFECT)
            self.assertEqual(fight.stuns[target.body], old + 10)
            fight._hit_effect(body, None)
            self.assertEqual(fight.stuns[target.body], old + 10)

    def test_body_replacement_and_manual_npc_removal_retire_control(self):
        run, caster, target = self.arena()
        self.cast(run, caster, target)
        old = target.body
        self.assertTrue(run.match.switch_hero(target, target.hero))
        self.assertNotIn(old, run.match.combat.stuns)
        self.assertFalse(run.match.combat.is_stunned(target))
        run, caster, target = self.arena(npc=True)
        target.spawner = {**target.spawner, "manual_spawn": True}
        self.cast(run, caster, target)
        npc.remove_spawned(run.match)
        self.assertNotIn(target.entity, run.match.combat.stuns)

    def test_paused_npc_route_keeps_target_and_resumes_without_stun_time_catchup(self):
        def walk(advances):
            actor = make_bot([(0, 0, 0), (0, 0, 30)], index=0)
            actor.wait = 0
            for tick in range(1, 11):
                actor.step(tick, combat.TICK)
            position, target = actor.position, actor.target
            actor.step(20, combat.TICK, paused_until=31)
            self.assertEqual(actor.position, position)
            self.assertEqual(actor.target, target)
            self.assertEqual(actor.velocity, (0, 0, 0))
            for tick in advances:
                actor.step(tick, combat.TICK, paused_until=31)
            return actor.position, actor.velocity, actor.target

        self.assertEqual(walk(range(21, 41)), walk([40]))

    def test_lethal_hit_retires_stun_but_preserves_retained_corpse(self):
        run, caster, target = self.arena(npc=True)
        target.retain_corpse = True
        for player in run.players:
            player.client.queue_bot_death = lambda *args: None
            player.client.queue_bot_death_update = lambda *args: None
        self.cast(run, caster, target)
        fight = run.match.combat
        body = next(item for item in fight._targets() if item.owner is target)
        fight._damage(caster, body, 10000, False, None, run.frame)
        self.assertTrue(target.dead)
        self.assertNotIn(target.entity, fight.stuns)
        for _ in range(70):
            self.step(run, caster, target)
        self.assertIn(target, run.match.bots)
        self.assertTrue(target.dead)
        self.assertFalse(any(owner is target for _, owner in fight.respawns))
