"""Soldier lane: actual pool changes through authored input and shared combat.

NPC fixtures use the training-bot body, not the five unintegrated PvE actors.
These are server witnesses and do not establish original-client acceptance.
"""

import unittest

from test_game_combat import Run, aim

from ow174.game import bots, combat, stats
from ow174.game.collision import World
from ow174.game.commands import Command
from ow174.game.content import SOLDIER


class SoldierCombatLaneTests(unittest.TestCase):
    def arena(self):
        run = Run(names=("Soldier", "Other"))
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        soldier, other = run.players
        soldier.mover.position = (0.0, 0.0, -3.0)
        other.mover.position = (1.0, 0.0, 1.0)
        other.team = 1 - soldier.team
        return run, soldier, other

    def add_bot(self, run, team, position):
        bot = bots.Bot(bots.BOT_ENTITY + len(run.match.bots), {
            "hero": f"0x{SOLDIER:X}", "team": team, "position": position,
            "rotation": (0, 0, 0, 1), "graphs": [],
        }, {"name": "Generic NPC witness", "body": "0x0400000000001403", "health": 200}, {})
        bot.retain_corpse = True
        run.match.bots.append(bot)
        return bot

    def vitals(self, run, owner):
        fight = run.match.combat
        return fight.vitals_of(next(target for target in fight._targets() if target.owner is owner))

    def launch_helix(self, run, soldier):
        frame = soldier.body_script.frame + 1
        soldier.body_script.command(frame, 2)
        run.match.combat.command(soldier, Command(frame, buttons=2))
        rocket, = run.match.combat.rockets
        self.assertEqual((rocket.direct.amount, rocket.blast.amount), (40, 80))
        return rocket

    def fly(self, run, rocket):
        fight = run.match.combat
        for _ in range(200):
            if rocket not in fight.rockets:
                return
            fight.tick += 1
            fight._fly(rocket)
        self.fail("projectile did not terminate")

    def test_wall_impact_keeps_front_splash_and_blocks_rear_players_and_npcs(self):
        for npc in (False, True):
            for side, damaged in ((-1.0, True), (1.0, False)):
                with self.subTest(npc=npc, side=side):
                    run, soldier, other = self.arena()
                    other.mover.position = (1.0, 0.0, side)
                    victim = other
                    if npc:
                        other.spawned = False
                        victim = self.add_bot(run, other.team, (1.0, 0.0, side))
                    # Actual static triangles; no mocked ray or damage resolver.
                    run.match.collision_world = World([
                        (-10, -10, 0, 10, -10, 0, 10, 10, 0),
                        (-10, -10, 0, 10, 10, 0, -10, 10, 0),
                    ])
                    vitals = self.vitals(run, victim)
                    rocket = self.launch_helix(run, soldier)
                    self.fly(run, rocket)
                    self.assertEqual(vitals.total < 200, damaged)
                    if damaged:
                        self.assertGreaterEqual(vitals.total, 120)  # splash only
                    else:
                        self.assertEqual(vitals.total, 200)
                        self.assertEqual(soldier.client.messages(combat.HIT), [])

    def test_unobstructed_helix_direct_and_splash_remain_120(self):
        run, soldier, other = self.arena()
        other.mover.position = (0.0, 0.0, 1.0)
        vitals = self.vitals(run, other)
        rocket = self.launch_helix(run, soldier)
        self.fly(run, rocket)
        self.assertEqual(vitals.total, 80)

    def test_direct_impact_does_not_splash_a_second_target_through_wall(self):
        run, soldier, other = self.arena()
        other.mover.position = (0.0, 0.0, -1.0)
        behind = self.add_bot(run, other.team, (1.0, 0.0, 1.0))
        run.match.collision_world = World([
            (-10, -10, 0, 10, -10, 0, 10, 10, 0),
            (-10, -10, 0, 10, 10, 0, -10, 10, 0),
        ])
        front_health = self.vitals(run, other)
        rear_health = self.vitals(run, behind)
        self.fly(run, self.launch_helix(run, soldier))
        self.assertEqual(front_health.total, 80)
        self.assertEqual(rear_health.total, 200)

    def test_field_heals_self_and_allied_player_and_npc_but_not_enemies_or_dead(self):
        run, soldier, other = self.arena()
        other.team = soldier.team
        other.mover.position = (0.0, 0.0, -3.0)
        ally = self.add_bot(run, soldier.team, other.mover.position)
        enemy = self.add_bot(run, 1 - soldier.team, other.mover.position)
        dead = self.add_bot(run, soldier.team, other.mover.position)
        owners = (soldier, other, ally, enemy, dead)
        pools = [self.vitals(run, owner) for owner in owners]
        for pool in pools:
            pool.damage(100)
        pools[-1].damage(100)
        pools[-1].died_at = run.match.combat.now
        run.match.combat.heal_area(soldier, soldier.mover.position, 3.0, 35.0)
        self.assertEqual([pool.total for pool in pools], [135, 135, 135, 100, 0])
        self.assertEqual(soldier.stats.value(stats.HEALING), 105)

    def test_enemy_player_and_npc_rifle_damage_soldier_from_authored_volley(self):
        for npc in (False, True):
            with self.subTest(npc=npc):
                run, soldier, other = self.arena()
                other.mover.position = (0.0, 0.0, -7.0)
                fight = run.match.combat
                vitals = self.vitals(run, soldier)
                yaw, pitch = aim(fight._eye(other), (0.0, 1.0, -3.0))
                frame = other.body_script.frame + 1
                other.body_script.command(frame, 1)
                reports = list(other.body_script.component.shots)
                self.assertTrue(reports)
                source = other
                if npc:
                    # Exercise the NPC source branch with the same authored rifle;
                    # this does not claim the five enemies' own weapons or AI.
                    source = self.add_bot(run, other.team, other.mover.position)
                    source.yaw = yaw
                state, number, when, activation = reports[0]
                command = None if npc else Command(frame, yaw=yaw, pitch=pitch)
                fight._shoot(source, command, state, number, when,
                             activation=activation)
                self.assertLess(vitals.total, 200)
                self.assertEqual(vitals.total, 160 if npc else 180)


if __name__ == "__main__":
    unittest.main()
