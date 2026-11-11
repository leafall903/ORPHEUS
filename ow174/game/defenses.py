"""Declared resource-phase defense hosts; not a native status-manager port."""

import math

from ow174.game.script import expr, graph, nodes, runtime

REAPER = 0x02E0000000000002
MEI = 0x02E00000000000DD
GENJI = 0x02E0000000000029
SIGMA = 0x02E000000000023B
HAND_CANNON = 0x0B90000000000109


def wraith(player):
    if player.hero.guid != REAPER or player.body_script is None:
        return None
    component = player.body_script.component
    if component.strict:
        return None
    return next((i for i in component.instances.values() if i.graph.index == 0x20), None)


def advance(player, command):
    """Complete the authored presentation gates for Wraith and Cryo.

    Shared C880C0 finishes on timer1. Its engine scheduling service is missing;
    this host uses the authored ED97 delay (zero in0020/node2), once active.
    The actual graph then owns its3s phase, cancellation, exit and cooldown.
    """
    if command is None:
        return
    if player.hero.guid == MEI:
        advance_cryo(player)
        return
    instance = wraith(player)
    if instance is None:
        return
    gate = instance.states.get(1)
    if gate is not None and gate.active:
        delay = expr.to_float(gate.evaluate(gate.node.field('m_ED97A97C'), watch=False))
        if instance.component.now - gate.start >= delay * 1000:
            gate.request(runtime.FINISH)
            instance.component.run_now()


def cryo(player):
    if player.hero.guid != MEI or player.body_script is None:
        return None
    component = player.body_script.component
    if component.strict:
        return None
    return next((i for i in component.instances.values() if i.graph.index == 0x05FE), None)


def advance_cryo(player):
    """Finish Mei's presentation gate to enter her authored protective branch."""
    instance = cryo(player)
    if instance is None:
        return
    gate = instance.states.get(41)  # 05FE authored presentation gate
    if gate is None or not gate.active:
        return
    gate.request(runtime.FINISH)
    instance.component.run_now()
    phase = instance.states.get(6)
    if not isinstance(phase, nodes.ChaseVar) or not phase.active:
        return
    destination = phase.evaluate(phase.node.field("m_destination"), watch=False)
    seconds = max(0.0, expr.to_float(phase.cur) - expr.to_float(destination))
    from ow174.game.combat import TICK

    player.body_script.world.cryo_until_tick = player.match.tick + math.ceil(seconds / TICK)


def expire_cryo(players, tick):
    """Retire cover at the server deadline even when Mei sends no commands."""
    for player in players:
        script = player.body_script
        if script is not None and 0 < script.world.cryo_until_tick <= tick:
            script.world.cryo_until_tick = 0


def cryo_capsule(player):
    """Use the authored 090B.003 pet capsule while Mei's protective branch is active."""
    if not player.has_body or not player.spawned or player.gone or player.client is None:
        return None
    instance = cryo(player)
    if instance is None:
        return None
    script = player.body_script
    if (script.retired or script.entity != player.body or script.component.owner_dead
            or script.component.fault is not None or player.match.tick >= script.world.cryo_until_tick):
        return None
    cast, phase = instance.states.get(0), instance.states.get(6)
    if not (isinstance(cast, nodes.Ability) and cast.flags & nodes.Ability.RUNNING
            and phase is not None and phase.active and not phase.ending):
        return None
    x, y, z = player.mover.position
    return (x, y + .5, z), (x, y + 1.5, z), .5


def cryo_blockers(players, *, exclude=None):
    return tuple(capsule for player in players if player is not exclude
                 and (capsule := cryo_capsule(player)) is not None)


def protected(match, entity):
    """Reconstruct no-damage policy for the authored Wraith and Cryo phases.

    Resource0020's state0 scopes registered9028/type0 and entity530=True.
    Native consumers of these flags, cleanse/stacking and presentation remain
    open. This policy only covers actual player bodies running that phase.
    """
    player = next((p for p in match.players if p.has_body and p.body == entity), None)
    if player is not None and cryo_capsule(player) is not None:
        return True
    instance = wraith(player) if player is not None else None
    if instance is None:
        return False
    phase, ability = instance.states.get(0), instance.states.get(10)
    flag = instance.component.entity_var(530, create=False)
    return (phase is not None and phase.active and ability is not None
            and bool(ability.flags & nodes.Ability.RUNNING)
            and flag is not None and expr.truthy(flag.value()))


def genji(player):
    if getattr(getattr(player, 'hero', None), 'guid', None) != GENJI or player.body_script is None:
        return None
    component = player.body_script.component
    if component.strict:
        return None
    return next((i for i in component.instances.values() if i.graph.index == 0x3A3), None)


def deflect(player):
    instance = genji(player)
    if instance is None:
        return None
    phase, ability = instance.states.get(10), instance.states.get(0)
    return (instance if phase is not None and phase.active and ability is not None
            and ability.flags & nodes.Ability.RUNNING else None)


def grasp(player):
    if getattr(getattr(player, 'hero', None), 'guid', None) != SIGMA or player.body_script is None:
        return None
    component = player.body_script.component
    if component.strict:
        return None
    instance = next((i for i in component.instances.values() if i.graph.index == 0x1D02), None)
    if instance is None:
        return None
    phase, ability = instance.states.get(1), instance.states.get(0)
    return (instance if phase is not None and phase.active and ability is not None
            and ability.flags & nodes.Ability.RUNNING else None)


def _basis(player):
    from ow174.game.combat import aim

    forward = aim(player.mover.yaw, player.mover.pitch)
    yaw = player.mover.yaw * 2 * math.pi / 65536
    right = (math.cos(yaw), 0.0, -math.sin(yaw))
    up = (forward[1] * right[2], forward[2] * right[0] - forward[0] * right[2],
          -forward[1] * right[0])
    return right, up, forward


def _box(origin, direction, corner_a, corner_b, limit):
    first, last = 0.0, limit
    for value, velocity, a, b in zip(origin, direction, corner_a, corner_b, strict=True):
        low, high = min(a, b), max(a, b)
        if abs(velocity) < 1e-12:
            if value < low or value > high:
                return None
            continue
        near, far = sorted(((low - value) / velocity, (high - value) / velocity))
        first, last = max(first, near), min(last, far)
        if first > last:
            return None
    return first


def reflection(combat, rocket, distance):
    """Sweep supported direct projectiles through Genji's authored boxes.

    Native animated box transforms,0145 projectile filter, aim projection,
    output metadata and visual ownership transfer remain open.
    """
    found = _interception(combat, rocket, distance, absorbing=False)
    return found[:3] if found is not None else None


def absorption(combat, rocket, distance):
    """Declared Sigma mode1 response for supported direct projectiles."""
    found = _interception(combat, rocket, distance, absorbing=True)
    return (found[0], found[1], found[3]) if found is not None else None


def _interception(combat, rocket, distance, *, absorbing):
    if rocket.direct is None or (rocket.direct.loadout != HAND_CANNON and not rocket.npc_direct_projectile):
        return None
    return ray_interception(combat, rocket.shooter, rocket.team, rocket.position, rocket.direction,
                            distance, absorbing=absorbing)


def ray_interception(combat, shooter, team, position, heading, distance, *, absorbing):
    from ow174.game.combat import _dot, _sub

    best = None
    for player in combat.match.players:
        if (player is shooter or not player.has_body or not player.spawned
                or player.client is None or player.gone or combat.is_dead(player)
                or combat.is_stunned(player)
                or (not combat.match.game_map.free_for_all and player.team == team)):
            continue
        instance = grasp(player) if absorbing else deflect(player)
        if instance is None:
            continue
        state = instance.state(1 if absorbing else 10)
        basis = _basis(player)
        angle = float(state.evaluate(state.node.field('m_025D0C39'), watch=False))
        threshold = math.cos(math.radians(angle))
        if not absorbing:
            threshold = max(0.0, threshold)
        if -_dot(heading, basis[2]) <= threshold:
            continue
        relative = _sub(position, combat._eye(player))
        origin = tuple(_dot(relative, axis) for axis in basis)
        direction = tuple(_dot(heading, axis) for axis in basis)
        for box in state.node.field('m_boxes') or ():
            a = state.evaluate(box.get('m_CBA118B2'), watch=False)
            b = state.evaluate(box.get('m_AEF67365'), watch=False)
            if not isinstance(a, expr.Vec3) or not isinstance(b, expr.Vec3):
                continue
            hit = _box(origin, direction, a, b, distance)
            if hit is not None and (best is None or hit < best[0]):
                best = hit, player, basis[2], state
    return best


def absorbed(state, amount):
    """Supply the declared damage output; the actual graph accumulates21312.

    Native per-projectile output marshaling/metadata and stat consumers remain
    open. This adapter supplies direct projectile damage at interception.
    """
    instance = state.instance
    instance.write(expr.dynamic(state.node.field('m_F6CDC81C')), float(amount))
    instance.follow(state.node, 'm_160B5DBE', state)
    instance.component.run_now()


def play_shield_script(combat, body, instance, node):
    """Scoped1D02 ->1D03 health-pool reconstruction, not general PlayScript.

    Native tagged pools, scaled rulesets, remote instance creation and visual
    lifetime remain open. The actual graph selects normal finish versus abort,
    accumulates damage and evaluates0.6 conversion/cap; this host supplies its
    default-ruleset aggregate pool,3s delay and authored5/s decay.
    """
    if instance.component.strict or instance.graph.index != 0x1D02 or node.pos != 34:
        return
    player = next((p for p in combat.match.players if p.has_body and p.body == body), None)
    if (player is None or player.hero.guid != SIGMA or combat.is_dead(player)
            or node.field('m_script.m_graph') != '0x0580000000001D03'):
        return
    params = {graph.guid(row.get('m_B5051BCE')): expr.to_float(instance.evaluate(row.get('m_value')))
              for row in node.field('m_params') or ()}
    cap, amount = params.get(0x0A400000000003D1, 0), params.get(0x0A4000000000002A, 0)
    delay = expr.to_float(instance.state(29).evaluate(instance.graph.node(71).field('m_timeout')))
    decay = expr.to_float(instance.state(28).evaluate(
        instance.graph.node(70).field('m_modifyHealth.m_amount')))
    if not all(math.isfinite(v) and v >= 0 for v in (cap, amount, delay, decay)):
        return
    if amount > 0 and cap > 0:
        # A later VM failure must discard this effect, like health/motion
        # reports. Combat commits only a living, posed player's result.
        instance.component.pool_reports.append((amount, cap, decay, delay))


def blocks_melee(combat, attacker, target, attack_state):
    """Scoped03A3 combat-mod tag intersection and authored forward-dot gate.

    Damage parameter bindings and the full native combat-mod filter service
    remain open. This adapter covers the actual quick-melee host only.
    """
    from ow174.game.combat import _dot, _sub

    instance = genji(target.owner)
    if instance is None:
        return False
    state = instance.states.get(59)
    if (state is None or not state.active
            or not instance.state(0).flags & nodes.Ability.RUNNING):
        return False
    tags = {row.get('m_EE3055BD', {}).get('m_identifier')
            for row in attack_state.node.field('m_modifyHealth.m_148F3152') or ()}
    if not tags.intersection(state.node.field('m_F60AA143') or ()):
        return False
    if _dot(_sub(attacker.mover.position, target.feet), _basis(target.owner)[2]) <= 0:
        return False
    return expr.to_float(state.evaluate(state.node.field('m_50530EAB'), watch=False)) == 0
