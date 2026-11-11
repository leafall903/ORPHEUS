"""104319 Barrier Field interactions with actual Mei and Reinhardt input."""

import unittest
from dataclasses import replace

import test_mei_combat as fixtures
import test_mei_wall as wall_fixtures
from test_game_combat import in_world

from ow174.game import combat, reinhardt_barrier, replay_manifest
from ow174.game.commands import FIRE
from ow174.game.content import heroes


@unittest.skipUnless(
    reinhardt_barrier.configured_geometry(), "external 104319 Barrier Field descriptor required",
)
class MeiReinhardtBarrierTests(unittest.TestCase):
    def test_replay_configuration_seals_barrier_descriptor(self):
        geometry = reinhardt_barrier.configured_geometry()
        config = replay_manifest.runtime_configuration(quanta_us=16000, run_options={})
        self.assertEqual(config["ow174.game.reinhardt_barrier.descriptor_sha256"], geometry.fingerprint)

    def scene(self, distance=5.):
        run, owner, rein, _target = fixtures.MeiCombatTests().duel()
        fight = run.match.combat
        self.assertTrue(run.match.switch_hero(rein, heroes()[reinhardt_barrier.HERO]))
        x, y, z = owner.mover.position
        fight.walls.place_player(rein, (x, y, z + distance))
        for _ in range(30):
            run.step({})
        for _ in range(35):
            run.step({rein: (-32768, 0, 2)})
        state = fight.reinhardt_barriers.states[rein.body]
        self.assertTrue(state.active)
        self.assertEqual(state.current, 1600.)
        target = next(t for t in fight._targets() if t.owner is rein)
        return run, owner, rein, target, state

    def fire(self, run, owner, rein, target, button, *, held=True):
        fight = run.match.combat
        x, y, z = target.feet
        aim = fixtures.aim(fight._eye(owner), (x, y + 1., z))
        for frame in range(45):
            attack = frame < (20 if button == 2 else 1)
            run.step({owner: (*aim, button if attack else 0), rein: (-32768, 0, 2 if held else 0)})

    def test_raised_barrier_intercepts_spray_and_icicle_until_released(self):
        run, owner, rein, target, barrier = self.scene()
        fight = run.match.combat
        vitals = fight.vitals_of(target)
        self.assertEqual(vitals.total, 500.)
        self.fire(run, owner, rein, target, FIRE)
        self.assertEqual(barrier.current, 1597.25)
        self.fire(run, owner, rein, target, 2)
        self.assertEqual(barrier.current, 1522.25)
        self.assertEqual(vitals.total, 500.)
        self.assertIsNone(fight.frost(rein))
        self.assertEqual(owner.client.messages(combat.HIT), [])

        self.fire(run, owner, rein, target, 2, held=False)
        self.assertFalse(barrier.active)
        self.assertEqual(barrier.current, 1522.25)
        self.assertEqual(vitals.total, 430.)
        self.assertEqual(len(owner.client.messages(combat.HIT)), 1)

    def test_icicle_resolves_ice_wall_and_barrier_in_distance_order(self):
        for distance, shield_health in ((2., 1600.), (7., 1525.)):
            with self.subTest(wall_distance=distance):
                run, owner, rein, target, barrier = self.scene()
                fight = run.match.combat
                walls = wall_fixtures.WallTests()
                pillars, aim = walls.place(run, owner, distance=distance)
                self.assertEqual(len(pillars), 5)
                for _ in range(35):
                    run.step({rein: (-32768, 0, 2)})
                self.assertTrue(barrier.active)

                self.fire(run, owner, rein, target, 2)
                self.assertEqual(barrier.current, shield_health)
                self.assertEqual(fight.vitals_of(target).total, 500.)
                self.assertTrue(all(pillar.health == 400. for pillar in pillars))

                walls.tap(run, owner, 16, aim)
                self.assertFalse(fight.walls.pieces)
                for _ in range(35):
                    run.step({rein: (-32768, 0, 2)})
                self.assertTrue(barrier.active)
                self.fire(run, owner, rein, target, 2)
                self.assertEqual(barrier.current, shield_health - 75.)
                self.assertEqual(fight.vitals_of(target).total, 500.)
                self.assertEqual(owner.client.messages(combat.HIT), [])

    def test_barrier_breaking_icicle_does_not_reach_body(self):
        run, owner, rein, target, barrier = self.scene()
        fight = run.match.combat
        barrier.current = 10.  # Start with the shield already damaged.
        self.fire(run, owner, rein, target, 2)
        self.assertEqual(barrier.current, 0.)
        self.assertFalse(barrier.active)
        self.assertEqual(fight.vitals_of(target).total, 500.)
        self.assertEqual(owner.client.messages(combat.HIT), [])
        self.fire(run, owner, rein, target, 2)
        self.assertFalse(barrier.active)
        self.assertEqual(fight.vitals_of(target).total, 430.)
        instance = fight.reinhardt_barriers.instance(rein)
        cooldown = instance.states[59]
        self.assertGreater(cooldown.remaining(), 0.)
        deadline = barrier.broken_until
        for frame in range(350):
            if fight.now >= deadline:
                break
            run.step({rein: (-32768, 0, 2 if frame % 2 else 0)})
            if fight.now < deadline:
                self.assertFalse(barrier.active)
        self.assertGreaterEqual(fight.now, deadline)
        self.assertGreater(barrier.current, 0.)
        run.step({})
        for _ in range(35):
            run.step({rein: (-32768, 0, 2)})
        self.assertTrue(barrier.active)

    def test_allied_barrier_does_not_block_icicle_to_rear_enemy(self):
        run, owner, rein, target, barrier = self.scene()
        fight = run.match.combat
        run.match.game_map = replace(run.match.game_map, free_for_all=False)
        rein.team = owner.team
        rear = run.match.add_player(17, "Rear", rein.hero.guid, 1 - owner.team, False)
        in_world(run.match, rear)
        run.players.append(rear)
        self.assertTrue(run.match.switch_hero(rear, rein.hero))
        x, y, z = target.feet
        fight.walls.place_player(rear, (x, y, z + 4.))
        rear_target = next(t for t in fight._targets() if t.owner is rear)
        self.fire(run, owner, rein, rear_target, 2)
        self.assertEqual(barrier.current, 1600.)
        self.assertEqual(fight.vitals_of(target).total, 500.)
        self.assertEqual(fight.vitals_of(rear_target).total, 430.)

    def test_close_melee_contacts_body_and_shield(self):
        run, owner, rein, target, barrier = self.scene(distance=2.2)
        self.fire(run, owner, rein, target, 0x800)
        self.assertEqual(barrier.current, 1570.)
        self.assertEqual(run.match.combat.vitals_of(target).total, 475.)

    def test_death_and_hero_switch_retire_raised_barrier(self):
        for death in (False, True):
            with self.subTest(death=death):
                run, owner, rein, target, _barrier = self.scene()
                fight = run.match.combat
                old_body = rein.body
                if death:
                    fight._damage(owner, target, 1000., False, None, run.frame)
                    self.assertTrue(fight.is_dead(rein))
                else:
                    self.assertTrue(run.match.switch_hero(rein, owner.hero))
                    self.assertNotEqual(rein.body, old_body)
                for _ in range(4):
                    run.step({})
                    self.assertNotIn(old_body, fight.reinhardt_barriers.states)

    def test_blizzard_damages_through_barrier_and_freeze_lowers_it(self):
        run, owner, rein, target, barrier = self.scene()
        fight = run.match.combat
        vitals = fight.vitals_of(target)
        owner.body_script.set_ult_charge(owner.body_script.component.entity_var(338).value())
        angle = fixtures.aim(fight._eye(owner), target.feet)
        for frame in range(140):
            run.step({owner: (*angle, 32 if frame == 0 else 0), rein: (-32768, 0, 2)})
            if vitals.total < 500.:
                break
        self.assertLess(vitals.total, 500.)
        self.assertTrue(barrier.active)
        self.assertEqual(barrier.current, 1600.)
        self.assertIsNotNone(fight.frost(rein))
        for _ in range(180):
            if fight.frozen(rein) and not barrier.active:
                break
            run.step({rein: (-32768, 0, 2)})
        self.assertTrue(fight.frozen(rein))
        self.assertFalse(barrier.active)
        self.assertEqual(barrier.current, 1600.)
