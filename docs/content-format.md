# 내용 파일 형식

`content/YYYY-MM-DD.json` 한 파일이 하루치 브리핑 한 페이지가 된다. 예시는 `content/2026-10-13.json`.
고정 페이지는 `content/pages/이름.json`이다 (예: `about.json`).

## 공개 입력 규칙
- 개인 내용은 처음부터 넣지 않는다. `actions`, `side.career`, `private` 표시는 생성 실패다.
- 금지 패턴(`_site.json`의 `blocklist`, 로컬 전용 `보관/blocklist.local.txt`)이 어느 문자열에라도 있으면 실패다.
- 실패하면 파일·위치·규칙만 알려 주고 아무것도 쓰지 않는다.

## 글 안에서 쓸 수 있는 표시
| 쓰는 법 | 결과 |
|---|---|
| `**굵게**` | 굵은 글씨 |
| `{{down:-5.46%}}` | 파랑 굵은 글씨 (하락) |
| `{{up:+1.8%}}` | 빨강 굵은 글씨 (상승) |

## 최상위 항목
| 항목 | 필수 | 설명 |
|---|---|---|
| `date` | 예 | `2026-10-13`. 파일 이름과 같아야 한다 |
| `headline`, `summary` | 예 | 헤드라인, 2문장 안팎 요약 |
| `sections` | 예 | 분야 목록. 기사가 없는 분야는 화면에 나오지 않는다 |
| `edition` | | `오후판` 등 |
| `checked_at` | | `17:02 KST` 형식. 뉴스를 마지막으로 확인한 시각. 모르면 넣지 않는다 |
| `market` | | 시장 요약 |
| `quick` | | 핵심 1분. 기사에 연결한다 |
| `side.schedule` | | 다음 일정 |
| `corrections` | | 정정·수정 내역 `[{"date", "text"}]` |

## 기사 (sections[].items[])
```json
{
  "id": "kospi-sidecar",
  "date": "2026-10-13",
  "featured": true,
  "title": "제목",
  "facts": "무슨 일이 있었나. 확인한 사실만.",
  "so": "왜 중요한가. 근거가 있을 때만.",
  "recheck": {"label": "다시 볼 조건", "text": "다음에 확인할 것."},
  "sources": [{"name": "매체", "url": "https://..."}]
}
```
- `id`: 영문 소문자·숫자·하이픈. 한 페이지 안에서 유일해야 한다. 주소의 `#id`가 된다.
- `date`: 사건이나 발표 날짜. 브리핑 날짜보다 뒤일 수 없다.
- `sources`: 하나 이상 필수 (`_site.json`의 `require_sources_from` 날짜부터). 같은 주소를 두 번 쓰면 실패다.
- `featured`: 홈의 "오늘의 주요 뉴스"에 제목과 첫 문장이 나온다 (최대 5건). 분야에서는 이 기사가 대표 기사로 바로 보이고 나머지는 "더 보기" 안에 접힌다. 없으면 첫 기사가 대표다.
- `so`, `recheck`는 없으면 화면에서 그 구역이 빠진다.

## 핵심 1분 (quick)
```json
{"text": "KOSPI {{down:6,035.35 (-5.46%)}} 마감", "ref": "kospi-sidecar"}
```
`ref`는 기사 id다. 문장 속 숫자는 연결된 기사의 제목이나 본문에 그대로 있어야 한다. 다르면 실패다.

## 시장 요약 (market)
```json
{"name": "KOSPI", "note": "참고", "basis": "10/13 종가", "asof": "2026-10-13",
 "value": "6,035.35", "change": "▼ 5.46%", "dir": "down"}
```
- `basis`: 화면에 보이는 기준 설명 (종가, 전일 종가, 전망치 등).
- `asof`: 기준일. 브리핑 날짜보다 뒤이거나 `market_max_age_days`보다 오래되면 실패다.
- `market.sources`: 수치의 출처 목록.

## 다음 일정 (side.schedule)
```json
{"at": "2026-10-14T08:30:00-04:00", "title": "미국 9월 CPI", "desc": "미국 동부 08:30"}
```
`at`은 시간대가 붙은 시각이다. 화면에는 한국 시간으로 바꿔 보여 준다. 브리핑 기준 시각보다 이르면 실패다.

## 사이트 설정 (content/_site.json)
| 항목 | 설명 |
|---|---|
| `brand`, `title`, `description` | 이름, 탭 제목, 홈 설명 |
| `base_url` | 대표 주소. `https://dogcowai.com` 형식, 끝에 `/` 없음 |
| `intro` | 홈 맨 위 소개글 `{heading, paragraphs[], items[]}`. 없으면 소개 구역이 빠진다 |
| `footer_meta`, `disclaimer` | 맨 아래 문구 |
| `require_sources_from` | 이 날짜부터 기사 출처가 필수 |
| `market_max_age_days` | 시장 수치 기준일의 허용 범위 |
| `blocklist` | 금지 패턴 (공개 가능한 일반 패턴만) |

## 고정 페이지 (content/pages/*.json)
`slug`, `title`, `sections[{heading, paragraphs[], items[]}]`. 선택: `nav`(메뉴 이름), `lead`, `description`.
