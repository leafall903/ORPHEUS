"""Manual combat bodies; input/UI and authored enemy AI are separate consumers.

Definitions supply explicit pools rather than borrowing training-bot health or
guessing NPC schema variable meanings. Calls belong to the match update thread.
"""

import json
import math
from dataclasses import dataclass
from pathlib import Path

from ow174.game import bots, combat, heavy_charge, npc_context, npc_melee, sniper_reposition, world
from ow174.game.script import graph


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


def _difficulty_pools(bot):
    """Read the spawn graph's declared level and active additional pools."""
    if bot.body_script is None:
        return {}
    component = bot.body_script.component
    slot = component.entity_var(9948, create=False)
    level = slot.value() if slot is not None else None
    if type(level) is not int or level not in range(4):
        return {}
    instance = next((item for item in component.instances.values() if item.graph.index == 0x143A), None)
    if instance is None:
        return {}
    pools = {}
    for state in instance.states.values():
        if not state.active or state.node.cls != 'STUStatescriptStateHealthPool':
            continue
        kind = state.node.field('m_7E8B666C')
        amount = state.evaluate(state.node.field('m_amount'), watch=False)
        if kind not in (0, 1, 2) or kind in pools or type(amount) not in (int, float):
            raise ValueError('unsupported Talon difficulty pool')
        if not math.isfinite(amount) or amount < 0:
            raise ValueError('invalid Talon difficulty pool')
        pools[kind] = amount
    return pools


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
    controller = npc_context.controller_definition(definition.body)
    if controller is not None and any(graph.graph(item["graph"]) is None for item in controller["graphs"]):
        raise ValueError('associated NPC controller graphs are incomplete')
    count = 2 if controller is not None else 1
    reserved = {value for b in match.bots for value in (b.entity, b.spawner.get("controller_entity"))}
    if not bots.BOT_ENTITY <= entity <= 0xD0000000 - count or any(
        value in reserved for value in range(entity, entity + count)
    ):
        raise ValueError('NPC entity allocator exhausted or collided')
    data = {'hero': f'0x{definition.hero:016X}', 'team': team, 'position': position,
            'rotation': (0.0, 0.0, 0.0, 1.0), 'graphs': [], 'vars': {}, 'manual_spawn': True}
    if controller is not None:
        data["controller_entity"] = entity + 1
    hero = {'name': definition.name, 'body': f'0x{definition.body:016X}', 'health': definition.health}
    bot = bots.Bot(entity, data, hero, {})
    bot.yaw = float(bots.s16(yaw))
    bot.retain_corpse = True
    # Import locally to keep Match's existing module graph acyclic.
    from ow174.game.match import BOT_RECORD_DELAY

    bot.records_from = match.tick + BOT_RECORD_DELAY
    match._prepare_bot_script(bot)
    additional = _difficulty_pools(bot)
    # Reconstructed spawn policy: the conditional graph adds to the simultaneously
    # active base pools. Capture both through the ordinary combat body lifecycle.
    vitals = combat.Vitals(entity, definition.health + additional.get(0, 0),
                           definition.armour + additional.get(1, 0),
                           definition.shields + additional.get(2, 0))
    match.combat.next_bot += count
    match.bots.append(bot)
    match.combat.vitals[entity] = vitals
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
        remove_actor(match, bot)
        removed.append(bot.entity)
    return tuple(removed)


def remove_actor(match, bot):
    """Retire a declared manual or summoned body and its runtime references."""
    if bot not in match.bots:
        return
    if bot.body_script is not None and bot.spawner.get("manual_spawn") is True:
        engage(match, bot, None)
    match.combat.hooks.retire(bot.entity)
    match.combat.clear_pins(bot.entity)
    match.combat.shields.retire(bot.entity)
    bot.died(match.tick)
    script, bot.body_script = bot.body_script, None
    if script is not None:
        script.retire_controller()
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
                  match.combat.replacement_ticks, match.combat.melee_seen,
                  match.combat.frost_hits, match.combat.frost_sources, match.combat.stuns):
        table.pop(bot.entity, None)
    match._pending_bot_scripts = type(match._pending_bot_scripts)(
        item for item in match._pending_bot_scripts if item is not bot)
    match.bots.remove(bot)
    for viewer in match.players:
        if viewer.client is not None and viewer.slot in match.bots_shown:
            viewer.client.queue_entities([world.EntityUpdate(bot.entity, world.OP_DESTROY)])


def _stop_primary_burst(bot):
    """Retire committed primary states for a manual sandbox Stop."""
    component = bot.body_script.component
    if bot.body == heavy_charge.BODY:
        graph_id = 0x114F
    elif bot.body == npc_melee.TROOPER:
        graph_id = 0x102E
    else:
        return False
    weapon = next((i for i in component.instances.values() if i.graph.index == graph_id), None)
    if weapon is None:
        return False
    component.run_now()
    if graph_id == 0x114F:
        windup = weapon.states.get(17)
        # The windup stack owns both its animation and its admission gate.
        # During a volley, cancel its wait and let the post-burst gate finish.
        states = (13,) if windup is not None and windup.active else (26,)
    else:
        # The Trooper priority stack owns prefire. Its burst has a separate wait.
        states = (16, 20)
    for index in states:
        state = weapon.states.get(index)
        if state is not None:
            state.end(False)
    component.run_now()
    return True


def engage(match, bot, target, *, action="primary"):
    """Aim and issue a manual NPC primary, melee or charge input.

    The attacker keeps its actual authored graph. Assassin uses the reviewed
    data-only target context; its 3 m contact/LOS policy is reconstructed.
    """
    from ow174.game import assassin
    if bot not in match.bots or bot.spawner.get('manual_spawn') is not True or bot.body_script is None:
        raise ValueError('manual engagement requires a live declared NPC body')
    component = bot.body_script.component
    primary = component.world.ai_weapon_button(0)
    if target is None:
        heavy_charge.stop(bot, match.combat)
        stopped_retreat = sniper_reposition.finish(match.combat, bot)
        npc_melee.stop(bot)
        released_pin = assassin.bind(component.world, component, None)
        if bot.body_script.controller is not None:
            bot.body_script.controller.world.manual_target = None
        component.edges([(primary, False)] if primary is not None else [])
        was_firing = bot.firing
        bot.firing, bot.attack_target = False, None
        stopped_primary = was_firing and not bot.dead and _stop_primary_burst(bot)
        if released_pin:
            component.run_now()
        if stopped_retreat or released_pin or stopped_primary:
            match.combat.publish_bot_live(bot, bot.body_script.capture_live())
        return
    if (bot.dead or target not in match.players or target.client is None or target.gone
            or match.combat.is_dead(target) or match.combat.is_stunned(bot) or match.combat.frozen(bot)
            or (not match.game_map.free_for_all and target.team == bot.team)):
        raise ValueError('manual engagement needs a living enemy and uncontrolled source')
    if action == "reposition":
        if not sniper_reposition.request(match.combat, bot, target):
            raise ValueError('manual Sniper reposition is unavailable')
        if bot.firing and primary is not None:
            component.edges([(primary, False)])
        bot.firing, bot.attack_target = False, None
        return
    if action in ("charge", "melee"):
        instance = heavy_charge.charge(bot) if action == "charge" else npc_melee.instance_of(bot)
        if instance is None:
            raise ValueError('manual NPC ability requires its authored body graph')
        if heavy_charge.clocked(bot) or npc_melee.clocked(bot):
            return
        if bot.firing and primary is not None:
            component.edges([(primary, False)])
        bot.firing, bot.attack_target = False, None
        bot.pending_ability = heavy_charge.BUTTON if action == "charge" else npc_melee.BUTTON
    elif action != "primary":
        raise ValueError('unknown manual NPC action')
    elif primary is None:
        raise ValueError('manual NPC has no extracted primary input')
    elif assassin.recovering(component):
        return
    delta = (target.mover.position[0] - bot.position[0], target.mover.position[2] - bot.position[2])
    bot.yaw = float(round(math.atan2(delta[0], delta[1]) * 65536 / (2 * math.pi)))
    if action != "primary":
        return
    heavy_charge.stop(bot, match.combat)
    sniper_reposition.finish(match.combat, bot)
    npc_melee.stop(bot)
    bot.firing, bot.attack_target = True, target.entity
    if bot.body_script.controller is not None and not bot.body_script.controller.owner_dead:
        bot.body_script.controller.world.manual_target = target.body
    if assassin.bind(component.world, component, target.body):
        component.run_now()
        match.combat.publish_bot_live(bot, bot.body_script.capture_live())
        return
    component.edges([(primary, True)])
