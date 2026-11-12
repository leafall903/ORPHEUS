"""030E/16FB/16FC primary-hit tags under an explicit combat selection host.

Actual16FC start/wait/tag states run on the victim. Per-owner priority and
nearest-target fallback are reconstructed; native target-tag services are not.
"""

from dataclasses import dataclass

from ow174.game.script import expr, graph

HERO = 0x02E0000000000006
EFFECT = 0x04A000000000030E


@dataclass
class Mark:
    owner: object
    target: object
    entity: int
    script: object
    instance: int
    born: float
    expires: float
    tag_state: int


def active(mark):
    instance = mark.script.component.instances.get(mark.instance)
    if instance is None:
        return False
    gate = instance.states.get(0)
    tag = instance.states.get(mark.tag_state)
    return tag is not None and tag.active and not (gate is not None and gate.current)


def remove(fight, key):
    mark = fight.torbjorn_marks.pop(key)
    instance = mark.script.component.instances.get(mark.instance)
    if instance is not None:
        mark.script.component.destroy(instance)


def apply(fight, owner, target):
    if (owner not in fight.match.players or owner.gone or owner.hero.guid != HERO
            or fight.vitals_of(target).dead):
        return
    cfg = (graph._data().get("deployables") or {}).get("primary_mark")
    if not cfg or graph.graph(cfg["graph"]) is None:
        expr.warn_once("torbjorn-primary-mark-data", "Torbjorn primary marking needs the prepared030E bundle")
        return
    script = getattr(target.owner, "body_script", None)
    if script is None or script.component.owner_dead or script.component.strict:
        return
    key = owner.entity, target.entity
    if key in fight.torbjorn_marks:
        remove(fight, key)
    instance = script.component.create(graph.graph(cfg["graph"]))
    instance.start_entries((), owner.body)
    script.component.run_now()
    mark = Mark(owner, target.owner, target.entity, script, instance.id, fight.now,
                fight.now + cfg["lifetime_seconds"], cfg["tag_state"])
    fight.torbjorn_marks[key] = mark
    if not active(mark):
        remove(fight, key)


def update(fight):
    live = {target.entity: target for target in fight._targets()}
    for key, mark in list(fight.torbjorn_marks.items()):
        target = live.get(mark.entity)
        if (mark.owner not in fight.match.players or mark.owner.gone or mark.owner.client is None
                or mark.owner.hero.guid != HERO
                or target is None or getattr(target.owner, "body_script", None) is not mark.script
                or fight.vitals_of(target).dead or fight.now >= mark.expires or not active(mark)):
            remove(fight, key)


def preferred(fight, owner, candidates):
    eligible = {row[1] for row in candidates}
    marks = [mark for mark in fight.torbjorn_marks.values()
             if mark.owner is owner and mark.entity in eligible and fight.now < mark.expires and active(mark)]
    return max(marks, key=lambda mark: (mark.born, mark.instance)).entity if marks else None
