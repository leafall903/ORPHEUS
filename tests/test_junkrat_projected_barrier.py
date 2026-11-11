"""Actual Zarya projected-barrier input against Junkrat in a team Match."""

import unittest
from dataclasses import replace

from test_game_combat import KANEZAKA_DM, Run, aim

from ow174.game import combat
from ow174.game.commands import FIRE
from ow174.game.content import find_hero
from ow174.game.script import expr


class JunkratProjectedBarrierTests(unittest.TestCase):
    def test_enemy_rejected_then_ally_selected_and_grenade_damage_credits_zarya(self):
        run = Run(replace(KANEZAKA_DM, free_for_all=False), ("Junkrat", "Zarya", "Target"))
        shooter, zarya, target = run.players
        run.match.bots.clear()
        run.match._pending_bot_scripts.clear()
        x, y, z = shooter.mover.position
        zarya.spawn = ((x + 4, y, z + 6), zarya.spawn[1])
        target.spawn = ((x, y, z + 6), target.spawn[1])
        run.match.switch_hero(shooter, find_hero("Junkrat"))
        run.match.switch_hero(zarya, find_hero("Zarya"))
        run.match.switch_hero(target, target.hero)
        target.team = shooter.team
        for _ in range(30):
            run.step({})
        fight = run.match.combat
        body = next(t for t in fight._targets() if t.owner is target)
        a, b = body.segment()
        centre = tuple((lo + hi) * .5 for lo, hi in zip(a, b, strict=True))
        cast = aim(fight._eye(zarya), centre)
        run.step({zarya: (*cast, 16)})
        instance = next(i for i in zarya.body_script.component.instances.values() if i.graph.index == 0x25C)
        self.assertFalse(fight.barriers.live)
        self.assertFalse(instance.states.get(10) and instance.states[10].active)
        run.step({zarya: (*cast, 0)})
        target.team = zarya.team
        run.step({zarya: (*cast, 16)})
        fight.barriers.sync(zarya)
        bubble, = fight.barriers.live.values()
        self.assertEqual(bubble.definition, 0x04000000000003DA)
        self.assertEqual(instance.vars[536].value(), expr.Entity(target.body))
        self.assertIs(bubble.owner, target)
        self.assertIs(bubble.caster, zarya)
        self.assertEqual(bubble.health, 200)
        run.step({shooter: (0, 0, FIRE)})
        for _ in range(30):
            run.step({})
        self.assertEqual(bubble.health, 80)
        self.assertEqual(fight.vitals_of(body).total, 200)
        self.assertFalse(shooter.client.messages(combat.HIT))
        self.assertGreater(zarya.body_script.component.entity_var(87).value(), 20)
        self.assertIsNone(target.body_script.component.entity_var(87, create=False))

        for _ in range(125):
            run.step({})
        self.assertFalse(bubble.phase.active)
        self.assertFalse(fight.barriers.live)
        counter = instance.states[7].counter
        self.assertGreater(instance.vars[61].value(), 0)
        run.step({zarya: (*cast, 16), shooter: (0, 0, FIRE)})
        for _ in range(30):
            run.step({})
        self.assertEqual(instance.states[7].counter, counter)
        self.assertEqual(fight.vitals_of(body).total, 80)
        for _ in range(520):
            run.step({})
        self.assertFalse(fight.barriers.live)
        run.step({zarya: (*cast, 16)})
        fight.barriers.sync(zarya)
        second, = fight.barriers.live.values()
        self.assertEqual(second.health, 200)
        self.assertNotEqual(second.entity, bubble.entity)
        self.assertGreater(second.counter, bubble.counter)
        run.match.switch_hero(target, find_hero("Genji"))
        self.assertFalse(fight.barriers.live)
