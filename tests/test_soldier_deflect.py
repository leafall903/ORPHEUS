"""Actual Soldier volleys against resource-box Genji interception reconstruction."""

import unittest

from test_game_combat import Run, aim, in_world

from ow174.game import combat, deflection, projectile_tags, projectiles, stats
from ow174.game.collision import World, flat_world
from ow174.game.commands import JUMP, Command
from ow174.game.content import heroes
from ow174.game.script.nodes import Ability
from ow174.game.world import OP_DESTROY


class SoldierDeflectTests(unittest.TestCase):
    def scene(self, distance=4, facing=-32768):
        run = Run(names=("Soldier", "Genji"))
        soldier, genji = run.players
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        run.match.switch_hero(genji, heroes()[deflection.GENJI])
        run.match.collision_world = flat_world(0)
        soldier.mover.position[:] = [0, 0, 0]
        genji.mover.position[:] = [0, 0, distance]
        genji.team = 1 - soldier.team
        for player in run.players:
            player.mover.body = None
            player.body_script.ult_seconds = 0
        for _ in range(20):
            self.step(run, soldier, genji, facing=facing)
        self.step(run, soldier, genji, facing=facing, genji_buttons=16)
        for _ in range(4):
            self.step(run, soldier, genji, facing=facing)
        self.assertTrue(self.state(genji).active)
        return run, soldier, genji

    def step(self, run, soldier, genji, buttons=0, *, facing=-32768, angles=(0, 0), genji_buttons=0):
        run.step({soldier: (*angles, buttons), genji: (facing, 0, genji_buttons)})

    def state(self, genji):
        return next(i for i in genji.body_script.component.instances.values()
                    if i.graph.index == 0x03A3).states[10]

    def target(self, run, owner):
        return next(t for t in run.match.combat._targets() if t.owner is owner)

    def pool(self, run, owner):
        return run.match.combat.vitals_of(self.target(run, owner))

    def launch(self, run, soldier, genji, *, angles=(0, 0)):
        self.step(run, soldier, genji, 2, angles=angles)
        rocket, = run.match.combat.rockets
        return rocket

    def redirect(self, run, soldier, genji, rocket, *, angles=(0, 0)):
        for _ in range(140):
            if rocket.shooter is genji:
                return
            self.assertIn(rocket, run.match.combat.rockets)
            self.step(run, soldier, genji, angles=angles)
        self.fail("actual tagged Helix did not redirect")

    def finish(self, run, soldier, genji, rocket):
        for _ in range(200):
            if rocket not in run.match.combat.rockets:
                return
            self.step(run, soldier, genji)
        self.fail("reflected projectile did not terminate")

    def test_actual_field_canister_keeps_ownership_through_active_deflect(self):
        control, shooter, defender = self.scene()
        helix = self.launch(control, shooter, defender)
        self.redirect(control, shooter, defender, helix)

        run, soldier, genji = self.scene()
        soldier.mover.position[:] = [0, 0, 2.6]
        soldier.mover.body = None
        fight = run.match.combat
        pool = self.pool(run, soldier)
        pool.damage(50)
        before = pool.total
        self.step(run, soldier, genji, 16)
        for _ in range(45):
            self.step(run, soldier, genji)
            if fight.rockets:
                break
        canister, = fight.rockets
        field = canister.shot
        self.assertEqual((canister.definition, field.spawn), (0x040000000000072D, projectiles.FIELD_ENTITY))
        ray = (canister.position, canister.direction, 10)
        contact = deflection.trace(fight, soldier, helix.direct, *ray)
        self.assertIsNotNone(contact)
        self.assertIs(contact[1].owner, genji)
        self.assertFalse(canister.direct.deflectable)
        self.assertIsNone(canister.blast)

        for _ in range(55):
            self.step(run, soldier, genji)
            if field.field_tick is not None and run.frame > field.field_tick:
                break
        else:
            self.fail("Field did not open and begin healing")
        self.assertTrue(self.state(genji).active)
        self.assertIs(canister.shooter, soldier)
        self.assertIs(canister.shot, field)
        self.assertIs(field.player, soldier)
        self.assertEqual(field.body, soldier.body)
        self.assertAlmostEqual(pool.total, before + 35 * combat.TICK)
        self.assertEqual(self.pool(run, genji).total, 200)
        self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), 0)
        self.assertFalse(genji.client.messages(combat.HIT))

    def test_reflected_rifle_critical_and_lethal_hit_belong_to_genji(self):
        for lethal in (False, True):
            with self.subTest(lethal=lethal):
                run, soldier, genji = self.scene()
                if lethal:
                    self.pool(run, soldier).damage(170)
                self.step(run, soldier, genji, 1)
                self.assertEqual(self.pool(run, genji).total, 200)
                self.assertEqual(self.pool(run, soldier).total, 0 if lethal else 160)
                expected = 30 if lethal else 40
                self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), expected)
                self.assertEqual(genji.body_script.component.entity_var(342).value(), expected)
                self.assertEqual(soldier.stats.value(stats.HERO_DAMAGE), 0)
                self.assertEqual(soldier.body_script.component.entity_var(342).value(), 0)
                self.assertFalse(soldier.client.messages(combat.HIT))
                self.assertEqual(genji.client.messages(combat.HIT)[-1]['+0x78']['+0x4'], expected)
                self.assertEqual(soldier.body_script.component.instances[11].vars[53].value(), 29)
                if lethal:
                    self.assertEqual(self.pool(run, soldier).killer, genji.body)
                    self.assertEqual(genji.stats.value(stats.ELIMINATIONS), 1)

    def test_genji_reflects_ana_rifle_through_full_ally_then_doses_soldier(self):
        for scoped in (False, True):
            with self.subTest(scoped=scoped):
                run, soldier, genji = self.scene()
                fight = run.match.combat
                ana_hero = heroes()[0x02E000000000013B]
                ana = run.match.add_player(3, "Ana", ana_hero.guid, soldier.team, False)
                in_world(run.match, ana)
                run.players.append(ana)
                self.assertTrue(run.match.switch_hero(ana, ana_hero))
                ana.mover.position = [0, 0, -3]
                ana.mover.body = None
                run.step({genji: (-32768, 0, 0)})
                angles = aim(fight._eye(ana), (0, 1, 4))
                if scoped:
                    for _ in range(35):
                        run.step({ana: (*angles, 2), genji: (-32768, 0, 0)})
                self.assertTrue(self.state(genji).active)
                run.step({ana: (*angles, 3 if scoped else 1), genji: (-32768, 0, 0)})
                for _ in range(35):
                    if fight.biotic_doses:
                        break
                    run.step({genji: (-32768, 0, 0)})
                dose, = fight.biotic_doses
                self.assertEqual((dose.source, dose.target), (genji.body, soldier.body))
                self.assertFalse(dose.healing)
                for _ in range(45):
                    run.step({})
                self.assertEqual(self.pool(run, genji).total, 200)
                self.assertEqual(self.pool(run, soldier).total, 130)
                self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), 70)
                self.assertEqual(ana.stats.value(stats.HERO_DAMAGE), 0)

    @unittest.skipUnless(projectile_tags.CATALOG_SHA256, "prepared original003 target tags required")
    def test_reflected_sleep_dart_interrupts_soldier_field_with_genji_credit(self):
        run, soldier, genji = self.scene(distance=8)
        fight = run.match.combat
        ana = run.match.add_player(3, "Ana", soldier.hero.guid, soldier.team, False)
        in_world(run.match, ana)
        run.players.append(ana)
        self.assertTrue(run.match.switch_hero(ana, heroes()[0x02E000000000013B]))
        ana.mover.position[:] = [1.5, 0, 0]
        ana.mover.body = None
        ana.body_script.ult_seconds = 0
        angles = aim(fight._eye(ana), (0, 1.1, 8))
        run.step({ana: (*angles, 8), genji: (-32768, 0, 0)})
        dart = None
        for _ in range(50):
            run.frame += 1
            run.match.update(run.frame * combat.TICK, run.frame)
            if fight.rockets:
                dart, = fight.rockets
                if dart.shooter is genji:
                    break
        self.assertIsNotNone(dart)
        self.assertIs(dart.shooter, genji)
        self.assertEqual(dart.source_entity, genji.body)
        self.assertEqual(dart.direct.effect, combat.SLEEP_EFFECT)
        self.assertFalse(fight.sleeps)
        field = next(i for i in soldier.body_script.component.instances.values()
                     if i.graph.index == 0x0257)
        windup = field.state(field.graph.node(8).state)
        soldier.take_commands([Command(run.frame, buttons=16)])
        self.assertTrue(windup.active)
        run.frame += 40
        run.match.update(run.frame * combat.TICK, run.frame)
        self.assertNotIn(dart, fight.rockets)
        self.assertEqual(self.pool(run, soldier).total, 195)
        self.assertEqual(set(fight.sleeps), {soldier.body})
        self.assertEqual(self.pool(run, genji).total, 200)
        self.assertEqual(self.pool(run, ana).total, 200)
        self.assertFalse(windup.active)
        self.assertFalse(any(shot.spawn == projectiles.FIELD_ENTITY for shot in fight.projectiles.shots))
        self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), 5)
        self.assertEqual(genji.body_script.component.entity_var(342).value(), 5)
        self.assertEqual(ana.stats.value(stats.HERO_DAMAGE), 0)
        self.assertEqual(ana.body_script.component.entity_var(342).value(), 0)

    def test_reflected_rifle_keeps_cumulative_falloff_and_remaining_range(self):
        for distance in (20, 60, 100):
            with self.subTest(distance=distance):
                run, soldier, genji = self.scene(distance=distance)
                self.step(run, soldier, genji, 1)
                damage = 200 - self.pool(run, soldier).total
                if distance == 20:
                    # The total return path exceeds30m; resetting at reflection
                    # would incorrectly apply the full40 critical damage.
                    # Authored falloff is30..50m down to0.3, not0.5.
                    self.assertGreater(damage, 29)
                    self.assertLess(damage, 30)
                elif distance == 60:
                    self.assertAlmostEqual(damage, 12, places=5)
                else:
                    #100m out and back exceeds the rifle's authored180m budget.
                    self.assertEqual(damage, 0)
                self.assertEqual(self.pool(run, genji).total, 200)
                self.assertAlmostEqual(genji.stats.value(stats.HERO_DAMAGE), damage)
                self.assertEqual(soldier.stats.value(stats.HERO_DAMAGE), 0)

    def test_helix_keeps_same_tick_remainder_lifetime_source_and_damage_credit(self):
        run, soldier, genji = self.scene()
        rocket = self.launch(run, soldier, genji)
        self.assertTrue(rocket.direct.deflectable)
        self.assertIsNotNone(rocket.shot)
        for _ in range(20):
            before, flown = rocket.position, rocket.flown
            self.step(run, soldier, genji)
            if rocket.shooter is genji:
                break
        self.assertIs(rocket.shooter, genji)
        self.assertEqual(rocket.source_entity, genji.body)
        self.assertEqual(rocket.team, genji.team)
        self.assertIsNone(rocket.shot)
        self.assertAlmostEqual(rocket.flown - flown, combat.TICK)
        self.assertAlmostEqual(rocket.left + rocket.flown, 2)
        # Front box reaches1m ahead of Genji at z4: contact z3.
        self.assertAlmostEqual(rocket.position[2], 6 - (before[2] + rocket.speed * combat.TICK), places=5)
        state = rocket.position, rocket.flown, rocket.left
        run.match.combat._fly(rocket)  # same world tick must do no extra work
        self.assertEqual((rocket.position, rocket.flown, rocket.left), state)
        self.finish(run, soldier, genji, rocket)
        self.assertEqual(self.pool(run, soldier).total, 80)
        self.assertEqual(self.pool(run, genji).total, 200)
        self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), 120)
        self.assertEqual(soldier.stats.value(stats.HERO_DAMAGE), 0)
        self.assertEqual(genji.body_script.component.entity_var(342).value(), 120)
        self.assertEqual(soldier.body_script.component.entity_var(342).value(), 0)

    def test_reflection_retires_confirmed_old_copies_without_explosion_release(self):
        run, soldier, genji = self.scene(distance=20)
        for player in run.players:
            player.client.entities.clear()
        rocket = self.launch(run, soldier, genji)
        original = rocket.shot
        self.redirect(run, soldier, genji, rocket)
        copies = {soldier: set(original.created),
                  genji: {entity for delivery in original.observers for entity in delivery.record.ids}}
        self.assertTrue(all(copies.values()))  # confirmed owner and launched observer paths
        self.assertNotIn(original, run.match.combat.projectiles.shots)
        self.assertIsNone(original.release_at)
        for _ in range(3):
            self.step(run, soldier, genji)
        for player, ids in copies.items():
            retired = [item for item in player.client.entities
                       if item.entity in ids and item.op == OP_DESTROY]
            self.assertEqual({item.entity for item in retired}, ids)
            self.assertEqual(len(retired), len(ids))
            self.assertTrue(all(item.ch2 is None for item in retired))
        self.assertTrue(all(not delivery.explode for delivery in original.observers))
        self.assertIn(rocket, run.match.combat.rockets)
        self.finish(run, soldier, genji, rocket)
        self.assertEqual(self.pool(run, soldier).total, 80)

    def test_rear_and_rotated_front_use_actual_facing(self):
        run, soldier, genji = self.scene(facing=0)
        self.step(run, soldier, genji, 1, facing=0)
        self.assertEqual(self.pool(run, genji).total, 160)
        self.assertEqual(self.pool(run, soldier).total, 200)
        run, soldier, genji = self.scene()
        genji.mover.position[:] = [4, 0, 0]
        genji.mover.body = None
        for _ in range(4):
            self.step(run, soldier, genji, facing=-16384)
        self.step(run, soldier, genji, 1, facing=-16384, angles=(16384, 0))
        self.assertEqual(self.pool(run, genji).total, 200)
        self.assertEqual(self.pool(run, soldier).total, 160)

    def test_wall_before_deflect_boxes_stops_rifle(self):
        run, soldier, genji = self.scene()
        run.match.collision_world = World([
            (-20, 0, -20, -20, 0, 20, 20, 0, 20), (-20, 0, -20, 20, 0, 20, 20, 0, -20),
            (-10, -10, 2, 10, -10, 2, 10, 10, 2), (-10, -10, 2, 10, 10, 2, -10, 10, 2),
        ])
        self.step(run, soldier, genji, 1)
        self.assertEqual(self.pool(run, genji).total, 200)
        self.assertEqual(self.pool(run, soldier).total, 200)
        self.assertFalse(genji.client.messages(combat.HIT))

    def test_frontal_melee_blocks_without_reflection_and_rear_melee_still_damages(self):
        for facing in (-32768, 0):
            with self.subTest(facing=facing):
                run, soldier, genji = self.scene(distance=1.5, facing=facing)
                self.step(run, soldier, genji, facing=facing, genji_buttons=JUMP)
                self.step(run, soldier, genji, 0x800, facing=facing)
                for _ in range(40):
                    self.step(run, soldier, genji, facing=facing)
                self.assertEqual(self.pool(run, genji).total, 200 if facing == -32768 else 170)
                if facing == -32768:
                    self.assertAlmostEqual(genji.mover.position[2], 1.5)
                else:
                    self.assertGreater(genji.mover.position[2], 1.5)
                self.assertEqual(self.pool(run, soldier).total, 200)
                self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), 0)

    def test_resource_box_intercepts_a_projectile_that_misses_the_body_capsule(self):
        run, soldier, genji = self.scene()
        angles = aim(run.match.combat._eye(soldier), (.68, 1.55, 4))
        target = self.target(run, genji)
        a, b = target.segment()
        self.assertIsNone(combat.ray_capsule(run.match.combat._eye(soldier), combat.aim(*angles),
                                            a, b, target.radius))
        rocket = self.launch(run, soldier, genji, angles=angles)
        self.redirect(run, soldier, genji, rocket)
        self.assertEqual(rocket.source_entity, genji.body)
        self.assertEqual(self.pool(run, genji).total, 200)

    def test_deflect_is_not_splash_immunity(self):
        run, soldier, genji = self.scene()
        run.match.collision_world = World([
            (-20, 0, -20, -20, 0, 20, 20, 0, 20), (-20, 0, -20, 20, 0, 20, 20, 0, -20),
            (-10, -10, 4.5, 10, -10, 4.5, 10, 10, 4.5),
            (-10, -10, 4.5, 10, 10, 4.5, -10, 10, 4.5),
        ])
        angles = aim(run.match.combat._eye(soldier), (2, 1.55, 4.5))
        rocket = self.launch(run, soldier, genji, angles=angles)
        self.finish(run, soldier, genji, rocket)
        self.assertIs(rocket.shooter, soldier)
        self.assertTrue(self.state(genji).active)
        self.assertLess(self.pool(run, genji).total, 200)
        self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), 0)

    def test_expiry_cooldown_rejection_and_recovery_control_reflection(self):
        run, soldier, genji = self.scene()
        for _ in range(140):
            self.step(run, soldier, genji)
        self.assertFalse(self.state(genji).active)
        counter = self.state(genji).counter
        self.step(run, soldier, genji, genji_buttons=16)
        for _ in range(5):
            self.step(run, soldier, genji)
        self.assertEqual(self.state(genji).counter, counter)
        for _ in range(650):
            self.step(run, soldier, genji)
            self.assertEqual(self.state(genji).counter, counter)
        self.step(run, soldier, genji, genji_buttons=16)
        self.assertTrue(self.state(genji).active)
        self.assertGreater(self.state(genji).counter, counter)
        self.step(run, soldier, genji, 1)
        self.assertEqual(self.pool(run, soldier).total, 160)

    def test_in_flight_helix_orders_deflect_expiry_without_genji_commands(self):
        for offset, reflected in ((110, True), (122, False)):
            for jump in (False, True):
                with self.subTest(offset=offset, jump=jump):
                    run, soldier, genji = self.scene()
                    state = self.state(genji)
                    elapsed = (state.now - state.start) // genji.body_script.frame_ms
                    launch = run.frame - elapsed + offset
                    run.match.update(launch * combat.TICK, launch)
                    self.assertTrue(state.active)
                    soldier.take_commands([Command(launch, buttons=2)])
                    run.match.update((launch + 1) * combat.TICK, launch + 1)
                    rocket, = run.match.combat.rockets
                    self.assertEqual(rocket.last_tick, launch + 1)

                    end = launch + 300
                    for tick in ((end,) if jump else range(launch + 2, end + 1)):
                        run.match.update(tick * combat.TICK, tick)
                    self.assertFalse(state.active)
                    self.assertNotIn(rocket, run.match.combat.rockets)
                    self.assertIs(rocket.shooter, genji if reflected else soldier)
                    health = self.pool(run, soldier).total, self.pool(run, genji).total
                    self.assertEqual(health, (80, 200) if reflected else (200, 80))
                    self.assertEqual(soldier.stats.value(stats.HERO_DAMAGE), 0 if reflected else 120)
                    self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), 120 if reflected else 0)

    def test_reflected_rocket_retires_with_deflector_body_replacement(self):
        run, soldier, genji = self.scene()
        rocket = self.launch(run, soldier, genji)
        self.redirect(run, soldier, genji, rocket)
        old = genji.body
        self.assertTrue(run.match.switch_hero(genji, genji.hero))
        self.assertNotEqual(genji.body, old)
        self.assertNotIn(rocket, run.match.combat.rockets)
        for _ in range(40):
            self.step(run, soldier, genji)
        self.assertEqual(self.pool(run, soldier).total, 200)

    def test_near_ready_press_keeps_original_short_buffer_after_release(self):
        run, soldier, genji = self.scene()
        ability = next(st for i in genji.body_script.component.instances.values()
                       if i.graph.index == 0x03A3 for st in i.states.values()
                       if isinstance(st, Ability))
        for _ in range(140):
            self.step(run, soldier, genji)
        counter = self.state(genji).counter
        for _ in range(750):
            if 0 < ability.remaining() <= .1:
                break
            self.step(run, soldier, genji)
        self.assertGreater(ability.remaining(), 0)
        self.assertLessEqual(ability.remaining(), .1)
        self.step(run, soldier, genji, genji_buttons=16)
        self.assertEqual(self.state(genji).counter, counter)
        for _ in range(10):
            self.step(run, soldier, genji)
        self.assertTrue(self.state(genji).active)
        self.assertGreater(self.state(genji).counter, counter)
        self.step(run, soldier, genji, 1)
        self.assertEqual(self.pool(run, soldier).total, 160)

    def test_held_start_waits_for_cooldown_even_after_original_press_buffer_expires(self):
        run, soldier, genji = self.scene()
        for _ in range(140):
            self.step(run, soldier, genji)
        counter = self.state(genji).counter
        ability = next(st for i in genji.body_script.component.instances.values()
                       if i.graph.index == 0x03A3 for st in i.states.values()
                       if isinstance(st, Ability))
        for _elapsed in range(650):
            before = ability.remaining()
            self.step(run, soldier, genji, genji_buttons=16)
            if self.state(genji).counter != counter:
                self.assertLessEqual(before, combat.TICK + 1e-6)
                break
        self.assertGreater(_elapsed, 300)  # the old press did not bypass its long cooldown
        self.assertTrue(self.state(genji).active)
        self.assertEqual(self.state(genji).counter, counter + 1)
        self.step(run, soldier, genji, 1, genji_buttons=16)
        self.assertEqual(self.pool(run, soldier).total, 160)

    def test_reflected_visor_round_keeps_noncritical_payload_and_new_charge_owner(self):
        run, soldier, genji = self.scene()
        soldier.body_script.set_ult_charge(soldier.body_script.component.vars[338].value())
        self.step(run, soldier, genji, 0x20)
        for _ in range(80):
            self.step(run, soldier, genji)
        self.step(run, soldier, genji, 1)
        self.assertEqual(self.pool(run, soldier).total, 180)
        self.assertEqual(self.pool(run, genji).total, 200)
        self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), 20)
        self.assertEqual(genji.body_script.component.entity_var(342).value(), 20)
        self.assertEqual(soldier.body_script.component.entity_var(342).value(), 0)

    def test_flying_reflected_helix_keeps_damage_after_deflector_is_killed(self):
        run, soldier, genji = self.scene(distance=20)
        self.pool(run, genji).damage(170)
        rocket = self.launch(run, soldier, genji)
        self.redirect(run, soldier, genji, rocket)
        for _ in range(10):
            self.step(run, soldier, genji, facing=0)
        self.step(run, soldier, genji, 1, facing=0)
        self.assertTrue(self.pool(run, genji).dead)
        self.assertIn(rocket, run.match.combat.rockets)
        self.finish(run, soldier, genji, rocket)
        self.assertEqual(self.pool(run, soldier).total, 80)
        self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), 120)
        self.assertEqual(genji.body_script.component.entity_var(342).value(), 0)
        self.assertEqual(soldier.stats.value(stats.HERO_DAMAGE), 30)
        self.assertEqual(soldier.body_script.component.entity_var(342).value(), 30)

    def test_reflected_helix_outlives_original_caster_body_replacement(self):
        run, soldier, genji = self.scene(distance=20)
        rocket = self.launch(run, soldier, genji)
        self.redirect(run, soldier, genji, rocket)
        old = soldier.body
        self.assertTrue(run.match.switch_hero(soldier, soldier.hero))
        self.assertNotEqual(soldier.body, old)
        soldier.mover.position[:] = [0, 0, 0]
        soldier.mover.body = None
        soldier.body_script.ult_seconds = 0
        self.assertIn(rocket, run.match.combat.rockets)
        self.finish(run, soldier, genji, rocket)
        self.assertEqual(self.pool(run, soldier).total, 80)
        self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), 120)
        self.assertEqual(genji.body_script.component.entity_var(342).value(), 120)
        self.assertEqual(soldier.stats.value(stats.HERO_DAMAGE), 0)

    def test_reflected_helix_keeps_original_range_lifetime_and_no_expiry_explosion(self):
        run, soldier, genji = self.scene(distance=60)
        rocket = self.launch(run, soldier, genji)
        self.redirect(run, soldier, genji, rocket)
        self.finish(run, soldier, genji, rocket)
        self.assertAlmostEqual(rocket.flown, 2)
        self.assertAlmostEqual(rocket.left, 0)
        self.assertAlmostEqual(rocket.position[2], 18, places=4)
        self.assertFalse(self.state(genji).active)
        self.assertEqual(self.pool(run, soldier).total, 200)
        self.assertEqual(self.pool(run, genji).total, 200)
        self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), 0)
        self.assertEqual(genji.body_script.component.entity_var(342).value(), 0)
