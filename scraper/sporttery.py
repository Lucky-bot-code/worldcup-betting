"""
中国竞彩（体彩）赔率爬虫 — 从 500.com 抓取实时竞彩赔率。
支持：胜平负(SPF)、让球(RQSPF)、总进球数(ZJQ)、比分(BF)

用法:
    python scraper/sporttery.py          # 打印赔率
    python scraper/sporttery.py --update # 更新到数据库
"""

import re
import ssl
import sys
import urllib.request
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Referer": "https://trade.500.com/jczq/",
}

JCZQ_BASE = "https://trade.500.com/jczq/"


# ─── SPF 抓取 (urllib, 快) ──────────────────────────────────────

def _fetch_spf() -> list[dict]:
    """从默认页面抓取 SPF + RQSPF 赔率"""
    ctx = ssl._create_unverified_context()
    req = urllib.request.Request(JCZQ_BASE, headers=HEADERS)
    resp = urllib.request.urlopen(req, context=ctx, timeout=15)
    html = resp.read().decode("gb18030", errors="replace")
    soup = BeautifulSoup(html, "html.parser")

    results = []
    for tr in soup.select("[data-matchid]"):
        tds = tr.select("td")
        if len(tds) < 6:
            continue
        if "世界杯" not in tds[1].get_text(strip=True):
            continue

        match_time = tds[2].get_text(strip=True)
        teams_raw = tds[3].get_text(strip=True)
        handicap = tds[4].get_text(strip=True)

        if "VS" in teams_raw:
            parts = teams_raw.split("VS", 1)
        elif "vs" in teams_raw:
            parts = teams_raw.split("vs", 1)
        else:
            continue

        team_home = parts[0].strip()
        team_away = parts[1].strip()

        # 解析 SPF 赔率
        bet_cell = tr.select_one(".td-betbtn")
        odds = _parse_spf_cell(bet_cell) if bet_cell else {}

        result = {
            "match_time": match_time,
            "team_home": team_home,
            "team_away": team_away,
            "handicap": handicap,
        }
        result.update(odds)
        results.append(result)

    return results


def _parse_spf_cell(td) -> dict:
    result = {}
    for btn in td.select(".betbtn"):
        dtype = btn.get("data-type", "")
        dval = btn.get("data-value", "")
        sp = btn.get("data-sp", "")
        if dtype == "nspf":
            if dval == "3":
                result["spf_home"] = float(sp)
            elif dval == "1":
                result["spf_draw"] = float(sp)
            elif dval == "0":
                result["spf_away"] = float(sp)
        elif dtype == "spf":
            if dval == "3":
                result["rq_home"] = float(sp)
            elif dval == "1":
                result["rq_draw"] = float(sp)
            elif dval == "0":
                result["rq_away"] = float(sp)
    return result


# ─── Playwright 页面抓取 ────────────────────────────────────────

def _fetch_playwright(playid: int) -> str:
    """用 Playwright 加载指定 playid 的页面（总进球/比分）"""
    import asyncio
    from playwright.async_api import async_playwright

    async def _get():
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent=HEADERS["User-Agent"],
                locale="zh-CN",
            )
            page = await context.new_page()
            if playid == 0:
                url = JCZQ_BASE
            else:
                url = f"{JCZQ_BASE}?playid={playid}&g=2"
            await page.goto(url, timeout=30000, wait_until="networkidle")
            await page.wait_for_timeout(3000)
            html = await page.content()
            await browser.close()
            return html

    return asyncio.run(_get())


# ─── 总进球数 解析 ──────────────────────────────────────────────

def _parse_zjq_html(html: str) -> list[dict]:
    """从 playid=270 页面解析总进球数赔率，按 match_time 索引"""
    soup = BeautifulSoup(html, "html.parser")
    results = []
    for tr in soup.select("[data-matchid]"):
        tds = tr.select("td")
        if len(tds) < 6:
            continue
        if "世界杯" not in tds[1].get_text(strip=True):
            continue

        match_time = tds[2].get_text(strip=True)
        bet_cell = tr.select_one(".td-betbtn")
        if not bet_cell:
            results.append({})
            continue

        goals = {}
        for btn in bet_cell.select(".betbtn"):
            dtype = btn.get("data-type", "")
            dval = btn.get("data-value", "")
            sp = btn.get("data-sp", "")
            if dtype == "jqs":
                try:
                    g = int(dval)
                    goals[f"goals_{g}"] = float(sp)
                except ValueError:
                    pass
        results.append({"match_time": match_time, "goals": goals})

    return results


# ─── 比分 解析 ──────────────────────────────────────────────────

def _parse_bf_html(html: str) -> list[dict]:
    """从 playid=271 页面解析比分赔率。
    每个比赛的 31 个比分在其 fixtureid 对应的 bet-more div 中。
    返回列表，按页面顺序（与 SPF 列表顺序一致）。
    """
    results = []

    # 找到所有 fixtureid 位置，用于分割
    fixture_positions = [
        (m.group(1), m.start())
        for m in re.finditer(r'data-fixtureid="(\d+)"', html)
    ]

    for i, (fid, fpos) in enumerate(fixture_positions):
        next_fpos = fixture_positions[i + 1][1] if i + 1 < len(fixture_positions) else len(html)
        segment = html[fpos:next_fpos]

        scores = {}
        for m in re.finditer(
            r'<p[^>]*class="[^"]*sbetbtn[^"]*"[^>]*data-type="bf"\s+data-value="([^"]+)"\s+data-sp="([^"]+)"',
            segment,
        ):
            score_val = m.group(1)  # e.g. "1:0"
            odds = float(m.group(2))
            scores[f"score_{score_val}"] = odds

        results.append({"fixture_id": fid, "scores": scores})

    return results


# ─── 主抓取函数 ─────────────────────────────────────────────────

def fetch_all_odds() -> list[dict]:
    """抓取所有世界杯比赛的 SPF + 总进球 + 比分赔率，按索引合并"""
    # 1. SPF (urllib, 快)
    spf_list = _fetch_spf()
    print(f"  SPF: {len(spf_list)} 场")

    # 2. 总进球 (Playwright, 最多重试 2 次)
    zjq_list = []
    for attempt in range(3):
        try:
            zjq_html = _fetch_playwright(270)
            zjq_list = _parse_zjq_html(zjq_html)
            print(f"  ZJQ: {len(zjq_list)} 场")
            break
        except Exception as e:
            print(f"  ZJQ 抓取失败 (第{attempt+1}次): {e}")
            if attempt < 2:
                import time
                time.sleep(3)

    # 3. 比分 (Playwright)
    try:
        bf_html = _fetch_playwright(271)
        bf_list = _parse_bf_html(bf_html)
        print(f"  BF: {len(bf_list)} 场")
    except Exception as e:
        print(f"  BF 抓取失败: {e}")
        bf_list = []

    # 按索引合并
    for i, spf in enumerate(spf_list):
        if i < len(zjq_list):
            spf.update(zjq_list[i].get("goals", {}))
        if i < len(bf_list):
            spf.update(bf_list[i].get("scores", {}))

    return spf_list


# ─── 数据库更新 ─────────────────────────────────────────────────

def update_manual_matches() -> dict:
    """抓取赔率并更新 manual_matches 表"""
    import json
    from models import get_session, ManualMatch
    from datetime import datetime, timedelta

    matches = fetch_all_odds()
    if not matches:
        return {"status": "error", "message": "未抓取到世界杯比赛数据"}

    session = get_session()
    updated = 0
    skipped = 0

    try:
        for m in matches:
            date_part = m["match_time"].split(" ")[0]
            month, day = date_part.lstrip("0").split("-")
            match_day = f"{int(month)}/{int(day)}"

            try:
                dt = datetime(2026, int(month), int(day))
                dt_prev = dt - timedelta(days=1)
                match_day_prev = f"{dt_prev.month}/{dt_prev.day}"
            except ValueError:
                match_day_prev = None

            existing = (
                session.query(ManualMatch)
                .filter(
                    ManualMatch.match_day.in_(
                        [match_day, match_day_prev] if match_day_prev else [match_day]
                    ),
                    ManualMatch.team_home == m["team_home"],
                    ManualMatch.team_away == m["team_away"],
                )
                .first()
            )

            if existing:
                existing.odds_home = m.get("spf_home")
                existing.odds_draw = m.get("spf_draw")
                existing.odds_away = m.get("spf_away")

                # 提取总进球赔率
                goals = {}
                for k, v in m.items():
                    if k.startswith("goals_"):
                        goals[k.replace("goals_", "")] = v
                if goals:
                    existing.goals_odds_json = json.dumps(goals, ensure_ascii=False)

                # 提取比分赔率
                scores = {}
                for k, v in m.items():
                    if k.startswith("score_"):
                        scores[k.replace("score_", "")] = v
                if scores:
                    existing.score_odds_json = json.dumps(scores, ensure_ascii=False)

                updated += 1
            else:
                skipped += 1

        session.commit()
        return {
            "status": "ok",
            "fetched": len(matches),
            "updated": updated,
            "skipped": skipped,
            "matches": matches,
        }
    except Exception as e:
        session.rollback()
        return {"status": "error", "message": str(e)}
    finally:
        session.close()


# ─── CLI ────────────────────────────────────────────────────────

if __name__ == "__main__":
    import json

    if "--update" in sys.argv:
        result = update_manual_matches()
        print(f"\n结果: fetched={result.get('fetched')}, updated={result.get('updated')}, skipped={result.get('skipped')}")
    else:
        result = fetch_all_odds()
        print(f"\n共 {len(result)} 场比赛\n")
        for m in result[:2]:
            # 只显示摘要
            summary = {k: v for k, v in m.items() if not k.startswith("score_") and not k.startswith("goals_")}
            summary["goals"] = {k: v for k, v in m.items() if k.startswith("goals_")}
            summary["scores_count"] = len([k for k in m if k.startswith("score_")])
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            print("---")
