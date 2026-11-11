"""Actual pilot Q creates an unpossessed native-definition mech, not Matrix acceptance."""

import math
import os
import unittest
from pathlib import Path
from unittest.mock import patch

import test_dva_placement as placement
from test_game_combat import KANEZAKA_DM, Run, aim

from ow174.game import combat, dva, npc
from ow174.game.body_vm_snapshot import export_component_vm, snapshot_digest
from ow174.game.commands import FIRE, SECONDARY
from ow174.game.content import heroes
from ow174.game.dva import HERO as DVA
from ow174.game.dva import MECH
from ow174.game.mover import mover_data
from ow174.game.script import expr, graph
from ow174.game.zenyatta import HERO as ZENYATTA
from ow174.paths import DATA_DIR


@unittest.skipUnless(os.environ.get('ORPHEUS_DVA_RUNTIME'), 'requires explicit prepared mech metadata')
class DvaFactoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        runtime = Path(os.environ['ORPHEUS_DVA_RUNTIME'])
        if DATA_DIR.resolve() != (runtime/'data').resolve():
            raise AssertionError('factory tests require the actual44-row runtime, no mover fallback')
        if graph.entity_body(MECH) is None:
            raise AssertionError('missing actual0407 entity definition')
        data = mover_data(MECH)
        if not all(math.isclose(a, b, abs_tol=1e-5) for a, b in
                   ((data.stand_height, 2), (data.stand_radius, .7), (data.run_forward, 5.5))):
            raise AssertionError('factory tests require actual0407 mover geometry')

    def arena(self, *, friendly=False, cast=True):
        helper = placement.DvaPlacementTests()
        run, pilot, zen = helper.arena()
        run.match.switch_hero(zen, heroes()[ZENYATTA])
        zen.team = pilot.team if friendly else 1-pilot.team
        x, y, z = pilot.mover.position
        zen.mover.position = [x, y, z+10]
        zen.mover.body = None
        if not cast:
            return helper, run, pilot, zen
        instance, state, calls = helper.cast(run, pilot, frames=20)
        entity = instance.vars[38].value()
        self.assertIsInstance(entity, expr.Entity)
        actor = next(b for b in run.match.bots if b.entity == entity.id)
        self.assertIsNotNone(actor.body_script)
        return run, pilot, zen, actor, state, calls

    def test_actual_q_allocates_independent_mech_with_own_graphs_pools_and_source_identity(self):
        run, pilot, _, actor, state, calls = self.arena()
        self.assertIn((15, 'STU_B6E3C77D'), calls)
        self.assertNotEqual(actor.entity, pilot.body)
        self.assertEqual(actor.body, MECH)
        self.assertEqual(tuple(actor.position), tuple(state.result[1]))
        self.assertEqual(actor.team, pilot.team)
        self.assertEqual(actor.spawner['dva_source_body'], pilot.body)
        self.assertEqual(actor.spawner['dva_controller'], pilot.entity)
        self.assertFalse(actor.spawner['manual_spawn'])
        self.assertTrue(actor.retain_corpse)
        vitals = run.match.combat.vitals[actor.entity]
        self.assertEqual((vitals.total, vitals.most[0], vitals.most[1]), (600, 300, 300))
        self.assertEqual(actor.body_script.component.entity_var(253).value(), 600)
        indexes = {i.graph.index for i in actor.body_script.component.instances.values()}
        self.assertTrue({0x0033, 0x0276, 0x02CC, 0x050A} <= indexes)

    def test_actual_zen_primary_burst_and_melee_damage_the_mech_armour(self):
        for kind, expected in (('primary', 43), ('burst', 215), ('melee', 25)):
            with self.subTest(kind=kind):
                run, _, zen, actor, _, _ = self.arena()
                if kind == 'melee':
                    zen.mover.position = [actor.position[0], actor.position[1], actor.position[2]+1.8]
                    zen.mover.body = None
                angles = aim(run.match.combat._eye(zen),
                             (actor.position[0], actor.position[1]+1, actor.position[2]))
                if kind == 'burst':
                    for _ in range(130):
                        run.step({zen: (*angles, SECONDARY)})
                else:
                    run.step({zen: (*angles, FIRE if kind == 'primary' else 0x800)})
                for _ in range(60):
                    run.step({zen: (*angles, 0)})
                vitals = run.match.combat.vitals[actor.entity]
                self.assertEqual(vitals.total, 600-expected)
                self.assertEqual(vitals.now[0], 300)
                self.assertEqual(actor.body_script.component.entity_var(253).value(), 600-expected)
                hits = [hit['+0x78'] for hit in zen.client.messages(combat.HIT)
                        if hit['+0x78']['+0x0']['+0x0'] == actor.entity]
                self.assertEqual(sum(hit['+0x4'] for hit in hits), expected)

    def test_actual_friendly_fire_and_insufficient_charge_do_not_damage_or_create_mech(self):
        run, _, zen, actor, _, _ = self.arena(friendly=True)
        angles = aim(run.match.combat._eye(zen),
                     (actor.position[0], actor.position[1]+1, actor.position[2]))
        run.step({zen: (*angles, FIRE)})
        for _ in range(30):
            run.step({})
        self.assertEqual(run.match.combat.vitals[actor.entity].total, 600)
        self.assertFalse(zen.client.messages(combat.HIT))
        helper, run, pilot, _ = self.arena(cast=False)
        instance, _, calls = helper.cast(run, pilot, charge=False)
        self.assertIsNone(instance.vars[38].value())
        self.assertFalse(run.match.bots)
        self.assertFalse(any(cls == 'STU_B6E3C77D' for _, cls in calls))

    def test_missing_mech_definition_stops_factory_without_publishing_or_following_success(self):
        helper, run, pilot, _ = self.arena(cast=False)
        # Explicit negative input-service fixture; not a combat/native-query claim.
        with patch.object(graph, 'entity_body', return_value=None):
            instance, state, calls = helper.cast(run, pilot, frames=20)
        self.assertTrue(state.result[0])
        self.assertIn((15, 'STU_B6E3C77D'), calls)
        self.assertIsNone(instance.vars[38].value())
        self.assertFalse(run.match.bots)
        self.assertNotIn((43, 'STUStatescriptActionSendGameMessage'), calls)

    def test_factory_publishes_mech_before_entry_and_manual_cleanup_preserves_it(self):
        run, pilot, _, actor, _, _ = self.arena()
        self.assertIsNone(dva.controlled_mech(pilot))
        self.assertNotEqual(pilot.body, actor.entity)
        self.assertNotIn(0x02CC, {i.graph.index for i in pilot.body_script.component.instances.values()})
        self.assertFalse(any(s.active for i in actor.body_script.component.instances.values()
                             if i.graph.index == 0x02CC for s in i.states.values() if s.node.pos == 9))
        self.assertEqual(npc.remove_spawned(run.match), ())
        self.assertIn(actor, run.match.bots)

    def test_created_mech_vm_exports_and_records_actual_received_health(self):
        run, _, zen, actor, _, _ = self.arena()
        component = actor.body_script.component
        before = snapshot_digest(export_component_vm(component))
        angles = aim(run.match.combat._eye(zen),
                     (actor.position[0], actor.position[1]+1, actor.position[2]))
        run.step({zen: (*angles, FIRE)})
        for _ in range(20):
            run.step({})
        self.assertEqual(run.match.combat.vitals[actor.entity].total, 557)
        self.assertNotEqual(before, snapshot_digest(export_component_vm(component)))

    def test_third_ffa_player_q_uses_native_ffa_filter_and_mech_remains_an_enemy_target(self):
        run = Run(KANEZAKA_DM, ('Zen', 'Other', 'Pilot'))
        zen, other, pilot = run.players
        run.match.bots.clear()
        run.match.switch_hero(pilot, heroes()[DVA])
        run.match.switch_hero(zen, heroes()[ZENYATTA])
        self.assertEqual(pilot.team, 2)
        x, y, z = pilot.mover.position
        zen.mover.position, zen.mover.body = [x, y, z+10], None
        other.mover.position, other.mover.body = [x+20, y, z], None
        _, _, calls = placement.DvaPlacementTests().cast(run, pilot, frames=20)
        self.assertFalse(pilot.body_script_failed)
        self.assertIsNotNone(pilot.body_script)
        self.assertIn((15, 'STU_B6E3C77D'), calls)
        actor = next(b for b in run.match.bots if b.body == MECH)
        self.assertEqual(actor.team, 4)
        angles = aim(run.match.combat._eye(zen),
                     (actor.position[0], actor.position[1]+1, actor.position[2]))
        run.step({zen: (*angles, FIRE)})
        for _ in range(20):
            run.step({})
        self.assertEqual(run.match.combat.vitals[actor.entity].total, 557)


if __name__ == '__main__':
    unittest.main()
