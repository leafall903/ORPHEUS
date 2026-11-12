"""Actual single-host particle-barrier inputs used by the retained Sentry proof."""
import unittest
from unittest.mock import patch
from test_game_combat import aim
from test_zarya_native_cleanse import NativePetApplicationTests
from ow174.game import barriers
from ow174.game.script import runtime
from ow174.game.symmetra_turrets import _single_replay_projection


class ReplayServiceTests(unittest.TestCase):
    def test_projection_reads_same_live_pet_and_pool_without_running_components(self):
        run, fight, caster, victim = NativePetApplicationTests().encounter(True)
        run.step({caster: (*aim(fight._eye(caster), fight._eye(victim)), 16)})
        actor = fight.barriers.authored_for(caster.body)
        self.assertIsNotNone(actor)
        before = (actor.entity, actor.vitals.total, actor.source.frame,
                  len(actor.source.instances), actor.component.frame)
        with patch.object(runtime.Component, "run_now", side_effect=AssertionError("query ran VM")), \
                patch.object(runtime.Component, "run_frame", side_effect=AssertionError("query ran VM")):
            self.assertIs(_single_replay_projection(fight), victim)
            self.assertEqual((actor.entity, actor.vitals.total, actor.source.frame,
                              len(actor.source.instances), actor.component.frame), before)
        barriers.damage(fight, actor, 200)
        self.assertIsNone(_single_replay_projection(fight))
        self.assertNotIn(actor.entity, fight.barriers.scripted)
