#!/usr/bin/env python3
"""
Fetch a CELCAT group timetable and publish it as an iCalendar feed.

Configured for UVSQ / S5 PHYSIQUE PSC by default.
Uses only the Python standard library so GitHub Actions needs no pip install.
"""
from __future__ import annotations

import datetime as dt
import html
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config.json"
DOCS = ROOT / "docs"
ICAL_PATH = DOCS / "schedule.ics"
STATUS_PATH = DOCS / "status.json"

PARIS = ZoneInfo("Europe/Paris")
UTC = dt.timezone.utc

# CELCAT installations commonly expose one of these equivalent paths.
ENDPOINT_PATHS = (
    "/Calendar/Home/GetCalendarData",
    "/calendar/Home/GetCalendarData",
    "/Home/GetCalendarData",
)

SKIP_CATEGORIES = {"CONGES", "FERIE", "PONT", "VACANCES"}


def load_config() -> dict:
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    cfg["base_url"] = os.getenv("CELCAT_BASE_URL", cfg["base_url"]).rstrip("/")
    cfg["group"] = os.getenv("CELCAT_GROUP", cfg["group"])
    cfg["calendar_name"] = os.getenv("CALENDAR_NAME", cfg["calendar_name"])
    cfg["past_days"] = int(os.getenv("PAST_DAYS", cfg.get("past_days", 14)))
    cfg["future_days"] = int(os.getenv("FUTURE_DAYS", cfg.get("future_days", 180)))
    return cfg


def request_json(url: str, form: dict[str, str]) -> list[dict]:
    body = urllib.parse.urlencode(form).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "User-Agent": (
                "Mozilla/5.0 (compatible; UVSQCalendarBridge/1.0; "
                "+https://github.com/)"
            ),
            "X-Requested-With": "XMLHttpRequest",
            "Referer": url.split("/Home/")[0] + "/",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        charset = resp.headers.get_content_charset() or "utf-8"
        raw = resp.read().decode(charset, errors="replace")
        data = json.loads(raw)
        if not isinstance(data, list):
            raise ValueError(f"Expected a JSON list, got {type(data).__name__}")
        return data


def fetch_events(cfg: dict) -> tuple[list[dict], str]:
    today = dt.datetime.now(PARIS).date()
    start = today - dt.timedelta(days=cfg["past_days"])
    end = today + dt.timedelta(days=cfg["future_days"])

    # CELCAT resource type 103 = group.
    form = {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "resType": "103",
        "calView": "agendaWeek",
        "federationIds[]": cfg["group"],
        "colourScheme": "3",
    }

    errors = []
    for path in ENDPOINT_PATHS:
        url = cfg["base_url"] + path
        try:
            events = request_json(url, form)
            return events, url
        except Exception as exc:
            errors.append(f"{url}: {exc}")

    raise RuntimeError(
        "Could not reach a CELCAT calendar-data endpoint.\n" + "\n".join(errors)
    )


_BR_RE = re.compile(r"<br\s*/?>", re.I)
_TAG_RE = re.compile(r"<[^>]+>")


def clean_html(value: object) -> str:
    text = "" if value is None else str(value)
    text = _BR_RE.sub("\n", text)
    text = _TAG_RE.sub("", text)
    text = html.unescape(text)
    lines = [re.sub(r"\s+", " ", x).strip() for x in text.splitlines()]
    return "\n".join(x for x in lines if x)


def event_lines(event: dict) -> list[str]:
    return clean_html(event.get("description", "")).splitlines()


def event_summary(event: dict, group: str) -> str:
    # CELCAT usually provides "module"; prefer it because it is the course name.
    for key in ("module", "moduleName", "subject", "name"):
        val = clean_html(event.get(key, ""))
        if val:
            return val

    category = clean_html(event.get("eventCategory", "")).upper()
    lines = event_lines(event)

    # Remove lines that are obviously metadata and pick the most course-like line.
    candidates = []
    for line in lines:
        upper = line.upper()
        if line == group:
            continue
        if category and upper == category:
            continue
        if re.fullmatch(r"(COURS|TD|TP|EXAMEN|CONTROLE CONTINU|R[ÉE]UNION.*)", upper):
            continue
        candidates.append(line)

    if candidates:
        # Descriptions often contain "CODE - Course name".
        for line in candidates:
            if " - " in line:
                left, right = line.split(" - ", 1)
                if right.strip():
                    return right.strip()
        return candidates[0]

    return category.title() if category else "Cours UVSQ"


def event_location(event: dict, group: str, summary: str) -> str:
    for key in ("location", "room", "rooms"):
        val = clean_html(event.get(key, ""))
        if val:
            return val

    # Best-effort fallback from CELCAT's HTML description.
    category = clean_html(event.get("eventCategory", "")).upper()
    lines = event_lines(event)
    filtered = []
    for line in lines:
        if line == group or line == summary:
            continue
        if category and line.upper() == category:
            continue
        if " - " in line and summary in line:
            continue
        filtered.append(line)

    # Room names at universities frequently contain a room number, building code,
    # "amphi", "salle", etc. Prefer those rather than guessing blindly.
    roomish = re.compile(
        r"\b(amphi|amphith[eé][aâ]tre|salle|room|bat(?:iment)?|b[âa]t\.?|"
        r"[A-Z]{1,4}[-_. ]?\d{1,4})\b",
        re.I,
    )
    for line in filtered:
        if roomish.search(line):
            return line
    return ""


def parse_celcat_datetime(value: object) -> dt.datetime:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("missing event date")

    # Python accepts offsets and fractional seconds with fromisoformat.
    parsed = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=PARIS)
    return parsed.astimezone(PARIS)


def ics_escape(text: str) -> str:
    return (
        str(text)
        .replace("\\", "\\\\")
        .replace(";", r"\;")
        .replace(",", r"\,")
        .replace("\r\n", r"\n")
        .replace("\n", r"\n")
        .replace("\r", r"\n")
    )


def fold_ics(line: str, limit: int = 73) -> str:
    # RFC 5545 requires folding long content lines. Fold by Unicode chars;
    # calendar clients are tolerant, and this keeps UTF-8 course names intact.
    if len(line) <= limit:
        return line
    pieces = [line[:limit]]
    line = line[limit:]
    while line:
        pieces.append(" " + line[: limit - 1])
        line = line[limit - 1 :]
    return "\r\n".join(pieces)


def dt_local_line(name: str, value: dt.datetime) -> str:
    return f"{name};TZID=Europe/Paris:{value.strftime('%Y%m%dT%H%M%S')}"


def make_uid(event: dict, start: dt.datetime, summary: str) -> str:
    raw_id = clean_html(event.get("id", ""))
    if raw_id:
        return f"{raw_id}@edt.uvsq.fr"
    safe = re.sub(r"[^A-Za-z0-9]+", "-", summary).strip("-")[:40]
    return f"{start.strftime('%Y%m%dT%H%M%S')}-{safe}@edt.uvsq.fr"


def build_ics(events: list[dict], cfg: dict) -> tuple[str, int]:
    now_utc = dt.datetime.now(UTC)
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "PRODID:-//UVSQ CELCAT Calendar Bridge//EN",
        f"X-WR-CALNAME:{ics_escape(cfg['calendar_name'])}",
        "X-WR-TIMEZONE:Europe/Paris",
        "REFRESH-INTERVAL;VALUE=DURATION:PT1H",
        "X-PUBLISHED-TTL:PT1H",
    ]

    kept = 0
    seen = set()

    sortable = []
    for event in events:
        try:
            start = parse_celcat_datetime(event.get("start"))
            end = parse_celcat_datetime(event.get("end"))
        except Exception:
            continue
        sortable.append((start, end, event))

    for start, end, event in sorted(sortable, key=lambda x: x[0]):
        category = clean_html(event.get("eventCategory", ""))
        if category.upper() in SKIP_CATEGORIES:
            continue

        summary = event_summary(event, cfg["group"])
        location = event_location(event, cfg["group"], summary)
        description = clean_html(event.get("description", ""))
        uid = make_uid(event, start, summary)

        dedupe = (uid, start, end)
        if dedupe in seen:
            continue
        seen.add(dedupe)

        ev = [
            "BEGIN:VEVENT",
            f"UID:{ics_escape(uid)}",
            f"DTSTAMP:{now_utc.strftime('%Y%m%dT%H%M%SZ')}",
            dt_local_line("DTSTART", start),
            dt_local_line("DTEND", end),
            f"SUMMARY:{ics_escape(summary)}",
        ]
        if location:
            ev.append(f"LOCATION:{ics_escape(location)}")
        if description:
            ev.append(f"DESCRIPTION:{ics_escape(description)}")
        if category:
            ev.append(f"CATEGORIES:{ics_escape(category)}")
        ev.append("END:VEVENT")

        lines.extend(ev)
        kept += 1

    lines.append("END:VCALENDAR")
    return "\r\n".join(fold_ics(line) for line in lines) + "\r\n", kept


def write_status(*, ok: bool, message: str, endpoint: str = "", event_count: int = 0):
    DOCS.mkdir(parents=True, exist_ok=True)
    payload = {
        "ok": ok,
        "updated_at": dt.datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "event_count": event_count,
        "endpoint": endpoint,
        "message": message,
    }
    STATUS_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def main() -> int:
    cfg = load_config()
    DOCS.mkdir(parents=True, exist_ok=True)

    try:
        events, endpoint = fetch_events(cfg)
        ics, count = build_ics(events, cfg)

        # Refuse to overwrite a healthy feed with an obviously empty response.
        # On the first ever run, an empty feed is still emitted so diagnostics work.
        if count == 0 and ICAL_PATH.exists() and "BEGIN:VEVENT" in ICAL_PATH.read_text(
            encoding="utf-8", errors="ignore"
        ):
            raise RuntimeError(
                "CELCAT returned zero usable events; keeping the previous calendar."
            )

        ICAL_PATH.write_text(ics, encoding="utf-8", newline="")
        write_status(
            ok=True,
            message=f"Calendar generated successfully for {cfg['group']}.",
            endpoint=endpoint,
            event_count=count,
        )
        print(f"Generated {ICAL_PATH} with {count} events from {endpoint}")
        return 0

    except Exception as exc:
        write_status(ok=False, message=str(exc))
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
