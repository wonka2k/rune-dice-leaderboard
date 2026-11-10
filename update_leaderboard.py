"""Rune Dice - Daily / Weekly / Monthly tops from Steam leaderboards -> docs/*.json for the site.

Read-only towards Steam. Key: env STEAM_WEBAPI_KEY (publisher Web API key, General permission).

Output (served by GitHub Pages from /docs):
  docs/index.json                 - list of available periods
  docs/daily/YYYY-MM-DD.json      - top of one Daily board
  docs/weekly/YYYY-Www.json       - top of one Weekly board
  docs/monthly/YYYY-MM.json       - monthly standings (points from Daily + Weekly)

Monthly points per board: place 1-10 -> 25,18,15,12,10,8,6,4,2,1; outside the top 10 -> 1 point for taking part.
Weekly points are multiplied by WEEKLY_MULT. A Weekly runs Wednesday 00:00 UTC - Tuesday 23:59 UTC
(board Weekly_YYYY-Www = ISO week of the date shifted back by WEEK_SHIFT_DAYS) and belongs to the month of its last day.
Closed periods are written once and never recalculated - except when blacklist.txt changes: then every period still on Steam
is recalculated, and blacklisted nicknames are removed from all files. Only the nickname is published (no avatars - Kwalee legal, 09.11.2026).
Anti-cheat cap: scores above MAX_DAILY / MAX_WEEKLY are dropped (same limits as the game).
"""
import datetime as dt
import hashlib
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
WEEK_SHIFT_DAYS = 2  # weekly reset on Wednesday 00:00 UTC (Monday + 2 days)
ROOT = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.join(ROOT, "docs")
BLACKLIST = os.path.join(ROOT, "blacklist.txt")
PRUNE = os.path.join(ROOT, "prune.txt")  # cut-off rules: "daily<2026-11-12", "weekly<2026-W46" (see main)
MAX_DAILY = int(os.environ.get("MAX_DAILY", "100000"))    # same cap as the game (0 = no cap)
MAX_WEEKLY = int(os.environ.get("MAX_WEEKLY", "500000"))
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


def board_entries(board, cap):
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
        if sid in seen or sid in BANNED or (cap > 0 and int(e["score"]) > cap):
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
            _names[p["steamid"]] = {"name": p.get("personaname", "")}
    for i in ids:
        _names.setdefault(i, {"name": ""})


def person(sid):
    return _names.get(sid, {"name": ""})


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


def week_last_day(year, week):
    # last day of a Wednesday-Tuesday week whose shifted ISO week is (year, week)
    return dt.date.fromisocalendar(year, week, 7) + dt.timedelta(days=WEEK_SHIFT_DAYS)


def main():
    if not KEY:
        sys.exit("STEAM_WEBAPI_KEY is not set")
    global BANNED
    lines = []
    if os.path.exists(BLACKLIST):
        lines = sorted(l.strip() for l in open(BLACKLIST, encoding="utf-8") if l.strip() and not l.startswith("#"))
    BANNED = {l for l in lines if not l.lower().startswith("name:")}
    banned_names = {l[5:].strip().lower() for l in lines if l.lower().startswith("name:")}
    # prune.txt: periods BEFORE the cut-off are ignored even if the boards still exist on Steam; their files are deleted.
    # Used to drop pre-release test boards. Lines: daily<YYYY-MM-DD, weekly<YYYY-Www. Changing the file recalculates everything.
    cuts, prune_lines = {}, []
    if os.path.exists(PRUNE):
        prune_lines = sorted(l.strip() for l in open(PRUNE, encoding="utf-8") if l.strip() and not l.startswith("#"))
        for l in prune_lines:
            m = re.match(r"^(daily|weekly)\s*<\s*(\S+)$", l, re.I)
            if m:
                cuts[m.group(1).lower()] = m.group(2)
    bl_hash = hashlib.sha1("|".join(lines + ["prune:" + l for l in prune_lines]).encode()).hexdigest()[:12]
    try:
        with open(os.path.join(DOCS, "index.json"), encoding="utf-8") as f:
            force = json.load(f).get("blacklistHash", "") != bl_hash
    except (OSError, ValueError):
        force = True

    today = NOW.date()
    cur_year, cur_week, _ = (today - dt.timedelta(days=WEEK_SHIFT_DAYS)).isocalendar()
    periods = {}  # (kind, key) -> dict(board, final, date_for_month)
    for b in list_boards():
        m = re.match(r"^Daily_(\d{4}-\d{2}-\d{2})$", b["name"])
        if m:
            d = dt.date.fromisoformat(m.group(1))
            if d > today:  # future board (debug date / wrong clock) - ignore
                continue
            if "daily" in cuts and m.group(1) < cuts["daily"]:  # before the prune cut-off
                continue
            periods[("daily", m.group(1))] = {"board": b, "final": d < today, "month": d.strftime("%Y-%m")}
            continue
        m = re.match(r"^Weekly_(\d{4})-W(\d{2})$", b["name"])
        if m:
            y, w = int(m.group(1)), int(m.group(2))
            if (y, w) > (cur_year, cur_week):  # future board - ignore
                continue
            key = f"{y}-W{w:02d}"
            if "weekly" in cuts and key < cuts["weekly"]:  # before the prune cut-off
                continue
            periods[("weekly", key)] = {"board": b, "final": (y, w) < (cur_year, cur_week),
                                        "month": week_last_day(y, w).strftime("%Y-%m")}

    # months that still need (re)calculation: not yet written as final
    months = sorted({p["month"] for p in periods.values()})
    open_months = [mo for mo in months if force or not is_final(path("monthly", mo))]

    # which boards to fetch: open boards, missing files, and every board of an open month
    need = {k for k, p in periods.items()
            if force or not p["final"] or not is_final(path(*k)) or p["month"] in open_months}
    fetched = {k: board_entries(periods[k]["board"], MAX_DAILY if k[0] == "daily" else MAX_WEEKLY) for k in sorted(need)}
    if banned_names:  # "name:<nickname>" lines -> drop those players by their current Steam nickname
        resolve({sid for rows in fetched.values() for sid, _ in rows})
        for k in fetched:
            fetched[k] = [(sid, sc) for sid, sc in fetched[k] if person(sid)["name"].strip().lower() not in banned_names]

    # per-board tops (only those not yet final on disk)
    board_keys = [k for k in fetched if force or not (periods[k]["final"] and is_final(path(*k)))]
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
                pts = (POINTS[place - 1] if place <= 10 else PARTICIPATION) * mult
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

    # prune.txt: delete files of periods before the cut-off; monthly files of months with no period left
    for kind in ("daily", "weekly"):
        folder = os.path.join(DOCS, kind)
        if kind in cuts and os.path.isdir(folder):
            for f in os.listdir(folder):
                if f.endswith(".json") and f[:-5] < cuts[kind]:
                    os.remove(os.path.join(folder, f))
    mfolder = os.path.join(DOCS, "monthly")
    if cuts and os.path.isdir(mfolder):
        for f in os.listdir(mfolder):
            if f.endswith(".json") and f[:-5] not in months:
                os.remove(os.path.join(mfolder, f))

    # remove files left from future boards written by older runs
    cur_week_key = f"{cur_year}-W{cur_week:02d}"
    cur_week_month = week_last_day(cur_year, cur_week).strftime("%Y-%m")  # a week can end in next month
    limits = {"daily": today.isoformat(), "weekly": cur_week_key, "monthly": cur_week_month}
    for kind, limit in limits.items():
        folder = os.path.join(DOCS, kind)
        if os.path.isdir(folder):
            for f in os.listdir(folder):
                if f.endswith(".json") and f[:-5] > limit:
                    os.remove(os.path.join(folder, f))

    # strip avatars from files written by older runs (closed periods are never recalculated)
    for kind in ("daily", "weekly", "monthly"):
        folder = os.path.join(DOCS, kind)
        if not os.path.isdir(folder):
            continue
        for f in os.listdir(folder):
            fp = os.path.join(folder, f)
            if not f.endswith(".json"):
                continue
            with open(fp, encoding="utf-8") as fh:
                data = json.load(fh)
            rows = data.get("top", [])
            keep = [r for r in rows if str(r.get("name", "")).strip().lower() not in banned_names]
            if any("avatar" in r for r in rows) or len(keep) != len(rows):
                for i, r in enumerate(keep, 1):
                    r.pop("avatar", None)
                    r["place"] = i
                data["top"] = keep
                write(fp, data)

    index = {kind: sorted(f[:-5] for f in os.listdir(os.path.join(DOCS, kind)) if f.endswith(".json"))
             for kind in ("daily", "weekly", "monthly") if os.path.isdir(os.path.join(DOCS, kind))}
    write(os.path.join(DOCS, "index.json"), {"updatedUtc": stamp, "blacklistHash": bl_hash, **{k: index.get(k, []) for k in ("daily", "weekly", "monthly")}})
    print(f"boards: {len(periods)}, fetched: {len(fetched)}, written: {len(board_keys)} boards + {len(month_rows)} months")


if __name__ == "__main__":
    main()
