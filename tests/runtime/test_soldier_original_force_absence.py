"""Original non-force actions do not manufacture character impulse events."""

import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from ow174.game import defenses, projectiles
from ow174.game.collision import flat_world
from tests.runtime.test_game_combat import Run, aim

FIXTURE_ROOT = Path(os.environ.get(
    'ORPHEUS_BEHAVIOR_FIXTURES', Path(__file__).resolve().parents[2] / 'fixtures/behavior',
))


class SoldierOriginalForceAbsenceTests(unittest.TestCase):
    def test_actual_rifle_field_and_visor_do_not_add_unauthored_force(self):
        contract = json.loads((FIXTURE_ROOT / 'soldier-original-force-absence-v1.json').read_text('utf-8'))
        run = Run(names=('Soldier', 'Enemy'))
        soldier, enemy = run.players
        run.match.collision_world = flat_world(0)
        npc_ids = tuple(bot.entity for bot in run.match.bots)
        self.assertEqual(len(npc_ids), 15)
        for player, point in ((soldier, (1000, 0, 1000)), (enemy, (1000, 0, 1010))):
            player.mover.position[:] = point
            player.mover.body = None
            player.body_script.create_arrived = True
        for _ in range(20):
            run.step({})
        fight = run.match.combat
        targets = {target.owner: target for target in fight._targets()}
        enemy_pool = fight.vitals_of(targets[enemy])
        own_pool = fight.vitals_of(targets[soldier])
        yaw, pitch = aim(fight._eye(soldier), (1000, 1.1, 1010))
        before = enemy_pool.total
        with patch.object(fight.knockbacks, 'apply', wraps=fight.knockbacks.apply) as force:
            run.step({soldier: (yaw, pitch, 1)})
            for _ in range(10):
                run.step({})
            self.assertEqual(before - enemy_pool.total, contract['rifle']['damage'])
            # Synthetic injury only supplies a real Field recipient; no weapon force is injected.
            own_pool.damage(100)
            health = own_pool.total
            run.step({soldier: (0, 12000, 16)})
            for _ in range(40):
                run.step({soldier: (0, 12000, 0)})
                if any(shot.spawn == projectiles.FIELD_ENTITY for shot in fight.projectiles.shots):
                    break
            fields = [shot for shot in fight.projectiles.shots if shot.spawn == projectiles.FIELD_ENTITY]
            self.assertEqual(len(fields), 1, {
                "fault": str(soldier.body_script.component.fault),
                "rockets": [(r.source_graph, r.shot.spawn if r.shot else None) for r in fight.rockets],
                "shots": [shot.spawn for shot in fight.projectiles.shots],
            })
            for _ in range(210):
                run.step({})
            self.assertGreater(own_pool.total, health)
            script = soldier.body_script
            script.ult_seconds = 0
            script.set_ult_charge(script.component.entity_var(338).value())
            run.step({soldier: (0, 0, 32)})
            for _ in range(80):
                run.step({})
            self.assertTrue(defenses.soldier_visor_running(soldier))
            self.assertEqual(force.call_count, contract['expected_public_force_events'])
        self.assertEqual(tuple(bot.entity for bot in run.match.bots), npc_ids)
