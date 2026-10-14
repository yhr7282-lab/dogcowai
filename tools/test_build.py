"""tools/build.py 테스트. 실행: python3 tools/test_build.py

테스트에 쓰는 이름·문장은 모두 가상의 표식이다. 실제 개인 정보를 넣지 않는다.
"""
import copy
import hashlib
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

    def assert_rejected(self, day, *needles):
        before = self.snapshot()
        self.write_day(day)
        with self.assertRaises(build.BuildError) as ctx:
            self.build()
        for needle in needles:
            self.assertIn(needle, str(ctx.exception))
        self.assertEqual(self.snapshot(), before)


class BuildOutputTests(SiteCase):
    def article_page(self, day, item_id):
        self.write_day(day)
        self.build()
        return self.read("news/%s/%s/index.html" % (day["date"], item_id))

    def test_creates_all_pages_and_site_files(self):
        self.write_day(make_day())
        self.build()
        for rel in ("index.html", "news/index.html", "news/2026-10-13/index.html",
                    "news/2026-10-13/a-one/index.html", "news/2026-10-13/a-two/index.html",
                    "topic/index.html", "topic/economy/index.html",
                    "404.html", "sitemap.xml", "robots.txt", "feed.xml"):
            self.assertTrue(self.exists(rel), rel)

    # ---- 일별 브리핑: 그날 기사의 목록

    def test_day_page_is_an_index_that_links_to_each_article(self):
        page = self.day_page(make_day())
        self.assertIn("2026-10-13 헤드라인", page)
        self.assertIn('id="economy"', page)
        self.assertIn("2026년 10월 13일 화요일 오후판", page)
        self.assertIn("뉴스 재확인 17:02 KST", page)
        self.assertIn('href="a-one/index.html"', page)
        self.assertIn('href="a-two/index.html"', page)
        self.assertIn("지수가 5.46% 내렸다.", page)

    def test_day_page_does_not_carry_article_bodies(self):
        page = self.day_page(make_day())
        for absent in ("둘째 문장", "의미 문장.", "조건 문장.", "https://example.com/a", "무슨 일이 있었나"):
            self.assertNotIn(absent, page)

    def test_each_category_shows_one_article_and_folds_the_rest(self):
        page = self.day_page(make_day())
        sec = page[page.index('<section class="sec" id="economy"'):]
        fold = sec.index("<details")
        self.assertLess(sec.index('id="a-one"'), fold)
        self.assertGreater(sec.index('id="a-two"'), fold)
        self.assertIn('<span class="when-closed">경제 기사 1건 더 보기</span>', sec)
        self.assertIn('<span class="when-open">경제 기사 접기</span>', sec)

    def test_category_lead_is_the_featured_article(self):
        day = make_day()
        items = day["sections"][0]["items"]
        del items[0]["featured"]
        items[1]["featured"] = True
        page = self.day_page(day)
        sec = page[page.index('<section class="sec" id="economy"'):]
        self.assertLess(sec.index('id="a-two"'), sec.index("<details"))
        self.assertGreater(sec.index('id="a-one"'), sec.index("<details"))

    def test_category_with_one_article_has_no_fold(self):
        day = make_day()
        day["sections"][0]["items"].pop()
        self.assertNotIn("<details", self.day_page(day))

    def test_category_shows_its_one_line_summary_but_not_the_bullet_points(self):
        page = self.day_page(make_day())
        self.assertIn('<p class="lede">한 줄 요약</p>', page)
        self.assertNotIn("포인트 하나", page)

    def test_every_category_ends_with_the_same_link_to_its_topic_page(self):
        day = make_day()
        day["sections"].append({"id": "auto", "title": "자동차", "items": [
            {"id": "c-one", "date": day["date"], "title": "차 기사", "facts": "사실.",
             "sources": [{"name": "AP", "url": "https://example.com/c"}]}]})
        page = self.day_page(day)
        self.assertIn('<a href="../../topic/economy/index.html">지난 경제 기사</a>', page)
        self.assertIn('<a href="../../topic/auto/index.html">지난 자동차 기사</a>', page)

    def test_fold_labels_use_one_vocabulary(self):
        html = self.day_page(make_day())
        closed = re.findall(r'<span class="when-closed">(.*?)</span>', html)
        opened = re.findall(r'<span class="when-open">(.*?)</span>', html)
        self.assertTrue(closed)
        self.assertTrue(all(c.endswith("더 보기") for c in closed), closed)
        self.assertTrue(all(o.endswith("접기") for o in opened), opened)
        self.assertEqual(html.count("<details"), len(closed))

    def test_count_label_shows_the_number_of_published_articles(self):
        day = make_day()
        day["sections"][0]["total"] = 5
        page = self.day_page(day)
        self.assertIn(">기사 2건<", page)
        self.assertNotIn("건 중", page)

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

    # ---- 기사 페이지: 기사 하나

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

    def test_article_page_shows_the_way_back_to_its_briefing_and_category(self):
        page = self.article_page(make_day(), "a-one")
        crumbs = page[page.index('class="crumbs"'):page.index("</nav>", page.index('class="crumbs"'))]
        self.assertIn('<a href="../../../index.html">홈</a>', crumbs)
        self.assertIn('<a href="../index.html">10월 13일 화요일</a>', crumbs)
        self.assertIn('<a href="../../../topic/economy/index.html">경제</a>', page)

    def test_article_page_links_the_previous_and_next_article_of_the_day(self):
        self.write_day(make_day())
        self.build()
        first = self.read("news/2026-10-13/a-one/index.html")
        second = self.read("news/2026-10-13/a-two/index.html")
        self.assertIn('href="../a-two/index.html"><span>다음 기사</span><b>기사 둘</b>', first)
        self.assertNotIn("<span>이전 기사</span>", first)
        self.assertIn('href="../a-one/index.html"><span>이전 기사</span><b>기사 하나</b>', second)
        self.assertNotIn("<span>다음 기사</span>", second)

    def test_article_page_lists_the_other_articles_of_its_category(self):
        page = self.article_page(make_day(), "a-one")
        side = page[page.index('class="rail"'):]
        self.assertIn('href="../a-two/index.html"', side)
        self.assertIn("기사 둘", side)
        self.assertNotIn('href="../a-one/index.html"', side)

    def test_article_page_points_to_corrections_when_the_day_has_them(self):
        day = make_day(corrections=[{"date": "2026-10-14", "text": "수치를 바로잡았다."}])
        self.assertIn('href="../index.html#h-corrections"', self.article_page(day, "a-one"))

    def test_article_page_without_day_corrections_has_no_corrections_link(self):
        self.assertNotIn("h-corrections", self.article_page(make_day(), "a-one"))

    def test_old_anchor_links_still_land_on_the_article_row(self):
        page = self.day_page(make_day())
        self.assertIn('<li id="a-one"><a href="a-one/index.html">', page)

    # ---- 홈

    def test_home_opens_with_the_site_introduction(self):
        site = dict(SITE, intro={"heading": "소개 제목", "paragraphs": ["소개 문단"], "items": ["원칙 하나"]})
        self.write_site(site)
        self.write_day(make_day())
        self.build()
        home = self.read("index.html")
        self.assertIn('<h1 id="intro-title">소개 제목</h1>', home)
        self.assertLess(home.index("소개 문단"), home.index("2026-10-13 헤드라인"))
        self.assertIn("<li>원칙 하나</li>", home)

    def test_home_links_to_the_briefing_instead_of_embedding_it(self):
        self.write_day(make_day())
        self.build()
        home = self.read("index.html")
        self.assertIn('<a class="go" href="news/2026-10-13/index.html">10월 13일 브리핑 전체 보기', home)
        for absent in ("<details", 'id="economy"', "두 번째 사실.", "둘째 문장", "핵심 1분"):
            self.assertNotIn(absent, home)

    def test_home_lists_featured_articles_and_links_to_their_pages(self):
        self.write_day(make_day())
        self.build()
        home = self.read("index.html")
        today = home[home.index('class="today"'):home.index("</section>", home.index('class="rows"'))]
        self.assertIn('href="news/2026-10-13/a-one/index.html"', today)
        self.assertIn("기사 하나", today)
        self.assertIn("지수가 5.46% 내렸다.", today)
        self.assertNotIn("기사 둘", today)

    def test_home_falls_back_to_category_leads_when_nothing_is_featured(self):
        day = make_day()
        del day["sections"][0]["items"][0]["featured"]
        self.write_day(day)
        self.build()
        home = self.read("index.html")
        self.assertIn('href="news/2026-10-13/a-one/index.html"', home)
        self.assertNotIn('href="news/2026-10-13/a-two/index.html"', home)

    def test_home_shows_latest_and_lists_previous_days(self):
        self.write_day(make_day("2026-10-12"))
        self.write_day(make_day("2026-10-13"))
        self.build()
        home = self.read("index.html")
        self.assertIn("2026-10-13 헤드라인", home)
        self.assertIn("2026-10-12 헤드라인", home)
        self.assertIn('href="news/2026-10-12/index.html"', home)

    def test_home_lists_the_categories(self):
        self.write_day(make_day())
        self.build()
        home = self.read("index.html")
        self.assertIn('href="topic/economy/index.html"', home)

    def test_home_shows_market_bars_with_a_link_to_the_day_table(self):
        day = make_day()
        day["market"]["rows"] = [
            {"name": "가", "value": "100", "change": "▼ 2.00%", "dir": "down", "basis": "종가", "asof": day["date"]},
            {"name": "나", "value": "200", "change": "▲ 1.00%", "dir": "up", "basis": "종가", "asof": day["date"]},
        ]
        self.write_day(day)
        self.build()
        home = self.read("index.html")
        self.assertIn('class="chart"', home)
        self.assertIn('href="news/2026-10-13/index.html#market-title"', home)
        self.assertNotIn("<table", home)

    def test_home_has_no_location_line(self):
        self.write_day(make_day())
        self.build()
        self.assertNotIn('class="crumbs"', self.read("index.html"))

    # ---- 메뉴, 위치, 목록 페이지

    def test_menu_marks_the_page_the_reader_is_on(self):
        self.write_day(make_day())
        self.build()
        home, archive, topics = self.read("index.html"), self.read("news/index.html"), self.read("topic/index.html")
        self.assertIn('href="index.html" aria-current="page">홈<', home)
        self.assertNotIn('aria-current="page">날짜별<', home)
        self.assertIn('href="../news/index.html" aria-current="page">날짜별<', archive)
        self.assertIn('href="../topic/index.html" aria-current="page">분야별<', topics)
        self.assertIn('aria-current="page">날짜별<', self.read("news/2026-10-13/a-one/index.html"))
        self.assertIn('aria-current="page">분야별<', self.read("topic/economy/index.html"))

    def test_day_page_shows_where_it_sits_in_the_site(self):
        page = self.day_page(make_day())
        crumbs = page[page.index('class="crumbs"'):page.index("</nav>", page.index('class="crumbs"'))]
        self.assertIn('<a href="../../index.html">홈</a>', crumbs)
        self.assertIn('<a href="../index.html">날짜별</a>', crumbs)
        self.assertIn('<li aria-current="page">10월 13일 화요일</li>', crumbs)

    def test_archive_groups_days_by_month(self):
        self.write_day(make_day("2026-09-30"))
        self.write_day(make_day("2026-10-13"))
        self.build()
        archive = self.read("news/index.html")
        self.assertLess(archive.index("2026년 10월"), archive.index("2026년 9월"))
        self.assertNotIn("topic/", archive.split("</header>")[1].split("<footer>")[0])

    def test_topic_index_lists_each_category_with_its_article_count(self):
        self.write_day(make_day("2026-10-12"))
        self.write_day(make_day("2026-10-13"))
        self.build()
        topics = self.read("topic/index.html")
        self.assertIn('<h1 id="top-title">분야별 기사</h1>', topics)
        self.assertIn('href="economy/index.html"', topics)
        self.assertIn("기사 4건", topics)

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

    def test_pages_carry_no_instruction_sentences(self):
        self.write_day(make_day())
        self.build()
        for rel in ("index.html", "news/index.html", "news/2026-10-13/index.html",
                    "news/2026-10-13/a-one/index.html", "topic/index.html", "topic/economy/index.html"):
            html = self.read(rel)
            self.assertNotIn('class="hint"', html, rel)
            self.assertNotIn("누르면", html, rel)

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

    def test_404_uses_root_absolute_links_and_is_not_indexed(self):
        self.write_day(make_day())
        self.build()
        page = self.read("404.html")
        self.assertIn('href="/assets/style.css"', page)
        self.assertIn('content="noindex"', page)

    def test_static_page_is_built_and_linked(self):
        self.write_page({"slug": "about", "title": "소개", "description": "설명",
                         "sections": [{"heading": "무엇인가", "paragraphs": ["문단 <b>"], "items": ["항목"]}]})
        self.write_day(make_day())
        self.build()
        about = self.read("about/index.html")
        self.assertIn("무엇인가", about)
        self.assertIn("문단 &lt;b&gt;", about)
        self.assertIn('href="about/index.html"', self.read("index.html"))

    # ---- 시장 수치

    def test_ticker_repeats_the_market_rows_as_plain_text(self):
        page = self.day_page(make_day())
        ticker = page[page.index('class="ticker"'):page.index("</div></div>", page.index('class="ticker"'))]
        self.assertIn("KOSPI", ticker)
        self.assertIn("6,035.35", ticker)
        self.assertIn('class="c down"', ticker)
        self.assertNotIn("<a ", ticker)

    def test_market_chart_draws_one_bar_per_row_scaled_to_the_largest_move(self):
        day = make_day()
        day["market"]["rows"] = [
            {"name": "가", "value": "100", "change": "▼ 2.00%", "dir": "down", "basis": "종가", "asof": day["date"]},
            {"name": "나", "value": "200", "change": "▲ 1.00%", "dir": "up", "basis": "종가", "asof": day["date"]},
        ]
        page = self.day_page(day)
        chart = page[page.index('class="chart"'):page.index("</ul>", page.index('class="chart"'))]
        self.assertEqual(chart.count("<li>"), 2)
        self.assertIn('class="bar down" style="width:50.0%"', chart)
        self.assertIn('class="bar up" style="width:25.0%"', chart)

    def test_market_chart_is_left_out_when_fewer_than_two_rows_have_a_change(self):
        self.write_day(make_day())
        self.build()
        self.assertNotIn('class="chart"', self.read("news/2026-10-13/index.html"))
        self.assertNotIn('class="chart"', self.read("index.html"))

    def test_page_without_market_has_no_ticker(self):
        day = make_day()
        del day["market"]
        self.assertNotIn('class="ticker"', self.day_page(day))

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
        page = self.day_page(make_day())
        ids = re.findall(r'\sid="([^"]+)"', page)
        self.assertEqual(len(ids), len(set(ids)))


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
        self.assert_rejected(day, "매입가")
        self.assertFalse(self.exists("index.html"))

    def test_blocklist_scans_fields_that_are_not_rendered(self):
        day = make_day()
        day["memo"] = "렌더링되지 않는 필드의 매입가"
        self.assert_rejected(day, "memo", "매입가")

    def test_blocklist_scans_attribute_values(self):
        day = make_day()
        day["sections"][0]["items"][0]["sources"][0]["url"] = "https://example.com/J-12345678"
        self.assert_rejected(day, "J-")

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
        self.assert_rejected(day, "가상비밀어")


if __name__ == "__main__":
    unittest.main(verbosity=2)
