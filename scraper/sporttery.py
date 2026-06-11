"""
中国竞彩（体彩）赔率爬虫 — 从 500.com 抓取实时竞彩 SPF 赔率。
数据源: https://trade.500.com/jczq/
"""

import re
import ssl
import urllib.request
from bs4 import BeautifulSoup


HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Referer": "https://trade.500.com/jczq/",
}

JCZQ_URL = "https://trade.500.com/jczq/"


def _parse_odds(raw: str) -> list[float]:
    """
    解析 500.com 拼接的赔率字符串。
    如 "1.264.459.002.003.253.11" → [1.26, 4.45, 9.00, 2.00, 3.25, 3.11]
    前3个是 SPF（胜平负），后3个是让球 SPF。
    支持前导中文（如"未开售2.023.822.69"）。
    """
    # 跳过前导非数字字符，直到第一个数字
    start = 0
    while start < len(raw) and not raw[start].isdigit():
        start += 1
    raw = raw[start:]

    values = []
    i = 0
    while i < len(raw):
        dot = raw.find(".", i)
        if dot == -1:
            break
        int_part = raw[i:dot]
        if not int_part:
            break
        frac = raw[dot + 1:dot + 3]
        values.append(float(f"{int_part}.{frac}"))
        i = dot + 3
    return values


def fetch_matches() -> list[dict]:
    """抓取当前竞彩足球全部世界杯比赛及赔率"""
    ctx = ssl._create_unverified_context()
    req = urllib.request.Request(JCZQ_URL, headers=HEADERS)
    resp = urllib.request.urlopen(req, context=ctx, timeout=15)
    html = resp.read().decode("gb18030", errors="replace")

    soup = BeautifulSoup(html, "html.parser")
    results = []

    for tr in soup.select("[data-matchid]"):
        tds = tr.select("td")
        if len(tds) < 6:
            continue

        tournament = tds[1].get_text(strip=True)
        if "世界杯" not in tournament and "world cup" not in tournament.lower():
            continue

        match_num = tds[0].get_text(strip=True)
        match_time = tds[2].get_text(strip=True)       # e.g. "06-12 03:00"
        teams_raw = tds[3].get_text(strip=True)         # e.g. "墨西哥VS南非"
        handicap = tds[4].get_text(strip=True)          # e.g. "让球0-1"
        odds_raw = tds[5].get_text(strip=True)

        # 分离主客队
        if "VS" in teams_raw:
            parts = teams_raw.split("VS", 1)
            team_home = parts[0].strip()
            team_away = parts[1].strip()
        elif "vs" in teams_raw:
            parts = teams_raw.split("vs", 1)
            team_home = parts[0].strip()
            team_away = parts[1].strip()
        else:
            continue

        odds_values = _parse_odds(odds_raw)

        if len(odds_values) < 3:
            continue

        result = {
            "match_num": match_num,
            "match_time": match_time,
            "team_home": team_home,
            "team_away": team_away,
            "handicap": handicap,
            "odds_home": odds_values[0],
            "odds_draw": odds_values[1],
            "odds_away": odds_values[2],
        }

        if len(odds_values) >= 6:
            result["rq_odds_home"] = odds_values[3]
            result["rq_odds_draw"] = odds_values[4]
            result["rq_odds_away"] = odds_values[5]

        results.append(result)

    return results


def update_manual_matches() -> dict:
    """
    将抓取到的竞彩赔率更新到 manual_matches 表中。
    按队伍名 + 日期匹配（500.com 用北京时间，DB 可能差一天）。
    """
    from models import get_session, ManualMatch
    from datetime import datetime, timedelta

    matches = fetch_matches()
    if not matches:
        return {"status": "error", "message": "未抓取到世界杯比赛数据"}

    session = get_session()
    updated = 0
    skipped = 0

    try:
        for m in matches:
            # match_time 格式: "06-12 03:00" → parse to get month/day
            date_part = m["match_time"].split(" ")[0]  # "06-12"
            month, day = date_part.lstrip("0").split("-")
            match_day = f"{int(month)}/{int(day)}"  # "6/12"

            # 也尝试前一天（因为 03:00 北京时间的比赛在 DB 可能记为前一天）
            try:
                dt = datetime(2026, int(month), int(day))
                dt_prev = dt - timedelta(days=1)
                match_day_prev = f"{dt_prev.month}/{dt_prev.day}"
            except ValueError:
                match_day_prev = None

            # 按队伍名 + 日期匹配（尝试当天和前一天）
            existing = (
                session.query(ManualMatch)
                .filter(
                    ManualMatch.match_day.in_([match_day, match_day_prev] if match_day_prev else [match_day]),
                    ManualMatch.team_home == m["team_home"],
                    ManualMatch.team_away == m["team_away"],
                )
                .first()
            )

            if existing:
                existing.odds_home = m["odds_home"]
                existing.odds_draw = m["odds_draw"]
                existing.odds_away = m["odds_away"]
                updated += 1
                print(f"  [更新] DB:{existing.match_day} ← 500:{match_day} {m['team_home']} vs {m['team_away']}: {m['odds_home']}/{m['odds_draw']}/{m['odds_away']}")
            else:
                skipped += 1
                print(f"  [跳过] {match_day} {m['team_home']} vs {m['team_away']}: 数据库中无匹配")

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


if __name__ == "__main__":
    import json
    result = update_manual_matches()
    print(f"\n结果: fetched={result.get('fetched')}, updated={result.get('updated')}, skipped={result.get('skipped')}")
