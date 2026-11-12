"""Actual1157 inputs against Lucio; manual contact/clock/control are reconstructed."""

import unittest
from unittest.mock import patch

import test_lucio_pve as pve
from test_game_combat import aim

from ow174.game import assassin, combat, npc, npc_control
from ow174.game.commands import FIRE, SECONDARY, Command
from ow174.game.content import heroes
from ow174.game.script import nodes


@unittest.skipUnless(pve.CATALOG and pve.BUNDLE, 'requires actual five-type NPC and Lucio status resources')
class LucioAssassinTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = pve.LucioPveTests()
        cls.fixture.setUpClass()
        cls.definition = next(d for d in cls.fixture.definitions.values() if d.hero == assassin.HERO)

    @classmethod
    def tearDownClass(cls):
        cls.fixture.tearDownClass()

    def scenario(self):
        helper, run, source, bot = self.fixture.scenario(self.definition, position=(0, 1, 2))
        source.body_script.ult_seconds = 0
        self.assertEqual(bot.body_script.component.world.ai_weapon_button(0), 36)
        return helper, run, source, bot

    def instance(self, bot):
        return next(i for i in bot.body_script.component.instances.values() if i.graph.index == 0x1157)

    def start(self, run, source, bot):
        point = (bot.position[0], bot.position[1]+.7, bot.position[2])
        yaw, pitch = aim(run.match.combat._eye(source), point)
        run.step({source:(yaw,pitch,0)})
        self.assertEqual(npc_control.start(run.match, source), bot.entity)
        run.step({})
        self.assertEqual(run.match.combat.pins.get(bot.entity), source.body)

    def advance(self, run, frames):
        for _ in range(frames):
            run.step({})

    def vitals(self, run, owner):
        fight = run.match.combat
        return fight.vitals_of(next(t for t in fight._targets() if t.owner is owner))

    def drive(self, run, source, bot, *, buttons=0, forward=0):
        run.frame += 1
        run.match.update(run.frame*combat.TICK, run.frame)
        for player in run.players:
            player.client.queue_entities(run.match.remote_movements(player))
            if player is source:
                yaw,pitch = aim(run.match.combat._eye(source),
                                (bot.position[0], bot.position[1]+.7, bot.position[2]))
                command = Command(run.frame, yaw=yaw, pitch=pitch, buttons=buttons, forward=forward)
            else:
                command = Command(run.frame)
            player.take_commands([command])

    def test_actual_contact_gap_then_continuous_rate_and_duplicate_frame(self):
        helper,run,source,bot = self.scenario()
        self.start(run,source,bot)
        self.assertEqual(helper.health(run,source), 175.)
        instance = self.instance(bot)
        self.assertEqual(instance.vars[536].value().id, source.body)
        self.assertEqual(instance.state(instance.graph.node(25).state).counter, 1)
        self.advance(run,30)
        self.assertEqual(helper.health(run,source),175.)
        self.assertTrue(run.match.combat.is_stunned(source))
        continuous = instance.state(instance.graph.node(15).state)
        for _ in range(100):
            run.step({})
            if continuous.active:
                break
        self.assertTrue(continuous.active)
        before = helper.health(run,source)
        self.advance(run,10)
        self.assertAlmostEqual(before-helper.health(run,source),16.,places=5)
        before = helper.health(run,source)
        self.assertIsNone(bot.body_script.advance_live(bot.body_script.frame))
        self.assertEqual(helper.health(run,source),before)

    def test_pin_blocks_inputs_resources_and_motion_then_wave_recovers(self):
        helper,run,source,bot = self.scenario()
        source.body_script.set_ult_charge(2940.)
        self.start(run,source,bot)
        position = tuple(source.mover.position)
        before = helper.health(run,bot)
        weapon = next(i for i in source.body_script.component.instances.values() if i.graph.index == 0x0776)
        for _ in range(20):
            self.drive(run,source,bot,buttons=FIRE|SECONDARY|8|16|32|0x800,forward=127)
            self.assertEqual((source.mover.position[0],source.mover.position[2]),(position[0],position[2]))
            self.assertFalse(source.body_script.component.held)
            self.assertEqual(weapon.vars[53].value(),20)
            self.assertEqual(source.body_script.component.entity_var(342).value(),2940.)
            self.assertEqual(helper.health(run,bot),before)
        npc_control.stop(run.match)
        self.assertFalse(run.match.combat.is_stunned(source))
        self.drive(run,source,bot,buttons=SECONDARY)
        self.assertEqual(helper.health(run,bot),before-25.)

    def test_manual_pin_release_does_not_replay_historical_input(self):
        _, run, source, bot = self.scenario()
        self.start(run, source, bot)
        fight = run.match.combat
        self.drive(run, source, bot, buttons=FIRE, forward=127)
        position = tuple(source.mover.position)
        deadline = run.frame + 12
        for frame in range(run.frame + 1, deadline + 1):
            run.match.update(frame * combat.TICK, frame)
        self.assertEqual(npc_control.stop(run.match), 1)
        self.assertIn(source.body, fight.stun_poses)
        source.take_commands([Command(deadline)])
        self.assertEqual(tuple(source.mover.position), position)
        self.assertFalse(fight.rockets)
        self.assertFalse(hasattr(fight, "stun_poses"))
        self.assertFalse(hasattr(fight, "pin_pose_starts"))
        self.assertFalse(fight.is_stunned(source))

    def test_manual_stop_no_late_damage_and_explicit_restart(self):
        helper,run,source,bot = self.scenario()
        self.start(run,source,bot)
        self.assertEqual(npc_control.stop(run.match),1)
        self.advance(run,100)
        self.assertEqual(helper.health(run,source),175.)
        self.assertFalse(run.match.combat.pins)
        self.start(run,source,bot)
        self.assertEqual(helper.health(run,source),150.)

    def test_raw_release_waits_for_clip_and_requires_fresh_press(self):
        helper, run, source, bot = self.scenario()
        self.start(run, source, bot)
        instance = self.instance(bot)
        component = bot.body_script.component
        button = component.world.ai_weapon_button(0)
        release = instance.state(instance.graph.node(31).state)
        contact = instance.state(instance.graph.node(25).state)
        with patch('ow174.game.npc_control.update_input', return_value=True):
            component.edges([(button, False)])
            self.advance(run, 16)
            self.assertTrue(release.active)
            self.assertFalse(run.match.combat.is_stunned(source))
            self.assertEqual(helper.health(run, source), 175.)

            component.edges([(button, True)])
            self.advance(run, 75)
            self.assertTrue(release.active)
            self.assertEqual(contact.counter, 1)
            self.advance(run, 1)
            self.assertFalse(release.active)
            self.advance(run, 32)
            self.assertEqual(contact.counter, 1)
            self.assertEqual(helper.health(run, source), 175.)

            component.edges([(button, False)])
            run.step({})
            component.edges([(button, True)])
            run.step({})
            self.assertEqual(contact.counter, 2)
            self.assertTrue(run.match.combat.is_stunned(source))
            self.assertEqual(helper.health(run, source), 150.)

    def test_range_real_wall_and_team_loss_release_pin(self):
        for condition in ('range','wall','team'):
            with self.subTest(condition=condition):
                helper,run,source,bot = self.scenario()
                self.start(run,source,bot)
                if condition == 'range':
                    bot.position = (0,1,10)
                elif condition == 'wall':
                    self.fixture.wall(run)
                else:
                    bot.team = source.team
                self.advance(run,100)
                self.assertEqual(helper.health(run,source),175.)
                self.assertFalse(run.match.combat.is_stunned(source))
                self.assertIsNone(bot.body_script.component.world.manual_target)

    def test_removal_body_replacement_and_script_failure_release_old_target(self):
        for condition in ('removal','replacement','failure'):
            with self.subTest(condition=condition):
                helper,run,source,bot = self.scenario()
                self.start(run,source,bot)
                old_body = source.body
                if condition == 'removal':
                    self.assertEqual(npc.remove_spawned(run.match),(bot.entity,))
                elif condition == 'replacement':
                    run.match.switch_hero(source,source.hero)
                    npc_control.stop(run.match)
                else:
                    with (patch.object(bot.body_script,'advance_live',
                                       side_effect=RuntimeError('fixture fault')),
                          self.assertLogs('ow174.game',level='ERROR')):
                        run.step({})
                    self.assertIsNone(bot.body_script)
                self.assertNotIn(old_body,run.match.combat.pins.values())
                before = helper.health(run,source)
                self.advance(run,100)
                self.assertEqual(helper.health(run,source),before)

    def test_allied_barrier_changes_survival_under_two_allies_actual_body_shots(self):
        # Preserve the unshielded death observation. This is the current manual
        # health-clock/body-shot model, not native pin balance or melee escape.
        for barrier in (False,True):
            with self.subTest(allied_barrier=barrier):
                helper,run,source,bot = self.scenario()
                allies = run.players[1:]
                for ally,x in zip(allies,(-1,1),strict=True):
                    run.match.switch_hero(ally,heroes()[pve.fixtures.LUCIO])
                    ally.team = source.team
                    ally.mover.position[:] = (x,1,0)
                    ally.mover.body = None
                self.start(run,source,bot)
                self.assertEqual(self.vitals(run,bot).total,500.)
                if barrier:
                    allies[0].body_script.ult_seconds = 0
                    allies[0].body_script.set_ult_charge(2940.)  # declared charged supporting caster
                for frame in range(260):
                    commands = {}
                    for index,ally in enumerate(allies):
                        yaw,pitch = aim(run.match.combat._eye(ally),
                                        (bot.position[0],bot.position[1]+.7,bot.position[2]))
                        # Give the charged ally an actual ultimate input before
                        # firing; a short Q during a minimum burst can expire.
                        buttons = 32 if barrier and index == 0 and frame < 50 else FIRE
                        commands[ally] = (yaw,pitch,buttons)
                    run.step(commands)
                    if barrier and frame == 50:
                        self.assertEqual(allies[0].body_script.component.entity_var(342).value(),0.)
                        self.assertTrue(self.vitals(run,source).temporary_shields)
                    if bot.dead:
                        break
                self.assertTrue(bot.dead)
                self.assertFalse(run.match.combat.is_stunned(source))
                if barrier:
                    self.assertGreater(helper.health(run,source),0.)
                else:
                    self.assertEqual(helper.health(run,source),0.)
                before = helper.health(run,source)
                self.advance(run,20)
                if not barrier:  # surviving temporary shields continue their ordinary decay
                    self.assertEqual(helper.health(run,source),before)
                self.assertIn(bot,run.match.bots)

    def test_ally_actual_flashbang_releases_pin_during_independent_source_stun(self):
        _,run,source,bot = self.scenario()
        ally = run.players[2]
        run.match.switch_hero(ally,heroes()[0x02E0000000000042])
        ally.mover.position[:] = (1,1,0)
        ally.mover.body = None
        self.start(run,source,bot)
        yaw,pitch = aim(run.match.combat._eye(ally),(0,1.7,2))
        run.step({ally:(yaw,pitch,16)})
        for _ in range(50):
            run.step({})
            if run.match.combat.is_stunned(bot):
                break
        self.assertTrue(run.match.combat.is_stunned(bot))
        self.assertFalse(run.match.combat.is_stunned(source))
        self.assertFalse(run.match.combat.pins)

    def test_pin_during_barrier_cast_aborts_late_slam_and_preserves_consumed_charge(self):
        _,run,source,bot = self.scenario()
        source.body_script.set_ult_charge(2940.)
        run.step({source:(0,0,32)})
        self.advance(run,10)
        self.start(run,source,bot)
        ultimate = next(i for i in source.body_script.component.instances.values() if i.graph.index == 0x073A)
        ability = ultimate.state(10)
        self.assertFalse(ability.flags & nodes.Ability.RUNNING)
        self.advance(run,80)
        self.assertFalse(run.match.combat.lucio_barriers.leases)
        self.assertEqual(source.body_script.component.entity_var(342).value(),0.)

    def test_pin_after_slam_absorbs_damage_from_existing_barrier(self):
        _,run,source,bot = self.scenario()
        source.body_script.set_ult_charge(2940.)
        run.step({source:(0,0,32)})
        self.advance(run,41)
        self.assertEqual(self.vitals(run,source).total,950.)
        self.start(run,source,bot)
        self.advance(run,90)
        self.assertEqual(self.vitals(run,source).now[combat.HEALTH_POOL],200.)
        self.assertTrue(self.vitals(run,source).temporary_shields)

    def test_existing_normal_and_amp_healing_continue_during_contact_delay(self):
        for amp in (False,True):
            with self.subTest(amp=amp):
                helper,run,source,bot = self.scenario()
                ally = run.players[2]
                ally.mover.position[:] = (1,1,0)
                ally.mover.body = None
                run.step({source:(0,0,8)})
                self.advance(run,30)
                if amp:
                    run.step({source:(0,0,16)})
                    self.advance(run,20)
                self.vitals(run,ally).damage(100.)
                self.start(run,source,bot)
                before = helper.health(run,ally)
                self.advance(run,20)
                self.assertTrue(run.match.combat.is_stunned(source))
                self.assertAlmostEqual(helper.health(run,ally)-before,(52. if amp else 16.)*20*.016,places=5)


if __name__ == '__main__':
    unittest.main()
