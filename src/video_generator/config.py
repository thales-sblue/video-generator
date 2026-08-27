"""Fail-closed loading for the local-only project configuration."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path


class ConfigurationError(ValueError):
    """Raised when configuration could permit unsafe processing."""


@dataclass(frozen=True, slots=True)
class ProcessingConfig:
    local_only: bool
    allow_external_services: bool
    preserve_sources: bool


@dataclass(frozen=True, slots=True)
class PathsConfig:
    inputs: str
    assets: str
    projects: str
    output: str


@dataclass(frozen=True, slots=True)
class AppConfig:
    processing: ProcessingConfig
    paths: PathsConfig
    source_path: Path


def default_config_path() -> Path:
    return Path(__file__).resolve().parents[2] / "config" / "default.toml"


def load_config(path: str | Path | None = None) -> AppConfig:
    source_path = Path(path) if path is not None else default_config_path()
    try:
        with source_path.open("rb") as handle:
            raw = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigurationError(f"cannot load configuration: {source_path}") from exc

    try:
        processing = raw["processing"]
        paths = raw["paths"]
        result = AppConfig(
            processing=ProcessingConfig(
                local_only=processing["local_only"],
                allow_external_services=processing["allow_external_services"],
                preserve_sources=processing["preserve_sources"],
            ),
            paths=PathsConfig(
                inputs=paths["inputs"],
                assets=paths["assets"],
                projects=paths["projects"],
                output=paths["output"],
            ),
            source_path=source_path.resolve(),
        )
    except (KeyError, TypeError) as exc:
        raise ConfigurationError("configuration is missing required fields") from exc

    if result.processing.local_only is not True:
        raise ConfigurationError("processing.local_only must be true")
    if result.processing.allow_external_services is not False:
        raise ConfigurationError("processing.allow_external_services must be false")
    if result.processing.preserve_sources is not True:
        raise ConfigurationError("processing.preserve_sources must be true")
    for name in ("inputs", "assets", "projects", "output"):
        value = getattr(result.paths, name)
        if not isinstance(value, str) or not value.strip():
            raise ConfigurationError(f"paths.{name} must be a non-empty string")
    return result
