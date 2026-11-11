"""Host the 0266/027B Biotic Rifle wound and its 0B95 damage window."""

from dataclasses import dataclass

from ow174.game import phase_defenses

LOADOUT = 0x0B900000000000E3
HIT_EFFECTS = frozenset((0x04A0000000000266, 0x04A000000000027B))
DURATION_MS = 600


@dataclass
class Wound:
    source: object
    source_body: int
    target: object
    amount: float
    last_ms: int
    ends_ms: int


class Wounds:
    def __init__(self, combat):
        self.combat = combat
        self.active = []

    def apply(self, shooter, target, amount, tick):
        from ow174.game.combat import _source_entity

        if phase_defenses.phase(target.owner)[0] or self.combat.vitals_of(target).dead:
            return
        source = getattr(shooter, "combat_owner", None) or shooter
        start_ms = tick * 16
        self.active.append(Wound(source, _source_entity(source), target, amount,
                                 start_ms, start_ms + DURATION_MS))

    def retire(self, body):
        self.active[:] = [wound for wound in self.active
                          if body not in (wound.source_body, wound.target.entity)]

    def update(self, tick):
        now_ms = tick * 16
        for wound in tuple(self.active):
            hp = self.combat.vitals.get(wound.target.entity)
            if hp is None or hp.dead:
                self.active.remove(wound)
                continue
            until = min(now_ms, wound.ends_ms)
            if until > wound.last_ms:
                # Integrate the authored window on the host clock, including its partial final frame.
                amount = wound.amount * (until - wound.last_ms) / DURATION_MS
                wound.last_ms = until
                self.combat._damage(wound.source, wound.target, amount, False, LOADOUT, tick,
                                    world_tick=tick, armour_reduction=False)
            if until == wound.ends_ms or hp.dead:
                self.active.remove(wound)
