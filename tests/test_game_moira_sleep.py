"""Real Ana dart against Moira; native sleep/wake animation remains open."""

import unittest
from dataclasses import replace
from unittest.mock import patch

import test_game_moira_biotic as biotic_tests
import test_game_moira_control as control_tests
import test_game_moira_nano as nano_tests
from test_game_combat import Run, aim

from ow174.game import combat, moira
from ow174.game.collision import World
from ow174.game.commands import Command
from ow174.game.content import find_hero
from ow174.game.script import expr, message_receive, nodes


class MoiraSleepTests(unittest.TestCase):
    step = staticmethod(control_tests.MoiraControlTests.step)
    ultimate = control_tests.MoiraControlTests.ultimate

    def arena(self):
        run = Run(names=('Ana', 'Moira'))
        match = run.match
        match.bots.clear()
        match._pending_bot_scripts.clear()
        caster, target = run.players
        match.switch_hero(caster, find_hero('ana'))
        match.switch_hero(target, find_hero('moira'))
        caster.team, target.team = 0, 1
        x, y, z = caster.mover.position
        target.mover.position = [x, y, z + 3]
        for actor in run.players:
            actor.mover.body = None
            actor.body_script.ult_seconds = 0
        target.body_script.component.set(12918, 0)
        for _ in range(20):
            self.step(run, caster, target)
        entries = {t.owner: t for t in match.combat._targets()}
        health = [match.combat.vitals_of(entries[p]) for p in run.players]
        return run, caster, target, health

    def cast(self, run, caster, target, *, buttons=0):
        self.step(run, caster, target, cast=8, buttons=buttons)
        for _ in range(60):
            if run.match.combat.is_asleep(target):
                return run.match.combat.vitals[target.body].sleep_until
            self.step(run, caster, target, buttons=buttons)
        self.fail('actual sleep dart applied no sleep')

    @staticmethod
    def cast_at(run, caster, actor):
        fight = run.match.combat
        entry = nano_tests.MoiraNanoTests.target(fight, actor)
        direction = aim(fight._eye(caster), (entry.feet[0], entry.feet[1] + entry.height / 2, entry.feet[2]))
        run.step({caster: (*direction, 8)})
        for _ in range(60):
            if fight.is_asleep(actor):
                return fight.vitals_of(entry).sleep_until
            run.step({caster: (*direction, 0)})
        raise AssertionError('actual dart did not sleep the selected body')

    def test_dart_damage_then_five_second_sleep_gates_grasp_fade_movement_and_future_input(self):
        run, caster, target, health = self.arena()
        for _ in range(20):
            self.step(run, caster, target, buttons=2)
        self.assertLess(health[0].total, 200)
        weapon = next(i for i in target.body_script.component.instances.values() if i.graph.index == 0xF76)
        search = weapon.state(106)
        self.assertEqual(search.payload()["targets"], ((caster.body, 0),))
        deadline = self.cast(run, caster, target, buttons=2)
        self.assertFalse(search.active)
        self.assertEqual(expr.to_entity(weapon.evaluate(weapon.state(22).node.field("m_FA3ECC43"))), 0)
        fight = run.match.combat
        self.assertEqual(health[1].total, 195)
        self.assertAlmostEqual(deadline - fight.now, 5)
        before = health[0].total, tuple(target.mover.position)
        for _ in range(50):
            self.step(run, caster, target, buttons=2 | 8 | 32 | 0x800, forward=127)
            self.assertEqual(health[0].total, before[0])
            self.assertEqual(tuple(target.mover.position), before[1])
            self.assertFalse(moira.fading(target))
            self.assertFalse(target.body_script.component.held)
        target.take_commands([Command(run.frame + 100, forward=127, buttons=8 | 2)])
        self.assertTrue(fight.is_asleep(target))
        self.assertEqual(tuple(target.mover.position), before[1])
        self.assertEqual(health[0].total, before[0])

    def test_expiry_releases_input_and_attack_without_refilling_health(self):
        run, caster, target, health = self.arena()
        deadline = self.cast(run, caster, target)
        while run.match.combat.now < deadline:
            self.step(run, caster, target)
        self.assertFalse(run.match.combat.is_asleep(target))
        self.assertEqual(health[1].total, 195)
        before = health[0].total, tuple(target.mover.position)
        for _ in range(20):
            self.step(run, caster, target, buttons=2, forward=127)
        self.assertLess(health[0].total, before[0])
        self.assertNotEqual(tuple(target.mover.position), before[1])

    def test_dart_damage_without_authored_sleep_effect_does_not_control_target(self):
        run, caster, target, health = self.arena()
        fight = run.match.combat
        resolve = combat.hit_def
        changed = []

        def without_sleep(state, config):
            if state.instance.graph.index == 0xBDD and state.node.pos == 14 and config is not None:
                original = resolve(state, config)
                self.assertEqual(original.effect, combat.SLEEP_DART_EFFECT)
                self.assertEqual(original.sleep_duration, 5.0)
                modify = {**config["m_modifyHealth"], "m_277A4ECA": None}
                config = {**config, "m_modifyHealth": modify}
                changed.append(state.node.pos)
            return resolve(state, config)

        with patch.object(combat, "hit_def", side_effect=without_sleep):
            self.step(run, caster, target, cast=8)
            for _ in range(30):
                self.step(run, caster, target)
        self.assertEqual(changed, [14])
        self.assertEqual(health[1].total, 195)
        self.assertEqual(health[1].sleep_until, 0.0)
        self.assertFalse(fight.is_stunned(target))

    def test_actual_sleep_holds_sampled_view_after_authored_delay_and_releases_it(self):
        run, caster, target, health = self.arena()
        fight = run.match.combat
        deadline = self.cast(run, caster, target)
        captured = (-32000, 8000)
        for _ in range(9):  # the last pose before the 150 ms sampling boundary
            run.step({target: (*captured, 0)})
            self.assertEqual((target.mover.yaw, target.mover.pitch), captured)
            self.assertIsNone(health[1].sleep_view)
        world = target.body_script.world
        with patch.object(world, "sample_sleep_view", wraps=world.sample_sleep_view) as sample:
            run.step({target: (32000, -8000, 0)})
        sample.assert_called_once_with()
        self.assertEqual((target.mover.yaw, target.mover.pitch), captured)
        self.assertEqual(health[1].sleep_view, world.sample_sleep_view())
        while fight.now + combat.TICK < deadline - combat.EPSILON:
            run.step({target: (32000, -8000, 0)})
            self.assertEqual((target.mover.yaw, target.mover.pitch), captured)
            self.assertIsNotNone(health[1].sleep_view)
        run.step({target: (32000, -8000, 0)})
        self.assertFalse(fight.is_asleep(target))
        self.assertIsNone(health[1].sleep_view)
        self.assertEqual((target.mover.yaw, target.mover.pitch), (32000, -8000))

    def test_second_ana_dart_during_recovery_restarts_sleep_and_resamples_view(self):
        run, first, victim, health = self.arena()
        second = nano_tests.MoiraNanoTests().add_ana(run, victim)
        second.team = first.team
        fight = run.match.combat
        vitals = health[1]
        initial_end = self.cast(run, first, victim)
        for _ in range(42):
            self.step(run, first, victim)
        original_view = vitals.sleep_view
        self.assertIsNotNone(original_view)

        # The first caster's new input edge remains on cooldown. The rifle
        # hit then starts wake recovery while the second caster is ready.
        self.step(run, first, victim, cast=8)
        for _ in range(24):
            self.step(run, first, victim)
        self.assertEqual(vitals.total, 195)
        self.assertEqual(vitals.sleep_until, initial_end)
        self.step(run, first, victim, cast=1)
        for _ in range(12):
            if vitals.total < 195:
                break
            self.step(run, first, victim)
        self.assertLess(vitals.total, 195)
        recovery_end = vitals.sleep_until
        self.assertAlmostEqual(recovery_end, fight.now + .5)

        target = nano_tests.MoiraNanoTests.target(fight, victim)
        center = (target.feet[0], target.feet[1] + target.height / 2, target.feet[2])
        direction = aim(fight._eye(second), center)
        before = vitals.total
        with patch.object(fight, "_apply_hit", wraps=fight._apply_hit) as contacts:
            run.step({second: (*direction, 8)})
            for _ in range(30):
                if vitals.sleep_until != recovery_end:
                    break
                run.step({second: (*direction, 0)})
        (impact,) = [call for call in contacts.call_args_list if call.args[2].sleep_duration]
        self.assertIs(impact.args[0], second)
        self.assertEqual(impact.kwargs["source_body"], second.body)
        self.assertLess(fight.now, recovery_end)
        self.assertEqual(vitals.total, before - 5)
        self.assertAlmostEqual(vitals.sleep_until, fight.now + 5)
        self.assertAlmostEqual(vitals.sleep_wake_after, fight.now + .5)
        self.assertIsNone(vitals.sleep_view)

        second_end = vitals.sleep_until
        new_pose = (12000, 6000)
        for _ in range(9):
            run.step({victim: (*new_pose, 0)})
        while fight.now < initial_end:
            run.step({victim: (-12000, -6000, 0)})
        self.assertNotEqual(vitals.sleep_view, original_view)
        self.assertEqual((victim.mover.yaw, victim.mover.pitch), new_pose)
        self.assertTrue(fight.is_asleep(victim))
        while fight.now < second_end:
            run.step({victim: (-12000, -6000, 0)})
        self.assertFalse(fight.is_asleep(victim))
        self.assertEqual((victim.mover.yaw, victim.mover.pitch), (-12000, -6000))

    def test_sleep_interrupts_coalescence_without_refund_or_restart(self):
        run, caster, target, health = self.arena()
        instance = self.ultimate(run, caster, target)
        deadline = self.cast(run, caster, target)
        self.assertFalse(instance.states[20].active)
        self.assertFalse(instance.states[0].flags & nodes.Ability.RUNNING)
        before = health[0].total, health[1].total
        while run.match.combat.now < deadline + .3:
            self.step(run, caster, target, buttons=32)
        self.assertEqual((health[0].total, health[1].total), before)
        self.assertEqual(target.body_script.component.vars[342].value(), 0)

    def test_active_fade_rejects_actual_dart_damage_and_sleep(self):
        for delay, impact_frame, immune in ((0, 22, True), (25, 47, True), (26, 48, False)):
            with self.subTest(impact_frame=impact_frame):
                run, caster, target, health = self.arena()
                self.step(run, caster, target, buttons=8)
                self.assertTrue(moira.fading(target))
                start = run.frame
                for _ in range(delay):
                    self.step(run, caster, target)
                fight = run.match.combat
                apply_hit = fight._apply_hit
                contacts = []

                def observe(shooter, recipient, definition, *args, **kwargs):
                    if definition.sleep_duration:
                        contacts.append((run.frame - start, moira.fading(target), fight.now))
                    return apply_hit(shooter, recipient, definition, *args, **kwargs)

                with patch.object(fight, '_apply_hit', side_effect=observe):
                    self.step(run, caster, target, cast=8)
                    for _ in range(25):
                        self.step(run, caster, target)
                (frame, active, impact_time), = contacts
                self.assertEqual((frame, active), (impact_frame, immune))
                self.assertEqual(health[1].total, 200 if immune else 195)
                self.assertEqual(fight.is_asleep(target), not immune)
                if immune:
                    self.assertEqual(health[1].sleep_until, 0)
                else:
                    self.assertAlmostEqual(health[1].sleep_until, impact_time + 5)

    def test_real_dart_cancels_orb_selection_without_late_release(self):
        run, caster, target, _ = self.arena()
        self.step(run, caster, target, buttons=16)
        ability = next(i for i in target.body_script.component.instances.values()
                       if i.graph.index == 0x10E4).states[0]
        self.assertTrue(ability.flags & nodes.Ability.RUNNING)
        deadline = self.cast(run, caster, target)
        while run.match.combat.now < deadline + .3:
            self.step(run, caster, target, buttons=1)
        self.assertFalse(ability.flags & nodes.Ability.RUNNING)
        self.assertEqual(run.match.combat.moira_orbs, [])

    def test_sleep_interrupts_pending_orb_at_actual_release_boundary(self):
        for choice in (1, 2):
            for cast, selection, expected_release in ((False, 15, 21), (True, 14, 20), (True, 15, None)):
                with self.subTest(choice=choice, dart=cast, selection_frame=selection):
                    run, caster, target, health = self.arena()
                    fight = run.match.combat
                    instance = next(i for i in target.body_script.component.instances.values()
                                    if i.graph.index == 0x10E4)
                    self.step(run, caster, target, cast=8 if cast else 0)
                    emitted, impact, health_at_impact = None, None, None
                    for frame in range(1, 38):
                        buttons = 16 if frame == selection else choice if frame > selection else 0
                        self.step(run, caster, target, buttons=buttons)
                        if impact is None and fight.is_asleep(target):
                            impact = frame
                            health_at_impact = health[1].total
                        if emitted is None and fight.moira_orbs:
                            emitted = frame
                    self.assertEqual(impact, 21 if cast else None)
                    self.assertEqual(emitted, expected_release)
                    self.assertEqual(health_at_impact, 195 if cast else None)
                    self.assertEqual(health[1].total, 200 if not cast or choice == 1 and emitted else 195)
                    if expected_release is None:
                        self.assertFalse(fight.moira_orbs)
                        self.assertEqual(instance.states[0].remaining(), 0)
                    else:
                        orb, = fight.moira_orbs
                        self.assertEqual(orb.healing, choice == 1)
                        self.assertGreater(orb.flown, 0)
                        self.assertGreater(instance.states[0].remaining(), 0)

    def test_previously_emitted_orb_keeps_running_while_caster_sleeps(self):
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
        self.assertTrue(run.match.combat.is_asleep(target))
        self.assertAlmostEqual(before[0] - health[0].total, 8, places=4)
        self.assertAlmostEqual(before[1] - orb.energy, 8, places=4)
        self.assertAlmostEqual(orb.flown - before[2], .16, places=4)

    def test_all_four_moira_damage_paths_wake_a_sleeping_enemy(self):
        case = nano_tests.MoiraNanoTests()
        for attack in ('grasp', 'orb', 'melee', 'ultimate'):
            with self.subTest(attack=attack):
                run, source, victim = case.fight()
                caster = case.add_ana(run, source)
                self.cast_at(run, caster, victim)
                for _ in range(33):
                    run.step({})  # actual224E damage admission begins after0.5s
                caster.mover.position[0] += 30
                fight = run.match.combat
                health = fight.vitals_of(case.target(fight, victim))
                before = health.total
                x, y, z = victim.mover.position
                direction = aim(fight._eye(source), (x, y + 1, z))
                if attack == 'orb':
                    case.launch_orb(run, source)
                elif attack == 'ultimate':
                    case.start_ult(run, source)
                for i in range(30):
                    buttons = 2 if attack == 'grasp' else 0x800 if attack == 'melee' and i == 0 else 0
                    run.step({source: (*direction, buttons)})
                    if health.total < before:
                        break
                self.assertLess(health.total, before)
                self.assertTrue(fight.is_asleep(victim))
                self.assertLessEqual(health.sleep_until - fight.now, .5 + 1e-6)
                deadline = health.sleep_until
                while fight.now < deadline:
                    run.step({})
                    self.assertLessEqual(health.sleep_until, deadline)
                self.assertFalse(fight.is_asleep(victim))

    def test_all_four_moira_ally_heals_leave_actual_sleep_undisturbed(self):
        case = biotic_tests.MoiraBioticTests()
        for attack in ('spray', 'tail', 'orb', 'ultimate'):
            with self.subTest(attack=attack):
                run, caster, source, target, health = case.fight()
                caster.team = 1 - source.team
                deadline = self.cast_at(run, caster, target.owner)
                caster.mover.position[0] += 30
                given = case.sample(run, source, target, health, attack)
                self.assertGreater(given, 0)
                self.assertEqual(health.sleep_until, deadline)
                self.assertTrue(run.match.combat.is_asleep(target.owner))

    def test_lethal_dart_and_replacement_do_not_leave_sleep_on_another_body(self):
        for lethal in (False, True):
            with self.subTest(lethal=lethal):
                run, caster, target, health = self.arena()
                old = target.body
                if lethal:
                    health[1].damage(196)
                    self.step(run, caster, target, cast=8)
                    for _ in range(30):
                        self.step(run, caster, target)
                    self.assertTrue(health[1].dead)
                    self.assertEqual(health[1].sleep_until, 0)
                    self.assertFalse(run.match.combat.is_asleep(target))
                else:
                    self.cast(run, caster, target)
                    for _ in range(10):
                        self.step(run, caster, target)
                    self.assertIsNotNone(health[1].sleep_view)
                self.assertTrue(run.match.switch_hero(target, target.hero))
                self.assertNotEqual(old, target.body)
                self.assertNotIn(old, run.match.combat.vitals)
                self.assertFalse(run.match.combat.is_asleep(target))

    def test_nano_heals_and_buffs_sleeping_body_without_waking_it(self):
        case = nano_tests.MoiraNanoTests()
        run, source, victim = case.fight()
        fight = run.match.combat
        health = fight.vitals_of(case.target(fight, victim))
        health.damage(400)
        fight._sync_body_health(victim, health)
        enemy = case.add_ana(run, source)
        deadline = self.cast_at(run, enemy, victim)
        enemy.mover.position[0] += 30
        friendly = case.add_ana(run, victim)
        case.cast(run, friendly, victim)
        self.assertEqual(health.total, 445)
        self.assertEqual(health.sleep_until, deadline)
        self.assertTrue(fight.is_asleep(victim))
        self.assertGreater(health.nano_until, fight.now)

    def test_real_sleep_dart_ally_miss_static_wall_and_ffa_admission(self):
        for mode in ('ally', 'miss', 'wall', 'ffa'):
            with self.subTest(mode=mode):
                run, caster, target, health = self.arena()
                fight = run.match.combat
                if mode in ('ally', 'ffa'):
                    target.team = caster.team
                if mode == 'ffa':
                    run.match.game_map = replace(run.match.game_map, free_for_all=True)
                direction = aim(fight._eye(caster), fight._eye(target)) if mode != 'miss' else (16384, 0)
                x, y, z = caster.mover.position
                wall = World([(x - 10, y - 10, z + 1.5, x + 10, y - 10, z + 1.5,
                               x + 10, y + 10, z + 1.5),
                              (x - 10, y - 10, z + 1.5, x + 10, y + 10, z + 1.5,
                               x - 10, y + 10, z + 1.5)])
                collision = wall if mode == 'wall' else fight._collision()
                with (patch.object(fight, '_collision', return_value=collision),
                      patch.object(fight, '_shoot', wraps=fight._shoot) as released):
                    run.step({caster: (*direction, 8)})
                    for _ in range(35):
                        run.step({caster: (*direction, 0)})
                self.assertGreater(released.call_count, 0)
                self.assertEqual(health[1].total, 195 if mode == 'ffa' else 200)
                self.assertEqual(fight.is_asleep(target), mode == 'ffa')

    def test_early_real_melee_cannot_skip_fall_and_later_melee_keeps_half_second_control(self):
        case = nano_tests.MoiraNanoTests()
        run, source, victim = case.fight()
        point = list(victim.mover.position)
        run.match.switch_hero(victim, find_hero('moira'))
        victim.mover.position = point
        victim.mover.body = None
        caster = case.add_ana(run, source)
        original = self.cast_at(run, caster, victim)
        caster.mover.position[0] += 30
        fight = run.match.combat
        health = fight.vitals_of(case.target(fight, victim))
        direction = aim(fight._eye(source), fight._eye(victim))
        before = health.total
        run.step({source: (*direction, 0x800)})
        for _ in range(15):
            run.step({})
            if health.total < before:
                break
        self.assertLess(health.total, before)
        self.assertLess(fight.now, health.sleep_wake_after)
        self.assertEqual(health.sleep_until, original)
        for _ in range(70):
            run.step({})
        before = health.total
        with patch.object(message_receive, "deliver", wraps=message_receive.deliver) as wake:
            run.step({source: (*direction, 0x800)})
            for _ in range(20):
                if health.total < before:
                    break
                run.step({})
        ((component, packet),) = [call.args for call in wake.call_args_list
                                  if call.args[1].message == 0x0240000000000011]
        self.assertEqual((packet.caller, packet.instigator), (source.body, source.body))
        self.assertEqual(component.entity, victim.body)
        self.assertIsNone(component.fault)
        self.assertEqual(component.destroyed[-1].find_var(0, 1903).value(), .5)
        self.assertLess(health.total, before)
        self.assertTrue(fight.is_asleep(victim))
        self.assertAlmostEqual(health.sleep_until - fight.now, .5, places=5)
        self.assertIsNotNone(health.sleep_view)
        held_view = victim.mover.yaw, victim.mover.pitch
        position = tuple(victim.mover.position)
        while fight.now + .016 < health.sleep_until:
            run.frame += 1
            run.match.update(run.frame * .016, run.frame)
            for actor in run.players:
                actor.take_commands([Command(run.frame, forward=127 if actor is victim else 0,
                                             yaw=12000 if actor is victim else 0,
                                             buttons=8 if actor is victim else 0)])
            self.assertEqual(tuple(victim.mover.position), position)
            self.assertEqual((victim.mover.yaw, victim.mover.pitch), held_view)
            self.assertFalse(moira.fading(victim))
        while fight.now < health.sleep_until:
            run.step({})
        run.step({victim: (12000, 0, 8)})
        self.assertTrue(moira.fading(victim))
        self.assertIsNone(health.sleep_view)
        self.assertEqual(victim.mover.yaw, 12000)

    def test_tag191f_health_operation_does_not_shorten_actual_sleep(self):
        from ow174.game.combat import HitDef

        run, caster, victim, health = self.arena()
        original = self.cast(run, caster, victim)
        for _ in range(40):
            self.step(run, caster, victim)
        fight = run.match.combat
        entry = nano_tests.MoiraNanoTests.target(fight, victim)
        # Explicit operation fixture for224E's excluded tag, not a claim that
        # this tag belongs to Ana's dart or to an emitted Moira attack.
        definition = HitDef(amount=1, tags=(0x0D8000000000191F,))
        before = health[1].total
        with patch.object(message_receive, "deliver", wraps=message_receive.deliver) as wake:
            fight._apply_hit(caster, entry, definition, 1, False, run.frame)
        wake.assert_called_once()
        component, packet = wake.call_args.args
        tag = expr.Asset(0x0D8000000000191F)
        self.assertEqual(packet.params, ((0x0A4000000000003D, (tag,)),))
        self.assertIsNone(component.fault)
        self.assertEqual(component.destroyed[-1].find_var(0, 510).value(), (tag,))
        self.assertEqual(health[1].total, before - 1)
        self.assertEqual(health[1].sleep_until, original)


if __name__ == '__main__':
    unittest.main()
