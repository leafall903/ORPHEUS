"""Crossfade executes the actual external 0267 graph and changes match health.

Requires an extracted build104319 ORPHEUS_GRAPH_SUPPLEMENT. Missing resource data
is a skipped external fixture, never evidence of completed combat acceptance.
"""

import unittest
from dataclasses import replace
from unittest.mock import patch

import test_lucio_combat as fixtures
from test_game_combat import aim

from ow174.game import bots, combat, healing
from ow174.game.collision import World
from ow174.game.commands import FIRE, Command
from ow174.game.content import heroes
from ow174.game.script import graph
from ow174.game.script.driver import ULT_CHARGE


@unittest.skipUnless(graph.graph(0x0267) is not None, "requires extracted Lucio status supplement")
class LucioAuraTests(unittest.TestCase):
    def scenario(self, healing=True):
        helper = fixtures.LucioCombatTests()
        run, source, enemy, ally = helper.scenario()
        if healing:
            run.step({source: (0, 0, 8)})  # authored Crossfade button35
            for _ in range(30):
                run.step({})
        else:
            run.step({})
        return helper, run, source, enemy, ally

    def wound(self, run, owner, amount=100):
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is owner)
        fight.vitals_of(target).damage(amount)

    def test_healing_before_retained_damage_can_prevent_a_lethal_hit(self):
        for schedule in ((1,) * 5, (5,)):
            with self.subTest(schedule=schedule):
                helper, run, source, enemy, _ = self.scenario()
                fight = run.match.combat
                source.body_script.ult_seconds = 0
                source.body_script.set_ult_charge(0)
                self.wound(run, source, 180.125)
                fight.rockets.append(combat.Rocket(
                    enemy, enemy.team, (0, 1.8, 4), (0, 0, -1), 50, 1.0,
                    combat.HitDef(20), None, last_tick=run.frame,
                ))
                for frames in schedule:
                    run.frame += frames
                    run.match.update(run.frame * combat.TICK, run.frame)
                healed = 5 * (16 / 1.3) * combat.TICK
                meter = source.body_script.component.entity_var(ULT_CHARGE)
                self.assertAlmostEqual(helper.health(run, source), healed - 0.125, places=5)
                self.assertAlmostEqual(meter.value(), healed, places=5)
                self.assertFalse(fight.is_dead(source))
                self.assertFalse(fight.rockets)
                self.assertFalse(fight.respawns)

    def test_authored_ally_and_self_rates_and_enemy_exclusion(self):
        helper, run, source, enemy, ally = self.scenario()
        for owner in (source, ally, enemy):
            self.wound(run, owner)
        for _ in range(64):
            run.step({})
        self.assertAlmostEqual(helper.health(run, ally), 100 + 16 * 64 * combat.TICK, places=5)
        self.assertAlmostEqual(helper.health(run, source), 100 + (16 / 1.3) * 64 * combat.TICK, places=5)
        self.assertEqual(helper.health(run, enemy), 100)

    def test_ana_grenade_denies_normal_and_amp_healing_until_expiry(self):
        for amp, skipped in ((False, 0), (True, 0), (False, 6)):
            with self.subTest(amp=amp, skipped=skipped):
                helper, run, source, enemy, ally = self.scenario()
                run.match.switch_hero(enemy, heroes()[0x02E000000000013B])
                enemy.mover.position[:] = (0, 1, 4)
                ally.mover.position[:] = (6, 1, 0)
                ally.mover.body.teleport((6, 1 + ally.mover._lift(), 0))
                self.wound(run, ally)
                if amp:
                    run.step({source: (0, 0, 16)})
                    for _ in range(10):
                        run.step({})
                fight = run.match.combat
                direction = aim(fight._eye(enemy), (0, 1.95, 0))
                run.step({enemy: (*direction, 16)})
                for _ in range(40):
                    if source.body in fight.healing.anti:
                        break
                    run.step({})
                self.assertIn(source.body, fight.healing.anti)
                effect = fight.healing.anti[source.body]
                impact, deadline = effect.start, effect.end
                self.assertEqual(impact, run.frame)
                self.assertEqual((deadline - impact) * combat.TICK, 4)
                self.assertEqual(helper.health(run, source), 140)
                self.assertNotIn(ally.body, fight.healing.anti)
                ally_before = helper.health(run, ally)
                for _ in range(10):
                    run.step({})
                self.assertEqual(helper.health(run, source), 140)
                self.assertAlmostEqual(helper.health(run, ally) - ally_before,
                                       (52 if amp else 16) * 10 * combat.TICK, places=5)
                while run.frame + 1 < deadline - skipped // 2:
                    run.step({})
                self.assertEqual(helper.health(run, source), 140)
                run.frame += skipped
                run.step({})
                healed_ticks = run.frame - deadline + 1
                self.assertAlmostEqual(helper.health(run, source),
                                       140 + 16 / 1.3 * combat.TICK * healed_ticks, places=5)
                self.assertNotIn(source.body, fight.healing.anti)

    def test_allied_ana_grenade_boosts_crossfade_and_amp_until_expiry(self):
        for amp in (False, True):
            with self.subTest(amp=amp):
                helper, run, source, ana, ally = self.scenario()
                run.match.switch_hero(ana, heroes()[0x02E000000000013B])
                ana.team = source.team
                ana.mover.position[:] = (0, 1, 4)
                ally.mover.position[:] = (6, 1, 0)
                ally.mover.body.teleport((6, 1 + ally.mover._lift(), 0))
                run.match.collision_world = World([
                    (-20, 1, -20, -20, 1, 20, 20, 1, 20), (-20, 1, -20, 20, 1, 20, 20, 1, -20),
                    (-4, 1, -1, 4, 1, -1, 4, 6, -1), (-4, 1, -1, 4, 6, -1, -4, 6, -1),
                ])
                if amp:
                    run.step({source: (0, 0, 16)})
                    for _ in range(10):
                        run.step({})
                self.wound(run, source, 130)
                fight = run.match.combat
                direction = aim(fight._eye(ana), (0, 1.95, -1))
                command = {ana: (*direction, 16)}
                for _ in range(40):
                    before = helper.health(run, source)
                    run.step(command)
                    command = {}
                    if source.body in fight.healing.amp:
                        break
                self.assertIn(source.body, fight.healing.amp)
                effect = fight.healing.amp[source.body]
                impact, deadline = effect.start, effect.end
                self.assertEqual((impact, deadline), (run.frame + 4, run.frame + 250))
                self.assertNotIn(ally.body, fight.healing.amp)
                self.assertNotIn(source.body, fight.healing.anti)
                rate = 40 if amp else 16 / 1.3
                self.assertAlmostEqual(helper.health(run, source) - before,
                                       100 + rate * combat.TICK, places=5)
                before = helper.health(run, source)
                for _ in range(10):
                    run.step({})
                self.assertAlmostEqual(helper.health(run, source) - before,
                                       rate * (3 + 7 * 1.5) * combat.TICK, places=5)
                while run.frame + 1 < deadline:
                    run.step({})
                self.wound(run, source, 30)
                before = helper.health(run, source)
                run.step({})
                self.assertAlmostEqual(helper.health(run, source) - before, 16 / 1.3 * combat.TICK, places=5)
                self.assertNotIn(source.body, fight.healing.amp)

    def test_future_ana_grenade_launch_and_retained_impact_use_their_own_frames(self):
        helper, run, source, ana, ally = self.scenario(healing=False)
        run.match.switch_hero(ana, heroes()[0x02E000000000013B])
        ana.mover.position[:] = (0, 1, 4)
        ally.mover.position[:] = (6, 1, 0)
        fight = run.match.combat
        launch = run.frame + 6
        yaw, pitch = aim(fight._eye(ana), (0, 1.95, 0))
        ana.take_commands([Command(launch, yaw=yaw, pitch=pitch, buttons=16)])
        rocket, = fight.rockets
        origin = rocket.position
        self.assertEqual(rocket.last_tick, launch)
        run.match.update(launch * combat.TICK, launch)
        self.assertEqual(rocket.position, origin)
        self.assertNotIn(source.body, fight.healing.anti)

        endpoint = launch + 16
        run.match.update(endpoint * combat.TICK, endpoint)
        effect = fight.healing.anti[source.body]
        impact, expiry = effect.start, effect.end
        self.assertGreater(impact, launch)
        self.assertLess(impact, endpoint)
        self.assertEqual(expiry, impact + 250)
        self.assertEqual(helper.health(run, source), 140)
        self.assertFalse(fight.rockets)

    def test_ana_grenade_reapplication_keeps_prior_boost_samples(self):
        def replay(gap=None):
            helper, run, source, ana, ally = self.scenario()
            run.match.switch_hero(ana, heroes()[0x02E000000000013B])
            ana.team = source.team
            ana.mover.position[:] = (0, 1, 4)
            ally.mover.position[:] = (6, 1, 0)
            run.match.collision_world = World([
                (-20, 1, -20, -20, 1, 20, 20, 1, 20), (-20, 1, -20, 20, 1, 20, 20, 1, -20),
                (-4, 1, -1, 4, 1, -1, 4, 6, -1), (-4, 1, -1, 4, 6, -1, -4, 6, -1),
            ])
            source.body_script.ult_seconds = 0
            self.wound(run, source, 130)
            fight = run.match.combat
            direction = aim(fight._eye(ana), (0, 1.95, -1))
            run.step({ana: (*direction, 16)})

            def world(frames):
                run.frame += frames
                run.match.update(run.frame * combat.TICK, run.frame)

            world(1)
            start = run.frame
            deadline = start + 5
            fight.healing.spec = healing.Spec.load()
            fight.healing.amp[source.body] = healing.Modifier(ana.body, start, deadline)
            previous = helper.health(run, source)
            meter = source.body_script.component.entity_var(ULT_CHARGE)
            previous_charge = meter.value()
            if gap is None:
                for _ in range(20):
                    world(1)
                    if (effect := fight.healing.amp.get(source.body)) is not None and effect.end > deadline:
                        break
            else:
                world(gap)

            effect = fight.healing.amp[source.body]
            impact = run.frame
            self.assertGreater(impact, deadline)
            self.assertEqual((effect.start, effect.end), (impact + 4, impact + 250))
            elapsed = impact - start
            # Four boosted frames from the previous grenade. The replacement
            # multiplier starts after the authored delay.
            expected = (elapsed + 2) * (16 / 1.3) * combat.TICK
            self.assertAlmostEqual(helper.health(run, source) - previous - 100, expected, places=5)
            self.assertAlmostEqual(meter.value() - previous_charge, expected, places=5)
            return elapsed

        replay(replay())

    def test_ana_grenade_denial_precedes_boost_and_both_retire_with_body(self):
        def throw(run, ana, point, windows, body):
            direction = aim(run.match.combat._eye(ana), point)
            run.step({ana: (*direction, 16)})
            for _ in range(40):
                if body in windows:
                    return
                run.step({})
            self.fail("Grenade did not apply its effect")

        for end in ("expiry", "hero_switch", "death"):
            with self.subTest(end=end):
                helper, run, source, enemy, ally = self.scenario()
                ana = heroes()[0x02E000000000013B]
                run.match.switch_hero(enemy, ana)
                run.match.switch_hero(ally, ana)
                enemy.mover.position[:] = (-2, 1, 4)
                ally.mover.position[:] = (2, 1, 4)
                run.match.collision_world = World([
                    (-20, 1, -20, -20, 1, 20, 20, 1, 20), (-20, 1, -20, 20, 1, 20, 20, 1, -20),
                    (-4, 1, -1, 4, 1, -1, 4, 6, -1), (-4, 1, -1, 4, 6, -1, -4, 6, -1),
                ])
                fight = run.match.combat
                body = source.body
                throw(run, enemy, (0, 1.95, 0), fight.healing.anti, body)
                self.assertEqual(helper.health(run, source), 140)
                for _ in range(12):
                    run.step({})
                throw(run, ally, (0, 1.95, -1), fight.healing.amp, body)
                self.assertEqual(helper.health(run, source), 140)
                denial_end = fight.healing.anti[body].end
                boost_end = fight.healing.amp[body].end
                self.assertGreater(boost_end, denial_end)

                if end == "death":
                    direction = aim(fight._eye(enemy), (0, 1.95, 0))
                    for _ in range(180):
                        run.step({enemy: (*direction, FIRE)})
                        if fight.is_dead(source):
                            break
                    self.assertTrue(fight.is_dead(source))
                    self.assertLess(run.frame, denial_end)
                    self.assertEqual(source.body, body)
                    self.assertNotIn(body, fight.healing.anti)
                    self.assertNotIn(body, fight.healing.amp)
                    deadline = next(when for when, actor in fight.respawns if actor is source)
                    run.frame = round(deadline / combat.TICK) + 1
                    run.match.update(run.frame * combat.TICK, run.frame)
                    self.assertFalse(fight.is_dead(source))
                    self.assertEqual(source.hero.guid, fixtures.LUCIO)
                    self.assertEqual(helper.health(run, source), 200)
                elif end == "hero_switch":
                    run.match.switch_hero(source, heroes()[0x02E0000000000002])

                if end != "expiry":
                    self.assertNotEqual(source.body, body)
                    for windows in (fight.healing.anti, fight.healing.amp):
                        self.assertNotIn(body, windows)
                        self.assertNotIn(source.body, windows)
                    continue

                while run.frame + 1 < denial_end:
                    run.step({})
                self.assertEqual(helper.health(run, source), 140)
                run.step({})
                rate = 16 / 1.3 * combat.TICK
                self.assertAlmostEqual(helper.health(run, source), 140 + rate * 1.5, places=5)
                self.assertNotIn(body, fight.healing.anti)
                while run.frame + 1 < boost_end:
                    run.step({})
                before = helper.health(run, source)
                run.step({})
                self.assertAlmostEqual(helper.health(run, source) - before, rate, places=5)
                self.assertNotIn(body, fight.healing.amp)

    def test_speed_song_does_not_heal_and_switching_stops_healing(self):
        helper, run, source, _, ally = self.scenario(healing=False)
        self.wound(run, ally)
        for _ in range(64):
            run.step({})
        self.assertEqual(helper.health(run, ally), 100)
        run.step({source: (0, 0, 8)})
        for _ in range(64):
            run.step({})
        self.assertGreater(helper.health(run, ally), 100)
        run.step({source: (0, 0, 8)})
        for _ in range(30):
            run.step({})
        after = helper.health(run, ally)
        for _ in range(64):
            run.step({})
        self.assertEqual(helper.health(run, ally), after)

    def test_range_and_occlusion_remove_and_recreate_live_status(self):
        helper, run, _source, _, ally = self.scenario()
        self.wound(run, ally)
        ally.mover.position[:] = (100, 1, 0)
        ally.mover.body.teleport((100, 1 + ally.mover._lift(), 0))
        for _ in range(64):
            run.step({})
        self.assertEqual(helper.health(run, ally), 100)
        ally.mover.position[:] = (0, 1, 4)
        ally.mover.body.teleport((0, 1 + ally.mover._lift(), 4))
        with patch("ow174.game.combat.capsule_visible", return_value=False):
            for _ in range(64):
                run.step({})
        self.assertEqual(helper.health(run, ally), 100)
        for _ in range(64):
            run.step({})
        self.assertGreater(helper.health(run, ally), 100)

    def test_ffa_only_self_and_no_resurrection_of_dead_ally(self):
        helper, run, source, enemy, ally = self.scenario()
        run.match.game_map = replace(run.match.game_map, free_for_all=True)
        for owner in (source, ally):
            self.wound(run, owner)
        for _ in range(64):
            run.step({})
        self.assertEqual(helper.health(run, ally), 100)
        self.assertGreater(helper.health(run, source), 100)
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is ally)
        fight._damage(enemy, target, 500, False, None, run.frame)
        for _ in range(20):
            run.step({})
        self.assertTrue(fight.is_dead(ally))
        self.assertEqual(helper.health(run, ally), 0)

    def test_source_death_and_body_retirement_remove_aura(self):
        helper, run, source, enemy, ally = self.scenario()
        self.wound(run, ally)
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is source)
        fight._damage(enemy, target, 500, False, None, run.frame)
        for _ in range(20):
            run.step({})
        self.assertEqual(helper.health(run, ally), 100)
        self.assertFalse(fight.lucio_auras.leases)
        fight.retire_player_body(source)
        self.assertFalse(fight.lucio_auras.leases)

    def test_amp_uses_authored_fifty_two_ally_and_forty_self_rates(self):
        helper, run, source, _, ally = self.scenario()
        run.step({source: (0, 0, 16)})
        for _ in range(20):
            run.step({})
        self.wound(run, source)
        self.wound(run, ally)
        for _ in range(64):
            run.step({})
        self.assertAlmostEqual(helper.health(run, ally), 100 + 52 * 64 * combat.TICK, places=5)
        self.assertAlmostEqual(helper.health(run, source), 100 + 40 * 64 * combat.TICK, places=5)

    def test_amp_switch_world_gap_preserves_both_healing_windows(self):
        def exercise(schedule):
            batch = schedule != "contiguous"
            helper, run, source, enemy, ally = self.scenario(healing=False)
            source.body_script.ult_seconds = 0
            meter = source.body_script.component.entity_var(ULT_CHARGE)

            def advance(count, buttons=0, *, settle_each=True, sparse=False):
                if sparse:
                    run.frame += count - 1
                    count = 1
                for _ in range(count):
                    run.frame += 1
                    for player in run.players:
                        command = Command(run.frame, buttons=buttons if player is source else 0)
                        recipient = ally.body_script.component
                        recipient_clock = (recipient.frame, recipient.now)
                        player.take_commands([command])
                        if sparse and player is source:
                            self.assertEqual((recipient.frame, recipient.now), recipient_clock)
                    if settle_each:
                        run.match.update(run.frame * combat.TICK, run.frame)
                if not settle_each:
                    run.match.update(run.frame * combat.TICK, run.frame)

            def sample():
                return helper.health(run, source), helper.health(run, ally), meter.value()

            advance(1, 16)
            cast_frame = run.frame
            advance(32)
            for player in (source, enemy, ally):
                self.wound(run, player)

            for boosted_frames, message in ((17, "speed to heal"), (11, "heal to speed")):
                advance(1, 8)
                previous = sample()
                advance(28, settle_each=not batch)
                expected = (40, 52, 92)
                with self.subTest(transition=message):
                    for value, start, rate in zip(sample(), previous, expected, strict=True):
                        self.assertAlmostEqual(
                            value - start, rate * boosted_frames * combat.TICK, places=4,
                        )
            advance(1, 8)
            advance(cast_frame + 165 - run.frame)
            self.wound(run, source, amount=80)
            self.wound(run, ally, amount=80)
            previous = sample()
            advance(70, settle_each=not batch, sparse=schedule == "sparse")
            with self.subTest(transition="Amp expiry"):
                normal_self = 16 / 1.3
                expected = (40 * 34 + normal_self * 36, 52 * 34 + 16 * 36,
                            92 * 34 + (normal_self + 16) * 36)
                # Charge is accumulated in float32 for each submitted heal.
                tolerances = (0.00001, 0.00001, 0.0002)
                results = zip(sample(), previous, expected, tolerances, strict=True)
                for value, start, rate_ticks, tolerance in results:
                    self.assertAlmostEqual(value - start, rate_ticks * combat.TICK, delta=tolerance)
                marker = source.body_script.component.entity_var(2381)
                self.assertFalse(marker.value())
            self.assertEqual(helper.health(run, enemy), 100)

        for schedule in ("contiguous", "batched", "sparse"):
            with self.subTest(schedule=schedule):
                exercise(schedule)

    def test_amp_ends_and_represses_cannot_bypass_cooldown(self):
        _, run, source, _, _ = self.scenario()
        transitions, previous = [], False
        for offset in range(963):
            run.step({source: (0, 0, 16 if offset % 2 == 0 else 0)})
            var = source.body_script.component.entity_var(2381, create=False)
            active = bool(var.value()) if var is not None else False
            if active != previous:
                transitions.append((offset, active))
            previous = active
        # Includes authored cast time, three-second Amp lifetime, end-started
        # twelve-second cooldown and the next cast. Every other tick presses.
        self.assertEqual(transitions, [(12, True), (200, False), (962, True)])

    def test_two_heal_sources_use_authored_nonstacking_rate(self):
        helper = fixtures.LucioCombatTests()
        run, source, second, ally = helper.scenario()
        run.match.switch_hero(second, heroes()[fixtures.LUCIO])
        second.team = 0
        second.mover.position[:] = (2, 1, 0)
        run.step({source: (0, 0, 8), second: (0, 0, 8)})
        for _ in range(30):
            run.step({})
        self.wound(run, ally)
        leases = [lease for (_, recipient), lease in run.match.combat.lucio_auras.leases.items()
                  if recipient == ally.body]
        self.assertEqual(len(leases), 2)
        for _ in range(64):
            run.step({})
        self.assertAlmostEqual(helper.health(run, ally), 100 + 16 * 64 * combat.TICK, places=5)

    def test_overlapping_amps_transfer_healing_credit_during_sparse_input(self):
        def exercise(first_index, reverse_arrival):
            helper = fixtures.LucioCombatTests()
            run, left, right, ally = helper.scenario()
            run.match.switch_hero(right, heroes()[fixtures.LUCIO])
            right.team = left.team
            right.mover.position[:] = (2, 1, 0)
            right.mover.body = None
            sources = (left, right)
            first, second = sources[first_index], sources[1 - first_index]
            for source in sources:
                source.body_script.ult_seconds = 0

            run.step({left: (0, 0, 8), right: (0, 0, 8)})
            for _ in range(40):
                run.step({})
            run.step({first: (0, 0, 16)})
            for offset in range(1, 166):
                run.step({second: (0, 0, 16)} if offset == 60 else {})
            self.wound(run, ally, amount=90)
            before = helper.health(run, ally)
            meters = [source.body_script.component.entity_var(ULT_CHARGE) for source in (first, second)]
            charge = [meter.value() for meter in meters]
            receiver = ally.body_script.component
            receiver_clock = receiver.frame, receiver.now

            run.frame += 70
            order = reversed(sources) if reverse_arrival else sources
            for source in order:
                source.take_commands([Command(run.frame)])
                self.assertEqual((receiver.frame, receiver.now), receiver_clock)
            ally.take_commands([Command(run.frame)])
            run.match.update(run.frame * combat.TICK, run.frame)

            self.assertAlmostEqual(helper.health(run, ally) - before, 52 * 70 * combat.TICK, places=5)
            # Both contribute 26/s while both Amps are active. After the
            # first expiry, its share falls to 8/s and the other rises to 44/s.
            rate_ticks = (26 * 34 + 8 * 36, 26 * 34 + 44 * 36)
            for meter, start, expected in zip(meters, charge, rate_ticks, strict=True):
                self.assertAlmostEqual(meter.value() - start, expected * combat.TICK, delta=0.0002)

        for first_index in (0, 1):
            for reverse_arrival in (False, True):
                with self.subTest(first=first_index, reverse_arrival=reverse_arrival):
                    exercise(first_index, reverse_arrival)

    def test_actual_npc_script_reads_source_song_and_receives_ally_healing(self):
        helper, run, _source, _, _ = self.scenario()
        bot = bots.make(run.match.game_map.map_guid)[0]
        if graph.entity_body(bot.body) is None:
            self.skipTest("requires an external training NPC body graph")
        bot.team, bot.position, bot.route = 0, (0, 1, 4), []
        run.match._prepare_bot_script(bot)
        self.assertIsNotNone(bot.body_script)
        run.match.bots.append(bot)
        self.wound(run, bot)
        before = helper.health(run, bot)
        run.step({})  # enter aura; no retroactive heal before creation
        for _ in range(64):
            run.step({})
        self.assertAlmostEqual(helper.health(run, bot), before + 16 * 64 * combat.TICK, places=5)
