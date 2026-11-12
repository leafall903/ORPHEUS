"""Command-driven Zarya damage regressions; these do not accept client presentation."""

import unittest
from unittest.mock import patch

from test_game_combat import Run

from ow174.game import bots, combat
from ow174.game.collision import World
from ow174.game.commands import Command
from ow174.game.content import heroes
from ow174.game.script import expr

ZARYA = 0x02E0000000000068


class ZaryaBeamTests(unittest.TestCase):
    def setUp(self):
        self.run = Run(names=("Zarya", "Enemy"))
        self.a, self.b = self.run.players
        self.fight = self.run.match.combat
        self.run.match.bots.clear()
        self.run.match._pending_bot_scripts.clear()
        self.run.match.switch_hero(self.a, heroes()[ZARYA])
        self.a.mover.position[:] = [0, 1, 0]
        self.b.mover.position[:] = [0, 1, 5]

    def fire(self, frames=51, buttons=1):
        for _ in range(frames):
            self.run.step({self.a: (0, 0, buttons)})

    def health(self, owner=None):
        owner = self.b if owner is None else owner
        target = next(t for t in self.fight._targets() if t.owner is owner)
        return self.fight.vitals_of(target).total

    def test_primary_integrates_authored_75_dps_and_never_crits(self):
        self.fire()
        self.assertAlmostEqual(self.health(), 140)
        self.assertTrue(self.a.client.messages(combat.HIT))
        self.assertTrue(all(not (m["+0x78"]["+0x8"] & combat.CRITICAL)
                            for m in self.a.client.messages(combat.HIT)))

    def test_live_energy_expression_changes_damage_to_170_dps(self):
        self.a.body_script.component.entity_var(87).base = 100
        self.fire()
        self.assertAlmostEqual(self.health(), 64)

    def test_repeated_command_consumption_does_not_damage_twice(self):
        self.fire()
        before = self.health()
        command = Command(self.run.frame, yaw=0, pitch=0, buttons=1, extra=0)
        self.fight.command(self.a, command)
        self.fight.command(self.a, command)
        self.assertEqual(self.health(), before)

    def test_release_stops_damage(self):
        self.fire()
        self.fire(2, 0)
        before = self.health()
        self.fire(30, 0)
        self.assertEqual(self.health(), before)

    def test_primary_future_command_batch_cannot_spend_unelapsed_world_time(self):
        self.fire(1)
        weapon = next(i for i in self.a.body_script.component.instances.values() if i.graph.index == 0x0241)
        ammo = weapon.vars[53].value()
        start = self.run.frame
        self.a.take_commands([Command(start + offset, buttons=1) for offset in range(1, 51)])
        self.assertEqual(self.fight.tick, start)
        self.assertEqual(self.health(), 200)
        self.assertEqual(weapon.vars[53].value(), ammo)

        tick = start + 3
        self.run.match.update(tick * combat.TICK, tick, frames=False)
        self.a.take_commands([Command(start + offset, buttons=1) for offset in range(51, 54)])
        self.assertAlmostEqual(self.health(), 196.4)
        self.assertAlmostEqual(weapon.vars[53].value(), ammo - 0.96, places=3)
        self.a.take_commands([Command(start + 54, buttons=1)])
        self.assertAlmostEqual(self.health(), 196.4)
        self.assertAlmostEqual(weapon.vars[53].value(), ammo - 0.96, places=3)

    def test_allies_are_not_damaged(self):
        self.b.team = self.a.team
        self.fire()
        self.assertEqual(self.health(), 200)

    def test_out_of_range_target_is_not_damaged(self):
        self.b.mover.position[2] = 17
        self.fire()
        self.assertEqual(self.health(), 200)

    def test_aimed_miss_deals_no_damage(self):
        for _ in range(51):
            self.run.step({self.a: (16384, 0, 1)})
        self.assertEqual(self.health(), 200)

    def test_beam_spends_ammo_stops_for_reload_and_resumes(self):
        target = next(t for t in self.fight._targets() if t.owner is self.b)
        vitals = self.fight.vitals_of(target)
        vitals.now[combat.HEALTH_POOL] = vitals.most[combat.HEALTH_POOL] = 10000
        reloading = False
        resumed = False
        before_reload = None
        for _ in range(500):
            self.fire(1)
            weapon = next(i for i in self.a.body_script.component.instances.values()
                          if i.graph.index == 0x0241)
            if weapon.vars[53].value() < 1 and not weapon.state(10).active:
                reloading = True
                if before_reload is None:
                    before_reload = self.health()
                self.assertEqual(self.health(), before_reload)
            elif reloading and weapon.state(10).active:
                resumed = True
        self.assertTrue(reloading)
        self.assertTrue(resumed)
        self.assertLess(self.health(), before_reload)

    def test_reload_waits_cannot_be_skipped_with_future_commands(self):
        self.fire(1, 2)
        first, = self.fight.rockets
        weapon = next(i for i in self.a.body_script.component.instances.values() if i.graph.index == 0x0241)
        start = self.run.frame
        self.a.take_commands([Command(start + 40, buttons=0x400), Command(start + 200)])
        refill, recovery = weapon.state(46), weapon.state(12)
        self.assertEqual(self.fight.tick, start)
        self.assertEqual(weapon.vars[53].value(), 75)
        self.assertTrue(refill.active)
        self.assertTrue(recovery.active)

        cases = ((65, 75, True, True), (66, 100, False, True),
                 (92, 100, False, True), (93, 75, False, False))
        for client_offset, (world_offset, ammo, refill_active, recovery_active) in enumerate(cases, 201):
            with self.subTest(world_offset=world_offset):
                tick = start + world_offset
                self.run.match.update(tick * combat.TICK, tick, frames=False)
                self.a.take_commands([Command(start + client_offset, buttons=2 if world_offset >= 92 else 0)])
                self.assertEqual(weapon.vars[53].value(), ammo)
                self.assertEqual(refill.active, refill_active)
                self.assertEqual(recovery.active, recovery_active)
                self.assertEqual(sum(rocket is not first for rocket in self.fight.rockets), int(world_offset == 93))
        self.a.take_commands([Command(start + 205, buttons=2)])
        self.assertEqual(sum(rocket is not first for rocket in self.fight.rockets), 1)
        self.assertEqual(weapon.vars[53].value(), 75)

    def test_idle_reload_releases_stale_fire_before_recovery(self):
        self.fire(1, 2)
        self.fire(39, 0)
        self.fire(1, 0x400)
        start = self.run.frame
        script = self.a.body_script
        weapon = next(i for i in script.component.instances.values() if i.graph.index == 0x0241)
        for offset, ammo in ((66, 75), (67, 100), (92, 100)):
            tick = start + offset
            self.run.match.update(tick * combat.TICK, tick, frames=False)
            self.assertEqual(weapon.vars[53].value(), ammo)
        self.a.take_commands([Command(start + 92, buttons=2)])
        self.assertTrue(weapon.state(12).active)
        self.assertFalse(self.fight.rockets)
        tick = start + 94
        self.run.match.update(tick * combat.TICK, tick, frames=False)
        self.assertFalse(weapon.state(12).active)
        self.assertFalse(self.fight.rockets)
        self.assertEqual(weapon.vars[53].value(), 100)
        self.assertEqual(script.frame, tick - 1)
        self.a.take_commands([Command(tick, buttons=2)])
        self.assertEqual(len(self.fight.rockets), 1)
        self.assertEqual(weapon.vars[53].value(), 75)

    def test_idle_reload_preserves_the_next_future_command(self):
        self.fire(1, 2)
        first, = self.fight.rockets
        start = self.run.frame
        self.a.take_commands([Command(start + 40, buttons=0x400), Command(start + 200)])
        script = self.a.body_script
        weapon = next(i for i in script.component.instances.values() if i.graph.index == 0x0241)
        for offset, ammo, refill, recovery in ((66, 75, True, True), (67, 100, False, True),
                                               (92, 100, False, True), (93, 100, False, True),
                                               (94, 100, False, False)):
            tick = start + offset
            self.run.match.update(tick * combat.TICK, tick, frames=False)
            if offset == 92:
                self.a.take_commands([Command(start + 201, buttons=2)])
            self.assertEqual(weapon.vars[53].value(), ammo)
            self.assertEqual(weapon.state(46).active, refill)
            self.assertEqual(weapon.state(12).active, recovery)
            self.assertEqual(script.frame, start + (200 if offset < 92 else 201))
        self.assertTrue(all(rocket is first for rocket in self.fight.rockets))
        self.a.take_commands([Command(start + 202, buttons=2)])
        self.assertEqual(weapon.vars[53].value(), 75)
        self.assertEqual(sum(rocket is not first for rocket in self.fight.rockets), 1)

    def test_secondary_consumes_25_ammo_including_volley(self):
        self.fire(1, 2)
        weapon = next(i for i in self.a.body_script.component.instances.values()
                      if i.graph.index == 0x0241)
        # Authored SetVar subtracts24; the volley's default single shot spends1.
        self.assertEqual(weapon.vars[53].value(), 75)

    def test_secondary_recovery_cannot_be_skipped_with_future_commands(self):
        self.fire(1, 2)
        weapon = next(i for i in self.a.body_script.component.instances.values() if i.graph.index == 0x0241)
        shot, = self.fight.rockets
        start = self.run.frame
        self.a.take_commands([Command(start + 1), Command(start + 100, buttons=2)])
        self.assertEqual(self.fight.tick, start)
        self.assertEqual(self.fight.rockets, [shot])
        self.assertEqual(weapon.vars[53].value(), 75)

        ready = start + 62
        self.run.match.update((ready - 1) * combat.TICK, ready - 1, frames=False)
        self.a.take_commands([Command(start + 101, buttons=2)])
        self.assertTrue(all(rocket is shot for rocket in self.fight.rockets))
        self.assertEqual(weapon.vars[53].value(), 75)
        self.run.match.update(ready * combat.TICK, ready, frames=False)
        self.a.take_commands([Command(start + 102, buttons=2)])
        self.assertEqual(sum(rocket is not shot for rocket in self.fight.rockets), 1)
        self.assertEqual(weapon.vars[53].value(), 50)

    def test_held_secondary_keeps_authored_paced_cadence(self):
        self.fire(1, 2)
        weapon = next(i for i in self.a.body_script.component.instances.values() if i.graph.index == 0x0241)
        self.fire(61, 2)
        self.assertEqual(weapon.vars[53].value(), 75)
        self.fire(1, 2)
        self.assertEqual(weapon.vars[53].value(), 50)

    def test_enemy_rifle_consumes_zarya_shields_before_health(self):
        self.b.mover.position[2] = -5
        before = self.health(self.a)
        for _ in range(12):
            self.run.step({self.b: (0, 0, 1)})
        vitals = self.fight.vitals[self.a.body]
        self.assertLess(vitals.total, before)
        self.assertEqual(vitals.now[combat.HEALTH_POOL], 200)
        self.assertLess(vitals.now[combat.SHIELDS_POOL], 200)

    def test_close_melee_deals_30_once(self):
        self.b.mover.position[2] = 1.5
        self.fire(1, 0x800)
        self.fire(40, 0)
        self.assertEqual(self.health(), 170)

    def melee_state(self):
        graph = next(i for i in self.a.body_script.component.instances.values()
                     if i.graph.index == 0x0043)
        return graph.state(4)

    def test_aimed_melee_miss_uses_swing_without_hit_feedback(self):
        self.b.mover.position[2] = 1.5
        self.run.step({self.a: (16384, 0, 0x800)})
        self.fire(40, 0)
        self.assertEqual(self.melee_state().counter, 1)
        self.assertEqual(self.health(), 200)
        self.assertFalse(self.a.client.messages(combat.HIT))

    def test_out_of_range_melee_uses_swing_without_hit_feedback(self):
        self.b.mover.position[2] = 3.5
        self.fire(1, 0x800)
        self.fire(40, 0)
        self.assertEqual(self.melee_state().counter, 1)
        self.assertEqual(self.health(), 200)
        self.assertFalse(self.a.client.messages(combat.HIT))

    def test_melee_cooldown_rejects_repress_until_authored_recovery(self):
        self.b.mover.position[2] = 1.5
        self.fire(1, 0x800)
        start = self.run.frame
        for offset in range(1, 62):
            self.fire(1, 0x800 if offset % 2 == 0 else 0)
            self.assertEqual(self.melee_state().counter, 1)
            self.assertEqual(self.health(), 170)
        self.fire(1, 0x800)
        self.assertEqual(self.run.frame - start, 62)
        self.assertEqual(self.melee_state().counter, 2)
        self.assertEqual(self.health(), 140)

    def test_melee_cooldown_cannot_be_skipped_with_future_commands(self):
        self.b.mover.position[2] = 1.5
        self.fire(1, 0x800)
        instance = self.melee_state().instance
        cooldown = instance.states[instance.graph.node(14).state].var
        start = self.run.frame
        self.a.take_commands([Command(start + 1), Command(start + 65, buttons=0x800)])
        self.assertEqual(self.fight.tick, start)
        self.assertEqual(self.melee_state().counter, 1)
        self.assertEqual(self.health(), 170)
        self.assertGreater(cooldown.value(), 0.98)

        ready = start + 62
        self.run.match.update((ready - 1) * combat.TICK, ready - 1, frames=False)
        self.a.take_commands([Command(start + 66), Command(start + 67, buttons=0x800)])
        self.assertEqual(self.melee_state().counter, 1)
        self.assertEqual(self.health(), 170)
        self.assertGreater(cooldown.value(), 0)

        self.run.match.update(ready * combat.TICK, ready, frames=False)
        self.assertEqual(cooldown.value(), 0)
        self.a.take_commands([Command(start + 68), Command(start + 69, buttons=0x800)])
        self.assertEqual(self.melee_state().counter, 2)
        self.assertEqual(self.health(), 140)

    def test_held_melee_requires_release_after_recovery(self):
        self.b.mover.position[2] = 1.5
        self.fire(130, 0x800)
        self.assertEqual(self.melee_state().counter, 1)
        self.assertEqual(self.health(), 170)
        self.fire(1, 0)
        self.fire(1, 0x800)
        self.assertEqual(self.melee_state().counter, 2)
        self.assertEqual(self.health(), 140)

    def test_static_wall_blocks_melee_without_hit_feedback(self):
        self.b.mover.position[2] = 1.5
        wall = World([(-10, 0, 0.75, 10, 0, 0.75, 10, 10, 0.75),
                      (-10, 0, 0.75, 10, 10, 0.75, -10, 10, 0.75)])
        with patch.object(self.fight, "_collision", return_value=wall):
            self.fire(1, 0x800)
            self.fire(40, 0)
        self.assertEqual(self.health(), 200)
        self.assertFalse(self.a.client.messages(combat.HIT))

    def test_secondary_projectile_deals_actual_damage(self):
        self.fire(1, 2)
        self.fire(90, 0)
        self.assertLess(self.health(), 200)

    def test_dead_zarya_cannot_continue_beam_damage(self):
        self.fire()
        target = next(t for t in self.fight._targets() if t.owner is self.a)
        self.fight._damage(self.b, target, 500, False, None, self.run.frame)
        before = self.health()
        self.fire(30)
        self.assertEqual(self.health(), before)

    def test_static_geometry_blocks_damage(self):
        wall = World([(-10, 0, 2, 10, 0, 2, 10, 10, 2),
                      (-10, 0, 2, 10, 10, 2, -10, 10, 2)])
        with patch.object(self.fight, "_collision", return_value=wall):
            self.fire()
        self.assertEqual(self.health(), 200)

    def test_body_retirement_discards_beam_clock(self):
        self.fire()
        old = self.a.body
        self.assertIn(old, self.fight.beam_seen)
        self.run.match.switch_hero(self.a, self.a.hero)
        self.assertNotIn(old, self.fight.beam_seen)

    def test_beam_damages_real_bot_actor_and_retains_manual_corpse(self):
        bot_hero, data = next(iter(bots.table()["heroes"].items()))
        bot = bots.Bot(bots.BOT_ENTITY, {
            "hero": bot_hero, "team": 1, "position": [0, 1, 3],
            "rotation": [0, 0, 0, 1], "graphs": [],
        }, data, {})
        bot.retain_corpse = True
        self.run.match.bots.append(bot)
        self.fire()
        self.assertAlmostEqual(self.health(bot), bot.health - 60)
        self.assertEqual(self.health(), 200)
        self.fire(180)
        self.assertTrue(bot.dead)
        self.assertFalse(any(who is bot for _, who in self.fight.respawns))


class ZaryaPersonalBarrierTests(unittest.TestCase):
    setUp = ZaryaBeamTests.setUp
    fire = ZaryaBeamTests.fire
    health = ZaryaBeamTests.health

    def barrier_instance(self):
        return next(i for i in self.a.body_script.component.instances.values()
                    if i.graph.index == 0x0244)

    def activate(self):
        self.run.step({self.a: (0, 0, 8)})
        self.assertTrue(self.barrier_instance().state(2).active)

    def hit(self, amount):
        target = next(t for t in self.fight._targets() if t.owner is self.a)
        self.fight._damage(self.b, target, amount, False, None, self.run.frame)

    def energy(self):
        return self.a.body_script.component.entity_var(87).value()

    def test_actual_rifle_shots_are_blocked_and_generate_energy(self):
        self.b.mover.position[2] = -5
        self.activate()
        for _ in range(12):
            self.run.step({self.b: (0, 0, 1)})
        self.assertEqual(self.health(self.a), 400)
        self.assertGreater(self.energy(), 0)
        self.assertFalse(self.b.client.messages(combat.HIT))

    def test_exact_capacity_blocks_200_and_authored_message_gives_40_energy(self):
        self.activate()
        self.hit(100)
        self.hit(100)
        self.run.step({})
        self.assertEqual(self.health(self.a), 400)
        self.assertEqual(self.energy(), 40)
        energy = next(i for i in self.a.body_script.component.instances.values()
                      if i.graph.index == 0x0242)
        self.assertEqual(energy.vars[392].value(), expr.Entity(self.b.body))
        self.assertFalse(self.barrier_instance().state(2).active)
        self.assertGreater(self.barrier_instance().vars[61].value(), 9.9)

    def test_breaking_event_has_no_overflow_and_next_event_deals_damage(self):
        self.activate()
        self.hit(500)
        self.assertEqual(self.health(self.a), 400)
        self.hit(20)
        self.assertEqual(self.health(self.a), 380)
        self.run.step({})
        self.assertEqual(self.energy(), 40)

    def test_repeated_press_does_not_refill_current_activation(self):
        self.activate()
        self.hit(150)
        self.run.step({})
        self.run.step({self.a: (0, 0, 8)})
        self.hit(100)
        self.hit(20)
        self.assertEqual(self.health(self.a), 380)
        self.run.step({})
        self.assertEqual(self.energy(), 40)

    def test_duration_expires_and_damage_reaches_body(self):
        self.activate()
        self.fire(126, 0)
        self.assertFalse(self.barrier_instance().state(2).active)
        self.hit(20)
        self.assertEqual(self.health(self.a), 380)

    def test_cooldown_rejects_press_then_allows_new_activation(self):
        self.activate()
        self.hit(200)
        self.run.step({})
        counter = self.barrier_instance().state(2).counter
        self.run.step({self.a: (0, 0, 8)})
        self.assertFalse(self.barrier_instance().state(2).active)
        self.assertEqual(self.barrier_instance().state(2).counter, counter)
        self.fire(630, 0)
        self.activate()
        self.assertGreater(self.barrier_instance().state(2).counter, counter)
        self.hit(100)
        self.assertEqual(self.health(self.a), 400)

    def test_held_button_cannot_bypass_cooldown(self):
        self.activate()
        self.hit(200)
        self.fire(80, 8)
        self.assertFalse(self.barrier_instance().state(2).active)
        self.assertEqual(self.barrier_instance().state(2).counter, 1)

    def test_authored_energy_message_caps_at_100(self):
        self.activate()
        self.a.body_script.component.set(87, 90.0)
        self.hit(100)
        self.run.step({})
        self.assertEqual(self.energy(), 100)

    def test_authored_energy_decays_after_absorption(self):
        self.activate()
        self.hit(100)
        self.run.step({})
        self.assertEqual(self.energy(), 20)
        self.fire(380, 0)
        self.assertGreater(self.energy(), 0)
        self.assertLess(self.energy(), 20)

    def test_body_replacement_drops_absorbed_pool(self):
        self.activate()
        self.hit(100)
        old = self.a.body
        self.run.match.switch_hero(self.a, self.a.hero)
        self.assertNotIn(old, self.fight.zarya_barriers.active)
        self.hit(20)
        self.assertEqual(self.health(self.a), 380)


class ZaryaProjectedBarrierTests(unittest.TestCase):
    def setUp(self):
        self.run = Run(names=("Zarya", "Ally", "Enemy"))
        self.a, self.b, self.c = self.run.players
        self.fight = self.run.match.combat
        self.run.match.bots.clear()
        self.run.match._pending_bot_scripts.clear()
        self.run.match.switch_hero(self.a, heroes()[ZARYA])
        self.b.team = self.a.team
        self.c.team = 1
        for player, z in ((self.a, 0), (self.b, 5), (self.c, 10)):
            player.mover.position[:] = [0, 1, z]

    fire = ZaryaBeamTests.fire
    health = ZaryaBeamTests.health

    def instance(self):
        return next(i for i in self.a.body_script.component.instances.values()
                    if i.graph.index == 0x025C)

    def cast(self):
        self.run.step({self.a: (0, 0, 16)})

    def hit(self, owner, amount):
        target = next(t for t in self.fight._targets() if t.owner is owner)
        self.fight._damage(self.c, target, amount, False, None, self.run.frame)

    def energy(self):
        return self.a.body_script.component.entity_var(87).value()

    def test_command_selects_ally_and_creates_authored_pet_without_damage(self):
        self.cast()
        self.assertTrue(self.instance().state(10).active)
        self.assertEqual(self.instance().vars[536].value(), expr.Entity(self.b.body))
        self.assertEqual(self.health(), 200)
        self.assertEqual(self.health(self.c), 200)

    def test_enemy_target_is_rejected_without_cooldown_or_damage(self):
        self.b.team = 1
        self.cast()
        self.assertFalse(self.instance().state(10).active)
        self.assertEqual(self.instance().vars[61].value(), 0)
        self.assertEqual(self.health(), 200)

    def test_out_of_range_target_is_rejected(self):
        self.b.mover.position[2] = 31
        self.cast()
        self.assertFalse(self.instance().state(10).active)

    def test_aimed_miss_is_rejected(self):
        self.run.step({self.a: (16384, 0, 16)})
        self.assertFalse(self.instance().state(10).active)

    def test_static_wall_rejects_target(self):
        wall = World([(-10, 0, 2, 10, 0, 2, 10, 10, 2),
                      (-10, 0, 2, 10, 10, 2, -10, 10, 2)])
        with patch.object(self.fight, "_collision", return_value=wall):
            self.cast()
        self.assertFalse(self.instance().state(10).active)

    def test_dead_ally_is_rejected(self):
        self.hit(self.b, 500)
        self.cast()
        self.assertFalse(self.instance().state(10).active)

    def test_self_is_not_a_projected_target(self):
        self.b.mover.position[2] = 40
        self.cast()
        self.assertFalse(self.instance().state(10).active)
        self.hit(self.a, 20)
        self.assertEqual(self.health(self.a), 380)

    def test_actual_enemy_rifle_is_blocked_and_caster_gets_energy(self):
        self.cast()
        for _ in range(12):
            self.run.step({self.c: (-32768, 0, 1)})
        self.assertEqual(self.health(), 200)
        self.assertGreater(self.energy(), 0)
        self.assertIsNone(self.b.body_script.component.entity_var(87).value())

    def test_projected_capacity_breaks_and_next_hit_damages_ally(self):
        self.cast()
        self.hit(self.b, 500)
        self.assertEqual(self.health(), 200)
        self.hit(self.b, 20)
        self.assertEqual(self.health(), 180)
        self.run.step({})
        self.assertEqual(self.energy(), 40)
        self.assertFalse(self.instance().state(10).active)
        self.assertGreater(self.instance().vars[61].value(), 7.9)

    def test_duration_ends_protection(self):
        self.cast()
        self.fire(126, 0)
        self.assertFalse(self.instance().state(10).active)
        self.hit(self.b, 20)
        self.assertEqual(self.health(), 180)

    def test_cooldown_rejects_recast_then_allows_next_cast(self):
        self.cast()
        self.hit(self.b, 200)
        self.run.step({})
        counter = self.instance().state(10).counter
        self.cast()
        self.assertFalse(self.instance().state(10).active)
        self.assertEqual(self.instance().state(10).counter, counter)
        self.fire(505, 0)
        self.cast()
        self.assertTrue(self.instance().state(10).active)
        self.assertGreater(self.instance().state(10).counter, counter)

    def test_held_button_cannot_refill_or_bypass_cooldown(self):
        self.cast()
        self.hit(self.b, 200)
        self.fire(30, 16)
        self.assertFalse(self.instance().state(10).active)
        self.assertEqual(self.instance().state(10).counter, 1)

    def test_changing_aim_does_not_retarget_active_barrier(self):
        self.cast()
        self.c.team = self.a.team
        self.c.mover.position[:] = [5, 1, 0]
        self.run.step({self.a: (16384, 0, 0)})
        self.assertEqual(self.instance().vars[536].value(), expr.Entity(self.b.body))
        self.hit(self.b, 20)
        self.assertEqual(self.health(), 200)

    def test_team_change_ends_protection(self):
        self.cast()
        self.b.team = 1
        self.hit(self.b, 20)
        self.assertEqual(self.health(), 180)

    def test_replacement_body_is_not_protected_by_old_selection(self):
        self.cast()
        self.run.match.switch_hero(self.b, self.b.hero)
        self.hit(self.b, 20)
        self.assertEqual(self.health(), 180)

    def test_caster_death_ends_reconstructed_protection(self):
        self.cast()
        self.hit(self.b, 20)
        self.assertEqual(self.health(), 200)
        storage = self.fight.zarya_barriers.projected
        self.assertEqual(storage[self.a.body].remaining, 180)
        self.hit(self.a, 500)
        self.assertTrue(self.fight.is_dead(self.a))
        self.assertNotIn(self.a.body, storage)
        self.hit(self.b, 20)
        self.assertEqual(self.health(), 180)


if __name__ == "__main__":
    unittest.main()
