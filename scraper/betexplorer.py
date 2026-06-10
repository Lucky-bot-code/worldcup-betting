"""
BetExplorer 世界杯真实赔率爬虫。
每年数据 = 淘汰赛（默认URL）+ 小组赛（stage参数），含完整 Bet365 赔率。
"""

import re
import ssl
import urllib.request
from bs4 import BeautifulSoup

GROUP_STAGE_IDS = {
    2022: "zkyDYRLU",
    2018: "OneVXSrp",
    2014: "61tCiOIs",
    2010: "QN1QYX1j",
}

BASE_URL = "https://www.betexplorer.com/soccer/world/world-cup-{year}/results/"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
}


def _fetch(url: str) -> str:
    ssl_ctx = ssl._create_unverified_context()
    req = urllib.request.Request(url, headers=HEADERS)
    resp = urllib.request.urlopen(req, context=ssl_ctx)
    return resp.read().decode("utf-8", errors="ignore")


def _parse_matches(html: str, year: int, is_group: bool) -> list[dict]:
    """从 BetExplorer HTML 中解析比赛列表"""
    soup = BeautifulSoup(html, "html.parser")
    results = []
    current_stage = "小组赛" if is_group else "淘汰赛"

    for row in soup.select("tr"):
        # 检测阶段标题
        th = row.select_one("th[colspan]")
        if th:
            stage_text = th.text.strip()
            if stage_text in ("Final", "决赛"):
                current_stage = "决赛"
            elif stage_text in ("3rd place", "季军赛"):
                current_stage = "季军赛"
            elif stage_text in ("Semi-finals", "半决赛"):
                current_stage = "半决赛"
            elif stage_text in ("Quarter-finals", "1/4-finals", "8强"):
                current_stage = "8强"
            elif stage_text in ("1/8-finals", "16强"):
                current_stage = "16强"
            continue

        # 解析比赛行
        match_link = row.select_one("a.in-match")
        if not match_link:
            continue

        # 队伍名
        spans = match_link.select("span")
        if len(spans) < 2:
            continue
        team_home = spans[0].text.strip()
        team_away = spans[1].text.strip()

        # 比分
        score_cell = row.select_one("td.h-text-center a")
        if not score_cell:
            continue
        score_text = score_cell.text.strip()
        # 去掉加时/点球标记
        score_text = re.sub(r'\s*\(.*?\)', '', score_text)
        score_m = re.match(r"(\d+)\s*[-:]\s*(\d+)", score_text)
        if not score_m:
            continue
        score_home = int(score_m.group(1))
        score_away = int(score_m.group(2))

        # 赔率 — 3个 data-odd 分别对应 Home / Draw / Away
        odds_cells = row.select("td.table-main__odds")
        if len(odds_cells) < 3:
            continue

        odds_list = []
        for cell in odds_cells[:3]:
            odd_val = cell.get("data-odd")
            if not odd_val:
                nested = cell.select_one("[data-odd]")
                if nested:
                    odd_val = nested.get("data-odd")
            if odd_val:
                try:
                    odds_list.append(float(odd_val))
                except ValueError:
                    odds_list.append(0)
            else:
                odds_list.append(0)

        if len(odds_list) < 3 or all(o == 0 for o in odds_list):
            continue

        # 日期
        date_cell = row.select_one("td.h-text-right")
        date_str = date_cell.text.strip() if date_cell else ""

        results.append({
            "year": year,
            "stage": current_stage,
            "team_home": team_home,
            "team_away": team_away,
            "score_home": score_home,
            "score_away": score_away,
            "odds_home": odds_list[0],
            "odds_draw": odds_list[1],
            "odds_away": odds_list[2],
            "date": date_str,
        })

    return results


def scrape_year(year: int) -> list[dict]:
    """爬取指定年份世界杯全部比赛 + 赔率"""
    results = []

    # 1. 淘汰赛（默认URL）
    url_ko = BASE_URL.format(year=year)
    html_ko = _fetch(url_ko)
    ko_matches = _parse_matches(html_ko, year, is_group=False)
    results.extend(ko_matches)

    # 2. 小组赛（stage参数）
    stage_id = GROUP_STAGE_IDS.get(year)
    if stage_id:
        url_gs = f"{BASE_URL.format(year=year)}?stage={stage_id}"
        html_gs = _fetch(url_gs)
        gs_matches = _parse_matches(html_gs, year, is_group=True)
        results.extend(gs_matches)

    return results


def import_all(years: list[int] = None):
    """爬取并导入到数据库"""
    from models import get_session, Match, Odds

    target_years = years or list(GROUP_STAGE_IDS.keys())
    session = get_session()
    total_new = 0
    total_updated = 0

    try:
        for year in target_years:
            print(f"\n爬取 {year} 世界杯真实赔率...")
            matches = scrape_year(year)
            print(f"  获取 {len(matches)} 场比赛")

            for m in matches:
                existing = (
                    session.query(Match)
                    .filter(
                        Match.year == m["year"],
                        Match.team_home == m["team_home"],
                        Match.team_away == m["team_away"],
                    )
                    .first()
                )

                if existing:
                    # 更新阶段 + 比分
                    existing.stage = m["stage"]
                    existing.score_home = m["score_home"]
                    existing.score_away = m["score_away"]

                    # 检查是否已有 Bet365 赔率
                    bet365 = (
                        session.query(Odds)
                        .filter(Odds.match_id == existing.id, Odds.provider == "Bet365")
                        .first()
                    )
                    if bet365:
                        bet365.odds_home = m["odds_home"]
                        bet365.odds_draw = m["odds_draw"]
                        bet365.odds_away = m["odds_away"]
                    else:
                        # 替换旧的合成赔率（第一个赔率记录）
                        old_odds = session.query(Odds).filter(Odds.match_id == existing.id).first()
                        if old_odds:
                            old_odds.provider = "Bet365"
                            old_odds.odds_home = m["odds_home"]
                            old_odds.odds_draw = m["odds_draw"]
                            old_odds.odds_away = m["odds_away"]
                        else:
                            session.add(Odds(
                                match_id=existing.id,
                                provider="Bet365",
                                odds_home=m["odds_home"],
                                odds_draw=m["odds_draw"],
                                odds_away=m["odds_away"],
                            ))
                    total_updated += 1
                else:
                    # 新增比赛
                    match = Match(
                        year=m["year"],
                        stage=m["stage"],
                        team_home=m["team_home"],
                        team_away=m["team_away"],
                        score_home=m["score_home"],
                        score_away=m["score_away"],
                    )
                    session.add(match)
                    session.flush()

                    session.add(Odds(
                        match_id=match.id,
                        provider="Bet365",
                        odds_home=m["odds_home"],
                        odds_draw=m["odds_draw"],
                        odds_away=m["odds_away"],
                    ))
                    total_new += 1

            session.commit()
            print(f"  [存档] 新增{sum(1 for x in matches if not session.query(Match).filter(Match.year==m['year'],Match.team_home==m['team_home'],Match.team_away==m['team_away']).first())}场")

        print(f"\n总计: 新增 {total_new} 场，更新 {total_updated} 场真实Bet365赔率")

    except Exception as e:
        session.rollback()
        print(f"导入失败: {e}")
        raise
    finally:
        session.close()


if __name__ == "__main__":
    import_all()
