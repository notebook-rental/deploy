"""overlays/prod/<app>/kustomization.yaml 의 이미지 태그(newTag)를 바꾼다.

코드 repo의 release 워크플로(.github/workflows/release-image.yml)가 이미지를 push 한 뒤 부른다.
kustomize를 설치하지 않아도 되고, 파일의 설명 주석을 그대로 남기려고 newTag 한 줄만 고친다.

    python -m tools.bump_image_tag api 3f2c9e1...
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

APPS = ("api", "web", "ml-inference")
IMAGE_PREFIX = "ghcr.io/notebook-rental/"
# 태그로 쓸 수 있는 글자(도커 규칙): 영문·숫자·_ . - , 첫 글자는 . - 불가, 최대 128자.
TAG_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$")


def kustomization_path(root: Path, app: str) -> Path:
    return root / "overlays" / "prod" / app / "kustomization.yaml"


def bump(text: str, image: str, tag: str) -> str:
    """text 안에서 `- name: <image>` 바로 다음 줄의 newTag 값을 tag로 바꾼 결과를 돌려준다."""
    if not TAG_RE.match(tag):
        raise ValueError(f"태그로 쓸 수 없는 값입니다: {tag!r}")
    pattern = re.compile(r"(- name: " + re.escape(image) + r"[ \t]*\n[ \t]+newTag: )(\S+)")
    new, count = pattern.subn(lambda m: m.group(1) + tag, text)
    if count != 1:
        raise ValueError(f"{image}의 newTag를 정확히 한 곳에서 찾지 못했습니다({count}곳)")
    return new


def main(argv: list[str] | None = None, root: Path | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    root = root or Path(__file__).resolve().parent.parent
    if len(args) != 2:
        print("사용법: python -m tools.bump_image_tag <api|web|ml-inference> <태그>")
        return 2
    app, tag = args
    if app not in APPS:
        print(f"알 수 없는 앱: {app} ({', '.join(APPS)} 중 하나)")
        return 2
    path = kustomization_path(root, app)
    try:
        old = path.read_text(encoding="utf-8")
        new = bump(old, IMAGE_PREFIX + app, tag)
    except (OSError, ValueError) as err:
        print(f"실패: {err}")
        return 1
    if new == old:
        print(f"{app}: 이미 {tag} 입니다")
        return 0
    path.write_text(new, encoding="utf-8")
    print(f"{app}: newTag → {tag}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
