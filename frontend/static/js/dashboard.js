/* ==========================================================================
   ORBITAL — SATELLITE DIGITAL TWIN (Phase 2 Full Kalman-Fused Live HMI)
   Tragic Bytes · Space Tech & Data Systems
   
   Architecture:
   - Live 1 Hz WebSocket ingestion from Python SGP4 + EKF Fusion Engine
   - Real-time 3D CesiumJS Earth rendering & geodetic positioning
   - Dynamic 3D Kalman Covariance Confidence Zone (visibly widens on data drop)
   - Live Subsystem Physics (Power EPS, Thermal TCS, Eclipse, Comms)
   - Interactive Fault-Injection Sandbox & Naive Tracker Comparison
========================================================================== */

"use strict";

const $ = (id) => document.getElementById(id);

// --------------------------------------------------------------------------
// 1. UTC CLOCK
// --------------------------------------------------------------------------
function updateClock() {
    const el = $("utc-clock");
    if (el) {
        el.textContent = new Date().toISOString().slice(11, 19) + " UTC";
    }
}
updateClock();
setInterval(updateClock, 1000);


// --------------------------------------------------------------------------
// 2. CESIUM INITIALIZATION & CINEMATIC EARTH RENDERING
// --------------------------------------------------------------------------
const viewer = new Cesium.Viewer("cesiumContainer", {
    animation: false,
    timeline: false,
    baseLayerPicker: false,
    geocoder: false,
    homeButton: false,
    sceneModePicker: false,
    navigationHelpButton: false,
    fullscreenButton: false,
    infoBox: false,
    selectionIndicator: false
});

viewer.imageryLayers.removeAll();

// Esri World Imagery (Satellite)
const earthImagery = new Cesium.UrlTemplateImageryProvider({
    url: "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    credit: "Esri, Maxar, Earthstar Geographics"
});
const earthLayer = viewer.imageryLayers.addImageryProvider(earthImagery);
earthLayer.brightness = 0.94;
earthLayer.contrast = 1.08;
earthLayer.saturation = 1.15;
earthLayer.gamma = 0.96;

// Earth Atmosphere & Lighting
viewer.scene.globe.enableLighting = true;
viewer.scene.globe.showGroundAtmosphere = true;
viewer.scene.globe.baseColor = Cesium.Color.fromCssColorString("#071321");
viewer.scene.skyAtmosphere.show = true;
viewer.scene.skyAtmosphere.atmosphereLightIntensity = 11.0;
viewer.scene.backgroundColor = Cesium.Color.fromCssColorString("#02040a");
viewer.scene.globe.maximumScreenSpaceError = 1.6;


// --------------------------------------------------------------------------
// 3. 3D ENTITIES: TWIN SATELLITE, CONFIDENCE ZONE, & NAIVE GHOST
// --------------------------------------------------------------------------

// Main Twin Satellite Marker (Cyan Glow)
const twinSatelliteEntity = viewer.entities.add({
    name: "Digital Twin Satellite",
    position: Cesium.Cartesian3.fromDegrees(0, 0, 420000),
    point: {
        pixelSize: 11,
        color: Cesium.Color.fromCssColorString("#58d5f5"),
        outlineColor: Cesium.Color.WHITE,
        outlineWidth: 2,
    },
    label: {
        text: "ISS (DIGITAL TWIN)",
        font: "10px DM Mono, monospace",
        style: Cesium.LabelStyle.FILL_AND_OUTLINE,
        fillColor: Cesium.Color.fromCssColorString("#e0f2fe"),
        outlineColor: Cesium.Color.BLACK,
        outlineWidth: 3,
        verticalOrigin: Cesium.VerticalOrigin.BOTTOM,
        pixelOffset: new Cesium.Cartesian2(0, -14),
    }
});

// Dynamic Kalman Covariance Confidence Zone (3D semi-transparent sphere)
// Expands visibly when telemetry drops!
let currentConfidenceRadiusMeters = 300.0;
const confidenceZoneEntity = viewer.entities.add({
    name: "Kalman 3-Sigma Confidence Zone",
    position: new Cesium.CallbackProperty(() => twinSatelliteEntity.position.getValue(viewer.clock.currentTime), false),
    ellipsoid: {
        radii: new Cesium.CallbackProperty(() => {
            const r = Math.max(800.0, currentConfidenceRadiusMeters);
            return new Cesium.Cartesian3(r, r, r);
        }, false),
        material: Cesium.Color.fromCssColorString("#06b6d4").withAlpha(0.22),
        outline: true,
        outlineColor: Cesium.Color.fromCssColorString("#58d5f5").withAlpha(0.65),
    }
});

// Naive Tracker Ghost (Amber Dot that freezes when severed, demonstrating Twin superiority)
let showNaiveGhost = true;
let naivePositionCartesian = null;
const naiveGhostEntity = viewer.entities.add({
    name: "Naive Tracker (Baseline Comparison)",
    show: showNaiveGhost,
    position: new Cesium.CallbackProperty(() => naivePositionCartesian || twinSatelliteEntity.position.getValue(viewer.clock.currentTime), false),
    point: {
        pixelSize: 9,
        color: Cesium.Color.fromCssColorString("#f59e0b"),
        outlineColor: Cesium.Color.fromCssColorString("#78350f"),
        outlineWidth: 2,
    },
    label: {
        text: "NAIVE TRACKER (FROZEN)",
        font: "9px DM Mono, monospace",
        fillColor: Cesium.Color.fromCssColorString("#fde68a"),
        outlineColor: Cesium.Color.BLACK,
        outlineWidth: 2,
        verticalOrigin: Cesium.VerticalOrigin.TOP,
        pixelOffset: new Cesium.Cartesian2(0, 12),
        show: new Cesium.CallbackProperty(() => isSeverActive, false)
    }
});

// Orbital Forecast Track & Ground Track
const forecastOrbitEntity = viewer.entities.add({
    name: "Predicted Orbital Forecast Track",
    polyline: {
        positions: [],
        width: 2,
        material: new Cesium.PolylineDashMaterialProperty({
            color: Cesium.Color.fromCssColorString("#58d5f5").withAlpha(0.7),
            dashLength: 14.0
        })
    }
});

const groundTrackEntity = viewer.entities.add({
    name: "Sub-Satellite Ground Track",
    polyline: {
        positions: [],
        width: 1.5,
        material: Cesium.Color.fromCssColorString("#10b981").withAlpha(0.5)
    }
});


// --------------------------------------------------------------------------
// 4. WEBSOCKET REAL-TIME TELEMETRY INTEGRATION
// --------------------------------------------------------------------------
let ws = null;
let reconnectTimer = null;
let isSeverActive = false;
let isDelayActive = false;
let isOutliersActive = false;
let hasInitialCameraCentered = false;

function connectWebSocket() {
    if (ws) {
        try { ws.close(); } catch (e) {}
    }

    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const wsUrl = `${protocol}//${window.location.host}/ws/telemetry`;
    console.info("[Digital Twin] Connecting to WebSocket:", wsUrl);

    ws = new WebSocket(wsUrl);

    ws.onopen = () => {
        console.info("[Digital Twin] WebSocket connected successfully!");
        const badge = $("stream-connection-badge");
        if (badge) {
            badge.innerHTML = "<i></i> WEBSOCKET CONNECTED";
            badge.style.color = "var(--green)";
        }
        clearTimeout(reconnectTimer);
    };

    ws.onmessage = (event) => {
        try {
            const data = JSON.parse(event.data);
            handleTelemetryPacket(data);
        } catch (err) {
            console.error("[Digital Twin] Error parsing telemetry packet:", err);
        }
    };

    ws.onclose = () => {
        console.warn("[Digital Twin] WebSocket disconnected. Reconnecting in 2.0s...");
        const badge = $("stream-connection-badge");
        if (badge) {
            badge.innerHTML = "<i></i> RECONNECTING...";
            badge.style.color = "var(--amber)";
        }
        reconnectTimer = setTimeout(connectWebSocket, 2000);
    };

    ws.onerror = (err) => {
        console.error("[Digital Twin] WebSocket error:", err);
    };
}

connectWebSocket();


// --------------------------------------------------------------------------
// 5. PACKET HANDLER & HMI UPDATER
// --------------------------------------------------------------------------
function handleTelemetryPacket(data) {
    if (!data) return;

    // Robust normalization across engine shapes
    const geodetic = data.position_geodetic || {};
    const lat = data.telemetry ? data.telemetry.latitude : (geodetic.latitude != null ? geodetic.latitude : 0.0);
    const lon = data.telemetry ? data.telemetry.longitude : (geodetic.longitude != null ? geodetic.longitude : 0.0);
    const altKm = data.telemetry ? data.telemetry.altitude_km : (geodetic.altitude_km != null ? geodetic.altitude_km : 408.0);
    const velKmS = data.telemetry ? data.telemetry.velocity_magnitude_km_s : (geodetic.velocity_km_s != null ? geodetic.velocity_km_s : 7.66);
    const posEcef = data.telemetry ? data.telemetry.position_ecef_km : (data.position_ecef_km || [0, 0, 0]);

    const statusVal = (data.twin_state ? data.twin_state.status : data.twin_status) || "LIVE";
    const statusColor = (data.twin_state ? data.twin_state.badge_color : data.status_color) || "#10b981";
    const statusDesc = (data.twin_state ? data.twin_state.description : "") || "SGP4 physics corrected by live telemetry via Extended Kalman Filter.";

    const confPct = (data.fusion ? data.fusion.confidence_pct : data.ekf_confidence_pct) != null ? (data.fusion ? data.fusion.confidence_pct : data.ekf_confidence_pct) : 99.2;
    const sigmaKm = (data.fusion ? data.fusion.sigma_pos_km : data.ekf_sigma_pos_km) != null ? (data.fusion ? data.fusion.sigma_pos_km : data.ekf_sigma_pos_km) : 0.045;
    const confRadiusKm = (data.fusion ? data.fusion.confidence_zone_radius_km : data.ekf_uncertainty_radius_km) != null ? (data.fusion ? data.fusion.confidence_zone_radius_km : data.ekf_uncertainty_radius_km) : 0.15;
    const innovKm = (data.fusion ? data.fusion.innovation_residual_km : data.ekf_innovation_residual_km) != null ? (data.fusion ? data.fusion.innovation_residual_km : data.ekf_innovation_residual_km) : 0.012;

    const pwr = (data.subsystems ? data.subsystems.power : data.power) || {};
    const thm = (data.subsystems ? data.subsystems.thermal : data.thermal) || {};
    const ecl = (data.subsystems ? data.subsystems.eclipse : data.eclipse) || {};
    const comm = (data.subsystems ? data.subsystems.comms : data.comms) || {};
    const naive = (data.fault_injection ? data.fault_injection.naive_tracker : data.naive_position);
    const anomalies = data.anomalies || data.active_anomalies || [];
    const faults = data.fault_injection || { active_config: data.active_faults };

    // 1. Update Cesium Satellite Position
    const altM = altKm * 1000.0;
    const newPos = Cesium.Cartesian3.fromDegrees(lon, lat, altM);
    twinSatelliteEntity.position = newPos;

    // Center camera on satellite once at startup
    if (!hasInitialCameraCentered) {
        viewer.camera.flyTo({
            destination: Cesium.Cartesian3.fromDegrees(lon, lat, altM + 8500000.0),
            duration: 1.5
        });
        hasInitialCameraCentered = true;
    }

    // 2. Update Confidence Zone Ellipsoid Radius
    // Convert 3-sigma radius (km) to meters
    currentConfidenceRadiusMeters = confRadiusKm * 1000.0;

    // 3. Update Naive Tracker Ghost
    if (naive && showNaiveGhost) {
        const nLat = naive.latitude != null ? naive.latitude : lat;
        const nLon = naive.longitude != null ? naive.longitude : lon;
        const nAlt = (naive.altitude_km || 408.0) * 1000.0;
        naivePositionCartesian = Cesium.Cartesian3.fromDegrees(nLon, nLat, nAlt);
        naiveGhostEntity.show = true;
    } else {
        naiveGhostEntity.show = false;
    }

    // 4. Update Forecast Orbit Path
    if (data.forecast_track && data.forecast_track.length > 0) {
        const orbitPoints = data.forecast_track.map(pt =>
            Cesium.Cartesian3.fromDegrees(pt.longitude, pt.latitude, pt.altitude_km * 1000.0)
        );
        forecastOrbitEntity.polyline.positions = orbitPoints;

        const groundPoints = data.forecast_track.map(pt =>
            Cesium.Cartesian3.fromDegrees(pt.longitude, pt.latitude, 2000.0)
        );
        groundTrackEntity.polyline.positions = groundPoints;
    }

    // 5. Update Digital Twin State Badge
    const badge = $("twin-state-badge");
    const dot = $("state-dot");
    const lbl = $("state-label");
    if (badge && dot && lbl) {
        lbl.textContent = statusVal;
        dot.style.background = statusColor;
        dot.style.boxShadow = `0 0 10px ${statusColor}`;
        badge.style.color = statusColor;
        badge.style.borderColor = statusColor;
    }

    if ($("panel-state-dot")) $("panel-state-dot").style.background = statusColor;
    if ($("panel-state-text")) {
        $("panel-state-text").textContent = `${statusVal} · ${statusVal === "PREDICTED" ? "FREE-RUNNING SGP4" : "FUSED STATE"}`;
        $("panel-state-text").style.color = statusColor;
    }
    if ($("panel-state-desc")) $("panel-state-desc").textContent = statusDesc;
    if ($("bottom-status-text")) {
        $("bottom-status-text").textContent = statusVal;
        $("bottom-status-text").style.color = statusColor;
    }

    // 6. Update Left Panel Flight Readouts
    if ($("altitude")) $("altitude").textContent = altKm.toFixed(1);
    if ($("velocity")) $("velocity").textContent = `${velKmS.toFixed(3)} km/s`;
    if ($("latitude")) $("latitude").textContent = formatLat(lat);
    if ($("longitude")) $("longitude").textContent = formatLon(lon);

    // Kalman Uncertainty Metrics
    if ($("header-confidence")) $("header-confidence").textContent = `${confPct.toFixed(1)}%`;
    if ($("sigma-pos")) $("sigma-pos").textContent = `±${sigmaKm.toFixed(3)} km`;
    if ($("confidence-radius")) $("confidence-radius").textContent = `${confRadiusKm.toFixed(2)} km (3σ)`;
    if ($("bottom-confidence-zone")) $("bottom-confidence-zone").textContent = confRadiusKm.toFixed(2);
    if ($("innovation-residual")) $("innovation-residual").textContent = `${innovKm.toFixed(3)} km`;

    // 7. Update Subsystem Health Panel (Right Panel)
    if ($("solar-power")) $("solar-power").textContent = `${(pwr.solar_generation_kw || 0).toFixed(1)} kW`;
    if ($("battery-soc")) $("battery-soc").textContent = `${(pwr.battery_soc_pct || 90).toFixed(1)}%`;
    if ($("battery-bar")) $("battery-bar").style.width = `${Math.min(100, Math.max(5, pwr.battery_soc_pct || 90))}%`;
    if ($("bus-voltage")) $("bus-voltage").textContent = `${(pwr.bus_voltage_v || 120).toFixed(1)} V`;
    if ($("net-power")) {
        const net = pwr.net_power_kw || 0;
        $("net-power").textContent = `${net >= 0 ? '+' : ''}${net.toFixed(1)} kW`;
        $("net-power").style.color = net >= 0 ? "var(--green)" : "var(--amber)";
    }
    if ($("power-mode-badge")) {
        const mode = pwr.charge_mode || pwr.charge_state || "CHARGING";
        $("power-mode-badge").textContent = mode;
        $("power-mode-badge").style.color = mode === "DISCHARGING" ? "var(--amber)" : "var(--green)";
    }

    if ($("core-temp")) $("core-temp").textContent = `${(thm.core_temp_c || 21).toFixed(1)} °C`;
    if ($("array-temp")) $("array-temp").textContent = `${(thm.solar_array_temp_c || 40).toFixed(1)} °C`;
    if ($("beta-angle")) $("beta-angle").textContent = `${(ecl.beta_angle_deg || 0).toFixed(1)}°`;
    if ($("solar-illumination")) $("solar-illumination").textContent = `${Math.round((ecl.illumination_pct != null ? ecl.illumination_pct : (ecl.illumination_fraction || 1.0) * 100))}%`;

    if ($("eclipse-badge")) {
        const eclStatus = ecl.status || "SUNLIGHT";
        $("eclipse-badge").textContent = eclStatus === "UMBRA" ? "🌑 UMBRA" : (eclStatus === "PENUMBRA" ? "🌗 PENUMBRA" : "☀️ SUNLIGHT");
        $("eclipse-badge").style.color = eclStatus === "UMBRA" ? "var(--rose)" : (eclStatus === "PENUMBRA" ? "var(--amber)" : "var(--green)");
    }

    if ($("comms-latency")) $("comms-latency").textContent = `${Math.round(comm.latency_ms || 95)} ms`;
    if ($("pointing-stability")) $("pointing-stability").textContent = `${((comm.attitude_stability_score || 0.99) * 100).toFixed(1)}%`;

    // 8. Update Bottom Console Position Vectors
    if ($("pos-x")) $("pos-x").textContent = posEcef[0] != null ? posEcef[0].toFixed(1) : "0.0";
    if ($("pos-y")) $("pos-y").textContent = posEcef[1] != null ? posEcef[1].toFixed(1) : "0.0";
    if ($("pos-z")) $("pos-z").textContent = posEcef[2] != null ? posEcef[2].toFixed(1) : "0.0";

    // 9. Update Fault Status Bar
    updateFaultStatusBar(faults);

    // 10. Update Anomaly Stream
    if (anomalies && anomalies.length > 0) {
        renderAnomalyEvents(anomalies);
    }
}
        $("net-power").style.color = net >= 0 ? "var(--green)" : "var(--amber)";
    }
    if ($("power-mode-badge")) {
        const mode = pwr.charge_mode || "CHARGING";
        $("power-mode-badge").textContent = mode;
        $("power-mode-badge").style.color = mode === "DISCHARGING" ? "var(--amber)" : "var(--green)";
    }

    if ($("core-temp")) $("core-temp").textContent = `${(thm.core_temp_c || 21).toFixed(1)} °C`;
    if ($("array-temp")) $("array-temp").textContent = `${(thm.solar_array_temp_c || 40).toFixed(1)} °C`;
    if ($("beta-angle")) $("beta-angle").textContent = `${(ecl.beta_angle_deg || 0).toFixed(1)}°`;
    if ($("solar-illumination")) $("solar-illumination").textContent = `${Math.round((ecl.illumination_fraction || 1.0) * 100)}%`;

    if ($("eclipse-badge")) {
        const eclStatus = ecl.status || "SUNLIGHT";
        $("eclipse-badge").textContent = eclStatus === "UMBRA" ? "🌑 UMBRA" : (eclStatus === "PENUMBRA" ? "🌗 PENUMBRA" : "☀️ SUNLIGHT");
        $("eclipse-badge").style.color = eclStatus === "UMBRA" ? "var(--rose)" : (eclStatus === "PENUMBRA" ? "var(--amber)" : "var(--green)");
    }

    if ($("comms-latency")) $("comms-latency").textContent = `${Math.round(comm.latency_ms || 95)} ms`;
    if ($("pointing-stability")) $("pointing-stability").textContent = `${((comm.pointing_stability || 0.99) * 100).toFixed(1)}%`;

    // 8. Update Bottom Console Position Vectors
    const posEcef = telem.position_ecef_km || [0, 0, 0];
    if ($("pos-x")) $("pos-x").textContent = posEcef[0].toFixed(1);
    if ($("pos-y")) $("pos-y").textContent = posEcef[1].toFixed(1);
    if ($("pos-z")) $("pos-z").textContent = posEcef[2].toFixed(1);

    // 9. Update Fault Status Bar
    updateFaultStatusBar(faults);

    // 10. Update Anomaly Stream
    if (data.anomalies && data.anomalies.length > 0) {
        renderAnomalyEvents(data.anomalies);
    }
}

function formatLat(lat) {
    if (lat == null) return "00.00°";
    const dir = lat >= 0 ? "N" : "S";
    return `${Math.abs(lat).toFixed(4)}° ${dir}`;
}

function formatLon(lon) {
    if (lon == null) return "00.00°";
    const dir = lon >= 0 ? "E" : "W";
    return `${Math.abs(lon).toFixed(4)}° ${dir}`;
}

function updateFaultStatusBar(faults) {
    const bar = $("fault-active-summary");
    if (!bar) return;

    const activeCfg = faults.active_config || {};
    const messages = [];

    if (activeCfg.sever_feed) {
        messages.push("🚨 DATA FEED SEVERED: Free-running SGP4 orbit physics with expanding covariance zone.");
    }
    if (activeCfg.latency_delay_seconds > 0) {
        messages.push(`⏱ Simulated Link Delay: +${activeCfg.latency_delay_seconds}s.`);
    }
    if (activeCfg.inject_outlier_spikes) {
        messages.push("⚠️ Sensor Outliers Injected: MAD filter active & innovation gate filtering spikes.");
    }

    if (messages.length === 0) {
        bar.textContent = "Nominal Stream: Ingesting live orbital data via Kalman fusion.";
        bar.style.color = "var(--muted)";
    } else {
        bar.textContent = messages.join(" | ");
        bar.style.color = "var(--amber)";
    }
}

function renderAnomalyEvents(anomalies) {
    const container = $("events");
    if (!container) return;

    let html = "";
    anomalies.forEach((a) => {
        const timeStr = a.timestamp ? a.timestamp.slice(11, 19) : "NOW";
        html += `
            <div class="event anomaly-event">
                <time>${timeStr}</time>
                <span>[${a.subsystem}] ${a.message}</span>
            </div>
        `;
    });
    container.innerHTML = html;
}


// --------------------------------------------------------------------------
// 6. INTERACTIVE FAULT INJECTION CONTROLS (DEMO HARNESS)
// --------------------------------------------------------------------------
function sendFaultCommand(payload) {
    const normalized = {
        feed_cut: payload.feed_cut != null ? payload.feed_cut : payload.sever_feed,
        packet_latency_s: payload.packet_latency_s != null ? payload.packet_latency_s : payload.latency_delay_seconds,
        sensor_noise_km: payload.sensor_noise_km != null ? payload.sensor_noise_km : (payload.inject_outlier_spikes ? 50.0 : 0.0),
        compare_naive: payload.compare_naive != null ? payload.compare_naive : showNaiveGhost,
    };

    fetch("/api/faults/inject", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(normalized)
    }).catch(err => console.error("Fault API error:", err));

    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ command: "SET_FAULT", ...normalized }));
    }
}

window.toggleSeverFeed = function () {
    isSeverActive = !isSeverActive;
    const btn = $("btn-sever-feed");
    if (btn) btn.classList.toggle("btn-active", isSeverActive);
    sendFaultCommand({ feed_cut: isSeverActive });
};

window.togglePacketDelay = function () {
    isDelayActive = !isDelayActive;
    const delay = isDelayActive ? 4.0 : 0.0;
    const btn = $("btn-inject-delay");
    if (btn) btn.classList.toggle("btn-active", isDelayActive);
    sendFaultCommand({ packet_latency_s: delay });
};

window.toggleOutlierSpikes = function () {
    isOutliersActive = !isOutliersActive;
    const btn = $("btn-inject-outliers");
    if (btn) btn.classList.toggle("btn-active", isOutliersActive);
    sendFaultCommand({ sensor_noise_km: isOutliersActive ? 50.0 : 0.0 });
};

window.toggleNaiveGhost = function () {
    showNaiveGhost = !showNaiveGhost;
    const btnText = $("ghost-btn-text");
    if (btnText) btnText.textContent = `Naive Ghost: ${showNaiveGhost ? "ON" : "OFF"}`;
    naiveGhostEntity.show = showNaiveGhost;
    sendFaultCommand({ compare_naive: showNaiveGhost });
};

window.resetAllFaults = function () {
    isSeverActive = false;
    isDelayActive = false;
    isOutliersActive = false;
    if ($("btn-sever-feed")) $("btn-sever-feed").classList.remove("btn-active");
    if ($("btn-inject-delay")) $("btn-inject-delay").classList.remove("btn-active");
    if ($("btn-inject-outliers")) $("btn-inject-outliers").classList.remove("btn-active");

    fetch("/api/faults/reset", { method: "POST" }).catch(e => console.error(e));
    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ command: "RESET_FAULTS" }));
    }
};


// --------------------------------------------------------------------------
// 7. SATELLITE TARGET SELECTOR
// --------------------------------------------------------------------------
const selector = $("satellite-selector");
if (selector) {
    selector.addEventListener("change", (e) => {
        const noradId = parseInt(e.target.value, 10);
        const satName = e.target.options[e.target.selectedIndex].text;
        
        if ($("target-name-display")) $("target-name-display").textContent = `LOW EARTH ORBIT · ${satName}`;
        if ($("vehicle-code-label")) $("vehicle-code-label").textContent = satName.split("·")[0].trim();
        if ($("satellite-norad-badge")) $("satellite-norad-badge").textContent = `NORAD ${noradId}`;

        fetch(`/api/select-satellite/${noradId}`, { method: "POST" })
            .then(res => res.json())
            .then(res => console.info("Switched satellite:", res))
            .catch(err => console.error("Error switching satellite:", err));
    });
}
