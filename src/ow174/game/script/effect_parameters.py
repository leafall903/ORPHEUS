"""104319 9D2E70: the parameter writes of 582E0D5F's auxiliary effect.

Offsets identify fields in the native effect object, not a wire format. The
consumer, target selection, creation/retirement and vt+88 scheduling are separate
services. This primitive does not mark that state class as executable in the VM.
"""

from dataclasses import dataclass, field

from ow174.game.script import expr, play, runtime

RADIANS = expr.f32(0.01745329238474369)
ZERO = expr.Vec3(0.0, 0.0, 0.0)
FAILED = object()


@dataclass
class Parameters:
    """Only the authored parameters overwritten by 9D2E70, using native offsets.

    Native vector padding is not a fourth authored component. Values retain xyz
    only. Keeping a block between calls models replacement, including its entity
    list; no effect allocation or renderer object is represented by this class.
    """

    values: dict = field(default_factory=dict)


def _value(state, field):
    return play.evaluate_scalar(state.instance, state.node.field(field), initial=FAILED, watcher=state)


def _number(state, field, default):
    value = _value(state, field)
    return expr.f32(default if value is FAILED else expr.to_float(value, default))


def _first(value):
    while isinstance(value, tuple) and value:
        value = value[0]
    return value


def _vector(state, field):
    value = _value(state, field)
    # 1B721B0 checks a direct Entity before F65E70's first-array vector cast.
    # An array containing an Entity does not take the position-query branch.
    if isinstance(value, expr.Entity):
        value = runtime.InstanceContext(state.instance, state).position(value.id)
    else:
        value = _first(value)
    if not isinstance(value, expr.Vec3):
        return ZERO
    return expr.Vec3(*(expr.f32(part) for part in value))


def initialize(state, block: Parameters, *, context_enabled: bool) -> None:
    """9D253B..9D2608: initial authored writes, then the complete refresh.

    Native allocation and binding of State+218 precede this sequence. The caller
    owns those operations; this function neither allocates nor begins a State.
    """
    if type(block) is not Parameters or type(context_enabled) is not bool:
        raise ValueError("expected effect parameters and explicit boolean context capability")
    with state.instance.component.operation():
        for offset, name in ((0x208, "m_0C4BE468"), (0x209, "m_F4669F53")):
            value = _value(state, name)
            block.values[offset] = expr.truthy(value) if value is not FAILED else False
        block.values[0x20C] = _number(state, "m_1A76C130", 0.0)
        value = _value(state, "m_494A6B1F")
        block.values[0x22C] = expr.truthy(value) if value is not FAILED else False
        value = _number(state, "m_6A79BD98", 2.0)
        for offset in (0xF4, 0xF8, 0xFC):
            block.values[offset] = value
        refresh(state, block, context_enabled=context_enabled)


def refresh(state, block: Parameters | None, *, context_enabled: bool) -> None:
    """Write the existing block; no block means no evaluation (native +218 guard).

    context_enabled is the explicit result of native context vt+C8. It is not
    derived from State.active, owner identity, possession or a local mode flag.
    Like native MAXSS(0, value), +2E4 keeps a NaN or signed zero in operand two.
    """
    if block is None:
        return
    if type(block) is not Parameters or type(context_enabled) is not bool:
        raise ValueError("expected effect parameters and explicit boolean context capability")
    with state.instance.component.operation():
        values = block.values
        values[0xC4] = _number(state, "m_F5C94645", 0.5)
        values[0xC8] = _number(state, "m_63FFE8DF", 0.5)
        values[0xF4] = _number(state, "m_6A79BD98", 2.0)
        enabled = _value(state, "m_6B30E42C")
        values[0x2E8] = (expr.truthy(enabled) if enabled is not FAILED else False) and context_enabled
        value = _number(state, "m_B53E150A", 3.0)
        values[0x2E4] = expr.f32(0.0 if value < 0.0 else value)
        asset = _first(_value(state, "m_51EFD5D6"))
        values[0x1A0] = asset.guid & 0xFFFFFFFF if isinstance(asset, expr.Asset) else 0
        for offset, name in (
            (0x1B0, "m_01D7DF02"), (0x170, "m_02400C28"),
            (0x180, "m_767D90FD"), (0x190, "m_4AB67781"),
        ):
            values[offset] = _vector(state, name)
        for offset, name in ((0x1C0, "m_D0103F1A"), (0x1C4, "m_595AE325")):
            values[offset] = expr.f32(_number(state, name, 0.0) * RADIANS)
        values[0x1C8] = _number(state, "m_8C563869", 0.0)
        for offset, name in (
            (0x1D0, "m_7642C777"), (0x1E0, "m_AB5F32F7"), (0x1F0, "m_79370307"),
        ):
            values[offset] = _vector(state, name)
        for offset, name in ((0x200, "m_B31CA390"), (0x204, "m_84DD5440")):
            values[offset] = expr.f32(_number(state, name, 0.0) * RADIANS)
        # Reset on every refresh; native order and duplicates are retained.
        entities = []
        values[0x218] = entities
        for item in play._iter_values(state.instance, state.node.field("m_62F44973"), watcher=state):
            entity = expr.to_entity(item)
            if expr.valid_entity(entity):
                entities.append(entity)
