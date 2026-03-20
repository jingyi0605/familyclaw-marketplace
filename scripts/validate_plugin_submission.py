from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from marketplace_submission_lib import (
    STATUS_FAILED,
    STATUS_PASSED,
    STATUS_SYSTEM_ERROR,
    AutomationSystemError,
    ValidationError,
    build_blob_file_url,
    build_raw_file_url,
    build_report_markdown,
    build_versions,
    dump_json_file,
    fetch_repo_content_metadata,
    fetch_repo_metadata,
    fetch_repo_releases,
    fetch_repo_tags,
    load_json_file,
    normalize_relative_path,
    normalize_text,
    parse_github_repo_url,
    parse_issue_form,
    request_text,
    resolve_min_app_version,
    validate_generated_entry,
    validate_required_submission_fields,
    write_github_output,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="校验插件收录 Issue，并生成结构化结果。")
    parser.add_argument("--event-path", help="GitHub 事件 JSON 路径。")
    parser.add_argument("--issue-body-file", help="Issue 正文文件路径，用于本地调试。")
    parser.add_argument("--issue-number", type=int, help="Issue 编号，用于本地调试。")
    parser.add_argument("--output", required=True, help="校验结果 JSON 输出路径。")
    parser.add_argument("--plugin-repo-dir", help="本地插件仓库目录，用于离线调试。")
    return parser.parse_args()


def load_issue_context(args: argparse.Namespace) -> tuple[int, str]:
    if args.event_path:
        payload = load_json_file(args.event_path)
        issue = payload.get("issue") or {}
        return int(issue.get("number") or 0), str(issue.get("body") or "")
    if args.issue_body_file and args.issue_number:
        return args.issue_number, Path(args.issue_body_file).read_text(encoding="utf-8")
    raise SystemExit("必须提供 --event-path，或同时提供 --issue-body-file 和 --issue-number。")


def validate_submission(
    *,
    issue_number: int,
    issue_body: str,
    token: str | None,
    plugin_repo_dir: str | None,
) -> dict[str, Any]:
    submission = parse_issue_form(issue_body)
    field_errors = validate_required_submission_fields(submission)
    repository_errors: list[dict[str, str]] = []
    generated_entry: dict[str, Any] | None = None
    plugin_id = ""

    if field_errors:
        return {
            "status": STATUS_FAILED,
            "issue_number": issue_number,
            "plugin_id": plugin_id,
            "submission": submission,
            "field_errors": field_errors,
            "repository_errors": repository_errors,
            "generated_entry": None,
            "report_markdown": build_report_markdown(
                status=STATUS_FAILED,
                summary_lines=["Issue 必填字段不完整，当前不能继续生成市场条目。"],
                field_errors=field_errors,
                repository_errors=repository_errors,
            ),
        }

    try:
        repo_info = parse_github_repo_url(submission["plugin_repo_url"])
        branch = normalize_text(submission["plugin_repo_branch"]) or "main"
        manifest_path = normalize_relative_path(submission["manifest_path"], field_name="manifest_path")
        readme_path = normalize_relative_path(submission["readme_path"], field_name="readme_path")
        package_root = normalize_relative_path(submission["package_root"], field_name="package_root")
        requirements_path = normalize_relative_path(submission["requirements_path"], field_name="requirements_path")

        if plugin_repo_dir:
            base_dir = Path(plugin_repo_dir).resolve()
            repo_metadata = {
                "description": "",
                "owner": {
                    "login": repo_info["owner"],
                    "html_url": f"https://github.com/{repo_info['owner']}",
                },
            }
            manifest = json.loads((base_dir / manifest_path).read_text(encoding="utf-8"))
            _ = (base_dir / readme_path).read_text(encoding="utf-8")
            requirements_file = base_dir / requirements_path
            if not requirements_file.exists():
                raise ValidationError(
                    "找不到 requirements.txt。",
                    error_code="install_target_invalid",
                    field="requirements_path",
                )
            if not normalize_text(requirements_file.read_text(encoding="utf-8")):
                raise ValidationError(
                    "requirements.txt 不能为空。",
                    error_code="install_target_invalid",
                    field="requirements_path",
                )
            if not (base_dir / package_root).is_dir():
                raise ValidationError(
                    "找不到插件包根目录。",
                    error_code="install_target_invalid",
                    field="package_root",
                )
            releases: list[dict[str, Any]] = []
            tags: list[dict[str, Any]] = []
        else:
            repo_metadata = fetch_repo_metadata(repo_info["owner"], repo_info["repo"], token=token)
            try:
                manifest = json.loads(
                    request_text(
                        build_raw_file_url(repo_info["owner"], repo_info["repo"], branch, manifest_path),
                        token=token,
                    )
                )
            except ValidationError as exc:
                if exc.error_code == "plugin_repo_unreachable":
                    raise ValidationError(
                        "找不到指定的 manifest.json。",
                        error_code="manifest_invalid",
                        field="manifest_path",
                    ) from exc
                raise
            try:
                _ = request_text(
                    build_raw_file_url(repo_info["owner"], repo_info["repo"], branch, readme_path),
                    token=token,
                )
            except ValidationError as exc:
                if exc.error_code == "plugin_repo_unreachable":
                    raise ValidationError(
                        "找不到指定的 README。",
                        error_code="manifest_invalid",
                        field="readme_path",
                    ) from exc
                raise
            try:
                requirements_text = request_text(
                    build_raw_file_url(repo_info["owner"], repo_info["repo"], branch, requirements_path),
                    token=token,
                )
            except ValidationError as exc:
                if exc.error_code == "plugin_repo_unreachable":
                    raise ValidationError(
                        "找不到指定的 requirements.txt。",
                        error_code="install_target_invalid",
                        field="requirements_path",
                    ) from exc
                raise
            if not normalize_text(requirements_text):
                raise ValidationError(
                    "requirements.txt 不能为空。",
                    error_code="install_target_invalid",
                    field="requirements_path",
                )
            try:
                package_root_meta = fetch_repo_content_metadata(
                    repo_info["owner"],
                    repo_info["repo"],
                    path=package_root,
                    ref=branch,
                    token=token,
                )
            except ValidationError as exc:
                if exc.error_code == "plugin_repo_unreachable":
                    raise ValidationError(
                        "找不到插件包根目录。",
                        error_code="install_target_invalid",
                        field="package_root",
                    ) from exc
                raise
            if package_root_meta.get("type") != "dir":
                raise ValidationError(
                    "插件包根目录不存在或不是目录。",
                    error_code="install_target_invalid",
                    field="package_root",
                )
            releases = fetch_repo_releases(repo_info["owner"], repo_info["repo"], token=token)
            tags = fetch_repo_tags(repo_info["owner"], repo_info["repo"], token=token)

        plugin_id = normalize_text(str(manifest.get("id") or ""))
        manifest_name = normalize_text(str(manifest.get("name") or ""))
        manifest_version = normalize_text(str(manifest.get("version") or ""))
        risk_level = normalize_text(str(manifest.get("risk_level") or ""))
        permissions = manifest.get("permissions") or []

        if not plugin_id:
            raise ValidationError("manifest.json 缺少 id。", error_code="manifest_invalid", field="manifest_path")
        if not manifest_name:
            raise ValidationError("manifest.json 缺少 name。", error_code="manifest_invalid", field="manifest_path")
        if not manifest_version:
            raise ValidationError("manifest.json 缺少 version。", error_code="manifest_invalid", field="manifest_path")
        if risk_level not in {"low", "medium", "high"}:
            raise ValidationError(
                "manifest.json 的 risk_level 只能是 low / medium / high。",
                error_code="manifest_invalid",
                field="manifest_path",
            )
        if not isinstance(permissions, list):
            raise ValidationError(
                "manifest.json 的 permissions 必须是数组。",
                error_code="manifest_invalid",
                field="manifest_path",
            )

        repo_description = normalize_text(str(repo_metadata.get("description") or ""))
        min_app_version = resolve_min_app_version(manifest)
        versions = build_versions(
            manifest_version=manifest_version,
            branch=branch,
            releases=releases,
            tags=tags,
            min_app_version=min_app_version,
        )
        latest_version = versions[0]["version"]

        generated_entry = {
            "plugin_id": plugin_id,
            "name": manifest_name,
            "summary": normalize_text(submission.get("summary_override")) or repo_description or manifest_name,
            "source_repo": repo_info["html_url"],
            "manifest_path": manifest_path,
            "readme_url": build_blob_file_url(repo_info["owner"], repo_info["repo"], branch, readme_path),
            "publisher": {
                "name": normalize_text(str((repo_metadata.get("owner") or {}).get("login") or "")) or repo_info["owner"],
                "url": normalize_text(str((repo_metadata.get("owner") or {}).get("html_url") or ""))
                or f"https://github.com/{repo_info['owner']}",
            },
            "categories": submission.get("category_hints") or [],
            "risk_level": risk_level,
            "permissions": permissions,
            "latest_version": latest_version,
            "versions": versions,
            "install": {
                "package_root": package_root,
                "requirements_path": requirements_path,
                "readme_path": readme_path,
            },
            "maintainers": submission.get("maintainers") or [],
        }

        entry_errors = validate_generated_entry(generated_entry)
        if entry_errors:
            repository_errors.extend(entry_errors)
            return {
                "status": STATUS_FAILED,
                "issue_number": issue_number,
                "plugin_id": plugin_id,
                "submission": submission,
                "field_errors": field_errors,
                "repository_errors": repository_errors,
                "generated_entry": generated_entry,
                "report_markdown": build_report_markdown(
                    status=STATUS_FAILED,
                    summary_lines=["自动生成市场条目时发现结构冲突，当前不能创建 PR。"],
                    field_errors=field_errors,
                    repository_errors=repository_errors,
                ),
            }

        return {
            "status": STATUS_PASSED,
            "issue_number": issue_number,
            "plugin_id": plugin_id,
            "submission": submission,
            "field_errors": field_errors,
            "repository_errors": repository_errors,
            "generated_entry": generated_entry,
            "report_markdown": build_report_markdown(
                status=STATUS_PASSED,
                summary_lines=[
                    f"插件 ID：`{plugin_id}`",
                    f"源码仓库：`{repo_info['html_url']}`",
                    f"默认安装版本：`{latest_version}`",
                    "当前可以进入机器人 PR 生成阶段。",
                ],
                field_errors=field_errors,
                repository_errors=repository_errors,
            ),
        }
    except ValidationError as exc:
        repository_errors.append(
            {
                "field": exc.field or "repository",
                "error_code": exc.error_code,
                "detail": exc.detail,
            }
        )
        return {
            "status": STATUS_FAILED,
            "issue_number": issue_number,
            "plugin_id": plugin_id,
            "submission": submission,
            "field_errors": field_errors,
            "repository_errors": repository_errors,
            "generated_entry": generated_entry,
            "report_markdown": build_report_markdown(
                status=STATUS_FAILED,
                summary_lines=["插件仓库校验没有通过，当前不会创建机器人 PR。"],
                field_errors=field_errors,
                repository_errors=repository_errors,
            ),
        }
    except AutomationSystemError as exc:
        repository_errors.append(
            {
                "field": "system",
                "error_code": exc.error_code,
                "detail": exc.detail,
            }
        )
        return {
            "status": STATUS_SYSTEM_ERROR,
            "issue_number": issue_number,
            "plugin_id": plugin_id,
            "submission": submission,
            "field_errors": field_errors,
            "repository_errors": repository_errors,
            "generated_entry": generated_entry,
            "report_markdown": build_report_markdown(
                status=STATUS_SYSTEM_ERROR,
                summary_lines=["自动化流程遇到系统异常，这次失败不应直接算成插件不合格。"],
                field_errors=field_errors,
                repository_errors=repository_errors,
            ),
        }


def main() -> int:
    args = parse_args()
    issue_number, issue_body = load_issue_context(args)
    token = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
    result = validate_submission(
        issue_number=issue_number,
        issue_body=issue_body,
        token=token,
        plugin_repo_dir=args.plugin_repo_dir,
    )
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    dump_json_file(args.output, result)
    write_github_output(
        os.getenv("GITHUB_OUTPUT"),
        {
            "status": str(result["status"]),
            "issue_number": str(result["issue_number"]),
            "plugin_id": str(result.get("plugin_id") or ""),
        },
    )
    print(result["report_markdown"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
