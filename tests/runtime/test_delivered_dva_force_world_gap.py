"""Actual Booster contact must advance its accepted force without recipient input."""
import unittest
from unittest.mock import patch
import test_dva_delivery_created_body as owned
import test_game_dva_mech as native
from ow174.game import combat
from ow174.game.commands import Command

class DvaForceWorldGapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        native.AuthoredMechTests.setUpClass()

    def encounter(self, controlled):
        if controlled:
            run, controller, victim, _, source = owned.CreatedBodyTests().scenario()
            source_mover = source.control_mover
        else:
            run, source, victim, _ = native.AuthoredMechTests().scenario()
            controller, source_mover = source, source.mover
            self.assertIsNone(source.controlled_mech)
        x, y, z = source_mover.position
        victim.mover.position[:] = (x, y, z + 4.5)
        victim.mover.body = None
        for _ in range(100):
            run.step({})
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is victim)
        vitals = fight.vitals[target.entity] = combat.Vitals(target.entity, 1000)
        with patch.object(fight, "_booster_impulse", wraps=fight._booster_impulse) as accepted:
            for frame in range(80):
                run.step({controller: (0, 0, 8 if frame == 0 else 0)})
                if vitals.total < 1000:
                    break
            self.assertEqual(vitals.total, 990)
            self.assertEqual(accepted.call_count, 1)
            self.assertEqual(accepted.call_args.args[2].instance.component.entity,
                             source.entity if controlled else source.body)
            self.assertEqual(fight.knockbacks.launch_sources[target.entity],
                             source.entity if controlled else source.body)
        return run, controller, victim, source, fight, target, vitals

    def test_actual_booster_contact_paced_and_input_gap_preserve_world_displacement(self):
        for controlled in (False, True):
            with self.subTest(controlled=controlled):
                samples = []
                for gap in (False, True):
                    run, controller, victim, source, fight, target, vitals = self.encounter(controlled)
                    at = tuple(victim.mover.position)
                    if gap:
                        run.frame += 20
                        run.match.update(run.frame * combat.TICK, run.frame, frames=False)
                        self.assertGreater(victim.mover.position[2], at[2] + .5)
                        frame = fight.knockbacks.body_frame(victim, run.frame, combat.TICK)
                        victim.take_commands([Command(frame)])
                    else:
                        for _ in range(20):
                            run.frame += 1
                            run.match.update(run.frame * combat.TICK, run.frame, frames=False)
                            frame = fight.knockbacks.body_frame(victim, run.frame, combat.TICK)
                            victim.take_commands([Command(frame)])
                    self.assertEqual(vitals.total, 990)
                    samples.append((tuple(victim.mover.position), tuple(victim.mover.velocity)))
                self.assertEqual(samples[0], samples[1])
