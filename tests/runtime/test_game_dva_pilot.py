"""D.Va pilot combat from actual Match command admission and authored pistol graphs.

This accepts only the existing pilot body. Initial mech creation/possession is
not implemented here, and no original-client presentation is claimed.
"""

import unittest
from dataclasses import replace

from test_game_combat import KANEZAKA_DM, Run, aim

from ow174.game import bots
from ow174.game.collision import World
from ow174.game.combat import Vitals
from ow174.game.content import find_hero


class PilotTests(unittest.TestCase):
    def scenario(self, npc=False, distance=3.0):
        run = Run(KANEZAKA_DM, ("Pilot", "Victim"))
        match = run.match
        match.bots.clear()
        match._pending_bot_scripts.clear()
        pilot, victim = run.players
        match.switch_hero(pilot, find_hero("dva"))
        pilot.mover.position = [0.0, 1.0, 0.0]
        victim.mover.position = [0.0, 1.0, distance]
        target_actor = victim
        if npc:
            victim.spawned = False
            target_actor = bots.Bot(bots.BOT_ENTITY, {
                "hero": f"0x{victim.hero.guid:X}", "team": 1,
                "position": (0.0, 1.0, distance), "rotation": (0, 0, 0, 1), "graphs": [],
            }, {"name": "Pilot target", "body": "0x0400000000001403", "health": 200}, {})
            match.bots.append(target_actor)
        for _ in range(100):
            run.step({})
        fight = match.combat
        target = next(body for body in fight._targets() if body.owner is target_actor)
        return run, pilot, target, fight.vitals_of(target)

    def step(self, run, pilot, frames, buttons=0, yaw=0):
        for _ in range(frames):
            run.step({pilot: (yaw, 0, buttons)})

    def test_primary_press_launches_authored_projectile_and_deals_fourteen_damage(self):
        for npc in (False, True):
            run, pilot, _, vitals = self.scenario(npc)
            self.step(run, pilot, 1, 1)
            self.assertEqual(vitals.total, 200)
            self.assertEqual(len(run.match.combat.rockets), 1)
            self.step(run, pilot, 8)
            self.assertEqual(vitals.total, 186)
            weapon = next(i for i in pilot.body_script.component.instances.values() if i.graph.index == 0x2B8)
            self.assertEqual(weapon.vars[53].value(), 19)

    def test_miss_cover_and_allied_target_take_no_damage(self):
        for case in ("miss", "cover", "ally"):
            run, pilot, target, vitals = self.scenario()
            if case == "cover":
                run.match.collision_world = World([
                    (-10, -10, 1, 10, -10, 1, 10, 10, 1),
                    (-10, -10, 1, 10, 10, 1, -10, 10, 1),
                ])
            elif case == "ally":
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                target.owner.team = pilot.team
            self.step(run, pilot, 1, 1, 16384 if case == "miss" else 0)
            self.step(run, pilot, 10)
            self.assertEqual(vitals.total, 200)
            self.assertEqual(pilot.client.messages(25001), [])

    def test_twenty_round_magazine_rejects_fire_during_authored_reload(self):
        run, pilot, target, _ = self.scenario()
        fight = run.match.combat
        vitals = fight.vitals[target.entity] = Vitals(target.entity, 5000)
        self.step(run, pilot, 200, 1)
        weapon = next(i for i in pilot.body_script.component.instances.values() if i.graph.index == 0x2B8)
        self.assertEqual(weapon.vars[53].value(), 0)
        self.assertEqual(vitals.total, 5000 - 20 * 14)
        self.step(run, pilot, 30, 1)
        self.assertEqual(weapon.vars[53].value(), 0)
        self.assertEqual(vitals.total, 5000 - 20 * 14)
        self.step(run, pilot, 100)
        self.assertEqual(weapon.vars[53].value(), 20)
        self.step(run, pilot, 1, 1)
        self.step(run, pilot, 8)
        self.assertEqual(vitals.total, 5000 - 21 * 14)

    def test_authored_projectile_range_expires_before_a_distant_target(self):
        run, pilot, _, vitals = self.scenario(distance=102.0)
        self.step(run, pilot, 1, 1)
        self.step(run, pilot, 130)
        self.assertEqual(vitals.total, 200)
        self.assertFalse(run.match.combat.rockets)

    def test_enemy_player_command_fire_damages_the_pilot(self):
        run, pilot, _, _ = self.scenario()
        enemy = run.players[1]
        fight = run.match.combat
        target = next(body for body in fight._targets() if body.owner is pilot)
        vitals = fight.vitals_of(target)
        self.assertEqual(vitals.total, 150)
        yaw, pitch = aim(fight._eye(enemy), (0.0, 1.8, 0.0))
        run.step({enemy: (yaw, pitch, 1)})
        self.assertEqual(vitals.total, 130)

    def test_death_prevents_new_shots_and_owner_replacement_removes_query_target(self):
        run, pilot, _, _ = self.scenario()
        script = pilot.body_script
        script.set_health(0, 150)
        self.step(run, pilot, 30, 1)
        self.assertFalse(run.match.combat.rockets)
        self.assertEqual(pilot.client.messages(25001), [])
        original = pilot.body
        run.match.switch_hero(pilot, find_hero("soldier"))
        self.assertIsNone(script.world._lookup_body_owner(original))


if __name__ == "__main__":
    unittest.main()
