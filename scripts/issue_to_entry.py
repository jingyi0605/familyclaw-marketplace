from __future__ import annotations

import argparse
import os
from pathlib import Path

from marketplace_submission_lib import dump_json_file, load_json_file, normalize_text, write_github_output


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="把校验结果写成市场条目文件。")
    parser.add_argument("--validation-json", required=True, help="校验结果 JSON 路径。")
    parser.add_argument("--repository-root", required=True, help="市场仓库根目录。")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    payload = load_json_file(args.validation_json)
    if payload.get("status") != "passed":
        raise SystemExit("只有校验通过的结果才能生成市场条目。")

    generated_entry = payload.get("generated_entry")
    if not isinstance(generated_entry, dict):
        raise SystemExit("校验结果缺少 generated_entry。")

    plugin_id = normalize_text(str(generated_entry.get("plugin_id") or ""))
    if not plugin_id:
        raise SystemExit("generated_entry.plugin_id 不能为空。")

    repository_root = Path(args.repository_root).resolve()
    entry_dir = repository_root / "plugins" / plugin_id
    entry_dir.mkdir(parents=True, exist_ok=True)
    entry_path = entry_dir / "entry.json"
    dump_json_file(str(entry_path), generated_entry)

    issue_number = int(payload.get("issue_number") or 0)
    branch_name = f"automation/plugin-submission/issue-{issue_number}"
    commit_message = f"编排：更新插件市场条目 {plugin_id}"
    pr_title = f"编排：收录插件 {plugin_id}（Issue #{issue_number}）"

    write_github_output(
        os.getenv("GITHUB_OUTPUT"),
        {
            "plugin_id": plugin_id,
            "entry_path": str(entry_path.relative_to(repository_root)).replace("\\", "/"),
            "branch_name": branch_name,
            "commit_message": commit_message,
            "pr_title": pr_title,
        },
    )
    print(f"已生成市场条目：{entry_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
