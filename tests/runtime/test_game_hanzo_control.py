"""Actual McCree Flashbang input versus Hanzo actions; explicit host control policy."""

import unittest
from unittest.mock import patch

import test_game_hanzo_dragons as dragon_tests
import test_game_hanzo_sonic as sonic_tests
import test_mccree_flashbang as flash_tests
from test_game_combat import aim, in_world
from test_game_mover import box, wall

from ow174.game import bodytrace, combat, hanzo_control, movelog, sonic
from ow174.game.collision import World
from ow174.game.commands import JUMP, Command
from ow174.game.content import SOLDIER, heroes

HANZO = 0x02E0000000000005


class HanzoControlTests(unittest.TestCase):
    step = flash_tests.FlashbangTests.step
    cast = flash_tests.FlashbangTests.cast

    @staticmethod
    def aim_at_caster(fight, player, caster):
        x, y, z = caster.mover.position
        return aim(fight._eye(player), (x, y + 0.85, z))

    def setUp(self):
        for module in (bodytrace, movelog):
            self.enterContext(patch.object(module, "ENABLED", False))

    def arena(self):
        run, caster, hero = flash_tests.FlashbangTests().arena(distance=3)
        position = tuple(hero.mover.position)
        run.match.switch_hero(hero, heroes()[HANZO])
        hero.mover.position[:] = position
        hero.mover.body = None
        return run, caster, hero

    def recover(self, run, caster, hero):
        end = run.match.combat.stuns[hero.body]
        while run.frame < end:
            self.step(run, caster, hero)
        self.assertFalse(run.match.combat.is_stunned(hero))

    def climb_step(self, run, caster, hero, *, cast=0, target_buttons=0, forward=0):
        run.frame += 1
        run.match.update(run.frame * combat.TICK, run.frame)
        point = hero.mover.position
        yaw, pitch = aim(run.match.combat._eye(caster), (point[0], point[1] + 1, point[2]))
        caster.take_commands([Command(run.frame, yaw=yaw, pitch=pitch, buttons=cast)])
        hero.take_commands([Command(run.frame, yaw=-32768, buttons=target_buttons, forward=forward)])

    def test_flashbang_cancels_draw_without_releasing_damage_then_fresh_draw_recovers(self):
        run, caster, hero = self.arena()
        fight = run.match.combat
        for _ in range(20):
            self.step(run, caster, hero, target_buttons=1)
        self.cast(run, caster, hero, target_buttons=1)
        self.assertEqual(fight.vitals[hero.body].total, 175)
        end = fight.stuns[hero.body]
        for _ in range(end - run.frame - 1):
            self.step(run, caster, hero, target_buttons=1)
            self.assertFalse(any(r.shooter is hero for r in fight.rockets))
        self.recover(run, caster, hero)
        self.assertEqual(fight.vitals.get(caster.body).total if caster.body in fight.vitals else 225, 225)
        for _ in range(60):
            self.step(run, caster, hero, target_buttons=1)
        self.step(run, caster, hero)
        for _ in range(10):
            self.step(run, caster, hero)
        self.assertEqual(fight.vitals[caster.body].total, 100)

    def test_flashbang_cancels_draw_without_spending_prepared_sonic(self):
        run, caster, hero = self.arena()
        fight = run.match.combat
        fight.configure_sonic(sonic_tests.profile())
        component = hero.body_script.component
        selected = component.entity_var(627)
        ability = next(i for i in component.instances.values() if i.graph.index == 0x00C1)
        self.step(run, caster, hero, target_buttons=8)
        self.step(run, caster, hero)
        self.assertEqual(selected.value(), 1)
        for _ in range(70):
            self.step(run, caster, hero, target_buttons=1)
        with patch.object(fight.projectiles, "fired", wraps=fight.projectiles.fired) as fired:
            self.cast(run, caster, hero, target_buttons=1)
            self.assertEqual(fight.vitals[hero.body].total, 175)
            self.recover(run, caster, hero)
            self.assertEqual(selected.value(), 1)
            self.assertEqual(ability.vars[61].value(), 0)
            self.assertFalse(any(call.args[0] is hero for call in fired.call_args_list))
            for _ in range(70):
                self.step(run, caster, hero, target_buttons=1)
            self.step(run, caster, hero)
            shots = [call.args[3] for call in fired.call_args_list if call.args[0] is hero]
            self.assertEqual(shots, [sonic.DEFINITIONS[0]])
            self.assertGreater(ability.vars[61].value(), 11.9)
        for _ in range(10):
            self.step(run, caster, hero)
        self.assertEqual(fight.vitals[caster.body].total, 100)
        self.assertTrue(fight.revealed_to(hero, caster))

    def test_flashbang_prevents_buffered_melee_after_control_or_death(self):
        for lethal in (False, True):
            with self.subTest(lethal=lethal):
                run, caster, hero = self.arena()
                fight = run.match.combat
                x, y, z = caster.mover.position
                hero.mover.position[:] = [x, y, z + 1.8]
                for _ in range(8):
                    self.step(run, caster, hero)
                old_body = hero.body
                if lethal:
                    target = next(t for t in fight._targets() if t.owner is hero)
                    fight.vitals_of(target).damage(199)  # 1 HP before the actual Flashbang
                self.step(run, caster, hero, target_buttons=0x800)
                first = run.frame
                self.assertEqual(fight.vitals[caster.body].total, 195)
                for _ in range(53):
                    self.step(run, caster, hero)
                self.step(run, caster, hero, cast=16, target_buttons=0x800)
                instance = next(i for i in hero.body_script.component.instances.values()
                                if i.graph.index == 0x0043)
                ability = next(state for state in instance.states.values() if state.node.pos == 14)
                self.assertTrue(ability.presses)
                interrupted = fight.is_dead if lethal else fight.is_stunned
                for _ in range(8):
                    if interrupted(hero):
                        break
                    self.step(run, caster, hero)
                self.assertTrue(interrupted(hero))
                self.assertLess(run.frame - first, 62)
                if lethal:
                    limit = round(combat.respawn_seconds(fight.match) / combat.TICK) + 120
                    for _ in range(limit):
                        if hero.body != old_body:
                            break
                        self.step(run, caster, hero)
                    self.assertNotEqual(hero.body, old_body)
                    self.assertFalse(fight.is_dead(hero))
                    self.assertNotIn(old_body, fight.melee_seen)
                else:
                    self.assertFalse(ability.presses)
                    self.recover(run, caster, hero)
                for _ in range(8):
                    self.step(run, caster, hero)
                self.assertEqual(fight.vitals[caster.body].total, 195)
                self.assertEqual(len(hero.client.messages(combat.HIT)), 1)
                if not lethal:
                    self.step(run, caster, hero, target_buttons=0x800)
                    self.assertEqual(fight.vitals[caster.body].total, 165)
                    self.assertEqual(len(hero.client.messages(combat.HIT)), 2)

    def test_flashbang_stops_lunge_and_jump_input_during_control(self):
        run, caster, hero = self.arena()
        self.step(run, caster, hero, target_buttons=JUMP)
        for _ in range(14):
            self.step(run, caster, hero)
        self.step(run, caster, hero, target_buttons=JUMP)
        self.assertGreater(abs(hero.mover.velocity[2]), 14)
        self.cast(run, caster, hero)
        self.step(run, caster, hero, target_buttons=JUMP, forward=127)
        self.assertEqual(hero.mover.velocity[0::2], (0, 0))
        instance = next(i for i in hero.body_script.component.instances.values() if i.graph.index == 0x010C)
        self.assertGreater(instance.vars[61].value(), 0)
        self.assertFalse(hero.body_script.component.held)

    def test_flashbang_interrupts_wall_climb_and_does_not_restart_held_jump(self):
        run, caster, hero = self.arena()
        x, y, z = hero.mover.position
        # Establish a climb facing the -z wall, then throw from an open side.
        caster.mover.position[:] = (x, y, z - 3)
        collision = World(box(x - 20, x + 20, y - 1, y, z - 20, z + 20)
                          + wall(x - 5, z - 0.7, x + 5, z - 0.7, y0=y, y1=y + 20))
        # Cast through an open side after establishing a real climb.
        run.match.collision_world = collision
        fight = run.match.combat
        active_at_hit = []
        effect = fight._hit_effect

        def observe(target, resource):
            if target.owner is hero and resource == combat.FLASHBANG_EFFECT:
                active_at_hit.append(hero.body_script.world.hanzo_climb.active)
            return effect(target, resource)

        with patch.object(self, "step", self.climb_step), patch.object(fight, "_hit_effect", observe):
            self.step(run, caster, hero, target_buttons=JUMP)
            self.step(run, caster, hero, target_buttons=JUMP)
            self.assertTrue(hero.body_script.world.hanzo_climb.active)
            caster.mover.position[:] = (x + 2, y, z)
            caster.mover.body = None
            self.cast(run, caster, hero, target_buttons=JUMP)
            self.step(run, caster, hero, target_buttons=JUMP)
        self.assertEqual(active_at_hit, [True])
        self.assertFalse(hero.body_script.world.hanzo_climb.active)
        self.assertFalse(hero.body_script.component.entity_var(1204).value())
        self.assertLess(hero.mover.velocity[1], 0)

    def test_flashbang_interrupts_initial_ultimate_phase_without_late_arrow(self):
        run, caster, hero = self.arena()
        hero.body_script.ult_seconds = 0
        hero.body_script.set_ult_charge(1680)
        fight = run.match.combat
        with patch.object(fight, "_shoot", wraps=fight._shoot) as fire:
            self.step(run, caster, hero, target_buttons=0x20)
            self.cast(run, caster, hero)
            for _ in range(110):
                self.step(run, caster, hero)
        self.assertFalse(any(call.args[0] is hero for call in fire.call_args_list))
        self.assertEqual(hero.body_script.component.entity_var(342).value(), 0)
        self.assertFalse(any(r.shooter is hero for r in fight.rockets))
        hero.body_script.set_ult_charge(1680)  # explicit GM refill isolates recast eligibility
        self.step(run, caster, hero, target_buttons=0x20)
        for _ in range(90):
            self.step(run, caster, hero)
        self.assertEqual(fight.vitals[caster.body].total, 100)

    def test_resource_prepared_sonic_survives_and_storm_cancels_under_host_control(self):
        # Execute the resource's distinct prepared states. This records server
        # policy behavior, not an original-client stun/immunity comparison.
        for button, definition, retained in (
            (8, 0x0400000000000149, True), (16, 0x040000000000270F, False),
        ):
            with self.subTest(button=button):
                run, caster, hero = self.arena()
                self.step(run, caster, hero, target_buttons=button)
                self.step(run, caster, hero)
                self.cast(run, caster, hero)
                self.recover(run, caster, hero)
                fight = run.match.combat
                with patch.object(fight.projectiles, "fired", wraps=fight.projectiles.fired) as fired:
                    for _ in range(70):
                        self.step(run, caster, hero, target_buttons=1)
                    self.step(run, caster, hero)
                    for _ in range(10):
                        self.step(run, caster, hero)
                self.assertTrue(fired.called)
                self.assertEqual(definition in [call.args[3] for call in fired.call_args_list], retained)
                self.assertEqual(fight.vitals[caster.body].total, 100)

    def test_flashbang_after_first_storm_arrow_starts_cooldown_and_gates_retry(self):
        run, caster, hero = self.arena()
        fight = run.match.combat
        storm = next(i for i in hero.body_script.component.instances.values() if i.graph.index == 0x146A)
        ability = storm.states[0]
        projectile = 0x040000000000270F
        with patch.object(fight.projectiles, "fired", wraps=fight.projectiles.fired) as fired:
            def hanzo_shots():
                return [call.args[3] for call in fired.call_args_list if call.args[0] is hero]

            self.step(run, caster, hero, target_buttons=16)
            self.step(run, caster, hero)
            for _ in range(50):
                self.step(run, caster, hero, target_buttons=1)
                if hanzo_shots():
                    break
            self.assertEqual(hanzo_shots(), [projectile])
            self.assertTrue(ability.flags & 1)
            self.cast(run, caster, hero, target_buttons=1)
            self.assertEqual(fight.vitals[hero.body].total, 175)
            self.assertEqual(fight.vitals[caster.body].total, 155)
            self.assertFalse(ability.flags & 1)
            self.assertEqual(storm.find_var(0, 215).value(), 0)
            self.assertGreater(storm.find_var(0, 61).value(), 9.9)
            self.recover(run, caster, hero)

            self.step(run, caster, hero, target_buttons=16)
            self.step(run, caster, hero)
            self.assertFalse(ability.flags & 1)
            for _ in range(700):
                if storm.find_var(0, 61).value() == 0:
                    break
                self.step(run, caster, hero)
            self.assertEqual(storm.find_var(0, 61).value(), 0)
            self.assertFalse(ability.flags & 1)
            self.assertEqual(hanzo_shots(), [projectile])

            self.step(run, caster, hero, target_buttons=16)
            self.step(run, caster, hero)
            self.assertTrue(ability.flags & 1)
            for _ in range(50):
                self.step(run, caster, hero, target_buttons=1)
                if len(hanzo_shots()) == 2:
                    break
            for _ in range(10):
                self.step(run, caster, hero)
            self.assertEqual(hanzo_shots(), [projectile, projectile])
            self.assertEqual(fight.vitals[caster.body].total, 85)

    def test_lethal_flashbang_retires_storm_volley_and_respawn_can_start_fresh(self):
        run, caster, hero = self.arena()
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is hero)
        vitals = fight.vitals_of(target)
        vitals.damage(199)  # Seed 1 HP so the actual Flashbang is lethal.
        old = hero.body_script
        projectile = 0x040000000000270F
        short_bow = 0x040000000000020B
        with patch.object(fight.projectiles, "fired", wraps=fight.projectiles.fired) as fired:
            def hanzo_shots():
                return [call.args[3] for call in fired.call_args_list if call.args[0] is hero]

            self.step(run, caster, hero, target_buttons=16)
            self.step(run, caster, hero)
            for _ in range(50):
                self.step(run, caster, hero, target_buttons=1)
                if hanzo_shots():
                    break
            self.assertEqual(hanzo_shots(), [projectile])
            self.step(run, caster, hero, cast=16, target_buttons=1)
            for _ in range(50):
                if vitals.dead:
                    break
                self.step(run, caster, hero, target_buttons=1)
            self.assertTrue(vitals.dead)
            self.assertEqual(fight.vitals[caster.body].total, 155)
            limit = round(combat.respawn_seconds(fight.match) / combat.TICK) + 120
            for _ in range(limit):
                if hero.body_script is not old:
                    break
                self.step(run, caster, hero, target_buttons=1)
            self.assertIsNot(hero.body_script, old)
            self.assertTrue(old.retired)
            self.assertFalse(fight.is_dead(hero))
            self.assertEqual(hanzo_shots(), [projectile])
            self.assertEqual(fight.vitals[caster.body].total, 155)
            for _ in range(8):
                self.step(run, caster, hero)
            storm = next(i for i in hero.body_script.component.instances.values()
                         if i.graph.index == 0x146A)
            self.assertEqual(storm.find_var(0, 61).value(), 0)
            self.assertFalse(storm.states[0].flags & 1)
            # Held primary was sampled by the fresh body as an ordinary draw.
            self.assertEqual(hanzo_shots(), [projectile, short_bow])

            self.step(run, caster, hero, target_buttons=16)
            self.step(run, caster, hero)
            for _ in range(50):
                self.step(run, caster, hero, target_buttons=1)
                if len(hanzo_shots()) == 3:
                    break
            self.assertEqual(hanzo_shots(), [projectile, short_bow, projectile])


    def test_lethal_flashbang_separates_selected_sonic_from_deployed_beacon(self):
        for deployed in (False, True):
            with self.subTest(deployed=deployed):
                run, caster, hero = self.arena()
                fight = run.match.combat
                fight.configure_sonic(sonic_tests.profile())
                target = next(t for t in fight._targets() if t.owner is hero)
                vitals = fight.vitals_of(target)
                vitals.damage(199)  # Seed 1 HP before the actual Flashbang.
                old = hero.body
                with patch.object(fight.projectiles, "fired", wraps=fight.projectiles.fired) as fired:
                    self.step(run, caster, hero, target_buttons=8)
                    self.step(run, caster, hero)
                    if deployed:
                        for _ in range(70):
                            self.step(run, caster, hero, target_buttons=1)
                        self.step(run, caster, hero)
                        for _ in range(10):
                            self.step(run, caster, hero)
                        beacon, = fight.sonars
                        self.assertEqual(beacon.attached, caster.body)
                        self.assertEqual(beacon.source_entity, old)
                        self.assertEqual(fight.vitals[caster.body].total, 100)
                    self.step(run, caster, hero, cast=16)
                    for _ in range(50):
                        if vitals.dead:
                            break
                        self.step(run, caster, hero)
                    self.assertTrue(vitals.dead)
                    self.assertEqual(fight.revealed_to(hero, caster), deployed)

                    limit = round(combat.respawn_seconds(fight.match) / combat.TICK) + 120
                    for _ in range(limit):
                        if hero.body != old:
                            break
                        self.step(run, caster, hero)
                    self.assertNotEqual(hero.body, old)
                    self.assertFalse(fight.is_dead(hero))
                    self.assertEqual(fight.revealed_to(hero, caster), deployed)
                    if deployed:
                        self.assertEqual(fight.sonars, [beacon])
                        self.assertEqual(beacon.source_entity, old)
                        for _ in range(400):
                            if not fight.sonars:
                                break
                            self.step(run, caster, hero)
                        self.assertFalse(fight.sonars)
                        self.assertFalse(sonic_tests.SonicPlayerTests.flagged(caster))
                    else:
                        for _ in range(70):
                            self.step(run, caster, hero, target_buttons=1)
                        self.step(run, caster, hero)
                        shots = [call.args[3] for call in fired.call_args_list if call.args[0] is hero]
                        self.assertEqual(len(shots), 1)
                        self.assertNotIn(shots[0], sonic.DEFINITIONS)
                        self.assertFalse(fight.sonars)

    def test_lethal_flashbang_cancels_full_bow_draw_and_respawn_starts_uncharged(self):
        run, caster, hero = self.arena()
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is hero)
        vitals = fight.vitals_of(target)
        vitals.damage(199)  # Seed 1 HP before the actual Flashbang.
        old_body = hero.body
        old_script = hero.body_script
        start = tuple(hero.mover.position)
        with patch.object(fight.projectiles, "fired", wraps=fight.projectiles.fired) as fired:
            for _ in range(70):
                self.step(run, caster, hero, target_buttons=1)
            self.assertFalse(fired.called)
            self.step(run, caster, hero, cast=16, target_buttons=1)
            for _ in range(50):
                if vitals.dead:
                    break
                self.step(run, caster, hero, target_buttons=1)
            self.assertTrue(vitals.dead)

            limit = round(combat.respawn_seconds(fight.match) / combat.TICK) + 120
            for _ in range(limit):
                if hero.body != old_body:
                    break
                self.step(run, caster, hero)
            self.assertNotEqual(hero.body, old_body)
            self.assertTrue(old_script.retired)
            self.assertFalse(fight.is_dead(hero))
            self.assertFalse(any(call.args[0] is hero for call in fired.call_args_list))
            self.assertFalse(hero.client.messages(combat.HIT))

            hero.mover.position[:] = start
            hero.mover.body = None
            for _ in range(5):
                self.step(run, caster, hero)
            target = next(t for t in fight._targets() if t.owner is caster)
            caster_vitals = fight.vitals_of(target)
            self.assertEqual(caster_vitals.total, 225)
            for _ in range(5):
                self.step(run, caster, hero, target_buttons=1)
            self.step(run, caster, hero)
            arrow, = fight.rockets
            expected = 25 + 100 * 1.375 * (5 * 16 - 1) / 1000
            self.assertAlmostEqual(arrow.direct.amount, expected, delta=0.12)
            for _ in range(20):
                self.step(run, caster, hero)
            self.assertAlmostEqual(caster_vitals.total, 225 - arrow.direct.amount)
            self.assertEqual(len(hero.client.messages(combat.HIT)), 1)
            self.assertEqual(sum(call.args[0] is hero for call in fired.call_args_list), 1)


    def test_arrows_released_before_lethal_flashbang_finish_under_old_body(self):
        self.verify_arrow_replacement_cases(early_respawn=False)

    def test_respawn_retires_leased_arrows_released_before_lethal_flashbang(self):
        with patch.object(combat, "respawn_seconds", return_value=2 * combat.TICK):
            self.verify_arrow_replacement_cases(early_respawn=True)

    def verify_arrow_replacement_cases(self, *, early_respawn):
        short_amount = 25 + 100 * 1.375 * 0.079
        for attack, held_frames, amount in ((0, 70, 125), (8, 70, 125), (16, 0, 70),
                                             (0, 5, short_amount), (8, 5, short_amount)):
            with self.subTest(attack=attack, held_frames=held_frames):
                self.verify_arrow_survives_lethal_flashbang(
                    attack, held_frames, amount, early_respawn=early_respawn,
                )

    def verify_arrow_survives_lethal_flashbang(self, attack, held_frames, amount, *, early_respawn):
        selected = attack == 8
        storm = attack == 16
        run, caster, hero = self.arena()
        fight = run.match.combat
        if selected:
            fight.configure_sonic(sonic_tests.profile())
        victim, step = self.downrange(run, caster, hero, distance=12 if held_frames == 5 else 24)

        for _ in range(12):
            step()
        body = next(t for t in fight._targets() if t.owner is hero)
        owner_health = fight.vitals_of(body)
        owner_health.damage(199)  # Seed 1 HP before the actual Flashbang.
        target = next(t for t in fight._targets() if t.owner is victim)
        victim_health = fight.vitals_of(target)
        old = hero.body
        old_script = hero.body_script
        if attack:
            step(attack)
            step()
        if storm:
            for _ in range(50):
                step(1)
                if any(r.shooter is hero for r in fight.rockets):
                    break
        else:
            for _ in range(held_frames):
                step(1)
            step()
        arrow, = [r for r in fight.rockets if r.shooter is hero]
        self.assertEqual(arrow.source_entity, old)
        self.assertIsNotNone(arrow.shot)
        if selected:
            self.assertEqual(arrow.shot.definition, sonic.DEFINITIONS[held_frames == 5])
        self.assertAlmostEqual(arrow.direct.amount, amount, delta=0.12)
        held = 1 if storm else 0
        step(held, flash=16)
        for _ in range(30):
            if owner_health.dead:
                break
            step(held)
            self.assertFalse(any(r.shooter is hero and r is not arrow for r in fight.rockets))
        self.assertTrue(owner_health.dead)
        charge = old_script.component.entity_var(combat.ULT_CHARGE)
        frozen = charge.value()
        self.assertIn(arrow, fight.rockets)
        self.assertEqual(victim_health.total, 200)
        if early_respawn:
            self.assertIsNotNone(arrow.shot)
            self.assertEqual(arrow.shot.body, old)
            for _ in range(4):
                if hero.body != old:
                    break
                step()
            self.assertNotEqual(hero.body, old)
            self.assertTrue(old_script.retired)
            self.assertFalse(fight.is_dead(hero))
            self.assertFalse(arrow in fight.rockets)
            fresh_charge = hero.body_script.component.entity_var(combat.ULT_CHARGE)
            for _ in range(20):
                step()
            self.assertEqual(victim_health.total, 200)
            self.assertFalse(fight.sonars)
            self.assertEqual(fresh_charge.value(), frozen)
            return
        for _ in range(30):
            if arrow not in fight.rockets:
                break
            step(held)
            self.assertFalse(any(r.shooter is hero and r is not arrow for r in fight.rockets))
        self.assertNotIn(arrow, fight.rockets)
        self.assertAlmostEqual(victim_health.total, 200 - amount, delta=0.12)
        self.assertEqual(charge.value(), frozen)
        self.assertEqual(len(fight.sonars), int(selected))
        if selected:
            beacon, = fight.sonars
            self.assertEqual(beacon.source_entity, old)
            self.assertEqual(beacon.attached, victim.body)
            self.assertTrue(fight.revealed_to(hero, victim))

    def downrange(self, run, caster, hero, *, distance=24):
        fight = run.match.combat
        victim = run.match.add_player(3, "Downrange", SOLDIER, caster.team, False)
        in_world(run.match, victim)
        run.players.append(victim)
        self.assertTrue(run.match.switch_hero(victim, heroes()[SOLDIER]))
        victim.team = caster.team
        x, y, z = hero.mover.position
        victim.mover.position[:] = [x, y, z + distance]
        victim.mover.body = None

        def step(primary=0, flash=0):
            hero_aim = aim(fight._eye(hero), (x, y + 1.2, z + distance))
            caster_aim = aim(fight._eye(caster), (x, y + 1, z))
            run.step({hero: (*hero_aim, primary), caster: (*caster_aim, flash)})

        return victim, step


    def test_initial_dragonstrike_transforms_after_actual_lethal_flashbang(self):
        run, caster, hero = self.arena()
        fight = run.match.combat
        fight.configure_dragonstrike(dragon_tests.prepared_profile())
        victim, step = self.downrange(run, caster, hero)
        for _ in range(12):
            step()
        target = next(t for t in fight._targets() if t.owner is hero)
        owner_health = fight.vitals_of(target)
        owner_health.damage(199)  # Seed 1 HP before the actual Flashbang.
        target = next(t for t in fight._targets() if t.owner is victim)
        victim_health = fight.vitals_of(target)
        old = hero.body
        script = hero.body_script
        script.ult_seconds = 0
        script.set_ult_charge(1680)  # Isolate flight lifecycle from charge acquisition.
        step(32)
        for _ in range(100):
            if fight.rockets:
                break
            step()
        arrow, = fight.rockets
        self.assertIsNotNone(arrow.continuation)
        self.assertEqual(arrow.source_entity, old)
        self.assertEqual(arrow.direct.amount, 125)
        self.assertEqual(script.component.entity_var(combat.ULT_CHARGE).value(), 0)
        step(flash=16)
        for _ in range(30):
            if owner_health.dead:
                break
            step()
        self.assertTrue(owner_health.dead)
        self.assertIn(arrow, fight.rockets)
        self.assertFalse(fight.dragons)
        self.assertEqual(victim_health.total, 200)

        for _ in range(60):
            if fight.dragons:
                break
            step()
        flight, = fight.dragons
        self.assertNotIn(arrow, fight.rockets)
        self.assertEqual(flight.source_entity, old)
        self.assertIs(flight.shooter, hero)
        self.assertTrue(owner_health.dead)
        self.assertEqual(victim_health.total, 200)
        for _ in range(100):
            if victim_health.total < 200:
                break
            step()
        self.assertLess(victim_health.total, 200)
        self.assertTrue(owner_health.dead)
        self.assertEqual(script.component.entity_var(combat.ULT_CHARGE).value(), 0)


    def test_lethal_flashbang_before_dragonstrike_launch_spends_charge_without_stale_arrow(self):
        run, caster, hero = self.arena()
        fight = run.match.combat
        fight.configure_dragonstrike(dragon_tests.prepared_profile())
        target = next(t for t in fight._targets() if t.owner is hero)
        vitals = fight.vitals_of(target)
        vitals.damage(199)  # Seed 1 HP before the actual Flashbang.
        old = hero.body_script
        old.set_ult_charge(1680)  # Isolate the cast and death gates.
        charge = old.component.entity_var(combat.ULT_CHARGE)
        with patch.object(fight.projectiles, "fired", wraps=fight.projectiles.fired) as fired:
            self.step(run, caster, hero, target_buttons=32)
            self.assertTrue(hanzo_control.casting(old))
            self.assertEqual(charge.value(), 0)
            self.step(run, caster, hero, cast=16, target_buttons=32)
            for _ in range(40):
                if vitals.dead:
                    break
                self.step(run, caster, hero, target_buttons=32)
            self.assertTrue(vitals.dead)
            self.assertEqual(charge.value(), 0)
            self.assertFalse(any(call.args[0] is hero for call in fired.call_args_list))

            limit = round(combat.respawn_seconds(fight.match) / combat.TICK) + 120
            for _ in range(limit):
                if hero.body_script is not old:
                    break
                self.step(run, caster, hero, target_buttons=32)
            fresh = hero.body_script
            self.assertIsNot(fresh, old)
            self.assertTrue(old.retired)
            self.assertFalse(fight.is_dead(hero))
            self.assertEqual(fresh.component.entity_var(combat.ULT_CHARGE).value(), 0)
            for _ in range(100):
                self.step(run, caster, hero, target_buttons=32)
            self.assertFalse(hanzo_control.casting(fresh))
            self.assertFalse(any(call.args[0] is hero for call in fired.call_args_list))
            self.assertFalse(fight.dragons)
