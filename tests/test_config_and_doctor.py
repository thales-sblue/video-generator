import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from unittest.mock import patch

from video_generator.cli import main
from video_generator.config import ConfigurationError, load_config
from video_generator.doctor import format_report, run_doctor


class ConfigTests(unittest.TestCase):
    def test_default_configuration_is_fail_closed(self):
        config = load_config()

        self.assertTrue(config.processing.local_only)
        self.assertFalse(config.processing.allow_external_services)
        self.assertTrue(config.processing.preserve_sources)
        self.assertEqual(config.paths.output, "output")

    def test_rejects_external_services(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "unsafe.toml"
            path.write_text(
                """
[processing]
local_only = true
allow_external_services = true
preserve_sources = true
[paths]
inputs = "inputs"
assets = "assets"
projects = "projects"
output = "output"
""".strip(),
                encoding="utf-8",
            )

            with self.assertRaises(ConfigurationError):
                load_config(path)


class DoctorTests(unittest.TestCase):
    def test_report_contains_required_environment_checks(self):
        report = run_doctor(load_config())

        self.assertTrue(report.operating_system)
        self.assertTrue(report.local_only)
        self.assertFalse(report.external_services_allowed)
        self.assertEqual(
            {tool.name for tool in report.tools},
            {
                "Python", "Node", "FFmpeg", "ffprobe", "Git",
                "HyperFrames", "Remotion", "Kokoro", "Aligner",
            },
        )
        self.assertTrue(next(tool for tool in report.tools if tool.name == "Python").available)
        self.assertIn("Local-only: enabled", format_report(report))

    def test_reports_kokoro_state_from_local_assets_and_package(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "kokoro"

            with patch.dict("os.environ", {"KOKORO_HOME": str(root)}, clear=False):
                # nothing installed
                missing = next(t for t in run_doctor(load_config()).tools if t.name == "Kokoro")
                self.assertFalse(missing.available)
                self.assertIn("not set up", missing.note)

                # model files present but a partial install fails closed
                root.mkdir(parents=True)
                (root / "kokoro-v1.0.onnx").write_bytes(b"onnx")
                rejected = next(t for t in run_doctor(load_config()).tools if t.name == "Kokoro")
                self.assertFalse(rejected.available)
                self.assertIn("rejected", rejected.note)

                # complete assets, package resolution mocked both ways
                (root / "voices-v1.0.bin").write_bytes(b"voices")
                with patch("importlib.util.find_spec", return_value=None):
                    no_pkg = next(t for t in run_doctor(load_config()).tools if t.name == "Kokoro")
                self.assertFalse(no_pkg.available)
                self.assertIn("tts' extra", no_pkg.note)
                with patch("importlib.util.find_spec", return_value=object()):
                    ready = next(t for t in run_doctor(load_config()).tools if t.name == "Kokoro")
                self.assertTrue(ready.available)

    def test_cli_emits_machine_readable_report(self):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exit_code = main(["doctor", "--json"])

        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertTrue(payload["local_only"])
        self.assertFalse(payload["external_services_allowed"])


if __name__ == "__main__":
    unittest.main()
