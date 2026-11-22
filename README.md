# 🎟️ TicketFinder Agent (`ticketfinderagent`)

A natural-language movie ticket watcher agent for macOS that checks **TicketNew**, **District (Paytm)**, **BookMyShow**, **Web Discovery**, and the cinema chains' own sites (**PVR INOX**, **Cinepolis**) in parallel, filters cinemas by **strict or inclusive chain filters** and **10–15 km Haversine locality radius**, tracks **booked vs. available seats**, consolidates **multi-date queries into a single notification**, and sends **Stateful Diff + 4-Hour Heartbeat Alerts** via **Telegram Bot API** and **Gmail SMTP** (working 100% in the background even when your Mac screen is locked).

See **[TIPSHEET.md](TIPSHEET.md)** (or run `./ticketfinder tips`) for a full command cheatsheet.

---

## 🚀 Quick Start

### 1. Configure Credentials (`config.env`)
Copy `.env.example` to `config.env` and set:
- **Gmail SMTP**: `GMAIL_SENDER`, `GMAIL_APP_PASSWORD`, `GMAIL_RECIPIENT`
- **Telegram Bot**: Message `@BotFather` on Telegram -> `/newbot` -> paste `TELEGRAM_BOT_TOKEN` into `config.env` -> send `"hi"` to your bot in Telegram (`TELEGRAM_CHAT_ID` is auto-discovered and saved on first run!).

### 2. Instant One-Shot Check (No Background Job)
```bash
# Live check + send Telegram & Gmail alert right now:
./ticketfinder watch movie tickets for ramayana tamil version on 5th november near perumbakkam. restrict only the following cinemas : inox , pvr, cinepolis, miraj --now

# Terminal-only preview (no Telegram or Email sent):
./ticketfinder watch movie tickets for ramayana tamil version on 5th november near perumbakkam. restrict only the following cinemas : inox , pvr, cinepolis, miraj --cli-only
```

### 3. Background Watcher Mode (5-Min Initial + 30-Min Diffs + 4-Hr Heartbeat)
Runs under `/usr/bin/caffeinate -i -s` so it keeps running while your Mac screen is locked:
```bash
./ticketfinder watch movie tickets for ramayana tamil version on 7th november or 9th november near perumbakkam. restrict only the following cinemas : inox , pvr, cinepolis, miraj --daemon --yes
```

### 4. Monitor & Stop Anytime
```bash
# Print full CLI tipsheet:
./ticketfinder tips

# View active daemon PID and watched jobs:
./ticketfinder status

# Stop watching a movie:
./ticketfinder stop --movie "Ramayana"

# Stop all jobs and shut down daemon:
./ticketfinder stop
```

---

## 🎬 Platforms Checked

| Platform | Source | Seats | Notes |
| --- | --- | --- | --- |
| District (Paytm), TicketNew | `__NEXT_DATA__` page payload | Exact, per seat class | Main aggregator source |
| BookMyShow | Page scrape | When shown | |
| Web Discovery | DuckDuckGo search for booking links | Via the linked page | |
| PVR INOX | Public JSON API at `api3.pvrcinemas.com` (no login) | Exact, from each show's seat map | One call covers PVR and INOX cinemas |
| Cinepolis | Public JSON API at `api_new.cinepolisindia.com` (no login) | `SeatsAvailable` per show | Currently blocked: Cloudflare returns 403 to non-browser clients, so this checker returns nothing and records why. It is not worked around. |

### What counts as bookable
Alerts list only shows you can actually book:
- not greyed out or disabled on the site (District `disableClick`, PVR "Lapsed", Cinepolis `AllowTicketSales=0`),
- at least `MIN_BOOKABLE_SEATS` seats left (default **4**; set it in `config.env` or the environment),
- for PVR INOX, the show's seat map must open. Dead sessions that still show as "Available" in listings (PVR answers *"This Show is no longer available for booking"*) are dropped.

A show seen earlier as not bookable that becomes bookable counts as a **new opening** and triggers an alert.

### Same cinema on several platforms
Cinemas are merged across platforms by name, or by shared name words plus coordinates within 0.5 km (e.g. District's "INOX The Marina Mall OMR, Egatoor" and PVR's "INOX The Marina Mall, OMR"). For PVR, INOX and Cinepolis cinemas, **the chain's own site wins** over the aggregators, both ways: open on PVR but greyed on District counts as open; housefull or dead on PVR but "Available" on District counts as closed.
