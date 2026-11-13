"""Field creates retain the cast's faction, independently of logical team numbers."""

import json
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import test_zarya_reported_presentation as presentation
from test_game_combat import in_world

from ow174.game import bots, combat, graviton, world
from ow174.game.bits import BitReader
from ow174.game.match import FREE_FOR_ALL_TEAM, SOLDIER, TEAM_BIT


class ZaryaGravitonFactionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spec = json.loads((Path(__file__).resolve().parents[2]
                               / "fixtures/behavior/zarya-graviton-faction-v1.json").read_text())

    def cast(self, *, ffa):
        run, caster, enemy, ally = presentation.ZaryaPresentationTests().scene()
        match, fight = run.match, run.match.combat
        match.game_map = replace(match.game_map, free_for_all=ffa)
        caster.team = 7 if ffa else 1
        enemy.team, ally.team = 0, caster.team
        caster.body_script.set_ult_charge(2100)
        run.step({caster: (0, 0, 32)})
        rocket, = fight.rockets
        captured = rocket.shot.filter_bits
        self.assertEqual(captured, TEAM_BIT << (FREE_FOR_ALL_TEAM if ffa else caster.team))
        caster.team = 8 if ffa else 0  # the cast retains its original faction
        for _ in range(90):
            run.step({})
            if fight.zarya_gravitons.fields:
                break
        field, = fight.zarya_gravitons.fields
        return run, caster, rocket, field, fight.deployables[field.entity], captured

    def assert_create(self, create, expected, source):
        with patch.object(world, "create", wraps=world.create) as build:
            encoded = create.build(0).getvalue()
        self.assertEqual(build.call_args.args[2][72], [source])
        reader = BitReader(encoded)
        self.assertEqual(reader.bit(), 0)  # explicit entity definition
        self.assertEqual(reader.bit(), 1)  # small GUID index
        self.assertEqual(reader.bits(12), graviton.FIELD_ENTITY & 0xFFF)
        self.assertEqual(reader.bits(8), world.WITH_TRANSFORM | world.WITH_SCALE)
        reader.bits(10 * 32)  # position, quaternion and scale
        self.assertEqual(reader.bits(8), 2)
        self.assertEqual(reader.bits(8), self.spec["faction_component"])
        reader.align()
        self.assertEqual((reader.bits(8), reader.bits(8)), (1, 1))
        mask = reader.bits(8)
        faction = sum(reader.bits(8) << (8 * bit) for bit in range(8) if mask & (1 << bit))
        self.assertEqual(faction, expected)

    def exercise_viewers_and_join(self, *, ffa):
        run, _, rocket, field, actor, captured = self.cast(ffa=ffa)
        self.assertEqual(actor.filter_bits, captured)
        self.assertEqual(actor.team, rocket.team)
        for viewer in run.players:  # owner, same-team observer, opposing observer
            create, = [item for item in viewer.client.entities
                       if item.entity == actor.entity and item.op == world.OP_CREATE]
            self.assert_create(create, captured, rocket.source_body)
        late = run.match.add_player(99, "Late", SOLDIER, rocket.team, False)
        in_world(run.match, late)
        create, = [item for item in run.match.spawn_updates(late)
                   if item.entity == actor.entity and item.op == world.OP_CREATE]
        self.assert_create(create, captured, rocket.source_body)
        end = field.end_tick + 1
        run.match.update(end * combat.TICK, end, frames=False)
        self.assertFalse(run.match.combat.zarya_gravitons.fields)
        self.assertNotIn(actor.entity, run.match.combat.deployables)
        self.assert_create(create, captured, rocket.source_body)  # retained create stays frozen

    def test_actual_group_cast_keeps_faction_for_all_viewers_and_late_join(self):
        self.exercise_viewers_and_join(ffa=False)

    def test_actual_ffa_cast_uses_source_faction_and_keeps_lifetime(self):
        self.exercise_viewers_and_join(ffa=True)

    def test_npc_landing_without_player_capture_retains_original_team_bits(self):
        run, _, rocket, _, _, _ = self.cast(ffa=True)
        hero, data = next(iter(bots.table()["heroes"].items()))
        npc = bots.Bot(bots.BOT_ENTITY, {
            "hero": hero, "team": 1, "position": [20, 1, 20],
            "rotation": [0, 0, 0, 1], "graphs": [],
        }, data, {})
        run.match.bots.append(npc)
        npc_rocket = replace(rocket, shooter=npc, source_body=npc.entity,
                             source_entity=npc.entity, team=npc.team, shot=None)
        fight = run.match.combat
        fight.zarya_gravitons.landed(npc_rocket, (20, 1, 20), combat.TICK)
        field = fight.zarya_gravitons.fields[-1]
        self.assertEqual(fight.deployables[field.entity].filter_bits, TEAM_BIT << npc.team)
