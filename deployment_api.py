"""
Deployment Management API for BMTC Optimization
SIH 2026 | Team 501BH

API endpoints for managing and tracking deployments.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime

from cost_calculator import (
    cost_calculator,
    DeploymentStatus,
    DeploymentRecord,
    CostBreakdown,
    format_cost
)

router = APIRouter(prefix="/api/deployments", tags=["Deployments"])


class DeploymentRequest(BaseModel):
    route_id: str
    status: str
    notes: Optional[str] = ""


class DeploymentResponse(BaseModel):
    route_id: str
    route_name: str
    timestamp: datetime
    status: str
    previous_fleet: int
    new_fleet: int
    headway: int
    total_cost: float
    net_savings: float
    reason: str
    notes: str


@router.get("/history")
def get_deployment_history(
    route_id: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 10
):
    """Get deployment history"""
    status_enum = None
    if status:
        try:
            status_enum = DeploymentStatus(status)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid status: {status}")
    
    records = cost_calculator.get_deployment_history(route_id, status_enum, limit)
    
    return {
        "count": len(records),
        "records": [
            {
                "route_id": r.route_id,
                "route_name": r.route_name,
                "timestamp": r.timestamp.isoformat(),
                "status": r.status.value,
                "previous_fleet": r.previous_fleet,
                "new_fleet": r.new_fleet,
                "headway": r.headway,
                "net_savings": r.cost_breakdown.net_savings,
                "roi_percentage": r.cost_breakdown.roi_percentage,
                "reason": r.reason[:200],
                "notes": r.notes
            }
            for r in records
        ]
    }


@router.post("/update")
def update_deployment_status(request: DeploymentRequest):
    """Update deployment status"""
    try:
        status = DeploymentStatus(request.status)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid status: {request.status}")
    
    record = cost_calculator.update_deployment_status(
        route_id=request.route_id,
        new_status=status,
        notes=request.notes
    )
    
    if not record:
        raise HTTPException(status_code=404, detail=f"No deployment found for route {request.route_id}")
    
    return {
        "route_id": record.route_id,
        "route_name": record.route_name,
        "status": record.status.value,
        "notes": record.notes,
        "timestamp": record.timestamp.isoformat()
    }


@router.get("/savings")
def get_total_savings():
    """Get total cost savings"""
    savings = cost_calculator.get_total_savings()
    return {
        "total_current_cost": savings["total_current_cost"],
        "total_recommended_cost": savings["total_recommended_cost"],
        "total_savings": savings["total_savings"],
        "formatted": {
            "total_current_cost": format_cost(savings["total_current_cost"]),
            "total_recommended_cost": format_cost(savings["total_recommended_cost"]),
            "total_savings": format_cost(savings["total_savings"])
        },
        "currency": savings["currency"]
    }


@router.get("/recommendations")
def get_recommendations():
    """Get all pending recommendations"""
    pending = cost_calculator.get_deployment_history(status=DeploymentStatus.PENDING)
    
    return {
        "pending_count": len(pending),
        "recommendations": [
            {
                "route_id": r.route_id,
                "route_name": r.route_name,
                "fleet_change": r.new_fleet - r.previous_fleet,
                "action": "ADD" if r.new_fleet > r.previous_fleet else "REMOVE" if r.new_fleet < r.previous_fleet else "KEEP",
                "net_savings": r.cost_breakdown.net_savings,
                "roi_percentage": r.cost_breakdown.roi_percentage,
                "reason": r.reason[:200]
            }
            for r in pending
        ]
    }