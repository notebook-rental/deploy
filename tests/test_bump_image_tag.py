"""tools/bump_image_tag.py 테스트."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from tools import bump_image_tag as bt

ROOT = Path(__file__).resolve().parent.parent
SHA = "3f2c9e1a7b4d5c6e8f9012345678901234567890"


@pytest.fixture
def repo(tmp_path):
    """실제 overlays를 임시 폴더로 복사해서, 진짜 파일 모양 그대로 시험한다."""
    shutil.copytree(ROOT / "overlays", tmp_path / "overlays")
    shutil.copytree(ROOT / "apps", tmp_path / "apps")
    return tmp_path


@pytest.mark.parametrize("app", bt.APPS)
def test_bumps_real_overlay_and_keeps_comments(repo, app, capsys):
    path = bt.kustomization_path(repo, app)
    before = path.read_text()
    assert bt.main([app, SHA], root=repo) == 0
    after = path.read_text()
    images = yaml.safe_load(after)["images"]
    assert images == [{"name": f"ghcr.io/notebook-rental/{app}", "newTag": SHA}]
    # 주석과 다른 줄은 그대로다.
    assert [l for l in before.splitlines() if "newTag" not in l] == \
           [l for l in after.splitlines() if "newTag" not in l]
    assert SHA in capsys.readouterr().out


def test_second_run_is_noop(repo, capsys):
    assert bt.main(["api", SHA], root=repo) == 0
    assert bt.main(["api", SHA], root=repo) == 0
    assert "이미" in capsys.readouterr().out


def test_only_target_image_changes():
    text = ("images:\n  - name: ghcr.io/notebook-rental/api\n    newTag: old\n"
            "  - name: ghcr.io/notebook-rental/api-worker\n    newTag: keep\n")
    out = bt.bump(text, "ghcr.io/notebook-rental/api", "new")
    assert "newTag: new" in out and "newTag: keep" in out


@pytest.mark.parametrize("tag", ["", "-bad", ".bad", "has space", "a" * 129, "x;rm"])
def test_rejects_invalid_tags(tag):
    with pytest.raises(ValueError, match="태그로 쓸 수 없는"):
        bt.bump("- name: i\n  newTag: a\n", "i", tag)


def test_missing_image_raises():
    with pytest.raises(ValueError, match="0곳"):
        bt.bump("images: []\n", "ghcr.io/notebook-rental/api", "abc")


def test_wrong_arg_count_prints_usage(capsys):
    assert bt.main([]) == 2
    assert "사용법" in capsys.readouterr().out


def test_unknown_app(capsys):
    assert bt.main(["db", SHA]) == 2
    assert "알 수 없는 앱" in capsys.readouterr().out


def test_missing_file_fails(tmp_path, capsys):
    assert bt.main(["api", SHA], root=tmp_path) == 1
    assert "실패" in capsys.readouterr().out
