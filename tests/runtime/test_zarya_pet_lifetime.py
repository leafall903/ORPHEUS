"""Actual original0068 Pet lifetime on its accepted body/world clock pair."""
import unittest
import test_zarya_native_cleanse as original
from test_game_combat import aim
from ow174.game import barriers, combat
from ow174.game.commands import Command

class ZaryaPetLifetimeTests(unittest.TestCase):
    def test_future_application_and_early_body_end_preserve_same_actual_pet(self):
        for projected in (False, True):
            with self.subTest(projected=projected):
                source = original.ZaryaNativeCleanseTests()
                run, fight, enemy, caster, victim = source.encounter(projected, protect=False)
                if projected:
                    caster.mover.position[:] = [6, 1, 5]
                    caster.mover.body = None
                match = run.match
                application = match.tick + 40
                match.tick = application
                frame = caster.body_script.frame + 1
                angles = aim(fight._eye(caster), fight._eye(victim)) if projected else (0, 0)
                caster.take_commands([Command(frame, yaw=angles[0], pitch=angles[1],
                                              buttons=16 if projected else 8)])
                pet = next(b for b in fight.barriers.scripted.values() if b.parent is caster)
                expiry = fight.barriers.expiries[caster.body, projected]
                self.assertEqual(pet.application_tick, application)
                self.assertNotEqual(pet.source.frame, application)
                self.assertFalse(barriers._live(fight, pet))
                self.assertEqual(pet.vitals.total, 200)
                match.update((application - 1) * combat.TICK, application - 1, frames=False)
                self.assertIs(fight.barriers.scripted[pet.entity], pet)
                self.assertFalse(barriers._live(fight, pet))
                match.update(application * combat.TICK, application, frames=False)
                self.assertTrue(barriers._live(fight, pet))
                caster.take_commands([Command(expiry.frame + 3)])
                self.assertFalse(expiry.duration.active)
                self.assertTrue(expiry.ended_ahead)
                self.assertIs(fight.barriers.scripted[pet.entity], pet)
                self.assertEqual(pet.vitals.total, 200)
                match.update((expiry.tick - 1) * combat.TICK, expiry.tick - 1, frames=False)
                self.assertIs(fight.barriers.scripted[pet.entity], pet)
                self.assertTrue(barriers._live(fight, pet))
                match.update((expiry.tick + 2) * combat.TICK, expiry.tick + 2, frames=False)
                self.assertNotIn(pet.entity, fight.barriers.scripted)
                self.assertFalse(barriers._live(fight, pet))
                self.assertIn((caster.body, projected), fight.barriers.cooldown_deadlines)
                ready = fight.barriers.cooldown_deadlines[caster.body, projected]
                self.assertGreater(ready, fight.tick)
                counter = pet.source.instances[pet.instance].states[pet.state].counter
                caster.take_commands([Command(caster.body_script.frame + 1000,
                                              buttons=16 if projected else 8)])
                self.assertEqual(pet.source.instances[pet.instance].states[pet.state].counter, counter)
                self.assertFalse(any(b.parent is caster for b in fight.barriers.scripted.values()))

    def test_break_death_and_replacement_retire_actual_projected_pet(self):
        for action in ("break", "caster_death", "recipient_replacement"):
            with self.subTest(action=action):
                run, fight, caster, victim = original.NativePetApplicationTests().encounter(True)
                angles = aim(fight._eye(caster), fight._eye(victim))
                run.step({caster: (*angles, 16)})
                pet = fight.barriers.authored_for(caster.body)
                self.assertIsNotNone(pet)
                before = fight.vitals[victim.body].total
                enemy = run.players[0]
                if action == "recipient_replacement":
                    self.assertTrue(run.match.switch_hero(victim, victim.hero))
                else:
                    owner = caster if action == "caster_death" else victim
                    target = next(t for t in fight._targets() if t.owner is owner)
                    fight._damage(enemy, target, 500 if action == "caster_death" else 200,
                                  False, None, fight.tick)
                self.assertNotIn(pet.entity, fight.barriers.scripted)
                self.assertFalse(barriers._live(fight, pet))
                if action == "break":
                    self.assertEqual(fight.vitals[victim.body].total, before)
