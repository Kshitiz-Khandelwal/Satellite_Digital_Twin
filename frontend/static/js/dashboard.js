
/* ==========================================
   ORBITAL — SATELLITE DIGITAL TWIN
   Phase 1: ISS orbital visualization

   DATA: Illustrative circular-orbit simulation
   NOT live ISS telemetry or actual SGP4 output.
========================================== */

"use strict";

const $ = (id) => document.getElementById(id);

// ------------------------------------------
// 1. SIMULATION PARAMETERS
// ------------------------------------------

const EARTH_RADIUS_KM = 6371;
const ORBIT_ALTITUDE_KM = 408.2;
const ORBIT_RADIUS_KM = EARTH_RADIUS_KM + ORBIT_ALTITUDE_KM;

const ORBIT_PERIOD_SECONDS = 92.4 * 60;
const ORBIT_INCLINATION = Cesium.Math.toRadians(51.64);

const EARTH_ROTATION_RATE = 7.2921159e-5;

// Accelerated simulation clock for demonstration.
const SIMULATION_SPEED = 20;

let elapsedSimulationSeconds = 0;
let lastFrameTime = performance.now();


// ------------------------------------------
// 2. CLOCK
// ------------------------------------------

function updateClock() {
    $("utc-clock").textContent =
        new Date().toISOString().slice(11, 19) + " UTC";
}

updateClock();
setInterval(updateClock, 1000);


// ------------------------------------------
// 3. CESIUM INITIALIZATION
// ------------------------------------------

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


// ------------------------------------------
// 3. CESIUM EARTH CONFIGURATION
// ------------------------------------------

viewer.imageryLayers.removeAll();

// Esri satellite imagery — no CARTO API-key watermark
const earthImagery = new Cesium.UrlTemplateImageryProvider({
    url: "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    credit: "Esri, Maxar, Earthstar Geographics"
});

const earthLayer = viewer.imageryLayers.addImageryProvider(
    earthImagery
);

// Dark cinematic satellite appearance

earthLayer.brightness = 0.92;
earthLayer.contrast = 1.08;
earthLayer.saturation = 1.15;
earthLayer.gamma = 0.95;

// ------------------------------------------
// 3A. PROCEDURAL EARTH CLOUD LAYER
// ------------------------------------------


// ------------------------------------------
// 3A. PROCEDURAL EARTH CLOUD LAYER
// ------------------------------------------

function createCloudTexture() {

    const width = 1024;
    const height = 512;

    const canvas = document.createElement("canvas");

    canvas.width = width;
    canvas.height = height;

    const ctx = canvas.getContext("2d");
    const image = ctx.createImageData(width, height);

    // Periodic noise for seamless globe wrapping.
    function hash(x, y, seed) {
        const n = Math.sin(
            x * 127.1 +
            y * 311.7 +
            seed * 74.7
        ) * 43758.5453;

        return n - Math.floor(n);
    }

    function smooth(t) {
        return t * t * (3 - 2 * t);
    }

    function noise(u, v, scaleX, scaleY, seed) {

        const gx = u * scaleX;
        const gy = v * scaleY;

        const x0 = Math.floor(gx);
        const y0 = Math.floor(gy);

        const fx = smooth(gx - x0);
        const fy = smooth(gy - y0);

        const wrapX = (x) =>
            ((x % scaleX) + scaleX) % scaleX;

        const a = hash(wrapX(x0), y0, seed);
        const b = hash(wrapX(x0 + 1), y0, seed);
        const c = hash(wrapX(x0), y0 + 1, seed);
        const d = hash(wrapX(x0 + 1), y0 + 1, seed);

        const top = a + (b - a) * fx;
        const bottom = c + (d - c) * fx;

        return top + (bottom - top) * fy;
    }

    // Generate multi-scale cloud formations.
    for (let y = 0; y < height; y++) {

        for (let x = 0; x < width; x++) {

            const u = x / width;
            const v = y / height;

            let cloud = 0;

            cloud += noise(u, v, 8, 4, 1) * 0.45;
            cloud += noise(u, v, 16, 8, 2) * 0.30;
            cloud += noise(u, v, 32, 16, 3) * 0.17;
            cloud += noise(u, v, 64, 32, 4) * 0.08;

            // Smooth cloud edges.
            let alpha = Math.max(
                0,
                Math.min(
                    1,
                    (cloud - 0.48) / 0.14
                )
            );

            alpha = Math.pow(alpha, 1.5) * 0.95;

            const index = (y * width + x) * 4;

            image.data[index] = 245;
            image.data[index + 1] = 249;
            image.data[index + 2] = 255;
            image.data[index + 3] =
                Math.round(alpha * 255);
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

    // Wrap the texture horizontally to avoid visible seams.
    const shift = ((shiftX % width) + width) % width;

    ctx.drawImage(
        sourceCanvas,
        0, 0, width, canvas.height,
        shift, 0, width, canvas.height
    );

    ctx.drawImage(
        sourceCanvas,
        0, 0, width, canvas.height,
        shift - width, 0, width, canvas.height
    );

    return canvas;
}

// Convert generated canvas into an image URL.
// Cesium SingleTileImageryProvider loads this URL.

// ==========================================
// CLOUD LAYER — ANIMATED
// ==========================================

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

    const layer = viewer.imageryLayers.addImageryProvider(
        provider
    );

    layer.alpha = 0;
    layer.brightness = 1.15;
    layer.contrast = 1.25;

    return { layer, provider };
}

function crossfadeCloudLayers(oldLayer, newLayer) {

    const startTime = performance.now();

    function animate(now) {

        const progress = Math.min(
            (now - startTime) / CLOUD_CROSSFADE_DURATION,
            1
        );

        newLayer.alpha = CLOUD_ALPHA * progress;

        if (oldLayer) {
            oldLayer.alpha = CLOUD_ALPHA * (1 - progress);
        }

        if (progress < 1) {
            requestAnimationFrame(animate);
        } else {

            if (oldLayer) {
                viewer.imageryLayers.remove(oldLayer);
            }

            cloudLayer = newLayer;
        }
    }

    requestAnimationFrame(animate);
}

// Create initial cloud layer.
const initialCloud = createCloudLayer(cloudCanvas);

cloudLayer = initialCloud.layer;

initialCloud.layer.alpha = CLOUD_ALPHA;

console.info("Earth cloud layer initialized");

// Slowly drift the cloud texture.
let cloudAnimationBusy = false;

setInterval(() => {

    if (cloudAnimationBusy) return;

    cloudAnimationBusy = true;

    // One-pixel horizontal texture shift per update.
    cloudShift += 1;

    const shiftedCanvas = createShiftedCloudTexture(
        cloudCanvas,
        cloudShift
    );

    const nextCloud = createCloudLayer(shiftedCanvas);

    nextCloud.provider.readyPromise.then(() => {

        const previousLayer = cloudLayer;

        crossfadeCloudLayers(
            previousLayer,
            nextCloud.layer
        );

        setTimeout(() => {
            cloudAnimationBusy = false;
        }, CLOUD_CROSSFADE_DURATION);

    }).catch((error) => {

        viewer.imageryLayers.remove(nextCloud.layer);

        cloudAnimationBusy = false;

        console.error("Cloud update failed:", error);

    });

}, CLOUD_UPDATE_INTERVAL);


// ------------------------------------------
// 3B. EARTH ATMOSPHERE
// ------------------------------------------

// Enable globe lighting.
viewer.scene.globe.enableLighting = true;

viewer.scene.globe.showGroundAtmosphere = true;

viewer.scene.globe.baseColor =
    Cesium.Color.fromCssColorString("#071321");

// Enable blue atmospheric scattering.
viewer.scene.skyAtmosphere.show = true;

viewer.scene.skyAtmosphere.atmosphereLightIntensity = 12.0;

viewer.scene.skyAtmosphere.hueShift = -0.04;

viewer.scene.skyAtmosphere.saturationShift = 0.15;

viewer.scene.skyAtmosphere.brightnessShift = 0.08;

// Deep-space background.
viewer.scene.backgroundColor =
    Cesium.Color.fromCssColorString("#02040a");

// Improve surface detail.
viewer.scene.globe.maximumScreenSpaceError = 1.5;

console.info("Earth atmosphere initialized");




// ------------------------------------------
// 4. ORBITAL MATHEMATICS
// ------------------------------------------

/*
    Generate an approximate circular orbit
    in an Earth-centred inertial-like frame.

    Then rotate into an Earth-fixed frame
    using Earth's approximate rotation rate.
*/

function getECIPosition(angle) {

    const x =
        ORBIT_RADIUS_KM * Math.cos(angle);

    const y =
        ORBIT_RADIUS_KM *
        Math.sin(angle) *
        Math.cos(ORBIT_INCLINATION);

    const z =
        ORBIT_RADIUS_KM *
        Math.sin(angle) *
        Math.sin(ORBIT_INCLINATION);

    return { x, y, z };
}


function eciToECEF(position, simulationTime) {

    const earthAngle =
        EARTH_ROTATION_RATE * simulationTime;

    const cosAngle = Math.cos(earthAngle);
    const sinAngle = Math.sin(earthAngle);

    return {
        x: cosAngle * position.x + sinAngle * position.y,
        y: -sinAngle * position.x + cosAngle * position.y,
        z: position.z
    };
}


function getSatelliteState(simulationTime) {

    const meanMotion =
        (2 * Math.PI) / ORBIT_PERIOD_SECONDS;

    const orbitAngle =
        meanMotion * simulationTime;

    const eci = getECIPosition(orbitAngle);

    const ecef = eciToECEF(
        eci,
        simulationTime
    );

    return {
        eci,
        ecef,
        angle: orbitAngle
    };
}


// ------------------------------------------
// 5. COORDINATE HELPERS
// ------------------------------------------

function ecefToGeographic(position) {

    const radius = Math.sqrt(
        position.x ** 2 +
        position.y ** 2 +
        position.z ** 2
    );

    const latitude = Math.asin(
        position.z / radius
    );

    const longitude = Math.atan2(
        position.y,
        position.x
    );

    return {
        latitude: Cesium.Math.toDegrees(latitude),
        longitude: Cesium.Math.toDegrees(longitude)
    };
}


function toCesiumPosition(position) {

    // Cesium uses metres, not kilometres.
    return new Cesium.Cartesian3(
        position.x * 1000,
        position.y * 1000,
        position.z * 1000
    );
}


// ------------------------------------------
// 6. BUILD THE ORBITAL TRAJECTORY
// ------------------------------------------

function createOrbitPath(startTime) {

    const positions = [];

    const samples = 240;

    for (let i = 0; i <= samples; i++) {

        const futureTime =
            startTime +
            (i / samples) * ORBIT_PERIOD_SECONDS;

        const state = getSatelliteState(futureTime);

        positions.push(
            toCesiumPosition(state.ecef)
        );
    }

    return positions;
}


const orbitEntity = viewer.entities.add({

    name: "ISS Orbital Trajectory",

    polyline: {

        positions: createOrbitPath(0),

        width: 3,

        arcType: Cesium.ArcType.NONE,

        material:
            new Cesium.PolylineGlowMaterialProperty({
                glowPower: 0.18,
                color: Cesium.Color.fromCssColorString(
                    "#48cfff"
                )
            })
    }
});


// ------------------------------------------
// 7. BUILD THE GROUND TRACK
// ------------------------------------------

function createGroundTrack(startTime) {

    const positions = [];

    const samples = 240;

    for (let i = 0; i <= samples; i++) {

        const futureTime =
            startTime +
            (i / samples) * ORBIT_PERIOD_SECONDS;

        const state = getSatelliteState(futureTime);

        const geo = ecefToGeographic(state.ecef);

        positions.push(
            Cesium.Cartesian3.fromDegrees(
                geo.longitude,
                geo.latitude,
                500
            )
        );
    }

    return positions;
}


const groundTrackEntity = viewer.entities.add({

    name: "ISS Ground Track",

    polyline: {

        positions: createGroundTrack(0),

        width: 2,

        arcType: Cesium.ArcType.NONE,

        material:
            new Cesium.PolylineDashMaterialProperty({
                color: Cesium.Color.fromCssColorString(
                    "#f3bd67"
                ),
                dashLength: 14
            })
    }
});


// ------------------------------------------
// 8. CREATE ISS MARKER
// ------------------------------------------

const satelliteEntity = viewer.entities.add({

    name: "ISS / ZARYA",

    position: toCesiumPosition(
        getSatelliteState(0).ecef
    ),

    
point: {
    pixelSize: 17,

    color: Cesium.Color.WHITE,

    outlineColor:
        Cesium.Color.fromCssColorString("#54d6ff"),

    outlineWidth: 4,

    scaleByDistance:
        new Cesium.NearFarScalar(
            1000000,
            1.8,
            30000000,
            0.9
        )
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

        backgroundColor:
            Cesium.Color.BLACK.withAlpha(0.75),

        scaleByDistance:
            new Cesium.NearFarScalar(
                1000000,
                1,
                30000000,
                0.5
            )
    }
});
/* ==========================================
   8A. 3D INTERNATIONAL SPACE STATION MODEL
========================================== */

const issParts = [];

const issMaterials = {
    truss: Cesium.Color.fromCssColorString("#aebbc7"),
    module: Cesium.Color.fromCssColorString("#dce5eb"),
    solar: Cesium.Color.fromCssColorString("#123c78"),
    radiator: Cesium.Color.fromCssColorString("#d5dce3"),
    detail: Cesium.Color.fromCssColorString("#59d6ff")
};

// Create a component attached to the moving ISS position.

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

            const basePosition =
                satelliteEntity.position.getValue(
                    Cesium.JulianDate.now()
                );

            if (!basePosition) return undefined;

            const frame =
                Cesium.Transforms.eastNorthUpToFixedFrame(
                    basePosition
                );

            return Cesium.Matrix4.multiplyByPoint(
                frame,
                new Cesium.Cartesian3(
                    scaledOffset.x,
                    scaledOffset.y,
                    scaledOffset.z
                ),
                new Cesium.Cartesian3()
            );

        }, false),

        orientation: new Cesium.CallbackProperty(() => {

            const basePosition =
                satelliteEntity.position.getValue(
                    Cesium.JulianDate.now()
                );

            if (!basePosition) return undefined;

            const frame =
                Cesium.Transforms.eastNorthUpToFixedFrame(
                    basePosition
                );

            const rotation = Cesium.Matrix4.getMatrix3(
                frame,
                new Cesium.Matrix3()
            );

            return Cesium.Quaternion.fromRotationMatrix(
                rotation
            );

        }, false),

        box: {

            dimensions: new Cesium.Cartesian3(
                scaledDimensions.x,
                scaledDimensions.y,
                scaledDimensions.z
            ),

            material: material,

            outline: true,

            outlineColor:
                Cesium.Color.fromCssColorString("#72cfff")
                    .withAlpha(0.7)
        }
    });

    issParts.push(part);

    return part;
}



// ------------------------------------------
// CENTRAL STRUCTURE
// ------------------------------------------

addISSPart(
    "ISS Main Truss",
    { x: 0, y: 0, z: 0 },
    { x: 75, y: 3, z: 3 },
    issMaterials.truss
);


// ------------------------------------------
// MAIN PRESSURIZED MODULES
// ------------------------------------------

addISSPart(
    "ISS Central Module",
    { x: 0, y: 0, z: 6 },
    { x: 15, y: 9, z: 9 },
    issMaterials.module
);

addISSPart(
    "ISS Forward Module",
    { x: 17, y: 0, z: 0 },
    { x: 13, y: 8, z: 8 },
    issMaterials.module
);

addISSPart(
    "ISS Research Module",
    { x: -17, y: 0, z: 0 },
    { x: 12, y: 8, z: 8 },
    issMaterials.module
);


// ------------------------------------------
// FOUR SOLAR ARRAY PANELS
// ------------------------------------------

const solarPositions = [
    { x: -25, y: 25, z: 0 },
    { x: -25, y: -25, z: 0 },
    { x: 25, y: 25, z: 0 },
    { x: 25, y: -25, z: 0 }
];

solarPositions.forEach((position, index) => {

    addISSPart(
        `ISS Solar Array ${index + 1}`,
        position,
        { x: 20, y: 19, z: 0.8 },
        issMaterials.solar
    );
});


// ------------------------------------------
// RADIATOR STRUCTURES
// ------------------------------------------

addISSPart(
    "ISS Radiator Left",
    { x: -8, y: 0, z: 12 },
    { x: 3, y: 22, z: 0.8 },
    issMaterials.radiator
);

addISSPart(
    "ISS Radiator Right",
    { x: 8, y: 0, z: 12 },
    { x: 3, y: 22, z: 0.8 },
    issMaterials.radiator
);
addISSPart(
    "ISS Radiator Right",
    { x: 8, y: 0, z: 12 },
    { x: 3, y: 22, z: 0.8 },
    issMaterials.radiator
);

// ------------------------------------------
// VERIFY ISS COMPONENTS
// ------------------------------------------

console.log("ISS model components:", issParts.length);

console.log(
    "ISS model parts:",
    issParts.map(part => part.name)
);
// ------------------------------------------
// CAMERA FOLLOW MODE
// ------------------------------------------

let followISS = false;

function toggleISSFollow(enabled) {

    followISS = enabled;

    // Hide the oversized marker when inspecting the model.
    satelliteEntity.point.show = !enabled;

    if (enabled) {

        const position =
            satelliteEntity.position.getValue(
                Cesium.JulianDate.now()
            );

        if (position) {

            viewer.camera.lookAt(
                position,
                new Cesium.HeadingPitchRange(
                0,
                Cesium.Math.toRadians(-15),
                6000
                )
            );
        }

        addEvent("ISS camera-follow enabled");

    } else {

        viewer.camera.lookAtTransform(
            Cesium.Matrix4.IDENTITY
        );

        viewer.camera.flyTo({

            destination:
                Cesium.Cartesian3.fromDegrees(
                    0,
                    15,
                    28000000
                ),

            duration: 2
        });

        addEvent("ISS camera-follow disabled");
    }
}


// Press F to follow the ISS.
// Press Escape to return to Earth overview.

window.addEventListener("keydown", (event) => {

    if (
        event.target instanceof HTMLInputElement ||
        event.target instanceof HTMLTextAreaElement
    ) {
        return;
    }

    if (event.key.toLowerCase() === "f") {
        toggleISSFollow(!followISS);
    }

    if (event.key === "Escape" && followISS) {
        toggleISSFollow(false);
    }
});

console.info("3D ISS schematic initialized");



// ------------------------------------------
// 9. CAMERA POSITION
// ------------------------------------------




viewer.camera.flyTo({
    destination: Cesium.Cartesian3.fromDegrees(
        0,
        15,
        28000000
    ),
    orientation: {
        heading: 0,
        pitch: Cesium.Math.toRadians(-90),
        roll: 0
    },
    duration: 3
});






// ------------------------------------------
// 10. EVENT STREAM
// ------------------------------------------

function addEvent(message) {

    const container = $("events");

    if (!container) return;

    const item = document.createElement("div");

    item.className = "event";

    const time = new Date()
        .toISOString()
        .slice(11, 19);

    const timeElement = document.createElement("time");
    timeElement.textContent = time;

    const messageElement = document.createElement("span");
    messageElement.textContent = message;

    item.append(timeElement, messageElement);

    container.prepend(item);

    while (container.children.length > 4) {
        container.removeChild(container.lastChild);
    }
}

addEvent("Orbital simulation initialized");


// ------------------------------------------
// 11. UPDATE TELEMETRY
// ------------------------------------------

function updateTelemetry() {

    const state = getSatelliteState(
        elapsedSimulationSeconds
    );

    const position = state.ecef;

    const geo = ecefToGeographic(position);

    const speed =
        (2 * Math.PI * ORBIT_RADIUS_KM) /
        ORBIT_PERIOD_SECONDS;

    const altitude =
        Math.sqrt(
            position.x ** 2 +
            position.y ** 2 +
            position.z ** 2
        ) - EARTH_RADIUS_KM;

    satelliteEntity.position =
        toCesiumPosition(position);
        // Keep the camera attached to the ISS when follow mode is active.
        if (followISS) {

            viewer.camera.lookAt(
                toCesiumPosition(position),

                new Cesium.HeadingPitchRange(
                0,
                Cesium.Math.toRadians(-15),
                6000
            )
            );
        }

    // Update position cards.
    $("pos-x").textContent = position.x.toFixed(2);
    $("pos-y").textContent = position.y.toFixed(2);
    $("pos-z").textContent = position.z.toFixed(2);

    $("latitude").textContent =
        geo.latitude.toFixed(2) + "°";

    $("longitude").textContent =
        geo.longitude.toFixed(2) + "°";

    $("altitude").textContent =
        altitude.toFixed(2);

    $("velocity").textContent =
        speed.toFixed(3) + " km/s";
}


// ------------------------------------------
// 12. SIMULATION LOOP
// ------------------------------------------

function animate(now) {

    const deltaSeconds = Math.min(
        (now - lastFrameTime) / 1000,
        0.1
    );

    lastFrameTime = now;

    elapsedSimulationSeconds +=
        deltaSeconds * SIMULATION_SPEED;

    updateTelemetry();

    requestAnimationFrame(animate);
}

updateTelemetry();

requestAnimationFrame(animate);


// ------------------------------------------
// 13. CAMERA / USER INTERACTION
// ------------------------------------------

viewer.screenSpaceEventHandler.setInputAction(
    function () {
        addEvent("Globe interaction detected");
    },
    Cesium.ScreenSpaceEventType.LEFT_CLICK
);

console.info(
    "ORBITAL initialized: ISS simulation active."
);
