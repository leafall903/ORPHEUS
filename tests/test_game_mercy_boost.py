"""Outgoing source attribution and projectile launch-time boost on real attacks."""

import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import test_game_mercy_staff as staff_tests
from test_game_combat import aim, in_world
from test_game_mercy_npc import clear_graphs

from ow174.game import cryo, defense_matrix, phase_defenses, stats
from ow174.game.commands import FIRE, SECONDARY
from ow174.game.content import find_hero, heroes
from ow174.game.script import expr, graph, rulesets, runtime
from ow174.game.script.driver import ULT_CHARGE
from ow174.game.script.projectile_defense import SHIELD_TAG
from ow174.game.script.regeneration import MERCY
from ow174.game.script.support import boost_amount

AMPLIFIED = 0x086000000000048A
ANA = 0x02E000000000013B

EFFECT_BUNDLE = (
    Path("D:/overwatch 1.7.4/verification/hero-lanes/0004/research") / "game_graphs_effects_174.json.gz"
)


@unittest.skipUnless(EFFECT_BUNDLE.is_file(), "external extracted effect pack required")
class MercyBoostTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(graph, "GRAPHS_PATH", EFFECT_BUNDLE))
        clear_graphs()
        self.addCleanup(clear_graphs)
        fixture = staff_tests.MercyStaffTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture = fixture
        self.run, self.match = fixture.run, fixture.match
        self.mercy, self.ally, self.enemy = fixture.mercy, fixture.ally, fixture.enemy
        self.fight = fixture.fight

    def step(self, count, *, beam=SECONDARY, attack=0):
        for _ in range(count):
            x, y, z = self.ally.mover.position
            beam_yaw, beam_pitch = aim(self.fight._eye(self.mercy), (x, y + 1, z))
            x, y, z = self.enemy.mover.position
            shot_yaw, shot_pitch = aim(self.fight._eye(self.ally), (x, y + 0.8, z))
            self.run.step({self.mercy: (beam_yaw, beam_pitch, beam),
                           self.ally: (shot_yaw, shot_pitch, attack)})

    def enemy_health(self):
        target = next(t for t in self.fight._targets() if t.owner is self.enemy)
        return self.fight.vitals_of(target).total

    def test_secondary_amplifies_actual_rifle_damage_and_keeps_damage_on_ally(self):
        self.step(10)
        self.step(1, attack=FIRE)
        self.step(10)
        self.assertAlmostEqual(self.enemy_health(), 174, places=4)
        self.assertAlmostEqual(self.ally.stats.value(stats.HERO_DAMAGE), 26, places=4)
        self.assertEqual(self.mercy.stats.value(stats.HERO_DAMAGE), 0)
        self.assertEqual(self.mercy.stats.value(stats.HEALING), 0)
        self.assertEqual(self.fixture.vitals.total, 100)  # secondary does not heal

    def test_ashe_ultimate_writes_authored_pet_position_from_live_mover(self):
        self.assertTrue(self.match.switch_hero(self.ally, find_hero("ashe")))
        self.ally.mover.position[:] = (3.25, 1, 5.75)
        self.ally.mover.body = None
        script = self.ally.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        ultimate = next(i for i in script.component.instances.values() if i.graph.index == 0x1536)
        create_positions = []
        run_node = runtime.Instance.run_node

        def observe(instance, node, caller):
            if instance is ultimate and node.pos == 11:
                x, y, z = self.ally.mover.position
                expected = expr.Vec3(x, y + self.ally.mover._lift() - 0.5, z)
                actual = instance.find_var(0, 11134).value()
                create_positions.append((actual, expected))
            return run_node(instance, node, caller)

        with patch.object(runtime.Instance, "run_node", observe):
            self.step(1, attack=0x20)
        (actual, expected), = create_positions
        self.assertIsInstance(actual, expr.Vec3)
        self.assertAlmostEqual(actual.x, expected.x)
        self.assertAlmostEqual(actual.y, expected.y, places=5)
        self.assertAlmostEqual(actual.z, expected.z)

    def test_mercy_staff_heals_rifle_damage_to_ashe_pet_created_by_ultimate(self):
        if graph.entity_body(0x04000000000025C3) is None or graph.graph(0x153B) is None:
            self.skipTest("prepared 104319 B.O.B. graph supplement required")
        self.assertTrue(self.match.switch_hero(self.ally, find_hero("ashe")))
        self.mercy.mover.position[:] = (0, 1, 0)
        self.ally.mover.position[:] = (0, 1, 5)
        self.enemy.mover.position[:] = (0, 1, 10)
        for player in (self.mercy, self.ally, self.enemy):
            player.mover.body = None
        script = self.ally.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        self.step(1, attack=0x20)
        pet, = self.match.bots
        self.assertEqual(script.component.entity_var(512).value(), expr.Entity(pet.entity))
        self.assertEqual(script.component.entity_var(ULT_CHARGE).value(), 0)
        self.assertEqual(pet.body, 0x04000000000025C3)
        self.assertEqual(pet.team, self.ally.team)
        vitals = self.fight.vitals[pet.entity]
        self.assertEqual(vitals.total, 1200)
        component = pet.body_script.component
        self.assertTrue(any(i.graph.index == 0x153B and i.initialized for i in component.instances.values()))
        behavior, = (i for i in component.instances.values() if i.graph.index == 0x1537)
        self.assertEqual(behavior.source_entity(), self.ally.body)
        self.ally.mover.position[:] = (4, 1, 5)
        self.ally.mover.body = None
        self.mercy.body_script.component.set(12918, 0.0)
        before = self.mercy.stats.value(stats.HEALING)
        x, y, z = pet.position
        target = (x, y + 0.8, z)
        rifle = aim(self.fight._eye(self.enemy), target)
        self.run.step({self.enemy: (*rifle, FIRE)})
        self.assertEqual(vitals.total, 1180)
        beam = aim(self.fight._eye(self.mercy), target)
        for _ in range(35):
            self.run.step({self.mercy: (*beam, FIRE)})
        self.assertAlmostEqual(vitals.total, 1200, places=4)
        self.assertAlmostEqual(self.mercy.stats.value(stats.HEALING) - before, 20, places=4)
        self.assertTrue(behavior.initialized)
        self.assertTrue(behavior.node_state(behavior.graph.node(7)).active)
        self.assertIsNone(component.fault)
        queue = component.world.pet_play_queue
        self.assertTrue(self.match.switch_hero(self.ally, find_hero("soldier")))
        self.assertNotIn(pet, self.match.bots)
        self.assertNotIn(pet.entity, self.fight.vitals)
        self.assertIsNone(pet.body_script)
        self.assertTrue(queue.closed)

    def test_ashe_pet_retires_when_original_winddown_finishes(self):
        if graph.entity_body(0x04000000000025C3) is None or graph.graph(0x153B) is None:
            self.skipTest("prepared 104319 B.O.B. graph supplement required")
        self.assertTrue(self.match.switch_hero(self.ally, find_hero("ashe")))
        script = self.ally.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        self.step(1, beam=0, attack=0x20)
        pet, = self.match.bots
        component = pet.body_script.component
        queue = component.world.pet_play_queue
        behavior, = (i for i in component.instances.values() if i.graph.index == 0x1537)
        self.step(3, beam=0)
        self.assertEqual(behavior.find_var(0, 14669).value(), (10.0,))
        shooting = behavior.node_state(behavior.graph.node(35))
        winddown = behavior.node_state(behavior.graph.node(39))
        saw_shooting = False
        for _ in range(1000):
            if winddown.active:
                break
            self.step(1, beam=0)
            if shooting.active:
                self.assertEqual(shooting.ms, 10000)
                saw_shooting = True
        self.assertTrue(saw_shooting)
        self.assertTrue(winddown.active)
        self.assertIn(pet, self.match.bots)
        self.step(45, beam=0)
        self.assertNotIn(pet, self.match.bots)
        self.assertNotIn(pet.entity, self.fight.vitals)
        self.assertIsNone(pet.body_script)
        self.assertTrue(queue.closed)
        self.assertIsNone(getattr(script.world, "bob", None))
        self.assertIs(self.ally.body_script, script)
        self.assertIsNone(component.fault)

    def test_ashe_pet_uses_configured_shooting_duration_scalar(self):
        if graph.entity_body(0x04000000000025C3) is None or graph.graph(0x153B) is None:
            self.skipTest("prepared 104319 B.O.B. graph supplement required")
        default = rulesets.default
        ashe = find_hero("ashe")

        def configured(key, hero, mode):
            return 0.5 if key == 0x32E8 and hero == ashe.guid else default(key, hero, mode)

        with patch.object(rulesets, "default", side_effect=configured):
            self.assertTrue(self.match.switch_hero(self.ally, ashe))
            script = self.ally.body_script
            script.set_ult_charge(script.component.entity_var(338).value())
            self.step(1, beam=0, attack=0x20)
        pet, = self.match.bots
        behavior, = (i for i in pet.body_script.component.instances.values() if i.graph.index == 0x1537)
        self.step(3, beam=0)
        self.assertEqual(behavior.find_var(0, 14669).value(), (5.0,))
        shooting = behavior.node_state(behavior.graph.node(35))
        for _ in range(350):
            if shooting.active:
                break
            self.step(1, beam=0)
        self.assertTrue(shooting.active)
        self.assertEqual(shooting.ms, 5000)
        self.step(300, beam=0)
        self.assertTrue(shooting.active)
        self.step(20, beam=0)
        self.assertFalse(shooting.active)

    def test_ashe_pet_automatically_fires_with_mercy_boost_when_enemy_is_visible(self):
        from test_game_mover import FLOOR, wall

        from ow174.game.collision import World

        if graph.entity_body(0x04000000000025CB) is None or graph.graph(0x153A) is None:
            self.skipTest("prepared 104319 B.O.B. AI supplement required")
        self.assertTrue(self.match.switch_hero(self.ally, find_hero("ashe")))
        for player, z in ((self.mercy, 0), (self.ally, 5), (self.enemy, 10)):
            player.mover.position[:] = (0, 1, z)
            player.mover.body = None
        script = self.ally.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        self.run.step({self.ally: (0, 0, 0x20)})
        pet, = self.match.bots
        self.ally.mover.position[:] = (4, 1, 5)
        self.ally.mover.body = None
        self.mercy.body_script.component.set(12918, 0.0)
        charge = self.mercy.body_script.component.entity_var(ULT_CHARGE)
        x, y, z = pet.position
        beam = aim(self.fight._eye(self.mercy), (x, y + 0.8, z))
        clear = self.match.collision_world
        self.match.collision_world = World(FLOOR + wall(-3, 7.5, 3, 7.5))

        def step():
            self.run.step({self.mercy: (*beam, SECONDARY)})

        health = self.enemy_health()
        with patch.object(self.fight, "_shoot", wraps=self.fight._shoot) as shoot:
            for _ in range(340):
                step()
            self.assertEqual(shoot.call_count, 0)
            self.assertEqual(self.enemy_health(), health)
            self.assertGreater(pet.position[2], z + 0.5)
            self.assertLess(pet.position[2], 7.5)
            self.match.collision_world = clear
            for _ in range(20):
                step()
                if shoot.call_count:
                    break
            self.assertEqual(shoot.call_count, 1)
            shooter, _, state, *_ = shoot.call_args.args
            self.assertIs(shooter, pet)
            self.assertEqual((state.instance.graph.index, state.node.pos), (0x153B, 14))
            self.assertAlmostEqual(health - self.enemy_health(), 18.2, places=4)
            self.assertAlmostEqual(self.mercy.stats.value(AMPLIFIED), 4.2, places=4)
            self.assertAlmostEqual(charge.value(), 4.2, places=4)
            self.enemy.mover.position[:] = (0, 1, 55)
            self.enemy.mover.body = None
            for _ in range(30):
                step()
            self.assertEqual(shoot.call_count, 1)
            self.assertFalse(pet.attack_input)

    def test_enemy_ashe_pet_automatically_hits_mercy(self):
        if graph.entity_body(0x04000000000025CB) is None or graph.graph(0x153A) is None:
            self.skipTest("prepared 104319 B.O.B. AI supplement required")
        self.assertTrue(self.match.switch_hero(self.enemy, find_hero("ashe")))
        for player, z in ((self.mercy, 0), (self.ally, 50), (self.enemy, 5)):
            player.mover.position[:] = (0, 1, z)
            player.mover.body = None
        script = self.enemy.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        self.run.step({self.enemy: (0, 0, 0x20)})
        pet, = self.match.bots
        target = next(t for t in self.fight._targets() if t.owner is self.mercy)
        vitals = self.fight.vitals_of(target)
        with patch.object(self.fight, "_shoot", wraps=self.fight._shoot) as shoot:
            for _ in range(340):
                self.run.step({})
                if shoot.call_count:
                    break
        self.assertEqual(shoot.call_count, 1)
        self.assertIs(shoot.call_args.args[0], pet)
        self.assertEqual(vitals.total, 186)

    def test_ashe_pet_charge_expressions_read_live_pose_and_authored_capsule(self):
        from ow174.game import combat
        from ow174.game.mover import mover_data

        if (graph.entity_body(0x04000000000025CB) is None or graph.graph(0x153A) is None
                or mover_data(0x04000000000025C3).stand_height != 2.3):
            self.skipTest("prepared 104319 B.O.B. AI and mover supplements required")
        self.assertTrue(self.match.switch_hero(self.ally, find_hero("ashe")))
        for player, z in ((self.mercy, 0), (self.ally, 5), (self.enemy, 10)):
            player.mover.position[:] = (0, 1, z)
            player.mover.body = None
        script = self.ally.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        self.run.step({self.ally: (16384, 0, 0x20)})
        pet, = self.match.bots
        target = next(t for t in self.fight._targets() if t.owner is pet)
        self.assertEqual((target.height, target.radius), (2.3, 0.7))
        behavior, = (i for i in pet.body_script.component.instances.values() if i.graph.index == 0x1537)
        charge = behavior.node_state(behavior.graph.node(195))
        velocity_config = charge.node.field("m_BC5E91CF")[0]["m_9B7A63EA"]
        launch = behavior.graph.node(196).field("m_BC5E91CF")[0]["m_9B7A63EA"]
        origin_config = behavior.graph.node(204).field("m_143D4C5B")[0]["m_value"]
        origin = behavior.evaluate(origin_config)
        self.assertAlmostEqual(origin.y, pet.position[1] + 0.5, places=5)
        velocity = charge.evaluate(velocity_config, watch=False)
        self.assertAlmostEqual(velocity.x, 10, places=5)
        self.assertAlmostEqual(velocity.z, 0, places=5)
        self.assertEqual(behavior.evaluate(launch), expr.Vec3(0, 10, 0))
        self.step(300, beam=0)
        velocity = charge.evaluate(velocity_config, watch=False)
        self.assertNotEqual(round(pet.yaw), 16384)
        dx, _, dz = combat.aim(round(pet.yaw), 0)
        self.assertAlmostEqual(velocity.x, 10 * dx, places=5)
        self.assertAlmostEqual(velocity.z, 10 * dz, places=5)

    def test_ashe_pet_initial_charge_keeps_horizontal_speed_through_one_launch(self):
        from ow174.game.mover import mover_data

        if (graph.entity_body(0x04000000000025CB) is None or graph.graph(0x153A) is None
                or mover_data(0x04000000000025C3).stand_height != 2.3):
            self.skipTest("prepared 104319 B.O.B. AI and mover supplements required")
        self.assertTrue(self.match.switch_hero(self.ally, find_hero("ashe")))
        for player, position in ((self.mercy, (0, 1, 0)), (self.ally, (0, 1, 5)),
                                 (self.enemy, (0, 1, 14))):
            player.mover.position[:] = position
            player.mover.body = None
        script = self.ally.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        self.run.step({self.ally: (16384, 0, 0x20)})
        pet, = self.match.bots
        origin = pet.position
        trace = []
        for _ in range(8):
            self.run.step({})
            trace.append((pet.position, pet.velocity))
        takeoff = 4
        self.assertEqual(trace[0][1], (0, 0, 0))
        self.assertTrue(all(v[1] <= 0 for _, v in trace[:takeoff]), trace)
        self.assertGreater(trace[takeoff][1][1], 9)
        self.assertGreater(trace[takeoff][0][1], origin[1])
        self.assertLess(trace[takeoff + 1][1][1], trace[takeoff][1][1])
        self.assertTrue(all(v[0] == 10 and v[2] == 0 for _, v in trace[1:]), trace)
        self.assertGreater(trace[-1][0][0], origin[0] + 1)

    def test_lethal_rifle_stops_ashe_pet_fire_and_original_script_retires_corpse(self):
        if graph.entity_body(0x04000000000025CB) is None or graph.graph(0x153A) is None:
            self.skipTest("prepared 104319 B.O.B. AI supplement required")
        self.assertTrue(self.match.switch_hero(self.ally, find_hero("ashe")))
        for player, z in ((self.mercy, 0), (self.ally, 5), (self.enemy, 10)):
            player.mover.position[:] = (0, 1, z)
            player.mover.body = None
            for method in ("queue_bot_death", "queue_bot_death_update"):
                self.enterContext(patch.object(player.client, method, create=True))
        script = self.ally.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        self.run.step({self.ally: (0, 0, 0x20)})
        pet, = self.match.bots
        self.ally.mover.position[:] = (4, 1, 5)
        self.ally.mover.body = None
        component = pet.body_script.component
        queue = component.world.pet_play_queue
        with patch.object(self.fight, "_shoot", wraps=self.fight._shoot) as shoot:
            for _ in range(340):
                self.run.step({})
                if shoot.call_count:
                    break
            self.assertEqual(shoot.call_count, 1)
            self.assertIs(shoot.call_args.args[0], pet)
            vitals = self.fight.vitals[pet.entity]
            vitals.damage(vitals.total - 19)
            self.fight._sync_body_health(pet, vitals)
            x, y, z = pet.position
            direction = aim(self.fight._eye(self.enemy), (x, y + 0.8, z))
            self.run.step({self.enemy: (*direction, FIRE)})
            self.assertTrue(pet.dead)
            self.assertEqual(vitals.total, 0)
            self.assertTrue(queue.closed)
            for _ in range(200):
                self.run.step({})
            self.assertIn(pet, self.match.bots)
            for _ in range(700):
                if pet not in self.match.bots:
                    break
                self.run.step({})
        self.assertEqual([call.args[0] for call in shoot.call_args_list], [pet, self.enemy])
        self.assertNotIn(pet, self.match.bots)
        self.assertNotIn(pet.entity, self.fight.vitals)
        self.assertIsNone(getattr(script.world, "bob", None))
        self.assertTrue(component.world.pet_destroy_requested)
        self.assertIsNone(component.fault)

    def automatic_bob_shot_into_defense(self, defender, button, *, hold=False):
        if graph.entity_body(0x04000000000025CB) is None or graph.graph(0x153A) is None:
            self.skipTest("prepared 104319 B.O.B. AI supplement required")
        self.assertTrue(self.match.switch_hero(self.ally, find_hero("ashe")))
        self.assertTrue(self.match.switch_hero(self.enemy, defender))
        for player, z in ((self.mercy, 0), (self.ally, 5), (self.enemy, 17)):
            player.mover.position[:] = (0, 1, z)
            player.mover.body = None
        script = self.ally.body_script
        script.set_ult_charge(script.component.entity_var(338).value())
        self.run.step({self.ally: (0, 0, 0x20)})
        pet, = self.match.bots
        self.ally.mover.position[:] = (4, 1, 5)
        self.ally.mover.body = None
        self.mercy.body_script.component.set(12918, 0.0)
        def step(defense=0):
            x, y, z = pet.position
            point = (x, y + 0.8, z)
            beam = aim(self.fight._eye(self.mercy), point)
            facing = aim(self.fight._eye(self.enemy), point)
            self.run.step({self.mercy: (*beam, SECONDARY), self.enemy: (*facing, defense)})

        for _ in range(272):
            step()
        self.assertAlmostEqual(boost_amount(self.mercy.body_script, pet.entity), 0.3)
        step(button)
        health = self.enemy_health()
        with patch.object(self.fight, "_hitscan", wraps=self.fight._hitscan) as hitscan:
            for _ in range(45):
                step(button if hold else 0)
                if hitscan.call_count:
                    break
        self.assertGreaterEqual(hitscan.call_count, 1)
        outgoing = hitscan.call_args_list[0]
        self.assertIs(outgoing.args[0], pet)
        self.assertEqual(outgoing.kwargs["definition"], 0x0400000000002999)
        self.assertAlmostEqual(outgoing.args[1].amount, 18.2, places=4)
        self.assertEqual(self.enemy_health(), health)
        self.assertEqual(self.mercy.stats.value(AMPLIFIED), 0)
        self.assertEqual(self.mercy.body_script.component.entity_var(ULT_CHARGE).value(), 0)
        return pet, hitscan.call_args_list

    def test_genji_reflects_automatically_fired_ashe_pet_round_at_base_damage(self):
        damage_before = self.enemy.stats.value(stats.HERO_DAMAGE)
        pet, shots = self.automatic_bob_shot_into_defense(find_hero("genji"), 0x10)
        self.assertEqual(len(shots), 2)
        reflected = shots[1]
        self.assertIs(reflected.args[0], self.enemy)
        self.assertAlmostEqual(reflected.args[1].amount, 14, places=4)
        self.assertAlmostEqual(self.fight.vitals[pet.entity].total, 1186, places=4)
        self.assertAlmostEqual(self.enemy.stats.value(stats.HERO_DAMAGE) - damage_before, 14, places=4)

    def test_matrix_absorbs_automatically_fired_ashe_pet_round(self):
        mech = defense_matrix.MECH_BODY
        if graph.entity_body(mech) is None or not defense_matrix.absorbable(0x0400000000002999):
            self.skipTest("prepared 104319 mech and B.O.B. projectile tags required")
        pet, shots = self.automatic_bob_shot_into_defense(
            replace(find_hero("dva"), body=mech), SECONDARY, hold=True,
        )
        matrix = next(i for i in self.enemy.body_script.component.instances.values() if i.graph.index == 0x02CC)
        self.assertTrue(matrix.state(3).active)
        self.assertEqual(len(shots), 1)
        self.assertEqual(self.enemy_health(), 600)
        self.assertEqual(self.fight.vitals[pet.entity].total, 1200)

    def fire_boosted_ana_rifle_into_defense(self, hero, button, *, scoped, hold=False):
        self.assertTrue(self.match.switch_hero(self.ally, heroes()[ANA]))
        self.assertTrue(self.match.switch_hero(self.enemy, hero))
        self.mercy.mover.position[:] = (0, 1, 0)
        self.ally.mover.position[:] = (0, 1, 5)
        self.enemy.mover.position[:] = (0, 1, 10)
        for player in (self.mercy, self.ally, self.enemy):
            player.mover.body = None
        self.mercy.body_script.component.set(12918, 0.0)
        scope = SECONDARY if scoped else 0
        self.step(85, attack=scope)
        self.assertGreater(boost_amount(self.mercy.body_script, self.ally.body), 0)
        facing = aim(self.fight._eye(self.enemy), (0, 1.8, 5))
        shot_aim = aim(self.fight._eye(self.ally), (0, 1.8, 10))
        health = self.enemy_health()
        self.run.step({self.mercy: (0, 0, SECONDARY), self.ally: (*shot_aim, scope),
                       self.enemy: (*facing, button)})
        held = button if hold else 0
        self.run.step({self.mercy: (0, 0, SECONDARY), self.ally: (*shot_aim, scope),
                       self.enemy: (*facing, held)})
        defender_damage = self.enemy.stats.value(stats.HERO_DAMAGE)
        with patch.object(self.fight, "_shoot", wraps=self.fight._shoot) as shoot:
            self.run.step({self.mercy: (0, 0, SECONDARY), self.ally: (*shot_aim, scope | FIRE),
                           self.enemy: (*facing, held)})
        shot, = [args[2] for args, _ in shoot.call_args_list if args[0] is self.ally]
        self.assertEqual((shot.instance.graph.index, shot.node.pos), (0x0B94, 88 if scoped else 7))
        for _ in range(20):
            self.run.step({self.enemy: (*facing, held)})
        self.assertFalse(any(rocket.shooter is self.ally for rocket in self.fight.rockets))
        self.assertAlmostEqual(self.enemy_health(), health, places=4)
        self.assertEqual(self.ally.stats.value(stats.HERO_DAMAGE), 0)
        self.assertEqual(self.mercy.stats.value(AMPLIFIED), 0)
        self.assertEqual(self.mercy.body_script.component.entity_var(ULT_CHARGE).value(), 0)
        return self.enemy.stats.value(stats.HERO_DAMAGE) - defender_damage

    def assert_sigma_absorbs_ana_rifle(self, *, scoped):
        self.fire_boosted_ana_rifle_into_defense(heroes()[0x02E000000000023B], 0x08, scoped=scoped)
        defense = next(i for i in self.enemy.body_script.component.instances.values() if i.graph.index == 0x1D02)
        state = defense.node_state(defense.graph.node(7))
        self.assertTrue(state.active)
        self.assertAlmostEqual(defense.find_var(0, 21312).value(), 91, places=4)
        self.assertEqual(defense.find_var(0, 41).value(), expr.Entity(self.ally.body))
        for _ in range(150):
            if not state.active:
                break
            self.run.step({})
        self.assertFalse(state.active)
        target = next(t for t in self.fight._targets() if t.owner is self.enemy)
        self.assertAlmostEqual(self.fight.vitals_of(target).temporary_shields[SHIELD_TAG].now, 54.6, places=4)

    def test_sigma_absorbs_actual_boosted_ana_scoped_rifle(self):
        self.assert_sigma_absorbs_ana_rifle(scoped=True)

    def test_sigma_absorbs_actual_boosted_ana_unscoped_rifle(self):
        self.assert_sigma_absorbs_ana_rifle(scoped=False)

    def assert_genji_reflects_ana_rifle(self, *, scoped):
        reflected = self.fire_boosted_ana_rifle_into_defense(heroes()[0x02E0000000000029], 0x10, scoped=scoped)
        target = next(t for t in self.fight._targets() if t.owner is self.ally)
        self.assertAlmostEqual(self.fight.vitals_of(target).total, 130, places=4)
        self.assertAlmostEqual(reflected, 70, places=4)

    def test_genji_reflects_actual_boosted_ana_scoped_rifle(self):
        self.assert_genji_reflects_ana_rifle(scoped=True)

    def test_genji_reflects_actual_boosted_ana_unscoped_rifle(self):
        self.assert_genji_reflects_ana_rifle(scoped=False)

    def assert_matrix_absorbs_ana_rifle(self, *, scoped):
        mech = defense_matrix.MECH_BODY
        definition = 0x0400000000001615 if scoped else 0x04000000000015B7
        if graph.entity_body(mech) is None or not defense_matrix.absorbable(definition):
            self.skipTest("prepared 104319 mech and Ana rifle projectile tags required")
        self.fire_boosted_ana_rifle_into_defense(replace(find_hero("dva"), body=mech),
                                               SECONDARY, scoped=scoped, hold=True)
        matrix = next(i for i in self.enemy.body_script.component.instances.values() if i.graph.index == 0x02CC)
        self.assertTrue(matrix.state(3).active)
        self.assertEqual(self.enemy_health(), 600)

    def test_matrix_absorbs_actual_boosted_ana_scoped_rifle(self):
        self.assert_matrix_absorbs_ana_rifle(scoped=True)

    def test_matrix_absorbs_actual_boosted_ana_unscoped_rifle(self):
        self.assert_matrix_absorbs_ana_rifle(scoped=False)

    def test_sigma_absorbs_authored_player_hitscan_volley_modes(self):
        self.assert_player_hitscan_volley_modes("sigma")

    def test_genji_reflects_authored_player_hitscan_volley_modes(self):
        self.assert_player_hitscan_volley_modes("genji")

    def test_matrix_absorbs_authored_player_hitscan_volley_modes(self):
        self.assert_player_hitscan_volley_modes("dva")

    def assert_player_hitscan_volley_modes(self, defender):
        # Expected shot counts and resource IDs come from the actual volley
        # emitted by each input, including burst and held-scope branches.
        cases = (
            ("Reaper", 0, FIRE, 0, 65, 0x004A, 1),
            ("Tracer", 0, FIRE, 0, 65, 0x0018, 1),
            ("Widowmaker", 0, FIRE, 0, 65, 0x004C, 1),
            ("Widowmaker", SECONDARY, FIRE, 0, 20, 0x0107, 1),
            ("Widowmaker", SECONDARY, FIRE, 0, 65, 0x004B, 1),
            ("Widowmaker", SECONDARY, FIRE, 0, 140, 0x0108, 1),
            ("Bastion", 0, FIRE, 0, 65, 0x0205, 1),
            ("Bastion", 0, FIRE, 0x08, 140, 0x0207, 1),
            ("Cassidy", 0, FIRE, 0, 65, 0x02A7, 1),
            ("Cassidy", 0, SECONDARY, 0, 65, 0x02A7, 2),
            ("Sombra", 0, FIRE, 0, 65, 0x1222, 1),
            ("Wrecking Ball", 0, FIRE, 0, 65, 0x2584, 1),
            ("Ashe", 0, FIRE, 0, 65, 0x280E, 1),
            ("Ashe", SECONDARY, FIRE, 0, 65, 0x29A1, 1),
            ("Ashe", 0, 0x08, 0, 65, 0x2B61, 1),
            ("Baptiste", 0, FIRE, 0, 65, 0x2CFA, 3),
        )
        for hero, scope, trigger, transform, windup, resource, count in cases:
            with self.subTest(hero=hero, resource=f"{resource:04X}", trigger=trigger):
                fixture = MercyBoostTests()
                fixture.setUp()
                try:
                    fixture.assert_player_volley_defended(
                        hero, scope, trigger, transform, windup, resource, count, defender=defender,
                    )
                finally:
                    fixture.doCleanups()

    def test_sigma_absorbs_actual_boosted_whole_hog(self):
        self.assert_player_volley_defended("Roadhog", 0, FIRE, 0, 80, 0x0405, 2,
                                           defender="sigma", ultimate=True)

    def test_genji_reflects_actual_boosted_whole_hog(self):
        self.assert_player_volley_defended("Roadhog", 0, FIRE, 0, 80, 0x0405, 2,
                                           defender="genji", ultimate=True)

    def test_matrix_absorbs_actual_boosted_whole_hog(self):
        self.assert_player_volley_defended("Roadhog", 0, FIRE, 0, 80, 0x0405, 2,
                                           defender="dva", ultimate=True)

    def assert_player_volley_defended(self, hero, scope, trigger, transform, windup, resource, count,
                                      *, defender, ultimate=False):
        definition = 0x0400000000000000 | resource
        defense_hero = find_hero(defender)
        if defender == "dva":
            mech = defense_matrix.MECH_BODY
            if graph.entity_body(mech) is None or not defense_matrix.absorbable(definition):
                self.skipTest("prepared 104319 mech and weapon projectile tags required")
            defense_hero = replace(defense_hero, body=mech)
        self.assertTrue(self.match.switch_hero(self.ally, find_hero(hero)))
        self.assertTrue(self.match.switch_hero(self.enemy, defense_hero))
        self.mercy.mover.position[:] = (0, 1, 0)
        self.ally.mover.position[:] = (0, 1, 5)
        self.enemy.mover.position[:] = (0, 1, 10)
        for player in (self.mercy, self.ally, self.enemy):
            player.mover.body = None
        self.mercy.body_script.component.set(12918, 0.0)
        if transform:
            self.step(1, attack=transform)
        if ultimate:
            script = self.ally.body_script
            script.set_ult_charge(script.component.entity_var(338).value())
            self.step(1, attack=32)
        # Finish defender startup before a short scoped windup.
        if windup < 65:
            self.step(65 - windup)
        self.step(windup, attack=scope)
        if ultimate:
            self.assertTrue(script.component.entity_var(842).value())
        self.assertAlmostEqual(boost_amount(self.mercy.body_script, self.ally.body), 0.3)
        facing = aim(self.fight._eye(self.enemy), (0, 1.8, 5))
        shot_aim = aim(self.fight._eye(self.ally), (0, 1.8, 10))

        def step(attack=0, defense=0):
            self.run.step({self.mercy: (0, 0, SECONDARY), self.ally: (*shot_aim, scope | attack),
                           self.enemy: (*facing, defense)})

        button = {"sigma": 0x08, "genji": 0x10, "dva": SECONDARY}[defender]
        held = button if defender == "dva" else 0
        step(defense=button)
        step(defense=held)
        health = self.enemy_health()
        ally = next(target for target in self.fight._targets() if target.owner is self.ally)
        ally_vitals = self.fight.vitals_of(ally)
        ally_health = ally_vitals.total
        ally_damage = self.ally.stats.value(stats.HERO_DAMAGE)
        defender_damage = self.enemy.stats.value(stats.HERO_DAMAGE)
        amplified = self.mercy.stats.value(AMPLIFIED)
        charge = self.mercy.body_script.component.entity_var(ULT_CHARGE)
        charge_before = charge.value()

        matrix_contacts = []
        matrix_distance = self.fight._matrix_distance

        def matrix_contact(*args):
            distance = matrix_distance(*args)
            if distance is not None:
                matrix_contacts.append(distance)
            return distance

        with (patch.object(self.fight, "_hitscan", wraps=self.fight._hitscan) as hitscan,
              patch.object(self.fight, "_matrix_distance", side_effect=matrix_contact)):
            step(attack=trigger, defense=held)
            for _ in range(15):
                step(defense=held)
        outgoing = []
        reflected = []
        for args, kwargs in hitscan.call_args_list:
            self.assertEqual(kwargs["definition"], definition)
            amount = args[1].amount
            self.assertGreater(amount, 0)
            if args[0] is self.ally:
                outgoing.append(amount)
            else:
                self.assertIs(args[0], self.enemy)
                reflected.append(amount)
        self.assertEqual(len(outgoing), count)
        if defender == "genji":
            self.assertEqual(len(reflected), count)
            for boosted, returned in zip(outgoing, reflected, strict=True):
                self.assertAlmostEqual(returned * 1.3, boosted, places=4)
            self.assertGreater(ally_health - ally_vitals.total, 0)
            self.assertAlmostEqual(self.enemy.stats.value(stats.HERO_DAMAGE) - defender_damage,
                                   ally_health - ally_vitals.total, places=4)
        else:
            self.assertFalse(reflected)
            self.assertEqual(ally_vitals.total, ally_health)
            self.assertEqual(self.enemy.stats.value(stats.HERO_DAMAGE), defender_damage)
        if defender == "sigma":
            defense = next(i for i in self.enemy.body_script.component.instances.values() if i.graph.index == 0x1D02)
            self.assertTrue(defense.node_state(defense.graph.node(7)).active)
            self.assertAlmostEqual(defense.find_var(0, 21312).value(), sum(outgoing), places=4)
            self.assertEqual(defense.find_var(0, 41).value(), expr.Entity(self.ally.body))
        if defender == "dva":
            matrix = next(i for i in self.enemy.body_script.component.instances.values() if i.graph.index == 0x02CC)
            self.assertTrue(matrix.state(3).active)
            self.assertEqual(len(matrix_contacts), count)
        self.assertEqual(self.enemy_health(), health)
        self.assertEqual(self.ally.stats.value(stats.HERO_DAMAGE), ally_damage)
        self.assertEqual(self.mercy.stats.value(AMPLIFIED), amplified)
        self.assertEqual(charge.value(), charge_before)

    def fire_boosted_rifle_into_defense(self, hero, defense_button, *, hold=False, visor=False):
        self.assertTrue(self.match.switch_hero(self.enemy, hero))
        self.mercy.mover.position[:] = (0, 1, 0)
        self.ally.mover.position[:] = (0, 1, 5)
        self.enemy.mover.position[:] = (0, 1, 10)
        for player in (self.mercy, self.ally, self.enemy):
            player.mover.body = None
        self.mercy.body_script.component.set(12918, 0.0)
        if visor:
            script = self.ally.body_script
            script.set_ult_charge(script.component.entity_var(338).value())
            self.run.step({self.ally: (0, 0, 32)})
            for _ in range(80):
                self.run.step({})
            self.assertTrue(script.component.entity_var(842).value())
        self.step(10)
        facing = aim(self.fight._eye(self.enemy), (0, 1.8, 5))
        held = defense_button if hold else 0
        self.run.step({self.mercy: (0, 0, SECONDARY), self.enemy: (*facing, defense_button)})
        self.run.step({self.mercy: (0, 0, SECONDARY), self.enemy: (*facing, held)})
        before = self.enemy.stats.value(stats.HERO_DAMAGE)
        self.run.step({self.mercy: (0, 0, SECONDARY), self.ally: (0, 0, FIRE),
                       self.enemy: (*facing, held)})
        self.assertEqual(self.ally.stats.value(stats.HERO_DAMAGE), 0)
        self.assertEqual(self.mercy.stats.value(AMPLIFIED), 0)
        self.assertEqual(self.mercy.body_script.component.entity_var(ULT_CHARGE).value(), 0)
        return before

    def test_sigma_absorbs_actual_boosted_soldier_rifle(self):
        self.fire_boosted_rifle_into_defense(heroes()[0x02E000000000023B], 0x08)
        defense = next(i for i in self.enemy.body_script.component.instances.values() if i.graph.index == 0x1D02)
        state = defense.node_state(defense.graph.node(7))
        self.assertTrue(state.active)
        self.assertAlmostEqual(defense.find_var(0, 21312).value(), 26, places=4)
        self.assertEqual(defense.find_var(0, 41).value(), expr.Entity(self.ally.body))
        health = self.enemy_health()
        for _ in range(150):
            if not state.active:
                break
            self.run.step({})
        self.assertFalse(state.active)
        target = next(t for t in self.fight._targets() if t.owner is self.enemy)
        vitals = self.fight.vitals_of(target)
        self.assertAlmostEqual(vitals.temporary_shields[SHIELD_TAG].now, 15.6, places=4)
        self.assertAlmostEqual(vitals.total, health + 15.6, places=4)

    def test_genji_returns_actual_boosted_soldier_rifle_with_base_damage(self):
        before = self.fire_boosted_rifle_into_defense(heroes()[0x02E0000000000029], 0x10)
        self.assertEqual(self.enemy_health(), 200)
        self.assertEqual(self.fixture.vitals.total, 80)
        self.assertEqual(self.enemy.stats.value(stats.HERO_DAMAGE) - before, 20)

    def test_dva_matrix_absorbs_actual_boosted_tactical_visor_rifle(self):
        mech = defense_matrix.MECH_BODY
        if graph.entity_body(mech) is None or not defense_matrix.absorbable(0x0400000000001C85):
            self.skipTest("prepared 104319 mech and Visor projectile tags required")
        with patch.object(self.fight, "_hitscan", wraps=self.fight._hitscan) as hitscan:
            self.fire_boosted_rifle_into_defense(replace(find_hero("dva"), body=mech),
                                                SECONDARY, hold=True, visor=True)
        self.assertEqual(hitscan.call_count, 1)
        self.assertEqual(hitscan.call_args.kwargs["definition"], 0x0400000000001C85)
        self.assertAlmostEqual(hitscan.call_args.args[1].amount, 26, places=4)
        matrix = next(i for i in self.enemy.body_script.component.instances.values() if i.graph.index == 0x02CC)
        self.assertTrue(matrix.state(3).active)
        self.assertEqual(self.enemy_health(), 600)

    def test_release_removes_bonus_before_the_next_attack(self):
        self.step(10)
        self.step(1, beam=0)
        self.step(1, beam=0, attack=FIRE)
        self.step(10, beam=0)
        self.assertAlmostEqual(self.enemy_health(), 180, places=4)

    def test_fortify_and_armour_reduce_actual_rifle_damage_and_mercy_boost_credit(self):
        self.match.switch_hero(self.enemy, heroes()[phase_defenses.ORISA])
        component = self.mercy.body_script.component
        component.set(12918, 0.0)
        charge = component.entity_var(ULT_CHARGE)
        self.step(35)
        before = self.enemy_health()
        charge_before = charge.value()
        self.run.step({self.enemy: (0, 0, 0x08)})
        self.assertAlmostEqual(phase_defenses.fortify_damage_scale(self.enemy, self.enemy.body), 0.6)
        self.step(10)
        self.step(1, attack=FIRE)
        self.step(5, beam=0)
        self.assertAlmostEqual(before - self.enemy_health(), 10.6, places=4)
        self.assertAlmostEqual(self.ally.stats.value(stats.HERO_DAMAGE), 10.6, places=4)
        self.assertAlmostEqual(charge.value() - charge_before, 3.6, places=4)

    def test_projectile_keeps_launch_bonus_after_beam_release(self):
        self.step(10)
        self.step(1, attack=SECONDARY)  # Soldier Helix:40 direct plus80 blast
        self.assertTrue(self.fight.rockets)
        rocket = next(r for r in self.fight.rockets if r.shooter is self.ally)
        self.assertAlmostEqual(rocket.direct.amount, 52, places=4)
        self.assertAlmostEqual(rocket.blast.amount, 104, places=4)
        self.step(20, beam=0)
        self.assertAlmostEqual(self.enemy_health(), 44, places=3)

    def test_projectile_does_not_gain_bonus_when_beam_attaches_after_launch(self):
        self.step(10, beam=0)
        self.step(1, beam=0, attack=SECONDARY)
        rocket = next(r for r in self.fight.rockets if r.shooter is self.ally)
        self.assertAlmostEqual(rocket.direct.amount, 40, places=4)
        self.step(20)
        self.assertAlmostEqual(self.enemy_health(), 80, places=3)

    def cryo_during_icicle_flight(self, *, launch_boosted):
        self.assertTrue(self.match.switch_hero(self.ally, heroes()[cryo.HERO]))
        self.enemy.mover.position[:] = (0, 1, 35)
        self.enemy.mover.body = None
        component = self.mercy.body_script.component
        component.set(12918, 0.0)
        charge = component.entity_var(ULT_CHARGE)
        before = charge.value()
        beam = SECONDARY if launch_boosted else 0
        self.step(45, beam=beam)
        self.assertAlmostEqual(self.fight.damage_scale(self.ally), 1.3 if launch_boosted else 1)
        for frame in range(45):
            self.step(1, beam=beam, attack=SECONDARY if frame < 2 else 0)
            if self.fight.rockets:
                break
        projectile, = self.fight.rockets
        self.assertIs(projectile.shooter, self.ally)
        expected = 97.5 if launch_boosted else 75
        self.assertAlmostEqual(projectile.direct.amount, expected, places=4)
        self.assertEqual(self.enemy_health(), 200)

        beam = 0 if launch_boosted else SECONDARY
        beam_aim = aim(self.fight._eye(self.mercy), (0, 2, 5))
        shot_aim = aim(self.fight._eye(self.ally), (0, 1.8, 35))
        self.run.step({self.mercy: (*beam_aim, beam), self.ally: (*shot_aim, 0x08)})
        self.assertTrue(cryo.protected(self.ally))
        self.step(5, beam=beam)
        self.assertIn(projectile, self.fight.rockets)
        self.assertAlmostEqual(self.fight.damage_scale(self.ally), 1 if launch_boosted else 1.3)
        self.assertAlmostEqual(projectile.direct.amount, expected, places=4)
        self.assertEqual(charge.value(), before)
        self.step(35, beam=beam)

        self.assertTrue(cryo.protected(self.ally))
        self.assertAlmostEqual(self.enemy_health(), 200 - expected, places=4)
        self.assertAlmostEqual(self.ally.stats.value(stats.HERO_DAMAGE), expected, places=4)
        self.assertEqual(self.mercy.stats.value(stats.HERO_DAMAGE), 0)
        self.assertAlmostEqual(charge.value() - before, 22.5 if launch_boosted else 0, places=4)
        self.assertAlmostEqual(self.mercy.stats.value(AMPLIFIED), 22.5 if launch_boosted else 0,
                               places=4)
        self.assertFalse(self.fight.rockets)

    def test_icicle_keeps_launch_boost_when_mei_enters_cryo_and_mercy_releases(self):
        self.cryo_during_icicle_flight(launch_boosted=True)

    def test_icicle_does_not_gain_boost_when_mercy_attaches_to_mei_in_cryo(self):
        self.cryo_during_icicle_flight(launch_boosted=False)

    def test_missing_effect_pack_does_not_invent_damage_bonus(self):
        self.step(10)
        with patch.object(graph, "effect_script", return_value=None):
            self.assertEqual(self.fight.damage_scale(self.ally), 1)
            self.step(1, attack=FIRE)
        self.assertAlmostEqual(self.enemy_health(), 180, places=4)

    def test_blocked_beam_does_not_amplify_a_real_attack(self):
        from test_game_mover import FLOOR, wall

        from ow174.game.collision import World

        self.step(10)
        self.match.collision_world = World(FLOOR + wall(-2, 2.5, 2, 2.5))
        self.step(1, attack=FIRE)
        self.assertAlmostEqual(self.enemy_health(), 180, places=4)

    def test_dead_caster_does_not_amplify_a_real_attack(self):
        self.step(10)
        caster = next(t for t in self.fight._targets() if t.owner is self.mercy)
        self.fight._damage(self.enemy, caster, 500, False, None, self.run.frame)
        self.step(1, attack=FIRE)
        self.assertAlmostEqual(self.enemy_health(), 180, places=4)

    def test_two_mercy_sources_do_not_compound_the_same_attack(self):
        other = self.match.add_player(4, "Second Mercy", MERCY, 0, False)
        in_world(self.match, other)
        self.match.switch_hero(other, heroes()[MERCY])
        self.run.players.append(other)
        charge = [p.body_script.component.entity_var(ULT_CHARGE) for p in (self.mercy, other)]
        before = [slot.value() for slot in charge]
        for frame in range(11):
            commands = {}
            for healer in (self.mercy, other):
                x, y, z = self.ally.mover.position
                yaw, pitch = aim(self.fight._eye(healer), (x, y + 1, z))
                commands[healer] = (yaw, pitch, SECONDARY)
            x, y, z = self.enemy.mover.position
            yaw, pitch = aim(self.fight._eye(self.ally), (x, y + 0.8, z))
            commands[self.ally] = (yaw, pitch, FIRE if frame == 10 else 0)
            self.run.step(commands)
        self.assertAlmostEqual(self.enemy_health(), 174, places=4)
        for healer in (self.mercy, other):
            self.assertAlmostEqual(boost_amount(healer.body_script, self.ally.body), 0.3)
        self.step(5, beam=0)
        self.assertAlmostEqual(charge[0].value() - before[0], 6, places=4)
        self.assertEqual(charge[1].value(), before[1])

    def test_team_change_removes_bonus_before_the_next_attack(self):
        self.step(10)
        self.mercy.team = 2
        self.step(1, attack=FIRE)
        self.assertAlmostEqual(self.enemy_health(), 180, places=4)
