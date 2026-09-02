#!/usr/bin/env python3
"""
BMTC Unified Backend - Standalone Version
SIH 2026 | Team 501BH
Runs without any API keys - uses mock data for demonstration
"""

import logging
import time
import sys
import random
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import math

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger("bmtc")

# Suppress noisy logs
logging.getLogger("apscheduler").setLevel(logging.WARNING)


# ============================================================================
# MOCK DATA - No API Keys Needed
# ============================================================================

ROUTE_LOCATIONS = {
    "501BH": (12.9716, 77.5946),
    "335-E": (12.9767, 77.5713),
    "500-C": (12.9250, 77.6228),
    "500-D": (12.9081, 77.6476),
}

ROUTE_NAMES = {
    "501BH": "Hebbal-BTM Layout",
    "335-E": "KBS-Electronic City",
    "500-C": "Majestic-Bannerghatta",
    "500-D": "Shivajinagar-Bidadi",
}

# Mock events that change over time
MOCK_EVENTS = [
    {"name": "Cricket Match - Chinnaswamy Stadium", "category": "sports", "lat": 12.9784, "lon": 77.5908, "scale": "high"},
    {"name": "Tech Summit - BIEC", "category": "conferences", "lat": 12.9351, "lon": 77.5350, "scale": "medium"},
    {"name": "Music Festival - Palace Grounds", "category": "festivals", "lat": 13.0025, "lon": 77.5946, "scale": "high"},
    {"name": "PES University Exams", "category": "exam", "lat": 12.9351, "lon": 77.5350, "scale": "medium"},
    {"name": "Food Festival - Church Street", "category": "community", "lat": 12.9762, "lon": 77.6033, "scale": "low"},
    {"name": "Marathon - MG Road", "category": "sports", "lat": 12.9767, "lon": 77.6033, "scale": "medium"},
]

BUS_CAPACITY = 45
TURNAROUND_MINUTES = 90.0
MIN_FREQUENCY_MIN = 4
MAX_FREQUENCY_MIN = 30


# ============================================================================
# Mock Signal Store
# ============================================================================

class MockSignal:
    def __init__(self, name, category, lat, lon, scale, affected_routes=None):
        self.name = name
        self.category = category
        self.lat = lat
        self.lon = lon
        self.expected_scale = scale
        self.affected_routes = affected_routes or []
        self.signal_id = f"mock_{name.replace(' ', '_')}"
        self.source = "mock"
        self.start_time = datetime.now() - timedelta(hours=1)
        self.end_time = datetime.now() + timedelta(hours=6)
        self.confidence = 0.85
    
    def is_active_or_upcoming(self, at=None):
        return True


class MockSignalStore:
    def __init__(self):
        self._signals = []
        self._refresh_mock_signals()
    
    def _refresh_mock_signals(self):
        """Generate mock signals with random variations"""
        self._signals = []
        
        # Randomly select 2-4 events to be active
        active_events = random.sample(MOCK_EVENTS, random.randint(2, 4))
        
        for event in active_events:
            # Match to routes based on distance
            affected = []
            for route_id, (r_lat, r_lon) in ROUTE_LOCATIONS.items():
                dist = self._haversine(event['lat'], event['lon'], r_lat, r_lon)
                if dist < 5.0:  # Within 5km
                    affected.append(route_id)
            
            # If no routes matched, assign randomly
            if not affected:
                affected = random.sample(list(ROUTE_LOCATIONS.keys()), random.randint(1, 2))
            
            signal = MockSignal(
                name=event['name'],
                category=event['category'],
                lat=event['lat'],
                lon=event['lon'],
                scale=event['scale'],
                affected_routes=affected
            )
            self._signals.append(signal)
    
    def _haversine(self, lat1, lon1, lat2, lon2):
        R = 6371
        lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
        dlon = lon2 - lon1
        dlat = lat2 - lat1
        a = math.sin(dlat/2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon/2)**2
        return 2 * R * math.asin(math.sqrt(a))
    
    def active_for_route(self, route_id):
        return [s for s in self._signals if route_id in s.affected_routes]
    
    def all_upcoming(self):
        return self._signals
    
    def add(self, signal):
        self._signals.append(signal)
    
    def purge_expired(self):
        pass


# ============================================================================
# Mock Demand Forecaster
# ============================================================================

class MockDemandForecaster:
    def __init__(self):
        logger.info("Mock Demand Forecaster initialized")
    
    def predict_demand(self, features):
        """Simple demand prediction based on time and events"""
        hour = features.get('hour', datetime.now().hour)
        day = features.get('day_of_week', datetime.now().weekday())
        is_event = features.get('is_event_nearby', 0)
        weather = features.get('weather_condition', 0)
        
        # Base demand
        base = 80
        
        # Peak hours (8-11am, 5-8pm)
        if (8 <= hour <= 11) or (17 <= hour <= 20):
            base += 40
        
        # Weekend effect
        if day >= 5:
            base -= 20
        
        # Weather effect
        if weather == 1:  # Rain
            base += 15
        elif weather == 2:  # Heavy traffic
            base += 25
        
        # Event effect
        if is_event:
            base += random.randint(20, 50)
        
        # Random variation
        base += random.randint(-15, 15)
        base = max(20, base)
        
        # Confidence
        confidence = 0.85 - (0.1 if weather > 0 else 0) - (0.1 if is_event else 0)
        confidence = max(0.5, min(0.95, confidence))
        
        return float(base), float(confidence)


# ============================================================================
# Optimization Functions
# ============================================================================

def optimize_frequency(predicted_demand: float, current_fleet: int, route_id: str) -> Dict:
    """Simple optimization logic"""
    
    # Calculate required buses
    required_buses = max(1, int(math.ceil(predicted_demand / BUS_CAPACITY)))
    
    # Apply some smoothing
    if required_buses > current_fleet + 3:
        required_buses = current_fleet + 3
    elif required_buses < current_fleet - 2:
        required_buses = current_fleet - 2
    
    required_buses = max(1, required_buses)
    
    # Calculate headway
    headway = max(MIN_FREQUENCY_MIN, min(MAX_FREQUENCY_MIN, int(TURNAROUND_MINUTES / required_buses)))
    
    delta = required_buses - current_fleet
    
    if delta > 0:
        action = "DEPLOY_EXTRA_BUS"
    elif delta < 0:
        action = "RELEASE_BUS_TO_POOL"
    else:
        action = "MAINTAIN_SCHEDULE"
    
    return {
        "fleet": required_buses,
        "headway": headway,
        "delta": delta,
        "action": action,
        "solver_used": "mock_optimizer",
        "reasoning": f"Demand {int(predicted_demand)} passengers requires {required_buses} buses"
    }


# ============================================================================
# Timer Display
# ============================================================================

class TimerDisplay:
    def __init__(self):
        self.signal_interval = 10
        self.optimize_interval = 5
        self.signal_last_run = datetime.now()
        self.optimize_last_run = datetime.now()
    
    def get_timers(self) -> Tuple[int, int]:
        now = datetime.now()
        signal_elapsed = (now - self.signal_last_run).total_seconds()
        signal_remaining = max(0, self.signal_interval - signal_elapsed)
        optimize_elapsed = (now - self.optimize_last_run).total_seconds()
        optimize_remaining = max(0, self.optimize_interval - optimize_elapsed)
        return int(signal_remaining), int(optimize_remaining)
    
    def mark_signal_run(self):
        self.signal_last_run = datetime.now()
    
    def mark_optimize_run(self):
        self.optimize_last_run = datetime.now()


# ============================================================================
# Main System
# ============================================================================

class UnifiedBMTCSystem:
    def __init__(self):
        self.signal_store = MockSignalStore()
        self.forecaster = MockDemandForecaster()
        self.timer = TimerDisplay()
        self.current_fleet: Dict[str, int] = {r: 10 for r in ROUTE_LOCATIONS.keys()}
        self.running = True
        self.stats = {
            "signals_fetched": 0,
            "optimizations_run": 0,
            "cycles": 0,
            "errors": 0
        }
        self.last_results = []
        logger.info("System initialized with MOCK data (no API keys needed)")
    
    def collect_signals(self) -> int:
        """Refresh mock signals"""
        print("\n[FETCHING SIGNALS]")
        
        # Randomize signals each time
        self.signal_store._refresh_mock_signals()
        all_signals = self.signal_store.all_upcoming()
        
        print(f"  Active signals: {len(all_signals)}")
        
        if all_signals:
            for s in all_signals:
                routes = ", ".join(s.affected_routes) if s.affected_routes else "none"
                print(f"    - {s.name} ({s.category}) -> {routes}")
        else:
            print("    No signals found")
        
        self.stats["signals_fetched"] += len(all_signals)
        return len(all_signals)
    
    def optimize_route(self, route_id: str) -> Optional[Dict]:
        """Optimize a single route"""
        try:
            active_signals = self.signal_store.active_for_route(route_id)
            current_fleet = self.current_fleet.get(route_id, 10)
            
            now = datetime.now()
            features = {
                "hour": now.hour,
                "day_of_week": now.weekday(),
                "weather_condition": random.choice([0, 0, 0, 1]),  # Occasionally rain
                "avg_speed": 14.5,
                "is_event_nearby": 1 if active_signals else 0,
            }
            
            demand, confidence = self.forecaster.predict_demand(features)
            opt_result = optimize_frequency(demand, current_fleet, route_id)
            
            new_fleet = opt_result['fleet']
            self.current_fleet[route_id] = new_fleet
            
            result = {
                'route_id': route_id,
                'route_name': ROUTE_NAMES.get(route_id, route_id),
                'timestamp': now,
                'signal_count': len(active_signals),
                'signal_names': [s.name for s in active_signals],
                'predicted_demand': round(demand, 2),
                'confidence': round(confidence, 2),
                'recommended_fleet': new_fleet,
                'current_fleet': current_fleet,
                'delta_buses': opt_result['delta'],
                'headway_minutes': opt_result['headway'],
                'action': opt_result['action'],
                'solver_used': opt_result['solver_used'],
            }
            
            self.stats["optimizations_run"] += 1
            return result
            
        except Exception as e:
            print(f"  Error: {e}")
            self.stats["errors"] += 1
            return None
    
    def run_optimization_cycle(self):
        """Run optimization for all routes"""
        print("\n[OPTIMIZING ROUTES]")
        
        results = []
        for route_id in ROUTE_LOCATIONS.keys():
            result = self.optimize_route(route_id)
            if result:
                results.append(result)
        
        self.last_results = results
        
        if results:
            print("\nRECOMMENDATIONS:")
            print("-" * 70)
            print(f"{'Route':<22} {'Action':<12} {'Details':<35} {'Events'}")
            print("-" * 70)
            
            for r in results:
                action_arrow = "ADD" if r['delta_buses'] > 0 else "REMOVE" if r['delta_buses'] < 0 else "KEEP"
                bus_count = abs(r['delta_buses']) if r['delta_buses'] != 0 else r['recommended_fleet']
                
                if r['delta_buses'] > 0:
                    details = f"+{bus_count} buses, {r['headway_minutes']}min"
                elif r['delta_buses'] < 0:
                    details = f"-{bus_count} buses, {r['headway_minutes']}min"
                else:
                    details = f"{r['recommended_fleet']} buses, {r['headway_minutes']}min"
                
                events = f"{r['signal_count']} events" if r['signal_count'] > 0 else "none"
                print(f"{r['route_name']:<22} {action_arrow:<12} {details:<35} {events}")
            
            # Show total impact
            total_delta = sum(r['delta_buses'] for r in results)
            deploy_routes = [r['route_name'] for r in results if r['delta_buses'] > 0]
            release_routes = [r['route_name'] for r in results if r['delta_buses'] < 0]
            
            print("-" * 70)
            print(f"TOTAL: {'+' if total_delta >= 0 else ''}{total_delta} buses")
            if deploy_routes:
                print(f"  ADD to: {', '.join(deploy_routes)}")
            if release_routes:
                print(f"  REMOVE from: {', '.join(release_routes)}")
            if not deploy_routes and not release_routes:
                print("  No changes needed")
            
            # Show detailed explanation for first changed route
            for r in results:
                if r['delta_buses'] != 0:
                    self.show_detailed_explanation(r)
                    break
    
    def show_detailed_explanation(self, result: Dict):
        """Show detailed explanation for a recommendation"""
        print("\n" + "=" * 70)
        print(f"DETAILED EXPLANATION: {result['route_name']}")
        print("=" * 70)
        
        if result['delta_buses'] > 0:
            print(f"ACTION: Add {result['delta_buses']} extra buses")
        elif result['delta_buses'] < 0:
            print(f"ACTION: Remove {abs(result['delta_buses'])} buses")
        else:
            print("ACTION: Keep current schedule")
        
        print(f"  Current: {result['current_fleet']} buses")
        print(f"  Recommended: {result['recommended_fleet']} buses")
        print(f"  Headway: {result['headway_minutes']} minutes between buses")
        print(f"  Confidence: {int(result['confidence']*100)}%")
        
        if result['signal_count'] > 0:
            print(f"\nActive events on this route:")
            for name in result['signal_names'][:3]:
                print(f"  - {name}")
            if len(result['signal_names']) > 3:
                print(f"  - and {len(result['signal_names'])-3} more...")
        
        # Generate explanation
        demand = result['predicted_demand']
        capacity = result['current_fleet'] * BUS_CAPACITY
        delta = result['delta_buses']
        
        print(f"\nWHY THIS RECOMMENDATION?")
        print("-" * 70)
        
        # Demand
        if demand > capacity * 1.2:
            print(f"  • Demand is very high ({int(demand)} passengers) compared to current capacity ({int(capacity)})")
        elif demand > capacity:
            print(f"  • Demand ({int(demand)} passengers) is higher than current capacity ({int(capacity)})")
        elif demand < capacity * 0.6:
            print(f"  • Demand is low ({int(demand)} passengers) compared to current capacity ({int(capacity)})")
        else:
            print(f"  • Demand ({int(demand)} passengers) matches current capacity ({int(capacity)}) well")
        
        # Events
        if result['signal_count'] > 0:
            print(f"  • There are {result['signal_count']} events happening on this route")
            print(f"  • Events increase passenger demand")
        
        # Recommendation
        if delta > 0:
            print(f"  • Need {delta} extra buses to handle the extra passengers")
            print(f"  • This will reduce waiting time for passengers")
        elif delta < 0:
            print(f"  • Can remove {abs(delta)} buses as demand is lower")
            print(f"  • These buses can be used on other routes")
        else:
            print(f"  • Current bus count is optimal for expected demand")
        
        print("=" * 70 + "\n")
    
    def run_continuous(self):
        """Main loop"""
        print("\n" + "=" * 70)
        print("BMTC DYNAMIC BUS OPTIMIZATION SYSTEM")
        print("=" * 70)
        print("Running with MOCK DATA - No API keys required")
        print("Signal fetch: every 10 seconds | Optimization: every 5 seconds")
        print("-" * 70 + "\n")
        
        # Initialize timers
        self.timer.signal_last_run = datetime.now() - timedelta(seconds=10)
        self.timer.optimize_last_run = datetime.now() - timedelta(seconds=5)
        
        cycle = 0
        
        try:
            while self.running:
                cycle += 1
                self.stats["cycles"] = cycle
                
                # Get timers
                signal_rem, opt_rem = self.timer.get_timers()
                
                # Display timer
                signal_bar = "[" + "=" * (10 - signal_rem) + " " * signal_rem + "]"
                opt_bar = "[" + "=" * (5 - opt_rem) + " " * opt_rem + "]"
                
                sys.stdout.write(f"\rNext signal: {signal_rem:2d}s {signal_bar}  |  Next optimize: {opt_rem:2d}s {opt_bar}")
                sys.stdout.flush()
                
                # Signal fetch
                if signal_rem == 0:
                    self.timer.mark_signal_run()
                    self.collect_signals()
                    print()
                
                # Optimization
                if opt_rem == 0:
                    self.timer.mark_optimize_run()
                    self.run_optimization_cycle()
                    print()
                
                # Stats every 30 cycles
                if cycle % 30 == 0:
                    print(f"\n[STATS] Cycle {cycle} | Signals: {self.stats['signals_fetched']} | "
                          f"Optimizations: {self.stats['optimizations_run']} | Errors: {self.stats['errors']}")
                    print()
                
                time.sleep(0.5)
                
        except KeyboardInterrupt:
            print("\n\nShutting down...")
            self.running = False
            print("System stopped.")
        except Exception as e:
            print(f"\nError: {e}")
            import traceback
            traceback.print_exc()


def main():
    import argparse
    parser = argparse.ArgumentParser(description="BMTC Unified System")
    parser.add_argument("--once", action="store_true", help="Run once and exit")
    args = parser.parse_args()
    
    system = UnifiedBMTCSystem()
    
    if args.once:
        system.collect_signals()
        system.run_optimization_cycle()
    else:
        system.run_continuous()


if __name__ == "__main__":
    main()