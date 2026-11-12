"""Native build104319 component requirements, not runtime actor presence.

18378C0/18380A0 initialize the 143 direct dependency records. 1826BD0 walks
these byte indices using the destination mask itself as its visited set.
Record flags do not participate in that traversal. No image bytes are bundled.
"""

from .definition_components import runtime_index

# Direct requirements by runtime component index, reconstructed from original
# initialization and matched byte-for-byte against the captured static table.
_REQUIREMENTS = (
    (),  # 0
    (),  # 1
    (),  # 2
    (1,),  # 3
    (1, 3),  # 4
    (1,),  # 5
    (1,),  # 6
    (1,),  # 7
    (1,),  # 8
    (1, 3),  # 9
    (),  # 10
    (1,),  # 11
    (1,),  # 12
    (1,),  # 13
    (1,),  # 14
    (),  # 15
    (),  # 16
    (1, 3),  # 17
    (1, 3),  # 18
    (),  # 19
    (),  # 20
    (),  # 21
    (26,),  # 22
    (),  # 23
    (),  # 24
    (97,),  # 25
    (25,),  # 26
    (1, 4, 3, 24),  # 27
    (1, 24),  # 28
    (44, 30, 84, 85, 137),  # 29
    (),  # 30
    (1, 28, 35, 127),  # 31
    (1, 28, 115),  # 32
    (28,),  # 33
    (1, 28),  # 34
    (),  # 35
    (),  # 36
    (1,),  # 37
    (1,),  # 38
    (1, 4, 28, 3),  # 39
    (1, 4, 3),  # 40
    (1, 4, 3),  # 41
    (1, 4),  # 42
    (3, 119),  # 43
    (25, 45),  # 44
    (),  # 45
    (),  # 46
    (48, 90),  # 47
    (),  # 48
    (),  # 49
    (1, 97, 3),  # 50
    (62, 56, 68, 25, 115),  # 51
    (),  # 52
    (28, 52),  # 53
    (52,),  # 54
    (52,),  # 55
    (),  # 56
    (62, 25),  # 57
    (1, 94),  # 58
    (63, 119),  # 59
    (),  # 60
    (),  # 61
    (),  # 62
    (),  # 63
    (),  # 64
    (),  # 65
    (),  # 66
    (44, 25, 74),  # 67
    (),  # 68
    (),  # 69
    (44, 25),  # 70
    (1, 3),  # 71
    (25, 96),  # 72
    (96,),  # 73
    (44, 64, 66, 65, 106, 75),  # 74
    (),  # 75
    (),  # 76
    (1,),  # 77
    (3, 106),  # 78
    (),  # 79
    (),  # 80
    (),  # 81
    (),  # 82
    (),  # 83
    (),  # 84
    (),  # 85
    (1, 28, 43, 69, 122),  # 86
    (69, 88),  # 87
    (),  # 88
    (),  # 89
    (),  # 90
    (),  # 91
    (44, 25, 75, 73),  # 92
    (1,),  # 93
    (),  # 94
    (),  # 95
    (),  # 96
    (),  # 97
    (1, 22, 24),  # 98
    (),  # 99
    (),  # 100
    (1,),  # 101
    (),  # 102
    (),  # 103
    (),  # 104
    (),  # 105
    (),  # 106
    (83,),  # 107
    (122,),  # 108
    (),  # 109
    (1,),  # 110
    (1,),  # 111
    (1,),  # 112
    (),  # 113
    (134,),  # 114
    (),  # 115
    (),  # 116
    (),  # 117
    (),  # 118
    (),  # 119
    (),  # 120
    (1, 28),  # 121
    (),  # 122
    (83, 142),  # 123
    (),  # 124
    (),  # 125
    (),  # 126
    (),  # 127
    (),  # 128
    (1,),  # 129
    (),  # 130
    (1,),  # 131
    (1,),  # 132
    (),  # 133
    (),  # 134
    (),  # 135
    (),  # 136
    (),  # 137
    (1,),  # 138
    (),  # 139
    (),  # 140
    (),  # 141
    (),  # 142
 )


def dependency_mask(index: int, initial: int = 0) -> int:
    """Execute the native visited-mask closure for one known component index.

    A bit already present in initial suppresses traversal of that component's
    dependencies, exactly as in 1826BD0. Initial is an unsigned 256-bit mask.
    """
    if type(index) is not int or not 0 <= index < len(_REQUIREMENTS):
        raise ValueError('expected a registered component index in 0..142')
    if type(initial) is not int or not 0 <= initial < 1 << 256:
        raise ValueError('expected an unsigned 256-bit component mask')
    pending = [index]
    value = initial
    while pending:
        item = pending.pop()
        if value & (1 << item):
            continue
        value |= 1 << item
        pending.extend(_REQUIREMENTS[item])
    return value


def declaration_mask(schema_hashes) -> int:
    """Union compiled requirements for local resource component declarations.

    17D3480 skips unmapped index zero. 18392F0 ORs a separately computed
    registry mask for each recognized component; previous declarations must
    not become a visited seed for later dependency traversal. This helper
    does not resolve child definitions, references, factories or lifetimes.
    """
    value = 0
    for schema_hash in schema_hashes:
        index = runtime_index(schema_hash)
        if index:
            value |= dependency_mask(index)
    return value
