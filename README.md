# BMTC Dynamic ML-Based Bus Frequency Optimization
### SIH 2026 | Team 501BH · PES University

A real-time intelligence & dynamic frequency optimization system for the **Bangalore Metropolitan Transport Corporation (BMTC)**.

---

## 🌟 Key Features

1. **XGBoost Passenger Demand Forecasting**: Predicts corridor passenger load factoring in hour, day-of-week, rain/weather conditions, congestion speed, and nearby real-time events.
2. **Google OR-Tools Constraint Optimization**: Computes mathematically optimal bus fleet allocation and dispatch headway (frequency in minutes) balancing passenger wait time penalties against operational vehicle costs.
3. **Hyper-Local Live News & Event Scraping**: Scrapes Bengaluru news (Times of India, Deccan Herald, The Hindu, NDTV, Bangalore Mirror, Inshorts API, and PES University campus announcements) in real time every 90 seconds.
4. **Anti-Oscillation Dampener**: Stabilizes dispatch schedules with a 15-minute cooldown window to prevent rapid alternating dispatch fluctuations.
5. **Real-Time Interactive Control Room**: Modern HTML5/Tailwind dashboard with live interactive OpenStreetMap/Leaflet bus tracking, WebSocket real-time feeds, event severity filters, confidence indicators, and recommendation approvals.

---

## 🚀 How to Run

### Option 1: One-Click Launcher (Windows)
Double-click [`run.bat`](file:///d:/tusha.TUSHAR/Desktop/Tushar/PES/Profiles/SIH/Prototype/run.bat) or execute in PowerShell/CMD:
```bat
run.bat
```

### Option 2: Manual Terminal Execution

1. **Install Dependencies:**
```bash
pip install fastapi "uvicorn[standard]" xgboost pandas numpy scikit-learn ortools requests beautifulsoup4
```

2. **Start Server:**
```bash
python main.py
```

3. **Open Dashboard:**
Open your browser and navigate to:
```
http://localhost:8010
```
*(Also accessible from other devices on the same local network via your machine's IP, e.g. `http://<YOUR_IP>:8010`)*

---

## 📂 Project Structure

- [`main.py`](file:///d:/tusha.TUSHAR/Desktop/Tushar/PES/Profiles/SIH/Prototype/main.py): FastAPI backend, XGBoost demand model, OR-Tools frequency solver, live news scraper, and WebSocket live bus broadcaster.
- [`index.html`](file:///d:/tusha.TUSHAR/Desktop/Tushar/PES/Profiles/SIH/Prototype/index.html): Control Room UI with live Leaflet map, news event ticker, intelligence filters, and dispatch recommendations.
- [`run.bat`](file:///d:/tusha.TUSHAR/Desktop/Tushar/PES/Profiles/SIH/Prototype/run.bat): One-click Windows runner script with automatic dependency verification and auto-restart loop.
