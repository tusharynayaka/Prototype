"""
signals.py — Real-world demand signal aggregation layer.
SIH 2026 | Team 501BH

This is the actual selling point of the project: route optimization has
historically been a manual, once-a-week process, done by people who don't
have visibility into every college's exam calendar or every concert booked
across the city. This module is the "connection" that replaces that —
it continuously pulls from real sources, normalizes everything into one
Signal shape, geofences each signal to the route(s) it actually affects,
and hands optimize_frequency() something a manual weekly process never had:
a live, structured answer to "why is demand on this corridor about to change?"

Three ways signals get in:
  1. PredictHQ Events API  -> real concerts/festivals/sports/etc, polled on
     a schedule (this is the piece that used to require someone to check
     event listings by hand).
  2. Exam calendars        -> no public API exists for "college X has exams
     this week", so this fetches configured college calendar pages/PDFs and
     uses an LLM to pull structured exam date ranges out of the unstructured
     text. This is where an LLM actually earns its place in this project —
     turning messy real-world text into a structured signal, not narrating
     numbers you already have.
  3. Manual entry          -> the human-in-the-loop escape hatch. If
     automation misses something (a one-off local event), a planner adds
     it once via /api/signals and it stays live — no weekly spreadsheet.

Set PREDICTHQ_API_KEY and GROQ_API_KEY as environment variables (see
.env.example). Either adapter silently no-ops (with a log warning) if its
key isn't set, so the rest of the backend still runs without them.
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Literal, Optional

import requests
from pydantic import BaseModel, Field

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("bmtc_backend.signals")

# ---------------------------------------------------------------------------
# Config — replace with real values before this leaves demo/hackathon stage
# ---------------------------------------------------------------------------

# Approx lat/lon per route corridor. Used to geofence incoming signals
# ("this concert is near which routes?"). Swap for real GTFS stop coordinates.
ROUTE_LOCATIONS: Dict[str, tuple] = {
    "501BH": (12.9716, 77.5946),
    "335-E": (12.9767, 77.5713),
    "500-C": (12.9250, 77.6228),
    "500-D": (12.9081, 77.6476),
}
COLLEGE_ROUTES = {
    "PES University": ["501BH", "335-E"],
}

# Center point + radius used for the single city-wide PredictHQ pull.
BANGALORE_CENTER = (12.9716, 77.5946)
CITY_FETCH_RADIUS_KM = 15.0

SIGNAL_MATCH_RADIUS_KM = 3.0   # how far a signal reaches from its own lat/lon
LOOKAHEAD_HOURS = 6            # surface a signal this many hours before it starts

PREDICTHQ_API_KEY = os.environ.get("PREDICTHQ_API_KEY", "")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
EXTRACTION_MODEL = "openai/gpt-oss-120b"  # fast/cheap — this is structured extraction, not reasoning

# Fill in real colleges near your test routes. url can be a calendar page or
# a direct PDF link — fetch_text_from_source() handles both.
COLLEGE_CALENDAR_SOURCES: List[dict] = [
     {"name": "PES University", "url": "https://drive.google.com/uc?export=download&id=1iuPmdXAbK5bsLrh5LGSkVxaPXCHW57iT", "lat": 12.9351, "lon": 77.5350},
     
]


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------
class Signal(BaseModel):
    signal_id: str
    source: Literal["predicthq", "exam_calendar", "manual"]
    category: str  # "concerts" / "festivals" / "sports" / "exam" / etc.
    name: str
    lat: float
    lon: float
    start_time: datetime
    end_time: datetime
    expected_scale: Literal["low", "medium", "high"] = "medium"
    confidence: float = 0.7
    raw_text: Optional[str] = None
    affected_routes: List[str] = Field(default_factory=list)

    def is_active_or_upcoming(self, at: datetime) -> bool:
        window_start = self.start_time - timedelta(hours=LOOKAHEAD_HOURS)
        return window_start <= at <= self.end_time


def _parse_dt(value: str) -> datetime:
    """PredictHQ / scraped sources sometimes emit a trailing 'Z' — Python's
    fromisoformat only accepts that from 3.11 onward, so normalize it."""
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def match_routes(lat: float, lon: float, radius_km: float = SIGNAL_MATCH_RADIUS_KM) -> List[str]:
    """Which known route corridors fall within radius_km of this signal?
    This is the geofencing step — it's what lets a raw lat/lon from a news
    story or an API response automatically know which bus routes care."""
    return [
        route_id
        for route_id, (r_lat, r_lon) in ROUTE_LOCATIONS.items()
        if haversine_km(lat, lon, r_lat, r_lon) <= radius_km
    ]


# ---------------------------------------------------------------------------
# In-memory signal store — swap for a real DB/Redis once this leaves demo
# ---------------------------------------------------------------------------
class SignalStore:
    def __init__(self):
        self._signals: Dict[str, Signal] = {}

    def add(self, signal: Signal) -> Signal:
        if not signal.affected_routes:
            signal.affected_routes = match_routes(signal.lat, signal.lon)
        self._signals[signal.signal_id] = signal
        logger.info(
            "Signal added: %s (%s) -> routes %s",
            signal.name, signal.category, signal.affected_routes or "none matched",
        )
        return signal

    def purge_expired(self, now: Optional[datetime] = None) -> None:
        now = now or datetime.now(timezone.utc)
        expired = [sid for sid, s in self._signals.items() if s.end_time < now]
        for sid in expired:
            del self._signals[sid]
        if expired:
            logger.info("Purged %d expired signal(s)", len(expired))

    def active_for_route(self, route_id: str, at: Optional[datetime] = None) -> List[Signal]:
        at = at or datetime.now(timezone.utc)
        return [
            s for s in self._signals.values()
            if route_id in s.affected_routes and s.is_active_or_upcoming(at)
        ]

    def all_upcoming(self, at: Optional[datetime] = None) -> List[Signal]:
        at = at or datetime.now(timezone.utc)
        return [s for s in self._signals.values() if s.is_active_or_upcoming(at)]


signal_store = SignalStore()


# ---------------------------------------------------------------------------
# Adapter 1 — PredictHQ (real event data)
# ---------------------------------------------------------------------------
_PHQ_CATEGORY_SCALE = {
    "concerts": "high",
    "festivals": "high",
    "sports": "high",
    "conferences": "medium",
    "expos": "medium",
    "performing-arts": "medium",
    "community": "low",
}


def fetch_predicthq_signals(
    lat: float,
    lon: float,
    radius_km: float = CITY_FETCH_RADIUS_KM,
    days_ahead: int = 7,
    categories: str = "concerts,festivals,sports,conferences,expos,performing-arts,community",
) -> List[Signal]:
    """Pull real upcoming events near (lat, lon) from PredictHQ's Events API.
    Needs PREDICTHQ_API_KEY — sign up for the free trial at predicthq.com.
    NOTE: verify exact response field names against your own API Explorer
    output before relying on this for a live demo; malformed entries are
    skipped rather than crashing the whole refresh."""
    if not PREDICTHQ_API_KEY:
        logger.warning("PREDICTHQ_API_KEY not set — skipping real event pull.")
        return []

    now = datetime.now(timezone.utc)
    params = {
        "within": f"{radius_km:g}km@{lat},{lon}",
        "category": categories,
        "active.gte": now.strftime("%Y-%m-%d"),
        "active.lte": (now + timedelta(days=days_ahead)).strftime("%Y-%m-%d"),
        "limit": 50,
    }
    try:
        resp = requests.get(
            "https://api.predicthq.com/v1/events/",
            headers={"Authorization": f"Bearer {PREDICTHQ_API_KEY}", "Accept": "application/json"},
            params=params,
            timeout=10,
        )
        resp.raise_for_status()
    except requests.RequestException:
        logger.exception("PredictHQ fetch failed")
        return []

    signals: List[Signal] = []
    for ev in resp.json().get("results", []):
        try:
            ev_lon, ev_lat = ev["location"][0], ev["location"][1]  # PredictHQ = [lon, lat]
            category = ev.get("category", "community")
            signals.append(Signal(
                signal_id=f"phq_{ev['id']}",
                source="predicthq",
                category=category,
                name=ev.get("title", "Untitled event"),
                lat=ev_lat, lon=ev_lon,
                start_time=_parse_dt(ev["start"]),
                end_time=_parse_dt(ev.get("end") or ev["start"]),
                expected_scale=_PHQ_CATEGORY_SCALE.get(category, "medium"),
                confidence=min(1.0, ev.get("rank", 50) / 100),
                raw_text=ev.get("title"),
            ))
        except (KeyError, ValueError, TypeError, IndexError):
            continue
    return signals


def refresh_predicthq_signals() -> int:
    """One city-wide fetch, geofenced locally — cheaper than one API call
    per route and gives the same result since add() runs match_routes()."""
    events = fetch_predicthq_signals(*BANGALORE_CENTER)
    for sig in events:
        signal_store.add(sig)
    signal_store.purge_expired()
    logger.info("PredictHQ refresh: %d signal(s) ingested.", len(events))
    return len(events)


# ---------------------------------------------------------------------------
# Adapter 2 — College exams (no API exists: fetch text, LLM extracts)
# ---------------------------------------------------------------------------
_EXTRACTION_SYSTEM_PROMPT = """
You extract examination dates from a college academic calendar.

Return ONLY a JSON array. No prose and no markdown.

Each item MUST have exactly:
{
  "name": "string",
  "start_date": "YYYY-MM-DD",
  "end_date": "YYYY-MM-DD"
}

Important instructions:

1. Identify the academic session/year from the calendar.
2. Look for examination-related activities, including abbreviations.
3. "ESA" means "End Semester Assessment" and MUST be treated as an examination.
4. "ISA" means "In Semester Assessment" and MUST be treated as an examination/assessment.
5. If the calendar gives a date range such as "ESA: May 04 -30", interpret it as:
   start_date = May 04
   end_date = May 30
   using the year from the academic session.
6. Extract examination/assessment periods even if they appear in a table,
   calendar cell, abbreviation, or summary line.
7. Do not require the word "exam" to appear explicitly.
8. Ignore holidays, faculty meetings, class committee meetings, PTMs,
   and other non-examination activities.
9. If examination or assessment dates are present, return them.
10. If genuinely no examination dates exist, return [].

Return ONLY valid JSON.
"""


def fetch_text_from_source(url: str) -> str:
    import io
    import requests
    from urllib.parse import urlparse, parse_qs
    from pypdf import PdfReader
    from bs4 import BeautifulSoup

    # -----------------------------
    # Handle Google Drive URL
    # -----------------------------
    if "drive.google.com" in url:
        parsed = urlparse(url)
        params = parse_qs(parsed.query)

        file_id = params.get("id", [None])[0]

        # Also support:
        # /file/d/FILE_ID/view
        if not file_id:
            parts = parsed.path.split("/")

            if "d" in parts:
                index = parts.index("d")
                if index + 1 < len(parts):
                    file_id = parts[index + 1]

        if not file_id:
            raise ValueError("Could not find Google Drive file ID")

        url = f"https://drive.google.com/uc?export=download&id={file_id}"

    # -----------------------------
    # Download
    # -----------------------------
    resp = requests.get(url, timeout=15)
    resp.raise_for_status()

    content_type = resp.headers.get("Content-Type", "").lower()


    # -----------------------------
    # PDF
    # -----------------------------
    if "pdf" in content_type or resp.content.startswith(b"%PDF"):
        reader = PdfReader(io.BytesIO(resp.content))

        text = "\n".join(
            page.extract_text() or ""
            for page in reader.pages
        )

        return text

    # -----------------------------
    # HTML webpage
    # -----------------------------
    return BeautifulSoup(
        resp.text,
        "html.parser"
    ).get_text(separator="\n")


def extract_exam_signals_via_llm(college_name: str, raw_text: str, lat: float, lon: float) -> List[Signal]:
    if not GROQ_API_KEY:
        logger.warning("GROQ_API_KEY not set — skipping exam extraction for %s.", college_name)
        return []

    from groq import Groq

    client = Groq(api_key=GROQ_API_KEY)

    completion = client.chat.completions.create(
        model=EXTRACTION_MODEL,
        temperature=0,
        max_tokens=2500,
        messages=[
            {"role": "system", "content": _EXTRACTION_SYSTEM_PROMPT},
            {"role": "user", "content": raw_text[:5000]},  # keep prompts small/cheap
        ],
    )

    try:
        content = completion.choices[0].message.content or ""

        # Be tolerant if the model still wraps the JSON in markdown fences.
        content = content.strip()
        if content.startswith("```"):
            content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content, flags=re.IGNORECASE)
        items = json.loads(content)
    except (json.JSONDecodeError, IndexError, AttributeError, TypeError):
        logger.warning("Exam extraction returned non-JSON for %s", college_name)
        return []

    signals: List[Signal] = []
    for item in items:
        try:
            start = _parse_dt(item["start_date"])
            end = _parse_dt(item["end_date"]).replace(hour=23, minute=59)
            signals.append(Signal(
                    signal_id=f"exam_{college_name}_{item['start_date']}".replace(" ", "_"),
                    source="exam_calendar",
                    category="exam",
                    name=f"{college_name}: {item.get('name', 'Exams')}",
                    lat=lat,
                    lon=lon,
                    start_time=start,
                    end_time=end,
                    expected_scale="medium",
                    confidence=0.65,
                    raw_text=item.get("name"),
                    affected_routes=COLLEGE_ROUTES.get(college_name, []),
                
            ))
        except (KeyError, ValueError):
            continue
    return signals


def refresh_exam_signals() -> int:
    count = 0
    for college in COLLEGE_CALENDAR_SOURCES:
        try:
            text = fetch_text_from_source(college["url"])
            new_signals = extract_exam_signals_via_llm(college["name"], text, college["lat"], college["lon"])
            for sig in new_signals:
                signal_store.add(sig)
            count += len(new_signals)
        except Exception:
            logger.exception("Exam calendar fetch failed for %s", college.get("name"))
    logger.info("Exam refresh: %d signal(s) ingested.", count)
    return count


# ---------------------------------------------------------------------------
# Manual entry — the human-in-the-loop escape hatch
# ---------------------------------------------------------------------------
class ManualSignalRequest(BaseModel):
    name: str = Field(..., example="Local college fest")
    category: str = Field("event", example="festival")
    lat: float = Field(..., example=12.9351)
    lon: float = Field(..., example=77.5350)
    start_time: datetime
    end_time: datetime
    expected_scale: Literal["low", "medium", "high"] = "medium"


def add_manual_signal(payload: ManualSignalRequest) -> Signal:
    sid = f"manual_{payload.name}_{int(time.time())}".replace(" ", "_")
    signal = Signal(
        signal_id=sid,
        source="manual",
        category=payload.category,
        name=payload.name,
        lat=payload.lat, lon=payload.lon,
        start_time=payload.start_time, end_time=payload.end_time,
        expected_scale=payload.expected_scale,
        confidence=1.0,
    )
    return signal_store.add(signal)


# ---------------------------------------------------------------------------
# Scheduler — this is what replaces "someone checks this once a week"
# ---------------------------------------------------------------------------
def start_scheduler():
    from apscheduler.schedulers.background import BackgroundScheduler

    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(
        refresh_predicthq_signals, "interval", minutes=30,
        id="predicthq_refresh", next_run_time=datetime.now(timezone.utc),
    )
    scheduler.add_job(
        refresh_exam_signals, "interval", hours=6,
        id="exam_refresh", next_run_time=datetime.now(timezone.utc),
    )
    scheduler.start()
    logger.info("Signal scheduler started: PredictHQ every 30min, exams every 6h.")
    return scheduler