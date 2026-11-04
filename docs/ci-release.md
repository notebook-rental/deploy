# 코드 repo의 이미지 빌드와 배포 연결

api, web, ml repo는 각자 이미지를 만들고, 이 deploy repo의 이미지 태그를 바꿔 배포를 일으킨다.
그 공통 부분을 이 repo의 **재사용 워크플로** [`.github/workflows/release-image.yml`](../.github/workflows/release-image.yml)로 만들어 두었다.
코드 repo는 이것을 한 줄로 불러 쓴다.

> **재사용 워크플로(reusable workflow)**: 다른 repo의 GitHub Actions 워크플로를 함수처럼 불러 쓰는 기능.
> 같은 빌드 절차를 세 repo에 복사하지 않아도 되고, 고칠 때 한 곳만 고치면 된다.

## 1. 재사용 워크플로가 하는 일

1. `app` 값 확인(`api`, `web`, `ml-inference` 중 하나)
2. 코드 repo의 `Dockerfile`로 이미지 빌드
3. `ghcr.io/notebook-rental/<app>:<git SHA 전체>`로 push (Actions 기본 `GITHUB_TOKEN` 사용, 레지스트리 비밀값 없음)
4. 이 repo를 받아 `overlays/prod/<app>/kustomization.yaml`의 `newTag`를 그 SHA로 바꿔 `main`에 커밋
   - 바꾸는 코드는 [`tools/bump_image_tag.py`](../tools/bump_image_tag.py)(테스트 있음)
   - 다른 repo와 동시에 push 해서 충돌하면 최신을 다시 받아 최대 5번 재시도
5. Argo CD가 커밋을 보고 클러스터에 반영

**왜 태그를 git SHA로 하나?** `latest`처럼 같은 이름을 덮어쓰면 지금 무엇이 떠 있는지, 무엇으로 되돌릴지 알 수 없다.
SHA는 코드의 정확한 시점을 가리키므로 "운영에 떠 있는 이미지 = 이 커밋의 코드"가 된다.

## 2. `DEPLOY_REPO_TOKEN` 만들기

코드 repo의 Actions가 이 deploy repo에 커밋하려면 권한이 필요하다. 기본 `GITHUB_TOKEN`은 자기 repo만 쓸 수 있어서 따로 만든다.
**deploy repo 하나, Contents 쓰기 하나**만 주는 fine-grained 토큰을 쓴다(새면 피해가 그 repo로 한정).

1. GitHub → 프로필 → **Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token**
2. Token name: `notebook-rental deploy bump`, Expiration: 원하는 기간(만료되면 배포가 멈추니 달력에 적어 둔다)
3. **Resource owner**: `notebook-rental` 조직
   - 조직이 fine-grained 토큰 승인을 요구하면 조직 Settings → Personal access tokens에서 승인한다
4. **Repository access**: Only select repositories → `notebook-rental/deploy`
5. **Permissions → Repository permissions → Contents: Read and write** (나머지는 건드리지 않음. Metadata: Read는 자동)
6. **Generate token** → 값 복사(이 화면을 닫으면 다시 볼 수 없다)
7. api, web, ml repo 각각 **Settings → Secrets and variables → Actions → New repository secret**
   - Name: `DEPLOY_REPO_TOKEN`, Secret: 6번 값

> deploy repo의 `main`에 브랜치 보호(PR 필수)를 켜면 이 토큰의 직접 push가 막힌다. 보호를 켜려면
> 보호 규칙의 우회 목록(bypass)에 토큰 주인을 넣거나, 태그 갱신을 PR 방식으로 바꿔야 한다.

## 3. 코드 repo에 넣을 워크플로

각 코드 repo에 `.github/workflows/release.yml`을 만든다. **테스트가 통과해야만** 이미지를 만든다(`needs: test`).

### api (Spring Boot, Maven)

```yaml
name: release

on:
  push:
    branches: [main]

permissions:
  contents: read

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-java@v4
        with:
          distribution: temurin
          java-version: "21"
          cache: maven
      - run: ./mvnw -B verify

  release:
    needs: test
    permissions:
      contents: read
      packages: write          # ghcr.io에 push
    uses: notebook-rental/deploy/.github/workflows/release-image.yml@main
    with:
      app: api
    secrets:
      DEPLOY_REPO_TOKEN: ${{ secrets.DEPLOY_REPO_TOKEN }}
```

### web (React SPA)

`test` job만 바꾼다.

```yaml
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: "22"
          cache: npm
      - run: npm ci
      - run: npm test
      - run: npm run build

  release:
    needs: test
    permissions:
      contents: read
      packages: write
    uses: notebook-rental/deploy/.github/workflows/release-image.yml@main
    with:
      app: web
    secrets:
      DEPLOY_REPO_TOKEN: ${{ secrets.DEPLOY_REPO_TOKEN }}
```

### ml (Python 추론 서비스)

ml repo에는 학습 코드도 함께 있으므로, 추론 서비스 Dockerfile 위치를 알려 준다(예: `inference/Dockerfile`).

```yaml
  release:
    needs: test
    permissions:
      contents: read
      packages: write
    uses: notebook-rental/deploy/.github/workflows/release-image.yml@main
    with:
      app: ml-inference
      context: .
      dockerfile: inference/Dockerfile
    secrets:
      DEPLOY_REPO_TOKEN: ${{ secrets.DEPLOY_REPO_TOKEN }}
```

입력값:

| 이름 | 필수 | 기본값 | 설명 |
|------|------|--------|------|
| `app` | 예 | | `api`, `web`, `ml-inference` |
| `context` | 아니오 | `.` | docker build 기준 폴더 |
| `dockerfile` | 아니오 | `<context>/Dockerfile` | Dockerfile 경로 |

이미지가 지켜야 할 약속(포트, 상태 확인 주소 등)은 [image-contract.md](image-contract.md)에 있다.

## 4. 첫 push 후: ghcr 패키지 공개로 바꾸기

처음 push 하면 ghcr.io에 패키지가 **Private**으로 생긴다. 클러스터가 비밀값 없이 받게 하려면 공개로 바꾼다.

1. GitHub `notebook-rental` 조직 → **Packages** 탭 → 패키지(`api`, `web`, `ml-inference`) 선택
2. 오른쪽 **Package settings** → 맨 아래 **Danger Zone → Change visibility → Public**

- 패키지를 Private으로 두어도 된다. 세 앱 모두 Rancher Secret `ghcr-pull`로 로그인해서 받는다([secrets.md](secrets.md) 3절).
- 조직 설정에서 Public 패키지 생성이 막혀 있으면: 조직 **Settings → Packages → Package creation**에서 Public을 허용한다.

## 5. 배포가 안 될 때 확인 순서

1. 코드 repo **Actions** 탭: `test` → `release` 중 어디서 멈췄나
2. `release`가 성공했는데 배포가 안 되면: deploy repo 커밋 기록에 `deploy(<app>): <sha>` 커밋이 있나
3. 커밋이 있으면: Argo CD 화면에서 해당 앱 상태([argocd.md](argocd.md) 3절)
4. 자주 나는 오류
   - `denied: permission_denied` (push): 호출하는 job에 `permissions: packages: write`가 없다
   - `Bad credentials` / `403` (deploy 커밋): `DEPLOY_REPO_TOKEN`이 없거나 만료됐거나 Contents 쓰기 권한이 없다
   - `newTag를 정확히 한 곳에서 찾지 못했습니다`: `overlays/prod/<app>/kustomization.yaml`의 `images` 부분 모양을 누가 바꿨다
