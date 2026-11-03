#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
codes.json(코드표)에서 모든 품목·품종의 수집 목록 items.json을 만듭니다.

  python make_items.py                 # 소매+도매(중도매) 전체
  python make_items.py --se 01         # 소매만 (호출량이 절반)
  python make_items.py --cats 200 600  # 채소류·수산물만 (부류코드)
  python make_items.py --sgg 1101      # 서울 시장만 (호출량 감소)

품목·품종마다 등급은 상품(04) → 중품(05) → 목록의 첫 등급 순으로 하나를 고릅니다.
(유기농 등 친환경 구분은 제외했습니다.)
"""
import argparse
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
CATS = {"100": "식량작물", "200": "채소류", "300": "특용작물", "400": "과일류", "500": "축산물", "600": "수산물"}
SES = {"01": "소매", "02": "도매"}
GRADE_PREF = ["04", "05"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--se", default="01,02", help="구분코드 쉼표 목록 (01 소매, 02 중도매)")
    ap.add_argument("--cats", nargs="*", help="포함할 부류코드 (기본: 전체)")
    ap.add_argument("--sgg", help="시군구코드 하나로 한정 (예: 1101 서울)")
    ap.add_argument("--out", default=os.path.join(HERE, "items.json"))
    a = ap.parse_args()

    codes = json.load(open(os.path.join(HERE, "codes.json"), encoding="utf-8"))
    item_nm = {(i["ctgry_cd"], i["item_cd"]): i["item_nm"] for i in codes["items"]}
    grades = {}
    for g in codes["grades"]:
        grades.setdefault((g["ctgry_cd"], g["item_cd"], g["vrty_cd"]), []).append(g["grd_cd"])

    # 같은 이름이 겹치는지 먼저 센다
    def base_label(cat, ic, v):
        n = item_nm.get((cat, ic), ic)
        return n if v["vrty_nm"] in (n, "") else f'{n} · {v["vrty_nm"]}'

    varieties = [v for v in codes["varieties"] if (not a.cats or v["ctgry_cd"] in a.cats)]
    counts = {}
    for v in varieties:
        lab = base_label(v["ctgry_cd"], v["item_cd"], v)
        counts[lab] = counts.get(lab, 0) + 1

    out = []
    for se in [s.strip() for s in a.se.split(",") if s.strip()]:
        for v in varieties:
            cat, ic, vc = v["ctgry_cd"], v["item_cd"], v["vrty_cd"]
            gl = grades.get((cat, ic, vc), [])
            if not gl:
                continue
            grd = next((g for g in GRADE_PREF if g in gl), gl[0])
            lab = base_label(cat, ic, v)
            if counts[lab] > 1:
                lab += f" ({ic})"
            cfg = {
                "id": f"{se}-{cat}-{ic}-{vc}",
                "label": lab, "cat": CATS.get(cat, "기타"),
                "se_cd": se, "se_nm": SES.get(se, se),
                "ctgry_cd": cat, "item_cd": ic, "vrty_cd": vc, "grd_cd": grd,
            }
            if a.sgg:
                cfg["sgg_cd"] = a.sgg
            out.append(cfg)
    # 그래도 이름이 같은 항목은 품종코드를 덧붙여 구분
    seen = {}
    for c in out:
        seen.setdefault((c["se_cd"], c["label"]), []).append(c)
    for group in seen.values():
        if len(group) > 1:
            for c in group:
                c["label"] += f' #{c["vrty_cd"]}'
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=0)
    print(f"{len(out)}개 항목을 {a.out}에 저장했습니다.")


if __name__ == "__main__":
    main()
