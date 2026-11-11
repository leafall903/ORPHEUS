"""Actual Zarya attacks against the build104319 Fortify and Transcendence graphs."""

import unittest

import test_game_zarya_npcs as npc_fixtures
from test_game_combat import Run, aim

from ow174.game import combat, defenses, npc
from ow174.game.collision import flat_world
from ow174.game.commands import Command
from ow174.game.content import SOLDIER, heroes


class ZaryaDefensesTests(unittest.TestCase):
    def scene(self, hero, *, distance=5, activate_defense=True):
        self.run = Run(names=("Zarya", "Defender", "Behind"))
        self.a, self.b, self.behind = self.run.players
        self.fight = self.run.match.combat
        self.run.match.bots.clear()
        self.run.match._pending_bot_scripts.clear()
        self.run.match.collision_world = flat_world(0)
        self.run.match.switch_hero(self.a, heroes()[0x02E0000000000068])
        self.run.match.switch_hero(self.b, heroes()[hero])
        self.b.team = self.behind.team = 1 - self.a.team
        for player, pos in ((self.a, (0, 0, 0)), (self.b, (0, 0, distance)),
                            (self.behind, (0, 0, distance + 5))):
            player.mover.position[:] = pos
            player.mover.body = None
            player.body_script.ult_seconds = 0
        self.step(20)
        if activate_defense and hero == defenses.ZENYATTA:
            self.b.body_script.set_ult_charge(self.b.body_script.component.entity_var(338).value())
            self.step(defense=0x20)
            self.assertTrue(defenses.invulnerable(self.b))
        elif activate_defense and hero == defenses.ORISA:
            self.step(defense=8)
            self.assertAlmostEqual(defenses.fortify_scale(self.b), 0.6, places=6)

    def step(self, frames=1, attack=0, defense=0):
        for _ in range(frames):
            self.run.step({self.a: (0, 0, attack), self.b: (-32768, 0, defense)})

    def health(self, player=None):
        player = self.b if player is None else player
        target = next(t for t in self.fight._targets() if t.owner is player)
        return self.fight.vitals_of(target).total

    def charge(self):
        return self.a.body_script.component.entity_var(342).value()

    def attack(self, button):
        if button == 1:
            self.step(51, attack=1)
        else:
            self.step(attack=button)
            self.step(60)

    def field(self):
        self.a.body_script.set_ult_charge(2100)
        self.step(attack=0x20)
        for _ in range(100):
            if self.fight.zarya_gravitons.fields:
                break
            self.step()
        field, = self.fight.zarya_gravitons.fields
        return field

    def activate_during_gap(self, hero, tick):
        match = self.run.match
        match.tick = tick
        if hero == defenses.ZENYATTA:
            cost = self.b.body_script.component.entity_var(338).value()
            self.b.body_script.set_ult_charge(cost)
        button = 8 if hero == defenses.ORISA else 32
        self.b.take_commands([Command(tick + 500, yaw=-32768, buttons=button)])
        active = defenses.fortify_state if hero == defenses.ORISA else defenses.invulnerable
        self.assertTrue(active(self.b))

    def test_late_defense_preserves_graviton_impact_and_prior_damage(self):
        for hero, direct, scale in ((defenses.ORISA, 2.5, 0.6), (defenses.ZENYATTA, 5, 0)):
            with self.subTest(hero=hero):
                self.scene(hero, activate_defense=False)
                self.a.body_script.set_ult_charge(2100)
                self.step(attack=32)
                self.step()
                rocket, = self.fight.rockets
                self.assertEqual(rocket.last_tick, self.run.frame)
                activation = self.run.frame + 32
                self.activate_during_gap(hero, activation)
                before = self.health()
                self.run.match.update(activation * combat.TICK, activation, frames=False)
                field, = self.fight.zarya_gravitons.fields
                landed = field.end_tick - 250
                self.assertLess(landed, activation)
                self.assertGreater(field.position[2], 4)
                expected = direct + (activation - landed - 1 + scale) * 0.08
                self.assertAlmostEqual(before - self.health(), expected, places=4)

    def test_late_defense_changes_only_subsequent_graviton_ticks(self):
        for hero, scale in ((defenses.ORISA, 0.6), (defenses.ZENYATTA, 0)):
            with self.subTest(hero=hero):
                self.scene(hero, activate_defense=False)
                field = self.field()
                activation = field.last_tick + 120
                self.activate_during_gap(hero, activation)
                before = self.health()
                tick = activation + 10
                self.run.match.update(tick * combat.TICK, tick, frames=False)
                self.assertAlmostEqual(before - self.health(), (119 + 11 * scale) * 0.08, places=4)
                pulled = self.b.body in self.fight.zarya_gravitons.player_pulls
                self.assertEqual(pulled, hero == defenses.ZENYATTA)

    def test_future_neutral_command_cannot_end_defenses_during_graviton(self):
        for hero, scale in ((defenses.ORISA, 0.6), (defenses.ZENYATTA, 0)):
            with self.subTest(hero=hero):
                self.scene(hero, activate_defense=False)
                self.field()
                script = self.b.body_script
                button = 8
                if hero == defenses.ZENYATTA:
                    script.set_ult_charge(script.component.entity_var(338).value())
                    button = 32
                self.step(defense=button)
                tick = self.run.frame
                deadline = script.world.phase_deadline
                self.assertGreater(deadline, tick + 8)
                self.b.take_commands([Command(tick + 600)])
                before = self.health()
                self.run.match.update((tick + 8) * combat.TICK, tick + 8, frames=False)
                self.assertAlmostEqual(before - self.health(), 8 * 0.08 * scale, places=5)
                self.assertEqual(self.b.body in self.fight.zarya_gravitons.player_pulls,
                                 hero == defenses.ZENYATTA)
                active = defenses.fortify_state if hero == defenses.ORISA else defenses.invulnerable
                for tick in (deadline - 1, deadline):
                    self.run.match.update(tick * combat.TICK, tick, frames=False)
                    self.assertEqual(bool(active(self.b)), tick < deadline)

    def test_sparse_secondary_uses_fortify_at_explosion_for_damage_and_impulse(self):
        for window, damage, lift, push in (
            ("late", 42, 8, 5),
            ("active", 23.2, 0, 0),
            ("active_future", 23.2, 0, 0),
            ("expired", 42, 8, 5),
        ):
            with self.subTest(window=window):
                self.scene(defenses.ORISA, activate_defense=window != "late")
                match = self.run.match
                launch = self.run.frame + 1
                if window == "expired":
                    launch = self.b.body_script.world.phase_deadline + 1
                elif window == "active_future":
                    self.b.take_commands([Command(self.run.frame + 600)])
                before = self.health()
                match.update(launch * combat.TICK, launch, frames=False)
                self.a.take_commands([Command(launch, buttons=2)])
                match.update((launch + 1) * combat.TICK, launch + 1, frames=False)
                rocket, = self.fight.rockets
                self.assertEqual(rocket.last_tick, launch + 1)
                tick = launch + 32
                if window == "late":
                    self.activate_during_gap(defenses.ORISA, tick)
                match.update(tick * combat.TICK, tick, frames=False)
                self.assertFalse(self.fight.rockets)
                self.assertAlmostEqual(before - self.health(), damage, places=4)
                self.assertAlmostEqual(self.b.mover.velocity[1], lift, places=5)
                self.assertAlmostEqual(self.b.mover.velocity[2], push, places=5)
                velocity = self.b.mover.velocity
                match.update(tick * combat.TICK, tick, frames=False)
                self.assertEqual(self.b.mover.velocity, velocity)

    def test_fortify_scales_beam_secondary_and_melee_before_armour(self):
        for button, loss, distance in ((1, 25.2, 5), (2, 23.2, 5), (0x800, 13, 2)):
            with self.subTest(button=button):
                self.scene(defenses.ORISA, distance=distance)
                before = self.health()
                self.attack(button)
                self.assertAlmostEqual(before - self.health(), loss, places=4)
                self.assertAlmostEqual(self.charge(), loss, places=4)
                self.assertEqual(self.b.mover.velocity, (0, 0, 0))

    def test_fortify_scales_field_damage_and_blocks_pull_until_expiry(self):
        self.scene(defenses.ORISA)
        self.step(180)
        before = self.health()
        self.field()
        point = tuple(self.b.mover.position)
        self.assertAlmostEqual(before - self.health(), 1.5, places=5)
        self.step(10)
        self.assertEqual(tuple(self.b.mover.position), point)
        self.assertAlmostEqual(before - self.health(), 1.98, places=5)
        deadline = self.run.frame + 100
        while defenses.fortify_scale(self.b) is not None:
            self.assertLess(self.run.frame, deadline)
            self.step()
        self.step(10)
        self.assertNotEqual(tuple(self.b.mover.position), point)
        self.assertEqual(self.charge(), 0)

    def test_fortify_releases_active_pull_on_next_world_tick_without_input(self):
        self.scene(defenses.ORISA, activate_defense=False)
        field = self.field()
        self.step(2)
        self.assertLess(self.b.mover.velocity[2], 0)
        self.step(defense=8)
        self.assertIsNotNone(defenses.fortify_state(self.b))
        position = tuple(self.b.mover.position)
        before = self.health()

        self.run.frame += 1
        self.run.match.update(self.run.frame * combat.TICK, self.run.frame)
        self.assertIn(field, self.fight.zarya_gravitons.fields)
        self.assertNotIn(self.b.body, self.fight.zarya_gravitons.player_pulls)
        self.assertEqual(tuple(self.b.mover.position), position)
        self.assertEqual(self.b.mover.velocity, (0.0, 0.0, 0.0))
        self.assertAlmostEqual(before - self.health(), 0.048, places=5)

    def test_projected_barrier_consumes_full_damage_before_fortify_or_transcendence(self):
        for hero in (defenses.ORISA, defenses.ZENYATTA):
            with self.subTest(hero=hero):
                self.scene(hero)
                caster = self.behind
                self.run.match.switch_hero(caster, heroes()[0x02E0000000000068])
                caster.mover.position[:] = [4, 0, 5]
                caster.mover.body = None
                caster.body_script.ult_seconds = 0
                self.step(20)
                self.run.step({caster: (-16384, 0, 16)})
                instance = next(i for i in caster.body_script.component.instances.values()
                                if i.graph.index == 0x025C)
                self.assertTrue(instance.state(10).active)
                before = self.health()
                self.attack(2)
                self.assertEqual(self.fight.zarya_barriers.projected[caster.body].remaining, 153)
                self.assertEqual(self.health(), before)
                self.assertAlmostEqual(caster.body_script.component.entity_var(87).value(), 9.4, places=5)
                self.assertEqual(self.charge(), 0)
                self.assertFalse(self.a.client.messages(combat.HIT))

    def test_defensive_windows_expire_on_world_clock_without_owner_input(self):
        for hero, duration, active in ((defenses.ORISA, 4, defenses.fortify_state),
                                       (defenses.ZENYATTA, 6, defenses.invulnerable)):
            with self.subTest(hero=hero):
                self.scene(hero)
                deadline = self.b.body_script.world.phase_deadline
                self.assertEqual(deadline - self.run.frame, round(duration / combat.TICK))
                self.run.match.update((deadline - 1) * combat.TICK, deadline - 1)
                self.assertTrue(active(self.b))
                self.run.match.update(deadline * combat.TICK, deadline)
                self.assertFalse(active(self.b))

    def test_fortify_blocks_flashbang_stun_and_rifle_critical(self):
        self.scene(defenses.ORISA)
        self.run.match.switch_hero(self.a, heroes()[0x02E0000000000042])
        self.a.mover.position[:] = [0, 0, 0]
        self.a.mover.body = None
        self.step(20)
        before = self.health()
        self.step(attack=16)
        self.step(35)
        self.assertAlmostEqual(before - self.health(), 10, places=4)
        self.assertFalse(self.fight.is_stunned(self.b))
        self.run.match.switch_hero(self.a, heroes()[SOLDIER])
        self.a.mover.position[:] = [0, 0, 0]
        self.a.mover.body = None
        self.step(20)
        before = self.health()
        self.assertIsNotNone(defenses.fortify_state(self.b))
        angles = aim(self.fight._eye(self.a), self.fight._eye(self.b))
        self.run.step({self.a: (*angles, 1), self.b: (-32768, 0, 0)})
        self.assertAlmostEqual(before - self.health(), 7, places=4)
        self.assertFalse(self.a.client.messages(combat.HIT)[-1]['+0x78']['+0x8'] & combat.CRITICAL)

    @unittest.skipUnless(npc_fixtures.NPC_DATA, "requires actual Assassin graph")
    def test_fortify_reduces_assassin_contact_and_prevents_pin(self):
        fixtures = npc_fixtures.ZaryaFiveNpcTests
        fixtures.setUpClass()
        self.addCleanup(fixtures.tearDownClass)
        self.scene(defenses.ORISA)
        definition = next(d for d in fixtures.catalog.values() if d.name == "Assassin")
        bot = npc.spawn(self.run.match, definition, (0, 0, 2.5), team=self.a.team)
        bot.firing, bot.attack_target = True, self.b.entity
        before = self.health()
        self.step()
        self.assertAlmostEqual(before - self.health(), 10, places=4)
        self.assertNotIn(bot.entity, self.fight.pins)
        self.assertFalse(self.fight.is_stunned(self.b))

    def test_reinhardt_passive_scales_secondary_impulse_without_changing_damage(self):
        self.scene(0x02E0000000000007)
        before = self.health()
        self.step(attack=2)
        for _ in range(60):
            self.run.frame += 1
            self.run.match.update(self.run.frame * combat.TICK, self.run.frame)
            if self.health() < before:
                break
        self.assertAlmostEqual(before - self.health(), 42, places=5)
        self.assertAlmostEqual(self.b.mover.velocity[1], 5.6, places=5)
        self.assertAlmostEqual(self.b.mover.velocity[2], 3.5, places=5)
        self.assertAlmostEqual(self.charge(), 42, places=5)

    def test_transcendence_retains_body_collision_and_denies_damage_and_credit(self):
        for button, distance in ((1, 5), (2, 5), (0x800, 2)):
            with self.subTest(button=button):
                self.scene(defenses.ZENYATTA, distance=distance)
                self.attack(button)
                self.assertEqual(self.health(), 200)
                self.assertEqual(self.health(self.behind), 200)
                self.assertEqual(self.charge(), 0)
                self.assertFalse(self.a.client.messages(combat.HIT))

    def test_transcendence_denies_ultimate_damage_and_expires(self):
        self.scene(defenses.ZENYATTA)
        self.behind.mover.position[0] = 20
        self.behind.mover.body = None
        self.step()
        self.field()
        before = self.b.mover.position[2]
        self.step(20)
        self.assertTrue(defenses.invulnerable(self.b))
        self.assertEqual(self.health(), 200)
        self.assertLess(self.b.mover.position[2], before)
        self.assertFalse(self.a.client.messages(combat.HIT))
        self.assertEqual(self.charge(), 0)
        deadline = self.run.frame + 400
        while defenses.invulnerable(self.b):
            self.assertLess(self.run.frame, deadline)
            self.step()
        self.attack(1)
        self.assertAlmostEqual(self.health(), 140, places=4)
        self.assertAlmostEqual(self.charge(), 60, places=4)
