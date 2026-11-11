"""104319 Sleep Dart timing used by the server control bridge."""

from dataclasses import dataclass

EFFECT = 0x04A000000000026F
PROJECTILE = 0x04000000000015E8  # 104319 target tags are empty
DURATION = 5.0  # 0BDE/node51, override 215
DAMAGE_GRACE = 0.5  # 224E/node166
WAKE = 0.5  # 224E/node145


@dataclass
class Sleep:
    start: int
    end: int
    waking: bool = False

    def active_at(self, tick: int) -> bool:
        return self.start <= tick < self.end
