"""Actual Genji input/state and Zenyatta projectiles, with reconstructed deflection hosting."""

import unittest
from dataclasses import replace
from itertools import product
from typing import NamedTuple

import test_zenyatta_combat as weapon_tests
from test_game_combat import Run, aim

from ow174.game import combat, dva, matrix
from ow174.game.barriers import PROJECTED, ZARYA
from ow174.game.collision import World
from ow174.game.commands import FIRE, SECONDARY, Command
from ow174.game.content import SOLDIER, heroes
from ow174.game.flashbang import HERO as MCCREE

GENJI = 0x02E0000000000029
REAPER = 0x02E0000000000002
ASHE = 0x02E0000000000200
BAPTISTE = 0x02E0000000000221
ROADHOG = 0x02E0000000000040
SCRAP_PELLET = 0x040000000000029A
SCRAP_BALL = 0x0400000000000295


class HitscanProfile(NamedTuple):
    hero: int
    graph: int
    fire: int
    damage: int
    rounds: int
    shots: int = 1
    followup_frames: int = 0


HITSCAN_PROFILES = (
    HitscanProfile(MCCREE, 0x01D2, FIRE, 70, 1),
    HitscanProfile(MCCREE, 0x01D2, SECONDARY, 50, 1),
    HitscanProfile(SOLDIER, 0x0254, FIRE, 20, 1),
    HitscanProfile(0x02E0000000000003, 0x0016, FIRE, 6, 2),  # Tracer
    HitscanProfile(0x02E000000000000A, 0x001B, FIRE, 13, 1),  # Widowmaker, unscoped
    HitscanProfile(0x02E0000000000015, 0x012E, FIRE, 20, 1),  # Bastion, Recon
    HitscanProfile(0x02E000000000012E, 0x0B1B, FIRE, 8, 1),  # Sombra
    HitscanProfile(0x02E000000000000A, 0x001B, FIRE | SECONDARY, 120, 5),  # Widowmaker, fully charged
    HitscanProfile(0x02E000000000013B, 0x0B94, FIRE | SECONDARY, 70, 1),  # Ana, scoped
    HitscanProfile(ASHE, 0x14FC, FIRE, 40, 1),
    HitscanProfile(ASHE, 0x14FC, FIRE | SECONDARY, 75, 1),
    HitscanProfile(BAPTISTE, 0x1932, FIRE, 24, 3, shots=3, followup_frames=12),
)


class ZenyattaDeflectTests(unittest.TestCase):
    def hook_arena(self):
        run = Run(names=("Roadhog", "Zen", "Zarya"))
        hog, zen, zarya = run.players
        run.match.bots.clear()
        x, y, z = hog.mover.position
        for player, hero, team, dx, dz in (
            (hog, ROADHOG, 1, 0, 0), (zen, 0x02E0000000000020, 0, 0, 12), (zarya, ZARYA, 0, 5, 8),
        ):
            self.assertTrue(run.match.switch_hero(player, heroes()[hero]))
            player.team = team
            player.mover.position = [x + dx, y, z + dz]
            player.mover.body = None
        for _ in range(25):
            run.step({})
        return run, hog, zen, zarya

    def hook_input(self, run, player, target, buttons):
        x, y, z = target.mover.position
        return (*aim(run.match.combat._eye(player), (x, y + 1, z)), buttons)

    def reach_hook_impact(self, run, hog, zen, *, zen_buttons=0):
        fight = run.match.combat
        run.step({hog: self.hook_input(run, hog, zen, 0x08), zen: self.hook_input(run, zen, hog, zen_buttons)})
        for _ in range(30):
            run.step({hog: self.hook_input(run, hog, zen, 0), zen: self.hook_input(run, zen, hog, zen_buttons)})
            hook = fight.chain_hooks.get(hog.body)
            if hook is not None and hook.victim == zen.body:
                return hook
        self.fail("Chain Hook did not latch onto Zen")

    def test_actual_chain_hook_cancels_charge_and_locks_through_final_pull_update(self):
        run, hog, zen, _ = self.hook_arena()
        fight = run.match.combat
        weapon = next(i for i in zen.body_script.component.instances.values() if i.graph.index == 0x0164)
        for _ in range(105):
            run.step({zen: self.hook_input(run, zen, hog, SECONDARY)})
        self.assertGreaterEqual(weapon.vars[1392].value(), 3)
        hook = self.reach_hook_impact(run, hog, zen, zen_buttons=SECONDARY)
        self.assertEqual(weapon.vars[1392].value(), 0)
        ammo = weapon.vars[53].value()
        for _ in range(20):
            run.step({zen: self.hook_input(run, zen, hog, FIRE)})
            self.assertEqual(weapon.vars[53].value(), ammo)
            self.assertFalse(any(rocket.shooter is zen for rocket in fight.rockets))
            if hook.victim is None:
                break
        else:
            self.fail("Chain Hook did not release")
        self.assertAlmostEqual(zen.mover.position[2] - hog.mover.position[2], 3, delta=0.2)
        run.step({zen: self.hook_input(run, zen, hog, FIRE)})
        self.assertEqual(weapon.vars[53].value(), ammo - 1)
        for _ in range(15):
            run.step({})
        self.assert_hit_notices(zen, [(hog.body, 48)])

    def test_actual_chain_hook_releases_on_late_projection_or_target_body_replacement(self):
        for case in ("projection", "replace"):
            with self.subTest(case=case):
                run, hog, zen, zarya = self.hook_arena()
                fight = run.match.combat
                hook = self.reach_hook_impact(run, hog, zen)
                run.step({})
                self.assertEqual(hook.victim, zen.body)
                if case == "projection":
                    run.step({zarya: self.hook_input(run, zarya, zen, 0x10)})
                    self.assertIn((zarya.body, PROJECTED), fight.barriers.bubbles)
                else:
                    self.assertTrue(run.match.switch_hero(zen, heroes()[SOLDIER]))
                position = tuple(zen.mover.position)
                run.step({})
                self.assertIsNone(hook.victim)
                self.assertFalse(fight.is_stunned(zen))
                for _ in range(140 if case == "projection" else 25):
                    run.step({})
                if case == "projection":
                    self.assertNotIn((zarya.body, PROJECTED), fight.barriers.bubbles)
                self.assertIsNone(hook.victim)
                self.assertAlmostEqual(zen.mover.position[2], position[2], delta=0.02)
                self.assertEqual(len(hog.client.messages(combat.HIT)), 1)

    def test_actual_zen_primary_kills_hook_owner_before_or_on_impact(self):
        for fire_offset, expected_zen_hp in ((7, 200), (8, 170)):
            with self.subTest(fire_offset=fire_offset):
                run, hog, zen, _ = self.hook_arena()
                fight = run.match.combat
                fight.vitals[hog.body].damage(570)
                start = run.frame
                zen_position = tuple(zen.mover.position)
                for offset in range(30):
                    run.step({
                        hog: self.hook_input(run, hog, zen, 0x08 if offset == 0 else 0),
                        zen: self.hook_input(run, zen, hog, FIRE if offset == fire_offset else 0),
                    })
                    if offset == 10:
                        self.assertGreater(fight.chain_hooks[hog.body].remaining, 0)
                        self.assertFalse(fight.is_dead(hog))
                self.assertTrue(fight.is_dead(hog))
                death_tick = fight.vitals[hog.body].death_tick
                self.assertEqual(death_tick, start + fire_offset + 9)
                self.assert_hit_notices(zen, [(hog.body, 30)])
                self.assertEqual(zen.client.messages(combat.HIT)[0]["+0x78"]["+0x8"], combat.KILLING)
                self.assertEqual(self.health(run, zen), expected_zen_hp)
                if expected_zen_hp == 170:
                    self.assert_hit_notices(hog, [(zen.body, 30)])
                    self.assertEqual(hog.client.messages(combat.HIT)[0]["+0x84"], death_tick)
                else:
                    self.assertFalse(hog.client.messages(combat.HIT))
                hook = fight.chain_hooks[hog.body]
                self.assertEqual(hook.remaining, 0)
                self.assertIsNone(hook.victim)
                self.assertEqual(tuple(zen.mover.position), zen_position)

    def test_actual_chain_hook_cast_damage_pull_and_rejections(self):
        for case in ("open", "gap", "ultimate", "wall", "ally", "range", "switch", "lethal"):
            with self.subTest(case=case):
                run = Run(names=("Roadhog", "Zen"))
                hog, zen = run.players
                run.match.bots.clear()
                x, y, z = hog.mover.position
                distance = 24 if case == "range" else 8
                for player, hero, team, offset in (
                    (hog, ROADHOG, 1, 0),
                    (zen, 0x02E0000000000020, 1 if case == "ally" else 0, distance),
                ):
                    self.assertTrue(run.match.switch_hero(player, heroes()[hero]))
                    player.team = team
                    player.mover.position = [x, y, z + offset]
                    player.mover.body = None
                for _ in range(25):
                    run.step({})
                fight = run.match.combat
                if case == "lethal":
                    fight.vitals[zen.body].damage(180)
                if case == "ultimate":
                    zen.body_script.set_ult_charge(zen.body_script.component.entity_var(338).value())
                    run.step({zen: (0, 0, 0x20)})
                    self.assertTrue(fight.zenyatta.ultimate(zen))
                if case == "wall":
                    plane = z + 4
                    run.match.collision_world = World([
                        (x-40, y, z-40, x+40, y, z+40, x+40, y, z-40),
                        (x-40, y, z-40, x-40, y, z+40, x+40, y, z+40),
                        (x-4, y-2, plane, x+4, y-2, plane, x-4, y+4, plane),
                        (x+4, y-2, plane, x+4, y+4, plane, x-4, y+4, plane),
                    ])
                aim_at_zen = aim(fight._eye(hog), (x, y + 1, z + distance))
                run.step({hog: (*aim_at_zen, 0x08)})
                for _ in range(11):
                    run.step({hog: (*aim_at_zen, 0)})
                if case == "switch":
                    self.assertTrue(run.match.switch_hero(hog, heroes()[SOLDIER]))
                if case == "gap":
                    run.frame += 6
                for _ in range(35):
                    run.step({})
                hits = hog.client.messages(combat.HIT)
                if case in ("open", "gap"):
                    self.assertEqual(self.health(run, zen), 170)
                    self.assert_hit_notices(hog, [(zen.body, 30)])
                elif case == "lethal":
                    self.assertTrue(fight.is_dead(zen))
                    self.assert_hit_notices(hog, [(zen.body, 20)])
                    self.assertEqual(hits[0]["+0x78"]["+0x8"], combat.KILLING)
                else:
                    self.assertEqual(self.health(run, zen), 200)
                    self.assertFalse(hits)
                if case in ("open", "gap", "ultimate"):
                    self.assertAlmostEqual(zen.mover.position[2] - z, 3, delta=0.2)
                    if case == "ultimate":
                        self.assertTrue(fight.zenyatta.ultimate(zen))
                else:
                    self.assertAlmostEqual(zen.mover.position[2] - z, distance, delta=0.02)
                if case == "open":
                    run.step({hog: (*aim_at_zen, 0x08)})
                    for _ in range(45):
                        run.step({})
                    self.assert_hit_notices(hog, [(zen.body, 30)])

    def test_actual_chain_hook_uses_barriers_but_not_projectile_guards(self):
        for case in ("deflect", "grasp", "matrix", "projection"):
            with self.subTest(case=case):
                run = Run(names=("Roadhog", "Defender", "Zen"))
                hog, defender, zen = run.players
                run.match.bots.clear()
                x, y, z = hog.mover.position
                hero = {
                    "deflect": heroes()[GENJI], "grasp": heroes()[0x02E000000000023B],
                    "matrix": replace(heroes()[dva.HERO], body=dva.MECH), "projection": heroes()[ZARYA],
                }[case]
                for player, definition, team, dx, dz in (
                    (hog, heroes()[ROADHOG], 1, 0, 0),
                    (defender, hero, 0, 4 if case == "projection" else 0, 6),
                    (zen, heroes()[0x02E0000000000020], 0, 0, 10),
                ):
                    self.assertTrue(run.match.switch_hero(player, definition))
                    player.team = team
                    player.mover.position = [x + dx, y, z + dz]
                    player.mover.body = None
                for _ in range(100):
                    run.step({})
                fight = run.match.combat
                button = {"deflect": 0x10, "grasp": 0x08, "matrix": SECONDARY, "projection": 0x10}[case]
                guard_aim = aim(fight._eye(defender), (x, y + 1.5, z + 10)) if case == "projection" else (-32768, 0)
                guard = {defender: (*guard_aim, button)}
                run.step(guard)
                if case == "projection":
                    self.assertIn((defender.body, PROJECTED), fight.barriers.bubbles)
                hog_aim = aim(fight._eye(hog), (x, y + 1, z + 10))
                run.step({**guard, hog: (*hog_aim, 0x08)})
                graph = {"deflect": 0x03A3, "grasp": 0x1D02, "matrix": 0x02CC}.get(case)
                for _ in range(40):
                    run.step({**guard, hog: (*hog_aim, 0)})
                    if hog.client.messages(combat.HIT):
                        instance = next(i for i in defender.body_script.component.instances.values()
                                        if i.graph.index == graph)
                        # A hook interrupts these guards when it reaches their owner.
                        self.assertTrue(any(s.counter and s.node.cls == "STUStatescriptStateDeflectProjectiles"
                                            for s in instance.states.values()))
                        break
                for _ in range(20):
                    run.step({})
                self.assertEqual(self.health(run, zen), 200)
                self.assertAlmostEqual(zen.mover.position[2] - z, 10, delta=0.02)
                if case == "projection":
                    self.assertFalse(hog.client.messages(combat.HIT))
                else:
                    self.assert_hit_notices(hog, [(defender.body, 25 if case == "matrix" else 30)])
                    self.assertAlmostEqual(defender.mover.position[2] - z, 3, delta=0.2)
                    self.assertFalse(defender.client.messages(combat.HIT))

    def test_actual_whole_hog_damage_and_push_are_separate(self):
        for case in ("direct", "ultimate", "wall", "far", "bubble", "grasp", "matrix", "matrix_off",
                     "reflect", "reflect_ultimate", "reflect_grasp", "reflect_matrix", "reflect_matrix_off"):
            with self.subTest(case=case):
                reflected = case.startswith("reflect")
                grasp, mech = case.endswith("grasp"), case.endswith(("matrix", "matrix_off"))
                has_guard = grasp or mech
                matrix_active = mech and not case.endswith("_off")
                run = Run(names=("Zen", "Genji", "Guard", "Roadhog"))
                zen, genji, defender, hog = run.players
                run.match.bots.clear()
                x, y, z = hog.mover.position
                genji_z = 7 if has_guard else 3
                zen_z = 1.2 if reflected else 25 if case == "far" else 4
                guard_hero = (replace(heroes()[dva.HERO], body=dva.MECH) if mech
                              else heroes()[0x02E000000000023B] if grasp else heroes()[ZARYA])
                for player, hero, team, dx, dz in (
                    (hog, heroes()[ROADHOG], 0 if reflected else 1, 0, 8 if has_guard and not reflected else 0),
                    (genji, heroes()[GENJI], 1, 0 if reflected else 25, genji_z),
                    (zen, heroes()[0x02E0000000000020], 0, 0, zen_z if not has_guard or reflected else 1.2),
                    (defender, guard_hero, 0, 0 if has_guard else 4, 3.5),
                ):
                    self.assertTrue(run.match.switch_hero(player, hero))
                    player.team = team
                    player.mover.position = [x + dx, y, z + dz]
                    player.mover.body = None
                for _ in range(100 if has_guard else 40):
                    run.step({})
                fight = run.match.combat
                target_z = z + (genji_z if reflected else 3.5 if has_guard else zen_z)
                direction = aim(fight._eye(hog), (x, y + 1, target_z))
                if case == "wall":
                    plane = z + 2
                    run.match.collision_world = World([
                        (x-4, y-2, plane, x+4, y-2, plane, x-4, y+4, plane),
                        (x+4, y-2, plane, x+4, y+4, plane, x-4, y+4, plane),
                    ])
                if case.endswith("ultimate"):
                    zen.body_script.set_ult_charge(zen.body_script.component.entity_var(338).value())
                    run.step({zen: (0, 0, 0x20)})
                    self.assertTrue(fight.zenyatta.ultimate(zen))
                guard = {genji: (-32768, 0, 0x10)} if reflected else {}
                if grasp or matrix_active:
                    guard[defender] = (0, 0, 0x08 if grasp else SECONDARY)
                if case == "bubble":
                    projection = aim(fight._eye(defender), (x, y + 1.5, z + zen_z))
                    run.step({defender: (*projection, 0x10)})
                    bubble = fight.barriers.bubbles[defender.body, PROJECTED]
                hog.body_script.set_ult_charge(hog.body_script.component.entity_var(338).value())
                gun = next(i for i in hog.body_script.component.instances.values() if i.graph.index == 0x1C6)
                ammo = gun.vars[53].value()
                run.step({**guard, hog: (*direction, 0x20)})
                for _ in range(70):
                    run.step({**guard, hog: (*direction, 0)})
                    if any(s.node.pos == 33 and s.reported for s in gun.states.values()):
                        break
                else:
                    self.fail("Whole Hog did not fire its first volley")
                self.assertEqual(gun.vars[53].value(), ammo)
                source = genji if reflected else hog
                hits = source.client.messages(combat.HIT)
                if case in ("wall", "bubble") or has_guard:
                    self.assertEqual(self.health(run, zen), 200)
                    self.assertAlmostEqual(zen.mover.velocity[2], 0)
                    if case == "bubble":
                        self.assertLess(bubble.hp, 200)
                    if grasp:
                        instance = next(i for i in defender.body_script.component.instances.values()
                                        if i.graph.index == 0x1D02)
                        self.assertEqual(instance.find_var(0, 21312).value(), 28 if reflected else 112)
                        self.assertEqual(self.health(run, defender), 400)
                        self.assertFalse(hits)
                    if mech:
                        self.assertEqual(bool(tuple(matrix.active_matrix_states(defender))), matrix_active)
                        if matrix_active:
                            self.assertEqual(self.health(run, defender), 600)
                            self.assertFalse(hits)
                            self.assertAlmostEqual(defender.mover.velocity[2], 0)
                        else:
                            self.assertLess(self.health(run, defender), 600)
                            self.assertTrue(any(hit["+0x78"]["+0x0"]["+0x0"] == defender.body for hit in hits))
                            self.assertLess(defender.mover.velocity[2], -8)
                    continue
                sign = -1 if reflected else 1
                self.assertAlmostEqual(zen.mover.velocity[2] * sign, 1.4 if case == "far" else 14, places=4)
                self.assertAlmostEqual(zen.mover.velocity[1], 0.18 if case == "far" else 1.8, places=4)
                if case.endswith("ultimate"):
                    self.assertEqual(self.health(run, zen), 200)
                    self.assertFalse(any(hit["+0x78"]["+0x0"]["+0x0"] == zen.body for hit in hits))
                else:
                    self.assertEqual(self.health(run, zen), 116 if reflected else 193 if case == "far" else 172)
                    self.assertTrue(any(hit["+0x78"]["+0x0"]["+0x0"] == zen.body for hit in hits))
                if case != "far":
                    before = zen.mover.position[2]
                    run.step(guard)
                    self.assertGreater((zen.mover.position[2] - before) * sign, 0.05)

    def test_actual_reflected_scrap_ball_passes_ownership_to_its_nested_volley(self):
        for ultimate in (False, True):
            with self.subTest(ultimate=ultimate):
                run = Run(names=("Roadhog", "Genji", "Zen"))
                hog, genji, zen = run.players
                run.match.bots.clear()
                x, y, z = hog.mover.position
                for player, hero, team, dx, dz in (
                    (hog, ROADHOG, 0, 0, 0), (genji, GENJI, 1, 0, 5),
                    (zen, 0x02E0000000000020, 0, 3.6, -5),
                ):
                    self.assertTrue(run.match.switch_hero(player, heroes()[hero]))
                    player.team = team
                    player.mover.position = [x + dx, y, z + dz]
                    player.mover.body = None
                for _ in range(25):
                    run.step({})
                fight = run.match.combat
                direction = aim(fight._eye(hog), (x, y + 1, z + 5))
                return_yaw, _ = aim(fight._eye(genji), (x + 4, y + 1, z - 5))
                inputs = {genji: (return_yaw, 0, 0x10)}
                run.step(inputs)
                run.step({**inputs, hog: (*direction, SECONDARY)})
                for _ in range(9):
                    run.step(inputs)
                self.assertEqual([(r.definition, r.shooter) for r in fight.rockets], [(SCRAP_BALL, genji)])
                if ultimate:
                    zen.body_script.set_ult_charge(zen.body_script.component.entity_var(338).value())
                    run.step({**inputs, zen: (0, 0, 0x20)})
                    self.assertTrue(fight.zenyatta.ultimate(zen))
                else:
                    run.step(inputs)
                self.assertEqual([(r.definition, r.shooter) for r in fight.rockets],
                                 [(SCRAP_PELLET, genji)] * 25)
                for _ in range(16):
                    run.step(inputs)
                self.assertFalse(hog.client.messages(combat.HIT))
                self.assertEqual(self.health(run, genji), 200)
                self.assertEqual(self.health(run, hog), 600)
                hits = genji.client.messages(combat.HIT)
                if ultimate:
                    self.assertFalse(hits)
                    self.assertEqual(self.health(run, zen), 200)
                else:
                    self.assertTrue(hits)
                    self.assertTrue(all(hit["+0x78"]["+0x0"]["+0x0"] == zen.body for hit in hits))
                    self.assertLess(self.health(run, zen), 200)

    def test_actual_scrap_ball_releases_nested_volley_only_at_free_expiry(self):
        outcomes = {}
        for case in ("open", "gap", "wall", "ultimate"):
            with self.subTest(case=case):
                run = Run(names=("Roadhog", "Zen"))
                hog, zen = run.players
                run.match.bots.clear()
                x, y, z = hog.mover.position
                for player, hero, team, offset in (
                    (hog, ROADHOG, 1, 0), (zen, 0x02E0000000000020, 0, 14),
                ):
                    self.assertTrue(run.match.switch_hero(player, heroes()[hero]))
                    player.team = team
                    player.mover.position = [x, y, z + offset]
                    player.mover.body = None
                for _ in range(25):
                    run.step({})
                if case == "wall":
                    plane = z + 6
                    run.match.collision_world = World([
                        (x-4, y-2, plane, x+4, y-2, plane, x-4, y+4, plane),
                        (x+4, y-2, plane, x+4, y+4, plane, x-4, y+4, plane),
                    ])
                fight = run.match.combat
                angles = aim(fight._eye(hog), (x, y + 1, z + 14))
                gun = next(i for i in hog.body_script.component.instances.values() if i.graph.index == 0x1C6)
                ammo = gun.vars[53].value()
                run.step({hog: (*angles, SECONDARY)})
                self.assertEqual([r.definition for r in fight.rockets], [SCRAP_BALL])
                if case == "ultimate":
                    zen.body_script.set_ult_charge(zen.body_script.component.entity_var(338).value())
                    run.step({zen: (0, 0, 0x20)})
                    self.assertTrue(fight.zenyatta.ultimate(zen))
                else:
                    run.step({})
                for _ in range(4 if case == "gap" else 8):
                    run.step({})
                if case == "wall":
                    self.assertFalse(fight.rockets)
                else:
                    self.assertEqual([r.definition for r in fight.rockets], [SCRAP_BALL])
                    if case == "gap":
                        run.frame += 4
                    run.step({})
                    self.assertEqual([r.definition for r in fight.rockets], [SCRAP_PELLET] * 25)
                    self.assertEqual(len({r.direction for r in fight.rockets}), 25)
                for _ in range(14):
                    run.step({})
                self.assertEqual(gun.vars[53].value(), ammo - 1)
                if case in ("open", "gap"):
                    hits = hog.client.messages(combat.HIT)
                    self.assertTrue(hits)
                    self.assertTrue(all(hit["+0x78"]["+0x0"]["+0x0"] == zen.body for hit in hits))
                    health = self.health(run, zen)
                    self.assertLess(health, 200)
                    outcomes[case] = health, [hit["+0x78"]["+0x4"] for hit in hits]
                else:
                    self.assertFalse(hog.client.messages(combat.HIT))
                    self.assertEqual(self.health(run, zen), 200)
        self.assertEqual(outcomes.get("open"), outcomes.get("gap"))

    @unittest.skipUnless(matrix.TAGS_PATH, "requires the external Matrix projectile tag catalog")
    def test_actual_scrap_gun_primary_and_close_ball_meet_defenses_before_zen(self):
        for secondary, case in product((False, True), (
            "open", "q", "reflect", "grasp", "matrix", "reflect_grasp", "reflect_matrix",
        )):
            with self.subTest(secondary=secondary, case=case):
                run = Run(names=("Roadhog", "Genji", "Zen", "Guard"))
                hog, genji, zen, guard = run.players
                run.match.bots.clear()
                x, y, z = hog.mover.position
                reflected = case.startswith("reflect")
                grasp, matrix_active = case.endswith("grasp"), case.endswith("matrix")
                protected = grasp or matrix_active
                mech = replace(heroes()[dva.HERO], body=dva.MECH)
                for player, hero, team, dx, dz in (
                    (hog, heroes()[ROADHOG], 0 if reflected else 1, 0, 8 if protected and not reflected else 0),
                    (genji, heroes()[GENJI], 1, 0 if reflected else 25, 6 if secondary else 7),
                    (zen, heroes()[0x02E0000000000020], 0, 0, 1.5 if reflected or protected else 4),
                    (guard, mech if matrix_active else heroes()[0x02E000000000023B], 0,
                     0 if protected else 25, 2.7 if reflected and secondary else 3.5),
                ):
                    self.assertTrue(run.match.switch_hero(player, hero))
                    player.team = team
                    player.mover.position = [x + dx, y, z + dz]
                    player.mover.body = None
                for _ in range(100):
                    run.step({})
                inputs = {guard: (0, 0, 0x08 if grasp else SECONDARY)} if protected else {}
                for _ in range(8 if protected else 0):
                    run.step(inputs)
                if reflected:
                    inputs[genji] = (-32768, 0, 0x10)
                if case == "q":
                    zen.body_script.set_ult_charge(zen.body_script.component.entity_var(338).value())
                    run.step({zen: (0, 0, 0x20)})
                    self.assertTrue(run.match.combat.zenyatta.ultimate(zen))
                run.step(inputs)
                fight = run.match.combat
                target = genji if reflected else guard if protected else zen
                tx, ty, tz = target.mover.position
                angles = aim(fight._eye(hog), (tx, ty + 1, tz))
                gun = next(i for i in hog.body_script.component.instances.values() if i.graph.index == 0x1C6)
                ammo = gun.vars[53].value()
                run.step({**inputs, hog: (*angles, SECONDARY if secondary else FIRE)})
                self.assertEqual([r.definition for r in fight.rockets],
                                 [SCRAP_BALL] if secondary else [SCRAP_PELLET] * 25)
                for _ in range(16):
                    run.step(inputs)
                self.assertEqual(gun.vars[53].value(), ammo - 1)
                if secondary:
                    self.assertFalse(fight.rockets)
                self.assertEqual(self.health(run, genji), 200)
                if case in ("open", "reflect"):
                    expected = 50 if secondary else 6.6 * (19 if reflected else 23)
                    self.assertAlmostEqual(self.health(run, zen), 200 - expected, places=4)
                    source, other = (genji, hog) if reflected else (hog, genji)
                    hits = source.client.messages(combat.HIT)
                    self.assertEqual(sum(hit["+0x78"]["+0x0"]["+0x0"] == zen.body for hit in hits),
                                     1 if secondary else (19 if reflected else 23))
                    self.assertFalse(other.client.messages(combat.HIT))
                else:
                    self.assertEqual(self.health(run, zen), 200)
                    self.assertFalse(hog.client.messages(combat.HIT))
                    if grasp:
                        instance = next(i for i in guard.body_script.component.instances.values()
                                        if i.graph.index == 0x1D02)
                        expected = 50 if secondary else 6.6 * (23 if reflected else 25)
                        self.assertAlmostEqual(instance.find_var(0, 21312).value(), expected, places=4)
                        self.assertEqual(self.health(run, guard), 400)
                    elif matrix_active:
                        leaks = 5 if reflected and not secondary else 0
                        self.assertAlmostEqual(self.health(run, guard), 600 - leaks * 3.3, places=4)
                        self.assert_hit_notices(genji, [(guard.body, 3)] * leaks)

    def reaper_shot(self, run, reaper, target, inputs):
        fight = run.match.combat
        x, y, z = target.mover.position
        angles = aim(fight._eye(reaper), (x, y + 1, z))
        gun = next(i for i in reaper.body_script.component.instances.values() if i.graph.index == 0x19)
        ammo = gun.vars[53].value()
        run.step({**inputs, reaper: (*angles, FIRE)})
        self.assertEqual(gun.vars[53].value(), ammo - 1)
        self.assertFalse(fight.rockets)

    def test_actual_reaper_pellets_respect_deflect_wall_and_transcendence(self):
        for case in ("direct", "direct_q", "front", "rear", "wall", "reflected_q"):
            with self.subTest(case=case):
                run = Run(names=("Reaper", "Genji", "Zen"))
                reaper, genji, zen = run.players
                run.match.bots.clear()
                x, y, z = reaper.mover.position
                reflected = case not in ("direct", "direct_q")
                for player, hero, team, offset in (
                    (reaper, REAPER, 0 if reflected else 1, (0, 0)),
                    (genji, GENJI, 1, (0 if reflected else 25, 5)),
                    (zen, 0x02E0000000000020, 0, (0, 1.5 if reflected else 4)),
                ):
                    self.assertTrue(run.match.switch_hero(player, heroes()[hero]))
                    player.team = team
                    player.mover.position = [x + offset[0], y, z + offset[1]]
                    player.mover.body = None
                for _ in range(25):
                    run.step({})
                if case in ("direct_q", "reflected_q"):
                    zen.body_script.set_ult_charge(zen.body_script.component.entity_var(338).value())
                    run.step({zen: (0, 0, 0x20)})
                    self.assertTrue(run.match.combat.zenyatta.ultimate(zen))
                if case == "wall":
                    plane = z + 2.5
                    run.match.collision_world = World([
                        (x-4, y-2, plane, x+4, y-2, plane, x-4, y+4, plane),
                        (x+4, y-2, plane, x+4, y+4, plane, x-4, y+4, plane),
                    ])
                inputs = {genji: (0 if case == "rear" else -32768, 0, 0x10)} if reflected else {}
                run.step(inputs)
                self.reaper_shot(run, reaper, genji if reflected else zen, inputs)

                self.assertEqual(self.health(run, reaper), 250)
                self.assertEqual(self.health(run, genji), 92 if case == "rear" else 200)
                self.assertEqual(self.health(run, zen), 80 if case in ("direct", "front") else 200)
                reaper_hits = [(zen.body, 6)] * 20 if case == "direct" else []
                if case == "rear":
                    reaper_hits = [(genji.body, 6)] * 18
                self.assert_hit_notices(reaper, reaper_hits)
                self.assert_hit_notices(genji, [(zen.body, 6)] * 20 if case == "front" else [])

    def assert_hit_notices(self, source, expected):
        self.assertEqual(
            [(hit["+0x78"]["+0x0"]["+0x0"], hit["+0x78"]["+0x4"])
             for hit in source.client.messages(combat.HIT)],
            expected,
        )

    def test_actual_reaper_pellets_are_absorbed_individually_before_zen(self):
        for grasp, reflected in product((False, True), repeat=2):
            with self.subTest(grasp=grasp, reflected=reflected):
                run = Run(names=("Reaper", "Genji", "Guard", "Zen"))
                reaper, genji, guard, zen = run.players
                run.match.bots.clear()
                x, y, z = reaper.mover.position
                definition = heroes()[0x02E000000000023B] if grasp else replace(heroes()[dva.HERO], body=dva.MECH)
                for player, hero, team, dx, dz in (
                    (reaper, heroes()[REAPER], 0 if reflected else 1, 0, 0 if reflected else 8),
                    (genji, heroes()[GENJI], 1, 0 if reflected else 25, 7),
                    (guard, definition, 0, 0, 3.5),
                    (zen, heroes()[0x02E0000000000020], 0, 0, 1.5),
                ):
                    self.assertTrue(run.match.switch_hero(player, hero))
                    player.team = team
                    player.mover.position = [x + dx, y, z + dz]
                    player.mover.body = None
                for _ in range(100):
                    run.step({})
                inputs = {guard: (0, 0, 0x08 if grasp else SECONDARY)}
                for _ in range(8):
                    run.step(inputs)
                graph = 0x1D02 if grasp else 0x02CC
                instance = next(i for i in guard.body_script.component.instances.values() if i.graph.index == graph)
                self.assertTrue(any(s.active and s.node.cls == "STUStatescriptStateDeflectProjectiles"
                                    for s in instance.states.values()))
                if reflected:
                    inputs[genji] = (-32768, 0, 0x10)
                run.step(inputs)
                self.reaper_shot(run, reaper, genji if reflected else guard, inputs)

                self.assertEqual(self.health(run, genji), 200)
                self.assertEqual(self.health(run, zen), 200)
                # The three low returned rays outside this Matrix box hit armor for 3 each.
                returned_hits = [(guard.body, 3)] * 3 if reflected and not grasp else []
                self.assert_hit_notices(reaper, [])
                self.assert_hit_notices(genji, returned_hits)
                self.assertEqual(self.health(run, guard), 400 if grasp else 600 - 3 * len(returned_hits))
                if grasp:
                    self.assertEqual(instance.find_var(0, 21312).value(), 120)
                    for _ in range(135):
                        run.step({})
                    self.assertAlmostEqual(self.health(run, guard), 472, places=4)

    def test_direct_and_reflected_hitscan_meet_grasp_and_matrix_before_zen(self):
        guards = (
            (heroes()[0x02E000000000023B], 0x1D02, 0x08, 400),
            (replace(heroes()[dva.HERO], body=dva.MECH), 0x02CC, SECONDARY, 600),
        )
        for guard_profile, weapon_profile, case, reflected in product(
            guards, HITSCAN_PROFILES, ("off", "rear", "front"), (False, True),
        ):
            hero, graph, button, health = guard_profile
            weapon_hero, weapon_graph, fire, damage, spent, shots, followup_frames = weapon_profile
            with self.subTest(graph=hex(graph), weapon=hex(weapon_graph), fire=fire, case=case, reflected=reflected):
                run = Run(names=("Shooter", "Genji", "Guard", "Zen"))
                caster, genji, guard, zen = run.players
                run.match.bots.clear()
                x, y, z = caster.mover.position
                for player, definition, team, offset in (
                    (caster, heroes()[weapon_hero], 0 if reflected else 1, 0 if reflected else 8),
                    (genji, heroes()[GENJI], 1, 8 if reflected else 0),
                    (guard, hero, 0, 3.5),
                    (zen, heroes()[0x02E0000000000020], 0, 1.5),
                ):
                    self.assertTrue(run.match.switch_hero(player, definition))
                    player.team = team
                    player.mover.position = [x, y, z + offset]
                    player.mover.body = None
                scope = {caster: (0, 0, SECONDARY)} if fire == (FIRE | SECONDARY) else {}
                for _ in range(100):
                    run.step(scope)
                guard_input = (-32768 if case == "rear" else 0, 0, 0 if case == "off" else button)
                for _ in range(8):
                    run.step({**scope, guard: guard_input})
                instance = next(i for i in guard.body_script.component.instances.values()
                                if i.graph.index == graph)
                self.assertEqual(any(s.active and s.node.cls == "STUStatescriptStateDeflectProjectiles"
                                     for s in instance.states.values()), case != "off")
                genji_input = (-32768, 0, 0x10 if reflected else 0)
                run.step({**scope, genji: genji_input, guard: guard_input})
                fight = run.match.combat
                angles = aim(fight._eye(caster), (x, y+1, z+(8 if reflected else 3.5)))
                instances = caster.body_script.component.instances.values()
                gun = next(i for i in instances if i.graph.index == weapon_graph)
                ammo = gun.vars[53].value()
                run.step({caster: (*angles, fire), genji: genji_input, guard: guard_input})
                for _ in range(followup_frames):
                    run.step({caster: (*angles, fire & SECONDARY), genji: genji_input, guard: guard_input})
                self.assertEqual(gun.vars[53].value(), ammo-spent)
                self.assertFalse(fight.rockets)
                source, other = (genji, caster) if reflected else (caster, genji)
                self.assertFalse(other.client.messages(combat.HIT))
                self.assertFalse(guard.client.messages(combat.HIT))
                self.assertEqual(self.health(run, genji), 200)
                self.assertEqual(self.health(run, zen), 200)
                hits = source.client.messages(combat.HIT)
                if case != "front":
                    effective = max(damage-5, damage/2) if graph == 0x02CC else damage
                    self.assertEqual(self.health(run, guard), health-effective*shots)
                    self.assertEqual(len(hits), shots)
                    for hit in hits:
                        self.assertEqual(hit["+0x78"]["+0x0"]["+0x0"], guard.body)
                        self.assertEqual(hit["+0x78"]["+0x4"], effective)
                    continue
                self.assertEqual(self.health(run, guard), health)
                self.assertFalse(hits)
                if graph == 0x1D02:
                    self.assertEqual(instance.find_var(0, 21312).value(), damage*shots)
                    for _ in range(135):
                        run.step({})
                    self.assertAlmostEqual(self.health(run, guard), 400+damage*shots*.6, places=4)

    def test_delayed_hitscan_uses_historical_guard_anchor_and_live_facing(self):
        guards = ((GENJI, 0x03A3, 0x10, 200), (0x02E000000000023B, 0x1D02, 0x08, 400))
        for profile, case in product(guards, ("delayed front", "delayed rear", "current front")):
            hero, graph_index, button, max_hp = profile
            with self.subTest(hero=hex(hero), case=case):
                run = Run(names=("Soldier", "Guard", "Zen"))
                shooter, guard, zen = run.players
                match = run.match
                fight = match.combat
                match.bots.clear()
                match.game_map = replace(match.game_map, free_for_all=False)
                x, y, z = shooter.mover.position
                for actor, definition, team, offset in (
                    (shooter, SOLDIER, 1, 12),
                    (guard, hero, 0, 3.5),
                    (zen, 0x02E0000000000020, 0, 0),
                ):
                    self.assertTrue(match.switch_hero(actor, heroes()[definition]))
                    actor.team = team
                    actor.mover.position = [x, y, z + offset]
                    actor.mover.body = None
                for _ in range(25):
                    run.step({guard: (-32768, 0, 0)})
                old_tick = run.frame
                instance = next(i for i in guard.body_script.component.instances.values()
                                if i.graph.index == graph_index)
                self.assertFalse(any(s.active and s.node.cls == "STUStatescriptStateDeflectProjectiles"
                                     for s in instance.states.values()))

                guard.mover.position[0] += 6
                guard.mover.body = None
                yaw = -32768 if case == "delayed rear" else 0
                for _ in range(10):
                    run.step({guard: (yaw, 0, button)})
                self.assertTrue(any(s.active and s.node.cls == "STUStatescriptStateDeflectProjectiles"
                                    for s in instance.states.values()))
                run.frame += 1
                match.update(run.frame * combat.TICK, run.frame, frames=False)
                target = next(t for t in fight._targets() if t.owner is guard)
                self.assertAlmostEqual(fight.seen(target, old_tick).feet[0], x)
                self.assertAlmostEqual(target.feet[0], x + 6)

                angles = aim(fight._eye(shooter), (x, y + 1, z + 3.5))
                gun = next(i for i in shooter.body_script.component.instances.values() if i.graph.index == 0x0254)
                ammo = gun.vars[53].value()
                delay = (run.frame - old_tick) * 16 if case.startswith("delayed") else 0
                shooter.take_commands([Command(run.frame, yaw=angles[0], pitch=angles[1],
                                               buttons=FIRE, extra=delay)])
                self.assertEqual(gun.vars[53].value(), ammo - 1)
                self.assertFalse(fight.rockets)
                hits = shooter.client.messages(combat.HIT)
                returns = guard.client.messages(combat.HIT)
                if case == "delayed front":
                    self.assertFalse(hits)
                    self.assertEqual(self.health(run, guard), max_hp)
                    self.assertEqual(self.health(run, zen), 200)
                    if hero == GENJI:
                        returned, = returns
                        self.assertEqual(returned["+0x78"]["+0x0"]["+0x0"], shooter.body)
                        self.assertEqual(returned["+0x78"]["+0x4"], 20)
                        self.assertEqual(self.health(run, shooter), 180)
                    else:
                        self.assertFalse(returns)
                        self.assertEqual(instance.find_var(0, 21312).value(), 20)
                        self.assertEqual(self.health(run, shooter), 200)
                        for _ in range(135):
                            run.step({})
                        self.assertAlmostEqual(self.health(run, guard), 412)
                else:
                    hit, = hits
                    victim = guard if case == "delayed rear" else zen
                    self.assertEqual(hit["+0x78"]["+0x0"]["+0x0"], victim.body)
                    self.assertEqual(hit["+0x78"]["+0x4"], 20)
                    self.assertEqual(self.health(run, guard), max_hp - (20 if victim is guard else 0))
                    self.assertEqual(self.health(run, zen), 180 if victim is zen else 200)
                    self.assertFalse(returns)
                    if hero != GENJI:
                        self.assertEqual(instance.find_var(0, 21312).value(), 0)

    def test_deflect_returns_hitscan_to_zen_and_respects_q(self):
        for (weapon_hero, weapon_graph, fire, damage, spent, shots, followup_frames), case in product(
            HITSCAN_PROFILES, ("inactive", "rear", "front", "wall", "ultimate"),
        ):
            with self.subTest(weapon=hex(weapon_graph), fire=fire, case=case):
                run = Run(names=("Shooter", "Genji", "Zen"))
                caster, genji, zen = run.players
                run.match.bots.clear()
                x, y, z = caster.mover.position
                for player, hero, team, offset in (
                    (caster, weapon_hero, 0, 0), (genji, GENJI, 1, 5),
                    (zen, 0x02E0000000000020, 0, 2),
                ):
                    self.assertTrue(run.match.switch_hero(player, heroes()[hero]))
                    player.team = team
                    player.mover.position = [x, y, z + offset]
                    player.mover.body = None
                scope = {caster: (0, 0, SECONDARY)} if fire == (FIRE | SECONDARY) else {}
                for _ in range(100 if scope else 25):
                    run.step(scope)
                fight = run.match.combat
                if case == "ultimate":
                    zen.body_script.set_ult_charge(zen.body_script.component.entity_var(338).value())
                    run.step({**scope, zen: (0, 0, 0x20)})
                    self.assertTrue(fight.zenyatta.ultimate(zen))
                guard = (0 if case == "rear" else -32768, 0, 0 if case == "inactive" else 0x10)
                run.step({**scope, genji: guard})
                instance = next(i for i in genji.body_script.component.instances.values()
                                if i.graph.index == 0x03A3)
                self.assertEqual(any(s.active and s.node.cls == "STUStatescriptStateDeflectProjectiles"
                                     for s in instance.states.values()), case != "inactive")
                if case == "wall":
                    plane = z + 2.5
                    run.match.collision_world = World([
                        (x-4, y-2, plane, x+4, y-2, plane, x-4, y+4, plane),
                        (x+4, y-2, plane, x+4, y+4, plane, x-4, y+4, plane),
                    ])

                instances = caster.body_script.component.instances.values()
                gun = next(i for i in instances if i.graph.index == weapon_graph)
                ammo = gun.vars[53].value()
                angles = aim(fight._eye(caster), (x, y+1, z+5))
                run.step({caster: (*angles, fire), genji: guard})
                for _ in range(followup_frames):
                    run.step({caster: (*angles, fire & SECONDARY), genji: guard})
                self.assertEqual(gun.vars[53].value(), ammo-spent)
                self.assertFalse(fight.rockets)
                caster_hits = caster.client.messages(combat.HIT)
                genji_hits = genji.client.messages(combat.HIT)
                if case in ("inactive", "rear"):
                    self.assertEqual(self.health(run, genji), 200-damage*shots)
                    self.assertEqual(self.health(run, zen), 200)
                    hits, victim = caster_hits, genji
                    self.assertFalse(genji_hits)
                else:
                    self.assertEqual(self.health(run, genji), 200)
                    self.assertEqual(self.health(run, zen), 200-damage*shots if case == "front" else 200)
                    self.assertFalse(caster_hits)
                    if case != "front":
                        self.assertFalse(genji_hits)
                        continue
                    hits, victim = genji_hits, zen
                self.assertEqual(len(hits), shots)
                for hit in hits:
                    self.assertEqual(hit["+0x78"]["+0x0"]["+0x0"], victim.body)
                    self.assertEqual(hit["+0x78"]["+0x4"], damage)

    def test_actual_baptiste_burst_samples_transcendence_per_shot(self):
        for unprotected_updates, landed in ((0, 1), (6, 2)):
            with self.subTest(landed=landed):
                run = Run(names=("Baptiste", "Zen"))
                shooter, zen = run.players
                run.match.bots.clear()
                x, y, z = shooter.mover.position
                for player, hero, team, offset in (
                    (shooter, BAPTISTE, 1, 0), (zen, 0x02E0000000000020, 0, 5),
                ):
                    self.assertTrue(run.match.switch_hero(player, heroes()[hero]))
                    player.team = team
                    player.mover.position = [x, y, z + offset]
                    player.mover.body = None
                for _ in range(25):
                    run.step({})
                fight = run.match.combat
                angles = aim(fight._eye(shooter), (x, y + 1, z + 5))
                gun = next(i for i in shooter.body_script.component.instances.values() if i.graph.index == 0x1932)
                ammo = gun.vars[53].value()
                run.step({shooter: (*angles, FIRE)})
                followup = {shooter: (*angles, 0)}
                for _ in range(unprotected_updates):
                    run.step(followup)
                self.assertEqual(self.health(run, zen), 200 - 24 * landed)
                self.assertEqual(gun.vars[53].value(), ammo - landed)

                zen.body_script.set_ult_charge(zen.body_script.component.entity_var(338).value())
                run.step({**followup, zen: (0, 0, 0x20)})
                self.assertTrue(fight.zenyatta.ultimate(zen))
                for _ in range(12):
                    run.step(followup)
                self.assertEqual(self.health(run, zen), 200)
                self.assertEqual(gun.vars[53].value(), ammo - 3)
                hits = shooter.client.messages(combat.HIT)
                self.assertEqual(len(hits), landed)
                self.assertTrue(all(hit["+0x78"]["+0x4"] == 24 for hit in hits))

    @unittest.skipUnless(matrix.TAGS_PATH, "requires the external Matrix projectile tag catalog")
    def test_actual_unscoped_ana_projectile_meets_guards_and_transcendence(self):
        definitions = heroes()
        for reflected, case in product((False, True), ("open", "ultimate", "grasp", "matrix")):
            with self.subTest(reflected=reflected, case=case):
                run = Run(names=("Ana", "Genji", "Guard", "Zen"))
                ana, genji, guard, zen = run.players
                run.match.bots.clear()
                x, y, z = ana.mover.position
                defense = case in ("grasp", "matrix")
                guard_hero = (replace(definitions[dva.HERO], body=dva.MECH) if case == "matrix"
                              else definitions[0x02E000000000023B])
                for player, hero, team, dx, dz in (
                    (ana, definitions[0x02E000000000013B], 0 if reflected else 1, 0, 0 if reflected else 8),
                    (genji, definitions[GENJI], 1, 0 if reflected else 5, 8),
                    (guard, guard_hero, 0, 0 if defense else 5, 3.5),
                    (zen, definitions[weapon_tests.ZENYATTA], 0, 0, 1.5),
                ):
                    self.assertTrue(run.match.switch_hero(player, hero))
                    player.team = team
                    player.mover.position = [x+dx, y, z+dz]
                    player.mover.body = None
                for _ in range(100):
                    run.step({})
                fight = run.match.combat
                if case == "ultimate":
                    zen.body_script.set_ult_charge(zen.body_script.component.entity_var(338).value())
                    run.step({zen: (0, 0, 0x20)})
                    self.assertTrue(fight.zenyatta.ultimate(zen))
                held = {genji: (-32768, 0, 0x10)} if reflected else {}
                if defense:
                    held[guard] = (0, 0, SECONDARY if case == "matrix" else 0x08)
                for _ in range(8):
                    run.step(held)
                if defense:
                    graph = 0x02CC if case == "matrix" else 0x1D02
                    instance = next(i for i in guard.body_script.component.instances.values()
                                    if i.graph.index == graph)
                    self.assertTrue(any(s.active and s.node.cls == "STUStatescriptStateDeflectProjectiles"
                                        for s in instance.states.values()))

                gun = next(i for i in ana.body_script.component.instances.values() if i.graph.index == 0x0B94)
                ammo = gun.vars[53].value()
                angles = aim(fight._eye(ana), (x, y+1, z+(8 if reflected else 1.5)))
                run.step({**held, ana: (*angles, FIRE)})
                self.assertEqual(gun.vars[53].value(), ammo-1)
                rocket, = fight.rockets
                self.assertEqual((rocket.definition, rocket.speed, rocket.direct.amount),
                                 (0x04000000000015B7, 125, 70))
                self.assertIsNotNone(rocket.shot)
                self.assertEqual(self.health(run, zen), 200)
                for _ in range(24):
                    run.step(held)
                self.assertFalse(fight.rockets)
                self.assertEqual(self.health(run, genji), 200)
                self.assertEqual(self.health(run, guard), 600 if case == "matrix" else 400)
                self.assertEqual(self.health(run, zen), 130 if case == "open" else 200)
                owner, other = (genji, ana) if reflected else (ana, genji)
                self.assertFalse(other.client.messages(combat.HIT))
                hits = owner.client.messages(combat.HIT)
                if case == "open":
                    hit, = hits
                    self.assertEqual(hit["+0x78"]["+0x0"]["+0x0"], zen.body)
                    self.assertEqual(hit["+0x78"]["+0x4"], 70)
                else:
                    self.assertFalse(hits)
                if case == "grasp":
                    self.assertEqual(instance.find_var(0, 21312).value(), 70)
                    for _ in range(135):
                        run.step({})
                    self.assertAlmostEqual(self.health(run, guard), 442, places=4)

    def test_reflected_flashbang_retains_gravity_and_respects_transcendence(self):
        for ultimate in (False, True):
            with self.subTest(ultimate=ultimate):
                self.check_reflected_flashbang(ultimate)

    def check_reflected_flashbang(self, ultimate):
        run = Run(names=("McCree", "Genji", "Zen"))
        caster, genji, zen = run.players
        run.match.bots.clear()
        x, y, z = caster.mover.position
        for player, hero, team, offset in (
            (caster, MCCREE, 0, (0, 0)),
            (genji, 0x02E0000000000029, 1, (0, 3)),
            (zen, weapon_tests.ZENYATTA, 0, (1, 0)),
        ):
            run.match.switch_hero(player, heroes()[hero])
            player.team = team
            player.mover.position = [x+offset[0], y, z+offset[1]]
            player.mover.body = None
        guard = {genji: (-32768, 0, 0x10)}
        for _ in range(20):
            run.step(guard)
        self.assertTrue(self.active(genji))
        fight = run.match.combat
        if ultimate:
            zen.body_script.set_ult_charge(zen.body_script.component.entity_var(338).value())
            run.step({**guard, zen: (0, 0, 0x20)})
            self.assertTrue(fight.zenyatta.ultimate(zen))
        run.step({**guard, caster: (0, 0, 16)})
        rocket, = fight.rockets
        self.assertGreater(rocket.velocity[2], 0)
        for _ in range(10):
            if rocket.shooter is genji:
                break
            run.step(guard)
        self.assertIs(rocket.shooter, genji)
        self.assertLess(rocket.velocity[2], 0)
        self.assertEqual(rocket.gravity, 20)
        vertical = rocket.velocity[1]
        run.step(guard)
        self.assertLess(rocket.velocity[1], vertical)
        for _ in range(20):
            run.step(guard)
        self.assertEqual(self.health(run, genji), 200)
        self.assertEqual(self.health(run, zen), 200 if ultimate else 175)
        self.assertEqual(fight.is_stunned(zen), not ultimate)
        self.assertEqual(fight.zenyatta.ultimate(zen), ultimate)
        self.assertFalse(caster.client.messages(combat.HIT))
        targets = [hit["+0x78"]["+0x0"]["+0x0"] for hit in genji.client.messages(combat.HIT)]
        self.assertIn(caster.body, targets)
        self.assertEqual(zen.body in targets, not ultimate)

    def arena(self, yaw=-32768):
        run, zen, genji, _ = weapon_tests.ZenyattaCombatTests().arena()
        run.match.switch_hero(genji, heroes()[0x02E0000000000029])
        x, y, z = zen.mover.position
        genji.mover.position = [x, y, z+8]
        genji.mover.body = None
        for _ in range(10):
            run.step({genji: (yaw, 0, 0x10)})
        self.assertTrue(self.active(genji))
        return run, zen, genji

    def active(self, genji):
        return any(s.active and s.node.cls == "STUStatescriptStateDeflectProjectiles"
                   for i in genji.body_script.component.instances.values() for s in i.states.values())

    def health(self, run, player):
        target = next(t for t in run.match.combat._targets() if t.owner is player)
        return run.match.combat.vitals_of(target).total

    def test_front_deflect_returns_orb_and_assigns_real_damage_and_hit_notice_to_genji(self):
        run, zen, genji = self.arena()
        run.step({zen: (0, 0, FIRE), genji: (-32768, 0, 0x10)})
        for _ in range(30):
            run.step({genji: (-32768, 0, 0x10)})
        self.assertEqual(self.health(run, genji), 200)
        self.assertEqual(self.health(run, zen), 104)
        self.assertFalse(zen.client.messages(combat.HIT))
        hit = genji.client.messages(combat.HIT)[0]["+0x78"]
        self.assertEqual(hit["+0x0"]["+0x0"], zen.body)
        self.assertEqual(hit["+0x4"], 96)
        self.assertEqual(hit["+0x8"], combat.CRITICAL)

    def double_deflect_arena(self, *, projected=False):
        names = ("Zen", "Enemy Genji", "Ally Genji", "Enemy Soldier")
        run = Run(names=(*names, "Zarya") if projected else names)
        zen, first, second, target = run.players[:4]
        run.match.bots.clear()
        x, y, z = zen.mover.position
        for actor, hero, team, offset in (
            (zen, weapon_tests.ZENYATTA, 0, (0, 0)),
            (first, GENJI, 1, (0, 8)),
            (second, GENJI, 0, (4, 4)),
            (target, target.hero.guid, 1, (1.2, 10)),
        ):
            self.assertTrue(run.match.switch_hero(actor, heroes()[hero]))
            actor.team = team
            actor.mover.position = [x+offset[0], y, z+offset[1]]
            actor.mover.body = None
        fight = run.match.combat
        # The second reflection starts at contact, offset from the aiming hero.
        guard = {
            first: (*aim(fight._eye(first), fight._eye(second)), 16),
            second: (*aim(fight._eye(second), (x+2, y+1, z+10)), 16),
        }
        for _ in range(20):
            run.step(guard)
        self.assertTrue(self.active(first))
        self.assertTrue(self.active(second))
        return run, zen, first, second, target, guard

    def double_deflect_orb(self, run, zen, first, second, guard):
        fight = run.match.combat
        run.step({**guard, zen: (0, 0, FIRE)})
        orb, = fight.rockets
        owners = [orb.shooter]
        for _ in range(40):
            run.step(guard)
            if orb.shooter is not owners[-1]:
                owners.append(orb.shooter)
            if orb not in fight.rockets:
                break
        self.assertEqual(owners, [zen, first, second])
        self.assertNotIn(orb, fight.rockets)
        self.assertFalse(zen.client.messages(combat.HIT))
        self.assertFalse(first.client.messages(combat.HIT))

    def test_last_deflector_owns_real_hit(self):
        run, zen, first, second, target, guard = self.double_deflect_arena()
        self.double_deflect_orb(run, zen, first, second, guard)
        notice, = second.client.messages(combat.HIT)
        self.assertEqual(notice["+0x78"]["+0x0"]["+0x0"], target.body)
        self.assertEqual(notice["+0x78"]["+0x4"], 48)
        self.assertEqual(self.health(run, target), 152)
        for actor in (zen, first, second):
            self.assertEqual(self.health(run, actor), 200)

    def test_projected_barrier_breaks_on_double_deflected_orb_without_actor_hit(self):
        run, zen, first, second, target, guard = self.double_deflect_arena(projected=True)
        zarya = run.players[4]
        self.assertTrue(run.match.switch_hero(zarya, heroes()[ZARYA]))
        zarya.team = target.team
        x, y, z = target.mover.position
        zarya.mover.position = [x+4, y, z]
        zarya.mover.body = None
        fight = run.match.combat
        angles = aim(fight._eye(zarya), fight._eye(target))
        run.step({**guard, zarya: (*angles, 16)})
        key = zarya.body, PROJECTED
        bubble = fight.barriers.bubbles[key]
        self.assertEqual(bubble.target, target.body)
        bubble.hp = 20

        self.double_deflect_orb(run, zen, first, second, guard)
        self.assertNotIn(key, fight.barriers.bubbles)
        self.assertFalse(second.client.messages(combat.HIT))
        self.assertEqual(self.health(run, target), 200)
        energy_graph = next(instance for instance in zarya.body_script.component.instances.values()
                            if instance.graph.index == 0x0242)
        self.assertAlmostEqual(energy_graph.find_var(1, 87, create=False).value(), 4, places=5)

        for _ in range(32):
            run.step(guard)
        self.double_deflect_orb(run, zen, first, second, guard)
        hit, = second.client.messages(combat.HIT)
        self.assertEqual(hit["+0x78"]["+0x0"]["+0x0"], target.body)
        self.assertEqual(hit["+0x78"]["+0x4"], 48)
        self.assertEqual(self.health(run, target), 152)

    def test_body_replacement_distinguishes_original_and_reflected_damage_owner(self):
        for replace_deflector in (False, True):
            with self.subTest(replace_deflector=replace_deflector):
                run, zen, genji = self.arena()
                fight = run.match.combat
                run.step({zen: (0, 0, FIRE), genji: (-32768, 0, 0x10)})
                orb, = fight.rockets
                for _ in range(20):
                    run.step({genji: (-32768, 0, 0x10)})
                    if orb.shooter is genji:
                        break
                self.assertIs(orb.shooter, genji)
                self.assertIn(orb, fight.rockets)
                self.assertEqual(self.health(run, zen), 200)

                replaced = genji if replace_deflector else zen
                old_body = replaced.body
                position = list(replaced.mover.position)
                self.assertTrue(run.match.switch_hero(replaced, replaced.hero))
                self.assertNotEqual(replaced.body, old_body)
                replaced.mover.position = position
                replaced.mover.body = None
                self.assertEqual(orb in fight.rockets, not replace_deflector)

                for _ in range(35):
                    run.step({})
                self.assertFalse(fight.rockets)
                self.assertEqual(self.health(run, genji), 200)
                self.assertFalse(zen.client.messages(combat.HIT))
                hits = [notice["+0x78"] for notice in genji.client.messages(combat.HIT)]
                if replace_deflector:
                    self.assertEqual(self.health(run, zen), 200)
                    self.assertFalse(hits)
                else:
                    self.assertEqual(self.health(run, zen), 104)
                    hit, = hits
                    self.assertEqual((hit["+0x0"]["+0x0"], hit["+0x4"], hit["+0x8"]),
                                     (zen.body, 96, combat.CRITICAL))

    def test_deflect_does_not_protect_the_back(self):
        run, zen, genji = self.arena(yaw=0)
        run.step({zen: (0, 600, FIRE), genji: (0, 0, 0x10)})
        for _ in range(20):
            run.step({genji: (0, 0, 0x10)})
        self.assertEqual(self.health(run, genji), 152)
        self.assertEqual(self.health(run, zen), 200)

    def test_native_deflect_end_restores_normal_orb_hits(self):
        run, zen, genji = self.arena()
        for _ in range(150):
            run.step({genji: (-32768, 0, 0)})
        self.assertFalse(self.active(genji))
        run.step({zen: (0, 600, FIRE)})
        for _ in range(20):
            run.step({})
        self.assertEqual(self.health(run, genji), 152)
        self.assertEqual(self.health(run, zen), 200)

    def test_wall_intercepts_before_deflect(self):
        run, zen, genji = self.arena()
        x, y, z = zen.mover.position
        run.match.collision_world = World([
            (x-5, y-1, z+4, x+5, y-1, z+4, x+5, y+5, z+4),
            (x-5, y-1, z+4, x+5, y+5, z+4, x-5, y+5, z+4),
        ])
        run.step({zen: (0, 0, FIRE), genji: (-32768, 0, 0x10)})
        for _ in range(20):
            run.step({genji: (-32768, 0, 0x10)})
        self.assertEqual(self.health(run, zen), 200)
        self.assertEqual(self.health(run, genji), 200)
        self.assertFalse(run.match.combat.rockets)

    def test_reflected_orb_cannot_damage_transcendent_zen(self):
        run, zen, genji = self.arena()
        run.step({zen: (0, 0, FIRE), genji: (-32768, 0, 0x10)})
        zen.body_script.set_ult_charge(zen.body_script.component.entity_var(338).value())
        run.step({zen: (0, 0, 0x20), genji: (-32768, 0, 0x10)})
        for _ in range(30):
            run.step({genji: (-32768, 0, 0x10)})
        self.assertEqual(self.health(run, zen), 200)
        self.assertEqual(self.health(run, genji), 200)
        self.assertFalse(genji.client.messages(combat.HIT))
        self.assertFalse(run.match.combat.rockets)

    def test_reflected_orb_samples_transcendence_at_impact_across_expiry(self):
        for expires_before_impact in (False, True):
            with self.subTest(expires_before_impact=expires_before_impact):
                run = Run(names=("Zen", "Genji", "Ally Zen"))
                zen, genji, ally = run.players
                match = run.match
                match.bots.clear()
                match.game_map = replace(match.game_map, free_for_all=False)
                x, y, z = zen.mover.position
                for player, hero, team, offset in (
                    (zen, weapon_tests.ZENYATTA, 0, 0),
                    (genji, GENJI, 1, 12),
                    (ally, weapon_tests.ZENYATTA, 0, -2),
                ):
                    self.assertTrue(match.switch_hero(player, heroes()[hero]))
                    player.team = team
                    player.mover.position = [x, y, z+offset]
                    player.mover.body = None
                for _ in range(20):
                    run.step({})
                fight = match.combat
                zen.body_script.set_ult_charge(zen.body_script.component.entity_var(338).value())
                run.step({zen: (0, 0, 0x20)})
                start = run.frame
                shot_delay = 359 if expires_before_impact else 345
                while run.frame < start+shot_delay-8:
                    run.step({})
                guard = {genji: (-32768, 0, 0x10)}
                for _ in range(8):
                    run.step(guard)
                self.assertTrue(self.active(genji))
                run.step({**guard, ally: (0, 0, FIRE)})
                orb, = fight.rockets
                for _ in range(20):
                    run.step(guard)
                    if orb.shooter is genji:
                        break
                self.assertIs(orb.shooter, genji)
                self.assertIn(orb, fight.rockets)
                self.assertTrue(fight.zenyatta.ultimate(zen))
                self.assertEqual(self.health(run, zen), 200)

                for _ in range(20):
                    run.step(guard)
                    if orb not in fight.rockets:
                        break
                self.assertNotIn(orb, fight.rockets)
                self.assertEqual(fight.zenyatta.ultimate(zen), not expires_before_impact)
                self.assertEqual(self.health(run, zen), 104 if expires_before_impact else 200)
                self.assertEqual(self.health(run, genji), 200)
                self.assertFalse(ally.client.messages(combat.HIT))
                hits = [notice["+0x78"] for notice in genji.client.messages(combat.HIT)]
                self.assertEqual([(hit["+0x0"]["+0x0"], hit["+0x4"]) for hit in hits],
                                 [(zen.body, 96)] if expires_before_impact else [])

    def test_skipped_world_frames_preserve_deflection_residual_travel(self):
        health = []
        for advances in (range(1, 21), (20,)):
            run, zen, genji = self.arena()
            run.step({zen: (0, 0, FIRE), genji: (-32768, 0, 0x10)})
            run.step({genji: (-32768, 0, 0x10)})  # admit the player's projectile into the world clock
            for advance in advances:
                tick = run.frame + advance
                run.match.update(tick*combat.TICK, tick)
            health.append(self.health(run, zen))
            self.assertEqual(self.health(run, genji), 200)
            self.assertFalse(run.match.combat.rockets)
        self.assertEqual(health, [104, 104])

    def test_deflect_expiry_during_catchup_preserves_projectile_ownership(self):
        def start():
            run, zen, genji = self.arena()
            x, y, z = zen.mover.position
            genji.mover.position = [x, y, z+16]
            genji.mover.body = None
            fight = run.match.combat
            run.step({genji: (-32768, 0, 0)})
            angles = aim(fight._eye(zen), (x, y+1, z+16))
            guard = next(i for i in genji.body_script.component.instances.values() if i.graph.index == 0x03A3)
            wait = guard.states[guard.graph.node(7).state]
            self.assertTrue(wait.active)
            self.assertEqual(wait.ms, 2000)
            return run, zen, genji, angles, (wait.start+wait.ms)//guard.component.frame_ms

        run, zen, genji, angles, _ = start()
        fight = run.match.combat
        run.step({zen: (*angles, FIRE), genji: (-32768, 0, 0)})
        launch = run.frame
        orb, = fight.rockets
        for _ in range(35):
            run.step({genji: (-32768, 0, 0)})
            if orb.shooter is genji:
                break
        self.assertIs(orb.shooter, genji)
        travel = run.frame-launch
        self.assertGreater(travel, 4)
        for _ in range(30):
            run.step({genji: (-32768, 0, 0)})
        reference, = genji.client.messages(combat.HIT)
        reflected_amount = reference["+0x78"]["+0x4"]
        self.assertIn(reflected_amount, (48, 96))

        for after_expiry, skipped in product((False, True), repeat=2):
            with self.subTest(after_expiry=after_expiry, skipped=skipped):
                run, zen, genji, angles, end = start()
                fight = run.match.combat
                release = end+int(after_expiry)-travel
                while run.frame < end-3:
                    run.step({genji: (-32768, 0, 0),
                              zen: (*angles, FIRE if run.frame+1 == release else 0)})
                self.assertTrue(self.active(genji))
                orb, = fight.rockets
                self.assertIs(orb.shooter, zen)
                if skipped:
                    run.frame += 4
                    run.step({genji: (-32768, 0, 0)})
                else:
                    for _ in range(5):
                        run.step({genji: (-32768, 0, 0)})
                self.assertEqual(run.frame, end+2)
                self.assertFalse(self.active(genji))
                for _ in range(30):
                    run.step({genji: (-32768, 0, 0)})
                self.assertFalse(fight.rockets)
                source, victim = (zen, genji) if after_expiry else (genji, zen)
                self.assertIs(orb.shooter, source)
                self.assertEqual(self.health(run, source), 200)
                self.assertEqual(self.health(run, victim), 152 if after_expiry else 200-reflected_amount)
                self.assertFalse(victim.client.messages(combat.HIT))
                notice, = source.client.messages(combat.HIT)
                self.assertEqual(notice["+0x78"]["+0x0"]["+0x0"], victim.body)
                self.assertEqual(notice["+0x78"]["+0x4"], 48 if after_expiry else reflected_amount)

    def test_discord_targeting_is_not_itself_reflected(self):
        run, zen, genji = self.arena()
        run.step({zen: (0, 0, 0x08), genji: (-32768, 0, 0x10)})
        owner = run.match.combat.zenyatta.owners[zen.body]
        self.assertEqual(owner.discord.target, genji.body)
        self.assertEqual(self.health(run, zen), 200)
        self.assertFalse(run.match.combat.rockets)
        self.assertFalse(genji.client.messages(combat.HIT))

    def test_charged_reflected_burst_kills_zen_with_genji_credit_and_cleans_up(self):
        run, zen, genji, _ = weapon_tests.ZenyattaCombatTests().arena()
        run.match.switch_hero(genji, heroes()[0x02E0000000000029])
        x, y, z = zen.mover.position
        genji.mover.position = [x, y, z+8]
        genji.mover.body = None
        run.step({zen: (0, 0, 0x08), genji: (-32768, 0, 0)})
        for i in range(140):
            run.step({zen: (0, 0, 0x02), genji: (-32768, 0, 0x10 if i >= 130 else 0)})
        self.assertTrue(self.active(genji))
        run.step({zen: (0, 0, 0), genji: (-32768, 0, 0x10)})
        for _ in range(150):
            run.step({genji: (-32768, 0, 0x10)})
        self.assertEqual(self.health(run, zen), 0)
        self.assertEqual(self.health(run, genji), 200)
        self.assertEqual([hit["+0x78"]["+0x4"] for hit in genji.client.messages(combat.HIT)], [96, 96, 8])
        self.assertEqual(len(genji.client.messages(combat.KILL_NOTICE)), 1)
        self.assertNotIn(zen.body, run.match.combat.zenyatta.owners)
        self.assertFalse(run.match.combat.rockets)

    def test_released_volley_preserves_reflected_ownership_across_deflect_expiry(self):
        run, zen, genji, _ = weapon_tests.ZenyattaCombatTests().arena()
        self.assertTrue(run.match.switch_hero(genji, heroes()[GENJI]))
        x, y, z = zen.mover.position
        genji.mover.position = [x, y, z + 24]
        genji.mover.body = None
        fight = run.match.combat
        target = next(target for target in fight._targets() if target.owner is genji)
        head_aim = aim(fight._eye(zen), fight._eye(genji))
        body_aim = aim(fight._eye(zen), (x, y + target.height * .5, z + 24))
        charge = zen.body_script.component.entity_var(342)
        weapon = next(i for i in zen.body_script.component.instances.values() if i.graph.index == 0x164)

        run.step({zen: (*head_aim, 8), genji: (-32768, 0, 0)})
        owner = fight.zenyatta.owner(zen)
        orb = owner.discord
        self.assertIsNotNone(orb)
        self.assertEqual(orb.target, genji.body)
        for frame in range(130):
            # Start Deflect so its authored timer expires between outgoing impacts two and three.
            run.step({zen: (*body_aim, SECONDARY), genji: (-32768, 0, 16 if frame == 31 else 0)})
        instance = next(i for i in genji.body_script.component.instances.values() if i.graph.index == 0x03A3)
        guard = instance.states[instance.graph.node(18).state]
        self.assertTrue(guard.active)
        before_charge, before_frame = charge.value(), run.frame
        shots = {}
        expired = False
        for _ in range(100):
            run.step({zen: (*body_aim, 0), genji: (-32768, 0, 0)})
            for rocket in fight.rockets:
                shots[id(rocket)] = rocket
            if not expired and not guard.active:
                self.assertAlmostEqual(guard.ended_at - guard.start, 2000, delta=16)
                reflected = [rocket for rocket in fight.rockets if rocket.shooter is genji]
                self.assertEqual(len(reflected), 2)
                self.assertTrue(any(rocket.shooter is zen for rocket in fight.rockets))
                self.assertFalse(genji.client.messages(combat.HIT))
                self.assertFalse(zen.client.messages(combat.HIT))
                expired = True
            if len(shots) == 5 and not fight.rockets:
                break

        self.assertTrue(expired)
        self.assertEqual([rocket.shooter for rocket in shots.values()], [genji] * 2 + [zen] * 3)
        self.assertFalse(fight.rockets)
        self.assertIs(owner.discord, orb)
        for shooter, victim, amount, count in ((genji, zen, 48, 2), (zen, genji, 60, 3)):
            hits = [notice["+0x78"] for notice in shooter.client.messages(combat.HIT)]
            self.assertEqual([(hit["+0x0"]["+0x0"], hit["+0x4"], hit["+0x8"]) for hit in hits],
                             [(victim.body, amount, 0)] * count)
            self.assertEqual(self.health(run, victim), 200 - amount * count)
        self.assertEqual(weapon.vars[53].value(), 15)
        self.assertAlmostEqual(charge.value() - before_charge,
                               180 + (run.frame - before_frame) * combat.TICK * 5, places=3)

    def test_actual_front_deflect_blocks_quick_melee_without_return_damage_or_charge(self):
        run, zen, genji = self.arena()
        x, y, z = zen.mover.position
        genji.mover.position = [x, y, z+1.5]
        genji.mover.body = None
        before = zen.body_script.component.entity_var(342).value()
        frame = run.frame
        with self.assertNoLogs('ow174.game', level='ERROR'):
            run.step({zen: (0, 0, 0x800), genji: (-32768, 0, 0x10)})
            for _ in range(30):
                run.step({genji: (-32768, 0, 0x10)})
        self.assertTrue(self.active(genji))
        self.assertEqual(self.health(run, genji), 200)
        self.assertEqual(self.health(run, zen), 200)
        self.assertFalse(zen.client.messages(combat.HIT))
        self.assertFalse(genji.client.messages(combat.HIT))
        self.assertAlmostEqual(zen.body_script.component.entity_var(342).value()-before,
                               (run.frame-frame)*combat.TICK*5, places=4)

    def test_actual_back_or_expired_deflect_allows_quick_melee_damage(self):
        for expired in (False, True):
            with self.subTest(expired=expired):
                yaw = -32768 if expired else 0
                run, zen, genji = self.arena(yaw=yaw)
                if expired:
                    for _ in range(150):
                        run.step({genji: (yaw, 0, 0)})
                    self.assertFalse(self.active(genji))
                x, y, z = zen.mover.position
                genji.mover.position = [x, y, z+1.5]
                genji.mover.body = None
                run.step({zen: (0, 0, 0x800), genji: (yaw, 0, 0)})
                for _ in range(30):
                    run.step({genji: (yaw, 0, 0)})
                self.assertEqual(self.health(run, genji), 170)
                self.assertEqual(self.health(run, zen), 200)
                self.assertEqual(len(zen.client.messages(combat.HIT)), 1)

    def test_actual_melee_filter_uses_positive_front_dot_instead_of_projectile_hand_boxes(self):
        for degrees, health in ((80, 200), (90, 170), (100, 170)):
            with self.subTest(degrees=degrees):
                yaw = -32768+round(degrees*65536/360)
                run, zen, genji = self.arena(yaw=yaw)
                x, y, z = zen.mover.position
                genji.mover.position = [x, y, z+1.5]
                genji.mover.body = None
                run.step({zen: (0, 0, 0x800), genji: (yaw, 0, 0x10)})
                for _ in range(30):
                    run.step({genji: (yaw, 0, 0x10)})
                self.assertEqual(self.health(run, genji), health)

    def test_actual_melee_filter_does_not_change_team_range_or_static_wall_rules(self):
        for case in ('ally', 'ffa', 'range', 'wall'):
            with self.subTest(case=case):
                run, zen, genji = self.arena(yaw=0)  # exposed back, so the filter cannot mask a hit
                x, y, z = zen.mover.position
                genji.mover.position = [x, y, z+(5 if case == 'range' else 1.5)]
                genji.mover.body = None
                if case in ('ally', 'ffa'):
                    genji.team = zen.team
                if case == 'ally':
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                if case == 'wall':
                    run.match.collision_world = World([
                        (x-5, y-1, z+.75, x+5, y-1, z+.75, x+5, y+5, z+.75),
                        (x-5, y-1, z+.75, x+5, y+5, z+.75, x-5, y+5, z+.75),
                    ])
                run.step({zen: (0, 0, 0x800), genji: (0, 0, 0x10)})
                for _ in range(30):
                    run.step({genji: (0, 0, 0x10)})
                self.assertEqual(self.health(run, genji), 170 if case == 'ffa' else 200)
                self.assertEqual(len(zen.client.messages(combat.HIT)), 1 if case == 'ffa' else 0)

    def test_body_replacement_cannot_keep_old_melee_guard(self):
        run, zen, genji = self.arena()
        old = genji.body
        run.match.switch_hero(genji, genji.hero)
        self.assertNotEqual(genji.body, old)
        self.assertFalse(self.active(genji))
        x, y, z = zen.mover.position
        genji.mover.position = [x, y, z+1.5]
        genji.mover.body = None
        run.step({zen: (0, 0, 0x800), genji: (-32768, 0, 0)})
        for _ in range(30):
            run.step({genji: (-32768, 0, 0)})
        self.assertEqual(self.health(run, genji), 170)


if __name__ == "__main__":
    unittest.main()
