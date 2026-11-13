"""Echo bindings for the authored Flight/Glide graphs and common kind16 mover.

DATA: Flight node26 queues vertical SET0 then direction*28; node42 caps
speed with the authored chase variable. Glide has limit4 and speed+.5.
The common kind16 mover consumes the actual graph's published values.
Native transform equality, prediction and client animation remain unaccepted.
"""
import math
from dataclasses import replace

from ow174.game.commands import JUMP
from ow174.game.mover import mover_data

ECHO = 0x02E0000000000206
# Same authored component fields as tools/extract_game_movers.py. No asset IO here.
MOVER_FIELDS = {
    '1972C687': 'stand_height', '7A70E3E3': 'stand_radius', '4A576BC2': 'stand_pogo',
    '03E04168': 'crouch_height', '915FF368': 'crouch_radius', 'D97B3735': 'crouch_pogo',
    'D4AE9D05': 'ground_circle_radius', '5C6B4602': 'ground_circle_rays',
    '6B7FDB90': 'pogo_frequency', 'E54438F7': 'pogo_damping', 'AEBAD1B0': 'max_slope',
    '08FFAA0A': 'step_down', 'CFD3E635': 'run_forward', '61D6B4E9': 'run_backward',
    '19452F15': 'run_strafe', 'B470B8DA': 'crouch_forward', 'AA6F4CE1': 'crouch_backward',
    '0DDD18C3': 'crouch_strafe', 'DAA0618B': 'accel_ground', 'E631BA84': 'accel_air',
    'D24BF4E6': 'accel_air_touching', 'E0A0FE25': 'decel_ground_base',
    '6F25AA23': 'decel_ground_base_speed', '4E03E892': 'decel_ground_per_speed',
    '142DA8D4': 'decel_air_base', '031FE7EE': 'decel_air_base_speed',
    '8895A9C8': 'decel_air_per_speed', 'EC289987': 'jump_speed', 'C2C40969': 'gravity_scale',
    'B74245E8': 'terminal_velocity', '975F21A5': 'terminal_velocity_up',
    'B99EC983': 'ground_gravity_accel', 'CEF4898C': 'ground_gravity_decel',
    'D4A3C694': 'character_collision',
}


def native(world, cfg, instance):
    """Bind 1BB6's own-body lateral basis used by its authored launch expression.

    This uses the same authoritative yaw basis as the existing Roll bridge;
    animated/native transform equivalence still requires client acceptance.
    """
    from ow174.game.script import expr

    if (world.hero != ECHO or world.match is None or instance is None
            or instance.graph.index != 0x1BB6 or cfg.get('$') != 'STU_66F3B89E'
            or expr.to_entity(instance.evaluate(cfg.get('m_entity'))) != world.body):
        return NotImplemented
    player = next((p for p in world.match.players if p.body == world.body
                   and p.has_body and not p.gone and p.body_script is not None
                   and p.body_script.component is instance.component), None)
    if player is None:
        return NotImplemented
    yaw = player.mover.yaw * math.tau / 65536
    return expr.Vec3(expr.f32(math.cos(yaw)), 0.0, expr.f32(-math.sin(yaw)))


def flight(player):
    script = player.body_script
    if player.hero.guid != ECHO or script is None or script.component.owner_dead:
        return None
    instance = next((i for i in script.component.instances.values() if i.graph.index == 0x1BB6), None)
    if instance is None:
        return None
    mode = instance.states.get(15)
    ability = instance.states.get(0)
    if mode is not None and mode.active and ability is not None and ability.flags & 1:
        return instance
    return None


def glide_properties(player, buttons):
    script = player.body_script
    if (player.hero.guid != ECHO or script is None or script.component.owner_dead
            or not buttons & JUMP or not player.mover.airborne or flight(player) is not None):
        return None
    if script.component.disabled.get(11):
        return None
    instance = next((i for i in script.component.instances.values() if i.graph.index == 0x1DFC), None)
    if instance is None:
        return None
    limit, bonus = None, 0.0
    for mod in instance.graph.states[8].field('m_BC5E91CF') or ():
        if mod.get('m_A9561CA0') == 13:
            limit = float(instance.evaluate(mod['m_9B7A63EA']))
        elif mod.get('m_A9561CA0') == 0:
            bonus = float(instance.evaluate(mod['m_9B7A63EA']))
    return None if limit is None else (limit, 1.0 + bonus)


def glide_limit(player, command):
    properties = glide_properties(player, command.buttons)
    return None if properties is None else properties[0]


def effective_data(player):
    base = mover_data(player.hero.body)
    instance = flight(player)
    if instance is None:
        script = player.body_script
        properties = glide_properties(player, script.buttons if script is not None else 0)
        if properties is None:
            return base
        limit, scalar = properties
        return replace(base, terminal_velocity=limit,
                       run_forward=base.run_forward * scalar, run_backward=base.run_backward * scalar,
                       run_strafe=base.run_strafe * scalar, crouch_forward=base.crouch_forward * scalar,
                       crouch_backward=base.crouch_backward * scalar,
                       crouch_strafe=base.crouch_strafe * scalar)
    state = instance.states.get(6)
    if state is None or not state.active:
        return base
    for mod in state.node.field('m_BC5E91CF') or ():
        if mod.get('$') == 'STU_ED2B22BD':
            fields = mod.get('m_9B7A63EA') or {}
            values = {name: fields['m_' + key] for key, name in MOVER_FIELDS.items() if 'm_' + key in fields}
            return replace(base, **values)
    return base
