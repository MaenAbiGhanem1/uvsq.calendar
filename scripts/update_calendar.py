#!/usr/bin/env python3
"""
UVSQ CELCAT -> organized iCalendar feeds.

Outputs:
  docs/schedule.ics  all classes
  docs/cours.ics     lectures
  docs/td.ics        tutorials
  docs/tp.ics        practicals
  docs/examens.ics   exams/tests
  docs/autres.ics    everything else

Google Calendar does not reliably honor per-event colors from subscribed ICS
files, so category-specific feeds let the user set one stable color per type.
"""
from __future__ import annotations

import datetime as dt
import html
import json
import os
import re
import sys
import unicodedata
import urllib.parse
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config.json"
DOCS = ROOT / "docs"
STATUS_PATH = DOCS / "status.json"

PARIS = ZoneInfo("Europe/Paris")
UTC = dt.timezone.utc

ENDPOINT_PATHS = (
    "/Calendar/Home/GetCalendarData",
    "/calendar/Home/GetCalendarData",
    "/Home/GetCalendarData",
)

SKIP_CATEGORIES = {"CONGES", "FERIE", "PONT", "VACANCES"}

FEEDS = {
    "all": {
        "filename": "schedule.ics",
        "name": "UVSQ — S5 PHYSIQUE PSC",
        "color": "#5F6368",
    },
    "cours": {
        "filename": "cours.ics",
        "name": "UVSQ — COURS",
        "color": "#3F51B5",
    },
    "td": {
        "filename": "td.ics",
        "name": "UVSQ — TD",
        "color": "#039BE5",
    },
    "tp": {
        "filename": "tp.ics",
        "name": "UVSQ — TP",
        "color": "#0B8043",
    },
    "examens": {
        "filename": "examens.ics",
        "name": "UVSQ — EXAMENS",
        "color": "#D50000",
    },
    "autres": {
        "filename": "autres.ics",
        "name": "UVSQ — AUTRES",
        "color": "#616161",
    },
}

_BR_RE = re.compile(r"<br\s*/?>", re.I)
_TAG_RE = re.compile(r"<[^>]+>")

ROOM_WORDS = re.compile(
    r"\b(amphi|amphith[eé][aâ]tre|salle|room|local|lab(?:o|oratoire)?|"
    r"auditorium|gymnase|studio|atelier|bureau|s[.\s-]?\d{2,4}|"
    r"[A-Z]{1,4}[-_. ]?\d{1,4})\b",
    re.I,
)
PLACE_WORDS = re.compile(
    r"\b(campus|b[âa]t(?:iment)?|versailles|guyancourt|v[eé]lizy|"
    r"saint[-\s]?quentin|rambouillet|mantes|universit[eé]|uvsq|"
    r"fermat|vauban|d'alembert|d'alembert|buffon|descartes)\b",
    re.I,
)


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
            "User-Agent": "Mozilla/5.0 (compatible; UVSQCalendarBridge/2.0)",
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
            return request_json(url, form), url
        except Exception as exc:
            errors.append(f"{url}: {exc}")

    raise RuntimeError(
        "Could not reach a CELCAT calendar-data endpoint.\n" + "\n".join(errors)
    )


def clean_html(value: object) -> str:
    text = "" if value is None else str(value)
    text = _BR_RE.sub("\n", text)
    text = _TAG_RE.sub("", text)
    text = html.unescape(text)
    lines = [re.sub(r"\s+", " ", x).strip() for x in text.splitlines()]
    return "\n".join(x for x in lines if x)


def clean_lines(value: object) -> list[str]:
    return [x for x in clean_html(value).splitlines() if x]


def ascii_upper(value: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", value)
        if unicodedata.category(c) != "Mn"
    ).upper()


def feed_key_for_category(category: str) -> str:
    c = ascii_upper(category)
    if re.search(r"\bTD\b|TRAVAUX DIRIGES?", c):
        return "td"
    if re.search(r"\bTP\b|TRAVAUX PRATIQUES?", c):
        return "tp"
    if re.search(r"EXAM|PARTIEL|CONTROLE|CC\b|EVALUATION", c):
        return "examens"
    if re.search(r"COURS|CM\b", c):
        return "cours"
    return "autres"


def category_label(category: str) -> str:
    key = feed_key_for_category(category)
    return {
        "cours": "COURS",
        "td": "TD",
        "tp": "TP",
        "examens": "EXAMEN",
        "autres": category.strip().upper() or "UVSQ",
    }[key]


def split_place_room(entry: str) -> tuple[str, str]:
    entry = entry.strip(" -")
    if not entry:
        return "", ""

    # CELCAT room names commonly use "site / room". Prefer the final slash as
    # the separator so nested site names are preserved.
    if "/" in entry:
        left, right = entry.rsplit("/", 1)
        return left.strip(" -"), right.strip(" -")

    # Also accept "site — room" or "site - Salle 123" when the second half
    # clearly looks like a room.
    for sep in (" — ", " – ", " - "):
        if sep in entry:
            left, right = entry.rsplit(sep, 1)
            if ROOM_WORDS.search(right):
                return left.strip(), right.strip()

    if ROOM_WORDS.search(entry) and not PLACE_WORDS.search(entry):
        return "", entry
    if PLACE_WORDS.search(entry) and not ROOM_WORDS.search(entry):
        return entry, ""

    # Unknown one-line CELCAT room: treat as room rather than inventing a site.
    return "", entry


def unique(items: list[str]) -> list[str]:
    out = []
    seen = set()
    for item in items:
        item = item.strip()
        if item and item.casefold() not in seen:
            seen.add(item.casefold())
            out.append(item)
    return out


def parse_event_fields(event: dict, cfg: dict) -> dict:
    category = clean_html(event.get("eventCategory", ""))
    module = clean_html(event.get("module", ""))
    lines = clean_lines(event.get("description", ""))

    # CELCAT commonly places room resources first, then groups, then module /
    # notes, with the event category also available separately.
    group_idx = None
    for i, line in enumerate(lines):
        if line.casefold() == cfg["group"].casefold():
            group_idx = i
            break

    if group_idx is not None:
        room_candidates = lines[:group_idx]
        after_group = lines[group_idx + 1 :]
    else:
        room_candidates = []
        after_group = lines[:]

    # Remove obvious non-room metadata from the room section.
    room_candidates = [
        x for x in room_candidates
        if x.casefold() != category.casefold()
        and (not module or x.casefold() != module.casefold())
        and x.casefold() != cfg["group"].casefold()
    ]

    places, rooms = [], []
    for line in room_candidates:
        place, room = split_place_room(line)
        if place:
            places.append(place)
        if room:
            rooms.append(room)

    places = unique(places)
    rooms = unique(rooms)

    # If explicit JSON location fields exist, use them as a fallback.
    explicit_location = ""
    for key in ("location", "room", "rooms"):
        value = clean_html(event.get(key, ""))
        if value:
            explicit_location = value
            break
    if explicit_location and not rooms and not places:
        place, room = split_place_room(explicit_location)
        places = unique([place]) if place else []
        rooms = unique([room]) if room else []

    # Derive course title if CELCAT omitted "module".
    title = module
    if not title:
        useful = []
        for line in after_group:
            if line.casefold() == category.casefold():
                continue
            if line.casefold() == cfg["group"].casefold():
                continue
            useful.append(line)
        if useful:
            title = useful[0]

    if not title:
        title = "Cours UVSQ"

    # Preserve remaining CELCAT information without duplicating structured fields.
    notes = []
    for line in after_group:
        if line.casefold() in {
            category.casefold(),
            cfg["group"].casefold(),
            title.casefold(),
            module.casefold() if module else "",
        }:
            continue
        notes.append(line)
    notes = unique(notes)

    place_text = " / ".join(places)
    room_text = " / ".join(rooms)
    if place_text and room_text:
        location = f"{place_text} — {room_text}"
    else:
        location = place_text or room_text

    return {
        "category": category,
        "type_label": category_label(category),
        "feed_key": feed_key_for_category(category),
        "title": title,
        "place": place_text,
        "room": room_text,
        "location": location,
        "notes": notes,
    }


def parse_celcat_datetime(value: object) -> dt.datetime:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("missing event date")
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


def make_uid(event: dict, start: dt.datetime, title: str) -> str:
    raw_id = clean_html(event.get("id", ""))
    if raw_id:
        return f"{raw_id}@edt.uvsq.fr"
    safe = re.sub(r"[^A-Za-z0-9]+", "-", title).strip("-")[:40]
    return f"{start.strftime('%Y%m%dT%H%M%S')}-{safe}@edt.uvsq.fr"


def event_to_ics_lines(event: dict, cfg: dict, now_utc: dt.datetime) -> tuple[list[str], str]:
    start = parse_celcat_datetime(event.get("start"))
    end = parse_celcat_datetime(event.get("end"))
    fields = parse_event_fields(event, cfg)

    summary = f"{fields['type_label']} · {fields['title']}"
    uid = make_uid(event, start, fields["title"])

    description_lines = [
        f"Type : {fields['type_label']}",
        f"Cours : {fields['title']}",
        f"Groupe : {cfg['group']}",
    ]
    if fields["place"]:
        description_lines.append(f"Site : {fields['place']}")
    if fields["room"]:
        description_lines.append(f"Salle : {fields['room']}")
    if fields["notes"]:
        description_lines.append("")
        description_lines.append("Détails CELCAT :")
        description_lines.extend(fields["notes"])

    lines = [
        "BEGIN:VEVENT",
        f"UID:{ics_escape(uid)}",
        f"DTSTAMP:{now_utc.strftime('%Y%m%dT%H%M%SZ')}",
        dt_local_line("DTSTART", start),
        dt_local_line("DTEND", end),
        f"SUMMARY:{ics_escape(summary)}",
    ]
    if fields["location"]:
        lines.append(f"LOCATION:{ics_escape(fields['location'])}")
    lines.append(f"DESCRIPTION:{ics_escape(chr(10).join(description_lines))}")
    if fields["category"]:
        lines.append(f"CATEGORIES:{ics_escape(fields['category'])}")
    lines.append("END:VEVENT")
    return lines, fields["feed_key"]


def calendar_header(name: str, color: str) -> list[str]:
    return [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "PRODID:-//UVSQ CELCAT Calendar Bridge//EN",
        f"X-WR-CALNAME:{ics_escape(name)}",
        "X-WR-TIMEZONE:Europe/Paris",
        f"COLOR:{color}",
        f"X-APPLE-CALENDAR-COLOR:{color}",
        "REFRESH-INTERVAL;VALUE=DURATION:PT1H",
        "X-PUBLISHED-TTL:PT1H",
    ]


def build_calendars(events: list[dict], cfg: dict) -> tuple[dict[str, str], dict[str, int]]:
    now_utc = dt.datetime.now(UTC)
    buckets: dict[str, list[list[str]]] = {k: [] for k in FEEDS if k != "all"}
    all_events: list[list[str]] = []
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
        if ascii_upper(category) in SKIP_CATEGORIES:
            continue

        event_lines, feed_key = event_to_ics_lines(event, cfg, now_utc)
        uid_line = next((x for x in event_lines if x.startswith("UID:")), "")
        dedupe = (uid_line, start, end)
        if dedupe in seen:
            continue
        seen.add(dedupe)

        all_events.append(event_lines)
        buckets[feed_key].append(event_lines)

    outputs = {}
    counts = {"all": len(all_events)}
    for key, meta in FEEDS.items():
        selected = all_events if key == "all" else buckets[key]
        counts[key] = len(selected)

        name = cfg["calendar_name"] if key == "all" else meta["name"]
        lines = calendar_header(name, meta["color"])
        for ev_lines in selected:
            lines.extend(ev_lines)
        lines.append("END:VCALENDAR")
        outputs[key] = "\r\n".join(fold_ics(line) for line in lines) + "\r\n"

    return outputs, counts


def write_status(*, ok: bool, message: str, endpoint: str = "", counts: dict | None = None):
    DOCS.mkdir(parents=True, exist_ok=True)
    payload = {
        "ok": ok,
        "updated_at": dt.datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "event_count": (counts or {}).get("all", 0),
        "category_counts": counts or {},
        "endpoint": endpoint,
        "message": message,
    }
    STATUS_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def main() -> int:
    cfg = load_config()
    DOCS.mkdir(parents=True, exist_ok=True)

    try:
        events, endpoint = fetch_events(cfg)
        calendars, counts = build_calendars(events, cfg)

        # Protect an existing healthy all-events feed against an obviously
        # temporary empty response from CELCAT.
        main_path = DOCS / FEEDS["all"]["filename"]
        if counts["all"] == 0 and main_path.exists() and "BEGIN:VEVENT" in main_path.read_text(
            encoding="utf-8", errors="ignore"
        ):
            raise RuntimeError(
                "CELCAT returned zero usable events; keeping the previous calendars."
            )

        for key, content in calendars.items():
            (DOCS / FEEDS[key]["filename"]).write_text(content, encoding="utf-8", newline="")

        write_status(
            ok=True,
            message=f"Organized calendars generated successfully for {cfg['group']}.",
            endpoint=endpoint,
            counts=counts,
        )

        print("Generated calendars:")
        for key, meta in FEEDS.items():
            print(f"  {meta['filename']}: {counts[key]} events")
        return 0

    except Exception as exc:
        write_status(ok=False, message=str(exc))
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
