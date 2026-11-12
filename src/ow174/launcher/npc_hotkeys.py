"""Observe sandbox console keys while a launched Windows client has focus."""

import ctypes
import ctypes.wintypes as wt
import logging
import queue
import threading
import time

log = logging.getLogger("ow174.launcher")

CHARACTERS = "=-0123456789{"
MODIFIERS = (0x10, 0x11, 0x12)  # Shift, Control, Alt
WH_KEYBOARD_LL = 13
KEY_DOWN_MESSAGES = (0x0100, 0x0104)


class KeyboardEvent(ctypes.Structure):
    _fields_ = [("vk", wt.DWORD), ("scan", wt.DWORD), ("flags", wt.DWORD),
                ("time", wt.DWORD), ("extra", ctypes.c_size_t)]


def watch(process, dispatch):
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    foreground = user32.GetForegroundWindow
    foreground.argtypes, foreground.restype = [], wt.HWND
    window_process = user32.GetWindowThreadProcessId
    window_process.argtypes, window_process.restype = [wt.HWND, ctypes.POINTER(wt.DWORD)], wt.DWORD
    keyboard_layout = user32.GetKeyboardLayout
    keyboard_layout.argtypes, keyboard_layout.restype = [wt.DWORD], wt.HANDLE
    scan = user32.VkKeyScanExW
    scan.argtypes, scan.restype = [wt.WCHAR, wt.HANDLE], ctypes.c_short
    key_state = user32.GetAsyncKeyState
    key_state.argtypes, key_state.restype = [ctypes.c_int], ctypes.c_short
    hook_type = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wt.WPARAM, wt.LPARAM)
    install = user32.SetWindowsHookExW
    install.argtypes, install.restype = [ctypes.c_int, hook_type, wt.HMODULE, wt.DWORD], wt.HANDLE
    next_hook = user32.CallNextHookEx
    next_hook.argtypes = [wt.HANDLE, ctypes.c_int, wt.WPARAM, wt.LPARAM]
    next_hook.restype = ctypes.c_ssize_t
    unhook = user32.UnhookWindowsHookEx
    unhook.argtypes, unhook.restype = [wt.HANDLE], wt.BOOL
    peek = user32.PeekMessageW
    peek.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT, wt.UINT]
    peek.restype = wt.BOOL
    module = ctypes.WinDLL("kernel32").GetModuleHandleW
    module.argtypes, module.restype = [wt.LPCWSTR], wt.HMODULE

    current_layout = None
    bindings = []
    pending = queue.SimpleQueue()
    stopped = threading.Event()

    def deliver():
        while (event := pending.get()) is not None and not stopped.is_set():
            window, key = event
            if foreground() != window or process.poll() is not None:
                continue
            try:
                dispatch(process.pid, key)
            except OSError:
                log.debug("NPC hotkey client disconnected", exc_info=True)

    def enqueue(event_address):
        nonlocal current_layout, bindings
        window = foreground()
        pid = wt.DWORD()
        thread = window_process(window, ctypes.byref(pid)) if window else 0
        if not thread or pid.value != process.pid:
            return
        event = KeyboardEvent.from_address(event_address)
        layout = keyboard_layout(thread)
        if layout != current_layout:
            current_layout = layout
            bindings = [("Escape", 0x1B, 0)]
            for character in CHARACTERS:
                code = scan(character, layout)
                if code != -1:
                    bindings.append((character, code & 0xFF, code >> 8))
            # A layout with a plain [ can stop NPCs without pressing the
            # same Shift key that the stock client uses for hero abilities.
            bracket = scan("[", layout)
            if 0 <= bracket <= 0xFF and all(vk != bracket or mask for _, vk, mask in bindings):
                bindings.append(("{", bracket, 0))
            bindings.extend((str(digit), 0x60 + digit, 0) for digit in range(10))
        # The low-level callback runs before Windows changes the state for
        # this event. A key already down here is an auto-repeat.
        if key_state(event.vk) & 0x8000:
            return
        modifiers = sum(1 << i for i, vk in enumerate(MODIFIERS) if key_state(vk) & 0x8000)
        for key, vk, mask in bindings:
            if vk == event.vk and mask == modifiers:
                pending.put((window, key))

    @hook_type
    def on_key(code, message, event_address):
        if code == 0 and message in KEY_DOWN_MESSAGES:
            enqueue(event_address)
        return next_hook(None, code, message, event_address)

    hook = install(WH_KEYBOARD_LL, on_key, module(None), 0)
    if not hook:
        log.warning("Unable to install NPC hotkey listener (Windows error %d)", ctypes.get_last_error())
        return
    message = wt.MSG()
    try:
        # Lobby dispatch can wait for the match lock or the network. Keep the
        # hook's thread pumping Windows messages independently of both.
        threading.Thread(target=deliver, daemon=True, name=f"npc-dispatch-{process.pid}").start()
        while process.poll() is None:
            while peek(ctypes.byref(message), None, 0, 0, 1):
                pass
            time.sleep(0.01)
    finally:
        unhook(hook)
        stopped.set()
        pending.put(None)
