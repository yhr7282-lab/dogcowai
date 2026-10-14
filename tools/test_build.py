"""tools/build.py 테스트. 실행: python3 tools/test_build.py

테스트에 쓰는 이름·문장은 모두 가상의 표식이다. 실제 개인 정보를 넣지 않는다.
"""
import contextlib
import copy
import hashlib
import io
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))
import build  # noqa: E402

SITE = {
    "brand": "dogcowai 경제브리핑",
    "title": "dogcowai 경제브리핑",
    "base_url": "http://example.test",
    "description": "경제·주식·AI 뉴스를 날짜별로 요약한 브리핑",
    "footer_meta": ["dogcowai 경제브리핑"],
    "disclaimer": "본 페이지는 정보 제공을 위한 요약이며 투자 권유가 아닙니다.",
    "require_sources_from": "2026-10-01",
    "market_max_age_days": 4,
    "blocklist": ["매입가", r"J-\d{6,}"],
}


def make_day(date="2026-10-13", **over):
    day = {
        "date": date,
        "edition": "오후판",
        "checked_at": "17:02 KST",
        "headline": f"{date} 헤드라인",
        "summary": "요약 문장입니다.",
        "market": {
            "rows": [{"name": "KOSPI", "note": "매도 사이드카 발동", "value": "6,035.35",
                      "change": "▼ 5.46%", "dir": "down", "basis": "종가", "asof": date}],
            "note": "기준시각이 자산마다 다름.",
            "sources": [{"name": "가상통신", "url": "https://example.com/market"}],
        },
        "quick": [{"text": "지수 {{down:-5.46%}} 하락", "ref": "a-one"}],
        "sections": [{
            "id": "economy", "title": "경제",
            "lede": "한 줄 요약", "points": ["포인트 하나"],
            "items": [
                {"id": "a-one", "date": date, "title": "기사 하나", "featured": True,
                 "facts": "지수가 5.46% 내렸다. 둘째 문장 **강조**.",
                 "so": "의미 문장.",
                 "recheck": {"label": "다시 볼 조건", "text": "조건 문장."},
                 "sources": [{"name": "Reuters", "url": "https://example.com/a"}]},
                {"id": "a-two", "date": date, "title": "기사 둘", "facts": "두 번째 사실.",
                 "sources": [{"name": "AP", "url": "https://example.com/b"}]},
            ],
        }],
    }
    day.update(over)
    return day


class InlineTests(unittest.TestCase):
    def test_escapes_html_and_applies_markup(self):
        out = build.inline("<script>x</script> **굵게** {{down:-5%}} {{up:+1%}} A&B")
        self.assertNotIn("<script>", out)
        self.assertIn("&lt;script&gt;", out)
        self.assertIn("<strong>굵게</strong>", out)
        self.assertIn('<strong class="down">-5%</strong>', out)
        self.assertIn('<strong class="up">+1%</strong>', out)
        self.assertIn("A&amp;B", out)

    def test_adds_word_joiner_after_middle_dot(self):
        self.assertEqual(build.inline("물가·금리"), "물가·⁠금리")

    def test_plain_removes_markup(self):
        self.assertEqual(build.plain("**a** {{down:b}}"), "a b")

    def test_first_sentence_keeps_decimals_together(self):
        self.assertEqual(build.first_sentence("지수가 5.46% 내렸다. 둘째 문장."), "지수가 5.46% 내렸다.")


class SiteCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "content").mkdir()
        self.write_site(SITE)

    def tearDown(self):
        self._tmp.cleanup()

    def write_site(self, site):
        (self.root / "content" / "_site.json").write_text(
            json.dumps(site, ensure_ascii=False), encoding="utf-8")

    def write_day(self, day, name=None):
        name = name or day["date"]
        (self.root / "content" / f"{name}.json").write_text(
            json.dumps(day, ensure_ascii=False), encoding="utf-8")

    def write_page(self, page):
        d = self.root / "content" / "pages"
        d.mkdir(exist_ok=True)
        (d / f"{page['slug']}.json").write_text(json.dumps(page, ensure_ascii=False), encoding="utf-8")

    def build(self, **kw):
        return build.build(self.root, **kw)

    def read(self, rel):
        return (self.root / rel).read_text(encoding="utf-8")

    def exists(self, rel):
        return (self.root / rel).exists()

    def snapshot(self):
        out = {}
        for p in sorted(self.root.rglob("*")):
            if p.is_file() and "content" not in p.relative_to(self.root).parts:
                out[str(p.relative_to(self.root))] = hashlib.sha256(p.read_bytes()).hexdigest()
        return out

    def day_page(self, day):
        self.write_day(day)
        self.build()
        return self.read(f"news/{day['date']}/index.html")

    def article_page(self, day, item_id):
        self.write_day(day)
        self.build()
        return self.read("news/%s/%s/index.html" % (day["date"], item_id))

    def assert_rejected(self, day, *needles):
        before = self.snapshot()
        self.write_day(day)
        with self.assertRaises(build.BuildError) as ctx:
            self.build()
        for needle in needles:
            self.assertIn(needle, str(ctx.exception))
        self.assertEqual(self.snapshot(), before)


class BuildOutputTests(SiteCase):
    """페이지 구성. 페이지는 여섯 가지다: 홈(소개 + 최신 브리핑), 브리핑, 기사, 날짜별 목록, 분야 목록, 분야별 기사."""

    PAGES = ("index.html", "news/index.html", "news/2026-10-13/index.html", "news/2026-10-13/a-one/index.html",
             "topic/index.html", "topic/economy/index.html")

    def two_sections(self):
        day = make_day()
        day["sections"].append({"id": "auto", "title": "자동차", "items": [
            {"id": "c-one", "date": day["date"], "title": "차 기사", "facts": "차 사실.",
             "sources": [{"name": "AP", "url": "https://example.com/c"}]}]})
        return day

    def test_creates_all_pages_and_site_files(self):
        self.write_day(make_day())
        self.build()
        for rel in self.PAGES + ("news/2026-10-13/a-two/index.html", "404.html", "sitemap.xml", "robots.txt",
                                 "feed.xml"):
            self.assertTrue(self.exists(rel), rel)

    # ---- 모든 페이지에 공통

    def test_menu_marks_the_page_the_reader_is_on(self):
        self.write_day(make_day())
        self.build()
        home, archive, topics = self.read("index.html"), self.read("news/index.html"), self.read("topic/index.html")
        self.assertIn('href="index.html" aria-current="page">홈<', home)
        self.assertNotIn('aria-current="page">날짜별<', home)
        self.assertIn('href="../news/index.html" aria-current="page">날짜별<', archive)
        self.assertIn('href="../topic/index.html" aria-current="page">분야별<', topics)
        self.assertIn('aria-current="page">날짜별<', self.read("news/2026-10-13/index.html"))
        self.assertIn('aria-current="page">날짜별<', self.read("news/2026-10-13/a-one/index.html"))
        self.assertIn('aria-current="page">분야별<', self.read("topic/economy/index.html"))

    def test_pages_carry_no_market_strip_location_line_folds_buttons_or_scripts(self):
        self.write_day(make_day())
        self.build()
        for rel in self.PAGES:
            html = self.read(rel)
            for absent in ('class="ticker"', 'class="crumbs"', "<details", 'class="go"', 'class="hint"', "누르면"):
                self.assertNotIn(absent, html, (rel, absent))
            scripts = re.findall(r"<script\b[^>]*>", html)
            self.assertTrue(all('type="application/ld+json"' in s for s in scripts), (rel, scripts))

    def test_footer_links_the_fixed_pages_and_the_feed_but_not_the_menu_again(self):
        self.write_page({"slug": "about", "title": "소개", "sections": [{"heading": "무엇인가", "paragraphs": ["문단"]}]})
        page = self.day_page(make_day())
        footer = page[page.index("<footer>"):]
        self.assertIn('href="../../about/index.html">소개<', footer)
        self.assertIn('href="../../feed.xml">RSS<', footer)
        for absent in (">홈<", ">날짜별<", ">분야별<"):
            self.assertNotIn(absent, footer)
        self.assertIn("투자 권유가 아닙니다", footer)

    def test_site_brand_is_used_in_masthead_title_and_footer(self):
        page = self.day_page(make_day())
        self.assertIn('class="brand" href="../../index.html">dogcowai 경제브리핑</a>', page)
        self.assertIn("| dogcowai 경제브리핑</title>", page)

    def test_internal_links_are_relative_index_files(self):
        self.write_day(make_day())
        self.build()
        page = self.read("news/2026-10-13/index.html")
        self.assertIn('href="../../index.html"', page)
        self.assertIn('href="../../assets/style.css"', page)
        self.assertIn('href="../../news/index.html"', page)
        article = self.read("news/2026-10-13/a-one/index.html")
        self.assertIn('href="../../../assets/style.css"', article)
        self.assertIn('href="../../../news/index.html"', article)

    # ---- 홈: 짧은 소개 다음에 최신 브리핑 전체

    def test_home_opens_with_a_short_introduction_then_the_latest_briefing(self):
        site = dict(SITE, intro={"heading": "소개 제목", "paragraphs": ["소개 문단"], "items": ["원칙 하나"]})
        self.write_site(site)
        self.write_page({"slug": "about", "title": "소개", "sections": [{"heading": "무엇인가", "paragraphs": ["문단"]}]})
        self.write_day(make_day())
        self.build()
        home = self.read("index.html")
        intro = home[home.index('class="intro"'):home.index("</section>", home.index('class="intro"'))]
        self.assertIn("소개 제목", intro)
        self.assertIn("소개 문단", intro)
        self.assertIn('href="about/index.html"', intro)
        self.assertNotIn("원칙 하나", home)
        self.assertLess(home.index('class="intro"'), home.index('<h1 id="top-title">2026-10-13 헤드라인</h1>'))

    def test_home_carries_the_whole_latest_briefing(self):
        self.write_day(make_day("2026-10-12"))
        self.write_day(make_day("2026-10-13"))
        self.build()
        home = self.read("index.html")
        for part in ('<h1 id="top-title">2026-10-13 헤드라인</h1>', 'id="quick-title"', 'id="economy"',
                     'href="news/2026-10-13/a-one/index.html"', 'href="news/2026-10-13/a-two/index.html"',
                     'id="market-title"', 'href="news/2026-10-12/index.html"'):
            self.assertIn(part, home)
        self.assertNotIn("2026-10-12 헤드라인</h1>", home)

    # ---- 브리핑: 헤드라인, 핵심 1분, 분야별 기사 목록, 시장, 일정, 정정, 이전·다음

    def test_briefing_reads_in_one_fixed_order(self):
        self.write_day(make_day("2026-10-12"))
        day = make_day("2026-10-13", corrections=[{"date": "2026-10-14", "text": "수치를 바로잡았다."}],
                       side={"schedule": [{"at": "2026-10-13T08:30:00-04:00", "title": "가상 발표"}]})
        page = self.day_page(day)
        order = [page.index(s) for s in (
            'class="dateline"', '<h1 id="top-title">', 'class="summary"', "뉴스 재확인 17:02 KST",
            'id="quick-title"', 'id="economy"', 'id="market-title"', 'id="h-sched"', 'id="h-corrections"',
            'class="pager"')]
        self.assertEqual(order, sorted(order))
        self.assertIn("2026년 10월 13일 화요일 오후판", page)

    def test_briefing_lists_every_story_with_its_title_and_one_line_but_not_its_body(self):
        page = self.day_page(make_day())
        self.assertIn('<li id="a-one"><a href="a-one/index.html">', page)
        self.assertIn('<li id="a-two"><a href="a-two/index.html">', page)
        self.assertIn('<span class="line">지수가 5.46% 내렸다.</span>', page)
        self.assertIn('<span class="line">두 번째 사실.</span>', page)
        for absent in ("둘째 문장", "의미 문장.", "조건 문장.", "https://example.com/a", "무슨 일이 있었나"):
            self.assertNotIn(absent, page)

    def test_featured_story_comes_first_in_its_section(self):
        day = make_day()
        items = day["sections"][0]["items"]
        del items[0]["featured"]
        items[1]["featured"] = True
        page = self.day_page(day)
        self.assertLess(page.index('id="a-two"'), page.index('id="a-one"'))

    def test_section_heading_is_the_link_to_its_category(self):
        page = self.day_page(self.two_sections())
        self.assertIn('<h2 id="h-economy"><a href="../../topic/economy/index.html">경제</a></h2>', page)
        self.assertIn('<h2 id="h-auto"><a href="../../topic/auto/index.html">자동차</a></h2>', page)

    def test_section_summary_points_and_counts_are_not_shown(self):
        page = self.day_page(make_day())
        for absent in ("한 줄 요약", "포인트 하나", "기사 2건", 'class="lede"', 'class="count"'):
            self.assertNotIn(absent, page)

    def test_sections_without_articles_are_not_shown(self):
        day = make_day()
        day["sections"].append({"id": "empty", "title": "빈분야", "items": []})
        page = self.day_page(day)
        self.assertNotIn("빈분야", page)
        self.assertNotIn('id="empty"', page)
        self.assertFalse(self.exists("topic/empty/index.html"))

    def test_quick_points_are_plain_text_not_links(self):
        page = self.day_page(make_day())
        quick = page[page.index('class="quick"'):page.index("</section>", page.index('class="quick"'))]
        self.assertIn("하락", quick)
        self.assertNotIn("<a ", quick)

    def test_day_pages_link_previous_and_next(self):
        self.write_day(make_day("2026-10-12"))
        self.write_day(make_day("2026-10-13"))
        self.build()
        self.assertIn('href="../2026-10-13/index.html"', self.read("news/2026-10-12/index.html"))
        self.assertIn('href="../2026-10-12/index.html"', self.read("news/2026-10-13/index.html"))

    # ---- 시장: 기준별로 묶은 한 곳

    def market_of(self, page):
        start = page.index('class="market"')
        return page[start:page.index("</section>", start)]

    def test_market_rows_are_grouped_under_their_basis(self):
        day = make_day()
        day["market"]["rows"] = [
            {"name": "가", "value": "1", "change": "▼ 1.00%", "dir": "down", "basis": "10/12 뉴욕 종가", "asof": "2026-10-12"},
            {"name": "나", "value": "2", "change": "▲ 1.00%", "dir": "up", "basis": "10/12 뉴욕 종가", "asof": "2026-10-12"},
            {"name": "다", "value": "3", "basis": "10/12 선물 결제", "asof": "2026-10-12"},
            {"name": "라", "value": "4"},
        ]
        market = self.market_of(self.day_page(day))
        self.assertEqual(market.count("10/12 뉴욕 종가"), 1)
        self.assertEqual(market.count("10/12 선물 결제"), 1)
        self.assertLess(market.index("10/12 뉴욕 종가"), market.index("<b>가</b>"))
        self.assertLess(market.index("<b>나</b>"), market.index("10/12 선물 결제"))
        self.assertLess(market.index("10/12 선물 결제"), market.index("<b>다</b>"))
        self.assertIn("<b>라</b>", market)

    def test_market_bars_are_scaled_to_the_largest_move(self):
        day = make_day()
        day["market"]["rows"] = [
            {"name": "가", "value": "100", "change": "▼ 2.00%", "dir": "down", "basis": "종가", "asof": day["date"]},
            {"name": "나", "value": "200", "change": "▲ 1.00%", "dir": "up", "basis": "종가", "asof": day["date"]},
            {"name": "다", "value": "300", "basis": "종가", "asof": day["date"]},
        ]
        market = self.market_of(self.day_page(day))
        self.assertIn('class="bar down" style="width:50.0%"', market)
        self.assertIn('class="bar up" style="width:25.0%"', market)
        self.assertEqual(market.count('class="track"'), 2)

    def test_market_has_no_bars_when_fewer_than_two_rows_move(self):
        market = self.market_of(self.day_page(make_day()))
        self.assertNotIn('class="track"', market)
        self.assertIn('<span class="v">6,035.35</span>', market)

    def test_market_forecast_is_listed_without_bars(self):
        day = make_day()
        day["market"]["forecast"] = {"title": "가상 전망", "rows": [
            {"name": "세계", "basis": "7월 전망 3.0%", "value": "3.1%", "change": "▲ 0.1%p", "dir": "up"}]}
        market = self.market_of(self.day_page(day))
        self.assertIn("가상 전망", market)
        self.assertIn("7월 전망 3.0%", market)
        forecast = market[market.index("가상 전망"):]
        self.assertNotIn('class="track"', forecast)

    # ---- 기사: 분야와 실린 브리핑, 제목, 본문, 출처, 이 브리핑의 다른 기사

    def test_each_article_has_its_own_page_with_the_full_text(self):
        page = self.article_page(make_day(), "a-one")
        self.assertIn('<h1 id="top-title">기사 하나</h1>', page)
        self.assertIn("둘째 문장 <strong>강조</strong>.", page)
        self.assertIn("의미 문장.", page)
        self.assertIn("조건 문장.", page)
        self.assertIn('href="https://example.com/a"', page)
        self.assertIn('rel="noopener"', page)
        self.assertIn("<title>기사 하나 | dogcowai 경제브리핑</title>", page)
        self.assertIn('<meta name="description" content="지수가 5.46% 내렸다.">', page)
        self.assertIn('<link rel="canonical" href="http://example.test/news/2026-10-13/a-one/">', page)
        self.assertNotIn("두 번째 사실.", page)

    def test_article_parts_are_labelled_and_missing_parts_are_omitted(self):
        self.write_day(make_day())
        self.build()
        first = self.read("news/2026-10-13/a-one/index.html")
        second = self.read("news/2026-10-13/a-two/index.html")
        for label in ("무슨 일이 있었나", "왜 중요한가", "다시 볼 조건", "출처"):
            self.assertIn(label, first)
        self.assertIn("두 번째 사실.", second)
        for label in ("무슨 일이 있었나", "왜 중요한가", "다시 볼 조건"):
            self.assertNotIn(label, second)

    def test_article_top_line_links_its_category_and_its_briefing(self):
        page = self.article_page(make_day(), "a-one")
        top = page[page.index('class="kicker"'):page.index("</p>", page.index('class="kicker"'))]
        self.assertIn('<a href="../../../topic/economy/index.html">경제</a>', top)
        self.assertIn('<a href="../index.html">10월 13일 화요일 오후판</a>', top)
        self.assertLess(page.index('class="kicker"'), page.index('<h1 id="top-title">'))
        stamps = page[page.index('class="checked"'):]
        self.assertIn("뉴스 재확인 17:02 KST", stamps[:stamps.index("</p>")])

    def test_article_page_lists_the_other_stories_of_its_briefing(self):
        page = self.article_page(self.two_sections(), "a-one")
        others = page[page.index('id="others-title"'):]
        self.assertIn("이 브리핑의 다른 기사", others)
        self.assertIn('href="../a-two/index.html"', others)
        self.assertIn('href="../c-one/index.html"', others)
        self.assertNotIn('href="../a-one/index.html"', others)

    def test_article_page_has_one_list_and_no_other_navigation_blocks(self):
        day = make_day(corrections=[{"date": "2026-10-14", "text": "수치를 바로잡았다."}])
        page = self.article_page(day, "a-one")
        for absent in ('class="pager"', 'class="rail"', "h-corrections", "기사 전체", "다른 경제 기사"):
            self.assertNotIn(absent, page)

    def test_article_page_without_other_stories_has_no_list(self):
        day = make_day()
        day["sections"][0]["items"].pop()
        day["quick"] = []
        self.assertNotIn("others-title", self.article_page(day, "a-one"))

    # ---- 확인·수정 시각

    def test_updated_at_is_shown_next_to_the_check_time(self):
        day = make_day(updated_at="2026-10-14T09:30:00+09:00")
        self.write_day(day)
        self.build()
        for rel in ("news/2026-10-13/index.html", "news/2026-10-13/a-one/index.html"):
            self.assertIn("<span>수정 10월 14일 09:30 KST</span>", self.read(rel), rel)

    def test_updated_at_in_another_time_zone_is_shown_in_korean_time(self):
        self.assertIn("<span>수정 10월 14일 09:30 KST</span>",
                      self.day_page(make_day(updated_at="2026-10-14T00:30:00Z")))

    def test_updated_at_with_only_a_date_shows_only_the_date(self):
        page = self.day_page(make_day(updated_at="2026-10-14"))
        self.assertIn("<span>수정 10월 14일</span>", page)
        self.assertNotIn("00:00", page)

    def test_pages_without_updated_at_show_no_modified_marks(self):
        self.write_day(make_day())
        self.build()
        for rel in ("news/2026-10-13/index.html", "news/2026-10-13/a-one/index.html"):
            page = self.read(rel)
            self.assertNotIn("<span>수정 ", page, rel)
            self.assertNotIn("article:modified_time", page, rel)
            self.assertNotIn("dateModified", page, rel)

    # ---- 목록: 날짜별, 분야, 분야별 기사

    def test_archive_groups_days_by_month(self):
        self.write_day(make_day("2026-09-30"))
        self.write_day(make_day("2026-10-13"))
        self.build()
        archive = self.read("news/index.html")
        self.assertLess(archive.index("2026년 10월"), archive.index("2026년 9월"))

    def test_archive_rows_show_the_day_and_its_headline_only(self):
        self.write_day(make_day())
        self.build()
        archive = self.read("news/index.html")
        self.assertIn('<a href="2026-10-13/index.html"><span class="meta">10월 13일 화요일 오후판</span>'
                      '<span class="t">2026-10-13 헤드라인</span></a>', archive)
        self.assertNotIn("요약 문장입니다.", archive)
        self.assertNotIn("기사 2건", archive)

    def test_topic_index_rows_show_the_category_and_its_article_count(self):
        self.write_day(make_day("2026-10-12"))
        self.write_day(make_day("2026-10-13"))
        self.build()
        topics = self.read("topic/index.html")
        self.assertIn('<h1 id="top-title">분야별 기사</h1>', topics)
        self.assertIn('<a href="economy/index.html"><span class="t">경제</span><span class="line">기사 4건</span></a>',
                      topics)
        self.assertNotIn("기사 하나", topics)

    def test_topic_page_collects_articles_across_days_newest_first(self):
        self.write_day(make_day("2026-10-12"))
        self.write_day(make_day("2026-10-13"))
        self.build()
        topic = self.read("topic/economy/index.html")
        self.assertIn('<h1 id="top-title">경제 기사</h1>', topic)
        new = topic.index("../../news/2026-10-13/a-one/index.html")
        old = topic.index("../../news/2026-10-12/a-one/index.html")
        self.assertLess(new, old)
        self.assertIn("/topic/economy/", self.read("sitemap.xml"))

    def test_lists_use_the_teaser_when_an_article_has_one(self):
        day = make_day()
        day["sections"][0]["items"][0]["teaser"] = "지수가 5.46% 내린 날 <b>&"
        self.write_day(day)
        self.build()
        for rel in ("index.html", "news/2026-10-13/index.html", "topic/economy/index.html"):
            page = self.read(rel)
            self.assertIn('<span class="line">지수가 5.46% 내린 날 &lt;b&gt;&amp;</span>', page, rel)
            self.assertNotIn('<span class="line">지수가 5.46% 내렸다.</span>', page, rel)

    def test_lists_fall_back_to_the_first_sentence_without_a_teaser(self):
        page = self.day_page(make_day())
        self.assertIn('<span class="line">지수가 5.46% 내렸다.</span>', page)

    def test_teaser_does_not_replace_the_article_text(self):
        day = make_day()
        day["sections"][0]["items"][0]["teaser"] = "목록에만 쓰는 문장"
        page = self.article_page(day, "a-one")
        self.assertIn("둘째 문장 <strong>강조</strong>.", page)
        self.assertNotIn("목록에만 쓰는 문장", page)

    # ---- 고정 페이지, 404

    def test_static_page_is_built_and_linked(self):
        self.write_page({"slug": "about", "title": "소개", "description": "설명",
                         "sections": [{"heading": "무엇인가", "paragraphs": ["문단 <b>"], "items": ["항목"]}]})
        self.write_day(make_day())
        self.build()
        about = self.read("about/index.html")
        self.assertIn("무엇인가", about)
        self.assertIn("문단 &lt;b&gt;", about)
        self.assertIn('href="about/index.html"', self.read("index.html"))

    def test_404_uses_root_absolute_links_and_is_not_indexed(self):
        self.write_day(make_day())
        self.build()
        page = self.read("404.html")
        self.assertIn('href="/assets/style.css"', page)
        self.assertIn('href="/index.html"', page)
        self.assertIn('content="noindex"', page)

    # ---- 쓰기와 정리

    def test_check_only_writes_nothing(self):
        self.write_day(make_day())
        pages = self.build(check_only=True)
        self.assertIn("index.html", pages)
        self.assertFalse(self.exists("index.html"))

    def test_removes_stale_day_pages_with_their_article_pages(self):
        self.write_day(make_day("2026-10-12"))
        self.write_day(make_day("2026-10-13"))
        self.build()
        (self.root / "content" / "2026-10-12.json").unlink()
        self.build()
        self.assertFalse(self.exists("news/2026-10-12"))
        self.assertTrue(self.exists("news/2026-10-13/a-one/index.html"))

    def test_removes_the_page_of_an_article_that_was_dropped(self):
        day = make_day()
        self.write_day(day)
        self.build()
        day["sections"][0]["items"].pop()
        self.write_day(day)
        self.build()
        self.assertFalse(self.exists("news/2026-10-13/a-two"))
        self.assertTrue(self.exists("news/2026-10-13/a-one/index.html"))

    def test_cleanup_leaves_folders_holding_files_it_did_not_write(self):
        self.write_day(make_day("2026-10-12"))
        self.write_day(make_day("2026-10-13"))
        self.build()
        (self.root / "news" / "2026-10-12" / "a-one" / "note.txt").write_text("남긴 파일", encoding="utf-8")
        (self.root / "content" / "2026-10-12.json").unlink()
        self.build()
        self.assertTrue(self.exists("news/2026-10-12/a-one/note.txt"))
        self.assertFalse(self.exists("news/2026-10-12/a-two"))

    def test_building_twice_gives_identical_files(self):
        self.write_day(make_day())
        self.build()
        first = self.snapshot()
        self.build()
        self.assertEqual(self.snapshot(), first)


class SeoTests(SiteCase):
    def test_pages_have_canonical_and_open_graph(self):
        page = self.day_page(make_day())
        self.assertIn('<link rel="canonical" href="http://example.test/news/2026-10-13/">', page)
        self.assertIn('property="og:url" content="http://example.test/news/2026-10-13/"', page)
        self.assertIn('property="og:title"', page)
        self.assertIn('<link rel="canonical" href="http://example.test/">', self.read("index.html"))

    def test_sitemap_lists_exactly_the_public_pages(self):
        self.write_day(make_day("2026-10-12"))
        self.write_day(make_day("2026-10-13"))
        pages = self.build()
        locs = set(re.findall(r"<loc>([^<]+)</loc>", self.read("sitemap.xml")))
        expected = {"http://example.test/" + rel[:-len("index.html")]
                    for rel in pages if rel.endswith("index.html")}
        self.assertEqual(locs, expected)
        self.assertNotIn("404", self.read("sitemap.xml"))
        self.assertIn("Sitemap: http://example.test/sitemap.xml", self.read("robots.txt"))

    def ld(self, html):
        blocks = re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)
        return [json.loads(b) for b in blocks]

    def test_article_page_describes_itself_as_a_news_article(self):
        page = self.article_page(make_day(), "a-one")
        data = {d["@type"]: d for d in self.ld(page)}
        art = data["NewsArticle"]
        self.assertEqual(art["headline"], "기사 하나")
        self.assertEqual(art["url"], "http://example.test/news/2026-10-13/a-one/")
        self.assertEqual(art["mainEntityOfPage"], art["url"])
        self.assertEqual(art["datePublished"], "2026-10-13T17:02:00+09:00")
        self.assertEqual(art["articleSection"], "경제")
        self.assertEqual(art["description"], "지수가 5.46% 내렸다.")
        self.assertEqual(art["inLanguage"], "ko-KR")
        self.assertEqual(art["publisher"]["name"], "dogcowai 경제브리핑")
        self.assertEqual([c["url"] for c in art["citation"]], ["https://example.com/a"])
        self.assertIn('<meta property="article:published_time" content="2026-10-13T17:02:00+09:00">', page)

    def test_breadcrumb_data_lists_the_path_to_the_page(self):
        page = self.article_page(make_day(), "a-one")
        crumbs = {d["@type"]: d for d in self.ld(page)}["BreadcrumbList"]["itemListElement"]
        self.assertEqual([c["name"] for c in crumbs], ["홈", "날짜별", "10월 13일 화요일", "기사 하나"])
        self.assertEqual([c["item"] for c in crumbs], [
            "http://example.test/", "http://example.test/news/", "http://example.test/news/2026-10-13/",
            "http://example.test/news/2026-10-13/a-one/"])
        self.assertEqual([c["position"] for c in crumbs], [1, 2, 3, 4])

    def test_home_describes_the_site(self):
        self.write_day(make_day())
        self.build()
        data = self.ld(self.read("index.html"))
        self.assertEqual(data[0]["@type"], "WebSite")
        self.assertEqual(data[0]["url"], "http://example.test/")
        self.assertEqual(data[0]["name"], "dogcowai 경제브리핑")

    def test_structured_data_cannot_break_out_of_its_script(self):
        day = make_day()
        day["sections"][0]["items"][0]["title"] = "기사 </script><b>하나"
        page = self.article_page(day, "a-one")
        block = re.findall(r'<script type="application/ld\+json">(.*?)</script>', page, re.S)
        self.assertTrue(block)
        self.assertNotIn("</script", "".join(block))
        self.assertEqual({d["@type"]: d for d in self.ld(page)}["NewsArticle"]["headline"], "기사 </script><b>하나")

    def test_updated_at_reaches_metadata_and_the_feed(self):
        self.write_day(make_day("2026-10-12", updated_at="2026-10-14T09:30:00+09:00"))
        self.write_day(make_day("2026-10-13"))
        self.build()
        page = self.read("news/2026-10-12/a-one/index.html")
        self.assertIn('<meta property="article:modified_time" content="2026-10-14T09:30:00+09:00">', page)
        self.assertEqual({d["@type"]: d for d in self.ld(page)}["NewsArticle"]["dateModified"],
                         "2026-10-14T09:30:00+09:00")
        feed = self.read("feed.xml")
        entry = feed[feed.index("<entry><title>2026-10-12"):]
        self.assertIn("<updated>2026-10-14T09:30:00+09:00</updated>", entry[:entry.index("</entry>")])
        head = feed[:feed.index("<entry>")]
        self.assertIn("<updated>2026-10-14T09:30:00+09:00</updated>", head)

    def test_feed_does_not_invent_a_time_when_only_the_update_date_is_known(self):
        self.write_day(make_day(updated_at="2026-10-14"))
        self.build()
        feed = self.read("feed.xml")
        self.assertNotIn("T00:00:00", feed)
        entry = feed[feed.index("<entry>"):feed.index("</entry>")]
        self.assertIn("<updated>2026-10-13T17:02:00+09:00</updated>", entry)
        self.assertIn("<summary>요약 문장입니다. (수정 10월 14일, 정확한 시각 미상)</summary>", entry)

    def test_feed_summary_is_plain_when_the_update_time_is_known(self):
        self.write_day(make_day(updated_at="2026-10-14T09:30:00+09:00"))
        self.build()
        feed = self.read("feed.xml")
        self.assertIn("<summary>요약 문장입니다.</summary>", feed)
        self.assertNotIn("시각 미상", feed)

    def test_feed_has_one_entry_per_day(self):
        self.write_day(make_day("2026-10-12"))
        self.write_day(make_day("2026-10-13"))
        self.build()
        feed = self.read("feed.xml")
        self.assertEqual(feed.count("<entry>"), 2)
        self.assertIn("http://example.test/news/2026-10-13/", feed)


class ComponentTests(SiteCase):
    def test_market_panel_shows_basis_and_sources(self):
        page = self.day_page(make_day())
        self.assertIn('<span class="v">6,035.35</span>', page)
        self.assertIn('<span class="c down">▼ 5.46%</span>', page)
        self.assertIn("종가", page)
        self.assertIn('href="https://example.com/market"', page)

    def test_schedule_is_shown_in_korean_time_with_as_of_note(self):
        day = make_day(side={"schedule": [{"at": "2026-10-13T08:30:00-04:00", "title": "가상 발표", "desc": "확인할 것"}]})
        page = self.day_page(day)
        self.assertIn("10/13 21:30", page)
        self.assertIn("17:02 KST 기준", page)

    def test_corrections_are_listed(self):
        page = self.day_page(make_day(corrections=[{"date": "2026-10-14", "text": "수치를 바로잡았다."}]))
        self.assertIn("정정·수정 내역", page)
        self.assertIn("수치를 바로잡았다.", page)

    def test_page_without_optional_blocks_has_no_empty_blocks(self):
        day = make_day()
        for key in ("market", "quick"):
            del day[key]
        page = self.day_page(day)
        for absent in ("시장 요약", "핵심 1분", 'class="market"', 'class="side"', 'class="rail"', "<ul></ul>",
                       "정정·수정 내역"):
            self.assertNotIn(absent, page)

    def test_anchor_ids_are_unique_in_a_page(self):
        self.write_day(make_day(side={"schedule": [{"at": "2026-10-13T08:30:00-04:00", "title": "가상 발표"}]},
                                corrections=[{"date": "2026-10-14", "text": "수치를 바로잡았다."}]))
        self.build()
        for rel in ("index.html", "news/2026-10-13/index.html", "news/2026-10-13/a-one/index.html"):
            ids = re.findall(r'\sid="([^"]+)"', self.read(rel))
            self.assertEqual(len(ids), len(set(ids)), rel)


class ContentRuleTests(SiteCase):
    def test_article_without_sources_is_rejected(self):
        day = make_day()
        del day["sections"][0]["items"][1]["sources"]
        self.assert_rejected(day, "sources", "a-two")

    def test_legacy_days_before_the_cutoff_may_lack_sources(self):
        day = make_day("2026-09-30")
        del day["sections"][0]["items"][1]["sources"]
        self.write_day(day)
        self.build()
        self.assertTrue(self.exists("news/2026-09-30/index.html"))

    def test_duplicate_source_url_in_one_article_is_rejected(self):
        day = make_day()
        day["sections"][0]["items"][0]["sources"].append({"name": "복사", "url": "https://example.com/a"})
        self.assert_rejected(day, "중복")

    def test_article_needs_a_unique_id(self):
        day = make_day()
        day["sections"][0]["items"][1]["id"] = "a-one"
        self.assert_rejected(day, "a-one")
        day = make_day()
        del day["sections"][0]["items"][1]["id"]
        self.assert_rejected(day, "id")

    def test_article_date_cannot_be_after_the_briefing_date(self):
        day = make_day()
        day["sections"][0]["items"][0]["date"] = "2026-10-14"
        self.assert_rejected(day, "a-one")

    def test_quick_point_must_reference_an_existing_article(self):
        self.assert_rejected(make_day(quick=[{"text": "문장", "ref": "nope"}]), "quick[0]")
        self.assert_rejected(make_day(quick=["연결 없는 문장"]), "quick[0]")

    def test_quick_point_numbers_must_appear_in_the_article(self):
        self.assert_rejected(make_day(quick=[{"text": "지수 9.99% 하락", "ref": "a-one"}]), "quick[0]", "9.99%")

    def test_teaser_numbers_must_appear_in_the_article(self):
        day = make_day()
        day["sections"][0]["items"][0]["teaser"] = "지수가 9.99% 내렸다"
        self.assert_rejected(day, "a-one", "teaser", "9.99%")

    def test_teaser_may_use_numbers_from_any_part_of_the_article(self):
        day = make_day()
        item = day["sections"][0]["items"][0]
        item["recheck"]["text"] = "15일 발표를 확인한다."
        item["teaser"] = "15일 발표가 남았다"
        self.day_page(day)

    def test_teaser_must_be_a_non_empty_text(self):
        for bad in ("", "   ", 7, ["문장"]):
            day = make_day()
            day["sections"][0]["items"][0]["teaser"] = bad
            self.assert_rejected(day, "teaser")

    def test_updated_at_needs_a_time_zone_or_only_a_date(self):
        for bad in ("2026-10-14T09:30:00", "어제", "2026-13-01", 20261014):
            self.assert_rejected(make_day(updated_at=bad), "updated_at")

    def test_updated_at_cannot_be_before_the_briefing(self):
        self.assert_rejected(make_day(updated_at="2026-10-13T09:00:00+09:00"), "updated_at")
        self.assert_rejected(make_day(updated_at="2026-10-12"), "updated_at")

    def test_checked_at_must_be_a_time(self):
        self.assert_rejected(make_day(checked_at="오후쯤"), "checked_at")

    def test_schedule_entry_already_in_the_past_is_rejected(self):
        day = make_day(side={"schedule": [{"at": "2026-10-13T09:00:00+09:00", "title": "지난 일정"}]})
        self.assert_rejected(day, "schedule[0]")

    def test_market_row_date_cannot_be_in_the_future_or_stale(self):
        day = make_day()
        day["market"]["rows"][0]["asof"] = "2026-10-14"
        self.assert_rejected(day, "market.rows[0]")
        day = make_day()
        day["market"]["rows"][0]["asof"] = "2026-10-01"
        self.assert_rejected(day, "market.rows[0]")

    def test_broken_json_is_reported_without_writing(self):
        before = self.snapshot()
        (self.root / "content" / "2026-10-13.json").write_text("{ 깨진", encoding="utf-8")
        with self.assertRaises(build.BuildError) as ctx:
            self.build()
        self.assertIn("2026-10-13.json", str(ctx.exception))
        self.assertEqual(self.snapshot(), before)

    def test_missing_required_field_fails(self):
        day = make_day()
        del day["headline"]
        self.assert_rejected(day, "headline")

    def test_rejects_non_http_source_url(self):
        day = make_day()
        day["sections"][0]["items"][0]["sources"][0]["url"] = "javascript:alert(1)"
        self.assert_rejected(day)

    def test_filename_must_match_date_field(self):
        self.write_day(make_day("2026-10-13"), name="2026-10-14")
        with self.assertRaises(build.BuildError):
            self.build()

    def test_duplicate_section_ids_fail(self):
        day = make_day()
        dup = copy.deepcopy(day["sections"][0])
        for i, it in enumerate(dup["items"]):
            it["id"] = "x-%d" % i
        day["sections"].append(dup)
        self.assert_rejected(day, "economy")


class PublicInputTests(SiteCase):
    """공개 저장소의 입력에는 개인 전용 내용을 둘 수 없다."""

    def test_actions_block_is_rejected(self):
        day = make_day()
        day["actions"] = ["가상 행동 문장"]
        self.assert_rejected(day, "actions", "2026-10-13.json")

    def test_career_block_is_rejected(self):
        self.assert_rejected(make_day(side={"career": [{"title": "가상 회사"}]}), "career")

    def test_private_flag_is_rejected_at_any_depth(self):
        day = make_day()
        day["sections"][0]["items"][1]["private"] = False
        self.assert_rejected(day, "private")

    def test_rejection_leaves_previously_generated_files_unchanged(self):
        self.write_day(make_day())
        self.build()
        day = make_day()
        day["sections"][0]["items"][0]["private"] = True
        self.assert_rejected(day)
        self.assertTrue(self.exists("index.html"))

    def test_blocklist_stops_build_and_writes_nothing(self):
        day = make_day()
        day["sections"][0]["items"][0]["facts"] = "샘플 매입가 5억원"
        day["quick"] = []
        self.assert_rejected(day, "공개 규칙 1번")
        self.assertFalse(self.exists("index.html"))

    def test_blocklist_scans_fields_that_are_not_rendered(self):
        day = make_day()
        day["memo"] = "렌더링되지 않는 필드의 매입가"
        self.assert_rejected(day, "memo", "공개 규칙 1번")

    def test_blocklist_scans_the_teaser(self):
        day = make_day()
        day["sections"][0]["items"][1]["teaser"] = "목록 문장 속 매입가"
        self.assert_rejected(day, "items[1].teaser", "공개 규칙 1번")

    def test_blocklist_scans_attribute_values(self):
        day = make_day()
        day["sections"][0]["items"][0]["sources"][0]["url"] = "https://example.com/J-12345678"
        self.assert_rejected(day, "sources[0].url", "공개 규칙 2번")

    def test_blocklist_scans_site_settings_but_not_its_own_definition(self):
        self.write_day(make_day())
        self.build()
        self.write_site(dict(SITE, description="설명에 매입가가 들어감"))
        with self.assertRaises(build.BuildError) as ctx:
            self.build()
        self.assertIn("_site.json", str(ctx.exception))

    def test_error_message_names_file_field_and_rule_but_not_the_text(self):
        day = make_day()
        day["sections"][0]["items"][0]["facts"] = "앞말 매입가 비밀문장XYZ 뒷말"
        day["quick"] = []
        self.write_day(day)
        with self.assertRaises(build.BuildError) as ctx:
            self.build()
        message = str(ctx.exception)
        self.assertIn("sections[0].items[0].facts", message)
        self.assertNotIn("비밀문장XYZ", message)

    def test_local_only_blocklist_file_is_applied(self):
        (self.root / "보관").mkdir()
        (self.root / "보관" / "blocklist.local.txt").write_text("# 주석\n가상비밀어\n", encoding="utf-8")
        day = make_day()
        day["sections"][0]["items"][1]["facts"] = "문장 속의 가상비밀어"
        self.assert_rejected(day, "로컬 규칙 2번째 줄")

    # ---- 규칙의 원문은 그 자체가 비공개일 수 있다. 오류와 로그에 다시 적지 않는다.

    def local_rules(self, text):
        (self.root / "보관").mkdir(exist_ok=True)
        (self.root / "보관" / "blocklist.local.txt").write_text(text, encoding="utf-8")

    def run_main(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = build.main(["--root", str(self.root)])
        return code, out.getvalue() + err.getvalue()

    def test_input_match_reports_the_rule_by_name_not_by_its_text(self):
        self.local_rules("가상표식A\\d+\n")
        day = make_day()
        day["sections"][0]["items"][1]["facts"] = "문장 속의 가상표식A77"
        before = self.snapshot()
        self.write_day(day)
        code, printed = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn("로컬 규칙 1번째 줄", printed)
        self.assertIn("sections[0].items[1].facts", printed)
        self.assertNotIn("가상표식A", printed)
        self.assertEqual(self.snapshot(), before)

    def test_public_rule_text_is_not_printed_either(self):
        day = make_day()
        day["memo"] = "렌더링되지 않는 필드의 매입가"
        self.write_day(day)
        code, printed = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn("공개 규칙 1번", printed)
        self.assertNotIn("매입가", printed)

    def test_broken_rule_is_reported_without_its_text(self):
        self.local_rules("# 주석\n가상표식B[\n")
        self.write_day(make_day())
        code, printed = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn("로컬 규칙 2번째 줄", printed)
        self.assertNotIn("가상표식B", printed)
        self.assertFalse(self.exists("index.html"))

    def test_local_rules_reached_through_a_link_are_applied(self):
        target = self.root / "rules-elsewhere.txt"
        target.write_text("가상표식F\n", encoding="utf-8")
        (self.root / "보관").mkdir(exist_ok=True)
        (self.root / "보관" / "blocklist.local.txt").symlink_to(target)
        day = make_day()
        day["summary"] = "요약 속의 가상표식F"
        self.assert_rejected(day, "로컬 규칙 1번째 줄")

    def test_broken_link_to_local_rules_stops_the_build(self):
        """규칙 파일이 다른 곳을 가리키는데 그 파일이 없으면, 규칙 없이 조용히 지나가지 않고 멈춘다."""
        (self.root / "보관").mkdir(exist_ok=True)
        (self.root / "보관" / "blocklist.local.txt").symlink_to(self.root / "없는-파일.txt")
        self.write_day(make_day())
        with self.assertRaises(build.BuildError) as ctx:
            self.build()
        self.assertIn("로컬 금지 규칙", str(ctx.exception))
        self.assertFalse(self.exists("index.html"))

    def test_broken_rule_message_never_carries_the_rule_text(self):
        """정규식 오류 설명(re.error.msg)에도 규칙 일부가 들어간다. 규칙 번호와 고정된 말만 내보내야 한다."""
        broken = ("(?P=가상표식X)", "(?P<가상표식Y>a)(?P<가상표식Y>b)", "(?P<가상-표식Z>a)")
        self.write_day(make_day())
        self.build()

        def generated():
            return {k: v for k, v in self.snapshot().items() if not k.startswith("보관")}
        before = generated()
        for rule in broken:
            self.local_rules("# 주석\n" + rule + "\n")
            code, printed = self.run_main()
            self.assertEqual(code, 1, rule)
            self.assertIn("로컬 규칙 2번째 줄", printed)
            self.assertIn("정규식 문법 오류", printed)
            self.assertNotIn("가상", printed, rule)
            self.assertNotIn("group", printed, rule)
            with self.assertRaises(build.BuildError) as ctx:
                self.build()
            err = ctx.exception
            self.assertIsNone(err.__context__, rule)
            self.assertIsNone(err.__cause__, rule)
            self.assertNotIn("가상", repr(err) + str(err.args), rule)
            self.assertEqual(generated(), before, rule)

    def test_normal_rules_still_work_after_a_broken_rule_is_fixed(self):
        self.local_rules("(?P<가상그룹>a\n")
        self.write_day(make_day())
        with self.assertRaises(build.BuildError):
            self.build()
        self.local_rules("가상표식E\n")
        day = make_day()
        day["summary"] = "요약 속의 가상표식E"
        self.assert_rejected(day, "로컬 규칙 1번째 줄")

    def test_error_object_does_not_carry_the_rule_text(self):
        self.local_rules("가상표식C\n")
        day = make_day()
        day["summary"] = "요약 속의 가상표식C"
        self.write_day(day)
        with self.assertRaises(build.BuildError) as ctx:
            self.build()
        err = ctx.exception
        self.assertNotIn("가상표식C", repr(err) + str(err.args) + repr(err.__cause__) + repr(err.__context__))

    def test_generated_page_match_reports_the_rule_by_name(self):
        problems = None
        rules = [("로컬 규칙 1번째 줄", re.compile("가상표식D"))]
        with self.assertRaises(build.BuildError) as ctx:
            build.check_pages({"index.html": "<p>가상표식D</p>"}, rules)
        self.assertIn("index.html", str(ctx.exception))
        self.assertIn("로컬 규칙 1번째 줄", str(ctx.exception))
        self.assertNotIn("가상표식D", str(ctx.exception))


if __name__ == "__main__":
    unittest.main(verbosity=2)
