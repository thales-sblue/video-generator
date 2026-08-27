"""Read-only diagnostics for local audiovisual dependencies."""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from video_generator.config import AppConfig


@dataclass(frozen=True, slots=True)
class ToolStatus:
    name: str
    available: bool
    version: str | None = None
    path: str | None = None
    note: str | None = None


@dataclass(frozen=True, slots=True)
class DoctorReport:
    operating_system: str
    platform_release: str
    machine: str
    local_only: bool
    external_services_allowed: bool
    preserve_sources: bool
    config_path: str
    tools: tuple[ToolStatus, ...]

    def to_dict(self) -> dict[str, object]:
        result = asdict(self)
        result["tools"] = [asdict(tool) for tool in self.tools]
        return result

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _first_line(value: str) -> str | None:
    for line in value.splitlines():
        if line.strip():
            return line.strip()
    return None


def _command_status(name: str, executable: str, version_args: tuple[str, ...]) -> ToolStatus:
    path = shutil.which(executable)
    if path is None:
        return ToolStatus(name=name, available=False, note="optional dependency not found on PATH")
    try:
        completed = subprocess.run(
            [path, *version_args],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
            shell=False,
        )
        output = completed.stdout or completed.stderr
        version = _first_line(output)
        note = None if completed.returncode == 0 else f"version command exited with {completed.returncode}"
        return ToolStatus(name=name, available=True, version=version, path=path, note=note)
    except (OSError, subprocess.SubprocessError) as exc:
        return ToolStatus(name=name, available=True, path=path, note=f"version unavailable: {type(exc).__name__}")


def _python_status() -> ToolStatus:
    return ToolStatus(
        name="Python",
        available=True,
        version=platform.python_version(),
        path=sys.executable,
    )


def _hyperframes_status(node_status: ToolStatus) -> ToolStatus:
    command = shutil.which("hyperframes") or shutil.which("hyperframes.cmd")
    if command:
        status = _command_status("HyperFrames", command, ("--version",))
        return status

    configured_home = os.environ.get("HYPERFRAMES_HOME")
    if configured_home:
        home = Path(configured_home).expanduser()
        if home.exists():
            return ToolStatus(
                name="HyperFrames",
                available=True,
                path=str(home.resolve()),
                note="found through HYPERFRAMES_HOME; no CLI version detected",
            )

    npm = shutil.which("npm") or shutil.which("npm.cmd")
    if node_status.available and npm:
        try:
            completed = subprocess.run(
                [npm, "root", "--global"],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
                shell=False,
            )
            root_text = _first_line(completed.stdout)
            if completed.returncode == 0 and root_text:
                npm_root = Path(root_text)
                for package_name in ("hyperframes", "@hyperframes/core"):
                    package = npm_root.joinpath(*package_name.split("/"))
                    if package.exists():
                        version = None
                        manifest = package / "package.json"
                        try:
                            version = json.loads(manifest.read_text(encoding="utf-8")).get("version")
                        except (OSError, json.JSONDecodeError):
                            pass
                        return ToolStatus(
                            name="HyperFrames",
                            available=True,
                            version=version,
                            path=str(package.resolve()),
                            note="found as a global npm package",
                        )
        except (OSError, subprocess.SubprocessError):
            pass

    return ToolStatus(
        name="HyperFrames",
        available=False,
        note="optional renderer not found; no installation was attempted",
    )


def run_doctor(config: AppConfig) -> DoctorReport:
    node = _command_status("Node", "node", ("--version",))
    tools = (
        _python_status(),
        node,
        _command_status("FFmpeg", "ffmpeg", ("-version",)),
        _command_status("ffprobe", "ffprobe", ("-version",)),
        _command_status("Git", "git", ("--version",)),
        _hyperframes_status(node),
    )
    return DoctorReport(
        operating_system=platform.system(),
        platform_release=platform.release(),
        machine=platform.machine(),
        local_only=config.processing.local_only,
        external_services_allowed=config.processing.allow_external_services,
        preserve_sources=config.processing.preserve_sources,
        config_path=str(config.source_path),
        tools=tools,
    )


def format_report(report: DoctorReport) -> str:
    lines = [
        "video-generator doctor",
        f"OS: {report.operating_system} {report.platform_release} ({report.machine})",
        f"Config: {report.config_path}",
        f"Local-only: {'enabled' if report.local_only else 'DISABLED'}",
        f"External services: {'allowed' if report.external_services_allowed else 'disabled'}",
        f"Preserve sources: {'enabled' if report.preserve_sources else 'DISABLED'}",
        "Tools:",
    ]
    for tool in report.tools:
        status = "available" if tool.available else "missing (optional)"
        details = [item for item in (tool.version, tool.path, tool.note) if item]
        suffix = f" — {'; '.join(details)}" if details else ""
        lines.append(f"  [{status}] {tool.name}{suffix}")
    return "\n".join(lines) + "\n"
