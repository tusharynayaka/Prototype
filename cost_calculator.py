"""
Cost Calculator and Deployment Tracker for BMTC Optimization
SIH 2026 | Team 501BH

Calculates operational costs, savings, and tracks deployment status
of bus frequency recommendations.
"""

import logging
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger("bmtc_backend.cost")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class CostConfig:
    """Cost configuration for bus operations"""
    # Per bus operational costs (per day)
    fuel_cost_per_bus: float = 5000.0  # Rs per bus per day
    driver_cost_per_bus: float = 1200.0  # Rs per bus per day
    maintenance_per_bus: float = 800.0  # Rs per bus per day
    insurance_per_bus: float = 300.0  # Rs per bus per day
    
    # Per bus operational costs (per hour)
    fuel_per_hour: float = 350.0  # Rs per hour
    driver_per_hour: float = 150.0  # Rs per hour
    
    # Passenger related costs
    passenger_wait_cost_per_min: float = 10.0  # Rs per passenger per minute
    overcrowding_penalty: float = 500.0  # Rs per overcrowded bus
    
    # Deployment costs
    deployment_setup_cost: float = 2000.0  # Rs per route for deployment
    notification_cost: float = 100.0  # Rs per notification
    
    # Operating hours per day
    operating_hours: float = 16.0  # 6 AM to 10 PM
    
    # Currency
    currency: str = "Rs"


class DeploymentStatus(Enum):
    """Deployment status of a recommendation"""
    PENDING = "pending"
    APPROVED = "approved"
    IN_PROGRESS = "in_progress"
    DEPLOYED = "deployed"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class CostBreakdown:
    """Detailed cost breakdown for a recommendation"""
    # Cost components
    current_operational_cost: float = 0.0
    recommended_operational_cost: float = 0.0
    fuel_cost: float = 0.0
    driver_cost: float = 0.0
    maintenance_cost: float = 0.0
    insurance_cost: float = 0.0
    passenger_wait_cost: float = 0.0
    overcrowding_cost: float = 0.0
    
    # Deployment costs
    deployment_setup_cost: float = 0.0
    notification_cost: float = 0.0
    total_deployment_cost: float = 0.0
    
    # Summary
    total_current_cost: float = 0.0
    total_recommended_cost: float = 0.0
    net_savings: float = 0.0
    one_time_investment: float = 0.0
    roi_percentage: float = 0.0
    
    # Payback period
    payback_days: float = 0.0
    payback_months: float = 0.0
    
    # Currency
    currency: str = "Rs"


@dataclass
class DeploymentRecord:
    """Record of a deployment"""
    route_id: str
    route_name: str
    timestamp: datetime
    status: DeploymentStatus
    previous_fleet: int
    new_fleet: int
    headway: int
    cost_breakdown: CostBreakdown
    reason: str
    deployed_by: str = "system"
    notes: str = ""


class CostCalculator:
    """Calculate costs and ROI for optimization recommendations"""
    
    def __init__(self, config: CostConfig = None):
        self.config = config or CostConfig()
        self.deployments: List[DeploymentRecord] = []
        self.currency = self.config.currency
    
    def calculate_costs(
        self,
        current_fleet: int,
        recommended_fleet: int,
        headway: int,
        predicted_demand: float,
        passenger_wait_time: float = 5.0
    ) -> CostBreakdown:
        """
        Calculate detailed cost breakdown for a recommendation
        
        Args:
            current_fleet: Current number of buses
            recommended_fleet: Recommended number of buses
            headway: Recommended headway in minutes
            predicted_demand: Predicted passenger demand
            passenger_wait_time: Average passenger wait time in minutes
        """
        breakdown = CostBreakdown(currency=self.currency)
        
        operating_hours = self.config.operating_hours
        
        # 1. Current operational costs
        fuel_current = current_fleet * self.config.fuel_per_hour * operating_hours
        driver_current = current_fleet * self.config.driver_per_hour * operating_hours
        maintenance_current = current_fleet * self.config.maintenance_per_bus
        insurance_current = current_fleet * self.config.insurance_per_bus
        
        breakdown.current_operational_cost = fuel_current + driver_current + maintenance_current + insurance_current
        
        # 2. Recommended operational costs
        fuel_recommended = recommended_fleet * self.config.fuel_per_hour * operating_hours
        driver_recommended = recommended_fleet * self.config.driver_per_hour * operating_hours
        maintenance_recommended = recommended_fleet * self.config.maintenance_per_bus
        insurance_recommended = recommended_fleet * self.config.insurance_per_bus
        
        breakdown.recommended_operational_cost = fuel_recommended + driver_recommended + maintenance_recommended + insurance_recommended
        
        # Individual costs
        breakdown.fuel_cost = abs(fuel_recommended - fuel_current)
        breakdown.driver_cost = abs(driver_recommended - driver_current)
        breakdown.maintenance_cost = abs(maintenance_recommended - maintenance_current)
        breakdown.insurance_cost = abs(insurance_recommended - insurance_current)
        
        # 3. Passenger wait time cost
        current_capacity = current_fleet * 45
        recommended_capacity = recommended_fleet * 45
        
        if predicted_demand > current_capacity:
            excess_demand = predicted_demand - current_capacity
            breakdown.passenger_wait_cost = excess_demand * passenger_wait_time * self.config.passenger_wait_cost_per_min
        
        # 4. Overcrowding cost
        if predicted_demand > current_capacity * 0.9:
            breakdown.overcrowding_cost = self.config.overcrowding_penalty * (current_fleet if current_fleet > 0 else 1)
        
        # 5. Deployment costs
        delta = recommended_fleet - current_fleet
        if delta != 0:
            breakdown.deployment_setup_cost = self.config.deployment_setup_cost
            breakdown.notification_cost = self.config.notification_cost
        breakdown.total_deployment_cost = breakdown.deployment_setup_cost + breakdown.notification_cost
        
        # 6. Summary
        breakdown.total_current_cost = breakdown.current_operational_cost + breakdown.passenger_wait_cost + breakdown.overcrowding_cost
        breakdown.total_recommended_cost = breakdown.recommended_operational_cost
        
        if breakdown.total_current_cost > breakdown.total_recommended_cost:
            breakdown.net_savings = breakdown.total_current_cost - breakdown.total_recommended_cost
        else:
            breakdown.net_savings = -(breakdown.total_recommended_cost - breakdown.total_current_cost)
        
        breakdown.one_time_investment = breakdown.total_deployment_cost
        
        # 7. ROI and payback period
        if breakdown.one_time_investment > 0 and breakdown.net_savings > 0:
            breakdown.roi_percentage = (breakdown.net_savings / breakdown.one_time_investment) * 100
            breakdown.payback_days = breakdown.one_time_investment / breakdown.net_savings
            breakdown.payback_months = breakdown.payback_days / 30
        else:
            breakdown.roi_percentage = 0.0
            breakdown.payback_days = 0.0
            breakdown.payback_months = 0.0
        
        return breakdown
    
    def create_deployment_record(
        self,
        route_id: str,
        route_name: str,
        current_fleet: int,
        recommended_fleet: int,
        headway: int,
        cost_breakdown: CostBreakdown,
        reason: str,
        status: DeploymentStatus = DeploymentStatus.PENDING,
        deployed_by: str = "system",
        notes: str = ""
    ) -> DeploymentRecord:
        """Create a deployment record for a recommendation"""
        
        record = DeploymentRecord(
            route_id=route_id,
            route_name=route_name,
            timestamp=datetime.now(),
            status=status,
            previous_fleet=current_fleet,
            new_fleet=recommended_fleet,
            headway=headway,
            cost_breakdown=cost_breakdown,
            reason=reason,
            deployed_by=deployed_by,
            notes=notes
        )
        self.deployments.append(record)
        return record
    
    def update_deployment_status(
        self,
        route_id: str,
        new_status: DeploymentStatus,
        notes: str = ""
    ) -> Optional[DeploymentRecord]:
        """Update the status of the most recent deployment for a route"""
        
        for record in reversed(self.deployments):
            if record.route_id == route_id:
                record.status = new_status
                if notes:
                    record.notes = notes
                return record
        return None
    
    def get_deployment_history(
        self,
        route_id: Optional[str] = None,
        status: Optional[DeploymentStatus] = None,
        limit: int = 10
    ) -> List[DeploymentRecord]:
        """Get deployment history, optionally filtered by route and status"""
        
        records = self.deployments
        
        if route_id:
            records = [r for r in records if r.route_id == route_id]
        
        if status:
            records = [r for r in records if r.status == status]
        
        return records[-limit:]
    
    def get_total_savings(self) -> Dict[str, float]:
        """Calculate total savings from all deployments"""
        
        total_current = 0.0
        total_recommended = 0.0
        
        for record in self.deployments:
            if record.status in [DeploymentStatus.DEPLOYED, DeploymentStatus.IN_PROGRESS]:
                total_current += record.cost_breakdown.total_current_cost
                total_recommended += record.cost_breakdown.total_recommended_cost
        
        return {
            "total_current_cost": total_current,
            "total_recommended_cost": total_recommended,
            "total_savings": total_current - total_recommended,
            "currency": self.currency
        }


def format_cost(amount: float, currency: str = "Rs") -> str:
    """Format cost with currency and thousands separator"""
    if amount >= 10000000:
        return f"{currency}{amount/10000000:.2f}Cr"
    elif amount >= 100000:
        return f"{currency}{amount/100000:.2f}L"
    elif amount >= 1000:
        return f"{currency}{amount:,.0f}"
    else:
        return f"{currency}{amount:.2f}"


# Global calculator instance
cost_calculator = CostCalculator()