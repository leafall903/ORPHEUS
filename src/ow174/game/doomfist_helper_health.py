"""Body-damage backend requests for staged original0D07/14 qualification.

No caller router installs this service. A settled backend receipt is not an
original health-state completion: paired-target semantics and completion order
must be qualified before adopting the full helper or following its end plugs.
"""

import math
from dataclasses import dataclass, field

from ow174.game import doomfist, doomfist_helper
from ow174.game.script import expr


@dataclass(frozen=True)
class Request:
    state: object
    activation: int
    recipient: object
    component: object
    source: object
    source_body: int
    source_script: object
    source_frame: int
    reference_entity: int
    amount: float
    loadout: int
    tags: tuple
    paired: object


@dataclass(frozen=True)
class Receipt:
    request: Request
    accepted: bool | None
    health_before: float
    health_after: float
    phase: str
    # This module cannot establish original server completion from client code.
    original_completion_qualified: bool = field(default=False, init=False)


def resolve(state, source, *, source_frame):
    """Freeze actual self-target/amount/tags and an explicit caller clock.

    This qualifies the existing body-damage backend only. Opaque paired data
    is retained verbatim; it is not replaced with a recipient entity or zero.
    The source dispatcher must supply its frame, never the recipient's frame.
    """
    instance = state.instance
    component = instance.component
    world = component.world
    if (instance.graph.index, state.node.pos) != (0x0D07, 14):
        return NotImplemented
    recipient = doomfist_helper._recipient(world, instance)
    script = getattr(source, 'body_script', None)
    if (recipient is None or not state.active or script is None
            or source not in world.match.players or source.hero.guid != doomfist.HERO
            or not source.has_body or source.gone or script.retired
            or script.component.world.match is not world.match
            or type(source_frame) is not int or not 0 <= source_frame <= script.frame):
        return None
    config = state.node.field('m_modifyHealth') or {}
    if (state.node.field('m_DD792FF0') != 3 or config.get('m_93CDFD2F') != 1
            or state.evaluate(state.node.field('m_FA3ECC43'), watch=False) != expr.Entity(component.entity)
            or state.evaluate(config.get('m_instigator'), watch=False) != expr.Entity(source.body)
            or state.evaluate(config.get('m_724CBA57'), watch=False) != expr.Entity(component.entity)
            or instance.source_entity() != source.body):
        return None
    from ow174.game.combat import hit_def

    hit = hit_def(state, {'m_modifyHealth': config})
    if (hit is None or not math.isfinite(hit.amount) or hit.amount <= 0 or hit.loadout != doomfist.PUNCH):
        return None
    return Request(state, state.counter, recipient, component, source, source.body, script,
                   source_frame, component.entity, hit.amount, hit.loadout, hit.tags,
                   state.evaluate(state.node.field('m_B12BFE8B'), watch=False))


def consume_body_damage(request):
    """Settle one real backend attempt without claiming the original State ended.

    This is deliberately separate from World.modify_health: an incomplete
    helper cannot gain live damage authority simply by allocating graph0D07.
    """
    if type(request) is not Request:
        raise TypeError('qualified self-damage request required')
    state, component, source = request.state, request.component, request.source
    instance = state.instance
    match = component.world.match
    config = state.node.field('m_modifyHealth') or {}
    if ((instance.graph.index, state.node.pos) != (0x0D07, 14)
            or not math.isfinite(request.amount) or request.amount <= 0 or request.loadout != doomfist.PUNCH
            or type(request.source_frame) is not int or request.source_frame < 0
            or instance.component is not component or not state.active or state.counter != request.activation
            or instance.stopped or instance.teardown or component.owner_dead or component.fault is not None
            or component.instances.get(instance.id) is not instance
            or source not in match.players or source.gone or not source.has_body
            or source.body != request.source_body or source.body_script is not request.source_script
            or instance.source_entity() != request.source_body
            or state.evaluate(config.get('m_instigator'), watch=False) != expr.Entity(request.source_body)
            or doomfist_helper._recipient(component.world, instance) is not request.recipient):
        return None
    previous = getattr(state, 'doomfist_body_damage_receipt', None)
    if previous is not None and previous.request.activation == request.activation:
        return previous  # The same active state cannot spend a second request, even through a new wrapper.
    target = next((t for t in match.combat._enemies(source)
                   if t.owner is request.recipient and t.entity == component.entity), None)
    if target is None or match.combat.vitals_of(target).dead:
        return None
    fight = match.combat
    before = fight.vitals_of(target).total
    # Reserve the activation before any externally visible side effect. A
    # reentrant call or an exception after health mutation cannot spend it twice.
    state.doomfist_body_damage_receipt = Receipt(request, None, before, before, 'in-flight')
    state.hit_entities.add(component.entity)
    try:
        accepted = fight._damage(source, target, request.amount, False, request.loadout, request.source_frame,
                                 hit_tags=request.tags, source_body=request.source_body)
    except Exception:
        state.doomfist_body_damage_receipt = Receipt(
            request, None, before, fight.vitals_of(target).total, 'failed')
        raise
    receipt = Receipt(request, bool(accepted), before, fight.vitals_of(target).total, 'settled')
    state.doomfist_body_damage_receipt = receipt
    if accepted:
        fight._doomfist_shields(source, request.loadout)
    return receipt
