"""Manual combat bodies; input/UI and authored enemy AI are separate consumers.

Definitions supply explicit pools rather than borrowing training-bot health or
guessing NPC schema variable meanings. Calls belong to the match update thread.
"""

import json
import math
from dataclasses import dataclass
from pathlib import Path

from ow174.game import bots, combat, enforcer_movement, heavy_charge, sniper_reposition, world
from ow174.game.script import expr, graph, play_queue


@dataclass(frozen=True)
class Definition:
    hero: int
    body: int
    name: str
    health: float
    armour: float = 0.0
    shields: float = 0.0

    def __post_init__(self):
        for value, kind in ((self.hero, 0x02E0), (self.body, 0x0400)):
            if type(value) is not int or value >> 48 != kind or not value & 0xFFFFFFFFFFFF:
                raise ValueError('NPC definition needs full hero and body GUIDs')
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError('NPC definition needs a name')
        if any(type(value) not in (int, float) or not math.isfinite(value) or value < 0
               for value in (self.health, self.armour, self.shields)) or self.health <= 0:
            raise ValueError('NPC pools must be finite and health must be positive')


def load_catalog(path: Path) -> dict[str, Definition]:
    """Read a prepared external catalog; missing data is not a training-bot fallback."""
    data = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data, dict) or data.get('format_version') != 1:
        raise ValueError('unsupported NPC catalog')
    rows = data.get('definitions')
    if not isinstance(rows, list) or not rows:
        raise ValueError('NPC catalog has no definitions')
    result = {}
    for row in rows:
        key = row['key']
        if not isinstance(key, str) or key not in '123456789' or len(key) != 1 or key in result:
            raise ValueError('NPC catalog needs unique selection keys 1..9')
        result[key] = Definition(int(row['hero'], 16), int(row['body'], 16), row['name'],
                                 row['health'], row['armour'], row['shields'])
    return result


def spawn(match, definition: Definition, position, *, team: int, yaw: int = 0, occupied=()) -> bots.Bot:
    """Allocate a fresh body, join combat, and queue actual creates for live viewers."""
    if match.ended:
        raise ValueError('cannot create NPCs in an ended match')
    if not isinstance(definition, Definition):
        raise ValueError('NPC spawn needs a validated definition')
    if type(team) is not int or team not in (0, 1) or type(yaw) is not int:
        raise ValueError('invalid NPC team or yaw')
    position = tuple(position)
    if len(position) != 3 or any(type(v) not in (int, float) or not math.isfinite(v) for v in position):
        raise ValueError('NPC position needs three finite coordinates')
    entity = match.combat.next_bot
    linked = (definition.body == enforcer_movement.BODY
              and graph.entity_body(enforcer_movement.CONTROLLER) is not None
              and graph.graph(enforcer_movement.CONTROL_GRAPH) is not None)
    controller_entity = entity + 1 if linked else None
    last = controller_entity if controller_entity is not None else entity
    if not bots.BOT_ENTITY <= entity <= last < 0xD0000000 or any(
        entity <= b.entity <= last for b in match.bots
    ) or any(entity <= reserved <= last for reserved in occupied):
        raise ValueError('NPC entity allocator exhausted or collided')
    data = {'hero': f'0x{definition.hero:016X}', 'team': team, 'position': position,
            'rotation': (0.0, 0.0, 0.0, 1.0), 'graphs': [], 'vars': {}, 'manual_spawn': True}
    hero = {'name': definition.name, 'body': f'0x{definition.body:016X}', 'health': definition.health}
    bot = bots.Bot(entity, data, hero, {})
    bot.yaw = float(bots.s16(yaw))
    bot.retain_corpse = True
    # Import locally to keep Match's existing module graph acyclic.
    from ow174.game.match import BOT_RECORD_DELAY

    bot.records_from = match.tick + BOT_RECORD_DELAY
    match.combat.next_bot = last + 1
    if controller_entity is None:
        match._prepare_bot_script(bot)
    else:
        match._prepare_bot_script(bot, controller_entity=controller_entity)
    match.bots.append(bot)
    match.combat.vitals[entity] = combat.Vitals(
        entity, definition.health, definition.armour, definition.shields)
    match.combat._sync_body_health(bot, match.combat.vitals[entity])
    match.combat.record_replacement(entity)
    for viewer in match.players:
        if viewer.client is not None and viewer.spawned:
            viewer.client.queue_entities([match._bot_create_update(bot)])
            match.bots_shown.setdefault(viewer.slot, match.tick)
    return bot


class RetributionProfiles:
    """Match-backed carrier, drop and wave sites with an explicit profile catalog."""

    def __init__(self, match, scene, definitions, acquire=None, hardpoints=None, waves=None):
        self.match = match
        self.scene = scene
        self.definitions = dict(definitions)
        if any(type(key) is not int or key >> 48 != 0x06B0 or not key & 0xFFFFFFFFFFFF
               or not isinstance(definition, Definition) for key, definition in self.definitions.items()):
            raise ValueError("expected full NPC profile GUIDs and validated definitions")
        self.created = {}
        if acquire is not None and not callable(acquire):
            raise ValueError("expected a PlayScript resource provider")
        if hardpoints is not None and not callable(hardpoints):
            raise ValueError("expected a profile hardpoint provider")
        self.acquire = acquire
        self.hardpoints = hardpoints
        self.queues = {}
        self.waves = {} if waves is None else dict(waves)
        self.wave_pending = {}
        self.completed_waves = {}
        for (index, identifier), profile in self.waves.items():
            if (index not in (0x113F, 0x13CE) or type(identifier) is not int
                    or identifier >> 48 != 0x0D80 or not identifier & 0xFFFFFFFFFFFF
                    or profile not in self.definitions):
                raise ValueError("expected a wave graph and identifier bound to a declared NPC profile")

    def create(self, instance, node, profile):
        index = instance.graph.index
        site = (index == 0x100B and node.pos in (283, 284) and node.field("m_51AB9D21") == 5
                or index == 0x11E0 and node.pos in (33, 34, 35, 37) and node.field("m_51AB9D21") == 3)
        if not site:
            return NotImplemented
        bot = self._create(instance, node, profile)
        if bot is NotImplemented:
            return NotImplemented
        if index == 0x100B:
            bot.spawner['scripted_transport'] = True
        return True

    def create_wave(self, instance, node, identifier):
        index = instance.graph.index
        component = instance.component
        if ((index, node.pos) not in ((0x113F, 9), (0x13CE, 4)) or node.field("m_51AB9D21") != 5
                or self.scene.script_components.get(component.entity) is not component):
            return NotImplemented
        bot = self._create(instance, node, self.waves.get((index, identifier)))
        if bot is NotImplemented:
            return NotImplemented
        self.wave_pending.setdefault((instance, identifier), []).append(bot.entity)
        return True

    def complete_wave_spawn(self, instance, node, identifier):
        component = instance.component
        index = instance.graph.index
        if (index not in (0x113F, 0x13CE) or node.pos != 1
                or self.scene.script_components.get(component.entity) is not component):
            return NotImplemented
        entities = self.wave_pending.pop((instance, identifier), None)
        if entities is None:
            return NotImplemented
        self.completed_waves[component.entity, index, identifier] = tuple(entities)
        return True

    def cancel_wave_spawns(self, component):
        for key in tuple(self.wave_pending):
            if key[0].component is component:
                del self.wave_pending[key]

    def _create(self, instance, node, profile):
        definition = self.definitions.get(profile)
        output = expr.lvalue(node.field("m_E95B313B"))
        component = instance.component
        if (definition is None or output is None or output[0] != graph.INSTANCE
                or component.owner_dead or component.entity not in self.scene.entities):
            return NotImplemented
        if node.field("m_rotation") is not None:
            return NotImplemented
        position = instance.evaluate(node.field("m_position"))
        team = instance.evaluate(node.field("m_3EBF988B"))
        if not isinstance(position, expr.Vec3) or type(team) is not int or team not in (0, 1):
            return NotImplemented
        bot = spawn(self.match, definition, position, team=team, occupied=self.scene.entities)
        self.scene.bind(bot.entity, position=position)
        self.created[bot.entity] = bot
        if bot.body_script is not None:
            component = bot.body_script.component
            component.world.match = self.match
            if self.acquire is not None:
                self.queues[bot.entity] = play_queue.Queue(component, self.acquire)
        instance.write(output, expr.Entity(bot.entity))
        return bot

    def retire(self, entity):
        if entity in self.created:
            self.scene.unbind(entity)

    def hardpoint_position(self, entity, point):
        bot = self.created.get(entity)
        if bot is None or self.hardpoints is None:
            return NotImplemented
        return self.hardpoints(bot, point)

    def unbind(self, entity):
        self.created.pop(entity, None)
        for key in tuple(self.wave_pending):
            if key[0].component.entity == entity:
                del self.wave_pending[key]
        for key in tuple(self.completed_waves):
            if key[0] == entity:
                del self.completed_waves[key]
        queue = self.queues.pop(entity, None)
        if queue is not None:
            queue.retire()

    def close(self):
        for entity in tuple(self.created):
            self.unbind(entity)
        self.wave_pending.clear()
        self.completed_waves.clear()


def remove_spawned(match, *, selected=None) -> tuple[int, ...]:
    """Explicitly remove only manual NPCs, including corpses and owned attacks."""
    removed = []
    for bot in tuple(match.bots):
        if bot.spawner.get('manual_spawn') is not True or (selected is not None and bot is not selected):
            continue
        match.combat.clear_pins(bot.entity)
        heavy_charge.release(match.combat, bot.entity)
        sniper_reposition.cancel(match.combat, bot)
        match.combat.heavy_grazes.pop(bot.entity, None)
        bot.died(match.tick)
        script, bot.body_script = bot.body_script, None
        if script is not None:
            enforcer_movement.retire(script)
            component = script.component
            component.world.retire_profile_play_queue(component)
            component.owner_dead = True
            component.held.clear()
            component.shots.clear()
            for instance in list(component.instances.values()):
                component.destroy(instance)
            component.world.match = None
            component.world.manual_target = None
        for shot in list(match.combat.projectiles.shots):
            if shot.player is bot:
                match.combat.projectiles._end(shot, match.tick, False)
        match.combat.rockets[:] = [rocket for rocket in match.combat.rockets if rocket.shooter is not bot]
        match.combat.respawns[:] = [(when, who) for when, who in match.combat.respawns if who is not bot]
        match.combat.support_effects.retire(bot.entity)
        match.combat.healing_status.retire(bot.entity)
        if match.scene_host is not None and (profiles := match.scene_host.world.profile_spawns) is not None:
            profiles.retire(bot.entity)
        for table in (match.combat.vitals, match.combat.history,
                      match.combat.replacement_ticks, match.combat.melee_seen, match.combat.stuns):
            table.pop(bot.entity, None)
        match._pending_bot_scripts = type(match._pending_bot_scripts)(
            item for item in match._pending_bot_scripts if item is not bot)
        match.bots.remove(bot)
        for viewer in match.players:
            if viewer.client is not None and viewer.slot in match.bots_shown:
                viewer.client.queue_entities([world.EntityUpdate(bot.entity, world.OP_DESTROY)])
        removed.append(bot.entity)
    return tuple(removed)
