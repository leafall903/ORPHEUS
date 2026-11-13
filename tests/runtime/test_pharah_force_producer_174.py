"""Actual Pharah commands feed the one provisional common world-time force API."""
import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from ow174.game import combat, correction, defenses, npc, npc_console, pharah
from ow174.game.collision import flat_world
from ow174.game.commands import Command
from ow174.game.content import heroes
from tests.runtime.test_game_combat import KANEZAKA_DM, Run

FIXTURES = Path(os.environ.get('ORPHEUS_BEHAVIOR_FIXTURES',
                              Path(__file__).resolve().parents[2] / 'fixtures/behavior'))
CONTRACT = json.loads((FIXTURES / 'pharah-force-producer-174-v1.json').read_text(encoding='utf-8'))


class PharahForceProducer174Tests(unittest.TestCase):
    def scene(self, *, distance=3):
        run = Run(KANEZAKA_DM, ('Pharah', 'Enemy'))
        source, enemy = run.players
        source.commands.last_frame = CONTRACT['input']['owner_birth']
        self.assertTrue(run.match.switch_hero(source, heroes()[pharah.HERO]))
        self.assertEqual(source.body_script.world_frame_anchor, (1000, 9000))
        run.match.collision_world = flat_world(0)
        for owner, pos in ((source, (1000, 0, 1000)), (enemy, (1000, 0, 1000 + distance))):
            owner.mover.position = list(pos)
            owner.mover.body = None
            owner.body_script.create_arrived = True
        for _ in range(CONTRACT['input']['warmup_ticks']):
            self.step(run)
        return run, source, enemy

    def step(self, run, buttons=0, enemy_buttons=0):
        run.frame += 1
        run.match.update(run.frame * combat.TICK, run.frame)
        offset = run.frame - CONTRACT['input']['world_birth']
        run.players[0].take_commands([Command(CONTRACT['input']['owner_birth'] + offset, buttons=buttons)])
        run.players[1].take_commands([Command(run.frame, buttons=enemy_buttons)])

    @staticmethod
    def health(run, owner):
        target = next(t for t in run.match.combat._targets() if t.owner is owner)
        return run.match.combat.vitals_of(target).total

    def fire(self, run, source, button, ticks=50):
        fight = run.match.combat
        events = []
        original = fight.knockbacks.apply

        def apply(target, impulse, **kwargs):
            accepted = original(target, impulse, **kwargs)
            events.append({'target': target.entity, 'impulse': tuple(impulse), 'accepted': accepted,
                           **kwargs})
            return accepted

        with patch.object(fight.knockbacks, 'apply', side_effect=apply):
            self.step(run, button)
            for _ in range(ticks):
                self.step(run)
        self.observation = {'events': events, 'source': source.body, 'worldEnd': run.frame,
                            'ownerEnd': source.body_script.frame}
        return events

    def assert_event(self, events, owner, vector, source):
        accepted = [e for e in events if e['accepted'] and e['target'] == owner.body]
        self.assertEqual(len(accepted), CONTRACT['expected']['force_events_per_recipient'])
        event, = accepted
        self.assertNotIn('impact_frame', event)
        self.assertTrue(1000 <= event['impact_tick'] < 2000)
        self.assertEqual(event['source_entity'], source.body)
        for actual, expected in zip(event['impulse'], vector, strict=True):
            self.assertAlmostEqual(actual, expected, places=5)

    def test_primary_damage_and_force_each_occur_once(self):
        run, source, enemy = self.scene()
        events = self.fire(run, source, 1)
        self.assertEqual(self.health(run, enemy), CONTRACT['expected']['primary_enemy_health'])
        hits = source.client.messages(combat.HIT)
        self.assertEqual([h['+0x78']['+0x4'] for h in hits], CONTRACT['expected']['primary_hit_damage'])
        self.assertEqual([h['+0x84'] for h in hits], CONTRACT['expected']['primary_existing_hit_frames'])
        self.assert_event(events, enemy, CONTRACT['expected']['primary_enemy_force'], source)
        self.assertEqual(len([e for e in events if e['accepted'] and e['target'] == source.body]), 1)
        self.assertFalse(run.match.combat.rockets)
        self.observation.update(enemyHealth=self.health(run, enemy),
                                hitDamage=[h['+0x78']['+0x4'] for h in hits],
                                existingHitFrames=[h['+0x84'] for h in hits])

    def test_concussive_zero_damage_self_enemy_force_then_landing_returns_control(self):
        run, source, enemy = self.scene()
        events = self.fire(run, source, 16)
        self.assertEqual(self.health(run, enemy), 200)
        self.assertEqual(self.health(run, source), 200)
        self.assertFalse(source.client.messages(combat.HIT))
        self.assert_event(events, enemy, CONTRACT['expected']['concussive_enemy_force'], source)
        self.assert_event(events, source, CONTRACT['expected']['concussive_self_force'], source)
        for _ in range(220):
            self.step(run)
        for actor in (source, enemy):
            self.assertFalse(actor.mover.airborne)
            self.assertFalse(actor.mover.external_impulse)
            self.assertFalse(actor.mover.velocity_changes)
            self.assertNotIn(actor.body, run.match.combat.knockbacks.active)
        self.assertFalse(correction.correcting(source))
        self.observation.update(enemyHealth=self.health(run, enemy), selfHealth=self.health(run, source),
                                landed=True, externalMotionRetired=True, ownerCorrectionExpired=True)

    def test_barrage_retains_damage_without_invented_character_force(self):
        run, source, enemy = self.scene(distance=8)
        source.body_script.set_ult_charge(source.body_script.component.entity_var(338).value())
        events = self.fire(run, source, 32, ticks=80)
        self.assertLess(self.health(run, enemy), 200)
        self.assertEqual(len([e for e in events if e['accepted']]), 0)
        self.observation.update(enemyHealth=self.health(run, enemy), characterForceEvents=0)

    def test_actual_heavy_zero_response_is_not_bypassed_by_common_api(self):
        run, source, _enemy = self.scene(distance=30)
        definition = next(d for d in npc_console.configured_catalog().values() if d.name == 'Heavy Assault')
        heavy = npc.spawn(run.match, definition, (1000, 0, 1003), team=1)
        for _ in range(3):
            self.step(run)
        self.assertEqual(defenses.knockback_scale(heavy), CONTRACT['expected']['heavy_response26'])
        before = tuple(heavy.position)
        health = self.health(run, heavy)
        events = self.fire(run, source, 16)
        self.assertEqual(tuple(heavy.position), before)
        self.assertEqual(self.health(run, heavy), health)
        self.assertFalse(any(e['accepted'] and e['target'] == heavy.entity for e in events))
        self.observation.update(heavyEntity=heavy.entity, response26=0, before=before,
                                after=heavy.position, healthBefore=health,
                                healthAfter=self.health(run, heavy))

    def test_actual_fortify_rejects_enemy_force_without_rejecting_self_force(self):
        run, source, enemy = self.scene()
        self.assertTrue(run.match.switch_hero(enemy, heroes()[defenses.ORISA]))
        enemy.mover.position = [1000, 0, 1003]
        enemy.mover.body = None
        for _ in range(3):
            self.step(run)
        self.step(run, enemy_buttons=8)
        self.assertIsNotNone(defenses.fortify_scale(enemy))
        before = tuple(enemy.mover.position)
        health = self.health(run, enemy)
        events = self.fire(run, source, 16)
        self.assertEqual(tuple(enemy.mover.position), before)
        self.assertEqual(self.health(run, enemy), health)
        self.assertFalse(any(e['accepted'] and e['target'] == enemy.body for e in events))
        self.assertEqual(len([e for e in events if e['accepted'] and e['target'] == source.body]), 1)
        self.observation.update(fortify=True, immuneTargetUnmoved=True, immuneTargetHealth=health)


if __name__ == '__main__':
    unittest.main()
