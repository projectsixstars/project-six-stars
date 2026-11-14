# Project Six Stars — Market Eye Watcher

The live pulse of the GTA 6 launch. One number, one leaderboard, refreshed every 15 minutes.

**Live:** https://project-six-stars.vercel.app

## What it is

- **The Six Stars Index** — a 0–100 composite of GTA 6 attention (YouTube view mass, Twitch live viewers, Take-Two stock, headline volume).
- **Starboard** — the top 10 long-form videos and top 10 Shorts in the GTA 6 niche, ranked by **views per hour**, not total views.

## Architecture

```
GitHub Actions (every 15 min)
  └─ fetch.py : YouTube · Twitch · Finnhub · Google News
       └─ data/current.json  ← committed to this repo
            └─ browser fetches it directly
```

No server. No database. The site is static and never needs rebuilding to show new numbers.

## Deploy

Static. Vercel serves the repo root directly; `data/current.json` is the live feed.

## Secrets required

| Name | Source |
|---|---|
| `YT_KEY` | Google Cloud → YouTube Data API v3 |
| `TWITCH_ID` | dev.twitch.tv |
| `TWITCH_SECRET` | dev.twitch.tv |
| `FINNHUB_KEY` | finnhub.io |

## Legal

Independent fan project. Not affiliated with Rockstar Games or Take-Two Interactive. Data via official APIs; no third-party footage is hosted.
