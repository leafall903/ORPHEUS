"""Ordinary Ball and Reinhardt input against the prepared 104319 shield mesh."""

import math
import os
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import test_wrecking_ball_combat as scenes
import test_wrecking_ball_phase as phase
import test_wrecking_ball_pve as pve
from test_game_combat import KANEZAKA_DM, PRACTICE_RANGE, Run, aim
from test_wrecking_ball_control import ANA, BallControlTests
from test_wrecking_ball_fireball import prepare_swing, relocate
from test_wrecking_ball_grapple import hold
from test_wrecking_ball_grapple import scene as swing_scene
from test_wrecking_ball_piledriver import lift, pile_instance

from ow174.game import (
    ball,
    barrier_geometry,
    barriers,
    combat,
    deflection,
    server_snapshot,
    sombra_emp,
    stats,
)
from ow174.game.collision import World
from ow174.game.commands import Command
from ow174.game.content import SOLDIER, heroes
from ow174.game.script import expr, graph
from ow174.game.script.driver import ULT_CHARGE, ULT_COST


@unittest.skipUnless(os.environ.get('ORPHEUS_BARRIER_DATA_DIR'), 'prepared barrier data required')
class BallBarrierTests(unittest.TestCase):
    def setUp(self):
        data = Path(os.environ['ORPHEUS_BARRIER_DATA_DIR'])
        self.addCleanup(pve.BallPveTests.clear_graphs)
        self.enterContext(patch.object(graph, 'GRAPHS_PATH', data / 'game_graphs_174.json.gz'))
        self.enterContext(patch.object(barrier_geometry, 'DATA_DIR', data))
        pve.BallPveTests.clear_graphs()

    def scene(self, distance=4, facing=-32768):
        run, player, rein, _, _ = scenes.BallCombatTests().scenario(distance=distance)
        run.match.switch_hero(rein, heroes()[barriers.REINHARDT])
        x, y, z = player.mover.position
        rein.mover.position[:] = [x, y, z + distance]
        for _ in range(90):
            run.step({})
        player.body_script.ult_seconds = 0
        player.body_script.set_ult_charge(0)
        shield = self.raise_barrier(run, rein, facing)
        self.assertEqual(shield.vitals.total, 1600)
        self.assertTrue(shield.mesh)
        self.assertEqual({i.graph.index for i in shield.component.instances.values()}, {0x25B4, 0x96})
        return run, player, rein, shield

    def raise_barrier(self, run, rein, facing=-32768):
        for _ in range(40):
            run.step({rein: (facing, 0, 2)})
        self.assertEqual(len(run.match.combat.barriers), 1)
        return next(iter(run.match.combat.barriers.values()))

    def hack_shield_scene(self, *, friendly=False):
        run = Run(KANEZAKA_DM, ('Ball', 'Sombra', 'Reinhardt'))
        player, sombra, rein = run.players
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        player.team, sombra.team = 0, 1
        rein.team = sombra.team if friendly else player.team
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        for owner, hero in ((player, ball.HERO), (sombra, sombra_emp.SOMBRA), (rein, barriers.REINHARDT)):
            run.match.switch_hero(owner, heroes()[hero])
        x, y, z = player.mover.position
        relocate(run, sombra, (x, y, z - 6))
        relocate(run, rein, (x, y, z - 3))
        for _ in range(60):
            run.step({})
        fight = run.match.combat
        angles = aim(fight._eye(sombra), (x, y + 1, z))
        hack = next(i for i in sombra.body_script.component.instances.values()
                    if i.graph.index == sombra_emp.ORDINARY_HACK)
        return run, player, sombra, rein, angles, hack

    def test_ordinary_hack_respects_reinhardt_shield_team(self):
        for friendly in (False, True):
            with self.subTest(friendly=friendly):
                run, player, sombra, rein, angles, hack = self.hack_shield_scene(friendly=friendly)
                shield = self.raise_barrier(run, rein)
                for _ in range(48):
                    run.step({sombra: (*angles, 2), rein: (-32768, 0, 2)})
                self.assertEqual(hack.state(54).counter, int(friendly))
                self.assertEqual(sombra_emp.locked(player.body_script, run.frame), friendly)
                self.assertEqual(shield.vitals.total, 1600)

    def test_ordinary_hack_aborts_when_reinhardt_raises_shield_during_channel(self):
        run, player, sombra, rein, angles, hack = self.hack_shield_scene()
        for _ in range(10):
            run.step({sombra: (*angles, 2)})
        self.assertTrue(hack.state(54).active)
        for _ in range(14):
            run.step({sombra: (*angles, 2), rein: (-32768, 0, 2)})
        shield, = run.match.combat.barriers.values()
        self.assertFalse(hack.state(54).active)
        self.assertTrue(hack.vars[8479].value())
        self.assertEqual(hack.state(98).counter, 0)
        self.assertFalse(sombra_emp.locked(player.body_script, run.frame))
        self.assertEqual(shield.vitals.total, 1600)
        for _ in range(150):
            run.step({})
        self.assertTrue(shield.retired)
        for _ in range(48):
            run.step({sombra: (*angles, 2)})
        self.assertEqual(hack.state(54).counter, 2)
        self.assertEqual(hack.state(98).counter, 1)
        self.assertTrue(sombra_emp.locked(player.body_script, run.frame))

    def transcendence_scene(self, defender_hero, *, friendly=False):
        run = Run(KANEZAKA_DM, ('Ball', 'Zenyatta', 'Barrier owner'))
        player, zen, defender = run.players
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        player.team = zen.team = 0
        defender.team = 0 if friendly else 1
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        for owner, hero in ((player, ball.HERO), (zen, phase.ZENYATTA), (defender, defender_hero)):
            run.match.switch_hero(owner, heroes()[hero])
        x, y, z = player.mover.position
        relocate(run, zen, (x, y, z + 7))
        relocate(run, defender, (x, y, z + 3))
        for _ in range(60):
            run.step({})
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is player)
        own = fight.vitals_of(target)
        own.damage(200)
        fight._sync_body_health(player, own)
        return run, player, zen, defender, own

    def test_transcendence_respects_reinhardt_shield_team_and_release(self):
        for friendly in (False, True):
            with self.subTest(friendly=friendly):
                run, _, zen, rein, own = self.transcendence_scene(barriers.REINHARDT, friendly=friendly)
                facing = 0
                raised = {rein: (facing, 0, 2)}
                shield = self.raise_barrier(run, rein, facing)
                initial = own.total
                script = zen.body_script
                script.set_ult_charge(script.component.entity_var(ULT_COST).value())
                run.step({**raised, zen: (0, 0, 32)})
                _, immunity = phase.BallPhaseTests().protection_instance(zen)
                self.assertTrue(immunity.active)
                for _ in range(8):
                    run.step(raised)
                self.assertAlmostEqual(own.total, initial + (38.4 if friendly else 0))
                self.assertAlmostEqual(zen.stats.value(stats.HEALING), 38.4 if friendly else 0)
                self.assertEqual(shield.vitals.total, 1600)

                if not friendly:
                    run.step({rein: (facing, 0, 0)})
                    self.assertTrue(shield.retired)
                    for _ in range(8):
                        run.step({})
                    self.assertTrue(immunity.active)
                    self.assertAlmostEqual(own.total, initial + 38.4)
                    self.assertAlmostEqual(zen.stats.value(stats.HEALING), 38.4)
                    fresh = self.raise_barrier(run, rein, facing)
                    self.assertIsNot(fresh, shield)
                    before = own.total
                    self.assertLess(before, 600)
                    for _ in range(8):
                        run.step(raised)
                    self.assertEqual(own.total, before)
                    self.assertEqual(fresh.vitals.total, 1600)

    def test_transcendence_resumes_inside_and_after_hostile_winston_dome(self):
        run, player, zen, winston, own = self.transcendence_scene(barriers.WINSTON)
        run.step({winston: (0, 0, 16)})
        fight = run.match.combat
        for _ in range(45):
            if fight.barriers:
                break
            run.step({})
        dome, = fight.barriers.values()
        self.assertEqual(dome.vitals.total, 700)
        for _ in range(390):
            run.step({})
        self.assertFalse(dome.retired)
        x, y, z = player.mover.position
        relocate(run, player, (x, y, z - 3))
        relocate(run, zen, (x, y, z + 4))
        immunity = phase.BallPhaseTests().cast_transcendence(run, zen)
        initial = own.total
        for _ in range(8):
            run.step({})
        self.assertEqual(own.total, initial)

        # Move Ball to the same side of the hostile shell as Zenyatta.
        relocate(run, player, (x, y, z + 1))
        for _ in range(5):
            run.step({})
        self.assertAlmostEqual(own.total, initial + 24)
        # Put Zenyatta outside and keep Ball inside to test the reverse crossing.
        relocate(run, zen, (x, y, z + 9))
        for _ in range(8):
            run.step({})
        self.assertAlmostEqual(own.total, initial + 24)
        self.assertEqual(dome.vitals.total, 700)

        for _ in range(190):
            run.step({})
            if dome.retired:
                break
        self.assertTrue(dome.retired)
        self.assertTrue(immunity.active)
        before = own.total
        for _ in range(5):
            run.step({})
        self.assertAlmostEqual(own.total, before + 24)
        self.assertAlmostEqual(zen.stats.value(stats.HEALING), own.total - initial)

    def test_cannon_hits_shield_before_body_and_preserves_damage_on_reraise(self):
        run, player, rein, shield = self.scene()
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is rein)

        def digest():
            stored = server_snapshot._Graph({
                'shield-owner': rein,
                'match': run.match,
                **{f'client-{p.slot}': p.client for p in run.players},
            }, None)
            root = stored.value(shield)
            return server_snapshot.snapshot_digest({
                'schema': server_snapshot.SCHEMA, 'scope': server_snapshot.SCOPE,
                'restorable': False, 'roots': {'shield': root}, 'nodes': stored.finish(), 'assets': [],
            })

        before = digest()
        run.step({player: (0, 0, 1), rein: (-32768, 0, 2)})
        self.assertEqual(shield.vitals.total, 1595)
        self.assertNotEqual(digest(), before)
        self.assertEqual(fight.vitals_of(target).total, 500)
        self.assertEqual(player.client.messages(combat.HIT), [])
        self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), 0)
        self.assertEqual(ball.instance(player, 0x1514).vars[53].value(), 79)
        for _ in range(30):
            run.step({})
        self.assertTrue(shield.retired)
        self.assertFalse(fight.barriers)
        replacement = self.raise_barrier(run, rein)
        self.assertNotEqual(replacement.entity, shield.entity)
        self.assertEqual(replacement.vitals.total, 1595)

    def test_wall_rear_and_melee_have_distinct_collision_outcomes(self):
        for condition in ('wall', 'rear', 'melee'):
            with self.subTest(condition=condition):
                distance = 1.5 if condition == 'melee' else 4
                facing = 0 if condition == 'rear' else -32768
                run, player, rein, shield = self.scene(distance, facing)
                fight = run.match.combat
                target = next(t for t in fight._targets() if t.owner is rein)
                x, y, z = player.mover.position
                if condition == 'wall':
                    wall = z + 1
                    run.match.collision_world = World([
                        (x - 3, y, wall, x + 3, y, wall, x + 3, y + 4, wall),
                        (x - 3, y, wall, x + 3, y + 4, wall, x - 3, y + 4, wall),
                    ])
                angles = aim(fight._eye(player), (x, y + 1, z + distance))
                button = 0x800 if condition == 'melee' else 1
                run.step({player: (*angles, button), rein: (facing, 0, 2)})
                for _ in range(35):
                    run.step({rein: (facing, 0, 2)})
                self.assertEqual(shield.vitals.total, 1600)
                lost = {'wall': 0, 'rear': 2.5, 'melee': 25}[condition]
                self.assertEqual(fight.vitals_of(target).total, 500 - lost)
                self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), lost)

    def test_held_cannon_breaks_shield_and_owner_reraises_after_lockout(self):
        run, player, rein, shield = self.scene()
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is rein)
        for _ in range(1700):
            run.step({player: (0, 0, 1), rein: (-32768, 0, 2)})
            if shield.retired:
                break
        self.assertTrue(shield.retired)
        self.assertEqual(shield.vitals.total, 0)
        self.assertFalse(fight.barriers)
        self.assertEqual(fight.vitals_of(target).total, 500)
        self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), 0)
        owner = shield.source.instances[shield.instance]
        self.assertAlmostEqual(owner.vars[61].value(), 5, delta=0.017)
        for _ in range(90):
            run.step({rein: (-32768, 0, 2)})
        self.assertFalse(fight.barriers)
        run.step({})
        for _ in range(40):
            run.step({rein: (-32768, 0, 2)})
        self.assertFalse(fight.barriers)
        for _ in range(189):
            run.step({})
        self.assertEqual(owner.vars[61].value(), 0)
        self.assertFalse(fight.barriers)
        saved = owner.vars[586].value()
        self.assertGreater(saved, 0)
        self.assertLess(saved, 1600)
        recovered = self.raise_barrier(run, rein)
        self.assertNotEqual(recovered.entity, shield.entity)
        self.assertGreaterEqual(recovered.vitals.total, saved)
        self.assertLess(recovered.vitals.total, 1600)
        self.assertEqual(recovered.vitals.total, owner.vars[586].value())

    def test_hero_replacement_retires_raised_shield(self):
        run, player, rein, shield = self.scene()
        fight = run.match.combat
        run.match.switch_hero(rein, heroes()[SOLDIER])
        x, y, z = player.mover.position
        rein.mover.position[:] = [x, y, z + 4]
        self.assertTrue(shield.retired)
        self.assertFalse(fight.barriers)
        for _ in range(90):
            run.step({})
        target = next(t for t in fight._targets() if t.owner is rein)
        angles = aim(fight._eye(player), (x, y + 1, z + 4))
        run.step({player: (*angles, 1)})
        self.assertEqual(fight.vitals_of(target).total, 195)
        self.assertEqual(shield.vitals.total, 1600)
        self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), 5)

    def test_zarya_personal_bubble_consumes_cannon_and_melee(self):
        run, player, zarya, _, _ = scenes.BallCombatTests().scenario(distance=1.5)
        run.match.switch_hero(zarya, heroes()[barriers.ZARYA])
        x, y, z = player.mover.position
        zarya.mover.position[:] = [x, y, z + 1.5]
        for _ in range(90):
            run.step({})
        player.body_script.ult_seconds = 0
        player.body_script.set_ult_charge(0)
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is zarya)
        run.step({zarya: (-32768, 0, 8)})
        self.assertEqual(len(fight.barriers), 1)
        bubble = next(iter(fight.barriers.values()))
        ability = bubble.source.instances[bubble.instance]
        self.assertEqual(ability.graph.index, 0x244)
        child = next(i for i in bubble.component.instances.values() if i.graph.index == 0x256)
        self.assertEqual(child.source_entity(), zarya.body)
        self.assertEqual(bubble.vitals.total, 200)

        energy = zarya.body_script.component.entity_var(87)
        run.step({player: (0, 0, 1)})
        self.assertEqual(bubble.vitals.total, 195)
        self.assertAlmostEqual(energy.value(), 1)
        self.assertEqual(ball.instance(player, 0x1514).vars[53].value(), 79)
        angles = aim(fight._eye(player), (x, y + 1, z + 1.5))
        run.step({player: (*angles, 0x800)})
        for _ in range(35):
            run.step({})
        self.assertEqual(bubble.vitals.total, 165)
        self.assertAlmostEqual(energy.value(), 7, places=4)
        self.assertEqual(fight.vitals_of(target).total, 400)
        self.assertEqual(player.client.messages(combat.HIT), [])
        self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), 0)

        for _ in range(130):
            run.step({})
        self.assertTrue(bubble.retired)
        self.assertFalse(fight.barriers)
        self.assertGreater(energy.value(), 0)
        self.assertLess(energy.value(), 7)
        run.step({zarya: (-32768, 0, 8), player: (*angles, 1)})
        self.assertFalse(fight.barriers)
        self.assertEqual(fight.vitals_of(target).total, 395)
        self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), 5)
        self.assertGreater(ability.vars[61].value(), 0)
        for _ in range(640):
            run.step({})
            if ability.vars[61].value() == 0:
                break
        self.assertEqual(ability.vars[61].value(), 0)
        self.assertFalse(fight.barriers)
        run.step({zarya: (-32768, 0, 8)})
        self.assertEqual(len(fight.barriers), 1)
        renewed = next(iter(fight.barriers.values()))
        self.assertNotEqual(renewed.entity, bubble.entity)
        self.assertEqual(renewed.vitals.total, 200)

    def test_zarya_bubble_break_consumes_entire_melee_and_caps_energy_to_shield_loss(self):
        run = Run(KANEZAKA_DM, ('Ball A', 'Ball B', 'Zarya'))
        first, second, zarya = run.players
        fight = run.match.combat
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        run.match.switch_hero(zarya, heroes()[barriers.ZARYA])
        x, y, z = zarya.mover.position
        for player, offset in ((first, -1.7), (second, 1.7)):
            run.match.switch_hero(player, heroes()[ball.HERO])
            player.mover.position[:] = [x + offset, y, z]
        for _ in range(90):
            run.step({})
        aim_first = aim(fight._eye(first), (x, y + 1, z))
        aim_second = aim(fight._eye(second), (x, y + 1, z))
        for player in (first, second):
            player.body_script.ult_seconds = 0
            player.body_script.set_ult_charge(0)

        run.step({zarya: (0, 0, 8)})
        bubble = next(iter(fight.barriers.values()))
        target = next(t for t in fight._targets() if t.owner is zarya)
        for _ in range(100):
            run.step({first: (*aim_first, 1), second: (*aim_second, 1)})
            if bubble.vitals.total < 30:
                break
        self.assertGreater(bubble.vitals.total, 0)
        self.assertLess(bubble.vitals.total, 30)
        run.step({first: (*aim_first, 0x800)})
        for _ in range(15):
            run.step({})
            if bubble.retired:
                break
        self.assertTrue(bubble.retired)
        self.assertEqual(bubble.vitals.total, 0)
        self.assertAlmostEqual(zarya.body_script.component.entity_var(87).value(), 40, places=4)
        self.assertEqual(fight.vitals_of(target).total, 400)
        for player in (first, second):
            self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), 0)

    def projected_scene(self, caster_distance=5, ally_distance=1.5, *, extra_players=()):
        run = Run(KANEZAKA_DM, ('Ball', 'Protected ally', 'Zarya', *extra_players))
        player, ally, zarya = run.players[:3]
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        player.team, ally.team, zarya.team = 0, 1, 1
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        run.match.switch_hero(player, heroes()[ball.HERO])
        run.match.switch_hero(zarya, heroes()[barriers.ZARYA])
        x, y, z = player.mover.position
        ally.mover.position[:] = [x, y, z + ally_distance]
        zarya.mover.position[:] = [x, y, z + caster_distance]
        for _ in range(90):
            run.step({})
        player.body_script.ult_seconds = 0
        player.body_script.set_ult_charge(0)
        return run, player, ally, zarya

    def cast_projected(self, run, zarya, yaw=-32768):
        run.step({zarya: (yaw, 0, 16)})
        for _ in range(12):
            run.step({zarya: (yaw, 0, 0)})

    def test_late_projected_cast_cannot_create_an_expired_bubble(self):
        run, player, ally, zarya = self.projected_scene()
        match = run.match
        fight = match.combat
        start = run.frame + 1
        run.frame = start + 126
        match.update(run.frame * combat.TICK, run.frame, frames=False)
        zarya.take_commands([Command(start, yaw=-32768, buttons=16)])
        projection = next(i for i in zarya.body_script.component.instances.values() if i.graph.index == 0x25C)
        self.assertEqual(projection.state(10).counter, 1)
        self.assertFalse(fight.barriers)
        x, y, z = ally.mover.position
        angles = aim(fight._eye(player), (x, y + 0.85, z))
        run.step({player: (*angles, 1)})
        self.assertEqual(fight.vitals[ally.body].total, 195)
        self.assertFalse(fight.barriers)

    def test_zarya_projected_barrier_credits_caster_and_retires_on_recipient_replacement(self):
        run, player, ally, zarya = self.projected_scene()
        fight = run.match.combat
        self.cast_projected(run, zarya)
        self.assertEqual(len(fight.barriers), 1)
        bubble = next(iter(fight.barriers.values()))
        self.assertIs(bubble.recipient, ally)
        ability = bubble.source.instances[bubble.instance]
        self.assertEqual(ability.graph.index, 0x25C)
        child = next(i for i in bubble.component.instances.values() if i.graph.index == 0x256)
        self.assertEqual(child.source_entity(), zarya.body)
        self.assertEqual(expr.to_entity(child.vars[2390].value()), ally.body)
        self.assertEqual(bubble.vitals.total, 200)
        x, y, z = ally.mover.position
        angles = aim(fight._eye(player), (x, y + 1, z))
        run.step({player: (*angles, 1)})
        run.step({player: (*angles, 0x800)})
        for _ in range(35):
            run.step({})
        target = next(t for t in fight._targets() if t.owner is ally)
        self.assertEqual(bubble.vitals.total, 165)
        self.assertAlmostEqual(zarya.body_script.component.entity_var(87).value(), 7, places=4)
        self.assertEqual(fight.vitals_of(target).total, 200)
        self.assertEqual(player.client.messages(combat.HIT), [])
        self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), 0)
        self.assertTrue(run.match.switch_hero(ally, ally.hero))
        ally.mover.position[:] = [x, y, z]
        self.assertTrue(bubble.retired)
        self.assertFalse(bubble.component.instances)
        self.assertFalse(fight.barriers)
        self.assertGreater(ability.vars[61].value(), 0)
        self.cast_projected(run, zarya)
        self.assertFalse(fight.barriers)
        for _ in range(680):
            run.step({})
            if ability.vars[61].value() == 0:
                break
        self.assertEqual(ability.vars[61].value(), 0)
        self.cast_projected(run, zarya)
        fresh = next(iter(fight.barriers.values()))
        self.assertNotEqual(fresh.entity, bubble.entity)
        self.assertIs(fresh.recipient_component, ally.body_script.component)
        self.assertEqual(fresh.vitals.total, 200)
        self.assertTrue(run.match.switch_hero(zarya, zarya.hero))
        self.assertTrue(fresh.retired)
        self.assertFalse(fresh.component.instances)
        self.assertFalse(fight.barriers)

    def test_adaptive_shield_counts_projected_barrier_recipient_once(self):
        run, player, recipient, zarya = self.projected_scene(caster_distance=12)
        fight = run.match.combat
        self.cast_projected(run, zarya)
        bubble, = fight.barriers.values()
        self.assertIs(bubble.recipient, recipient)
        x, y, z = recipient.mover.position
        angles = aim(fight._eye(player), (x, y + 1, z))
        run.step({player: (*angles, 1)})
        self.assertEqual(bubble.vitals.total, 195)
        self.assertFalse(player.client.messages(combat.HIT))

        own = next(t for t in fight._targets() if t.owner is player)
        run.step({player: (0, 0, 16)})
        self.assertEqual(fight.vitals_of(own).temporary_shields, 175)
        self.assertEqual(bubble.vitals.total, 195)

    def test_overlapping_projected_barriers_consume_one_hit_and_keep_distinct_lifetimes(self):
        run, player, recipient, first = self.projected_scene(extra_players=('Second Zarya',))
        second = run.players[3]
        second.team = first.team
        run.match.switch_hero(second, heroes()[barriers.ZARYA])
        x, y, z = recipient.mover.position
        relocate(run, second, (x + 3.5, y, z))
        for _ in range(90):
            run.step({})

        fight = run.match.combat
        self.cast_projected(run, first)
        oldest, = fight.barriers.values()
        self.cast_projected(run, second, yaw=-16384)
        self.assertEqual(len(fight.barriers), 2)
        newest = next(bubble for bubble in fight.barriers.values() if bubble.parent is second)
        for bubble in (oldest, newest):
            self.assertIs(bubble.recipient, recipient)
            self.assertEqual(bubble.vitals.total, 200)
        target = next(target for target in fight._targets() if target.owner is recipient)
        charge = player.body_script.component.entity_var(ULT_CHARGE)
        first_energy = first.body_script.component.entity_var(87)
        second_energy = second.body_script.component.entity_var(87)
        angles = aim(fight._eye(player), (x, y + 1, z))

        run.step({player: (*angles, 1)})
        self.assertEqual((oldest.vitals.total, newest.vitals.total), (195, 200))
        self.assertAlmostEqual(first_energy.value(), 1, places=4)
        self.assertIsNone(second_energy.value())
        self.assertEqual(fight.vitals_of(target).total, 200)
        self.assertEqual(charge.value(), 0)

        for _ in range(140):
            run.step({})
            if oldest.retired:
                break
        self.assertTrue(oldest.retired)
        self.assertFalse(newest.retired)
        run.step({player: (*angles, 1)})
        self.assertEqual(newest.vitals.total, 195)
        self.assertAlmostEqual(second_energy.value(), 1, places=4)
        self.assertEqual(fight.vitals_of(target).total, 200)
        self.assertEqual(charge.value(), 0)

        for _ in range(35):
            run.step({})
            if newest.retired:
                break
        self.assertTrue(newest.retired)
        self.assertFalse(fight.barriers)
        run.step({player: (*angles, 1)})
        self.assertEqual(fight.vitals_of(target).total, 195)
        self.assertEqual(charge.value(), 5)

    def nano_barrier_scene(self, distance=6):
        run = Run(replace(PRACTICE_RANGE, team_sizes=(6, 6)),
                  ('Protected Ball', 'Attacking Ball', 'Zarya', 'Ana', 'Enemy Ana'))
        player, enemy, zarya, ana, enemy_ana = run.players
        match = run.match
        match.bots.clear()
        match._pending_bot_scripts.clear()
        x, y, z = player.mover.position
        for actor, hero, team, position in (
            (player, ball.HERO, 0, (x, y, z)),
            (enemy, ball.HERO, 1, (x, y, z + distance)),
            (zarya, barriers.ZARYA, 0, (x + 12, y, z + 2)),
            (ana, ANA, 0, (x - 10, y, z)),
            (enemy_ana, ANA, 1, (x + 5, y, z + distance)),
        ):
            actor.team = team
            self.assertTrue(match.switch_hero(actor, heroes()[hero]))
            relocate(run, actor, position)
            actor.body_script.ult_seconds = 0
            actor.body_script.set_ult_charge(0)
        for _ in range(40):
            run.step({})
        fight = match.combat
        casts = {}
        for caster, target in ((ana, player), (enemy_ana, enemy)):
            script = caster.body_script
            script.set_ult_charge(script.component.entity_var(ULT_COST).value())
            tx, ty, tz = target.mover.position
            casts[caster] = (*aim(fight._eye(caster), (tx, ty + 0.8, tz)), 32)
        run.step(casts)
        for _ in range(13):
            run.step({})
        own = fight.vitals[player.body]
        self.assertGreater(own.nano_until, run.frame)
        self.assertGreater(fight.vitals[enemy.body].nano_until, run.frame)
        return run, player, enemy, zarya, own

    def test_nano_scales_cannon_on_projected_barrier_then_reduces_body_damage(self):
        for adaptive in (False, True):
            with self.subTest(adaptive=adaptive):
                run, player, enemy, zarya, own = self.nano_barrier_scene()
                fight = run.match.combat
                x, y, z = player.mover.position
                if adaptive:
                    run.step({player: (0, 0, 16)})
                    self.assertGreater(own.temporary_shields, 0)
                total, shield = own.total, own.temporary_shields
                yaw, _ = aim(fight._eye(zarya), (x, y + 0.8, z))
                self.cast_projected(run, zarya, yaw)
                bubble, = barriers.projected_on(fight, player.body)
                self.assertEqual(bubble.vitals.total, 200)
                energy = zarya.body_script.component.entity_var(87)
                charge = enemy.body_script.component.entity_var(ULT_CHARGE)
                shot = aim(fight._eye(enemy), (x, y + 0.8, z))

                run.step({enemy: (*shot, 1)})
                self.assertEqual(bubble.vitals.total, 192.5)
                self.assertAlmostEqual(energy.value(), 1.5, places=5)
                self.assertEqual(own.total, total)
                self.assertEqual(charge.value(), 0)
                for _ in range(12):
                    run.step({})
                # Leave less capacity than the next boosted shot.
                bubble.vitals.damage(bubble.vitals.total - 6)
                run.step({enemy: (*shot, 1)})
                self.assertTrue(bubble.retired)
                self.assertAlmostEqual(energy.value(), 2.7, places=5)
                self.assertEqual(own.total, total)
                self.assertEqual(charge.value(), 0)
                for _ in range(12):
                    run.step({})
                run.step({enemy: (*shot, 1)})
                self.assertEqual(own.total, total - (3.75 if adaptive else 1.875))
                self.assertEqual(own.temporary_shields, shield - 3.75 if adaptive else 0)
                self.assertEqual(charge.value(), 0 if adaptive else 1.875)

    def test_nano_scales_piledriver_before_projected_barrier_and_body_admission(self):
        for projected in (False, True):
            with self.subTest(projected=projected):
                run, player, enemy, zarya, own = self.nano_barrier_scene(distance=1.5)
                fight = run.match.combat
                if projected:
                    x, y, z = player.mover.position
                    yaw, _ = aim(fight._eye(zarya), (x, y + 0.8, z))
                    self.cast_projected(run, zarya, yaw)
                    bubble, = barriers.projected_on(fight, player.body)
                lift(run, enemy)
                run.step({enemy: (0, 0, 128)})
                for _ in range(100):
                    run.step({})
                    if own.total < 600 or (projected and bubble.vitals.total < 200):
                        break
                self.assertEqual(pile_instance(enemy).states[7].counter, 1)
                charge = enemy.body_script.component.entity_var(ULT_CHARGE)
                if projected:
                    self.assertEqual(bubble.vitals.total, 50)
                    self.assertAlmostEqual(zarya.body_script.component.entity_var(87).value(), 30)
                    self.assertEqual(own.total, 600)
                    self.assertFalse(player.mover.airborne)
                    self.assertNotIn(player.body, fight.pile_air_locks)
                    self.assertEqual(charge.value(), 0)
                else:
                    self.assertEqual(own.total, 530)
                    self.assertGreater(player.mover.velocity[1], 0)
                    self.assertIn(player.body, fight.pile_air_locks)
                    self.assertEqual(charge.value(), 70)

    def test_reinhardt_shield_and_grasp_use_nearest_contact(self):
        for grasp_first in (False, True):
            with self.subTest(grasp_first=grasp_first):
                self.check_reinhardt_shield_and_grasp_order(grasp_first=grasp_first)

    def check_reinhardt_shield_and_grasp_order(self, *, grasp_first):
        run = Run(KANEZAKA_DM, ('Ball', 'Reinhardt', 'Sigma'))
        player, rein, sigma = run.players
        match = run.match
        match.game_map = replace(match.game_map, free_for_all=False)
        match.bots.clear()
        match._pending_bot_scripts.clear()
        player.team, rein.team, sigma.team = 0, 1, 1
        for owner, hero in ((player, ball.HERO), (rein, barriers.REINHARDT), (sigma, deflection.SIGMA)):
            self.assertTrue(match.switch_hero(owner, heroes()[hero]))
        x, y, z = player.mover.position
        relocate(run, rein, (x, y, z + 7))
        relocate(run, sigma, (x, y, z + (3 if grasp_first else 12)))
        for _ in range(90):
            run.step({})
        shield = self.raise_barrier(run, rein)
        run.step({rein: (-32768, 0, 2), sigma: (-32768, 0, 8)})
        held = {rein: (-32768, 0, 2), sigma: (-32768, 0, 0)}
        for _ in range(5):
            run.step(held)

        fight = match.combat
        grasp = next(i for i in sigma.body_script.component.instances.values() if i.graph.index == 0x1D02)
        self.assertTrue(grasp.state(1).active)
        self.assertTrue(shield.mesh)
        origin, direction = fight._eye(player), combat.aim(0, 0)
        shield_contact = barriers.ray_hit(fight, player, origin, direction, 100)
        grasp_contact = deflection.intercept(fight, player, origin, direction, 100)
        self.assertIsNotNone(shield_contact)
        self.assertIsNotNone(grasp_contact)
        self.assertIs(shield_contact[1], shield)
        self.assertIs(grasp_contact[1], sigma)
        self.assertNotEqual(grasp_contact[0], shield_contact[0])
        self.assertEqual(grasp_contact[0] < shield_contact[0], grasp_first)

        vitals = [fight.vitals_of(t) for t in fight._enemies(player)]
        health = [v.total for v in vitals]
        player.body_script.ult_seconds = 0
        player.body_script.set_ult_charge(0)
        ammo = ball.instance(player, 0x1514).vars[53]
        absorbed = grasp.vars[21312]
        self.assertEqual((shield.vitals.total, absorbed.value()), (1600, 0))
        run.step({**held, player: (0, 0, 1)})
        self.assertEqual(ammo.value(), 79)
        self.assertEqual((shield.vitals.total, absorbed.value()), (1600, 5) if grasp_first else (1595, 0))
        self.assertEqual([v.total for v in vitals], health)
        self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), 0)
        self.assertFalse(player.client.messages(combat.HIT))

    def test_projected_barrier_and_grasp_use_nearest_contact(self):
        for grasp_first in (False, True):
            with self.subTest(grasp_first=grasp_first):
                self.check_projected_barrier_and_grasp_order(grasp_first=grasp_first)

    def check_projected_barrier_and_grasp_order(self, *, grasp_first):
        run, player, recipient, zarya = self.projected_scene(ally_distance=7, extra_players=('Sigma',))
        sigma = run.players[3]
        sigma.team = recipient.team
        self.assertTrue(run.match.switch_hero(sigma, heroes()[deflection.SIGMA]))
        fight = run.match.combat
        x, y, z = player.mover.position
        relocate(run, zarya, (x + 3.5, y, z + 7))
        relocate(run, sigma, (x, y, z + (3 if grasp_first else 12)))
        for _ in range(45):
            run.step({})
        self.cast_projected(run, zarya, yaw=-16384)
        bubble, = fight.barriers.values()
        self.assertIs(bubble.recipient, recipient)
        run.step({sigma: (-32768, 0, 8)})
        held = {sigma: (-32768, 0, 0)}
        for _ in range(5):
            run.step(held)
        grasp = next(i for i in sigma.body_script.component.instances.values() if i.graph.index == 0x1D02)
        self.assertTrue(grasp.state(1).active)
        origin, direction = fight._eye(player), combat.aim(0, 0)
        target = next(t for t in fight._enemies(player) if t.owner is recipient)
        body_contact = combat.ray_capsule(origin, direction, *target.segment(), target.radius)
        grasp_contact = deflection.intercept(fight, player, origin, direction, 100)
        self.assertIsNotNone(body_contact)
        self.assertIsNotNone(grasp_contact)
        self.assertIs(grasp_contact[1], sigma)
        self.assertNotEqual(grasp_contact[0], body_contact)
        self.assertEqual(grasp_contact[0] < body_contact, grasp_first)

        vitals = [fight.vitals_of(t) for t in fight._enemies(player)]
        health = [v.total for v in vitals]
        absorbed = grasp.vars[21312]
        energy = zarya.body_script.component.entity_var(87)
        ammo = ball.instance(player, 0x1514).vars[53]
        charge = player.body_script.component.entity_var(ULT_CHARGE)
        self.assertEqual((bubble.vitals.total, expr.to_float(energy.value()), absorbed.value()), (200, 0, 0))
        trigger = {**held, player: (0, 0, 1)}
        run.step(trigger)
        self.assertEqual(ammo.value(), 79)
        expected = (200, 0, 5) if grasp_first else (195, 1, 0)
        self.assertEqual((bubble.vitals.total, expr.to_float(energy.value()), absorbed.value()), expected)

        nearer = sigma if grasp_first else recipient
        bx, by, bz = nearer.mover.position
        relocate(run, nearer, (bx + 12, by, bz))
        for _ in range(20):
            run.step(trigger)
            if ammo.value() == 78:
                break
        self.assertTrue(grasp.state(1).active)
        self.assertFalse(bubble.retired)
        self.assertEqual(ammo.value(), 78)
        self.assertEqual((bubble.vitals.total, expr.to_float(energy.value()), absorbed.value()), (195, 1, 5))
        self.assertEqual([v.total for v in vitals], health)
        self.assertEqual(charge.value(), 0)
        self.assertFalse(player.client.messages(combat.HIT))

    def test_projected_barrier_and_grasp_compete_after_genji_deflect(self):
        for grasp_first, shield_loss, grasp_damage in ((False, 3.059879, 1.5), (True, 1.5, 3.444944)):
            with self.subTest(grasp_first=grasp_first):
                self.check_projected_barrier_and_grasp_return(
                    grasp_first=grasp_first, shield_loss=shield_loss, grasp_damage=grasp_damage,
                )

    def check_projected_barrier_and_grasp_return(self, *, grasp_first, shield_loss, grasp_damage):
        recipient_distance = 5 if grasp_first else 13
        run, player, recipient, zarya = self.projected_scene(
            ally_distance=recipient_distance, extra_players=('Sigma', 'Genji'),
        )
        sigma, genji = run.players[3:]
        recipient.team = zarya.team = sigma.team = player.team
        genji.team = 1
        self.assertTrue(run.match.switch_hero(sigma, heroes()[deflection.SIGMA]))
        self.assertTrue(run.match.switch_hero(genji, heroes()[deflection.GENJI]))
        x, y, z = player.mover.position
        relocate(run, zarya, (x + 3.5, y, z + recipient_distance))
        relocate(run, sigma, (x, y, z + (12 if grasp_first else 4)))
        relocate(run, genji, (x, y, z + 18))
        for _ in range(45):
            run.step({})
        for owner in (player, genji):
            owner.body_script.ult_seconds = 0
            owner.body_script.set_ult_charge(0)
        self.cast_projected(run, zarya, yaw=-16384)
        fight = run.match.combat
        bubble, = fight.barriers.values()
        self.assertIs(bubble.recipient, recipient)
        run.step({sigma: (0, 0, 8), genji: (-32768, 0, 16)})
        held = {sigma: (0, 0, 0), genji: (-32768, 0, 0)}
        for _ in range(5):
            run.step(held)
        grasp = next(i for i in sigma.body_script.component.instances.values() if i.graph.index == 0x1D02)
        self.assertTrue(grasp.state(1).active)
        absorbed = grasp.vars[21312]
        energy = zarya.body_script.component.entity_var(87)
        ammo = ball.instance(player, 0x1514).vars[53]
        vitals = {t.owner: fight.vitals_of(t) for t in fight._targets()}
        self.assertEqual(set(vitals), set(run.players))
        health = {owner: pools.total for owner, pools in vitals.items()}
        contacts = []
        intercept = deflection.intercept

        def record_intercept(fight, shooter, *args, **kwargs):
            result = intercept(fight, shooter, *args, **kwargs)
            contacts.append((shooter, result[1] if result else None))
            return result

        angles = aim(fight._eye(player), (x, y + 0.55, z + 18))
        trigger = {**held, player: (*angles, 1)}
        with patch.object(deflection, 'intercept', side_effect=record_intercept):
            run.step(trigger)
            self.assertEqual(ammo.value(), 79)
            self.assertEqual(contacts, [(player, genji), (genji, sigma if grasp_first else None)])
            first_loss = 200 - bubble.vitals.total
            self.assertAlmostEqual(first_loss, 0 if grasp_first else shield_loss, places=4)
            self.assertAlmostEqual(absorbed.value(), grasp_damage if grasp_first else 0, places=4)
            self.assertAlmostEqual(expr.to_float(energy.value()), first_loss / 5, places=4)

            nearer = sigma if grasp_first else recipient
            nx, ny, nz = nearer.mover.position
            relocate(run, nearer, (nx + 12, ny, nz))
            contacts.clear()
            for _ in range(20):
                run.step(trigger)
                if ammo.value() == 78:
                    break
        self.assertEqual(ammo.value(), 78)
        self.assertEqual(contacts, [(player, genji), (genji, None if grasp_first else sigma)])
        self.assertTrue(grasp.state(1).active)
        self.assertFalse(bubble.retired)
        self.assertIs(bubble.recipient, recipient)
        loss = 200 - bubble.vitals.total
        self.assertAlmostEqual(loss, shield_loss, places=4)
        self.assertAlmostEqual(absorbed.value(), grasp_damage, places=4)
        self.assertAlmostEqual(expr.to_float(energy.value()), loss / 5, places=4)
        for owner, total in health.items():
            self.assertEqual(vitals[owner].total, total)
            self.assertFalse(owner.client.messages(combat.HIT))
        for owner in (player, genji):
            self.assertEqual(owner.body_script.component.entity_var(ULT_CHARGE).value(), 0)

    def test_projected_genji_reflects_frontally_then_spends_barrier_after_deflect(self):
        for frontal in (False, True):
            for adaptive in (False, True):
                with self.subTest(frontal=frontal, adaptive=adaptive):
                    self.check_projected_genji_cannon(frontal=frontal, adaptive=adaptive)

    def test_projected_genji_barrier_takes_over_at_world_deflect_deadline(self):
        for schedule in ('stepped', 'skipped'):
            for adaptive in (False, True):
                with self.subTest(schedule=schedule, adaptive=adaptive):
                    self.check_projected_genji_cannon(frontal=True, adaptive=adaptive, schedule=schedule)

    def test_projected_genji_takes_cannon_damage_when_both_defenses_expire(self):
        for schedule in ('stepped', 'skipped'):
            for adaptive in (False, True):
                with self.subTest(schedule=schedule, adaptive=adaptive):
                    self.check_projected_genji_cannon(
                        frontal=True, adaptive=adaptive, schedule=schedule, shared_end=True,
                    )

    def check_projected_genji_cannon(self, *, frontal, adaptive, schedule=None, shared_end=False):
        run, player, genji, zarya = self.projected_scene(ally_distance=4)
        self.assertTrue(run.match.switch_hero(genji, heroes()[deflection.GENJI]))
        x, y, z = player.mover.position
        relocate(run, genji, (x, y, z + 4))
        relocate(run, zarya, (x + 3.5, y, z + 4))
        genji.body_script.ult_seconds = 0
        genji.body_script.set_ult_charge(0)
        for _ in range(45):
            run.step({})
        facing = -32768 if frontal else 0
        activation = {genji: (facing, 0, 16), player: (0, 0, 16 if adaptive else 0)}
        if shared_end:
            activation[zarya] = (-16384, 0, 16)
        run.step(activation)
        held = {genji: (facing, 0, 0)}
        if shared_end:
            for _ in range(12):
                run.step({**held, zarya: (-16384, 0, 0)})
        else:
            for _ in range(40):
                run.step(held)
            self.cast_projected(run, zarya, yaw=-16384)
        run.step(held)

        fight = run.match.combat
        bubble, = fight.barriers.values()
        self.assertIs(bubble.recipient, genji)
        reflect = next(i for i in genji.body_script.component.instances.values() if i.graph.index == 0x03A3)
        self.assertTrue(reflect.state(10).active)
        vitals = {t.owner: fight.vitals_of(t) for t in fight._targets()}
        own = vitals[player]
        self.assertEqual(own.temporary_shields > 0, adaptive)
        initial_shield = own.temporary_shields
        ball_charge = player.body_script.component.entity_var(ULT_CHARGE)
        genji_charge = genji.body_script.component.entity_var(ULT_CHARGE)
        energy = zarya.body_script.component.entity_var(87)
        ammo = ball.instance(player, 0x1514).vars[53]
        angles = aim(fight._eye(player), (x, y + 0.55, z + 4))
        trigger = {**held, player: (*angles, 1)}
        if schedule is None:
            run.step(trigger)
        else:
            source = genji.body_script
            frozen = source.frame, source.component.now
            wait = reflect.state(1)
            self.assertTrue(wait.active)
            deadline = (wait.start + wait.ms) // source.frame_ms
            self.assertLess(run.frame, deadline - 2)
            if shared_end:
                self.assertEqual(deadline, bubble.expires)
                caster = zarya.body_script
                caster_frozen = caster.frame, caster.component.now
            else:
                self.assertLess(deadline, bubble.expires)

            def fire_at(frame):
                match = run.match
                ticks = (frame,) if schedule == 'skipped' else range(match.tick + 1, frame + 1)
                for tick in ticks:
                    match.update(tick * combat.TICK, tick, frames=False)
                player.take_commands([Command(frame, yaw=angles[0], pitch=angles[1], buttons=1)])

            fire_at(deadline - 2)
        self.assertEqual(ammo.value(), 79)
        expected_shield = initial_shield - (5 if frontal and adaptive else 0)
        permanent_damage = 2.5 if frontal and not adaptive else 0
        self.assertEqual(own.temporary_shields, expected_shield)
        self.assertEqual(own.total, 600 + expected_shield - permanent_damage)
        self.assertEqual(genji_charge.value(), permanent_damage)
        first_loss = 0 if frontal else 5
        self.assertEqual(bubble.vitals.total, 200 - first_loss)
        self.assertAlmostEqual(expr.to_float(energy.value()), first_loss / 5, places=4)
        self.assertEqual(vitals[genji].total, 200)
        self.assertEqual(ball_charge.value(), 0)

        if schedule is None:
            for _ in range(130):
                if not reflect.state(10).active:
                    break
                run.step(held)
            self.assertFalse(reflect.state(10).active)
        else:
            fire_at(deadline - 1)
            self.assertEqual(ammo.value(), 79)
        self.assertFalse(bubble.retired)
        energy_gains = []
        damage_barrier = barriers.damage

        def record_barrier_damage(*args, **kwargs):
            before = expr.to_float(energy.value())
            dealt = damage_barrier(*args, **kwargs)
            energy_gains.append(expr.to_float(energy.value()) - before)
            return dealt

        with patch.object(barriers, 'damage', side_effect=record_barrier_damage):
            if schedule is None:
                run.step(trigger)
            else:
                fire_at(deadline)
        if schedule is not None:
            self.assertEqual((source.frame, source.component.now), frozen)
            self.assertTrue(reflect.state(10).active)
            genji.take_commands([Command(deadline, yaw=facing)])
            self.assertGreater(source.frame, frozen[0])
        if shared_end:
            self.assertEqual((caster.frame, caster.component.now), caster_frozen)
            zarya.take_commands([Command(deadline, yaw=-16384)])
            self.assertGreater(caster.frame, caster_frozen[0])
        self.assertEqual(ammo.value(), 78)
        self.assertEqual(bubble.retired, shared_end)
        if shared_end:
            self.assertEqual(bubble.vitals.total, 200)
            self.assertFalse(fight.barriers)
            self.assertEqual(expr.to_float(energy.value()), 0)
            self.assertFalse(energy_gains)
        else:
            self.assertEqual(bubble.vitals.total, 195 - first_loss)
            gain, = energy_gains
            self.assertAlmostEqual(gain, 1, places=4)
        genji_damage = 5 if shared_end else 0
        self.assertEqual(own.total, 600 + expected_shield - permanent_damage)
        self.assertEqual(vitals[genji].total, 200 - genji_damage)
        self.assertEqual(genji_charge.value(), permanent_damage)
        self.assertEqual(ball_charge.value(), genji_damage)
        self.assertEqual(len(player.client.messages(combat.HIT)), int(shared_end))
        self.assertEqual(len(genji.client.messages(combat.HIT)), int(frontal))
        if shared_end:
            for tick in range(deadline + 1, deadline + 16):
                fire_at(tick)
                if ammo.value() == 77:
                    break
            self.assertEqual(ammo.value(), 77)
            self.assertFalse(fight.barriers)
            self.assertEqual(vitals[genji].total, 190)
            self.assertEqual(ball_charge.value(), 10)
            self.assertEqual(genji_charge.value(), permanent_damage)
            self.assertEqual(own.total, 600 + expected_shield - permanent_damage)
            self.assertEqual(expr.to_float(energy.value()), 0)
            self.assertEqual(len(player.client.messages(combat.HIT)), 2)
            self.assertEqual(len(genji.client.messages(combat.HIT)), 1)

    def test_projected_barrier_protects_ball_from_its_reflected_cannon_until_expiry(self):
        run, player, genji, zarya = self.projected_scene(ally_distance=4)
        zarya.team = player.team
        run.match.switch_hero(genji, heroes()[deflection.GENJI])
        x, y, z = player.mover.position
        relocate(run, genji, (x, y, z + 4))
        relocate(run, zarya, (x + 3.5, y, z))
        genji.body_script.ult_seconds = 0
        genji.body_script.set_ult_charge(0)
        for _ in range(60):
            run.step({})
        self.cast_projected(run, zarya, yaw=-16384)
        fight = run.match.combat
        bubble, = fight.barriers.values()
        self.assertIs(bubble.recipient, player)
        for _ in range(40):
            run.step({})
        run.step({genji: (-32768, 0, 16)})
        for _ in range(5):
            run.step({genji: (-32768, 0, 0)})
        reflect = next(i for i in genji.body_script.component.instances.values() if i.graph.index == 0x03A3)
        self.assertTrue(reflect.state(10).active)
        vitals = {target.owner: fight.vitals_of(target) for target in fight._targets()}
        genji_charge = genji.body_script.component.entity_var(ULT_CHARGE)
        ball_charge = player.body_script.component.entity_var(ULT_CHARGE)
        energy = zarya.body_script.component.entity_var(87)
        weapon = ball.instance(player, 0x1514)
        angles = aim(fight._eye(player), (x, y + 0.55, z + 4))
        run.step({player: (*angles, 1), genji: (-32768, 0, 0)})
        self.assertEqual(weapon.vars[53].value(), 79)
        self.assertEqual(bubble.vitals.total, 195)
        self.assertAlmostEqual(energy.value(), 1, places=4)
        self.assertEqual(vitals[player].total, 600)
        self.assertEqual(vitals[genji].total, 200)
        self.assertEqual(genji_charge.value(), 0)
        self.assertFalse(genji.client.messages(combat.HIT))

        for _ in range(140):
            if bubble.retired:
                break
            run.step({genji: (-32768, 0, 0)})
        self.assertTrue(bubble.retired)
        self.assertTrue(reflect.state(10).active)
        run.step({player: (*angles, 1), genji: (-32768, 0, 0)})
        self.assertEqual(weapon.vars[53].value(), 78)
        self.assertEqual(vitals[player].total, 597.5)
        self.assertEqual(vitals[genji].total, 200)
        self.assertEqual(genji_charge.value(), 2.5)
        self.assertEqual(len(genji.client.messages(combat.HIT)), 1)
        self.assertEqual(ball_charge.value(), 0)
        self.assertFalse(player.client.messages(combat.HIT))

    def test_zarya_projected_cast_rejects_enemy_and_off_aim_without_spending_cooldown(self):
        for case in ('enemy', 'aim', 'wall', 'range', 'free_for_all'):
            with self.subTest(case=case):
                run, player, ally, zarya = self.projected_scene(40 if case == 'range' else 5)
                x, y, z = player.mover.position
                if case == 'enemy':
                    ally.team = player.team
                elif case == 'wall':
                    wall = z + 3
                    run.match.collision_world = World([
                        (x - 3, y, wall, x + 3, y, wall, x + 3, y + 4, wall),
                        (x - 3, y, wall, x + 3, y + 4, wall, x - 3, y + 4, wall),
                    ])
                elif case == 'free_for_all':
                    run.match.game_map = replace(run.match.game_map, free_for_all=True)
                self.cast_projected(run, zarya, yaw=0 if case == 'aim' else -32768)
                self.assertFalse(run.match.combat.barriers)
                ability = next(
                    i for i in zarya.body_script.component.instances.values() if i.graph.index == 0x25C
                )
                self.assertEqual(ability.vars[61].value(), 0)

    def test_ana_dart_spends_dynamic_barrier_health_without_sleeping_ball(self):
        for hero, distance, cover in (
            (barriers.REINHARDT, 3, False), (barriers.ZARYA, 9, False),
            (barriers.WINSTON, 7, False), (barriers.REINHARDT, 3, True),
        ):
            with self.subTest(hero=hero, cover=cover):
                run = Run(KANEZAKA_DM, ('Ana', 'Ball', 'Protector'))
                caster, player, protector = run.players
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                caster.team, player.team, protector.team = 0, 1, 1
                run.match.bots.clear()
                run.match._pending_bot_scripts.clear()
                run.match.switch_hero(caster, heroes()[0x02E000000000013B])
                run.match.switch_hero(player, heroes()[ball.HERO])
                run.match.switch_hero(protector, heroes()[hero])
                x, y, z = caster.mover.position
                player.mover.position[:] = [x, y, z + 6]
                protector.mover.position[:] = [x, y, z + distance]
                for _ in range(90):
                    run.step({})
                hold_shield = 2 if hero == barriers.REINHARDT else 0
                if hero in (barriers.ZARYA, barriers.WINSTON):
                    run.step({protector: (-32768, 0, 16)})
                for _ in range(40 if hold_shield else 12):
                    run.step({protector: (-32768, 0, hold_shield)})
                fight = run.match.combat
                shield = next(iter(fight.barriers.values()))
                self.assertIs(shield.parent, protector)
                if hero == barriers.ZARYA:
                    self.assertIs(shield.recipient, player)
                initial = shield.vitals.total
                health = fight.vitals[player.body].total
                if cover:
                    wall = z + 1
                    run.match.collision_world = World([
                        (x - 3, y - 1, wall, x + 3, y - 1, wall, x + 3, y + 4, wall),
                        (x - 3, y - 1, wall, x + 3, y + 4, wall, x - 3, y + 4, wall),
                    ])
                angles = aim(fight._eye(caster), (x, y + 1.2, z + 6))
                flew = False
                for frame in range(65):
                    run.step({caster: (*angles, 8 if frame == 0 else 0),
                              protector: (-32768, 0, hold_shield)})
                    flew |= any(
                        rocket.direct and rocket.direct.effect == combat.SLEEP_EFFECT
                        for rocket in fight.rockets
                    )
                self.assertTrue(flew)
                self.assertFalse(fight.rockets)
                self.assertEqual(shield.vitals.total, initial if cover else initial - 5)
                self.assertEqual(fight.vitals[player.body].total, health)
                self.assertFalse(fight.is_stunned(player))
                self.assertFalse(fight.is_stunned(protector))
                self.assertFalse(caster.client.messages(combat.HIT))
                if hero == barriers.ZARYA:
                    self.assertAlmostEqual(protector.body_script.component.entity_var(87).value(), 1)

    def test_reinhardt_shield_facing_controls_flashbang_cover_for_ball(self):
        for facing, remaining in ((-32768, 1600), (-32768, 10), (0, 1600)):
            with self.subTest(facing=facing, remaining=remaining):
                run = Run(KANEZAKA_DM, ('McCree', 'Ball', 'Reinhardt'))
                caster, player, rein = run.players
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                caster.team, player.team, rein.team = 0, 1, 1
                run.match.bots.clear()
                run.match._pending_bot_scripts.clear()
                run.match.switch_hero(caster, heroes()[0x02E0000000000042])
                run.match.switch_hero(player, heroes()[ball.HERO])
                run.match.switch_hero(rein, heroes()[barriers.REINHARDT])
                x, y, z = caster.mover.position
                relocate(run, rein, (x, y, z + 3))
                relocate(run, player, (x, y, z + 4))
                for _ in range(90):
                    run.step({})
                shield = self.raise_barrier(run, rein, facing)
                fight = run.match.combat
                if remaining < 1600:
                    barriers.damage(fight, shield, 1600 - remaining, attacker=caster.body)
                angles = aim(fight._eye(caster), (x, y + 1, z + 3))
                for frame in range(40):
                    run.step({caster: (*angles, 16 if frame == 0 else 0), rein: (facing, 0, 2)})
                blocked = facing == -32768
                self.assertEqual(shield.vitals.total, max(0, remaining - 25) if blocked else remaining)
                self.assertEqual(fight.vitals[player.body].total, 600 if blocked else 580)
                self.assertEqual(fight.is_stunned(player), not blocked)
                self.assertEqual(fight.is_stunned(rein), not blocked)

    def test_projected_bubble_blocks_flashbang_damage_and_stun_on_ball(self):
        run, caster, player, zarya = self.projected_scene(ally_distance=3)
        origin = tuple(caster.mover.position)
        recipient = tuple(player.mover.position)
        run.match.switch_hero(caster, heroes()[0x02E0000000000042])
        run.match.switch_hero(player, heroes()[ball.HERO])
        relocate(run, caster, origin)
        relocate(run, player, recipient)
        for _ in range(40):
            run.step({})
        self.cast_projected(run, zarya)
        fight = run.match.combat
        bubble, = fight.barriers.values()
        self.assertIs(bubble.recipient, player)
        x, y, z = recipient
        angles = aim(fight._eye(caster), (x, y + 1, z))
        for frame in range(40):
            run.step({caster: (*angles, 16 if frame == 0 else 0)})
        self.assertEqual(bubble.vitals.total, 175)
        self.assertEqual(fight.vitals[player.body].total, 600)
        self.assertFalse(fight.is_stunned(player))
        hit, = caster.client.messages(combat.HIT)
        self.assertEqual(hit['+0x78']['+0x0']['+0x0'], zarya.body)
        self.assertTrue(fight.is_stunned(zarya))
        self.assertAlmostEqual(zarya.body_script.component.entity_var(87).value(), 5)

    def test_projected_barrier_intercepts_ana_rifle_or_cleanses_its_remaining_dose(self):
        for scoped, already_hit in ((False, False), (True, False), (False, True), (True, True)):
            with self.subTest(scoped=scoped, already_hit=already_hit):
                run, ana, player, zarya = self.projected_scene(ally_distance=3)
                origin, recipient = tuple(ana.mover.position), tuple(player.mover.position)
                self.assertTrue(run.match.switch_hero(ana, heroes()[ANA]))
                self.assertTrue(run.match.switch_hero(player, heroes()[ball.HERO]))
                relocate(run, ana, origin)
                relocate(run, player, recipient)
                for _ in range(40):
                    run.step({})
                fight = run.match.combat
                own = fight.vitals_of(next(t for t in fight._targets() if t.owner is player))
                x, y, z = recipient
                shot = aim(fight._eye(ana), (x, y + 1, z))
                if not already_hit:
                    self.cast_projected(run, zarya)
                if scoped:
                    for _ in range(35):
                        run.step({ana: (*shot, 2)})
                run.step({ana: (*shot, 3 if scoped else 1)})
                for _ in range(10):
                    run.step({})
                if already_hit:
                    self.assertTrue(fight.rifle_doses)
                    self.assertLess(own.total, 600)
                    self.cast_projected(run, zarya)
                bubble, = barriers.projected_on(fight, player.body)
                after = own.total
                for _ in range(45):
                    run.step({})
                self.assertEqual(own.total, after)
                self.assertFalse(fight.rifle_doses)
                self.assertEqual(bubble.vitals.total, 200 if already_hit else 130)
                energy = zarya.body_script.component.entity_var(87).value()
                self.assertAlmostEqual(energy or 0, 0 if already_hit else 14)
                if already_hit:
                    self.assertGreater(after, 530)
                else:
                    self.assertEqual(after, 600)

    def test_projected_bubble_blocks_ana_grenade_damage_and_healing_denial_on_ball(self):
        run, ana, player, zarya = self.projected_scene(ally_distance=3)
        origin = tuple(ana.mover.position)
        recipient = tuple(player.mover.position)
        run.match.switch_hero(ana, heroes()[0x02E000000000013B])
        run.match.switch_hero(player, heroes()[ball.HERO])
        relocate(run, ana, origin)
        relocate(run, player, recipient)
        for _ in range(40):
            run.step({})
        self.cast_projected(run, zarya)
        fight = run.match.combat
        bubble, = fight.barriers.values()
        self.assertIs(bubble.recipient, player)
        x, y, z = recipient
        angles = aim(fight._eye(ana), (x, y + 1, z))
        for frame in range(40):
            run.step({ana: (*angles, 16 if frame == 0 else 0)})
        self.assertEqual(bubble.vitals.total, 150)
        own = fight.vitals[player.body]
        self.assertEqual(own.total, 600)
        self.assertFalse(own.healing_block)

    def test_projected_barrier_intercepts_waking_damage_on_sleeping_ball(self):
        for projected in (False, True):
            with self.subTest(projected=projected):
                run, ana, player, zarya = self.projected_scene(
                    ally_distance=3, extra_players=('Enemy Soldier',),
                )
                soldier = run.players[3]
                soldier.team = ana.team
                x, y, z = player.mover.position
                origin = tuple(ana.mover.position)
                self.assertTrue(run.match.switch_hero(ana, heroes()[ANA]))
                self.assertTrue(run.match.switch_hero(player, heroes()[ball.HERO]))
                relocate(run, ana, origin)
                relocate(run, player, (x, y, z))
                relocate(run, soldier, (x + 6, y, z))
                for _ in range(40):
                    run.step({})
                fight = run.match.combat
                target = next(t for t in fight._targets() if t.owner is player)
                own = fight.vitals_of(target)
                script = player.body_script
                script.ult_seconds = 0
                cost = script.component.entity_var(ULT_COST).value()
                script.set_ult_charge(cost)
                charge = script.component.entity_var(ULT_CHARGE)
                run.step({player: (0, 0, 16)})
                shield = own.temporary_shields
                start, original_end = BallControlTests().sleep_dart(run, ana, player)
                self.assertEqual(original_end - start, 313)
                self.assertEqual(own.temporary_shields, shield - 5)
                for _ in range(34):
                    run.step({player: (0, 0, 32)})
                self.assertEqual(charge.value(), cost)
                if projected:
                    self.cast_projected(run, zarya)
                    bubble, = barriers.projected_on(fight, player.body)
                    self.assertEqual(bubble.vitals.total, 200)
                    self.assertEqual(fight.sleeps[player.body], (start, original_end))

                angles = aim(fight._eye(soldier), (x, y + 1, z))
                for frame in range(16):
                    run.step({soldier: (*angles, 1 if frame == 0 else 0), player: (0, 0, 32)})
                    if own.temporary_shields < shield - 5 or (projected and bubble.vitals.total < 200):
                        break
                end = fight.sleeps[player.body][1]
                if projected:
                    self.assertEqual(bubble.vitals.total, 180)
                    self.assertAlmostEqual(zarya.body_script.component.entity_var(87).value(), 4)
                    self.assertEqual(own.temporary_shields, shield - 5)
                    self.assertEqual(end, original_end)
                else:
                    self.assertEqual(own.temporary_shields, shield - 25)
                    self.assertEqual(end, run.frame + 32)
                    self.assertLess(end, original_end)
                self.assertEqual(charge.value(), cost)
                self.assertFalse(fight.mines.entities)

                run.frame = end - 2
                run.match.update(run.frame * combat.TICK, run.frame, frames=False)
                run.step({player: (0, 0, 32)})
                self.assertTrue(fight.is_stunned(player))
                self.assertEqual(charge.value(), cost)
                if projected:
                    self.assertFalse(fight.barriers)
                run.step({player: (0, 0, 32)})
                self.assertFalse(fight.is_stunned(player))
                self.assertEqual(charge.value(), 0)
                self.assertFalse(hasattr(script, 'timed_control_commands'))

    def test_projected_barrier_cleanses_ball_antiheal_and_preserves_healing_amplification(self):
        run, enemy, player, zarya = self.projected_scene(
            caster_distance=8, ally_distance=3, extra_players=('Zenyatta', 'Friendly Ana'),
        )
        zen, ally = run.players[3:]
        zen.team = ally.team = player.team
        x, y, z = player.mover.position
        for actor, hero, position in (
            (enemy, ANA, tuple(enemy.mover.position)),
            (player, ball.HERO, (x, y, z)),
            (zen, phase.ZENYATTA, (x - 6, y, z)),
            (ally, ANA, (x + 6, y, z)),
        ):
            self.assertTrue(run.match.switch_hero(actor, heroes()[hero]))
            relocate(run, actor, position)
        for _ in range(40):
            run.step({})
        fight = run.match.combat
        own = fight.vitals_of(next(t for t in fight._targets() if t.owner is player))
        own.damage(200)
        fight._sync_body_health(player, own)
        run.step({player: (0, 0, 16)})
        total, shield = own.total, own.temporary_shields
        for caster, status in ((enemy, 'healing_block'), (ally, 'healing_boost')):
            angles = aim(fight._eye(caster), (x, y + 1, z))
            for frame in range(45):
                run.step({caster: (*angles, 16 if frame == 0 else 0)})
                if getattr(own, status):
                    break
            self.assertTrue(getattr(own, status))
        self.assertEqual(own.total, total - 50)
        self.assertEqual(own.temporary_shields, shield - 50)
        boost = own.healing_boost
        deadline = own.healing_block[-1][1]
        phase.BallPhaseTests().cast_transcendence(run, zen)

        # A cast facing away cannot cleanse or let the running aura heal.
        self.cast_projected(run, zarya, yaw=0)
        self.assertFalse(fight.barriers)
        self.assertEqual(own.total, total - 50)
        self.assertTrue(own.healing_block)

        run.step({zarya: (-32768, 0, 16)})
        for _ in range(12):
            bubbles = list(barriers.projected_on(fight, player.body))
            if bubbles:
                break
            run.step({zarya: (-32768, 0, 0)})
        bubble, = bubbles
        self.assertEqual(own.total, total - 50)
        run.step({})
        self.assertLess(run.frame, deadline)
        self.assertAlmostEqual(own.total, total - 50 + 7.2)
        self.assertFalse(own.healing_block)
        self.assertEqual(own.healing_boost, boost)
        self.assertEqual(own.temporary_shields, shield - 50)
        self.assertEqual(bubble.vitals.total, 200)
        self.assertEqual(zarya.stats.value(stats.HEALING), 0)

        run.frame += 8
        run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        self.assertAlmostEqual(own.total, total - 50 + 9 * 7.2)
        self.assertEqual(own.temporary_shields, shield - 50)
        self.assertEqual(bubble.vitals.total, 200)

    def test_zarya_bubble_consumes_piledriver_and_fireball_without_body_impulse(self):
        for attack in ('Piledriver', 'Fireball'):
            with self.subTest(attack=attack):
                pile = attack == 'Piledriver'
                distance = 1.5 if pile else 6
                run, player, zarya, _, _ = scenes.BallCombatTests().scenario(distance=distance) if pile else (
                    swing_scene()
                )
                run.match.switch_hero(zarya, heroes()[barriers.ZARYA])
                x, y, z = player.mover.position
                zarya.mover.position[:] = [x, y, z + distance]
                for _ in range(90):
                    run.step({})
                player.body_script.ult_seconds = 0
                player.body_script.set_ult_charge(0)
                fight = run.match.combat
                target = next(t for t in fight._targets() if t.owner is zarya)
                lift(run, player)
                run.step({zarya: (-32768, 0, 8), player: (0, 0, 128 if pile else 2)})
                bubble = next(iter(fight.barriers.values()))
                for _ in range(100):
                    if pile:
                        run.step({})
                    else:
                        hold(run, player, 1)
                    if bubble.vitals.total < 200:
                        break
                if pile:
                    self.assertEqual(pile_instance(player).states[7].counter, 1)
                else:
                    self.assertTrue(ball.instance(player, 0x1203).states[35].active)
                damage = 100 if pile else 50
                self.assertEqual(bubble.vitals.total, 200 - damage)
                self.assertAlmostEqual(zarya.body_script.component.entity_var(87).value(), damage / 5)
                self.assertEqual(fight.vitals_of(target).total, 400)
                self.assertEqual(tuple(zarya.mover.velocity), (0, 0, 0))
                self.assertFalse(zarya.mover.airborne)
                self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), 0)

    def test_projected_bubble_consumes_piledriver_and_credits_caster(self):
        run, player, ally, zarya = self.projected_scene(caster_distance=14)
        lift(run, player)
        self.cast_projected(run, zarya)
        fight = run.match.combat
        bubble = next(iter(fight.barriers.values()))
        self.assertIs(bubble.recipient, ally)
        run.step({player: (0, 0, 128)})
        for _ in range(100):
            run.step({})
            if bubble.vitals.total < 200:
                break
        self.assertEqual(pile_instance(player).states[7].counter, 1)
        self.assertEqual(bubble.vitals.total, 100)
        self.assertAlmostEqual(zarya.body_script.component.entity_var(87).value(), 20)
        self.assertEqual(fight.vitals[ally.body].total, 200)
        self.assertFalse(ally.mover.airborne)
        self.assertEqual(tuple(ally.mover.velocity), (0, 0, 0))
        self.assertNotIn(ally.body, fight.pile_air_locks)
        self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), 0)

    def test_projected_bubble_consumes_actual_fireball_without_lift(self):
        run, player, ally, zarya = self.projected_scene(caster_distance=10, ally_distance=6)
        prepare_swing(run, player)
        fight = run.match.combat
        run.step({zarya: (-32768, 0, 16), player: (0, 0, 2)})
        for _ in range(12):
            if fight.barriers:
                break
            run.step({zarya: (-32768, 0, 0), player: (0, 0, 2)})
        bubble, = fight.barriers.values()
        self.assertIs(bubble.recipient, ally)
        for _ in range(100):
            hold(run, player, 1)
            if bubble.vitals.total < 200:
                break
        self.assertTrue(ball.instance(player, 0x1203).states[35].active)
        self.assertEqual(bubble.vitals.total, 150)
        self.assertAlmostEqual(zarya.body_script.component.entity_var(87).value(), 10)
        self.assertEqual(fight.vitals[ally.body].total, 200)
        self.assertEqual(tuple(ally.mover.velocity), (0, 0, 0))
        self.assertFalse(ally.mover.airborne)
        self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), 0)

    def winston_scene(self, distance, *, cast=True):
        run, player, winston, _, _ = scenes.BallCombatTests().scenario(distance=distance)
        run.match.switch_hero(winston, heroes()[barriers.WINSTON])
        x, y, z = player.mover.position
        winston.mover.position[:] = [x, y, z + distance]
        for _ in range(90):
            run.step({})
        player.body_script.ult_seconds = 0
        player.body_script.set_ult_charge(0)
        if not cast:
            return run, player, winston, None
        run.step({winston: (-32768, 0, 16)})
        fight = run.match.combat
        for _ in range(45):
            if fight.barriers:
                break
            run.step({})
        dome, = fight.barriers.values()
        self.assertEqual(dome.vitals.total, 700)
        child, = dome.component.instances.values()
        self.assertEqual(child.graph.index, 0xFC)
        return run, player, winston, dome

    def test_grapple_passes_live_enemy_barriers_and_requires_static_anchor(self):
        for hero, wall in ((barriers.REINHARDT, 12), (barriers.WINSTON, 12), (barriers.WINSTON, None)):
            with self.subTest(hero=hex(hero), wall=wall):
                run, player, defender, _, _ = swing_scene(wall)
                run.match.switch_hero(defender, heroes()[hero])
                x, y, z = player.mover.position
                relocate(run, defender, (x, y, z + 7))
                for _ in range(40):
                    run.step({})
                fight = run.match.combat
                facing = -32768
                if hero == barriers.REINHARDT:
                    shield = self.raise_barrier(run, defender, facing)
                    keep_shield = 2
                else:
                    run.step({defender: (facing, 0, 16)})
                    for _ in range(45):
                        if fight.barriers:
                            break
                        run.step({})
                    shield, = fight.barriers.values()
                    keep_shield = 0
                health = shield.vitals.total
                intercept = barriers.ray_hit(fight, player, fight._eye(player), (0, 0, 1), 12)
                self.assertIsNotNone(intercept)
                self.assertIs(intercept[1], shield)
                self.assertLess(intercept[0], 12)
                run.step({player: (0, 0, 2), defender: (facing, 0, keep_shield)})
                tether = fight.ball_grapples[player.body]
                self.assertFalse(tether.attached)
                if wall is None:
                    self.assertIsNone(tether.anchor)
                else:
                    self.assertAlmostEqual(tether.anchor[2], z + wall)
                while run.frame < tether.arrives:
                    run.step({player: (0, 0, 2), defender: (facing, 0, keep_shield)})
                if wall is None:
                    self.assertNotIn(player.body, fight.ball_grapples)
                else:
                    self.assertTrue(tether.attached)
                    self.assertIs(fight.ball_grapples[player.body], tether)
                    run.step({defender: (facing, 0, keep_shield)})
                    self.assertNotIn(player.body, fight.ball_grapples)
                self.assertGreater(ball.instance(player, ball.GRAPPLE).states[25].remaining(), 4.8)
                self.assertEqual(shield.vitals.total, health)
                self.assertFalse(player.client.messages(combat.HIT))

    def test_piledriver_lifts_target_across_winston_shell_without_damaging_dome(self):
        for dome_offset in (6, -4.5):
            with self.subTest(dome_offset=dome_offset):
                run = Run(KANEZAKA_DM, ('Ball', 'Target', 'Winston'))
                player, target, winston = run.players
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                player.team, target.team, winston.team = 0, 1, 1
                run.match.bots.clear()
                run.match._pending_bot_scripts.clear()
                run.match.switch_hero(player, heroes()[ball.HERO])
                run.match.switch_hero(winston, heroes()[barriers.WINSTON])
                x, y, z = player.mover.position
                relocate(run, target, (x, y, z + 1.5))
                relocate(run, winston, (x, y, z + dome_offset))
                for _ in range(90):
                    run.step({})
                run.step({winston: (0, 0, 16)})
                fight = run.match.combat
                for _ in range(45):
                    if fight.barriers:
                        break
                    run.step({})
                dome, = fight.barriers.values()
                self.assertIs(dome.parent, winston)
                self.assertEqual(dome.vitals.total, 700)
                relocate(run, winston, (x + 30, y, z))
                self.assertEqual(math.dist(target.mover.position, dome.centre) < barriers.DOME_RADIUS,
                                 dome_offset > 0)
                player.body_script.ult_seconds = 0
                player.body_script.set_ult_charge(0)
                lift(run, player)
                run.step({player: (0, 0, 128)})
                instance = pile_instance(player)
                for _ in range(90):
                    run.step({})
                    if instance.state(7).counter:
                        break
                self.assertEqual(instance.state(7).counter, 1)
                impact = tuple(instance.vars[1175].value())
                self.assertEqual(math.dist(impact, dome.centre) < barriers.DOME_RADIUS, dome_offset < 0)
                self.assertEqual(fight.vitals[target.body].total, 100)
                self.assertTrue(target.mover.airborne)
                self.assertGreater(target.mover.velocity[1], 0)
                self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), 100)
                self.assertEqual(dome.vitals.total, 700)
                self.assertFalse(dome.retired)

    def test_fireball_contacts_body_across_winston_shell_without_damaging_dome(self):
        run = Run(KANEZAKA_DM, ('Ball', 'Target', 'Winston'))
        player, target, winston = run.players
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        player.team, target.team, winston.team = 0, 1, 1
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        run.match.switch_hero(player, heroes()[ball.HERO])
        run.match.switch_hero(winston, heroes()[barriers.WINSTON])
        x, y, z = player.mover.position
        relocate(run, target, (x, y, z + 6.5))
        relocate(run, winston, (x, y, z + 11))
        prepare_swing(run, player)
        player.body_script.ult_seconds = 0
        player.body_script.set_ult_charge(0)
        run.step({winston: (-32768, 0, 16), player: (0, 0, 2)})
        fight = run.match.combat
        victim = next(t for t in fight._targets() if t.owner is target)
        vitals = fight.vitals_of(victim)
        for _ in range(100):
            if victim.entity in fight.ball_contacts.get(player.body, ()):
                break
            hold(run, player, 1)
        self.assertIn(victim.entity, fight.ball_contacts.get(player.body, ()))
        self.assertTrue(ball.instance(player, 0x1203).states[35].active)
        dome, = fight.barriers.values()
        self.assertGreater(math.dist(player.mover.position, dome.centre), barriers.DOME_RADIUS)
        self.assertLess(math.dist(victim.feet, dome.centre), barriers.DOME_RADIUS)
        self.assertEqual(dome.vitals.total, 700)
        self.assertEqual(vitals.total, 150)
        self.assertGreater(target.mover.velocity[1], 4)
        self.assertGreater(target.mover.velocity[2], 20)
        self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), 50)

    def test_winston_dome_blocks_flashbang_across_shell_but_not_on_same_side(self):
        for caster_offset, ball_offset, hostile_shell, blocked in (
            (7, 3, True, True), (3, 7, True, True), (3, 1, True, False),
            (7, 9, True, False), (7, 3, False, False),
        ):
            with self.subTest(caster=caster_offset, target=ball_offset, hostile_shell=hostile_shell):
                run = Run(KANEZAKA_DM, ('McCree', 'Ball', 'Winston'))
                caster, player, winston = run.players
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                caster.team, player.team, winston.team = 0, 1, int(hostile_shell)
                run.match.bots.clear()
                run.match._pending_bot_scripts.clear()
                run.match.switch_hero(caster, heroes()[0x02E0000000000042])
                run.match.switch_hero(player, heroes()[ball.HERO])
                run.match.switch_hero(winston, heroes()[barriers.WINSTON])
                x, y, z = winston.mover.position
                relocate(run, caster, (x, y, z + caster_offset))
                relocate(run, player, (x, y, z + ball_offset))
                for _ in range(90):
                    run.step({})
                run.step({winston: (0, 0, 16)})
                fight = run.match.combat
                for _ in range(45):
                    if fight.barriers:
                        break
                    run.step({})
                dome, = fight.barriers.values()
                self.assertIs(dome.parent, winston)
                self.assertEqual(dome.vitals.total, 700)
                angles = aim(fight._eye(caster), (x, y + 1, z + ball_offset))
                for frame in range(40):
                    run.step({caster: (*angles, 16 if frame == 0 else 0)})
                self.assertEqual(dome.vitals.total, 675 if blocked else 700)
                self.assertEqual(fight.vitals[player.body].total, 600 if blocked else 580)
                self.assertEqual(fight.is_stunned(player), not blocked)

    def test_winston_dome_intercepts_cannon_from_outside_and_expires(self):
        run, player, winston, dome = self.winston_scene(7)
        fight = run.match.combat
        child, = dome.component.instances.values()
        timer = child.states[11]
        target = next(t for t in fight._targets() if t.owner is winston)
        initial = fight.vitals_of(target).total
        x, y, z = winston.mover.position
        angles = aim(fight._eye(player), (x, y + 1, z))
        run.step({player: (*angles, 1)})
        self.assertEqual(dome.vitals.total, 695)
        self.assertEqual(fight.vitals_of(target).total, initial)
        self.assertFalse(player.client.messages(combat.HIT))
        self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), 0)
        for _ in range(650):
            run.step({})
            if dome.retired:
                break
        self.assertTrue(dome.retired)
        self.assertFalse(fight.barriers)
        self.assertAlmostEqual(timer.ended_at - timer.start, 9000, delta=16)
        run.step({player: (*angles, 1)})
        self.assertLess(fight.vitals_of(target).total, initial)

    def test_winston_dome_cannon_boundary_without_caster_commands(self):
        for before in (False, True):
            with self.subTest(before=before):
                run, player, winston, dome = self.winston_scene(7)
                match = run.match
                fight = match.combat
                source = winston.body_script
                frozen = source.frame, source.component.now
                child, = dome.component.instances.values()
                timer = child.state(11)
                seconds = expr.to_float(timer.evaluate(timer.node.field('m_EB0FC261'), watch=False))
                self.assertEqual(seconds, 9)
                deadline = (timer.start + round(seconds * 1000)) // dome.component.frame_ms
                run.frame = deadline - int(before)
                match.update(run.frame * combat.TICK, run.frame, frames=False)
                self.assertEqual((source.frame, source.component.now), frozen)
                own = fight.vitals_of(next(t for t in fight._targets() if t.owner is winston))
                initial = own.total
                x, y, z = winston.mover.position
                angles = aim(fight._eye(player), (x, y + 1, z))
                player.take_commands([Command(run.frame, yaw=angles[0], pitch=angles[1], buttons=1)])
                self.assertEqual(dome.vitals.total, 695 if before else 700)
                self.assertEqual(dome.retired, not before)
                self.assertEqual(own.total, initial if before else initial - 2.5)
                charge = player.body_script.component.entity_var(ULT_CHARGE)
                self.assertEqual(charge.value(), 0 if before else 2.5)

    def test_late_winston_dome_preserves_its_original_world_lifetime(self):
        for elapsed in (60, 650):
            with self.subTest(elapsed=elapsed):
                self.check_late_winston_dome(elapsed)

    def check_late_winston_dome(self, elapsed):
        run, player, winston, _ = self.winston_scene(7, cast=False)
        match = run.match
        fight = match.combat
        start = run.frame + 1
        run.frame = start + elapsed
        match.update(run.frame * combat.TICK, run.frame, frames=False)
        next_barrier = fight.next_barrier
        winston.take_commands([Command(start, yaw=-32768, buttons=16)])
        winston.take_commands([Command(start + 40, yaw=-32768)])
        self.assertGreater(fight.next_barrier, next_barrier)
        x, y, z = winston.mover.position
        angles = aim(fight._eye(player), (x, y + 1, z))
        own = fight.vitals_of(next(t for t in fight._targets() if t.owner is winston))
        initial = own.total
        player.take_commands([Command(run.frame, yaw=angles[0], pitch=angles[1], buttons=1)])
        if elapsed == 650:
            self.assertEqual(own.total, initial - 2.5)
            self.assertFalse(fight.barriers)
            return
        dome, = fight.barriers.values()
        self.assertEqual(own.total, initial)
        self.assertEqual(dome.vitals.total, 695)
        self.assertEqual(dome.component.frame, run.frame)
        child, = dome.component.instances.values()
        timer = child.state(11)
        self.assertLess(timer.start // dome.component.frame_ms, run.frame)
        deadline = (timer.start + 9000) // dome.component.frame_ms
        match.update(deadline * combat.TICK, deadline, frames=False)
        self.assertTrue(dome.retired)

    def test_adaptive_shield_counts_winston_through_dome_but_not_dome_itself(self):
        for owner_near in (True, False):
            with self.subTest(owner_near=owner_near):
                run, player, winston, dome = self.winston_scene(7)
                fight = run.match.combat
                x, y, z = winston.mover.position
                angles = aim(fight._eye(player), (x, y + 1, z))
                if not owner_near:
                    relocate(run, winston, (x, y, z + 15))
                # Confirm the live shell still intercepts the same cannon path.
                run.step({player: (*angles, 1)})
                self.assertEqual(dome.vitals.total, 695)
                self.assertFalse(player.client.messages(combat.HIT))

                own = next(t for t in fight._targets() if t.owner is player)
                run.step({player: (0, 0, 16)})
                expected = 175 if owner_near else 100
                self.assertEqual(fight.vitals_of(own).temporary_shields, expected)
                self.assertEqual(dome.vitals.total, 695)
                self.assertFalse(dome.retired)

    def test_winston_shell_and_grasp_use_nearest_contact(self):
        for case in ('grasp_first', 'shell_first', 'inside'):
            with self.subTest(case=case):
                self.check_winston_shell_and_grasp_order(case)

    def check_winston_shell_and_grasp_order(self, case):
        run = Run(KANEZAKA_DM, ('Ball', 'Winston', 'Sigma'))
        player, winston, sigma = run.players
        match = run.match
        match.game_map = replace(match.game_map, free_for_all=False)
        match.bots.clear()
        match._pending_bot_scripts.clear()
        player.team, winston.team, sigma.team = 0, 1, 1
        for owner, hero in ((player, ball.HERO), (winston, barriers.WINSTON), (sigma, deflection.SIGMA)):
            self.assertTrue(match.switch_hero(owner, heroes()[hero]))
        x, y, z = player.mover.position
        relocate(run, winston, (x, y, z + 12))
        relocate(run, sigma, (x, y, z + (3 if case == 'grasp_first' else 13)))
        for _ in range(90):
            run.step({})
        fight = match.combat
        run.step({winston: (-32768, 0, 16)})
        for _ in range(45):
            if fight.barriers:
                break
            run.step({})
        dome, = fight.barriers.values()
        self.assertIs(dome.parent, winston)
        self.assertEqual(dome.vitals.total, 700)
        if case == 'inside':
            relocate(run, player, (x, y, z + 8))
        run.step({sigma: (-32768, 0, 8)})
        for _ in range(5):
            run.step({sigma: (-32768, 0, 0)})
        grasp = next(i for i in sigma.body_script.component.instances.values() if i.graph.index == 0x1D02)
        self.assertTrue(grasp.state(1).active)
        origin, direction = fight._eye(player), combat.aim(0, 0)
        shell = barriers.ray_hit(fight, player, origin, direction, 100)
        grasp_contact = deflection.intercept(fight, player, origin, direction, 100)
        self.assertIsNotNone(shell)
        self.assertIsNotNone(grasp_contact)
        self.assertIs(shell[1], dome)
        self.assertIs(grasp_contact[1], sigma)
        self.assertNotEqual(grasp_contact[0], shell[0])
        self.assertEqual(shell[0] < grasp_contact[0], case == 'shell_first')
        if case == 'inside':
            self.assertLess(math.dist(origin, dome.centre), barriers.DOME_RADIUS)
            self.assertLess(math.dist(fight._eye(sigma), dome.centre), barriers.DOME_RADIUS)

        vitals = [fight.vitals_of(t) for t in fight._enemies(player)]
        health = [v.total for v in vitals]
        player.body_script.ult_seconds = 0
        player.body_script.set_ult_charge(0)
        ammo = ball.instance(player, 0x1514).vars[53]
        absorbed = grasp.vars[21312]
        self.assertEqual(absorbed.value(), 0)
        run.step({player: (0, 0, 1), sigma: (-32768, 0, 0)})
        self.assertEqual(ammo.value(), 79)
        expected = (695, 0) if case == 'shell_first' else (700, 5)
        self.assertEqual((dome.vitals.total, absorbed.value()), expected)
        self.assertEqual([v.total for v in vitals], health)
        self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), 0)
        self.assertFalse(player.client.messages(combat.HIT))

    def test_winston_shell_and_grasp_compete_after_genji_deflect(self):
        for case, shell_loss, grasp_damage in (
            ('grasp_first', 0, 4.284909), ('shell_first', 3.575501, 0), ('inside', 0, 5),
        ):
            with self.subTest(case=case):
                actual_loss, absorbed = self.check_reflected_cannon_at_winston_shell(case)
                self.assertAlmostEqual(actual_loss, shell_loss, places=4)
                self.assertAlmostEqual(absorbed, grasp_damage, places=4)

    def check_reflected_cannon_at_winston_shell(self, case):
        run = Run(KANEZAKA_DM, ('Ball', 'Winston', 'Sigma', 'Genji'))
        player, winston, sigma, genji = run.players
        match = run.match
        match.game_map = replace(match.game_map, free_for_all=False)
        match.bots.clear()
        match._pending_bot_scripts.clear()
        player.team, winston.team, sigma.team, genji.team = 0, 0, 0, 1
        for owner, hero in ((player, ball.HERO), (winston, barriers.WINSTON), (sigma, deflection.SIGMA),
                            (genji, deflection.GENJI)):
            self.assertTrue(match.switch_hero(owner, heroes()[hero]))
            owner.body_script.ult_seconds = 0
            owner.body_script.set_ult_charge(0)
        x, y, z = player.mover.position
        genji_distance = 13 if case == 'inside' else 18
        relocate(run, winston, (x, y, z + 10))
        relocate(run, genji, (x, y, z + genji_distance))
        relocate(run, sigma, (x, y, z + (16.5 if case == 'grasp_first' else 9)))
        for _ in range(90):
            run.step({})
        fight = match.combat
        run.step({winston: (-32768, 0, 16)})
        for _ in range(45):
            if fight.barriers:
                break
            run.step({})
        dome, = fight.barriers.values()
        self.assertEqual(dome.vitals.total, 700)
        relocate(run, winston, (x + 12, y, z + 10))
        run.step({sigma: (0, 0, 8), genji: (-32768, 0, 16)})
        held = {sigma: (0, 0, 0), genji: (-32768, 0, 0)}
        for _ in range(5):
            run.step(held)
        if case == 'inside':
            for owner in (genji, sigma):
                self.assertLess(math.dist(fight._eye(owner), dome.centre), barriers.DOME_RADIUS)

        grasp = next(i for i in sigma.body_script.component.instances.values() if i.graph.index == 0x1D02)
        self.assertTrue(grasp.state(1).active)
        absorbed = grasp.vars[21312]
        self.assertEqual(absorbed.value(), 0)
        health = {t.owner: fight.vitals_of(t).total for t in fight._targets()}
        self.assertEqual(set(health), set(run.players))
        contacts = []
        return_ray = []
        intercept = deflection.intercept

        def record_intercept(fight, shooter, origin, direction, limit, **kwargs):
            result = intercept(fight, shooter, origin, direction, limit, **kwargs)
            contacts.append((shooter, result[1] if result else None))
            if shooter is genji:
                return_ray.append((origin, direction))
            return result

        angles = aim(fight._eye(player), (x, y + 0.55, z + genji_distance))
        with patch.object(deflection, 'intercept', side_effect=record_intercept):
            run.step({**held, player: (*angles, 1)})
        self.assertEqual(ball.instance(player, 0x1514).vars[53].value(), 79)
        shell_first = case == 'shell_first'
        self.assertEqual(contacts, [(player, genji), (genji, None if shell_first else sigma)])
        (origin, direction), = return_ray
        shell = barriers.ray_hit(fight, genji, origin, direction, 100)
        grasp_contact = intercept(fight, genji, origin, direction, 100)
        self.assertIsNotNone(shell)
        self.assertIsNotNone(grasp_contact)
        self.assertIs(shell[1], dome)
        self.assertIs(grasp_contact[1], sigma)
        self.assertNotEqual(shell[0], grasp_contact[0])
        self.assertEqual(shell[0] < grasp_contact[0], shell_first)
        if case == 'inside':
            self.assertLess(math.dist(origin, dome.centre), barriers.DOME_RADIUS)
        for owner, total in health.items():
            self.assertEqual(fight.vitals[owner.body].total, total)
            self.assertFalse(owner.client.messages(combat.HIT))
        for owner in (player, genji):
            self.assertEqual(owner.body_script.component.entity_var(ULT_CHARGE).value(), 0)
        return 700 - dome.vitals.total, absorbed.value()

    def test_winston_dome_does_not_block_cannon_at_body_inside_same_shell(self):
        run, player, winston, dome = self.winston_scene(1.5)
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is winston)
        initial = fight.vitals_of(target).total
        x, y, z = winston.mover.position
        angles = aim(fight._eye(player), (x, y + 1, z))
        run.step({player: (*angles, 1)})
        self.assertEqual(dome.vitals.total, 700)
        self.assertLess(fight.vitals_of(target).total, initial)
        self.assertGreater(player.body_script.component.entity_var(ULT_CHARGE).value(), 0)

    def test_two_actual_ball_cannons_break_winston_dome_without_spill(self):
        run = Run(KANEZAKA_DM, ('Ball A', 'Ball B', 'Winston'))
        first, second, winston = run.players
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        x, y, z = first.mover.position
        for shooter, offset in ((first, -1), (second, 1)):
            run.match.switch_hero(shooter, heroes()[ball.HERO])
            shooter.mover.position[:] = [x + offset, y, z]
        run.match.switch_hero(winston, heroes()[barriers.WINSTON])
        winston.mover.position[:] = [x, y, z + 7]
        for _ in range(90):
            run.step({})
        fight = run.match.combat
        for shooter in (first, second):
            shooter.body_script.ult_seconds = 0
            shooter.body_script.set_ult_charge(0)
        run.step({winston: (-32768, 0, 16)})
        for _ in range(45):
            if fight.barriers:
                break
            run.step({})
        dome, = fight.barriers.values()
        first_aim = aim(fight._eye(first), (x, y + 1, z + 7))
        second_aim = aim(fight._eye(second), (x, y + 1, z + 7))
        target = next(t for t in fight._targets() if t.owner is winston)
        vitals = fight.vitals_of(target)
        initial = vitals.total
        for _ in range(520):
            commands = {first: (*first_aim, 1)}
            if dome.vitals.total > 10:
                commands[second] = (*second_aim, 1)
            run.step(commands)
            if dome.vitals.total == 0:
                break
        self.assertEqual(dome.vitals.total, 0)
        self.assertEqual(vitals.total, initial)
        for shooter in (first, second):
            self.assertEqual(shooter.body_script.component.entity_var(ULT_CHARGE).value(), 0)
        for _ in range(12):
            run.step({})
        self.assertTrue(dome.retired)
        self.assertFalse(fight.barriers)

    def test_actual_allied_winston_dome_passes_ball_cannon(self):
        run = Run(KANEZAKA_DM, ('Ball', 'Allied Winston', 'Enemy'))
        player, winston, enemy = run.players
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        player.team, winston.team, enemy.team = 0, 0, 1
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        run.match.switch_hero(player, heroes()[ball.HERO])
        run.match.switch_hero(winston, heroes()[barriers.WINSTON])
        x, y, z = player.mover.position
        winston.mover.position[:] = [x, y, z + 7]
        enemy.mover.position[:] = [x, y, z + 8]
        for _ in range(90):
            run.step({})
        player.body_script.ult_seconds = 0
        player.body_script.set_ult_charge(0)
        run.step({winston: (-32768, 0, 16)})
        fight = run.match.combat
        for _ in range(45):
            if fight.barriers:
                break
            run.step({})
        dome, = fight.barriers.values()
        angles = aim(fight._eye(player), (x, y + 1, z + 8))
        run.step({player: (*angles, 1)})
        self.assertEqual(dome.vitals.total, 700)
        self.assertEqual(fight.vitals[enemy.body].total, 195)
        self.assertEqual(player.body_script.component.entity_var(ULT_CHARGE).value(), 5)
