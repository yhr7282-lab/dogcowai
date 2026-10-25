"""수집·검증 도구 테스트. 실행: python3 tools/test_pipeline.py (네트워크를 쓰지 않는다)"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import candidates  # noqa: E402
import check_links  # noqa: E402
import edition_check  # noqa: E402
import market_snapshot  # noqa: E402
import build  # noqa: E402
import datetime  # noqa: E402
import json  # noqa: E402
import tempfile  # noqa: E402


def cand(title, url, source="가상통신", at="2026-10-13T09:00:00+09:00", topic="economy"):
    return {"title": title, "url": url, "source": source, "published_at": at, "topic": topic}


class CandidateTests(unittest.TestCase):
    def test_same_url_with_tracking_parameters_is_one_candidate(self):
        groups = candidates.cluster([
            cand("지수 급락", "https://example.com/a?utm_source=x"),
            cand("지수 급락", "https://example.com/a"),
        ])
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0]["members"]), 2)

    def test_similar_titles_about_one_event_are_grouped(self):
        groups = candidates.cluster([
            cand("코스피 5.46% 급락, 매도 사이드카 발동", "https://a.example/1", "가"),
            cand("코스피 5.46% 급락…매도 사이드카 발동", "https://b.example/2", "나"),
            cand("TSMC 3분기 순이익 65% 증가 전망", "https://c.example/3", "다"),
        ])
        self.assertEqual(len(groups), 2)
        self.assertEqual(groups[0]["independent_sources"], 2)

    def test_different_topics_are_not_merged(self):
        groups = candidates.cluster([
            cand("금리 동결 결정", "https://a.example/1", topic="economy"),
            cand("금리 동결 결정", "https://b.example/2", topic="auto"),
        ])
        self.assertEqual(len(groups), 2)

    def test_earliest_report_is_marked_as_first(self):
        groups = candidates.cluster([
            cand("지수 급락 마감", "https://b.example/2", "나", "2026-10-13T16:00:00+09:00"),
            cand("지수 급락 마감", "https://a.example/1", "가", "2026-10-13T15:40:00+09:00"),
        ])
        self.assertEqual(groups[0]["first"]["url"], "https://a.example/1")

    def test_already_published_urls_are_dropped(self):
        groups = candidates.cluster([cand("지수 급락", "https://example.com/a")],
                                    published_urls={"https://example.com/a"})
        self.assertEqual(groups, [])

    def test_candidate_without_url_or_title_is_rejected(self):
        with self.assertRaises(ValueError):
            candidates.cluster([{"title": "", "url": "https://example.com/a"}])


class LinkClassificationTests(unittest.TestCase):
    def test_ok_page(self):
        self.assertEqual(check_links.classify(200, "기사 제목", "본문 " * 300), "ok")

    def test_blocked_is_not_the_same_as_broken(self):
        self.assertEqual(check_links.classify(403, "", ""), "blocked")
        self.assertEqual(check_links.classify(401, "", ""), "blocked")
        self.assertEqual(check_links.classify(404, "", ""), "broken")
        self.assertEqual(check_links.classify(None, "", ""), "broken")

    def test_placeholder_page_with_status_200_is_flagged(self):
        self.assertEqual(check_links.classify(200, "Coming Soon: 보고서", "준비 중"), "placeholder")
        self.assertEqual(check_links.classify(200, "요청하신 페이지를 찾을 수 없습니다", "x" * 2000), "placeholder")

    def test_urls_of_a_day_are_collected_once(self):
        day = {"market": {"sources": [{"name": "가", "url": "https://x.example/1"}]},
               "sections": [{"items": [{"id": "a", "sources": [{"name": "가", "url": "https://x.example/1"},
                                                                {"name": "나", "url": "https://x.example/2"}]}]}]}
        self.assertEqual(check_links.collect_urls(day), ["https://x.example/1", "https://x.example/2"])



class EditionCheckTests(unittest.TestCase):
    """회차 검사기: 표지·개정 일치, 후보 수 = 반영 + 제외, 반영한 기사의 실제 존재. 모든 값은 가상의 표식이다."""

    ED = "2026-10-25-AM"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "repo"
        self.state = Path(self._tmp.name) / "state"
        (self.root / "content").mkdir(parents=True)
        self.state.mkdir()
        self.day = {"date": "2026-10-25", "edition": "오전판", "sections": [
            {"id": "economy", "title": "경제", "items": [{"id": "fake-one"}, {"id": "fake-two"}]}]}
        self.src = {"edition": self.ED, "format": "v2", "revision": 2, "status": "검증완료", "result": "UPDATED",
                    "public_count": 3,
                    "markers": {m: {"edition": self.ED, "revision": 2} for m in edition_check.MARKERS},
                    "candidates": [
                        {"key": "fake-event-a", "section": "경제", "status": "신규", "event_date": "2026-10-25"},
                        {"key": "fake-event-b", "section": "배터리", "status": "보완", "event_date": "2026-10-23"},
                        {"key": "fake-event-c", "section": "AI", "status": "진전", "event_date": "2026-10-24"}]}
        self.src["markers"]["saved_line"]["public_count"] = 3
        self.dec = {"fake-event-a": {"id": "fake-one"}, "fake-event-b": {"id": "fake-two"},
                    "fake-event-c": {"excluded": "가상 사유: 공개 원문 확인 실패"}}

    def tearDown(self):
        self._tmp.cleanup()

    def run_check(self):
        (self.root / "content" / "2026-10-25.json").write_text(json.dumps(self.day, ensure_ascii=False), encoding="utf-8")
        (self.state / "source.json").write_text(json.dumps(self.src, ensure_ascii=False), encoding="utf-8")
        (self.state / "decisions.json").write_text(json.dumps(self.dec, ensure_ascii=False), encoding="utf-8")
        return edition_check.check(self.state, self.ED, self.root)

    def assert_problem(self, needle):
        problems, _ = self.run_check()
        self.assertTrue(any(needle in p for p in problems), problems)

    def test_complete_edition_passes_and_counts_add_up(self):
        problems, summary = self.run_check()
        self.assertEqual(problems, [])
        self.assertEqual((summary["candidates"], summary["included"], summary["excluded"]), (3, 2, 1))

    def test_incomplete_edition_is_refused(self):
        self.src["status"] = "작성중"
        self.assert_problem("미완성")

    def test_mixed_edition_markers_are_refused(self):
        self.src["markers"]["public_end"]["revision"] = 1
        self.assert_problem("판 혼합")

    def test_missing_marker_is_refused(self):
        del self.src["markers"]["public_start"]
        self.assert_problem("표지 누락")

    def test_silently_dropped_candidate_is_refused(self):
        del self.dec["fake-event-b"]
        self.assert_problem("결정이 없는 후보")

    def test_included_article_must_really_exist(self):
        self.dec["fake-event-a"] = {"id": "fake-missing"}
        self.assert_problem("조용한 누락")

    def test_exclusion_needs_a_reason(self):
        self.dec["fake-event-c"] = {"excluded": " "}
        self.assert_problem("제외 사유")

    def test_public_count_must_match_the_candidates(self):
        self.src["public_count"] = 4
        self.assert_problem("public_count")

    def test_personal_sections_are_not_candidates(self):
        self.src["candidates"][1]["section"] = "부동산"
        self.assert_problem("공개 분야가 아닙니다")

    def test_edition_name_must_match_the_slot(self):
        self.day["edition"] = "저녁판"
        self.assert_problem("판 이름")

    # ---- 노션 v3 (2026-10-24): 개인/공개 구분 없이 브리핑 시작~종료 전체가 게시 대상
    def to_v3(self):
        self.src["format"] = "v3"
        self.src.pop("public_count", None)
        self.src["article_count"] = 3
        self.src["markers"] = {m: {"edition": self.ED, "revision": 2} for m in edition_check.MARKERS_V3}
        self.src["markers"]["saved_line"]["article_count"] = 3

    def test_v3_edition_with_real_estate_career_and_sk_on_passes(self):
        self.to_v3()
        for c, section in zip(self.src["candidates"], ("부동산", "커리어", "SK On")):
            c["section"] = section
        problems, summary = self.run_check()
        self.assertEqual(problems, [])
        self.assertEqual(summary["candidates"], 3)

    def test_kept_earlier_article_card_is_a_valid_candidate(self):
        # 노션 v3 '이전 기사 유지' 카드 (2026-10-25): 이미 실린 기사는 제외로, 처음 싣는 기사는 반영으로 적는다
        self.to_v3()
        self.src["candidates"][0]["status"] = "유지"
        problems, _ = self.run_check()
        self.assertEqual(problems, [])
        self.src["candidates"][0]["status"] = "보류"
        self.assert_problem("신규·진전·보완·유지")

    def test_v3_needs_its_own_markers_and_article_count(self):
        self.to_v3()
        del self.src["markers"]["brief_start"]
        self.assert_problem("표지 누락")
        self.to_v3()
        self.src["article_count"] = 4
        self.assert_problem("article_count")

    def test_unknown_section_is_still_refused_in_v3(self):
        self.to_v3()
        self.src["candidates"][0]["section"] = "가상 분야"
        self.assert_problem("분야가 아닙니다")

    def test_earlier_edition_articles_count_as_present(self):
        self.day["sections"][0]["items"] = [{"id": "fake-one"}]
        self.day["earlier"] = [{"edition": "가상판", "sections": [{"id": "economy", "items": [{"id": "fake-two"}]}]}]
        problems, _ = self.run_check()
        self.assertEqual(problems, [])


def fake_fetch(fail=()):
    """주소에 따라 가상의 응답을 돌려주는 가져오기 함수. fail 에 있는 코드가 주소에 있으면 실패한다."""
    def fetch(url):
        for code in fail:
            if code in url:
                raise OSError("가상 실패")
        if "/api/index/" in url:  # 국내 지수
            return {"closePrice": "5,550.30", "compareToPreviousClosePrice": "16.53", "fluctuationsRatio": "0.30",
                    "compareToPreviousPrice": {"name": "RISING"}, "marketStatus": "CLOSE",
                    "localTradedAt": "2026-10-23T20:15:00+09:00"}
        if "/chart/domestic/" in url:
            return [{"currentPrice": v} for v in (5400.0, 5300.0, 5560.0)]
        if "/index/" in url and "/basic" in url:  # 해외 지수
            return {"closePrice": "7,628.87", "compareToPreviousClosePrice": "-34.56", "fluctuationsRatio": "-0.45",
                    "compareToPreviousPrice": {"name": "FALLING"}, "marketStatus": "OPEN",
                    "localTradedAt": "2026-10-23T13:30:31-04:00"}
        if "pricesByPeriod" in url:
            return {"result": {"lastClosePrice": 100.0, "priceInfos": [{"currentPrice": v} for v in (99.0, 101.0, 102.0)]}}
        if "productDetail" in url:
            month = "26.12." if "CLcv1" in url or "GCcv1" in url else None
            return {"result": {"closePrice": "102.88", "fluctuations": "3.41", "fluctuationsRatio": "3.43",
                               "fluctuationsType": {"name": "RISING"}, "marketStatus": "OPEN", "month": month,
                               "localTradedAt": "2026-10-23T12:21:13-05:00"}}
        if "/ticker" in url:
            return [{"trade_price": 109058000.0, "signed_change_price": 155000.0, "signed_change_rate": 0.0014232849,
                     "change": "RISE", "trade_timestamp": 1792776540721, "prev_closing_price": 108903000.0}]
        if "/candles/" in url:
            return [{"trade_price": v} for v in (109000000.0, 108800000.0, 108900000.0)]
        raise AssertionError("모르는 주소")
    return fetch


class MarketSnapshotTests(unittest.TestCase):
    """맨 위 시장 지표 가져오기. 네트워크를 쓰지 않고 가상의 응답으로 시험한다."""
    NOW = datetime.datetime(2026, 10, 24, 5, 12, tzinfo=market_snapshot.KST)

    def test_twelve_indicators_in_the_fixed_order(self):
        ind, failed = market_snapshot.collect(fake_fetch(), self.NOW)
        self.assertEqual(failed, [])
        self.assertEqual([r["key"] for r in ind["rows"]], list(build.TICKER_KEYS))
        self.assertEqual(market_snapshot.KEYS, list(build.TICKER_KEYS))
        self.assertEqual(ind["as_of"], "2026-10-24T05:12:00+09:00")

    def test_values_keep_the_source_text_and_changes_get_signs(self):
        rows = {r["key"]: r for r in market_snapshot.collect(fake_fetch(), self.NOW)[0]["rows"]}
        k = rows["kospi"]
        self.assertEqual((k["value"], k["change"], k["rate"], k["dir"], k["time"]),
                         ("5,550.30", "+16.53", "+0.30%", "up", "10.23 장마감"))
        s = rows["sp500"]
        self.assertEqual((s["change"], s["rate"], s["dir"], s["time"]), ("-34.56", "-0.45%", "down", "10.24 02:30"))
        self.assertEqual(rows["wti"]["name"], "WTI (26.12)")
        self.assertEqual(rows["usd"]["name"], "미국 USD")
        b = rows["btc"]
        self.assertEqual((b["value"], b["change"], b["rate"], b["dir"]), ("109,058,000", "+155,000", "+0.14%", "up"))

    def test_sparkline_is_scaled_with_the_previous_close(self):
        pts, base = market_snapshot.spark([99.0, 101.0, 102.0], 100.0)
        self.assertEqual(pts, [0, 67, 100])
        self.assertEqual(base, 33)
        self.assertEqual(market_snapshot.spark([5.0], 4.0), ([], None))
        self.assertEqual(len(market_snapshot.spark(list(range(500)))[0]), market_snapshot.SPARK_POINTS)

    def test_one_failure_drops_only_that_indicator(self):
        ind, failed = market_snapshot.collect(fake_fetch(fail=("FX_EURKRW",)), self.NOW)
        self.assertEqual(failed, ["eur"])
        self.assertEqual(len(ind["rows"]), 11)

    def test_total_failure_removes_old_values_from_the_day_file(self):
        ind, failed = market_snapshot.collect(lambda url: (_ for _ in ()).throw(OSError("가상")), self.NOW)
        self.assertIsNone(ind)
        self.assertEqual(len(failed), 12)
        with tempfile.TemporaryDirectory() as tmp:
            day = Path(tmp) / "2026-10-24.json"
            day.write_text(json.dumps({"date": "2026-10-24", "indicators": {"as_of": "옛 값"}}), encoding="utf-8")
            market_snapshot.write_into_day(day, None)
            self.assertNotIn("indicators", json.loads(day.read_text(encoding="utf-8")))

    def test_collected_indicators_pass_the_site_checks(self):
        ind, _ = market_snapshot.collect(fake_fetch(), self.NOW)
        build._check_indicators(ind, "가상")

if __name__ == "__main__":
    unittest.main(verbosity=2)
