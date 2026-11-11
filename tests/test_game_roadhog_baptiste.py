"""Roadhog match tests for Baptiste's authored Immortality Field cast."""

import os
import unittest
from dataclasses import replace
from unittest.mock import patch

import test_game_roadhog_defenses as defenses
from test_game_combat import aim
from test_game_mover import box

from ow174.game import combat, npc, npc_control, reinhardt_charge, stats
from ow174.game.collision import World
from ow174.game.script import nodes
from ow174.game.script.driver import ULT_CHARGE, ULT_COST


class BaptisteFieldTests(unittest.TestCase):
    @staticmethod
    def deploy(run, caster):
        fight = run.match.combat
        x, y, z = caster.mover.position
        angles = aim(fight._eye(caster), (x, y, z - 0.5))
        run.step({caster: (*angles, 16)})
        for _ in range(90):
            run.step({caster: (*angles, 0)})
            device = fight.baptiste_fields.devices.get(caster.body)
            if device is not None:
                return device
        raise AssertionError("Baptiste's field did not land")

    def test_actual_cast_lands_and_starts_field_graph(self):
        setup = defenses.RoadhogDefenseTests()
        run, hog, bap, _ = setup.session("Baptiste", distance=4)
        fight = run.match.combat
        fields = fight.baptiste_fields
        setup.step(run, hog, bap, cast=16)
        flight_body = None
        for _ in range(90):
            setup.step(run, hog, bap)
            flight = fields.flights.get(bap.body)
            if flight is not None:
                flight_body = flight.body
                self.assertEqual(flight.gravity, 20.0)
            if bap.body in fields.devices:
                break

        self.assertIsNotNone(flight_body)
        self.assertNotIn(bap.body, fields.flights)
        device = fields.devices[bap.body]
        self.assertNotEqual(device.body, flight_body)
        self.assertEqual(device.health, 150.0)
        self.assertLess(device.position[2], hog.mover.position[2])
        instance, = device.component.instances.values()
        self.assertEqual(instance.graph.index, 0x1762)
        self.assertEqual(instance.vars[1165].value(), 10)

        # The horizontal cast lands outside the owner's protection radius.
        vitals = combat.Vitals(bap.body, 200)
        vitals.now[combat.HEALTH_POOL] = 40
        fight.vitals[bap.body] = vitals
        setup.step(run, hog, bap, fire=2)
        for _ in range(25):
            setup.step(run, hog, bap)
        self.assertEqual(vitals.total, 0)
        self.assertTrue(vitals.dead)

    def test_actual_slug_stops_at_health_floor_after_field_lands(self):
        setup = defenses.RoadhogDefenseTests()
        run, hog, bap, _ = setup.session("Baptiste", distance=4)
        fight = run.match.combat
        vitals = combat.Vitals(bap.body, 200)
        vitals.now[combat.HEALTH_POOL] = 40
        fight.vitals[bap.body] = vitals
        x, y, z = bap.mover.position
        angles = aim(fight._eye(bap), (x, y, z - 0.5))
        run.step({bap: (*angles, 16)})
        for _ in range(90):
            run.step({bap: (*angles, 0)})
            if bap.body in fight.baptiste_fields.devices:
                break
        self.assertIn(bap.body, fight.baptiste_fields.devices)

        setup.step(run, hog, bap, fire=2)
        for _ in range(25):
            setup.step(run, hog, bap)
        self.assertAlmostEqual(vitals.total, 20.0)
        self.assertFalse(vitals.dead)

        device = fight.baptiste_fields.devices[bap.body]
        for _ in range(380):
            setup.step(run, hog, bap)
            if bap.body not in fight.baptiste_fields.devices:
                break
        self.assertNotIn(bap.body, fight.baptiste_fields.devices)
        self.assertTrue(device.component.world.retired)
        self.assertFalse(device.component.instances)
        setup.step(run, hog, bap, fire=2)
        for _ in range(25):
            setup.step(run, hog, bap)
        self.assertTrue(vitals.dead)

    def test_actual_slugs_destroy_the_device_and_remove_protection(self):
        setup = defenses.RoadhogDefenseTests()
        run, hog, bap, _ = setup.session("Baptiste", distance=4)
        fight = run.match.combat
        fields = fight.baptiste_fields
        x, y, z = bap.mover.position
        cast = aim(fight._eye(bap), (x, y, z - 0.5))
        run.step({bap: (*cast, 16)})
        for _ in range(90):
            run.step({bap: (*cast, 0)})
            if bap.body in fields.devices:
                break
        device = fields.devices[bap.body]
        bap.mover.position[0] += 2
        x, y, z = device.position
        shot = aim(fight._eye(hog), (x, y + 0.45, z))
        for expected in (100, 50, 0):
            run.step({hog: (*shot, 2)})
            for _ in range(65):
                run.step({hog: (*shot, 0)})
            self.assertEqual(device.health, expected)
        self.assertNotIn(bap.body, fields.devices)

        vitals = combat.Vitals(bap.body, 200)
        vitals.now[combat.HEALTH_POOL] = 40
        fight.vitals[bap.body] = vitals
        setup.step(run, hog, bap, fire=2)
        for _ in range(25):
            setup.step(run, hog, bap)
        self.assertTrue(vitals.dead)

    def test_actual_weapons_respect_field_team_eligibility(self):
        for attack in (1, 2):
            for caster_team, occluded, expected in ((1, False, 20), (0, False, 0), (1, True, 0)):
                with self.subTest(attack=attack, caster_team=caster_team, occluded=occluded):
                    setup = defenses.RoadhogDefenseTests()
                    run, hog, bap, target = setup.session("Baptiste", distance=3)
                    match = run.match
                    fight = match.combat
                    match.game_map = replace(match.game_map, free_for_all=False)
                    hog.team, bap.team, target.team = 0, caster_team, 1
                    bap.mover.position[0] += 2
                    bap.mover.body = None
                    y = hog.mover.position[1]
                    surfaces = list(box(-30, 30, y - 1, y, -30, 30))
                    if occluded:
                        surfaces.extend(box(0.9, 1.1, y, y + 3, 1, 8))
                    match.collision_world = World(surfaces)
                    vitals = combat.Vitals(target.body, 200)
                    vitals.now[combat.HEALTH_POOL] = 40
                    fight.vitals[target.body] = vitals
                    x, y, z = bap.mover.position
                    cast = aim(fight._eye(bap), (x, y, z - 0.5))
                    run.step({bap: (*cast, 16)})
                    for _ in range(90):
                        run.step({bap: (*cast, 0)})
                        if bap.body in fight.baptiste_fields.devices:
                            break
                    self.assertIn(bap.body, fight.baptiste_fields.devices)
                    setup.step(run, hog, target, fire=attack)
                    for _ in range(25):
                        setup.step(run, hog, target)
                    device = fight.baptiste_fields.devices[bap.body]
                    self.assertEqual(vitals.total, expected, (device.position, device.health,
                                     tuple(bap.mover.position), tuple(target.mover.position)))
                    self.assertEqual(vitals.dead, expected == 0)

    def test_actual_melee_damages_device(self):
        setup = defenses.RoadhogDefenseTests()
        run, hog, bap, _ = setup.session("Baptiste", distance=4)
        fight = run.match.combat
        x, y, z = bap.mover.position
        cast = aim(fight._eye(bap), (x, y, z - 0.5))
        run.step({bap: (*cast, 16)})
        for _ in range(90):
            run.step({bap: (*cast, 0)})
            if bap.body in fight.baptiste_fields.devices:
                break
        device = fight.baptiste_fields.devices[bap.body]
        hog.mover.position[2] = device.position[2] - 1.25
        hog.mover.body = None
        bap.mover.position[0] += 2
        bap.mover.body = None
        angles = aim(fight._eye(hog), device.centre())
        for _ in range(3):
            run.step({hog: (*angles, 0)})
        run.step({hog: (*angles, 0x800)})
        for _ in range(25):
            run.step({hog: (*angles, 0)})
        self.assertEqual(device.health, 120)

    def test_actual_hook_applies_control_and_respects_health_floor(self):
        setup = defenses.RoadhogDefenseTests()
        run, hog, bap, _ = setup.session("Baptiste", distance=6)
        fight = run.match.combat
        device = self.deploy(run, bap)
        vitals = combat.Vitals(bap.body, 200)
        vitals.now[combat.HEALTH_POOL] = 40
        fight.vitals[bap.body] = vitals
        setup.step(run, hog, bap, fire=8)
        controlled = False
        for _ in range(110):
            setup.step(run, hog, bap)
            controlled |= fight.hooks.controlled(bap.body)
        self.assertTrue(controlled)
        self.assertFalse(fight.hooks.controlled(bap.body))
        self.assertEqual(vitals.total, 20)
        self.assertEqual(device.health, 150)

    def test_actual_whole_hog_destroys_device(self):
        setup = defenses.RoadhogDefenseTests()
        run, hog, bap, _ = setup.session("Baptiste", distance=4)
        fight = run.match.combat
        device = self.deploy(run, bap)
        bap.mover.position[0] += 2
        bap.mover.body = None
        hog.mover.position[2] = device.position[2] - 1.5
        hog.mover.body = None
        angles = aim(fight._eye(hog), device.centre())
        hog.body_script.set_ult_charge(2240)
        run.step({hog: (*angles, 32)})
        for _ in range(150):
            run.step({hog: (*angles, 0)})
            if bap.body not in fight.baptiste_fields.devices:
                break
        self.assertEqual(device.health, 0)
        self.assertNotIn(bap.body, fight.baptiste_fields.devices)

    def test_whole_hog_health_floor_prevents_new_force_until_device_breaks(self):
        setup = defenses.RoadhogDefenseTests()
        run, hog, bap, victim = setup.session("Baptiste", distance=4)
        match = run.match
        fight = match.combat
        fields = fight.baptiste_fields
        match.game_map = replace(match.game_map, free_for_all=False)
        hog.team, bap.team, victim.team = 0, 1, 1
        y = hog.mover.position[1]
        match.collision_world = World(list(box(-30, 30, y - 1, y, -30, 30)))
        bap.mover.position[:] = (3, y, 4)
        victim.mover.position[:] = (0, y, 4)
        bap.mover.body = victim.mover.body = None
        device = self.deploy(run, bap)
        bap.mover.position[0] = 13
        bap.mover.body = None
        target = next(t for t in fight._targets() if t.owner is victim)
        pool = combat.Vitals(victim.body, 200)
        pool.now[combat.HEALTH_POOL] = 40
        fight.vitals[victim.body] = pool
        self.assertIs(fields.protecting_device(target), device)

        body = victim.body
        ultimate, = hog.body_script.ultimates
        hog.body_script.set_ult_charge(hog.body_script.component.entity_var(338).value())
        floor_contacts = []
        damage = fight._damage

        def observe_damage(shooter, hit, amount, *args, **kwargs):
            if hit.entity == body and pool.total == 20:
                floor_contacts.append(fields.protecting_device(hit))
            return damage(shooter, hit, amount, *args, **kwargs)

        def aim_at_victim():
            x, y, z = victim.mover.position
            return aim(fight._eye(hog), (x, y + 0.9, z))

        def force_packets():
            return {key for key in fight.impulses.seen if key[-1] == body}

        with patch.object(fight, "_damage", side_effect=observe_damage):
            run.step({hog: (*aim_at_victim(), 32)})
            for _ in range(120):
                run.step({hog: (*aim_at_victim(), 0)})
                if pool.total == 20:
                    break
            self.assertEqual(pool.total, 20)
            first_packet, = force_packets()
            self.assertGreater(fight.impulses.velocity(body)[2], 0)
            floor_contacts.clear()
            for _ in range(50):
                run.step({hog: (*aim_at_victim(), 0)})
            self.assertTrue(floor_contacts, "later volleys must still reach the body")
            self.assertTrue(all(protection is device for protection in floor_contacts))
            self.assertEqual(pool.total, 20)
            self.assertEqual(force_packets(), {first_packet})
            self.assertGreater(victim.mover.position[2], 4)
            self.assertNotIn(body, fight.impulses.live)
            self.assertEqual(device.health, 150)

        at_device = aim(fight._eye(hog), device.centre())
        for _ in range(215):
            run.step({hog: (*at_device, 0)})
            if bap.body not in fields.devices:
                break
        self.assertEqual(device.health, 0)
        self.assertNotIn(bap.body, fields.devices)
        self.assertEqual(pool.total, 20)
        for _ in range(75):
            run.step({hog: (*aim_at_victim(), 0)})
            if pool.dead:
                break
        self.assertTrue(pool.dead)
        self.assertTrue(ultimate.flags & 1)
        death_position = tuple(victim.mover.position)
        run.step({hog: (*aim_at_victim(), 0)})
        self.assertNotIn(body, fight.impulses.live)
        self.assertEqual(tuple(victim.mover.position), death_position)

    def test_actual_field_cooldown_refuses_and_reopens(self):
        setup = defenses.RoadhogDefenseTests()
        run, hog, bap, _ = setup.session("Baptiste", distance=4)
        fight = run.match.combat
        device = self.deploy(run, bap)
        instance = next(i for i in bap.body_script.component.instances.values() if i.graph.index == 0x1761)
        ability, = (state for state in instance.states.values() if isinstance(state, nodes.Ability))
        for _ in range(45):
            if ability.remaining() > 0:
                break
            setup.step(run, hog, bap)
        initial = ability.remaining()
        self.assertGreater(initial, 0)
        setup.step(run, hog, bap, cast=16)
        for _ in range(35):
            setup.step(run, hog, bap)
        self.assertLess(ability.remaining(), initial)
        self.assertIs(fight.baptiste_fields.devices[bap.body], device)
        self.assertNotIn(bap.body, fight.baptiste_fields.flights)
        for _ in range(2000):
            if ability.remaining() == 0:
                break
            setup.step(run, hog, bap)
        self.assertEqual(ability.remaining(), 0)
        self.assertNotIn(bap.body, fight.baptiste_fields.devices)
        self.assertNotEqual(self.deploy(run, bap).body, device.body)
        for _ in range(45):
            if ability.remaining() > 0:
                break
            setup.step(run, hog, bap)
        self.assertGreater(ability.remaining(), 0)

    @unittest.skipUnless(os.environ.get("ORPHEUS_NPC_GRAPHS"), "actual five NPC graphs required")
    def test_actual_field_protects_all_five_npc_bodies_from_road_hog_slug(self):
        self.check_field_against_npcs((("slug", 2, 4),))

    @unittest.skipUnless(os.environ.get("ORPHEUS_NPC_GRAPHS"), "actual five NPC graphs required")
    def test_actual_field_caps_other_roadhog_attack_forms_on_all_five_npcs(self):
        self.check_field_against_npcs((
            ("primary", 1, 4),
            ("fragments", 2, 12),
            ("melee", 0x800, 1.5),
            ("hook", 8, 4),
            ("whole hog", 32, 4),
        ))

    def check_field_against_npcs(self, attacks):
        import test_game_roadhog_npcs as actual

        actual.RoadhogFiveNpcTests.setUpClass()
        self.addCleanup(actual.RoadhogFiveNpcTests.tearDownClass)
        for definition in actual.RoadhogFiveNpcTests.definitions.values():
            for attack, button, distance in attacks:
                with self.subTest(npc=definition.name, attack=attack):
                    self.check_field_against_npc(definition, attack, button, distance)

    def check_field_against_npc(self, definition, attack, button, distance):
        run, hog, bap, rear = defenses.RoadhogDefenseTests().session("Baptiste", distance=3)
        match = run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        hog.team, bap.team, rear.team = 0, 1, 0
        y = hog.mover.position[1]
        hog.mover.position[:] = (0, y, 4 - distance)
        bap.mover.position[0] = 3
        hog.mover.body = bap.mover.body = None
        match.collision_world = World(list(box(-30, 30, y - 1, y, -30, 30)))
        device = self.deploy(run, bap)
        bot = npc.spawn(match, definition, (0, y, 4), team=1)
        target = next(t for t in fight._targets() if t.owner is bot)
        vitals = fight.vitals_of(target)
        floor = 0.1 * vitals.permanent_maximum
        vitals.now.update(dict.fromkeys(vitals.now, 0.0))
        vitals.now[combat.HEALTH_POOL] = floor + 15
        angles = aim(fight._eye(hog), (0, y + target.height / 2, 4))
        for _ in range(4):
            run.step({hog: (*angles, 0)})
        self.assertIs(fight.baptiste_fields.protecting_device(target), device)
        script = hog.body_script
        charge = script.component.entity_var(ULT_CHARGE)
        if button == 32:
            script.set_ult_charge(script.component.entity_var(ULT_COST).value())
        run.step({hog: (*angles, button)})
        fragments_released = False
        for _ in range(95):
            run.step({hog: (*angles, 0)})
            fragments_released |= len(fight.rockets) > 1
            if vitals.total <= floor + 1e-6:
                break
        self.assertAlmostEqual(vitals.total, floor)
        self.assertFalse(vitals.dead)
        self.assertEqual(device.health, 150)
        self.assertAlmostEqual(hog.stats.value(stats.HERO_DAMAGE), 15)
        self.assertAlmostEqual(charge.value(), 0 if button == 32 else 15)
        if attack == "fragments":
            self.assertTrue(fragments_released)
        elif button == 8:
            self.assertTrue(fight.hooks.controlled(bot.entity))
        elif button == 32:
            self.assertTrue(script.ultimates[0].flags & nodes.Ability.RUNNING)
            self.assertGreater(fight.impulses.velocity(bot.entity)[2], 0)
        if button in (8, 32):
            initial_z = bot.position[2]
            run.step({hog: (*angles, 0)})
            if button == 8:
                self.assertLess(bot.position[2], initial_z)
            else:
                self.assertGreater(bot.position[2], initial_z)
            self.assertAlmostEqual(vitals.total, floor)

    @unittest.skipUnless(os.environ.get("ORPHEUS_NPC_GRAPHS"), "actual five NPC graphs required")
    def test_actual_field_protects_roadhog_from_all_five_npc_attacks(self):
        import test_game_roadhog_npcs as actual

        from ow174.game import npc, npc_control

        actual.RoadhogFiveNpcTests.setUpClass()
        self.addCleanup(actual.RoadhogFiveNpcTests.tearDownClass)
        for definition in actual.RoadhogFiveNpcTests.definitions.values():
            with self.subTest(npc=definition.name):
                run, hog, bap, rear = defenses.RoadhogDefenseTests().session("Baptiste", distance=3)
                match = run.match
                fight = match.combat
                match.game_map = replace(match.game_map, free_for_all=False)
                hog.team = bap.team = rear.team = 0
                for player in run.players:
                    player.client = actual.NpcClient(player)
                y = hog.mover.position[1]
                bap.mover.position[:] = (2, y, 1)
                rear.mover.position[:] = (7, y, 6)
                bap.mover.body = rear.mover.body = None
                match.collision_world = World(list(box(-30, 30, y - 1, y, -30, 30)))
                device = self.deploy(run, bap)
                distance = 2 if definition.name == "Assassin" else 10
                bot = npc.spawn(match, definition, (0, y, distance), team=1)
                target = next(t for t in fight._targets() if t.owner is hog)
                vitals = fight.vitals_of(target)
                floor = 0.1 * sum(vitals.most.values())
                vitals.now[combat.HEALTH_POOL] = floor + 1
                victim = next(t for t in fight._targets() if t.owner is bot)
                hog.mover.yaw, hog.mover.pitch = aim(fight._eye(hog), (0, y + victim.height / 2, distance))
                self.assertEqual(npc_control.start(match, hog), bot.entity)
                for _ in range(200):
                    run.step({})
                    if vitals.total <= floor:
                        break
                npc_control.stop(match)
                self.assertAlmostEqual(vitals.total, floor)
                self.assertFalse(vitals.dead)
                self.assertEqual(device.health, 150)

    @unittest.skipUnless(os.environ.get("ORPHEUS_NPC_GRAPHS"), "actual five NPC graphs required")
    def test_actual_field_protects_roadhog_from_manual_npc_melee_and_wall_slam(self):
        import test_game_roadhog_npcs as actual

        actual.RoadhogFiveNpcTests.setUpClass()
        self.addCleanup(actual.RoadhogFiveNpcTests.tearDownClass)
        definitions = {d.name: d for d in actual.RoadhogFiveNpcTests.definitions.values()}
        for name, action in (("Trooper", "melee"), ("Heavy Assault", "melee"), ("Heavy Assault", "charge")):
            with self.subTest(npc=name, action=action):
                self.check_field_against_incoming_npc_special(definitions[name], action)

    def check_field_against_incoming_npc_special(self, definition, action):
        run, hog, bap, rear = defenses.RoadhogDefenseTests().session("Baptiste", distance=3)
        match = run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        hog.team = bap.team = rear.team = 0
        _, y, _ = hog.mover.position
        hog.mover.position[:] = (0, y, 6)
        bap.mover.position[:] = (-5, y, 6)
        rear.mover.position[:] = (15, y, 6)
        hog.mover.body = bap.mover.body = rear.mover.body = None
        charging = action == "charge"
        surfaces = list(box(-30, 30, y - 1, y, -30, 30))
        if charging:
            surfaces += box(-15, 15, y, y + 12, 10, 10.2)
        match.collision_world = World(surfaces)
        device = self.deploy(run, bap)
        enemy_z = 0 if charging else 7.55
        bot = npc.spawn(match, definition, (0, y, enemy_z), team=1)
        target = next(t for t in fight._targets() if t.owner is hog)
        enemy = next(t for t in fight._targets() if t.owner is bot)
        pool = fight.vitals_of(target)
        floor = .1 * pool.permanent_maximum
        pool.now[combat.HEALTH_POOL] = floor + 1
        self.assertIs(fight.baptiste_fields.protecting_device(target), device)
        look = aim(fight._eye(hog), (0, y + enemy.height / 2, enemy_z))
        run.step({hog: (*look, 0)})
        self.assertEqual(npc_control.start(match, hog, action=action), bot.entity)
        pinned = False
        for _ in range(130):
            run.step({})
            if reinhardt_charge.pinned(fight, hog.body):
                pinned = True
                self.assertAlmostEqual(pool.total, floor + 1)
            if pool.total < floor + 1:
                break
        self.assertEqual(pinned, charging)
        self.assertAlmostEqual(pool.total, floor)
        self.assertFalse(pool.dead)
        self.assertFalse(reinhardt_charge.pinned(fight, hog.body))
        if definition.name == "Heavy Assault" and not charging:
            self.assertGreater(hog.mover.velocity[1], 0)
        self.assertEqual(device.health, 150)
        npc_control.stop(match)
        for _ in range(30):
            run.step({})
        self.assertAlmostEqual(pool.total, floor)

    def test_actual_slug_credits_only_pool_loss_above_field_floor(self):
        from ow174.game.script.driver import ULT_CHARGE
        from ow174.game.world import ARMOUR_POOL, HEALTH_POOL, SHIELDS_POOL

        setup = defenses.RoadhogDefenseTests()
        run, hog, bap, _ = setup.session("Baptiste", distance=4)
        fight = run.match.combat
        device = self.deploy(run, bap)
        vitals = combat.Vitals(bap.body, 100, 60, 40)
        vitals.now.update({HEALTH_POOL: 10, ARMOUR_POOL: 15, SHIELDS_POOL: 8})
        fight.vitals[bap.body] = vitals
        charge = hog.body_script.component.entity_var(ULT_CHARGE)

        for _ in range(2):
            setup.step(run, hog, bap, fire=2)
            for _ in range(65):
                setup.step(run, hog, bap)
            self.assertEqual(vitals.now, {HEALTH_POOL: 10, ARMOUR_POOL: 10, SHIELDS_POOL: 0})
            self.assertEqual(vitals.total, 20)
            self.assertEqual(vitals.ult_healing_deficit, 13)
            self.assertEqual(charge.value(), 13)
            self.assertEqual(len(hog.client.messages(combat.HIT)), 1)
            self.assertEqual(device.health, 150)

    def field_ana_scene(self):
        from test_game_roadhog_ana import RoadhogAnaTests

        from ow174.game.content import heroes

        run, hog, bap, ana = defenses.RoadhogDefenseTests().session("Baptiste", distance=4)
        match = run.match
        match.game_map = replace(match.game_map, free_for_all=False)
        match.switch_hero(ana, next(hero for hero in heroes().values() if hero.name == "Ana"))
        hog.team, bap.team, ana.team = 0, 0, 1
        y = hog.mover.position[1]
        bap.mover.position[:] = (7, y, 1)
        ana.mover.position[:] = (0, y, 6)
        bap.mover.body = ana.mover.body = None
        match.collision_world = World(list(box(-30, 30, y - 1, y, -30, 30)))
        return run, hog, bap, ana, RoadhogAnaTests()

    def test_actual_grenade_denies_field_healing_but_keeps_damage_floor(self):
        run, hog, bap, ana, ana_test = self.field_ana_scene()
        fight = run.match.combat
        deadline = ana_test.land(run, hog, ana, 16)
        vitals = ana_test.pool(run, hog)
        vitals.now[combat.HEALTH_POOL] = 45

        device = self.deploy(run, bap)
        self.assertLess(run.frame, deadline)
        self.assertEqual(vitals.total, 45)
        for _ in range(65):
            ana_test.step(run, hog, ana)
        with patch.object(fight, "_damage", wraps=fight._damage) as damage:
            ana_test.step(run, hog, ana, cast=8)
            for _ in range(45):
                ana_test.step(run, hog, ana)
        self.assertTrue(any(call.args[0] is ana and call.args[1].owner is hog and call.args[2] > 0
                            for call in damage.call_args_list))
        self.assertIn(hog.body, fight.antiheal)
        self.assertFalse(vitals.dead)
        self.assertEqual(vitals.total, 45)

        while run.frame < deadline:
            ana_test.step(run, hog, ana)
        self.assertNotIn(hog.body, fight.antiheal)
        self.assertIs(fight.baptiste_fields.devices[bap.body], device)
        self.assertEqual(vitals.total, 60)

    def test_field_recovers_first_at_antiheal_expiry_then_breather_credits_the_remainder(self):
        from ow174.game import stats
        from ow174.game.commands import Command
        from ow174.game.script.driver import ULT_CHARGE

        run, hog, bap, ana, ana_test = self.field_ana_scene()
        fight = run.match.combat
        pool = ana_test.pool(run, hog)
        pool.damage(520)
        fight._sync_body_health(hog, pool)
        deadline = ana_test.land(run, hog, ana, 16)
        self.assertEqual(pool.total, 30)
        self.assertEqual(pool.ult_healing_deficit, 50)
        device = self.deploy(run, bap)
        target = next(t for t in fight._targets() if t.owner is hog)
        self.assertIs(fight.baptiste_fields.protecting_device(target), device)
        self.assertEqual(pool.total, 30)
        self.assertEqual(bap.stats.value(stats.HEALING), 0)
        while run.frame < deadline - 57:
            ana_test.step(run, hog, ana)
        ana_test.step(run, hog, ana, fire=16)
        while run.frame < deadline - 1:
            ana_test.step(run, hog, ana)
        self.assertEqual(fight.incoming_damage_scale(hog), .5)
        self.assertEqual(pool.total, 30)
        charge = hog.body_script.component.entity_var(ULT_CHARGE)
        self.assertEqual(charge.value(), 0)

        run.frame += 1
        run.match.update(run.frame * combat.TICK, run.frame)
        self.assertEqual(run.frame, deadline)
        self.assertNotIn(hog.body, fight.antiheal)
        self.assertEqual(pool.total, 60)
        self.assertEqual(pool.ult_healing_deficit, 20)
        self.assertEqual(bap.stats.value(stats.HEALING), 30)
        self.assertEqual(charge.value(), 0)
        hog.take_commands([Command(run.frame)])
        for _ in range(35):
            ana_test.step(run, hog, ana)
        self.assertGreater(pool.total, 80)
        self.assertEqual(pool.ult_healing_deficit, 0)
        self.assertAlmostEqual(charge.value(), 20, places=4)
        self.assertEqual(bap.stats.value(stats.HEALING), 30)
        self.assertEqual(device.health, 150)

    def test_floor_clamps_grenade_and_reduced_scrap_then_cleanse_enables_breather(self):
        from test_game_combat import in_world

        from ow174.game import stats
        from ow174.game.content import heroes
        from ow174.game.script.driver import ULT_CHARGE

        run, hog, bap, enemy, ana_test = self.field_ana_scene()
        match = run.match
        fight = match.combat
        zarya = match.add_player(8, "Zarya", 0x02E0000000000068, hog.team, False)
        in_world(match, zarya)
        self.assertTrue(match.switch_hero(zarya, heroes()[0x02E0000000000068]))
        run.players.append(zarya)
        x, y, z = hog.mover.position
        zarya.mover.position[:] = (x - 5, y, z)
        zarya.mover.body = None
        device = self.deploy(run, bap)
        pool = ana_test.pool(run, hog)
        pool.damage(520)
        fight._sync_body_health(hog, pool)
        deadline = ana_test.land(run, hog, enemy, 16)
        self.assertEqual(pool.total, 60)
        self.assertEqual(pool.ult_healing_deficit, 20)
        self.assertTrue(match.switch_hero(enemy, hog.hero))
        enemy.body_script.ult_seconds = 0
        enemy.mover.position[:] = (x, y, z + 2)
        enemy.mover.body = None
        for _ in range(30):
            run.step({})
        run.step({hog: (0, 0, 16)})
        for _ in range(40):
            run.step({})
        self.assertEqual(fight.incoming_damage_scale(hog), .5)
        self.assertEqual(pool.total, 60)
        enemy_charge = enemy.body_script.component.entity_var(ULT_CHARGE).value()
        angles = aim(fight._eye(enemy), (x, y + .7, z))
        with patch.object(fight, "_damage", wraps=fight._damage) as damage:
            run.step({enemy: (*angles, 1)})
            for _ in range(20):
                run.step({})
        self.assertTrue(any(call.args[0] is enemy and call.args[1].owner is hog and call.args[2] > 0
                            for call in damage.call_args_list))
        self.assertEqual(enemy.body_script.component.entity_var(ULT_CHARGE).value(), enemy_charge)
        self.assertEqual(pool.total, 60)
        self.assertEqual(pool.ult_healing_deficit, 20)
        self.assertIn(hog.body, fight.antiheal)
        ana_test.project(run, hog, zarya)
        self.assertNotIn(hog.body, fight.antiheal)
        for _ in range(30):
            run.step({})
        self.assertLess(run.frame, deadline)
        self.assertGreater(pool.total, 80)
        self.assertAlmostEqual(hog.body_script.component.entity_var(ULT_CHARGE).value(), 20, places=4)
        self.assertEqual(bap.stats.value(stats.HEALING), 0)
        self.assertEqual(pool.ult_healing_deficit, 0)
        self.assertEqual(device.health, 150)

    def test_overlapping_fields_keep_one_floor_after_the_first_device_breaks(self):
        from test_game_combat import in_world

        from ow174.game.script.driver import ULT_CHARGE

        run, shooter, first, second = defenses.RoadhogDefenseTests().session("Baptiste", distance=4)
        match = run.match
        fight = match.combat
        match.game_map = replace(match.game_map, free_for_all=False)
        shooter.team, first.team, second.team = 0, 1, 1
        self.assertTrue(match.switch_hero(second, first.hero))
        hog = match.add_player(8, "Protected Roadhog", shooter.hero.guid, 1, False)
        in_world(match, hog)
        self.assertTrue(match.switch_hero(hog, shooter.hero))
        run.players.append(hog)
        y = shooter.mover.position[1]
        match.collision_world = World(list(box(-30, 30, y - 1, y, -30, 30)))
        poses = ((shooter, (0, y, 0)), (first, (4, y, 4)), (second, (-4, y, 4)), (hog, (0, y, 4)))
        for actor, point in poses:
            actor.mover.position[:] = point
            actor.mover.body = None
        for _ in range(30):
            run.step({})
        near = self.deploy(run, first)
        backup = self.deploy(run, second)
        first.mover.position[0] = 13
        second.mover.position[0] = -13
        first.mover.body = second.mover.body = None
        fields = fight.baptiste_fields
        target = next(t for t in fight._targets() if t.owner is hog)
        self.assertIs(fields.protecting_device(target), near)
        pool = fight.vitals_of(target)
        pool.damage(520)
        fight._sync_body_health(hog, pool)
        charge = shooter.body_script.component.entity_var(ULT_CHARGE)
        at_hog = aim(fight._eye(shooter), (0, y + .7, 4))
        at_device = aim(fight._eye(shooter), near.centre())

        def shoot(look, ticks, button=2):
            run.step({shooter: (*look, button)})
            for _ in range(ticks):
                run.step({shooter: (*look, 0)})

        shoot(at_hog, 65)
        self.assertEqual(pool.total, 60)
        self.assertEqual(pool.ult_healing_deficit, 20)
        self.assertEqual(charge.value(), 15)
        for health in (100, 50, 0):
            shoot(at_device, 65)
            self.assertEqual(near.health, health)
            self.assertEqual(backup.health, 150)
        self.assertNotIn(first.body, fields.devices)
        self.assertIs(fields.protecting_device(target), backup)
        with patch.object(fight, "_damage", wraps=fight._damage) as damage:
            shoot(at_hog, 15)
        self.assertTrue(any(call.args[0] is shooter and call.args[1].owner is hog
                            for call in damage.call_args_list))
        self.assertIs(fields.protecting_device(target), backup)
        self.assertEqual(pool.total, 60)
        self.assertEqual(pool.ult_healing_deficit, 20)
        self.assertEqual(charge.value(), 15)
        weapon = next(i for i in shooter.body_script.component.instances.values() if i.graph.index == 0x01C6)
        for _ in range(160):
            run.step({})
        self.assertFalse(fields.devices)
        self.assertEqual(weapon.vars[53].value(), 5)
        shoot(at_hog, 25, button=1)
        self.assertEqual(weapon.vars[53].value(), 4)
        self.assertTrue(pool.dead)
        self.assertAlmostEqual(charge.value(), 60, places=4)

    def test_owner_switch_or_leave_retires_flight_and_landed_device(self):
        from ow174.game.content import SOLDIER, heroes

        for leave, landed in ((False, False), (False, True), (True, False), (True, True)):
            with self.subTest(leave=leave, landed=landed):
                setup = defenses.RoadhogDefenseTests()
                run, hog, bap, target = setup.session("Baptiste", distance=3)
                match = run.match
                fight = match.combat
                fields = fight.baptiste_fields
                match.game_map = replace(match.game_map, free_for_all=False)
                hog.team, bap.team, target.team = 0, 1, 1
                y = hog.mover.position[1]
                bap.mover.position[:] = (2, y, 3)
                bap.mover.body = None
                match.collision_world = World(list(box(-30, 30, y - 1, y, -30, 30)))
                source = bap.body
                if landed:
                    device = self.deploy(run, bap)
                else:
                    setup.step(run, hog, bap, cast=16)
                    for _ in range(15):
                        if source in fields.flights:
                            break
                        setup.step(run, hog, bap)
                    self.assertIn(source, fields.flights)

                if leave:
                    match.leave(bap)
                    run.players.remove(bap)
                else:
                    match.switch_hero(bap, heroes()[SOLDIER])
                self.assertNotIn(source, fields.flights)
                self.assertNotIn(source, fields.devices)
                if landed:
                    self.assertTrue(device.component.world.retired)
                    self.assertFalse(device.component.instances)
                bap.mover.position[:] = (8, y, 8)
                bap.mover.body = None
                vitals = combat.Vitals(target.body, 200)
                vitals.now[combat.HEALTH_POOL] = 40
                fight.vitals[target.body] = vitals
                setup.step(run, hog, target)
                setup.step(run, hog, target, fire=2)
                for _ in range(35):
                    setup.step(run, hog, target)
                self.assertTrue(vitals.dead)
                self.assertFalse(fields.devices)
