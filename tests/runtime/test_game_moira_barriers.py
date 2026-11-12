"""Actual raised Reinhardt mesh versus Moira commands; external descriptor required."""

import os
import unittest
from unittest.mock import patch

import test_game_moira as fixtures
import test_game_moira_charge as charge_tests
from test_game_combat import Run, aim, in_world

from ow174.game import combat
from ow174.game.collision import World
from ow174.game.content import SOLDIER, find_hero, heroes
from ow174.game.script import expr
from ow174.game.script.driver import ULT_COST

REINHARDT = 0x02E0000000000007
BARRIER = os.environ.get('ORPHEUS_REINHARDT_COLLISION')


@unittest.skipUnless(BARRIER, 'prepared external104319 barrier descriptor required')
class MoiraBarrierTests(unittest.TestCase):
    def fight(self, *, ally_barrier=False, ally_target=True, distance=4):
        run = Run(names=('Moira', 'Barrier', 'Recipient'))
        match = run.match
        match.bots.clear()
        match._pending_bot_scripts.clear()
        source, rein, recipient = run.players
        match.switch_hero(source, heroes()[fixtures.MOIRA])
        match.switch_hero(rein, heroes()[REINHARDT])
        rein.team = source.team if ally_barrier else 1 - source.team
        recipient.team = source.team if ally_target else 1 - source.team
        x, y, z = source.mover.position
        rein.mover.position = [x, y, z + distance]
        recipient.mover.position = [x, y, z + 7]
        for _ in range(20):
            self.step(run, rein, raised=False)
        for _ in range(20):
            self.step(run, rein)
        shield = match.combat.barriers[rein.body]
        self.assertTrue(shield.active)
        targets = {t.owner: t for t in match.combat._targets()}
        health = [match.combat.vitals_of(targets[p]) for p in run.players]
        health[2].damage(100)
        charge_tests.MoiraCombatChargeTests.reset(source)
        return run, source, rein, recipient, shield, health

    @staticmethod
    def step(run, rein, source=None, button=0, *, raised=True):
        commands = {rein: (-32768, 0, 2 if raised else 0)}
        if source is not None:
            commands[source] = (0, 0, button)
        run.step(commands)

    def start_ult(self, run, source, rein):
        source.body_script.set_ult_charge(source.body_script.component.entity_var(ULT_COST).value())
        self.step(run, rein, source, 32)
        for _ in range(60):
            instance = next(i for i in source.body_script.component.instances.values()
                            if i.graph.index == 0x1007)
            if instance.states.get(20) is not None and instance.states[20].active:
                return
            self.step(run, rein)
        self.fail('ultimate failed to enter authored beam phase')

    def test_enemy_barrier_stops_spray_without_damage_or_trailing_heal(self):
        run, source, rein, recipient, shield, health = self.fight()
        for _ in range(40):
            self.step(run, rein, source, 1)
        self.assertEqual(health[2].total, 100)
        self.assertNotIn(recipient.body, run.match.combat.moira_hots)
        self.assertEqual(shield.current, 1600)

    def test_nano_scales_grasp_barrier_damage_but_owner_reduction_does_not(self):
        import test_game_moira_nano as nano_tests

        case = nano_tests.MoiraNanoTests()
        for mode, expected in (('outgoing', 24), ('incoming', 16)):
            with self.subTest(mode=mode):
                run, source, rein, _, shield, health = self.fight(ally_target=False)
                recipient = source if mode == 'outgoing' else rein
                caster = case.add_ana(run, recipient)
                case.cast(run, caster, recipient)
                caster.mover.position[0] += 30
                for _ in range(20):
                    self.step(run, rein)
                self.assertTrue(shield.active)
                before = shield.current
                body = health[1].total
                for _ in range(20):
                    self.step(run, rein, source, 2)
                self.assertAlmostEqual(before - shield.current, expected, places=2)
                self.assertEqual(health[1].total, body)

    def test_actual_reinhardt_barrier_intercepts_sleep_before_owner_and_protected_moira(self):
        from ow174.game.content import find_hero

        run, source, rein, recipient, shield, _ = self.fight(ally_target=False)
        self.assertTrue(run.match.switch_hero(source, find_hero('ana')))
        self.assertTrue(run.match.switch_hero(recipient, find_hero('moira')))
        x, y, z = rein.mover.position
        source.mover.position = [x, y, z - 4]
        recipient.mover.position = [x, y, z + 3]
        source.mover.body = recipient.mover.body = None
        source.team, recipient.team = 1 - rein.team, rein.team
        for _ in range(20):
            self.step(run, rein)
        targets = {t.owner: t for t in run.match.combat._targets()}
        owner = run.match.combat.vitals_of(targets[rein])
        protected = run.match.combat.vitals_of(targets[recipient])
        before = owner.total, protected.total, shield.current
        self.step(run, rein, source, 8)
        for _ in range(50):
            self.step(run, rein)
        self.assertEqual((owner.total, protected.total), before[:2])
        self.assertEqual(owner.sleep_until, 0)
        self.assertEqual(protected.sleep_until, 0)
        self.assertTrue(shield.active)
        self.assertAlmostEqual(before[2] - shield.current, 5, places=2)

    def test_sleep_barrier_order_respects_nearer_wall_ally_and_lowered_shield(self):
        from ow174.game.content import find_hero

        for mode in ('wall', 'ally', 'lowered'):
            with self.subTest(mode=mode):
                run, source, rein, recipient, shield, _ = self.fight(ally_target=False)
                self.assertTrue(run.match.switch_hero(source, find_hero('ana')))
                self.assertTrue(run.match.switch_hero(recipient, find_hero('moira')))
                x, y, z = source.mover.position
                rein.mover.position = [x + 1.5, y, z + 4]  # mesh covers ray; owner capsule does not
                recipient.mover.position = [x, y, z + 7]
                source.mover.body = recipient.mover.body = rein.mover.body = None
                source.team, recipient.team = 0, 1
                rein.team = 0 if mode == 'ally' else 1
                for _ in range(20):
                    self.step(run, rein, raised=mode != 'lowered')
                fight = run.match.combat
                targets = {t.owner: t for t in fight._targets()}
                owner = fight.vitals_of(targets[rein])
                protected = fight.vitals_of(targets[recipient])
                before = shield.current
                wall = World([(x - 10, y - 10, z + 1.8, x + 10, y - 10, z + 1.8,
                               x + 10, y + 10, z + 1.8),
                              (x - 10, y - 10, z + 1.8, x + 10, y + 10, z + 1.8,
                               x - 10, y + 10, z + 1.8)])
                collision = wall if mode == 'wall' else fight._collision()
                with patch.object(fight, '_collision', return_value=collision):
                    self.step(run, rein, source, 8, raised=mode != 'lowered')
                    for _ in range(50):
                        self.step(run, rein, raised=mode != 'lowered')
                self.assertEqual(owner.sleep_until, 0)
                self.assertEqual(shield.current, before)
                self.assertEqual(protected.total, 200 if mode == 'wall' else 195)
                self.assertEqual(fight.is_asleep(recipient), mode != 'wall')

    def test_allied_barrier_passes_spray_to_multiple_allies(self):
        run, source, rein, _recipient, shield, health = self.fight(ally_barrier=True)
        health[1].damage(100)
        before = health[1].total
        for _ in range(40):
            self.step(run, rein, source, 1)
        self.assertGreater(health[1].total, before)
        self.assertGreater(health[2].total, 100)
        self.assertEqual(shield.current, 1600)

    def test_grasp_spends_damage_on_barrier_without_body_hit_self_heal_charge_or_refill(self):
        run, source, rein, _recipient, shield, health = self.fight(ally_target=False)
        health[0].damage(100)
        weapon = next(i for i in source.body_script.component.instances.values() if i.graph.index == 0xF76)
        weapon.write((expr.INSTANCE, 87), 20.0)
        before = health[1].total
        for _ in range(20):
            self.step(run, rein, source, 2)
        self.assertAlmostEqual(shield.current, 1584, places=2)
        self.assertEqual((health[0].total, health[1].total, health[2].total), (100, before, 100))
        self.assertEqual(charge_tests.MoiraCombatChargeTests.charge(source), 0)
        self.assertLess(weapon.find_var(expr.INSTANCE, 87).value(), 21)

    def test_coalescence_heals_and_damages_through_barrier_without_damaging_it(self):
        for ally in (False, True):
            with self.subTest(ally=ally):
                run, source, rein, _recipient, shield, health = self.fight(ally_target=ally)
                self.start_ult(run, source, rein)
                before = health[2].total
                for _ in range(20):
                    self.step(run, rein)
                self.assertAlmostEqual(health[2].total - before, 44.8 if ally else -22.4, places=4)
                self.assertEqual(shield.current, 1600)

    def test_both_orbs_pass_barrier_mesh_without_spending_energy_on_it(self):
        for healing in (False, True):
            with self.subTest(healing=healing):
                run, source, rein, _recipient, shield, health = self.fight(ally_target=healing)
                self.step(run, rein, source, 16)
                for _ in range(40):
                    self.step(run, rein, source, 1 if healing else 2)
                    if run.match.combat.moira_orbs:
                        break
                orb = run.match.combat.moira_orbs[-1]
                contact = run.match.combat._barrier_contact(source, orb.position, orb.direction, 15)
                self.assertIsNotNone(contact)
                plane = orb.position[2] + contact[0]
                for _ in range(60):
                    self.step(run, rein)
                self.assertGreater(orb.position[2], plane)
                if healing:
                    self.assertGreater(health[2].total, 100)
                else:
                    self.assertLess(health[2].total, 100)
                self.assertEqual(shield.current, 1600)

    def test_quick_melee_records_actual_mesh_and_existing_sphere_query_boundary(self):
        for distance, expected_shield in ((2, 1600), (2.5, 1570)):
            with self.subTest(distance=distance):
                run, source, rein, _recipient, shield, health = self.fight(distance=distance)
                before = health[1].total
                self.step(run, rein, source, 0x800)
                for _ in range(30):
                    self.step(run, rein)
                # At2m the reconstructed forward shield is before the sphere's
                # near edge; the body is still inside the swing. This records
                # an open native-sweep/attachment limitation, not full acceptance.
                self.assertEqual(shield.current, expected_shield)
                self.assertEqual(before - health[1].total, 25)  # inherited armour policy

    def test_grasp_breaks_barrier_and_held_raise_recovers_after_authored_delay(self):
        run, source, rein, _recipient, shield, health = self.fight()
        # Explicit near-break suffix; damage, cooldown and recovery still run
        # through real source and defender command streams.
        shield.current = .4
        before = health[1].total
        self.step(run, rein, source, 2)
        self.assertEqual(shield.current, 0)
        self.assertFalse(shield.active)
        self.assertEqual(health[1].total, before)
        for _ in range(300):
            self.step(run, rein)
        self.assertFalse(shield.active)
        for _ in range(30):
            self.step(run, rein)
        self.assertTrue(shield.active)
        self.assertGreater(shield.current, 0)

    def test_death_and_disconnect_lower_barrier(self):
        for disconnect in (False, True):
            with self.subTest(disconnect=disconnect):
                run, source, rein, _recipient, shield, health = self.fight()
                if disconnect:
                    run.match.leave(rein)
                    self.assertNotIn(rein.body, run.match.combat.barriers)
                    self.assertFalse(list(run.match.combat._enemy_barriers(source)))
                else:
                    # Beam pierces barrier; a near-death owner suffix exercises
                    # the actual damage/death callback rather than a dead flag.
                    self.start_ult(run, source, rein)
                    health[1].damage(health[1].total - 1)
                    for _ in range(20):
                        self.step(run, rein)
                    self.assertTrue(health[1].dead)
                    run.match.combat._sync_barrier(rein)
                    self.assertFalse(shield.active)

    def test_retirement_discards_old_barrier_resource(self):
        run, _source, rein, _recipient, _shield, _health = self.fight()
        old = rein.body
        self.assertTrue(run.match.switch_hero(rein, heroes()[SOLDIER]))
        self.assertNotIn(old, run.match.combat.barriers)

    def test_static_wall_prevents_melee_damaging_barrier_mesh(self):
        run, source, rein, _recipient, shield, health = self.fight(distance=2.5)
        x, y, z = source.mover.position
        z += .3
        wall = World([
            (x - 10, y - 5, z, x + 10, y - 5, z, x + 10, y + 5, z),
            (x - 10, y - 5, z, x + 10, y + 5, z, x - 10, y + 5, z),
        ])
        before = health[1].total
        with patch.object(run.match.combat, '_collision', return_value=wall):
            self.step(run, rein, source, 0x800)
            for _ in range(30):
                self.step(run, rein)
        self.assertEqual(shield.current, 1600)
        self.assertEqual(health[1].total, before)


    def deploy_wall(self, run, source, rein, distance, *, enemy=True):
        match = run.match
        fight = match.combat
        x, y, z = source.mover.position
        mei = find_hero("mei")
        team = rein.team if enemy else source.team
        caster = match.add_player(4, "Mei", mei.guid, team, False)
        in_world(match, caster)
        run.players.append(caster)
        self.assertTrue(match.switch_hero(caster, mei))
        caster.team = team
        caster.mover.position = [x, y, z - 6]
        caster.mover.body = None
        for _ in range(25):
            self.step(run, rein)
        direction = aim(fight._eye(caster), (x, y, z + distance))
        for button in (16, 0, 1, 0):
            run.step({rein: (-32768, 0, 2), caster: (*direction, button)})
        self.assertEqual(len(fight.walls.pieces), 5)
        pillar = min(fight.walls.pieces.values(), key=lambda p: abs(p.feet[0] - x))
        return caster, direction, pillar

    def test_actual_ice_wall_occludes_melee_shield_witnesses_until_cancel(self):
        for enemy in (False, True):
            with self.subTest(enemy=enemy):
                run, source, rein, _, shield, health = self.fight(distance=2.5)
                fight = run.match.combat
                caster, direction, pillar = self.deploy_wall(run, source, rein, 1.05, enemy=enemy)
                origin, forward = fight._eye(source), combat.aim(0, 0)
                shield_at = fight._barrier_contact(source, origin, forward, 4)[0]
                self.assertLess(fight.walls.cast(origin, forward, 4)[0], shield_at)
                before = health[1].total
                self.step(run, rein, source, 0x800)
                for _ in range(90):
                    self.step(run, rein)
                self.assertEqual(shield.current, 1600)
                self.assertEqual(health[1].total, before)
                self.assertEqual(pillar.health, 370 if enemy else 400)
                run.step({rein: (-32768, 0, 2), caster: (*direction, 16)})
                self.assertFalse(fight.walls.pieces)
                self.step(run, rein, source, 0x800)
                for _ in range(30):
                    self.step(run, rein)
                self.assertEqual(shield.current, 1570)
                self.assertEqual(health[1].total, before - 25)

    def test_actual_rifle_stops_at_nearer_ice_wall_or_shield(self):
        for wall_first in (False, True):
            with self.subTest(wall_first=wall_first):
                run, source, rein, recipient, shield, _ = self.fight(ally_target=False)
                x, y, z = source.mover.position
                self.assertTrue(run.match.switch_hero(source, heroes()[SOLDIER]))
                self.assertTrue(run.match.switch_hero(recipient, heroes()[fixtures.MOIRA]))
                source.mover.position = [x, y, z]
                rein.mover.position = [x + 1.5, y, z + 4]
                recipient.mover.position = [x, y, z + 7]
                for player in run.players:
                    player.mover.body = None
                fight = run.match.combat
                caster, direction, pillar = self.deploy_wall(run, source, rein, 1.5 if wall_first else 5.5)
                origin, forward = fight._eye(source), combat.aim(0, 0)
                self.assertEqual(fight.walls.cast(origin, forward, 8)[0]
                                 < fight._barrier_contact(source, origin, forward, 8)[0], wall_first)
                target = next(t for t in fight._targets() if t.owner is recipient)
                health = fight.vitals_of(target)
                for _ in range(12):
                    self.step(run, rein, source, 1)
                self.assertEqual(health.total, 200)
                self.assertEqual(shield.current < 1600, not wall_first)
                self.assertEqual(pillar.health < 400, wall_first)
                run.step({caster: (*direction, 16)})
                self.assertFalse(fight.walls.pieces)
                for _ in range(16):
                    self.step(run, rein, source, 1, raised=False)
                self.assertLess(health.total, 200)

    def test_actual_grenade_cover_blocks_health_and_status(self):
        for cover in ("enemy shield", "allied shield", "ice wall"):
            for friendly in (False, True):
                with self.subTest(cover=cover, friendly=friendly):
                    run, source, rein, recipient, shield, _ = self.fight()
                    x, y, z = source.mover.position
                    self.assertTrue(run.match.switch_hero(source, find_hero("ana")))
                    self.assertTrue(run.match.switch_hero(recipient, find_hero("moira")))
                    rein.team = source.team if cover == "allied shield" else 1 - source.team
                    recipient.team = source.team if friendly else 1 - source.team
                    for player, position in ((source, [x, y, z]), (rein, [x + 1.5, y, z + 4]),
                                             (recipient, [x, y, z + 4.6])):
                        player.mover.position = position
                        player.mover.body = None
                    fight = run.match.combat
                    pillar = self.deploy_wall(run, source, rein, 1.8)[2] if cover == "ice wall" else None
                    for _ in range(20):
                        self.step(run, rein)
                    target = next(t for t in fight._targets() if t.owner is recipient)
                    health = fight.vitals_of(target)
                    health.damage(100)
                    shot_aim = aim(fight._eye(source), (x, y + target.height / 2, z + 4.6))
                    run.step({source: (*shot_aim, 16), rein: (-32768, 0, 2)})
                    for _ in range(25):
                        self.step(run, rein)
                    blocked = cover != "allied shield"
                    self.assertEqual(health.total, 100 if blocked else 200 if friendly else 50)
                    self.assertEqual(health.healing_boost_until > fight.now, not blocked and friendly)
                    self.assertEqual(health.healing_block_until > fight.now, not blocked and not friendly)
                    self.assertEqual(shield.current, 1550 if cover == "enemy shield" else 1600)
                    if pillar is not None:
                        self.assertEqual(pillar.health, 350)

    def test_actual_helix_explodes_on_first_cover_without_splash_through(self):
        for first in ("shield", "pillar", "body"):
            with self.subTest(first=first):
                run, source, rein, recipient, shield, _ = self.fight(ally_target=False)
                x, y, z = source.mover.position
                self.assertTrue(run.match.switch_hero(source, heroes()[SOLDIER]))
                self.assertTrue(run.match.switch_hero(recipient, heroes()[fixtures.MOIRA]))
                source.mover.position = [x, y, z]
                rein.mover.position = [x + 1.5, y, z + 4]
                recipient.mover.position = [x, y, z + 4.6]
                for player in run.players:
                    player.mover.body = None
                fight = run.match.combat
                pillar = None
                if first != "body":
                    _, _, pillar = self.deploy_wall(run, source, rein, 1.8 if first == "pillar" else 6)
                for _ in range(20):
                    self.step(run, rein, raised=first != "body")
                health = fight.vitals_of(next(t for t in fight._targets() if t.owner is recipient))
                self.step(run, rein, source, 2, raised=first != "body")
                for _ in range(30):
                    self.step(run, rein, raised=first != "body")
                self.assertEqual(shield.current, 1480 if first == "shield" else 1600)
                if pillar is not None:
                    self.assertEqual(pillar.health, 280 if first == "pillar" else 400)
                self.assertEqual(health.total, 80 if first == "body" else 200)

    def test_actual_sleep_dart_stops_at_nearer_ice_wall_or_shield(self):
        for wall_first in (False, True):
            with self.subTest(wall_first=wall_first):
                run, source, rein, recipient, shield, _ = self.fight(ally_target=False)
                x, y, z = source.mover.position
                self.assertTrue(run.match.switch_hero(source, find_hero("ana")))
                self.assertTrue(run.match.switch_hero(recipient, find_hero("moira")))
                source.mover.position = [x, y, z]
                recipient.mover.position = [x, y, z + 7]
                source.mover.body = recipient.mover.body = None
                fight = run.match.combat
                _, _, pillar = self.deploy_wall(run, source, rein, 1.5 if wall_first else 5.5)
                origin, forward = fight._eye(source), combat.aim(0, 0)
                self.assertEqual(fight.walls.cast(origin, forward, 8)[0]
                                 < fight._barrier_contact(source, origin, forward, 8)[0], wall_first)
                targets = {target.owner: target for target in fight._targets()}
                health = fight.vitals_of(targets[recipient])
                self.step(run, rein, source, 8)
                for _ in range(50):
                    self.step(run, rein)
                self.assertEqual(health.total, 200)
                self.assertFalse(fight.is_asleep(recipient))
                self.assertEqual((health.sleep_until, health.sleep_wake_after), (0.0, 0.0))
                self.assertAlmostEqual(pillar.health, 395 if wall_first else 400, places=2)
                self.assertAlmostEqual(shield.current, 1600 if wall_first else 1595, places=2)


    def test_actual_grasp_damages_only_the_nearer_ice_wall_or_shield(self):
        for wall_first in (False, True):
            with self.subTest(wall_first=wall_first):
                run, source, rein, _, shield, health = self.fight(ally_target=False)
                _, _, pillar = self.deploy_wall(run, source, rein, 1.5 if wall_first else 5.5)
                health[0].damage(100)
                charge_tests.MoiraCombatChargeTests.reset(source)
                weapon = next(i for i in source.body_script.component.instances.values()
                              if i.graph.index == 0xF76)
                weapon.write((expr.INSTANCE, 87), 20.0)
                before = tuple(pool.total for pool in health)
                for _ in range(20):
                    self.step(run, rein, source, 2)
                self.assertAlmostEqual(pillar.health, 384 if wall_first else 400, places=2)
                self.assertAlmostEqual(shield.current, 1600 if wall_first else 1584, places=2)
                self.assertEqual(tuple(pool.total for pool in health), before)
                self.assertEqual(charge_tests.MoiraCombatChargeTests.charge(source), 0)
                self.assertLess(weapon.find_var(expr.INSTANCE, 87).value(), 21)
