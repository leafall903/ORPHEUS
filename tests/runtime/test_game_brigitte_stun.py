"""Real Bash input -> hosted224E control, expiry and target lifecycle."""

import unittest
from unittest.mock import patch

import test_game_brigitte as brigitte_tests
import test_game_brigitte_shield as shield_tests
import test_mccree_flashbang as flash_tests

from ow174.game.brigitte import HERO
from ow174.game.commands import FIRE, JUMP, SECONDARY, Command
from ow174.game.content import find_hero, heroes
from ow174.game.script import expr


class BashStunTests(unittest.TestCase):
    def fixture(self, *, immune=False):
        run, source, target, _ = shield_tests.BarrierTests().fixture()
        x, y, z = source.mover.position
        target.mover.position, target.mover.body = [x, y, z + 3], None
        if immune:
            target.body_script.component.set(2547, True)  #224E's authored immunity input
        shield_tests.BarrierTests().bash(run, source, frames=2)
        return run, source, target

    def test_actual_recipient_control_expires_from_native_chase_without_parallel_timer(self):
        run, source, target = self.fixture()
        bridge = run.match.combat.brigitte
        self.assertEqual(len(bridge.stuns), 1)
        lease = bridge.stuns[0]
        self.assertIs(lease.target, target)
        self.assertEqual(lease.instance.vars[215].value(), 0.75)
        self.assertEqual(lease.instance.vars[27].value(), expr.Entity(source.body))
        self.assertTrue(target.body_script.component.entity_var(888).value())
        self.assertFalse(target.body_script.component.entity_var(3772).value())
        for _ in range(44):
            run.step({})
        self.assertTrue(target.body_script.component.entity_var(888).value())
        for _ in range(5):
            run.step({})
        self.assertFalse(target.body_script.component.entity_var(888).value())
        self.assertFalse(bridge.stuns)
        self.assertNotIn(lease.instance.id, target.body_script.component.instances)
        self.assertIsNotNone(target.body_script)

    def test_held_primary_cannot_damage_until_stun_expires_then_resumes(self):
        run, source, target = self.fixture()
        x, y, z = target.mover.position
        # Hold the source in front after its dash; this fixture invalidates the
        # cached capsule and removes post-dash coasting, without altering stun.
        source.mover.position, source.mover.body = [x, y, z - 3], None
        source.mover.velocity = (0.0, 0.0, 0.0)
        barrier = run.match.combat.brigitte.barriers[source.body]
        before = barrier.health
        for _ in range(40):
            run.step({source: (0, 0, SECONDARY), target: (-32768, 0, FIRE)})
        self.assertEqual(barrier.health, before)
        for _ in range(15):
            run.step({source: (0, 0, SECONDARY), target: (-32768, 0, FIRE)})
        self.assertLess(barrier.health, before)

    def test_authored_movement_and_jump_modifiers_block_wish_then_release(self):
        run, _source, target = self.fixture()
        idle_run, _, idle_target = self.fixture()
        for _ in range(35):
            run.frame += 1
            run.match.update(run.frame * 0.016, run.frame)
            target.take_commands([Command(run.frame, forward=127, right=127, buttons=JUMP)])
            idle_run.frame += 1
            idle_run.match.update(idle_run.frame * 0.016, idle_run.frame)
            idle_target.take_commands([Command(idle_run.frame)])
            self.assertEqual(tuple(target.mover.position), tuple(idle_target.mover.position))
        start = tuple(target.mover.position)
        for _ in range(25):
            run.frame += 1
            run.match.update(run.frame * 0.016, run.frame)
            target.take_commands([Command(run.frame, forward=127)])
        self.assertGreater(target.mover.position[2], start[2] + 0.5)

    def test_authored_immunity_input_rejects_control_while_damage_still_applies(self):
        run, _source, target = self.fixture(immune=True)
        self.assertEqual(shield_tests.BarrierTests().health(run, target), 195)
        component = target.body_script.component
        status = component.entity_var(888, create=False)
        self.assertFalse(status.value() if status is not None else False)
        run.step({})
        self.assertFalse(run.match.combat.brigitte.stuns)

    def test_released_rocket_punch_hits_brigitte_through_held_shield(self):
        for shield, offset in ((False, 0), (True, 0), (True, 4)):
            with self.subTest(shield=shield, offset=offset):
                case = brigitte_tests.BrigitteTests()
                run, brig = case.fixture()
                doom = run.players[1]
                self.assertTrue(run.match.switch_hero(doom, find_hero("Doomfist")))
                x, y, z = brig.mover.position
                doom.mover.position, doom.mover.body = [x + offset, y, z + 8], None
                hold = SECONDARY if shield else 0
                fight = run.match.combat
                hp = fight.vitals_of(case.target(run, brig))
                before = hp.total
                for _ in range(80):
                    run.step({brig: (0, 0, hold), doom: (-32768, 0, SECONDARY)})
                self.assertEqual(hp.total, before)
                self.assertEqual(doom.mover.position[2], z + 8)
                stunned = False
                for _ in range(65):
                    run.step({brig: (0, 0, hold), doom: (-32768, 0, 0)})
                    stunned |= fight.is_stunned(brig)
                self.assertFalse(fight.is_stunned(brig))
                self.assertEqual(stunned, not offset)
                self.assertAlmostEqual(before - hp.total, 0 if offset else 90.04, delta=0.01)
                self.assertEqual(fight.brigitte.barriers[brig.body].health, 250)
                self.assertLess(doom.mover.position[2], z + 2)
                shield = fight.vitals_of(case.target(run, doom)).temporary_shield
                self.assertEqual(shield.amount if shield is not None else 0, 0 if offset else 30)

    def test_rocket_punch_wall_bonus_requires_wall_contact_and_applies_once(self):
        from test_game_mover import box

        from ow174.game.collision import StaticCollisionProvider, World
        from ow174.game.combat import HIT
        from ow174.game.world import HEALTH_POOL

        for wall in ("none", "behind", "alongside"):
            with self.subTest(wall=wall):
                case = brigitte_tests.BrigitteTests()
                run, brig = case.fixture()
                doom = run.players[1]
                self.assertTrue(run.match.switch_hero(doom, find_hero("Doomfist")))
                x, y, z = brig.mover.position
                doom.mover.position, doom.mover.body = [x, y, z + 8], None
                if wall != "none":
                    floor = box(x - 20, x + 20, y - 2, y, z - 20, z + 20)
                    obstacle = (box(x - 8, x + 8, y, y + 6, z - 2, z - 0.8) if wall == "behind"
                                else box(x + 0.65, x + 2, y, y + 6, z - 8, z + 8))
                    collision = World(floor + obstacle)
                    run.match.collision_provider = StaticCollisionProvider(collision)
                    run.match.collision_world = collision
                    for player in (brig, doom):
                        player.mover.use(world=collision)
                fight = run.match.combat
                hp = fight.vitals_of(case.target(run, brig))
                hp.now[HEALTH_POOL] = hp.most[HEALTH_POOL] = 450
                before = hp.total
                for _ in range(90):
                    run.step({doom: (-32768, 0, SECONDARY)})
                doom_hp = fight.vitals_of(case.target(run, doom))
                peak = 0
                for _ in range(70):
                    run.step({doom: (-32768, 0, 0)})
                    shield = doom_hp.temporary_shield
                    if shield is not None:
                        peak = max(peak, shield.amount)
                wall_hit = wall == "behind"
                self.assertAlmostEqual(before - hp.total, 245 if wall_hit else 95, delta=0.1)
                self.assertEqual(len(doom.client.messages(HIT)), 2 if wall_hit else 1)
                self.assertNotIn(brig.body, fight.punch_impacts)
                self.assertEqual(peak, 30)
                self.assertIsNotNone(shield)
                self.assertEqual(shield.rate, 3)
                decay_from = shield.decay_from
                amount = shield.amount
                for _ in range(130):
                    run.step({})
                self.assertAlmostEqual(shield.amount, amount - (fight.now - decay_from) * 3)
                for _ in range(620):
                    run.step({})
                self.assertEqual(shield.amount, 0)
                self.assertEqual(len(doom.client.messages(HIT)), 2 if wall_hit else 1)

    def test_reinhardt_charge_handoff_advances_and_expires_the_authored_drive(self):
        case = brigitte_tests.BrigitteTests()
        run, brig = case.fixture()
        rein = run.players[1]
        self.assertTrue(run.match.switch_hero(rein, find_hero("Reinhardt")))
        x, y, z = brig.mover.position
        rein.mover.position, rein.mover.body = [x + 4, y, z + 8], None
        for _ in range(25):
            run.step({})
        instance = next(i for i in rein.body_script.component.instances.values() if i.graph.index == 0x007D)
        run.step({rein: (-32768, 0, 8)})
        self.assertTrue(instance.states[37].active)
        self.assertEqual(rein.mover.position[2], z + 8)
        for _ in range(38):
            run.step({rein: (-32768, 0, 0)})
        self.assertFalse(instance.states[37].active)
        self.assertTrue(instance.states[3].active)
        for _ in range(190):
            run.step({rein: (-32768, 0, 0)})
        self.assertFalse(instance.states[3].active)
        self.assertAlmostEqual(z + 8 - rein.mover.position[2], 49.7, delta=0.2)
        self.assertGreater(instance.states[46].remaining(), 0)
        self.assertFalse(run.match.combat.is_stunned(brig))

    def test_reinhardt_charge_carry_resolves_on_wall_expiry_or_source_death(self):
        from test_game_combat import aim
        from test_game_mover import box

        from ow174.game.collision import StaticCollisionProvider, World
        from ow174.game.combat import HIT
        from ow174.game.world import HEALTH_POOL

        for ending in ("expiry", "wall", "source_death"):
            with self.subTest(ending=ending):
                case = brigitte_tests.BrigitteTests()
                run, brig = case.fixture(("Brigitte", "Reinhardt", "Soldier"))
                rein, soldier = run.players[1:]
                wall = ending == "wall"
                self.assertTrue(run.match.switch_hero(rein, find_hero("Reinhardt")))
                x, y, z = brig.mover.position
                rein.mover.position, rein.mover.body = [x, y, z + 8], None
                soldier.mover.position, soldier.mover.body = [x + 8, y, z], None
                if wall:
                    collision = World(box(x - 20, x + 20, y - 2, y, z - 20, z + 20)
                                      + box(x - 8, x + 8, y, y + 6, z - 5, z - 4))
                    run.match.collision_provider = StaticCollisionProvider(collision)
                    run.match.collision_world = collision
                    for player in (brig, rein):
                        player.mover.use(world=collision)
                fight = run.match.combat
                hp = fight.vitals_of(case.target(run, brig))
                hp.now[HEALTH_POOL] = hp.most[HEALTH_POOL] = 450
                before = hp.total
                for _ in range(25):
                    run.step({brig: (0, 0, SECONDARY)})
                for frame in range(100):
                    run.step({brig: (0, 0, SECONDARY), rein: (-32768, 0, 8 if frame == 0 else 0)})
                    if fight.is_stunned(brig):
                        break
                self.assertTrue(fight.is_stunned(brig))
                self.assertEqual(fight.charge_contacts[rein.body].captured, brig.body)
                self.assertEqual(hp.total, before)
                captured_z = brig.mover.position[2]
                for _ in range(4):
                    run.step({rein: (-32768, 0, 0)})
                self.assertTrue(fight.is_stunned(brig))
                self.assertLess(brig.mover.position[2], captured_z - 0.5)
                with patch.object(brig.mover, "add_external_impulse",
                                  wraps=brig.mover.add_external_impulse) as release:
                    if ending == "source_death":
                        rein_hp = fight.vitals_of(case.target(run, rein))
                        rein_hp.damage(rein_hp.total - 1)
                        facing = aim(fight._eye(soldier), fight._eye(rein))
                        run.step({soldier: (*facing, FIRE), rein: (-32768, 0, 0)})
                        self.assertTrue(rein_hp.dead)
                        self.assertEqual(rein_hp.killer, soldier.body)
                        self.assertFalse(fight.is_stunned(brig))
                    for _ in range(190):
                        run.step({rein: (-32768, 0, 0)})
                    self.assertEqual(release.call_count, 1 if ending == "expiry" else 0)
                    if ending == "expiry":
                        impulse, = release.call_args.args
                        self.assertLess(impulse[2], 0)
                self.assertFalse(fight.is_stunned(brig))
                self.assertNotIn(rein.body, fight.charge_contacts)
                self.assertEqual(before - hp.total, 295 if wall else 0)
                self.assertEqual(len(rein.client.messages(HIT)), 1 if wall else 0)
                self.assertEqual(fight.brigitte.barriers[brig.body].health, 250)

    def test_reinhardt_charge_glances_but_respects_team_wall_and_lateral_miss(self):
        from dataclasses import replace

        from test_game_mover import box

        from ow174.game.collision import StaticCollisionProvider, World
        from ow174.game.combat import HIT

        for boundary, offset in (("glance", 1.6), ("friendly", 0), ("wall", 0), ("miss", 4)):
            with self.subTest(boundary=boundary):
                case = brigitte_tests.BrigitteTests()
                run, brig = case.fixture()
                rein = run.players[1]
                self.assertTrue(run.match.switch_hero(rein, find_hero("Reinhardt")))
                x, y, z = brig.mover.position
                rein.mover.position, rein.mover.body = [x + offset, y, z + 8], None
                if boundary == "friendly":
                    run.match.game_map = replace(run.match.game_map, free_for_all=False)
                    rein.team = brig.team
                if boundary == "wall":
                    collision = World(box(x - 20, x + 20, y - 2, y, z - 20, z + 20)
                                      + box(x - 8, x + 8, y, y + 6, z + 4, z + 4.2))
                    run.match.collision_provider = StaticCollisionProvider(collision)
                    run.match.collision_world = collision
                    for player in (brig, rein):
                        player.mover.use(world=collision)
                fight = run.match.combat
                hp = fight.vitals_of(case.target(run, brig))
                before = hp.total
                for _ in range(25):
                    run.step({brig: (0, 0, SECONDARY)})
                with patch.object(brig.mover, "add_external_impulse",
                                  wraps=brig.mover.add_external_impulse) as impulse:
                    for frame in range(120):
                        run.step({brig: (0, 0, SECONDARY), rein: (-32768, 0, 8 if frame == 0 else 0)})
                        self.assertFalse(fight.is_stunned(brig))
                    glancing = boundary == "glance"
                    self.assertEqual(impulse.call_count, int(glancing))
                    if glancing:
                        vector, = impulse.call_args.args
                        self.assertLess(vector[0], 0)
                        self.assertGreater(vector[1], 0)
                        self.assertLess(vector[2], 0)
                self.assertEqual(before - hp.total, 45 if glancing else 0)
                self.assertEqual(len(rein.client.messages(HIT)), int(glancing))
                self.assertEqual(fight.brigitte.barriers[brig.body].health, 250)
                if boundary == "wall":
                    self.assertGreater(rein.mover.position[2], z + 4)
                    self.assertNotIn(rein.body, fight.charge_contacts)

    def test_actual_bash_counter_charge_applies_both_two_second_profiles(self):
        helper = shield_tests.BarrierTests()
        for hero, health in (("Reinhardt", 500), ("Doomfist", 250)):
            for reverse in (False, True):
                with self.subTest(hero=hero, reverse=reverse):
                    run, brig, rival, _ = helper.fixture()
                    self.assertTrue(run.match.switch_hero(rival, find_hero(hero)))
                    x, y, z = brig.mover.position
                    rival.mover.position, rival.mover.body = [x, y, z + 3], None
                    if reverse:
                        run.players.reverse()
                    for _ in range(80 if hero == "Doomfist" else 30):
                        run.step({brig: (0, 0, SECONDARY),
                                  rival: (-32768, 0, SECONDARY if hero == "Doomfist" else 0)})
                    fight = run.match.combat
                    run.step({brig: (0, 0, SECONDARY | FIRE),
                              rival: (-32768, 0, 8 if hero == "Reinhardt" else 0)})
                    for owner in (brig, rival):
                        self.assertTrue(owner.body_script.component.entity_var(596).value())
                    run.step({rival: (-32768, 0, 0)})
                    self.assertEqual(len(fight.brigitte.stuns), 2)
                    for owner in (brig, rival):
                        self.assertTrue(fight.is_stunned(owner))
                        self.assertFalse(owner.body_script.component.entity_var(596).value())
                        self.assertFalse(owner.body_script.component.held)
                    self.assertEqual({stun.instance.vars[215].value() for stun in fight.brigitte.stuns}, {2})
                    self.assertEqual((helper.health(run, brig), helper.health(run, rival)), (200, health))
                    for _ in range(120):
                        run.step({})
                    self.assertTrue(all(fight.is_stunned(owner) for owner in (brig, rival)))
                    for _ in range(10):
                        run.step({})
                    self.assertFalse(any(fight.is_stunned(owner) for owner in (brig, rival)))
                    self.assertEqual((helper.health(run, brig), helper.health(run, rival)), (200, health))

    def test_reinhardt_counter_resolves_outside_brigittes_forward_cone(self):
        self.assert_diagonal_counter("Reinhardt", 2, 500)

    def test_released_punch_counters_outside_brigittes_forward_cone(self):
        self.assert_diagonal_counter("Doomfist", 1.75, 250)

    def assert_diagonal_counter(self, hero, offset, health):
        punch = hero == "Doomfist"
        helper = shield_tests.BarrierTests()
        for reverse in (False, True):
            with self.subTest(reverse=reverse):
                run, brig, rival, _ = helper.fixture()
                self.assertTrue(run.match.switch_hero(rival, find_hero(hero)))
                x, y, z = brig.mover.position
                rival.mover.position, rival.mover.body = [x + offset, y, z + offset], None
                yaw = -24576
                if reverse:
                    run.players.reverse()
                for _ in range(80 if punch else 30):
                    run.step({brig: (0, 0, SECONDARY), rival: (yaw, 0, SECONDARY if punch else 0)})
                fight = run.match.combat
                run.step({brig: (0, 0, SECONDARY | FIRE), rival: (yaw, 0, 0 if punch else 8)})
                source = fight.brigitte.barriers[brig.body].instance
                normal, counter = (source.states[index] for index in (12, 75))
                self.assertTrue(normal.active and counter.active)
                contact = fight.brigitte._bash_target(brig, Command(run.frame), counter, counter=True)
                self.assertIsNone(contact)
                run.step({rival: (yaw, 0, 0)})
                self.assertTrue(all(fight.is_stunned(owner) for owner in (brig, rival)))
                self.assertEqual(len(fight.brigitte.stuns), 2)
                self.assertEqual({stun.instance.vars[27].value() for stun in fight.brigitte.stuns},
                                 {expr.Entity(rival.body)})
                self.assertEqual({stun.instance.vars[215].value() for stun in fight.brigitte.stuns}, {2})
                self.assertEqual((helper.health(run, brig), helper.health(run, rival)), (200, health))

    def test_counter_knockdown_uses_authored_immunity_override_on_both_bodies(self):
        helper = shield_tests.BarrierTests()
        for immune in ("source", "recipient"):
            with self.subTest(immune=immune):
                run, brig, rival, _ = helper.fixture()
                self.assertTrue(run.match.switch_hero(rival, find_hero("Reinhardt")))
                x, y, z = brig.mover.position
                rival.mover.position, rival.mover.body = [x, y, z + 3], None
                for _ in range(30):
                    run.step({brig: (0, 0, SECONDARY), rival: (-32768, 0, 0)})
                fight = run.match.combat
                run.step({brig: (0, 0, SECONDARY | FIRE), rival: (-32768, 0, 8)})
                immune_body = brig if immune == "source" else rival
                immunity = immune_body.body_script.component
                immunity.set(2547, True)
                self.assertTrue(immunity.entity_var(2547).value())
                run.step({rival: (-32768, 0, 0)})
                self.assertTrue(all(fight.is_stunned(owner) for owner in (brig, rival)))
                self.assertEqual(len(fight.brigitte.stuns), 2)
                for stun in fight.brigitte.stuns:
                    self.assertTrue(stun.instance.vars[0x7312].value())
                    self.assertEqual(stun.instance.vars[215].value(), 2)
                self.assertEqual((helper.health(run, brig), helper.health(run, rival)), (200, 500))

    def test_counter_charge_obeys_bash_cooldown_then_can_counter_again(self):
        helper = shield_tests.BarrierTests()
        run, brig, rival, _ = helper.fixture()
        self.assertTrue(run.match.switch_hero(rival, find_hero("Reinhardt")))
        x, y, z = brig.mover.position
        rival.mover.position, rival.mover.body = [x, y, z + 3], None
        fight = run.match.combat
        for cycle in range(2):
            for _ in range(30):
                run.step({brig: (0, 0, SECONDARY), rival: (-32768, 0, 0)})
            run.step({brig: (0, 0, SECONDARY | FIRE), rival: (-32768, 0, 8)})
            run.step({rival: (-32768, 0, 0)})
            self.assertTrue(all(fight.is_stunned(owner) for owner in (brig, rival)))
            self.assertEqual((helper.health(run, brig), helper.health(run, rival)), (200, 500))
            if cycle == 1:
                break
            for _ in range(135):
                run.step({})
            self.assertFalse(any(fight.is_stunned(owner) for owner in (brig, rival)))
            helper.raise_shield(run, brig, 20)
            barrier = fight.brigitte.barriers[brig.body]
            self.assertTrue(fight.brigitte._barrier_active(brig, barrier))
            movement = barrier.instance.states[14]
            before = movement.counter
            helper.bash(run, brig, frames=15)
            self.assertEqual(movement.counter, before)
            self.assertEqual(helper.health(run, rival), 500)
            for _ in range(700):
                run.step({})
            brig.mover.position, brig.mover.body = [x, y, z], None
            rival.mover.position, rival.mover.body = [x, y, z + 3], None

    def test_counter_tracker_reaches_beyond_the_ordinary_bash_contact(self):
        helper = shield_tests.BarrierTests()
        run, brig, rival, _ = helper.fixture()
        self.assertTrue(run.match.switch_hero(rival, find_hero("Reinhardt")))
        x, y, z = brig.mover.position
        rival.mover.position, rival.mover.body = [x, y, z + 3.85], None
        for _ in range(30):
            run.step({brig: (0, 0, SECONDARY), rival: (-32768, 0, 0)})
        fight = run.match.combat
        run.step({brig: (0, 0, SECONDARY | FIRE), rival: (-32768, 0, 8)})
        instance = fight.brigitte.barriers[brig.body].instance
        normal, counter = (instance.states[index] for index in (12, 75))
        self.assertTrue(normal.active and counter.active)
        radii = [state.evaluate(state.node.field("m_radius"), watch=False) for state in (normal, counter)]
        run.step({rival: (-32768, 0, 0)})
        target = next(item for item in fight._targets() if item.owner is rival)
        gap = rival.mover.position[2] - brig.mover.position[2] - target.radius
        self.assertGreater(gap, radii[0])
        self.assertLessEqual(gap, radii[1])
        self.assertTrue(fight.is_stunned(brig))
        self.assertTrue(fight.is_stunned(rival))
        self.assertEqual(helper.health(run, rival), 500)

    def test_counter_tracker_rejects_nonqualifying_charge_contacts(self):
        from test_game_mover import box

        from ow174.game.collision import StaticCollisionProvider, World

        helper = shield_tests.BarrierTests()
        cases = ("friendly", "same_facing", "wall", "out_of_range", "side", "held_punch")
        for boundary in cases:
            with self.subTest(boundary=boundary):
                run, brig, rival, _ = helper.fixture()
                punch = boundary == "held_punch"
                self.assertTrue(run.match.switch_hero(rival, find_hero("Doomfist" if punch else "Reinhardt")))
                if boundary == "friendly":
                    rival.team = brig.team
                yaw = 0 if boundary == "same_facing" else -32768
                x, y, z = brig.mover.position
                dx, dz = (3, 1.8) if boundary == "side" else (0, 4.5 if boundary == "out_of_range" else 3)
                rival.mover.position, rival.mover.body = [x + dx, y, z + dz], None
                if boundary == "wall":
                    collision = World(box(x - 20, x + 20, y - 2, y, z - 20, z + 20)
                                      + box(x - 2, x + 2, y, y + 4, z + 1.5, z + 1.7))
                    run.match.collision_provider = StaticCollisionProvider(collision)
                    run.match.collision_world = collision
                    for player in run.players:
                        player.mover.use(world=collision)
                for _ in range(80 if punch else 30):
                    run.step({brig: (0, 0, SECONDARY), rival: (yaw, 0, SECONDARY if punch else 0)})
                fight = run.match.combat
                before = helper.health(run, rival)
                run.step({brig: (0, 0, SECONDARY | FIRE), rival: (yaw, 0, SECONDARY if punch else 8)})
                self.assertTrue(brig.body_script.component.entity_var(596).value())
                rival_flag = rival.body_script.component.entity_var(596, create=False)
                self.assertEqual(bool(rival_flag and rival_flag.value()), not punch)
                run.step({rival: (yaw, 0, SECONDARY if punch else 0)})
                self.assertFalse(fight.is_stunned(brig))
                ordinary_damage = {"same_facing": 2.5, "held_punch": 5}.get(boundary, 0)
                self.assertEqual(helper.health(run, rival), before - ordinary_damage)
                self.assertEqual(fight.is_stunned(rival), ordinary_damage > 0)
                self.assertTrue(all(stun.instance.vars[215].value() == 0.75 for stun in fight.brigitte.stuns))

    def test_rejected_second_bash_does_not_borrow_the_first_stun_for_impulse(self):
        helper = shield_tests.BarrierTests()
        run, first, target, second = helper.fixture()
        self.assertTrue(run.match.switch_hero(second, heroes()[HERO]))
        x, y, z = first.mover.position
        target.mover.position, target.mover.body = [x, y, z + 3], None
        second.mover.position, second.mover.body = [x + 8, y, z], None
        for _ in range(20):
            run.step({first: (0, 0, SECONDARY), second: (0, 0, SECONDARY)})
        run.step({first: (0, 0, SECONDARY | FIRE), second: (0, 0, SECONDARY)})
        run.step({first: (0, 0, SECONDARY), second: (0, 0, SECONDARY)})
        fight = run.match.combat
        old = fight.brigitte.stuns[0]
        self.assertTrue(old.instance.states[20].active)
        self.assertEqual(helper.health(run, target), 195)
        target.body_script.component.set(2547, True)
        x, y, z = target.mover.position
        second.mover.position, second.mover.body = [x, y, z - 3], None
        with patch.object(
            target.mover, "add_external_impulse", wraps=target.mover.add_external_impulse,
        ) as impulse:
            run.step({second: (0, 0, SECONDARY | FIRE)})
            for _ in range(8):
                run.step({second: (0, 0, SECONDARY)})
                if helper.health(run, target) == 190:
                    break
        self.assertEqual(helper.health(run, target), 190)
        self.assertTrue(old.instance.states[20].active)
        new = next(stun for stun in fight.brigitte.stuns if stun is not old)
        phase = new.instance.states.get(20)
        self.assertFalse(phase and phase.active)
        impulse.assert_not_called()

    def test_caster_death_keeps_issued_control_until_its_own_expiry(self):
        run, source, target = self.fixture()
        fight = run.match.combat
        body = next(t for t in fight._targets() if t.owner is source)
        fight._damage(target, body, 1000, False, None, run.frame)
        self.assertTrue(target.body_script.component.entity_var(888).value())
        for _ in range(55):
            run.step({})
        self.assertFalse(fight.brigitte.stuns)

    def test_target_death_and_body_replacement_retire_control_without_stale_instances(self):
        for replace in (False, True):
            with self.subTest(replace=replace):
                run, source, target = self.fixture()
                fight = run.match.combat
                lease = fight.brigitte.stuns[0]
                if replace:
                    run.match.switch_hero(target, source.hero)
                else:
                    body = next(t for t in fight._targets() if t.owner is target)
                    fight._damage(source, body, 1000, False, None, run.frame)
                run.step({})
                self.assertFalse(fight.brigitte.stuns)
                self.assertNotIn(lease.instance.id, lease.instance.component.instances)

    def flash_arena(self):
        helper = flash_tests.FlashbangTests()
        run, caster, recipient = helper.arena(distance=3)
        run.match.switch_hero(recipient, heroes()[HERO])
        x, y, z = caster.mover.position
        recipient.mover.position, recipient.mover.body = [x, y, z + 3], None
        return helper, run, caster, recipient

    def test_actual_flashbang_suppresses_brigitte_actions_then_flail_recovers(self):
        helper, run, caster, recipient = self.flash_arena()
        helper.cast(run, caster, recipient)
        fight = run.match.combat
        end = fight.stuns[recipient.body]
        self.assertEqual(helper.health(run, recipient), 180)  #25 reduced by5 armor
        before = helper.health(run, caster)
        position = tuple(recipient.mover.position)
        while run.frame < end - 1:
            helper.step(run, caster, recipient, target_buttons=3 | 8 | 16 | 32, forward=127)
            self.assertEqual(tuple(recipient.mover.position), position)
            self.assertEqual(helper.health(run, caster), before)
            self.assertFalse(recipient.body_script.component.held)
        for _ in range(40):
            helper.step(run, caster, recipient, target_buttons=FIRE)
        self.assertLess(helper.health(run, caster), before)

    def test_actual_flashbang_cancels_brigitte_whip_before_outgoing_damage(self):
        from ow174.game.script import nodes

        helper, run, caster, recipient = self.flash_arena()
        before = helper.health(run, caster)
        helper.step(run, caster, recipient, cast=16, target_buttons=8)
        whip = next(i for i in recipient.body_script.component.instances.values() if i.graph.index == 0x1259)
        self.assertTrue(whip.states[0].flags & nodes.Ability.RUNNING)
        for _ in range(50):
            if run.match.combat.is_stunned(recipient):
                break
            helper.step(run, caster, recipient, target_buttons=8)
        self.assertTrue(run.match.combat.is_stunned(recipient))
        self.assertFalse(whip.states[0].flags & nodes.Ability.RUNNING)
        self.assertEqual(helper.health(run, caster), before)
        for _ in range(20):
            helper.step(run, caster, recipient, target_buttons=8)
        self.assertEqual(helper.health(run, caster), before)
        self.assertFalse(run.match.combat.brigitte.whips)

    def test_actual_bash_interrupts_brigitte_whip_windup_and_clears_input(self):
        from ow174.game.script import nodes

        run, source, target, _ = shield_tests.BarrierTests().fixture()
        run.match.switch_hero(target, heroes()[HERO])
        x, y, z = source.mover.position
        target.mover.position, target.mover.body = [x, y, z + 3], None
        run.step({source: (0, 0, SECONDARY), target: (-32768, 0, 8)})
        whip = next(i for i in target.body_script.component.instances.values() if i.graph.index == 0x1259)
        self.assertTrue(whip.states[0].flags & nodes.Ability.RUNNING)
        run.step({source: (0, 0, SECONDARY | FIRE), target: (-32768, 0, 8)})
        run.step({source: (0, 0, SECONDARY), target: (-32768, 0, 8)})
        self.assertTrue(run.match.combat.is_stunned(target))
        self.assertFalse(whip.states[0].flags & nodes.Ability.RUNNING)
        self.assertFalse(target.body_script.component.held)
        for _ in range(25):
            run.step({source: (0, 0, SECONDARY), target: (-32768, 0, 8)})
        self.assertFalse(run.match.combat.brigitte.whips)
        self.assertEqual(shield_tests.BarrierTests().health(run, source), 200)

    def test_two_actual_casters_keep_independent_control_until_last_graph_expires(self):
        from test_game_combat import aim

        run, first, second, target = shield_tests.BarrierTests().fixture()
        run.match.switch_hero(second, heroes()[HERO])
        second.team, target.team = first.team, 1 - first.team
        x, y, z = first.mover.position
        second.mover.position, second.mover.body = [x + 1, y, z], None
        target.mover.position, target.mover.body = [x, y, z + 3], None
        facing = aim(run.match.combat._eye(second), run.match.combat._eye(target))
        for _ in range(20):
            run.step({first: (0, 0, SECONDARY), second: (*facing, SECONDARY)})
        run.step({first: (0, 0, SECONDARY | FIRE), second: (*facing, SECONDARY)})
        run.step({first: (0, 0, SECONDARY), second: (*facing, SECONDARY)})
        for _ in range(20):
            run.step({second: (*facing, SECONDARY)})
        run.step({second: (*facing, SECONDARY | FIRE)})
        run.step({second: (*facing, SECONDARY)})
        bridge = run.match.combat.brigitte
        self.assertEqual(len(bridge.stuns), 2)
        for _ in range(28):
            run.step({})
        self.assertEqual(len(bridge.stuns), 1)
        self.assertTrue(target.body_script.component.entity_var(888).value())
        for _ in range(25):
            run.step({})
        self.assertFalse(bridge.stuns)
        self.assertFalse(target.body_script.component.entity_var(888).value())

    def test_bash_blocks_full_charge_rally_tap_until_a_fresh_press(self):
        run, source, target, _ = shield_tests.BarrierTests().fixture()
        self.assertTrue(run.match.switch_hero(target, heroes()[HERO]))
        x, y, z = source.mover.position
        target.mover.position, target.mover.body = [x, y, z + 3], None
        script = target.body_script
        script.ult_seconds = 0
        component = script.component
        cost = component.entity_var(338).value()
        script.set_ult_charge(cost)
        fight = run.match.combat
        vitals = fight.vitals_of(next(t for t in fight._targets() if t.owner is target))
        shield_tests.BarrierTests().raise_shield(run, source, 22)
        shield_tests.BarrierTests().bash(run, source, frames=2)
        self.assertTrue(fight.is_stunned(target))

        run.step({target: (0, 0, 32)})
        self.assertFalse(fight.brigitte.rally_active(component))
        self.assertEqual(component.entity_var(342).value(), cost)
        self.assertEqual(vitals.temporary_armour, 0)
        run.step({target: (0, 0, 0)})
        for _ in range(60):
            run.step({})
        self.assertFalse(fight.is_stunned(target))
        self.assertFalse(fight.brigitte.rally_active(component))
        self.assertEqual(component.entity_var(342).value(), cost)
        self.assertEqual(vitals.temporary_armour, 0)

        run.step({target: (0, 0, 32)})
        self.assertTrue(fight.brigitte.rally_active(component))
        self.assertEqual(component.entity_var(342).value(), 0)
        self.assertEqual(vitals.temporary_armour, 15)

    def test_bash_control_does_not_end_an_already_running_rally_aura(self):
        run, source, target, _ = shield_tests.BarrierTests().fixture()
        run.match.switch_hero(target, heroes()[HERO])
        x, y, z = source.mover.position
        target.mover.position, target.mover.body = [x, y, z + 3], None
        target.body_script.ult_seconds = 0
        target.body_script.set_ult_charge(target.body_script.component.entity_var(338).value())
        run.step({source: (0, 0, SECONDARY), target: (0, 0, 32)})
        for _ in range(60):
            run.step({source: (0, 0, SECONDARY)})
        bridge = run.match.combat.brigitte
        self.assertTrue(bridge.rally_active(target.body_script.component))
        shield_tests.BarrierTests().bash(run, source, frames=2)
        self.assertTrue(run.match.combat.is_stunned(target))
        self.assertTrue(bridge.rally_active(target.body_script.component))
        health = shield_tests.BarrierTests().health(run, target)
        actor = next(t for t in run.match.combat._targets() if t.owner is target)
        before = run.match.combat.vitals_of(actor).temporary_armour
        for _ in range(35):
            run.step({})
        self.assertTrue(run.match.combat.is_stunned(target))
        self.assertGreater(run.match.combat.vitals_of(actor).temporary_armour, before)
        self.assertGreater(shield_tests.BarrierTests().health(run, target), health)
        for _ in range(20):
            run.step({})
        self.assertFalse(run.match.combat.is_stunned(target))
        self.assertTrue(bridge.rally_active(target.body_script.component))


if __name__ == "__main__":
    unittest.main()
