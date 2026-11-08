"""Authored0407 commands with prepared external inputs; creation/transforms remain open."""

import unittest
from dataclasses import replace

from test_game_combat import KANEZAKA_DM, Run

from ow174.game.collision import World
from ow174.game.combat import Vitals
from ow174.game.content import find_hero
from ow174.game.mover import defined_mover_data
from ow174.game.script import graph

MECH = 0x0400000000000407


class AuthoredMechTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if graph.entity_body(MECH) is None or defined_mover_data(MECH) is None:
            raise unittest.SkipTest("requires external0407 graph and mover supplements; mech acceptance OPEN")

    def scenario(self, armour=False):
        run = Run(KANEZAKA_DM, ("Mech", "Target"))
        match = run.match
        match.bots.clear()
        match._pending_bot_scripts.clear()
        mech, victim = run.players
        self.assertTrue(match.switch_hero(mech, replace(find_hero("dva"), body=MECH)))
        mech.mover.position = [0.0, 1.0, 0.0]
        victim.mover.position = [0.0, 1.0, 2.0]
        self.step(run, mech, 100)
        target = next(body for body in match.combat._targets() if body.owner is victim)
        vitals = Vitals(target.entity, 5000, armour=5000 if armour else 0)
        match.combat.vitals[target.entity] = vitals
        return run, mech, victim, vitals

    def step(self, run, mech, frames, buttons=0, yaw=0):
        for _ in range(frames):
            run.step({mech: (yaw, 0, buttons)})

    def test_primary_uses_eleven_rays_and_actual_sustained_graph_cadence(self):
        run, mech, _, vitals = self.scenario()
        self.step(run, mech, 62, 1)
        self.assertEqual(vitals.total, 5000 - 154)
        self.assertEqual(len(mech.client.messages(25001)), 77)
        body = next(target for target in run.match.combat._targets() if target.owner is mech)
        self.assertEqual(run.match.combat.vitals_of(body).total, 600)
        self.assertEqual(mech.mover.data.stand_radius, 0.7)

    def test_primary_armour_is_applied_to_each_authored_pellet(self):
        run, mech, _, vitals = self.scenario(armour=True)
        self.step(run, mech, 1, 1)
        self.assertEqual(vitals.total, 10000 - 11)

    def test_primary_miss_wall_and_allies_are_excluded(self):
        for case in ("miss", "wall", "ally"):
            run, mech, victim, vitals = self.scenario()
            if case == "wall":
                run.match.collision_world = World([
                    (-10, -10, 1, 10, -10, 1, 10, 10, 1),
                    (-10, -10, 1, 10, 10, 1, -10, 10, 1),
                ])
            elif case == "ally":
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                victim.team = mech.team
            self.step(run, mech, 1, 1, 16384 if case == "miss" else 0)
            self.assertEqual(vitals.total, 5000)

    def test_missiles_execute_eighteen_direct_and_splash_hits(self):
        run, mech, _, vitals = self.scenario()
        self.step(run, mech, 160, 16)
        self.assertEqual(vitals.total, 5000 - 126)
        self.assertEqual(len(mech.client.messages(25001)), 36)

    def test_missiles_cooldown_rejects_repress_then_finishes_and_allows_next_burst(self):
        run, mech, _, vitals = self.scenario()
        self.step(run, mech, 160, 16)
        script = next(i for i in mech.body_script.component.instances.values() if i.graph.index == 0x1128)
        self.assertGreater(script.vars[61].value(), 7.0)
        self.step(run, mech, 1)
        self.step(run, mech, 30, 16)
        self.assertEqual(vitals.total, 5000 - 126)
        self.assertGreater(script.vars[61].value(), 0)
        while script.vars[61].value() > 0:
            self.step(run, mech, 1)
        self.step(run, mech, 160, 16)
        self.assertEqual(vitals.total, 5000 - 252)

    def test_missiles_can_fire_at_the_same_time_as_primary(self):
        damage = {}
        for buttons in (1, 16, 17):
            run, mech, _, vitals = self.scenario()
            self.step(run, mech, 160, buttons)
            damage[buttons] = 5000 - vitals.total
        self.assertEqual(damage[17], damage[1] + damage[16])


if __name__ == "__main__":
    unittest.main()
