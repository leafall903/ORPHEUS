"""Actual Sombra EMP versus Wrecking Ball ability input."""

import unittest
from dataclasses import replace

import test_wrecking_ball_combat as scenes
from test_game_combat import KANEZAKA_DM, Run, aim
from test_wrecking_ball_fireball import relocate
from test_wrecking_ball_grapple import hold
from test_wrecking_ball_grapple import scene as grapple_scene
from test_wrecking_ball_piledriver import lift, pile_instance

from ow174.game import ball, body_vm_snapshot, combat, sombra_emp
from ow174.game.collision import World
from ow174.game.commands import Command
from ow174.game.content import heroes
from ow174.game.script.driver import ULT_CHARGE, ULT_COST


class BallEmpTests(unittest.TestCase):
    def two_sombras(self):
        run = Run(KANEZAKA_DM, ('Ball', 'First Sombra', 'Second Sombra'))
        player, first, second = run.players
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        player.team, first.team, second.team = 0, 1, 1
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        for owner, hero in ((player, ball.HERO), (first, sombra_emp.SOMBRA), (second, sombra_emp.SOMBRA)):
            run.match.switch_hero(owner, heroes()[hero])
        x, y, z = player.mover.position
        relocate(run, first, (x, y, z + 6))
        relocate(run, second, (x + 6, y, z))
        for _ in range(60):
            run.step({})
        for owner in run.players:
            script = owner.body_script
            script.ult_seconds = 0
            script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        return run, player, first, second

    def test_overlapping_emp_preserves_first_window_and_expires_from_second_hit(self):
        run, player, first, second = self.two_sombras()
        script = player.body_script
        charge = script.component.entity_var(ULT_CHARGE)
        cost = charge.value()
        self.cast_without_ball_commands(run, first)
        first_impact = run.frame
        run.step({})
        first_child, = (i for i in script.component.instances.values() if i.graph.index == sombra_emp.CHILD)
        first_effect, = (i for i in script.component.instances.values() if i.graph.index == sombra_emp.EFFECT)
        self.assertEqual(first_child.source_entity(), first.body)
        self.assertEqual(first_effect.source_entity(), first.body)

        run.frame += 65
        self.cast_without_ball_commands(run, second)
        second_impact = run.frame
        self.assertTrue(first_child.teardown)
        self.assertTrue(first_effect.teardown)
        second_child, = (i for i in script.component.instances.values() if i.graph.index == sombra_emp.CHILD)
        self.assertEqual(second_child.source_entity(), second.body)

        # This packet arrives after the replacement, but its frame is in the
        # first EMP window only. It must still be rejected.
        player.take_commands([Command(first_impact + 10, buttons=32)])
        self.assertEqual(charge.value(), cost)
        self.assertEqual(script.emp_suppressed_buttons, 32)
        player.take_commands([Command(second_impact + 1, buttons=32)])
        second_effect, = (i for i in script.component.instances.values()
                          if i.graph.index == sombra_emp.EFFECT)
        self.assertEqual(second_effect.source_entity(), second.body)

        run.frame = first_impact + 314
        run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        self.assertTrue(sombra_emp.locked(script, run.frame))
        self.assertFalse(second_child.teardown)
        player.take_commands([Command(run.frame, buttons=32)])
        self.assertEqual(charge.value(), cost)

        run.frame = second_impact + 314
        run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        self.assertFalse(sombra_emp.locked(script, run.frame))
        self.assertTrue(second_child.teardown)
        self.assertTrue(second_effect.teardown)
        player.take_commands([Command(run.frame, buttons=32)])
        self.assertEqual(charge.value(), cost)
        run.step({})
        run.step({player: (0, 0, 32)})
        self.assertEqual(charge.value(), 0)

    def test_separated_emps_reject_delayed_input_without_locking_the_gap(self):
        run, player, first, second = self.two_sombras()
        script = player.body_script
        charge = script.component.entity_var(ULT_CHARGE)
        cost = charge.value()
        start_frame = script.frame
        self.cast_without_ball_commands(run, first)
        first_impact = run.frame
        gap = first_impact + 314
        run.frame = gap
        run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        self.cast_without_ball_commands(run, second)
        second_impact = run.frame
        self.assertEqual(script.frame, start_frame)

        player.take_commands([Command(first_impact + 10, buttons=32)])
        self.assertEqual(charge.value(), cost)
        self.assertEqual(script.emp_suppressed_buttons, 32)
        player.take_commands([Command(gap), Command(gap + 1, buttons=8)])
        self.assertTrue(ball.rolling(player))
        self.assertFalse(hasattr(script, 'emp_previous_windows'))

        player.take_commands([Command(second_impact + 1, buttons=32)])
        self.assertEqual(charge.value(), cost)
        run.frame = second_impact + 314
        run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        player.take_commands([Command(run.frame, buttons=32)])
        self.assertEqual(charge.value(), cost)
        run.step({})
        run.step({player: (0, 0, 32)})
        self.assertEqual(charge.value(), 0)

    def scene(self, distance=6):
        run, player, sombra, target, own = scenes.BallCombatTests().scenario(distance=distance)
        position = tuple(sombra.mover.position)
        run.match.switch_hero(sombra, heroes()[sombra_emp.SOMBRA])
        relocate(run, sombra, position)
        for owner in (player, sombra):
            script = owner.body_script
            script.ult_seconds = 0
            script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        for _ in range(40):
            run.step({})
        return run, player, sombra, target, own

    def cast(self, run, sombra, held=None):
        held = held or {}
        run.step({**held, sombra: (-32768, 0, 32)})
        instances = sombra.body_script.component.instances.values()
        graph = next(i for i in instances if i.graph.index == sombra_emp.EMP)
        for _ in range(65):
            run.step(held)
            if graph.state(17).counter:
                break
        self.assertEqual(graph.state(17).counter, 1)
        self.assertEqual(graph.vars[1648].value(), 15)
        self.assertEqual(sombra.body_script.component.entity_var(ULT_CHARGE).value(), 0)

    def test_actual_emp_drains_ball_shields_without_spill_refill_or_charge(self):
        for base_shields in (0, 50):
            with self.subTest(base_shields=base_shields):
                run, player, sombra, _, own = self.scene()
                if base_shields:
                    # Ball has no authored base shield pool. Stage a base pool
                    # to exercise both target pools through the same actual EMP.
                    own.most[combat.SHIELDS_POOL] = own.now[combat.SHIELDS_POOL] = base_shields
                    run.match.combat._sync_body_health(player, own)
                run.step({player: (0, 0, 16)})
                for _ in range(8):
                    run.step({})
                shield = ball.instance(player, 0x1566).states[6]
                self.assertTrue(shield.active)
                self.assertEqual(own.temporary_shields, 175)
                self.assertEqual(own.total, 775 + base_shields)
                health = own.now[combat.HEALTH_POOL]
                armor = own.now[combat.ARMOUR_POOL]
                activation = own.adaptive_activation
                self.cast(run, sombra)
                for _ in range(4):
                    run.step({})
                child, = (i for i in player.body_script.component.instances.values()
                          if i.graph.index == sombra_emp.CHILD)
                self.assertEqual(child.states[child.graph.node(9).state].counter, 1)
                self.assertEqual(own.total, 600)
                self.assertEqual(own.now[combat.HEALTH_POOL], health)
                self.assertEqual(own.now[combat.ARMOUR_POOL], armor)
                self.assertEqual(own.now[combat.SHIELDS_POOL], 0)
                self.assertEqual(own.temporary_shields, 0)
                self.assertTrue(shield.active)
                for _ in range(45):
                    run.step({player: (0, 0, 16)})
                    self.assertEqual(own.temporary_shields, 0)
                    self.assertEqual(own.adaptive_activation, activation)
                self.assertEqual(sombra.body_script.component.entity_var(ULT_CHARGE).value(), 0)

    def test_actual_emp_rejects_ball_abilities_without_replaying_held_ultimate(self):
        run, player, sombra, _, own = self.scene()
        self.cast(run, sombra)
        script = player.body_script
        child, = (i for i in script.component.instances.values() if i.graph.index == sombra_emp.CHILD)
        self.assertEqual(child.source_entity(), sombra.body)
        cost = script.component.entity_var(ULT_COST).value()
        charge = script.component.entity_var(ULT_CHARGE)
        run.step({player: (0, 0, 8)})
        self.assertTrue(sombra_emp.locked(script, run.frame))
        self.assertFalse(ball.rolling(player))
        self.assertEqual(child.vars[567].value(), (5,))
        self.assertEqual(sum(i.graph.index == sombra_emp.EFFECT
                             for i in script.component.instances.values()), 1)
        run.step({player: (0, 0, 16)})
        self.assertEqual(own.temporary_shields, 0)
        fight = run.match.combat
        angles = aim(fight._eye(player), (sombra.mover.position[0], sombra.mover.position[1] + 0.6,
                                         sombra.mover.position[2]))
        run.step({player: (*angles, 1)})
        self.assertEqual(ball.instance(player, 0x1514).vars[53].value(), 79)
        target = next(t for t in fight._targets() if t.owner is sombra)
        self.assertEqual(fight.vitals_of(target).total, 195)
        run.step({player: (0, 0, 32)})
        self.assertTrue(body_vm_snapshot.export_body_vm(script))
        for _ in range(340):
            run.step({player: (0, 0, 32)})
            self.assertEqual(charge.value(), cost)
            if not sombra_emp.locked(script, run.frame):
                break
        self.assertFalse(sombra_emp.locked(script, run.frame))
        self.assertFalse(any(i.graph.index == sombra_emp.EFFECT for i in script.component.instances.values()))
        for _ in range(5):
            run.step({player: (0, 0, 32)})
        self.assertEqual(charge.value(), cost)
        run.step({})
        run.step({player: (0, 0, 32)})
        self.assertEqual(charge.value(), 0)

    def test_actual_emp_cuts_attached_grapple_and_starts_its_cooldown(self):
        run, player, sombra, _, _ = grapple_scene()
        x, y, z = player.mover.position
        run.match.switch_hero(sombra, heroes()[sombra_emp.SOMBRA])
        relocate(run, sombra, (x + 6, y, z))
        for _ in range(40):
            run.step({})
        script = sombra.body_script
        script.ult_seconds = 0
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        hold(run, player, 5)
        fight = run.match.combat
        self.assertTrue(fight.ball_grapples[player.body].attached)
        self.cast(run, sombra, {player: (0, 0, 2)})
        hold(run, player, 2)
        self.assertTrue(sombra_emp.locked(player.body_script, run.frame))
        self.assertNotIn(player.body, fight.ball_grapples)
        self.assertGreater(ball.instance(player, ball.GRAPPLE).states[25].remaining(), 4.8)

    def test_actual_emp_rejects_airborne_piledriver_until_fresh_press(self):
        run, player, sombra, _, _ = self.scene(distance=1.5)
        script = player.body_script
        script.set_ult_charge(0)
        self.cast(run, sombra)
        lift(run, player)
        self.assertTrue(player.mover.airborne)
        self.assertTrue(sombra_emp.locked(script, run.frame))
        pile = pile_instance(player)
        target = next(t for t in run.match.combat._targets() if t.owner is sombra)
        vitals = run.match.combat.vitals_of(target)
        for _ in range(340):
            run.step({player: (0, 0, 128)})
            self.assertFalse(pile.state(18).active)
            self.assertEqual(pile.state(24).remaining(), 0)
            if not sombra_emp.locked(script, run.frame):
                break
        self.assertFalse(sombra_emp.locked(script, run.frame))
        self.assertEqual(pile.state(7).counter, 0)
        self.assertEqual(vitals.total, 200)
        self.assertEqual(script.emp_suppressed_buttons, 128)
        run.step({})
        lift(run, player)
        run.step({player: (0, 0, 128)})
        self.assertTrue(pile.state(18).active)
        for _ in range(100):
            run.step({})
            if pile.state(7).counter:
                break
        self.assertEqual(pile.state(7).counter, 1)
        self.assertEqual(vitals.total, 100)
        self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 100)

    def test_running_piledriver_can_land_during_current_emp_implementation(self):
        for lead, height, phase in ((0, 8, 18), (24, 12, 3)):
            with self.subTest(phase=phase):
                run, player, sombra, _, _ = self.scene(distance=1.5)
                script = player.body_script
                script.set_ult_charge(0)
                lift(run, player, height)
                run.step({player: (0, 0, 128)})
                pile = pile_instance(player)
                self.assertTrue(pile.state(18).active)
                for _ in range(lead):
                    run.step({})
                self.cast(run, sombra)
                self.assertTrue(sombra_emp.locked(script, run.frame))
                self.assertTrue(pile.state(phase).active)
                self.assertEqual(pile.state(7).counter, 0)
                target = next(t for t in run.match.combat._targets() if t.owner is sombra)
                vitals = run.match.combat.vitals_of(target)
                for _ in range(100):
                    run.step({})
                    if pile.state(7).counter:
                        break
                self.assertTrue(sombra_emp.locked(script, run.frame))
                self.assertEqual(pile.state(7).counter, 1)
                self.assertEqual(vitals.total, 100)
                self.assertGreater(sombra.mover.velocity[1], 0)
                self.assertGreater(pile.state(24).remaining(), 0)
                self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 100)

    def test_actual_emp_target_selection_rejects_team_cover_and_range(self):
        for case in ('ally', 'wall', 'range'):
            with self.subTest(case=case):
                run, player, sombra, _, _ = self.scene(distance=17 if case == 'range' else 6)
                if case == 'ally':
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    sombra.team = player.team
                elif case == 'wall':
                    x, y, z = player.mover.position
                    a, b = (x - 3, y, z + 3), (x + 3, y, z + 3)
                    c, d = (x + 3, y + 4, z + 3), (x - 3, y + 4, z + 3)
                    run.match.collision_world = World([(*a, *b, *c), (*a, *c, *d)])
                self.cast(run, sombra)
                self.assertFalse(sombra_emp.locked(player.body_script, run.frame))
                self.assertFalse(any(i.graph.index == sombra_emp.CHILD
                                     for i in player.body_script.component.instances.values()))

    def cast_without_ball_commands(self, run, sombra):
        instances = sombra.body_script.component.instances.values()
        emp = next(i for i in instances if i.graph.index == sombra_emp.EMP)
        for step in range(65):
            run.frame += 1
            run.match.update(run.frame * combat.TICK, run.frame, frames=False)
            sombra.take_commands([Command(run.frame, buttons=32 if step == 0 else 0)])
            if emp.state(17).counter:
                break
        self.assertEqual(emp.state(17).counter, 1)

    def test_emp_drains_live_shields_at_impact_without_ball_commands(self):
        run, player, sombra, _, own = self.scene()
        run.step({player: (0, 0, 16)})
        self.assertEqual(own.temporary_shields, 175)
        script = player.body_script
        original_clock = script.frame, script.component.now
        self.cast_without_ball_commands(run, sombra)
        self.assertEqual((script.frame, script.component.now), original_clock)
        self.assertEqual(own.temporary_shields, 0)
        self.assertEqual(own.total, 600)
        self.assertTrue(sombra_emp.locked(script, run.frame))
        run.step({})
        self.assertEqual(own.total, 600)
        self.assertEqual(sombra.body_script.component.entity_var(ULT_CHARGE).value(), 0)

    def test_late_preimpact_adaptive_shield_is_drained_on_emp_replay(self):
        run, player, sombra, _, own = self.scene()
        preimpact = run.frame + 1
        self.cast_without_ball_commands(run, sombra)
        script = player.body_script
        self.assertFalse(sombra_emp.locked(script, preimpact))
        player.take_commands([Command(preimpact, buttons=16), Command(run.frame)])
        self.assertEqual(ball.instance(player, 0x1566).states[6].counter, 1)
        self.assertEqual(own.temporary_maximum, 175)
        self.assertEqual(own.temporary_shields, 0)
        self.assertEqual(own.total, 600)

    def test_late_preimpact_adaptive_shield_stays_drained_after_emp_expires(self):
        run, player, sombra, _, own = self.scene()
        preimpact = run.frame + 1
        self.cast_without_ball_commands(run, sombra)
        script = player.body_script
        run.frame += 314
        run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        self.assertFalse(sombra_emp.locked(script, run.frame))
        self.assertFalse(any(i.graph.index == sombra_emp.CHILD for i in script.component.instances.values()))
        player.take_commands([Command(preimpact, buttons=16), Command(run.frame)])
        self.assertEqual(ball.instance(player, 0x1566).states[6].counter, 1)
        self.assertEqual(own.temporary_maximum, 175)
        self.assertEqual(own.temporary_shields, 0)
        self.assertEqual(own.total, 600)
        self.assertTrue(body_vm_snapshot.export_body_vm(script))
        run.frame += 1000
        run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        player.take_commands([Command(run.frame, buttons=16)])
        self.assertEqual(ball.instance(player, 0x1566).states[6].counter, 2)
        self.assertEqual(own.temporary_shields, 175)

    def test_first_ball_command_after_expired_emp_accepts_fresh_ultimate(self):
        run, player, sombra, _, _ = self.scene()
        script = player.body_script
        last_ball_frame = script.frame
        self.cast_without_ball_commands(run, sombra)
        self.assertEqual(script.frame, last_ball_frame)
        self.assertFalse(sombra_emp.locked(script, last_ball_frame))
        self.assertTrue(sombra_emp.locked(script, run.frame))
        run.frame += 314
        run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        self.assertFalse(sombra_emp.locked(script, run.frame))
        self.assertFalse(any(i.graph.index in (sombra_emp.CHILD, sombra_emp.EFFECT)
                             for i in script.component.instances.values()))
        player.take_commands([Command(run.frame, buttons=32)])
        self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)

    def test_late_press_during_emp_stays_rejected_across_another_command_gap(self):
        run, player, sombra, _, _ = self.scene()
        script = player.body_script
        charge = script.component.entity_var(ULT_CHARGE)
        cost = charge.value()
        self.cast_without_ball_commands(run, sombra)
        impact = run.frame
        run.frame = impact + 290
        run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        player.take_commands([Command(run.frame, buttons=32)])
        self.assertTrue(sombra_emp.locked(script, run.frame))
        self.assertEqual(charge.value(), cost)
        self.assertTrue(any(i.graph.index == sombra_emp.EFFECT for i in script.component.instances.values()))

        run.frame = impact + 340
        run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        self.assertFalse(sombra_emp.locked(script, run.frame))
        player.take_commands([Command(run.frame, buttons=32)])
        self.assertEqual(charge.value(), cost)
        run.step({})
        run.step({player: (0, 0, 32)})
        self.assertEqual(charge.value(), 0)
