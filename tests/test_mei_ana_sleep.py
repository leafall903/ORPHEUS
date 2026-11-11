"""Actual Ana Sleep Dart input against Mei's static combat paths."""

import math
import unittest
from dataclasses import replace

import test_mei_ana_grenade as grenade
import test_mei_blizzard as blizzard
import test_mei_frost as frost
import test_mei_wall as walls
import test_mei_wall_placement as placement
from test_game_combat import aim
from test_game_mover import box

from ow174.game import combat, mei


class MeiAnaSleepTests(unittest.TestCase):
    def launch(self, run, ana, owner, *, miss=False):
        fight = run.match.combat
        x, y, z = owner.mover.position
        angles = (16384, 0) if miss else aim(fight._eye(ana), (x, y + 1., z))
        run.step({ana: (*angles, 8)})
        for _ in range(40):
            darts = [rocket for rocket in fight.rockets
                     if rocket.direct is not None and rocket.direct.effect == combat.SLEEP_DART_EFFECT]
            if darts:
                break
            run.step({ana: (*angles, 0)})
        [dart] = darts
        self.assertEqual(dart.definition, 0x04000000000015E8)
        self.assertEqual(dart.direct.amount, 5.)
        return dart

    def impact(self, run, dart):
        for _ in range(math.ceil(dart.left / combat.TICK) + 2):
            if dart not in run.match.combat.rockets:
                return
            run.step({})
        self.fail("Sleep Dart did not resolve")

    def test_sleep_locks_inputs_until_world_deadline_and_then_allows_cryo(self):
        fixture = grenade.MeiAnaGrenadeTests()
        run, owner, ana, vitals = fixture.scene()
        fight = run.match.combat
        self.impact(run, self.launch(run, ana, owner))
        self.assertEqual(vitals.total, 245.)
        deadline = vitals.sleep_until
        self.assertEqual(deadline - fight.tick, math.ceil(5. / combat.TICK))
        weapon = next(i for i in owner.body_script.component.instances.values() if i.graph.index == 0x5E6)
        before, ammo = tuple(owner.mover.position), weapon.vars[53].value()
        frost.FrostTests().commands(run, owner, 12, buttons=1 | 2 | 8 | 16 | 32 | 0x800, forward=127)
        self.assertEqual(tuple(owner.mover.position), before)
        self.assertEqual(weapon.vars[53].value(), ammo)
        self.assertFalse(mei.protected(owner))
        self.assertFalse(fight.walls.pieces or fight.blizzard.flights)
        run.frame = deadline - 1
        run.match.update(run.frame * combat.TICK, run.frame)
        self.assertTrue(fight.is_stunned(owner))
        run.step({owner: (0, 0, 8)})
        self.assertFalse(fight.is_stunned(owner))
        self.assertEqual((vitals.sleep_until, vitals.sleep_wake_floor), (0, 0))
        self.assertTrue(mei.protected(owner))

    def test_early_and_late_rifle_damage_shorten_sleep_without_extending_it(self):
        for late in (False, True):
            with self.subTest(late=late):
                fixture = grenade.MeiAnaGrenadeTests()
                run, owner, ana, vitals = fixture.scene()
                soldier = fixture.field_caster(run, owner)
                fight = run.match.combat
                self.impact(run, self.launch(run, ana, owner))
                floor, deadline = vitals.sleep_wake_floor, vitals.sleep_until
                self.assertEqual(floor - fight.tick, math.ceil(1.5 / combat.TICK))
                target = next(target for target in fight._targets() if target.owner is owner)
                fight._damage(soldier, target, 0., False, None, run.frame)
                self.assertEqual(vitals.sleep_until, deadline)
                if late:
                    while fight.tick < floor:
                        run.step({})
                x, y, z = owner.mover.position
                angles = aim(fight._eye(soldier), (x, y + .9, z))
                run.step({soldier: (*angles, 1)})
                self.assertLess(vitals.total, 245.)
                wake = max(floor, fight.tick + math.ceil(.5 / combat.TICK))
                self.assertEqual(vitals.sleep_until, wake)
                self.assertEqual(wake == floor, not late)
                for _ in range(10):
                    run.step({soldier: (*angles, 1)})
                    self.assertEqual(vitals.sleep_until, wake)
                run.frame = wake - 2
                run.step({})
                self.assertTrue(fight.is_stunned(owner))
                run.step({})
                self.assertFalse(fight.is_stunned(owner))

    def test_cryo_and_projected_barrier_block_sleep_and_barrier_can_cleanse_it(self):
        for mode in ("cryo", "barrier", "cleanse"):
            with self.subTest(mode=mode):
                fixture = grenade.MeiAnaGrenadeTests()
                run, owner, ana, vitals = fixture.scene()
                zarya = fixture.projector(run, owner, ana)
                fight = run.match.combat
                bubble = None
                if mode == "cryo":
                    run.step({owner: (0, 0, 8)})
                elif mode == "barrier":
                    bubble = fixture.project_barrier(run, zarya, owner)
                self.impact(run, self.launch(run, ana, owner))
                if mode == "cleanse":
                    self.assertTrue(fight.is_stunned(owner))
                    self.assertEqual(vitals.total, 245.)
                    fixture.project_barrier(run, zarya, owner)
                    run.step({owner: (0, 0, 8)})
                    self.assertTrue(mei.protected(owner))
                else:
                    self.assertEqual(vitals.total, 250.)
                self.assertEqual((vitals.sleep_until, vitals.sleep_wake_floor), (0, 0))
                self.assertFalse(fight.is_stunned(owner))
                if bubble is not None:
                    self.assertEqual(bubble.health, 195.)

    def test_sleep_dart_miss_ally_and_static_cover_apply_no_damage_or_control(self):
        for mode in ("miss", "ally", "cover"):
            with self.subTest(mode=mode):
                run, owner, ana, vitals = grenade.MeiAnaGrenadeTests().scene()
                fight = run.match.combat
                if mode == "ally":
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    ana.team = owner.team
                elif mode == "cover":
                    x, y, z = owner.mover.position
                    fixture = placement.WallPlacementTests()
                    fixture.install(run, fixture.floor(x, y, z) + box(
                        x - 2., x + 2., y, y + 4., z - 1.6, z - 1.4,
                    ))
                self.impact(run, self.launch(run, ana, owner, miss=mode == "miss"))
                self.assertEqual(vitals.total, 250.)
                self.assertEqual((vitals.sleep_until, vitals.sleep_wake_floor), (0, 0))
                self.assertFalse(fight.is_stunned(owner))

    def test_death_and_body_replacement_retire_sleep(self):
        for death in (False, True):
            with self.subTest(death=death):
                run, owner, ana, vitals = grenade.MeiAnaGrenadeTests().scene()
                fight = run.match.combat
                self.impact(run, self.launch(run, ana, owner))
                self.assertTrue(fight.is_stunned(owner))
                old_body = owner.body
                if death:
                    target = next(target for target in fight._targets() if target.owner is owner)
                    fight._damage(ana, target, 1000., False, None, run.frame)
                    self.assertTrue(vitals.dead)
                    self.assertEqual((vitals.sleep_until, vitals.sleep_wake_floor), (0, 0))
                self.assertTrue(run.match.switch_hero(owner, owner.hero))
                self.assertNotEqual(owner.body, old_body)
                self.assertNotIn(old_body, fight.vitals)
                self.assertFalse(fight.is_stunned(owner))

    def test_sleep_cancels_wall_preview_without_owner_commands(self):
        run, owner, ana, vitals = grenade.MeiAnaGrenadeTests().scene()
        fight = run.match.combat
        x, y, z = owner.mover.position
        angles = aim(fight._eye(owner), (x, y, z + 2.))
        walls.WallTests().tap(run, owner, 16, angles)
        wall = fight.walls.inputs[owner.body]
        self.assertTrue(wall.preview)
        dart = self.launch(run, ana, owner)
        for _ in range(30):
            if dart not in fight.rockets:
                break
            run.frame += 1
            run.match.update(run.frame * combat.TICK, run.frame)
        self.assertNotIn(dart, fight.rockets)
        self.assertEqual(vitals.total, 245.)
        self.assertTrue(fight.is_stunned(owner))
        self.assertFalse(wall.preview or fight.walls.pieces)
        self.assertEqual(fight.walls.instance(owner).vars[61].value(), 0.)

    def test_sleep_interrupts_blizzard_windup_but_released_blizzard_lands(self):
        for released in (False, True):
            with self.subTest(released=released):
                run, owner, ana, vitals = grenade.MeiAnaGrenadeTests().scene()
                fight = run.match.combat
                owner.body_script.ult_seconds = 0
                if released:
                    blizzard.BlizzardTests().launch(run, owner)
                    dart = self.launch(run, ana, owner)
                else:
                    dart = self.launch(run, ana, owner)
                    owner.body_script.set_ult_charge(1610.)
                    run.step({owner: (0, 9000, 32)})
                charge = owner.body_script.component.entity_var(342)
                self.assertEqual(charge.value(), 0.)
                self.impact(run, dart)
                self.assertEqual(vitals.total, 245.)
                self.assertTrue(fight.is_stunned(owner))
                for _ in range(100):
                    run.step({})
                self.assertEqual(charge.value(), 0.)
                self.assertEqual(bool(fight.blizzard.areas), released)
