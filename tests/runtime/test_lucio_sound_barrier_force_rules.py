"""Original Sound Barrier producer separation; native Physics receiver stays open."""

import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from ow174.game import combat, lucio, sound_barrier
from ow174.game.collision import flat_world
from ow174.game.content import heroes
from ow174.game.mover import SET
from tests.runtime.test_game_combat import Run


class SoundBarrierForceRulesTests(unittest.TestCase):
    def test_actual_cast_keeps_self_hop_and_native_physics_separate(self):
        fixtures = Path(os.environ.get("ORPHEUS_BEHAVIOR_FIXTURES")
                        or Path(__file__).resolve().parents[2] / "fixtures/behavior")
        contract = json.loads((fixtures / "lucio-sound-barrier-force-separation-v1.json").read_text())
        inputs, expected = contract["input"], contract["expected"]
        run = Run(names=("Lucio", "Enemy", "Ally"))
        source, enemy, ally = run.players
        match, fight = run.match, run.match.combat
        original_npcs = tuple(bot.entity for bot in match.bots)
        self.assertEqual(len(original_npcs), expected["retained_npcs"])
        match.switch_hero(source, heroes()[lucio.HERO])
        source.team, enemy.team, ally.team = 0, 1, 0
        match.collision_world = flat_world(inputs["floor"])
        for actor, position in ((source, (1000, 0, 1000)), (enemy, (1000, 0, 1004)),
                                (ally, (1002, 0, 1004))):
            actor.mover.position[:] = position
            actor.mover.body = None
            actor.body_script.create_arrived = True
        for _ in range(inputs["warmup_commands"]):
            run.step({})
        source.body_script.ult_seconds = 0
        source.body_script.set_ult_charge(inputs["initial_charge"])
        instance = next(i for i in source.body_script.component.instances.values()
                        if i.graph.index == sound_barrier.GRAPH)
        jump_node, physics_node = instance.graph.node(26), instance.graph.node(50)
        enemy_target = next(t for t in fight._targets() if t.owner is enemy)
        before_health = fight.vitals_of(enemy_target).total
        enemy_position = tuple(enemy.mover.position)
        height = source.mover.position[1]
        max_height, physics_counter, shield_granted = height, 0, False
        with (patch.object(match, "queue_body_velocity_changes",
                           wraps=match.queue_body_velocity_changes) as queue,
              patch.object(fight.knockbacks, "apply", wraps=fight.knockbacks.apply) as apply):
            for index in range(inputs["commands"]):
                run.step({source: (0, 0, inputs["cast_button"]
                                  if index < inputs["held_commands"] else 0)})
                max_height = max(max_height, source.mover.position[1])
                state = instance.states.get(physics_node.state)
                physics_counter = max(physics_counter, state.counter if state is not None else 0)
                shield_granted |= source.body in fight.sound_barriers.shielded
            self.assertEqual(apply.call_count, expected["character_force_requests"])
        authored = [call.args[3] for call in queue.call_args_list
                    if call.args[0] == source.body and call.args[2] == (instance.id, jump_node.rid)]
        self.assertEqual(len(authored), expected["self_jump_actions"])
        self.assertEqual(len(authored[0]), 1)
        _, channel, operation, ratio, _ = authored[0][0]
        self.assertEqual((channel, operation, ratio), (3, SET, expected["self_jump_ratio"]))
        self.assertGreater(max_height, height)
        self.assertFalse(source.mover.airborne)
        self.assertEqual(shield_granted, expected["shield_granted"])
        self.assertEqual(before_health - fight.vitals_of(enemy_target).total, expected["enemy_damage"])
        self.assertEqual(tuple(enemy.mover.position), enemy_position)
        self.assertFalse(source.client.messages(combat.HIT))
        self.assertEqual(fight.knockbacks.serial, expected["character_force_requests"])
        self.assertGreater(physics_counter, 0)
        self.assertFalse(physics_node.server_only)
        self.assertIn(physics_node, instance.graph.owner_states())
        self.assertIn(physics_node, instance.graph.remote_states())
        self.assertEqual(tuple(bot.entity for bot in match.bots), original_npcs)
