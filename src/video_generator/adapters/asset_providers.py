"""Asset providers: the acquisition boundary for the Asset Resolver.

A provider turns a set of sanitised query terms into
:class:`~video_generator.domain.assets.AssetCandidate` values and, on request,
acquires exactly the chosen one into a local file.

- ``LocalAssetProvider`` indexes a local library plus optional JSON sidecars and
  is fully offline — the whole resolution pipeline can be tested without a
  network.
- ``PexelsProvider`` / ``PixabayProvider`` talk to free, commercial-use image
  APIs that need a free API key (read from the environment). They are isolated
  here: importing this module needs no key, constructing a provider without one
  yields a disabled provider, and the domain never imports this file.

No paid API, no scraping, no watermark removal, no arbitrary video download.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Protocol, Sequence, runtime_checkable

from video_generator.domain.assets import AssetCandidate

IMAGE_EXTENSIONS = frozenset(
    {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
)
VIDEO_EXTENSIONS = frozenset(
    {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi"}
)

_SIDECAR_KEYS = frozenset(
    {
        "title", "description", "tags", "license", "license_url", "author",
        "source_url", "width", "height", "duration_seconds",
    }
)


class ProviderError(RuntimeError):
    """Raised when a provider cannot search or cannot acquire a chosen asset."""


@dataclass(frozen=True, slots=True)
class AcquiredFile:
    local_path: str
    original_filename: str
    sha256: str
    bytes_written: int


# Probe signature: path -> (width|None, height|None, duration_seconds|None)
Probe = Callable[[Path], "tuple[int | None, int | None, float | None]"]


@runtime_checkable
class AssetProvider(Protocol):
    source_kind: str

    def search(
        self,
        terms: Sequence[str],
        *,
        media_type: str,
        orientation: str,
        min_duration_seconds: float,
        limit: int,
    ) -> list[AssetCandidate]: ...

    def acquire(self, candidate: AssetCandidate, dest_path: Path | str) -> AcquiredFile: ...


def _sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _media_type_for(path: Path) -> str | None:
    ext = path.suffix.lower()
    if ext in IMAGE_EXTENSIONS:
        return "image"
    if ext in VIDEO_EXTENSIONS:
        return "video"
    return None


def _copy_without_clobber(source: Path, dest: Path) -> AcquiredFile:
    dest.parent.mkdir(parents=True, exist_ok=True)
    source_hash = _sha256_of(source)
    if dest.exists():
        if _sha256_of(dest) != source_hash:
            raise ProviderError(
                f"refusing to overwrite {dest} with different bytes from {source}"
            )
        return AcquiredFile(
            local_path=str(dest),
            original_filename=source.name,
            sha256=source_hash,
            bytes_written=dest.stat().st_size,
        )
    shutil.copy2(source, dest)
    return AcquiredFile(
        local_path=str(dest),
        original_filename=source.name,
        sha256=source_hash,
        bytes_written=dest.stat().st_size,
    )


def _write_bytes_without_clobber(data: bytes, dest: Path, original_filename: str) -> AcquiredFile:
    dest.parent.mkdir(parents=True, exist_ok=True)
    new_hash = hashlib.sha256(data).hexdigest()
    if dest.exists():
        if _sha256_of(dest) != new_hash:
            raise ProviderError(
                f"refusing to overwrite {dest} with different downloaded bytes"
            )
        return AcquiredFile(
            local_path=str(dest),
            original_filename=original_filename,
            sha256=new_hash,
            bytes_written=dest.stat().st_size,
        )
    dest.write_bytes(data)
    return AcquiredFile(
        local_path=str(dest),
        original_filename=original_filename,
        sha256=new_hash,
        bytes_written=len(data),
    )


# --------------------------------------------------------------------------- #
# LocalAssetProvider
# --------------------------------------------------------------------------- #
def _default_probe() -> Probe:
    """A best-effort probe backed by the ffprobe adapter; if ffprobe is not
    installed the resolver simply loses dimension/duration signals."""

    def probe(path: Path) -> "tuple[int | None, int | None, float | None]":
        try:
            from video_generator.adapters.ffprobe import ProbeError as _PE
            from video_generator.adapters.ffprobe import probe_media
        except Exception:  # pragma: no cover - import guard
            return (None, None, None)
        try:
            result = probe_media(path)
        except Exception:  # pragma: no cover - ffprobe missing / unreadable
            return (None, None, None)
        width = height = None
        for stream in result.streams:
            if stream.width and stream.height:
                width, height = stream.width, stream.height
                break
        return (width, height, result.duration_seconds)

    return probe


class LocalAssetProvider:
    """Index a local asset library (``assets/library/**``) plus optional
    ``<file>.json`` sidecars. Fully offline."""

    def __init__(
        self,
        library_root: Path | str,
        *,
        source_kind: str = "local_library",
        probe: Probe | None = "__default__",  # type: ignore[assignment]
    ) -> None:
        self.source_kind = source_kind
        self.available = True
        self._root = Path(library_root)
        if probe == "__default__":
            probe = _default_probe()
        self._probe = probe
        self._index: list[AssetCandidate] | None = None

    # -- indexing --------------------------------------------------------- #
    def _sidecar_for(self, media_path: Path) -> dict[str, Any]:
        for candidate in (
            media_path.with_suffix(media_path.suffix + ".json"),
            media_path.with_suffix(".json"),
        ):
            if candidate.is_file():
                try:
                    payload = json.loads(candidate.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError) as exc:
                    raise ProviderError(f"bad sidecar {candidate}: {exc}") from exc
                if not isinstance(payload, Mapping):
                    raise ProviderError(f"sidecar {candidate} must be a JSON object")
                unknown = set(payload) - _SIDECAR_KEYS
                if unknown:
                    raise ProviderError(
                        f"sidecar {candidate} has unknown keys: {', '.join(sorted(unknown))}"
                    )
                return dict(payload)
        return {}

    def _build_index(self) -> list[AssetCandidate]:
        if not self._root.is_dir():
            return []
        out: list[AssetCandidate] = []
        for path in sorted(self._root.rglob("*")):
            if not path.is_file():
                continue
            media_type = _media_type_for(path)
            if media_type is None:
                continue
            sidecar = self._sidecar_for(path)
            width = sidecar.get("width")
            height = sidecar.get("height")
            duration = sidecar.get("duration_seconds")
            if (width is None or height is None or duration is None) and self._probe:
                p_width, p_height, p_duration = self._probe(path)
                width = width if width is not None else p_width
                height = height if height is not None else p_height
                duration = duration if duration is not None else p_duration
            rel = path.relative_to(self._root).as_posix()
            tags = tuple(str(t) for t in sidecar.get("tags", ()) if str(t).strip())
            out.append(
                AssetCandidate(
                    candidate_id=f"{self.source_kind}:{rel}",
                    source_kind=self.source_kind,
                    source_id=path.stem,
                    media_type=media_type,
                    local_path=str(path),
                    remote_locator=None,
                    title=str(sidecar.get("title") or path.stem.replace("_", " ")),
                    description=str(sidecar.get("description") or ""),
                    tags=tags,
                    width=width,
                    height=height,
                    duration_seconds=duration if media_type == "video" else None,
                    license=str(sidecar.get("license") or "unknown"),
                    license_url=sidecar.get("license_url"),
                    author=sidecar.get("author"),
                    source_url=sidecar.get("source_url"),
                    score=0.0,
                    score_breakdown={},
                )
            )
        return out

    def _catalogue(self) -> list[AssetCandidate]:
        if self._index is None:
            self._index = self._build_index()
        return self._index

    # -- AssetProvider -------------------------------------------------- #
    def search(
        self,
        terms: Sequence[str],
        *,
        media_type: str,
        orientation: str,
        min_duration_seconds: float,
        limit: int,
    ) -> list[AssetCandidate]:
        term_set = {t.lower() for t in terms}
        hits: list[AssetCandidate] = []
        for candidate in self._catalogue():
            if candidate.media_type != media_type:
                continue
            if term_set and not (term_set & candidate.metadata_bag()):
                # keep it only if nothing else matches; recorded after the loop
                continue
            hits.append(candidate)
        if not hits and term_set:
            hits = [c for c in self._catalogue() if c.media_type == media_type]
        return hits[:limit]

    def acquire(self, candidate: AssetCandidate, dest_path: Path | str) -> AcquiredFile:
        if not candidate.local_path:
            raise ProviderError("local candidate has no local_path")
        source = Path(candidate.local_path)
        if not source.is_file():
            raise ProviderError(f"local candidate file is gone: {source}")
        return _copy_without_clobber(source, Path(dest_path))


# --------------------------------------------------------------------------- #
# HTTP helper (stdlib only) + external providers
# --------------------------------------------------------------------------- #
def _require_web_url(url: str) -> str:
    scheme = urllib.parse.urlparse(url).scheme.lower()
    if scheme not in ("http", "https"):
        raise ProviderError(f"refusing a non-http(s) URL: {url!r}")
    return url


class _UrllibHttp:
    def __init__(self, timeout_seconds: float = 20.0) -> None:
        self._timeout = timeout_seconds

    def get_json(self, url: str, headers: Mapping[str, str] | None = None,
                 params: Mapping[str, Any] | None = None) -> Any:
        _require_web_url(url)
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        request = urllib.request.Request(url, headers=dict(headers or {}))
        with urllib.request.urlopen(request, timeout=self._timeout) as response:  # noqa: S310
            return json.loads(response.read().decode("utf-8"))

    def get_bytes(self, url: str, headers: Mapping[str, str] | None = None) -> bytes:
        _require_web_url(url)
        request = urllib.request.Request(url, headers=dict(headers or {}))
        with urllib.request.urlopen(request, timeout=self._timeout) as response:  # noqa: S310
            return response.read()


def _orientation_param(orientation: str) -> str:
    return {"landscape": "landscape", "portrait": "portrait", "square": "square"}.get(
        orientation, "landscape"
    )


class PexelsProvider:
    source_kind = "pexels"
    _PHOTO_API = "https://api.pexels.com/v1/search"
    _VIDEO_API = "https://api.pexels.com/videos/search"
    LICENSE = "Pexels"
    LICENSE_URL = "https://www.pexels.com/license/"

    def __init__(self, api_key: str | None = None, *, http: Any | None = None) -> None:
        self._api_key = (api_key or "").strip()
        self.available = bool(self._api_key)
        self._http = http or _UrllibHttp()

    def _require(self) -> None:
        if not self.available:
            raise ProviderError("pexels provider is disabled: no PEXELS_API_KEY")

    def search(self, terms, *, media_type, orientation, min_duration_seconds, limit):
        self._require()
        query = " ".join(terms) or "abstract"
        headers = {"Authorization": self._api_key}
        params = {
            "query": query,
            "per_page": max(1, min(limit, 80)),
            "orientation": _orientation_param(orientation),
        }
        if media_type == "video":
            payload = self._http.get_json(self._VIDEO_API, headers=headers, params=params)
            return self._parse_videos(payload, query)
        payload = self._http.get_json(self._PHOTO_API, headers=headers, params=params)
        return self._parse_photos(payload, query)

    def _parse_photos(self, payload: Mapping[str, Any], query: str) -> list[AssetCandidate]:
        out: list[AssetCandidate] = []
        for photo in payload.get("photos", []) or []:
            src = photo.get("src", {}) or {}
            locator = src.get("large2x") or src.get("large") or src.get("original")
            if not locator:
                continue
            out.append(
                AssetCandidate(
                    candidate_id=f"pexels:{photo.get('id')}",
                    source_kind="pexels",
                    source_id=str(photo.get("id")),
                    media_type="image",
                    local_path=None,
                    remote_locator=locator,
                    title=str(photo.get("alt") or query),
                    description=str(photo.get("alt") or ""),
                    tags=tuple(query.split()),
                    width=photo.get("width"),
                    height=photo.get("height"),
                    duration_seconds=None,
                    license=self.LICENSE,
                    license_url=self.LICENSE_URL,
                    author=photo.get("photographer"),
                    source_url=photo.get("url"),
                    score=0.0,
                    score_breakdown={},
                )
            )
        return out

    def _parse_videos(self, payload: Mapping[str, Any], query: str) -> list[AssetCandidate]:
        out: list[AssetCandidate] = []
        for video in payload.get("videos", []) or []:
            files = sorted(
                (f for f in video.get("video_files", []) if f.get("link")),
                key=lambda f: (f.get("width") or 0),
                reverse=True,
            )
            if not files:
                continue
            best = files[0]
            out.append(
                AssetCandidate(
                    candidate_id=f"pexels:{video.get('id')}",
                    source_kind="pexels",
                    source_id=str(video.get("id")),
                    media_type="video",
                    local_path=None,
                    remote_locator=best.get("link"),
                    title=str(video.get("url") or query),
                    description="",
                    tags=tuple(query.split()),
                    width=best.get("width") or video.get("width"),
                    height=best.get("height") or video.get("height"),
                    duration_seconds=float(video.get("duration") or 0.0) or None,
                    license=self.LICENSE,
                    license_url=self.LICENSE_URL,
                    author=(video.get("user") or {}).get("name"),
                    source_url=video.get("url"),
                    score=0.0,
                    score_breakdown={},
                )
            )
        return out

    def acquire(self, candidate: AssetCandidate, dest_path: Path | str) -> AcquiredFile:
        self._require()
        if not candidate.remote_locator:
            raise ProviderError("pexels candidate has no remote_locator")
        data = self._http.get_bytes(candidate.remote_locator)
        name = Path(urllib.parse.urlparse(candidate.remote_locator).path).name or (
            f"{candidate.source_id}.jpg"
        )
        return _write_bytes_without_clobber(data, Path(dest_path), name)


class PixabayProvider:
    source_kind = "pixabay"
    _API = "https://pixabay.com/api/"
    _VIDEO_API = "https://pixabay.com/api/videos/"
    LICENSE = "Pixabay"
    LICENSE_URL = "https://pixabay.com/service/license-summary/"

    def __init__(self, api_key: str | None = None, *, http: Any | None = None) -> None:
        self._api_key = (api_key or "").strip()
        self.available = bool(self._api_key)
        self._http = http or _UrllibHttp()

    def _require(self) -> None:
        if not self.available:
            raise ProviderError("pixabay provider is disabled: no PIXABAY_API_KEY")

    def search(self, terms, *, media_type, orientation, min_duration_seconds, limit):
        self._require()
        query = " ".join(terms) or "abstract"
        params = {
            "key": self._api_key,
            "q": query,
            "per_page": max(3, min(limit, 200)),
            "safesearch": "true",
        }
        if media_type == "video":
            payload = self._http.get_json(self._VIDEO_API, params=params)
            return self._parse_videos(payload, query)
        params["image_type"] = "photo"
        params["orientation"] = "horizontal" if orientation == "landscape" else "vertical"
        payload = self._http.get_json(self._API, params=params)
        return self._parse_hits(payload, query)

    def _parse_hits(self, payload: Mapping[str, Any], query: str) -> list[AssetCandidate]:
        out: list[AssetCandidate] = []
        for hit in payload.get("hits", []) or []:
            locator = hit.get("largeImageURL") or hit.get("webformatURL")
            if not locator:
                continue
            tags = tuple(t.strip() for t in str(hit.get("tags", "")).split(",") if t.strip())
            out.append(
                AssetCandidate(
                    candidate_id=f"pixabay:{hit.get('id')}",
                    source_kind="pixabay",
                    source_id=str(hit.get("id")),
                    media_type="image",
                    local_path=None,
                    remote_locator=locator,
                    title=str(tags[0] if tags else query),
                    description=str(hit.get("tags") or ""),
                    tags=tags or tuple(query.split()),
                    width=hit.get("imageWidth"),
                    height=hit.get("imageHeight"),
                    duration_seconds=None,
                    license=self.LICENSE,
                    license_url=self.LICENSE_URL,
                    author=hit.get("user"),
                    source_url=hit.get("pageURL"),
                    score=0.0,
                    score_breakdown={},
                )
            )
        return out

    def _parse_videos(self, payload: Mapping[str, Any], query: str) -> list[AssetCandidate]:
        out: list[AssetCandidate] = []
        for hit in payload.get("hits", []) or []:
            streams = hit.get("videos", {}) or {}
            best = streams.get("large") or streams.get("medium") or streams.get("small")
            if not best or not best.get("url"):
                continue
            tags = tuple(t.strip() for t in str(hit.get("tags", "")).split(",") if t.strip())
            out.append(
                AssetCandidate(
                    candidate_id=f"pixabay:{hit.get('id')}",
                    source_kind="pixabay",
                    source_id=str(hit.get("id")),
                    media_type="video",
                    local_path=None,
                    remote_locator=best.get("url"),
                    title=str(tags[0] if tags else query),
                    description=str(hit.get("tags") or ""),
                    tags=tags or tuple(query.split()),
                    width=best.get("width"),
                    height=best.get("height"),
                    duration_seconds=float(hit.get("duration") or 0.0) or None,
                    license=self.LICENSE,
                    license_url=self.LICENSE_URL,
                    author=hit.get("user"),
                    source_url=hit.get("pageURL"),
                    score=0.0,
                    score_breakdown={},
                )
            )
        return out

    def acquire(self, candidate: AssetCandidate, dest_path: Path | str) -> AcquiredFile:
        self._require()
        if not candidate.remote_locator:
            raise ProviderError("pixabay candidate has no remote_locator")
        data = self._http.get_bytes(candidate.remote_locator)
        name = Path(urllib.parse.urlparse(candidate.remote_locator).path).name or (
            f"{candidate.source_id}.jpg"
        )
        return _write_bytes_without_clobber(data, Path(dest_path), name)


_EXTERNAL_BUILDERS: dict[str, "tuple[str, Callable[[str], Any]]"] = {
    "pexels": ("PEXELS_API_KEY", lambda key: PexelsProvider(api_key=key)),
    "pixabay": ("PIXABAY_API_KEY", lambda key: PixabayProvider(api_key=key)),
}


def build_providers(
    names: Iterable[str],
    *,
    library_root: Path | str,
    env: Mapping[str, str] | None = None,
    probe: Probe | None = "__default__",  # type: ignore[assignment]
) -> list[AssetProvider]:
    """Instantiate the requested providers. ``local`` is always available; an
    external provider is included only when its free API key is in ``env`` — a
    missing key silently drops that provider rather than failing the run."""

    import os

    env = env if env is not None else os.environ
    out: list[AssetProvider] = []
    seen: set[str] = set()
    for raw in names:
        name = raw.strip().lower()
        if not name or name in seen:
            continue
        seen.add(name)
        if name in ("local", "local_library"):
            out.append(LocalAssetProvider(library_root, probe=probe))
        elif name in _EXTERNAL_BUILDERS:
            env_key, builder = _EXTERNAL_BUILDERS[name]
            key = env.get(env_key, "").strip()
            if key:
                out.append(builder(key))
        else:
            raise ProviderError(f"unknown provider: {raw}")
    return out
