#!/usr/bin/env python3
"""
BMTC Optimization Runner - CLI for Backend Operations
SIH 2026 | Team 501BH

Run: python runner.py --once --route 501BH
"""

import argparse
import re
import logging
import sys
import time
import math
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from signals import (
    refresh_predicthq_signals,
    refresh_exam_signals,
    signal_store,
    start_scheduler,
    ROUTE_LOCATIONS,
    ROUTE_NAMES
)

from explainer import AIExplainer
from cost_calculator import cost_calculator, DeploymentStatus, format_cost


logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s', datefmt='%H:%M:%S')
logger = logging.getLogger("bmtc_runner")

logging.getLogger("apscheduler").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("requests").setLevel(logging.WARNING)
logging.getLogger("groq").setLevel(logging.WARNING)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BUS_CAPACITY = 45
TURNAROUND_MINUTES = 90.0
MIN_FREQUENCY_MIN = 4
MAX_FREQUENCY_MIN = 30


# ---------------------------------------------------------------------------
# Demand Forecasting (Simplified)
# ---------------------------------------------------------------------------

def predict_demand_for_route(route_id: str, active_signals: List) -> tuple:
    """Simple demand prediction based on time and events"""
    now = datetime.now()
    hour = now.hour
    day = now.weekday()
    
    base_demand = {
        "501BH": 380,
        "335-E": 350,
        "500-C": 300,
        "500-D": 280,
    }
    
    base = base_demand.get(route_id, 300)
    
    if (8 <= hour <= 11) or (17 <= hour <= 20):
        base += 40
    
    if day >= 5:
        base -= 20
    
    event_boost = 0
    for signal in active_signals:
        if signal.expected_scale == "high":
            event_boost += 35
        elif signal.expected_scale == "medium":
            event_boost += 20
        else:
            event_boost += 10
    
    base += event_boost
    base += -10 + 20  # random variation
    base = max(20, base)
    
    confidence = 0.85
    if len(active_signals) > 2:
        confidence -= 0.05
    if event_boost > 50:
        confidence -= 0.10
    confidence = max(0.60, min(0.95, confidence))
    
    return base, confidence


def optimize_frequency(predicted_demand: float, current_fleet: int) -> Dict:
    """Simple optimization logic"""
    required_buses = max(1, int(math.ceil(predicted_demand / BUS_CAPACITY)))
    
    if required_buses > current_fleet:
        required_buses = current_fleet + min(3, required_buses - current_fleet)
    else:
        required_buses = current_fleet
    
    required_buses = max(current_fleet, required_buses)
    
    headway = max(MIN_FREQUENCY_MIN, min(MAX_FREQUENCY_MIN, 
                                         int(TURNAROUND_MINUTES / required_buses)))
    
    delta = required_buses - current_fleet
    
    if delta > 0:
        action = "DEPLOY_EXTRA_BUS"
        action_label = "ADD"
    else:
        action = "MAINTAIN_SCHEDULE"
        action_label = "KEEP"
    
    return {
        "fleet": required_buses,
        "headway": headway,
        "delta": delta,
        "action": action,
        "action_label": action_label,
        "reasoning": f"Demand {int(predicted_demand)} passengers requires {required_buses} buses"
    }


# ---------------------------------------------------------------------------
# Main Fetch and Analyze Function
# ---------------------------------------------------------------------------

def fetch_and_analyze(use_ai: bool = True, route_filter: Optional[str] = None):
    """Fetch signals and analyze all routes"""
    
    print("=" * 80)
    print("BMTC DYNAMIC BUS OPTIMIZATION SYSTEM")
    print("=" * 80)
    print(f"Location: Bangalore, India | {datetime.now().strftime('%A, %B %d, %Y %H:%M:%S')}")
    print("=" * 80)
    print()
    
    # 1. Fetch signals
    print("FETCHING SIGNALS...")
    
    try:
        phq_count = refresh_predicthq_signals()
        print(f"  - PredictHQ/Free APIs: {phq_count} signals")
    except Exception as e:
        print(f"  - PredictHQ: Error - {e}")
        phq_count = 0
    
    try:
        exam_count = refresh_exam_signals()
        print(f"  - Exam Calendars: {exam_count} signals")
    except Exception as e:
        print(f"  - Exam Calendars: Error - {e}")
        exam_count = 0
    
    total_signals = len(signal_store.all_upcoming())
    print(f"  - TOTAL ACTIVE SIGNALS: {total_signals}")
    print()
    
    # 2. Initialize AI Explainer
# In fetch_and_analyze function, around line 168

# 2. Initialize AI Explainer
    explainer = AIExplainer() if use_ai else None
    if use_ai and explainer:
        print(f"AI Explainer: {explainer.provider.upper()}")
    else:
        print("AI Explainer: Disabled (using template-based explanations)")
        
    print()
    print("-" * 80)
    print("ANALYZING ROUTES")
    print("-" * 80)
    
    # 3. Analyze each route
    results = []
    routes_to_analyze = [route_filter] if route_filter else list(ROUTE_LOCATIONS.keys())
    
    for route_id in routes_to_analyze:
        current_fleet = 5
        
        active_signals = signal_store.active_for_route(route_id)
        signals_data = [
            {
                "name": s.name,
                "category": s.category,
                "expected_scale": s.expected_scale,
                "affected_routes": s.affected_routes
            }
            for s in active_signals
        ]
        
        predicted_demand, confidence = predict_demand_for_route(route_id, active_signals)
        opt_result = optimize_frequency(predicted_demand, current_fleet)
        
        if use_ai and explainer:
            explanation = explainer.generate_explanation(
                route_name=ROUTE_NAMES.get(route_id, route_id),
                route_id=route_id,
                current_fleet=current_fleet,
                recommended_fleet=opt_result['fleet'],
                predicted_demand=predicted_demand,
                confidence=confidence,
                active_signals=signals_data,
                headway=opt_result['headway'],
                action=opt_result['action_label']
            )
        else:
            explanation = f"Demand is {int(predicted_demand)} passengers. " \
                         f"Current fleet: {current_fleet} buses. " \
                         f"{'Adding' if opt_result['delta'] > 0 else 'Keeping'} {abs(opt_result['delta'])} buses."
        
        result = {
            'route_id': route_id,
            'route_name': ROUTE_NAMES.get(route_id, route_id),
            'current_fleet': current_fleet,
            'recommended_fleet': opt_result['fleet'],
            'delta': opt_result['delta'],
            'headway': opt_result['headway'],
            'predicted_demand': predicted_demand,
            'confidence': confidence,
            'signals': active_signals,
            'signals_data': signals_data,
            'action': opt_result['action_label'],
            'explanation': explanation
        }
        results.append(result)
        
        # Print analysis
        print()
        print(f"Route: {result['route_name']} ({route_id})")
        print(f"  Current Fleet: {current_fleet} buses")
        print(f"  Recommended: {opt_result['fleet']} buses ({opt_result['action_label']} {abs(opt_result['delta']) if opt_result['delta'] != 0 else 'no change'})")
        print(f"  Headway: {opt_result['headway']} minutes")
        print(f"  Predicted Demand: {int(predicted_demand)} passengers")
        print(f"  Confidence: {int(confidence * 100)}%")
        
        if active_signals:
            print(f"  Active Signals ({len(active_signals)}):")
            for sig in active_signals[:3]:
                routes = ", ".join(sig.affected_routes[:2]) if sig.affected_routes else "none"
                print(f"    - {sig.name} -> {routes}")
            if len(active_signals) > 3:
                print(f"    ... and {len(active_signals) - 3} more")
        
        print("  Explanation:")
        cleaned_explanation = clean_text(explanation)
        for line in cleaned_explanation.split('\n'):
            if line.strip():
                print(f"    {line}")
    
    # 4. Print summary
    print()
    print("-" * 80)
    print("SUMMARY OF ALL ROUTES")
    print("-" * 80)
    
    print(f"{'Route':<20} {'Current':<8} {'Rec':<8} {'Delta':<8} {'Headway':<10} {'Demand':<10} {'Conf':<8} {'Events':<8} {'Action'}")
    print("-" * 80)
    
    for r in results:
        delta = r['delta']
        if delta > 0:
            delta_display = f"+{delta}"
        elif delta < 0:
            delta_display = f"{delta}"
        else:
            delta_display = "0"
        
        print(f"{r['route_name'][:18]:<20} {r['current_fleet']:<8} {r['recommended_fleet']:<8} {delta_display:<8} {r['headway']}min{' ':<6} {int(r['predicted_demand']):<10} {int(r['confidence'] * 100)}%{' ':<5} {len(r['signals']):<8} {r['action']}")
    
    total_delta = sum(r['delta'] for r in results)
    total_add = sum(1 for r in results if r['delta'] > 0)
    
    print()
    print("-" * 80)
    if total_delta > 0:
        print(f"TOTAL IMPACT: +{total_delta} buses added across {total_add} routes")
    elif total_delta < 0:
        print(f"TOTAL IMPACT: {total_delta} buses removed")
    else:
        print("TOTAL IMPACT: No changes needed - all routes optimized")
    print("-" * 80)
    
    return results


def run_continuous(interval: int = 60, use_ai: bool = True):
    """Run continuously with real-time updates"""
    
    print("=" * 80)
    print("BMTC DYNAMIC BUS OPTIMIZATION SYSTEM")
    print("=" * 80)
    print(f"Running continuously - Updates every {interval} seconds")
    print("Press Ctrl+C to stop")
    print("=" * 80)
    print()
    
    scheduler = start_scheduler()
    
    try:
        cycle = 0
        while True:
            cycle += 1
            print(f"\nCYCLE {cycle} - {datetime.now().strftime('%H:%M:%S')}")
            print("-" * 80)
            
            fetch_and_analyze(use_ai=use_ai)
            
            print(f"\nNext update in {interval} seconds...")
            time.sleep(interval)
            
    except KeyboardInterrupt:
        print("\n\nShutting down...")
        scheduler.shutdown()
        print("Done.")


# ---------------------------------------------------------------------------
# Main Entry Point
# ---------------------------------------------------------------------------
def clean_text(text: str) -> str:
    """Remove markdown formatting and emojis from text."""
    # Remove bold/italic markdown
    text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)   # **bold** -> bold
    text = re.sub(r'\*([^*]+)\*', r'\1', text)       # *italic* -> italic
    text = re.sub(r'__(.+?)__', r'\1', text)         # __underline__ -> underline
    text = re.sub(r'_([^_]+)_', r'\1', text)         # _italic_ -> italic
    
    # Remove common emojis (simple range)
    emoji_pattern = re.compile(
        "["
        u"\U0001F600-\U0001F64F"  # emoticons
        u"\U0001F300-\U0001F5FF"  # symbols & pictographs
        u"\U0001F680-\U0001F6FF"  # transport & map symbols
        u"\U0001F1E0-\U0001F1FF"  # flags
        u"\U00002702-\U000027B0"
        u"\U000024C2-\U0001F251"
        "]+", 
        flags=re.UNICODE
    )
    text = emoji_pattern.sub(r'', text)
    
    # Remove extra spaces
    text = re.sub(r'\s+', ' ', text).strip()
    return text

def main():
    parser = argparse.ArgumentParser(
        description="BMTC Route Optimization Runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python runner.py                    # Run once with AI explanations
  python runner.py --once             # Run once and exit
  python runner.py --no-ai            # Run without AI (faster)
  python runner.py --route 501BH      # Analyze only specific route
  python runner.py --continuous 30    # Run continuously every 30s
        """
    )
    
    parser.add_argument("--once", action="store_true", help="Run once and exit (default: continuous)")
    parser.add_argument("--continuous", type=int, default=0, help="Run continuously with updates every N seconds")
    parser.add_argument("--no-ai", action="store_true", help="Disable AI explanations (use template-based)")
    parser.add_argument("--route", type=str, help="Analyze only a specific route (e.g., 501BH)")
    parser.add_argument("--interval", type=int, default=60, help="Update interval in seconds (default: 60)")
    
    args = parser.parse_args()
    
    use_ai = not args.no_ai
    
    if args.once:
        fetch_and_analyze(use_ai=use_ai, route_filter=args.route)
        return
    
    if args.continuous > 0:
        run_continuous(interval=args.continuous, use_ai=use_ai)
        return
    
    fetch_and_analyze(use_ai=use_ai, route_filter=args.route)


if __name__ == "__main__":
    main()