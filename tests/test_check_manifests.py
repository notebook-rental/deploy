"""tools/check_manifests.py 테스트.

두 종류로 나뉜다.
1. 실제 overlays/prod/* 를 kustomize로 만들어 규칙을 모두 통과하는지(회귀 방지).
2. 일부러 규칙을 어긴 작은 매니페스트를 넣어 검사기가 잡아내는지(검사기 자체의 정확성).
"""

from __future__ import annotations

import copy
import shutil
from pathlib import Path

import pytest

from tools import check_manifests as cm

ROOT = Path(__file__).resolve().parent.parent
needs_kustomize = pytest.mark.skipif(shutil.which("kustomize") is None, reason="kustomize 미설치")


# ---------------------------------------------------------------- 실제 매니페스트

@needs_kustomize
@pytest.mark.parametrize("app", ["api", "web", "ml-inference", "ingress"])
def test_real_overlays_pass_all_checks(app):
    manifests = cm.render(ROOT / "overlays" / "prod" / app)
    assert manifests, "빈 결과"
    assert cm.run_checks(manifests) == []


@needs_kustomize
def test_default_targets_lists_every_app():
    names = [p.name for p in cm.default_targets(ROOT)]
    assert names == ["api", "ingress", "ml-inference", "web"]


@needs_kustomize
def test_ml_has_no_cpu_limit_and_threads_match_cpu_request():
    manifests = cm.render(ROOT / "overlays" / "prod" / "ml-inference")
    deploy = cm.of_kind(manifests, "Deployment")[0]
    res = deploy["spec"]["template"]["spec"]["containers"][0]["resources"]
    assert "cpu" not in res.get("limits", {})
    config = cm.of_kind(manifests, "ConfigMap")[0]["data"]
    assert config["INFERENCE_THREADS"] == str(res["requests"]["cpu"])
    assert config["OMP_NUM_THREADS"] == str(res["requests"]["cpu"])


@needs_kustomize
def test_ml_gets_only_the_secret_keys_it_needs():
    manifests = cm.render(ROOT / "overlays" / "prod" / "ml-inference")
    c = cm.of_kind(manifests, "Deployment")[0]["spec"]["template"]["spec"]["containers"][0]
    assert not any("secretRef" in s for s in c.get("envFrom", []))
    keys = {e["valueFrom"]["secretKeyRef"]["key"] for e in c["env"] if "valueFrom" in e}
    assert keys == {"MINIO_ENDPOINT", "ML_MINIO_ACCESS_KEY", "ML_MINIO_SECRET_KEY", "INTERNAL_TOKEN"}


@needs_kustomize
def test_api_network_policy_allows_ingress_controller_and_ml():
    manifests = cm.render(ROOT / "overlays" / "prod" / "api")
    policy = cm.of_kind(manifests, "NetworkPolicy")[0]
    peers = policy["spec"]["ingress"][0]["from"]
    ml_peer = [p for p in peers if "matchLabels" in p.get("podSelector", {})][0]
    # kustomize labels 변환이 이 선택자를 api로 덮어쓰지 않았는지 확인(실제로 겪은 문제).
    assert ml_peer["podSelector"]["matchLabels"] == {"app.kubernetes.io/name": "ml-inference"}
    assert "namespaceSelector" not in ml_peer


@needs_kustomize
def test_api_uses_pull_secret_and_app_secret():
    manifests = cm.render(ROOT / "overlays" / "prod" / "api")
    spec = cm.of_kind(manifests, "Deployment")[0]["spec"]["template"]["spec"]
    assert spec["imagePullSecrets"] == [{"name": "ghcr-pull"}]
    refs = [s["secretRef"]["name"] for s in spec["containers"][0]["envFrom"] if "secretRef" in s]
    assert refs == ["notebook-rental-secrets"]


@needs_kustomize
def test_ingress_routes_and_host():
    manifests = cm.render(ROOT / "overlays" / "prod" / "ingress")
    routes = {}
    for ing in cm.of_kind(manifests, "Ingress"):
        for rule in ing["spec"]["rules"]:
            assert rule["host"] == "notebook-rental.java21.net"
            for p in rule["http"]["paths"]:
                routes[p["path"]] = p["backend"]["service"]["name"]
    assert routes == {"/": "web", "/api": "api", "/api/v1/inspections": "api"}


@needs_kustomize
def test_argocd_apps_point_at_existing_overlays():
    manifests = cm.render(ROOT / "argocd" / "apps")
    apps = cm.of_kind(manifests, "Application")
    assert len(apps) == 4
    for app in apps:
        spec = app["spec"]
        assert (ROOT / spec["source"]["path"] / "kustomization.yaml").exists()
        assert spec["destination"]["namespace"] == "notebook-rental"
        assert spec["syncPolicy"]["automated"] == {"prune": True, "selfHeal": True}


def test_root_application_points_at_child_folder():
    root_app = cm.load_manifests((ROOT / "argocd" / "notebook-rental.yaml").read_text())[0]
    assert root_app["spec"]["source"]["path"] == "argocd/apps"
    assert (ROOT / "argocd" / "apps" / "kustomization.yaml").exists()


def test_repo_contains_no_secret_manifests():
    """repo 어디에도 kind: Secret YAML이 없어야 한다(public repo)."""
    for path in ROOT.rglob("*.yaml"):
        if ".git" in path.parts or ".venv" in path.parts:
            continue
        for doc in cm.load_manifests(path.read_text()):
            assert isinstance(doc, dict)
            assert doc.get("kind") != "Secret", path


# ---------------------------------------------------------------- 작은 가짜 매니페스트

def good_set() -> list[dict]:
    labels = {"app.kubernetes.io/name": "ml-inference"}
    meta = lambda name: {"name": name, "namespace": "notebook-rental"}  # noqa: E731
    return [
        {
            "kind": "Deployment",
            "metadata": meta("ml-inference"),
            "spec": {"template": {
                "metadata": {"labels": labels},
                "spec": {
                    "securityContext": {"runAsNonRoot": True},
                    "containers": [{
                        "name": "ml",
                        "image": "ghcr.io/notebook-rental/ml-inference:abc123",
                        "readinessProbe": {}, "livenessProbe": {},
                        "resources": {"requests": {"cpu": "4", "memory": "4Gi"}, "limits": {"memory": "4Gi"}},
                        "securityContext": {"allowPrivilegeEscalation": False},
                        "envFrom": [{"secretRef": {"name": "notebook-rental-secrets"}}],
                        "env": [{"name": "X", "valueFrom": {"secretKeyRef": {"name": "notebook-rental-secrets", "key": "X"}}},
                                {"name": "PLAIN", "value": "1"}],
                    }],
                },
            }},
        },
        {"kind": "Service", "metadata": meta("ml-inference"), "spec": {"selector": labels}},
        {"kind": "Service", "metadata": meta("ml-inference-headless"),
         "spec": {"clusterIP": "None", "selector": labels}},
        {"kind": "NetworkPolicy", "metadata": meta("ml-inference"),
         "spec": {"podSelector": {"matchLabels": labels}}},
        {"kind": "ConfigMap", "metadata": meta("cfg"), "data": {"APP_PROFILE": "prod"}},
        {
            "kind": "Ingress",
            "metadata": {**meta("main"), "annotations": {
                "nginx.ingress.kubernetes.io/proxy-body-size": "20m",
                "nginx.ingress.kubernetes.io/proxy-buffering": "off",
                "nginx.ingress.kubernetes.io/proxy-read-timeout": "3600"}},
            "spec": {"ingressClassName": "nginx", "rules": [{"host": "h", "http": {"paths": [
                {"path": "/api/v1/inspections"}, {"path": "/"}]}}]},
        },
    ]


def find(ms, kind, name=None):
    return [m for m in ms if m["kind"] == kind and (name is None or m["metadata"]["name"] == name)][0]


def container(ms):
    return find(ms, "Deployment")["spec"]["template"]["spec"]["containers"][0]


def test_good_set_passes():
    assert cm.run_checks(good_set()) == []


def broken(mutate):
    ms = copy.deepcopy(good_set())
    mutate(ms)
    return cm.run_checks(ms)


@pytest.mark.parametrize("mutate, expected", [
    (lambda ms: ms.append({"kind": "Secret", "metadata": {"name": "s", "namespace": "notebook-rental"}}),
     "Secret은 이 repo에"),
    (lambda ms: container(ms)["env"].append({"name": "DB_PASSWORD", "value": "x"}), "값을 직접"),
    (lambda ms: find(ms, "ConfigMap")["data"].update({"JWT_SECRET": "x"}), "비밀값으로 보이는 키"),
    (lambda ms: container(ms)["envFrom"].append({"secretRef": {"name": "other"}}), "모르는 Secret other"),
    (lambda ms: container(ms)["env"].append(
        {"name": "Y", "valueFrom": {"secretKeyRef": {"name": "other2", "key": "Y"}}}), "모르는 Secret other2"),
    (lambda ms: find(ms, "Deployment")["spec"]["template"]["spec"].update(
        {"imagePullSecrets": [{"name": "docker"}]}), "모르는 imagePullSecret"),
    (lambda ms: container(ms).update({"image": "docker.io/x:1"}), "에 있지 않습니다"),
    (lambda ms: container(ms).update({"image": "ghcr.io/notebook-rental/ml-inference"}), "태그가 없습니다"),
    (lambda ms: container(ms).update({"image": "ghcr.io/notebook-rental/ml-inference:latest"}), "latest"),
    (lambda ms: find(ms, "ConfigMap")["metadata"].update({"namespace": "default"}), "네임스페이스가 'default'"),
    (lambda ms: ms.append({"kind": "Namespace", "metadata": {"name": "x"}}), "클러스터 전체"),
    (lambda ms: container(ms).pop("readinessProbe"), "readinessProbe가 없습니다"),
    (lambda ms: container(ms)["resources"]["requests"].pop("cpu"), "requests.cpu"),
    (lambda ms: container(ms)["resources"]["limits"].pop("memory"), "limits.memory"),
    (lambda ms: container(ms)["resources"]["limits"].update({"cpu": "2"}), "throttling"),
    (lambda ms: find(ms, "Deployment")["spec"]["template"]["spec"].pop("securityContext"), "runAsNonRoot"),
    (lambda ms: container(ms).pop("securityContext"), "allowPrivilegeEscalation"),
    (lambda ms: ms.remove(find(ms, "NetworkPolicy")), "NetworkPolicy가 없습니다"),
    (lambda ms: find(ms, "Service", "ml-inference")["spec"].update({"selector": {"a": "b"}}), "맞는 파드가 없습니다"),
    (lambda ms: find(ms, "Service", "ml-inference-headless")["spec"].pop("clusterIP"), "clusterIP: None"),
    (lambda ms: ms.remove(find(ms, "Service", "ml-inference-headless")), "ml-inference-headless가 없습니다"),
    (lambda ms: find(ms, "Ingress")["spec"].update({"ingressClassName": "traefik"}), "ingressClassName"),
    (lambda ms: find(ms, "Ingress")["metadata"]["annotations"].pop(
        "nginx.ingress.kubernetes.io/proxy-body-size"), "proxy-body-size"),
    (lambda ms: find(ms, "Ingress")["spec"]["rules"][0].pop("host"), "host(도메인)"),
    (lambda ms: find(ms, "Ingress")["spec"]["rules"][0]["http"]["paths"].append({"path": "/internal"}),
     "밖에 열면 안 됩니다"),
    (lambda ms: find(ms, "Ingress")["metadata"]["annotations"].pop(
        "nginx.ingress.kubernetes.io/proxy-buffering"), "proxy-buffering"),
    (lambda ms: find(ms, "Ingress")["metadata"]["annotations"].update(
        {"nginx.ingress.kubernetes.io/proxy-read-timeout": "60"}), "proxy-read-timeout"),
    (lambda ms: find(ms, "Ingress")["spec"]["rules"][0]["http"]["paths"].pop(0), "SSE 경로"),
])
def test_each_rule_catches_its_violation(mutate, expected):
    problems = broken(mutate)
    assert any(expected in p for p in problems), problems


def test_digest_pinned_image_is_allowed():
    ms = good_set()
    container(ms)["image"] = "ghcr.io/notebook-rental/ml-inference@sha256:" + "a" * 64
    assert cm.check_images(ms) == []


def test_cpu_limit_equal_to_request_is_allowed_for_ml():
    ms = good_set()
    container(ms)["resources"]["limits"]["cpu"] = "4"
    assert cm.check_resources(ms) == []


def test_headless_rule_skipped_without_ml():
    ms = [m for m in good_set() if m["kind"] != "Deployment"]
    assert cm.check_ml_headless(ms) == []


def test_load_manifests_skips_empty_documents():
    assert cm.load_manifests("---\n---\nkind: A\n---\n") == [{"kind": "A"}]


# ---------------------------------------------------------------- render / main

def test_render_without_kustomize_raises():
    with pytest.raises(RuntimeError, match="찾을 수 없습니다"):
        cm.render(ROOT, kustomize="kustomize-does-not-exist")


@needs_kustomize
def test_render_failure_raises(tmp_path):
    with pytest.raises(RuntimeError, match="실패"):
        cm.render(tmp_path)  # kustomization.yaml 없음


@needs_kustomize
def test_main_passes_on_real_repo(capsys):
    assert cm.main([], root=ROOT) == 0
    assert "[통과]" in capsys.readouterr().out


@needs_kustomize
def test_main_reports_violations(tmp_path, capsys):
    (tmp_path / "kustomization.yaml").write_text("resources: [cm.yaml]\n")
    (tmp_path / "cm.yaml").write_text(
        "apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: c\ndata:\n  DB_PASSWORD: x\n")
    assert cm.main([str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert "[실패]" in out and "DB_PASSWORD" in out


@needs_kustomize
def test_main_reports_render_errors(tmp_path, capsys):
    assert cm.main([str(tmp_path / "missing")]) == 1
    assert "[실패]" in capsys.readouterr().out


# ---------------------------------------------------------------- Argo CD 프로젝트

def load_project():
    return cm.load_manifests((ROOT / "argocd" / "project.yaml").read_text())[0]


def test_project_allows_repo_and_both_namespaces():
    spec = load_project()["spec"]
    assert spec["sourceRepos"] == ["https://github.com/notebook-rental/deploy.git"]
    namespaces = {d["namespace"] for d in spec["destinations"]
                  if d["server"] == "https://kubernetes.default.svc"}
    assert namespaces == {"argocd", "notebook-rental"}
    assert spec["clusterResourceWhitelist"] == []


def test_project_never_allows_secrets():
    allowed = {(r["group"], r["kind"]) for r in load_project()["spec"]["namespaceResourceWhitelist"]}
    assert ("", "Secret") not in allowed


@needs_kustomize
def test_every_rendered_kind_is_allowed_by_project():
    """새 리소스 종류(HPA 등)를 추가하면 프로젝트 허용 목록도 같이 고쳐야 sync가 된다."""
    allowed = {(r["group"], r["kind"]) for r in load_project()["spec"]["namespaceResourceWhitelist"]}
    targets = cm.default_targets(ROOT) + [ROOT / "argocd" / "apps"]
    for target in targets:
        for m in cm.render(target):
            group = m["apiVersion"].split("/")[0] if "/" in m["apiVersion"] else ""
            assert (group, m["kind"]) in allowed, f"{target.name}: {m['kind']}"


@needs_kustomize
def test_all_applications_use_project_and_allowed_destinations():
    spec = load_project()["spec"]
    dests = {(d["server"], d["namespace"]) for d in spec["destinations"]}
    apps = cm.render(ROOT / "argocd" / "apps")
    apps.append(cm.load_manifests((ROOT / "argocd" / "notebook-rental.yaml").read_text())[0])
    for app in apps:
        assert app["metadata"]["namespace"] == "argocd"
        assert app["spec"]["project"] == "notebook-rental"
        d = app["spec"]["destination"]
        assert (d["server"], d["namespace"]) in dests
        assert app["spec"]["source"]["repoURL"] in spec["sourceRepos"]


@needs_kustomize
def test_api_routes_db_host_to_internal_address():
    """DB 호스트 s3.java21.net 은 api 파드 안에서 10.116.64.13 으로 가야 한다."""
    manifests = cm.render(ROOT / "overlays" / "prod" / "api")
    spec = cm.of_kind(manifests, "Deployment")[0]["spec"]["template"]["spec"]
    assert spec["hostAliases"] == [
        {"ip": "10.116.64.13", "hostnames": ["s3.java21.net"]},
        {"ip": "10.116.64.20", "hostnames": ["storage.java21.net"]},
    ]
    # 이미지 받기용 비밀값 패치도 그대로 남아 있어야 한다.
    assert spec["imagePullSecrets"] == [{"name": "ghcr-pull"}]


@needs_kustomize
def test_ml_routes_minio_host_to_internal_address():
    """ml은 MinIO만 쓴다: storage.java21.net → 10.116.64.20."""
    manifests = cm.render(ROOT / "overlays" / "prod" / "ml-inference")
    spec = cm.of_kind(manifests, "Deployment")[0]["spec"]["template"]["spec"]
    assert spec["hostAliases"] == [{"ip": "10.116.64.20", "hostnames": ["storage.java21.net"]}]
