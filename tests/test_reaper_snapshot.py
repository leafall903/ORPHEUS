"""Reaper effect clocks are causal server state, not hidden module storage."""

import unittest
from dataclasses import replace
from unittest.mock import patch

import test_server_snapshot as snapshot_tests
from test_game_combat import aim
from test_server_journal import pick

from ow174.game import (
    ana,
    barriers,
    defenses,
    echo_duplicate,
    npc,
    npc_control,
    reaper,
    sigma_flux,
    sigma_grasp,
    winston_jump,
    winston_primal,
)
from ow174.game.collision import flat_world
from ow174.game.commands import Command
from ow174.game.content import SOLDIER
from ow174.game.native_replay import snapshot_hash as body_trace_hash
from ow174.game.script import runtime
from ow174.game.script.driver import ULT_COST
from ow174.game.server_snapshot import snapshot_digest


class ReaperSnapshotTests(unittest.TestCase):
    def test_actual_echo_copied_helix_source_body_is_passively_causal(self):
        fixture = snapshot_tests.ServerSnapshotTests()
        session, peers, players = fixture.scene(two=True)
        echo, soldier = players
        peers[0].message(21616, pick(echo.entity, echo_duplicate.HERO), now=100.8)
        peers[1].message(21616, pick(soldier.entity, SOLDIER), now=100.8)
        match = echo.match
        match.collision_world = flat_world(1)
        echo.mover.position[:] = (0, 1, 0)
        soldier.mover.position[:] = (0, 1, 3)
        echo.mover.body = soldier.mover.body = None
        fight = match.combat
        match.update(fight.now + 0.016, match.tick + 1, frames=False)
        script = echo.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        echo.take_commands([Command(max(script.frame, match.tick) + 1, buttons=32)])
        self.assertEqual(echo.hero.guid, SOLDIER)
        frame = echo.body_script.frame
        echo.take_commands([Command(frame + 1, yaw=16384, buttons=2)])
        rocket, = fight.rockets
        self.assertEqual(rocket.duplicate_body, echo.body)
        with patch.object(runtime.Var, "value", side_effect=AssertionError("no evaluation")):
            before = snapshot_digest(fixture.capture(session))
            rocket.duplicate_body = None
            changed = snapshot_digest(fixture.capture(session))
        rocket.duplicate_body = echo.body
        self.assertNotEqual(before, changed)

    def test_actual_echo_reaper_copy_deadline_is_passively_causal(self):
        fixture = snapshot_tests.ServerSnapshotTests()
        session, peers, players = fixture.scene(two=True)
        echo, target = players
        peers[0].message(21616, pick(echo.entity, echo_duplicate.HERO), now=100.8)
        peers[1].message(21616, pick(target.entity, reaper.HERO), now=100.8)
        match = echo.match
        match.collision_world = flat_world(1)
        echo.mover.position[:] = (0, 1, 0)
        target.mover.position[:] = (0, 1, 2)
        echo.mover.body = target.mover.body = None
        fight = match.combat
        match.update(fight.now + 0.016, match.tick + 1, frames=False)
        script = echo.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        echo.take_commands([Command(max(script.frame, match.tick) + 1, buttons=32)])
        copy = fight.echo_duplicates.copies[echo.entity]
        self.assertEqual(echo.hero.guid, reaper.HERO)
        with patch.object(runtime.Var, "value", side_effect=AssertionError("no evaluation")):
            before = snapshot_digest(fixture.capture(session))
            copy.until += 1
            changed = snapshot_digest(fixture.capture(session))
        copy.until -= 1
        self.assertNotEqual(before, changed)

    def test_actual_winston_jump_pending_impact_is_passively_causal(self):
        fixture = snapshot_tests.ServerSnapshotTests()
        session, peers, players = fixture.scene(two=True)
        winston, _ = players
        peers[0].message(21616, pick(winston.entity, winston_primal.HERO), now=100.8)
        match = winston.match
        match.collision_world = flat_world(1)
        winston.mover.position[:] = (0, 1, 0)
        winston.mover.body = None
        script = winston.body_script
        winston.take_commands([Command(script.frame + 1, pitch=-16384, buttons=8)])
        fight = match.combat
        self.assertIn(winston.body, fight.winston_jumps)
        digests = []
        settle = winston_jump.settle

        def capture_pending(fight):
            if winston.body in fight.winston_landing_pending:
                with patch.object(runtime.Var, "value", side_effect=AssertionError("no evaluation")):
                    queued = snapshot_digest(fixture.capture(session))
                    pending = fight.winston_landing_pending.pop(winston.body)
                    absent = snapshot_digest(fixture.capture(session))
                fight.winston_landing_pending[winston.body] = pending
                digests.append((queued, absent))
            settle(fight)

        with patch.object(winston_jump, "settle", side_effect=capture_pending):
            match.update(fight.now + 190 * 0.016, match.tick + 190, frames=False)
        self.assertEqual(len(digests), 1)
        self.assertNotEqual(*digests[0])
        self.assertFalse(fight.winston_landing_pending)

    def test_actual_winston_jump_flight_is_passively_causal(self):
        fixture = snapshot_tests.ServerSnapshotTests()
        session, peers, players = fixture.scene(two=True)
        winston, _ = players
        peers[0].message(21616, pick(winston.entity, winston_primal.HERO), now=100.8)
        match = winston.match
        match.collision_world = flat_world(1)
        script = winston.body_script
        winston.take_commands([Command(script.frame + 1, buttons=8)])
        fight = match.combat
        instance, tick, frame = fight.winston_jumps[winston.body]
        with patch.object(runtime.Var, "value", side_effect=AssertionError("no evaluation")):
            before = snapshot_digest(fixture.capture(session))
            fight.winston_jumps[winston.body] = instance, tick + 1, frame
            changed = snapshot_digest(fixture.capture(session))
        fight.winston_jumps[winston.body] = instance, tick, frame
        self.assertNotEqual(before, changed)

    def test_actual_primal_bonus_is_passively_causal(self):
        fixture = snapshot_tests.ServerSnapshotTests()
        session, peers, players = fixture.scene(two=True)
        winston, _ = players
        peers[0].message(21616, pick(winston.entity, winston_primal.HERO), now=100.8)
        match = winston.match
        match.collision_world = flat_world(1)
        script = winston.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        winston.take_commands([Command(script.frame + 1, buttons=32)])
        fight = match.combat
        self.assertEqual(fight.primal_bonus[winston.body], 500)
        with patch.object(runtime.Var, "value", side_effect=AssertionError("no evaluation")):
            before = snapshot_digest(fixture.capture(session))
            bonus = fight.primal_bonus.pop(winston.body)
            absent = snapshot_digest(fixture.capture(session))
        fight.primal_bonus[winston.body] = bonus
        self.assertNotEqual(before, absent)

    def test_actual_whole_hog_impulse_is_passively_causal(self):
        fixture = snapshot_tests.ServerSnapshotTests()
        session, peers, players = fixture.scene(two=True)
        hog, target = players
        peers[0].message(21616, pick(hog.entity, 0x02E0000000000040), now=100.8)
        peers[1].message(21616, pick(target.entity, reaper.HERO), now=100.8)
        match = hog.match
        match.collision_world = flat_world(1)
        hog.mover.position[:] = (0, 1, 0)
        target.mover.position[:] = (0, 1, 2)
        hog.mover.body = target.mover.body = None
        fight = match.combat
        script = hog.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        yaw, pitch = aim(fight._eye(hog), (0, 1.9, 2))
        for frame in range(85):
            match.update(fight.now + 0.016, match.tick + 1, frames=False)
            button = 32 if frame == 0 else 0
            hog.take_commands([Command(script.frame + 1, yaw=yaw, pitch=pitch, buttons=button)])
            if target.body in fight.npc_impulses:
                break
        tick, (x, y, z) = fight.npc_impulses[target.body]
        with patch.object(runtime.Var, "value", side_effect=AssertionError("no evaluation")):
            before = snapshot_digest(fixture.capture(session))
            fight.npc_impulses[target.body] = tick, (x, y, z + 1)
            changed_force = snapshot_digest(fixture.capture(session))
            fight.npc_impulses[target.body] = tick + 1, (x, y, z)
            changed_tick = snapshot_digest(fixture.capture(session))
        fight.npc_impulses[target.body] = tick, (x, y, z)
        match.update(fight.now + 0.016, match.tick + 1, frames=False)
        landing = fight.impulse_landings[target.body]
        with patch.object(runtime.Var, "value", side_effect=AssertionError("no evaluation")):
            lifted = snapshot_digest(fixture.capture(session))
            fight.impulse_landings.pop(target.body)
            changed_landing = snapshot_digest(fixture.capture(session))
        fight.impulse_landings[target.body] = landing
        self.assertEqual(len({before, changed_force, changed_tick}), 3)
        self.assertNotEqual(lifted, changed_landing)

    def test_actual_talon_flux_movement_and_lift_clock_are_passively_causal(self):
        import os
        from pathlib import Path

        root = os.environ.get("ORPHEUS_NPC_RUNTIME_DIR")
        if not root:
            self.skipTest("set ORPHEUS_NPC_RUNTIME_DIR for actual Trooper")
        definition = npc.load_catalog(Path(root) / "data/pve_npcs_174.json")["1"]
        fixture = snapshot_tests.ServerSnapshotTests()
        session, peers, players = fixture.scene(two=True)
        sigma, other = players
        peers[0].message(21616, pick(sigma.entity, sigma_flux.HERO), now=100.8)
        match = sigma.match
        match.collision_world = flat_world(1)
        sigma.mover.position[:] = (0, 1, 0)
        other.mover.position[:] = (15, 1, 6)
        sigma.mover.body = other.mover.body = None
        sigma.team = 0
        bot = npc.spawn(match, definition, (0, 1, 6), team=1)
        fight = match.combat
        script = sigma.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        for frame in range(81):
            match.update(fight.now + 0.016, match.tick + 1, frames=False)
            yaw, pitch = aim(fight._eye(sigma), (0, 1, 6))
            button = 32 if frame == 0 else 1 if frame == 80 else 0
            sigma.take_commands([Command(script.frame + 1, yaw=yaw, pitch=pitch, buttons=button)])
        for _ in range(140):
            match.update(fight.now + 0.016, match.tick + 1, frames=False)
            if bot.position[1] > 2:
                break
        self.assertGreater(bot.position[1], 2)
        self.assertTrue(bot.navigation.forced_landing)
        effect = next(i for i in bot.body_script.component.instances.values()
                      if i.graph.index == sigma_flux.FLUX_EFFECT)
        lift = effect.states[16]
        with patch.object(runtime.Var, "value", side_effect=AssertionError("no evaluation")):
            before = snapshot_digest(fixture.capture(session))
            bot.navigation.forced_landing = False
            changed_pose = snapshot_digest(fixture.capture(session))
            bot.navigation.forced_landing = True
            lift.start += 1
            changed_clock = snapshot_digest(fixture.capture(session))
        self.assertEqual(len({before, changed_pose, changed_clock}), 3)

    def test_actual_flux_placement_and_lift_are_passively_causal(self):
        fixture = snapshot_tests.ServerSnapshotTests()
        session, peers, players = fixture.scene(two=True)
        sigma, target = players
        peers[0].message(21616, pick(sigma.entity, sigma_flux.HERO), now=100.8)
        peers[1].message(21616, pick(target.entity, reaper.HERO), now=100.8)
        match = sigma.match
        match.collision_world = flat_world(1)
        sigma.mover.position[:] = (0, 1, 0)
        target.mover.position[:] = (0, 1, 6)
        sigma.mover.body = target.mover.body = None
        fight = match.combat
        script = sigma.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        point = tuple(target.mover.position)
        for frame in range(81):
            match.update(fight.now + 0.016, match.tick + 1, frames=False)
            yaw, pitch = aim(fight._eye(sigma), point)
            button = 32 if frame == 0 else 0
            sigma.take_commands([Command(script.frame + 1, yaw=yaw, pitch=pitch, buttons=button)])
        caster = next(i for i in script.component.instances.values() if i.graph.index == sigma_flux.FLUX)
        placement = caster.states[7]
        self.assertTrue(placement.active)
        with patch.object(runtime.Var, "value", side_effect=AssertionError("no evaluation")):
            before = snapshot_digest(fixture.capture(session))
            placement.start += 1
            changed_placement = snapshot_digest(fixture.capture(session))
            placement.start -= 1
        self.assertNotEqual(before, changed_placement)
        sigma.take_commands([Command(script.frame + 1, yaw=yaw, pitch=pitch, buttons=1)])
        lift = None
        for _ in range(135):
            match.update(fight.now + 0.016, match.tick + 1, frames=False)
            effect = next((i for i in target.body_script.component.instances.values()
                           if i.graph.index == sigma_flux.FLUX_EFFECT), None)
            if effect is not None and (lift := effect.states.get(16)) is not None and lift.active:
                break
        self.assertIsNotNone(lift)
        self.assertTrue(lift.active)
        with patch.object(runtime.Var, "value", side_effect=AssertionError("no evaluation")):
            before = snapshot_digest(fixture.capture(session))
            lift.start += 1
            changed_lift = snapshot_digest(fixture.capture(session))
        self.assertNotEqual(before, changed_lift)

    def test_actual_graviton_flight_field_and_recipient_are_passively_causal(self):
        fixture = snapshot_tests.ServerSnapshotTests()
        session, peers, players = fixture.scene(two=True)
        zarya, target = players
        peers[0].message(21616, pick(zarya.entity, 0x02E0000000000068), now=100.8)
        peers[1].message(21616, pick(target.entity, reaper.HERO), now=100.8)
        match = zarya.match
        match.collision_world = flat_world(1)
        zarya.mover.position[:] = (0, 1, 0)
        target.mover.position[:] = (2, 1, 4)
        zarya.mover.body = target.mover.body = None
        fight = match.combat
        script = zarya.body_script
        script.set_ult_charge(2100)
        yaw, pitch = aim(fight._eye(zarya), (0, 1, 4))
        zarya.take_commands([Command(script.frame + 1, yaw=yaw, pitch=pitch, buttons=32)])
        rocket, = fight.rockets
        with patch.object(runtime.Var, "value", side_effect=AssertionError("no evaluation")):
            before = snapshot_digest(fixture.capture(session))
            rocket.graviton_body = None
            generic_rocket = snapshot_digest(fixture.capture(session))
            rocket.graviton_body = zarya.body
        self.assertNotEqual(before, generic_rocket)
        for _ in range(32):
            match.update(fight.now + 0.016, match.tick + 1, frames=False)
            if fight.gravitons.controls(target):
                break
        field, = fight.gravitons.fields
        self.assertTrue(fight.gravitons.controls(target))
        with patch.object(runtime.Var, "value", side_effect=AssertionError("no evaluation")):
            before = snapshot_digest(fixture.capture(session))
            field.until += 1
            changed_clock = snapshot_digest(fixture.capture(session))
            field.until -= 1
            fight.gravitons.pulled.remove(target.body)
            changed_recipient = snapshot_digest(fixture.capture(session))
        self.assertEqual(len({before, changed_clock, changed_recipient}), 3)

    def test_actual_halt_orb_and_recipient_are_passively_causal(self):
        from ow174.game import orisa_halt

        if not orisa_halt.SUPPLEMENT_SHA256:
            self.skipTest("requires the pinned Halt supplement")
        fixture = snapshot_tests.ServerSnapshotTests()
        session, peers, players = fixture.scene(two=True)
        orisa, target = players
        peers[0].message(21616, pick(orisa.entity, 0x02E000000000013E), now=100.8)
        peers[1].message(21616, pick(target.entity, reaper.HERO), now=100.8)
        match = orisa.match
        x, y, z = orisa.mover.position
        target.mover.position[:] = (x + 2, y, z + 6)
        target.mover.body = None
        fight = match.combat
        script = orisa.body_script
        for frame in range(26):
            match.update(fight.now + 0.016, match.tick + 1, frames=False)
            orisa.take_commands([Command(script.frame + 1, buttons=2 if frame in (0, 25) else 0)])
        orb, = fight.halts.orbs.values()
        pull = fight.halts.pulls[target.body]
        self.assertIsNotNone(orb.until)
        self.assertEqual(pull.script.graph.index, 0xCCE)
        with patch.object(runtime.Var, "value", side_effect=AssertionError("no evaluation")):
            before = snapshot_digest(fixture.capture(session))
            orb.until += 1
            changed_orb = snapshot_digest(fixture.capture(session))
            orb.until -= 1
            pull.until += 1
            changed_recipient = snapshot_digest(fixture.capture(session))
        self.assertEqual(len({before, changed_orb, changed_recipient}), 3)

    def test_actual_helix_launch_modifiers_are_passively_causal(self):
        fixture = snapshot_tests.ServerSnapshotTests()
        session, _, players = fixture.scene()
        shooter, = players
        script = shooter.body_script
        shooter.take_commands([Command(script.frame + 1, buttons=2)])
        rocket, = shooter.match.combat.rockets
        self.assertEqual(rocket.actor_modifiers, (1.0, 0.0))
        with patch.object(runtime.Var, "value", side_effect=AssertionError("no evaluation")):
            before = snapshot_digest(fixture.capture(session))
            rocket.actor_modifiers = (1.0, 0.5)
            after = snapshot_digest(fixture.capture(session))
        self.assertNotEqual(before, after)

    def test_actual_supercharger_status_and_pool_are_passively_causal(self):
        from ow174.game import orisa_supercharger

        if not orisa_supercharger.SUPPLEMENT_SHA256:
            self.skipTest("requires the pinned Supercharger supplement")
        fixture = snapshot_tests.ServerSnapshotTests()
        session, peers, players = fixture.scene(two=True)
        orisa, ally = players
        peers[0].message(21616, pick(orisa.entity, 0x02E000000000013E), now=100.8)
        peers[1].message(21616, pick(ally.entity, reaper.HERO), now=100.8)
        match = orisa.match
        match.game_map = replace(match.game_map, free_for_all=False)
        orisa.team = ally.team = 0
        x, y, z = orisa.mover.position
        ally.mover.position[:] = (x + 2, y, z)
        ally.mover.body = None
        fight = match.combat
        script = orisa.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        for frame in range(85):
            match.update(fight.now + 0.016, match.tick + 1, frames=False)
            orisa.take_commands([Command(script.frame + 1, buttons=32 if frame == 0 else 0)])
            if fight.superchargers.scale(ally) == 1.5:
                break
        device, = fight.superchargers.active.values()
        self.assertEqual(fight.superchargers.scale(ally), 1.5)
        self.assertEqual(device.health, 200)
        with patch.object(runtime.Var, "value", side_effect=AssertionError("no evaluation")):
            before = snapshot_digest(fixture.capture(session))
            device.health -= 6
            changed_pool = snapshot_digest(fixture.capture(session))
            device.health += 6
            device.until -= 1
            changed_clock = snapshot_digest(fixture.capture(session))
        self.assertNotEqual(before, changed_pool)
        self.assertNotEqual(before, changed_clock)
        self.assertNotEqual(changed_pool, changed_clock)

    def test_actual_baptiste_field_lifetime_and_pool_are_passively_causal(self):
        from ow174.game import baptiste_field

        fixture = snapshot_tests.ServerSnapshotTests()
        session, peers, players = fixture.scene(two=True)
        bap, target = players
        peers[0].message(21616, pick(bap.entity, baptiste_field.HERO), now=100.8)
        peers[1].message(21616, pick(target.entity, reaper.HERO), now=100.8)
        bap.team, target.team = 0, 1
        match = bap.match
        fight = match.combat
        x, y, z = bap.mover.position
        yaw, pitch = aim(fight._eye(bap), (x, y, z + 0.5))
        for frame in range(85):
            match.update(fight.now + 0.016, match.tick + 1, frames=False)
            bap.take_commands([Command(bap.body_script.frame + 1, yaw=yaw, pitch=pitch,
                                       buttons=16 if frame == 0 else 0)])
            if bap.body in fight.baptiste_fields.devices:
                break
        device = fight.baptiste_fields.devices[bap.body]
        self.assertEqual(device.health, 150)
        self.assertEqual(device.floor_fraction, 0.1)
        with patch.object(runtime.Var, "value", side_effect=AssertionError("no evaluation")):
            before = snapshot_digest(fixture.capture(session))
            device.health -= 6
            changed_pool = snapshot_digest(fixture.capture(session))
            device.health += 6
            device.component.frame += 1
            changed_clock = snapshot_digest(fixture.capture(session))
        self.assertNotEqual(before, changed_pool)
        self.assertNotEqual(before, changed_clock)
        self.assertNotEqual(changed_pool, changed_clock)

    def test_actual_sound_barrier_lease_and_child_are_passively_causal(self):
        from ow174.game import lucio_barrier
        from ow174.game.script.graph import LUCIO_STATUS_SHA256

        fixture = snapshot_tests.ServerSnapshotTests()
        session, peers, players = fixture.scene(two=True)
        lucio, target = players
        peers[0].message(21616, pick(lucio.entity, lucio_barrier.HERO), now=100.8)
        peers[1].message(21616, pick(target.entity, reaper.HERO), now=100.8)
        match = lucio.match
        match.game_map = replace(match.game_map, free_for_all=False)
        lucio.team = target.team = 0
        x, y, z = lucio.mover.position
        target.mover.position[:] = (x, y, z + 6)
        target.mover.body = None
        fight = match.combat
        script = lucio.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        for frame in range(85):
            match.update(fight.now + 0.016, match.tick + 1, frames=False)
            lucio.take_commands([Command(script.frame + 1, buttons=32 if frame == 0 else 0)])
            if target.body in fight.sound_barriers.leases:
                break
        lease = fight.sound_barriers.leases[target.body]
        if LUCIO_STATUS_SHA256:
            self.assertEqual(lease.effect.graph.index, 0x073B)
        pool = fight.vitals[target.body].temporary_shields[lucio_barrier.POOL]
        self.assertEqual(pool.now, 750)
        with patch.object(runtime.Var, "value", side_effect=AssertionError("no evaluation")):
            before = snapshot_digest(fixture.capture(session))
            lease.last_tick -= 1
            changed_clock = snapshot_digest(fixture.capture(session))
            lease.last_tick += 1
            pool.now -= 25
            changed_pool = snapshot_digest(fixture.capture(session))
        self.assertNotEqual(before, changed_clock)
        self.assertNotEqual(before, changed_pool)
        self.assertNotEqual(changed_clock, changed_pool)

    def test_actual_meteor_strike_commit_and_anchor_are_passively_causal(self):
        from ow174.game import meteor_strike, rocket_punch

        fixture = snapshot_tests.ServerSnapshotTests()
        session, peers, players = fixture.scene(two=True)
        doom, target = players
        peers[0].message(21616, pick(doom.entity, rocket_punch.HERO), now=100.8)
        peers[1].message(21616, pick(target.entity, reaper.HERO), now=100.8)
        doom.team, target.team = 0, 1
        x, y, z = doom.mover.position
        target.mover.position[:] = (x, y, z + 5)
        target.mover.body = None
        match = doom.match
        fight = match.combat
        script = doom.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        for frame in range(85):
            match.update(fight.now + 0.016, match.tick + 1, frames=False)
            doom.take_commands([Command(script.frame + 1, buttons=32 if frame == 0 else 0)])
            if meteor_strike.aiming(meteor_strike.phase_of(doom)):
                break
        self.assertTrue(meteor_strike.aiming(meteor_strike.phase_of(doom)))
        doom.take_commands([Command(script.frame + 1, buttons=1)])
        cast = fight.meteors[doom.body]
        self.assertTrue(cast.committed)
        self.assertFalse(cast.impacted)
        with patch.object(runtime.Var, "value", side_effect=AssertionError("no evaluation")):
            before = snapshot_digest(fixture.capture(session))
            landing = cast.target
            cast.target = (landing[0] + 1, landing[1], landing[2])
            changed_target = snapshot_digest(fixture.capture(session))
            cast.target = landing
            cast.committed = False
            changed_phase = snapshot_digest(fixture.capture(session))
        self.assertNotEqual(before, changed_target)
        self.assertNotEqual(before, changed_phase)
        self.assertNotEqual(changed_target, changed_phase)

    def test_actual_seismic_slam_origin_and_hit_set_are_passively_causal(self):
        from ow174.game import rocket_punch

        fixture = snapshot_tests.ServerSnapshotTests()
        session, peers, players = fixture.scene(two=True)
        doom, target = players
        peers[0].message(21616, pick(doom.entity, rocket_punch.HERO), now=100.8)
        peers[1].message(21616, pick(target.entity, reaper.HERO), now=100.8)
        doom.team, target.team = 0, 1
        x, y, z = doom.mover.position
        target.mover.position[:] = (x, y, z + 10)
        target.mover.body = None
        match = doom.match
        fight = match.combat
        yaw, pitch = aim(fight._eye(doom), (x, y + 1.2, z + 10))
        for frame in range(95):
            match.update(fight.now + 0.016, match.tick + 1, frames=False)
            doom.take_commands([Command(doom.body_script.frame + 1, yaw=yaw, pitch=pitch,
                                        buttons=16 if frame == 0 else 0)])
            cast = fight.slams.get(doom.body)
            if cast is not None and cast.wave_tick is not None:
                break
        self.assertIsNotNone(cast)
        self.assertIsNotNone(cast.origin)
        self.assertIsNotNone(cast.wave_tick)
        self.assertFalse(cast.hit)
        with patch.object(runtime.Var, "value", side_effect=AssertionError("no evaluation")):
            before = snapshot_digest(fixture.capture(session))
            cast.wave_tick -= 1
            changed_clock = snapshot_digest(fixture.capture(session))
            cast.wave_tick += 1
            cast.hit.add(target.body)
            changed_hit = snapshot_digest(fixture.capture(session))
        self.assertNotEqual(before, changed_clock)
        self.assertNotEqual(before, changed_hit)
        self.assertNotEqual(changed_clock, changed_hit)

    def test_actual_rocket_punch_wall_and_shield_clocks_are_passively_causal(self):
        from ow174.game import rocket_punch

        fixture = snapshot_tests.ServerSnapshotTests()
        session, peers, players = fixture.scene(two=True)
        doom, target = players
        peers[0].message(21616, pick(doom.entity, rocket_punch.HERO), now=100.8)
        peers[1].message(21616, pick(target.entity, reaper.HERO), now=100.8)
        doom.team, target.team = 0, 1
        x, y, z = doom.mover.position
        target.mover.position[:] = (x, y, z + 6)
        target.mover.body = None
        match = doom.match
        fight = match.combat
        for frame in range(135):
            match.update(fight.now + 0.016, match.tick + 1, frames=False)
            doom.take_commands([Command(doom.body_script.frame + 1, buttons=2 if frame < 90 else 0)])
            if target.body in fight.punch_walls:
                break
        impact = fight.punch_walls[target.body]
        self.assertEqual(impact.source_body, doom.body)
        self.assertEqual(fight.vitals[target.body].total, 150)
        clock = fight.doomfist_shields[doom.body]
        self.assertEqual(fight.vitals[doom.body].temporary_shields[rocket_punch.SHIELD_TAG].now, 30)
        with patch.object(runtime.Var, "value", side_effect=AssertionError("no evaluation")):
            before = snapshot_digest(fixture.capture(session))
            impact.deadline -= 1
            changed_impact = snapshot_digest(fixture.capture(session))
            impact.deadline += 1
            clock.starts += 0.25
            changed_decay = snapshot_digest(fixture.capture(session))
        self.assertNotEqual(before, changed_impact)
        self.assertNotEqual(before, changed_decay)
        self.assertNotEqual(changed_impact, changed_decay)

    def test_actual_reinhardt_charge_contact_is_passively_causal(self):
        from ow174.game import reinhardt_charge

        fixture = snapshot_tests.ServerSnapshotTests()
        session, peers, players = fixture.scene(two=True)
        rein, target = players
        peers[0].message(21616, pick(rein.entity, reinhardt_charge.HERO), now=100.8)
        peers[1].message(21616, pick(target.entity, reaper.HERO), now=100.8)
        rein.team, target.team = 0, 1
        x, y, z = rein.mover.position
        target.mover.position[:] = (x, y, z + 6)
        target.mover.body = None
        match = rein.match
        fight = match.combat
        rein.take_commands([Command(rein.body_script.frame + 1, buttons=8)])
        for _ in range(100):
            match.update(fight.now + 0.016, match.tick + 1, frames=False)
            rein.take_commands([Command(rein.body_script.frame + 1)])
            if reinhardt_charge.pinned(fight, target.body):
                break
        charge = fight.charges[rein.body]
        self.assertEqual(charge.pinned, target.body)
        self.assertIn(target.body, charge.hit)
        with patch.object(runtime.Var, "value", side_effect=AssertionError("no evaluation")):
            before = snapshot_digest(fixture.capture(session))
            charge.deadline -= 1
            moved = snapshot_digest(fixture.capture(session))
            charge.pinned = None
            released = snapshot_digest(fixture.capture(session))
        self.assertNotEqual(before, moved)
        self.assertNotEqual(moved, released)

    def test_actual_swift_strike_hit_history_is_passively_causal(self):
        fixture = snapshot_tests.ServerSnapshotTests()
        session, peers, players = fixture.scene(two=True)
        genji, target = players
        peers[0].message(21616, pick(genji.entity, 0x02E0000000000029), now=100.8)
        peers[1].message(21616, pick(target.entity, reaper.HERO), now=100.8)
        genji.team, target.team = 0, 1
        x, y, z = genji.mover.position
        target.mover.position[:] = (x, y, z + 4)
        target.mover.body = None
        script = genji.body_script
        fight = genji.match.combat
        dash = next(i for i in script.component.instances.values() if i.graph.index == 0x01DB)
        genji.take_commands([Command(script.frame + 1, buttons=8)])
        for _ in range(12):
            genji.take_commands([Command(script.frame + 1)])
            if target.body in dash.state(1).swept_targets:
                break
        state = dash.state(1)
        self.assertTrue(state.active)
        self.assertEqual(state.swept_targets, {target.body})
        self.assertEqual(fight.vitals[target.body].total, 200)
        with patch.object(runtime.Var, "value", side_effect=AssertionError("no evaluation")):
            before = snapshot_digest(fixture.capture(session))
            state.swept_targets.clear()
            changed = snapshot_digest(fixture.capture(session))
            state.swept_targets.add(target.body)
            restored = snapshot_digest(fixture.capture(session))
        self.assertNotEqual(before, changed)
        self.assertEqual(before, restored)
        for _ in range(24):
            genji.take_commands([Command(script.frame + 1)])
        self.assertFalse(state.active)
        self.assertFalse(state.swept_targets)
        self.assertEqual(fight.vitals[target.body].total, 200)

    def test_actual_amplification_matrix_deadline_is_passively_causal(self):
        from ow174.game import amplification
        from ow174.game.script.driver import ULT_COST

        fixture = snapshot_tests.ServerSnapshotTests()
        session, peers, players = fixture.scene()
        caster = players[0]
        peers[0].message(21616, pick(caster.entity, amplification.HERO), now=100.8)
        script = caster.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        fight = caster.match.combat
        for offset in range(40):
            buttons = 32 if offset in (0, 8) else 1 if offset == 10 else 0
            caster.take_commands([Command(script.frame + 1, pitch=8192, buttons=buttons)])
        window, = fight.amplification.windows.values()
        self.assertEqual(window.expires - window.created, 625)
        with patch.object(runtime.Var, "value", side_effect=AssertionError("no evaluation")):
            before = snapshot_digest(fixture.capture(session))
            window.expires += 1
            changed = snapshot_digest(fixture.capture(session))
        self.assertNotEqual(before, changed)

    def test_actual_discord_target_and_los_clock_are_passively_causal(self):
        from ow174.game import discord

        fixture = snapshot_tests.ServerSnapshotTests()
        session, peers, players = fixture.scene(two=True)
        caster, target = players
        peers[0].message(21616, pick(caster.entity, discord.HERO), now=100.8)
        peers[1].message(21616, pick(target.entity, reaper.HERO), now=100.8)
        x, y, z = caster.mover.position
        target.mover.position[:] = (x, y, z + 4)
        target.mover.body = None
        peers[0].commands(caster.body_script.frame + 1, 1, 8)
        orb = caster.match.combat.discord.active[caster.body]
        self.assertEqual(orb.target, target.body)
        with patch.object(runtime.Var, "value", side_effect=AssertionError("no evaluation")):
            before = snapshot_digest(fixture.capture(session))
            orb.last_visible -= 0.016
            shifted = snapshot_digest(fixture.capture(session))
        self.assertNotEqual(before, shifted)

    def test_actual_nano_deadline_is_passively_causal(self):
        from ow174.game import nano
        from ow174.game.script.driver import ULT_COST

        fixture = snapshot_tests.ServerSnapshotTests()
        session, peers, players = fixture.scene(two=True)
        caster, target = players
        peers[0].message(21616, pick(caster.entity, ana.HERO), now=100.8)
        peers[1].message(21616, pick(target.entity, reaper.HERO), now=100.8)
        target.team = caster.team
        x, y, z = caster.mover.position
        target.mover.position[:] = (x, y, z + 4)
        target.mover.body = None
        script = caster.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        fight = caster.match.combat
        yaw, pitch = aim(fight._eye(caster), (x, y + 1, z + 4))
        for offset in range(20):
            caster.take_commands([Command(script.frame + 1, yaw=yaw, pitch=pitch,
                                          buttons=32 if offset == 0 else 0)])
        vitals = fight.vitals[target.body]
        self.assertTrue(nano.active(fight, vitals))
        with patch.object(runtime.Var, "value", side_effect=AssertionError("no evaluation")):
            before = snapshot_digest(fixture.capture(session))
            vitals.nano_until += 0.016
            changed = snapshot_digest(fixture.capture(session))
        self.assertNotEqual(before, changed)

    def test_actual_mercy_beam_target_is_passively_causal(self):
        from ow174.game import mercy_beam

        fixture = snapshot_tests.ServerSnapshotTests()
        session, peers, players = fixture.scene(two=True)
        caster, target = players
        peers[0].message(21616, pick(caster.entity, mercy_beam.HERO), now=100.8)
        peers[1].message(21616, pick(target.entity, reaper.HERO), now=100.8)
        target.team = caster.team
        x, y, z = caster.mover.position
        target.mover.position[:] = (x, y, z + 4)
        target.mover.body = None
        peers[0].commands(caster.body_script.frame + 1, 20, 2)
        fight = caster.match.combat
        self.assertEqual(mercy_beam.factor(fight, target), 1.3)
        instance = next(i for i in caster.body_script.component.instances.values()
                        if i.graph.index == mercy_beam.STAFF)
        hold = instance.states[5]
        with patch.object(runtime.Var, "value", side_effect=AssertionError("no evaluation")):
            before = snapshot_digest(fixture.capture(session))
            hold.targets = ()
            released = snapshot_digest(fixture.capture(session))
        self.assertNotEqual(before, released)

    def test_actual_steel_trap_root_deadline_is_passively_causal(self):
        from ow174.game.content import find_hero

        fixture = snapshot_tests.ServerSnapshotTests()
        session, peers, players = fixture.scene(two=True)
        caster, target = players
        peers[0].message(21616, pick(caster.entity, find_hero("Junkrat").guid), now=100.8)
        peers[1].message(21616, pick(target.entity, reaper.HERO), now=100.8)
        match = caster.match
        fight = match.combat
        x, y, z = caster.mover.position
        target.mover.position[:] = (x + 8, y, z)
        target.mover.body = None
        script = caster.body_script
        for offset in range(80):
            match.update(fight.now + 0.016, match.tick + 1, frames=False)
            caster.take_commands([Command(script.frame + 1, buttons=16 if offset == 0 else 0)])
        trap, = fight.traps.actors.values()
        self.assertIsNotNone(trap.armed_at)
        target.mover.position[:] = trap.position
        target.mover.body = None
        match.update(fight.now + 0.016, match.tick + 1, frames=False)
        self.assertTrue(fight.traps.rooted(target.body, match.tick))
        with patch.object(runtime.Var, "value", side_effect=AssertionError("no evaluation")):
            before = snapshot_digest(fixture.capture(session))
            trap.release_tick += 1
            shifted = snapshot_digest(fixture.capture(session))
        self.assertNotEqual(before, shifted)

    def test_actual_blizzard_area_deadline_is_passively_causal(self):
        from ow174.game import mei
        from ow174.game.script.driver import ULT_COST

        fixture = snapshot_tests.ServerSnapshotTests()
        session, peers, players = fixture.scene(two=True)
        caster, target = players
        peers[0].message(21616, pick(caster.entity, mei.HERO), now=100.8)
        peers[1].message(21616, pick(target.entity, reaper.HERO), now=100.8)
        match = caster.match
        fight = match.combat
        x, y, z = caster.mover.position
        target.mover.position[:] = (x, y, z + 4)
        target.mover.body = None
        script = caster.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        yaw, pitch = aim(fight._eye(caster), (x, y, z + 5))
        for offset in range(100):
            match.update(fight.now + 0.016, match.tick + 1, frames=False)
            caster.take_commands([Command(script.frame + 1, yaw=yaw, pitch=pitch,
                                          buttons=32 if offset == 0 else 0)])
            if fight.blizzard.areas:
                break
        area, = fight.blizzard.areas.values()
        self.assertEqual(area.ends - area.starts, 4250)
        with patch.object(runtime.Var, "value", side_effect=AssertionError("no evaluation")):
            before = snapshot_digest(fixture.capture(session))
            area.ends += 16
            shifted = snapshot_digest(fixture.capture(session))
        self.assertNotEqual(before, shifted)

    def test_actual_mei_spray_frost_and_penetration_are_passively_causal(self):
        from ow174.game import mei

        fixture = snapshot_tests.ServerSnapshotTests()
        session, peers, players = fixture.scene(two=True)
        caster, target = players
        peers[0].message(21616, pick(caster.entity, mei.HERO), now=100.8)
        peers[1].message(21616, pick(target.entity, reaper.HERO), now=100.8)
        match = caster.match
        fight = match.combat
        x, y, z = caster.mover.position
        target.mover.position[:] = (x, y, z + 4)
        target.mover.body = None
        peers[0].commands(caster.body_script.frame + 1, 1, 1)
        for _ in range(12):
            match.update(fight.now + 0.016, match.tick + 1, frames=False)
            if fight.frost(target):
                break
        self.assertAlmostEqual(fight.frost(target)[0], 0.2)
        spray = next(rocket for rocket in fight.rockets if target.body in rocket.hit_entities)
        with patch.object(runtime.Var, "value", side_effect=AssertionError("no evaluation")):
            before = snapshot_digest(fixture.capture(session))
            spray.hit_entities.clear()
            changed = snapshot_digest(fixture.capture(session))
            fight.frost_sources[target.body].clear()
            without_source = snapshot_digest(fixture.capture(session))
        self.assertNotEqual(before, changed)
        self.assertNotEqual(changed, without_source)

    def test_actual_hack_receiver_deadline_is_passively_causal(self):
        from ow174.game import sombra

        fixture = snapshot_tests.ServerSnapshotTests()
        session, peers, players = fixture.scene(two=True)
        caster, target = players
        peers[0].message(21616, pick(caster.entity, sombra.SOMBRA), now=100.8)
        peers[1].message(21616, pick(target.entity, reaper.HERO), now=100.8)
        x, y, z = caster.mover.position
        target.mover.position[:] = (x, y, z + 4)
        target.mover.body = None
        peers[0].commands(caster.body_script.frame + 1, 55, 2)
        receiver = sombra.instance_for(target, 0xB1F)
        self.assertTrue(sombra.locked(target))
        wait = receiver.states[58]
        self.assertEqual(wait.ms, 5000)
        with patch.object(runtime.Var, "value", side_effect=AssertionError("no evaluation")):
            before = snapshot_digest(fixture.capture(session))
            wait.start += 1
            shifted = snapshot_digest(fixture.capture(session))
        self.assertNotEqual(before, shifted)

    def test_actual_hook_target_and_pull_tick_are_passively_causal(self):
        from ow174.game.hook import ROADHOG

        fixture = snapshot_tests.ServerSnapshotTests()
        session, peers, players = fixture.scene(two=True)
        caster, target = players
        peers[0].message(21616, pick(caster.entity, ROADHOG), now=100.8)
        peers[1].message(21616, pick(target.entity, reaper.HERO), now=100.8)
        match = caster.match
        fight = match.combat
        x, y, z = caster.mover.position
        target.mover.position[:] = (x, y, z + 10)
        target.mover.body = None
        peers[0].commands(caster.body_script.frame + 1, 1, 8)
        for _ in range(45):
            match.update(fight.now + 0.016, match.tick + 1, frames=False)
            if fight.hooks.controlled(target.body):
                break
            caster.take_commands([Command(caster.body_script.frame + 1)])
        flight = fight.hooks.flights[caster.body]
        self.assertEqual(flight.target.entity, target.body)
        with patch.object(runtime.Var, "value", side_effect=AssertionError("no evaluation")):
            before = snapshot_digest(fixture.capture(session))
            flight.last_move_tick -= 1
            moved = snapshot_digest(fixture.capture(session))
            flight.target = None
            released = snapshot_digest(fixture.capture(session))
        self.assertNotEqual(before, moved)
        self.assertNotEqual(moved, released)

    def test_actual_photon_barrier_pool_and_expiry_are_passively_causal(self):
        from ow174.game.body_vm_snapshot import export_component_vm
        from ow174.game.script.driver import ULT_COST
        from ow174.game.symmetra_barrier import GEOMETRY_SHA256, HERO

        if GEOMETRY_SHA256 is None:
            self.skipTest("requires the pinned Symmetra 6880 collision mesh")
        fixture = snapshot_tests.ServerSnapshotTests()
        session, peers, players = fixture.scene(two=True)
        defender = players[1]
        peers[1].message(21616, pick(defender.entity, HERO), now=100.8)
        script = defender.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        peers[1].commands(script.frame + 1, 16, 32)
        peers[1].commands(script.frame + 1, 15, 0)
        peers[1].commands(script.frame + 1, 1, 1)
        peers[1].commands(script.frame + 1, 35, 0)
        actor = defender.match.combat.symmetra_barriers.actors[defender.body]
        self.assertEqual(actor.health, 4000)
        self.assertTrue(any(i.graph.index == 0x156C for i in actor.component.instances.values()))
        with patch.object(runtime.Var, "value", side_effect=AssertionError("no evaluation")):
            self.assertTrue(export_component_vm(actor.component))
            before = snapshot_digest(fixture.capture(session))
            actor.health -= 6
            damaged = snapshot_digest(fixture.capture(session))
            actor.expires += 1
            extended = snapshot_digest(fixture.capture(session))
        self.assertNotEqual(before, damaged)
        self.assertNotEqual(damaged, extended)

    def test_actual_brigitte_barrier_pool_and_break_clock_are_passively_causal(self):
        from ow174.game.brigitte_shield import GEOMETRY_SHA256, HERO

        if GEOMETRY_SHA256 is None:
            self.skipTest("requires the pinned Brigitte 5DA0 collision mesh")
        fixture = snapshot_tests.ServerSnapshotTests()
        session, peers, players = fixture.scene(two=True)
        defender = players[1]
        peers[1].message(21616, pick(defender.entity, HERO), now=100.8)
        peers[1].commands(defender.body_script.frame + 1, 40, 2)
        shield = defender.match.combat.brigitte_shields.by_body[defender.body]
        self.assertTrue(shield.active())
        self.assertEqual(shield.health, 250)
        with patch.object(runtime.Var, "value", side_effect=AssertionError("no evaluation")):
            before = snapshot_digest(fixture.capture(session))
            shield.health -= 6
            damaged = snapshot_digest(fixture.capture(session))
            shield.broken_until += 1
            locked = snapshot_digest(fixture.capture(session))
        self.assertNotEqual(before, damaged)
        self.assertNotEqual(damaged, locked)

    def test_actual_sigma_barrier_mesh_pose_and_pool_are_passively_causal(self):
        from ow174.game.sigma_barrier import GEOMETRY_SHA256

        if GEOMETRY_SHA256 is None:
            self.skipTest("requires the pinned Sigma C015 collision mesh")
        fixture = snapshot_tests.ServerSnapshotTests()
        session, peers, players = fixture.scene(two=True)
        defender = players[1]
        peers[1].message(21616, pick(defender.entity, sigma_grasp.HERO), now=100.8)
        peers[1].commands(defender.body_script.frame + 1, 40, 2)
        actor = defender.match.combat.sigma_barriers.actors[defender.body]
        self.assertTrue(actor.active())
        self.assertEqual(actor.health, 700)
        self.assertEqual(actor.instance().graph.index, 0x1A2A)
        with patch.object(runtime.Var, "value", side_effect=AssertionError("no evaluation")):
            before = snapshot_digest(fixture.capture(session))
            actor.health -= 6
            damaged = snapshot_digest(fixture.capture(session))
            actor.position = (actor.position[0] + 0.25, *actor.position[1:])
            moved = snapshot_digest(fixture.capture(session))
        self.assertNotEqual(before, damaged)
        self.assertNotEqual(damaged, moved)

    def test_actual_zarya_bubble_pool_and_phase_are_passively_causal(self):
        fixture = snapshot_tests.ServerSnapshotTests()
        session, peers, players = fixture.scene(two=True)
        defender = players[1]
        peers[1].message(21616, pick(defender.entity, 0x02E0000000000068), now=100.8)
        peers[1].commands(defender.body_script.frame + 1, 40, 8)
        bubble = defender.match.combat.zarya_bubbles.for_target(defender)
        self.assertIsNotNone(bubble)
        self.assertEqual(bubble.health, 200)
        with patch.object(runtime.Var, "value", side_effect=AssertionError("no evaluation")):
            before = snapshot_digest(fixture.capture(session))
            bubble.health -= 6
            damaged = snapshot_digest(fixture.capture(session))
            bubble.phase.start += 1
            advanced = snapshot_digest(fixture.capture(session))
        self.assertNotEqual(before, damaged)
        self.assertNotEqual(damaged, advanced)

    def test_actual_orisa_flight_and_landed_mesh_are_passively_causal(self):
        from ow174.game.orisa_barrier import GEOMETRY_SHA256

        if GEOMETRY_SHA256 is None:
            self.skipTest("requires the pinned Orisa barrier collision mesh")
        fixture = snapshot_tests.ServerSnapshotTests()
        session, peers, players = fixture.scene(two=True)
        defender = players[1]
        peers[1].message(21616, pick(defender.entity, 0x02E000000000013E), now=100.8)
        script = defender.body_script
        match = defender.match
        fight = match.combat
        for index in range(45):
            defender.take_commands([Command(script.frame + 1, pitch=8192, buttons=16 if index == 0 else 0)])
            if defender.body in fight.orisa_barriers.flights:
                break
        flight = fight.orisa_barriers.flights.get(defender.body)
        self.assertIsNotNone(flight, "Orisa barrier cast did not launch")
        with patch.object(runtime.Var, "value", side_effect=AssertionError("no evaluation")):
            first = snapshot_digest(fixture.capture(session))
            flight.remaining -= 0.001
            self.assertNotEqual(first, snapshot_digest(fixture.capture(session)))
        start = fight.tick
        fight.update(fight.now + 40 * 0.016, start + 40, frames=False)
        actor = fight.orisa_barriers.actors.get(defender.body)
        self.assertIsNotNone(actor, "Orisa barrier projectile did not land")
        self.assertEqual(actor.health, 600)
        with patch.object(runtime.Var, "value", side_effect=AssertionError("no evaluation")):
            before = snapshot_digest(fixture.capture(session))
            actor.health -= 6
            self.assertNotEqual(before, snapshot_digest(fixture.capture(session)))

    def test_actual_winston_dome_and_child_vm_are_passively_causal(self):
        fixture = snapshot_tests.ServerSnapshotTests()
        session, peers, players = fixture.scene(two=True)
        defender = players[1]
        peers[1].message(21616, pick(defender.entity, 0x02E0000000000009), now=100.8)
        peers[1].commands(defender.body_script.frame + 1, 40, 16)
        bubble, = defender.match.combat.winston_bubbles.actors.values()
        self.assertEqual(bubble.health, 700)
        self.assertTrue(any(i.graph.index == 0xFC for i in bubble.component.instances.values()))
        with (
            patch.object(runtime.Var, "value", side_effect=AssertionError("no evaluation")),
            patch.object(runtime.Component, "run_now", side_effect=AssertionError("no VM update")),
        ):
            before = snapshot_digest(fixture.capture(session))
            bubble.health -= 6
            damaged = snapshot_digest(fixture.capture(session))
            bubble.component.frame += 1
            advanced = snapshot_digest(fixture.capture(session))
        self.assertNotEqual(before, damaged)
        self.assertNotEqual(damaged, advanced)

    def test_grasp_temporary_pool_and_decay_clock_are_passively_causal(self):
        fixture = snapshot_tests.ServerSnapshotTests()
        session, _, players = fixture.scene(two=True)
        owner = players[0]
        fight = owner.match.combat
        target = next(t for t in fight._targets() if t.owner is owner)
        vitals = fight.vitals_of(target)
        vitals.grant_temporary_shields(sigma_grasp.SHIELD_TAG, 72, 400)
        fight.grasp_shields[owner.body] = sigma_grasp.ShieldDecay(120, 117, 5)
        fight.grasp_grants[owner.body] = (1, 2)
        with (
            patch.object(runtime.Var, "value", side_effect=AssertionError("no evaluation")),
            patch.object(runtime.Component, "run_now", side_effect=AssertionError("no VM update")),
        ):
            before = snapshot_digest(fixture.capture(session))
            vitals.temporary_shields[sigma_grasp.SHIELD_TAG].now -= 1
            damaged = snapshot_digest(fixture.capture(session))
            fight.grasp_shields[owner.body].last += 0.5
            decayed = snapshot_digest(fixture.capture(session))
        self.assertNotEqual(before, damaged)
        self.assertNotEqual(damaged, decayed)

    def test_layered_temporary_shield_priority_is_passively_causal(self):
        from ow174.game import lucio_barrier

        fixture = snapshot_tests.ServerSnapshotTests()
        session, peers, players = fixture.scene(two=True)
        owner, sigma = players
        peers[0].message(21616, pick(owner.entity, reaper.HERO), now=100.8)
        peers[1].message(21616, pick(sigma.entity, sigma_grasp.HERO), now=100.8)
        match = owner.match
        fight = match.combat
        match.collision_world = flat_world(1)
        owner.mover.position[:] = (0, 1, 0)
        sigma.mover.position[:] = (0, 1, 3)
        owner.mover.body = sigma.mover.body = None
        owner.team, sigma.team = 0, 1
        victim = next(t for t in fight._targets() if t.owner is sigma)
        vitals = fight.vitals_of(victim)
        vitals.grant_temporary_shields(lucio_barrier.POOL, 50, 50)
        vitals.grant_temporary_shields(sigma_grasp.SHIELD_TAG, 50, 50)
        shields = vitals.temporary_shields
        original = tuple(shields.items())
        health = fight.vitals_of(next(t for t in fight._targets() if t.owner is owner))
        health.damage(health.total - 100)
        owner.body_script.set_ult_charge(owner.body_script.component.entity_var(ULT_COST).value())
        with (
            patch.object(runtime.Var, "value", side_effect=AssertionError("no evaluation")),
            patch.object(runtime.Component, "run_now", side_effect=AssertionError("no VM update")),
        ):
            before = fixture.capture(session)
            shields.clear()
            shields.update(reversed(original))
            reversed_order = fixture.capture(session)
        self.assertFalse(before["restorable"])
        self.assertFalse(reversed_order["restorable"])
        self.assertNotEqual(snapshot_digest(before), snapshot_digest(reversed_order))
        self.assertEqual(tuple(shields.items()), tuple(reversed(original)))
        self.assertEqual([pool.now for pool in shields.values()], [50, 50])

        match.update(fight.now + 0.016, match.tick + 1, frames=False)
        script = owner.body_script
        owner.take_commands([Command(max(script.frame, match.tick) + 1, buttons=32)])
        self.assertIsNotNone(reaper.blossom_state(script))
        self.assertEqual(shields[lucio_barrier.POOL].now, 16)
        self.assertEqual(shields[sigma_grasp.SHIELD_TAG].now, 50)
        self.assertAlmostEqual(health.total, 100 + 34 * reaper.REAPING_RATIO, places=4)

    def test_actual_barrier_health_and_regen_presence_poll_are_passively_causal(self):
        fixture = snapshot_tests.ServerSnapshotTests()
        session, peers, players = fixture.scene(two=True)
        defender = players[1]
        peers[1].message(21616, pick(defender.entity, barriers.REINHARDT), now=100.8)
        peers[1].commands(defender.body_script.frame + 1, 30, 2)
        instance = barriers.active(defender)
        self.assertIsNotNone(instance)
        health = instance.vars[586]
        presence = instance.states[6]
        self.assertTrue(presence.polled)
        with (
            patch.object(runtime.Var, "value", side_effect=AssertionError("no evaluation")),
            patch.object(barriers, "active", side_effect=AssertionError("no active query")),
            patch.object(barriers, "_geometry", side_effect=AssertionError("no geometry load")),
        ):
            before = snapshot_digest(fixture.capture(session))
            health.base -= 6
            damaged = snapshot_digest(fixture.capture(session))
            presence.polled = False
            unpolled = snapshot_digest(fixture.capture(session))
        self.assertNotEqual(before, damaged)
        self.assertNotEqual(damaged, unpolled)

    def test_actual_assassin_navigation_is_passively_causal(self):
        import os
        from pathlib import Path

        from ow174.game import npc_navigation

        root = os.environ.get("ORPHEUS_NPC_RUNTIME_DIR")
        if not root:
            self.skipTest("set ORPHEUS_NPC_RUNTIME_DIR for actual Assassin")
        definition = npc.load_catalog(Path(root) / "data/pve_npcs_174.json")["4"]
        fixture = snapshot_tests.ServerSnapshotTests()
        session, peers, players = fixture.scene(two=True)
        owner, other = players
        if owner.hero.guid != reaper.HERO:
            peers[0].message(21616, pick(owner.entity, reaper.HERO), now=100.8)
        x, y, z = owner.mover.position
        other.mover.position[:] = [x + 20, y, z]
        other.mover.body = None
        bot = npc.spawn(owner.match, definition, (x, y, z + 5), team=1)
        owner.mover.yaw, owner.mover.pitch = aim(owner.match.combat._eye(owner), (x, y + 1, z + 5))
        self.assertEqual(npc_control.start(owner.match, owner), bot.entity)
        fight = owner.match.combat
        owner.match.update(fight.now + 0.016, owner.match.tick + 1, frames=False)
        self.assertIsNotNone(bot.navigation)
        with patch.object(npc_navigation, "step", side_effect=AssertionError("no navigation update")):
            before = snapshot_digest(fixture.capture(session))
            bot.navigation.next_plan += 1
            changed_plan = snapshot_digest(fixture.capture(session))
            bot.navigation.next_plan -= 1
            bot.navigation.forced_landing = True
            changed_landing = snapshot_digest(fixture.capture(session))
        self.assertEqual(len({before, changed_plan, changed_landing}), 3)

    def test_actual_assassin_pin_clock_target_and_control_are_passively_causal(self):
        import os
        from pathlib import Path

        root = os.environ.get("ORPHEUS_NPC_RUNTIME_DIR")
        if not root:
            self.skipTest("set ORPHEUS_NPC_RUNTIME_DIR for actual Assassin")
        definition = npc.load_catalog(Path(root) / "data/pve_npcs_174.json")["4"]
        fixture = snapshot_tests.ServerSnapshotTests()
        session, peers, players = fixture.scene(two=True)
        owner, other = players
        if owner.hero.guid != reaper.HERO:
            peers[0].message(21616, pick(owner.entity, reaper.HERO), now=100.8)
        x, y, z = owner.mover.position
        other.mover.position[:] = [x + 20, y, z]
        other.mover.body = None
        bot = npc.spawn(owner.match, definition, (x, y, z + 1.5), team=1)
        yaw, pitch = aim(owner.match.combat._eye(owner), (x, y + 1, z + 1.5))
        owner.mover.yaw, owner.mover.pitch = yaw, pitch
        self.assertEqual(npc_control.start(owner.match, owner), bot.entity)
        fight = owner.match.combat
        instance = next(i for i in bot.body_script.component.instances.values() if i.graph.index == 0x1157)
        health = None
        for _ in range(120):
            owner.match.update(fight.now + 0.016, owner.match.tick + 1, frames=False)
            health = instance.states.get(instance.graph.node(15).state)
            if health is not None and health.active:
                break
        self.assertIsNotNone(health)
        self.assertTrue(health.active)
        self.assertEqual(fight.pins[bot.entity], owner.body)
        world = bot.body_script.component.world
        with (
            patch.object(runtime.Var, "value", side_effect=AssertionError("no evaluation")),
            patch.object(type(world), "native", side_effect=AssertionError("no context query")),
        ):
            before = snapshot_digest(fixture.capture(session))
            health.last_applied += 1
            clock = snapshot_digest(fixture.capture(session))
            world.manual_target = None
            target = snapshot_digest(fixture.capture(session))
            fight.pins.pop(bot.entity)
            control = snapshot_digest(fixture.capture(session))
        self.assertNotEqual(before, clock)
        self.assertNotEqual(clock, target)
        self.assertNotEqual(target, control)

    def test_actual_enforcer_controller_facts_are_causal_without_query_evaluation(self):
        import os
        from pathlib import Path

        root = os.environ.get("ORPHEUS_NPC_RUNTIME_DIR")
        if not root:
            self.skipTest("set ORPHEUS_NPC_RUNTIME_DIR for actual Enforcer")
        definition = npc.load_catalog(Path(root) / "data/pve_npcs_174.json")["2"]
        fixture = snapshot_tests.ServerSnapshotTests()
        session, _peers, players = fixture.scene(two=True)
        owner = players[0]
        x, y, z = owner.mover.position
        bot = npc.spawn(owner.match, definition, (x, y, z + 2), team=1)
        world = bot.body_script.component.world
        self.assertEqual(world.controller_components, (75,))
        with (
            patch.object(runtime.Var, "value", side_effect=AssertionError("no evaluation")),
            patch.object(type(world), "native", side_effect=AssertionError("no context query")),
        ):
            before = snapshot_digest(fixture.capture(session))
            world.controller_components = (75, 92)
            after = snapshot_digest(fixture.capture(session))
        self.assertNotEqual(before, after)

    def test_actual_sleep_clock_is_causal_without_running_control_or_vm(self):
        fixture = snapshot_tests.ServerSnapshotTests()
        session, peers, players = fixture.scene(two=True)
        target, caster = players
        peers[1].message(21616, pick(caster.entity, ana.HERO), now=100.8)
        x, y, z = target.mover.position
        caster.mover.position[:] = [x, y, z + 1.5]
        caster.mover.body = None
        fight = target.match.combat
        yaw, pitch = aim(fight._eye(caster), (x, y + 1, z))
        for frame in range(60):
            caster.take_commands([Command(caster.body_script.frame + 1, yaw=yaw, pitch=pitch,
                                          buttons=8 if frame == 0 else 0)])
            target.match.update(fight.now + 0.016, target.match.tick + 1, frames=False)
            if target.body in fight.ana_sleeps:
                break
        record = fight.ana_sleeps[target.body]
        with (
            patch.object(runtime.Var, "value", side_effect=AssertionError("no evaluation")),
            patch.object(type(fight), "is_stunned", side_effect=AssertionError("no control evaluation")),
        ):
            before = snapshot_digest(fixture.capture(session))
            record["end"] += 1
            after = snapshot_digest(fixture.capture(session))
        self.assertNotEqual(before, after)

    def test_healing_modifier_clocks_are_causal_without_status_or_vm_evaluation(self):
        fixture = snapshot_tests.ServerSnapshotTests()
        session, peers, players = fixture.scene(two=True)
        target, caster = players
        peers[1].message(21616, pick(caster.entity, ana.HERO), now=100.8)
        x, y, z = target.mover.position
        caster.mover.position[:] = [x, y, z + 1.5]
        caster.mover.body = None
        fight = target.match.combat
        yaw, pitch = aim(fight._eye(caster), (x, y, z))
        caster.take_commands([Command(caster.body_script.frame + 1, yaw=yaw, pitch=pitch, buttons=16)])
        for _ in range(15):
            target.match.update(fight.now + 0.016, target.match.tick + 1, frames=False)
        record = fight.heal_modifiers[target.body]["anti"]
        with (
            patch.object(runtime.Var, "value", side_effect=AssertionError("no evaluation")),
            patch.object(type(fight), "_healing_factor", side_effect=AssertionError("no status evaluation")),
        ):
            before = snapshot_digest(fixture.capture(session))
            record["end"] += 0.01
            after = snapshot_digest(fixture.capture(session))
        self.assertNotEqual(before, after)

    def test_defensive_healing_clock_is_causal_without_running_vm_or_healing(self):
        fixture = snapshot_tests.ServerSnapshotTests()
        session, peers, players = fixture.scene(two=True)
        caster = players[1]
        peers[1].message(21616, pick(caster.entity, defenses.MEI), now=100.8)
        peers[1].commands(caster.body_script.frame + 1, 1, 8)
        record = caster.match.combat.defense_heals[caster.body]
        with (
            patch.object(runtime.Var, "value", side_effect=AssertionError("no evaluation")),
            patch.object(runtime.State, "payload", side_effect=AssertionError("no payload")),
            patch.object(defenses, "healing", side_effect=AssertionError("no healing getter")),
        ):
            before = snapshot_digest(fixture.capture(session))
            record["last"] += 0.001
            after = snapshot_digest(fixture.capture(session))
        self.assertNotEqual(before, after)

    def test_cross_hero_protection_scope_is_exported_without_defense_or_vm_evaluation(self):
        for hero, button in ((defenses.ZENYATTA, 32), (defenses.MEI, 8)):
            with self.subTest(hero=hero):
                fixture = snapshot_tests.ServerSnapshotTests()
                session, peers, players = fixture.scene(two=True)
                defender = players[1]
                peers[1].message(21616, pick(defender.entity, hero), now=100.8)
                script = defender.body_script
                if hero == defenses.ZENYATTA:
                    script.set_ult_charge(script.component.vars[338].value())
                peers[1].commands(script.frame + 1, 1, button)
                self.assertTrue(defenses.invulnerable(defender))
                graph_id, _, index = defenses.SELF_IMMUNITY[hero]
                scope = next(i for i in script.component.instances.values()
                             if i.graph.index == graph_id).state(index)
                with (
                    patch.object(runtime.Var, "value", side_effect=AssertionError("no evaluation")),
                    patch.object(runtime.State, "payload", side_effect=AssertionError("no payload")),
                    patch.object(defenses, "invulnerable", side_effect=AssertionError("no defense getter")),
                ):
                    before = snapshot_digest(fixture.capture(session))
                    scope.active = False
                    after = snapshot_digest(fixture.capture(session))
                self.assertNotEqual(before, after)

    def test_partial_passive_charge_cadence_is_passively_exported(self):
        fixture = snapshot_tests.ServerSnapshotTests()
        session, peers, players = fixture.scene(two=True)
        owner = players[0]
        peers[0].message(21616, pick(owner.entity, reaper.HERO), now=100.8)
        peers[0].commands(owner.body_script.frame + 1, 30, 0)
        owner.match.update(101.28, owner.match.tick + 30, frames=False)
        script = owner.body_script
        self.assertGreater(script.reaper_charge_ms, 0)
        self.assertLess(script.reaper_charge_ms, 1000)
        with patch.object(runtime.Var, "value", side_effect=AssertionError("no evaluation")):
            before = snapshot_digest(fixture.capture(session))
            trace_before = body_trace_hash(script)
            script.reaper_charge_ms += 1
            after = snapshot_digest(fixture.capture(session))
            trace_after = body_trace_hash(script)
        self.assertNotEqual(before, after)
        self.assertNotEqual(trace_before, trace_after)
        with patch.object(runtime.Var, "value", side_effect=AssertionError("no evaluation")):
            before = snapshot_digest(fixture.capture(session))
            trace_before = body_trace_hash(script)
            script.reaper_charge_tick += 1
            after = snapshot_digest(fixture.capture(session))
            trace_after = body_trace_hash(script)
        self.assertNotEqual(before, after)
        self.assertNotEqual(trace_before, trace_after)

    def test_world_frame_anchor_is_causal_without_running_vm_or_movement(self):
        fixture = snapshot_tests.ServerSnapshotTests()
        session, peers, players = fixture.scene(two=True)
        owner = players[0]
        peers[0].message(21616, pick(owner.entity, reaper.HERO), now=100.8)
        script = owner.body_script
        with patch.object(runtime.Var, "value", side_effect=AssertionError("no evaluation")):
            before = snapshot_digest(fixture.capture(session))
            trace_before = body_trace_hash(script)
            tick, frame = script.world_frame_anchor
            script.world_frame_anchor = tick + 1, frame
            after = snapshot_digest(fixture.capture(session))
            trace_after = body_trace_hash(script)
        self.assertNotEqual(before, after)
        self.assertNotEqual(trace_before, trace_after)

    def test_active_selector_edge_state_is_passively_exported(self):
        fixture = snapshot_tests.ServerSnapshotTests()
        session, peers, players = fixture.scene(two=True)
        owner = players[0]
        peers[0].message(21616, pick(owner.entity, reaper.HERO), now=100.8)
        peers[0].commands(owner.body_script.frame + 1, 1, 16)
        record = owner.match.combat.reaper_shadows[owner.body]
        with patch.object(runtime.Var, "value", side_effect=AssertionError("no evaluation")):
            before = snapshot_digest(fixture.capture(session))
            record["buttons"] ^= 1
            after = snapshot_digest(fixture.capture(session))
        self.assertNotEqual(before, after)

    def test_active_channel_and_target_clock_are_exported_without_evaluation(self):
        fixture = snapshot_tests.ServerSnapshotTests()
        session, peers, players = fixture.scene(two=True)
        owner, victim = players
        match = owner.match
        peers[0].message(21616, pick(owner.entity, reaper.HERO), now=100.8)
        x, y, z = owner.mover.position
        victim.mover.position[:] = [x, y, z + 1.5]
        victim.mover.body = None
        owner.body_script.set_ult_charge(2100)
        peers[0].commands(owner.body_script.frame + 1, 1, 32)
        channel = match.combat.reaper_blossoms[owner.body]
        self.assertIn(victim.body, channel["targets"])
        with (
            patch.object(runtime.Var, "value", side_effect=AssertionError("no evaluation")),
            patch.object(runtime.State, "payload", side_effect=AssertionError("no payload")),
        ):
            before = snapshot_digest(fixture.capture(session))
            channel["targets"][victim.body] += 0.01
            after = snapshot_digest(fixture.capture(session))
        self.assertNotEqual(before, after)


if __name__ == "__main__":
    unittest.main()
