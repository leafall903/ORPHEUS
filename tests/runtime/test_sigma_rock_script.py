"""18B7's own original component and remote model-driving states on both peers."""

import json
import unittest
from pathlib import Path

from test_game_repeated_lifecycles import wire_frame
from test_server_journal import ServerJournalTests

from ow174.game import accretion, sigma_presentation
from ow174.game.body_vm_snapshot import export_component_vm, snapshot_digest
from ow174.game.collision import World
from ow174.game.link import KIND_FRAME, peek_connection
from ow174.game.script import decode, graph


class SigmaRockScriptTests(unittest.TestCase):
    def test_original_component_initializes_before_create_and_drives_remote_model(self):
        fixture = json.loads((Path(__file__).resolve().parents[2] /
                              "fixtures/behavior/sigma-rock-script-v1.json").read_text())
        self.assertIsNotNone(graph.graph(0x1EB9), "requires the minimal Sigma rock external profile")
        case = ServerJournalTests()
        session, sent = case.make()
        peers, (owner, enemy) = case.connected(session, sent, first_hero=accretion.HERO)
        fight = owner.match.combat
        fight.match.collision_world = World(fixture["input"]["collision_triangles"])
        enemy.mover.position[0] += fixture["input"]["enemy_offset_x_m"]
        now = 100.944
        captured = None
        component = None
        snapshots = []
        sent_before_expiry = None
        storage_digest = None
        for at in range(fixture["input"]["frames"]):
            now += .016
            session.tick(now)
            peers[0].commands(owner.body_script.frame + 1, 1,
                              fixture["input"]["button"] if at == 0 else 0, now=now)
            peers[1].commands(enemy.body_script.frame + 1, 1, 0, now=now)
            for rocket in fight.rockets:
                if rocket.definition != accretion.ROCK:
                    continue
                captured = rocket.shot
                component = fight.projectiles.sigma_scripts[rocket.entity]
                projected = sigma_presentation.projection(fight, rocket.entity)
                self.assertIs(projected.component, component)
                snapshots.append(projected.latest_live)
                if at == fixture["input"]["expire_flight_at_input_step"]:
                    storage_digest = snapshot_digest(export_component_vm(component))
                    sent_before_expiry = list(sent)
                    rocket.left = 0  # End only after checking the authored .25s curve.
        self.assertIsNotNone(captured)
        self.assertIsNotNone(storage_digest)
        self.assertTrue(any(s.vars.get((1, 154), (None,))[0] == 1.0 for s in snapshots))
        for peer in peers:
            vm = decode.ClientBody((0x1EB9,))
            chunks = set()
            progress = []
            saw_scope = False
            for data, _address in sent_before_expiry:
                if peek_connection(data) != peer.handoff.conn:
                    continue
                packet = peer.cipher.open(data)
                if packet is None or packet.kind != KIND_FRAME:
                    continue
                _, records = wire_frame(packet)
                for record, _fields in records:
                    if record.entity != captured.entity or record.chunk is None:
                        continue
                    key = record.chunk.getvalue()
                    if key in chunks:
                        continue
                    chunks.add(key)
                    vm.apply(record)
                    if (1, 154) in vm.vars:
                        progress.append(vm.vars[(1, 154)])
                    saw_scope |= vm.states.get((1, fixture["expected"]["scope_state_index"]), False)
            self.assertTrue(progress, "the projectile itself must receive ch1 frames")
            self.assertLess(progress[0], 1)
            self.assertEqual(progress[-1], fixture["expected"]["curve_end"])
            self.assertTrue(saw_scope, "1EB9/node22 enables client-only model DataFlow initialization")
        self.assertFalse(fight.projectiles.sigma_scripts)
        self.assertFalse(component.instances)


if __name__ == "__main__":
    unittest.main()
