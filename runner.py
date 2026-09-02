#!/usr/bin/env python3
"""
BMTC Optimization Runner - Clean UI with AI Explanations
SIH 2026 | Team 501BH

Run: python runner.py [--once] [--explain] [--route ROUTE_ID]
"""

import argparse
import logging
import sys
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from tabulate import tabulate
from colorama import init, Fore, Style

# Initialize colorama for colored output
init(autoreset=True)

# Import from your existing modules
from signals import (
    refresh_predicthq_signals,
    refresh_exam_signals,
    signal_store,
    start_scheduler,
    ROUTE_LOCATIONS,
    ROUTE_NAMES
)

from explainer import AIExplainer

# Configure logging
logging.basicConfig(
    level=logging.WARNING,
    format='%(asctime)s - %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger("bmtc_runner")

# Suppress noisy logs
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
    
    # Base demand per route
    base_demand = {
        "501BH": 80,
        "335-E": 75,
        "500-C": 65,
        "500-D": 60,
    }
    
    base = base_demand.get(route_id, 70)
    
    # Peak hours (8-11am, 5-8pm)
    if (8 <= hour <= 11) or (17 <= hour <= 20):
        base += 40
    
    # Weekend effect
    if day >= 5:
        base -= 20
    
    # Event effect
    event_boost = 0
    for signal in active_signals:
        if signal.expected_scale == "high":
            event_boost += 35
        elif signal.expected_scale == "medium":
            event_boost += 20
        else:
            event_boost += 10
    
    base += event_boost
    
    # Random variation for realism
    import random
    base += random.randint(-10, 10)
    base = max(20, base)
    
    # Confidence based on number of signals
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
    
    # Only increase if needed, never decrease (per your requirement)
    if required_buses > current_fleet:
        # Add buses but limit increase
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
# Display Functions
# ---------------------------------------------------------------------------

def print_header():
    """Print the main header"""
    print(Fore.CYAN + "=" * 80)
    print(Fore.CYAN + Style.BRIGHT + "🚍 BMTC DYNAMIC BUS OPTIMIZATION SYSTEM")
    print(Fore.CYAN + "=" * 80)
    print(Fore.YELLOW + f"📍 Bangalore, India | {datetime.now().strftime('%A, %B %d, %Y %H:%M:%S')}")
    print(Fore.CYAN + "=" * 80 + Style.RESET_ALL)
    print()


def print_route_analysis(
    route_id: str,
    route_name: str,
    current_fleet: int,
    predicted_demand: float,
    confidence: float,
    opt_result: Dict,
    active_signals: List,
    explanation: str
):
    """Print a clean, detailed analysis for a single route"""
    
    delta = opt_result['delta']
    action_label = opt_result['action_label']
    
    # Route header with color based on action
    if delta > 0:
        header_color = Fore.GREEN
        status_icon = "📈"
        status_text = "ADD BUSES"
    elif delta < 0:
        header_color = Fore.YELLOW
        status_icon = "📉"
        status_text = "REMOVE BUSES"
    else:
        header_color = Fore.BLUE
        status_icon = "✅"
        status_text = "OPTIMAL"
    
    print()
    print(header_color + "┌" + "─" * 78 + "┐")
    print(header_color + f"│ {status_icon} {route_name} ({route_id})")
    print(header_color + "├" + "─" * 78 + "┤")
    
    # Key metrics
    print(header_color + f"│ {Fore.WHITE}📊 Current Fleet:{Style.RESET_ALL} {current_fleet} buses")
    print(header_color + f"│ {Fore.WHITE}🎯 Recommended:{Style.RESET_ALL} {opt_result['fleet']} buses ({action_label} {abs(delta) if delta != 0 else 'no change'})")
    print(header_color + f"│ {Fore.WHITE}⏱️  Headway:{Style.RESET_ALL} {opt_result['headway']} minutes")
    print(header_color + f"│ {Fore.WHITE}👥 Predicted Demand:{Style.RESET_ALL} {int(predicted_demand)} passengers")
    print(header_color + f"│ {Fore.WHITE}🎯 Confidence:{Style.RESET_ALL} {int(confidence * 100)}%")
    
    # Signals
    if active_signals:
        print(header_color + "├" + "─" * 78 + "┤")
        print(header_color + f"│ {Fore.YELLOW}📡 Active Signals ({len(active_signals)}):{Style.RESET_ALL}")
        for sig in active_signals[:3]:
            scale_emoji = "🔴" if sig.expected_scale == "high" else "🟡" if sig.expected_scale == "medium" else "🟢"
            routes = ", ".join(sig.affected_routes[:2]) if sig.affected_routes else "none"
            print(header_color + f"│   {scale_emoji} {sig.name[:45]} → {routes}")
        if len(active_signals) > 3:
            print(header_color + f"│   ... and {len(active_signals) - 3} more")
    
    # Explanation
    print(header_color + "├" + "─" * 78 + "┤")
    print(header_color + f"│ {Fore.WHITE}💡 WHY THIS RECOMMENDATION:{Style.RESET_ALL}")
    
    # Format explanation with wrapping
    lines = explanation.split('\n')
    for line in lines:
        if line.strip():
            # Wrap long lines
            words = line.split()
            current_line = ""
            for word in words:
                if len(current_line) + len(word) + 1 > 70:
                    print(header_color + f"│   {current_line}")
                    current_line = word
                else:
                    current_line = current_line + " " + word if current_line else word
            if current_line:
                print(header_color + f"│   {current_line}")
    
    print(header_color + "└" + "─" * 78 + "┘" + Style.RESET_ALL)


def print_summary(results: List[Dict]):
    """Print a summary table of all routes"""
    print()
    print(Fore.CYAN + "─" * 80)
    print(Fore.CYAN + Style.BRIGHT + "📊 SUMMARY OF ALL ROUTES")
    print(Fore.CYAN + "─" * 80)
    
    # Prepare table data
    table_data = []
    for r in results:
        delta = r['delta']
        if delta > 0:
            action_display = Fore.GREEN + f"+{delta}" + Style.RESET_ALL
            status = "ADD"
        elif delta < 0:
            action_display = Fore.YELLOW + f"{delta}" + Style.RESET_ALL
            status = "REMOVE"
        else:
            action_display = Fore.BLUE + "0" + Style.RESET_ALL
            status = "KEEP"
        
        table_data.append([
            r['route_name'][:20],
            r['current_fleet'],
            r['recommended_fleet'],
            action_display,
            f"{r['headway']}min",
            f"{int(r['predicted_demand'])}",
            f"{int(r['confidence'] * 100)}%",
            f"{len(r['signals'])}" + (" 🔴" if len(r['signals']) > 0 else " 🟢"),
            status
        ])
    
    headers = ["Route", "Current", "Rec", "Δ", "Headway", "Demand", "Conf", "Events", "Action"]
    print(tabulate(table_data, headers=headers, tablefmt="grid"))
    
    # Total impact
    total_delta = sum(r['delta'] for r in results)
    total_add = sum(1 for r in results if r['delta'] > 0)
    
    print()
    print(Fore.CYAN + "─" * 80)
    if total_delta > 0:
        print(Fore.GREEN + f"📈 TOTAL IMPACT: +{total_delta} buses added across {total_add} routes")
    elif total_delta < 0:
        print(Fore.YELLOW + f"📉 TOTAL IMPACT: {total_delta} buses removed")
    else:
        print(Fore.BLUE + "✅ TOTAL IMPACT: No changes needed - all routes optimized")
    print(Fore.CYAN + "─" * 80 + Style.RESET_ALL)


# ---------------------------------------------------------------------------
# Main Fetch and Analyze Function
# ---------------------------------------------------------------------------

def fetch_and_analyze(use_ai: bool = True, route_filter: Optional[str] = None):
    """Fetch signals and analyze all routes"""
    
    print_header()
    
    # 1. Fetch signals
    print(Fore.YELLOW + "📡 FETCHING SIGNALS..." + Style.RESET_ALL)
    
    # Fetch from PredictHQ (with free API fallback)
    try:
        phq_count = refresh_predicthq_signals()
        print(f"  • PredictHQ/Free APIs: {phq_count} signals")
    except Exception as e:
        print(f"  • PredictHQ: Error - {e}")
        phq_count = 0
    
    # Fetch from Exam Calendars
    try:
        exam_count = refresh_exam_signals()
        print(f"  • Exam Calendars: {exam_count} signals")
    except Exception as e:
        print(f"  • Exam Calendars: Error - {e}")
        exam_count = 0
    
    total_signals = len(signal_store.all_upcoming())
    print(f"  • TOTAL ACTIVE SIGNALS: {total_signals}")
    print()
    
    # 2. Initialize AI Explainer
    explainer = AIExplainer() if use_ai else None
    if use_ai and explainer:
        print(Fore.CYAN + f"🧠 AI Explainer: {explainer.provider.upper()}" + Style.RESET_ALL)
    
    print()
    print(Fore.CYAN + "─" * 80)
    print(Fore.CYAN + Style.BRIGHT + "🔍 ANALYZING ROUTES" + Style.RESET_ALL)
    print(Fore.CYAN + "─" * 80)
    
    # 3. Analyze each route
    results = []
    routes_to_analyze = [route_filter] if route_filter else list(ROUTE_LOCATIONS.keys())
    
    for route_id in routes_to_analyze:
        # Get current fleet (default to 10)
        current_fleet = 10  # You can customize this
        
        # Get active signals for this route
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
        
        # Predict demand
        predicted_demand, confidence = predict_demand_for_route(route_id, active_signals)
        
        # Optimize
        opt_result = optimize_frequency(predicted_demand, current_fleet)
        
        # Get AI explanation
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
        
        # Store result
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
        
        # Print detailed analysis
        print_route_analysis(
            route_id=route_id,
            route_name=result['route_name'],
            current_fleet=current_fleet,
            predicted_demand=predicted_demand,
            confidence=confidence,
            opt_result=opt_result,
            active_signals=active_signals,
            explanation=explanation
        )
    
    # 4. Print summary
    print_summary(results)
    
    return results


# ---------------------------------------------------------------------------
# Continuous Runner
# ---------------------------------------------------------------------------

def run_continuous(interval: int = 60, use_ai: bool = True):
    """Run continuously with real-time updates"""
    
    print(Fore.CYAN + "=" * 80)
    print(Fore.CYAN + Style.BRIGHT + "🚍 BMTC DYNAMIC BUS OPTIMIZATION SYSTEM")
    print(Fore.CYAN + "=" * 80)
    print(Fore.YELLOW + f"📍 Running continuously - Updates every {interval} seconds")
    print(Fore.YELLOW + "Press Ctrl+C to stop")
    print(Fore.CYAN + "=" * 80 + Style.RESET_ALL)
    print()
    
    # Start the background scheduler
    scheduler = start_scheduler()
    
    try:
        cycle = 0
        while True:
            cycle += 1
            print(Fore.CYAN + f"\n🔄 CYCLE {cycle} - {datetime.now().strftime('%H:%M:%S')}" + Style.RESET_ALL)
            print(Fore.CYAN + "─" * 80 + Style.RESET_ALL)
            
            # Run analysis
            fetch_and_analyze(use_ai=use_ai)
            
            # Wait for next cycle
            print(Fore.YELLOW + f"\n⏳ Next update in {interval} seconds..." + Style.RESET_ALL)
            time.sleep(interval)
            
    except KeyboardInterrupt:
        print(Fore.YELLOW + "\n\n🛑 Shutting down..." + Style.RESET_ALL)
        scheduler.shutdown()
        print(Fore.GREEN + "✅ Done." + Style.RESET_ALL)


# ---------------------------------------------------------------------------
# Main Entry Point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="BMTC Route Optimization Runner with AI Explanations",
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
    
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run once and exit (default: continuous)"
    )
    parser.add_argument(
        "--continuous",
        type=int,
        default=0,
        help="Run continuously with updates every N seconds"
    )
    parser.add_argument(
        "--no-ai",
        action="store_true",
        help="Disable AI explanations (use template-based)"
    )
    parser.add_argument(
        "--route",
        type=str,
        help="Analyze only a specific route (e.g., 501BH)"
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=60,
        help="Update interval in seconds (default: 60)"
    )
    
    args = parser.parse_args()
    
    use_ai = not args.no_ai
    
    # Run once
    if args.once:
        fetch_and_analyze(use_ai=use_ai, route_filter=args.route)
        return
    
    # Continuous mode
    if args.continuous > 0:
        run_continuous(interval=args.continuous, use_ai=use_ai)
        return
    
    # Default: run once
    fetch_and_analyze(use_ai=use_ai, route_filter=args.route)


if __name__ == "__main__":
    # Import math for calculations
    import math
    main()