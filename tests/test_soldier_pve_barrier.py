"""Actual PvE bodies and weapons interacting with hosted hero defenses."""

import unittest
from unittest.mock import patch

import test_soldier_barrier as barrier_fixture
import test_soldier_pve_static as fixture
from test_game_combat import aim, in_world, first_canister_sweep

from ow174.game import combat, defenses, projectiles, reinhardt_barrier, stats, winston_barrier, zarya_barriers as zarya_barrier
from ow174.game.commands import Command
from ow174.game.content import heroes


class SoldierPveBarrierTests(unittest.TestCase):
    setUpClass = classmethod(fixture.SoldierPveStaticTests.setUpClass.__func__)
    tearDownClass = classmethod(fixture.SoldierPveStaticTests.tearDownClass.__func__)
    reset_catalogs = staticmethod(fixture.SoldierPveStaticTests.reset_catalogs)
    pool = barrier_fixture.SoldierBarrierTests.pool
    shield = barrier_fixture.SoldierBarrierTests.shield
    instance = barrier_fixture.SoldierBarrierTests.instance

    protections = (
        (defenses.REAPER, 8, defenses.intangible),
        (defenses.MEI, 8, defenses.cryo_protected),
        (defenses.ZENYATTA, 0x20, defenses.invulnerable),
    )

    def phase_arena(self, key, hero, *, incoming, melee=False):
        run, soldier, actor = fixture.SoldierPveStaticTests.arena(self, key, (0, 0, -1.1 if melee else 5), 1)
        team = soldier.team if incoming else actor.team
        protector = run.match.add_player(2, "Protector", hero, team, False)
        in_world(run.match, protector)
        protector.client = fixture.NpcRecordingClient(protector)
        run.players.append(protector)
        self.assertTrue(run.match.switch_hero(protector, heroes()[hero]))
        soldier.mover.position = [0, 0, -3]
        protector.mover.position = [0, 0, -1.6 if incoming else 0]
        if melee:
            protector.mover.position[2] = -2.1
        for player in run.players:
            player.mover.body = None
            player.body_script.ult_seconds = 0
        run.step({})
        return run, soldier, protector, actor

    def activate_protection(self, run, protector, activation, active):
        if activation == 0x20:
            script = protector.body_script
            script.set_ult_charge(script.component.entity_var(338).value())
        run.step({protector: (0, 0, activation)})
        self.assertTrue(active(protector))

    def test_actual_invulnerability_body_routing_for_all_five_npcs(self):
        weapons = (("rifle", 1, 20), ("helix", 2, 120), ("melee", 0x800, 30), ("visor", 1, 20))
        for key, definition in self.catalog.items():
            for hero, activation, active in self.protections:
                for weapon, button, damage in weapons:
                    with self.subTest(npc=definition.name, hero=hero, weapon=weapon):
                        melee = weapon == "melee"
                        run, soldier, protector, actor = self.phase_arena(
                            key, hero, incoming=False, melee=melee,
                        )
                        script = soldier.body_script
                        if weapon == "visor":
                            script.set_ult_charge(script.component.entity_var(338).value())
                            run.step({soldier: (0, 0, 0x20)})
                            for _ in range(80):
                                run.step({})
                        self.activate_protection(run, protector, activation, active)
                        fight = run.match.combat
                        protected_pool, rear_pool = self.pool(run, protector), self.pool(run, actor)
                        protected_before, rear_before = protected_pool.total, rear_pool.total
                        ammo = script.component.instances[11].vars[53]
                        before_ammo = ammo.value()
                        angles = aim(fight._eye(soldier), (0, 0.8, -1.1 if melee else 5))
                        run.step({soldier: (*angles, button)})
                        if weapon == "helix":
                            rocket, = fight.rockets
                            self.assertIs(rocket.shooter, soldier)
                        elif not melee:
                            self.assertEqual(ammo.value(), before_ammo - 1)
                        else:
                            self.assertEqual(ammo.value(), before_ammo)
                        for _ in range(25):
                            run.step({})
                        self.assertTrue(active(protector))
                        self.assertEqual(protected_pool.total, protected_before)
                        expected = damage if melee or hero == defenses.REAPER else 0
                        self.assertEqual(rear_pool.total, rear_before - expected)
                        self.assertEqual(soldier.stats.value(stats.HERO_DAMAGE), expected)
                        charge = script.component.entity_var(342).value()
                        self.assertEqual(charge, 0 if weapon == "visor" else expected)
                        notices = soldier.client.messages(combat.HIT)
                        self.assertEqual({msg["+0x78"]["+0x0"]["+0x0"] for msg in notices},
                                         {actor.entity} if expected else set())
                        self.assertFalse(fight.rockets)

    def test_actual_invulnerability_body_routing_for_four_incoming_npc_weapons(self):
        for key in ("1", "2", "3", "5"):
            for hero, activation, active in self.protections:
                with self.subTest(npc=self.catalog[key].name, hero=hero):
                    run, soldier, protector, actor = self.phase_arena(key, hero, incoming=True)
                    self.activate_protection(run, protector, activation, active)
                    fight = run.match.combat
                    soldier_pool, protected_pool = self.pool(run, soldier), self.pool(run, protector)
                    protected_before = protected_pool.total
                    # Record calls through both hitscan and projectile paths without replacing resolution.
                    with (patch.object(fight, "_shoot", wraps=fight._shoot) as volleys,
                          patch.object(fight, "_direct_hit", wraps=fight._direct_hit) as contacts):
                        actor.attack_target, actor.firing = soldier.entity, True
                        for _ in range(170):
                            run.step({})
                            if volleys.called:
                                break
                        else:
                            self.fail("the NPC did not fire its actual weapon")
                        actor.firing = False
                        for _ in range(25):
                            run.step({})
                    self.assertTrue(all(call.args[0] is actor for call in volleys.call_args_list))
                    hit_owners = {call.args[1].owner for call in contacts.call_args_list}
                    self.assertTrue(active(protector))
                    self.assertEqual(protected_pool.total, protected_before)
                    if hero == defenses.REAPER:
                        self.assertEqual(hit_owners, {soldier})
                        self.assertLess(soldier_pool.total, 200)
                    else:
                        self.assertEqual(hit_owners, {protector})
                        self.assertEqual(soldier_pool.total, 200)

    def orisa_arena(self, key, *, incoming, visor=False, deployed=True, allied=False):
        run, soldier, actor = fixture.SoldierPveStaticTests.arena(self, key, (0, 0, 5), 1)
        service = run.match.combat.orisa_barriers
        if service.geometry is None:
            self.skipTest("original Orisa collision descriptor is required")
        team = soldier.team if incoming or allied else actor.team
        orisa = run.match.add_player(2, "Orisa", defenses.ORISA, team, False)
        in_world(run.match, orisa)
        orisa.client = fixture.NpcRecordingClient(orisa)
        run.players.append(orisa)
        self.assertTrue(run.match.switch_hero(orisa, heroes()[defenses.ORISA]))
        soldier.mover.position = [0, 0, -3]
        orisa.mover.position = [1.2, 0, -3 if incoming else 5]
        for player in run.players:
            player.body_script.ult_seconds = 0
        run.step({})
        if visor:
            script = soldier.body_script
            script.set_ult_charge(script.component.entity_var(338).value())
            run.step({soldier: (0, 0, 0x20)})
            for _ in range(80):
                run.step({})
        yaw = 0 if incoming else -32768
        run.step({orisa: (yaw, 8192, 16 if deployed else 0)})
        for _ in range(45):
            if service.actors:
                break
            run.step({orisa: (yaw, 8192, 0)})
        shield = service.actors.get(orisa.body)
        if deployed:
            self.assertEqual(shield.health, 600)
        return run, soldier, orisa, actor, shield

    def test_orisa_mesh_protects_all_five_actual_pve_recipients(self):
        for key, definition in self.catalog.items():
            for weapon, button, loss in (("rifle", 1, 20), ("helix", 2, 120), ("visor", 1, 20)):
                with self.subTest(npc=definition.name, weapon=weapon):
                    run, soldier, orisa, actor, shield = self.orisa_arena(
                        key, incoming=False, visor=weapon == "visor",
                    )
                    before = self.pool(run, actor).total
                    orisa_before = self.pool(run, orisa).total
                    angles = aim(run.match.combat._eye(soldier), (0, 0.8, 5))
                    run.step({soldier: (*angles, button)})
                    for _ in range(30):
                        run.step({})
                    self.assertEqual(shield.health, 600 - loss)
                    self.assertEqual(self.pool(run, actor).total, before)
                    self.assertEqual(self.pool(run, orisa).total, orisa_before)
                    self.assertFalse(soldier.client.messages(combat.HIT))
                    self.assertEqual(soldier.stats.value(stats.HERO_DAMAGE), 0)

    def test_orisa_mesh_protects_soldier_against_four_actual_npc_weapons(self):
        for key in ("1", "2", "3", "5"):
            for deployed in (True, False):
                with self.subTest(npc=self.catalog[key].name, deployed=deployed):
                    run, soldier, orisa, actor, shield = self.orisa_arena(
                        key, incoming=True, deployed=deployed,
                    )
                    soldier_pool = self.pool(run, soldier)
                    orisa_before = self.pool(run, orisa).total
                    actor.attack_target, actor.firing = soldier.entity, True
                    for _ in range(180):
                        run.step({})
                        if soldier_pool.total < 200 or (shield is not None and shield.health < 600):
                            break
                    if deployed:
                        self.assertLess(shield.health, 600)
                        self.assertEqual(soldier_pool.total, 200)
                    else:
                        self.assertLess(soldier_pool.total, 200)
                    self.assertEqual(self.pool(run, orisa).total, orisa_before)

    def test_visor_breaks_orisa_mesh_then_hits_all_five_pve_bodies(self):
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                run, soldier, _, actor, shield = self.orisa_arena(key, incoming=False, visor=True)
                shield.health = 10  # Fixture isolates the breaking shot.
                pool = self.pool(run, actor)
                initial = pool.total
                angles = aim(run.match.combat._eye(soldier), (-1.1, 0.8, 5))
                run.step({soldier: (*angles, 1)})
                self.assertEqual(shield.health, 0)
                self.assertEqual(pool.total, initial)
                self.assertEqual(soldier.stats.value(stats.HERO_DAMAGE), 0)
                for _ in range(15):
                    run.step({})
                run.step({soldier: (*angles, 1)})
                self.assertEqual(pool.total, initial - 20)
                self.assertEqual(soldier.stats.value(stats.HERO_DAMAGE), 20)
                for _ in range(400):
                    run.step({})
                run.step({soldier: (*angles, 1)})
                self.assertEqual(pool.total, initial - 20)

    def test_orisa_mesh_breaking_helix_preserves_all_five_npc_pools_and_motion(self):
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                run, soldier, _, actor, shield = self.orisa_arena(key, incoming=False)
                shield.health = 100  # Isolate the breaking event on the real landed actor.
                before, position = self.pool(run, actor).total, actor.position
                angles = aim(run.match.combat._eye(soldier), (0, 0.8, 5))
                run.step({soldier: (*angles, 2)})
                for _ in range(35):
                    run.step({})
                self.assertEqual(shield.health, 0)
                self.assertEqual(self.pool(run, actor).total, before)
                self.assertEqual(actor.position, position)
                self.assertIsNone(actor.impulse_mover)
                self.assertFalse(soldier.client.messages(combat.HIT))

    def test_allied_orisa_mesh_passes_soldier_rifle_to_all_five_npcs(self):
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                run, soldier, _, actor, shield = self.orisa_arena(key, incoming=False, allied=True)
                before = self.pool(run, actor).total
                angles = aim(run.match.combat._eye(soldier), (0, 0.8, 5))
                run.step({soldier: (*angles, 1)})
                self.assertEqual(shield.health, 600)
                self.assertEqual(self.pool(run, actor).total, before - 20)

    def winston_arena(self, key, *, incoming, visor=False, same_side=False):
        position = (0, 0, -0.5 if same_side else 5)
        run, soldier, actor = fixture.SoldierPveStaticTests.arena(self, key, position, 1)
        team = soldier.team if incoming else actor.team
        winston = run.match.add_player(2, "Winston", winston_barrier.HERO, team, False)
        in_world(run.match, winston)
        winston.client = fixture.NpcRecordingClient(winston)
        run.players.append(winston)
        self.assertTrue(run.match.switch_hero(winston, heroes()[winston_barrier.HERO]))
        soldier.mover.position = [0, 0, -3]
        winston.mover.position = [2, 0, -3 if incoming else 5]
        for player in run.players:
            player.body_script.ult_seconds = 0
        run.step({})
        if visor:
            script = soldier.body_script
            script.set_ult_charge(script.component.entity_var(338).value())
            run.step({soldier: (0, 0, 0x20)})
            for _ in range(80):
                run.step({})
        fight = run.match.combat
        run.step({winston: (0, 0, 16)})
        for _ in range(45):
            if fight.winston_barriers.actors:
                break
            run.step({})
        dome, = fight.winston_barriers.actors.values()
        self.assertEqual(dome.health, 700)
        return run, soldier, winston, actor, dome

    def test_winston_dome_protects_all_five_npc_bodies_from_soldier(self):
        for key, definition in self.catalog.items():
            for weapon, button, damage in (("rifle", 1, 20), ("helix", 2, 120), ("visor", 1, 20)):
                with self.subTest(npc=definition.name, weapon=weapon):
                    run, soldier, winston, actor, dome = self.winston_arena(
                        key, incoming=False, visor=weapon == "visor",
                    )
                    before = self.pool(run, actor).total
                    eye = run.match.combat._eye(soldier)
                    angles = aim(eye, (0, 0.8, 5))
                    run.step({soldier: (*angles, button)})
                    for _ in range(24):
                        run.step({})
                    self.assertEqual(dome.health, 700 - damage)
                    self.assertEqual(self.pool(run, actor).total, before)
                    self.assertEqual(self.pool(run, winston).total, 500)
                    self.assertFalse(soldier.client.messages(combat.HIT))
                    self.assertEqual(soldier.stats.value(stats.HERO_DAMAGE), 0)

    def test_winston_dome_protects_soldier_from_four_actual_npc_weapons(self):
        for key in ("1", "2", "3", "5"):
            for inside in (False, True):
                with self.subTest(npc=self.catalog[key].name, inside=inside):
                    run, soldier, winston, actor, dome = self.winston_arena(
                        key, incoming=True, same_side=inside,
                    )
                    actor.attack_target, actor.firing = soldier.entity, True
                    soldier_pool = self.pool(run, soldier)
                    for _ in range(180):
                        run.step({})
                        if dome.health < 700 or soldier_pool.total < 200:
                            break
                    if inside:
                        self.assertEqual(dome.health, 700)
                        self.assertLess(soldier_pool.total, 200)
                    else:
                        self.assertLess(dome.health, 700)
                        self.assertEqual(soldier_pool.total, 200)
                    self.assertEqual(self.pool(run, winston).total, 500)

    def test_breaking_winston_dome_shields_all_five_npcs_from_same_helix(self):
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                run, soldier, _, actor, dome = self.winston_arena(key, incoming=False)
                # Set up the last 100 HP. The full pool break is covered by the
                # player duel with actual rifle damage before the Helix.
                dome.health = 100
                before, position = self.pool(run, actor).total, actor.position
                angles = aim(run.match.combat._eye(soldier), (0, 0.8, 5))
                run.step({soldier: (*angles, 2)})
                for _ in range(28):
                    run.step({})
                self.assertEqual(dome.health, 0)
                self.assertTrue(dome.retired)
                self.assertEqual(self.pool(run, actor).total, before)
                self.assertEqual(actor.position, position)
                self.assertIsNone(actor.impulse_mover)
                self.assertFalse(soldier.client.messages(combat.HIT))

    def projected_arena(self, key, *, incoming, slot2=False, position=(0, 0, 3), visor=False, npc_team=1):
        run, soldier, actor = fixture.SoldierPveStaticTests.arena(self, key, position, npc_team)
        team = soldier.team if incoming else actor.team
        zarya = run.match.add_player(2, "Zarya", zarya_barrier.HERO, team, False)
        in_world(run.match, zarya)
        zarya.client = fixture.NpcRecordingClient(zarya)
        run.players.append(zarya)
        run.match.switch_hero(zarya, heroes()[zarya_barrier.HERO])
        soldier.mover.position = [0, 0, -3]
        zarya.mover.position = [3, 0, 0]
        zarya.body_script.ult_seconds = 0
        soldier.body_script.ult_seconds = 0
        run.step({})
        if visor:
            script = soldier.body_script
            script.set_ult_charge(script.component.entity_var(338).value())
            run.step({soldier: (0, 0, 0x20)})
            for _ in range(80):
                run.step({})
        if slot2:
            actor.body_script.component.edges([(16, True), (16, False)])
        elif incoming and key == "3":
            actor.attack_target, actor.firing = soldier.entity, True
            for _ in range(55):
                run.step({})
            self.assertEqual(self.pool(run, soldier).total, 200)
        fight = run.match.combat
        target = soldier if incoming else actor
        shape = next(item for item in fight._targets() if item.owner is target)
        a, b = shape.segment()
        centre = tuple((x + y) * .5 for x, y in zip(a, b, strict=True))
        angles = aim(fight._eye(zarya), centre)
        run.step({zarya: (*angles, 16)})
        self.assertEqual(fight.zarya_barriers.projected[zarya.body][1:], (200, shape.entity))
        return run, soldier, zarya, actor

    def test_field_canister_and_healing_check_projected_barrier_team_on_all_five_npcs(self):
        for key, definition in self.catalog.items():
            for allied in (False, True):
                with self.subTest(npc=definition.name, allied=allied):
                    run, soldier, zarya, actor = self.projected_arena(
                        key, incoming=False, position=(0, 0, 1), npc_team=0 if allied else 1,
                    )
                    soldier.mover.position = [0, 0, -.78 if key == "5" else -.8]
                    soldier.mover.body = None
                    fight = run.match.combat
                    service = fight.zarya_barriers
                    energy = zarya.body_script.component.entity_var(87)
                    initial_energy = energy.value()
                    npc_pool = self.pool(run, actor)
                    npc_pool.damage(20)
                    npc_health = npc_pool.total
                    recipient = actor
                    if not allied:
                        recipient = run.match.add_player(3, "Ally", soldier.hero.guid, soldier.team, False)
                        in_world(run.match, recipient)
                        recipient.client = fixture.NpcRecordingClient(recipient)
                        run.players.append(recipient)
                        self.assertTrue(run.match.switch_hero(recipient, soldier.hero))
                        recipient.mover.position = [1.4, 0, 1.5]
                        recipient.mover.body = None
                        self.pool(run, recipient).damage(20)
                    pool = self.pool(run, recipient)
                    before = pool.total
                    run.step({soldier: (0, 0, 16)})
                    for _ in range(60):
                        run.step({})
                        if fight.rockets:
                            break
                    canister, = fight.rockets
                    field = canister.shot
                    self.assertEqual(field.spawn, projectiles.FIELD_ENTITY)
                    origin, direction, reach = first_canister_sweep(canister)
                    intersection = service.intersect(origin, direction, service.centre(actor))
                    self.assertIsNotNone(intersection)
                    self.assertLess(intersection, reach)
                    contact = service.trace(soldier, origin, direction, reach)
                    if allied:
                        self.assertIsNone(contact)
                    else:
                        self.assertIsNotNone(contact)
                        self.assertIs(contact[1], actor)
                    for _ in range(60):
                        run.step({})
                        if field.field_tick is not None and run.frame > field.field_tick:
                            break
                    self.assertIsNotNone(field.field_tick)
                    travelled = canister.distance
                    if allied:
                        self.assertGreater(travelled, intersection)
                    else:
                        self.assertNotIn(canister, fight.rockets)
                        self.assertAlmostEqual(travelled, intersection, places=5)
                        self.assertEqual(npc_pool.total, npc_health)
                    self.assertEqual(service.projected[zarya.body][1:], (200, actor.entity))
                    self.assertEqual(energy.value(), initial_energy)
                    self.assertIs(field.player, soldier)
                    self.assertEqual(field.body, soldier.body)
                    self.assertAlmostEqual(pool.total - before, 35 * combat.TICK)
                    self.assertFalse(soldier.client.messages(combat.HIT))

    def test_projected_barrier_covers_all_five_actual_npcs_against_soldier(self):
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                run, soldier, zarya, actor = self.projected_arena(key, incoming=False)
                before = self.pool(run, actor).total
                yaw, pitch = aim(run.match.combat._eye(soldier), (0.0, 0.8, 3.0))
                run.step({soldier: (yaw, pitch, 1)})
                self.assertEqual(self.pool(run, actor).total, before)
                self.assertEqual(run.match.combat.zarya_barriers.projected[zarya.body][1], 180)
                self.assertEqual(zarya.body_script.component.entity_var(87).value(), 4)

    def test_projected_barrier_protects_the_viewed_melee_contact_on_each_npc(self):
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                run, soldier, zarya, actor = self.projected_arena(key, incoming=False, position=(0, 0, -1.5))
                before = self.pool(run, actor).total
                for _ in range(12):
                    run.step({})
                actor.position = (0, 0, 2)
                run.frame += 1
                run.match.update(run.frame * combat.TICK, run.frame)
                soldier.take_commands([Command(run.frame, buttons=0x800, extra=128)])
                self.assertEqual(
                    run.match.combat.zarya_barriers.projected[zarya.body][1:], (170, actor.entity),
                )
                self.assertEqual(zarya.body_script.component.entity_var(87).value(), 6)
                self.assertEqual(self.pool(run, actor).total, before)
                self.assertIsNone(actor.impulse_mover)
                self.assertFalse(soldier.client.messages(combat.HIT))

    def test_projected_barrier_protects_the_viewed_rifle_contact_on_each_npc(self):
        for key, definition in self.catalog.items():
            for visor in (False, True):
                with self.subTest(npc=definition.name, visor=visor):
                    run, soldier, zarya, actor = self.projected_arena(key, incoming=False, visor=visor)
                    before = self.pool(run, actor).total
                    for _ in range(12):
                        run.step({})
                    actor.position = (30, 0, 3)
                    run.frame += 1
                    run.match.update(run.frame * combat.TICK, run.frame)
                    soldier.take_commands([Command(run.frame, buttons=1, extra=128)])
                    self.assertEqual(
                        run.match.combat.zarya_barriers.projected[zarya.body][1:], (180, actor.entity),
                    )
                    self.assertEqual(zarya.body_script.component.entity_var(87).value(), 4)
                    self.assertEqual(self.pool(run, actor).total, before)
                    self.assertFalse(soldier.client.messages(combat.HIT))

    def test_held_visor_breaks_projection_kills_each_npc_and_retargets_a_player(self):
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                run, soldier, zarya, actor = self.projected_arena(key, incoming=False, visor=True)
                rear = run.match.add_player(3, "Second target", soldier.hero.guid, actor.team, False)
                in_world(run.match, rear)
                rear.client = fixture.NpcRecordingClient(rear)
                run.players.append(rear)
                self.assertTrue(run.match.switch_hero(rear, soldier.hero))
                rear.mover.position = [-2.5, 0, 4]
                rear.mover.body = None
                rear.body_script.ult_seconds = 0
                fight = run.match.combat
                pool = self.pool(run, actor)
                # Isolate one lethal body shot after the full barrier pool is spent.
                pool.damage(pool.total - 15)
                rear_pool = self.pool(run, rear)
                ammo = soldier.body_script.component.instances[11].vars[53]
                rounds = ammo.value()
                for _ in range(10):
                    run.step({})
                visor = next(
                    i for i in soldier.body_script.component.instances.values() if i.graph.index == 0x0259
                )
                self.assertTrue(visor.states[7].active)
                self.assertIn(zarya.body, fight.zarya_barriers.projected)
                for _ in range(110):
                    run.step({soldier: (0, 0, 1)})
                    if zarya.body not in fight.zarya_barriers.projected:
                        break
                self.assertNotIn(zarya.body, fight.zarya_barriers.projected)
                self.assertEqual(rounds - ammo.value(), 10)
                self.assertEqual(pool.total, 15)
                self.assertEqual(rear_pool.total, 200)
                self.assertEqual(zarya.body_script.component.entity_var(87).value(), 40)
                self.assertFalse(soldier.client.messages(combat.HIT))
                for _ in range(12):
                    run.step({soldier: (0, 0, 1)})
                    if actor.dead:
                        break
                self.assertTrue(actor.dead)
                self.assertEqual(pool.killer, soldier.body)
                self.assertEqual(rounds - ammo.value(), 11)
                self.assertEqual(rear_pool.total, 200)
                for _ in range(12):
                    run.step({soldier: (0, 0, 1)})
                    if rear_pool.total < 200:
                        break
                self.assertEqual(rear_pool.total, 180)
                self.assertEqual(rounds - ammo.value(), 12)
                self.assertIn(actor, run.match.bots)
                self.assertTrue(any(entity == actor.entity for entity, _ in rear.client.bot_deaths))
                self.assertEqual(len(soldier.client.messages(combat.HIT)), 2)
                self.assertEqual(soldier.body_script.component.entity_var(342).value(), 0)

    def test_projected_barrier_protects_soldier_from_four_actual_npc_weapons(self):
        for key, slot2 in (("1", False), ("2", False), ("3", False), ("3", True), ("5", False)):
            with self.subTest(npc=self.catalog[key].name, slot2=slot2):
                run, soldier, zarya, actor = self.projected_arena(key, incoming=True, slot2=slot2)
                fight = run.match.combat
                actor.attack_target, actor.firing = soldier.entity, True
                for _ in range(115):
                    run.step({})
                    if fight.zarya_barriers.projected.get(zarya.body, (None, 0))[1] < 200:
                        break
                self.assertGreater(fight.zarya_barriers.projected[zarya.body][1], 0)
                self.assertLess(fight.zarya_barriers.projected[zarya.body][1], 200)
                self.assertEqual(self.pool(run, soldier).total, 200)
                if slot2:
                    self.assertEqual(fight.zarya_barriers.projected[zarya.body][1], 195)
                    self.assertEqual(zarya.body_script.component.entity_var(87).value(), 1)

    def arena(self, key, *, incoming=False):
        run, soldier, actor = fixture.SoldierPveStaticTests.arena(self, key,
                                                               (0, 0, 6 if incoming else 3), 1)
        rein = run.match.add_player(2, "Rein", reinhardt_barrier.HERO, 1, False)
        in_world(run.match, rein)
        rein.client = fixture.NpcRecordingClient(rein)
        run.players.append(rein)
        run.match.switch_hero(rein, heroes()[reinhardt_barrier.HERO])
        rein.team = soldier.team if incoming else 1 - soldier.team
        rein.mover.position[:] = [1, 0, 3.5] if incoming else [.8, 0, 1]
        self.facing, self.incoming = (0 if incoming else -32768), incoming
        self.actor = actor
        for player in run.players:
            player.mover.position = list(player.mover.position)
            player.mover.body = None
            player.body_script.ult_seconds = 0
        for _ in range(20):
            self.step(run, soldier, rein, held=0)
        return run, soldier, rein, actor

    def angles(self, run, soldier):
        if self.incoming:
            return 0, 0
        x, y, z = self.actor.position
        return aim(run.match.combat._eye(soldier), (x, y + .8, z))

    def step(self, run, soldier, rein, buttons=0, *, held=2):
        run.step({soldier: (*self.angles(run, soldier), buttons), rein: (self.facing, 0, held)})

    def test_all_five_real_npcs_require_enemy_raised_mesh_to_block_soldier_attacks(self):
        for key, definition in self.catalog.items():
            for weapon, amount in (("rifle", 20), ("helix", 120), ("visor", 20)):
                for mode in ("raised_enemy", "lowered_enemy", "raised_ally"):
                    with self.subTest(npc=definition.name, weapon=weapon, mode=mode):
                        run, soldier, rein, actor = self.arena(key)
                        if mode == "raised_ally":
                            rein.team = soldier.team
                        held = 0 if mode == "lowered_enemy" else 2
                        if weapon == "visor":
                            soldier.body_script.set_ult_charge(soldier.body_script.component.vars[338].value())
                            self.step(run, soldier, rein, 0x20, held=0)
                            for _ in range(80):
                                self.step(run, soldier, rein, held=0)
                        for _ in range(5):
                            self.step(run, soldier, rein, held=held)
                        target = next(t for t in run.match.combat._targets() if t.owner is rein)
                        a, b = target.segment()
                        self.assertIsNone(combat.ray_capsule(run.match.combat._eye(soldier),
                                                           combat.aim(*self.angles(run, soldier)),
                                                           a, b, target.radius))
                        before = self.pool(run, actor).total
                        self.step(run, soldier, rein, 2 if weapon == "helix" else 1, held=held)
                        for _ in range(30):
                            self.step(run, soldier, rein, held=held)
                        protected = mode == "raised_enemy"
                        self.assertEqual(before - self.pool(run, actor).total, 0 if protected else amount)
                        self.assertEqual(1600 - self.shield(run, rein).current, amount if protected else 0)
                        if protected:
                            self.assertEqual(soldier.stats.value(stats.HERO_DAMAGE), 0)
                            self.assertEqual(soldier.body_script.component.entity_var(342).value(), 0)
                            self.assertEqual(self.pool(run, rein).total, 500)

    def test_four_actual_npc_controller_weapons_hit_soldier_when_offset_shield_is_lowered(self):
        for key, amount in (("1", 20), ("2", 50), ("3", 100), ("5", 5)):
            for raised in (True, False):
                with self.subTest(npc=self.catalog[key].name, raised=raised):
                    run, soldier, rein, actor = self.arena(key, incoming=True)
                    held = 2 if raised else 0
                    for _ in range(5):
                        self.step(run, soldier, rein, held=held)
                    actor.attack_target, actor.firing = soldier.entity, True
                    for _ in range(180):
                        self.step(run, soldier, rein, held=held)
                        if ((self.pool(run, soldier).total < 200 or self.shield(run, rein).current < 1600)
                                and not any(r.shooter is actor for r in run.match.combat.rockets)):
                            break
                    actor.firing = False
                    exposed = 28 if key == "2" else amount
                    self.assertEqual(self.pool(run, soldier).total, 200 if raised else 200 - exposed)
                    self.assertEqual(1600 - self.shield(run, rein).current, amount if raised else 0)
                    self.assertEqual(self.pool(run, rein).total, 500)

    def test_breaking_helix_preserves_all_five_npc_pools_and_motion_for_same_explosion(self):
        for key, definition in self.catalog.items():
            with self.subTest(npc=definition.name):
                run, soldier, rein, actor = self.arena(key)
                # Explicit resource override isolates breaking-impact behavior.
                # The default1600 capacity is separately exhausted by actual rifle input.
                self.instance(rein).write((0, 587), 100)
                for _ in range(5):
                    self.step(run, soldier, rein)
                self.assertEqual(self.shield(run, rein).current, 100)
                before, position = self.pool(run, actor).total, actor.position
                self.step(run, soldier, rein, 2)
                for _ in range(30):
                    self.step(run, soldier, rein)
                self.assertEqual(self.shield(run, rein).current, 0)
                self.assertEqual(self.pool(run, actor).total, before)
                self.assertEqual(actor.position, position)
                self.assertIsNone(actor.impulse_mover)
                self.assertEqual(soldier.stats.value(stats.HERO_DAMAGE), 0)
