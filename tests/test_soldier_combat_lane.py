"""Soldier lane: actual pool changes through authored input and shared combat.

NPC fixtures use the training-bot body, not the five unintegrated PvE actors.
These are server witnesses and do not establish original-client acceptance.
"""

import unittest

from test_game_combat import Run, aim

from ow174.game import bots, combat, stats
from ow174.game.collision import World, flat_world
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

    def input(self, run, soldier, buttons=0, yaw=0, pitch=0):
        """Run authored input, consume its actual reports and advance shared effects."""
        frame = soldier.body_script.frame + 1
        fight = run.match.combat
        soldier.body_script.command(frame, buttons)
        fight.command(soldier, Command(frame, yaw=yaw, pitch=pitch, buttons=buttons))
        fight.update(frame * combat.TICK, frame, frames=False)

    def test_melee_hit_miss_range_team_and_static_wall(self):
        for z, ally, wall, expected in (
            (-1.5, False, False, 170), (-1.5, True, False, 200),
            (2.0, False, False, 200), (-1.5, False, True, 200),
        ):
            with self.subTest(z=z, ally=ally, wall=wall):
                run, soldier, other = self.arena()
                other.mover.position = (0.0, 0.0, z)
                if ally:
                    other.team = soldier.team
                if wall:
                    run.match.collision_world = World([
                        (-10, -10, -2, 10, -10, -2, 10, 10, -2),
                        (-10, -10, -2, 10, 10, -2, -10, 10, -2),
                    ])
                pool = self.vitals(run, other)
                self.input(run, soldier, 0x800)
                for _ in range(40):
                    self.input(run, soldier)
                self.assertEqual(pool.total, expected)

    def test_melee_cooldown_rejects_repress_until_recovered(self):
        run, soldier, other = self.arena()
        other.mover.position = (0.0, 0.0, -1.5)
        pool = self.vitals(run, other)
        self.input(run, soldier, 0x800)
        self.input(run, soldier)
        self.input(run, soldier, 0x800)
        for _ in range(40):
            self.input(run, soldier)
        self.assertEqual(pool.total, 170)
        for _ in range(30):
            self.input(run, soldier)
        self.input(run, soldier, 0x800)
        for _ in range(40):
            self.input(run, soldier)
        self.assertEqual(pool.total, 140)

    def test_helix_cooldown_repress_cannot_damage_until_six_seconds(self):
        run, soldier, other = self.arena()
        other.mover.position = (0.0, 0.0, 1.0)
        pool = self.vitals(run, other)
        self.input(run, soldier, 2)
        for _ in range(20):
            self.input(run, soldier)
        self.assertEqual(pool.total, 80)
        pool.heal(120)
        self.input(run, soldier, 2)
        for _ in range(20):
            self.input(run, soldier)
        self.assertEqual(pool.total, 200)
        for _ in range(360):
            self.input(run, soldier)
        self.input(run, soldier, 2)
        for _ in range(20):
            self.input(run, soldier)
        self.assertEqual(pool.total, 80)

    def test_authored_field_heals_over_time_expires_and_rejects_cooldown_repress(self):
        run, soldier, other = self.arena()
        run.match.collision_world = flat_world(0.0)
        other.team = soldier.team
        other.mover.position = soldier.mover.position
        own, ally = self.vitals(run, soldier), self.vitals(run, other)
        own.damage(199)
        ally.damage(199)
        self.input(run, soldier, 0x10)
        for _ in range(120):
            self.input(run, soldier)
        self.assertGreater(own.total, 1)
        self.assertGreater(ally.total, 1)
        self.assertEqual(own.total, ally.total)
        self.input(run, soldier, 0x10)  # still cooling down; no second field
        for _ in range(330):
            self.input(run, soldier)
        self.assertFalse(run.match.combat.projectiles.shots)
        self.assertLess(own.total, 200)  # one field cannot restore 199 HP
        self.assertAlmostEqual(own.total, 175.72, places=5)  # 312 healing ticks at 35 HP/s
        stopped = own.total
        for _ in range(40):
            self.input(run, soldier)
        self.assertEqual(own.total, stopped)
        for _ in range(510):
            self.input(run, soldier)
        self.input(run, soldier, 0x10)
        for _ in range(120):
            self.input(run, soldier)
        self.assertGreater(own.total, stopped)

    def start_visor(self, run, soldier):
        script = soldier.body_script
        script.ult_seconds = 0  # isolate active-use tests from the existing timed surrogate
        script.set_ult_charge(script.component.vars[338].value())
        self.input(run, soldier, 0x20)
        self.assertEqual(script.component.vars[342].value(), 0)
        for _ in range(80):
            self.input(run, soldier)

    def test_visor_acquires_off_axis_enemy_player_and_npc_only_while_active(self):
        for npc in (False, True):
            with self.subTest(npc=npc):
                run, soldier, other = self.arena()
                other.mover.position = (3.0, 0.0, 3.0)
                victim = other
                if npc:
                    other.spawned = False
                    victim = self.add_bot(run, other.team, other.mover.position)
                pool = self.vitals(run, victim)
                self.input(run, soldier, 1)  # unassisted ray misses
                self.input(run, soldier)
                self.assertEqual(pool.total, 200)
                self.start_visor(run, soldier)
                self.input(run, soldier, 1)
                self.assertEqual(pool.total, 180)
                for _ in range(400):
                    self.input(run, soldier)
                self.input(run, soldier, 1)
                self.assertEqual(pool.total, 180)  # expired: the same ray misses again

    def test_visor_rejects_allied_dead_occluded_out_of_range_and_outside_cone(self):
        for case in ("ally", "dead", "wall", "range", "cone"):
            with self.subTest(case=case):
                run, soldier, other = self.arena()
                other.mover.position = (3.0, 0.0, 3.0)
                if case == "ally":
                    other.team = soldier.team
                elif case == "range":
                    other.mover.position = (3.0, 0.0, 50.0)
                elif case == "cone":
                    other.mover.position = (10.0, 0.0, -1.0)
                elif case == "wall":
                    run.match.collision_world = World([
                        (-10, -10, 0, 10, -10, 0, 10, 10, 0),
                        (-10, -10, 0, 10, 10, 0, -10, 10, 0),
                    ])
                pool = self.vitals(run, other)
                if case == "dead":
                    pool.died_at = run.match.combat.now
                self.start_visor(run, soldier)
                self.input(run, soldier, 1)
                self.assertEqual(pool.total, 200)

    def test_visor_cannot_acquire_without_full_charge_or_before_activation_delay(self):
        run, soldier, other = self.arena()
        other.mover.position = (3.0, 0.0, 3.0)
        pool = self.vitals(run, other)
        script = soldier.body_script
        script.ult_seconds = 0
        script.set_ult_charge(0)
        self.input(run, soldier, 0x20)
        self.input(run, soldier, 1)
        self.assertEqual(pool.total, 200)
        self.input(run, soldier)
        script.set_ult_charge(script.component.vars[338].value())
        self.input(run, soldier, 0x20)
        self.input(run, soldier, 1)
        self.assertEqual(pool.total, 200)
        for _ in range(80):
            self.input(run, soldier)
        self.input(run, soldier, 1)
        self.assertEqual(pool.total, 180)

    def test_death_during_visor_prevents_rifle_helix_melee_and_field_inputs(self):
        run, soldier, other = self.arena()
        other.mover.position = (0.0, 0.0, -1.5)
        self.start_visor(run, soldier)
        fight = run.match.combat
        victim = next(target for target in fight._targets() if target.owner is soldier)
        fight._damage(other, victim, 200, False, None, fight.tick)
        self.assertTrue(fight.is_dead(soldier))
        pool = self.vitals(run, other)
        self.input(run, soldier, 1 | 2 | 0x800 | 0x10)
        for _ in range(80):
            self.input(run, soldier)
        self.assertEqual(pool.total, 200)
        self.assertFalse(fight.rockets)
        self.assertFalse(fight.projectiles.shots)

    def test_rifle_body_critical_far_falloff_miss_ally_and_wall_change_real_pools(self):
        for case, expected in (("body", 180), ("head", 160), ("far", 194),
                               ("miss", 200), ("ally", 200), ("wall", 200)):
            with self.subTest(case=case):
                run, soldier, other = self.arena()
                other.mover.position = (0.0, 0.0, 57.0 if case == "far" else 1.0)
                if case == "ally":
                    other.team = soldier.team
                if case == "wall":
                    run.match.collision_world = World([
                        (-10, -10, 0, 10, -10, 0, 10, 10, 0),
                        (-10, -10, 0, 10, 10, 0, -10, 10, 0),
                    ])
                height = 1.55 if case == "head" else 1.0
                yaw, pitch = aim(run.match.combat._eye(soldier),
                                 (0.0, height, other.mover.position[2]))
                if case == "miss":
                    yaw = 16384
                pool = self.vitals(run, other)
                self.input(run, soldier, 1, yaw, pitch)
                self.assertAlmostEqual(pool.total, expected, places=5)

    def test_rifle_empty_magazine_and_reload_prevent_extra_damage(self):
        run, soldier, other = self.arena()
        other.mover.position = (3.0, 0.0, 3.0)  # deliberate miss while draining ammo
        pool = self.vitals(run, other)
        rifle = soldier.body_script.component.instances[11]
        for _ in range(204):
            self.input(run, soldier, 1)
        self.assertEqual(rifle.vars[53].value(), 0)
        yaw, pitch = aim(run.match.combat._eye(soldier), (3.0, 1.0, 3.0))
        self.input(run, soldier)
        self.input(run, soldier, 1, yaw, pitch)
        self.assertEqual(pool.total, 200)  # no ammunition, despite an aimed press
        self.input(run, soldier, 4)  # reload
        for _ in range(150):
            self.input(run, soldier)
        self.assertEqual(rifle.vars[53].value(), 30)
        self.input(run, soldier, 1, yaw, pitch)
        self.assertEqual(pool.total, 180)
        self.assertEqual(rifle.vars[53].value(), 29)

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
