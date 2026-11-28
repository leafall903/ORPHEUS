import sys
from pathlib import Path
import tempfile
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runtime_voice import voice_locales


class VoiceLocaleTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def active(self, tags):
        (self.root / ".build.info").write_text(
            "Product!STRING:0|Active!DEC:1|Version!STRING:0|Tags!STRING:0\n"
            "pro|0|104319|enUS text?:enUS speech?\n"
            "other|1|104319|enUS text?:enUS speech?\n"
            "pro|1|1|enUS text?:enUS speech?\n"
            f"pro|1|104319|{tags}\n", encoding="utf-8-sig")

    def test_active_installation_locales_and_regional_preference(self):
        self.active("tag deDE text?:tag deDE speech?:tag zhTW text?:tag zhTW speech?:tag zhCN speech?:tag zhCN text?")
        self.assertEqual(voice_locales(self.root, "104319", "zh_CN"), ("zhCN", "zhCN"))
        self.assertEqual(voice_locales(self.root, "104319", "zh-HK"), ("zhTW", "zhTW"))
        self.assertEqual(voice_locales(self.root, "104319", "en-US"), ("deDE", "deDE"))

    def test_different_text_and_speech_languages(self):
        self.active("tag zhCN text?:tag koKR speech?")
        self.assertEqual(voice_locales(self.root, "104319", "zh-CN"), ("zhCN", "koKR"))
        self.active("tag zhCN text?")
        with self.assertRaisesRegex(ValueError, "speech"):
            voice_locales(self.root, "104319", "zh-CN")
