"""104319 CF4340/D0DA20 classification masks and candidate conditions."""

from contextlib import nullcontext
from dataclasses import dataclass

from ow174.game.script import expr, play

CLASS = "STU_AC829876"
WILDCARD = 0x3FFFFFFF
MASK_FIELDS = ("m_84065FD1", "m_1E14CCA8", "m_4EA686DB")


@dataclass(frozen=True)
class Record:
    """Explicit component25 mask, component97 availability and extension decision."""

    bits: int
    component97: bool
    extension_rejects: bool

    def __post_init__(self):
        if type(self.bits) is not int or not 0 <= self.bits <= 0xFFFFFFFFFFFFFFFF:
            raise ValueError("entity filter needs a uint64 classification mask")
        if type(self.component97) is not bool or type(self.extension_rejects) is not bool:
            raise ValueError("entity filter needs explicit component and extension decisions")


def evaluate(evaluation, cfg):
    if cfg.get("$") != CLASS:
        return NotImplemented
    filt = cfg.get("m_filter")
    masks = ()
    if filt is not None:
        if (type(filt) is not dict or filt.get("$") != "STUConfigVarFilter"
                or any(value not in (None, 0, [])
                       for key, value in filt.items() if key not in ("$", "m_condition", *MASK_FIELDS))):
            return NotImplemented
        masks = tuple(filt.get(field, 0) for field in MASK_FIELDS)
        if any(type(mask) is not int or not 0 <= mask <= 0xFFFFFFFFFFFFFFFF for mask in masks):
            return NotImplemented

    instance, watcher = evaluation.instance, evaluation.watcher
    component = instance.component
    value, success = play.evaluate_scalar_result(instance, cfg.get("m_entity"), watcher=watcher)
    entity = expr.to_entity(value) if success else expr.INVALID_ENTITY
    if filt is None:
        return True
    record = component.world.entity_filter_record(entity)
    component.ensure_valid()
    if (instance.stopped or instance.teardown
            or component.instances.get(instance.id) is not instance):
        component.unsupported(instance, "entity filter owner retired during lookup", CLASS)
    if record is NotImplemented:
        return NotImplemented
    if record is None:
        return False
    if type(record) is not Record:
        component.unsupported(instance, "entity filter record", CLASS)
    include, required, excluded = masks
    if include or required or excluded:
        if include != WILDCARD and not record.component97:
            return False
        bits = record.bits
        if ((include != WILDCARD and not include & bits)
                or required & bits != required or excluded & bits):
            return False
    condition = filt.get("m_condition")
    if condition is not None:
        # 1B8D970 supplies the candidate to 1B8D810 while 90BFC0 evaluates
        # the condition with a true default for failed conversion.
        scope = component.parameter_scope({8: expr.Entity(entity)}) if expr.valid_entity(entity) else nullcontext()
        with scope:
            value, success = play.evaluate_scalar_result(instance, condition, watcher=watcher)
        if success and not expr.truthy(value, default=True):
            return False
    return not record.extension_rejects
