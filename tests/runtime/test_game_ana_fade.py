"""Actual Ana inputs against Moira's 0F7C Fade phase."""

import unittest

import test_game_ana as fixture
from test_game_combat import Run, aim

from ow174.game import combat, fade, stats
from ow174.game.content import heroes


class AnaFadeTests(unittest.TestCase):
    @staticmethod
    def place(player, position):
        player.spawn = position, player.spawn[1]
        player.mover = player.new_mover()

    def fight(self, *, enemy=True, distance=3):
        run, ana, moira, _ = fixture.AnaRifleTests().setup_fight(enemy=enemy, distance=distance)
        self.assertTrue(run.match.switch_hero(moira, heroes()[fade.HERO]))
        x, y, z = ana.mover.position
        self.place(moira, (x, y, z + distance))
        for _ in range(25):
            run.step({})
        fight = run.match.combat
        target = next(target for target in fight._targets() if target.owner is moira)
        return run, ana, moira, target, fight.vitals_of(target)

    def direction(self, run, ana, target):
        x, y, z = target.feet
        return aim(run.match.combat._eye(ana), (x, y + 1, z))

    def enter(self, run, moira, commands=None):
        run.step({moira: (0, 0, 8), **(commands or {})})
        self.assertTrue(fade.active(moira))
        return next(i for i in moira.body_script.component.instances.values() if i.graph.index == fade.GRAPH)

    def test_actual_ana_attacks_and_status_miss_during_fade(self):
        for button, scoped, distance in ((1, False, 3), (1, True, 3), (8, False, 3),
                                         (16, False, 2.5), (0x800, False, 1.5)):
            with self.subTest(button=button, scoped=scoped):
                run, ana, moira, target, vitals = self.fight(distance=distance)
                direction = self.direction(run, ana, target)
                if button == 16:
                    x, y, z = target.feet
                    direction = fixture.grenade_aim(run, ana, (x + 1, y, z))
                held = 2 if scoped else 0
                for _ in range(35 if scoped else 0):
                    run.step({ana: (*direction, held)})
                self.enter(run, moira, {ana: (*direction, held)})
                run.step({ana: (*direction, button | held)})
                for _ in range(36):
                    run.step({ana: (*direction, held)})
                self.assertTrue(fade.active(moira))
                self.assertEqual(vitals.total, 200)
                self.assertEqual(vitals.healing_block_until, 0)
                self.assertFalse(run.match.combat.is_asleep(moira))
                self.assertEqual(ana.stats.value(stats.HERO_DAMAGE), 0)
                self.assertFalse(ana.client.messages(combat.HIT))

    def test_fade_ends_at_750_ms_before_recovery_and_cooldown_reuse(self):
        run, ana, moira, target, vitals = self.fight()
        instance = self.enter(run, moira)
        phase = instance.states[3]
        for _ in range(45):
            run.step({})
        self.assertTrue(fade.active(moira))
        run.step({})
        self.assertFalse(fade.active(moira))
        self.assertEqual(phase.ended_at - phase.start, 750)
        recovery = instance.states[6]
        self.assertTrue(recovery.active)
        direction = self.direction(run, ana, target)
        run.step({ana: (*direction, 1)})
        fight = run.match.combat
        for _ in range(9):
            run.step({})
            if any(hit.target == moira.body for hit in fight.timed_hits):
                break
        self.assertTrue(recovery.active)
        self.assertTrue(any(hit.target == moira.body for hit in fight.timed_hits))
        for _ in range(55):
            run.step({})
        self.assertEqual(vitals.total, 130)
        self.assertGreater(instance.vars[61].value(), 0)
        run.step({moira: (0, 0, 8)})
        self.assertFalse(fade.active(moira))
        for _ in range(420):
            run.step({})
            if instance.vars[61].value() == 0:
                break
        self.assertEqual(instance.vars[61].value(), 0)
        self.enter(run, moira)

    def test_fade_cleanses_applied_grenade_and_pending_rifle_damage(self):
        run, ana, moira, target, vitals = self.fight()
        fight = run.match.combat
        direction = self.direction(run, ana, target)
        run.step({ana: (*direction, 16)})
        for _ in range(58):
            run.step({})
        self.assertGreater(vitals.healing_block_until, fight.now)
        run.step({ana: (*direction, 1)})
        for _ in range(20):
            run.step({})
            if any(hit.target == moira.body for hit in fight.timed_hits):
                break
        self.assertTrue(any(hit.target == moira.body for hit in fight.timed_hits))
        self.enter(run, moira)
        self.assertEqual(vitals.healing_block_until, 0)
        self.assertFalse(any(hit.target == moira.body for hit in fight.timed_hits))
        before = vitals.total
        for _ in range(52):
            run.step({})
        self.assertEqual(vitals.total, before)

    def test_allied_rifle_and_nano_still_apply_during_fade(self):
        for button, expected in ((1, 150), (32, 200)):
            with self.subTest(button=button):
                run, ana, moira, target, vitals = self.fight(enemy=False, distance=2)
                vitals.damage(120)
                ana.body_script.set_ult_charge(ana.body_script.component.vars[338].value())
                direction = self.direction(run, ana, target)
                self.enter(run, moira)
                run.step({ana: (*direction, button)})
                for _ in range(44):
                    run.step({})
                self.assertTrue(fade.active(moira))
                self.assertEqual(vitals.total, expected)
                self.assertEqual(vitals.nano_until > run.match.combat.now, button == 32)

    def test_enemy_rifle_passes_through_fading_moira_to_the_next_body(self):
        run = Run(fixture.TEAM_MAP, ("Ana", "Moira", "Behind"))
        ana, moira, behind = run.players
        match = run.match
        match.bots.clear()
        match._pending_bot_scripts.clear()
        self.assertTrue(match.switch_hero(ana, heroes()[fixture.ANA]))
        self.assertTrue(match.switch_hero(moira, heroes()[fade.HERO]))
        ana.team, moira.team, behind.team = 0, 1, 1
        x, y, z = ana.mover.position
        self.place(moira, (x, y, z + 3))
        self.place(behind, (x, y, z + 6))
        for _ in range(25):
            run.step({})
        fight = match.combat
        for target in fight._targets():
            fight.vitals_of(target)
        direction = aim(fight._eye(ana), (x, y + 1, z + 6))
        self.enter(run, moira)
        run.step({ana: (*direction, 1)})
        for _ in range(44):
            run.step({})
        self.assertTrue(fade.active(moira))
        self.assertEqual(fight.vitals[moira.body].total, 200)
        self.assertEqual(fight.vitals[behind.body].total, 130)
        self.assertEqual(ana.stats.value(stats.HERO_DAMAGE), 70)

    def test_already_sleeping_moira_cannot_fade_then_can_cast_after_damage_wake(self):
        run, ana, moira, target, vitals = self.fight()
        fight = run.match.combat
        direction = self.direction(run, ana, target)
        run.step({ana: (*direction, 8)})
        for _ in range(40):
            run.step({})
            if fight.is_asleep(moira):
                break
        self.assertTrue(fight.is_asleep(moira))
        self.assertEqual(vitals.total, 195)
        run.step({moira: (0, 0, 8)})
        self.assertFalse(fade.active(moira))
        for _ in range(64):
            run.step({})
        self.assertTrue(fight.is_asleep(moira))
        run.step({ana: (*direction, 1)})
        for _ in range(45):
            run.step({})
            if not fight.is_asleep(moira):
                break
        self.assertLess(vitals.total, 195)
        self.assertFalse(fight.is_asleep(moira))
        self.enter(run, moira)
