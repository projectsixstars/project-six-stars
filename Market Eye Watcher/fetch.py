#!/usr/bin/env python3
"""Project Six Stars - Market Eye Watcher data pipeline.

Runs on GitHub Actions every 15 minutes. No external dependencies.
Writes data/current.json which the static site reads in the browser.
"""
from __future__ import annotations

import html
import json
import os
import re
import time
import datetime as dt
import urllib.parse
import urllib.request
import urllib.error

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")
HIST = os.path.join(DATA, "history")
os.makedirs(DATA, exist_ok=True)
os.makedirs(HIST, exist_ok=True)

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
NOW = dt.datetime.now(dt.timezone.utc)
LAUNCH = dt.datetime(2026, 11, 18, 11, 0, tzinfo=dt.timezone.utc)  # 16:30 IST
SITE = "https://project-six-stars.vercel.app"

health = {}


def H(name, ok, note=""):
    health[name] = {"ok": bool(ok), "note": str(note)[:180]}
    print("[%s] %s %s" % ("OK " if ok else "ERR", name, note))


def load(name, default=None):
    try:
        with open(os.path.join(DATA, name), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save(name, obj):
    p = os.path.join(DATA, name)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, p)


def http(url, headers=None, body=None, timeout=25):
    req = urllib.request.Request(url, data=body, headers=headers or {})
    req.add_header("User-Agent", "ProjectSixStars/1.0 (+%s)" % SITE)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def get_json(url, headers=None):
    return json.loads(http(url, headers).decode("utf-8"))


def iso_dur(s):
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", s or "")
    if not m:
        return 0
    h, mi, se = (int(x) if x else 0 for x in m.groups())
    return h * 3600 + mi * 60 + se


def parse_dt(s):
    try:
        return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


# ----------------------------------------------------------------------------
# YouTube
# ----------------------------------------------------------------------------
QUERIES = [
    "GTA 6",
    "GTA VI",
    "Grand Theft Auto 6",
    "GTA 6 gameplay",
    "GTA 6 funny",
    "GTA 6 secrets",
]


def youtube(prev):
    key = (os.environ.get("YT_KEY") or "").strip()
    if not key:
        H("youtube", False, "YT_KEY missing")
        return []

    ids = []
    last_search = float((prev or {}).get("_search_ts") or 0)
    need_search = (time.time() - last_search) > 5400  # search at most every 90 min

    if need_search:
        after = (NOW - dt.timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
        for q in QUERIES:
            try:
                u = (
                    "https://www.googleapis.com/youtube/v3/search?part=snippet&type=video"
                    "&maxResults=50&order=viewCount&publishedAfter=%s"
                    "&q=%s&key=%s" % (after, urllib.parse.quote(q), key)
                )
                d = get_json(u)
                for it in d.get("items", []):
                    vid = (it.get("id") or {}).get("videoId")
                    if vid:
                        ids.append(vid)
            except urllib.error.HTTPError as e:
                H("youtube.search", False, "%s -> HTTP %s" % (q, e.code))
            except Exception as e:
                H("youtube.search", False, "%s -> %s" % (q, e))
        H("youtube.search", bool(ids), "%d ids / %d queries" % (len(set(ids)), len(QUERIES)))
        search_ts = time.time()
    else:
        cur = load("current.json", {}) or {}
        vids = cur.get("videos")
        if isinstance(vids, dict):  # legacy schema: {longform:[], shorts:[]}
            vids = (vids.get("longform") or []) + (vids.get("shorts") or [])
        for v in (vids or []):
            if isinstance(v, dict) and v.get("id"):
                ids.append(v["id"])
        H("youtube.search", bool(ids), "reused %d ids (quota save)" % len(ids))
        search_ts = last_search

    ids = list(dict.fromkeys(ids))[:300]
    if not ids:
        return []

    out = []
    for i in range(0, len(ids), 50):
        chunk = ids[i:i + 50]
        try:
            u = (
                "https://www.googleapis.com/youtube/v3/videos?part=snippet,statistics,contentDetails"
                "&maxResults=50&id=%s&key=%s" % (",".join(chunk), key)
            )
            d = get_json(u)
            out.extend(d.get("items", []))
        except urllib.error.HTTPError as e:
            H("youtube.videos", False, "HTTP %s" % e.code)
        except Exception as e:
            H("youtube.videos", False, str(e))

    # channel subscriber counts
    chans = list({v["snippet"]["channelId"] for v in out if v.get("snippet")})
    subs = {}
    for i in range(0, len(chans), 50):
        try:
            u = (
                "https://www.googleapis.com/youtube/v3/channels?part=statistics"
                "&maxResults=50&id=%s&key=%s" % (",".join(chans[i:i + 50]), key)
            )
            d = get_json(u)
            for c in d.get("items", []):
                subs[c["id"]] = int(c.get("statistics", {}).get("subscriberCount") or 0)
        except Exception as e:
            H("youtube.channels", False, str(e))

    H("youtube.videos", bool(out), "%d videos resolved" % len(out))

    prev_views = (prev or {}).get("videos", {}) or {}
    prev_ranks = (prev or {}).get("ranks", {}) or {}
    prev_ts = float((prev or {}).get("ts") or 0)
    hours = max((time.time() - prev_ts) / 3600.0, 0.01) if prev_ts else 0

    videos = []
    for v in out:
        sn = v.get("snippet") or {}
        st = v.get("statistics") or {}
        cd = v.get("contentDetails") or {}
        dur = iso_dur(cd.get("duration"))
        title = html.unescape(sn.get("title") or "")
        desc = (sn.get("description") or "").lower()
        views = int(st.get("viewCount") or 0)
        is_short = dur <= 60
        vel = None
        if prev_ts and v["id"] in prev_views:
            dv = views - int(prev_views[v["id"]])
            if dv >= 0:
                vel = int(dv / hours) if hours > 0 else 0
        videos.append({
            "id": v["id"],
            "title": title,
            "channel": html.unescape(sn.get("channelTitle") or ""),
            "subs": subs.get(sn.get("channelId"), 0),
            "views": views,
            "published": sn.get("publishedAt"),
            "duration": dur,
            "short": bool(is_short),
            "velocity": vel,
            "rankDelta": prev_ranks.get(v["id"], 0) and None,
        })

    # --- ranking (velocity first, views fallback) ---
    keyf = lambda x: (x["velocity"] if x["velocity"] is not None else x["views"] / max(time_ago_hours(x), 1))
    videos.sort(key=keyf, reverse=True)

    top = videos[:20]

    ranks = {}
    for i, v in enumerate(top):
        v["rank"] = i + 1
        prevr = prev_ranks.get(v["id"]) or 0
        v["rankDelta"] = (prevr - (i + 1)) if prevr else 0
        ranks[v["id"]] = i + 1

    meta = {"_search_ts": search_ts, "ranks": ranks}
    return [top, meta, sum(x["views"] for x in videos)]


def time_ago_hours(v):
    d = parse_dt(v.get("published") or "")
    if not d:
        return 48.0
    return max((NOW - d).total_seconds() / 3600.0, 0.5)


# ----------------------------------------------------------------------------
# Twitch
# ----------------------------------------------------------------------------
def twitch():
    cid = (os.environ.get("TWITCH_ID") or "").strip()
    sec = (os.environ.get("TWITCH_SECRET") or "").strip()
    if not (cid and sec):
        H("twitch", False, "TWITCH_ID/SECRET missing")
        return {}
    try:
        body = urllib.parse.urlencode({
            "client_id": cid,
            "client_secret": sec,
            "grant_type": "client_credentials",
        }).encode()
        raw = http("https://id.twitch.tv/oauth2/token",
                   {"Content-Type": "application/x-www-form-urlencoded"}, body)
        token = json.loads(raw.decode("utf-8"))["access_token"]
    except Exception as e:
        H("twitch", False, "auth: %s" % e)
        return {}

    hdrs = {"Client-Id": cid, "Authorization": "Bearer " + token}
    gid, gname = None, None
    for name in ["Grand Theft Auto VI", "GTA VI", "Grand Theft Auto V"]:
        try:
            d = get_json("https://api.twitch.tv/helix/games?name=%s" % urllib.parse.quote(name), hdrs)
            if d.get("data"):
                gid, gname = d["data"][0]["id"], d["data"][0]["name"]
                break
        except Exception:
            continue

    if not gid:
        H("twitch", False, "no game found")
        return {}

    try:
        d = get_json("https://api.twitch.tv/helix/streams?first=100&game_id=%s" % gid, hdrs)
        rows = d.get("data", [])
        viewers = sum(int(s.get("viewer_count") or 0) for s in rows)
        H("twitch", True, "%s: %d streams, %d viewers" % (gname, len(rows), viewers))
        return {"game": gname, "streams": len(rows), "viewers": viewers}
    except Exception as e:
        H("twitch.streams", False, str(e))
        return {}


# ----------------------------------------------------------------------------
# Google News RSS
# ----------------------------------------------------------------------------
def news():
    try:
        q = urllib.parse.urlencode({
            "q": '"GTA 6" OR "GTA VI" OR "Grand Theft Auto VI"',
            "hl": "en-US", "gl": "US", "ceid": "US:en",
        })
        raw = http("https://news.google.com/rss/search?" + q).decode("utf-8", "ignore")
        items = re.findall(r"<item>(.*?)</item>", raw, re.S)
        out = []
        for it in items[:40]:
            t = re.search(r"<title>(.*?)</title>", it, re.S)
            l = re.search(r"<link>(.*?)</link>", it, re.S)
            p = re.search(r"<pubDate>(.*?)</pubDate>", it, re.S)
            if not t:
                continue
            title = re.sub(r"<!\[CDATA\[|\]\]>", "", t.group(1)).strip()
            title = html.unescape(title)
            out.append({
                "title": title[:300],
                "link": l.group(1).strip() if l else "",
                "published": p.group(1).strip() if p else "",
            })
        H("news", bool(out), "%d headlines" % len(out))
        return out
    except Exception as e:
        H("news", False, str(e))
        return []


# ----------------------------------------------------------------------------
# Index
# ----------------------------------------------------------------------------
def clamp(x, lo=0.0, hi=100.0):
    return max(lo, min(hi, x))


def norm(v, lo, hi):
    if hi <= lo:
        return 0.0
    return clamp((v - lo) / (hi - lo) * 100.0)


def main():
    prev = load("prev.json", {}) or {}
    yt = youtube(prev)
    if len(yt) == 3:
        top, ytmeta, total_views = yt
    else:
        top, ytmeta, total_views = [], {}, 0

    tw = twitch()
    nw = news()

    # --- components -----------------------------------------------------------
    attention = norm(total_views, 0, 300_000_000)          # tracked view mass
    live = norm(tw.get("viewers", 0), 0, 1_200_000)         # twitch viewers
    headlines = norm(len(nw), 0, 100)                       # news velocity

    index = 0.50 * attention + 0.30 * live + 0.20 * headlines
    index = round(clamp(index, 0, 100), 1)

    # --- 24h delta ------------------------------------------------------------
    hist = load("index_history.json", []) or []
    now_ts = time.time()
    hist.append({"t": int(now_ts), "v": index})
    hist = [h for h in hist if now_ts - h["t"] < 7 * 86400]

    delta = None
    for h in hist:
        if now_ts - h["t"] >= 20 * 3600:
            delta = round(index - h["v"], 1)
            break
    if delta is None and len(hist) > 2:
        delta = round(index - hist[0]["v"], 1)

    save("index_history.json", hist)

    # --- ranks / velocity bookkeeping ---------------------------------------
    ranks = {}
    views_map = {}
    for v in top:
        ranks[v["id"]] = v["rank"]
        views_map[v["id"]] = v["views"]

    save("prev.json", {
        "ts": int(now_ts),
        "videos": views_map,
        "ranks": ranks,
        "_search_ts": ytmeta.get("_search_ts", 0),
    })

    # --- health ---------------------------------------------------------------
    ok = sum(1 for v in health.values() if v.get("ok"))
    total = len(health) or 1
    status = "ok" if ok >= max(2, total - 2) else "degraded"

    payload = {
        "updated": dt.datetime.now(IST).strftime("%d %b %Y - %H:%M IST"),
        "updatedISO": NOW.isoformat(),
        "status": status,
        "mode": "live" if NOW >= LAUNCH else "pre",
        "launchISO": LAUNCH.isoformat(),
        "index": index,
        "delta": delta,
        "components": {
            "attention": round(attention, 1),
            "live": round(live, 1),
            "headlines": round(headlines, 1),
        },
        "stats": {
            "twitchViewers": tw.get("viewers", 0),
            "twitchStreams": tw.get("streams", 0),
            "twitchGame": tw.get("game", ""),
            "trackedViews": total_views,
            "headlines": len(nw),
        },
        "videos": top,
        "news": nw[:12],
        "health": health,
    }

    save("current.json", payload)
    print("\nINDEX %.1f  (%s)  sources %d/%d" % (index, status, ok, total))


if __name__ == "__main__":
    main()
