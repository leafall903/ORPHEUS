"""Minimal production delivery checks for Sigma's existing actors."""
import unittest
from types import SimpleNamespace

import test_sigma_combat as fixtures
from ow174.game import observers, sigma_barrier, sigma_presentation, world
from ow174.game.content import SOLDIER, heroes
from ow174.game.server import Client


class SigmaPresentationTests(unittest.TestCase):
    def arena(self, distance=60):
        return fixtures.SigmaCombatTests().arena(offset=0, distance=distance)

    def test_existing_barrier_vm_create_motion_recall_and_body_retirement(self):
        run, owner, enemy, _ = self.arena()
        fight = run.match.combat
        for _ in range(30):
            run.step({})
        actor = next(a for a in fight.sigma_barriers.values() if a.owner is owner)
        for viewer in run.players:
            creates = [u for u in viewer.client.entities
                       if u.entity == actor.entity and u.op == world.OP_CREATE]
            self.assertEqual(len(creates), 1)
            self.assertGreater(creates[0].build(0).count, 0)
        projected = sigma_presentation.projection(fight, actor.entity)
        self.assertIs(projected.component, actor.component)
        self.assertGreater(observers.full_frame(projected, projected.latest_live).count, 0)
        link = SimpleNamespace(player=enemy, _bot_scripts={}, _combat_notices={},
                               _project_bot_snapshot=lambda snapshot, **kwargs: snapshot)
        Client._track_body_create(link, actor.entity)
        self.assertIs(link._bot_scripts[actor.entity].source.component, actor.component)
        before = actor.position
        for _ in range(15):
            run.step({owner: (0, 0, 2)})
        self.assertTrue(actor.active)
        self.assertNotEqual(actor.position, before)
        run.step({})
        self.assertEqual(actor.phase, 3)
        for _ in range(18):
            run.step({})
        run.step({owner: (0, 0, 2)})
        run.step({})
        self.assertFalse(actor.active)
        for viewer in run.players:
            self.assertFalse(any(u.entity == actor.entity and u.op == world.OP_DESTROY
                                 for u in viewer.client.entities))
        self.assertTrue(run.match.switch_hero(owner, heroes()[SOLDIER]))
        run.step({})
        for viewer in run.players:
            self.assertTrue(any(u.entity == actor.entity and u.op == world.OP_DESTROY
                                for u in viewer.client.entities))

    def test_nonpredicted_rock_reaches_owner_and_observer_then_recovers(self):
        run, owner, enemy, health = self.arena(distance=6)
        from test_game_combat import aim
        fight = run.match.combat
        facing = aim(fight._eye(owner), fight._eye(enemy))
        for _ in range(30):
            run.step({})
        run.step({owner: (*facing, 16)})
        shot = None
        for _ in range(45):
            run.step({owner: (*facing, 0)})
            if fight.rockets:
                shot = fight.rockets[0].shot
                break
        self.assertIsNotNone(shot)
        self.assertTrue(shot.authoritative)
        self.assertEqual(shot.definition, 0x04000000000018B7)
        self.assertEqual({d.viewer for d in shot.observers}, {owner, enemy})
        for _ in range(100):
            run.step({owner: (*facing, 0)})
        self.assertEqual(health.total, 130)
        self.assertFalse(fight.rockets)
        self.assertTrue(all(d.launch_queued and d.launch_arrival.received for d in shot.observers))
        for delivery in shot.observers:
            self.assertTrue(any(u.entity == delivery.record.ids[0] and u.op == world.OP_DESTROY
                                and u.ch2 is not None for u in delivery.client.entities))
        # Recovery is authored; a fresh primary press emits the original spheres.
        run.step({owner: (*facing, 1)})
        for _ in range(10):
            run.step({owner: (*facing, 1)})
            if fight.rockets:
                break
        self.assertTrue(fight.rockets)
        self.assertTrue(all(r.definition in (0x0400000000002D35, 0x04000000000032B4)
                            for r in fight.rockets))
    def test_rock_authored_timeout_retires_without_registering_damage(self):
        import math
        from ow174.game.collision import World

        run, owner, enemy, health = self.arena(distance=1000)
        fight = run.match.combat
        run.step({owner: (0, 0, 16)})
        for _ in range(45):
            run.step({})
            if fight.rockets:
                break
        rocket, = fight.rockets
        shot = rocket.shot
        self.assertIsNotNone(shot)
        # Remove physical contacts so this exercises the actual authored lifetime,
        # rather than the normal ground impact of the gravity-driven rock.
        run.match.collision_world = World([])
        for _ in range(3):
            run.step({})
        run.frame += math.ceil(rocket.left / 0.016) + 3
        run.match.update(run.frame * 0.016, run.frame, frames=False)
        self.assertFalse(fight.rockets)
        self.assertEqual(health.total, health.maximum)
        self.assertTrue(all(d.finished for d in shot.observers))
