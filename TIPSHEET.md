# 🎬 TicketFinder Bot — Command Tipsheet & Quick Reference

Run all commands from `/Users/dinakaran/code/ticketfinderagent` (or print this cheatsheet anytime in terminal with `./ticketfinder tips`).

---

## 1. Quick Command Summary Table

| Goal | Command |
| :--- | :--- |
| **Print terminal tipsheet** | `./ticketfinder tips` |
| **Check once right now (Telegram + Gmail)** | `./ticketfinder watch <query> --now` |
| **Check in terminal only (no alerts sent)** | `./ticketfinder watch <query> --cli-only` |
| **Delayed 1-min check (lock-screen test)** | `./ticketfinder watch <query> --now --initial-delay 1` |
| **Start background watcher (5m + 30m + 1h HB)** | `./ticketfinder watch <query> --daemon --yes` |
| **Check active jobs & daemon PID** | `./ticketfinder status` |
| **Force immediate check on active jobs** | `./ticketfinder check` |
| **Preview 1-hour heartbeat message** | `./ticketfinder check --heartbeat --cli-only` |
| **Stop watching a specific movie** | `./ticketfinder stop --movie "Ramayana"` |
| **Stop a specific Job ID** | `./ticketfinder stop --id 39` |
| **Stop ALL jobs & background daemon** | `./ticketfinder stop` |
| **View live background logs** | `tail -n 50 .ticketfinder/daemon.log` |
| **Simulate Wave 1 → Wave 2 diff alerts** | `./ticketfinder test-notify` |

---

## 2. Instant One-Shot Checks (No Cron / Background Process)

### A. Live Check + Send Telegram & Gmail Alert Immediately (`--now`)
```bash
./ticketfinder watch movie tickets for ramayana tamil version on 5th november near perumbakkam. restrict only the following cinemas : inox , pvr, cinepolis, miraj --now
```

### B. Terminal-Only Preview (`--cli-only` / `--dry-run`)
Prints the full table of open cinemas, distances, showtimes, and seat counts in your terminal **without** sending any Telegram or Email notifications:
```bash
./ticketfinder watch movie tickets for ramayana tamil version on 5th november near perumbakkam. restrict only the following cinemas : inox , pvr, cinepolis, miraj --cli-only
```

### C. Locked-Screen Delivery Test (`--now --initial-delay 1`)
Waits **1 minute (`60s`)** so you can lock your Mac (`Control + Command + Q`), then runs the live check and sends the alert to **Telegram + Gmail**:
```bash
./ticketfinder watch movie tickets for ramayana tamil version on 5th november near perumbakkam. restrict only the following cinemas : inox , pvr, cinepolis, miraj --now --initial-delay 1
```

---

## 3. Background Watcher Mode (`--daemon --yes`)

Runs in the background wrapped with macOS `/usr/bin/caffeinate -i -s` so it keeps working even when your Mac screen is locked:
- **First alert**: Sent **5 minutes** after setup.
- **Diff checks**: Runs every **30 minutes** (alerts immediately when new cinemas or showtimes open).
- **Heartbeat alert**: Sends a **1-Hour Heartbeat** every **60 minutes** confirming the batch job is alive.

### A. Watch Multiple Dates for the Same Movie (1 Consolidated Alert)
```bash
./ticketfinder watch movie tickets for ramayana tamil version on 7th november or 9th november near perumbakkam. restrict only the following cinemas : inox , pvr, cinepolis, miraj --daemon --yes
```

### B. Watch a Single Date
```bash
./ticketfinder watch movie tickets for ramayana tamil version on 7th november near perumbakkam. restrict only the following cinemas : inox , pvr, cinepolis, miraj --daemon --yes
```

### C. Custom Initial Delay & Check Interval
To get the first alert in **2 minutes** and check every **15 minutes**:
```bash
./ticketfinder watch movie tickets for ramayana tamil version on 7th november or 9th november near perumbakkam. restrict only the following cinemas : inox , pvr, cinepolis, miraj --daemon --yes --initial-delay 2 --interval 15
```

---

## 4. Natural Language Query Patterns

You can phrase queries naturally—here are the supported keywords:

- **Movie & Language**:
  - `movie tickets for ramayana tamil version ...`
  - `movie tickets for coolie telugu ...`
- **Single or Multiple Dates**:
  - `on 7th november`
  - `on 7th november or 9th november`
  - `on 2026-11-07 or 2026-11-09`
  - `on friday` / `on tomorrow`
- **Locality & Radius**:
  - `near perumbakkam` (defaults to `15 km` radius in Chennai)
  - `near velachery within 10km`
  - `near anna nagar within 12 km`
- **Cinema Filtering Modes**:
  - **Strict Mode** (`restrict` / `only` — matches **only** the listed chains within the radius):
    `restrict only the following cinemas : inox , pvr, cinepolis, miraj`
  - **Inclusive Mode** (matches the listed cinemas **plus** any other cinema within the radius):
    `and cinemas sathyam, pvr, inox`

---

## 5. Monitoring, Logs & Stopping

### Check Active Daemon & Watch Jobs
```bash
./ticketfinder status
```

### View Live Background Logs
```bash
tail -f .ticketfinder/daemon.log
```

### Stop Watching
```bash
# Stop a specific movie:
./ticketfinder stop --movie "Ramayana"

# Stop a specific Job ID (shown in ./ticketfinder status):
./ticketfinder stop --id 39

# Stop ALL active jobs and shut down the background daemon:
./ticketfinder stop
```

---

## 6. Notification Channels Configuration (`config.env`)

Edited in `/Users/dinakaran/code/ticketfinderagent/config.env` (reloaded automatically by the daemon on every check cycle):
- **Gmail SMTP**: `GMAIL_SENDER`, `GMAIL_APP_PASSWORD`, `GMAIL_RECIPIENT` (`dinakaranonline@gmail.com`)
- **Telegram Bot**: `TELEGRAM_BOT_TOKEN` (`@movieticketfinderbot`) & `TELEGRAM_CHAT_ID=9086551434`
- **WhatsApp**: Set `WHATSAPP_PROVIDER=disabled` to keep disabled, or `WHATSAPP_PROVIDER=callmebot` + `WHATSAPP_PHONE=+918939954584` to re-enable.
