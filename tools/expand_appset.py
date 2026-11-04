"""ApplicationSet이 만들 Application들을 로컬에서 미리 펼쳐 본다.

Argo CD의 git 디렉터리 생성기와 같은 규칙으로 overlays/prod/* 폴더를 찾고,
템플릿의 {{.path.path}}, {{.path.basename}} 을 채워 Application 목록을 만든다.
CI는 이 결과를 kubeconform으로 검사해 ApplicationSet 템플릿 오류를 클러스터에 넣기 전에 잡는다.

    python -m tools.expand_appset            # 펼친 Application YAML 출력
"""

from __future__ import annotations

import copy
import fnmatch
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
APPSET = ROOT / "argocd" / "applicationset.yaml"
PLACEHOLDER = re.compile(r"\{\{\s*\.([A-Za-z0-9_.]+)\s*\}\}")


def load_appset(path: Path = APPSET) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def directories(root: Path, pattern: str) -> list[str]:
    """repo 안에서 pattern(예: overlays/prod/*)에 맞는 폴더의 상대 경로 목록."""
    base = pattern.split("*", 1)[0].rstrip("/")
    found = []
    for p in sorted((root / base).iterdir()) if (root / base).is_dir() else []:
        rel = p.relative_to(root).as_posix()
        if p.is_dir() and fnmatch.fnmatch(rel, pattern):
            found.append(rel)
    return found


def fill(value, params: dict):
    """문자열·목록·사전 안의 {{.a.b}} 를 params 값으로 바꾼다. 없는 키는 오류(missingkey=error)."""
    if isinstance(value, str):
        def repl(m):
            cur = params
            for part in m.group(1).split("."):
                if not isinstance(cur, dict) or part not in cur:
                    raise KeyError(f"템플릿 값 {m.group(0)}을 채울 수 없습니다")
                cur = cur[part]
            return str(cur)
        return PLACEHOLDER.sub(repl, value)
    if isinstance(value, list):
        return [fill(v, params) for v in value]
    if isinstance(value, dict):
        return {k: fill(v, params) for k, v in value.items()}
    return value


def expand(appset: dict, root: Path = ROOT) -> list[dict]:
    """ApplicationSet에서 Application 목록을 만든다(git 디렉터리 생성기만 지원)."""
    spec = appset["spec"]
    apps = []
    for gen in spec["generators"]:
        git = gen.get("git")
        if git is None:
            raise ValueError("git 생성기만 지원합니다")
        for d in git["directories"]:
            for rel in directories(root, d["path"]):
                params = {"path": {"path": rel, "basename": rel.rsplit("/", 1)[-1]}}
                tmpl = copy.deepcopy(spec["template"])
                app = {"apiVersion": "argoproj.io/v1alpha1", "kind": "Application", **fill(tmpl, params)}
                app["metadata"].setdefault("namespace", appset["metadata"]["namespace"])
                apps.append(app)
    return apps


def main(argv: list[str] | None = None) -> int:
    apps = expand(load_appset())
    sys.stdout.write(yaml.safe_dump_all(apps, sort_keys=False, allow_unicode=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
