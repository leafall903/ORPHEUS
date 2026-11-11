"""Actual 104319 Nano Boost targeting, release and Mei combat."""

import unittest
from dataclasses import replace

import test_mei_ana_grenade as grenade
import test_mei_blizzard as blizzard
import test_mei_combat as fixtures
from test_game_combat import aim

from ow174.game import combat, stats
from ow174.game.content import heroes


class MeiAnaNanoTests(unittest.TestCase):
    def scene(self):
        run, owner, ana, vitals = grenade.MeiAnaGrenadeTests().scene()
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        ana.team = owner.team
        ana.body_script.ult_seconds = 0
        ana.body_script.set_ult_charge(2100.)
        return run, owner, ana, vitals

    def target(self, fight, owner):
        return next(target for target in fight._targets() if target.owner is owner)

    def enemy(self, run, owner, distance=5.):
        soldier = grenade.MeiAnaGrenadeTests().field_caster(run, owner)
        soldier.team = 1 - owner.team
        x, y, z = owner.mover.position
        run.match.combat.walls.place_player(soldier, (x, y, z + distance))
        for _ in range(4):
            run.step({})
        return soldier

    def press(self, run, ana, owner, *, miss=False):
        fight = run.match.combat
        target = self.target(fight, owner)
        x, y, z = target.feet
        angles = aim(fight._eye(ana), (x, y + target.height / 2, z))
        if miss:
            angles = ((angles[0] + 49152) % 65536 - 32768, angles[1])
        run.step({ana: (*angles, 32)})
        return angles

    def cast(self, run, ana, owner, vitals):
        angles = self.press(run, ana, owner)
        for _ in range(13):
            if vitals.nano_until:
                break
            run.step({ana: (*angles, 0)})
        self.assertGreater(vitals.nano_until, run.match.combat.now)

    def test_authored_windup_releases_once_and_expires_with_no_ana_input(self):
        for skipped in (False, True):
            with self.subTest(skipped=skipped):
                run, owner, ana, vitals = self.scene()
                fight = run.match.combat
                enemy = self.enemy(run, owner)
                vitals.damage(130.)
                self.press(run, ana, owner)
                charge = ana.body_script.component.entity_var(342)
                self.assertEqual(charge.value(), 0.)
                self.assertEqual(vitals.total, 120.)
                self.assertEqual(vitals.nano_until, 0.)
                for _ in range(1 if skipped else 13):
                    run.frame += 13 if skipped else 1
                    run.match.update(run.frame * combat.TICK, run.frame)
                self.assertEqual(vitals.total, 250.)
                self.assertAlmostEqual(vitals.nano_until - fight.now, 8., delta=.1)
                self.assertEqual(ana.stats.value(stats.HEALING), 130.)
                angles = aim(fight._eye(enemy), (owner.mover.position[0], owner.mover.position[1] + .9,
                                                 owner.mover.position[2]))
                run.step({enemy: (*angles, 1)})
                self.assertEqual(vitals.total, 240.)
                run.frame = fight.tick + round((vitals.nano_until - fight.now) / combat.TICK) - 1
                run.step({})
                self.assertEqual(fight.outgoing_damage_scale(owner), 1.)
                run.step({enemy: (*angles, 1)})
                self.assertEqual(vitals.total, 220.)

    def test_target_search_rejects_enemy_miss_range_cover_and_uncharged_input(self):
        for mode in ("enemy", "miss", "range", "cover", "uncharged"):
            with self.subTest(mode=mode):
                run, owner, ana, vitals = self.scene()
                fight = run.match.combat
                if mode == "enemy":
                    ana.team = 1 - owner.team
                elif mode == "range":
                    x, y, z = ana.mover.position
                    fight.walls.place_player(owner, (x, y, z + 41.))
                elif mode == "cover":
                    fixtures.static_wall(run, ana)
                elif mode == "uncharged":
                    ana.body_script.set_ult_charge(100.)
                vitals.damage(130.)
                angles = self.press(run, ana, owner, miss=mode == "miss")
                for _ in range(13):
                    run.step({ana: (*angles, 0)})
                self.assertEqual(vitals.total, 120.)
                self.assertEqual(vitals.nano_until, 0.)
                self.assertEqual(ana.body_script.component.entity_var(342).value(),
                                 100. if mode == "uncharged" else 2100.)

    def test_nano_scales_mei_primary_secondary_and_melee_damage(self):
        for buttons in (1, 2, 0x800):
            results = []
            for boosted in (False, True):
                with self.subTest(buttons=buttons, boosted=boosted):
                    run, owner, ana, vitals = self.scene()
                    fight = run.match.combat
                    enemy = self.enemy(run, owner, 1.5 if buttons == 0x800 else 5.)
                    target = self.target(fight, enemy)
                    if boosted:
                        self.cast(run, ana, owner, vitals)
                    fixtures.MeiCombatTests().fire(run, owner, target, buttons, .9)
                    damage = 200. - fight.vitals_of(target).total
                    self.assertGreater(damage, 0.)
                    results.append(damage)
            self.assertAlmostEqual(results[1], 1.5 * results[0])

    def test_blizzard_captures_nano_at_release_and_keeps_damage_after_expiry(self):
        run, owner, ana, vitals = self.scene()
        fight = run.match.combat
        enemy = self.enemy(run, owner)
        target = self.target(fight, enemy)
        self.cast(run, ana, owner, vitals)
        run.frame = fight.tick + round((vitals.nano_until - fight.now) / combat.TICK) - 75
        run.step({})
        fixture = blizzard.BlizzardTests()
        flight = fixture.launch(run, owner)
        self.assertEqual(flight.damage_scale, 1.5)
        area = fixture.landed(run)
        self.assertEqual(area.damage_scale, 1.5)
        fixture.finish(run, area)
        self.assertGreater(fight.now, vitals.nano_until)
        self.assertAlmostEqual(fight.vitals_of(target).total, 72.5)

    def test_caster_death_cancels_windup_but_preserves_an_applied_nano(self):
        for released in (False, True):
            with self.subTest(released=released):
                run, owner, ana, vitals = self.scene()
                fight = run.match.combat
                enemy = self.enemy(run, owner)
                vitals.damage(130.)
                if released:
                    self.cast(run, ana, owner, vitals)
                else:
                    self.press(run, ana, owner)
                deadline = vitals.nano_until
                self.assertEqual(ana.body_script.component.entity_var(342).value(), 0.)
                fight._damage(enemy, self.target(fight, ana), 1000., False, None, run.frame)
                self.assertTrue(fight.is_dead(ana))
                for _ in range(14):
                    run.step({})
                self.assertEqual(vitals.total, 250. if released else 120.)
                self.assertEqual(vitals.nano_until, deadline)

    def test_target_replacement_retires_nano_and_does_not_retarget_windup(self):
        for released in (False, True):
            with self.subTest(released=released):
                run, owner, ana, vitals = self.scene()
                fight = run.match.combat
                if released:
                    self.cast(run, ana, owner, vitals)
                else:
                    self.press(run, ana, owner)
                self.assertEqual(ana.body_script.component.entity_var(342).value(), 0.)
                body = owner.body
                self.assertTrue(run.match.switch_hero(owner, owner.hero))
                self.assertNotEqual(owner.body, body)
                replacement = fight.vitals_of(self.target(fight, owner))
                replacement.damage(100.)
                for _ in range(14):
                    run.step({})
                self.assertEqual(replacement.total, 150.)
                self.assertEqual(replacement.nano_until, 0.)
                self.assertNotIn(body, fight.vitals)

    def test_antiheal_blocks_nano_dose_without_blocking_the_damage_modifiers(self):
        run, owner, ana, vitals = self.scene()
        enemy = self.enemy(run, owner)
        self.assertTrue(run.match.switch_hero(enemy, heroes()[grenade.ANA]))
        fight = run.match.combat
        x, y, z = owner.mover.position
        fight.walls.place_player(enemy, (x + 3., y, z))
        for _ in range(30):
            run.step({})
        grenade.MeiAnaGrenadeTests().throw(run, enemy, owner)
        self.assertEqual(vitals.total, 200.)
        deadline = vitals.healing_block_until
        self.assertGreater(deadline, fight.now)
        self.cast(run, ana, owner, vitals)
        self.assertEqual(vitals.total, 200.)
        self.assertEqual(ana.stats.value(stats.HEALING), 0.)
        self.assertEqual(vitals.healing_block_until, deadline)
        self.assertEqual(fight.outgoing_damage_scale(owner), 1.5)
