"""Actual 104319 Sigma Flux against a Mei body."""

import math
import unittest
from dataclasses import replace

import test_mei_wall as walls
from test_game_combat import aim
from test_mei_combat import static_wall
from test_mei_sigma import scene

from ow174.game import combat, sigma_flux
from ow174.game import mei as mei_rules
from ow174.game.commands import FIRE, SECONDARY
from ow174.game.script import expr


class MeiSigmaFluxTests(unittest.TestCase):
    def arena(self, distance=6.):
        _, run, mei, sigma, fight, sigma_target = scene(self, distance)
        target = next(t for t in fight._targets() if t.owner is mei)
        return run, mei, sigma, fight, fight.vitals_of(target), fight.vitals_of(sigma_target)

    def flux(self, sigma):
        script = sigma.body_script
        self.assertEqual(script.component.entity_var(338).value(), 1960.)
        script.set_ult_charge(script.component.entity_var(338).value())
        return next(i for i in script.component.instances.values() if i.graph.index == sigma_flux.FLUX)

    def effects(self, mei):
        instances = mei.body_script.component.instances.values()
        return tuple(i for i in instances if i.graph.index == sigma_flux.FLUX_EFFECT)

    def test_actual_lift_and_slam_with_and_without_commands(self):
        landings = []
        for silent in (False, True):
            with self.subTest(silent=silent):
                run, mei, sigma, fight, vitals, _ = self.arena()
                instance = self.flux(sigma)
                position = tuple(mei.mover.position)
                for frame in range(148):
                    angle = aim(fight._eye(sigma), position)
                    run.step({sigma: (*angle, 32 if frame == 0 else 1 if frame == 80 else 0)})
                self.assertEqual(instance.vars[375].value(), (expr.Entity(mei.body),))
                self.assertEqual(len(self.effects(mei)), 1)
                self.assertEqual(self.effects(mei)[0].source_entity(), sigma.body)
                self.assertEqual(vitals.total, 200.)
                self.assertTrue(mei.mover.airborne)
                peak = mei.mover.position[1]
                landing = None
                for frame in range(225):
                    if silent:
                        run.frame += 1
                        run.match.update(run.frame * combat.TICK, run.frame, frames=False)
                    else:
                        run.step({})
                    peak = max(peak, mei.mover.position[1])
                    if landing is None and vitals.total < 200.:
                        self.assertEqual(vitals.total, 75.)
                        self.assertFalse(mei.mover.airborne)
                        landing = frame
                self.assertIsNotNone(landing)
                self.assertGreater(peak, position[1] + 2.)
                self.assertEqual(vitals.total, 75.)
                self.assertFalse(self.effects(mei))
                landings.append((landing, tuple(mei.mover.position)))
        self.assertLessEqual(abs(landings[0][0] - landings[1][0]), 2)
        self.assertLess(math.dist(landings[0][1], landings[1][1]), .01)

    def test_confirmed_target_search_respects_team_range_and_cover(self):
        for mode in ('clear', 'static', 'ice', 'ally', 'range'):
            with self.subTest(mode=mode):
                run, mei, sigma, fight, vitals, _ = self.arena()
                if mode == 'ally':
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    mei.team = sigma.team
                x, y, z = mei.mover.position
                if mode == 'static':
                    static_wall(run, mei, distance=1.)
                if mode == 'ice':
                    walls.WallTests().place(run, mei, distance=1.5)
                instance = self.flux(sigma)
                point = (x + 10. if mode == 'range' else x, y, z + 2.)
                for frame in range(155):
                    angle = aim(fight._eye(sigma), point)
                    run.step({sigma: (*angle, 32 if frame == 0 else 1 if frame == 80 else 0)})
                expected = (expr.Entity(mei.body),) if mode == 'clear' else ()
                self.assertEqual(instance.vars[375].value(), expected)
                self.assertEqual(vitals.total, 200. if mode == 'clear' else 250.)

    def test_sky_confirmation_can_retry_on_valid_ground(self):
        run, mei, sigma, fight, vitals, _ = self.arena()
        instance = self.flux(sigma)
        point = tuple(mei.mover.position)
        for frame in range(179):
            angle = (0, -8192) if frame <= 80 else aim(fight._eye(sigma), point)
            buttons = 32 if frame == 0 else 1 if frame in (80, 90) else 0
            run.step({sigma: (*angle, buttons)})
            if frame == 80:
                self.assertEqual(instance.vars[59].value(), 1)
                self.assertFalse(instance.vars[1002].value())
        self.assertEqual(instance.vars[375].value(), (expr.Entity(mei.body),))
        self.assertEqual(instance.vars[59].value(), 3)
        self.assertEqual(vitals.total, 200.)

    def test_cryo_freeze_and_caster_death_prevent_the_slam(self):
        for mode in ('cryo', 'freeze', 'kill'):
            with self.subTest(mode=mode):
                run, mei, sigma, fight, vitals, caster_vitals = self.arena(5.)
                self.flux(sigma)
                if mode == 'kill':
                    caster_vitals.damage(380.)
                position = tuple(mei.mover.position)
                seen_effect = caster_frozen = False
                for frame in range(390):
                    sigma_aim = aim(fight._eye(sigma), position)
                    mei_aim = aim(fight._eye(mei), fight._eye(sigma))
                    buttons = 0
                    if mode == 'cryo' and frame == 108:
                        buttons = 8
                    if mode == 'freeze' and 45 <= frame <= 205:
                        buttons = FIRE
                    if mode == 'kill' and 122 <= frame < 140:
                        buttons = SECONDARY
                    run.step({sigma: (*sigma_aim, 32 if frame == 0 else 1 if frame == 80 else 0),
                              mei: (*mei_aim, buttons)})
                    seen_effect |= bool(self.effects(mei))
                    caster_frozen |= fight.frozen(sigma)
                    if mode == 'cryo' and frame == 142:
                        self.assertTrue(mei_rules.protected(mei))
                self.assertFalse(self.effects(mei))
                self.assertEqual(vitals.total, 200. if mode == 'kill' else 250.)
                if mode == 'cryo':
                    self.assertFalse(seen_effect)
                elif mode == 'freeze':
                    self.assertTrue(caster_frozen)
                else:
                    self.assertTrue(seen_effect)
                    self.assertTrue(caster_vitals.dead)

