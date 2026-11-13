"""Actual zero-multiplier Punch reaches the stationary canonical Trooper path."""
import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from test_doomfist_combat import DoomfistFixture
from test_game_combat import Run

from ow174.game import combat, doomfist, npc
from ow174.game.collision import flat_world
from ow174.game.content import heroes
from ow174.game.mover import MULTIPLY, mover_data


class PunchZeroNpcTests(unittest.TestCase):
    def test_actual_zero_punch_trooper_keeps_damage_and_hands_back_finite_mover(self):
        spec = json.loads((Path(__file__).resolve().parents[2] /
                           'fixtures/behavior/doomfist-punch-zero-npc-v1.json').read_text())
        run = Run(names=('Doomfist', 'Observer'))
        source, observer = run.players
        match = run.match
        fight = match.combat
        self.assertEqual(len(match.bots), spec['expected']['initial_npc_count'])
        match.collision_world = flat_world(0)
        match.switch_hero(source, heroes()[doomfist.HERO])
        origin = tuple(spec['inputs']['source_origin'])
        DoomfistFixture.place_player(source, origin)
        DoomfistFixture.place_player(observer, (1100, 0, 1100))
        catalog = npc.load_catalog(Path(os.environ.get(
            'ORPHEUS_NPC_CATALOG', str(Path(os.environ['ORPHEUS_RUNTIME_DIR']) /
                                     'data/pve_npcs_174.json'))))
        definition = next(row for row in catalog.values() if row.name == spec['inputs']['npc_name'])
        bot = npc.spawn(match, definition, (origin[0], origin[1], origin[2] +
                                          spec['inputs']['target_distance']), team=1-source.team)
        bot.route = ()
        bot.firing = False
        bot.keys = False
        bot.velocity = (0, 0, 0)
        run.step({})
        self.assertEqual(bot.body, int(spec['inputs']['npc_body'], 16))
        self.assertAlmostEqual(mover_data(bot.body).stand_height, 1.7)
        identities = tuple((b.entity, b.body, b.body_script) for b in match.bots)
        self.assertEqual(len(identities), spec['expected']['count_with_trooper'])
        other_health = {t.entity: fight.vitals_of(t).total for t in fight._targets()
                        if t.owner in match.bots and t.owner is not bot}
        target = next(t for t in fight._targets() if t.owner is bot)
        before = fight.vitals_of(target).total
        self.assertEqual(before, spec['expected']['initial_health'])
        position, script = tuple(bot.position), bot.body_script
        self.assertIsNone(bot.navigation)
        self.assertIsNone(bot.impulse_mover)
        source.body_script.component.set(10959, spec['inputs']['caller_multiplier'])
        serial = fight.knockbacks.serial
        damage = []
        actual_damage = fight._damage

        def observe(*args, **kwargs):
            accepted = actual_damage(*args, **kwargs)
            if args[1].entity == bot.entity and args[4] == doomfist.PUNCH and accepted:
                damage.append(args[2])
            return accepted

        with patch.object(fight, '_damage', side_effect=observe), \
                patch.object(fight.knockbacks, 'apply', wraps=fight.knockbacks.apply) as force, \
                patch.object(fight.knockbacks, 'admit_modifiers',
                             wraps=fight.knockbacks.admit_modifiers) as finite:
            for _ in range(spec['inputs']['hold_frames']):
                run.step({source: (0, 0, 2)})
            run.step({})
            for _ in range(60):
                run.step({})
                if damage:
                    break
        self.assertEqual(len(damage), 1)
        self.assertAlmostEqual(before - fight.vitals_of(target).total, damage[0])
        self.assertEqual(len(source.client.messages(combat.HIT)), spec['expected']['hit_count'])
        self.assertEqual(force.call_count, spec['expected']['force_count'])
        self.assertEqual(finite.call_count, spec['expected']['finite_admissions'])
        self.assertEqual(fight.knockbacks.serial, serial)
        mover = bot.impulse_mover
        self.assertIsNotNone(mover)
        self.assertFalse(mover.external_impulse)
        self.assertFalse(mover.velocity_changes)
        self.assertNotIn(bot.entity, fight.knockbacks.active)
        self.assertTrue(fight.punches.controlled(bot.entity))
        mods = [m for key, m in mover.mods.items() if key[0] == -3]
        self.assertEqual({(m.index, m.op, m.priority, m.end-m.start) for m in mods},
                         {(4, MULTIPLY, 0, 20), (7, MULTIPLY, 1, 20)})
        run.step({})
        self.assertAlmostEqual(mover.values.decel_ground_base,
                               mover.data.decel_ground_base * .01, delta=1e-6)
        self.assertEqual(mover.values.decel_air_base, 0)
        for _ in range(22):
            run.step({})
        self.assertIsNone(bot.impulse_mover)
        self.assertEqual(mover.values.decel_ground_base, mover.data.decel_ground_base)
        self.assertFalse(any(key[0] == -3 for key in mover.mods))
        self.assertTrue(fight.punches.controlled(bot.entity))
        for _ in range(20):
            run.step({})
        self.assertFalse(fight.punches.controlled(bot.entity))
        self.assertFalse(script.component.input_suppressed)
        self.assertIsNone(script.component.fault)
        self.assertIs(bot.body_script, script)
        self.assertEqual(tuple(bot.position), position)
        self.assertEqual(bot.velocity, (0, 0, 0))
        current = tuple((b.entity, b.body, b.body_script) for b in match.bots)
        self.assertEqual(tuple((entity, model) for entity, model, _vm in identities),
                         tuple((entity, model) for entity, model, _vm in current))
        for (entity, _model, original_vm), (_entity, _body, current_vm) in zip(
                identities, current, strict=True):
            if original_vm is not None:
                self.assertIs(current_vm, original_vm)
            if current_vm is not None:
                # Previously absent training VMs may initialize normally during the cast.
                component = current_vm.component
                self.assertEqual(component.entity, entity)
                self.assertIs(component.world.match, match)
                self.assertIs(source.body_script.world._body_component(entity), component)
                self.assertIsNone(component.fault)
        for t in fight._targets():
            if t.entity in other_health:
                self.assertEqual(fight.vitals_of(t).total, other_health[t.entity])
        self.assertAlmostEqual(before-fight.vitals_of(target).total, damage[0])
        self.assertEqual(len(source.client.messages(combat.HIT)), 1)
        self.assertEqual(fight.knockbacks.serial, serial)
