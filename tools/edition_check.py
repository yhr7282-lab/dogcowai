#!/usr/bin/env python3
"""한 회차의 공개 변환 결과를 게시 전에 맞춰 보는 검사기.

노션 원본을 읽은 제작 단계가 두 파일을 남긴다 (공개 저장소 밖, 실행기의 회차 폴더):
    source.json     판 정보(판 ID·형식·개정·상태·결과·기사 수·표지들)와 후보 목록
형식은 두 가지다.
    v3 (2026-10-24 저녁부터): 개인/공개 구분 없이 '브리핑 시작'~'브리핑 종료' 전체가 게시 대상.
        표지 header·brief_start·brief_end·saved_line, 기사 수 article_count, 분야에 부동산·커리어·SK On 포함.
    v2 (이전): '공개 시작'~'공개 종료'만 게시. 표지 header·public_start·public_end·saved_line, 수 public_count.
    decisions.json  후보마다 반영한 기사 id 또는 제외 사유
이 검사기는 그 두 파일과 content/날짜.json 을 대조한다.

    python3 tools/edition_check.py --state 회차폴더 --edition 2026-10-25-AM [--root 저장소]

확인하는 것
- 판 ID·형식(v2/v3)·상태(검증완료)와, 첫 줄·시작·종료·저장 완료 표지의 판 ID·개정이 모두 같은지
- 저장 완료 줄의 기사 수 = 후보 수
- 후보 수 = 반영 수 + 이유 있는 제외 수 (후보마다 결정이 정확히 하나)
- 후보의 분야가 그 형식의 분야인지, 상태가 신규·진전·보완·유지인지
- 반영한 기사 id 가 그날 내용 파일(지금 판 또는 이전 판)에 실제로 있는지, 판 이름이 오전/저녁과 맞는지
문제를 알릴 때는 파일·항목·키만 적고 원문 문장은 적지 않는다. 표준 라이브러리만 쓴다.
"""
import argparse
import datetime
import json
import re
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
EDITION_ID = re.compile(r"^(\d{4}-\d{2}-\d{2})-(AM|PM)$")
KEY = re.compile(r"^[a-z0-9][a-z0-9-]{0,79}$")
PUBLIC_SECTIONS = ("경제", "주요 사건", "주식", "AI", "반도체", "배터리", "자동차")
SECTIONS_V3 = PUBLIC_SECTIONS + ("부동산", "커리어", "SK On")
STATUSES = ("신규", "진전", "보완", "유지")   # 유지: 새 기사 없는 분야에 남긴 이전 기사 카드 (2026-10-25)
EDITION_NAME = {"AM": ("오전판",), "PM": ("저녁판", "오후판")}
MARKERS = ("header", "public_start", "public_end", "saved_line")
MARKERS_V3 = ("header", "brief_start", "brief_end", "saved_line")
FORMATS = {  # 형식: (표지, 기사 수 항목, 분야)
    "v2": (MARKERS, "public_count", PUBLIC_SECTIONS),
    "v3": (MARKERS_V3, "article_count", SECTIONS_V3),
}
REASON_MAX = 200


def _load(path, problems):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        problems.append("%s: 파일이 없습니다" % Path(path).name)
    except json.JSONDecodeError as e:
        problems.append("%s: JSON 형식 오류 (%d번째 줄)" % (Path(path).name, e.lineno))
    return None


def _published_ids(day):
    ids = {i["id"] for s in day.get("sections") or [] for i in s.get("items") or []}
    for g in day.get("earlier") or []:
        ids |= {i["id"] for s in g.get("sections") or [] for i in s.get("items") or []}
    return ids


def check(state_dir, edition, root=ROOT_DIR):
    """문제 목록과 요약(dict)을 돌려준다. 문제가 없으면 목록이 비어 있다."""
    problems = []
    m = EDITION_ID.match(edition or "")
    if not m:
        return ["판 ID 형식이 YYYY-MM-DD-AM/PM 이 아닙니다"], {}
    date, slot = m.group(1), m.group(2)
    state_dir = Path(state_dir)
    src = _load(state_dir / "source.json", problems)
    dec = _load(state_dir / "decisions.json", problems)
    day = _load(Path(root) / "content" / ("%s.json" % date), problems)
    if problems:
        return problems, {}

    # ---- 판 정보와 표지
    if src.get("edition") != edition:
        problems.append("source.json: edition 이 이번 판(%s)과 다릅니다" % edition)
    fmt = src.get("format")
    if fmt not in FORMATS:
        problems.append("source.json: format 은 v2 또는 v3 이어야 합니다")
        fmt = "v3"
    marker_names, count_key, sections = FORMATS[fmt]
    if src.get("status") != "검증완료":
        problems.append("source.json: status 가 검증완료가 아닙니다 (미완성 판)")
    if src.get("result") not in ("UPDATED", "NO_CHANGE"):
        problems.append("source.json: result 는 UPDATED 또는 NO_CHANGE 여야 합니다")
    rev = src.get("revision")
    if not isinstance(rev, int) or rev < 1:
        problems.append("source.json: revision 은 1 이상의 정수여야 합니다")
    markers = src.get("markers") if isinstance(src.get("markers"), dict) else {}
    for name in marker_names:
        mk = markers.get(name)
        if not isinstance(mk, dict):
            problems.append("source.json: markers.%s 가 없습니다 (표지 누락)" % name)
            continue
        if mk.get("edition") != edition or mk.get("revision") != rev:
            problems.append("source.json: markers.%s 의 판 ID·개정이 첫 줄과 다릅니다 (판 혼합)" % name)
    cands = src.get("candidates")
    if not isinstance(cands, list):
        problems.append("source.json: candidates 목록이 없습니다")
        cands = []
    saved = markers.get("saved_line") if isinstance(markers.get("saved_line"), dict) else {}
    if src.get(count_key) != len(cands):
        problems.append("source.json: %s(%s)와 후보 수(%d)가 다릅니다" % (count_key, src.get(count_key), len(cands)))
    if saved and saved.get(count_key) != src.get(count_key):
        problems.append("source.json: 저장 완료 줄의 기사 수와 %s 가 다릅니다" % count_key)

    # ---- 후보
    keys = []
    for n, c in enumerate(cands):
        where = "candidates[%d]" % n
        if not isinstance(c, dict):
            problems.append("source.json: %s 형식이 잘못됐습니다" % where)
            continue
        key = c.get("key")
        if not isinstance(key, str) or not KEY.match(key):
            problems.append("source.json: %s.key 는 영문 소문자·숫자·하이픈이어야 합니다" % where)
            continue
        if key in keys:
            problems.append("source.json: 키 '%s' 가 중복됩니다" % key)
        keys.append(key)
        if c.get("section") not in sections:
            problems.append("source.json: %s(%s).section 이 %s 형식의 공개 분야가 아닙니다" % (where, key, fmt))
        if c.get("status") not in STATUSES:
            problems.append("source.json: %s(%s).status 는 신규·진전·보완·유지 중 하나여야 합니다" % (where, key))
        try:
            if datetime.date.fromisoformat(c.get("event_date") or "") > datetime.date.fromisoformat(date):
                problems.append("source.json: %s(%s).event_date 가 판 날짜보다 뒤입니다" % (where, key))
        except (TypeError, ValueError):
            problems.append("source.json: %s(%s).event_date 는 YYYY-MM-DD 여야 합니다" % (where, key))

    # ---- 결정: 후보마다 정확히 하나
    if not isinstance(dec, dict):
        problems.append("decisions.json: {키: 결정} 형태여야 합니다")
        dec = {}
    missing = [k for k in keys if k not in dec]
    extra = [k for k in dec if k not in keys]
    if missing:
        problems.append("decisions.json: 결정이 없는 후보 %d건: %s" % (len(missing), ", ".join(missing)))
    if extra:
        problems.append("decisions.json: 후보에 없는 키 %d건: %s" % (len(extra), ", ".join(extra)))
    published = _published_ids(day)
    included, excluded = [], []
    for key in keys:
        d = dec.get(key)
        if d is None:
            continue
        if not isinstance(d, dict) or (("id" in d) == ("excluded" in d)):
            problems.append("decisions.json: %s 는 id 또는 excluded 중 하나만 있어야 합니다" % key)
            continue
        if "id" in d:
            if d["id"] not in published:
                problems.append("decisions.json: %s 의 기사 id 가 %s.json 에 없습니다 (조용한 누락)" % (key, date))
            included.append(key)
        else:
            reason = d["excluded"]
            if not isinstance(reason, str) or not reason.strip() or len(reason) > REASON_MAX:
                problems.append("decisions.json: %s 의 제외 사유가 비었거나 너무 깁니다" % key)
            excluded.append(key)

    # ---- 내용 파일의 판 이름
    if day.get("edition") not in EDITION_NAME[slot]:
        problems.append("%s.json: edition 이 %s 판 이름(%s)과 맞지 않습니다" % (date, slot, "/".join(EDITION_NAME[slot])))

    summary = {"edition": edition, "revision": rev, "candidates": len(keys),
               "included": len(included), "excluded": len(excluded)}
    if not problems and len(keys) != len(included) + len(excluded):
        problems.append("후보 수(%d) ≠ 반영(%d) + 제외(%d)" % (len(keys), len(included), len(excluded)))
    return problems, summary


def main(argv=None):
    ap = argparse.ArgumentParser(description="한 회차의 공개 변환 결과 검사")
    ap.add_argument("--state", required=True, help="회차 폴더 (source.json, decisions.json)")
    ap.add_argument("--edition", required=True, help="판 ID (YYYY-MM-DD-AM/PM)")
    ap.add_argument("--root", default=str(ROOT_DIR), help="저장소 루트")
    args = ap.parse_args(argv)
    problems, summary = check(args.state, args.edition, Path(args.root))
    if problems:
        print("회차 검사 실패 (게시하지 않음):", file=sys.stderr)
        for p in problems:
            print("  " + p, file=sys.stderr)
        return 1
    print("회차 검사 통과: 판 %(edition)s 개정 %(revision)s · 후보 %(candidates)d = 반영 %(included)d + 제외 %(excluded)d"
          % summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
