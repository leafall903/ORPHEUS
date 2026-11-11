"""Incremental Moira effect reconstruction using the authored ability lifecycle.

This module does not implement the missing native state/context/filter services.
See docs/MOIRA_COMBAT.txt for assumptions and unaccepted interactions.
"""

import math
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

from ow174.game import absorption, bots, combat, deflection, sigma_barrier, stats
from ow174.game.script import entity_filter, expr, nodes, runtime
from ow174.game.script.target_volume import Volume

MOIRA = 0x02E00000000001A2
TIMED_CASTS = {0x0F7C: 9, 0x1007: 0}  # Fade and Coalescence ability states
# 1566/state6 authors pool 03EBB, the adjustment selected by 0B8B/node84.
ADAPTIVE_SHIELD_TAG = 0x3EBB
HEAL_ORB, DAMAGE_ORB = 0x0400000000001E60, 0x0400000000001E61
# Target-build entities1E60/1E61 -> graphs10FF/1100: radius5/4, aura
# parameter002A=65/50, budgets300/200, D3F71FCF scalar0.275.
ORB_SLOW = 0.275
SPRAY_TAIL_TAGS = (0x0D80000000002F9F,)  # 10B3 ModifyHealth
DAMAGE_ORB_TAGS = (0x0D80000000002DFD, 0x0D80000000002F99, 0x0D80000000004D29, 0x0D80000000004910)  # 1101
REFLECTED_DAMAGE_ORB_TAGS = (*DAMAGE_ORB_TAGS[:2], 0x0D800000000019C4, *DAMAGE_ORB_TAGS[2:])
HEAL_ORB_TAGS = (0x0D80000000002DFD, 0x0D800000000031AC)  # 1102 ModifyHealth


class _RecoveryContext(runtime.InstanceContext):
    """Explicit per-command step for the missing damage-callback context.

    4B02509E's compiled getter CF26E0 reads context+10's int64+430 and multiplies
    by f32(.001). This adapter declares that duration as this posed script step;
    native health notification cadence remains a separate acceptance question.
    """

    def native(self, cfg):
        if cfg.get("$") == "STU_4B02509E":
            return expr.f32(self.instance.component.frame_ms * expr.f32(0.001))
        return super().native(cfg)


def _recover_grasp(instance):
    # F76/node48 is authored min(maxFuel, fuel + 3 * stepSeconds / .016).
    # Execute its expression rather than duplicating rates or resetting fuel.
    node = instance.graph.node(48)
    value = expr.evaluate(node.field("m_value"), _RecoveryContext(instance, None))
    instance.write(expr.lvalue(node.field("m_out_Var")), value)


def _active_npc_charge_tag(target):
    script = getattr(target.owner, "body_script", None)
    if script is None or script.component.entity != target.entity:
        return False
    for instance in script.component.instances.values():
        if instance.graph.index != 0x0E62:
            continue
        state = instance.states.get(7)
        if state is None or not state.active:
            continue
        tag = state.evaluate(state.node.field("m_targetTags"), watch=False)
        recipient = state.evaluate(state.node.field("m_31714AFC"), watch=False)
        if tag == expr.Asset(0x0D600000000003C3) and recipient == expr.Entity(target.entity):
            return True
    return False


def damage_charge_query(world, cfg, instance):
    """Host the 0B8B queries for the match's player and NPC combat bodies."""
    cls = cfg.get("$")
    if cls == "STU_AC829876" and (cfg.get("m_filter") or {}).get("m_84065FD1") != 1:
        return NotImplemented
    match = world.match
    if match is None:
        return NotImplemented
    target_id = instance.evaluate(cfg.get("m_target") if cls == "STU_A58C1E63" else cfg.get("m_entity"))
    targets = match.combat._targets()
    target = next((actor for actor in targets if expr.Entity(actor.entity) == target_id), None)
    if target is None:
        return False
    if cls == "STU_AC829876":
        return True
    if cls == "STU_A58C1E63":
        source_id = instance.evaluate(cfg.get("m_source"))
        source = next((actor for actor in targets if expr.Entity(actor.entity) == source_id), None)
        return source is not None and source.entity != target.entity and (
            match.game_map.free_for_all or source.team != target.team
        )
    tag = expr.guid_of(cfg.get("m_88B0050A"))
    if tag == 0x0D600000000003C3:
        return _active_npc_charge_tag(target)
    if tag == 0x0D6000000000011E:
        return getattr(getattr(target.owner, "hero", None), "body", None) == 0x040000000000028F
    return False


def start_charge(player, script):
    """Translate the first active match command to 01C7's authored start message."""
    from ow174.game.script.driver import ULT_GAIN_ENABLED

    component = script.component
    enabled = component.entity_var(ULT_GAIN_ENABLED, create=False)
    if enabled is not None and enabled.value() is not None:
        return
    common = next((item for item in component.instances.values() if item.graph.index == 0x01C7), None)
    if common is not None:
        runtime.send_instance_message(common, 0x0240000000000694, {}, player.entity)


def gain_combat_charge(player, amount, *, body=None, healing_target=None, healing_tags=(),
                       damage_target=None, damage_tags=(), adaptive_loss=0.0):
    """Use the authored gain arithmetic when the host supplies its enablement.

    Hit-local values project the effective delta and the reconstructed pool loss.
    Healing uses message 44 and damage uses message 12.
    """
    if getattr(getattr(player, "hero", None), "guid", None) != MOIRA or amount <= 0:
        return
    script = player.body_script
    if script is None or (body is not None and body != player.body):
        return
    match = script.world.match
    if match is not None and match.assembling():
        return
    if any(state.flags & nodes.Ability.RUNNING for state in script.ultimates):
        return
    # Import only at effect time to keep the combat/driver module graph acyclic.
    from ow174.game.script.driver import ULT_CHARGE, ULT_COST, ULT_GAIN_ENABLED, ULT_GAIN_GRAPH

    component = script.component
    cost = expr.to_float(component.entity_var(ULT_COST).value())
    charge = expr.to_float(component.entity_var(ULT_CHARGE).value())
    if cost > 0 and charge < cost:
        enabled = component.entity_var(ULT_GAIN_ENABLED, create=False)
        if enabled is not None and enabled.value() is not None:
            instance = next((item for item in component.instances.values()
                             if item.graph.index == ULT_GAIN_GRAPH), None)
            if instance is not None:
                if healing_target is not None:
                    params = {
                        0x40: amount, 0x41: tuple(expr.Asset(tag) for tag in healing_tags),
                        0x3F: expr.Entity(healing_target.entity),
                    }
                    runtime.send_instance_message(instance, 0x0240000000000044, params, script.entity)
                    component.run_now()
                    return
                if damage_target is not None:
                    adjustments = ()
                    if adaptive_loss > 0:
                        identifier = expr.Asset(0x0D80000000000000 | ADAPTIVE_SHIELD_TAG)
                        adjustments = (expr.Map(((16056, identifier), (499, adaptive_loss))),)
                    params = {
                        0x13: amount, 0x15: expr.Entity(damage_target.entity),
                        0x3D: tuple(expr.Asset(tag) for tag in damage_tags), 0x624: adjustments,
                    }
                    runtime.send_instance_message(instance, 0x0240000000000012, params, script.entity)
                    component.run_now()
                    return
        script.set_ult_charge(min(cost, charge + max(0.0, amount - adaptive_loss)))


@dataclass
class Orb:
    shooter: object
    body: int
    position: tuple
    direction: tuple
    speed: float
    left: float
    energy: float
    healing: bool
    last_tick: int
    shot: object = None
    flown: float = 0.0
    bounces: int = 0
    absorption_amount: float = 0.0  # authored emitted volley's nominal value, not remaining aura energy
    launch_body: int | None = None

    def __post_init__(self):
        if self.launch_body is None:
            self.launch_body = self.body


@dataclass
class SprayHeal:
    healer: object
    target: int
    begins: float
    expires: float
    last_tick: int
    remaining: float = 35.0
    source_body: int | None = None


@dataclass
class SprayTrace:
    """Declared finite-width, body-piercing reconstruction for each spray packet.

    F76/st6's hit cfg contains m_71A4B759=.75. Treating that number as
    a sweep radius is an explicit inference, not a restored native shape.
    """

    radius: float
    source_body: int
    hits: set[int] = field(default_factory=set)


def spray_trace(player, state, definition):
    if (getattr(getattr(player, 'hero', None), 'guid', None) != MOIRA
            or state.instance.graph.index != 0xF76 or state.index != 6
            or definition is None or not definition.healing):
        return None
    cfg = state.node.field('m_6396149F') or {}
    radius = combat._number(state.evaluate(cfg.get('m_71A4B759'), watch=False), 0)
    return SprayTrace(max(0.0, radius), player.body)


def fly_spray(fight, rocket, duration):
    """Pass through eligible bodies once; static collision terminates the packet.

    A widened capsule sweep is an explicit geometry approximation. Visibility
    to the nearest actual capsule surface prevents width leaking through walls.
    Neither teammates nor hostile bodies consume the packet. Its finite flight
    remains governed by the authored speed/lifetime/range, like other volleys.
    """
    trace = rocket.spray
    distance = rocket.speed * duration
    wall = combat.ray_world(fight._collision(), rocket.position, rocket.direction, distance)
    pillar = fight.walls.cast(rocket.position, rocket.direction, distance)
    if pillar is not None and (wall is None or pillar[0] < wall):
        wall = pillar[0]
    limit = min(distance, wall) if wall is not None else distance
    shield = fight._barrier_contact(rocket.shooter, rocket.position, rocket.direction, limit, trace.radius, bubbles=True)
    if shield is not None:
        limit = min(limit, shield[0])
    contacts = []
    for target in fight._hit_targets(rocket.shooter, rocket.direct):
        if target.entity in trace.hits:
            continue
        a, b = target.segment()
        hit = combat.ray_capsule(rocket.position, rocket.direction, a, b, target.radius + trace.radius)
        if hit is None or hit > limit:
            continue
        point = combat._along(rocket.position, rocket.direction, hit)
        nearest = (a[0], min(max(point[1], a[1]), b[1]), a[2])
        delta = combat._sub(nearest, point)
        length = math.sqrt(combat._dot(delta, delta))
        surface = max(0.0, length - target.radius)
        if surface > combat.EPSILON:
            direction = tuple(value / length for value in delta)
            obstruction = combat.ray_world(fight._collision(), point, direction, surface)
            if ((obstruction is not None and obstruction < surface)
                    or fight.walls.cast(point, direction, surface) is not None):
                continue
        contacts.append((hit, target))
    for _hit, target in sorted(contacts, key=lambda row: (row[0], row[1].entity)):
        trace.hits.add(target.entity)
        fight._apply_hit(rocket.shooter, target, rocket.direct, rocket.direct.amount, False, fight.tick,
                         world_tick=fight.tick, source_entity=trace.source_body)
    rocket.position = combat._along(rocket.position, rocket.direction, limit)
    elapsed = limit / rocket.speed
    rocket.flown += elapsed
    rocket.left = max(0.0, rocket.left - elapsed)
    if wall is not None or shield is not None or rocket.left <= combat.EPSILON:
        fight._landed(rocket)


def spray_hit(fight, healer, target, definition, *, source_body=None):
    """10B4->10B3 reconstructs one refreshed trailing heal, not stacked taps.

    The graph delays0.2s, then distributes35 over the two-second Wait lifetime.
    Native PlayScript/aura binding/clear services remain unhosted. Do not infer
    the unrelated F76 time-since-input variables as the healing resource pool.
    """
    if getattr(healer.hero, "guid", None) != MOIRA or definition.loadout != 0x0B9000000000014A:
        return
    begins = fight.tick * combat.TICK + 0.2
    fight.moira_hots[target.entity] = SprayHeal(healer, target.entity, begins, begins + 2.0, fight.tick,
                                              source_body=healer.body if source_body is None else source_body)


def advance_spray_heals(fight):
    if not fight.moira_hots:
        return
    targets = {target.entity: target for target in fight._targets()}
    for entity, effect in list(fight.moira_hots.items()):
        target = targets.get(entity)
        if (target is None or fight.vitals_of(target).dead or fight.match.game_map.free_for_all
                or target.team != effect.healer.team):
            del fight.moira_hots[entity]
            continue
        start = max(effect.last_tick * combat.TICK, effect.begins)
        end = min(fight.tick * combat.TICK, effect.expires)
        effect.last_tick = max(effect.last_tick, fight.tick)
        if end > start:
            given = fight._heal_pools(target.owner, fight.vitals_of(target),
                                      min(effect.remaining, (end - start) * 17.5), tags=SPRAY_TAIL_TAGS)
            # Time consumes the trailing pulse even when the recipient is full.
            effect.remaining = max(0.0, effect.remaining - (end - start) * 17.5)
            if given > 0:
                effect.healer.stats.add(stats.HEALING, given)
                gain_combat_charge(effect.healer, given, body=effect.source_body,
                                   healing_target=target, healing_tags=SPRAY_TAIL_TAGS)
        if fight.tick * combat.TICK >= effect.expires or effect.remaining <= combat.EPSILON:
            del fight.moira_hots[entity]


def orb_fired(fight, player, state, definition, speed, lifetime, origin, direction, shot) -> bool:
    """Admit only an actual emitted Moira orb volley, not raw ability input."""
    if (getattr(player.hero, "guid", None) != MOIRA or state.instance.graph.index != 0x10E4
            or definition not in (HEAL_ORB, DAMAGE_ORB)):
        return False
    healing = definition == HEAL_ORB
    direct = combat.hit_def(state, state.node.field('m_6396149F'))
    # 10FF/4 and 1100/13 multiply the base budget by the launch owner's ruleset value.
    component = state.instance.component
    key, base = (12618, 300.0) if healing else (12619, 200.0)
    slot = component.entity_var(key, create=False)
    scalar = slot.value() if slot is not None else None
    if scalar is None:
        scalar = component.world.ruleset(key)
    energy = expr.f32(base * expr.to_float(scalar))
    energy = max(0.0, energy) if math.isfinite(energy) else 0.0
    fight.moira_orbs.append(Orb(player, player.body, origin, direction, speed, lifetime,
                               energy, healing, fight.tick, shot,
                               absorption_amount=max(0.0, direct.amount) if direct else 0.0))
    return True


def _orb_targets(fight, orb):
    radius = 5.0 if orb.healing else 4.0
    source = orb.shooter
    result = []
    for target in fight._targets():
        vitals = fight.vitals_of(target)
        if vitals.dead:
            continue
        friendly = target.owner is source or (
            not fight.match.game_map.free_for_all and target.team == source.team)
        if orb.healing != friendly:
            continue
        if orb.healing and sum(vitals.now.values()) >= sum(vitals.most.values()) - combat.EPSILON:
            continue
        if not orb.healing and fight.damage_immune(target.owner):
            continue
        a, b = target.segment()
        if combat.segment_distance(orb.position, a, b) - target.radius > radius:
            continue
        # Reconstructed capsule-centre visibility. Native filter/shape services
        # are not hosted; walls must never be silently treated as transparent.
        centre = (target.feet[0], target.feet[1] + target.height / 2, target.feet[2])
        delta = combat._sub(centre, orb.position)
        distance = math.sqrt(combat._dot(delta, delta))
        if distance > combat.EPSILON:
            direction = tuple(v / distance for v in delta)
            wall = combat.ray_world(fight._collision(), orb.position, direction, distance)
            if ((wall is not None and wall < distance)
                    or fight.walls.cast(orb.position, direction, distance) is not None
                    or fight.barriers.world.trace(source, orb.position, direction, distance) is not None
                    or fight.orisa_barriers.trace(source, orb.position, direction, distance) is not None
                    or fight.photon_barriers.trace(source, orb.position, direction, distance) is not None
                    or sigma_barrier.trace(fight, source, orb.position, direction, distance) is not None):
                continue
        result.append(target)
    return result


def _orb_move(fight, orb, distance, duration):
    """Point wall/Genji sweep with bounded contacts and preserved flight budgets.

    Genji contact reuses his lane's declared capsule/frontal Deflect admission.
    It transfers both health operations and body attribution, retires the old
    prediction, and preserves remaining life/energy. Native boxes/filters and
    reflected client motion remain separate acceptance questions.
    """
    total = distance
    for _ in range(8):
        hit = fight._collision().cast_ray(orb.position, orb.direction, distance)
        wall_distance, normal = (hit.distance, hit.normal) if hit is not None else (distance, None)
        pillar = fight.walls.cast(orb.position, orb.direction, distance)
        if (pillar is not None and any(pillar[2])
                and (normal is None or pillar[0] < wall_distance)):
            wall_distance, _, normal = pillar
        limit = min(distance, wall_distance)
        defender = None
        for target in fight._enemies(orb.shooter):
            if not fight._genji_deflects(target, orb.direction):
                continue
            contact = combat.ray_capsule(orb.position, orb.direction, *target.segment(), target.radius)
            if contact is not None and contact < limit:
                limit, defender = contact, target
        grasp = absorption.grasp_intersection(fight, orb.shooter, orb.position, orb.direction, limit)
        matrix = fight._matrix_hit(
            orb.shooter, orb.position, orb.direction,
            min(limit, grasp[0]) if grasp is not None else limit,
            HEAL_ORB if orb.healing else DAMAGE_ORB,
        )
        if matrix is not None and matrix < limit and (grasp is None or matrix < grasp[0]):
            orb.position = combat._along(orb.position, orb.direction, matrix)
            fraction = (total - distance + matrix) / total if total > 0 else 0
            orb.flown += duration * fraction
            orb.left = max(0.0, orb.left - duration * fraction)
            if orb.shot is not None:
                fight.projectiles.absorbed(orb.shot)
                orb.shot = None
            return True
        if grasp is not None and grasp[0] < limit:
            distance_to, state = grasp
            orb.position = combat._along(orb.position, orb.direction, distance_to)
            fraction = (total - distance + distance_to) / total if total > 0 else 0
            orb.flown += duration * fraction
            orb.left = max(0.0, orb.left - duration * fraction)
            absorption.absorbed(state, orb.absorption_amount)
            if orb.shot is not None:
                fight.projectiles.absorbed(orb.shot)
                orb.shot = None
            return True
        if defender is not None:
            orb.position = combat._along(orb.position, orb.direction, limit)
            if orb.shot is not None:
                fraction = (total - distance + limit) / total if total > 0 else 0
                fight.projectiles.flown(orb.shot, orb.flown + duration * fraction)
                orb.shot = None
            orb.shooter, orb.body = defender.owner, defender.entity
            orb.direction = combat.aim(defender.owner.mover.yaw, defender.owner.mover.pitch)
            distance = max(0.0, distance - limit)
            step = min(1e-4, distance)
            orb.position = combat._along(orb.position, orb.direction, step)
            distance -= step
            if distance <= combat.EPSILON:
                return
            continue
        if normal is None:
            orb.position = combat._along(orb.position, orb.direction, distance)
            return
        orb.position = combat._along(orb.position, orb.direction, wall_distance)
        dot = combat._dot(orb.direction, normal)
        reflected = tuple(d - 2 * dot * n for d, n in zip(orb.direction, normal, strict=True))
        norm = math.sqrt(combat._dot(reflected, reflected))
        if norm <= combat.EPSILON:
            return
        orb.direction = tuple(v / norm for v in reflected)
        orb.bounces += 1
        distance = max(0.0, distance - wall_distance)
        step = min(1e-4, distance)
        orb.position = combat._along(orb.position, orb.direction, step)
        distance -= step
        if distance <= combat.EPSILON:
            return


def advance_orbs(fight):
    """World-clock orbs survive their caster's death and stop at energy/lifetime.

    Integrate in <=16ms slices; spend only actual health changed, cap the shared
    per-orb energy across all targets, and slow only while an eligible tether
    exists. Native aura modifiers, Deflect boxes/filters and absorption remain open.
    """
    for orb in list(fight.moira_orbs):
        elapsed = max(0, fight.tick - orb.last_tick) * combat.TICK
        orb.last_tick = fight.tick
        while elapsed > combat.EPSILON and orb.left > combat.EPSILON and orb.energy > combat.EPSILON:
            dt = min(combat.TICK, elapsed, orb.left)
            targets = _orb_targets(fight, orb)
            if _orb_move(fight, orb, orb.speed * (ORB_SLOW if targets else 1.0) * dt, dt):
                fight.moira_orbs.remove(orb)
                break
            # A contact can transfer owner/team in this slice. Reacquire before
            # the health pulse, so no old-owner target list survives reflection.
            # 1101 adds 19C4 when current and launch owner identities differ.
            damage_tags = REFLECTED_DAMAGE_ORB_TAGS if orb.body != orb.launch_body else DAMAGE_ORB_TAGS
            for target in _orb_targets(fight, orb):
                vitals = fight.vitals_of(target)
                bubble = None if orb.healing else fight.barriers.for_target(target.owner)
                before = vitals.total + (bubble.health if bubble is not None else 0.0)
                amount = min(orb.energy, (65.0 if orb.healing else 50.0) * dt)
                if orb.healing:
                    given = fight._heal_pools(target.owner, vitals, amount, maximum=orb.energy, tags=HEAL_ORB_TAGS)
                    if given > 0 and target.owner is not orb.shooter:
                        orb.shooter.stats.add(stats.HEALING, given)
                        gain_combat_charge(orb.shooter, given, body=orb.body,
                                           healing_target=target, healing_tags=HEAL_ORB_TAGS)
                else:
                    # Aura1101 ModifyHealth names loadout016D (target-build XML).
                    fight._damage(orb.shooter, target, amount, False, 0x0B9000000000016D, fight.tick,
                                  world_tick=fight.tick, source_body=orb.body, maximum_damage=orb.energy,
                                  hit_tags=damage_tags)
                after = vitals.total + (bubble.health if bubble is not None else 0.0)
                orb.energy = max(0.0, orb.energy - abs(after - before))
                if orb.energy <= combat.EPSILON:
                    orb.energy = 0.0
            orb.flown += dt
            orb.left = max(0.0, orb.left - dt)
            elapsed -= dt
        if orb not in fight.moira_orbs:
            continue
        if orb.left <= combat.EPSILON or orb.energy <= combat.EPSILON:
            fight.moira_orbs.remove(orb)
            if orb.shot is not None:
                fight.projectiles.flown(orb.shot, orb.flown)


def transition(player) -> None:
    """Reconstruct Fade's zero-time entry transition on the Moira graph only.

    F7C/st1's context-dependent 54FEB21C service is not hosted. Its authored
    onEnd starts st3 (duration #215) then st6 (0.2s), which writes cooldown
    #472 and stops the ability. Keep that authored lifecycle, rather than
    inventing an independent cooldown clock. Full native service remains open.
    """
    if player.hero.guid != 0x02E00000000001A2:
        return
    for instance in player.body_script.component.instances.values():
        if instance.graph.index != 0x0F7C:
            continue
        entry = instance.states.get(1)
        if (entry is not None and entry.active
                and entry.start // instance.component.frame_ms == instance.component.frame):
            fight = player.match.combat
            own = fight.vitals.get(player.body)
            if own is not None:
                own.healing_block_until = 0.0  # declared self-cleanse on actual Fade entry
            delay = combat._number(entry.evaluate(entry.node.field("m_ED97A97C"), watch=False), 0.0)
            entry.request_finish(max(0, nodes.seconds_to_ms(delay)))


def _active_timed_cast(component):
    for instance in component.instances.values():
        index = TIMED_CASTS.get(instance.graph.index)
        if not instance.stopped and index is not None:
            ability = instance.states.get(index)
            if ability is not None and ability.flags & nodes.Ability.RUNNING:
                return instance
    return None


def track_clock(fight, owner, *, posed):
    if owner.hero.guid != MOIRA or (not posed and owner in fight.moira_clocks):
        return
    from ow174.game.script.driver import ULT_GAIN_ENABLED

    component = owner.body_script.component
    enabled = component.entity_var(ULT_GAIN_ENABLED, create=False)
    if (enabled is not None and enabled.value() is not None) or _active_timed_cast(component) is not None:
        fight.moira_clocks[owner] = (component, fight.tick, component.frame)


def _passive_due(component, first_pose):
    from ow174.game.script.driver import ULT_GAIN_GRAPH

    limit = first_pose * component.frame_ms
    for instance in component.instances.values():
        if instance.stopped:
            continue
        if instance.graph.index == ULT_GAIN_GRAPH:
            wait = instance.states.get(1)  # 0B8B node 3, passive charge
        elif instance.graph.index == 0x0F76:
            wait = instance.states.get(20)  # F76 node 44, passive fuel recovery
        else:
            continue
        if wait is not None and wait.active and wait.start + wait.ms < limit:
            return True
    return False


def advance_clocks(fight):
    """Keep active casts and the authored passive recovery on the body clock."""
    from ow174.game.projectiles import MAX_LEASE_FRAME
    from ow174.game.script.driver import MAX_GAP

    for owner, (component, start_tick, start_frame) in tuple(fight.moira_clocks.items()):
        script = owner.body_script
        if (fight.match.ended or owner.gone or script is None or script.retired
                or script.component is not component or fight.is_dead(owner)):
            del fight.moira_clocks[owner]
            continue
        elapsed = max(0, fight.tick - start_tick) * round(combat.TICK * 1000)
        # Bound each advance so the VM preserves the missing prefix. Leave
        # the current pose to a real command if one arrives.
        first_pose = min(MAX_LEASE_FRAME - 1, start_frame + elapsed // script.frame_ms, script.frame + MAX_GAP)
        if (first_pose > script.frame + 1
                and (_active_timed_cast(component) is not None or _passive_due(component, first_pose))):
            owner._advance_body_gap(first_pose)


def fading(owner) -> bool:
    """Reconstructed damage immunity during Fade entry/main, not recovery.

    This deliberately does not label generic stack variable IDs as native
    damage flags. Cleanse/control and shared status services remain open.
    """
    hero = getattr(owner, "hero", None)
    script = getattr(owner, "body_script", None)
    if getattr(hero, "guid", None) != 0x02E00000000001A2 or script is None:
        return False
    return any(instance.graph.index == 0x0F7C
               and any(instance.states.get(index) is not None and instance.states[index].active
                       for index in (1, 3))
               for instance in script.component.instances.values())

def clear_fade_press(player) -> None:
    """Discard queued Fade input when a world effect suppresses its start."""
    if player.hero.guid != MOIRA or player.body_script is None:
        return
    for instance in player.body_script.component.instances.values():
        if instance.graph.index == 0x0F7C:
            ability = instance.states.get(9)
            if isinstance(ability, nodes.Ability):
                ability.presses.clear()


def _grasp_projection(origin, forward, right, up, target):
    """Score the historical capsule's projected bounds in the command view."""
    x, y, z = combat._sub(target.feet, origin)
    bounds = []
    # The server has an upright capsule and a symmetric 90-degree view.
    for height, side in ((0, -target.radius), (target.height, target.radius)):
        corner = (x, y + height, z)
        depth = combat._dot(corner, forward)
        if depth <= combat.EPSILON:
            return None
        bounds.append(((combat._dot(corner, right) + side) / (2 * depth),
                       combat._dot(corner, up) / (2 * depth)))
    horizontal, vertical = (max(0, min(a, b), -max(a, b)) for a, b in zip(*bounds))
    return math.hypot(horizontal, vertical) * math.sqrt(2)


@dataclass
class GraspFrame:
    """Share one posed command's contact between TrackTargets and combat."""
    fight: object
    player: object
    command: object
    contacts: dict = field(default_factory=dict)

    def targets(self, search):
        if search not in self.contacts:
            self.contacts[search] = _grasp_contact(self.fight, self.player, self.command, search)
        target, _, _ = self.contacts[search]
        return (expr.Entity(target.entity),) if target is not None else ()


_grasp_frame = ContextVar("moira_grasp_frame", default=None)


@contextmanager
def grasp_command(fight, player, command):
    frame = GraspFrame(fight, player, command)
    token = _grasp_frame.set(frame)
    try:
        yield
    finally:
        _grasp_frame.reset(token)


def grasp_targets(world, search):
    frame = _grasp_frame.get()
    script = frame.player.body_script if frame is not None else None
    return frame.targets(search) if script is not None and script.world is world else ()


def _grasp_contact(fight, player, command, search):
    if (not command.buttons & 2 or search.instance.component.disabled.get(29)
            or fight.match.collision_world is None or fight.is_dead(player) or fight.is_stunned(player)):
        return None, None, None
    # CAF449/CAFC26: a resolved nonpositive cap leaves no selected targets.
    cap = search.evaluate(search.node.field("m_C39691D9"), watch=False)
    if cap is not None and expr.to_int(cap, default=0x7FFFFFFF) <= 0:
        return None, None, None
    yaw, pitch = command.aim()
    origin, direction = fight._eye(player), combat.aim(yaw, pitch)
    right = combat.aim(yaw + 16384, 0)
    up = combat.aim(yaw, pitch - 16384)
    margin = combat._number(search.evaluate(search.node.field("m_16BDE607"), watch=False), 0)
    volume = _track_volume(search, origin, direction, margin=margin)
    reach = volume.depth
    terms = []
    for term in search.node.field("m_84E5DE4E.m_503F0304") or ():
        config = term.get("m_84E5DE4E") or {}
        kind = config.get("$")
        if kind not in ("STU_2FD4C482", "STU_AD7331B0"):
            continue
        weight = combat._number(search.evaluate(term.get("m_weight"), watch=False), 0)
        lower = combat._number(search.evaluate(config.get("m_ABC68B33"), watch=False), 0)
        upper = combat._number(search.evaluate(config.get("m_2AFC083F"), watch=False), 0)
        if upper > lower:
            terms.append((kind, weight, lower, upper))
    collision = fight._collision()
    # The build catalog contains the same 003 target-tag components for
    # actors and projectiles. Runtime additions and removals need a live host.
    catalog = deflection.tags()
    filters = search.node.field("m_filter") or {}
    tag_lists = tuple(entity_filter.collect_tags(search.instance, filters.get(name))
                      for name in entity_filter.TAG_FIELDS) if catalog else ()
    best = None
    view_tick = command.frame - command.view_delay(player.quanta) * 1e6 / player.quanta
    for target in fight._enemies(player):
        shown = fight.seen(target, view_tick)
        if shown is None or not volume.capsule(shown.feet, shown.radius, shown.height)[0]:
            continue
        owner = target.owner
        definition = owner.body if isinstance(owner, bots.Bot) else owner.hero.body
        tags = catalog.get(definition)
        if tags is not None:
            static = entity_filter.TagSet(static=(tag & 0xFFFFFFFF for tag in tags))
            if not entity_filter.matches_tags(static, *tag_lists):
                continue
        if not _track_visible(fight, collision, origin, shown):
            continue
        segment = shown.segment()
        contact_direction = direction
        distance = combat.ray_capsule(origin, direction, *segment, shown.radius)
        if distance is None:
            # The search margin can admit a body outside the center ray.
            # Trace to its unexpanded capsule for cover and barrier contact.
            lower, upper = segment
            nearest = (lower[0], max(lower[1], min(upper[1], origin[1])), lower[2])
            offset = combat._sub(nearest, origin)
            length = math.sqrt(combat._dot(offset, offset))
            contact_direction = tuple(axis / length for axis in offset)
            distance = max(0.0, length - shown.radius)
        score = 0.0
        for kind, weight, lower, upper in terms:
            if kind == "STU_2FD4C482":
                value = _grasp_projection(origin, direction, right, up, shown)
                if value is None:
                    score += 100
                    continue
            else:
                value = math.dist(origin, shown.feet)
            score += weight * min(1, max(0, (value - lower) / (upper - lower)))
        rank = (score, distance)
        if best is None or rank < best[0]:
            best = rank, target, contact_direction

    rank, target, direction = best if best is not None else ((0, reach), None, direction)
    distance = rank[1]
    wall = combat.ray_world(collision, origin, direction, distance)
    pillar = fight.walls.cast(origin, direction, distance)
    if pillar is not None and (wall is None or pillar[0] < wall):
        wall = pillar[0]
    else:
        pillar = None
    limit = min(distance, wall) if wall is not None else distance
    shield = fight._barrier_contact(player, origin, direction, limit, bubbles=True)
    ice = fight._cryo_contact(player, origin, direction, limit)
    if ice is not None and (shield is None or ice < shield[0]) and (wall is None or ice < wall):
        return None, None, None
    if shield is not None and (wall is None or shield[0] < wall):
        return None, shield, None
    if pillar is not None:
        return None, None, pillar
    if target is None or (wall is not None and wall < distance):
        return None, None, None
    return target, None, None


def grasp(fight, player, command) -> None:
    if player.hero.guid != MOIRA or command is None:
        return
    frame = _grasp_frame.get()
    if frame is None or frame.player is not player:
        return
    for search, (target, shield, pillar) in frame.contacts.items():
        if not search.active or search.instance.component.disabled.get(29):
            continue
        instance = search.instance
        damage = instance.state(22)
        definition = combat.hit_def(damage, {"m_modifyHealth": damage.node.field("m_modifyHealth")})
        if definition is None or definition.healing:
            continue
        dt = player.quanta / 1e6
        if shield is not None:
            fight._damage_contact_barrier(shield, definition.amount * dt, shooter=player)
            continue
        if pillar is not None:
            fight.walls.damage(pillar[1], player, definition.amount * dt)
            continue
        damage_target = expr.to_entity(damage.evaluate(damage.node.field("m_FA3ECC43"), watch=False))
        if target is None or damage_target != target.entity:
            continue
        before = fight.vitals_of(target).total
        fight._damage(player, target, definition.amount * dt, False, definition.loadout, command.frame,
                      hit_tags=definition.tags)
        if fight.vitals_of(target).total >= before:
            continue
        _recover_grasp(instance)
        self_heal = instance.state(45)
        healing = combat.hit_def(self_heal, {"m_modifyHealth": self_heal.node.field("m_modifyHealth")})
        own = fight.vitals.get(player.body)
        if own is not None and not own.dead and healing is not None and healing.healing:
            fight._heal_pools(player, own, healing.amount * dt, tags=healing.tags)


def _track_volume(search, origin, direction, *, margin=0.0):
    depth = combat._number(search.evaluate(search.node.field("m_radius"), watch=False), 0)
    aperture = combat._number(search.evaluate(search.node.field("m_A30B383E"), watch=False), 0)
    parallel = search.evaluate(search.node.field("m_E474FFD6"), watch=False)
    half_angle = expr.f32(math.radians(aperture) * 0.5)
    outer_cos = expr.f32(math.cos(half_angle))
    extent = expr.f32(expr.f32(math.tan(half_angle)) * depth) if parallel else 0
    return Volume(origin, direction, (outer_cos, outer_cos), (extent, extent),
                  (0, 0), (1, 1), depth, margin=margin, parallel=bool(parallel))


def _track_visible(fight, collision, origin, target):
    # CA3570's default point and its fallback when hardpoint 01CD is unavailable.
    # The host supplies the historical upright capsule and static world queries.
    x, y, z = target.feet
    for fraction in (0.3, 0.6):
        offset = combat._sub((x, y + target.height * fraction, z), origin)
        distance = math.sqrt(combat._dot(offset, offset))
        if distance <= combat.EPSILON:
            return True
        direction = tuple(axis / distance for axis in offset)
        if (combat.ray_world(collision, origin, direction, distance) is None
                and fight.walls.cast(origin, direction, distance) is None):
            return True
    return False


def _active_beam_health(instance, index):
    state = instance.states.get(index)
    if state is None or not state.active:
        return None
    targets = state.evaluate(state.node.field("m_FA3ECC43"), watch=False)
    if isinstance(targets, expr.Entity):
        targets = (targets,)
    if not isinstance(targets, tuple):
        return None
    recipients = {target.id for target in targets if isinstance(target, expr.Entity)}
    if not recipients:
        return None
    definition = combat.hit_def(state, {"m_modifyHealth": state.node.field("m_modifyHealth")})
    return definition, recipients


def _beam_route(instance, search_index, origin, direction):
    search = instance.states.get(search_index)
    if search is None or not search.active:
        return None
    return search, _track_volume(search, origin, direction)


def coalescence(fight, player, command) -> None:
    """Piercing authored TrackTargets volume with reconstructed frame and visibility.

    The real graph gates charge, consumes it, delays startup and ends the
    cast. Each posed sample publishes the active searches and uses the
    active ModifyHealth states' recipients and rates. The native
    capsule predicate uses the server eye and command aim. Static visibility
    uses CA3570's generic capsule-height points. Native pose, hardpoint and
    filter orchestration remain open.
    """
    if player.hero.guid != 0x02E00000000001A2 or command is None:
        return
    for instance in player.body_script.component.instances.values():
        if instance.graph.index != 0x1007:
            continue
        own_healing = _active_beam_health(instance, 12)
        origin, direction = fight._eye(player), combat.aim(*command.aim())
        ally = _beam_route(instance, 17, origin, direction)
        enemy = _beam_route(instance, 19, origin, direction)
        if ally is None and enemy is None and own_healing is None:
            continue
        dt = player.quanta / 1e6
        if ally is not None or enemy is not None:
            collision = fight._collision()
            source = combat._source_entity(player)
            view_tick = command.frame - command.view_delay(player.quanta) * 1e6 / player.quanta
            ally_targets, enemy_targets = [], []
            contacts = []
            for target in fight._targets():
                friendly = not fight.match.game_map.free_for_all and target.team == player.team
                route, selected = (ally, ally_targets) if friendly else (enemy, enemy_targets)
                if route is None or target.entity == source or fight.vitals_of(target).dead:
                    continue
                shown = fight.seen(target, view_tick)
                if shown is None:
                    continue
                _, volume = route
                if not volume.capsule(shown.feet, shown.radius, shown.height)[0]:
                    continue
                if not _track_visible(fight, collision, origin, shown):
                    continue
                selected.append(expr.Entity(target.entity))
                contacts.append((target, friendly))
            for route, selected in ((ally, ally_targets), (enemy, enemy_targets)):
                if route is not None:
                    search, _ = route
                    search.publish_targets(tuple(selected))
            ally_health = _active_beam_health(instance, 18)
            enemy_health = _active_beam_health(instance, 20)
            for target, friendly in contacts:
                health = ally_health if friendly else enemy_health
                if health is None:
                    continue
                definition, recipients = health
                if target.entity in recipients:
                    fight._apply_hit(player, target, definition, definition.amount * dt, False, command.frame)
        own = fight.vitals.get(player.body)
        if own is not None and not own.dead and own_healing is not None:
            definition, recipients = own_healing
            if definition.healing and own.entity in recipients:
                fight._heal_pools(player, own, definition.amount * dt, tags=definition.tags)
