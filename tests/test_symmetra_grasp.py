"""Live 157C orb and beam interactions with Sigma's authored Grasp."""

import os
import unittest
from dataclasses import replace
from struct import unpack
from unittest.mock import patch

from test_game_combat import aim, in_world
from test_symmetra_charge import charge, orb_after_pvp_respawn
from test_symmetra_combat import arena, health
from test_symmetra_turrets import check_sentry_crosses_live_defense

from ow174.game import combat, projectile_tags, sigma_grasp, stats, symmetra_turrets
from ow174.game.collision import World
from ow174.game.content import heroes
from ow174.game.script import expr


@unittest.skipIf(projectile_tags.CATALOG_SHA256 is None, "requires external projectile tags")
class SymmetraGraspTests(unittest.TestCase):
    def scene(self, facing=-32768):
        run, owner, sigma = arena()
        self.assertTrue(run.match.switch_hero(sigma, heroes()[sigma_grasp.SIGMA]))
        sigma.mover.position[:] = (0, 1, 6)
        owner.body_script.component.set(12918, 0)
        fight = run.match.combat

        def step(attack=0, defense=0, count=1):
            for _ in range(count):
                angles = aim(fight._eye(owner), fight._eye(sigma))
                run.step({owner: (*angles, attack), sigma: (facing, 0, defense)})

        step(count=20)
        instance = next(i for i in sigma.body_script.component.instances.values()
                        if i.graph.index == sigma_grasp.GRAPH)
        return run, owner, sigma, step, instance

    def launch(self, run, step, instance):
        step(2, count=90)
        step(2, 8)
        self.assertTrue(instance.state(sigma_grasp.STATE).active)
        step()
        [orb] = run.match.combat.rockets
        self.assertTrue(projectile_tags.absorbable(orb.definition))
        return orb

    @unittest.skipUnless(os.environ.get("ORPHEUS_EXTRA_GRAPH_BUNDLES"), "requires the summon graph shard")
    def test_live_grasp_does_not_consume_sentry_launch(self):
        run, owner, sigma, step, instance = self.scene()
        self.assertTrue(projectile_tags.has_tag(symmetra_turrets.BODY, projectile_tags.EXCLUDED_TAG))
        step(defense=8)
        check_sentry_crosses_live_defense(
            self, run, owner, sigma, step,
            lambda: instance.state(sigma_grasp.STATE) if instance.state(sigma_grasp.STATE).active else None,
        )
        self.assertFalse(instance.find_var(0, 21312).value())
        self.assertEqual(health(run, sigma).bonus_shields, 0)

    def test_absorbed_orb_enters_authored_sum_converts_then_decays(self):
        run, owner, sigma, step, instance = self.scene()
        fight = run.match.combat
        pool = health(run, sigma)
        orb = self.launch(run, step, instance)
        with patch.object(fight, "_damage", wraps=fight._damage) as damage:
            step(count=20)
        self.assertFalse(damage.call_args_list)
        self.assertNotIn(orb, fight.rockets)
        self.assertAlmostEqual(instance.find_var(0, 21312).value(), 120)
        self.assertEqual(pool.total, 400)
        self.assertFalse(owner.client.messages(combat.HIT))
        self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 0)
        self.assertEqual(owner.body_script.component.entity_var(342).value(), 0)
        self.assertEqual(pool.bonus_shields, 0)
        for _ in range(160):
            if not instance.state(sigma_grasp.STATE).active:
                break
            step()
        self.assertFalse(instance.state(sigma_grasp.STATE).active)
        self.assertAlmostEqual(pool.bonus_shields, 72, places=4)
        self.assertAlmostEqual(pool.total, 472, places=4)
        extra = pool.component({combat.SHIELDS_POOL})[51][combat.SHIELDS_POOL * combat.POOLS + 1]
        self.assertEqual(unpack("<IffI", extra[:16]), (4, 72, 72, combat.SHIELDS_POOL))

        base = dict(pool.now)
        step(1, count=5)
        self.assertEqual(pool.now, base)
        self.assertAlmostEqual(pool.bonus_shields, 72 - 5 * 60 * .016, places=4)
        step(count=120)
        self.assertAlmostEqual(pool.bonus_shields, 72 - 5 * 60 * .016, places=4)
        for _ in range(100):
            decay = next((s for s in instance.states.values() if s.node.pos == sigma_grasp.DECAY), None)
            if decay is not None and decay.active:
                break
            step()
        self.assertIsNotNone(decay)
        self.assertTrue(decay.active)
        before = pool.bonus_shields
        step(count=60)
        self.assertAlmostEqual(before - pool.bonus_shields, 5 * 60 * .016, places=4)
        before = pool.bonus_shields
        fight.grasp.update()
        self.assertEqual(pool.bonus_shields, before)
        step(count=800)
        self.assertEqual(pool.bonus_shields, 0)
        self.assertEqual(pool.now, base)
        self.assertEqual(health(run, sigma).total, 400)

    def test_simultaneous_orbs_convert_up_to_authored_shield_cap(self):
        for count, expected in ((5, 360), (7, 400)):
            with self.subTest(attackers=count):
                self.check_grasp_volley(count, expected)

    def check_grasp_volley(self, count, expected):
        run, owner, sigma, step, instance = self.scene()
        fight = run.match.combat
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        owner.team, sigma.team = 1, 2
        attackers = [owner]
        hero = owner.hero.guid
        for slot in range(2, count + 1):
            attacker = run.match.add_player(slot + 1, f"Symmetra {slot}", hero, slot, False)
            in_world(run.match, attacker)
            run.players.append(attacker)
            self.assertTrue(run.match.switch_hero(attacker, heroes()[hero]))
            attacker.team = owner.team
            attacker.mover.position[:] = ((slot - 4) * .15, 1, 0)
            attacker.body_script.component.set(12918, 0)
            attackers.append(attacker)
        pool = health(run, sigma)
        base = pool.now.copy()
        conversion = instance.graph.node(sigma_grasp.CONVERSION)
        params = {expr.guid_of(p["m_B5051BCE"]): p["m_value"] for p in conversion.field("m_params")}
        self.assertEqual(expr.to_float(instance.evaluate(params[0x0A400000000003D1])), 400)

        def volley(attack=0, defense=0):
            commands = {attacker: (*aim(fight._eye(attacker), fight._eye(sigma)), attack)
                        for attacker in attackers}
            commands[sigma] = (-32768, 0, defense)
            run.step(commands)

        for _ in range(90):
            volley(2)
        volley(2, 8)
        self.assertTrue(instance.state(sigma_grasp.STATE).active)
        volley()
        orbs = tuple(fight.rockets)
        self.assertEqual(len(orbs), count)
        self.assertEqual({orb.shooter for orb in orbs}, set(attackers))
        self.assertTrue(all(projectile_tags.absorbable(orb.definition) for orb in orbs))
        absorbed = []
        absorb = fight.grasp.absorb

        def observe_absorption(state, shooter, direct, blast, distance):
            absorb(state, shooter, direct, blast, distance)
            absorbed.append((fight.tick, shooter, instance.find_var(0, 21312).value()))

        with (
            patch.object(fight, "_damage", wraps=fight._damage) as damage,
            patch.object(fight.grasp, "absorb", side_effect=observe_absorption),
        ):
            for _ in range(25):
                if not fight.rockets:
                    break
                volley()
        damage.assert_not_called()
        self.assertFalse(fight.rockets)
        self.assertTrue(instance.state(sigma_grasp.STATE).active)
        self.assertEqual(len({tick for tick, _, _ in absorbed}), 1)
        self.assertCountEqual([shooter for _, shooter, _ in absorbed], attackers)
        self.assertEqual([amount for _, _, amount in absorbed], [120 * n for n in range(1, count + 1)])
        self.assertEqual(pool.now, base)
        self.assertEqual(pool.bonus_shields, 0)
        for attacker in attackers:
            self.assertEqual(attacker.stats.value(stats.HERO_DAMAGE), 0)
            self.assertFalse(attacker.client.messages(combat.HIT))

        for _ in range(160):
            if not instance.state(sigma_grasp.STATE).active:
                break
            volley()
        self.assertFalse(instance.state(sigma_grasp.STATE).active)
        self.assertEqual(pool.bonus_shields, expected)
        self.assertEqual(pool.bonus_shields_max, expected)
        self.assertEqual(pool.now, base)
        extra = pool.component({combat.SHIELDS_POOL})[51][combat.SHIELDS_POOL * combat.POOLS + 1]
        self.assertEqual(unpack("<IffI", extra[:16]), (4, expected, expected, combat.SHIELDS_POOL))

        step(1, count=5)
        self.assertEqual(pool.now, base)
        self.assertAlmostEqual(pool.bonus_shields, expected - 5 * 60 * .016, places=4)
        self.assertEqual(pool.bonus_shields_max, expected)

        self.check_grasp_recast(run, owner, step, instance, pool, base, expected)

    def check_grasp_recast(self, run, owner, step, instance, pool, base, first_max):
        fight = run.match.combat
        state = instance.state(sigma_grasp.STATE)
        ability = instance.state(0)
        damage_before = owner.stats.value(stats.HERO_DAMAGE)
        hits_before = owner.client.messages(combat.HIT)
        self.assertGreater(ability.remaining(), 0)
        step(defense=8)
        self.assertFalse(state.active)
        for _ in range(500):
            step()
            step(defense=8)
            if state.active:
                break
        self.assertTrue(state.active)
        self.assertEqual(ability.remaining(), 0)
        self.assertGreater(pool.bonus_shields, 0)
        self.assertLess(pool.bonus_shields, first_max - 5 * 60 * .016)
        self.assertFalse(instance.find_var(0, 21312).value())

        step(2, count=90)
        step()
        [orb] = fight.rockets
        self.assertIs(orb.shooter, owner)
        self.assertTrue(projectile_tags.absorbable(orb.definition))
        with patch.object(fight, "_damage", wraps=fight._damage) as damage:
            for _ in range(25):
                if orb not in fight.rockets:
                    break
                step()
        damage.assert_not_called()
        self.assertNotIn(orb, fight.rockets)
        self.assertTrue(state.active)
        self.assertEqual(instance.find_var(0, 21312).value(), 120)

        for _ in range(125):
            before = pool.bonus_shields
            step()
            if not state.active:
                break
        self.assertFalse(state.active)
        self.assertGreater(before, 0)
        # The old decay advances once before conversion on the exit tick.
        self.assertAlmostEqual(pool.bonus_shields, min(400, before - 5 * combat.TICK + 72), places=4)
        self.assertAlmostEqual(pool.bonus_shields, 372.24 if first_max == 360 else 400, places=4)
        self.assertEqual(pool.bonus_shields_max, max(first_max, pool.bonus_shields))
        self.assertEqual(pool.now, base)
        self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), damage_before)
        self.assertEqual(owner.client.messages(combat.HIT), hits_before)

    def test_rear_flashbang_interrupts_recast_with_decaying_bonus_shields(self):
        run, owner, sigma, step, instance = self.scene()
        fight = run.match.combat
        pool = health(run, sigma)
        base = pool.now.copy()
        first = self.launch(run, step, instance)
        state = instance.state(sigma_grasp.STATE)
        step(2, count=90)
        step()
        [second] = fight.rockets
        self.assertIsNot(second, first)
        self.assertIs(second.shooter, owner)
        step(count=20)
        self.assertFalse(fight.rockets)
        self.assertTrue(state.active)
        self.assertEqual(instance.find_var(0, 21312).value(), 240)
        for _ in range(125):
            if not state.active:
                break
            step()
        self.assertFalse(state.active)
        self.assertAlmostEqual(pool.bonus_shields, 144)

        ability = instance.state(0)
        self.assertGreater(ability.remaining(), 0)
        for _ in range(900):
            if not ability.remaining():
                break
            step()
        self.assertEqual(ability.remaining(), 0)
        self.assertLess(pool.bonus_shields, 144)
        self.assertGreater(pool.bonus_shields, 25)
        caster, angles = self.rear_flashbang_caster(run)

        step(2, count=90)
        step(2, 8)
        self.assertTrue(state.active)
        self.assertFalse(instance.find_var(0, 21312).value())
        step()
        [orb] = fight.rockets
        self.assertIs(orb.shooter, owner)
        step(count=20)
        self.assertNotIn(orb, fight.rockets)
        self.assertTrue(state.active)
        self.assertEqual(instance.find_var(0, 21312).value(), 120)
        self.assertEqual(pool.now, base)
        self.assertFalse(owner.client.messages(combat.HIT))
        self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 0)

        before = pool.bonus_shields
        start = run.frame
        with patch.object(fight.grasp, "play_script", wraps=fight.grasp.play_script) as play:
            run.step({sigma: (-32768, 0, 0), caster: (*angles, 16)})
            [flashbang] = fight.rockets
            self.assertIs(flashbang.shooter, caster)
            for _ in range(20):
                if fight.is_stunned(sigma):
                    break
                step()
            self.assertNotIn(flashbang, fight.rockets)
            self.assertTrue(fight.is_stunned(sigma))
            self.assertFalse(state.active)
            self.assertAlmostEqual(pool.bonus_shields, before - 25 - (run.frame - start) * 5 * combat.TICK)
            self.assertEqual(pool.bonus_shields_max, 144)
            self.assertEqual(pool.now, base)
            self.assertAlmostEqual(caster.stats.value(stats.HERO_DAMAGE), 25)
            after = pool.bonus_shields
            step(count=125)
            self.assertFalse(any(call.args[1] is instance and call.args[2].pos == sigma_grasp.CONVERSION
                                 for call in play.call_args_list))
        self.assertAlmostEqual(pool.bonus_shields, after - 125 * 5 * combat.TICK)
        self.assertEqual(pool.bonus_shields_max, 144)
        self.assertEqual(pool.now, base)

    def test_lethal_rear_orbs_end_recast_and_new_body_starts_with_clean_grasp(self):
        run, owner, sigma, step, instance = self.scene()
        fight = run.match.combat
        pool = health(run, sigma)
        base = pool.now.copy()
        old_body = sigma.body
        sigma.spawn = ((0, 1, 6), 180)
        first = self.launch(run, step, instance)
        state = instance.state(sigma_grasp.STATE)
        ability = instance.state(0)
        for _ in range(160):
            if not state.active:
                break
            step()
        self.assertFalse(state.active)
        self.assertNotIn(first, fight.rockets)
        self.assertEqual(pool.bonus_shields, 72)
        self.assertGreater(ability.remaining(), 0)
        for _ in range(900):
            if not ability.remaining():
                break
            step()
        self.assertEqual(ability.remaining(), 0)

        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        owner.team, sigma.team = 1, 2
        rear = []
        for slot in range(2, 6):
            attacker = run.match.add_player(slot + 1, f"Rear Symmetra {slot}", owner.hero.guid, slot, False)
            in_world(run.match, attacker)
            run.players.append(attacker)
            self.assertTrue(run.match.switch_hero(attacker, owner.hero))
            attacker.team = owner.team
            attacker.mover.position[:] = ((slot - 3.5) * .15, 1, 9)
            attacker.body_script.component.set(12918, 0)
            rear.append(attacker)

        def attack(front=0, back=2, defense=0):
            commands = {p: (*aim(fight._eye(p), fight._eye(sigma)), back) for p in rear}
            commands[owner] = (*aim(fight._eye(owner), fight._eye(sigma)), front)
            commands[sigma] = (-32768, 0, defense)
            run.step(commands)

        for _ in range(90):
            attack(2)
        attack(2, defense=8)
        self.assertTrue(state.active)
        self.assertFalse(instance.find_var(0, 21312).value())
        attack()
        [front_orb] = fight.rockets
        self.assertIs(front_orb.shooter, owner)
        for _ in range(20):
            if not fight.rockets:
                break
            attack()
        self.assertFalse(fight.rockets)
        self.assertEqual(instance.find_var(0, 21312).value(), 120)
        self.assertTrue(state.active)
        self.assertEqual(pool.now, base)
        self.assertGreater(pool.bonus_shields, 0)
        self.assertFalse(owner.client.messages(combat.HIT))
        self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 0)

        with patch.object(fight.grasp, "play_script", wraps=fight.grasp.play_script) as play:
            attack(back=0)
            self.assertCountEqual([orb.shooter for orb in fight.rockets], rear)
            for _ in range(25):
                if pool.dead:
                    break
                attack(back=0)
            self.assertTrue(pool.dead)
            self.assertEqual(pool.bonus_shields, 0)
            self.assertIn(pool.killer, {p.body for p in rear})
            self.assertTrue(any(p.client.messages(combat.KILL_NOTICE) for p in rear))
            self.assertIsNone(sigma_grasp.active(sigma))
            self.assertEqual(sigma.body, old_body)
            self.assertTrue(any(player is sigma for _, player in fight.respawns))
            for _ in range(900):
                if sigma.body != old_body:
                    break
                step()
            self.assertFalse(any(call.args[1] is instance and call.args[2].pos == sigma_grasp.CONVERSION
                                 for call in play.call_args_list))

        self.assertNotEqual(sigma.body, old_body)
        self.assertFalse(any(body == old_body for body, _ in fight.grasp.conversions))
        fresh = health(run, sigma)
        self.assertFalse(fresh.dead)
        self.assertEqual(fresh.now, base)
        self.assertEqual((fresh.bonus_shields, fresh.bonus_shields_max), (0, 0))
        step(count=20)
        self.assertAlmostEqual(sigma.mover.position[0], 0)
        self.assertAlmostEqual(sigma.mover.position[2], 6)
        new_instance = next(i for i in sigma.body_script.component.instances.values()
                            if i.graph.index == sigma_grasp.GRAPH)
        self.assertIsNot(new_instance, instance)
        orb = self.launch(run, step, new_instance)
        step(count=20)
        self.assertNotIn(orb, fight.rockets)
        self.assertEqual(new_instance.find_var(0, 21312).value(), 120)
        step(count=110)
        self.assertIsNone(sigma_grasp.active(sigma))
        self.assertEqual((fresh.bonus_shields, fresh.bonus_shields_max), (72, 72))
        self.assertEqual(fresh.now, base)

    def test_grasp_accumulates_charged_orb_and_flashbang_in_arrival_order(self):
        for flash_frame in (6, 7, 8):
            with self.subTest(flash_frame=flash_frame):
                self.check_orb_and_flashbang(flash_frame, protected=True)
        self.check_orb_and_flashbang(7, protected=False)

    def check_orb_and_flashbang(self, flash_frame, *, protected):
        run, owner, sigma, step, instance = self.scene()
        fight = run.match.combat
        caster, _ = self.rear_flashbang_caster(run)
        caster.mover.position[:] = (.4, 1, 2)
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        owner.team = caster.team = 1
        sigma.team = 2
        pool = health(run, sigma)
        base = pool.now.copy()
        state = instance.state(sigma_grasp.STATE)
        step(2, count=90)
        step(2, 8 if protected else 0)
        self.assertEqual(state.active, protected)
        start = run.frame
        launches = {}
        contacts = []
        absorb = fight.grasp.absorb

        def observe(state, shooter, direct, blast, distance):
            absorb(state, shooter, direct, blast, distance)
            contacts.append((fight.tick - start, shooter, direct.amount, blast.amount,
                             instance.find_var(0, 21312).value()))

        with (
            patch.object(fight.grasp, "absorb", side_effect=observe),
            patch.object(fight, "_damage", wraps=fight._damage) as damage,
        ):
            for frame in range(1, 27):
                run.step({
                    owner: (*aim(fight._eye(owner), fight._eye(sigma)), 0),
                    caster: (*aim(fight._eye(caster), (0, 2, 6)), 16 if frame == flash_frame else 0),
                    sigma: (-32768, 0, 0),
                })
                for rocket in fight.rockets:
                    launches.setdefault(rocket.shooter, (frame, rocket.definition))
        self.assertEqual(launches, {owner: (1, 0x04000000000026F1),
                                    caster: (flash_frame, 0x040000000000037D)})
        self.assertTrue(all(projectile_tags.absorbable(definition) for _, definition in launches.values()))
        self.assertFalse(fight.rockets)
        self.assertEqual(pool.bonus_shields, 0)

        if not protected:
            self.assertEqual(contacts, [])
            self.assertTrue(damage.called)
            self.assertEqual(pool.total, 255)
            self.assertTrue(fight.is_stunned(sigma))
            return

        damage.assert_not_called()
        orb = (10, owner, 60, 60)
        flash = (flash_frame + 3, caster, 0, 25)
        expected = [(*flash, 25), (*orb, 145)] if flash_frame == 6 else [(*orb, 120), (*flash, 145)]
        self.assertEqual(contacts, expected)
        self.assertFalse(fight.is_stunned(sigma))
        self.assertTrue(state.active)
        self.assertEqual(pool.now, base)
        for attacker in (owner, caster):
            self.assertEqual(attacker.stats.value(stats.HERO_DAMAGE), 0)
            self.assertFalse(attacker.client.messages(combat.HIT))
        for _ in range(125):
            if not state.active:
                break
            step()
        self.assertFalse(state.active)
        self.assertAlmostEqual(pool.bonus_shields, 87, places=4)
        self.assertAlmostEqual(pool.bonus_shields_max, 87, places=4)
        self.assertEqual(pool.now, base)

    def test_grasp_converts_the_orb_in_flight_at_natural_owner_respawn(self):
        run, owner, sigma, orb = orb_after_pvp_respawn(self, heroes()[sigma_grasp.SIGMA])
        fight = run.match.combat
        pool = health(run, sigma)
        instance = next(i for i in sigma.body_script.component.instances.values()
                        if i.graph.index == sigma_grasp.GRAPH)
        initial_charge = charge(owner)
        self.assertTrue(projectile_tags.absorbable(orb.definition))
        run.step({sigma: (-32768, 0, 8)})
        self.assertTrue(instance.state(sigma_grasp.STATE).active)
        with patch.object(fight, "_damage", wraps=fight._damage) as damage:
            for _ in range(55):
                if orb not in fight.rockets:
                    break
                run.step({sigma: (-32768, 0, 0)})
        damage.assert_not_called()
        self.assertNotIn(orb, fight.rockets)
        self.assertTrue(instance.state(sigma_grasp.STATE).active)
        self.assertEqual(instance.find_var(0, 21312).value(), 120)
        self.assertEqual(pool.total, 400)
        self.assertEqual(pool.bonus_shields, 0)
        self.assertEqual(charge(owner), initial_charge)
        self.assertEqual(owner.stats.value(stats.HERO_DAMAGE), 0)
        self.assertFalse(owner.client.messages(combat.HIT))
        for _ in range(160):
            if not instance.state(sigma_grasp.STATE).active:
                break
            run.step({})
        self.assertFalse(instance.state(sigma_grasp.STATE).active)
        self.assertEqual(pool.bonus_shields, 72)
        self.assertEqual(pool.total, 472)

    def test_actual_orb_hits_shields_after_prior_retained_decay_ticks(self):
        stepped = self.orb_after_grasp_decay(gap=False)
        self.assertEqual(self.orb_after_grasp_decay(gap=True), stepped)

    def orb_after_grasp_decay(self, *, gap):
        run, owner, sigma, step, instance = self.scene()
        fight = run.match.combat
        first = self.launch(run, step, instance)
        pool = health(run, sigma)
        step(count=218)
        self.assertNotIn(first, fight.rockets)
        self.assertAlmostEqual(pool.bonus_shields, 72, places=4)
        step(2, count=90)
        decay_index = instance.graph.node(sigma_grasp.DECAY).state
        for _ in range(10):
            decay = instance.states.get(decay_index)
            if decay is not None and decay.active:
                break
            step(2)
        self.assertTrue(instance.states[decay_index].active)
        step()
        step()
        [orb] = fight.rockets
        self.assertIs(orb.shooter, owner)
        self.assertEqual(orb.last_tick, run.frame)
        self.assertIsNone(sigma_grasp.active(sigma))
        start = run.frame
        starting_bonus = pool.bonus_shields
        self.assertGreater(starting_bonus, 70)
        trace = []
        resolve = fight._damage

        def observe(shooter, target, amount, *args, **kwargs):
            if shooter is owner and target.owner is sigma:
                trace.append((fight.tick - start, amount, round(pool.bonus_shields, 4)))
            return resolve(shooter, target, amount, *args, **kwargs)

        with patch.object(fight, "_damage", side_effect=observe):
            if gap:
                run.frame += 25
                run.match.update(run.frame * combat.TICK, run.frame)
            else:
                step(count=25)
        self.assertNotIn(orb, fight.rockets)
        [(hit_tick, direct, bonus_at_hit), (splash_tick, splash, _)] = trace
        self.assertEqual((direct, splash, splash_tick), (60, 60, hit_tick))
        self.assertEqual(hit_tick, 13)
        expected_bonus = starting_bonus - (hit_tick - 1) * .08
        self.assertAlmostEqual(bonus_at_hit, expected_bonus, places=4)
        self.assertEqual(pool.bonus_shields, 0)
        self.assertAlmostEqual(pool.total, 400 - 120 + expected_bonus, places=4)
        return trace

    def test_live_grasp_does_not_absorb_beam(self):
        run, _owner, sigma, step, instance = self.scene()
        step(defense=8)
        self.assertTrue(instance.state(sigma_grasp.STATE).active)
        step(1, count=5)
        self.assertAlmostEqual(health(run, sigma).total, 400 - 5 * 60 * .016, places=4)
        self.assertFalse(instance.find_var(0, 21312).value())

    def test_rear_orb_hits_and_earlier_wall_prevents_conversion(self):
        for cover in (False, True):
            with self.subTest(cover=cover):
                run, _owner, sigma, step, instance = self.scene(-32768 if cover else 0)
                fight = run.match.combat
                self.launch(run, step, instance)
                if cover:
                    wall = World([(-5, 0, 2, 5, 0, 2, 5, 8, 2),
                                  (-5, 0, 2, 5, 8, 2, -5, 8, 2)])
                    with patch.object(fight, "_collision", return_value=wall):
                        step(count=25)
                else:
                    step(count=25)
                self.assertAlmostEqual(health(run, sigma).total, 400 if cover else 280, places=4)
                self.assertFalse(instance.find_var(0, 21312).value())
                step(count=140)
                self.assertEqual(health(run, sigma).bonus_shields, 0)

    def test_rear_flashbang_interrupts_and_prevents_conversion(self):
        run, _owner, sigma, step, instance = self.scene()
        fight = run.match.combat
        self.launch(run, step, instance)
        step(count=20)
        self.assertAlmostEqual(instance.find_var(0, 21312).value(), 120)
        caster, angles = self.rear_flashbang_caster(run)
        for frame in range(50):
            if fight.is_stunned(sigma):
                break
            run.step({sigma: (-32768, 0, 0), caster: (*angles, 16 if frame == 3 else 0)})
        self.assertTrue(fight.is_stunned(sigma))
        self.assertFalse(instance.state(sigma_grasp.STATE).active)
        pool = health(run, sigma)
        self.assertAlmostEqual(pool.total, 375)
        step(1)
        self.assertLess(pool.total, 375)
        step(count=160)
        self.assertEqual(pool.bonus_shields, 0)

    def rear_flashbang_caster(self, run):
        mccree = 0x02E0000000000042
        caster = run.match.add_player(3, "McCree", mccree, 2, False)
        in_world(run.match, caster)
        run.players.append(caster)
        self.assertTrue(run.match.switch_hero(caster, heroes()[mccree]))
        caster.mover.position[:] = (0, 1, 9)
        return caster, aim(run.match.combat._eye(caster), (0, 2, 6))

    def test_retained_flashbang_interrupts_grasp_or_spends_converted_shields(self):
        for before_expiry in (True, False):
            with self.subTest(before_expiry=before_expiry):
                stepped = self.flashbang_across_grasp_expiry(before_expiry, gap=False)
                self.assertEqual(self.flashbang_across_grasp_expiry(before_expiry, gap=True), stepped)

    def flashbang_across_grasp_expiry(self, before_expiry, *, gap):
        run, owner, sigma, step, instance = self.scene()
        self.launch(run, step, instance)
        state = instance.state(sigma_grasp.STATE)
        caster, angles = self.rear_flashbang_caster(run)
        launch = 115 if before_expiry else 125
        result = self.check_rear_flashbang_expiry(
            run, sigma, step, state, caster, angles, launch, before_expiry=before_expiry, gap=gap,
        )
        self.assertEqual(health(run, owner).total, 225)
        return result

    def check_rear_flashbang_expiry(self, run, sigma, step, state, caster, angles, launch, *,
                                    before_expiry, gap, converted=72):
        fight = run.match.combat
        cast_frame = state.start // round(combat.TICK * 1000)
        step(count=cast_frame + launch - run.frame)
        run.step({sigma: (-32768, 0, 0), caster: (*angles, 16)})
        step()
        [flashbang] = fight.rockets
        self.assertIs(flashbang.shooter, caster)
        self.assertEqual(flashbang.last_tick, run.frame)
        active_at_launch = launch < 125
        self.assertEqual(state.active, active_at_launch)
        pool = health(run, sigma)
        self.assertAlmostEqual(pool.bonus_shields, 0 if active_at_launch else converted)
        if gap:
            run.frame += 20
            run.match.update(run.frame * combat.TICK, run.frame)
        else:
            step(count=20)
        self.assertNotIn(flashbang, fight.rockets)
        self.assertTrue(fight.is_stunned(sigma))
        self.assertFalse(state.active)
        self.assertAlmostEqual(pool.bonus_shields, 0 if before_expiry else converted - 25)
        self.assertAlmostEqual(pool.total, 375 if before_expiry else 400 + converted - 25)
        hit_frame = round(pool.last_damage_at / combat.TICK) - cast_frame
        self.assertEqual(hit_frame, launch + 6)
        return hit_frame, fight.stuns[sigma.body] - cast_frame
