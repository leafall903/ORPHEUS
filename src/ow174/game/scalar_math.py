"""Host x64 scalar instruction required by the target's distance arithmetic.

Only generated RSQRTSS/STMXCSR instructions execute in this process. No game
code, binary assets, compiler or external service is loaded. Executable memory
is written once then protected read/execute; it is released at process exit.
"""

import atexit
import ctypes
import hashlib
import platform
import struct
import threading

_instruction = b'\xf3\x0f\x52\xc0\xc3'  # rsqrtss xmm0,xmm0; ret
_control_instruction = b'\x0f\xae\x19\xc3'  # stmxcsr [rcx]; ret
_lock = threading.RLock()
_kernel = None


class ScalarMathUnavailable(RuntimeError):
    """No qualified instruction backend; an ideal-sqrt fallback is not equivalent."""


def supported():
    return (platform.system() == 'Windows' and platform.machine().upper() in {'AMD64', 'X86_64'}
            and struct.calcsize('P') == 8)


class _Kernel:
    def __init__(self):
        if not supported():
            raise ScalarMathUnavailable('RSQRTSS requires the qualified Windows x64 backend')
        api = self.api = ctypes.WinDLL('kernel32', use_last_error=True)
        api.VirtualAlloc.argtypes = (ctypes.c_void_p, ctypes.c_size_t, ctypes.c_ulong, ctypes.c_ulong)
        api.VirtualAlloc.restype = ctypes.c_void_p
        api.VirtualProtect.argtypes = (
            ctypes.c_void_p, ctypes.c_size_t, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong),
        )
        api.VirtualProtect.restype = ctypes.c_int
        api.VirtualFree.argtypes = (ctypes.c_void_p, ctypes.c_size_t, ctypes.c_ulong)
        api.VirtualFree.restype = ctypes.c_int
        api.GetCurrentProcess.argtypes = ()
        api.GetCurrentProcess.restype = ctypes.c_void_p
        api.FlushInstructionCache.argtypes = (ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t)
        api.FlushInstructionCache.restype = ctypes.c_int
        self.page = api.VirtualAlloc(None, 4096, 0x3000, 0x04)
        if not self.page:
            raise ScalarMathUnavailable(f'RSQRTSS allocation failed: {ctypes.get_last_error()}')
        try:
            ctypes.memmove(self.page, _instruction, len(_instruction))
            ctypes.memmove(self.page + 16, _control_instruction, len(_control_instruction))
            previous = ctypes.c_ulong()
            if not api.VirtualProtect(self.page, 4096, 0x20, ctypes.byref(previous)):
                raise ScalarMathUnavailable(f'RSQRTSS protection failed: {ctypes.get_last_error()}')
            if not api.FlushInstructionCache(api.GetCurrentProcess(), self.page, 4096):
                raise ScalarMathUnavailable(f'RSQRTSS instruction flush failed: {ctypes.get_last_error()}')
            self.rsqrt = ctypes.WINFUNCTYPE(ctypes.c_float, ctypes.c_float)(self.page)
            self.control = ctypes.WINFUNCTYPE(None, ctypes.POINTER(ctypes.c_uint32))(self.page + 16)
        except BaseException:
            self.close()
            raise
        atexit.register(self.close)

    def close(self):
        # ctypes releases the GIL during calls; hold the same lock so shutdown
        # cannot free the page while another thread is executing it.
        with _lock:
            if self.page is not None:
                self.rsqrt = self.control = None
                self.api.VirtualFree(self.page, 0, 0x8000)
                self.page = None


def _get_kernel():
    global _kernel
    with _lock:
        if _kernel is None:
            _kernel = _Kernel()
        if _kernel.page is None:
            raise ScalarMathUnavailable('RSQRTSS backend is closed')
        return _kernel


def reciprocal_sqrt(value):
    """Return the host's single-precision RSQRTSS approximation, not ideal sqrt."""
    with _lock:
        kernel = _get_kernel()
        control = ctypes.c_uint32()
        kernel.control(ctypes.byref(control))
        if control.value & 0xFFC0 != 0x1F80:
            raise ScalarMathUnavailable('distance arithmetic requires qualified default MXCSR controls')
        return kernel.rsqrt(value)


def profile():
    """Detached, nonsecret arithmetic identity for strict replay compatibility."""
    if not supported():
        return {'backend': 'unavailable', 'system': platform.system(), 'machine': platform.machine()}
    with _lock:
        return _profile()


def _profile():
    kernel = _get_kernel()
    control = ctypes.c_uint32()
    kernel.control(ctypes.byref(control))
    # Include subnormal/extreme inputs as well as a deterministic normal grid.
    # Sticky exception flags are not controls and must not change the manifest.
    inputs = [0, 1, 0x007FFFFF, 0x00800000, 0x7F7FFFFF]
    inputs.extend(0x3C000000 + index * 0x00071357 for index in range(256))
    outputs = b''.join(struct.pack('<f', kernel.rsqrt(struct.unpack('<f', struct.pack('<I', item))[0]))
                       for item in inputs)
    return {
        'backend': 'windows-x64-rsqrtss-v1', 'mxcsr_controls': control.value & 0xFFC0,
        'distance_controls_qualified': control.value & 0xFFC0 == 0x1F80,
        'probe_count': len(inputs), 'probe_sha256': hashlib.sha256(outputs).hexdigest(),
    }
