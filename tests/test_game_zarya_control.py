"""Actual Flashbang inputs vs Zarya attacks and both barrier host services."""

import os
import unittest
from dataclasses import replace

import test_mccree_flashbang as flash
from test_game_combat import Run, aim, in_world

from ow174.game import accretion, combat, defenses, graviton, interception
from ow174.game.collision import World
from ow174.game.commands import Command
from ow174.game.content import SOLDIER, heroes
from ow174.game.script.driver import ULT_CHARGE, ULT_COST

ZARYA, MCCREE = 0x02E0000000000068, 0x02E0000000000042


class ZaryaControlTests(unittest.TestCase):
    step = flash.FlashbangTests.step

    def arena(self, *, projected=False, far_target=False, airburst=False):
        names = ("Flashbang", "Victim", "Zarya") if projected or far_target else ("Flashbang", "Zarya")
        run = Run(names=names)
        source, victim, *others = run.players
        caster = others[0] if projected else victim
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        self.assertTrue(run.match.switch_hero(source, heroes()[MCCREE]))
        self.assertTrue(run.match.switch_hero(caster, heroes()[ZARYA]))
        source.team = 0
        caster.team = victim.team = 1
        source.mover.position[:] = [0, 1, 0]
        victim.mover.position[:] = [2, 1, 7] if airburst else [0, 1, 3]
        if projected:
            caster.mover.position[:] = [4, 1, 3]
        elif far_target:
            others[0].team = source.team
            others[0].mover.position[:] = [0, 1, 9]
        for _ in range(20):
            self.step(run, source, victim)
        return run, source, victim, caster

    def test_flashbang_and_accretion_keep_impact_and_stun_ticks_across_a_gap(self):
        for hero, damage in ((MCCREE, 25), (accretion.HERO, 70)):
            with self.subTest(hero=hero):
                outcomes = []
                for mode in ("paced", "sparse", "late_barrier"):
                    run, source, victim, _ = self.arena(airburst=hero == MCCREE)
                    match, fight = run.match, run.match.combat
                    if hero != MCCREE:
                        self.assertTrue(match.switch_hero(source, heroes()[hero]))
                        source.mover.position[:] = (0, 1, 0)
                    before = self.health(run, victim)
                    for frame in range(100):
                        run.step({source: (0, 0, 16 if frame == 0 else 0)})
                        if fight.rockets:
                            break
                    rocket, = fight.rockets
                    self.assertEqual(rocket.flown, 0)
                    launch = run.frame
                    end = launch + 35
                    if mode == "late_barrier":
                        activation = launch + 25
                        match.tick = activation
                        victim.take_commands([Command(activation, buttons=8)])
                        self.assertIn((victim.body, False), fight.zarya_barriers.expiries)
                    for tick in range(launch + 1, end + 1) if mode == "paced" else (end,):
                        match.update(tick * combat.TICK, tick, frames=False)
                    self.assertFalse(fight.rockets)
                    self.assertEqual(before - self.health(run, victim), damage)
                    self.assertFalse(victim.body_script.component.entity_var(87).value())
                    deadline = fight.stuns[victim.body]
                    outcomes.append((deadline - launch, rocket.distance, victim.mover.velocity))
                    match.update((deadline - 1) * combat.TICK, deadline - 1, frames=False)
                    self.assertTrue(fight.is_stunned(victim))
                    match.update(deadline * combat.TICK, deadline, frames=False)
                    self.assertFalse(fight.is_stunned(victim))
                self.assertEqual(outcomes[1], outcomes[0])
                self.assertEqual(outcomes[2], outcomes[0])

    def test_off_axis_expiry_blast_stuns_only_unprotected_zarya(self):
        for protected in (False, True):
            with self.subTest(protected=protected):
                run, source, victim, _ = self.arena(airburst=True)
                if protected:
                    run.step({victim: (0, 0, 8)})
                fight = run.match.combat
                launched = False
                for frame in range(45):
                    run.step({source: (0, 0, 16 if frame == 0 else 0)})
                    launched |= bool(fight.rockets)
                self.assertTrue(launched)
                self.assertFalse(fight.rockets)
                self.assertEqual(self.health(run, victim), 400 if protected else 375)
                self.assertEqual(fight.is_stunned(victim), not protected)
                if protected:
                    energy = next(i for i in victim.body_script.component.instances.values()
                                  if i.graph.index == 0x0242)
                    self.assertEqual(victim.body_script.component.entity_var(87).value(), 5)
                    self.assertEqual(energy.vars[392].value().id, source.body)

    def defence_arena(self, hero, *, reflected=False):
        run = Run(names=("McCree", "Defender", "Zarya"))
        source, defender, target = run.players
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        for player, selected, team, position in (
            (source, heroes()[MCCREE], 0, (0, 1, 0)),
            (defender, hero, 1, (0, 1, 5)),
            (target, heroes()[ZARYA], int(not reflected), (2, 1, 0 if reflected else 6)),
        ):
            self.assertTrue(run.match.switch_hero(player, selected))
            player.team = team
            player.mover.position[:] = position
        return run, source, defender, target

    @unittest.skipUnless(
        os.environ.get("ORPHEUS_PROJECTILE_TAGS") and os.environ.get("ORPHEUS_GRAPH_SUPPLEMENT")
        and os.environ.get("ORPHEUS_MOVERS_SUPPLEMENT"),
        "requires projectile catalog and explicit mech inputs",
    )
    def test_absorbed_flashbang_never_stuns_behind_grasp_or_matrix(self):
        self.assertTrue(interception.eligible(0x040000000000037D))
        for mech in (False, True):
            for enabled in (False, True):
                with self.subTest(mech=mech, enabled=enabled):
                    hero = (replace(heroes()[0x02E000000000007A], body=0x0400000000000407)
                            if mech else heroes()[0x02E000000000023B])
                    run, source, defender, target = self.defence_arena(hero)
                    for _ in range(100 if mech else 20):
                        run.step({defender: (-32768, 0, 0)})
                    for frame in range(40):
                        buttons = (2 if mech else 8 if frame == 0 else 0) if enabled else 0
                        run.step({source: (0, 0, 16 if frame == 3 else 0),
                                  defender: (-32768, 0, buttons)})
                    self.assertEqual(self.health(run, target), 400 if enabled else 375)
                    self.assertEqual(run.match.combat.is_stunned(target), not enabled)
                    self.assertFalse(run.match.combat.rockets)
                    if enabled and not mech:
                        grasp = next(i for i in defender.body_script.component.instances.values()
                                     if i.graph.index == 0x1D02)
                        self.assertEqual(grasp.vars[21312].value(), 25)

    @unittest.skipUnless(os.environ.get("ORPHEUS_PROJECTILE_TAGS"), "requires projectile catalog")
    def test_reflected_flashbang_preserves_body_credit_into_personal_barrier(self):
        self.assertTrue(interception.eligible(0x040000000000037D))
        for protected in (False, True):
            with self.subTest(protected=protected):
                run, source, genji, target = self.defence_arena(heroes()[0x02E0000000000029], reflected=True)
                for _ in range(20):
                    run.step({genji: (-32768, 0, 0)})
                fight = run.match.combat
                reflected = False
                for frame in range(40):
                    run.step({source: (0, 0, 16 if frame == 3 else 0),
                              genji: (-32768, 0, 16 if frame == 0 else 0),
                              target: (0, 0, 8 if frame == 0 and protected else 0)})
                    reflected |= any(rocket.source_entity == genji.body for rocket in fight.rockets)
                self.assertTrue(reflected)
                self.assertEqual(self.health(run, source), 200)
                self.assertTrue(fight.is_stunned(source))
                self.assertEqual(self.health(run, target), 400 if protected else 375)
                self.assertEqual(fight.is_stunned(target), not protected)
                if protected:
                    energy = next(i for i in target.body_script.component.instances.values()
                                  if i.graph.index == 0x0242)
                    self.assertEqual(target.body_script.component.entity_var(87).value(), 5)
                    self.assertEqual(energy.vars[392].value().id, genji.body)

    @staticmethod
    def health(run, owner):
        fight = run.match.combat
        return fight.vitals_of(next(t for t in fight._targets() if t.owner is owner)).total

    @staticmethod
    def ability(caster, projected=False):
        return next(i for i in caster.body_script.component.instances.values()
                    if i.graph.index == (0x025C if projected else 0x0244))

    def throw(self, run, source, victim, *, buttons=0, until_stun=False):
        self.step(run, source, victim, cast=16, target_buttons=buttons)
        for _ in range(35):
            if until_stun and run.match.combat.is_stunned(victim):
                return
            self.step(run, source, victim, target_buttons=buttons)
        if until_stun:
            self.fail("Actual Flashbang did not stun the unprotected body")

    def test_personal_barrier_prevents_actual_flash_damage_and_stun(self):
        run, source, victim, caster = self.arena()
        run.step({caster: (-32768, 0, 8)})
        self.assertTrue(self.ability(caster).state(2).active)
        self.throw(run, source, victim, buttons=1)
        self.assertEqual(self.health(run, victim), 400)
        self.assertFalse(run.match.combat.is_stunned(victim))
        self.assertEqual(caster.body_script.component.entity_var(87).value(), 5)
        self.assertLess(self.health(run, source), 225)

    def test_projected_barrier_prevents_actual_flash_damage_and_stun_on_ally(self):
        run, source, victim, caster = self.arena(projected=True)
        run.step({caster: (-16384, 0, 16)})
        self.assertTrue(self.ability(caster, True).state(10).active)
        self.throw(run, source, victim)
        self.assertEqual(self.health(run, victim), 200)
        self.assertFalse(run.match.combat.is_stunned(victim))
        self.assertEqual(caster.body_script.component.entity_var(87).value(), 5)

    def test_flashbang_hits_bubble_edge_before_reaching_rear_enemy(self):
        for projected in (False, True):
            for breaking in (False, True):
                with self.subTest(projected=projected, breaking=breaking):
                    run, source, victim, caster = self.arena(projected=projected)
                    match = run.match
                    fight = match.combat
                    rear = match.add_player(4, "Rear", SOLDIER, victim.team, False)
                    in_world(match, rear)
                    run.players.append(rear)
                    self.assertTrue(match.switch_hero(rear, heroes()[SOLDIER]))
                    rear.mover.position[:] = [1, 1, 7]
                    rear.mover.body = None
                    source.mover.position[0] = 1
                    source.mover.body = None
                    if projected:
                        caster.mover.position[:] = [8, 1, 3]
                        caster.mover.body = None
                    angles = aim(fight._eye(caster), fight._eye(victim)) if projected else (0, 0)
                    run.step({caster: (*angles, 16 if projected else 8)})
                    barriers = fight.zarya_barriers
                    self.assertTrue(barriers.protects(victim))
                    pool = (barriers.projected if projected else barriers.active)[caster.body]
                    if breaking:
                        pool.remaining = 10
                    before = self.health(run, victim)
                    rear_before = self.health(run, rear)
                    rocket = None
                    for frame in range(45):
                        run.step({source: (0, 0, 16 if frame == 0 else 0)})
                        if fight.rockets:
                            rocket, = fight.rockets
                        elif rocket is not None:
                            break
                    self.assertIsNotNone(rocket)
                    self.assertNotIn(rocket, fight.rockets)
                    self.assertGreater(rocket.distance, 1)
                    self.assertLess(rocket.distance, 3)
                    self.assertEqual(pool.remaining, 0 if breaking else 175)
                    energy = caster.body_script.component.entity_var(87).value()
                    self.assertEqual(energy, 2 if breaking else 5)
                    self.assertEqual(self.health(run, victim), before)
                    self.assertEqual(self.health(run, rear), rear_before)
                    self.assertFalse(fight.is_stunned(victim))
                    self.assertFalse(fight.is_stunned(rear))
                    self.assertFalse(source.client.messages(combat.HIT))

    def test_projection_protects_ally_with_existing_flashbang_stun(self):
        run, source, victim, caster = self.arena(projected=True)
        self.throw(run, source, victim, until_stun=True)
        fight = run.match.combat
        deadline = fight.stuns[victim.body]
        self.assertEqual(self.health(run, victim), 175)

        run.step({caster: (-16384, 0, 16)})
        self.assertTrue(self.ability(caster, True).state(10).active)
        energy = caster.body_script.component.entity_var(87)
        for _ in range(40):
            run.step({source: (0, 0, 1)})
            if energy.value():
                break
        self.assertGreater(energy.value() or 0, 0)
        self.assertEqual(self.health(run, victim), 175)
        self.assertEqual(fight.stuns[victim.body], deadline)
        self.assertTrue(fight.is_stunned(victim))

        while fight.tick < deadline:
            run.step({})
        self.assertFalse(fight.is_stunned(victim))
        self.assertTrue(self.ability(caster, True).state(10).active)

    def test_flash_that_exhausts_either_barrier_still_cannot_stun_body(self):
        for projected in (False, True):
            with self.subTest(projected=projected):
                run, source, victim, caster = self.arena(projected=projected)
                run.step({caster: (-16384 if projected else -32768, 0, 16 if projected else 8)})
                fight = run.match.combat
                target = next(t for t in fight._targets() if t.owner is victim)
                # Capacity setup uses production damage; the final breaking
                # Flashbang comes from its real input/projectile/effect path.
                fight._damage(source, target, 190, False, None, run.frame)
                before = self.health(run, victim)
                self.throw(run, source, victim)
                self.assertEqual(self.health(run, victim), before)
                self.assertFalse(fight.is_stunned(victim))
                self.assertEqual(caster.body_script.component.entity_var(87).value(), 40)
                self.assertFalse(self.ability(caster, projected).state(10 if projected else 2).active)

    def test_flash_stops_primary_and_movement_then_new_press_recovers(self):
        run, source, victim, _ = self.arena()
        for _ in range(10):
            self.step(run, source, victim, target_buttons=1)
        self.assertLess(self.health(run, source), 225)
        self.throw(run, source, victim, buttons=1, until_stun=True)
        fight = run.match.combat
        self.assertEqual(self.health(run, victim), 375)
        end = fight.stuns[victim.body]
        before, position = self.health(run, source), tuple(victim.mover.position)
        while run.frame < end - 1:
            self.step(run, source, victim, target_buttons=1, forward=127)
            self.assertEqual(self.health(run, source), before)
            self.assertEqual(tuple(victim.mover.position), position)
        self.step(run, source, victim)
        for _ in range(25):
            self.step(run, source, victim, target_buttons=1, forward=127)
        self.assertLess(self.health(run, source), before)
        self.assertNotEqual(tuple(victim.mover.position), position)

    def swift_strike_into_flashbang(self, *, projected):
        run, source, victim, caster = self.arena(projected=projected)
        self.assertTrue(run.match.switch_hero(victim, heroes()[0x02E0000000000029]))
        victim.mover.position[:] = [0, 1, 5]
        if projected:
            caster.mover.position[:] = [4, 1, 5]
            caster.mover.body = None
            run.step({caster: (-16384, 0, 16)})
            self.assertTrue(self.ability(caster, True).state(10).active)
        fight = run.match.combat
        dash = next(i for i in victim.body_script.component.instances.values()
                    if i.graph.index == 0x01DB).state(1)
        self.step(run, source, victim, cast=16)
        for _ in range(30):
            if fight.rockets:
                break
            self.step(run, source, victim)
        self.assertTrue(fight.rockets)
        self.step(run, source, victim, target_buttons=8)
        self.assertTrue(dash.active)
        for _ in range(25):
            self.step(run, source, victim)
            if fight.is_stunned(victim):
                break
        return run, source, victim, caster, dash

    def test_actual_flashbang_interrupts_active_swift_strike(self):
        run, source, victim, _, dash = self.swift_strike_into_flashbang(projected=False)
        fight = run.match.combat
        self.assertTrue(fight.is_stunned(victim))
        self.assertEqual(self.health(run, victim), 175)
        self.assertFalse(dash.active)
        self.assertNotIn(victim.body, fight.genji_strikes)
        position = tuple(victim.mover.position)
        for _ in range(10):
            self.step(run, source, victim)
        self.assertEqual(tuple(victim.mover.position), position)

    def test_projection_prevents_flashbang_from_interrupting_swift_strike(self):
        run, _, victim, caster, dash = self.swift_strike_into_flashbang(projected=True)
        self.assertFalse(run.match.combat.is_stunned(victim))
        self.assertEqual(self.health(run, victim), 200)
        self.assertEqual(caster.body_script.component.entity_var(87).value(), 5)
        self.assertLess(victim.mover.position[2], 0)
        self.assertFalse(dash.active)

    def test_flashbang_preserves_dragonblade_but_blocks_swings_until_recovery(self):
        for draw_frames in (1, 80):
            with self.subTest(draw_frames=draw_frames):
                run, source, genji, zarya = self.arena(projected=True)
                self.assertTrue(run.match.switch_hero(genji, heroes()[0x02E0000000000029]))
                genji.mover.position[:] = [0, 1, 3]
                zarya.team = source.team
                zarya.mover.position[:] = [0, 1, -1]
                zarya.mover.body = None
                script = genji.body_script
                script.ult_seconds = 0
                script.set_ult_charge(script.component.entity_var(338).value())
                blade = next(i for i in script.component.instances.values()
                             if i.graph.index == 0x0317).state(3)
                for frame in range(draw_frames):
                    run.step({genji: (-32768, 0, 32 if frame == 0 else 0)})
                self.assertTrue(blade.flags & 1)
                self.throw(run, source, genji, until_stun=True)
                fight = run.match.combat
                deadline = fight.stuns[genji.body]
                self.assertTrue(blade.flags & 1)
                self.assertEqual(self.health(run, genji), 175)
                while run.frame < deadline - 1:
                    run.step({genji: (-32768, 0, 1 | 32)})
                    self.assertTrue(blade.flags & 1)
                    self.assertEqual(self.health(run, zarya), 400)
                for _ in range(20):
                    run.step({})
                for _ in range(65):
                    run.step({genji: (-32768, 0, 1)})
                    if self.health(run, zarya) < 400:
                        break
                self.assertEqual(self.health(run, zarya), 290)
                self.assertEqual(script.component.entity_var(342).value(), 0)
                self.assertTrue(blade.flags & 1)

    def test_late_flashbang_does_not_extend_dragonblade_or_restart_it(self):
        endings = []
        for flashbang in (False, True):
            with self.subTest(flashbang=flashbang):
                run, source, genji, _ = self.arena()
                self.assertTrue(run.match.switch_hero(genji, heroes()[0x02E0000000000029]))
                genji.mover.position[:] = [0, 1, 3]
                script = genji.body_script
                script.ult_seconds = 0
                script.set_ult_charge(script.component.entity_var(338).value())
                blade = next(i for i in script.component.instances.values()
                             if i.graph.index == 0x0317).state(3)
                for frame in range(470):
                    run.step({genji: (-32768, 0, 32 if frame == 0 else 0)})
                self.assertTrue(blade.flags & 1)
                for frame in range(160):
                    self.step(run, source, genji, cast=16 if flashbang and frame == 0 else 0)
                    if not blade.flags & 1:
                        endings.append(run.frame)
                        if flashbang:
                            self.assertTrue(run.match.combat.is_stunned(genji))
                        break
                else:
                    self.fail("Dragonblade did not expire")
                for _ in range(60):
                    run.step({genji: (-32768, 0, 32)})
                    self.assertFalse(blade.flags & 1)
        self.assertEqual(endings[0], endings[1])

    def test_death_and_body_replacement_retire_stunned_dragonblade(self):
        for replacement in (False, True):
            with self.subTest(replacement=replacement):
                run, source, genji, _ = self.arena()
                self.assertTrue(run.match.switch_hero(genji, heroes()[0x02E0000000000029]))
                genji.mover.position[:] = [0, 1, 3]
                script = genji.body_script
                script.ult_seconds = 0
                script.set_ult_charge(script.component.entity_var(338).value())
                blade = next(i for i in script.component.instances.values()
                             if i.graph.index == 0x0317).state(3)
                for frame in range(80):
                    run.step({genji: (-32768, 0, 32 if frame == 0 else 0)})
                fight = run.match.combat
                old_body = genji.body
                for _ in range(30):
                    run.step({genji: (-32768, 0, 1)})
                    if old_body in fight.genji_blades:
                        break
                self.assertIn(old_body, fight.genji_blades)
                if not replacement:
                    target = next(t for t in fight._targets() if t.owner is genji)
                    fight.vitals_of(target).damage(170)
                self.throw(run, source, genji, until_stun=True)
                self.assertTrue(blade.flags & 1)
                if replacement:
                    self.assertTrue(run.match.switch_hero(genji, genji.hero))
                    self.assertNotEqual(genji.body, old_body)
                    self.assertTrue(script.retired)
                    new_blade = next(i for i in genji.body_script.component.instances.values()
                                     if i.graph.index == 0x0317).state(3)
                    self.assertFalse(new_blade.flags & 1)
                else:
                    for _ in range(90):
                        self.step(run, source, genji, cast=1)
                        if fight.is_dead(genji):
                            break
                    self.assertTrue(fight.is_dead(genji))
                    self.assertFalse(blade.flags & 1)
                self.assertNotIn(old_body, fight.stuns)
                self.assertNotIn(old_body, fight.genji_blades)

    def sleep_dart(self, run, source, victim, *, miss=False, stop_at_launch=False):
        self.assertTrue(run.match.switch_hero(source, heroes()[0x02E000000000013B]))
        source.mover.position[:] = [0, 1, 0]
        x, y, z = victim.mover.position
        angles = (16384, 0) if miss else aim(run.match.combat._eye(source), (x, y + 1, z))
        run.step({source: (*angles, 8)})
        launched = bool(run.match.combat.rockets)
        for _ in range(45):
            if stop_at_launch and run.match.combat.rockets:
                rocket, = run.match.combat.rockets
                self.assertEqual(rocket.direct.effect, combat.SLEEP_DART_EFFECT)
                self.assertEqual(rocket.flown, 0)
                return rocket
            run.step({source: (*angles, 0)})
            launched |= bool(run.match.combat.rockets)
        self.assertTrue(launched)

    def test_sleep_dart_blocks_zarya_input_until_world_deadline(self):
        run, source, victim, _ = self.arena()
        self.sleep_dart(run, source, victim)
        fight = run.match.combat
        self.assertEqual(self.health(run, victim), 395)
        deadline = fight.sleeps[victim.body]
        self.assertTrue(fight.is_stunned(victim))
        script = victim.body_script
        script.ult_seconds = 0
        script.set_ult_charge(2100)
        position = tuple(victim.mover.position)
        run.frame += 1
        run.match.update(run.frame * combat.TICK, run.frame)
        victim.take_commands([Command(run.frame, forward=127, buttons=1 | 8 | 32)])
        self.assertEqual(tuple(victim.mover.position), position)
        self.assertFalse(self.ability(victim).state(2).active)
        self.assertEqual(script.component.entity_var(342).value(), 2100)
        self.assertFalse(fight.rockets)
        run.frame = deadline
        run.match.update(deadline * combat.TICK, deadline)
        self.assertFalse(fight.is_stunned(victim))
        self.assertNotIn(victim.body, fight.sleeps)
        run.step({victim: (0, 0, 8)})
        self.assertTrue(self.ability(victim).state(2).active)

    def test_sleep_dart_matches_paced_impact_and_wake_after_sparse_update(self):
        outcomes = []
        for sparse in (False, True):
            run, ana, victim, _ = self.arena()
            match, fight = run.match, run.match.combat
            rocket = self.sleep_dart(run, ana, victim, stop_at_launch=True)
            launch = run.frame
            end = launch + 100
            for tick in (end,) if sparse else range(launch + 1, end + 1):
                match.update(tick * combat.TICK, tick, frames=False)
            self.assertFalse(fight.rockets)
            self.assertEqual(self.health(run, victim), 395)
            deadline = fight.sleeps[victim.body]
            outcomes.append((deadline - combat.SLEEP_TICKS - launch, rocket.distance))
            match.update((deadline - 1) * combat.TICK, deadline - 1, frames=False)
            self.assertTrue(fight.is_stunned(victim))
            match.update(deadline * combat.TICK, deadline, frames=False)
            self.assertFalse(fight.is_stunned(victim))
        self.assertLess(outcomes[0][0], 10)
        self.assertEqual(outcomes[1], outcomes[0])

    def test_late_barrier_does_not_absorb_earlier_sleep_dart(self):
        for projected in (False, True):
            with self.subTest(projected=projected):
                run, ana, victim, caster = self.arena(projected=projected)
                match, fight = run.match, run.match.combat
                self.sleep_dart(run, ana, victim, stop_at_launch=True)
                activation = run.frame + 20
                match.tick = activation
                caster.take_commands([Command(activation, yaw=-16384 if projected else 0,
                                              buttons=16 if projected else 8)])
                self.assertIn((caster.body, projected), fight.zarya_barriers.expiries)
                before = self.health(run, victim)
                match.update(activation * combat.TICK, activation, frames=False)
                self.assertFalse(fight.rockets)
                self.assertEqual(before - self.health(run, victim), 5)
                self.assertLess(fight.sleeps[victim.body] - combat.SLEEP_TICKS, activation)
                self.assertFalse(caster.body_script.component.entity_var(87).value())

    def test_sleep_dart_checks_fortify_at_the_impact_tick(self):
        for offset in (1, 20):
            with self.subTest(offset=offset):
                run, ana, victim, _ = self.arena()
                match, fight = run.match, run.match.combat
                self.assertTrue(match.switch_hero(victim, heroes()[defenses.ORISA]))
                victim.mover.position[:] = (0, 1, 3)
                self.sleep_dart(run, ana, victim, stop_at_launch=True)
                activation = run.frame + offset
                match.tick = activation
                victim.take_commands([Command(activation, buttons=8)])
                self.assertIsNotNone(defenses.fortify_state(victim))
                end = activation + 20
                match.update(end * combat.TICK, end, frames=False)
                self.assertFalse(fight.rockets)
                self.assertEqual(fight.is_stunned(victim), offset == 20)

    def test_sleep_dart_interrupts_held_beam_without_catchup_damage(self):
        run, ana, zarya, _ = self.arena()
        self.assertTrue(run.match.switch_hero(ana, heroes()[0x02E000000000013B]))
        ana.mover.position[:] = [0, 1, 0]
        fight = run.match.combat
        x, y, z = zarya.mover.position
        angles = aim(fight._eye(ana), (x, y + 1, z))
        held_beam = {zarya: (-32768, 0, 1)}
        weapon = next(i for i in zarya.body_script.component.instances.values()
                      if i.graph.index == 0x0241)
        beam = weapon.state(10)
        ammo = weapon.vars[53]

        initial_health = self.health(run, ana)
        for _ in range(12):
            run.step(held_beam)
        self.assertAlmostEqual(initial_health - self.health(run, ana), 13.2)
        self.assertTrue(beam.active)
        activation = beam.counter
        for frame in range(60):
            run.step({**held_beam, ana: (*angles, 8 if frame == 0 else 0)})
            if fight.is_stunned(zarya):
                break
        self.assertTrue(fight.is_stunned(zarya))
        self.assertEqual(self.health(run, zarya), 395)
        self.assertFalse(beam.active)

        deadline = fight.sleeps[zarya.body]
        health_at_sleep = self.health(run, ana)
        ammo_at_sleep = ammo.value()
        while run.frame < deadline - 1:
            run.step(held_beam)
            self.assertTrue(fight.is_stunned(zarya))
            self.assertFalse(beam.active)
            self.assertEqual(ammo.value(), ammo_at_sleep)
            self.assertEqual(self.health(run, ana), health_at_sleep)

        run.step(held_beam)
        self.assertFalse(fight.is_stunned(zarya))
        self.assertTrue(beam.active)
        self.assertEqual(beam.counter, activation + 1)
        self.assertAlmostEqual(ammo_at_sleep - ammo.value(), 0.3, places=4)
        self.assertEqual(self.health(run, ana), health_at_sleep)
        run.step(held_beam)
        self.assertAlmostEqual(health_at_sleep - self.health(run, ana), 1.2)
        self.assertAlmostEqual(ammo_at_sleep - ammo.value(), 0.62, places=4)

    def test_sleep_dart_preserves_inflight_secondary_and_blocks_new_shots(self):
        run, ana, zarya, _ = self.arena(far_target=True)
        target = run.players[2]
        self.assertTrue(run.match.switch_hero(ana, heroes()[0x02E000000000013B]))
        ana.mover.position[:] = [0, 1, 0]
        fight = run.match.combat
        x, y, z = zarya.mover.position
        angles = aim(fight._eye(ana), (x, y + 1, z))
        held_secondary = {zarya: (0, 0, 2)}
        weapon = next(i for i in zarya.body_script.component.instances.values()
                      if i.graph.index == 0x0241)
        ammo = weapon.vars[53]

        run.step({ana: (*angles, 8)})
        for _ in range(18):
            run.step({ana: (*angles, 0)})
        self.assertFalse(fight.is_stunned(zarya))
        run.step(held_secondary)
        first, = (r for r in fight.rockets if r.shooter is zarya)
        self.assertEqual(ammo.value(), 75)
        for _ in range(20):
            run.step(held_secondary)
            if fight.is_stunned(zarya):
                break
        self.assertTrue(fight.is_stunned(zarya))
        self.assertEqual(self.health(run, zarya), 395)
        self.assertIn(first, fight.rockets)
        self.assertEqual(self.health(run, target), 200)

        deadline = fight.sleeps[zarya.body]
        while run.frame < deadline - 1:
            run.step(held_secondary)
            self.assertTrue(fight.is_stunned(zarya))
            self.assertEqual(ammo.value(), 75)
            self.assertTrue(all(r is first for r in fight.rockets if r.shooter is zarya))
        self.assertNotIn(first, fight.rockets)
        self.assertAlmostEqual(self.health(run, target), 153)

        run.step(held_secondary)
        self.assertFalse(fight.is_stunned(zarya))
        self.assertEqual(ammo.value(), 50)
        second, = (r for r in fight.rockets if r.shooter is zarya)
        self.assertIsNot(second, first)
        for _ in range(40):
            run.step({})
        self.assertNotIn(second, fight.rockets)
        self.assertAlmostEqual(self.health(run, target), 106)
        self.assertEqual(ammo.value(), 50)

    def test_sleep_dart_defers_held_graviton_until_wake_without_spending_charge(self):
        run, ana, zarya, _ = self.arena(far_target=True)
        target = run.players[2]
        fight = run.match.combat
        gravitons = fight.zarya_gravitons
        script = zarya.body_script
        script.ult_seconds = 0
        cost = script.component.entity_var(ULT_COST).value()
        self.assertEqual(cost, 2100)
        script.set_ult_charge(cost)
        charge = script.component.entity_var(ULT_CHARGE)
        held_ultimate = {zarya: (0, 0, 0x20)}

        self.sleep_dart(run, ana, zarya)
        self.assertTrue(fight.is_stunned(zarya))
        deadline = fight.sleeps[zarya.body]
        while run.frame < deadline - 1:
            run.step(held_ultimate)
            self.assertTrue(fight.is_stunned(zarya))
            self.assertEqual(charge.value(), cost)
            self.assertFalse(fight.rockets)
            self.assertFalse(gravitons.fields)
        self.assertEqual(self.health(run, target), 200)

        run.step(held_ultimate)
        self.assertEqual(run.frame, deadline)
        self.assertFalse(fight.is_stunned(zarya))
        self.assertEqual(charge.value(), 0)
        rocket, = fight.rockets
        self.assertIs(rocket.shooter, zarya)
        self.assertEqual(rocket.spawn, graviton.FIELD_ENTITY)
        for _ in range(90):
            run.step(held_ultimate)
            if gravitons.fields:
                break
            self.assertEqual(fight.rockets, [rocket])
        field, = gravitons.fields
        self.assertFalse(fight.rockets)
        self.assertEqual(field.end_tick - run.frame, 250)
        self.assertEqual(self.health(run, target), 195)
        self.assertIsNotNone(gravitons.movement_for(target))

        while run.frame < field.end_tick - 1:
            run.step(held_ultimate)
            self.assertEqual(gravitons.fields, [field])
            self.assertFalse(fight.rockets)
        run.step(held_ultimate)
        self.assertFalse(gravitons.fields)
        self.assertIsNone(gravitons.movement_for(target))
        for _ in range(30):
            run.step(held_ultimate)
        self.assertAlmostEqual(self.health(run, target), 175)
        self.assertEqual(charge.value(), 0)
        self.assertFalse(fight.rockets)
        self.assertFalse(gravitons.fields)

    def test_sleep_dart_discards_zarya_melee_press_until_fresh_input(self):
        run, source, victim, _ = self.arena()
        self.sleep_dart(run, source, victim)
        fight = run.match.combat
        self.assertTrue(fight.is_stunned(victim))
        deadline = fight.sleeps[victim.body]
        # Move Ana into melee range using commands so the rewind history matches her pose.
        for _ in range(18):
            run.frame += 1
            run.match.update(run.frame * combat.TICK, run.frame)
            source.take_commands([Command(run.frame, forward=127)])
            victim.take_commands([Command(run.frame, yaw=-32768)])

        melee = next(i for i in victim.body_script.component.instances.values()
                     if i.graph.index == 0x0043)
        before = self.health(run, source)
        self.assertTrue(fight.is_stunned(victim))
        run.step({victim: (-32768, 0, 0x800)})
        self.assertEqual(melee.state(4).counter, 0)
        while run.frame < deadline:
            run.step({victim: (-32768, 0, 0)})
        self.assertFalse(fight.is_stunned(victim))
        self.assertEqual(melee.state(4).counter, 0)
        self.assertEqual(self.health(run, source), before)
        self.assertFalse(victim.client.messages(combat.HIT))

        run.step({victim: (-32768, 0, 0x800)})
        self.assertEqual(melee.state(4).counter, 1)
        self.assertAlmostEqual(before - self.health(run, source), 30)
        self.assertEqual(len(victim.client.messages(combat.HIT)), 1)

    def test_personal_and_projected_barriers_absorb_sleep_dart(self):
        for projected in (False, True):
            with self.subTest(projected=projected):
                run, source, victim, caster = self.arena(projected=projected)
                run.step({caster: (-16384 if projected else 0, 0, 16 if projected else 8)})
                before = self.health(run, victim)
                self.sleep_dart(run, source, victim)
                self.assertEqual(self.health(run, victim), before)
                self.assertFalse(run.match.combat.is_stunned(victim))
                self.assertEqual(caster.body_script.component.entity_var(87).value(), 1)

    def test_projection_protects_ally_until_expiry_while_caster_sleeps(self):
        run, ana, ally, caster = self.arena(projected=True)
        fight = run.match.combat
        barriers = fight.zarya_barriers
        projection = {caster: (-16384, 0, 16)}
        run.step(projection)
        expiry = run.frame + 124
        pet = self.ability(caster, True).state(10)
        self.assertTrue(pet.active)
        self.assertTrue(barriers.protects(ally))
        pool = barriers.projected[caster.body]
        activation = pet.counter
        self.assertEqual(pool.remaining, 200)

        self.sleep_dart(run, ana, caster)
        self.assertTrue(fight.is_stunned(caster))
        deadline = fight.sleeps[caster.body]
        self.assertEqual(self.health(run, caster), 395)
        self.assertEqual(self.health(run, ally), 200)
        x, y, z = ally.mover.position
        angles = aim(fight._eye(ana), (x, y + 1, z))
        rifle = {**projection, ana: (*angles, 1)}
        energy = caster.body_script.component.entity_var(87)
        for _ in range(40):
            run.step(rifle)
            if pool.remaining < 200:
                break
        self.assertIs(barriers.projected[caster.body], pool)
        self.assertEqual(pool.remaining, 130)
        self.assertEqual(energy.value(), 14)
        self.assertEqual(self.health(run, ally), 200)
        self.assertEqual(fight.sleeps[caster.body], deadline)

        while run.frame < expiry - 1:
            run.step(projection)
            self.assertTrue(pet.active)
        self.assertTrue(barriers.protects(ally))
        run.step(projection)
        self.assertFalse(pet.active)
        self.assertFalse(barriers.protects(ally))
        self.assertNotIn(caster.body, barriers.projected)

        for _ in range(40):
            run.step(rifle)
            if self.health(run, ally) < 200:
                break
        self.assertLess(self.health(run, ally), 200)
        for _ in range(50):
            run.step(projection)
        self.assertAlmostEqual(self.health(run, ally), 130)
        self.assertEqual(pet.counter, activation)
        self.assertEqual(fight.sleeps[caster.body], deadline)
        self.assertTrue(fight.is_stunned(caster))
        self.assertGreater(energy.value(), 0)
        self.assertLess(energy.value(), 14)
        decay_gate = caster.body_script.component.entity_var(232, create=False)
        self.assertFalse(decay_gate.value() if decay_gate is not None else None)

    def test_projected_barrier_absorbs_rifle_without_waking_sleeping_ally(self):
        run, ana, victim, caster = self.arena(projected=True)
        self.sleep_dart(run, ana, victim)
        fight = run.match.combat
        before = self.health(run, victim)
        deadline = fight.sleeps[victim.body]
        self.assertEqual(before, 195)
        self.assertTrue(fight.is_stunned(victim))

        run.step({caster: (-16384, 0, 16)})
        self.assertTrue(self.ability(caster, True).state(10).active)
        x, y, z = victim.mover.position
        angles = aim(fight._eye(ana), (x, y + 1, z))
        for _ in range(85):
            run.step({ana: (*angles, 1)})
            if caster.body_script.component.entity_var(87).value():
                break
        self.assertAlmostEqual(caster.body_script.component.entity_var(87).value() or 0, 14)
        self.assertEqual(fight.zarya_barriers.projected[caster.body].remaining, 130)
        self.assertEqual(self.health(run, victim), before)
        self.assertEqual(fight.sleeps[victim.body], deadline)
        self.assertTrue(fight.is_stunned(victim))

    def test_graviton_wakes_sleeping_ally_only_after_projected_barrier_breaks(self):
        run, ana, victim, projector = self.arena(projected=True)
        match = run.match
        fight = match.combat
        source = match.add_player(4, "Graviton caster", ZARYA, ana.team, False)
        in_world(match, source)
        run.players.append(source)
        self.assertTrue(match.switch_hero(source, heroes()[ZARYA]))
        source.mover.position[:] = [0, 1, -3]
        source.mover.body = None
        self.sleep_dart(run, ana, victim)
        deadline = fight.sleeps[victim.body]
        before = self.health(run, victim)
        self.assertEqual(before, 195)

        run.step({projector: (-16384, 0, 16)})
        barriers = fight.zarya_barriers
        self.assertTrue(barriers.protects(victim))
        pool = barriers.projected[projector.body]
        pool.remaining = 5.12
        source.body_script.ult_seconds = 0
        source.body_script.set_ult_charge(2100)
        run.step({source: (0, 0, 0x20)})
        for _ in range(60):
            if fight.zarya_gravitons.fields:
                break
            run.step({})
        field, = fight.zarya_gravitons.fields
        self.assertIs(field.shooter, source)
        for remaining in (0.12, 0.04, 0):
            self.assertAlmostEqual(pool.remaining, remaining, places=4)
            self.assertEqual(self.health(run, victim), before)
            self.assertEqual(fight.sleeps[victim.body], deadline)
            self.assertIsNotNone(fight.zarya_gravitons.movement_for(victim))
            run.step({})

        self.assertLess(run.frame, deadline)
        self.assertNotIn(victim.body, fight.sleeps)
        self.assertAlmostEqual(self.health(run, victim), before - 0.08, places=4)
        self.assertAlmostEqual(projector.body_script.component.entity_var(87).value(), 1.024, places=4)
        self.assertEqual(source.body_script.component.entity_var(ULT_CHARGE).value(), 0)
        while run.frame < field.end_tick:
            run.step({})
        self.assertAlmostEqual(self.health(run, victim), before - 19.84, places=4)
        self.assertFalse(fight.zarya_gravitons.fields)
        self.assertIsNone(fight.zarya_gravitons.movement_for(victim))

    def test_later_actual_rifle_damage_wakes_sleeping_zarya(self):
        run, source, victim, _ = self.arena(far_target=True)
        rifle = run.players[2]
        self.sleep_dart(run, source, victim)
        fight = run.match.combat
        deadline = fight.sleeps[victim.body]
        x, y, z = victim.mover.position
        angles = aim(fight._eye(rifle), (x, y + 1, z))
        for _ in range(20):
            run.step({rifle: (*angles, 1)})
            if victim.body not in fight.sleeps:
                break
        self.assertLess(run.frame, deadline)
        self.assertEqual(self.health(run, victim), 375)
        self.assertFalse(fight.is_stunned(victim))

    def test_known_sleep_resolves_earlier_wake_before_graviton_input(self):
        for timing in ("earlier", "same", "later", "expiry"):
            with self.subTest(timing=timing):
                run, ana, victim, _ = self.arena(far_target=True)
                match, fight = run.match, run.match.combat
                waker = run.players[2]
                self.assertTrue(match.switch_hero(waker, heroes()[ZARYA]))
                waker.mover.position[:] = (0, 1, 4.5)
                waker.mover.body = None
                self.sleep_dart(run, ana, victim)
                self.assertTrue(fight.is_stunned(victim))
                self.assertEqual(self.health(run, victim), 395)
                start = run.frame
                script = victim.body_script
                script.ult_seconds = 0
                script.set_ult_charge(2100)
                charge = script.component.entity_var(ULT_CHARGE)
                admitted = timing in ("earlier", "expiry")
                cast = fight.sleeps[victim.body] if timing == "expiry" else start + 25
                inputs = [(cast, victim, Command(cast, yaw=-32768, buttons=32))]
                if timing != "expiry":
                    wake = cast + {"earlier": -10, "same": 0, "later": 10}[timing]
                    inputs.insert(0, (wake, waker, Command(wake, yaw=-32768, buttons=0x800)))
                for at, owner, command in sorted(inputs, key=lambda event: event[0]):
                    match.tick = at
                    owner.take_commands([command])
                self.assertEqual(charge.value(), 0 if admitted else 2100)
                self.assertEqual(len(fight.rockets), int(admitted))
                if admitted:
                    rocket, = fight.rockets
                    self.assertIs(rocket.shooter, victim)
                    self.assertEqual(rocket.spawn, graviton.FIELD_ENTITY)
                    self.assertEqual(rocket.last_tick, cast)
                end = cast + 12
                match.update(end * combat.TICK, end, frames=False)
                self.assertFalse(fight.is_stunned(victim))
                self.assertEqual(self.health(run, victim), 400 if timing == "expiry" else 365)
                self.assertEqual(bool(fight.zarya_gravitons.fields), admitted)

    def test_sleep_dart_rejects_wall_ally_and_aimed_miss(self):
        for case in ("wall", "ally", "miss"):
            with self.subTest(case=case):
                run, source, victim, _ = self.arena()
                if case == "wall":
                    run.match.collision_world = World([
                        (-10, 0, 1.5, 10, 10, 1.5, 10, 0, 1.5),
                        (-10, 0, 1.5, -10, 10, 1.5, 10, 10, 1.5),
                        (-10, 1, -10, -10, 1, 10, 10, 1, 10),
                        (-10, 1, -10, 10, 1, 10, 10, 1, -10),
                    ])
                elif case == "ally":
                    victim.team = source.team
                self.sleep_dart(run, source, victim, miss=case == "miss")
                self.assertEqual(self.health(run, victim), 400)
                self.assertNotIn(victim.body, run.match.combat.sleeps)

    def test_stunned_body_cannot_start_barriers_or_spend_ultimate_charge(self):
        run, source, victim, _ = self.arena()
        self.throw(run, source, victim, until_stun=True)
        victim.body_script.ult_seconds = 0
        victim.body_script.set_ult_charge(2100)
        end = run.match.combat.stuns[victim.body]
        while run.frame < end - 1:
            self.step(run, source, victim, target_buttons=8 | 16 | 0x20)
            self.assertFalse(self.ability(victim).state(2).active)
            self.assertFalse(self.ability(victim, True).state(10).active)
            self.assertEqual(victim.body_script.component.entity_var(342).value(), 2100)
            self.assertFalse(any(r.shooter is victim for r in run.match.combat.rockets))
        self.step(run, source, victim)
        self.step(run, source, victim, target_buttons=8)
        self.assertTrue(self.ability(victim).state(2).active)

    def test_secondary_already_in_flight_survives_owner_stun(self):
        run, source, victim, _ = self.arena(far_target=True)
        far = run.players[2]
        run.step({source: (0, 0, 16), victim: (0, 0, 2)})
        for _ in range(35):
            if run.match.combat.is_stunned(victim):
                break
            self.step(run, source, victim)
        self.assertTrue(run.match.combat.is_stunned(victim))
        self.assertEqual(self.health(run, far), 200)
        self.assertTrue(any(r.shooter is victim for r in run.match.combat.rockets))
        for _ in range(35):
            self.step(run, source, victim)
        self.assertAlmostEqual(self.health(run, far), 153)

    def test_deployed_graviton_continues_after_owner_stun(self):
        run, source, victim, _ = self.arena(far_target=True)
        far = run.players[2]
        victim.body_script.set_ult_charge(2100)
        run.step({victim: (0, 0, 0x20)})
        for _ in range(40):
            if run.match.combat.zarya_gravitons.fields:
                break
            self.step(run, source, victim)
        self.assertTrue(run.match.combat.zarya_gravitons.fields)
        self.throw(run, source, victim, until_stun=True)
        before = self.health(run, far)
        self.assertTrue(run.match.combat.zarya_gravitons.fields)
        for _ in range(30):
            self.step(run, source, victim)
        self.assertLess(self.health(run, far), before)


if __name__ == "__main__":
    unittest.main()
