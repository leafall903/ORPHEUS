"""Narrow reconstruction of Roadhog's authored 0240/node18 timed self heal.

The graph supplies amount 300, interval 1 and a self target. Interpreting the
interval as the time over which that amount is distributed is reconstructed;
the original server implementation and client acceptance remain open. Other
ModifyHealth states retain their existing behavior until their semantics are known.
"""

from ow174.game.script import expr
from ow174.game.script.runtime import State


class TimedSelfHeal(State):
    def __init__(self, *args):
        super().__init__(*args)
        self.heal_last = 0
        self.heal_elapsed = 0
        self.heal_duration = 0
        self.heal_amount = 0.0

    def on_begin(self) -> None:
        self.heal_last = self.now
        self.heal_elapsed = 0
        self.heal_duration = 0
        self.heal_amount = 0.0
        if self.instance.graph.index != 0x0240 or self.node.pos != 18:
            return
        fields = self.node.fields
        modify = fields.get("m_modifyHealth") or {}
        target = fields.get("m_FA3ECC43") or {}
        if target.get("$") != "STU_B5A0CAF0" or modify.get("m_93CDFD2F") != 0:
            return
        duration = expr.to_float(self.evaluate(fields.get("m_95F1FB0B"), watch=False))
        amount = expr.to_float(self.evaluate(modify.get("m_amount"), watch=False))
        if duration <= 0 or amount <= 0:
            return
        self.heal_duration = max(1, round(duration * 1000))
        self.heal_amount = amount
        self.ticks()

    def _apply(self) -> None:
        if not self.heal_duration:
            return
        elapsed = min(self.heal_duration - self.heal_elapsed, max(0, self.now - self.heal_last))
        self.heal_last = self.now
        self.heal_elapsed += elapsed
        if elapsed and not self.instance.component.owner_dead:
            self.instance.component.world.heal_self(
                self.instance.component.entity, self.heal_amount * elapsed / self.heal_duration,
            )

    def tick(self) -> None:
        self._apply()
        super().tick()

    def on_ending(self) -> None:
        self._apply()
