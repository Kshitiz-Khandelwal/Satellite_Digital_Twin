# 🛰️ Satellite Digital Twin

### An Interactive 3D Satellite Monitoring and Orbital Visualization Platform

[![Python](https://img.shields.io/badge/Python-Backend-blue?logo=python)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-API-009688?logo=fastapi)](https://fastapi.tiangolo.com/)
[![CesiumJS](https://img.shields.io/badge/CesiumJS-3D%20Visualization-6CADDF)](https://cesium.com/platform/cesiumjs/)
[![License](https://img.shields.io/badge/License-To%20Be%20Decided-lightgrey)]()

**Satellite Digital Twin** is a software project focused on visualizing and monitoring satellites in orbit through an interactive 3D representation of Earth.

The platform combines satellite data processing, orbital mechanics, coordinate transformations, and 3D geospatial visualization to create a foundation for a satellite digital-twin system.

The goal is to make satellite orbital information easier to visualize, understand, and monitor through a unified dashboard.

---

## 🌍 Project Overview

Satellites continuously move around Earth, and understanding their orbital position and movement requires processing orbital data and converting it into meaningful geographic information.

Satellite Digital Twin aims to simplify this process by combining:

- Satellite orbital data acquisition.
- Orbital propagation using SGP4.
- Coordinate transformation.
- Satellite position visualization.
- Interactive 3D Earth rendering.
- Telemetry monitoring through a dashboard.

Instead of looking only at raw orbital parameters, users can visualize satellite movement in a 3D environment.

---

## 🎯 Project Objectives

1. Build a modular backend for satellite data ingestion and processing.
2. Implement orbital propagation using the SGP4 model.
3. Convert orbital coordinates into usable geographic information.
4. Visualize Earth and satellite trajectories using CesiumJS.
5. Develop a dashboard for monitoring satellite telemetry.
6. Build a foundation for a more complete satellite digital-twin platform.

---

## ✨ Features

### 1. Satellite Data Acquisition

The backend includes adapters for obtaining and generating satellite data.

- CelesTrak integration for orbital element data.
- Satellite simulation adapter for testing.
- Structured satellite data models.
- Measurement data representation.

### 2. Orbital Mechanics

The project includes the foundational components needed to process satellite orbital information.

- SGP4 orbital propagation module.
- Coordinate transformation utilities.
- TEME-related coordinate processing.
- Satellite position calculations.

### 3. 3D Earth Visualization

The frontend uses CesiumJS to provide an interactive 3D Earth environment.

Current visualization components include:

- Earth satellite imagery.
- 3D globe visualization.
- Satellite marker.
- Orbital trajectory.
- Ground track visualization.
- Procedurally generated cloud texture.
- Atmospheric visual effects.

### 4. Satellite Monitoring Dashboard

The dashboard provides a centralized interface for observing satellite information.

It currently includes:

- Satellite altitude.
- Orbital velocity.
- Latitude and longitude.
- Position coordinates.
- Mission profile information.
- Event stream.
- UTC clock.

---

## 🏗️ System Architecture

The project is organized into two main components.

### Backend — Data Processing and Orbital Mechanics

The Python backend is responsible for satellite data acquisition, simulation, orbital calculations, and coordinate processing.

Main components:

| Module | Responsibility |
|---|---|
| `adapters/celestrak.py` | CelesTrak data integration |
| `adapters/simulator.py` | Simulated satellite data |
| `models/satellite.py` | Satellite data model |
| `models/measurement.py` | Measurement data model |
| `twin/propagator.py` | SGP4 orbital propagation |
| `twin/coordinate.py` | Coordinate transformations |
| `main.py` | FastAPI application entry point |

### Frontend — Visualization and Monitoring

The frontend provides the user interface and 3D visualization.

| Component | Responsibility |
|---|---|
| `index.html` | Dashboard structure |
| `style.css` | Dashboard styling |
| `dashboard.js` | Dashboard behavior and Cesium visualization |

### Architecture Flow

```text
             SATELLITE DATA
                   |
          +--------+--------+
          |                 |
       CelesTrak         Simulator
          |                 |
          +--------+--------+
                   |
             BACKEND
               Python
                   |
          Satellite Models
                   |
          SGP4 Propagation
                   |
        Coordinate Processing
                   |
             FastAPI
                   |
            FRONTEND
                   |
              CesiumJS
                   |
          3D EARTH GLOBE
                   |
         SATELLITE DASHBOARD
```

**Note:** The backend processing modules and frontend visualization are currently separate development components. Full integration of backend SGP4 output with frontend satellite positions is a planned milestone.

---

## 🛠️ Technology Stack

| Technology | Purpose |
|---|---|
| Python | Backend development |
| FastAPI | Backend API framework |
| Pydantic | Data validation and models |
| NumPy | Scientific computation |
| SciPy | Scientific computing |
| SGP4 | Orbital propagation |
| Skyfield | Astronomical and orbital utilities |
| CelesTrak | Satellite orbital data source |
| HTML5 | Frontend structure |
| CSS3 | Frontend styling |
| JavaScript | Frontend logic |
| CesiumJS | 3D Earth visualization |
| Pytest | Automated testing |

---

## 📁 Project Structure

```text
Satellite_Digital_Twin/
│
├── backend/
│   ├── app/
│   │   ├── adapters/
│   │   │   ├── celestrak.py
│   │   │   └── simulator.py
│   │   │
│   │   ├── api/
│   │   ├── core/
│   │   │
│   │   ├── models/
│   │   │   ├── satellite.py
│   │   │   └── measurement.py
│   │   │
│   │   ├── pipeline/
│   │   ├── twin/
│   │   │   ├── coordinate.py
│   │   │   └── propagator.py
│   │   │
│   │   └── main.py
│   │
│   ├── tests/
│   │   ├── test_celestrak.py
│   │   ├── test_coordinate.py
│   │   └── test_simulator.py
│   │
│   ├── requirements.txt
│   └── pyproject.toml
│
├── frontend/
│   ├── templates/
│   │   └── index.html
│   │
│   ├── static/
│   │   ├── css/
│   │   │   └── style.css
│   │   │
│   │   └── js/
│   │       └── dashboard.js
│   │
│   └── app.py
│
├── .gitignore
├── README.md
└── PROJECT_STATUS.md
```

---

## 🚀 Getting Started

Follow these instructions to run the project locally.

### Prerequisites

- Python installed on your system.
- Git.
- A modern web browser.
- Internet connection for external satellite data and map imagery.

### 1. Clone the Repository

```bash
git clone https://github.com/lakshmijshastry1406/Satellite_Digital_Twin.git
```

Navigate into the project:

```bash
cd Satellite_Digital_Twin
```

### 2. Create a Virtual Environment

```bash
python -m venv .venv
```

Activate it on Windows:

```powershell
.\.venv\Scripts\Activate.ps1
```

### 3. Install Backend Dependencies

```powershell
cd backend
pip install -r requirements.txt
```

### 4. Start the FastAPI Server

From the `backend` directory, run:

```powershell
uvicorn app.main:app --reload
```

### 5. Open the Dashboard

Visit:

**Dashboard:** http://127.0.0.1:8000/

**API Documentation:** http://127.0.0.1:8000/docs

**Health Check:** http://127.0.0.1:8000/health

---

## 🧪 Running Tests

The backend includes automated tests for important components.

Run the tests from the `backend` directory:

```bash
python -m pytest -q
```

Last verified test result:

```text
13 passed in 8.30s
```

The test suite covers the CelesTrak adapter, coordinate transformations, and satellite simulator.

---

## 📊 Development Status

The project is actively under development.

| Component | Status |
|---|---|
| Backend architecture | Implemented |
| CelesTrak adapter | Implemented |
| Satellite simulator | Implemented |
| SGP4 propagator | Implemented |
| Coordinate transformations | Implemented |
| Automated backend tests | 13 passing in last verified run |
| FastAPI entry point | Implemented |
| CesiumJS globe | Implemented |
| Satellite dashboard | Implemented |
| Procedural cloud texture | Initial implementation |
| Animated cloud movement | In progress |
| Atmospheric rendering | In progress |
| Day/night lighting | Planned |
| Live backend-to-frontend integration | Planned |

### Important Implementation Note

The current frontend uses illustrative simulated orbital movement.

Although the backend contains an SGP4 propagation module, its output is not yet fully integrated into the frontend visualization.

Similarly, the current procedural cloud texture is a visual approximation and does not represent live meteorological cloud observations.

The project will progressively integrate these components as development continues.

---

## 🗺️ Future Roadmap

### Phase 1 — Earth Visualization

- [x] 3D Earth globe.
- [x] Satellite imagery.
- [x] Initial cloud texture.
- [ ] Improved cloud formations.
- [ ] Animated cloud movement.
- [ ] Atmospheric scattering.
- [ ] Day/night boundary.
- [ ] Satellite illumination and shadows.

### Phase 2 — Satellite Tracking

- [ ] Connect SGP4 propagation to the frontend.
- [ ] Display actual propagated satellite coordinates.
- [ ] Synchronize orbital trajectory with backend data.
- [ ] Improve ground track calculations.
- [ ] Support multiple satellite objects.

### Phase 3 — Digital Twin Capabilities

- [ ] Satellite state management.
- [ ] Historical orbital tracking.
- [ ] Telemetry history.
- [ ] Satellite health monitoring.
- [ ] Orbital anomaly detection.
- [ ] Event and alert management.

### Phase 4 — Platform Improvements

- [ ] Responsive dashboard.
- [ ] Improved API architecture.
- [ ] Persistent telemetry storage.
- [ ] Deployment configuration.
- [ ] Performance optimization.

---

## 🔭 Long-Term Vision

The long-term goal is to evolve this project into a more complete satellite digital-twin environment.

Potential capabilities include:

- Real-time satellite position monitoring.
- Multiple satellite tracking.
- Orbital state visualization.
- Historical satellite movement.
- Telemetry analysis.
- Satellite health monitoring.
- Orbital anomaly identification.
- Interactive geospatial visualization.

These are future objectives, not claims about currently implemented functionality.

---

## 🤝 Contributing

Contributions, suggestions, and technical discussions are welcome.

If you would like to contribute:

1. Fork the repository.
2. Create a new feature branch.
3. Implement your changes.
4. Test your implementation.
5. Submit a pull request with a clear explanation.

Please keep changes modular and maintain the separation between backend processing and frontend visualization.

---

## 📌 Repository

**Project:** Satellite Digital Twin

**GitHub:** https://github.com/lakshmijshastry1406/Satellite_Digital_Twin

**Primary branch:** `main`

---

*Satellite Digital Twin — Bringing satellite orbital information into an interactive 3D environment.*
