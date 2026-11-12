"""Actual Ana Sleep Dart against Zenyatta weapon, ultimate and wake timing."""
import unittest
from itertools import product

from test_game_combat import Run, aim

from ow174.game import combat
from ow174.game.barriers import PROJECTED, ZARYA
from ow174.game.commands import FIRE, SECONDARY
from ow174.game.content import SOLDIER, heroes
from ow174.game.flashbang import HERO as MCCREE
from ow174.game.zenyatta import HERO as ZEN

ANA = 0x02E000000000013B


class ZenyattaSleepTests(unittest.TestCase):
    def arena(self):
        run = Run(names=("Ana", "Zen", "Soldier"))
        ana, zen, soldier = run.players
        run.match.bots.clear()
        x, y, z = ana.mover.position
        for player, hero, team, offset in (
            (ana, ANA, 0, (0, 0)), (zen, ZEN, 1, (0, 4)), (soldier, SOLDIER, 0, (3, 0))
        ):
            self.assertTrue(run.match.switch_hero(player, heroes()[hero]))
            player.team = team
            player.mover.position = [x+offset[0], y, z+offset[1]]
            player.mover.body = None
        for _ in range(30):
            run.step({})
        return run, ana, zen, soldier

    def aimed(self, run, source, target, buttons):
        fight = run.match.combat
        x, y, z = target.mover.position
        return (*aim(fight._eye(source), (x, y+1.15, z)), buttons)

    def dart(self, run, ana, zen, *, zen_buttons=0, immune=False):
        fight = run.match.combat
        saw_dart = False
        for frame in range(55):
            run.step({ana: self.aimed(run, ana, zen, 8 if frame == 0 else 0),
                      zen: self.aimed(run, zen, ana, zen_buttons)})
            saw_dart |= any(r.direct is not None and r.direct.effect == combat.ANA_SLEEP_EFFECT
                            for r in fight.rockets)
            if zen.body in fight.sleeps:
                self.assertFalse(immune)
                return fight.sleeps[zen.body]
        self.assertTrue(saw_dart)
        self.assertTrue(immune)

    def test_actual_dart_cancels_charged_orbs_and_blocks_full_charge_ultimate(self):
        run, ana, zen, soldier = self.arena()
        fight = run.match.combat
        for _ in range(60):
            run.step({zen: self.aimed(run, zen, ana, SECONDARY)})
        weapon = next(i for i in zen.body_script.component.instances.values() if i.graph.index == 0x0164)
        self.assertGreater(weapon.vars[1392].value(), 0)
        before_dart = weapon.vars[53].value()
        start, end, waking = self.dart(run, ana, zen, zen_buttons=SECONDARY)
        self.assertEqual(end-start, 313)
        self.assertFalse(waking)
        self.assertEqual(fight.vitals[zen.body].total, 195)
        self.assertEqual(weapon.vars[1392].value(), 0)
        ammo = weapon.vars[53].value()
        self.assertLessEqual(ammo, before_dart)
        zen.body_script.set_ult_charge(zen.body_script.component.entity_var(338).value())
        run.step({zen: self.aimed(run, zen, ana, FIRE | 0x20),
                  soldier: self.aimed(run, soldier, zen, FIRE)})
        self.assertFalse(fight.zenyatta.ultimate(zen))
        self.assertEqual(fight.sleeps[zen.body], (start, end, False))
        while run.frame <= start+35:
            run.step({})
        run.step({soldier: self.aimed(run, soldier, zen, FIRE)})
        wake = fight.sleeps[zen.body]
        self.assertTrue(wake[2])
        self.assertLess(wake[1], end)
        self.assertEqual(weapon.vars[53].value(), ammo)
        for _ in range(10):
            run.step({})
        run.step({soldier: self.aimed(run, soldier, zen, FIRE)})
        self.assertEqual(fight.sleeps[zen.body], wake)
        while run.frame < wake[1]-1:
            run.step({})
            self.assertTrue(fight.is_stunned(zen))
        run.step({})
        self.assertFalse(fight.is_stunned(zen))
        run.step({zen: self.aimed(run, zen, ana, FIRE)})
        for _ in range(15):
            run.step({})
        self.assertEqual(weapon.vars[53].value(), ammo-1)
        self.assertTrue(zen.client.messages(combat.HIT))

    def test_sleep_cancels_charge_and_natural_wake_allows_a_fresh_full_volley(self):
        run, ana, zen, _ = self.arena()
        fight = run.match.combat
        weapon = next(i for i in zen.body_script.component.instances.values() if i.graph.index == 0x0164)
        for _ in range(105):
            run.step({zen: self.aimed(run, zen, ana, SECONDARY)})
        self.assertGreaterEqual(weapon.vars[1392].value(), 3)
        before_dart = weapon.vars[53].value()
        _, end, waking = self.dart(run, ana, zen, zen_buttons=SECONDARY)
        self.assertFalse(waking)
        self.assertEqual(weapon.vars[1392].value(), 0)
        ammo = weapon.vars[53].value()
        self.assertLessEqual(ammo, before_dart)

        while run.frame < end+5:
            run.step({})
            self.assertEqual(weapon.vars[53].value(), ammo)
            self.assertFalse(any(rocket.shooter is zen for rocket in fight.rockets))
        self.assertNotIn(zen.body, fight.sleeps)
        self.assertFalse(zen.client.messages(combat.HIT))

        charge = zen.body_script.component.entity_var(342)
        start_charge, start_frame = charge.value(), run.frame
        for _ in range(130):
            run.step({zen: self.aimed(run, zen, ana, SECONDARY)})
        self.assertEqual(weapon.vars[1392].value(), 4)
        for _ in range(65):
            run.step({zen: self.aimed(run, zen, ana, 0)})
        hits = [event["+0x78"] for event in zen.client.messages(combat.HIT)]
        self.assertEqual([(hit["+0x0"]["+0x0"], hit["+0x4"]) for hit in hits],
                         [(ana.body, damage) for damage in (48, 48, 48, 48, 8)])
        self.assertEqual(fight.vitals[ana.body].total, 0)
        self.assertEqual(weapon.vars[53].value(), ammo-5)
        self.assertAlmostEqual(charge.value()-start_charge,
                               200+(run.frame-start_frame)*.016*5, places=2)

    def test_sleep_during_release_keeps_airborne_orbs_and_cancels_pending_orbs(self):
        run, ana, zen, soldier = self.arena()
        fight = run.match.combat
        x, y, z = zen.mover.position
        soldier.mover.position = [x, y, z+24]
        soldier.mover.body = None
        for _ in range(10):
            run.step({})
        weapon = next(i for i in zen.body_script.component.instances.values() if i.graph.index == 0x0164)
        charge = zen.body_script.component.entity_var(342)
        before_charge, before_frame = charge.value(), run.frame

        # At these distances the dart lands after the first two launches, before either impact.
        for tick in range(160):
            run.step({ana: self.aimed(run, ana, zen, 8 if tick == 118 else 0),
                      zen: self.aimed(run, zen, soldier, SECONDARY if tick < 130 else 0)})
            if zen.body in fight.sleeps:
                break
        else:
            self.fail("Sleep Dart did not intercept the running release")

        launched = [rocket for rocket in fight.rockets if rocket.shooter is zen]
        self.assertEqual(len(launched), 2)
        self.assertFalse(zen.client.messages(combat.HIT))
        self.assertEqual(weapon.vars[1392].value(), 0)
        self.assertEqual(weapon.vars[53].value(), 15)
        _, wake, _ = fight.sleeps[zen.body]
        while run.frame < wake+10:
            run.step({})
            self.assertTrue(all(any(rocket is previous for previous in launched)
                                for rocket in fight.rockets if rocket.shooter is zen))
            self.assertEqual(weapon.vars[53].value(), 15)
        self.assertNotIn(zen.body, fight.sleeps)
        self.assertFalse(any(rocket.shooter is zen for rocket in fight.rockets))
        hits = [notice["+0x78"] for notice in zen.client.messages(combat.HIT)]
        self.assertEqual([(hit["+0x0"]["+0x0"], hit["+0x4"]) for hit in hits],
                         [(soldier.body, 48)]*2)
        self.assertEqual(fight.vitals[soldier.body].total, 104)
        self.assertAlmostEqual(charge.value()-before_charge,
                               96+(run.frame-before_frame)*.016*5, places=2)

    def test_actual_dart_cannot_damage_or_sleep_transcendent_zen(self):
        run, ana, zen, _ = self.arena()
        fight = run.match.combat
        zen.body_script.set_ult_charge(zen.body_script.component.entity_var(338).value())
        run.step({zen: self.aimed(run, zen, ana, 0x20)})
        self.assertTrue(fight.zenyatta.ultimate(zen))
        self.dart(run, ana, zen, immune=True)
        self.assertTrue(fight.zenyatta.ultimate(zen))
        self.assertEqual(fight.vitals[zen.body].total, 200)
        self.assertNotIn(zen.body, fight.sleeps)

    def test_applied_dart_survives_caster_replacement_but_not_target_replacement(self):
        run, ana, zen, _ = self.arena()
        fight = run.match.combat
        sleep = self.dart(run, ana, zen)
        body = zen.body
        self.assertTrue(run.match.switch_hero(ana, heroes()[SOLDIER]))
        for _ in range(5):
            run.step({})
        self.assertEqual(fight.sleeps[body], sleep)
        self.assertTrue(fight.is_stunned(zen))
        self.assertTrue(run.match.switch_hero(zen, heroes()[SOLDIER]))
        self.assertNotEqual(zen.body, body)
        self.assertNotIn(body, fight.sleeps)
        self.assertFalse(fight.is_stunned(zen))

    def test_actual_projected_barrier_absorbs_dart_damage_and_sleep(self):
        run, ana, zen, ally = self.arena()
        self.assertTrue(run.match.switch_hero(ally, heroes()[ZARYA]))
        ally.team = zen.team
        x, y, z = zen.mover.position
        ally.mover.position = [x+3, y, z+2]
        ally.mover.body = None
        for _ in range(20):
            run.step({})
        fight = run.match.combat
        angles = aim(fight._eye(ally), (x, y+1.5, z))
        run.step({ally: (*angles, 0x10)})
        bubble = fight.barriers.bubbles[ally.body, PROJECTED]
        self.assertEqual(bubble.hp, 200)
        self.dart(run, ana, zen, immune=True)
        self.assertEqual(bubble.hp, 195)
        self.assertEqual(fight.vitals[zen.body].total, 200)
        self.assertNotIn(zen.body, fight.sleeps)

    def sleep_then_flashbang(self, *, hold_secondary=False, flashbang=True):
        run, ana, zen, caster = self.arena()
        self.assertTrue(run.match.switch_hero(caster, heroes()[MCCREE]))
        x, y, z = zen.mover.position
        caster.mover.position = [x+3, y, z]
        caster.mover.body = None
        for _ in range(20):
            run.step({})
        fight = run.match.combat
        buttons = SECONDARY if hold_secondary else 0
        held = {zen: self.aimed(run, zen, ana, buttons)}
        if hold_secondary:
            for _ in range(105):
                run.step(held)
        start, end, _ = self.dart(run, ana, zen, zen_buttons=buttons)
        if not flashbang:
            return run, ana, zen, end, end, held
        while run.frame < start+36:
            run.step(held)
        run.step({**held, caster: self.aimed(run, caster, zen, 0x10)})
        for _ in range(30):
            run.step({**held, caster: self.aimed(run, caster, zen, 0)})
            if zen.body in fight.stuns:
                break
        deadline = fight.stuns[zen.body]
        _, wake, waking = fight.sleeps[zen.body]
        self.assertTrue(waking)
        self.assertLess(wake, min(deadline, end))
        self.assertEqual(fight.vitals[zen.body].total, 170)
        return run, ana, zen, wake, deadline, held

    def test_late_flashbang_starts_wake_but_preserves_its_own_later_deadline(self):
        run, _, zen, wake, deadline, _ = self.sleep_then_flashbang()
        fight = run.match.combat
        while run.frame < wake:
            run.step({})
        self.assertNotIn(zen.body, fight.sleeps)
        self.assertTrue(fight.is_stunned(zen))
        while run.frame < deadline:
            run.step({})
        self.assertFalse(fight.is_stunned(zen))

    def test_held_secondary_restarts_only_after_sleep_and_flashbang_end(self):
        for flashbang, skipped in product((False, True), repeat=2):
            with self.subTest(flashbang=flashbang, skipped=skipped):
                run, ana, zen, wake, deadline, held = self.sleep_then_flashbang(
                    hold_secondary=True, flashbang=flashbang)
                fight = run.match.combat
                weapon = next(i for i in zen.body_script.component.instances.values() if i.graph.index == 0x0164)
                charge_node = weapon.graph.node(122)
                ammo = weapon.vars[53].value()
                while run.frame < deadline - 3:
                    run.step(held)
                    self.assertTrue(fight.is_stunned(zen))
                    self.assertEqual(zen.body in fight.sleeps, run.frame < wake)
                    self.assertEqual(weapon.vars[1392].value(), 0)
                    self.assertEqual(weapon.vars[53].value(), ammo)
                    self.assertFalse(any(orb.shooter is zen for orb in fight.rockets))
                if skipped:
                    run.frame += 2
                while run.frame < deadline:
                    run.step(held)
                self.assertFalse(fight.is_stunned(zen))
                charge = weapon.states[charge_node.state]
                self.assertTrue(charge.active)
                self.assertEqual(charge.start, deadline * 16)
                self.assertEqual(weapon.vars[1392].value(), 1)
                self.assertEqual(weapon.vars[53].value(), ammo - 1)
                self.assertFalse(zen.client.messages(combat.HIT))
                for _ in range(130):
                    run.step(held)
                self.assertEqual(weapon.vars[1392].value(), 5)
                for _ in range(60):
                    run.step({zen: self.aimed(run, zen, ana, 0)})
                hits = [message["+0x78"] for message in zen.client.messages(combat.HIT)]
                self.assertEqual([(hit["+0x0"]["+0x0"], hit["+0x4"]) for hit in hits],
                                 [(ana.body, amount) for amount in (48, 48, 48, 48, 8)])
                self.assertEqual(weapon.vars[53].value(), ammo - 5)

    def test_actual_reflected_dart_targets_zen_and_preserves_transcendence(self):
        for protected in (False, True):
            with self.subTest(protected=protected):
                self.check_reflected_dart(protected)

    def check_reflected_dart(self, protected):
        run, ana, zen, genji = self.arena()
        self.assertTrue(run.match.switch_hero(genji, heroes()[0x02E0000000000029]))
        genji.team, zen.team = 1, 0
        x, y, z = ana.mover.position
        for player, dx, dz in ((ana, -1.6, 0), (zen, 0, 0), (genji, 0, 5)):
            player.mover.position = [x+dx, y, z+dz]
            player.mover.body = None
        for _ in range(20):
            run.step({})
        fight = run.match.combat
        if protected:
            zen.body_script.set_ult_charge(zen.body_script.component.entity_var(338).value())
            run.step({zen: (0, 0, 0x20)})
            self.assertTrue(fight.zenyatta.ultimate(zen))
        guard = {genji: self.aimed(run, genji, zen, 0x10)}
        for _ in range(10):
            run.step(guard)
        reflected = False
        for frame in range(55):
            run.step({**guard, ana: self.aimed(run, ana, genji, 8 if frame == 0 else 0)})
            reflected |= any(r.shooter is genji and r.direct is not None
                             and r.direct.effect == combat.ANA_SLEEP_EFFECT for r in fight.rockets)
        self.assertTrue(reflected)
        self.assertNotIn(genji.body, fight.sleeps)
        self.assertEqual(zen.body in fight.sleeps, not protected)
        self.assertEqual(fight.vitals[zen.body].total, 200 if protected else 195)
        hits = genji.client.messages(combat.HIT)
        self.assertEqual(any(row["+0x78"]["+0x0"]["+0x0"] == zen.body for row in hits), not protected)
