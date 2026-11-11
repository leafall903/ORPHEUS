"""Actual enemy Flashbang against Roadhog's running skills; control reconstructed."""

import os
import unittest
from dataclasses import replace
from unittest.mock import patch

import test_game_roadhog_defenses as phases
import test_mccree_flashbang as flashbang_tests
from test_game_combat import KANEZAKA_DM, Run, aim

from ow174.game import body_vm_snapshot, combat, sombra
from ow174.game.collision import World
from ow174.game.commands import Command
from ow174.game.content import heroes
from ow174.game.script import nodes
from ow174.game.script.driver import ULT_CHARGE, ULT_COST

ROADHOG = 0x02E0000000000040
MCCREE = 0x02E0000000000042


class RoadhogStunTests(unittest.TestCase):
    step = flashbang_tests.FlashbangTests.step
    cast = flashbang_tests.FlashbangTests.cast

    def session(self):
        run, caster, hog = flashbang_tests.FlashbangTests().arena(distance=3)
        position = tuple(hog.mover.position)
        run.match.switch_hero(hog, heroes()[ROADHOG])
        hog.mover.position[:] = position
        # Retain the hostile caster through a full channel without retail-HP claims.
        run.match.combat.vitals[caster.body] = combat.Vitals(caster.body, 20000)
        targets = {t.owner: t for t in run.match.combat._targets()}
        return run, caster, hog, targets

    def ability(self, hog, index):
        return next(s for i in hog.body_script.component.instances.values()
                    if i.graph.index == index for s in i.states.values()
                    if isinstance(s, nodes.Ability))

    def test_earthshatter_input_releases_authored_shockwave_after_windup(self):
        fixture = phases.RoadhogDefenseTests()
        run, hog, reinhardt, _ = fixture.session("Reinhardt", distance=6)
        script = reinhardt.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        ultimate, = script.ultimates
        instance = ultimate.instance
        wave = instance.state(instance.graph.node(1).state)
        fixture.step(run, hog, reinhardt, cast=32)
        self.assertTrue(ultimate.flags & nodes.Ability.RUNNING)
        self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)
        for _ in range(20):
            fixture.step(run, hog, reinhardt)
            self.assertEqual(wave.counter, 0)
        for _ in range(20):
            fixture.step(run, hog, reinhardt)
            if wave.counter:
                break
        self.assertEqual(wave.counter, 1)
        self.assertTrue(wave.active)
        before = body_vm_snapshot.export_body_vm(script)
        self.assertEqual(body_vm_snapshot.export_body_vm(script), before)

    def test_roadhog_hook_during_earthshatter_windup_prevents_release(self):
        fixture = phases.RoadhogDefenseTests()
        run, hog, reinhardt, _ = fixture.session("Reinhardt", distance=3)
        script = reinhardt.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        ultimate, = script.ultimates
        wave = ultimate.instance.state(ultimate.instance.graph.node(1).state)
        hooks = run.match.combat.hooks
        fixture.step(run, hog, reinhardt, fire=8, cast=32)
        caught = False
        for _ in range(65):
            fixture.step(run, hog, reinhardt)
            caught |= hooks.controlled(reinhardt.body)
            self.assertEqual(wave.counter, 0)
        self.assertTrue(caught)
        self.assertFalse(ultimate.flags & nodes.Ability.RUNNING)
        self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)

    def test_earthshatter_wave_travels_before_interrupting_whole_hog_once(self):
        fixture = phases.RoadhogDefenseTests()
        run, hog, reinhardt, _ = fixture.session("Reinhardt", distance=8)
        fight = run.match.combat
        for player in (hog, reinhardt):
            script = player.body_script
            script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        attack, = hog.body_script.ultimates
        cast, = reinhardt.body_script.ultimates
        wave = cast.instance.state(cast.instance.graph.node(1).state)
        fixture.step(run, hog, reinhardt, fire=32, cast=32)
        for _ in range(60):
            if wave.active:
                break
            fixture.step(run, hog, reinhardt)
        self.assertTrue(wave.active)
        self.assertEqual(fixture.health(run, hog), 600)
        self.assertTrue(attack.flags & nodes.Ability.RUNNING)
        for elapsed in range(1, 35):
            fixture.step(run, hog, reinhardt)
            if fight.is_stunned(hog):
                self.assertGreater(elapsed, 8)
                self.assertLess(elapsed, 16)
                break
        self.assertTrue(fight.is_stunned(hog))
        self.assertEqual(fight.stuns[hog.body] - run.frame, 157)
        self.assertEqual(fixture.health(run, hog), 550)
        self.assertFalse(attack.flags & nodes.Ability.RUNNING)
        self.assertEqual(hog.body_script.component.entity_var(ULT_CHARGE).value(), 0)
        for _ in range(45):
            fixture.step(run, hog, reinhardt)
        self.assertFalse(wave.active)
        self.assertEqual(fixture.health(run, hog), 550)

    def test_earthshatter_keeps_travelling_after_source_dies_or_is_hooked(self):
        for lethal in (False, True):
            with self.subTest(lethal=lethal):
                fixture = phases.RoadhogDefenseTests()
                run, hog, reinhardt, support = fixture.session("Reinhardt", distance=18)
                fight = run.match.combat
                run.match.switch_hero(support, hog.hero)
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                hog.team = support.team = 0
                reinhardt.team = 1
                x, y, z = reinhardt.mover.position
                support.mover.position[:] = (x+2, y, z)
                support.mover.body = None
                for _ in range(40):
                    run.step({})
                script = reinhardt.body_script
                script.set_ult_charge(script.component.entity_var(ULT_COST).value())
                cast, = script.ultimates
                wave = cast.instance.state(cast.instance.graph.node(1).state)
                fixture.step(run, hog, reinhardt, cast=32)
                for _ in range(55):
                    if wave.active:
                        break
                    fixture.step(run, hog, reinhardt)
                self.assertTrue(wave.active)
                self.assertEqual(fixture.health(run, hog), 600)
                if lethal:
                    target = next(t for t in fight._targets() if t.owner is reinhardt)
                    vitals = fight.vitals_of(target)
                    vitals.damage(vitals.total - 50)
                    fight._sync_body_health(reinhardt, vitals)
                angles = aim(fight._eye(support), (x, y+1, z))
                run.step({support: (*angles, 1 if lethal else 8)})
                interrupted = False
                for _ in range(45):
                    run.step({support: (*angles, 0)})
                    if fight.is_stunned(hog):
                        break
                    interrupted |= (fight.is_dead(reinhardt) if lethal
                                    else fight.hooks.controlled(reinhardt.body))
                self.assertTrue(interrupted)
                self.assertTrue(fight.is_stunned(hog))
                self.assertEqual(fixture.health(run, hog), 550)

    def test_lethal_primary_before_earthshatter_release_prevents_wave(self):
        fixture = phases.RoadhogDefenseTests()
        run, hog, reinhardt, _ = fixture.session("Reinhardt", distance=2)
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is reinhardt)
        vitals = fight.vitals_of(target)
        vitals.damage(vitals.total - 50)
        fight._sync_body_health(reinhardt, vitals)
        script = reinhardt.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        cast, = script.ultimates
        wave = cast.instance.state(cast.instance.graph.node(1).state)
        fixture.step(run, hog, reinhardt, cast=32)
        self.assertTrue(cast.flags & nodes.Ability.RUNNING)
        fixture.step(run, hog, reinhardt, fire=1)
        for _ in range(75):
            fixture.step(run, hog, reinhardt)
            self.assertEqual(wave.counter, 0)
        self.assertTrue(fight.is_dead(reinhardt))
        self.assertFalse(fight.is_stunned(hog))
        self.assertEqual(fixture.health(run, hog), 600)

    def test_earthshatter_world_clock_matches_gap_without_owner_commands(self):
        outcomes = []
        for gap in (False, True):
            fixture = phases.RoadhogDefenseTests()
            run, hog, reinhardt, _ = fixture.session("Reinhardt", distance=12)
            fight = run.match.combat
            script = reinhardt.body_script
            script.set_ult_charge(script.component.entity_var(ULT_COST).value())
            cast, = script.ultimates
            wave = cast.instance.state(cast.instance.graph.node(1).state)
            fixture.step(run, hog, reinhardt, cast=32)
            for _ in range(55):
                if wave.active:
                    break
                fixture.step(run, hog, reinhardt)
            self.assertTrue(wave.active)
            frame = run.frame
            source_frame = script.component.frame
            for offset in ((34,) if gap else range(1, 35)):
                run.match.update((frame+offset) * combat.TICK, frame+offset)
            self.assertEqual(script.component.frame, source_frame)
            self.assertFalse(wave.active)
            self.assertEqual(fixture.health(run, hog), 550)
            outcomes.append(fight.stuns[hog.body]-frame)
        self.assertEqual(outcomes[0], outcomes[1])

    def test_earthshatter_close_impact_and_wave_do_not_double_hit(self):
        fixture = phases.RoadhogDefenseTests()
        run, hog, reinhardt, rear = fixture.session("Reinhardt", distance=3)
        x, y, z = hog.mover.position
        rear.mover.position[:] = (x, y, z-5)
        rear.mover.body = None
        script = reinhardt.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        cast, = script.ultimates
        wave = cast.instance.state(cast.instance.graph.node(1).state)
        fight = run.match.combat
        fixture.step(run, hog, reinhardt, cast=32)
        for _ in range(50):
            if wave.active:
                break
            fixture.step(run, hog, reinhardt)
        self.assertTrue(wave.active)
        self.assertEqual(fixture.health(run, hog), 350)
        self.assertTrue(fight.is_stunned(hog))
        self.assertEqual(fixture.health(run, rear), 20000)
        for _ in range(42):
            fixture.step(run, hog, reinhardt)
        self.assertEqual(fixture.health(run, hog), 350)
        self.assertEqual(fixture.health(run, rear), 19950)
        self.assertFalse(wave.active)
        close = cast.instance.state(cast.instance.graph.node(114).state)
        self.assertFalse(close.active)

    def test_allied_reinhardt_barrier_blocks_incoming_earthshatter(self):
        fixture = phases.RoadhogDefenseTests()
        run, hog, reinhardt, defender = fixture.session("Reinhardt", distance=8)
        fight = run.match.combat
        self.assertIsNotNone(fight.barrier_geometry)
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        hog.team = defender.team = 0
        reinhardt.team = 1
        run.match.switch_hero(defender, reinhardt.hero)
        x, y, z = hog.mover.position
        defender.mover.position[:] = (x, y, z+2)
        defender.mover.body = None
        for _ in range(35):
            run.step({})
        for _ in range(20):
            run.step({defender: (0, 0, 2)})
        self.assertTrue(fight.barriers[defender.body].active)
        script = reinhardt.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        for offset in range(85):
            run.step({reinhardt: (-32768, 0, 32 if not offset else 0), defender: (0, 0, 2)})
            self.assertFalse(fight.is_stunned(hog))
        self.assertEqual(fixture.health(run, hog), 600)

    def test_earthshatter_wave_respects_range_facing_team_and_wall(self):
        for case in ("range", "behind", "ally", "wall"):
            with self.subTest(case=case):
                fixture = phases.RoadhogDefenseTests()
                run, hog, reinhardt, rear = fixture.session(
                    "Reinhardt", distance=22 if case == "range" else 8,
                )
                rear.mover.position[0] = 30
                rear.mover.body = None
                fight = run.match.combat
                if case == "ally":
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    reinhardt.team = hog.team
                x, y, z = hog.mover.position
                wall_z = z + 4
                wall = World([
                    (x-5, y-2, wall_z, x+5, y-2, wall_z, x+5, y+5, wall_z),
                    (x-5, y-2, wall_z, x+5, y+5, wall_z, x-5, y+5, wall_z),
                ])
                script = reinhardt.body_script
                script.set_ult_charge(script.component.entity_var(ULT_COST).value())
                cast, = script.ultimates
                wave = cast.instance.state(cast.instance.graph.node(1).state)
                with patch.object(fight, "_collision",
                                  return_value=wall if case == "wall" else fight._collision()):
                    for offset in range(85):
                        run.step({reinhardt: (0 if case == "behind" else -32768, 0, 32 if not offset else 0)})
                        self.assertFalse(fight.is_stunned(hog))
                self.assertEqual(wave.counter, 1)
                self.assertFalse(wave.active)
                self.assertEqual(fixture.health(run, hog), 600)

    def test_sigma_accretion_direct_hit_interrupts_breather_without_splash_stun(self):
        fixture = phases.RoadhogDefenseTests()
        run, hog, sigma, nearby = fixture.session("Sigma", distance=8)
        fight = run.match.combat
        x, y, z = hog.mover.position
        nearby.mover.position[:] = (x+1.1, y, z)
        nearby.mover.body = None
        for _ in range(20):
            fixture.step(run, hog, sigma)
        fixture.step(run, hog, sigma, fire=16, cast=16)
        self.assertEqual(fight.incoming_damage_scale(hog), 0.5)
        for _ in range(100):
            fixture.step(run, hog, sigma)
            if fight.is_stunned(hog):
                break
        self.assertTrue(fight.is_stunned(hog))
        deadline = fight.stuns[hog.body]
        self.assertEqual(deadline - run.frame, 50)
        self.assertFalse(fight.is_stunned(nearby))
        self.assertLess(fixture.health(run, nearby), 20000)
        self.assertLess(fixture.health(run, hog), 600)
        breather = self.ability(hog, 0x0240)
        self.assertFalse(breather.flags & nodes.Ability.RUNNING)
        self.assertGreater(breather.remaining(), 0)
        self.assertEqual(fight.incoming_damage_scale(hog), 1)
        while run.frame < deadline - 1:
            fixture.step(run, hog, sigma, fire=8|16)
            self.assertFalse(hog.body_script.component.held)
        fixture.step(run, hog, sigma)
        self.assertFalse(fight.is_stunned(hog))
        self.assertNotIn(hog.body, fight.hooks.flights)

    def test_sigma_accretion_cast_respects_wall_team_and_projected_barrier(self):
        for case in ("wall", "ally", "projected", "miss"):
            with self.subTest(case=case):
                fixture = phases.RoadhogDefenseTests()
                run, hog, sigma, support = fixture.session("Sigma", distance=8)
                fight = run.match.combat
                x, y, z = hog.mover.position
                bubble = None
                if case in ("ally", "projected"):
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    hog.team = support.team = 0
                    sigma.team = 0 if case == "ally" else 1
                if case == "projected":
                    run.match.switch_hero(support, heroes()[0x02E0000000000068])
                    support.mover.position[:] = (x-4, y, z)
                    support.mover.body = None
                    for _ in range(8):
                        run.step({})
                    angles = aim(fight._eye(support), (x, y+1, z))
                    run.step({support: (*angles, 0)})
                    run.step({support: (*angles, 16)})
                    bubble = fight.zarya_bubbles.for_target(hog)
                    self.assertIsNotNone(bubble)
                    self.assertEqual(bubble.health, 200)
                wall = World([
                    (x-5, y-2, z+4, x+5, y-2, z+4, x+5, y+6, z+4),
                    (x-5, y-2, z+4, x+5, y+6, z+4, x-5, y+6, z+4),
                ])
                fired = False
                with patch.object(fight, "_collision",
                                  return_value=wall if case == "wall" else fight._collision()):
                    for offset in range(90):
                        yaw = 0 if case == "miss" else -32768
                        run.step({sigma: (yaw, 0, 16 if offset == 0 else 0)})
                        fired |= any(r.definition == 0x04000000000018B7 for r in fight.rockets)
                        self.assertFalse(fight.is_stunned(hog))
                self.assertTrue(fired)
                self.assertEqual(fixture.health(run, hog), 600)
                if bubble is not None:
                    self.assertLess(bubble.health, 200)

    def test_sigma_accretion_stops_whole_hog_without_refund(self):
        fixture = phases.RoadhogDefenseTests()
        run, hog, sigma, _ = fixture.session("Sigma", distance=8)
        fight = run.match.combat
        script = hog.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        fixture.step(run, hog, sigma, fire=32, cast=16)
        ultimate, = script.ultimates
        self.assertTrue(ultimate.flags & nodes.Ability.RUNNING)
        flew = False
        for _ in range(100):
            fixture.step(run, hog, sigma)
            for rocket in fight.rockets:
                if rocket.definition == 0x04000000000018B7:
                    self.assertEqual(rocket.direct.stun_ms, 800)
                    self.assertEqual(rocket.blast.stun_ms, 0)
                    flew = True
            if fight.is_stunned(hog):
                break
        self.assertTrue(flew)
        self.assertTrue(fight.is_stunned(hog))
        self.assertFalse(ultimate.flags & nodes.Ability.RUNNING)
        self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)
        stopped = fixture.health(run, sigma)
        self.assertLess(stopped, 20000)
        for _ in range(70):
            fixture.step(run, hog, sigma)
        self.assertEqual(fixture.health(run, sigma), stopped)

    def test_flashbang_discards_buffered_melee_on_stun_or_death(self):
        for lethal in (False, True):
            with self.subTest(lethal=lethal):
                run, caster, hog, targets = self.session()
                fight = run.match.combat
                x, y, z = caster.mover.position
                hx, _, hz = hog.mover.position
                distance = ((hx-x)**2 + (hz-z)**2)**0.5
                close = (x + (hx-x)*1.5/distance, y, z + (hz-z)*1.5/distance)
                hog.mover.position[:] = close
                hog.mover.body = None
                self.step(run, caster, hog)
                pool = fight.vitals_of(targets[caster])
                self.step(run, caster, hog, target_buttons=0x800)
                self.assertEqual(pool.total, 19970)
                self.step(run, caster, hog)
                self.step(run, caster, hog, target_buttons=0x800)
                self.assertEqual(pool.total, 19970)
                melee = self.ability(hog, 0x0043)
                self.assertTrue(melee.presses)
                if lethal:
                    own = fight.vitals_of(targets[hog])
                    own.damage(own.total - 25)
                    fight._sync_body_health(hog, own)
                self.step(run, caster, hog, cast=16, target_buttons=0x800)
                for _ in range(45):
                    if fight.is_dead(hog) or fight.is_stunned(hog):
                        break
                    self.step(run, caster, hog, target_buttons=0x800)
                self.assertEqual(fight.is_dead(hog), lethal)
                self.assertEqual(fight.is_stunned(hog), not lethal)
                if not lethal:
                    self.assertFalse(melee.presses)
                for _ in range(105):
                    self.step(run, caster, hog)
                    self.assertEqual(pool.total, 19970)
                self.step(run, caster, hog, target_buttons=0x800)
                self.assertEqual(pool.total, 19970 if lethal else 19940)
                if lethal:
                    retired = hog.body_script
                    deadline = next(when for when, player in fight.respawns if player is hog)
                    while run.frame * combat.TICK < deadline + combat.TICK and fight.is_dead(hog):
                        self.step(run, caster, hog)
                    self.assertFalse(fight.is_dead(hog))
                    self.assertIsNot(hog.body_script, retired)
                    hog.mover.position[:] = close
                    hog.mover.body = None
                    for _ in range(5):
                        self.step(run, caster, hog)
                    self.assertEqual(pool.total, 19970)
                    self.step(run, caster, hog, target_buttons=0x800)
                    self.assertEqual(pool.total, 19940)

    def test_sleep_clears_buffered_melee_but_sombra_preserves_it(self):
        for hero, button in (("Ana", 8), ("Sombra", 2), ("Sombra", 32)):
            with self.subTest(hero=hero, button=button):
                self.check_melee_buffer_under_control(hero, button)

    def check_melee_buffer_under_control(self, hero, button):
        fixture = phases.RoadhogDefenseTests()
        run, hog, caster, victim = fixture.session(hero, distance=4)
        match = run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        hog.team, caster.team, victim.team = 0, 1, 1
        x, y, z = hog.mover.position
        victim.mover.position[:] = (x, y, z - 1.5)
        victim.mover.body = None
        look = aim(fight._eye(caster), (x, y + 1, z))
        melee_aim = (-32768, 0)
        for _ in range(35):
            run.step({caster: (*look, 0)})
        script = hog.body_script
        weapon = next(i for i in script.component.instances.values() if i.graph.index == 0x01C6)
        ammo = weapon.vars[53].value()
        if button == 32:
            caster.body_script.set_ult_charge(caster.body_script.component.entity_var(ULT_COST).value())
        run.step({hog: (*melee_aim, 0x800), caster: (*look, 0)})
        first = run.frame
        self.assertEqual(fixture.health(run, victim), 19970)
        run.step({hog: (*melee_aim, 0), caster: (*look, 0)})
        run.step({hog: (*melee_aim, 0x800), caster: (*look, 0)})
        melee = self.ability(hog, 0x0043)
        self.assertTrue(melee.presses)
        self.assertEqual(fixture.health(run, victim), 19970)

        sleeping = hero == "Ana"
        for offset in range(3, 61):
            cast = button if offset == 3 or button == 2 else 0
            run.step({hog: (*melee_aim, 0x800), caster: (*look, cast)})
            if (fight.is_stunned(hog) if sleeping else sombra.locked(hog)):
                break
        self.assertLess(run.frame, first + 62)
        self.assertEqual(fixture.health(run, victim), 19970)
        if sleeping:
            self.assertIn(hog.body, fight.sleep)
            self.assertFalse(melee.presses)
            deadline = fight.sleep[hog.body]
            while run.frame < deadline - 1:
                run.step({hog: (*melee_aim, 0x800)})
                self.assertTrue(fight.is_stunned(hog))
                self.assertFalse(melee.presses)
                self.assertEqual(fixture.health(run, victim), 19970)
            run.step({hog: (*melee_aim, 0)})
            self.assertEqual(run.frame, deadline)
            self.assertFalse(fight.is_stunned(hog))
            for _ in range(65):
                run.step({hog: (*melee_aim, 0)})
                self.assertEqual(fixture.health(run, victim), 19970)
            run.step({hog: (*melee_aim, 0x800)})
        else:
            self.assertTrue(sombra.locked(hog))
            self.assertTrue(melee.presses)
            while run.frame < first + 61:
                run.step({hog: (*melee_aim, 0x800)})
                self.assertEqual(fixture.health(run, victim), 19970)
            run.step({hog: (*melee_aim, 0x800)})
            self.assertEqual(run.frame, first + 62)
            self.assertTrue(sombra.locked(hog))
            self.assertFalse(fight.is_stunned(hog))
        self.assertEqual(fixture.health(run, victim), 19940)
        self.assertEqual(weapon.vars[53].value(), ammo)

    def test_hook_accretion_and_earthshatter_discard_buffered_melee(self):
        for hero, cast in (("Roadhog", 8), ("Sigma", 16), ("Reinhardt", 32)):
            with self.subTest(hero=hero):
                self.check_hard_control_discards_melee(hero, cast)

    @unittest.skipUnless(os.environ.get("ORPHEUS_BRIGITTE_COLLISION"), "actual Brigitte shield input required")
    def test_shield_bash_discards_buffered_melee(self):
        self.check_hard_control_discards_melee("Brigitte", 3, ready=2)

    def check_hard_control_discards_melee(self, hero, cast, *, ready=0):
        fixture = phases.RoadhogDefenseTests()
        run, hog, caster, victim = fixture.session(hero, distance=4)
        match = run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        hog.team, caster.team, victim.team = 0, 1, 1
        x, y, z = hog.mover.position
        victim.mover.position[:] = (x, y, z - 1.5)
        victim.mover.body = None
        aim_at_hog = aim(fight._eye(caster), (x, y + 1, z))
        melee_aim = (-32768, 0)
        for _ in range(35):
            run.step({caster: (*aim_at_hog, ready)})
        if cast == 32:
            script = caster.body_script
            script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        weapon = next(i for i in hog.body_script.component.instances.values() if i.graph.index == 0x01C6)
        ammo = weapon.vars[53].value()

        for buttons in (0x800, 0, 0x800):
            run.step({hog: (*melee_aim, buttons), caster: (*aim_at_hog, ready)})
        melee = self.ability(hog, 0x0043)
        self.assertTrue(melee.presses)
        self.assertEqual(fixture.health(run, victim), 19970)

        def controlled():
            return fight.hooks.controlled(hog.body) if hero == "Roadhog" else fight.is_stunned(hog)

        for offset in range(59):
            run.step({hog: (*melee_aim, 0x800), caster: (*aim_at_hog, cast if offset == 0 else ready)})
            if controlled():
                break
        self.assertTrue(controlled())
        self.assertFalse(melee.presses)
        for _ in range(6):
            run.step({hog: (*melee_aim, 0x800)})
            self.assertTrue(controlled())
            self.assertFalse(melee.presses)
            self.assertEqual(fixture.health(run, victim), 19970)
        for _ in range(220):
            run.step({hog: (*melee_aim, 0)})
            if not controlled():
                break
        self.assertFalse(controlled())
        for _ in range(65):
            run.step({hog: (*melee_aim, 0)})
        x, y, z = hog.mover.position
        victim.mover.position[:] = (x, y, z - 1.5)
        victim.mover.body = None
        for _ in range(6):
            run.step({hog: (*melee_aim, 0)})
        self.assertEqual(fixture.health(run, victim), 19970)
        self.assertFalse(melee.presses)
        run.step({hog: (*melee_aim, 0x800)})
        self.assertEqual(fixture.health(run, victim), 19940)
        self.assertEqual(weapon.vars[53].value(), ammo)

    def test_flashbang_interrupts_whole_hog_without_refund_or_delayed_restart(self):
        run, caster, hog, targets = self.session()
        script = hog.body_script
        script.ult_seconds = 0
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        self.step(run, caster, hog, target_buttons=32)
        for _ in range(80):
            self.step(run, caster, hog)
        ultimate, = script.ultimates
        self.assertTrue(ultimate.flags & nodes.Ability.RUNNING)
        pool = run.match.combat.vitals_of(targets[caster])
        self.assertLess(pool.total, 20000)
        self.cast(run, caster, hog)
        self.assertFalse(ultimate.flags & nodes.Ability.RUNNING)
        self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)
        stopped = pool.total
        deadline = run.match.combat.stuns[hog.body]
        position = tuple(hog.mover.position)
        own = run.match.combat.vitals_of(targets[hog]).total
        while run.frame < deadline - 1:
            self.step(run, caster, hog, target_buttons=32 | 1 | 2 | 8 | 16 | 0x800, forward=127)
            self.assertEqual(tuple(hog.mover.position), position)
            self.assertEqual(pool.total, stopped)
            self.assertEqual(run.match.combat.vitals_of(targets[hog]).total, own)
            self.assertFalse(script.component.held)
            self.assertFalse(self.ability(hog, 0x0240).flags & nodes.Ability.RUNNING)
        for _ in range(30):
            self.step(run, caster, hog)
        self.assertEqual(pool.total, stopped)
        self.assertFalse(ultimate.flags & nodes.Ability.RUNNING)
        self.step(run, caster, hog, target_buttons=1)
        for _ in range(20):
            self.step(run, caster, hog)
        self.assertLess(pool.total, stopped)

    def test_late_input_from_sleep_or_flashbang_cannot_cast_after_expiry(self):
        for mode in ("Sleep Dart", "Flashbang"):
            with self.subTest(mode=mode):
                self.check_late_input_at_control_expiry(mode)

    def test_late_input_from_hack_or_emp_keeps_gun_input_without_casting(self):
        for mode in ("Hack", "EMP"):
            with self.subTest(mode=mode):
                self.check_late_input_at_control_expiry(mode)

    def late_input_control_scene(self, mode):
        if mode == "Sleep Dart":
            from test_game_roadhog_ana import RoadhogAnaTests

            case = RoadhogAnaTests()
            run, hog, caster = case.session()
            case.land(run, hog, caster, 8)
        elif mode == "Flashbang":
            run, caster, hog, _ = self.session()
            self.cast(run, caster, hog)
        else:
            from test_game_roadhog_sombra import RoadhogSombraTests

            case = RoadhogSombraTests()
            run, hog, caster = case.session()
            if mode == "EMP":
                script = caster.body_script
                script.set_ult_charge(script.component.entity_var(ULT_COST).value())
            case.land(run, hog, caster, 32 if mode == "EMP" else 2)
        return run, hog

    def check_late_input_at_control_expiry(self, mode):
        ability_lock = mode in ("Hack", "EMP")

        def locked(run, hog):
            return sombra.locked(hog) if ability_lock else run.match.combat.is_stunned(hog)

        run, hog = self.late_input_control_scene(mode)
        applied = run.frame
        self.assertTrue(locked(run, hog))
        for _ in range(400):
            if not locked(run, hog):
                break
            run.step({})
        self.assertFalse(locked(run, hog))
        duration = run.frame - applied

        run, hog = self.late_input_control_scene(mode)
        expiry = run.frame + duration
        script = hog.body_script
        charge = script.component.entity_var(ULT_CHARGE)
        cost = script.component.entity_var(ULT_COST).value()
        script.set_ult_charge(cost)
        ultimate, = script.ultimates
        weapon = next(i for i in script.component.instances.values() if i.graph.index == 0x1C6)
        rounds = weapon.vars[53].value()
        while run.frame < expiry - 9:
            run.step({})
        self.assertTrue(locked(run, hog))
        anchor = hog.command_clock
        with patch.object(hog, "take_commands"):
            while run.frame < expiry + 4:
                run.step({})
        self.assertEqual(hog.command_clock, anchor)
        self.assertLess(script.frame, expiry - 1)
        pose = tuple(hog.mover.position)
        delayed = Command(expiry - 1, buttons=32 | 1, forward=127)
        hog.take_commands([delayed, Command(run.frame)])

        self.assertFalse(locked(run, hog))
        self.assertFalse(ultimate.flags & nodes.Ability.RUNNING)
        self.assertEqual(charge.value(), cost)
        self.assertEqual(weapon.vars[53].value(), rounds - int(ability_lock))
        self.assertEqual(tuple(hog.mover.position) != pose, ability_lock)
        run.step({hog: (0, 0, 32)})
        self.assertTrue(ultimate.flags & nodes.Ability.RUNNING)
        self.assertEqual(charge.value(), 0)

    def test_flashbang_around_whole_hog_refill_preserves_only_committed_ammo(self):
        for impact_offset in (373, 374, 375):
            with self.subTest(impact_offset=impact_offset):
                self.check_whole_hog_refill_interruption(impact_offset)

    def check_whole_hog_refill_interruption(self, impact_offset):
        run, caster, hog, targets = self.session()
        fight = run.match.combat
        script = hog.body_script
        script.ult_seconds = 0
        ultimate, = script.ultimates
        weapon = next(i for i in script.component.instances.values() if i.graph.index == 0x01C6)
        ammo = weapon.vars[53]
        caster_pool = fight.vitals_of(targets[caster])
        hog_pool = fight.vitals_of(targets[hog])
        x, y, z = caster.mover.position
        fire_aim = aim(fight._eye(hog), (x, y + .7, z))
        x, y, z = hog.mover.position
        flash_aim = aim(fight._eye(caster), (x, y + 1, z))
        run.step({hog: (*fire_aim, 2)})
        for _ in range(65):
            run.step({})
        self.assertEqual(ammo.value(), 4)
        self.assertEqual(caster_pool.total, 19950)
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        run.step({hog: (16384, 0, 32)})
        self.assertTrue(ultimate.flags & nodes.Ability.RUNNING)
        self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)
        start = run.frame
        for elapsed in range(1, 458):
            # At this range Flashbang reaches Roadhog five ticks after the actual press.
            run.step({
                caster: (*flash_aim, 16 if elapsed == impact_offset - 5 else 0),
                hog: (16384, 0, 2 | 0x400 if impact_offset <= elapsed < impact_offset + 49 else 0),
            })
            self.assertEqual(bool(ultimate.flags & nodes.Ability.RUNNING), elapsed < impact_offset)
            self.assertEqual(ammo.value(), 5 if impact_offset > 374 and elapsed >= 374 else 4)
            self.assertEqual(hog_pool.total, 575 if elapsed >= impact_offset else 600)
            self.assertEqual(caster_pool.total, 19950)
            self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)
            if elapsed == impact_offset:
                self.assertEqual(fight.stuns[hog.body], start + impact_offset + 50)
            if impact_offset <= elapsed < impact_offset + 49:
                self.assertFalse(script.component.held)
        self.assertFalse(fight.is_stunned(hog))
        before = ammo.value()
        run.step({hog: (*fire_aim, 2)})
        for _ in range(25):
            run.step({})
        self.assertEqual(ammo.value(), before - 1)
        self.assertEqual(caster_pool.total, 19900)

    def test_flashbang_interrupts_breather_heal_and_reduction_keeps_cooldown(self):
        run, caster, hog, targets = self.session()
        fight = run.match.combat
        pool = fight.vitals_of(targets[hog])
        pool.damage(400)
        fight._sync_body_health(hog, pool)
        self.step(run, caster, hog, target_buttons=16)
        for _ in range(50):
            self.step(run, caster, hog)
        self.assertGreater(pool.total, 200)
        self.assertEqual(fight.incoming_damage_scale(hog), 0.5)
        self.cast(run, caster, hog)
        stopped = pool.total
        ability = self.ability(hog, 0x0240)
        self.assertFalse(ability.flags & nodes.Ability.RUNNING)
        self.assertGreater(ability.remaining(), 0)
        self.assertEqual(fight.incoming_damage_scale(hog), 1)
        heal = next(s for i in hog.body_script.component.instances.values()
                    if i.graph.index == 0x0240 for s in i.states.values() if s.node.pos == 18)
        self.assertEqual(heal.counter, 1)
        for _ in range(150):
            self.step(run, caster, hog, target_buttons=16)
        self.assertEqual(pool.total, stopped)
        self.assertEqual(heal.counter, 1)
        while ability.remaining() > 0:
            self.step(run, caster, hog)
        self.step(run, caster, hog, target_buttons=16)
        for _ in range(180):
            self.step(run, caster, hog)
        self.assertEqual(heal.counter, 2)
        self.assertGreater(pool.total, stopped)

    def hook_scene(self):
        run = Run(KANEZAKA_DM, ("Roadhog", "McCree", "Victim"))
        match = run.match
        match.bots.clear()
        match._pending_bot_scripts.clear()
        hog, caster, victim = run.players
        match.switch_hero(hog, heroes()[ROADHOG])
        match.switch_hero(caster, heroes()[MCCREE])
        x, y, z = hog.mover.position
        caster.mover.position[:] = (x + 2.5, y, z)
        victim.mover.position[:] = (x, y, z + 18)
        return run, hog, caster, victim

    def hook_step(self, run, hog, caster, victim, *, hook=False, cast=0):
        run.frame += 1
        match = run.match
        match.update(run.frame * combat.TICK, run.frame)
        for player in run.players:
            player.client.queue_entities(match.remote_movements(player))
        hog.take_commands([Command(run.frame, buttons=8 if hook else 0)])
        yaw, pitch = aim(match.combat._eye(caster), match.combat._eye(hog))
        caster.take_commands([Command(run.frame, yaw=yaw, pitch=pitch, buttons=cast)])
        victim.take_commands([Command(run.frame)])

    def test_accretion_and_earthshatter_keep_the_later_control_deadline(self):
        for accretion_first in (False, True):
            with self.subTest(accretion_first=accretion_first):
                self.check_accretion_earthshatter_overlap(accretion_first)

    def check_accretion_earthshatter_overlap(self, accretion_first):
        fixture = phases.RoadhogDefenseTests()
        run, hog, reinhardt, sigma = fixture.session("Reinhardt", distance=8)
        match = run.match
        fight = match.combat
        self.assertTrue(match.switch_hero(sigma, heroes()[0x02E000000000023B]))
        match.game_map = replace(match.game_map, free_for_all=False)
        hog.team, reinhardt.team, sigma.team = 0, 1, 1
        x, y, z = hog.mover.position
        sigma.mover.position[:] = (x + 8, y, z + 1)
        sigma.mover.body = None
        script = reinhardt.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        rein_aim = aim(fight._eye(reinhardt), fight._eye(hog))
        sigma_aim = aim(fight._eye(sigma), fight._eye(hog))
        for _ in range(12):
            run.step({reinhardt: (*rein_aim, 0), sigma: (*sigma_aim, 0)})

        observed = []
        apply_effect = fight._hit_effect

        def record_effect(target, effect, *, stun_ms=0):
            before = fight.control_deadline(target.entity)
            result = apply_effect(target, effect, stun_ms=stun_ms)
            if target.entity == hog.body and stun_ms:
                observed.append((stun_ms, fight.tick, before, fight.control_deadline(target.entity)))
            return result

        with patch.object(fight, "_hit_effect", side_effect=record_effect):
            for offset in range(120):
                run.step({
                    reinhardt: (*rein_aim, 32 if offset == (25 if accretion_first else 0) else 0),
                    sigma: (*sigma_aim, 16 if offset == (0 if accretion_first else 25) else 0),
                })
                if len(observed) == 2:
                    break

        self.assertEqual([entry[0] for entry in observed], [800, 2500] if accretion_first else [2500, 800])
        first, second = observed
        self.assertLess(second[1], first[3])
        self.assertEqual(second[2], first[3])
        first_frames, second_frames = (50, 157) if accretion_first else (157, 50)
        deadline = max(first[1] + first_frames, second[1] + second_frames)
        self.assertEqual(second[3], deadline)
        self.assertLess(fixture.health(run, hog), 550)
        while run.frame < deadline - 1:
            run.step({hog: (0, 0, 8 | 16)})
            self.assertFalse(hog.body_script.component.held)
            self.assertNotIn(hog.body, fight.hooks.flights)
        run.step({hog: (0, 0, 0)})
        self.assertFalse(fight.is_stunned(hog))
        self.assertNotIn(hog.body, fight.hooks.flights)
        run.step({hog: (0, 0, 8)})
        for _ in range(40):
            run.step({})
            if hog.body in fight.hooks.flights:
                break
        self.assertIn(hog.body, fight.hooks.flights)

    def test_accretion_cancels_hook_flight_and_active_pull(self):
        for hook_at, caught in ((25, False), (10, True)):
            with self.subTest(caught=caught):
                self.check_hook_control_interruption(hook_at, caught, earthshatter=False)

    def test_earthshatter_cancels_hook_flight_and_active_pull(self):
        for hook_at, caught in ((25, False), (10, True)):
            with self.subTest(caught=caught):
                self.check_hook_control_interruption(hook_at, caught, earthshatter=True)

    def check_hook_control_interruption(self, hook_at, caught, *, earthshatter):
        run, hog, caster, victim = self.hook_scene()
        hero = 0x02E0000000000007 if earthshatter else 0x02E000000000023B
        self.assertTrue(run.match.switch_hero(caster, heroes()[hero]))
        x, y, z = hog.mover.position
        caster.mover.position[:] = (x + (8 if earthshatter else 2.5), y, z)
        caster.mover.body = None
        fight = run.match.combat
        for _ in range(10):
            self.hook_step(run, hog, caster, victim)
        if earthshatter:
            script = caster.body_script
            script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        button = 32 if earthshatter else 16
        control_frames = 157 if earthshatter else 50
        victim_pool = fight.vitals_of(next(t for t in fight._targets() if t.owner is victim))
        before = victim_pool.total

        for offset in range(100):
            flight = fight.hooks.flights.get(hog.body)
            tethered = fight.hooks.controlled(victim.body)
            cast = button if offset == 0 else 0
            self.hook_step(run, hog, caster, victim, hook=offset == hook_at, cast=cast)
            if fight.is_stunned(hog):
                break
        self.assertTrue(fight.is_stunned(hog))
        self.assertIsNotNone(flight)
        self.assertIsNone(flight.arrived_at)
        self.assertEqual(tethered, caught)
        self.assertEqual(flight.target is not None, caught)
        self.assertEqual(fight.stuns[hog.body] - run.frame, control_frames)
        self.assertNotIn(hog.body, fight.hooks.flights)
        self.assertFalse(fight.hooks.controlled(victim.body))
        self.assertEqual(victim_pool.total, before - (30 if caught else 0))

        position = tuple(victim.mover.position)
        for _ in range(control_frames + 20):
            self.hook_step(run, hog, caster, victim)
        self.assertEqual(tuple(victim.mover.position), position)
        self.assertEqual(victim_pool.total, before - (30 if caught else 0))
        self.assertFalse(fight.is_stunned(hog))
        self.assertGreater(self.ability(hog, 0x01C8).remaining(), 0)

    def test_flashbang_cancels_hook_flight_and_active_pull(self):
        for caught in (False, True):
            with self.subTest(caught=caught):
                run, hog, caster, victim = self.hook_scene()
                fight = run.match.combat
                self.hook_step(run, hog, caster, victim, hook=True)
                for _ in range(60):
                    self.hook_step(run, hog, caster, victim)
                    if (fight.hooks.controlled(victim.body) if caught else hog.body in fight.hooks.flights):
                        break
                self.assertIn(hog.body, fight.hooks.flights)
                self.assertEqual(fight.hooks.controlled(victim.body), caught)
                observed = []
                apply_effect = fight._hit_effect

                def record_effect(target, effect, fight=fight, hog=hog,
                                  observed=observed, apply_effect=apply_effect):
                    if target.entity == hog.body and effect == combat.FLASHBANG_EFFECT:
                        flight = fight.hooks.flights.get(hog.body)
                        observed.append(None if flight is None else
                                        (flight.target is not None, flight.arrived_at))
                    return apply_effect(target, effect)

                with patch.object(fight, "_hit_effect", side_effect=record_effect):
                    self.hook_step(run, hog, caster, victim, cast=16)
                    for _ in range(40):
                        self.hook_step(run, hog, caster, victim)
                        if fight.is_stunned(hog):
                            break
                self.assertEqual(observed, [(caught, None)])
                self.assertTrue(fight.is_stunned(hog))
                self.assertNotIn(hog.body, fight.hooks.flights)
                self.assertFalse(fight.hooks.controlled(victim.body))
                position = tuple(victim.mover.position)
                for _ in range(80):
                    self.hook_step(run, hog, caster, victim)
                self.assertEqual(tuple(victim.mover.position), position)
                self.assertGreater(self.ability(hog, 0x01C8).remaining(), 0)


if __name__ == "__main__":
    unittest.main()
