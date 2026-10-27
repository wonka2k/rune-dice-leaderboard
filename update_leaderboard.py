"""Rune Dice - Daily / Weekly / Monthly tops from Steam leaderboards -> docs/*.json for the site.

Read-only towards Steam. Key: env STEAM_WEBAPI_KEY (publisher Web API key, General permission).

Output (served by GitHub Pages from /docs):
  docs/index.json                 - list of available periods
  docs/daily/YYYY-MM-DD.json      - top of one Daily board
  docs/weekly/YYYY-Www.json       - top of one Weekly board
  docs/monthly/YYYY-MM.json       - monthly standings (points from Daily + Weekly)

Monthly points per board: place 1-10 -> 25,18,15,12,10,8,6,4,2,1; +1 for taking part.
Weekly points are multiplied by WEEKLY_MULT. A week belongs to the month of its Sunday.
Closed periods are written once and never recalculated. Only nickname + avatar are published.
"""
import datetime as dt
import json
import os
import re
import sys
import urllib.parse
import urllib.request

APP_ID = 3542380
TOP_N = 50
POINTS = [25, 18, 15, 12, 10, 8, 6, 4, 2, 1]
PARTICIPATION = 1
WEEKLY_MULT = 5
ROOT = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.join(ROOT, "docs")
BLACKLIST = os.path.join(ROOT, "blacklist.txt")
MAX_SCORE = int(os.environ.get("MAX_SCORE", "0"))  # 0 = no cap
KEY = os.environ.get("STEAM_WEBAPI_KEY", "").strip()
NOW = dt.datetime.now(dt.timezone.utc)


def get(url, params):
    req = urllib.request.Request(f"{url}?{urllib.parse.urlencode(params)}",
                                 headers={"x-webapi-key": KEY, "User-Agent": "rune-dice-leaderboard"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def list_boards():
    r = get("https://partner.steam-api.com/ISteamLeaderboards/GetLeaderboardsForGame/v2/", {"appid": APP_ID})
    return r.get("response", {}).get("leaderboards", []) or []


def board_entries(board):
    total = max(int(board.get("entries", 0)), 0)
    out, start = [], 0
    while total > 0 and start <= total:
        end = min(start + 999, total)
        r = get("https://partner.steam-api.com/ISteamLeaderboards/GetLeaderboardEntries/v1/",
                {"appid": APP_ID, "leaderboardid": board["id"], "rangestart": start, "rangeend": end, "datarequest": 0})
        out += r.get("leaderboardEntryInformation", {}).get("leaderboardEntries", []) or []
        start = end + 1
    seen, clean = set(), []
    for e in sorted(out, key=lambda e: -int(e["score"])):  # re-rank after filtering
        sid = str(e["steamID"])
        if sid in seen or sid in BANNED or (MAX_SCORE > 0 and int(e["score"]) > MAX_SCORE):
            continue
        seen.add(sid)
        clean.append((sid, int(e["score"])))
    return clean


_names = {}


def resolve(ids):
    ids = [i for i in ids if i not in _names]
    for i in range(0, len(ids), 100):
        r = get("https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v2/", {"steamids": ",".join(ids[i:i + 100])})
        for p in r.get("response", {}).get("players", []):
            _names[p["steamid"]] = {"name": p.get("personaname", ""), "avatar": p.get("avatarmedium", "")}
    for i in ids:
        _names.setdefault(i, {"name": "", "avatar": ""})


def person(sid):
    return _names.get(sid, {"name": "", "avatar": ""})


def path(kind, key):
    return os.path.join(DOCS, kind, f"{key}.json")


def write(p, data):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)


def is_final(p):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f).get("final", False)
    except (OSError, ValueError):
        return False


def week_sunday(year, week):
    return dt.date.fromisocalendar(year, week, 7)


def main():
    if not KEY:
        sys.exit("STEAM_WEBAPI_KEY is not set")
    global BANNED
    BANNED = set()
    if os.path.exists(BLACKLIST):
        BANNED = {l.strip() for l in open(BLACKLIST, encoding="utf-8") if l.strip() and not l.startswith("#")}

    today = NOW.date()
    cur_year, cur_week, _ = today.isocalendar()
    periods = {}  # (kind, key) -> dict(board, final, date_for_month)
    for b in list_boards():
        m = re.match(r"^Daily_(\d{4}-\d{2}-\d{2})$", b["name"])
        if m:
            d = dt.date.fromisoformat(m.group(1))
            periods[("daily", m.group(1))] = {"board": b, "final": d < today, "month": d.strftime("%Y-%m")}
            continue
        m = re.match(r"^Weekly_(\d{4})-W(\d{2})$", b["name"])
        if m:
            y, w = int(m.group(1)), int(m.group(2))
            key = f"{y}-W{w:02d}"
            periods[("weekly", key)] = {"board": b, "final": (y, w) < (cur_year, cur_week),
                                        "month": week_sunday(y, w).strftime("%Y-%m")}

    # months that still need (re)calculation: not yet written as final
    months = sorted({p["month"] for p in periods.values()})
    open_months = [mo for mo in months if not is_final(path("monthly", mo))]

    # which boards to fetch: open boards, missing files, and every board of an open month
    need = {k for k, p in periods.items()
            if not p["final"] or not is_final(path(*k)) or p["month"] in open_months}
    fetched = {k: board_entries(periods[k]["board"]) for k in sorted(need)}

    # per-board tops (only those not yet final on disk)
    board_keys = [k for k in fetched if not (periods[k]["final"] and is_final(path(*k)))]
    month_rows = {}
    for mo in open_months:
        rows = {}
        for k, p in periods.items():
            if p["month"] != mo:
                continue
            mult = WEEKLY_MULT if k[0] == "weekly" else 1
            for place, (sid, score) in enumerate(fetched[k], 1):
                r = rows.setdefault(sid, {"points": 0, "dailyPoints": 0, "weeklyPoints": 0,
                                          "wins": 0, "top10": 0, "played": 0})
                pts = (PARTICIPATION + (POINTS[place - 1] if place <= 10 else 0)) * mult
                r["points"] += pts
                r["dailyPoints" if k[0] == "daily" else "weeklyPoints"] += pts
                r["played"] += 1
                r["top10"] += place <= 10
                r["wins"] += place == 1
        ranked = sorted(rows.items(), key=lambda kv: (-kv[1]["points"], -kv[1]["wins"], -kv[1]["top10"]))[:TOP_N]
        month_rows[mo] = ranked

    resolve({sid for k in board_keys for sid, _ in fetched[k][:TOP_N]} |
            {sid for rows in month_rows.values() for sid, _ in rows})

    stamp = NOW.replace(microsecond=0).isoformat()
    for k in board_keys:
        p = periods[k]
        write(path(*k), {"kind": k[0], "period": k[1], "final": p["final"], "updatedUtc": stamp,
                         "players": len(fetched[k]),
                         "top": [dict(place=i, score=s, **person(sid)) for i, (sid, s) in enumerate(fetched[k][:TOP_N], 1)]})
    first_of_month = today.replace(day=1).strftime("%Y-%m")
    for mo, ranked in month_rows.items():
        write(path("monthly", mo), {"kind": "monthly", "period": mo, "final": mo < first_of_month, "updatedUtc": stamp,
                                    "rules": {"points": POINTS, "participation": PARTICIPATION, "weeklyMultiplier": WEEKLY_MULT},
                                    "top": [dict(place=i, **person(sid), **r) for i, (sid, r) in enumerate(ranked, 1)]})

    index = {kind: sorted(f[:-5] for f in os.listdir(os.path.join(DOCS, kind)) if f.endswith(".json"))
             for kind in ("daily", "weekly", "monthly") if os.path.isdir(os.path.join(DOCS, kind))}
    write(os.path.join(DOCS, "index.json"), {"updatedUtc": stamp, **{k: index.get(k, []) for k in ("daily", "weekly", "monthly")}})
    print(f"boards: {len(periods)}, fetched: {len(fetched)}, written: {len(board_keys)} boards + {len(month_rows)} months")


if __name__ == "__main__":
    main()
