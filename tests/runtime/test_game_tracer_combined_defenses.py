"""Actual Tracer, Genji and Winston inputs in the same combat fixture."""
import unittest
from dataclasses import replace

import test_game_tracer_deflect as deflect_tests
import test_game_tracer_dva_matrix as matrix_tests
import test_game_tracer_pulse_deflect as pulse_tests
import test_game_tracer_winston_barrier as winston_tests

from ow174.game import combat, stats
from ow174.game.commands import FIRE
from ow174.game.content import find_hero
from ow174.game.script.driver import ULT_COST


class TracerCombinedDefensesTests(unittest.TestCase):
    def arena(self, *, bubble_allied_to_tracer=True, raise_deflect=True):
        run, tracer, genji, incoming, reflected, _ = deflect_tests.TracerDeflectTests().arena(
            distance=3, activate_deflect=raise_deflect,
        )
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        tracer.team, genji.team = 0, 1
        winston, _ = pulse_tests.PulseDeflectTests().support(run, genji, hero=0x02E0000000000009)
        winston.team = tracer.team if bubble_allied_to_tracer else genji.team
        x, y, z = tracer.mover.position
        winston_tests.TracerWinstonBarrierTests.move(winston, (x, y, z - 3))
        buttons = 16 if raise_deflect else 0
        for _ in range(25):
            run.step({genji: (*reflected, buttons)})
        run.step({winston: (0, 0, 16), genji: (*reflected, buttons)})
        for _ in range(4):
            run.step({genji: (*reflected, buttons)})
        bubble = run.match.combat.winston_barriers.actors[winston.body]
        self.assertEqual(bubble.health, 700)
        return run, tracer, genji, bubble, incoming, reflected

    def pulse_contact(self, run, tracer, genji, incoming, reflected):
        script = tracer.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        run.step({tracer: (*incoming, 32), genji: (*reflected, 16)})
        bombs = run.match.combat.pulse_bombs.bombs
        for _ in range(50):
            if bombs and bombs[0].stuck:
                return bombs[0]
            run.step({genji: (*reflected, 16)})
        self.fail("actual Pulse did not stick")

    def test_reflected_pulse_hits_the_bubble_allied_to_tracer(self):
        run, tracer, genji, bubble, incoming, reflected = self.arena()
        bomb = self.pulse_contact(run, tracer, genji, incoming, reflected)
        self.assertIs(bomb.shooter, genji)
        self.assertEqual(bomb.source, genji.body)
        self.assertIs(bomb.attached_bubble, bubble)
        self.assertIsNone(bomb.attached)
        self.assertEqual(bubble.health, 695)
        for _ in range(70):
            run.step({})
        self.assertEqual(bubble.health, 345)
        self.assertEqual(winston_tests.TracerWinstonBarrierTests.health(run, tracer).total, 150)
        self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), 0)
        self.assertEqual(tracer.stats.value(stats.HERO_DAMAGE), 0)

    def test_enemy_bubble_contacts_outgoing_pulse_before_genji_can_reflect(self):
        run, tracer, genji, bubble, incoming, reflected = self.arena(bubble_allied_to_tracer=False)
        bomb = self.pulse_contact(run, tracer, genji, incoming, reflected)
        self.assertIs(bomb.shooter, tracer)
        self.assertEqual(bomb.source, tracer.body)
        self.assertIs(bomb.attached_bubble, bubble)
        self.assertEqual(bubble.health, 695)
        for _ in range(70):
            run.step({})
        self.assertEqual(bubble.health, 345)
        self.assertEqual(winston_tests.TracerWinstonBarrierTests.health(run, genji).total, 200)
        winston_loss = 500 - winston_tests.TracerWinstonBarrierTests.health(run, bubble.owner).total
        self.assertGreater(winston_loss, 0)
        self.assertAlmostEqual(tracer.stats.value(stats.HERO_DAMAGE), winston_loss)

    def test_reflected_pistols_check_bubble_after_owner_changes(self):
        run, tracer, genji, bubble, incoming, reflected = self.arena()
        run.step({tracer: (*incoming, FIRE), genji: (*reflected, 16)})
        self.assertEqual(bubble.health, 688)
        for actor, initial in ((tracer, 150), (genji, 200)):
            self.assertEqual(winston_tests.TracerWinstonBarrierTests.health(run, actor).total, initial)
            self.assertFalse(actor.client.messages(combat.HIT))
            self.assertEqual(actor.stats.value(stats.HERO_DAMAGE), 0)



class TracerMatrixBubbleOrderingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        matrix_tests.TracerDvaMatrixTests.setUpClass()

    def test_reflected_pulse_is_absorbed_before_the_return_path_reaches_the_bubble(self):
        fixture = TracerCombinedDefensesTests()
        run, tracer, genji, bubble, incoming, reflected = fixture.arena(raise_deflect=False)
        match = run.match
        mech, _ = pulse_tests.PulseDeflectTests().support(run, genji, hero=0x02E000000000007A)
        self.assertTrue(match.switch_hero(mech, replace(find_hero("dva"), body=0x0400000000000407)))
        mech.team = tracer.team
        x, y, z = tracer.mover.position
        winston_tests.TracerWinstonBarrierTests.move(mech, (x, y, z - 2))
        for _ in range(105):
            run.step({})
        for _ in range(8):
            run.step({mech: (0, 0, 2)})
        matrix = next(i for i in mech.body_script.component.instances.values() if i.graph.index == 0x02CC)
        self.assertTrue(matrix.state(3).active)
        script = tracer.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        manager = match.combat.pulse_bombs
        bomb = None
        for index in range(50):
            run.step({mech: (0, 0, 2), genji: (*reflected, 16),
                      tracer: (*incoming, 32 if index == 0 else 0)})
            if manager.bombs:
                bomb = manager.bombs[0]
            elif bomb is not None:
                break
        self.assertIsNotNone(bomb)
        self.assertIs(bomb.shooter, genji)
        self.assertNotIn(bomb, manager.bombs)
        self.assertFalse(bomb.stuck)
        self.assertEqual(bubble.health, 700)
        self.assertEqual(winston_tests.TracerWinstonBarrierTests.health(run, tracer).total, 150)

    def test_nearest_defense_owns_actual_pulse_contact(self):
        for bubble_first in (True, False):
            with self.subTest(bubble_first=bubble_first):
                fixture = matrix_tests.TracerDvaMatrixTests()
                fixture.setUp()
                run, mech, tracer = fixture.run, fixture.mech, fixture.tracer
                match = run.match
                match.game_map = replace(match.game_map, free_for_all=False)
                mech.team, tracer.team = 1, 0
                winston, _ = pulse_tests.PulseDeflectTests().support(run, mech, hero=0x02E0000000000009)
                winston.team = mech.team
                move = winston_tests.TracerWinstonBarrierTests.move
                move(tracer, (0, 1, 14))
                move(winston, (0, 1, 7.5 if bubble_first else 3))
                for _ in range(30):
                    run.step({})
                bubble = winston_tests.TracerWinstonBarrierTests().cast(run, winston)
                for _ in range(8):
                    run.step({mech: (0, 0, 2)})
                self.assertTrue(fixture.matrix.state(3).active)
                script = tracer.body_script
                script.set_ult_charge(script.component.entity_var(ULT_COST).value())
                manager = match.combat.pulse_bombs
                bomb = None
                for offset in range(55):
                    run.step({mech: (0, 0, 2), tracer: (-32768, 0, 32 if offset == 0 else 0)})
                    if manager.bombs:
                        bomb = manager.bombs[0]
                        if bomb.stuck:
                            break
                    elif bomb is not None:
                        break
                self.assertIsNotNone(bomb)
                if bubble_first:
                    self.assertIs(bomb.attached_bubble, bubble)
                    self.assertEqual(bubble.health, 695)
                    self.assertIn(bomb, manager.bombs)
                else:
                    self.assertFalse(bomb.stuck)
                    self.assertNotIn(bomb, manager.bombs)
                    self.assertEqual(bubble.health, 700)
                self.assertEqual(fixture.vitals.total, 600)
