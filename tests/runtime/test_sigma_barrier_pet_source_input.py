"""The original Pet source argument is consumed at actual actor construction."""

import unittest
from unittest.mock import patch

import test_game_combat as fixtures

from ow174.game import sigma_barrier
from ow174.game.content import heroes
from ow174.game.script import expr, runtime


class SigmaBarrierPetSourceInputTests(unittest.TestCase):
    def test_actual_creation_rejects_unavailable_authored_source(self):
        run = fixtures.Run(names=("Sigma", "Observer"))
        owner = run.players[0]
        original = runtime.State.evaluate

        def evaluate(state, cfg, *args, **kwargs):
            if (state.instance.graph.index == 0x1B55 and state.node.pos == 6
                    and cfg is state.node.field("m_D749D7D8")):
                return expr.Entity(0)
            return original(state, cfg, *args, **kwargs)

        with patch.object(runtime.State, "evaluate", evaluate):
            run.match.switch_hero(owner, heroes()[sigma_barrier.HERO])
        self.assertFalse(any(a.owner is owner for a in run.match.combat.sigma_barriers.values()))

    def test_actual_creation_captures_controller_source_distinct_from_body(self):
        run = fixtures.Run(names=("Sigma", "Observer"))
        owner = run.players[0]
        run.match.switch_hero(owner, heroes()[sigma_barrier.HERO])
        actor, = (a for a in run.match.combat.sigma_barriers.values() if a.owner is owner)
        self.assertEqual(actor.pet_source, owner.entity)
        self.assertNotEqual(actor.pet_source, owner.body)
