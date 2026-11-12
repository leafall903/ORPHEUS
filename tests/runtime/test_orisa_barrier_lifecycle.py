"""Actual barrier inputs/lifetimes; native owner-persistence policies stay open."""

import os
import unittest

import test_orisa_barrier as fixture
import test_orisa_combat as combat_fixture
from test_game_combat import aim

from ow174.game.commands import FIRE
from ow174.game.content import find_hero


@unittest.skipUnless(os.environ.get("ORPHEUS_ORISA_GEOMETRY"), "actual extracted mesh required")
class OrisaBarrierLifecycleTests(unittest.TestCase):
    def test_early_refusal_and_fresh_cooldown_redeployment_replace_only_on_new_landing(self):
        run, player, enemy, _, old = fixture.BarrierTests().deploy()
        manager = run.match.combat.orisa.barriers
        weapon = next(i for i in player.body_script.component.instances.values() if i.graph.index == 0xBD7)
        deadline = old.until
        run.step({enemy: (32768, 0, FIRE)})
        self.assertEqual(old.health, 580)
        self.assertGreater(weapon.vars[61].value(), 0)
        run.step({player: (0, 0, 16)})
        run.step({})
        for _ in range(1000):
            run.step({})
            if weapon.vars[61].value() <= 0:
                break
        self.assertLessEqual(weapon.vars[61].value(), 0)
        # Refused input does not replay after the authored10-second cooldown.
        for _ in range(20):
            run.step({})
        self.assertEqual(tuple(manager.active), (old.entity,))
        self.assertEqual(old.until, deadline)
        self.assertEqual(old.health, 580)
        run.step({player: (0, 0, 16)})
        self.assertIn(old.entity, manager.active)
        for _ in range(150):
            run.step({})
            if old.entity not in manager.active:
                break
        self.assertNotIn(old.entity, manager.active)
        self.assertEqual(len(manager.active), 1)
        new = next(iter(manager.active.values()))
        self.assertGreater(new.entity, old.entity)
        self.assertEqual(new.source_body, player.body)
        self.assertEqual(new.health, 600)
        self.assertGreater(new.until, deadline)
        self.assertGreater(weapon.vars[61].value(), 0)

    def test_actual_owner_death_and_respawn_keep_deployed_identity_deadline_and_interception(self):
        run, player, enemy, _, shield = fixture.BarrierTests().deploy()
        fight = run.match.combat
        manager = fight.orisa.barriers
        source, deadline = shield.source_body, shield.until
        x, y, z = player.mover.position
        fixture.BarrierTests.place(enemy, (x, y, z - 5))
        for _ in range(1000):
            run.step({enemy: (0, 0, FIRE)})
            if fight.is_dead(player):
                break
        self.assertTrue(fight.is_dead(player))
        self.assertIs(manager.active[shield.entity], shield)
        self.assertEqual(shield.health, 600)
        for _ in range(1000):
            run.step({})
            if player.body != source:
                break
        self.assertNotEqual(player.body, source)
        self.assertIs(manager.active[shield.entity], shield)
        self.assertEqual((shield.source_body, shield.until), (source, deadline))
        x, y, z = shield.position
        fixture.BarrierTests.place(player, (x, y, z - 6))
        fixture.BarrierTests.place(enemy, (x, y, z + 5))
        run.step({})
        vitals = fight.vitals_of(combat_fixture.OrisaCombatTests().target(run, player))
        before = vitals.total
        for _ in range(200):
            run.step({enemy: (32768, 0, FIRE)})
            if shield.health < 600:
                break
        self.assertEqual(shield.health, 580)
        self.assertEqual(vitals.total, before)

    def test_hero_switch_retains_deployed_source_until_original_lifetime(self):
        run, player, enemy, _, shield = fixture.BarrierTests().deploy()
        manager = run.match.combat.orisa.barriers
        source, deadline = shield.source_body, shield.until
        run.match.switch_hero(player, find_hero("Soldier: 76"))
        self.assertNotEqual(player.body, source)
        self.assertIs(manager.active[shield.entity], shield)
        self.assertEqual((shield.source_body, shield.until), (source, deadline))
        run.step({enemy: (32768, 0, FIRE)})
        self.assertEqual(shield.health, 580)
        for _ in range(1500):
            run.step({})
            if shield.entity not in manager.active:
                break
        # Original ChaseVar uses f32 and a per-step epsilon; the configured
        # deadline is metadata, while its health-pool scope owns collision life.
        self.assertLessEqual(abs(run.frame - deadline), 1)
        self.assertEqual(shield.component.entity_var(215).value(), 0)
        self.assertFalse(shield.blocking())
        self.assertNotIn(shield.entity, manager.active)

    def test_deployed_barrier_keeps_intercepting_after_owner_leaves(self):
        run, owner, enemy, _, shield = fixture.BarrierTests().deploy()
        manager = run.match.combat.orisa.barriers
        body, deadline = shield.source_body, shield.until
        run.match.leave(owner)
        run.players.remove(owner)
        run.step({enemy: (32768, 0, FIRE)})
        self.assertTrue(owner.gone)
        self.assertIs(manager.active[shield.entity], shield)
        self.assertEqual((shield.source_body, shield.until), (body, deadline))
        self.assertEqual(shield.health, 580)
        while run.frame < deadline + 1 and shield.entity in manager.active:
            run.step({})
        self.assertNotIn(shield.entity, manager.active)
        self.assertFalse(shield.blocking())
        self.assertLessEqual(abs(run.frame - deadline), 1)

    def test_actual_inflight_or_pending_cast_is_retired_on_switch_or_leave(self):
        for phase in ("flight", "pending"):
            for retirement in ("switch", "leave"):
                with self.subTest(phase=phase, retirement=retirement):
                    run, player, _ = combat_fixture.OrisaCombatTests().arena()
                    fight = run.match.combat
                    manager = fight.orisa.barriers
                    source = player.body
                    run.step({player: (0, 0, 16)})
                    rocket = None
                    for _ in range(150):
                        rocket = next((r for r in fight.rockets if r.source_graph == 0xBD7), rocket)
                        if phase == "flight" and rocket is not None:
                            break
                        if phase == "pending" and manager.pending:
                            rocket = manager.pending[0][1]
                            break
                        run.step({})
                    self.assertIsNotNone(rocket)
                    self.assertEqual(rocket.source_body, source)
                    self.assertEqual(rocket.spawn, 0x04000000000014A1)
                    self.assertFalse(manager.active)
                    if phase == "pending":
                        self.assertTrue(manager.pending)
                        self.assertEqual(rocket.ended, "world")
                    else:
                        self.assertIn(rocket, fight.rockets)
                        self.assertIsNone(rocket.ended)
                    if retirement == "switch":
                        run.match.switch_hero(player, find_hero("Soldier: 76"))
                    else:
                        run.match.leave(player)
                        run.players.remove(player)  # no more commands from the departed fake client
                    for _ in range(150):
                        run.step({})
                    self.assertFalse(manager.pending)
                    self.assertFalse(manager.active)
                    self.assertFalse(any(r.source_body == source for r in fight.rockets))

    def test_actual_death_before_launch_cancels_but_emitted_projectile_still_lands(self):
        for delay in (0, 4):
            with self.subTest(delay_before_cast=delay):
                run, player, enemy = combat_fixture.OrisaCombatTests().arena()
                fight = run.match.combat
                source = player.body
                vitals = fight.vitals_of(combat_fixture.OrisaCombatTests().target(run, player))
                yaw, pitch = aim(fight._eye(enemy), fight._eye(player))
                # Earn the low-health boundary through real enemy weapon fire.
                for _ in range(1000):
                    run.step({enemy: (yaw, pitch, FIRE)})
                    if 0 < vitals.total <= 40 or vitals.dead:
                        break
                self.assertGreater(vitals.total, 0)
                self.assertLessEqual(vitals.total, 40)
                for _ in range(delay):
                    run.step({})
                run.step({player: (0, 0, 16)})
                emitted = {}
                death_tick = None
                for _ in range(120):
                    run.step({enemy: (yaw, pitch, FIRE)})
                    if vitals.dead and death_tick is None:
                        death_tick = run.frame
                    for rocket in fight.rockets:
                        if rocket.source_graph == 0xBD7:
                            emitted.setdefault(id(rocket), (run.frame, rocket))
                self.assertIsNotNone(death_tick)
                self.assertEqual(player.body, source)
                self.assertTrue(fight.is_dead(player))
                if delay:
                    self.assertFalse(emitted)
                    self.assertFalse(fight.orisa.barriers.active)
                else:
                    self.assertEqual(len(emitted), 1)
                    tick, rocket = next(iter(emitted.values()))
                    self.assertLess(tick, death_tick)
                    self.assertEqual(rocket.ended, "world")
                    self.assertEqual(len(fight.orisa.barriers.active), 1)
                    shield = next(iter(fight.orisa.barriers.active.values()))
                    self.assertEqual(shield.source_body, source)
                    self.assertEqual(shield.health, 600)
