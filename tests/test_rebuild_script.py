"""``scripts/rebuild.sh`` catches a build that is not reproducible.

The script runs for real, in a throwaway git repository, with a fake ``uv`` on
the PATH: one that builds the same bytes every time, and one that leaks the
build's environment (its umask, time zone and file dates) into the artifact the
way a non-reproducible backend would. The real build is checked by CI and by
the release workflow, which run the script on the project itself.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "rebuild.sh"

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")

#: Writes a wheel and an sdist into the folder after -o, from the sources.
FAKE_UV = """#!/bin/sh
while [ "$1" != "-o" ]; do shift; done
mkdir -p "$2"
cat pyproject.toml > "$2/pkg-1.0-py3-none-any.whl"
cat pyproject.toml > "$2/pkg-1.0.tar.gz"
{leak}
"""

#: What a non-reproducible build would carry: the environment of the build.
LEAKS = {
    "umask": 'umask >> "$2/pkg-1.0-py3-none-any.whl"',
    "time zone": 'echo "${TZ:-}" >> "$2/pkg-1.0.tar.gz"',
    "file dates": 'ls -l pyproject.toml | cut -c30- >> "$2/pkg-1.0.tar.gz"',
}


def _repo(tmp_path: Path, leak: str = "") -> tuple[Path, dict[str, str]]:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pyproject.toml").write_text("[project]\nname = 'pkg'\n", encoding="utf-8")
    (repo / "build-constraints.txt").write_text("", encoding="utf-8")
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid"]
    subprocess.run([*git, "init", "-q"], cwd=repo, check=True, timeout=30)
    subprocess.run([*git, "add", "."], cwd=repo, check=True, timeout=30)
    subprocess.run([*git, "commit", "-qm", "init"], cwd=repo, check=True, timeout=30)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    uv = bin_dir / "uv"
    uv.write_text(FAKE_UV.format(leak=leak), encoding="utf-8")
    uv.chmod(0o755)
    env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}
    return repo, env


def _run(repo: Path, env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["sh", str(SCRIPT), *args],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def test_identical_builds_pass_and_print_their_sums(tmp_path):
    repo, env = _repo(tmp_path)

    result = _run(repo, env)

    assert result.returncode == 0, result.stderr
    assert "reproducible: two builds of" in result.stdout
    assert "pkg-1.0.tar.gz" in result.stdout


@pytest.mark.parametrize("leak", sorted(LEAKS))
def test_a_build_that_depends_on_its_environment_fails(tmp_path, leak):
    repo, env = _repo(tmp_path, LEAKS[leak])

    result = _run(repo, env)

    assert result.returncode == 1
    assert "two builds of the same commit differ" in result.stderr


def test_a_rebuild_is_checked_against_published_sums(tmp_path):
    repo, env = _repo(tmp_path)
    published = tmp_path / "SHA256SUMS"
    first = _run(repo, env)
    published.write_text(
        "".join(line + "\n" for line in first.stdout.splitlines() if "pkg-1.0" in line),
        encoding="utf-8",
    )

    assert _run(repo, env, "--against", str(published)).returncode == 0

    published.write_text("0" * 64 + "  pkg-1.0.tar.gz\n", encoding="utf-8")
    tampered = _run(repo, env, "--against", str(published))
    assert tampered.returncode == 1
    assert "does not match" in tampered.stderr


def test_it_refuses_unknown_arguments(tmp_path):
    repo, env = _repo(tmp_path)

    assert _run(repo, env, "--sums").returncode == 2
    assert _run(repo, env, "--against", str(tmp_path / "missing")).returncode == 2
