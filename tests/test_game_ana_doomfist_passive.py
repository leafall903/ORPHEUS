"""Actual Uppercut and Ana interaction with Doomfist's temporary shield."""

import math
import unittest

import test_game_ana as fixture
import test_game_ana_projected_barrier as projected
from test_game_combat import aim

from ow174.game import doomfist_passive, stats
from ow174.game.content import heroes
from ow174.game.script.driver import ULT_CHARGE
from ow174.game.server_snapshot import FIELDS, OPTIONAL_FIELDS, _Graph
from ow174.game.world import HEALTH_POOL


class AnaDoomfistPassiveTests(unittest.TestCase):
    def arena(self, *, distance=1.5, enemy=True):
        run, ana, doom, placed = fixture.AnaRifleTests().setup_fight(enemy=enemy, distance=distance)
        self.assertTrue(run.match.switch_hero(doom, heroes()[doomfist_passive.HERO]))
        doom.mover.position = list(placed.feet)
        for _ in range(25):
            run.step({})
        fight = run.match.combat
        targets = {entry.owner: entry for entry in fight._targets()}
        ana.body_script.ult_seconds = 0
        ana.body_script.set_ult_charge(0)
        return run, ana, doom, targets

    def uppercut(self, run, doom, target, *, backwards=False):
        x, y, z = target.feet
        yaw, pitch = aim(run.match.combat._eye(doom), (x, y + 1, z))
        if backwards:
            yaw = yaw - 32768 if yaw >= 0 else yaw + 32768
        run.step({doom: (yaw, pitch, 8)})
        run.step({doom: (yaw, pitch, 0)})

    def test_actual_uppercut_damages_ana_once_and_shields_decay(self):
        run, ana, doom, targets = self.arena()
        fight = run.match.combat
        own = fight.vitals_of(targets[doom])
        victim = fight.vitals_of(targets[ana])
        self.uppercut(run, doom, targets[ana])
        self.assertEqual((victim.total, own.bonus_shields), (150, 30))
        for _ in range(40):
            run.step({})
        self.assertEqual((victim.total, own.bonus_shields), (150, 30))
        for _ in range(60):
            run.step({})
        self.assertLess(own.bonus_shields, 30)
        self.assertGreater(own.bonus_shields, 28)
        self.assertEqual(doom.stats.value(stats.HERO_DAMAGE), 50)
        self.assertIsNone(doom.body_script.component.fault)

    def test_uppercut_rejects_team_cover_range_and_reverse_facing(self):
        for rejected in ("team", "cover", "range", "facing"):
            with self.subTest(rejected=rejected):
                run, ana, doom, targets = self.arena(distance=7 if rejected == "range" else 1.5,
                                                      enemy=rejected != "team")
                fight = run.match.combat
                if rejected == "cover":
                    fixture.set_static_wall(run, ana, .75)
                self.uppercut(run, doom, targets[ana], backwards=rejected == "facing")
                for _ in range(20):
                    run.step({})
                self.assertEqual(fight.vitals_of(targets[ana]).total, 200)
                self.assertEqual(fight.vitals_of(targets[doom]).bonus_shields, 0)
                self.assertNotIn(ana.body, fight.external_impulses)

    def test_uppercut_impulse_advances_without_commands_and_retires_on_replacement(self):
        run, ana, doom, targets = self.arena()
        fight = run.match.combat
        x, y, z = targets[ana].feet
        run.step({doom: (*aim(fight._eye(doom), (x, y + 1, z)), 8)})
        dx, dy, dz = fight.external_impulses[ana.body]
        self.assertAlmostEqual(math.hypot(dx, dz), 7)
        self.assertAlmostEqual(dy, 14)
        self.assertLess(dz, 0)
        frame = run.frame + 1
        run.match.update(frame * .016, frame, frames=False)
        self.assertGreater(ana.mover.position[1], y)
        self.assertLess(ana.mover.position[2], z)
        position = tuple(ana.mover.position)
        impulse = fight.external_impulses[ana.body]
        run.match.update(frame * .016, frame, frames=False)
        self.assertEqual(tuple(ana.mover.position), position)
        self.assertEqual(fight.external_impulses[ana.body], impulse)
        old_body = ana.body
        self.assertTrue(run.match.switch_hero(ana, ana.hero))
        self.assertNotEqual(ana.body, old_body)
        self.assertFalse(fight.external_impulses)

    def test_lethal_uppercut_does_not_launch_ana(self):
        run, ana, doom, targets = self.arena()
        fight = run.match.combat
        fight.vitals_of(targets[ana]).damage(151)
        self.uppercut(run, doom, targets[ana])
        self.assertTrue(fight.is_dead(ana))
        self.assertNotIn(ana.body, fight.external_impulses)

    def test_projected_barrier_absorbs_uppercut_without_launch_or_passive_gain(self):
        helper = projected.AnaProjectedBarrierTests()
        run, ana, zarya, doom = helper.fight(ally_ana=True, distance=1.5)
        position = list(doom.mover.position)
        self.assertTrue(run.match.switch_hero(doom, heroes()[doomfist_passive.HERO]))
        doom.team = 1 - ana.team
        doom.mover.position = position
        for _ in range(25):
            run.step({})
        barrier = helper.cast(run, zarya, ana)
        self.uppercut(run, doom, helper.entry(run, ana))
        fight = run.match.combat
        self.assertEqual(barrier.health, 150)
        self.assertEqual(fight.vitals_of(helper.entry(run, ana)).total, 200)
        self.assertEqual(fight.vitals_of(helper.entry(run, doom)).bonus_shields, 0)
        self.assertNotIn(ana.body, fight.external_impulses)

    def test_passive_decay_uses_world_time_without_doomfist_commands(self):
        results = []
        for skipped in (False, True):
            with self.subTest(skipped=skipped):
                run, ana, doom, targets = self.arena()
                self.uppercut(run, doom, targets[ana])
                fight = run.match.combat
                own = fight.vitals_of(targets[doom])
                script_frame = doom.body_script.frame
                start = run.frame
                frames = (start + 61, start + 125) if skipped else range(start + 1, start + 126)
                for frame in frames:
                    run.match.update(frame * .016, frame, frames=False)
                    if frame == start + 61:
                        self.assertEqual(own.bonus_shields, 30)
                self.assertEqual(doom.body_script.frame, script_frame)
                results.append(own.bonus_shields)
                self.assertAlmostEqual(own.bonus_shields, 26.952)
                at_end = own.bonus_shields
                run.match.update((start + 125) * .016, start + 125, frames=False)
                self.assertEqual(own.bonus_shields, at_end)
                run.frame = start + 125
                run.step({})
                self.assertAlmostEqual(own.bonus_shields, at_end - .048)
        if len(results) == 2:
            self.assertAlmostEqual(*results, places=9)

    def test_pending_passive_decay_is_captured_and_retires_on_body_replacement(self):
        run, ana, doom, targets = self.arena()
        self.uppercut(run, doom, targets[ana])
        fight = run.match.combat
        own = fight.vitals_of(targets[doom])
        encoder = _Graph({}, {})
        layout = type(own)
        self.assertIn("doomfist_decay", encoder._fields(own, FIELDS[layout], OPTIONAL_FIELDS[layout])["fields"])
        old_body = doom.body
        self.assertTrue(run.match.switch_hero(doom, doom.hero))
        self.assertNotEqual(doom.body, old_body)
        self.assertNotIn(old_body, fight.vitals)
        frame = run.frame + 125
        run.match.update(frame * .016, frame, frames=False)
        replacement = next(target for target in fight._targets() if target.owner is doom)
        new_vitals = fight.vitals_of(replacement)
        self.assertEqual(new_vitals.bonus_shields, 0)
        self.assertNotIn("doomfist_decay", encoder._fields(
            new_vitals, FIELDS[layout], OPTIONAL_FIELDS[layout])["fields"])
        self.assertEqual(own.bonus_shields, 30)

    def test_rifle_overflow_credits_only_ordinary_health(self):
        run, ana, doom, targets = self.arena()
        self.uppercut(run, doom, targets[ana])
        own = run.match.combat.vitals_of(targets[doom])
        self.assertEqual(own.bonus_shields, 30)
        health = own.now[HEALTH_POOL]
        fixture.AnaRifleTests().shoot(run, ana, targets[doom], False)
        self.assertEqual(own.bonus_shields, 0)
        self.assertEqual(own.now[HEALTH_POOL], health - 40)
        self.assertEqual(ana.stats.value(stats.HERO_DAMAGE), 70)
        self.assertEqual(ana.body_script.component.entity_var(ULT_CHARGE).value(), 40)

    def test_dart_applies_sleep_with_shield_only_damage(self):
        run, ana, doom, targets = self.arena()
        self.uppercut(run, doom, targets[ana])
        fight = run.match.combat
        own = fight.vitals_of(targets[doom])
        health = own.now[HEALTH_POOL]
        x, y, z = targets[doom].feet
        run.step({ana: (*aim(fight._eye(ana), (x, y + 1, z)), 8)})
        for _ in range(40):
            if fight.is_asleep(doom):
                break
            run.step({ana: (*aim(fight._eye(ana), (x, y + 1, z)), 0)})
        self.assertTrue(fight.is_asleep(doom))
        self.assertEqual((own.now[HEALTH_POOL], own.bonus_shields), (health, 25))
        self.assertEqual(ana.stats.value(stats.HERO_DAMAGE), 5)
        self.assertEqual(ana.body_script.component.entity_var(ULT_CHARGE).value(), 0)

    def test_friendly_rifle_restores_health_without_refilling_passive_shields(self):
        run, ana, doom, targets = self.arena()
        self.uppercut(run, doom, targets[ana])
        own = run.match.combat.vitals_of(targets[doom])
        health = own.now[HEALTH_POOL]
        own.now[HEALTH_POOL] = health - 100
        doom.team = ana.team
        fixture.AnaRifleTests().shoot(run, ana, targets[doom], False)
        self.assertEqual(own.now[HEALTH_POOL], health - 30)
        self.assertEqual(own.bonus_shields, 30)
        self.assertEqual(ana.stats.value(stats.HEALING), 70)
        self.assertEqual(ana.body_script.component.entity_var(ULT_CHARGE).value(), 70)
