"""Actual Zarya input against Reinhardt graph 0095 and the target-build mesh."""

import os
import unittest

import test_game_zarya_npcs as npc_fixtures
from test_game_combat import Run, aim, in_world

from ow174.game import (
    combat,
    heavy_charge,
    interception,
    npc,
    npc_control,
    phase,
    reinhardt_barrier,
    reinhardt_charge,
    zarya,
)
from ow174.game.collision import World, flat_world
from ow174.game.commands import Command
from ow174.game.content import SOLDIER, heroes
from ow174.game.npc_console import Console
from ow174.game.script import expr
from ow174.game.server_snapshot import SCHEMA, SCOPE, _Graph, snapshot_digest


@unittest.skipUnless(os.environ.get("ORPHEUS_REINHARDT_COLLISION"), "requires the target-build shield mesh")
class ZaryaReinhardtTests(unittest.TestCase):
    def scene(self, *, distance=6, allied=False, raised=True):
        self.run = Run(names=("Zarya", "Reinhardt", "Target"))
        self.zarya, self.rein, self.target = self.run.players
        self.fight = self.run.match.combat
        self.run.match.bots.clear()
        self.run.match._pending_bot_scripts.clear()
        self.run.match.collision_world = flat_world(0)
        self.run.match.switch_hero(self.zarya, heroes()[0x02E0000000000068])
        self.run.match.switch_hero(self.rein, heroes()[reinhardt_barrier.HERO])
        self.rein.team = self.zarya.team if allied else 1 - self.zarya.team
        self.target.team = 1 - self.zarya.team
        for player, position in ((self.zarya, (0, 0, 0)), (self.rein, (0, 0, distance)),
                                 (self.target, (0, 0, distance + 1.1))):
            player.mover.position[:] = position
            player.mover.body = None
            player.body_script.ult_seconds = 0
        for _ in range(20):
            self.step(held=0)
        for _ in range(5):
            self.step(held=2 if raised else 0)
        self.shield = self.fight.reinhardt_barriers.states[self.rein.body]
        self.assertEqual(self.shield.active, raised)
        self.assertEqual(self.shield.current, 1600)
        self.assertEqual(len(self.fight.reinhardt_barriers.geometry.triangles), 192)

    def step(self, buttons=0, *, held=2, facing=-32768, angles=(0, 0)):
        self.run.step({self.zarya: (*angles, buttons), self.rein: (facing, 0, held)})

    def health(self, owner):
        target = next(t for t in self.fight._targets() if t.owner is owner)
        return self.fight.vitals_of(target).total

    def charge(self):
        return self.zarya.body_script.component.entity_var(342).value()

    def secondary(self, **kwargs):
        self.step(2, **kwargs)
        rocket, = self.fight.rockets
        for _ in range(100):
            self.step(**kwargs)
            if rocket not in self.fight.rockets:
                return
        self.fail("secondary never landed")

    def test_beam_and_secondary_damage_mesh_without_body_hit_or_charge(self):
        self.scene()
        for _ in range(51):
            self.step(1)
        self.assertAlmostEqual(self.shield.current, 1540, places=2)
        self.scene()
        self.secondary()
        self.assertEqual(self.shield.current, 1553)
        self.assertEqual(self.health(self.rein), 500)
        self.assertEqual(self.health(self.target), 200)
        self.assertEqual(self.charge(), 0)
        self.assertEqual(self.zarya.stats.value(combat.stats.HERO_DAMAGE), 0)
        self.assertFalse(self.zarya.client.messages(combat.HIT))
        instance = self.fight.reinhardt_barriers.instance(self.rein)
        self.assertEqual(instance.find_var(0, 586).value(), self.shield.current)

    def test_sparse_grenade_preserves_raised_barrier_before_and_after_admitted_input(self):
        for raise_shield in (False, True):
            for delay in (2, 25):
                for sparse in (False, True):
                    with self.subTest(raise_shield=raise_shield, delay=delay, sparse=sparse):
                        self.scene(raised=not raise_shield)
                        self.step(2, held=0 if raise_shield else 2)
                        match = self.run.match
                        launch = self.run.frame
                        rocket, = self.fight.rockets
                        admission = launch + delay
                        endpoint = launch + 40
                        for tick in range(launch + 1, endpoint + 1):
                            if not sparse or tick == endpoint:
                                match.update(tick * combat.TICK, tick, frames=False)
                            if admission <= tick < admission + 5:
                                match.tick = tick
                                self.rein.take_commands([Command(tick, yaw=-32768,
                                                                 buttons=2 if raise_shield else 0)])
                        covered = raise_shield == (delay == 2)
                        self.assertNotIn(rocket, self.fight.rockets)
                        self.assertEqual(self.shield.active, raise_shield)
                        self.assertEqual(self.shield.current, 1553 if covered else 1600)
                        self.assertEqual(self.health(self.rein), 500 if covered else 458)

    def test_admitted_beam_and_shield_input_keep_same_tick_command_order(self):
        for raise_shield in (False, True):
            for shield_first in (False, True):
                with self.subTest(raise_shield=raise_shield, shield_first=shield_first):
                    self.scene(raised=not raise_shield)
                    if raise_shield:
                        for _ in range(3):
                            self.step()
                    self.step(1)
                    self.assertEqual(self.shield.active, not raise_shield)
                    match = self.run.match
                    tick = self.run.frame + 1
                    match.tick = tick
                    beam = (self.zarya, Command(tick, buttons=1))
                    shield = (self.rein, Command(tick, yaw=-32768, buttons=2 if raise_shield else 0))
                    for owner, command in (shield, beam) if shield_first else (beam, shield):
                        owner.take_commands([command])
                    match.update(tick * combat.TICK, tick, frames=False)
                    self.assertEqual(self.shield.active, raise_shield)
                    covered = raise_shield == shield_first
                    self.assertAlmostEqual(self.shield.current, 1598.8 if covered else 1600, places=3)
                    self.assertAlmostEqual(self.health(self.rein), 500 if covered else 499.16)

    def test_admitted_quick_melee_and_shield_keep_same_tick_command_order(self):
        for raise_shield in (False, True):
            for shield_first in (False, True):
                with self.subTest(raise_shield=raise_shield, shield_first=shield_first):
                    self.scene(distance=2.4, raised=not raise_shield)
                    if raise_shield:
                        for _ in range(4):
                            self.step()
                    self.assertEqual(self.shield.active, not raise_shield)
                    match = self.run.match
                    tick = self.run.frame + 1
                    match.tick = tick
                    melee = (self.zarya, Command(tick, buttons=0x800))
                    shield = (self.rein, Command(tick, yaw=-32768, buttons=2 if raise_shield else 0))
                    for owner, command in (shield, melee) if shield_first else (melee, shield):
                        owner.take_commands([command])
                    match.update(tick * combat.TICK, tick, frames=False)
                    self.assertEqual(self.shield.active, raise_shield)
                    covered = raise_shield == shield_first
                    self.assertEqual(self.shield.current, 1570 if covered else 1600)
                    self.assertEqual(self.health(self.rein), 475)

    def test_sparse_quick_melee_cannot_be_blocked_by_later_deflect(self):
        for late in (False, True):
            with self.subTest(late=late):
                self.scene(distance=2.4, raised=False)
                match = self.run.match
                self.assertTrue(match.switch_hero(self.rein, heroes()[0x02E0000000000029]))
                self.rein.mover.position[:] = (0, 0, 2.4)
                self.rein.mover.body = None
                for _ in range(4):
                    self.step(held=0)
                start = self.run.frame
                deflect = (self.rein, Command(start + (5 if late else 1), yaw=-32768, buttons=16))
                melee = (self.zarya, Command(start + 2, buttons=0x800))
                for owner, command in (melee, deflect) if late else (deflect, melee):
                    match.tick = command.frame
                    owner.take_commands([command])
                instance = next(i for i in self.rein.body_script.component.instances.values()
                                if i.graph.index == 0x03A3)
                self.assertTrue(instance.states[10].active)
                end = start + 12
                match.update(end * combat.TICK, end, frames=False)
                self.assertEqual(self.health(self.rein), 170 if late else 200)

    def test_sparse_late_lower_preserves_actual_grenade_break_and_cooldown(self):
        self.scene(distance=3.8)
        self.shield.current = 20
        self.step(2)
        match = self.run.match
        launch = self.run.frame
        rocket, = self.fight.rockets
        match.tick = launch + 25
        self.rein.take_commands([Command(match.tick, yaw=-32768)])
        end = launch + 40
        match.update(end * combat.TICK, end, frames=False)
        instance = self.fight.reinhardt_barriers.instance(self.rein)
        cooldown = instance.find_var(0, 472).value()
        self.assertEqual(self.shield.current, 0)
        self.assertEqual(self.health(self.rein), 500)
        self.assertEqual(self.health(self.target), 200)
        self.assertAlmostEqual(self.shield.broken_until, rocket.last_tick * combat.TICK + cooldown)
        self.assertGreater(self.shield.broken_until, self.fight.now)
        self.run.frame = end
        for _ in range(30):
            self.step()
            self.assertFalse(self.shield.active)

    def test_melee_damages_mesh_and_body(self):
        self.scene(distance=2.4)
        self.step(0x800)
        for _ in range(30):
            self.step()
        self.assertEqual(self.shield.current, 1570)
        self.assertEqual(self.health(self.rein), 475)
        self.assertEqual(self.charge(), 25)

    def test_allied_shield_and_earlier_wall(self):
        self.scene(allied=True)
        for _ in range(51):
            self.step(1)
        self.assertEqual(self.shield.current, 1600)
        self.assertAlmostEqual(self.health(self.target), 140, places=4)
        self.scene()
        self.run.match.collision_world = World([
            (-20, 0, -20, -20, 0, 20, 20, 0, 20), (-20, 0, -20, 20, 0, 20, 20, 0, -20),
            (-10, -10, 1, 10, -10, 1, 10, 10, 1), (-10, -10, 1, 10, 10, 1, -10, 10, 1),
        ])
        for _ in range(51):
            self.step(1)
        for _ in range(40):
            self.step()
        self.secondary()
        self.assertEqual(self.shield.current, 1600)
        self.assertEqual(self.health(self.rein), 500)

    @unittest.skipUnless(npc_fixtures.NPC_DATA, "requires all five actual NPC definitions")
    def test_active_mesh_covers_each_actual_npc_from_beam_and_secondary(self):
        fixtures = npc_fixtures.ZaryaFiveNpcTests
        fixtures.setUpClass()
        self.addCleanup(fixtures.tearDownClass)
        for definition in fixtures.catalog.values():
            with self.subTest(npc=definition.name):
                self.scene(distance=3.8)
                self.target.mover.position[0] = 12
                bot = npc.spawn(self.run.match, definition, (0, 0, 4.9), team=self.rein.team)
                self.assertIsNotNone(bot.body_script)
                before = self.fight.vitals[bot.entity].total
                for _ in range(51):
                    self.step(1)
                self.assertAlmostEqual(self.shield.current, 1540, places=2)
                for _ in range(40):
                    self.step()
                self.secondary()
                self.assertAlmostEqual(self.shield.current, 1493, places=2)
                self.assertEqual(self.fight.vitals[bot.entity].total, before)
                self.assertIsNone(bot.impulse_mover)
                self.assertEqual(self.charge(), 0)

    def test_ground_splash_damages_mesh_and_leaves_rein_covered(self):
        self.scene()
        angles = aim(self.fight._eye(self.zarya), (0, 0, 3.7))
        self.secondary(angles=angles)
        self.assertGreater(self.shield.current, 1553)
        self.assertLess(self.shield.current, 1600)
        self.assertEqual(self.health(self.rein), 500)
        self.assertEqual(self.charge(), 0)

    def test_release_and_body_replacement_remove_cover(self):
        self.scene()
        self.secondary()
        for _ in range(40):
            self.step()
        for _ in range(51):
            self.step(1, held=0)
        self.assertFalse(self.shield.active)
        self.assertEqual(self.shield.current, 1553)
        self.assertAlmostEqual(self.health(self.rein), 458, places=4)
        old = self.rein.body
        self.assertTrue(self.run.match.switch_hero(self.rein, self.rein.hero))
        self.assertNotIn(old, self.fight.reinhardt_barriers.states)
        self.rein.mover.position[:] = [0, 0, 6]
        self.rein.mover.body = None
        for _ in range(20):
            self.step(held=0)
        self.assertEqual(self.fight.reinhardt_barriers.states[self.rein.body].current, 1600)
        self.assertFalse(self.fight.reinhardt_barriers.states[self.rein.body].active)

    def test_breaking_secondary_keeps_cover_for_that_explosion(self):
        self.scene(distance=3.8)
        # Seed a nearly broken pool; the attack, break, and cooldown use live input.
        self.shield.current = 20
        self.secondary()
        self.assertEqual(self.shield.current, 0)
        self.assertFalse(self.shield.active)
        self.assertEqual(self.health(self.rein), 500)
        self.assertEqual(self.health(self.target), 200)
        self.assertEqual(self.charge(), 0)
        deadline = self.shield.broken_until
        self.assertGreater(deadline, self.fight.now)
        for _ in range(60):
            self.step()
            self.assertFalse(self.shield.active)
        self.assertLess(self.fight.now, deadline)
        for _ in range(51):
            self.step(1)
        self.assertAlmostEqual(self.health(self.rein), 458, places=4)

    def test_graviton_impact_spends_charge_and_deploys_on_incoming_side(self):
        self.scene()
        self.zarya.body_script.set_ult_charge(2100)
        self.step(0x20)
        for _ in range(100):
            if self.fight.zarya_gravitons.fields:
                break
            self.step()
        field, = self.fight.zarya_gravitons.fields
        self.assertLess(field.position[2], self.rein.mover.position[2] - 0.7)
        self.assertEqual(self.shield.current, 1595)
        self.assertEqual(self.health(self.rein), 500)
        self.assertEqual(self.charge(), 0)
        before = self.rein.mover.position[2]
        for _ in range(20):
            self.step()
        self.assertTrue(self.shield.active)
        self.assertEqual(self.shield.current, 1595)
        self.assertAlmostEqual(self.health(self.rein), 498.4, places=4)
        self.assertLess(self.rein.mover.position[2], before)

class IncomingReinhardtFixture(unittest.TestCase):
    def scene(self, *, ally=False, projected=False):
        self.run = Run(names=("Reinhardt", "Zarya", "Behind"))
        self.rein, self.zarya, self.behind = self.run.players
        self.fight = self.run.match.combat
        self.run.match.bots.clear()
        self.run.match._pending_bot_scripts.clear()
        self.run.match.collision_world = flat_world(0)
        self.run.match.switch_hero(self.rein, heroes()[reinhardt_barrier.HERO])
        self.run.match.switch_hero(self.zarya, heroes()[0x02E0000000000068])
        self.zarya.team = self.rein.team if ally else 1 - self.rein.team
        self.behind.team = 1 - self.rein.team
        if projected:
            self.run.match.switch_hero(self.behind, heroes()[0x02E0000000000068])
        for actor, pos in ((self.rein, (0, 0, 0)), (self.zarya, (0, 0, 4)),
                           (self.behind, (0, 0, 7))):
            actor.mover.position[:] = pos
            actor.mover.body = None
            actor.body_script.ult_seconds = 0
        self.advance(20)

    def advance(self, count, poses=None):
        for _ in range(count):
            self.run.step(poses or {})

    def health(self, actor):
        return self.fight.vitals_of(next(t for t in self.fight._targets() if t.owner is actor)).total


class IncomingFireStrikeTests(IncomingReinhardtFixture):
    def strike(self, commands=None, poses=None):
        self.run.step({self.rein: (0, 0, 16), **(commands or {})})
        self.assertFalse(self.fight.rockets)
        for _ in range(50):
            self.run.step(poses or {})
            if self.fight.rockets:
                rocket, = self.fight.rockets
                self.assertEqual(rocket.definition, 0x04000000000001EA)
                self.assertEqual(rocket.direct.amount, 100)
                self.assertEqual(rocket.speed, 25)
                self.assertAlmostEqual(rocket.penetration_radius, 0.85)
                self.assertAlmostEqual(rocket.left, 4)
                return rocket
        self.fail("actual Fire Strike input never released")

    def test_unprotected_and_allied_body_order(self):
        for ally in (False, True):
            with self.subTest(ally=ally):
                self.scene(ally=ally)
                rocket = self.strike()
                self.advance(50)
                self.assertEqual(self.health(self.zarya), 400 if ally else 300)
                self.assertEqual(self.health(self.behind), 100)
                self.assertEqual(len(rocket.hit_targets), 1 if ally else 2)
                self.assertEqual(len(self.rein.client.messages(combat.HIT)), 1 if ally else 2)
                self.run.step({self.rein: (0, 0, 16)})
                self.advance(40)
                self.assertEqual(self.fight.rockets, [rocket])

    def test_sparse_fire_strike_keeps_two_contacts_on_opposite_sides_of_barrier_start(self):
        for sparse in (False, True):
            with self.subTest(sparse=sparse):
                self.scene(projected=True)
                match = self.run.match
                self.behind.mover.position[:] = (0, 0, 12)
                self.behind.mover.body = None
                rocket = self.strike()
                launch = self.run.frame
                activation = launch + 14
                end = launch + 50
                if not sparse:
                    for tick in range(launch + 1, activation):
                        match.update(tick * combat.TICK, tick, frames=False)
                match.tick = activation
                for player in (self.zarya, self.behind):
                    player.take_commands([Command(activation, buttons=8)])
                for tick in (end,) if sparse else range(activation, end + 1):
                    match.update(tick * combat.TICK, tick, frames=False)
                self.assertEqual(self.health(self.zarya), 300)
                self.assertEqual(self.health(self.behind), 400)
                self.assertEqual(self.fight.zarya_barriers.active[self.behind.body].remaining, 100)
                self.assertFalse(self.zarya.body_script.component.entity_var(87).value())
                self.assertAlmostEqual(self.behind.body_script.component.entity_var(87).value(), 20)
                self.assertCountEqual([key[0] for key in rocket.hit_targets],
                                      [self.zarya.body, self.behind.body])

    def test_pending_beam_is_cancelled_only_if_fire_strike_kills_before_admission(self):
        for late in (False, True):
            with self.subTest(late=late):
                self.scene()
                match = self.run.match
                target = next(target for target in self.fight._targets() if target.owner is self.zarya)
                self.fight.vitals_of(target).damage(310)
                self.strike()
                launch = self.run.frame
                self.run.step({self.zarya: (-32768, 0, 1)})
                match.tick = launch + (18 if late else 3)
                self.zarya.take_commands([Command(self.run.frame + 1, yaw=-32768, buttons=1)])
                self.assertEqual(self.health(self.rein), 500)
                end = launch + 35
                match.update(end * combat.TICK, end, frames=False)
                self.assertTrue(self.fight.is_dead(self.zarya))
                self.assertAlmostEqual(self.health(self.rein), 500 if late else 499.16)

    def test_pending_quick_melee_respects_an_earlier_lethal_fire_strike(self):
        for late in (False, True):
            with self.subTest(late=late):
                self.scene()
                match = self.run.match
                self.zarya.mover.position[:] = (0, 0, 2.4)
                self.zarya.mover.body = None
                target = next(target for target in self.fight._targets() if target.owner is self.zarya)
                self.fight.vitals_of(target).damage(310)
                self.strike()
                launch = self.run.frame
                match.tick = launch + (18 if late else 1)
                self.zarya.take_commands([Command(launch + 1, yaw=-32768, buttons=0x800)])
                end = launch + 35
                match.update(end * combat.TICK, end, frames=False)
                self.assertTrue(self.fight.is_dead(self.zarya))
                self.assertEqual(self.health(self.rein), 500 if late else 475)

    @unittest.skipUnless(os.environ.get("ORPHEUS_ZARYA_NPC_DATA"), "requires the five target-build NPCs")
    def test_sparse_fire_strike_cannot_hit_each_npc_before_its_body_exists(self):
        fixtures = npc_fixtures.ZaryaFiveNpcTests
        fixtures.setUpClass()
        self.addCleanup(fixtures.tearDownClass)
        for definition in fixtures.catalog.values():
            with self.subTest(npc=definition.name):
                self.scene()
                match = self.run.match
                rocket = self.strike()
                launch = self.run.frame
                match.tick = launch + 3
                early = npc.spawn(match, definition, (0, 0, 4), team=self.zarya.team)
                match.tick = launch + 18
                late = npc.spawn(match, definition, (0, 0, 4), team=self.zarya.team)
                early_vitals = self.fight.vitals[early.entity]
                late_vitals = self.fight.vitals[late.entity]
                before = late_vitals.total
                end = launch + 35
                match.update(end * combat.TICK, end, frames=False)
                self.assertEqual(early_vitals.total, before - 100)
                self.assertEqual(late_vitals.total, before)
                self.assertNotIn(late.entity, {key[0] for key in rocket.hit_targets})

    def test_personal_barrier_absorbs_and_fire_strike_continues(self):
        self.scene()
        self.strike({self.zarya: (0, 0, 8)})
        self.advance(45)
        self.assertEqual(self.health(self.zarya), 400)
        self.assertEqual(self.health(self.behind), 100)
        self.assertEqual(self.fight.zarya_barriers.active[self.zarya.body].remaining, 100)
        self.assertAlmostEqual(self.zarya.body_script.component.entity_var(87).value(), 20)

    def test_projection_absorbs_first_hit_and_fire_strike_continues(self):
        self.scene(projected=True)
        self.strike({self.behind: (-32768, 0, 16)})
        self.advance(45)
        self.assertEqual(self.health(self.zarya), 400)
        self.assertEqual(self.health(self.behind), 300)
        self.assertEqual(self.fight.zarya_barriers.projected[self.behind.body].remaining, 100)
        self.assertAlmostEqual(self.behind.body_script.component.entity_var(87).value(), 20)


    def test_fire_strike_hits_bubble_edge_once_and_continues_to_rear_body(self):
        for projected in (False, True):
            for case in ("edge", "break", "outside", "ally"):
                with self.subTest(projected=projected, case=case):
                    self.scene(ally=case == "ally", projected=projected)
                    caster = self.behind if projected else self.zarya
                    caster.team = self.zarya.team
                    x = 2.6 if case == "outside" else 1.75
                    self.rein.mover.position[:] = [x, 0, 0]
                    self.rein.mover.body = None
                    if projected:
                        caster.mover.position[:] = [8, 0, 4]
                        caster.mover.body = None
                        match = self.run.match
                        rear = match.add_player(4, "Rear", SOLDIER, 1 - self.rein.team, False)
                        in_world(match, rear)
                        self.run.players.append(rear)
                        self.assertTrue(match.switch_hero(rear, heroes()[SOLDIER]))
                    else:
                        rear = self.behind
                    rear.mover.position[:] = [x, 0, 7]
                    rear.mover.body = None
                    rear_before = self.health(rear)
                    target_eye = self.fight._eye(self.zarya)
                    angles = aim(self.fight._eye(caster), target_eye) if projected else (0, 0)
                    self.run.step({caster: (*angles, 16 if projected else 8)})
                    barriers = self.fight.zarya_barriers
                    self.assertTrue(barriers.protects(self.zarya))
                    pool = (barriers.projected if projected else barriers.active)[caster.body]
                    if case == "break":
                        pool.remaining = 60
                    rocket = self.strike()
                    if case == "break":
                        for _ in range(20):
                            self.advance(1)
                            if pool.remaining == 0:
                                break
                        self.assertEqual(pool.remaining, 0)
                        self.zarya.mover.position[0] = x
                        self.zarya.mover.body = None
                    self.advance(35)
                    absorbed = {"edge": 100, "break": 60}.get(case, 0)
                    self.assertEqual(pool.remaining, (60 if case == "break" else 200) - absorbed)
                    energy = caster.body_script.component.entity_var(87).value() or 0
                    self.assertAlmostEqual(energy, absorbed / 5)
                    self.assertEqual(self.health(self.zarya), 400)
                    self.assertEqual(rear_before - self.health(rear), 100)
                    self.assertEqual(len(rocket.hit_targets), 2 if absorbed else 1)

    def test_wall_stops_fire_strike_before_barrier(self):
        self.scene()
        self.run.match.collision_world = World([
            (-20, 0, -20, -20, 0, 20, 20, 0, 20), (-20, 0, -20, 20, 0, 20, 20, 0, -20),
            (-10, -10, 2, 10, -10, 2, 10, 10, 2), (-10, -10, 2, 10, 10, 2, -10, 10, 2),
        ])
        rocket = self.strike({self.zarya: (0, 0, 8)})
        self.advance(20)
        self.assertNotIn(rocket, self.fight.rockets)
        self.assertEqual(rocket.hit_targets, set())
        self.assertEqual(self.health(self.zarya), 400)
        self.assertEqual(self.health(self.behind), 200)
        self.assertNotIn(self.zarya.body, self.fight.zarya_barriers.active)

    @unittest.skipUnless(interception.eligible(0x04000000000001EA), "requires actual Fire Strike tags")
    def test_reflected_fire_strike_penetrates_rein_then_hits_zarya_barrier(self):
        self.scene(ally=True)
        self.run.match.switch_hero(self.behind, heroes()[0x02E0000000000029])
        self.behind.mover.position[:] = [0, 0, 4]
        self.behind.mover.body = None
        self.zarya.mover.position[:] = [0, 0, -3]
        self.zarya.mover.body = None
        self.advance(20)
        poses = {self.behind: (-32768, 0, 0)}
        rocket = self.strike({self.behind: (-32768, 0, 16), self.zarya: (0, 0, 8)}, poses)
        self.advance(45, poses)
        self.assertIs(rocket.shooter, self.behind)
        self.assertEqual(rocket.source_entity, self.behind.body)
        self.assertEqual(self.health(self.behind), 200)
        self.assertEqual(self.health(self.rein), 405)
        self.assertEqual(self.health(self.zarya), 400)
        self.assertEqual(self.fight.zarya_barriers.active[self.zarya.body].remaining, 100)
        self.assertAlmostEqual(self.zarya.body_script.component.entity_var(87).value(), 20)

    @unittest.skipUnless(npc_fixtures.NPC_DATA, "requires all five actual NPC definitions")
    def test_personal_barrier_does_not_stop_strike_hitting_actual_npcs(self):
        fixtures = npc_fixtures.ZaryaFiveNpcTests
        fixtures.setUpClass()
        self.addCleanup(fixtures.tearDownClass)
        for definition in fixtures.catalog.values():
            with self.subTest(npc=definition.name):
                self.scene()
                self.behind.mover.position[0] = 12
                self.behind.mover.body = None
                bot = npc.spawn(self.run.match, definition, (0, 0, 7), team=self.zarya.team)
                vitals = self.fight.vitals[bot.entity]
                before = vitals.total
                rocket = self.strike({self.zarya: (0, 0, 8)})
                self.advance(45)
                self.assertEqual(self.health(self.zarya), 400)
                self.assertEqual(before - vitals.total, 100)
                self.assertEqual(len(rocket.hit_targets), 2)

    @unittest.skipUnless(npc_fixtures.NPC_DATA, "requires all five actual NPC definitions")
    def test_fire_strike_hits_projected_edge_outside_each_actual_npc(self):
        fixtures = npc_fixtures.ZaryaFiveNpcTests
        fixtures.setUpClass()
        self.addCleanup(fixtures.tearDownClass)
        for definition in fixtures.catalog.values():
            with self.subTest(npc=definition.name):
                self.scene(projected=True)
                caster = self.behind
                self.rein.mover.position[:] = [1.75, 0, 0]
                self.rein.mover.body = None
                self.zarya.mover.position[:] = [1.75, 0, 7]
                self.zarya.mover.body = None
                caster.mover.position[:] = [8, 0, 4]
                caster.mover.body = None
                bot = npc.spawn(self.run.match, definition, (0, 0, 4), team=caster.team)
                target = next(t for t in self.fight._targets() if t.owner is bot)
                point = (target.feet[0], target.feet[1] + target.eye, target.feet[2])
                angles = aim(self.fight._eye(caster), point)
                vitals = self.fight.vitals[bot.entity]
                before = vitals.total
                rocket = self.strike({caster: (*angles, 16)})
                self.assertLess(target.radius + rocket.penetration_radius, 1.75)
                self.advance(35)
                pool = self.fight.zarya_barriers.projected[caster.body]
                self.assertEqual((pool.recipient, pool.remaining), (bot.entity, 100))
                self.assertAlmostEqual(caster.body_script.component.entity_var(87).value(), 20)
                self.assertEqual(vitals.total, before)
                self.assertEqual(self.health(self.zarya), 300)
                self.assertEqual(len(rocket.hit_targets), 2)

    @unittest.skipUnless(interception.eligible(0x04000000000001EA), "requires actual Fire Strike tags")
    def test_grasp_ends_fire_strike_before_zarya(self):
        self.scene()
        self.run.match.switch_hero(self.behind, heroes()[0x02E000000000023B])
        self.behind.mover.position[:] = [0, 0, 3]
        self.behind.mover.body = None
        self.zarya.mover.position[:] = [0, 0, 6]
        self.zarya.mover.body = None
        self.advance(20)
        poses = {self.behind: (-32768, 0, 0)}
        rocket = self.strike({self.behind: (-32768, 0, 8), self.zarya: (0, 0, 8)}, poses)
        self.advance(35, poses)
        self.assertNotIn(rocket, self.fight.rockets)
        self.assertEqual(rocket.hit_targets, set())
        instance = next(i for i in self.behind.body_script.component.instances.values()
                        if i.graph.index == 0x1D02)
        self.assertEqual(instance.vars[21312].value(), 100)
        self.assertEqual(self.health(self.zarya), 400)
        self.assertNotIn(self.zarya.body, self.fight.zarya_barriers.active)

    @unittest.skipUnless(os.environ.get("ORPHEUS_REINHARDT_COLLISION"), "requires shield mesh")
    def test_sparse_fire_strike_records_the_original_raised_shield_generation(self):
        self.scene()
        match = self.run.match
        match.switch_hero(self.behind, heroes()[reinhardt_barrier.HERO])
        self.behind.mover.position[:] = [0, 0, 3]
        self.behind.mover.body = None
        self.advance(20)
        poses = {self.behind: (-32768, 0, 2)}
        self.advance(5, poses)
        barrier = self.fight.reinhardt_barriers
        shield = barrier.states[self.behind.body]
        pet = barrier.instance(self.behind).states[17]
        generation = pet.counter
        rocket = self.strike(poses, poses)
        launch = self.run.frame
        match.tick = launch + 20
        self.behind.take_commands([Command(match.tick, yaw=-32768)])
        for tick in range(launch + 110, launch + 116):
            match.tick = tick
            self.behind.take_commands([Command(tick, yaw=-32768, buttons=2)])
        self.assertTrue(pet.active)
        self.assertNotEqual(pet.counter, generation)
        end = launch + 150
        match.update(end * combat.TICK, end, frames=False)
        self.assertEqual(shield.current, 1500)
        self.assertTrue(shield.active)
        self.assertEqual([key for key in rocket.hit_targets if key[0] == -self.behind.body],
                         [(-self.behind.body, generation)])

    def test_fire_strike_contact_history_is_snapshot_causal(self):
        self.scene()
        rocket = self.strike()

        def digest():
            graph = _Graph({"shooter": self.rein}, {})
            root = graph.value(rocket)
            return snapshot_digest({"schema": SCHEMA, "scope": SCOPE, "restorable": False,
                                    "roots": {"projectile": root}, "nodes": graph.finish(), "assets": {}})

        first = digest()
        rocket.hit_targets.add((self.zarya.body, None))
        self.assertNotEqual(first, digest())
        rocket.hit_targets.clear()
        self.assertEqual(first, digest())

    @unittest.skipUnless(os.environ.get("ORPHEUS_REINHARDT_COLLISION"), "requires shield mesh")
    def test_fire_strike_penetrates_mesh_and_body_before_zarya(self):
        self.scene()
        self.run.match.switch_hero(self.behind, heroes()[reinhardt_barrier.HERO])
        self.behind.mover.position[:] = [0, 0, 3]
        self.behind.mover.body = None
        self.zarya.mover.position[:] = [0, 0, 6]
        self.zarya.mover.body = None
        self.advance(20)
        poses = {self.behind: (-32768, 0, 2)}
        self.advance(5, poses)
        shield = self.fight.reinhardt_barriers.states[self.behind.body]
        self.assertTrue(shield.active)
        rocket = self.strike({**poses, self.zarya: (0, 0, 8)}, poses)
        self.advance(45, poses)
        self.assertEqual(shield.current, 1500)
        self.assertEqual(self.health(self.behind), 405)
        self.assertEqual(self.health(self.zarya), 400)
        self.assertEqual(self.fight.zarya_barriers.active[self.zarya.body].remaining, 100)
        self.assertEqual(len(rocket.hit_targets), 3)

class IncomingHammerTests(IncomingReinhardtFixture):
    def prepare(self, *, distance=2.3, ally=False, projected=False):
        self.scene(ally=ally, projected=projected)
        self.zarya.mover.position[:] = [0, 0, distance]
        self.zarya.mover.body = None
        if projected:
            self.behind.mover.position[:] = [4, 0, distance]
            self.behind.mover.body = None
        self.advance(2)

    def swing(self, button=1, others=None):
        self.run.step({self.rein: (0, 0, button), **(others or {})})
        self.advance(60)

    def test_primary_and_melee_each_deal_one_85_hit(self):
        for button in (1, 0x800):
            with self.subTest(button=button):
                self.prepare()
                self.swing(button)
                self.assertEqual(self.health(self.zarya), 315)
                self.assertEqual(self.health(self.behind), 200)
                self.assertEqual(len(self.rein.client.messages(combat.HIT)), 1)
                self.assertEqual(self.rein.stats.value(combat.stats.HERO_DAMAGE), 85)

    def test_hammer_absorbed_by_personal_and_projected_barriers(self):
        for projected in (False, True):
            with self.subTest(projected=projected):
                self.prepare(projected=projected)
                caster = self.behind if projected else self.zarya
                command = (-16384, 0, 16) if projected else (0, 0, 8)
                self.swing(others={caster: command})
                self.assertEqual(self.health(self.zarya), 400)
                barriers = self.fight.zarya_barriers
                storage = barriers.projected if projected else barriers.active
                self.assertEqual(storage[caster.body].remaining, 115)
                self.assertAlmostEqual(caster.body_script.component.entity_var(87).value(), 17)
                self.assertFalse(self.rein.client.messages(combat.HIT))

    def test_hammer_contacts_bubble_beyond_body_reach_once_per_swing(self):
        for projected in (False, True):
            for case in ("edge", "outside", "cover", "ally"):
                with self.subTest(projected=projected, case=case):
                    distance = 5.7 if case == "outside" else 4.8
                    self.prepare(distance=distance, ally=case == "ally", projected=projected)
                    caster = self.behind if projected else self.zarya
                    caster.team = self.zarya.team
                    target_eye = self.fight._eye(self.zarya)
                    angles = aim(self.fight._eye(caster), target_eye) if projected else (0, 0)
                    self.run.step({caster: (*angles, 16 if projected else 8)})
                    barriers = self.fight.zarya_barriers
                    self.assertTrue(barriers.protects(self.zarya))
                    pool = (barriers.projected if projected else barriers.active)[caster.body]
                    if case == "cover":
                        self.run.match.collision_world = World([
                            (-20, 0, -20, -20, 0, 20, 20, 0, 20), (-20, 0, -20, 20, 0, 20, 20, 0, -20),
                            (-10, -10, 2, 10, -10, 2, 10, 10, 2), (-10, -10, 2, 10, 10, 2, -10, 10, 2),
                        ])
                    self.swing(0x800 if projected else 1)
                    absorbed = 85 if case == "edge" else 0
                    self.assertEqual(pool.remaining, 200 - absorbed)
                    energy = caster.body_script.component.entity_var(87).value() or 0
                    self.assertAlmostEqual(energy, absorbed / 5)
                    self.assertEqual(self.health(self.zarya), 400)
                    self.assertFalse(self.rein.client.messages(combat.HIT))

    def test_hammer_misses_out_of_range_and_does_not_hit_allies(self):
        for distance, ally in ((8, False), (2.3, True)):
            with self.subTest(distance=distance, ally=ally):
                self.prepare(distance=distance, ally=ally)
                self.swing()
                self.assertEqual(self.health(self.zarya), 400)
                self.assertFalse(self.rein.client.messages(combat.HIT))

    def test_wall_blocks_hammer_without_barrier_energy(self):
        self.prepare()
        self.run.match.collision_world = World([
            (-20, 0, -20, -20, 0, 20, 20, 0, 20), (-20, 0, -20, 20, 0, 20, 20, 0, -20),
            (-10, -10, 1, 10, -10, 1, 10, 10, 1), (-10, -10, 1, 10, 10, 1, -10, 10, 1),
        ])
        self.swing(others={self.zarya: (0, 0, 8)})
        self.assertEqual(self.health(self.zarya), 400)
        self.assertNotIn(self.zarya.body, self.fight.zarya_barriers.active)
        self.assertFalse(self.rein.client.messages(combat.HIT))

    def test_switching_melee_controls_during_windup_preserves_one_swing(self):
        self.prepare()
        inputs = (1, 0x801, 0x800, 0x801, 1, 0x800)
        for buttons in inputs:
            self.run.step({self.rein: (0, 0, buttons)})
        self.assertEqual(self.rein.body_script.buttons, 0x800)
        self.advance(60)
        self.assertEqual(self.health(self.zarya), 315)
        self.assertEqual(len(self.rein.client.messages(combat.HIT)), 1)

    @unittest.skipUnless(npc_fixtures.NPC_DATA, "requires actual five-NPC bodies")
    def test_hammer_hits_all_five_actual_npcs_once_while_zarya_is_protected(self):
        fixtures = npc_fixtures.ZaryaFiveNpcTests
        fixtures.setUpClass()
        self.addCleanup(fixtures.tearDownClass)
        for definition in fixtures.catalog.values():
            with self.subTest(npc=definition.name):
                self.prepare()
                bot = npc.spawn(self.run.match, definition, (0.6, 0, 2.7), team=self.zarya.team)
                vitals = self.fight.vitals[bot.entity]
                before = vitals.total
                self.swing(others={self.zarya: (0, 0, 8)})
                self.assertEqual(self.health(self.zarya), 400)
                self.assertEqual(before - vitals.total, 85)
                self.assertEqual(len(self.rein.client.messages(combat.HIT)), 1)

    @unittest.skipUnless(npc_fixtures.NPC_DATA, "requires actual five-NPC bodies")
    def test_projection_absorbs_hammer_on_each_npc_but_not_adjacent_enemy(self):
        fixtures = npc_fixtures.ZaryaFiveNpcTests
        fixtures.setUpClass()
        self.addCleanup(fixtures.tearDownClass)
        for definition in fixtures.catalog.values():
            for button in (1, 0x800):
                with self.subTest(npc=definition.name, button=button):
                    self.prepare(projected=True)
                    caster = self.behind
                    caster.mover.position[:] = [4, 0, 2.7]
                    caster.mover.body = None
                    bot = npc.spawn(self.run.match, definition, (0.6, 0, 2.7), team=caster.team)
                    vitals = self.fight.vitals[bot.entity]
                    before = vitals.total
                    self.swing(button, others={caster: (-16384, 0, 16)})
                    pool = self.fight.zarya_barriers.projected[caster.body]
                    self.assertEqual((pool.recipient, pool.remaining), (bot.entity, 115))
                    self.assertAlmostEqual(caster.body_script.component.entity_var(87).value(), 17)
                    self.assertEqual(vitals.total, before)
                    self.assertEqual(self.health(self.zarya), 315)
                    self.assertEqual(self.health(caster), 400)
                    self.assertEqual(len(self.rein.client.messages(combat.HIT)), 1)

    @unittest.skipUnless(npc_fixtures.NPC_DATA, "requires actual five-NPC bodies")
    def test_hammer_hits_projected_edge_outside_each_actual_npc_body(self):
        fixtures = npc_fixtures.ZaryaFiveNpcTests
        fixtures.setUpClass()
        self.addCleanup(fixtures.tearDownClass)
        for definition in fixtures.catalog.values():
            with self.subTest(npc=definition.name):
                self.prepare(distance=8, projected=True)
                caster = self.behind
                caster.mover.position[:] = [4, 0, 4.8]
                caster.mover.body = None
                bot = npc.spawn(self.run.match, definition, (0, 0, 4.8), team=caster.team)
                target = next(t for t in self.fight._targets() if t.owner is bot)
                self.assertLess(3 + 1 + target.radius, target.feet[2])
                vitals = self.fight.vitals[bot.entity]
                before = vitals.total
                point = (target.feet[0], target.feet[1] + target.eye, target.feet[2])
                angles = aim(self.fight._eye(caster), point)
                self.swing(others={caster: (*angles, 16)})
                pool = self.fight.zarya_barriers.projected[caster.body]
                self.assertEqual((pool.recipient, pool.remaining), (bot.entity, 115))
                self.assertAlmostEqual(caster.body_script.component.entity_var(87).value(), 17)
                self.assertEqual(vitals.total, before)
                self.assertFalse(self.rein.client.messages(combat.HIT))

    @unittest.skipUnless(os.environ.get("ORPHEUS_REINHARDT_COLLISION"), "requires shield mesh")
    def test_hammer_hits_raised_mesh_and_its_owner_once(self):
        self.prepare()
        self.run.match.switch_hero(self.behind, heroes()[reinhardt_barrier.HERO])
        self.behind.mover.position[:] = [0.65, 0, 2.7]
        self.behind.mover.body = None
        self.advance(20)
        poses = {self.behind: (-32768, 0, 2)}
        self.advance(5, poses)
        shield = self.fight.reinhardt_barriers.states[self.behind.body]
        self.assertTrue(shield.active)
        self.run.step({self.rein: (0, 0, 1), self.zarya: (0, 0, 8), **poses})
        self.advance(60, poses)
        self.assertEqual(shield.current, 1515)
        self.assertEqual(self.health(self.behind), 420)
        self.assertEqual(self.health(self.zarya), 400)
        self.assertEqual(self.fight.zarya_barriers.active[self.zarya.body].remaining, 115)

    def test_flashbang_interrupts_windup_before_zarya_is_hit(self):
        self.prepare()
        self.run.match.switch_hero(self.behind, heroes()[0x02E0000000000042])
        self.behind.mover.position[:] = [0, 0, 1.8]
        self.behind.mover.body = None
        self.advance(20)
        self.run.step({self.rein: (0, 0, 1), self.behind: (-32768, 0, 16)})
        self.advance(20, {self.behind: (-32768, 0, 0)})
        self.assertTrue(self.fight.is_stunned(self.rein))
        self.advance(90)
        self.assertFalse(self.fight.is_stunned(self.rein))
        self.assertEqual(self.health(self.zarya), 400)
        self.assertFalse(self.rein.client.messages(combat.HIT))
        self.swing()
        self.assertEqual(self.health(self.zarya), 315)

    def test_active_deflect_and_cryo_deny_hammer_body_damage(self):
        for hero, button in ((0x02E0000000000029, 16), (0x02E00000000000DD, 8)):
            with self.subTest(hero=hero):
                self.prepare()
                self.run.match.switch_hero(self.zarya, heroes()[hero])
                self.zarya.mover.position[:] = [0, 0, 2.3]
                self.zarya.mover.body = None
                self.advance(20)
                before = self.health(self.zarya)
                self.run.step({self.rein: (0, 0, 1), self.zarya: (-32768, 0, button)})
                self.advance(60, {self.zarya: (-32768, 0, 0)})
                self.assertEqual(self.health(self.zarya), before)
                self.assertFalse(self.rein.client.messages(combat.HIT))

class IncomingEarthshatterTests(IncomingReinhardtFixture):
    def prepare(self, *, distance=10, ally=False, projected=False):
        self.scene(ally=ally, projected=projected)
        self.zarya.mover.position[:] = [0, 0, distance]
        self.zarya.mover.body = None
        self.behind.mover.position[:] = [6 if projected else 12, 0, distance]
        self.behind.mover.body = None
        self.advance(2)

    def cast(self, others=None, poses=None):
        cost = self.rein.body_script.component.entity_var(338).value()
        self.assertEqual(cost, 1540)
        self.rein.body_script.set_ult_charge(cost)
        self.run.step({self.rein: (0, 0, 32), **(others or {})})
        self.assertEqual(self.rein.body_script.component.entity_var(342).value(), 0)
        self.advance(27, poses)
        self.assertFalse(self.fight.shatters)
        self.advance(1, poses)
        wave = self.fight.shatters[self.rein.body]
        self.assertEqual((wave.speed, wave.reach, wave.cone, wave.duration), (40, 20, 50, 2.5))
        return wave

    def test_far_wave_damage_stun_and_exclusive_deadline(self):
        self.prepare()
        self.cast()
        for _ in range(40):
            self.advance(1)
            if self.health(self.zarya) < 400:
                break
        self.assertEqual(self.health(self.zarya), 350)
        self.assertEqual(self.fight.stuns[self.zarya.body] - self.fight.tick, 157)
        self.advance(156)
        self.assertTrue(self.fight.is_stunned(self.zarya))
        self.advance(1)
        self.assertFalse(self.fight.is_stunned(self.zarya))
        self.assertFalse(self.fight.shatters)
        self.assertEqual(self.health(self.zarya), 350)

    def test_personal_and_projected_barriers_absorb_wave_and_control(self):
        for projected in (False, True):
            with self.subTest(projected=projected):
                self.prepare(projected=projected)
                caster = self.behind if projected else self.zarya
                command = (-16384, 0, 16) if projected else (0, 0, 8)
                self.cast({caster: command})
                self.advance(42)
                self.assertEqual(self.health(self.zarya), 400)
                barriers = self.fight.zarya_barriers
                storage = barriers.projected if projected else barriers.active
                self.assertEqual(storage[caster.body].remaining, 150)
                self.assertAlmostEqual(caster.body_script.component.entity_var(87).value(), 10)
                self.assertFalse(self.fight.is_stunned(self.zarya))
                self.assertFalse(self.rein.client.messages(combat.HIT))

    def test_near_and_front_are_two_distinct_hits(self):
        self.prepare(distance=3)
        self.cast()
        self.advance(45)
        self.assertEqual(self.health(self.zarya), 100)
        self.assertEqual([hit["+0x78"]["+0x4"] for hit in self.rein.client.messages(combat.HIT)], [250, 50])
        self.assertTrue(self.fight.is_stunned(self.zarya))

    def test_wall_range_and_team_prevent_wave_damage(self):
        for distance, ally, wall in ((10, False, True), (22, False, False), (10, True, False)):
            with self.subTest(distance=distance, ally=ally, wall=wall):
                self.prepare(distance=distance, ally=ally)
                if wall:
                    self.run.match.collision_world = World([
                        (-30, 0, -30, -30, 0, 30, 30, 0, 30), (-30, 0, -30, 30, 0, 30, 30, 0, -30),
                        (-15, -10, 5, 15, -10, 5, 15, 10, 5), (-15, -10, 5, 15, 10, 5, -15, 10, 5),
                    ])
                self.cast()
                self.advance(45)
                self.assertEqual(self.health(self.zarya), 400)
                self.assertFalse(self.fight.is_stunned(self.zarya))

    def test_near_breaking_hit_is_absorbed_but_later_front_hits(self):
        self.prepare(distance=3)
        self.cast({self.zarya: (0, 0, 8)})
        self.assertEqual(self.health(self.zarya), 400)
        self.assertFalse(self.fight.is_stunned(self.zarya))
        self.assertAlmostEqual(self.zarya.body_script.component.entity_var(87).value(), 40)
        self.advance(15)
        self.assertEqual(self.health(self.zarya), 350)
        self.assertTrue(self.fight.is_stunned(self.zarya))
        self.assertEqual([hit["+0x78"]["+0x4"] for hit in self.rein.client.messages(combat.HIT)], [50])

    @unittest.skipUnless(npc_fixtures.NPC_DATA, "requires all five actual NPC definitions")
    def test_far_wave_hits_all_five_actual_npcs_behind_a_bubbled_zarya(self):
        fixtures = npc_fixtures.ZaryaFiveNpcTests
        fixtures.setUpClass()
        self.addCleanup(fixtures.tearDownClass)
        for definition in fixtures.catalog.values():
            with self.subTest(npc=definition.name):
                self.prepare()
                bot = npc.spawn(self.run.match, definition, (0.8, 0, 11), team=self.zarya.team)
                vitals = self.fight.vitals[bot.entity]
                before = vitals.total
                self.cast({self.zarya: (0, 0, 8)})
                self.advance(45)
                self.assertEqual(self.health(self.zarya), 400)
                self.assertFalse(self.fight.is_stunned(self.zarya))
                self.assertEqual(before - vitals.total, 50)
                self.assertTrue(self.fight.is_stunned(bot))

    @unittest.skipUnless(npc_fixtures.NPC_DATA, "requires all five actual NPC definitions")
    def test_npc_projection_blocks_far_wave_or_breaks_on_near_impact(self):
        fixtures = npc_fixtures.ZaryaFiveNpcTests
        fixtures.setUpClass()
        self.addCleanup(fixtures.tearDownClass)
        for definition in fixtures.catalog.values():
            for near in (False, True):
                with self.subTest(npc=definition.name, near=near):
                    self.prepare(projected=True)
                    distance = 3 if near else 8
                    caster = self.behind
                    caster.mover.position[:] = [6, 0, distance]
                    caster.mover.body = None
                    bot = npc.spawn(self.run.match, definition, (0, 0, distance), team=caster.team)
                    vitals = self.fight.vitals[bot.entity]
                    before = vitals.total
                    self.cast({caster: (-16384, 0, 16)})
                    self.assertEqual(vitals.total, before)
                    self.assertFalse(self.fight.is_stunned(bot))
                    self.advance(40)
                    self.assertEqual(vitals.total, before - (50 if near else 0))
                    self.assertEqual(self.fight.is_stunned(bot), near)
                    energy = caster.body_script.component.entity_var(87).value()
                    self.assertAlmostEqual(energy, 40 if near else 10)
                    if not near:
                        pool = self.fight.zarya_barriers.projected[caster.body]
                        self.assertEqual((pool.recipient, pool.remaining), (bot.entity, 150))
                    self.assertEqual(self.health(self.zarya), 350)
                    self.assertTrue(self.fight.is_stunned(self.zarya))
                    self.assertEqual(self.health(caster), 400)

    @unittest.skipUnless(os.environ.get("ORPHEUS_REINHARDT_COLLISION"), "requires shield mesh")
    def test_raised_mesh_stops_front_before_owner_and_zarya(self):
        self.prepare()
        self.run.match.switch_hero(self.behind, heroes()[reinhardt_barrier.HERO])
        self.behind.mover.position[:] = [0, 0, 5]
        self.behind.mover.body = None
        self.advance(20)
        poses = {self.behind: (-32768, 0, 2)}
        self.advance(5, poses)
        shield = self.fight.reinhardt_barriers.states[self.behind.body]
        self.assertTrue(shield.active)
        self.cast(poses, poses)
        self.advance(35, poses)
        self.assertEqual(shield.current, 1550)
        self.assertEqual(self.health(self.behind), 500)
        self.assertEqual(self.health(self.zarya), 400)

    def test_wave_front_and_hit_history_are_snapshot_causal(self):
        self.prepare()
        wave = self.cast()

        def digest():
            graph = _Graph({"shooter": self.rein}, {})
            root = graph.value(wave)
            return snapshot_digest({"schema": SCHEMA, "scope": SCOPE, "restorable": False,
                                    "roots": {"wave": root}, "nodes": graph.finish(), "assets": {}})

        first = digest()
        self.advance(16)
        travelled = digest()
        self.assertNotEqual(first, travelled)
        self.assertIn(self.zarya.body, wave.hit)
        wave.hit.clear()
        self.assertNotEqual(travelled, digest())

    def test_flashbang_cancels_windup_without_refunding_ultimate(self):
        self.prepare()
        self.run.match.switch_hero(self.behind, heroes()[0x02E0000000000042])
        self.behind.mover.position[:] = [0, 0, 1.8]
        self.behind.mover.body = None
        self.advance(20)
        self.rein.body_script.set_ult_charge(1540)
        self.run.step({self.rein: (0, 0, 32), self.behind: (-32768, 0, 16)})
        self.advance(20, {self.behind: (-32768, 0, 0)})
        self.assertTrue(self.fight.is_stunned(self.rein))
        self.advance(80)
        self.assertEqual(self.health(self.zarya), 400)
        self.assertEqual(self.rein.body_script.component.entity_var(342).value(), 0)
        self.assertFalse(self.fight.shatters)
        self.assertFalse(self.rein.client.messages(combat.HIT))

class IncomingChargeTests(IncomingReinhardtFixture):
    def prepare(self, *, ally=False, projected=False, wall=True):
        self.scene(ally=ally, projected=projected)
        self.behind.mover.position[:] = [4, 0, 4] if projected else [12, 0, 5]
        self.behind.mover.body = None
        if wall:
            self.run.match.collision_world = World([
                (-30, 0, -30, -30, 0, 80, 30, 0, 80), (-30, 0, -30, 30, 0, 80, 30, 0, -30),
                (-15, -10, 7, 15, -10, 7, 15, 10, 7), (-15, -10, 7, 15, 10, 7, -15, 10, 7),
            ])
        else:
            self.run.match.collision_world = flat_world(0)
        for actor in self.run.players:
            actor.mover.use(world=self.run.match.collision_world)
        self.advance(2)

    def start(self, others=None):
        self.run.step({self.rein: (0, 0, 8), **(others or {})})
        for _ in range(85):
            self.advance(1)
            contact = self.fight.charge_contacts.get(self.rein.body)
            if contact is not None and contact.pinned == self.zarya.body:
                return contact
        self.fail("actual Charge did not capture Zarya")

    def test_charge_carries_and_applies_300_wall_damage_once(self):
        self.prepare()
        self.start()
        self.assertEqual(self.health(self.zarya), 400)
        self.assertTrue(self.fight.is_stunned(self.zarya))
        self.zarya.body_script.set_ult_charge(2100)
        self.run.step({self.zarya: (0, 0, 32 | 8 | 1)})
        self.assertEqual(self.zarya.body_script.component.entity_var(342).value(), 2100)
        self.assertFalse(self.fight.rockets)
        self.advance(50)
        self.assertEqual(self.health(self.zarya), 100)
        self.assertFalse(self.fight.is_stunned(self.zarya))
        self.assertEqual([hit["+0x78"]["+0x4"] for hit in self.rein.client.messages(combat.HIT)], [300])

    def test_personal_and_projected_barriers_absorb_charge_wall_hit(self):
        for projected in (False, True):
            with self.subTest(projected=projected):
                self.prepare(projected=projected)
                caster = self.behind if projected else self.zarya
                command = (-16384, 0, 16) if projected else (0, 0, 8)
                self.start({caster: command})
                self.advance(50)
                self.assertEqual(self.health(self.zarya), 400)
                self.assertAlmostEqual(caster.body_script.component.entity_var(87).value(), 40)
                self.assertFalse(self.fight.is_stunned(self.zarya))
                self.assertFalse(self.rein.client.messages(combat.HIT))

    def test_allied_zarya_is_not_captured(self):
        self.prepare(ally=True)
        self.run.step({self.rein: (0, 0, 8)})
        self.advance(85)
        self.assertGreater(self.rein.mover.position[2], 4)
        self.assertEqual(self.health(self.zarya), 400)
        self.assertFalse(self.fight.is_stunned(self.zarya))

    def test_timeout_releases_without_wall_damage(self):
        self.prepare(wall=False)
        self.start()
        self.assertTrue(self.fight.is_stunned(self.zarya))
        self.advance(205)
        self.assertGreater(self.rein.mover.position[2], 35)
        self.assertEqual(self.health(self.zarya), 400)
        self.assertFalse(self.fight.is_stunned(self.zarya))
        self.assertFalse(self.fight.charge_contacts)
        self.run.step({self.rein: (0, 0, 8)})
        self.advance(45)
        self.assertFalse(self.fight.charge_contacts)

    def test_lateral_contact_deals_50_without_capture(self):
        self.prepare()
        self.zarya.mover.position[0] = 1.45
        self.zarya.mover.body = None
        self.advance(2)
        self.run.step({self.rein: (0, 0, 8)})
        self.advance(85)
        self.assertEqual(self.health(self.zarya), 350)
        self.assertGreater(self.zarya.mover.position[0], 1.5)
        self.assertFalse(self.fight.is_stunned(self.zarya))
        self.assertEqual([hit["+0x78"]["+0x4"] for hit in self.rein.client.messages(combat.HIT)], [50])

    @unittest.skipUnless(npc_fixtures.NPC_DATA, "requires actual five-NPC bodies")
    def test_charge_bumps_projected_edges_without_capturing_npcs(self):
        fixtures = npc_fixtures.ZaryaFiveNpcTests
        fixtures.setUpClass()
        self.addCleanup(fixtures.tearDownClass)
        for definition in fixtures.catalog.values():
            with self.subTest(npc=definition.name):
                self.prepare(projected=True)
                caster = self.behind
                caster.mover.position[:] = [9, 0, 5.5]
                caster.mover.body = None
                bot = npc.spawn(self.run.match, definition, (2, 0, 5.5), team=caster.team)
                target = next(t for t in self.fight._targets() if t.owner is bot)
                instance = reinhardt_charge.instance_of(self.rein)
                radius = expr.to_float(instance.find_var(0, 9149).value())
                offset = radius + (target.radius + zarya.BUBBLE_RADIUS) / 2
                self.assertLess(radius + target.radius, offset)
                bot.position = (offset, 0, 5.5)
                before = self.fight.vitals_of(target).total
                angles = aim(self.fight._eye(caster), (offset, target.eye, 5.5))
                self.start({caster: (*angles, 16)})
                barriers = self.fight.zarya_barriers
                self.assertTrue(barriers.protects(bot))
                pool = barriers.projected[caster.body]
                self.assertEqual(pool.recipient, bot.entity)
                self.advance(50)
                self.assertEqual(pool.remaining, 150)
                self.assertAlmostEqual(caster.body_script.component.entity_var(87).value(), 10)
                self.assertEqual(self.fight.vitals_of(target).total, before)
                self.assertEqual(bot.position, (offset, 0, 5.5))
                self.assertFalse(self.fight.is_stunned(bot))
                self.assertEqual(self.health(self.zarya), 100)
                hits = self.rein.client.messages(combat.HIT)
                self.assertEqual([hit["+0x78"]["+0x4"] for hit in hits], [300])

    @unittest.skipUnless(npc_fixtures.NPC_DATA, "requires all five actual NPC definitions")
    def test_four_actual_npcs_are_captured_and_heavy_stops_the_charge(self):
        fixtures = npc_fixtures.ZaryaFiveNpcTests
        fixtures.setUpClass()
        self.addCleanup(fixtures.tearDownClass)
        for definition in fixtures.catalog.values():
            with self.subTest(npc=definition.name):
                self.prepare()
                for actor in self.run.players:
                    actor.client = npc_fixtures.NpcClient(actor)
                self.zarya.mover.position[0] = 12
                self.zarya.mover.body = None
                bot = npc.spawn(self.run.match, definition, (0, 0, 4), team=self.zarya.team)
                vitals = self.fight.vitals[bot.entity]
                before = vitals.total
                self.run.step({self.rein: (0, 0, 8)})
                self.advance(100)
                if definition.name == "Heavy Assault":
                    self.assertEqual(vitals.total, before)
                    self.assertEqual(bot.position[2], 4)
                    self.assertLess(self.rein.mover.position[2], 4)
                else:
                    self.assertEqual(vitals.total, max(0, before - 300))
                    self.assertGreater(bot.position[2], 5)
                    self.assertEqual(bot.dead, before <= 300)
                    if bot.dead:
                        self.assertEqual(len(self.rein.client.deaths), 1)
                self.assertFalse(self.fight.is_stunned(bot))
                self.assertFalse(self.fight.charge_contacts)

    @unittest.skipUnless(npc_fixtures.NPC_DATA, "requires all five actual NPC definitions")
    def test_projection_absorbs_npc_charge_wall_hit_and_preserves_heavy_immunity(self):
        fixtures = npc_fixtures.ZaryaFiveNpcTests
        fixtures.setUpClass()
        self.addCleanup(fixtures.tearDownClass)
        for definition in fixtures.catalog.values():
            with self.subTest(npc=definition.name):
                self.prepare(projected=True)
                for actor in self.run.players:
                    actor.client = npc_fixtures.NpcClient(actor)
                caster = self.behind
                self.zarya.mover.position[0] = 12
                self.zarya.mover.body = None
                bot = npc.spawn(self.run.match, definition, (0, 0, 4), team=caster.team)
                vitals = self.fight.vitals[bot.entity]
                before = vitals.total
                self.run.step({self.rein: (0, 0, 8), caster: (-16384, 0, 16)})
                service = self.fight.zarya_barriers
                self.assertTrue(service.protects(bot))
                pool = service.projected[caster.body]
                self.assertEqual((pool.recipient, pool.remaining), (bot.entity, 200))
                captured = False
                for _ in range(100):
                    self.advance(1)
                    contact = self.fight.charge_contacts.get(self.rein.body)
                    if not captured and contact is not None and contact.pinned == bot.entity:
                        captured = True
                        self.assertTrue(self.fight.is_stunned(bot))
                        self.assertEqual(pool.remaining, 200)
                        self.assertEqual(vitals.total, before)
                heavy = definition.name == "Heavy Assault"
                self.assertEqual(captured, not heavy)
                self.assertEqual(pool.remaining, 200 if heavy else 0)
                energy = caster.body_script.component.entity_var(87).value()
                self.assertAlmostEqual(energy or 0, 0 if heavy else 40)
                self.assertEqual(vitals.total, before)
                self.assertFalse(bot.dead)
                self.assertFalse(self.fight.is_stunned(bot))
                self.assertFalse(self.fight.charge_contacts)
                self.assertFalse(self.rein.client.deaths)
                self.assertFalse(self.rein.client.messages(combat.HIT))
                if heavy:
                    self.assertEqual(bot.position[2], 4)
                    self.assertLess(self.rein.mover.position[2], 4)
                else:
                    self.assertGreater(bot.position[2], 5)

    def test_flashbang_releases_a_captured_zarya_without_wall_damage(self):
        self.prepare()
        self.run.match.switch_hero(self.behind, heroes()[0x02E0000000000042])
        self.behind.mover.position[:] = [0, 0, 6]
        self.behind.mover.body = None
        self.advance(20)
        self.start()
        self.run.step({self.behind: (-32768, 0, 16)})
        self.advance(12, {self.behind: (-32768, 0, 0)})
        self.assertTrue(self.fight.is_stunned(self.rein))
        self.assertFalse(self.fight.is_stunned(self.zarya))
        self.advance(60)
        self.assertEqual(self.health(self.zarya), 400)
        self.assertFalse(self.fight.charge_contacts)

    def test_capture_expires_without_source_input(self):
        self.prepare(wall=False)
        contact = self.start()
        for tick, pinned in ((contact.deadline - 1, True), (contact.deadline, False)):
            self.run.match.update(tick * combat.TICK, tick)
            self.assertEqual(self.fight.is_stunned(self.zarya), pinned)
        self.assertEqual(self.health(self.zarya), 400)
        self.assertFalse(self.fight.charge_contacts)

    def test_actual_graviton_cancels_charge_and_releases_victim(self):
        self.prepare(projected=True, wall=False)
        self.start()
        caster = self.behind
        caster.body_script.set_ult_charge(2100)
        angles = aim(self.fight._eye(caster), (0, 0, 6))
        self.run.step({caster: (*angles, 32)})
        for _ in range(40):
            self.advance(1)
            if self.fight.zarya_gravitons.fields and not self.fight.charge_contacts:
                break
        self.assertTrue(self.fight.zarya_gravitons.fields)
        self.assertFalse(self.fight.charge_contacts)
        self.assertFalse(self.fight.is_stunned(self.zarya))
        self.assertEqual(self.health(self.zarya), 400)

    def test_graviton_blocks_new_player_charge_and_cooldown_until_pull_ends(self):
        self.prepare(projected=True, wall=False)
        caster = self.behind
        caster.body_script.set_ult_charge(2100)
        angles = aim(self.fight._eye(caster), self.fight._eye(self.rein))
        self.run.step({caster: (*angles, 32)})
        gravitons = self.fight.zarya_gravitons
        for _ in range(45):
            if gravitons.fields:
                break
            self.advance(1)
        field, = gravitons.fields
        self.assertIsNotNone(gravitons.movement_for(self.rein))
        instance = next(i for i in self.rein.body_script.component.instances.values()
                        if i.graph.index == 0x007D)
        windup, drive = instance.state(0), instance.state(3)
        cooldown = instance.vars[61]
        before = self.health(self.zarya)
        initial_windup = windup.counter
        initial_drive = drive.counter
        held = {self.rein: (0, 0, 8)}
        while self.run.frame < field.end_tick - 1:
            self.advance(1, held)
            self.assertEqual(windup.counter, initial_windup)
            self.assertEqual(cooldown.value() or 0, 0)
            self.assertEqual(drive.counter, initial_drive)
            self.assertNotIn(self.rein.body, self.fight.charge_contacts)
            self.assertEqual(self.health(self.zarya), before)

        self.advance(1, held)
        self.assertFalse(gravitons.fields)
        self.assertTrue(windup.active)
        self.assertEqual(windup.counter, initial_windup + 1)
        self.assertEqual(drive.counter, initial_drive)
        for _ in range(36):
            self.advance(1, held)
            self.assertFalse(drive.active)
        self.advance(1, held)
        self.assertTrue(drive.active)
        self.assertEqual(drive.counter, initial_drive + 1)

    def test_graviton_cancels_existing_player_charge_windup(self):
        self.prepare(projected=True, wall=False)
        caster = self.behind
        caster.body_script.set_ult_charge(2100)
        angles = aim(self.fight._eye(caster), self.fight._eye(self.rein))
        self.run.step({caster: (*angles, 32), self.rein: (0, 0, 8)})
        instance = next(i for i in self.rein.body_script.component.instances.values()
                        if i.graph.index == 0x007D)
        windup, drive = instance.state(0), instance.state(3)
        self.assertTrue(windup.active)
        self.assertFalse(drive.active)
        initial_drive = drive.counter
        initial_windup = windup.counter
        before = self.health(self.zarya)
        gravitons = self.fight.zarya_gravitons
        for _ in range(35):
            if gravitons.fields:
                break
            self.advance(1)
        field, = gravitons.fields
        self.assertTrue(windup.active)
        self.assertFalse(drive.active)
        self.advance(1)
        self.assertIsNotNone(gravitons.movement_for(self.rein))
        self.assertFalse(windup.active)
        cooldown = instance.vars[61]
        self.assertAlmostEqual(cooldown.value(), 10, delta=0.017)

        while self.run.frame < field.end_tick:
            self.advance(1)
            self.assertFalse(windup.active)
            self.assertEqual(windup.counter, initial_windup)
            self.assertEqual(drive.counter, initial_drive)
            self.assertNotIn(self.rein.body, self.fight.charge_contacts)
        self.assertFalse(gravitons.fields)
        self.assertEqual(self.health(self.zarya), before)
        self.assertFalse(self.fight.is_stunned(self.zarya))
        self.assertGreater(cooldown.value(), 0)
        self.advance(5, {self.rein: (0, 0, 8)})
        self.assertEqual(windup.counter, initial_windup)
        self.assertEqual(drive.counter, initial_drive)

    def prepare_counter(self, *, allies=False, distance=12):
        self.prepare(wall=False)
        self.run.match.switch_hero(self.behind, heroes()[reinhardt_barrier.HERO])
        self.behind.team = self.rein.team if allies else self.zarya.team
        self.behind.mover.position[:] = [0, 0, distance]
        self.behind.mover.body = None
        poses = {self.behind: (-32768, 0, 0)}
        self.advance(20, poses)
        return poses

    def until_counter(self, poses):
        for _ in range(100):
            self.advance(1, poses)
            if self.fight.is_stunned(self.rein) and self.fight.is_stunned(self.behind):
                return
        self.fail("opposed Charge inputs did not cause a mutual knockdown")

    def test_counter_charge_releases_pinned_zarya_without_damage(self):
        poses = self.prepare_counter()
        self.run.step({self.rein: (0, 0, 8), self.zarya: (0, 0, 8), **poses})
        for _ in range(80):
            self.advance(1, poses)
            if self.fight.is_stunned(self.zarya):
                break
        self.assertTrue(self.fight.is_stunned(self.zarya))
        self.run.step({self.behind: (-32768, 0, 8)})
        self.until_counter(poses)
        self.assertFalse(self.fight.is_stunned(self.zarya))
        self.assertFalse(self.fight.charge_contacts)
        self.assertEqual(self.health(self.zarya), 400)
        barrier = next(i for i in self.zarya.body_script.component.instances.values()
                       if i.graph.index == 0x0244)
        self.assertTrue(barrier.state(2).active)
        self.assertFalse(self.zarya.body_script.component.entity_var(87).value())
        self.assertEqual(self.health(self.rein), 500)
        self.assertEqual(self.health(self.behind), 500)

    def test_head_on_charge_uses_authored_knockdown_for_both_players(self):
        poses = self.prepare_counter(distance=9)
        self.zarya.mover.position[0] = 12
        self.zarya.mover.body = None
        self.run.step({self.rein: (0, 0, 8), self.behind: (-32768, 0, 8)})
        self.until_counter(poses)
        a = self.fight.stuns[self.rein.body]
        b = self.fight.stuns[self.behind.body]
        self.assertEqual(a, b)
        self.assertEqual(a - self.fight.tick, 125)
        self.advance(124, poses)
        self.assertTrue(self.fight.is_stunned(self.rein))
        self.assertTrue(self.fight.is_stunned(self.behind))
        self.advance(1, poses)
        self.assertFalse(self.fight.is_stunned(self.rein))
        self.assertFalse(self.fight.is_stunned(self.behind))
        self.assertFalse(self.fight.charge_contacts)
        self.assertEqual(self.health(self.rein), 500)
        self.assertEqual(self.health(self.behind), 500)

    def test_allied_head_on_charges_do_not_knock_down_either_player(self):
        poses = self.prepare_counter(allies=True, distance=9)
        self.zarya.mover.position[0] = 12
        self.zarya.mover.body = None
        self.run.step({self.rein: (0, 0, 8), self.behind: (-32768, 0, 8)})
        self.advance(90, poses)
        self.assertFalse(self.fight.is_stunned(self.rein))
        self.assertFalse(self.fight.is_stunned(self.behind))
        self.assertEqual(self.health(self.rein), 500)
        self.assertEqual(self.health(self.behind), 500)

    def test_wall_between_charges_prevents_mutual_knockdown(self):
        poses = self.prepare_counter(distance=8)
        self.zarya.mover.position[0] = 12
        self.zarya.mover.body = None
        self.run.match.collision_world = World([
            (-30, 0, -30, -30, 0, 30, 30, 0, 30), (-30, 0, -30, 30, 0, 30, 30, 0, -30),
            (-15, -10, 4, 15, -10, 4, 15, 10, 4), (-15, -10, 4, 15, 10, 4, -15, 10, 4),
            (-15, -10, 4, 15, 10, 4, 15, -10, 4), (-15, -10, 4, -15, 10, 4, 15, 10, 4),
        ])
        for actor in self.run.players:
            actor.mover.use(world=self.run.match.collision_world)
        self.run.step({self.rein: (0, 0, 8), self.behind: (-32768, 0, 8)})
        self.advance(90, poses)
        self.assertLess(self.rein.mover.position[2], 4)
        self.assertGreater(self.behind.mover.position[2], 4)
        self.assertFalse(self.fight.is_stunned(self.rein))
        self.assertFalse(self.fight.is_stunned(self.behind))
        self.assertFalse(self.fight.charge_contacts)

@unittest.skipUnless(npc_fixtures.NPC_DATA, "requires actual Heavy Assault graph and mover")
class IncomingHeavyChargeTests(IncomingReinhardtFixture):
    @classmethod
    def setUpClass(cls):
        npc_fixtures.ZaryaFiveNpcTests.setUpClass()
        cls.catalog = npc_fixtures.ZaryaFiveNpcTests.catalog

    @classmethod
    def tearDownClass(cls):
        npc_fixtures.ZaryaFiveNpcTests.tearDownClass()

    def engage(self, *, wall=True, shield=False):
        self.scene()
        for actor in self.run.players:
            actor.client = npc_fixtures.NpcClient(actor)
        self.rein.mover.position[0] = 12
        self.rein.mover.body = None
        self.behind.mover.position[0] = 12
        self.behind.mover.body = None
        if wall:
            self.run.match.collision_world = World([
                (-30, 0, -30, -30, 0, 80, 30, 0, 80), (-30, 0, -30, 30, 0, 80, 30, 0, -30),
                (-15, -10, 8, 15, -10, 8, 15, 10, 8), (-15, -10, 8, 15, 10, 8, -15, 10, 8),
            ])
        for actor in self.run.players:
            actor.mover.use(world=self.run.match.collision_world)
        definition = next(d for d in self.catalog.values() if d.name == "Heavy Assault")
        self.bot = npc.spawn(self.run.match, definition, (0, 0, 0), team=self.rein.team)
        self.run.step({self.zarya: (-32768, 0, 8 if shield else 0)})
        result = Console({}).press("0", self.run.match, self.zarya, attack_action="charge")
        self.assertEqual(result["entity"], self.bot.entity)
        self.assertEqual(npc_control.button(self.bot), 35)
        self.assertIsNotNone(heavy_charge.instance_of(self.bot))

    def until_pin(self):
        for _ in range(90):
            self.advance(1)
            contact = self.fight.charge_contacts.get(self.bot.entity)
            if contact is not None and contact.pinned == self.zarya.body:
                return
        self.fail("actual Heavy Assault Charge did not pin Zarya")

    def test_heavy_charge_transports_and_hits_wall_for_300(self):
        self.engage()
        self.advance(48)
        self.assertEqual(self.bot.position, (0, 0, 0))
        self.until_pin()
        self.assertTrue(self.fight.is_stunned(self.zarya))
        self.assertEqual(self.health(self.zarya), 400)
        self.advance(45)
        self.assertEqual(self.health(self.zarya), 100)
        self.assertFalse(self.fight.is_stunned(self.zarya))
        self.assertNotIn(self.bot.entity, self.fight.charge_contacts)
        self.assertGreater(heavy_charge.instance_of(self.bot).find_var(0, 61).value(), 13)

    def test_personal_barrier_absorbs_impact_and_credits_40(self):
        self.engage(shield=True)
        self.until_pin()
        self.advance(45)
        self.assertEqual(self.health(self.zarya), 400)
        self.assertAlmostEqual(self.zarya.body_script.component.entity_var(87).value(), 40)
        self.assertFalse(self.fight.is_stunned(self.zarya))

    def test_manual_stop_releases_without_wall_damage(self):
        self.engage(wall=False)
        self.until_pin()
        before = self.health(self.zarya)
        npc_control.stop(self.run.match)
        self.assertFalse(self.fight.is_stunned(self.zarya))
        position = self.bot.position
        self.advance(45)
        self.assertEqual(self.bot.position, position)
        self.assertEqual(self.health(self.zarya), before)
        self.assertFalse(self.fight.charge_contacts)

    def test_projected_barrier_added_during_capture_absorbs_wall_hit(self):
        self.engage()
        self.run.match.switch_hero(self.behind, heroes()[0x02E0000000000068])
        self.behind.mover.position[:] = [4, 0, 4]
        self.behind.mover.body = None
        self.until_pin()
        angles = aim(self.fight._eye(self.behind), self.fight._eye(self.zarya))
        self.run.step({self.behind: (*angles, 16)})
        self.advance(45)
        self.assertEqual(self.health(self.zarya), 400)
        self.assertAlmostEqual(self.behind.body_script.component.entity_var(87).value(), 40)

    def test_heavy_releases_projected_capture_after_target_replacement_or_team_change(self):
        for replacement in (False, True):
            with self.subTest(replacement=replacement):
                self.engage()
                caster = self.behind
                self.assertTrue(self.run.match.switch_hero(caster, heroes()[0x02E0000000000068]))
                caster.mover.position[:] = [4, 0, 4]
                caster.mover.body = None
                self.until_pin()
                angles = aim(self.fight._eye(caster), self.fight._eye(self.zarya))
                self.run.step({caster: (*angles, 16)})
                service = self.fight.zarya_barriers
                self.assertTrue(service.protects(self.zarya))
                pool = service.projected[caster.body]
                old_body = self.zarya.body
                self.assertEqual((pool.recipient, pool.remaining), (old_body, 200))
                if replacement:
                    self.assertTrue(self.run.match.switch_hero(self.zarya, heroes()[0x02E0000000000068]))
                    self.assertNotEqual(self.zarya.body, old_body)
                    self.assertNotIn(caster.body, service.projected)
                else:
                    self.zarya.team = self.bot.team
                self.advance(2)
                self.assertFalse(self.fight.charge_contacts)
                self.assertFalse(self.fight.is_stunned(self.zarya))
                self.assertFalse(service.protects(self.zarya))
                self.advance(45)
                self.assertEqual(self.health(self.zarya), 400)
                self.assertEqual(pool.remaining, 200)
                self.assertEqual(caster.body_script.component.entity_var(87).value() or 0, 0)

    def test_secondary_lethal_hit_interrupts_heavy_charge_windup(self):
        for lethal in (False, True):
            with self.subTest(lethal=lethal):
                self.engage()
                caster = self.behind
                self.assertTrue(self.run.match.switch_hero(caster, heroes()[0x02E0000000000068]))
                caster.mover.position[:] = [-4, 0, 0]
                caster.mover.body = None
                vitals = self.fight.vitals[self.bot.entity]
                if lethal:
                    vitals.now = dict.fromkeys(vitals.now, 0.0)
                    vitals.now[combat.HEALTH_POOL] = 40
                before = vitals.total
                start = self.bot.position
                angles = aim(self.fight._eye(caster), (0, 1, 0))
                self.run.step({caster: (*angles, 2)})
                for _ in range(45):
                    if vitals.total < before:
                        break
                    self.advance(1)
                self.assertEqual(self.bot.position, start)
                self.assertEqual(vitals.total, before - (40 if lethal else 47))
                self.assertEqual(self.bot.dead, lethal)
                self.assertEqual(self.health(self.zarya), 400)
                if lethal:
                    for _ in range(100):
                        self.advance(1)
                        self.assertNotIn(self.bot.entity, self.fight.charge_contacts)
                        self.assertFalse(self.fight.is_stunned(self.zarya))
                    self.assertEqual(self.health(self.zarya), 400)
                    self.assertEqual([entity for entity, _ in caster.client.deaths], [self.bot.entity])
                else:
                    self.until_pin()
                    self.assertTrue(self.fight.is_stunned(self.zarya))
                    self.advance(45)
                    self.assertEqual(self.health(self.zarya), 100)

    def test_lethal_primary_releases_heavy_capture_before_wall_damage(self):
        self.engage()
        self.until_pin()
        caster = self.behind
        self.assertTrue(self.run.match.switch_hero(caster, heroes()[0x02E0000000000068]))
        x, y, z = self.bot.position
        caster.mover.position[:] = [x + 3, y, z]
        caster.mover.body = None
        self.assertTrue(self.fight.is_stunned(self.zarya))
        vitals = self.fight.vitals[self.bot.entity]
        vitals.now = dict.fromkeys(vitals.now, 0.0)
        vitals.now[combat.HEALTH_POOL] = 1
        for _ in range(12):
            if self.bot.dead:
                break
            x, y, z = self.bot.position
            angles = aim(self.fight._eye(caster), (x, y + 1.4, z))
            self.run.step({caster: (*angles, 1)})
        self.assertTrue(self.bot.dead)
        self.assertEqual(vitals.killer, caster.body)
        self.assertEqual(self.health(self.zarya), 400)
        self.assertFalse(self.fight.charge_contacts)
        self.assertFalse(self.fight.is_stunned(self.zarya))
        self.assertEqual([entity for entity, _ in caster.client.deaths], [self.bot.entity])
        self.advance(50)
        self.assertEqual(self.health(self.zarya), 400)
        self.assertFalse(self.fight.charge_contacts)

    def test_heavy_charge_secondary_contact_respects_both_barriers(self):
        cases = (("body", "none"), ("body", "personal"), ("body", "projected"),
                 ("edge", "personal"), ("edge", "projected"), ("break", "projected"),
                 ("outside", "personal"), ("ally", "personal"), ("edge", "none"))
        for case, protection in cases:
            with self.subTest(case=case, protection=protection):
                self.engage()
                bystander, caster = self.rein, self.behind
                self.assertTrue(self.run.match.switch_hero(bystander, heroes()[0x02E0000000000068]))
                self.assertTrue(self.run.match.switch_hero(caster, heroes()[0x02E0000000000068]))
                bystander.team = caster.team = self.zarya.team
                if case == "ally":
                    bystander.team = self.bot.team
                target = next(t for t in self.fight._targets() if t.owner is bystander)
                tracker = heavy_charge.instance_of(self.bot).state(48)
                radius = expr.to_float(tracker.evaluate(tracker.node.field("m_radius"), watch=False))
                offset = radius + (target.radius + zarya.BUBBLE_RADIUS) / 2
                self.assertLess(radius + target.radius, offset)
                x = {"body": 1.4, "outside": radius + zarya.BUBBLE_RADIUS + .2}.get(case, offset)
                bystander.mover.position[:] = [x, 0, 5.5]
                caster.mover.position[:] = [9, 0, 5.5]
                for actor in (bystander, caster):
                    actor.mover.body = None
                commands = {}
                if protection == "personal":
                    commands[bystander] = (0, 0, 8)
                elif protection == "projected":
                    commands[caster] = (-16384, 0, 16)
                self.run.step(commands)
                start_x = bystander.mover.position[0]
                service = self.fight.zarya_barriers
                owner, pools = ((bystander, service.active) if protection == "personal"
                                else (caster, service.projected))
                self.assertEqual(service.protects(bystander), protection != "none")
                pool = pools[owner.body] if protection != "none" else None
                capacity = 25 if case == "break" else 200
                if pool is not None:
                    pool.remaining = capacity
                self.until_pin()
                self.advance(45)
                self.assertEqual(self.health(self.zarya), 100)
                self.assertFalse(self.fight.charge_contacts)
                self.assertFalse(self.fight.is_stunned(bystander))
                if protection == "none" and case == "body":
                    self.assertEqual(self.health(bystander), 350)
                    self.assertGreater(bystander.mover.position[0], start_x)
                else:
                    self.assertEqual(self.health(bystander), 400)
                    self.assertAlmostEqual(bystander.mover.position[0], start_x)
                    if pool is not None:
                        absorbed = {"body": 50, "edge": 50, "break": 25}.get(case, 0)
                        self.assertEqual(pool.remaining, capacity - absorbed)
                        energy = owner.body_script.component.entity_var(87).value() or 0
                        self.assertAlmostEqual(energy, absorbed / 5)

    def test_heavy_side_charge_checks_cryo_cover_at_the_barrier_surface(self):
        for projected in (False, True):
            for allied in (False, True):
                for case, x, absorbed in (("clear", 3.4, 50), ("covered", 3.4, 0), ("near", 2.65, 50)):
                    with self.subTest(projected=projected, allied=allied, case=case):
                        self.engage()
                        run, fight = self.run, self.fight
                        bystander, caster = self.rein, self.behind
                        for actor, position in ((bystander, (x, 0, 5.5)), (caster, (9, 0, 5.5))):
                            self.assertTrue(run.match.switch_hero(actor, self.zarya.hero))
                            actor.team = self.zarya.team
                            actor.mover.position[:] = position
                            actor.mover.body = None
                        mei_hero = heroes()[0x02E00000000000DD]
                        mei_team = self.zarya.team if allied else self.bot.team
                        mei = run.match.add_player(4, "Mei", mei_hero.guid, mei_team, False)
                        in_world(run.match, mei)
                        mei.client = npc_fixtures.NpcClient(mei)
                        run.players.append(mei)
                        self.assertTrue(run.match.switch_hero(mei, mei_hero))
                        mei.mover.position[:] = (1.7 if case != "clear" else 12, 0,
                                                 5.9 if case == "near" else 5.5)
                        mei.mover.body = None
                        owner = caster if projected else bystander
                        run.step({owner: (-16384, 0, 16) if projected else (0, 0, 8), mei: (0, 0, 8)})
                        self.assertEqual(phase.protection(mei), "cryo")
                        self.assertTrue(fight.zarya_barriers.protects(bystander))
                        pools = fight.zarya_barriers.projected if projected else fight.zarya_barriers.active
                        pool = pools[owner.body]
                        target = next(t for t in fight._targets() if t.owner is bystander)
                        start = target.feet
                        self.assertGreater(x, 2 + target.radius)
                        self.assertLess(x, 2 + zarya.BUBBLE_RADIUS)
                        origin = (0, 1, 5.5)
                        surface = (x - zarya.BUBBLE_RADIUS, 1, 5.5)
                        self.assertEqual(fight.combat_visible(origin, surface, surface, 0), case != "covered")

                        self.until_pin()
                        self.advance(45)
                        self.assertEqual(self.health(self.zarya), 100)
                        self.assertEqual(self.health(bystander), 400)
                        self.assertEqual(tuple(bystander.mover.position), start)
                        self.assertEqual(self.health(mei), 250)
                        self.assertEqual(pool.remaining, 200 - absorbed)
                        energy = owner.body_script.component.entity_var(87).value() or 0
                        self.assertAlmostEqual(energy, absorbed / 5)

    def test_cryo_stops_captured_zarya_and_resolves_one_heavy_impact(self):
        for allied in (False, True):
            for protection in ("none", "personal", "projected"):
                with self.subTest(allied=allied, protection=protection):
                    self.engage(wall=False, shield=protection == "personal")
                    run, fight = self.run, self.fight
                    mei = self.rein
                    self.assertTrue(run.match.switch_hero(mei, heroes()[0x02E00000000000DD]))
                    mei.team = self.zarya.team if allied else self.bot.team
                    mei.mover.position[:] = (0, 0, 7)
                    mei.mover.body = None
                    owner = self.zarya
                    if protection == "projected":
                        owner = self.behind
                        self.assertTrue(run.match.switch_hero(owner, self.zarya.hero))
                        owner.team = self.zarya.team
                        owner.mover.position[:] = (5, 0, 5)
                        owner.mover.body = None
                    self.until_pin()
                    self.assertTrue(fight.is_stunned(self.zarya))
                    captured_at = self.zarya.mover.position[2]
                    commands = {mei: (0, 0, 8)}
                    if protection == "projected":
                        angles = aim(fight._eye(owner), fight._eye(self.zarya))
                        commands[owner] = (*angles, 16)
                    run.step(commands)
                    self.assertEqual(phase.protection(mei), "cryo")
                    self.assertTrue(fight.is_stunned(self.zarya))
                    if protection != "none":
                        self.assertTrue(fight.zarya_barriers.protects(self.zarya))
                        pools = (fight.zarya_barriers.projected if protection == "projected"
                                 else fight.zarya_barriers.active)
                        pool = pools[owner.body]
                        self.assertEqual(pool.remaining, 200)

                    for _ in range(40):
                        self.advance(1)
                        if self.bot.entity not in fight.charge_contacts:
                            break
                    self.assertNotIn(self.bot.entity, fight.charge_contacts)
                    self.assertFalse(fight.is_stunned(self.zarya))
                    self.assertEqual(phase.protection(mei), "cryo")
                    self.assertEqual(self.health(mei), 250)
                    stopped_at = self.zarya.mover.position[2]
                    self.assertGreater(stopped_at, captured_at)
                    self.assertAlmostEqual(stopped_at, 6, delta=0.08)
                    expected_health = 100 if protection == "none" else 400
                    self.assertEqual(self.health(self.zarya), expected_health)
                    if protection != "none":
                        self.assertEqual(pool.remaining, 0)
                        self.assertEqual(owner.body_script.component.entity_var(87).value(), 40)
                    cooldown = heavy_charge.instance_of(self.bot).vars[61]
                    self.assertGreater(cooldown.value(), 13)
                    self.advance(20)
                    self.assertEqual(self.health(self.zarya), expected_health)
                    self.assertAlmostEqual(self.zarya.mover.position[2], stopped_at)
                    self.assertFalse(fight.charge_contacts)

    def test_actual_graviton_releases_heavy_capture(self):
        self.engage(wall=False)
        self.run.match.switch_hero(self.behind, heroes()[0x02E0000000000068])
        self.behind.mover.position[:] = [4, 0, 4]
        self.behind.mover.body = None
        self.until_pin()
        self.behind.body_script.set_ult_charge(2100)
        angles = aim(self.fight._eye(self.behind), (0, 0, 6))
        self.run.step({self.behind: (*angles, 32)})
        for _ in range(45):
            self.advance(1)
            if self.fight.zarya_gravitons.fields and not self.fight.charge_contacts:
                break
        self.assertTrue(self.fight.zarya_gravitons.fields)
        self.assertFalse(self.fight.charge_contacts)
        self.assertFalse(self.fight.is_stunned(self.zarya))
        self.assertEqual(self.health(self.zarya), 400)

    def test_graviton_blocks_new_heavy_charge_until_pull_ends(self):
        self.engage(wall=False)
        match = self.run.match
        npc_control.stop(match)
        self.assertTrue(match.switch_hero(self.behind, heroes()[0x02E0000000000068]))
        self.behind.mover.position[:] = [4, 0, 4]
        self.behind.mover.body = None
        self.behind.body_script.set_ult_charge(2100)
        x, y, z = self.bot.position
        angles = aim(self.fight._eye(self.behind), (x, y + 1, z))
        self.run.step({self.behind: (*angles, 32)})
        gravitons = self.fight.zarya_gravitons
        for _ in range(45):
            self.advance(1)
            if self.bot.entity in gravitons.npc_movers:
                break
        field, = gravitons.fields
        self.assertIn(self.bot.entity, gravitons.npc_movers)
        x, y, z = self.bot.position
        selection = aim(self.fight._eye(self.zarya), (x, y + 1, z))
        self.run.step({self.zarya: (*selection, 0)})
        self.assertEqual(npc_control.start(match, self.zarya, action="charge"), self.bot.entity)
        drive = heavy_charge.instance_of(self.bot).state(3)
        before = self.health(self.zarya)
        while self.run.frame < field.end_tick - 1:
            self.advance(1)
            self.assertFalse(self.bot.attack_input)
            self.assertFalse(drive.active)
            self.assertNotIn(self.bot.entity, self.fight.charge_contacts)
            self.assertFalse(self.fight.is_stunned(self.zarya))
            self.assertEqual(self.health(self.zarya), before)

        self.advance(1)
        self.assertFalse(gravitons.fields)
        self.assertTrue(self.bot.attack_input)
        self.until_pin()
        self.assertEqual(self.health(self.zarya), before)

    def test_graviton_cancels_existing_heavy_charge_windup(self):
        self.engage(wall=False)
        self.assertTrue(self.run.match.switch_hero(self.behind, heroes()[0x02E0000000000068]))
        self.behind.mover.position[:] = [4, 0, 4]
        self.behind.mover.body = None
        self.behind.body_script.set_ult_charge(2100)
        x, y, z = self.bot.position
        angles = aim(self.fight._eye(self.behind), (x, y + 1, z))
        self.run.step({self.behind: (*angles, 32)})
        instance = heavy_charge.instance_of(self.bot)
        windup, drive = instance.state(0), instance.state(3)
        self.assertTrue(windup.active)
        self.assertFalse(drive.active)
        first_windup, first_drive = windup.counter, drive.counter
        before = self.health(self.zarya)
        gravitons = self.fight.zarya_gravitons
        for _ in range(35):
            if self.bot.entity in gravitons.npc_movers:
                break
            self.advance(1)
        field, = gravitons.fields
        self.assertIn(self.bot.entity, gravitons.npc_movers)
        self.assertFalse(windup.active)
        cooldown = instance.vars[61]
        self.assertGreater(cooldown.value(), 0)
        while self.run.frame < field.end_tick:
            self.advance(1)
            self.assertFalse(windup.active)
            self.assertEqual(windup.counter, first_windup)
            self.assertEqual(drive.counter, first_drive)
            self.assertNotIn(self.bot.entity, self.fight.charge_contacts)
        self.assertFalse(gravitons.fields)
        self.assertTrue(self.bot.attack_input)
        self.assertGreater(cooldown.value(), 0)
        self.assertEqual(self.health(self.zarya), before)

    def test_cryo_cancels_heavy_charge_before_capture_and_keeps_cooldown(self):
        for driving in (False, True):
            for allied in (False, True):
                with self.subTest(driving=driving, allied=allied):
                    self.engage(wall=False)
                    mei = self.behind
                    self.zarya.mover.position[:] = (0, 0, 8)
                    self.zarya.mover.body = None
                    self.assertTrue(self.run.match.switch_hero(mei, heroes()[0x02E00000000000DD]))
                    mei.team = self.zarya.team if allied else self.bot.team
                    mei.mover.position[:] = (12, 0, 5)
                    mei.mover.body = None
                    instance = heavy_charge.instance_of(self.bot)
                    windup, drive = instance.state(0), instance.state(3)
                    active = drive if driving else windup
                    for _ in range(90):
                        self.advance(1)
                        if active.active:
                            break
                    self.assertTrue(active.active)
                    contact = self.fight.charge_contacts.get(self.bot.entity)
                    if driving:
                        self.assertIsNotNone(contact)
                        self.assertIsNone(contact.pinned)
                    else:
                        self.assertIsNone(contact)

                    mei.mover.position[:] = (0, 0, 5)
                    mei.mover.body = None
                    self.run.step({mei: (0, 0, 8)})
                    self.assertEqual(phase.protection(mei), "cryo")
                    position = self.bot.position
                    self.advance(1)
                    self.assertFalse(self.bot.attack_input)
                    self.assertFalse(windup.active)
                    self.assertFalse(drive.active)
                    self.assertNotIn(self.bot.entity, self.fight.charge_contacts)
                    self.assertEqual(self.bot.position, position)
                    self.assertEqual(self.health(self.zarya), 400)
                    self.assertFalse(self.fight.is_stunned(self.zarya))
                    counters = windup.counter, drive.counter
                    cooldown = instance.vars[61]
                    self.assertGreater(cooldown.value(), 13)
                    self.advance(26)
                    self.run.step({mei: (0, 0, 1)})
                    self.assertIsNone(phase.protection(mei))
                    self.advance(1)
                    self.assertTrue(self.bot.attack_input)
                    self.assertGreater(cooldown.value(), 12)
                    self.advance(40)
                    self.assertEqual((windup.counter, drive.counter), counters)
                    self.assertNotIn(self.bot.entity, self.fight.charge_contacts)
                    self.assertEqual(self.health(self.zarya), 400)
                    if not driving and allied:
                        mei.mover.position[:] = (12, 0, 5)
                        mei.mover.body = None
                        self.run.frame += int(cooldown.value() / combat.TICK) - 12
                        self.advance(1)
                        self.assertGreater(cooldown.value(), 0)
                        self.assertEqual((windup.counter, drive.counter), counters)
                        self.until_pin()
                        self.assertEqual((windup.counter, drive.counter), (counters[0] + 1, counters[1] + 1))

    def test_heavy_counter_against_reinhardt_windup_releases_zarya(self):
        self.engage(wall=False)
        self.until_pin()
        before = self.fight.vitals[self.bot.entity].total
        self.rein.team = self.zarya.team
        self.run.step({self.rein: (-30000, 0, 8)})
        self.rein.mover.position[:] = [1.2, 0, self.bot.position[2] + 2.5]
        self.rein.mover.body = None
        for _ in range(18):
            self.advance(1, {self.rein: (-30000, 0, 0)})
            if self.fight.is_stunned(self.rein) and self.fight.is_stunned(self.bot):
                break
        self.assertEqual(self.fight.stuns.get(self.rein.body, 0) - self.fight.tick, 125)
        self.assertEqual(self.fight.stuns.get(self.bot.entity, 0) - self.fight.tick, 125)
        self.assertEqual(self.health(self.rein), 500)
        self.assertEqual(self.fight.vitals[self.bot.entity].total, before)
        self.assertEqual(self.health(self.zarya), 400)
        self.assertFalse(self.fight.is_stunned(self.zarya))
        self.assertFalse(self.fight.charge_contacts)

    def test_removing_heavy_releases_live_capture(self):
        self.engage(wall=False)
        self.until_pin()
        self.assertIn(self.bot.entity, npc.remove_spawned(self.run.match))
        self.assertFalse(self.fight.is_stunned(self.zarya))
        self.assertFalse(self.fight.charge_contacts)
        self.advance(10)
        self.assertEqual(self.health(self.zarya), 400)
