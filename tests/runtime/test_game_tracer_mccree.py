"""Peacekeeper primary and Fan through actual McCree and Tracer inputs."""

import unittest
from dataclasses import replace
from unittest.mock import patch

from test_game_combat import KANEZAKA_DM, Run, aim, in_world
from test_game_mover import box

from ow174.game import combat, mccree_fan, stats, tracer
from ow174.game.collision import StaticCollisionProvider, World
from ow174.game.commands import FIRE, Command
from ow174.game.content import SOLDIER, heroes
from ow174.game.script.driver import ULT_CHARGE, ULT_COST


class McCreeArena(unittest.TestCase):
    def setUp(self):
        self.run = Run(KANEZAKA_DM, ("McCree", "Tracer"))
        self.match = self.run.match
        self.match.bots.clear()
        self.match._pending_bot_scripts.clear()
        self.fight = self.match.combat
        self.mccree, self.tracer = self.run.players
        self.assertTrue(self.match.switch_hero(self.mccree, heroes()[mccree_fan.HERO]))
        self.assertTrue(self.match.switch_hero(self.tracer, heroes()[0x02E0000000000003]))
        self.place_tracer(1.5)
        for _ in range(25):
            self.run.step({})
        self.health = self.vitals(self.tracer)
        self.source_health = self.vitals(self.mccree)
        self.weapon = next(i for i in self.mccree.body_script.component.instances.values()
                           if i.graph.index == mccree_fan.WEAPON)

    def place_tracer(self, distance, offset=0):
        x, y, z = self.mccree.mover.position
        self.tracer.mover.position[:] = (x + offset, y, z + distance)
        self.tracer.mover.body = None

    def vitals(self, actor):
        return self.fight.vitals_of(next(t for t in self.fight._targets() if t.owner is actor))

    def aim_at(self, source, target):
        x, y, z = target.mover.position
        return aim(self.fight._eye(source), (x, y + 1, z))

    def hits(self):
        return [hit["+0x78"] for hit in self.mccree.client.messages(combat.HIT)]

    def fan(self, *, angles=None, replay=False):
        facing = self.aim_at(self.mccree, self.tracer) if angles is None else angles
        for _ in range(95):
            self.run.step({self.mccree: (*facing, 2)})
            if replay:
                self.mccree.take_commands([Command(
                    self.run.frame, yaw=facing[0], pitch=facing[1], buttons=2,
                )])
            if self.weapon.vars[53].value() == 0:
                return
        self.fail("Actual Fan input did not exhaust the six rounds")


class TracerMcCreeTests(McCreeArena):
    def test_primary_uses_one_round_and_tracer_returns_fire(self):
        self.run.step({self.mccree: (*self.aim_at(self.mccree, self.tracer), FIRE)})
        self.assertEqual(self.health.total, 80)
        self.assertEqual(self.weapon.vars[53].value(), 5)
        self.assertEqual([hit["+0x4"] for hit in self.hits()], [70])
        self.run.step({self.tracer: (*self.aim_at(self.tracer, self.mccree), FIRE)})
        self.assertEqual(self.source_health.total, 213)

    def test_fan_consumes_six_rounds_and_replay_cannot_duplicate_shots(self):
        self.health.most[combat.HEALTH_POOL] = self.health.now[combat.HEALTH_POOL] = 1000
        with patch.object(mccree_fan, "ray", wraps=mccree_fan.ray) as observed:
            self.fan(replay=True)
        self.assertEqual(observed.call_count, 6)
        activations = {call.args[4] for call in observed.call_args_list}
        self.assertEqual(len(activations), 6)
        self.assertEqual([hit["+0x4"] for hit in self.hits()], [50] * 6)
        self.assertEqual(self.health.total, 700)
        self.assertFalse(any(hit["+0x8"] & combat.CRITICAL for hit in self.hits()))
        self.assertEqual(len(self.weapon.state(15).shot_spread), 6)

    def test_lethal_fan_does_not_add_damage_or_extra_kills_after_death(self):
        self.fan()
        self.assertTrue(self.health.dead)
        self.assertEqual(self.health.killer, self.mccree.body)
        self.assertEqual([hit["+0x4"] for hit in self.hits()], [50, 50, 50])
        self.assertEqual(len(self.mccree.client.messages(combat.KILL_NOTICE)), 1)

    def test_fan_does_not_damage_ally_or_tracer_behind_wall(self):
        for gate in ("ally", "wall"):
            with self.subTest(gate=gate):
                self.setUp()
                if gate == "ally":
                    self.match.game_map = replace(self.match.game_map, free_for_all=False)
                    self.tracer.team = self.mccree.team
                else:
                    x, y, z = self.mccree.mover.position
                    world = World(box(x - 20, x + 20, y - 2, y, z - 20, z + 20)
                                  + box(x - 4, x + 4, y, y + 4, z + 0.7, z + 0.8))
                    self.match.collision_provider = StaticCollisionProvider(world)
                    self.match.collision_world = world
                    for actor in self.run.players:
                        actor.mover.use(world=world)
                self.fan()
                self.assertEqual(self.health.total, 150)
                self.assertFalse(self.hits())

    def test_recall_blocks_fan_and_primary_hits_after_recovery(self):
        facing = self.aim_at(self.mccree, self.tracer)
        self.run.step({self.tracer: (0, 0, 16)})
        self.assertTrue(tracer.recalling(self.tracer))
        self.fan(angles=facing)
        self.assertTrue(tracer.recalling(self.tracer))
        self.assertEqual(self.health.total, 150)
        self.assertFalse(self.hits())
        for _ in range(160):
            self.run.step({})
            if (not tracer.recalling(self.tracer) and self.weapon.vars[53].value() == 6
                    and not self.weapon.state(38).active):
                break
        self.assertFalse(tracer.recalling(self.tracer))
        self.assertEqual(self.weapon.vars[53].value(), 6)
        self.run.step({self.mccree: (*facing, FIRE)})
        self.assertEqual(self.health.total, 80)

    def test_manual_reload_refills_and_held_primary_can_resume(self):
        facing = self.aim_at(self.mccree, self.tracer)
        self.run.step({self.mccree: (*facing, FIRE)})
        self.assertEqual(self.weapon.vars[53].value(), 5)
        self.run.step({self.mccree: (*facing, 0x400)})
        for _ in range(115):
            self.run.step({self.mccree: (*facing, FIRE)})
            self.assertEqual(self.health.total, 80)
            if self.weapon.vars[53].value() == 6:
                break
        self.assertEqual(self.weapon.vars[53].value(), 6)
        for _ in range(100):
            self.run.step({self.mccree: (*facing, FIRE)})
            if self.health.total < 80:
                break
        self.assertEqual(self.health.total, 10)
        self.assertEqual(self.weapon.vars[53].value(), 5)

    def test_off_axis_fan_can_hit_where_centre_aim_misses(self):
        totals = []
        for scatter in (False, True):
            self.setUp()
            self.place_tracer(20, offset=-1.9)
            for _ in range(3):
                self.run.step({})
            x, y, z = self.mccree.mover.position
            facing = aim(self.fight._eye(self.mccree), (x, y + 1, z + 20))
            with patch.object(mccree_fan, "ray", wraps=mccree_fan.ray,
                              side_effect=None if scatter else lambda state, forward, *_: forward):
                self.fan(angles=facing)
            totals.append(self.health.total)
        self.assertEqual(totals[0], 150)
        self.assertLess(totals[1], 150)


    def test_blink_leaves_remaining_fan_rays_after_first_hit(self):
        facing = self.aim_at(self.mccree, self.tracer)
        self.run.step({self.mccree: (*facing, 2)})
        self.assertEqual(self.health.total, 100)
        self.run.step({self.tracer: (16384, 0, 8), self.mccree: (*facing, 2)})
        for _ in range(65):
            if self.weapon.vars[53].value() == 0:
                break
            self.run.step({self.mccree: (*facing, 2)})
        self.assertEqual(self.weapon.vars[53].value(), 0)
        self.assertEqual(self.health.total, 100)

    def test_far_fan_does_not_deliver_six_close_damage_hits(self):
        self.place_tracer(80)
        for _ in range(3):
            self.run.step({})
        self.fan()
        self.assertGreater(self.health.total, 60)
        self.assertFalse(self.health.dead)


    def test_actual_flashbang_then_fan_stops_tracer_before_recall(self):
        facing = self.aim_at(self.mccree, self.tracer)
        self.run.step({self.mccree: (*facing, 16)})
        for _ in range(40):
            if self.fight.is_stunned(self.tracer):
                break
            self.run.step({})
        self.assertTrue(self.fight.is_stunned(self.tracer))
        self.assertEqual(self.health.total, 125)
        self.run.step({self.tracer: (0, 0, 16), self.mccree: (*facing, 2)})
        self.assertFalse(tracer.recalling(self.tracer))
        self.fan(angles=facing)
        self.assertTrue(self.health.dead)
        self.assertEqual(self.health.killer, self.mccree.body)
        self.assertEqual(len(self.mccree.client.messages(combat.KILL_NOTICE)), 1)

    def test_tracer_attacks_damage_flashbang_stunned_player(self):
        for attack, button, amount in (("primary", FIRE, 12), ("melee", 0x800, 30), ("pulse", 32, 5)):
            for lethal in (False, True):
                with self.subTest(attack=attack, lethal=lethal):
                    self.setUp()
                    self.check_stunned_soldier_attack(attack, button, amount, lethal)

    def check_stunned_soldier_attack(self, attack, button, amount, lethal):
        self.match.game_map = replace(self.match.game_map, free_for_all=False)
        self.mccree.team = self.tracer.team
        soldier = self.match.add_player(3, "Soldier", SOLDIER, 1 - self.tracer.team, False)
        in_world(self.match, soldier)
        self.assertTrue(self.match.switch_hero(soldier, heroes()[SOLDIER]))
        self.run.players.append(soldier)
        x, y, z = self.tracer.mover.position
        soldier.mover.position[:] = (x, y, z + 1.5)
        soldier.mover.body = None
        self.mccree.mover.position[:] = (x + 2.5, y, z)
        self.mccree.mover.body = None
        script = self.tracer.body_script
        script.ult_seconds = 0
        charge = script.component.entity_var(ULT_CHARGE)
        target = self.vitals(soldier)
        if lethal:
            target.damage(200 - (25 + amount - 1))
            self.fight._sync_body_health(soldier, target)
        before = target.total
        self.run.step({self.mccree: (*self.aim_at(self.mccree, soldier), 16)})
        for _ in range(40):
            if self.fight.is_stunned(soldier):
                break
            self.run.step({})
        self.assertTrue(self.fight.is_stunned(soldier))
        deadline = self.fight.stuns[soldier.body]
        self.assertFalse(self.fight.is_stunned(self.tracer))
        self.assertEqual(target.total, before - 25)
        self.assertEqual(self.health.total, 150)

        return_fire = {soldier: (*self.aim_at(soldier, self.tracer), FIRE)}
        self.run.step(return_fire)
        self.assertFalse(soldier.client.messages(combat.HIT))
        self.assertEqual(self.health.total, 150)
        self.assertEqual(charge.value(), 0)
        if attack == "pulse":
            script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        self.run.step({self.tracer: (*self.aim_at(self.tracer, soldier), button)})
        bombs = self.fight.pulse_bombs.bombs
        if attack == "pulse":
            for _ in range(45):
                if bombs and bombs[0].attached == soldier.body:
                    break
                self.assertTrue(self.fight.is_stunned(soldier))
                self.run.step({})
            bomb, = bombs
            self.assertEqual(bomb.attached, soldier.body)
        damage = amount - int(lethal)
        self.assertEqual(target.total, before - 25 - damage)
        self.assertEqual(self.tracer.stats.value(stats.HERO_DAMAGE), damage)
        self.assertEqual(charge.value(), 0 if attack == "pulse" else damage)
        self.assertEqual(target.dead, lethal)
        if lethal:
            self.assertEqual(target.killer, self.tracer.body)
            self.assertNotIn(soldier.body, self.fight.stuns)
        else:
            self.assertTrue(self.fight.is_stunned(soldier))
            self.assertEqual(self.fight.stuns[soldier.body], deadline)
            for _ in range(55):
                self.run.step({})
                if not self.fight.is_stunned(soldier):
                    break
            self.assertFalse(self.fight.is_stunned(soldier))
            self.assertFalse(target.dead)
            self.run.step(return_fire)
            self.assertEqual(self.health.total, 130)

        if attack == "pulse":
            self.run.step({self.tracer: (16384, 0, 8)})
            for _ in range(75):
                if not bombs:
                    break
                self.run.step({})
            self.assertFalse(bombs)
            self.assertTrue(target.dead)
            self.assertEqual(target.killer, self.tracer.body)
            self.assertEqual(self.tracer.stats.value(stats.HERO_DAMAGE), before - 25)
            self.assertEqual(charge.value(), 0)
        self.assertEqual(len(soldier.client.messages(combat.DEATH_NOTICE)), int(target.dead))
        self.assertEqual(self.tracer.stats.value(stats.ELIMINATIONS), int(target.dead))
