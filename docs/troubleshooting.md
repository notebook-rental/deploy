# 문제 해결

파드 상태는 Rancher → **Workloads → Pods** (네임스페이스 `notebook-rental`)에서 보고, 파드를 눌러 **Events**와 ⋮ → **View Logs**를 확인한다.
kubectl을 쓴다면:

```bash
kubectl -n notebook-rental get pods
kubectl -n notebook-rental describe pod <파드 이름>    # 맨 아래 Events가 원인을 알려 준다
kubectl -n notebook-rental logs <파드 이름> --previous # 직전에 죽은 컨테이너의 로그
```

## 파드 상태별 원인

| 상태 | 뜻 | 흔한 원인과 해결 |
|------|----|------------------|
| `ImagePullBackOff` / `ErrImagePull` | 이미지를 못 받음 | ① 태그가 아직 `bootstrap` — 코드 repo의 첫 release가 끝나면 풀린다. ② 패키지가 Private — Public으로 바꾸거나 `ghcr-pull` Secret 확인([secrets.md](secrets.md) 3절). ③ 태그 오타 |
| `CreateContainerConfigError` | 컨테이너 설정을 만들지 못함 | Secret `notebook-rental-secrets`가 없거나 키가 빠졌다. Events에 `couldn't find key ML_MINIO_ACCESS_KEY` 처럼 빠진 키 이름이 나온다 |
| `CrashLoopBackOff` | 시작했다가 계속 죽음 | 로그(`--previous`) 확인. api는 DB 접속 실패(`DB_URL`, 방화벽), ml은 MinIO 접속·모델 다운로드 실패가 흔하다. api 파드에서 DB 호스트 `s3.java21.net`은 `hostAliases`로 `10.116.64.13`에 고정돼 있다(`kubectl -n notebook-rental exec deploy/api -- cat /etc/hosts`로 확인). DB 서버 주소가 바뀌면 `overlays/prod/api/kustomization.yaml`의 이 값을 고친다 |
| `Running`인데 `0/1 Ready` | 준비 확인 통과 못 함 | ml은 모델 로드·워밍업 중이면 정상(몇 분). 계속되면 로그 확인. api는 DB 연결 확인 |
| `OOMKilled` | 메모리 한도 초과로 강제 종료 | 메모리 limit을 늘리거나(overlay 패치) 메모리 사용을 줄인다 |
| `Pending` | 놓을 서버가 없음 | Events에 `Insufficient cpu/memory`. ml은 CPU 4를 요구하므로 노드 여유를 확인 |

## 사이트 접속 문제

| 증상 | 확인할 것 |
|------|-----------|
| 외부 Nginx가 502/504 | ① 외부 Nginx → Ingress 주소가 맞나(`curl -H 'Host: notebook-rental.java21.net' http://<INGRESS_ADDR>/`) ② 파드가 Ready인가 |
| Ingress가 404(nginx 기본 페이지) | 외부 Nginx가 `Host` 헤더를 넘기지 않음 → `proxy_set_header Host $host;` |
| Ingress가 502/504, 파드는 Ready | **NetworkPolicy**가 Ingress Controller를 막는 경우. 아래 참고 |
| 사진 업로드가 413 | 외부 Nginx `client_max_body_size 20m` 빠짐 |
| 검사 결과가 실시간으로 안 뜨고 몰려서 뜸 | 외부 Nginx의 `/api/v1/inspections/` 위치에 `proxy_buffering off` 빠짐 |
| 촬영 중 결과 연결이 1분마다 끊김 | 외부 Nginx `proxy_read_timeout 3600s` 빠짐 |

## NetworkPolicy가 Ingress Controller를 막을 때

api·web의 NetworkPolicy는 이름표 `app.kubernetes.io/name`이 `ingress-nginx` 또는 `rke2-ingress-nginx`인 파드에서 오는 연결만 받는다.
설치 방식에 따라 이름표가 다르거나, Ingress Controller가 `hostNetwork`(노드 네트워크를 그대로 사용)로 떠서 파드 이름표로 구분이 안 될 수 있다.

```bash
# Ingress Controller 파드의 이름표 확인
kubectl get pods -A -l 'app.kubernetes.io/component=controller' --show-labels | grep -i ingress
# hostNetwork 여부 확인(true면 이름표로는 못 고른다)
kubectl get pods -A -o custom-columns=NS:.metadata.namespace,NAME:.metadata.name,HOSTNET:.spec.hostNetwork | grep -i ingress
```

- 이름표가 다르면: `apps/api/base/networkpolicy.yaml`, `apps/web/base/networkpolicy.yaml`의 `values`에 그 이름을 추가하고 테스트를 돌린 뒤 PR
- `hostNetwork: true`면: 이름표 대신 노드 IP 대역을 허용해야 한다. 내부 IP는 공개 repo에 넣지 않기로 했으므로, 그 경우 알려 주면 방법을 같이 정한다

## Argo CD 문제

| 증상 | 확인할 것 |
|------|-----------|
| 자식 Application이 안 생김 | 부모 `notebook-rental`이 Synced인가, Argo CD가 `argocd` 네임스페이스에 있나 |
| `namespaces "notebook-rental" not found` | Rancher에서 네임스페이스를 먼저 만든다([secrets.md](secrets.md) 1절) |
| 계속 OutOfSync | 앱 → DIFF. 클러스터에서 누가 바꾼 값이면 selfHeal이 되돌린다. 다른 컨트롤러가 값을 바꾸는 필드면 알려 준다 |
| 코드는 push 했는데 배포가 안 됨 | [ci-release.md](ci-release.md) 5절 |
