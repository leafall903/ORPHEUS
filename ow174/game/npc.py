"""Manual combat bodies; input/UI and authored enemy AI are separate consumers.

Definitions supply explicit pools rather than borrowing training-bot health or
guessing NPC schema variable meanings. Calls belong to the match update thread.
"""

import json
import math
from dataclasses import dataclass, fields
from pathlib import Path

from ow174.game import bots, combat, world
from ow174.game.mover import MoverData


@dataclass(frozen=True)
class Definition:
    hero: int
    body: int
    name: str
    health: float
    armour: float = 0.0
    shields: float = 0.0
    mover: MoverData | None = None
    controller: int | None = None  # authored linked003 definition; not mounted by manual spawn

    def __post_init__(self):
        for value, kind in ((self.hero, 0x02E0), (self.body, 0x0400)):
            if type(value) is not int or value >> 48 != kind or not value & 0xFFFFFFFFFFFF:
                raise ValueError('NPC definition needs full hero and body GUIDs')
        if self.controller is not None and (
            type(self.controller) is not int or self.controller >> 48 != 0x0400
            or not self.controller & 0xFFFFFFFFFFFF
        ):
            raise ValueError('NPC controller needs a full entity definition GUID')
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError('NPC definition needs a name')
        if any(type(value) not in (int, float) or not math.isfinite(value) or value < 0
               for value in (self.health, self.armour, self.shields)) or self.health <= 0:
            raise ValueError('NPC pools must be finite and health must be positive')
        if self.mover is not None:
            if type(self.mover) is not MoverData or any(
                type(value := getattr(self.mover, field.name)) not in (int, float)
                or not math.isfinite(value) for field in fields(MoverData)
            ):
                raise ValueError('NPC mover must contain finite numeric values')
            if any(getattr(self.mover, name) <= 0 for name in (
                'stand_height', 'stand_radius', 'crouch_height', 'crouch_radius',
            )):
                raise ValueError('NPC capsule dimensions must be positive')


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
        mover = row.get('mover')
        controller = row.get('controller')
        if controller is not None and not isinstance(controller, str):
            raise ValueError('NPC controller catalog needs a hexadecimal GUID string')
        if mover is not None:
            if not isinstance(mover, dict) or set(mover) != {f.name for f in fields(MoverData)}:
                raise ValueError('NPC mover catalog must contain all authored mover fields')
            mover = MoverData(**mover)
        result[key] = Definition(int(row['hero'], 16), int(row['body'], 16), row['name'],
                                 row['health'], row['armour'], row['shields'], mover,
                                 int(controller, 16) if controller is not None else None)
    return result


def spawn(match, definition: Definition, position, *, team: int, yaw: int = 0) -> bots.Bot:
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
    if not bots.BOT_ENTITY <= entity < 0xD0000000 or any(b.entity == entity for b in match.bots):
        raise ValueError('NPC entity allocator exhausted or collided')
    data = {'hero': f'0x{definition.hero:016X}', 'team': team, 'position': position,
            'rotation': (0.0, 0.0, 0.0, 1.0), 'graphs': [], 'vars': {}, 'manual_spawn': True}
    if definition.controller is not None:
        data['controller_definition'] = f'0x{definition.controller:016X}'
    hero = {'name': definition.name, 'body': f'0x{definition.body:016X}', 'health': definition.health}
    bot = bots.Bot(entity, data, hero, {}, mover=definition.mover)
    bot.yaw = float(bots.s16(yaw))
    bot.retain_corpse = True
    # Import locally to keep Match's existing module graph acyclic.
    from ow174.game.match import BOT_RECORD_DELAY

    bot.records_from = match.tick + BOT_RECORD_DELAY
    match.combat.next_bot += 1
    match.combat.vitals[entity] = combat.Vitals(
        entity, definition.health, definition.armour, definition.shields)
    match._prepare_bot_script(bot)
    match.bots.append(bot)
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
        _remove_body(match, bot)
        removed.append(bot.entity)
    return tuple(removed)


def remove_summoned(match, bot) -> bool:
    """A summon owner may retire its object without removing manual PvE corpses."""
    if bot.spawner.get('summoned') is not True:
        raise ValueError('summon retirement cannot remove a manual NPC')
    if bot not in match.bots:
        return False
    _remove_body(match, bot)
    return True


def _remove_body(match, bot) -> None:
    match.combat.clear_frost(bot)
    match.combat.clear_pins(bot.entity)
    bot.died(match.tick)
    script, bot.body_script = bot.body_script, None
    if script is not None:
        component = script.component
        component.owner_dead = True
        component.held.clear()
        component.shots.clear()
        for instance in list(component.instances.values()):
            component.destroy(instance)
    for shot in list(match.combat.projectiles.shots):
        if shot.player is bot:
            match.combat.projectiles._end(shot, match.tick, False)
    match.combat.rockets[:] = [rocket for rocket in match.combat.rockets if rocket.shooter is not bot]
    match.combat.respawns[:] = [(when, who) for when, who in match.combat.respawns if who is not bot]
    for table in (match.combat.vitals, match.combat.history,
                  match.combat.replacement_ticks, match.combat.melee_seen, match.combat.stuns):
        table.pop(bot.entity, None)
    match._pending_bot_scripts = type(match._pending_bot_scripts)(
        item for item in match._pending_bot_scripts if item is not bot)
    match.bots.remove(bot)
    for viewer in match.players:
        if viewer.client is not None and viewer.slot in match.bots_shown:
            viewer.client.queue_entities([world.EntityUpdate(bot.entity, world.OP_DESTROY)])
