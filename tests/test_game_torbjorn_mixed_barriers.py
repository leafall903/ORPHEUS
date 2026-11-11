"""Actual combat against mixed deployed shields."""

import os
import unittest
from dataclasses import replace

import test_game_torbjorn_orisa as orisa_tests
import test_game_torbjorn_sigma_barrier as sigma_tests
import test_game_torbjorn_winston as winston_tests
from test_game_combat import in_world

from ow174.game import stats
from ow174.game.content import heroes


@unittest.skipUnless(all(os.environ.get(name) for name in (
    "ORPHEUS_SIGMA_COLLISION", "ORPHEUS_ORISA_COLLISION",
    "ORPHEUS_TORBJORN_GRAPH_BUNDLE", "ORPHEUS_PROJECTILE_TAGS",
)), "needs both deployed meshes and prepared combat inputs")
class TorbjornMixedBarrierTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sigma_tests.TorbjornSigmaBarrierTests.setUpClass()
        cls.addClassCleanup(sigma_tests.TorbjornSigmaBarrierTests.doClassCleanups)

    def test_graviton_lands_on_hostile_deployed_shields_and_crosses_allied_ones(self):
        for name in ("Sigma", "Orisa", "Winston"):
            for mode in ("intact", "break", "allied"):
                with self.subTest(shield=name, mode=mode):
                    self.check_graviton_deployed_shield(name, mode)

    def check_graviton_deployed_shield(self, name, mode):
        if name == "Sigma":
            case = sigma_tests.TorbjornSigmaBarrierTests()
            fixture, run, torb, defender, shield = case.arena()
        elif name == "Orisa":
            case = orisa_tests.TorbjornOrisaTests()
            fixture, run, torb, defender, _ = case.arena()
        else:
            case = winston_tests.TorbjornWinstonTests()
            fixture, run, torb, defender, _ = case.arena()

        match = run.match
        fight = match.combat
        caster = run.players[2]
        zarya = next(hero for hero in heroes().values() if hero.name == "Zarya")
        self.assertTrue(match.switch_hero(caster, zarya))
        caster.body_script.ult_seconds = 0
        match.game_map = replace(match.game_map, free_for_all=False)
        caster.team = 1 - torb.team
        defender.team = caster.team if mode == "allied" else torb.team
        if name == "Sigma":
            case.deploy(fixture, run, torb, defender, shield)
            x, _, face_z = shield.position
        elif name == "Orisa":
            shield = case.deploy(run, defender)
            x, _, face_z = shield.position
        else:
            shield = case.cast(fixture, run, torb, defender)
            x, _, centre_z = shield.point
            face_z = centre_z - shield.radius
        y = torb.mover.position[1]
        fixture.place(torb, (x, y, face_z + 1.5))
        fixture.place(caster, (x, y, face_z - 5.5))
        fixture.place(defender, (x + 10, y, face_z + 5))
        fixture.advance(run, torb, caster, 35)
        if mode == "break":
            fight._shield_damage(caster, shield, shield.health - 3)
            self.assertEqual(shield.health, 3)
        capacity = shield.health
        hp = fixture.health(run, torb)
        base = dict(hp.now)
        caster.body_script.set_ult_charge(caster.body_script.component.entity_var(338).value())
        fixture.step(run, torb, caster, fire=16, defense=32)
        armour = hp.over_armour.source
        for _ in range(45):
            if fight.gravitons.fields:
                break
            fixture.step(run, torb, caster)
        field, = fight.gravitons.fields
        self.assertIs(field.owner, caster)
        direct = 2.5 if mode == "allied" else 0
        remaining = capacity if mode == "allied" else max(0, capacity - 5)
        self.assertEqual(shield.health, remaining)
        if mode == "allied":
            self.assertGreater(field.point[2], face_z + .75)
        else:
            self.assertAlmostEqual(field.point[2], face_z, delta=.5)
        self.assertEqual(hp.over_armour.amount, 100 - direct)
        self.assertEqual(caster.stats.value(stats.HERO_DAMAGE), direct)
        fixture.advance(run, torb, caster, 24)
        self.assertIn(torb.body, fight.gravitons.pulled)
        self.assertLess(torb.mover.position[2], face_z + (1.3 if mode == "allied" else .9))
        self.assertEqual(shield.health, remaining)
        self.assertAlmostEqual(hp.over_armour.amount, 100 - direct - 1.92)
        while run.frame < field.end_tick:
            run.step({})
        self.assertFalse(fight.gravitons.fields)
        self.assertFalse(fight.gravitons.pulled)
        self.assertEqual(torb.mover.velocity, (0, 0, 0))
        if name == "Sigma" and mode == "break":
            self.assertGreater(shield.health, 0)
            self.assertFalse(shield.active())
        else:
            self.assertEqual(shield.health, remaining)
        self.assertEqual(hp.over_armour.source, armour)
        self.assertAlmostEqual(hp.over_armour.amount, 80 - direct)
        self.assertEqual(hp.now, base)
        self.assertAlmostEqual(caster.stats.value(stats.HERO_DAMAGE), 20 + direct)

    def test_flashbang_breaks_front_sigma_and_spares_rear_orisa_and_torbjorn(self):
        case = sigma_tests.TorbjornSigmaBarrierTests()
        fixture, run, torb, sigma, front = case.arena()
        fight = run.match.combat
        caster = run.players[2]
        run.match.switch_hero(caster, heroes()[0x02E0000000000042])
        orisa = run.match.add_player(8, "Orisa", 0x02E000000000013E, torb.team, False)
        in_world(run.match, orisa)
        run.players.append(orisa)
        run.match.switch_hero(orisa, heroes()[0x02E000000000013E])
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        sigma.team = orisa.team = torb.team
        caster.team = 1 - torb.team
        x, y, z = torb.mover.position
        fixture.place(sigma, (x, y, z + 8))
        fixture.place(orisa, (x, y, z + 7))
        fixture.place(caster, (x, y, z))
        fixture.advance(run, torb, sigma, 40)
        run.step({sigma: (-32768, 0, 2), orisa: (-32768, 8192, 16)})
        for _ in range(14):
            run.step({sigma: (-32768, 0, 2), orisa: (-32768, 8192, 0)})
        for _ in range(30):
            run.step({orisa: (-32768, 8192, 0)})
        rear = fight.orisa_barriers.actors[orisa.body]
        self.assertEqual(front.mode, 3)
        self.assertLess(front.position[2], rear.position[2])
        self.assertLess(rear.position[2] - front.position[2], 2.5)
        fixture.place(caster, (x, y, front.position[2] - 2))
        fixture.place(torb, (x, y, (front.position[2] + rear.position[2]) * .5))
        fixture.advance(run, torb, sigma, 10)
        # Seed a breaking contact. The damage and stun under test come from actual E.
        fight.sigma_barriers.damage(front, 680)
        yaw, pitch = fixture.body_aim(run, caster, torb)
        run.step({caster: (yaw, pitch, 16)})
        for _ in range(40):
            run.step({caster: (yaw, pitch, 0)})
            self.assertFalse(fight.is_stunned(torb))
        self.assertFalse(front.active())
        self.assertEqual(rear.health, 600)
        self.assertEqual(fixture.health(run, torb).total, 250)

        # The same aim is now exposed. Wait out Cassidy's authored cooldown.
        for _ in range(680):
            run.step({caster: (yaw, pitch, 0)})
        self.assertTrue(rear.active())
        run.step({caster: (yaw, pitch, 16)})
        stunned = False
        for _ in range(40):
            run.step({caster: (yaw, pitch, 0)})
            stunned |= fight.is_stunned(torb)
        self.assertTrue(stunned)
        self.assertEqual(fixture.health(run, torb).total, 230)
        self.assertLess(rear.health, 600)

    def test_breaking_splash_pins_sphere_and_mesh_in_both_orders(self):
        for shell_first in (False, True):
            with self.subTest(shell_first=shell_first):
                case = sigma_tests.TorbjornSigmaBarrierTests()
                fixture, run, torb, sigma, mesh = case.arena()
                fight = run.match.combat
                caster = run.players[2]
                run.match.switch_hero(caster, heroes()[0x02E0000000000042])
                winston = run.match.add_player(8, "Winston", 0x02E0000000000009, torb.team, False)
                in_world(run.match, winston)
                run.players.append(winston)
                run.match.switch_hero(winston, heroes()[0x02E0000000000009])
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                sigma.team = winston.team = torb.team
                caster.team = 1 - torb.team
                case.deploy(fixture, run, torb, sigma, mesh)
                x, _, z = mesh.position
                y = torb.mover.position[1]
                shell_edge = z - 1.4 if shell_first else z + 1.4
                fixture.place(winston, (x, y, shell_edge + 5))
                fixture.advance(run, torb, sigma, 24)
                run.step({winston: (0, 0, 16)})
                fixture.advance(run, torb, sigma, 14)
                shell, = fight.world_bubbles.actors.values()
                fixture.place(caster, (x, y, min(z, shell_edge) - 2))
                fixture.place(torb, (x, y, (z + shell_edge) * .5))
                fixture.advance(run, torb, sigma, 6)
                front, rear = (shell, mesh) if shell_first else (mesh, shell)
                # Preset a breaking impact while leaving the rear shield at full capacity.
                fight._shield_damage(caster, front, 680)
                yaw, pitch = fixture.body_aim(run, caster, torb)
                run.step({caster: (yaw, pitch, 16)})
                for _ in range(38):
                    run.step({caster: (yaw, pitch, 0)})
                    self.assertFalse(fight.is_stunned(torb))
                self.assertLess(front.health, 20)
                self.assertEqual(rear.health, 700)
                self.assertEqual(fixture.health(run, torb).total, 250)
