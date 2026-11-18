from __future__ import annotations

import html
from datetime import datetime

from ticketfinder.models import CinemaVenue, DiffReport, ShowtimeSlot


def _friendly_date(iso_date: Optional[str]) -> str:
    if not iso_date:
        return ""
    if iso_date.upper() == "ANY":
        return "Any Available Date"
    try:
        dt = datetime.strptime(iso_date, "%Y-%m-%d")
        return dt.strftime("%a, %d %b %Y")
    except Exception:
        return iso_date


def _resolve_effective_dates(report: DiffReport) -> list[str]:
    """
    Returns the concrete ISO dates (YYYY-MM-DD) for this report.
    If job.target_date != 'ANY', returns [job.target_date].
    If job.target_date == 'ANY', returns report.discovered_dates when tickets are open,
    or ['ANY'] when bookings have not opened yet.
    """
    if report.job.target_date.upper() != "ANY":
        return [report.job.target_date]
    if report.discovered_dates:
        return report.discovered_dates
    return ["ANY"]


def _effective_date_label(report: DiffReport) -> str:
    """Returns a user-friendly date string for display in subject, headers, and alerts."""
    dates = _resolve_effective_dates(report)
    if dates == ["ANY"]:
        return "Any Available Date (Whenever Bookings Open)"
    return " & ".join(_friendly_date(d) for d in dates)


def _number_badge(idx: int) -> str:
    badges = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
    if 1 <= idx <= len(badges):
        return badges[idx - 1]
    return f"{idx}."


def render_whatsapp_message(report: DiffReport, is_daemon_running: bool = False) -> str:
    """Renders the approved WhatsApp / Telegram notification message for initial status or diff updates."""
    job = report.job
    effective_date = _effective_date_label(report)
    job_id_str = f" --id {job.id}" if job.id else ""

    if not report.any_tickets_open:
        header = (
            "🤖 *TICKET WATCHER ACTIVE (5-Min Initial Check)*"
            if report.is_initial_check
            else "🤖 *TICKET WATCHER STATUS*"
        )
        lines = [
            header,
            f"🎬 *Movie:* {job.movie}",
            f"📅 *Date:* {effective_date}",
            f"📍 *Target:* {job.target_summary}",
            "",
            f"⏳ *Current Status:* Bookings NOT open yet for {effective_date}",
            f"🌐 *Sites Checked:* {', '.join(report.sites_checked)}",
        ]
        if is_daemon_running:
            lines.extend(
                [
                    "",
                    "✅ Your Mac bot is running in the background (sleep-protected) and will check every *30 minutes*. You will get an alert as soon as the first cinema opens!",
                    "",
                    f"🛑 _To stop anytime: run `ticketfinder stop{job_id_str}`_",
                ]
            )
        return "\n".join(lines)

    # Active ticket opening / diff report
    header_date = (
        f"{effective_date} (Watch: Any Date)"
        if job.target_date.upper() == "ANY"
        else effective_date
    )
    lines = [
        f"🎟️ *TICKET UPDATE #{report.update_number}: {job.movie} — {effective_date}*",
        f"📅 *Show Date(s):* {header_date}",
        f"📍 *Area:* {job.target_summary}",
    ]

    if report.newly_opened_cinemas:
        count = len(report.newly_opened_cinemas)
        noun = "Cinema" if count == 1 else "Cinemas"
        lines.extend(
            [
                "",
                f"🟢 *NEWLY OPENED ({count} {noun})*",
                "────────────────────",
            ]
        )
        for idx, venue in enumerate(report.newly_opened_cinemas, start=1):
            dist_str = (
                f" ({venue.distance_km:.1f} km away)"
                if venue.distance_km is not None
                else ""
            )
            plat_names = " & ".join([p for p, _ in venue.all_platform_links()])
            lines.append(f"{_number_badge(idx)} *{venue.name}*{dist_str}")
            lines.append(f"🌐 Platform: {plat_names}")

            # Group showtimes by date (or show date header)
            slots_by_date: dict[Optional[str], list[ShowtimeSlot]] = {}
            for slot in venue.showtimes:
                s_date = slot.date or venue.show_date
                slots_by_date.setdefault(s_date, []).append(slot)

            lines.append("🕒 Showtimes & Seats:")
            if len(slots_by_date) > 1:
                for d_iso, slots_in_date in slots_by_date.items():
                    d_header = f"  📅 *{_friendly_date(d_iso)}:*" if d_iso else "  📅 *Showtimes:*"
                    lines.append(d_header)
                    for slot in slots_in_date:
                        fmt_part = f" ({slot.screen_format})" if slot.screen_format else ""
                        lines.append(
                            f"    • *{slot.time}*{fmt_part} — {slot.format_seat_summary()}"
                        )
            else:
                single_date = next(iter(slots_by_date.keys()), None)
                if single_date and (job.target_date.upper() == "ANY" or single_date != job.target_date):
                    lines.append(f"  📅 Date: *{_friendly_date(single_date)}*")
                for slot in venue.showtimes:
                    fmt_part = f" ({slot.screen_format})" if slot.screen_format else ""
                    lines.append(
                        f"  • *{slot.time}*{fmt_part} — {slot.format_seat_summary()}"
                    )
            lines.append(f"🔗 Book: {venue.booking_url}")
            lines.append("")

    if report.new_showtimes_cinemas:
        count = len(report.new_showtimes_cinemas)
        noun = "Cinema" if count == 1 else "Cinemas"
        lines.extend(
            [
                f"🔵 *NEW SHOWTIMES ADDED ({count} {noun})*",
                "────────────────────",
            ]
        )
        for item in report.new_showtimes_cinemas:
            v = item.venue
            dist_str = f" ({v.distance_km:.1f} km away)" if v.distance_km is not None else ""
            added_parts = []
            for s in item.added_showtimes:
                s_date = s.date or v.show_date
                d_pfx = f"[{_friendly_date(s_date)}] " if (s_date and job.target_date.upper() == "ANY") else ""
                added_parts.append(f"*{d_pfx}{s.time}* ({s.format_seat_summary()})")
            lines.append(f"• *{v.name}*{dist_str}")
            lines.append(f"  ➕ Added: {', '.join(added_parts)}")
            lines.append(f"  🔗 Book: {v.booking_url}")
            lines.append("")

    if report.previously_opened_cinemas:
        lines.extend(
            [
                "⚪ *PREVIOUSLY OPENED (From Earlier Checks)*",
                "────────────────────",
            ]
        )
        for item in report.previously_opened_cinemas:
            v = item.venue
            dist_str = f" ({v.distance_km:.1f} km)" if v.distance_km is not None else ""
            opened_at = f" — _Opened at {v.first_seen_at}_" if v.first_seen_at else ""
            v_date_str = f" [📅 {_friendly_date(v.show_date)}]" if (v.show_date and job.target_date.upper() == "ANY") else ""
            lines.append(f"• *{v.name}*{dist_str}{v_date_str}{opened_at}")
            for slot in v.showtimes:
                slot_key = f"{slot.date or ''}_{slot.time.strip().upper()}"
                prev_slot = item.previous_slots_by_time.get(slot_key) or item.previous_slots_by_time.get(
                    slot.time.strip().upper()
                )
                summary = slot.format_seat_summary(prev_slot=prev_slot)
                d_pfx = f"[{_friendly_date(slot.date)}] " if (slot.date and job.target_date.upper() == "ANY") else ""
                lines.append(f"  • {d_pfx}{slot.time} — {summary}")
        lines.append("")

    lines.append(
        f"🛑 _Keep waiting for better screens, or run `ticketfinder stop{job_id_str}` once booked._"
    )
    return "\n".join(lines).strip()


def _format_ascii_table(slots: list[ShowtimeSlot], show_date_col: bool = False) -> str:
    """Formats a neat ASCII box table for plain-text email & terminal output."""
    if show_date_col:
        headers = ("Date", "Showtime", "Screen/Format", "Status", "Seats (Avail / Booked)")
        rows: list[tuple[str, str, str, str, str]] = []
        for s in slots:
            d_str = _friendly_date(s.date) if s.date else "-"
            status_col = f"{s.status_emoji} {s.status_label}"
            if s.available_seats is not None and s.booked_seats is not None:
                pct = int(round(s.occupancy_pct or 0))
                seats_col = f"{s.available_seats} avail / {s.booked_seats} booked ({pct}%)"
            elif s.category_info:
                seats_col = s.category_info
            else:
                seats_col = s.status_label
            rows.append((d_str, s.time, s.screen_format or "2D", status_col, seats_col))

        widths = [
            max(len(headers[i]), *(len(r[i]) for r in rows)) if rows else len(headers[i])
            for i in range(5)
        ]

        top = "   ┌─" + "─┬─".join("─" * w for w in widths) + "─┐"
        head = "   │ " + " │ ".join(headers[i].ljust(widths[i]) for i in range(5)) + " │"
        mid = "   ├─" + "─┼─".join("─" * w for w in widths) + "─┤"
        bot = "   └─" + "─┴─".join("─" * w for w in widths) + "─┘"

        body_lines = [
            "   │ " + " │ ".join(r[i].ljust(widths[i]) for i in range(5)) + " │"
            for r in rows
        ]
        return "\n".join([top, head, mid, *body_lines, bot])

    headers = ("Showtime", "Screen/Format", "Status", "Seats (Avail / Booked)")
    rows_4: list[tuple[str, str, str, str]] = []
    for s in slots:
        status_col = f"{s.status_emoji} {s.status_label}"
        if s.available_seats is not None and s.booked_seats is not None:
            pct = int(round(s.occupancy_pct or 0))
            seats_col = f"{s.available_seats} avail / {s.booked_seats} booked ({pct}%)"
        elif s.category_info:
            seats_col = s.category_info
        else:
            seats_col = s.status_label
        rows_4.append((s.time, s.screen_format or "2D", status_col, seats_col))

    widths = [
        max(len(headers[i]), *(len(r[i]) for r in rows_4)) if rows_4 else len(headers[i])
        for i in range(4)
    ]

    top = "   ┌─" + "─┬─".join("─" * w for w in widths) + "─┐"
    head = "   │ " + " │ ".join(headers[i].ljust(widths[i]) for i in range(4)) + " │"
    mid = "   ├─" + "─┼─".join("─" * w for w in widths) + "─┤"
    bot = "   └─" + "─┴─".join("─" * w for w in widths) + "─┘"

    body_lines = [
        "   │ " + " │ ".join(r[i].ljust(widths[i]) for i in range(4)) + " │"
        for r in rows_4
    ]
    return "\n".join([top, head, mid, *body_lines, bot])


def render_email_content(
    report: DiffReport,
    is_daemon_running: bool = False,
) -> tuple[str, str, str]:
    """
    Renders (subject, plain_text_body, html_body) for the Gmail SMTP notifier.
    """
    job = report.job
    date_label = _friendly_date(job.target_date)
    job_id_str = f" --id {job.id}" if job.id else ""

    if not report.any_tickets_open:
        city_slug = job.city.lower().strip().replace(" ", "-")
        bms_city_url = f"https://in.bookmyshow.com/explore/movies-{city_slug}"
        tn_city_url = f"https://ticketnew.com/movies/{city_slug}"
        dist_city_url = "https://www.district.in/movies/"

        subject = (
            f"🤖 [Watching] {job.movie} on {date_label} ({job.target_summary}) — Checked All Sites"
        )
        text_body = "\n".join(
            [
                "=" * 70,
                f"🤖 TICKET WATCHER ACTIVE: {job.movie.upper()}",
                f"📅 Date: {date_label}   |   📍 Target: {job.target_summary}",
                "=" * 70,
                "",
                f"⏳ Current Status : Bookings NOT open yet for {date_label}",
                f"🌐 Sites Checked  : {', '.join(report.sites_checked)}",
                f"🕒 Checked At     : {report.checked_at}",
                "",
                f"🔗 BookMyShow ({job.city}) : {bms_city_url}",
                f"🔗 TicketNew ({job.city})  : {tn_city_url}",
                f"🔗 District Movies         : {dist_city_url}",
                "",
                "✅ Your Mac watcher is active and will check every 30 minutes.",
                "   You will receive an email & WhatsApp alert as soon as tickets open!",
                "",
                "-" * 70,
                f"🛑 To stop watching anytime, run: ticketfinder stop{job_id_str}",
                "=" * 70,
            ]
        )
        html_body = f"""
        <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 680px; margin: 0 auto; border: 1px solid #e5e7eb; border-radius: 10px; overflow: hidden;">
          <div style="background: #1e293b; color: #ffffff; padding: 18px 24px;">
            <h2 style="margin: 0; font-size: 18px;">🤖 Ticket Watcher Active: {html.escape(job.movie)}</h2>
            <p style="margin: 6px 0 0; color: #cbd5e1; font-size: 14px;">📅 {html.escape(date_label)} &nbsp;|&nbsp; 📍 {html.escape(job.target_summary)}</p>
          </div>
          <div style="padding: 20px 24px; color: #1f2937; line-height: 1.6;">
            <p><strong>⏳ Current Status:</strong> Bookings are <strong>not open yet</strong> for {html.escape(date_label)}.</p>
            <p><strong>🌐 Platforms Checked:</strong> {html.escape(', '.join(report.sites_checked))}</p>
            <div style="margin: 14px 0;">
              <a href="{html.escape(bms_city_url)}" style="display:inline-block; background:#dc2626; color:#ffffff; text-decoration:none; padding:7px 12px; border-radius:6px; font-weight:600; font-size:12px; margin-right:8px;">BookMyShow ({html.escape(job.city)})</a>
              <a href="{html.escape(tn_city_url)}" style="display:inline-block; background:#2563eb; color:#ffffff; text-decoration:none; padding:7px 12px; border-radius:6px; font-weight:600; font-size:12px; margin-right:8px;">TicketNew ({html.escape(job.city)})</a>
              <a href="{html.escape(dist_city_url)}" style="display:inline-block; background:#4f46e5; color:#ffffff; text-decoration:none; padding:7px 12px; border-radius:6px; font-weight:600; font-size:12px;">District Movies</a>
            </div>
            <p style="background: #f0fdf4; border-left: 4px solid #22c55e; padding: 12px; border-radius: 4px;">
              ✅ Your Mac background agent will check every <strong>30 minutes</strong> and send the full cinema + seat occupancy table as soon as bookings open.
            </p>
            <hr style="border: none; border-top: 1px solid #e5e7eb; margin: 20px 0;" />
            <p style="font-size: 12px; color: #6b7280;">🛑 To stop anytime, run: <code>ticketfinder stop{html.escape(job_id_str)}</code></p>
          </div>
        </div>
        """
        return subject, text_body, html_body.strip()

    # Tickets are open: build rich diff email
    effective_date_str = _effective_date_label(report)
    new_count = len(report.newly_opened_cinemas)
    if new_count > 0:
        preview_names = ", ".join(v.name.split(",")[0] for v in report.newly_opened_cinemas[:2])
        noun = "Cinema Opens" if new_count == 1 else "NEW Cinemas Open"
        subject = f"🎟️ [Update #{report.update_number}] {new_count} {noun} for {job.movie} ({effective_date_str}) — {preview_names}"
    else:
        subject = f"🎟️ [Update #{report.update_number}] New Showtimes / Seat Update for {job.movie} ({effective_date_str})"

    header_date_str = (
        f"{effective_date_str} (Watch: Any Date)"
        if job.target_date.upper() == "ANY"
        else effective_date_str
    )
    text_lines = [
        "=" * 70,
        f"🎟️ MOVIE TICKET UPDATE #{report.update_number}: {job.movie.upper()}",
        f"📅 Date: {header_date_str}   |   📍 Near: {job.target_summary}",
        "=" * 70,
        "",
    ]

    html_sections: list[str] = []

    # Section 1: Newly Opened Cinemas
    if report.newly_opened_cinemas:
        c_len = len(report.newly_opened_cinemas)
        noun = "Cinema" if c_len == 1 else "Cinemas"
        text_lines.append(f"🟢 NEWLY OPENED SINCE LAST ALERT ({c_len} {noun})")
        text_lines.append("-" * 70)

        html_cards: list[str] = []
        for idx, venue in enumerate(report.newly_opened_cinemas, start=1):
            badge = venue.distance_badge(job.area)
            plats = ", ".join(p for p, _ in venue.all_platform_links())
            venue_dates = venue.dates
            v_date_str = ", ".join(_friendly_date(d) for d in venue_dates) if venue_dates else ""
            date_info = f"  [📅 {v_date_str}]" if v_date_str and job.target_date.upper() == "ANY" else ""

            text_lines.append(f"{idx}. {venue.name}  [📍 {badge}]{date_info}")
            text_lines.append(f"   Platforms: {plats}")
            if v_date_str and (job.target_date.upper() == "ANY" or len(venue_dates) > 1):
                text_lines.append(f"   Date: {v_date_str}")
            text_lines.append("   Showtimes & Seat Occupancy:")
            show_date_col = bool(job.target_date.upper() == "ANY" or any(s.date for s in venue.showtimes))
            text_lines.append(_format_ascii_table(venue.showtimes, show_date_col=show_date_col))
            links_txt = "   |   ".join(
                f"👉 [Book on {p}]: {u}" for p, u in venue.all_platform_links()
            )
            text_lines.append(f"   {links_txt}")
            text_lines.append("")

            # HTML table rows for showtimes
            tr_list: list[str] = []
            for s in venue.showtimes:
                s_date_str = _friendly_date(s.date or venue.show_date)
                date_td = (
                    f'<td style="padding: 8px 10px; border-bottom: 1px solid #e5e7eb; font-weight: 600; color: #1e40af;">📅 {html.escape(s_date_str)}</td>'
                    if show_date_col
                    else ""
                )
                if s.available_seats is not None and s.booked_seats is not None:
                    pct = int(round(s.occupancy_pct or 0))
                    seats_html = f"<strong>{s.available_seats} avail</strong> / {s.booked_seats} booked ({pct}% full)"
                elif s.category_info:
                    seats_html = html.escape(s.category_info)
                else:
                    seats_html = html.escape(s.status_label)
                tr_list.append(
                    f"""
                    <tr>
                      {date_td}
                      <td style="padding: 8px 10px; border-bottom: 1px solid #e5e7eb; font-weight: 600;">{html.escape(s.time)}</td>
                      <td style="padding: 8px 10px; border-bottom: 1px solid #e5e7eb;">{html.escape(s.screen_format or '2D')}</td>
                      <td style="padding: 8px 10px; border-bottom: 1px solid #e5e7eb;">{s.status_emoji} {html.escape(s.status_label)}</td>
                      <td style="padding: 8px 10px; border-bottom: 1px solid #e5e7eb;">{seats_html}</td>
                    </tr>
                    """
                )

            date_th = (
                '<th style="padding: 8px 10px; border-bottom: 1px solid #e2e8f0;">Date</th>'
                if show_date_col
                else ""
            )
            date_badge_html = (
                f'<span style="font-size: 12px; font-weight: 600; background: #dbeafe; color: #1e40af; padding: 3px 8px; border-radius: 12px; margin-left: 6px;">📅 {html.escape(v_date_str)}</span>'
                if v_date_str
                else ""
            )
            btns_html = " ".join(
                f'<a href="{html.escape(u)}" style="display:inline-block; background:#dc2626; color:#ffffff; text-decoration:none; padding:8px 14px; border-radius:6px; font-weight:600; font-size:13px; margin-right:8px;">👉 Book on {html.escape(p)}</a>'
                for p, u in venue.all_platform_links()
            )
            html_cards.append(
                f"""
                <div style="border: 1px solid #bbf7d0; background: #f0fdf4; border-radius: 8px; padding: 14px 16px; margin-bottom: 14px;">
                  <div style="font-size: 16px; font-weight: 700; color: #14532d;">
                    {idx}. {html.escape(venue.name)}
                    <span style="font-size: 12px; font-weight: 600; background: #dcfce7; color: #166534; padding: 3px 8px; border-radius: 12px; margin-left: 6px;">📍 {html.escape(badge)}</span>
                    {date_badge_html}
                  </div>
                  <div style="font-size: 13px; color: #374151; margin: 4px 0 10px;">Platforms: <strong>{html.escape(plats)}</strong></div>
                  <table style="width: 100%; border-collapse: collapse; background: #ffffff; border-radius: 6px; overflow: hidden; font-size: 13px; margin-bottom: 12px;">
                    <thead>
                      <tr style="background: #f8fafc; text-align: left; color: #475569;">
                        {date_th}
                        <th style="padding: 8px 10px; border-bottom: 1px solid #e2e8f0;">Showtime</th>
                        <th style="padding: 8px 10px; border-bottom: 1px solid #e2e8f0;">Screen/Format</th>
                        <th style="padding: 8px 10px; border-bottom: 1px solid #e2e8f0;">Status</th>
                        <th style="padding: 8px 10px; border-bottom: 1px solid #e2e8f0;">Seats (Avail / Booked)</th>
                      </tr>
                    </thead>
                    <tbody>{''.join(tr_list)}</tbody>
                  </table>
                  <div>{btns_html}</div>
                </div>
                """
            )

        html_sections.append(
            f"""
            <h3 style="color: #15803d; border-bottom: 2px solid #bbf7d0; padding-bottom: 6px; margin-top: 20px;">
              🟢 Newly Opened Since Last Alert ({c_len} {noun})
            </h3>
            {''.join(html_cards)}
            """
        )

    # Section 2: New Showtimes Added
    if report.new_showtimes_cinemas:
        c_len = len(report.new_showtimes_cinemas)
        noun = "Cinema" if c_len == 1 else "Cinemas"
        text_lines.append(f"🔵 NEW SHOWTIMES ADDED TO EXISTING CINEMAS ({c_len} {noun})")
        text_lines.append("-" * 70)

        added_html_items: list[str] = []
        for item in report.new_showtimes_cinemas:
            v = item.venue
            badge = v.distance_badge(job.area)
            v_dates = v.dates
            v_date_str = ", ".join(_friendly_date(d) for d in v_dates) if v_dates else ""
            date_info = f" [📅 {v_date_str}]" if v_date_str and job.target_date.upper() == "ANY" else ""
            text_lines.append(f"• {v.name} [📍 {badge}]{date_info}")
            slot_lis: list[str] = []
            for s in item.added_showtimes:
                summary = s.format_seat_summary()
                s_date_str = f"[{_friendly_date(s.date or v.show_date)}] " if (s.date or v.show_date) and job.target_date.upper() == "ANY" else ""
                text_lines.append(f"  + NEW {s_date_str}{s.time} ({s.screen_format}) : {summary}")
                slot_lis.append(
                    f"<li><strong>+ NEW {html.escape(s_date_str)}{html.escape(s.time)}</strong> ({html.escape(s.screen_format)}) — {html.escape(summary)}</li>"
                )
            text_lines.append(f"  👉 [Book on {v.platform}]: {v.booking_url}")
            text_lines.append("")

            date_badge_html = (
                f'<span style="font-size:12px; background:#dbeafe; color:#1e40af; padding:2px 6px; border-radius:10px; margin-left:6px;">📅 {html.escape(v_date_str)}</span>'
                if v_date_str and job.target_date.upper() == "ANY"
                else ""
            )
            added_html_items.append(
                f"""
                <div style="border: 1px solid #bfdbfe; background: #eff6ff; border-radius: 8px; padding: 12px 16px; margin-bottom: 12px;">
                  <div style="font-weight: 700; color: #1e3a8a;">• {html.escape(v.name)} <span style="font-size:12px; color:#1d4ed8;">[📍 {html.escape(badge)}]</span>{date_badge_html}</div>
                  <ul style="margin: 8px 0; padding-left: 20px; font-size: 13px;">{''.join(slot_lis)}</ul>
                  <a href="{html.escape(v.booking_url)}" style="font-size: 13px; font-weight: 600; color: #2563eb;">👉 Book on {html.escape(v.platform)}</a>
                </div>
                """
            )

        html_sections.append(
            f"""
            <h3 style="color: #1d4ed8; border-bottom: 2px solid #bfdbfe; padding-bottom: 6px; margin-top: 20px;">
              🔵 New Showtimes Added to Existing Cinemas ({c_len} {noun})
            </h3>
            {''.join(added_html_items)}
            """
        )

    # Section 3: Previously Opened Cinemas (Updated Occupancy)
    if report.previously_opened_cinemas:
        text_lines.append("⚪ PREVIOUSLY OPENED CINEMAS (Updated Occupancy)")
        text_lines.append("-" * 70)

        prev_html_items: list[str] = []
        for item in report.previously_opened_cinemas:
            v = item.venue
            badge = v.distance_badge(job.area)
            first_seen = f" — First opened at {v.first_seen_at}" if v.first_seen_at else ""
            v_dates = v.dates
            v_date_str = f" [📅 {', '.join(_friendly_date(d) for d in v_dates)}]" if v_dates and job.target_date.upper() == "ANY" else ""
            text_lines.append(f"• {v.name} [📍 {badge}]{v_date_str}{first_seen}")
            slot_lis = []
            for s in v.showtimes:
                slot_key = f"{s.date or ''}_{s.time.strip().upper()}"
                prev_slot = item.previous_slots_by_time.get(slot_key) or item.previous_slots_by_time.get(
                    s.time.strip().upper()
                )
                summary = s.format_seat_summary(prev_slot=prev_slot)
                s_date_str = f"[{_friendly_date(s.date or v.show_date)}] " if (s.date or v.show_date) and job.target_date.upper() == "ANY" else ""
                text_lines.append(f"  - {s_date_str}{s.time} : {summary}")
                slot_lis.append(f"<li><strong>{html.escape(s_date_str)}{html.escape(s.time)}</strong> : {html.escape(summary)}</li>")
            prev_html_items.append(
                f"""
                <div style="border: 1px solid #e5e7eb; background: #f9fafb; border-radius: 8px; padding: 12px 16px; margin-bottom: 10px;">
                  <div style="font-weight: 600; color: #374151;">• {html.escape(v.name)} <span style="font-size:12px; color:#6b7280;">[📍 {html.escape(badge)}]{html.escape(v_date_str)}{html.escape(first_seen)}</span></div>
                  <ul style="margin: 6px 0 0; padding-left: 20px; font-size: 13px; color: #4b5563;">{''.join(slot_lis)}</ul>
                </div>
                """
            )
        text_lines.append("")

        html_sections.append(
            f"""
            <h3 style="color: #4b5563; border-bottom: 2px solid #e5e7eb; padding-bottom: 6px; margin-top: 20px;">
              ⚪ Previously Opened Cinemas (Updated Occupancy)
            </h3>
            {''.join(prev_html_items)}
            """
        )

    text_lines.extend(
        [
            "-" * 70,
            "ℹ️ Bot Status: Active on your Mac (Checks every 30 minutes)",
            "🛑 Bought your tickets? Stop this watch by running:",
            f"   ticketfinder stop{job_id_str}   (or `ticketfinder stop` to stop all)",
            "=" * 70,
        ]
    )

    html_body = f"""
    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 720px; margin: 0 auto; border: 1px solid #e5e7eb; border-radius: 10px; overflow: hidden;">
      <div style="background: #111827; color: #ffffff; padding: 18px 24px;">
        <h2 style="margin: 0; font-size: 19px;">🎟️ Movie Ticket Update #{report.update_number}: {html.escape(job.movie)}</h2>
        <p style="margin: 6px 0 0; color: #d1d5db; font-size: 14px;">📅 {html.escape(header_date_str)} &nbsp;|&nbsp; 📍 {html.escape(job.target_summary)}</p>
      </div>
      <div style="padding: 18px 24px; color: #1f2937;">
        {''.join(html_sections)}
        <hr style="border: none; border-top: 1px solid #e5e7eb; margin: 22px 0 14px;" />
        <p style="font-size: 12px; color: #6b7280; margin: 0;">
          🛑 Bought your tickets? Stop this watch by running: <code>ticketfinder stop{html.escape(job_id_str)}</code>
        </p>
      </div>
    </div>
    """

    return subject, "\n".join(text_lines), html_body.strip()


def render_multi_whatsapp_message(
    reports: list[DiffReport],
    is_daemon_running: bool = False,
    is_heartbeat: bool = False,
) -> str:
    """
    Renders a single consolidated WhatsApp notification across one or more target dates
    for the same movie, including 1-hour batch job heartbeat status.
    """
    if not reports:
        return ""
    if len(reports) == 1 and not is_heartbeat:
        return render_whatsapp_message(reports[0], is_daemon_running=is_daemon_running)

    ordered = sorted(reports, key=lambda r: r.job.target_date)
    first_job = ordered[0].job
    date_labels = [_effective_date_label(r) for r in ordered]
    combined_dates_str = " & ".join(date_labels)
    any_open = any(r.any_tickets_open for r in ordered)
    any_initial = any(r.is_initial_check for r in ordered)
    sites_checked = ordered[0].sites_checked
    checked_at = ordered[0].checked_at
    total_checks = max((r.job.check_count for r in ordered), default=1) + 1
    stop_cmd = f'ticketfinder stop --movie "{first_job.movie.split()[0]}"'

    if not any_open:
        if is_heartbeat:
            header = "💓 *4-HOUR HEARTBEAT: TICKET WATCHER ACTIVE*"
        elif any_initial:
            header = "🤖 *TICKET WATCHER ACTIVE (5-Min Initial Check)*"
        else:
            header = "🤖 *TICKET WATCHER STATUS*"

        lines = [
            header,
            f"🎬 *Movie:* {first_job.movie}",
            f"📅 *Dates Watched ({len(ordered)}):* {combined_dates_str}",
            f"📍 *Target:* {first_job.target_summary}",
            f"🕒 *Checked At:* {checked_at} (Batch Check #{total_checks})",
            "",
            "📊 *Status by Date:*",
        ]
        for r in ordered:
            d_lbl = _effective_date_label(r)
            lines.append(f"  • *{d_lbl}*: ⏳ Bookings NOT open yet")

        lines.extend(
            [
                "",
                f"🌐 *Sites Checked:* {', '.join(sites_checked)}",
            ]
        )
        if is_daemon_running or is_heartbeat:
            lines.extend(
                [
                    "",
                    "✅ *Batch Job Health:* Running in background (checks every *30 mins*, heartbeat every *4 hours*). You will get an instant alert as soon as tickets open!",
                    f"🛑 _To stop anytime: `{stop_cmd}`_",
                ]
            )
        return "\n".join(lines).strip()

    # At least one date has open cinemas
    max_update = max(r.update_number for r in ordered)
    prefix = "💓 *4-HOUR HEARTBEAT + TICKET STATUS" if is_heartbeat else f"🎟️ *TICKET UPDATE #{max_update}"
    lines = [
        f"{prefix}: {first_job.movie}*",
        f"📅 *Dates ({len(ordered)}):* {combined_dates_str}",
        f"📍 *Area:* {first_job.target_summary}",
    ]

    for r in ordered:
        d_lbl = _effective_date_label(r)
        lines.extend(
            [
                "",
                f"════════════════════",
                f"📅 *{d_lbl.upper()}*",
                f"════════════════════",
            ]
        )
        if not r.any_tickets_open:
            lines.append("⏳ _Bookings NOT open yet for this date._")
            continue

        if r.newly_opened_cinemas:
            c_cnt = len(r.newly_opened_cinemas)
            noun = "Cinema" if c_cnt == 1 else "Cinemas"
            lines.append(f"🟢 *NEWLY OPENED ({c_cnt} {noun})*")
            for idx, venue in enumerate(r.newly_opened_cinemas, start=1):
                dist_str = (
                    f" ({venue.distance_km:.1f} km away)"
                    if venue.distance_km is not None
                    else ""
                )
                plat_names = " & ".join([p for p, _ in venue.all_platform_links()])
                v_dates = venue.dates
                v_date_str = f" [📅 {', '.join(_friendly_date(d) for d in v_dates)}]" if v_dates and r.job.target_date.upper() == "ANY" else ""
                lines.append(f"{_number_badge(idx)} *{venue.name}*{dist_str}{v_date_str} [{plat_names}]")
                for slot in venue.showtimes:
                    fmt_part = f" ({slot.screen_format})" if slot.screen_format else ""
                    d_pfx = f"[{_friendly_date(slot.date)}] " if (slot.date and r.job.target_date.upper() == "ANY") else ""
                    lines.append(
                        f"  • *{d_pfx}{slot.time}*{fmt_part} — {slot.format_seat_summary()}"
                    )
                lines.append(f"🔗 Book: {venue.booking_url}")

        if r.new_showtimes_cinemas:
            c_cnt = len(r.new_showtimes_cinemas)
            noun = "Cinema" if c_cnt == 1 else "Cinemas"
            lines.append(f"🔵 *NEW SHOWTIMES ADDED ({c_cnt} {noun})*")
            for item in r.new_showtimes_cinemas:
                v = item.venue
                dist_str = f" ({v.distance_km:.1f} km away)" if v.distance_km is not None else ""
                v_dates = v.dates
                v_date_str = f" [📅 {', '.join(_friendly_date(d) for d in v_dates)}]" if v_dates and r.job.target_date.upper() == "ANY" else ""
                added_parts = []
                for s in item.added_showtimes:
                    d_pfx = f"[{_friendly_date(s.date or v.show_date)}] " if (s.date or v.show_date) and r.job.target_date.upper() == "ANY" else ""
                    added_parts.append(f"*{d_pfx}{s.time}* ({s.format_seat_summary()})")
                lines.append(f"• *{v.name}*{dist_str}{v_date_str}: {', '.join(added_parts)}")
                lines.append(f"  🔗 Book: {v.booking_url}")

        if r.previously_opened_cinemas:
            lines.append("⚪ *PREVIOUSLY OPENED (Updated Seats)*")
            for item in r.previously_opened_cinemas:
                v = item.venue
                dist_str = f" ({v.distance_km:.1f} km)" if v.distance_km is not None else ""
                v_dates = v.dates
                v_date_str = f" [📅 {', '.join(_friendly_date(d) for d in v_dates)}]" if v_dates and r.job.target_date.upper() == "ANY" else ""
                lines.append(f"• *{v.name}*{dist_str}{v_date_str}")
                for slot in v.showtimes:
                    slot_key = f"{slot.date or ''}_{slot.time.strip().upper()}"
                    prev_slot = item.previous_slots_by_time.get(slot_key) or item.previous_slots_by_time.get(
                        slot.time.strip().upper()
                    )
                    d_pfx = f"[{_friendly_date(slot.date)}] " if (slot.date and r.job.target_date.upper() == "ANY") else ""
                    lines.append(f"  • {d_pfx}{slot.time} — {slot.format_seat_summary(prev_slot=prev_slot)}")

    lines.extend(
        [
            "",
            f"🛑 _Once booked, run `{stop_cmd}` to stop._",
        ]
    )
    return "\n".join(lines).strip()


def render_multi_email_content(
    reports: list[DiffReport],
    is_daemon_running: bool = False,
    is_heartbeat: bool = False,
) -> tuple[str, str, str]:
    """
    Renders (subject, plain_text_body, html_body) consolidating one or more target dates
    for the same movie into a single email, with 1-hour heartbeat support.
    """
    if not reports:
        return "", "", ""
    if len(reports) == 1 and not is_heartbeat:
        return render_email_content(reports[0], is_daemon_running=is_daemon_running)

    ordered = sorted(reports, key=lambda r: r.job.target_date)
    first_job = ordered[0].job
    date_labels = [_effective_date_label(r) for r in ordered]
    combined_dates_str = " & ".join(date_labels)
    any_open = any(r.any_tickets_open for r in ordered)
    sites_checked = ordered[0].sites_checked
    checked_at = ordered[0].checked_at
    total_checks = max((r.job.check_count for r in ordered), default=1) + 1
    stop_cmd = f'ticketfinder stop --movie "{first_job.movie.split()[0]}"'

    city_slug = first_job.city.lower().strip().replace(" ", "-")
    bms_city_url = f"https://in.bookmyshow.com/explore/movies-{city_slug}"
    tn_city_url = f"https://ticketnew.com/movies/{city_slug}"
    dist_city_url = "https://www.district.in/movies/"

    if not any_open:
        if is_heartbeat:
            subject = (
                f"💓 [4-Hour Heartbeat] Watcher Active for {first_job.movie} "
                f"({combined_dates_str}) — Checked at {checked_at}"
            )
            title_banner = f"💓 4-Hour Heartbeat — Ticket Watcher Active: {first_job.movie}"
            header_bg = "#0f172a"
        else:
            subject = (
                f"🤖 [Watching] {first_job.movie} on {combined_dates_str} "
                f"({first_job.target_summary}) — Checked All Sites"
            )
            title_banner = f"🤖 Ticket Watcher Active: {first_job.movie}"
            header_bg = "#1e293b"

        text_lines = [
            "=" * 70,
            title_banner.upper(),
            f"📅 Dates Watched ({len(ordered)}): {combined_dates_str}",
            f"📍 Target: {first_job.target_summary}",
            "=" * 70,
            "",
            "📊 Status by Target Date:",
        ]
        html_date_rows: list[str] = []
        for r in ordered:
            d_lbl = _effective_date_label(r)
            date_code = f" ({r.job.target_date})" if r.job.target_date.upper() != "ANY" else ""
            text_lines.append(f"  • {d_lbl}{date_code} : ⏳ Bookings NOT open yet")
            html_date_rows.append(
                f"""
                <tr>
                  <td style="padding: 10px 12px; border-bottom: 1px solid #e2e8f0; font-weight: 600;">📅 {html.escape(d_lbl)} <span style="color:#64748b; font-weight:400;">{html.escape(date_code)}</span></td>
                  <td style="padding: 10px 12px; border-bottom: 1px solid #e2e8f0; color: #b45309; font-weight: 600;">⏳ Bookings NOT open yet</td>
                </tr>
                """
            )

        text_lines.extend(
            [
                "",
                f"🌐 Sites Checked  : {', '.join(sites_checked)}",
                f"🕒 Checked At     : {checked_at} (Batch Check #{total_checks})",
                "",
                f"🔗 BookMyShow ({first_job.city}) : {bms_city_url}",
                f"🔗 TicketNew ({first_job.city})  : {tn_city_url}",
                f"🔗 District Movies         : {dist_city_url}",
                "",
                "✅ Batch Job Status: Active on your Mac (checks every 30 minutes, heartbeat every 4 hours).",
                "   You will receive an immediate alert as soon as tickets open for any watched date!",
                "",
                "-" * 70,
                f"🛑 To stop watching anytime, run: {stop_cmd}",
                "=" * 70,
            ]
        )

        html_body = f"""
        <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 680px; margin: 0 auto; border: 1px solid #e5e7eb; border-radius: 10px; overflow: hidden;">
          <div style="background: {header_bg}; color: #ffffff; padding: 18px 24px;">
            <h2 style="margin: 0; font-size: 18px;">{html.escape(title_banner)}</h2>
            <p style="margin: 6px 0 0; color: #cbd5e1; font-size: 14px;">📅 {html.escape(combined_dates_str)} &nbsp;|&nbsp; 📍 {html.escape(first_job.target_summary)}</p>
          </div>
          <div style="padding: 20px 24px; color: #1f2937; line-height: 1.6;">
            <p style="margin-top:0;"><strong>🕒 Checked At:</strong> {html.escape(checked_at)} &nbsp;<span style="background:#e2e8f0; color:#334155; padding:2px 8px; border-radius:10px; font-size:12px;">Batch Check #{total_checks}</span></p>
            <table style="width: 100%; border-collapse: collapse; background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px; overflow: hidden; font-size: 13px; margin: 12px 0 16px;">
              <thead>
                <tr style="background: #f1f5f9; text-align: left; color: #475569;">
                  <th style="padding: 9px 12px; border-bottom: 1px solid #e2e8f0;">Target Date</th>
                  <th style="padding: 9px 12px; border-bottom: 1px solid #e2e8f0;">Booking Status</th>
                </tr>
              </thead>
              <tbody>
                {''.join(html_date_rows)}
              </tbody>
            </table>
            <p><strong>🌐 Platforms Checked:</strong> {html.escape(', '.join(sites_checked))}</p>
            <div style="margin: 14px 0;">
              <a href="{html.escape(bms_city_url)}" style="display:inline-block; background:#dc2626; color:#ffffff; text-decoration:none; padding:7px 12px; border-radius:6px; font-weight:600; font-size:12px; margin-right:8px;">BookMyShow ({html.escape(first_job.city)})</a>
              <a href="{html.escape(tn_city_url)}" style="display:inline-block; background:#2563eb; color:#ffffff; text-decoration:none; padding:7px 12px; border-radius:6px; font-weight:600; font-size:12px; margin-right:8px;">TicketNew ({html.escape(first_job.city)})</a>
              <a href="{html.escape(dist_city_url)}" style="display:inline-block; background:#4f46e5; color:#ffffff; text-decoration:none; padding:7px 12px; border-radius:6px; font-weight:600; font-size:12px;">District Movies</a>
            </div>
            <p style="background: #f0fdf4; border-left: 4px solid #22c55e; padding: 12px; border-radius: 4px;">
              💓 <strong>Batch Job Healthy:</strong> Polling every <strong>30 minutes</strong> with a 4-hour heartbeat. As soon as bookings open on any watched date, you'll receive the full cinema &amp; seat availability table immediately.
            </p>
            <hr style="border: none; border-top: 1px solid #e5e7eb; margin: 20px 0;" />
            <p style="font-size: 12px; color: #6b7280;">🛑 To stop anytime, run: <code>{html.escape(stop_cmd)}</code></p>
          </div>
        </div>
        """
        return subject, "\n".join(text_lines), html_body.strip()

    # Tickets are open on at least one date: build a consolidated multi-date email
    total_new = sum(len(r.newly_opened_cinemas) for r in ordered)
    max_update = max(r.update_number for r in ordered)
    if total_new > 0:
        subject = (
            f"🎟️ [Update #{max_update}] Tickets Open for {first_job.movie} "
            f"({combined_dates_str}) — {total_new} New Cinema Opening(s)"
        )
    else:
        subject = (
            f"🎟️ [Update #{max_update}] Ticket & Seat Update for {first_job.movie} ({combined_dates_str})"
        )

    text_blocks: list[str] = [
        "=" * 70,
        f"🎟️ CONSOLIDATED TICKET UPDATE #{max_update}: {first_job.movie.upper()}",
        f"📅 Dates ({len(ordered)}): {combined_dates_str}   |   📍 Near: {first_job.target_summary}",
        "=" * 70,
    ]
    html_date_sections: list[str] = []

    for r in ordered:
        d_lbl = _effective_date_label(r)
        date_code = f" ({r.job.target_date})" if r.job.target_date.upper() != "ANY" else ""
        text_blocks.extend(
            [
                "",
                "#" * 70,
                f"📅 DATE: {d_lbl.upper()}{date_code}",
                "#" * 70,
            ]
        )
        if not r.any_tickets_open:
            text_blocks.append(f"⏳ Bookings NOT open yet for {d_lbl}.")
            html_date_sections.append(
                f"""
                <div style="margin-top: 20px; border: 1px solid #e2e8f0; border-radius: 8px; padding: 14px 18px; background: #f8fafc;">
                  <h3 style="margin: 0 0 6px; color: #1e293b; font-size: 16px;">📅 {html.escape(d_lbl)}{html.escape(date_code)}</h3>
                  <p style="margin: 0; color: #b45309; font-weight: 600; font-size: 13px;">⏳ Bookings are not open yet for this date.</p>
                </div>
                """
            )
            continue

        _, single_text, single_html = render_email_content(r, is_daemon_running=is_daemon_running)
        text_blocks.append(single_text)
        html_date_sections.append(
            f"""
            <div style="margin-top: 22px;">
              <div style="background: #1e293b; color: #ffffff; padding: 10px 16px; border-radius: 8px 8px 0 0; font-weight: 700; font-size: 15px;">
                📅 Date: {html.escape(d_lbl)}{html.escape(date_code)}
              </div>
              <div style="border: 1px solid #cbd5e1; border-top: none; border-radius: 0 0 8px 8px; padding: 12px;">
                {single_html}
              </div>
            </div>
            """
        )

    html_body = f"""
    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 740px; margin: 0 auto; border: 1px solid #e5e7eb; border-radius: 10px; overflow: hidden;">
      <div style="background: #111827; color: #ffffff; padding: 18px 24px;">
        <h2 style="margin: 0; font-size: 19px;">🎟️ Consolidated Ticket Update: {html.escape(first_job.movie)}</h2>
        <p style="margin: 6px 0 0; color: #d1d5db; font-size: 14px;">📅 {html.escape(combined_dates_str)} &nbsp;|&nbsp; 📍 {html.escape(first_job.target_summary)}</p>
      </div>
      <div style="padding: 18px 24px; color: #1f2937;">
        {''.join(html_date_sections)}
        <hr style="border: none; border-top: 1px solid #e5e7eb; margin: 22px 0 14px;" />
        <p style="font-size: 12px; color: #6b7280; margin: 0;">
          🛑 Bought your tickets? Stop this watch by running: <code>{html.escape(stop_cmd)}</code>
        </p>
      </div>
    </div>
    """
    return subject, "\n".join(text_blocks), html_body.strip()
