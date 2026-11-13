"""D.Va's own view is not black once she is on foot.

Graph 026A sets #6828 at its Entry (node 110) and again at each boarding (node 116), and its presenter
(node 193) draws the screen black while it is on. In the data node 115 clears it on the branch of node 2
where no player drives the pilot: she is stowed in her mech."""

import unittest

import test_dva_delivery_created_body as created
from test_game_combat import KANEZAKA_DM, Run

from ow174.game.combat import Vitals
from ow174.game.content import find_hero
from ow174.game.script.driver import ULT_CHARGE, ULT_COST

PILOT_GRAPH, BLACKOUT, PRESENTER = 0x026A, 6828, 193
ULTIMATE = 0x20
BLAST, BOARDED = 250, 200  # frames: past Self-Destruct's fuse; past Call Mech's boarding


class BoardingBlackoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        created.CreatedBodyTests.setUpClass()

    def picked(self):
        run = Run(KANEZAKA_DM, ("Pilot", "Other"))
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        pilot, other = run.players
        # Call Mech retires the old owner stream through two members of the real client.
        pilot.client.in_flight = {}
        pilot.client.cancel_entity_record = lambda update: None
        self.assertTrue(run.match.switch_hero(pilot, find_hero("dva")))
        self.steps(run, 120)
        self.assertIsNotNone(pilot.controlled_mech)
        return run, pilot, other

    @staticmethod
    def steps(run, count: int) -> None:
        for _ in range(count):
            run.step({})

    def ultimate(self, run, pilot, component) -> None:
        component.set(ULT_CHARGE, component.entity_var(ULT_COST).value())
        run.step({pilot: (0, 0, ULTIMATE)})

    def wreck(self, run, pilot, other) -> None:
        fight, mech = run.match.combat, pilot.controlled_mech
        target = next(found for found in fight._targets() if found.entity == mech.entity)
        fight.vitals.setdefault(mech.entity, Vitals(mech.entity, 600))
        fight._damage(other, target, 5000.0, False, None, run.frame)
        self.steps(run, 20)
        self.assertIsNone(pilot.controlled_mech)
        self.assertFalse(fight.is_dead(pilot))

    def blackout(self, pilot) -> tuple:
        """(#6828, whether the presenter that draws the screen black is on) of her own graph."""
        graph = next(instance for instance in pilot.body_script.component.instances.values()
                     if instance.graph.index == PILOT_GRAPH)
        presenter = graph.states.get(graph.graph.node(PRESENTER).state)
        return graph.vars[BLACKOUT].value(), bool(presenter is not None and presenter.active)

    def test_her_view_is_not_black_after_self_destruct(self):
        run, pilot, _ = self.picked()
        self.ultimate(run, pilot, pilot.controlled_mech.body_script.component)
        self.steps(run, 10)
        self.assertIsNone(pilot.controlled_mech)
        self.assertEqual(self.blackout(pilot), (False, False))
        self.steps(run, BLAST)
        self.assertEqual(self.blackout(pilot), (False, False))

    def test_her_view_is_not_black_after_her_mech_is_wrecked(self):
        run, pilot, other = self.picked()
        self.wreck(run, pilot, other)
        self.assertEqual(self.blackout(pilot), (False, False))

    def test_her_view_is_not_black_after_a_called_mech_is_wrecked(self):
        # The boarding of a called mech sets the blackout again (node 116), after she is bound to it.
        run, pilot, other = self.picked()
        self.ultimate(run, pilot, pilot.controlled_mech.body_script.component)
        self.steps(run, BLAST)
        self.ultimate(run, pilot, pilot.body_script.component)
        self.steps(run, BOARDED)
        self.assertIsNotNone(pilot.controlled_mech)
        self.assertIs(self.blackout(pilot)[0], True)
        self.wreck(run, pilot, other)
        self.assertEqual(self.blackout(pilot), (False, False))


if __name__ == "__main__":
    unittest.main()
