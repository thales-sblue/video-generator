import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from video_generator import tooling


class LocalToolResolutionTests(unittest.TestCase):
    def tearDown(self):
        tooling._validated_local_bin.cache_clear()

    def _installation(self, directory: str):
        project = Path(directory)
        local_root = project / ".local-tools" / "ffmpeg"
        bin_path = local_root / "approved" / "bin"
        bin_path.mkdir(parents=True)
        files = {"ffmpeg.exe": b"ffmpeg", "ffprobe.exe": b"ffprobe", "codec.dll": b"codec"}
        records = {}
        for name, content in files.items():
            path = bin_path / name
            path.write_bytes(content)
            records[name] = {
                "size": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            }
        lock_path = project / "ffmpeg-lock.json"
        lock_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "install_directory": "approved",
                    "bin_files": records,
                }
            ),
            encoding="utf-8",
        )
        return local_root, lock_path, bin_path

    def test_prefers_a_complete_hash_locked_local_installation(self):
        with tempfile.TemporaryDirectory() as directory:
            local_root, lock_path, bin_path = self._installation(directory)
            with patch.object(tooling, "LOCAL_ROOT", local_root), patch.object(
                tooling, "LOCK_PATH", lock_path
            ):
                tooling._validated_local_bin.cache_clear()
                resolved = tooling.resolve_media_tool("ffprobe", path_lookup=lambda _: "PATH")

        self.assertEqual(resolved, str(bin_path / "ffprobe.exe"))

    def test_rejects_tampering_and_unexpected_dlls(self):
        with tempfile.TemporaryDirectory() as directory:
            local_root, lock_path, bin_path = self._installation(directory)
            (bin_path / "codec.dll").write_bytes(b"changed")
            with patch.object(tooling, "LOCAL_ROOT", local_root), patch.object(
                tooling, "LOCK_PATH", lock_path
            ):
                tooling._validated_local_bin.cache_clear()
                with self.assertRaisesRegex(tooling.ToolResolutionError, "integrity check failed"):
                    tooling.resolve_media_tool("ffmpeg")

            (bin_path / "codec.dll").write_bytes(b"codec")
            (bin_path / "unexpected.dll").write_bytes(b"unexpected")
            with patch.object(tooling, "LOCAL_ROOT", local_root), patch.object(
                tooling, "LOCK_PATH", lock_path
            ):
                tooling._validated_local_bin.cache_clear()
                with self.assertRaisesRegex(tooling.ToolResolutionError, "file set differs"):
                    tooling.resolve_media_tool("ffmpeg")

    def test_falls_back_to_path_only_when_no_local_install_exists(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "missing"
            with patch.object(tooling, "LOCAL_ROOT", missing):
                tooling._validated_local_bin.cache_clear()
                resolved = tooling.resolve_media_tool("ffmpeg", path_lookup=lambda _: "PATH/ffmpeg")

        self.assertEqual(resolved, "PATH/ffmpeg")


class KokoroAssetResolutionTests(unittest.TestCase):
    def test_returns_none_when_the_assets_directory_is_absent(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(tooling, "KOKORO_LOCAL_ROOT", Path(directory) / "kokoro"):
                self.assertIsNone(tooling.resolve_kokoro_assets())

    def test_resolves_both_files_and_fails_closed_on_a_partial_install(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "kokoro"
            root.mkdir()
            model = root / "kokoro-v1.0.onnx"
            voices = root / "voices-v1.0.bin"
            model.write_bytes(b"onnx-bytes")
            with patch.object(tooling, "KOKORO_LOCAL_ROOT", root):
                with self.assertRaisesRegex(tooling.ToolResolutionError, "voices-v1.0.bin"):
                    tooling.resolve_kokoro_assets()
                voices.write_bytes(b"")
                with self.assertRaisesRegex(tooling.ToolResolutionError, "empty"):
                    tooling.resolve_kokoro_assets()
                voices.write_bytes(b"voice-bytes")
                self.assertEqual(
                    tooling.resolve_kokoro_assets(),
                    (str(model.resolve()), str(voices.resolve())),
                )

    def test_honours_the_kokoro_home_override(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "elsewhere"
            root.mkdir()
            for name in tooling.KOKORO_ASSET_NAMES:
                (root / name).write_bytes(b"x")
            with patch.dict("os.environ", {"KOKORO_HOME": str(root)}, clear=False):
                self.assertEqual(tooling.kokoro_assets_root(), root)
                self.assertIsNotNone(tooling.resolve_kokoro_assets())


if __name__ == "__main__":
    unittest.main()
