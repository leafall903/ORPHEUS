"""Actual Pulse Bomb interactions with projectile guards and Ice Wall."""

import unittest
from dataclasses import replace

import test_game_sombra_grenade as grenades
import test_game_sombra_pulse_bomb as pulse
from test_game_combat import aim
from test_game_sombra_mei_wall import tap
from test_game_sombra_orisa_halt import place
from test_game_sombra_reaper_blossom import charge

from ow174.game import combat, mei, shot_guards
from ow174.game.content import heroes


class SombraPulseGuardTests(unittest.TestCase):
    def guard(self, hero):
        f, fight = pulse.SombraPulseBombTests().arena()
        definition = heroes()[hero]
        if hero == shot_guards.DVA:
            definition = replace(definition, body=shot_guards.MECH)
        self.assertTrue(f.run.match.switch_hero(f.a, definition))
        place(f.a, (0, 0, 0))
        f.press(0, 100 if hero == shot_guards.DVA else 40)
        face = (0, 0) if hero == shot_guards.DVA else aim(fight._eye(f.a), (0, 1.15, 3))
        attack = aim(fight._eye(f.b), (0, 1.15, 0))
        button = {shot_guards.GENJI: 16, shot_guards.SIGMA: 8, shot_guards.DVA: 2}[hero]
        held = button if hero == shot_guards.DVA else 0
        f.run.step({f.a: (*face, button)})
        for _ in range(4):
            f.run.step({f.a: (*face, held)})
        state = shot_guards.active_guard(f.a, fight.tick * combat.TICK * 1000)
        self.assertIsNotNone(state)
        seen = False
        for frame in range(70):
            f.run.step({f.a: (*face, held), f.b: (*attack, 32 if frame == 0 else 0)})
            bombs = pulse.bombs(fight)
            seen |= bool(bombs)
            if hero == shot_guards.GENJI and any(r.shooter is f.a for r in bombs):
                return f, fight, state, bombs[0]
        self.assertTrue(seen)
        return f, fight, state, None

    def test_genji_returns_actual_bomb_and_owns_the_detonation(self):
        f, fight, _, rocket = self.guard(shot_guards.GENJI)
        self.assertIsNotNone(rocket)
        self.assertEqual(rocket.source_body, f.a.body)
        for _ in range(95):
            f.run.step({})
        self.assertFalse(pulse.bombs(fight))
        self.assertTrue(fight.is_dead(f.b))
        self.assertEqual(fight.vitals[f.b.body].killer, f.a.body)

    def test_sigma_grasp_absorbs_contact_and_blast_without_explosion(self):
        f, fight, state, _ = self.guard(shot_guards.SIGMA)
        self.assertFalse(pulse.bombs(fight))
        self.assertEqual(state.instance.vars[21312].value(), 355)
        self.assertEqual(f.health(f.a), 400)
        self.assertEqual(f.health(f.b), 150)

    def test_matrix_consumes_bomb_without_damage_to_mech_or_tracer(self):
        f, fight, _, _ = self.guard(shot_guards.DVA)
        self.assertFalse(pulse.bombs(fight))
        self.assertEqual(f.health(f.a), 600)
        self.assertEqual(f.health(f.b), 150)
        self.assertEqual(charge(f.b), 0)

    def test_pulse_sticks_to_actual_pillar_and_blast_uses_its_approach_side(self):
        f, fight = pulse.SombraPulseBombTests().arena()
        caster = grenades.SombraGrenadeTests().join_ally(f, mei.HERO, (0, 0, -3))
        face = aim(fight._eye(caster), (0, 0, 1.5))
        tap(f, caster, 16, face)
        tap(f, caster, 1, face)
        pieces = tuple(fight.walls.pieces.values())
        self.assertEqual(len(pieces), 5)
        centre = pieces[2]
        rocket = pulse.throw(f)
        pulse.wait_for_contact(f, rocket)
        self.assertEqual(rocket.pulse.pillar, centre.entity)
        self.assertEqual(centre.health, 395)
        for _ in range(66):
            f.run.step({})
        self.assertEqual(centre.health, 45)
        self.assertEqual(f.health(f.a), 200)
        self.assertFalse(pulse.bombs(fight))
