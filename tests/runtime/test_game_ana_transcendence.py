"""Actual Ana and Zenyatta inputs during Transcendence on 104319."""

import unittest

import test_game_ana as fixture
from test_game_combat import Run, aim

from ow174.game import combat, stats, transcendence
from ow174.game.content import heroes


class AnaTranscendenceTests(unittest.TestCase):
    @staticmethod
    def place(player, position):
        player.spawn = position, player.spawn[1]
        player.mover = player.new_mover()

    def arena(self, *, distance=3, third=False, allied=False):
        names = ("Ana", "Zen", "Target") if third else ("Ana", "Zen")
        run = Run(fixture.TEAM_MAP, names)
        ana, zen = run.players[:2]
        match = run.match
        match.bots.clear()
        match._pending_bot_scripts.clear()
        self.assertTrue(match.switch_hero(ana, heroes()[fixture.ANA]))
        self.assertTrue(match.switch_hero(zen, heroes()[transcendence.HERO]))
        ana.team, zen.team = 0, int(not allied)
        x, y, z = ana.mover.position
        self.place(zen, (x, y, z + distance))
        if third:
            third_player = run.players[2]
            third_player.team = zen.team
            self.place(third_player, (x, y, z + distance + 3))
        for _ in range(30):
            run.step({})
        fight = match.combat
        for target in fight._targets():
            fight.vitals_of(target)
        return run, ana, zen

    def enter(self, run, zen):
        zen.body_script.set_ult_charge(zen.body_script.component.vars[338].value())
        run.step({zen: (0, 0, 32)})
        phase = transcendence.phase(zen)
        self.assertIsNotNone(phase)
        self.assertEqual(zen.body_script.component.vars[342].value(), 0)
        return phase

    def attack(self, run, ana, target, button, *, scoped=False):
        x, y, z = target.mover.position
        direction = aim(run.match.combat._eye(ana), (x, y + 1, z))
        held = 2 if scoped else 0
        for _ in range(35 if scoped else 0):
            run.step({ana: (*direction, held)})
        run.step({ana: (*direction, held | button)})
        for _ in range(60):
            run.step({ana: (*direction, held)})

    def test_ana_damage_sleep_and_antiheal_do_not_apply_to_transcending_zen(self):
        cases = ((1, False, 3), (1, True, 3), (8, False, 3), (16, False, 3), (0x800, False, 1.5))
        for button, scoped, distance in cases:
            with self.subTest(button=button, scoped=scoped):
                run, ana, zen = self.arena(distance=distance)
                self.enter(run, zen)
                self.attack(run, ana, zen, button, scoped=scoped)
                fight = run.match.combat
                vitals = fight.vitals[zen.body]
                self.assertTrue(transcendence.active(zen))
                self.assertEqual(vitals.total, 200)
                self.assertEqual(vitals.healing_block_until, 0)
                self.assertFalse(fight.is_asleep(zen))
                self.assertEqual(ana.stats.value(stats.HERO_DAMAGE), 0)
                self.assertFalse(ana.client.messages(combat.HIT))

    def test_solid_zen_blocks_rifle_to_enemy_behind_and_expires(self):
        run, ana, zen = self.arena(third=True)
        behind = run.players[2]
        phase = self.enter(run, zen)
        fight = run.match.combat
        self.attack(run, ana, behind, 1, scoped=True)
        self.assertEqual(fight.vitals[behind.body].total, 200)
        self.assertEqual(ana.stats.value(stats.HERO_DAMAGE), 0)
        for _ in range(400):
            if not transcendence.active(zen):
                break
            run.step({})
        self.assertFalse(transcendence.active(zen))
        self.assertAlmostEqual(phase.ended_at - phase.start, 6000, delta=16)
        run.step({zen: (0, 0, 32)})
        self.assertFalse(transcendence.active(zen))
        self.attack(run, ana, zen, 1)
        self.assertEqual(fight.vitals[zen.body].total, 130)

    def test_transcendence_heals_allied_ana_at_the_authored_rate(self):
        run, ana, zen = self.arena(allied=True)
        vitals = run.match.combat.vitals[ana.body]
        vitals.damage(100)
        phase = self.enter(run, zen)
        for _ in range(3):
            run.step({})
        self.assertTrue(phase.instance.states[6].active)
        before = vitals.total
        run.step({})
        self.assertAlmostEqual(vitals.total - before, 4.8)

    def test_transcendence_rejects_enemy_out_of_range_and_occluded_recipients(self):
        for gate in ("enemy", "range", "wall"):
            with self.subTest(gate=gate):
                run, ana, zen = self.arena(allied=gate != "enemy", distance=12 if gate == "range" else 3)
                if gate == "wall":
                    fixture.set_static_wall(run, ana, 1.5)
                vitals = run.match.combat.vitals[ana.body]
                vitals.damage(100)
                self.enter(run, zen)
                for _ in range(12):
                    run.step({})
                self.assertEqual(vitals.total, 100)

    def test_actual_grenade_blocks_or_amplifies_transcendence_healing(self):
        for allied in (False, True):
            with self.subTest(allied=allied):
                run, ana, zen = self.arena(third=True, allied=allied)
                recipient = run.players[2]
                x, y, z = recipient.mover.position
                self.place(recipient, (x + 3, y, z - 1))
                for _ in range(25):
                    run.step({})
                fight = run.match.combat
                vitals = fight.vitals[recipient.body]
                vitals.damage(170 if allied else 100)
                self.attack(run, ana, recipient, 16)
                self.assertEqual(vitals.total, 130 if allied else 50)
                self.assertGreater(vitals.healing_boost_until if allied else vitals.healing_block_until,
                                   fight.now)
                self.enter(run, zen)
                for _ in range(3):
                    run.step({})
                before = vitals.total
                run.step({})
                if allied:
                    self.assertAlmostEqual(vitals.total - before, 7.2)
                else:
                    self.assertEqual(vitals.total, before)
                    deadline = vitals.healing_block_until
                    for _ in range(250):
                        run.step({})
                        if fight.now >= deadline:
                            break
                        self.assertEqual(vitals.total, before)
                    self.assertGreaterEqual(fight.now, deadline)
                    self.assertTrue(transcendence.active(zen))
                    self.assertAlmostEqual(vitals.total - before, 4.8)

    def test_allied_nano_targets_transcending_zen(self):
        run, ana, zen = self.arena(allied=True)
        fight = run.match.combat
        vitals = fight.vitals[zen.body]
        vitals.damage(120)
        self.enter(run, zen)
        ana.body_script.set_ult_charge(ana.body_script.component.vars[338].value())
        self.attack(run, ana, zen, 32)
        self.assertTrue(transcendence.active(zen))
        self.assertEqual(vitals.total, 200)
        self.assertGreater(vitals.nano_until, fight.now)

    def test_duplicate_tick_does_not_heal_and_catchup_integrates_each_frame(self):
        run, ana, zen = self.arena(allied=True)
        fight = run.match.combat
        vitals = fight.vitals[ana.body]
        vitals.damage(100)
        self.enter(run, zen)
        for _ in range(3):
            run.step({})
        before = vitals.total
        fight.update(fight.now, fight.tick, frames=False)
        self.assertEqual(vitals.total, before)
        fight.update(fight.now + 5 * combat.TICK, fight.tick + 5, frames=False)
        self.assertAlmostEqual(vitals.total - before, 24)

    def test_sleep_prevents_transcendence_without_spending_charge(self):
        run, ana, zen = self.arena()
        fight = run.match.combat
        variables = zen.body_script.component.vars
        cost = variables[338].value()
        zen.body_script.set_ult_charge(cost)
        self.attack(run, ana, zen, 8)
        self.assertTrue(fight.is_asleep(zen))
        run.step({zen: (0, 0, 32)})
        self.assertFalse(transcendence.active(zen))
        self.assertEqual(variables[342].value(), cost)
        for _ in range(350):
            run.step({})
            if not fight.is_asleep(zen):
                break
        self.assertFalse(fight.is_asleep(zen))
        run.step({zen: (0, 0, 32)})
        self.assertTrue(transcendence.active(zen))
        self.assertEqual(variables[342].value(), 0)
