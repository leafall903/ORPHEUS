"""A real joining viewer must receive the still-live Echo volley identities."""
import json
import unittest
from pathlib import Path

from test_echo_combat import EchoPrimaryTests, EchoStickyTests
from test_game_combat import in_world

from ow174.game import combat, projectiles
from ow174.game.content import heroes
from ow174.game.world import OP_DESTROY

EXPECTED = json.loads((Path(__file__).resolve().parents[2]
                       / 'fixtures/behavior/echo-live-observer-v1.json').read_text())['expected']


class EchoLiveObserverTests(unittest.TestCase):
    def check_join(self, sticky, initial_viewer=True, distance=95):
        run, shooter, _, target, angles = EchoPrimaryTests().session(
            npc=not initial_viewer, distance=distance)
        if sticky:
            EchoStickyTests().launch(run, shooter, angles)
        else:
            run.step({shooter: (*angles, 1)})
        rockets = tuple(run.match.combat.rockets)
        self.assertEqual(len(rockets), EXPECTED['sticky_members' if sticky else 'primary_members'])
        originals = {id(rocket.shot): rocket.shot.echo_launch for rocket in rockets}
        existing = {id(rocket.shot): tuple(rocket.shot.observers) for rocket in rockets}
        late = run.match.add_player(3, 'Joining viewer', shooter.hero.guid, shooter.team, False)
        in_world(run.match, late)
        run.match.switch_hero(late, heroes()[shooter.hero.guid])
        late.mover.position[0] += 30
        run.players.append(late)
        for _ in range(15):
            run.step({})
        self.assertIsNotNone(run.match.combat.observers.acknowledged_stream(late, shooter.body))
        self.assertTrue(all(rocket in run.match.combat.rockets for rocket in rockets))
        deliveries = [d for rocket in rockets for d in rocket.shot.observers if d.viewer is late]
        self.assertEqual(len(deliveries), len(rockets))
        for rocket in rockets:
            delivery, = [d for d in rocket.shot.observers if d.viewer is late]
            original = originals[id(rocket.shot)]
            self.assertEqual(delivery.launch.entity, shooter.body)
            self.assertEqual(delivery.record.frame, original.frame)
            self.assertEqual(delivery.record.origin, original.origin)
            self.assertEqual(delivery.record.direction, original.direction)
            self.assertEqual(delivery.record.shot, original.shot)
            deadline = min((d.deadline for d in existing[id(rocket.shot)]),
                           default=projectiles.owner_to_world(rocket.shot.clock_origin, original.frame)
                           + rocket.shot.bound + projectiles.DELIVERY_GRACE_TICKS)
            self.assertEqual(delivery.deadline, deadline)
            self.assertTrue(delivery.creation.received)
            self.assertTrue(delivery.launch_arrival.received)
            self.assertTrue(all(d in rocket.shot.observers for d in existing[id(rocket.shot)]))
        self.assertEqual(len({id(d.launch) for d in deliveries}), 6 if sticky else 1)
        ids = {d.entity for d in deliveries}
        for _ in range(240):
            run.step({})
        self.assertTrue(all(rocket not in run.match.combat.rockets for rocket in rockets))
        self.assertTrue(ids <= {item.entity for item in late.client.entities if item.op == OP_DESTROY})
        self.assertTrue(all(sum(d.viewer is late for d in rocket.shot.observers) == 1 for rocket in rockets))
        if sticky and distance == 5:
            self.assertEqual(run.match.combat.vitals_of(target).total, 200-EXPECTED['sticky_total_damage'])
            self.assertEqual(len(shooter.client.messages(combat.HIT)), EXPECTED['sticky_hit_notices'])

    def test_joining_viewer_receives_three_live_primary_pellets(self):
        self.check_join(False)

    def test_joining_viewer_receives_six_live_stickies(self):
        self.check_join(True)

    def test_first_observer_can_join_after_sticky_emission(self):
        self.check_join(True, initial_viewer=False)

    def test_join_after_contacts_does_not_repeat_sticky_damage_or_hits(self):
        self.check_join(True, distance=5)
