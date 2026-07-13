"""P0-5A quota 前端模块的 node:test 包装（无第三方依赖，使用 Node 内置 test runner）。

沿用仓库既有"用 node 子进程验证 UI 脚本"的方式（见 test_observation_api.run_ui_probe），
不引入 Vitest 等新依赖。fixture 仅存在于 tests/quota_ui/fixtures 下，生产代码不含 fixture。
"""

from pathlib import Path
import shutil
import subprocess

import pytest

QUOTA_UI_TEST_DIR = Path(__file__).resolve().parent / "quota_ui"


@pytest.mark.skipif(shutil.which("node") is None, reason="node 未安装，跳过前端 node:test")
def test_quota_ui_node_tests_pass():
    test_files = sorted(str(p) for p in QUOTA_UI_TEST_DIR.glob("*.test.js"))
    assert test_files, "未找到 quota 前端 node:test 用例"
    result = subprocess.run(
        ["node", "--test", *test_files],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
