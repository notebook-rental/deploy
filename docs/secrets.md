# 비밀값 관리: Rancher에서 Secret 만들기

이 repo는 공개(Public)라서 비밀값을 넣을 수 없다. 그래서 비밀값은 **Rancher 화면에서 클러스터에 직접** 넣고,
매니페스트에는 **Secret의 이름만** 적는다. Argo CD는 이 Secret을 만들지도, 지우지도 않는다.

```
Rancher에서 직접 만듦                     이 repo(매니페스트)
┌───────────────────────────────┐        ┌─────────────────────────────────────────┐
│ Secret notebook-rental-secrets│ ◀──────│ envFrom: secretRef: notebook-rental-... │ (이름만)
│   DB_URL=...                  │        └─────────────────────────────────────────┘
│   DB_PASSWORD=...             │
└───────────────────────────────┘
```

**왜 이렇게 하나?** git에 한 번 들어간 값은 파일을 지워도 기록(history)에 남고, 공개 repo는 누구나 복제할 수 있다.
값을 암호화해서 git에 넣는 방법(Sealed Secrets 등)은 클러스터에 컨트롤러를 더 설치해야 해서 쓰지 않기로 했다(docs research R16).

---

## 1. 네임스페이스 만들기

Secret은 네임스페이스 안에 만들어지므로 네임스페이스부터 만든다.

1. Rancher 접속 → 왼쪽 위 메뉴(☰) → 대상 클러스터 선택
2. 왼쪽 메뉴 **Cluster → Projects/Namespaces**
3. 넣을 프로젝트(예: `Default`) 줄의 **Create Namespace** 클릭
4. Name: `notebook-rental` → **Create**

> Rancher의 "프로젝트"는 네임스페이스 여러 개를 묶어 권한을 주는 Rancher만의 단위다.
> Argo CD가 네임스페이스를 만들게 하면 어느 프로젝트에도 속하지 않아 Rancher 화면에서 찾기 어려워서, 직접 만든다
> (그래서 Argo CD 설정은 `CreateNamespace=false`).

---

## 2. 앱 비밀값 Secret 만들기 (`notebook-rental-secrets`)

### 2-1. 넣을 키

키 이름은 대소문자까지 정확히 같아야 한다. 하나라도 빠지면 그 키를 쓰는 파드가
`CreateContainerConfigError` 상태로 시작하지 못한다.

| 키 | 누가 씀 | 값의 모양(예시, 실제 값 아님) | 설명 |
|----|---------|-------------------------------|------|
| `DB_URL` | api | `jdbc:postgresql://<DB 호스트>:5432/notebook_rental` | 기존 PostgreSQL에 만든 DB 주소. JDBC 형식 |
| `DB_USERNAME` | api | `notebook_rental` | 이 서비스 전용 DB 계정 |
| `DB_PASSWORD` | api | | 위 계정 비밀번호 |
| `MINIO_ENDPOINT` | api, ml | `http://storage.java21.net:8000` | MinIO S3 API 주소. HTTPS로 바꾸면 이 값만 고친다 |
| `MINIO_ACCESS_KEY` | api | | api 전용 MinIO 사용자의 Access Key |
| `MINIO_SECRET_KEY` | api | | 위 사용자의 Secret Key |
| `ML_MINIO_ACCESS_KEY` | ml | | ml-inference 전용 MinIO 사용자의 Access Key(원본 삭제 권한 없음) |
| `ML_MINIO_SECRET_KEY` | ml | | 위 사용자의 Secret Key |
| `JWT_SECRET` | api | 32바이트 이상 무작위 문자열 | 로그인 토큰(JWT) 서명 키 |
| `INTERNAL_TOKEN` | api, ml | 32바이트 이상 무작위 문자열 | api ⇄ ml 내부 호출 인증(`X-Internal-Token`) |

- api는 Secret 전체를 환경변수로 받는다(`envFrom`).
- ml-inference는 필요한 4개만 골라 받는다(`MINIO_ENDPOINT`, `ML_MINIO_ACCESS_KEY` → 컨테이너 안에서는 `MINIO_ACCESS_KEY`,
  `ML_MINIO_SECRET_KEY` → `MINIO_SECRET_KEY`, `INTERNAL_TOKEN`). DB 비밀번호와 JWT 키는 ml에 들어가지 않는다.
- **MinIO 사용자를 api와 ml로 나누는 이유**: docs `infra/README.md`의 권한 표처럼 ml은 원본(`originals/`)을 읽기만 해야 한다.
  아직 사용자를 나누지 않았다면 처음에는 `ML_MINIO_*`에 api와 같은 값을 넣어도 동작한다. 나중에 ml 전용 사용자를 만들면 이 두 값만 바꾼다.

무작위 문자열 만드는 법(내 PC 터미널에서):

```bash
openssl rand -base64 48
```

### 2-2. Rancher 화면에서 만들기

1. 클러스터 → 왼쪽 메뉴 **Storage → Secrets** → 오른쪽 위 **Create**
2. 종류에서 **Opaque** 선택
3. 위쪽 **Namespace**: `notebook-rental`, **Name**: `notebook-rental-secrets`
4. **Data** 탭에서 **Add** 를 눌러 위 표의 키마다 한 줄씩 Key / Value 입력
   - 값 앞뒤에 공백이나 줄바꿈이 들어가지 않게 주의(복사·붙여넣기할 때 자주 생긴다)
   - Rancher가 알아서 base64로 바꿔 저장하므로 직접 base64로 바꿔 넣지 않는다
5. **Create**

### 2-3. (대안) kubectl로 만들기

Rancher 화면 오른쪽 위 **Kubectl Shell** 이나 내 PC의 kubectl로도 만들 수 있다.
값이 셸 기록(history)에 남지 않게 **파일로 넣고 바로 지운다**.

```bash
# 1) 임시 파일 작성(편집기로 직접 입력). 이 파일은 절대 git 폴더 안에 만들지 않는다.
cat > /tmp/nr.env   # KEY=VALUE 를 한 줄에 하나씩 입력 후 Ctrl+D

# 2) Secret 생성
kubectl -n notebook-rental create secret generic notebook-rental-secrets --from-env-file=/tmp/nr.env

# 3) 임시 파일 삭제
rm /tmp/nr.env

# 4) 키 이름만 확인(값은 출력하지 않음)
kubectl -n notebook-rental get secret notebook-rental-secrets -o jsonpath='{.data}' | tr ',' '\n' | cut -d'"' -f2
```

---

## 3. 이미지 받기용 Secret (`ghcr-pull`, api repo가 Private인 동안만)

공개 패키지는 누구나 받을 수 있지만, `api` repo가 Private이면 그 이미지 패키지도 Private이라 클러스터가 로그인해야 받을 수 있다.

1. GitHub → 오른쪽 위 프로필 → **Settings → Developer settings → Personal access tokens → Tokens (classic)** → **Generate new token (classic)**
   - Note: `notebook-rental ghcr pull`, Expiration: 원하는 기간, Scope: **`read:packages` 하나만**
   - (패키지 읽기는 아직 fine-grained 토큰을 지원하지 않아 classic 토큰을 쓴다)
2. Rancher → **Storage → Secrets → Create → Registry**
3. Namespace `notebook-rental`, Name `ghcr-pull`
4. Registry Domain Name: **Custom** → `ghcr.io`, Username: GitHub 사용자 이름, Password: 1번 토큰 → **Create**

`overlays/prod/api/kustomization.yaml`이 api 파드에 이 Secret을 `imagePullSecrets`로 건다.
api repo를 Public으로 바꾸고 패키지도 Public으로 바꾸면, 그 패치를 지우고 이 Secret도 지운다.
(Secret이 없어도 파드는 뜬다. 공개 이미지는 로그인 없이 받기 때문이다.)

---

## 4. 값을 바꿀 때

환경변수는 **파드가 시작할 때 한 번** 읽힌다. Secret 값을 바꿔도 이미 떠 있는 파드는 예전 값을 쓴다.

1. Rancher → Storage → Secrets → `notebook-rental-secrets` → ⋮ → **Edit Config** → 값 수정 → **Save**
2. Rancher → **Workloads → Deployments** → 해당 Deployment(`api` 또는 `ml-inference`) ⋮ → **Redeploy**
   - kubectl: `kubectl -n notebook-rental rollout restart deployment/api`
3. 파드가 하나씩 교체된다(롤링 업데이트라 서비스는 멈추지 않는다)

`INTERNAL_TOKEN`처럼 api와 ml이 같이 쓰는 값은 **두 Deployment를 모두** 재시작한다.

> Argo CD의 selfHeal은 이 repo에 있는 리소스만 되돌린다. Secret은 이 repo에 없으므로 Rancher에서 고친 값이 그대로 유지된다.

---

## 5. GitHub 쪽 비밀값 (CI용)

| 어디에 | 이름 | 무엇 | 만드는 법 |
|--------|------|------|-----------|
| 코드 repo(api, web, ml) → Settings → Secrets and variables → Actions | `DEPLOY_REPO_TOKEN` | 이 deploy repo에 이미지 태그를 커밋할 권한 | [ci-release.md](ci-release.md) 2절 |

이미지 push는 GitHub Actions가 자동으로 주는 `GITHUB_TOKEN`으로 하므로 레지스트리 비밀값은 따로 없다.

---

## 6. 하지 말 것

- 비밀값을 이 repo의 파일, 커밋 메시지, PR, 이슈, 채팅에 적지 않는다
- `kubectl get secret -o yaml` 출력을 그대로 공유하지 않는다(base64는 암호화가 아니라 누구나 되돌릴 수 있다)
- 노출이 의심되면: 그 값을 발급한 곳(MinIO 콘솔, DB, GitHub)에서 새 값을 만들고 → 4절대로 Secret을 바꾸고 재시작 → 옛 값을 폐기
