"""Versioned, JSON-safe contracts with no renderer or I/O dependencies."""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping


SCHEMA_VERSION = 1
SHA256_HEX_LENGTH = 64


class ContractError(ValueError):
    """Raised when persisted planning data violates a contract."""


def _text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"{field_name} must be a non-empty string")
    return value.strip()


def _optional_text(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    return _text(value, field_name)


def _text_tuple(value: object, field_name: str, *, allow_empty: bool = False) -> tuple[str, ...]:
    if isinstance(value, str) or not isinstance(value, (list, tuple)):
        raise ContractError(f"{field_name} must be an array of strings")
    result = tuple(_text(item, field_name) for item in value)
    if not result and not allow_empty:
        raise ContractError(f"{field_name} must contain at least one item")
    if len(set(result)) != len(result):
        raise ContractError(f"{field_name} must not contain duplicates")
    return result


def _schema_version(value: object) -> int:
    if value != SCHEMA_VERSION:
        raise ContractError(f"schema_version must be {SCHEMA_VERSION}")
    return SCHEMA_VERSION


def _sha256(value: object, field_name: str) -> str:
    result = _text(value, field_name)
    if (
        result != result.lower()
        or len(result) != SHA256_HEX_LENGTH
        or any(character not in "0123456789abcdef" for character in result)
    ):
        raise ContractError(f"{field_name} must be a lowercase SHA-256 hex digest")
    return result


def _keys(data: Mapping[str, Any], *, required: set[str], optional: set[str]) -> None:
    if not isinstance(data, Mapping):
        raise ContractError("contract payload must be an object")
    present = set(data)
    missing = required - present
    unknown = present - required - optional
    if missing:
        raise ContractError(f"missing fields: {', '.join(sorted(missing))}")
    if unknown:
        raise ContractError(f"unknown fields: {', '.join(sorted(unknown))}")


def _json_value(value: object, field_name: str) -> object:
    try:
        encoded = json.dumps(value, allow_nan=False, sort_keys=True)
        return json.loads(encoded)
    except (TypeError, ValueError) as exc:
        raise ContractError(f"{field_name} must contain JSON-safe values") from exc


def _freeze_json(value: object) -> object:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze_json(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    return value


def _thaw_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


def _to_json(payload: Mapping[str, Any], *, indent: int | None = 2) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=indent, sort_keys=True) + "\n"


@dataclass(frozen=True, slots=True)
class VideoRequest:
    """The user's audiovisual intent and immutable source references."""

    request_id: str
    intent: str
    sources: tuple[str, ...]
    platform: str | None = None
    workflow: str | None = None
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "request_id", _text(self.request_id, "request_id"))
        object.__setattr__(self, "intent", _text(self.intent, "intent"))
        object.__setattr__(self, "sources", _text_tuple(self.sources, "sources"))
        object.__setattr__(self, "platform", _optional_text(self.platform, "platform"))
        object.__setattr__(self, "workflow", _optional_text(self.workflow, "workflow"))
        _schema_version(self.schema_version)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "request_id": self.request_id,
            "intent": self.intent,
            "sources": list(self.sources),
            "platform": self.platform,
            "workflow": self.workflow,
        }

    def to_json(self, *, indent: int | None = 2) -> str:
        return _to_json(self.to_dict(), indent=indent)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> VideoRequest:
        _keys(
            data,
            required={"schema_version", "request_id", "intent", "sources"},
            optional={"platform", "workflow"},
        )
        return cls(
            schema_version=data["schema_version"],
            request_id=data["request_id"],
            intent=data["intent"],
            sources=data["sources"],
            platform=data.get("platform"),
            workflow=data.get("workflow"),
        )


@dataclass(frozen=True, slots=True)
class VideoBrief:
    """Editorial interpretation of a VideoRequest."""

    brief_id: str
    request_id: str
    objective: str
    platform: str
    workflow: str
    audience: str | None = None
    aspect_ratio: str | None = None
    target_duration_seconds: float | None = None
    editorial_notes: tuple[str, ...] = ()
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        for name in ("brief_id", "request_id", "objective", "platform", "workflow"):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        object.__setattr__(self, "audience", _optional_text(self.audience, "audience"))
        object.__setattr__(self, "aspect_ratio", _optional_text(self.aspect_ratio, "aspect_ratio"))
        object.__setattr__(
            self,
            "editorial_notes",
            _text_tuple(self.editorial_notes, "editorial_notes", allow_empty=True),
        )
        if self.target_duration_seconds is not None:
            if isinstance(self.target_duration_seconds, bool) or not isinstance(
                self.target_duration_seconds, (int, float)
            ):
                raise ContractError("target_duration_seconds must be a number")
            if not math.isfinite(self.target_duration_seconds) or self.target_duration_seconds <= 0:
                raise ContractError("target_duration_seconds must be greater than zero")
            object.__setattr__(self, "target_duration_seconds", float(self.target_duration_seconds))
        _schema_version(self.schema_version)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "brief_id": self.brief_id,
            "request_id": self.request_id,
            "objective": self.objective,
            "platform": self.platform,
            "workflow": self.workflow,
            "audience": self.audience,
            "aspect_ratio": self.aspect_ratio,
            "target_duration_seconds": self.target_duration_seconds,
            "editorial_notes": list(self.editorial_notes),
        }

    def to_json(self, *, indent: int | None = 2) -> str:
        return _to_json(self.to_dict(), indent=indent)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> VideoBrief:
        _keys(
            data,
            required={"schema_version", "brief_id", "request_id", "objective", "platform", "workflow"},
            optional={"audience", "aspect_ratio", "target_duration_seconds", "editorial_notes"},
        )
        return cls(
            schema_version=data["schema_version"],
            brief_id=data["brief_id"],
            request_id=data["request_id"],
            objective=data["objective"],
            platform=data["platform"],
            workflow=data["workflow"],
            audience=data.get("audience"),
            aspect_ratio=data.get("aspect_ratio"),
            target_duration_seconds=data.get("target_duration_seconds"),
            editorial_notes=data.get("editorial_notes", ()),
        )


@dataclass(frozen=True, slots=True)
class EditOperation:
    """A declarative, renderer-independent operation in an EditPlan."""

    operation_id: str
    kind: str
    source: str | None = None
    start_seconds: float | None = None
    end_seconds: float | None = None
    parameters: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "operation_id", _text(self.operation_id, "operation_id"))
        object.__setattr__(self, "kind", _text(self.kind, "kind"))
        object.__setattr__(self, "source", _optional_text(self.source, "source"))
        for name in ("start_seconds", "end_seconds"):
            value = getattr(self, name)
            if value is not None:
                if (
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(value)
                    or value < 0
                ):
                    raise ContractError(f"{name} must be a non-negative number")
                object.__setattr__(self, name, float(value))
        if self.start_seconds is not None and self.end_seconds is not None:
            if self.end_seconds <= self.start_seconds:
                raise ContractError("end_seconds must be greater than start_seconds")
        if not isinstance(self.parameters, Mapping):
            raise ContractError("parameters must be an object")
        safe_parameters = _json_value(dict(self.parameters), "parameters")
        object.__setattr__(self, "parameters", _freeze_json(safe_parameters))

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation_id": self.operation_id,
            "kind": self.kind,
            "source": self.source,
            "start_seconds": self.start_seconds,
            "end_seconds": self.end_seconds,
            "parameters": _thaw_json(self.parameters),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> EditOperation:
        _keys(
            data,
            required={"operation_id", "kind"},
            optional={"source", "start_seconds", "end_seconds", "parameters"},
        )
        return cls(
            operation_id=data["operation_id"],
            kind=data["kind"],
            source=data.get("source"),
            start_seconds=data.get("start_seconds"),
            end_seconds=data.get("end_seconds"),
            parameters=data.get("parameters", {}),
        )


@dataclass(frozen=True, slots=True)
class EditPlan:
    """A validated, non-destructive sequence of audiovisual operations."""

    plan_id: str
    brief_id: str
    sources: tuple[str, ...]
    output_path: str
    operations: tuple[EditOperation, ...]
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "plan_id", _text(self.plan_id, "plan_id"))
        object.__setattr__(self, "brief_id", _text(self.brief_id, "brief_id"))
        object.__setattr__(self, "sources", _text_tuple(self.sources, "sources"))
        object.__setattr__(self, "output_path", _text(self.output_path, "output_path"))
        if not isinstance(self.operations, (list, tuple)) or not self.operations:
            raise ContractError("operations must contain at least one item")
        operations = tuple(self.operations)
        if not all(isinstance(operation, EditOperation) for operation in operations):
            raise ContractError("operations must contain EditOperation values")
        if len({operation.operation_id for operation in operations}) != len(operations):
            raise ContractError("operation_id values must be unique")
        object.__setattr__(self, "operations", operations)
        output = os.path.normcase(os.path.realpath(os.path.abspath(self.output_path)))
        inputs = {
            os.path.normcase(os.path.realpath(os.path.abspath(source)))
            for source in self.sources
        }
        if output in inputs:
            raise ContractError("output_path must not overwrite a source")
        referenced = {operation.source for operation in operations if operation.source is not None}
        unknown = referenced - set(self.sources)
        if unknown:
            raise ContractError("operation sources must be declared in plan sources")
        _schema_version(self.schema_version)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "plan_id": self.plan_id,
            "brief_id": self.brief_id,
            "sources": list(self.sources),
            "output_path": self.output_path,
            "operations": [operation.to_dict() for operation in self.operations],
        }

    def to_json(self, *, indent: int | None = 2) -> str:
        return _to_json(self.to_dict(), indent=indent)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> EditPlan:
        _keys(
            data,
            required={"schema_version", "plan_id", "brief_id", "sources", "output_path", "operations"},
            optional=set(),
        )
        raw_operations = data["operations"]
        if not isinstance(raw_operations, (list, tuple)):
            raise ContractError("operations must be an array")
        return cls(
            schema_version=data["schema_version"],
            plan_id=data["plan_id"],
            brief_id=data["brief_id"],
            sources=data["sources"],
            output_path=data["output_path"],
            operations=tuple(EditOperation.from_dict(item) for item in raw_operations),
        )


@dataclass(frozen=True, slots=True)
class FileFingerprint:
    """A content-addressed local file reference used by a render manifest."""

    path: str
    sha256: str
    file_size_bytes: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", _text(self.path, "path"))
        object.__setattr__(self, "sha256", _sha256(self.sha256, "sha256"))
        if isinstance(self.file_size_bytes, bool) or not isinstance(self.file_size_bytes, int):
            raise ContractError("file_size_bytes must be an integer")
        if self.file_size_bytes <= 0:
            raise ContractError("file_size_bytes must be greater than zero")

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "sha256": self.sha256,
            "file_size_bytes": self.file_size_bytes,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> FileFingerprint:
        _keys(data, required={"path", "sha256", "file_size_bytes"}, optional=set())
        return cls(data["path"], data["sha256"], data["file_size_bytes"])


@dataclass(frozen=True, slots=True)
class ToolRecord:
    """The detected local executable used by a render."""

    name: str
    path: str
    version: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _text(self.name, "name"))
        object.__setattr__(self, "path", _text(self.path, "path"))
        object.__setattr__(self, "version", _optional_text(self.version, "version"))

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "path": self.path, "version": self.version}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ToolRecord:
        _keys(data, required={"name", "path", "version"}, optional=set())
        return cls(data["name"], data["path"], data["version"])


@dataclass(frozen=True, slots=True)
class RenderManifest:
    """A deterministic record of one local workflow execution and its artifact."""

    manifest_id: str
    plan_id: str
    brief_id: str
    workflow: str
    plan_sha256: str
    sources: tuple[FileFingerprint, ...]
    outputs: tuple[FileFingerprint, ...]
    tools: tuple[ToolRecord, ...]
    technical_validation_valid: bool
    technical_validation_issues: tuple[str, ...] = ()
    local_only: bool = True
    editorial_review: str = "not_performed"
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        for name in ("manifest_id", "plan_id", "brief_id", "workflow"):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        object.__setattr__(self, "plan_sha256", _sha256(self.plan_sha256, "plan_sha256"))
        collections = (
            ("sources", FileFingerprint),
            ("outputs", FileFingerprint),
            ("tools", ToolRecord),
        )
        for name, expected_type in collections:
            values = getattr(self, name)
            if not isinstance(values, (list, tuple)) or not values:
                raise ContractError(f"{name} must contain at least one item")
            normalized = tuple(values)
            if not all(isinstance(value, expected_type) for value in normalized):
                raise ContractError(f"{name} contains an invalid item")
            object.__setattr__(self, name, normalized)
        if len({item.path for item in self.sources}) != len(self.sources):
            raise ContractError("source paths must be unique")
        if len({item.path for item in self.outputs}) != len(self.outputs):
            raise ContractError("output paths must be unique")
        if len({item.name for item in self.tools}) != len(self.tools):
            raise ContractError("tool names must be unique")
        source_paths = {
            os.path.normcase(os.path.realpath(os.path.abspath(item.path)))
            for item in self.sources
        }
        output_paths = {
            os.path.normcase(os.path.realpath(os.path.abspath(item.path)))
            for item in self.outputs
        }
        if source_paths & output_paths:
            raise ContractError("manifest outputs must not overwrite sources")
        if not isinstance(self.technical_validation_valid, bool):
            raise ContractError("technical_validation_valid must be a boolean")
        object.__setattr__(
            self,
            "technical_validation_issues",
            _text_tuple(
                self.technical_validation_issues,
                "technical_validation_issues",
                allow_empty=True,
            ),
        )
        if self.technical_validation_valid and self.technical_validation_issues:
            raise ContractError("valid technical validation must not contain issues")
        if not self.technical_validation_valid and not self.technical_validation_issues:
            raise ContractError("invalid technical validation must contain issues")
        if self.local_only is not True:
            raise ContractError("local_only must be true")
        if self.editorial_review != "not_performed":
            raise ContractError("editorial_review must be not_performed in schema v1")
        _schema_version(self.schema_version)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "manifest_id": self.manifest_id,
            "plan_id": self.plan_id,
            "brief_id": self.brief_id,
            "workflow": self.workflow,
            "local_only": self.local_only,
            "editorial_review": self.editorial_review,
            "plan_sha256": self.plan_sha256,
            "sources": [item.to_dict() for item in self.sources],
            "outputs": [item.to_dict() for item in self.outputs],
            "tools": [item.to_dict() for item in self.tools],
            "technical_validation_valid": self.technical_validation_valid,
            "technical_validation_issues": list(self.technical_validation_issues),
        }

    def to_json(self, *, indent: int | None = 2) -> str:
        return _to_json(self.to_dict(), indent=indent)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> RenderManifest:
        required = {
            "schema_version",
            "manifest_id",
            "plan_id",
            "brief_id",
            "workflow",
            "local_only",
            "editorial_review",
            "plan_sha256",
            "sources",
            "outputs",
            "tools",
            "technical_validation_valid",
            "technical_validation_issues",
        }
        _keys(data, required=required, optional=set())
        for name in ("sources", "outputs", "tools", "technical_validation_issues"):
            if not isinstance(data[name], (list, tuple)):
                raise ContractError(f"{name} must be an array")
        return cls(
            schema_version=data["schema_version"],
            manifest_id=data["manifest_id"],
            plan_id=data["plan_id"],
            brief_id=data["brief_id"],
            workflow=data["workflow"],
            local_only=data["local_only"],
            editorial_review=data["editorial_review"],
            plan_sha256=data["plan_sha256"],
            sources=tuple(FileFingerprint.from_dict(item) for item in data["sources"]),
            outputs=tuple(FileFingerprint.from_dict(item) for item in data["outputs"]),
            tools=tuple(ToolRecord.from_dict(item) for item in data["tools"]),
            technical_validation_valid=data["technical_validation_valid"],
            technical_validation_issues=tuple(data["technical_validation_issues"]),
        )
