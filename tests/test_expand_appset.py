"""tools/expand_appset.py 테스트(ApplicationSet 템플릿 펼치기)."""

from __future__ import annotations

import pytest
import yaml

from tools import expand_appset as ea


def appset(template, generators=None):
    return {
        "metadata": {"name": "x", "namespace": "argocd"},
        "spec": {
            "generators": generators or [{"git": {"directories": [{"path": "overlays/prod/*"}]}}],
            "template": template,
        },
    }


@pytest.fixture
def repo(tmp_path):
    for name in ["b", "a"]:
        (tmp_path / "overlays" / "prod" / name).mkdir(parents=True)
    (tmp_path / "overlays" / "prod" / "README.md").write_text("파일은 무시")
    return tmp_path


def test_directories_lists_only_matching_folders_sorted(repo):
    assert ea.directories(repo, "overlays/prod/*") == ["overlays/prod/a", "overlays/prod/b"]


def test_directories_missing_base_is_empty(tmp_path):
    assert ea.directories(tmp_path, "nothing/*") == []


def test_expand_fills_path_and_basename(repo):
    t = {"metadata": {"name": "app-{{.path.basename}}"}, "spec": {"source": {"path": "{{ .path.path }}"},
         "list": ["{{.path.basename}}", 3]}}
    apps = ea.expand(appset(t), root=repo)
    assert [a["metadata"] for a in apps] == [
        {"name": "app-a", "namespace": "argocd"}, {"name": "app-b", "namespace": "argocd"}]
    assert apps[0]["spec"] == {"source": {"path": "overlays/prod/a"}, "list": ["a", 3]}
    assert apps[0]["kind"] == "Application"


def test_missing_key_is_an_error(repo):
    with pytest.raises(KeyError, match="채울 수 없습니다"):
        ea.expand(appset({"metadata": {"name": "{{.path.nope}}"}}), root=repo)


def test_non_dict_lookup_is_an_error(repo):
    with pytest.raises(KeyError):
        ea.expand(appset({"metadata": {"name": "{{.path.basename.x}}"}}), root=repo)


def test_only_git_generator_supported(repo):
    with pytest.raises(ValueError, match="git 생성기"):
        ea.expand(appset({"metadata": {}}, generators=[{"list": {"elements": []}}]), root=repo)


def test_main_prints_real_applications(capsys):
    assert ea.main([]) == 0
    apps = list(yaml.safe_load_all(capsys.readouterr().out))
    assert len(apps) == 4
    assert all(a["spec"]["project"] == "notebook-rental" for a in apps)
