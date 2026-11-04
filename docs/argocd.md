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

### 방법 A: kubectl (가장 간단)

Rancher 오른쪽 위 **Kubectl Shell** 또는 내 PC에서:

```bash
kubectl apply -n argocd -f https://raw.githubusercontent.com/notebook-rental/deploy/main/argocd/notebook-rental.yaml
```

### 방법 B: Argo CD 화면

1. Argo CD 접속 → **+ NEW APP** → 오른쪽 위 **EDIT AS YAML**
2. [`argocd/notebook-rental.yaml`](../argocd/notebook-rental.yaml) 내용을 붙여넣기 → **SAVE** → **CREATE**

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
