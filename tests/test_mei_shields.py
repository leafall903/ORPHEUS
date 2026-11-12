"""Real Mei inputs against resource-backed Zarya shield reconstruction."""

import sys
import unittest
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_mei_blizzard as blizzard
import test_delivered_mei_combat as fixtures
import test_mei_control as control
import test_mei_frost as frost
import test_mei_wall as wall
from test_game_combat import KANEZAKA_DM, Run

from ow174.game import barriers, combat, defenses, mei, world
from ow174.game.content import heroes


class ShieldTests(unittest.TestCase):
    def scene(self):
        run = Run(KANEZAKA_DM, ('Mei', 'Zarya', 'Receiver'))
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        owner, zarya, receiver = run.players
        run.match.switch_hero(owner, heroes()[fixtures.MEI])
        run.match.switch_hero(zarya, heroes()[defenses.ZARYA])
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        owner.team, zarya.team, receiver.team = 0, 1, 1
        x, y, z = owner.mover.position
        run.match.combat.walls.place_player(zarya, (x + 3., y, z + 5.))
        run.match.combat.walls.place_player(receiver, (x, y, z + 5.))
        for _ in range(30):
            run.step({})
        return run, owner, zarya, receiver

    def target(self, run, actor):
        return next(t for t in run.match.combat._targets() if t.owner is actor)

    def cast(self, run, zarya, target=None):
        if target is None:
            aim = (0, 0)
        else:
            point = run.match.combat._eye(target) if hasattr(target, 'mover') else (
                target.position[0], target.position[1] + combat.EYE, target.position[2],
            )
            aim = fixtures.aim(run.match.combat._eye(zarya), point)
        run.step({zarya: (*aim, 8 if target is None else 16)})
        run.step({zarya: (*aim, 0)})
        bubble = run.match.combat.shields.shielded(zarya if target is None else target)
        self.assertIsNotNone(bubble)
        self.assertEqual(bubble.health, 200.0)
        return bubble

    def test_projected_barrier_and_cryo_keep_independent_protection(self):
        for cryo_first in (False, True):
            with self.subTest(cryo_first=cryo_first):
                run, owner, zarya, receiver = self.scene()
                self.assertTrue(run.match.switch_hero(receiver, heroes()[fixtures.MEI]))
                fight = run.match.combat
                for _ in range(30):
                    run.step({})
                if cryo_first:
                    run.step({receiver: (0, 0, 8)})
                bubble = self.cast(run, zarya, receiver)
                if not cryo_first:
                    run.step({receiver: (0, 0, 8)})
                self.assertTrue(mei.protected(receiver))
                target = self.target(run, receiver)
                vitals = fight.vitals_of(target)
                attacks = fixtures.MeiCombatTests()
                attacks.fire(run, owner, target, 2, .9)
                self.assertEqual(bubble.health, 125.)
                self.assertEqual(vitals.total, 250.)
                hits = owner.client.messages(combat.HIT)
                self.assertEqual(len(hits), 1)
                self.assertEqual(hits[0]['+0x78']['+0x0']['+0x0'], bubble.entity)

                if cryo_first:
                    run.step({receiver: (0, 0, 2)})
                    self.assertFalse(mei.protected(receiver))
                    self.assertIs(fight.shields.shielded(receiver), bubble)
                    attacks.fire(run, owner, target, 2, .9)
                    self.assertEqual(bubble.health, 50.)
                    self.assertEqual(len(owner.client.messages(combat.HIT)), 2)
                    for _ in range(45):
                        run.step({})
                    hit_count = 2
                else:
                    for _ in range(90):
                        run.step({})
                    self.assertIsNone(fight.shields.shielded(receiver))
                    self.assertTrue(mei.protected(receiver))
                    attacks.fire(run, owner, target, 2, .9)
                    self.assertEqual(len(owner.client.messages(combat.HIT)), 1)
                    run.step({receiver: (0, 0, 2)})
                    hit_count = 1
                self.assertIsNone(fight.shields.shielded(receiver))
                self.assertFalse(mei.protected(receiver))
                self.assertEqual(vitals.total, 250.)
                attacks.fire(run, owner, target, 2, .9)
                self.assertEqual(vitals.total, 175.)
                self.assertEqual(len(owner.client.messages(combat.HIT)), hit_count + 1)

    def test_overlapping_barrier_admission_break_order_and_status_restoration(self):
        for projected_first in (False, True):
            with self.subTest(projected_first=projected_first):
                run, owner, caster, receiver = self.scene()
                self.assertTrue(run.match.switch_hero(receiver, heroes()[defenses.ZARYA]))
                fight = run.match.combat
                x, y, z = owner.mover.position
                fight.walls.place_player(receiver, (x, y, z + 5.))
                for _ in range(30):
                    run.step({})
                target = self.target(run, receiver)
                component = receiver.body_script.component
                flags = tuple(component.entity_var(key) for key in (8440, 7350))
                bases = tuple(flag.base for flag in flags)
                if not projected_first:
                    personal = self.cast(run, receiver)
                    aim = fixtures.aim(fight._eye(caster), fight._eye(receiver))
                    run.step({caster: (*aim, 16)})
                    run.step({caster: (*aim, 0)})
                    self.assertEqual(list(fight.shields.bubbles.values()), [personal])
                    continue
                projected = self.cast(run, caster, receiver)
                self.cast(run, receiver)
                self.assertEqual(len(fight.shields.bubbles), 2)
                personal = next(bubble for bubble in fight.shields.bubbles.values()
                                if bubble.owner is receiver)
                self.assertEqual(personal.health, 200.)
                attacks = fixtures.MeiCombatTests()
                projected.health = 10.  # Explicit capacity fixture for a breaking hit.
                attacks.fire(run, owner, target, 2, .9)
                self.assertEqual(projected.health, 0.)
                self.assertEqual(personal.health, 200.)
                self.assertTrue(all(flag.value() for flag in flags))
                self.assertEqual(fight.vitals_of(target).total, 400.)
                personal.health = 10.
                attacks.fire(run, owner, target, 2, .9)
                self.assertEqual(personal.health, 0.)
                self.assertEqual(fight.vitals_of(target).total, 400.)
                self.assertEqual(tuple(flag.base for flag in flags), bases)
                self.assertIsNone(fight.frost(receiver))
                attacks.fire(run, owner, target, 1, .9)
                self.assertLess(fight.vitals_of(target).total, 400.)
                self.assertIsNotNone(fight.frost(receiver))

    def test_personal_actual_shift_blocks_body_and_head_icicles_without_critical(self):
        for height in (0.9, 1.55):
            with self.subTest(height=height):
                run, owner, zarya, _receiver = self.scene()
                bubble = self.cast(run, zarya)
                target = self.target(run, zarya)
                fixtures.MeiCombatTests().fire(run, owner, target, 2, height)
                self.assertEqual(run.match.combat.vitals_of(target).total, 400.0)
                self.assertEqual(bubble.health, 125.0)
                hits = owner.client.messages(combat.HIT)
                self.assertEqual(len(hits), 1)
                self.assertEqual(hits[0]['+0x78']['+0x0']['+0x0'], bubble.entity)
                self.assertEqual(hits[0]['+0x78']['+0x8'], 0)
                self.assertEqual(zarya.body_script.component.entity_var(87).value(), 15.)

    def test_actual_spray_and_melee_damage_shield_without_health_or_frost(self):
        for button in (1, 0x800):
            with self.subTest(button=button):
                run, owner, zarya, _receiver = self.scene()
                if button == 0x800:
                    p = zarya.mover.position
                    run.match.combat.walls.place_player(owner, (p[0], p[1], p[2] - 1.5))
                bubble = self.cast(run, zarya)
                target = self.target(run, zarya)
                fixtures.MeiCombatTests().fire(run, owner, target, button, 0.9)
                self.assertEqual(run.match.combat.vitals_of(target).total, 400.0)
                self.assertIsNone(run.match.combat.frost(zarya))
                self.assertLess(bubble.health, 200.0)
                if button == 0x800:
                    self.assertEqual(bubble.health, 170.0)
                else:
                    hits = owner.client.messages(combat.HIT)
                    self.assertAlmostEqual(bubble.health, 200 - len(hits) * 2.75)

    def test_rifle_consumes_capacity_then_last_actual_icicle_is_blocked_in_full(self):
        run, owner, zarya, rifle = self.scene()
        rifle.team = owner.team
        bubble = self.cast(run, zarya)
        target = self.target(run, zarya)
        point = (target.feet[0], target.feet[1] + 0.9, target.feet[2])
        aim = fixtures.aim(run.match.combat._eye(rifle), point)
        for _ in range(100):
            if bubble.health <= 50:
                break
            run.step({rifle: (*aim, 1)})
        self.assertGreater(bubble.health, 0.)
        self.assertLessEqual(bubble.health, 50.)
        fixtures.MeiCombatTests().fire(run, owner, target, 2, 0.9)
        self.assertIsNone(run.match.combat.shields.shielded(zarya))
        self.assertEqual(run.match.combat.vitals_of(target).total, 400.0)
        instance = run.match.combat.shields.instance(zarya, 0x244)
        self.assertGreater(instance.vars[61].value(), 9.0)
        fixtures.MeiCombatTests().fire(run, owner, target, 2, 0.9)
        self.assertEqual(run.match.combat.vitals_of(target).total, 325.0)

    def test_projected_actual_e_uses_target_entity_and_authored_duration_cooldown(self):
        run, owner, zarya, receiver = self.scene()
        bubble = self.cast(run, zarya, receiver)
        self.assertEqual(bubble.definition, 0x04000000000003DA)
        self.assertEqual(bubble.target_body, receiver.body)
        target = self.target(run, receiver)
        fixtures.MeiCombatTests().fire(run, owner, target, 2, 0.9)
        self.assertEqual(run.match.combat.vitals_of(target).total, 200.)
        self.assertEqual(bubble.health, 125.)
        for _ in range(90):
            run.step({})
        self.assertIsNone(run.match.combat.shields.shielded(receiver))
        instance = run.match.combat.shields.instance(zarya, 0x25C)
        self.assertGreater(instance.vars[61].value(), 7.7)
        self.assertLess(instance.vars[61].value(), 8.)
        self.assertFalse(receiver.body_script.component.entity_var(8440).value())
        self.assertTrue(any(u.entity == bubble.entity and u.op == world.OP_DESTROY
                            for u in receiver.client.entities))

    def test_projected_cooldown_cannot_be_bypassed_by_repeated_e(self):
        run, _owner, zarya, receiver = self.scene()
        fight = run.match.combat
        bubble = self.cast(run, zarya, receiver)
        for _ in range(130):
            run.step({})
        self.assertIsNone(fight.shields.shielded(receiver))
        cooldown = fight.shields.instance(zarya, 0x25C).vars[61]
        before = cooldown.value()
        self.assertGreater(before, 0.)
        aim = fixtures.aim(fight._eye(zarya), fight._eye(receiver))
        for frame in range(40):
            run.step({zarya: (*aim, 16 if frame % 2 else 0)})
            self.assertIsNone(fight.shields.shielded(receiver))
        self.assertGreater(cooldown.value(), 0.)
        self.assertLess(cooldown.value(), before)
        for _ in range(650):
            if cooldown.value() <= 0.:
                break
            run.step({})
        self.assertEqual(cooldown.value(), 0.)
        self.assertIsNone(fight.shields.shielded(receiver))
        new = self.cast(run, zarya, receiver)
        self.assertNotEqual(new.entity, bubble.entity)
        self.assertEqual(new.target_body, receiver.body)

    def test_projected_rejects_enemy_ffa_range_miss_dead_and_static_occlusion(self):
        for reason in ('enemy', 'ffa', 'range', 'miss', 'dead', 'static'):
            with self.subTest(reason=reason):
                run, owner, zarya, receiver = self.scene()
                p = owner.mover.position
                run.match.combat.walls.place_player(zarya, (p[0], p[1], p[2]))
                run.match.combat.walls.place_player(owner, (p[0] + 20., p[1], p[2]))
                if reason == 'enemy':
                    receiver.team = 0
                elif reason == 'ffa':
                    run.match.game_map = replace(run.match.game_map, free_for_all=True)
                elif reason == 'range':
                    run.match.combat.walls.place_player(receiver, (p[0], p[1], p[2] + 35.))
                elif reason == 'dead':
                    run.match.combat._damage(owner, self.target(run, receiver), 1000., False, None, run.frame)
                elif reason == 'static':
                    fixtures.static_wall(run, zarya)
                aim = fixtures.aim(run.match.combat._eye(zarya), run.match.combat._eye(receiver))
                if reason == 'miss':
                    aim = (aim[0] + 16000, aim[1])
                run.step({zarya: (*aim, 16)})
                run.step({})
                self.assertFalse(run.match.combat.shields.bubbles)
                instance = run.match.combat.shields.instance(zarya, 0x25C)
                self.assertEqual(instance.vars[61].value(), 0.)

    def test_actual_ice_wall_blocks_projected_shield_until_fresh_cast_after_cancel(self):
        run, owner, zarya, receiver = self.scene()
        fight = run.match.combat
        x, y, z = owner.mover.position
        fight.walls.place_player(zarya, (x, y, z + 1.))
        for _ in range(20):
            run.step({})
        walls = wall.WallTests()
        pillars, wall_aim = walls.place(run, owner)
        self.assertEqual(len(pillars), 5)

        cast_aim = fixtures.aim(fight._eye(zarya), fight._eye(receiver))
        self.assertTrue(fight.walls.blocked(fight._eye(zarya), fight._eye(receiver)))
        cooldown = fight.shields.instance(zarya, 0x25C).vars[61]
        run.step({zarya: (*cast_aim, 16)})
        self.assertFalse(fight.shields.bubbles)
        self.assertEqual(cooldown.value(), 0.)

        run.step({owner: (*wall_aim, 16), zarya: (*cast_aim, 16)})
        self.assertFalse(fight.walls.pieces)
        for _ in range(6):
            run.step({zarya: (*cast_aim, 16)})
        self.assertFalse(fight.shields.bubbles)
        self.assertEqual(cooldown.value(), 0.)
        run.step({})

        bubble = self.cast(run, zarya, receiver)
        target = self.target(run, receiver)
        # Clear Zarya's body from the icicle path without moving the shielded ally.
        fight.walls.place_player(zarya, (x + 3., y, z + 1.))
        fixtures.MeiCombatTests().fire(run, owner, target, 2, .9)
        self.assertEqual(fight.vitals_of(target).total, 200.)
        self.assertEqual(bubble.health, 125.)
        hits = owner.client.messages(combat.HIT)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]['+0x78']['+0x0']['+0x0'], bubble.entity)
        for _ in range(130):
            if fight.shields.shielded(receiver) is None:
                break
            run.step({})
        self.assertIsNone(fight.shields.shielded(receiver))
        self.assertGreater(cooldown.value(), 7.9)
        self.assertLessEqual(cooldown.value(), 8.)
        fixtures.MeiCombatTests().fire(run, owner, target, 2, .9)
        self.assertEqual(fight.vitals_of(target).total, 125.)

    def test_projected_shield_cleanses_actual_spray_slow(self):
        run, owner, zarya, receiver = self.scene()
        frost.FrostTests().one_spray(run, owner, self.target(run, receiver))
        self.assertIsNotNone(run.match.combat.frost(receiver))
        self.cast(run, zarya, receiver)
        self.assertIsNone(run.match.combat.frost(receiver))
        self.assertEqual(receiver.body_script.component.entity_var(2427).value(), 0.)
        self.assertTrue(receiver.body_script.component.entity_var(8440).value())

    def test_projected_shield_thaws_actual_frozen_ally_and_restores_movement(self):
        run, owner, zarya, receiver = self.scene()
        frost.FrostTests().freeze(run, owner, self.target(run, receiver))
        for _ in range(20):
            run.step({})  # Clear spray already in flight before checking fresh capacity.
        self.assertTrue(run.match.combat.frozen(receiver))
        self.cast(run, zarya, receiver)
        self.assertFalse(run.match.combat.frozen(receiver))
        position = list(receiver.mover.position)
        frost.FrostTests().commands(run, receiver, 10, forward=127)
        self.assertGreater(receiver.mover.position[2], position[2] + 0.2)

    def test_projected_shield_cleanses_actual_flashbang_on_mei(self):
        run, owner, zarya, caster = self.scene()
        owner.team, caster.team = 1, 0
        run.match.switch_hero(caster, heroes()[0x02E0000000000042])
        p = owner.mover.position
        run.match.combat.walls.place_player(caster, (p[0], p[1], p[2] + 3.))
        for _ in range(30):
            run.step({})
        control.MeiControlTests().cast(run, owner, caster)
        self.assertTrue(run.match.combat.is_stunned(owner))
        self.cast(run, zarya, owner)
        self.assertFalse(run.match.combat.is_stunned(owner))
        position = list(owner.mover.position)
        frost.FrostTests().commands(run, owner, 10, forward=127)
        self.assertGreater(owner.mover.position[2], position[2] + 0.2)

    def test_last_shield_hit_blocks_flashbang_effect_even_when_shield_breaks(self):
        run, owner, zarya, caster = self.scene()
        owner.team, caster.team = 1, 0
        run.match.switch_hero(caster, heroes()[0x02E0000000000042])
        p = owner.mover.position
        run.match.combat.walls.place_player(caster, (p[0], p[1], p[2] + 3.))
        for _ in range(30):
            run.step({})
        bubble = self.cast(run, zarya, owner)
        bubble.health = 10.0  # Explicit partly depleted-capacity fixture.
        control.MeiControlTests().cast(run, owner, caster)
        self.assertIsNone(run.match.combat.shields.shielded(owner))
        self.assertEqual(run.match.combat.vitals_of(self.target(run, owner)).total, 250.)
        self.assertFalse(run.match.combat.is_stunned(owner))

    def test_blizzard_spends_shield_capacity_without_health_damage_or_frost_then_resumes(self):
        run, owner, zarya, _receiver = self.scene()
        blizzard.BlizzardTests().launch(run, owner)
        area = blizzard.BlizzardTests().landed(run)
        bubble = self.cast(run, zarya)
        target = self.target(run, zarya)
        for _ in range(60):
            run.step({})
        self.assertLess(bubble.health, 200.)
        self.assertEqual(run.match.combat.vitals_of(target).total, 400.)
        self.assertIsNone(run.match.combat.frost(zarya))
        for _ in range(90):
            run.step({})
        self.assertIsNone(run.match.combat.shields.shielded(zarya))
        self.assertLess(run.match.combat.vitals_of(target).total, 400.)
        self.assertIsNotNone(run.match.combat.frost(zarya))
        self.assertIn(area.entity, run.match.combat.blizzard.areas)

    def test_blizzard_waits_for_cryo_then_spends_projected_barrier(self):
        run, owner, zarya, receiver = self.scene()
        self.assertTrue(run.match.switch_hero(receiver, heroes()[fixtures.MEI]))
        fight = run.match.combat
        x, y, z = owner.mover.position
        fight.walls.place_player(receiver, (x, y, z + 5.))
        storm = blizzard.BlizzardTests()
        storm.launch(run, owner)
        area = storm.landed(run)
        run.step({receiver: (0, 0, 8)})
        self.assertTrue(mei.protected(receiver))
        bubble = self.cast(run, zarya, receiver)
        vitals = fight.vitals_of(self.target(run, receiver))
        for _ in range(45):
            run.step({})
        self.assertEqual(bubble.health, 200.)
        self.assertEqual(vitals.total, 250.)
        self.assertIsNone(fight.frost(receiver))
        self.assertNotIn(receiver.body, area.frost_due)

        run.step({receiver: (0, 0, 2)})
        self.assertFalse(mei.protected(receiver))
        run.step({})
        self.assertAlmostEqual(bubble.health, 199.68)
        self.assertEqual(vitals.total, 250.)
        self.assertIsNone(fight.frost(receiver))
        for _ in range(130):
            if fight.shields.shielded(receiver) is None:
                break
            run.step({})
            if fight.shields.shielded(receiver) is not None:
                self.assertEqual(vitals.total, 250.)
                self.assertIsNone(fight.frost(receiver))
        self.assertIsNone(fight.shields.shielded(receiver))
        for _ in range(2):
            if vitals.total < 250.:
                break
            run.step({})
        self.assertIn(area.entity, fight.blizzard.areas)
        self.assertAlmostEqual(vitals.total, 249.68)
        self.assertAlmostEqual(fight.frost(receiver)[0], .5)

    def test_blizzard_breaking_tick_blocks_full_damage_and_frost_until_next_tick(self):
        for projected in (False, True):
            with self.subTest(projected=projected):
                run, owner, zarya, receiver = self.scene()
                victim = receiver if projected else zarya
                fight = run.match.combat
                vitals = fight.vitals_of(self.target(run, victim))
                initial_health = vitals.total
                storm = blizzard.BlizzardTests()
                storm.launch(run, owner)
                area = storm.landed(run)
                bubble = self.cast(run, zarya, receiver if projected else None)
                bubble.health = .1  # Explicit capacity fixture below one area tick.
                for _ in range(30):
                    if fight.shields.shielded(victim) is None:
                        break
                    run.step({})
                self.assertEqual(bubble.health, 0.)
                self.assertIsNone(fight.shields.shielded(victim))
                self.assertEqual(vitals.total, initial_health)
                self.assertIsNone(fight.frost(victim))
                self.assertNotIn(victim.body, area.frost_due)

                run.step({})
                self.assertAlmostEqual(vitals.total, initial_health - .32)
                self.assertAlmostEqual(fight.frost(victim)[0], .5)
                self.assertIn(victim.body, area.frost_due)

    def test_midstorm_barrier_cleanses_and_resumes_without_backfilling_frost(self):
        for projected in (False, True):
            with self.subTest(projected=projected):
                run, owner, zarya, receiver = self.scene()
                victim = receiver if projected else zarya
                fight = run.match.combat
                vitals = fight.vitals_of(self.target(run, victim))
                storm = blizzard.BlizzardTests()
                storm.launch(run, owner)
                area = storm.landed(run)
                while run.frame * 16 < area.starts + 1000:
                    run.step({})
                self.assertAlmostEqual(fight.frost(victim)[0], .66, places=6)
                self.assertIn(victim.body, area.frost_due)

                aim = fixtures.aim(fight._eye(zarya), fight._eye(receiver)) if projected else (0, 0)
                run.step({zarya: (*aim, 16 if projected else 8)})
                run.step({zarya: (*aim, 0)})
                bubble = fight.shields.shielded(victim)
                self.assertIsNotNone(bubble)
                self.assertLess(bubble.health, 200.)
                shielded_health = vitals.total
                self.assertIsNone(fight.frost(victim))
                for _ in range(135):
                    run.step({})
                    if fight.shields.shielded(victim) is None:
                        break
                    self.assertEqual(vitals.total, shielded_health)
                    self.assertIsNone(fight.frost(victim))

                self.assertIsNone(fight.shields.shielded(victim))
                self.assertGreater(bubble.health, 0.)  # Natural expiry.
                self.assertIn(area.entity, fight.blizzard.areas)
                if vitals.total == shielded_health:
                    run.step({})
                self.assertAlmostEqual(vitals.total, shielded_health - .32)
                self.assertAlmostEqual(fight.frost(victim)[0], .5, places=6)
                self.assertFalse(fight.frozen(victim))

    def test_sphere_intercepts_a_body_miss_and_static_geometry_still_blocks_it(self):
        for static in (False, True):
            with self.subTest(static=static):
                run, owner, zarya, _receiver = self.scene()
                bubble = self.cast(run, zarya)
                if static:
                    fixtures.static_wall(run, owner)
                p = zarya.mover.position
                aim = fixtures.aim(run.match.combat._eye(owner), (p[0] + 1., p[1] + 1., p[2]))
                for frame in range(45):
                    run.step({owner: (*aim, 2 if frame < 20 else 0)})
                self.assertEqual(bubble.health, 200. if static else 125.)
                self.assertEqual(run.match.combat.vitals_of(self.target(run, zarya)).total, 400.)

    def test_shield_stops_piercing_spray_before_front_and_rear_bodies(self):
        run, owner, zarya, receiver = self.scene()
        p = owner.mover.position
        run.match.combat.walls.place_player(zarya, (p[0], p[1], p[2] + 3.))
        run.match.combat.walls.place_player(receiver, (p[0], p[1], p[2] + 6.))
        bubble = self.cast(run, zarya)
        fixtures.MeiCombatTests().fire(run, owner, self.target(run, receiver), 1, 0.9)
        self.assertLess(bubble.health, 200.)
        for actor, health in ((zarya, 400.), (receiver, 200.)):
            self.assertEqual(run.match.combat.vitals_of(self.target(run, actor)).total, health)
            self.assertIsNone(run.match.combat.frost(actor))

    def test_personal_expiry_cooldown_spam_and_reuse_do_not_replenish_capacity(self):
        run, _owner, zarya, _receiver = self.scene()
        bubble = self.cast(run, zarya)
        for _ in range(130):
            run.step({})
        self.assertFalse(run.match.combat.shields.bubbles)
        self.assertFalse(zarya.body_script.component.entity_var(8440).value())
        instance = run.match.combat.shields.instance(zarya, 0x244)
        for frame in range(40):
            run.step({zarya: (0, 0, 8 if frame % 2 else 0)})
        self.assertFalse(run.match.combat.shields.bubbles)
        self.assertGreater(instance.vars[61].value(), 9.)
        for _ in range(650):
            run.step({})
        new = self.cast(run, zarya)
        self.assertNotEqual(bubble.entity, new.entity)
        self.assertEqual(new.health, 200.)

    def test_owner_or_target_body_retirement_removes_replica_and_status(self):
        for owner_retired in (False, True):
            with self.subTest(owner_retired=owner_retired):
                run, _owner, zarya, receiver = self.scene()
                bubble = self.cast(run, zarya, receiver)
                retired = zarya if owner_retired else receiver
                self.assertTrue(run.match.switch_hero(retired, retired.hero))
                self.assertFalse(run.match.combat.shields.bubbles)
                self.assertFalse(run.match.combat.shields.flags)
                self.assertTrue(any(u.entity == bubble.entity and u.op == world.OP_DESTROY
                                    for u in receiver.client.entities))

    def test_owner_death_retires_projected_shield_and_later_icicle_reaches_ally(self):
        run, owner, zarya, receiver = self.scene()
        bubble = self.cast(run, zarya, receiver)
        fight = run.match.combat
        # Explicit lethal damage drives the real death path, not body replacement.
        fight._damage(owner, self.target(run, zarya), 1000., False, None, run.frame)
        self.assertTrue(fight.is_dead(zarya))
        self.assertFalse(fight.shields.bubbles)
        self.assertFalse(receiver.body_script.component.entity_var(8440).value())
        self.assertTrue(any(u.entity == bubble.entity and u.op == world.OP_DESTROY
                            for u in receiver.client.entities))
        target = self.target(run, receiver)
        fixtures.MeiCombatTests().fire(run, owner, target, 2, 0.9)
        self.assertEqual(fight.vitals_of(target).total, 125.)

    def test_late_viewer_gets_current_shield_once_and_motion_follows_target(self):
        run, owner, zarya, receiver = self.scene()
        owner.spawned = False
        bubble = self.cast(run, zarya, receiver)
        owner.spawned = True
        run.step({})
        run.step({})
        self.assertEqual(len([u for u in owner.client.entities
                              if u.entity == bubble.entity and u.op == world.OP_CREATE]), 1)
        p = receiver.mover.position
        run.match.combat.walls.place_player(receiver, (p[0] + 1., p[1], p[2]))
        run.step({})
        self.assertEqual(bubble.position, tuple(receiver.mover.position))
        self.assertTrue(any(u.entity == bubble.entity and u.movement is not None
                            for u in owner.client.entities))

    def test_friendly_projectiles_pass_shield_and_protected_shooter_can_fire_out(self):
        for outgoing in (False, True):
            with self.subTest(outgoing=outgoing):
                run, owner, zarya, receiver = self.scene()
                if outgoing:
                    bubble = self.cast(run, zarya, receiver)
                    target = self.target(run, owner)
                    before = run.match.combat.vitals_of(target).total
                    aim = fixtures.aim(run.match.combat._eye(receiver),
                                       (target.feet[0], target.feet[1] + 0.9, target.feet[2]))
                    for frame in range(3):
                        run.step({receiver: (*aim, 1 if frame == 0 else 0)})
                    self.assertEqual(run.match.combat.vitals_of(target).total, before - 20.)
                else:
                    owner.team, receiver.team = 1, 0
                    p = owner.mover.position
                    run.match.combat.walls.place_player(zarya, (p[0], p[1], p[2] + 3.))
                    run.match.combat.walls.place_player(receiver, (p[0], p[1], p[2] + 6.))
                    bubble = self.cast(run, zarya)
                    target = self.target(run, receiver)
                    fixtures.MeiCombatTests().fire(run, owner, target, 2, 0.9)
                    self.assertEqual(run.match.combat.vitals_of(target).total, 125.)
                self.assertEqual(bubble.health, 200.)

    def test_absorbed_energy_uses_authored_cap_and_actual_remaining_capacity(self):
        run, owner, zarya, _receiver = self.scene()
        bubble = self.cast(run, zarya)
        zarya.body_script.component.set(87, 95.)
        run.match.combat.shields.damage(bubble, owner, 500., run.frame)
        self.assertEqual(bubble.health, 0.)
        self.assertEqual(zarya.body_script.component.entity_var(87).value(), 100.)
        self.assertIsNone(run.match.combat.shields.shielded(zarya))
        self.assertEqual(run.match.combat.vitals_of(self.target(run, zarya)).total, 400.)

    def test_shield_expiry_preserves_preexisting_status_base_and_frost_immunity(self):
        run, owner, zarya, _receiver = self.scene()
        component = zarya.body_script.component
        component.set(8440, True)  # Explicit independent status source/base fixture.
        self.cast(run, zarya)
        for _ in range(130):
            run.step({})
        self.assertFalse(run.match.combat.shields.bubbles)
        self.assertTrue(component.entity_var(8440).value())
        target = self.target(run, zarya)
        before = run.match.combat.vitals_of(target).total
        frost.FrostTests().one_spray(run, owner, target)
        self.assertLess(run.match.combat.vitals_of(target).total, before)
        self.assertIsNone(run.match.combat.frost(zarya))

    def test_passive_storage_observes_shield_health_target_token_and_status_bases(self):
        import test_server_snapshot as snapshots

        from ow174.game.server_snapshot import snapshot_digest

        fixture = snapshots.ServerSnapshotTests()
        session, _peers, players = fixture.scene()
        owner = players[0]
        manager = owner.match.combat.shields
        before = snapshot_digest(fixture.capture(session))
        bubble = barriers.Bubble(0xC0004000, owner, owner.body, owner, owner.body, 0x244,
                                     (1, 1), 0x04000000000013F4, (0., 0., 5.))
        manager.bubbles[(owner.body, 0x244)] = bubble
        created = snapshot_digest(fixture.capture(session))
        self.assertNotEqual(before, created)
        bubble.health -= 1.
        health = snapshot_digest(fixture.capture(session))
        self.assertNotEqual(created, health)
        bubble.token = (1, 2)
        token = snapshot_digest(fixture.capture(session))
        self.assertNotEqual(health, token)
        manager.flags[owner.body] = (owner.body_script.component, (None, False))
        self.assertNotEqual(token, snapshot_digest(fixture.capture(session)))


if __name__ == '__main__':
    unittest.main()
