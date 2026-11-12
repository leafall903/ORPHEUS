"""Actual 104319 Tesla Cannon input against Zenyatta."""

import os
import unittest
from dataclasses import replace
from itertools import product

import test_zenyatta_bubbles as bubbles
from test_game_combat import Run, aim

from ow174.game import combat, dva
from ow174.game.barriers import PROJECTED, ZARYA
from ow174.game.collision import World
from ow174.game.commands import FIRE, SECONDARY
from ow174.game.content import heroes
from ow174.game.script import expr
from ow174.game.winston import HERO as WINSTON
from ow174.game.zenyatta import HERO as ZENYATTA


class WinstonTeslaTests(unittest.TestCase):
    def test_actual_tesla_cone_checks_team_range_wall_and_transcendence(self):
        for case in ("body", "wall", "range", "rear", "friendly", "ultimate"):
            with self.subTest(case=case):
                run = Run(names=("Winston", "Zen", "Other"))
                winston, zen, other = run.players
                run.match.bots.clear()
                x, y, z = winston.mover.position
                dz = {"range": 9, "rear": -4}.get(case, 4)
                for player, hero, team, dx, offset in (
                    (winston, WINSTON, 0, 0, 0),
                    (zen, ZENYATTA, 0 if case == "friendly" else 1, 0, dz),
                    (other, ZENYATTA, 1 if case in ("body", "ultimate") else 0, 1, 5),
                ):
                    self.assertTrue(run.match.switch_hero(player, heroes()[hero]))
                    player.team = team
                    player.mover.position = [x+dx, y, z+offset]
                    player.mover.body = None
                for _ in range(25):
                    run.step({})
                if case == "wall":
                    plane = z + 2
                    run.match.collision_world = World([
                        (x-4, y-2, plane, x+4, y-2, plane, x-4, y+4, plane),
                        (x+4, y-2, plane, x+4, y+4, plane, x-4, y+4, plane),
                    ])
                fight = run.match.combat
                if case == "ultimate":
                    zen.body_script.set_ult_charge(zen.body_script.component.entity_var(338).value())
                    run.step({zen: (0, 0, 0x20)})
                    self.assertTrue(fight.zenyatta.ultimate(zen))
                weapon = next(i for i in winston.body_script.component.instances.values()
                              if i.graph.index == 0x00FE)
                ammo = weapon.vars[53].value()
                for _ in range(30):
                    run.step({winston: (0, 0, FIRE)})
                state = weapon.node_state(weapon.graph.node(37))
                self.assertTrue(state.active)
                self.assertLess(weapon.vars[53].value(), ammo)
                self.assertFalse(fight.rockets)
                vitals, other_vitals = fight.vitals[zen.body], fight.vitals[other.body]
                expected = 60 * .479
                self.assertAlmostEqual(vitals.total, 200-expected if case == "body" else 200, places=4)
                if case == "ultimate":
                    hits = winston.client.messages(combat.HIT)
                    self.assertTrue(hits)
                    self.assertEqual({hit["+0x78"]["+0x0"]["+0x0"] for hit in hits}, {other.body})
                    self.assertGreater(other_vitals.total, 200-expected)  # Zen's aura heals the second target
                else:
                    self.assertAlmostEqual(other_vitals.total, 200-expected if case == "body" else 200,
                                           places=4)
                slot = weapon.find_var(0, 468)
                targets = {expr.Entity(p.body) for p in (zen, other)} if case in ("body", "ultimate") else set()
                self.assertIsInstance(slot.value(), tuple)
                self.assertEqual(set(slot.value()), targets)
                totals = vitals.total, other_vitals.total
                for _ in range(8):
                    run.step({})
                self.assertFalse(state.active)
                self.assertEqual(slot.value(), 0)
                self.assertEqual(vitals.total, totals[0])
                if case == "ultimate":
                    self.assertGreaterEqual(other_vitals.total, totals[1])
                else:
                    self.assertEqual(other_vitals.total, totals[1])

    def test_actual_tesla_damages_zen_and_active_projectile_guards(self):
        definitions = heroes()
        guards = (
            (definitions[0x02E0000000000029], 0x03A3, 0x10),
            (definitions[0x02E000000000023B], 0x1D02, 0x08),
            (replace(definitions[dva.HERO], body=dva.MECH), 0x02CC, SECONDARY),
        )
        for hero, graph, button in guards:
            with self.subTest(graph=hex(graph)):
                run = Run(names=("Winston", "Zen", "Guard"))
                winston, zen, guard = run.players
                run.match.bots.clear()
                x, y, z = winston.mover.position
                for player, definition, team, offset in (
                    (winston, definitions[WINSTON], 0, 0),
                    (guard, hero, 1, 3),
                    (zen, definitions[ZENYATTA], 1, 5),
                ):
                    self.assertTrue(run.match.switch_hero(player, definition))
                    player.team = team
                    player.mover.position = [x, y, z+offset]
                    player.mover.body = None
                for _ in range(100):
                    run.step({})
                fight = run.match.combat
                guard_input = (-32768, 0, button)
                for _ in range(8):
                    run.step({guard: guard_input})
                instance = next(i for i in guard.body_script.component.instances.values() if i.graph.index == graph)
                defense = next(s for s in instance.states.values()
                               if s.active and s.node.cls == "STUStatescriptStateDeflectProjectiles")
                guard_health = fight.vitals[guard.body].total
                attacker_health = fight.vitals[winston.body].total
                for _ in range(30):
                    run.step({winston: (0, 0, FIRE), guard: guard_input})
                    self.assertTrue(defense.active)
                damage = 60*.479
                self.assertAlmostEqual(fight.vitals[zen.body].total, 200-damage, places=4)
                self.assertAlmostEqual(fight.vitals[guard.body].total,
                                       guard_health-(damage/2 if graph == 0x02CC else damage), places=4)
                self.assertEqual(fight.vitals[winston.body].total, attacker_health)
                self.assertFalse(guard.client.messages(combat.HIT))
                self.assertFalse(fight.rockets)
                if graph == 0x1D02:
                    self.assertEqual(instance.find_var(0, 21312).value(), 0)

    def test_actual_tesla_chips_projected_bubble_and_does_not_backfill_gap(self):
        fixtures = bubbles.ZenyattaBubbleTests()
        run, zen, winston = fixtures.arena(projected=True)
        defender = run.players[2]
        self.assertTrue(run.match.switch_hero(defender, heroes()[ZARYA]))
        self.assertTrue(run.match.switch_hero(winston, heroes()[WINSTON]))
        defender.team = zen.team
        x, y, z = zen.mover.position
        defender.mover.position = [x+4, y, z]
        defender.mover.body = None
        winston.mover.position = [x, y, z+4]
        winston.mover.body = None
        for _ in range(25):
            run.step({})
        fixtures.projection(run, defender, zen)
        fight = run.match.combat
        key = defender.body, PROJECTED
        bubble = fight.barriers.bubbles[key]
        yaw, pitch = aim(fight._eye(winston), (x, y+1, z))
        firing = {winston: (yaw, pitch, FIRE)}
        for _ in range(30):
            run.step(firing)
        damage = 60*.479
        self.assertEqual(fight.vitals[zen.body].total, 200)
        self.assertAlmostEqual(bubble.hp, 200-damage, places=4)
        self.assertAlmostEqual(fixtures.energy(defender), damage*.2, places=4)

        before = bubble.hp
        run.frame += 70
        run.step(firing)
        self.assertIs(fight.barriers.bubbles[key], bubble)
        self.assertAlmostEqual(before-bubble.hp, 60*.015, places=4)
        for _ in range(45):
            if key not in fight.barriers.bubbles:
                break
            self.assertEqual(fight.vitals[zen.body].total, 200)
            run.step(firing)
        self.assertNotIn(key, fight.barriers.bubbles)
        before = fight.vitals[zen.body].total
        run.step(firing)
        self.assertAlmostEqual(before-fight.vitals[zen.body].total, 60*.016, places=4)

    def test_actual_sleep_interrupts_tesla_and_held_fire_restarts_on_wake(self):
        for early, omitted in product((False, True), (0, 3)):
            with self.subTest(early=early, omitted=omitted):
                run = Run(names=("Winston", "Zen", "Ana"))
                winston, zen, ana = run.players
                run.match.bots.clear()
                x, y, z = winston.mover.position
                for player, guid, team, dx, dz in (
                    (winston, WINSTON, 0, 0, 0),
                    (zen, ZENYATTA, 1, 0, 4),
                    (ana, 0x02E000000000013B, 1, 3, 4),
                ):
                    self.assertTrue(run.match.switch_hero(player, heroes()[guid]))
                    player.team = team
                    player.mover.position = [x+dx, y, z+dz]
                    player.mover.body = None
                for _ in range(30):
                    run.step({})
                fight = run.match.combat
                weapon = next(i for i in winston.body_script.component.instances.values()
                              if i.graph.index == 0x00FE)
                vitals = fight.vitals[zen.body]
                owner = fight.zenyatta.owner(zen)
                firing = {winston: (0, 0, FIRE)}
                for _ in range(30):
                    run.step(firing)
                damage_state = weapon.node_state(weapon.graph.node(37))
                self.assertTrue(damage_state.active)
                self.assertLess(vitals.total, 200)
                activation = damage_state.counter
                before = vitals.total
                run.frame += 3
                run.step(firing)
                self.assertEqual(damage_state.counter, activation)
                self.assertAlmostEqual(before-vitals.total, 60*.064, places=4)

                dart_aim = aim(fight._eye(ana), (x, y+1.15, z))
                run.step({**firing, ana: (*dart_aim, 0x08)})
                saw_dart = False
                for _ in range(55):
                    saw_dart |= any(rocket.direct is not None and rocket.direct.effect == combat.ANA_SLEEP_EFFECT
                                    for rocket in fight.rockets)
                    if winston.body in fight.sleeps:
                        break
                    run.step({**firing, ana: (*dart_aim, 0)})
                self.assertTrue(saw_dart)
                self.assertIn(winston.body, fight.sleeps)
                start, natural_end, waking = fight.sleeps[winston.body]
                self.assertEqual(natural_end-start, 313)
                self.assertFalse(waking)
                self.assertFalse(damage_state.active)
                self.assertEqual(weapon.find_var(0, 468).value(), 0)
                ammo = weapon.vars[53].value()
                last_damage = owner.last_damage

                end = natural_end
                if early:
                    while run.frame < start+36:
                        run.step(firing)
                    orb_aim = aim(fight._eye(zen), (x, y+1.15, z))
                    run.step({**firing, zen: (*orb_aim, FIRE)})
                    self.assertTrue(any(rocket.shooter is zen for rocket in fight.rockets))
                    for _ in range(20):
                        if fight.sleeps[winston.body][2]:
                            break
                        run.step(firing)
                    self.assertTrue(fight.sleeps[winston.body][2])
                    end = fight.sleeps[winston.body][1]
                    self.assertEqual(end-run.frame, 32)
                    self.assertLess(end, natural_end)

                while run.frame < end-omitted-1:
                    run.step(firing)
                    self.assertTrue(fight.is_stunned(winston))
                    self.assertFalse(damage_state.active)
                    self.assertEqual(owner.last_damage, last_damage)
                    self.assertEqual(weapon.vars[53].value(), ammo)
                self.assertEqual(owner.last_damage, last_damage)
                self.assertEqual(weapon.vars[53].value(), ammo)
                run.frame += omitted
                before = vitals.total
                run.step(firing)
                self.assertEqual(run.frame, end)
                self.assertNotIn(winston.body, fight.sleeps)
                self.assertTrue(damage_state.active)
                self.assertEqual(damage_state.counter, activation+1)
                self.assertGreater(owner.last_damage, last_damage)
                self.assertAlmostEqual(before-vitals.total, 60*.015, places=4)

    @unittest.skipUnless(os.environ.get("ORPHEUS_BARRIER_DATA"), "requires prepared Reinhardt barrier mesh")
    def test_actual_tesla_reaches_zen_across_field_barriers_that_stop_an_orb(self):
        definitions = heroes()
        reinhardt = next(hero for hero in definitions.values() if hero.name == "Reinhardt")
        for dome in (False, True):
            with self.subTest(dome=dome):
                run = Run(names=("Attacker", "Zen", "Defender"))
                attacker, zen, defender = run.players
                run.match.bots.clear()
                x, y, z = attacker.mover.position
                for player, hero, team, offset in (
                    (attacker, definitions[WINSTON], 0, 0),
                    (zen, definitions[ZENYATTA], 1, 5),
                    (defender, definitions[WINSTON] if dome else reinhardt, 1, 7 if dome else 3),
                ):
                    self.assertTrue(run.match.switch_hero(player, hero))
                    player.team = team
                    player.mover.position = [x, y, z+offset]
                    player.mover.body = None
                for _ in range(90):
                    run.step({})
                guard = {} if dome else {defender: (-32768, 0, SECONDARY)}
                if dome:
                    run.step({defender: (-32768, 0, 0x10)})
                fight = run.match.combat
                for _ in range(45):
                    run.step(guard)
                    barrier = (fight.barriers.domes.get(defender.body) if dome
                               else fight.barriers.owners.get(defender.body))
                    if barrier is not None and barrier.hp > 0 and (dome or barrier.raised):
                        break
                self.assertIsNotNone(barrier)
                target = (x, y+1, z+5)
                origin = fight._eye(attacker)
                angles = aim(origin, target)
                crossing = fight.barriers.ray(attacker, origin, combat.aim(*angles), 5)
                self.assertIsNotNone(crossing)
                self.assertIs(crossing[1], barrier)
                zen_hp = fight.vitals[zen.body]
                for _ in range(30):
                    run.step({**guard, attacker: (*angles, FIRE)})
                self.assertAlmostEqual(zen_hp.total, 200-60*.479, places=4)

                position = list(attacker.mover.position)
                self.assertTrue(run.match.switch_hero(attacker, definitions[ZENYATTA]))
                attacker.mover.position = position
                attacker.mover.body = None
                for _ in range(25):
                    run.step(guard)
                before = barrier.hp
                before_zen = zen_hp.total
                angles = aim(fight._eye(attacker), target)
                run.step({**guard, attacker: (*angles, FIRE)})
                self.assertEqual(len(fight.rockets), 1)
                for _ in range(24):
                    run.step(guard)
                self.assertFalse(fight.rockets)
                self.assertEqual(barrier.hp, before-48)
                self.assertEqual(zen_hp.total, before_zen)
