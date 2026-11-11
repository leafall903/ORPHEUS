"""Actual13EB input and finite pellets; manual controller/geometry are reconstructed."""

import math
import unittest
from unittest.mock import patch

import test_game_zarya_npcs as fixtures
from test_game_combat import aim

from ow174.game import combat, npc, zarya
from ow174.game.collision import World
from ow174.game.content import heroes
from ow174.game.script.driver import OWNER_HEALTH


@unittest.skipUnless(fixtures.NPC_DATA, "requires actual NPC bundle and movers")
class EnforcerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.ZaryaFiveNpcTests.setUpClass()

    @classmethod
    def tearDownClass(cls):
        fixtures.ZaryaFiveNpcTests.tearDownClass()

    def encounter(self, z=5, team=1):
        fixture = fixtures.ZaryaFiveNpcTests()
        definition = next(d for d in fixture.catalog.values() if d.name == "Enforcer")
        run, player, bot = fixture.encounter(definition, z=z, team=team)
        bot.yaw = -32768
        bot.firing = True
        bot.attack_target = player.entity
        target = next(t for t in run.match.combat._targets() if t.owner is player)
        return run, player, bot, target

    def emit(self, run, bot):
        for _ in range(90):
            run.step({})
            if run.match.combat.rockets:
                return tuple(run.match.combat.rockets)
        self.fail("actual Enforcer input did not emit its volley")

    def drain(self, run, frames=15):
        for _ in range(frames):
            run.step({})

    def test_real_input_emits_25_distinct_pellets_and_consumes_one_ammo(self):
        run, _, bot, _ = self.encounter()
        self.assertEqual(bot.body_script.component.entity_var(OWNER_HEALTH).value(), 350)
        pellets = self.emit(run, bot)
        self.assertEqual(len(pellets), 25)
        self.assertEqual(len({r.direction for r in pellets}), 25)
        self.assertTrue(all(r.direct.amount == 2 and r.speed == 80 and r.left == 1.25
                            and r.definition == 0x04000000000023F0 for r in pellets))
        instance = next(i for i in bot.body_script.component.instances.values() if i.graph.index == 0x13EB)
        self.assertEqual(instance.vars[53].value(), 3)

    def test_all_25_close_pellets_deal_50_to_zarya(self):
        run, _, bot, target = self.encounter()
        self.emit(run, bot)
        self.drain(run)
        self.assertEqual(run.match.combat.vitals_of(target).total, 350)

    def test_armour_mitigates_each_two_damage_pellet(self):
        run, _, bot, target = self.encounter()
        vitals = combat.Vitals(target.entity, 200, armour=200)
        run.match.combat.vitals[target.entity] = vitals
        self.emit(run, bot)
        self.drain(run)
        self.assertEqual(vitals.total, 375)

    def test_flight_applies_authored_falloff_to_each_landed_pellet(self):
        run, _, bot, target = self.encounter(z=10)
        pellets = self.emit(run, bot)
        a, b = target.segment()
        expected = 0
        count = 0
        for pellet in pellets:
            distance = combat.ray_capsule(pellet.position, pellet.direction, a, b, target.radius)
            if distance is not None:
                count += 1
                expected += 2 * (1 - (1 - 0.20000000298023224) * (distance - 8) / 7)
        self.assertEqual(count, 14)
        self.drain(run)
        self.assertAlmostEqual(400 - run.match.combat.vitals_of(target).total, expected)
        self.assertTrue(all(math.isfinite(r.distance) and r.distance > 0 for r in pellets))

    def test_personal_barrier_blocks_25_pellets_and_credits_ten_energy(self):
        run, player, bot, target = self.encounter()
        self.emit(run, bot)
        run.step({player: (0, 0, 8)})
        self.drain(run)
        self.assertEqual(run.match.combat.vitals_of(target).total, 400)
        self.assertAlmostEqual(player.body_script.component.entity_var(87).value(), 10, delta=0.00001)

    def test_breaking_pellet_does_not_spill_but_later_pellets_continue(self):
        for overlap in (False, True):
            with self.subTest(overlap=overlap):
                run = fixtures.Run(names=("Target", "Projector"))
                target, caster = run.players
                fight = run.match.combat
                run.match.bots.clear()
                run.match._pending_bot_scripts.clear()
                for player, point in ((target, [0, 1, 0]), (caster, [4, 1, 0])):
                    player.client = fixtures.NpcClient(player)
                    self.assertTrue(run.match.switch_hero(player, heroes()[fixtures.ZARYA]))
                    player.team = 0
                    player.mover.position[:] = point
                    player.mover.body = None
                catalog = fixtures.ZaryaFiveNpcTests.catalog
                definition = next(d for d in catalog.values() if d.name == "Enforcer")
                bot = npc.spawn(run.match, definition, (0, 1, 5), team=1)
                bot.yaw = -32768
                bot.firing, bot.attack_target = True, target.entity
                pellets = self.emit(run, bot)
                self.assertEqual(len(pellets), 25)
                run.step({target: (0, 0, 8), caster: (-16384, 0, 16 if overlap else 0)})
                self.assertTrue(fight.zarya_barriers.protects(target))
                personal = fight.zarya_barriers.active[target.body]
                self.assertEqual(personal.remaining, 200)
                personal.remaining = 21
                self.drain(run)
                self.assertFalse(fight.rockets)
                self.assertEqual(personal.remaining, 0)
                self.assertAlmostEqual(target.body_script.component.entity_var(87).value(), 4.2, places=4)
                self.assertEqual(fight.vitals[target.body].total, 400 if overlap else 372)
                if overlap:
                    projected = fight.zarya_barriers.projected[caster.body]
                    self.assertEqual((projected.recipient, projected.remaining), (target.body, 172))
                    self.assertAlmostEqual(caster.body_script.component.entity_var(87).value(), 5.6, places=4)

    def test_static_wall_stops_the_whole_volley(self):
        run, _, bot, target = self.encounter()
        self.emit(run, bot)
        wall = World([(-10, 0, 2.5, 10, 0, 2.5, 10, 10, 2.5),
                      (-10, 0, 2.5, 10, 10, 2.5, -10, 10, 2.5)])
        with patch.object(run.match.combat, "_collision", return_value=wall):
            self.drain(run)
        self.assertEqual(run.match.combat.vitals_of(target).total, 400)
        self.assertFalse(run.match.combat.rockets)

    def test_volley_hits_bubble_edge_then_later_pellets_continue_on_break(self):
        definition = next(d for d in fixtures.ZaryaFiveNpcTests.catalog.values() if d.name == "Enforcer")
        for projected in (False, True):
            for case in ("full", "break", "outside", "ally"):
                with self.subTest(projected=projected, case=case):
                    run = fixtures.Run(names=("Front", "Rear", "Projector"))
                    front, rear, projector = run.players
                    fight = run.match.combat
                    run.match.bots.clear()
                    run.match._pending_bot_scripts.clear()
                    offset = 2 if case == "outside" else 1
                    poses = ((front, (offset, 1, 2.5)), (rear, (0, 1, 0)), (projector, (6, 1, 2.5)))
                    for actor, point in poses:
                        actor.client = fixtures.NpcClient(actor)
                        self.assertTrue(run.match.switch_hero(actor, heroes()[fixtures.ZARYA]))
                        actor.team = int(actor is not rear and case == "ally")
                        actor.mover.position[:] = point
                        actor.mover.body = None
                    bot = npc.spawn(run.match, definition, (0, 1, 5), team=1)
                    bot.firing, bot.attack_target = True, rear.entity
                    pellets = self.emit(run, bot)
                    self.assertEqual(len(pellets), 25)
                    target = next(t for t in fight._targets() if t.owner is front)
                    a, b = target.segment()
                    x, y, z = target.feet
                    centre = x, y + target.height / 2, z
                    for pellet in pellets:
                        body = combat.ray_capsule(pellet.position, pellet.direction, a, b, target.radius)
                        self.assertIsNone(body)
                        edge = combat.ray_capsule(
                            pellet.position, pellet.direction, centre, centre, zarya.BUBBLE_RADIUS,
                        )
                        self.assertEqual(edge is not None, case != "outside")
                    caster = projector if projected else front
                    angles = aim(fight._eye(caster), fight._eye(front)) if projected else (0, 0)
                    run.step({caster: (*angles, 16 if projected else 8)})
                    barriers = fight.zarya_barriers
                    self.assertTrue(barriers.protects(front))
                    pool = (barriers.projected if projected else barriers.active)[caster.body]
                    capacity = 21 if case == "break" else 200
                    pool.remaining = capacity
                    self.drain(run)
                    self.assertFalse(fight.rockets)
                    absorbed = {"full": 50, "break": 21}.get(case, 0)
                    self.assertEqual(pool.remaining, capacity - absorbed)
                    energy = caster.body_script.component.entity_var(87).value() or 0
                    self.assertAlmostEqual(energy, absorbed / 5, places=4)
                    self.assertEqual(fight.vitals_of(target).total, 400)
                    expected = {"full": 400, "break": 372}.get(case, 350)
                    self.assertEqual(fight.vitals[rear.body].total, expected)

    def test_projected_barrier_blocks_all_pellets_on_ally_and_credits_caster(self):
        run = fixtures.Run(names=("Caster", "Ally"))
        caster, ally = run.players
        for player in run.players:
            player.client = fixtures.NpcClient(player)
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        self.assertTrue(run.match.switch_hero(caster, heroes()[fixtures.ZARYA]))
        ally.team = caster.team
        caster.mover.position[:] = [0, 1, 0]
        ally.mover.position[:] = [0, 1, 5]
        definition = next(d for d in fixtures.ZaryaFiveNpcTests.catalog.values() if d.name == "Enforcer")
        bot = npc.spawn(run.match, definition, (0, 1, 10), team=1)
        bot.firing, bot.attack_target = True, ally.entity
        self.emit(run, bot)
        target = next(t for t in run.match.combat._targets() if t.owner is ally)
        before = run.match.combat.vitals_of(target).total
        run.step({caster: (0, 0, 16)})
        self.drain(run)
        self.assertEqual(run.match.combat.vitals_of(target).total, before)
        self.assertAlmostEqual(caster.body_script.component.entity_var(87).value(), 10, delta=0.00001)
        energy = next(i for i in caster.body_script.component.instances.values() if i.graph.index == 0x0242)
        self.assertEqual(energy.vars[392].value().id, bot.entity)

    def test_release_before_warmup_prevents_delayed_shot(self):
        run, _, bot, target = self.encounter()
        self.drain(run, 20)
        bot.firing = False
        self.drain(run, 100)
        self.assertEqual(run.match.combat.vitals_of(target).total, 400)
        self.assertFalse(run.match.combat.rockets)

    def test_friendly_target_does_not_admit_manual_fire(self):
        run, _, _, target = self.encounter(team=0)
        self.drain(run, 100)
        self.assertEqual(run.match.combat.vitals_of(target).total, 400)
        self.assertFalse(run.match.combat.rockets)
