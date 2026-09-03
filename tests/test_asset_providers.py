"""Tests for the asset provider adapters.

``LocalAssetProvider`` is exercised fully offline against a temp library.
The external providers are exercised with an injected fake HTTP client so
no test here touches the network; a real-network smoke test is skipped
unless the matching API key is present in the environment.
"""

import hashlib
import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from video_generator.adapters.asset_providers import (
    LocalAssetProvider,
    PexelsProvider,
    PixabayProvider,
    ProviderError,
    build_providers,
)


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


class LocalAssetProviderTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name) / "library"
        _write(self.root / "office_desk.jpg", b"fake-jpeg-bytes-office")
        (self.root / "office_desk.jpg.json").write_text(
            json.dumps(
                {
                    "title": "Empty office at dusk",
                    "description": "rows of desks, low light",
                    "tags": ["escritorio", "mesa", "office", "desk"],
                    "license": "CC0-1.0",
                    "license_url": "https://creativecommons.org/publicdomain/zero/1.0/",
                    "author": "A. Photographer",
                    "source_url": "https://example.org/office",
                    "width": 1920,
                    "height": 1080,
                }
            ),
            encoding="utf-8",
        )
        _write(self.root / "sub" / "crowd.jpg", b"fake-jpeg-bytes-crowd-no-sidecar")
        self.addCleanup(self._tmp.cleanup)

    def test_indexes_media_and_reads_sidecar(self):
        provider = LocalAssetProvider(self.root, probe=lambda p: (None, None, None))
        cands = provider.search(("escritorio", "mesa"), media_type="image",
                                orientation="landscape", min_duration_seconds=0.0, limit=10)
        by_id = {c.source_id: c for c in cands}
        office = by_id["office_desk"]
        self.assertEqual(office.license, "CC0-1.0")
        self.assertEqual(office.author, "A. Photographer")
        self.assertEqual(office.width, 1920)
        self.assertEqual(office.media_type, "image")
        self.assertIsNotNone(office.local_path)
        self.assertEqual(office.source_kind, "local_library")

    def test_missing_sidecar_degrades_to_unknown_license(self):
        provider = LocalAssetProvider(self.root, probe=lambda p: (640, 480, None))
        cands = provider.search((), media_type="image", orientation="landscape",
                                min_duration_seconds=0.0, limit=10)
        crowd = next(c for c in cands if c.source_id == "crowd")
        self.assertEqual(crowd.license, "unknown")
        self.assertEqual(crowd.width, 640)

    def test_works_without_any_probe(self):
        provider = LocalAssetProvider(self.root, probe=None)
        cands = provider.search((), media_type="image", orientation="landscape",
                                min_duration_seconds=0.0, limit=10)
        self.assertTrue(cands)  # still indexes, dims simply unknown

    def test_acquire_copies_and_hashes(self):
        provider = LocalAssetProvider(self.root, probe=lambda p: (None, None, None))
        cand = next(c for c in provider.search(("office",), media_type="image",
                    orientation="landscape", min_duration_seconds=0.0, limit=10))
        with TemporaryDirectory() as out:
            dest = Path(out) / "asset_x.jpg"
            acquired = provider.acquire(cand, dest)
            self.assertTrue(dest.exists())
            self.assertEqual(acquired.local_path, str(dest))
            self.assertEqual(
                acquired.sha256,
                hashlib.sha256((self.root / "office_desk.jpg").read_bytes()).hexdigest(),
            )
            self.assertEqual(acquired.original_filename, "office_desk.jpg")

    def test_reacquire_same_bytes_is_idempotent(self):
        provider = LocalAssetProvider(self.root, probe=lambda p: (None, None, None))
        cand = next(c for c in provider.search(("office",), media_type="image",
                    orientation="landscape", min_duration_seconds=0.0, limit=10))
        with TemporaryDirectory() as out:
            dest = Path(out) / "asset_x.jpg"
            first = provider.acquire(cand, dest)
            second = provider.acquire(cand, dest)
            self.assertEqual(first.sha256, second.sha256)

    def test_refuses_to_overwrite_different_bytes(self):
        provider = LocalAssetProvider(self.root, probe=lambda p: (None, None, None))
        cand = next(c for c in provider.search(("office",), media_type="image",
                    orientation="landscape", min_duration_seconds=0.0, limit=10))
        with TemporaryDirectory() as out:
            dest = Path(out) / "asset_x.jpg"
            dest.write_bytes(b"something-else-entirely")
            with self.assertRaises(ProviderError):
                provider.acquire(cand, dest)


class _FakeHttp:
    def __init__(self, responses):
        self._responses = responses
        self.calls = []

    def get_json(self, url, headers=None, params=None):
        self.calls.append(("json", url, params))
        for needle, payload in self._responses.items():
            if needle in url:
                return payload
        raise AssertionError(f"unexpected url {url}")

    def get_bytes(self, url, headers=None):
        self.calls.append(("bytes", url, None))
        return b"downloaded-media-bytes-for-" + url.encode("ascii", "ignore")[:8]


class PexelsProviderTests(unittest.TestCase):
    def test_disabled_without_key(self):
        provider = PexelsProvider(api_key=None)
        self.assertFalse(provider.available)
        with self.assertRaises(ProviderError):
            provider.search(("office",), media_type="image", orientation="landscape",
                            min_duration_seconds=0.0, limit=5)

    def test_search_parses_photos_with_injected_http(self):
        http = _FakeHttp(
            {
                "/v1/search": {
                    "photos": [
                        {
                            "id": 12345,
                            "width": 4000,
                            "height": 2200,
                            "url": "https://www.pexels.com/photo/desk-12345/",
                            "photographer": "Jane Roe",
                            "alt": "wooden desk near window",
                            "src": {"large2x": "https://images.pexels.com/photos/12345/large2x.jpg"},
                        }
                    ]
                }
            }
        )
        provider = PexelsProvider(api_key="test-key", http=http)
        self.assertTrue(provider.available)
        cands = provider.search(("desk", "window"), media_type="image",
                                orientation="landscape", min_duration_seconds=0.0, limit=5)
        self.assertEqual(len(cands), 1)
        cand = cands[0]
        self.assertEqual(cand.source_kind, "pexels")
        self.assertEqual(cand.source_id, "12345")
        self.assertEqual(cand.license, "Pexels")
        self.assertEqual(cand.width, 4000)
        self.assertEqual(cand.remote_locator, "https://images.pexels.com/photos/12345/large2x.jpg")
        self.assertEqual(cand.author, "Jane Roe")

    def test_acquire_downloads_remote_locator(self):
        http = _FakeHttp({})
        provider = PexelsProvider(api_key="k", http=http)
        from video_generator.domain.assets import AssetCandidate

        cand = AssetCandidate(
            candidate_id="pexels:1", source_kind="pexels", source_id="1",
            media_type="image", local_path=None,
            remote_locator="https://images.pexels.com/photos/1/x.jpg",
            title="t", description="", tags=(), width=3000, height=2000,
            duration_seconds=None, license="Pexels",
            license_url="https://www.pexels.com/license/", author="x",
            source_url="https://www.pexels.com/photo/1/", score=0.0, score_breakdown={},
        )
        with TemporaryDirectory() as out:
            dest = Path(out) / "a.jpg"
            acquired = provider.acquire(cand, dest)
            self.assertTrue(dest.exists())
            self.assertEqual(len(acquired.sha256), 64)


class HttpSchemeGuardTests(unittest.TestCase):
    def test_real_http_client_refuses_non_web_schemes(self):
        from video_generator.adapters.asset_providers import _UrllibHttp

        http = _UrllibHttp()
        with self.assertRaises(ProviderError):
            http.get_bytes("file:///etc/passwd")
        with self.assertRaises(ProviderError):
            http.get_json("ftp://example.org/data")


class PixabayProviderTests(unittest.TestCase):
    def test_search_parses_hits(self):
        http = _FakeHttp(
            {
                "pixabay.com/api": {
                    "hits": [
                        {
                            "id": 999,
                            "imageWidth": 3200,
                            "imageHeight": 2100,
                            "pageURL": "https://pixabay.com/photos/office-999/",
                            "user": "pixa_user",
                            "tags": "office, desk, work",
                            "largeImageURL": "https://pixabay.com/get/999_large.jpg",
                        }
                    ]
                }
            }
        )
        provider = PixabayProvider(api_key="key", http=http)
        cands = provider.search(("office",), media_type="image", orientation="landscape",
                                min_duration_seconds=0.0, limit=3)
        self.assertEqual(cands[0].source_kind, "pixabay")
        self.assertEqual(cands[0].license, "Pixabay")
        self.assertIn("office", cands[0].tags)


class BuildProvidersTests(unittest.TestCase):
    def test_local_only_by_default(self):
        with TemporaryDirectory() as lib:
            providers = build_providers(["local"], library_root=Path(lib), env={})
            self.assertEqual([p.source_kind for p in providers], ["local_library"])

    def test_external_provider_omitted_when_key_absent(self):
        with TemporaryDirectory() as lib:
            providers = build_providers(
                ["local", "pexels", "pixabay"], library_root=Path(lib), env={}
            )
            self.assertEqual([p.source_kind for p in providers], ["local_library"])

    def test_external_provider_included_when_key_present(self):
        with TemporaryDirectory() as lib:
            providers = build_providers(
                ["local", "pexels"],
                library_root=Path(lib),
                env={"PEXELS_API_KEY": "abc"},
            )
            self.assertEqual(
                sorted(p.source_kind for p in providers), ["local_library", "pexels"]
            )


@unittest.skipUnless(
    os.environ.get("PEXELS_API_KEY"), "no PEXELS_API_KEY in environment"
)
class PexelsNetworkSmokeTest(unittest.TestCase):
    def test_live_search_returns_candidates(self):
        provider = PexelsProvider(api_key=os.environ["PEXELS_API_KEY"])
        cands = provider.search(("office", "desk"), media_type="image",
                                orientation="landscape", min_duration_seconds=0.0, limit=3)
        self.assertTrue(cands)
        self.assertTrue(all(c.remote_locator for c in cands))


if __name__ == "__main__":
    unittest.main()
