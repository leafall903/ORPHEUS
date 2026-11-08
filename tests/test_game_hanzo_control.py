"""Actual McCree Flashbang input versus Hanzo actions; explicit host control policy."""

import unittest
from unittest.mock import patch

import test_mccree_flashbang as flash_tests
from test_game_combat import aim
from test_game_mover import box, wall

from ow174.game import bodytrace, combat, movelog
from ow174.game.collision import World
from ow174.game.commands import JUMP, Command
from ow174.game.content import heroes

HANZO = 0x02E0000000000005


class HanzoControlTests(unittest.TestCase):
    step = flash_tests.FlashbangTests.step
    cast = flash_tests.FlashbangTests.cast

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
