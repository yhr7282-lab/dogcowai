#!/usr/bin/env python3
"""dogcowai 사이트 생성기.

content/YYYY-MM-DD.json (날짜별 내용), content/_site.json (공통 설정), content/pages/*.json (고정 페이지)에서
홈·목록·일별·고정 페이지와 404, sitemap.xml, robots.txt, feed.xml 을 만든다.

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
RESERVED_IDS = {"brief", "top-title", "market-title", "quick-title", "recent-title", "featured-title",
                "topics-title", "h-sched", "h-absent", "h-corrections"}

_BOLD = re.compile(r"\*\*(.+?)\*\*")
_DIRECTION = re.compile(r"\{\{(up|down):(.+?)\}\}")
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?%?")

NAV_SCRIPT = """<script>
(function(){if(!('IntersectionObserver' in window))return;var bar=document.querySelector('.sections .wrap');
var links=document.querySelectorAll('.sections a');var map={};
links.forEach(function(a){map[a.getAttribute('href').slice(1)]=a});
var io=new IntersectionObserver(function(es){es.forEach(function(e){if(!e.isIntersecting)return;
links.forEach(function(a){a.removeAttribute('aria-current')});var a=map[e.target.id];
if(a){a.setAttribute('aria-current','true');bar.scrollLeft=a.offsetLeft-16}})},{rootMargin:'-15% 0px -75% 0px'});
document.querySelectorAll('.sec').forEach(function(s){io.observe(s)})})();
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


def render_quick(day, href_prefix=""):
    items = day.get("quick") or []
    if not items:
        return ""
    lis = "".join('<li><a href="%s#%s">%s</a></li>' % (href_prefix, esc(q["ref"]), inline(q["text"]))
                  for q in items)
    return ('<section class="quick" aria-labelledby="quick-title">'
            '<h2 id="quick-title">핵심 1분</h2><ul>%s</ul></section>' % lis)


def _part(cls, label, text):
    return '<div class="part %s"><h4>%s</h4><p>%s</p></div>' % (cls, inline(label), inline(text))


def render_item(item):
    parts = ['<article class="item" id="%s">' % esc(item["id"]),
             '<time datetime="%s">%s</time>' % (esc(item["date"]), short_date(item["date"])),
             "<h3>%s</h3>" % inline(item["title"]),
             _part("fact", "무슨 일이 있었나", item["facts"])]
    if item.get("so"):
        parts.append(_part("why", "왜 중요한가", item["so"]))
    recheck = item.get("recheck")
    if recheck and recheck.get("text"):
        parts.append(_part("recheck", recheck.get("label", "다시 볼 조건"), recheck["text"]))
    parts.append(render_sources(item.get("sources")))
    parts.append("</article>")
    return "\n".join(p for p in parts if p)


def render_section(sec):
    sid = esc(sec["id"])
    parts = ['<section class="sec" id="%s" aria-labelledby="h-%s">' % (sid, sid),
             '<div class="sec-head"><h2 id="h-%s">%s</h2><span class="count">%d건</span></div>'
             % (sid, inline(sec["title"]), len(sec["items"]))]
    if sec.get("lede"):
        parts.append('<p class="lede">%s</p>' % inline(sec["lede"]))
    points = sec.get("points") or []
    if points:
        parts.append('<ul class="points">%s</ul>' % "".join("<li>%s</li>" % inline(p) for p in points))
    parts.extend(render_item(i) for i in sec["items"])
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
    rows = day.get("corrections") or []
    if not rows:
        return ""
    lis = "".join('<li><time datetime="%s">%s</time> %s</li>' % (esc(c["date"]), short_date(c["date"]), inline(c["text"]))
                  for c in rows)
    return ('<section class="corrections" aria-labelledby="h-corrections">'
            '<h2 id="h-corrections">정정·수정 내역</h2><ul>%s</ul></section>' % lis)


def render_section_nav(sections):
    if not sections:
        return ""
    links = "".join('<a href="#%s">%s</a>' % (esc(s["id"]), inline(s.get("nav") or s["title"]))
                    for s in sections)
    return '<nav class="sections" aria-label="분야 바로가기"><div class="wrap">%s</div></nav>' % links


def render_pager(older, newer):
    if not (older or newer):
        return ""

    def link(day, cls, label):
        return ('<a class="%s" href="../%s/index.html"><span>%s</span><b>%s %s</b></a>'
                % (cls, day["date"], label, short_date(day["date"]), inline(day["headline"])))

    parts = ""
    if older:
        parts += link(older, "prev", "이전 브리핑")
    if newer:
        parts += link(newer, "next", "다음 브리핑")
    return '<nav class="pager" aria-label="이전·다음 브리핑">%s</nav>' % parts


def _dateline(day):
    edition = " " + inline(day["edition"]) if day.get("edition") else ""
    return '<p class="dateline">%s%s</p>' % (long_date(day["date"]), edition)


def _hero(day, extra=""):
    checked = ""
    if day.get("checked_at"):
        checked = '<p class="checked">뉴스 재확인 %s</p>' % inline(day["checked_at"])
    return ('<section class="hero" aria-labelledby="top-title">%s'
            '<h1 id="top-title">%s</h1><p class="summary">%s</p>%s%s</section>'
            % (_dateline(day), inline(day["headline"]), inline(day["summary"]), checked, extra))


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

def wrap_page(site, pages, *, title, description, prefix, path, content,
              section_nav="", og_type="website", noindex=False, script=""):
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
    links = [("지난 브리핑", prefix + "news/index.html")]
    links += [(p.get("nav") or p["title"], "%s%s/index.html" % (prefix, p["slug"])) for p in pages]
    values = {
        "title": esc(title),
        "head": "\n".join(head),
        "css": prefix + "assets/style.css",
        "home": prefix + "index.html",
        "brand": inline(site["brand"]),
        "toplinks": "".join('<a class="toplink" href="%s">%s</a>' % (href, inline(label)) for label, href in links),
        "sectionnav": section_nav,
        "content": content,
        "footer": render_footer(site, links),
        "script": script,
    }
    template = TEMPLATE.read_text(encoding="utf-8")
    return re.sub(r"\{\{(\w+)\}\}", lambda m: values[m.group(1)], template)


def render_day(day, older, newer, site, pages):
    sections = visible_sections(day)
    content = (
        '<main class="page" id="brief">'
        '<div class="lead">%s%s</div>%s'
        '<div class="body">%s%s<p class="totop"><a href="#brief">맨 위로</a></p>%s</div>%s</main>'
        % (_hero(day), render_quick(day), render_market(day),
           "\n".join(render_section(s) for s in sections), render_corrections(day),
           render_pager(older, newer), render_side(day)))
    edition = " " + plain(day["edition"]) if day.get("edition") else ""
    return wrap_page(
        site, pages,
        title="%s%s | %s" % (long_date(day["date"]), edition, site["title"]),
        description=plain(day["summary"]), prefix="../../", path="/news/%s/" % day["date"],
        content=content, section_nav=render_section_nav(sections), og_type="article",
        script=NAV_SCRIPT if sections else "")


def _day_list(days, href_prefix):
    lis = "".join(
        '<li><a href="%s%s/index.html"><time datetime="%s">%s %s</time><span class="t">%s</span></a></li>'
        % (href_prefix, d["date"], d["date"], short_date(d["date"]), weekday_ko(d["date"]), inline(d["headline"]))
        for d in days)
    return '<ul class="daylist">%s</ul>' % lis


def render_home(days, site, pages):
    latest = days[0]
    day_href = "news/%s/index.html" % latest["date"]
    readmore = ('<p class="readmore"><a href="%s">%s 브리핑 전체 보기</a></p>' % (day_href, long_date(latest["date"])))

    featured = [(s, it) for s, it in all_items(latest) if it.get("featured")][:HOME_FEATURED_LIMIT]
    featured_html = ""
    if featured:
        lis = "".join(
            '<li><a href="%s#%s"><span class="cat">%s</span><span class="t">%s</span>'
            '<span class="lead-line">%s</span><time datetime="%s">%s</time></a></li>'
            % (day_href, esc(it["id"]), inline(s["title"]), inline(it["title"]),
               inline(first_sentence(it["facts"])), esc(it["date"]), short_date(it["date"]))
            for s, it in featured)
        featured_html = ('<section class="featured" aria-labelledby="featured-title">'
                         '<h2 id="featured-title">주요 기사</h2><ul>%s</ul></section>' % lis)

    topics = "".join('<li><a href="%s#%s">%s <span class="count">%d건</span></a></li>'
                     % (day_href, esc(s["id"]), inline(s["title"]), len(s["items"]))
                     for s in visible_sections(latest))
    topics_html = ('<section class="topics" aria-labelledby="topics-title">'
                   '<h2 id="topics-title">분야별 보기</h2><ul>%s</ul></section>' % topics) if topics else ""

    recent = days[1:1 + HOME_RECENT_LIMIT]
    recent_html = ""
    if recent:
        recent_html = ('<section class="recent" aria-labelledby="recent-title">'
                       '<h2 id="recent-title">지난 브리핑</h2>%s'
                       '<p class="more"><a href="news/index.html">전체 목록 보기</a></p></section>'
                       % _day_list(recent, "news/"))
    content = ('<main class="page" id="brief"><div class="lead">%s%s</div>%s'
               '<div class="body">%s%s%s</div></main>'
               % (_hero(latest, readmore), render_quick(latest, day_href), render_market(latest),
                  featured_html, topics_html, recent_html))
    return wrap_page(site, pages, title=site["title"],
                     description=site.get("description") or plain(latest["summary"]),
                     prefix="", path="/", content=content)


def render_archive(days, site, pages):
    groups = {}
    for d in days:  # days 는 최신순이라 월도 최신순으로 쌓인다
        groups.setdefault(month_label(d["date"]), []).append(d)
    months = "".join('<section class="month"><h2>%s</h2>%s</section>' % (label, _day_list(ds, ""))
                     for label, ds in groups.items())
    content = ('<main class="page" id="brief"><div class="lead"><section class="hero" aria-labelledby="top-title">'
               '<h1 id="top-title">지난 브리핑</h1>'
               '<p class="summary">날짜별로 쌓이는 브리핑입니다. 날짜를 누르면 그날의 전체 내용을 볼 수 있습니다.</p>'
               '</section></div><div class="body">%s</div></main>' % months)
    return wrap_page(site, pages, title="지난 브리핑 | %s" % site["title"],
                     description="%s의 날짜별 브리핑 목록" % site["title"],
                     prefix="../", path="/news/", content=content)


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
    content = ('<main class="page" id="brief"><div class="lead"><section class="hero" aria-labelledby="top-title">'
               '<h1 id="top-title">%s</h1>%s</section></div><div class="body">%s</div></main>'
               % (inline(page["title"]), lead, "\n".join(blocks)))
    return wrap_page(site, pages, title="%s | %s" % (page["title"], site["title"]),
                     description=page.get("description", ""), prefix="../", path="/%s/" % page["slug"],
                     content=content)


def render_404(site, pages):
    content = ('<main class="page" id="brief"><div class="lead"><section class="hero" aria-labelledby="top-title">'
               '<h1 id="top-title">페이지를 찾을 수 없습니다</h1>'
               '<p class="summary">주소가 바뀌었거나 없는 페이지입니다.</p>'
               '<p class="readmore"><a href="/index.html">홈으로 가기</a></p>'
               '</section></div></main>')
    return wrap_page(site, pages, title="페이지를 찾을 수 없습니다 | %s" % site["title"], description="",
                     prefix="/", path="/404.html", content=content, noindex=True)


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

    outputs = {"index.html": render_home(days, site, pages),
               "news/index.html": render_archive(days, site, pages)}
    for i, day in enumerate(days):
        newer = days[i - 1] if i > 0 else None
        older = days[i + 1] if i + 1 < len(days) else None
        outputs["news/%s/index.html" % day["date"]] = render_day(day, older, newer, site, pages)
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

    keep = {d["date"] for d in days}
    for child in (out / "news").iterdir():
        if child.is_dir() and DAY_DIR.match(child.name) and child.name not in keep:
            if {p.name for p in child.iterdir()} <= {"index.html"}:
                shutil.rmtree(child)
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
