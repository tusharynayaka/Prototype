"""
BMTC Dynamic ML-Based Bus Frequency Optimization — Backend
SIH 2026 | Team 501BH

Design principles (per your last messages + Book1.xlsx reference sheet):
  - We NEVER cancel a trip that's already running. Buses on route stay on route.
  - "Frequency optimization" = deciding headway / how many buses run on the
    corridor over the next planning window, not diverting or short-turning.
  - Stop-level decisions (skip vs serve) are separate and evaluated per stop,
    per bus arrival — a bus can skip ONE stop with zero demand and keep going.
  - Real optimizer (OR-Tools CP-SAT) picks fleet size, with a deterministic
    greedy fallback if the solver times out or ML confidence is low.
  - Anti-oscillation: a route's target fleet size can't flip back and forth
    every request — mirrors "decision stability" note in your reference sheet.

Run:
    pip install fastapi uvicorn xgboost pandas numpy scikit-learn ortools
    uvicorn main:app --reload
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import List, Optional, Dict
import numpy as np
import pandas as pd
import xgboost as xgb
import logging
import time

from ortools.sat.python import cp_model

from signals import (
    signal_store,
    ManualSignalRequest,
    add_manual_signal,
    refresh_predicthq_signals,
    start_scheduler,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("bmtc_backend")

app = FastAPI(
    title="BMTC Dynamic Frequency Optimization Backend",
    version="2.0.0",
    description="SIH 2026 (501BH) - ML demand forecasting + OR-Tools frequency optimization",
)

# Local dev only — the control-room frontend (served from a file:// page or a
# different port) needs to call this API from the browser. Lock this down to
# your actual frontend origin before deploying anywhere real.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
BUS_CAPACITY = 45              # effective passengers per bus per trip
TURNAROUND_MINUTES = 90.0      # full round-trip cycle time for this corridor
MIN_FREQUENCY_MIN = 4          # never schedule tighter than this
MAX_FREQUENCY_MIN = 30         # never let headway drift looser than this
COST_PER_EXTRA_BUS = 1.0       # relative operating cost unit (fuel+driver+wear)
WAIT_PENALTY_WEIGHT = 1.2      # relative passenger-waiting cost unit
OSCILLATION_COOLDOWN_SEC = 15 * 60   # don't flip fleet size within this window
CONFIDENCE_FALLBACK_THRESHOLD = 0.50

# In-memory "last decision per route" store — swap for Redis in production
ROUTE_STATE: Dict[str, dict] = {}


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------
class RouteOptimizationRequest(BaseModel):
    route_id: str = Field(..., example="501BH")
    current_fleet_count: int = Field(..., example=10)
    average_speed_kmh: float = Field(..., example=14.5)
    weather_condition: int = Field(..., description="0 normal, 1 rain/heavy traffic, 2 event/festival", example=1)
    hour_of_day: int = Field(..., ge=0, le=23, example=18)
    day_of_week: int = Field(..., ge=0, le=6, example=2)
    is_event_nearby: int = Field(0, description="1 if a known event/festival is active near this corridor")


class OptimizationRecommendation(BaseModel):
    route_id: str
    predicted_passenger_demand: float
    confidence_score: float
    recommended_frequency_minutes: int
    recommended_fleet_allocation: int
    delta_buses: int                  # +N buses to add, -N to release back to pool
    action_type: str                  # DEPLOY_EXTRA_BUS / RELEASE_BUS / MAINTAIN_SCHEDULE
    solver_used: str                  # "or_tools" / "greedy_fallback" / "schedule_fallback"
    reasoning: str
    detected_signals: List[str] = Field(default_factory=list)  # real-world signals factored in


class StopOptimizationRequest(BaseModel):
    route_id: str = Field(..., example="501BH")
    stop_id: str = Field(..., example="silk_board")
    stop_name: str = Field(..., example="Silk Board Stop")
    current_waiting_passengers: int = Field(..., example=0)
    bus_capacity_available: int = Field(..., example=40)


class StopOptimizationResponse(BaseModel):
    route_id: str
    stop_id: str
    stop_name: str
    action_type: str   # SERVE_STOP / SKIP_STOP_DYNAMIC / ALERT_OVERCROWDING
    reasoning: str


# ---------------------------------------------------------------------------
# 1. ML Demand Forecasting
# ---------------------------------------------------------------------------
class DemandForecaster:
    """XGBoost regressor over hour/day/weather/speed/event features.
    Trained on synthetic data now — swap _train_dummy_model's X/y for your
    real ETM + GTFS history once you have it logged."""

    def __init__(self):
        self.model: Optional[xgb.XGBRegressor] = None
        self._train_dummy_model()

    @staticmethod
    def _peak_factor(hour: int) -> int:
        return 25 if (8 <= hour <= 11) or (17 <= hour <= 20) else 5

    def _train_dummy_model(self):
        np.random.seed(42)
        n = 2000

        hours = np.random.randint(0, 24, n)
        dows = np.random.randint(0, 7, n)
        weather = np.random.choice([0, 1, 2], n, p=[0.7, 0.2, 0.1])
        speeds = np.random.uniform(8, 35, n)
        events = np.random.choice([0, 1], n, p=[0.9, 0.1])

        X = pd.DataFrame({
            "hour": hours,
            "day_of_week": dows,
            "weather_condition": weather,
            "avg_speed": speeds,
            "is_event_nearby": events,
        })

        peak_boost = np.array([self._peak_factor(h) for h in hours])
        weekend_damp = np.where(np.isin(dows, [5, 6]), -8, 0)
        y = (
            50
            + peak_boost
            + weekend_damp
            + weather * 15
            + events * 30
            - speeds * 0.8
            + np.random.normal(0, 5, n)
        )
        y = np.maximum(y, 10)

        self.model = xgb.XGBRegressor(
            n_estimators=120, max_depth=4, learning_rate=0.08, random_state=42
        )
        self.model.fit(X, y)
        logger.info("DemandForecaster: XGBoost model trained.")

    def predict_demand(self, features: dict) -> tuple[float, float]:
        df = pd.DataFrame([features])
        pred = float(self.model.predict(df)[0])

        # Confidence heuristic: penalize bad weather / events / extreme hours,
        # since those are exactly the conditions our synthetic training data
        # is thinnest on. Replace with real prediction-interval width (SHAP /
        # quantile regression) once you're training on live data.
        confidence = 0.90
        if features["weather_condition"] != 0:
            confidence -= 0.15
        if features["is_event_nearby"]:
            confidence -= 0.10
        if features["hour"] < 5 or features["hour"] > 22:
            confidence -= 0.10
        confidence = max(0.30, round(confidence, 2))

        return pred, confidence


forecaster = DemandForecaster()


# ---------------------------------------------------------------------------
# 2. OR-Tools fleet/frequency optimizer (+ greedy fallback)
# ---------------------------------------------------------------------------
def _candidate_table(current_fleet: int, predicted_demand: float):
    """Precompute (fleet, headway, cost) for every feasible fleet size.
    Costs are plain integers computed in Python, then handed to CP-SAT as an
    allowed-assignment table — this keeps the model a straightforward
    constraint-satisfaction/optimization problem (no variable*variable
    multiplication, which CP-SAT can't do directly) while still letting the
    solver pick the minimum-cost row, same as a real MILP fleet-sizing model
    would."""
    min_fleet, max_fleet = 1, current_fleet + 6
    rows = []
    for fleet in range(min_fleet, max_fleet + 1):
        headway = max(MIN_FREQUENCY_MIN, min(MAX_FREQUENCY_MIN, int(TURNAROUND_MINUTES / fleet)))
        trips_per_hour = max(1, int(round(60 / headway)))
        served_capacity = trips_per_hour * fleet * BUS_CAPACITY
        shortfall = max(0, int(predicted_demand) - served_capacity)

        operating_cost = int(fleet * COST_PER_EXTRA_BUS * 100)
        waiting_cost = int(shortfall * WAIT_PENALTY_WEIGHT * 100)
        total_cost = operating_cost + waiting_cost

        rows.append((fleet, headway, total_cost))
    return rows


def solve_with_or_tools(predicted_demand: float, current_fleet: int) -> Optional[dict]:
    """CP-SAT model: choose the (fleet, headway) pair from the feasibility
    table that minimizes total cost = operating cost + passenger waiting
    cost. This is the 'rolling-horizon allocation' step from your reference
    sheet, simplified to a single-route decision per call."""
    rows = _candidate_table(current_fleet, predicted_demand)
    if not rows:
        return None

    model = cp_model.CpModel()

    fleet_domain = sorted({r[0] for r in rows})
    headway_domain = sorted({r[1] for r in rows})
    cost_domain = sorted({r[2] for r in rows})

    fleet_var = model.NewIntVarFromDomain(cp_model.Domain.FromValues(fleet_domain), "fleet")
    headway_var = model.NewIntVarFromDomain(cp_model.Domain.FromValues(headway_domain), "headway")
    cost_var = model.NewIntVarFromDomain(cp_model.Domain.FromValues(cost_domain), "cost")

    model.AddAllowedAssignments([fleet_var, headway_var, cost_var], rows)
    model.Minimize(cost_var)

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 1.5
    status = solver.Solve(model)

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None

    return {"fleet": solver.Value(fleet_var), "headway": solver.Value(headway_var)}


def greedy_fallback(predicted_demand: float, current_fleet: int) -> dict:
    """Deterministic, explainable fallback: buses needed = ceil(demand / capacity),
    clamped so we never ask for more than +4 buses in one shot."""
    buses_needed = int(np.ceil(predicted_demand / BUS_CAPACITY))
    fleet = max(1, min(buses_needed, current_fleet + 4))
    headway = max(MIN_FREQUENCY_MIN, min(MAX_FREQUENCY_MIN, int(TURNAROUND_MINUTES / fleet)))
    return {"fleet": fleet, "headway": headway}


def apply_anti_oscillation(route_id: str, proposed_fleet: int, current_fleet: int) -> int:
    """If we changed this route's target fleet size very recently, damp a
    flip-flop back toward the previous target unless the new signal is strong
    (>=2 bus difference)."""
    now = time.time()
    state = ROUTE_STATE.get(route_id)

    if state is None:
        ROUTE_STATE[route_id] = {"fleet": proposed_fleet, "ts": now}
        return proposed_fleet

    since_last = now - state["ts"]
    last_fleet = state["fleet"]

    if since_last < OSCILLATION_COOLDOWN_SEC and abs(proposed_fleet - last_fleet) < 2:
        # not enough new evidence to justify changing again so soon
        stabilized = last_fleet
    else:
        stabilized = proposed_fleet
        ROUTE_STATE[route_id] = {"fleet": stabilized, "ts": now}

    return stabilized


def optimize_frequency(predicted_demand: float, current_fleet: int, route_id: str) -> dict:
    result = solve_with_or_tools(predicted_demand, current_fleet)
    solver_used = "or_tools"

    if result is None:
        logger.warning("OR-Tools solve failed/timed out for %s — using greedy fallback.", route_id)
        result = greedy_fallback(predicted_demand, current_fleet)
        solver_used = "greedy_fallback"

    stabilized_fleet = apply_anti_oscillation(route_id, result["fleet"], current_fleet)
    headway = max(MIN_FREQUENCY_MIN, min(MAX_FREQUENCY_MIN, int(TURNAROUND_MINUTES / stabilized_fleet)))

    delta = stabilized_fleet - current_fleet
    if delta > 0:
        action = "DEPLOY_EXTRA_BUS"
    elif delta < 0:
        action = "RELEASE_BUS_TO_POOL"   # buses already running finish their trip; only the NEXT dispatch is skipped
    else:
        action = "MAINTAIN_SCHEDULE"

    return {
        "fleet": stabilized_fleet,
        "headway": headway,
        "delta": delta,
        "action": action,
        "solver_used": solver_used,
    }


# ---------------------------------------------------------------------------
# 3. API endpoints
# ---------------------------------------------------------------------------
@app.on_event("startup")
def _start_signal_scheduler():
    """Kicks off the background pollers (PredictHQ + exam calendars). This is
    the piece that replaces a person manually checking event listings once a
    week — see signals.py."""
    start_scheduler()


@app.get("/")
def read_root():
    return {"system": "BMTC Dynamic ML Frequency Optimization API", "status": "online"}


@app.post("/api/optimize", response_model=OptimizationRecommendation)
def get_optimization_recommendation(payload: RouteOptimizationRequest):
    try:
        # Live signals replace the old approach of trusting whatever the
        # caller happened to pass in is_event_nearby — this is the part that
        # used to be a person manually checking event listings once a week.
        active_signals = signal_store.active_for_route(payload.route_id)
        signal_names = [f"{s.name} ({s.category}, {s.expected_scale})" for s in active_signals]

        features = {
            "hour": payload.hour_of_day,
            "day_of_week": payload.day_of_week,
            "weather_condition": payload.weather_condition,
            "avg_speed": payload.average_speed_kmh,
            "is_event_nearby": 1 if (payload.is_event_nearby or active_signals) else 0,
        }

        predicted_demand, confidence = forecaster.predict_demand(features)

        if confidence < CONFIDENCE_FALLBACK_THRESHOLD:
            return OptimizationRecommendation(
                route_id=payload.route_id,
                predicted_passenger_demand=round(predicted_demand, 2),
                confidence_score=confidence,
                recommended_frequency_minutes=15,
                recommended_fleet_allocation=payload.current_fleet_count,
                delta_buses=0,
                action_type="MAINTAIN_SCHEDULE",
                solver_used="schedule_fallback",
                reasoning=(
                    f"Confidence {confidence} is below the {CONFIDENCE_FALLBACK_THRESHOLD} "
                    "safety threshold, so the system falls back to the standard fixed schedule "
                    "instead of trusting a shaky prediction."
                ),
                detected_signals=signal_names,
            )

        opt = optimize_frequency(predicted_demand, payload.current_fleet_count, payload.route_id)

        reasoning = (
            f"Predicted demand ~{int(predicted_demand)} passengers this window "
            f"(confidence {confidence}). {opt['solver_used']} recommends "
            f"{opt['fleet']} buses at a {opt['headway']}-min headway "
            f"({'+' if opt['delta'] >= 0 else ''}{opt['delta']} vs current fleet). "
            "No bus already on its trip is pulled off route — this only changes "
            "how many buses are dispatched on the next cycle."
        )
        if signal_names:
            reasoning += f" Live signal feed found: {', '.join(signal_names)} — factored in as an active event."

        return OptimizationRecommendation(
            route_id=payload.route_id,
            predicted_passenger_demand=round(predicted_demand, 2),
            confidence_score=confidence,
            recommended_frequency_minutes=opt["headway"],
            recommended_fleet_allocation=opt["fleet"],
            delta_buses=opt["delta"],
            action_type=opt["action"],
            solver_used=opt["solver_used"],
            reasoning=reasoning,
            detected_signals=signal_names,
        )

    except Exception as e:
        logger.exception("Optimization pipeline failure")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/optimize-stop", response_model=StopOptimizationResponse)
def optimize_stop_level(payload: StopOptimizationRequest):
    """Per-stop decision, independent of the route-level frequency call.
    A bus already en route can skip ONE stop with zero waiting passengers
    without affecting its trip status or the rest of its schedule."""
    action = "SERVE_STOP"
    reasoning = "Normal passenger load detected — bus stops as scheduled."

    if payload.current_waiting_passengers == 0:
        action = "SKIP_STOP_DYNAMIC"
        reasoning = (
            f"No waiting passengers at {payload.stop_name}. The bus bypasses this "
            "one stop and continues its trip normally — nothing about the trip itself changes."
        )
    elif payload.current_waiting_passengers > payload.bus_capacity_available:
        action = "ALERT_OVERCROWDING"
        reasoning = (
            f"{payload.current_waiting_passengers} waiting at {payload.stop_name} exceeds "
            f"available capacity ({payload.bus_capacity_available}). Flagging for a trailing "
            "buffer bus rather than overloading this one."
        )

    return StopOptimizationResponse(
        route_id=payload.route_id,
        stop_id=payload.stop_id,
        stop_name=payload.stop_name,
        action_type=action,
        reasoning=reasoning,
    )


@app.post("/api/signals")
def create_manual_signal(payload: ManualSignalRequest):
    """The human-in-the-loop escape hatch: add anything automation missed
    (a one-off local event) once, and it stays live until it expires —
    no re-editing a spreadsheet every week."""
    signal = add_manual_signal(payload)
    return {
        "signal_id": signal.signal_id,
        "affected_routes": signal.affected_routes,
        "note": "No routes matched" if not signal.affected_routes else "Attached to matching routes",
    }


@app.get("/api/signals")
def list_signals():
    """Everything currently active or upcoming, across all sources — useful
    for a dashboard view or for debugging why a route's demand changed."""
    return [s.dict() for s in signal_store.all_upcoming()]  # .dict() works on both pydantic v1 and v2


@app.post("/api/signals/refresh")
def trigger_signal_refresh():
    """Manually kick a PredictHQ pull instead of waiting for the scheduler —
    handy for demos."""
    count = refresh_predicthq_signals()
    return {"status": "refreshed", "new_or_updated": count, "total_active": len(signal_store.all_upcoming())}


@app.get("/api/live-buses")
def get_live_buses():
    """Mock bus feed shaped like what the frontend map expects. Replace the
    sample_active_buses list with a real GTFS-Realtime VehiclePositions feed
    or BMTC's own feed once you have access — the frontend doesn't need to
    change, just point it at real data with this same shape."""
    return {
        "source_portal": "https://nammabmtcapp.karnataka.gov.in/commuter/track-a-bus",
        "note": "Direct scraping of this portal will likely hit CORS/anti-bot walls — "
                "plan to get real feed access via BMTC/GTFS-Realtime instead of polling the site.",
        "sample_active_buses": [
            {"bus_id": "KA-01-F-1234", "route": "335-E", "lat": 12.9767, "lon": 77.5713, "speed": 9.2, "status": "delayed", "delay_min": 12},
            {"bus_id": "KA-01-F-5678", "route": "500-C", "lat": 12.9250, "lon": 77.6228, "speed": 21.4, "status": "on_time", "delay_min": 0},
            {"bus_id": "KA-01-F-9012", "route": "500-D", "lat": 12.9081, "lon": 77.6476, "speed": 18.7, "status": "on_time", "delay_min": 0},
            {"bus_id": "KA-01-F-3456", "route": "501BH", "lat": 12.9716, "lon": 77.5946, "speed": 22.4, "status": "on_time", "delay_min": 0},
        ],
    }


# Add to main.py after the existing endpoints

@app.post("/api/fetch/all")
def fetch_all_signals_endpoint():
    """Fetch signals from all configured sources"""
    from signals import refresh_predicthq_signals, refresh_exam_signals, signal_store
    
    results = {
        "predicthq": 0,
        "exam_calendars": 0,
        "total": 0,
        "active_after_fetch": 0
    }
    
    try:
        results["predicthq"] = refresh_predicthq_signals()
    except Exception as e:
        results["predicthq_error"] = str(e)
    
    try:
        results["exam_calendars"] = refresh_exam_signals()
    except Exception as e:
        results["exam_calendars_error"] = str(e)
    
    results["total"] = results["predicthq"] + results["exam_calendars"]
    results["active_after_fetch"] = len(signal_store.all_upcoming())
    
    return {
        "status": "success",
        "message": f"Fetched {results['total']} signals total",
        "details": results
    }


@app.post("/api/fetch/predicthq")
def fetch_predicthq_endpoint():
    """Fetch only PredictHQ signals"""
    from signals import refresh_predicthq_signals, signal_store
    
    count = refresh_predicthq_signals()
    
    return {
        "status": "success",
        "source": "predicthq",
        "signals_fetched": count,
        "total_active": len(signal_store.all_upcoming())
    }


@app.post("/api/fetch/exams")
def fetch_exams_endpoint():
    """Fetch only exam calendar signals"""
    from signals import refresh_exam_signals, signal_store
    
    count = refresh_exam_signals()
    
    return {
        "status": "success",
        "source": "exam_calendars",
        "signals_fetched": count,
        "total_active": len(signal_store.all_upcoming())
    }


@app.get("/api/signals/status")
def signals_status():
    """Get current signal store status"""
    from signals import signal_store, ROUTE_LOCATIONS
    
    all_signals = signal_store.all_upcoming()
    
    by_source = {}
    for sig in all_signals:
        by_source[sig.source] = by_source.get(sig.source, 0) + 1
    
    by_route = {}
    for route_id in ROUTE_LOCATIONS.keys():
        active = signal_store.active_for_route(route_id)
        if active:
            by_route[route_id] = [s.name for s in active]
    
    return {
        "total_active": len(all_signals),
        "by_source": by_source,
        "by_route": by_route,
        "signals": [
            {
                "id": s.signal_id,
                "name": s.name,
                "source": s.source,
                "category": s.category,
                "routes": s.affected_routes,
                "start": s.start_time.isoformat(),
                "end": s.end_time.isoformat()
            }
            for s in all_signals[:20]  # Limit to 20 for response size
        ]
    }


    