"""배포 매니페스트 규칙 검사기.

`kustomize build` 로 만든 최종 YAML을 읽어, 이 프로젝트가 지키기로 한 규칙을 확인한다.
스키마 검사(kubeconform)는 "문법이 맞는가"를 보고, 이 검사기는 "우리 약속을 지켰는가"를 본다.

    python -m tools.check_manifests            # overlays/prod/* 전부 검사
    python -m tools.check_manifests overlays/prod/api

규칙 하나하나는 `check_*` 함수이고, 위반 내용을 문자열 목록으로 돌려준다(없으면 빈 목록).
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable, Iterable

import yaml

NAMESPACE = "notebook-rental"
IMAGE_PREFIX = "ghcr.io/notebook-rental/"
APP_SECRET = "notebook-rental-secrets"
PULL_SECRET = "ghcr-pull"
ML_APP = "ml-inference"
ML_HEADLESS = "ml-inference-headless"
SSE_PATH = "/api/v1/inspections"
INGRESS_CLASS = "nginx"
BODY_SIZE = "20m"
SSE_READ_TIMEOUT = "3600"

ANN = "nginx.ingress.kubernetes.io/"
# 이름만 보고 비밀값일 가능성이 높은 환경변수. 이런 변수에 값을 직접 적으면 안 된다.
SECRET_HINTS = ("PASSWORD", "SECRET", "TOKEN", "ACCESS_KEY", "PRIVATE_KEY")
# 네임스페이스가 없는(클러스터 전체) 리소스 종류.
CLUSTER_SCOPED = {"Namespace", "ClusterRole", "ClusterRoleBinding", "CustomResourceDefinition"}

Manifest = dict
Check = Callable[[list[Manifest]], list[str]]


# ---------------------------------------------------------------- 읽기

def load_manifests(text: str) -> list[Manifest]:
    """여러 문서(---로 구분)가 들어 있는 YAML을 읽는다. 빈 문서는 버린다."""
    return [doc for doc in yaml.safe_load_all(text) if doc]


def render(path: Path, kustomize: str = "kustomize") -> list[Manifest]:
    """`kustomize build <path>` 결과를 읽는다."""
    exe = shutil.which(kustomize)
    if exe is None:
        raise RuntimeError(f"{kustomize} 명령을 찾을 수 없습니다. 설치 후 다시 실행하세요.")
    result = subprocess.run([exe, "build", str(path)], capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"kustomize build {path} 실패:\n{result.stderr}")
    return load_manifests(result.stdout)


# ---------------------------------------------------------------- 도우미

def name_of(m: Manifest) -> str:
    return f"{m.get('kind')}/{m.get('metadata', {}).get('name')}"


def of_kind(manifests: Iterable[Manifest], kind: str) -> list[Manifest]:
    return [m for m in manifests if m.get("kind") == kind]


def pod_spec(deploy: Manifest) -> dict:
    return deploy.get("spec", {}).get("template", {}).get("spec", {}) or {}


def pod_labels(deploy: Manifest) -> dict:
    return deploy.get("spec", {}).get("template", {}).get("metadata", {}).get("labels", {}) or {}


def containers(deploy: Manifest) -> list[dict]:
    spec = pod_spec(deploy)
    return list(spec.get("containers", []) or []) + list(spec.get("initContainers", []) or [])


def matches(selector: dict, labels: dict) -> bool:
    """matchLabels 형태(또는 Service의 단순 selector)가 labels에 들어맞는지."""
    return bool(selector) and all(labels.get(k) == v for k, v in selector.items())


# ---------------------------------------------------------------- 규칙

def check_no_secret_objects(manifests: list[Manifest]) -> list[str]:
    """Secret 객체를 repo에 넣지 않는다. 값은 Rancher에서 직접 만든다."""
    return [f"{name_of(m)}: Secret은 이 repo에 넣지 않습니다(Rancher에서 직접 생성)"
            for m in of_kind(manifests, "Secret")]


def check_no_inline_secrets(manifests: list[Manifest]) -> list[str]:
    """비밀값처럼 보이는 환경변수·ConfigMap 키에 값을 직접 적지 않는다."""
    problems = []
    for deploy in of_kind(manifests, "Deployment"):
        for c in containers(deploy):
            for env in c.get("env", []) or []:
                key = str(env.get("name", "")).upper()
                if "value" in env and any(h in key for h in SECRET_HINTS):
                    problems.append(f"{name_of(deploy)}: 환경변수 {env['name']}에 값을 직접 적었습니다")
    for cm in of_kind(manifests, "ConfigMap"):
        for key in (cm.get("data") or {}):
            if any(h in key.upper() for h in SECRET_HINTS):
                problems.append(f"{name_of(cm)}: 비밀값으로 보이는 키 {key}는 Secret에 두세요")
    return problems


def check_secret_references(manifests: list[Manifest]) -> list[str]:
    """참조하는 Secret 이름은 약속한 것(notebook-rental-secrets, ghcr-pull)뿐이어야 한다."""
    problems = []
    for deploy in of_kind(manifests, "Deployment"):
        spec = pod_spec(deploy)
        for ref in spec.get("imagePullSecrets", []) or []:
            if ref.get("name") != PULL_SECRET:
                problems.append(f"{name_of(deploy)}: 모르는 imagePullSecret {ref.get('name')}")
        for c in containers(deploy):
            for src in c.get("envFrom", []) or []:
                ref = src.get("secretRef")
                if ref and ref.get("name") != APP_SECRET:
                    problems.append(f"{name_of(deploy)}: 모르는 Secret {ref.get('name')}")
            for env in c.get("env", []) or []:
                ref = (env.get("valueFrom") or {}).get("secretKeyRef")
                if ref and ref.get("name") != APP_SECRET:
                    problems.append(f"{name_of(deploy)}: 모르는 Secret {ref.get('name')}")
    return problems


def check_images(manifests: list[Manifest]) -> list[str]:
    """이미지는 ghcr.io/notebook-rental/* 이고, 태그가 있으며 latest가 아니어야 한다."""
    problems = []
    for deploy in of_kind(manifests, "Deployment"):
        for c in containers(deploy):
            image = str(c.get("image", ""))
            if not image.startswith(IMAGE_PREFIX):
                problems.append(f"{name_of(deploy)}: 이미지 {image!r}가 {IMAGE_PREFIX}에 있지 않습니다")
                continue
            repo_part = image[len(IMAGE_PREFIX):]
            if "@" in repo_part:
                continue  # sha256 다이제스트로 고정한 경우
            if ":" not in repo_part:
                problems.append(f"{name_of(deploy)}: 이미지 {image}에 태그가 없습니다")
            elif repo_part.rsplit(":", 1)[1] == "latest":
                problems.append(f"{name_of(deploy)}: latest 태그는 되돌리기가 안 돼서 쓰지 않습니다")
    return problems


def check_namespace(manifests: list[Manifest]) -> list[str]:
    """네임스페이스가 있는 리소스는 모두 notebook-rental 에 들어간다."""
    problems = []
    for m in manifests:
        if m.get("kind") in CLUSTER_SCOPED:
            problems.append(f"{name_of(m)}: 클러스터 전체 리소스는 이 repo에서 만들지 않습니다")
            continue
        ns = m.get("metadata", {}).get("namespace")
        if ns != NAMESPACE:
            problems.append(f"{name_of(m)}: 네임스페이스가 {ns!r}입니다({NAMESPACE} 이어야 함)")
    return problems


def check_probes(manifests: list[Manifest]) -> list[str]:
    """모든 컨테이너에 readiness·liveness 검사가 있어야 무중단 배포가 된다."""
    problems = []
    for deploy in of_kind(manifests, "Deployment"):
        for c in pod_spec(deploy).get("containers", []) or []:
            for probe in ("readinessProbe", "livenessProbe"):
                if probe not in c:
                    problems.append(f"{name_of(deploy)}/{c.get('name')}: {probe}가 없습니다")
    return problems


def check_resources(manifests: list[Manifest]) -> list[str]:
    """requests(cpu, memory)와 memory limit은 필수. ml-inference는 CPU limit을 두지 않는다."""
    problems = []
    for deploy in of_kind(manifests, "Deployment"):
        is_ml = deploy["metadata"].get("name") == ML_APP
        for c in pod_spec(deploy).get("containers", []) or []:
            res = c.get("resources") or {}
            requests, limits = res.get("requests") or {}, res.get("limits") or {}
            where = f"{name_of(deploy)}/{c.get('name')}"
            for key in ("cpu", "memory"):
                if key not in requests:
                    problems.append(f"{where}: requests.{key}가 없습니다")
            if "memory" not in limits:
                problems.append(f"{where}: limits.memory가 없습니다")
            if is_ml and "cpu" in limits and limits["cpu"] != requests.get("cpu"):
                problems.append(f"{where}: CPU limit이 requests와 다르면 추론이 느려집니다(throttling)")
    return problems


def check_security(manifests: list[Manifest]) -> list[str]:
    """비루트 실행, 권한 상승 금지."""
    problems = []
    for deploy in of_kind(manifests, "Deployment"):
        if not (pod_spec(deploy).get("securityContext") or {}).get("runAsNonRoot"):
            problems.append(f"{name_of(deploy)}: runAsNonRoot: true가 없습니다")
        for c in pod_spec(deploy).get("containers", []) or []:
            if (c.get("securityContext") or {}).get("allowPrivilegeEscalation") is not False:
                problems.append(f"{name_of(deploy)}/{c.get('name')}: allowPrivilegeEscalation: false가 없습니다")
    return problems


def check_network_policies(manifests: list[Manifest]) -> list[str]:
    """Deployment마다 그 파드를 고르는 NetworkPolicy가 있어야 한다."""
    policies = of_kind(manifests, "NetworkPolicy")
    problems = []
    for deploy in of_kind(manifests, "Deployment"):
        labels = pod_labels(deploy)
        if not any(matches((p.get("spec", {}).get("podSelector") or {}).get("matchLabels") or {}, labels)
                   for p in policies):
            problems.append(f"{name_of(deploy)}: 이 파드를 고르는 NetworkPolicy가 없습니다")
    return problems


def check_service_selectors(manifests: list[Manifest]) -> list[str]:
    """Service의 selector가 같은 묶음의 Deployment 파드에 실제로 맞아야 한다."""
    deploys = of_kind(manifests, "Deployment")
    problems = []
    for svc in of_kind(manifests, "Service"):
        selector = svc.get("spec", {}).get("selector") or {}
        if not any(matches(selector, pod_labels(d)) for d in deploys):
            problems.append(f"{name_of(svc)}: selector {selector}에 맞는 파드가 없습니다")
    return problems


def check_ml_headless(manifests: list[Manifest]) -> list[str]:
    """ml-inference가 있으면 모든 파드 주소를 주는 headless Service도 있어야 한다."""
    if not any(d["metadata"].get("name") == ML_APP for d in of_kind(manifests, "Deployment")):
        return []
    for svc in of_kind(manifests, "Service"):
        if svc["metadata"].get("name") == ML_HEADLESS:
            if svc.get("spec", {}).get("clusterIP") != "None":
                return [f"{name_of(svc)}: clusterIP: None 이어야 headless입니다"]
            return []
    return [f"Service/{ML_HEADLESS}가 없습니다(api가 모든 ml 파드에 알림을 못 보냄)"]


def check_ingress(manifests: list[Manifest]) -> list[str]:
    """ingress-nginx 클래스, 업로드 20m, /internal 미등록, SSE 경로 버퍼링 끄기."""
    ingresses = of_kind(manifests, "Ingress")
    problems = []
    sse_found = False
    for ing in ingresses:
        spec = ing.get("spec", {})
        ann = ing.get("metadata", {}).get("annotations") or {}
        if spec.get("ingressClassName") != INGRESS_CLASS:
            problems.append(f"{name_of(ing)}: ingressClassName이 {INGRESS_CLASS}가 아닙니다")
        if ann.get(ANN + "proxy-body-size") != BODY_SIZE:
            problems.append(f"{name_of(ing)}: proxy-body-size가 {BODY_SIZE}가 아닙니다")
        for rule in spec.get("rules", []) or []:
            if not rule.get("host"):
                problems.append(f"{name_of(ing)}: host(도메인)가 없습니다")
            for p in (rule.get("http") or {}).get("paths", []) or []:
                path = p.get("path", "")
                if path.startswith("/internal") or "/internal" in path:
                    problems.append(f"{name_of(ing)}: {path}는 밖에 열면 안 됩니다")
                if path == SSE_PATH:
                    sse_found = True
                    if ann.get(ANN + "proxy-buffering") != "off":
                        problems.append(f"{name_of(ing)}: SSE 경로에 proxy-buffering: off가 없습니다")
                    if ann.get(ANN + "proxy-read-timeout") != SSE_READ_TIMEOUT:
                        problems.append(f"{name_of(ing)}: SSE 경로 proxy-read-timeout이 {SSE_READ_TIMEOUT}가 아닙니다")
    if ingresses and not sse_found:
        problems.append(f"Ingress: SSE 경로 {SSE_PATH} 규칙이 없습니다")
    return problems


CHECKS: list[Check] = [
    check_no_secret_objects,
    check_no_inline_secrets,
    check_secret_references,
    check_images,
    check_namespace,
    check_probes,
    check_resources,
    check_security,
    check_network_policies,
    check_service_selectors,
    check_ml_headless,
    check_ingress,
]


def run_checks(manifests: list[Manifest], checks: Iterable[Check] = CHECKS) -> list[str]:
    problems: list[str] = []
    for check in checks:
        problems.extend(check(manifests))
    return problems


def default_targets(root: Path) -> list[Path]:
    return sorted(p for p in (root / "overlays" / "prod").iterdir() if (p / "kustomization.yaml").exists())


def main(argv: list[str] | None = None, root: Path | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    root = root or Path(__file__).resolve().parent.parent
    targets = [Path(a) for a in args] or default_targets(root)
    failed = False
    for target in targets:
        try:
            problems = run_checks(render(target))
        except RuntimeError as err:
            print(f"[실패] {target}: {err}")
            failed = True
            continue
        if problems:
            failed = True
            print(f"[실패] {target}")
            for p in problems:
                print(f"  - {p}")
        else:
            print(f"[통과] {target}")
    return 1 if failed else 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
