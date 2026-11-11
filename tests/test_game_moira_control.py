"""Actual McCree Flashbang input versus Moira phases; reconstructed server control."""

import unittest
from unittest.mock import patch

import test_game_moira as fixtures
from test_game_combat import Run, aim

from ow174.game import combat, moira
from ow174.game.commands import Command
from ow174.game.content import heroes
from ow174.game.script import expr, graph, nodes
from ow174.game.script.driver import ULT_CHARGE, ULT_COST
from ow174.game.world import HEALTH_POOL

MCCREE = 0x02E0000000000042


class MoiraControlTests(unittest.TestCase):
    def arena(self):
        run = Run(names=('Flashbang', 'Moira'))
        match = run.match
        match.bots.clear()
        match._pending_bot_scripts.clear()
        caster, target = run.players
        match.switch_hero(caster, heroes()[MCCREE])
        match.switch_hero(target, heroes()[fixtures.MOIRA])
        target.team = 1 - caster.team
        x, y, z = caster.mover.position
        target.mover.position = [x, y, z + 3]
        for _ in range(20):
            self.step(run, caster, target)
        target.body_script.component.set(12918, 0)
        bodies = {t.owner: t for t in match.combat._targets()}
        health = [match.combat.vitals_of(bodies[p]) for p in (caster, target)]
        return run, caster, target, health

    @staticmethod
    def step(run, caster, target, *, cast=0, buttons=0, forward=0):
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
                y, p = aim(match.combat._eye(player), match.combat._eye(caster))
                command = Command(run.frame, yaw=y, pitch=p, buttons=buttons, forward=forward)
            player.take_commands([command])

    def cast(self, run, caster, target, *, buttons=0):
        self.step(run, caster, target, cast=16, buttons=buttons)
        for _ in range(50):
            if run.match.combat.is_stunned(target):
                return run.match.combat.stuns[target.body]
            self.step(run, caster, target, buttons=buttons)
        self.fail('actual Flashbang release applied no control')

    def ultimate(self, run, caster, target):
        target.body_script.set_ult_charge(target.body_script.component.entity_var(ULT_COST).value())
        self.step(run, caster, target, buttons=32)
        for _ in range(60):
            instance = next(i for i in target.body_script.component.instances.values()
                            if i.graph.index == 0x1007)
            if instance.states.get(20) is not None and instance.states[20].active:
                return instance
            self.step(run, caster, target)
        self.fail('actual charged ultimate did not enter active phase')

    def launch_off_axis(self, *, ally=False, wall=False, offset=2):
        from test_game_moira_fan import MoiraFanTests

        run, caster, target, health = MoiraFanTests().arena(
            distance=7, offset=offset, ally=ally, wall=wall)
        fight = run.match.combat
        for frame in range(50):
            run.step({caster: (0, 0, 16 if frame == 0 else 0)})
            if fight.rockets:
                return run, target, health[1], fight.rockets[0]
        self.fail('Flashbang input did not produce a projectile')

    def test_real_flashbang_expiry_reaches_off_axis_enemy_once(self):
        for options, expected in (({}, 25), ({'ally': True}, 0),
                                  ({'wall': True}, 0), ({'offset': 5}, 0)):
            with self.subTest(options=options):
                run, target, health, rocket = self.launch_off_axis(**options)
                fight = run.match.combat
                self.assertEqual(rocket.gravity, 20)
                self.assertTrue(rocket.detonate_on_expiry)
                self.assertAlmostEqual(rocket.velocity[1], 1.4981285, delta=1e-6)
                self.assertAlmostEqual(rocket.left, 7 / 30)
                before = health.total
                for _ in range(20):
                    run.step({})
                self.assertNotIn(rocket, fight.rockets)
                self.assertEqual(before - health.total, expected)
                self.assertEqual(fight.is_stunned(target), bool(expected))
                if expected:
                    self.assertAlmostEqual(rocket.flown, 7 / 30)
                for _ in range(60):
                    run.step({})
                self.assertEqual(before - health.total, expected)

    def test_real_flashbang_contiguous_and_gap_flight_match(self):
        samples = []
        for advances in (range(1, 9), (8,)):
            run, _, _, rocket = self.launch_off_axis()
            run.step({})
            start = run.frame
            for advance in advances:
                tick = start + advance
                run.match.update(tick * combat.TICK, tick)
            samples.append((rocket.position, rocket.velocity, rocket.left, rocket.flown))
        self.assertEqual(*samples)

    def test_actual_flashbang_damage_and_resource_duration_branches(self):
        for special, expected in ((False, 50), (True, 107)):
            with self.subTest(special=special):
                run, caster, target, health = self.arena()
                original = graph.mode_variables
                with patch.object(graph, 'mode_variables', side_effect=lambda mode, lookup=original,
                                  selected=special: {**(lookup(mode) or {}),
                                                     combat.FLASHBANG_MODE_FLAG: int(selected)}):
                    end = self.cast(run, caster, target)
                self.assertEqual(health[1].total, 175)
                self.assertEqual(end - run.match.combat.tick, expected)
                self.assertTrue(run.match.combat.is_stunned(target, tick=end - 1))
                self.assertFalse(run.match.combat.is_stunned(target, tick=end))

    def test_stun_releases_grasp_blocks_movement_and_fade_then_attack_recovers(self):
        run, caster, target, health = self.arena()
        initial = health[0].total
        for _ in range(20):
            self.step(run, caster, target, buttons=2)
        self.assertLess(health[0].total, initial)
        weapon = next(i for i in target.body_script.component.instances.values() if i.graph.index == 0xF76)
        search = weapon.state(106)
        self.assertEqual(search.payload()["targets"], ((caster.body, 0),))
        end = self.cast(run, caster, target, buttons=2)
        self.assertFalse(search.active)
        self.assertEqual(expr.to_entity(weapon.evaluate(weapon.state(22).node.field("m_FA3ECC43"))), 0)
        before = health[0].total, tuple(target.mover.position)
        while run.frame < end - 1:
            self.step(run, caster, target, buttons=2 | 8 | 32 | 0x40, forward=127)
            self.assertEqual(health[0].total, before[0])
            self.assertEqual(tuple(target.mover.position), before[1])
            self.assertFalse(target.body_script.component.held)
            self.assertFalse(moira.fading(target))
        for _ in range(30):
            self.step(run, caster, target, buttons=2, forward=127)
        self.assertLess(health[0].total, before[0])
        self.assertNotEqual(tuple(target.mover.position), before[1])

    def test_stun_interrupts_coalescence_and_cannot_refund_or_resume_it(self):
        run, caster, target, health = self.arena()
        instance = self.ultimate(run, caster, target)
        end = self.cast(run, caster, target)
        self.assertFalse(instance.states[20].active)
        self.assertFalse(instance.states[0].flags & nodes.Ability.RUNNING)
        before = health[0].total, health[1].total
        while run.frame < end + 30:
            self.step(run, caster, target)
        self.assertEqual((health[0].total, health[1].total), before)
        self.assertEqual(target.body_script.component.entity_var(ULT_CHARGE).value(), 0)

    def test_already_active_fade_rejects_real_flashbang_damage_and_control(self):
        run, caster, target, health = self.arena()
        self.step(run, caster, target, buttons=8)
        self.assertTrue(moira.fading(target))
        fight = run.match.combat
        with patch.object(fight, '_apply_hit', wraps=fight._apply_hit) as hits:
            self.step(run, caster, target, cast=16)
            for _ in range(20):
                self.step(run, caster, target)
        self.assertTrue(any(call.args[1].owner is target and
                            call.args[2].effect == combat.FLASHBANG_EFFECT for call in hits.call_args_list))
        self.assertEqual(health[1].total, 200)
        self.assertFalse(fight.is_stunned(target))
        self.assertNotIn(target.body, fight.stuns)

    def test_stun_cancels_orb_selection_without_late_release(self):
        run, caster, target, _health = self.arena()
        self.step(run, caster, target, buttons=16)
        ability = next(i for i in target.body_script.component.instances.values()
                       if i.graph.index == 0x10E4).states[0]
        self.assertTrue(ability.flags & nodes.Ability.RUNNING)
        end = self.cast(run, caster, target)
        while run.frame < end + 30:
            self.step(run, caster, target, buttons=1)
        self.assertFalse(ability.flags & nodes.Ability.RUNNING)
        self.assertEqual(run.match.combat.moira_orbs, [])

    def test_launched_orb_continues_world_clock_damage_while_caster_is_stunned(self):
        run, caster, target, health = self.arena()
        self.step(run, caster, target, buttons=16)
        for _ in range(40):
            self.step(run, caster, target, buttons=2)
            if run.match.combat.moira_orbs:
                break
        orb = run.match.combat.moira_orbs[-1]
        self.cast(run, caster, target)
        before = health[0].total, orb.energy, orb.flown
        for _ in range(10):
            self.step(run, caster, target)
        self.assertTrue(run.match.combat.is_stunned(target))
        self.assertAlmostEqual(before[0] - health[0].total, 8, places=4)
        self.assertAlmostEqual(before[1] - orb.energy, 8, places=4)
        self.assertAlmostEqual(orb.flown - before[2], .16, places=4)

    def test_death_and_body_retirement_discard_stun_identity(self):
        for retire in (False, True):
            with self.subTest(retire=retire):
                run, caster, target, health = self.arena()
                self.cast(run, caster, target)
                old = target.body
                if not retire:
                    health[1].now[HEALTH_POOL] = 1
                    for _ in range(30):
                        self.step(run, caster, target, cast=1)
                    self.assertTrue(health[1].dead)
                    self.assertNotIn(old, run.match.combat.stuns)
                self.assertTrue(run.match.switch_hero(target, heroes()[fixtures.MOIRA]))
                self.assertNotIn(old, run.match.combat.stuns)
                self.assertFalse(run.match.combat.is_stunned(target))
