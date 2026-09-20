# Notion 기반 일간 스니펫 초안

`.github/workflows/notion-daily-draft.yml`은 매일 **21:00 (Asia/Seoul)** 에 실행됩니다. 지정한 Notion 데이터 소스에서 그날 수정한 페이지의 본문만 모아 AI로 정리한 뒤, GCS Pulse의 **개인 전용 초안**으로 저장합니다.

초안은 팀 피드, 리더보드, 업적 계산에 포함되지 않습니다. 사용자가 `일간 스니펫` 화면에서 내용을 검토·수정하고 **저장하기**를 눌러야만 일반 일간 스니펫으로 제출됩니다.

## 한 번만 설정하기

1. Notion에서 내부 통합을 만들고 `Read content` 권한을 부여합니다. 기록을 보관하는 데이터 소스를 그 통합에 공유합니다. 데이터베이스 ID가 아니라 Notion의 **data source ID**를 복사합니다.
2. GCS Pulse에 로그인한 뒤 `/auth/tokens`에서 이 자동화 전용 API 토큰을 발급합니다.
3. GitHub 저장소의 **Settings → Secrets and variables → Actions**에 아래 repository secrets를 추가합니다.

   | Secret | 값 |
   | --- | --- |
   | `NOTION_TOKEN` | Notion 내부 통합 토큰 |
   | `NOTION_DATA_SOURCE_ID` | 기록용 Notion data source ID |
   | `GCS_API_TOKEN` | 본인 계정의 GCS Pulse API 토큰 |

4. Actions 탭에서 **Create daily snippet draft from Notion**을 한 번 수동 실행해 연결을 확인합니다.

토큰은 코드나 Notion 페이지 본문에 붙여 넣지 말고 GitHub Secrets에만 보관하세요. 토큰이 노출되었다고 의심되면 GCS Pulse 토큰을 폐기하고 새로 발급해야 합니다.

## 동작 규칙

- 해당 날짜에 저장된 일간 스니펫 또는 초안이 있으면 자동화는 아무것도 덮어쓰지 않습니다.
- 실행일(Asia/Seoul)에 수정된 페이지 중 내용이 있는 최대 20개만 가져옵니다.
- 본문은 최대 20,000자로 제한하며, 텍스트 블록과 중첩 블록을 읽습니다.
- 기록이 없거나 빈 페이지뿐이면 초안을 만들지 않습니다.
- GitHub Actions의 정기 실행은 몇 분 정도 지연될 수 있습니다.

`NOTION_MAX_PAGES`, `NOTION_MAX_CHARACTERS`, `NOTION_VERSION`, `GCS_API_URL` 환경 변수로 기본값을 조정할 수 있습니다. 로컬에서 시험할 때도 같은 변수만 설정한 뒤 다음을 실행합니다.

```bash
python apps/server/scripts/sync_notion_daily_draft.py
```
