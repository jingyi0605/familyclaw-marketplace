from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import sync_marketplace_versions as sync  # noqa: E402


class SyncMarketplaceVersionsTests(unittest.TestCase):
    def test_collect_candidate_tag_names_skips_existing_tag_refs(self) -> None:
        candidate_tag_names, release_published_at_by_tag = sync.collect_candidate_tag_names(
            existing_versions=[
                {
                    "version": "1.0.0",
                    "git_ref": "refs/tags/v1.0.0",
                    "artifact_type": "source_archive",
                    "min_app_version": "0.1.0",
                }
            ],
            releases=[
                {"tag_name": "v1.0.0", "draft": False, "prerelease": False, "published_at": "2026-03-20T00:00:00Z"},
                {"tag_name": "v1.1.0", "draft": False, "prerelease": False, "published_at": "2026-03-21T00:00:00Z"},
            ],
            tags=[{"name": "v1.0.0"}, {"name": "v1.1.0"}],
        )

        self.assertEqual(candidate_tag_names, ["v1.1.0"])
        self.assertEqual(release_published_at_by_tag["v1.1.0"], "2026-03-21T00:00:00Z")

    def test_merge_versions_adds_new_tag_version_and_updates_latest(self) -> None:
        merged_versions, changed, removed_branch_fallback_versions = sync.merge_versions(
            existing_versions=[
                {
                    "version": "1.0.0",
                    "git_ref": "refs/tags/v1.0.0",
                    "artifact_type": "source_archive",
                    "min_app_version": "0.1.0",
                }
            ],
            discovered_versions=[
                {
                    "version": "1.1.0",
                    "git_ref": "refs/tags/v1.1.0",
                    "artifact_type": "source_archive",
                    "artifact_url": "https://example.com/archive/refs/tags/v1.1.0.zip",
                    "published_at": "2026-03-21T00:00:00Z",
                    "min_app_version": "0.2.0",
                }
            ],
        )

        self.assertTrue(changed)
        self.assertEqual(removed_branch_fallback_versions, [])
        self.assertEqual([item["version"] for item in merged_versions], ["1.1.0", "1.0.0"])
        self.assertEqual(merged_versions[0]["min_app_version"], "0.2.0")

    def test_merge_versions_replaces_branch_record_with_tag_record_for_same_version(self) -> None:
        merged_versions, changed, removed_branch_fallback_versions = sync.merge_versions(
            existing_versions=[
                {
                    "version": "1.0.0",
                    "git_ref": "main",
                    "artifact_type": "source_archive",
                    "checksum": "sha256:demo",
                    "min_app_version": "0.1.0",
                }
            ],
            discovered_versions=[
                {
                    "version": "1.0.0",
                    "git_ref": "refs/tags/v1.0.0",
                    "artifact_type": "source_archive",
                    "artifact_url": "https://example.com/archive/refs/tags/v1.0.0.zip",
                    "published_at": "2026-03-20T00:00:00Z",
                    "min_app_version": "0.1.0",
                }
            ],
        )

        self.assertTrue(changed)
        self.assertEqual(removed_branch_fallback_versions, [])
        self.assertEqual(len(merged_versions), 1)
        self.assertEqual(merged_versions[0]["git_ref"], "refs/tags/v1.0.0")
        self.assertEqual(merged_versions[0]["checksum"], "sha256:demo")

    def test_merge_versions_drops_branch_fallback_when_formal_tag_versions_exist(self) -> None:
        merged_versions, changed, removed_branch_fallback_versions = sync.merge_versions(
            existing_versions=[
                {
                    "version": "0.1.0",
                    "git_ref": "main",
                    "artifact_type": "source_archive",
                    "min_app_version": "0.1.0",
                }
            ],
            discovered_versions=[
                {
                    "version": "0.1.1",
                    "git_ref": "refs/tags/v0.1.1",
                    "artifact_type": "source_archive",
                    "artifact_url": "https://example.com/archive/refs/tags/v0.1.1.zip",
                    "min_app_version": "0.1.0",
                }
            ],
        )

        self.assertTrue(changed)
        self.assertEqual(removed_branch_fallback_versions, ["0.1.0"])
        self.assertEqual([item["version"] for item in merged_versions], ["0.1.1"])


if __name__ == "__main__":
    unittest.main()
