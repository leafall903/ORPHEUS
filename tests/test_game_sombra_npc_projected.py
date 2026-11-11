"""Actual NPC recipients of Zarya Projected Barrier in Sombra combat."""

import unittest
from dataclasses import replace

import test_game_sombra_grenade as grenade_fixture
import test_game_sombra_npc as npc_fixture
from test_game_combat import SOLDIER, aim, in_world

from ow174.game import combat, graviton, npc, reinhardt_barrier
from ow174.game.commands import FIRE
from ow174.game.content import heroes
from ow174.game.script import nodes
from ow174.game.server_snapshot import SCHEMA, SCOPE, _Graph, snapshot_digest
from ow174.game.world import HEALTH_POOL, SHIELDS_POOL


class SombraNpcProjectedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        npc_fixture.SombraActualNpcTests.setUpClass.__func__(cls)

    @classmethod
    def restore_graphs(cls, path):
        npc_fixture.SombraActualNpcTests.restore_graphs(path)

    def arena(self, key):
        f, bot = npc_fixture.SombraActualNpcTests.make(self, key, distance=1.5)
        match = f.run.match
        match.game_map = replace(match.game_map, free_for_all=False)
        f.a.team, f.b.team, bot.team = 0, 1, 1
        f.b.spawned = True
        self.assertTrue(match.switch_hero(f.b, heroes()[reinhardt_barrier.ZARYA]))
        f.b.mover.position[:] = (5, 0, 1.5)
        f.b.mover.body = None
        f.a.body_script.ult_seconds = 0
        f.press(0, 35)
        return f, bot

    @staticmethod
    def project(f, bot):
        fight = f.run.match.combat
        x, y, z = bot.position
        angles = aim(fight._eye(f.b), (x, y + 1, z))
        f.run.step({f.b: (*angles, 16)})

    def bubble(self, f, bot):
        fight = f.run.match.combat
        self.project(f, bot)
        shield, = fight.barriers.values()
        self.assertIs(shield.protected, bot)
        self.assertEqual(shield.protected_body, bot.entity)
        self.assertNotEqual(shield.protected_body, bot.body)
        self.assertEqual(shield.vitals.total, 200)
        return shield

    def add_attacker(self, f, bot, hero_id):
        match = f.run.match
        hero = heroes()[hero_id]
        attacker = match.add_player(3, hero.name, hero_id, 0, False)
        in_world(match, attacker)
        f.run.players.append(attacker)
        self.assertTrue(match.switch_hero(attacker, hero))
        attacker.team = 0
        x, y, z = bot.position
        attacker.mover.position[:] = (x, y, z + 6)
        attacker.mover.body = None
        f.press(0, 35)
        return attacker, aim(match.combat._eye(attacker), (x, y + 1, z))

    def test_all_five_projected_npc_barriers_resolve_pistol_melee_hack_and_emp(self):
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                f, bot = self.arena(key)
                fight = f.run.match.combat
                vitals = fight.vitals[bot.entity]
                before = vitals.total
                shield_pool = vitals.now[SHIELDS_POOL]
                shield = self.bubble(f, bot)
                npc_fixture.SombraActualNpcTests.press(f, bot, FIRE)
                npc_fixture.SombraActualNpcTests.press(f, bot, 0, 8)
                npc_fixture.SombraActualNpcTests.press(f, bot, 0x800)
                f.press(0, 15)
                self.assertEqual(vitals.total, before)
                self.assertEqual(shield.vitals.total, 162)
                self.assertAlmostEqual(f.b.body_script.component.entity_var(87).value(), 7.6, places=5)
                self.assertFalse(f.a.client.messages(combat.HIT))
                self.assertEqual(f.a.body_script.component.entity_var(342).value(), 0)
                npc_fixture.SombraActualNpcTests.press(f, bot, 2, 45)
                hack = f.weapon().state(53)
                self.assertFalse(f.weapon().vars[375].value())
                lock = f.weapon().vars.get(8909)
                self.assertFalse(lock is not None and lock.value())
                self.assertEqual(hack.remaining(), 0)
                self.assertNotIn(bot.entity, fight.hacks)
                self.assertIn(shield.entity, fight.barriers)
                f.a.body_script.set_ult_charge(1400)
                npc_fixture.SombraActualNpcTests.press(f, bot, 32)
                f.press(0, 35)
                self.assertNotIn(shield.entity, fight.barriers)
                self.assertEqual(shield.vitals.total, 0)
                effect, = fight.hacks[bot.entity]
                self.assertEqual(effect.application.source_entity(), f.a.body)
                self.assertEqual(vitals.total, before - shield_pool)
                f.press(0, 45)
                npc_fixture.SombraActualNpcTests.press(f, bot, FIRE)
                self.assertEqual(vitals.total, before - shield_pool - 8)

    def test_all_five_projected_npc_barriers_cleanse_actual_antiheal_and_sleep(self):
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                f, bot = self.arena(key)
                fight = f.run.match.combat
                ana, angles = self.add_attacker(f, bot, grenade_fixture.ANA)
                vitals = fight.vitals[bot.entity]
                before = vitals.total
                f.run.step({ana: (*angles, 16)})
                for _ in range(60):
                    if vitals.healing_block_until:
                        break
                    f.run.step({ana: (*angles, 0)})
                block = vitals.healing_block_until
                self.assertGreater(block, f.run.frame)
                self.assertEqual(vitals.total, before - 50)
                f.press(0, 35)
                f.run.step({ana: (*angles, 8)})
                for _ in range(65):
                    if vitals.sleep_until:
                        break
                    f.run.step({ana: (*angles, 0)})
                self.assertEqual(vitals.total, before - 55)
                self.assertTrue(fight.is_stunned(bot))
                self.assertEqual(vitals.healing_block_until, block)
                self.bubble(f, bot)
                clocks = (vitals.sleep_until, vitals.sleep_wake_floor, vitals.healing_block_until)
                self.assertEqual(clocks, (0, 0, 0))
                self.assertFalse(fight.is_stunned(bot))
                self.assertEqual(vitals.total, before - 55)
                fight.heal_area(f.b, tuple(bot.position), 1, 20)
                self.assertEqual(vitals.total, before - 35)

    def test_actual_grenade_and_dart_hit_only_the_projected_pool_on_all_five_npcs(self):
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                f, bot = self.arena(key)
                fight = f.run.match.combat
                ana, angles = self.add_attacker(f, bot, grenade_fixture.ANA)
                vitals = fight.vitals[bot.entity]
                before = vitals.total
                shield = self.bubble(f, bot)
                f.run.step({ana: (*angles, 16)})
                for _ in range(45):
                    if shield.vitals.total < 200:
                        break
                    f.run.step({ana: (*angles, 0)})
                self.assertEqual(shield.vitals.total, 150)
                f.press(0, 35)
                f.run.step({ana: (*angles, 8)})
                for _ in range(60):
                    if shield.vitals.total < 150:
                        break
                    f.run.step({ana: (*angles, 0)})
                self.assertEqual(shield.vitals.total, 145)
                self.assertEqual(f.b.body_script.component.entity_var(87).value(), 11)
                self.assertEqual(vitals.total, before)
                self.assertEqual((vitals.healing_block_until, vitals.sleep_until), (0, 0))
                self.assertFalse(ana.client.messages(combat.HIT))

    def test_normal_soldier_rifle_hits_projected_pool_and_does_not_spill_on_break(self):
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                f, bot = self.arena(key)
                fight = f.run.match.combat
                soldier, angles = self.add_attacker(f, bot, SOLDIER)
                vitals = fight.vitals[bot.entity]
                before = vitals.total
                shield = self.bubble(f, bot)
                f.run.step({soldier: (*angles, FIRE)})
                self.assertEqual(shield.vitals.total, 180)
                self.assertEqual(vitals.total, before)
                self.assertFalse(soldier.client.messages(combat.HIT))
                shield.vitals.damage(170)
                f.press(0, 12)
                f.run.step({soldier: (*angles, FIRE)})
                self.assertEqual(shield.vitals.total, 0)
                self.assertNotIn(shield.entity, fight.barriers)
                self.assertEqual(vitals.total, before)
                self.assertFalse(soldier.client.messages(combat.HIT))
                self.assertEqual(f.b.body_script.component.entity_var(87).value(), 6)
                f.press(0, 12)
                f.run.step({soldier: (*angles, FIRE)})
                hit, = soldier.client.messages(combat.HIT)
                self.assertEqual(hit["+0x78"]["+0x0"]["+0x0"], bot.entity)
                self.assertEqual(vitals.total, before - 20)

    def test_actual_helix_direct_and_splash_share_one_contact_on_all_five_projected_npcs(self):
        for key, definition in self.catalog.items():
            for breaking in (False, True):
                with self.subTest(npc=definition.name, breaking=breaking):
                    f, bot = self.arena(key)
                    match = f.run.match
                    fight = match.combat
                    soldier, angles = self.add_attacker(f, bot, SOLDIER)
                    vitals = fight.vitals[bot.entity]
                    before = vitals.total
                    shield = self.bubble(f, bot)
                    if breaking:
                        shield.vitals.damage(190)
                    # This nearby ally of the recipient remains exposed to splash.
                    for player in f.run.players:
                        player.client.bot_stream = lambda body: None
                    flank = npc.spawn(match, self.catalog["1"], (1.4, 0, 1.5), team=bot.team)
                    f.press(0, 3)
                    flank_vitals = fight.vitals[flank.entity]
                    flank_before = flank_vitals.total
                    f.run.step({soldier: (*angles, 2)})
                    for _ in range(45):
                        if flank_vitals.total < flank_before:
                            break
                        f.run.step({soldier: (*angles, 0)})
                    self.assertLess(flank_vitals.total, flank_before)
                    self.assertEqual(shield.vitals.total, 0 if breaking else 80)
                    self.assertEqual(shield.entity in fight.barriers, not breaking)
                    self.assertEqual(vitals.total, before)
                    self.assertEqual(f.b.body_script.component.entity_var(87).value(), 2 if breaking else 24)
                    hit, = soldier.client.messages(combat.HIT)
                    self.assertEqual(hit["+0x78"]["+0x0"]["+0x0"], flank.entity)

    def test_flashbang_breaks_projected_pool_without_stunning_any_of_the_five_npcs(self):
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                f, bot = self.arena(key)
                fight = f.run.match.combat
                mccree, _ = self.add_attacker(f, bot, 0x02E0000000000042)
                x, y, z = bot.position
                mccree.mover.position[:] = (x, y, z + 3)
                mccree.mover.body = None
                f.press(0, 5)
                angles = aim(fight._eye(mccree), (x, y + 1, z))
                vitals = fight.vitals[bot.entity]
                before = vitals.total
                shield = self.bubble(f, bot)
                shield.vitals.damage(190)
                f.run.step({mccree: (*angles, 16)})
                for _ in range(45):
                    if shield.entity not in fight.barriers:
                        break
                    f.run.step({mccree: (*angles, 0)})
                self.assertEqual(shield.vitals.total, 0)
                self.assertNotIn(shield.entity, fight.barriers)
                self.assertEqual(vitals.total, before)
                self.assertNotIn(bot.entity, fight.stuns)
                self.assertEqual(f.b.body_script.component.entity_var(87).value(), 2)
                self.assertFalse(mccree.client.messages(combat.HIT))

    def land_graviton(self, f, caster, angles):
        fight = f.run.match.combat
        script = caster.body_script
        script.ult_seconds = 0
        script.set_ult_charge(script.component.entity_var(338).value())
        f.run.step({caster: (*angles, 32)})
        shot, = fight.rockets
        self.assertEqual(shot.spawn, graviton.FIELD_ENTITY)
        for _ in range(35):
            if fight.gravitons.fields:
                break
            f.run.step({caster: (*angles, 0)})
        field, = fight.gravitons.fields
        self.assertNotIn(shot, fight.rockets)
        self.assertEqual(field.source_body, caster.body)
        return field

    def sombra_graviton_scene(self):
        scene = grenade_fixture.SombraGrenadeTests()
        f = scene.arena()
        self.assertTrue(f.run.match.switch_hero(f.b, heroes()[reinhardt_barrier.ZARYA]))
        f.b.team = 1
        f.b.mover.position[:] = (0, 0, 3)
        f.b.mover.body = None
        return f, scene.join_ally(f, reinhardt_barrier.ZARYA, (5, 0, 0))

    def assert_projected_snapshot(self, match, shield, **actors):
        def digest():
            stored = _Graph({"match": match, **actors}, None)
            roots = {
                "shield": stored.value(shield),
                "bodies": [stored.value(actor.body_script.component) for actor in actors.values()],
            }
            return snapshot_digest({
                "schema": SCHEMA, "scope": SCOPE, "restorable": False,
                "roots": roots, "nodes": stored.finish(), "assets": [],
            })

        before = digest()
        self.assertEqual(digest(), before)
        self.assertIsNotNone(shield.expiry_tick)
        shield.expiry_tick += 1
        self.assertNotEqual(digest(), before)

    def test_active_projected_shield_vm_and_expiry_export_for_player_and_all_five_npcs(self):
        f, zarya = self.sombra_graviton_scene()
        match = f.run.match
        fight = match.combat
        f.run.step({zarya: (*aim(fight._eye(zarya), (0, 1, 0)), 16)})
        shield, = fight.barriers.values()
        self.assertIs(shield.protected, f.a)
        self.assert_projected_snapshot(match, shield, caster=zarya, recipient=f.a, enemy=f.b)

        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                f, bot = self.arena(key)
                shield = self.bubble(f, bot)
                self.assert_projected_snapshot(
                    f.run.match, shield, caster=f.b, recipient=bot, enemy=f.a,
                )

    def test_graviton_impact_and_field_ticks_drain_only_the_projected_pool_on_all_five_npcs(self):
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                f, bot = self.arena(key)
                fight = f.run.match.combat
                caster, angles = self.add_attacker(f, bot, reinhardt_barrier.ZARYA)
                before = fight.vitals[bot.entity].total
                shield = self.bubble(f, bot)
                field = self.land_graviton(f, caster, angles)
                self.assertEqual(shield.vitals.total, 195)
                self.assertEqual(fight.vitals[bot.entity].total, before)
                f.press(0, 20)
                self.assertAlmostEqual(shield.vitals.total, 193.4, places=5)
                self.assertEqual(fight.vitals[bot.entity].total, before)
                hit_ids = {hit["+0x78"]["+0x0"]["+0x0"] for hit in caster.client.messages(combat.HIT)}
                self.assertEqual(hit_ids, {f.b.body})  # only the nearby unshielded caster takes field damage
                self.assertAlmostEqual(f.b.body_script.component.entity_var(87).value(), 1.32, places=4)
                self.assertIn(bot.entity, fight.gravitons.npc_movers)
                self.assertIn(field, fight.gravitons.fields)

    def test_graviton_field_shield_break_matches_contiguous_and_catchup_for_sombra(self):
        for catchup in (False, True):
            with self.subTest(catchup=catchup):
                f, zarya = self.sombra_graviton_scene()
                match = f.run.match
                fight = match.combat
                angles = aim(fight._eye(zarya), (0, 1, 0))
                f.run.step({zarya: (*angles, 16)})
                shield, = fight.barriers.values()
                self.assertIs(shield.protected, f.a)
                field = self.land_graviton(f, f.b, aim(fight._eye(f.b), (0, 1, 0)))
                self.assertEqual(f.health(f.a), 200)
                self.assertEqual(shield.vitals.total, 195)
                shield.vitals.damage(194.88)
                if catchup:
                    f.run.frame += 70
                    match.update(f.run.frame * combat.TICK, f.run.frame)
                else:
                    f.press(0)
                    self.assertAlmostEqual(shield.vitals.total, 0.04, places=5)
                    self.assertEqual(f.health(f.a), 200)
                    f.press(0)
                    self.assertNotIn(shield.entity, fight.barriers)
                    self.assertEqual(f.health(f.a), 200)
                    f.press(0)
                    self.assertAlmostEqual(f.health(f.a), 199.92, places=5)
                    f.press(0, 67)
                self.assertNotIn(shield.entity, fight.barriers)
                self.assertAlmostEqual(f.health(f.a), 200 - 68 * 0.08, places=5)
                self.assertTrue(fight.gravitons.pulling(f.a))
                self.assertEqual(field.last_tick, f.run.frame)

    def test_graviton_crosses_projected_expiry_in_retained_or_omitted_frames(self):
        for elapsed in (140, 205):
            for catchup in (False, True):
                with self.subTest(elapsed=elapsed, catchup=catchup):
                    f, zarya = self.sombra_graviton_scene()
                    match = f.run.match
                    fight = match.combat
                    f.run.step({zarya: (*aim(fight._eye(zarya), (0, 1, 0)), 16)})
                    shield, = fight.barriers.values()
                    cast_tick = f.run.frame
                    self.assertEqual(shield.expiry_tick, cast_tick + 124)
                    field = self.land_graviton(f, f.b, aim(fight._eye(f.b), (0, 1, 0)))
                    covered = shield.expiry_tick - field.last_tick
                    self.assertGreater(covered, 0)
                    if catchup:
                        f.run.frame += elapsed
                        match.update(f.run.frame * combat.TICK, f.run.frame)
                    else:
                        f.press(0, elapsed)
                    self.assertEqual(field.last_tick, f.run.frame)
                    self.assertNotIn(shield.entity, fight.barriers)
                    self.assertFalse(shield.pet.active)
                    self.assertAlmostEqual(shield.vitals.total, 195 - covered * 0.08, places=4)
                    self.assertAlmostEqual(f.health(f.a), 200 - (elapsed - covered) * 0.08, places=4)
                    self.assertTrue(fight.gravitons.pulling(f.a))

    def test_world_clock_expires_personal_and_projected_pet_and_starts_authored_cooldown(self):
        for personal in (False, True):
            with self.subTest(personal=personal):
                f, zarya = self.sombra_graviton_scene()
                match = f.run.match
                fight = match.combat
                f.run.step({zarya: (*aim(fight._eye(zarya), (0, 1, 0)), 8 if personal else 16)})
                shield, = fight.barriers.values()
                script = zarya.body_script
                owner_frame = script.frame
                expiry = f.run.frame + 124
                self.assertEqual(shield.expiry_tick, expiry)
                match.update((expiry - 1) * combat.TICK, expiry - 1)
                self.assertIn(shield.entity, fight.barriers)
                match.update(expiry * combat.TICK, expiry)
                self.assertNotIn(shield.entity, fight.barriers)
                self.assertFalse(shield.pet.active)
                self.assertFalse(shield.component.instances)
                self.assertEqual(script.frame, owner_frame)
                self.assertEqual(shield.pet.ended_at, (expiry + 1) * 16 - 1)
                ability = shield.pet.instance.state(0 if personal else 9)
                duration = 10 if personal else 8
                self.assertEqual(ability.last, shield.pet.ended_at)
                self.assertEqual(ability.cur, duration)
                f.run.frame = expiry
                f.press(0)
                self.assertAlmostEqual(ability.remaining(), duration - combat.TICK, places=5)
                angles = aim(fight._eye(zarya), (0, 1, 0))
                button = 8 if personal else 16
                f.run.step({zarya: (*angles, button)})
                self.assertFalse(fight.barriers)
                f.press(0)
                f.run.frame = expiry + round(duration / combat.TICK)
                match.update(f.run.frame * combat.TICK, f.run.frame)
                f.run.step({zarya: (*angles, button)})
                fresh, = fight.barriers.values()
                self.assertNotEqual(fresh.entity, shield.entity)
                self.assertIs(fresh.protected, zarya if personal else f.a)
                self.assertEqual(fresh.vitals.total, 200)

    def test_helix_in_flight_across_projected_expiry_uses_its_impact_side_of_the_boundary(self):
        for launch_age in (100, 122):
            for catchup in (False, True):
                with self.subTest(launch_age=launch_age, catchup=catchup):
                    f, zarya = self.sombra_graviton_scene()
                    match = f.run.match
                    fight = match.combat
                    self.assertTrue(match.switch_hero(f.b, heroes()[SOLDIER]))
                    f.b.team = 1
                    f.b.mover.position[:] = (0, 0, 12)
                    f.b.mover.body = None
                    f.press(0, 35)
                    f.run.step({zarya: (*aim(fight._eye(zarya), (0, 1, 0)), 16)})
                    shield, = fight.barriers.values()
                    f.press(0, launch_age - 1)
                    f.run.step({f.b: (*aim(fight._eye(f.b), (0, 1, 0)), 2)})
                    rocket, = fight.rockets
                    f.press(0)
                    self.assertIsNotNone(rocket.last_tick)
                    if catchup:
                        f.run.frame += 30
                        match.update(f.run.frame * combat.TICK, f.run.frame)
                    else:
                        f.press(0, 30)
                    self.assertNotIn(shield.entity, fight.barriers)
                    self.assertNotIn(rocket, fight.rockets)
                    self.assertEqual(shield.vitals.total, 80 if launch_age == 100 else 200)
                    self.assertEqual(f.health(f.a), 200 if launch_age == 100 else 80)

    def test_late_projected_barrier_absorbs_future_graviton_ticks_until_expiry(self):
        f, zarya = self.sombra_graviton_scene()
        fight = f.run.match.combat
        field = self.land_graviton(f, f.b, aim(fight._eye(f.b), (0, 1, 0)))
        self.assertEqual(f.health(f.a), 195)
        f.press(0, 20)
        self.assertAlmostEqual(f.health(f.a), 193.4, places=5)
        self.assertTrue(fight.gravitons.pulling(f.a))
        x, y, z = f.a.mover.position
        angles = aim(fight._eye(zarya), (x, y + 1, z))
        f.run.step({zarya: (*angles, 16)})
        shield, = fight.barriers.values()
        self.assertIs(shield.protected, f.a)
        self.assertAlmostEqual(f.health(f.a), 193.32, places=5)
        f.press(0, 20)
        self.assertAlmostEqual(shield.vitals.total, 198.4, places=5)
        self.assertAlmostEqual(f.health(f.a), 193.32, places=5)
        f.press(8)
        f.press(0, 65)
        camo = f.camo().state(13)
        self.assertTrue(camo.flags & nodes.Ability.RUNNING)
        self.assertAlmostEqual(f.health(f.a), 193.32, places=5)
        for _ in range(135):
            if shield.entity not in fight.barriers:
                break
            f.press(0)
        self.assertNotIn(shield.entity, fight.barriers)
        self.assertGreater(shield.vitals.total, 0)
        self.assertIn(field, fight.gravitons.fields)
        before = f.health(f.a)
        f.press(0)
        self.assertAlmostEqual(f.health(f.a), before - 0.08, places=5)
        f.press(0, 65)
        self.assertFalse(camo.flags & nodes.Ability.RUNNING)
        self.assertGreater(camo.remaining(), 0)

    def test_lethal_graviton_tick_after_shield_break_keeps_original_caster_attribution(self):
        f, zarya = self.sombra_graviton_scene()
        match = f.run.match
        fight = match.combat
        f.run.step({zarya: (*aim(fight._eye(zarya), (0, 1, 0)), 16)})
        shield, = fight.barriers.values()
        self.assertIs(shield.protected, f.a)
        field = self.land_graviton(f, f.b, aim(fight._eye(f.b), (0, 1, 0)))
        source = f.b.body
        self.assertTrue(match.switch_hero(f.b, heroes()[SOLDIER]))
        self.assertNotEqual(f.b.body, source)
        vitals = fight.vitals[f.a.body]
        vitals.now[HEALTH_POOL] = 0.04
        shield.vitals.damage(194.96)
        f.press(0)
        self.assertNotIn(shield.entity, fight.barriers)
        self.assertFalse(vitals.dead)
        self.assertEqual(vitals.total, 0.04)
        f.press(0)
        self.assertTrue(vitals.dead)
        self.assertEqual(vitals.death_tick, f.run.frame)
        self.assertEqual(vitals.killer, source)
        self.assertFalse(fight.gravitons.pulling(f.a))
        self.assertIn(field, fight.gravitons.fields)

    def test_projected_barrier_breaking_to_flashbang_preserves_sombras_paid_emp(self):
        scene = grenade_fixture.SombraGrenadeTests()
        f = scene.arena()
        match = f.run.match
        fight = match.combat
        self.assertTrue(match.switch_hero(f.b, heroes()[0x02E0000000000042]))
        f.b.team = 1
        f.b.mover.position[:] = (0, 0, 3)
        f.b.mover.body = None
        zarya = scene.join_ally(f, reinhardt_barrier.ZARYA, (5, 0, 0))
        angles = aim(fight._eye(zarya), (0, 1, 0))
        f.run.step({zarya: (*angles, 16)})
        shield, = fight.barriers.values()
        self.assertIs(shield.protected, f.a)
        shield.vitals.damage(190)
        script = f.a.body_script
        script.set_ult_charge(1400)
        emp = next(i for i in script.component.instances.values() if i.graph.index == 0x0B31)
        pulse = emp.node_state(emp.graph.node(40))
        angles = aim(fight._eye(f.b), (0, 1, 0))
        f.run.step({f.a: (0, 0, 32), f.b: (*angles, 16)})
        self.assertTrue(emp.state(1).flags & nodes.Ability.RUNNING)
        self.assertEqual(script.component.entity_var(342).value(), 0)
        for _ in range(22):
            if shield.entity not in fight.barriers:
                break
            f.run.step({f.b: (*angles, 0)})
        self.assertNotIn(shield.entity, fight.barriers)
        self.assertEqual(shield.vitals.total, 0)
        self.assertEqual(pulse.counter, 0)
        self.assertTrue(emp.state(1).flags & nodes.Ability.RUNNING)
        self.assertFalse(fight.is_stunned(f.a))
        self.assertEqual(f.health(f.a), 200)
        self.assertEqual(zarya.body_script.component.entity_var(87).value(), 2)
        f.press(0, 85)
        self.assertEqual(pulse.counter, 1)
        effect, = fight.hacks[f.b.body]
        self.assertEqual(effect.application.source_entity(), f.a.body)
        self.assertEqual(f.health(f.a), 200)

    def test_projected_barrier_breaking_to_zarya_secondary_preserves_sombras_camo(self):
        for protected in (False, True):
            with self.subTest(protected=protected):
                scene = grenade_fixture.SombraGrenadeTests()
                f = scene.arena()
                match = f.run.match
                fight = match.combat
                self.assertTrue(match.switch_hero(f.b, heroes()[reinhardt_barrier.ZARYA]))
                f.b.team = 1
                f.b.mover.position[:] = (0, 0, 3)
                f.b.mover.body = None
                zarya = scene.join_ally(f, reinhardt_barrier.ZARYA, (5, 0, 0))
                f.press(8)
                f.press(0, 65)
                camo = f.camo().state(13)
                self.assertTrue(camo.flags & nodes.Ability.RUNNING)
                if protected:
                    angles = aim(fight._eye(zarya), (0, 1, 0))
                    f.run.step({zarya: (*angles, 16)})
                    shield, = fight.barriers.values()
                    self.assertIs(shield.protected, f.a)
                    shield.vitals.damage(190)
                angles = aim(fight._eye(f.b), (0, 1, 0))
                f.run.step({f.b: (*angles, 2)})
                shot, = fight.rockets
                self.assertEqual(shot.blast.amount, 47)
                for _ in range(45):
                    if shot not in fight.rockets:
                        break
                    f.run.step({f.b: (*angles, 0)})
                self.assertNotIn(shot, fight.rockets)
                self.assertEqual(f.health(f.a), 200 if protected else 153)
                if protected:
                    self.assertNotIn(shield.entity, fight.barriers)
                    self.assertEqual(zarya.body_script.component.entity_var(87).value(), 2)
                f.press(0, 55)
                self.assertEqual(bool(camo.flags & nodes.Ability.RUNNING), protected)

    def test_projected_npc_recipient_removal_retires_the_field_immediately(self):
        f, bot = self.arena("1")
        fight = f.run.match.combat
        shield = self.bubble(f, bot)
        npc.retire_body(f.run.match, bot)
        self.assertNotIn(bot, f.run.match.bots)
        self.assertNotIn(shield.entity, fight.barriers)
        self.assertFalse(shield.component.instances)
        self.assertFalse(shield.pet.active)

    def test_late_projected_barrier_cancels_hack_and_expiry_allows_a_fresh_hack_on_each_npc(self):
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                f, bot = self.arena(key)
                fight = f.run.match.combat
                if key == "2":
                    # Isolate the barrier from Enforcer's own authored gate.
                    bot.body_script.component.set(14679, False)
                npc_fixture.SombraActualNpcTests.press(f, bot, 2, 15)
                self.assertTrue(f.weapon().state(53).flags & nodes.Ability.RUNNING)
                self.assertEqual(f.weapon().vars[8909].value().id, bot.entity)
                x, y, z = bot.position
                sombra_aim = aim(fight._eye(f.a), (x, y + 1, z))
                zarya_aim = aim(fight._eye(f.b), (x, y + 1, z))
                f.run.step({f.a: (*sombra_aim, 2), f.b: (*zarya_aim, 16)})
                shield, = fight.barriers.values()
                self.assertIs(shield.protected, bot)
                npc_fixture.SombraActualNpcTests.press(f, bot, 2, 35)
                self.assertNotIn(bot.entity, fight.hacks)
                self.assertFalse(f.weapon().vars[8909].value())
                self.assertEqual(shield.vitals.total, 200)
                for _ in range(160):
                    if shield.entity not in fight.barriers:
                        break
                    f.press(0)
                self.assertNotIn(shield.entity, fight.barriers)
                self.assertNotIn(bot.entity, fight.hacks)
                npc_fixture.SombraActualNpcTests.press(f, bot, 2, 80)
                effect, = fight.hacks[bot.entity]
                self.assertEqual(effect.application.source_entity(), f.a.body)

    def test_projected_barrier_rejects_enemy_or_covered_npc_then_accepts_fresh_input(self):
        for wall in (False, True):
            with self.subTest(wall=wall):
                f, bot = self.arena("1")
                fight = f.run.match.combat
                if wall:
                    f.geometry((2.5, 0, -2, 2.5, 0, 5, 2.5, 4, 5),
                               (2.5, 0, -2, 2.5, 4, 5, 2.5, 4, -2))
                else:
                    bot.team = f.a.team
                self.project(f, bot)
                self.assertFalse(fight.barriers)
                f.press(0, 10)
                if wall:
                    f.geometry()
                else:
                    bot.team = f.b.team
                self.bubble(f, bot)
