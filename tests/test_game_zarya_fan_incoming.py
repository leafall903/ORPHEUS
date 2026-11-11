"""Physical incoming Fan rays through projected protection on actual NPC bodies."""

import unittest
from unittest.mock import patch

import test_game_zarya_npcs as fixtures
from test_game_combat import aim

from ow174.game import combat, mccree, npc
from ow174.game.content import heroes


@unittest.skipUnless(fixtures.NPC_DATA, "requires actual five-NPC resources")
class ZaryaFanIncomingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.ZaryaFiveNpcTests.setUpClass()

    @classmethod
    def tearDownClass(cls):
        fixtures.ZaryaFiveNpcTests.tearDownClass()

    def test_actual_six_fan_rays_exhaust_projection_on_each_allied_npc(self):
        for definition in fixtures.ZaryaFiveNpcTests.catalog.values():
            with self.subTest(npc=definition.name), patch.object(
                combat.log, "exception", side_effect=AssertionError("unexpected combat exception"),
            ):
                run = fixtures.Run(names=("McCree", "Caster"))
                source, caster = run.players
                run.match.bots.clear()
                run.match._pending_bot_scripts.clear()
                for player, hero, team, position in (
                    (source, mccree.HERO, 0, (0, 1, 7)),
                    (caster, fixtures.ZARYA, 1, (0, 1, 0)),
                ):
                    player.client = fixtures.NpcClient(player)
                    self.assertTrue(run.match.switch_hero(player, heroes()[hero]))
                    player.team = team
                    player.mover.position[:] = position
                target = npc.spawn(run.match, definition, (0, 1, 5), team=1)
                for _ in range(24):
                    run.step({})
                fight = run.match.combat
                before = fight.vitals[target.entity].total
                cast = aim(fight._eye(caster), (0, 1 + combat.EYE, 5))
                run.step({caster: (*cast, 16)})
                projection = next(i for i in caster.body_script.component.instances.values()
                                  if i.graph.index == 0x025C)
                self.assertTrue(projection.state(10).active)
                self.assertEqual(projection.vars[536].value().id, target.entity)
                body = next(t for t in fight._targets() if t.owner is target)
                direction = aim(fight._eye(source), (0, 1 + body.height / 2, 5))
                samples = []
                original = mccree.fan_direction

                def observe(player, state, forward, number, time, activation,
                            original=original, source=source, samples=samples):
                    ray = original(player, state, forward, number, time, activation)
                    if player is source and state.node.pos == 37:
                        samples.append((forward, ray))
                    return ray

                with patch.object(mccree, "fan_direction", side_effect=observe):
                    run.step({source: (*direction, 2)})
                    for _ in range(50):
                        run.step({source: (*direction, 0)})
                self.assertEqual(len(samples), 6)
                self.assertEqual(len({ray for _, ray in samples}), 6)
                self.assertTrue(all(forward != ray for forward, ray in samples))
                self.assertAlmostEqual(before - fight.vitals[target.entity].total, 100)
                self.assertAlmostEqual(caster.body_script.component.entity_var(87).value(), 40)
                energy = next(i for i in caster.body_script.component.instances.values()
                              if i.graph.index == 0x0242)
                self.assertEqual(energy.vars[392].value().id, source.body)
