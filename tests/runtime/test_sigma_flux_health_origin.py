"""Actual Flux health callbacks resolve their victim's authored force origin."""

import unittest
from unittest.mock import patch

import test_game_combat as fixtures
import test_sigma_flux_placement as placement

from ow174.game import defenses, sigma_flux
from ow174.game.collision import flat_world
from ow174.game.commands import Command
from ow174.game.content import heroes
from ow174.game.script import expr


class FrameRun(fixtures.Run):
    def step(self, commands):
        self.frame += 1
        self.match.update(self.frame * .016, self.frame)
        for player in self.players:
            player.client.queue_entities(self.match.remote_movements(player))
        for player, offset in zip(self.players, (8000, 6000), strict=True):
            yaw, pitch, buttons = commands.get(player, (0, 0, 0))
            player.take_commands([Command(self.frame + offset, yaw=yaw, pitch=pitch, buttons=buttons)])


class SigmaFluxHealthOriginTests(unittest.TestCase):
    def arena(self, victim_hero=0x02E000000000023B):
        run = FrameRun(names=("Sigma", "Victim"))
        source, victim = run.players
        for player, offset in zip(run.players, (8000, 6000), strict=True):
            player.commands.reset(last_frame=run.frame + offset)
            hero = 0x02E000000000023B if player is source else victim_hero
            run.match.switch_hero(player, heroes()[hero], start_frame=run.frame + offset)
            player.body_script.create_arrived = True
        run.match.collision_world = flat_world(0)
        for player, position in ((source, (1000, 0, 1000)), (victim, (1000, 0, 1006))):
            player.mover.position = list(position)
            player.mover.body = None
            player.mover.use(world=run.match.collision_world)
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is victim)
        health = fight.vitals_of(target)
        source.body_script.set_ult_charge(source.body_script.component.entity_var(338).value())
        return run, source, victim, health

    def finish(self, run, source, victim, health):
        placement.SigmaFluxPlacementTests().select(run, source, tuple(victim.mover.position))
        for _ in range(550):
            run.step({})
            if health.total == 150 and not victim.mover.airborne:
                break

    def test_actual_Q_health_origins_follow_victim_body_not_caster_or_owner_frame(self):
        run, source, victim, health = self.arena()
        seen = []
        original = sigma_flux.flux_modify_health

        def health_callback(world, state, elapsed):
            if state.instance.graph.index == 0x1DBC and state.node.pos in (12, 14) and not elapsed:
                cfg = state.node.field("m_modifyHealth")["m_C0139FA6"]
                value = state.instance.evaluate(cfg)
                seen.append((state.node.pos, value, tuple(victim.mover.position),
                             state.instance.component.frame, run.match.tick))
            return original(world, state, elapsed)

        with patch.object(sigma_flux, "flux_modify_health", health_callback):
            self.finish(run, source, victim, health)
        self.assertEqual(health.total, 150)
        self.assertEqual([row[0] for row in seen], [12, 14])
        for _, value, position, owner_frame, world_tick in seen:
            self.assertIsInstance(value, expr.Vec3)
            self.assertEqual(tuple(value), position)
            self.assertNotEqual(tuple(value), tuple(source.mover.position))
            self.assertEqual(owner_frame - world_tick, 6000)
        self.assertEqual(len(run.match.bots), 15)

    def test_actual_Q_submits_entry_health_force_once_in_world_time(self):
        run, source, victim, health = self.arena()
        host = run.match.combat.knockbacks
        original = host.apply
        forces = []

        def apply(target, impulse, **kwargs):
            accepted = original(target, impulse, **kwargs)
            forces.append((target.entity, tuple(impulse), kwargs, run.match.tick, accepted))
            return accepted

        with patch.object(host, "apply", apply):
            self.finish(run, source, victim, health)
        self.assertEqual(health.total, 150)
        self.assertEqual(len(forces), 1)
        body, impulse, kwargs, world_tick, accepted = forces[0]
        self.assertEqual(body, victim.body)
        # Original D6D6C6..D6DB01, own-body origin and this upright capsule:
        # horizontal -1, vertical 1, flat radius15 ring -> (0,1,0) before cap.
        self.assertEqual(impulse, (0.0, 1.0, 0.0))
        self.assertEqual(kwargs, {"impact_tick": world_tick, "source_entity": source.body})
        self.assertTrue(accepted)
        for _ in range(50):
            run.step({})
        self.assertFalse(victim.mover.airborne)
        self.assertFalse(sigma_flux.has_effect(victim))
        self.assertFalse(victim.mover.external_impulse)
        self.assertEqual(len(run.match.bots), 15)

    def test_actual_Q_entry_force_applies_live_Reinhardt_response_once(self):
        run, source, victim, health = self.arena(victim_hero=0x02E0000000000007)
        for _ in range(30):
            run.step({})
        self.assertAlmostEqual(defenses.knockback_scale(victim), 0.7)
        initial = health.total
        host = run.match.combat.knockbacks
        original = host.apply
        forces = []

        def apply(target, impulse, **kwargs):
            accepted = original(target, impulse, **kwargs)
            forces.append((target.entity, tuple(impulse), defenses.knockback_scale(target.owner), accepted))
            return accepted

        with patch.object(host, "apply", apply):
            placement.SigmaFluxPlacementTests().select(run, source, tuple(victim.mover.position))
            for _ in range(150):
                run.step({})
                if health.total < initial:
                    break
        self.assertEqual(initial - health.total, 45)  # Original entry50 against Reinhardt armour.
        self.assertEqual(len(forces), 1)
        body, impulse, response, accepted = forces[0]
        self.assertEqual(body, victim.body)
        self.assertAlmostEqual(response, 0.7)
        self.assertEqual(impulse[0], 0.0)
        self.assertEqual(impulse[2], 0.0)
        self.assertAlmostEqual(impulse[1], 0.7, places=6)
        self.assertTrue(accepted)
        self.assertEqual(len(run.match.bots), 15)
