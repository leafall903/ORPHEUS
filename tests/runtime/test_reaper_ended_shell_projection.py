"""Real owner chunks introduce ended Reaper shells with their pre-shot ammo."""

import json
import unittest
from pathlib import Path

from ow174.game import combat, reaper
from ow174.game.script.decode import read_chunk
from tests.runtime import test_game_reaper as reaper_tests
from tests.runtime.test_game_combat import aim


class ReaperEndedShellProjectionTests(unittest.TestCase):
    def test_eight_real_shells_keep_start_ammo_once_and_then_settle(self):
        path = Path(__file__).resolve().parents[2] / 'fixtures/behavior/reaper-ended-shell-projection-v1.json'
        case = json.loads(path.read_text(encoding='utf-8'))
        inputs, expected = case['input'], case['expected']
        run, owner, victim, target = reaper_tests.ReaperTests().scenario()
        fight, script = run.match.combat, owner.body_script
        weapon = next(i for i in script.component.instances.values() if i.graph.index == reaper.SHOTGUN_GRAPH)
        health = fight.vitals_of(target)
        health.now[combat.HEALTH_POOL] = inputs['victim_health']
        x, y, z = victim.mover.position
        heading = aim(fight._eye(owner), (x, y + 1, z))
        for offset in range((inputs['shells'] - 1) * inputs['spacing_frames'] + inputs['tail_frames']):
            run.step({owner: (*heading, 1 if offset % inputs['spacing_frames'] == 0 else 0)})
        self.assertEqual(len(owner.client.messages(combat.HIT)), inputs['shells'] * 20)
        self.assertEqual(health.total, inputs['victim_health'] - inputs['shells'] * 120)
        self.assertEqual(weapon.vars[53].value(), 0)
        known = {i.id: i.graph.index for i in script.component.instances.values()}
        states, variables, rows = {}, {}, []
        for update in owner.client.entities:
            if update.entity != owner.body or update.chunk is None:
                continue
            frame = read_chunk(update.chunk.getvalue(), update.chunk.count, known)
            for iid, item in frame.instances.items():
                if item.graph:
                    known[iid] = item.graph
                if iid == weapon.id:
                    states.update(item.states)
                    variables.update(item.vars)
            volley = states.get(6)
            if not frame.owner or not volley or not volley[1].get('counter'):
                continue
            rows.append({'counter': volley[1]['counter'], 'active': volley[0],
                         'ammo': variables[53][0], 'gun': variables[58][0],
                         'cosmetics': [states[0][0], states[1][0]], 'cmfd': frame.cmfd})
        for counter, ammo in enumerate(expected['introduction_ammo'], 1):
            row = next(row for row in rows if row['counter'] == counter)
            self.assertFalse(row['active'])
            self.assertEqual(row['ammo'], ammo)
            self.assertEqual(row['cosmetics'], [True, True])
            self.assertEqual(row['gun'].kind, 1)
            self.assertEqual(row['gun'].instance, weapon.id)
            self.assertEqual(row['gun'].state, (counter - 1) % 2)
        last = [row for row in rows if row['counter'] == inputs['shells']]
        self.assertEqual(last[-1]['ammo'], expected['settled_ammo'])
        self.assertGreater(last[-1]['cmfd'], last[0]['cmfd'])
