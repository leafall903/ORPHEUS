import ctypes
import threading
import unittest
from unittest.mock import MagicMock, patch

from ow174.launcher import npc_hotkeys


class NpcHotkeyTests(unittest.TestCase):
    def test_stop_key_is_captured_while_lobby_dispatch_is_busy(self):
        user32 = MagicMock()
        kernel32 = MagicMock()
        process = MagicMock(pid=123)
        first_started = threading.Event()
        stop_delivered = threading.Event()
        capture_finished = threading.Event()
        finished = threading.Event()
        delivered = []
        user32.GetForegroundWindow.return_value = 456
        user32.GetKeyboardLayout.return_value = 1
        user32.GetAsyncKeyState.return_value = 0
        user32.SetWindowsHookExW.return_value = 1
        user32.CallNextHookEx.return_value = 0
        process.poll.side_effect = lambda: 0 if finished.is_set() else None

        def window_process(window, pid):
            pid._obj.value = process.pid
            return 1

        user32.GetWindowThreadProcessId.side_effect = window_process
        user32.VkKeyScanExW.side_effect = lambda key, layout: {"0": 0x30, "{": 0x1DB, "[": 0xDB}.get(key, -1)

        def send_key(vk, shifted=False):
            event = npc_hotkeys.KeyboardEvent(vk)
            callback = user32.SetWindowsHookExW.call_args.args[1]
            user32.GetAsyncKeyState.side_effect = lambda key: 0x8000 if shifted and key == 0x10 else 0
            callback(0, 0x0100, ctypes.addressof(event))

        def dispatch(pid, key):
            delivered.append((pid, key))
            if key == "0":
                first_started.set()
                capture_finished.wait(2)
            if delivered.count((pid, "{")) == 2:
                stop_delivered.set()

        def pump(*_):
            if not capture_finished.is_set():
                send_key(0x30)
                self.assertTrue(first_started.wait(2))
                send_key(0xDB, shifted=True)
                send_key(0xDB)
                send_key(0x10)
                capture_finished.set()
                self.assertTrue(stop_delivered.wait(2))
                finished.set()
            return False

        user32.PeekMessageW.side_effect = pump
        with patch.object(ctypes, "WinDLL", side_effect=[user32, kernel32]):
            npc_hotkeys.watch(process, dispatch)
        self.assertEqual(delivered, [(123, "0"), (123, "{"), (123, "{")])
        user32.UnhookWindowsHookEx.assert_called_once_with(1)
