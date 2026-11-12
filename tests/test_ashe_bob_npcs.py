"""Actual B.O.B. body/gun versus the five authored NPC definitions.

Requires the read-only PvE base and its separately fingerprint-bound extension.
Uses explicit collision/transport/permissive-VM fixtures; native AI is not claimed.
"""

import math
import os
import unittest
from pathlib import Path
from unittest.mock import patch

import test_ashe_pve_npcs as fixtures
from test_ashe_combat import aim
from test_ashe_impulses import wall_scene

from ow174.game import combat, heavy_charge, immunity, npc, npc_console, npc_control, stats
from ow174.game.mover import statescript_mods
from ow174.game.script import expr, graph
from ow174.game.script.driver import ULT_CHARGE, ULT_COST

EXTRA = Path(os.environ.get("ORPHEUS_MOVER_EXTENSION", "."))


@unittest.skipUnless(fixtures.CATALOG.is_file() and fixtures.BUNDLE.is_file() and EXTRA.is_file(),
                     "requires actual PvE inputs and their B.O.B. extension")
class BobNpcTests(unittest.TestCase):
    setUpClass = classmethod(fixtures.AshePveNpcTests.setUpClass.__func__)
    tearDownClass = classmethod(fixtures.AshePveNpcTests.tearDownClass.__func__)
    fight = fixtures.AshePveNpcTests.fight
    press = fixtures.AshePveNpcTests.press
    spawn = fixtures.AshePveNpcTests.spawn

    def cast(self, definition):
        run, owner, enemy = self.spawn(definition, 8)
        return run, owner, enemy, self.deploy(run, owner)

    def deploy(self, run, owner):
        collision = run.match.collision_world
        run.match.collision_world = wall_scene(3)
        owner.body_script.set_ult_charge(2240)
        self.press(run, owner, 32, frames=45)
        actor, = run.match.combat.companions.active.values()
        self.assertEqual(actor.stopped_reason, "wall")
        run.match.collision_world = collision
        self.assertIsNotNone(actor.bot.body_script)
        return actor

    def test_earned_ultimate_charges_hits_and_expires_against_heavy_assault(self):
        heavy = next(item for item in self.catalog.values() if item.name == "Heavy Assault")
        run, owner, enemy = self.spawn(heavy, 10)
        fight = run.match.combat
        vitals = fight.vitals[enemy.entity]
        component = owner.body_script.component
        charge = component.entity_var(ULT_CHARGE)
        cost = component.entity_var(ULT_COST).value()
        self.assertEqual(cost, 2240)
        self.assertEqual(vitals.total, 4000)
        initial_charge = charge.value()
        self.press(run, owner, 32, frames=20)
        self.assertGreater(charge.value(), initial_charge)
        self.assertLess(charge.value(), cost)
        self.assertFalse(fight.companions.active)

        # Earn the cast from the NPC's real pool and the usual passive rate.
        _, pitch = aim(fight._eye(owner), (0, enemy.position[1] + 1.55, 10))
        for _ in range(40):
            run.step({owner: (0, pitch, 2)})
        for index in range(12):
            self.assertLess(charge.value(), cost)
            for frame in range(70):
                run.step({owner: (0, pitch, 3 if frame < 5 else 2)})
            self.assertEqual(vitals.total, 4000 - (index + 1) * 150)
        self.assertEqual(charge.value(), cost)
        self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 1800)

        self.press(run, owner, 32, frames=1)
        actor, = fight.companions.active.values()
        self.assertEqual(actor.phase, "charge")
        self.assertEqual(charge.value(), 0)
        self.assertEqual(component.entity_var(512).value(), expr.Entity(actor.bot.entity))
        while actor.phase == "charge" and run.frame <= actor.charge_until:
            run.step({})
        self.assertEqual(actor.stopped_reason, "enemy")
        self.assertEqual(actor.uppercut_target, enemy.entity)
        self.assertEqual(vitals.total, 2080)
        self.assertEqual(actor.shots, 0)
        position = enemy.position
        for _ in range(5):
            run.step({})
        self.assertEqual(enemy.position, position)
        self.assertEqual(enemy.velocity, (0, 0, 0))
        self.assertNotIn(enemy.entity, fight.impulses.npcs)

        while run.frame < actor.ready + 200:
            run.step({})
        self.assertEqual(actor.phase, "gun")
        self.assertEqual(actor.target, enemy.entity)
        self.assertEqual(actor.until - actor.ready, math.ceil(10 / combat.TICK))
        self.assertGreater(actor.shots, 0)
        self.assertEqual(charge.value(), 0)
        self.press(run, owner, 32, frames=5)
        self.assertEqual(list(fight.companions.active.values()), [actor])

        # Ashe can still shoot the same target while her ultimate meter is locked.
        for _ in range(40):
            yaw, pitch = aim(fight._eye(owner), (0, enemy.position[1] + 1, 10))
            run.step({owner: (yaw, pitch, 2)})
        shots_before = actor.shots
        health_before = vitals.total
        yaw, pitch = aim(fight._eye(owner), (0, enemy.position[1] + 1, 10))
        run.step({owner: (yaw, pitch, 3)})
        self.assertEqual(health_before - vitals.total, 75 + (actor.shots - shots_before) * 14)
        self.assertEqual(charge.value(), 0)

        while run.frame < actor.until - 1:
            run.step({})
        self.assertIn(actor.bot, run.match.bots)
        self.assertEqual(actor.shots, 80)
        self.assertEqual(vitals.total, 885)
        self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 3115)
        self.assertEqual(charge.value(), 0)
        run.step({})
        self.assertNotIn(actor.bot, run.match.bots)
        self.assertFalse(fight.companions.active)
        self.assertFalse(fight.respawns)
        self.assertEqual(component.entity_var(512).value(), expr.Entity(0))
        self.assertGreater(charge.value(), 0)
        self.press(run, owner, 32, frames=12)
        self.assertFalse(fight.companions.active)
        self.assertEqual(vitals.total, 885)
        self.assertEqual(actor.shots, 80)

    def test_gun_holds_fire_at_wall_then_hits_each_npc_and_credits_owner(self):
        for definition in self.catalog.values():
            with self.subTest(npc=definition.name):
                run, owner, enemy, actor = self.cast(definition)
                match = run.match
                vitals = match.combat.vitals[enemy.entity]
                initial = vitals.total
                open_world = match.collision_world
                match.collision_world = wall_scene(3)
                while run.frame < actor.ready + 30:
                    run.step({})
                self.assertEqual(actor.phase, "gun")
                self.assertIsNone(actor.target)
                self.assertEqual(actor.shots, 0)
                self.assertEqual(vitals.total, initial)
                self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 0)

                match.collision_world = open_world
                for _ in range(62):
                    run.step({})
                self.assertEqual(actor.target, enemy.entity)
                self.assertEqual(actor.shots, 8)
                self.assertEqual(initial - vitals.total, 8 * 14)
                self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 8 * 14)
                self.assertEqual(owner.body_script.component.entity_var(342).value(), 0)
                self.assertFalse(enemy.dead)

    def test_controlled_npc_weapons_hit_bob_before_its_owner(self):
        cases = (("Trooper", 60, 100), ("Heavy Assault", 85, 100),
                 ("Enforcer", 50, 100), ("Sniper", 100, 200))
        for name, expected, frames in cases:
            with self.subTest(npc=name):
                definition = next(item for item in self.catalog.values() if item.name == name)
                run, owner, enemy = self.spawn(definition, 8)
                match = run.match
                fight = match.combat
                console = npc_console.Console(self.catalog)
                yaw, pitch = aim(fight._eye(owner), (0, enemy.position[1] + 1, 8))
                run.step({owner: (yaw, pitch, 0)})
                self.assertEqual(console.press("0", match, owner)["entity"], enemy.entity)
                actor = self.deploy(run, owner)
                owner_target = next(item for item in fight._targets() if item.owner is owner)
                self.assertEqual(fight.vitals_of(owner_target).total, 200)
                self.assertFalse(enemy.attack_input)

                # Selection is blocked after deployment. The earlier engagement retains its target.
                with self.assertRaisesRegex(ValueError, "No living manual enemy"):
                    console.press("0", match, owner)
                self.assertEqual(enemy.attack_target, owner.entity)
                with patch.object(fight, "_damage", wraps=fight._damage) as damage:
                    for _ in range(frames):
                        run.step({})
                    hits = [call.args for call in damage.call_args_list if call.args[0] is enemy]
                    self.assertEqual(console.press("{", match, owner)["count"], 1)
                    self.assertIsNone(enemy.attack_target)
                    for _ in range(45):
                        run.step({})
                    self.assertEqual(
                        [call.args for call in damage.call_args_list if call.args[0] is enemy], hits,
                    )
                self.assertFalse(enemy.attack_input)
                self.assertTrue(hits)
                self.assertTrue(all(args[1].owner is actor.bot for args in hits))
                self.assertEqual(sum(args[2] for args in hits), expected)
                if name == "Enforcer":
                    self.assertEqual([args[2] for args in hits], [2] * 25)
                elif name == "Sniper":
                    self.assertEqual([args[2] for args in hits], [100])
                self.assertEqual(fight.vitals[actor.bot.entity].total, 1200 - expected)
                self.assertEqual(fight.vitals_of(owner_target).total, 200)
                self.assertIsNone(enemy.body_script.component.fault)

    def test_charge_passes_heavy_assault_carry_victim_until_effect_retires(self):
        heavy = next(item for item in self.catalog.values() if item.name == "Heavy Assault")
        for carried in (True, False):
            with self.subTest(carried=carried):
                run, owner, enemy = self.fight(distance=12)
                match = run.match
                for player in run.players:
                    player.client.queue_bot_death = lambda *args: None
                    player.client.queue_bot_death_update = lambda *args: None
                bot = npc.spawn(match, heavy, (20, enemy.mover.position[1], 12), team=owner.team)
                component = enemy.body_script.component
                # Declare carry delivery. The actual victim graph owns its flag.
                victim = component.play_loaded_graph(
                    graph.graph(0x1407), source=bot.entity, instigator=bot.entity)
                component.run_now()
                self.assertTrue(immunity.heavy_carry_active(enemy, enemy.body))
                if not carried:
                    component.destroy(victim)
                    component.run_now()
                    self.assertFalse(immunity.heavy_carry_active(enemy, enemy.body))
                fight = match.combat
                vitals = fight.vitals[enemy.body] = combat.Vitals(enemy.body, 10000)
                owner.body_script.set_ult_charge(2240)
                self.press(run, owner, 32, frames=1)
                actor, = fight.companions.active.values()
                for _ in range(90):
                    run.step({})
                    if actor.phase != "charge" or actor.bot.position[2] > 14:
                        break
                if carried:
                    self.assertGreater(actor.bot.position[2], 14)
                    self.assertEqual(actor.phase, "charge")
                    self.assertIsNone(actor.uppercut_target)
                    self.assertNotIn(enemy.body, fight.impulses.pending)
                    self.assertEqual(vitals.total, 10000)
                    self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 0)
                else:
                    self.assertEqual(actor.stopped_reason, "enemy")
                    self.assertEqual(actor.uppercut_target, enemy.body)
                    self.assertEqual(vitals.total, 9880)
                    self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 120)

    def start_carry_against_bob(self):
        heavy = next(item for item in self.catalog.values() if item.name == "Heavy Assault")
        run, owner, victim = self.fight(distance=12)
        match = run.match
        fight = match.combat
        for player in run.players:
            player.client.queue_bot_death = lambda *args: None
            player.client.queue_bot_death_update = lambda *args: None
        self.assertTrue(match.switch_hero(victim, owner.hero))
        victim.mover.position[:] = (0, owner.mover.position[1], 12)
        bot = npc.spawn(match, heavy, (0, victim.mover.position[1], 24), team=victim.team)
        victim.mover.yaw, victim.mover.pitch = aim(
            fight._eye(victim), (0, bot.position[1] + 1, 24))
        self.assertEqual(npc_control.start(match, victim), bot.entity)
        for _ in range(120):
            run.step({})
            if immunity.heavy_carry_active(victim, victim.body):
                break
        self.assertTrue(immunity.heavy_carry_active(victim, victim.body))
        self.assertIs(bot.body_script.component.entity_var(596, create=False).value(), True)
        owner.body_script.set_ult_charge(2240)
        return run, owner, victim, bot

    def test_bob_passes_a_live_opposing_heavy_charge_without_uppercut(self):
        run, owner, victim, bot = self.start_carry_against_bob()
        fight = run.match.combat
        before = fight.vitals[bot.entity].total
        self.press(run, owner, 32, frames=1)
        actor, = fight.companions.active.values()
        for _ in range(80):
            run.step({})
            if actor.phase != "charge" or actor.bot.position[2] > bot.position[2] + 2:
                break
        self.assertEqual(actor.phase, "charge")
        self.assertGreater(actor.bot.position[2], bot.position[2] + 2)
        self.assertIsNone(actor.uppercut_target)
        self.assertEqual(fight.vitals[bot.entity].total, before)
        self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 0)
        self.assertNotIn(bot.entity, fight.impulses.pending)
        self.assertTrue(immunity.heavy_carry_active(victim, victim.body))

    def test_bob_damages_the_lateral_carrier_without_overriding_resistant_motion(self):
        run, owner, victim, bot = self.start_carry_against_bob()
        fight = run.match.combat
        x, y, z = bot.position
        owner.mover.position[:] = (x - 6, y, z - 4)
        owner.mover.body = None
        run.step({owner: (16384, 0, 32)})
        actor, = fight.companions.active.values()
        for _ in range(35):
            run.step({})
            if actor.phase != "charge":
                break
        self.assertEqual(actor.stopped_reason, "enemy")
        self.assertEqual(actor.uppercut_target, bot.entity)
        self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 120)
        self.assertEqual(fight.vitals[victim.body].total, 200)
        self.assertNotIn(victim.body, fight.impulses.pending)
        impulse, = fight.impulses.pending[bot.entity]
        self.assertTrue(impulse.apply_resistance)
        component = bot.body_script.component
        effect, = (item for item in component.instances.values() if item.graph.index == 0x17F6)
        run.step({})
        self.assertNotIn(bot.entity, fight.impulses.pending)
        self.assertNotIn(bot.entity, fight.impulses.npcs)
        position = bot.position
        run.step({})
        self.assertCountEqual(
            [row for key, row in statescript_mods(component).items() if key[0] == effect.id],
            [(17, 2, 0.0, 0.0), (18, 2, 0.0, 0.0), (19, 2, 0.0, 0.0)],
        )
        self.assertNotEqual(bot.position, position)
        for _ in range(12):
            if effect.id not in component.instances:
                break
            run.step({})
        self.assertNotIn(effect.id, component.instances)
        self.assertFalse(any(key[0] == effect.id for key in statescript_mods(component)))
        self.assertTrue(immunity.heavy_carry_active(victim, victim.body))
        npc_control.stop(run.match)
        self.assertFalse(immunity.heavy_carry_active(victim, victim.body))

    def start_heavy_against_bob_owner(self):
        heavy = next(item for item in self.catalog.values() if item.name == "Heavy Assault")
        run, owner, bot = self.spawn(heavy, 12)
        owner.mover.yaw, owner.mover.pitch = aim(run.match.combat._eye(owner), (0, 2, 12))
        self.assertEqual(npc_control.start(run.match, owner), bot.entity)
        charge = next(item for item in bot.body_script.component.instances.values()
                      if item.graph.index == heavy_charge.CHARGE)
        owner.body_script.set_ult_charge(2240)
        return run, owner, bot, charge

    def test_heavy_charge_skips_opposing_bob_then_carries_ashe(self):
        run, owner, heavy, charge = self.start_heavy_against_bob_owner()
        fight = run.match.combat
        movement = charge.graph.node(71).state
        for _ in range(100):
            run.step({})
            state = charge.states.get(movement)
            if state is not None and state.active:
                break
        self.assertTrue(charge.states[movement].active)
        self.press(run, owner, 32, frames=1)
        actor, = fight.companions.active.values()
        for _ in range(85):
            run.step({})
            self.assertFalse(immunity.heavy_carry_active(actor.bot, actor.bot.entity))
            if immunity.heavy_carry_active(owner, owner.body):
                break
        self.assertEqual(actor.phase, "charge")
        self.assertGreater(actor.bot.position[2], heavy.position[2] + 2)
        self.assertTrue(immunity.heavy_carry_active(owner, owner.body))
        self.assertEqual(expr.to_entity(charge.vars[536].value()), owner.body)
        self.assertEqual(fight.vitals[actor.bot.entity].total, 1200)

    def carry_settled_bob(self):
        run, owner, heavy, charge = self.start_heavy_against_bob_owner()
        fight = run.match.combat
        run.match.collision_world = wall_scene(5)
        self.press(run, owner, 32, frames=1)
        actor, = fight.companions.active.values()
        for _ in range(25):
            run.step({})
            if actor.phase == "settle":
                break
        self.assertEqual(actor.stopped_reason, "wall")
        start = actor.bot.position[2]
        run.match.collision_world = wall_scene(-4)
        for _ in range(130):
            run.step({})
            if immunity.heavy_carry_active(actor.bot, actor.bot.entity):
                break
        self.assertTrue(immunity.heavy_carry_active(actor.bot, actor.bot.entity))
        self.assertEqual(expr.to_entity(charge.vars[536].value()), actor.bot.entity)
        return run, owner, heavy, charge, actor, start

    def test_heavy_charge_carries_settled_bob_to_wall_and_releases_at_impact(self):
        run, owner, heavy, charge, actor, start = self.carry_settled_bob()
        fight = run.match.combat
        shots = actor.shots
        for _ in range(60):
            run.step({})
            if charge.vars[59].value() == 2:
                break
            self.assertEqual(actor.shots, shots)
            self.assertFalse(immunity.heavy_carry_active(owner, owner.body))
        self.assertEqual(charge.vars[59].value(), 2)
        self.assertLess(actor.bot.position[2], start - 5)
        self.assertGreater(heavy.position[2], -4)
        self.assertEqual(fight.vitals[actor.bot.entity].total, 900)
        self.assertEqual(fight.vitals[owner.body].total, 200)
        self.assertFalse(immunity.heavy_carry_active(actor.bot, actor.bot.entity))
        self.assertGreater(run.frame, actor.ready)
        until = actor.until
        for _ in range(15):
            run.step({})
            if actor.shots > shots:
                break
        self.assertGreater(actor.shots, shots)
        self.assertEqual(actor.until, until)

    def test_stopping_heavy_charge_releases_bob_without_impact(self):
        run, owner, _heavy, charge, actor, _start = self.carry_settled_bob()
        fight = run.match.combat
        self.assertEqual(npc_control.stop(run.match), 1)
        self.assertFalse(immunity.heavy_carry_active(actor.bot, actor.bot.entity))
        self.assertEqual(charge.vars[59].value(), 2)
        for _ in range(20):
            run.step({})
        self.assertEqual(fight.vitals[actor.bot.entity].total, 1200)
        self.assertEqual(fight.vitals[owner.body].total, 200)

    def test_owner_hero_switch_retires_carried_bob_and_cancels_impact(self):
        from ow174.game.content import SOLDIER, heroes

        run, owner, _heavy, charge, actor, _start = self.carry_settled_bob()
        fight = run.match.combat
        component = actor.bot.body_script.component
        self.assertTrue(run.match.switch_hero(owner, heroes()[SOLDIER]))
        self.assertNotIn(actor.bot, run.match.bots)
        self.assertFalse(component.instances)
        for _ in range(10):
            run.step({})
        self.assertEqual(charge.vars[59].value(), 2)
        self.assertFalse(expr.to_entity(charge.vars[536].value()))
        self.assertEqual(fight.vitals[owner.body].total, 200)

    def test_bob_kill_leaves_actual_manual_npc_corpse_after_companion_expiry(self):
        definition = next(item for item in self.catalog.values() if item.name == "Trooper")
        run, owner, enemy, actor = self.cast(definition)
        while not enemy.dead and run.frame < actor.until:
            run.step({})
        self.assertTrue(enemy.dead)
        self.assertEqual(owner.stats.value(stats.ELIMINATIONS), 1)
        while run.frame < actor.until + 10:
            run.step({})
        self.assertIn(enemy, run.match.bots)
        self.assertTrue(enemy.dead)
        self.assertNotIn(actor.bot, run.match.bots)
        self.assertFalse(any(who is enemy for _, who in run.match.combat.respawns))

    def test_manual_clear_does_not_remove_owned_companion(self):
        definition = next(iter(self.catalog.values()))
        run, _owner, enemy, actor = self.cast(definition)
        self.assertEqual(npc.remove_spawned(run.match), (enemy.entity,))
        self.assertIn(actor.bot, run.match.bots)
        self.assertNotIn(enemy, run.match.bots)


if __name__ == "__main__":
    unittest.main()
