import os
import unittest
from dataclasses import replace

from test_game_combat import in_world
from test_game_mover import box
from test_widowmaker_barriers import _ProjectedBarrierMatch
from test_widowmaker_combat import _WidowmakerMatch

from ow174.game import dva_destruct, rein_barrier
from ow174.game.collision import World
from ow174.game.content import heroes
from ow174.game.world import HEALTH_POOL


class WidowmakerSelfDestructTests(_WidowmakerMatch, unittest.TestCase):
    def test_authored_explosion_target_boundary(self):
        for label, position, cover, ally in (
            ("core", (0, 0, 2), False, False),
            ("outer", (0, 0, 10), False, False),
            ("out of range", (0, 0, 23), False, False),
            ("wall", (0, 0, 3), True, False),
            ("ally", (0, 0, 2), False, True),
        ):
            with self.subTest(label=label):
                self.setUp()
                match = self.run.match
                self.assertTrue(
                    match.switch_hero(self.b, replace(heroes()[0x02E000000000007A], body=0x0400000000000407))
                )
                self.a.mover.position[:] = position
                self.b.mover.position[:] = (0, 0, 0)
                self.a.mover.body = self.b.mover.body = None
                if ally:
                    self.b.team = self.a.team
                if cover:
                    match.collision_world = World(box(-30, 30, -1, 0, -30, 30) + box(-8, 8, 0, 6, 1, 1.4))
                for _ in range(100):
                    self.run.step({})
                fight = match.combat
                vitals = fight.vitals_of(self.target(self.a))
                if label == "outer":
                    vitals.now[HEALTH_POOL] = vitals.most[HEALTH_POOL] = 1000
                    fight._sync_body_health(self.a, vitals)
                script = self.b.body_script
                script.set_ult_charge(script.component.entity_var(338).value())
                self.run.step({self.b: (0, 0, 32)})
                self.assertEqual(script.component.entity_var(342).value(), 0)
                for _ in range(186):
                    self.run.step({})
                self.assertEqual(vitals.total, 1000 if label == "outer" else 175)
                self.run.step({})
                if label == "outer":
                    expected = 50 + 850 * (20 - 9.5) / 16
                    self.assertAlmostEqual(vitals.total, 1000 - expected, places=3)
                    self.assertGreater(self.a.mover.velocity[1], 0)
                elif label == "core":
                    self.assertTrue(vitals.dead)
                    self.assertEqual(vitals.killer, self.b.body)
                else:
                    self.assertEqual(vitals.total, 175)
                before = vitals.total
                for _ in range(45):
                    self.run.step({})
                self.assertEqual(vitals.total, before)


    @unittest.skipUnless(os.environ.get("ORPHEUS_REINHARDT_BARRIER_GEOMETRY"), "requires shield mesh")
    def test_raised_reinhardt_barrier_shields_widow_from_authored_blast(self):
        match = self.run.match
        mech = replace(heroes()[dva_destruct.HERO], body=dva_destruct.MECH)
        self.assertTrue(match.switch_hero(self.b, mech))
        rein = match.add_player(3, "Barrier", rein_barrier.REINHARDT, self.a.team, False)
        in_world(match, rein)
        self.assertTrue(match.switch_hero(rein, heroes()[rein_barrier.REINHARDT]))
        self.run.players.append(rein)
        self.a.mover.position[:] = (0, 0, 6)
        self.b.mover.position[:] = (0, 0, 0)
        rein.mover.position[:] = (0, 0, 4)
        for player in self.run.players:
            player.mover.body = None
        for _ in range(100):
            self.run.step({rein: (-32768, 0, 2)})
        shield = rein_barrier.active(rein)
        self.assertIsNotNone(shield)
        self.assertEqual(shield.vars[586].value(), 1600)
        script = self.b.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        self.run.step({self.b: (0, 0, 32), rein: (-32768, 0, 2)})
        for _ in range(186):
            self.run.step({rein: (-32768, 0, 2)})
        self.assertEqual(shield.vars[586].value(), 1600)
        self.run.step({rein: (-32768, 0, 2)})
        self.assertEqual(shield.vars[586].value(), 600)
        self.assertEqual(self.health(self.a), 175)
        self.assertFalse(self.a.mover.external_impulse)

class WidowmakerSelfDestructBarrierTests(_ProjectedBarrierMatch, unittest.TestCase):
    def test_actual_projected_barrier_absorbs_authored_blast(self):
        self.assertTrue(
            self.run.match.switch_hero(self.b, replace(heroes()[0x02E000000000007A], body=0x0400000000000407))
        )
        self.a.mover.position[:] = (0, 0, 2)
        self.b.mover.position[:] = (0, 0, 0)
        self.c.mover.position[:] = (-22, 0, 2)
        for player in self.run.players:
            player.mover.body = None
        self.idle(100)
        script = self.b.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        self.step(enemy=32)
        self.assertEqual(script.component.entity_var(342).value(), 0)
        self.idle(171)
        self.activate()
        self.idle(14)
        fight = self.run.match.combat
        self.assertTrue(self.barrier().state(10).active)
        self.idle(1)
        barrier = fight.zarya_barriers.projected[self.c.body]
        self.assertEqual(self.health(self.a), 175)
        self.assertEqual(barrier.remaining, 0)
        self.assertFalse(self.barrier().state(10).active)
        self.assertEqual(self.health(self.c), 400)
