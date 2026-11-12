"""104319 9D1D50/6B8ED0/6B8E20: auxiliary effect list ownership.

This models list entries, not engine shared-pointer counters or a renderable
native object. Callers supply the resolved context/target/observer. Routing,
consumer advancement, observer dispatch and final reclamation remain separate.
"""

from dataclasses import dataclass, field

from ow174.game.script.effect_parameters import Parameters


@dataclass(eq=False)
class Effect:
    context: object
    target: int
    observer: object
    parameters: Parameters = field(default_factory=Parameters)


@dataclass(eq=False)
class Entry:
    effect: Effect
    key: int
    live: bool = True


class EffectList:
    """Keep unsigned serial order and retain retired entries until consumption.

    The native 582E0D5F allocator supplies high-key word zero. Equal keys after
    a full serial cycle require the native sort's tie order, which is not
    established here; reject that case instead of inventing an order.
    """

    def __init__(self, counter: int = 0):
        if type(counter) is not int or not 0 <= counter <= 0xFFFFFFFF:
            raise ValueError("effect counter must be uint32")
        self.counter = counter
        self.entries: list[Entry] = []

    def allocate(self, *, context, target: int, observer) -> Effect:
        if type(target) is not int or not 0 <= target <= 0xFFFFFFFF:
            raise ValueError("effect target must be uint32")
        key = (self.counter + 1) & 0xFFFFFFFF
        if any(entry.key == key for entry in self.entries):
            raise ValueError("native effect serial collision needs an established tie order")
        effect = Effect(context, target, observer)
        self.counter = key
        self.entries.append(Entry(effect, key))
        self.entries.sort(key=lambda entry: entry.key)
        return effect

    def retire(self, effect: Effect) -> bool:
        """6B8E20 compares object identity and clears the first matching live bit.

        Finding an already-retired entry still succeeds. The native helper does
        not erase it, decrement references, dispatch its observer or re-sort it.
        """
        for entry in self.entries:
            if entry.effect is effect:
                entry.live = False
                return True
        return False
