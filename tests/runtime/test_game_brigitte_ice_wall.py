"""Actual 104319 Ice Wall input against Brigitte's attacks."""

import unittest
from dataclasses import replace

import test_game_brigitte as brigitte_tests
import test_game_brigitte_shield as shield_tests
from test_game_combat import aim

from ow174.game.commands import FIRE, SECONDARY
from ow174.game.content import find_hero


class IceWallTests(unittest.TestCase):
    def place(self, *, distance=6, wall_distance=3, support=False, friendly_wall=False):
        case = brigitte_tests.BrigitteTests()
        names = ("Brigitte", "Mei", "Ally", "Enemy") if support else ("Brigitte", "Mei")
        run, brig = case.fixture(names)
        mei = run.players[1]
        self.assertTrue(run.match.switch_hero(mei, find_hero("Mei")))
        x, y, z = brig.mover.position
        mei.mover.position, mei.mover.body = [x, y, z + distance], None
        fight = run.match.combat
        if support:
            run.match.game_map = replace(run.match.game_map, free_for_all=False)
            ally, enemy = run.players[2:]
            brig.team = ally.team = 0
            enemy.team = 1
            mei.team = 0 if friendly_wall else 1
            ally.mover.position, ally.mover.body = [x, y, z + 5], None
            enemy.mover.position, enemy.mover.body = [x + 1, y, z + 2], None
        for _ in range(20):
            run.step({})
        angles = aim(fight._eye(mei), (x, y, z + wall_distance))
        for button in (16, 0):
            run.step({mei: (*angles, button)})
        self.assertTrue(fight.walls.inputs[mei.body].preview)
        self.assertFalse(fight.walls.pieces)
        for button in (1, 0):
            run.step({mei: (*angles, button)})
        pieces = list(fight.walls.pieces.values())
        self.assertEqual([piece.health for piece in pieces], [400] * 5)
        self.assertFalse(fight.walls.inputs[mei.body].preview)
        self.assertGreater(fight.walls.instance(mei).find_var(0, 61).value(), 11)
        return case, run, brig, mei, pieces[2]

    def test_actual_ice_wall_stops_whip_and_cancel_or_expiry_restores_contact(self):
        for release in ("cancel", "expiry", "break"):
            with self.subTest(release=release):
                case, run, brig, mei, pillar = self.place()
                fight = run.match.combat
                hp = fight.vitals_of(case.target(run, mei))
                before = hp.total
                if release == "break":
                    fight.walls.damage(pillar, brig, 350)
                case.whip(run, brig)
                self.assertEqual(hp.total, before)
                self.assertEqual(pillar.health, 0 if release == "break" else 330)
                self.assertFalse(fight.brigitte.inspire)
                if release == "cancel":
                    run.step({mei: (-32768, 0, 16)})
                    self.assertFalse(fight.walls.pieces)
                if release == "break":
                    self.assertNotIn(pillar.entity, fight.walls.pieces)
                    self.assertEqual(len(fight.walls.pieces), 4)
                for _ in range(318):
                    run.step({})
                self.assertFalse(fight.walls.pieces)
                case.whip(run, brig)
                self.assertEqual(hp.total, before - 70)

    def test_actual_ice_wall_stops_bash_travel_and_prevents_control(self):
        case, run, brig, mei, pillar = self.place(distance=4.5, wall_distance=2.5)
        fight = run.match.combat
        hp = fight.vitals_of(case.target(run, mei))
        before = hp.total
        z = brig.mover.position[2]
        shield_case = shield_tests.BarrierTests()
        shield_case.raise_shield(run, brig, 25)
        shield_case.bash(run, brig)
        self.assertGreater(brig.mover.position[2], z)
        self.assertLess(brig.mover.position[2], pillar.feet[2])
        self.assertEqual(pillar.health, 400)
        self.assertEqual(hp.total, before)
        self.assertFalse(fight.is_stunned(mei))

    def test_flail_and_quick_melee_break_pillar_without_hitting_covered_mei(self):
        for button in (1, 0x800):
            with self.subTest(button=button):
                case, run, brig, mei, pillar = self.place(distance=3.5, wall_distance=2.2)
                fight = run.match.combat
                hp = fight.vitals_of(case.target(run, mei))
                before = hp.total
                fight.walls.damage(pillar, brig, 380)
                run.step({brig: (0, 0, button)})
                for _ in range(70):
                    run.step({})
                self.assertEqual(pillar.health, 0)
                self.assertEqual(hp.total, before)
                self.assertFalse(fight.brigitte.inspire)
                run.step({brig: (0, 0, button)})
                for _ in range(30):
                    run.step({})
                self.assertEqual(hp.total, before - 35)

    def test_ice_wall_occludes_repair_and_inspire_but_rally_pulses_cross(self):
        for ability in ("repair", "inspire", "rally"):
            with self.subTest(ability=ability):
                case, run, brig, mei, _ = self.place(support=True)
                ally, enemy = run.players[2:]
                fight = run.match.combat
                target = case.target(run, ally)
                hp = fight.vitals_of(target)
                fight._damage(enemy, target, 120, False, None, run.frame)
                before = hp.total
                key = (brig.body, ally.body)
                repair = next(i for i in brig.body_script.component.instances.values()
                              if i.graph.index == 0x0EDD)
                if ability == "rally":
                    component = brig.body_script.component
                    brig.body_script.set_ult_charge(component.entity_var(338).value())
                    run.step({brig: (0, 0, 32)})
                    self.assertEqual(hp.temporary_armour, 15)
                    self.assertEqual(hp.total, before + 15)
                    continue
                if ability == "repair":
                    run.step({brig: (0, 0, 16)})
                    for _ in range(60):
                        run.step({})
                    self.assertEqual(repair.vars[11].value(), 3)
                    self.assertFalse(fight.brigitte.repair_deliveries)
                else:
                    case.swing(run, brig, 20)
                    self.assertIn((brig.body, brig.body), fight.brigitte.inspire)
                    self.assertNotIn(key, fight.brigitte.inspire)
                self.assertEqual(hp.total, before)
                run.step({mei: (-32768, 0, 16)})
                self.assertFalse(fight.walls.pieces)
                for _ in range(85):
                    run.step({})
                if ability == "repair":
                    run.step({brig: (0, 0, 16)})
                    for _ in range(170):
                        run.step({})
                    self.assertAlmostEqual(hp.total, before + 110, places=5)
                else:
                    case.swing(run, brig, 20)
                    self.assertIn(key, fight.brigitte.inspire)
                    self.assertGreater(hp.total, before)

    def test_live_ice_wall_intercepts_rifle_and_helix_before_raised_shield(self):
        for button, damage in ((FIRE, 20), (SECONDARY, 120)):
            for relation in ("friendly", "hostile", "break"):
                with self.subTest(button=button, relation=relation):
                    case, run, brig, mei, pillar = self.place(
                        wall_distance=1.8, support=True, friendly_wall=relation != "friendly",
                    )
                    ally, shooter = run.players[2:]
                    x, y, z = brig.mover.position
                    for actor, where in ((mei, [x + 5, y, z + 7]), (ally, [x + 5, y, z]),
                                         (shooter, [x, y, z + 7])):
                        actor.mover.position, actor.mover.body = where, None
                    shield_case = shield_tests.BarrierTests()
                    shield_case.raise_shield(run, brig, 25)
                    fight = run.match.combat
                    shield = fight.brigitte.barriers[brig.body]
                    hp = fight.vitals_of(case.target(run, brig))
                    initial = hp.total
                    if relation == "break":
                        fight.walls.damage(pillar, shooter, 385)
                    run.step({brig: (0, 0, SECONDARY), shooter: (-32768, 0, button)})
                    if button == SECONDARY:
                        self.assertTrue(any(shot.shooter is shooter for shot in fight.rockets))
                    shield_case.raise_shield(run, brig, 50)
                    expected = {"friendly": 400, "hostile": 400 - damage, "break": 0}
                    self.assertEqual(pillar.health, expected[relation])
                    self.assertEqual(shield.health, 250)
                    self.assertEqual(hp.total, initial)
                    self.assertEqual(pillar.entity in fight.walls.pieces, relation != "break")
