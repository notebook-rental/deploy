# notebook-rental/deploy

노트북 대여 반납 검사 서비스를 Kubernetes에 올리는 설정을 모아 둔 저장소(repo)다.
**이 repo의 `main` 브랜치에 있는 내용이 곧 운영 클러스터의 상태**가 되도록 Argo CD가 계속 맞춘다.

> 이 repo는 **공개(Public)** 다. 비밀번호, 접근 키, 토큰은 어떤 파일에도 넣지 않는다.
> 내부 IP도 넣지 않는 것이 원칙이고, 예외는 DB 라우팅용 `hostAliases` 하나뿐이다(사설 주소 10.x라 밖에서는 닿지 않음, `overlays/prod/api`).
> 비밀값은 Rancher에서 직접 만드는 Secret에만 있다 → [docs/secrets.md](docs/secrets.md)

설계 근거는 docs repo의 `specs/001-return-damage-inspection/research.md` R16과 `infra/README.md`에 있다.

---

## 1. 먼저 알아 둘 용어

| 용어 | 쉬운 설명 | 이 프로젝트에서 |
|------|-----------|-----------------|
| 컨테이너 이미지 | 프로그램과 실행에 필요한 것을 한 덩어리로 묶은 파일 | `ghcr.io/notebook-rental/api` 등 3개 |
| Kubernetes(쿠버네티스) | 여러 서버 위에서 컨테이너를 띄우고, 죽으면 다시 띄우고, 개수를 맞춰 주는 시스템 | 운영 클러스터 |
| Pod(파드) | 컨테이너가 실제로 돌아가는 가장 작은 단위 | api 파드 2개, ml 파드 2개, web 파드 2개 |
| Deployment | "이 이미지로 파드를 N개 유지해라"라는 선언. 새 이미지로 바꾸면 하나씩 교체(롤링 업데이트) | `api`, `web`, `ml-inference` |
| Service | 파드 여러 개 앞에 붙는 고정된 이름과 주소. 파드가 바뀌어도 이름은 그대로 | `http://api:8080`, `http://ml-inference:8000` |
| headless Service | 대표 주소 대신 **모든 파드의 주소 목록**을 알려 주는 Service | `ml-inference-headless` (api가 모든 ml 파드에 알림을 보낼 때) |
| Ingress | 클러스터 밖에서 들어온 HTTP 요청을 경로별로 Service에 나눠 주는 규칙 | `/` → web, `/api` → api |
| Ingress Controller | Ingress 규칙을 실제로 수행하는 프로그램 | ingress-nginx (`ingressClassName: nginx`) |
| ConfigMap | 비밀이 아닌 설정값 묶음 | `SPRING_PROFILES_ACTIVE=prod` 등 |
| Secret | 비밀값 묶음 | `notebook-rental-secrets` (**Rancher에서 직접 생성**, 이 repo에 없음) |
| NetworkPolicy | 파드끼리 누가 누구에게 연결할 수 있는지 정하는 방화벽 규칙 | ml 파드는 api만 부를 수 있음 |
| PodDisruptionBudget(PDB) | 서버 점검으로 파드를 옮길 때도 최소 몇 개는 살려 두라는 규칙 | 앱마다 최소 1개 |
| Namespace | 클러스터 안의 칸막이. 이름이 같은 리소스도 칸이 다르면 따로 존재 | `notebook-rental` |
| Kustomize | YAML을 복사하지 않고 "기본(base) + 환경별 변경(overlay)"으로 조립하는 도구 | `apps/*/base` + `overlays/prod/*` |
| GitOps | 클러스터를 손으로 바꾸지 않고 git에 커밋해서 바꾸는 방식. 기록이 남고 되돌리기 쉽다 | 이 repo |
| Argo CD | git repo를 감시하다가 바뀌면 클러스터에 반영하는 프로그램 | 이 repo의 `main`을 감시 |
| Rancher | Kubernetes 클러스터를 웹 화면으로 관리하는 도구 | 네임스페이스, Secret 생성 |
| ghcr.io | GitHub이 제공하는 컨테이너 이미지 저장소 | 이미지 push·pull |

---

## 2. 전체 그림

```
직원 폰 ─▶ 외부 Nginx(marco 관리: 도메인, HTTPS) ─▶ ingress-nginx ─┬─ /     → Service web → web 파드
                                                                 └─ /api  → Service api → api 파드
클러스터 안에서만:  api ⇄ ml-inference  (/internal/** 는 Ingress에 없다)
클러스터 밖(이미 있음): PostgreSQL, MinIO(storage.java21.net)
```

배포가 일어나는 순서:

```
1. 개발자가 api/web/ml repo의 main에 push
2. 그 repo의 GitHub Actions: 테스트 → 이미지 빌드 → ghcr.io/notebook-rental/<앱>:<git SHA> 로 push
3. 같은 워크플로가 이 repo의 overlays/prod/<앱>/kustomization.yaml 의 newTag 를 그 SHA로 바꿔 커밋
4. Argo CD가 바뀐 것을 보고 클러스터에 반영(새 파드가 Ready가 된 뒤 옛 파드를 내림)
```

되돌리기는 3번 커밋을 `git revert` 하면 된다. Argo CD가 예전 이미지로 다시 맞춘다.

---

## 3. 폴더 구조

```
deploy/
├── apps/                         # 앱마다 "기본" 설정(환경과 무관한 것)
│   ├── api/base/                 #   Deployment, Service, ConfigMap, PDB, NetworkPolicy
│   ├── web/base/                 #   Deployment, Service, PDB, NetworkPolicy
│   ├── ml-inference/base/        #   + headless Service
│   └── ingress/base/             #   Ingress 2개(기본, SSE 전용)
├── overlays/prod/                # 운영 환경에서 바꾸는 것
│   ├── api/                      #   네임스페이스, 이미지 태그, ghcr-pull
│   ├── web/
│   ├── ml-inference/
│   └── ingress/                  #   도메인(host)
├── argocd/
│   ├── project.yaml              # Argo CD 프로젝트(허용 repo·네임스페이스·리소스 종류), 처음 한 번 적용
│   ├── notebook-rental.yaml      # 처음 한 번 등록하는 부모 Application
│   └── apps/                     # 부모가 만드는 자식 Application 4개
├── .github/workflows/
│   ├── validate.yml              # 이 repo 검사(테스트, 스키마, 규칙)
│   └── release-image.yml         # 코드 repo가 불러 쓰는 이미지 빌드·태그 갱신 워크플로
├── tools/                        # 규칙 검사기, 태그 갱신 스크립트(Python)
├── tests/                        # 위 도구의 테스트
└── docs/                         # 설명 문서
```

**왜 앱마다 overlay를 나눴나?** Argo CD Application을 앱마다 하나씩 두면 화면에서 api, web, ml 상태를 따로 보고,
하나만 되돌리거나 다시 맞출 수 있다. CI도 자기 앱 파일만 고치므로 동시에 배포해도 충돌이 적다.

---

## 4. 처음 설치 순서 (한 번만)

| 순서 | 할 일 | 문서 |
|------|------|------|
| 1 | Rancher에서 네임스페이스 `notebook-rental` 만들기 | [docs/secrets.md](docs/secrets.md) 1절 |
| 2 | Rancher에서 Secret `notebook-rental-secrets` 만들기 | [docs/secrets.md](docs/secrets.md) 2절 |
| 3 | (api repo가 Private인 동안) Secret `ghcr-pull` 만들기 | [docs/secrets.md](docs/secrets.md) 3절 |
| 4 | 외부 Nginx에 프록시 설정 | [docs/external-nginx.md](docs/external-nginx.md) |
| 5 | 코드 repo에 `DEPLOY_REPO_TOKEN` 등록, release 워크플로 추가 | [docs/ci-release.md](docs/ci-release.md) |
| 6 | Argo CD에 프로젝트 `notebook-rental`과 부모 Application 등록 | [docs/argocd.md](docs/argocd.md) 2절 |
| 7 | 첫 이미지 push 후 ghcr 패키지를 Public으로 | [docs/ci-release.md](docs/ci-release.md) 4절 |

> 6번을 먼저 해도 괜찮다. 코드 repo가 아직 이미지를 만들지 않았으면 태그가 `bootstrap`이라
> 파드가 `ImagePullBackOff`(이미지를 못 받음) 상태로 기다린다. 첫 이미지가 올라오면 저절로 풀린다.

각 이미지가 지켜야 할 약속(포트, 상태 확인 주소, 환경변수, 쓰기 가능한 폴더)은 [docs/image-contract.md](docs/image-contract.md)에 있다.
문제가 생기면 [docs/troubleshooting.md](docs/troubleshooting.md)를 본다.

---

## 5. 이 repo를 고칠 때 로컬에서 확인하기

필요한 것: Python 3.11 이상, [kustomize](https://kubectl.docs.kubernetes.io/installation/kustomize/) 5.x, (선택) [kubeconform](https://github.com/yannh/kubeconform)

```bash
pip install -r requirements-dev.txt

pytest                                   # 도구 테스트 + 커버리지(80% 미만이면 실패)
python -m tools.check_manifests          # overlays/prod/* 가 프로젝트 규칙을 지키는지
kustomize build overlays/prod/api        # 실제로 클러스터에 들어갈 YAML 보기
kustomize build overlays/prod/api | kubeconform -strict -summary -   # 문법(스키마) 검사
```

PR을 열면 GitHub Actions(`validate`)가 위 검사를 모두 돌린다.

`tools/check_manifests.py`가 확인하는 규칙:

- Secret 객체를 repo에 넣지 않았는가, 비밀값처럼 보이는 환경변수에 값을 직접 적지 않았는가
- 참조하는 Secret 이름이 `notebook-rental-secrets`, `ghcr-pull` 뿐인가
- 이미지가 `ghcr.io/notebook-rental/*` 이고 태그가 있으며 `latest`가 아닌가
- 모든 리소스가 `notebook-rental` 네임스페이스에 있는가
- 모든 컨테이너에 readiness·liveness 검사, CPU·메모리 requests, 메모리 limit, 비루트 실행이 있는가
- ml-inference에 CPU limit이 없는가(있으면 requests와 같은가) — 추론 속도 때문
- Deployment마다 NetworkPolicy가 있는가, Service selector가 실제 파드에 맞는가
- ml-inference headless Service가 있는가
- Ingress가 `nginx` 클래스, 업로드 20m, `/internal` 미등록, SSE 경로 버퍼링 끄기·3600초인가

---

## 6. 자원(CPU·메모리) 시작 값

| 앱 | 파드 수 | CPU requests | CPU limits | 메모리 requests | 메모리 limits |
|----|---------|--------------|------------|-----------------|---------------|
| api | 2 | 1 | 없음 | 1Gi | 1Gi |
| ml-inference | 2 | 4 | **없음** | 4Gi | 4Gi |
| web | 2 | 0.1 | 없음 | 64Mi | 128Mi |

- **requests**: "이만큼은 꼭 확보해 달라". 스케줄러가 이 값을 보고 파드를 놓을 서버를 고른다.
- **limits**: "이 이상은 못 쓰게 막아라". CPU limit에 걸리면 프로그램이 잠깐씩 멈춰서(throttling)
  추론 시간이 몇 배로 튈 수 있다. 그래서 CPU limit은 두지 않는다. 메모리는 넘치면 다른 파드를 밀어내므로 limit을 둔다.
- ml의 `INFERENCE_THREADS`·`OMP_NUM_THREADS`(추론 스레드 수)는 CPU requests와 같게 맞춘다. 하나를 바꾸면 둘 다 바꾼다
  (테스트가 이 둘이 같은지 확인한다).
- 실제 속도를 재 본 뒤(docs tasks T123) 조정한다. 바꿀 때는 `overlays/prod/<앱>/kustomization.yaml`에 패치를 추가한다.
