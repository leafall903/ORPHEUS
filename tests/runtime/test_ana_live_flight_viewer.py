"""Late source admission on actual Ana skills, using the immediate-ACK model."""

import dataclasses
import json
import unittest
from pathlib import Path

from ow174.game.content import heroes
from ow174.game.world import OP_CREATE, OP_DESTROY
from tests.runtime.test_game_ana import ANA, TEAM_MAP, grenade_aim
from tests.runtime.test_game_combat import Run, aim, in_world

CONTRACT = json.loads((Path(__file__).resolve().parents[2]
                       / "fixtures/behavior/ana-live-flight-viewer-v1.json").read_text())


class AnaLiveFlightViewerTests(unittest.TestCase):
    def check_join(self, button):
        run = Run(TEAM_MAP, ("Ana", "Enemy", "Late"))
        ana, enemy, late = run.players
        run.match.switch_hero(ana, heroes()[ANA])
        ana.team, enemy.team, late.team = 0, 1, 0
        x, y, z = ana.mover.position
        enemy.mover.position = [x, y, z + CONTRACT["input"]["target_distance"]]
        late.mover.position = [x + 20, y, z]
        # This connection has not joined: no source watch can exist at launch.
        late.client, late.spawned = None, False
        run.players.remove(late)
        late.seen.clear()
        for key in list(run.match.combat.observers.watches):
            if key[0] == late.slot:
                del run.match.combat.observers.watches[key]
        for _ in range(30):
            run.step({})
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is enemy)
        point = (target.feet[0], target.feet[1] + 1, target.feet[2])
        direction = (grenade_aim(run, ana, point) if button == 16 else aim(fight._eye(ana), point))
        run.step({ana: (*direction, button)})
        for _ in range(24):
            if any(r.shooter is ana for r in fight.rockets):
                break
            run.step({ana: (*direction, 0)})
        rocket, = [r for r in fight.rockets if r.shooter is ana]
        shot = rocket.shot
        original = shot.observers[0].record
        deadline = min(d.deadline for d in shot.observers)
        self.assertFalse(any(d.viewer is late for d in shot.observers))
        in_world(run.match, late)
        run.players.append(late)
        for _ in range(4):
            run.step({})
            fight.observers.update()
        self.assertIn(rocket, fight.rockets)
        deliveries = [d for d in shot.observers if d.viewer is late]
        self.assertEqual(len(deliveries), CONTRACT["expected"]["late_deliveries"])
        delivery, = deliveries
        self.assertEqual(dataclasses.replace(delivery.record, ids=original.ids), original)
        self.assertLessEqual(delivery.deadline, deadline)
        self.assertTrue(delivery.creation.received and delivery.launch_arrival.received)
        for _ in range(100):
            run.step({})
            fight.observers.update()
        self.assertNotIn(rocket, fight.rockets)
        self.assertEqual(fight.vitals_of(target).total, CONTRACT["expected"]["target_health"][str(button)])
        self.assertEqual(fight.is_asleep(enemy), button == 8)
        entity, = delivery.record.ids
        self.assertEqual([sum(u.entity == entity and u.op == op for u in late.client.entities)
                          for op in (OP_CREATE, OP_DESTROY)], CONTRACT["expected"]["create_destroy_counts"])
        self.assertEqual(len([d for d in shot.observers if d.viewer is late]), 1)
        # A different connection admitted after contact must not resurrect it.
        in_world(run.match, late)
        for _ in range(4):
            run.step({})
        self.assertFalse(any(u.entity == entity for u in late.client.entities))

    def test_grenade_live_join_and_terminal_boundary(self):
        self.check_join(16)

    def test_sleep_dart_live_join_and_terminal_boundary(self):
        self.check_join(8)
