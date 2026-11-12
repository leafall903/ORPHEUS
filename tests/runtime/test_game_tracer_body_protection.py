"""Actual Tracer combat against Wraith Form, Fade and Cryo-Freeze."""

import unittest

import test_game_tracer as tracer_tests
import test_game_tracer_pulse_deflect as pulse_tests
import test_game_tracer_sigma_flux as flux_tests
from test_game_combat import aim

from ow174.game import body_protection
from ow174.game.commands import FIRE, Command
from ow174.game.content import heroes
from ow174.game.script.driver import ULT_COST

REAPER = 0x02E0000000000002
MOIRA = 0x02E00000000001A2
MEI = 0x02E00000000000DD


class TracerBodyProtectionTests(unittest.TestCase):
    def arena(self, hero, *, behind=True, distance=4):
        self.run, self.tracer, self.enemy, _ = tracer_tests.TracerPistolTests().fight()
        self.fight = self.run.match.combat
        self.assertTrue(self.run.match.switch_hero(self.enemy, heroes()[hero]))
        x, y, z = self.tracer.mover.position
        self.enemy.mover.position = [x, y, z + distance]
        self.enemy.mover.body = None
        self.behind = None
        if behind:
            self.behind, _ = pulse_tests.PulseDeflectTests().support(self.run, self.enemy)
        self.aim = aim(self.fight._eye(self.tracer), (x, y + 1, z + distance))
        for _ in range(30):
            self.step()
        self.vitals = self.health(self.enemy)
        self.original_health = self.vitals.total

    def health(self, player):
        target = next(t for t in self.fight._targets() if t.owner is player)
        return self.fight.vitals_of(target)

    def step(self, *, enemy=0, tracer=0, pitch=None):
        yaw, aim_pitch = self.aim
        self.run.step({self.tracer: (yaw, aim_pitch if pitch is None else pitch, tracer),
                       self.enemy: (-32768, 0, enemy)})

    def activate(self, expected):
        self.step(enemy=8)
        self.step()
        self.assertEqual(body_protection.active(self.enemy), expected)

    def wait_end(self):
        for _ in range(320):
            if not body_protection.active(self.enemy):
                return
            self.step()
        self.fail("Protection did not end")

    def launch_pulse(self):
        script = self.tracer.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        self.step(tracer=32, pitch=0)
        for _ in range(25):
            if self.fight.pulse_bombs.bombs:
                return self.fight.pulse_bombs.bombs[0]
            self.step(pitch=0)
        self.fail("Pulse did not launch")

    def test_pistols_pass_through_wraith_and_fade_then_normal_hits_resume(self):
        for hero in (REAPER, MOIRA):
            with self.subTest(hero=hero):
                self.arena(hero)
                rear = self.health(self.behind)
                self.activate("phase")
                self.step(tracer=FIRE)
                self.assertEqual(self.vitals.total, self.original_health)
                self.assertEqual(rear.total, 188)
                self.wait_end()
                self.step(enemy=8)
                self.assertIsNone(body_protection.active(self.enemy))
                self.step(tracer=FIRE)
                self.assertEqual(self.vitals.total, self.original_health - 12)

    def test_cryo_blocks_pistols_for_mei_and_the_player_behind(self):
        self.arena(MEI)
        rear = self.health(self.behind)
        self.activate("cryo")
        self.step(tracer=FIRE)
        self.assertEqual(self.vitals.total, self.original_health)
        self.assertEqual(rear.total, 200)
        self.wait_end()
        self.step(tracer=FIRE)
        self.assertEqual(self.vitals.total, self.original_health - 12)

    def test_pulse_passes_through_wraith_and_sticks_to_player_behind(self):
        self.arena(REAPER, distance=2)
        self.activate("phase")
        bomb = self.launch_pulse()
        for _ in range(65):
            if bomb.stuck:
                break
            self.step(pitch=0)
        self.assertEqual(bomb.attached, self.behind.body)
        self.assertEqual(self.health(self.behind).total, 195)
        self.assertEqual(self.vitals.total, self.original_health)

    def test_fade_detaches_a_previously_stuck_pulse_and_avoids_the_blast(self):
        self.arena(MOIRA, behind=False, distance=3)
        bomb = self.launch_pulse()
        for _ in range(65):
            if bomb.stuck:
                break
            self.step(pitch=0)
        self.assertEqual(bomb.attached, self.enemy.body)
        self.assertEqual(self.vitals.total, self.original_health - 5)
        self.activate("phase")
        self.assertIsNone(bomb.attached)
        # Move the phased target out of the pending blast using real commands.
        for _ in range(70):
            self.run.frame += 1
            frame = self.run.frame
            self.run.match.update(frame * 0.016, frame)
            self.enemy.take_commands([Command(frame, yaw=0, forward=127)])
            self.tracer.take_commands([Command(frame)])
        self.assertFalse(self.fight.pulse_bombs.bombs)
        self.assertEqual(self.vitals.total, self.original_health - 5)

    def test_quick_melee_respects_all_three_protections(self):
        for hero, protection in ((REAPER, "phase"), (MOIRA, "phase"), (MEI, "cryo")):
            with self.subTest(hero=hero):
                self.arena(hero, behind=False, distance=2)
                self.activate(protection)
                self.step(tracer=0x800)
                for _ in range(28):
                    self.step()
                self.assertEqual(self.vitals.total, self.original_health)
                self.wait_end()
                for _ in range(40):
                    self.step()
                self.step(tracer=0x800)
                for _ in range(28):
                    self.step()
                self.assertEqual(self.vitals.total, self.original_health - 30)

    def test_wraith_cancel_rejects_early_recast_and_allows_later_reuse(self):
        self.arena(REAPER, behind=False)
        self.activate("phase")
        for _ in range(45):
            self.step()
        self.assertEqual(body_protection.active(self.enemy), "phase")
        self.step(enemy=8)
        self.assertIsNone(body_protection.active(self.enemy))
        self.step()
        self.step(enemy=8)
        self.assertIsNone(body_protection.active(self.enemy))
        for _ in range(580):
            self.step()
        self.activate("phase")
        self.step(tracer=FIRE)
        self.assertEqual(self.vitals.total, self.original_health)

    def test_wraith_and_fade_release_live_flux_before_slam(self):
        for hero in (REAPER, MOIRA):
            with self.subTest(hero=hero):
                case = flux_tests.TracerSigmaFluxTests()
                case.setUp()
                position = list(case.tracer.mover.position)
                self.assertTrue(case.run.match.switch_hero(case.tracer, heroes()[hero]))
                case.tracer.mover.position = position
                case.tracer.mover.body = None
                target = next(t for t in case.fight._targets() if t.owner is case.tracer)
                vitals = case.fight.vitals_of(target)
                after_entry = vitals.total - 50
                case.select()
                case.step(sigma_buttons=FIRE)
                for _ in range(115):
                    if vitals.total == after_entry:
                        break
                    case.step()
                self.assertEqual(vitals.total, after_entry)
                for _ in range(12):
                    case.step()
                self.assertTrue(case.tracer.mover.airborne)
                self.assertIsNotNone(case.effect())
                case.step(tracer_buttons=8)
                self.assertEqual(body_protection.active(case.tracer), "phase")
                self.assertIsNone(case.effect())
                for _ in range(220):
                    case.step()
                self.assertEqual(vitals.total, after_entry)
                self.assertFalse(case.tracer.mover.airborne)
