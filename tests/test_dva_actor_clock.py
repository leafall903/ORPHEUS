"""Real Q-created mech VM timing in separate creator/world domains; ownership remains open."""

import os
import unittest
from unittest.mock import patch

import test_dva_factory as factory

from ow174.game import dva, npc, world
from ow174.game.commands import FIRE, Command
from ow174.game.content import heroes
from ow174.game.match import Match
from ow174.game.script import runtime


@unittest.skipUnless(os.environ.get('ORPHEUS_DVA_RUNTIME'), 'requires actual mech metadata')
class DvaActorClockTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        factory.DvaFactoryTests.setUpClass()

    def spawn(self, *, source=1000, world_tick=1000, frame_ms=16):
        _, run, pilot, zen = factory.DvaFactoryTests().arena(cast=False)
        if frame_ms != 16:
            pilot.quanta = frame_ms*1000
            run.match.switch_hero(pilot, heroes()[dva.HERO])
        pilot.body_script.set_ult_charge(pilot.body_script.component.entity_var(338).value())
        clocks = [source, world_tick]

        def advance(*, gap=1, pilot_buttons=0, zen_buttons=0, angles=(0, 0)):
            clocks[0] += 1
            clocks[1] += gap
            run.match.update(clocks[1]*.016, clocks[1])
            for player in run.players:
                player.client.queue_entities(run.match.remote_movements(player))
            pilot.take_commands([Command(clocks[0], buttons=pilot_buttons)])
            zen.take_commands([Command(clocks[1], yaw=angles[0], pitch=angles[1], buttons=zen_buttons)])

        for index in range(100):
            advance(pilot_buttons=0x20 if index == 0 else 0)
            if run.match.bots:
                actor = run.match.bots[0]
                self.assertIsNotNone(actor.body_script)
                return run, pilot, zen, actor, advance
        self.fail('actual full-charge Q never created the mech')

    def wait(self, actor):
        instance = next(i for i in actor.body_script.component.instances.values() if i.graph.index == 0x01C7)
        state = next(s for s in instance.states.values() if s.node.pos == 143)
        event = next(e for e in instance.queue if e.state is state and e.kind == runtime.TIMER
                     and e.param == 1)
        self.assertTrue(state.active)
        self.assertEqual(event.time-state.start, 350)  # actual shared resource's0.35-second wait
        return state, event.time

    def test_real_q_mech_continuously_advances_and_finishes_actual_wait_at_its_original_time(self):
        run, _, _, actor, advance = self.spawn()
        state, deadline = self.wait(actor)
        before = actor.body_script.frame
        for _ in range(30):
            advance()
        self.assertGreater(actor.body_script.frame, before)
        self.assertEqual(actor.body_script.frame, run.match.tick)
        self.assertEqual(state.ended_at, deadline)

    def test_positive_negative_origins_and_seven_ms_vm_keep_creator_clock_and_real_duration(self):
        for source, world_tick, quantum in ((6000, 1000, 16), (1000, 6000, 16), (1000, 1000, 7)):
            with self.subTest(source=source, world=world_tick, quantum=quantum):
                run, pilot, _, actor, advance = self.spawn(
                    source=source, world_tick=world_tick, frame_ms=quantum)
                clock = actor.spawner['dva_clock']
                self.assertEqual(clock['frame'], pilot.body_script.component.frame)
                self.assertEqual(actor.body_script.frame, clock['frame'])
                self.assertEqual(actor.body_script.component.frame_ms, quantum)
                state, deadline = self.wait(actor)
                for _ in range(30):
                    advance()
                self.assertEqual(state.ended_at, deadline)
                self.assertEqual(actor.body_script.component.now,
                                 (dva.actor_frame(actor, run.match.combat.now, run.match.tick)+1)*quantum-1)

    def test_skipped_world_ticks_preserve_authored_deadline_without_fabricated_input(self):
        run, _, _, actor, advance = self.spawn(source=6000)
        state, deadline = self.wait(actor)
        advance(gap=50)
        self.assertEqual(state.ended_at, deadline)
        self.assertFalse(actor.body_script.component.held)
        self.assertFalse(run.match.combat.rockets)
        self.assertIsNone(actor.body_script.behavior)

    def test_required_script_failure_rolls_back_before_id_health_or_create_publication(self):
        helper, run, pilot, _ = factory.DvaFactoryTests().arena(cast=False)
        entity = run.match.combat.next_bot
        with patch.object(Match, '_prepare_bot_script', return_value=None):
            instance, _, _ = helper.cast(run, pilot, frames=20)
        self.assertIsNone(instance.vars[38].value())
        self.assertFalse(run.match.bots)
        self.assertNotIn(entity, run.match.combat.vitals)
        self.assertEqual(run.match.combat.next_bot, entity)
        self.assertFalse(any(update.entity == entity and update.op == world.OP_CREATE
                             for player in run.players for update in player.client.entities))

    def test_source_script_failure_retires_owned_mech_but_preserves_unrelated_manual_corpse(self):
        run, pilot, _, actor, _ = self.spawn()
        x, y, z = actor.position
        corpse = npc.spawn(run.match, npc.Definition(dva.HERO, dva.MECH, 'Manual corpse', 300, 300),
                           (x+20, y, z), team=1)
        corpse.died(run.match.tick)
        script = actor.body_script
        run.match.stop_body_script(pilot, failed=True)
        self.assertNotIn(actor, run.match.bots)
        self.assertIsNone(actor.body_script)
        self.assertFalse(script.component.instances)
        self.assertNotIn(actor.entity, run.match.combat.vitals)
        self.assertIn(corpse, run.match.bots)
        self.assertTrue(corpse.dead)

    def test_actual_zen_kill_maps_mech_death_and_postdeath_events_to_its_vm_clock(self):
        run, _, zen, actor, advance = self.spawn(world_tick=6000)
        for player in run.players:
            player.client.queue_bot_death = lambda *args: None
            player.client.queue_bot_death_update = lambda *args: None
        script = actor.body_script
        angles = factory.aim(run.match.combat._eye(zen),
                             (actor.position[0], actor.position[1]+1, actor.position[2]))
        for _ in range(600):
            advance(zen_buttons=FIRE, angles=angles)
            if actor.dead:
                break
        else:
            self.fail('actual held Zen primary did not kill the mech')
        vitals = run.match.combat.vitals[actor.entity]
        self.assertEqual(vitals.total, 0)
        self.assertIs(actor.body_script, script)
        self.assertEqual(script.death_frame, dva.actor_frame(actor, run.match.combat.now, run.match.tick))
        self.assertLess(script.death_frame, vitals.death_tick)
        for _ in range(80):
            advance()
        self.assertIs(actor.body_script, script)
        self.assertTrue(script.component.owner_dead)
        self.assertIn(actor, run.match.bots)
        self.assertLessEqual(script.frame, dva.actor_frame(actor, run.match.combat.now, run.match.tick))

    def test_projectile_birth_translation_uses_world_ticks_without_changing_activation_identity(self):
        for source, world_tick, quantum in ((6000, 1000, 16), (1000, 6000, 16), (1000, 1000, 7)):
            with self.subTest(source=source, world=world_tick, quantum=quantum):
                _, _, _, actor, _ = self.spawn(source=source, world_tick=world_tick, frame_ms=quantum)
                clock = actor.spawner['dva_clock']
                original = clock['frame']*quantum
                self.assertEqual(dva.actor_world_frame(actor, original, -1), clock['world_tick'])
                self.assertEqual(dva.actor_world_frame(actor, original+32, -1), clock['world_tick']+2)
                # Source activation timestamps stay in the actual component's domain.
                state = next(s for i in actor.body_script.component.instances.values()
                             for s in i.states.values()
                             if s.node.cls == 'STUStatescriptStateWait' and s.active)
                self.assertGreaterEqual(state.start, original)
