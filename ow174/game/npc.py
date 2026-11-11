"""Manual combat bodies; input/UI and authored enemy AI are separate consumers.

Definitions supply explicit pools rather than borrowing training-bot health or
guessing NPC schema variable meanings. Calls belong to the match update thread.
"""

import json
import math
from dataclasses import dataclass
from pathlib import Path

from ow174.game import bots, combat, npc_navigation, world

ACQUISITION_RANGE = 100.0  # Stationary host policy in metres.
TROOPER_HERO = 0x02E00000000001AC
TROOPER_BODY = 0x0400000000001D40  # The body definition declares TargetTag 04BA.
MELEE_BUTTON = 31
MELEE_GRAPHS = {
    TROOPER_HERO: (0x1422, 2, 5, 1.4),
    0x02E00000000001BA: (0x140E, 3, 6, 2.5),
}


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


def spawn(match, definition: Definition, position, *, team: int, yaw: int = 0,
          acquire: bool = False, pursue: bool = False, specials: bool = False,
          authored_assassin: bool = False, authored_trooper: bool = False) -> bots.Bot:
    """Allocate a fresh body, join combat, and queue actual creates for live viewers."""
    if match.ended:
        raise ValueError('cannot create NPCs in an ended match')
    if not isinstance(definition, Definition):
        raise ValueError('NPC spawn needs a validated definition')
    if type(team) is not int or team not in (0, 1) or type(yaw) is not int:
        raise ValueError('invalid NPC team or yaw')
    if authored_assassin:
        from ow174.game import assassin, assassin_controller

        if definition.hero != assassin.HERO or not pursue:
            raise ValueError("authored Assassin spawn requires Assassin pursuit")
        assassin_controller.phase2_definition()
    if authored_trooper:
        from ow174.game import enforcer_controller, trooper_coordination
        from ow174.game.script import graph

        if definition.body != TROOPER_BODY or definition.hero != TROOPER_HERO or not (acquire or pursue):
            raise ValueError("authored coordination requires an acquiring Trooper")
        enforcer_controller.require_trooper_controller()
        graph.graph(trooper_coordination.GRAPH)
    position = tuple(position)
    if len(position) != 3 or any(type(v) not in (int, float) or not math.isfinite(v) for v in position):
        raise ValueError('NPC position needs three finite coordinates')
    entity = match.combat.next_bot
    if not bots.BOT_ENTITY <= entity < 0xD0000000 or any(b.entity == entity for b in match.bots):
        raise ValueError('NPC entity allocator exhausted or collided')
    data = {'hero': f'0x{definition.hero:016X}', 'team': team, 'position': position,
            'rotation': (0.0, 0.0, 0.0, 1.0), 'graphs': [], 'vars': {}, 'manual_spawn': True}
    if acquire or pursue:
        data['acquire_targets'] = True
    if authored_trooper:
        data['authored_trooper'] = True
    if specials:
        data['acquire_specials'] = True
    hero = {'name': definition.name, 'body': f'0x{definition.body:016X}', 'health': definition.health}
    bot = bots.Bot(entity, data, hero, {})
    if pursue:
        bot.pursuit = npc_navigation.Pursuit()
    bot.yaw = float(bots.s16(yaw))
    bot.retain_corpse = True
    # Import locally to keep Match's existing module graph acyclic.
    from ow174.game.match import BOT_RECORD_DELAY

    bot.records_from = match.tick + BOT_RECORD_DELAY
    match.combat.next_bot += 1
    match._prepare_bot_script(bot)
    match.bots.append(bot)
    match.combat.vitals[entity] = combat.Vitals(
        entity, definition.health, definition.armour, definition.shields)
    if authored_assassin:
        world = assassin_controller.Phase2World(match.combat, bot, native_state_misses=("STU_356628FC",))
        assassin_controller.mount_phase2(match.combat, bot, world)
    match.combat.record_replacement(entity)
    for viewer in match.players:
        if viewer.client is not None and viewer.spawned:
            viewer.client.queue_entities([match._bot_create_update(bot)])
            match.bots_shown.setdefault(viewer.slot, match.tick)
    return bot


def remove_spawned(match) -> tuple[int, ...]:
    """Explicitly remove only manual NPCs, including corpses and owned attacks."""
    from ow174.game import enforcer_controller

    removed = []
    for bot in tuple(match.bots):
        if bot.spawner.get('manual_spawn') is not True:
            continue
        match.combat.clear_pins(bot.entity)
        match.combat.sombra_controls.retire(bot.entity)
        match.combat._retire_trooper_coordination(bot.entity)
        enforcer_controller.retire(bot)
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
                      match.combat.replacement_ticks, match.combat.melee_seen, match.combat.stuns,
                      match.combat.body_barriers):
            table.pop(bot.entity, None)
        match._pending_bot_scripts = type(match._pending_bot_scripts)(
            item for item in match._pending_bot_scripts if item is not bot)
        match.bots.remove(bot)
        for viewer in match.players:
            if viewer.client is not None and viewer.slot in match.bots_shown:
                viewer.client.queue_entities([world.EntityUpdate(bot.entity, world.OP_DESTROY)])
        removed.append(bot.entity)
    return tuple(removed)



def interrupt_specials(fight, bot):
    from ow174.game import assassin, sniper_retreat

    component = bot.body_script.component
    if component.strict or component.fault is not None:
        return
    if bot.hero == assassin.HERO:
        assassin.stop(bot)
    elif bot.hero == sniper_retreat.HERO:
        sniper_retreat.interrupt(bot)
    else:
        npc_navigation.interrupt_charge(fight, bot)


def trooper_coordination_targets(fight, owner, state):
    """Reconstruct 1034's coordination search over live Trooper bodies."""
    source = next((bot for bot in fight.match.bots if bot.entity == owner), None)
    if source is None or source.dead or source.body != TROOPER_BODY:
        return ()
    radius = state.evaluate(state.node.field("m_radius"), watch=False)
    if type(radius) not in (int, float) or radius <= 0:
        return ()
    x, y, z = source.position
    origin = x, y + combat.EYE, z
    candidates = []
    for target in fight._targets():
        candidate = target.owner
        if (target.entity == owner or not isinstance(candidate, bots.Bot)
                or candidate.body != TROOPER_BODY or candidate.dead or fight.vitals_of(target).dead):
            continue
        low, high = target.segment()
        closest = low[0], min(max(y, low[1]), high[1]), low[2]
        distance = math.dist(source.position, closest) - target.radius
        if distance > radius:
            continue
        tx, ty, tz = target.feet
        if npc_navigation.visible(fight.match.collision_world, origin, (tx, ty + target.eye, tz), target,
                                  pillars=fight.ice_wall.pieces.values()):
            candidates.append((distance, target.entity))
    return tuple(entity for _, entity in sorted(candidates))


def update_acquisition(fight, bot):
    """Opt-in target selection for authored weapon, melee and special inputs."""
    from ow174.game import assassin, assassin_controller, enforcer_controller, sniper_retreat

    component = bot.body_script.component
    if component.strict or component.fault is not None:
        return
    specials = bot.spawner.get('acquire_specials')
    locked = bot.entity in fight.sombra_controls.locks
    charge = npc_navigation.charge_instance(bot) if specials else None
    phase = charge.states.get(36) if charge is not None else None
    if phase is not None and phase.active:
        return  # Keep the heading established by the charge input.
    x, y, z = bot.position
    origin = x, y + combat.EYE, z
    candidates, blocked, approach = [], [], []
    pursuit = getattr(bot, "pursuit", None)
    if not fight.is_asleep(bot) and not fight.is_stunned(bot):
        for target in fight._enemies(bot):
            if isinstance(target.owner, bots.Bot) or target.owner.client is None:
                continue
            point = fight._eye(target.owner)
            distance = math.dist(origin, point)
            if not 0 < distance <= ACQUISITION_RANGE:
                continue
            in_reach = bot.hero != assassin.HERO or assassin.contact(fight, bot, target.entity) is not None
            if in_reach and npc_navigation.visible(
                fight.match.collision_world, origin, point, target, pillars=fight.ice_wall.pieces.values(),
            ):
                candidates.append((distance, target.entity, target.owner, point))
                if bot.hero == assassin.HERO and pursuit is not None and distance > 2.0:
                    approach.append((distance, target.entity, target))
            elif pursuit is not None:
                blocked.append((distance, target.entity, target))
    if bot.hero == assassin.HERO and assassin_controller.apply_phase2_input(fight, bot, candidates):
        if pursuit is not None:
            controller = bot.assassin_controller
            destination = None
            if not locked and controller.fault is None and assassin.BUTTON not in component.held:
                selected_entity = assassin_controller._selected_entity(controller)
                if selected_entity:
                    destination = next((item for item in approach if item[1] == selected_entity), None)
                else:
                    destination = min(blocked + approach, default=None, key=lambda item: item[:2])
            npc_navigation.update(fight, bot, destination[2] if destination else None, 2.0)
        return
    selected = min(candidates, default=None, key=lambda item: item[:2])
    if bot.hero == assassin.HERO:
        selected, admitted = assassin_controller.select(fight, bot, candidates)
    elif bot.hero in enforcer_controller.HOSTS:
        admitted = enforcer_controller.admit(fight, bot, selected)
    else:
        admitted = selected is not None
    ground_motion = specials and not locked and getattr(bot, "wall_mover", None) is None
    retreating = ground_motion and sniper_retreat.acquire(bot, selected if admitted else None)
    if pursuit is not None:
        destination = None
        if selected is None and not retreating:
            destination = min(blocked, default=None, key=lambda item: item[:2])
        reach = 2.5 if bot.hero == assassin.HERO else 12.0
        npc_navigation.update(fight, bot, destination[2] if destination else None, reach)
    if bot.hero == assassin.HERO:
        assassin.acquire(fight, bot, selected[2] if admitted else None)
        return
    button = component.world.ai_weapon_button(0)
    if type(button) is not int:
        return
    if selected is not None:
        dx, dy, dz = (axis - start for axis, start in zip(selected[3], origin, strict=True))
        bot.yaw = float(bots.heading(dx, dz))
        bot.pitch = float(round(-math.atan2(dy, math.hypot(dx, dz)) * 65536 / (2 * math.pi)))
    bot.firing = admitted and not retreating
    melee = MELEE_GRAPHS.get(bot.hero) if specials else None
    if melee is None:
        inputs = [(button, bot.firing)]
    else:
        close = bot.firing and selected[0] <= melee[3]
        ability = charge.states.get(41) if charge is not None else None
        charging = (admitted and ground_motion and 5 <= selected[0] <= 8 and ability is not None
                    and ability.remaining() == 0 and charge.vars[59].value() in (None, 0))
        inputs = [(button, bot.firing and not (close or charging)), (MELEE_BUTTON, close)]
        if charge is not None:
            inputs.append((npc_navigation.CHARGE_BUTTON, charging))
    component.edges([(value, down) for value, down in inputs if (value in component.held) != down])
