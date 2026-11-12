"""Original delivered Sombra queries on the combined server's actual body VMs.

Application, targeting and lock ownership remain in the single SombraControls host.
These original read-only helpers do not create state, pools or a second controller.
"""

from ow174.game.commands import SECONDARY
from ow174.game.script import nodes

SOMBRA = 0x02E000000000012E
ASHE = 0x02E0000000000200
ASHE_LOCK_BUTTONS = 8 | 16 | 32
REINHARDT_LOCK_BUTTONS = SECONDARY | 16 | 32
HACK_REJECT = 14679
EMP, EMP_CHILD, HACK = 0x0B31, 0x0B32, 0x0B1F


def instance_for(player, index):
    script = getattr(player, "body_script", None)
    if script is None or getattr(script, "retired", False) or script.component.owner_dead:
        return None
    return next((i for i in script.component.instances.values() if i.graph.index == index), None)


def locked(player, frame=None):
    instance = instance_for(player, HACK)
    wait = instance.states.get(58) if instance is not None else None
    if not isinstance(wait, nodes.Wait) or not wait.active:
        return False
    frame_ms = instance.component.frame_ms
    return frame is None or (wait.start // frame_ms <= frame and frame * frame_ms < wait.start + wait.ms)


