#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
공공데이터포털 'aT 일별 도,소매 가격정보' API → data.json 수집기

  python collect.py                      # items.json의 항목을 수집해 data.json 갱신
  python collect.py --items items.kimjang.json   # 다른 목록 사용
  python collect.py --max-calls 3000     # 이번 실행의 API 호출 상한 (기본 8000)
  python collect.py --find 배추           # 품목·품종·등급 코드 찾기

인증키: 환경변수 DATA_GO_KR_KEY (Decoding/Encoding 키 모두 가능)

- 처음 받는 항목은 최근 약 14개월을 내려받고(백필), 이후에는 최근 일주일만 다시 받아 합칩니다.
- 항목이 많아 하루 호출 한도(개발계정 10,000건)에 걸릴 것 같으면 상한에서 멈추고 저장합니다.
  다음 실행 때 못 받은 항목부터 이어서 받으므로, 여러 번(여러 날) 나눠 실행해도 됩니다.
- 데이터가 없는 항목(예: 소매로는 조사하지 않는 품목)은 '비어 있음'으로 기록해 14일간 다시 묻지 않습니다.
"""
import argparse
import datetime as dt
import json
import os
import sys
import time
from urllib.parse import unquote

import requests

URL = "https://apis.data.go.kr/B552845/perDay/price"
HERE = os.path.dirname(os.path.abspath(__file__))
KEEP_DAYS = 430          # 보관 기간: 전년 동월 비교에 필요한 14개월 남짓
REFETCH_DAYS = 7         # 증분 갱신 시 되돌아가 다시 받을 일수(원본 수정 대비)
EMPTY_RECHECK_DAYS = 14  # 데이터가 없던 항목을 다시 확인하는 주기
PAGE_ROWS = 1000         # 명세상 최대값
SAVE_EVERY = 25          # 이 개수마다 중간 저장
KST = dt.timezone(dt.timedelta(hours=9))


class BudgetExceeded(Exception):
    pass


class Api:
    def __init__(self, key, max_calls):
        self.key, self.max_calls, self.calls = key, max_calls, 0
        self.session = requests.Session()

    def page(self, params, page, retries=3):
        q = {"serviceKey": self.key, "returnType": "JSON", "pageNo": page, "numOfRows": PAGE_ROWS}
        q.update(params)
        last = None
        for attempt in range(retries):
            if self.calls >= self.max_calls:
                raise BudgetExceeded()
            self.calls += 1
            try:
                r = self.session.get(URL, params=q, timeout=60)
                r.raise_for_status()
                try:
                    payload = r.json()
                except ValueError:
                    # 게이트웨이 오류는 JSON이 아닌 XML/텍스트로 올 수 있음
                    raise RuntimeError(f"JSON이 아닌 응답: {r.text[:300]}")
                return parse_body(payload)
            except BudgetExceeded:
                raise
            except Exception as e:  # noqa: BLE001
                last = e
                time.sleep(1.5 * (attempt + 1))
        raise RuntimeError(str(last))


def load_json(path, default=None):
    if not os.path.isabs(path):
        path = os.path.join(HERE, path)
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def parse_body(payload):
    """응답 JSON에서 (items, totalCount) 추출. 형태 차이(response 래퍼 유무, 단건 dict)를 흡수."""
    resp = payload.get("response", payload) if isinstance(payload, dict) else {}
    body = resp.get("body")
    if not isinstance(body, dict):
        header = resp.get("header", {})
        raise RuntimeError(f"응답에 body가 없습니다: {json.dumps(header or payload, ensure_ascii=False)[:300]}")
    items = body.get("items")
    if isinstance(items, dict):
        items = items.get("item", [])
    if not items:
        items = []
    if isinstance(items, dict):
        items = [items]
    return items, int(body.get("totalCount") or 0)


def to_price(v):
    try:
        x = float(str(v).replace(",", "").strip())
    except ValueError:
        return None
    return x if x > 0 else None


def fetch_item(api, cfg, start, end):
    """한 항목의 기간 데이터를 전 시장 평균 일별 가격으로 집계해 ({YYYY-MM-DD: price}, meta) 반환."""
    params = {
        "cond[exmn_ymd::GTE]": start.strftime("%Y%m%d"),
        "cond[exmn_ymd::LTE]": end.strftime("%Y%m%d"),
        "cond[se_cd::EQ]": cfg.get("se_cd", "01"),
        "cond[ctgry_cd::EQ]": cfg["ctgry_cd"],
        "cond[item_cd::EQ]": cfg["item_cd"],
    }
    if cfg.get("vrty_cd"):
        params["cond[vrty_cd::EQ]"] = cfg["vrty_cd"]
    if cfg.get("grd_cd"):
        params["cond[grd_cd::EQ]"] = cfg["grd_cd"]
    if cfg.get("sgg_cd"):
        params["cond[sgg_cd::EQ]"] = cfg["sgg_cd"]

    by_day, meta, got, page = {}, {}, 0, 1
    while True:
        rows, total = api.page(params, page)
        for r in rows:
            p = to_price(r.get("exmn_dd_prc"))
            d = str(r.get("exmn_ymd", ""))
            if p is None or len(d) != 8:
                continue
            by_day.setdefault(f"{d[:4]}-{d[4:6]}-{d[6:]}", []).append(p)
            if not meta:
                meta = {"unit": r.get("unit", ""), "unit_sz": r.get("unit_sz", "")}
        got += len(rows)
        if not rows or got >= total:
            break
        page += 1
        time.sleep(0.1)
    daily = {d: round(sum(v) / len(v), 1) for d, v in by_day.items()}
    return daily, meta


def find_codes(keyword):
    codes = load_json("codes.json")
    if not codes:
        sys.exit("codes.json이 없습니다.")
    hits = [i for i in codes["items"] if keyword in i["item_nm"]]
    if not hits:
        print("일치하는 품목이 없습니다.")
    for it in hits:
        print(f'[{it["ctgry_cd"]}] {it["item_nm"]}  item_cd={it["item_cd"]}')
        for v in codes["varieties"]:
            if v["ctgry_cd"] == it["ctgry_cd"] and v["item_cd"] == it["item_cd"]:
                gs = [f'{g["grd_cd"]}={g["grd_nm"]}' for g in codes["grades"]
                      if (g["ctgry_cd"], g["item_cd"], g["vrty_cd"]) == (v["ctgry_cd"], v["item_cd"], v["vrty_cd"])]
                print(f'    vrty_cd={v["vrty_cd"]} {v["vrty_nm"]}   등급: {", ".join(gs)}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--find", metavar="품목명", help="코드 찾기")
    ap.add_argument("--items", default="items.json", help="수집 목록 파일")
    ap.add_argument("--out", default=os.path.join(HERE, "data.json"))
    ap.add_argument("--max-calls", type=int, default=8000, help="이번 실행의 API 호출 상한")
    a = ap.parse_args()

    if a.find:
        return find_codes(a.find)

    key = unquote(os.environ.get("DATA_GO_KR_KEY", "").strip())
    if not key:
        sys.exit("환경변수 DATA_GO_KR_KEY(공공데이터포털 인증키)를 설정해 주세요.")

    items_cfg = load_json(a.items, [])
    if not items_cfg:
        sys.exit(f"{a.items} 가 비어 있거나 없습니다. (python make_items.py 로 만들 수 있습니다)")
    old = {}
    if os.path.exists(a.out):
        with open(a.out, encoding="utf-8") as f:
            old = {i["id"]: i for i in json.load(f).get("items", [])}

    today = dt.datetime.now(KST).date()
    floor = today - dt.timedelta(days=KEEP_DAYS)
    api = Api(key, a.max_calls)
    results = {i: v for i, v in old.items()}      # 처리 전 항목은 이전 값을 그대로 보존
    cfg_by_id = {c["id"]: c for c in items_cfg}
    failed, state = [], {"consec": 0}

    def has_data(c):
        p = old.get(c["id"])
        return bool(p and p.get("daily"))

    def recently_empty(c):
        p = old.get(c["id"])
        if p and not p.get("daily") and p.get("checked"):
            return (today - dt.date.fromisoformat(p["checked"])).days < EMPTY_RECHECK_DAYS
        return False

    # 가벼운 증분 갱신을 먼저, 무거운 백필은 남은 호출 한도 안에서
    todo = [c for c in items_cfg if has_data(c)] + [c for c in items_cfg if not has_data(c) and not recently_empty(c)]
    skipped_empty = sum(1 for c in items_cfg if recently_empty(c))

    new_n = sum(1 for c in todo if not has_data(c))
    print(f"수집 시작: 총 {len(todo)}개 항목 (이어받기 {len(todo) - new_n}개 + 새로 받기 {new_n}개). "
          f"처음엔 오래 걸립니다. 창을 닫지 마세요.", flush=True)

    def build_doc():
        out_items = []
        for c in items_cfg:
            r = results.get(c["id"])
            if r is None:
                continue
            r.update({"label": c["label"], "cat": c.get("cat", ""), "se": c.get("se_cd", "01"),
                      "se_nm": c.get("se_nm", "소매")})
            out_items.append(r)
        return {
            "updated": dt.datetime.now(KST).isoformat(timespec="minutes"),
            "source": "한국농수산식품유통공사(aT) KAMIS · 공공데이터포털 일별 도,소매 가격정보",
            "basis": "조사 시장 단순평균",
            "items": out_items,
        }

    def save():
        doc = build_doc()
        tmp = a.out + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, a.out)

    done, aborted = 0, False
    try:
        for n, cfg in enumerate(todo, 1):
            prev = old.get(cfg["id"])
            daily = {d: p for d, p in (prev["daily"] if prev else [])}
            meta = {k: prev.get(k, "") for k in ("unit", "unit_sz")} if prev else {}
            start = max(floor, dt.date.fromisoformat(max(daily)) - dt.timedelta(days=REFETCH_DAYS)) if daily else floor
            try:
                new, m = fetch_item(api, cfg, start, today)
                state["consec"] = 0
            except BudgetExceeded:
                aborted = True
                print(f"[호출 상한 {a.max_calls}건 도달] 여기까지 저장합니다. 다시 실행하면 이어서 받습니다.")
                break
            except Exception as e:  # noqa: BLE001
                failed.append(cfg["label"])
                state["consec"] += 1
                print(f'[실패] {cfg["label"]}: {e}', file=sys.stderr)
                if state["consec"] >= 3 and not any(results.get(c["id"], {}).get("daily") for c in todo[:n]):
                    print("연속 실패 — 인증키/활용신청 승인/네트워크를 확인해 주세요. 중단합니다.", file=sys.stderr)
                    aborted = True
                    break
                continue
            daily.update(new)
            meta = m or meta
            cutoff = floor.isoformat()
            rows = sorted((d, p) for d, p in daily.items() if d >= cutoff)
            results[cfg["id"]] = {
                "id": cfg["id"], "unit": meta.get("unit", ""), "unit_sz": meta.get("unit_sz", ""),
                "daily": [[d, p] for d, p in rows],
                **({} if rows else {"checked": today.isoformat()}),
            }
            done += 1
            print(f'[{n}/{len(todo)}] {cfg["label"]} ({cfg.get("se_nm", "")}): '
                  f'{len(rows)}일치 · 누적 호출 {api.calls}건' + ("" if rows else " · 데이터 없음"), flush=True)
            if done % SAVE_EVERY == 0:
                save()
    except KeyboardInterrupt:
        print("\n사용자가 중단했습니다. 지금까지의 결과를 저장합니다.")
        aborted = True
    finally:
        if results:
            save()

    got = sum(1 for c in items_cfg if results.get(c["id"], {}).get("daily"))
    empty = sum(1 for c in items_cfg if c["id"] in results and not results[c["id"]].get("daily"))
    pending = sum(1 for c in items_cfg if c["id"] not in results)
    print(f"완료: 데이터 있음 {got}개 · 데이터 없음 {empty}개 · 아직 미수집 {pending}개 · "
          f"이번 호출 {api.calls}건 (최근 확인한 빈 항목 {skipped_empty}개는 건너뜀)")
    if not got:
        sys.exit("수집된 항목이 없습니다. 키 승인 여부와 items.json 코드를 확인해 주세요.")
    if pending:
        print("미수집 항목이 남아 있습니다. 같은 명령을 다시 실행하면 이어서 받습니다.")
    if failed:
        print("실패 항목: " + ", ".join(failed[:20]) + (" …" if len(failed) > 20 else ""), file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
