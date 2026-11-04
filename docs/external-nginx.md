# 외부 Nginx 설정 예시 (marco 관리 서버)

도메인 `notebook-rental.java21.net`과 HTTPS 인증서는 클러스터 밖의 Nginx가 맡고, 그 Nginx가 클러스터의
Ingress Controller(ingress-nginx)로 요청을 넘긴다. 이렇게 프록시가 **두 단**이면, 업로드 크기나 시간 제한 같은 설정을
**양쪽 모두** 맞춰야 한다. 한쪽만 바꾸면 작은 쪽 값에 걸린다.

| 설정 | 외부 Nginx (이 문서) | Ingress (이 repo, 이미 설정됨) | 이유 |
|------|----------------------|--------------------------------|------|
| 업로드 크기 | `client_max_body_size 20m;` | `proxy-body-size: "20m"` | 사진 작업본 1장 약 1MB, 여유 |
| SSE 버퍼링 | `/api/v1/inspections/`에 `proxy_buffering off;` | 같은 경로 `proxy-buffering: "off"` | 버퍼링되면 컷 결과가 몰려서 늦게 뜬다 |
| 연결 유지 | `proxy_read_timeout 3600s;` | `proxy-read-timeout: "3600"` | 촬영 중 SSE 연결이 끊기지 않게 |
| HTTP 버전 | `proxy_http_version 1.1;` + keepalive | 기본값 | 연결 재사용으로 왕복 시간 감소 |
| 원본 헤더 | `Host`, `X-Forwarded-For`, `X-Forwarded-Proto` 전달 | Ingress Controller의 `use-forwarded-headers` (아래 3절) | Ingress 규칙의 도메인 매칭, 감사 로그의 실제 IP, HTTPS 판별 |

> **SSE(Server-Sent Events)**: 서버가 브라우저에 연결을 열어 둔 채 결과가 나올 때마다 한 줄씩 보내는 방식.
> 중간 프록시가 응답을 모았다가(버퍼링) 한꺼번에 보내면 실시간이 아니게 된다.

---

## 1. 설정 예시

`<INGRESS_ADDR>`는 Ingress Controller에 닿는 주소다(예: 워커 노드 IP의 80 포트, 또는 LoadBalancer 주소).
이 repo는 공개라 실제 IP를 여기 적지 않는다. 실제 값은 Nginx 서버에만 둔다.

```nginx
# /etc/nginx/conf.d/notebook-rental.conf

upstream notebook_rental_ingress {
    server <INGRESS_ADDR>:80;
    # 노드가 여러 대면 줄을 추가한다. 한 대가 죽으면 다른 대로 넘긴다.
    # server <INGRESS_ADDR_2>:80;
    keepalive 32;                       # Ingress와의 연결을 재사용
}

# HTTPS 적용 전(첫 배포 직후)에는 이 server 블록 하나로 시작한다.
# HTTPS를 붙이면 아래 2절처럼 443 블록으로 옮기고, 이 블록은 https로 넘기는 용도로 바꾼다.
server {
    listen 80;
    server_name notebook-rental.java21.net;

    client_max_body_size 20m;

    # 모든 location에 공통으로 넣을 프록시 설정
    proxy_http_version 1.1;
    proxy_set_header Connection "";                 # keepalive 쓰려면 비워야 함
    proxy_set_header Host $host;                    # Ingress 규칙이 도메인으로 매칭하므로 꼭 필요
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-Host $host;

    # 검사 결과 실시간 알림(SSE)과 검사 API
    location /api/v1/inspections/ {
        proxy_pass http://notebook_rental_ingress;
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }

    location / {
        proxy_pass http://notebook_rental_ingress;
        proxy_read_timeout 60s;
    }
}
```

설정 확인 후 반영:

```bash
sudo nginx -t && sudo systemctl reload nginx
```

## 2. HTTPS를 붙일 때

**휴대폰 카메라는 HTTPS 페이지에서만 켜진다**(브라우저 보안 규칙). 직원 폰으로 시험하기 전에 붙여야 한다.

```nginx
server {
    listen 80;
    server_name notebook-rental.java21.net;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl;
    http2 on;                                         # nginx 1.25.1 이상. 그 전 버전은 `listen 443 ssl http2;`
    server_name notebook-rental.java21.net;

    ssl_certificate     /etc/letsencrypt/live/notebook-rental.java21.net/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/notebook-rental.java21.net/privkey.pem;

    # 이하 1절의 client_max_body_size, proxy_* 설정, location 두 개를 그대로 옮긴다.
}
```

HTTPS를 켜면 **MinIO 주소도 HTTPS로** 바꿔야 한다. 앱이 `https://`인데 사진을 `http://storage.java21.net:8000`으로 올리면
브라우저가 "혼합 콘텐츠"로 막는다. MinIO S3 API를 HTTPS로 연 뒤 Rancher Secret의 `MINIO_ENDPOINT`를 바꾸고
api·ml을 재시작한다([secrets.md](secrets.md) 4절). 자세한 내용은 docs repo `infra/README.md` 3절.

## 3. Ingress Controller의 `use-forwarded-headers`

외부 Nginx가 보낸 `X-Forwarded-For`(실제 접속자 IP)와 `X-Forwarded-Proto`(https였는지)를 Ingress Controller가 믿고
뒤로 넘기게 하는 설정이다. **Ingress Controller 전체 설정**이라 이 repo(앱 네임스페이스)에서는 바꿀 수 없고 클러스터 관리자가 바꾼다.

- 일반 ingress-nginx(Helm 설치): `ingress-nginx-controller` ConfigMap에 `use-forwarded-headers: "true"`
- Rancher RKE2 기본 ingress: `kube-system`에 아래 HelmChartConfig를 적용

```yaml
apiVersion: helm.cattle.io/v1
kind: HelmChartConfig
metadata:
  name: rke2-ingress-nginx
  namespace: kube-system
spec:
  valuesContent: |-
    controller:
      config:
        use-forwarded-headers: "true"
```

**주의**: 이 설정을 켜면 Ingress Controller에 직접 접속하는 사람이 가짜 `X-Forwarded-For`를 보낼 수 있다.
Ingress Controller의 80/443 포트는 외부 Nginx 서버에서만 닿게(방화벽) 두는 것이 안전하다.

## 4. 확인

```bash
# 1) web(SPA)이 뜨는지
curl -I http://notebook-rental.java21.net/

# 2) api가 응답하는지(로그인 없이 부르면 401 같은 오류가 나와도, 공통 응답 형식 JSON이면 api까지 닿은 것)
curl -i http://notebook-rental.java21.net/api/v1/devices

# 3) /internal 은 밖에서 안 열려야 한다(web의 index.html 또는 404가 나와야 정상, api 응답이면 안 됨)
curl -i http://notebook-rental.java21.net/internal/jobs/claim
```

502/504가 나오면 [troubleshooting.md](troubleshooting.md)를 본다.
