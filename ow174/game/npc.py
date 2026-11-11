"""Manual combat bodies; input/UI and authored enemy AI are separate consumers.

Definitions supply explicit pools rather than borrowing training-bot health or
guessing NPC schema variable meanings. Calls belong to the match update thread.
"""

import json
import math
from dataclasses import dataclass
from pathlib import Path

from ow174.game import bots, combat, world


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


class BodyScriptUnavailable(ValueError):
    """A requested timed body failed before publishing its identity or creates."""


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


def spawn(match, definition: Definition, position, *, team: int, yaw: int = 0,
          manual: bool = True, script_clock: tuple[int, int] | None = None,
          dva_owner: tuple[int, int] | None = None, difficulty_index: int | None = None) -> bots.Bot:
    """Allocate a fresh body, join combat, and queue actual creates for live viewers."""
    if match.ended:
        raise ValueError('cannot create NPCs in an ended match')
    if not isinstance(definition, Definition):
        raise ValueError('NPC spawn needs a validated definition')
    allowed_teams = (0, 1) if manual else (0, 1, 4)  # native FFA filter for summoned bodies
    if (type(team) is not int or team not in allowed_teams or type(yaw) is not int
            or type(manual) is not bool):
        raise ValueError('invalid NPC team or yaw')
    if difficulty_index is not None and (
            not manual or type(difficulty_index) is not int or not 0 <= difficulty_index <= 3):
        raise ValueError('manual NPC difficulty must select one of four authored entries')
    if script_clock is not None and (
            type(script_clock) is not tuple or len(script_clock) != 2
            or any(type(value) is not int for value in script_clock)
            or not 0 <= script_clock[0] <= 0xFFFFFFFF or not 1 <= script_clock[1] <= 1000):
        raise ValueError('body script clock needs uint32 frame and positive millisecond quantum')
    position = tuple(position)
    if len(position) != 3 or any(type(v) not in (int, float) or not math.isfinite(v) for v in position):
        raise ValueError('NPC position needs three finite coordinates')
    entity = match.combat.next_bot
    if not bots.BOT_ENTITY <= entity < 0xD0000000 or any(b.entity == entity for b in match.bots):
        raise ValueError('NPC entity allocator exhausted or collided')
    data = {'hero': f'0x{definition.hero:016X}', 'team': team, 'position': position,
            'rotation': (0.0, 0.0, 0.0, 1.0), 'graphs': [], 'vars': {}, 'manual_spawn': manual}
    if difficulty_index is not None:
        data["difficulty_index"] = difficulty_index
    if script_clock is not None:
        data['dva_clock'] = {'frame': script_clock[0], 'frame_ms': script_clock[1],
                            'world_ms': round(match.combat.now*1000), 'world_tick': match.tick}
    hero = {'name': definition.name, 'body': f'0x{definition.body:016X}', 'health': definition.health}
    if dva_owner is not None:
        data["dva_source_body"], data["dva_controller"] = dva_owner
    bot = bots.Bot(entity, data, hero, {})
    bot.yaw = float(bots.s16(yaw))
    bot.retain_corpse = True
    if script_clock is not None:
        bot.firing = True  # advance the body VM; no fire button or AI placement graph is synthesized
    # Import locally to keep Match's existing module graph acyclic.
    from ow174.game.match import BOT_RECORD_DELAY

    bot.records_from = match.tick + BOT_RECORD_DELAY
    match.bots.append(bot)
    match.combat.vitals[entity] = combat.Vitals(
        entity, definition.health, definition.armour, definition.shields)
    match._prepare_bot_script(bot)
    if (script_clock is not None or difficulty_index is not None) and bot.body_script is None:
        match.bots.remove(bot)
        match.combat.vitals.pop(entity, None)
        raise BodyScriptUnavailable('required body script failed before publication')
    match.combat.next_bot += 1
    match.combat._sync_body_health(bot, match.combat.vitals[entity])
    match.combat.record_replacement(entity)
    for viewer in match.players:
        if viewer.client is not None and viewer.spawned:
            viewer.client.queue_entities([match._bot_create_update(bot)])
            match.bots_shown.setdefault(viewer.slot, match.tick)
    return bot


def remove_spawned(match) -> tuple[int, ...]:
    """Explicitly remove only manual NPCs, including corpses and owned attacks."""
    removed = []
    for bot in tuple(match.bots):
        if bot.spawner.get('manual_spawn') is not True:
            continue
        remove_body(match, bot)
        removed.append(bot.entity)
    return tuple(removed)


def remove_body(match, bot) -> None:
    """Retire one explicit body, without selecting other manual NPCs or corpses."""
    if bot not in match.bots:
        return
    bot.died(match.tick)
    match.combat.clear_pins(bot.entity)
    match.combat.frost.retire(bot.entity, bot)
    script, bot.body_script = bot.body_script, None
    if script is not None:
        component = script.component
        component.owner_dead = True
        component.held.clear()
        component.shots.clear()
        for instance in list(component.instances.values()):
            component.destroy(instance)
        if hasattr(component.world, 'match'):
            component.world.match = None
            component.world.manual_target = None
    for shot in list(match.combat.projectiles.shots):
        if shot.player is bot:
            match.combat.projectiles._end(shot, match.tick, False)
    match.combat.rockets[:] = [rocket for rocket in match.combat.rockets if rocket.shooter is not bot]
    match.combat.respawns[:] = [(when, who) for when, who in match.combat.respawns if who is not bot]
    for table in (match.combat.vitals, match.combat.history,
                  match.combat.replacement_ticks, match.combat.melee_seen, match.combat.stuns, match.combat.sleeps):
        table.pop(bot.entity, None)
    match._pending_bot_scripts = type(match._pending_bot_scripts)(
        item for item in match._pending_bot_scripts if item is not bot)
    match.bots.remove(bot)
    for viewer in match.players:
        if viewer.client is not None and viewer.slot in match.bots_shown:
            viewer.client.queue_entities([world.EntityUpdate(bot.entity, world.OP_DESTROY)])
