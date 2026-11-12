"""Compare 104319's server 54FEB21C callback with the production State model.

Requires Unicorn and an authorized external loaded client-image capture. This
executes original callback instructions; it does not launch or attach a client.
"""

import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path

from unicorn import UC_ARCH_X86, UC_HOOK_CODE, UC_MODE_64, Uc
from unicorn.x86_const import UC_X86_REG_RCX, UC_X86_REG_RDX, UC_X86_REG_RSP

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(Path(__file__).resolve().parents[1] / "src"),
                str(Path(__file__).resolve().parents[1])]

from ow174.game.script.nodes import FinishOnSignal  # noqa: E402

CALLBACK, FINISH_REQUEST = 0xC880C0, 0x1B712E0
SERVER_VTABLE, NO_OP = 0x261E338, 0x1CE5640
CALLBACK_BYTES = bytes.fromhex('83fa01750733d2e91492ee00c3')
STACK, END = 0x200000, 0x600000


class Capture:
    def __init__(self, state, requests):
        self.state, self.requests = state, requests

    def request_finish(self, delay=0):
        self.requests.append((self.state, delay))


def compare(image, base):
    if image[CALLBACK:CALLBACK + len(CALLBACK_BYTES)] != CALLBACK_BYTES:
        raise ValueError('input is not the expected build104319 server callback')
    hooks = {name: struct.unpack_from('<Q', image, SERVER_VTABLE + slot)[0] - base
             for name, slot in (('enter', 0x60), ('begin', 0xB8), ('update', 0xE0), ('timer', 0x90))}
    if hooks != {'enter': NO_OP, 'begin': NO_OP, 'update': NO_OP, 'timer': CALLBACK}:
        raise ValueError('server lifecycle table differs from the inspected104319 input')
    uc = Uc(UC_ARCH_X86, UC_MODE_64)
    uc.mem_map(base, (len(image) + 4095) & ~4095)
    uc.mem_write(base, image)
    uc.mem_map(STACK, 0x10000)
    uc.mem_map(END, 0x1000)
    requests = []

    def hook(machine, address, size, data):
        if address == base + FINISH_REQUEST:
            requests.append((machine.reg_read(UC_X86_REG_RCX), machine.reg_read(UC_X86_REG_RDX)))
            machine.emu_stop()
        elif address == END:
            machine.emu_stop()

    uc.hook_add(UC_HOOK_CODE, hook)
    failures, count = [], 0
    params = (*range(256), 0x7FFFFFFF, 0x80000000, 0xFFFFFFFF)
    for state in (0, 0x400000, 0x410000, 0x100000001, 0x7FFFFFFFFFFF):
        for param in params:
            requests.clear()
            stack = STACK + 0xFFF8
            uc.mem_write(stack, struct.pack('<Q', END))
            uc.reg_write(UC_X86_REG_RSP, stack)
            uc.reg_write(UC_X86_REG_RCX, state)
            uc.reg_write(UC_X86_REG_RDX, param)
            uc.emu_start(base + CALLBACK, END, count=32)
            expected = list(requests)
            actual = []

            FinishOnSignal.timer(Capture(state, actual), param)
            count += 1
            if actual != expected:
                failures.append({'state': state, 'param': param, 'native': expected, 'model': actual})
    return {'cases': count, 'failures': failures,
            'callback_rva': f'{CALLBACK:X}', 'finish_request_rva': f'{FINISH_REQUEST:X}',
            'server_vtable_rva': f'{SERVER_VTABLE:X}',
            'lifecycle_rvas': {name: f'{value:X}' for name, value in hooks.items()},
            'scope': 'server timer/event callback only; original request helper intercepted',
            'client_scheduling_and_transport': 'not supplied or accepted'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', type=Path, required=True)
    parser.add_argument('--base', type=lambda value: int(value, 0), required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.resolve().is_relative_to(ROOT) or args.output.resolve() == args.image.resolve():
        parser.error('write the report outside source and the immutable input')
    image = args.image.read_bytes()
    result = compare(image, args.base)
    result['input_sha256'] = hashlib.sha256(image).hexdigest()
    args.output.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result))
    return bool(result['failures'])


if __name__ == '__main__':
    raise SystemExit(main())
