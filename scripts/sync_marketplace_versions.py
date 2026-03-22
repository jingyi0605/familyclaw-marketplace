from __future__ import annotations

import argparse
import json
import os
from functools import cmp_to_key
from pathlib import Path
from typing import Any

from marketplace_submission_lib import (
    STATUS_FAILED,
    STATUS_PASSED,
    AutomationSystemError,
    ValidationError,
    build_raw_file_url,
    build_source_archive_artifact_url,
    build_version_manifest_overrides,
    collect_version_tag_names,
    compare_version_text,
    dump_json_file,
    fetch_repo_releases,
    fetch_repo_tags,
    is_tag_git_ref,
    load_json_file,
    normalize_tag_git_ref,
    normalize_text,
    parse_github_repo_url,
    request_text,
    validate_generated_entry,
    write_github_output,
)

STATUS_NO_CHANGES = "no_changes"
STATUS_UPDATED = "updated"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="定时扫描已收录插件仓库的 release/tag，并更新市场条目。")
    parser.add_argument("--repository-root", required=True, help="市场仓库根目录。")
    parser.add_argument("--output", required=True, help="扫描结果 JSON 输出路径。")
    return parser.parse_args()


def _load_remote_manifest_for_git_ref(
    *,
    owner: str,
    repo: str,
    git_ref: str,
    manifest_path: str,
    token: str | None,
) -> dict[str, Any]:
    try:
        return json.loads(
            request_text(
                build_raw_file_url(owner, repo, git_ref, manifest_path),
                token=token,
            )
        )
    except ValidationError as exc:
        if exc.error_code == "plugin_repo_unreachable":
            raise ValidationError(
                f"找不到 {git_ref} 对应的 manifest.json。",
                error_code="manifest_invalid",
                field="manifest_path",
            ) from exc
        raise
    except json.JSONDecodeError as exc:
        raise ValidationError(
            f"{git_ref} 对应的 manifest.json 不是合法 JSON。",
            error_code="manifest_invalid",
            field="manifest_path",
        ) from exc


def load_marketplace_entries(repository_root: Path) -> list[Path]:
    return sorted((repository_root / "plugins").glob("*/entry.json"))


def sort_versions_desc(versions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        versions,
        key=cmp_to_key(lambda left, right: compare_version_text(str(left["version"]), str(right["version"]))),
        reverse=True,
    )


def collect_candidate_tag_names(
    *,
    existing_versions: list[dict[str, Any]],
    releases: list[dict[str, Any]],
    tags: list[dict[str, Any]],
) -> tuple[list[str], dict[str, str | None]]:
    ordered_tag_names, release_published_at_by_tag = collect_version_tag_names(releases=releases, tags=tags)
    existing_by_git_ref = {
        normalize_text(str(item.get("git_ref") or "")): item
        for item in existing_versions
        if isinstance(item, dict)
    }
    candidate_tag_names: list[str] = []
    for tag_name in ordered_tag_names:
        git_ref = normalize_tag_git_ref(tag_name)
        existing = existing_by_git_ref.get(git_ref)
        if existing is None:
            candidate_tag_names.append(tag_name)
            continue
        if not normalize_text(str(existing.get("min_app_version") or "")):
            candidate_tag_names.append(tag_name)
    return candidate_tag_names, release_published_at_by_tag


def build_discovered_version_items(
    *,
    source_repo_url: str,
    candidate_tag_names: list[str],
    release_published_at_by_tag: dict[str, str | None],
    owner: str,
    repo: str,
    manifest_path: str,
    token: str | None,
) -> list[dict[str, Any]]:
    if not candidate_tag_names:
        return []

    overrides = build_version_manifest_overrides(
        tag_names=candidate_tag_names,
        release_published_at_by_tag=release_published_at_by_tag,
        load_manifest_for_git_ref=lambda git_ref: _load_remote_manifest_for_git_ref(
            owner=owner,
            repo=repo,
            git_ref=git_ref,
            manifest_path=manifest_path,
            token=token,
        ),
    )
    discovered_items: list[dict[str, Any]] = []
    for tag_name in candidate_tag_names:
        git_ref = normalize_tag_git_ref(tag_name)
        override = overrides.get(git_ref)
        if not override:
            continue
        item: dict[str, Any] = {
            "version": normalize_text(str(override.get("version") or "")),
            "git_ref": git_ref,
            "artifact_type": "source_archive",
            "artifact_url": build_source_archive_artifact_url(source_repo_url, git_ref),
            "min_app_version": normalize_text(str(override.get("min_app_version") or "")),
        }
        published_at = normalize_text(str(override.get("published_at") or ""))
        if published_at:
            item["published_at"] = published_at
        discovered_items.append(item)
    return discovered_items


def merge_versions(
    *,
    existing_versions: list[dict[str, Any]],
    discovered_versions: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], bool, list[str]]:
    working = [dict(item) for item in existing_versions]
    version_to_index = {
        normalize_text(str(item.get("version") or "")): index
        for index, item in enumerate(working)
        if isinstance(item, dict)
    }
    changed = False
    removed_branch_fallback_versions: list[str] = []

    for discovered in discovered_versions:
        version_text = normalize_text(str(discovered.get("version") or ""))
        if not version_text:
            continue
        existing_index = version_to_index.get(version_text)
        if existing_index is None:
            working.append(dict(discovered))
            version_to_index[version_text] = len(working) - 1
            changed = True
            continue

        merged = dict(working[existing_index])
        for key, value in discovered.items():
            merged[key] = value
        if merged != working[existing_index]:
            working[existing_index] = merged
            changed = True

    if discovered_versions and any(is_tag_git_ref(str(item.get("git_ref") or "")) for item in working):
        kept_versions: list[dict[str, Any]] = []
        for item in working:
            git_ref = normalize_text(str(item.get("git_ref") or ""))
            version_text = normalize_text(str(item.get("version") or ""))
            if is_tag_git_ref(git_ref):
                kept_versions.append(item)
                continue
            if version_text:
                removed_branch_fallback_versions.append(version_text)
            changed = True
        working = kept_versions

    sorted_versions = sort_versions_desc(working)
    if sorted_versions != working:
        changed = True
    return sorted_versions, changed, removed_branch_fallback_versions


def sync_entry(
    *,
    entry_path: Path,
    token: str | None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    entry = load_json_file(str(entry_path))
    if not isinstance(entry, dict):
        raise ValidationError(
            f"{entry_path.name} 不是合法对象。",
            error_code="entry_generation_failed",
            field="entry",
        )

    versions = entry.get("versions")
    if not isinstance(versions, list) or not versions:
        raise ValidationError(
            f"{entry_path.name} 缺少有效的 versions。",
            error_code="entry_generation_failed",
            field="versions",
        )

    source_repo = normalize_text(str(entry.get("source_repo") or ""))
    manifest_path = normalize_text(str(entry.get("manifest_path") or ""))
    if not source_repo or not manifest_path:
        raise ValidationError(
            f"{entry_path.name} 缺少 source_repo 或 manifest_path。",
            error_code="entry_generation_failed",
            field="entry",
        )

    repo_info = parse_github_repo_url(source_repo)
    releases = fetch_repo_releases(repo_info["owner"], repo_info["repo"], token=token)
    tags = fetch_repo_tags(repo_info["owner"], repo_info["repo"], token=token)
    candidate_tag_names, release_published_at_by_tag = collect_candidate_tag_names(
        existing_versions=versions,
        releases=releases,
        tags=tags,
    )
    discovered_versions = build_discovered_version_items(
        source_repo_url=repo_info["html_url"],
        candidate_tag_names=candidate_tag_names,
        release_published_at_by_tag=release_published_at_by_tag,
        owner=repo_info["owner"],
        repo=repo_info["repo"],
        manifest_path=manifest_path,
        token=token,
    )
    merged_versions, versions_changed, removed_branch_fallback_versions = merge_versions(
        existing_versions=[item for item in versions if isinstance(item, dict)],
        discovered_versions=discovered_versions,
    )

    latest_version = normalize_text(str(entry.get("latest_version") or ""))
    recalculated_latest_version = normalize_text(str(merged_versions[0].get("version") or ""))
    entry_changed = versions_changed or latest_version != recalculated_latest_version
    if not entry_changed:
        return None, {
            "plugin_id": normalize_text(str(entry.get("plugin_id") or entry_path.parent.name)),
            "entry_path": str(entry_path),
            "status": STATUS_NO_CHANGES,
            "latest_version_before": latest_version,
            "latest_version_after": latest_version,
            "added_versions": [],
            "normalized_versions": [],
            "removed_branch_fallback_versions": [],
        }

    old_versions_by_version = {
        normalize_text(str(item.get("version") or "")): item
        for item in versions
        if isinstance(item, dict)
    }
    added_versions: list[str] = []
    normalized_versions: list[str] = []
    for item in discovered_versions:
        version_text = normalize_text(str(item.get("version") or ""))
        if not version_text:
            continue
        old_item = old_versions_by_version.get(version_text)
        if old_item is None:
            added_versions.append(version_text)
            continue
        old_git_ref = normalize_text(str(old_item.get("git_ref") or ""))
        new_git_ref = normalize_text(str(item.get("git_ref") or ""))
        if old_git_ref != new_git_ref:
            normalized_versions.append(version_text)

    updated_entry = dict(entry)
    updated_entry["versions"] = merged_versions
    updated_entry["latest_version"] = recalculated_latest_version
    entry_errors = validate_generated_entry(updated_entry)
    if entry_errors:
        first_error = entry_errors[0]
        raise ValidationError(
            f"{entry_path.name} 生成后的条目校验失败：{first_error.get('detail')}",
            error_code=str(first_error.get("error_code") or "entry_generation_failed"),
            field=str(first_error.get("field") or "entry"),
        )
    return updated_entry, {
        "plugin_id": normalize_text(str(updated_entry.get("plugin_id") or entry_path.parent.name)),
        "entry_path": str(entry_path),
        "status": STATUS_UPDATED,
        "latest_version_before": latest_version,
        "latest_version_after": recalculated_latest_version,
        "added_versions": added_versions,
        "normalized_versions": normalized_versions,
        "removed_branch_fallback_versions": removed_branch_fallback_versions,
    }


def build_report_markdown(
    *,
    updated_plugins: list[dict[str, Any]],
    unchanged_plugins: list[dict[str, Any]],
) -> str:
    if not updated_plugins:
        return "\n".join(
            [
                "## 定时扫描结果",
                "",
                "- 本轮没有发现需要更新的插件版本。",
                f"- 已检查条目数：{len(unchanged_plugins)}",
            ]
        )

    lines = [
        "## 定时扫描结果",
        "",
        f"- 本轮更新插件数：{len(updated_plugins)}",
        f"- 本轮无变化插件数：{len(unchanged_plugins)}",
        "",
        "### 已更新插件",
        "",
    ]
    for item in updated_plugins:
        plugin_id = item["plugin_id"]
        latest_before = item["latest_version_before"] or "未知"
        latest_after = item["latest_version_after"] or "未知"
        details: list[str] = []
        if item["added_versions"]:
            details.append(f"新增版本 {', '.join(item['added_versions'])}")
        if item["normalized_versions"]:
            details.append(f"把版本 {', '.join(item['normalized_versions'])} 从 branch 记录收口为 tag 记录")
        if item["removed_branch_fallback_versions"]:
            details.append(f"移除了 branch 兜底版本 {', '.join(item['removed_branch_fallback_versions'])}")
        if not details and latest_before != latest_after:
            details.append("重新校正了 latest_version")
        lines.append(f"- `{plugin_id}`：{'; '.join(details) or '版本信息已更新'}；latest_version {latest_before} -> {latest_after}")
    return "\n".join(lines)


def build_pr_body_fragment(updated_plugins: list[dict[str, Any]]) -> str:
    if not updated_plugins:
        return "- 本轮没有发现版本变化。"
    lines = []
    for item in updated_plugins:
        plugin_id = item["plugin_id"]
        details: list[str] = []
        if item["added_versions"]:
            details.append(f"新增版本 {', '.join(item['added_versions'])}")
        if item["normalized_versions"]:
            details.append(f"规范化版本 {', '.join(item['normalized_versions'])} 的 tag 引用")
        if item["removed_branch_fallback_versions"]:
            details.append(f"移除 branch 兜底版本 {', '.join(item['removed_branch_fallback_versions'])}")
        if item["latest_version_before"] != item["latest_version_after"]:
            details.append(f"latest_version {item['latest_version_before']} -> {item['latest_version_after']}")
        lines.append(f"- `{plugin_id}`：{'；'.join(details) or '版本信息已更新'}")
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    repository_root = Path(args.repository_root).resolve()
    token = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")

    updated_plugins: list[dict[str, Any]] = []
    unchanged_plugins: list[dict[str, Any]] = []
    failed_plugins: list[dict[str, str]] = []
    changed_paths: list[str] = []

    for entry_path in load_marketplace_entries(repository_root):
        try:
            updated_entry, sync_result = sync_entry(entry_path=entry_path, token=token)
            if sync_result is None:
                continue
            if sync_result["status"] == STATUS_UPDATED and updated_entry is not None:
                dump_json_file(str(entry_path), updated_entry)
                updated_plugins.append(sync_result)
                changed_paths.append(str(entry_path.relative_to(repository_root)).replace("\\", "/"))
            else:
                unchanged_plugins.append(sync_result)
        except (ValidationError, AutomationSystemError) as exc:
            failed_plugins.append(
                {
                    "entry_path": str(entry_path.relative_to(repository_root)).replace("\\", "/"),
                    "detail": getattr(exc, "detail", str(exc)),
                }
            )

    if failed_plugins:
        report_markdown = "\n".join(
            [
                "## 定时扫描失败",
                "",
                f"- 失败条目数：{len(failed_plugins)}",
                "",
                "### 失败条目",
                "",
                *[f"- `{item['entry_path']}`：{item['detail']}" for item in failed_plugins],
            ]
        )
        result = {
            "status": STATUS_FAILED,
            "updated_plugins": updated_plugins,
            "unchanged_plugins": unchanged_plugins,
            "failed_plugins": failed_plugins,
            "changed_paths": changed_paths,
            "report_markdown": report_markdown,
        }
        dump_json_file(args.output, result)
        write_github_output(
            os.getenv("GITHUB_OUTPUT"),
            {
                "status": STATUS_FAILED,
                "changed_paths": "\n".join(changed_paths),
                "updated_plugins": ",".join(item["plugin_id"] for item in updated_plugins),
                "report_markdown": report_markdown,
                "pr_body_fragment": build_pr_body_fragment(updated_plugins),
            },
        )
        print(report_markdown)
        return 1

    status = STATUS_UPDATED if updated_plugins else STATUS_NO_CHANGES
    report_markdown = build_report_markdown(
        updated_plugins=updated_plugins,
        unchanged_plugins=unchanged_plugins,
    )
    result = {
        "status": status,
        "updated_plugins": updated_plugins,
        "unchanged_plugins": unchanged_plugins,
        "failed_plugins": failed_plugins,
        "changed_paths": changed_paths,
        "report_markdown": report_markdown,
    }
    dump_json_file(args.output, result)
    write_github_output(
        os.getenv("GITHUB_OUTPUT"),
        {
            "status": status,
            "changed_paths": "\n".join(changed_paths),
            "updated_plugins": ",".join(item["plugin_id"] for item in updated_plugins),
            "report_markdown": report_markdown,
            "pr_body_fragment": build_pr_body_fragment(updated_plugins),
            "branch_name": "automation/marketplace-version-sync",
            "commit_message": "编排：同步插件市场已收录插件版本",
            "pr_title": "编排：同步插件市场已收录插件版本",
        },
    )
    print(report_markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
