/* ==========================================================================
   ORBITAL — SATELLITE DIGITAL TWIN (Cyber-Physical Kalman HMI)
   Tragic Bytes · Space Tech & Data Systems
   
   Combines:
   - Photorealistic 3D Earth globe with procedural cloud layers & atmospheric glow
   - Full 360-degree glowing orbital trajectory ring around Earth
   - 3D ISS physical structural schematic model & interactive follow mode ('F' key)
   - Live 1 Hz WebSocket telemetry streaming from Python SGP4 + EKF Fusion Engine
   - Real-time satellite geodetic positioning and coordinate transformation
   - Dynamic 3D Kalman Covariance Confidence Zone (visibly widens on data drop)
   - Interactive Fault-Injection Sandbox & Naive Tracker Comparison
========================================================================== */

"use strict";

const $ = (id) => document.getElementById(id);

// --------------------------------------------------------------------------
// 1. UTC CLOCK & EVENT STREAM
// --------------------------------------------------------------------------
function updateClock() {
    const el = $("utc-clock");
    if (el) {
        el.textContent = new Date().toISOString().slice(11, 19) + " UTC";
    }
}
updateClock();
setInterval(updateClock, 1000);

function addEvent(message) {
    const container = $("events");
    if (!container) return;
    const item = document.createElement("div");
    item.className = "event";
    const time = new Date().toISOString().slice(11, 19);
    const timeElement = document.createElement("time");
    timeElement.textContent = time;
    const messageElement = document.createElement("span");
    messageElement.textContent = message;
    item.append(timeElement, messageElement);
    container.prepend(item);
    while (container.children.length > 5) {
        container.removeChild(container.lastChild);
    }
}


// --------------------------------------------------------------------------
// 2. CESIUM INITIALIZATION & CINEMATIC EARTH CONFIGURATION
// --------------------------------------------------------------------------
const viewer = new Cesium.Viewer("cesiumContainer", {
    animation: false,
    timeline: false,
    baseLayerPicker: false,
    geocoder: false,
    homeButton: true,
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

// Earth Atmosphere & Cinematic Space Lighting
viewer.scene.globe.enableLighting = true;
viewer.scene.globe.showGroundAtmosphere = true;
viewer.scene.globe.baseColor = Cesium.Color.fromCssColorString("#071321");
viewer.scene.skyAtmosphere.show = true;
viewer.scene.skyAtmosphere.atmosphereLightIntensity = 12.0;
viewer.scene.skyAtmosphere.hueShift = -0.04;
viewer.scene.skyAtmosphere.saturationShift = 0.15;
viewer.scene.skyAtmosphere.brightnessShift = 0.08;
viewer.scene.backgroundColor = Cesium.Color.fromCssColorString("#02040a");
viewer.scene.globe.maximumScreenSpaceError = 1.5;


// --------------------------------------------------------------------------
// 3. PROCEDURAL EARTH CLOUD LAYER WITH DYNAMIC DRIFT
// --------------------------------------------------------------------------
function createCloudTexture() {
    const width = 1024;
    const height = 512;
    const canvas = document.createElement("canvas");
    canvas.width = width;
    canvas.height = height;
    const ctx = canvas.getContext("2d");
    const image = ctx.createImageData(width, height);

    function hash(x, y, seed) {
        const n = Math.sin(x * 127.1 + y * 311.7 + seed * 74.7) * 43758.5453;
        return n - Math.floor(n);
    }
    function smooth(t) { return t * t * (3 - 2 * t); }
    function noise(u, v, scaleX, scaleY, seed) {
        const gx = u * scaleX, gy = v * scaleY;
        const x0 = Math.floor(gx), y0 = Math.floor(gy);
        const fx = smooth(gx - x0), fy = smooth(gy - y0);
        const wrapX = (x) => ((x % scaleX) + scaleX) % scaleX;
        const a = hash(wrapX(x0), y0, seed);
        const b = hash(wrapX(x0 + 1), y0, seed);
        const c = hash(wrapX(x0), y0 + 1, seed);
        const d = hash(wrapX(x0 + 1), y0 + 1, seed);
        const top = a + (b - a) * fx;
        const bottom = c + (d - c) * fx;
        return top + (bottom - top) * fy;
    }

    for (let y = 0; y < height; y++) {
        for (let x = 0; x < width; x++) {
            const u = x / width;
            const v = y / height;
            let cloud = 0;
            cloud += noise(u, v, 8, 4, 1) * 0.45;
            cloud += noise(u, v, 16, 8, 2) * 0.30;
            cloud += noise(u, v, 32, 16, 3) * 0.17;
            cloud += noise(u, v, 64, 32, 4) * 0.08;

            let alpha = Math.max(0, Math.min(1, (cloud - 0.48) / 0.14));
            alpha = Math.pow(alpha, 1.5) * 0.95;
            const index = (y * width + x) * 4;
            image.data[index] = 245;
            image.data[index + 1] = 249;
            image.data[index + 2] = 255;
            image.data[index + 3] = Math.round(alpha * 255);
        }
    }
    ctx.putImageData(image, 0, 0);
    return canvas;
}

function createShiftedCloudTexture(sourceCanvas, shiftX) {
    const canvas = document.createElement("canvas");
    canvas.width = sourceCanvas.width;
    canvas.height = sourceCanvas.height;
    const ctx = canvas.getContext("2d");
    const width = canvas.width;
    const shift = ((shiftX % width) + width) % width;
    ctx.drawImage(sourceCanvas, 0, 0, width, canvas.height, shift, 0, width, canvas.height);
    ctx.drawImage(sourceCanvas, 0, 0, width, canvas.height, shift - width, 0, width, canvas.height);
    return canvas;
}

const cloudCanvas = createCloudTexture();
let cloudLayer = null;
let cloudShift = 0;
const CLOUD_ALPHA = 0.90;
const CLOUD_UPDATE_INTERVAL = 30000;
const CLOUD_CROSSFADE_DURATION = 5000;

function createCloudLayer(canvas) {
    const imageUrl = canvas.toDataURL("image/png");
    const provider = new Cesium.SingleTileImageryProvider({
        url: imageUrl,
        rectangle: Cesium.Rectangle.MAX_VALUE
    });
    const layer = viewer.imageryLayers.addImageryProvider(provider);
    layer.alpha = 0;
    layer.brightness = 1.15;
    layer.contrast = 1.25;
    return { layer, provider };
}

function crossfadeCloudLayers(oldLayer, newLayer) {
    const startTime = performance.now();
    function animateClouds(now) {
        const progress = Math.min((now - startTime) / CLOUD_CROSSFADE_DURATION, 1);
        newLayer.alpha = CLOUD_ALPHA * progress;
        if (oldLayer) oldLayer.alpha = CLOUD_ALPHA * (1 - progress);
        if (progress < 1) {
            requestAnimationFrame(animateClouds);
        } else {
            if (oldLayer) viewer.imageryLayers.remove(oldLayer);
            cloudLayer = newLayer;
        }
    }
    requestAnimationFrame(animateClouds);
}

const initialCloud = createCloudLayer(cloudCanvas);
cloudLayer = initialCloud.layer;
initialCloud.layer.alpha = CLOUD_ALPHA;

let cloudAnimationBusy = false;
setInterval(() => {
    if (cloudAnimationBusy) return;
    cloudAnimationBusy = true;
    cloudShift += 1;
    const shiftedCanvas = createShiftedCloudTexture(cloudCanvas, cloudShift);
    const nextCloud = createCloudLayer(shiftedCanvas);
    nextCloud.provider.readyPromise.then(() => {
        const previousLayer = cloudLayer;
        crossfadeCloudLayers(previousLayer, nextCloud.layer);
        setTimeout(() => { cloudAnimationBusy = false; }, CLOUD_CROSSFADE_DURATION);
    }).catch(() => {
        viewer.imageryLayers.remove(nextCloud.layer);
        cloudAnimationBusy = false;
    });
}, CLOUD_UPDATE_INTERVAL);


// --------------------------------------------------------------------------
// 4. FULL GLOWING ORBITAL TRAJECTORY RING
// --------------------------------------------------------------------------
function createFullOrbitPath(inclinationDeg = 51.64, altKm = 419.0) {
    const positions = [];
    const samples = 240;
    const r = (6371.0 + altKm) * 1000.0;
    const inc = Cesium.Math.toRadians(inclinationDeg);
    for (let i = 0; i <= samples; i++) {
        const angle = (i / samples) * 2 * Math.PI;
        const x = r * Math.cos(angle);
        const y = r * Math.sin(angle) * Math.cos(inc);
        const z = r * Math.sin(angle) * Math.sin(inc);
        positions.push(new Cesium.Cartesian3(x, y, z));
    }
    return positions;
}

const orbitEntity = viewer.entities.add({
    name: "Orbital Trajectory Track",
    polyline: {
        positions: createFullOrbitPath(51.64, 419.0),
        width: 3,
        arcType: Cesium.ArcType.NONE,
        material: new Cesium.PolylineGlowMaterialProperty({
            glowPower: 0.22,
            color: Cesium.Color.fromCssColorString("#48cfff")
        })
    }
});

const groundTrackEntity = viewer.entities.add({
    name: "Sub-Satellite Ground Track",
    polyline: {
        positions: [],
        width: 2,
        arcType: Cesium.ArcType.NONE,
        material: new Cesium.PolylineDashMaterialProperty({
            color: Cesium.Color.fromCssColorString("#f3bd67"),
            dashLength: 14
        })
    }
});


// --------------------------------------------------------------------------
// 5. 3D ENTITIES: SATELLITE MARKER, KALMAN ELLIPSOID & NAIVE GHOST
// --------------------------------------------------------------------------
const satelliteEntity = viewer.entities.add({
    name: "ISS // 25544 (DIGITAL TWIN)",
    position: Cesium.Cartesian3.fromDegrees(0, 0, 419000),
    point: {
        pixelSize: 15,
        color: Cesium.Color.WHITE,
        outlineColor: Cesium.Color.fromCssColorString("#54d6ff"),
        outlineWidth: 4,
        scaleByDistance: new Cesium.NearFarScalar(1000000, 1.8, 30000000, 0.9)
    },
    label: {
        text: "ISS // 25544",
        font: "12px monospace",
        fillColor: Cesium.Color.WHITE,
        outlineColor: Cesium.Color.BLACK,
        outlineWidth: 3,
        style: Cesium.LabelStyle.FILL_AND_OUTLINE,
        pixelOffset: new Cesium.Cartesian2(16, -12),
        showBackground: true,
        backgroundColor: Cesium.Color.BLACK.withAlpha(0.75),
        scaleByDistance: new Cesium.NearFarScalar(1000000, 1.0, 30000000, 0.5)
    }
});

// Dynamic Kalman 3-Sigma Covariance Ellipsoid (expands visibly on feed loss)
let currentConfidenceRadiusMeters = 300.0;
const confidenceZoneEntity = viewer.entities.add({
    name: "Kalman 3-Sigma Confidence Ellipsoid",
    position: new Cesium.CallbackProperty(() => satelliteEntity.position.getValue(viewer.clock.currentTime), false),
    ellipsoid: {
        radii: new Cesium.CallbackProperty(() => {
            const r = Math.max(1200.0, currentConfidenceRadiusMeters);
            return new Cesium.Cartesian3(r, r, r);
        }, false),
        material: Cesium.Color.fromCssColorString("#06b6d4").withAlpha(0.24),
        outline: true,
        outlineColor: Cesium.Color.fromCssColorString("#58d5f5").withAlpha(0.75),
    }
});

// Naive Tracker Ghost (Amber Dot that freezes when severed, demonstrating Twin superiority)
let showNaiveGhost = true;
let naivePositionCartesian = null;
let isSeverActive = false;
let isDelayActive = false;
let isOutliersActive = false;

const naiveGhostEntity = viewer.entities.add({
    name: "Naive Tracker (Baseline Comparison)",
    show: showNaiveGhost,
    position: new Cesium.CallbackProperty(() => naivePositionCartesian || satelliteEntity.position.getValue(viewer.clock.currentTime), false),
    point: {
        pixelSize: 11,
        color: Cesium.Color.fromCssColorString("#f59e0b"),
        outlineColor: Cesium.Color.fromCssColorString("#78350f"),
        outlineWidth: 2,
    },
    label: {
        text: "NAIVE TRACKER (FROZEN)",
        font: "10px monospace",
        fillColor: Cesium.Color.fromCssColorString("#fde68a"),
        outlineColor: Cesium.Color.BLACK,
        outlineWidth: 2,
        verticalOrigin: Cesium.VerticalOrigin.TOP,
        pixelOffset: new Cesium.Cartesian2(0, 14),
        show: new Cesium.CallbackProperty(() => isSeverActive, false)
    }
});


// --------------------------------------------------------------------------
// 6. 3D ISS STRUCTURAL SCHEMATIC COMPONENTS
// --------------------------------------------------------------------------
const issParts = [];
const issMaterials = {
    truss: Cesium.Color.fromCssColorString("#aebbc7"),
    module: Cesium.Color.fromCssColorString("#dce5eb"),
    solar: Cesium.Color.fromCssColorString("#123c78"),
    radiator: Cesium.Color.fromCssColorString("#d5dce3"),
    detail: Cesium.Color.fromCssColorString("#59d6ff")
};
const ISS_VISUAL_SCALE = 8;

function addISSPart(name, offset, dimensions, material) {
    const scaledOffset = {
        x: offset.x * ISS_VISUAL_SCALE,
        y: offset.y * ISS_VISUAL_SCALE,
        z: offset.z * ISS_VISUAL_SCALE
    };
    const scaledDimensions = {
        x: dimensions.x * ISS_VISUAL_SCALE,
        y: dimensions.y * ISS_VISUAL_SCALE,
        z: dimensions.z * ISS_VISUAL_SCALE
    };
    const part = viewer.entities.add({
        name,
        position: new Cesium.CallbackProperty(() => {
            const basePosition = satelliteEntity.position.getValue(Cesium.JulianDate.now());
            if (!basePosition) return undefined;
            const frame = Cesium.Transforms.eastNorthUpToFixedFrame(basePosition);
            return Cesium.Matrix4.multiplyByPoint(
                frame,
                new Cesium.Cartesian3(scaledOffset.x, scaledOffset.y, scaledOffset.z),
                new Cesium.Cartesian3()
            );
        }, false),
        orientation: new Cesium.CallbackProperty(() => {
            const basePosition = satelliteEntity.position.getValue(Cesium.JulianDate.now());
            if (!basePosition) return undefined;
            const frame = Cesium.Transforms.eastNorthUpToFixedFrame(basePosition);
            const rotation = Cesium.Matrix4.getMatrix3(frame, new Cesium.Matrix3());
            return Cesium.Quaternion.fromRotationMatrix(rotation);
        }, false),
        box: {
            dimensions: new Cesium.Cartesian3(scaledDimensions.x, scaledDimensions.y, scaledDimensions.z),
            material: material,
            outline: true,
            outlineColor: Cesium.Color.fromCssColorString("#72cfff").withAlpha(0.7)
        }
    });
    issParts.push(part);
    return part;
}

addISSPart("ISS Main Truss", { x: 0, y: 0, z: 0 }, { x: 75, y: 3, z: 3 }, issMaterials.truss);
addISSPart("ISS Central Module", { x: 0, y: 0, z: 6 }, { x: 15, y: 9, z: 9 }, issMaterials.module);
addISSPart("ISS Forward Module", { x: 17, y: 0, z: 0 }, { x: 13, y: 8, z: 8 }, issMaterials.module);
addISSPart("ISS Research Module", { x: -17, y: 0, z: 0 }, { x: 12, y: 8, z: 8 }, issMaterials.module);


// --------------------------------------------------------------------------
// 7. CAMERA SPACE OVERVIEW & FOLLOW MODE ('F' Key)
// --------------------------------------------------------------------------
let followISS = false;

function toggleISSFollow(enabled) {
    followISS = enabled;
    satelliteEntity.point.show = !enabled;
    if (enabled) {
        const position = satelliteEntity.position.getValue(Cesium.JulianDate.now());
        if (position) {
            viewer.camera.lookAt(
                position,
                new Cesium.HeadingPitchRange(0, Cesium.Math.toRadians(-15), 6000)
            );
        }
        addEvent("ISS close-up inspection camera enabled");
    } else {
        viewer.camera.lookAtTransform(Cesium.Matrix4.IDENTITY);
        viewer.camera.flyTo({
            destination: Cesium.Cartesian3.fromDegrees(0, 15, 28000000),
            duration: 2
        });
        addEvent("Global Earth orbital view restored");
    }
}

window.addEventListener("keydown", (event) => {
    if (event.target instanceof HTMLInputElement || event.target instanceof HTMLTextAreaElement) return;
    if (event.key.toLowerCase() === "f") toggleISSFollow(!followISS);
    if (event.key === "Escape" && followISS) toggleISSFollow(false);
});

// Set Cinematic Space Earth Overview at Startup
viewer.camera.flyTo({
    destination: Cesium.Cartesian3.fromDegrees(0, 15, 28000000),
    orientation: {
        heading: 0,
        pitch: Cesium.Math.toRadians(-90),
        roll: 0
    },
    duration: 3
});


// --------------------------------------------------------------------------
// 8. WEBSOCKET REAL-TIME TELEMETRY INTEGRATION (WITH SERVERLESS POLLING FALLBACK)
// --------------------------------------------------------------------------
let ws = null;
let reconnectTimer = null;
let wsFailCount = 0;
let useHttpPolling = false;
let pollingTimer = null;
let recentPositionsHistory = [];

function startHttpPolling() {
    if (useHttpPolling) return;
    useHttpPolling = true;
    console.info("[Digital Twin] Serverless/Vercel mode: Fallback to 1 Hz HTTP telemetry stream");
    const badge = $("stream-connection-badge");
    if (badge) {
        badge.innerHTML = "<i></i> HTTP DOWNLINK (1 Hz)";
        badge.style.color = "var(--green)";
    }
    addEvent("HTTP 1 Hz Telemetry polling active");

    async function poll() {
        try {
            const res = await fetch("/api/telemetry/snapshot");
            if (res.ok) {
                const data = await res.json();
                handleTelemetryPacket(data);
            }
        } catch (err) {
            console.warn("[Digital Twin] Telemetry poll failed:", err);
        }
        if (useHttpPolling) {
            pollingTimer = setTimeout(poll, 1000);
        }
    }
    poll();
}

function stopHttpPolling() {
    useHttpPolling = false;
    if (pollingTimer) {
        clearTimeout(pollingTimer);
        pollingTimer = null;
    }
}

function connectWebSocket() {
    if (ws) {
        try { ws.close(); } catch (e) {}
    }
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const wsUrl = `${protocol}//${window.location.host}/ws/telemetry`;
    console.info("[Digital Twin] Connecting to WebSocket:", wsUrl);

    try {
        ws = new WebSocket(wsUrl);
    } catch (e) {
        startHttpPolling();
        return;
    }

    ws.onopen = () => {
        console.info("[Digital Twin] WebSocket connected successfully!");
        wsFailCount = 0;
        stopHttpPolling();
        const badge = $("stream-connection-badge");
        if (badge) {
            badge.innerHTML = "<i></i> WEBSOCKET CONNECTED";
            badge.style.color = "var(--green)";
        }
        addEvent("WebSocket telemetry downlink acquired");
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
        wsFailCount += 1;
        if (wsFailCount >= 2) {
            // Serverless platform (like Vercel) does not support persistent WebSockets
            startHttpPolling();
        } else {
            console.warn("[Digital Twin] WebSocket disconnected. Reconnecting...");
            const badge = $("stream-connection-badge");
            if (badge && !useHttpPolling) {
                badge.innerHTML = "<i></i> RECONNECTING...";
                badge.style.color = "var(--amber)";
            }
            reconnectTimer = setTimeout(connectWebSocket, 2000);
        }
    };

    ws.onerror = (err) => {
        console.error("[Digital Twin] WebSocket error:", err);
        wsFailCount += 1;
        if (wsFailCount >= 2) {
            startHttpPolling();
        }
    };
}
connectWebSocket();


// --------------------------------------------------------------------------
// 9. TELEMETRY PACKET HANDLER & HMI UPDATES
// --------------------------------------------------------------------------
function handleTelemetryPacket(data) {
    if (!data) return;

    const geodetic = data.position_geodetic || {};
    const lat = geodetic.latitude != null ? geodetic.latitude : 0.0;
    const lon = geodetic.longitude != null ? geodetic.longitude : 0.0;
    const altKm = geodetic.altitude_km != null ? geodetic.altitude_km : 419.0;
    const velKmS = geodetic.velocity_km_s != null ? geodetic.velocity_km_s : 7.66;
    const posEcef = data.position_ecef_km || [0, 0, 0];

    const statusVal = data.twin_status || "LIVE";
    const statusColor = data.status_color || "#10b981";

    const confPct = data.ekf_confidence_pct != null ? data.ekf_confidence_pct : 98.6;
    const sigmaKm = data.ekf_sigma_pos_km != null ? data.ekf_sigma_pos_km : 0.086;
    const confRadiusKm = data.ekf_uncertainty_radius_km != null ? data.ekf_uncertainty_radius_km : 0.258;
    const innovKm = data.ekf_innovation_residual_km != null ? data.ekf_innovation_residual_km : 0.068;

    const pwr = data.power || {};
    const thm = data.thermal || {};
    const ecl = data.eclipse || {};
    const comm = data.comms || {};
    const naive = data.naive_position;
    const anomalies = data.active_anomalies || [];

    // 1. Update Cesium Satellite Position
    const altM = altKm * 1000.0;
    const newPos = Cesium.Cartesian3.fromDegrees(lon, lat, altM);
    satelliteEntity.position = newPos;

    // 2. Update Ground Track Trail
    recentPositionsHistory.push(Cesium.Cartesian3.fromDegrees(lon, lat, 1000.0));
    if (recentPositionsHistory.length > 180) {
        recentPositionsHistory.shift();
    }
    groundTrackEntity.polyline.positions = recentPositionsHistory;

    // 3. Update Confidence Ellipsoid Radius
    currentConfidenceRadiusMeters = confRadiusKm * 1000.0;

    // 4. Update Naive Tracker Comparison Marker
    if (naive && showNaiveGhost) {
        const nLat = naive.latitude != null ? naive.latitude : lat;
        const nLon = naive.longitude != null ? naive.longitude : lon;
        const nAlt = (naive.altitude_km || altKm) * 1000.0;
        naivePositionCartesian = Cesium.Cartesian3.fromDegrees(nLon, nLat, nAlt);
        naiveGhostEntity.show = true;
    } else {
        naiveGhostEntity.show = false;
    }

    // 5. Update Digital Twin State Badge & Header
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
    if ($("header-confidence")) $("header-confidence").textContent = `${confPct.toFixed(1)}%`;

    if ($("panel-state-dot")) $("panel-state-dot").style.background = statusColor;
    if ($("panel-state-text")) {
        $("panel-state-text").textContent = `${statusVal} · ${statusVal === "PREDICTED" ? "FREE-RUNNING SGP4" : "EKF FUSED"}`;
        $("panel-state-text").style.color = statusColor;
    }
    if ($("panel-confidence-badge")) $("panel-confidence-badge").textContent = `${confPct.toFixed(1)}% CONF`;

    // 6. Update Flight Metrics Cards
    if ($("altitude")) $("altitude").textContent = altKm.toFixed(2);
    if ($("velocity")) $("velocity").textContent = `${velKmS.toFixed(3)} km/s`;
    if ($("latitude")) $("latitude").textContent = `${lat.toFixed(2)}°`;
    if ($("longitude")) $("longitude").textContent = `${lon.toFixed(2)}°`;

    // 7. Update Extended EKF Uncertainty Telemetry
    if ($("ekf-sigma")) $("ekf-sigma").textContent = `±${sigmaKm.toFixed(3)} km`;
    if ($("ekf-uncertainty-radius")) $("ekf-uncertainty-radius").textContent = `${confRadiusKm.toFixed(2)} km`;
    if ($("ekf-residual")) $("ekf-residual").textContent = `${innovKm.toFixed(3)} km`;

    // 8. Update Subsystem Health Readouts
    if ($("pwr-generation")) $("pwr-generation").textContent = `${(pwr.solar_generation_kw || 0).toFixed(1)} kW`;
    if ($("pwr-soc")) $("pwr-soc").textContent = `${(pwr.battery_soc_pct || 0).toFixed(1)}%`;
    if ($("pwr-voltage")) $("pwr-voltage").textContent = `${(pwr.bus_voltage_v || 120.0).toFixed(1)} V`;

    if ($("thm-core")) $("thm-core").textContent = `${(thm.core_temp_c || 21.0).toFixed(1)} °C`;
    if ($("thm-array")) $("thm-array").textContent = `${(thm.solar_array_temp_c || 35.0).toFixed(1)} °C`;
    if ($("thm-status")) $("thm-status").textContent = thm.status || "NOMINAL";

    if ($("ecl-status")) {
        const statusStr = ecl.status || "SUNLIGHT";
        $("ecl-status").textContent = statusStr;
        $("ecl-status").style.color = statusStr === "SUNLIGHT" ? "var(--green)" : "var(--amber)";
    }
    if ($("ecl-illumination")) $("ecl-illumination").textContent = `${(ecl.illumination_pct || 100.0).toFixed(0)}%`;

    if ($("comms-latency")) $("comms-latency").textContent = `${(comm.latency_ms || 108.0).toFixed(0)} ms`;
    if ($("adcs-score")) $("adcs-score").textContent = `${((comm.attitude_stability_score || 0.99) * 100.0).toFixed(1)}%`;

    // 9. Update Bottom Vector Bar
    if ($("pos-x")) $("pos-x").textContent = (posEcef[0] || 0).toFixed(2);
    if ($("pos-y")) $("pos-y").textContent = (posEcef[1] || 0).toFixed(2);
    if ($("pos-z")) $("pos-z").textContent = (posEcef[2] || 0).toFixed(2);

    // 10. Update Anomaly Stream
    if (anomalies && anomalies.length > 0) {
        anomalies.forEach(a => {
            addEvent(`[${a.subsystem}] ${a.message}`);
        });
    }
}


// --------------------------------------------------------------------------
// 10. FAULT INJECTION CONTROLS (DEMO HARNESS)
// --------------------------------------------------------------------------
function sendFaultCommand(payload) {
    const normalized = {
        feed_cut: payload.feed_cut != null ? payload.feed_cut : isSeverActive,
        packet_latency_s: payload.packet_latency_s != null ? payload.packet_latency_s : (isDelayActive ? 4.0 : 0.0),
        sensor_noise_km: payload.sensor_noise_km != null ? payload.sensor_noise_km : (isOutliersActive ? 50.0 : 0.0),
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
    addEvent(isSeverActive ? "Telemetry feed severed! EKF dead-reckoning engaged." : "Telemetry feed restored. EKF re-converging.");
};

window.togglePacketDelay = function () {
    isDelayActive = !isDelayActive;
    const delay = isDelayActive ? 4.0 : 0.0;
    const btn = $("btn-inject-delay");
    if (btn) btn.classList.toggle("btn-active", isDelayActive);
    sendFaultCommand({ packet_latency_s: delay });
    addEvent(isDelayActive ? "Simulating +4.0s packet latency delay" : "Latency delay cleared");
};

window.toggleOutlierSpikes = function () {
    isOutliersActive = !isOutliersActive;
    const btn = $("btn-inject-outliers");
    if (btn) btn.classList.toggle("btn-active", isOutliersActive);
    sendFaultCommand({ sensor_noise_km: isOutliersActive ? 50.0 : 0.0 });
    addEvent(isOutliersActive ? "Injecting rogue sensor outliers (MAD gate active)" : "Outlier injection stopped");
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
    addEvent("All fault injections cleared. Nominal operations restored.");
};

// Target Selector
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
            .then(res => {
                console.info("Switched satellite:", res);
                addEvent(`Target switched to ${satName}`);
            })
            .catch(err => console.error("Error switching satellite:", err));
    });
}

// User click on globe
viewer.screenSpaceEventHandler.setInputAction(() => {
    addEvent("Globe interaction detected");
}, Cesium.ScreenSpaceEventType.LEFT_CLICK);

console.info("ORBITAL Cyber-Physical Digital Twin HMI fully initialized.");
