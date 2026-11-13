"""Real Winston skill triggers against the pinned canonical recipient API."""
import json
import unittest
from pathlib import Path
from unittest import mock

from test_game_combat import Run

from ow174.game import combat, correction, fortify, winston
from ow174.game.collision import flat_world
from ow174.game.commands import Command
from ow174.game.content import heroes
from ow174.game.script.driver import ULT_COST

OBSERVATIONS = []
CONTRACT = Path(__file__).resolve().parents[2] / 'fixtures/behavior/winston-knockback-r2-producers-v1.json'


class ActualSkills:
    def __init__(self, victim_hero=winston.HERO, *, aligned_fortify=False, recipient_birth=None):
        self.contract = json.loads(CONTRACT.read_text(encoding='utf-8'))['input']
        if recipient_birth is not None:
            self.contract['recipient_birth'] = recipient_birth
        if aligned_fortify:
            self.contract['recipient_birth'] = self.contract['fortify_recipient_birth']
        self.run = Run(names=('Winston', 'Recipient'))
        self.match = self.run.match
        self.source, self.victim = self.run.players
        for player, hero, birth in ((self.source, winston.HERO, self.contract['source_birth']),
                                    (self.victim, victim_hero, self.contract['recipient_birth'])):
            player.commands.last_frame = birth
            self.match.switch_hero(player, heroes()[hero])
            player.mover.body = None
            player.body_script.create_arrived = True
        self.match.collision_world = flat_world(0)
        self.source.mover.position[:] = (1000, 0, 1000)
        self.victim.mover.position[:] = (1000, 0, 1002)
        self.tick = 1000
        self.fight = self.match.combat
        self.npcs = tuple(bot.entity for bot in self.match.bots)

    def health(self):
        target = next(t for t in self.fight._targets() if t.owner is self.victim)
        return self.fight.vitals_of(target).total

    def step(self, buttons=0, *, pitch=0, victim_buttons=0, victim_forward=0):
        self.tick += 1
        self.match.update(self.tick * .016, self.tick)
        offset = self.tick - self.contract['world_birth']
        self.source.take_commands([
            Command(self.contract['source_birth'] + offset, buttons=buttons, pitch=pitch)])
        self.victim.take_commands([
            Command(self.contract['recipient_birth'] + offset,
                    buttons=victim_buttons, forward=victim_forward)])

    def rage(self):
        script = self.source.body_script
        script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        self.step(32)
        assert winston.primal_active(script.component)

    def observe(self, label, apply, before):
        calls = [c for c in apply.call_args_list if c.args[0].owner is self.victim]
        hits = self.source.client.messages(combat.HIT)
        record = correction.own_record(self.victim, self.tick)
        row = {'label': label, 'world_tick': self.tick, 'source_body': self.source.body,
               'victim_body': self.victim.body, 'health_before': before, 'health_after': self.health(),
               'force_calls': [{'impulse': tuple(c.args[1]), 'impact_tick': c.kwargs.get('impact_tick'),
                                'source_entity': c.kwargs.get('source_entity')} for c in calls],
               'hit_owner_frames': [hit['+0x84'] for hit in hits],
               'recipient_frame': self.victim.body_script.frame,
               'recipient_position': tuple(self.victim.mover.position),
               'own_record': record is not None,
               'record_frame': record.correction.frame if record is not None else None,
               'native_adoption': 'NOT TESTED',
               'npcs_retained': tuple(bot.entity for bot in self.match.bots) == self.npcs}
        OBSERVATIONS.append(row)
        return row


class WinstonR2ProducerTests(unittest.TestCase):
    def check_contact(self, row):
        self.assertLess(row['health_after'], row['health_before'])
        self.assertEqual(len(row['force_calls']), 1)
        self.assertEqual(len(row['hit_owner_frames']), 1)
        force, = row['force_calls']
        hit, = row['hit_owner_frames']
        self.assertEqual(force['impact_tick'], 1000 + hit - 9000)
        self.assertEqual(force['source_entity'], row['source_body'])
        self.assertTrue(row['own_record'])
        self.assertEqual(row['record_frame'], row['recipient_frame'])
        self.assertTrue(row['npcs_retained'])

    def test_primal_real_hit_uses_world_event_and_actual_source_once(self):
        case = ActualSkills()
        case.rage()
        before = case.health()
        with mock.patch.object(case.fight.knockbacks, 'apply', wraps=case.fight.knockbacks.apply) as apply:
            for _ in range(60):
                case.step(1)
                if case.health() < before:
                    break
            for _ in range(6):
                case.step()
            row = case.observe('primal', apply, before)
        self.check_contact(row)
        self.assertEqual(row['health_after'], before - 35)  # Original Winston armour.
        self.assertGreater(row['recipient_position'][2], 1002)

    def test_primal_landing_and_input_return_under_supported_packet_epoch(self):
        case = ActualSkills(recipient_birth=1000)
        case.rage()
        before = case.health()
        with mock.patch.object(case.fight.knockbacks, 'apply', wraps=case.fight.knockbacks.apply) as apply:
            for _ in range(60):
                case.step(1)
                if case.health() < before:
                    break
            for _ in range(6):
                case.step()
            row = case.observe('primal_landing_control', apply, before)
            # Check this skill's ordinary gravity/landing and release through
            # the existing finite host; no duplicate shared wire/ACK suite.
            for _ in range(correction.IMPULSE_WINDOW_US // 16000 + 12):
                case.step()
            row['landed_after_force'] = not case.victim.mover.airborne
            row['finite_correction_released'] = not correction.correcting(case.victim)
            position = case.victim.mover.position[2]
            for _ in range(5):
                case.step(victim_forward=127)
            row['normal_input_displacement_after_release'] = case.victim.mover.position[2] - position
            row['release_diagnostic'] = {
                'still_in_launches': case.victim.body in case.fight.knockbacks.launches,
                'velocity': tuple(case.victim.mover.velocity),
                'pending_velocity_changes': len(case.victim.mover.velocity_changes),
                'last_received_input': case.victim.commands.last_frame,
                'script_frame': case.victim.body_script.frame,
                'last_pose_frame': case.victim.mover.last_command.frame,
                'last_pose_forward': case.victim.mover.last_command.forward,
                'replayed_frame': case.fight.knockbacks.replayed_frames.get(
                    case.victim.body, (None, None))[1],
            }
        self.check_contact(row)
        self.assertTrue(row['landed_after_force'])
        self.assertTrue(row['finite_correction_released'])
        self.assertGreater(row['normal_input_displacement_after_release'], 0)

    def test_jump_real_takeoff_and_landing_each_dispatch_once(self):
        for landing in (False, True):
            with self.subTest(landing=landing):
                case = ActualSkills()
                if landing:
                    case.victim.mover.position[:] = (1000, 0, 1100)
                before = case.health()
                with mock.patch.object(case.fight.knockbacks, 'apply',
                                       wraps=case.fight.knockbacks.apply) as apply:
                    case.step(8, pitch=-16384)
                    if landing:
                        for _ in range(180):
                            x, _, z = case.source.mover.position
                            case.victim.mover.position[:] = (x, 0, z)
                            case.step()
                            if case.health() < before:
                                break
                    for _ in range(3):
                        case.step()
                    row = case.observe('landing' if landing else 'takeoff', apply, before)
                self.check_contact(row)

    def test_canonical_melee_dispatches_once_without_legacy_winston_fallback(self):
        case = ActualSkills()
        case.victim.mover.position[:] = (1000, 0, 1001.5)
        before = case.health()
        with (
            mock.patch.object(case.fight.knockbacks, 'apply', wraps=case.fight.knockbacks.apply) as apply,
            mock.patch.object(case.fight.winston_jump, 'knockback',
                              wraps=case.fight.winston_jump.knockback) as legacy,
        ):
            for _ in range(30):
                case.step(0x800)
                if case.health() < before:
                    break
            for _ in range(3):
                case.step()
            row = case.observe('quick_melee', apply, before)
            self.assertEqual(legacy.call_count, 0)
        self.check_contact(row)

    def test_actual_fortify_blocks_punch_force_without_swallowing_damage(self):
        case = ActualSkills(0x02E000000000013E, aligned_fortify=True)
        # Match the existing actual Fortify fixture's authored body-ready phase.
        for _ in range(25):
            case.step()
        case.step(victim_buttons=8)
        self.assertTrue(fortify.control_immune(case.victim))
        case.rage()
        before = case.health()
        with mock.patch.object(case.fight.knockbacks, 'apply', wraps=case.fight.knockbacks.apply) as apply:
            for _ in range(60):
                case.step(1)
                if case.health() < before:
                    break
            row = case.observe('fortify', apply, before)
        self.assertLess(row['health_after'], before)
        self.assertEqual(row['force_calls'], [])

    def test_tesla_and_barrier_have_no_force_producer(self):
        case = ActualSkills()
        before = case.health()
        with mock.patch.object(case.fight.knockbacks, 'apply', wraps=case.fight.knockbacks.apply) as apply:
            case.step(1)
            self.assertLess(case.health(), before)
            for _ in range(5):
                case.step(16)
            self.assertTrue(case.fight.barriers.world.actors)
            row = case.observe('tesla_barrier_no_force', apply, before)
        self.assertEqual(row['force_calls'], [])
