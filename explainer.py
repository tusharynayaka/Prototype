"""
AI Explainer for BMTC Optimization Recommendations
SIH 2026 | Team 501BH

Uses Groq or Gemini to generate human-readable explanations
for why specific optimization suggestions are made.
"""

import logging
import os
from typing import Dict, List, Optional
from datetime import datetime

logger = logging.getLogger("bmtc_backend.explainer")

# Try to import Groq, fallback to mock if not available
try:
    from groq import Groq
    GROQ_AVAILABLE = True
except ImportError:
    GROQ_AVAILABLE = False
    logger.warning("Groq not available, using mock explanations")

# Try to import Gemini
try:
    import google.generativeai as genai
    GEMINI_AVAILABLE = True
except ImportError:
    GEMINI_AVAILABLE = False
    logger.warning("Gemini not available")


class AIExplainer:
    """Generate human-readable explanations for optimization decisions"""
    
    def __init__(self):
        self.groq_client = None
        self.gemini_client = None
        self.provider = "template"  # default fallback

        # Try Groq first
        groq_key = os.environ.get("GROQ_API_KEY")
        if groq_key and GROQ_AVAILABLE:
            try:
                self.groq_client = Groq(api_key=groq_key)
                self.provider = "groq"
                logger.info("AI Explainer initialized with Groq")
                return
            except Exception as e:
                logger.warning(f"Failed to initialize Groq: {e}")

        # Try Gemini as fallback
        gemini_key = os.environ.get("GEMINI_API_KEY")
        if gemini_key and GEMINI_AVAILABLE:
            try:
                genai.configure(api_key=gemini_key)
                self.gemini_client = genai.GenerativeModel('gemini-pro')
                self.provider = "gemini"
                logger.info("AI Explainer initialized with Gemini")
                return
            except Exception as e:
                logger.warning(f"Failed to initialize Gemini: {e}")

        # If no AI works, keep provider="template"
        logger.info("AI Explainer using template-based explanations")               
    def generate_explanation(
        self,
        route_name: str,
        route_id: str,
        current_fleet: int,
        recommended_fleet: int,
        predicted_demand: float,
        confidence: float,
        active_signals: List[Dict],
        headway: int,
        action: str
    ) -> str:
        """Generate a detailed, human-readable explanation"""
        
        if self.provider == "groq" and self.groq_client:
            return self._generate_with_groq(
                route_name, route_id, current_fleet, recommended_fleet,
                predicted_demand, confidence, active_signals, headway, action
            )
        elif self.provider == "gemini" and self.gemini_client:
            return self._generate_with_gemini(
                route_name, route_id, current_fleet, recommended_fleet,
                predicted_demand, confidence, active_signals, headway, action
            )
        else:
            return self._generate_template(
                route_name, route_id, current_fleet, recommended_fleet,
                predicted_demand, confidence, active_signals, headway, action
            )
    
    def _generate_with_groq(
        self,
        route_name: str,
        route_id: str,
        current_fleet: int,
        recommended_fleet: int,
        predicted_demand: float,
        confidence: float,
        active_signals: List[Dict],
        headway: int,
        action: str
    ) -> str:
        """Generate explanation using Groq LLM"""
        
        signals_text = ""
        if active_signals:
            for sig in active_signals:
                signals_text += f"- {sig.get('name', 'Unknown')} ({sig.get('category', 'event')}) - Scale: {sig.get('expected_scale', 'medium')}\n"
        else:
            signals_text = "No active events detected"
        
        prompt = f"""
You are a public transportation operations expert for BMTC (Bangalore Metropolitan Transport Corporation).

Provide a clear, concise, and human-readable explanation for a bus frequency optimization recommendation.

Make sure its consies and under 5 points each with at mots 20 words.

ROUTE: {route_name} ({route_id})
CURRENT FLEET: {current_fleet} buses
RECOMMENDED FLEET: {recommended_fleet} buses
HEADWAY: {headway} minutes between buses
PREDICTED DEMAND: {int(predicted_demand)} passengers
CONFIDENCE: {int(confidence * 100)}%
ACTION: {action} ({'Adding buses' if recommended_fleet > current_fleet else 'Removing buses' if recommended_fleet < current_fleet else 'Maintaining current schedule'})

ACTIVE EVENTS/SIGNALS AFFECTING THIS ROUTE:
{signals_text}

Please provide a professional explanation that covers:
1. WHY this recommendation is being made (demand drivers, events)
2. WHAT this means for passengers (wait times, crowding)
3. HOW this affects operations (fleet usage, efficiency)

Be professional but accessible to non-technical stakeholders.
"""
        
        try:
            completion = self.groq_client.chat.completions.create(
                model="openai/gpt-oss-120b",
                messages=[
                    {"role": "system", "content": "You are a public transportation operations expert providing clear, professional explanations."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.5,
                max_tokens=500
            )
            return completion.choices[0].message.content or self._generate_template(
                route_name, route_id, current_fleet, recommended_fleet,
                predicted_demand, confidence, active_signals, headway, action
            )
        except Exception as e:
            logger.error(f"Groq explanation failed: {e}")
            return self._generate_template(
                route_name, route_id, current_fleet, recommended_fleet,
                predicted_demand, confidence, active_signals, headway, action
            )
    
    def _generate_with_gemini(
        self,
        route_name: str,
        route_id: str,
        current_fleet: int,
        recommended_fleet: int,
        predicted_demand: float,
        confidence: float,
        active_signals: List[Dict],
        headway: int,
        action: str
    ) -> str:
        """Generate explanation using Gemini"""
        
        signals_text = ""
        if active_signals:
            for sig in active_signals:
                signals_text += f"- {sig.get('name', 'Unknown')} ({sig.get('category', 'event')}) - Scale: {sig.get('expected_scale', 'medium')}\n"
        else:
            signals_text = "No active events detected"
        
        prompt = f"""
You are a public transportation operations expert for BMTC (Bangalore Metropolitan Transport Corporation).

Provide a clear, concise, and human-readable explanation for a bus frequency optimization recommendation.

ROUTE: {route_name} ({route_id})
CURRENT FLEET: {current_fleet} buses
RECOMMENDED FLEET: {recommended_fleet} buses
HEADWAY: {headway} minutes between buses
PREDICTED DEMAND: {int(predicted_demand)} passengers
CONFIDENCE: {int(confidence * 100)}%
ACTION: {action} ({'Adding buses' if recommended_fleet > current_fleet else 'Removing buses' if recommended_fleet < current_fleet else 'Maintaining current schedule'})

ACTIVE EVENTS/SIGNALS AFFECTING THIS ROUTE:
{signals_text}

Please provide a professional explanation that covers:
1. WHY this recommendation is being made (demand drivers, events)
2. WHAT this means for passengers (wait times, crowding)
3. HOW this affects operations (fleet usage, efficiency)

Keep it to 3-4 paragraphs. Be specific and use the actual numbers provided.
"""
        
        try:
            response = self.gemini_client.generate_content(prompt)
            return response.text or self._generate_template(
                route_name, route_id, current_fleet, recommended_fleet,
                predicted_demand, confidence, active_signals, headway, action
            )
        except Exception as e:
            logger.error(f"Gemini explanation failed: {e}")
            return self._generate_template(
                route_name, route_id, current_fleet, recommended_fleet,
                predicted_demand, confidence, active_signals, headway, action
            )
    
    def _generate_template(
        self,
        route_name: str,
        route_id: str,
        current_fleet: int,
        recommended_fleet: int,
        predicted_demand: float,
        confidence: float,
        active_signals: List[Dict],
        headway: int,
        action: str
    ) -> str:
        """Generate template-based explanation (fallback)"""
        
        current_capacity = current_fleet * 45
        recommended_capacity = recommended_fleet * 45
        delta = recommended_fleet - current_fleet
        
        parts = []
        
        if delta > 0:
            parts.append(
                f"Based on predicted demand of approximately {int(predicted_demand)} passengers "
                f"(confidence: {int(confidence * 100)}%), we recommend adding {delta} additional "
                f"bus{'es' if delta > 1 else ''} to Route {route_id} ({route_name}). "
                f"This will increase capacity from {current_capacity} to {recommended_capacity} passengers."
            )
        elif delta < 0:
            parts.append(
                f"Based on predicted demand of approximately {int(predicted_demand)} passengers "
                f"(confidence: {int(confidence * 100)}%), we recommend reducing the fleet by "
                f"{abs(delta)} bus{'es' if abs(delta) > 1 else ''} on Route {route_id} ({route_name}). "
                f"This will adjust capacity from {current_capacity} to {recommended_capacity} passengers."
            )
        else:
            parts.append(
                f"Based on predicted demand of approximately {int(predicted_demand)} passengers "
                f"(confidence: {int(confidence * 100)}%), the current fleet of {current_fleet} buses "
                f"on Route {route_id} ({route_name}) is well-suited. No changes are needed at this time."
            )
        
        if active_signals:
            event_names = [s.get('name', 'Unknown') for s in active_signals[:3]]
            parts.append(
                f"The recommendation considers {len(active_signals)} active event{'s' if len(active_signals) > 1 else ''} "
                f"affecting this route: {', '.join(event_names)}{' and more' if len(active_signals) > 3 else ''}. "
                f"These events are expected to increase passenger demand during the affected periods."
            )
        
        if delta > 0:
            parts.append(
                f"With the recommended {headway}-minute headway, passengers will experience "
                f"shorter wait times and reduced crowding. The extra buses will provide additional "
                f"capacity during peak demand periods."
            )
        elif delta < 0:
            parts.append(
                f"With the recommended {headway}-minute headway, service efficiency is optimized "
                f"while maintaining acceptable wait times. The reduced fleet allows resources to be "
                f"reallocated to higher-demand routes."
            )
        else:
            parts.append(
                f"The current {headway}-minute headway provides balanced service levels, "
                f"ensuring passengers have reasonable wait times while maintaining operational efficiency."
            )
        
        return "\n\n".join(parts)