"""Ordinary Ball and Sigma commands through the authored Grasp accumulator."""

import gzip
import json
import math
import os
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import test_wrecking_ball_combat as scenes
from test_game_combat import KANEZAKA_DM, Run, aim
from test_wrecking_ball_control import ANA
from test_wrecking_ball_fireball import add_side_wall, relocate
from test_wrecking_ball_grapple import scene as swing_scene
from test_wrecking_ball_piledriver import cast, lift, pile_instance

from ow174.game import ball, combat, deflection, mover, pools, stats
from ow174.game.collision import World
from ow174.game.commands import Command
from ow174.game.content import SOLDIER, heroes
from ow174.game.script import graph
from ow174.game.script.driver import ULT_CHARGE, ULT_COST


def projectile_defense_scene(case, hero, caster_hero=ANA, *, teams=(0, 0, 1)):
    run = Run(KANEZAKA_DM, ('Ball', 'Protector', 'Caster'))
    player, protector, caster = run.players
    match = run.match
    match.bots.clear()
    match._pending_bot_scripts.clear()
    match.game_map = replace(match.game_map, free_for_all=False)
    player.team, protector.team, caster.team = teams
    for actor, choice in ((player, ball.HERO), (protector, hero), (caster, caster_hero)):
        selected = heroes()[choice]
        if choice == graph.DVA:
            selected = replace(selected, body=graph.DVA_MECH)
        case.assertTrue(match.switch_hero(actor, selected))
    x, y, z = player.mover.position
    offset = {graph.DVA: (0, -1.5), deflection.SIGMA: (0.75, 2), deflection.GENJI: (0, 2)}[hero]
    relocate(run, protector, (x + offset[0], y, z + offset[1]))
    relocate(run, caster, (x, y, z + 6))
    for _ in range(100):
        run.step({})
    for actor in run.players:
        actor.body_script.ult_seconds = 0
        actor.body_script.set_ult_charge(0)
    return run, player, protector, caster


def launch_sleep_dart(case, run, target, ana, held):
    fight = run.match.combat
    x, y, z = target.mover.position
    angles = aim(fight._eye(ana), (x, y + 1, z))
    for frame in range(65):
        run.step({**held, ana: (*angles, 8 if frame == 0 else 0)})
        for rocket in fight.rockets:
            if rocket.shooter is ana and rocket.direct and rocket.direct.effect == combat.SLEEP_EFFECT:
                return rocket
    case.fail('actual Ana dart did not launch')


def launch_grenade(case, run, target, caster, held, *, effect=combat.FLASHBANG_EFFECT):
    fight = run.match.combat
    x, y, z = target.mover.position
    angles = aim(fight._eye(caster), (x, y + 1, z))
    for frame in range(50):
        run.step({**held, caster: (*angles, 16 if frame == 0 else 0)})
        for rocket in fight.rockets:
            if rocket.shooter is caster and rocket.blast and rocket.blast.effect == effect:
                return rocket
    case.fail(f'actual grenade with effect {effect:016X} did not launch')


def check_grenade_absorption(case, hero, *, biotic=False):
    caster_hero = ANA if biotic else 0x02E0000000000042
    effect = combat.ANA_GRENADE_EFFECT if biotic else combat.FLASHBANG_EFFECT
    run, player, protector, caster = projectile_defense_scene(case, hero, caster_hero)
    fight = run.match.combat
    own = fight.vitals_of(next(t for t in fight._targets() if t.owner is player))
    guard = fight.vitals_of(next(t for t in fight._targets() if t.owner is protector))
    before = guard.total
    mech = hero == graph.DVA
    graph_id, state_index, button = (0x02CC, 3, 2) if mech else (0x1D02, 1, 8)
    defense = next(i for i in protector.body_script.component.instances.values() if i.graph.index == graph_id)
    run.step({protector: (0, 0, button)})
    held = {protector: (0, 0, button)} if mech else {}
    grenade = launch_grenade(case, run, player, caster, held, effect=effect)
    case.assertTrue(defense.state(state_index).active)
    for _ in range(40):
        run.step(held)
        if grenade not in fight.rockets:
            break
    case.assertNotIn(grenade, fight.rockets)
    case.assertEqual(own.total, 600)
    case.assertEqual(guard.total, before)
    case.assertFalse(fight.stuns)
    case.assertFalse(own.healing_block)
    case.assertFalse(guard.healing_block)
    if mech:
        case.assertLess(defense.vars[87].value(), 100)
    else:
        absorbed = 50 if biotic else 25
        case.assertEqual(defense.vars[21312].value(), absorbed)
        for _ in range(160):
            run.step({})
            if not defense.state(state_index).active:
                break
        case.assertAlmostEqual(guard.temporary_shields, absorbed * 0.6, places=5)
        case.assertEqual(own.total, 600)


def check_biotic_rifle_defense(case, hero):
    for scoped in (False, True):
        for friendly in (False, True):
            with case.subTest(scoped=scoped, friendly=friendly):
                run, player, protector, ana = projectile_defense_scene(
                    case, hero, teams=(0, 0, 0 if friendly else 1),
                )
                fight = run.match.combat
                vitals = {target.owner: fight.vitals_of(target) for target in fight._targets()}
                own, guard = vitals[player], vitals[protector]
                if friendly:
                    own.damage(150)
                    fight._sync_body_health(player, own)
                before, guard_before = own.total, guard.total
                x, y, z = player.mover.position
                angles = aim(fight._eye(ana), (x, y + 1, z))
                if scoped:
                    for _ in range(35):
                        run.step({ana: (*angles, 2)})
                mech = hero == graph.DVA
                graph_id, state_id, button = (0x02CC, 3, 2) if mech else (0x1D02, 1, 8)
                defense = next(i for i in protector.body_script.component.instances.values()
                               if i.graph.index == graph_id)
                run.step({protector: (0, 0, button), ana: (*angles, 2 if scoped else 0)})
                case.assertTrue(defense.state(state_id).active)
                held = {protector: (0, 0, button)} if mech else {}
                run.step({**held, ana: (*angles, 3 if scoped else 1)})
                for _ in range(50):
                    run.step(held)
                case.assertAlmostEqual(own.total, before + (70 if friendly else 0))
                case.assertEqual(guard.total, guard_before)
                case.assertAlmostEqual(ana.stats.value(stats.HEALING), 70 if friendly else 0)
                case.assertEqual(ana.stats.value(stats.HERO_DAMAGE), 0)
                case.assertFalse(fight.rifle_doses)
                case.assertFalse(ana.client.messages(combat.HIT))
                rifle = next(i for i in ana.body_script.component.instances.values()
                             if i.graph.index == 0x0B94)
                case.assertEqual(rifle.vars[53].value(), 11)
                if mech:
                    case.assertLess(defense.vars[87].value(), 100)
                else:
                    case.assertEqual(defense.vars[21312].value() or 0, 0 if friendly else 70)
                    for _ in range(160):
                        run.step({})
                        if not defense.state(state_id).active:
                            break
                    case.assertAlmostEqual(guard.temporary_shields, 0 if friendly else 42)


class BallAbsorptionTests(unittest.TestCase):
    def test_allied_grasp_consumes_hostile_rifle_and_passes_friendly_healing(self):
        check_biotic_rifle_defense(self, deflection.SIGMA)

    def test_enemy_deflect_returns_allied_ana_rifle_to_ball_in_both_modes(self):
        for scoped in (False, True):
            for adaptive in (0, 35, 175):
                with self.subTest(scoped=scoped, shield=adaptive):
                    run, player, genji, ana = projectile_defense_scene(
                        self, deflection.GENJI, teams=(0, 1, 0),
                    )
                    fight = run.match.combat
                    x, y, z = player.mover.position
                    relocate(run, genji, (x, y, z + 6))
                    relocate(run, ana, (x, y, z - 3))
                    vitals = {target.owner: fight.vitals_of(target) for target in fight._targets()}
                    own = vitals[player]
                    angles = aim(fight._eye(ana), (x, y + 1, z + 6))
                    if scoped:
                        for _ in range(35):
                            run.step({ana: (*angles, 2)})
                    if adaptive:
                        run.step({player: (0, 0, 16), ana: (*angles, 2 if scoped else 0)})
                        self.assertGreaterEqual(own.temporary_shields, adaptive)
                        own.damage(own.temporary_shields - adaptive)
                        fight._sync_body_health(player, own)
                    before, shield = own.total, own.temporary_shields
                    run.step({genji: (-32768, 0, 16), ana: (*angles, 2 if scoped else 0)})
                    run.step({genji: (-32768, 0, 0), ana: (*angles, 3 if scoped else 1)})
                    for _ in range(32):
                        if fight.rifle_doses:
                            break
                        run.step({genji: (-32768, 0, 0)})
                    dose, = fight.rifle_doses
                    self.assertEqual((dose.source, dose.recipient), (genji.body, player.body))
                    self.assertTrue(dose.reflected)
                    self.assertFalse(dose.healing)
                    self.assertEqual(own.total, before)
                    for _ in range(42):
                        run.step({})
                    self.assertAlmostEqual(own.total, before - 70)
                    self.assertAlmostEqual(own.temporary_shields, max(0, shield - 70))
                    self.assertEqual(vitals[genji].total, 200)
                    self.assertAlmostEqual(genji.stats.value(stats.HERO_DAMAGE), 70)
                    self.assertEqual(ana.stats.value(stats.HERO_DAMAGE), 0)
                    charge = genji.body_script.component.entity_var(ULT_CHARGE).value()
                    self.assertAlmostEqual(charge, max(0, 70 - shield), places=3)
    def test_allied_grasp_consumes_flashbang_and_grants_shields(self):
        check_grenade_absorption(self, deflection.SIGMA)

    def test_allied_grasp_consumes_biotic_grenade_without_healing_denial(self):
        check_grenade_absorption(self, deflection.SIGMA, biotic=True)

    def test_enemy_reflected_biotic_grenade_denies_healing_on_ball_and_credits_genji(self):
        for shield in (False, True):
            with self.subTest(shield=shield):
                run, player, genji, ana = projectile_defense_scene(self, deflection.GENJI, teams=(0, 1, 0))
                fight = run.match.combat
                x, y, z = ana.mover.position
                vitals = {target.owner: fight.vitals_of(target) for target in fight._targets()}
                own, guard, caster = vitals[player], vitals[genji], vitals[ana]
                if shield:
                    run.step({player: (0, 0, 16)})
                    self.assertGreaterEqual(own.temporary_shields, 50)
                shield_before = own.temporary_shields
                run.step({genji: (0, 0, 16)})
                grenade = launch_grenade(self, run, genji, ana, {}, effect=combat.ANA_GRENADE_EFFECT)
                travelled_before_return = grenade.travelled
                for _ in range(30):
                    run.step({})
                    if grenade.shooter is genji or grenade not in fight.rockets:
                        break
                self.assertIs(grenade.shooter, genji)
                self.assertEqual(grenade.team, genji.team)
                self.assertEqual(grenade.reflections, 1)
                self.assertIn(grenade, fight.rockets)
                travelled_at_return = grenade.travelled
                self.assertGreater(travelled_at_return, travelled_before_return)
                relocate(run, ana, (x - 8, y, z))
                relocate(run, player, (x, y, z))
                for _ in range(35):
                    run.step({})
                    if grenade not in fight.rockets:
                        break
                self.assertNotIn(grenade, fight.rockets)
                self.assertGreater(grenade.travelled, travelled_at_return)
                self.assertEqual(own.total, 600 + shield_before - 50 if shield else 555)
                self.assertEqual(own.temporary_shields, shield_before - 50 if shield else 0)
                self.assertEqual(own.healing_block, ((run.frame, run.frame + 250),))
                self.assertEqual(guard.total, 200)
                self.assertEqual(caster.total, 200)
                self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), 50 if shield else 45)
                charge = genji.body_script.component.entity_var(ULT_CHARGE).value()
                self.assertEqual(charge, 0 if shield else 45)
                self.assertEqual(ana.stats.value(stats.HERO_DAMAGE), 0)
                self.assertFalse(fight.stuns)

    def test_allied_reflected_biotic_grenade_heals_ball_and_denies_healing_on_ana(self):
        run, player, genji, ana = projectile_defense_scene(self, deflection.GENJI)
        fight = run.match.combat
        x, y, z = ana.mover.position
        relocate(run, player, (x + 2, y, z))
        vitals = {target.owner: fight.vitals_of(target) for target in fight._targets()}
        own, guard, caster = vitals[player], vitals[genji], vitals[ana]
        own.damage(100)
        fight._sync_body_health(player, own)
        missing = own.maximum - own.total
        run.step({genji: (0, 0, 16)})
        grenade = launch_grenade(self, run, genji, ana, {}, effect=combat.ANA_GRENADE_EFFECT)
        for _ in range(45):
            run.step({})
            if grenade not in fight.rockets:
                break
        self.assertIs(grenade.shooter, genji)
        self.assertNotIn(grenade, fight.rockets)
        self.assertEqual(own.total, 600)
        self.assertEqual(own.healing_boost, ((run.frame, run.frame + 250),))
        self.assertFalse(own.healing_block)
        self.assertEqual(guard.total, 200)
        self.assertEqual(caster.total, 150)
        self.assertEqual(caster.healing_block, own.healing_boost)
        self.assertEqual(genji.stats.value(stats.HEALING), missing)
        self.assertEqual(ana.stats.value(stats.HEALING), 0)

    def test_allied_deflect_returns_flashbang_damage_and_stun(self):
        run, player, genji, caster = projectile_defense_scene(self, deflection.GENJI, 0x02E0000000000042)
        fight = run.match.combat
        x, y, z = player.mover.position
        relocate(run, caster, (x, y, z + 5))
        own = fight.vitals_of(next(t for t in fight._targets() if t.owner is player))
        guard = fight.vitals_of(next(t for t in fight._targets() if t.owner is genji))
        enemy = fight.vitals_of(next(t for t in fight._targets() if t.owner is caster))
        guard_before, enemy_before = guard.total, enemy.total
        run.step({genji: (0, 0, 16)})
        grenade = launch_grenade(self, run, player, caster, {})
        for _ in range(40):
            run.step({})
            if grenade not in fight.rockets:
                break
        self.assertIs(grenade.shooter, genji)
        self.assertNotIn(grenade, fight.rockets)
        self.assertEqual(own.total, 600)
        self.assertEqual(guard.total, guard_before)
        self.assertEqual(enemy.total, enemy_before - 25)
        self.assertEqual(fight.stuns[caster.body] - run.frame, 50)
        self.assertNotIn(player.body, fight.stuns)
        self.assertNotIn(genji.body, fight.stuns)
        self.assertEqual(genji.body_script.component.entity_var(ULT_CHARGE).value(), 25)

    def test_allied_grasp_converts_ana_sleep_dart_into_shields(self):
        run, player, sigma, ana = projectile_defense_scene(self, deflection.SIGMA)
        fight = run.match.combat
        own = fight.vitals_of(next(t for t in fight._targets() if t.owner is player))
        guard = fight.vitals_of(next(t for t in fight._targets() if t.owner is sigma))
        before = guard.total
        grasp = next(i for i in sigma.body_script.component.instances.values() if i.graph.index == 0x1D02)
        run.step({sigma: (0, 0, 8)})
        self.assertTrue(grasp.state(1).active)
        dart = launch_sleep_dart(self, run, player, ana, {})
        for _ in range(30):
            run.step({})
            if dart not in fight.rockets:
                break
        self.assertNotIn(dart, fight.rockets)
        self.assertEqual(own.total, 600)
        self.assertEqual(guard.total, before)
        self.assertNotIn(player.body, fight.sleeps)
        self.assertNotIn(sigma.body, fight.sleeps)
        self.assertEqual(grasp.vars[21312].value(), 5)
        for _ in range(160):
            run.step({})
            if not grasp.state(1).active:
                break
        self.assertAlmostEqual(guard.temporary_shields, 3)

    def test_allied_deflect_returns_sleep_dart_to_ana(self):
        run, player, genji, ana = projectile_defense_scene(self, deflection.GENJI)
        fight = run.match.combat
        own = fight.vitals_of(next(t for t in fight._targets() if t.owner is player))
        guard = fight.vitals_of(next(t for t in fight._targets() if t.owner is genji))
        caster = fight.vitals_of(next(t for t in fight._targets() if t.owner is ana))
        before = guard.total
        run.step({genji: (0, 0, 16)})
        dart = launch_sleep_dart(self, run, player, ana, {})
        for _ in range(35):
            run.step({})
            if ana.body in fight.sleeps:
                break
        self.assertIs(dart.shooter, genji)
        self.assertNotIn(dart, fight.rockets)
        self.assertEqual(own.total, 600)
        self.assertEqual(guard.total, before)
        self.assertEqual(caster.total, 195)
        self.assertNotIn(player.body, fight.sleeps)
        self.assertNotIn(genji.body, fight.sleeps)
        start, deadline = fight.sleeps[ana.body]
        self.assertEqual(deadline - start, 313)
        self.assertEqual(genji.body_script.component.entity_var(ULT_CHARGE).value(), 5)

    def test_enemy_deflected_sleep_dart_interrupts_ball_grapple_and_minefield_input(self):
        for shield in (False, True):
            with self.subTest(shield=shield):
                self.check_enemy_sleep_return(shield)

    def check_enemy_sleep_return(self, shield):
        run, player, genji, ana = projectile_defense_scene(self, deflection.GENJI)
        match = run.match
        fight = match.combat
        genji.team, ana.team = 1, 0
        x, y, z = player.mover.position
        relocate(run, ana, (x, y, z + 2))
        relocate(run, genji, (x, y, z + 9))
        add_side_wall(run, player, x + 12, y, z)
        grapple = ball.instance(player, ball.GRAPPLE).state(25)
        for _ in range(325):
            if grapple.remaining() == 0:
                break
            run.step({})
        own = fight.vitals_of(next(t for t in fight._targets() if t.owner is player))
        guard = fight.vitals_of(next(t for t in fight._targets() if t.owner is genji))
        caster = fight.vitals_of(next(t for t in fight._targets() if t.owner is ana))
        before = own.total
        guard_before, caster_before = guard.total, caster.total
        if shield:
            run.step({player: (16384, 0, 16)})
            before += 100
        self.assertEqual(own.total, before)
        hook = {player: (16384, 0, 2), genji: (-32768, 0, 0)}
        for _ in range(8):
            run.step(hook)
        tether = fight.ball_grapples[player.body]
        self.assertTrue(tether.attached)
        self.assertAlmostEqual(tether.anchor[0], x + 12, places=4)
        run.step({**hook, genji: (-32768, 0, 16)})
        dart = launch_sleep_dart(self, run, genji, ana, hook)
        for _ in range(15):
            run.step(hook)
            if dart.shooter is genji:
                break
        self.assertIs(dart.shooter, genji)
        self.assertIn(dart, fight.rockets)
        self.assertEqual(dart.reflections, 1)
        self.assertEqual(dart.team, genji.team)
        self.assertIs(fight.ball_grapples[player.body], tether)
        # Clear the original caster from the returned trajectory.
        relocate(run, ana, (x - 8, y, z + 2))
        for _ in range(20):
            run.step(hook)
            if player.body in fight.sleeps:
                break
        start, deadline = fight.sleeps[player.body]
        self.assertEqual(deadline - start, 313)
        self.assertNotIn(dart, fight.rockets)
        self.assertNotIn(player.body, fight.ball_grapples)
        self.assertGreater(grapple.remaining(), 4.8)
        self.assertNotIn(ana.body, fight.sleeps)
        self.assertEqual(caster.total, caster_before)
        self.assertEqual(guard.total, guard_before)
        loss = 5 if shield else 2.5
        self.assertEqual(own.total, before - loss)
        self.assertEqual(own.temporary_shields, 95 if shield else 0)
        self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), loss)
        self.assertEqual(genji.body_script.component.entity_var(ULT_CHARGE).value(), 0 if shield else loss)
        self.assertEqual(ana.body_script.component.entity_var(ULT_CHARGE).value(), 0)

        script = player.body_script
        cost = script.component.entity_var(ULT_COST).value()
        script.set_ult_charge(cost)
        run.step({player: (16384, 0, 32)})
        self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), cost)
        self.assertFalse(fight.mines.entities)
        run.frame = deadline
        match.update(deadline * combat.TICK, deadline, frames=False)
        self.assertFalse(fight.is_stunned(player))
        run.step({player: (16384, 0, 32)})
        self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)

    def test_allied_grasp_absorbs_ball_cannon_only_after_enemy_deflect(self):
        run = Run(KANEZAKA_DM, ('Ball', 'Genji', 'Sigma'))
        player, genji, sigma = run.players
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        player.team, genji.team, sigma.team = 0, 1, 0
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        for owner, hero in ((player, ball.HERO), (genji, deflection.GENJI), (sigma, deflection.SIGMA)):
            run.match.switch_hero(owner, heroes()[hero])
            owner.body_script.ult_seconds = 0
            owner.body_script.set_ult_charge(0)
        x, y, z = player.mover.position
        relocate(run, genji, (x, y, z + 6))
        relocate(run, sigma, (x, y, z + 2))
        for _ in range(60):
            run.step({})
        run.step({sigma: (0, 0, 8)})
        for _ in range(40):
            run.step({})
        run.step({genji: (-32768, 0, 16)})
        for _ in range(5):
            run.step({genji: (-32768, 0, 0)})

        fight = run.match.combat
        grasp = next(i for i in sigma.body_script.component.instances.values() if i.graph.index == 0x1D02)
        reflect = next(i for i in genji.body_script.component.instances.values() if i.graph.index == 0x03A3)
        weapon = ball.instance(player, 0x1514)
        vitals = {target.owner: fight.vitals_of(target) for target in fight._targets()}
        before = {owner: vitals[owner].total for owner in (player, genji, sigma)}
        self.assertTrue(grasp.state(1).active)
        self.assertTrue(reflect.state(10).active)
        self.assertEqual(grasp.vars[21312].value(), 0)
        angles = aim(fight._eye(player), (x, y + 0.55, z + 6))
        run.step({player: (*angles, 1), genji: (-32768, 0, 0)})
        self.assertEqual(weapon.vars[53].value(), 79)
        self.assertEqual(grasp.vars[21312].value(), 5)
        for owner, total in before.items():
            self.assertEqual(vitals[owner].total, total)
            self.assertFalse(owner.client.messages(combat.HIT))
            self.assertEqual(owner.body_script.component.entity_var(ULT_CHARGE).value(), 0)

        for _ in range(150):
            if not grasp.state(1).active:
                break
            run.step({genji: (-32768, 0, 0)})
        self.assertFalse(grasp.state(1).active)
        self.assertTrue(reflect.state(10).active)
        self.assertEqual(vitals[sigma].temporary_shields, 3)
        run.step({player: (*angles, 1), genji: (-32768, 0, 0)})
        self.assertEqual(weapon.vars[53].value(), 78)
        self.assertEqual(vitals[sigma].temporary_shields, 0)
        self.assertEqual(vitals[sigma].total, before[sigma] - 2)
        self.assertEqual(vitals[player].total, before[player])
        self.assertEqual(vitals[genji].total, before[genji])
        self.assertEqual(genji.body_script.component.entity_var(ULT_CHARGE).value(), 2)
        self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), 0)
        self.assertEqual(len(genji.client.messages(combat.HIT)), 1)
        self.assertFalse(player.client.messages(combat.HIT))

    def test_grasp_world_expiry_grants_before_cannon_without_sigma_commands(self):
        for jump in (False, True):
            with self.subTest(jump=jump):
                self.check_commandless_grasp_expiry(jump)

    def check_commandless_grasp_expiry(self, jump):
        player, sigma, grasp, vitals, initial, deadline, shoot = self.commandless_grasp_grant(jump)
        shoot(deadline, 1)
        self.assertEqual(grasp.vars[21312].value(), 5)
        self.assertEqual(vitals.temporary_shields, 0)
        self.assertAlmostEqual(vitals.total, initial - 2)
        self.assertAlmostEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), 2)
        sigma.take_commands([Command(deadline, yaw=-32768)])
        self.assertEqual(vitals.temporary_shields, 0)
        self.assertAlmostEqual(vitals.total, initial - 2)

    def commandless_grasp_grant(self, jump):
        run, player, sigma, _, _ = scenes.BallCombatTests().scenario(distance=4)
        match = run.match
        fight = match.combat
        self.assertTrue(match.switch_hero(sigma, heroes()[deflection.SIGMA]))
        x, y, z = player.mover.position
        relocate(run, sigma, (x, y, z + 4))
        for _ in range(40):
            run.step({})
        player.body_script.ult_seconds = 0
        player.body_script.set_ult_charge(0)
        run.step({sigma: (-32768, 0, 8)})
        source = sigma.body_script
        frozen = source.frame, source.component.now
        grasp = next(i for i in source.component.instances.values() if i.graph.index == 0x1D02)
        self.assertTrue(grasp.state(1).active)
        self.assertEqual(grasp.vars[567].value(), 2)
        deadline = (grasp.state(20).start + 2000) // source.frame_ms
        target = next(t for t in fight._targets() if t.owner is sigma)
        vitals = fight.vitals_of(target)
        initial = vitals.total
        angles = aim(fight._eye(player), (x, y + 0.65, z + 4))

        def shoot(frame, buttons):
            match.update(frame * combat.TICK, frame, frames=False)
            player.take_commands([Command(frame, yaw=angles[0], pitch=angles[1], buttons=buttons)])

        shoot(run.frame + 1, 1)
        shoot(run.frame + 2, 0)
        self.assertEqual(grasp.vars[21312].value(), 5)
        self.assertEqual(vitals.total, initial)
        if not jump:
            for tick in range(run.frame + 3, deadline - 1):
                match.update(tick * combat.TICK, tick, frames=False)
        match.update((deadline - 1) * combat.TICK, deadline - 1, frames=False)
        self.assertEqual(vitals.temporary_shields, 0)
        match.update(deadline * combat.TICK, deadline, frames=False)
        self.assertEqual((source.frame, source.component.now), frozen)
        self.assertTrue(grasp.state(1).active)
        self.assertAlmostEqual(vitals.temporary_shields, 3)
        return player, sigma, grasp, vitals, initial, deadline, shoot

    def test_grasp_shield_decay_uses_world_time_and_does_not_repeat_on_replay(self):
        for jump in (False, True):
            with self.subTest(jump=jump):
                self.check_commandless_grasp_decay(jump)

    def check_commandless_grasp_decay(self, jump):
        player, sigma, grasp, vitals, initial, deadline, shoot = self.commandless_grasp_grant(jump)
        source = sigma.body_script
        frozen = source.frame, source.component.now
        delay = grasp.evaluate(grasp.graph.node(71).field('m_timeout'))
        self.assertEqual(delay, 3)
        start = deadline * source.frame_ms + round(delay * 1000)
        first_decay = start // source.frame_ms
        if not jump:
            for tick in range(deadline + 1, first_decay - 1):
                shoot(tick, 0)
        shoot(first_decay - 1, 0)
        self.assertAlmostEqual(vitals.temporary_shields, 3)
        partial = first_decay + 10
        for tick in ((partial,) if jump else range(first_decay, partial + 1)):
            shoot(tick, 0)
        self.assertEqual((source.frame, source.component.now), frozen)
        elapsed = ((partial + 1) * source.frame_ms - 1 - start) / 1000
        expected = 3 - elapsed * 5
        self.assertAlmostEqual(vitals.temporary_shields, expected)
        sigma.take_commands([Command(partial, yaw=-32768)])
        self.assertAlmostEqual(vitals.temporary_shields, expected)
        shoot(first_decay + 45, 0)
        self.assertEqual(vitals.temporary_shields, 0)
        self.assertEqual(vitals.total, initial)
        shoot(first_decay + 46, 1)
        self.assertEqual(vitals.total, initial - 5)
        self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), 5)

    def test_grasp_accumulates_cannon_and_respects_geometry_team_and_lifetime(self):
        for condition in ('front', 'spend', 'rear', 'expired', 'wall', 'ally', 'melee'):
            with self.subTest(condition=condition):
                run, player, sigma, _, _ = scenes.BallCombatTests().scenario()
                run.match.switch_hero(sigma, heroes()[deflection.SIGMA])
                fight = run.match.combat
                x, y, z = player.mover.position
                distance = 1.5 if condition == 'melee' else 4
                sigma.mover.position[:] = [x, y, z + distance]
                if condition == 'ally':
                    run.match.game_map = run.match.game_map.__class__(
                        **{**run.match.game_map.__dict__, 'free_for_all': False})
                    sigma.team = player.team
                for _ in range(40):
                    run.step({})
                facing = 0 if condition == 'rear' else -32768
                run.step({sigma: (facing, 0, 8)})
                for _ in range(150 if condition == 'expired' else 5):
                    run.step({sigma: (facing, 0, 0)})
                graph = next(i for i in sigma.body_script.component.instances.values()
                             if i.graph.index == 0x1D02)
                self.assertEqual(graph.states[1].active, condition != 'expired')
                target = next(t for t in fight._targets() if t.owner is sigma)
                if condition == 'wall':
                    wall = z + 1
                    run.match.collision_world = World([
                        (x - 3, y, wall, x + 3, y, wall, x + 3, y + 4, wall),
                        (x - 3, y, wall, x + 3, y + 4, wall, x - 3, y + 4, wall),
                    ])
                angles = aim(fight._eye(player), (x, y + 0.65, z + distance))
                script = player.body_script
                script.ult_seconds = 0
                script.set_ult_charge(0)
                health = fight.vitals_of(target)
                before = health.total
                run.step({player: (*angles, 0x800 if condition == 'melee' else 1), sigma: (facing, 0, 0)})
                if condition == 'melee':
                    for _ in range(40):
                        run.step({sigma: (facing, 0, 0)})
                ammo = ball.instance(player, 0x1514).vars[53].value()
                self.assertEqual(ammo, 80 if condition == 'melee' else 79)
                loss = {'melee': 30, 'rear': 5, 'expired': 5}.get(condition, 0)
                self.assertEqual(health.total, before - loss)
                absorbed = condition in ('front', 'spend')
                self.assertEqual(graph.vars[21312].value(), 5 if absorbed else 0)
                self.assertEqual(len(player.client.messages(combat.HIT)), int(bool(loss)))
                self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), loss)
                if absorbed:
                    for _ in range(240):
                        run.step({sigma: (facing, 0, 0)})
                        if health.temporary_shields:
                            break
                    self.assertAlmostEqual(health.temporary_shields, 3, places=5)
                    self.assertAlmostEqual(health.total, before + 3, places=5)
                    self.assertFalse(graph.states[1].active)
                    if condition == 'spend':
                        run.step({player: (*angles, 1), sigma: (facing, 0, 0)})
                        self.assertEqual(health.temporary_shields, 0)
                        self.assertAlmostEqual(health.total, before - 2, places=5)
                        self.assertAlmostEqual(script.component.entity_var(ULT_CHARGE).value(), 2, places=5)
                        continue
                    for _ in range(100):
                        run.step({sigma: (facing, 0, 0)})
                    self.assertAlmostEqual(health.temporary_shields, 3, places=5)
                    for _ in range(700):
                        run.step({sigma: (facing, 0, 0)})
                        if health.temporary_shields == 0:
                            break
                    self.assertEqual(health.temporary_shields, 0)
                    self.assertEqual(health.total, before)
                    self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)


def use_mech_fixture(case):
    def clear_graphs():
        graph._records.cache_clear()
        graph.graph.cache_clear()
        pools.hero_pools.cache_clear()

    inputs = Path(os.environ['ORPHEUS_DVA_INPUT_DIR'])
    supplement = json.loads(gzip.decompress((inputs / 'mech-supplement.json.gz').read_bytes()))
    base = graph._data()
    records = {record['index']: record for record in (*base['graphs'], *supplement['graphs'])}
    merged = {**base, 'graphs': list(records.values()),
              'entities': {**base.get('entities', {}), **supplement['entities']}}
    case.addCleanup(clear_graphs)
    case.enterContext(patch.object(graph, '_data', return_value=merged))
    clear_graphs()
    mech_movers = json.loads((inputs / 'mech-movers.json').read_text())
    mech = mover.MoverData(**mech_movers[f'0x{graph.DVA_MECH:016X}'])
    movers = {**mover._movers(), graph.DVA_MECH: mech}
    case.enterContext(patch.object(mover, '_movers', return_value=movers))


@unittest.skipUnless(os.environ.get('ORPHEUS_DVA_INPUT_DIR'), 'prepared 0407 inputs required')
class BallMatrixTests(unittest.TestCase):
    def setUp(self):
        use_mech_fixture(self)

    def test_nearest_matrix_or_grasp_consumes_each_ball_cannon_round_once(self):
        for matrix_first in (False, True):
            with self.subTest(matrix_first=matrix_first):
                self.check_matrix_and_grasp_order(matrix_first=matrix_first)

    def check_matrix_and_grasp_order(self, *, matrix_first):
        run, player, dva, sigma = projectile_defense_scene(
            self, graph.DVA, deflection.SIGMA, teams=(0, 1, 1),
        )
        fight = run.match.combat
        near, far = (dva, sigma) if matrix_first else (sigma, dva)
        x, y, z = player.mover.position
        relocate(run, near, (x, y, z + 4))
        relocate(run, far, (x, y, z + 16))
        run.step({dva: (-32768, 0, 2), sigma: (-32768, 0, 8)})
        held = {dva: (-32768, 0, 2), sigma: (-32768, 0, 0)}
        for _ in range(5):
            run.step(held)
        matrix = next(i for i in dva.body_script.component.instances.values()
                      if i.graph.index == 0x02CC).state(3)
        grasp = next(i for i in sigma.body_script.component.instances.values() if i.graph.index == 0x1D02)
        absorbed = grasp.vars[21312]
        self.assertTrue(matrix.active)
        self.assertTrue(grasp.state(1).active)
        self.assertEqual(absorbed.value(), 0)

        distances = []
        for owner in (near, far):
            target = next(t for t in fight._enemies(player) if t.owner is owner)
            with patch.object(fight, '_enemies', return_value=(target,)):
                contact = deflection.intercept(
                    fight, player, fight._eye(player), combat.aim(0, 0), 100, matrix_eligible=True,
                )
            self.assertIsNotNone(contact)
            self.assertIs(contact[1], owner)
            distances.append(contact[0])
        self.assertLess(*distances)

        health = tuple(fight.vitals[owner.body].total for owner in (dva, sigma))
        ammo = ball.instance(player, 0x1514).vars[53]
        charge = player.body_script.component.entity_var(ULT_CHARGE)
        trigger = {**held, player: (0, 0, 1)}
        run.step(trigger)
        self.assertEqual(ammo.value(), 79)
        self.assertEqual(absorbed.value(), 0 if matrix_first else 5)
        self.assertEqual(charge.value(), 0)
        self.assertEqual(tuple(fight.vitals[owner.body].total for owner in (dva, sigma)), health)

        relocate(run, near, (x + 12, y, z + 4))
        for _ in range(20):
            run.step(trigger)
            if ammo.value() == 78:
                break
        self.assertTrue(matrix.active)
        self.assertTrue(grasp.state(1).active)
        self.assertEqual(ammo.value(), 78)
        self.assertEqual(absorbed.value(), 5)
        self.assertEqual(charge.value(), 0)
        self.assertEqual(tuple(fight.vitals[owner.body].total for owner in (dva, sigma)), health)
        self.assertFalse(player.client.messages(combat.HIT))

    def test_allied_matrix_consumes_hostile_rifle_and_passes_friendly_healing(self):
        check_biotic_rifle_defense(self, graph.DVA)

    def test_grasp_takes_over_at_matrix_depletion_with_frozen_commands(self):
        for jump in (False, True):
            with self.subTest(jump=jump):
                self.check_grasp_at_matrix_depletion(jump=jump)

    def check_grasp_at_matrix_depletion(self, *, jump):
        run, player, dva, sigma = projectile_defense_scene(
            self, graph.DVA, deflection.SIGMA, teams=(0, 1, 1),
        )
        match = run.match
        fight = match.combat
        x, y, z = player.mover.position
        relocate(run, dva, (x + 1.5, y, z + 4))
        relocate(run, sigma, (x, y, z + 16))
        yaw, pitch = aim(fight._eye(dva), fight._eye(player))
        run.step({dva: (yaw, pitch, 2)})
        source = dva.body_script
        frozen = source.frame, source.component.now
        matrix = next(i for i in source.component.instances.values() if i.graph.index == 0x02CC)
        drain = matrix.state(45)
        resource = matrix.vars[87].value()
        rate = drain.evaluate(drain.node.field('m_9B0385B4'), watch=False)
        deadline = (drain.last + round(1000 * resource / rate)) // source.frame_ms
        self.assertEqual(deadline - run.frame, 125)

        def advance(frame):
            for tick in (frame,) if jump else range(match.tick + 1, frame + 1):
                match.update(tick * combat.TICK, tick, frames=False)

        grasp_frame = deadline - 90
        advance(grasp_frame)
        sigma.take_commands([Command(grasp_frame, yaw=-32768, buttons=8)])
        grasp = next(i for i in sigma.body_script.component.instances.values() if i.graph.index == 0x1D02)
        absorbed = grasp.vars[21312]
        self.assertTrue(matrix.state(3).active)
        self.assertTrue(grasp.state(1).active)
        vitals = [fight.vitals_of(t) for t in fight._enemies(player)]
        health = [v.total for v in vitals]
        ammo = ball.instance(player, 0x1514).vars[53]
        charge = player.body_script.component.entity_var(ULT_CHARGE)

        def fire(frame):
            advance(frame)
            player.take_commands([Command(frame, buttons=1)])

        fire(deadline - 2)
        self.assertEqual(ammo.value(), 79)
        self.assertEqual(absorbed.value(), 0)
        fire(deadline - 1)
        self.assertEqual(ammo.value(), 79)
        fire(deadline)
        self.assertEqual(ammo.value(), 78)
        self.assertEqual((source.frame, source.component.now), frozen)
        self.assertTrue(matrix.state(3).active)
        self.assertEqual(matrix.vars[87].value(), resource)
        self.assertEqual(absorbed.value(), 5)
        self.assertEqual([v.total for v in vitals], health)
        self.assertEqual(charge.value(), 0)

        dva.take_commands([Command(deadline, yaw=yaw, pitch=pitch, buttons=2)])
        self.assertGreater(source.frame, frozen[0])
        self.assertEqual(ammo.value(), 78)
        self.assertEqual(absorbed.value(), 5)
        for tick in range(deadline + 1, deadline + 16):
            fire(tick)
            if ammo.value() == 77:
                break
        self.assertEqual(ammo.value(), 77)
        self.assertEqual(absorbed.value(), 10)
        self.assertEqual([v.total for v in vitals], health)
        self.assertEqual(charge.value(), 0)
        self.assertFalse(player.client.messages(combat.HIT))

    def test_matrix_takes_over_at_grasp_expiry_with_frozen_commands(self):
        for jump in (False, True):
            with self.subTest(jump=jump):
                self.check_matrix_at_grasp_expiry(jump=jump, matrix_outlasts=True)

    def test_cannon_hits_armor_when_matrix_and_grasp_end_on_same_frame(self):
        stepped = self.check_matrix_at_grasp_expiry(jump=False, matrix_outlasts=False)
        skipped = self.check_matrix_at_grasp_expiry(jump=True, matrix_outlasts=False)
        self.assertEqual(skipped, stepped)

    def check_matrix_at_grasp_expiry(self, *, jump, matrix_outlasts):
        run, player, dva, sigma = projectile_defense_scene(
            self, graph.DVA, deflection.SIGMA, teams=(0, 1, 1),
        )
        match = run.match
        fight = match.combat
        x, y, z = player.mover.position
        relocate(run, sigma, (x + 1.5, y, z + 4))
        relocate(run, dva, (x, y, z + 16))
        yaw, pitch = aim(fight._eye(sigma), fight._eye(player))
        activation = {sigma: (yaw, pitch, 8)}
        if not matrix_outlasts:
            activation[dva] = (-32768, 0, 2)
        run.step(activation)
        source = sigma.body_script
        frozen = source.frame, source.component.now
        grasp = next(i for i in source.component.instances.values() if i.graph.index == 0x1D02)
        absorbed = grasp.vars[21312]
        self.assertTrue(grasp.state(1).active)
        self.assertEqual(grasp.vars[567].value(), 2)
        deadline = (grasp.state(20).start + 2000) // source.frame_ms

        def advance(frame):
            for tick in (frame,) if jump else range(match.tick + 1, frame + 1):
                match.update(tick * combat.TICK, tick, frames=False)

        if matrix_outlasts:
            matrix_frame = deadline - 90
            advance(matrix_frame)
            dva.take_commands([Command(matrix_frame, yaw=-32768, buttons=2)])
        matrix = next(i for i in dva.body_script.component.instances.values() if i.graph.index == 0x02CC)
        self.assertTrue(matrix.state(3).active)
        drain = matrix.state(45)
        rate = drain.evaluate(drain.node.field('m_9B0385B4'), watch=False)
        matrix_end = (drain.last + round(1000 * drain.cur / rate)) // dva.body_script.frame_ms
        self.assertEqual(matrix_end, deadline + (35 if matrix_outlasts else 0))
        for target in fight._enemies(player):
            fight.vitals_of(target)
        sigma_vitals, mech_vitals = (fight.vitals[owner.body] for owner in (sigma, dva))
        initial = sigma_vitals.total, mech_vitals.total
        initial_armor = mech_vitals.now[combat.ARMOUR_POOL]
        ammo = ball.instance(player, 0x1514).vars[53]
        charge = player.body_script.component.entity_var(ULT_CHARGE)
        intercept = deflection.intercept
        contacts = []

        def record_intercept(*args, **kwargs):
            contact = intercept(*args, **kwargs)
            contacts.append(contact[1] if contact else None)
            return contact

        def fire(frame):
            advance(frame)
            with patch.object(deflection, 'intercept', side_effect=record_intercept):
                player.take_commands([Command(frame, buttons=1)])

        fire(deadline - 2)
        self.assertEqual(contacts, [sigma])
        self.assertEqual((ammo.value(), absorbed.value(), sigma_vitals.temporary_shields), (79, 5, 0))
        fire(deadline - 1)
        self.assertEqual(ammo.value(), 79)
        fire(deadline)
        interceptor = dva if matrix_outlasts else None
        self.assertEqual(contacts, [sigma, interceptor])
        self.assertEqual((source.frame, source.component.now), frozen)
        self.assertTrue(grasp.state(1).active)
        self.assertEqual((ammo.value(), absorbed.value(), sigma_vitals.temporary_shields), (78, 5, 3))
        self.assertEqual(sigma_vitals.total, initial[0] + 3)
        first_damage = initial[1] - mech_vitals.total
        if matrix_outlasts:
            self.assertEqual(first_damage, 0)
        else:
            self.assertGreater(first_damage, 0)
        self.assertAlmostEqual(charge.value(), first_damage, places=4)
        self.assertAlmostEqual(initial_armor - mech_vitals.now[combat.ARMOUR_POOL], first_damage, places=4)

        sigma.take_commands([Command(deadline, yaw=yaw, pitch=pitch)])
        if not matrix_outlasts:
            dva.take_commands([Command(deadline, yaw=-32768, buttons=2)])
        self.assertGreater(source.frame, frozen[0])
        self.assertEqual((ammo.value(), sigma_vitals.temporary_shields), (78, 3))
        self.assertAlmostEqual(charge.value(), first_damage, places=4)
        for tick in range(deadline + 1, deadline + 16):
            fire(tick)
            if ammo.value() == 77:
                break
        self.assertEqual((ammo.value(), sigma_vitals.temporary_shields), (77, 3))
        self.assertEqual(contacts, [sigma, interceptor, interceptor])
        self.assertEqual(sigma_vitals.total, initial[0] + 3)
        total_damage = initial[1] - mech_vitals.total
        if matrix_outlasts:
            self.assertEqual(total_damage, 0)
        else:
            self.assertGreater(total_damage, first_damage)
        self.assertAlmostEqual(charge.value(), total_damage, places=4)
        self.assertAlmostEqual(initial_armor - mech_vitals.now[combat.ARMOUR_POOL], total_damage, places=4)
        self.assertEqual(len(player.client.messages(combat.HIT)), 0 if matrix_outlasts else 2)
        return first_damage, total_damage

    def test_nearest_matrix_or_grasp_consumes_ball_cannon_after_deflect(self):
        for matrix_first in (False, True):
            with self.subTest(matrix_first=matrix_first):
                self.check_reflected_matrix_and_grasp_order(matrix_first=matrix_first)

    def check_reflected_matrix_and_grasp_order(self, *, matrix_first):
        run = Run(KANEZAKA_DM, ('Ball', 'Dva', 'Sigma', 'Genji'))
        player, dva, sigma, genji = run.players
        match = run.match
        match.game_map = replace(match.game_map, free_for_all=False)
        match.bots.clear()
        match._pending_bot_scripts.clear()
        for owner, hero in ((player, ball.HERO), (dva, graph.DVA), (sigma, deflection.SIGMA),
                            (genji, deflection.GENJI)):
            owner.team = int(owner is genji)
            selected = heroes()[hero]
            if owner is dva:
                selected = replace(selected, body=graph.DVA_MECH)
            self.assertTrue(match.switch_hero(owner, selected))
            owner.body_script.ult_seconds = 0
            owner.body_script.set_ult_charge(0)
        near, far = (dva, sigma) if matrix_first else (sigma, dva)
        x, y, z = player.mover.position
        relocate(run, genji, (x, y, z + 18))
        relocate(run, near, (x, y, z + 14))
        relocate(run, far, (x, y, z + 2))
        for _ in range(100):
            run.step({})
        run.step({dva: (0, 0, 2), sigma: (0, 0, 8), genji: (-32768, 0, 16)})
        held = {dva: (0, 0, 2), sigma: (0, 0, 0), genji: (-32768, 0, 0)}
        for _ in range(5):
            run.step(held)

        fight = match.combat
        grasp = next(i for i in sigma.body_script.component.instances.values() if i.graph.index == 0x1D02)
        absorbed = grasp.vars[21312]
        self.assertEqual(absorbed.value(), 0)
        ammo = ball.instance(player, 0x1514).vars[53]
        health = {target.owner: fight.vitals_of(target).total for target in fight._targets()}
        self.assertEqual(set(health), set(run.players))
        angles = aim(fight._eye(player), (x, y + 0.55, z + 18))
        trigger = {**held, player: (*angles, 1)}
        intercept = deflection.intercept
        contacts = []

        def record_intercept(fight, shooter, *args, **kwargs):
            contact = intercept(fight, shooter, *args, **kwargs)
            contacts.append((shooter, contact[1] if contact else None))
            return contact

        with patch.object(deflection, 'intercept', side_effect=record_intercept):
            run.step(trigger)
            self.assertEqual(ammo.value(), 79)
            self.assertEqual(contacts, [(player, genji), (genji, near)])
            self.assertAlmostEqual(absorbed.value(), 0 if matrix_first else 4.14494, places=4)
            relocate(run, near, (x + 12, y, z + 14))
            contacts.clear()
            for _ in range(20):
                run.step(trigger)
                if ammo.value() == 78:
                    break
            self.assertEqual(ammo.value(), 78)
            self.assertEqual(contacts, [(player, genji), (genji, far)])
            self.assertAlmostEqual(absorbed.value(), 1.5 if matrix_first else 4.14494, places=4)

        for owner, total in health.items():
            self.assertEqual(fight.vitals[owner.body].total, total)
            self.assertFalse(owner.client.messages(combat.HIT))
        for owner in (player, genji):
            self.assertEqual(owner.body_script.component.entity_var(ULT_CHARGE).value(), 0)

    def test_allied_matrix_consumes_biotic_grenade_without_healing_denial(self):
        check_grenade_absorption(self, graph.DVA, biotic=True)

    def test_mech_selection_rejects_missing_mover_before_replacing_the_body(self):
        run, _, enemy, _, _ = scenes.BallCombatTests().scenario()
        before = enemy.body
        script = enemy.body_script
        movers = {body: data for body, data in mover._movers().items() if body != graph.DVA_MECH}
        with patch.object(mover, '_movers', return_value=movers):
            self.assertFalse(run.match.switch_hero(enemy, replace(heroes()[graph.DVA], body=graph.DVA_MECH)))
        self.assertEqual(enemy.body, before)
        self.assertIs(enemy.body_script, script)

    def mech_scene(self, distance):
        run, player, enemy, _, _ = scenes.BallCombatTests().scenario(distance=distance)
        target, vitals, matrix = self.select_mech(run, player, enemy)
        return run, player, enemy, target, vitals, matrix

    def select_mech(self, run, player, enemy):
        position = tuple(enemy.mover.position)
        self.assertTrue(run.match.switch_hero(enemy, replace(heroes()[graph.DVA], body=graph.DVA_MECH)))
        relocate(run, enemy, position)
        for _ in range(100):
            run.step({})
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is enemy)
        vitals = fight.vitals_of(target)
        matrix = next(i for i in enemy.body_script.component.instances.values() if i.graph.index == 0x02CC)
        player.body_script.ult_seconds = 0
        player.body_script.set_ult_charge(0)
        return target, vitals, matrix

    def test_active_matrix_leaves_melee_and_piledriver_damage(self):
        for piledriver in (False, True):
            with self.subTest(piledriver=piledriver):
                run, player, enemy, target, vitals, matrix = self.mech_scene(1.5)
                fight = run.match.combat
                if piledriver:
                    lift(run, player)
                    cast(run, player, 30)
                for _ in range(5):
                    run.step({enemy: (-32768, 0, 2)})
                self.assertTrue(matrix.state(3).active)
                angles = aim(fight._eye(player), (target.feet[0], target.feet[1] + 1, target.feet[2]))
                for tick in range(75):
                    buttons = 0x800 if not piledriver and tick == 0 else 0
                    run.step({enemy: (-32768, 0, 2), player: (*angles, buttons)})
                    if vitals.total < 600:
                        break
                damage = 95 if piledriver else 25
                self.assertEqual(vitals.total, 600 - damage)
                self.assertTrue(matrix.state(3).active)
                self.assertEqual(ball.instance(player, 0x1514).vars[53].value(), 80)
                self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), damage)
                if piledriver:
                    self.assertEqual(pile_instance(player).state(7).counter, 1)
                    self.assertGreater(enemy.mover.velocity[1], 10)
                    self.assertIn(enemy.body, fight.pile_air_locks)

    def test_authored_matrix_consumes_ball_cannon_then_release_reopens_damage(self):
        run, player, enemy, target, vitals, matrix = self.mech_scene(4)
        fight = run.match.combat
        self.assertEqual(vitals.total, 600)
        self.assertEqual(matrix.vars[87].value(), 100)
        for _ in range(20):
            run.step({enemy: (-32768, 0, 2)})
        self.assertTrue(matrix.state(3).active)
        self.assertLess(matrix.vars[87].value(), 100)
        charge = player.body_script.component.entity_var(ULT_CHARGE)
        ammo = ball.instance(player, 0x1514).vars[53]
        angles = aim(fight._eye(player), (target.feet[0], target.feet[1] + 1, target.feet[2]))
        run.step({enemy: (-32768, 0, 2), player: (*angles, 1)})
        self.assertEqual(vitals.total, 600)
        self.assertEqual(ammo.value(), 79)
        self.assertEqual(charge.value(), 0)
        self.assertFalse(player.client.messages(combat.HIT))

        for _ in range(30):
            run.step({})
            if not matrix.state(3).active:
                break
        self.assertFalse(matrix.state(3).active)
        run.step({player: (*angles, 1)})
        self.assertEqual(vitals.total, 597.5)
        self.assertEqual(ammo.value(), 78)
        self.assertEqual(charge.value(), 2.5)


    def test_matrix_cannot_intercept_past_its_remaining_resource_during_command_gap(self):
        for partial in (False, True):
            with self.subTest(partial=partial):
                self.check_matrix_command_gap(partial)

    def check_matrix_command_gap(self, partial):
        run, player, enemy, target, vitals, matrix = self.mech_scene(4)
        match = run.match
        fight = match.combat
        if partial:
            for _ in range(130):
                run.step({enemy: (-32768, 0, 2)})
            self.assertEqual(matrix.vars[87].value(), 0)
            for _ in range(200):
                run.step({})
            self.assertGreater(matrix.vars[87].value(), 10)
            self.assertLess(matrix.vars[87].value(), 100)
        run.step({enemy: (-32768, 0, 2)})
        self.assertTrue(matrix.state(3).active)
        source = enemy.body_script
        frozen = source.frame, source.component.now
        drain = matrix.state(45)
        resource = matrix.vars[87].value()
        rate = drain.evaluate(drain.node.field('m_9B0385B4'), watch=False)
        self.assertEqual(rate, 50)
        deadline = (drain.last + round(1000 * resource / rate)) // source.frame_ms
        if partial:
            self.assertLess(deadline - run.frame, 125)
            self.assertGreater(deadline - run.frame, 30)
        else:
            self.assertEqual(deadline - run.frame, 125)
        angles = aim(fight._eye(player), (target.feet[0], target.feet[1] + 1, target.feet[2]))

        def fire(frame, buttons):
            match.update(frame * combat.TICK, frame, frames=False)
            player.take_commands([Command(frame, yaw=angles[0], pitch=angles[1], buttons=buttons)])

        fire(deadline - 30, 1)
        fire(deadline - 29, 0)
        ammo = ball.instance(player, 0x1514).vars[53]
        self.assertEqual(ammo.value(), 79)
        self.assertEqual(vitals.total, 600)
        self.assertFalse(player.client.messages(combat.HIT))
        fire(deadline, 1)
        self.assertEqual(ammo.value(), 78)
        self.assertEqual((source.frame, source.component.now), frozen)
        self.assertEqual(matrix.vars[87].value(), resource)
        self.assertTrue(matrix.state(3).active)
        self.assertEqual(vitals.total, 597.5)
        self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), 2.5)

    def test_sustained_cannon_crosses_matrix_depletion_and_reuse(self):
        run, player, enemy, target, vitals, matrix = self.mech_scene(4)
        fight = run.match.combat
        charge = player.body_script.component.entity_var(ULT_CHARGE)
        ammo = ball.instance(player, 0x1514).vars[53]
        angles = aim(fight._eye(player), (target.feet[0], target.feet[1] + 1, target.feet[2]))
        for _ in range(5):
            run.step({enemy: (-32768, 0, 2)})
        self.assertTrue(matrix.state(3).active)

        deadline = matrix.state(3).start // enemy.body_script.frame_ms + 125
        for _ in range(145):
            run.step({enemy: (-32768, 0, 2), player: (*angles, 1)})
            if run.frame < deadline:
                self.assertEqual(vitals.total, 600)
                self.assertEqual(charge.value(), 0)
                self.assertFalse(player.client.messages(combat.HIT))
        self.assertFalse(matrix.state(3).active)
        self.assertEqual(matrix.vars[87].value(), 0)
        self.assertLess(ammo.value(), 80)
        self.assertLess(vitals.total, 600)
        self.assertEqual(charge.value(), 600 - vitals.total)

        for _ in range(41):
            run.step({})
        self.assertEqual(matrix.vars[61].value(), 0)
        self.assertLess(matrix.vars[87].value(), 10)
        before = vitals.total
        run.step({enemy: (-32768, 0, 2), player: (*angles, 1)})
        self.assertFalse(matrix.state(3).active)
        self.assertEqual(vitals.total, before - 2.5)

        for _ in range(80):
            run.step({})
        self.assertGreater(matrix.vars[87].value(), 10)
        run.step({enemy: (-32768, 0, 2)})
        self.assertTrue(matrix.state(3).active)
        before = vitals.total
        earned = charge.value()
        rounds = ammo.value()
        run.step({enemy: (-32768, 0, 2), player: (*angles, 1)})
        self.assertEqual(ammo.value(), rounds - 1)
        self.assertEqual(vitals.total, before)
        self.assertEqual(charge.value(), earned)

    def test_cannon_hits_through_allied_matrix_and_hits_mech_from_rear(self):
        for allied in (True, False):
            with self.subTest(allied=allied):
                run = Run(KANEZAKA_DM, ('Ball', 'Mech', 'Enemy'))
                player, mech, enemy = run.players
                run.match.bots.clear()
                run.match._pending_bot_scripts.clear()
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                player.team, mech.team, enemy.team = 0, int(not allied), 1
                self.assertTrue(run.match.switch_hero(player, heroes()[ball.HERO]))
                x, y, z = player.mover.position
                relocate(run, mech, (x, y, z + 4))
                relocate(run, enemy, (x, y, z + 7))
                _, mech_vitals, matrix = self.select_mech(run, player, mech)
                fight = run.match.combat
                target = next(t for t in fight._targets() if t.owner is (enemy if allied else mech))
                vitals = fight.vitals_of(target)
                before = vitals.total
                yaw = -32768 if allied else 0
                for _ in range(8):
                    run.step({mech: (yaw, 0, 2)})
                self.assertTrue(matrix.state(3).active)
                angles = aim(fight._eye(player), (x, y + 1, target.feet[2]))
                run.step({mech: (yaw, 0, 2), player: (*angles, 1)})

                damage = 5 if allied else 2.5
                self.assertEqual(vitals.total, before - damage)
                self.assertEqual(mech_vitals.total, 600 if allied else 597.5)
                self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), damage)
                self.assertEqual(ball.instance(player, 0x1514).vars[53].value(), 79)
                self.assertEqual(len(player.client.messages(combat.HIT)), 1)

    def test_allied_matrix_consumes_flashbang_without_damage_or_stun(self):
        check_grenade_absorption(self, graph.DVA)

    def test_allied_matrix_blocks_sleep_dart_until_release(self):
        for active in (True, False):
            with self.subTest(active=active):
                run, player, mech, ana = projectile_defense_scene(self, graph.DVA)
                fight = run.match.combat
                own = fight.vitals_of(next(t for t in fight._targets() if t.owner is player))
                instances = mech.body_script.component.instances.values()
                matrix = next(i for i in instances if i.graph.index == 0x02CC)
                held = {mech: (0, 0, 2)}
                run.step({**held, player: (0, 0, 16)})
                self.assertTrue(matrix.state(3).active)
                self.assertEqual(own.total, 775)
                if not active:
                    held = {}
                    for _ in range(65):
                        run.step({})
                        if not matrix.state(3).active:
                            break
                self.assertEqual(matrix.state(3).active, active)
                dart = launch_sleep_dart(self, run, player, ana, held)
                for _ in range(30):
                    run.step(held)
                    if dart not in fight.rockets:
                        break
                self.assertNotIn(dart, fight.rockets)
                self.assertEqual(own.total, 775 if active else 770)
                self.assertEqual(player.body in fight.sleeps, not active)
                self.assertEqual(matrix.state(3).active, active)
                if active:
                    self.assertLess(matrix.vars[87].value(), 100)
                script = player.body_script
                cost = script.component.entity_var(ULT_COST).value()
                script.set_ult_charge(cost)
                run.step({**held, player: (0, 0, 32)})
                self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0 if active else cost)

    def test_rear_sleep_dart_interrupts_matrix_during_held_cannon_and_reopens_reuse(self):
        run = Run(KANEZAKA_DM, ('Ball', 'Mech', 'Ana'))
        player, mech, ana = run.players
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        player.team, mech.team, ana.team = 0, 1, 0
        self.assertTrue(run.match.switch_hero(player, heroes()[ball.HERO]))
        self.assertTrue(run.match.switch_hero(ana, heroes()[ANA]))
        x, y, z = player.mover.position
        relocate(run, mech, (x, y, z + 4))
        relocate(run, ana, (x + 1.5, y, z + 7))
        target, vitals, matrix = self.select_mech(run, player, mech)
        fight = run.match.combat
        angles = aim(fight._eye(player), (x, y + 1, target.feet[2]))
        dart = aim(fight._eye(ana), (x, y + 1, target.feet[2]))
        charge = player.body_script.component.entity_var(ULT_CHARGE)
        ammo = ball.instance(player, 0x1514).vars[53]
        for _ in range(8):
            run.step({mech: (-32768, 0, 2)})
        self.assertTrue(matrix.state(3).active)
        for tick in range(65):
            run.step({player: (*angles, 1), mech: (-32768, 0, 2),
                      ana: (*dart, 8 if tick == 0 else 0)})
            if mech.body in fight.sleeps:
                break
            self.assertTrue(matrix.state(3).active)
            self.assertEqual(vitals.total, 600)
            self.assertEqual(charge.value(), 0)
        self.assertIn(mech.body, fight.sleeps)
        self.assertFalse(matrix.state(3).active)
        self.assertLess(matrix.vars[87].value(), 100)
        self.assertLess(ammo.value(), 80)

        for _ in range(8):
            run.step({player: (*angles, 1), mech: (-32768, 0, 2)})
            self.assertTrue(fight.is_stunned(mech))
            self.assertFalse(matrix.state(3).active)
        self.assertGreater(charge.value(), 0)
        self.assertEqual(vitals.total, 597.5 - charge.value())
        _, deadline = fight.sleeps[mech.body]
        while run.frame < deadline:
            run.step({})
        self.assertFalse(fight.is_stunned(mech))
        self.assertGreater(matrix.vars[87].value(), 10)

        run.step({mech: (-32768, 0, 2)})
        self.assertTrue(matrix.state(3).active)
        before, earned, rounds = vitals.total, charge.value(), ammo.value()
        run.step({player: (*angles, 1), mech: (-32768, 0, 2)})
        self.assertEqual(ammo.value(), rounds - 1)
        self.assertEqual(vitals.total, before)
        self.assertEqual(charge.value(), earned)

    def test_rear_sleep_dart_cancels_held_matrix_across_command_gap(self):
        run, player, mech, ana = projectile_defense_scene(self, graph.DVA, teams=(0, 1, 0))
        match = run.match
        fight = match.combat
        x, y, z = player.mover.position
        relocate(run, mech, (x, y, z + 4))
        relocate(run, ana, (x + 1.5, y, z + 7))
        target = (x, y + 1, z + 4)
        dart = aim(fight._eye(ana), target)
        cannon = aim(fight._eye(player), target)
        run.step({mech: (-32768, 0, 2), ana: (*dart, 8)})
        source = mech.body_script
        frozen = source.frame, source.component.now
        matrix = next(i for i in source.component.instances.values() if i.graph.index == 0x02CC)
        active = matrix.state(3)
        self.assertTrue(active.active)
        activation = active.counter

        def advance(frame):
            match.update(frame * combat.TICK, frame, frames=False)

        for frame in range(run.frame + 1, run.frame + 66):
            advance(frame)
            ana.take_commands([Command(frame, yaw=dart[0], pitch=dart[1])])
            if mech.body in fight.sleeps:
                break
        self.assertIn(mech.body, fight.sleeps)
        _, deadline = fight.sleeps[mech.body]
        self.assertTrue(fight.is_stunned(mech))
        self.assertEqual((source.frame, source.component.now), frozen)
        self.assertFalse(active.active)
        vitals = fight.vitals[mech.body]
        self.assertEqual(vitals.total, 597.5)

        frame += 1
        advance(frame)
        ammo = ball.instance(player, 0x1514).vars[53]
        charge = player.body_script.component.entity_var(ULT_CHARGE)
        player.take_commands([Command(frame, yaw=cannon[0], pitch=cannon[1], buttons=1)])
        self.assertEqual((ammo.value(), charge.value(), vitals.total), (79, 2.5, 595))
        mech.take_commands([Command(frame, yaw=-32768, buttons=2)])
        self.assertFalse(active.active)
        self.assertEqual(active.counter, activation)
        self.assertEqual((ammo.value(), charge.value(), vitals.total), (79, 2.5, 595))

        advance(frame + 1)
        player.take_commands([Command(frame + 1, yaw=cannon[0], pitch=cannon[1])])
        advance(deadline)
        mech.take_commands([Command(deadline, yaw=-32768)])
        self.assertFalse(fight.is_stunned(mech))
        self.assertFalse(active.active)
        advance(deadline + 1)
        mech.take_commands([Command(deadline + 1, yaw=-32768, buttons=2)])
        self.assertTrue(active.active)
        self.assertEqual(active.counter, activation + 1)
        player.take_commands([Command(deadline + 1, yaw=cannon[0], pitch=cannon[1], buttons=1)])
        self.assertEqual((ammo.value(), charge.value(), vitals.total), (78, 2.5, 595))

    def test_lethal_piledriver_ends_matrix_interception_at_death(self):
        run = Run(KANEZAKA_DM, ('Ball', 'Mech', 'Behind'))
        player, mech, behind = run.players
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        self.assertTrue(run.match.switch_hero(player, heroes()[ball.HERO]))
        x, y, z = player.mover.position
        relocate(run, mech, (x, y, z + 1.5))
        relocate(run, behind, (x + 20, y, z))
        target, vitals, matrix = self.select_mech(run, player, mech)
        fight = run.match.combat
        aim_point = (target.feet[0], target.feet[1] + 1, target.feet[2])
        for _ in range(1400):
            run.step({player: (*aim(fight._eye(player), aim_point), 1)})
            if vitals.total <= 90:
                break
        self.assertGreater(vitals.total, 0)
        self.assertLessEqual(vitals.total, 90)
        remaining = vitals.total
        charge = player.body_script.component.entity_var(ULT_CHARGE)
        before = charge.value()
        lift(run, player)
        cast(run, player, 30)
        for _ in range(8):
            run.step({mech: (-32768, 0, 2)})
        self.assertTrue(matrix.state(3).active)
        for _ in range(75):
            run.step({mech: (-32768, 0, 2)})
            if vitals.dead:
                break
            self.assertTrue(matrix.state(3).active)
        self.assertTrue(vitals.dead)
        self.assertEqual(pile_instance(player).state(7).counter, 1)
        self.assertEqual(vitals.killer, player.body)
        self.assertEqual(charge.value(), before + remaining)
        self.assertNotIn(mech.body, fight.pile_air_locks)

        origin = fight._eye(player)
        direction = combat.aim(*aim(origin, aim_point))
        self.assertIsNone(deflection.intercept(fight, player, origin, direction, 6, matrix_eligible=True))

        relocate(run, behind, (x, y, z + 4))
        other = fight.vitals_of(next(t for t in fight._targets() if t.owner is behind))
        before = charge.value()
        for _ in range(150):
            angles = aim(fight._eye(player), (x, y + 1, z + 4))
            run.step({player: (*angles, 1), mech: (-32768, 0, 2)})
            if other.total < 200:
                break
        self.assertEqual(other.total, 195)
        self.assertEqual(charge.value(), before + 5)
        self.assertTrue(vitals.dead)

    def test_replacing_active_matrix_owner_reopens_hits_and_new_mech_can_block(self):
        run, player, mech, target, vitals, matrix = self.mech_scene(4)
        fight = run.match.combat
        position = tuple(mech.mover.position)
        angles = aim(fight._eye(player), (target.feet[0], target.feet[1] + 1, target.feet[2]))
        charge = player.body_script.component.entity_var(ULT_CHARGE)
        ammo = ball.instance(player, 0x1514).vars[53]
        for _ in range(8):
            run.step({mech: (-32768, 0, 2)})
        self.assertTrue(matrix.state(3).active)
        run.step({player: (*angles, 1), mech: (-32768, 0, 2)})
        self.assertEqual(vitals.total, 600)
        self.assertEqual(ammo.value(), 79)
        self.assertEqual(charge.value(), 0)

        old_script = mech.body_script
        old_body = mech.body
        self.assertTrue(run.match.switch_hero(mech, heroes()[SOLDIER]))
        relocate(run, mech, position)
        self.assertTrue(old_script.retired)
        self.assertNotEqual(mech.body, old_body)
        replacement = next(t for t in fight._targets() if t.owner is mech)
        fresh = fight.vitals_of(replacement)
        for _ in range(12):
            run.step({player: (*angles, 1)})
            if fresh.total < 200:
                break
        self.assertEqual(fresh.total, 195)
        self.assertEqual(ammo.value(), 78)
        self.assertEqual(charge.value(), 5)
        self.assertEqual(vitals.total, 600)
        self.assertNotIn(old_body, fight.vitals)

        run.step({})
        _, fresh, fresh_matrix = self.select_mech(run, player, mech)
        player.body_script.set_ult_charge(5)
        self.assertIsNot(fresh_matrix, matrix)
        self.assertEqual(fresh_matrix.vars[87].value(), 100)
        for _ in range(8):
            run.step({mech: (-32768, 0, 2)})
        self.assertTrue(fresh_matrix.state(3).active)
        run.step({player: (*angles, 1), mech: (-32768, 0, 2)})
        self.assertEqual(fresh.total, 600)
        self.assertEqual(ammo.value(), 77)
        self.assertEqual(charge.value(), 5)

    def test_active_matrix_leaves_grapple_wall_attachment_and_cooldown(self):
        run, player, enemy, _, _ = swing_scene()
        _, vitals, matrix = self.select_mech(run, player, enemy)
        fight = run.match.combat
        for _ in range(5):
            run.step({enemy: (-32768, 0, 2)})
        self.assertTrue(matrix.state(3).active)
        origin = fight._eye(player)
        intercept = deflection.intercept(fight, player, origin, combat.aim(0, 0), 12,
                                         matrix_eligible=True)
        self.assertIsNotNone(intercept)
        self.assertIs(intercept[3], matrix.state(3))
        for _ in range(5):
            run.step({player: (0, 0, 2), enemy: (-32768, 0, 2)})
        hook = fight.ball_grapples[player.body]
        self.assertTrue(hook.attached)
        self.assertAlmostEqual(hook.anchor[2], origin[2] + 12, places=4)
        run.step({enemy: (-32768, 0, 2)})
        self.assertTrue(matrix.state(3).active)
        self.assertNotIn(player.body, fight.ball_grapples)
        self.assertGreater(ball.instance(player, ball.GRAPPLE).state(25).remaining(), 4.8)
        self.assertEqual(vitals.total, 600)
        self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), 0)

    def test_active_matrix_leaves_actual_fireball_damage_and_impulse(self):
        run, player, enemy, _, _ = swing_scene()
        _, vitals, matrix = self.select_mech(run, player, enemy)
        lift(run, player)
        run.step({enemy: (-32768, 0, 2), player: (0, 0, 2)})
        for _ in range(100):
            run.step({enemy: (-32768, 0, 2), player: (0, 0, 2)})
            if vitals.total < 600:
                break
        self.assertTrue(matrix.state(3).active)
        self.assertTrue(ball.instance(player, 0x1203).state(35).active)
        self.assertEqual(vitals.total, 555)
        self.assertGreater(enemy.mover.velocity[2], 20)
        self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), 45)

    @unittest.skipUnless(os.environ.get('ORPHEUS_BALL_MINE_BUNDLE'), 'prepared Minefield inputs required')
    def test_flying_mine_survives_crossing_active_projectile_defenses(self):
        for hero, graph_id, index, button in (
            (graph.DVA, 0x02CC, 3, 2), (deflection.SIGMA, 0x1D02, 1, 8),
            (deflection.GENJI, 0x03A3, 10, 16),
        ):
            with self.subTest(hero=hex(hero)):
                self.check_flying_mine_crossing(hero, graph_id, index, button)

    def check_flying_mine_crossing(self, hero, graph_id, index, button):
        if hero == graph.DVA:
            run, player, enemy, _, vitals, defense = self.mech_scene(2)
        else:
            run, player, enemy, _, _ = scenes.BallCombatTests().scenario(
                distance=0.75 if hero == deflection.GENJI else 1.5,
            )
            position = tuple(enemy.mover.position)
            run.match.switch_hero(enemy, heroes()[hero])
            relocate(run, enemy, position)
            for _ in range(100):
                run.step({})
            defense = next(i for i in enemy.body_script.component.instances.values()
                           if i.graph.index == graph_id)
            fight = run.match.combat
            target = next(target for target in fight._targets() if target.owner is enemy)
            vitals = fight.vitals_of(target)
        fight = run.match.combat
        script = player.body_script
        script.ult_seconds = 0
        before = vitals.total
        state = defense.state(index)
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        run.step({player: (0, 0, 32)})
        for _ in range(10):
            if fight.mines.entities:
                break
            run.step({})
        mine, = fight.mines.entities.values()
        self.assertFalse(mine.landed)
        for _ in range(65):
            previous = mine.position
            angles = aim(fight._eye(enemy), previous)
            run.step({enemy: (*angles, button)})
            self.assertIs(fight.mines.entities.get(mine.entity), mine)
            if mine.landed:
                self.fail('the mine landed without crossing active defense geometry')
            length = math.dist(previous, mine.position)
            if length == 0:
                continue
            direction = tuple((end - start) / length
                              for start, end in zip(previous, mine.position, strict=True))
            # Opt in only to check geometry. Native eligibility for body 2578 remains open.
            hit = deflection.intercept(fight, player, previous, direction, length, matrix_eligible=True)
            if hit is not None:
                self.assertIs(hit[1], enemy)
                self.assertIs(hit[3], None if hero == deflection.GENJI else state)
                break
        else:
            self.fail('the actual flight never entered active defense geometry')
        self.assertTrue(state.active)
        if hero == graph.DVA:
            self.assertLess(defense.vars[87].value(), 100)
        elif hero == deflection.SIGMA:
            self.assertEqual(defense.vars[21312].value(), 0)
        self.assertEqual(mine.health, 50)
        self.assertIs(mine.owner, player)
        self.assertEqual(vitals.total, before)
        self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)

        # Exercise cannon input while the very same defense activation is live.
        target = next(target for target in fight._targets() if target.owner is enemy)
        own_target = next(target for target in fight._targets() if target.owner is player)
        own_vitals = fight.vitals_of(own_target)
        own_before = own_vitals.total
        height = {graph.DVA: 1, deflection.SIGMA: 0.65, deflection.GENJI: 0.55}[hero]
        cannon_angles = aim(fight._eye(player),
                            (target.feet[0], target.feet[1] + height, target.feet[2]))
        facing = aim(fight._eye(enemy), fight._eye(player))
        ammo = ball.instance(player, 0x1514).vars[53]
        for _ in range(35):
            run.step({player: (*cannon_angles, 1), enemy: (*facing, button)})
            self.assertTrue(state.active)
            if ammo.value() != 80:
                break
        self.assertEqual(ammo.value(), 79)
        self.assertEqual(vitals.total, before)
        self.assertFalse(player.client.messages(combat.HIT))
        if hero == deflection.SIGMA:
            self.assertEqual(defense.vars[21312].value(), 5)
        elif hero == deflection.GENJI:
            self.assertEqual(own_vitals.total, own_before - 5)
            self.assertTrue(enemy.client.messages(combat.HIT))
        else:
            self.assertEqual(own_vitals.total, own_before)

    @unittest.skipUnless(os.environ.get('ORPHEUS_BALL_MINE_BUNDLE'), 'prepared Minefield inputs required')
    def test_active_matrix_leaves_armed_mine_damage_and_impulse(self):
        from test_wrecking_ball_mines import MineTests

        run, player, enemy, _, vitals, matrix = self.mech_scene(30)
        mine = MineTests().isolated(run, player)
        self.assertTrue(mine.body_script.primary().states[28].active)
        for _ in range(8):
            run.step({enemy: (-32768, 0, 2)})
        x, y, z = mine.position
        relocate(run, enemy, (x, y, z + 0.5))
        run.step({enemy: (-32768, 0, 2)})
        self.assertTrue(matrix.state(3).active)
        self.assertTrue(mine.triggered)
        self.assertEqual(vitals.total, 475)
        self.assertGreater(enemy.mover.velocity[1], 4)
        self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), 0)
