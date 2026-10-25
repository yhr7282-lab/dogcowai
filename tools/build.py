#!/usr/bin/env python3
"""dogcowai 사이트 생성기.

content/YYYY-MM-DD.json (날짜별 내용), content/_site.json (공통 설정), content/pages/*.json (고정 페이지)에서
아래 페이지와 404, sitemap.xml, robots.txt, feed.xml 을 만든다.

    /                       홈: 짧은 소개, 최신 브리핑과 그 기사 전부
    /news/                  날짜별 브리핑: 날마다 헤드라인·요약·기사 목록을 최신순으로 쌓는다
    /news/날짜/              하루치 브리핑: 날짜 선택, 헤드라인, 주요 뉴스, 분야별 기사(같은 날 이전 판 기사 포함)
    /news/날짜/기사id/        기사 하나
    /이름/                   고정 페이지 (content/pages/*.json 이 있을 때만. 지금은 없다)

    python3 tools/build.py            # 저장소 루트에 생성
    python3 tools/build.py --check    # 검증만 하고 파일은 쓰지 않음
    python3 tools/build.py --out DIR  # 다른 폴더에 생성

규칙 (자세한 형식은 docs/content-format.md)
- 공개 입력에는 개인 전용 내용을 둘 수 없다: 'actions', 'side.career', 'private' 표시는 오류.
- 기사에는 고유 id 와 출처가 있어야 한다. 주요 뉴스(quick)는 기사에 연결되고 숫자가 기사와 맞아야 한다.
- 게시한 기사 주소는 지우지 않는다. 같은 날짜의 새 판에서 뺀 기사는 earlier(이전 판 기사)로 옮기고,
  내리는 기사는 withdrawn 에 적는다. 이미 만든 기사 페이지가 둘 다에 없으면 생성을 멈춘다.
- 날짜·기준 시각·일정·시장 수치의 기준일을 검사한다.
- 금지 규칙은 입력 JSON 전체와 생성 결과 전체에서 검사한다. 오류에는 파일·필드·규칙 번호만 적는다 (규칙 원문은 적지 않는다).
- 검증에 실패하면 아무 파일도 쓰지 않는다.

표준 라이브러리만 쓴다 (Python 3.9 이상).
"""
import argparse
import datetime
import html
import json
import posixpath
import re
import shutil
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
ROOT_DIR = TOOLS_DIR.parent
TEMPLATE = TOOLS_DIR / "templates" / "base.html"
LOCAL_BLOCKLIST = Path("보관") / "blocklist.local.txt"

WEEKDAYS = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"]
KST = datetime.timezone(datetime.timedelta(hours=9))
WORD_JOINER = "⁠"  # 가운뎃점 뒤에서 줄이 끊기지 않게 한다
DAY_FILE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.json$")
DAY_DIR = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SLUG = re.compile(r"^[a-z0-9-]+$")
CHECKED_AT = re.compile(r"^(\d{2}):(\d{2}) KST$")
HOME_LABEL = "홈"          # 메뉴에 쓰는 이름
NEWS_LABEL = "날짜별"
NEWS_TITLE = "날짜별 브리핑"  # 그 페이지의 제목
ITEM_STATUS = ("신규", "진전", "보완")   # 노션 v2 기록 줄의 상태. 화면에는 진전·보완만 작은 글자로 보인다
SHOWN_STATUS = ("진전", "보완")
BASIS_MAX = 24                            # basis(전망·잠정·단일 보도 등 근거 문구)의 최대 글자 수
SUMMARY_MAX = 110                         # v2 판부터 summary(대표 제목의 짧은 의미)의 최대 글자 수
RELATED_MAX = 5                           # 기사 페이지의 '이 브리핑의 다른 기사' 줄 수 (같은 분야 먼저)
# 기사체 점검 규칙: (이름, 정규식, 대상 칸). 제작 단계가 'AI 티'나 점검표·훈계 말투를 내지 않게 한다.
STYLE_RULES = [
    ("굵게 표시(**)", r"\*\*", ("headline", "summary", "quick", "title", "teaser", "facts", "so", "recheck", "tip")),
    ("색 표시는 주요 뉴스 줄에만", r"\{\{(up|down):", ("headline", "summary", "title", "teaser", "facts", "so", "recheck", "tip")),
    ("주요 뉴스 색 표시 안의 부호(+/-)", r"\{\{(up|down):[+\-−]", ("quick",)),
    ("점검표 말투(~확인한다/~본다)", r"(확인한다|본다)\.?\s*$", ("so", "recheck", "tip")),
    ("훈계 말투(~봐야/해야 한다, ~필요가 있다, ~서는 안 된다)", r"(봐야|해야|읽어야) 한다\.|필요가 있다\.|서는 안 된다\.",
     ("summary", "teaser", "so", "recheck", "tip")),
    ("편집 메모(기사 참고·일반적인 설명 등)", r"기사 참고\)|일반적인 설명\)|이번에 확인한 자료", ("summary", "teaser", "facts", "so", "recheck", "tip")),
]
# 공개 입력(content/날짜.json)에 쓸 수 있는 항목. 여기에 없는 항목이 있으면 생성을 멈춘다.
# 개인 원본의 다른 칸이나 제작용 운영 정보(판 상태·개정·내부 키 등)가 공개 파일에 섞여 들어오지 않게 하려는 것이다.
ALLOWED = {
    "day": {"date", "edition", "checked_at", "headline", "summary", "lead_ref", "market", "quick", "sections",
            "side", "updated_at", "corrections", "earlier", "withdrawn", "indicators"},
    "indicators": {"as_of", "source", "rows"},
    "indicator_row": {"key", "name", "value", "change", "rate", "dir", "time", "spark", "base"},
    "section": {"id", "title", "items", "lede", "points"},
    "item": {"id", "date", "featured", "title", "teaser", "facts", "so", "recheck", "tip", "sources",
             "status", "basis", "follows"},
    "recheck": {"label", "text"},
    "source": {"name", "url"},
    "follows": {"date", "id"},
    "market": {"rows", "note", "sources", "forecast"},
    "forecast": {"title", "rows"},
    "market_row": {"name", "note", "basis", "asof", "value", "change", "dir", "source"},
    "side": {"absent"},                # 다음 일정(schedule)은 2026-10-24부터 싣지 않는다
    "absent": {"name", "note"},
    "quick": {"text", "ref"},
    "correction": {"date", "text", "ref"},
    "earlier": {"edition", "checked_at", "sections"},
    "withdrawn": {"id", "reason"},
}
# 맨 위 시장 지표 12개와 순서 (tools/market_snapshot.py 가 제작 시점에 가져와 indicators 에 넣는다)
TICKER_KEYS = ("kospi", "kosdaq", "sp500", "nasdaq", "dow", "usd", "eur", "wti", "gold", "us10y", "dxy", "btc")
TICKER_PAGE = 6   # 좁은 화면에서 한 쪽에 보이는 칸 수 (3칸 × 2줄)
RETIRED_DIRS = ("topic", "about")  # 없앤 페이지(분야별·소개, 2026-10-24). 이번 생성에 없으면 생성 파일만 지운다
RESERVED_IDS = {"brief", "top-title", "market-title", "quick-title", "recent-title", "topics-title",
                "today-title", "intro-title", "related-title", "others-title", "h-absent",
                "h-corrections", "fix-title", "ticker-title"}

_BOLD = re.compile(r"\*\*(.+?)\*\*")
_DIRECTION = re.compile(r"\{\{(up|down):(.+?)\}\}")
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?%?")

class BuildError(Exception):
    """내용 파일 오류나 금지 단어 검출처럼 생성을 멈춰야 하는 경우."""


# ---------------------------------------------------------------- 텍스트 도구

def inline(text):
    """사용자 텍스트를 HTML로: 먼저 이스케이프하고, 허용한 마크업만 되살린다."""
    s = html.escape(str(text), quote=False).replace("·", "·" + WORD_JOINER)
    s = _BOLD.sub(r"<strong>\1</strong>", s)
    return _DIRECTION.sub(r'<strong class="\1">\2</strong>', s)


def plain(text):
    s = _BOLD.sub(r"\1", str(text))
    return _DIRECTION.sub(r"\2", s)


def esc(text):
    return html.escape(str(text), quote=True)


def first_sentence(text):
    """첫 문장. 마침표 뒤에 공백이 올 때만 끊어 소수점(5.46%)은 건드리지 않는다."""
    return re.split(r"(?<=\.)\s+", plain(text).strip(), maxsplit=1)[0]


def parse_date(value):
    return datetime.date.fromisoformat(value)


def short_date(value):
    d = parse_date(value)
    return "%d월 %d일" % (d.month, d.day)


def weekday_ko(value):
    return WEEKDAYS[parse_date(value).weekday()]


def long_date(value):
    d = parse_date(value)
    return "%d년 %d월 %d일 %s" % (d.year, d.month, d.day, WEEKDAYS[d.weekday()])


def month_label(value):
    d = parse_date(value)
    return "%d년 %d월" % (d.year, d.month)


def briefing_time(day):
    """브리핑 기준 시각 (KST). checked_at 이 없으면 그날 0시."""
    d = parse_date(day["date"])
    m = CHECKED_AT.match(day.get("checked_at") or "")
    hh, mm = (int(m.group(1)), int(m.group(2))) if m else (0, 0)
    return datetime.datetime(d.year, d.month, d.day, hh, mm, tzinfo=KST)


_DATE_ONLY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def parse_updated(day, where=""):
    """게시한 판을 고친 시각 updated_at 을 읽는다. 돌려주는 값: (한국 시각, 날짜만 있는지).
    없으면 (None, False). 날짜만 있으면 그날 0시(한국 시각)로 두고 date_only=True 로 알린다.
    형식이 틀리면 where 가 있을 때 BuildError, 없을 때 (None, False)."""
    value = day.get("updated_at")
    if value is None:
        return None, False
    try:
        if not isinstance(value, str):
            raise ValueError
        if _DATE_ONLY.match(value):
            d = datetime.date.fromisoformat(value)
            return datetime.datetime(d.year, d.month, d.day, tzinfo=KST), True
        text = value[:-1] + "+00:00" if value.endswith("Z") else value  # Python 3.9 는 Z 를 읽지 못한다
        at = datetime.datetime.fromisoformat(text)
        if at.tzinfo is None:
            raise ValueError
        return at.astimezone(KST), False
    except ValueError:
        if where:
            raise BuildError("%s: updated_at 은 시간대가 붙은 시각(2026-10-14T10:47:00+09:00)이나 "
                             "날짜(2026-10-14)여야 합니다" % where)
        return None, False


def updated_label(day):
    """화면에 보이는 수정 표시. 날짜만 알면 날짜만 쓴다 (없는 시각을 만들지 않는다)."""
    at, date_only = parse_updated(day)
    if at is None:
        return ""
    if date_only:
        return "수정 %d월 %d일" % (at.month, at.day)
    return "수정 %d월 %d일 %02d:%02d KST" % (at.month, at.day, at.hour, at.minute)


def modified_value(day):
    """메타데이터용 수정 시각 문자열. 날짜만 알면 날짜만."""
    at, date_only = parse_updated(day)
    if at is None:
        return ""
    return at.date().isoformat() if date_only else at.isoformat()


def visible_sections(day):
    return [s for s in day["sections"] if s["items"]]


def all_items(day):
    return [(s, it) for s in visible_sections(day) for it in s["items"]]


def earlier_items(day):
    """이전 판 기사: [(판, 분야, 기사)]. 새 판에서 빠졌지만 주소는 남는다."""
    return [(g, s, it) for g in day.get("earlier") or [] for s in g["sections"] for it in s["items"]]


def edition_view(day, group):
    """이전 판 기사 페이지에 쓰는 그날 정보. 판 이름과 확인 시각은 그 판의 것이고, 지금 판의 수정 시각은 붙이지 않는다."""
    view = {k: v for k, v in day.items() if k not in ("edition", "checked_at", "updated_at")}
    view["edition"] = group["edition"]
    if group.get("checked_at"):
        view["checked_at"] = group["checked_at"]
    return view


def published_ids(day):
    """그날 주소가 있는 기사: 지금 판과 이전 판 기사."""
    return {it["id"] for _, it in all_items(day)} | {it["id"] for _, _, it in earlier_items(day)}


def withdrawn_ids(day):
    return {w["id"] for w in day.get("withdrawn") or []}


# ---------------------------------------------------------------- 읽기·검증

def _read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise BuildError("%s 파일이 없습니다" % path)
    except json.JSONDecodeError as e:
        raise BuildError("%s: JSON 형식 오류 (%d번째 줄)" % (path.name, e.lineno))


def iter_strings(obj, path="", skip=()):
    """JSON 안의 모든 문자열과 그 위치(sections[0].items[1].facts 형식)를 낸다."""
    if isinstance(obj, str):
        yield path, obj
    elif isinstance(obj, dict):
        for key, value in obj.items():
            if not path and key in skip:
                continue
            yield from iter_strings(value, "%s.%s" % (path, key) if path else str(key))
    elif isinstance(obj, list):
        for i, value in enumerate(obj):
            yield from iter_strings(value, "%s[%d]" % (path, i))


def iter_private_flags(obj, path=""):
    if isinstance(obj, dict):
        for key, value in obj.items():
            child = "%s.%s" % (path, key) if path else str(key)
            if key == "private":
                yield child
            yield from iter_private_flags(value, child)
    elif isinstance(obj, list):
        for i, value in enumerate(obj):
            yield from iter_private_flags(value, "%s[%d]" % (path, i))


def reject_personal_only(data, filename):
    """공개 입력에 있으면 안 되는 것: private 표시, 개인 전용 블록."""
    for path in iter_private_flags(data):
        raise BuildError("%s: %s — 공개 입력에는 'private' 표시를 둘 수 없습니다. "
                         "비공개 내용은 숨기지 말고 입력에서 삭제하세요" % (filename, path))
    if "actions" in data:
        raise BuildError("%s: actions — 개인 전용 블록은 공개 입력에 둘 수 없습니다" % filename)
    side = data.get("side")
    if isinstance(side, dict) and "career" in side:
        raise BuildError("%s: side.career — 개인 전용 블록은 공개 입력에 둘 수 없습니다" % filename)


def load_site(root):
    site = _read_json(root / "content" / "_site.json")
    reject_personal_only(site, "_site.json")
    for key in ("brand", "title", "base_url"):
        if not site.get(key):
            raise BuildError("_site.json: 필수 항목 '%s' 가 없습니다" % key)
    if not re.match(r"^https?://[^/]+$", site["base_url"]):
        raise BuildError("_site.json: base_url 은 http(s)://도메인 형식이어야 합니다 (끝에 / 없음)")
    site.setdefault("description", "")
    site.setdefault("footer_meta", [])
    site.setdefault("disclaimer", "")
    site.setdefault("blocklist", [])
    site.setdefault("require_sources_from", "0001-01-01")
    site.setdefault("market_max_age_days", 4)
    site.setdefault("v2_from", "9999-12-31")  # 이 날짜의 판부터 v2 규칙(행별 근거, 짧은 의미, 대표 기사)을 적용한다
    site.setdefault("style_from", "9999-12-31")  # 이 날짜의 판부터 기사체 점검(STYLE_RULES)을 적용한다
    site.setdefault("context_rules", [])
    site.setdefault("review_terms", [])
    site.setdefault("tagline", "")
    return site


def _need(obj, key, where):
    if not isinstance(obj, dict) or obj.get(key) in (None, ""):
        raise BuildError("%s: 필수 항목 '%s' 가 없습니다" % (where, key))


def _check_keys(obj, kind, where):
    """obj 가 사전이고 ALLOWED[kind] 에 있는 항목만 쓰는지 본다. 오류에는 항목 이름만 적는다 (값은 적지 않는다)."""
    if not isinstance(obj, dict):
        raise BuildError("%s: 형식이 잘못됐습니다 (사전이어야 합니다)" % where)
    extra = sorted(set(obj) - ALLOWED[kind])
    if extra:
        raise BuildError("%s: 공개 입력에 쓸 수 없는 항목 %s — 허용된 항목만 옮기세요" % (where, ", ".join(extra)))


def _check_row_source(row, where, required):
    """시장·일정 한 줄의 근거(source: {name, url}). required 면 없을 때 멈춘다."""
    src = row.get("source")
    if src is None:
        if required:
            raise BuildError("%s: source — 줄마다 근거 링크가 있어야 합니다" % where)
        return
    _check_keys(src, "source", where + ".source")
    _check_sources([src], where + ".source")


def _check_indicators(ind, where):
    """맨 위 시장 지표 (indicators). 정해진 12개 키만, 정해진 순서로. 값은 문자열, 차트는 0~100 정수."""
    _check_keys(ind, "indicators", where)
    try:
        at = datetime.datetime.fromisoformat(ind.get("as_of") or "")
    except (TypeError, ValueError):
        at = None
    if at is None or at.tzinfo is None:
        raise BuildError("%s: as_of 는 시간대가 붙은 ISO 시각이어야 합니다" % where)
    rows = ind.get("rows")
    if not isinstance(rows, list) or not 1 <= len(rows) <= len(TICKER_KEYS):
        raise BuildError("%s: rows 는 지표 1~%d개 목록이어야 합니다" % (where, len(TICKER_KEYS)))
    keys = [r.get("key") if isinstance(r, dict) else None for r in rows]
    order = [k for k in TICKER_KEYS if k in keys]
    if keys != order:
        raise BuildError("%s: 지표는 정해진 12개(%s) 안에서 정해진 순서로 한 번씩만 씁니다" % (where, ", ".join(TICKER_KEYS)))
    for n, r in enumerate(rows):
        w = "%s.rows[%d]" % (where, n)
        _check_keys(r, "indicator_row", w)
        for key in ("name", "value", "change", "rate", "time"):
            _need(r, key, w)
            if not isinstance(r[key], str):
                raise BuildError("%s: %s 는 문자열이어야 합니다" % (w, key))
        if r.get("dir") not in ("up", "down", "flat"):
            raise BuildError("%s: dir 은 up·down·flat 중 하나여야 합니다" % w)
        spark = r.get("spark", [])
        if not isinstance(spark, list) or len(spark) > 120 or any(
                not isinstance(v, int) or isinstance(v, bool) or not 0 <= v <= 100 for v in spark):
            raise BuildError("%s: spark 는 0~100 정수 120개 이하여야 합니다" % w)
        base = r.get("base")
        if base is not None and (not isinstance(base, int) or isinstance(base, bool) or not 0 <= base <= 100):
            raise BuildError("%s: base 는 0~100 정수여야 합니다" % w)


def style_problems(day):
    """기사체 점검. 걸린 곳을 '칸 — 규칙 이름'으로 돌려준다 (원문은 적지 않는다)."""
    texts = [("headline", "headline", day.get("headline") or ""), ("summary", "summary", day.get("summary") or "")]
    texts += [("quick", "quick[%d]" % n, q.get("text") or "") for n, q in enumerate(day.get("quick") or [])
              if isinstance(q, dict)]
    groups = [day.get("sections") or []] + [g.get("sections") or [] for g in day.get("earlier") or []]
    for secs in groups:
        for sec in secs:
            for item in sec.get("items") or []:
                iid = item.get("id", "?")
                for key in ("title", "teaser", "facts", "so", "tip"):
                    if item.get(key):
                        texts.append((key, "%s.%s" % (iid, key), item[key]))
                recheck = item.get("recheck")
                if isinstance(recheck, dict) and recheck.get("text"):
                    texts.append(("recheck", "%s.recheck" % iid, recheck["text"]))
    problems = []
    for kind, where, text in texts:
        for name, pattern, kinds in STYLE_RULES:
            if kind in kinds and re.search(pattern, text):
                problems.append("%s — %s" % (where, name))
    return problems


def _check_date(value, where):
    try:
        return parse_date(value)
    except (ValueError, TypeError):
        raise BuildError("%s: 날짜는 YYYY-MM-DD 형식이어야 합니다" % where)


def _check_sources(sources, where):
    seen = set()
    for src in sources:
        _need(src, "name", where)
        _need(src, "url", where)
        if not re.match(r"^https?://", src["url"]):
            raise BuildError("%s: 출처 주소는 http(s):// 로 시작해야 합니다" % where)
        if src["url"] in seen:
            raise BuildError("%s: 같은 출처 주소가 중복됩니다" % where)
        seen.add(src["url"])


def _check_item(item, where, date, need_sources, ids):
    """기사 하나를 검사하고 id 를 ids 에 더한다. 지금 판의 기사와 이전 판 기사(earlier)가 같은 검사를 받는다."""
    _check_keys(item, "item", where)
    for key in ("id", "date", "title", "facts"):
        _need(item, key, where)
    iid = item["id"]
    where = "%s (%s)" % (where, iid)
    if not SLUG.match(iid):
        raise BuildError("%s: 기사 id 는 영문 소문자·숫자·하이픈만 쓸 수 있습니다" % where)
    if iid in ids:
        raise BuildError("%s: id '%s' 가 중복됩니다" % (where, iid))
    ids.add(iid)
    if "teaser" in item:
        teaser = item["teaser"]
        if not isinstance(teaser, str) or not teaser.strip():
            raise BuildError("%s: teaser 는 비어 있지 않은 문장이어야 합니다" % where)
        recheck = item.get("recheck") if isinstance(item.get("recheck"), dict) else {}
        body = plain(" ".join(str(v) for v in (item["title"], item["facts"], item.get("so", ""),
                                                recheck.get("text", ""))))
        for num in _NUMBER.findall(plain(teaser)):
            if num not in body:
                raise BuildError("%s: teaser — 숫자 '%s' 가 기사 본문에 없습니다" % (where, num))
    if _check_date(item["date"], where) > date:
        raise BuildError("%s: 기사 날짜가 브리핑 날짜보다 뒤입니다" % where)
    if "recheck" in item:
        _check_keys(item["recheck"], "recheck", where + ".recheck")
    if "tip" in item and (not isinstance(item["tip"], str) or not item["tip"].strip()):
        raise BuildError("%s: tip(알아둘 점)은 비어 있지 않은 문장이어야 합니다" % where)
    if "status" in item and item["status"] not in ITEM_STATUS:
        raise BuildError("%s: status 는 %s 중 하나여야 합니다" % (where, "·".join(ITEM_STATUS)))
    if "basis" in item:
        basis = item["basis"]
        if not isinstance(basis, str) or not basis.strip() or len(basis) > BASIS_MAX:
            raise BuildError("%s: basis 는 %d자 이내의 짧은 근거 문구여야 합니다" % (where, BASIS_MAX))
    if "follows" in item:
        fol = item["follows"]
        _check_keys(fol, "follows", where + ".follows")
        _need(fol, "date", where + ".follows")
        _need(fol, "id", where + ".follows")
        if _check_date(fol["date"], where + ".follows") >= date:
            raise BuildError("%s: follows 는 앞선 날짜의 기사를 가리켜야 합니다 (같은 날은 같은 id 를 씁니다)" % where)
    sources = item.get("sources") or []
    for n, src in enumerate(sources):
        _check_keys(src, "source", "%s.sources[%d]" % (where, n))
    if need_sources and not sources:
        raise BuildError("%s: sources — 기사에는 출처가 하나 이상 있어야 합니다" % where)
    _check_sources(sources, where)


def _check_earlier(day, filename, date, need_sources, ids, now):
    """이전 판 기사(earlier): 같은 날짜의 새 판에서 빠졌지만 주소를 남기는 기사.
    [{"edition", "checked_at", "sections": [{"id", "title", "items"}]}]. 돌려주는 값: 기사 id 집합."""
    groups = day.get("earlier")
    if groups is None:
        return set()
    if not isinstance(groups, list):
        raise BuildError("%s: earlier 는 목록이어야 합니다" % filename)
    found = set()
    for g, group in enumerate(groups):
        where = "%s earlier[%d]" % (filename, g)
        _check_keys(group, "earlier", where)
        _need(group, "edition", where)
        checked = group.get("checked_at")
        if checked:
            if not CHECKED_AT.match(checked):
                raise BuildError("%s: checked_at 은 'HH:MM KST' 형식이어야 합니다" % where)
            if day.get("checked_at") and briefing_time({"date": day["date"], "checked_at": checked}) > now:
                raise BuildError("%s: 이전 판의 checked_at 이 지금 판보다 늦습니다" % where)
        if not isinstance(group.get("sections"), list) or not group["sections"]:
            raise BuildError("%s: 필수 항목 'sections' 가 없습니다" % where)
        for sec in group["sections"]:
            swhere = "%s 섹션" % where
            _check_keys(sec, "section", swhere)
            for key in ("id", "title"):
                _need(sec, key, swhere)
            if not SLUG.match(sec["id"]):
                raise BuildError("%s: 섹션 id '%s' 는 영문 소문자·숫자·하이픈만 쓸 수 있습니다" % (swhere, sec["id"]))
            if not isinstance(sec.get("items"), list) or not sec["items"]:
                raise BuildError("%s: 필수 항목 'items' 가 없습니다" % swhere)
            for n, item in enumerate(sec["items"]):
                _check_item(item, "%s %s.items[%d]" % (where, sec["id"], n), date, need_sources, ids)
                found.add(item["id"])
    return found


def _check_withdrawn(day, filename, published):
    """철회(withdrawn): 게시했다가 내린 기사. id 와 공개할 수 있는 짧은 사유만 둔다 (기사 내용은 남기지 않는다).
    published: 그날 지금 판·이전 판 기사의 id."""
    rows_ = day.get("withdrawn")
    if rows_ is None:
        return
    if not isinstance(rows_, list):
        raise BuildError("%s: withdrawn 은 목록이어야 합니다" % filename)
    seen = set()
    for n, w in enumerate(rows_):
        where = "%s withdrawn[%d]" % (filename, n)
        _check_keys(w, "withdrawn", where)
        _need(w, "id", where)
        _need(w, "reason", where)
        wid = w["id"]
        if not isinstance(wid, str) or not SLUG.match(wid):
            raise BuildError("%s: 기사 id 는 영문 소문자·숫자·하이픈만 쓸 수 있습니다" % where)
        if wid in published:
            raise BuildError("%s: 철회한 기사 '%s' 가 아직 내용(sections 또는 earlier)에 있습니다" % (where, wid))
        if wid in seen:
            raise BuildError("%s: '%s' 를 두 번 철회했습니다" % (where, wid))
        seen.add(wid)


def validate_day(day, name_date, filename, site):
    reject_personal_only(day, filename)
    _check_keys(day, "day", filename)
    for key in ("date", "headline", "summary", "sections"):
        _need(day, key, filename)
    date = _check_date(day["date"], filename)
    if day["date"] != name_date:
        raise BuildError("%s: 파일 이름의 날짜와 date 항목이 다릅니다" % filename)
    if day.get("checked_at") and not CHECKED_AT.match(day["checked_at"]):
        raise BuildError("%s: checked_at 은 'HH:MM KST' 형식이어야 합니다" % filename)
    now = briefing_time(day)
    if "updated_at" in day:
        at, date_only = parse_updated(day, filename)
        if (at.date() < date) if date_only else (at < now):
            raise BuildError("%s: updated_at 이 브리핑 기준 시각보다 이릅니다" % filename)
    need_sources = day["date"] >= site["require_sources_from"]

    ids = set(RESERVED_IDS)
    items_by_id = {}
    for sec in day["sections"]:
        where = "%s 섹션" % filename
        _check_keys(sec, "section", where)
        for key in ("id", "title"):
            _need(sec, key, where)
        if not isinstance(sec.get("items"), list):
            raise BuildError("%s: 필수 항목 'items' 가 없습니다" % where)
        sid = sec["id"]
        if not SLUG.match(sid):
            raise BuildError("%s: 섹션 id '%s' 는 영문 소문자·숫자·하이픈만 쓸 수 있습니다" % (where, sid))
        if sid in ids:
            raise BuildError("%s: id '%s' 가 중복됩니다" % (where, sid))
        ids.add(sid)
        for n, item in enumerate(sec["items"]):
            _check_item(item, "%s %s.items[%d]" % (filename, sid, n), date, need_sources, ids)
            items_by_id[item["id"]] = item
    earlier = _check_earlier(day, filename, date, need_sources, ids, now)
    _check_withdrawn(day, filename, set(items_by_id) | earlier)
    if day["date"] >= site["style_from"]:
        problems = style_problems(day)
        if problems:
            raise BuildError("%s: 기사체 점검에 걸렸습니다 (docs/prompts/briefing.md 의 '문체'):\n  %s"
                             % (filename, "\n  ".join(problems)))
    v2 = day["date"] >= site["v2_from"]
    if "indicators" in day:
        _check_indicators(day["indicators"], "%s indicators" % filename)
    if v2 and "lead_ref" not in day:
        raise BuildError("%s: lead_ref — 대표 제목이 다루는 기사(대표 기사)의 id 가 있어야 합니다" % filename)
    if "lead_ref" in day and day["lead_ref"] not in items_by_id:
        raise BuildError("%s: lead_ref 가 지금 판의 기사를 가리켜야 합니다" % filename)
    if v2 and len(plain(day["summary"]).strip()) > SUMMARY_MAX:
        raise BuildError("%s: summary 는 %d자 이내로 대표 사건의 의미만 씁니다 (다른 사건은 주요 뉴스(quick)에)" % (filename, SUMMARY_MAX))
    headline = plain(day["headline"]).strip()
    if plain(day["summary"]).strip() == headline:
        raise BuildError("%s: summary 가 headline 과 같은 문장입니다 — 대표 제목의 의미를 따로 쓰세요" % filename)

    for n, q in enumerate(day.get("quick") or []):
        where = "%s quick[%d]" % (filename, n)
        if isinstance(q, dict):
            _check_keys(q, "quick", where)
        if isinstance(q, dict) and plain(q.get("text") or "").strip() == headline:
            raise BuildError("%s: 주요 뉴스(quick) 줄이 headline 과 같은 문장입니다" % where)
        if not isinstance(q, dict) or not q.get("text") or not q.get("ref"):
            raise BuildError("%s: 주요 뉴스(quick) 항목은 {\"text\", \"ref\"} 형태로 기사에 연결해야 합니다" % where)
        item = items_by_id.get(q["ref"])
        if item is None:
            raise BuildError("%s: ref 가 가리키는 기사가 없습니다" % where)
        body = plain(" ".join(str(item.get(k, "")) for k in ("title", "facts", "so")))
        for num in _NUMBER.findall(plain(q["text"])):
            if num not in body:
                raise BuildError("%s: 숫자 '%s' 가 연결된 기사 본문에 없습니다" % (where, num))

    need_row_sources = v2
    market = day.get("market") or {}
    if "market" in day:
        _check_keys(market, "market", "%s market" % filename)
    if "forecast" in market:
        _check_keys(market["forecast"], "forecast", "%s market.forecast" % filename)
    rows = list(market.get("rows") or []) + list((market.get("forecast") or {}).get("rows") or [])
    for n, row in enumerate(rows):
        where = "%s market.rows[%d]" % (filename, n)
        _check_keys(row, "market_row", where)
        _need(row, "name", where)
        _check_row_source(row, where, need_row_sources)
        if row.get("asof"):
            asof = _check_date(row["asof"], where)
            if asof > date:
                raise BuildError("%s: 기준일이 브리핑 날짜보다 뒤입니다" % where)
            if (date - asof).days > site["market_max_age_days"]:
                raise BuildError("%s: 기준일이 너무 오래됐습니다 (%d일 초과)" % (where, site["market_max_age_days"]))
    _check_sources(market.get("sources") or [], "%s market.sources" % filename)

    side = day.get("side") or {}
    if isinstance(side, dict) and "schedule" in side:
        raise BuildError("%s: side.schedule — 다음 일정은 2026-10-24부터 사이트에 싣지 않습니다. 입력에서 빼세요" % filename)
    if "side" in day:
        _check_keys(side, "side", "%s side" % filename)
    for n, a in enumerate(side.get("absent") or []):
        _check_keys(a, "absent", "%s side.absent[%d]" % (filename, n))

    targets = set(items_by_id) | earlier | withdrawn_ids(day)
    for n, c in enumerate(day.get("corrections") or []):
        where = "%s corrections[%d]" % (filename, n)
        _check_keys(c, "correction", where)
        _need(c, "date", where)
        _need(c, "text", where)
        _check_date(c["date"], where)
        if "ref" in c and c["ref"] not in targets:
            raise BuildError("%s: ref 가 그날의 기사(지금 판·이전 판·철회)를 가리켜야 합니다" % where)


def load_days(root, site):
    days = []
    for path in sorted((root / "content").glob("*.json")):
        m = DAY_FILE.match(path.name)
        if not m:
            continue  # _site.json 등
        day = _read_json(path)
        validate_day(day, m.group(1), path.name, site)
        days.append(day)
    days.sort(key=lambda d: d["date"], reverse=True)
    return days


def article_index(days):
    """(날짜, 기사 id) → (그날, 분야, 기사). 지금 판과 이전 판 기사를 모두 담는다."""
    index = {}
    for day in days:
        for sec, item in all_items(day):
            index[(day["date"], item["id"])] = (day, sec, item)
        for group, sec, item in earlier_items(day):
            index[(day["date"], item["id"])] = (edition_view(day, group), sec, item)
    return index


def check_follows(days, index):
    """follows(다른 날 후속 기사)가 실제로 있는 앞선 기사를 가리키는지 본다. 철회한 기사는 가리킬 수 없다."""
    for day in days:
        for sec, item in all_items(day) + [(s, i) for _, s, i in earlier_items(day)]:
            fol = item.get("follows")
            if fol and (fol["date"], fol["id"]) not in index:
                raise BuildError("%s.json (%s): follows 가 가리키는 앞선 기사가 없습니다" % (day["date"], item["id"]))


def load_pages(root):
    pages = []
    folder = root / "content" / "pages"
    for path in sorted(folder.glob("*.json")) if folder.is_dir() else []:
        page = _read_json(path)
        where = "pages/%s" % path.name
        reject_personal_only(page, where)
        for key in ("slug", "title", "sections"):
            _need(page, key, where)
        if not SLUG.match(page["slug"]) or page["slug"] in ("news", "assets", "content", "tools", "docs"):
            raise BuildError("%s: slug 를 쓸 수 없습니다" % where)
        pages.append(page)
    return pages


# ---------------------------------------------------------------- 구성요소
#
# 페이지는 네 가지다. 같은 정보는 한 곳에만 둔다.
#   홈      제호 아래 한 줄 소개 + 최신 브리핑 (브리핑 페이지와 같은 구성)
#   브리핑  맨 위 시장 지표 12개(제작 시점 값) → 날짜·판·기준 시각 → 대표 제목 → 짧은 의미
#           → 주요 뉴스(줄마다 본문 기사로 이동)
#           → 분야별 기사 전문(사실 문단 → 해설 → 전망 → 관련 기사 → 출처, 같은 날 이전 판 기사도 분야 안에) → 바로잡습니다 → 이전·다음
#   기사    분야·실린 판, 제목, 기준·수정 시각, 요약 한 줄, 진행·근거 표시, 본문, 관련 기사, 출처,
#           바로잡습니다, 이 브리핑의 다른 기사, 브리핑으로 돌아가기
#   날짜별  날마다 한 칸, 최신순: 날짜·판, 대표 제목, 짧은 요약, 그날 실린 기사 줄 (전문은 다시 싣지 않는다)
# 철회한 기사의 주소에는 내린 사유만 적은 안내 페이지를 남긴다 (검색 제외).
# 누를 수 있는 것은 모두 청록색 글자·줄이다. 펼치는 버튼과 스크립트는 없다.

def render_sources(sources):
    if not sources:
        return ""
    links = " ".join('<a href="%s" target="_blank" rel="noopener">%s</a>'
                     % (esc(s["url"]), inline(s["name"])) for s in sources)
    return '<p class="src"><span>출처<span class="sr"> (새 창에서 열림)</span></span> %s</p>' % links


def _sparkline(points, base):
    """작은 흐름 선 (SVG). 점수는 0~100 (100이 가장 높음), base 는 전 거래일 종가 높이(점선)."""
    if len(points) < 2:
        return ""
    step = 100 / (len(points) - 1)
    y = lambda v: 2 + (100 - v) * 0.26
    line = " ".join("%.1f,%.1f" % (i * step, y(v)) for i, v in enumerate(points))
    base_line = ('<line x1="0" x2="100" y1="%.1f" y2="%.1f"/>' % (y(base), y(base))) if base is not None else ""
    return ('<svg class="tk-s" viewBox="0 0 100 30" preserveAspectRatio="none" aria-hidden="true" focusable="false">'
            '%s<polyline points="%s"/></svg>' % (base_line, line))


def render_ticker(day):
    """맨 위 시장 지표: 정해진 12개, 좁은 화면에서는 6개씩 두 쪽(옆으로 넘김), 넓은 화면에서는 한 줄에 다 보인다.
    값은 제작 시점에 가져온 것이고, 칸마다 그 값의 시각(장마감이면 거래일)을 적는다."""
    ind = day.get("indicators")
    if not ind:
        return ""
    words = {"up": "상승", "down": "하락", "flat": "보합"}
    tiles = []
    for r in ind["rows"]:
        tiles.append('<li class="tk %s"><span class="tk-n">%s</span><span class="tk-v">%s</span>'
                     '<span class="tk-c"><span class="sr">%s </span>%s <span class="tk-r">(%s)</span></span>%s'
                     '<span class="tk-t">%s</span></li>'
                     % (r["dir"], esc(r["name"]), esc(r["value"]), words[r["dir"]], esc(r["change"]), esc(r["rate"]),
                        _sparkline(r.get("spark") or [], r.get("base")), esc(r["time"])))
    pages = "".join('<ul class="tk-page">%s</ul>' % "".join(tiles[i:i + TICKER_PAGE])
                    for i in range(0, len(tiles), TICKER_PAGE))
    at = datetime.datetime.fromisoformat(ind["as_of"]).astimezone(KST)
    return ('<section class="ticker" aria-labelledby="ticker-title"><h2 id="ticker-title" class="sr">시장 지표</h2>'
            '<div class="tk-pages" role="region" aria-label="시장 지표, 옆으로 넘겨 보기" tabindex="0">%s</div>'
            '<p class="tk-src">%d월 %d일 %02d:%02d 기준 · 자료: %s</p></section>'
            % (pages, at.month, at.day, at.hour, at.minute, esc(ind.get("source") or "")))


def render_quick(day):
    """주요 뉴스(quick). 줄마다 같은 페이지의 그 기사(#기사id)로 이동한다. 앞의 세 줄은 조금 더 크게 보인다 (class top).
    줄 수는 입력 그대로다 (중요 기사가 적으면 적은 대로)."""
    items = day.get("quick") or []
    if not items:
        return ""
    lis = "".join('<li%s><a href="#%s">%s</a></li>' % (' class="top"' if n < 3 else "", esc(q["ref"]), inline(q["text"]))
                  for n, q in enumerate(items))
    return ('<section class="quick" aria-labelledby="quick-title">'
            '<h2 id="quick-title">주요 뉴스</h2><ol>%s</ol></section>' % lis)


def item_meta(item, day):
    """기사 제목 아래 작은 글자 한 줄: 진행(진전·보완), 사건 날짜(브리핑 날짜와 다를 때),
    근거 문구(basis). '신규'는 대부분이라 표시하지 않는다. 포괄적인 '검증 완료' 같은 표시는 만들지 않는다."""
    parts = []
    if item.get("status") in SHOWN_STATUS:
        parts.append('<span class="st">%s</span>' % item["status"])
    if item["date"] != day["date"]:
        parts.append(date_meta(item["date"]))
    if item.get("basis"):
        parts.append("<span>%s</span>" % inline(item["basis"]))
    return '<p class="meta-line">%s</p>' % "".join(parts) if parts else ""


def render_follows(item, index, days_prefix):
    """다른 날 후속 기사면 앞선 기사로 가는 '관련 기사' 줄. days_prefix: 이 페이지에서 news/ 폴더까지의 상대 경로."""
    fol = item.get("follows")
    if not fol:
        return ""
    target = index.get((fol["date"], fol["id"]))
    title = target[2]["title"] if target else fol["id"]
    return ('<p class="follows">관련 기사 <a href="%s%s/%s/index.html">%s · %s</a></p>'
            % (days_prefix, fol["date"], esc(fol["id"]), short_date(fol["date"]), inline(title)))


def row(href, title, meta="", line="", rid=""):
    """다른 페이지로 가는 줄 하나: 작은 머리글(meta, 이미 만든 HTML), 제목, 한 줄. 사이트의 모든 목록이 이 모양이다."""
    parts = []
    if meta:
        parts.append('<span class="meta">%s</span>' % meta)
    parts.append('<span class="t">%s</span>' % inline(title))
    if line:
        parts.append('<span class="line">%s</span>' % inline(line))
    attr = ' id="%s"' % esc(rid) if rid else ""
    return '<li%s><a href="%s">%s</a></li>' % (attr, href, "".join(parts))


def rows(items):
    return '<ul class="rows">%s</ul>' % "".join(items)


def date_meta(value):
    return '<time datetime="%s">%s</time>' % (esc(value), short_date(value))


def day_label(day):
    return "%s %s" % (short_date(day["date"]), weekday_ko(day["date"]))


def edition_label(day):
    """'10월 13일 화요일 브리핑'. 오전·오후판은 한 브리핑으로 합쳐 보여 주므로 판 이름은 화면에 쓰지 않는다 (2026-10-24)."""
    return "%s 브리핑" % day_label(day)


def list_line(item):
    """목록에서 제목 아래에 붙는 한 줄. 편집자가 쓴 teaser 가 있으면 그것, 없으면 본문의 첫 문장."""
    return item.get("teaser") or first_sentence(item["facts"])


def ordered_items(sec):
    """분야 안의 기사 순서: featured 표시가 있는 기사를 먼저, 나머지는 입력 순서대로."""
    return [i for i in sec["items"] if i.get("featured")] + [i for i in sec["items"] if not i.get("featured")]


def _part(cls, label, text, level):
    return '<div class="part %s"><h%d>%s</h%d><p>%s</p></div>' % (cls, level, inline(label), level, inline(text))


def article_body(item, level=2, before_sources=""):
    """기사 본문. 신문 기사처럼 사실 문단(facts)은 소제목 없이 맨 앞에 두고, 그다음 해설(so)과 전망(recheck)만
    작은 소제목을 단다. 입력의 recheck.label 은 쓰지 않는다 (소제목은 '전망'으로 통일).
    tip(노션 v3 의 생활·투자·커리어 체크포인트)이 있으면 전망 다음에 '알아둘 점'으로 둔다.
    level: 소제목의 단계. 기사 페이지는 2, 브리핑 안에서는 기사 제목(h3) 아래라 4."""
    parts = ['<div class="part fact"><p>%s</p></div>' % inline(item["facts"])]
    recheck = item.get("recheck")
    if item.get("so"):
        parts.append(_part("why", "해설", item["so"], level))
    if recheck and recheck.get("text"):
        parts.append(_part("recheck", "전망", recheck["text"], level))
    if item.get("tip"):
        parts.append(_part("tip", "알아둘 점", item["tip"], level))
    parts.append(before_sources)
    parts.append(render_sources(item.get("sources")))
    return "\n".join(p for p in parts if p)


def render_story(item, href, day, index, days_prefix, lead=False):
    """브리핑 안의 기사 한 건: 제목, 진행·근거 표시, 본문, 관련 기사, 출처. 누르지 않아도 다 읽을 수 있다.
    제목은 기사 페이지(공유·검색용 주소)로 가는 링크다. 대표 기사(lead_ref)는 제목을 더 크게 쓴다 (class lead).
    목록용 한 줄(teaser)은 본문과 겹쳐서 넣지 않는다."""
    return ('<article class="item%s" id="%s"><h3><a href="%s">%s</a></h3>%s%s</article>'
            % (" lead" if lead else "", esc(item["id"]), href, inline(item["title"]), item_meta(item, day),
               article_body(item, level=4, before_sources=render_follows(item, index, days_prefix))))


CARRY_DAYS = 7


def carried_sections(days):
    """홈 전용: 최신 브리핑에 새 기사가 없는 분야는 그 분야의 가장 최근 기사를 남겨 둔다 (2026-10-25 사용자 결정).
    최신 날짜 앞의 날을 최근 순으로 보며, 분야마다 기사가 있는 가장 가까운 하루의 기사(이전 판 기사 포함)를 가져온다.
    CARRY_DAYS 일보다 오래된 기사는 남기지 않는다. 반환: [(그 날, 분야 id, 분야 이름, [기사])], 원래 분야 순서."""
    latest = days[0]
    have = {s["id"] for s in visible_sections(latest)}
    have |= {s["id"] for g in latest.get("earlier") or [] for s in g["sections"] if s["items"]}
    taken = {it["id"] for _, it in all_items(latest)} | {it["id"] for _, _, it in earlier_items(latest)}
    oldest = parse_date(latest["date"]) - datetime.timedelta(days=CARRY_DAYS)
    out = []
    for day in days[1:]:
        if parse_date(day["date"]) < oldest:
            break
        found = {}
        for sec in visible_sections(day) + [s for g in day.get("earlier") or [] for s in g["sections"]]:
            if sec["id"] in have:
                continue
            for i in ordered_items(sec):
                if i["id"] not in taken:
                    found.setdefault(sec["id"], (sec["title"], []))[1].append(i)
        for sid, (title, items) in found.items():
            have.add(sid)
            taken.update(i["id"] for i in items)
            out.append((day, sid, title, items))
    return out


def render_sections(day, here, days_prefix, index, carried=()):
    """분야별 기사. 분야 이름(링크 아님) 아래에 기사마다 내용을 모두 펼쳐 둔다. 기사가 없는 분야는 내지 않는다.
    같은 날 이전 판에서 빠진 기사(earlier)도 따로 모으지 않고 같은 분야 안에 지금 판 기사 뒤로 전문을 싣는다
    (판 이름은 적지 않는다). 지금 판에 없는 분야는 지금 판 분야 뒤에 둔다.
    carried: 홈에서만, 새 기사가 없는 분야에 남겨 두는 앞선 날 기사 (carried_sections). 그날 기사 페이지로 링크한다.
    here: 이 페이지에서 그날 브리핑 폴더까지의 상대 경로. days_prefix: news/ 폴더까지의 상대 경로.
    입력의 lede(분야 요약)와 points 는 화면에 내지 않는다."""
    order, titles, stories = [], {}, {}
    def add(sec, html):
        if sec["id"] not in titles:
            order.append(sec["id"])
            titles[sec["id"]] = sec["title"]
            stories[sec["id"]] = []
        stories[sec["id"]].append(html)
    lead = day.get("lead_ref")
    for sec in visible_sections(day):
        for i in ordered_items(sec):
            add(sec, render_story(i, "%s%s/index.html" % (here, esc(i["id"])), day, index, days_prefix,
                                  lead=i["id"] == lead))
    for group in day.get("earlier") or []:
        for sec in group["sections"]:
            for i in ordered_items(sec):
                add(sec, render_story(i, "%s%s/index.html" % (here, esc(i["id"])), day, index, days_prefix))
    for prev, sid, title, items in carried:
        for i in items:
            add({"id": sid, "title": title},
                render_story(i, "%s%s/%s/index.html" % (days_prefix, prev["date"], esc(i["id"])), day, index,
                             days_prefix))
    return "\n".join('<section class="sec" id="%s" aria-labelledby="h-%s"><h2 id="h-%s">%s</h2>%s</section>'
                     % (esc(sid), esc(sid), esc(sid), inline(titles[sid]), "\n".join(stories[sid]))
                     for sid in order)


def render_side(day):
    """이번 판 부재 항목 (있을 때만, 넓은 화면에서는 오른쪽 보조 열). 다음 일정은 2026-10-24부터 싣지 않는다."""
    side = day.get("side") or {}
    blocks = []

    absent = side.get("absent") or []
    if absent:
        lis = ['<li><b>%s</b><span class="d">%s</span></li>' % (inline(a["name"]), inline(a.get("note", "")))
               for a in absent]
        blocks.append('<section class="absent" aria-labelledby="h-absent"><h2 id="h-absent">이번 판 부재 항목</h2>'
                      '<ul>%s</ul></section>' % "".join(lis))
    return "".join(blocks)


def render_corrections(day, days_prefix):
    """바로잡습니다(정정). 대상 기사(ref)가 있으면 그 기사로 가는 링크를 붙인다."""
    rows_ = day.get("corrections") or []
    if not rows_:
        return ""
    lis = []
    for c in rows_:
        link = ""
        if c.get("ref"):
            link = ' <a href="%s%s/%s/index.html">해당 기사</a>' % (days_prefix, day["date"], esc(c["ref"]))
        lis.append('<li><time datetime="%s">%s</time> %s%s</li>' % (esc(c["date"]), short_date(c["date"]),
                                                                   inline(c["text"]), link))
    return ('<section class="corrections" aria-labelledby="h-corrections">'
            '<h2 id="h-corrections">바로잡습니다</h2><ul>%s</ul></section>' % "".join(lis))


def render_pager(older, newer, days_prefix):
    """이전·다음 브리핑. days_prefix: 이 페이지에서 news/ 폴더까지의 상대 경로."""
    def link(d, cls, label):
        return ('<a class="%s" href="%s%s/index.html"><span>%s</span><b>%s %s</b></a>'
                % (cls, days_prefix, d["date"], label, short_date(d["date"]), inline(d["headline"])))
    parts = (link(older, "prev", "이전 브리핑") if older else "") + (link(newer, "next", "다음 브리핑") if newer else "")
    return '<nav class="pager" aria-label="이전·다음 브리핑">%s</nav>' % parts if parts else ""


def _clock(checked_at):
    """'16:59 KST' → '16:59'. 사이트의 시각은 모두 한국 시간이라 KST 를 따로 적지 않는다."""
    m = CHECKED_AT.match(checked_at or "")
    return "%s:%s" % (m.group(1), m.group(2)) if m else ""


def _stamps(day):
    """뉴스를 다시 확인한 시각과 고친 시각을 한 줄로. 둘 다 없으면 빈 문자열."""
    out = []
    if day.get("checked_at"):
        out.append("<span>%s 기준</span>" % _clock(day["checked_at"]))
    label = updated_label(day)
    if label:
        out.append("<span>%s</span>" % label)
    return '<p class="checked">%s</p>' % "".join(out) if out else ""


def render_hero(day):
    """브리핑 머리: 날짜·판과 기준·수정 시각 한 줄, 대표 제목, 짧은 의미."""
    stamps = []
    if day.get("checked_at"):
        stamps.append("<span>%s 기준</span>" % _clock(day["checked_at"]))
    label = updated_label(day)
    if label:
        stamps.append("<span>%s</span>" % label)
    return ('<header class="hero"><p class="dateline"><span>%s</span>%s</p><h1 id="top-title">%s</h1>'
            '<p class="summary">%s</p></header>'
            % (long_date(day["date"]), "".join(stamps), inline(day["headline"]), inline(day["summary"])))


def render_date_picker(days, current, days_prefix):
    """날짜 선택 칸: 고르면 그날 브리핑으로 간다. 날짜별 브리핑과 하루치 브리핑 맨 위에 둔다.
    고르는 순간 이동하도록 select 에 한 줄짜리 동작(onchange)만 붙인다. 별도 스크립트 파일은 없다.
    current: 지금 보고 있는 날짜(없으면 '날짜 선택'이 먼저 보인다). days_prefix: news/ 폴더까지의 상대 경로."""
    opts = [] if current else ['<option value="" selected disabled>날짜 선택</option>']
    for d in days:
        label = day_label(d)
        opts.append('<option value="%s%s/index.html"%s>%s</option>'
                    % (days_prefix, d["date"], " selected" if d["date"] == current else "", esc(label)))
    return ('<p class="pick"><label for="pick-date">날짜</label>'
            '<select id="pick-date" onchange="if(this.value)location.href=this.value">%s</select></p>'
            % "".join(opts))


def render_briefing(day, older, newer, *, here, days_prefix, index, picker="", carried=()):
    """브리핑 본문. 홈과 날짜 페이지가 함께 쓴다. 맨 위 시장 지표(render_ticker)는 이 앞에 따로 붙는다.
    문서 순서(좁은 화면에서 읽는 순서): 머리(날짜·판·기준 시각, 대표 제목, 짧은 의미) → 주요 뉴스
    → 분야별 기사(이전 판 기사도 같은 분야 안에) → 정정 → 이전·다음. (분야 바로가기는 2026-10-24에 없앴다.)
    picker: 하루치 브리핑 페이지의 날짜 선택 칸(홈에는 없다).
    이번 판 부재 항목이 있으면 넓은 화면(1080px 이상)에서 오른쪽 열로 간다."""
    rail = render_side(day)
    tail = render_corrections(day, days_prefix) + render_pager(older, newer, days_prefix)
    return ('<main class="page briefing%s" id="brief"><div class="lead">%s%s%s</div>%s<div class="body">%s</div>%s</main>'
            % (" has-rail" if rail else "", picker, render_hero(day), render_quick(day),
               '<aside class="rail" aria-label="이번 판 부재 항목">%s</aside>' % rail if rail else "",
               render_sections(day, here, days_prefix, index, carried),
               '<div class="tail">%s</div>' % tail if tail else ""))


def _title_page(title, body, lead=""):
    """제목 하나와 본문으로 된 목록·고정 페이지의 틀."""
    return ('<main class="page" id="brief"><div class="lead"><header class="hero">'
            '<h1 id="top-title">%s</h1>%s</header></div><div class="body">%s</div></main>' % (title, lead, body))


def render_footer(site, pages, prefix):
    """맨 아래: 사이트 이름, 고정 페이지(소개 등), RSS, 면책 문구. 상단 메뉴는 되풀이하지 않는다."""
    meta = "".join("<span>%s</span>" % inline(m) for m in site["footer_meta"])
    meta += "".join('<a href="%s%s/index.html">%s</a>' % (prefix, p["slug"], inline(p.get("nav") or p["title"]))
                    for p in pages)
    meta += '<a href="%sfeed.xml">RSS</a>' % prefix
    parts = ['<p class="meta">%s</p>' % meta]
    if site["disclaimer"]:
        parts.append("<p>%s</p>" % inline(site["disclaimer"]))
    return "\n".join(parts)


# ---------------------------------------------------------------- 구조화 데이터

def json_ld(obj):
    """검색 엔진용 구조화 데이터. < > & 를 \\u 표기로 바꿔 script 밖으로 나가지 못하게 한다."""
    text = json.dumps(dict({"@context": "https://schema.org"}, **obj), ensure_ascii=False)
    for ch, code in (("<", "\\u003c"), (">", "\\u003e"), ("&", "\\u0026")):
        text = text.replace(ch, code)
    return '<script type="application/ld+json">%s</script>' % text


def published_at(day):
    """게시 기준 시각. checked_at 이 있으면 그 시각, 없으면 날짜만 쓴다 (없는 시각을 만들지 않는다)."""
    return briefing_time(day).isoformat() if day.get("checked_at") else day["date"]


def site_org(site):
    return {"@type": "Organization", "name": site["title"], "url": site["base_url"] + "/"}


def absolute_path(page_path, href):
    """이 페이지의 대표 경로(/news/날짜/)와 상대 주소에서 대표 경로를 만든다. index.html 은 뗀다."""
    path = posixpath.normpath(posixpath.join(page_path, href))
    if path.endswith("index.html"):
        path = path[:-len("index.html")]
    return path if path.endswith("/") else path + "/"


def breadcrumb_ld(site, page_path, trail, page_name=""):
    """검색 엔진에 알리는 이 페이지까지의 경로 (화면에는 내지 않는다).
    trail: [(이름, 상대 주소 또는 None)]. 주소가 없는 항목은 이 페이지다. 링크로 끝나면 이 페이지를 마지막에 더한다."""
    steps = [(label, absolute_path(page_path, href) if href else page_path) for label, href in trail]
    if trail[-1][1] and page_name:
        steps.append((page_name, page_path))
    return {"@type": "BreadcrumbList", "itemListElement": [
        {"@type": "ListItem", "position": n, "name": plain(label), "item": site["base_url"] + path}
        for n, (label, path) in enumerate(steps, 1)]}


# ---------------------------------------------------------------- 페이지

def wrap_page(site, pages, *, title, description, prefix, path, content,
              og_type="website", noindex=False, current="", trail=None, ld=(), page_name="",
              published="", modified="", tagline=False):
    """prefix: 이 페이지에서 사이트 루트까지의 상대 경로. path: 대표 주소의 경로 부분.
    ld: 구조화 데이터 목록. trail 이 있으면 BreadcrumbList 를 덧붙인다 (page_name 은 그 마지막 이름).
    published, modified: 게시·수정 시각 (article:published_time, article:modified_time)."""
    url = site["base_url"] + path
    head = []
    if description:
        head.append('<meta name="description" content="%s">' % esc(description))
    if noindex:
        head.append('<meta name="robots" content="noindex">')
    else:
        head.append('<link rel="canonical" href="%s">' % esc(url))
        head.append('<meta property="og:type" content="%s">' % og_type)
        head.append('<meta property="og:site_name" content="%s">' % esc(site["title"]))
        head.append('<meta property="og:locale" content="ko_KR">')
        head.append('<meta property="og:title" content="%s">' % esc(title))
        if description:
            head.append('<meta property="og:description" content="%s">' % esc(description))
        head.append('<meta property="og:url" content="%s">' % esc(url))
        if site.get("og_image"):
            head.append('<meta property="og:image" content="%s/%s">' % (site["base_url"], esc(site["og_image"])))
            head.append('<meta name="twitter:card" content="summary_large_image">')
        if published:
            head.append('<meta property="article:published_time" content="%s">' % esc(published))
        if modified:
            head.append('<meta property="article:modified_time" content="%s">' % esc(modified))
        data = list(ld) + ([breadcrumb_ld(site, path, trail, page_name)] if trail else [])
        head.extend(json_ld(d) for d in data)
    head.append('<link rel="icon" href="%sassets/favicon.svg" type="image/svg+xml">' % prefix)
    head.append('<link rel="alternate" type="application/atom+xml" title="%s" href="%sfeed.xml">'
                % (esc(site["title"]), prefix))
    menu = [("home", HOME_LABEL, prefix + "index.html"),
            ("news", NEWS_LABEL, prefix + "news/index.html")]
    menu += [(p["slug"], p.get("nav") or p["title"], "%s%s/index.html" % (prefix, p["slug"])) for p in pages]

    def toplink(key, label, href):
        mark = ' aria-current="page"' if key == current else ""
        return '<a class="toplink" href="%s"%s>%s</a>' % (href, mark, inline(label))
    values = {
        "title": esc(title),
        "head": "\n".join(head),
        "css": prefix + "assets/style.css",
        "home": prefix + "index.html",
        "brand": inline(site["brand"]),
        "toplinks": "".join(toplink(*m) for m in menu),
        "content": content,
        "footer": render_footer(site, pages, prefix),
        "tagline": '<p class="tagline">%s</p>' % inline(site["tagline"]) if tagline and site.get("tagline") else "",
    }
    template = TEMPLATE.read_text(encoding="utf-8")
    return re.sub(r"\{\{(\w+)\}\}", lambda m: values[m.group(1)], template)


def render_home(days, site, pages, index):
    """홈: 제호 아래 한 줄 소개, 그다음 최신 브리핑 전체. 브리핑 페이지와 같은 구성이라 한 번 더 누를 필요가 없다."""
    latest = days[0]
    content = render_ticker(latest) + render_briefing(latest, days[1] if len(days) > 1 else None, None,
                                                      here="news/%s/" % latest["date"], days_prefix="news/",
                                                      index=index, carried=carried_sections(days))
    website = {"@type": "WebSite", "name": site["title"], "url": site["base_url"] + "/", "inLanguage": "ko-KR",
               "publisher": site_org(site)}
    if site.get("description"):
        website["description"] = site["description"]
    return wrap_page(site, pages, title=site["title"],
                     description=site.get("description") or plain(latest["summary"]),
                     prefix="", path="/", content=content, current="home", ld=[website], tagline=True)


def render_day(day, older, newer, site, pages, index, days):
    """하루치 브리핑. 기사 전문이 모두 펼쳐져 있고, 기사마다 공유용 기사 페이지가 따로 있다."""
    return wrap_page(
        site, pages,
        title="%s 브리핑 | %s" % (long_date(day["date"]), site["title"]),
        description=plain(day["summary"]), prefix="../../", path="/news/%s/" % day["date"],
        content=render_ticker(day) + render_briefing(day, older, newer, here="", days_prefix="../", index=index,
                                                     picker=render_date_picker(days, day["date"], "../")),
        og_type="article", current="news", published=published_at(day), modified=modified_value(day),
        trail=[(HOME_LABEL, "../../index.html"), (NEWS_LABEL, "../index.html"), (day_label(day), None)])


def render_article(day, sec, item, site, pages, index):
    """기사 하나. 맨 위에 분야 이름과 실린 브리핑(링크), 제목, 기준·수정 시각, 요약 한 줄(teaser 가 있을 때만),
    진행·근거 표시, 본문, 관련 기사, 출처, 바로잡습니다, 이 브리핑의 다른 기사, 브리핑으로 돌아가는 줄.
    검색·공유용 설명문도 teaser 가 있으면 그것을 쓴다."""
    root = "../../../"
    path = "/news/%s/%s/" % (day["date"], item["id"])
    top = ('<p class="kicker"><span>%s</span><a href="../index.html">%s</a></p>'
           % (inline(sec["title"]), edition_label(day)))
    others = [(s, i) for s in visible_sections(day) for i in ordered_items(s) if i is not item]
    others = ([(s, i) for s, i in others if s["id"] == sec["id"]] + [(s, i) for s, i in others if s["id"] != sec["id"]])
    others = others[:RELATED_MAX]
    others_html = ""
    if others:
        others_html = ('<div class="body"><section class="others" aria-labelledby="others-title">'
                       '<h2 id="others-title">이 브리핑의 다른 기사</h2>%s</section></div>'
                       % rows([row("../%s/index.html" % esc(i["id"]), i["title"], meta=inline(s["title"]))
                               for s, i in others]))
    deck = '<p class="deck">%s</p>' % inline(item["teaser"]) if item.get("teaser") else ""
    fixes = [c for c in day.get("corrections") or [] if c.get("ref") == item["id"]]
    fix_html = ""
    if fixes:
        fix_html = ('<section class="corrections" aria-labelledby="fix-title"><h2 id="fix-title">바로잡습니다</h2>'
                    '<ul>%s</ul></section>'
                    % "".join('<li><time datetime="%s">%s</time> %s</li>' % (esc(c["date"]), short_date(c["date"]),
                                                                          inline(c["text"])) for c in fixes))
    back = '<p class="back"><a href="../index.html">%s 전체 보기</a></p>' % edition_label(day)
    description = plain(item["teaser"]) if item.get("teaser") else first_sentence(item["facts"])
    content = ('<main class="page article" id="brief"><div class="lead">'
               '<article class="story" aria-labelledby="top-title"><header class="hero">%s<h1 id="top-title">%s</h1>%s%s%s'
               '</header>%s%s</article></div>%s<div class="tail">%s</div></main>'
               % (top, inline(item["title"]), _stamps(day), deck, item_meta(item, day),
                  article_body(item, before_sources=render_follows(item, index, "../../")), fix_html, others_html, back))
    url = site["base_url"] + path
    news = {"@type": "NewsArticle", "headline": plain(item["title"]), "description": description,
            "url": url, "mainEntityOfPage": url, "datePublished": published_at(day), "inLanguage": "ko-KR",
            "articleSection": plain(sec["title"]), "isAccessibleForFree": True,
            "author": site_org(site), "publisher": site_org(site)}
    if modified_value(day):
        news["dateModified"] = modified_value(day)
    sources = item.get("sources") or []
    if sources:
        news["citation"] = [{"@type": "CreativeWork", "name": plain(s["name"]), "url": s["url"]} for s in sources]
    return wrap_page(site, pages, title="%s | %s" % (plain(item["title"]), site["title"]),
                     description=description, prefix=root,
                     path=path, content=content, og_type="article", current="news",
                     trail=[(HOME_LABEL, root + "index.html"), (NEWS_LABEL, root + "news/index.html"),
                            (day_label(day), "../index.html")],
                     ld=[news], page_name=plain(item["title"]), published=published_at(day),
                     modified=modified_value(day))


def render_archive_day(day):
    """날짜별 브리핑 페이지의 하루 한 칸: 날짜·판, 대표 제목, 짧은 요약, 그날 실린 기사 줄(이전 판 기사 포함).
    날짜·판과 대표 제목은 그날 브리핑으로, 기사 줄은 기사 페이지로 간다. 기사 전문과 주요 뉴스는 다시 싣지 않는다."""
    d = day["date"]
    hid = "h-day-%s" % d
    lines = [row("%s/%s/index.html" % (d, esc(i["id"])), i["title"], meta=inline(s["title"]))
             for s in visible_sections(day) for i in ordered_items(s)]
    for _, s, i in earlier_items(day):
        lines.append(row("%s/%s/index.html" % (d, esc(i["id"])), i["title"],
                         meta=inline(s["title"])))
    return ('<section class="day" id="day-%s" aria-labelledby="%s">'
            '<p class="dateline">%s</p>'
            '<h2 id="%s"><a href="%s/index.html">%s</a></h2><p class="summary">%s</p>%s</section>'
            % (d, hid, edition_label(day), hid, d, inline(day["headline"]), inline(day["summary"]),
               rows(lines) if lines else ""))


def render_archive(days, site, pages):
    """날짜별 브리핑. 날마다의 브리핑(헤드라인, 요약, 기사 목록)을 최신순으로 한 페이지에 쌓는다."""
    body = "\n".join(render_archive_day(d) for d in days)
    return wrap_page(site, pages, title="%s | %s" % (NEWS_TITLE, site["title"]),
                     description="%s의 날짜별 브리핑. 날마다 대표 제목과 요약, 실린 기사를 최신순으로 모았다" % site["title"],
                     prefix="../", path="/news/", content=_title_page(NEWS_TITLE, body, render_date_picker(days, None, "")),
                     current="news",
                     trail=[(HOME_LABEL, "../index.html"), (NEWS_LABEL, None)])


def render_static(page, site, pages):
    blocks = []
    for sec in page["sections"]:
        part = ["<section class=\"doc\">", "<h2>%s</h2>" % inline(sec.get("heading", ""))]
        part += ["<p>%s</p>" % inline(p) for p in sec.get("paragraphs") or []]
        items = sec.get("items") or []
        if items:
            part.append('<ul class="points">%s</ul>' % "".join("<li>%s</li>" % inline(i) for i in items))
        part.append("</section>")
        blocks.append("\n".join(part))
    lead = '<p class="summary">%s</p>' % inline(page["lead"]) if page.get("lead") else ""
    return wrap_page(site, pages, title="%s | %s" % (page["title"], site["title"]),
                     description=page.get("description", ""), prefix="../", path="/%s/" % page["slug"],
                     content=_title_page(inline(page["title"]), "\n".join(blocks), lead), current=page["slug"],
                     trail=[(HOME_LABEL, "../index.html"), (page.get("nav") or page["title"], None)])


def render_withdrawn(day, w, site, pages):
    """철회한 기사의 주소에 남기는 안내 페이지. 기사 내용은 두지 않고 내린 사유와 그날 브리핑으로 가는 길만 둔다.
    검색에는 올리지 않는다 (noindex, sitemap 제외)."""
    content = ('<main class="page article" id="brief"><div class="lead"><header class="hero">'
               '<p class="kicker"><a href="../index.html">%s</a></p><h1 id="top-title">삭제된 기사입니다</h1>'
               '<p class="summary">%s</p></header></div><div class="tail"><p class="back">'
               '<a href="../index.html">%s 전체 보기</a></p></div></main>'
               % (edition_label(day), inline(w["reason"]), edition_label(day)))
    return wrap_page(site, pages, title="삭제된 기사 | %s" % site["title"], description="", prefix="../../../",
                     path="/news/%s/%s/" % (day["date"], w["id"]), content=content, noindex=True, current="news")


def render_404(site, pages):
    lead = ('<p class="summary">주소가 바뀌었거나 없는 페이지입니다.</p>'
            '<p class="more"><a href="/index.html">홈으로 가기</a></p>')
    return wrap_page(site, pages, title="페이지를 찾을 수 없습니다 | %s" % site["title"], description="",
                     prefix="/", path="/404.html", content=_title_page("페이지를 찾을 수 없습니다", "", lead),
                     noindex=True)


def render_sitemap(site, rels):
    urls = sorted(site["base_url"] + "/" + rel[:-len("index.html")] for rel in rels)
    body = "".join("<url><loc>%s</loc></url>\n" % esc(u) for u in urls)
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n%s</urlset>\n' % body)


def entry_updated(day):
    """피드의 갱신 시각: 정확한 수정 시각이 있으면 그것, 없으면 브리핑 기준 시각.
    수정 날짜만 알 때는 시각을 만들지 않는다 (요약에 '정확한 시각 미상'을 적는다, entry_summary)."""
    at, date_only = parse_updated(day)
    return at if at and not date_only else briefing_time(day)


def entry_summary(day):
    text = plain(day["summary"])
    at, date_only = parse_updated(day)
    if at and date_only:
        text += " (수정 %d월 %d일, 정확한 시각 미상)" % (at.month, at.day)
    return text


def render_feed(site, days):
    def entry(d):
        url = "%s/news/%s/" % (site["base_url"], d["date"])
        return ("<entry><title>%s</title><link href=\"%s\"/><id>%s</id><updated>%s</updated>"
                "<summary>%s</summary></entry>\n"
                % (esc(plain(d["headline"])), url, url, entry_updated(d).isoformat(), esc(entry_summary(d))))
    updated = max(entry_updated(d) for d in days[:30]).isoformat()
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<feed xmlns="http://www.w3.org/2005/Atom">\n'
            "<title>%s</title><link href=\"%s/\"/><link rel=\"self\" href=\"%s/feed.xml\"/>"
            "<id>%s/</id><updated>%s</updated><author><name>%s</name></author>\n%s</feed>\n"
            % (esc(site["title"]), site["base_url"], site["base_url"], site["base_url"], updated,
               esc(site["title"]), "".join(entry(d) for d in days[:30])))


# ---------------------------------------------------------------- 금지 단어 검사·쓰기

def load_blocklist(root, site):
    """공개 규칙(_site.json)과 로컬 전용 규칙(보관/blocklist.local.txt)을 합쳐 (규칙 이름, 정규식) 목록으로 낸다.

    규칙 이름은 '공개 규칙 3번', '로컬 규칙 5번째 줄'처럼 위치만 가리킨다. 규칙의 원문은 그 자체가
    비공개일 수 있으므로 오류 메시지와 로그 어디에도 넣지 않는다."""
    rules = [("공개 규칙 %d번" % n, p) for n, p in enumerate(site["blocklist"], 1)]
    # 문맥 규칙: 개인을 가리키는 말과 민감한 단어가 가까이 붙어 나오는 경우 (예: 본인 … 월세). 공개 가능한 일반 규칙이다.
    rules += [("문맥 규칙 %d번" % n, p) for n, p in enumerate(site["context_rules"], 1)]
    local = Path(root) / LOCAL_BLOCKLIST
    if local.is_symlink() and not local.exists():
        # 다른 곳의 규칙 파일을 가리키는데 그 파일이 없다. 규칙 없이 조용히 지나가면 안 된다.
        raise BuildError("로컬 금지 규칙 파일(%s)의 연결 대상이 없습니다. 연결을 고치거나 파일을 되살리세요" % LOCAL_BLOCKLIST)
    if local.is_file():
        for n, line in enumerate(local.read_text(encoding="utf-8").splitlines(), 1):
            line = line.strip()
            if line and not line.startswith("#"):
                rules.append(("로컬 규칙 %d번째 줄" % n, line))
    compiled = []
    for name, pattern in rules:
        try:
            rx = re.compile(pattern)
        except re.error:
            rx = None
        if rx is None:
            # re.error 의 설명(msg)과 속성에는 규칙의 일부가 들어 있다. 설명을 옮기지 않고, except 밖에서 올려
            # 원래 예외가 __context__ 로도 이어지지 않게 한다. 알리는 것은 규칙 번호와 고정된 말뿐이다.
            raise BuildError("blocklist %s: 정규식 문법 오류" % name)
        compiled.append((name, rx))
    return compiled


LAST_REVIEW = []  # 마지막 생성에서 주의 단어가 나온 위치 (main 이 알린다)


def check_review_terms(named, site):
    """주의 단어(_site.json review_terms: 월세·보증금처럼 공공 정책 기사에도 나오는 일반 단어)는 막지 않고
    위치만 돌려준다. 원문은 적지 않는다. 제작자는 그 위치가 개인 사정이 아니라 공개 기사 문맥인지 확인한다."""
    terms = []
    for n, pattern in enumerate(site["review_terms"], 1):
        try:
            terms.append((n, re.compile(pattern)))
        except re.error:
            terms.append((n, None))
        if terms[-1][1] is None:
            raise BuildError("_site.json review_terms %d번: 정규식 문법 오류" % n)
    notes = []
    for label, data, skip in named:
        for path, text in iter_strings(data, skip=skip):
            for n, rx in terms:
                if rx.search(text):
                    notes.append("%s: %s — 주의 단어 %d번" % (label, path, n))
    return notes


def _stop_on_matches(problems):
    if problems:
        raise BuildError("금지 규칙에 걸려 생성을 멈췄습니다 (아무것도 쓰지 않음):\n  "
                         + "\n  ".join(problems))


def check_sources_text(named, rules):
    """입력 JSON의 모든 문자열을 검사한다. 렌더링되지 않는 필드도 포함한다."""
    problems = []
    for label, data, skip in named:
        for path, text in iter_strings(data, skip=skip):
            for name, rx in rules:
                if rx.search(text):
                    problems.append("%s: %s — %s" % (label, path, name))
    _stop_on_matches(problems)


def check_pages(outputs, rules):
    """생성된 결과 전체(본문·속성·메타)를 검사한다."""
    problems = []
    for rel, page in outputs.items():
        text = html.unescape(page).replace(WORD_JOINER, "")
        for name, rx in rules:
            if rx.search(text):
                problems.append("%s: 생성 결과 — %s" % (rel, name))
    _stop_on_matches(problems)


def _only_generated(path):
    """이 프로그램이 만든 index.html 말고는 아무것도 없는 폴더인가."""
    return {p.name for p in path.iterdir()} <= {"index.html"}


def dropped_articles(out, days):
    """이미 만든 기사 페이지 가운데 내용(지금 판·이전 판 기사)에도 철회 목록에도 없는 것.
    게시한 주소가 소리 없이 사라지지 않도록, 이런 것이 있으면 생성을 멈춘다."""
    news = out / "news"
    if not news.is_dir():
        return []
    keep = {d["date"]: published_ids(d) | withdrawn_ids(d) for d in days}
    found = []
    for child in sorted(news.iterdir()):
        if not (child.is_dir() and DAY_DIR.match(child.name)):
            continue
        for sub in sorted(child.iterdir()):
            if sub.is_dir() and (sub / "index.html").is_file() and sub.name not in keep.get(child.name, set()):
                found.append("news/%s/%s/" % (child.name, sub.name))
    return found


def _only_generated_tree(path):
    """폴더 안(하위 폴더 포함)에 이 프로그램이 만든 index.html 말고는 아무것도 없는가."""
    return all(p.is_dir() or p.name == "index.html" for p in path.rglob("*"))


def remove_stale(out, days, outputs):
    """없앤 페이지의 폴더(RETIRED_DIRS)와 기사 페이지가 남지 않은 지난 날짜 폴더를 지운다.
    이 프로그램이 만든 파일만 지운다. 철회한 기사는 그 주소에 안내 페이지를 다시 쓰므로 지우지 않는다.
    게시한 기사가 그냥 빠진 경우는 그 전에 생성을 멈춘다 (dropped_articles)."""
    made = {rel.split("/", 1)[0] for rel in outputs}
    for name in RETIRED_DIRS:
        retired = out / name
        if name not in made and retired.is_dir() and _only_generated_tree(retired):
            shutil.rmtree(retired)
    dates = {d["date"] for d in days}
    for child in sorted((out / "news").iterdir()):
        if child.is_dir() and DAY_DIR.match(child.name) and child.name not in dates and _only_generated(child):
            shutil.rmtree(child)


def build(root, out=None, check_only=False):
    root = Path(root)
    out = Path(out) if out else root
    site = load_site(root)
    days = load_days(root, site)
    pages = load_pages(root)
    if not days:
        raise BuildError("content/ 에 YYYY-MM-DD.json 파일이 없습니다")
    rules = load_blocklist(root, site)
    named = [("_site.json", site, ("blocklist", "context_rules", "review_terms"))]
    named += [("%s.json" % d["date"], d, ()) for d in days]
    named += [("pages/%s.json" % p["slug"], p, ()) for p in pages]
    check_sources_text(named, rules)
    LAST_REVIEW[:] = check_review_terms(named, site)
    dropped = dropped_articles(out, days)
    if dropped:
        raise BuildError("게시한 기사 %d건의 주소가 내용에서 빠졌습니다: %s — 새 판에서 뺀 기사는 earlier(이전 판 기사)로 "
                         "옮기고, 내려야 하는 기사만 withdrawn 에 적으세요" % (len(dropped), ", ".join(dropped)))

    index = article_index(days)
    check_follows(days, index)
    outputs = {"index.html": render_home(days, site, pages, index),
               "news/index.html": render_archive(days, site, pages)}
    for i, day in enumerate(days):
        newer = days[i - 1] if i > 0 else None
        older = days[i + 1] if i + 1 < len(days) else None
        outputs["news/%s/index.html" % day["date"]] = render_day(day, older, newer, site, pages, index, days)
        for sec, item in all_items(day):
            outputs["news/%s/%s/index.html" % (day["date"], item["id"])] = render_article(day, sec, item, site, pages,
                                                                                        index)
        for group, sec, item in earlier_items(day):
            outputs["news/%s/%s/index.html" % (day["date"], item["id"])] = render_article(
                edition_view(day, group), sec, item, site, pages, index)
    for page in pages:
        outputs["%s/index.html" % page["slug"]] = render_static(page, site, pages)
    public = [rel for rel in outputs]
    for day in days:  # 철회 안내 페이지는 검색 목록(sitemap)에 넣지 않는다
        for w in day.get("withdrawn") or []:
            outputs["news/%s/%s/index.html" % (day["date"], w["id"])] = render_withdrawn(day, w, site, pages)
    outputs["404.html"] = render_404(site, pages)
    outputs["sitemap.xml"] = render_sitemap(site, public)
    outputs["robots.txt"] = "User-agent: *\nAllow: /\n\nSitemap: %s/sitemap.xml\n" % site["base_url"]
    outputs["feed.xml"] = render_feed(site, days)

    check_pages(outputs, rules)
    if check_only:
        return outputs

    for rel, text in outputs.items():
        path = out / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists() or path.read_text(encoding="utf-8") != text:
            path.write_text(text, encoding="utf-8")
    remove_stale(out, days, outputs)
    return outputs


def main(argv=None):
    ap = argparse.ArgumentParser(description="dogcowai 사이트 생성기")
    ap.add_argument("--check", action="store_true", help="검증만 하고 파일은 쓰지 않음")
    ap.add_argument("--out", help="결과를 쓸 폴더 (기본: 저장소 루트)")
    ap.add_argument("--root", default=str(ROOT_DIR), help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    try:
        outputs = build(Path(args.root), Path(args.out) if args.out else None, check_only=args.check)
    except BuildError as e:
        print("생성 실패: %s" % e, file=sys.stderr)
        return 1
    print("%s: 파일 %d개" % ("검증 완료" if args.check else "생성 완료", len(outputs)))
    if LAST_REVIEW:
        print("주의 단어 위치 %d곳 (공개 기사 문맥인지 확인하고, 개인 사정이면 그 문장을 입력에서 지우세요):" % len(LAST_REVIEW))
        for note in LAST_REVIEW:
            print("  " + note)
    for rel in sorted(outputs):
        print("  " + rel)
    return 0


if __name__ == "__main__":
    sys.exit(main())
