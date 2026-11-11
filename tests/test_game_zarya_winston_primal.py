"""Actual Primal Rage casts and fist contacts against Zarya barriers."""

import unittest

import test_game_zarya_npcs as fixtures
import test_server_snapshot as snapshot_fixtures
from test_game_combat import Run, aim
from test_game_mover import FLOOR, wall
from test_game_zarya_tesla import health, setup
from test_game_zarya_winston_jump import move

from ow174.game import combat, npc, winston_jump, winston_primal
from ow174.game.collision import World
from ow174.game.commands import FIRE, Command
from ow174.game.content import heroes
from ow174.game.script import expr
from ow174.game.script.driver import ULT_COST
from ow174.game.server_snapshot import snapshot_digest
from ow174.game.world import ARMOUR_POOL, HEALTH_POOL

EXPIRY_FRAME = 1625  # Actual 0109 exit with posed input for the cast below.


def rage(run, winston):
    script = winston.body_script
    script.set_ult_charge(script.component.entity_var(ULT_COST).value())
    run.step({winston: (0, 0, 32)})
    for _ in range(35):
        run.step({})


def punch(run, winston, target, pool=None):
    fight = run.match.combat
    before = health(fight, target) if pool is None else pool.remaining
    for _ in range(60):
        run.step({winston: (0, 0, FIRE)})
        after = health(fight, target) if pool is None else pool.remaining
        if after < before:
            return
    raise AssertionError("Primal punch never reached target")


def swing(run, winston, yaw=0):
    seen = run.match.combat.winston_primal.seen
    before = dict(seen)
    for _ in range(60):
        run.step({winston: (yaw, 0, FIRE)})
        if seen != before:
            return
    raise AssertionError("Primal fist never reached an authored damage state")


class ZaryaPrimalTests(unittest.TestCase):
    def test_primal_resets_jump_cooldown_without_launching_or_replaying_an_old_press(self):
        run = Run(names=("Winston", "Caster", "Receiver"))
        winston, caster, receiver = setup(run)
        fight = run.match.combat
        move(caster, (20, 1, 20))
        move(receiver, (30, 1, 30))
        press = {winston: (0, -16384, 8)}
        run.step(press)
        jump, _ = fight.winston_jump.flights[winston.body]
        cooldown = jump.state(19)
        self.assertAlmostEqual(cooldown.remaining(), 5.985, places=4)
        first = fight.winston_jump.seen[winston.body]
        for _ in range(100):
            run.step({})
        run.step(press)
        run.step({})
        self.assertGreater(cooldown.remaining(), 4)
        self.assertEqual(fight.winston_jump.seen[winston.body], first)

        rage(run, winston)
        self.assertIsNotNone(winston_primal.health_state(winston.body_script))
        self.assertEqual(jump.vars[472].value(), 2)
        self.assertEqual(cooldown.remaining(), 0)
        self.assertEqual(fight.winston_jump.seen[winston.body], first)
        run.step(press)
        self.assertAlmostEqual(cooldown.remaining(), 1.985, places=4)
        self.assertGreater(winston.mover.velocity[1], 20)
        second = fight.winston_jump.seen[winston.body]
        self.assertNotEqual(second, first)
        run.step({})
        run.step(press)
        self.assertEqual(fight.winston_jump.seen[winston.body], second)
        for _ in range(126):
            run.step({})
        self.assertEqual(cooldown.remaining(), 0)
        self.assertEqual(fight.winston_jump.seen[winston.body], second)
        self.assertLess(winston.mover.velocity[1], 0)
        run.step(press)
        self.assertGreater(winston.mover.velocity[1], 20)
        self.assertNotEqual(fight.winston_jump.seen[winston.body], second)

    def test_primal_exit_resets_jump_and_held_input_waits_for_exit_gate(self):
        run = Run(names=("Winston", "Caster", "Receiver"))
        winston, caster, receiver = setup(run)
        fight = run.match.combat
        move(caster, (20, 1, 20))
        move(receiver, (30, 1, 30))
        rage(run, winston)
        run.frame = EXPIRY_FRAME - 49
        press = {winston: (0, -16384, 8)}
        run.step(press)
        jump, _ = fight.winston_jump.flights[winston.body]
        cooldown = jump.state(19)
        self.assertAlmostEqual(cooldown.remaining(), 1.985, places=4)
        first = fight.winston_jump.seen[winston.body]
        run.step({})
        while run.frame < EXPIRY_FRAME - 1:
            run.step(press)
            self.assertEqual(fight.winston_jump.seen[winston.body], first)
        self.assertAlmostEqual(cooldown.remaining(), 1.233, places=4)
        run.step(press)
        self.assertIsNone(winston_primal.health_state(winston.body_script))
        self.assertEqual(jump.vars[472].value(), 6)
        self.assertEqual(cooldown.remaining(), 0)
        self.assertEqual(fight.winston_jump.seen[winston.body], first)

        while run.frame < EXPIRY_FRAME + 21:
            run.step(press)
            self.assertEqual(fight.winston_jump.seen[winston.body], first)
        run.step(press)
        second = fight.winston_jump.seen[winston.body]
        self.assertNotEqual(second, first)
        self.assertGreater(winston.mover.velocity[1], 20)
        self.assertAlmostEqual(cooldown.remaining(), 5.998, places=4)
        for _ in range(10):
            run.step(press)
            self.assertEqual(fight.winston_jump.seen[winston.body], second)

    def test_graviton_blocks_a_ready_primal_jump_until_field_expiry(self):
        run = Run(names=("Winston", "Caster", "Anchor"))
        winston, caster, anchor = setup(run)
        fight = run.match.combat
        move(winston, (4, 1, 0))
        move(caster, (0, 1, -6))
        move(anchor, (0, 1, 0))
        anchor.team = winston.team
        rage(run, winston)
        caster.body_script.set_ult_charge(2100)
        run.step({caster: (0, 0, 32)})
        for _ in range(80):
            run.step({})
            if fight.zarya_gravitons.fields:
                break
        field, = fight.zarya_gravitons.fields
        self.assertIsNotNone(fight.zarya_gravitons.movement_for(winston))
        self.assertIsNotNone(winston_primal.health_state(winston.body_script))
        jump = next(i for i in winston.body_script.component.instances.values()
                    if i.graph.index == winston_jump.GRAPH)
        press = {winston: (0, -16384, 8)}
        while run.frame < field.end_tick - 1:
            run.step(press)
            self.assertNotIn(winston.body, fight.winston_jump.flights)
            self.assertEqual(jump.state(26).counter, 0)
            self.assertEqual(jump.state(19).remaining(), 0)
        run.step(press)
        self.assertFalse(fight.zarya_gravitons.fields)
        self.assertIn(winston.body, fight.winston_jump.flights)
        self.assertEqual(jump.state(26).counter, 1)
        self.assertAlmostEqual(jump.state(19).remaining(), 1.985, places=4)

    def test_breaking_projection_preserves_primal_windup_against_actual_flashbang(self):
        for protected in (False, True):
            with self.subTest(protected=protected):
                run = Run(names=("Winston", "Caster", "Cassidy"))
                winston, caster, cassidy = setup(run)
                run.match.switch_hero(cassidy, heroes()[0x02E0000000000042])
                fight = run.match.combat
                caster.team = winston.team
                move(caster, (4, 1, 0))
                move(cassidy, (0, 1, 1.5))
                rage(run, winston)
                if protected:
                    angles = aim(fight._eye(caster), fight._eye(winston))
                    run.step({caster: (*angles, 16)})
                    self.assertTrue(fight.zarya_barriers.protects(winston))
                    pool = fight.zarya_barriers.projected[caster.body]
                    pool.remaining = 10

                instance = winston_primal.weapon(winston.body_script)
                before = health(fight, cassidy)
                held = {winston: (0, 0, FIRE)}
                run.step({**held, cassidy: (-32768, 0, 16)})
                windup = instance.state(21)
                self.assertTrue(windup.active)
                for _ in range(8):
                    run.step(held)
                    if (pool.remaining == 0 if protected else fight.is_stunned(winston)):
                        break
                self.assertEqual(health(fight, cassidy), before)
                self.assertIsNotNone(winston_primal.health_state(winston.body_script))
                self.assertEqual(windup.active, protected)
                if protected:
                    self.assertEqual(pool.remaining, 0)
                    self.assertFalse(fight.is_stunned(winston))
                    self.assertEqual(health(fight, winston), 1000)
                    self.assertAlmostEqual(caster.body_script.component.entity_var(87).value(), 2)
                else:
                    self.assertTrue(fight.is_stunned(winston))
                    self.assertEqual(health(fight, winston), 980)
                    for _ in range(80):
                        run.step({})
                        if not fight.is_stunned(winston):
                            break
                    self.assertFalse(fight.is_stunned(winston))
                    self.assertEqual(health(fight, cassidy), before)
                    self.assertFalse(winston.client.messages(combat.HIT))
                punch(run, winston, cassidy)
                self.assertEqual(health(fight, cassidy), before - 40)

    def test_actual_primal_punch_respects_barrier_edge_range_cover_and_team(self):
        for projected in (False, True):
            for case in ("edge", "break", "outside", "wall", "ally", "exposed"):
                with self.subTest(projected=projected, case=case):
                    run = Run(names=("Winston", "Caster", "Receiver"))
                    winston, caster, receiver = setup(run)
                    fight = run.match.combat
                    target = receiver if projected else caster
                    distance = 4.3 if case == "outside" else 3.0
                    move(caster, (-5, 1, distance) if projected else (0, 1, distance))
                    move(receiver, (0, 1, distance) if projected else (30, 1, 30))
                    rage(run, winston)
                    body = next(t for t in fight._targets() if t.owner is target)
                    # Both authored 00FE fist queries reach 1.5m forward with a 0.5m radius.
                    self.assertGreater(distance, 1.5 + 0.5 + body.radius)
                    before = health(fight, target)
                    capacity = 10 if case == "break" else 200
                    if case != "exposed":
                        angles = aim(fight._eye(caster), fight._eye(target)) if projected else (0, 0)
                        run.step({caster: (*angles, 16 if projected else 8)})
                        self.assertTrue(fight.zarya_barriers.protects(target))
                        pools = fight.zarya_barriers.projected if projected else fight.zarya_barriers.active
                        pool = pools[caster.body]
                        pool.remaining = capacity
                    if case == "wall":
                        run.match.collision_world = World(FLOOR + wall(-8, 1.1, 8, 1.1))
                    if case == "ally":
                        winston.team = target.team
                    swing(run, winston)
                    absorbed = {"edge": 40, "break": 10}.get(case, 0)
                    if case != "exposed":
                        self.assertEqual(pool.remaining, capacity - absorbed)
                    energy = expr.to_float(caster.body_script.component.entity_var(87).value())
                    self.assertAlmostEqual(energy, absorbed / 5)
                    self.assertEqual(health(fight, target), before)
                    self.assertEqual(target.mover.velocity, (0, 0, 0))
                    self.assertFalse(winston.client.messages(combat.HIT))
                    if case == "break":
                        move(target, (0, 1, 1.5))
                        punch(run, winston, target)
                        self.assertEqual(health(fight, target), before - 40)

    def test_lethal_zarya_grenade_retires_primal_before_winston_respawns(self):
        run = Run(names=("Winston", "Caster", "Receiver"))
        winston, caster, receiver = setup(run)
        fight = run.match.combat
        move(caster, (0, 1, 8))
        move(receiver, (30, 1, 30))
        rage(run, winston)
        old_body = winston.body
        script = winston.body_script
        target = next(t for t in fight._targets() if t.owner is winston)
        vitals = fight.vitals_of(target)
        vitals.now[HEALTH_POOL] = 40
        vitals.now[ARMOUR_POOL] = 0
        self.assertIn(old_body, fight.winston_primal.expiries)

        angles = aim(fight._eye(caster), fight._eye(winston))
        run.step({caster: (*angles, 2)})
        self.assertEqual(len(fight.rockets), 1)
        for _ in range(25):
            run.step({})
            if fight.is_dead(winston):
                break
        self.assertTrue(fight.is_dead(winston))
        self.assertEqual(vitals.maximum, 500)
        self.assertNotIn(old_body, fight.winston_primal.expiries)
        self.assertNotIn(old_body, fight.winston_primal.bonus)

        run.frame = EXPIRY_FRAME + 500
        run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        self.assertNotEqual(winston.body, old_body)
        self.assertTrue(script.retired)
        self.assertEqual(health(fight, winston), 500)
        self.assertIsNone(winston_primal.health_state(winston.body_script))

    def test_live_primal_deadline_is_causal_snapshot_state(self):
        fixture = snapshot_fixtures.ServerSnapshotTests()
        session, peers, players = fixture.scene()
        winston = players[0]
        match = winston.match
        peers[0].message(21616, snapshot_fixtures.pick(winston.entity, winston_jump.HERO), now=100.95)
        match.bots.clear()
        match._pending_bot_scripts.clear()
        script = winston.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        tick = match.tick + 1
        match.update(tick * combat.TICK, tick)
        winston.take_commands([Command(tick, buttons=32, extra=0)])
        expiry = match.combat.winston_primal.expiries[winston.body]
        before = snapshot_digest(fixture.capture(session))
        expiry.tick += 1
        self.assertNotEqual(snapshot_digest(fixture.capture(session)), before)
        expiry.tick -= 1
        self.assertEqual(snapshot_digest(fixture.capture(session)), before)

    def test_idle_primal_expires_on_paced_and_sparse_world_updates(self):
        for sparse in (False, True):
            with self.subTest(sparse=sparse):
                run = Run(names=("Winston", "Caster", "Receiver"))
                winston, caster, receiver = setup(run)
                fight = run.match.combat
                move(caster, (0, 1, 1.5))
                receiver.team = winston.team
                rage(run, winston)
                script = winston.body_script
                last_input = script.frame
                ticks = (EXPIRY_FRAME,) if sparse else range(last_input + 1, EXPIRY_FRAME + 1)
                for tick in ticks:
                    run.match.update(tick * combat.TICK, tick, frames=False)
                self.assertEqual(script.frame, last_input)
                self.assertEqual(health(fight, winston), 1000)

                run.frame = EXPIRY_FRAME + (20 if sparse else 1)
                run.match.update(run.frame * combat.TICK, run.frame, frames=False)
                self.assertIsNone(winston_primal.health_state(script))
                self.assertEqual(script.frame, EXPIRY_FRAME)
                self.assertEqual(health(fight, winston), 500)
                self.assertNotIn(winston.body, fight.winston_primal.expiries)
                before = health(fight, caster)
                punch(run, winston, caster)
                self.assertAlmostEqual(before - health(fight, caster), 0.96)

    def test_inflight_zarya_grenade_keeps_order_across_idle_primal_expiry(self):
        for before_expiry in (False, True):
            for sparse in (False, True):
                with self.subTest(before_expiry=before_expiry, sparse=sparse):
                    run = Run(names=("Winston", "Caster", "Receiver"))
                    winston, caster, receiver = setup(run)
                    fight = run.match.combat
                    move(caster, (0, 1, 8))
                    move(receiver, (30, 1, 30))
                    rage(run, winston)
                    target = next(t for t in fight._targets() if t.owner is winston)
                    vitals = fight.vitals_of(target)
                    vitals.now[HEALTH_POOL] = 360
                    vitals.now[ARMOUR_POOL] = 0
                    launch = EXPIRY_FRAME - (26 if before_expiry else 4)
                    run.match.update(launch * combat.TICK, launch, frames=False)
                    yaw, pitch = aim(fight._eye(caster), fight._eye(winston))
                    caster.take_commands([Command(launch, yaw=yaw, pitch=pitch, buttons=2, extra=0)])
                    self.assertEqual(len(fight.rockets), 1)
                    run.match.update((launch + 1) * combat.TICK, launch + 1, frames=False)
                    self.assertEqual(len(fight.rockets), 1)
                    end = EXPIRY_FRAME + 25
                    ticks = (end,) if sparse else range(launch + 2, end + 1)
                    for tick in ticks:
                        run.match.update(tick * combat.TICK, tick, frames=False)
                    self.assertFalse(fight.rockets)
                    self.assertIsNone(winston_primal.health_state(winston.body_script))
                    self.assertAlmostEqual(health(fight, winston), 313 if before_expiry else 303)

    def test_primal_pool_expires_then_normal_tesla_resumes(self):
        run = Run(names=("Winston", "Caster", "Receiver"))
        winston, caster, receiver = setup(run)
        fight = run.match.combat
        move(caster, (0, 1, 1.5))
        receiver.team = winston.team
        rage(run, winston)
        self.assertEqual(health(fight, winston), 1000)
        for _ in range(660):
            run.step({})
            if winston_primal.health_state(winston.body_script) is None:
                break
        self.assertIsNone(winston_primal.health_state(winston.body_script))
        self.assertEqual(run.frame, EXPIRY_FRAME)
        self.assertEqual(health(fight, winston), 500)
        self.assertNotIn(winston.body, fight.winston_primal.bonus)
        before = health(fight, caster)
        punch(run, winston, caster)
        self.assertAlmostEqual(before - health(fight, caster), 0.96)

    def test_actual_primal_punch_damage_and_impulse_respect_each_barrier(self):
        for case in ("exposed", "personal", "projected", "personal_break", "projected_break"):
            with self.subTest(case=case):
                run = Run(names=("Winston", "Caster", "Receiver"))
                winston, caster, receiver = setup(run)
                fight = run.match.combat
                move(caster, (-5, 1, 0) if case.startswith("projected") else (0, 1, 1.5))
                move(receiver, (0, 1, 1.5) if case.startswith("projected") else (30, 1, 30))
                rage(run, winston)
                self.assertIsNotNone(winston_primal.health_state(winston.body_script))
                self.assertEqual(health(fight, winston), 1000)
                target = receiver if case.startswith("projected") else caster
                shielded = case != "exposed"
                breaking = case.endswith("break")
                pool = None
                if shielded:
                    angles = aim(fight._eye(caster), fight._eye(target)) if target is receiver else (0, 0)
                    run.step({caster: (*angles, 16 if target is receiver else 8)})
                    self.assertTrue(fight.zarya_barriers.protects(target))
                    table = (fight.zarya_barriers.projected if target is receiver
                             else fight.zarya_barriers.active)
                    pool = table[caster.body]
                    pool.remaining = capacity = 10 if breaking else 200
                before = health(fight, target)
                punch(run, winston, target, pool)
                if shielded:
                    self.assertEqual(pool.remaining, max(0, capacity - 40))
                    self.assertAlmostEqual(caster.body_script.component.entity_var(87).value(),
                                           2 if breaking else 8)
                    self.assertEqual(health(fight, target), before)
                    self.assertEqual(target.mover.velocity, (0, 0, 0))
                    self.assertFalse(winston.client.messages(combat.HIT))
                else:
                    self.assertEqual(before - health(fight, target), 40)
                    self.assertGreater(target.mover.velocity[2], 0)
                    self.assertGreater(target.mover.velocity[1], 0)
                if breaking:
                    fight.command(winston, Command(run.frame, buttons=FIRE))
                    self.assertEqual(health(fight, target), before)
                    punch(run, winston, target)
                    self.assertEqual(before - health(fight, target), 40)
                    self.assertGreater(target.mover.velocity[2], 0)


@unittest.skipUnless(fixtures.NPC_DATA, "requires actual five-NPC graphs and movers")
class ZaryaPrimalNpcTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.ZaryaFiveNpcTests.setUpClass()

    @classmethod
    def tearDownClass(cls):
        fixtures.ZaryaFiveNpcTests.tearDownClass()

    def test_actual_primal_punch_releases_only_an_admitted_assassin_pin(self):
        definition = next(d for d in fixtures.ZaryaFiveNpcTests.catalog.values() if d.name == "Assassin")
        for case in ("nonlethal", "breaking_barrier", "lethal", "overlap"):
            with self.subTest(case=case):
                run = Run(names=("Winston", "Victim", "Projector"))
                winston, victim, projector = setup(run)
                match = run.match
                fight = match.combat
                for actor in run.players:
                    actor.client = fixtures.NpcClient(actor)
                victim.team = winston.team
                match.switch_hero(projector, victim.hero)
                move(winston, (1.5, 1, 2.5))
                move(victim, (0, 1, 0))
                move(projector, (-5, 1, 2.5))
                rage(run, winston)
                bot = npc.spawn(match, definition, (0, 1, 2.5), team=projector.team)
                bot.firing, bot.attack_target = True, victim.entity
                if case == "overlap":
                    other = npc.spawn(match, definition, (0, 1, -2.5), team=bot.team)
                    other.firing, other.attack_target = True, victim.entity
                run.step({})
                self.assertEqual(fight.pins.get(bot.entity), victim.body)
                if case == "overlap":
                    self.assertEqual(fight.pins.get(other.entity), victim.body)
                vitals = fight.vitals[bot.entity]
                if case == "breaking_barrier":
                    run.step({projector: (*aim(fight._eye(projector), (0, 2, 2.5)), 16)})
                    self.assertTrue(fight.zarya_barriers.protects(bot))
                    pool = fight.zarya_barriers.projected[projector.body]
                    pool.remaining = 10
                elif case == "lethal":
                    vitals.now = dict.fromkeys(vitals.now, 0.0)
                    vitals.now[HEALTH_POOL] = 10
                before = vitals.total
                swing(run, winston, -16384)
                if case == "breaking_barrier":
                    self.assertEqual(pool.remaining, 0)
                    self.assertAlmostEqual(projector.body_script.component.entity_var(87).value(), 2)
                    self.assertEqual(vitals.total, before)
                    self.assertIsNone(bot.impulse_mover)
                    self.assertEqual(fight.pins, {bot.entity: victim.body})
                    self.assertTrue(fight.is_stunned(victim))
                    self.assertFalse(winston.client.messages(combat.HIT))
                    continue

                self.assertEqual(before - vitals.total, min(40, before))
                self.assertEqual(bot.dead, case == "lethal")
                self.assertNotIn(bot.entity, fight.pins)
                if case == "lethal":
                    self.assertEqual(vitals.killer, winston.body)
                    self.assertIsNone(bot.impulse_mover)
                    self.assertEqual([entity for entity, _ in winston.client.deaths], [bot.entity])
                else:
                    self.assertIsNotNone(bot.impulse_mover)
                    self.assertLess(bot.velocity[0], 0)
                if case == "overlap":
                    self.assertEqual(fight.pins, {other.entity: victim.body})
                    self.assertTrue(fight.is_stunned(victim))
                    continue
                self.assertFalse(fight.is_stunned(victim))
                victim_health = health(fight, victim)
                run.step({victim: (0, 0, 8)})
                self.assertTrue(fight.zarya_barriers.protects(victim))
                for _ in range(20):
                    run.step({})
                self.assertEqual(health(fight, victim), victim_health)
                self.assertNotIn(bot.entity, fight.pins)
                self.assertEqual(fight.zarya_barriers.active[victim.body].remaining, 200)

    def test_actual_primal_punch_hits_projected_edge_outside_each_npc(self):
        for definition in fixtures.ZaryaFiveNpcTests.catalog.values():
            with self.subTest(npc=definition.name):
                run = Run(names=("Winston", "Caster", "Observer"))
                winston, caster, observer = setup(run)
                fight = run.match.combat
                move(caster, (5, 1, 3.2))
                move(observer, (30, 1, 30))
                rage(run, winston)
                bot = npc.spawn(run.match, definition, (0, 1, 3.2), team=caster.team)
                target = next(t for t in fight._targets() if t.owner is bot)
                self.assertGreater(target.feet[2], 1.5 + 0.5 + target.radius)
                angles = aim(fight._eye(caster), (0, 1 + target.eye, 3.2))
                run.step({caster: (*angles, 16)})
                self.assertTrue(fight.zarya_barriers.protects(bot))
                pool = fight.zarya_barriers.projected[caster.body]
                before = health(fight, bot)
                swing(run, winston)
                self.assertEqual(pool.remaining, 160)
                self.assertAlmostEqual(caster.body_script.component.entity_var(87).value(), 8)
                self.assertEqual(health(fight, bot), before)
                self.assertIsNone(bot.impulse_mover)
                self.assertFalse(winston.client.messages(combat.HIT))

    def test_breaking_projection_blocks_primal_impulse_on_each_actual_npc(self):
        for definition in fixtures.ZaryaFiveNpcTests.catalog.values():
            with self.subTest(npc=definition.name):
                run = Run(names=("Winston", "Caster", "Observer"))
                winston, caster, observer = setup(run)
                fight = run.match.combat
                move(caster, (5, 1, 1.5))
                move(observer, (30, 1, 30))
                rage(run, winston)
                bot = npc.spawn(run.match, definition, (0, 1, 1.5), team=caster.team)
                target = next(t for t in fight._targets() if t.owner is bot)
                angles = aim(fight._eye(caster), (0, 1 + target.eye, 1.5))
                run.step({caster: (*angles, 16)})
                self.assertTrue(fight.zarya_barriers.protects(bot))
                pool = fight.zarya_barriers.projected[caster.body]
                pool.remaining = 10
                before = health(fight, bot)
                punch(run, winston, bot, pool)
                self.assertEqual(health(fight, bot), before)
                self.assertIsNone(bot.impulse_mover)
                self.assertAlmostEqual(caster.body_script.component.entity_var(87).value(), 2)
                self.assertFalse(winston.client.messages(combat.HIT))
