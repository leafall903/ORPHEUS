"""Actual 104319 Symmetra primary against Zenyatta."""

import os
import unittest
from itertools import product

import test_zenyatta_bubbles as bubbles
from test_game_combat import Run, aim

from ow174.game import combat, stats
from ow174.game.barriers import PERSONAL, PROJECTED, ZARYA
from ow174.game.body_vm_snapshot import export_body_vm, snapshot_digest
from ow174.game.collision import World
from ow174.game.commands import FIRE, SECONDARY
from ow174.game.content import heroes
from ow174.game.script import expr
from ow174.game.symmetra import HERO as SYMMETRA
from ow174.game.winston import HERO as WINSTON
from ow174.game.zenyatta import HERO as ZEN


class SymmetraBeamTests(unittest.TestCase):
    def test_actual_sleep_interrupts_charged_beam_and_only_observed_input_restarts(self):
        for early, omitted in product((False, True), (0, 3)):
            with self.subTest(early=early, omitted=omitted):
                run = Run(names=("Symmetra", "Zen", "Ana"))
                symmetra, zen, ana = run.players
                run.match.bots.clear()
                x, y, z = symmetra.mover.position
                for player, guid, team, dx, dz in (
                    (symmetra, SYMMETRA, 0, 0, 0), (zen, ZEN, 1, 0, 4),
                    (ana, 0x02E000000000013B, 1, 3, 4),
                ):
                    self.assertTrue(run.match.switch_hero(player, heroes()[guid]))
                    player.team = team
                    player.mover.position = [x+dx, y, z+dz]
                    player.mover.body = None
                for _ in range(30):
                    run.step({})
                fight = run.match.combat
                weapon = next(i for i in symmetra.body_script.component.instances.values()
                              if i.graph.index == 0x157C)
                firing = {symmetra: (0, 0, FIRE)}
                for _ in range(90):
                    run.step(firing)
                rate, ammo = weapon.vars[499], weapon.vars[53]
                state = weapon.node_state(weapon.graph.node(170))
                self.assertTrue(state.active)
                self.assertEqual(rate.value(), 120)
                activation = state.counter
                damage = symmetra.stats.value(stats.HERO_DAMAGE)
                run.frame += 3
                run.step(firing)
                self.assertEqual(state.counter, activation)
                self.assertAlmostEqual(symmetra.stats.value(stats.HERO_DAMAGE)-damage, 120*.064, places=4)

                dart_aim = aim(fight._eye(ana), (x, y+1.15, z))
                saw_dart = False
                for frame in range(55):
                    run.step({**firing, ana: (*dart_aim, 8 if frame == 0 else 0)})
                    saw_dart |= any(r.direct and r.direct.effect == combat.ANA_SLEEP_EFFECT for r in fight.rockets)
                    if symmetra.body in fight.sleeps:
                        break
                self.assertTrue(saw_dart)
                self.assertIn(symmetra.body, fight.sleeps)
                start, end, waking = fight.sleeps[symmetra.body]
                self.assertEqual(end-start, 313)
                self.assertFalse(waking)
                self.assertFalse(state.active)
                self.assertNotIsInstance(weapon.vars[536].value(), expr.Entity)
                frozen = ammo.value(), symmetra.stats.value(stats.HERO_DAMAGE)
                if early:
                    while run.frame < start+36:
                        run.step(firing)
                    orb_aim = aim(fight._eye(zen), (x, y+1.15, z))
                    run.step({**firing, zen: (*orb_aim, FIRE)})
                    self.assertTrue(any(orb.shooter is zen for orb in fight.rockets))
                    for _ in range(20):
                        if fight.sleeps[symmetra.body][2]:
                            break
                        run.step(firing)
                    _, end, waking = fight.sleeps[symmetra.body]
                    self.assertTrue(waking)
                    self.assertEqual(end-run.frame, 32)
                while run.frame < end-omitted-1:
                    run.step(firing)
                    self.assertTrue(fight.is_stunned(symmetra))
                    self.assertFalse(state.active)
                    self.assertEqual((ammo.value(), symmetra.stats.value(stats.HERO_DAMAGE)), frozen)
                expected_rate = 120 if early else 60
                self.assertEqual(rate.value(), expected_rate)
                run.frame += omitted
                run.step(firing)
                self.assertEqual(run.frame, end)
                self.assertNotIn(symmetra.body, fight.sleeps)
                self.assertTrue(state.active)
                self.assertEqual(state.counter, activation+1)
                self.assertAlmostEqual(symmetra.stats.value(stats.HERO_DAMAGE)-frozen[1],
                                       expected_rate*.015, places=4)

    def test_actual_primary_requires_visible_enemy_and_respects_transcendence(self):
        for case in ("body", "wall", "range", "friendly", "miss", "ultimate"):
            with self.subTest(case=case):
                run = Run(names=("Symmetra", "Zen"))
                symmetra, zen = run.players
                run.match.bots.clear()
                x, y, z = symmetra.mover.position
                self.assertTrue(run.match.switch_hero(symmetra, heroes()[SYMMETRA]))
                self.assertTrue(run.match.switch_hero(zen, heroes()[ZEN]))
                symmetra.team, zen.team = 0, 0 if case == "friendly" else 1
                symmetra.mover.position = [x, y, z]
                zen.mover.position = [x+(3 if case == "miss" else 0), y, z+(14 if case == "range" else 4)]
                symmetra.mover.body = zen.mover.body = None
                for _ in range(30):
                    run.step({})
                fight = run.match.combat
                if case == "wall":
                    plane = z+2
                    run.match.collision_world = World([
                        (x-4, y-2, plane, x+4, y-2, plane, x-4, y+4, plane),
                        (x+4, y-2, plane, x+4, y+4, plane, x-4, y+4, plane),
                    ])
                if case == "ultimate":
                    zen.body_script.set_ult_charge(zen.body_script.component.entity_var(338).value())
                    run.step({zen: (0, 0, 0x20)})
                    self.assertTrue(fight.zenyatta.ultimate(zen))
                weapon = next(i for i in symmetra.body_script.component.instances.values()
                              if i.graph.index == 0x157C)
                ammo = weapon.vars[53].value()
                for _ in range(30):
                    run.step({symmetra: (0, 0, FIRE)})
                self.assertLess(weapon.vars[53].value(), ammo)
                self.assertFalse(fight.rockets)
                state = weapon.node_state(weapon.graph.node(170))
                selected = case in ("body", "ultimate")
                self.assertEqual(state.active, selected)
                self.assertEqual(weapon.vars[536].value(), expr.Entity(zen.body) if selected else None)
                vitals = fight.vitals[zen.body]
                self.assertAlmostEqual(vitals.total, 200-60*.479 if case == "body" else 200, places=4)
                if case == "body":
                    self.assertEqual(len(snapshot_digest(export_body_vm(symmetra.body_script))), 64)
                    before = vitals.total
                    zen.mover.position[0] += 3
                    zen.mover.body = None
                    run.step({symmetra: (0, 0, FIRE)})
                    self.assertEqual(vitals.total, before)
                    self.assertFalse(state.active)
                    self.assertIsNone(weapon.vars[536].value())
                    zen.mover.position[0] -= 3
                    zen.mover.body = None
                    run.step({symmetra: (0, 0, FIRE)})
                    self.assertTrue(state.active)
                    self.assertAlmostEqual(before-vitals.total, 60*.015, places=4)
                before = vitals.total
                for _ in range(8):
                    run.step({})
                self.assertFalse(state.active)
                self.assertEqual(vitals.total, before)

    def test_actual_primary_ramps_on_target_and_decays_after_release(self):
        run = Run(names=("Symmetra", "Transcendent Zen", "Zen"))
        symmetra, protected, zen = run.players
        run.match.bots.clear()
        x, y, z = symmetra.mover.position
        for player, guid, team, offset in (
            (symmetra, SYMMETRA, 0, 0), (protected, ZEN, 1, 4), (zen, ZEN, 1, -8),
        ):
            self.assertTrue(run.match.switch_hero(player, heroes()[guid]))
            player.team = team
            player.mover.position = [x, y, z+offset]
            player.mover.body = None
        for _ in range(30):
            run.step({})
        fight = run.match.combat
        protected.body_script.set_ult_charge(protected.body_script.component.entity_var(338).value())
        run.step({protected: (0, 0, 0x20)})
        self.assertTrue(fight.zenyatta.ultimate(protected))
        weapon = next(i for i in symmetra.body_script.component.instances.values() if i.graph.index == 0x157C)
        rate = weapon.vars[499]
        firing = {symmetra: (0, 0, FIRE)}
        other = {symmetra: (-32768, 0, FIRE)}
        vitals = fight.vitals[zen.body]
        self.assertEqual(rate.value(), 60)
        for _ in range(83):
            run.step(firing)
            self.assertEqual(rate.value(), 60)
        run.step(firing)
        self.assertEqual(rate.value(), 120)
        run.step(other)
        before = vitals.total
        run.step(other)
        self.assertAlmostEqual(before-vitals.total, 120*.016, places=4)
        for _ in range(81):
            run.step(firing)
            self.assertEqual(rate.value(), 120)
        run.step(firing)
        self.assertEqual(rate.value(), 180)
        run.step(other)
        before = vitals.total
        run.step(other)
        self.assertAlmostEqual(before-vitals.total, 180*.016, places=4)
        for _ in range(100):
            run.step(firing)
        self.assertTrue(fight.zenyatta.ultimate(protected))
        self.assertEqual(fight.vitals[protected.body].total, 200)
        self.assertFalse(fight.rockets)

        state = weapon.node_state(weapon.graph.node(170))
        run.step({})
        self.assertFalse(state.active)
        for _ in range(119):
            run.step({})
            self.assertEqual(rate.value(), 180)
        observed = [(0, rate.value())]
        for frame in range(121, 401):
            run.step({})
            if rate.value() != observed[-1][1]:
                observed.append((frame, rate.value()))
        self.assertEqual(observed, [(0, 180), (250, 120), (375, 60)])

    def test_actual_primary_damages_dome_refills_ammo_and_reaches_zen_after_break(self):
        run = Run(names=("Symmetra", "Zen", "Winston"))
        symmetra, zen, winston = run.players
        run.match.bots.clear()
        x, y, z = symmetra.mover.position
        for player, guid, team, offset in (
            (symmetra, SYMMETRA, 0, 0), (zen, ZEN, 1, 5), (winston, WINSTON, 1, 7),
        ):
            self.assertTrue(run.match.switch_hero(player, heroes()[guid]))
            player.team = team
            player.mover.position = [x, y, z+offset]
            player.mover.body = None
        for _ in range(90):
            run.step({})
        run.step({winston: (-32768, 0, 0x10)})
        fight = run.match.combat
        for _ in range(45):
            dome = fight.barriers.domes.get(winston.body)
            if dome is not None and dome.hp > 0:
                break
            run.step({})
        self.assertIsNotNone(dome)
        self.assertEqual(dome.hp, 700)
        weapon = next(i for i in symmetra.body_script.component.instances.values() if i.graph.index == 0x157C)
        ammo = weapon.vars[53]
        maximum = weapon.vars[198].value()
        for _ in range(60):
            run.step({symmetra: (-32768, 0, FIRE)})
        spent = ammo.value()
        self.assertLess(spent, maximum)
        firing = {symmetra: (0, 0, FIRE)}
        vitals = fight.vitals[zen.body]
        for _ in range(30):
            run.step(firing)
        self.assertEqual(weapon.vars[536].value(), expr.Entity(dome.entity))
        self.assertAlmostEqual(dome.hp, 700-60*.479, places=4)
        self.assertGreater(ammo.value(), spent)
        self.assertLessEqual(ammo.value(), maximum)
        self.assertEqual(vitals.total, 200)
        before_ammo, before_hp = ammo.value(), dome.hp
        run.step(firing)
        self.assertAlmostEqual(ammo.value()-before_ammo, 7*.016, places=4)
        self.assertAlmostEqual(before_hp-dome.hp, 60*.016, places=4)
        for _ in range(375):
            run.step(firing)
            if dome.hp == 0:
                break
            self.assertEqual(vitals.total, 200)
        self.assertTrue(dome.retired)
        self.assertEqual(dome.hp, 0)
        self.assertEqual(vitals.total, 200)
        self.assertEqual(weapon.vars[499].value(), 180)
        self.assertAlmostEqual(ammo.value(), maximum, places=3)
        for _ in range(2):
            run.step(firing)
        self.assertEqual(weapon.vars[536].value(), expr.Entity(zen.body))
        self.assertLess(vitals.total, 200)
        self.assertLess(ammo.value(), maximum)

    @unittest.skipUnless(os.environ.get("ORPHEUS_BARRIER_DATA"), "requires prepared Reinhardt barrier mesh")
    def test_actual_primary_targets_reinhardt_field_and_both_zarya_bubbles(self):
        definitions = heroes()
        reinhardt = next(hero for hero in definitions.values() if hero.name == "Reinhardt")
        for case in ("reinhardt", "personal", "projected"):
            with self.subTest(case=case):
                run = Run(names=("Symmetra", "Zen", "Guard"))
                symmetra, zen, guard = run.players
                run.match.bots.clear()
                x, y, z = symmetra.mover.position
                dx, dz = {"reinhardt": (1.8, 3), "personal": (0, 3), "projected": (4, 5)}[case]
                for player, hero, team, px, pz in (
                    (symmetra, definitions[SYMMETRA], 0, 0, 0),
                    (zen, definitions[ZEN], 1, 0, 5),
                    (guard, reinhardt if case == "reinhardt" else definitions[ZARYA], 1, dx, dz),
                ):
                    self.assertTrue(run.match.switch_hero(player, hero))
                    player.team = team
                    player.mover.position = [x+px, y, z+pz]
                    player.mover.body = None
                for _ in range(45):
                    run.step({symmetra: (-32768, 0, FIRE)})
                fight = run.match.combat
                holding = {}
                if case == "reinhardt":
                    holding[guard] = (-32768, 0, SECONDARY)
                    for _ in range(8):
                        run.step(holding)
                    shield = fight.barriers.owners[guard.body]
                    self.assertTrue(shield.raised)
                else:
                    direction = aim(fight._eye(guard), (x, y+1.5, z+5))
                    run.step({guard: (*direction, 16 if case == "projected" else 8)})
                    shield = fight.barriers.bubbles[guard.body, PROJECTED if case == "projected" else PERSONAL]
                weapon = next(i for i in symmetra.body_script.component.instances.values()
                              if i.graph.index == 0x157C)
                ammo = weapon.vars[53]
                spent, before = ammo.value(), shield.hp
                health = fight.vitals[zen.body], fight.vitals[guard.body]
                totals = tuple(v.total for v in health)
                firing = {symmetra: (0, 0, FIRE)}
                for _ in range(30):
                    run.step({**holding, **firing})
                selected = weapon.vars[536].value()
                self.assertIsInstance(selected, expr.Entity)
                self.assertEqual(selected.id, shield.entity)
                self.assertNotIn(selected.id, (guard.body, zen.body))
                self.assertAlmostEqual(before-shield.hp, 60*.479, places=4)
                self.assertGreater(ammo.value(), spent)
                self.assertEqual(tuple(v.total for v in health), totals)
                self.assertIs(fight.barriers.live_target(shield.entity), shield)
                if case == "reinhardt":
                    old_id = shield.entity
                    run.step({guard: (-32768, 0, 0)})
                    run.step({})
                    self.assertFalse(shield.raised)
                    self.assertIsNone(fight.barriers.live_target(old_id))
                    for _ in range(8):
                        run.step(holding)
                    self.assertTrue(shield.raised)
                    self.assertNotEqual(shield.entity, old_id)
                else:
                    self.assertAlmostEqual(bubbles.ZenyattaBubbleTests().energy(guard), 60*.479*.2, places=4)
                    for _ in range(125):
                        if fight.barriers.live_target(shield.entity) is None:
                            break
                        self.assertEqual(tuple(v.total for v in health), totals)
                        run.step(firing)
                    self.assertIsNone(fight.barriers.live_target(shield.entity))
                    before_ammo = ammo.value()
                    for _ in range(2):
                        run.step(firing)
                    target = zen if case == "projected" else guard
                    self.assertEqual(weapon.vars[536].value(), expr.Entity(target.body))
                    self.assertLess(fight.vitals[target.body].total, totals[0 if case == "projected" else 1])
                    self.assertLess(ammo.value(), before_ammo)
