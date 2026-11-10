/* ==========================================================================
   ORBITAL — SATELLITE DIGITAL TWIN (Cyber-Physical Kalman HMI)
   Tragic Bytes · Space Tech & Data Systems
   ========================================================================== */

"use strict";

const $ = (id) => document.getElementById(id);

// --------------------------------------------------------------------------
// 1. STATE & FAULT TRACKING (SYNCED FOR BOTH WEBSOCKET AND SERVERLESS VERCEL)
// --------------------------------------------------------------------------
let activeNoradId = 25544;
let activeSatLabel = "ISS (ZARYA)";
let lastRenderedTrackNorad = null;

let isSeverActive = false;
let severStartTimeMs = null;
let isDelayActive = false;
let isOutliersActive = false;
let showNaiveGhost = true;

let currentLat = 0.0;
let currentLon = 0.0;
let currentAltKm = 418.0;
let currentTwinStatus = "LIVE";
let currentConfidenceRadiusKm = 0.25;
let currentConfidenceRadiusMeters = 80000.0;

let recentPositionsHistory = [];
let naivePositionCartesian = null;
let delayedPositionCartesian = null;
let delayedPosVisible = false;
let delayedLagLabel = "LAGGED (-4.0s)";
let naiveDriftKm = 0.0;

let rejectedSpikeCartesian = null;
let rejectedSpikeLabel = "";
let rejectedSpikeExpiresAt = 0;

let lastLoggedState = null;
let lastLoggedAnomalyKey = null;
let lastLoggedSpikeMs = 0;
let recoveryFlashUntil = 0;

// --------------------------------------------------------------------------
// 2. UTC CLOCK & DEDUPLICATED EVENT STREAM
// --------------------------------------------------------------------------
function updateClock() {
    const el = $("utc-clock");
    if (el) {
        el.textContent = new Date().toISOString().slice(11, 19) + " UTC";
    }
}
updateClock();
setInterval(updateClock, 1000);

function addEvent(message, isAnomaly = false) {
    const container = $("events");
    if (!container) return;
    const item = document.createElement("div");
    item.className = isAnomaly ? "event anomaly-event" : "event";
    const time = new Date().toISOString().slice(11, 19);
    const timeEl = document.createElement("time");
    timeEl.textContent = time;
    const msgEl = document.createElement("span");
    msgEl.textContent = message;
    item.append(timeEl, msgEl);
    container.prepend(item);
    while (container.children.length > 6) {
        container.removeChild(container.lastChild);
    }
}

// --------------------------------------------------------------------------
// 3. CESIUM INITIALIZATION, CLOUDS, ATMOSPHERE & HOME BUTTON OVERRIDE
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

const earthImagery = new Cesium.UrlTemplateImageryProvider({
    url: "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    credit: "Esri, Maxar, Earthstar Geographics"
});
const earthLayer = viewer.imageryLayers.addImageryProvider(earthImagery);
earthLayer.brightness = 0.94;
earthLayer.contrast = 1.08;
earthLayer.saturation = 1.15;
earthLayer.gamma = 0.96;

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

// Intercept Cesium Home Button so it cleanly flies back to global Earth overview
if (viewer.homeButton && viewer.homeButton.viewModel) {
    viewer.homeButton.viewModel.command.beforeExecute.addEventListener((e) => {
        e.cancel = true;
        if (followISS) {
            toggleISSFollow(false);
        } else {
            viewer.camera.lookAtTransform(Cesium.Matrix4.IDENTITY);
            viewer.camera.flyTo({
                destination: Cesium.Cartesian3.fromDegrees(currentLon || 0, Math.max(-45, Math.min(45, currentLat * 0.4)), 23500000),
                orientation: { heading: 0, pitch: Cesium.Math.toRadians(-90), roll: 0 },
                duration: 1.8
            });
        }
        addEvent("Reset camera to Global Orbital Overview");
    });
}

// Procedural animated cloud layer
function createCloudTexture() {
    const width = 1024, height = 512;
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
            const u = x / width, v = y / height;
            let cloud = 0;
            cloud += noise(u, v, 8, 4, 1) * 0.45;
            cloud += noise(u, v, 16, 8, 2) * 0.30;
            cloud += noise(u, v, 32, 16, 3) * 0.17;
            cloud += noise(u, v, 64, 32, 4) * 0.08;
            let alpha = Math.max(0, Math.min(1, (cloud - 0.48) / 0.14));
            alpha = Math.pow(alpha, 1.5) * 0.90;
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
const CLOUD_ALPHA = 0.85;

function createCloudLayer(canvas) {
    const provider = new Cesium.SingleTileImageryProvider({
        url: canvas.toDataURL("image/png"),
        rectangle: Cesium.Rectangle.MAX_VALUE
    });
    const layer = viewer.imageryLayers.addImageryProvider(provider);
    layer.alpha = 0;
    layer.brightness = 1.15;
    layer.contrast = 1.25;
    return { layer, provider };
}

const initialCloud = createCloudLayer(cloudCanvas);
cloudLayer = initialCloud.layer;
initialCloud.layer.alpha = CLOUD_ALPHA;

let cloudAnimationBusy = false;
setInterval(() => {
    if (cloudAnimationBusy) return;
    cloudAnimationBusy = true;
    cloudShift += 1;
    const shifted = createShiftedCloudTexture(cloudCanvas, cloudShift);
    const nextCloud = createCloudLayer(shifted);
    nextCloud.provider.readyPromise.then(() => {
        const old = cloudLayer;
        nextCloud.layer.alpha = CLOUD_ALPHA;
        if (old) viewer.imageryLayers.remove(old);
        cloudLayer = nextCloud.layer;
        cloudAnimationBusy = false;
    }).catch(() => {
        viewer.imageryLayers.remove(nextCloud.layer);
        cloudAnimationBusy = false;
    });
}, 30000);

// --------------------------------------------------------------------------
// 4. 3D TRAJECTORY RING, TWIN SATELLITE, UNCERTAINTY DOME & FAULT MARKERS
// --------------------------------------------------------------------------
function createFallbackOrbitRing(inclinationDeg = 51.64, altKm = 418.0) {
    const positions = [];
    const samples = 180;
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
    name: "Active SGP4 Orbital Ring",
    polyline: {
        positions: createFallbackOrbitRing(51.64, 418.0),
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
            color: Cesium.Color.fromCssColorString("#5be0ad").withAlpha(0.75),
            dashLength: 12
        })
    }
});

// Primary Twin Satellite Entity
const satelliteEntity = viewer.entities.add({
    name: "Digital Twin Active Target",
    position: Cesium.Cartesian3.fromDegrees(0, 0, 418000),
    point: {
        pixelSize: 15,
        color: Cesium.Color.WHITE,
        outlineColor: new Cesium.CallbackProperty(() => {
            if (currentTwinStatus === "PREDICTED") return Cesium.Color.fromCssColorString("#38bdf8");
            if (currentTwinStatus === "DELAYED" || currentTwinStatus === "STALE") return Cesium.Color.fromCssColorString("#f59e0b");
            return Cesium.Color.fromCssColorString("#54d6ff");
        }, false),
        outlineWidth: 4,
        scaleByDistance: new Cesium.NearFarScalar(1000000, 1.8, 30000000, 0.9)
    },
    label: {
        text: new Cesium.CallbackProperty(() => {
            if (currentTwinStatus === "PREDICTED" || isSeverActive) {
                return `${activeSatLabel} // PREDICTED (±${currentConfidenceRadiusKm.toFixed(1)} km)`;
            }
            return `${activeSatLabel} // ${activeNoradId}`;
        }, false),
        font: "11px DM Mono, monospace",
        fillColor: Cesium.Color.WHITE,
        outlineColor: Cesium.Color.BLACK,
        outlineWidth: 3,
        style: Cesium.LabelStyle.FILL_AND_OUTLINE,
        pixelOffset: new Cesium.Cartesian2(18, -14),
        showBackground: true,
        backgroundColor: Cesium.Color.BLACK.withAlpha(0.78),
        scaleByDistance: new Cesium.NearFarScalar(1000000, 1.0, 30000000, 0.65)
    }
});

// Dynamic Kalman 3-Sigma Covariance Confidence Zone (visually scaled so expansion from space is dramatic!)
const confidenceZoneEntity = viewer.entities.add({
    name: "Kalman 3-Sigma Confidence Dome",
    show: new Cesium.CallbackProperty(() => !followISS, false),
    position: new Cesium.CallbackProperty(() => satelliteEntity.position.getValue(Cesium.JulianDate.now()), false),
    ellipsoid: {
        radii: new Cesium.CallbackProperty(() => {
            const r = Math.max(70000.0, currentConfidenceRadiusMeters);
            return new Cesium.Cartesian3(r, r, r);
        }, false),
        material: new Cesium.CallbackProperty(() => {
            if (currentTwinStatus === "PREDICTED" || isSeverActive) {
                return Cesium.Color.fromCssColorString("#38bdf8").withAlpha(0.32);
            }
            return Cesium.Color.fromCssColorString("#06b6d4").withAlpha(0.16);
        }, false),
        outline: true,
        outlineColor: new Cesium.CallbackProperty(() => {
            if (currentTwinStatus === "PREDICTED" || isSeverActive) {
                return Cesium.Color.fromCssColorString("#7dd3fc").withAlpha(0.90);
            }
            return Cesium.Color.fromCssColorString("#58d5f5").withAlpha(0.45);
        }, false)
    }
});

// Naive Tracker Comparison Ghost (Freezes in place when Sever Telemetry is active)
const naiveGhostEntity = viewer.entities.add({
    name: "Naive Tracker (Baseline Comparison)",
    show: new Cesium.CallbackProperty(() => showNaiveGhost && (isSeverActive || isOutliersActive), false),
    position: new Cesium.CallbackProperty(() => naivePositionCartesian || satelliteEntity.position.getValue(Cesium.JulianDate.now()), false),
    point: {
        pixelSize: 13,
        color: Cesium.Color.fromCssColorString("#f59e0b"),
        outlineColor: Cesium.Color.fromCssColorString("#78350f"),
        outlineWidth: 3
    },
    label: {
        text: new Cesium.CallbackProperty(() => {
            if (isSeverActive) {
                return `NAIVE TRACKER FROZEN · DRIFT ${naiveDriftKm.toFixed(1)} km`;
            }
            return "NAIVE TRACKER (ERRATIC SPIKES)";
        }, false),
        font: "10px DM Mono, monospace",
        fillColor: Cesium.Color.fromCssColorString("#fde68a"),
        outlineColor: Cesium.Color.BLACK,
        outlineWidth: 3,
        style: Cesium.LabelStyle.FILL_AND_OUTLINE,
        verticalOrigin: Cesium.VerticalOrigin.TOP,
        pixelOffset: new Cesium.Cartesian2(0, 16),
        showBackground: true,
        backgroundColor: Cesium.Color.fromCssColorString("#451a03").withAlpha(0.85)
    }
});

// Drift Tether connecting Frozen Naive Ghost to Continuing Digital Twin
const driftTetherEntity = viewer.entities.add({
    name: "Naive vs Digital Twin Drift Tether",
    show: new Cesium.CallbackProperty(() => showNaiveGhost && isSeverActive && naivePositionCartesian != null, false),
    polyline: {
        positions: new Cesium.CallbackProperty(() => {
            const pTwin = satelliteEntity.position.getValue(Cesium.JulianDate.now());
            if (!pTwin || !naivePositionCartesian) return [];
            return [naivePositionCartesian, pTwin];
        }, false),
        width: 3,
        arcType: Cesium.ArcType.NONE,
        material: new Cesium.PolylineDashMaterialProperty({
            color: Cesium.Color.fromCssColorString("#f97316"),
            dashLength: 16
        })
    }
});

// Delayed Packet Echo Marker (Visible when Inject 4s Delay is active)
const delayedEchoEntity = viewer.entities.add({
    name: "Delayed Ground Packet Echo",
    show: new Cesium.CallbackProperty(() => isDelayActive && delayedPosVisible && delayedPositionCartesian != null, false),
    position: new Cesium.CallbackProperty(() => delayedPositionCartesian || satelliteEntity.position.getValue(Cesium.JulianDate.now()), false),
    point: {
        pixelSize: 11,
        color: Cesium.Color.fromCssColorString("#fbbf24"),
        outlineColor: Cesium.Color.WHITE,
        outlineWidth: 2
    },
    label: {
        text: new Cesium.CallbackProperty(() => delayedLagLabel, false),
        font: "10px DM Mono, monospace",
        fillColor: Cesium.Color.fromCssColorString("#fef08a"),
        outlineColor: Cesium.Color.BLACK,
        outlineWidth: 3,
        style: Cesium.LabelStyle.FILL_AND_OUTLINE,
        verticalOrigin: Cesium.VerticalOrigin.BOTTOM,
        pixelOffset: new Cesium.Cartesian2(0, -14),
        showBackground: true,
        backgroundColor: Cesium.Color.fromCssColorString("#422006").withAlpha(0.85)
    }
});

// MAD / Chi2 Gate Rejected Outlier Flash Marker (Visible when Inject Outliers is active)
const rejectedSpikeEntity = viewer.entities.add({
    name: "Rejected Rogue Outlier Spike",
    show: new Cesium.CallbackProperty(() => Date.now() < rejectedSpikeExpiresAt && rejectedSpikeCartesian != null, false),
    position: new Cesium.CallbackProperty(() => rejectedSpikeCartesian || satelliteEntity.position.getValue(Cesium.JulianDate.now()), false),
    point: {
        pixelSize: 12,
        color: Cesium.Color.fromCssColorString("#f43f5e"),
        outlineColor: Cesium.Color.WHITE,
        outlineWidth: 2
    },
    label: {
        text: new Cesium.CallbackProperty(() => rejectedSpikeLabel, false),
        font: "10px DM Mono, monospace",
        fillColor: Cesium.Color.fromCssColorString("#fecdd3"),
        outlineColor: Cesium.Color.BLACK,
        outlineWidth: 3,
        style: Cesium.LabelStyle.FILL_AND_OUTLINE,
        verticalOrigin: Cesium.VerticalOrigin.BOTTOM,
        pixelOffset: new Cesium.Cartesian2(12, -12),
        showBackground: true,
        backgroundColor: Cesium.Color.fromCssColorString("#4c0519").withAlpha(0.88)
    }
});

const outlierLineEntity = viewer.entities.add({
    name: "Rejected Outlier Vector",
    show: new Cesium.CallbackProperty(() => Date.now() < rejectedSpikeExpiresAt && rejectedSpikeCartesian != null, false),
    polyline: {
        positions: new Cesium.CallbackProperty(() => {
            const pTwin = satelliteEntity.position.getValue(Cesium.JulianDate.now());
            if (!pTwin || !rejectedSpikeCartesian) return [];
            return [pTwin, rejectedSpikeCartesian];
        }, false),
        width: 2,
        arcType: Cesium.ArcType.NONE,
        material: new Cesium.PolylineDashMaterialProperty({
            color: Cesium.Color.fromCssColorString("#f43f5e"),
            dashLength: 10
        })
    }
});

// --------------------------------------------------------------------------
// 5. 3D ISS / SPACECRAFT STRUCTURAL SCHEMATIC & FOLLOW MODE
// --------------------------------------------------------------------------
const issMaterials = {
    truss: Cesium.Color.fromCssColorString("#aebbc7"),
    module: Cesium.Color.fromCssColorString("#dce5eb"),
    solar: Cesium.Color.fromCssColorString("#123c78")
};
const ISS_VISUAL_SCALE = 8;

function addISSPart(name, offset, dimensions, material) {
    viewer.entities.add({
        name,
        position: new Cesium.CallbackProperty(() => {
            const base = satelliteEntity.position.getValue(Cesium.JulianDate.now());
            if (!base) return undefined;
            const frame = Cesium.Transforms.eastNorthUpToFixedFrame(base);
            return Cesium.Matrix4.multiplyByPoint(
                frame,
                new Cesium.Cartesian3(offset.x * ISS_VISUAL_SCALE, offset.y * ISS_VISUAL_SCALE, offset.z * ISS_VISUAL_SCALE),
                new Cesium.Cartesian3()
            );
        }, false),
        orientation: new Cesium.CallbackProperty(() => {
            const base = satelliteEntity.position.getValue(Cesium.JulianDate.now());
            if (!base) return undefined;
            const frame = Cesium.Transforms.eastNorthUpToFixedFrame(base);
            const rot = Cesium.Matrix4.getMatrix3(frame, new Cesium.Matrix3());
            return Cesium.Quaternion.fromRotationMatrix(rot);
        }, false),
        box: {
            dimensions: new Cesium.Cartesian3(dimensions.x * ISS_VISUAL_SCALE, dimensions.y * ISS_VISUAL_SCALE, dimensions.z * ISS_VISUAL_SCALE),
            material,
            outline: true,
            outlineColor: Cesium.Color.fromCssColorString("#72cfff").withAlpha(0.7)
        }
    });
}
addISSPart("Main Truss", { x: 0, y: 0, z: 0 }, { x: 75, y: 3, z: 3 }, issMaterials.truss);
addISSPart("Central Module", { x: 0, y: 0, z: 6 }, { x: 15, y: 9, z: 9 }, issMaterials.module);
addISSPart("Forward Lab", { x: 17, y: 0, z: 0 }, { x: 13, y: 8, z: 8 }, issMaterials.module);
addISSPart("Solar Array Port", { x: -24, y: 20, z: 0 }, { x: 8, y: 34, z: 1 }, issMaterials.solar);
addISSPart("Solar Array Starboard", { x: 24, y: -20, z: 0 }, { x: 8, y: 34, z: 1 }, issMaterials.solar);

let followISS = false;
function toggleISSFollow(enabled) {
    followISS = enabled;
    satelliteEntity.point.show = !enabled;
    const followBtn = $("btn-follow-sat");
    const followTxt = $("follow-btn-text");
    if (followBtn) followBtn.classList.toggle("btn-active-cyan", enabled);
    if (followTxt) followTxt.textContent = enabled ? "Exit Follow (Esc)" : "Follow Target (F)";

    if (enabled) {
        const position = satelliteEntity.position.getValue(Cesium.JulianDate.now());
        if (position) {
            viewer.camera.lookAt(position, new Cesium.HeadingPitchRange(0, Cesium.Math.toRadians(-18), 5500));
        }
        addEvent("Close-up 3D target tracking camera active");
    } else {
        viewer.camera.lookAtTransform(Cesium.Matrix4.IDENTITY);
        viewer.camera.flyTo({
            destination: Cesium.Cartesian3.fromDegrees(currentLon || 0, Math.max(-45, Math.min(45, currentLat * 0.4)), 23500000),
            orientation: { heading: 0, pitch: Cesium.Math.toRadians(-90), roll: 0 },
            duration: 1.8
        });
        addEvent("Global Earth overview camera restored");
    }
}

window.toggleISSFollowBtn = function () {
    toggleISSFollow(!followISS);
};

window.addEventListener("keydown", (event) => {
    if (event.target instanceof HTMLInputElement || event.target instanceof HTMLTextAreaElement || event.target instanceof HTMLSelectElement) return;
    if (event.key.toLowerCase() === "f") toggleISSFollow(!followISS);
    if (event.key === "Escape" && followISS) toggleISSFollow(false);
});

viewer.camera.flyTo({
    destination: Cesium.Cartesian3.fromDegrees(20, 15, 24500000),
    orientation: { heading: 0, pitch: Cesium.Math.toRadians(-90), roll: 0 },
    duration: 2.2
});

// --------------------------------------------------------------------------
// 6. RESILIENT TELEMETRY LINK (WEBSOCKET + STATELESS SERVERLESS FALLBACK)
// --------------------------------------------------------------------------
let ws = null;
let reconnectTimer = null;
let wsFailCount = 0;
let useHttpPolling = false;
let pollingTimer = null;

function buildSnapshotUrl() {
    const params = new URLSearchParams();
    params.set("norad_id", String(activeNoradId));
    params.set("feed_cut", String(isSeverActive));
    if (isSeverActive && severStartTimeMs != null) {
        params.set("outage_s", ((Date.now() - severStartTimeMs) / 1000.0).toFixed(2));
    }
    params.set("packet_latency_s", isDelayActive ? "4.0" : "0.0");
    params.set("sensor_noise_km", isOutliersActive ? "55.0" : "0.0");
    params.set("compare_naive", String(showNaiveGhost));
    params.set("include_track", String(lastRenderedTrackNorad !== activeNoradId));
    return `/api/telemetry/snapshot?${params.toString()}`;
}

async function fetchImmediateSnapshot() {
    try {
        const res = await fetch(buildSnapshotUrl());
        if (res.ok) {
            const data = await res.json();
            handleTelemetryPacket(data);
        }
    } catch (err) {
        console.warn("[Digital Twin] Immediate snapshot error:", err);
    }
}

function startHttpPolling() {
    if (useHttpPolling) return;
    useHttpPolling = true;
    const badge = $("stream-connection-badge");
    if (badge) {
        badge.innerHTML = "<i></i> 1 HZ SERVERLESS STREAM";
        badge.style.color = "var(--green)";
    }
    addEvent("Connected via 1 Hz HTTP telemetry stream");

    async function poll() {
        await fetchImmediateSnapshot();
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

    try {
        ws = new WebSocket(wsUrl);
    } catch (e) {
        startHttpPolling();
        return;
    }

    ws.onopen = () => {
        wsFailCount = 0;
        stopHttpPolling();
        const badge = $("stream-connection-badge");
        if (badge) {
            badge.innerHTML = "<i></i> WEBSOCKET LIVE";
            badge.style.color = "var(--green)";
        }
        addEvent("Live WebSocket downlink established");
        clearTimeout(reconnectTimer);
    };

    ws.onmessage = (event) => {
        try {
            const data = JSON.parse(event.data);
            handleTelemetryPacket(data);
        } catch (err) {
            console.error("[Digital Twin] Parse error:", err);
        }
    };

    ws.onclose = () => {
        wsFailCount += 1;
        if (wsFailCount >= 1) {
            startHttpPolling();
        } else {
            reconnectTimer = setTimeout(connectWebSocket, 1500);
        }
    };

    ws.onerror = () => {
        wsFailCount += 1;
        if (wsFailCount >= 1) {
            startHttpPolling();
        }
    };
}
// Fetch first frame immediately for instant boot, then negotiate WebSocket
fetchImmediateSnapshot();
connectWebSocket();

// --------------------------------------------------------------------------
// 7. PACKET HANDLER: UPDATES 3D GLOBE + EVERY HMI READOUT CLEANLY
// --------------------------------------------------------------------------
function handleTelemetryPacket(data) {
    if (!data || !data.position_geodetic) return;

    // Ignore stale asynchronous packets from previous satellite while switching
    if (data.norad_id && Number(data.norad_id) !== activeNoradId) {
        return;
    }

    const geo = data.position_geodetic;
    currentLat = geo.latitude;
    currentLon = geo.longitude;
    currentAltKm = geo.altitude_km;
    const velKmS = geo.velocity_km_s;
    const posEcef = data.position_ecef_km || [0, 0, 0];

    activeSatLabel = data.satellite_name || activeSatLabel;

    let statusVal = data.twin_status || "LIVE";
    let statusColor = data.status_color || "#10b981";
    if (Date.now() < recoveryFlashUntil && !isSeverActive && !isDelayActive) {
        statusVal = "RECOVERED";
        statusColor = "#06b6d4";
    }
    currentTwinStatus = statusVal;

    const confPct = data.ekf_confidence_pct != null ? data.ekf_confidence_pct : 98.6;
    const sigmaKm = data.ekf_sigma_pos_km != null ? data.ekf_sigma_pos_km : 0.086;
    const confRadiusKm = data.ekf_uncertainty_radius_km != null ? data.ekf_uncertainty_radius_km : 0.26;
    const innovKm = data.ekf_innovation_residual_km != null ? data.ekf_innovation_residual_km : 0.05;

    currentConfidenceRadiusKm = confRadiusKm;
    // Scale 3D sphere visually so small LEO uncertainty is a sleek halo (~80km) and outage divergence balloons visibly (~900km)
    currentConfidenceRadiusMeters = 75000.0 + Math.min(850000.0, confRadiusKm * 52000.0);

    // 1. Update Satellite Marker Position
    const newPos = Cesium.Cartesian3.fromDegrees(currentLon, currentLat, currentAltKm * 1000.0);
    satelliteEntity.position = newPos;
    if (followISS) {
        viewer.camera.lookAt(newPos, new Cesium.HeadingPitchRange(0, Cesium.Math.toRadians(-18), 5500));
    }

    // 2. Update 3D SGP4 Orbital Ring when orbit_track arrives (on startup or target switch)
    if (data.orbit_track && data.orbit_track.length > 10) {
        const ringPts = data.orbit_track.map(
            (pt) => new Cesium.Cartesian3(pt.x * 1000.0, pt.y * 1000.0, pt.z * 1000.0)
        );
        orbitEntity.polyline.positions = ringPts;
        lastRenderedTrackNorad = data.norad_id;
    }

    // 3. Update Ground Track Trail
    recentPositionsHistory.push(Cesium.Cartesian3.fromDegrees(currentLon, currentLat, 2000.0));
    if (recentPositionsHistory.length > 140) recentPositionsHistory.shift();
    groundTrackEntity.polyline.positions = recentPositionsHistory;

    // 4. Update Naive Ghost & Drift Distance
    const naive = data.naive_position;
    if (naive) {
        naivePositionCartesian = Cesium.Cartesian3.fromDegrees(
            naive.longitude,
            naive.latitude,
            (naive.altitude_km || currentAltKm) * 1000.0
        );
        naiveDriftKm = naive.drift_km || 0.0;
    }

    // 5. Update Delayed Echo Marker
    const delayed = data.delayed_position;
    if (delayed && isDelayActive) {
        delayedPositionCartesian = Cesium.Cartesian3.fromDegrees(
            delayed.longitude,
            delayed.latitude,
            (delayed.altitude_km || currentAltKm) * 1000.0
        );
        delayedLagLabel = `LAGGED PACKET (-${delayed.latency_s}s · ${delayed.lag_distance_km} km)`;
        delayedPosVisible = true;
    } else {
        delayedPosVisible = false;
    }

    // 6. Update Rejected Outlier Spike Flash on 3D Globe
    if (data.rejected_spike) {
        const rs = data.rejected_spike;
        rejectedSpikeCartesian = Cesium.Cartesian3.fromDegrees(rs.longitude, rs.latitude, (rs.altitude_km || currentAltKm) * 1000.0);
        rejectedSpikeLabel = `✕ MAD REJECTED (+${rs.error_km} km)`;
        rejectedSpikeExpiresAt = Date.now() + 2400;

        if (Date.now() - lastLoggedSpikeMs > 1800) {
            addEvent(`[MAD/χ² GATE] Rejected corrupted spike (+${rs.error_km} km)`, true);
            lastLoggedSpikeMs = Date.now();
        }
    }

    // 7. Update Header & Target Identity
    if ($("satellite-norad-badge")) {
        $("satellite-norad-badge").textContent = `NORAD ${data.norad_id} · ${(data.inclination_deg || 51.64).toFixed(2)}°`;
    }
    if ($("target-name-display")) {
        $("target-name-display").textContent = `${data.satellite_description || activeSatLabel}`;
    }
    if ($("vehicle-code-label")) {
        $("vehicle-code-label").textContent = activeSatLabel;
    }

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

    // 8. Update Left Panel (01 / FLIGHT DATA)
    if ($("panel-state-dot")) {
        $("panel-state-dot").style.background = statusColor;
        $("panel-state-dot").style.boxShadow = `0 0 10px ${statusColor}`;
    }
    if ($("panel-state-text")) {
        const modeTxt = statusVal === "PREDICTED" || statusVal === "STALE"
            ? "AUTONOMOUS DEAD-RECKONING"
            : statusVal === "DELAYED"
            ? "LAG-COMPENSATED FUSION"
            : "SGP4 + EKF FUSED";
        $("panel-state-text").textContent = `${statusVal} · ${modeTxt}`;
        $("panel-state-text").style.color = statusColor;
    }
    if ($("panel-state-desc")) {
        if (isSeverActive) {
            $("panel-state-desc").textContent = `Feed lost for ${data.time_since_last_measurement_s.toFixed(0)}s. Propagating orbit via physics while 3σ confidence dome expands.`;
        } else if (isOutliersActive) {
            $("panel-state-desc").textContent = "MAD & χ² innovation gates actively blocking rogue GPS spikes from corrupting orbit state.";
        } else if (isDelayActive) {
            $("panel-state-desc").textContent = "Compensating +4.0s downlink delay so cockpit renders true instantaneous position.";
        } else {
            $("panel-state-desc").textContent = "SGP4 physics corrected every 1s by live telemetry via Extended Kalman Filter.";
        }
    }

    if ($("altitude")) $("altitude").textContent = currentAltKm.toFixed(2);
    if ($("velocity")) $("velocity").textContent = `${velKmS.toFixed(3)} km/s`;
    if ($("latitude")) $("latitude").textContent = `${currentLat.toFixed(4)}°`;
    if ($("longitude")) $("longitude").textContent = `${currentLon.toFixed(4)}°`;
    if ($("sigma-pos")) $("sigma-pos").textContent = `±${sigmaKm.toFixed(3)} km`;
    if ($("confidence-radius")) $("confidence-radius").textContent = `${confRadiusKm.toFixed(2)} km`;
    if ($("innovation-residual")) $("innovation-residual").textContent = `${innovKm.toFixed(3)} km`;

    // 9. Update Right Panel (02 / SUBSYSTEM TWIN)
    const pwr = data.power || {};
    const thm = data.thermal || {};
    const ecl = data.eclipse || {};
    const comm = data.comms || {};

    if ($("solar-power")) $("solar-power").textContent = `${(pwr.solar_generation_kw || 0).toFixed(1)} kW`;
    if ($("battery-soc")) $("battery-soc").textContent = `${(pwr.battery_soc_pct || 0).toFixed(1)}%`;
    if ($("bus-voltage")) $("bus-voltage").textContent = `${(pwr.bus_voltage_v || 0).toFixed(1)} V`;
    if ($("net-power")) {
        const net = pwr.net_power_kw || 0;
        $("net-power").textContent = `${net >= 0 ? "+" : ""}${net.toFixed(1)} kW`;
        $("net-power").style.color = net >= 0 ? "var(--green)" : "var(--amber)";
    }
    if ($("battery-bar")) $("battery-bar").style.width = `${Math.min(100, Math.max(0, pwr.battery_soc_pct || 0))}%`;
    if ($("power-mode-badge")) {
        const cs = pwr.charge_state || "CHARGING";
        $("power-mode-badge").textContent = cs;
        $("power-mode-badge").style.color = cs === "DISCHARGING" ? "var(--amber)" : "var(--green)";
    }

    if ($("core-temp")) $("core-temp").textContent = `${(thm.core_temp_c || 21.0).toFixed(1)} °C`;
    if ($("array-temp")) $("array-temp").textContent = `${(thm.solar_array_temp_c || 35.0).toFixed(1)} °C`;
    if ($("beta-angle")) $("beta-angle").textContent = `${(ecl.beta_angle_deg || 0).toFixed(1)}°`;
    if ($("solar-illumination")) $("solar-illumination").textContent = `${(ecl.illumination_pct != null ? ecl.illumination_pct : 100).toFixed(0)}%`;
    if ($("eclipse-badge")) {
        const st = ecl.status || "SUNLIGHT";
        $("eclipse-badge").textContent = st === "SUNLIGHT" ? "☀️ SUNLIGHT" : "🌑 ECLIPSE";
        $("eclipse-badge").style.color = st === "SUNLIGHT" ? "var(--green)" : "var(--amber)";
    }

    if ($("comms-latency")) {
        const latMs = comm.latency_ms || 95.0;
        $("comms-latency").textContent = `${latMs.toFixed(0)} ms`;
        $("comms-latency").style.color = latMs > 1000 ? "var(--amber)" : "#e1ebf2";
    }
    if ($("pointing-stability")) {
        $("pointing-stability").textContent = `${((comm.attitude_stability_score || 0.99) * 100.0).toFixed(1)}%`;
    }
    if ($("comms-badge")) {
        $("comms-badge").textContent = isSeverActive ? "SIGNAL LOST" : isDelayActive ? "HIGH LAG" : "HEALTHY";
        $("comms-badge").style.color = isSeverActive ? "var(--rose)" : isDelayActive ? "var(--amber)" : "var(--green)";
    }

    // 10. Update Bottom Console (03 / TELEMETRY)
    if ($("pos-x")) $("pos-x").textContent = (posEcef[0] || 0).toFixed(2);
    if ($("pos-y")) $("pos-y").textContent = (posEcef[1] || 0).toFixed(2);
    if ($("pos-z")) $("pos-z").textContent = (posEcef[2] || 0).toFixed(2);
    if ($("orbit-period")) $("orbit-period").textContent = `${(data.orbital_period_min || 92.9).toFixed(1)}m`;
    if ($("orbit-inclination")) $("orbit-inclination").textContent = `${(data.inclination_deg || 51.6).toFixed(1)}°`;
    if ($("bottom-confidence-zone")) $("bottom-confidence-zone").textContent = confRadiusKm.toFixed(2);
    if ($("bottom-status-text")) {
        $("bottom-status-text").textContent = statusVal;
        $("bottom-status-text").style.color = statusColor;
    }

    // 11. Update Sandbox Live Explanation Summary
    const summaryEl = $("fault-active-summary");
    if (summaryEl) {
        if (isSeverActive) {
            summaryEl.style.color = "#7dd3fc";
            summaryEl.textContent = `✂ FEED SEVERED (${data.time_since_last_measurement_s.toFixed(0)}s): Twin running autonomously (${statusVal})! 3D Uncertainty Dome widened to ±${confRadiusKm.toFixed(1)} km while Amber Naive Ghost froze ${naiveDriftKm.toFixed(1)} km behind.`;
        } else if (isDelayActive && isOutliersActive) {
            summaryEl.style.color = "#fcd34d";
            summaryEl.textContent = `⏱ +4s LAG & ⚠️ MAD GATE ACTIVE: Compensating ${((comm.latency_ms || 4000)).toFixed(0)}ms lag while blocking rogue red GPS spikes.`;
        } else if (isDelayActive) {
            summaryEl.style.color = "#fcd34d";
            summaryEl.textContent = `⏱ DELAYED (+4.0s LAG): Ground packets trail ${delayed ? delayed.lag_distance_km : 30} km behind satellite; Twin compensates forward to true orbit.`;
        } else if (isOutliersActive) {
            summaryEl.style.color = "#fda4af";
            summaryEl.textContent = `⚠️ ROGUE SPIKE INJECTION ACTIVE: 3.5σ MAD & χ² Gate rejecting red off-track GPS outliers to protect orbit stability.`;
        } else if (statusVal === "RECOVERED") {
            summaryEl.style.color = "#22d3ee";
            summaryEl.textContent = `↺ RECOVERED: Live ground link re-acquired — EKF collapsing covariance back to ±${confRadiusKm.toFixed(2)} km.`;
        } else {
            summaryEl.style.color = "#cbd5e1";
            summaryEl.textContent = "Nominal Stream: Ingesting live telemetry & refining orbital state via Extended Kalman Filter";
        }
    }

    // 12. Smart State & Anomaly Event Logging
    if (lastLoggedState !== statusVal) {
        if (lastLoggedState !== null) {
            addEvent(`Twin regime transition: ${lastLoggedState} ➔ ${statusVal}`, statusVal !== "LIVE");
        }
        lastLoggedState = statusVal;
    }

    const anomalies = data.active_anomalies || [];
    if (anomalies.length > 0 && !isOutliersActive) {
        const latest = anomalies[0];
        const key = `${latest.timestamp}-${latest.code}`;
        if (key !== lastLoggedAnomalyKey) {
            lastLoggedAnomalyKey = key;
            addEvent(`[${latest.subsystem}] ${latest.message}`, true);
        }
    }
}

// --------------------------------------------------------------------------
// 8. FAULT INJECTION & TARGET SELECTOR HANDLERS
// --------------------------------------------------------------------------
function syncFaultsToBackend() {
    const payload = {
        feed_cut: isSeverActive,
        packet_latency_s: isDelayActive ? 4.0 : 0.0,
        sensor_noise_km: isOutliersActive ? 55.0 : 0.0,
        compare_naive: showNaiveGhost
    };

    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ command: "SET_FAULT", ...payload }));
    } else {
        fetch("/api/faults/inject", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        }).then(() => fetchImmediateSnapshot()).catch(e => console.error(e));
    }
}

window.toggleSeverFeed = function () {
    isSeverActive = !isSeverActive;
    severStartTimeMs = isSeverActive ? Date.now() : null;
    if (!isSeverActive) recoveryFlashUntil = Date.now() + 2200;
    const btn = $("btn-sever-feed");
    if (btn) btn.classList.toggle("btn-active", isSeverActive);
    syncFaultsToBackend();
    addEvent(
        isSeverActive ? "✂ Ground feed severed — EKF autonomous propagation active" : "↺ Ground feed restored — EKF re-converging",
        isSeverActive
    );
};

window.togglePacketDelay = function () {
    isDelayActive = !isDelayActive;
    const btn = $("btn-inject-delay");
    if (btn) btn.classList.toggle("btn-active", isDelayActive);
    syncFaultsToBackend();
    addEvent(isDelayActive ? "⏱ Injected +4.0s downlink packet lag" : "Downlink latency restored to nominal", isDelayActive);
};

window.toggleOutlierSpikes = function () {
    isOutliersActive = !isOutliersActive;
    const btn = $("btn-inject-outliers");
    if (btn) btn.classList.toggle("btn-active", isOutliersActive);
    syncFaultsToBackend();
    addEvent(isOutliersActive ? "⚠️ Injecting rogue 55km GPS spikes into MAD Gate" : "GPS outlier injection stopped", isOutliersActive);
};

window.toggleNaiveGhost = function () {
    showNaiveGhost = !showNaiveGhost;
    const btn = $("btn-toggle-ghost");
    const txt = $("ghost-btn-text");
    if (btn) btn.classList.toggle("btn-active-subtle", showNaiveGhost);
    if (txt) txt.textContent = `Naive Ghost: ${showNaiveGhost ? "ON" : "OFF"}`;
    syncFaultsToBackend();
};

window.resetAllFaults = function () {
    const wasFaulted = isSeverActive || isDelayActive || isOutliersActive;
    isSeverActive = false;
    severStartTimeMs = null;
    isDelayActive = false;
    isOutliersActive = false;
    delayedPosVisible = false;
    rejectedSpikeExpiresAt = 0;
    if (wasFaulted) recoveryFlashUntil = Date.now() + 2500;

    if ($("btn-sever-feed")) $("btn-sever-feed").classList.remove("btn-active");
    if ($("btn-inject-delay")) $("btn-inject-delay").classList.remove("btn-active");
    if ($("btn-inject-outliers")) $("btn-inject-outliers").classList.remove("btn-active");

    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ command: "RESET_FAULTS" }));
    } else {
        fetch("/api/faults/reset", { method: "POST" })
            .then(() => fetchImmediateSnapshot())
            .catch(e => console.error(e));
    }
    addEvent("↺ All faults cleared — nominal EKF tracking restored");
};

// Target Satellite Switcher
const selector = $("satellite-selector");
if (selector) {
    selector.addEventListener("change", async (e) => {
        const noradId = parseInt(e.target.value, 10);
        const satName = e.target.options[e.target.selectedIndex].text.split("·")[0].trim();

        activeNoradId = noradId;
        activeSatLabel = satName;
        lastRenderedTrackNorad = null;
        recentPositionsHistory = [];
        groundTrackEntity.polyline.positions = [];
        naivePositionCartesian = null;
        delayedPosVisible = false;
        rejectedSpikeExpiresAt = 0;

        addEvent(`Target switched to ${satName} (NORAD ${noradId})`);

        if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ command: "SELECT_SATELLITE", norad_id: noradId }));
        }

        try {
            const res = await fetch(`/api/select-satellite/${noradId}`, { method: "POST" });
            if (res.ok) {
                const payload = await res.json();
                if (payload.telemetry) {
                    handleTelemetryPacket(payload.telemetry);
                    // Smoothly pivot Earth camera to look at the newly selected spacecraft's hemisphere
                    if (!followISS && payload.telemetry.position_geodetic) {
                        const g = payload.telemetry.position_geodetic;
                        viewer.camera.flyTo({
                            destination: Cesium.Cartesian3.fromDegrees(g.longitude, Math.max(-50, Math.min(50, g.latitude * 0.5)), 23500000),
                            orientation: { heading: 0, pitch: Cesium.Math.toRadians(-90), roll: 0 },
                            duration: 1.8
                        });
                    }
                }
            }
            if (isSeverActive || isDelayActive || isOutliersActive || lastRenderedTrackNorad !== noradId) {
                await fetchImmediateSnapshot();
            }
        } catch (err) {
            console.error("[Digital Twin] Error switching satellite:", err);
            await fetchImmediateSnapshot();
        }
    });
}

console.info("ORBITAL Cyber-Physical Digital Twin HMI ready.");
