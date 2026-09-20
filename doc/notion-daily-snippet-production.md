# Notion → GCS Pulse 일간 스니펫 자동 제출

매일 21:00(Asia/Seoul)에 당일 수정된 Notion 기록을 읽고, GCS Pulse의 정리 API를
거쳐 일간 스니펫으로 제출합니다.

기존 `notion-daily-draft.yml`은 아직 운영 API에 없는
`POST /daily-snippets/draft`를 사용하므로 현재 운영 환경에서는
`notion-daily-snippet.yml`을 사용합니다.

## 팀원별 최초 설정

1. 저장소를 각자 Fork합니다.
2. Notion 내부 통합을 만들고 대상 페이지에 읽기 권한을 부여합니다.
3. GCS Pulse `설정 → API 토큰`에서 개인 토큰을 발급합니다.
4. Fork의 `Settings → Secrets and variables → Actions`에 다음 값을 저장합니다.

| Secret | 값 |
| --- | --- |
| `NOTION_TOKEN` | 개인 Notion 통합 토큰 |
| `GCS_API_TOKEN` | 개인 GCS Pulse API 토큰 |

5. Actions에서 `Submit daily snippet from Notion`을 수동 실행합니다.
6. GCS Pulse 일간 스니펫 화면에서 결과를 확인합니다.

## 안전 동작

- 토큰은 GitHub Actions Secrets에서만 읽고 로그에 출력하지 않습니다.
- 이미 당일 스니펫이 있으면 덮어쓰지 않습니다.
- Notion/AI 정리 중 수동 제출될 수 있으므로 최종 제출 직전 다시 확인합니다.
- 읽는 페이지 수는 기본 20개, 원문은 기본 20,000자로 제한됩니다.
- 예약 실행과 수동 실행이 겹치지 않도록 concurrency를 설정했습니다.

현재 운영 API에는 create-only 초안 엔드포인트가 없으므로 결과는 초안이 아니라
일간 스니펫으로 바로 제출됩니다. 첫 수동 실행 결과를 확인한 뒤 예약 실행을 유지하세요.
