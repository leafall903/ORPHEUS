"""Actual Hanzo inputs against Reinhardt's resource-backed mesh."""

import math
import os
import unittest
from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import test_game_hanzo_dragons as dragon_tests
import test_game_hanzo_matrix as matrix_tests
import test_game_hanzo_sonic as sonic_tests
from test_game_combat import Run, aim, in_world
from test_game_mover import box, wall

from ow174.game import arrow_deflection, barrier_mesh, bodytrace, combat, movelog, reinhardt_barrier
from ow174.game.collision import World, flat_world
from ow174.game.commands import Command
from ow174.game.content import SOLDIER, heroes


class HanzoBarrierTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = os.environ.get("ORPHEUS_REINHARDT_COLLISION")
        if not path:
            raise unittest.SkipTest("prepared build 104319 barrier mesh required")
        cls.geometry = barrier_mesh.load_geometry(Path(path))

    def setUp(self):
        for module in (bodytrace, movelog):
            self.enterContext(patch.object(module, "ENABLED", False))

    def arena(self, *, facing=-32768, held=2, genji_at=None, distance=6, genji_facing=-32768):
        names = ("Hanzo", "Reinhardt") if genji_at is None else ("Hanzo", "Reinhardt", "Genji")
        run = Run(names=names)
        hanzo, rein = run.players[:2]
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        run.match.collision_world = flat_world(0)
        self.assertTrue(run.match.switch_hero(hanzo, heroes()[0x02E0000000000005]))
        self.assertTrue(run.match.switch_hero(rein, heroes()[reinhardt_barrier.HERO]))
        genji = None
        if genji_at is not None:
            genji = run.players[2]
            self.assertTrue(run.match.switch_hero(genji, heroes()[arrow_deflection.GENJI]))
            genji.team = rein.team
            genji.mover.position[:] = [0, 0, genji_at]
            genji.mover.body = None
        for actor, position in ((hanzo, [0, 0, 0]), (rein, [0, 0, distance])):
            actor.mover.position[:] = position
            actor.mover.body = None
        fight = run.match.combat
        fight.barriers.geometry = self.geometry
        angles = aim(fight._eye(hanzo), (0, 1.25, distance))

        def step(button=0, shield=held, deflect=0):
            commands = {hanzo: (*angles, button), rein: (facing, 0, shield)}
            if genji is not None:
                commands[genji] = (genji_facing, 0, deflect)
            run.step(commands)

        for _ in range(20):
            step(shield=0)
        for _ in range(5):
            step()
        target = next(t for t in fight._targets() if t.owner is rein)
        return fight, hanzo, rein, fight.vitals_of(target), step

    @staticmethod
    def draw(step):
        for _ in range(70):
            step(1)
        step()
        for _ in range(12):
            step()

    def test_live_mesh_takes_bow_and_sonic_damage_without_body_contact(self):
        for sonic in (False, True):
            with self.subTest(sonic=sonic):
                fight, hanzo, rein, pool, step = self.arena()
                if sonic:
                    fight.configure_sonic(sonic_tests.profile())
                    step(8)
                    step()
                shield = fight.barriers.states[rein.body]
                self.assertTrue(shield.active)
                self.assertEqual(shield.current, 1600)
                self.draw(step)
                self.assertEqual(shield.current, 1475)
                self.assertEqual(pool.total, 500)
                self.assertFalse(hanzo.client.messages(combat.HIT))
                self.assertFalse(fight.rockets)
                self.assertFalse(fight.sonars)

    def zarya_arena(self):
        from ow174.game import zarya_barrier

        run = Run(names=("Hanzo", "Zarya"))
        hanzo, zarya = run.players
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        run.match.collision_world = flat_world(0)
        for player, hero, z in ((hanzo, 0x02E0000000000005, -3), (zarya, zarya_barrier.HERO, 5)):
            self.assertTrue(run.match.switch_hero(player, heroes()[hero]))
            player.mover.position[:] = (0, 0, z)
            player.mover.body = None
        fight = run.match.combat
        for _ in range(25):
            run.step({})
        return run, hanzo, zarya, fight

    def test_zarya_personal_barrier_consumes_bow_and_breaking_sonic_without_spill(self):
        run, hanzo, zarya, fight = self.zarya_arena()
        fight.configure_sonic(sonic_tests.profile())
        angles = aim(fight._eye(hanzo), (0, 1, 5))

        def step(button=0):
            run.step({hanzo: (*angles, button)})

        for _ in range(70):
            step(1)
        run.step({hanzo: (*angles, 1), zarya: (0, 0, 8)})
        barriers = fight.zarya_barriers
        phase = barriers.sync(zarya)
        self.assertIsNotNone(phase)
        self.assertEqual(barriers.pools[zarya.body][1], 200)
        step()
        for _ in range(12):
            step()
        self.assertEqual(barriers.pools[zarya.body][1], 75)
        energy = zarya.body_script.component.entity_var(87)
        self.assertEqual(energy.value(), 25)
        self.assertEqual(fight.vitals[zarya.body].total, 400)
        step(8)
        step()
        self.draw(step)
        self.assertFalse(phase.active)
        self.assertNotIn(zarya.body, barriers.pools)
        self.assertGreater(energy.value(), 39)
        self.assertLessEqual(energy.value(), 40)
        self.assertEqual(fight.vitals[zarya.body].total, 400)
        self.assertFalse(fight.sonars)
        self.assertFalse(hanzo.client.messages(combat.HIT))
        self.draw(step)
        self.assertEqual(fight.vitals[zarya.body].total, 275)

    def test_personal_barrier_raised_in_flight_stops_short_bow_and_sonic(self):
        for use_sonic, shield_hp in ((False, 200), (False, 25), (True, 200), (True, 25)):
            with self.subTest(sonic=use_sonic, shield_hp=shield_hp):
                run, hanzo, zarya, fight = self.zarya_arena()
                if use_sonic:
                    fight.configure_sonic(sonic_tests.profile())
                angles = aim(fight._eye(hanzo), (0, 1, 5))

                def short_arrow(prepare_sonic=False):
                    if prepare_sonic:
                        run.step({hanzo: (*angles, 8)})
                        run.step({})
                    for _ in range(5):
                        run.step({hanzo: (*angles, 1)})
                    run.step({hanzo: (*angles, 0)})
                    arrow, = fight.rockets
                    self.assertIsNotNone(arrow.shot)
                    definition = 0x040000000000020D if prepare_sonic else 0x040000000000020B
                    self.assertEqual(arrow.shot.definition, definition)
                    self.assertAlmostEqual(arrow.direct.amount, 25 + 100 * 1.375 * 0.079, delta=0.12)
                    return arrow

                arrow = short_arrow(use_sonic)
                lease = arrow.shot
                run.step({zarya: (0, 0, 8)})
                self.assertIn(arrow, fight.rockets)
                barriers = fight.zarya_barriers
                phase = barriers.sync(zarya)
                self.assertIsNotNone(phase)
                self.assertEqual(barriers.pools[zarya.body][1], 200)
                if shield_hp < 200:
                    barriers.damage(zarya, 200 - shield_hp, hanzo.body)
                self.assertEqual(barriers.pools[zarya.body][1], shield_hp)

                for _ in range(35):
                    run.step({})
                self.assertNotIn(arrow, fight.rockets)
                self.assertNotIn(lease, fight.projectiles.shots)
                self.assertIsNone(arrow.shot)
                self.assertAlmostEqual(barriers.pools.get(zarya.body, (None, 0))[1],
                                       max(0, shield_hp - arrow.direct.amount), delta=0.01)
                self.assertEqual(phase.active, shield_hp == 200)
                pool = fight.vitals[zarya.body]
                self.assertEqual(pool.total, 400)
                self.assertFalse(fight.sonars)
                self.assertFalse(hanzo.client.messages(combat.HIT))

                if shield_hp == 25:
                    followup = short_arrow()
                    for _ in range(25):
                        run.step({})
                        if followup not in fight.rockets:
                            break
                    self.assertAlmostEqual(pool.total, 400 - followup.direct.amount, delta=0.01)
                    hit, = hanzo.client.messages(combat.HIT)
                    self.assertEqual(hit["+0x78"]["+0x4"], round(followup.direct.amount))
                    self.assertFalse(fight.sonars)

    def test_zarya_personal_barrier_breaks_on_third_of_five_storm_arrows(self):
        run, hanzo, zarya, fight = self.zarya_arena()
        angles = aim(fight._eye(hanzo), (0, 1, 5))
        barriers = fight.zarya_barriers
        run.step({hanzo: (*angles, 16), zarya: (0, 0, 8)})
        phase = barriers.sync(zarya)
        self.assertIsNotNone(phase)
        target = next(t for t in fight._targets() if t.owner is zarya)
        pool = fight.vitals_of(target)
        changes = [(200, pool.total)]
        run.step({})
        with patch.object(fight.projectiles, "fired", wraps=fight.projectiles.fired) as fired:
            for _ in range(140):
                run.step({hanzo: (*angles, 1)})
                hp = barriers.pools.get(zarya.body, (None, 0))[1]
                pair = hp, pool.total
                if pair != changes[-1]:
                    changes.append(pair)
        self.assertEqual([call.args[3] for call in fired.call_args_list], [0x040000000000270F] * 5)
        self.assertEqual(changes, [(200, 400), (130, 400), (60, 400), (0, 400), (0, 330), (0, 260)])
        self.assertFalse(phase.active)
        self.assertFalse(fight.rockets)

    def test_zarya_personal_barrier_stops_initial_dragon_and_transforms_at_contact(self):
        run, hanzo, zarya, fight = self.zarya_arena()
        fight.configure_dragonstrike(dragon_tests.prepared_profile())
        hanzo.body_script.ult_seconds = 0
        hanzo.body_script.set_ult_charge(1680)
        angles = aim(fight._eye(hanzo), (0, 1, 5))
        run.step({hanzo: (*angles, 32), zarya: (0, 0, 8)})
        initial = None
        for _ in range(100):
            run.step({hanzo: (*angles, 0)})
            if fight.rockets:
                initial, = fight.rockets
                break
        self.assertIsNotNone(initial)
        self.assertEqual(initial.direct.amount, 125)
        for _ in range(30):
            run.step({})
            if fight.dragons:
                break
        flight, = fight.dragons
        self.assertEqual(flight.origin, initial.position)
        self.assertLess(flight.origin[2], zarya.mover.position[2] - 1)
        self.assertEqual(fight.zarya_barriers.pools[zarya.body][1], 75)
        self.assertFalse(fight.rockets)
        for _ in range(20):
            run.step({})
        self.assertLess(fight.vitals[zarya.body].total, 400)
        self.assertEqual(fight.zarya_barriers.pools[zarya.body][1], 75)

    def test_zarya_personal_barrier_melee_range_faction_wall_and_break(self):
        for distance, allied, blocked, initial_hp, expected_hp in (
            (3.3, False, False, 200, 170), (4.3, False, False, 200, 200),
            (2.2, True, False, 200, 200), (2.2, False, True, 200, 200),
            (2.2, False, False, 25, 0),
        ):
            with self.subTest(distance=distance, allied=allied, blocked=blocked, initial_hp=initial_hp):
                run, hanzo, zarya, fight = self.zarya_arena()
                match = run.match
                match.game_map = replace(match.game_map, free_for_all=False)
                hanzo.team = zarya.team if allied else 1 - zarya.team
                zarya.mover.position[:] = (0, 0, -3 + distance)
                zarya.mover.body = None
                if blocked:
                    match.collision_world = World(
                        box(-20, 20, -1, 0, -20, 20) + wall(-5, -2.7, 5, -2.7, y0=0, y1=6)
                    )
                for _ in range(25):
                    run.step({})
                run.step({zarya: (0, 0, 8)})
                barriers = fight.zarya_barriers
                phase = barriers.sync(zarya)
                self.assertIsNotNone(phase)
                if initial_hp < 200:
                    barriers.damage(zarya, 200 - initial_hp, hanzo.body)
                pool = fight.vitals_of(next(t for t in fight._targets() if t.owner is zarya))
                angles = aim(fight._eye(hanzo), (0, 1, -3 + distance))
                run.step({hanzo: (*angles, 0x800)})
                for _ in range(20):
                    run.step({})
                self.assertEqual(barriers.pools.get(zarya.body, (None, 0))[1], expected_hp)
                self.assertEqual(pool.total, 400)
                self.assertFalse(hanzo.client.messages(combat.HIT))
                if expected_hp == 0:
                    self.assertFalse(phase.active)
                    for _ in range(65):
                        run.step({})
                    run.step({hanzo: (*angles, 0x800)})
                    for _ in range(20):
                        run.step({})
                    self.assertEqual(pool.total, 370)

    def zarya_projected_arena(self):
        run, hanzo, zarya, fight = self.zarya_arena()
        match = run.match
        match.game_map = replace(match.game_map, free_for_all=False)
        hanzo.team = 1 - zarya.team
        ally = match.add_player(3, "Protected Soldier", SOLDIER, zarya.team, False)
        in_world(match, ally)
        run.players.append(ally)
        self.assertTrue(match.switch_hero(ally, heroes()[SOLDIER]))
        ally.team = zarya.team
        ally.mover.position[:] = (0, 0, 1)
        ally.mover.body = None
        return run, hanzo, zarya, ally, fight

    def test_zarya_projected_barrier_rejects_invalid_targets_then_accepts_fresh_cast(self):
        for condition in ("enemy", "off-axis", "range", "wall"):
            with self.subTest(condition=condition):
                run, hanzo, zarya, ally, fight = self.zarya_projected_arena()
                match = run.match
                original_world = match.collision_world
                if condition == "enemy":
                    ally.team = hanzo.team
                elif condition == "range":
                    ally.mover.position[2] = -40
                elif condition == "wall":
                    match.collision_world = World(
                        box(-20, 20, -1, 0, -20, 20) + wall(-10, 3, 10, 3, y0=0, y1=6)
                    )
                for _ in range(20):
                    run.step({})
                cast = aim(fight._eye(zarya), (0, 1, ally.mover.position[2]))
                if condition == "off-axis":
                    cast = (0, 0)
                run.step({zarya: (*cast, 16)})
                barriers = fight.zarya_barriers
                self.assertIsNone(barriers.projected_phase(zarya))
                self.assertNotIn(zarya.body, barriers.projected)

                ally.team = zarya.team
                ally.mover.position[:] = (0, 0, 1)
                ally.mover.body = None
                match.collision_world = original_world
                run.step({})
                cast = aim(fight._eye(zarya), (0, 1, 1))
                run.step({zarya: (*cast, 16)})
                self.assertEqual(barriers.projected[zarya.body][1:], (200, ally.body))

    def test_zarya_projected_barrier_retires_on_body_change_and_recast_expires(self):
        run, hanzo, zarya, ally, fight = self.zarya_projected_arena()
        cast = aim(fight._eye(zarya), (0, 1, 1))
        barriers = fight.zarya_barriers
        run.step({zarya: (*cast, 16)})
        old_body = ally.body
        self.assertEqual(barriers.projected[zarya.body][1:], (200, old_body))
        self.assertTrue(run.match.switch_hero(ally, ally.hero))
        self.assertNotEqual(ally.body, old_body)
        self.assertNotIn(zarya.body, barriers.projected)
        ally.mover.position[:] = (0, 0, 1)
        ally.mover.body = None
        run.step({})
        run.step({zarya: (*cast, 16)})
        self.assertIsNone(barriers.projected_phase(zarya))

        run.frame += 680
        run.match.update(run.frame * combat.TICK, run.frame)
        run.step({zarya: (*cast, 16)})
        self.assertEqual(barriers.projected[zarya.body][1:], (200, ally.body))
        # Enter the world once before measuring a gap during an active shield.
        run.frame += 1
        run.match.update(run.frame * combat.TICK, run.frame)
        run.frame += 129
        run.match.update(run.frame * combat.TICK, run.frame)
        self.assertIsNone(barriers.projected_phase(zarya))
        self.assertNotIn(zarya.body, barriers.projected)
        angles = aim(fight._eye(hanzo), (0, 1, 1))
        self.draw(lambda button=0: run.step({hanzo: (*angles, button)}))
        self.assertEqual(fight.vitals[ally.body].total, 75)

    def test_zarya_new_projected_cast_does_not_intercept_earlier_catchup_bow(self):
        for skip in (90, 180):
            with self.subTest(skip=skip):
                self.verify_zarya_fresh_cast_after_gap(skip)

    def verify_zarya_fresh_cast_after_gap(self, skip):
        run, hanzo, zarya, ally, fight = self.zarya_projected_arena()
        angles = aim(fight._eye(hanzo), (0, 1, 1))
        cast_yaw, cast_pitch = aim(fight._eye(zarya), (0, 1, 1))
        for _ in range(70):
            run.step({hanzo: (*angles, 1)})
        run.step({hanzo: (*angles, 0)})
        arrow, = fight.rockets
        run.frame += 1
        run.match.update(run.frame * combat.TICK, run.frame)
        self.assertEqual(arrow.last_tick, run.frame)
        self.assertIn(arrow, fight.rockets)
        zarya.take_commands([Command(run.frame, yaw=cast_yaw, pitch=cast_pitch, buttons=16)])
        barriers = fight.zarya_barriers
        self.assertEqual(barriers.projected[zarya.body][1:], (200, ally.body))
        run.frame += skip
        run.match.update(run.frame * combat.TICK, run.frame)
        self.assertFalse(fight.rockets)
        self.assertEqual(fight.vitals[ally.body].total, 75)
        self.assertEqual(barriers.projected[zarya.body][1:], (200, ally.body))
        run.frame += 120
        run.match.update(run.frame * combat.TICK, run.frame)
        self.assertIn(zarya.body, barriers.projected)
        run.frame += 10
        run.match.update(run.frame * combat.TICK, run.frame)
        self.assertNotIn(zarya.body, barriers.projected)

    def test_zarya_overlapping_barriers_keep_independent_clocks_in_both_cast_orders(self):
        for projected_first in (False, True):
            for skip in (90, 180):
                with self.subTest(projected_first=projected_first, skip=skip):
                    self.verify_zarya_overlapping_shield_clocks(skip, projected_first)

    def verify_zarya_overlapping_shield_clocks(self, skip, projected_first):
        run, _, zarya, ally, fight = self.zarya_projected_arena()
        cast = aim(fight._eye(zarya), (0, 1, 1))
        barriers = fight.zarya_barriers
        old_phase, new_phase = ((barriers.projected_phase, barriers.phase) if projected_first
                                else (barriers.phase, barriers.projected_phase))
        first, second = (16, 8) if projected_first else (8, 16)
        run.step({zarya: (*cast, first)})
        run.step({})
        self.assertIsNotNone(old_phase(zarya))
        run.step({zarya: (*cast, second)})
        self.assertEqual(barriers.pools[zarya.body][1], 200)
        self.assertEqual(barriers.projected[zarya.body][1:], (200, ally.body))
        cooldown = new_phase(zarya).instance.vars[61]
        new_pool = barriers.pools if projected_first else barriers.projected
        before = cooldown.value()
        run.frame += skip
        run.match.update(run.frame * combat.TICK, run.frame)
        self.assertEqual(old_phase(zarya) is not None, skip == 90)
        self.assertEqual(new_pool[zarya.body][1], 200)
        self.assertAlmostEqual(cooldown.value(), before, delta=combat.TICK)
        run.frame += 40
        run.match.update(run.frame * combat.TICK, run.frame)
        self.assertIsNone(old_phase(zarya))
        self.assertIn(zarya.body, new_pool)
        run.frame += 80
        run.match.update(run.frame * combat.TICK, run.frame)
        self.assertIn(zarya.body, new_pool)
        run.frame += 10
        run.match.update(run.frame * combat.TICK, run.frame)
        self.assertNotIn(zarya.body, new_pool)

    def test_zarya_idle_projected_expiry_orders_inflight_bow_on_tick_jump(self):
        for offset, expected_hp in ((115, 200), (124, 75)):
            for skip in (0, 90):
                with self.subTest(offset=offset, skip=skip):
                    run, hanzo, zarya, ally, fight = self.zarya_projected_arena()
                    cast = aim(fight._eye(zarya), (0, 1, 1))
                    angles = aim(fight._eye(hanzo), (0, 1, 1))
                    run.step({zarya: (*cast, 16)})
                    cast_frame = run.frame
                    barriers = fight.zarya_barriers
                    self.assertEqual(barriers.projected[zarya.body][1:], (200, ally.body))
                    while run.frame < cast_frame + offset:
                        run.frame += 1
                        run.match.update(run.frame * combat.TICK, run.frame)
                        hanzo.take_commands([Command(run.frame, yaw=angles[0], pitch=angles[1], buttons=1)])
                    run.frame += 1
                    run.match.update(run.frame * combat.TICK, run.frame)
                    hanzo.take_commands([Command(run.frame, yaw=angles[0], pitch=angles[1])])
                    arrow, = fight.rockets
                    run.frame += 1
                    run.match.update(run.frame * combat.TICK, run.frame)
                    self.assertEqual(arrow.last_tick, run.frame)
                    self.assertIn(arrow, fight.rockets)
                    for advance in ([skip] if skip else [1] * 90):
                        run.frame += advance
                        run.match.update(run.frame * combat.TICK, run.frame)
                    self.assertFalse(fight.rockets)
                    self.assertNotIn(zarya.body, barriers.projected)
                    self.assertEqual(fight.vitals[ally.body].total, expected_hp)

    def test_zarya_personal_and_projected_barriers_consume_storm_arrows_separately(self):
        for seeded in (False, True):
            with self.subTest(seeded=seeded):
                run, hanzo, caster, recipient, fight = self.zarya_projected_arena()
                self.assertTrue(run.match.switch_hero(recipient, caster.hero))
                recipient.team = caster.team
                recipient.mover.position[:] = (0, 0, 1)
                recipient.mover.body = None
                barriers = fight.zarya_barriers
                for _ in range(25):
                    run.step({})
                angles = aim(fight._eye(hanzo), (0, 1, 1))
                cast = aim(fight._eye(caster), (0, 1, 1))
                run.step({hanzo: (*angles, 16), caster: (*cast, 16)})
                self.assertEqual(barriers.projected[caster.body][1:], (200, recipient.body))
                if seeded:
                    barriers.damage(recipient, 155, hanzo.body)
                run.step({recipient: (0, 0, 8)})
                self.assertIsNotNone(barriers.phase(recipient))
                if seeded:
                    barriers.damage(recipient, 175, hanzo.body)
                pool = fight.vitals_of(next(t for t in fight._targets() if t.owner is recipient))

                def health():
                    personal = barriers.pools.get(recipient.body, (None, 0))[1]
                    projected = barriers.projected.get(caster.body, (None, 0))[1]
                    return personal, projected, pool.total

                changes = [health()]
                with patch.object(fight.projectiles, "fired", wraps=fight.projectiles.fired) as fired:
                    for _ in range(140):
                        run.step({hanzo: (*angles, 1)})
                        state = health()
                        if state != changes[-1]:
                            changes.append(state)
                        if fired.call_count == 5 and not fight.rockets:
                            break
                self.assertEqual([call.args[3] for call in fired.call_args_list], [0x040000000000270F] * 5)
                if seeded:
                    self.assertEqual(changes, [
                        (25, 45, 400), (0, 45, 400), (0, 0, 400),
                        (0, 0, 330), (0, 0, 260), (0, 0, 190),
                    ])
                else:
                    self.assertEqual(changes, [
                        (200, 200, 400), (130, 200, 400), (60, 200, 400),
                        (0, 200, 400), (0, 130, 400), (0, 60, 400),
                    ])

    def test_zarya_projected_barrier_stops_initial_dragon_but_not_moving_damage(self):
        run, hanzo, zarya, ally, fight = self.zarya_projected_arena()
        fight.configure_dragonstrike(dragon_tests.prepared_profile())
        hanzo.body_script.ult_seconds = 0
        hanzo.body_script.set_ult_charge(1680)
        angles = aim(fight._eye(hanzo), (0, 1, 1))
        cast = aim(fight._eye(zarya), (0, 1, 1))
        run.step({hanzo: (*angles, 32), zarya: (*cast, 16)})
        barriers = fight.zarya_barriers
        self.assertEqual(barriers.projected[zarya.body][1:], (200, ally.body))
        for _ in range(100):
            run.step({hanzo: (*angles, 0)})
            if fight.rockets:
                break
        initial, = fight.rockets
        self.assertEqual(initial.direct.amount, 125)
        for _ in range(30):
            run.step({})
            if fight.dragons:
                break
        flight, = fight.dragons
        self.assertEqual(flight.origin, initial.position)
        self.assertLess(flight.origin[2], ally.mover.position[2] - 1)
        self.assertEqual(barriers.projected[zarya.body][1:], (75, ally.body))
        self.assertFalse(fight.rockets)
        for _ in range(20):
            run.step({})
        self.assertLess(fight.vitals[ally.body].total, 200)
        self.assertEqual(barriers.projected[zarya.body][1:], (75, ally.body))

    def test_zarya_projected_barrier_breaking_melee_preserves_recipient(self):
        run, hanzo, zarya, ally, fight = self.zarya_projected_arena()
        ally.mover.position[:] = (0, 0, -0.8)
        ally.mover.body = None
        for _ in range(25):
            run.step({})
        angles = aim(fight._eye(hanzo), (0, 1, -0.8))
        cast = aim(fight._eye(zarya), (0, 1, -0.8))
        run.step({zarya: (*cast, 16)})
        barriers = fight.zarya_barriers
        self.assertEqual(barriers.projected[zarya.body][1:], (200, ally.body))
        barriers.damage(ally, 175, hanzo.body)
        run.step({hanzo: (*angles, 0x800)})
        for _ in range(20):
            run.step({})
        self.assertNotIn(zarya.body, barriers.projected)
        self.assertEqual(fight.vitals[ally.body].total, 200)
        self.assertFalse(hanzo.client.messages(combat.HIT))
        for _ in range(65):
            run.step({})
        run.step({hanzo: (*angles, 0x800)})
        for _ in range(20):
            run.step({})
        self.assertEqual(fight.vitals[ally.body].total, 170)

    def test_zarya_projected_barrier_protects_ally_until_third_storm_arrow(self):
        run, hanzo, zarya, ally, fight = self.zarya_projected_arena()
        angles = aim(fight._eye(hanzo), (0, 1, 1))
        cast = aim(fight._eye(zarya), (0, 1, 1))
        run.step({hanzo: (*angles, 16), zarya: (*cast, 16)})
        barriers = fight.zarya_barriers
        self.assertEqual(barriers.projected[zarya.body][1:], (200, ally.body))
        target = next(t for t in fight._targets() if t.owner is ally)
        pool = fight.vitals_of(target)
        changes = [(200, pool.total)]
        run.step({})
        with patch.object(fight.projectiles, "fired", wraps=fight.projectiles.fired) as fired:
            for _ in range(140):
                run.step({hanzo: (*angles, 1)})
                entry = barriers.projected.get(zarya.body)
                pair = (entry[1] if entry else 0), pool.total
                if pair != changes[-1]:
                    changes.append(pair)
        self.assertEqual([call.args[3] for call in fired.call_args_list], [0x040000000000270F] * 5)
        self.assertEqual(changes, [(200, 200), (130, 200), (60, 200), (0, 200), (0, 130), (0, 60)])
        self.assertEqual([hit["+0x78"]["+0x4"] for hit in hanzo.client.messages(combat.HIT)], [70, 70])
        self.assertFalse(fight.rockets)

    def test_zarya_projected_barrier_protects_ally_from_actual_bow_and_sonic(self):
        run, hanzo, zarya, ally, fight = self.zarya_projected_arena()
        fight.configure_sonic(sonic_tests.profile())
        angles = aim(fight._eye(hanzo), (0, 1, 1))
        cast = aim(fight._eye(zarya), (0, 1, 1))
        for _ in range(70):
            run.step({hanzo: (*angles, 1)})
        run.step({hanzo: (*angles, 1), zarya: (*cast, 16)})
        barriers = fight.zarya_barriers
        self.assertEqual(barriers.projected[zarya.body][1:], (200, ally.body))
        self.assertNotIn(zarya.body, barriers.pools)

        def step(button=0):
            run.step({hanzo: (*angles, button)})

        step()
        for _ in range(12):
            step()
        self.assertEqual(barriers.projected[zarya.body][1:], (75, ally.body))
        self.assertEqual(zarya.body_script.component.entity_var(87).value(), 25)
        self.assertEqual(fight.vitals[ally.body].total, 200)
        step(8)
        step()
        self.draw(step)
        self.assertNotIn(zarya.body, barriers.projected)
        self.assertEqual(fight.vitals[ally.body].total, 200)
        self.assertFalse(fight.sonars)
        self.assertFalse(hanzo.client.messages(combat.HIT))
        self.draw(step)
        self.assertEqual(fight.vitals[ally.body].total, 75)

    def orisa_arena(self):
        run = Run(names=("Hanzo", "Orisa"))
        hanzo, orisa = run.players
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        run.match.collision_world = flat_world(0)
        self.assertTrue(run.match.switch_hero(hanzo, heroes()[0x02E0000000000005]))
        self.assertTrue(run.match.switch_hero(orisa, heroes()[0x02E000000000013E]))
        for player, position in ((hanzo, (0, 0, -3)), (orisa, (0, 0, 5))):
            player.mover.position[:] = position
            player.mover.body = None
        fight = run.match.combat
        service = fight.orisa_barriers
        if service.geometry is None:
            self.skipTest("prepared build 104319 Orisa collision mesh required")
        for _ in range(25):
            run.step({})
        return run, hanzo, orisa, service

    def deploy_orisa_barrier(self):
        run, hanzo, orisa, service = self.orisa_arena()
        run.step({orisa: (-32768, 8192, 16)})
        flight = None
        for _ in range(70):
            flight = service.flights.get(orisa.body, flight)
            if orisa.body in service.actors:
                break
            run.step({orisa: (-32768, 8192, 0)})
        self.assertIsNotNone(flight)
        self.assertAlmostEqual(flight.gravity, 9.8, places=5)
        shield = service.actors[orisa.body]
        self.assertEqual(shield.health, 600)
        self.assertLess(shield.position[2], orisa.mover.position[2])
        return run, hanzo, orisa, shield

    def test_orisa_throw_releases_without_further_input_and_stops_hanzo_bow(self):
        for switch_hero, jump in ((False, False), (False, True), (True, True)):
            with self.subTest(switch_hero=switch_hero, jump=jump):
                run, hanzo, orisa, service = self.orisa_arena()
                run.step({orisa: (-32768, 8192, 16)})
                self.assertTrue(service.pending(orisa))
                self.assertFalse(service.flights)
                self.assertFalse(service.actors)
                if switch_hero:
                    self.assertTrue(run.match.switch_hero(orisa, heroes()[SOLDIER]))
                for advance in ([60] if jump else [1] * 60):
                    run.frame += advance
                    run.match.update(run.frame * combat.TICK, run.frame)
                self.assertFalse(service.flights)
                if switch_hero:
                    self.assertFalse(service.actors)
                    continue
                shield, = service.actors.values()
                self.assertEqual(shield.health, 600)
                self.assertEqual(shield.yaw, -32768)
                self.assertLess(shield.position[2], orisa.mover.position[2])
                self.assertEqual(shield.component.frame, run.frame)
                fight = run.match.combat
                angles = aim(fight._eye(hanzo), (0, 0.8, 5))
                self.draw(lambda key=0: run.step({hanzo: (*angles, key)}))
                self.assertEqual(shield.health, 475)
                self.assertEqual(fight.vitals[orisa.body].total, 450)

    def test_inflight_bow_orders_contact_against_orisa_landing_on_tick_jump(self):
        for shooter_z, expected in ((-3, (600, 330)), (-30, (475, 450))):
            for skip in (0, 60, 90):
                with self.subTest(shooter_z=shooter_z, skip=skip):
                    run, hanzo, orisa, service = self.orisa_arena()
                    fight = run.match.combat
                    hanzo.mover.position[:] = (0, 0, shooter_z)
                    hanzo.mover.body = None
                    angles = aim(fight._eye(hanzo), (0, 1.2, 5))
                    for _ in range(70):
                        run.step({hanzo: (*angles, 1)})
                    run.step({hanzo: (*angles, 0), orisa: (-32768, 8192, 16)})
                    rocket, = fight.rockets
                    run.frame += 1
                    run.match.update(run.frame * combat.TICK, run.frame)
                    self.assertEqual(rocket.last_tick, run.frame)
                    self.assertFalse(service.actors)
                    for advance in ([skip] if skip else [1] * 60):
                        run.frame += advance
                        run.match.update(run.frame * combat.TICK, run.frame)
                    self.assertFalse(fight.rockets)
                    shield, = service.actors.values()
                    self.assertEqual((shield.health, fight.vitals[orisa.body].total), expected)

    def test_pending_orisa_throw_and_fresh_fortify_keep_independent_gap_timing(self):
        for shooter_z, expected in ((-3, (600, 330)), (-30, (475, 450))):
            with self.subTest(shooter_z=shooter_z):
                run, hanzo, orisa, service = self.orisa_arena()
                match = run.match
                fight = match.combat
                hanzo.mover.position[:] = (0, 0, shooter_z)
                hanzo.mover.body = None
                angles = aim(fight._eye(hanzo), (0, 1.2, 5))
                for _ in range(70):
                    run.step({hanzo: (*angles, 1)})
                run.step({hanzo: (*angles, 0), orisa: (-32768, 8192, 16)})
                arrow, = fight.rockets
                run.frame += 1
                match.update(run.frame * combat.TICK, run.frame)
                self.assertEqual(arrow.last_tick, run.frame)
                self.assertTrue(service.pending(orisa))
                orisa.take_commands([Command(run.frame, yaw=-32768, pitch=8192, buttons=8)])
                self.assertTrue(service.pending(orisa))
                self.assertTrue(orisa.body_script.orisa_fortify_pending)
                fortify = combat._active_fortify(orisa)
                self.assertIsNotNone(fortify)
                duration = fortify.instance.vars[215]
                before = duration.value()

                run.frame += 90
                match.update(run.frame * combat.TICK, run.frame)
                self.assertFalse(fight.rockets)
                self.assertFalse(service.flights)
                self.assertFalse(service.pending(orisa))
                shield, = service.actors.values()
                self.assertEqual((shield.health, fight.vitals[orisa.body].total), expected)
                self.assertTrue(fortify.active)
                self.assertFalse(orisa.body_script.orisa_fortify_pending)
                self.assertLessEqual(abs(duration.value() - before), combat.TICK)

    def test_inflight_bow_and_sonic_select_old_or_replacement_orisa_mesh(self):
        for sonic in (False, True):
            for shooter_z, expected in ((-3, (475, 600)), (-30, (600, 475))):
                for skip in (0, 90):
                    with self.subTest(sonic=sonic, shooter_z=shooter_z, skip=skip):
                        run, hanzo, orisa, old = self.deploy_orisa_barrier()
                        fight = run.match.combat
                        service = fight.orisa_barriers
                        hanzo.mover.position[:] = (0, 0, shooter_z)
                        hanzo.mover.body = None
                        run.frame += 650
                        run.match.update(run.frame * combat.TICK, run.frame)
                        angles = aim(fight._eye(hanzo), (0, 1.2, 5))
                        if sonic:
                            fight.configure_sonic(sonic_tests.profile())
                            run.step({hanzo: (*angles, 8)})
                            run.step({})
                        for _ in range(70):
                            run.step({hanzo: (*angles, 1)})
                        run.step({hanzo: (*angles, 0), orisa: (-32768, 8192, 16)})
                        rocket, = fight.rockets
                        self.assertEqual(rocket.shot.definition, 0x0400000000000149 if sonic
                                         else 0x0400000000000144)
                        run.frame += 1
                        run.match.update(run.frame * combat.TICK, run.frame)
                        self.assertEqual(rocket.last_tick, run.frame)
                        self.assertIs(service.actors[orisa.body], old)
                        self.assertTrue(service.pending(orisa))
                        for advance in ([skip] if skip else [1] * 90):
                            run.frame += advance
                            run.match.update(run.frame * combat.TICK, run.frame)
                        self.assertFalse(fight.rockets)
                        self.assertFalse(fight.sonars)
                        self.assertFalse(fight.revealed_to(hanzo, orisa))
                        replacement = service.actors[orisa.body]
                        self.assertIsNot(old, replacement)
                        self.assertTrue(old.retired)
                        self.assertEqual((old.health, replacement.health), expected)
                        self.assertEqual(fight.vitals[orisa.body].total, 450)

    def test_inflight_bow_orders_contact_against_orisa_expiry_on_tick_jump(self):
        for shooter_z, expected in ((-3, (475, 450)), (-30, (600, 330))):
            for skip in (0, 90):
                with self.subTest(shooter_z=shooter_z, skip=skip):
                    run, hanzo, orisa, shield = self.deploy_orisa_barrier()
                    fight = run.match.combat
                    expiry_base = shield.component.frame
                    hanzo.mover.position[:] = (0, 0, shooter_z)
                    hanzo.mover.body = None
                    angles = aim(fight._eye(hanzo), (0, 1.2, 5))
                    run.frame = expiry_base + 1172
                    run.match.update(run.frame * combat.TICK, run.frame)
                    for _ in range(70):
                        run.step({hanzo: (*angles, 1)})
                    self.assertTrue(shield.active())
                    run.step({hanzo: (*angles, 0)})
                    rocket, = fight.rockets
                    run.frame += 1
                    run.match.update(run.frame * combat.TICK, run.frame)
                    self.assertEqual(rocket.last_tick, run.frame)
                    self.assertTrue(shield.active())
                    for advance in ([skip] if skip else [1] * 60):
                        run.frame += advance
                        run.match.update(run.frame * combat.TICK, run.frame)
                    self.assertFalse(fight.rockets)
                    self.assertFalse(fight.orisa_barriers.actors)
                    self.assertEqual((shield.health, fight.vitals[orisa.body].total), expected)

    def test_thrown_orisa_barrier_stops_bow_sonic_and_exactly_five_storm_arrows(self):
        for button, definition, count, loss in (
            (0, 0x0400000000000144, 1, 125),
            (8, 0x0400000000000149, 1, 125),
            (16, 0x040000000000270F, 5, 350),
        ):
            with self.subTest(button=button):
                run, hanzo, orisa, shield = self.deploy_orisa_barrier()
                fight = run.match.combat
                if button == 8:
                    fight.configure_sonic(sonic_tests.profile())
                angles = aim(fight._eye(hanzo), (0, 0.8, 5))
                with patch.object(fight.projectiles, "fired", wraps=fight.projectiles.fired) as fired:
                    if button:
                        run.step({hanzo: (*angles, button)})
                        run.step({})
                    for _ in range(140 if button == 16 else 70):
                        run.step({hanzo: (*angles, 1)})
                    if button != 16:
                        run.step({hanzo: (*angles, 0)})
                        for _ in range(20):
                            run.step({})
                self.assertEqual([call.args[3] for call in fired.call_args_list], [definition] * count)
                self.assertEqual(shield.health, 600 - loss)
                self.assertEqual(fight.vitals[orisa.body].total, 450)
                self.assertFalse(hanzo.client.messages(combat.HIT))
                self.assertFalse(fight.sonars)
                self.assertFalse(fight.rockets)
                if button == 0:
                    run.frame += 1800
                    run.match.update(run.frame * combat.TICK, run.frame)
                    self.assertTrue(shield.retired)
                    self.assertFalse(fight.orisa_barriers.actors)

    def test_allied_orisa_barrier_allows_bow_sonic_and_storm_to_hit_behind_it(self):
        for button, expected in ((0, [125]), (8, [125]), (16, [70, 70, 60])):
            with self.subTest(button=button):
                run, hanzo, orisa, shield = self.deploy_orisa_barrier()
                match = run.match
                match.game_map = replace(match.game_map, free_for_all=False)
                hanzo.team = orisa.team
                enemy = match.add_player(3, "Behind allied barrier", SOLDIER, 1 - orisa.team, False)
                in_world(match, enemy)
                run.players.append(enemy)
                self.assertTrue(match.switch_hero(enemy, heroes()[SOLDIER]))
                enemy.team = 1 - orisa.team
                enemy.mover.position[:] = (0, 0, 8)
                enemy.mover.body = None
                fight = match.combat
                orisa_pool = fight.vitals_of(next(t for t in fight._targets() if t.owner is orisa))
                angles = aim(fight._eye(hanzo), (0, 0.8, 8))
                if button == 8:
                    fight.configure_sonic(sonic_tests.profile())
                if button:
                    run.step({hanzo: (*angles, button)})
                    run.step({})
                if button == 16:
                    for _ in range(140):
                        run.step({hanzo: (*angles, 1)})
                else:
                    self.draw(lambda key=0: run.step({hanzo: (*angles, key)}))
                hits = hanzo.client.messages(combat.HIT)
                self.assertEqual([hit["+0x78"]["+0x4"] for hit in hits], expected)
                self.assertEqual(fight.vitals[enemy.body].total, 200 - sum(expected))
                self.assertEqual(orisa_pool.total, 450)
                self.assertEqual(shield.health, 600)
                self.assertTrue(shield.active())
                if button == 8:
                    beacon, = fight.sonars
                    self.assertEqual(beacon.attached, enemy.body)
                    self.assertTrue(fight.revealed_to(hanzo, enemy))
                    self.assertFalse(fight.revealed_to(hanzo, orisa))
                else:
                    self.assertFalse(fight.sonars)

    def test_nearest_of_orisa_barrier_and_genji_deflect_gets_actual_bow(self):
        for genji_first in (False, True):
            with self.subTest(genji_first=genji_first):
                run, hanzo, orisa, shield = self.deploy_orisa_barrier()
                match = run.match
                fight = match.combat
                hanzo.mover.position[:] = (0, 0, -8)
                hanzo.mover.body = None
                origin = fight._eye(hanzo)
                distance = fight.orisa_barriers.intersect(shield, origin, (0, 0, 1), 20)
                self.assertIsNotNone(distance)
                crossing = origin[2] + distance
                genji = match.add_player(3, "Genji", arrow_deflection.GENJI, orisa.team, False)
                in_world(match, genji)
                run.players.append(genji)
                self.assertTrue(match.switch_hero(genji, heroes()[arrow_deflection.GENJI]))
                genji.mover.position[:] = (0, 0, crossing + (-2.5 if genji_first else 2.5))
                genji.mover.body = None

                for _ in range(70):
                    run.step({hanzo: (0, 0, 1), genji: (-32768, 0, 0)})
                run.step({hanzo: (0, 0, 1), genji: (-32768, 0, 16)})
                run.step({genji: (-32768, 0, 0)})
                arrow, = fight.rockets
                target = next(t for t in fight._targets() if t.owner is genji)
                intercept = arrow_deflection.trace(target, arrow.position, arrow.direction, 20)
                barrier = fight.orisa_barriers.intersect(shield, arrow.position, arrow.direction, 20)
                self.assertIsNotNone(intercept)
                self.assertIsNotNone(barrier)
                self.assertEqual(intercept < barrier, genji_first)
                for _ in range(30):
                    run.step({genji: (-32768, 0, 0)})
                    if arrow not in fight.rockets:
                        break
                self.assertFalse(fight.rockets)
                self.assertEqual(shield.health, 600 if genji_first else 475)
                self.assertIs(arrow.shooter, genji if genji_first else hanzo)
                self.assertEqual(fight.vitals_of(target).total, 200)
                hanzo_target = next(t for t in fight._targets() if t.owner is hanzo)
                self.assertEqual(fight.vitals_of(hanzo_target).total, 75 if genji_first else 200)
                self.assertFalse(hanzo.client.messages(combat.HIT))

    def test_reflected_bow_and_sonic_hit_hanzos_projected_barrier(self):
        for sonic in (False, True):
            for protected in (False, True):
                with self.subTest(sonic=sonic, protected=protected):
                    run, hanzo, zarya, genji, fight = self.zarya_projected_arena()
                    self.assertTrue(run.match.switch_hero(genji, heroes()[arrow_deflection.GENJI]))
                    zarya.team = hanzo.team
                    genji.team = 1 - hanzo.team
                    hanzo.mover.position[:] = (0, 0, -8)
                    zarya.mover.position[:] = (4, 0, -5)
                    genji.mover.position[:] = (0, 0, 1)
                    for player in (hanzo, zarya, genji):
                        player.mover.body = None
                    if sonic:
                        fight.configure_sonic(sonic_tests.profile())
                        run.step({hanzo: (0, 0, 8)})
                        run.step({})
                    for _ in range(70):
                        run.step({hanzo: (0, 0, 1), genji: (-32768, 0, 0)})
                    cast = aim(fight._eye(zarya), (0, 1, -8))
                    run.step({hanzo: (0, 0, 1), genji: (-32768, 0, 16),
                              zarya: (*cast, 16 if protected else 0)})
                    barriers = fight.zarya_barriers
                    if protected:
                        self.assertEqual(barriers.projected[zarya.body][1:], (200, hanzo.body))
                    run.step({genji: (-32768, 0, 0)})
                    arrow, = fight.rockets
                    self.assertIs(arrow.shooter, hanzo)
                    self.assertEqual(arrow.shot.definition, 0x0400000000000149 if sonic
                                     else 0x0400000000000144)
                    for _ in range(30):
                        run.step({genji: (-32768, 0, 0)})
                        if not fight.rockets:
                            break
                    self.assertFalse(fight.rockets)
                    self.assertIs(arrow.shooter, genji)
                    self.assertEqual(arrow.source_entity, genji.body)
                    self.assertEqual(fight.vitals[hanzo.body].total, 200 if protected else 75)
                    self.assertEqual(fight.vitals[genji.body].total, 200)
                    if protected:
                        self.assertEqual(barriers.projected[zarya.body][1:], (75, hanzo.body))
                        self.assertEqual(zarya.body_script.component.entity_var(87).value(), 25)
                        self.assertFalse(fight.sonars)
                        self.assertFalse(fight.revealed_to(genji, hanzo))
                    elif sonic:
                        beacon, = fight.sonars
                        self.assertEqual(beacon.attached, hanzo.body)
                        self.assertTrue(fight.revealed_to(genji, hanzo))

    def test_nearest_of_orisa_and_inflight_zarya_barrier_gets_actual_sonic(self):
        from ow174.game import zarya_barrier

        for zarya_first in (False, True):
            with self.subTest(zarya_first=zarya_first):
                run, hanzo, orisa, shield = self.deploy_orisa_barrier()
                match = run.match
                fight = match.combat
                fight.configure_sonic(sonic_tests.profile())
                hanzo.mover.position[:] = (0, 0, -8)
                hanzo.mover.body = None
                origin = fight._eye(hanzo)
                distance = fight.orisa_barriers.intersect(shield, origin, (0, 0, 1), 20)
                self.assertIsNotNone(distance)
                crossing = origin[2] + distance
                zarya = match.add_player(3, "Zarya", zarya_barrier.HERO, orisa.team, False)
                in_world(match, zarya)
                run.players.append(zarya)
                self.assertTrue(match.switch_hero(zarya, heroes()[zarya_barrier.HERO]))
                zarya.mover.position[:] = (0, 0, crossing + (-3 if zarya_first else 3))
                zarya.mover.body = None
                run.step({hanzo: (0, 0, 8)})
                run.step({})
                for _ in range(70):
                    run.step({hanzo: (0, 0, 1)})
                run.step({})
                arrow, = fight.rockets
                self.assertIsNone(fight.zarya_barriers.phase(zarya))
                run.step({zarya: (0, 0, 8)})
                self.assertIn(arrow, fight.rockets)
                bubble = fight.zarya_barriers.trace(hanzo, arrow.position, arrow.direction, 20)
                mesh = fight.orisa_barriers.intersect(shield, arrow.position, arrow.direction, 20)
                self.assertIsNotNone(bubble)
                self.assertIs(bubble[1], zarya)
                self.assertIsNotNone(mesh)
                self.assertEqual(bubble[0] < mesh, zarya_first)
                for _ in range(20):
                    run.step({})
                    if not fight.rockets:
                        break
                self.assertFalse(fight.rockets)
                self.assertEqual(shield.health, 600 if zarya_first else 475)
                self.assertEqual(fight.zarya_barriers.pools[zarya.body][1], 75 if zarya_first else 200)
                energy = zarya.body_script.component.entity_var(87).value() or 0
                self.assertEqual(energy, 25 if zarya_first else 0)
                self.assertEqual(fight.vitals[zarya.body].total, 400)
                self.assertEqual(fight.vitals[orisa.body].total, 450)
                self.assertFalse(fight.sonars)
                self.assertFalse(fight.revealed_to(hanzo, zarya))
                self.assertFalse(fight.revealed_to(hanzo, orisa))

    def test_nearer_zarya_barrier_or_sigma_grasp_consumes_actual_sonic_once(self):
        from ow174.game import matrix, sigma_grasp

        tags = os.environ.get("ORPHEUS_MATRIX_TAGS")
        if not tags:
            self.skipTest("prepared projectile tags required")
        admitted = matrix.load_admitted_definitions(Path(tags))
        for sigma_first in (False, True):
            with self.subTest(sigma_first=sigma_first):
                run, hanzo, zarya, fight = self.zarya_arena()
                match = run.match
                match.game_map = replace(match.game_map, free_for_all=False)
                hanzo.team = 1 - zarya.team
                hanzo.mover.position[:] = (0, 0, -8)
                hanzo.mover.body = None
                sigma = match.add_player(3, "Sigma", sigma_grasp.SIGMA, zarya.team, False)
                in_world(match, sigma)
                run.players.append(sigma)
                self.assertTrue(match.switch_hero(sigma, heroes()[sigma_grasp.SIGMA]))
                sigma.team = zarya.team
                sigma.mover.position[:] = (0, 0, 0 if sigma_first else 9)
                sigma.mover.body = None
                fight.matrix_definitions = admitted
                fight.configure_sonic(sonic_tests.profile())
                run.step({hanzo: (0, 0, 8)})
                run.step({})
                for _ in range(70):
                    run.step({hanzo: (0, 0, 1), sigma: (-32768, 0, 0)})
                run.step({hanzo: (0, 0, 1), sigma: (-32768, 0, 8), zarya: (0, 0, 8)})
                run.step({sigma: (-32768, 0, 0)})
                arrow, = fight.rockets
                target = next(t for t in fight._targets() if t.owner is sigma)
                grasp = next(i for i in sigma.body_script.component.instances.values()
                             if i.graph.index == sigma_grasp.GRAPH)
                absorb = fight.grasp.trace(target, arrow.position, arrow.direction, 30)
                barrier = fight.zarya_barriers.trace(hanzo, arrow.position, arrow.direction, 30)
                self.assertIsNotNone(absorb)
                self.assertIsNotNone(barrier)
                self.assertEqual(absorb[0] < barrier[0], sigma_first)
                for _ in range(30):
                    run.step({sigma: (-32768, 0, 0)})
                    if not fight.rockets:
                        break
                self.assertFalse(fight.rockets)
                self.assertEqual(fight.zarya_barriers.pools[zarya.body][1], 200 if sigma_first else 75)
                self.assertEqual(zarya.body_script.component.entity_var(87).value() or 0, 0 if sigma_first else 25)
                self.assertEqual(grasp.find_var(0, 21312).value(), 125 if sigma_first else 0)
                self.assertEqual(fight.vitals[zarya.body].total, 400)
                self.assertEqual(fight.vitals_of(target).total, 400)
                self.assertFalse(fight.sonars)
                self.assertFalse(fight.revealed_to(hanzo, sigma))
                for _ in range(160):
                    if not grasp.states[1].active:
                        break
                    run.step({})
                self.assertFalse(grasp.states[1].active)
                run.step({})
                self.assertEqual(fight.vitals_of(target).bonus_shields, 75 if sigma_first else 0)

    def test_nearest_of_orisa_barrier_and_sigma_grasp_gets_actual_sonic(self):
        from ow174.game import matrix, sigma_grasp

        tags = os.environ.get("ORPHEUS_MATRIX_TAGS")
        if not tags:
            self.skipTest("prepared projectile tags required")
        admitted = matrix.load_admitted_definitions(Path(tags))
        for sigma_first in (False, True):
            with self.subTest(sigma_first=sigma_first):
                run, hanzo, orisa, shield = self.deploy_orisa_barrier()
                match = run.match
                fight = match.combat
                fight.matrix_definitions = admitted
                fight.configure_sonic(sonic_tests.profile())
                hanzo.mover.position[:] = (0, 0, -8)
                hanzo.mover.body = None
                origin = fight._eye(hanzo)
                distance = fight.orisa_barriers.intersect(shield, origin, (0, 0, 1), 20)
                self.assertIsNotNone(distance)
                crossing = origin[2] + distance
                sigma = match.add_player(3, "Sigma", sigma_grasp.SIGMA, orisa.team, False)
                in_world(match, sigma)
                run.players.append(sigma)
                self.assertTrue(match.switch_hero(sigma, heroes()[sigma_grasp.SIGMA]))
                sigma.mover.position[:] = (0, 0, crossing + (-3 if sigma_first else 3))
                sigma.mover.body = None
                grasp = next(i for i in sigma.body_script.component.instances.values()
                             if i.graph.index == sigma_grasp.GRAPH)
                run.step({hanzo: (0, 0, 8)})
                run.step({})
                for _ in range(70):
                    run.step({hanzo: (0, 0, 1), sigma: (-32768, 0, 0)})
                run.step({hanzo: (0, 0, 1), sigma: (-32768, 0, 8)})
                run.step({sigma: (-32768, 0, 0)})
                arrow, = fight.rockets
                target = next(t for t in fight._targets() if t.owner is sigma)
                intercept = fight.grasp.trace(target, arrow.position, arrow.direction, 20)
                barrier = fight.orisa_barriers.intersect(shield, arrow.position, arrow.direction, 20)
                self.assertIsNotNone(intercept)
                self.assertIsNotNone(barrier)
                self.assertEqual(intercept[0] < barrier, sigma_first)
                for _ in range(30):
                    run.step({sigma: (-32768, 0, 0)})
                    if arrow not in fight.rockets:
                        break
                self.assertFalse(fight.rockets)
                self.assertEqual(shield.health, 600 if sigma_first else 475)
                self.assertEqual(grasp.find_var(0, 21312).value(), 125 if sigma_first else 0)
                self.assertEqual(fight.vitals_of(target).total, 400)
                self.assertFalse(hanzo.client.messages(combat.HIT))
                self.assertFalse(fight.sonars)
                for _ in range(160):
                    if not grasp.states[1].active:
                        break
                    run.step({})
                self.assertFalse(grasp.states[1].active)
                run.step({})
                self.assertEqual(fight.vitals_of(target).bonus_shields, 75 if sigma_first else 0)

    def prepare_matrix_resources(self):
        from ow174.game import matrix, mover
        from ow174.game.script import graph

        paths = [os.environ.get("ORPHEUS_MATRIX_" + name) for name in ("GRAPHS", "MOVERS", "TAGS")]
        additive = graph.GRAPH_SUPPLEMENT_PATH is not None and mover.MOVERS_SUPPLEMENT_PATH is not None
        if not paths[2] or (not additive and not all(paths[:2])):
            self.skipTest("prepared build 104319 0407 graphs, movers and projectile tags required")
        if not additive:
            self.enterContext(patch.object(graph, "GRAPHS_PATH", Path(paths[0])))
            self.enterContext(patch.object(mover, "MOVERS_PATH", Path(paths[1])))
        self.addCleanup(matrix_tests.HanzoMatrixTests.reset_catalogs)
        matrix_tests.HanzoMatrixTests.reset_catalogs()
        return matrix.load_admitted_definitions(Path(paths[2]))

    def test_nearer_zarya_barrier_or_matrix_intercepts_actual_sonic(self):
        from ow174.game import matrix

        admitted = self.prepare_matrix_resources()
        for matrix_first in (False, True):
            with self.subTest(matrix_first=matrix_first):
                run, hanzo, zarya, fight = self.zarya_arena()
                match = run.match
                match.game_map = replace(match.game_map, free_for_all=False)
                hanzo.team = 1 - zarya.team
                hanzo.mover.position[:] = (0, 0, -8)
                hanzo.mover.body = None
                mech = match.add_player(3, "D.Va", matrix.DVA, zarya.team, False)
                in_world(match, mech)
                run.players.append(mech)
                self.assertTrue(match.switch_hero(mech, replace(heroes()[matrix.DVA], body=matrix.MECH)))
                mech.team = zarya.team
                mech.mover.position[:] = (0, 0, 7 if matrix_first else 20)
                mech.mover.body = None
                fight.matrix_definitions = admitted
                fight.configure_sonic(sonic_tests.profile())
                run.step({hanzo: (0, 0, 8)})
                run.step({})
                for _ in range(70):
                    run.step({hanzo: (0, 0, 1), mech: (-32768, 0, 0)})
                run.step({hanzo: (0, 0, 1), mech: (-32768, 0, 2), zarya: (0, 0, 8)})
                run.step({mech: (-32768, 0, 2)})
                arrow, = fight.rockets
                lease = arrow.shot
                target = next(t for t in fight._targets() if t.owner is mech)
                intercept = matrix.trace(target, arrow.position, arrow.direction, 32)
                barrier = fight.zarya_barriers.trace(hanzo, arrow.position, arrow.direction, 32)
                self.assertIsNotNone(intercept)
                self.assertIsNotNone(barrier)
                self.assertEqual(intercept < barrier[0], matrix_first)
                for _ in range(30):
                    run.step({mech: (-32768, 0, 2)})
                    if not fight.rockets:
                        break
                self.assertFalse(fight.rockets)
                self.assertNotIn(lease, fight.projectiles.shots)
                self.assertEqual(fight.zarya_barriers.pools[zarya.body][1], 200 if matrix_first else 75)
                self.assertEqual(zarya.body_script.component.entity_var(87).value() or 0, 0 if matrix_first else 25)
                self.assertEqual(fight.vitals[zarya.body].total, 400)
                self.assertEqual(fight.vitals_of(target).total, 600)
                self.assertFalse(fight.sonars)
                self.assertFalse(hanzo.client.messages(combat.HIT))

    def test_nearer_orisa_barrier_or_matrix_intercepts_actual_bow_and_sonic(self):
        from ow174.game import matrix

        admitted = self.prepare_matrix_resources()

        for attack in ("bow", "sonic"):
            for matrix_first in (False, True):
                with self.subTest(attack=attack, matrix_first=matrix_first):
                    run, hanzo, orisa, shield = self.deploy_orisa_barrier()
                    match = run.match
                    fight = match.combat
                    fight.matrix_definitions = admitted
                    if attack == "sonic":
                        fight.configure_sonic(sonic_tests.profile())
                    hanzo.mover.position[:] = (0, 0, -8)
                    hanzo.mover.body = None
                    origin = fight._eye(hanzo)
                    distance = fight.orisa_barriers.intersect(shield, origin, (0, 0, 1), 32)
                    self.assertIsNotNone(distance)
                    crossing = origin[2] + distance
                    mech = match.add_player(3, "D.Va", matrix.DVA, orisa.team, False)
                    in_world(match, mech)
                    run.players.append(mech)
                    self.assertTrue(match.switch_hero(mech, replace(heroes()[matrix.DVA], body=matrix.MECH)))
                    mech.mover.position[:] = (0, 0, crossing + (3 if matrix_first else 17))
                    mech.mover.body = None
                    if attack == "sonic":
                        run.step({hanzo: (0, 0, 8)})
                        run.step({})
                    for _ in range(70):
                        run.step({hanzo: (0, 0, 1), mech: (-32768, 0, 0)})
                    run.step({hanzo: (0, 0, 1), mech: (-32768, 0, 2)})
                    run.step({mech: (-32768, 0, 2)})
                    arrow, = fight.rockets
                    target = next(t for t in fight._targets() if t.owner is mech)
                    intercept = matrix.trace(target, arrow.position, arrow.direction, 32)
                    barrier = fight.orisa_barriers.intersect(shield, arrow.position, arrow.direction, 32)
                    self.assertIsNotNone(intercept)
                    self.assertIsNotNone(barrier)
                    self.assertEqual(intercept < barrier, matrix_first)
                    for _ in range(30):
                        run.step({mech: (-32768, 0, 2)})
                        if not fight.rockets:
                            break
                    self.assertFalse(fight.rockets)
                    self.assertEqual(shield.health, 600 if matrix_first else 475)
                    self.assertEqual(fight.vitals[orisa.body].total, 450)
                    self.assertEqual(fight.vitals_of(target).total, 600)
                    self.assertFalse(hanzo.client.messages(combat.HIT))
                    self.assertFalse(fight.sonars)

    def test_nearer_orisa_barrier_or_wall_determines_sonic_reveal(self):
        for wall_first in (False, True):
            with self.subTest(wall_first=wall_first):
                run, hanzo, orisa, shield = self.deploy_orisa_barrier()
                fight = run.match.combat
                fight.configure_sonic(sonic_tests.profile())
                origin = fight._eye(hanzo)
                distance = fight.orisa_barriers.intersect(shield, origin, (0, 0, 1), 20)
                self.assertIsNotNone(distance)
                wall_z = origin[2] + distance + (-1 if wall_first else 1)
                run.match.collision_world = World(
                    box(-20, 20, -1, 0, -20, 20) + wall(-5, wall_z, 5, wall_z, y0=0, y1=6)
                )
                run.step({hanzo: (0, 0, 8)})
                run.step({})
                for _ in range(70):
                    run.step({hanzo: (0, 0, 1)})
                run.step({})
                for _ in range(20):
                    run.step({})
                    if not fight.rockets:
                        break
                self.assertFalse(fight.rockets)
                self.assertEqual(shield.health, 600 if wall_first else 475)
                self.assertEqual(fight.vitals[orisa.body].total, 450)
                self.assertFalse(hanzo.client.messages(combat.HIT))
                self.assertEqual(fight.revealed_to(hanzo, orisa), wall_first)
                if wall_first:
                    beacon, = fight.sonars
                    self.assertIsNone(beacon.attached)
                    self.assertAlmostEqual(beacon.position[2], wall_z, places=4)
                else:
                    self.assertFalse(fight.sonars)

    def test_quick_melee_can_contact_deployed_orisa_mesh_and_body_independently(self):
        for shield_distance, body_offset, allied, wall_between, shield_loss, body_loss in (
            (1.5, 4, False, False, 30, 0),
            (1.5, 0.5, False, False, 30, 25),
            (1.5, 0.5, False, True, 0, 0),
            (1.5, 0.5, True, False, 0, 0),
            (2.4, 4, False, False, 30, 0),
            (2.7, 4, False, False, 0, 0),
            (4, -2.5, False, False, 0, 25),
        ):
            with self.subTest(shield_distance=shield_distance, body_offset=body_offset,
                              allied=allied, wall_between=wall_between):
                run, hanzo, orisa, shield = self.deploy_orisa_barrier()
                fight = run.match.combat
                run.match.game_map = replace(run.match.game_map, free_for_all=False)
                hanzo.team = orisa.team if allied else 1 - orisa.team
                origin = fight._eye(hanzo)
                distance = fight.orisa_barriers.intersect(shield, origin, (0, 0, 1), 20)
                self.assertIsNotNone(distance)
                crossing = origin[2] + distance
                hanzo.mover.position[:] = (0, 0, crossing - shield_distance)
                hanzo.mover.body = None
                orisa.mover.position[:] = (0, 0, crossing + body_offset)
                orisa.mover.body = None
                if wall_between:
                    wall_z = crossing - 0.75
                    run.match.collision_world = World(
                        box(-20, 20, -1, 0, -20, 20) + wall(-5, wall_z, 5, wall_z, y0=0, y1=6)
                    )
                for _ in range(25):
                    run.step({})
                pool = fight.vitals_of(next(t for t in fight._targets() if t.owner is orisa))
                run.step({hanzo: (0, 0, 0x800)})
                for _ in range(20):
                    run.step({})
                self.assertEqual(shield.health, 600 - shield_loss)
                self.assertEqual(pool.total, 450 - body_loss)
                self.assertEqual(len(hanzo.client.messages(combat.HIT)), int(bool(body_loss)))

    def reinhardt_cover_arena(self, *, allied, attacker_hero=SOLDIER, attacker_z=0):
        run = Run(names=("Attacker", "Reinhardt", "Hanzo"))
        match = run.match
        attacker, rein, hanzo = run.players
        match.bots.clear()
        match._pending_bot_scripts.clear()
        match.collision_world = flat_world(0)
        match.game_map = replace(match.game_map, free_for_all=False)
        for player, hero in ((attacker, attacker_hero), (rein, reinhardt_barrier.HERO),
                             (hanzo, dragon_tests.dragon.HANZO)):
            if player.hero.guid != hero:
                self.assertTrue(match.switch_hero(player, heroes()[hero]))
        attacker.team = 0
        rein.team = 0 if allied else 1
        hanzo.team = 1
        for player, position in ((attacker, (0, 0, attacker_z)), (rein, (1.5, 0, 5)), (hanzo, (0, 0, 6))):
            player.mover.position[:] = position
            player.mover.body = None
        fight = match.combat
        fight.barriers.geometry = self.geometry
        for _ in range(25):
            run.step({})
        held = {rein: (-32768, 0, 2)}
        for _ in range(5):
            run.step(held)
        self.assertTrue(fight.barriers.states[rein.body].active)
        return run, attacker, rein, hanzo, held

    def test_flashbang_reinhardt_cover_preserves_hanzo_draw_even_on_break(self):
        for allied, initial, final_shield, final_hanzo in (
            (False, 1600, 1575, 200), (False, 15, 0, 200), (True, 1600, 1600, 175),
        ):
            with self.subTest(allied=allied, initial=initial):
                run, caster, rein, hanzo, held = self.reinhardt_cover_arena(
                    allied=allied, attacker_hero=0x02E0000000000042, attacker_z=3,
                )
                fight = run.match.combat
                shield = fight.barriers.states[rein.body]
                flash = aim(fight._eye(caster), (0, 1, 6))
                bow = aim(fight._eye(hanzo), (0, 1, 3))
                draw = {**held, hanzo: (*bow, 1)}
                for _ in range(70):
                    run.step(draw)
                if initial < 1600:
                    fight.barriers.damage(rein, 1600 - initial)
                self.assertIn(20, hanzo.body_script.component.held)
                run.step({**draw, caster: (*flash, 16)})
                for _ in range(35):
                    if fight.rockets:
                        break
                    run.step(draw)
                grenade, = fight.rockets
                self.assertEqual((grenade.blast.amount, grenade.blast.effect), (25, combat.FLASHBANG_EFFECT))
                for _ in range(25):
                    if grenade not in fight.rockets:
                        break
                    run.step(draw)
                self.assertNotIn(grenade, fight.rockets)
                self.assertEqual(shield.current, final_shield)
                self.assertEqual(fight.vitals[hanzo.body].total, final_hanzo)
                self.assertEqual(fight.is_stunned(hanzo), allied)
                run.step({**held, hanzo: (*bow, 0)})
                if allied:
                    self.assertFalse(fight.rockets)
                else:
                    arrow, = fight.rockets
                    self.assertIs(arrow.shooter, hanzo)
                    self.assertEqual(arrow.direct.amount, 125)

    def test_helix_direct_and_ground_blast_against_reinhardt_cover(self):
        for ground, blocked, allied, initial, final_shield, final_hanzo in (
            (None, False, False, 1600, 1480, 200), (None, False, False, 100, 0, 200),
            (None, False, True, 1600, 1600, 80), (3.2, False, False, 1600, 1520, 200),
            (3.2, False, False, 60, 0, 200), (3.2, True, False, 1600, 1600, 200),
            (-1.0, False, False, 1600, 1600, 200),
        ):
            with self.subTest(ground=ground, blocked=blocked, allied=allied, initial=initial):
                run, soldier, rein, hanzo, held = self.reinhardt_cover_arena(allied=allied)
                match = run.match
                fight = match.combat
                barriers = fight.barriers
                shield = barriers.states[rein.body]
                if initial < 1600:
                    barriers.damage(rein, 1600 - initial)
                if blocked:
                    match.collision_world = World(
                        box(-20, 20, -1, 0, -20, 20) + wall(-5, ground + 0.12, 5, ground + 0.12, y0=0, y1=6)
                    )
                point = (0, 1, 6) if ground is None else (0, 0, ground)
                angles = aim(fight._eye(soldier), point)
                run.step({**held, soldier: (*angles, 2)})
                rocket, = fight.rockets
                self.assertEqual((rocket.direct.amount, rocket.blast.amount), (40, 80))
                if ground is None:
                    distance = barriers.intersect(rein, rocket.position, rocket.direction, 12)
                    self.assertIsNotNone(distance)
                    contact_z = rocket.position[2] + distance * rocket.direction[2]
                for _ in range(25):
                    run.step(held)
                    if not fight.rockets:
                        break
                self.assertFalse(fight.rockets)
                self.assertEqual(shield.current, final_shield)
                self.assertEqual(fight.vitals[hanzo.body].total, final_hanzo)
                rein_target = next(t for t in fight._targets() if t.owner is rein)
                self.assertEqual(fight.vitals_of(rein_target).total, 500)
                if ground is not None:
                    self.assertAlmostEqual(rocket.position[1], 0, places=4)
                    self.assertAlmostEqual(rocket.position[2], ground, places=2)
                elif allied:
                    self.assertGreater(rocket.position[2], contact_z)
                else:
                    self.assertAlmostEqual(rocket.position[2], contact_z, places=4)

    def test_direct_helix_hits_orisa_mesh_before_hanzo_and_explodes_once(self):
        for initial_hp, allied, final_hp, hanzo_hp in (
            (600, False, 480, 200), (100, False, 0, 200),
            (0, False, 0, 80), (600, True, 600, 80),
        ):
            with self.subTest(initial_hp=initial_hp, allied=allied):
                run, hanzo, orisa, shield = self.deploy_orisa_barrier()
                match = run.match
                match.game_map = replace(match.game_map, free_for_all=False)
                soldier_team = orisa.team if allied else 1 - orisa.team
                hanzo.team = 1 - soldier_team
                fight = match.combat
                _, _, z = shield.position
                soldier = match.add_player(3, "Soldier", SOLDIER, soldier_team, False)
                in_world(match, soldier)
                run.players.append(soldier)
                self.assertTrue(match.switch_hero(soldier, heroes()[SOLDIER]))
                soldier.team = soldier_team
                soldier.mover.position[:] = (0, 0, z - 7)
                soldier.mover.body = None
                hanzo.mover.position[:] = (0, 0, z + 1)
                hanzo.mover.body = None
                for _ in range(25):
                    run.step({})
                if initial_hp < 600:
                    fight.orisa_barriers.damage(shield, 600 - initial_hp)
                origin = fight._eye(soldier)
                angles = aim(origin, (0, 1, z + 1))
                run.step({soldier: (*angles, 2)})
                rocket, = fight.rockets
                self.assertEqual(rocket.shot.definition, 0x040000000000072C)
                self.assertEqual((rocket.direct.amount, rocket.blast.amount), (40, 80))
                distance = fight.orisa_barriers.intersect(shield, origin, rocket.direction, 20)
                self.assertIsNotNone(distance)
                for _ in range(30):
                    run.step({})
                    if not fight.rockets:
                        break
                self.assertFalse(fight.rockets)
                self.assertEqual(shield.health, final_hp)
                self.assertEqual(fight.vitals[hanzo.body].total, hanzo_hp)
                contact_z = origin[2] + distance * rocket.direction[2]
                if initial_hp and not allied:
                    self.assertAlmostEqual(rocket.position[2], contact_z, places=4)
                    self.assertEqual(fight.vitals[orisa.body].total, 450)
                    self.assertFalse(soldier.client.messages(combat.HIT))
                else:
                    self.assertGreater(rocket.position[2], contact_z)

    def test_ground_helix_splash_damages_orisa_mesh_and_shields_hanzo(self):
        for distance, blocked, initial_hp, final_hp in (
            (0.35, False, 600, 520), (0.35, True, 600, 600), (4, False, 600, 600),
            (0.35, False, 60, 0), (0.35, False, 0, 0),
        ):
            with self.subTest(distance=distance, blocked=blocked, initial_hp=initial_hp):
                run, hanzo, orisa, shield = self.deploy_orisa_barrier()
                match = run.match
                match.game_map = replace(match.game_map, free_for_all=False)
                hanzo.team = orisa.team
                fight = match.combat
                x, _, z = shield.position
                point = (x, 0, z - distance)
                soldier = match.add_player(3, "Soldier", SOLDIER, 1 - orisa.team, False)
                in_world(match, soldier)
                run.players.append(soldier)
                self.assertTrue(match.switch_hero(soldier, heroes()[SOLDIER]))
                soldier.team = 1 - orisa.team
                soldier.mover.position[:] = (0, 0, z - 7)
                soldier.mover.body = None
                hanzo.mover.position[:] = (0, 0, z + 1)
                hanzo.mover.body = None
                if blocked:
                    wall_z = z - 0.22
                    match.collision_world = World(
                        box(-20, 20, -1, 0, -20, 20) + wall(-5, wall_z, 5, wall_z, y0=0, y1=6)
                    )
                for _ in range(25):
                    run.step({})
                if initial_hp < 600:
                    fight.orisa_barriers.damage(shield, 600 - initial_hp)
                angles = aim(fight._eye(soldier), point)
                run.step({soldier: (*angles, 2)})
                rocket, = fight.rockets
                self.assertEqual(rocket.shot.definition, 0x040000000000072C)
                self.assertEqual(rocket.blast.amount, 80)
                for _ in range(30):
                    run.step({})
                    if not fight.rockets:
                        break
                self.assertFalse(fight.rockets)
                self.assertAlmostEqual(rocket.position[1], 0, places=4)
                self.assertAlmostEqual(rocket.position[2], point[2], places=2)
                self.assertEqual(shield.health, final_hp)
                self.assertEqual(shield.active(), final_hp > 0)
                if initial_hp:
                    self.assertEqual(fight.vitals[hanzo.body].total, 200)
                    self.assertEqual(fight.vitals[orisa.body].total, 450)
                else:
                    self.assertLess(fight.vitals[hanzo.body].total, 200)
                    self.assertLess(fight.vitals[orisa.body].total, 450)

    def test_breaking_orisa_barrier_contains_final_bow_hit_then_sonic_attaches(self):
        run, hanzo, orisa, shield = self.deploy_orisa_barrier()
        fight = run.match.combat
        fight.configure_sonic(sonic_tests.profile())
        target = next(t for t in fight._targets() if t.owner is orisa)
        pool = fight.vitals_of(target)
        angles = aim(fight._eye(hanzo), (0, 0.8, 5))

        def step(button=0):
            run.step({hanzo: (*angles, button)})

        for remaining in (475, 350, 225, 100, 0):
            self.draw(step)
            self.assertEqual(shield.health, remaining)
            self.assertEqual(shield.active(), bool(remaining))
            self.assertEqual(pool.total, 450)
            self.assertFalse(hanzo.client.messages(combat.HIT))
        step(8)
        step()
        self.draw(step)
        self.assertEqual(pool.total, 330)
        hits = hanzo.client.messages(combat.HIT)
        self.assertEqual([hit["+0x78"]["+0x4"] for hit in hits], [120])
        beacon, = fight.sonars
        self.assertEqual(beacon.attached, orisa.body)
        self.assertTrue(fight.revealed_to(hanzo, orisa))
        for _ in range(150):
            if shield.retired:
                break
            step()
        self.assertTrue(shield.retired)
        self.assertFalse(fight.orisa_barriers.actors)

    def test_orisa_replacement_keeps_old_arrow_cover_until_new_landing(self):
        run, hanzo, orisa, old = self.deploy_orisa_barrier()
        match = run.match
        fight = match.combat
        service = fight.orisa_barriers
        instance = next(i for i in orisa.body_script.component.instances.values() if i.graph.index == 0x0BD7)
        target = match.add_player(3, "Behind old barrier", SOLDIER, orisa.team, False)
        in_world(match, target)
        run.players.append(target)
        self.assertTrue(match.switch_hero(target, heroes()[SOLDIER]))
        target.team = orisa.team
        target.mover.position[:] = (0, 0, 5)
        target.mover.body = None
        orisa.mover.position[:] = (7, 0, 5)
        orisa.mover.body = None
        old_aim = aim(fight._eye(hanzo), (0, 0.8, 5))
        pool = fight.vitals_of(next(t for t in fight._targets() if t.owner is target))

        run.step({orisa: (-32768, 8192, 16)})
        for _ in range(35):
            run.step({})
        self.assertGreater(instance.vars[61].value(), 0)
        self.assertFalse(service.flights)
        self.assertIs(service.actors[orisa.body], old)
        run.frame += 650
        match.update(run.frame * combat.TICK, run.frame)
        for _ in range(70):
            run.step({hanzo: (*old_aim, 1)})
        self.assertEqual(instance.vars[61].value(), 0)
        self.assertFalse(service.flights)

        run.step({hanzo: (*old_aim, 1), orisa: (-32768, 8192, 16)})
        for _ in range(40):
            if service.flights:
                break
            run.step({hanzo: (*old_aim, 1), orisa: (-32768, 8192, 0)})
        self.assertTrue(service.flights)
        self.assertIs(service.actors[orisa.body], old)
        run.step({hanzo: (*old_aim, 0)})
        for _ in range(12):
            if old.health < 600:
                break
            run.step({})
        self.assertEqual(old.health, 475)
        self.assertEqual(pool.total, 200)
        self.assertTrue(service.flights)
        for _ in range(50):
            if service.actors[orisa.body] is not old:
                break
            run.step({})
        new = service.actors[orisa.body]
        self.assertIsNot(new, old)
        self.assertTrue(old.retired)
        self.assertGreater(new.position[0], old.position[0] + 6)
        self.assertEqual(new.health, 600)
        for _ in range(20):
            run.step({})
        self.draw(lambda button=0: run.step({hanzo: (*old_aim, button)}))
        self.assertEqual(pool.total, 75)
        self.assertEqual(new.health, 600)

        hanzo.mover.position[:] = (7, 0, -3)
        hanzo.mover.body = None
        new_aim = aim(fight._eye(hanzo), (7, 0.8, 5))
        for _ in range(20):
            run.step({})
        self.draw(lambda button=0: run.step({hanzo: (*new_aim, button)}))
        self.assertEqual(new.health, 475)
        self.assertEqual(fight.vitals[orisa.body].total, 450)

    def test_lethal_bow_before_orisa_throw_release_prevents_deployment(self):
        run, hanzo, orisa, service = self.orisa_arena()
        fight = run.match.combat
        hanzo.mover.position[:] = (0, 0, 3)
        hanzo.mover.body = None
        target = next(t for t in fight._targets() if t.owner is orisa)
        pool = fight.vitals_of(target)
        pool.damage(330)
        angles = aim(fight._eye(hanzo), (0, 0.8, 5))
        instance = next(i for i in orisa.body_script.component.instances.values() if i.graph.index == 0x0BD7)
        for _ in range(70):
            run.step({hanzo: (*angles, 1)})
        with patch.object(service, "launch", wraps=service.launch) as launch:
            run.step({hanzo: (*angles, 0), orisa: (-32768, 0, 16)})
            self.assertTrue(instance.states[7].active)
            self.assertFalse(service.flights)
            for _ in range(12):
                if pool.dead:
                    break
                run.step({})
            self.assertTrue(pool.dead)
            self.assertEqual(pool.killer, hanzo.body)
            for _ in range(70):
                run.step({})
            launch.assert_not_called()
        self.assertFalse(service.flights)
        self.assertFalse(service.actors)

    def test_launched_orisa_throw_can_land_after_hanzo_kills_its_owner(self):
        self.verify_launched_orisa_throw_at_owner_death()

    def test_launched_orisa_throw_retires_if_owner_respawns_before_landing(self):
        self.verify_launched_orisa_throw_at_owner_death(early_respawn=True)

    def verify_launched_orisa_throw_at_owner_death(self, *, early_respawn=False):
        run, hanzo, orisa, service = self.orisa_arena()
        match = run.match
        fight = match.combat
        protected = match.add_player(3, "Behind future barrier", SOLDIER, orisa.team, False)
        in_world(match, protected)
        run.players.append(protected)
        self.assertTrue(match.switch_hero(protected, heroes()[SOLDIER]))
        protected.team = orisa.team
        protected.mover.position[:] = (0, 0, 7)
        protected.mover.body = None
        targets = {t.owner: t for t in fight._targets()}
        orisa_pool = fight.vitals_of(targets[orisa])
        protected_pool = fight.vitals_of(targets[protected])
        orisa_pool.damage(330)
        old_body = orisa.body
        angles = aim(fight._eye(hanzo), (0, 0.8, 5))
        for _ in range(70):
            run.step({hanzo: (*angles, 1)})
        run.step({hanzo: (*angles, 1), orisa: (-32768, 0, 16)})
        for _ in range(40):
            if old_body in service.flights:
                break
            run.step({hanzo: (*angles, 1), orisa: (-32768, 0, 0)})
        flight = service.flights[old_body]
        self.assertFalse(service.actors)
        run.step({hanzo: (*angles, 0)})
        respawn = patch.object(combat, "respawn_seconds", return_value=2 * combat.TICK) if early_respawn else nullcontext()
        with respawn:
            for _ in range(15):
                if orisa_pool.dead:
                    break
                run.step({})
        self.assertTrue(orisa_pool.dead)
        self.assertEqual(orisa_pool.killer, hanzo.body)
        self.assertIs(service.flights[old_body], flight)
        self.assertFalse(service.actors)
        if early_respawn:
            for _ in range(6):
                if orisa.body != old_body:
                    break
                run.step({})
            self.assertNotEqual(orisa.body, old_body)
            self.assertIs(service.flights[old_body], flight)
            run.step({})
            self.assertFalse(service.flights)
            for _ in range(60):
                run.step({})
            self.assertFalse(service.actors)
            self.assertEqual(protected_pool.total, 200)
            return
        for _ in range(60):
            if old_body in service.actors:
                break
            run.step({})
        shield = service.actors[old_body]
        self.assertEqual(shield.source_body, old_body)
        self.assertEqual(shield.health, 600)
        self.assertTrue(orisa_pool.dead)

        sx, sy, sz = shield.position
        hanzo.mover.position[:] = (sx, sy, sz - 7)
        hanzo.mover.body = None
        protected.mover.position[:] = (sx, sy, sz + 3)
        protected.mover.body = None
        for _ in range(20):
            run.step({})
        angles = aim(fight._eye(hanzo), (sx, sy + 0.8, sz + 3))
        self.draw(lambda button=0: run.step({hanzo: (*angles, button)}))
        self.assertEqual(shield.health, 475)
        self.assertEqual(protected_pool.total, 200)

    def test_landed_orisa_barrier_keeps_stopping_hanzo_through_owner_death_and_respawn(self):
        run, hanzo, orisa, shield = self.deploy_orisa_barrier()
        match = run.match
        fight = match.combat
        protected = match.add_player(3, "Behind deployed barrier", SOLDIER, orisa.team, False)
        in_world(match, protected)
        run.players.append(protected)
        self.assertTrue(match.switch_hero(protected, heroes()[SOLDIER]))
        protected.team = orisa.team
        protected.mover.position[:] = (0, 0, 5)
        protected.mover.body = None
        orisa.mover.position[:] = (7, 0, 5)
        orisa.mover.body = None
        hanzo.mover.position[:] = (7, 0, -3)
        hanzo.mover.body = None
        targets = {t.owner: t for t in fight._targets()}
        orisa_pool = fight.vitals_of(targets[orisa])
        protected_pool = fight.vitals_of(targets[protected])
        old_body = orisa.body
        orisa_pool.damage(330)
        angles = aim(fight._eye(hanzo), (7, 0.8, 5))
        self.draw(lambda button=0: run.step({hanzo: (*angles, button)}))
        self.assertTrue(orisa_pool.dead)
        self.assertEqual(orisa_pool.killer, hanzo.body)
        self.assertEqual(shield.health, 600)
        self.assertTrue(shield.active())

        hanzo.mover.position[:] = (0, 0, -3)
        hanzo.mover.body = None
        angles = aim(fight._eye(hanzo), (0, 0.8, 5))
        for _ in range(20):
            run.step({})
        self.draw(lambda button=0: run.step({hanzo: (*angles, button)}))
        self.assertEqual(orisa.body, old_body)
        self.assertEqual(protected_pool.total, 200)
        self.assertEqual(shield.health, 475)

        run.frame += math.ceil(combat.respawn_seconds(match) / combat.TICK) + 2
        match.update(run.frame * combat.TICK, run.frame)
        self.assertNotEqual(orisa.body, old_body)
        self.assertIs(fight.orisa_barriers.actors[old_body], shield)
        self.assertEqual(shield.source_body, old_body)
        orisa.mover.position[:] = (7, 0, 5)
        orisa.mover.body = None
        self.draw(lambda button=0: run.step({hanzo: (*angles, button)}))
        self.assertEqual(protected_pool.total, 200)
        self.assertEqual(shield.health, 350)

    def test_initial_dragon_hits_orisa_barrier_and_moving_dragon_damages_beyond_it(self):
        run, hanzo, orisa, shield = self.deploy_orisa_barrier()
        fight = run.match.combat
        fight.configure_dragonstrike(dragon_tests.prepared_profile())
        hanzo.body_script.set_ult_charge(1680)
        target = next(t for t in fight._targets() if t.owner is orisa)
        pool = fight.vitals_of(target)
        self.assertEqual(pool.total, 450)
        angles = aim(fight._eye(hanzo), (0, 1.2, 5))
        run.step({hanzo: (*angles, 32)})
        for _ in range(100):
            if fight.rockets:
                break
            run.step({hanzo: (*angles, 0)})
        arrow, = fight.rockets
        self.assertEqual(arrow.direct.amount, 125)
        with patch.object(fight, "_damage", wraps=fight._damage) as damage:
            for _ in range(40):
                if fight.dragons:
                    break
                run.step({})
            for _ in range(100):
                if pool.total < 450:
                    break
                run.step({})
        flight, = fight.dragons
        self.assertFalse(fight.rockets)
        self.assertEqual(shield.health, 475)
        doses = [call.args[2] for call in damage.call_args_list if call.args[1].owner is orisa]
        self.assertTrue(doses)
        self.assertTrue(all(0 < amount <= flight.profile.core_rate * combat.TICK for amount in doses))
        self.assertEqual(flight.origin, arrow.position)
        self.assertLess(flight.origin[2], orisa.mover.position[2])
        self.assertEqual(flight.source_entity, hanzo.body)
        self.assertLess(pool.total, 450)
        self.assertTrue(shield.active())
        self.assertTrue(flight in fight.dragons)

    def test_raising_or_lowering_barrier_in_flight_changes_bow_and_sonic_contact(self):
        for use_sonic in (False, True):
            for raise_shield in (False, True):
                with self.subTest(sonic=use_sonic, raise_shield=raise_shield):
                    initial = 0 if raise_shield else 2
                    final = 2 if raise_shield else 0
                    fight, hanzo, rein, pool, step = self.arena(held=initial, distance=20)
                    if use_sonic:
                        fight.configure_sonic(sonic_tests.profile())
                        step(8)
                        step()
                    shield = fight.barriers.states[rein.body]
                    for _ in range(70):
                        step(1)
                    step()
                    arrow, = fight.rockets
                    self.assertEqual(shield.active, bool(initial))
                    step(shield=initial)
                    self.assertTrue(arrow in fight.rockets)
                    for _ in range(5):
                        step(shield=final)
                        if shield.active == bool(final):
                            break
                    self.assertEqual(shield.active, bool(final))
                    self.assertTrue(arrow in fight.rockets)
                    self.assertEqual(pool.total, 500)
                    self.assertEqual(shield.current, 1600)

                    for _ in range(30):
                        if arrow not in fight.rockets:
                            break
                        step(shield=final)
                    self.assertFalse(arrow in fight.rockets)
                    self.assertEqual(shield.current, 1475 if raise_shield else 1600)
                    self.assertEqual(pool.total, 500 if raise_shield else 380)
                    hits = hanzo.client.messages(combat.HIT)
                    self.assertEqual([hit["+0x78"]["+0x4"] for hit in hits], [] if raise_shield else [120])
                    if use_sonic and not raise_shield:
                        beacon, = fight.sonars
                        self.assertEqual(beacon.attached, rein.body)
                        self.assertTrue(fight.revealed_to(hanzo, rein))
                    else:
                        self.assertFalse(fight.sonars)

    def test_lowered_rear_and_nearer_wall_have_distinct_outcomes(self):
        for facing, held, wall, health in ((0, 2, False, 380), (-32768, 0, False, 380),
                                           (-32768, 2, True, 500)):
            with self.subTest(facing=facing, held=held, wall=wall):
                fight, _, rein, pool, step = self.arena(facing=facing, held=held)
                for _ in range(70):
                    step(1)
                if wall:
                    fight.match.collision_world = World([
                        (-5, -1, .5, 5, -1, .5, 5, 5, .5),
                        (-5, -1, .5, 5, 5, .5, -5, 5, .5),
                        (5, 5, .5, 5, -1, .5, -5, -1, .5),
                        (-5, 5, .5, 5, 5, .5, -5, -1, .5),
                    ])
                for _ in range(13):
                    step()
                self.assertEqual(pool.total, health)
                self.assertEqual(fight.barriers.states[rein.body].current, 1600)

    def test_initial_dragon_hits_shield_then_transforms_at_contact(self):
        fight, hanzo, rein, pool, step = self.arena()
        fight.configure_dragonstrike(dragon_tests.prepared_profile())
        hanzo.body_script.ult_seconds = 0
        hanzo.body_script.set_ult_charge(1680)
        with patch.object(fight, "_damage", wraps=fight._damage) as damage:
            step(32)
            for _ in range(120):
                step()
                if fight.dragons:
                    break
        attempts = [call.args[2] for call in damage.call_args_list if call.args[1].owner is rein]
        self.assertTrue(attempts)
        self.assertTrue(all(0 < amount < 125 for amount in attempts))
        flight, = fight.dragons
        self.assertEqual(fight.barriers.states[rein.body].current, 1475)
        self.assertLess(flight.origin[2], 6)
        self.assertIs(flight.shooter, hanzo)
        self.assertGreater(pool.total, 490)

    def test_moving_dragonstrike_damages_through_raised_barrier_and_deflect(self):
        fight, hanzo, rein, pool, step = self.arena(distance=24, genji_at=24)
        genji = fight.match.players[2]
        genji_target = next(t for t in fight._targets() if t.owner is genji)
        genji_pool = fight.vitals_of(genji_target)
        shield = fight.barriers.states[rein.body]
        fight.configure_dragonstrike(dragon_tests.prepared_profile())
        hanzo.body_script.ult_seconds = 0
        hanzo.body_script.set_ult_charge(1680)
        step(32)
        for _ in range(130):
            if fight.dragons:
                break
            step()
        flight, = fight.dragons
        self.assertAlmostEqual(flight.origin[2], 10, places=3)
        self.assertEqual(pool.total, 500)
        self.assertEqual(genji_pool.total, 200)
        self.assertEqual(shield.current, 1600)

        step(deflect=16)
        self.assertIsNotNone(arrow_deflection.active_volume(genji_target, 0x03A3, 10))
        for _ in range(90):
            if pool.total < 500 and genji_pool.total < 200:
                break
            step()
        self.assertLess(pool.total, 500)
        self.assertLess(genji_pool.total, 200)
        self.assertTrue(shield.active)
        self.assertEqual(shield.current, 1600)
        self.assertIsNotNone(arrow_deflection.active_volume(genji_target, 0x03A3, 10))
        self.assertIn(flight, fight.dragons)
        self.assertIs(flight.shooter, hanzo)
        self.assertEqual(flight.source_entity, hanzo.body)
        self.assertFalse(fight.rockets)
        self.assertFalse(genji.client.messages(combat.HIT))

    def test_storm_arrows_and_full_draws_break_barrier_without_spill(self):
        fight, hanzo, rein, pool, step = self.arena()
        shield = fight.barriers.states[rein.body]
        step(16)
        step()
        with patch.object(fight.projectiles, "fired", wraps=fight.projectiles.fired) as fired:
            for _ in range(140):
                step(1)
                if fired.call_count == 5:
                    break
        self.assertEqual([call.args[3] for call in fired.call_args_list], [0x040000000000270F] * 5)
        for _ in range(15):
            step()
        self.assertEqual(shield.current, 1250)
        for _ in range(9):
            self.draw(step)
        self.assertEqual(shield.current, 125)
        for _ in range(35):
            step(1)
        for _ in range(12):
            step()
        self.assertGreater(shield.current, 0)
        self.assertLess(shield.current, 125)
        self.draw(step)
        self.assertEqual(shield.current, 0)
        self.assertFalse(shield.active)
        self.assertEqual(pool.total, 500)
        self.assertFalse(hanzo.client.messages(combat.HIT))
        self.draw(step)
        self.assertLess(pool.total, 500)
        for _ in range(600):
            step(shield=0)
            if shield.current == shield.maximum:
                break
        self.assertEqual(shield.current, shield.maximum)
        self.assertFalse(shield.active)
        instance = fight.barriers.instance(rein)
        self.assertFalse(instance.states[35].active)
        for _ in range(130):
            step()
            if shield.active:
                break
        self.assertTrue(shield.active)

    def test_nearest_of_raised_barrier_and_active_deflect_wins(self):
        for genji_at in (2.5, 8):
            with self.subTest(genji_at=genji_at):
                fight, hanzo, rein, pool, step = self.arena(genji_at=genji_at)
                for _ in range(69):
                    step(1)
                step(1, deflect=16)
                step()
                arrow, = fight.rockets
                for _ in range(15):
                    step()
                self.assertFalse(fight.rockets)
                self.assertEqual(pool.total, 500)
                if genji_at < 6:
                    self.assertEqual(arrow.shooter.hero.guid, arrow_deflection.GENJI)
                    self.assertEqual(fight.vitals[hanzo.body].total, 75)
                    self.assertEqual(fight.barriers.states[rein.body].current, 1600)
                else:
                    self.assertIs(arrow.shooter, hanzo)
                    self.assertEqual(fight.barriers.states[rein.body].current, 1475)

    def test_arrow_passes_allied_barrier_to_enemy_body(self):
        fight, hanzo, rein, pool, step = self.arena(genji_at=8)
        genji = fight.match.players[2]
        self.assertNotEqual(hanzo.team, genji.team)
        rein.team = hanzo.team
        self.draw(step)
        self.assertEqual(pool.total, 500)
        self.assertEqual(fight.barriers.states[rein.body].current, 1600)
        self.assertEqual(fight.vitals[genji.body].total, 75)

    def test_melee_mesh_and_body_are_independent_targets(self):
        for distance, wall, shield_loss, body_loss in (
            (2, False, 0, 25), (2.4, False, 30, 25), (3.25, False, 30, 0),
            (6, False, 0, 0), (2.4, True, 0, 0),
        ):
            with self.subTest(distance=distance, wall=wall):
                fight, hanzo, rein, pool, step = self.arena(distance=distance)
                self.assertTrue(fight.barriers.states[rein.body].active)
                if wall:
                    fight.match.collision_world = World([
                        (-5, -1, .3, 5, -1, .3, 5, 5, .3),
                        (-5, -1, .3, 5, 5, .3, -5, 5, .3),
                        (5, 5, .3, 5, -1, .3, -5, -1, .3),
                        (-5, 5, .3, 5, 5, .3, -5, -1, .3),
                    ])
                step(0x800)
                for _ in range(20):
                    step()
                self.assertEqual(fight.barriers.states[rein.body].current, 1600 - shield_loss)
                self.assertEqual(pool.total, 500 - body_loss)
                self.assertEqual(len(hanzo.client.messages(combat.HIT)), int(bool(body_loss)))

    def test_active_deflect_blocks_front_melee_and_rear_takes_damage(self):
        for facing, starting_health, loss in (
            (-32768, 200, 0), (0, 200, 30), (-32768, 20, 0), (0, 20, 20),
        ):
            with self.subTest(facing=facing, starting_health=starting_health):
                fight, hanzo, _, _, step = self.arena(genji_at=1.8, genji_facing=facing)
                genji = fight.match.players[2]
                step(deflect=16)
                target = next(t for t in fight._targets() if t.owner is genji)
                pool = fight.vitals_of(target)
                pool.damage(200 - starting_health)
                self.assertIsNotNone(arrow_deflection.active_volume(target, 0x03A3, 10))
                step(0x800)
                for _ in range(20):
                    step()
                self.assertEqual(pool.total, starting_health - loss)
                self.assertFalse(fight.rockets)
                self.assertFalse(genji.client.messages(combat.HIT))
                hits = hanzo.client.messages(combat.HIT)
                self.assertEqual([hit["+0x78"]["+0x4"] for hit in hits], [loss] if loss else [])
                self.assertEqual(hanzo.body_script.component.entity_var(combat.ULT_CHARGE).value(), loss)
                if pool.dead:
                    self.assertEqual(pool.killer, hanzo.body)
                    self.assertNotIn(genji, [enemy.owner for enemy in fight._enemies(hanzo)])
                    self.assertTrue(genji.body_script.component.owner_dead)
                else:
                    self.assertIsNotNone(arrow_deflection.active_volume(target, 0x03A3, 10))
