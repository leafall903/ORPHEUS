"""Executed Fusion Cannon ray consumers; these do not accept mech possession."""

import math
import unittest
from dataclasses import replace
from types import SimpleNamespace

from test_game_combat import KANEZAKA_DM, Run
from test_game_graph_analysis import make_graph, node

from ow174.game import bots, dva
from ow174.game.collision import World
from ow174.game.combat import Vitals
from ow174.game.commands import Command
from ow174.game.content import find_hero
from ow174.game.script import runtime


def number(value):
    return {"$": "STUConfigVarFloat" if type(value) is float else "STUConfigVarInt", "m_value": value}


def volley(spread=4, count=11):
    pattern = {"$": "STU_613BDDB0", "m_spread": number(spread), "m_0D30E40C": [
        {"m_6425FFB9": number(radius), "m_B19BD35D": number(size)}
        for radius, size in ((0, 1), (0.5, 4), (1, 6))
    ]}
    definition = make_graph(dva.FUSION_CANNONS, [node(
        0, "STUStatescriptStateWeaponVolley", state=0, fields={
            "m_numProjectilesPerShot": number(count), "m_5EC1EB38": pattern,
            "m_FEC435C6": number(100),
            "m_6396149F": {"m_modifyHealth": {"m_amount": number(2)}},
        },
    )], states=[0])
    return runtime.Component(0xA0000100).create(definition).state(0)


class DvaPelletTests(unittest.TestCase):
    def scenario(self, armour=False, distance=1.5):
        run = Run(KANEZAKA_DM, ("Shooter", "Target"))
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        shooter, victim = run.players
        # Synthetic volley consumer: keep a consistent hero/body pair for health
        # serialization; this fixture does not execute the Fusion Cannon VM.
        shooter.hero = find_hero("dva")
        shooter.mover.position = [0.0, 1.0, 0.0]
        victim.mover.position = [0.0, 1.0, distance]
        run.step({})
        fight = run.match.combat
        target = next(target for target in fight._targets() if target.owner is victim)
        vitals = Vitals(target.entity, 100, armour=100) if armour else Vitals(target.entity, 200)
        fight.vitals[target.entity] = vitals
        return run, shooter, vitals

    def fire(self, run, shooter, state=None, yaw=0):
        run.match.combat._shoot(shooter, Command(run.frame, yaw=yaw), state or volley(),
                               0, run.frame * 16, activation=(1, run.frame * 16))

    def test_close_range_applies_eleven_independent_damage_events(self):
        run, shooter, vitals = self.scenario()
        self.fire(run, shooter)
        self.assertEqual(vitals.total, 178)
        self.assertEqual(len(shooter.client.messages(25001)), 11)

    def test_armour_mitigates_each_pellet_instead_of_an_aggregated_hit(self):
        run, shooter, vitals = self.scenario(armour=True)
        self.fire(run, shooter)
        self.assertEqual(vitals.total, 189)

    def test_aimed_miss_and_out_of_range_do_no_damage(self):
        for distance, yaw in ((1.5, 16384), (102, 0)):
            run, shooter, vitals = self.scenario(distance=distance)
            self.fire(run, shooter, yaw=yaw)
            self.assertEqual(vitals.total, 200)

    def test_wider_pattern_can_miss_individual_pellets(self):
        run, shooter, vitals = self.scenario(distance=10)
        self.fire(run, shooter, volley(spread=15.0))
        self.assertEqual(vitals.total, 196)  # centre and downward inner-ring pellet

    def test_enemy_npc_takes_all_close_range_pellets(self):
        run, shooter, _ = self.scenario()
        victim = run.players[1]
        victim.spawned = False
        bot = bots.Bot(bots.BOT_ENTITY, {
            "hero": f"0x{victim.hero.guid:X}", "team": 1,
            "position": tuple(victim.mover.position), "rotation": (0, 0, 0, 1), "graphs": [],
        }, {"name": "Pellet target", "body": "0x0400000000001403", "health": 200}, {})
        run.match.bots.append(bot)
        run.step({})
        self.fire(run, shooter)
        target = next(target for target in run.match.combat._targets() if target.owner is bot)
        self.assertEqual(run.match.combat.vitals_of(target).total, 178)

    def test_wall_blocks_every_pellet(self):
        run, shooter, vitals = self.scenario()
        run.match.collision_world = World([
            (-10, -10, 0.5, 10, -10, 0.5, 10, 10, 0.5),
            (-10, -10, 0.5, 10, 10, 0.5, -10, 10, 0.5),
        ])
        self.fire(run, shooter)
        self.assertEqual(vitals.total, 200)

    def test_allied_target_is_excluded(self):
        run, shooter, vitals = self.scenario()
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        run.players[1].team = shooter.team
        self.fire(run, shooter)
        self.assertEqual(vitals.total, 200)

    def test_ring_geometry_handles_vertical_aim_and_rejects_invalid_counts(self):
        actor = SimpleNamespace(hero=SimpleNamespace(guid=dva.HERO))
        for direction in ((0, 0, 1), (0, 1, 0), (0, -1, 0)):
            rays = dva.pellet_directions(actor, volley(), direction)
            self.assertEqual(len(rays), 11)
            self.assertEqual(rays[0], direction)
            for ray in rays:
                self.assertAlmostEqual(math.sqrt(sum(v * v for v in ray)), 1)
        with self.assertRaises(ValueError):
            dva.pellet_directions(actor, volley(count=10), (0, 0, 1))
        with self.assertRaises(ValueError):
            dva.pellet_directions(actor, volley(spread=float("nan")), (0, 0, 1))


if __name__ == "__main__":
    unittest.main()
