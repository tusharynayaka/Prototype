"""
signals.py — Real-world demand signal aggregation layer.
SIH 2026 | Team 501BH
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
# Config
# ---------------------------------------------------------------------------

# Add this to signals.py after ROUTE_LOCATIONS (around line 38)

ROUTE_LOCATIONS: Dict[str, tuple] = {
    "501BH": (12.9716, 77.5946),
    "335-E": (12.9767, 77.5713),
    "500-C": (12.9250, 77.6228),
    "500-D": (12.9081, 77.6476),
}

# ADD THIS:
ROUTE_NAMES: Dict[str, str] = {
    "501BH": "Hebbal-BTM Layout",
    "335-E": "KBS-Electronic City",
    "500-C": "Majestic-Bannerghatta",
    "500-D": "Shivajinagar-Bidadi",
}

COLLEGE_ROUTES = {
    "PES University": ["501BH", "335-E"],
}

BANGALORE_CENTER = (12.9716, 77.5946)
CITY_FETCH_RADIUS_KM = 15.0
SIGNAL_MATCH_RADIUS_KM = 3.0
LOOKAHEAD_HOURS = 6

PREDICTHQ_API_KEY = os.environ.get("PREDICTHQ_API_KEY", "")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
EXTRACTION_MODEL = "openai/gpt-oss-120b"

COLLEGE_CALENDAR_SOURCES: List[dict] = [
    {"name": "PES University", "url": "https://drive.google.com/uc?export=download&id=1iuPmdXAbK5bsLrh5LGSkVxaPXCHW57iT", "lat": 12.9351, "lon": 77.5350},
]


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------
class Signal(BaseModel):
    signal_id: str
    source: Literal["predicthq", "exam_calendar", "manual", "free_api"]
    category: str
    name: str
    lat: float
    lon: float
    start_time: datetime
    end_time: datetime
    expected_scale: Literal["low", "medium", "high"] = "medium"
    confidence: float = 0.7
    raw_text: Optional[str] = None
    affected_routes: List[str] = Field(default_factory=list)

    # Update the is_active_or_upcoming method in the Signal class (around line 72)

    def is_active_or_upcoming(self, at: datetime) -> bool:
        window_start = self.start_time - timedelta(hours=LOOKAHEAD_HOURS)
        # Ensure both datetimes are timezone-aware for comparison
        if window_start.tzinfo is None:
            window_start = window_start.replace(tzinfo=timezone.utc)
        if at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
        if self.end_time.tzinfo is None:
            end_time = self.end_time.replace(tzinfo=timezone.utc)
        else:
            end_time = self.end_time
        return window_start <= at <= end_time


def _parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def match_routes(lat: float, lon: float, radius_km: float = SIGNAL_MATCH_RADIUS_KM) -> List[str]:
    return [
        route_id
        for route_id, (r_lat, r_lon) in ROUTE_LOCATIONS.items()
        if haversine_km(lat, lon, r_lat, r_lon) <= radius_km
    ]


# ---------------------------------------------------------------------------
# Signal Store
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
# Free API Adapter (No API Keys Required)
# ---------------------------------------------------------------------------

class FreeEventFetcher:
    """Fetches events from free APIs - falls back when PredictHQ is unavailable"""
    
    def __init__(self):
        self._last_fetch = None
    
    def fetch_from_neighborhood_commons(self, lat: float, lon: float, radius_km: float = 15.0, days_ahead: int = 7) -> List[Signal]:
        """Fetch from Neighborhood Commons or generate mock data"""
        logger.info("Fetching from free API sources...")
        
        # Try the actual Neighborhood Commons API first
        try:
            params = {
                "lat": lat,
                "lon": lon,
                "radius": radius_km,
                "days_ahead": days_ahead,
                "limit": 50
            }
            response = requests.get(
                "https://api.neighborhood-commons.org/v1/events",
                params=params,
                timeout=5
            )
            if response.status_code == 200:
                data = response.json()
                events = data.get("events", [])
                if events:
                    logger.info(f"Free API: Found {len(events)} real events")
                    return self._convert_neighborhood_events(events)
        except Exception as e:
            logger.warning(f"Free API failed: {e}, using mock data")
        
        # Fallback to mock data
        return self._generate_mock_events(lat, lon, radius_km, days_ahead)
    
    def _convert_neighborhood_events(self, events: List[Dict]) -> List[Signal]:
        """Convert Neighborhood Commons events to Signals"""
        signals = []
        for event in events:
            try:
                lat = event.get("lat") or event.get("latitude")
                lon = event.get("lon") or event.get("longitude")
                if not lat or not lon:
                    continue
                
                start = event.get("start_time") or event.get("start_date")
                if not start:
                    continue
                
                if isinstance(start, str):
                    start = _parse_dt(start)
                end = event.get("end_time") or event.get("end_date") or start.isoformat()
                if isinstance(end, str):
                    end = _parse_dt(end)
                
                category = event.get("category", "community")
                scale = "high" if event.get("size") == "large" else "medium"
                
                signal = Signal(
                    signal_id=f"free_{event.get('id', hash(event.get('name', '')))}",
                    source="free_api",
                    category=category,
                    name=event.get("name", "Untitled event"),
                    lat=float(lat),
                    lon=float(lon),
                    start_time=start,
                    end_time=end,
                    expected_scale=scale,
                    confidence=0.7,
                    raw_text=event.get("description", ""),
                )
                signal.affected_routes = match_routes(signal.lat, signal.lon)
                signals.append(signal)
            except Exception as e:
                logger.warning(f"Failed to convert event: {e}")
                continue
        return signals
    
    def _generate_mock_events(self, lat: float, lon: float, radius_km: float, days_ahead: int) -> List[Signal]:
        """Generate mock events for demo purposes"""
        logger.info("Generating mock events for demonstration...")
        
        now = datetime.now(timezone.utc)
        
        mock_events = [
            {"name": "Cricket Match - Chinnaswamy Stadium", "category": "sports", "lat": 12.9784, "lon": 77.5908, "scale": "high"},
            {"name": "Tech Summit - BIEC", "category": "conferences", "lat": 12.9351, "lon": 77.5350, "scale": "medium"},
            {"name": "Music Festival - Palace Grounds", "category": "festivals", "lat": 13.0025, "lon": 77.5946, "scale": "high"},
            {"name": "Food Festival - Church Street", "category": "community", "lat": 12.9762, "lon": 77.6033, "scale": "low"},
        ]
        
        signals = []
        for event in mock_events:
            if haversine_km(lat, lon, event["lat"], event["lon"]) > radius_km:
                continue
            
            signal = Signal(
                signal_id=f"mock_{event['name'].replace(' ', '_')}",
                source="free_api",
                category=event["category"],
                name=event["name"],
                lat=event["lat"],
                lon=event["lon"],
                start_time=now + timedelta(hours=1),
                end_time=now + timedelta(hours=6),
                expected_scale=event["scale"],
                confidence=0.8,
                raw_text=f"Mock event: {event['name']}",
            )
            signal.affected_routes = match_routes(signal.lat, signal.lon)
            signals.append(signal)
        
        logger.info(f"Generated {len(signals)} mock events")
        return signals


def refresh_free_api_signals() -> int:
    """Refresh signals from free APIs"""
    fetcher = FreeEventFetcher()
    signals = fetcher.fetch_from_neighborhood_commons(*BANGALORE_CENTER)
    
    for sig in signals:
        signal_store.add(sig)
    
    signal_store.purge_expired()
    logger.info(f"Free API refresh: {len(signals)} signal(s) ingested.")
    return len(signals)


# ---------------------------------------------------------------------------
# PredictHQ Adapter
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
    if not PREDICTHQ_API_KEY:
        logger.warning("PREDICTHQ_API_KEY not set")
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
            ev_lon, ev_lat = ev["location"][0], ev["location"][1]
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
    """Fetch from PredictHQ, fallback to free APIs if unavailable"""
    if not PREDICTHQ_API_KEY:
        logger.warning("PREDICTHQ_API_KEY not set — using free APIs.")
        return refresh_free_api_signals()
    
    try:
        events = fetch_predicthq_signals(*BANGALORE_CENTER)
        if events:
            for sig in events:
                signal_store.add(sig)
            signal_store.purge_expired()
            logger.info(f"PredictHQ refresh: {len(events)} signal(s) ingested.")
            return len(events)
        else:
            logger.warning("PredictHQ returned no events — using free APIs.")
            return refresh_free_api_signals()
    except Exception as e:
        logger.error(f"PredictHQ failed: {e} — using free APIs.")
        return refresh_free_api_signals()


# ---------------------------------------------------------------------------
# Exam Calendar Adapter
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
   start_date = May 04, end_date = May 30 using the year from the academic session.
6. Extract examination/assessment periods even if they appear in a table, calendar cell, abbreviation, or summary line.
7. Do not require the word "exam" to appear explicitly.
8. Ignore holidays, faculty meetings, class committee meetings, PTMs, and other non-examination activities.
9. If examination or assessment dates are present, return them.
10. If genuinely no examination dates exist, return [].

Return ONLY valid JSON.
"""

def fetch_text_from_source(url: str) -> str:
    import io
    from urllib.parse import urlparse, parse_qs
    from pypdf import PdfReader
    from bs4 import BeautifulSoup

    if "drive.google.com" in url:
        parsed = urlparse(url)
        params = parse_qs(parsed.query)
        file_id = params.get("id", [None])[0]
        if not file_id:
            parts = parsed.path.split("/")
            if "d" in parts:
                index = parts.index("d")
                if index + 1 < len(parts):
                    file_id = parts[index + 1]
        if not file_id:
            raise ValueError("Could not find Google Drive file ID")
        url = f"https://drive.google.com/uc?export=download&id={file_id}"

    resp = requests.get(url, timeout=15)
    resp.raise_for_status()
    content_type = resp.headers.get("Content-Type", "").lower()

    if "pdf" in content_type or resp.content.startswith(b"%PDF"):
        reader = PdfReader(io.BytesIO(resp.content))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
        return text

    return BeautifulSoup(resp.text, "html.parser").get_text(separator="\n")


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
            {"role": "user", "content": raw_text[:5000]},
        ],
    )

    try:
        content = completion.choices[0].message.content or ""
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
# Manual Entry
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
# Scheduler
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