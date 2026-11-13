"""Actual Helix and moving recipient commands discriminate delayed collision time."""

import json
import unittest
from pathlib import Path
from types import SimpleNamespace

from ow174.game import combat, soldier_projectile_history
from ow174.game.collision import flat_world
from ow174.game.commands import Command
from ow174.game.content import PRACTICE_RANGE, SOLDIER
from tests.runtime.test_game_combat import Run, aim

CASE = json.loads((Path(__file__).resolve().parents[2]
                   / 'fixtures/behavior/soldier-helix-moving-target-v1.json').read_text())


class SoldierHelixMovingTargetTests(unittest.TestCase):
    def replay(self, packet_size, separate_epoch):
        data = CASE['input']
        run = Run(PRACTICE_RANGE, ('Soldier', 'Enemy'))
        shooter, victim = run.players
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        run.match.collision_world = flat_world(0)
        for player, position in ((shooter, data['shooter_position']), (victim, data['target_position'])):
            player.mover.position[:] = position
            player.mover.body = None
            player.body_script.create_arrived = True
        for _ in range(data['prewarm_frames']):
            run.step({})
        if separate_epoch:
            birth = data['separate_owner_start']
            run.match.switch_hero(shooter, shooter.hero, preserve_pose=True, start_frame=birth)
            shooter.commands.reset(last_frame=birth)
            shooter.body_script.create_arrived = True
            for offset in range(1, data['prewarm_frames'] + 1):
                run.frame += 1
                run.match.update(run.frame * combat.TICK, run.frame)
                shooter.take_commands([Command(birth + offset)])
                victim.take_commands([Command(run.frame)])
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is victim)
        health = fight.vitals_of(target).total
        serial = fight.knockbacks.serial
        angles = aim(fight._eye(shooter), data['aim_point'])
        world_start, owner_start = run.frame, shooter.commands.last_frame
        pending = []
        for offset in range(1, data['sample_frames'] + 1):
            tick = world_start + offset
            run.match.update(tick * combat.TICK, tick)
            victim.take_commands([Command(tick, right=data['target_right'])])
            pending.append(Command(owner_start + offset, yaw=angles[0], pitch=angles[1],
                                   buttons=data['press_button'] if offset == 1 else 0))
            if offset % packet_size == 0:
                shooter.take_commands(pending)
                pending = []
        self.assertEqual((fight.tick, fight.now), (tick, tick * combat.TICK))
        hits = shooter.client.messages(combat.HIT)
        return {'damage': health - fight.vitals_of(target).total, 'hits': hits,
                'live': len(fight.rockets), 'force_events': fight.knockbacks.serial - serial,
                'target': victim.body, 'owner_start': owner_start, 'world_start': world_start}

    def test_packet_delay_preserves_helix_damage_full_hit_labels_and_single_force(self):
        expected = CASE['expected']
        for separate_epoch in (False, True):
            with self.subTest(separate_epoch=separate_epoch):
                direct, delayed = [self.replay(size, separate_epoch)
                                   for size in CASE['input']['source_packet_sizes']]
                self.assertEqual(direct, delayed)
                self.assertEqual(direct['damage'], expected['damage'])
                self.assertEqual(direct['live'], expected['live_projectiles'])
                self.assertEqual(direct['force_events'], expected['force_events'])
                self.assertEqual([h['+0x78']['+0x4'] for h in direct['hits']], expected['hit_amounts'])
                for hit in direct['hits']:
                    self.assertEqual(hit['+0x78']['+0x0']['+0x0'], direct['target'])
                    self.assertEqual(hit['+0x78']['+0x8'], 0)
                    # Preserve the existing Helix world-labelled HIT policy.
                    # Separate-owner notice clock qualification remains open.
                    self.assertEqual(hit['+0x84'], direct['world_start'] + expected['impact_offset'])

    def test_unqualified_projectiles_keep_current_targets_and_construct_is_preserved(self):
        shot = SimpleNamespace(body=11)
        values = {'source_graph': 0x0254, 'definition': 0x040000000000072C,
                  'shooter': SimpleNamespace(hero=SimpleNamespace(guid=SOLDIER)),
                  'shot': shot, 'source_entity': 11, 'launch_tick': 100, 'last_tick': 101}
        original = combat.Target(22, 1, (0, 0, 5), 2, .3, 1.5, None, construct=object())
        old = combat.Target(22, 1, (1, 0, 5), 2, .3, 1.5, None)
        fight = SimpleNamespace(tick=101, match=SimpleNamespace(tick=132), seen=lambda target, tick: old)
        shown, = soldier_projectile_history.targets(fight, SimpleNamespace(**values), [original])
        self.assertEqual(shown.feet, old.feet)
        self.assertIs(shown.construct, original.construct)
        for change in ({'definition': 1}, {'source_graph': 0x0257}, {'shot': None},
                       {'source_entity': 33}, {'launch_tick': None}, {'last_tick': None}):
            with self.subTest(change=change):
                candidates = [original]
                rocket = SimpleNamespace(**(values | change))
                self.assertIs(soldier_projectile_history.targets(fight, rocket, candidates), candidates)
                self.assertFalse(soldier_projectile_history.advance(fight, rocket))
