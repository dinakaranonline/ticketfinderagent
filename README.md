# 🎟️ TicketFinder Agent (`ticketfinderagent`)

A natural-language movie ticket watcher agent for macOS that checks **TicketNew**, **District (Paytm)**, **BookMyShow**, and **Web Discovery** in parallel, filters cinemas by **strict or inclusive chain filters** and **10–15 km Haversine locality radius**, tracks **booked vs. available seats**, consolidates **multi-date queries into a single notification**, and sends **Stateful Diff + 1-Hour Heartbeat Alerts** via **Telegram Bot API** and **Gmail SMTP** (working 100% in the background even when your Mac screen is locked).

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

### 3. Background Watcher Mode (5-Min Initial + 30-Min Diffs + 1-Hr Heartbeat)
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
