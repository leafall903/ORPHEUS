"""Actual Mercy hits against Zarya's personal and projected barriers."""

import unittest
from unittest.mock import Mock

from test_game_combat import Run, aim
from test_game_mercy_blaster_critical import equip
import test_game_mercy_npc as npc_tests

from ow174.game import combat, heavy_charge, npc, npc_control, phase_defenses, stats, zarya_barriers
from ow174.game.commands import FIRE
from ow174.game.content import heroes
from ow174.game.script import expr
from ow174.game.script.driver import ULT_CHARGE

MELEE = 0x800
MERCY = 0x02E0000000000004


class MercyZaryaBarrierTests(unittest.TestCase):
    def encounter(self, projected, *, close=False, ally_hero=None):
        run = Run(names=("Mercy", "Zarya", "Ally"))
        mercy, zarya, ally = run.players
        fight = run.match.combat
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        run.match.switch_hero(mercy, heroes()[MERCY])
        run.match.switch_hero(zarya, heroes()[zarya_barriers.HERO])
        if ally_hero is not None:
            self.assertTrue(run.match.switch_hero(ally, heroes()[ally_hero]))
        zarya.team = ally.team = 1
        mercy.team = 0
        mercy.mover.position[:] = [0, 1, 3.5 if close else 0]
        zarya.mover.position[:] = [3 if projected else 0, 1, 5]
        ally.mover.position[:] = [0, 1, 5 if projected else 20]
        victim = ally if projected else zarya
        equip(run, mercy)
        cast = (-16384, 0, 16) if projected else (0, 0, 8)
        run.step({zarya: cast})
        instance = next(i for i in zarya.body_script.component.instances.values()
                        if i.graph.index == (0x025C if projected else 0x0244))
        self.assertTrue(instance.state(10 if projected else 2).active)
        target = next(t for t in fight._targets() if t.owner is victim)
        return run, mercy, zarya, victim, fight.vitals_of(target), instance

    def attack(self, run, mercy, victim, button, frames=25):
        x, y, z = victim.mover.position
        yaw, pitch = aim(run.match.combat._eye(mercy), (x, y + 0.8, z))
        run.step({mercy: (yaw, pitch, button)})
        for _ in range(frames):
            run.step({})

    def test_personal_and_projected_barriers_block_actual_bullet_and_melee(self):
        for projected in (False, True):
            for button, amount in ((FIRE, 20), (MELEE, 30)):
                with self.subTest(projected=projected, button=button):
                    run, mercy, zarya, victim, health, instance = self.encounter(
                        projected, close=button == MELEE)
                    before = health.total
                    self.attack(run, mercy, victim, button, frames=40)
                    self.assertEqual(health.total, before)
                    self.assertEqual(mercy.stats.value(stats.HERO_DAMAGE), 0)
                    host = run.match.combat.zarya_barriers
                    storage = host.projected if projected else host.active
                    self.assertEqual(storage[zarya.body].remaining, 200 - amount)
                    component = zarya.body_script.component
                    self.assertAlmostEqual(component.entity_var(87).value(), amount * 0.2)
                    energy = next(i for i in component.instances.values() if i.graph.index == 0x0242)
                    self.assertEqual(energy.vars[392].value(), expr.Entity(mercy.body))
                    for _ in range(140):
                        run.step({})
                    self.assertFalse(instance.state(10 if projected else 2).active)
                    self.attack(run, mercy, victim, button, frames=40)
                    self.assertEqual(health.total, before - amount)

    def test_projected_barrier_spends_durability_before_frontal_melee_deflect(self):
        run, mercy, zarya, genji, health, barrier_state = self.encounter(
            True, close=True, ally_hero=0x02E0000000000029,
        )
        fight = run.match.combat
        defense, = (i for i in genji.body_script.component.instances.values() if i.graph.index == 0x03A3)
        filter_state = defense.node_state(defense.graph.node(107))
        component = mercy.body_script.component
        component.set(12918, 0.0)
        charge = component.entity_var(ULT_CHARGE)
        before = health.total, mercy.stats.value(stats.HERO_DAMAGE), charge.value()
        facing = (-32768, 0, 0)

        def swing():
            x, y, z = genji.mover.position
            direction = aim(fight._eye(mercy), (x, y + 0.8, z))
            run.step({mercy: (*direction, MELEE), genji: facing})
            for _ in range(25):
                run.step({genji: facing})

        for _ in range(60):
            run.step({})
        run.step({genji: (-32768, 0, 0x10)})
        for _ in range(2):
            run.step({genji: facing})
        self.assertTrue(filter_state.active)
        self.assertTrue(barrier_state.state(10).active)
        self.assertTrue(fight.zarya_barriers.protects(genji))
        swing()
        self.assertTrue(filter_state.active)
        self.assertTrue(barrier_state.state(10).active)
        barrier = fight.zarya_barriers.projected[zarya.body]
        self.assertEqual(barrier.remaining, 170)
        self.assertAlmostEqual(zarya.body_script.component.entity_var(87).value(), 6)
        self.assertEqual((health.total, mercy.stats.value(stats.HERO_DAMAGE), charge.value()), before)

        for _ in range(100):
            if not barrier_state.state(10).active:
                break
            run.step({genji: facing})
        self.assertFalse(barrier_state.state(10).active)
        self.assertTrue(filter_state.active)
        swing()
        self.assertTrue(filter_state.active)
        self.assertEqual((health.total, mercy.stats.value(stats.HERO_DAMAGE), charge.value()), before)

        for _ in range(150):
            if not filter_state.active:
                break
            run.step({})
        self.assertFalse(filter_state.active)
        swing()
        self.assertEqual(
            (health.total, mercy.stats.value(stats.HERO_DAMAGE), charge.value()),
            (before[0] - 30, before[1] + 30, before[2] + 30),
        )

    def test_break_blocks_full_hit_then_rejects_early_recast(self):
        for projected in (False, True):
            with self.subTest(projected=projected):
                run, mercy, zarya, victim, health, instance = self.encounter(projected)
                self.attack(run, mercy, victim, FIRE)
                host = run.match.combat.zarya_barriers
                storage = host.projected if projected else host.active
                barrier = storage[zarya.body]
                barrier.remaining = 10.0  # Arrange the final ten durability before an actual hit.
                before = health.total
                self.attack(run, mercy, victim, FIRE)
                self.assertEqual(barrier.remaining, 0)
                self.assertEqual(health.total, before)
                self.assertEqual(zarya.body_script.component.entity_var(87).value(), 6)
                self.assertFalse(instance.state(10 if projected else 2).active)
                self.assertGreater(instance.vars[61].value(), 0)
                run.step({zarya: (-16384, 0, 16) if projected else (0, 0, 8)})
                self.assertFalse(instance.state(10 if projected else 2).active)
                self.attack(run, mercy, victim, FIRE)
                self.assertEqual(health.total, before - 20)

    def test_in_flight_critical_crosses_wraith_and_breaks_rear_barrier(self):
        run = Run(names=("Mercy", "Reaper", "Zarya"))
        mercy, reaper, zarya = run.players
        fight = run.match.combat
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        for player, hero, team, z in ((mercy, MERCY, 0, 0),
                                       (reaper, 0x02E0000000000002, 1, 15),
                                       (zarya, zarya_barriers.HERO, 1, 20)):
            self.assertTrue(run.match.switch_hero(player, heroes()[hero]))
            player.team = team
            player.mover.position[:] = (0, 1, z)
            player.mover.body = None
        equip(run, mercy)
        component = mercy.body_script.component
        component.set(12918, 0.0)
        charge = component.entity_var(ULT_CHARGE)
        before_charge = charge.value()
        targets = {t.owner: t for t in fight._targets()}
        front, rear = (fight.vitals_of(targets[player]) for player in (reaper, zarya))
        before_front, before_rear = front.total, rear.total
        direction = aim(fight._eye(mercy), (0, 1 + targets[zarya].eye, 20))
        run.step({mercy: (*direction, FIRE)})
        projectile, = fight.rockets
        self.assertFalse(phase_defenses.active(reaper))
        self.assertNotIn(zarya.body, fight.zarya_barriers.active)
        run.step({reaper: (0, 0, 8), zarya: (0, 0, 8)})
        self.assertTrue(phase_defenses.active(reaper))
        self.assertTrue(fight.zarya_barriers.protects(zarya))
        self.assertIn(projectile, fight.rockets)
        for _ in range(35):
            run.step({})
        barrier = fight.zarya_barriers.active[zarya.body]
        self.assertEqual(barrier.remaining, 160)
        energy = zarya.body_script.component
        self.assertEqual(energy.entity_var(87).value(), 8)
        self.assertEqual((front.total, rear.total), (before_front, before_rear))
        self.assertEqual(charge.value(), before_charge)

        barrier.remaining = 10
        run.step({mercy: (*direction, FIRE)})
        for _ in range(35):
            run.step({})
        self.assertTrue(phase_defenses.active(reaper))
        self.assertEqual((front.total, rear.total), (before_front, before_rear))
        self.assertEqual(barrier.remaining, 0)
        self.assertFalse(fight.zarya_barriers.protects(zarya))
        self.assertEqual(energy.entity_var(87).value(), 10)
        self.assertEqual(mercy.stats.value(stats.HERO_DAMAGE), 0)
        self.assertEqual(charge.value(), before_charge)
        self.assertFalse(mercy.client.messages(combat.HIT))

        run.step({mercy: (*direction, FIRE)})
        for _ in range(35):
            run.step({})
        self.assertTrue(phase_defenses.active(reaper))
        self.assertEqual((front.total, rear.total), (before_front, before_rear - 40))
        self.assertEqual(mercy.stats.value(stats.HERO_DAMAGE), 40)
        self.assertEqual(charge.value() - before_charge, 40)
        marker, = mercy.client.messages(combat.HIT)
        self.assertEqual((marker["+0x78"]["+0x4"], marker["+0x78"]["+0x8"]), (40, combat.CRITICAL))
        self.assertFalse(fight.rockets)
        self.assertFalse(fight.projectiles.shots)

    def test_projected_barrier_on_mercy_blocks_actual_enemy_rifle_then_expires(self):
        run = Run(names=("Rifle", "Zarya", "Mercy"))
        enemy, zarya, mercy = run.players
        fight = run.match.combat
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        run.match.switch_hero(mercy, heroes()[MERCY])
        run.match.switch_hero(zarya, heroes()[zarya_barriers.HERO])
        enemy.team, zarya.team, mercy.team = 0, 1, 1
        enemy.mover.position[:] = [0, 1, 0]
        zarya.mover.position[:] = [3, 1, 5]
        mercy.mover.position[:] = [0, 1, 5]
        run.step({zarya: (-16384, 0, 16)})
        target = next(t for t in fight._targets() if t.owner is mercy)
        health = fight.vitals_of(target)
        self.attack(run, enemy, mercy, FIRE, frames=15)
        barrier = fight.zarya_barriers.projected[zarya.body]
        self.assertLess(barrier.remaining, 200)
        self.assertEqual(health.total, 200)
        self.assertEqual(enemy.stats.value(stats.HERO_DAMAGE), 0)
        for _ in range(130):
            run.step({})
        self.attack(run, enemy, mercy, FIRE, frames=15)
        self.assertLess(health.total, 200)
        self.assertGreater(enemy.stats.value(stats.HERO_DAMAGE), 0)


    def projected_npc_encounter(self, name, distance, *, shield=True):
        run = Run(names=("Mercy", "Zarya"))
        mercy, zarya = run.players
        fight = run.match.combat
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        run.match.switch_hero(mercy, heroes()[MERCY])
        run.match.switch_hero(zarya, heroes()[zarya_barriers.HERO])
        mercy.team = zarya.team = 0
        mercy.mover.position[:] = [0, 1, 0]
        zarya.mover.position[:] = [3, 1, 0]
        mercy.client.bot_stream = Mock(return_value=None)
        zarya.client.bot_stream = Mock(return_value=None)
        fixture = npc_tests.MercyNpcTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        definition = next(d for d in fixture.catalog.values() if d.name == name)
        bot = npc.spawn(run.match, definition, (0, 1, distance), team=1)
        yaw, pitch = aim(fight._eye(mercy), (0, 2, distance))
        run.step({mercy: (yaw, pitch, 0), zarya: (-16384, 0, 16 if shield else 0)})
        return run, mercy, zarya, definition, bot

    def test_projected_barrier_absorbs_assassin_contact_without_pinning_mercy(self):
        run, mercy, zarya, _, bot = self.projected_npc_encounter("Assassin", 2.5)
        fight = run.match.combat
        self.assertEqual(npc_control.start(run.match, mercy), bot.entity)
        for _ in range(60):
            run.step({})
            if zarya.body in fight.zarya_barriers.projected:
                break
        target = next(t for t in fight._targets() if t.owner is mercy)
        self.assertEqual(fight.zarya_barriers.projected[zarya.body].remaining, 175)
        self.assertEqual(fight.vitals_of(target).total, 200)
        self.assertFalse(fight.is_stunned(mercy))
        self.assertFalse(fight.pins)

    def test_projected_barrier_absorbs_heavy_impact_without_stunning_mercy(self):
        run, mercy, zarya, definition, bot = self.projected_npc_encounter("Heavy Assault", 0.7)
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is mercy)
        health = fight.vitals_of(target)
        for remaining, gained in ((25, 35), (0, 40)):
            self.assertTrue(fight.zarya_barriers.protects(mercy))
            self.assertEqual(npc_control.start(run.match, mercy, charge=True), bot.entity)
            for _ in range(80):
                run.step({})
                barrier = fight.zarya_barriers.projected.get(zarya.body)
                if barrier is not None and barrier.remaining == remaining:
                    break
            self.assertEqual(fight.zarya_barriers.projected[zarya.body].remaining, remaining)
            self.assertEqual(health.total, 200)
            self.assertFalse(fight.is_stunned(mercy))
            self.assertFalse(fight.heavy_pins)
            self.assertAlmostEqual(zarya.body_script.component.entity_var(87).value(), gained)
            energy = next(i for i in zarya.body_script.component.instances.values() if i.graph.index == 0x0242)
            self.assertEqual(energy.vars[392].value(), expr.Entity(bot.entity))
            npc.remove_spawned(run.match)
            if remaining:
                bot = npc.spawn(run.match, definition, (0, 1, 0.7), team=1)

        self.assertFalse(fight.zarya_barriers.protects(mercy))
        replacement = npc.spawn(run.match, definition, (0, 1, 0.7), team=1)
        self.assertEqual(npc_control.start(run.match, mercy, charge=True), replacement.entity)
        for _ in range(80):
            run.step({})
            if fight.is_stunned(mercy):
                break
        self.assertEqual(health.total, 25)
        self.assertEqual(fight.stuns[mercy.body] - run.frame, 125)
        self.assertFalse(fight.heavy_pins)

    def test_projected_barrier_preserves_guardian_angel_during_heavy_impact(self):
        run, mercy, zarya, _, bot = self.projected_npc_encounter("Heavy Assault", 9)
        fight = run.match.combat
        self.assertEqual(npc_control.start(run.match, mercy, charge=True), bot.entity)
        zarya.mover.position[:] = (12, 1, 0)
        zarya.mover.body = None
        charge = heavy_charge.charge(bot)
        for _ in range(70):
            run.step({})
            if heavy_charge.travelling(charge):
                break
        self.assertTrue(heavy_charge.travelling(charge))
        guardian = next(instance for instance in mercy.body_script.component.instances.values()
                        if instance.graph.index == 0x006E)
        flight = guardian.state(0)
        x, y, z = zarya.mover.position
        yaw, pitch = aim(fight._eye(mercy), (x, y + 1, z))
        run.step({mercy: (yaw, pitch, 0x08)})
        self.assertTrue(flight.active)
        activation = flight.counter

        x, y, z = mercy.mover.position
        bot.position = (x, y, z + 0.7)
        run.step({mercy: (yaw, pitch, 0x08)})
        self.assertEqual(fight.zarya_barriers.projected[zarya.body].remaining, 25)
        self.assertEqual(fight.vitals[mercy.body].total, 200)
        self.assertFalse(fight.is_stunned(mercy))
        self.assertTrue(flight.active)
        self.assertEqual(flight.counter, activation)
        self.assertGreater(mercy.mover.position[0], x)

    def test_projected_barrier_preserves_staff_healing_during_heavy_impact(self):
        for shield in (False, True):
            with self.subTest(shield=shield):
                run, mercy, zarya, _, bot = self.projected_npc_encounter("Heavy Assault", 0.7, shield=shield)
                fight = run.match.combat
                patient = next(target for target in fight._targets() if target.owner is zarya)
                mercy_target = next(target for target in fight._targets() if target.owner is mercy)
                health = fight.vitals_of(mercy_target)
                fight._damage(bot, patient, 200, False, None, run.frame)
                self.assertEqual(npc_control.start(run.match, mercy, charge=True), bot.entity)
                staff = next(instance for instance in mercy.body_script.component.instances.values()
                             if instance.graph.index == 0x006B)
                yaw, pitch = aim(fight._eye(mercy), (3, 2, 0))

                for _ in range(20):
                    run.step({mercy: (yaw, pitch, FIRE)})
                self.assertGreater(mercy.stats.value(stats.HEALING), 0)
                hold = staff.state(5)
                self.assertTrue(hold.active)
                generation = hold.counter
                self.assertEqual(health.total, 200)

                for _ in range(60):
                    run.step({mercy: (yaw, pitch, FIRE)})
                    barrier = fight.zarya_barriers.projected.get(zarya.body)
                    if (barrier is not None and barrier.remaining == 25) or fight.is_stunned(mercy):
                        break
                if shield:
                    self.assertIsNotNone(barrier)
                    self.assertEqual(barrier.remaining, 25)
                    self.assertEqual(health.total, 200)
                    self.assertFalse(fight.is_stunned(mercy))
                    self.assertTrue(hold.active)
                else:
                    self.assertEqual(health.total, 25)
                    self.assertTrue(fight.is_stunned(mercy))

                npc_control.stop(run.match)
                healed = mercy.stats.value(stats.HEALING)
                for _ in range(20):
                    run.step({mercy: (yaw, pitch, FIRE)})
                if shield:
                    self.assertTrue(hold.active)
                    self.assertEqual(hold.counter, generation)
                    self.assertAlmostEqual(mercy.stats.value(stats.HEALING) - healed, 17.6)
                else:
                    self.assertFalse(hold.active)
                    self.assertEqual(mercy.stats.value(stats.HEALING), healed)
                    for _ in range(125):
                        run.step({})
                    self.assertFalse(fight.is_stunned(mercy))
                    for _ in range(20):
                        run.step({mercy: (yaw, pitch, FIRE)})
                    self.assertTrue(hold.active)
                    self.assertGreater(hold.counter, generation)
                    self.assertAlmostEqual(mercy.stats.value(stats.HEALING) - healed, 17.6)
