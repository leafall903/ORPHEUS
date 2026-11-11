"""Actual 104319 Biotic Grenade and Cryo-Freeze inputs."""

import unittest
from dataclasses import replace

import test_mei_combat as fixtures
from test_game_combat import in_world
from test_game_projectiles import ABILITY_2

from ow174.game import ana_grenade, combat, mei, projectiles, stats, zarya_shields
from ow174.game.content import SOLDIER, heroes

ANA = 0x02E000000000013B


class MeiAnaGrenadeTests(unittest.TestCase):
    def scene(self):
        run, owner, ana, _ = fixtures.MeiCombatTests().duel()
        fight = run.match.combat
        self.assertTrue(run.match.switch_hero(ana, heroes()[ANA]))
        x, y, z = owner.mover.position
        fight.walls.place_player(ana, (x, y, z - 3.))
        for _ in range(30):
            run.step({})
        target = next(t for t in fight._targets() if t.owner is owner)
        return run, owner, ana, fight.vitals_of(target)

    def throw(self, run, ana, owner):
        fight = run.match.combat
        x, y, z = owner.mover.position
        aim = fixtures.aim(fight._eye(ana), (x, y + 1.1, z))
        run.step({ana: (*aim, 16)})
        for _ in range(30):
            if any(shot.definition == ana_grenade.DEFINITION for shot in fight.rockets):
                break
            run.step({})
        [grenade] = [shot for shot in fight.rockets if shot.definition == ana_grenade.DEFINITION]
        self.assertAlmostEqual(grenade.gravity, 9.8, places=5)
        self.assertEqual(grenade.blast.amount, 50.)
        self.assertEqual(grenade.blast.rings, ((4., 1.),))
        for _ in range(30):
            if grenade not in fight.rockets:
                break
            run.step({})
        self.assertNotIn(grenade, fight.rockets)

    def test_enemy_grenade_blocks_cryo_healing_without_backfill(self):
        totals = []
        for skipped in (False, True):
            with self.subTest(skipped=skipped):
                run, owner, ana, vitals = self.scene()
                fight = run.match.combat
                vitals.damage(130.)
                self.throw(run, ana, owner)
                self.assertEqual(vitals.total, 70.)
                self.assertAlmostEqual(vitals.healing_block_until - fight.now, 4.)
                for _ in range(75):
                    run.step({})
                owner.body_script.ult_seconds = 0
                owner.body_script.set_ult_charge(0.)
                charge = owner.body_script.component.entity_var(342)
                run.step({owner: (0, 0, 8)})
                self.assertTrue(mei.protected(owner))
                self.assertEqual(vitals.total, 70.)
                for _ in range(30):
                    run.step({})
                if skipped:
                    run.frame += 64
                    run.step({})
                else:
                    for _ in range(65):
                        run.step({})
                self.assertEqual(vitals.total, 70.)
                self.assertEqual(owner.stats.value(stats.HEALING), 0.)
                self.assertEqual(charge.value(), 0.)
                while fight.now < vitals.healing_block_until:
                    run.step({})
                for _ in range(3):
                    if vitals.total > 70.:
                        break
                    run.step({})
                self.assertGreater(vitals.total, 70.)
                self.assertLessEqual(vitals.total, 71.)
                for _ in range(255):
                    run.step({})
                self.assertFalse(mei.protected(owner))
                self.assertAlmostEqual(vitals.total, 130., delta=1.)
                self.assertAlmostEqual(owner.stats.value(stats.HEALING), vitals.total - 70.)
                self.assertAlmostEqual(charge.value(), vitals.total - 70., places=3)
                totals.append(vitals.total)
        self.assertAlmostEqual(totals[0], totals[1])

    def test_active_cryo_intercepts_grenade_and_protects_rear_enemy(self):
        for protected in (False, True):
            with self.subTest(protected=protected):
                run, owner, ana, vitals = self.scene()
                fight = run.match.combat
                rear = run.match.add_player(17, "Rear", ana.hero.guid, 0, False)
                in_world(run.match, rear)
                run.players.append(rear)
                self.assertTrue(run.match.switch_hero(rear, ana.hero))
                x, y, z = owner.mover.position
                fight.walls.place_player(rear, (x, y, z + 1.8))
                for _ in range(10):
                    run.step({})
                rear_target = next(t for t in fight._targets() if t.owner is rear)
                rear_vitals = fight.vitals_of(rear_target)
                rear_health = rear_vitals.total
                if protected:
                    run.step({owner: (0, 0, 8)})
                self.throw(run, ana, owner)
                self.assertEqual(mei.protected(owner), protected)
                self.assertEqual(vitals.total, 250. if protected else 200.)
                self.assertEqual(rear_vitals.total, rear_health if protected else rear_health - 50.)
                self.assertEqual(vitals.healing_block_until > fight.now, not protected)
                self.assertEqual(rear_vitals.healing_block_until > fight.now, not protected)
                self.assertEqual(ana.stats.value(stats.HERO_DAMAGE), 0. if protected else 100.)

    def test_allied_grenade_boosts_only_subsequent_cryo_healing(self):
        run, owner, ana, vitals = self.scene()
        fight = run.match.combat
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        ana.team = owner.team
        vitals.damage(240.)
        run.step({owner: (0, 0, 8)})
        for _ in range(20):
            run.step({})
        self.throw(run, ana, owner)
        self.assertAlmostEqual(vitals.healing_boost_until - fight.now, 4.)
        self.assertEqual(ana.stats.value(stats.HEALING), 100.)
        self.assertAlmostEqual(vitals.total, 110. + owner.stats.value(stats.HEALING))
        before = vitals.total
        for _ in range(10):
            run.step({})
        self.assertAlmostEqual(vitals.total - before, 12., delta=.41)

    def test_grenade_boost_expiry_restores_base_cryo_rate(self):
        run, owner, ana, vitals = self.scene()
        fight = run.match.combat
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        ana.team = owner.team
        vitals.damage(240.)
        self.throw(run, ana, owner)
        self.assertEqual(vitals.total, 110.)
        deadline = vitals.healing_boost_until
        while fight.now < deadline - .5:
            run.step({})
        run.step({owner: (0, 0, 8)})
        before = vitals.total
        for _ in range(10):
            run.step({})
        self.assertAlmostEqual(vitals.total - before, 12.)
        while fight.now < deadline + .05:
            run.step({})
        before = vitals.total
        for _ in range(10):
            run.step({})
        self.assertAlmostEqual(vitals.total - before, 8.)

    def test_projected_barrier_cleanses_antiheal_during_cryo_without_backfill(self):
        for skipped in (False, True):
            with self.subTest(skipped=skipped):
                run, owner, ana, vitals = self.scene()
                zarya = self.projector(run, owner, ana)
                fight = run.match.combat
                vitals.damage(130.)
                self.throw(run, ana, owner)
                deadline = vitals.healing_block_until
                owner.body_script.ult_seconds = 0
                owner.body_script.set_ult_charge(0.)
                charge = owner.body_script.component.entity_var(342)
                run.step({owner: (0, 0, 8)})
                for _ in range(30):
                    run.step({})
                self.assertTrue(mei.protected(owner))
                self.assertEqual(vitals.total, 70.)
                self.assertEqual(charge.value(), 0.)

                self.project_barrier(run, zarya, owner)
                self.assertEqual(vitals.healing_block_until, 0.)
                self.assertLessEqual(vitals.total, 70.8 + 1e-9)
                self.assertTrue(mei.protected(owner))
                health = vitals.total
                if skipped:
                    run.frame += 19
                    run.step({})
                else:
                    for _ in range(20):
                        run.step({})
                self.assertLess(fight.now, deadline)
                self.assertAlmostEqual(vitals.total - health, 16.)
                self.assertAlmostEqual(owner.stats.value(stats.HEALING), vitals.total - 70.)
                self.assertAlmostEqual(charge.value(), vitals.total - 70., places=3)

    def test_existing_projected_barrier_blocks_grenade_damage_and_antiheal(self):
        run, owner, ana, vitals = self.scene()
        zarya = self.projector(run, owner, ana)
        bubble = self.project_barrier(run, zarya, owner)
        self.throw(run, ana, owner)
        self.assertEqual(bubble.health, 150.)
        self.assertEqual(vitals.total, 250.)
        self.assertEqual(vitals.healing_block_until, 0.)

    def test_projected_barrier_resumes_field_pulses_before_antiheal_expiry(self):
        run, owner, ana, vitals = self.scene()
        zarya = self.projector(run, owner, ana)
        soldier = self.field_caster(run, owner)
        fight = run.match.combat
        vitals.damage(160.)
        self.throw(run, ana, owner)
        deadline = vitals.healing_block_until
        self.open_field(run, soldier)
        for _ in range(10):
            run.step({})
        self.assertEqual(vitals.total, 40.)
        self.assertEqual(soldier.stats.value(stats.HEALING), 0.)

        self.project_barrier(run, zarya, owner)
        self.assertEqual(vitals.total, 40.)
        run.frame += 9
        run.step({})
        self.assertLess(fight.now, deadline)
        healing = 10 * projectiles.HEAL_PER_SECOND * combat.TICK
        self.assertAlmostEqual(vitals.total, 40. + healing)
        self.assertAlmostEqual(soldier.stats.value(stats.HEALING), healing)

    def test_projected_barrier_keeps_existing_grenade_heal_boost(self):
        run, owner, ana, vitals = self.scene()
        zarya = self.projector(run, owner, ana)
        fight = run.match.combat
        healer = run.match.add_player(20, "Allied Ana", ANA, owner.team, False)
        in_world(run.match, healer)
        run.players.append(healer)
        self.assertTrue(run.match.switch_hero(healer, heroes()[ANA]))
        x, y, z = owner.mover.position
        fight.walls.place_player(healer, (x, y, z + 3.))
        for _ in range(30):
            run.step({})
        vitals.damage(130.)
        self.throw(run, ana, owner)
        self.throw(run, healer, owner)
        self.assertEqual(vitals.total, 70.)
        boost = vitals.healing_boost_until
        self.assertGreater(boost, fight.now)

        self.project_barrier(run, zarya, owner)
        self.assertEqual(vitals.healing_block_until, 0.)
        self.assertEqual(vitals.healing_boost_until, boost)
        run.step({owner: (0, 0, 8)})
        health = vitals.total
        for _ in range(10):
            run.step({})
        self.assertAlmostEqual(vitals.total - health, 12.)

    def test_biotic_field_respects_antiheal_across_skipped_expiry(self):
        for skipped in (False, True):
            with self.subTest(skipped=skipped):
                run, owner, ana, vitals = self.scene()
                fight = run.match.combat
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                ana.team = 1 - owner.team
                soldier = self.field_caster(run, owner)
                vitals.damage(160.)
                self.throw(run, ana, owner)
                self.assertEqual(vitals.total, 40.)
                deadline = vitals.healing_block_until
                expires = fight.tick + round((deadline - fight.now) / combat.TICK)
                self.open_field(run, soldier)
                while run.frame < expires - 10:
                    run.step({})
                self.assertGreater(deadline, fight.now)
                self.assertEqual(vitals.total, 40.)
                self.assertEqual(soldier.stats.value(stats.HEALING), 0.)
                if skipped:
                    run.frame += 19
                    run.step({})
                else:
                    for _ in range(20):
                        run.step({})
                pulse = projectiles.HEAL_PER_SECOND * combat.TICK
                self.assertAlmostEqual(vitals.total, 40. + 11 * pulse)
                self.assertAlmostEqual(soldier.stats.value(stats.HEALING), 22 * pulse)

    def test_biotic_field_uses_status_at_each_pulse_across_grenade_impact(self):
        for ally in (False, True):
            for skipped in (False, True):
                with self.subTest(ally=ally, skipped=skipped):
                    run, owner, ana, vitals = self.scene()
                    fight = run.match.combat
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    ana.team = owner.team if ally else 1 - owner.team
                    soldier = self.field_caster(run, owner)
                    vitals.damage(150.)
                    self.open_field(run, soldier)
                    x, y, z = owner.mover.position
                    aim = fixtures.aim(fight._eye(ana), (x, y + 1.1, z))
                    run.step({ana: (*aim, ABILITY_2)})
                    for _ in range(30):
                        if any(shot.definition == ana_grenade.DEFINITION for shot in fight.rockets):
                            break
                        run.step({})
                    run.step({})
                    [grenade] = [shot for shot in fight.rockets if shot.definition == ana_grenade.DEFINITION]
                    self.assertEqual(grenade.last_tick, run.frame)
                    self.assertFalse(vitals.healing_block_until or vitals.healing_boost_until)
                    first = run.frame + 1
                    health = vitals.total
                    credited = soldier.stats.value(stats.HEALING)
                    if skipped:
                        run.frame += 19
                        run.step({})
                    else:
                        for _ in range(20):
                            run.step({})
                    deadline = vitals.healing_boost_until if ally else vitals.healing_block_until
                    impact = fight.tick + round((deadline - ana_grenade.DURATION - fight.now) / combat.TICK)
                    self.assertGreater(impact, first)
                    self.assertLess(impact, run.frame)
                    before = impact - first
                    after = run.frame - impact + 1
                    pulse = projectiles.HEAL_PER_SECOND * combat.TICK
                    healing = (before + after * (1.5 if ally else 0.)) * pulse
                    self.assertAlmostEqual(vitals.total, health + healing + (100. if ally else -50.))
                    self.assertAlmostEqual(soldier.stats.value(stats.HEALING) - credited, healing)

    def projector(self, run, owner, ana):
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        ana.team = 1 - owner.team
        zarya = run.match.add_player(18, "Zarya", zarya_shields.HERO, owner.team, False)
        in_world(run.match, zarya)
        run.players.append(zarya)
        self.assertTrue(run.match.switch_hero(zarya, heroes()[zarya_shields.HERO]))
        x, y, z = owner.mover.position
        run.match.combat.walls.place_player(zarya, (x - 5., y, z))
        for _ in range(30):
            run.step({})
        return zarya

    def project_barrier(self, run, zarya, owner):
        fight = run.match.combat
        aim = fixtures.aim(fight._eye(zarya), fight._eye(owner))
        run.step({zarya: (*aim, ABILITY_2)})
        bubble = fight.shields.shielded(owner)
        self.assertIsNotNone(bubble)
        self.assertIs(bubble.owner, zarya)
        self.assertEqual(bubble.health, 200.)
        return bubble

    def field_caster(self, run, owner):
        soldier = run.match.add_player(19, "Soldier", SOLDIER, owner.team, False)
        in_world(run.match, soldier)
        run.players.append(soldier)
        self.assertTrue(run.match.switch_hero(soldier, heroes()[SOLDIER]))
        x, y, z = owner.mover.position
        run.match.combat.walls.place_player(soldier, (x + 1.5, y, z))
        for _ in range(30):
            run.step({})
        return soldier

    def open_field(self, run, soldier):
        fight = run.match.combat
        run.step({soldier: (0, 0, ABILITY_2)})
        for _ in range(80):
            if any(shot.player is soldier and shot.field_tick is not None
                   for shot in fight.projectiles.shots):
                return
            run.step({})
        self.fail("Biotic Field did not open")
