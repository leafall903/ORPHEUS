"""Reconstructed combat protection for Reaper's authored 0020 Wraith scope."""

HERO = 0x02E0000000000002
GRAPH = 0x0020


def instance_for(actor):
    if getattr(getattr(actor, "hero", None), "guid", None) != HERO:
        return None
    script = getattr(actor, "body_script", None)
    if script is None or script.retired or script.component.owner_dead:
        return None
    return next((instance for instance in script.component.instances.values()
                 if instance.graph.index == GRAPH and not instance.stopped), None)


def finish_entry(actor):
    """Complete the zero-duration entry callback, letting the graph start its wait."""
    instance = instance_for(actor)
    if instance is None:
        return
    entry = instance.states.get(1)
    if (entry is not None and entry.active and entry.node.cls == "STU_54FEB21C"
            and entry.evaluate(entry.node.field("m_ED97A97C"), watch=False) == 0):
        entry.request_finish()
        instance.update()


def protects(actor, tick):
    instance = instance_for(actor)
    if instance is None:
        return False
    wait = instance.states.get(3)
    return (wait is not None and wait.active and wait.node.pos == 6
            and tick * instance.component.frame_ms < wait.start + wait.ms)
