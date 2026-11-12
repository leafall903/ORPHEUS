"""Actual five-NPC WholeHog movement uses one prepared movement host."""

import os
import unittest
from dataclasses import replace
from pathlib import Path

from test_game_combat import Run, KANEZAKA_DM, aim
from ow174.game import npc, mover
from ow174.game.content import heroes
from ow174.game.body_vm_snapshot import export_component_vm, BodyVMSnapshotError
from ow174.game.server_snapshot import snapshot_digest
import test_server_snapshot as stored
from test_server_journal import pick


class WholeHogNpcTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.definitions = npc.load_catalog(Path(os.environ['ORPHEUS_NPC_DATA_DIR']) / 'pve_npcs_174.json')

    def scene(self, definition):
        run = Run(KANEZAKA_DM, ('Hog',))
        hog = run.players[0]
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        run.match.switch_hero(hog, heroes()[0x02E0000000000040])
        x, y, z = hog.mover.position
        bot = npc.spawn(run.match, definition, (x, y, z + 4), team=1)
        bot.route = []
        bot.firing = False
        for _ in range(30):
            run.step({})
        fight = run.match.combat
        old = bot.impulse_mover or bot.ensure_manual_mover(fight.tick, fight._collision(), mover.MOVER)
        self.assertIsNotNone(old)
        target = next(target for target in fight._targets() if target.owner is bot)
        low, high = target.segment()
        look = aim(fight._eye(hog), tuple((a + b) / 2 for a, b in zip(low, high)))
        hog.body_script.set_ult_charge(2240)
        run.step({hog: (*look, 32)})
        for _ in range(50):
            run.step({hog: (*look, 0)})
            if bot.entity in fight.knockbacks.whole_hog_motion:
                break
        self.assertIn(bot.entity, fight.knockbacks.whole_hog_motion)
        for ability in hog.body_script.ultimates:
            ability.stop()
        hog.body_script.component.run_now()
        return run, hog, bot, old, fight.knockbacks.whole_hog_motion[bot.entity]

    def test_five_real_bodies_keep_existing_mover_and_cleanup(self):
        for definition in self.definitions.values():
            with self.subTest(npc=definition.name):
                run, hog, bot, old, motion = self.scene(definition)
                self.assertIs(bot.impulse_mover, old)
                self.assertIs(motion.npc_mover, old)
                self.assertNotIn(bot.entity, run.match.combat.knockbacks.active)
                before = tuple(bot.position)
                run.step({})
                self.assertGreater(bot.position[2], before[2])
                self.assertGreater(bot.position[1], before[1])
                self.assertIs(bot.impulse_mover, old)
                held = tuple(bot.position)
                run.match._advance_bots(run.match.tick)
                run.match.combat.update(run.match.combat.now, run.match.tick, frames=False)
                self.assertEqual(tuple(bot.position), held)
                # The associated AI controller is a foreign Component. Standalone
                # export must stay closed; the full domain is proved below.
                with self.assertRaisesRegex(BodyVMSnapshotError, 'NpcContext'):
                    export_component_vm(bot.body_script.component)
                npc.remove_spawned(run.match)
                self.assertNotIn(bot.entity, run.match.combat.knockbacks.whole_hog_motion)

    def test_explicit_definition_data_remains_the_same_damping_input(self):
        definition = next(iter(self.definitions.values()))
        data = replace(mover.mover_data(definition.body), gravity_scale=3.0)
        definition = replace(definition, mover=data)
        run, _, bot, old, motion = self.scene(definition)
        self.assertIs(bot.movement_data, data)
        self.assertIs(old.data, data)
        run.step({})
        self.assertIs(bot.impulse_mover, old)
        self.assertAlmostEqual(motion.velocity[1], 1.32, places=6)

    def test_actual_session_stores_one_coupled_npc_mover_and_external_state(self):
        case = stored.ServerSnapshotTests()
        session, peers, players = case.scene(two=True)
        hog = players[0]
        now = 101.1
        peers[0].message(21616, pick(hog.entity, 0x02E0000000000040), now=now)
        for _ in range(8):
            now += .016
            session.tick(now)
            for peer in peers:
                peer.drain()
                peer.send(now=now)
        fight = hog.match.combat
        definition = next(iter(self.definitions.values()))
        x, y, z = hog.mover.position
        bot = npc.spawn(hog.match, definition, (x, y, z + 4), team=1)
        bot.firing = False
        bot.route = []
        original = bot.impulse_mover or bot.ensure_manual_mover(fight.tick, fight._collision(), mover.MOVER)
        hog.body_script.set_ult_charge(2240)
        low, high = next(target for target in fight._targets() if target.owner is bot).segment()
        look = aim(fight._eye(hog), tuple((a + b) / 2 for a, b in zip(low, high)))
        peers[0].commands(hog.body_script.frame + 1, 1, 32, yaw=look[0], pitch=look[1], now=now)
        for _ in range(50):
            if bot.entity in fight.knockbacks.whole_hog_motion:
                break
            now += .016
            session.tick(now)
            for peer in peers:
                peer.drain()
                peer.send(now=now)
            peers[0].commands(hog.body_script.frame + 1, 1, 0, yaw=look[0], pitch=look[1], now=now)
        self.assertIn(bot.entity, fight.knockbacks.whole_hog_motion)
        motion = fight.knockbacks.whole_hog_motion[bot.entity]
        self.assertIs(motion.npc_mover, original)
        self.assertIs(bot.impulse_mover, original)
        context = bot.body_script.component.world.control_context
        self.assertIs(context.body_component, bot.body_script.component)
        self.assertIsNotNone(context.controller_component)
        def digest():
            return snapshot_digest(case.capture(session))
        baseline = digest()
        velocity = motion.velocity
        motion.velocity = (velocity[0], velocity[1] + .1, velocity[2])
        self.assertNotEqual(digest(), baseline)
        motion.velocity = velocity
        self.assertEqual(digest(), baseline)
