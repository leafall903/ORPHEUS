"""Actual Jump Pack input and landing against Zarya's barriers."""

import math
import unittest

import test_game_zarya_npcs as fixtures
import test_server_snapshot as snapshot_fixtures
from test_game_combat import Run, aim
from test_game_mover import FLOOR, wall
from test_game_zarya_tesla import health, setup

from ow174.game import combat, npc, winston_jump, zarya
from ow174.game.collision import World
from ow174.game.commands import Command
from ow174.game.server_snapshot import snapshot_digest


def move(actor, position):
    actor.mover.position[:] = position
    actor.mover.velocity = (0, 0, 0)
    actor.mover.body = None


def near_landing(run, winston):
    run.step({winston: (0, -16384, 8)})
    for _ in range(500):
        run.step({})
        if winston.mover.velocity[1] < 0 and winston.mover.position[1] < 4:
            break


def resolve_impact(run, winston, landing):
    flights = run.match.combat.winston_jump.flights
    if not landing:
        run.step({winston: (0, -16384, 8)})
        if winston.body in flights:
            return
    else:
        for _ in range(40):
            run.step({})
            if winston.body not in flights and winston.mover.landed:
                return
    raise AssertionError("Jump Pack did not reach the requested impact")


def check_admission_geometry(test, body, offset, landing, edge):
    radius = 5 if landing else 4
    body_distance = math.hypot(offset, body.radius) - body.radius
    if edge:
        test.assertGreater(body_distance, radius)
        test.assertLess(math.hypot(offset, body.height / 2) - zarya.BUBBLE_RADIUS, radius)
        return 0
    test.assertLess(body_distance, radius)
    return 50 * (1 - body_distance / radius) if landing else 1


class ZaryaWinstonJumpTests(unittest.TestCase):
    def test_jump_pack_body_admission_precedes_personal_and_projected_interception(self):
        for landing in (False, True):
            for projected in (False, True):
                for case in ("edge", "inside", "wall", "ally", "exposed"):
                    with self.subTest(landing=landing, projected=projected, case=case):
                        run = Run(names=("Winston", "Caster", "Receiver"))
                        winston, caster, receiver = setup(run)
                        fight = run.match.combat
                        move(caster, (20, 1, 20))
                        move(receiver, (30, 1, 30))
                        if landing:
                            near_landing(run, winston)
                            self.assertIn(winston.body, fight.winston_jump.flights)
                        target = receiver if projected else caster
                        offset = (6 if landing else 5) if case == "edge" else 3
                        move(target, (0, 1, offset))
                        if projected:
                            move(caster, (-10, 1, offset))
                        if case == "ally":
                            caster.team = target.team = winston.team
                        if case != "exposed":
                            angles = aim(fight._eye(caster), fight._eye(target)) if projected else (0, 0)
                            run.step({caster: (*angles, 16 if projected else 8)})
                            self.assertTrue(fight.zarya_barriers.protects(target))
                            pools = (fight.zarya_barriers.projected if projected
                                     else fight.zarya_barriers.active)
                            pool = pools[caster.body]
                        if case == "wall":
                            run.match.collision_world = World(FLOOR + wall(-8, 1.4, 8, 1.4))
                        body = next(t for t in fight._targets() if t.owner is target)
                        amount = check_admission_geometry(self, body, offset, landing, case == "edge")
                        before = health(fight, target)
                        resolve_impact(run, winston, landing)
                        if case == "exposed":
                            self.assertAlmostEqual(before - health(fight, target), amount, places=4)
                            self.assertGreater(target.mover.velocity[1], 0)
                            self.assertEqual(len(winston.client.messages(combat.HIT)), 1)
                        else:
                            absorbed = amount if case == "inside" else 0
                            self.assertAlmostEqual(200 - pool.remaining, absorbed, places=4)
                            energy = caster.body_script.component.entity_var(87).value() or 0
                            self.assertAlmostEqual(energy, absorbed / 5, places=4)
                            self.assertEqual(health(fight, target), before)
                            self.assertEqual(target.mover.velocity, (0, 0, 0))
                            self.assertFalse(winston.client.messages(combat.HIT))

    def test_live_jump_launch_frame_is_causal_snapshot_state(self):
        fixture = snapshot_fixtures.ServerSnapshotTests()
        session, peers, players = fixture.scene()
        winston = players[0]
        match = winston.match
        peers[0].message(21616, snapshot_fixtures.pick(winston.entity, winston_jump.HERO), now=100.95)
        self.assertEqual(winston.hero.guid, winston_jump.HERO)
        match.bots.clear()
        match._pending_bot_scripts.clear()
        tick = match.tick + 1
        match.update(tick * combat.TICK, tick)
        winston.take_commands([Command(tick, pitch=-16384, buttons=8, extra=0)])
        flights = match.combat.winston_jump.flights
        instance, frame = flights[winston.body]
        before = snapshot_digest(fixture.capture(session))
        flights[winston.body] = instance, frame + 1
        self.assertNotEqual(snapshot_digest(fixture.capture(session)), before)
        flights[winston.body] = instance, frame
        self.assertEqual(snapshot_digest(fixture.capture(session)), before)

    def test_actual_landing_respects_personal_projected_and_breaking_barriers(self):
        for case in ("exposed", "personal", "projected", "personal_break", "projected_break"):
            with self.subTest(case=case):
                run = Run(names=("Winston", "Caster", "Receiver"))
                winston, caster, receiver = setup(run)
                fight = run.match.combat
                move(caster, (0, 1, 30))
                move(receiver, (30, 1, 30))
                run.step({winston: (0, -16384, 8)})
                instance = next(i for i in winston.body_script.component.instances.values()
                                if i.graph.index == 0x010A)
                self.assertGreater(instance.state(26).counter, 0)
                self.assertGreater(winston.mover.velocity[1], 10)
                for _ in range(500):
                    run.step({})
                    if winston.mover.velocity[1] < 0 and winston.mover.position[1] < 4:
                        break
                self.assertIn(winston.body, fight.winston_jump.flights)
                self.assertGreater(winston.mover.position[1], 1)
                target = receiver if case.startswith("projected") else caster
                move(target, (0, 1, 0))
                if case.startswith("projected"):
                    move(caster, (-5, 1, 0))
                    angles = aim(fight._eye(caster), fight._eye(target))
                    run.step({caster: (*angles, 16)})
                    move(caster, (-30, 1, 0))
                elif case.startswith("personal"):
                    run.step({caster: (0, 0, 8)})
                shielded = case != "exposed"
                if shielded:
                    self.assertTrue(fight.zarya_barriers.protects(target))
                    table = (fight.zarya_barriers.active if target is caster
                             else fight.zarya_barriers.projected)
                    pool = table[caster.body]
                    if case.endswith("break"):
                        pool.remaining = 10
                before = health(fight, target)
                for _ in range(40):
                    run.step({})
                    if winston.body not in fight.winston_jump.flights:
                        break
                self.assertNotIn(winston.body, fight.winston_jump.flights)
                self.assertTrue(winston.mover.landed)
                if shielded:
                    self.assertEqual(pool.remaining, 0 if case.endswith("break") else 150)
                    self.assertAlmostEqual(caster.body_script.component.entity_var(87).value(),
                                           2 if case.endswith("break") else 10)
                    self.assertEqual(health(fight, target), before)
                    self.assertEqual(target.mover.velocity, (0, 0, 0))
                    self.assertFalse(winston.client.messages(combat.HIT))
                else:
                    self.assertEqual(before - health(fight, target), 50)
                    self.assertGreater(target.mover.velocity[1], 0)
                    self.assertEqual(len(winston.client.messages(combat.HIT)), 1)
                after = health(fight, target)
                for _ in range(12):
                    run.step({})
                self.assertEqual(health(fight, target), after)

    def graviton_scene(self, offset):
        run = Run(names=("Winston", "Caster", "Anchor"))
        winston, caster, anchor = setup(run)
        fight = run.match.combat
        move(winston, (offset, 1, 0))
        move(caster, (0, 1, -6))
        move(anchor, (0, 1, 0))
        anchor.team = winston.team
        caster.body_script.set_ult_charge(2100)
        run.step({caster: (0, 0, 32)})
        for _ in range(80):
            run.step({})
            if fight.zarya_gravitons.fields:
                break
        self.assertEqual(len(fight.zarya_gravitons.fields), 1)
        return run, winston, fight

    def test_actual_graviton_cancels_jump_pack_before_landing(self):
        run, winston, fight = self.graviton_scene(8)
        self.assertIsNone(fight.zarya_gravitons.movement_for(winston))
        run.step({winston: (-16384, 0, 8)})
        self.assertIn(winston.body, fight.winston_jump.flights)
        instance, _ = fight.winston_jump.flights[winston.body]
        identity = fight.winston_jump.seen[winston.body]
        self.assertGreater(winston.mover.velocity[1], 0)
        for _ in range(20):
            run.step({winston: (-16384, 0, 8)})
            if winston.body in fight.zarya_gravitons.player_pulls:
                break
        self.assertIn(winston.body, fight.zarya_gravitons.player_pulls)
        self.assertNotIn(winston.body, fight.winston_jump.flights)
        self.assertFalse(instance.state(2).active)
        for _ in range(15):
            run.step({winston: (-16384, 0, 8)})
            self.assertNotIn(winston.body, fight.winston_jump.flights)
        self.assertEqual(fight.winston_jump.seen[winston.body], identity)
        self.assertGreater(instance.state(19).remaining(), 0)

    def test_graviton_blocks_new_jump_pack_without_spending_its_cooldown(self):
        run, winston, fight = self.graviton_scene(4)
        host = fight.zarya_gravitons
        field, = host.fields
        self.assertIsNotNone(host.movement_for(winston))
        instance = next(i for i in winston.body_script.component.instances.values()
                        if i.graph.index == winston_jump.GRAPH)
        held = {winston: (-16384, 0, 8)}
        while run.frame < field.end_tick - 1:
            run.step(held)
            self.assertNotIn(winston.body, fight.winston_jump.flights)
            self.assertEqual(instance.state(26).counter, 0)
            self.assertEqual(instance.state(19).remaining(), 0)
        run.step(held)
        self.assertFalse(host.fields)
        self.assertIn(winston.body, fight.winston_jump.flights)
        self.assertEqual(instance.state(26).counter, 1)
        self.assertGreater(instance.state(19).remaining(), 0)


@unittest.skipUnless(fixtures.NPC_DATA, "requires actual five-NPC graphs and movers")
class ZaryaWinstonJumpNpcTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.ZaryaFiveNpcTests.setUpClass()

    @classmethod
    def tearDownClass(cls):
        fixtures.ZaryaFiveNpcTests.tearDownClass()

    def test_jump_pack_projection_uses_each_npc_body_boundary_and_falloff(self):
        for definition in fixtures.ZaryaFiveNpcTests.catalog.values():
            for landing in (False, True):
                for edge in (False, True):
                    with self.subTest(npc=definition.name, landing=landing, edge=edge):
                        run = Run(names=("Winston", "Caster", "Observer"))
                        winston, caster, observer = setup(run)
                        fight = run.match.combat
                        move(caster, (20, 1, 20))
                        move(observer, (30, 1, 30))
                        if landing:
                            near_landing(run, winston)
                            self.assertIn(winston.body, fight.winston_jump.flights)
                        offset = (6 if landing else 5) if edge else 3
                        bot = npc.spawn(run.match, definition, (0, 1, offset), team=caster.team)
                        body = next(t for t in fight._targets() if t.owner is bot)
                        amount = check_admission_geometry(self, body, offset, landing, edge)
                        move(caster, (-10, 1, offset))
                        angles = aim(fight._eye(caster), (0, 1 + body.eye, offset))
                        run.step({caster: (*angles, 16)})
                        self.assertTrue(fight.zarya_barriers.protects(bot))
                        pool = fight.zarya_barriers.projected[caster.body]
                        before = health(fight, bot)
                        resolve_impact(run, winston, landing)
                        self.assertAlmostEqual(200 - pool.remaining, amount, places=4)
                        energy = caster.body_script.component.entity_var(87).value() or 0
                        self.assertAlmostEqual(energy, amount / 5, places=4)
                        self.assertEqual(health(fight, bot), before)
                        self.assertIsNone(bot.impulse_mover)
                        self.assertFalse(winston.client.messages(combat.HIT))

    def test_takeoff_releases_assassin_pin_only_after_body_damage(self):
        definition = next(d for d in fixtures.ZaryaFiveNpcTests.catalog.values() if d.name == "Assassin")
        for protected in (False, True):
            with self.subTest(protected=protected):
                run = Run(names=("Winston", "Victim", "Projector"))
                winston, victim, projector = setup(run)
                fight = run.match.combat
                victim.team = winston.team
                self.assertTrue(run.match.switch_hero(projector, victim.hero))
                move(victim, (0, 1, 3.5))
                move(projector, (-5, 1, 1))
                bot = npc.spawn(run.match, definition, (0, 1, 1), team=projector.team)
                bot.firing, bot.attack_target = True, victim.entity
                run.step({})
                self.assertEqual(fight.pins.get(bot.entity), victim.body)
                if protected:
                    angles = aim(fight._eye(projector), (0, 2, 1))
                    run.step({projector: (*angles, 16)})
                    self.assertTrue(fight.zarya_barriers.protects(bot))
                    pool = fight.zarya_barriers.projected[projector.body]
                    pool.remaining = 0.5
                before = health(fight, bot)
                run.step({winston: (0, -16384, 8)})
                self.assertIn(winston.body, fight.winston_jump.flights)
                self.assertEqual(fight.is_stunned(victim), protected)
                if protected:
                    self.assertEqual(pool.remaining, 0)
                    self.assertAlmostEqual(projector.body_script.component.entity_var(87).value(), 0.1)
                    self.assertEqual(health(fight, bot), before)
                    self.assertIsNone(bot.impulse_mover)
                    self.assertEqual(fight.pins, {bot.entity: victim.body})
                    self.assertFalse(winston.client.messages(combat.HIT))
                else:
                    self.assertEqual(health(fight, bot), before - 1)
                    self.assertGreater(bot.velocity[1], 0)
                    self.assertGreater(bot.velocity[2], 0)
                    self.assertNotIn(bot.entity, fight.pins)

    def test_landing_releases_only_an_admitted_assassin_pin(self):
        definition = next(d for d in fixtures.ZaryaFiveNpcTests.catalog.values() if d.name == "Assassin")
        for case in ("exposed", "breaking", "lethal", "overlap"):
            with self.subTest(case=case):
                run = Run(names=("Winston", "Victim", "Projector"))
                winston, victim, projector = setup(run)
                fight = run.match.combat
                for actor in run.players:
                    actor.client = fixtures.NpcClient(actor)
                victim.team = winston.team
                self.assertTrue(run.match.switch_hero(projector, victim.hero))
                move(victim, (0, 1, 2.9))
                move(projector, (-5, 1, 0))
                near_landing(run, winston)
                self.assertIn(winston.body, fight.winston_jump.flights)
                bot = npc.spawn(run.match, definition, (0, 1, 0), team=projector.team)
                bot.firing, bot.attack_target = True, victim.entity
                if case == "overlap":
                    other = npc.spawn(run.match, definition, (0, 1, 5.8), team=bot.team)
                    other.firing, other.attack_target = True, victim.entity
                run.step({})
                self.assertEqual(fight.pins.get(bot.entity), victim.body)
                if case == "overlap":
                    self.assertEqual(fight.pins.get(other.entity), victim.body)
                    other_health = health(fight, other)
                vitals = fight.vitals[bot.entity]
                if case == "breaking":
                    angles = aim(fight._eye(projector), (0, 2, 0))
                    run.step({projector: (*angles, 16)})
                    self.assertTrue(fight.zarya_barriers.protects(bot))
                    pool = fight.zarya_barriers.projected[projector.body]
                    pool.remaining = 10
                    move(projector, (-30, 1, 0))
                elif case == "lethal":
                    vitals.now = dict.fromkeys(vitals.now, 0.0)
                    vitals.now[combat.HEALTH_POOL] = 10
                before = vitals.total
                for _ in range(40):
                    run.step({})
                    if winston.body not in fight.winston_jump.flights:
                        break
                self.assertNotIn(winston.body, fight.winston_jump.flights)
                self.assertTrue(winston.mover.landed)
                if case == "breaking":
                    self.assertEqual(pool.remaining, 0)
                    self.assertAlmostEqual(projector.body_script.component.entity_var(87).value(), 2)
                    self.assertEqual(vitals.total, before)
                    self.assertIsNone(bot.impulse_mover)
                    self.assertEqual(fight.pins, {bot.entity: victim.body})
                    self.assertTrue(fight.is_stunned(victim))
                    self.assertFalse(winston.client.messages(combat.HIT))
                    continue

                self.assertEqual(before - vitals.total, min(before, 50))
                self.assertNotIn(bot.entity, fight.pins)
                if case == "lethal":
                    self.assertTrue(bot.dead)
                    self.assertEqual(vitals.killer, winston.body)
                    self.assertEqual([entity for entity, _ in winston.client.deaths], [bot.entity])
                    self.assertIsNone(bot.impulse_mover)
                else:
                    self.assertGreater(bot.velocity[1], 0)
                if case == "overlap":
                    self.assertEqual(health(fight, other), other_health)
                    self.assertEqual(fight.pins, {other.entity: victim.body})
                    self.assertTrue(fight.is_stunned(victim))
                    continue
                self.assertFalse(fight.is_stunned(victim))
                after = health(fight, victim)
                run.step({victim: (0, 0, 8)})
                self.assertTrue(fight.zarya_barriers.protects(victim))
                for _ in range(20):
                    run.step({})
                self.assertEqual(health(fight, victim), after)
                self.assertNotIn(bot.entity, fight.pins)

    def test_landing_breaks_projection_without_damage_or_impulse_for_each_actual_npc(self):
        for definition in fixtures.ZaryaFiveNpcTests.catalog.values():
            with self.subTest(npc=definition.name):
                run = Run(names=("Winston", "Caster", "Observer"))
                winston, caster, observer = setup(run)
                fight = run.match.combat
                move(caster, (-5, 1, 0))
                move(observer, (30, 1, 30))
                near_landing(run, winston)
                self.assertIn(winston.body, fight.winston_jump.flights)
                bot = npc.spawn(run.match, definition, (0, 1, 0), team=caster.team)
                target = next(t for t in fight._targets() if t.owner is bot)
                angles = aim(fight._eye(caster), (0, 1 + target.eye, 0))
                run.step({caster: (*angles, 16)})
                self.assertTrue(fight.zarya_barriers.protects(bot))
                pool = fight.zarya_barriers.projected[caster.body]
                self.assertEqual(pool.recipient, bot.entity)
                pool.remaining = 10
                move(caster, (-30, 1, 0))
                before = health(fight, bot)
                for _ in range(40):
                    run.step({})
                    if winston.body not in fight.winston_jump.flights:
                        break
                self.assertNotIn(winston.body, fight.winston_jump.flights)
                self.assertEqual(pool.remaining, 0)
                self.assertAlmostEqual(caster.body_script.component.entity_var(87).value(), 2)
                self.assertEqual(health(fight, bot), before)
                self.assertIsNone(bot.impulse_mover)
                self.assertFalse(winston.client.messages(combat.HIT))
