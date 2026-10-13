#!/usr/bin/env python3
"""뉴스 후보 정리: 같은 사건을 다룬 후보를 하나로 묶는다.

입력은 후보 목록(JSON 배열)이다. 각 후보는 title, url, source, published_at, topic 을 가진다.
이 도구는 후보를 "수집"하지 않는다. 수집한 목록을 받아 중복을 없애고, 사건별로 가장 이른 보도와
독립 출처 수를 알려 준다. 기사 원문은 저장하지 않는다.

    python3 tools/candidates.py 후보.json            # 묶은 결과를 JSON 으로 출력
    python3 tools/candidates.py 후보.json --content  # content/ 에 이미 실린 출처는 제외
"""
import argparse
import difflib
import json
import re
import sys
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

ROOT_DIR = Path(__file__).resolve().parent.parent
SIMILAR = 0.72
TRACKING = re.compile(r"^(utm_|fbclid$|gclid$|ref$|cmpid$)")


def normalize_url(url):
    """추적용 파라미터와 #조각을 떼어 같은 기사를 같은 주소로 본다."""
    parts = urlsplit(url.strip())
    query = urlencode([(k, v) for k, v in parse_qsl(parts.query) if not TRACKING.match(k)])
    return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path.rstrip("/"), query, ""))


def _key(title):
    return re.sub(r"[\W_]+", "", title.lower())


def cluster(items, published_urls=()):
    published = {normalize_url(u) for u in published_urls}
    groups = []
    for item in items:
        if not item.get("title") or not item.get("url"):
            raise ValueError("후보에는 title 과 url 이 있어야 합니다")
        url = normalize_url(item["url"])
        if url in published:
            continue
        entry = dict(item, url=url)
        key = _key(item["title"])
        for g in groups:
            if g["topic"] != item.get("topic"):
                continue
            same_url = any(m["url"] == url for m in g["members"])
            if same_url or difflib.SequenceMatcher(None, key, g["key"]).ratio() >= SIMILAR:
                g["members"].append(entry)
                break
        else:
            groups.append({"topic": item.get("topic"), "key": key, "members": [entry]})

    out = []
    for g in groups:
        members = sorted(g["members"], key=lambda m: m.get("published_at") or "")
        out.append({
            "topic": g["topic"],
            "title": members[0]["title"],
            "first": members[0],
            "independent_sources": len({m.get("source") for m in members if m.get("source")}),
            "members": members,
        })
    out.sort(key=lambda g: (-g["independent_sources"], g["first"].get("published_at") or ""))
    return out


def published_urls(root):
    urls = set()
    for path in (Path(root) / "content").glob("[0-9]*.json"):
        day = json.loads(path.read_text(encoding="utf-8"))
        for sec in day.get("sections", []):
            for item in sec.get("items", []):
                urls.update(s["url"] for s in item.get("sources") or [])
    return urls


def main(argv=None):
    ap = argparse.ArgumentParser(description="뉴스 후보를 사건별로 묶는다")
    ap.add_argument("file")
    ap.add_argument("--content", action="store_true", help="이미 실린 출처는 제외")
    args = ap.parse_args(argv)
    items = json.loads(Path(args.file).read_text(encoding="utf-8"))
    groups = cluster(items, published_urls(ROOT_DIR) if args.content else ())
    json.dump(groups, sys.stdout, ensure_ascii=False, indent=2)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
