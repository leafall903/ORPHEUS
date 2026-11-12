"""Actual Soldier attacks against resource1D02 grasp and reconstructed extra HP."""

import hashlib
import json
import os
import struct
import unittest
from pathlib import Path

from test_game_combat import Run, aim, in_world

from ow174.game import combat, deflection, projectile_tags, projectiles, replay_manifest, absorption as sigma_grasp, stats
from ow174.game.collision import World, flat_world
from ow174.game.commands import Command
from ow174.game.content import heroes
from ow174.game.script import graph
from ow174.game.world import OP_DESTROY, POOLS, SHIELDS_POOL


class ProjectileTagTests(unittest.TestCase):
    def test_input_validation_preserves_unknown_and_excluded_definitions(self):
        data = {"version": 1, "build": projectile_tags.BUILD,
                "entities": {"0x04000000000003D4": [],
                             "0x04000000000008BE": ["0x0D60000000000145"]}}
        result = projectile_tags.read_catalog(json.dumps(data))
        self.assertEqual(result[0x04000000000003D4], frozenset())
        with self.assertRaises(TypeError):
            result[0x04000000000003D4] = frozenset({1})
        for edit in ({"version": True}, {"build": "other"}, {"entities": {"03D4": []}},
                     {"entities": {"0x04000000000003D4": ["0x0400000000000145"]}}):
            with self.subTest(edit=edit), self.assertRaises(ValueError):
                projectile_tags.read_catalog(json.dumps({**data, **edit}))
        with self.assertRaises(ValueError):
            projectile_tags.read_catalog("[]")
        self.assertFalse(projectile_tags.absorbable(None))
        self.assertFalse(projectile_tags.absorbable(123))

    def test_loaded_external_catalog_digest_is_in_replay_identity(self):
        path = os.environ.get("ORPHEUS_PROJECTILE_TAGS")
        if not path:
            self.skipTest("explicit original003 tag input is required")
        expected = hashlib.sha256(Path(path).read_bytes()).hexdigest()
        configuration = replay_manifest.runtime_configuration(quanta_us=16000, run_options={})
        self.assertEqual(configuration["ow174.game.projectile_tags.CATALOG_SHA256"], expected)
        self.assertTrue(projectile_tags.absorbable(0x04000000000003D4))
        self.assertTrue(projectile_tags.absorbable(0x040000000000072C))
        self.assertFalse(projectile_tags.absorbable(0x04000000000008BE))


class SoldierAbsorbTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.environ.get("ORPHEUS_PROJECTILE_TAGS"):
            raise unittest.SkipTest("explicit original003 tag input is required; absorption stays OPEN")

    def scene(self, *, facing=-32768, active=True, distance=6):
        run = Run(names=("Soldier", "Sigma"))
        soldier, sigma = run.players
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        run.match.collision_world = flat_world(0)
        run.match.switch_hero(sigma, heroes()[sigma_grasp.SIGMA])
        soldier.mover.position[:] = [0, 0, 0]
        sigma.mover.position[:] = [0, 0, distance]
        sigma.team = 1 - soldier.team
        for player in run.players:
            player.mover.body = None
            player.body_script.ult_seconds = 0
        for _ in range(20):
            self.step(run, soldier, sigma, facing=facing)
        if active:
            self.activate(run, soldier, sigma, facing=facing)
        return run, soldier, sigma

    def activate(self, run, soldier, sigma, *, facing=-32768):
        self.step(run, soldier, sigma, sigma_buttons=8, facing=facing)
        for _ in range(4):
            self.step(run, soldier, sigma, facing=facing)
        self.assertTrue(self.state(sigma).active)

    def step(self, run, soldier, sigma, buttons=0, *, facing=-32768, sigma_buttons=0, angles=(0, 0)):
        run.step({soldier: (*angles, buttons), sigma: (facing, 0, sigma_buttons)})

    def instance(self, sigma):
        return next(i for i in sigma.body_script.component.instances.values() if i.graph.index == 0x1D02)

    def state(self, sigma):
        return self.instance(sigma).states[1]

    def pool(self, run, owner):
        return run.match.combat.vitals_of(next(t for t in run.match.combat._targets() if t.owner is owner))

    def headshot(self, run, soldier, sigma):
        # Preserve intentional40 damage by aiming at the authored camera level,
        # rather than relying on the old generic1.55 horizontal-ray head zone.
        angles = aim(run.match.combat._eye(soldier), run.match.combat._eye(sigma))
        self.step(run, soldier, sigma, 1, angles=angles)

    def finish(self, run, soldier, sigma):
        for _ in range(160):
            self.step(run, soldier, sigma)
            if not self.state(sigma).active:
                self.step(run, soldier, sigma)  # graph conversion reached after the prior world update
                return
        self.fail("authored two-second Grasp did not end")

    def fire_helix(self, run, soldier, sigma, **kwargs):
        self.step(run, soldier, sigma, 2, **kwargs)
        rocket, = run.match.combat.rockets
        for _ in range(160):
            self.step(run, soldier, sigma, **kwargs)
            if rocket not in run.match.combat.rockets:
                break
        self.assertNotIn(rocket, run.match.combat.rockets)
        return rocket

    def test_actual_field_canister_is_excluded_by_active_grasp(self):
        run, soldier, sigma = self.scene()
        soldier.mover.position[:] = [0, 0, 3.05]
        soldier.mover.body = None
        fight = run.match.combat
        pool = self.pool(run, soldier)
        pool.damage(50)
        before = pool.total
        state = self.state(sigma)
        self.assertEqual(int(state.node.field("m_filter.m_27CF71D5")), 2)
        tag = graph.guid(state.node.field("m_filter.m_8B2A4DF3.m_7D7C86A1"))
        self.assertEqual(tag, projectile_tags.EXCLUDED_TAG)
        self.step(run, soldier, sigma, 16)
        for _ in range(45):
            self.step(run, soldier, sigma)
            if fight.rockets:
                break
        canister, = fight.rockets
        field = canister.shot
        self.assertEqual(field.spawn, projectiles.FIELD_ENTITY)
        self.assertEqual(canister.definition, 0x040000000000072D)
        self.assertEqual(projectile_tags.tags_of(canister.definition), frozenset({tag}))
        ray = (canister.position, canister.direction, 20)
        contact = fight.grasp.trace(soldier, 0x040000000000072C, *ray)
        self.assertIsNotNone(contact)
        self.assertIs(contact[1].owner, sigma)
        self.assertIsNone(fight.grasp.trace(soldier, canister.definition, *ray))
        for _ in range(55):
            self.step(run, soldier, sigma)
            if field.field_tick is not None and run.frame > field.field_tick:
                break
        else:
            self.fail("Field did not open and begin healing")
        self.assertTrue(state.active)
        self.assertAlmostEqual(pool.total, before + 35 * combat.TICK)
        self.assertEqual(self.instance(sigma).find_var(0, 21312).value(), 0)
        self.finish(run, soldier, sigma)
        self.assertEqual(self.pool(run, sigma).total, 400)

    def test_rifle_and_visor_check_grasp_at_the_viewed_pose(self):
        for visor in (False, True):
            for viewed, current in ((0, 30), (30, 0)):
                with self.subTest(visor=visor, viewed=viewed, current=current):
                    run, soldier, sigma = self.scene(active=False)
                    if visor:
                        soldier.body_script.set_ult_charge(soldier.body_script.component.vars[338].value())
                        self.step(run, soldier, sigma, 0x20)
                        for _ in range(80):
                            self.step(run, soldier, sigma)
                    sigma.mover.position[0] = viewed
                    sigma.mover.body = None
                    self.activate(run, soldier, sigma)
                    for _ in range(12):
                        self.step(run, soldier, sigma)
                    sigma.mover.position[0] = current
                    sigma.mover.body = None
                    run.frame += 1
                    run.match.update(run.frame * combat.TICK, run.frame)
                    soldier.take_commands([Command(run.frame, buttons=1, extra=128)])
                    absorbed = self.instance(sigma).find_var(0, 21312).value()
                    self.assertEqual(absorbed, 20 if viewed == 0 else 0)
                    self.assertEqual(self.pool(run, sigma).total, 400)
                    self.assertFalse(soldier.client.messages(combat.HIT))

    def test_grasp_and_deflect_settle_only_the_first_rifle_or_helix_contact(self):
        for first in ("grasp", "deflect"):
            for button, amount in ((1, 20), (2, 120)):
                with self.subTest(first=first, button=button):
                    run, soldier, sigma = self.scene(distance=4 if first == "grasp" else 8)
                    genji = run.match.add_player(3, "Genji", deflection.GENJI, sigma.team, False)
                    in_world(run.match, genji)
                    run.players.append(genji)
                    run.match.switch_hero(genji, heroes()[deflection.GENJI])
                    genji.mover.position = [0, 0, 8 if first == "grasp" else 4]
                    genji.mover.body = None
                    genji.body_script.ult_seconds = 0
                    for _ in range(15):
                        run.step({sigma: (-32768, 0, 0), genji: (-32768, 0, 0)})
                    run.step({sigma: (-32768, 0, 0), genji: (-32768, 0, 16)})
                    for _ in range(5):
                        run.step({sigma: (-32768, 0, 0), genji: (-32768, 0, 0)})
                    fight = run.match.combat
                    target = next(t for t in fight._targets() if t.owner is genji)
                    self.assertIsNotNone(deflection.state_of(target))
                    self.assertTrue(self.state(sigma).active)
                    run.step({soldier: (0, 0, button), sigma: (-32768, 0, 0), genji: (-32768, 0, 0)})
                    for _ in range(25):
                        run.step({sigma: (-32768, 0, 0), genji: (-32768, 0, 0)})
                    self.assertFalse(fight.rockets)
                    absorbed = self.instance(sigma).find_var(0, 21312).value()
                    self.assertEqual(absorbed, amount if first == "grasp" else 0)
                    self.assertEqual(self.pool(run, sigma).total, 400)
                    self.assertEqual(self.pool(run, genji).total, 200)
                    loss = 200 - self.pool(run, soldier).total
                    if first == "grasp":
                        self.assertEqual(loss, 0)
                    else:
                        self.assertGreater(loss, 0)
                    self.assertAlmostEqual(genji.stats.value(stats.HERO_DAMAGE), loss)
                    self.assertFalse(soldier.client.messages(combat.HIT))

    def test_rifle_helix_and_visor_feed_authored_sum_then_convert_at_actual_end(self):
        for weapon, amount in (("rifle", 20), ("helix", 120), ("visor", 20)):
            with self.subTest(weapon=weapon):
                run, soldier, sigma = self.scene(active=weapon != "visor")
                if weapon == "visor":
                    soldier.body_script.set_ult_charge(soldier.body_script.component.vars[338].value())
                    self.step(run, soldier, sigma, 0x20)
                    for _ in range(80):
                        self.step(run, soldier, sigma)
                    self.activate(run, soldier, sigma)
                if weapon == "helix":
                    rocket = self.fire_helix(run, soldier, sigma)
                    self.assertEqual(rocket.definition, 0x040000000000072C)
                    self.assertIsNone(rocket.shot)
                else:
                    self.step(run, soldier, sigma, 1)
                self.assertEqual(self.instance(sigma).find_var(0, 21312).value(), amount)
                self.assertEqual(self.pool(run, sigma).total, 400)
                self.assertFalse(soldier.client.messages(combat.HIT))
                self.assertEqual(soldier.stats.value(stats.HERO_DAMAGE), 0)
                self.assertEqual(soldier.body_script.component.entity_var(342).value(), 0)
                self.assertEqual(self.pool(run, sigma).bonus_shields, 0)
                self.finish(run, soldier, sigma)
                self.assertAlmostEqual(self.pool(run, sigma).bonus_shields, amount * .6, places=4)
                self.assertAlmostEqual(self.pool(run, sigma).total, 400 + amount * .6, places=4)
                self.assertEqual(self.pool(run, soldier).total, 200)

    def test_inactive_rear_and_ally_do_not_fabricate_absorption(self):
        for case in ("inactive", "rear", "ally"):
            for weapon in ("rifle", "helix"):
                with self.subTest(case=case, weapon=weapon):
                    facing = 0 if case == "rear" else -32768
                    run, soldier, sigma = self.scene(facing=facing, active=case != "inactive")
                    if case == "ally":
                        sigma.team = soldier.team
                    if weapon == "helix":
                        self.fire_helix(run, soldier, sigma, facing=facing)
                    else:
                        self.step(run, soldier, sigma, 1, facing=facing,
                                  angles=aim(run.match.combat._eye(soldier), (0, .8, 6)))
                    expected = 0 if case == "ally" else 120 if weapon == "helix" else 20
                    self.assertEqual(self.pool(run, sigma).total, 400 - expected)
                    self.assertEqual(self.instance(sigma).find_var(0, 21312).value(),
                                     None if case == "inactive" else 0)
                    self.assertEqual(self.pool(run, sigma).bonus_shields, 0)

    def test_earlier_static_wall_prevents_grasp_sum_and_later_conversion(self):
        for weapon in ("rifle", "helix"):
            with self.subTest(weapon=weapon):
                run, soldier, sigma = self.scene()
                run.match.collision_world = World([
                    (-20, 0, -20, -20, 0, 20, 20, 0, 20), (-20, 0, -20, 20, 0, 20, 20, 0, -20),
                    (-10, -10, 2, 10, -10, 2, 10, 10, 2), (-10, -10, 2, 10, 10, 2, -10, 10, 2),
                ])
                if weapon == "helix":
                    self.fire_helix(run, soldier, sigma)
                else:
                    self.step(run, soldier, sigma, 1)
                self.finish(run, soldier, sigma)
                self.assertEqual(self.instance(sigma).find_var(0, 21312).value(), 0)
                self.assertEqual(self.pool(run, sigma).total, 400)
                self.assertEqual(soldier.stats.value(stats.HERO_DAMAGE), 0)

    def test_melee_and_unintercepted_splash_still_damage_live_grasp(self):
        run, soldier, sigma = self.scene(distance=1.5)
        self.step(run, soldier, sigma, 0x800)
        for _ in range(40):
            self.step(run, soldier, sigma)
        self.assertTrue(self.state(sigma).active)
        self.assertEqual(self.pool(run, sigma).total, 370)
        self.assertEqual(self.instance(sigma).find_var(0, 21312).value(), 0)
        run, soldier, sigma = self.scene()
        run.match.collision_world = World([
            (-20, 0, -20, -20, 0, 20, 20, 0, 20), (-20, 0, -20, 20, 0, 20, 20, 0, -20),
            (-10, -10, 7, 10, -10, 7, 10, 10, 7), (-10, -10, 7, 10, 10, 7, -10, 10, 7),
        ])
        angles = aim(run.match.combat._eye(soldier), (3, 1.55, 7))
        self.fire_helix(run, soldier, sigma, angles=angles)
        self.assertLess(self.pool(run, sigma).total, 400)
        self.assertTrue(self.state(sigma).active)
        self.assertEqual(self.instance(sigma).find_var(0, 21312).value(), 0)

    def test_interception_destroys_confirmed_helix_copies_without_impact_release(self):
        run, soldier, sigma = self.scene(distance=15)
        self.step(run, soldier, sigma, 2)
        rocket, = run.match.combat.rockets
        old = rocket.shot
        for _ in range(30):
            self.step(run, soldier, sigma)
            if rocket not in run.match.combat.rockets:
                break
        self.assertTrue(old.created)
        self.assertNotIn(old, run.match.combat.projectiles.shots)
        for _ in range(3):
            self.step(run, soldier, sigma)
        retired = [item for item in soldier.client.entities
                   if item.entity in old.created and item.op == OP_DESTROY]
        self.assertEqual(len(retired), len(old.created))
        self.assertTrue(all(item.ch2 is None for item in retired))
        self.assertTrue(all(not delivery.explode for delivery in old.observers))
        self.assertEqual(self.pool(run, sigma).total, 400)

    def test_conversion_is_separate_pool_and_authored_wait_precedes_five_per_second_decay(self):
        run, soldier, sigma = self.scene()
        self.fire_helix(run, soldier, sigma)
        self.finish(run, soldier, sigma)
        pool = self.pool(run, sigma)
        extra = pool.component((SHIELDS_POOL,))[51][SHIELDS_POOL * POOLS + 1]
        self.assertEqual(struct.unpack("<IffI", extra[:16]), (4, 72, 72, SHIELDS_POOL))
        self.assertEqual(extra[17], 0)
        decay = self.instance(sigma).states[28]
        for _ in range(200):
            if decay.active:
                break
            self.assertAlmostEqual(pool.bonus_shields, 72, places=4)
            self.step(run, soldier, sigma)
        self.assertTrue(decay.active)
        self.step(run, soldier, sigma)  # synchronize the sub-frame activation boundary
        before = pool.bonus_shields
        for _ in range(60):
            self.step(run, soldier, sigma)
        self.assertAlmostEqual(before - pool.bonus_shields, 4.8, places=3)
        once = pool.bonus_shields
        run.match.combat.grasp.update()
        self.assertEqual(pool.bonus_shields, once)
        self.assertEqual(pool.base_total, 400)
        self.assertEqual(soldier.stats.value(stats.HERO_DAMAGE), 0)

    def test_real_field_heals_base_deficit_without_refilling_extra_shields_or_losing_credit(self):
        run, soldier, sigma = self.scene(active=False)
        self.headshot(run, soldier, sigma)
        self.assertEqual(self.pool(run, sigma).ult_healing_deficit, 40)
        self.activate(run, soldier, sigma)
        self.fire_helix(run, soldier, sigma)
        self.finish(run, soldier, sigma)
        pool = self.pool(run, sigma)
        self.assertEqual(pool.base_total, 360)
        self.assertAlmostEqual(pool.bonus_shields, 72, places=4)
        sigma.team = soldier.team
        sigma.mover.position[:] = [0, 0, 3]
        sigma.mover.body = None
        self.step(run, soldier, sigma, 16)
        for _ in range(60):
            self.step(run, soldier, sigma)
        healed = pool.base_total - 360
        self.assertGreater(healed, 0)
        self.assertLess(healed, 40)
        self.assertAlmostEqual(pool.bonus_shields, 72, places=4)
        self.assertAlmostEqual(pool.ult_healing_deficit, 40 - healed, places=4)
        self.assertAlmostEqual(soldier.stats.value(stats.HEALING), healed, places=4)
        self.assertAlmostEqual(soldier.body_script.component.entity_var(342).value(), 40 + healed, places=3)

    def test_damage_to_extra_pool_gives_no_healable_deficit_and_field_cannot_refill_it(self):
        run, soldier, sigma = self.scene()
        self.fire_helix(run, soldier, sigma)
        self.finish(run, soldier, sigma)
        self.headshot(run, soldier, sigma)
        pool = self.pool(run, sigma)
        self.assertAlmostEqual(pool.bonus_shields, 32, places=4)
        self.assertEqual(pool.base_total, 400)
        self.assertEqual(pool.ult_healing_deficit, 0)
        self.assertEqual(soldier.stats.value(stats.HERO_DAMAGE), 40)
        self.assertEqual(soldier.body_script.component.entity_var(342).value(), 40)
        sigma.team = soldier.team
        sigma.mover.position[:] = [0, 0, 3]
        sigma.mover.body = None
        self.step(run, soldier, sigma, 16)
        for _ in range(60):
            self.step(run, soldier, sigma)
        self.assertAlmostEqual(pool.bonus_shields, 32, places=4)
        self.assertEqual(soldier.stats.value(stats.HEALING), 0)
        self.assertEqual(soldier.body_script.component.entity_var(342).value(), 40)

    def test_actual_six_helix_payloads_hit_authored_four_hundred_cap(self):
        run = Run(names=(*(f"Soldier{i}" for i in range(6)), "Sigma"))
        *soldiers, sigma = run.players
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        run.match.collision_world = flat_world(0)
        run.match.switch_hero(sigma, heroes()[sigma_grasp.SIGMA])
        for player in run.players:
            player.mover.position[:] = [0, 0, 6 if player is sigma else 0]
            player.mover.body = None
            player.team = 1 if player is sigma else 0
            player.body_script.ult_seconds = 0
        for _ in range(20):
            run.step({sigma: (-32768, 0, 0)})
        run.step({sigma: (-32768, 0, 8)})
        for _ in range(4):
            run.step({sigma: (-32768, 0, 0)})
        run.step({**dict.fromkeys(soldiers, (0, 0, 2)), sigma: (-32768, 0, 0)})
        for _ in range(20):
            run.step({sigma: (-32768, 0, 0)})
        self.assertEqual(self.instance(sigma).find_var(0, 21312).value(), 720)
        self.finish(run, soldiers[0], sigma)
        self.assertEqual(self.pool(run, sigma).bonus_shields, 400)
        self.assertEqual(self.pool(run, sigma).total, 800)
        for soldier in soldiers:
            self.assertEqual(soldier.stats.value(stats.HERO_DAMAGE), 0)
            self.assertEqual(soldier.body_script.component.entity_var(342).value(), 0)

    def test_cast_death_or_body_replacement_cannot_convert_absorbed_damage_later(self):
        for replacement in (False, True):
            with self.subTest(replacement=replacement):
                run, soldier, sigma = self.scene()
                self.fire_helix(run, soldier, sigma)
                self.assertEqual(self.instance(sigma).find_var(0, 21312).value(), 120)
                old = sigma.body
                if replacement:
                    self.assertTrue(run.match.switch_hero(sigma, sigma.hero))
                    sigma.mover.position[:] = [0, 0, 6]
                    sigma.mover.body = None
                else:
                    # Terminal-state setup is explicit; ordinary incoming attacks
                    # and actual ability input are exercised in the other methods.
                    target = next(t for t in run.match.combat._targets() if t.owner is sigma)
                    run.match.combat._damage(soldier, target, 1000, False, None, run.frame)
                    self.assertTrue(self.pool(run, sigma).dead)
                for _ in range(160):
                    self.step(run, soldier, sigma)
                self.assertEqual(self.pool(run, sigma).bonus_shields, 0)
                if replacement:
                    self.assertNotIn(old, run.match.combat.vitals)
                    self.assertFalse(any(k[0] == old for k in run.match.combat.grasp.conversions))

    def test_expiry_rejects_early_restart_and_recovers_after_authored_cooldown(self):
        run, soldier, sigma = self.scene()
        self.fire_helix(run, soldier, sigma)
        self.finish(run, soldier, sigma)
        counter = self.state(sigma).counter
        self.step(run, soldier, sigma, sigma_buttons=8)
        for _ in range(5):
            self.step(run, soldier, sigma)
        self.assertEqual(self.state(sigma).counter, counter)
        self.step(run, soldier, sigma, 1)
        self.assertLess(self.pool(run, sigma).bonus_shields, 72)
        for _ in range(780):
            self.step(run, soldier, sigma)
            self.assertEqual(self.state(sigma).counter, counter)
        self.step(run, soldier, sigma, sigma_buttons=8)
        self.assertTrue(self.state(sigma).active)
        self.assertGreater(self.state(sigma).counter, counter)
        pool = self.pool(run, sigma)
        before_base, before_extra = pool.base_total, pool.bonus_shields
        self.step(run, soldier, sigma, 1)
        self.assertEqual(pool.base_total, before_base)
        self.assertAlmostEqual(before_extra - pool.bonus_shields, min(before_extra, .08), places=4)
        self.assertEqual(self.instance(sigma).find_var(0, 21312).value(), 20)

    def test_in_flight_helix_orders_grasp_expiry_without_sigma_commands(self):
        for offset, absorbed in ((105, True), (121, False)):
            for jump in (False, True):
                with self.subTest(offset=offset, jump=jump):
                    run, soldier, sigma = self.scene()
                    state = self.state(sigma)
                    elapsed = (state.now - state.start) // sigma.body_script.frame_ms
                    launch = run.frame - elapsed + offset
                    run.match.update(launch * combat.TICK, launch)
                    self.assertTrue(state.active)
                    soldier.take_commands([Command(launch, buttons=2)])
                    run.match.update((launch + 1) * combat.TICK, launch + 1)
                    rocket, = run.match.combat.rockets
                    self.assertEqual(rocket.last_tick, launch + 1)

                    previous = launch + 1
                    for end, bonus in ((launch + 45, 72), (launch + 370, 58.995)):
                        for tick in ((end,) if jump else range(previous + 1, end + 1)):
                            run.match.update(tick * combat.TICK, tick)
                        self.assertFalse(state.active)
                        self.assertNotIn(rocket, run.match.combat.rockets)
                        pool = self.pool(run, sigma)
                        self.assertEqual(pool.base_total, 400 if absorbed else 280)
                        self.assertAlmostEqual(pool.bonus_shields, bonus if absorbed else 0)
                        points = self.instance(sigma).find_var(0, 21312).value()
                        charge = soldier.body_script.component.entity_var(342).value()
                        self.assertEqual(points, 120 if absorbed else 0)
                        self.assertEqual(charge, 0 if absorbed else 120)
                        previous = end

    def test_explicit_zero_cap_and_decay_modifier_are_not_replaced_by_defaults(self):
        run, soldier, sigma = self.scene()
        self.instance(sigma).write((0, 11218), 0.0)
        self.fire_helix(run, soldier, sigma)
        self.finish(run, soldier, sigma)
        self.assertEqual(self.instance(sigma).find_var(0, 21312).value(), 120)
        self.assertEqual(self.pool(run, sigma).bonus_shields, 0)
        run, soldier, sigma = self.scene()
        self.fire_helix(run, soldier, sigma)
        self.finish(run, soldier, sigma)
        sigma.body_script.component.set(9527, 0.0)
        for _ in range(250):
            self.step(run, soldier, sigma)
        self.assertTrue(self.instance(sigma).states[28].active)
        self.assertAlmostEqual(self.pool(run, sigma).bonus_shields, 72, places=4)
        sigma.body_script.component.set(9527, 1.0)
        for _ in range(60):
            self.step(run, soldier, sigma)
        self.assertAlmostEqual(self.pool(run, sigma).bonus_shields, 67.2, places=3)

    def test_actual_rear_flashbang_interrupts_grasp_and_prevents_delayed_conversion(self):
        run, soldier, sigma = self.scene()
        self.fire_helix(run, soldier, sigma)
        self.assertEqual(self.instance(sigma).find_var(0, 21312).value(), 120)
        caster = run.match.add_player(3, "McCree", 0x02E0000000000042, 2, False)
        in_world(run.match, caster)
        run.players.append(caster)
        run.match.switch_hero(caster, heroes()[0x02E0000000000042])
        caster.team = soldier.team
        caster.mover.position[:] = [0, 0, 9]
        caster.mover.body = None
        caster.body_script.ult_seconds = 0
        angles = aim(run.match.combat._eye(caster), (0, 1, 6))
        for _ in range(4):
            run.step({sigma: (-32768, 0, 0), caster: (*angles, 0)})
        run.step({sigma: (-32768, 0, 0), caster: (*angles, 16)})
        for _ in range(50):
            if run.match.combat.is_stunned(sigma):
                break
            run.step({sigma: (-32768, 0, 0), caster: (*angles, 0)})
        self.assertTrue(run.match.combat.is_stunned(sigma))
        self.assertFalse(self.state(sigma).active)
        before = self.pool(run, sigma).total
        for _ in range(30):
            self.headshot(run, soldier, sigma)
            if self.pool(run, sigma).total < before:
                break
        self.assertEqual(self.pool(run, sigma).total, before - 40)
        for _ in range(160):
            self.step(run, soldier, sigma)
        self.assertEqual(self.pool(run, sigma).bonus_shields, 0)
        self.assertEqual(soldier.stats.value(stats.HERO_DAMAGE), 40)

    def test_conversion_occurs_in_actual_end_action_without_next_world_tick_or_repeat(self):
        run, soldier, sigma = self.scene()
        self.fire_helix(run, soldier, sigma)
        for _ in range(160):
            self.step(run, soldier, sigma)
            if not self.state(sigma).active:
                break
        self.assertFalse(self.state(sigma).active)
        self.assertAlmostEqual(self.pool(run, sigma).bonus_shields, 72, places=4)
        instance = self.instance(sigma)
        self.assertEqual(run.match.combat.grasp.conversions[(sigma.body, instance.id)],
                         instance.states[29].counter)
        run.match.combat.grasp.update()
        run.match.combat.grasp.update()
        self.assertAlmostEqual(self.pool(run, sigma).bonus_shields, 72, places=4)
        self.assertEqual(sigma.stats.value(stats.HERO_DAMAGE), 0)
        self.assertEqual(sigma.body_script.component.entity_var(342).value(), 0)
