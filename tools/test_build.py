"""tools/build.py 테스트. 실행: python3 tools/test_build.py

테스트에 쓰는 이름·문장은 모두 가상의 표식이다. 실제 개인 정보를 넣지 않는다.
"""
import contextlib
import copy
import hashlib
import io
import json
import re
import shutil
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
    """페이지 구성. 페이지는 네 가지다: 홈(소개 + 최신 브리핑), 브리핑, 기사, 날짜별 브리핑(날마다 쌓음).
    분야별·소개 페이지는 2026-10-24에 없앴다."""

    PAGES = ("index.html", "news/index.html", "news/2026-10-13/index.html", "news/2026-10-13/a-one/index.html")

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
        for gone in ("topic", "about"):
            self.assertFalse(self.exists(gone), gone)

    def test_removes_retired_topic_and_about_folders_it_wrote(self):
        for rel in ("topic/index.html", "topic/economy/index.html", "about/index.html"):
            path = self.root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("예전 생성 파일", encoding="utf-8")
        self.write_day(make_day())
        self.build()
        self.assertFalse(self.exists("topic"))
        self.assertFalse(self.exists("about"))

    def test_keeps_a_retired_folder_holding_other_files(self):
        (self.root / "topic").mkdir()
        (self.root / "topic" / "note.txt").write_text("남긴 파일", encoding="utf-8")
        self.write_day(make_day())
        self.build()
        self.assertTrue(self.exists("topic/note.txt"))

    def test_no_page_links_to_the_removed_topic_or_about_pages(self):
        self.write_day(self.two_sections())
        self.build()
        for rel in self.PAGES + ("404.html", "sitemap.xml", "feed.xml"):
            html = self.read(rel)
            for absent in ("topic/", "about/", ">분야별<", ">소개<", "편집 기준"):
                self.assertNotIn(absent, html, (rel, absent))

    # ---- 모든 페이지에 공통

    def test_menu_marks_the_page_the_reader_is_on(self):
        self.write_day(make_day())
        self.build()
        home, archive = self.read("index.html"), self.read("news/index.html")
        self.assertIn('href="index.html" aria-current="page">홈<', home)
        self.assertNotIn('aria-current="page">날짜별<', home)
        self.assertIn('href="../news/index.html" aria-current="page">날짜별<', archive)
        self.assertIn('aria-current="page">날짜별<', self.read("news/2026-10-13/index.html"))
        self.assertIn('aria-current="page">날짜별<', self.read("news/2026-10-13/a-one/index.html"))

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

    def test_home_shows_a_one_line_tagline_then_the_latest_briefing(self):
        self.write_site(dict(SITE, tagline="가상 한 줄 소개"))
        self.write_day(make_day())
        self.build()
        home = self.read("index.html")
        self.assertIn('<p class="tagline">가상 한 줄 소개</p>', home)
        self.assertLess(home.index('class="tagline"'), home.index('<h1 id="top-title">2026-10-13 헤드라인</h1>'))
        self.assertNotIn("가상 한 줄 소개", self.read("news/2026-10-13/index.html"))
        self.assertNotIn("편집 기준", home)

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

    # ---- 브리핑: 헤드라인, 핵심 1분, 분야별 기사(내용 전부), 시장, 일정, 정정, 이전·다음

    def test_briefing_reads_in_one_fixed_order(self):
        self.write_day(make_day("2026-10-12"))
        day = self.two_sections()
        day.update(corrections=[{"date": "2026-10-14", "text": "수치를 바로잡았다."}],
                   side={"schedule": [{"at": "2026-10-13T08:30:00-04:00", "title": "가상 발표"}]})
        page = self.day_page(day)
        order = [page.index(s) for s in (
            'class="dateline"', "뉴스 확인 17:02 KST", '<h1 id="top-title">', 'class="summary"',
            'id="quick-title"', 'id="market-title"', 'id="h-sched"', 'class="jump"', 'id="economy"', 'id="auto"',
            'id="h-corrections"', 'class="pager"')]
        self.assertEqual(order, sorted(order))
        self.assertIn("2026년 10월 13일 화요일 오후판", page)

    def test_briefing_shows_every_story_in_full_without_clicking(self):
        page = self.day_page(make_day())
        for text in ("지수가 5.46% 내렸다. 둘째 문장 <strong>강조</strong>.", "의미 문장.", "조건 문장.",
                     "두 번째 사실.", 'href="https://example.com/a"', 'href="https://example.com/b"'):
            self.assertIn(text, page)
        self.assertIn('<article class="item" id="a-one"><h3><a href="a-one/index.html">기사 하나</a></h3>', page)
        self.assertIn('<article class="item" id="a-two"><h3><a href="a-two/index.html">기사 둘</a></h3>', page)
        self.assertNotIn('<span class="line">', page)

    def test_briefing_story_parts_sit_under_the_story_title(self):
        page = self.day_page(make_day())
        one = page[page.index('id="a-one"'):page.index("</article>", page.index('id="a-one"'))]
        for label in ("<h4>무슨 일이 있었나</h4>", "<h4>왜 중요한가</h4>", "<h4>다시 볼 조건</h4>"):
            self.assertIn(label, one)
        two = page[page.index('id="a-two"'):page.index("</article>", page.index('id="a-two"'))]
        self.assertNotIn("<h4>", two)
        self.assertIn("두 번째 사실.", two)

    def test_home_shows_the_latest_briefing_in_full(self):
        self.write_day(make_day())
        self.build()
        home = self.read("index.html")
        for text in ("의미 문장.", "조건 문장.", "두 번째 사실.",
                     '<h3><a href="news/2026-10-13/a-one/index.html">기사 하나</a></h3>'):
            self.assertIn(text, home)

    def test_briefing_does_not_repeat_the_teaser_above_the_full_text(self):
        day = make_day()
        day["sections"][0]["items"][0]["teaser"] = "지수가 5.46% 내린 날의 가상 요약"
        self.write_day(day)
        self.build()
        for rel in ("index.html", "news/2026-10-13/index.html"):
            self.assertNotIn("가상 요약", self.read(rel), rel)

    def test_featured_story_comes_first_in_its_section(self):
        day = make_day()
        items = day["sections"][0]["items"]
        del items[0]["featured"]
        items[1]["featured"] = True
        page = self.day_page(day)
        self.assertLess(page.index('id="a-two"'), page.index('id="a-one"'))

    def test_section_heading_is_plain_text(self):
        page = self.day_page(self.two_sections())
        self.assertIn('<h2 id="h-economy">경제</h2>', page)
        self.assertIn('<h2 id="h-auto">자동차</h2>', page)

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

    def test_quick_lines_link_to_their_articles_and_the_first_three_stand_out(self):
        day = make_day()
        day["quick"] = [{"text": "지수 {{down:-5.46%}} 하락", "ref": "a-one"},
                        {"text": "두 번째 줄", "ref": "a-two"},
                        {"text": "세 번째 줄", "ref": "a-one"},
                        {"text": "네 번째 줄", "ref": "a-two"}]
        page = self.day_page(day)
        quick = page[page.index('class="quick"'):page.index("</section>", page.index('class="quick"'))]
        self.assertIn('<li class="top"><a href="#a-one">지수 <strong class="down">-5.46%</strong> 하락</a></li>', quick)
        self.assertEqual(quick.count('class="top"'), 3)
        self.assertIn('<li><a href="#a-two">네 번째 줄</a></li>', quick)
        self.assertIn('id="a-two"', page)

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
        self.assertLess(market.index("10/12 뉴욕 종가"), market.index('<span class="n">가</span>'))
        self.assertLess(market.index('<span class="n">나</span>'), market.index("10/12 선물 결제"))
        self.assertLess(market.index("10/12 선물 결제"), market.index('<span class="n">다</span>'))
        self.assertIn('<span class="n">라</span>', market)

    def test_market_draws_no_bars_and_links_each_row_source(self):
        day = make_day()
        day["market"]["rows"] = [
            {"name": "가", "value": "100", "change": "▼ 2.00%", "dir": "down", "basis": "종가", "asof": day["date"],
             "source": {"name": "가상시세", "url": "https://example.com/row-a"}},
            {"name": "나", "value": "200", "change": "▲ 30.00", "dir": "up", "basis": "종가", "asof": day["date"]},
        ]
        market = self.market_of(self.day_page(day))
        self.assertNotIn('class="track"', market)
        self.assertNotIn('class="bar', market)
        row = market[market.index('<span class="n">가</span>'):market.index('<span class="n">나</span>')]
        self.assertIn('href="https://example.com/row-a"', row)
        self.assertIn("가상시세", row)

    def test_row_sources_are_required_from_the_set_date(self):
        self.write_site(dict(SITE, v2_from="2026-10-13"))
        self.assert_rejected(make_day(lead_ref="a-one"), "market.rows[0]", "source")
        day = make_day(lead_ref="a-one")
        day["market"]["rows"][0]["source"] = {"name": "가상시세", "url": "https://example.com/row"}
        day["side"] = {"schedule": [{"at": "2026-10-14T08:30:00+09:00", "title": "가상 발표"}]}
        self.assert_rejected(day, "side.schedule[0]", "source")
        day["side"]["schedule"][0]["source"] = {"name": "가상기관 일정", "url": "https://example.com/cal"}
        page = self.day_page(day)
        sched = page[page.index('id="h-sched"'):]
        self.assertIn('href="https://example.com/cal"', sched[:sched.index("</section>")])
        self.write_day(make_day("2026-10-12"))
        self.build()  # 기준일 이전 판은 행별 출처 없이도 된다

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

    def test_article_top_line_names_its_category_and_links_its_briefing(self):
        page = self.article_page(make_day(), "a-one")
        top = page[page.index('class="kicker"'):page.index("</p>", page.index('class="kicker"'))]
        self.assertIn('<span>경제</span>', top)
        self.assertNotIn("topic/", top)
        self.assertIn('<a href="../index.html">10월 13일 화요일 오후판</a>', top)
        self.assertLess(page.index('class="kicker"'), page.index('<h1 id="top-title">'))
        stamps = page[page.index('class="checked"'):]
        self.assertIn("뉴스 확인 17:02 KST", stamps[:stamps.index("</p>")])

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

    # ---- 날짜별 브리핑: 날마다의 브리핑을 최신순으로 쌓는다

    def test_archive_stacks_days_newest_first(self):
        self.write_day(make_day("2026-09-30"))
        self.write_day(make_day("2026-10-13"))
        self.build()
        archive = self.read("news/index.html")
        self.assertLess(archive.index('id="day-2026-10-13"'), archive.index('id="day-2026-09-30"'))
        self.assertIn('<h1 id="top-title">날짜별 브리핑</h1>', archive)

    def test_archive_day_shows_headline_summary_and_its_article_rows(self):
        self.write_day(make_day())
        self.build()
        archive = self.read("news/index.html")
        day = archive[archive.index('id="day-2026-10-13"'):]
        self.assertIn('<p class="dateline">10월 13일 화요일 오후판</p>', day)
        self.assertIn('<a href="2026-10-13/index.html">2026-10-13 헤드라인</a></h2>', day)
        self.assertIn("요약 문장입니다.", day)
        self.assertIn('href="2026-10-13/a-one/index.html"', day)
        self.assertIn('href="2026-10-13/a-two/index.html"', day)
        for absent in ("핵심 1분", "무슨 일이 있었나", 'class="line"'):
            self.assertNotIn(absent, day)

    def test_archive_rows_name_the_section_and_title_only(self):
        day = make_day()
        day["sections"][0]["items"][0]["teaser"] = "가상 한 줄"
        self.write_day(day)
        self.build()
        archive = self.read("news/index.html")
        self.assertIn('<a href="2026-10-13/a-one/index.html"><span class="meta">경제</span>'
                      '<span class="t">기사 하나</span></a>', archive)
        self.assertNotIn("가상 한 줄", archive)

    def test_article_page_shows_its_teaser_once_between_the_times_and_the_body(self):
        day = make_day()
        day["sections"][0]["items"][0]["teaser"] = "요약 한 줄 <b>"
        page = self.article_page(day, "a-one")
        self.assertEqual(page.count('<p class="deck">요약 한 줄 &lt;b&gt;</p>'), 1)
        self.assertLess(page.index('class="checked"'), page.index('class="deck"'))
        self.assertLess(page.index('class="deck"'), page.index('class="part fact"'))
        self.assertIn("둘째 문장 <strong>강조</strong>.", page)

    def test_article_without_teaser_has_no_empty_summary(self):
        page = self.article_page(make_day(), "a-two")
        self.assertNotIn('class="deck"', page)

    def test_teaser_is_the_description_for_search_and_sharing(self):
        day = make_day()
        day["sections"][0]["items"][0]["teaser"] = "지수가 5.46% 내린 날의 요약"
        page = self.article_page(day, "a-one")
        self.assertIn('<meta name="description" content="지수가 5.46% 내린 날의 요약">', page)
        self.assertIn('<meta property="og:description" content="지수가 5.46% 내린 날의 요약">', page)
        blocks = re.findall(r'<script type="application/ld\+json">(.*?)</script>', page, re.S)
        news = [json.loads(b) for b in blocks if '"NewsArticle"' in b][0]
        self.assertEqual(news["description"], "지수가 5.46% 내린 날의 요약")

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

    def test_refuses_to_drop_a_day_whose_article_pages_exist(self):
        self.write_day(make_day("2026-10-12"))
        self.write_day(make_day("2026-10-13"))
        self.build()
        before = self.snapshot()
        (self.root / "content" / "2026-10-12.json").unlink()
        with self.assertRaises(build.BuildError) as ctx:
            self.build()
        self.assertIn("news/2026-10-12/a-one/", str(ctx.exception))
        self.assertEqual(self.snapshot(), before)

    def test_removes_a_day_page_once_its_content_and_article_pages_are_gone(self):
        self.write_day(make_day("2026-10-12"))
        self.write_day(make_day("2026-10-13"))
        self.build()
        (self.root / "content" / "2026-10-12.json").unlink()
        for item_id in ("a-one", "a-two"):
            shutil.rmtree(self.root / "news" / "2026-10-12" / item_id)
        self.build()
        self.assertFalse(self.exists("news/2026-10-12"))
        self.assertTrue(self.exists("news/2026-10-13/a-one/index.html"))

    def test_building_twice_gives_identical_files(self):
        self.write_day(make_day())
        self.build()
        first = self.snapshot()
        self.build()
        self.assertEqual(self.snapshot(), first)


class PublishedAddressTests(SiteCase):
    """게시한 기사 주소는 새 판이 나와도 남는다 (GROWTH-URL-01). 새 판에서 뺀 기사는 earlier 로 옮기고,
    내리는 기사는 withdrawn 에 적는다. 그냥 빠지면 생성을 멈춘다."""

    def morning(self):
        return make_day(edition="오전판", checked_at="05:06 KST")

    def afternoon(self, morning):
        """오전판의 a-one 은 고쳐서 다시 싣고, a-two 는 빼서 earlier 로 옮기고, a-three 를 새로 싣는다."""
        day = copy.deepcopy(morning)
        sec = day["sections"][0]
        one, two = sec["items"]
        one["title"] = "기사 하나 오후 정정"
        three = {"id": "a-three", "date": day["date"], "title": "새 기사 셋", "facts": "세 번째 사실.",
                 "sources": [{"name": "AP", "url": "https://example.com/c"}]}
        sec["items"] = [one, three]
        day.update(edition="오후판", checked_at="17:02 KST",
                   earlier=[{"edition": "오전판", "checked_at": "05:06 KST",
                             "sections": [{"id": "economy", "title": "경제", "items": [two]}]}])
        return day

    def publish_morning_then(self, afternoon):
        self.write_day(self.morning())
        self.build()
        self.write_day(afternoon)
        self.build()

    def test_article_moved_to_earlier_keeps_its_address_and_text(self):
        self.publish_morning_then(self.afternoon(self.morning()))
        page = self.read("news/2026-10-13/a-two/index.html")
        self.assertIn("두 번째 사실.", page)
        self.assertIn("화요일 오전판", page)
        self.assertIn("뉴스 확인 05:06 KST", page)
        self.assertIn('"datePublished": "2026-10-13T05:06:00+09:00"', page)

    def test_republished_article_is_corrected_at_the_same_address(self):
        self.publish_morning_then(self.afternoon(self.morning()))
        page = self.read("news/2026-10-13/a-one/index.html")
        self.assertIn("기사 하나 오후 정정", page)
        self.assertIn("화요일 오후판", page)
        self.assertTrue(self.exists("news/2026-10-13/a-three/index.html"))

    def test_briefing_lists_earlier_articles_under_their_edition(self):
        self.publish_morning_then(self.afternoon(self.morning()))
        for rel, href in (("news/2026-10-13/index.html", 'href="a-two/index.html"'),
                          ("index.html", 'href="news/2026-10-13/a-two/index.html"')):
            main, earlier = self.read(rel).split("오전판에 실린 기사", 1)
            self.assertNotIn(href, main)
            self.assertIn(href, earlier)

    def test_archive_and_sitemap_keep_earlier_articles(self):
        self.publish_morning_then(self.afternoon(self.morning()))
        self.assertIn('href="2026-10-13/a-two/index.html"', self.read("news/index.html"))
        self.assertIn("/news/2026-10-13/a-two/", self.read("sitemap.xml"))

    def test_clean_rebuild_gives_the_same_site(self):
        self.publish_morning_then(self.afternoon(self.morning()))
        with tempfile.TemporaryDirectory() as other:
            build.build(self.root, out=Path(other))
            fresh = {str(p.relative_to(other)): hashlib.sha256(p.read_bytes()).hexdigest()
                     for p in sorted(Path(other).rglob("*")) if p.is_file()}
        self.assertEqual(fresh, self.snapshot())

    def test_dropping_a_published_article_stops_the_build(self):
        self.write_day(self.morning())
        self.build()
        day = self.afternoon(self.morning())
        del day["earlier"]
        self.assert_rejected(day, "news/2026-10-13/a-two/", "earlier", "withdrawn")

    def test_check_only_also_stops_on_a_dropped_article(self):
        self.write_day(self.morning())
        self.build()
        day = self.afternoon(self.morning())
        del day["earlier"]
        self.write_day(day)
        with self.assertRaises(build.BuildError):
            self.build(check_only=True)

    def test_withdrawn_article_leaves_a_notice_page_that_nothing_links_to(self):
        day = self.afternoon(self.morning())
        del day["earlier"]
        day["withdrawn"] = [{"id": "a-two", "reason": "가상 사유"}]
        self.publish_morning_then(day)
        notice = self.read("news/2026-10-13/a-two/index.html")
        self.assertIn("내린 기사입니다", notice)
        self.assertIn("가상 사유", notice)
        self.assertIn('<meta name="robots" content="noindex">', notice)
        self.assertNotIn("두 번째 사실.", notice)
        for rel in ("sitemap.xml", "news/index.html", "news/2026-10-13/index.html", "index.html"):
            self.assertNotIn("a-two", self.read(rel))
            self.assertNotIn("가상 사유", self.read(rel))

    def test_withdrawing_rewrites_only_the_page_it_wrote(self):
        self.write_day(self.morning())
        self.build()
        (self.root / "news" / "2026-10-13" / "a-two" / "note.txt").write_text("남긴 파일", encoding="utf-8")
        day = self.afternoon(self.morning())
        del day["earlier"]
        day["withdrawn"] = [{"id": "a-two", "reason": "가상 사유"}]
        self.write_day(day)
        self.build()
        self.assertIn("내린 기사입니다", self.read("news/2026-10-13/a-two/index.html"))
        self.assertTrue(self.exists("news/2026-10-13/a-two/note.txt"))

    def test_same_article_cannot_be_current_and_earlier(self):
        day = self.afternoon(self.morning())
        day["earlier"][0]["sections"][0]["items"].append(copy.deepcopy(day["sections"][0]["items"][0]))
        self.assert_rejected(day, "earlier[0]", "a-one", "중복")

    def test_withdrawn_article_must_be_gone_from_content(self):
        day = self.afternoon(self.morning())
        day["withdrawn"] = [{"id": "a-two", "reason": "가상 사유"}]
        self.assert_rejected(day, "withdrawn[0]", "a-two")

    def test_earlier_articles_are_checked_like_current_ones(self):
        day = self.afternoon(self.morning())
        del day["earlier"][0]["edition"]
        self.assert_rejected(day, "earlier[0]", "edition")
        day = self.afternoon(self.morning())
        del day["earlier"][0]["sections"][0]["items"][0]["sources"]
        self.assert_rejected(day, "earlier[0]", "sources")

    def test_earlier_edition_cannot_be_later_than_this_one(self):
        day = self.afternoon(self.morning())
        day["earlier"][0]["checked_at"] = "18:00 KST"
        self.assert_rejected(day, "earlier[0]", "checked_at")


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
        day["sections"][0]["lede"] = "렌더링되지 않는 필드의 매입가"
        self.assert_rejected(day, "sections[0].lede", "공개 규칙 1번")

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
        day["sections"][0]["lede"] = "렌더링되지 않는 필드의 매입가"
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



class NewsV2Tests(SiteCase):
    """노션 v2 공개 구역에서 옮기는 항목: 허용 필드, 진행·근거 표시, 앞선 기사, 대표 기사, 분야 바로가기,
    정정 대상, 개인 문맥 규칙과 주의 단어."""

    def two_days(self):
        self.write_day(make_day("2026-10-12"))
        day = make_day("2026-10-13")
        day["sections"][0]["items"][1].update(status="진전", basis="단일 보도", date="2026-10-12",
                                              follows={"date": "2026-10-12", "id": "a-one"})
        return day

    # ---- 허용 필드: 공개 입력에는 정해진 항목만
    def test_unknown_fields_stop_the_build_and_name_only_the_field(self):
        day = make_day()
        day["판_상태"] = "가상 운영값"
        self.assert_rejected(day, "판_상태", "쓸 수 없는 항목")
        day = make_day()
        day["sections"][0]["items"][0]["internal_key"] = "가상-키"
        self.assert_rejected(day, "internal_key")
        day = make_day()
        day["market"]["rows"][0]["extra"] = "x"
        self.assert_rejected(day, "market.rows[0]", "extra")

    def test_unknown_field_message_does_not_repeat_its_value(self):
        day = make_day()
        day["메모"] = "가상비밀문장QQ"
        self.write_day(day)
        with self.assertRaises(build.BuildError) as ctx:
            self.build()
        self.assertNotIn("가상비밀문장QQ", str(ctx.exception))

    # ---- 진행·근거 표시
    def test_progress_date_and_basis_show_as_one_small_line(self):
        page = self.day_page(self.two_days())
        story = page[page.index('id="a-two"'):]
        meta = story[story.index('class="meta-line"'):story.index("</p>", story.index('class="meta-line"'))]
        for needle in ('<span class="st">진전</span>', "10월 12일", "단일 보도"):
            self.assertIn(needle, meta)
        self.assertNotIn("검증", page)

    def test_new_status_is_not_shown_and_unknown_status_is_rejected(self):
        day = make_day()
        day["sections"][0]["items"][0]["status"] = "신규"
        page = self.day_page(day)
        self.assertNotIn("신규", page)
        day["sections"][0]["items"][0]["status"] = "속보"
        self.assert_rejected(day, "status")

    def test_basis_must_be_short(self):
        day = make_day()
        day["sections"][0]["items"][0]["basis"] = "가" * 25
        self.assert_rejected(day, "basis")

    # ---- 다른 날 후속 기사: 앞선 기사와 잇는다
    def test_follow_up_links_to_the_earlier_article(self):
        day = self.two_days()
        page = self.day_page(day)
        self.assertIn('앞선 기사 <a href="../2026-10-12/a-one/index.html">10월 12일 · 기사 하나</a>', page)
        article = self.read("news/2026-10-13/a-two/index.html")
        self.assertIn('href="../../2026-10-12/a-one/index.html"', article)
        home = self.read("index.html")
        self.assertIn('href="news/2026-10-12/a-one/index.html">10월 12일 · 기사 하나', home)

    def test_follow_up_to_a_missing_article_stops_the_build(self):
        day = self.two_days()
        day["sections"][0]["items"][1]["follows"]["id"] = "no-such"
        self.assert_rejected(day, "follows")

    def test_follow_up_must_point_to_an_earlier_day(self):
        day = make_day()
        day["sections"][0]["items"][1]["follows"] = {"date": "2026-10-13", "id": "a-one"}
        self.assert_rejected(day, "follows")

    # ---- 대표 기사
    def test_lead_article_is_marked_and_must_exist(self):
        day = make_day(lead_ref="a-two")
        page = self.day_page(day)
        self.assertIn('<article class="item lead" id="a-two">', page)
        self.assertIn('<article class="item" id="a-one">', page)
        self.assert_rejected(make_day(lead_ref="nope"), "lead_ref")

    def test_v2_editions_need_a_lead_article_and_a_short_summary(self):
        self.write_site(dict(SITE, v2_from="2026-10-13"))
        day = make_day()
        day["market"]["rows"][0]["source"] = {"name": "가상시세", "url": "https://example.com/row"}
        self.assert_rejected(day, "lead_ref")
        day["lead_ref"] = "a-one"
        day["summary"] = "가" * 111
        self.assert_rejected(day, "summary")
        day["summary"] = "가" * 110
        self.write_day(day)
        self.build()
        self.write_day(make_day("2026-10-12"))  # v2 이전 판은 그대로 된다
        self.build()

    def test_summary_and_quick_must_not_repeat_the_headline(self):
        self.assert_rejected(make_day(summary="2026-10-13 헤드라인"), "summary")
        day = make_day()
        day["quick"].append({"text": "2026-10-13 헤드라인", "ref": "a-one"})
        self.assert_rejected(day, "quick[1]")

    # ---- 분야 바로가기
    def test_section_shortcuts_list_only_sections_with_articles_and_their_counts(self):
        day = make_day()
        day["sections"].append({"id": "empty", "title": "빈분야", "items": []})
        day["sections"].append({"id": "auto", "title": "자동차", "items": [
            {"id": "c-one", "date": day["date"], "title": "차 기사", "facts": "차 사실.",
             "sources": [{"name": "AP", "url": "https://example.com/c"}]}]})
        page = self.day_page(day)
        jump = page[page.index('class="jump"'):page.index("</nav>", page.index('class="jump"'))]
        self.assertIn('<a href="#economy">경제<span class="n">2<span class="sr">건</span></span></a>', jump)
        self.assertIn('<a href="#auto">자동차<span class="n">1<span class="sr">건</span></span></a>', jump)
        self.assertNotIn("빈분야", page)

    def test_no_shortcuts_when_there_is_only_one_section(self):
        self.assertNotIn('class="jump"', self.day_page(make_day()))

    # ---- 정정: 대상 기사와 잇는다
    def test_correction_links_to_its_article_and_appears_on_it(self):
        day = make_day(corrections=[{"date": "2026-10-14", "text": "가상 수치를 바로잡았다.", "ref": "a-one"}])
        page = self.day_page(day)
        fixes = page[page.index('id="h-corrections"'):]
        self.assertIn('<a href="../2026-10-13/a-one/index.html">해당 기사</a>', fixes)
        article = self.read("news/2026-10-13/a-one/index.html")
        self.assertIn('id="fix-title"', article)
        self.assertIn("가상 수치를 바로잡았다.", article)
        self.assertNotIn('id="fix-title"', self.read("news/2026-10-13/a-two/index.html"))

    def test_correction_must_point_to_an_article_of_that_day(self):
        self.assert_rejected(make_day(corrections=[{"date": "2026-10-14", "text": "가상", "ref": "nope"}]), "ref")

    def test_related_list_shows_same_section_first_and_at_most_five(self):
        day = make_day()
        day["sections"].append({"id": "auto", "title": "자동차", "items": [
            {"id": "c-%d" % n, "date": day["date"], "title": "차 기사 %d" % n, "facts": "차 사실.",
             "sources": [{"name": "AP", "url": "https://example.com/c%d" % n}]} for n in range(6)]})
        page = self.article_page(day, "a-one")
        others = page[page.index('id="others-title"'):]
        self.assertEqual(others.count("<li>"), 5)
        self.assertLess(others.index('href="../a-two/index.html"'), others.index('href="../c-0/index.html"'))
        self.assertNotIn('href="../c-4/index.html"', others)

    def test_schedule_is_listed_in_time_order(self):
        day = make_day(side={"schedule": [{"at": "2026-10-14T09:00:00+09:00", "title": "가상 늦은 일정"},
                                          {"at": "2026-10-14T06:00:00+09:00", "title": "가상 이른 일정"}]})
        page = self.day_page(day)
        self.assertLess(page.index("가상 이른 일정"), page.index("가상 늦은 일정"))

    # ---- 기사 페이지: 브리핑으로 돌아가는 줄
    def test_article_page_has_a_way_back_to_its_briefing(self):
        page = self.article_page(make_day(), "a-one")
        self.assertIn('<p class="back"><a href="../index.html">10월 13일 화요일 오후판 전체 보기</a></p>', page)

    # ---- 개인 문맥 규칙과 주의 단어
    RULES = dict(SITE, blocklist=[],
                 context_rules=[r"(본인|나의|저의|제가|내가|우리 ?집)[^.\n]{0,24}(월세|보증금|분양가|매입가|보유 ?종목)"],
                 review_terms=["월세", "보증금"])

    def test_personal_context_is_blocked(self):
        self.write_site(self.RULES)
        day = make_day()
        day["sections"][0]["items"][1]["facts"] = "본인 소유 가상단지의 월세 조건이 바뀌었다."
        self.assert_rejected(day, "items[1].facts", "문맥 규칙 1번")

    def test_public_policy_text_passes_with_a_review_note_that_hides_the_text(self):
        self.write_site(self.RULES)
        day = make_day()
        day["sections"][0]["items"][1]["facts"] = "정부가 가상 전월세 신고 기준을 바꿨다. 보증금 기준도 낮췄다."
        self.write_day(day)
        code, printed = self.run_main()
        self.assertEqual(code, 0)
        self.assertIn("주의 단어 위치", printed)
        self.assertIn("sections[0].items[1].facts", printed)
        self.assertNotIn("전월세 신고", printed)

    def run_main(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = build.main(["--root", str(self.root)])
        return code, out.getvalue() + err.getvalue()


if __name__ == "__main__":
    unittest.main(verbosity=2)
