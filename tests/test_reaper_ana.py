"""Actual normal Ana grenade -> Reaper status/health/cleanse reconstruction."""

import unittest
from dataclasses import replace
from unittest.mock import patch

import test_game_reaper as reaper_tests
from test_game_combat import aim

from ow174.game import ana, combat, defenses, dva_matrix, pools, reaper, sigma_grasp, stats
from ow174.game.commands import Command
from ow174.game.content import SOLDIER, find_hero, heroes


class ReaperAnaTests(unittest.TestCase):
    def scene(self, *, friendly=False, hero=None, names=("Reaper", "Ana", "Enemy")):
        run, target, caster, _ = reaper_tests.ReaperTests().scenario(team_mode=True, names=names)
        run.match.switch_hero(caster, heroes()[ana.HERO])
        if hero is not None:
            run.match.switch_hero(target, heroes()[hero])
        target.team = 0
        caster.team = 0 if friendly else 1
        run.players[2].team = 1
        x, y, z = target.mover.position
        caster.mover.position[:] = [x, y, z + 1.5]
        run.players[2].mover.position[:] = [x + 15, y, z]
        run.players[2].mover.body = None
        caster.mover.body = None
        for _ in range(20):
            run.step({})
        fight = run.match.combat
        for item in fight._targets():
            fight.vitals_of(item)
            if item.owner.body_script is not None:
                item.owner.body_script.ult_seconds = 0
        return run, target, caster

    def health(self, run, actor):
        return run.match.combat.vitals[actor.body].total

    def hurt(self, run, actor, amount):
        fight = run.match.combat
        fight.vitals[actor.body].damage(amount)
        fight._sync_body_health(actor, fight.vitals[actor.body])

    def grenade(self, run, caster, target):
        x, y, z = target.mover.position
        yaw, pitch = aim(run.match.combat._eye(caster), (x, y, z))
        run.step({caster: (yaw, pitch, 16)})
        for _ in range(15):
            run.step({})

    def world(self, run, ticks):
        run.frame += ticks
        run.match.update(run.frame * combat.TICK, run.frame, frames=False)

    def shoot(self, run, target, victim):
        reaper_tests.ReaperTests().fire(run, target, victim)

    def test_enemy_grenade_uses_effect_graph_sixty_once_and_blocks_reaping(self):
        run, target, caster = self.scene()
        self.hurt(run, target, 100)
        self.grenade(run, caster, target)
        self.assertEqual(self.health(run, target), 90)
        record = run.match.combat.heal_modifiers[target.body]["anti"]
        self.assertAlmostEqual(record["end"] - record["start"], 4)
        self.shoot(run, target, caster)
        self.assertEqual(self.health(run, target), 90)
        self.assertEqual(self.health(run, caster), 80)
        self.assertEqual(target.stats.value(stats.HEALING), 0)
        self.assertEqual(target.body_script.component.vars[342].value(), 120)

    def deflect_scene(self, *, activate=True, **kwargs):
        run, target, caster = self.scene(friendly=True, **kwargs)
        fight = run.match.combat
        genji = run.players[2]
        self.assertTrue(run.match.switch_hero(genji, heroes()[0x02E0000000000029]))
        x, y, z = target.mover.position
        caster.mover.position[:] = (x + 2, y, z - 3)
        genji.mover.position[:] = (x, y, z + 5)
        caster.mover.body = genji.mover.body = None
        run.step({})
        guard = aim(fight._eye(genji), (x, y + 1, z))
        for frame in range(12):
            run.step({genji: (*guard, 16 if activate and frame == 0 else 0)})
        heading = aim(fight._eye(caster), (x, y + 1, z + 5))
        return run, target, caster, genji, guard, heading

    def test_actual_genji_reflects_allied_grenade_into_hostile_antiheal(self):
        run, target, caster, genji, guard, heading = self.deflect_scene()
        fight = run.match.combat
        self.hurt(run, target, 100)
        run.step({caster: (*heading, 16), genji: (*guard, 0)})
        original, = fight.rockets
        self.assertTrue(original.ana_grenade)
        self.assertIs(original.shooter, caster)
        for _ in range(30):
            run.step({genji: (*guard, 0)})
            if any(rocket.reflected for rocket in fight.rockets):
                break
        reflected, = fight.rockets
        self.assertTrue(reflected.reflected)
        self.assertTrue(reflected.ana_grenade)
        self.assertIs(reflected.shooter, genji)
        self.assertEqual(reflected.team, genji.team)
        for _ in range(30):
            run.step({genji: (*guard, 0)})
            if not fight.rockets:
                break
        self.assertFalse(fight.rockets)
        self.assertEqual(self.health(run, target), 90)
        modifiers = fight.heal_modifiers[target.body]
        self.assertNotIn("boost", modifiers)
        self.assertAlmostEqual(modifiers["anti"]["end"] - modifiers["anti"]["start"], 4)
        self.assertGreaterEqual(genji.stats.value(stats.HERO_DAMAGE), 60)
        self.assertEqual(caster.stats.value(stats.HERO_DAMAGE), 0)
        deflect = next(i for i in genji.body_script.component.instances.values() if i.graph.index == 0x03A3)
        for _ in range(150):
            if not deflect.states[10].active:
                break
            run.step({genji: (*guard, 0)})
        self.assertFalse(deflect.states[10].active)
        self.assertEqual(fight._healing_factor(target), 0)
        self.shoot(run, target, genji)
        self.assertLess(self.health(run, genji), 200)
        self.assertEqual(self.health(run, target), 90)
        self.assertEqual(target.stats.value(stats.HEALING), 0)

    def reflected_grenade_grasp_trace(self, *, active, skipped):
        run, target, caster, genji, guard, heading = self.deflect_scene(
            names=("Reaper", "Ana", "Genji", "Sigma"),
        )
        fight = run.match.combat
        sigma = run.players[3]
        self.assertTrue(run.match.switch_hero(sigma, heroes()[sigma_grasp.HERO]))
        sigma.team = target.team
        x, y, z = target.mover.position
        sigma.mover.position[:] = (x, y, z + 2)
        sigma.mover.body = None
        self.hurt(run, target, 100)
        run.step({
            caster: (*heading, 16), genji: (*guard, 0), sigma: (0, 0, 8 if active else 0),
        })
        original, = fight.rockets
        self.assertIs(original.shooter, caster)
        self.assertTrue(original.ana_grenade)
        for _ in range(30):
            run.step({genji: (*guard, 0)})
            if any(rocket.reflected for rocket in fight.rockets):
                break
        returned, = fight.rockets
        self.assertTrue(returned.reflected)
        self.assertIs(returned.shooter, genji)
        self.assertTrue(returned.ana_grenade)
        self.assertEqual(returned.definition, original.definition)
        self.assertEqual(returned.blast.amount, 50)
        self.assertEqual(sigma_grasp.active(sigma), active)
        grasp = next(instance for instance in sigma.body_script.component.instances.values()
                     if instance.graph.index == sigma_grasp.GRASP)
        impacts = []
        landed = fight._landed

        def observe(rocket):
            self.assertIs(rocket, returned)
            impacts.append(fight.tick)
            landed(rocket)

        frame = target.commands.last_frame
        end = run.frame + 30
        with patch.object(fight, "_landed", side_effect=observe):
            while run.frame < end:
                self.world(run, end - run.frame if skipped else 1)
        self.assertFalse(fight.rockets)
        self.assertEqual(len(impacts), 1)
        self.assertEqual(target.commands.last_frame, frame)
        self.assertEqual(self.health(run, target), 150 if active else 90)
        anti = fight.heal_modifiers.get(target.body, {}).get("anti")
        if active:
            self.assertEqual(grasp.vars[21312].value(), 50)
            self.assertIsNone(anti)
            self.assertEqual(self.health(run, sigma), 400)
            self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), 0)
        else:
            self.assertEqual(anti["source"], genji.body)
            self.assertAlmostEqual(anti["start"], impacts[0] * combat.TICK)
            self.assertAlmostEqual(anti["end"] - anti["start"], ana.DURATION)
            self.assertEqual(anti["factor"], ana.ANTI)
            self.assertEqual(self.health(run, sigma), 340)
            self.assertGreaterEqual(genji.stats.value(stats.HERO_DAMAGE), 120)
        self.assertEqual(caster.stats.value(stats.HERO_DAMAGE), 0)
        end += 130
        while run.frame < end:
            self.world(run, end - run.frame if skipped else 1)
        self.assertFalse(sigma_grasp.active(sigma))
        shield = fight.vitals[sigma.body].temporary_shields.get(sigma_grasp.SHIELD_TAG)
        if active:
            self.assertAlmostEqual(shield.now, 30, places=5)
        else:
            self.assertIsNone(shield)
        return impacts, self.health(run, target), self.health(run, sigma), shield.now if shield else None

    def test_actual_grasp_absorbs_reflected_biotic_grenade_before_antiheal(self):
        for active in (False, True):
            with self.subTest(active=active):
                stepped = self.reflected_grenade_grasp_trace(active=active, skipped=False)
                skipped = self.reflected_grenade_grasp_trace(active=active, skipped=True)
                self.assertEqual(stepped, skipped)

    def reflected_grenade_matrix_trace(self, *, active, skipped):
        run, target, caster, genji, guard, heading = self.deflect_scene(
            activate=False, names=("Reaper", "Ana", "Genji", "Dva"),
        )
        fight = run.match.combat
        mech = run.players[3]
        self.assertTrue(run.match.switch_hero(mech, replace(find_hero("dva"), body=pools.DVA_MECH)))
        mech.team = target.team
        x, y, z = target.mover.position
        mech.mover.position[:] = (x, y, z + 1.5)
        mech.mover.body = None
        for _ in range(100):
            run.step({})
        held = {mech: (0, 0, 2)} if active else {}
        for frame in range(12):
            run.step(held | {genji: (*guard, 16 if frame == 0 else 0)})
        self.hurt(run, target, 100)
        run.step(held | {caster: (*heading, 16), genji: (*guard, 0)})
        original, = fight.rockets
        self.assertTrue(original.ana_grenade)
        self.assertIs(original.shooter, caster)
        for _ in range(30):
            run.step(held | {genji: (*guard, 0)})
            if any(rocket.reflected for rocket in fight.rockets):
                break
        returned, = fight.rockets
        self.assertTrue(returned.ana_grenade)
        self.assertTrue(returned.reflected)
        self.assertIs(returned.shooter, genji)
        self.assertEqual(returned.definition, original.definition)
        self.assertTrue(dva_matrix.eligible(returned.definition))
        self.assertEqual(dva_matrix.active(mech), active)
        self.assertEqual(returned.last_tick, run.frame)

        contacts = []
        query = fight._projectile_defense_contact

        def observe(shooter, *args, **kwargs):
            hit = query(shooter, *args, **kwargs)
            if shooter is genji and hit is not None:
                self.assertIs(hit[1], mech)
                self.assertEqual(hit[2].instance.graph.index, dva_matrix.GRAPH)
                contacts.append(fight.tick)
            return hit

        expected = run.frame + 1
        frame = mech.commands.last_frame
        end = run.frame + 30
        with patch.object(fight, "_projectile_defense_contact", side_effect=observe):
            while run.frame < end:
                self.world(run, end - run.frame if skipped else 1)
        self.assertFalse(fight.rockets)
        self.assertEqual(mech.commands.last_frame, frame)
        self.assertEqual(contacts, [expected] if active else [])
        self.assertEqual(self.health(run, target), 150 if active else 90)
        anti = fight.heal_modifiers.get(target.body, {}).get("anti")
        hits = [event["+0x84"] for event in genji.client.messages(combat.HIT)]
        if active:
            self.assertIsNone(anti)
            self.assertEqual(self.health(run, mech), 600)
            self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), 0)
            self.assertEqual(hits, [])
        else:
            self.assertIsNotNone(anti)
            self.assertEqual(anti["source"], genji.body)
            self.assertAlmostEqual(anti["end"] - anti["start"], ana.DURATION)
            self.assertAlmostEqual(anti["start"], hits[0] * combat.TICK)
        self.assertEqual(caster.stats.value(stats.HERO_DAMAGE), 0)
        return contacts, hits, self.health(run, target), self.health(run, mech)

    def test_actual_matrix_absorbs_reflected_biotic_grenade_before_antiheal(self):
        from ow174.game.mover import DVA_MOVER_SHA256
        from ow174.game.script.graph import DVA_GRAPH_SHA256

        if not (DVA_MOVER_SHA256 and DVA_GRAPH_SHA256 and dva_matrix.TAG_CATALOG_SHA256):
            self.skipTest("requires the pinned D.Va supplements and projectile catalog")
        for active in (False, True):
            with self.subTest(active=active):
                stepped = self.reflected_grenade_matrix_trace(active=active, skipped=False)
                skipped = self.reflected_grenade_matrix_trace(active=active, skipped=True)
                self.assertEqual(stepped, skipped)

    def reflected_grenade_projection_trace(self, *, shield_health, skipped):
        run, target, caster, genji, guard, heading = self.deflect_scene(
            names=("Reaper", "Ana", "Genji", "Zarya"),
        )
        fight = run.match.combat
        zarya = run.players[3]
        self.assertTrue(run.match.switch_hero(zarya, find_hero("Zarya")))
        zarya.team = target.team
        x, y, z = target.mover.position
        zarya.mover.position[:] = (x + 5, y, z + 1)
        zarya.mover.body = None
        self.hurt(run, target, 100)
        run.step({caster: (*heading, 16), genji: (*guard, 0)})
        original, = fight.rockets
        self.assertIs(original.shooter, caster)
        for _ in range(30):
            run.step({genji: (*guard, 0)})
            if any(rocket.reflected for rocket in fight.rockets):
                break
        returned, = fight.rockets
        self.assertTrue(returned.reflected)
        self.assertTrue(returned.ana_grenade)
        self.assertIs(returned.shooter, genji)
        self.assertEqual(returned.definition, original.definition)
        self.assertEqual(returned.blast.amount, 50)

        projected = shield_health is not None
        if projected:
            heading = aim(fight._eye(zarya), fight._eye(target))
            run.step({zarya: (*heading, 16)})
            bubble = fight.zarya_bubbles.for_target(target)
            self.assertIsNotNone(bubble)
            self.assertEqual(bubble.health, 200)
            bubble.health = shield_health
        else:
            run.step({})
        self.assertIn(returned, fight.rockets)

        impacts = []
        landed = fight._landed

        def observe(rocket):
            self.assertIs(rocket, returned)
            impacts.append(fight.tick)
            landed(rocket)

        end = run.frame + 30
        with patch.object(fight, "_landed", side_effect=observe):
            while run.frame < end:
                self.world(run, end - run.frame if skipped else 1)
        impact, = impacts
        self.assertFalse(fight.rockets)
        self.assertEqual(self.health(run, target), 150 if projected else 90)
        anti = fight.heal_modifiers.get(target.body, {}).get("anti")
        if projected:
            remaining = max(0, shield_health - returned.blast.amount)
            self.assertEqual(bubble.health, remaining)
            self.assertIs(fight.zarya_bubbles.for_target(target), bubble if remaining else None)
            self.assertIsNone(anti)
            self.assertEqual(genji.stats.value(stats.HERO_DAMAGE), 0)
        else:
            self.assertIsNotNone(anti)
            self.assertEqual(anti["source"], genji.body)
            self.assertAlmostEqual(anti["start"], impact * combat.TICK)
            self.assertAlmostEqual(anti["end"] - anti["start"], ana.DURATION)
            hits = [event["+0x78"] for event in genji.client.messages(combat.HIT)]
            damage = [hit["+0x4"] for hit in hits if hit["+0x0"]["+0x0"] == target.body]
            self.assertEqual(damage, [60])
        return impact, self.health(run, target), genji.stats.value(stats.HERO_DAMAGE)

    def test_actual_projection_blocks_reflected_biotic_grenade_on_break(self):
        for shield_health in (None, 200, 20):
            with self.subTest(shield_health=shield_health):
                stepped = self.reflected_grenade_projection_trace(shield_health=shield_health, skipped=False)
                skipped = self.reflected_grenade_projection_trace(shield_health=shield_health, skipped=True)
                self.assertEqual(stepped, skipped)

    def test_expiry_uses_world_time_without_input_and_does_not_depend_on_caster_life(self):
        run, target, caster = self.scene()
        self.hurt(run, target, 100)
        self.grenade(run, caster, target)
        fight = run.match.combat
        own = next(t for t in fight._targets() if t.owner is caster)
        fight._damage(target, own, 1000, False, None, run.frame)
        self.assertIn("anti", fight.heal_modifiers[target.body])
        deadline = fight.heal_modifiers[target.body]["anti"]["end"]
        last_input = target.commands.last_frame
        while fight.now < deadline - 0.02:
            run.frame += 1
            run.match.update(run.frame * combat.TICK, run.frame, frames=False)
        self.assertEqual(fight._healing_factor(target), 0)
        self.world(run, 2)
        self.assertNotIn(target.body, fight.heal_modifiers)
        self.assertEqual(target.commands.last_frame, last_input)
        self.assertEqual(fight._healing_factor(target), 1)

    def test_friendly_grenade_heals_one_hundred_then_boosts_reaping_by_one_point_five(self):
        run, target, caster = self.scene(friendly=True)
        self.hurt(run, target, 200)
        self.grenade(run, caster, target)
        self.assertEqual(self.health(run, target), 150)
        self.assertEqual(caster.stats.value(stats.HEALING), 100)
        enemy = run.players[2]
        enemy.mover.position[:] = caster.mover.position
        enemy.mover.body = None
        self.shoot(run, target, enemy)
        self.assertAlmostEqual(self.health(run, target), 150 + 120 * 0.35000002 * 1.5)
        self.assertAlmostEqual(target.stats.value(stats.HEALING), 120 * 0.35000002 * 1.5)
        self.assertEqual(target.body_script.component.vars[342].value(), 120)

    def test_wraith_authored8440_cleanses_hostile_modifier_and_preserves_friendly_boost(self):
        run, target, caster = self.scene()
        self.hurt(run, target, 100)
        self.grenade(run, caster, target)
        self.assertIn("anti", run.match.combat.heal_modifiers[target.body])
        run.step({target: (0, 0, 8)})
        self.assertTrue(target.body_script.component.entity_var(8440).value())
        self.assertNotIn(target.body, run.match.combat.heal_modifiers)
        # A new scene verifies positive modifiers are not indiscriminately cleansed.
        run, target, caster = self.scene(friendly=True)
        self.grenade(run, caster, target)
        run.step({target: (0, 0, 8)})
        self.assertIn("boost", run.match.combat.heal_modifiers[target.body])

    def test_wraith_zen_and_mei_protection_reject_new_enemy_grenade_damage_and_status(self):
        for hero, button in ((None, 8), (defenses.ZENYATTA, 32), (defenses.MEI, 8)):
            with self.subTest(hero=hero):
                run, target, caster = self.scene(hero=hero)
                if button == 32:
                    target.body_script.set_ult_charge(target.body_script.component.vars[338].value())
                run.step({target: (0, 0, button)})
                before = self.health(run, target)
                self.grenade(run, caster, target)
                self.assertEqual(self.health(run, target), before)
                self.assertNotIn(target.body, run.match.combat.heal_modifiers)

    def test_actual_grenade_resolves_wraith_expiry_across_world_gap(self):
        def scene():
            run, target, caster = self.scene()
            x, y, z = target.mover.position
            # Land beside Reaper so the capsule cannot change the impact tick.
            caster.mover.position[:] = (x + 1.5, y, z + 5)
            caster.mover.body = None
            run.step({})
            self.hurt(run, target, 100)
            run.step({target: (0, 0, 8)})
            self.assertTrue(reaper.intangible(target))
            return run, target, caster, (x + 1.5, y, z)

        def launch(run, caster, point):
            fight = run.match.combat
            yaw, pitch = aim(fight._eye(caster), point)
            self.world(run, 1)
            caster.take_commands([Command(run.frame, yaw=yaw, pitch=pitch, buttons=16)])
            rocket, = fight.rockets
            self.assertTrue(rocket.ana_grenade)
            return rocket

        run, target, caster, point = scene()
        start = run.frame
        grenade = launch(run, caster, point)
        fired = run.frame
        for _ in range(30):
            self.world(run, 1)
            if grenade not in run.match.combat.rockets:
                break
        self.assertNotIn(grenade, run.match.combat.rockets)
        travel = run.frame - fired
        self.assertGreater(travel, 1)
        self.assertEqual(self.health(run, target), 150)
        while reaper.intangible(target) and run.frame < start + 210:
            self.world(run, 1)
        self.assertFalse(reaper.intangible(target))
        duration = run.frame - start

        for protected in (True, False):
            outcomes = []
            for skipped in (False, True):
                with self.subTest(protected=protected, skipped=skipped):
                    run, target, caster, point = scene()
                    fight = run.match.combat
                    frame = target.commands.last_frame
                    expiry = run.frame + duration
                    impact_tick = expiry - int(protected)
                    self.world(run, impact_tick - travel - run.frame - 1)
                    grenade = launch(run, caster, point)
                    self.world(run, 1)
                    self.assertEqual(grenade.last_tick, run.frame)
                    self.assertTrue(reaper.intangible(target))
                    impacts = []

                    def observe(rocket, *, world=fight, owner=target, hits=impacts, landed=fight._landed):
                        hits.append((world.tick, reaper.intangible(owner)))
                        landed(rocket)

                    end = expiry + 35
                    with patch.object(fight, "_landed", side_effect=observe):
                        while run.frame < end:
                            self.world(run, end - run.frame if skipped else 1)
                    self.assertEqual(impacts, [(impact_tick, protected)])
                    self.assertEqual(target.commands.last_frame, frame)
                    self.assertFalse(reaper.intangible(target))
                    self.assertEqual(self.health(run, target), 150 if protected else 90)
                    anti = fight.heal_modifiers.get(target.body, {}).get("anti")
                    if protected:
                        self.assertIsNone(anti)
                    else:
                        self.assertIsNotNone(anti)
                        self.assertEqual(anti["source"], caster.body)
                        self.assertAlmostEqual(anti["start"], impact_tick * combat.TICK)
                        self.assertAlmostEqual(anti["end"] - anti["start"], 4)
                    before = self.health(run, caster)
                    self.shoot(run, target, caster)
                    dealt = before - self.health(run, caster)
                    self.assertGreater(dealt, 0)
                    expected = 150 + dealt * reaper.REAPING_RATIO if protected else 90
                    self.assertAlmostEqual(self.health(run, target), expected)
                    outcomes.append((impacts, dealt, self.health(run, target)))
            if len(outcomes) == 2:
                self.assertEqual(outcomes[0], outcomes[1])

    def test_antiheal_suppresses_defensive_healing_until_its_resource_cleanse_entry(self):
        run, target, caster = self.scene(hero=defenses.MEI)
        self.hurt(run, target, 100)
        self.grenade(run, caster, target)
        fight = run.match.combat
        before = self.health(run, target)
        self.assertEqual(fight._heal(caster, target, fight.vitals[target.body], 10), 0)
        self.assertEqual(self.health(run, target), before)
        run.step({target: (0, 0, 8)})
        self.assertNotIn(target.body, fight.heal_modifiers)
        self.world(run, 63)
        self.assertGreater(self.health(run, target), before)

    def test_refresh_does_not_stack_and_recipient_death_or_replacement_removes_modifiers(self):
        run, target, caster = self.scene(friendly=True)
        self.grenade(run, caster, target)
        fight = run.match.combat
        first = dict(fight.heal_modifiers[target.body]["boost"])
        # Real cooldown refusal cannot create another interval.
        self.grenade(run, caster, target)
        self.assertEqual(fight.heal_modifiers[target.body]["boost"], first)
        enemy = run.players[2]
        t = next(t for t in fight._targets() if t.owner is target)
        fight._damage(enemy, t, 1000, False, None, run.frame)
        self.assertNotIn(target.body, fight.heal_modifiers)
        run, target, caster = self.scene(friendly=True)
        self.grenade(run, caster, target)
        old = target.body
        run.match.switch_hero(target, heroes()[SOLDIER])
        self.assertNotIn(old, run.match.combat.heal_modifiers)

    def test_ffa_other_player_is_hostile_and_miss_does_not_apply_a_status(self):
        run, target, caster = self.scene(friendly=True)
        run.match.game_map = run.match.game_map.__class__(**{
            **run.match.game_map.__dict__, "free_for_all": True,
        })
        self.grenade(run, caster, target)
        self.assertIn("anti", run.match.combat.heal_modifiers[target.body])
        run, target, caster = self.scene()
        run.step({caster: (0, -10000, 16)})  # away from the nearby receiver and floor
        for _ in range(20):
            run.step({})
        self.assertNotIn(target.body, run.match.combat.heal_modifiers)

    def second_ana(self, run, target, *, friendly):
        caster = run.players[2]
        run.match.switch_hero(caster, heroes()[ana.HERO])
        caster.team = target.team if friendly else 1
        x, y, z = target.mover.position
        caster.mover.position[:] = [x + 1.5, y, z]
        caster.mover.body = None
        return caster

    def test_second_source_replaces_boost_without_stacking_or_amplifying_its_own_instant_heal(self):
        run, target, first = self.scene(friendly=True)
        self.hurt(run, target, 200)
        self.grenade(run, first, target)
        previous = dict(run.match.combat.heal_modifiers[target.body]["boost"])
        self.hurt(run, target, 100)
        second = self.second_ana(run, target, friendly=True)
        self.grenade(run, second, target)
        current = run.match.combat.heal_modifiers[target.body]["boost"]
        self.assertEqual(self.health(run, target), 150)
        self.assertEqual(second.stats.value(stats.HEALING), 100)
        self.assertGreater(current["end"], previous["end"])
        self.assertEqual(current["source"], second.body)
        self.assertEqual(run.match.combat._healing_factor(target), 1.5)

    def test_boost_waits_for_its_authored_fifty_millisecond_entry(self):
        run, target, caster = self.scene(friendly=True)
        self.hurt(run, target, 200)
        x, y, z = target.mover.position
        yaw, pitch = aim(run.match.combat._eye(caster), (x, y, z))
        run.step({caster: (yaw, pitch, 16)})
        fight = run.match.combat
        for _ in range(15):
            if target.body in fight.heal_modifiers:
                break
            run.step({})
        self.assertIn("boost", fight.heal_modifiers[target.body])
        self.assertEqual(fight._healing_factor(target), 1)
        self.assertEqual(self.health(run, target), 150)
        self.world(run, 3)
        self.assertEqual(fight._healing_factor(target), 1)
        self.world(run, 1)
        self.assertEqual(fight._healing_factor(target), 1.5)

    def test_hostile_modifier_wins_until_wraith_cleanse_then_existing_boost_applies(self):
        run, target, enemy = self.scene()
        self.hurt(run, target, 100)
        self.grenade(run, enemy, target)
        friend = self.second_ana(run, target, friendly=True)
        self.grenade(run, friend, target)
        fight = run.match.combat
        self.assertEqual(self.health(run, target), 90)
        self.assertEqual(fight._healing_factor(target), 0)
        self.assertEqual(set(fight.heal_modifiers[target.body]), {"anti", "boost"})
        run.step({target: (0, 0, 8)})
        self.assertEqual(set(fight.heal_modifiers[target.body]), {"boost"})
        self.world(run, 35)
        run.step({target: (0, 0, 1)})
        self.world(run, 26)
        friend.spawned = False  # keep the real enemy target's ray unobstructed
        self.shoot(run, target, enemy)
        self.assertAlmostEqual(self.health(run, target), 90 + 120 * 0.35000002 * 1.5)

    def test_antiheal_blocks_live_zen_healing_then_world_expiry_restores_it(self):
        run, target, enemy = self.scene()
        self.hurt(run, target, 100)
        self.grenade(run, enemy, target)
        healer = run.players[2]
        run.match.switch_hero(healer, heroes()[defenses.ZENYATTA])
        healer.team = 0
        x, y, z = target.mover.position
        healer.mover.position[:] = [x, y, z - 6]
        healer.mover.body = None
        healer.body_script.set_ult_charge(healer.body_script.component.vars[338].value())
        run.step({healer: (0, 0, 32)})
        self.world(run, 125)
        self.assertEqual(self.health(run, target), 90)
        self.world(run, 150)
        self.assertEqual(self.health(run, target), 250)

    def test_body_replacement_retires_a_pending_ana_grenade(self):
        run, target, caster = self.scene()
        x, y, z = target.mover.position
        caster.mover.position[:] = [x, y, z + 20]
        caster.mover.body = None
        yaw, pitch = aim(run.match.combat._eye(caster), (x, y, z))
        run.step({caster: (yaw, pitch, 16)})
        fight = run.match.combat
        self.assertTrue(any(r.ana_grenade for r in fight.rockets))
        run.match.switch_hero(caster, heroes()[SOLDIER])
        self.assertFalse(any(r.ana_grenade for r in fight.rockets))
        self.world(run, 100)
        self.assertEqual(self.health(run, target), 250)
        self.assertNotIn(target.body, fight.heal_modifiers)

    def shadow(self, run, target):
        run.step({target: (0, 5000, 16)})
        run.step({target: (0, 5000, 0)})
        run.step({target: (0, 5000, 1)})
        record = run.match.combat.reaper_shadows[target.body]
        self.assertIsNotNone(record["cast"])
        return record

    def test_existing_antiheal_survives_shadow_step_without_an_authored_cleanse_flag(self):
        run, target, caster = self.scene()
        self.hurt(run, target, 100)
        self.grenade(run, caster, target)
        record = self.shadow(run, target)
        self.world(run, 100)
        self.assertTrue(record["moved"])
        self.assertEqual(run.match.combat._healing_factor(target), 0)
        self.assertEqual(self.health(run, target), 90)

    def test_shadow_immunity_rejects_an_incoming_enemy_grenade(self):
        run, target, caster = self.scene()
        self.shadow(run, target)
        self.world(run, 67)
        self.grenade(run, caster, target)
        self.assertEqual(self.health(run, target), 250)
        self.assertNotIn(target.body, run.match.combat.heal_modifiers)

    def test_antiheal_blocks_actual_blossom_reaping_through_channel_end(self):
        run, target, caster = self.scene()
        self.hurt(run, target, 100)
        self.grenade(run, caster, target)
        target.body_script.set_ult_charge(2100)
        run.step({target: (0, 0, 32)})
        self.world(run, 200)
        self.assertTrue(run.match.combat.is_dead(caster))
        self.assertEqual(self.health(run, target), 90)
        self.assertEqual(target.stats.value(stats.HEALING), 0)
        self.assertEqual(target.body_script.component.vars[342].value(), 0)

    def test_actual_grenade_during_blossom_blocks_later_reaping(self):
        run, target, caster = self.scene()
        fight = run.match.combat
        self.hurt(run, target, 100)
        enemy = fight.vitals[caster.body] = combat.Vitals(caster.body, 1000)
        target.body_script.set_ult_charge(2100)
        run.step({target: (0, 0, 32)})
        self.assertEqual(enemy.total, 966)
        self.assertAlmostEqual(self.health(run, target), 150 + 11.9, places=4)
        self.grenade(run, caster, target)
        self.assertIn("anti", fight.heal_modifiers[target.body])
        self.assertEqual(enemy.total, 966)  # Ana's grenade restores the opening 34 before the second pulse.
        self.assertAlmostEqual(self.health(run, target), 90 + 11.9, places=4)
        self.assertIsNotNone(reaper.blossom_state(target.body_script))
        for _ in range(190):
            run.step({})
        self.assertIsNone(reaper.blossom_state(target.body_script))
        self.assertEqual(enemy.total, 524)
        self.assertEqual(target.stats.value(stats.HERO_DAMAGE), 510)
        self.assertAlmostEqual(self.health(run, target), 90 + 11.9, places=4)
        self.assertAlmostEqual(target.stats.value(stats.HEALING), 11.9, places=4)

    def test_actual_antiheal_expiry_during_blossom_restores_reaping(self):
        self.check_antiheal_expiry_during_blossom(skipped=False)

    def test_actual_blossom_reaping_respects_antiheal_expiry_across_skipped_frames(self):
        self.check_antiheal_expiry_during_blossom(skipped=True)

    def check_antiheal_expiry_during_blossom(self, *, skipped):
        run, target, caster = self.scene()
        fight = run.match.combat
        self.hurt(run, target, 100)
        self.grenade(run, caster, target)
        self.assertEqual(self.health(run, target), 90)
        enemy = fight.vitals[caster.body] = combat.Vitals(caster.body, 1000)
        ends = fight.heal_modifiers[target.body]["anti"]["end"]
        while ends - fight.now > 0.92:
            run.step({})
        target.body_script.set_ult_charge(2100)
        run.step({target: (0, 0, 32)})
        self.assertEqual(enemy.total, 966)
        self.assertEqual(self.health(run, target), 90)
        if skipped:
            self.world(run, 70)
            self.assertGreater(fight.now, ends)
            self.assertEqual(enemy.total, 796)
            self.assertAlmostEqual(self.health(run, target), 90 + 34 * 0.35, places=4)
        else:
            while fight.now < ends:
                run.step({})
            self.assertEqual(enemy.total, 830)
            self.assertEqual(self.health(run, target), 90)
        for _ in range(140):
            run.step({})
        self.assertEqual(enemy.total, 490)
        self.assertAlmostEqual(self.health(run, target), 90 + 340 * 0.35, places=4)
        self.assertAlmostEqual(target.stats.value(stats.HEALING), 340 * 0.35, places=4)
        self.assertEqual(target.body_script.component.vars[342].value(), 0)

    def blossom_overlapping_grenade_trace(self, *, skipped):
        run, owner, hostile = self.scene(names=("Reaper", "Enemy Ana", "Allied Ana", "Enemy"))
        fight = run.match.combat
        self.hurt(run, owner, 180)
        self.grenade(run, hostile, owner)
        own = fight.vitals[owner.body]
        self.assertEqual(own.total, 10)
        anti_end = fight.heal_modifiers[owner.body]["anti"]["end"]
        ally = self.second_ana(run, owner, friendly=True)
        while anti_end - fight.now > 3.2:
            run.step({})
        self.grenade(run, ally, owner)
        self.assertEqual(own.total, 10)
        modifiers = fight.heal_modifiers[owner.body]
        self.assertEqual(set(modifiers), {"anti", "boost"})
        self.assertEqual(modifiers["anti"]["source"], hostile.body)
        self.assertEqual(modifiers["boost"]["source"], ally.body)
        boost_end = modifiers["boost"]["end"]
        self.assertGreater(boost_end, anti_end)
        self.assertEqual(fight._healing_factor(owner), 0)

        enemy = run.players[3]
        enemy.team = 1
        x, y, z = owner.mover.position
        hostile.mover.position[:] = (x + 15, y, z)
        enemy.mover.position[:] = (x, y, z + 3)
        hostile.mover.body = enemy.mover.body = None
        pool = fight.vitals[enemy.body] = combat.Vitals(enemy.body, 1000)
        while anti_end - fight.now > 0.35:
            run.step({})
        script = owner.body_script
        script.set_ult_charge(2100)
        damage = fight._damage
        pulses = []

        def observe(shooter, target, amount, *args, **kwargs):
            before = own.total
            result = damage(shooter, target, amount, *args, **kwargs)
            if shooter is owner and target.owner is enemy:
                pulses.append((fight.tick, fight.now, own.total - before))
            return result

        with patch.object(fight, "_damage", side_effect=observe):
            run.step({owner: (0, 0, 32)})
            frames = tuple(actor.commands.last_frame for actor in (owner, ally, hostile))
            if skipped:
                self.world(run, 200)
            else:
                for _ in range(200):
                    self.world(run, 1)
        self.assertEqual(tuple(actor.commands.last_frame for actor in (owner, ally, hostile)), frames)
        self.assertEqual(len(pulses), 15)
        base = 34 * reaper.REAPING_RATIO
        factors = [0 if at < anti_end else ana.BOOST if at < boost_end else 1 for _, at, _ in pulses]
        self.assertEqual(set(factors), {0, 1, ana.BOOST})
        for (_, _, healed), factor in zip(pulses, factors, strict=True):
            self.assertAlmostEqual(healed, base * factor, places=4)
        self.assertEqual(pool.total, 490)
        self.assertAlmostEqual(own.total, 10 + base * sum(factors), places=4)
        self.assertAlmostEqual(owner.stats.value(stats.HEALING), base * sum(factors), places=4)
        self.assertEqual(script.component.vars[342].value(), 0)
        self.assertNotIn(owner.body, fight.heal_modifiers)
        self.assertIsNone(reaper.blossom_state(script))
        return [(tick, healed) for tick, _, healed in pulses], factors, own.total

    def test_actual_blossom_reaps_across_staggered_grenade_expiries(self):
        stepped = self.blossom_overlapping_grenade_trace(skipped=False)
        self.assertEqual(stepped, self.blossom_overlapping_grenade_trace(skipped=True))

    def test_boost_amplifies_actual_blossom_reaping_and_never_charges_the_ultimate(self):
        run, target, caster = self.scene(friendly=True)
        self.hurt(run, target, 240)
        self.grenade(run, caster, target)
        enemy = run.players[2]
        enemy.mover.position[:] = caster.mover.position
        enemy.mover.body = None
        target.body_script.set_ult_charge(2100)
        run.step({target: (0, 0, 32)})
        self.world(run, 200)
        self.assertTrue(run.match.combat.is_dead(enemy))
        self.assertAlmostEqual(self.health(run, target), 110 + 200 * 0.35000002 * 1.5)
        self.assertAlmostEqual(target.stats.value(stats.HEALING), 200 * 0.35000002 * 1.5)
        self.assertEqual(target.body_script.component.vars[342].value(), 0)


if __name__ == "__main__":
    unittest.main()
