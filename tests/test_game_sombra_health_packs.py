"""Actual Sombra input against health packs extracted from Practice Range."""

import os
import unittest
from dataclasses import replace
from pathlib import Path

import test_game_sombra_npc as actual_npcs
from test_game_combat import Run, aim

from ow174.game import combat, npc, npc_control
from ow174.game.collision import World, flat_world
from ow174.game.commands import FIRE, Command
from ow174.game.content import PRACTICE_RANGE, heroes
from ow174.game.script import expr
from ow174.game.script.runtime import send_instance_message
from ow174.game.server_snapshot import _Graph
from ow174.game.world import HEALTH_POOL


@unittest.skipUnless(Path(os.environ.get("ORPHEUS_HEALTH_PACK_DATA", "")).is_file(),
                     "requires the extracted 104319 health-pack placement bundle")
class SombraHealthPackTests(unittest.TestCase):
    def arena(self, definition, distance=4, *, wall=False, names=("Sombra",)):
        run = Run(PRACTICE_RANGE, names)
        player = run.players[0]
        self.assertTrue(run.match.switch_hero(player, heroes()[0x02E000000000012E]))
        fight = run.match.combat
        self.assertEqual(len(fight.health_packs), 4)
        pack = next(pack for pack in fight.health_packs.values() if pack.definition == definition)
        x, y, z = pack.position
        run.match.bots.clear()
        collision = flat_world(y)
        if wall:
            middle = z - distance / 2
            collision = World([
                (x - 100, y, z - 100, x - 100, y, z + 100, x + 100, y, z + 100),
                (x - 100, y, z - 100, x + 100, y, z + 100, x + 100, y, z - 100),
                (x - 4, y, middle, x + 4, y, middle, x + 4, y + 4, middle),
                (x - 4, y, middle, x + 4, y + 4, middle, x - 4, y + 4, middle),
            ])
        run.match.collision_world = collision
        player.mover.use(world=run.match.collision_world)
        player.mover.position[:] = (x, y, z - distance)
        player.mover.body = None
        for _ in range(50):
            run.step({})
        pickup = next(i for i in pack.component.instances.values() if i.graph.index == 0x0046)
        return run, player, pack, pickup

    def cast(self, run, player, pack, frames, *, button=2, offset=0):
        x, y, z = pack.position
        angles = aim(run.match.combat._eye(player), (x + offset, y + 0.35, z))
        for _ in range(frames):
            run.step({player: (*angles, button)})

    def test_real_cast_applies_to_both_placed_definitions(self):
        for definition, refill in ((0x0400000000000FC2, 15), (0x0400000000000FC3, 10)):
            with self.subTest(definition=definition):
                run, player, pack, pickup = self.arena(definition)
                self.assertEqual(expr.to_float(pickup.vars[384].value()), refill)
                self.cast(run, player, pack, 12)
                self.assertFalse(pack.component.entity_var(6396).value())
                self.cast(run, player, pack, 70)
                self.assertEqual(pack.component.entity_var(6396).value(), expr.Entity(player.body))
                applied, = (i for i in pack.component.instances.values() if i.graph.index == 0x0B1F)
                self.assertEqual(applied.source_entity(), player.body)
                self.assertEqual(expr.to_float(pickup.vars[384].value()), refill / 4)
                self.assertNotIn(pack.entity, run.match.combat.hacks)
                weapon = next(
                    i for i in player.body_script.component.instances.values() if i.graph.index == 0x0B1B
                )
                self.assertGreater(weapon.state(53).remaining(), 0)
                self.assertEqual(weapon.state(53).cur, 4)

                encoder = _Graph({"match": run.match}, None)
                encoder.value(pack)
                self.assertTrue(encoder.finish())

    def test_emp_hacks_pack_through_authored_effect_without_spending_hack_cooldown(self):
        run, player, pack, pickup = self.arena(0x0400000000000FC3)
        player.body_script.set_ult_charge(1400)
        self.cast(run, player, pack, 55, button=32, offset=15)
        self.assertEqual(pack.component.entity_var(6396).value(), expr.Entity(player.body))
        applied, = (i for i in pack.component.instances.values() if i.graph.index == 0x0B1F)
        self.assertEqual(applied.source_entity(), player.body)
        self.assertEqual(expr.to_float(pickup.vars[384].value()), 2.5)
        self.assertNotIn(pack.entity, run.match.combat.vitals)
        weapon = next(i for i in player.body_script.component.instances.values() if i.graph.index == 0x0B1B)
        self.assertEqual(weapon.state(53).remaining(), 0)
        tick = run.frame + 350
        run.match.combat.update(tick * 0.016, tick)
        self.assertFalse(any(i.graph.index == 0x0B32 for i in pack.component.instances.values()))
        self.assertIn(applied.id, pack.component.instances)

    def test_emp_rejects_covered_and_distant_packs(self):
        for distance, wall in ((4, True), (25, False)):
            with self.subTest(distance=distance, wall=wall):
                run, player, pack, pickup = self.arena(0x0400000000000FC3, distance, wall=wall)
                player.body_script.set_ult_charge(1400)
                self.cast(run, player, pack, 55, button=32)
                self.assertFalse(pack.component.entity_var(6396).value())
                self.assertEqual(expr.to_float(pickup.vars[384].value()), 10)

    def test_paid_emp_claims_the_placed_pack_during_world_only_update(self):
        run, player, pack, pickup = self.arena(0x0400000000000FC3)
        player.body_script.set_ult_charge(1400)
        self.cast(run, player, pack, 1, button=32)
        cast = run.frame
        at = cast + 80
        run.match.update(at * combat.TICK, at)
        self.assertEqual(pack.component.entity_var(6396).value(), expr.Entity(player.body))
        application, = (i for i in pack.component.instances.values() if i.graph.index == 0x0B1F)
        self.assertEqual(application.start // pack.component.frame_ms, cast + 21)
        self.assertEqual(expr.to_float(pickup.vars[384].value()), 2.5)
        self.assertNotIn(pack.entity, run.match.combat.hacks)

    def test_emp_refreshes_friendly_pack_and_preserves_opposing_claim(self):
        run, owner, pack, _ = self.arena(0x0400000000000FC2, names=("Sombra", "Rival"))
        rival = run.players[1]
        self.assertTrue(run.match.switch_hero(rival, owner.hero))
        self.cast(run, owner, pack, 82)
        initial, = (i for i in pack.component.instances.values() if i.graph.index == 0x0B1F)
        self.place(owner, run, pack, 20)
        self.place(rival, run, pack, 4)
        rival.body_script.set_ult_charge(1400)
        self.cast(run, rival, pack, 55, button=32)
        self.assertEqual(pack.component.entity_var(6396).value(), expr.Entity(owner.body))
        self.assertIn(initial.id, pack.component.instances)
        self.place(rival, run, pack, 20)
        self.place(owner, run, pack, 4)
        owner.body_script.set_ult_charge(1400)
        self.cast(run, owner, pack, 55, button=32)
        refreshed, = (i for i in pack.component.instances.values() if i.graph.index == 0x0B1F)
        self.assertNotEqual(refreshed.id, initial.id)
        self.assertEqual(refreshed.source_entity(), owner.body)

    def test_pack_acquisition_rejects_wall_range_and_aim_miss(self):
        for distance, wall, offset in ((4, True, 0), (18, False, 0), (4, False, 2)):
            with self.subTest(distance=distance, wall=wall, offset=offset):
                run, player, pack, _ = self.arena(0x0400000000000FC2, distance, wall=wall)
                self.cast(run, player, pack, 82, offset=offset)
                self.assertFalse(pack.component.entity_var(6396).value())
                weapon = next(
                    i for i in player.body_script.component.instances.values() if i.graph.index == 0x0B1B
                )
                self.assertEqual(weapon.state(53).remaining(), 0)

    def test_release_cancels_pack_channel_and_later_cast_expires(self):
        run, player, pack, pickup = self.arena(0x0400000000000FC2)
        self.cast(run, player, pack, 12)
        self.cast(run, player, pack, 65, button=0)
        self.assertFalse(pack.component.entity_var(6396).value())
        self.cast(run, player, pack, 82)
        applied, = (i for i in pack.component.instances.values() if i.graph.index == 0x0B1F)
        deadline = applied.component.frame + 3750
        run.match.combat.update((deadline - 80) * 0.016, deadline - 80)
        self.assertEqual(pack.component.entity_var(6396).value(), expr.Entity(player.body))
        run.match.combat.update(deadline * 0.016, deadline)
        self.assertFalse(pack.component.entity_var(6396).value())
        self.assertEqual(expr.to_float(pickup.vars[384].value()), 15)

    @staticmethod
    def vitals(run, player):
        fight = run.match.combat
        target = next(target for target in fight._targets() if target.owner is player)
        return fight.vitals_of(target)

    @staticmethod
    def place(player, run, pack, offset):
        x, y, z = pack.position
        player.mover.use(world=run.match.collision_world)
        player.mover.position[:] = (x, y, z - offset)
        player.mover.body = None

    def test_walking_into_small_pack_heals_and_uses_normal_refill(self):
        run, player, pack, pickup = self.arena(0x0400000000000FC3, distance=1.5)
        vitals = self.vitals(run, player)
        vitals.damage(100)
        for _ in range(30):
            run.frame += 1
            player.take_commands([Command(run.frame, forward=127)])
            run.match.update(run.frame * 0.016, run.frame)
            if vitals.total > 100:
                break
        self.assertEqual(vitals.total, 175)
        refill = pickup.state(2)
        self.assertTrue(refill.active)
        ready_tick = (refill.start + 10000) // 16
        self.place(player, run, pack, 4)
        fight = run.match.combat
        fight.update((ready_tick - 3) * 0.016, ready_tick - 3)
        self.assertLess(pickup.vars[467].value(), 1)
        fight.update((ready_tick + 3) * 0.016, ready_tick + 3)
        self.assertEqual(pickup.vars[467].value(), 1)

    def test_healthy_contact_and_wounded_contact_through_wall_do_not_consume(self):
        run, player, pack, pickup = self.arena(0x0400000000000FC3, distance=0.6, wall=True)
        vitals = self.vitals(run, player)
        self.place(player, run, pack, 0)
        run.step({})
        self.assertEqual(pickup.vars[467].value(), 1)
        vitals.damage(100)
        self.place(player, run, pack, 0.6)
        run.step({})
        self.assertEqual(pickup.vars[467].value(), 1)
        self.assertEqual(vitals.total, 100)

    def test_hacked_pack_denies_enemy_then_heals_ally_and_refills(self):
        run, player, pack, pickup = self.arena(0x0400000000000FC2, names=("Sombra", "Other"))
        other = run.players[1]
        self.cast(run, player, pack, 82)
        player.body_script.ult_seconds = 0
        player.body_script.set_ult_charge(0)
        vitals = self.vitals(run, other)
        vitals.damage(100)
        self.place(other, run, pack, 0)
        run.step({})
        self.assertEqual(vitals.total, 100)
        self.assertEqual(pickup.vars[467].value(), 1)
        other.team = player.team
        run.step({})
        self.assertEqual(vitals.total, 200)
        self.assertEqual(player.body_script.component.entity_var(342).value(), 0)
        self.assertLess(pickup.vars[467].value(), 1)
        vitals.damage(100)
        for _ in range(180):
            run.step({})
            if vitals.total == 200:
                break
        self.assertEqual(vitals.total, 100)
        for _ in range(65):
            run.step({})
            if vitals.total == 200:
                break
        self.assertEqual(vitals.total, 200)

    def test_enemy_sombra_cannot_steal_pack_until_effect_expires(self):
        run, owner, pack, _ = self.arena(0x0400000000000FC2, names=("Sombra", "Rival"))
        rival = run.players[1]
        self.assertTrue(run.match.switch_hero(rival, owner.hero))
        self.cast(run, owner, pack, 82)
        self.place(owner, run, pack, 20)
        self.place(rival, run, pack, 4)
        self.cast(run, rival, pack, 82)
        self.assertEqual(pack.component.entity_var(6396).value(), expr.Entity(owner.body))
        weapon = next(i for i in rival.body_script.component.instances.values() if i.graph.index == 0x0B1B)
        self.assertEqual(weapon.state(53).remaining(), 0)
        run.frame += 3750
        run.match.update(run.frame * 0.016, run.frame)
        self.assertFalse(pack.component.entity_var(6396).value())
        self.cast(run, rival, pack, 10, button=0)
        self.cast(run, rival, pack, 82)
        self.assertEqual(pack.component.entity_var(6396).value(), expr.Entity(rival.body))

    def test_recasting_on_own_pack_refreshes_single_effect(self):
        run, owner, pack, _ = self.arena(0x0400000000000FC2)
        self.cast(run, owner, pack, 82)
        initial, = (i for i in pack.component.instances.values() if i.graph.index == 0x0B1F)
        original_deadline = run.frame + 3750
        self.cast(run, owner, pack, 300, button=0)
        self.cast(run, owner, pack, 82)
        refreshed, = (i for i in pack.component.instances.values() if i.graph.index == 0x0B1F)
        self.assertNotEqual(refreshed.id, initial.id)
        self.assertEqual(pack.component.entity_var(6396).value(), expr.Entity(owner.body))
        run.frame = original_deadline
        run.match.update(run.frame * 0.016, run.frame)
        self.assertEqual(pack.component.entity_var(6396).value(), expr.Entity(owner.body))

    def test_ownership_change_interrupts_an_opposing_channel(self):
        run, owner, pack, _ = self.arena(0x0400000000000FC2, names=("Sombra", "Rival"))
        rival = run.players[1]
        self.assertTrue(run.match.switch_hero(rival, owner.hero))
        self.place(rival, run, pack, -4)
        self.cast(run, owner, pack, 12)
        x, y, z = pack.position
        angles = {player: aim(run.match.combat._eye(player), (x, y + 0.35, z))
                  for player in (owner, rival)}
        for _ in range(12):
            run.step({player: (*angle, 2) for player, angle in angles.items()})
        rival_weapon = next(
            i for i in rival.body_script.component.instances.values() if i.graph.index == 0x0B1B
        )
        self.assertEqual(rival_weapon.vars[8909].value(), expr.Entity(pack.entity))
        for _ in range(70):
            run.step({player: (*angle, 2) for player, angle in angles.items()})
        application, = (i for i in pack.component.instances.values() if i.graph.index == 0x0B1F)
        self.assertEqual(application.source_entity(), owner.body)
        self.assertEqual(pack.component.entity_var(6396).value(), expr.Entity(owner.body))


@unittest.skipUnless(Path(os.environ.get("ORPHEUS_HEALTH_PACK_DATA", "")).is_file(),
                     "requires the extracted 104319 health-pack placement bundle")
class SombraNpcHealthPackTests(unittest.TestCase):
    cast = SombraHealthPackTests.cast
    vitals = staticmethod(SombraHealthPackTests.vitals)

    @classmethod
    def setUpClass(cls):
        actual_npcs.SombraActualNpcTests.setUpClass.__func__(cls)

    @classmethod
    def restore_graphs(cls, path):
        actual_npcs.SombraActualNpcTests.restore_graphs(path)

    def npc_arena(self, key, definition):
        run, player, pack, pickup = SombraHealthPackTests.arena(self, definition)
        match = run.match
        match.game_map = replace(match.game_map, free_for_all=False)
        player.team = 0
        player.client.bot_stream = lambda body: None
        x, y, z = player.mover.position
        bot = npc.spawn(match, self.catalog[key], (x, y, z - 2.5), team=0, autonomous=True)
        component = bot.body_script.component
        component.set(9948, 0)
        for _ in range(75):
            run.step({})
        if key == "3":
            special = next(i for i in component.instances.values() if i.graph.index == 0x142C)
            self.assertEqual(send_instance_message(special, 0x0240000000000A45, {}, bot.entity), 1)
        return run, player, pack, pickup, bot

    def npc_first_contact(self, key, definition, damage):
        run, player, _, _, bot = self.npc_arena(key, definition)
        health = self.vitals(run, player)
        start = run.frame
        bot.team = 1
        for _ in range(170):
            run.step({})
            if health.total < 200:
                break
        self.assertEqual(health.total, 200 - damage)
        return run.frame - start

    def test_all_five_actual_npc_attacks_interrupt_pack_hack_and_allow_recovery(self):
        attacks = (("1", 20), ("2", 2), ("3", 100), ("4", 25), ("5", 5))
        for key, damage in attacks:
            for definition, refill in ((0x0400000000000FC2, 15), (0x0400000000000FC3, 10)):
                with self.subTest(npc=self.catalog[key].name, pack=definition):
                    contact = self.npc_first_contact(key, definition, damage)

                    run, player, pack, pickup, bot = self.npc_arena(key, definition)
                    fight = run.match.combat
                    health = self.vitals(run, player)
                    weapon = next(i for i in player.body_script.component.instances.values()
                                  if i.graph.index == 0x0B1B)
                    hack = weapon.state(53)
                    if contact > 12:
                        bot.team = 1
                        for _ in range(contact - 12):
                            run.step({})
                        windup = 11
                    else:
                        windup = 12
                    self.cast(run, player, pack, windup)
                    self.assertTrue(hack.flags & hack.RUNNING)
                    self.assertEqual(weapon.vars[8909].value(), expr.Entity(pack.entity))
                    self.assertEqual(health.total, 200)
                    self.assertFalse(pack.component.entity_var(6396).value())

                    bot.team = 1
                    for _ in range(14):
                        self.cast(run, player, pack, 1)
                        if health.total < 200:
                            break
                    self.assertEqual(health.total, 200 - damage)
                    self.assertEqual(bot.attack_target, player.entity)
                    if key == "4":
                        self.assertEqual(fight.pins.get(bot.entity), player.body)
                    impact_frame = run.frame
                    self.assertEqual(npc_control.stop(run.match), 1)
                    for _ in range(40):
                        if not hack.flags & hack.RUNNING:
                            break
                        self.cast(run, player, pack, 1)
                    self.assertFalse(hack.flags & hack.RUNNING)
                    elapsed = (run.frame - impact_frame) * combat.TICK
                    self.assertAlmostEqual(hack.remaining() + elapsed, 2, delta=0.04)
                    self.assertFalse(pack.component.entity_var(6396).value())
                    self.assertEqual(pickup.vars[384].value(), refill)
                    self.assertFalse(any(i.graph.index == 0x0B1F for i in pack.component.instances.values()))

                    self.cast(run, player, pack, 150, button=0)
                    self.assertEqual(hack.remaining(), 0)
                    self.assertFalse(pack.component.entity_var(6396).value())
                    self.cast(run, player, pack, 82)
                    applied, = (i for i in pack.component.instances.values() if i.graph.index == 0x0B1F)
                    self.assertEqual(applied.source_entity(), player.body)
                    self.assertEqual(pack.component.entity_var(6396).value(), expr.Entity(player.body))
                    self.assertEqual(pickup.vars[384].value(), refill / 4)
                    self.assertGreater(hack.remaining(), 3)
                    self.assertEqual(health.total, 200 - damage)

    def test_emp_pack_claim_depends_on_npc_hit_survival_and_capture(self):
        attacks = (("1", 20), ("2", 2), ("3", 100), ("4", 25), ("5", 5))
        for key, damage in attacks:
            for definition, refill in ((0x0400000000000FC2, 15), (0x0400000000000FC3, 10)):
                contact = self.npc_first_contact(key, definition, damage)
                capture = key == "4"
                for lethal in ((False,) if capture else (False, True)):
                    with self.subTest(npc=self.catalog[key].name, pack=definition, lethal=lethal):
                        self.check_emp_pack_claim(key, definition, refill, contact, damage, capture, lethal)

    def check_emp_pack_claim(self, key, definition, refill, contact, damage, capture, lethal):
        run, player, pack, pickup, bot = self.npc_arena(key, definition)
        fight = run.match.combat
        health = self.vitals(run, player)
        script = player.body_script
        script.ult_seconds = 0
        script.set_ult_charge(1400)
        emp = next(i for i in script.component.instances.values() if i.graph.index == 0x0B31)
        cast = emp.state(1)
        pulse = emp.node_state(emp.graph.node(40))
        if contact >= 2:
            bot.team = 1
            for _ in range(contact - 2):
                run.step({})
        self.cast(run, player, pack, 1, button=32)
        self.assertTrue(cast.flags & cast.RUNNING)
        self.assertEqual(script.component.entity_var(342).value(), 0)
        self.assertEqual(health.total, 200)
        self.assertEqual(pulse.counter, 0)
        self.assertFalse(pack.component.entity_var(6396).value())

        if lethal:
            health.now[HEALTH_POOL] = 1
        bot.team = 1
        self.cast(run, player, pack, 1, button=0)
        self.assertEqual(health.total, 0 if lethal else 200 - damage)
        self.assertEqual(pulse.counter, 0)
        if capture:
            self.assertEqual(fight.pins.get(bot.entity), player.body)
        if lethal:
            self.assertEqual(health.death_tick, run.frame)
            self.assertEqual(health.killer, bot.entity)
        self.assertEqual(npc_control.stop(run.match), 1)

        self.cast(run, player, pack, 100, button=0)
        applies = not (lethal or capture)
        self.assertEqual(pulse.counter, int(applies))
        effects = [i for i in pack.component.instances.values() if i.graph.index == 0x0B1F]
        if applies:
            effect, = effects
            self.assertEqual(effect.source_entity(), player.body)
            self.assertEqual(pack.component.entity_var(6396).value(), expr.Entity(player.body))
            self.assertEqual(pickup.vars[384].value(), refill / 4)
        else:
            self.assertFalse(effects)
            self.assertFalse(pack.component.entity_var(6396).value())
            self.assertEqual(pickup.vars[384].value(), refill)
            self.assertEqual(script.component.entity_var(342).value(), 0)
        weapon = next(i for i in script.component.instances.values() if i.graph.index == 0x0B1B)
        self.assertEqual(weapon.state(53).remaining(), 0)

    def test_heavy_charge_cancels_paid_emp_without_claiming_either_pack(self):
        def arena(definition):
            run, player, pack, pickup, bot = self.npc_arena("5", definition)
            x, y, z = player.mover.position
            bot.position = (x, y, z - 1.5)
            bot.team = 1
            bot.firing = False
            bot.yaw = 0
            bot.body_script.component.edges([(35, True)])
            return run, player, pack, pickup, bot

        for definition, refill in ((0x0400000000000FC2, 15), (0x0400000000000FC3, 10)):
            with self.subTest(pack=definition):
                baseline, victim, _, _, bot = arena(definition)
                fight = baseline.match.combat
                start = baseline.frame
                for _ in range(75):
                    baseline.step({})
                    if bot.entity in fight.pins:
                        break
                self.assertEqual(fight.pins.get(bot.entity), victim.body)
                contact = baseline.frame - start
                self.assertGreaterEqual(contact, 2)

                run, player, pack, pickup, bot = arena(definition)
                fight = run.match.combat
                script = player.body_script
                script.ult_seconds = 0
                script.set_ult_charge(1400)
                emp = next(i for i in script.component.instances.values() if i.graph.index == 0x0B31)
                cast = emp.state(1)
                pulse = emp.node_state(emp.graph.node(40))
                self.cast(run, player, pack, contact - 2, button=0)
                self.cast(run, player, pack, 1, button=32)
                self.assertTrue(cast.flags & cast.RUNNING)
                self.assertEqual(script.component.entity_var(342).value(), 0)
                self.assertEqual(pulse.counter, 0)
                self.assertNotIn(bot.entity, fight.pins)
                self.cast(run, player, pack, 1, button=0)
                self.assertEqual(fight.pins.get(bot.entity), player.body)
                self.assertEqual(self.vitals(run, player).total, 200)
                self.assertFalse(cast.flags & cast.RUNNING)

                bot.body_script.component.edges([(35, False)])
                self.cast(run, player, pack, 300, button=0)
                self.assertNotIn(bot.entity, fight.pins)
                self.assertEqual(pulse.counter, 0)
                self.assertEqual(script.component.entity_var(342).value(), 0)
                self.assertFalse(pack.component.entity_var(6396).value())
                self.assertEqual(pickup.vars[384].value(), refill)
                self.assertFalse(any(i.graph.index == 0x0B1F for i in pack.component.instances.values()))

    def test_npc_damage_hacked_pack_healing_and_earned_emp_admission(self):
        attacks = (("1", 20), ("2", 2), ("3", 100), ("4", 25), ("5", 5))
        for key, damage in attacks:
            for definition, healing in ((0x0400000000000FC2, 250), (0x0400000000000FC3, 75)):
                with self.subTest(npc=self.catalog[key].name, pack=definition):
                    self.check_pack_healing_and_earned_emp(key, damage, definition, healing)

    def check_pack_healing_and_earned_emp(self, key, damage, definition, healing):
        run, player, pack, pickup, bot = self.npc_arena(key, definition)
        fight = run.match.combat
        script = player.body_script
        script.ult_seconds = 0
        script.set_ult_charge(1392)
        charge = script.component.entity_var(342)
        self.cast(run, player, pack, 82)
        first, = (i for i in pack.component.instances.values() if i.graph.index == 0x0B1F)
        self.assertEqual(first.source_entity(), player.body)
        self.assertEqual(pickup.vars[467].value(), 1)
        self.assertEqual(charge.value(), 1392)

        health = self.vitals(run, player)
        bot.team = 1
        for _ in range(170):
            run.step({})
            if health.total < 200:
                break
        self.assertEqual(health.total, 200 - damage)
        self.assertEqual(bot.attack_target, player.entity)
        self.assertEqual(npc_control.stop(run.match), 1)
        self.assertEqual(charge.value(), 1392)
        for _ in range(100):
            run.frame += 1
            player.take_commands([Command(run.frame, forward=127)])
            run.match.update(run.frame * combat.TICK, run.frame)
            if health.total > 200 - damage:
                break
        self.assertEqual(health.total, min(200, 200 - damage + healing))
        self.assertTrue(pickup.state(2).active)
        self.assertLess(pickup.vars[467].value(), 1)
        self.assertEqual(charge.value(), 1392)

        emp = next(i for i in script.component.instances.values() if i.graph.index == 0x0B31)
        pulse = emp.node_state(emp.graph.node(40))
        self.cast(run, player, pack, 1, button=32)
        self.cast(run, player, pack, 35, button=0)
        self.assertEqual(pulse.counter, 0)
        self.assertIn(first.id, pack.component.instances)
        self.assertEqual(charge.value(), 1392)

        x, y, z = bot.position
        angles = aim(fight._eye(player), (x, y + 1, z))
        victim = fight.vitals[bot.entity]
        before = victim.total
        run.step({player: (*angles, FIRE)})
        self.assertEqual(victim.total, before - 8)
        self.assertEqual(charge.value(), 1400)
        self.cast(run, player, pack, 1, button=32)
        self.assertEqual(charge.value(), 0)
        self.cast(run, player, pack, 55, button=0)
        self.assertEqual(pulse.counter, 1)
        refreshed, = (i for i in pack.component.instances.values() if i.graph.index == 0x0B1F)
        self.assertNotEqual(refreshed.id, first.id)
        self.assertEqual(refreshed.source_entity(), player.body)
        self.assertEqual(pack.component.entity_var(6396).value(), expr.Entity(player.body))
