# Argo CD 등록과 사용법

## 1. 구조: ApplicationSet 하나가 앱 넷을 만든다

```
ApplicationSet notebook-rental (argocd/applicationset.yaml)   ← 처음 한 번만 직접 적용
  └─ repo의 overlays/prod/* 폴더마다 Application을 하나씩 만든다
       ├─ notebook-rental-api           → overlays/prod/api
       ├─ notebook-rental-web           → overlays/prod/web
       ├─ notebook-rental-ml-inference  → overlays/prod/ml-inference
       └─ notebook-rental-ingress       → overlays/prod/ingress
```

> **ApplicationSet**: Application을 찍어 내는 틀(template)과 "무엇마다 하나씩 찍을지"(generator)를 적은 Argo CD 설정.
> 여기서는 **git 디렉터리 생성기**를 써서 `overlays/prod/` 아래 폴더 하나마다 앱 하나를 만든다.
> 앱을 하나 더 만들려면 `overlays/prod/<이름>/kustomization.yaml`만 추가하면 되고, 폴더를 지우면 그 앱도 사라진다.

Argo CD 화면 왼쪽 메뉴의 **ApplicationSets** 화면에 `notebook-rental`이 다른 서비스(crowfoot, fourcut)와 같이 보인다.

**전에는 왜 app-of-apps였나?** 처음에는 부모 Application이 `argocd/apps/`의 자식 파일 4개를 읽는 방식이었다.
앱마다 파일을 따로 써야 했고 다른 서비스와 화면 구성이 달라, 2026-11-04 ApplicationSet으로 바꿨다(marco 요청). 만들어지는 앱 4개의 이름과 설정은 같다.

모든 Application의 동기화 설정:

| 설정 | 뜻 |
|------|----|
| `automated` | `main`이 바뀌면 사람이 누르지 않아도 반영 |
| `prune: true` | repo에서 지운 리소스는 클러스터에서도 지움 |
| `selfHeal: true` | 누가 클러스터에서 손으로 바꾸면 repo 상태로 되돌림 |
| `CreateNamespace=false` | 네임스페이스는 Rancher에서 미리 만든다([secrets.md](secrets.md) 1절) |
| `retry` | 실패하면 10초, 20초, 40초… 간격으로 최대 5번 다시 시도 |
| `preserveResourcesOnDeletion` (ApplicationSet) | ApplicationSet을 지워도 실제 파드·Service는 지우지 않는다 |

로컬에서 만들어질 앱을 미리 보려면 `python -m tools.expand_appset`을 실행한다(CI도 이 결과를 스키마 검사한다).

## 2. 등록하기 (한 번만)

전제: Argo CD가 `argocd` 네임스페이스에 설치돼 있다(일반적인 설치 위치). 다른 곳이면 `argocd/*.yaml`의
`metadata.namespace`를 그 이름으로 바꾼다. repo가 공개라 Argo CD에 repo 접속 정보를 따로 등록할 필요가 없다.

순서는 **① 프로젝트 → ② ApplicationSet**이다. 명령은 Rancher 오른쪽 위 **Kubectl Shell**이나 내 PC의 kubectl에서 실행한다.

### ① 프로젝트 `notebook-rental` (AppProject)

> **프로젝트(AppProject)**: Argo CD 앱 여러 개를 묶고 "어느 repo를 읽고, 어느 클러스터·네임스페이스에, 어떤 종류의 리소스를 만들 수 있는지"를 정하는 울타리.

```bash
kubectl apply -n argocd -f https://raw.githubusercontent.com/notebook-rental/deploy/main/argocd/project.yaml
```

| 항목 | 값 | 이유 |
|------|----|------|
| Source repositories | `https://github.com/notebook-rental/deploy.git` | 이 repo만 읽는다 |
| Destinations | `https://kubernetes.default.svc` + 네임스페이스 `notebook-rental` | 서비스를 배포하는 곳 하나뿐 |
| Cluster resource allow list | 없음 | Namespace, ClusterRole 같은 클러스터 전체 리소스는 만들지 않는다 |
| Namespace resource allow list | ConfigMap, Service, Deployment, PodDisruptionBudget, NetworkPolicy, Ingress | 이 repo가 쓰는 종류만. **Secret은 일부러 뺐다**(Rancher에서 직접 관리) |

이 파일은 Argo CD 앱이 관리하지 않는다. 앱이 자기 울타리를 스스로 넓힐 수 없게 하려는 것이다. 바꾸면 위 명령으로 다시 적용한다.
화면에서 이미 같은 이름의 프로젝트를 만들었어도 위 명령을 쓰면 된다(파일에 있는 항목은 파일 값으로 바뀐다).
화면에서 넣은 **DESTINATION SERVICE ACCOUNTS** 줄은 파일에 없어서 남으니 화면에서 지운다(EDIT → 줄 삭제 → SAVE).

### ② ApplicationSet `notebook-rental`

```bash
kubectl apply -n argocd -f https://raw.githubusercontent.com/notebook-rental/deploy/main/argocd/applicationset.yaml
```

1~2분 안에 앱 4개가 생기고 각자 동기화를 시작한다. Argo CD 화면 **Applications**에서 앱 4개, **ApplicationSets**에서 `notebook-rental`을 확인한다.

### 이미 app-of-apps(부모 앱 `notebook-rental`)로 등록했다면: 옮기기

부모 앱을 **리소스는 남기고(non-cascading)** 지운 뒤 ApplicationSet을 적용한다. 그러면 ApplicationSet이 이름이 같은 앱 4개를 그대로 이어받는다.
파드는 내려가지 않는다.

1. 부모 앱만 지우기(자식 앱과 파드는 남김)
   - 화면: Applications → `notebook-rental`(부모) → **DELETE** → 삭제 방식에서 **Non-cascading** 선택 → 이름 입력 → OK
   - 또는 kubectl:
     ```bash
     # 부모 앱이 자식까지 지우는 설정(finalizer)이 있다면 먼저 떼어 낸다
     kubectl -n argocd patch application notebook-rental --type merge -p '{"metadata":{"finalizers":null}}'
     kubectl -n argocd delete application notebook-rental
     ```
2. 프로젝트 다시 적용(①의 명령). 부모 앱용이던 `argocd` 대상과 Application 허용이 빠진다.
3. ApplicationSet 적용(②의 명령)
4. 확인: `kubectl -n argocd get applicationset notebook-rental`과 `kubectl -n argocd get applications`에서 앱 4개가 보이고,
   앱 4개의 소유자가 ApplicationSet인지 본다.
   ```bash
   kubectl -n argocd get applications -o custom-columns=NAME:.metadata.name,OWNER:.metadata.ownerReferences[0].kind
   ```
   `OWNER`가 `ApplicationSet`이면 옮기기가 끝난 것이다.

**주의**: 1번에서 **Foreground/Background(cascading)** 를 고르면 자식 앱과 그 앱이 만든 파드까지 지워질 수 있다. 꼭 Non-cascading을 고른다.

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

장애 조사처럼 클러스터를 손으로 만져야 할 때는 자동 동기화를 끈다.
앱 화면에서 끄면 ApplicationSet이 몇 분 안에 틀(template)대로 다시 켠다. 그래서 **ApplicationSet의 틀에서 끈다**.

```bash
# 모든 앱의 자동 동기화 끄기(조사 시작)
kubectl -n argocd patch applicationset notebook-rental --type json \
  -p '[{"op":"remove","path":"/spec/template/spec/syncPolicy/automated"}]'

# 다시 켜기(조사 끝): repo의 파일로 되돌린다
kubectl apply -n argocd -f https://raw.githubusercontent.com/notebook-rental/deploy/main/argocd/applicationset.yaml
```

## 6. 지울 때 주의

ApplicationSet에 `preserveResourcesOnDeletion: true`를 넣었다. ApplicationSet이나 앱을 실수로 지워도 api·web·ml 파드는 그대로 남는다.
`overlays/prod/<이름>` 폴더를 지우면 그 앱(Application 객체)은 사라지지만, 위 설정 때문에 파드는 남는다. 정말 내리려면 Rancher에서 그 리소스를 직접 지운다.
