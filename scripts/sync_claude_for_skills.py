"""从参考仓库刷新 vendored 的 claude-for 技能语料（开发期工具，运行期不依赖它）。

用法：
    uv run scripts/sync_claude_for_skills.py --src E:/claude-for-financial-services-cn --dry-run
    uv run scripts/sync_claude_for_skills.py --src E:/claude-for-financial-services-cn

只同步 `vertical-plugins/<域>/skills/<技能>/SKILL.md`（`agent-plugins/` 的 31 个是 vertical 的子集，
`.claude-plugin/`、`.mcp.json` 是 Claude Code 打包物，本项目不需要）。

**同步后必须跑** `pytest tests/test_skill_loader.py -q`：
`skill_catalog.py` 的 63 条中文短描述是人工维护的，upstream 新增/改名会让它缺项（router 清单立刻退化成
英文长句），那个测试专门守这件事。
"""

from __future__ import annotations

import argparse
import filecmp
import shutil
import subprocess
import sys
from pathlib import Path

from demomcp.graph.skill_loader import DEFAULT_LIBRARY_DIR

REL = "vertical-plugins"


def upstream_commit(src: Path) -> str:
    try:
        out = subprocess.run(
            ["git", "-C", str(src), "log", "-1", "--format=%h %ci %s"],
            capture_output=True, text=True, check=False, encoding="utf-8",
        )
        return (out.stdout or "").strip() or "(no git info)"
    except OSError:
        return "(git unavailable)"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="同步 claude-for 的 SKILL.md 语料到 demomcp/skill_library/")
    ap.add_argument("--src", required=True, help="claude-for-financial-services-cn 仓库路径")
    ap.add_argument("--dest", default=str(DEFAULT_LIBRARY_DIR), help="目标目录（默认包内 skill_library/claude-for）")
    ap.add_argument("--dry-run", action="store_true", help="只报告差异，不写文件")
    args = ap.parse_args(argv)

    src_root = Path(args.src) / REL
    dest_root = Path(args.dest) / REL
    if not src_root.is_dir():
        print(f"[错误] 源目录不存在：{src_root}", file=sys.stderr)
        return 2

    src_files = {p.relative_to(src_root): p for p in src_root.glob("*/skills/*/SKILL.md")}
    dest_files = {p.relative_to(dest_root): p for p in dest_root.glob("*/skills/*/SKILL.md")}

    added = sorted(set(src_files) - set(dest_files))
    removed = sorted(set(dest_files) - set(src_files))
    changed = sorted(
        rel for rel in set(src_files) & set(dest_files)
        if not filecmp.cmp(src_files[rel], dest_files[rel], shallow=False)
    )

    print(f"upstream: {upstream_commit(Path(args.src))}")
    print(f"源 {len(src_files)} 个 / 本地 {len(dest_files)} 个")
    for label, rels in (("新增", added), ("删除", removed), ("修改", changed)):
        for rel in rels:
            print(f"  {label}: {rel.as_posix()}")
    if not (added or removed or changed):
        print("无差异。")
        return 0

    if args.dry_run:
        print("\n--dry-run：未写入任何文件。")
        return 0

    for rel in added + changed:
        dst = dest_root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_files[rel], dst)
    for rel in removed:
        (dest_root / rel).unlink()
        parent = (dest_root / rel).parent
        if not any(parent.iterdir()):
            parent.rmdir()

    print(f"\n已同步：新增 {len(added)} / 修改 {len(changed)} / 删除 {len(removed)}")
    print("接着跑：.venv/Scripts/python.exe -m pytest tests/test_skill_loader.py -q")
    print("（新增/改名的技能要在 demomcp/graph/skill_catalog.py 补中文短描述，否则 router 清单退化）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
