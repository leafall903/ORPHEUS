"""Actual1157 manual input against Genji; engine pin policies remain reconstructed."""

import os
import unittest
from unittest.mock import patch

import test_genji_npc as npc_tests
from test_game_combat import aim
from test_game_mover import FLOOR, TICK, soldier, wall
from test_genji_combat import GenjiFixture

from ow174.game import combat, npc, npc_control
from ow174.game.body_vm_snapshot import export_component_vm, snapshot_digest
from ow174.game.collision import World
from ow174.game.commands import Command
from ow174.game.content import heroes
from ow174.game.mover import statescript_mods


@unittest.skipUnless(os.environ.get("ORPHEUS_NPC_DATA_DIR"), "requires explicit authored NPC inputs")
class GenjiAssassinTests(GenjiFixture):
    @classmethod
    def setUpClass(cls):
        npc_tests.GenjiNpcTests.setUpClass()
        cls.catalog = npc_tests.GenjiNpcTests.catalog

    @classmethod
    def tearDownClass(cls):
        npc_tests.GenjiNpcTests.tearDownClass()

    def arena(self, distance=2, *, helper=False):
        if helper:
            run, player = self.spawn(names=("Genji", "Support"))
            run.match._pending_bot_scripts.clear()
            self.enterContext(patch.object(player.client, "bot_stream", return_value=None, create=True))
            bot = npc.spawn(run.match, self.catalog["4"], (0, 1, distance), team=1, yaw=-32768)
            run.players[1].mover.position[:] = [2, 1, distance]
        else:
            run, player, bot = npc_tests.GenjiNpcTests.scene(self, "4", distance=distance)
        for peer in run.players:
            self.enterContext(patch.object(peer.client, "queue_bot_death", create=True))
            self.enterContext(patch.object(peer.client, "queue_bot_death_update", create=True))
        player.body_script.ult_seconds = 0
        player.body_script.set_ult_charge(0)
        yaw, pitch = aim(run.match.combat._eye(player), (0, 1.75, distance))
        run.step({player: (yaw, pitch, 0)})
        return run, player, bot

    def state(self, bot, pos):
        instance = next(i for i in bot.body_script.component.instances.values() if i.graph.index == 0x1157)
        return instance.states.get(instance.graph.node(pos).state)

    def begin(self, run, player, bot):
        x, y, z = bot.position
        yaw, pitch = aim(run.match.combat._eye(player), (x, y + .75, z))
        run.step({player: (yaw, pitch, 0)})
        self.assertEqual(npc_control.start(run.match, player), bot.entity)
        return run.frame

    def start(self, run, player, bot, buttons=0):
        first = self.begin(run, player, bot)
        for _ in range(80):
            run.step({player: (0, 0, buttons)})
            if bot.entity in getattr(run.match.combat, "pins", {}):
                return run.frame - first
        self.fail("actual1157 input did not establish contact")

    def test_primary_and_assassin_contact_trade_damage_in_one_encounter(self):
        run, player, bot = self.arena()
        vitals = run.match.combat.vitals[bot.entity]
        before = vitals.total
        yaw, pitch = aim(run.match.combat._eye(player), (0, 1.75, 2))
        for frame in range(24):
            run.step({player: (yaw, pitch, 1 if frame == 0 else 0)})
        self.assertEqual(vitals.total, before - 87)
        self.assertEqual(npc_control.start(run.match, player), bot.entity)
        for _ in range(80):
            run.step({player: (yaw, pitch, 0)})
            if run.match.combat.pins.get(bot.entity) == player.body:
                break
        self.assertEqual(run.match.combat.pins[bot.entity], player.body)
        self.assertEqual(self.health(run, player), 175)
        self.assertEqual(vitals.total, before - 87)
        self.assertFalse(bot.dead)

    def test_airborne_primary_hits_during_pin_but_remaining_burst_is_suppressed(self):
        run, player, bot = self.arena(distance=2.8)
        fight = run.match.combat
        vitals = fight.vitals[bot.entity]
        before = vitals.total
        yaw, pitch = aim(fight._eye(player), (0, 1.75, 2.8))
        fire = {player: (yaw, pitch, 1)}
        run.step(fire)
        airborne, = fight.rockets
        self.assertIs(airborne.shooter, player)
        self.assertEqual(vitals.total, before)

        self.assertEqual(npc_control.start(run.match, player), bot.entity)
        run.step(fire)
        self.assertEqual(fight.pins[bot.entity], player.body)
        self.assertEqual(self.health(run, player), 175)
        self.assertIs(fight.rockets[0], airborne)
        self.assertEqual(vitals.total, before)

        for _ in range(24):
            run.step(fire)
            self.assertEqual(fight.pins[bot.entity], player.body)
        self.assertFalse(fight.rockets)
        self.assertEqual(vitals.total, before - 29)
        hits = player.client.messages(combat.HIT)
        self.assertEqual([hit["+0x78"]["+0x4"] for hit in hits], [29])

    def test_airborne_fan_hits_during_pin_without_consuming_more_ammo(self):
        run, player, bot = self.arena(distance=2.8)
        fight = run.match.combat
        vitals = fight.vitals[bot.entity]
        before = vitals.total
        weapon = next(i for i in player.body_script.component.instances.values() if i.graph.index == 0x0304)
        yaw, pitch = aim(fight._eye(player), (0, 1.75, 2.8))
        fire = {player: (yaw, pitch, 2)}
        run.step(fire)
        airborne = tuple(fight.rockets)
        self.assertEqual(len(airborne), 3)
        self.assertTrue(all(projectile.shooter is player for projectile in airborne))
        self.assertEqual(vitals.total, before)
        self.assertEqual(weapon.vars[53].value(), 27)

        self.assertEqual(npc_control.start(run.match, player), bot.entity)
        run.step(fire)
        self.assertEqual(fight.pins[bot.entity], player.body)
        self.assertEqual(self.health(run, player), 175)
        self.assertEqual(tuple(map(id, fight.rockets)), tuple(map(id, airborne)))
        self.assertEqual(vitals.total, before)

        for _ in range(60):
            run.step(fire)
            self.assertEqual(fight.pins[bot.entity], player.body)
        self.assertFalse(fight.rockets)
        self.assertEqual(vitals.total, before - 87)
        self.assertEqual(weapon.vars[53].value(), 27)
        hits = player.client.messages(combat.HIT)
        self.assertEqual([hit["+0x78"]["+0x4"] for hit in hits], [29] * 3)

    def test_airborne_primary_kills_pin_source_and_restores_weapon_input(self):
        run, player, bot = self.arena(distance=2.8)
        fight = run.match.combat
        vitals = fight.vitals[bot.entity]
        # Isolate the lethal impact of a single already-launched shuriken.
        vitals.damage(vitals.total - 20)
        fight._sync_body_health(bot, vitals)
        weapon = next(i for i in player.body_script.component.instances.values() if i.graph.index == 0x0304)
        yaw, pitch = aim(fight._eye(player), (0, 1.75, 2.8))
        fire = {player: (yaw, pitch, 1)}
        run.step(fire)
        airborne, = fight.rockets
        self.assertIs(airborne.shooter, player)

        self.assertEqual(npc_control.start(run.match, player), bot.entity)
        run.step(fire)
        self.assertEqual(fight.pins[bot.entity], player.body)
        self.assertEqual(self.health(run, player), 175)
        self.assertIs(fight.rockets[0], airborne)

        for _ in range(10):
            run.step({})
            if bot.dead:
                break
        self.assertTrue(bot.dead)
        self.assertEqual(vitals.killer, player.body)
        self.assertFalse(fight.pins)
        self.assertTrue(bot.retain_corpse)
        self.assertEqual(weapon.vars[53].value(), 29)
        hits = player.client.messages(combat.HIT)
        self.assertEqual([hit["+0x78"]["+0x4"] for hit in hits], [20])
        self.assertTrue(hits[0]["+0x78"]["+0x8"] & combat.KILLING)

        for _ in range(80):
            run.step({})
        self.assertFalse(fight.assassin_victims)
        self.assertEqual(self.health(run, player), 175)
        run.step(fire)
        self.assertEqual(weapon.state(4).counter, 2)
        self.assertEqual(weapon.vars[53].value(), 28)
        self.assertIn(bot, run.match.bots)

    def test_actual_contact_damage_delay_rate_and_command_suppression(self):
        run, player, bot = self.arena()
        self.start(run, player, bot)
        self.assertEqual(run.match.combat.pins[bot.entity], player.body)
        self.assertEqual(self.health(run, player), 175)
        self.assertGreater(self.state(bot, 25).counter, 0)
        for _ in range(10):
            run.step({player: (0, 0, 1 | 2 | 8 | 16 | 32 | 0x800)})
        self.assertEqual(self.health(run, player), 175)
        self.assertFalse(player.body_script.component.held)
        self.assertFalse(run.match.combat.rockets)
        for _ in range(80):
            run.step({})
            if (state := self.state(bot, 15)) is not None and state.active:
                break
        self.assertTrue(self.state(bot, 15).active)
        before = self.health(run, player)
        for _ in range(10):
            run.step({})
        self.assertAlmostEqual(before - self.health(run, player), 16, places=4)

    def test_pin_cancels_pending_refill_and_blocks_fire_until_release(self):
        for delay, contact_ammo in ((5, 27), (57, 30)):
            with self.subTest(delay=delay):
                run, player, bot = self.arena()
                weapon = next(i for i in player.body_script.component.instances.values()
                              if i.graph.index == 0x0304)
                for frame in range(60):
                    run.step({player: (-32768, 0, 1 if frame == 0 else 0)})
                self.assertEqual(weapon.vars[53].value(), 27)
                before = run.match.combat.vitals[bot.entity].total
                run.step({player: (0, 0, 0x400)})
                for _ in range(delay):
                    run.step({})
                self.start(run, player, bot)
                self.assertEqual(weapon.vars[53].value(), contact_ammo)
                for _ in range(65):
                    run.step({player: (0, 0, 1)})
                    self.assertEqual(weapon.state(4).counter, 1)
                self.assertEqual(run.match.combat.pins.get(bot.entity), player.body)
                self.assertEqual(weapon.vars[53].value(), contact_ammo)
                self.assertEqual(run.match.combat.vitals[bot.entity].total, before)

                npc_control.stop(run.match)
                for _ in range(70):
                    run.step({})
                self.assertEqual(weapon.vars[53].value(), contact_ammo)
                if contact_ammo == 27:
                    run.step({player: (0, 0, 0x400)})
                    for _ in range(95):
                        run.step({})
                self.assertEqual(weapon.vars[53].value(), 30)
                x, y, z = bot.position
                yaw, pitch = aim(run.match.combat._eye(player), (x, y + .75, z))
                for frame in range(60):
                    run.step({player: (yaw, pitch, 1 if frame == 0 else 0)})
                self.assertEqual(weapon.state(4).counter, 2)
                self.assertEqual(weapon.vars[53].value(), 27)
                self.assertEqual(run.match.combat.vitals[bot.entity].total, before - 87)

    def test_pin_interrupts_automatic_reload_and_empty_weapon_retries_on_release(self):
        for delay, contact_ammo in ((55, 0), (100, 30)):
            with self.subTest(contact_ammo=contact_ammo):
                run, player, bot = self.arena()
                weapon = next(i for i in player.body_script.component.instances.values()
                              if i.graph.index == 0x0304)
                before = run.match.combat.vitals[bot.entity].total
                for _ in range(500):
                    run.step({player: (-32768, 0, 2)})
                    if weapon.vars[53].value() == 0:
                        break
                self.assertEqual(weapon.vars[53].value(), 0)
                self.assertEqual(run.match.combat.vitals[bot.entity].total, before)
                for _ in range(delay):
                    run.step({})
                self.assertTrue(weapon.state(54 if contact_ammo == 0 else 53).active)
                self.start(run, player, bot)

                for _ in range(65):
                    run.step({player: (0, 0, 1)})
                    self.assertEqual(weapon.state(4).counter, 0)
                    self.assertEqual(weapon.vars[53].value(), contact_ammo)
                self.assertEqual(run.match.combat.pins.get(bot.entity), player.body)
                self.assertEqual(run.match.combat.vitals[bot.entity].total, before)
                npc_control.stop(run.match)
                self.assertNotIn(bot.entity, run.match.combat.pins)

                # The victim release phase can outlast the weapon wait.
                for _ in range(70):
                    run.step({})
                for _ in range(160):
                    run.step({})
                    if weapon.vars[53].value() == 30 and not weapon.state(53).active:
                        break
                self.assertEqual(weapon.vars[53].value(), 30)
                self.assertFalse(weapon.state(53).active)
                self.assertEqual(weapon.state(4).counter, 0)
                x, y, z = bot.position
                yaw, pitch = aim(run.match.combat._eye(player), (x, y + .75, z))
                for frame in range(60):
                    run.step({player: (yaw, pitch, 1 if frame == 0 else 0)})
                self.assertEqual(weapon.state(4).counter, 1)
                self.assertEqual(weapon.vars[53].value(), 27)
                self.assertEqual(run.match.combat.vitals[bot.entity].total, before - 87)

    def test_stop_releases_without_late_damage_and_restart_uses_new_contact(self):
        run, player, bot = self.arena()
        self.start(run, player, bot)
        npc_control.stop(run.match)
        self.assertFalse(run.match.combat.pins)
        before = self.health(run, player)
        for _ in range(100):
            run.step({})
        self.assertEqual(self.health(run, player), before)
        self.start(run, player, bot)
        self.assertEqual(self.health(run, player), before - 25)
        self.assertEqual(self.state(bot, 25).counter, 2)

    def test_contact_range_wall_and_friendly_loss_release_without_late_damage(self):
        for reason in ("range", "wall", "team"):
            with self.subTest(reason=reason):
                run, player, bot = self.arena()
                self.start(run, player, bot)
                before = self.health(run, player)
                if reason == "range":
                    bot.position = (0, 1, 8)
                elif reason == "wall":
                    run.match.collision_world = World(FLOOR + wall(-5, 1, 5, 1))
                else:
                    run.match.game_map = run.match.game_map.__class__(
                        **{**run.match.game_map.__dict__, "free_for_all": False})
                    bot.team = player.team
                for _ in range(100):
                    run.step({})
                self.assertEqual(self.health(run, player), before)
                self.assertFalse(run.match.combat.pins)
                self.assertFalse(bot.attack_input)
                self.assertIsNone(bot.body_script.component.world.manual_target)

    def test_out_of_contact_start_does_not_pin_or_damage(self):
        run, player, bot = self.arena(distance=8)
        self.begin(run, player, bot)
        for _ in range(120):
            run.step({})
        self.assertEqual(self.health(run, player), 200)
        self.assertFalse(run.match.combat.pins)
        self.assertFalse(bot.attack_input)

    def test_pin_blocks_motion_and_skills_but_preserves_look_and_release_recovers(self):
        run, player, bot = self.arena()
        self.start(run, player, bot)
        position = tuple(player.mover.position)
        for _ in range(20):
            run.frame += 1
            run.match.update(run.frame * combat.TICK, run.frame)
            player.take_commands([Command(run.frame, forward=127, right=127, yaw=1234, pitch=345,
                                          buttons=1 | 2 | 8 | 16 | 32 | 0x40 | 0x800)])
        self.assertEqual(tuple(player.mover.position), position)
        self.assertEqual((player.mover.yaw, player.mover.pitch), (1234, 345))
        self.assertFalse(player.body_script.component.held)
        npc_control.stop(run.match)
        for _ in range(100):
            run.frame += 1
            run.match.update(run.frame * combat.TICK, run.frame)
            player.take_commands([Command(run.frame, forward=127, yaw=0)])
        self.assertGreater(player.mover.position[2], position[2] + .1)
        self.assertFalse(run.match.combat.assassin_victims)

    def test_victim_body_replacement_and_manual_source_removal_retire_pin_identity(self):
        for action in ("replace", "remove"):
            with self.subTest(action=action):
                run, player, bot = self.arena()
                self.start(run, player, bot)
                if action == "replace":
                    old = player.body
                    run.match.switch_hero(player, player.hero)
                    self.assertNotEqual(player.body, old)
                    npc_control.stop(run.match)
                else:
                    self.assertEqual(npc.remove_spawned(run.match), (bot.entity,))
                self.assertFalse(run.match.combat.pins)
                before = self.health(run, player)
                for _ in range(100):
                    run.step({})
                self.assertEqual(self.health(run, player), before)

    def test_actual_support_bullet_kills_pin_source_and_retains_corpse(self):
        run, player, bot = self.arena(helper=True)
        vitals = run.match.combat.vitals[bot.entity]
        vitals.damage(vitals.total - 10)
        run.match.combat._sync_body_health(bot, vitals)
        self.start(run, player, bot)
        support = run.players[1]
        yaw, pitch = aim(run.match.combat._eye(support), (0, 1.75, 2))
        for frame in range(30):
            run.step({support: (yaw, pitch, 1 if frame == 0 else 0)})
        self.assertTrue(bot.dead)
        self.assertFalse(run.match.combat.pins)
        self.assertIsNotNone(bot.body_script)
        before = self.health(run, player)
        for _ in range(200):
            run.step({})
        self.assertEqual(self.health(run, player), before)
        self.assertIn(bot, run.match.bots)

    def test_contact_lethal_to_victim_releases_without_future_body_damage(self):
        run, player, bot = self.arena()
        target = next(t for t in run.match.combat._targets() if t.owner is player)
        vitals = run.match.combat.vitals_of(target)
        vitals.damage(190)
        run.match.combat._sync_body_health(player, vitals)
        self.begin(run, player, bot)
        for _ in range(40):
            run.step({})
        self.assertTrue(vitals.dead)
        self.assertEqual(vitals.total, 0)
        self.assertFalse(run.match.combat.pins)
        self.assertFalse(bot.attack_input)
        self.assertIsNone(bot.body_script.component.world.manual_target)

    def test_sustained_pin_kills_and_reacquires_the_respawned_body(self):
        run, player, bot = self.arena()
        player.spawn = ((0, 1, 0), 0)
        old_body = player.body
        old_script = player.body_script
        self.start(run, player, bot)
        self.assertEqual(self.health(run, player), 175)
        for _ in range(450):
            run.step({})
            if run.match.combat.is_dead(player):
                break
        self.assertTrue(run.match.combat.is_dead(player))
        self.assertEqual(run.match.combat.vitals[old_body].killer, bot.entity)
        self.assertFalse(run.match.combat.pins)
        for _ in range(650):
            run.step({})
            if player.body != old_body:
                break
            self.assertFalse(bot.attack_input)
            self.assertIsNone(bot.body_script.component.world.manual_target)
        self.assertNotEqual(player.body, old_body)
        self.assertTrue(old_script.retired)
        self.assertEqual(self.health(run, player), 200)
        for _ in range(250):
            run.step({})
            if run.match.combat.pins.get(bot.entity) == player.body:
                break
        self.assertEqual(run.match.combat.pins.get(bot.entity), player.body)
        self.assertEqual(self.health(run, player), 175)

    def test_live_clock_and_target_context_are_causal_vm_state(self):
        run, player, bot = self.arena()
        self.start(run, player, bot)
        component = bot.body_script.component
        state = self.state(bot, 25)
        before = snapshot_digest(export_component_vm(component))
        state.last_applied += 1
        self.assertNotEqual(snapshot_digest(export_component_vm(component)), before)
        state.last_applied -= 1
        self.assertEqual(snapshot_digest(export_component_vm(component)), before)
        original = component.world.manual_target
        component.world.manual_target = player.entity
        self.assertNotEqual(snapshot_digest(export_component_vm(component)), before)
        component.world.manual_target = original
        self.assertEqual(snapshot_digest(export_component_vm(component)), before)

    def test_continuous_clock_deduplicates_ticks_and_catches_up_short_world_gaps(self):
        outcomes = []
        for gap in (False, True):
            run, player, bot = self.arena()
            self.start(run, player, bot)
            for _ in range(80):
                run.step({})
                if (state := self.state(bot, 15)) is not None and state.active:
                    break
            before = self.health(run, player)
            if gap:
                run.frame += 10
                run.match.update(run.frame * combat.TICK, run.frame)
            else:
                for _ in range(10):
                    run.step({})
            outcomes.append(before - self.health(run, player))
            run.match.combat.update(run.frame * combat.TICK, run.frame)
            self.assertEqual(before - self.health(run, player), outcomes[-1])
        self.assertAlmostEqual(outcomes[0], 16, places=4)
        self.assertEqual(outcomes[0], outcomes[1])

    def test_reconstructed_contact_interrupts_deflect_without_reflecting_pin_damage(self):
        run, player, bot = self.arena()
        source_health = run.match.combat.vitals[bot.entity].total
        for frame in range(5):
            run.step({player: (0, 0, 16 if frame == 0 else 0)})
        instance = next(i for i in player.body_script.component.instances.values() if i.graph.index == 0x03A3)
        self.assertTrue(instance.state(10).active)
        self.start(run, player, bot)
        self.assertFalse(instance.state(10).active)
        self.assertEqual(self.health(run, player), 175)
        self.assertEqual(run.match.combat.vitals[bot.entity].total, source_health)
        npc_control.stop(run.match)
        # First cover rejection after the original .2s wind-down writes cooldown.
        # Immediate release/repress is separately covered through victim1177.
        for _ in range(20):
            run.step({})
        for _ in range(80):
            run.step({player: (0, 0, 16)})
        self.assertEqual(instance.state(10).counter, 1)
        self.assertFalse(instance.state(10).active)
        self.assertGreater(instance.state(0).var.value(), 0)

    def test_immediate_pin_release_keeps_victim_control_until_cooldown_write(self):
        # Actual1177 control scope now covers the authored cooldown-write gap.
        # Native animation crop/rate still needs independent acceptance.
        run, player, bot = self.arena()
        for frame in range(5):
            run.step({player: (0, 0, 16 if frame == 0 else 0)})
        instance = next(i for i in player.body_script.component.instances.values() if i.graph.index == 0x03A3)
        self.start(run, player, bot)
        npc_control.stop(run.match)
        run.step({player: (0, 0, 16)})
        self.assertEqual(instance.state(10).counter, 1)
        self.assertFalse(instance.state(10).active)

    def test_contact_stops_active_strike_displacement_and_cooldown_does_not_resume_it(self):
        run, player, bot = self.arena()
        for frame in range(2):
            run.step({player: (0, 0, 8 if frame == 0 else 0)})
        self.assertGreater(player.mover.position[2], 0)
        self.start(run, player, bot)
        position = tuple(player.mover.position)
        self.assertGreater(position[2], 0)
        self.assertLess(position[2], 15)
        npc_control.stop(run.match)
        for _ in range(60):
            run.step({})
        self.assertEqual(tuple(player.mover.position), position)
        instance = next(i for i in player.body_script.component.instances.values() if i.graph.index == 0x01DB)
        self.assertFalse(instance.state(1).active)
        self.assertGreater(instance.state(0).var.value(), 0)

    def blade(self, player):
        return next(i.state(3) for i in player.body_script.component.instances.values()
                    if i.graph.index == 0x0317)

    def draw(self, run, player, frames):
        player.body_script.set_ult_charge(player.body_script.component.entity_var(338).value())
        for frame in range(frames):
            run.step({player: (0, 0, 32 if frame == 0 else 0)})
        self.assertTrue(self.blade(player).flags & 1)

    def test_reconstructed_pin_preserves_blade_timer_during_draw_and_active_phase(self):
        for frames in (1, 80):
            with self.subTest(draw_frames=frames):
                run, player, bot = self.arena()
                self.draw(run, player, frames)
                self.start(run, player, bot)
                before = run.match.combat.vitals[bot.entity].total
                for _ in range(20):
                    run.step({player: (0, 0, 1)})
                    self.assertTrue(self.blade(player).flags & 1)
                self.assertEqual(run.match.combat.vitals[bot.entity].total, before)
                npc_control.stop(run.match)
                for _ in range(120):
                    run.step({player: (0, 0, 1)})
                self.assertLess(run.match.combat.vitals[bot.entity].total, before)
                self.assertEqual(player.body_script.component.entity_var(342).value(), 0)

    def test_pin_preserves_blade_active_timer_and_authored_holster_priority_can_end_wrapper(self):
        active_endings, wrapper_endings = [], []
        for pin_at in (None, 80, 470):
            run, player, bot = self.arena()
            start = run.frame
            player.body_script.set_ult_charge(player.body_script.component.entity_var(338).value())
            seen_active, active_end, released, began = False, None, False, False
            while run.frame - start < 650:
                elapsed = run.frame - start
                if pin_at is not None and not began and elapsed >= pin_at:
                    self.start(run, player, bot)
                    began = run.frame
                if began and not released and run.frame - began >= 20:
                    npc_control.stop(run.match)
                    released = True
                run.step({player: (0, 0, 32 if elapsed == 0 else 0)})
                instance = next(i for i in player.body_script.component.instances.values()
                                if i.graph.index == 0x0317)
                slot = instance.vars.get(215)
                active = slot is not None and (slot.value() or 0) > 0
                if active:
                    seen_active = True
                elif seen_active and active_end is None:
                    active_end = run.frame - start
                if seen_active and not self.blade(player).flags & 1:
                    break
            active_endings.append(active_end)
            wrapper_endings.append(run.frame - start)
            self.assertFalse(self.blade(player).flags & 1)
            self.assertFalse(run.match.combat.is_dead(player))
            self.assertEqual(player.body_script.component.entity_var(342).value(), 0)
        self.assertEqual(active_endings, [active_endings[0]] * 3)
        self.assertIsNotNone(active_endings[0])
        self.assertEqual(wrapper_endings[0], wrapper_endings[1])
        #0317 node17 loses top -> abort Wait18 -> onEnd StopAbility19.
        #This occurs in the holster phase after215 reaches0, not in the sword timer.
        self.assertLess(wrapper_endings[2], wrapper_endings[0])

    def test_actual_friendly_flashbang_interrupts_pin_source_and_keeps_source_stun_deadline(self):
        run, player, bot = self.arena(helper=True)
        support = run.players[1]
        run.match.switch_hero(support, heroes()[0x02E0000000000042])
        support.mover.position[:] = [2, 1, 2]
        run.match.game_map = run.match.game_map.__class__(
            **{**run.match.game_map.__dict__, "free_for_all": False})
        support.team = player.team
        self.start(run, player, bot)
        yaw, pitch = aim(run.match.combat._eye(support), (0, 1.75, 2))
        for frame in range(70):
            run.step({support: (yaw, pitch, 16 if frame == 0 else 0)})
            if run.match.combat.is_stunned(bot):
                break
        self.assertTrue(run.match.combat.is_stunned(bot))
        deadline = run.match.combat.stuns[bot.entity]
        self.assertFalse(run.match.combat.pins)
        self.assertNotIn(player.body, run.match.combat.stuns)
        npc_control.stop(run.match)
        before = self.health(run, player)
        while run.frame < deadline:
            run.step({})
        self.assertEqual(self.health(run, player), before)
        self.assertFalse(run.match.combat.is_stunned(bot))
        for _ in range(100):
            run.step({})
        self.assertFalse(run.match.combat.is_stunned(player))

    def test_victim_script_real_entry_sender_phase_release_animation_and_stop_are_bounded(self):
        run, player, bot = self.arena()
        self.start(run, player, bot)
        fight = run.match.combat
        lease = fight.assassin_victims[bot.entity]
        component = player.body_script.component
        instance = component.instances[lease.instance]
        self.assertEqual(instance.graph.index, 0x1177)
        self.assertEqual(instance.source_entity(), bot.entity)
        self.assertEqual(instance.vars[40].value().id, bot.entity)
        receiver = instance.states[instance.graph.node(17).state]
        self.assertTrue(receiver.received)
        self.assertEqual(receiver.payload()["sender"], bot.entity)
        self.assertIsInstance(snapshot_digest(export_component_vm(component)), str)
        tree, base = player.body_script.tree_frame()
        delta, _, snapshot = player.body_script.delta(base, set(), player.body_script.frame, False)
        self.assertIn(lease.instance, snapshot.instances)
        self.assertGreater(tree.count, 0)
        self.assertGreater(delta.count, 0)
        self.assertTrue(instance.states[instance.graph.node(2).state].active)
        for _ in range(70):
            run.step({})
        self.assertTrue(instance.states[instance.graph.node(3).state].active)
        npc_control.stop(run.match)
        before = self.health(run, player)
        run.step({})
        self.assertFalse(receiver.received)
        self.assertEqual(receiver.message_sender, 0)
        self.assertTrue(instance.states[instance.graph.node(4).state].active)
        for _ in range(30):
            run.step({})
        self.assertTrue(fight.is_stunned(player))
        for _ in range(60):
            run.step({})
        self.assertNotIn(lease.instance, component.instances)
        self.assertFalse(fight.assassin_victims)
        self.assertFalse(fight.is_stunned(player))
        self.assertEqual(self.health(run, player), before)

    def test_manual_restart_replaces_old_release_and_stale_request_cannot_recreate_it(self):
        run, player, bot = self.arena()
        self.start(run, player, bot)
        fight = run.match.combat
        old = fight.assassin_victims[bot.entity]
        npc_control.stop(run.match)
        self.start(run, player, bot)
        current = fight.assassin_victims[bot.entity]
        self.assertNotEqual(current.instance, old.instance)
        self.assertNotIn(old.instance, player.body_script.component.instances)
        self.assertEqual(current.activation, old.activation + 1)
        npc_control.stop(run.match)
        for _ in range(100):
            run.step({})
        self.assertFalse(fight.assassin_victims)
        source = next(i for i in bot.body_script.component.instances.values() if i.graph.index == 0x1157)
        bot.body_script.component.world.compatibility_play_script(source, source.graph.node(45))
        self.assertFalse(fight.assassin_victims)

    def test_reprepared_source_vm_cannot_keep_or_replay_old_victim_authority(self):
        run, player, bot = self.arena()
        self.start(run, player, bot)
        old = bot.body_script.component
        source = next(i for i in old.instances.values() if i.graph.index == 0x1157)
        lease = run.match.combat.assassin_victims[bot.entity]
        run.match._prepare_bot_script(bot)
        self.assertIsNot(bot.body_script.component, old)
        npc_control.stop(run.match)
        old.world.compatibility_play_script(source, source.graph.node(45))
        self.assertIs(run.match.combat.assassin_victims[bot.entity], lease)
        before = self.health(run, player)
        for _ in range(100):
            run.step({})
        self.assertFalse(run.match.combat.assassin_victims)
        self.assertEqual(self.health(run, player), before)

    def test_authored_victim_throttle_mods_apply_next_pose_and_restore_after_end(self):
        run, player, bot = self.arena()
        self.start(run, player, bot)
        active = statescript_mods(player.body_script.component)
        throttles = {key: value for key, value in active.items() if value[0] in (17, 18, 19)}
        self.assertEqual({value[0] for value in throttles.values()}, {17, 18, 19})
        # Isolate the actual VM mod rows from Combat's separate input suppression.
        for right, forward in ((127, 0), (-127, 0), (0, 127), (0, -127)):
            with self.subTest(right=right, forward=forward):
                mover = soldier((0, 1, 0))
                mover.track_mods(throttles, 10)
                self.assertTrue(all(not mod.acts(10) and mod.acts(11) for mod in mover.mods.values()))
                mover.step(Command(11, right=right, forward=forward), TICK, 11)
                self.assertEqual(mover.velocity, (0, 0, 0))
                self.assertEqual(mover.throttles, (right, forward))
                mover.track_mods({}, 11)
                mover.step(Command(12, right=right, forward=forward), TICK, 12)
                self.assertGreater(sum(abs(v) for v in mover.velocity), 0)
        npc_control.stop(run.match)
        for _ in range(100):
            run.step({})
        ended = statescript_mods(player.body_script.component)
        self.assertFalse({key: value for key, value in ended.items() if key in throttles})

    def test_victim_intro_and_release_use_authored_duration_not_raw_clip_length(self):
        run, player, bot = self.arena()
        self.start(run, player, bot)
        fight = run.match.combat
        lease = fight.assassin_victims[bot.entity]
        component = player.body_script.component
        instance = component.instances[lease.instance]
        intro = instance.state(instance.graph.node(2).state)
        release = instance.state(instance.graph.node(4).state)
        self.assertAlmostEqual(intro.evaluate(intro.node.field("m_86701C3E")), .55)
        self.assertEqual(release.evaluate(release.node.field("m_86701C3E")), 1)
        intro_due = next(e.time for e in instance.queue if e.state is intro and e.kind == 1)
        self.assertGreater(intro_due, component.now)
        self.assertLessEqual(intro_due - component.now, 550)
        while component.now < intro_due:
            self.assertTrue(intro.active)
            run.step({})
        self.assertFalse(intro.active)
        self.assertTrue(instance.state(instance.graph.node(3).state).active)
        stopped_at = component.now
        npc_control.stop(run.match)
        run.step({})
        self.assertTrue(release.active)
        release_due = next(e.time for e in instance.queue if e.state is release and e.kind == 1)
        self.assertEqual(release_due - stopped_at, 1000)
        while component.now < release_due:
            self.assertIn(lease.instance, component.instances)
            self.assertTrue(fight.is_stunned(player))
            run.step({})
        self.assertNotIn(lease.instance, component.instances)
        self.assertFalse(fight.assassin_victims)
        self.assertFalse(fight.is_stunned(player))
