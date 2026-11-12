"""Build 104319 authored NPC difficulty pools against actual Zen weapon commands."""

import os
import unittest

import test_npc_shields_delivery as npc_fixture
from test_game_combat import aim

from ow174.game import combat, npc_control, world
from ow174.game.body_vm_snapshot import export_component_vm, snapshot_digest
from ow174.game.commands import FIRE, SECONDARY
from ow174.game.mover import mover_data

PROFILES = {
    "Trooper": ((0, 0, 0, 0), (0, 0, 0, 0)),
    "Enforcer": ((0, 150, 250, 250), (0, 50, 100, 200)),
    "Sniper": ((0, 150, 200, 100), (0, 0, 100, 400)),
    "Assassin": ((0, 350, 600, 400), (0, 0, 0, 300)),
    "Heavy Assault": ((2000, 3000, 5000, 7000), (0, 0, 0, 0)),
}


@unittest.skipUnless(os.environ.get("ORPHEUS_NPC_DATA"), "needs explicit prepared NPC data")
class ZenyattaNpcDifficultyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        npc_fixture.ZenyattaNpcTests.setUpClass()
        cls.addClassCleanup(npc_fixture.ZenyattaNpcTests.tearDownClass)

    def test_each_authored_difficulty_pool_takes_an_actual_zen_orb(self):
        fixture = npc_fixture.ZenyattaNpcTests()
        for definition in fixture.catalog.values():
            health, shields = PROFILES[definition.name]
            for tier in range(4):
                with self.subTest(npc=definition.name, tier=tier):
                    run, zen, bot, vitals = fixture.arena(definition, difficulty_index=tier)
                    component = bot.body_script.component
                    self.assertEqual(component.entity_var(9948).value(), tier)
                    self.assertEqual(vitals.most[combat.HEALTH_POOL], definition.health)
                    self.assertEqual(vitals.most[combat.SHIELDS_POOL], definition.shields)
                    self.assertEqual(vitals.pool_maximum(combat.HEALTH_POOL), definition.health+health[tier])
                    self.assertEqual(vitals.pool_maximum(combat.SHIELDS_POOL),
                                     definition.shields+shields[tier])
                    extra_pools = ((combat.HEALTH_POOL, health[tier]), (combat.SHIELDS_POOL, shields[tier]))
                    for kind, amount in extra_pools:
                        if amount:
                            pool = vitals.conditional_pools[kind]
                            self.assertNotIn(pool.pool_id, vitals.ids.values())
                            wire = vitals.component(vitals.most)[51][kind*world.POOLS+1]
                            self.assertEqual(wire, world.pool(pool.pool_id, kind, amount, amount))
                    expected = combat.Vitals(0, definition.health+health[tier],
                                             definition.armour, definition.shields+shields[tier])
                    dealt = expected.damage(48)
                    run.step({zen: (0, 600, FIRE)})
                    for _ in range(20):
                        run.step({})
                    self.assertEqual(vitals.total, expected.total)
                    hit, = zen.client.messages(combat.HIT)
                    self.assertEqual(hit["+0x78"]["+0x0"]["+0x0"], bot.entity)
                    self.assertEqual(hit["+0x78"]["+0x4"], dealt)

    def test_each_spawn_tier_executes_actual_incoming_damage(self):
        fixture = npc_fixture.ZenyattaNpcTests()
        incoming = {
            "Trooper": (20, 20, 20, 20),
            "Enforcer": (28, 56, 84, 112),
            "Sniper": (100, 100, 100, 100),
            "Assassin": (25, 25, 25, 25),
            "Heavy Assault": (5, 5, 5, 5),
        }
        for definition in fixture.catalog.values():
            for tier, expected in enumerate(incoming[definition.name]):
                with self.subTest(npc=definition.name, tier=tier):
                    run, zen, bot, _ = fixture.arena(definition, difficulty_index=tier)
                    fight = run.match.combat
                    if definition.name == "Assassin":
                        x, y, z = zen.mover.position
                        bot.position = (x, y, z+2)
                        centre = (x, y+mover_data(bot.body).stand_height*.5, z+2)
                        run.step({zen: (*aim(fight._eye(zen), centre), 0)})
                    self.assertEqual(npc_control.start(run.match, zen), bot.entity)
                    target = next(t for t in fight._targets() if t.owner is zen)
                    vitals = fight.vitals_of(target)
                    for _ in range(260):
                        run.step({})
                        if vitals.total < 200:
                            break
                    if definition.name == "Enforcer":
                        for _ in range(8):
                            run.step({})
                    if definition.name == "Assassin":
                        self.assertEqual(fight.pins[bot.entity], zen.body)
                    self.assertEqual(vitals.total, 200-expected)

    def test_each_difficulty_pool_takes_actual_discord_melee_and_charged_orbs(self):
        fixture = npc_fixture.ZenyattaNpcTests()
        for definition in fixture.catalog.values():
            health, shields = PROFILES[definition.name]
            for tier in range(4):
                for attack in ("melee", "volley"):
                    with self.subTest(npc=definition.name, tier=tier, attack=attack):
                        run, zen, bot, vitals = fixture.arena(definition, difficulty_index=tier)
                        expected = combat.Vitals(0, definition.health+health[tier],
                                                 definition.armour, definition.shields+shields[tier])
                        run.step({zen: (0, 0, 0x08)})
                        owner = run.match.combat.zenyatta.owner(zen)
                        self.assertEqual(owner.discord.target, bot.entity)
                        charge = zen.body_script.component.entity_var(342)
                        initial_charge, initial_frame = charge.value(), run.frame
                        before = vitals.total
                        if attack == "melee":
                            x, y, z = zen.mover.position
                            bot.position = (x, y, z+1.5)
                            run.step({zen: (0, 0, 0x800)})
                            for _ in range(30):
                                run.step({})
                            damage = [expected.damage(37.5)]
                        else:
                            for _ in range(130):
                                run.step({zen: (0, 600, SECONDARY)})
                            for _ in range(55):
                                run.step({zen: (0, 600, 0)})
                            damage = [value for _ in range(5) if (value := expected.damage(60))]
                            weapon, = (i for i in zen.body_script.component.instances.values()
                                       if i.graph.index == 0x0164)
                            self.assertEqual(weapon.vars[53].value(), 15)
                        hits = [n["+0x78"] for n in zen.client.messages(combat.HIT)]
                        self.assertEqual([(h["+0x0"]["+0x0"], h["+0x4"]) for h in hits],
                                         [(bot.entity, round(value)) for value in damage])
                        self.assertEqual(vitals.total, expected.total)
                        self.assertAlmostEqual(charge.value()-initial_charge,
                                               before-vitals.total+(run.frame-initial_frame)*.08, places=2)

    def test_enforcer_regenerates_its_conditional_shields_after_an_actual_hit(self):
        fixture = npc_fixture.ZenyattaNpcTests()
        run, zen, bot, vitals = fixture.arena(fixture.catalog["2"], difficulty_index=3)
        shield = vitals.conditional_pools[combat.SHIELDS_POOL]
        self.assertEqual(shield.now, 200)
        component = bot.body_script.component
        start_digest = snapshot_digest(export_component_vm(component))
        run.step({zen: (0, 600, FIRE)})
        for _ in range(20):
            run.step({})
        self.assertEqual(shield.now, 152)
        self.assertEqual(vitals.now[combat.SHIELDS_POOL], 100)
        self.assertNotEqual(snapshot_digest(export_component_vm(component)), start_digest)
        for _ in range(180):
            run.step({})
        self.assertGreater(shield.now, 152)
        self.assertLess(shield.now, 200)
        for _ in range(110):
            run.step({})
        self.assertEqual(shield.now, 200)
        self.assertEqual(vitals.pool_total(combat.SHIELDS_POOL), 300)
        self.assertEqual(vitals.pool_total(combat.HEALTH_POOL), 500)

    def test_harmony_and_transcendence_heal_each_actual_difficulty_profile(self):
        fixture = npc_fixture.ZenyattaNpcTests()
        for definition in fixture.catalog.values():
            for tier in range(4):
                with self.subTest(npc=definition.name, tier=tier):
                    run, zen, bot, vitals = fixture.arena(definition, difficulty_index=tier)
                    fight = run.match.combat
                    shield_capacity = vitals.pool_maximum(combat.SHIELDS_POOL)
                    kind = combat.SHIELDS_POOL if shield_capacity else combat.HEALTH_POOL
                    maximum = vitals.pool_maximum(kind)
                    extras = getattr(vitals, "conditional_pools", {})
                    base = vitals.now[kind]
                    run.step({zen: (0, 600, FIRE)})
                    for _ in range(25):
                        run.step({})
                    self.assertEqual(vitals.pool_total(kind), maximum-48)
                    if kind in extras:
                        self.assertEqual(extras[kind].now, extras[kind].most-48)
                        self.assertEqual(vitals.now[kind], base)

                    bot.team = zen.team
                    run.step({zen: (0, 0, 0x10)})
                    self.assertEqual(fight.zenyatta.owner(zen).harmony.target, bot.entity)
                    charge = zen.body_script.component.entity_var(342)
                    before = vitals.pool_total(kind)
                    start_charge = charge.value()
                    for _ in range(10):
                        run.step({})
                    self.assertAlmostEqual(vitals.pool_total(kind)-before, 4.8, places=5)
                    self.assertAlmostEqual(charge.value()-start_charge, 5.6, places=3)

                    cost = zen.body_script.component.entity_var(338).value()
                    zen.body_script.set_ult_charge(cost)
                    run.step({zen: (0, 0, 0x20)})
                    self.assertTrue(fight.zenyatta.ultimate(zen))
                    before = vitals.pool_total(kind)
                    for _ in range(3):
                        run.step({})
                    self.assertAlmostEqual(vitals.pool_total(kind)-before, 15.84, places=5)
                    for _ in range(12):
                        run.step({})
                    self.assertEqual(vitals.total, vitals.maximum)
                    self.assertEqual(charge.value(), 0)
                    if kind in extras:
                        self.assertEqual(extras[kind].now, extras[kind].most)
                        self.assertEqual(vitals.now[kind], base)

    def test_shield_recovery_adds_healing_without_crediting_zen_charge(self):
        fixture = npc_fixture.ZenyattaNpcTests()
        for key in ("2", "3", "4"):
            with self.subTest(npc=key):
                run, zen, bot, vitals = fixture.arena(fixture.catalog[key], difficulty_index=3)
                fight = run.match.combat
                component = bot.body_script.component
                recovery, = (i for i in component.instances.values() if i.graph.index == 0x0E62)
                kind = combat.SHIELDS_POOL
                run.step({zen: (0, 600, FIRE)})
                for _ in range(25):
                    run.step({})
                self.assertEqual(vitals.pool_total(kind), vitals.pool_maximum(kind)-48)
                bot.team = zen.team
                for _ in range(200):
                    run.step({})
                    regen = recovery.states.get(recovery.graph.node(51).state)
                    if regen is not None and regen.active:
                        break
                self.assertTrue(regen.active)
                run.step({zen: (0, 0, 0x10)})
                self.assertEqual(fight.zenyatta.owner(zen).harmony.target, bot.entity)
                charge = zen.body_script.component.entity_var(342)
                before = vitals.pool_total(kind)
                start_charge = charge.value()
                for _ in range(10):
                    run.step({})
                self.assertAlmostEqual(vitals.pool_total(kind)-before, 9.6, places=5)
                self.assertAlmostEqual(charge.value()-start_charge, 5.6, places=3)

                zen.body_script.set_ult_charge(zen.body_script.component.entity_var(338).value())
                run.step({zen: (0, 0, 0x20)})
                self.assertTrue(fight.zenyatta.ultimate(zen))
                before = vitals.pool_total(kind)
                for _ in range(3):
                    run.step({})
                self.assertAlmostEqual(vitals.pool_total(kind)-before, 17.28, places=5)
                self.assertEqual(charge.value(), 0)

    def test_sniper_and_assassin_recover_authored_shields_when_a_pool_exists(self):
        fixture = npc_fixture.ZenyattaNpcTests()
        for key in ("3", "4"):
            for tier in (0, 3):
                with self.subTest(npc=key, tier=tier):
                    run, zen, bot, vitals = fixture.arena(fixture.catalog[key], difficulty_index=tier)
                    component = bot.body_script.component
                    recovery, = (i for i in component.instances.values() if i.graph.index == 0x0E62)
                    before = vitals.total
                    run.step({zen: (0, 600, FIRE)})
                    for _ in range(25):
                        run.step({})
                    self.assertEqual(vitals.total, before-48)
                    wait = recovery.states.get(recovery.graph.node(52).state)
                    maximum = vitals.pool_maximum(combat.SHIELDS_POOL)
                    if not maximum:
                        self.assertTrue(wait is None or not wait.active)
                        for _ in range(300):
                            run.step({})
                        self.assertEqual(vitals.total, before-48)
                        continue

                    shield = combat.SHIELDS_POOL
                    self.assertEqual(vitals.pool_total(shield), maximum-48)
                    self.assertEqual(wait.ms, 3000)
                    self.assertTrue(wait.active)
                    deadline = wait.start+wait.ms
                    while component.now+16 < deadline:
                        run.step({})
                        self.assertEqual(vitals.pool_total(shield), maximum-48)
                    for _ in range(3):
                        run.step({})
                        if vitals.pool_total(shield) > maximum-48:
                            break
                    self.assertFalse(wait.active)
                    expected = maximum-48+.03*(component.now-deadline)
                    self.assertAlmostEqual(vitals.pool_total(shield), expected, places=5)
                    for _ in range(110):
                        run.step({})
                    self.assertEqual(vitals.pool_total(shield), maximum)
                    self.assertEqual(vitals.total, before)
