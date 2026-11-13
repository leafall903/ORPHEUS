"""Preserve actual historical healing factors across Mercy's owner/world epochs."""
import unittest
from test_canonical_knockback_174 import CanonicalKnockbackTests
from ow174.game.content import find_hero

class HealingFilterHistoryWorldClockTests(unittest.TestCase):
    def test_ahead_and_behind_mercy_keep_world_boundary_and_discard_older_state(self):
        for birth in (200,9000):
            with self.subTest(owner_birth=birth):
                fixture=CanonicalKnockbackTests()
                run,recipient,mercy,_,_=fixture.scene(victim_birth=birth)
                run.match.switch_hero(mercy,find_hero('Mercy'))
                fight=run.match.combat
                frame=fight.knockbacks.world_frame(mercy,mercy.body_script.frame,.016)
                self.assertEqual(frame,1030)
                # A pending healer command can still refer to the status that
                # began at1025 and ended at1040; all ledger labels are world time.
                fight.healing.filter_history[recipient.body]=[(1005,()),(1025,(((),0.),)),(1040,())]
                run.match.tick=fight.tick=1100
                self.assertEqual(fight.healing.scale(recipient.body,frame=1030),0.)
                fight.healing.prune_filters(1100)
                self.assertEqual(fight.healing.scale(recipient.body,frame=1030),0.)
                self.assertEqual([row[0] for row in fight.healing.filter_history[recipient.body]],[1025,1040])
