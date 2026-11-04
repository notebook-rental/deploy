# 이미지 약속(계약): 각 앱 이미지가 지켜야 할 것

매니페스트는 이미지가 아래 약속을 지킨다고 가정하고 만들어졌다. api, web, ml repo에서 Dockerfile과 설정을 만들 때 이 표를 따른다.
약속을 바꾸려면 이 문서와 매니페스트를 같은 PR에서 함께 고친다.

## 공통

| 항목 | 약속 | 이유 |
|------|------|------|
| 실행 사용자 | root가 아님. 매니페스트가 UID를 지정한다(api·ml `10001`, web `101`) | 컨테이너가 뚫려도 피해를 줄인다 |
| 파일시스템 | 루트 파일시스템은 **읽기 전용**. 아래 "쓰기 가능한 폴더"에만 쓴다 | 실행 중 파일 변조 방지 |
| 비밀값 | 이미지 안에 넣지 않는다. 모두 환경변수로 받는다 | 이미지가 공개 저장소에 올라간다 |
| 종료 | `SIGTERM`을 받으면 새 요청을 받지 않고, 처리 중인 요청만 끝낸 뒤 종료(최대 55초) | 배포 중 요청 끊김 방지. 쿠버네티스는 60초 뒤 강제 종료 |
| `sleep` 명령 | 이미지에 있어야 한다(preStop에서 `sleep 5`) | distroless처럼 셸 도구가 없는 이미지는 안 된다 |
| 로그 | 표준 출력(stdout)으로. 파일에 쓰지 않는다 | Rancher 화면에서 바로 본다 |
| 시간대 | 매니페스트가 `TZ=Asia/Seoul`을 넣는다. 이미지에 시간대 자료(`tzdata`, `/usr/share/zoneinfo`)가 있어야 한다. 지금 쓰는 실행 이미지 3개(`eclipse-temurin:21-jre`, `python:3.12-slim`, `nginx-unprivileged:1.27-alpine`)에는 이미 들어 있다 | 로그 시각을 한국 시간으로 본다. DB는 `timestamptz`라 저장 값은 시간대와 상관없다 |

## api (Spring Boot)

| 항목 | 값 |
|------|----|
| 이미지 | `ghcr.io/notebook-rental/api` (실행 이미지 `eclipse-temurin:21-jre`) |
| 포트 | `8080` |
| 시작 확인(startup) | `GET /actuator/health/liveness` — Flyway 마이그레이션까지 최대 5분 |
| 준비 확인(readiness) | `GET /actuator/health/readiness` — DB 연결 가능할 때 200 |
| 생존 확인(liveness) | `GET /actuator/health/liveness` |
| 쓰기 가능한 폴더 | `/tmp` (최대 512Mi) |
| 메모리 | 컨테이너 1Gi, `JAVA_TOOL_OPTIONS=-XX:MaxRAMPercentage=75.0` |

`application-prod.yml`에 넣을 것:

```yaml
server:
  shutdown: graceful                  # SIGTERM 받으면 처리 중인 요청을 마저 끝냄
spring:
  lifecycle:
    timeout-per-shutdown-phase: 50s
  datasource:
    url: ${DB_URL}
    username: ${DB_USERNAME}
    password: ${DB_PASSWORD}
management:
  endpoint:
    health:
      probes:
        enabled: true                 # /actuator/health/liveness, /readiness 켜기
  endpoints:
    web:
      exposure:
        include: health
```

받는 환경변수:

| 이름 | 출처 | 값 |
|------|------|----|
| `SPRING_PROFILES_ACTIVE` | ConfigMap `api-config` | `prod` |
| `APP_PROFILE` | ConfigMap | `prod` |
| `STORAGE_BUCKET` | ConfigMap | `notebook-rental` |
| `ML_BASE_URL` | ConfigMap | `http://ml-inference:8000` (요청 하나를 파드 하나에) |
| `ML_HEADLESS_HOST` | ConfigMap | `ml-inference-headless` (DNS로 모든 ml 파드 주소를 얻어 `/prefetch`, `/models/reload` 전송) |
| `ML_PORT` | ConfigMap | `8000` |
| `TZ` | ConfigMap | `Asia/Seoul` |
| `JAVA_TOOL_OPTIONS` | ConfigMap | JVM 메모리 옵션, `-Duser.timezone=Asia/Seoul`(JVM 기본 시간대) |
| `DB_URL`, `DB_USERNAME`, `DB_PASSWORD` | Secret `notebook-rental-secrets` | |
| `MINIO_ENDPOINT`, `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY` | Secret | api 전용 MinIO 사용자 |
| `JWT_SECRET`, `INTERNAL_TOKEN` | Secret | |

api는 Secret 전체를 받으므로 `ML_MINIO_ACCESS_KEY`, `ML_MINIO_SECRET_KEY`도 환경에 보이지만 쓰지 않는다.

## ml-inference (Python FastAPI)

| 항목 | 값 |
|------|----|
| 이미지 | `ghcr.io/notebook-rental/ml-inference` (Python slim 계열). **모델 파일은 이미지에 넣지 않는다** |
| 포트 | `8000` |
| 시작 확인(startup) | `GET /healthz` — 프로세스가 떠서 응답하면 200. 모델 로드 전이어도 200. 최대 10분 기다림 |
| 준비 확인(readiness) | `GET /readyz` — **MinIO에서 운영 모델을 받고 더미 이미지로 한 번 추론(워밍업)한 뒤에만 200**. 종료 신호를 받았거나 동시 처리 상한이면 503 |
| 생존 확인(liveness) | `GET /healthz` — 3초 안에 응답. 추론 중에도 응답할 수 있게 별도 스레드/이벤트 루프에서 처리 |
| 쓰기 가능한 폴더 | `/tmp` (최대 1Gi), `/var/cache/models` (모델 파일, 최대 4Gi) |
| CPU | requests 4, **limits 없음**. 추론 스레드 수는 `INFERENCE_THREADS` 값을 쓴다 |

받는 환경변수:

| 이름 | 출처 | 값 |
|------|------|----|
| `APP_PROFILE` | ConfigMap `ml-inference-config` | `prod` |
| `TZ` | ConfigMap | `Asia/Seoul` |
| `STORAGE_BUCKET` | ConfigMap | `notebook-rental` |
| `API_BASE_URL` | ConfigMap | `http://api:8080` (`/internal/jobs/claim`, 활성 모델 확인) |
| `INFERENCE_THREADS`, `OMP_NUM_THREADS` | ConfigMap | `4` (= CPU requests) |
| `MODEL_CACHE_DIR` | ConfigMap | `/var/cache/models` |
| `XDG_CACHE_HOME` | ConfigMap | `/tmp/.cache` |
| `MINIO_ENDPOINT` | Secret 키 `MINIO_ENDPOINT` | |
| `MINIO_ACCESS_KEY` | Secret 키 **`ML_MINIO_ACCESS_KEY`** | ml 전용 MinIO 사용자 |
| `MINIO_SECRET_KEY` | Secret 키 **`ML_MINIO_SECRET_KEY`** | |
| `INTERNAL_TOKEN` | Secret 키 `INTERNAL_TOKEN` | api 호출 시 `X-Internal-Token` 헤더 |

## web (React SPA + 비루트 nginx)

| 항목 | 값 |
|------|----|
| 이미지 | `ghcr.io/notebook-rental/web` (실행 이미지 `nginxinc/nginx-unprivileged` 계열, UID 101) |
| 포트 | `8080` |
| 준비·생존 확인 | `GET /healthz` → 200 |
| 쓰기 가능한 폴더 | `/tmp`, `/var/cache/nginx` |
| 환경변수 | 없음. api 주소는 같은 도메인의 `/api`로 고정(상대 경로)이라 빌드 때 넣을 값이 없다 |

nginx 설정에 넣을 것:

```nginx
server {
    listen 8080;
    root /usr/share/nginx/html;

    location = /healthz { access_log off; return 200 "ok\n"; }

    # 파일 이름에 해시가 붙은 빌드 결과물은 1년 캐시
    location /assets/ { expires 1y; add_header Cache-Control "public, immutable"; }

    # index.html 은 캐시하지 않는다(새 배포가 바로 보이게)
    location = /index.html { add_header Cache-Control "no-cache"; }

    # SPA: 없는 경로는 index.html 로(새로고침해도 화면이 뜨게)
    location / { try_files $uri $uri/ /index.html; }
}
```
