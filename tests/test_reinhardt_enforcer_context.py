"""Actual Enforcer graph with explicitly declared query facts, not native AI.

The controller profile and aggregate-health getter are fixture inputs. Normal
Match does not install them. This verifies the generic predicate integration.
"""

import unittest
from unittest.mock import patch

import test_game_reinhardt as fixtures

from ow174.game.script import controller_query, expr
from ow174.game.script.bot import BotWorld
from ow174.game.script.driver import OWNER_HEALTH


@unittest.skipUnless((fixtures.NPC_DATA / 'pve_npcs_174.json').is_file()
                     and fixtures.NPC_MOVERS.is_file(), 'authorized NPC body/mover inputs required')
class EnforcerControllerContextTests(unittest.TestCase):
    advance = fixtures.HammerTests.advance
    place = staticmethod(fixtures.HammerTests.place)
    scenario = fixtures.TalonInteractionTests.scenario

    @classmethod
    def setUpClass(cls):
        fixtures.TalonInteractionTests.setUpClass()
        cls.catalog = fixtures.TalonInteractionTests.catalog

    @classmethod
    def tearDownClass(cls):
        fixtures.TalonInteractionTests.tearDownClass()

    def test_actual_weapon_uses_generic_controller_query_and_known92_rejects_profile(self):
        for component92 in (False, True):
            with self.subTest(component92=component92):
                contexts, events = {}, []
                # A data-only fixture descriptor. No corresponding controller
                # entity is created, sent to clients or aliased to a body.
                descriptor = controller_query.Controller(0xD0000100, True, component92)

                def provider(world, owner, contexts=contexts, events=events, descriptor=descriptor):
                    if world.hero != 0x02E00000000001CE:
                        return NotImplemented
                    if owner not in contexts:
                        contexts[owner] = controller_query.Context(owner, True,
                            lookup=lambda entity: controller_query.Target(entity, True)
                            if entity == owner else None,
                            resolve_controller=lambda target: events.append('controller') or descriptor,
                            watch_component43=lambda *args: events.append('component43'))
                    return contexts[owner]

                base = BotWorld.native

                def health_service(world, cfg, instance, base=base):
                    if (world.hero == 0x02E00000000001CE and instance is not None
                            and instance.graph.index == 0x13EB and cfg.get('$') == 'STU_9B7648C3'
                            and instance.evaluate(cfg.get('m_entity'))
                            == expr.Entity(instance.component.entity)):
                        return expr.f32(instance.component.entity_var(OWNER_HEALTH).value())
                    return base(world, cfg, instance)

                with (patch.object(BotWorld, 'entity_controller_context', provider),
                      patch.object(BotWorld, 'native', health_service)):
                    actor, _, _ = self.scenario(self.catalog['2'], (0, 1, 8))
                    self.advance(20, buttons=0)
                    actor.attack_target = self.rein.entity
                    actor.firing = True
                    victim = self.match.combat._target_entity(self.rein.body)
                    health = self.match.combat.vitals_of(victim)
                    for _ in range(220):
                        self.advance(1, buttons=0)
                        if health.total < 500:
                            break
                    self.assertIsNotNone(actor.body_script)
                    self.assertIn('component43', events)
                    self.assertIn('controller', events)
                    if component92:
                        self.assertEqual(health.total, 500)
                    else:
                        self.assertLess(health.total, 500)


if __name__ == '__main__':
    unittest.main()
