import sys
from pathlib import Path
import time

backend_path = Path(r"C:\Users\Admin\Desktop\Kshitiz\Satellite_Digital_Twin\backend")
sys.path.insert(0, str(backend_path))

from app.api.websocket import twin_engine

print("=== 1. NOMINAL STATE ===")
f1 = twin_engine.step()
print(f"Status: {f1.twin_status} ({f1.status_color})")
print(f"Position: Lat {f1.position_geodetic['latitude']}°, Lon {f1.position_geodetic['longitude']}°, Alt {f1.position_geodetic['altitude_km']} km")
print(f"EKF: Confidence {f1.ekf_confidence_pct}%, Sigma {f1.ekf_sigma_pos_km} km, Radius {f1.ekf_uncertainty_radius_km} km")
print(f"Subsystems: Solar {f1.power['solar_generation_kw']} kW, Batt {f1.power['battery_soc_pct']}%, Core Temp {f1.thermal['core_temp_c']}°C, Eclipse: {f1.eclipse['status']}")

print("\n=== 2. INJECT FEED CUT (GROUND STATION LOSS) ===")
twin_engine.set_fault(feed_cut=True)
for i in range(4):
    time.sleep(1)
    f = twin_engine.step()
    naive_stat = f.naive_position['status'] if f.naive_position else 'NONE'
    print(f"T+{i+1}s -> Status: {f.twin_status} | Since last meas: {f.time_since_last_measurement_s}s | EKF Conf: {f.ekf_confidence_pct}% | Sigma: {f.ekf_sigma_pos_km}km | Naive Tracker: {naive_stat}")

print("\n=== 3. RESTORE FEED AND INJECT GPS SENSOR NOISE (+50km) ===")
twin_engine.set_fault(feed_cut=False, sensor_noise_km=50.0)
f_noise = twin_engine.step()
print(f"Status: {f_noise.twin_status}")
print(f"Rejections: {f_noise.recent_rejections}")
print(f"Active Anomaly Alerts: {[a['code'] for a in f_noise.active_anomalies]}")

print("\n=== 4. RESET ALL FAULTS ===")
twin_engine.reset_faults()
f_reset = twin_engine.step()
print(f"Status: {f_reset.twin_status} | EKF Conf: {f_reset.ekf_confidence_pct}% | Active faults: {f_reset.active_faults}")
