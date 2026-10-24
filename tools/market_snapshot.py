#!/usr/bin/env python3
"""시장 지표 12개를 사이트를 만드는 시점에 가져와 그날 내용 파일의 indicators 에 넣는다.

    python3 tools/market_snapshot.py --day content/2026-10-24.json   # 가져와서 파일에 쓴다
    python3 tools/market_snapshot.py --print                          # 가져온 값만 보여 준다

지표와 순서는 INDICATORS 한 곳에서 정한다: 코스피, 코스닥, S&P 500, 나스닥, 다우존스,
미국 USD, 유럽 EUR, WTI, 국제 금, 미국채 10년, 달러인덱스, 비트코인.
자료: 네이버페이 증권 화면이 쓰는 자료(공식 공개 API 아님, 2026-10-24 사용자 결정)와 업비트 공개 API(비트코인).
값은 원본 표기를 그대로 쓰고, 등락에는 부호를 붙인다. 작은 차트는 그날(또는 마지막 거래일) 흐름을 0~100 으로 줄여 담는다.
하나가 실패하면 그 지표만 빠지고, 모두 실패하면 indicators 를 지운다 (오래된 값을 남기지 않는다).
표준 라이브러리만 쓴다. 네트워크 없이 시험할 수 있게 가져오기 함수(fetch)를 바꿔 끼울 수 있다.
"""
import argparse
import datetime
import json
import sys
import urllib.request
from pathlib import Path

KST = datetime.timezone(datetime.timedelta(hours=9))
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/605.1.15 "
      "(KHTML, like Gecko) Version/17.0 Safari/605.1.15")
NAVER_M = "https://m.stock.naver.com"
NAVER_API = "https://api.stock.naver.com"
UPBIT = "https://api.upbit.com/v1"
SOURCE_NAME = "네이버페이 증권·업비트"
SPARK_POINTS = 60

# (키, 화면 이름, 종류, 코드, 분류, 차트 분류)
INDICATORS = [
    ("kospi", "코스피", "domestic", "KOSPI", None, None),
    ("kosdaq", "코스닥", "domestic", "KOSDAQ", None, None),
    ("sp500", "S&P 500", "world", ".INX", None, None),
    ("nasdaq", "나스닥", "world", ".IXIC", None, None),
    ("dow", "다우존스", "world", ".DJI", None, None),
    ("usd", "미국 USD", "marketindex", "FX_USDKRW", "exchange", "marketindex"),
    ("eur", "유럽 EUR", "marketindex", "FX_EURKRW", "exchange", "marketindex"),
    ("wti", "WTI", "marketindex", "CLcv1", "energy", "futures"),
    ("gold", "국제 금", "marketindex", "GCcv1", "metals", "futures"),
    ("us10y", "미국채 10년", "marketindex", "US10YT=RR", "bond", "governmentBond"),
    ("dxy", "달러인덱스", "marketindex", ".DXY", "exchange", "index"),
    ("btc", "비트코인", "upbit", "KRW-BTC", None, None),
]
KEYS = [k for k, *_ in INDICATORS]
DIR_BY_NAME = {"RISING": "up", "UPPER_LIMIT": "up", "FALLING": "down", "LOWER_LIMIT": "down"}


def fetch_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": NAVER_M + "/"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ---------------------------------------------------------------- 모양 맞추기

def _num(text):
    return float(str(text).replace(",", "").replace("+", "").strip())


def _signed(text, direction):
    """'16.53' / '-12.41' → 방향에 맞춘 부호 문자열. 보합이면 부호 없이."""
    body = str(text).replace("+", "").replace("-", "").strip()
    if direction == "up":
        return "+" + body
    if direction == "down":
        return "-" + body
    return body


def _direction(name=None, value=None):
    if name in DIR_BY_NAME:
        return DIR_BY_NAME[name]
    if value is not None:
        v = _num(value)
        return "up" if v > 0 else "down" if v < 0 else "flat"
    return "flat"


def _parse_time(text):
    """네이버의 시각 표기('2026-10-23T13:30:31-04:00' 등)를 시간대가 있는 시각으로."""
    return datetime.datetime.fromisoformat(text)


def time_label(at, closed):
    """장이 끝났으면 거래일(그 시장의 날짜)과 '장마감', 아니면 한국 시각."""
    if closed:
        return "%02d.%02d 장마감" % (at.month, at.day)
    k = at.astimezone(KST)
    return "%02d.%02d %02d:%02d" % (k.month, k.day, k.hour, k.minute)


def spark(prices, base=None):
    """흐름을 최대 SPARK_POINTS 개의 0~100 정수로 줄인다 (100이 가장 높음). base(전 거래일 종가)도 같은 눈금으로."""
    vals = [float(p) for p in prices if p is not None]
    if len(vals) < 2:
        return [], None
    if len(vals) > SPARK_POINTS:
        step = (len(vals) - 1) / (SPARK_POINTS - 1)
        vals = [vals[round(i * step)] for i in range(SPARK_POINTS)]
    lo = min(vals + ([base] if base is not None else []))
    hi = max(vals + ([base] if base is not None else []))
    if hi == lo:
        return [50] * len(vals), (50 if base is not None else None)
    scale = lambda v: round((v - lo) / (hi - lo) * 100)
    return [scale(v) for v in vals], (scale(base) if base is not None else None)


def _row(key, name, value, change, rate, direction, label, prices, base, extra=""):
    pts, b = spark(prices, base)
    return {"key": key, "name": name + extra, "value": value, "change": _signed(change, direction),
            "rate": _signed(rate, direction) + "%", "dir": direction, "time": label, "spark": pts, "base": b}


# ---------------------------------------------------------------- 자료별 읽기

def domestic(key, name, code, fetch):
    q = fetch("%s/api/index/%s/basic" % (NAVER_M, code))
    direction = _direction((q.get("compareToPreviousPrice") or {}).get("name"), q.get("compareToPreviousClosePrice"))
    at = _parse_time(q["localTradedAt"])
    day = at.strftime("%Y%m%d")
    series = fetch("%s/chart/domestic/index/%s/minute?startDateTime=%s0900&endDateTime=%s1530"
                   % (NAVER_API, code, day, day))
    prices = [p.get("currentPrice") for p in series] if isinstance(series, list) else []
    base = _num(q["closePrice"]) - _num(q["compareToPreviousClosePrice"])
    return _row(key, name, q["closePrice"], q["compareToPreviousClosePrice"], q["fluctuationsRatio"], direction,
                time_label(at, q.get("marketStatus") != "OPEN"), prices, base)


def _period_series(code, category, chart_type, fetch):
    data = fetch("%s/front-api/chart/pricesByPeriod?reutersCode=%s&category=%s&chartInfoType=%s&scriptChartType=day"
                 % (NAVER_M, code, category, chart_type))
    result = (data or {}).get("result") or {}
    prices = [p.get("currentPrice") for p in result.get("priceInfos") or []]
    base = result.get("lastClosePrice")
    return prices, (float(base) if base is not None else None)


def world(key, name, code, fetch):
    q = fetch("%s/index/%s/basic" % (NAVER_API, code))
    direction = _direction((q.get("compareToPreviousPrice") or {}).get("name"), q.get("compareToPreviousClosePrice"))
    prices, base = _period_series(code, "major", "index", fetch)
    return _row(key, name, q["closePrice"], q["compareToPreviousClosePrice"], q["fluctuationsRatio"], direction,
                time_label(_parse_time(q["localTradedAt"]), q.get("marketStatus") != "OPEN"), prices, base)


def marketindex(key, name, code, category, chart_type, fetch):
    q = fetch("%s/front-api/marketIndex/productDetail?category=%s&reutersCode=%s" % (NAVER_M, category, code))["result"]
    direction = _direction((q.get("fluctuationsType") or {}).get("name"), q.get("fluctuations"))
    prices, base = _period_series(code, category, chart_type, fetch)
    month = q.get("month")
    extra = " (%s)" % month.rstrip(".") if month else ""
    return _row(key, name, q["closePrice"], q["fluctuations"], q["fluctuationsRatio"], direction,
                time_label(_parse_time(q["localTradedAt"]), q.get("marketStatus") == "CLOSE"), prices, base, extra)


def upbit(key, name, code, fetch):
    q = fetch("%s/ticker?markets=%s" % (UPBIT, code))[0]
    direction = {"RISE": "up", "FALL": "down"}.get(q.get("change"), "flat")
    candles = fetch("%s/candles/minutes/10?market=%s&count=144" % (UPBIT, code))
    prices = [c.get("trade_price") for c in reversed(candles)] if isinstance(candles, list) else []
    at = datetime.datetime.fromtimestamp(q["trade_timestamp"] / 1000, KST)
    return _row(key, name, "{:,.0f}".format(q["trade_price"]), "{:,.0f}".format(abs(q["signed_change_price"])),
                "%.2f" % abs(q["signed_change_rate"] * 100), direction, time_label(at, False), prices,
                q.get("prev_closing_price"))


def collect(fetch=fetch_json, now=None):
    """지표마다 가져와 줄 목록을 만든다. 돌려주는 값: (indicators 사전 또는 None, 실패한 키 목록)."""
    rows, failed = [], []
    for key, name, kind, code, category, chart_type in INDICATORS:
        try:
            if kind == "domestic":
                rows.append(domestic(key, name, code, fetch))
            elif kind == "world":
                rows.append(world(key, name, code, fetch))
            elif kind == "marketindex":
                rows.append(marketindex(key, name, code, category, chart_type, fetch))
            else:
                rows.append(upbit(key, name, code, fetch))
        except Exception:  # 한 지표의 실패가 다른 지표와 게시를 막지 않게 한다
            failed.append(key)
    if not rows:
        return None, failed
    now = now or datetime.datetime.now(KST)
    return {"as_of": now.isoformat(timespec="seconds"), "source": SOURCE_NAME, "rows": rows}, failed


def write_into_day(day_path, indicators):
    p = Path(day_path)
    day = json.loads(p.read_text(encoding="utf-8"))
    if indicators:
        day["indicators"] = indicators
    else:
        day.pop("indicators", None)
    p.write_text(json.dumps(day, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main(argv=None):
    ap = argparse.ArgumentParser(description="시장 지표 12개를 지금 시점 값으로 가져온다")
    ap.add_argument("--day", help="indicators 를 넣을 content/날짜.json")
    ap.add_argument("--print", action="store_true", help="가져온 값을 보여 주기만 한다")
    args = ap.parse_args(argv)
    indicators, failed = collect()
    if failed:
        print("가져오지 못한 지표: %s" % ", ".join(failed), file=sys.stderr)
    if args.print or not args.day:
        print(json.dumps(indicators, ensure_ascii=False, indent=1))
        return 0
    write_into_day(args.day, indicators)
    if indicators:
        print("시장 지표 %d개를 %s 기준으로 넣었습니다" % (len(indicators["rows"]), indicators["as_of"]))
    else:
        print("시장 지표를 하나도 가져오지 못해 indicators 를 비웠습니다", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
