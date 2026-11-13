"""Health death enters the original receiver branch before teardown."""
import os
import unittest
from unittest.mock import patch

import test_game_moira_photon as photon_fixture

from ow174.game import orisa_halt


@unittest.skipUnless(os.environ.get('ORPHEUS_SYMMETRA_COLLISION'), '104319 Photon mesh required')
class PhotonDeathBranchTests(unittest.TestCase):
    def test_health_break_runs_original_physics_once_before_retirement(self):
        fixture = photon_fixture.MoiraPhotonTests()
        run, _, caster, instance = fixture.arena()
        wall = fixture.deploy(run, caster, instance)
        manager = run.match.combat.photon_barriers
        entries = []
        original = orisa_halt.HaltPhysicsExplosion.on_enter

        def observe(state):
            if state.instance.graph.index == 0x156C:
                entries.append((state.node.pos, state.node.state,
                                state.evaluate(state.node.field('m_6EFD110C'), watch=False),
                                wall.component.entity_var(478).value(),
                                wall.source_body in manager.actors))
            return original(state)

        with patch.object(orisa_halt.HaltPhysicsExplosion, 'on_enter', observe):
            manager.damage(wall, wall.health - 1)
            self.assertEqual(entries, [])
            manager.damage(wall, 1)
        self.assertEqual(entries, [(39, 14, 8, True, True)])
        self.assertNotIn(wall.source_body, manager.actors)
        self.assertFalse(wall.component.instances)

    def test_expiry_keeps_death_branch_inactive(self):
        fixture = photon_fixture.MoiraPhotonTests()
        run, _, caster, instance = fixture.arena()
        wall = fixture.deploy(run, caster, instance)
        entries = []
        original = orisa_halt.HaltPhysicsExplosion.on_enter

        def observe(state):
            if state.instance.graph.index == 0x156C:
                entries.append(state.node.pos)
            return original(state)

        with patch.object(orisa_halt.HaltPhysicsExplosion, 'on_enter', observe):
            run.match.combat.photon_barriers.update(wall.expires)
        self.assertEqual(entries, [])
        self.assertFalse(wall.component.instances)
