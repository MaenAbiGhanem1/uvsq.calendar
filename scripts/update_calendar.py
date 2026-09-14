#!/usr/bin/env python3
"""
UVSQ CELCAT -> organized iCalendar feeds.

Calendar display goal:
  TITLE    = course name only
  LOCATION = building/site + room
  COLOR    = class type, by subscribing to separate COURS / TD / TP / EXAM feeds

Outputs:
  docs/schedule.ics  all classes
  docs/cours.ics     lectures
  docs/td.ics        tutorials
  docs/tp.ics        practicals
  docs/examens.ics   exams/tests
  docs/autres.ics    everything else
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
    "all":      {"filename": "schedule.ics", "name": "UVSQ — S5 PHYSIQUE PSC", "color": "#5F6368"},
    "cours":    {"filename": "cours.ics", "name": "UVSQ — COURS", "color": "#3F51B5"},
    "td":       {"filename": "td.ics", "name": "UVSQ — TD", "color": "#039BE5"},
    "tp":       {"filename": "tp.ics", "name": "UVSQ — TP", "color": "#0B8043"},
    "examens":  {"filename": "examens.ics", "name": "UVSQ — EXAMENS", "color": "#D50000"},
    "autres":   {"filename": "autres.ics", "name": "UVSQ — AUTRES", "color": "#616161"},
}

_BR_RE = re.compile(r"<br\s*/?>", re.I)
_TAG_RE = re.compile(r"<[^>]+>")

# Examples actually seen in UVSQ CELCAT:
#   G209 - GERMAIN [Salle de TD]
#   E303 - Bât Joliot-Curie
#   AMPHI J - FERMAT (176 / 92) [Amphithéâtre]
#   G101 - GERMAIN [CARTABLE NUMERIQUE ]
ROOM_PREFIX_RE = re.compile(
    r"^(?:AMPHI(?:TH[EÉ][AÂ]TRE)?\s+[A-Z0-9]+|"
    r"SALLE\s+[A-Z0-9._-]+|"
    r"[A-Z]{1,2}\s*[-_.]?\s*\d{1,4}[A-Z]?)$",
    re.I,
)
ROOM_TAG_RE = re.compile(
    r"\[(?:[^\]]*(?:SALLE|AMPHI|CARTABLE|LABO|TP|TD)[^\]]*)\]",
    re.I,
)
CAPACITY_RE = re.compile(r"\s*\(\s*\d+\s*/\s*\d+\s*\)\s*")
BUILDING_WORD_RE = re.compile(
    r"\b(B[ÂA]T(?:IMENT)?|GERMAIN|FERMAT|DESCARTES|JOLIOT[-\s]?CURIE|"
    r"D'ALEMBERT|D’ALEMBERT|VAUBAN|BUFFON|CAMPUS|UVSQ|VERSAILLES|"
    r"GUYANCOURT|V[EÉ]LIZY)\b",
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
            "User-Agent": "Mozilla/5.0 (compatible; UVSQCalendarBridge/3.0)",
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
    if re.search(r"COURS|\bCM\b", c):
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


def strip_room_decoration(line: str) -> str:
    line = ROOM_TAG_RE.sub("", line)
    line = CAPACITY_RE.sub(" ", line)
    return re.sub(r"\s+", " ", line).strip(" -")


def parse_uvsq_location(line: str) -> tuple[str, str] | None:
    """
    Return (place, room) if a CELCAT line looks like a UVSQ location.

    Example:
      "AMPHI J - FERMAT (176 / 92) [Amphithéâtre]"
        -> ("FERMAT", "AMPHI J")
    """
    original = line.strip()
    cleaned = strip_room_decoration(original)
    if not cleaned:
        return None

    # Strong signal: CELCAT's [Salle ...] / [Amphithéâtre] resource tags.
    tagged = bool(ROOM_TAG_RE.search(original))

    if " - " in cleaned:
        left, right = cleaned.split(" - ", 1)
        left, right = left.strip(), right.strip()

        left_is_room = bool(ROOM_PREFIX_RE.fullmatch(left))
        right_is_place = bool(BUILDING_WORD_RE.search(right))

        if tagged or left_is_room or right_is_place:
            return right, left

    # A standalone room such as "G209" or "AMPHI J".
    if ROOM_PREFIX_RE.fullmatch(cleaned):
        return "", cleaned

    # A standalone building/site.
    if BUILDING_WORD_RE.search(cleaned) and len(cleaned.split()) <= 6:
        return cleaned, ""

    return None


def looks_like_category(line: str) -> bool:
    u = ascii_upper(line.strip())
    return bool(re.fullmatch(
        r"(TD|TP|CM|COURS|EXAMEN|EXAM|PARTIEL|CONTROLE(?: CONTINU)?|CC)",
        u,
    ))


def strip_course_code(line: str) -> str:
    """
    Clean common UVSQ course-code decorations.

    Examples:
      "PHY301 - Mécanique quantique" -> "Mécanique quantique"
      "LSPH513N-LSPH513 - Optique Physique" -> "Optique Physique"
      "Mécanique quantique 1 [LSPH516]" -> "Mécanique quantique 1"
    """
    s = line.strip()

    # Remove trailing bracketed course codes such as [LSPH516].
    s = re.sub(
        r"\s*\[[A-Z]{2,}[A-Z0-9._-]*\d[A-Z0-9._-]*\]\s*$",
        "",
        s,
        flags=re.I,
    ).strip()

    # Remove leading UVSQ module codes, including compound codes.
    if " - " in s:
        left, right = s.split(" - ", 1)
        compact = re.sub(r"[\s._-]", "", left)

        if (
            2 <= len(compact) <= 30
            and re.fullmatch(r"[A-Za-z0-9]+", compact)
            and any(ch.isdigit() for ch in compact)
            and len(right.strip()) >= 3
        ):
            s = right.strip()

    return s


def looks_like_group_name(line: str, cfg: dict) -> bool:
    """
    Reject CELCAT group/resource labels that can otherwise be mistaken
    for the subject name.
    """
    s = clean_html(line)
    u = ascii_upper(s)
    group_u = ascii_upper(cfg["group"])

    if not s:
        return True
    if u == group_u:
        return True

    # Example actually seen:
    # "L3 Physique S5 ( S5 PHYSIQUE ) [S5 PHYSIQUE ]"
    if "S5 PHYSIQUE" in u and (
        "L3 PHYSIQUE" in u
        or "[" in s
        or "(" in s
        or "PSC" in u
    ):
        return True

    if re.search(r"\b(GROUPE|PROMO|PARCOURS|SEMESTRE)\b", u):
        return True

    return False

def candidate_score(line: str) -> int:
    """
    Score a non-location CELCAT string as a probable course name.
    """
    s = line.strip()
    if not s:
        return -999

    score = 0
    letters = sum(ch.isalpha() for ch in s)
    words = re.findall(r"[A-Za-zÀ-ÿ]+", s)

    if letters >= 6:
        score += 3
    if len(words) >= 2:
        score += 3
    if len(words) >= 3:
        score += 1

    # Typical academic vocabulary gives a mild boost, but is not required.
    if re.search(
        r"\b(m[eé]canique|quantique|physique|math|optique|thermo|"
        r"[eé]lectro|signal|ondes?|chimie|anglais|informatique|"
        r"programmation|relativit[eé]|statistique|analyse|alg[eè]bre)\b",
        s,
        re.I,
    ):
        score += 4

    # Things that are very unlikely to be the subject title.
    if re.search(r"\b(S5|PSC|GROUPE|PROMO|ENSEIGNANT|PROF|M\.)\b", s, re.I):
        score -= 3
    if re.fullmatch(r"[A-ZÀ-Ý '-]{2,30}", s) and len(words) <= 2:
        # Could be a person's surname/building; only a slight penalty.
        score -= 1

    return score


def choose_course_title(event: dict, cfg: dict, lines: list[str]) -> str:
    """
    Prefer structured CELCAT course fields, but only when they are not actually
    room resources. Then fall back to description lines after removing group,
    category and location data.
    """
    candidates: list[tuple[int, str]] = []

    # Different CELCAT deployments use different field names.
    structured_fields = (
        "subject",
        "subjectName",
        "course",
        "courseName",
        "activityName",
        "eventName",
        "moduleName",
        "module",
        "title",
        "name",
    )

    for idx, key in enumerate(structured_fields):
        value = clean_html(event.get(key, ""))
        if not value:
            continue
        if "\n" in value:
            values = value.splitlines()
        else:
            values = [value]

        for item in values:
            item = item.strip()
            if not item:
                continue
            if item.casefold() == cfg["group"].casefold():
                continue
            if looks_like_group_name(item, cfg):
                continue
            if looks_like_category(item):
                continue
            if parse_uvsq_location(item):
                continue
            cleaned = strip_course_code(item)
            if looks_like_group_name(cleaned, cfg):
                continue
            # Structured fields get a large preference.
            candidates.append((100 - idx + candidate_score(cleaned), cleaned))

    # Description fallback.
    for item in lines:
        item = item.strip()
        if not item:
            continue
        if item.casefold() == cfg["group"].casefold():
            continue
        if looks_like_group_name(item, cfg):
            continue
        if looks_like_category(item):
            continue
        if parse_uvsq_location(item):
            continue
        cleaned = strip_course_code(item)
        if looks_like_group_name(cleaned, cfg):
            continue
        candidates.append((candidate_score(cleaned), cleaned))

    if not candidates:
        return "Cours UVSQ"

    candidates.sort(key=lambda x: x[0], reverse=True)
    return strip_course_code(candidates[0][1])


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
    lines = clean_lines(event.get("description", ""))

    # Search ALL description lines for room/building data. UVSQ does not keep
    # the location in a consistent position relative to the group line.
    places, rooms = [], []
    location_source_lines = set()

    for line in lines:
        parsed = parse_uvsq_location(line)
        if parsed:
            place, room = parsed
            if place:
                places.append(place)
            if room:
                rooms.append(room)
            location_source_lines.add(line.casefold())

    # Also inspect explicit JSON location-ish fields.
    for key in ("location", "room", "rooms"):
        value = clean_html(event.get(key, ""))
        for line in value.splitlines():
            parsed = parse_uvsq_location(line)
            if parsed:
                place, room = parsed
                if place:
                    places.append(place)
                if room:
                    rooms.append(room)
            elif value and not rooms:
                # Keep an unknown explicit room field rather than discard it.
                rooms.append(line)

    places = unique(places)
    rooms = unique(rooms)

    title = choose_course_title(event, cfg, lines)

    # Keep extra CELCAT info only in the event details.
    notes = []
    for line in lines:
        if line.casefold() == cfg["group"].casefold():
            continue
        if looks_like_group_name(line, cfg):
            continue
        if line.casefold() in location_source_lines:
            continue
        if looks_like_category(line):
            continue
        if strip_course_code(line).casefold() == title.casefold():
            continue
        notes.append(line)
    notes = unique(notes)

    place_text = " / ".join(places)
    room_text = " / ".join(rooms)

    # User-facing order: place first, classroom second.
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

    # IMPORTANT: no TD / TP / COURS prefix. The calendar color carries that info.
    summary = fields["title"]
    uid = make_uid(event, start, fields["title"])

    description_lines = [
        f"Cours : {fields['title']}",
        f"Type : {fields['type_label']}",
    ]
    if fields["place"]:
        description_lines.append(f"Lieu : {fields['place']}")
    if fields["room"]:
        description_lines.append(f"Salle : {fields['room']}")
    description_lines.append(f"Groupe : {cfg['group']}")

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
