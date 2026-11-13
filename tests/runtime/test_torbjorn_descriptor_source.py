"""Original ultimate input through encrypted two-peer transport, without a native client."""
import os
import unittest

import test_game_repeated_lifecycles as protocol
import test_game_server as server_tests

from ow174.game import hero_torbjorn, messages, molten_core
from ow174.game.bits import BitWriter
from ow174.game.content import PRACTICE_RANGE, SOLDIER
from ow174.game.script import decode, graph


def command(run, link, player, *, buttons=0, pitch=0):
    frame = max(player.body_script.frame, player.commands.last_frame or 0) + 1
    body, payload = BitWriter(), BitWriter()
    body.bits(0, 24)
    body.bits(1, 8)
    body.bits(frame, 32)
    body.bit(0)
    body.bits((pitch & 0xFFFF) << 16, 32)
    body.bits(buttons, 24)
    body.bits(0, 24)
    messages.write_channel(payload, [])
    payload.bit(1)
    payload.bits(body.count, 32)
    payload.append(body)
    run.send(link, payload=payload.getvalue())
    assert player.commands.last_frame == frame


@unittest.skipUnless(os.environ.get('ORPHEUS_TORBJORN_GRAPH_BUNDLE'), 'needs prepared Torbjorn bundle')
class TorbjornDescriptorSourceTests(unittest.TestCase):
    def test_actual_molten_input_publishes_captured_launcher_to_both_encrypted_peers(self):
        run = protocol.ProtocolHarness()
        try:
            own, other = run.server.create_match(
                PRACTICE_RANGE, [(1, 'Torbjorn', 0, 0, False), (2, 'Observer', 0, 0, False)],
            )
            owner_link, owner = run.connect(own)
            observer_link, observer = run.connect(other)
            run.message(owner_link, 21616, server_tests.pick(owner.entity, hero_torbjorn.HERO))
            run.message(observer_link, 21616, server_tests.pick(observer.entity, SOLDIER))
            command(run, owner_link, owner)
            command(run, observer_link, observer)
            run.until(lambda: owner.body in owner_link.models and owner.body in observer_link.models)

            def advance(count):
                for _ in range(count):
                    command(run, owner_link, owner)
                    run.step()

            advance(20)
            owner.body_script.set_ult_charge(10000)
            command(run, owner_link, owner, buttons=32)
            advance(45)
            launcher = owner.body
            command(run, owner_link, owner, buttons=1, pitch=4000)
            advance(60)
            pool = next(iter(owner.match.combat.molten_pools.values()))
            instance = pool.script.component.instances[pool.instance]
            self.assertEqual(instance.graph.index, 0x16DD)
            self.assertEqual(instance.descriptor_source_entity, launcher)
            initial = [item['graph'] for item in graph.entity_body(molten_core.POOL)['graphs']]
            for link in (owner_link, observer_link):
                model = decode.ClientBody(initial)
                sources = []
                for _, update in link.records:
                    if update.entity != pool.entity or update.chunk is None:
                        continue
                    frame = decode.read_chunk(update.chunk.getvalue(), update.chunk.count, model.graphs)
                    if frame.last <= model.applied or frame.first > model.applied + 1:
                        continue
                    model.apply(update)
                    sources.extend(item.source_entity for item in frame.instances.values()
                                   if item.descriptor and item.graph == 0x16DD)
                self.assertTrue(sources)
                self.assertEqual(set(sources), {launcher})
                self.assertEqual(model.sources[pool.instance], launcher)
                self.assertNotEqual(launcher, pool.entity)
        finally:
            run.close()


if __name__ == '__main__':
    unittest.main()
