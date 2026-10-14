#!/usr/bin/env python3
"""dogcowai 사이트 생성기.

content/YYYY-MM-DD.json (날짜별 내용), content/_site.json (공통 설정), content/pages/*.json (고정 페이지)에서
아래 페이지와 404, sitemap.xml, robots.txt, feed.xml 을 만든다.

    /                       홈: 소개, 오늘의 주요 뉴스
    /news/                  날짜별 브리핑 목록
    /news/날짜/              하루치 브리핑: 헤드라인, 핵심 1분, 분야별 기사 목록
    /news/날짜/기사id/        기사 하나
    /topic/, /topic/분야id/   분야 목록, 분야별 기사 목록
    /소개 등                 고정 페이지

    python3 tools/build.py            # 저장소 루트에 생성
    python3 tools/build.py --check    # 검증만 하고 파일은 쓰지 않음
    python3 tools/build.py --out DIR  # 다른 폴더에 생성

규칙 (자세한 형식은 docs/content-format.md)
- 공개 입력에는 개인 전용 내용을 둘 수 없다: 'actions', 'side.career', 'private' 표시는 오류.
- 기사에는 고유 id 와 출처가 있어야 한다. 핵심 1분은 기사에 연결되고 숫자가 기사와 맞아야 한다.
- 날짜·기준 시각·일정·시장 수치의 기준일을 검사한다.
- 금지 패턴은 입력 JSON 전체와 생성 결과 전체에서 검사한다. 오류에는 파일·필드·규칙만 적는다.
- 검증에 실패하면 아무 파일도 쓰지 않는다.

표준 라이브러리만 쓴다 (Python 3.9 이상).
"""
import argparse
import datetime
import html
import json
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
HOME_RECENT_LIMIT = 10
HOME_FEATURED_LIMIT = 5
HOME_LABEL = "홈"          # 메뉴와 위치 줄에 쓰는 이름
NEWS_LABEL = "날짜별"
TOPIC_LABEL = "분야별"
NEWS_TITLE = "날짜별 브리핑"  # 그 페이지의 제목
TOPIC_TITLE = "분야별 기사"
RESERVED_IDS = {"brief", "top-title", "market-title", "quick-title", "recent-title", "topics-title",
                "today-title", "intro-title", "related-title", "h-sched", "h-absent", "h-corrections"}

_BOLD = re.compile(r"\*\*(.+?)\*\*")
_DIRECTION = re.compile(r"\{\{(up|down):(.+?)\}\}")
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?%?")

# 접힌 줄을 가리키는 주소(#기사id)로 들어오면 그 펼침을 연다.
OPEN_SCRIPT = """<script>
(function(){function show(){var id=decodeURIComponent(location.hash.slice(1));if(!id)return;
var el=document.getElementById(id);if(!el)return;var opened=false;
for(var p=el.parentElement;p;p=p.parentElement){if(p.tagName==='DETAILS'&&!p.open){p.open=true;opened=true}}
if(opened)el.scrollIntoView()}
window.addEventListener('hashchange',show);show()})();
</script>"""


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


def visible_sections(day):
    return [s for s in day["sections"] if s["items"]]


def all_items(day):
    return [(s, it) for s in visible_sections(day) for it in s["items"]]


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
            where = "%s %s.items[%d]" % (filename, sid, n)
            for key in ("id", "date", "title", "facts"):
                _need(item, key, where)
            iid = item["id"]
            where = "%s (%s)" % (where, iid)
            if not SLUG.match(iid):
                raise BuildError("%s: 기사 id 는 영문 소문자·숫자·하이픈만 쓸 수 있습니다" % where)
            if iid in ids:
                raise BuildError("%s: id '%s' 가 중복됩니다" % (where, iid))
            ids.add(iid)
            items_by_id[iid] = item
            if _check_date(item["date"], where) > date:
                raise BuildError("%s: 기사 날짜가 브리핑 날짜보다 뒤입니다" % where)
            sources = item.get("sources") or []
            if need_sources and not sources:
                raise BuildError("%s: sources — 기사에는 출처가 하나 이상 있어야 합니다" % where)
            _check_sources(sources, where)

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

def render_sources(sources):
    if not sources:
        return ""
    links = " ".join('<a href="%s" target="_blank" rel="noopener">%s</a>'
                     % (esc(s["url"]), inline(s["name"])) for s in sources)
    return '<p class="src"><span>출처<span class="sr"> (새 창에서 열림)</span></span> %s</p>' % links


_PERCENT = re.compile(r"(\d+(?:\.\d+)?)\s*%")


def render_chart(rows):
    """등락률 막대. 가운데 선에서 오른 것은 오른쪽, 내린 것은 왼쪽으로 뻗는다. 가장 큰 움직임이 반 칸을 채운다.
    수치는 표에 그대로 있으므로 화면 낭독기에는 숨긴다."""
    moves = []
    for r in rows:
        m = _PERCENT.search(r.get("change") or "")
        if m and r.get("dir") in ("up", "down"):
            moves.append((r, float(m.group(1))))
    if len(moves) < 2:
        return ""
    top = max(pct for _, pct in moves) or 1.0
    lis = "".join(
        '<li><span class="n">%s</span><span class="track"><span class="bar %s" style="width:%.1f%%"></span></span>'
        '<span class="p %s">%s%.2f%%</span></li>'
        % (inline(r["name"]), r["dir"], pct / top * 50, r["dir"], "+" if r["dir"] == "up" else "-", pct)
        for r, pct in moves)
    return '<ul class="chart" aria-hidden="true">%s</ul>' % lis


def render_market(day):
    market = day.get("market")
    if not market:
        return ""

    def row(r):
        small = "".join(" <small>%s</small>" % inline(r[k]) for k in ("note", "basis") if r.get(k))
        value = '<span class="v">%s</span>' % inline(r["value"]) if r.get("value") else ""
        change = ""
        if r.get("change"):
            cls = " " + r["dir"] if r.get("dir") in ("up", "down") else ""
            change = '<span class="c%s">%s</span>' % (cls, inline(r["change"]))
        cell = " ".join(part for part in (value, change) if part)
        return '<tr><th scope="row">%s%s</th><td>%s</td></tr>' % (inline(r["name"]), small, cell)

    parts = ['<aside class="market" aria-labelledby="market-title">',
             '<h2 id="market-title">시장 요약</h2>']
    rows = market.get("rows") or []
    parts.append(render_chart(rows))
    if rows:
        parts.append("<table><tbody>%s</tbody></table>" % "".join(row(r) for r in rows))
    forecast = market.get("forecast") or {}
    frows = forecast.get("rows") or []
    if frows:
        parts.append('<table><caption>%s</caption><tbody>%s</tbody></table>'
                     % (inline(forecast.get("title", "")), "".join(row(r) for r in frows)))
    if market.get("note"):
        parts.append('<p class="note">%s</p>' % inline(market["note"]))
    parts.append(render_sources(market.get("sources")))
    parts.append("</aside>")
    return "\n".join(p for p in parts if p)


def render_ticker(day):
    """시장 수치 한 줄. 읽기만 하는 줄이다."""
    rows = (day.get("market") or {}).get("rows") or []
    if not rows:
        return ""
    cells = []
    for r in rows:
        change = ""
        if r.get("change"):
            cls = " " + r["dir"] if r.get("dir") in ("up", "down") else ""
            change = ' <span class="c%s">%s</span>' % (cls, inline(r["change"]))
        value = ' <span class="v">%s</span>' % inline(r["value"]) if r.get("value") else ""
        cells.append('<li><span class="n">%s</span>%s%s</li>' % (inline(r["name"]), value, change))
    return ('<div class="ticker" role="group" aria-label="시장 수치 한 줄 요약"><div class="wrap"><ul>%s</ul>'
            '</div></div>' % "".join(cells))


def render_quick(day):
    items = day.get("quick") or []
    if not items:
        return ""
    lis = "".join("<li>%s</li>" % inline(q["text"]) for q in items)
    return ('<section class="quick" aria-labelledby="quick-title">'
            '<h2 id="quick-title">핵심 1분</h2><ul>%s</ul></section>' % lis)


def fold(cls, closed, opened, body):
    """펼침 하나의 모양과 말을 한 곳에서 정한다: 닫혔을 때 '… 더 보기', 열렸을 때 '… 접기'."""
    return ('<details class="fold %s"><summary><span class="when-closed">%s</span>'
            '<span class="when-open">%s</span></summary>%s</details>' % (cls, closed, opened, body))


def row(href, title, meta="", line="", count="", rid=""):
    """다른 페이지로 가는 줄 하나. meta 는 이미 만든 HTML, 나머지는 원문이다."""
    parts = []
    if meta:
        parts.append('<span class="meta">%s</span>' % meta)
    parts.append('<span class="t">%s</span>' % inline(title))
    if line:
        parts.append('<span class="line">%s</span>' % inline(line))
    if count:
        parts.append('<span class="count">%s</span>' % inline(count))
    attr = ' id="%s"' % esc(rid) if rid else ""
    return '<li%s><a href="%s">%s</a></li>' % (attr, href, "".join(parts))


def rows(items, cls=""):
    return '<ul class="rows%s">%s</ul>' % (" " + cls if cls else "", "".join(items))


def date_meta(value, weekday=False):
    label = short_date(value) + (" " + weekday_ko(value) if weekday else "")
    return '<time datetime="%s">%s</time>' % (esc(value), label)


def day_label(day):
    return "%s %s" % (short_date(day["date"]), weekday_ko(day["date"]))


def day_rows(days, href_prefix):
    return rows([row("%s%s/index.html" % (href_prefix, d["date"]), d["headline"],
                     meta=date_meta(d["date"], weekday=True)) for d in days], "days")


def topic_rows(topics, href_prefix):
    return rows([row("%s%s/index.html" % (href_prefix, esc(t["id"])), t["title"],
                     count="기사 %d건" % len(t["rows"])) for t in topics], "topics")


def lead_item(sec):
    """분야의 대표 기사. featured 가 있으면 그 기사, 없으면 첫 기사."""
    for item in sec["items"]:
        if item.get("featured"):
            return item
    return sec["items"][0]


def _part(cls, label, text):
    return '<div class="part %s"><h2>%s</h2><p>%s</p></div>' % (cls, inline(label), inline(text))


def article_body(item):
    """기사 본문. 사실만 있는 기사는 소제목 없이 문단만 둔다."""
    parts = []
    recheck = item.get("recheck")
    has_recheck = bool(recheck and recheck.get("text"))
    if item.get("so") or has_recheck:
        parts.append(_part("fact", "무슨 일이 있었나", item["facts"]))
    else:
        parts.append('<div class="part fact"><p>%s</p></div>' % inline(item["facts"]))
    if item.get("so"):
        parts.append(_part("why", "왜 중요한가", item["so"]))
    if has_recheck:
        parts.append(_part("recheck", recheck.get("label", "다시 볼 조건"), recheck["text"]))
    parts.append(render_sources(item.get("sources")))
    return "\n".join(p for p in parts if p)


def render_day_section(sec, root):
    """일별 브리핑의 분야 하나. 순서는 늘 같다: 이름과 건수, 한 줄 요약, 대표 기사, (더 있으면) 더 보기, 지난 기사.
    입력의 points 는 화면에 내지 않는다 (기사 줄의 첫 문장과 겹친다)."""
    sid = esc(sec["id"])
    name = inline(sec["title"])

    def item_row(i):
        return row("%s/index.html" % esc(i["id"]), i["title"], line=first_sentence(i["facts"]), rid=i["id"])

    parts = ['<section class="sec" id="%s" aria-labelledby="h-%s">' % (sid, sid),
             '<div class="sec-head"><h2 id="h-%s">%s</h2><span class="count">기사 %d건</span></div>'
             % (sid, name, len(sec["items"]))]
    if sec.get("lede"):
        parts.append('<p class="lede">%s</p>' % inline(sec["lede"]))
    first = lead_item(sec)
    rest = [i for i in sec["items"] if i is not first]
    parts.append(rows([item_row(first)]))
    if rest:
        parts.append(fold("more-items", "%s 기사 %d건 더 보기" % (name, len(rest)), "%s 기사 접기" % name,
                          rows([item_row(i) for i in rest])))
    parts.append('<p class="sec-more"><a href="%stopic/%s/index.html">지난 %s 기사</a></p>' % (root, sid, name))
    parts.append("</section>")
    return "\n".join(parts)


def render_side(day):
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

    if not blocks:
        return ""
    return '<aside class="side" aria-label="참고 정보">%s</aside>' % "".join(blocks)


def render_corrections(day):
    rows_ = day.get("corrections") or []
    if not rows_:
        return ""
    lis = "".join('<li><time datetime="%s">%s</time> %s</li>' % (esc(c["date"]), short_date(c["date"]), inline(c["text"]))
                  for c in rows_)
    return ('<section class="corrections" aria-labelledby="h-corrections">'
            '<h2 id="h-corrections">정정·수정 내역</h2><ul>%s</ul></section>' % lis)


def render_pager(prev, nxt, kind):
    """prev, nxt: (주소, 제목 HTML) 또는 None. kind: '브리핑' 또는 '기사'."""
    if not (prev or nxt):
        return ""
    parts = ""
    if prev:
        parts += '<a class="prev" href="%s"><span>이전 %s</span><b>%s</b></a>' % (prev[0], kind, prev[1])
    if nxt:
        parts += '<a class="next" href="%s"><span>다음 %s</span><b>%s</b></a>' % (nxt[0], kind, nxt[1])
    return '<nav class="pager" aria-label="이전·다음 %s">%s</nav>' % (kind, parts)


def _dateline(day):
    edition = " " + inline(day["edition"]) if day.get("edition") else ""
    return '<p class="dateline">%s%s</p>' % (long_date(day["date"]), edition)


def _hero(day, tag="h1"):
    checked = ""
    if day.get("checked_at"):
        checked = '<p class="checked">뉴스 재확인 %s</p>' % inline(day["checked_at"])
    return ('<section class="hero" aria-labelledby="top-title">%s'
            '<%s id="top-title">%s</%s><p class="summary">%s</p>%s</section>'
            % (_dateline(day), tag, inline(day["headline"]), tag, inline(day["summary"]), checked))


def _title_page(title, body, lead=""):
    """제목 하나와 본문으로 된 목록·고정 페이지의 틀."""
    return ('<main class="page" id="brief"><div class="lead"><section class="hero" aria-labelledby="top-title">'
            '<h1 id="top-title">%s</h1>%s</section></div><div class="body">%s</div></main>' % (title, lead, body))


def render_footer(site, links):
    parts = []
    meta = "".join("<span>%s</span>" % inline(m) for m in site["footer_meta"])
    meta += "".join('<a href="%s">%s</a>' % (href, inline(label)) for label, href in links)
    if meta:
        parts.append('<p class="meta">%s</p>' % meta)
    if site["disclaimer"]:
        parts.append("<p>%s</p>" % inline(site["disclaimer"]))
    return "\n".join(parts)


# ---------------------------------------------------------------- 페이지

def render_crumbs(crumbs):
    """위치 줄. crumbs: [(이름, 주소 또는 None)]. 주소가 없는 항목은 지금 있는 페이지다."""
    if not crumbs:
        return ""
    lis = []
    for label, href in crumbs:
        if href:
            lis.append('<li><a href="%s">%s</a></li>' % (href, inline(label)))
        else:
            lis.append('<li aria-current="page">%s</li>' % inline(label))
    return '<nav class="crumbs" aria-label="현재 위치"><ol class="wrap">%s</ol></nav>' % "".join(lis)


def wrap_page(site, pages, *, title, description, prefix, path, content,
              og_type="website", noindex=False, script="", current="", crumbs=None, ticker=""):
    """prefix: 이 페이지에서 사이트 루트까지의 상대 경로. path: 대표 주소의 경로 부분."""
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
    head.append('<link rel="icon" href="%sassets/favicon.svg" type="image/svg+xml">' % prefix)
    head.append('<link rel="alternate" type="application/atom+xml" title="%s" href="%sfeed.xml">'
                % (esc(site["title"]), prefix))
    menu = [("home", HOME_LABEL, prefix + "index.html"),
            ("news", NEWS_LABEL, prefix + "news/index.html"),
            ("topic", TOPIC_LABEL, prefix + "topic/index.html")]
    menu += [(p["slug"], p.get("nav") or p["title"], "%s%s/index.html" % (prefix, p["slug"])) for p in pages]
    links = [(label, href) for _, label, href in menu]

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
        "sectionnav": ticker + render_crumbs(crumbs),
        "content": content,
        "footer": render_footer(site, links),
        "script": script,
    }
    template = TEMPLATE.read_text(encoding="utf-8")
    return re.sub(r"\{\{(\w+)\}\}", lambda m: values[m.group(1)], template)


def render_intro(site, about_href):
    intro = site.get("intro")
    if not intro:
        return ""
    parts = ['<section class="intro" aria-labelledby="intro-title">',
             '<h1 id="intro-title">%s</h1>' % inline(intro["heading"])]
    parts += ['<p class="summary">%s</p>' % inline(p) for p in intro.get("paragraphs") or []]
    items = intro.get("items") or []
    if items:
        parts.append('<ul class="points">%s</ul>' % "".join("<li>%s</li>" % inline(i) for i in items))
    if about_href:
        parts.append('<p class="more"><a href="%s">편집 기준</a></p>' % about_href)
    parts.append("</section>")
    return "\n".join(parts)


def render_home(days, site, pages, topics):
    """첫 화면: 소개, 오늘의 주요 뉴스, 그날 브리핑으로 가는 버튼. 옆 칸에 시장·지난 브리핑·분야."""
    latest = days[0]
    day_href = "news/%s/index.html" % latest["date"]
    sections = visible_sections(latest)
    about = next((p for p in pages if p["slug"] == "about"), None)
    intro_html = render_intro(site, "about/index.html" if about else "")

    picks = [(s, it) for s, it in all_items(latest) if it.get("featured")]
    if not picks:
        picks = [(s, lead_item(s)) for s in sections]
    picks_html = ""
    if picks:
        picks_html = rows([row("news/%s/%s/index.html" % (latest["date"], esc(it["id"])), it["title"],
                               meta=inline(s["title"]), line=first_sentence(it["facts"]))
                           for s, it in picks[:HOME_FEATURED_LIMIT]])
    go = ""
    if sections:
        go = ('<p class="readmore"><a class="go" href="%s">%s 브리핑 전체 보기 <span class="count">기사 %d건</span></a></p>'
              % (day_href, short_date(latest["date"]), sum(len(s["items"]) for s in sections)))
    today = ('<section class="today" aria-labelledby="today-title">'
             '<p class="kicker" id="today-title">오늘의 주요 뉴스</p>%s%s%s</section>'
             % (_hero(latest, tag="h2" if intro_html else "h1"), picks_html, go))

    side = []
    chart = render_chart((latest.get("market") or {}).get("rows") or [])
    if chart:
        side.append('<section class="glance" aria-labelledby="market-title"><h2 id="market-title">시장</h2>%s'
                    '<p class="more"><a href="%s#market-title">시장 요약</a></p></section>' % (chart, day_href))
    recent = days[1:1 + HOME_RECENT_LIMIT]
    if recent:
        side.append('<section aria-labelledby="recent-title"><h2 id="recent-title">지난 브리핑</h2>%s'
                    '<p class="more"><a href="news/index.html">%s 전체</a></p></section>'
                    % (day_rows(recent, "news/"), NEWS_TITLE))
    if topics:
        side.append('<section aria-labelledby="topics-title"><h2 id="topics-title">%s</h2>%s</section>'
                    % (TOPIC_TITLE, topic_rows(topics, "topic/")))
    aside = '<aside class="rail" aria-label="둘러보기">%s</aside>' % "".join(side) if side else ""
    content = '<main class="page home" id="brief"><div class="lead">%s%s</div>%s</main>' % (intro_html, today, aside)
    return wrap_page(site, pages, title=site["title"],
                     description=site.get("description") or plain(latest["summary"]),
                     prefix="", path="/", content=content, current="home", ticker=render_ticker(latest))


def render_day(day, older, newer, site, pages):
    """하루치 브리핑: 헤드라인, 핵심 1분, 분야별 기사 목록. 기사 본문은 기사 페이지에 있다."""
    sections = visible_sections(day)

    def link(d):
        return ("../%s/index.html" % d["date"], "%s %s" % (short_date(d["date"]), inline(d["headline"]))) if d else None

    rail = render_market(day) + render_side(day)
    content = ('<main class="page" id="brief"><div class="lead">%s%s</div><div class="body">%s%s%s</div>%s</main>'
               % (_hero(day), render_quick(day),
                  "\n".join(render_day_section(s, "../../") for s in sections), render_corrections(day),
                  render_pager(link(older), link(newer), "브리핑"),
                  '<div class="rail">%s</div>' % rail if rail else ""))
    edition = " " + plain(day["edition"]) if day.get("edition") else ""
    has_fold = any(len(s["items"]) > 1 for s in sections)
    return wrap_page(
        site, pages,
        title="%s%s | %s" % (long_date(day["date"]), edition, site["title"]),
        description=plain(day["summary"]), prefix="../../", path="/news/%s/" % day["date"],
        content=content, og_type="article", ticker=render_ticker(day),
        script=OPEN_SCRIPT if has_fold else "", current="news",
        crumbs=[(HOME_LABEL, "../../index.html"), (NEWS_LABEL, "../index.html"), (day_label(day), None)])


def render_article(day, sec, item, prev, nxt, site, pages):
    """기사 하나. 옆 칸에 같은 날 같은 분야의 다른 기사와 돌아갈 곳을 둔다."""
    root = "../../../"
    sid = esc(sec["id"])
    name = inline(sec["title"])

    def link(i):
        return ("../%s/index.html" % esc(i["id"]), inline(i["title"])) if i else None

    head = ('<p class="dateline"><a href="%stopic/%s/index.html">%s</a> %s</p><h1 id="top-title">%s</h1>'
            % (root, sid, name, date_meta(item["date"]), inline(item["title"])))
    fixes = ""
    if day.get("corrections"):
        fixes = '<p class="more"><a href="../index.html#h-corrections">이 브리핑의 정정·수정 내역</a></p>'
    side = []
    others = [i for i in sec["items"] if i is not item]
    if others:
        side.append('<section aria-labelledby="related-title"><h2 id="related-title">같은 날 %s 기사</h2>%s</section>'
                    % (name, rows([row("../%s/index.html" % esc(i["id"]), i["title"]) for i in others])))
    side.append('<section>%s</section>' % rows([
        row("../index.html", "%s 브리핑" % day_label(day)),
        row("%stopic/%s/index.html" % (root, sid), "지난 %s 기사" % sec["title"])]))
    content = ('<main class="page article" id="brief"><div class="lead">'
               '<article class="story" aria-labelledby="top-title">%s%s</article>%s%s</div>'
               '<aside class="rail" aria-label="이어 읽기">%s</aside></main>'
               % (head, article_body(item), fixes, render_pager(link(prev), link(nxt), "기사"), "".join(side)))
    return wrap_page(site, pages, title="%s | %s" % (plain(item["title"]), site["title"]),
                     description=first_sentence(item["facts"]), prefix=root,
                     path="/news/%s/%s/" % (day["date"], item["id"]), content=content, og_type="article",
                     current="news",
                     crumbs=[(HOME_LABEL, root + "index.html"), (NEWS_LABEL, root + "news/index.html"),
                             (day_label(day), "../index.html")])


def render_archive(days, site, pages):
    groups = {}
    for d in days:  # days 는 최신순이라 월도 최신순으로 쌓인다
        groups.setdefault(month_label(d["date"]), []).append(d)
    months = "".join('<section class="month"><h2>%s</h2>%s</section>' % (label, day_rows(ds, ""))
                     for label, ds in groups.items())
    return wrap_page(site, pages, title="%s | %s" % (NEWS_TITLE, site["title"]),
                     description="%s의 날짜별 브리핑 목록" % site["title"],
                     prefix="../", path="/news/", content=_title_page(NEWS_TITLE, months), current="news",
                     crumbs=[(HOME_LABEL, "../index.html"), (NEWS_LABEL, None)])


def collect_topics(days):
    """분야 id 별로 모든 날짜의 기사를 모은다. 이름은 가장 최근 날짜의 것을 쓴다. days 는 최신순."""
    topics = {}
    for day in days:
        for sec in visible_sections(day):
            topic = topics.setdefault(sec["id"], {"id": sec["id"], "title": sec["title"], "rows": []})
            topic["rows"].extend((day, item) for item in sec["items"])
    return list(topics.values())


def render_topic_index(topics, site, pages):
    return wrap_page(site, pages, title="%s | %s" % (TOPIC_TITLE, site["title"]),
                     description="%s의 분야별 기사 목록" % site["title"],
                     prefix="../", path="/topic/", content=_title_page(TOPIC_TITLE, topic_rows(topics, "")),
                     current="topic", crumbs=[(HOME_LABEL, "../index.html"), (TOPIC_LABEL, None)])


def render_topic(topic, site, pages):
    label = "%s 기사" % plain(topic["title"])
    body = rows([row("../../news/%s/%s/index.html" % (day["date"], esc(item["id"])), item["title"],
                     meta=date_meta(day["date"]), line=first_sentence(item["facts"]))
                 for day, item in topic["rows"]])
    return wrap_page(site, pages, title="%s | %s" % (label, site["title"]),
                     description="%s에 실린 %s 기사 목록" % (site["title"], plain(topic["title"])),
                     prefix="../../", path="/topic/%s/" % topic["id"],
                     content=_title_page("%s 기사" % inline(topic["title"]), body), current="topic",
                     crumbs=[(HOME_LABEL, "../../index.html"), (TOPIC_LABEL, "../index.html"),
                             (plain(topic["title"]), None)])


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
                     crumbs=[(HOME_LABEL, "../index.html"), (page.get("nav") or page["title"], None)])


def render_404(site, pages):
    lead = ('<p class="summary">주소가 바뀌었거나 없는 페이지입니다.</p>'
            '<p class="readmore"><a class="go" href="/index.html">홈으로 가기</a></p>')
    return wrap_page(site, pages, title="페이지를 찾을 수 없습니다 | %s" % site["title"], description="",
                     prefix="/", path="/404.html", content=_title_page("페이지를 찾을 수 없습니다", "", lead),
                     noindex=True)


def render_sitemap(site, rels):
    urls = sorted(site["base_url"] + "/" + rel[:-len("index.html")] for rel in rels)
    body = "".join("<url><loc>%s</loc></url>\n" % esc(u) for u in urls)
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n%s</urlset>\n' % body)


def render_feed(site, days):
    def entry(d):
        url = "%s/news/%s/" % (site["base_url"], d["date"])
        return ("<entry><title>%s</title><link href=\"%s\"/><id>%s</id><updated>%s</updated>"
                "<summary>%s</summary></entry>\n"
                % (esc(plain(d["headline"])), url, url, briefing_time(d).isoformat(), esc(plain(d["summary"]))))
    updated = briefing_time(days[0]).isoformat()
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<feed xmlns="http://www.w3.org/2005/Atom">\n'
            "<title>%s</title><link href=\"%s/\"/><link rel=\"self\" href=\"%s/feed.xml\"/>"
            "<id>%s/</id><updated>%s</updated><author><name>%s</name></author>\n%s</feed>\n"
            % (esc(site["title"]), site["base_url"], site["base_url"], site["base_url"], updated,
               esc(site["title"]), "".join(entry(d) for d in days[:30])))


# ---------------------------------------------------------------- 금지 단어 검사·쓰기

def load_blocklist(root, site):
    """공개 패턴(_site.json)과 로컬 전용 패턴(보관/blocklist.local.txt)을 합쳐 컴파일한다."""
    patterns = list(site["blocklist"])
    local = Path(root) / LOCAL_BLOCKLIST
    if local.is_file():
        for line in local.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                patterns.append(line)
    compiled = []
    for p in patterns:
        try:
            compiled.append((p, re.compile(p)))
        except re.error as e:
            raise BuildError("blocklist 패턴 오류 %r: %s" % (p, e))
    return compiled


def _stop_on_matches(problems):
    if problems:
        raise BuildError("금지 단어가 있어 생성을 멈췄습니다 (아무것도 쓰지 않음):\n  "
                         + "\n  ".join(problems))


def check_sources_text(named, patterns):
    """입력 JSON의 모든 문자열을 검사한다. 렌더링되지 않는 필드도 포함한다."""
    problems = []
    for label, data, skip in named:
        for path, text in iter_strings(data, skip=skip):
            for p, rx in patterns:
                if rx.search(text):
                    problems.append("%s: %s — 금지 패턴 '%s'" % (label, path, p))
    _stop_on_matches(problems)


def check_pages(outputs, patterns):
    """생성된 결과 전체(본문·속성·메타)를 검사한다."""
    problems = []
    for rel, page in outputs.items():
        text = html.unescape(page).replace(WORD_JOINER, "")
        for p, rx in patterns:
            if rx.search(text):
                problems.append("%s: 생성 결과 — 금지 패턴 '%s'" % (rel, p))
    _stop_on_matches(problems)


def _only_generated(path):
    """이 프로그램이 만든 index.html 말고는 아무것도 없는 폴더인가."""
    return {p.name for p in path.iterdir()} <= {"index.html"}


def remove_stale(out, days, topics):
    """내용이 사라진 날짜·기사·분야의 폴더를 지운다. 이 프로그램이 만든 파일만 든 폴더만 지운다."""
    topic_dir = out / "topic"
    keep_topics = {t["id"] for t in topics}
    if topic_dir.is_dir():
        for child in sorted(topic_dir.iterdir()):
            if child.is_dir() and child.name not in keep_topics and _only_generated(child):
                shutil.rmtree(child)

    keep = {d["date"]: {item["id"] for _, item in all_items(d)} for d in days}
    for child in sorted((out / "news").iterdir()):
        if not (child.is_dir() and DAY_DIR.match(child.name)):
            continue
        ids = keep.get(child.name, set())
        for sub in sorted(child.iterdir()):
            if sub.is_dir() and sub.name not in ids and _only_generated(sub):
                shutil.rmtree(sub)
        if child.name not in keep and _only_generated(child):
            shutil.rmtree(child)


def build(root, out=None, check_only=False):
    root = Path(root)
    out = Path(out) if out else root
    site = load_site(root)
    days = load_days(root, site)
    pages = load_pages(root)
    if not days:
        raise BuildError("content/ 에 YYYY-MM-DD.json 파일이 없습니다")
    patterns = load_blocklist(root, site)
    named = [("_site.json", site, ("blocklist",))]
    named += [("%s.json" % d["date"], d, ()) for d in days]
    named += [("pages/%s.json" % p["slug"], p, ()) for p in pages]
    check_sources_text(named, patterns)

    topics = collect_topics(days)
    outputs = {"index.html": render_home(days, site, pages, topics),
               "news/index.html": render_archive(days, site, pages),
               "topic/index.html": render_topic_index(topics, site, pages)}
    for topic in topics:
        outputs["topic/%s/index.html" % topic["id"]] = render_topic(topic, site, pages)
    for i, day in enumerate(days):
        newer = days[i - 1] if i > 0 else None
        older = days[i + 1] if i + 1 < len(days) else None
        outputs["news/%s/index.html" % day["date"]] = render_day(day, older, newer, site, pages)
        seq = all_items(day)
        for n, (sec, item) in enumerate(seq):
            prev = seq[n - 1][1] if n > 0 else None
            nxt = seq[n + 1][1] if n + 1 < len(seq) else None
            outputs["news/%s/%s/index.html" % (day["date"], item["id"])] = render_article(
                day, sec, item, prev, nxt, site, pages)
    for page in pages:
        outputs["%s/index.html" % page["slug"]] = render_static(page, site, pages)
    public = [rel for rel in outputs]
    outputs["404.html"] = render_404(site, pages)
    outputs["sitemap.xml"] = render_sitemap(site, public)
    outputs["robots.txt"] = "User-agent: *\nAllow: /\n\nSitemap: %s/sitemap.xml\n" % site["base_url"]
    outputs["feed.xml"] = render_feed(site, days)

    check_pages(outputs, patterns)
    if check_only:
        return outputs

    for rel, text in outputs.items():
        path = out / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists() or path.read_text(encoding="utf-8") != text:
            path.write_text(text, encoding="utf-8")
    remove_stale(out, days, topics)
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
