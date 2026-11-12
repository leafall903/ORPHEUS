"""Moira attacks through actual Zarya barrier phases on the reconstructed server."""

import unittest
from dataclasses import replace

import test_game_moira as moira_tests
import test_game_moira_biotic as biotic_tests
import test_game_moira_orbs as orb_tests
from test_game_combat import Run, aim, in_world

from ow174.game import stats
from ow174.game.content import find_hero


class MoiraZaryaTests(unittest.TestCase):
    start_ult = moira_tests.MoiraOpenAcceptanceTests.start_ult
    launch_orb = orb_tests.MoiraOrbTests.launch
    cast_grenade = biotic_tests.MoiraBioticTests.cast

    def scene(self, projected, *, allied=False, depth=1.75):
        run = Run(names=('Moira', 'Recipient', 'Zarya') if projected else ('Moira', 'Zarya'))
        match = run.match
        match.game_map = replace(match.game_map, free_for_all=False)
        match.bots.clear()
        match._pending_bot_scripts.clear()
        source, recipient = run.players[:2]
        caster = run.players[-1]
        for actor, hero in ((source, 'moira'), (recipient, 'soldier: 76' if projected else 'zarya')):
            self.assertTrue(match.switch_hero(actor, find_hero(hero)))
        if projected:
            self.assertTrue(match.switch_hero(caster, find_hero('zarya')))
        source.team, recipient.team = 0, 0 if allied else 1
        caster.team = recipient.team
        x, y, z = source.mover.position
        recipient.mover.position[:] = (x, y, z + depth)
        if projected:
            caster.mover.position[:] = (x - 4, y, z + depth)
        for player in run.players:
            player.mover.body = None
        fight = match.combat
        for _ in range(30):
            run.step({})
        target = next(t for t in fight._targets() if t.owner is recipient)
        a, b = target.segment()
        centre = tuple((left + right) * 0.5 for left, right in zip(a, b, strict=True))
        cast_aim = aim(fight._eye(caster), centre) if projected else (0, 0)
        attack_aim = aim(fight._eye(source), centre)
        run.step({caster: (*cast_aim, 16 if projected else 8)})
        bubble = fight.zarya_bubbles.for_target(recipient)
        self.assertIsNotNone(bubble)
        self.assertIs(bubble.caster, caster)
        self.assertEqual(bubble.definition & 0xFFFF, 0x3DA if projected else 0x13F4)
        self.assertEqual(bubble.health, 200)
        if projected:
            caster.mover.position[0] -= 16
            caster.mover.body = None
        return run, source, recipient, fight.vitals_of(target), bubble, attack_aim

    def test_four_actual_attacks_damage_personal_and_projected_barriers(self):
        for projected in (False, True):
            for attack, amount in (('grasp', 0.8), ('melee', 30), ('orb', 0.8), ('beam', 1.12)):
                with self.subTest(projected=projected, attack=attack):
                    run, source, recipient, health, bubble, direction = self.scene(projected)
                    fight = run.match.combat
                    before = health.total
                    damage_before = source.stats.value(stats.HERO_DAMAGE)
                    orb = None
                    if attack == 'orb':
                        orb = self.launch_orb(run, source)
                    elif attack == 'beam':
                        self.start_ult(run, source)
                    shield_before = bubble.health
                    for frame in range(45):
                        button = 2 if attack == 'grasp' else 0x800 if attack == 'melee' and frame == 0 else 0
                        run.step({source: (*direction, button)})
                        if bubble.health < shield_before:
                            break
                    self.assertAlmostEqual(shield_before - bubble.health, amount, places=4)
                    self.assertEqual(health.total, before)
                    self.assertEqual(source.stats.value(stats.HERO_DAMAGE), damage_before)
                    self.assertIs(fight.zarya_bubbles.for_target(recipient), bubble)
                    if orb is not None:
                        self.assertAlmostEqual(200 - orb.energy, 200 - bubble.health, places=4)

    def test_actual_melee_break_has_no_body_overflow_and_fresh_swing_hits(self):
        for projected in (False, True):
            with self.subTest(projected=projected):
                run, source, recipient, health, bubble, direction = self.scene(projected)
                fight = run.match.combat
                bubble.health = 1
                before = health.total
                velocity = recipient.mover.velocity
                run.step({source: (*direction, 0x800)})
                self.assertEqual(recipient.mover.velocity, velocity)
                for _ in range(25):
                    run.step({source: (*direction, 0)})
                self.assertEqual(bubble.health, 0)
                self.assertIsNone(fight.zarya_bubbles.for_target(recipient))
                self.assertEqual(health.total, before)
                for _ in range(60):
                    run.step({source: (*direction, 0)})
                run.step({source: (*direction, 0x800)})
                for _ in range(25):
                    run.step({source: (*direction, 0)})
                self.assertLess(health.total, before)

    def test_actual_spray_heals_through_allied_bubble(self):
        run, source, _, health, bubble, direction = self.scene(True, allied=True, depth=4)
        health.damage(100)
        before = health.total
        for _ in range(30):
            run.step({source: (*direction, 1)})
        self.assertGreater(health.total, before)
        self.assertEqual(bubble.health, 200)


    def test_hostile_bubble_blocks_spray_to_ally_until_expiry(self):
        run, source, _, _, bubble, _ = self.scene(False, depth=3.5)
        match, fight = run.match, run.match.combat
        ally = match.add_player(3, 'Ally', find_hero('soldier: 76').guid, source.team, False)
        in_world(match, ally)
        run.players.append(ally)
        self.assertTrue(match.switch_hero(ally, find_hero('soldier: 76')))
        ally.team = source.team
        x, y, z = source.mover.position
        ally.mover.position[:] = (x, y, z + 6)
        ally.mover.body = None
        target = next(t for t in fight._targets() if t.owner is ally)
        health = fight.vitals_of(target)
        health.damage(100)
        before = health.total
        direction = aim(fight._eye(source), (x, y + target.height / 2, z + 6))
        for _ in range(30):
            run.step({source: (*direction, 1)})
        self.assertEqual(health.total, before)
        self.assertNotIn(target.entity, fight.moira_hots)
        self.assertEqual(bubble.health, 200)
        for _ in range(150):
            run.step({})
        self.assertIsNone(fight.zarya_bubbles.for_target(bubble.owner))
        for _ in range(30):
            run.step({source: (*direction, 1)})
        self.assertGreater(health.total, before)

    def test_projected_bubble_cleanses_or_blocks_actual_antiheal_on_moira(self):
        for shield_first in (False, True):
            with self.subTest(shield_first=shield_first):
                run = Run(names=('Moira', 'Zarya', 'Ana'))
                match, fight = run.match, run.match.combat
                match.game_map = replace(match.game_map, free_for_all=False)
                match.bots.clear()
                match._pending_bot_scripts.clear()
                moira, zarya, ana = run.players
                for actor, hero in ((moira, 'moira'), (zarya, 'zarya'), (ana, 'ana')):
                    self.assertTrue(match.switch_hero(actor, find_hero(hero)))
                    actor.mover.body = None
                x, y, z = moira.mover.position
                moira.team, zarya.team, ana.team = 0, 0, 1
                zarya.mover.position[:] = (x - 4, y, z)
                ana.mover.position[:] = (x + 3, y, z)
                for _ in range(25):
                    run.step({})
                target = next(t for t in fight._targets() if t.owner is moira)
                direction = aim(fight._eye(zarya), (x, y + target.height / 2, z))
                health = fight.vitals_of(target)
                if not shield_first:
                    self.cast_grenade(run, ana, target)
                    self.assertGreater(health.healing_block_until, fight.now)
                run.step({zarya: (*direction, 16)})
                bubble = fight.zarya_bubbles.for_target(moira)
                self.assertIsNotNone(bubble)
                self.assertEqual(health.healing_block_until, 0)
                if shield_first:
                    before = health.total
                    self.cast_grenade(run, ana, target)
                    self.assertLess(bubble.health, 200)
                    self.assertEqual(health.total, before)
                    self.assertEqual(health.healing_block_until, 0)
