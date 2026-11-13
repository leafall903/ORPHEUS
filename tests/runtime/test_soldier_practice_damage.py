"""Reported missing Practice health feedback through actual Soldier command input.

Requires the original three training-body records in the external graph profile.
The modeled peers establish decoded server feedback, with client rendering pending.
"""

import json
import unittest
from pathlib import Path

from ow174.game import combat
from ow174.game.bits import BitReader, BitWriter
from ow174.game.collision import flat_world
from ow174.game.script import graph
from ow174.game.script.bot import NAMEPLATE_ENABLED, practice_nameplate
from ow174.game.script.driver import OWNER_HEALTH
from tests.runtime.test_game_combat import FakeClient, Run, aim


class RecordingClient(FakeClient):
    def __init__(self, player):
        super().__init__(player)
        self.bot_updates = []

    def queue_bot_live(self, entity, snapshot):
        self.bot_updates.append((entity, snapshot))


class SoldierPracticeDamageTests(unittest.TestCase):
    def test_rifle_register_and_nameplate_agree_for_original_training_bodies(self):
        path = Path(__file__).resolve().parents[2] / "fixtures/behavior/soldier-practice-damage-v1.json"
        contract = json.loads(path.read_text())
        inputs, expected = contract["input"], contract["expected"]
        bodies = [int(value, 16) for value in inputs["training_bodies"]]
        missing = [value for value in bodies if graph.entity_body(0x0400000000000000 | value) is None]
        if missing:
            self.skipTest("external original training body metadata required")
        for body in bodies:
            with self.subTest(body=f"{body:04X}"):
                run = Run(names=("Soldier", "Observer"))
                for _ in range(len(run.match.bots) + 2):
                    run.step({})
                original_bots = list(run.match.bots)
                soldier, observer = run.players
                soldier.client = RecordingClient(soldier)
                observer.client = RecordingClient(observer)
                run.match.collision_world = flat_world(0)
                soldier.mover.position = list(inputs["shooter_position"])
                observer.mover.position = [20, 0, 0]
                soldier.mover.body = observer.mover.body = None
                bot = next(actor for actor in original_bots if actor.body & 0xFFFF == body)
                self.assertIsNotNone(bot.body_script)
                self.assertIsNone(bot.body_script.component.fault)
                run.match.bots[:] = [bot]
                bot.team = 1 - soldier.team
                bot.position = list(inputs["target_position"])
                bot.waypoints = []
                bot.keys = False
                target = next(item for item in run.match.combat._targets() if item.owner is bot)
                vitals = run.match.combat.vitals_of(target)
                self.assertEqual(vitals.total, inputs["initial_health"][f"{body:04X}"])
                soldier.client.reliable.clear()
                yaw, pitch = aim(run.match.combat._eye(soldier),
                                 (bot.position[0], bot.position[1] + inputs["aim_height"], bot.position[2]))
                for buttons in inputs["buttons"]:
                    run.step({soldier: (yaw, pitch, buttons)})
                self.assertEqual(vitals.total, expected["target_health"][f"{body:04X}"])
                self.assertEqual(bot.body_script.component.entity_var(OWNER_HEALTH).value(), vitals.total)
                hits = soldier.client.messages(combat.HIT)
                self.assertEqual([hit["+0x78"]["+0x4"] for hit in hits], expected["owner_hit_damage"])
                self.assertTrue(all(hit["+0x78"]["+0x0"]["+0x0"] == bot.entity for hit in hits))
                snapshot = bot.body_script.latest_live
                child = practice_nameplate(snapshot)
                self.assertIsNotNone(child)
                self.assertEqual(snapshot.vars[(child, NAMEPLATE_ENABLED)][0], expected["nameplate_enabled"])
                for viewer in (soldier, observer):
                    updates = [state for entity, state in viewer.client.bot_updates if entity == bot.entity]
                    self.assertTrue(updates)
                    self.assertEqual(updates[-1].vars[(child, NAMEPLATE_ENABLED)][0],
                                     expected["nameplate_enabled"])


class EnemyHeroSelectionTests(unittest.TestCase):
    def test_normal_movement_respects_live_enemies_and_passes_allies_and_corpses(self):
        from ow174.game import npc_console
        from ow174.game.commands import Command

        for kind in ('training', 'hero', 'ally', 'dead'):
            with self.subTest(kind=kind):
                run = Run()
                soldier = run.players[0]
                target = next(bot for bot in run.match.bots if bot.team != soldier.team)
                run.match.bots.clear()
                run.match._pending_bot_scripts.clear()
                run.match.collision_world = flat_world(0)
                soldier.mover.position = [0, 0, 0]
                soldier.mover.body = None
                if kind == 'training':
                    target.firing = False
                    target.route = []
                    target.waypoints = []
                    run.match.bots.append(target)
                else:
                    npc_console.Console({}).press('hero', run.match, soldier, hero='Soldier: 76')
                    target = run.match.bots[-1]
                target.position = (0, 0, 3)
                if kind == 'ally':
                    target.team = soldier.team
                if kind == 'dead':
                    target.dead = True
                for tick in range(1001, 1081):
                    run.match.update(tick * combat.TICK, tick)
                    soldier.take_commands([Command(tick, forward=127)])
                if kind in ('training', 'hero'):
                    self.assertLess(soldier.mover.position[2], target.position[2])
                    self.assertGreater(soldier.mover.position[2], 1)
                else:
                    self.assertGreater(soldier.mover.position[2], 5)

    def test_selected_hero_has_native_identity_real_damage_and_shared_cleanup(self):
        from ow174.game import npc_console
        from ow174.game.content import SOLDIER
        from ow174.game.world import OP_CREATE
        from tests.runtime.test_game_repeated_lifecycles import entity_record

        run = Run(names=("Soldier", "Observer"))
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        run.match.collision_world = flat_world(0)
        soldier, observer = run.players
        soldier.mover.position, observer.mover.position = [0, 0, 0], [20, 0, 0]
        soldier.mover.body = observer.mover.body = None
        console = npc_console.Console({})
        choices = console.press('heroes', None, None)['choices']
        self.assertIn(f'0x{SOLDIER:016X}', [item['guid'] for item in choices])
        result = console.press('hero', run.match, soldier, hero='Soldier: 76')
        target = run.match.bots[-1]
        self.assertEqual((target.hero, target.team), (SOLDIER, 1 - soldier.team))
        self.assertEqual(result['entity'], target.entity)
        record = run.match._bot_create_update(target).build(14)
        payload = BitWriter()
        payload.bits(record.count, 14)
        payload.append(record)
        fields = entity_record(BitReader(payload.getvalue()), OP_CREATE)
        self.assertEqual(fields[123], {0: SOLDIER})
        for _ in range(12):
            run.step({})
        self.assertIsNotNone(target.body_script)
        self.assertIsNone(target.body_script.component.fault)
        yaw, pitch = aim(run.match.combat._eye(soldier),
                         (target.position[0], target.position[1] + .8, target.position[2]))
        run.step({soldier: (yaw, pitch, 1)})
        for _ in range(2):
            run.step({})
        self.assertEqual(run.match.combat.vitals[target.entity].total, 180)
        self.assertEqual(soldier.client.messages(combat.HIT)[-1]['+0x78']['+0x4'], 20)
        removed = console.press('-', run.match, soldier)
        self.assertIn(target.entity, removed['entities'])
        self.assertNotIn(target, run.match.bots)

    def test_invalid_hero_selection_leaves_the_match_unchanged(self):
        from ow174.game import npc_console

        run = Run()
        before = tuple(run.match.bots), run.match.combat.next_bot
        with self.assertRaisesRegex(ValueError, 'Select a hero'):
            npc_console.Console({}).press('hero', run.match, run.players[0], hero='missing hero')
        self.assertEqual((tuple(run.match.bots), run.match.combat.next_bot), before)
