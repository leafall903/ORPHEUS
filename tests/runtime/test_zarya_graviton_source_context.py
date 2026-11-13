"""Original 03E2 Pet ownership must survive field create retransmission."""

import json
import unittest
from pathlib import Path
from unittest.mock import patch

import test_zarya_reported_presentation as presentation

from ow174.game import graviton, world
from ow174.game.match import TEAM_BIT


class ZaryaGravitonSourceContextTests(unittest.TestCase):
    def test_actual_cast_field_keeps_pet_source_on_every_viewer_create(self):
        spec = json.loads((Path(__file__).resolve().parents[2]
                           / "fixtures/behavior/zarya-graviton-source-context-v1.json").read_text())
        run, caster, _, _ = presentation.ZaryaPresentationTests().scene()
        match, fight = run.match, run.match.combat
        caster.body_script.set_ult_charge(2100)
        run.step({caster: (0, 0, 32)})
        rocket, = fight.rockets
        source, team = rocket.source_body, rocket.team
        caster.team = 1 - team  # landing keeps the cast context
        for _ in range(90):
            run.step({})
            if fight.zarya_gravitons.fields:
                break
        field, = fight.zarya_gravitons.fields
        actor = fight.deployables[field.entity]
        self.assertEqual(actor.definition, graviton.FIELD_ENTITY)
        self.assertEqual(actor.source_body, source)
        self.assertEqual(actor.pet_owner, source)
        self.assertEqual(actor.team, team)
        self.assertEqual(field.radius, spec["field_radius"])
        creates = []
        for viewer in run.players:
            create, = [item for item in viewer.client.entities
                       if item.entity == actor.entity and item.op == world.OP_CREATE]
            creates.append(create)
            with patch.object(world, "create", wraps=world.create) as build:
                self.assertTrue(create.build(0).getvalue())
            self.assertEqual(build.call_args.args[2][spec["pet_component"]], [source])
            self.assertEqual(build.call_args.args[2][26], [TEAM_BIT << team])
        for _ in range(field.end_tick - run.frame + 1):
            run.step({})
        self.assertNotIn(actor.entity, fight.deployables)
        self.assertFalse(fight.zarya_gravitons.fields)
        match.switch_hero(caster, caster.hero, preserve_pose=True, start_frame=18000)
        self.assertNotEqual(caster.body, source)
        with patch.object(world, "create", wraps=world.create) as build:
            creates[0].build(0)
        self.assertEqual(build.call_args.args[2][spec["pet_component"]], [source])
