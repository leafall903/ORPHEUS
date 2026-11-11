"""Real Reaper and Orisa input through their build-104319 graphs."""

import unittest
from dataclasses import replace
from unittest.mock import patch

import test_game_reaper as reaper_tests
import test_reaper_control as control_tests
import test_reaper_sleep as sleep_tests
from test_game_combat import aim

from ow174.game import ana, combat, reaper, stats
from ow174.game.content import find_hero, heroes
from ow174.game.script.driver import ULT_CHARGE, ULT_COST

ORISA = 0x02E000000000013E


class ReaperFortifyTests(unittest.TestCase):
    def scene(self, *, names=("Reaper", "Orisa")):
        run, reaper, orisa, _ = reaper_tests.ReaperTests().scenario(names=names)
        self.assertTrue(run.match.switch_hero(orisa, heroes()[ORISA]))
        for _ in range(28):
            run.step({})
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is orisa)
        run.step({orisa: (0, 0, 8)})
        self.assertAlmostEqual(fight._fortify_scale(target), 0.6)
        return run, reaper, orisa, target

    def test_actual_fortify_shell_mitigates_before_armour_and_reaping(self):
        run, reaper, orisa, target = self.scene()
        fight = run.match.combat
        armour = fight.vitals_of(target)
        own = fight.vitals[reaper.body]
        own.damage(100)
        before = armour.total
        reaper_tests.ReaperTests().fire(run, reaper, orisa)
        self.assertAlmostEqual(before - armour.total, 36, places=4)
        self.assertAlmostEqual(own.total, 162.6, places=4)
        self.assertEqual(len(reaper.client.messages(combat.HIT)), 20)

    def test_actual_fortify_suppresses_shell_critical_flags_until_expiry(self):
        run, reaper, orisa, target = self.scene()
        fight = run.match.combat
        pool = combat.Vitals(orisa.body, 600)
        fight.vitals[orisa.body] = pool
        x, y, z = fight._eye(orisa)
        yaw, pitch = aim(fight._eye(reaper), (x, y - 0.12, z))
        run.step({reaper: (yaw, pitch, 1)})
        hits = reaper.client.messages(combat.HIT)
        self.assertGreater(len(hits), 0)
        self.assertFalse(any(hit["+0x78"]["+0x8"] & combat.CRITICAL for hit in hits))
        before = pool.total
        for _ in range(270):
            run.step({})
        self.assertIsNone(fight._fortify_scale(target))
        run.step({reaper: (yaw, pitch, 1)})
        after = reaper.client.messages(combat.HIT)[len(hits):]
        self.assertEqual(len(after), len(hits))
        self.assertTrue(any(hit["+0x78"]["+0x8"] & combat.CRITICAL for hit in after))
        self.assertGreater(before - pool.total, (600 - before) / 0.6)

    def test_actual_blossom_and_orisa_return_fire_during_fortify(self):
        run, reaper, orisa, target = self.scene()
        fight = run.match.combat
        pool = combat.Vitals(orisa.body, 1000)
        fight.vitals[orisa.body] = pool
        own = fight.vitals[reaper.body]
        x, y, z = reaper.mover.position
        yaw, pitch = aim(fight._eye(orisa), (x, y + 1, z))
        reaper.body_script.set_ult_charge(2100)
        run.step({reaper: (0, 0, 32)})
        self.assertAlmostEqual(pool.total, 979.6, places=4)
        for _ in range(20):
            run.step({orisa: (yaw, pitch, 1)})
        self.assertLess(own.total, 250)
        self.assertAlmostEqual(fight._fortify_scale(target), 0.6)
        for _ in range(180):
            run.step({})
        self.assertTrue(fight.reaper_blossoms[reaper.body]["ended"])
        self.assertAlmostEqual(pool.total, 694, places=4)

    def test_actual_earthshatter_stuns_reaper_but_fortify_only_takes_reduced_damage(self):
        run, owner, rein, _ = reaper_tests.ReaperTests().scenario(
            team_mode=True, names=("Reaper", "Reinhardt", "Orisa"),
        )
        orisa = run.players[2]
        self.assertTrue(run.match.switch_hero(rein, find_hero("Reinhardt")))
        self.assertTrue(run.match.switch_hero(orisa, heroes()[ORISA]))
        owner.team = orisa.team = 0
        rein.team = 1
        fight = run.match.combat
        x, y, z = owner.mover.position
        rein.mover.position[:] = (x, y, z + 6)
        orisa.mover.position[:] = (x + 1.5, y, z)
        rein.mover.body = orisa.mover.body = None
        for _ in range(25):
            run.step({})
        pool = fight.vitals[orisa.body] = combat.Vitals(orisa.body, 450)
        target = next(t for t in fight._targets() if t.owner is orisa)
        run.step({orisa: (0, 0, 8)})
        self.assertAlmostEqual(fight._fortify_scale(target), 0.6)
        heading = aim(fight._eye(rein), (x, y + 1, z))
        script = rein.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        run.step({rein: (*heading, 32)})
        self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)
        for _ in range(85):
            run.step({rein: (*heading, 0)})
            self.assertFalse(fight.is_stunned(orisa))
        self.assertEqual(fight.vitals[owner.body].total, 200)
        self.assertTrue(fight.is_stunned(owner))
        self.assertAlmostEqual(pool.total, 420, places=4)
        self.assertAlmostEqual(fight._fortify_scale(target), 0.6)

    def test_actual_flashbang_deals_damage_but_fortify_refuses_stun(self):
        fixture = control_tests.ReaperControlTests()
        run, caster, orisa = fixture.scene(hero=ORISA)
        fight = run.match.combat
        fight.vitals[orisa.body] = combat.Vitals(orisa.body, 450)
        run.step({orisa: (0, 0, 8)})
        fixture.step(run, caster, orisa, cast=16)
        for _ in range(20):
            fixture.step(run, caster, orisa)
        self.assertAlmostEqual(fixture.health(run, orisa), 435, places=4)
        self.assertFalse(fight.is_stunned(orisa))

    def test_actual_sleep_dart_deals_damage_but_fortify_refuses_sleep(self):
        fixture = sleep_tests.ReaperSleepTests()
        run, orisa, ana = fixture.scene()
        position = tuple(orisa.mover.position)
        self.assertTrue(run.match.switch_hero(orisa, heroes()[ORISA]))
        orisa.mover.position[:] = position
        for _ in range(28):
            run.step({})
        fight = run.match.combat
        fight.vitals[orisa.body] = combat.Vitals(orisa.body, 450)
        run.step({orisa: (0, 0, 8)})
        fixture.sleep(run, ana, orisa)
        self.assertAlmostEqual(fixture.health(run, orisa), 447, places=4)
        self.assertNotIn(orisa.body, fight.ana_sleeps)

    def test_actual_sleep_dart_resolves_fortify_expiry_across_world_gap(self):
        def scene():
            run, owner, orisa, _ = self.scene(names=("Reaper", "Orisa", "Ana"))
            caster = run.players[2]
            self.assertTrue(run.match.switch_hero(caster, heroes()[ana.HERO]))
            run.match.game_map = replace(run.match.game_map, free_for_all=False)
            owner.team = caster.team = 0
            orisa.team = 1
            x, y, z = owner.mover.position
            orisa.mover.position[:] = (x, y, z + 1.5)
            caster.mover.position[:] = (x, y, z - 6)
            orisa.mover.body = caster.mover.body = None
            fight = run.match.combat
            target = next(t for t in fight._targets() if t.owner is orisa)
            pool = fight.vitals[orisa.body] = combat.Vitals(orisa.body, 450)
            heading = aim(fight._eye(caster), (x, y + 1, z + 1.5))
            return run, owner, orisa, caster, target, pool, heading

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

        run, _, _, caster, target, pool, heading = scene()
        fight = run.match.combat
        started = run.frame
        launch(run, caster, heading)
        for _ in range(25):
            advance(run, run.frame + 1)
            if not fight.rockets:
                break
        self.assertFalse(fight.rockets)
        self.assertAlmostEqual(pool.total, 447)
        travel = run.frame - started
        for _ in range(260):
            advance(run, run.frame + 1)
            if fight._fortify_scale(target) is None:
                break
        self.assertIsNone(fight._fortify_scale(target))
        expiry = run.frame

        for protected in (True, False):
            outcomes = []
            for skipped in (False, True):
                with self.subTest(protected=protected, skipped=skipped):
                    run, owner, orisa, caster, target, pool, heading = scene()
                    fight = run.match.combat
                    contact = expiry - int(protected)
                    advance(run, contact - travel)
                    dart = launch(run, caster, heading)
                    advance(run, run.frame + 1)
                    self.assertEqual(dart.last_tick, run.frame)
                    self.assertIsNotNone(fight._fortify_scale(target))
                    impacts = []

                    def observe(missile, *, world=fight, recipient=target, hits=impacts,
                                landed=fight._landed):
                        hits.append((world.tick, world._fortify_scale(recipient) is not None))
                        landed(missile)

                    end = expiry + 35
                    with patch.object(fight, "_landed", side_effect=observe):
                        while run.frame < end:
                            advance(run, end if skipped else run.frame + 1)
                    self.assertEqual(impacts, [(contact, protected)])
                    self.assertFalse(fight.rockets)
                    self.assertAlmostEqual(pool.total, 447 if protected else 445)
                    self.assertAlmostEqual(caster.stats.value(stats.HERO_DAMAGE), 3 if protected else 5)
                    sleep = fight.ana_sleeps.get(orisa.body)
                    if protected:
                        self.assertIsNone(sleep)
                    else:
                        self.assertIsNotNone(sleep)
                        self.assertEqual((sleep["start"], sleep["end"]), (contact, contact + 313))
                    own = fight.vitals[owner.body]
                    own.damage(100)
                    before = pool.total
                    reaper_tests.ReaperTests().fire(run, owner, orisa)
                    self.assertAlmostEqual(before - pool.total, 120)
                    self.assertAlmostEqual(own.total, 150 + 120 * reaper.REAPING_RATIO)
                    if sleep is not None:
                        self.assertTrue(sleep["waking"])
                    outcomes.append((impacts, sleep, own.total))
            if len(outcomes) == 2:
                self.assertEqual(outcomes[0], outcomes[1])

    def test_actual_fortify_cooldown_refuses_then_accepts_fresh_input(self):
        run, _, orisa, target = self.scene()
        fight = run.match.combat
        instance = next(i for i in orisa.body_script.component.instances.values()
                        if i.graph.index == 0x0CF4)
        ability = instance.node_state(instance.graph.node(3))
        for _ in range(270):
            run.step({})
        self.assertIsNone(fight._fortify_scale(target))
        self.assertGreater(ability.remaining(), 1)
        run.step({orisa: (0, 0, 8)})
        self.assertIsNone(fight._fortify_scale(target))
        for _ in range(1000):
            run.step({})
            if ability.remaining() == 0:
                break
        self.assertEqual(ability.remaining(), 0)
        self.assertIsNone(fight._fortify_scale(target))
        run.step({orisa: (0, 0, 8)})
        self.assertAlmostEqual(fight._fortify_scale(target), 0.6)
