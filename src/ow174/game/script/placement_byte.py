"""Original priority2 placement-byte storage for the authoritative body host.

This qualifies the current server producer model, not client/base/strict roles.
Geometry, confirmation routing, owner/source and actor behavior stay in their hosts.
"""


def eligible(state):
    from ow174.game.script.driver import BodyWorld

    component = state.instance.component
    return (state.node.cls in ('STU_4C96470A', 'STU_43DB06B1')
            and not component.strict and type(component.world) is BodyWorld
            and component.entity == component.world.body)


def begin(state):
    if eligible(state):
        state.placement_byte = 0


def typed_input(state, input_path):
    if not eligible(state) or input_path != 'm_6647ED22':
        return
    typed = state.node.field(input_path)
    if isinstance(typed, dict) and typed.get('$') == 'STU_A1AAB8C4':
        # The native class extension itself has no active/ending/death gate.
        # Dispatch eligibility belongs to the existing VM, independently.
        state.placement_byte = 1


def payload(state):
    if not eligible(state):
        return None
    # Preserve explicit storage, including corruption, for strict codec validation.
    # Missing storage raises AttributeError here; neither case acquires a default.
    return {'m_B4802CE3': state.placement_byte}
