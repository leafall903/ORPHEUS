"""Resource-driven static placement preview for owned Symmetra server graphs.

This is an explicit server reconstruction: static line casts, existing material
ground flags, no clipping volumes, and unobstructed static sight to the candidate.
Native swept/filter/receiver/dynamic geometry and C9DA00 footprint validation,
confirmation ownership and Pet deployment remain separate acceptance work.
"""

import math

from ow174.game.script import expr, placement
from ow174.game.script import placement_geometry as geometry

HERO = 0x02E0000000000016
NODES = {(0x156B, 57), (0x1571, 69)}


def static_transform(world, cfg, instance):
    """World-space identity for the static/null-parent Photon Barrier position.

    This server policy supplies the two authored position expressions only.
    Entity-parent, attachment and bone transforms remain unsupported.
    """
    if (world.hero != HERO or world.match is None or instance.graph.index != 0x156B
            or cfg.get('$') != 'STU_C5148EFE' or cfg.get('m_bone') is not None):
        return NotImplemented
    pet = instance.graph.node(17).field('m_position')['m_configVars'][0]
    if cfg not in (instance.graph.node(89).field('m_value'), pet):
        return NotImplemented
    parent = instance.evaluate(cfg.get('m_entity'))
    # Node39 explicitly initializes the parent dynamic variable to integer0.
    # Admit that authored null alongside the typed placement null; never turn
    # a missing/unknown value into a world-space identity transform.
    if not (parent == expr.Entity(0) or (type(parent) is int and parent == 0)):
        return NotImplemented
    point = instance.evaluate(cfg.get('m_BD631144'))
    if type(point) is not expr.Vec3 or not all(math.isfinite(value) for value in point):
        return NotImplemented
    return _vec(point)


def _vec(value):
    return expr.Vec3(*(expr.f32(v) for v in value))


def _distance(a, b):
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b, strict=True)))


def _toward(a, b):
    length = _distance(a, b)
    return _vec((y - x) / length for x, y in zip(a, b, strict=True)) if length else None


def _offset(point, direction, distance):
    return _vec(expr.f32(p + expr.f32(d * distance))
                for p, d in zip(point, direction, strict=True))


def _cast(world, start, end):
    length = _distance(start, end)
    if length <= 1e-6:
        return None
    hit = world.cast_ray(tuple(start), tuple(_toward(start, end)), length)
    return hit if hit is not None and hit.distance <= length else None


def _sample_hit(world, query):
    hit = _cast(world, query.start, query.end)
    if hit is None:
        return None, 0
    length = _distance(query.start, query.end)
    contact = geometry.Contact(_vec(hit.point), _vec(hit.normal), min(1, max(0, hit.distance / length)))
    result = geometry.SampleHit(contact, expr.Entity(0), 0xFFFF)
    return result, hit.surface


def solve(world, origin, direction, axis, *, minimum, maximum, offset, spacing, angle, scalar, step):
    """Use native-verified math with the declared static server query services."""
    if maximum <= 0 or minimum > maximum:
        return placement.StaticResult(False)
    angles = geometry.projection_angles(direction, expr.Vec3(0, 1, 0), step)
    for attempt in range(angles.additional_attempts + 1):
        end = _offset(origin, direction, maximum)
        hit = _cast(world, origin, end)
        pair = geometry.Candidates(end, end)
        if hit is not None:
            contact = geometry.Contact(_vec(hit.point), _vec(hit.normal),
                                       min(1, max(0, hit.distance / _distance(origin, end))))

            def clearance(start, target, query_scalar):
                blocker = _cast(world, start, target)
                return blocker is not None and blocker.distance < _distance(start, target) - .015

            pair = geometry.refine_contact(origin, end, direction, contact, offset, scalar, clearance)
        if pair is not None:
            pair = geometry.range_candidates(origin, pair, minimum)
        if pair is not None:
            primary_length = _distance(origin, pair.primary)
            fallback_length = _distance(origin, pair.fallback)
            if primary_length > 1e-6 and fallback_length > 1e-6:
                plan = geometry.sampling_plan(primary_length, fallback_length, offset, spacing)
                # Reconstructed selection camera: the authored sampling angle,
                # unit viewport and near1. Actual owner FOV/viewport is separate.
                camera = geometry.CameraProjection(
                    geometry.view_matrix(origin, _toward(origin, pair.primary), expr.Vec3(0, 1, 0), 1),
                    geometry.perspective_matrix(0, angle, (0, 0, 1, 1), 1, maximum + 2).matrix,
                    True, (0, 0, 1, 1))

                def probe(fallback, index, pair=pair, plan=plan, direction=direction,
                          primary_length=primary_length, fallback_length=fallback_length):
                    if fallback:
                        point, length, interval = pair.fallback, fallback_length, plan.fallback_step
                    else:
                        point, length, interval = pair.primary, primary_length, plan.primary_step
                    query = geometry.sample_query(
                        index, point, _toward(point, origin), direction, length=length, step=interval,
                        drop_limit=maximum, angle=angle, sine_scale=expr.f32(math.sin(angle)),
                        offset=offset, sweep=fallback, query_scalar=scalar)

                    def final(result, corrected):
                        if _distance(origin, result.contact.point) > maximum + 1e-4:
                            return False
                        blocker = _cast(world, origin, corrected)
                        return blocker is None or blocker.distance >= _distance(origin, corrected) - .015

                    # Static ground/line policy, not an implementation of the
                    # native swept shape, clipping volume or receiver filter.
                    sample, flags = _sample_hit(world, query)
                    return geometry.validate_sample(
                        query, origin, exclusion=minimum, limit_height=False, maximum_y=0,
                        cast=lambda q: sample, clipped=lambda result: False,
                        surface=lambda result: geometry.static_surface_allowed(
                            result.contact.normal, flags, False),
                        final=final)

                candidates = geometry.collect_candidates(plan, stride=1, start=2, preferred=-1,
                                                          shorten=True, probe=probe, accept=lambda *_: True)
                chosen = geometry.select_candidate(candidates, pair.primary, camera.selection_point)
                if chosen is not None and chosen is not NotImplemented:
                    return placement.StaticResult(True, chosen.hit.contact.point)
        if attempt < angles.additional_attempts:
            direction = angles.advance(direction, axis, attempt)
    return placement.StaticResult(False)


def preview(world, state):
    if world.hero != HERO or world.match is None or (state.instance.graph.index, state.node.pos) not in NODES:
        return NotImplemented
    owner = next((p for p in world.match.players if p.body == world.body), None)
    if owner is None or owner.gone or not owner.spawned or world.match.combat.is_dead(owner):
        return None
    if world.match.collision_world is None:
        return NotImplemented
    if state.evaluate(state.node.field('m_DC05B2EE'), watch=False) != expr.Entity(world.body):
        return NotImplemented

    def value(field, default):
        cfg = state.node.field(field)
        result = state.evaluate(cfg, watch=False) if cfg is not None else default
        if type(result) not in (int, float) or not math.isfinite(result):
            raise ValueError('placement preview requires resolved finite resource parameters')
        return expr.f32(result)

    from ow174.game.combat import aim

    yaw = owner.mover.yaw * 2 * math.pi / 65536
    return solve(world.match.collision_world, _vec(world.match.combat._eye(owner)),
                 _vec(aim(owner.mover.yaw, owner.mover.pitch)),
                 _vec((math.cos(yaw), 0, -math.sin(yaw))),
                 minimum=value('m_00A23E4B', 0), maximum=value('m_6386E7B4', 35),
                 offset=value('m_B3094B0E', .523), spacing=value('m_B42B2BEB', .5),
                 angle=value('m_2ADABA5F', .34), scalar=value('m_3656A811', 0),
                 step=value('m_1B05EA98', 1))
