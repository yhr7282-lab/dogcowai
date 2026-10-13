#!/usr/bin/env python3
"""출처 링크 검사: 하루치 내용 파일의 모든 출처 주소를 실제로 열어 본다.

    python3 tools/check_links.py content/2026-10-13.json

결과는 네 가지다.
- ok          열리고 본문이 있다
- blocked     자동 접근이 막혔다 (401/403/429). 링크 오류가 아니다. 사람이 브라우저로 확인한다
- placeholder 열리지만 안내·오류 페이지다 (예: Coming Soon, 페이지를 찾을 수 없습니다)
- broken      열리지 않는다 (404 등)

이 도구는 "링크가 살아 있는지"만 본다. 링크의 내용이 기사의 숫자를 뒷받침하는지는
사람이나 AI가 원문을 읽고 대조해야 한다. broken 이나 placeholder 가 있으면 종료 코드 1.
"""
import html
import json
import re
import sys
import urllib.error
import urllib.request

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
      "(KHTML, like Gecko) Version/17.5 Safari/605.1.15")
PLACEHOLDER = re.compile(r"coming soon|page not found|찾을 수 없습니다|삭제되었거나|존재하지 않는", re.I)
LABEL = {"ok": "열림", "blocked": "접근 제한", "placeholder": "안내 페이지", "broken": "열리지 않음"}


def classify(status, title, text):
    if status in (401, 403, 429):
        return "blocked"
    if status is None or status >= 400:
        return "broken"
    if PLACEHOLDER.search(title) or PLACEHOLDER.search(text[:1500]) and len(text) < 3000:
        return "placeholder"
    return "ok"


def collect_urls(day):
    urls = []
    lists = [(day.get("market") or {}).get("sources") or []]
    lists += [item.get("sources") or [] for sec in day.get("sections", []) for item in sec.get("items", [])]
    for sources in lists:
        for s in sources:
            if s["url"] not in urls:
                urls.append(s["url"])
    return urls


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "ko,en;q=0.8"})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            status, body = r.status, r.read(1_500_000).decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        status, body = e.code, e.read(300_000).decode("utf-8", "ignore")
    except Exception:
        return None, "", ""
    m = re.search(r"(?is)<title[^>]*>(.*?)</title>", body)
    title = html.unescape(m.group(1)).strip() if m else ""
    body = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", body)
    text = re.sub(r"\s+", " ", html.unescape(re.sub(r"(?s)<[^>]+>", " ", body)))
    return status, title, text


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print(__doc__)
        return 2
    day = json.load(open(argv[0], encoding="utf-8"))
    bad = 0
    for url in collect_urls(day):
        status, title, text = fetch(url)
        kind = classify(status, title, text)
        bad += kind in ("broken", "placeholder")
        print("%-8s %-4s %s\n         %s" % (LABEL[kind], status or "-", url, title[:70]))
    print("\n문제 링크 %d개" % bad)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
