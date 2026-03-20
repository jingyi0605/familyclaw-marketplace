from __future__ import annotations

import json
from functools import cmp_to_key
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import PurePosixPath
from typing import Any, Callable


STATUS_PASSED = "passed"
STATUS_FAILED = "failed"
STATUS_SYSTEM_ERROR = "system_error"

ALLOWED_RISK_LEVELS = {"low", "medium", "high"}
_VERSION_PATTERN = re.compile(
    r"^v?(?P<release>\d+(?:\.\d+)*)"
    r"(?:(?:[-_.]?)(?P<pre_label>alpha|a|beta|b|rc|pre|preview)(?P<pre_number>\d*)?)?"
    r"(?:\+.*)?$",
    re.IGNORECASE,
)
_PRE_RELEASE_ORDER = {
    "alpha": 0,
    "a": 0,
    "beta": 1,
    "b": 1,
    "pre": 2,
    "preview": 2,
    "rc": 2,
}
FORM_SECTION_FIELD_MAP = {
    "插件源码仓库地址": "plugin_repo_url",
    "仓库分支": "plugin_repo_branch",
    "manifest.json 路径": "manifest_path",
    "README 路径": "readme_path",
    "插件包根目录": "package_root",
    "requirements.txt 路径": "requirements_path",
    "市场摘要补充": "summary_override",
    "分类建议": "category_hints",
    "维护者信息": "maintainers",
    "补充说明": "maintainer_notes",
}
EMPTY_FORM_VALUES = {"_No response_", "N/A", "无", "none", "None"}


class ValidationError(ValueError):
    def __init__(self, detail: str, *, error_code: str, field: str | None = None) -> None:
        super().__init__(detail)
        self.detail = detail
        self.error_code = error_code
        self.field = field


class AutomationSystemError(RuntimeError):
    def __init__(self, detail: str, *, error_code: str = "automation_system_error") -> None:
        super().__init__(detail)
        self.detail = detail
        self.error_code = error_code


def write_github_output(path: str | None, pairs: dict[str, str]) -> None:
    if not path:
        return
    with open(path, "a", encoding="utf-8") as stream:
        for key, value in pairs.items():
            stream.write(f"{key}={value}\n")


def load_json_file(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as stream:
        return json.load(stream)


def dump_json_file(path: str, payload: Any) -> None:
    with open(path, "w", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def normalize_text(value: str | None) -> str:
    return (value or "").strip()


def collapse_form_value(value: str | None) -> str:
    normalized = normalize_text(value)
    if not normalized or normalized in EMPTY_FORM_VALUES:
        return ""
    if normalized.startswith("```") and normalized.endswith("```"):
        lines = normalized.splitlines()
        if len(lines) >= 3:
            normalized = "\n".join(lines[1:-1]).strip()
    if normalized.startswith("`") and normalized.endswith("`") and normalized.count("`") == 2:
        normalized = normalized[1:-1].strip()
    return normalized


def parse_issue_form(body: str) -> dict[str, Any]:
    sections = re.findall(r"^###\s+(.+?)\r?\n([\s\S]*?)(?=^###\s+|\Z)", body, flags=re.MULTILINE)
    parsed: dict[str, Any] = {}
    for title, content in sections:
        field_name = FORM_SECTION_FIELD_MAP.get(title.strip())
        if field_name is None:
            continue
        parsed[field_name] = collapse_form_value(content)

    parsed["plugin_repo_branch"] = parsed.get("plugin_repo_branch") or "main"
    parsed["manifest_path"] = parsed.get("manifest_path") or "manifest.json"
    parsed["readme_path"] = parsed.get("readme_path") or "README.md"
    parsed["package_root"] = parsed.get("package_root") or "plugin"
    parsed["requirements_path"] = parsed.get("requirements_path") or "requirements.txt"
    parsed["category_hints"] = parse_text_list(parsed.get("category_hints", ""))
    parsed["maintainers"] = parse_maintainers(parsed.get("maintainers", ""))
    parsed["summary_override"] = parsed.get("summary_override", "")
    parsed["maintainer_notes"] = parsed.get("maintainer_notes", "")
    return parsed


def parse_text_list(value: str) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for raw_line in value.splitlines():
        line = raw_line.strip().lstrip("-").strip()
        if not line or line in seen:
            continue
        seen.add(line)
        result.append(line)
    return result


def parse_maintainers(value: str) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for raw_line in value.splitlines():
        line = raw_line.strip().lstrip("-").strip()
        if not line:
            continue
        name = line
        url = ""
        if "|" in line:
            left, right = line.split("|", 1)
            name = left.strip()
            url = right.strip()
        key = (name, url)
        if key in seen:
            continue
        seen.add(key)
        item: dict[str, str] = {"name": name}
        if url:
            item["url"] = url
        result.append(item)
    return result


def parse_github_repo_url(repo_url: str) -> dict[str, str]:
    normalized = normalize_text(repo_url)
    parsed = urllib.parse.urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or parsed.netloc.lower() != "github.com":
        raise ValidationError(
            "插件源码仓库地址必须是 GitHub 仓库地址。",
            error_code="issue_form_invalid",
            field="plugin_repo_url",
        )
    segments = [segment for segment in parsed.path.split("/") if segment]
    if len(segments) < 2:
        raise ValidationError(
            "插件源码仓库地址不完整，必须至少包含 owner 和 repo。",
            error_code="issue_form_invalid",
            field="plugin_repo_url",
        )
    owner = segments[0]
    repo = segments[1]
    if repo.endswith(".git"):
        repo = repo[:-4]
    return {
        "owner": owner,
        "repo": repo,
        "html_url": f"https://github.com/{owner}/{repo}",
    }


def normalize_relative_path(value: str, *, field_name: str) -> str:
    normalized = normalize_text(value).strip("/")
    if not normalized:
        raise ValidationError(f"{field_name} 不能为空。", error_code="issue_form_invalid", field=field_name)
    path = PurePosixPath(normalized)
    if path.is_absolute() or ".." in path.parts:
        raise ValidationError(f"{field_name} 不能包含越界路径。", error_code="issue_form_invalid", field=field_name)
    return str(path)


def translate_http_error(exc: urllib.error.HTTPError) -> Exception:
    if exc.code == 404:
        return ValidationError(
            "目标资源不存在或当前无法访问。",
            error_code="plugin_repo_unreachable",
            field="plugin_repo_url",
        )
    if exc.code in {401, 403, 429}:
        return AutomationSystemError(f"GitHub 返回 {exc.code}，可能是权限不足或速率限制，请稍后重试。")
    if exc.code >= 500:
        return AutomationSystemError(f"GitHub 服务异常（HTTP {exc.code}），请稍后重试。")
    return AutomationSystemError(f"GitHub 请求失败（HTTP {exc.code}）。")


def request_json(url: str, *, token: str | None = None) -> Any:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "familyclaw-marketplace-submission-bot",
            **({"Authorization": f"Bearer {token}"} if token else {}),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise translate_http_error(exc)
    except urllib.error.URLError as exc:
        raise AutomationSystemError(f"访问 GitHub 失败：{exc.reason}") from exc


def request_text(url: str, *, token: str | None = None) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github.raw+json",
            "User-Agent": "familyclaw-marketplace-submission-bot",
            **({"Authorization": f"Bearer {token}"} if token else {}),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raise translate_http_error(exc)
    except urllib.error.URLError as exc:
        raise AutomationSystemError(f"访问 GitHub 失败：{exc.reason}") from exc


def fetch_repo_metadata(owner: str, repo: str, *, token: str | None = None) -> dict[str, Any]:
    return request_json(f"https://api.github.com/repos/{owner}/{repo}", token=token)


def fetch_repo_releases(owner: str, repo: str, *, token: str | None = None) -> list[dict[str, Any]]:
    payload = request_json(f"https://api.github.com/repos/{owner}/{repo}/releases?per_page=20", token=token)
    return payload if isinstance(payload, list) else []


def fetch_repo_tags(owner: str, repo: str, *, token: str | None = None) -> list[dict[str, Any]]:
    payload = request_json(f"https://api.github.com/repos/{owner}/{repo}/tags?per_page=20", token=token)
    return payload if isinstance(payload, list) else []


def fetch_repo_content_metadata(
    owner: str,
    repo: str,
    *,
    path: str,
    ref: str,
    token: str | None = None,
) -> dict[str, Any]:
    quoted_path = urllib.parse.quote(path)
    quoted_ref = urllib.parse.quote(ref, safe="")
    return request_json(
        f"https://api.github.com/repos/{owner}/{repo}/contents/{quoted_path}?ref={quoted_ref}",
        token=token,
    )


def build_raw_file_url(owner: str, repo: str, ref: str, path: str) -> str:
    return f"https://raw.githubusercontent.com/{owner}/{repo}/{urllib.parse.quote(ref, safe='')}/{path}"


def build_blob_file_url(owner: str, repo: str, ref: str, path: str) -> str:
    return f"https://github.com/{owner}/{repo}/blob/{urllib.parse.quote(ref, safe='')}/{path}"


def strip_version_prefix(value: str) -> str:
    normalized = normalize_text(value)
    if normalized.lower().startswith("v") and len(normalized) > 1 and normalized[1].isdigit():
        return normalized[1:]
    return normalized


def _parse_version_sort_key(value: str) -> tuple[tuple[int, ...], int, int]:
    normalized = strip_version_prefix(value)
    match = _VERSION_PATTERN.fullmatch(normalized)
    if match is None:
        raise ValueError(f"当前版本比较规则不支持 {value!r}")
    release = tuple(int(part) for part in match.group("release").split("."))
    pre_label = match.group("pre_label")
    if pre_label is None:
        return release, 99, 0
    return release, _PRE_RELEASE_ORDER[pre_label.lower()], int(match.group("pre_number") or "0")


def compare_version_text(left: str, right: str) -> int:
    parsed_left = _parse_version_sort_key(left)
    parsed_right = _parse_version_sort_key(right)
    if parsed_left < parsed_right:
        return -1
    if parsed_left > parsed_right:
        return 1
    return 0


def pick_highest_version(versions: list[str]) -> tuple[str | None, str | None]:
    if not versions:
        return None, None
    highest = versions[0]
    try:
        for version in versions[1:]:
            if compare_version_text(version, highest) > 0:
                highest = version
    except ValueError as exc:
        return None, str(exc)
    return highest, None


def normalize_tag_git_ref(value: str) -> str:
    normalized = normalize_text(value)
    if normalized.startswith("refs/tags/"):
        return normalized
    return f"refs/tags/{normalized}"


def is_tag_git_ref(value: str | None) -> bool:
    normalized = normalize_text(value)
    return normalized.startswith("refs/tags/") and len(normalized) > len("refs/tags/")


def build_source_archive_artifact_url(source_repo_url: str, git_ref: str) -> str:
    return f"{source_repo_url.rstrip('/')}/archive/{normalize_text(git_ref)}.zip"


def resolve_min_app_version(manifest: dict[str, Any]) -> str | None:
    compatibility = manifest.get("compatibility")
    if isinstance(compatibility, dict):
        value = compatibility.get("min_app_version")
        if isinstance(value, str) and value.strip():
            return value.strip()
    value = manifest.get("min_app_version")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def build_version_manifest_overrides(
    *,
    tag_names: list[str],
    release_published_at_by_tag: dict[str, str | None] | None,
    load_manifest_for_git_ref: Callable[[str], dict[str, Any]],
) -> dict[str, dict[str, str]]:
    overrides: dict[str, dict[str, str]] = {}
    seen: set[str] = set()
    published_at_map = release_published_at_by_tag or {}
    for tag_name in tag_names:
        normalized_tag_name = normalize_text(tag_name)
        if not normalized_tag_name or normalized_tag_name in seen:
            continue
        seen.add(normalized_tag_name)
        git_ref = normalize_tag_git_ref(normalized_tag_name)
        manifest = load_manifest_for_git_ref(git_ref)
        manifest_version = normalize_text(str(manifest.get("version") or ""))
        expected_version = strip_version_prefix(normalized_tag_name)
        if not manifest_version:
            raise ValidationError(
                f"tag {normalized_tag_name} 对应的 manifest.json 缺少 version。",
                error_code="manifest_invalid",
                field="manifest_path",
            )
        if manifest_version != expected_version:
            raise ValidationError(
                f"tag {normalized_tag_name} 和 manifest.version 不一致。发布前请先把 manifest.version 更新为 {expected_version}。",
                error_code="manifest_invalid",
                field="manifest_path",
            )
        min_app_version = resolve_min_app_version(manifest)
        if not min_app_version:
            raise ValidationError(
                f"tag {normalized_tag_name} 对应的 manifest.json 必须声明 compatibility.min_app_version。每个版本都要单独维护最低宿主版本。",
                error_code="manifest_invalid",
                field="manifest_path",
            )
        item = {
            "version": manifest_version,
            "min_app_version": min_app_version,
        }
        published_at = normalize_text(published_at_map.get(normalized_tag_name))
        if published_at:
            item["published_at"] = published_at
        overrides[git_ref] = item
    return overrides


def build_versions(
    *,
    manifest_version: str,
    branch: str,
    source_repo_url: str,
    releases: list[dict[str, Any]],
    tags: list[dict[str, Any]],
    min_app_version: str | None,
    version_manifest_overrides: dict[str, dict[str, str]] | None = None,
) -> list[dict[str, Any]]:
    versions: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add_version(version: str, git_ref: str, *, published_at: str | None = None) -> None:
        override = (version_manifest_overrides or {}).get(normalize_text(git_ref)) or {}
        normalized_version = normalize_text(override.get("version")) or strip_version_prefix(version)
        if not normalized_version or normalized_version in seen:
            return
        seen.add(normalized_version)
        item: dict[str, Any] = {
            "version": normalized_version,
            "git_ref": git_ref,
            "artifact_type": "source_archive",
            "artifact_url": build_source_archive_artifact_url(source_repo_url, git_ref),
        }
        resolved_published_at = normalize_text(override.get("published_at")) or published_at
        resolved_min_app_version = normalize_text(override.get("min_app_version")) or min_app_version
        if resolved_published_at:
            item["published_at"] = resolved_published_at
        if resolved_min_app_version:
            item["min_app_version"] = resolved_min_app_version
        versions.append(item)

    for release in releases:
        if release.get("draft") or release.get("prerelease"):
            continue
        tag_name = normalize_text(str(release.get("tag_name") or ""))
        if not tag_name:
            continue
        add_version(
            strip_version_prefix(tag_name),
            normalize_tag_git_ref(tag_name),
            published_at=normalize_text(str(release.get("published_at") or release.get("created_at") or "")) or None,
        )

    preferred_tags = [f"v{manifest_version}", manifest_version]
    for preferred in preferred_tags:
        for tag in tags:
            tag_name = normalize_text(str(tag.get("name") or ""))
            if tag_name == preferred:
                add_version(manifest_version, normalize_tag_git_ref(tag_name))

    for tag in tags:
        tag_name = normalize_text(str(tag.get("name") or ""))
        if tag_name:
            add_version(strip_version_prefix(tag_name), normalize_tag_git_ref(tag_name))

    if not versions:
        add_version(manifest_version, branch)
    try:
        return sorted(
            versions,
            key=cmp_to_key(lambda left, right: compare_version_text(str(left["version"]), str(right["version"]))),
            reverse=True,
        )
    except ValueError:
        return versions


def validate_required_submission_fields(submission: dict[str, Any]) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    for field_name in ("plugin_repo_url", "manifest_path", "readme_path", "package_root", "requirements_path"):
        if not normalize_text(submission.get(field_name)):
            errors.append(
                {
                    "field": field_name,
                    "error_code": "issue_form_invalid",
                    "detail": f"{field_name} 不能为空。",
                }
            )
    if not submission.get("maintainers"):
        errors.append(
            {
                "field": "maintainers",
                "error_code": "issue_form_invalid",
                "detail": "至少要提供一条维护者信息。",
            }
        )
    return errors


def validate_generated_entry(entry: dict[str, Any]) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    if not normalize_text(str(entry.get("plugin_id") or "")):
        errors.append({"field": "plugin_id", "error_code": "entry_generation_failed", "detail": "plugin_id 不能为空。"})
    if not normalize_text(str(entry.get("name") or "")):
        errors.append({"field": "name", "error_code": "entry_generation_failed", "detail": "name 不能为空。"})
    if not normalize_text(str(entry.get("summary") or "")):
        errors.append({"field": "summary", "error_code": "entry_generation_failed", "detail": "summary 不能为空。"})
    if entry.get("risk_level") not in ALLOWED_RISK_LEVELS:
        errors.append(
            {"field": "risk_level", "error_code": "entry_generation_failed", "detail": "risk_level 只能是 low / medium / high。"}
        )
    versions = entry.get("versions")
    if not isinstance(versions, list) or not versions:
        errors.append({"field": "versions", "error_code": "entry_generation_failed", "detail": "versions 至少要有一个版本。"})
    if isinstance(versions, list):
        seen_versions: set[str] = set()
        for index, item in enumerate(versions):
            if not isinstance(item, dict):
                errors.append(
                    {
                        "field": f"versions[{index}]",
                        "error_code": "entry_generation_failed",
                        "detail": "versions 里的每一项都必须是对象。",
                    }
                )
                continue
            version_text = normalize_text(str(item.get("version") or ""))
            if not version_text:
                errors.append(
                    {
                        "field": f"versions[{index}].version",
                        "error_code": "entry_generation_failed",
                        "detail": "每个市场版本都必须声明 version。",
                    }
                )
            elif version_text in seen_versions:
                errors.append(
                    {
                        "field": f"versions[{index}].version",
                        "error_code": "entry_generation_failed",
                        "detail": f"版本 {version_text} 在 versions 里重复出现了。",
                    }
                )
            else:
                seen_versions.add(version_text)
            git_ref = normalize_text(str(item.get("git_ref") or ""))
            if not git_ref:
                errors.append(
                    {
                        "field": f"versions[{index}].git_ref",
                        "error_code": "entry_generation_failed",
                        "detail": "每个市场版本都必须声明 git_ref。",
                    }
                )
            elif len(versions) > 1 and not is_tag_git_ref(git_ref):
                errors.append(
                    {
                        "field": f"versions[{index}].git_ref",
                        "error_code": "entry_generation_failed",
                        "detail": "多版本市场条目只能引用 tag，git_ref 必须写成 refs/tags/<tag>。",
                    }
                )
            artifact_type = normalize_text(str(item.get("artifact_type") or ""))
            if artifact_type == "release_asset" and not normalize_text(str(item.get("artifact_url") or "")):
                errors.append(
                    {
                        "field": f"versions[{index}].artifact_url",
                        "error_code": "entry_generation_failed",
                        "detail": "release_asset 必须提供 artifact_url。",
                    }
                )
            if not normalize_text(str(item.get("min_app_version") or "")):
                errors.append(
                    {
                        "field": f"versions[{index}].min_app_version",
                        "error_code": "entry_generation_failed",
                        "detail": "每个市场版本都必须声明 min_app_version，不能生成兼容性未知的条目。",
                    }
                )
    latest_version = normalize_text(str(entry.get("latest_version") or ""))
    if not latest_version:
        errors.append({"field": "latest_version", "error_code": "entry_generation_failed", "detail": "latest_version 不能为空。"})
    if isinstance(versions, list) and latest_version:
        version_set = {normalize_text(str(item.get("version") or "")) for item in versions if isinstance(item, dict)}
        if latest_version not in version_set:
            errors.append(
                {
                    "field": "latest_version",
                    "error_code": "entry_generation_failed",
                    "detail": "latest_version 必须能在 versions 里找到。",
                }
            )
        else:
            highest_version, compare_error = pick_highest_version([version for version in version_set if version])
            if compare_error is not None:
                errors.append(
                    {
                        "field": "versions",
                        "error_code": "entry_generation_failed",
                        "detail": compare_error,
                    }
                )
            elif highest_version is not None and latest_version != highest_version:
                errors.append(
                    {
                        "field": "latest_version",
                        "error_code": "entry_generation_failed",
                        "detail": f"latest_version 必须指向当前最高版本 {highest_version}。",
                    }
                )
    install = entry.get("install")
    if not isinstance(install, dict):
        errors.append({"field": "install", "error_code": "entry_generation_failed", "detail": "install 必须存在。"})
    else:
        for field_name in ("package_root", "requirements_path", "readme_path"):
            if not normalize_text(str(install.get(field_name) or "")):
                errors.append(
                    {
                        "field": f"install.{field_name}",
                        "error_code": "entry_generation_failed",
                        "detail": f"install.{field_name} 不能为空。",
                    }
                )
    return errors


def build_report_markdown(
    *,
    status: str,
    summary_lines: list[str],
    field_errors: list[dict[str, str]],
    repository_errors: list[dict[str, str]],
) -> str:
    title = {
        STATUS_PASSED: "## 自动校验通过",
        STATUS_FAILED: "## 自动校验失败",
        STATUS_SYSTEM_ERROR: "## 自动校验遇到系统异常",
    }[status]
    lines = [title, ""]
    lines.extend(f"- {item}" for item in summary_lines if item)
    if field_errors:
        lines.extend(["", "### Issue 字段问题", ""])
        lines.extend(f"- `{item.get('field')}`：{item.get('detail')}" for item in field_errors)
    if repository_errors:
        lines.extend(["", "### 插件仓库问题", ""])
        lines.extend(f"- `{item.get('field') or 'repository'}`：{item.get('detail')}" for item in repository_errors)
    return "\n".join(lines).strip()
