# Argo CD 등록과 사용법

## 1. 구조: 부모 하나, 자식 넷 (app-of-apps)

```
notebook-rental (부모, argocd/notebook-rental.yaml)      ← 처음 한 번만 직접 등록
  └─ argocd/apps/ 를 읽어서 만든다
       ├─ notebook-rental-api           → overlays/prod/api
       ├─ notebook-rental-web           → overlays/prod/web
       ├─ notebook-rental-ml-inference  → overlays/prod/ml-inference
       └─ notebook-rental-ingress       → overlays/prod/ingress
```

**왜 부모를 두나?** 자식 Application 정의도 git에 두고 싶어서다. 앱을 하나 더 만들 때 `argocd/apps/`에 파일만 추가하면
부모가 알아서 등록한다. 클러스터에 직접 손대는 것은 처음 부모 등록 한 번뿐이다.

모든 Application의 동기화 설정:

| 설정 | 뜻 |
|------|----|
| `automated` | `main`이 바뀌면 사람이 누르지 않아도 반영 |
| `prune: true` | repo에서 지운 리소스는 클러스터에서도 지움 |
| `selfHeal: true` | 누가 클러스터에서 손으로 바꾸면 repo 상태로 되돌림 |
| `CreateNamespace=false` | 네임스페이스는 Rancher에서 미리 만든다([secrets.md](secrets.md) 1절) |
| `retry` | 실패하면 10초, 20초, 40초… 간격으로 최대 5번 다시 시도 |

## 2. 등록하기 (한 번만)

전제: Argo CD가 `argocd` 네임스페이스에 설치돼 있다(일반적인 설치 위치). 다른 곳이면 모든 `argocd/*.yaml`의
`metadata.namespace`를 그 이름으로 바꾼다.

repo가 공개라 Argo CD에 repo 접속 정보(비밀번호)를 따로 등록할 필요가 없다.

순서는 **① 프로젝트 → ② 부모 앱**이다. 모든 앱이 프로젝트 `notebook-rental`에 속하므로 프로젝트가 먼저 있어야 한다.

### ① 프로젝트 `notebook-rental` (AppProject)

> **프로젝트(AppProject)**: Argo CD 앱 여러 개를 묶고 "어느 repo를 읽고, 어느 클러스터·네임스페이스에, 어떤 종류의 리소스를 만들 수 있는지"를 정하는 울타리.
> 이 서비스 앱이 실수로 다른 네임스페이스나 클러스터 전체 설정을 건드리지 못하게 한다.

[`argocd/project.yaml`](../argocd/project.yaml)에 정의돼 있다.

| 항목 | 값 | 이유 |
|------|----|------|
| Source repositories | `https://github.com/notebook-rental/deploy.git` | 이 repo만 읽는다 |
| Destinations | `https://kubernetes.default.svc` + 네임스페이스 `argocd` | 부모 앱이 자식 Application을 만드는 곳 |
| | `https://kubernetes.default.svc` + 네임스페이스 `notebook-rental` | 자식 앱이 실제 서비스를 배포하는 곳 |
| Cluster resource allow list | 없음 | Namespace, ClusterRole 같은 클러스터 전체 리소스는 만들지 않는다 |
| Namespace resource allow list | Application, ConfigMap, Service, Deployment, PodDisruptionBudget, NetworkPolicy, Ingress | 이 repo가 쓰는 종류만. **Secret은 일부러 뺐다**(Rancher에서 직접 관리) |

이 파일은 부모 앱이 관리하지 않는다(`argocd/apps` 밖에 있음). 앱이 자기 울타리를 스스로 넓힐 수 없게 하려는 것이다.
그래서 프로젝트를 바꿀 때는 이 파일을 고친 뒤 **다시 직접 적용**한다.

kubectl로 적용(Rancher **Kubectl Shell** 또는 내 PC):

```bash
kubectl apply -n argocd -f https://raw.githubusercontent.com/notebook-rental/deploy/main/argocd/project.yaml
```

화면에서 이미 같은 이름의 프로젝트를 만들었어도 위 명령을 쓰면 된다. 파일에 있는 항목은 파일 값으로 바뀐다.
다만 파일에 없는 항목(예: 화면에서 넣은 DESTINATION SERVICE ACCOUNTS)은 그대로 남으니 아래 설명대로 화면에서 지운다.
화면에서 직접 채우려면 Settings → Projects → `notebook-rental`에서 위 표대로 넣는다.

**DESTINATION SERVICE ACCOUNTS는 비워 둔다.** 이 항목은 Argo CD가 "다른 서비스 계정 이름으로 배포하기"(impersonation) 기능을 켰을 때만 쓰는 설정이다.
켜져 있는데 그 계정(예: `in-cluster`)이 없으면 sync가 권한 오류로 실패한다. 기본은 꺼져 있어 보통은 영향이 없지만, 헷갈리지 않게 지워 둔다(EDIT → 줄 삭제 → SAVE).

### ② 부모 앱 `notebook-rental`

방법 A, kubectl:

```bash
kubectl apply -n argocd -f https://raw.githubusercontent.com/notebook-rental/deploy/main/argocd/notebook-rental.yaml
```

방법 B, Argo CD 화면: **+ NEW APP** → 오른쪽 위 **EDIT AS YAML** → [`argocd/notebook-rental.yaml`](../argocd/notebook-rental.yaml) 내용을 붙여넣기 → **SAVE** → **CREATE**

칸마다 직접 넣는다면:

| 칸 | 값 |
|----|----|
| Application Name | `notebook-rental` |
| Project Name | `notebook-rental` |
| Sync Policy | Automatic, PRUNE RESOURCES와 SELF HEAL 체크 |
| Repository URL | `https://github.com/notebook-rental/deploy.git` |
| Revision | `main` |
| Path | `argocd/apps` (`argocd`로 두면 하위 폴더를 읽지 않아 자식 앱이 안 생긴다) |
| Cluster URL | `https://kubernetes.default.svc` |
| Namespace | `argocd` (Application 객체는 Argo CD가 읽는 `argocd` 네임스페이스에 있어야 한다) |

등록하면 1~2분 안에 자식 Application 4개가 생기고 각자 동기화를 시작한다.

## 3. 화면 읽는 법

| 표시 | 뜻 | 할 일 |
|------|----|------|
| Synced | 클러스터가 git과 같다 | 없음 |
| OutOfSync | git과 다르다(곧 자동으로 맞춤) | 몇 분 넘게 계속되면 앱을 눌러 차이(DIFF) 확인 |
| Healthy | 파드가 모두 Ready | 없음 |
| Progressing | 새 파드를 띄우는 중 | ml은 모델 로드·워밍업 때문에 몇 분 걸릴 수 있다 |
| Degraded | 파드가 시작하지 못함 | [troubleshooting.md](troubleshooting.md) |
| Missing | 아직 만들어지지 않음 | 동기화가 진행되면 사라진다 |

## 4. 되돌리기(롤백)

**git으로 되돌리는 것이 원칙이다.** Argo CD 화면의 ROLLBACK은 자동 동기화가 켜져 있으면 곧 git 상태로 다시 덮인다.

```bash
git log --oneline -- overlays/prod/api      # 배포 기록(커밋 하나 = 배포 하나)
git revert <되돌릴 커밋>                     # 그 배포를 취소하는 커밋을 만든다
git push                                    # Argo CD가 예전 이미지로 맞춘다
```

## 5. 잠깐 자동 반영을 멈추고 싶을 때

장애 조사처럼 클러스터를 손으로 만져야 할 때는 해당 자식 Application의 자동 동기화를 끈다.
그냥 끄면 부모가 git 상태(자동 켜짐)로 되돌리므로, **부모의 자동 동기화를 먼저 끄고** 자식을 끈다.
조사가 끝나면 반대 순서로 다시 켠다.

Argo CD 화면 → 앱 → **APP DETAILS** → SYNC POLICY → **DISABLE AUTO-SYNC**

## 6. 지울 때 주의

자식 Application에는 일부러 finalizer(지울 때 리소스도 같이 지우는 설정)를 넣지 않았다.
부모를 실수로 지워도 api·web·ml 파드는 그대로 남는다. 정말 전부 내리려면 Rancher에서 네임스페이스 안의 리소스를 직접 지운다.
