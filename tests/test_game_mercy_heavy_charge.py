"""Actual Heavy Assault charge movement and graze against Mercy."""

import unittest
from unittest.mock import Mock

import test_game_mercy_npc_geometry as geometry
import test_game_mercy_resurrect as resurrect
from test_game_combat import aim, in_world
from test_game_mercy_blaster_critical import equip, shot
from test_game_mover import FLOOR, wall

from ow174.game import heavy_charge, npc, npc_control, stats
from ow174.game.collision import World
from ow174.game.combat import TICK
from ow174.game.commands import FIRE, JUMP, Command
from ow174.game.content import SOLDIER, heroes
from ow174.game.script import nodes


class MercyHeavyChargeTests(unittest.TestCase):
    def encounter(self, *, blaster=False):
        geo = geometry.MercyNpcGeometryTests()
        geo.setUp()
        self.addCleanup(geo.doCleanups)
        fixture = geo.npc_fixture()
        definition = next(d for d in fixture.catalog.values() if d.name == "Heavy Assault")
        run, mercy, bot, _ = fixture.spawn(definition, 1)
        bot.position = (0, 1, 9)
        fight = run.match.combat
        target = next(t for t in fight._targets() if t.owner is mercy)
        health = fight.vitals_of(target)
        if blaster:
            equip(run, mercy)
        fixture.tick(run, mercy, bot, 15, buttons=0)
        self.assertEqual(npc_control.start(run.match, mercy, charge=True), bot.entity)
        return run, mercy, bot, health, heavy_charge.charge(bot)

    def travel(self, run, instance):
        for _ in range(70):
            run.step({})
            if heavy_charge.travelling(instance):
                return
        self.fail("authored windup did not reach travel")

    def test_actual_charge_windup_travel_and_static_wall_exit(self):
        run, _, bot, health, instance = self.encounter()
        start = bot.position
        self.travel(run, instance)
        self.assertEqual(bot.position, start)
        run.step({})
        self.assertAlmostEqual(bot.position[2], 8.76)
        self.assertAlmostEqual(bot.velocity[2], -15)
        run.match.collision_world = World(FLOOR + wall(-8, 2, 8, 2))
        for _ in range(45):
            run.step({})
        self.assertEqual(heavy_charge.phase(instance), 0)
        self.assertGreater(bot.position[2], 2)
        self.assertLess(bot.position[2], 4)
        self.assertEqual(bot.velocity, (0, 0, 0))
        self.assertEqual(health.total, 200)
        stopped = bot.position
        self.assertGreater(instance.state(41).remaining(), 14)
        for _ in range(30):
            run.step({})
        self.assertEqual(bot.position, stopped)
        self.assertEqual(heavy_charge.phase(instance), 0)

    def test_actual_side_contact_grazes_mercy_once_and_queues_impulse(self):
        run, mercy, bot, health, instance = self.encounter()
        self.travel(run, instance)
        mercy.mover.position[:] = (2, 1, 7)
        mercy.mover.body = None
        for _ in range(25):
            run.step({})
            if health.total < 200:
                break
        self.assertEqual(health.total, 150)
        self.assertEqual(run.match.combat.heavy_grazes[bot.entity], {mercy.body})
        for _ in range(10):
            run.step({})
        self.assertEqual(health.total, 150)
        self.assertGreater(mercy.mover.position[0], 2)
        npc_control.stop(run.match)
        run.step({})
        self.assertNotIn(bot.entity, run.match.combat.heavy_grazes)
        self.assertEqual(heavy_charge.phase(instance), 0)

    def frontal(self, *, wall_at=None):
        run, mercy, bot, health, instance = self.encounter()
        mercy.mover.position[:] = (0, 1, 7)
        mercy.mover.body = None
        if wall_at is not None:
            run.match.collision_world = World(FLOOR + wall(-8, wall_at, 8, wall_at))
        self.travel(run, instance)
        fight = run.match.combat
        for _ in range(20):
            run.step({})
            if bot.entity in fight.heavy_pins:
                break
        owner, app = fight.heavy_pins[bot.entity]
        self.assertIs(owner, mercy)
        self.assertEqual(app.graph.index, 0x1407)
        self.assertEqual(app.source_entity(), bot.entity)
        self.assertTrue(fight.is_stunned(mercy))
        return run, mercy, bot, health, app

    def test_frontal_capture_carries_mercy_until_lethal_wall_crush(self):
        run, mercy, bot, health, app = self.frontal(wall_at=3)
        fight = run.match.combat
        before = mercy.mover.position[2]
        for _ in range(5):
            run.step({mercy: (14000, 0, 1 | 8 | 16 | 32)})
            self.assertTrue(fight.is_stunned(mercy))
            self.assertEqual(mercy.body_script.buttons, 0)
        self.assertLess(mercy.mover.position[2], before)
        self.assertEqual(health.total, 200)
        for _ in range(45):
            run.step({})
            if health.dead:
                break
        self.assertTrue(health.dead)
        self.assertEqual(health.killer, bot.entity)
        self.assertFalse(fight.heavy_pins)
        self.assertNotIn(app.id, app.component.instances)

    def test_actual_wall_crush_ends_valkyrie_and_retires_capture(self):
        run, mercy, bot, health, instance = self.encounter()
        script = mercy.body_script
        script.set_ult_charge(1820)
        run.step({mercy: (0, 0, 0x20)})
        mercy.mover.position[:] = (0, 1, 7)
        mercy.mover.body = None
        run.match.collision_world = World(FLOOR + wall(-8, 3, 8, 3))
        self.travel(run, instance)
        self.assertTrue(script.component.entity_var(842).value())
        self.assertEqual(mercy.mover.flight_scale, 1)

        fight = run.match.combat
        captured = False
        for _ in range(65):
            run.step({})
            captured |= bot.entity in fight.heavy_pins
            if health.dead:
                break
        self.assertTrue(captured)
        self.assertTrue(health.dead)
        self.assertEqual(health.killer, bot.entity)
        self.assertFalse(fight.heavy_pins)
        self.assertFalse(fight.is_stunned(mercy))
        run.step({})
        self.assertFalse(script.component.entity_var(842).value())
        self.assertIsNone(mercy.mover.flight_scale)

    def test_manual_stop_releases_frontal_capture_without_crush_damage(self):
        run, mercy, _, health, app = self.frontal()
        fight = run.match.combat
        npc_control.stop(run.match)
        run.step({})
        self.assertFalse(fight.is_stunned(mercy))
        self.assertEqual(health.total, 200)
        self.assertFalse(fight.heavy_pins)
        self.assertNotIn(app.id, app.component.instances)

    def test_carried_mercy_rejects_weapon_switch_until_fresh_request(self):
        run, mercy, bot, health, app = self.frontal()
        match = run.match
        fight = match.combat
        component = mercy.body_script.component
        slot = component.entity_var(31)
        self.assertEqual(slot.value(), 1)

        def switch(buttons=0):
            run.frame += 1
            match.update(run.frame * TICK, run.frame)
            mercy.take_commands([Command(run.frame, buttons=buttons, action=2)])

        for _ in range(5):
            switch(FIRE)
            self.assertTrue(fight.is_stunned(mercy))
            self.assertEqual(slot.value(), 1)
            self.assertEqual(mercy.body_script.buttons, 0)
        self.assertEqual(health.total, 200)

        self.assertEqual(npc_control.stop(match), 1)
        for _ in range(40):
            run.step({})
        self.assertFalse(fight.is_stunned(mercy))
        self.assertNotIn(app.id, app.component.instances)
        self.assertEqual(slot.value(), 1)
        self.assertFalse(fight.rockets)
        x, y, z = bot.position
        mercy.mover.position[:] = (x + 4, y, z)
        mercy.mover.body = None
        switch()
        self.assertEqual(slot.value(), 2)
        for _ in range(35):
            run.step({})
        weapon = next(i for i in component.instances.values() if i.graph.index == 0x006D)
        magazine = weapon.find_var(0, 53)
        self.assertEqual(magazine.value(), 20)
        target = next(t for t in fight._targets() if t.owner is bot)
        attacker_health = fight.vitals_of(target)
        before = attacker_health.total
        shot(run, mercy, bot.position, 0.8)
        self.assertEqual(magazine.value(), 19)
        self.assertEqual(before - attacker_health.total, 20)

    def test_source_removal_retires_carry_without_later_damage(self):
        run, mercy, bot, health, app = self.frontal()
        fight = run.match.combat
        self.assertEqual(npc.remove_spawned(run.match), (bot.entity,))
        self.assertFalse(fight.is_stunned(mercy))
        for _ in range(35):
            run.step({})
        self.assertEqual(health.total, 200)
        self.assertFalse(fight.heavy_pins)
        self.assertNotIn(app.id, app.component.instances)

    def test_blaster_death_releases_carried_teammate_before_wall_crush(self):
        for lethal in (False, True):
            with self.subTest(lethal=lethal):
                run, mercy, bot, _, charge = self.encounter(blaster=True)
                match = run.match
                fight = match.combat
                ally = match.add_player(2, "Teammate", SOLDIER, mercy.team, False)
                in_world(match, ally)
                run.players.append(ally)
                self.assertTrue(match.switch_hero(ally, heroes()[SOLDIER]))
                ally.client.bot_stream = Mock(return_value=None)
                ally.client.queue_bot_death = Mock()
                ally.client.queue_bot_death_update = Mock()
                ally.mover.position[:] = (0, 1, 7)
                ally.mover.body = None
                target = next(t for t in fight._targets() if t.owner is ally)
                ally_health = fight.vitals_of(target)
                target = next(t for t in fight._targets() if t.owner is bot)
                health = fight.vitals_of(target)
                remaining = 15 if lethal else 35
                fight._damage(mercy, target, health.total - remaining,
                              False, None, run.frame)
                self.assertEqual(health.total, remaining)
                match.collision_world = World(FLOOR + wall(-8, -2, 8, -2))
                self.travel(run, charge)
                mercy.mover.position[:] = (0, 1, 12)
                mercy.mover.body = None
                for _ in range(20):
                    run.step({})
                    if bot.entity in fight.heavy_pins:
                        break
                self.assertIn(bot.entity, fight.heavy_pins)
                owner, app = fight.heavy_pins[bot.entity]
                self.assertIs(owner, ally)
                self.assertEqual(app.graph.index, 0x1407)
                self.assertTrue(fight.is_stunned(ally))

                eliminations = mercy.stats.value(stats.ELIMINATIONS)
                shot(run, mercy, bot.position, 0.8, frames=18)
                self.assertEqual(health.total, 0 if lethal else 15)
                self.assertEqual(bot.dead, lethal)
                self.assertEqual(bot.entity in fight.heavy_pins, not lethal)
                self.assertEqual(app.id in app.component.instances, not lethal)
                self.assertEqual(fight.is_stunned(ally), not lethal)
                self.assertEqual(mercy.stats.value(stats.ELIMINATIONS), eliminations + int(lethal))
                self.assertEqual(ally.client.queue_bot_death.call_count, int(lethal))
                self.assertEqual(ally_health.total, 200)
                run.step({ally: (0, 0, FIRE)})
                self.assertEqual(ally.body_script.buttons & FIRE, FIRE if lethal else 0)
                for _ in range(55):
                    run.step({})
                self.assertEqual(ally_health.dead, not lethal)
                if lethal:
                    self.assertEqual(ally_health.total, 200)
                else:
                    self.assertEqual(ally_health.killer, bot.entity)

    def test_close_body_impact_damages_mercy_once_and_stuns_for_two_seconds(self):
        run, mercy, _bot, health, instance = self.encounter()
        mercy.mover.position[:] = (0, 1, 8.3)
        mercy.mover.body = None
        for _ in range(80):
            run.step({})
            if health.total < 200:
                break
        fight = run.match.combat
        self.assertEqual(health.total, 25)
        self.assertEqual(fight.stuns[mercy.body] - run.frame, 125)
        self.assertFalse(fight.heavy_pins)
        self.assertEqual(heavy_charge.phase(instance), 0)
        for _ in range(120):
            run.step({mercy: (0, 0, 8 | 16 | 32)})
        self.assertTrue(fight.is_stunned(mercy))
        self.assertEqual(mercy.body_script.buttons, 0)
        for _ in range(5):
            run.step({})
        self.assertFalse(fight.is_stunned(mercy))

    def test_close_charge_interrupts_guardian_angel_until_stun_and_cooldown_end(self):
        run, mercy, bot, health, charge = self.encounter()
        fixture = geometry.MercyNpcGeometryTests().npc_fixture()
        trooper = next(definition for definition in fixture.catalog.values() if definition.name == "Trooper")
        ally = npc.spawn(run.match, trooper, (0, 1, 15), team=mercy.team)
        guardian = next(instance for instance in mercy.body_script.component.instances.values()
                        if instance.graph.index == 0x006E)
        state = guardian.state(0)
        cooldown = guardian.find_var(0, 61)

        def step(buttons=0):
            x, y, z = ally.position
            yaw, pitch = aim(run.match.combat._eye(mercy), (x, y + 1, z))
            run.step({mercy: (yaw, pitch, buttons)})

        self.travel(run, charge)
        step(0x08)
        self.assertTrue(state.active)
        self.assertEqual(health.total, 200)
        x, y, z = mercy.mover.position
        bot.position = (x, y, z + 0.7)
        step(0x08)
        self.assertEqual(health.total, 25)
        self.assertFalse(state.active)
        self.assertGreater(cooldown.value(), 0)
        fight = run.match.combat
        until = fight.stuns[mercy.body]
        self.assertEqual(until - run.frame, 125)
        npc_control.stop(run.match)
        while run.frame + 1 < until:
            step(0x08)
            self.assertFalse(state.active)
        self.assertTrue(fight.is_stunned(mercy))
        step()
        self.assertFalse(fight.is_stunned(mercy))
        self.assertEqual(cooldown.value(), 0)
        step(0x08)
        self.assertTrue(state.active)

    def test_close_charge_interrupts_angelic_descent_and_restores_terminal_speed_after_stun(self):
        run, mercy, bot, health, charge = self.encounter()
        self.travel(run, charge)
        mercy.spawn = ((0.0, 40.0, 0.0), 0.0)
        mercy.mover = mercy.new_mover()
        descent = next(instance for instance in mercy.body_script.component.instances.values()
                       if instance.graph.index == 0x0071).state(1)

        for _ in range(30):
            run.step({mercy: (0, 0, JUMP)})
        self.assertTrue(descent.active)
        self.assertAlmostEqual(mercy.mover.velocity[1], -2, delta=0.01)
        self.assertTrue(heavy_charge.travelling(charge))

        x, y, z = mercy.mover.position
        bot.position = (x, y, z + 0.7)
        run.step({mercy: (0, 0, JUMP)})
        self.assertEqual(health.total, 25)
        self.assertFalse(descent.active)
        fight = run.match.combat
        deadline = fight.stuns[mercy.body]
        self.assertEqual(deadline - run.frame, 125)
        npc_control.stop(run.match)
        for _ in range(6):
            run.step({mercy: (0, 0, JUMP)})
        self.assertLess(mercy.mover.velocity[1], -2.5)
        self.assertFalse(any(mod.index == 13 for mod in mercy.mover.mods.values()))
        while run.frame + 1 < deadline:
            run.step({mercy: (0, 0, JUMP)})
            self.assertFalse(descent.active)

        run.step({})
        self.assertFalse(fight.is_stunned(mercy))
        self.assertGreater(mercy.mover.position[1], 1)
        for _ in range(30):
            run.step({mercy: (0, 0, JUMP)})
        self.assertTrue(descent.active)
        self.assertAlmostEqual(mercy.mover.velocity[1], -2, delta=0.01)

    def resurrection_charge_arena(self, position):
        geo = geometry.MercyNpcGeometryTests()
        geo.setUp()
        self.addCleanup(geo.doCleanups)
        fixture = geo.npc_fixture()
        definition = next(d for d in fixture.catalog.values() if d.name == "Heavy Assault")
        cast = resurrect.ResurrectTests()
        cast.setUp()
        self.addCleanup(cast.doCleanups)
        run, mercy, fight = cast.run, cast.mercy, cast.fight
        bot = npc.spawn(run.match, definition, position, team=cast.enemy.team)
        x, y, z = position
        yaw, pitch = aim(fight._eye(mercy), (x, y + 1, z))
        run.step({mercy: (yaw, pitch, 0)})
        self.assertEqual(npc_control.start(run.match, mercy, charge=True), bot.entity)
        health = fight.vitals_of(next(target for target in fight._targets() if target.owner is mercy))
        return cast, bot, health

    def test_close_charge_interrupts_resurrection_and_preserves_authored_cooldown(self):
        cast, bot, health = self.resurrection_charge_arena((4, 1, 0))
        run, mercy, fight = cast.run, cast.mercy, cast.fight
        bot.position = (0.7, 1, 0)

        cast.step(1, resurrect.RESURRECT)
        ability = next(state for state in cast.instance.states.values()
                       if isinstance(state, nodes.Ability) and state.flags & nodes.Ability.RUNNING)
        self.assertEqual(cast.cooldown(), 0)
        for _ in range(80):
            cast.step(1)
            if health.total < 200:
                break
        self.assertEqual(health.total, 25)
        self.assertEqual(fight.stuns[mercy.body] - run.frame, 125)
        self.assertFalse(ability.flags & nodes.Ability.RUNNING)
        self.assertTrue(fight.is_dead(cast.ally))
        self.assertGreater(cast.cooldown(), 29)
        self.assertLessEqual(cast.cooldown(), 30)

        npc_control.stop(run.match)
        cast.step(125)
        self.assertFalse(fight.is_stunned(mercy))
        self.assertTrue(fight.is_dead(cast.ally))
        cast.step(1, resurrect.RESURRECT)
        self.assertFalse(ability.flags & nodes.Ability.RUNNING)
        self.assertTrue(fight.is_dead(cast.ally))

        cast.step(1880)
        self.assertEqual(cast.cooldown(), 0)
        target = next(target for target in fight._targets() if target.owner is cast.ally)
        fight._damage(cast.enemy, target, 500, False, None, run.frame)
        cast.step(3)
        cast.cast()
        self.assertEqual(fight.vitals_of(target).total, 200)

    def test_charge_graze_interrupts_resurrection_only_when_pushed_out_of_tether(self):
        for bounded in (False, True):
            with self.subTest(bounded=bounded):
                cast, bot, health = self.resurrection_charge_arena((0, 1, 9))
                run, mercy, fight = cast.run, cast.mercy, cast.fight
                self.travel(run, heavy_charge.charge(bot))
                mercy.mover.position[:] = (2, 1, 7)
                mercy.mover.body = None
                cast.ally.mover.position[:] = (2, 1, 5)
                old_body = cast.ally.body
                if bounded:
                    run.match.collision_world = World(FLOOR + wall(4, 4, 4, 12) + wall(0.8, 4, 4, 4))

                cast.step(1, resurrect.RESURRECT)
                ability = next(state for state in cast.instance.states.values()
                               if isinstance(state, nodes.Ability) and state.flags & nodes.Ability.RUNNING)
                for _ in range(25):
                    cast.step(1)
                    if health.total < 200:
                        break
                self.assertEqual(health.total, 150)
                self.assertEqual(fight.heavy_grazes[bot.entity], {mercy.body})
                self.assertFalse(fight.is_stunned(mercy))
                self.assertTrue(ability.flags & nodes.Ability.RUNNING)
                self.assertEqual(cast.cooldown(), 0)

                npc_control.stop(run.match)
                cast.step(115)
                self.assertEqual(cast.ally.body, old_body)
                self.assertEqual(fight.is_dead(cast.ally), not bounded)
                self.assertEqual(fight.vitals_of(cast.target).total, 200 if bounded else 0)
                self.assertGreater(cast.cooldown(), 28)

    def test_second_body_contact_crushes_carried_mercy_and_stuns_the_second_body(self):
        run, mercy, bot, health, app = self.frontal()
        match = run.match
        other = match.add_player(2, "Bystander", SOLDIER, 1, False)
        in_world(match, other)
        run.players.append(other)
        match.switch_hero(other, heroes()[SOLDIER])
        other.team = mercy.team
        other.client.bot_stream = Mock(return_value=None)
        other.mover.position[:] = (0.4, 1, bot.position[2] - 0.3)
        other.mover.body = None
        fight = match.combat
        target = next(t for t in fight._targets() if t.owner is other)
        other_health = fight.vitals_of(target)
        run.step({})
        self.assertTrue(health.dead)
        self.assertEqual(health.killer, bot.entity)
        self.assertEqual(other_health.total, 25)
        self.assertTrue(fight.is_stunned(other))
        self.assertFalse(fight.heavy_pins)
        self.assertNotIn(app.id, app.component.instances)
