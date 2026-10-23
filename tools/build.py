#!/usr/bin/env python3
"""dogcowai 사이트 생성기.

content/YYYY-MM-DD.json (날짜별 내용), content/_site.json (공통 설정), content/pages/*.json (고정 페이지)에서
아래 페이지와 404, sitemap.xml, robots.txt, feed.xml 을 만든다.

    /                       홈: 짧은 소개, 최신 브리핑과 그 기사 전부
    /news/                  날짜별 브리핑: 날마다 헤드라인·요약·핵심 1분·기사 목록을 최신순으로 쌓는다
    /news/날짜/              하루치 브리핑: 헤드라인, 핵심 1분, 분야별 기사, 이전 판에 실린 기사
    /news/날짜/기사id/        기사 하나
    /이름/                   고정 페이지 (content/pages/*.json 이 있을 때만. 지금은 없다)

    python3 tools/build.py            # 저장소 루트에 생성
    python3 tools/build.py --check    # 검증만 하고 파일은 쓰지 않음
    python3 tools/build.py --out DIR  # 다른 폴더에 생성

규칙 (자세한 형식은 docs/content-format.md)
- 공개 입력에는 개인 전용 내용을 둘 수 없다: 'actions', 'side.career', 'private' 표시는 오류.
- 기사에는 고유 id 와 출처가 있어야 한다. 핵심 1분은 기사에 연결되고 숫자가 기사와 맞아야 한다.
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
RETIRED_DIRS = ("topic", "about")  # 없앤 페이지(분야별·소개, 2026-10-24). 이번 생성에 없으면 생성 파일만 지운다
RESERVED_IDS = {"brief", "top-title", "market-title", "quick-title", "recent-title", "topics-title",
                "today-title", "intro-title", "related-title", "others-title", "h-sched", "h-absent",
                "h-corrections"}

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
    return re.split(r"(?<=\.)\s+", plain(text).strip(), 1)[0]


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
    return site


def _need(obj, key, where):
    if not isinstance(obj, dict) or obj.get(key) in (None, ""):
        raise BuildError("%s: 필수 항목 '%s' 가 없습니다" % (where, key))


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
    sources = item.get("sources") or []
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

    for n, q in enumerate(day.get("quick") or []):
        where = "%s quick[%d]" % (filename, n)
        if not isinstance(q, dict) or not q.get("text") or not q.get("ref"):
            raise BuildError("%s: 핵심 1분 항목은 {\"text\", \"ref\"} 형태로 기사에 연결해야 합니다" % where)
        item = items_by_id.get(q["ref"])
        if item is None:
            raise BuildError("%s: ref 가 가리키는 기사가 없습니다" % where)
        body = plain(" ".join(str(item.get(k, "")) for k in ("title", "facts", "so")))
        for num in _NUMBER.findall(plain(q["text"])):
            if num not in body:
                raise BuildError("%s: 숫자 '%s' 가 연결된 기사 본문에 없습니다" % (where, num))

    market = day.get("market") or {}
    rows = list(market.get("rows") or []) + list((market.get("forecast") or {}).get("rows") or [])
    for n, row in enumerate(rows):
        where = "%s market.rows[%d]" % (filename, n)
        _need(row, "name", where)
        if row.get("asof"):
            asof = _check_date(row["asof"], where)
            if asof > date:
                raise BuildError("%s: 기준일이 브리핑 날짜보다 뒤입니다" % where)
            if (date - asof).days > site["market_max_age_days"]:
                raise BuildError("%s: 기준일이 너무 오래됐습니다 (%d일 초과)" % (where, site["market_max_age_days"]))
    _check_sources(market.get("sources") or [], "%s market.sources" % filename)

    for n, s in enumerate((day.get("side") or {}).get("schedule") or []):
        where = "%s side.schedule[%d]" % (filename, n)
        _need(s, "at", where)
        _need(s, "title", where)
        try:
            at = datetime.datetime.fromisoformat(s["at"])
        except (ValueError, TypeError):
            at = None
        if at is None or at.tzinfo is None:
            raise BuildError("%s: at 은 시간대가 붙은 ISO 형식이어야 합니다 (예: 2026-10-14T21:30:00+09:00)" % where)
        if at <= now:
            raise BuildError("%s: 브리핑 기준 시각보다 이른 일정은 '다음 일정'에 둘 수 없습니다" % where)

    for n, c in enumerate(day.get("corrections") or []):
        where = "%s corrections[%d]" % (filename, n)
        _need(c, "date", where)
        _need(c, "text", where)
        _check_date(c["date"], where)


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
# 페이지는 여섯 가지다. 같은 정보는 한 곳에만 둔다.
#   홈        짧은 소개 + 최신 브리핑 (브리핑 페이지와 같은 구성)
#   브리핑    날짜·판, 헤드라인, 요약, 확인·수정 시각, 핵심 1분, 분야별 기사 목록, 시장, 다음 일정, 정정, 이전·다음
#   기사      분야·실린 브리핑, 제목, 확인·수정 시각, 본문, 출처, 이 브리핑의 다른 기사
#   날짜별    월별 날짜 목록 (날짜·판, 헤드라인)
#   분야별    분야 목록 (이름, 기사 수) / 분야 하나의 기사 목록 (날짜, 제목, 한 줄)
#   고정      소개 등
# 누를 수 있는 것은 모두 같은 모양의 줄(rows)이나 링크 글자다. 펼치는 버튼과 스크립트는 없다.

def render_sources(sources):
    if not sources:
        return ""
    links = " ".join('<a href="%s" target="_blank" rel="noopener">%s</a>'
                     % (esc(s["url"]), inline(s["name"])) for s in sources)
    return '<p class="src"><span>출처<span class="sr"> (새 창에서 열림)</span></span> %s</p>' % links


_PERCENT = re.compile(r"(\d+(?:\.\d+)?)\s*%")


def market_groups(rows_):
    """연속한 시장 수치를 기준(basis)이 같은 것끼리 묶는다. [(기준, [행...])]"""
    groups = []
    for r in rows_:
        basis = r.get("basis") or ""
        if groups and groups[-1][0] == basis:
            groups[-1][1].append(r)
        else:
            groups.append((basis, [r]))
    return groups


def _move(r):
    """등락률(%)과 방향이 모두 있으면 그 크기, 아니면 None."""
    m = _PERCENT.search(r.get("change") or "")
    if m and r.get("dir") in ("up", "down"):
        return float(m.group(1))
    return None


def _market_row(r, top=0.0, sub_keys=("value", "note")):
    """시장 수치 한 줄: 이름과 값 / 막대 / 등락. top 이 있으면 막대를 그린다 (top 이 칸의 절반)."""
    sub = "".join(('<span class="v">%s</span>' % inline(r[k])) if k == "value" else
                  '<span class="x">%s</span>' % inline(r[k]) for k in sub_keys if r.get(k))
    name = '<span class="n"><b>%s</b>%s</span>' % (inline(r["name"]), "<small>%s</small>" % sub if sub else "")
    track = ""
    pct = _move(r)
    if top and pct is not None:
        track = ('<span class="track" aria-hidden="true"><span class="bar %s" style="width:%.1f%%"></span></span>'
                 % (r["dir"], pct / top * 50))
    change = ""
    if r.get("change"):
        cls = " " + r["dir"] if r.get("dir") in ("up", "down") else ""
        change = '<span class="c%s">%s</span>' % (cls, inline(r["change"]))
    return "<li>%s%s%s</li>" % (name, track, change)


def render_market(day):
    """시장 수치는 이 한 곳에만 둔다. 기준(10/13 뉴욕 종가 등)이 같은 줄끼리 묶고 기준을 먼저 적는다.
    등락률이 있는 수치가 둘 이상이면 막대를 함께 그린다. 막대 길이는 이 판에서 가장 크게 움직인 수치가 칸의
    절반을 채우도록 맞춘다. 숫자는 글자로도 있으므로 막대는 화면 낭독기에 읽히지 않게 한다."""
    market = day.get("market")
    if not market:
        return ""
    rows_ = market.get("rows") or []
    moves = [p for p in (_move(r) for r in rows_) if p is not None]
    top = max(moves) if len(moves) >= 2 and max(moves) > 0 else 0.0
    parts = ['<section class="market" aria-labelledby="market-title">', '<h2 id="market-title">시장</h2>']
    for basis, group in market_groups(rows_):
        head = '<h3>%s</h3>' % inline(basis) if basis else ""
        parts.append('<div class="mgroup">%s<ul class="mrows%s">%s</ul></div>'
                     % (head, " bars" if top else "", "".join(_market_row(r, top) for r in group)))
    forecast = market.get("forecast") or {}
    frows = forecast.get("rows") or []
    if frows:
        head = '<h3>%s</h3>' % inline(forecast["title"]) if forecast.get("title") else ""
        parts.append('<div class="mgroup">%s<ul class="mrows">%s</ul></div>'
                     % (head, "".join(_market_row(r, 0.0, ("value", "basis", "note")) for r in frows)))
    if market.get("note"):
        parts.append('<p class="note">%s</p>' % inline(market["note"]))
    parts.append(render_sources(market.get("sources")))
    parts.append("</section>")
    return "\n".join(p for p in parts if p)


def render_quick(day):
    items = day.get("quick") or []
    if not items:
        return ""
    lis = "".join("<li>%s</li>" % inline(q["text"]) for q in items)
    return ('<section class="quick" aria-labelledby="quick-title">'
            '<h2 id="quick-title">핵심 1분</h2><ul>%s</ul></section>' % lis)


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
    """'10월 13일 화요일 오후판'. 판이 없으면 '10월 13일 화요일 브리핑'."""
    return "%s %s" % (day_label(day), inline(day["edition"]) if day.get("edition") else "브리핑")


def list_line(item):
    """목록에서 제목 아래에 붙는 한 줄. 편집자가 쓴 teaser 가 있으면 그것, 없으면 본문의 첫 문장."""
    return item.get("teaser") or first_sentence(item["facts"])


def ordered_items(sec):
    """분야 안의 기사 순서: featured 표시가 있는 기사를 먼저, 나머지는 입력 순서대로."""
    return [i for i in sec["items"] if i.get("featured")] + [i for i in sec["items"] if not i.get("featured")]


def _part(cls, label, text, level):
    return '<div class="part %s"><h%d>%s</h%d><p>%s</p></div>' % (cls, level, inline(label), level, inline(text))


def article_body(item, level=2):
    """기사 본문. 사실만 있는 기사는 소제목 없이 문단만 둔다.
    level: 소제목의 단계. 기사 페이지는 2, 브리핑 안에서는 기사 제목(h3) 아래라 4."""
    parts = []
    recheck = item.get("recheck")
    has_recheck = bool(recheck and recheck.get("text"))
    if item.get("so") or has_recheck:
        parts.append(_part("fact", "무슨 일이 있었나", item["facts"], level))
    else:
        parts.append('<div class="part fact"><p>%s</p></div>' % inline(item["facts"]))
    if item.get("so"):
        parts.append(_part("why", "왜 중요한가", item["so"], level))
    if has_recheck:
        parts.append(_part("recheck", recheck.get("label", "다시 볼 조건"), recheck["text"], level))
    parts.append(render_sources(item.get("sources")))
    return "\n".join(p for p in parts if p)


def render_story(item, href):
    """브리핑 안의 기사 한 건: 제목과 본문·출처 전부. 누르지 않아도 다 읽을 수 있다.
    제목은 기사 페이지(공유·검색용 주소)로 가는 링크다. 목록용 한 줄(teaser)은 본문과 겹쳐서 넣지 않는다."""
    return ('<article class="item" id="%s"><h3><a href="%s">%s</a></h3>%s</article>'
            % (esc(item["id"]), href, inline(item["title"]), article_body(item, level=4)))


def render_sections(day, here):
    """분야별 기사. 분야 이름(링크 아님) 아래에 기사마다 내용을 모두 펼쳐 둔다.
    here: 이 페이지에서 그날 브리핑 폴더까지의 상대 경로.
    입력의 lede(분야 요약)와 points 는 화면에 내지 않는다."""
    out = []
    for sec in visible_sections(day):
        sid = esc(sec["id"])
        stories = "\n".join(render_story(i, "%s%s/index.html" % (here, esc(i["id"]))) for i in ordered_items(sec))
        out.append('<section class="sec" id="%s" aria-labelledby="h-%s">'
                   '<h2 id="h-%s">%s</h2>%s</section>'
                   % (sid, sid, sid, inline(sec["title"]), stories))
    return "\n".join(out)


def render_earlier(day, here):
    """이전 판에만 실린 기사. 새 판에서 빠져도 주소는 남아 있으므로, 브리핑에서 찾을 수 있게 판마다 제목 줄만 둔다."""
    out = []
    for n, group in enumerate(day.get("earlier") or [], 1):
        lines = [row("%s%s/index.html" % (here, esc(i["id"])), i["title"], meta=inline(s["title"]), rid=i["id"])
                 for s in group["sections"] for i in ordered_items(s)]
        out.append('<section class="sec earlier" aria-labelledby="h-earlier-%d">'
                   '<h2 id="h-earlier-%d">%s에 실린 기사</h2>%s</section>'
                   % (n, n, inline(group["edition"]), rows(lines)))
    return "\n".join(out)


def render_side(day):
    """다음 일정과 이번 판 부재 항목."""
    side = day.get("side") or {}
    blocks = []

    sched = side.get("schedule") or []
    if sched:
        lis = []
        for s in sched:
            at = datetime.datetime.fromisoformat(s["at"]).astimezone(KST)
            desc = ' <span class="d">%s</span>' % inline(s["desc"]) if s.get("desc") else ""
            lis.append('<li><time class="when" datetime="%s">%d/%d %02d:%02d</time><span><b>%s</b>%s</span></li>'
                       % (esc(s["at"]), at.month, at.day, at.hour, at.minute, inline(s["title"]), desc))
        asof = "%s %s 기준, 한국 시간" % (short_date(day["date"]), day.get("checked_at") or "0시")
        blocks.append('<section class="sched" aria-labelledby="h-sched"><h2 id="h-sched">다음 일정</h2>'
                      '<p class="asof">%s</p><ul>%s</ul></section>' % (inline(asof), "".join(lis)))

    absent = side.get("absent") or []
    if absent:
        lis = ['<li><b>%s</b><span class="d">%s</span></li>' % (inline(a["name"]), inline(a.get("note", "")))
               for a in absent]
        blocks.append('<section class="absent" aria-labelledby="h-absent"><h2 id="h-absent">이번 판 부재 항목</h2>'
                      '<ul>%s</ul></section>' % "".join(lis))
    return "".join(blocks)


def render_corrections(day):
    rows_ = day.get("corrections") or []
    if not rows_:
        return ""
    lis = "".join('<li><time datetime="%s">%s</time> %s</li>' % (esc(c["date"]), short_date(c["date"]), inline(c["text"]))
                  for c in rows_)
    return ('<section class="corrections" aria-labelledby="h-corrections">'
            '<h2 id="h-corrections">정정·수정 내역</h2><ul>%s</ul></section>' % lis)


def render_pager(older, newer, days_prefix):
    """이전·다음 브리핑. days_prefix: 이 페이지에서 news/ 폴더까지의 상대 경로."""
    def link(d, cls, label):
        return ('<a class="%s" href="%s%s/index.html"><span>%s</span><b>%s %s</b></a>'
                % (cls, days_prefix, d["date"], label, short_date(d["date"]), inline(d["headline"])))
    parts = (link(older, "prev", "이전 브리핑") if older else "") + (link(newer, "next", "다음 브리핑") if newer else "")
    return '<nav class="pager" aria-label="이전·다음 브리핑">%s</nav>' % parts if parts else ""


def _stamps(day):
    """뉴스를 다시 확인한 시각과 고친 시각을 한 줄로. 둘 다 없으면 빈 문자열."""
    out = []
    if day.get("checked_at"):
        out.append("<span>뉴스 재확인 %s</span>" % inline(day["checked_at"]))
    label = updated_label(day)
    if label:
        out.append("<span>%s</span>" % label)
    return '<p class="checked">%s</p>' % "".join(out) if out else ""


def render_briefing(day, older, newer, *, here, days_prefix, intro=""):
    """브리핑 본문. 홈(intro 가 붙는다)과 날짜 페이지가 함께 쓴다.
    좁은 화면에서 읽는 순서: 소개, 날짜·헤드라인·요약, 핵심 1분, 분야별 기사, 이전 판에 실린 기사(있을 때),
    시장·다음 일정, 정정, 이전·다음. 넓은 화면에서는 시장·다음 일정이 오른쪽 칸(rail)으로 간다."""
    edition = " " + inline(day["edition"]) if day.get("edition") else ""
    head = ('<header class="hero"><p class="dateline">%s%s</p><h1 id="top-title">%s</h1>'
            '<p class="summary">%s</p>%s</header>'
            % (long_date(day["date"]), edition, inline(day["headline"]), inline(day["summary"]), _stamps(day)))
    rail = render_market(day) + render_side(day)
    tail = render_corrections(day) + render_pager(older, newer, days_prefix)
    body = "\n".join(p for p in (render_sections(day, here), render_earlier(day, here)) if p)
    return ('<main class="page briefing%s" id="brief"><div class="lead">%s%s%s</div><div class="body">%s</div>%s%s</main>'
            % (" has-rail" if rail else "", intro, head, render_quick(day), body,
               '<aside class="rail" aria-label="시장과 일정">%s</aside>' % rail if rail else "",
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
              published="", modified=""):
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
    }
    template = TEMPLATE.read_text(encoding="utf-8")
    return re.sub(r"\{\{(\w+)\}\}", lambda m: values[m.group(1)], template)


def render_intro(site, about_href):
    """홈 맨 위의 짧은 소개: 한 줄 제목, 한 단락, 소개 페이지 링크."""
    intro = site.get("intro")
    if not intro:
        return ""
    parts = ['<section class="intro" aria-label="사이트 소개">',
             '<p class="intro-title">%s</p>' % inline(intro["heading"])]
    parts += ["<p>%s</p>" % inline(p) for p in intro.get("paragraphs") or []]
    if about_href:
        parts.append('<p class="more"><a href="%s">편집 기준</a></p>' % about_href)
    parts.append("</section>")
    return "".join(parts)


def render_home(days, site, pages):
    """홈: 짧은 소개 다음에 최신 브리핑 전체. 브리핑 페이지와 같은 구성이라 한 번 더 누를 필요가 없다."""
    latest = days[0]
    about = next((p for p in pages if p["slug"] == "about"), None)
    content = render_briefing(latest, days[1] if len(days) > 1 else None, None,
                              here="news/%s/" % latest["date"], days_prefix="news/",
                              intro=render_intro(site, "about/index.html" if about else ""))
    website = {"@type": "WebSite", "name": site["title"], "url": site["base_url"] + "/", "inLanguage": "ko-KR",
               "publisher": site_org(site)}
    if site.get("description"):
        website["description"] = site["description"]
    return wrap_page(site, pages, title=site["title"],
                     description=site.get("description") or plain(latest["summary"]),
                     prefix="", path="/", content=content, current="home", ld=[website])


def render_day(day, older, newer, site, pages):
    """하루치 브리핑. 기사 본문은 기사 페이지에 있다."""
    edition = " " + plain(day["edition"]) if day.get("edition") else ""
    return wrap_page(
        site, pages,
        title="%s%s | %s" % (long_date(day["date"]), edition, site["title"]),
        description=plain(day["summary"]), prefix="../../", path="/news/%s/" % day["date"],
        content=render_briefing(day, older, newer, here="", days_prefix="../"),
        og_type="article", current="news", published=published_at(day), modified=modified_value(day),
        trail=[(HOME_LABEL, "../../index.html"), (NEWS_LABEL, "../index.html"), (day_label(day), None)])


def render_article(day, sec, item, site, pages):
    """기사 하나. 맨 위에 분야 이름과 실린 브리핑(링크), 제목, 확인·수정 시각, 요약 한 줄(teaser 가 있을 때만),
    본문, 출처, 끝에 이 브리핑의 다른 기사. 검색·공유용 설명문도 teaser 가 있으면 그것을 쓴다."""
    root = "../../../"
    path = "/news/%s/%s/" % (day["date"], item["id"])
    top = ('<p class="kicker"><span>%s</span><a href="../index.html">%s</a></p>'
           % (inline(sec["title"]), edition_label(day)))
    others = [(s, i) for s in visible_sections(day) for i in ordered_items(s) if i is not item]
    others_html = ""
    if others:
        others_html = ('<div class="body"><section class="others" aria-labelledby="others-title">'
                       '<h2 id="others-title">이 브리핑의 다른 기사</h2>%s</section></div>'
                       % rows([row("../%s/index.html" % esc(i["id"]), i["title"], meta=inline(s["title"]))
                               for s, i in others]))
    deck = '<p class="deck">%s</p>' % inline(item["teaser"]) if item.get("teaser") else ""
    description = plain(item["teaser"]) if item.get("teaser") else first_sentence(item["facts"])
    content = ('<main class="page article" id="brief"><div class="lead">'
               '<article class="story" aria-labelledby="top-title"><header class="hero">%s<h1 id="top-title">%s</h1>%s%s'
               '</header>%s</article></div>%s</main>'
               % (top, inline(item["title"]), _stamps(day), deck, article_body(item), others_html))
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
    """날짜별 브리핑 페이지의 하루 한 칸: 날짜·판, 헤드라인, 요약, 핵심 1분, 그날 실린 기사 목록(이전 판 기사 포함).
    날짜·판과 헤드라인은 그날 브리핑으로, 기사 줄은 기사 페이지로 간다."""
    d = day["date"]
    hid = "h-day-%s" % d
    quick = day.get("quick") or []
    quick_html = ('<div class="quick"><h3>핵심 1분</h3><ul>%s</ul></div>'
                  % "".join("<li>%s</li>" % inline(q["text"]) for q in quick)) if quick else ""
    lines = [row("%s/%s/index.html" % (d, esc(i["id"])), i["title"], meta=inline(s["title"]), line=list_line(i))
             for s in visible_sections(day) for i in ordered_items(s)]
    for group, s, i in earlier_items(day):
        lines.append(row("%s/%s/index.html" % (d, esc(i["id"])), i["title"],
                         meta="%s · %s" % (inline(s["title"]), inline(group["edition"])), line=list_line(i)))
    return ('<section class="day" id="day-%s" aria-labelledby="%s">'
            '<p class="dateline"><a href="%s/index.html">%s</a></p>'
            '<h2 id="%s"><a href="%s/index.html">%s</a></h2><p class="summary">%s</p>%s%s</section>'
            % (d, hid, d, edition_label(day), hid, d, inline(day["headline"]), inline(day["summary"]),
               quick_html, rows(lines) if lines else ""))


def render_archive(days, site, pages):
    """날짜별 브리핑. 날마다의 브리핑(헤드라인, 요약, 핵심 1분, 기사 목록)을 최신순으로 한 페이지에 쌓는다."""
    body = "\n".join(render_archive_day(d) for d in days)
    return wrap_page(site, pages, title="%s | %s" % (NEWS_TITLE, site["title"]),
                     description="%s의 날짜별 브리핑. 날마다 헤드라인, 핵심 1분, 실린 기사를 최신순으로 모았다" % site["title"],
                     prefix="../", path="/news/", content=_title_page(NEWS_TITLE, body), current="news",
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
    """없앤 페이지의 폴더(RETIRED_DIRS), 철회한 기사의 페이지, 기사 페이지가 남지 않은 지난 날짜 폴더를 지운다.
    이 프로그램이 만든 파일만 지운다. 게시한 기사가 그냥 빠진 경우는 그 전에 생성을 멈춘다 (dropped_articles)."""
    made = {rel.split("/", 1)[0] for rel in outputs}
    for name in RETIRED_DIRS:
        retired = out / name
        if name not in made and retired.is_dir() and _only_generated_tree(retired):
            shutil.rmtree(retired)

    gone = {d["date"]: withdrawn_ids(d) for d in days}
    for child in sorted((out / "news").iterdir()):
        if not (child.is_dir() and DAY_DIR.match(child.name)):
            continue
        for wid in sorted(gone.get(child.name, ())):
            sub = child / wid
            if (sub / "index.html").is_file():
                (sub / "index.html").unlink()
            if sub.is_dir() and not any(sub.iterdir()):
                sub.rmdir()
        if child.name not in gone and _only_generated(child):
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
    named = [("_site.json", site, ("blocklist",))]
    named += [("%s.json" % d["date"], d, ()) for d in days]
    named += [("pages/%s.json" % p["slug"], p, ()) for p in pages]
    check_sources_text(named, rules)
    dropped = dropped_articles(out, days)
    if dropped:
        raise BuildError("게시한 기사 %d건의 주소가 내용에서 빠졌습니다: %s — 새 판에서 뺀 기사는 earlier(이전 판 기사)로 "
                         "옮기고, 내려야 하는 기사만 withdrawn 에 적으세요" % (len(dropped), ", ".join(dropped)))

    outputs = {"index.html": render_home(days, site, pages),
               "news/index.html": render_archive(days, site, pages)}
    for i, day in enumerate(days):
        newer = days[i - 1] if i > 0 else None
        older = days[i + 1] if i + 1 < len(days) else None
        outputs["news/%s/index.html" % day["date"]] = render_day(day, older, newer, site, pages)
        for sec, item in all_items(day):
            outputs["news/%s/%s/index.html" % (day["date"], item["id"])] = render_article(day, sec, item, site, pages)
        for group, sec, item in earlier_items(day):
            outputs["news/%s/%s/index.html" % (day["date"], item["id"])] = render_article(
                edition_view(day, group), sec, item, site, pages)
    for page in pages:
        outputs["%s/index.html" % page["slug"]] = render_static(page, site, pages)
    public = [rel for rel in outputs]
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
    for rel in sorted(outputs):
        print("  " + rel)
    return 0


if __name__ == "__main__":
    sys.exit(main())
