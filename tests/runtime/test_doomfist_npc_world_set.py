"""Actual prepared NPC contact keeps the existing manual/navigation body and clock."""

import json
import os
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch

from test_doomfist_combat import DoomfistFixture
from test_game_combat import Run

from ow174.game import combat, doomfist, doomfist_helper_velocity, mover, npc, velocity
from ow174.game.collision import flat_world
from ow174.game.content import SOLDIER, heroes
from ow174.game.script import expr, graph, play, play_queue


class PunchNpcWorldSetTests(unittest.TestCase):
    def case(self, caller_r, *, ai=False, retire=False, staged=False):
        spec = json.loads((Path(__file__).resolve().parents[2] / 'fixtures/behavior'
                           / 'doomfist-npc-world-set-v1.json').read_text())
        run = Run(names=('Doomfist', 'Observer'))
        source, observer = run.players
        match, fight = run.match, run.match.combat
        match.collision_world = flat_world(0)
        match.switch_hero(source, heroes()[doomfist.HERO])
        DoomfistFixture.place_player(source, (1000, 0, 1000))
        DoomfistFixture.place_player(observer, (1100, 0, 1100))
        catalog = npc.load_catalog(Path(os.environ['ORPHEUS_RUNTIME_DIR']) / 'data/pve_npcs_174.json')
        definition = next(d for d in catalog.values() if d.name == 'Trooper')
        bot = npc.spawn(match, definition, (1000, 0, 1012 if ai else 1003),
                        team=1-source.team, autonomous=ai)
        bot.route, bot.firing = (), ai
        for _ in range(30 if ai else 3):
            run.step({})
        if not ai:
            bot.ensure_manual_mover(match.tick, match.collision_world, mover.MOVER)
            run.step({})
        original_body = bot.impulse_mover or bot.navigation.mover
        script = bot.body_script
        self.assertEqual(bot.body, int(spec['body'], 16))
        identities = tuple(b.entity for b in match.bots)
        target = next(t for t in fight._targets() if t.owner is bot)
        before, serial = fight.vitals_of(target).total, fight.knockbacks.serial
        source.body_script.component.set(10959, caller_r)
        actual_damage, actual_apply = fight._damage, fight.knockbacks.apply
        actual_consume, actual_pose = velocity.consume, mover.Mover.step_pose
        queued, consumed, poses, damage_count = [], [], [], []
        leases, effects = [], []
        contact_health = []

        def damage(*args, **kwargs):
            if staged and args[1].entity == bot.entity and args[4] == doomfist.PUNCH:
                original_body.velocity = tuple(spec['moving_velocity'])
                bot.velocity = original_body.velocity
                instance = next(i for i in source.body_script.component.instances.values()
                                if i.graph.index == 0xD06)
                action = instance.graph.node(16)
                evaluate = instance.evaluate

                def target(config, *values, **options):
                    if config is action.field('m_targets'):
                        return (expr.Entity(bot.entity),)
                    return evaluate(config, *values, **options)

                with patch.object(instance, 'evaluate', side_effect=target):
                    request = play.resolve_request(instance, action, play.resolve_resource(instance, action),
                        context_provider=lambda: play.InvocationContext(source.body, source.body,
                                                                        instance.source_entity()))
                component = script.component
                ids = set(component.instances)
                queue = play_queue.Queue(component, lambda guid: play_queue.Load('ready', graph.graph(0xD07))
                                         if guid == request.resource.guid else None)
                queue.submit(request)
                helper, = [i for key, i in component.instances.items() if key not in ids]
                bridge = doomfist_helper_velocity.StagedVelocityBridge(component.world, source, bot)
                original_velocity = component.world.velocity_action

                def apply_action(active, node):
                    value = bridge(active, node)
                    return original_velocity(active, node) if value is NotImplemented else value

                with patch.object(component.world, 'velocity_action', side_effect=apply_action):
                    component.run_now()
                leases.append((helper, bridge, queue))
                return False  # Staged service owns only action; legacy damage/force never run.
            result = actual_damage(*args, **kwargs)
            if args[1].entity == bot.entity and args[4] == doomfist.PUNCH and result:
                damage_count.append(args[2])
                contact_health.append(fight.vitals_of(args[1]).total)
                original_body.velocity = tuple(spec['moving_velocity'])
                bot.velocity = original_body.velocity
            return result

        def apply(selected, requested, **kwargs):
            result = actual_apply(selected, requested, **kwargs)
            if selected.owner is bot:
                queued.append((match.tick, tuple(requested), result, bot.impulse_mover))
            return result

        def consume(current, jump, changes, frame):
            result = actual_consume(current, jump, changes, frame)
            if bot.impulse_mover is not None and changes is bot.impulse_mover.velocity_changes:
                consumed.extend((frame, current, row, result) for row in changes if row.frame == frame)
            return result

        def pose(body, command, *args, **kwargs):
            if body is original_body:
                poses.append(command.frame)
            result = actual_pose(body, command, *args, **kwargs)
            if body is original_body and staged and leases:
                helper = leases[0][0]
                rows = [m for k, m in body.mods.items() if k[0] == helper.id and k[1] == 23]
                effects.append((command.frame, any(m.acts(command.frame) for m in rows),
                                sum(k[0] == -3 for k in body.mods)))
            return result

        with patch.object(fight, '_damage', side_effect=damage), \
                patch.object(fight.knockbacks, 'apply', side_effect=apply), \
                patch.object(velocity, 'consume', side_effect=consume), \
                patch.object(mover.Mover, 'step_pose', new=pose):
            for _ in range(18):
                run.step({source: (0, 0, 2)})
            run.step({})
            for _ in range(60):
                if queued:
                    break
                run.step({})
            self.assertEqual(len(queued), 1)
            impact, requested, admitted, selected_body = queued[0]
            self.assertTrue(admitted)
            self.assertIs(selected_body, original_body)
            self.assertEqual(script.component.frame, impact)
            timed = {(m.index, m.start, m.end, m.priority)
                     for k, m in original_body.mods.items() if k[0] == -3}
            self.assertEqual(timed, set() if staged else
                             {(4, impact + 1, impact + 21, 0), (7, impact + 1, impact + 21, 1)})
            if retire:
                self.assertTrue(original_body.velocity_changes)
                removed = npc.remove_spawned(match, entities=(bot.entity,))
                self.assertEqual(removed, (bot.entity,))
                self.assertFalse(original_body.velocity_changes)
                self.assertIsNone(script.component.world.match)
                count = len(poses)
                for _ in range(3):
                    run.step({})
                self.assertEqual(len(poses), count)
                self.assertNotIn(bot.entity, fight.knockbacks.active)
            else:
                for _ in range(60):
                    run.step({})
                self.assertEqual(len(consumed), 1)
                frame, current, row, result = consumed[0]
                self.assertEqual(frame, impact + spec['frame_offset'])
                self.assertEqual((row.mode, row.op, row.priority), (0, 2, 1))
                self.assertEqual(row.value, requested)
                self.assertEqual(result, requested)
                self.assertGreater(current[0], 0)
                if caller_r == 0:
                    self.assertEqual(result, (0., 0., 0.))
                self.assertTrue(all(n == 1 for n in Counter(poses).values()))
                if staged:
                    self.assertEqual([at for at, active, _ in effects if active],
                                     list(range(impact + 1, impact + 21)))
                    self.assertTrue(all(n == 0 for _, _, n in effects))
                if ai:
                    self.assertIs(bot.navigation.mover, original_body)
                self.assertIs(bot.body_script, script)
                self.assertIsNone(script.component.fault)
                self.assertEqual(tuple(b.entity for b in match.bots), identities)
                self.assertAlmostEqual(before-fight.vitals_of(target).total,
                                       0 if staged else damage_count[0], places=5)
        self.assertEqual(len(damage_count), 0 if staged else 1)
        self.assertEqual(len(source.client.messages(combat.HIT)), 0 if staged else 1)
        self.assertEqual(fight.knockbacks.serial-serial, 1)
        for helper, bridge, queue in leases:
            script.component.destroy(helper)
            bridge.close()
            queue.retire()
        return {'callerR': caller_r, 'autonomous': ai, 'staged': staged, 'retired': retire,
                'worldImpact': impact, 'sourceBody': source.body, 'recipient': bot.entity,
                'bodyModel': hex(bot.body), 'samePhysicalMover': selected_body is original_body,
                'serialDelta': fight.knockbacks.serial-serial,
                'damageDelta': before-contact_health[0] if contact_health else 0,
                'HITs': len(source.client.messages(combat.HIT)),
                'consumed': [{'frame': at, 'mode': row.mode, 'operation': row.op, 'priority': row.priority,
                              'before': current, 'requested': row.value, 'after': result}
                             for at, current, row, result in consumed],
                'maximumPosesPerFrame': max(Counter(poses).values(), default=0),
                'staged48ActiveFrames': [at for at, active, _ in effects if active],
                'hostFiniteRowsAtAdmission': sorted(timed)}

    def test_manual_and_autonomous_contact_consume_world_set_on_the_same_physical_body(self):
        for ai in (False, True):
            with self.subTest(ai=ai):
                self.case(1, ai=ai)

    def test_zero_is_a_real_npc_reset_with_one_pose_per_world_frame(self):
        for ai in (False, True):
            with self.subTest(ai=ai):
                self.case(0, ai=ai)

    def test_body_retirement_cancels_pending_action_on_manual_and_navigation_movers(self):
        for ai in (False, True):
            with self.subTest(ai=ai):
                self.case(0, ai=ai, retire=True)

    def test_staged_original58_and47_48_share_npc_consumption_clock_without_host_finite_brakes(self):
        for ai, caller_r in ((False, 1), (False, 0), (True, 1), (True, 0)):
            with self.subTest(ai=ai, caller_r=caller_r):
                self.case(caller_r, ai=ai, staged=True)

    def test_foreign_caller_slots_remain_read_only_and_retired_bodies_do_not_alias(self):
        f = DoomfistFixture()
        f.prepare()
        catalog = npc.load_catalog(Path(os.environ['ORPHEUS_RUNTIME_DIR']) / 'data/pve_npcs_174.json')
        definition = next(d for d in catalog.values() if d.name == 'Trooper')
        bot = npc.spawn(f.match, definition, tuple(f.b.mover.position), team=1-f.a.team)
        f.run.step({})
        world = bot.body_script.component.world
        source = f.a.body_script.component
        source.set(10959, 0)
        bot.body_script.component.set(10959, 99)
        self.assertEqual(world.entity_var(f.a.body, 10959), 0)
        slots = dict(source.vars)
        self.assertIsNone(world.entity_var(f.a.body, 0x7FFF1234))
        self.assertEqual(source.vars, slots)
        self.assertIs(world.entity_var_slot(f.a.body, 10959), NotImplemented)
        source.set(10959, 2)
        self.assertEqual(world.entity_var(f.a.body, 10959), 2)
        body = f.a.body
        f.match.switch_hero(f.a, heroes()[SOLDIER])
        self.assertIsNone(world.entity_var(body, 10959))
        self.assertFalse(f.a.client.messages(combat.HIT))
        self.assertEqual(f.fight.knockbacks.serial, 0)
