"""Actual commands against the declared Transcendence/Cryo self-protection bridge."""

import unittest
from dataclasses import replace
from unittest.mock import patch

import test_game_reaper as reaper_tests
import test_mccree_combat as melee_tests
from test_game_combat import aim

from ow174.game import ana, combat, defenses, reaper, stats
from ow174.game.content import heroes


class ReaperDefenseTests(unittest.TestCase):
    def scene(self, hero, *, third=False):
        names = ("Reaper", "Defender", "Behind") if third else ("Reaper", "Defender")
        run, owner, defender, _ = reaper_tests.ReaperTests().scenario(names=names)
        run.match.switch_hero(defender, heroes()[hero])
        x, y, z = owner.mover.position
        defender.mover.position[:] = (x, y, z + 1.5)
        defender.mover.body = None
        if third:
            run.players[2].mover.position[:] = (x, y, z + 3.5)
            run.players[2].mover.body = None
        for _ in range(20):
            run.step({})
        fight = run.match.combat
        targets = {t.entity: t for t in fight._targets()}
        for target in targets.values():
            fight.vitals_of(target)
        owner.body_script.ult_seconds = 0
        owner.body_script.set_ult_charge(0)
        fight.vitals[owner.body].damage(100)
        return run, owner, defender, targets[defender.body]

    def cast(self, run, defender):
        zen = defender.hero.guid == defenses.ZENYATTA
        if zen:
            defender.body_script.set_ult_charge(defender.body_script.component.vars[338].value())
        run.step({defender: (0, 0, 32 if zen else 8)})
        self.assertTrue(defenses.invulnerable(defender))
        if zen:
            self.assertEqual(defender.body_script.component.vars[342].value(), 0)

    def fire(self, run, owner, defender):
        reaper_tests.ReaperTests().fire(run, owner, defender)

    def test_primary_before_during_and_after_authored_defensive_scope(self):
        for hero, inside, after in ((defenses.ZENYATTA, 370, 12), (defenses.MEI, 244, 12)):
            with self.subTest(hero=hero):
                run, owner, defender, target = self.scene(hero)
                fight = run.match.combat
                initial = fight.vitals_of(target).total
                self.fire(run, owner, defender)
                self.assertEqual(fight.vitals_of(target).total, initial - 120)
                self.assertEqual(owner.body_script.component.vars[342].value(), 120)

                run, owner, defender, target = self.scene(hero)
                fight = run.match.combat
                initial = fight.vitals_of(target).total
                self.cast(run, defender)
                self.fire(run, owner, defender)
                self.assertEqual(fight.vitals_of(target).total, initial)
                self.assertEqual(fight.vitals[owner.body].total, 150)
                self.assertEqual(owner.body_script.component.vars[342].value(), 0)
                self.assertFalse(owner.client.messages(combat.HIT))
                for _ in range(inside):
                    run.step({})
                self.assertTrue(defenses.invulnerable(defender))
                for _ in range(after):
                    run.step({})
                self.assertFalse(defenses.invulnerable(defender))
                self.fire(run, owner, defender)
                self.assertEqual(fight.vitals_of(target).total, initial - 120)

    def test_actual_sleep_dart_resolves_self_immunity_expiry_across_world_gap(self):
        def scene(hero):
            run, owner, defender, target = self.scene(hero, third=True)
            caster = run.players[2]
            self.assertTrue(run.match.switch_hero(caster, heroes()[ana.HERO]))
            run.match.game_map = replace(run.match.game_map, free_for_all=False)
            owner.team = caster.team = 0
            defender.team = 1
            x, y, z = owner.mover.position
            caster.mover.position[:] = (x, y, z - 6)
            caster.mover.body = None
            self.cast(run, defender)
            fight = run.match.combat
            heading = aim(fight._eye(caster), (x, y + 1, z + 1.5))
            return run, owner, defender, caster, fight.vitals_of(target), heading

        def advance(run, tick):
            run.frame = tick
            run.match.update(tick * combat.TICK, tick, frames=False)

        def launch(run, caster, heading):
            fight = run.match.combat
            run.step({caster: (*heading, 8)})
            for _ in range(30):
                if fight.rockets:
                    break
                run.step({caster: (*heading, 0)})
            dart, = fight.rockets
            self.assertEqual(dart.direct.effect, ana.SLEEP_EFFECT)
            return dart

        for hero in (defenses.ZENYATTA, defenses.MEI):
            run, _, defender, caster, pool, heading = scene(hero)
            fight = run.match.combat
            initial = pool.total
            fired = run.frame
            launch(run, caster, heading)
            for _ in range(25):
                advance(run, run.frame + 1)
                if not fight.rockets:
                    break
            self.assertFalse(fight.rockets)
            self.assertEqual(pool.total, initial)
            self.assertNotIn(defender.body, fight.ana_sleeps)
            travel = run.frame - fired
            for _ in range(400):
                advance(run, run.frame + 1)
                if not defenses.invulnerable(defender):
                    break
            self.assertFalse(defenses.invulnerable(defender))
            expiry = run.frame

            for protected in (True, False):
                outcomes = []
                for skipped in (False, True):
                    with self.subTest(hero=hero, protected=protected, skipped=skipped):
                        run, owner, defender, caster, pool, heading = scene(hero)
                        fight = run.match.combat
                        contact = expiry - int(protected)
                        advance(run, contact - travel)
                        dart = launch(run, caster, heading)
                        advance(run, run.frame + 1)
                        self.assertEqual(dart.last_tick, run.frame)
                        self.assertTrue(defenses.invulnerable(defender))
                        impacts = []

                        def observe(missile, *, world=fight, target=defender, hits=impacts,
                                    landed=fight._landed):
                            hits.append((world.tick, defenses.invulnerable(target)))
                            landed(missile)

                        end = expiry + 35
                        with patch.object(fight, "_landed", side_effect=observe):
                            while run.frame < end:
                                advance(run, end if skipped else run.frame + 1)
                        self.assertEqual(impacts, [(contact, protected)])
                        self.assertFalse(fight.rockets)
                        self.assertEqual(pool.total, initial if protected else initial - 5)
                        self.assertEqual(caster.stats.value(stats.HERO_DAMAGE), 0 if protected else 5)
                        sleep = fight.ana_sleeps.get(defender.body)
                        if protected:
                            self.assertIsNone(sleep)
                        else:
                            self.assertIsNotNone(sleep)
                            self.assertEqual((sleep["start"], sleep["end"]), (contact, contact + 313))
                        before = pool.total
                        self.fire(run, owner, defender)
                        self.assertEqual(before - pool.total, 120)
                        own = fight.vitals[owner.body]
                        self.assertAlmostEqual(own.total, 150 + 120 * reaper.REAPING_RATIO)
                        if sleep is not None:
                            self.assertTrue(sleep["waking"])
                        outcomes.append((impacts, sleep, own.total))
                if len(outcomes) == 2:
                    self.assertEqual(outcomes[0], outcomes[1])

    def test_melee_and_full_blossom_cannot_damage_or_reap_protected_body(self):
        for hero in (defenses.ZENYATTA, defenses.MEI):
            for attack in ("melee", "blossom"):
                with self.subTest(hero=hero, attack=attack):
                    run, owner, defender, target = self.scene(hero)
                    fight = run.match.combat
                    initial = fight.vitals_of(target).total
                    self.cast(run, defender)
                    if attack == "blossom":
                        owner.body_script.set_ult_charge(2100)
                    run.step({owner: (0, 0, 0x800 if attack == "melee" else 32)})
                    for _ in range(195 if attack == "blossom" else 40):
                        run.step({})
                    self.assertEqual(fight.vitals_of(target).total, initial)
                    self.assertEqual(fight.vitals[owner.body].total, 150)
                    self.assertFalse(owner.client.messages(combat.HIT))
                    self.assertEqual(owner.body_script.component.vars[342].value(), 0)
                    self.assertIsNone(reaper.blossom_state(owner.body_script))

    def test_protected_bodies_still_intercept_pellets_before_an_enemy_behind_them(self):
        for hero in (defenses.ZENYATTA, defenses.MEI):
            with self.subTest(hero=hero):
                run, owner, defender, target = self.scene(hero, third=True)
                fight = run.match.combat
                initial = fight.vitals_of(target).total
                behind = run.players[2]
                self.cast(run, defender)
                self.assertIn(defender.body, [t.entity for t in fight._enemies(owner)])
                self.fire(run, owner, defender)
                self.assertEqual(fight.vitals_of(target).total, initial)
                self.assertEqual(fight.vitals[behind.body].total, 200)

    def test_mei_primary_cancel_removes_protection_and_hero_replacement_cannot_keep_it(self):
        for reason in ("cancel", "switch"):
            with self.subTest(reason=reason):
                run, owner, defender, target = self.scene(defenses.MEI)
                self.cast(run, defender)
                for _ in range(30):
                    run.step({})
                if reason == "cancel":
                    run.step({defender: (0, 0, 1)})
                else:
                    script = defender.body_script
                    run.match.switch_hero(defender, owner.hero)
                    self.assertTrue(script.retired)
                self.assertFalse(defenses.invulnerable(defender))
                if reason == "cancel":
                    self.fire(run, owner, defender)
                    self.assertEqual(run.match.combat.vitals_of(target).total, 130)

    def test_shared_melee_wall_fix_cannot_fund_reaping_or_ultimate_charge(self):
        fixture = melee_tests.McCreeMeleeTests()
        for npc in (False, True):
            for wall in (False, True):
                with self.subTest(npc=npc, wall=wall):
                    run, owner, target = fixture.arena(npc=npc, wall=wall)
                    origin = tuple(owner.mover.position)
                    run.match.switch_hero(owner, heroes()[reaper.HERO])
                    owner.mover.position[:] = origin
                    owner.mover.body = None
                    owner.body_script.ult_seconds = 0
                    owner.body_script.set_ult_charge(0)
                    for _ in range(20):
                        run.step({})
                    fight = run.match.combat
                    own = next(t for t in fight._targets() if t.owner is owner)
                    fight.vitals_of(own).damage(100)
                    initial = fixture.health(run, target)
                    fixture.strike(run, owner, target)
                    damage = 0 if wall else 30
                    self.assertEqual(fixture.health(run, target), initial - damage)
                    self.assertAlmostEqual(fight.vitals[owner.body].total,
                                           150 + damage * reaper.REAPING_RATIO)
                    self.assertEqual(owner.body_script.component.vars[342].value(), damage)

    def test_defensive_scopes_can_end_and_recast_after_their_resource_gate(self):
        for hero in (defenses.ZENYATTA, defenses.MEI):
            with self.subTest(hero=hero):
                run, _, defender, _ = self.scene(hero)
                if hero == defenses.ZENYATTA:
                    defender.body_script.set_ult_charge(0)
                    run.step({defender: (0, 0, 32)})
                    self.assertFalse(defenses.invulnerable(defender))
                    run.step({})
                self.cast(run, defender)
                for _ in range(400 if hero == defenses.ZENYATTA else 270):
                    run.step({})
                self.assertFalse(defenses.invulnerable(defender))
                if hero == defenses.MEI:
                    run.step({defender: (0, 0, 8)})
                    self.assertFalse(defenses.invulnerable(defender))
                    for _ in range(780):
                        run.step({})
                    self.assertFalse(defenses.invulnerable(defender))
                self.cast(run, defender)


if __name__ == "__main__":
    unittest.main()
