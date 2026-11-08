"""Sombra's real extracted body in a modeled Match; no original-client acceptance implied."""

import unittest

from test_game_combat import KANEZAKA_DM, Run, aim

from ow174.game import combat, stats
from ow174.game.collision import World
from ow174.game.commands import FIRE, Command
from ow174.game.content import heroes
from ow174.game.script import expr
from ow174.game.world import ARMOUR_POOL, HEALTH_POOL, SHIELDS_POOL

SOMBRA = 0x02E000000000012E
MELEE, RELOAD = 0x800, 0x400


class SombraCombatTests(unittest.TestCase):
    def setUp(self):
        self.run = Run(KANEZAKA_DM, ("Sombra", "Enemy"))
        self.a, self.b = self.run.players
        match = self.run.match
        match.bots.clear()
        match.switch_hero(self.a, heroes()[SOMBRA])
        # Fixed geometry makes weapon tests independent of map spawn ordering.
        self.a.mover.position[:] = [0.0, 0.0, 0.0]
        self.b.mover.position[:] = [0.0, 0.0, 1.5]
        self.floor = [(-10, 0, -10, -10, 0, 100, 10, 0, 100),
                      (-10, 0, -10, 10, 0, 100, 10, 0, -10)]
        self.geometry()
        for _ in range(50):
            self.run.step({})

    def geometry(self, *triangles):
        self.run.match.collision_world = World(self.floor + list(triangles))
        for player in self.run.players:
            player.mover.use(world=self.run.match.collision_world)

    def wall(self):
        self.geometry((-3, 0, 0.75, 3, 0, 0.75, 3, 4, 0.75),
                      (-3, 0, 0.75, 3, 4, 0.75, -3, 4, 0.75))

    def press(self, buttons, frames=1, shooter=None, target=None):
        shooter, target = shooter or self.a, target or self.b
        x, y, z = target.mover.position
        yaw, pitch = aim(self.run.match.combat._eye(shooter), (x, y + 1, z))
        for _ in range(frames):
            self.run.step({shooter: (yaw, pitch, buttons)})

    def health(self, player):
        fight = self.run.match.combat
        target = next(t for t in fight._targets() if t.owner is player)
        return fight.vitals_of(target).total

    def weapon(self):
        return next(i for i in self.a.body_script.component.instances.values() if i.graph.index == 0x0B1B)

    def camo(self):
        return next(i for i in self.a.body_script.component.instances.values() if i.graph.index == 0x0C9B)

    def hack_applications(self):
        return [i for i in self.b.body_script.component.instances.values() if i.graph.index == 0x0B1F]

    def test_hack_channel_queues_authored_application_and_starts_cooldown(self):
        self.press(2, 20)
        self.assertEqual(self.hack_applications(), [])
        self.assertEqual(self.weapon().vars[375].value(), (expr.Entity(self.b.body),))
        self.press(2, 80)
        applications = self.hack_applications()
        self.assertEqual(len(applications), 1)
        self.assertEqual(applications[0].source_entity(), self.a.body)
        self.assertEqual(applications[0].vars[41].value(), (expr.Entity(self.a.body),))
        self.assertEqual(self.b.body_script.component.entity_var(6396).value(), expr.Entity(self.a.body))
        self.assertGreater(self.weapon().state(53).remaining(), 6)
        self.assertEqual(self.health(self.b), 200)  # Hack is a status skill, not replacement damage.

    def test_hack_miss_does_not_apply_or_spend_cooldown(self):
        for _ in range(100):
            self.run.step({self.a: (16384, 0, 2)})
        self.assertEqual(self.hack_applications(), [])
        self.assertFalse(self.weapon().vars[375].value())
        self.assertEqual(self.weapon().state(53).remaining(), 0)

    def test_hack_wall_rejects_acquisition_and_application(self):
        self.wall()
        self.press(2, 100)
        self.assertEqual(self.hack_applications(), [])
        self.assertFalse(self.weapon().vars[375].value())
        self.assertEqual(self.weapon().state(53).remaining(), 0)

    def test_hack_outside_authored_range_rejects_application(self):
        self.b.mover.position[:] = [0.0, 0.0, 18.0]
        self.b.mover.body = None
        self.press(0, 3)
        self.press(2, 100)
        self.assertEqual(self.hack_applications(), [])
        self.assertFalse(self.weapon().vars[375].value())
        self.assertEqual(self.weapon().state(53).remaining(), 0)

    def test_hack_rejects_an_allied_player(self):
        self.run.match.game_map = KANEZAKA_DM.__class__(**{
            **KANEZAKA_DM.__dict__, "free_for_all": False,
        })
        self.b.team = self.a.team
        self.press(2, 100)
        self.assertEqual(self.hack_applications(), [])
        self.assertEqual(self.weapon().state(53).remaining(), 0)

    def test_hack_release_before_channel_finishes_does_not_apply(self):
        self.press(2, 20)
        self.press(0, 50)
        self.assertEqual(self.hack_applications(), [])
        self.assertFalse(self.weapon().state(53).flags & self.weapon().state(53).RUNNING)

    def test_hack_actual_received_damage_interrupts_channel(self):
        self.press(2, 10)
        yaw_a, pitch_a = aim(self.run.match.combat._eye(self.a), (0, 1, 1.5))
        yaw_b, pitch_b = aim(self.run.match.combat._eye(self.b), (0, 1, 0))
        self.run.step({self.a: (yaw_a, pitch_a, 2), self.b: (yaw_b, pitch_b, FIRE)})
        self.press(2, 55)
        self.assertEqual(self.health(self.a), 180)
        self.assertEqual(self.hack_applications(), [])
        self.assertGreater(self.weapon().state(53).remaining(), 0)
        self.assertLess(self.weapon().state(53).remaining(), 2)

    def test_hack_cooldown_refuses_fresh_press_and_expires_for_reuse(self):
        self.press(2, 100)
        first = self.hack_applications()[0].id
        self.press(0, 30)
        before = self.weapon().state(53).remaining()
        self.press(2, 60)
        self.assertLess(self.weapon().state(53).remaining(), before)
        self.assertEqual([i.id for i in self.hack_applications()], [first])
        self.press(0, 520)
        self.assertEqual(self.weapon().state(53).remaining(), 0)
        self.press(2, 100)
        self.assertTrue(any(i.id != first for i in self.hack_applications()))

    def test_hack_blocks_actual_helix_and_releases_it_after_effect_expiry(self):
        self.press(2, 100)
        application = self.hack_applications()[0]
        deadline = self.run.match.combat.hacks[self.b.body][0].until_tick
        self.assertGreater(deadline, self.run.frame)
        self.assertTrue(self.b.body_script.component.entity_var(888).value())
        self.press(2, shooter=self.b, target=self.a)
        self.press(0, 65)
        self.assertEqual(self.health(self.a), 200)
        self.press(0, deadline - self.run.frame - 1)
        self.assertTrue(self.b.body_script.component.entity_var(888).value())
        self.press(0)
        self.assertEqual(self.hack_applications(), [])
        self.assertTrue(application.stopped)
        self.assertFalse(self.b.body_script.component.entity_var(888).value())
        self.assertFalse(self.b.body_script.component.entity_var(6396).value())
        self.press(2, shooter=self.b, target=self.a)
        self.press(0, 65)
        self.assertEqual(self.health(self.a), 80)

    def test_hack_keeps_enemy_primary_fire_and_melee_available(self):
        self.press(2, 100)
        self.press(FIRE, shooter=self.b, target=self.a)
        self.press(0, 4)
        self.assertEqual(self.health(self.a), 180)
        self.press(MELEE, shooter=self.b, target=self.a)
        self.press(0, 45)
        self.assertEqual(self.health(self.a), 150)
        self.assertTrue(self.b.body_script.component.entity_var(888).value())

    def test_hack_interrupts_enemy_sprint_while_its_input_stays_held(self):
        sprint = next(i for i in self.b.body_script.component.instances.values() if i.graph.index == 0x0255)
        ability = next(st for st in sprint.states.values() if st.node.cls == "STUStatescriptStateAbility")
        def step_moving(hack):
            self.run.frame += 1
            self.run.match.update(self.run.frame * combat.TICK, self.run.frame)
            x, y, z = self.b.mover.position
            yaw, pitch = aim(self.run.match.combat._eye(self.a), (x, y + 1, z))
            self.a.take_commands([Command(self.run.frame, yaw=yaw, pitch=pitch, buttons=2 if hack else 0)])
            self.b.take_commands([Command(self.run.frame, forward=127, buttons=8)])

        for _ in range(5):
            step_moving(False)
        self.assertTrue(ability.flags & ability.RUNNING)
        for _ in range(100):
            step_moving(True)
        self.assertTrue(self.b.body_script.component.entity_var(888).value())
        self.assertFalse(ability.flags & ability.RUNNING)

    def test_hacked_body_death_retires_effect_and_ownership(self):
        self.press(2, 100)
        self.press(0, 4)
        self.press(FIRE, 100)
        self.press(0, 3)
        self.assertTrue(self.run.match.combat.is_dead(self.b))
        self.assertEqual(self.hack_applications(), [])
        self.assertFalse(self.b.body_script.component.entity_var(888).value())
        self.assertFalse(self.b.body_script.component.entity_var(6396).value())

    def test_hero_replacement_does_not_inherit_the_retired_body_silence(self):
        self.press(2, 100)
        old_body = self.b.body
        application = self.hack_applications()[0]
        self.assertTrue(self.run.match.switch_hero(self.b, self.b.hero))
        self.b.mover.position[:] = [0.0, 0.0, 1.5]
        self.b.mover.body = None
        self.press(0, 50)
        self.assertNotEqual(self.b.body, old_body)
        self.assertNotIn(old_body, self.run.match.combat.hacks)
        self.assertNotIn(self.b.body, self.run.match.combat.hacks)
        self.assertTrue(application.stopped)
        self.press(2, shooter=self.b, target=self.a)
        self.press(0, 65)
        self.assertEqual(self.health(self.a), 80)

    def test_hack_interrupts_nearby_enemy_sombra_camo_and_blocks_emp_resource_use(self):
        self.run.match.switch_hero(self.b, heroes()[SOMBRA])
        self.b.mover.position[:] = [0.0, 0.0, 1.5]
        self.b.mover.body = None
        self.press(0, 50)
        target_camo = next(i for i in self.b.body_script.component.instances.values()
                           if i.graph.index == 0x0C9B).state(13)
        self.press(8, shooter=self.b, target=self.a)
        self.press(0, 60)
        self.assertTrue(target_camo.flags & target_camo.RUNNING)
        self.press(2, 100)
        self.assertFalse(target_camo.flags & target_camo.RUNNING)
        self.assertGreater(target_camo.remaining(), 0)
        self.b.body_script.set_ult_charge(1400)
        self.press(0x20, shooter=self.b, target=self.a)
        self.press(0, 10)
        target_emp = next(i for i in self.b.body_script.component.instances.values()
                          if i.graph.index == 0x0B31).state(1)
        self.assertFalse(target_emp.flags & target_emp.RUNNING)
        self.assertEqual(self.b.body_script.component.entity_var(342).value(), 1400)

    def test_camo_taking_actual_enemy_damage_ends_the_ability(self):
        self.press(8)
        self.press(0, 60)
        ability = self.camo().state(13)
        self.assertTrue(ability.flags & ability.RUNNING)
        self.press(FIRE, shooter=self.b, target=self.a)
        self.press(0, 65)
        self.assertEqual(self.health(self.a), 180)
        self.assertFalse(ability.flags & ability.RUNNING)
        self.assertGreater(ability.remaining(), 0)

    def test_authored_excluded_damage_tag_does_not_end_camo(self):
        self.press(8)
        self.press(0, 60)
        ability = self.camo().state(13)
        fight = self.run.match.combat
        target = next(t for t in fight._targets() if t.owner is self.a)
        excluded = 0x0D8000000000191F  # both authored Camo damage-entry branches exclude this tag
        fight._damage(self.b, target, 10, False, None, self.run.frame, tags=(excluded,))
        self.press(0, 65)
        self.assertEqual(self.health(self.a), 190)
        self.assertTrue(ability.flags & ability.RUNNING)
        self.assertEqual(self.camo().vars[510].value(), (expr.Asset(excluded),))

    def test_zero_damage_does_not_produce_a_camo_interrupt(self):
        self.press(8)
        self.press(0, 60)
        fight = self.run.match.combat
        target = next(t for t in fight._targets() if t.owner is self.a)
        fight._damage(self.b, target, 0, False, None, self.run.frame)
        self.press(0, 65)
        self.assertEqual(self.health(self.a), 200)
        self.assertTrue(self.camo().state(13).flags & self.camo().state(13).RUNNING)

    def test_primary_fire_exits_camo_and_then_deals_damage(self):
        self.press(8)
        self.press(0, 60)
        self.press(FIRE, 100)
        self.assertFalse(self.camo().state(13).flags & self.camo().state(13).RUNNING)
        self.assertLess(self.health(self.b), 200)
        self.assertLess(self.weapon().vars[53].value(), 60)

    def test_actual_enemy_melee_interrupts_camo(self):
        self.press(8)
        self.press(0, 60)
        self.press(MELEE, shooter=self.b, target=self.a)
        self.press(0, 65)
        self.assertEqual(self.health(self.a), 170)
        self.assertFalse(self.camo().state(13).flags & self.camo().state(13).RUNNING)

    def test_actual_enemy_helix_interrupts_camo(self):
        self.press(8)
        self.press(0, 60)
        self.press(2, shooter=self.b, target=self.a)
        self.press(0, 65)
        self.assertLess(self.health(self.a), 200)
        self.assertFalse(self.camo().state(13).flags & self.camo().state(13).RUNNING)

    def test_camo_speed_changes_real_movement_and_cancel_starts_cooldown(self):
        self.press(8)
        self.press(0, 60)
        ability = self.camo().state(13)
        self.assertTrue(ability.flags & ability.RUNNING)
        start = self.a.mover.position[2]
        for _ in range(60):
            self.run.frame += 1
            self.run.match.update(self.run.frame * 0.016, self.run.frame)
            self.a.take_commands([Command(self.run.frame, forward=127)])
            self.b.take_commands([Command(self.run.frame)])
        self.assertGreater(self.a.mover.position[2] - start, 7)
        self.press(8)
        self.press(0, 65)
        self.assertFalse(ability.flags & ability.RUNNING)
        self.assertGreater(ability.remaining(), 0)
        self.press(8)
        self.press(0, 10)
        self.assertFalse(ability.flags & ability.RUNNING)
        self.press(0, 400)
        self.assertEqual(ability.remaining(), 0)
        self.press(8)
        self.press(0, 65)
        self.assertTrue(ability.flags & ability.RUNNING)

    def test_machine_pistol_uses_authored_damage_rate_and_ammunition(self):
        self.press(FIRE, 30)
        self.assertEqual(self.health(self.b), 120)
        self.assertEqual(self.weapon().vars[53].value(), 50)
        hits = self.a.client.messages(combat.HIT)
        self.assertEqual([h["+0x78"]["+0x4"] for h in hits], [8] * 10)
        self.assertEqual(self.a.stats.value(stats.HERO_DAMAGE), 80)
        self.assertEqual(hits[-1]["+0x84"] - hits[0]["+0x84"], 28)

    def test_release_stops_pistol_and_reload_restores_magazine(self):
        self.press(FIRE, 15)
        self.press(0, 3)
        hp, ammo = self.health(self.b), self.weapon().vars[53].value()
        self.press(0, 40)
        self.assertEqual((self.health(self.b), self.weapon().vars[53].value()), (hp, ammo))
        self.press(RELOAD)
        self.press(0, 130)
        self.assertEqual(self.weapon().vars[53].value(), 60)
        self.assertEqual(self.health(self.b), hp)

    def test_enemy_player_can_damage_and_kill_sombra_and_dead_body_cannot_fire(self):
        self.press(FIRE, 80, self.b, self.a)
        self.assertTrue(self.run.match.combat.is_dead(self.a))
        self.assertEqual(self.health(self.a), 0)
        self.assertTrue(self.a.body_script.component.owner_dead)
        self.assertEqual(self.a.movement().flags & combat.DEAD, combat.DEAD)
        before = self.health(self.b)
        self.press(FIRE, 30)
        self.assertEqual(self.health(self.b), before)
        self.assertEqual(len(self.a.client.messages(combat.DEATH_NOTICE)), 1)

    def test_melee_hits_once_per_activation(self):
        self.press(MELEE)
        self.press(0, 45)
        self.assertEqual(self.health(self.b), 170)
        self.assertEqual(self.run.match.combat.vitals[self.b.body].now[HEALTH_POOL], 170)
        self.press(MELEE)
        self.press(0, 45)
        self.assertEqual(self.health(self.b), 140)

    def test_static_wall_blocks_pistol(self):
        self.wall()
        self.press(FIRE, 30)
        self.assertEqual(self.health(self.b), 200)
        self.assertEqual(self.a.client.messages(combat.HIT), [])

    def test_static_wall_blocks_melee(self):
        self.wall()
        self.press(MELEE)
        self.press(0, 45)
        self.assertEqual(self.health(self.b), 200)
        self.assertEqual(self.a.client.messages(combat.HIT), [])

    def test_melee_does_not_trace_beyond_the_target(self):
        self.geometry((-3, 0, 2.5, 3, 0, 2.5, 3, 4, 2.5),
                      (-3, 0, 2.5, 3, 4, 2.5, -3, 4, 2.5))
        self.press(MELEE)
        self.press(0, 45)
        self.assertEqual(self.health(self.b), 170)

    def test_machine_pistol_falloff_reaches_authored_floor(self):
        self.b.mover.position[:] = [0.0, 0.0, 40.0]
        self.b.mover.body = None  # rebuild the fixture's physics capsule at its new position
        self.press(0, 3)
        self.press(FIRE)
        self.press(0, 3)
        self.assertAlmostEqual(self.health(self.b), 200 - 8 * 0.3, places=6)
        self.assertEqual(self.weapon().vars[53].value(), 59)

    def test_machine_pistol_removes_enemy_shields_before_health(self):
        self.run.match.switch_hero(self.b, heroes()[0x02E0000000000020])  # Zenyatta
        self.b.mover.position[:] = [0.0, 0.0, 1.5]
        self.press(0, 50)
        self.press(FIRE)
        self.press(0, 3)
        vitals = self.run.match.combat.vitals[self.b.body]
        self.assertEqual(vitals.now[HEALTH_POOL], 50)
        self.assertEqual(vitals.now[SHIELDS_POOL], 142)

    def test_machine_pistol_low_damage_is_halved_by_enemy_armour(self):
        self.run.match.switch_hero(self.b, heroes()[0x02E0000000000006])  # Torbjorn
        self.b.mover.position[:] = [0.0, 0.0, 1.5]
        self.press(0, 50)
        fight = self.run.match.combat
        target = next(t for t in fight._targets() if t.owner is self.b)
        vitals = fight.vitals_of(target)
        before = dict(vitals.now)
        self.assertGreater(before[ARMOUR_POOL], 0)
        self.press(FIRE)
        self.press(0, 3)
        self.assertEqual(vitals.now[ARMOUR_POOL], before[ARMOUR_POOL] - 4)
        self.assertEqual(vitals.now[HEALTH_POOL], before[HEALTH_POOL])

    def test_allied_player_is_not_damaged(self):
        self.run.match.game_map = KANEZAKA_DM.__class__(**{
            **KANEZAKA_DM.__dict__, "free_for_all": False,
        })
        self.b.team = self.a.team
        self.press(FIRE, 30)
        self.press(0, 3)
        self.press(MELEE)
        self.press(0, 45)
        self.assertEqual(self.health(self.b), 200)
        self.assertEqual(self.a.client.messages(combat.HIT), [])


if __name__ == "__main__":
    unittest.main()
