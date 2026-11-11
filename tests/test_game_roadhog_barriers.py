"""Actual Roadhog commands versus104319 Reinhardt mesh; pose/filter policy reconstructed."""

import os
import unittest
from dataclasses import replace
from unittest.mock import patch

import test_game_roadhog_defenses as phases

from ow174.game import barriers, combat
from ow174.game.collision import World
from ow174.game.content import heroes

ROADHOG = 0x02E0000000000040


@unittest.skipUnless(os.environ.get("ORPHEUS_REINHARDT_COLLISION"),
                     "prepared external104319 Reinhardt descriptor required")
class RoadhogBarrierTests(unittest.TestCase):
    health = phases.RoadhogDefenseTests.health

    def step(self, run, hog, rein, *, fire=0, raised=True, yaw=-32768):
        run.step({hog: (0, 0, fire), rein: (yaw, 0, 2 if raised else 0)})

    def session(self, distance=4, *, raised=True):
        run, hog, rein, behind = phases.RoadhogDefenseTests().session("Reinhardt", distance=distance)
        self.assertIsNotNone(run.match.combat.barrier_geometry, "external104319 descriptor REQUIRED")
        behind.mover.position[2] = distance + 3
        for _ in range(25):
            self.step(run, hog, rein, raised=False)
        for _ in range(20):
            self.step(run, hog, rein, raised=raised)
        state = run.match.combat.barriers[rein.body]
        self.assertEqual(state.active, raised)
        return run, hog, rein, behind, state

    def test_primary_close_slug_and_expiry_fragments_spend_ammo_on_mesh_without_body_charge(self):
        for button, distance in ((1, 4), (2, 4), (2, 15), (2, 23)):
            with self.subTest(button=button, distance=distance):
                run, hog, rein, behind, shield = self.session(distance)
                weapon = next(i for i in hog.body_script.component.instances.values()
                              if i.graph.index == 0x1C6)
                ammo = weapon.vars[53].value()
                self.step(run, hog, rein, fire=button)
                for _ in range(45):
                    self.step(run, hog, rein)
                self.assertLess(shield.current, shield.maximum)
                self.assertEqual(self.health(run, rein), 20000)
                self.assertEqual(self.health(run, behind), 20000)
                self.assertEqual(weapon.vars[53].value(), ammo - 1)
                self.assertEqual(hog.body_script.component.entity_var(342).value(), 0)
                self.assertFalse(hog.client.messages(combat.HIT))
                self.assertFalse(run.match.combat.rockets)

    def test_close_slug_collision_does_not_emit_lifetime_expiry_children(self):
        run, hog, rein, behind, shield = self.session()
        with patch.object(run.match.combat, "_expired", wraps=run.match.combat._expired) as expired:
            self.step(run, hog, rein, fire=2)
            for _ in range(50):
                self.step(run, hog, rein)
        self.assertEqual(expired.call_count, 0)
        self.assertEqual(shield.current, 1550)
        self.assertEqual(self.health(run, behind), 20000)

    def test_shared_helix_direct_plus_blast_hits_mesh_without_splash_spill(self):
        from ow174.game.content import SOLDIER

        run, shooter, rein, behind, shield = self.session()
        run.match.switch_hero(shooter, heroes()[SOLDIER])
        shooter.mover.position[:] = (0, rein.mover.position[1], 0)
        shooter.mover.body = None
        for _ in range(30):
            self.step(run, shooter, rein)
        self.step(run, shooter, rein, fire=2)
        for _ in range(45):
            self.step(run, shooter, rein)
        self.assertEqual(shield.current, 1480)
        self.assertEqual(self.health(run, rein), 20000)
        self.assertEqual(self.health(run, behind), 20000)

    def test_actual_melee_kills_low_health_owner_and_retires_raised_collision(self):
        run, hog, rein, _, shield = self.session(2.5)
        run.match.combat.vitals[rein.body] = combat.Vitals(rein.body, 20)  # explicit near-death fixture
        self.step(run, hog, rein, fire=0x800)
        for _ in range(25):
            self.step(run, hog, rein)
        self.assertTrue(run.match.combat.is_dead(rein))
        self.assertFalse(shield.active)
        self.assertFalse(run.match.combat._barrier_contacts(hog, run.match.combat._eye(hog), (0, 0, 1), 20))

    def test_hook_hits_shield_for30_without_control_or_body_damage_and_keeps_cooldown(self):
        run, hog, rein, behind, shield = self.session()
        self.step(run, hog, rein, fire=8)
        for _ in range(50):
            self.step(run, hog, rein)
            self.assertFalse(run.match.combat.hooks.controlled(rein.body))
            self.assertFalse(run.match.combat.hooks.controlled(behind.body))
        self.assertEqual(shield.current, 1570)
        self.assertTrue(shield.active)
        self.assertEqual(self.health(run, rein), 20000)
        self.assertEqual(hog.body_script.component.entity_var(342).value(), 0)
        ability = next(s for i in hog.body_script.component.instances.values() if i.graph.index == 0x1C8
                       for s in i.states.values() if s.node.pos == 11)
        self.assertGreater(ability.remaining(), 0)
        self.step(run, hog, rein, fire=8)
        for _ in range(30):
            self.step(run, hog, rein)
        self.assertEqual(shield.current, 1570)

    def test_whole_hog_damages_shield_consumes_charge_and_ends(self):
        run, hog, rein, behind, shield = self.session(5)
        hog.body_script.set_ult_charge(2240)
        self.step(run, hog, rein, fire=32)
        for _ in range(70):
            self.step(run, hog, rein)
        self.assertLess(shield.current, 1600)
        self.assertGreater(shield.current, 0)
        self.assertEqual(self.health(run, rein), 20000)
        self.assertEqual(self.health(run, behind), 20000)
        self.assertEqual(hog.body_script.component.entity_var(342).value(), 0)
        for _ in range(400):
            self.step(run, hog, rein)
        self.assertFalse(any(s.flags & 1 for s in hog.body_script.ultimates))

    def test_melee_damages_mesh_and_nearby_owner_through_it(self):
        run, hog, rein, behind, shield = self.session(2.5)
        behind.mover.position[0] = 20
        self.step(run, hog, rein, fire=0x800)
        for _ in range(25):
            self.step(run, hog, rein)
        self.assertEqual(shield.current, 1570)
        self.assertEqual(self.health(run, rein), 19970)
        self.assertTrue(shield.active)

    def test_lowered_allied_and_off_mesh_paths_do_not_shield_an_enemy(self):
        for mode in ("lowered", "ally", "offmesh"):
            with self.subTest(mode=mode):
                run, hog, rein, behind, shield = self.session()
                if mode == "ally":
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    rein.team = hog.team
                    behind.team = 1 - hog.team
                elif mode == "offmesh":
                    rein.mover.position[0] = 10
                    rein.mover.body = None
                self.step(run, hog, rein, raised=mode != "lowered")
                self.step(run, hog, rein, fire=2, raised=mode != "lowered")
                for _ in range(45):
                    self.step(run, hog, rein, raised=mode != "lowered")
                self.assertEqual(shield.current, 1600)
                self.assertLess(self.health(run, rein if mode == "lowered" else behind), 20000)

    def test_nearer_wall_precedes_primary_slug_hook_and_melee_mesh_contacts(self):
        for button in (1, 2, 8, 0x800):
            with self.subTest(button=button):
                run, hog, rein, behind, shield = self.session(1.5 if button == 0x800 else 4)
                y = hog.mover.position[1]
                run.match.collision_world = World([
                    (-10, y - 10, .2, 10, y - 10, .2, 10, y + 10, .2),
                    (-10, y - 10, .2, 10, y + 10, .2, -10, y + 10, .2),
                ])
                self.step(run, hog, rein, fire=button)
                for _ in range(45):
                    self.step(run, hog, rein)
                self.assertEqual(shield.current, 1600)
                self.assertEqual(self.health(run, rein), 20000)
                self.assertEqual(self.health(run, behind), 20000)

    def test_release_regen_delay_recovery_and_actual_reraise(self):
        run, hog, rein, _, shield = self.session()
        self.step(run, hog, rein, fire=2)
        for _ in range(30):
            self.step(run, hog, rein)
        self.assertEqual(shield.current, 1550)
        self.step(run, hog, rein, raised=False)
        for _ in range(100):
            self.step(run, hog, rein, raised=False)
        self.assertFalse(shield.active)
        self.assertEqual(shield.current, 1550)
        for _ in range(60):
            self.step(run, hog, rein, raised=False)
        self.assertEqual(shield.current, 1600)
        for _ in range(10):
            self.step(run, hog, rein)
        self.assertTrue(shield.active)

    def test_actual_raise_during_slug_or_hook_flight_intercepts_before_body(self):
        for button in (2, 8):
            with self.subTest(button=button):
                run, hog, rein, behind, shield = self.session(9, raised=False)
                self.step(run, hog, rein, fire=button, raised=False)
                fight = run.match.combat
                for _ in range(20):
                    if fight.rockets if button == 2 else fight.hooks.flights:
                        break
                    self.step(run, hog, rein, raised=False)
                self.assertTrue(fight.rockets if button == 2 else fight.hooks.flights)
                for _ in range(45):
                    self.step(run, hog, rein)
                    self.assertFalse(fight.hooks.controlled(rein.body))
                self.assertEqual(shield.current, 1550 if button == 2 else 1570)
                self.assertEqual(self.health(run, rein), 20000)
                self.assertEqual(self.health(run, behind), 20000)

    def test_new_support_barrier_breaks_established_hook_link_without_second_damage(self):
        run, hog, rein, behind, shield = self.session(6, raised=False)
        rein.mover.position[0] = 2.5  # owner capsule misses; mesh still covers the tether
        behind.mover.position[2] = 12
        rein.mover.body = behind.mover.body = None
        for _ in range(5):
            self.step(run, hog, rein, raised=False)
        self.step(run, hog, rein, fire=8, raised=False)
        for _ in range(60):
            self.step(run, hog, rein, raised=False)
            if run.match.combat.hooks.controlled(behind.body):
                break
        self.assertTrue(run.match.combat.hooks.controlled(behind.body))
        for _ in range(20):
            self.step(run, hog, rein)
        self.assertTrue(shield.active)
        self.assertFalse(run.match.combat.hooks.controlled(behind.body))
        self.assertEqual(self.health(run, behind), 19970)
        self.assertEqual(shield.current, 1600)

    @unittest.skipUnless(os.environ.get("ORPHEUS_NPC_GRAPHS"), "actual NPC graph bundle required")
    def test_new_support_barrier_breaks_each_five_npc_hook_link(self):
        import test_game_roadhog_npcs as actual
        from ow174.game import npc
        from test_game_combat import aim

        actual.RoadhogFiveNpcTests.setUpClass()
        self.addCleanup(actual.RoadhogFiveNpcTests.tearDownClass)
        for definition in actual.RoadhogFiveNpcTests.definitions.values():
            for lateral, cuts in ((2.5, True), (8, False)):
                with self.subTest(npc=definition.name, cuts=cuts):
                    run, hog, rein, rear, shield = self.session(6, raised=False)
                    fight = run.match.combat
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    hog.team, rein.team = 0, 1
                    rein.mover.position[0] = lateral
                    rear.mover.position[0] = 20
                    rein.mover.body = rear.mover.body = None
                    for player in run.players:
                        player.client = actual.NpcClient(player)
                    bot = npc.spawn(run.match, definition, (0, hog.mover.position[1], 16), team=1)
                    target = next(t for t in fight._targets() if t.owner is bot)
                    pool = fight.vitals_of(target)
                    before = pool.total
                    low, high = target.segment()
                    centre = tuple((a + b) / 2 for a, b in zip(low, high, strict=True))
                    look = aim(fight._eye(hog), centre)
                    for _ in range(5):
                        run.step({})
                    run.step({hog: (*look, 8)})
                    for _ in range(50):
                        run.step({hog: (*look, 0)})
                        if fight.hooks.controlled(bot.entity):
                            break
                    self.assertTrue(fight.hooks.controlled(bot.entity))
                    self.assertEqual(pool.total, before - 30)
                    self.assertFalse(shield.active)
                    position = bot.position[2]
                    for _ in range(10):
                        run.step({rein: (-32768, 0, 2)})
                        if not fight.hooks.controlled(bot.entity):
                            break
                    self.assertTrue(shield.active)
                    self.assertEqual(fight.hooks.controlled(bot.entity), not cuts)
                    if cuts:
                        position = bot.position[2]
                    for _ in range(6):
                        run.step({rein: (-32768, 0, 2)})
                    if cuts:
                        self.assertEqual(bot.position[2], position)
                        self.assertEqual(bot.velocity, (0, 0, 0))
                    else:
                        self.assertLess(bot.position[2], position)
                    self.assertEqual(shield.current, 1600)
                    self.assertEqual(pool.total, before - 30)

    def test_nearer_barrier_and_actual_deflect_grasp_choose_geometric_first_contact(self):
        for name in ("Genji", "Sigma"):
            for front in (False, True):
                for button in (1, 2):
                    with self.subTest(hero=name, defense_first=front, button=button):
                        run, hog, rein, caster, shield = self.session(6)
                        run.match.switch_hero(caster, next(h for h in heroes().values() if h.name == name))
                        caster.mover.position[:] = (0, hog.mover.position[1], 2 if front else 9)
                        caster.mover.body = None
                        run.match.combat.vitals[caster.body] = combat.Vitals(caster.body, 20000)
                        run.match.combat.vitals[hog.body] = combat.Vitals(hog.body, 20000)
                        run.step({rein: (-32768, 0, 2), caster: (-32768, 0, 16 if name == "Genji" else 8)})
                        for _ in range(5):
                            run.step({rein: (-32768, 0, 2), caster: (-32768, 0, 0)})
                        run.step({hog: (0, 0, button), rein: (-32768, 0, 2), caster: (-32768, 0, 0)})
                        for _ in range(40):
                            run.step({rein: (-32768, 0, 2), caster: (-32768, 0, 0)})
                        self.assertEqual(shield.current == 1600, front)
                        self.assertEqual(self.health(run, hog) < 20000, front and name == "Genji")
                        self.assertEqual(self.health(run, rein), 20000)
                        self.assertEqual(self.health(run, caster), 20000)

    def test_whole_hog_breaks_held_shield_then_projected_bubble_before_pushing_player(self):
        self.check_layered_whole_hog(None)

    @unittest.skipUnless(os.environ.get("ORPHEUS_NPC_GRAPHS"), "actual NPC graph bundle required")
    def test_whole_hog_breaks_held_shield_then_projected_bubble_before_pushing_each_npc(self):
        import test_game_roadhog_npcs as actual

        actual.RoadhogFiveNpcTests.setUpClass()
        self.addCleanup(actual.RoadhogFiveNpcTests.tearDownClass)
        for definition in actual.RoadhogFiveNpcTests.definitions.values():
            with self.subTest(npc=definition.name):
                self.check_layered_whole_hog(definition)

    def layered_barrier_scene(
        self, definition, *, shooter_z=0, shield_z=4, receiver_z=7, attack_height=None,
    ):
        from math import dist

        import test_game_roadhog_npcs as actual
        from test_game_combat import KANEZAKA_DM, Run, aim
        from ow174.game import npc

        run = Run(KANEZAKA_DM, ("Roadhog", "Reinhardt", "Zarya", "Target"))
        match = run.match
        fight = match.combat
        match.bots.clear()
        match._pending_bot_scripts.clear()
        match.game_map = replace(match.game_map, free_for_all=False)
        hog, rein, zarya, rear = run.players
        roster = {hero.name: hero for hero in heroes().values()}
        for player, name in ((hog, "Roadhog"), (rein, "Reinhardt"), (zarya, "Zarya")):
            match.switch_hero(player, roster[name])
        y = hog.mover.position[1]
        for player, x, z, team in ((hog, 0, shooter_z, 0), (rein, 2.5, shield_z, 1),
                                  (zarya, -5, receiver_z, 1), (rear, 0, receiver_z, 1)):
            player.team = team
            player.mover.position[:] = (x, y, z)
            player.mover.body = None
        receiver = rear
        if definition is not None:
            for player in run.players:
                player.client = actual.NpcClient(player)
            rear.team = 0
            rear.mover.position[0] = 20
            receiver = npc.spawn(match, definition, (0, y, receiver_z), team=1)
        receiver_id = combat._source_entity(receiver)
        fight.vitals[receiver_id] = combat.Vitals(receiver_id, 20000)
        target = next(target for target in fight._targets() if target.owner is receiver)
        low, high = target.segment()
        centre = tuple((a + b) / 2 for a, b in zip(low, high, strict=True))
        cast_aim = aim(fight._eye(zarya), centre)
        attack_point = centre if attack_height is None else (0, y + attack_height, receiver_z)
        attack_aim = aim(fight._eye(hog), attack_point)
        hog.body_script.ult_seconds = 0

        def step(fire=0, cast=0, *, raised=True):
            run.step({hog: (*attack_aim, fire), rein: (-32768, 0, 2 if raised else 0),
                      zarya: (*cast_aim, cast)})

        for _ in range(35):
            step()
        step(cast=16)
        shield = fight.barriers[rein.body]
        bubble = fight.zarya_bubbles.for_target(receiver)
        self.assertTrue(shield.active)
        self.assertIsNotNone(bubble)
        self.assertIs(bubble.caster, zarya)
        self.assertEqual(bubble.health, 200)
        origin = fight._eye(hog)
        length = dist(origin, attack_point)
        direction = tuple((b - a) / length for a, b in zip(origin, attack_point, strict=True))
        self.assertIs(fight._weapon_cover(hog, origin, direction, 20)[1], rein)
        return run, hog, receiver, shield, bubble, fight.vitals_of(target), step

    def check_layered_whole_hog(self, definition):
        run, hog, receiver, shield, bubble, pool, step = self.layered_barrier_scene(definition)
        fight = run.match.combat
        receiver_id = combat._source_entity(receiver)
        component = hog.body_script.component
        hog.body_script.set_ult_charge(component.entity_var(338).value())
        shield.current = 130  # near-break fixture keeps both defenses within one ultimate
        health = pool.total
        start = tuple(receiver.mover.position) if definition is None else receiver.position
        shield_spent = bubble_spent = False
        step(fire=32)
        for _ in range(110):
            if 0 < shield.current < 130:
                shield_spent = True
                self.assertEqual(bubble.health, 200)
            if 0 < bubble.health < 200:
                bubble_spent = True
                self.assertEqual(shield.current, 0)
            if shield.current > 0 or bubble.health > 0:
                self.assertEqual(pool.total, health)
                self.assertNotIn(receiver_id, fight.impulses.live)
            if receiver_id in fight.impulses.live:
                break
            step()
        self.assertTrue(shield_spent)
        self.assertTrue(bubble_spent)
        self.assertEqual(shield.current, 0)
        self.assertEqual(bubble.health, 0)
        self.assertIn(receiver_id, fight.impulses.live)
        self.assertLess(pool.total, health)
        self.assertEqual(component.entity_var(342).value(), 0)
        step()
        position = tuple(receiver.mover.position) if definition is None else receiver.position
        self.assertGreater(position[2], start[2])

    def test_hook_needs_fresh_casts_after_two_barrier_breaks_against_player(self):
        self.check_layered_hook(None)

    @unittest.skipUnless(os.environ.get("ORPHEUS_NPC_GRAPHS"), "actual NPC graph bundle required")
    def test_hook_needs_fresh_casts_after_two_barrier_breaks_against_each_npc(self):
        import test_game_roadhog_npcs as actual

        actual.RoadhogFiveNpcTests.setUpClass()
        self.addCleanup(actual.RoadhogFiveNpcTests.tearDownClass)
        for definition in actual.RoadhogFiveNpcTests.definitions.values():
            with self.subTest(npc=definition.name):
                self.check_layered_hook(definition)

    def check_layered_hook(self, definition):
        run, hog, receiver, shield, bubble, pool, step = self.layered_barrier_scene(definition)
        fight = run.match.combat
        entity = combat._source_entity(receiver)
        health = pool.total
        component = hog.body_script.component
        graph = next(i for i in component.instances.values() if i.graph.index == 0x01C8)
        cooldown = graph.state(graph.graph.node(11).state)
        launch = graph.state(graph.graph.node(12).state)
        charge = component.entity_var(342)

        def position():
            return tuple(receiver.mover.position) if definition is None else receiver.position

        start = position()

        def assert_no_body_hit():
            self.assertFalse(fight.hooks.controlled(entity))
            self.assertNotIn(hog.body, fight.hooks.flights)
            self.assertEqual(pool.total, health)
            self.assertEqual(position(), start)
            self.assertEqual(charge.value(), 0)

        shield.current = 25  # a single actual impact must break the front pool
        step(fire=8)
        for _ in range(65):
            if not shield.current:
                break
            step(fire=8)
        self.assertEqual(shield.current, 0)
        self.assertEqual(bubble.health, 200)
        self.assertIs(fight.zarya_bubbles.for_target(receiver), bubble)
        self.assertEqual(launch.counter, 1)
        self.assertGreater(cooldown.remaining(), 0)
        assert_no_body_hit()

        step(raised=False)
        step(fire=8, raised=False)
        self.assertEqual(launch.counter, 1)
        for _ in range(860):
            step(raised=False)
        self.assertEqual(cooldown.remaining(), 0)
        self.assertEqual(launch.counter, 1)
        self.assertIsNone(fight.zarya_bubbles.for_target(receiver))
        assert_no_body_hit()

        step(cast=16, raised=False)
        second = fight.zarya_bubbles.for_target(receiver)
        self.assertIsNotNone(second)
        self.assertNotEqual(second.entity, bubble.entity)
        self.assertEqual(second.health, 200)
        second.health = 25  # a single actual impact must break the rear pool
        step(fire=8, raised=False)
        for _ in range(65):
            if not second.health:
                break
            step(fire=8, raised=False)
        self.assertEqual(second.health, 0)
        self.assertEqual(launch.counter, 2)
        self.assertGreater(cooldown.remaining(), 0)
        assert_no_body_hit()

        for _ in range(640):
            if cooldown.remaining() == 0:
                break
            step(fire=8, raised=False)
        self.assertEqual(cooldown.remaining(), 0)
        for _ in range(25):
            step(fire=8, raised=False)
        self.assertEqual(launch.counter, 2)
        assert_no_body_hit()

        step(raised=False)
        step(fire=8, raised=False)
        for _ in range(65):
            if fight.hooks.controlled(entity):
                break
            step(raised=False)
        self.assertEqual(launch.counter, 3)
        self.assertTrue(fight.hooks.controlled(entity))
        self.assertEqual(pool.total, health - 30)
        self.assertEqual(charge.value(), 30)
        for _ in range(25):
            step(raised=False)
            if position()[2] < start[2]:
                break
        self.assertLess(position()[2], start[2])

    def test_scrap_gun_contact_order_through_two_barriers_against_player(self):
        self.check_layered_scrap_gun(None)

    @unittest.skipUnless(os.environ.get("ORPHEUS_NPC_GRAPHS"), "actual NPC graph bundle required")
    def test_scrap_gun_contact_order_through_two_barriers_against_each_npc(self):
        import test_game_roadhog_npcs as actual

        actual.RoadhogFiveNpcTests.setUpClass()
        self.addCleanup(actual.RoadhogFiveNpcTests.tearDownClass)
        for definition in actual.RoadhogFiveNpcTests.definitions.values():
            with self.subTest(npc=definition.name):
                self.check_layered_scrap_gun(definition)

    def check_layered_scrap_gun(self, definition):
        for attack, button, shooter_z in (("primary", 1, 0), ("slug", 2, 0),
                                          ("fragments", 2, -9)):
            with self.subTest(attack=attack):
                self.check_layered_scrap_gun_shot(definition, attack, button, shooter_z)

    def check_layered_scrap_gun_shot(self, definition, attack, button, shooter_z):
        run, hog, receiver, shield, bubble, pool, step = self.layered_barrier_scene(
            definition, shooter_z=shooter_z,
        )
        fight = run.match.combat
        component = hog.body_script.component
        weapon = next(i for i in component.instances.values() if i.graph.index == 0x01C6)
        ammo = weapon.vars[53].value()
        health = pool.total
        shield.current = bubble.health = 2  # low pools isolate the breaking projectile
        events = []
        damage_barrier = fight._damage_barrier
        damage_body = fight._damage

        def observe_barrier(contact, amount, **kwargs):
            front, rear = shield.current, bubble.health
            result = damage_barrier(contact, amount, **kwargs)
            if shield.current < front:
                events.append("held shield")
            if bubble.health < rear:
                events.append("projected barrier")
            return result

        def observe_body(shooter, target, *args, **kwargs):
            before = pool.total
            result = damage_body(shooter, target, *args, **kwargs)
            if shooter is hog and target.owner is receiver and pool.total < before:
                events.append("body")
            return result

        with (patch.object(fight, "_damage_barrier", side_effect=observe_barrier),
              patch.object(fight, "_damage", side_effect=observe_body),
              patch.object(fight, "_expired", wraps=fight._expired) as expired):
            step(fire=button)
            for _ in range(35):
                step()
            self.assertEqual(weapon.vars[53].value(), ammo - 1)
            self.assertEqual(shield.current, 0)
            if attack == "slug":
                self.assertEqual(events, ["held shield"])
                self.assertEqual(bubble.health, 2)
                self.assertEqual(pool.total, health)
                self.assertEqual(component.entity_var(342).value(), 0)
                # Use the remaining lifetime of the original projected cast.
                for _ in range(30):
                    step(raised=False)
                step(fire=button, raised=False)
                for _ in range(20):
                    step(raised=False)
                self.assertEqual(weapon.vars[53].value(), ammo - 2)
                self.assertEqual(events, ["held shield", "projected barrier"])
                self.assertEqual(pool.total, health)
                self.assertEqual(component.entity_var(342).value(), 0)
                self.assertFalse(any(call.args[0].burst is not None for call in expired.call_args_list))
            else:
                self.assertEqual(events[:2], ["held shield", "projected barrier"])
                self.assertTrue(events[2:] and all(event == "body" for event in events[2:]), events)
                self.assertLess(pool.total, health)
                self.assertAlmostEqual(component.entity_var(342).value(), health - pool.total, places=3)
                burst_parents = [call.args[0] for call in expired.call_args_list
                                 if call.args[0].burst is not None]
                self.assertEqual(len(burst_parents), int(attack == "fragments"))
            self.assertEqual(bubble.health, 0)
            for _ in range(65):
                if not fight.rockets:
                    break
                step(raised=False)
            self.assertFalse(fight.rockets)

    def test_layered_melee_player(self):
        self.check_layered_melee(None, 200)
        self.check_layered_melee(None, 25)

    @unittest.skipUnless(os.environ.get("ORPHEUS_NPC_GRAPHS"), "actual NPC graph bundle required")
    def test_layered_melee_npcs(self):
        import test_game_roadhog_npcs as actual

        actual.RoadhogFiveNpcTests.setUpClass()
        self.addCleanup(actual.RoadhogFiveNpcTests.tearDownClass)
        for definition in actual.RoadhogFiveNpcTests.definitions.values():
            for remaining in (200, 25):
                with self.subTest(npc=definition.name, projected_health=remaining):
                    self.check_layered_melee(definition, remaining)

    def check_layered_melee(self, definition, remaining):
        run, hog, receiver, shield, bubble, pool, step = self.layered_barrier_scene(
            definition, shield_z=1.8, receiver_z=2.5, attack_height=1,
        )
        fight = run.match.combat
        component = hog.body_script.component
        weapon = next(i for i in component.instances.values() if i.graph.index == 0x01C6)
        ammo = weapon.vars[53].value()
        health = pool.total
        shield_health = shield.current
        charge = component.entity_var(342)
        bubble.health = remaining
        step(fire=0x800)
        for _ in range(65):
            step(fire=0x800)
        self.assertTrue(shield.active)
        self.assertEqual(shield.current, shield_health - 30)
        self.assertEqual(bubble.health, max(0, remaining - 30))
        self.assertEqual(pool.total, health)
        self.assertEqual(charge.value(), 0)
        self.assertEqual(weapon.vars[53].value(), ammo)

        if remaining >= 30:
            self.assertIs(fight.zarya_bubbles.for_target(receiver), bubble)
            return

        self.assertIsNone(fight.zarya_bubbles.for_target(receiver))
        step()
        step(fire=0x800)
        for _ in range(25):
            step()
        self.assertTrue(shield.active)
        self.assertEqual(shield.current, shield_health - 60)
        self.assertEqual(pool.total, health - 30)
        self.assertEqual(charge.value(), 30)
        self.assertEqual(weapon.vars[53].value(), ammo)

    def test_break_stops_current_pellet_reraise_refused_then_recovery_allows_fresh_input(self):
        run, hog, rein, _, shield = self.session()
        # Exhaust the actual resource by real Roadhog shots rather than editing shield health.
        for _ in range(1400):
            self.step(run, hog, rein, fire=1)
            if shield.current == 0:
                break
        self.assertEqual(shield.current, 0)
        self.assertFalse(shield.active)
        self.assertGreater(shield.broken_until, run.match.combat.now)
        before = shield.broken_until
        for _ in range(80):
            self.step(run, hog, rein)
        self.assertFalse(shield.active)
        self.assertEqual(shield.broken_until, before)
        for _ in range(300):
            self.step(run, hog, rein, raised=False)
        self.assertGreater(shield.current, 0)
        for _ in range(20):
            self.step(run, hog, rein)
        self.assertTrue(shield.active)

    @unittest.skipUnless(
        all(os.environ.get(key) for key in ("ORPHEUS_NPC_GRAPHS", "ORPHEUS_ORISA_COLLISION",
                                           "ORPHEUS_SIGMA_COLLISION", "ORPHEUS_SYMMETRA_COLLISION")),
        "actual NPC graphs and deployed barrier meshes required",
    )
    def test_deployed_barrier_break_distinguishes_primary_volley_from_slug_on_each_npc(self):
        self.check_deployed_barriers_against_npcs(("primary", "slug"))

    @unittest.skipUnless(
        all(os.environ.get(key) for key in ("ORPHEUS_NPC_GRAPHS", "ORPHEUS_ORISA_COLLISION",
                                           "ORPHEUS_SIGMA_COLLISION", "ORPHEUS_SYMMETRA_COLLISION")),
        "actual NPC graphs and deployed barrier meshes required",
    )
    def test_fragmented_secondary_breaks_deployed_barrier_before_hitting_each_npc(self):
        self.check_deployed_barriers_against_npcs(("fragments",))

    @unittest.skipUnless(
        all(os.environ.get(key) for key in ("ORPHEUS_NPC_GRAPHS", "ORPHEUS_ORISA_COLLISION",
                                           "ORPHEUS_SIGMA_COLLISION", "ORPHEUS_SYMMETRA_COLLISION")),
        "actual NPC graphs and deployed barrier meshes required",
    )
    def test_whole_hog_breaks_deployed_barrier_before_displacing_each_npc(self):
        self.check_deployed_barriers_against_npcs(("whole hog",))

    @unittest.skipUnless(
        all(os.environ.get(key) for key in ("ORPHEUS_NPC_GRAPHS", "ORPHEUS_ORISA_COLLISION",
                                           "ORPHEUS_SIGMA_COLLISION", "ORPHEUS_SYMMETRA_COLLISION")),
        "actual NPC graphs and deployed barrier meshes required",
    )
    def test_hook_breaks_deployed_barrier_without_tether_then_recasts_on_each_npc(self):
        self.check_deployed_barriers_against_npcs(("hook",))

    @unittest.skipUnless(
        all(os.environ.get(key) for key in ("ORPHEUS_NPC_GRAPHS", "ORPHEUS_ORISA_COLLISION",
                                           "ORPHEUS_SIGMA_COLLISION", "ORPHEUS_SYMMETRA_COLLISION")),
        "actual NPC graphs and deployed barrier meshes required",
    )
    def test_melee_checks_npc_reach_independently_of_deployed_barrier_break(self):
        self.check_deployed_barriers_against_npcs(("melee intact", "melee break near", "melee break far"))

    def check_deployed_barriers_against_npcs(self, attacks):
        import test_game_roadhog_npcs as actual
        import test_game_roadhog_orisa_barrier as orisa
        import test_game_roadhog_sigma_barrier as sigma
        import test_game_roadhog_symmetra as symmetra
        import test_game_roadhog_winston as winston

        actual.RoadhogFiveNpcTests.setUpClass()
        self.addCleanup(actual.RoadhogFiveNpcTests.tearDownClass)
        for definition in actual.RoadhogFiveNpcTests.definitions.values():
            for fixture in (orisa.RoadhogOrisaBarrierTests(), sigma.RoadhogSigmaBarrierTests(),
                            symmetra.RoadhogSymmetraTests(), winston.RoadhogWinstonTests()):
                for attack in attacks:
                    with self.subTest(npc=definition.name, barrier=type(fixture).__name__, attack=attack):
                        self.check_deployed_barrier_break(fixture, definition, actual.NpcClient, attack)

    def check_deployed_barrier_break(self, fixture, definition, client, attack):
        from test_game_combat import aim

        from ow174.game import npc

        run, hog, caster, *other = fixture.session()
        match = run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        hog.team, caster.team = 0, 1
        for player in run.players:
            player.client = client(player)
            if player is not caster:
                player.team = hog.team
        y = hog.mover.position[1]
        name = caster.hero.name
        if name == "Winston":
            caster.mover.position[0] = 2
            caster.mover.body = None
            actor = fixture.cast(run, hog, caster)
            point = (0, y, 8)
        elif name == "Symmetra":
            caster.mover.position[0] = 2.4
            caster.mover.body = None
            actor = fixture.cast(run, hog, caster)
            point = (0, y, 12)
        elif name == "Sigma":
            actor, = other
            actor.team = caster.team
            fixture.deploy(run, hog, caster, actor)
            point = (1, y, 9)
        else:
            actor = fixture.deploy(run, hog, caster)
            point = (1, y, 9)
        caster.mover.position[0] = 18
        caster.mover.body = None
        if attack == "fragments":
            hog.mover.position[2] -= 10
            hog.mover.body = None
        if attack.startswith("melee"):
            contact = fight._barrier_contact(hog, fight._eye(hog), (0, 0, 1), 20)
            self.assertIsNotNone(contact)
            self.assertIs(contact[1], actor)
            surface = hog.mover.position[2] + contact[0]
            hog.mover.position[2] = surface - 1.4
            hog.mover.body = None
            point = (0, y, surface + (3.5 if attack == "melee break far" else .7))
        bot = npc.spawn(match, definition, point, team=caster.team)
        target = next(t for t in fight._targets() if t.owner is bot)
        pool = fight.vitals_of(target)
        before = pool.total
        for _ in range(6):
            run.step({})
        if attack.startswith("melee"):
            self.check_melee_reach_at_deployed_barrier(run, hog, actor, bot, pool, surface, attack)
            return
        actor.health = 2
        low, high = target.segment()
        look = aim(fight._eye(hog), tuple((a + b) / 2 for a, b in zip(low, high, strict=True)))
        component = hog.body_script.component
        weapon = next(i for i in component.instances.values() if i.graph.index == 0x01C6)
        ammo = weapon.vars[53].value()
        if attack == "hook":
            self.check_hook_after_deployed_barrier_break(run, hog, actor, bot, pool, look)
            self.assertEqual(weapon.vars[53].value(), ammo)
            return
        events = []
        damage_barrier = fight._damage_barrier
        damage_body = fight._damage
        hitscan = fight._hitscan
        ray = 0

        def observe_ray(*args, **kwargs):
            nonlocal ray
            ray += 1
            return hitscan(*args, **kwargs)

        def observe_barrier(*args, **kwargs):
            prior = actor.health
            result = damage_barrier(*args, **kwargs)
            if actor.health < prior:
                events.append(("barrier", run.frame, prior - actor.health, ray))
            return result

        def observe_body(shooter, victim, *args, **kwargs):
            prior = pool.total
            result = damage_body(shooter, victim, *args, **kwargs)
            if shooter is hog and victim.owner is bot and pool.total < prior:
                events.append(("body", run.frame, prior - pool.total, ray))
            return result

        button = {"primary": 1, "slug": 2, "fragments": 2, "whole hog": 32}[attack]
        if button == 32:
            hog.body_script.set_ult_charge(component.entity_var(338).value())
        split_before_contact = False
        with (patch.object(fight, "_damage_barrier", side_effect=observe_barrier),
              patch.object(fight, "_damage", side_effect=observe_body),
              patch.object(fight, "_hitscan", side_effect=observe_ray)):
            run.step({hog: (*look, button)})
            slug = None
            if attack == "fragments":
                slug, = fight.rockets
                self.assertIsNotNone(slug.burst)
            for _ in range(65 if attack in ("fragments", "whole hog") else 15):
                run.step({hog: (*look, 0)})
                if slug is not None and not events and len(fight.rockets) == 25:
                    self.assertTrue(all(shot is not slug and shot.burst is None for shot in fight.rockets))
                    split_before_contact = True
                if button == 32:
                    if actor.health > 0:
                        self.assertNotIn(bot.entity, fight.impulses.live)
                    elif pool.total < before:
                        break
        self.assertTrue(events)
        first_kind, break_frame, spent, break_ray = events[0]
        self.assertEqual((first_kind, spent), ("barrier", 2))
        self.assertEqual(actor.health, 0)
        loss = before - pool.total
        self.assertEqual(split_before_contact, attack == "fragments")
        if button == 32:
            self.assertGreater(loss, 0)
            self.assertGreater(break_ray, 0)
            self.assertTrue(all(kind == "body" and ray_id > break_ray for kind, _, _, ray_id in events[1:]),
                            events)
            self.assertGreater(fight.impulses.velocity(bot.entity)[2], 0)
            self.assertTrue(hog.body_script.ultimates[0].flags & 1)
        elif attack != "slug":
            self.assertGreater(loss, 0)
            self.assertTrue(all(kind == "body" and frame > break_frame for kind, frame, _, _ in events[1:]))
        else:
            self.assertEqual(len(events), 1)
            self.assertEqual(loss, 0)
            self.assertFalse(fight.rockets)
        self.assertAlmostEqual(loss, sum(amount for _, _, amount, _ in events[1:]), places=3)
        self.assertAlmostEqual(component.entity_var(342).value(), 0 if button == 32 else loss, places=3)
        self.assertEqual(weapon.vars[53].value(), ammo if button == 32 else ammo - 1)
        self.assertEqual(self.health(run, caster), 20000)


    def check_hook_after_deployed_barrier_break(self, run, hog, barrier, bot, pool, look):
        fight = run.match.combat
        component = hog.body_script.component
        hook = next(i for i in component.instances.values() if i.graph.index == 0x01C8)
        charge = component.entity_var(342)
        health = pool.total
        position = bot.position
        held = {hog: (*look, 8)}
        run.step(held)
        for _ in range(65):
            if not barrier.health:
                break
            run.step(held)
        self.assertEqual(barrier.health, 0)
        self.assertFalse(fight.hooks.controlled(bot.entity))
        self.assertNotIn(hog.body, fight.hooks.flights)
        self.assertEqual(pool.total, health)
        self.assertEqual(bot.position, position)
        self.assertEqual(charge.value(), 0)
        self.assertFalse(hog.client.messages(combat.HIT))
        cooldown = hook.state(hook.graph.node(11).state)
        launch = hook.state(hook.graph.node(12).state)
        self.assertGreater(cooldown.remaining(), 0)
        self.assertEqual(launch.counter, 1)
        for _ in range(640):
            run.step(held)
            if cooldown.remaining() == 0:
                break
        self.assertEqual(cooldown.remaining(), 0)
        for _ in range(25):
            run.step(held)
        self.assertEqual(launch.counter, 1)
        self.assertFalse(fight.hooks.controlled(bot.entity))
        self.assertNotIn(hog.body, fight.hooks.flights)
        self.assertEqual(pool.total, health)
        self.assertEqual(bot.position, position)
        self.assertEqual(charge.value(), 0)

        run.step({hog: (*look, 0)})
        run.step(held)
        for _ in range(65):
            if fight.hooks.controlled(bot.entity):
                break
            run.step({hog: (*look, 0)})
        self.assertEqual(launch.counter, 2)
        self.assertTrue(fight.hooks.controlled(bot.entity))
        self.assertEqual(pool.total, health - 30)
        self.assertEqual(charge.value(), 30)
        for _ in range(25):
            run.step({hog: (*look, 0)})
            if bot.position[2] < position[2]:
                break
        self.assertLess(bot.position[2], position[2])


    def check_melee_reach_at_deployed_barrier(self, run, hog, barrier, bot, pool, surface, attack):
        component = hog.body_script.component
        weapon = next(i for i in component.instances.values() if i.graph.index == 0x01C6)
        ammo = weapon.vars[53].value()
        charge = component.entity_var(342)
        held = {hog: (0, 0, 0x800)}
        far = attack == "melee break far"
        if attack != "melee intact":
            barrier.health = 2
        shield = barrier.health
        health = pool.total
        position = bot.position

        run.step(held)
        self.assertEqual(barrier.health, max(0, shield - 30))
        self.assertEqual(pool.total, health if far else health - 30)
        self.assertEqual(charge.value(), 0 if far else 30)
        self.assertEqual(bot.position, position)
        for _ in range(65):
            run.step(held)
        self.assertEqual(pool.total, health if far else health - 30)
        self.assertEqual(charge.value(), 0 if far else 30)
        if far:
            # Retain the miss through recovery, then put the target in reach.
            x, y, _ = bot.position
            bot.position = (x, y, surface + .7)
            for _ in range(6):
                run.step(held)
            self.assertEqual(pool.total, health)
            run.step({})
            run.step(held)
            self.assertEqual(pool.total, health - 30)
            self.assertEqual(charge.value(), 30)
        self.assertEqual(weapon.vars[53].value(), ammo)

    def test_body_replacement_retires_resource_and_old_mesh(self):
        run, hog, rein, _, _ = self.session()
        old = rein.body
        self.assertTrue(run.match.switch_hero(rein, heroes()[ROADHOG]))
        rein.mover.position[:] = (0, hog.mover.position[1], 4)
        rein.mover.body = None
        self.assertNotIn(old, run.match.combat.barriers)
        self.step(run, hog, rein, fire=2, raised=False)
        for _ in range(45):
            self.step(run, hog, rein, raised=False)
        self.assertNotIn(old, run.match.combat.barriers)
        self.assertLess(self.health(run, rein), 600)

    def test_active_geometry_resource_and_deadlines_export_read_only(self):
        import test_server_snapshot as stored

        from ow174.game.script import runtime
        from ow174.game.server_snapshot import snapshot_digest

        case = stored.ServerSnapshotTests()
        session, _, players = case.scene()
        rein = players[0]
        session.apply("switch_hero", {"account": rein.account_lo, "hero": 0x02E0000000000007})
        fight = rein.match.combat
        for offset in range(50):
            frame = rein.body_script.frame + 1
            rein.match.tick = fight.tick = frame
            fight.now = frame * combat.TICK
            rein.body_script.command(frame, 0 if offset < 30 else 2)
            fight._sync_barrier(rein)
        state = fight.barriers[rein.body]
        self.assertIsNotNone(state)
        self.assertTrue(state.active)
        with patch.object(runtime.Var, "value", side_effect=AssertionError("no VM evaluation")):
            first = snapshot_digest(case.capture(session))
            self.assertEqual(snapshot_digest(case.capture(session)), first)
            for name in ("current", "regen_after", "broken_until", "last_update"):
                value = getattr(state, name)
                setattr(state, name, value + 1)
                self.assertNotEqual(snapshot_digest(case.capture(session)), first)
                setattr(state, name, value)
            ability = fight._barrier_instance(rein).states[59]
            value = ability.last_sample
            ability.last_sample += 1
            self.assertNotEqual(snapshot_digest(case.capture(session)), first)
            ability.last_sample = value
            fight.barrier_geometry = replace(fight.barrier_geometry, forward_offset=.3)
            self.assertNotEqual(snapshot_digest(case.capture(session)), first)

    def test_external_geometry_digest_is_required_in_replay_configuration(self):
        from ow174.game import replay_manifest

        config = replay_manifest.runtime_configuration(quanta_us=16000, run_options={})
        self.assertTrue(barriers.GEOMETRY_SHA256)
        self.assertIn("ow174.game.barriers", str(config))
        self.assertIn(barriers.GEOMETRY_SHA256, str(config))


if __name__ == "__main__":
    unittest.main()
