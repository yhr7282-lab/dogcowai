"""수집·검증 도구 테스트. 실행: python3 tools/test_pipeline.py (네트워크를 쓰지 않는다)"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import candidates  # noqa: E402
import check_links  # noqa: E402


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


if __name__ == "__main__":
    unittest.main(verbosity=2)
