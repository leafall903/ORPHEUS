"""Original retained owner replacement on the actual Sentry/body/ID services."""
import unittest
from unittest.mock import patch
from test_symmetra_combat import arena, health
from test_symmetra_turrets import wall
from ow174.game import combat, stats


class RetainedRespawnTests(unittest.TestCase):
    def scene(self):
        run, owner, enemy = arena()
        enemy.mover.position[:] = (2, 1, 1)
        owner.body_script.component.set(12918, 0)
        fight = run.match.combat
        with patch.object(fight, '_collision', return_value=wall()):
            for _ in range(40):
                run.step({owner: (0, 0, 8)})
            for _ in range(50):
                run.step({})
        self.assertEqual(len(fight.sentries.sentries), 1)
        sentry = fight.sentries.sentries[0]
        self.assertIsNotNone(sentry.slow)
        target = next(t for t in fight._targets() if t.owner is owner)
        fight._damage(enemy, target, 10000, False, 0, run.frame)
        self.assertTrue(fight.is_dead(owner))
        return run, owner, enemy, sentry

    def outcome(self, gap, tail, failures=0):
        run, owner, enemy, sentry = self.scene()
        fight = run.match.combat
        old_body, old_script, aura = owner.body, owner.body_script, sentry.slow[1]
        # Exercise the real pending replacement at a nearby known world boundary.
        due = run.frame + 3
        fight.respawns[:] = [(due * combat.TICK, owner)]
        endpoint = due + tail + failures * round(combat.RESPAWN_RETRY / combat.TICK)
        attempts = []
        allocate = run.match._next_body_id
        def next_id(player, *, at=None):
            attempts.append((fight.tick, at))
            if len(attempts) <= failures:
                return None
            return allocate(player, at=at)
        before = health(run, enemy).total
        with patch.object(fight, '_collision', return_value=wall()), \
             patch.object(run.match, '_next_body_id', side_effect=next_id):
            for tick in ([endpoint] if gap else range(run.frame + 1, endpoint + 1)):
                run.match.update(tick * combat.TICK, tick)
        self.assertNotEqual(owner.body, old_body)
        self.assertTrue(old_script.retired)
        self.assertIs(sentry.owner, owner)
        self.assertIs(sentry.slow[1], aura)
        self.assertFalse(any(who is owner for _, who in fight.respawns))
        if gap:
            self.assertTrue(all(at is not None for _, at in attempts))
            for (previous_tick, previous_at), (next_tick, next_at) in zip(attempts, attempts[1:]):
                self.assertAlmostEqual(next_at - previous_at, (next_tick - previous_tick) * combat.TICK)
        return ([tick for tick, _ in attempts], fight.replacement_ticks[owner.body],
                owner.body_script.component.entity_var(342).value(),
                before - health(run, enemy).total, owner.stats.value(stats.HERO_DAMAGE))

    def test_actual_sentry_owner_replaces_at_retained_deadline(self):
        self.assertEqual(self.outcome(True, 7), self.outcome(False, 7))

    def test_prewindow_replacement_and_failed_allocations_keep_source_boundaries(self):
        # Original source integrates attacks only inside the retained window;
        # owner replacement and its passive clock still occur at earlier deadlines.
        tail = combat.COMBAT_CATCH_UP + 3
        retained = self.outcome(True, tail, 2)
        contiguous = self.outcome(False, tail, 2)
        self.assertEqual(retained[:2], contiguous[:2])
        self.assertEqual(retained[0], [1093, 1109, 1125])
        self.assertAlmostEqual(contiguous[2] - retained[2], 3 * .64, places=4)
        self.assertAlmostEqual(retained[3], combat.COMBAT_CATCH_UP * .64, places=4)

    def test_historical_peer_reservation_retains_reliable_guard(self):
        from types import SimpleNamespace
        from ow174.game.server import Client
        peer = Client.__new__(Client)
        peer._player_body_reuse_after = {123: 20.0, 124: None}
        peer._combat_notice_tail = {123: 7}
        peer._oldest_pending_reliable = lambda: 8
        peer.server = SimpleNamespace(environment=SimpleNamespace(
            monotonic=lambda **kw: self.fail('historical allocation read live time')))
        self.assertFalse(peer.body_id_available(123, at=19.999))
        self.assertTrue(peer.body_id_available(123, at=20.0))
        self.assertFalse(peer.body_id_available(124, at=999))
        peer._oldest_pending_reliable = lambda: 7
        self.assertFalse(peer.body_id_available(123, at=999))


class ProjectileAndSphereRespawnTests(unittest.TestCase):
    def test_omitted_actual_orb_death_replaces_before_retained_window(self):
        for split in (False, True):
            from test_game_combat import aim
            run, owner, enemy = arena()
            fight = run.match.combat
            enemy.mover.position[:] = (0, 1, 12)
            angles = aim(fight._eye(owner), (0, 2, 12))
            for _ in range(90):
                run.step({owner: (*angles, 2)})
            pool = health(run, enemy)
            pool.now[combat.HEALTH_POOL] = 1
            pool.now[combat.SHIELDS_POOL] = 0
            run.step({owner: (*angles, 0)})
            run.step({})
            self.assertEqual(len(fight.rockets), 1)
            old_body, old_script = enemy.body, enemy.body_script
            endpoint = run.frame + 800
            if split:
                checkpoint = run.frame + 100
                run.match.update(checkpoint * combat.TICK, checkpoint)
                self.assertTrue(pool.dead)
                self.assertEqual(enemy.body, old_body)
                self.assertIn(old_body, fight.early_projectile_respawns)
            run.match.update(endpoint * combat.TICK, endpoint)
            self.assertTrue(pool.dead)
            expected = combat._tick_at_or_after(endpoint * combat.TICK, endpoint,
                                                 pool.died_at + combat.respawn_seconds(run.match))
            self.assertLess(expected, endpoint - combat.COMBAT_CATCH_UP)
            self.assertNotEqual(enemy.body, old_body)
            self.assertTrue(old_script.retired)
            self.assertEqual(fight.replacement_ticks[enemy.body], expected)
            self.assertNotIn(old_body, fight.early_projectile_respawns)
            self.assertFalse(any(who is enemy for _, who in fight.respawns))

    def test_actual_native_sphere_ends_after_retained_owner_replacement_tick(self):
        from test_delivered_winston_barrier import BarrierTests
        fixture = BarrierTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        bubble = fixture.cast()
        run, fight, owner = fixture.run, fixture.fight, fixture.owner
        target = next(t for t in fight._targets() if t.owner is owner)
        fight._damage(fixture.enemy, target, 10000, False, 0, run.frame)
        self.assertTrue(fight.is_dead(owner))
        due = run.frame + 3
        fight.respawns[:] = [(due * combat.TICK, owner)]
        old_body = owner.body
        observed = []
        update = fight.sentries.update
        def sentries(*args, **kwargs):
            observed.extend(args[2][1][1])
            return update(*args, **kwargs)
        with patch.object(fight.sentries, 'update', side_effect=sentries):
            endpoint = due + 7
            run.match.update(endpoint * combat.TICK, endpoint)
        self.assertNotEqual(owner.body, old_body)
        self.assertEqual(fight.replacement_ticks[owner.body], due)
        self.assertTrue(bubble.retired)
        self.assertNotIn(bubble.entity, fight.barriers.world.actors)
        self.assertTrue(any(actor is bubble for actor, _ in observed))
        for actor, ended in observed:
            if actor is bubble:
                self.assertAlmostEqual(ended, (due + 1) * combat.TICK)

    def test_early_projectile_tracking_changes_raw_server_causal_state(self):
        from test_server_snapshot import ServerSnapshotTests, snapshot_digest
        fixture = ServerSnapshotTests()
        session, _, players = fixture.scene()
        fight = players[0].match.combat
        before = snapshot_digest(fixture.capture(session))
        fight.early_projectile_respawns.add(players[0].body)
        self.assertNotEqual(snapshot_digest(fixture.capture(session)), before)
        fight.early_projectile_respawns.clear()
        self.assertEqual(snapshot_digest(fixture.capture(session)), before)
