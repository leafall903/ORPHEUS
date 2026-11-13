"""Actual Sigma inputs submit each authored impulse once to the canonical host."""

import json
import unittest
from pathlib import Path
from unittest.mock import patch

import test_game_combat as fixtures
import test_sigma_combat as sigma_fixtures
import test_sigma_flux_placement as flux_fixtures

from ow174.game import combat, correction, sigma_flux
from ow174.game.collision import flat_world
from ow174.game.commands import Command
from ow174.game.content import heroes

DATA = json.loads((Path(__file__).resolve().parents[2] / "fixtures" / "behavior"
                   / "sigma-force-producers-v1.json").read_text())


class FrameRun(fixtures.Run):
    def step(self, commands):
        self.frame += 1
        self.match.update(self.frame * .016, self.frame)
        for player in self.players:
            player.client.queue_entities(self.match.remote_movements(player))
        for player, offset in zip(self.players, self.offsets, strict=True):
            yaw, pitch, buttons = commands.get(player, (0, 0, 0))
            player.take_commands([Command(self.frame + offset, yaw=yaw, pitch=pitch, buttons=buttons)])


class SigmaForceProducerTests(unittest.TestCase):
    def scenario(self, offsets=(0, 0)):
        run = FrameRun(names=("Sigma", "Target"))
        run.offsets = offsets
        source, victim = run.players
        for player, offset in zip(run.players, offsets, strict=True):
            player.commands.reset(last_frame=run.frame + offset)
            run.match.switch_hero(player, heroes()[0x02E000000000023B], start_frame=run.frame + offset)
            player.body_script.create_arrived = True
        run.match.collision_world = flat_world(0)
        for player, position in ((source, (1000, 0, 1000)), (victim, (1000, 0, 1006))):
            player.mover.position = list(position)
            player.mover.body = None
            player.mover.use(world=run.match.collision_world)
        target = next(t for t in run.match.combat._targets() if t.owner is victim)
        return run, source, victim, run.match.combat.vitals_of(target)

    def capture(self, fight, events):
        original = fight.knockbacks.apply

        def apply(target, impulse, **kwargs):
            accepted = original(target, impulse, **kwargs)
            events.append((target.entity, tuple(impulse), kwargs, fight.tick, accepted))
            return accepted

        return patch.object(fight.knockbacks, "apply", apply)

    def test_actual_E_submits_one_direct_force_with_world_impact_and_source(self):
        run, source, victim, health = self.scenario(offsets=(8000, 6000))
        fight = run.match.combat
        events = []
        initial = health.total
        with self.capture(fight, events):
            run.step({source: (0, 0, 0x10)})
            for _ in range(100):
                run.step({})
                if health.total < initial:
                    break
        self.assertEqual(initial - health.total, DATA["cases"]["E"]["damage"])
        self.assertEqual(len(events), 1)
        entity, impulse, kwargs, tick, accepted = events[0]
        self.assertEqual(entity, victim.body)
        self.assertEqual(kwargs, {"impact_tick": tick, "source_entity": source.body})
        self.assertNotEqual(tick, source.body_script.frame)
        self.assertTrue(accepted)
        self.assertAlmostEqual(impulse[2], 6)
        self.assertIsNotNone(victim.impulse_correction)
        for _ in range(170):
            run.step({})
        self.assertFalse(victim.mover.airborne)
        self.assertFalse(fight.is_stunned(victim))
        self.assertFalse(victim.mover.external_impulse)
        self.assertEqual(len(run.match.bots), 15)

    def test_actual_flux_preserves_velocity_rows_and_requests_motion_without_second_force(self):
        run, source, victim, health = self.scenario()
        source.body_script.set_ult_charge(source.body_script.component.entity_var(338).value())
        changed = []
        accepted_requests = []
        original = sigma_flux.flux_velocity
        original_request = correction.request_motion

        def request(*args, **kwargs):
            accepted = original_request(*args, **kwargs)
            accepted_requests.append(accepted)
            return accepted

        def velocity(instance, node):
            player = source if instance.component.entity == source.body else victim
            before = player.mover.velocity
            result = original(instance, node)
            if player.mover.velocity != before:
                changed.append((instance.graph.index, node.pos, instance.component.entity))
            return result

        with (patch.object(sigma_flux, "flux_velocity", velocity),
              patch.object(correction, "request_motion", side_effect=request) as motion):
            flux_fixtures.SigmaFluxPlacementTests().select(run, source, tuple(victim.mover.position))
            for _ in range(550):
                run.step({})
                if health.total == 150 and not victim.mover.airborne:
                    break
        self.assertEqual(health.total, 150)  # one50 entry and one200 landing hit
        self.assertFalse(victim.mover.airborne)
        self.assertIn((0x1E41, 18, source.body), changed)
        self.assertIn((0x1DBC, 38, victim.body), changed)
        self.assertIn((0x1DBC, 47, victim.body), changed)
        self.assertEqual(motion.call_count, len(changed))
        self.assertGreater(motion.call_count, 0)
        self.assertTrue(all(accepted_requests))
        for call in motion.call_args_list:
            self.assertIn(call.kwargs["body"], (source.body, victim.body))
        for _ in range(50):
            run.step({})
        self.assertFalse(sigma_flux.has_effect(victim))
        self.assertFalse(victim.mover.external_impulse)
        before_z = victim.mover.position[2]
        run.frame += 1
        run.match.update(run.frame * .016, run.frame)
        source.take_commands([Command(run.frame)])
        victim.take_commands([Command(run.frame, forward=127)])
        self.assertGreater(victim.mover.position[2], before_z)
        self.assertEqual(len(run.match.bots), 15)

    def test_actual_pair_submits_two_blast_forces_without_duplicate_direct_force(self):
        run, source, victim, health = self.scenario()
        fight = run.match.combat
        events = []
        initial = health.total
        with self.capture(fight, events):
            run.step({source: (0, 0, 1)})
            for _ in range(65):
                run.step({})
        self.assertEqual(initial - health.total, DATA["cases"]["pair"]["damage"])
        self.assertEqual(len(events), 2)
        for entity, _impulse, kwargs, tick, accepted in events:
            self.assertEqual(entity, victim.body)
            self.assertEqual(kwargs, {"impact_tick": tick, "source_entity": source.body})
            self.assertTrue(accepted)
        self.assertEqual(len(run.match.bots), 15)

    def test_actual_wall_E_self_push_uses_same_host_without_stun_or_hit_credit(self):
        run, source, _victim, _health = self.scenario()
        sigma_fixtures.SigmaCombatTests().wall(run, sideways=False, offset=1.25)
        fight = run.match.combat
        own = fight.vitals_of(next(t for t in fight._targets() if t.owner is source))
        initial = own.total
        events = []
        with self.capture(fight, events):
            run.step({source: (0, 0, 0x10)})
            for _ in range(95):
                run.step({})
        self.assertEqual(initial - own.total, DATA["cases"]["self_E"]["self_damage"])
        self.assertEqual(len(events), 1)
        entity, impulse, kwargs, tick, accepted = events[0]
        self.assertEqual(entity, source.body)
        self.assertLess(impulse[2], 0)
        self.assertEqual(kwargs, {"impact_tick": tick, "source_entity": source.body})
        self.assertTrue(accepted)
        self.assertFalse(fight.is_stunned(source))
        self.assertFalse(source.client.messages(combat.HIT))
