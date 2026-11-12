"""Actual Sombra alternate input and Zen's reconstructed Hack receiver."""

import unittest

import test_zenyatta_emp as emp_fixture
from test_game_combat import aim
from test_game_mover import box

from ow174.game import combat, sombra_emp, sombra_hack
from ow174.game.barriers import PROJECTED
from ow174.game.collision import World
from ow174.game.commands import FIRE, SECONDARY
from ow174.game.content import heroes
from ow174.game.zenyatta import DISCORD, HARMONY, HERO


class ZenyattaHackTests(unittest.TestCase):
    def setUp(self):
        self.fixture = emp_fixture.ZenyattaEmpTests()

    def channel(self, sombra):
        cast, = self.fixture.instances(sombra, sombra_hack.CAST)
        return cast.states.get(cast.graph.node(100).state)

    def step_hack(self, run, zen, sombra):
        fight = run.match.combat
        angle = aim(fight._eye(sombra), fight._eye(zen))
        run.step({sombra: (*angle, SECONDARY)})

    def start(self, run, zen, sombra):
        for _ in range(60):
            self.step_hack(run, zen, sombra)
            channel = self.channel(sombra)
            if channel is not None and channel.active:
                return channel
        self.fail("actual Hack did not start its channel")

    def apply(self, run, zen, sombra):
        channel = self.start(run, zen, sombra)
        self.assertEqual(channel.ms, 650)
        for _ in range(60):
            self.step_hack(run, zen, sombra)
            effects = [i for i in self.fixture.instances(zen, sombra_emp.EFFECT)
                       if sombra_hack.direct_effect(i)]
            if effects:
                break
        effect, = effects
        self.assertFalse(channel.active)
        wait = effect.node_state(effect.graph.node(118))
        self.assertTrue(wait.active)
        self.assertEqual(wait.ms, 5000)
        return effect, wait

    def test_hack_locks_zen_skills_and_preserves_shields_and_return_fire(self):
        run, zen, sombra, zarya = self.fixture.arena()
        fight = run.match.combat
        owner = fight.zenyatta.owner(zen)
        old_clock = owner.last_damage
        cost = self.fixture.fill(zen)
        effect, wait = self.apply(run, zen, sombra)
        self.assertEqual(self.fixture.hp(run, zen).total, 200)
        self.assertEqual(owner.last_damage, old_clock)
        self.assertFalse(self.fixture.instances(zen, sombra_emp.CHILD))
        run.step({zen: (*aim(fight._eye(zen), fight._eye(zarya)), HARMONY)})
        run.step({zen: (0, 0, FIRE | DISCORD | 0x20)})
        for _ in range(30):
            run.step({})
        self.assertEqual(self.fixture.charge(zen), cost)
        self.assertIsNone(owner.harmony)
        self.assertIsNone(owner.discord)
        self.assertFalse(fight.zenyatta.ultimate(zen))
        hit, = zen.client.messages(combat.HIT)
        self.assertEqual(hit["+0x78"]["+0x0"]["+0x0"], sombra.body)
        self.assertTrue(wait.active)
        for _ in range(320):
            run.step({})
            if not wait.active:
                break
        self.assertFalse(wait.active)
        self.assertTrue(effect.stopped)
        run.step({zen: (0, 0, 0x20)})
        self.assertTrue(fight.zenyatta.ultimate(zen))

    def test_actual_zen_orb_damage_aborts_the_hack_channel(self):
        run, zen, sombra, _ = self.fixture.arena()
        fight = run.match.combat
        channel = self.start(run, zen, sombra)
        direction = aim(fight._eye(zen), fight._eye(sombra))
        run.step({zen: (*direction, FIRE), sombra: (*aim(fight._eye(sombra), fight._eye(zen)), SECONDARY)})
        for _ in range(55):
            self.step_hack(run, zen, sombra)
        self.assertFalse(channel.active)
        hit, = zen.client.messages(combat.HIT)
        self.assertEqual(hit["+0x78"]["+0x0"]["+0x0"], sombra.body)
        self.assertEqual(hit["+0x78"]["+0x4"], 96)
        self.assertFalse(self.fixture.instances(zen, sombra_emp.EFFECT))
        self.assertEqual(self.fixture.hp(run, zen).total, 200)

    def test_lethal_orb_cancels_pending_hack_but_applied_wait_survives_caster_death(self):
        for applied in (False, True):
            with self.subTest(applied=applied):
                run, zen, sombra, _ = self.fixture.arena()
                fight = run.match.combat
                cost = self.fixture.fill(zen)
                source_body = sombra.body
                vitals = self.fixture.hp(run, sombra)
                vitals.now[combat.HEALTH_POOL] = 96
                fight._sync_body_health(sombra, vitals)
                if applied:
                    effect, wait = self.apply(run, zen, sombra)
                else:
                    channel = self.start(run, zen, sombra)

                fire = aim(fight._eye(zen), fight._eye(sombra))
                run.step({zen: (*fire, FIRE)})
                for _ in range(16):
                    run.step({})
                    if vitals.dead:
                        break
                self.assertTrue(vitals.dead)
                self.assertEqual(vitals.killer, zen.body)
                notice, = zen.client.messages(combat.HIT)
                self.assertEqual(notice["+0x78"]["+0x0"]["+0x0"], source_body)
                self.assertEqual(notice["+0x78"]["+0x8"], combat.CRITICAL | combat.KILLING)
                if not applied:
                    self.assertFalse(channel.active)
                    for _ in range(50):
                        run.step({})
                    self.assertFalse(self.fixture.instances(zen, sombra_emp.EFFECT))
                    continue

                deadline = wait.start + wait.ms
                self.assertTrue(wait.active)
                self.assertTrue(run.match.switch_hero(sombra, heroes()[sombra_emp.SOMBRA]))
                self.assertNotEqual(sombra.body, source_body)
                run.step({zen: (0, 0, 0x20)})
                self.assertTrue(wait.active)
                self.assertEqual(wait.start + wait.ms, deadline)
                self.assertFalse(fight.zenyatta.ultimate(zen))
                self.assertEqual(self.fixture.charge(zen), cost)
                for _ in range(320):
                    run.step({})
                    if not wait.active:
                        break
                self.assertFalse(wait.active)
                self.assertTrue(effect.stopped)
                run.step({zen: (0, 0, 0x20)})
                self.assertTrue(fight.zenyatta.ultimate(zen))

    def test_hack_rejects_ineligible_targets_and_aborts_when_cover_appears(self):
        for case in ("range", "team", "wall", "mid-channel wall", "projection", "transcendence"):
            with self.subTest(case=case):
                run, zen, sombra, zarya = self.fixture.arena(distance=17 if case == "range" else 6)
                fight = run.match.combat
                channel = self.start(run, zen, sombra) if case == "mid-channel wall" else None
                if case == "team":
                    sombra.team = zen.team
                if "wall" in case:
                    x, y, z = zen.mover.position
                    floor = box(x-30, x+30, y-2, y, z-30, z+30)
                    run.match.collision_world = World(floor + box(x-3, x+3, y-1, y+4, z+2, z+3))
                if case == "projection":
                    run.step({zarya: (*aim(fight._eye(zarya), fight._eye(zen)), HARMONY)})
                    self.assertEqual(fight.barriers.bubbles[zarya.body, PROJECTED].target, zen.body)
                if case == "transcendence":
                    self.fixture.fill(zen)
                    run.step({zen: (0, 0, 0x20)})
                    self.assertTrue(fight.zenyatta.ultimate(zen))
                for _ in range(60):
                    self.step_hack(run, zen, sombra)
                if channel is not None:
                    self.assertFalse(channel.active)
                else:
                    self.assertIsNone(self.channel(sombra))
                self.assertFalse(self.fixture.instances(zen, sombra_emp.EFFECT))

    def test_projection_on_caster_absorbs_zen_hit_but_projection_on_target_breaks_channel(self):
        for caster_protected in (False, True):
            with self.subTest(caster_protected=caster_protected):
                run, zen, sombra, zarya = self.fixture.arena()
                fight = run.match.combat
                channel = self.start(run, zen, sombra)
                protected = sombra if caster_protected else zen
                zarya.team = protected.team
                aim_bubble = aim(fight._eye(zarya), fight._eye(protected))
                aim_hack = aim(fight._eye(sombra), fight._eye(zen))
                run.step({zarya: (*aim_bubble, HARMONY), sombra: (*aim_hack, SECONDARY)})
                bubble = fight.barriers.bubbles[zarya.body, PROJECTED]
                self.assertEqual(bubble.target, protected.body)
                if caster_protected:
                    fire = aim(fight._eye(zen), fight._eye(sombra))
                    run.step({zen: (*fire, FIRE), sombra: (*aim_hack, SECONDARY)})
                for _ in range(55):
                    self.step_hack(run, zen, sombra)
                self.assertFalse(channel.active)
                effects = self.fixture.instances(zen, sombra_emp.EFFECT)
                if caster_protected:
                    self.assertLess(bubble.hp, 200)
                    effect, = effects
                    self.assertTrue(sombra_hack.direct_effect(effect))
                else:
                    self.assertEqual(bubble.hp, 200)
                    self.assertFalse(effects)
                self.assertEqual(self.fixture.hp(run, sombra).total, 200)
                self.assertEqual(self.fixture.hp(run, zen).total, 200)
                self.assertFalse(zen.client.messages(combat.HIT))

    def test_transcendence_started_during_hack_channel_prevents_application(self):
        run, zen, sombra, _ = self.fixture.arena()
        fight = run.match.combat
        channel = self.start(run, zen, sombra)
        self.fixture.fill(zen)
        angle = aim(fight._eye(sombra), fight._eye(zen))
        run.step({zen: (0, 0, 0x20), sombra: (*angle, SECONDARY)})
        self.assertTrue(fight.zenyatta.ultimate(zen))
        self.assertEqual(self.fixture.charge(zen), 0)
        self.assertFalse(channel.active)
        for _ in range(55):
            self.step_hack(run, zen, sombra)
        self.assertFalse(self.fixture.instances(zen, sombra_emp.EFFECT))
        self.assertTrue(fight.zenyatta.ultimate(zen))
        self.assertEqual(self.fixture.hp(run, zen).total, 200)

    def test_emp_expiry_does_not_clear_a_newer_direct_hack(self):
        run, zen, sombra, _ = self.fixture.arena()
        fight = run.match.combat
        self.fixture.fill(sombra)
        run.step({sombra: (0, 0, 0x20)})
        for _ in range(65):
            run.step({})
        emp_effect, = self.fixture.instances(zen, sombra_emp.EFFECT)
        child, = self.fixture.instances(zen, sombra_emp.CHILD)
        chase = child.node_state(child.graph.node(4))
        self.assertTrue(chase.active)

        hack_effect, wait = self.apply(run, zen, sombra)
        self.assertIsNot(hack_effect, emp_effect)
        for _ in range(320):
            run.step({})
            if not chase.active:
                break
        self.assertFalse(chase.active)
        self.assertTrue(emp_effect.stopped)
        self.assertTrue(wait.active)
        self.assertEqual(self.fixture.instances(zen, sombra_emp.EFFECT), [hack_effect])

        cost = self.fixture.fill(zen)
        run.step({zen: (0, 0, 0x20)})
        self.assertEqual(self.fixture.charge(zen), cost)
        self.assertFalse(fight.zenyatta.ultimate(zen))

        old_body = zen.body
        self.assertTrue(run.match.switch_hero(zen, heroes()[HERO]))
        self.assertNotEqual(zen.body, old_body)
        self.assertFalse(self.fixture.instances(zen, sombra_emp.EFFECT))
        self.fixture.fill(zen)
        run.step({zen: (0, 0, 0x20)})
        self.assertTrue(fight.zenyatta.ultimate(zen))
