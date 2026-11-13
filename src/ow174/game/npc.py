"""Manual combat bodies; input/UI and authored enemy AI are separate consumers.

Definitions supply explicit pools rather than borrowing training-bot health or
guessing NPC schema variable meanings. Calls belong to the match update thread.
"""

import json
import math
from dataclasses import dataclass, fields
from pathlib import Path

from ow174.game import bots, combat, npc_navigation, world
from ow174.game.mover import MoverData


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
    mover: MoverData | None = None
    controller: int | None = None

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


class BodyScriptUnavailable(ValueError):
    """A requested timed body failed before publishing its identity or creates."""


def spawn(match, definition: Definition, position, *, team: int, yaw: int = 0,
          current_health: float | None = None,
          autonomous: bool = False, acquire: bool = False, pursue: bool = False, specials: bool = False,
          authored_assassin: bool = False, authored_trooper: bool = False,
          difficulty_index: int | None = None, born_tick: int | None = None,
          source_entity: int | None = None, hero_target: bool = False,
          pet_owner: int | None = None) -> bots.Bot:
    """Allocate a fresh body, join combat, and queue actual creates for live viewers."""
    if match.ended:
        raise ValueError('cannot create NPCs in an ended match')
    if born_tick is not None and (type(born_tick) is not int or not 0 <= born_tick <= match.tick):
        raise ValueError('NPC birth tick must be an observed world tick')
    if not isinstance(definition, Definition):
        raise ValueError('NPC spawn needs a validated definition')
    if source_entity is not None and (type(source_entity) is not int or not 0 < source_entity <= 0xFFFFFFFF):
        raise ValueError('NPC script source needs a nonzero 32-bit entity')
    if pet_owner is not None and (type(pet_owner) is not int or not 0 <= pet_owner <= 0xFFFFFFFF):
        raise ValueError('NPC Pet relationship needs a 32-bit entity')
    if type(hero_target) is not bool:
        raise ValueError('Hero target mode must be boolean')
    if type(team) is not int or team not in (0, 1) or type(yaw) is not int:
        raise ValueError('invalid NPC team or yaw')
    if difficulty_index is not None and (type(difficulty_index) is not int or not 0 <= difficulty_index <= 3):
        raise ValueError('manual NPC difficulty must select one of four authored entries')
    if current_health is not None and (type(current_health) not in (int, float)
            or not math.isfinite(current_health) or not 0 < current_health <= definition.health):
        raise ValueError('NPC initial health must be within its maximum')
    position = tuple(position)
    if type(autonomous) is not bool:
        raise ValueError('NPC autonomous mode must be boolean')
    if len(position) != 3 or any(type(v) not in (int, float) or not math.isfinite(v) for v in position):
        raise ValueError('NPC position needs three finite coordinates')
    if authored_assassin:
        from ow174.game import assassin, assassin_controller

        if definition.hero != assassin.HERO or not pursue:
            raise ValueError("authored Assassin spawn requires Assassin pursuit")
        assassin_controller.phase2_definition()
    if authored_trooper:
        from ow174.game import enforcer_controller

        if definition.body != TROOPER_BODY or definition.hero != TROOPER_HERO or not (acquire or pursue):
            raise ValueError("authored coordination requires an acquiring Trooper")
        enforcer_controller.require_trooper_controller()
    entity = match.combat.next_bot
    if (not bots.BOT_ENTITY <= entity < 0xD0000000 or entity in match.combat.deployables
            or any(b.entity == entity for b in match.bots)):
        raise ValueError('NPC entity allocator exhausted or collided')
    data = {'hero': f'0x{definition.hero:016X}', 'team': team, 'position': position,
            'rotation': (0.0, 0.0, 0.0, 1.0), 'graphs': [], 'vars': {}, 'manual_spawn': True}
    if hero_target:
        data['manual_hero_target'] = True
    if definition.controller is not None:
        data['controller_definition'] = f'0x{definition.controller:016X}'
    hero = {'name': definition.name, 'body': f'0x{definition.body:016X}', 'health': definition.health}
    if autonomous:
        data["autonomous_targeting"] = True
    if difficulty_index is not None:
        data["difficulty_index"] = difficulty_index
    if acquire or pursue:
        data["acquire_targets"] = True
    if specials:
        data["acquire_specials"] = True
    if authored_trooper:
        data["authored_trooper"] = True
    bot = bots.Bot(entity, data, hero, {}, mover=definition.mover)
    if pet_owner is not None:
        bot.pet_owner = pet_owner
    if pursue:
        bot.pursuit = npc_navigation.Pursuit()
    bot.yaw = float(bots.s16(yaw))
    bot.retain_corpse = True
    match.combat.next_bot += 1
    match.bots.append(bot)
    match.combat.vitals[entity] = combat.Vitals(
        entity, definition.health, definition.armour, definition.shields)
    if current_health is not None:
        match.combat.vitals[entity].now[combat.HEALTH_POOL] = float(current_health)
    script_options = {}
    if born_tick is not None:
        script_options["born_tick"] = born_tick
    if source_entity is not None:
        script_options["source_entity"] = source_entity
    match._prepare_bot_script(bot, **script_options)
    if difficulty_index is not None and bot.body_script is None:
        match.bots.remove(bot)
        match.combat.vitals.pop(entity, None)
        raise BodyScriptUnavailable('required body script failed before publication')
    match.combat._sync_body_health(bot, match.combat.vitals[entity])
    match.combat.record_replacement(entity)
    if authored_assassin:
        from ow174.game import assassin_controller

        controller = assassin_controller.Phase2World(
            match.combat, bot, native_state_misses=("STU_356628FC",),
        )
        assassin_controller.mount_phase2(match.combat, bot, controller)
    match.publish_bot(bot)
    return bot


def remove_body(match, bot) -> None:
    """Retire one present body using the shared NPC cleanup authority."""
    if bot not in match.bots:
        return
    if bot.control_mover is not None:
        from ow174.game import body_entities

        pilot = match._message_player(bot.source_body)
        if pilot is not None and pilot.controlled_mech is bot:
            body_entities.release_control(pilot)
    from ow174.game import barriers

    barriers.retire_owner(match.combat, bot)
    match.combat.junkrat.release_trap_victim(bot.entity)
    match.combat.clear_pins(bot.entity)
    match.combat.knockbacks.retire(bot.entity)
    match.combat.slams.retire_target(bot.entity)
    match.combat.support_effects.retire(bot.entity)
    match.combat.healing.retire(bot.entity)
    match.combat.sound_barriers.retire(bot.entity)
    match.combat.lucio_auras.retire(bot.entity)
    match.combat.sombra_controls.retire(bot.entity)
    bot.died(match.tick)
    bot.navigation = None
    match.combat._end_npc_hit_reaction(bot.entity)
    match.combat.clear_pins(bot.entity)
    script, bot.body_script = bot.body_script, None
    if script is not None:
        script.retire_controller()
        component = script.component
        match.unregister_body_component(component)
        component.disconnect_foreign_watchers()
        from ow174.game import assassin

        assassin.bind(component.world, component, None)
        component.owner_dead = True
        component.held.clear()
        component.shots.clear()
        for instance in list(component.instances.values()):
            component.destroy(instance)
        if hasattr(component.world, 'match'):
            component.world.match = None
            component.world.manual_target = None
    match.combat.cancel_npc_projectiles(bot)
    match.combat.respawns[:] = [(when, who) for when, who in match.combat.respawns if who is not bot]
    for table in (match.combat.vitals, match.combat.history, match.combat.replacement_ticks,
                  match.combat.melee_seen, match.combat.genji_assists, match.combat.stuns,
                  match.combat.projected_barriers, match.combat.input_locks, match.combat.booster_contacts):
        table.pop(bot.entity, None)
    match.combat.brigitte.retire(bot.entity)
    match._pending_bot_scripts = type(match._pending_bot_scripts)(
        item for item in match._pending_bot_scripts if item is not bot)
    match.bots.remove(bot)
    for viewer in match.players:
        if viewer.client is not None and viewer.slot in match.bots_shown:
            viewer.client.queue_entities([world.EntityUpdate(bot.entity, world.OP_DESTROY)])


def remove_spawned(match, *, entities=None) -> tuple[int, ...]:
    """Explicitly remove only manual NPCs, including corpses and owned attacks."""
    removed = []
    for bot in tuple(match.bots):
        if ((entities is None and bot.spawner.get('manual_spawn') is True)
                or (entities is not None and bot.entity in entities)):
            remove_body(match, bot)
            removed.append(bot.entity)
    return tuple(removed)


def trooper_coordination_targets(fight, owner, state):
    """Reconstruct 1034's coordination search over live Trooper bodies."""
    source = next((bot for bot in fight.match.bots if bot.entity == owner), None)
    if source is None or source.dead or source.body != TROOPER_BODY:
        return ()
    radius = state.evaluate(state.node.field("m_radius"), watch=False)
    if type(radius) not in (int, float) or radius <= 0:
        return ()
    _, y, _ = source.position
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
        if npc_navigation.visible(fight.match.collision_world, source.position, target):
            candidates.append((distance, target.entity))
    return tuple(entity for _, entity in sorted(candidates))


def update_acquisition(fight, bot):
    """Opt-in target selection for authored weapon, melee and special inputs."""
    from ow174.game import assassin, assassin_controller, enforcer_controller, heavy_assault, sniper_retreat

    component = bot.body_script.component
    if component.strict or component.fault is not None:
        return
    specials = bot.spawner.get('acquire_specials')
    controls = getattr(fight, "sombra_controls", None)
    locked = controls is not None and bot.entity in controls.locks
    charge_ability = heavy_assault.ability(component, 0x1408) if specials and bot.hero == heavy_assault.HERO else None
    charge = charge_ability.instance if charge_ability is not None else None
    phase = charge.states.get(36) if charge is not None else None
    if phase is not None and phase.active:
        return  # Keep the heading established by the charge input.
    x, y, z = bot.position
    origin = x, y + combat.EYE, z
    candidates, blocked, approach = [], [], []
    pursuit = getattr(bot, "pursuit", None)
    if not locked and not fight.is_asleep(bot) and not fight.is_stunned(bot):
        for target in fight._enemies(bot):
            if getattr(target.owner, "client", None) is None:
                continue
            point = fight._eye(target.owner)
            distance = math.dist(origin, point)
            if not 0 < distance <= ACQUISITION_RANGE:
                continue
            in_reach = bot.hero != assassin.HERO or assassin.selected_contact(fight, bot, target.entity) is not None
            if in_reach and npc_navigation.visible(fight.match.collision_world, bot.position, target):
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
    ground_motion = (specials and not locked and getattr(bot, "wall_mover", None) is None
                     and not fight.junkrat.rooted(bot.entity, fight.tick))
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
            inputs.append((35, charging))
    component.edges([(value, down) for value, down in inputs if (value in component.held) != down])


def aim_at(match, bot: bots.Bot, point) -> tuple[int, int]:
    """Explicit fixture/controller aim; autonomous acquisition is a separate service."""
    if bot not in match.bots or bot.dead:
        raise ValueError('aim needs a living NPC in this match')
    point = tuple(point)
    if len(point) != 3 or any(type(v) not in (int, float) or not math.isfinite(v) for v in point):
        raise ValueError('NPC aim needs three finite coordinates')
    x, y, z = bot.position
    eye = bot.combat_geometry[2] if bot.combat_geometry else combat.EYE
    dx, dy, dz = point[0] - x, point[1] - y - eye, point[2] - z
    if dx * dx + dy * dy + dz * dz < combat.EPSILON:
        raise ValueError('NPC aim target coincides with muzzle')
    bot.yaw = bots.s16(round(math.atan2(dx, dz) * 65536 / (2 * math.pi)))
    bot.pitch = bots.s16(round(-math.atan2(dy, math.hypot(dx, dz)) * 65536 / (2 * math.pi)))
    bot.moved_tick = match.tick
    return bot.yaw, bot.pitch
