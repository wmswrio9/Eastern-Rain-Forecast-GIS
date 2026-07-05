import os
import json
import sqlite3
import shapefile
from datetime import datetime, timezone, timedelta
THAI_TZ = timezone(timedelta(hours=7))
import numpy as np
import matplotlib.pyplot as plt
from scipy.interpolate import griddata
from scipy.ndimage import gaussian_filter

DB_PATH = os.path.join(os.path.dirname(__file__), "eastern_rain.db")
SHP_PATH = os.path.join(os.path.dirname(__file__), "Shapefile", "Amphoe", "Amphoe_Province_RIO9_WGS1984.shp")
PROV_SHP_PATH = os.path.join(os.path.dirname(__file__), "Shapefile", "Province", "Province_RIO9_WGS1984.shp")
OUTPUT_HTML = os.path.join(os.path.dirname(__file__), "dashboard.html")

def compute_smooth_contours(grid_points, dates):
    contours_by_date = {}
    levels = [0.1, 10, 20, 35, 50, 70, 90, 500]
    
    if not grid_points or not dates:
        return contours_by_date
        
    real_lats = [pt["lat"] for pt in grid_points]
    real_lons = [pt["lon"] for pt in grid_points]
    
    min_lat, max_lat = min(real_lats), max(real_lats)
    min_lon, max_lon = min(real_lons), max(real_lons)
    
    grid_lat, grid_lon = np.mgrid[min_lat:max_lat:160j, min_lon:max_lon:160j]
    
    for d in dates:
        real_rains = [pt["rain"].get(d, 0.0) for pt in grid_points]
        
        grid_z = griddata((real_lats, real_lons), real_rains, (grid_lat, grid_lon), method='linear', fill_value=0.0)
        grid_z = gaussian_filter(grid_z, sigma=1.2)
        
        cs = plt.contourf(grid_lon, grid_lat, grid_z, levels=levels)
        
        date_polys = []
        for idx, segs in enumerate(cs.allsegs):
            level_val = levels[idx]
            for seg in segs:
                if len(seg) < 3:
                    continue
                step = 2 if len(seg) > 80 else 1
                poly = [[round(float(p[1]), 4), round(float(p[0]), 4)] for p in seg[::step]]
                if poly[0] != poly[-1]:
                    poly.append(poly[0])
                date_polys.append({
                    "level": level_val,
                    "coords": poly
                })
        plt.close()
        contours_by_date[d] = date_polys
    return contours_by_date

def clean_ring(ring):
    new_ring = []
    for pt in ring:
        r_pt = [round(pt[0], 4), round(pt[1], 4)]
        if not new_ring or new_ring[-1] != r_pt:
            new_ring.append(r_pt)
    return new_ring if len(new_ring) >= 4 else ring

def process_geometry(geom):
    g_type = geom.get("type")
    coords = geom.get("coordinates", [])
    if g_type == "Polygon":
        new_coords = [clean_ring(ring) for ring in coords]
    elif g_type == "MultiPolygon":
        new_coords = [[clean_ring(ring) for ring in poly] for poly in coords]
    else:
        new_coords = coords
    return {"type": g_type, "coordinates": new_coords}

def get_geojson_data():
    if not os.path.exists(SHP_PATH):
        print(f"[!] ไม่พบ Shapefile ที่: {SHP_PATH}")
        return {"type": "FeatureCollection", "features": []}
        
    sf = shapefile.Reader(SHP_PATH, encoding='cp874')
    features = []
    for r, s in zip(sf.records(), sf.shapes()):
        rec = r.as_dict()
        prov = rec.get('PROV_NAM_T', '').replace('จ.', '').strip()
        amp = rec.get('AMP_NAM_T', '').replace('กิ่ง อ.', '').replace('อ.', '').replace('กิ่ง', '').strip()
        
        geom = process_geometry(s.__geo_interface__)
        features.append({
            "type": "Feature",
            "properties": {"prov": prov, "amp": amp},
            "geometry": geom
        })
    return {"type": "FeatureCollection", "features": features}

def get_province_geojson_data():
    if not os.path.exists(PROV_SHP_PATH):
        print(f"[!] ไม่พบ Shapefile จังหวัดที่: {PROV_SHP_PATH}")
        return {"type": "FeatureCollection", "features": []}
        
    sf = shapefile.Reader(PROV_SHP_PATH, encoding='cp874')
    features = []
    for r, s in zip(sf.records(), sf.shapes()):
        rec = r.as_dict()
        prov = rec.get('PROV_NAM_T', '').replace('จ.', '').strip()
        
        geom = process_geometry(s.__geo_interface__)
        features.append({
            "type": "Feature",
            "properties": {"prov": prov},
            "geometry": geom
        })
    return {"type": "FeatureCollection", "features": features}

def get_dashboard_data():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("SELECT province_name, name_th, lat, lon FROM amphoes ORDER BY province_name, name_th")
    amphoes_raw = cursor.fetchall()
    
    provinces_dict = {}
    for p_name, a_name, lat, lon in amphoes_raw:
        if p_name not in provinces_dict:
            provinces_dict[p_name] = []
        provinces_dict[p_name].append({"name": a_name, "lat": lat, "lon": lon})
        
    today_str = datetime.now(THAI_TZ).strftime("%Y-%m-%d")
    cursor.execute("SELECT DISTINCT forecast_date FROM spatial_amphoe_summary WHERE forecast_date >= ? ORDER BY forecast_date", (today_str,))
    dates = [row[0] for row in cursor.fetchall()]
    if not dates:
        cursor.execute("SELECT DISTINCT forecast_date FROM rain_forecast_daily WHERE forecast_date >= ? ORDER BY forecast_date", (today_str,))
        dates = [row[0] for row in cursor.fetchall()]
    
    cursor.execute("SELECT province_name, amphoe_name, forecast_date, avg_rain_mm FROM spatial_amphoe_summary WHERE forecast_date >= ?", (today_str,))
    forecasts_raw = cursor.fetchall()
    
    data_matrix = {}
    for p_name, a_name, f_date, rain in forecasts_raw:
        if p_name not in data_matrix:
            data_matrix[p_name] = {}
        if a_name not in data_matrix[p_name]:
            data_matrix[p_name][a_name] = {}
        data_matrix[p_name][a_name][f_date] = {"rain": rain, "cond": None}
        
    cursor.execute("SELECT province_name, forecast_date, avg_rain_mm FROM spatial_province_summary WHERE forecast_date >= ?", (today_str,))
    prov_raw = cursor.fetchall()
    
    province_summary = {p: {} for p in provinces_dict}
    for p_name, f_date, avg_rain in prov_raw:
        if p_name not in province_summary:
            province_summary[p_name] = {}
        province_summary[p_name][f_date] = avg_rain

    cursor.execute("SELECT p.lat, p.lon, f.forecast_date, f.rain_mm FROM grid_points p JOIN grid_forecast_daily f ON p.point_id = f.point_id WHERE f.forecast_date >= ?", (today_str,))
    grid_raw = cursor.fetchall()
    grid_list = {}
    for lat, lon, f_date, rain in grid_raw:
        ck = f"{lat},{lon}"
        if ck not in grid_list:
            grid_list[ck] = {"lat": lat, "lon": lon, "rain": {}}
        grid_list[ck]["rain"][f_date] = rain

    cursor.execute("SELECT MAX(fetched_at) FROM anchor_forecast_daily")
    row_fetch = cursor.fetchone()
    raw_fetch_time = row_fetch[0] if row_fetch and row_fetch[0] else datetime.now(THAI_TZ).strftime("%Y-%m-%d %H:%M:%S")
    
    def get_model_cycle_thai(dt_str):
        try:
            from datetime import timedelta
            dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
            hour = dt.hour
            if hour < 1:
                dt = dt - timedelta(days=1)
                cycle = "19:00 น."
            elif hour < 7:
                cycle = "01:00 น."
            elif hour < 13:
                cycle = "07:00 น."
            elif hour < 19:
                cycle = "13:00 น."
            else:
                cycle = "19:00 น."
            thai_months = ['', 'ม.ค.', 'ก.พ.', 'มี.ค.', 'เม.ย.', 'พ.ค.', 'มิ.ย.', 'ก.ค.', 'ส.ค.', 'ก.ย.', 'ต.ค.', 'พ.ย.', 'ธ.ค.']
            thai_year = (dt.year + 543) % 100
            return f"วันที่ {dt.day} {thai_months[dt.month]} {thai_year} (รอบเวลา {cycle})"
        except Exception:
            return dt_str

    model_time_str = get_model_cycle_thai(raw_fetch_time)

    extreme_alerts = []
    for p_name, amp_dict in data_matrix.items():
        for a_name, date_dict in amp_dict.items():
            for f_date, val in date_dict.items():
                r = val.get("rain", 0)
                if r >= 90.0:
                    extreme_alerts.append({
                        "province": p_name,
                        "amphoe": a_name,
                        "date": f_date,
                        "rain": r
                    })
    extreme_alerts.sort(key=lambda x: x["rain"], reverse=True)

    conn.close()
    
    grid_pts = list(grid_list.values())
    print("[*] กำลังคำนวณเส้นชั้นข้อมูลฝนโค้งมน (Smooth Isohyet Contours)...")
    contours = compute_smooth_contours(grid_pts, dates)

    DESIRED_ORDER = ['ฉะเชิงเทรา', 'ชลบุรี', 'ระยอง', 'จันทบุรี', 'ตราด', 'สระแก้ว', 'ปราจีนบุรี', 'นครนายก']
    ordered_provinces = {p: provinces_dict[p] for p in DESIRED_ORDER if p in provinces_dict}
    for p in provinces_dict:
        if p not in ordered_provinces:
            ordered_provinces[p] = provinces_dict[p]

    ordered_matrix = {p: data_matrix[p] for p in DESIRED_ORDER if p in data_matrix}
    for p in data_matrix:
        if p not in ordered_matrix:
            ordered_matrix[p] = data_matrix[p]

    ordered_summary = {p: province_summary[p] for p in DESIRED_ORDER if p in province_summary}
    for p in province_summary:
        if p not in ordered_summary:
            ordered_summary[p] = province_summary[p]

    return {
        "dates": dates,
        "provinces": ordered_provinces,
        "matrix": ordered_matrix,
        "summary": ordered_summary,
        "grid_points": grid_pts,
        "contours": contours,
        "generated_at": datetime.now(THAI_TZ).strftime("%d/%m/%Y %H:%M:%S"),
        "model_updated_at": model_time_str,
        "extreme_alerts": extreme_alerts
    }

def generate_html(data, geojson_data, prov_geojson_data):
    json_str = json.dumps(data, ensure_ascii=False)
    geojson_str = json.dumps(geojson_data, ensure_ascii=False)
    prov_geojson_str = json.dumps(prov_geojson_data, ensure_ascii=False)
    
    html_content = f"""<!DOCTYPE html>
<html lang="th">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">
    <meta http-equiv="Pragma" content="no-cache">
    <meta http-equiv="Expires" content="0">
    <script>
        // ระบบอัจฉริยะล้างความจำมือถือและคอมพิวเตอร์อัตโนมัติ (Self-Healing Cache Buster): หากเปิดเว็บโดยไม่มีรหัสเวลา หรือรหัสเวลาเก่าเกิน 5 นาที จะบังคับโหลดไฟล์ใหม่จากเซิร์ฟเวอร์ทันที
        (function() {{
            const params = new URLSearchParams(window.location.search);
            const t = params.get('t');
            const now = new Date().getTime();
            if (!t || (now - parseInt(t)) > 300000) {{
                window.location.replace(window.location.pathname + '?t=' + now);
            }}
        }})();
    </script>
    <title>ระบบสารสนเทศภูมิศาสตร์พยากรณ์และวิเคราะห์ปริมาณฝนล่วงหน้า 9 วัน ภาคตะวันออก</title>
    <!-- Google Fonts -->
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600;700&family=Prompt:wght@300;400;500;600;700&display=swap" rel="stylesheet">
    <!-- Chart.js -->
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <!-- Leaflet CSS & JS -->
    <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
    <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
    <!-- html2canvas -->
    <script src="https://cdnjs.cloudflare.com/ajax/libs/html2canvas/1.4.1/html2canvas.min.js"></script>
    <style>
        :root {{
            --bg-primary: #f4f6f9;
            --bg-secondary: #e2e8f0;
            --bg-card: #ffffff;
            --accent-cyan: #0284c7;
            --accent-blue: #0369a1;
            --accent-glow: rgba(2, 132, 199, 0.15);
            --text-main: #0f172a;
            --text-muted: #475569;
            --border-glass: rgba(0, 0, 0, 0.08);
            --shadow-card: 0 4px 20px rgba(0, 0, 0, 0.04);
        }}

        * {{
            box-sizing: border-box;
            margin: 0;
            padding: 0;
            font-family: 'Prompt', 'Outfit', sans-serif;
        }}

        body {{
            background: var(--bg-primary);
            color: var(--text-main);
            min-height: 100vh;
            padding: 2rem;
            overflow-x: hidden;
        }}

        .glow-bg {{
            position: fixed;
            width: 600px;
            height: 600px;
            background: radial-gradient(circle, rgba(2, 132, 199, 0.06) 0%, rgba(0,0,0,0) 70%);
            top: -200px;
            right: -200px;
            z-index: -1;
            pointer-events: none;
        }}
        .glow-bg-2 {{
            position: fixed;
            width: 500px;
            height: 500px;
            background: radial-gradient(circle, rgba(3, 105, 161, 0.05) 0%, rgba(0,0,0,0) 70%);
            bottom: -150px;
            left: -150px;
            z-index: -1;
            pointer-events: none;
        }}

        header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 2.5rem;
            border-bottom: 2px solid var(--border-glass);
            padding-bottom: 1.5rem;
            flex-wrap: wrap;
            gap: 1rem;
        }}

        .logo-area h1 {{
            font-size: 2.2rem;
            font-weight: 700;
            color: #0f172a;
            display: flex;
            align-items: center;
            gap: 12px;
        }}

        .logo-area p {{
            color: var(--text-muted);
            font-size: 0.95rem;
            margin-top: 4px;
        }}

        .status-badge {{
            background: #e0f2fe;
            border: 1px solid #bae6fd;
            padding: 8px 16px;
            border-radius: 50px;
            font-size: 0.85rem;
            color: #0284c7;
            display: flex;
            align-items: center;
            gap: 8px;
            box-shadow: 0 2px 10px rgba(2, 132, 199, 0.08);
            font-weight: 500;
        }}
        .status-dot {{
            width: 8px;
            height: 8px;
            background: #0284c7;
            border-radius: 50%;
            animation: pulse 2s infinite;
        }}

        @keyframes pulse {{
            0% {{ transform: scale(0.95); box-shadow: 0 0 0 0 rgba(2, 132, 199, 0.4); }}
            70% {{ transform: scale(1); box-shadow: 0 0 0 6px rgba(2, 132, 199, 0); }}
            100% {{ transform: scale(0.95); box-shadow: 0 0 0 0 rgba(2, 132, 199, 0); }}
        }}

        .kpi-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
            gap: 1.5rem;
            margin-bottom: 2.5rem;
        }}

        .kpi-card {{
            background: var(--bg-card);
            box-shadow: var(--shadow-card);
            border: 1px solid var(--border-glass);
            border-radius: 20px;
            padding: 1.5rem;
            transition: transform 0.3s ease, border-color 0.3s ease;
        }}
        .kpi-card:hover {{
            transform: translateY(-5px);
            border-color: var(--accent-cyan);
        }}
        .kpi-title {{
            color: var(--text-muted);
            font-size: 0.9rem;
            margin-bottom: 0.5rem;
            font-weight: 500;
        }}
        .kpi-value {{
            font-size: 2rem;
            font-weight: 700;
            color: #0f172a;
        }}
        .kpi-sub {{
            font-size: 0.85rem;
            color: var(--accent-cyan);
            margin-top: 0.5rem;
            font-weight: 500;
        }}

        /* Map Section */
        .map-section {{
            background: var(--bg-card);
            box-shadow: var(--shadow-card);
            border: 1px solid var(--border-glass);
            border-radius: 24px;
            padding: 2rem;
            margin-bottom: 2.5rem;
        }}

        .map-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 1.5rem;
            flex-wrap: wrap;
            gap: 1rem;
        }}

        .map-header h2 {{
            font-size: 1.4rem;
            color: #0f172a;
        }}

        .date-selector-map select {{
            background: #ffffff;
            border: 1px solid #cbd5e1;
            color: #0f172a;
            padding: 8px 16px;
            border-radius: 10px;
            font-size: 1rem;
            cursor: pointer;
            outline: none;
            box-shadow: 0 2px 6px rgba(0,0,0,0.03);
        }}

        .layer-btn {{
            background: #f1f5f9;
            color: #475569;
            border: none;
            padding: 8px 16px;
            font-family: 'Prompt', sans-serif;
            font-size: 0.9rem;
            cursor: pointer;
            transition: all 0.3s ease;
        }}
        .layer-btn:hover {{
            background: #e2e8f0;
        }}
        .layer-btn.active {{
            background: var(--accent-cyan);
            color: #ffffff;
            font-weight: 600;
        }}

        #rainMap {{
            width: 100%;
            height: 580px;
            border-radius: 16px;
            border: 1px solid var(--border-glass);
            z-index: 1;
        }}

        .map-legend {{
            display: flex;
            flex-wrap: wrap;
            gap: 18px;
            margin-top: 1rem;
            padding-top: 1rem;
            border-top: 1px solid #e2e8f0;
            font-size: 0.88rem;
            color: #334155;
            align-items: center;
        }}
        .map-legend span {{
            display: flex;
            align-items: center;
            gap: 8px;
        }}
        .map-legend i {{
            width: 18px;
            height: 18px;
            border-radius: 4px;
            display: inline-block;
        }}

        /* Navigation Pills */
        .nav-pills {{
            display: flex;
            flex-wrap: wrap;
            gap: 10px;
            margin-bottom: 2rem;
        }}

        .pill-btn {{
            background: #ffffff;
            border: 1px solid #cbd5e1;
            color: #334155;
            padding: 10px 20px;
            border-radius: 12px;
            cursor: pointer;
            font-size: 0.95rem;
            font-weight: 500;
            transition: all 0.3s ease;
            box-shadow: 0 2px 6px rgba(0,0,0,0.02);
        }}
        .pill-btn:hover {{
            background: #f8fafc;
            border-color: #94a3b8;
        }}
        .pill-btn.active {{
            background: var(--accent-cyan);
            color: #ffffff;
            border-color: var(--accent-cyan);
            box-shadow: 0 4px 12px rgba(2, 132, 199, 0.2);
        }}

        .chart-container {{
            background: var(--bg-card);
            box-shadow: var(--shadow-card);
            border: 1px solid var(--border-glass);
            border-radius: 24px;
            padding: 2rem;
            margin-bottom: 2.5rem;
            height: auto;
            min-height: 440px;
        }}

        .table-container {{
            background: var(--bg-card);
            box-shadow: var(--shadow-card);
            border: 1px solid var(--border-glass);
            border-radius: 24px;
            padding: 2rem;
            overflow-x: auto;
        }}

        .table-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 1.5rem;
            flex-wrap: wrap;
            gap: 1rem;
        }}

        .table-header h2 {{
            font-size: 1.4rem;
            color: #0f172a;
        }}

        .search-input {{
            background: #f8fafc;
            border: 1px solid #cbd5e1;
            color: #0f172a;
            padding: 10px 16px;
            border-radius: 10px;
            outline: none;
            width: 280px;
            transition: border-color 0.3s ease;
        }}
        .search-input:focus {{
            border-color: var(--accent-cyan);
            background: #ffffff;
        }}

        .btn-capture {{
            background: linear-gradient(135deg, #0284c7, #0369a1);
            color: #ffffff;
            border: none;
            padding: 9px 16px;
            border-radius: 10px;
            font-family: 'Prompt', sans-serif;
            font-size: 0.9rem;
            font-weight: 600;
            cursor: pointer;
            box-shadow: 0 2px 6px rgba(2, 132, 199, 0.2);
            transition: all 0.2s ease;
            display: inline-flex;
            align-items: center;
            gap: 6px;
        }}
        .btn-capture:hover {{
            background: linear-gradient(135deg, #0369a1, #075985);
            transform: translateY(-1px);
            box-shadow: 0 4px 10px rgba(2, 132, 199, 0.3);
        }}
        .btn-capture:active {{
            transform: translateY(0);
        }}

        table {{
            width: 100%;
            border-collapse: collapse;
            text-align: left;
        }}

        th {{
            padding: 14px;
            border-bottom: 2px solid #cbd5e1;
            color: #0369a1;
            font-weight: 600;
            white-space: nowrap;
            background: #f8fafc;
        }}

        td {{
            padding: 12px 14px;
            border-bottom: 1px solid #e2e8f0;
            font-size: 0.95rem;
        }}

        .card-footer-info {{
            margin-top: 16px;
            padding-top: 12px;
            border-top: 1px dashed #cbd5e1;
            font-size: 0.88rem;
            color: #475569;
            display: flex;
            align-items: center;
            gap: 6px;
        }}

        tr:hover td {{
            background: #cbd5e1 !important;
            color: #000000 !important;
        }}

        /* Custom Leaflet Tooltip & Map Labels */
        .leaflet-popup-content-wrapper {{
            background: #ffffff !important;
            color: #0f172a !important;
            border-radius: 12px !important;
            box-shadow: 0 4px 15px rgba(0,0,0,0.1) !important;
        }}
        .map-label {{
            background: rgba(255, 255, 255, 0.88) !important;
            border: 1px solid #94a3b8 !important;
            border-radius: 4px !important;
            box-shadow: 0 1px 3px rgba(0,0,0,0.15) !important;
            color: #0f172a !important;
            font-family: 'Prompt', sans-serif !important;
            font-size: 0.72rem !important;
            font-weight: 600 !important;
            padding: 1px 4px !important;
            pointer-events: none;
        }}
        .map-label::before {{
            display: none !important;
        }}
        .province-label {{
            font-size: 0.95rem !important;
            font-weight: 700 !important;
            color: #0284c7 !important;
            border: 1.5px solid #0284c7 !important;
            background: rgba(255, 255, 255, 0.93) !important;
            padding: 2px 7px !important;
        .leaflet-popup-content-wrapper {{
            border: 1px solid #cbd5e1;
            border-radius: 12px;
            padding: 6px;
            box-shadow: 0 6px 24px rgba(0,0,0,0.1);
        }}
        .leaflet-popup-tip {{
            background: #ffffff !important;
        }}
        @media (max-width: 768px) {{
            body {{
                padding: 10px !important;
            }}
            .main-header h1 {{
                font-size: 1.35rem !important;
                line-height: 1.4 !important;
            }}
            .main-header .subtitle {{
                font-size: 0.85rem !important;
            }}
            .kpi-container {{
                grid-template-columns: repeat(2, 1fr) !important;
                gap: 10px !important;
            }}
            .kpi-card {{
                padding: 12px !important;
            }}
            .kpi-title {{
                font-size: 0.8rem !important;
            }}
            .kpi-value {{
                font-size: 1.3rem !important;
            }}
            .map-header, .table-header {{
                flex-direction: column !important;
                align-items: flex-start !important;
                gap: 12px !important;
            }}
            .layer-btn {{
                font-size: 0.75rem !important;
                padding: 6px 10px !important;
            }}
            #rainMap {{
                height: 400px !important;
            }}
            .map-legend {{
                font-size: 0.75rem !important;
                gap: 8px !important;
                padding: 8px !important;
                justify-content: flex-start !important;
            }}
            .table-container {{
                overflow-x: auto !important;
                -webkit-overflow-scrolling: touch !important;
            }}
            #dataTable {{
                min-width: 680px !important;
                font-size: 0.82rem !important;
            }}
            #dataTable th, #dataTable td {{
                padding: 8px 6px !important;
            }}
            .search-input {{
                width: 100% !important;
            }}
            .province-label {{
                font-size: 0.72rem !important;
                padding: 1px 4px !important;
                border-width: 1px !important;
            }}
            .amphoe-label {{
                font-size: 0.60rem !important;
                padding: 0px 2px !important;
            }}
        }}
    </style>
</head>
<body>
    <div class="glow-bg"></div>
    <div class="glow-bg-2"></div>

    <header>
        <div class="logo-area">
            <h1>🛰️ ระบบสารสนเทศภูมิศาสตร์พยากรณ์และวิเคราะห์ปริมาณฝนล่วงหน้า 9 วัน ภาคตะวันออก</h1>
            <p>Eastern Thailand 9-Day NWP Rain Forecasting & GIS Analytics Portal (TMD High-Resolution Model)</p>
        </div>
        <div class="status-badge">
            <div class="status-dot"></div>
            <span>ข้อมูลอัปเดตล่าสุด: <strong id="gen-time"></strong></span>
        </div>
    </header>


    <div class="kpi-grid">
        <div class="kpi-card">
            <div class="kpi-title">📍 พื้นที่ติดตามทั้งหมด</div>
            <div class="kpi-value" id="kpi-provinces">8 จังหวัด</div>
            <div class="kpi-sub" id="kpi-amphoes">ครอบคลุม 67 อำเภอ</div>
        </div>
        <div class="kpi-card">
            <div class="kpi-title">🌧️ จังหวัดฝนตกชุกสุดวันนี้</div>
            <div class="kpi-value" id="kpi-max-today">-</div>
            <div class="kpi-sub" id="kpi-max-today-val">เฉลี่ย 0.00 มม.</div>
        </div>
        <div class="kpi-card">
            <div class="kpi-title">⚠️ เฝ้าระวังฝนหนักสูงสุดล่วงหน้า</div>
            <div class="kpi-value" id="kpi-max-trend">-</div>
            <div class="kpi-sub" id="kpi-max-trend-val">พยากรณ์ล่วงหน้า 9 วัน</div>
        </div>
        <div class="kpi-card">
            <div class="kpi-title">📅 ช่วงเวลาพยากรณ์ล่วงหน้า</div>
            <div class="kpi-value" id="kpi-days">9 วัน</div>
            <div class="kpi-sub" id="kpi-date-range">...</div>
        </div>
    </div>

    <!-- Map Section -->
    <div class="map-section" id="map-container-capture">
        <div class="map-header">
            <div>
                <h2>🗺️ แผนที่พยากรณ์ปริมาณฝนภาคตะวันออก (Interactive GIS Map)</h2>
                <div style="display:flex; gap:10px; align-items:center; margin-top:10px; flex-wrap:wrap;">
                    <span style="color:#475569; font-size:0.9rem; font-weight:500;">รูปแบบชั้นข้อมูลแผนที่:</span>
                    <div style="display:flex; background:#ffffff; border:1px solid var(--accent-cyan); border-radius:10px; overflow:hidden; box-shadow: 0 2px 6px rgba(0,0,0,0.03);">
                        <button type="button" class="layer-btn active" id="btnLayerAmphoe" onclick="switchMapLayer('amphoe')">🗺️ อำเภอ</button>
                        <button type="button" class="layer-btn" id="btnLayerProvince" onclick="switchMapLayer('province')">🌐 จังหวัด</button>
                        <button type="button" class="layer-btn" id="btnLayerSmooth" onclick="switchMapLayer('smooth')">🌈 เส้นชั้นน้ำฝน</button>
                        <button type="button" class="layer-btn" id="btnLayerIsohyet" onclick="switchMapLayer('isohyet')">📦 Grid</button>
                        <button type="button" class="layer-btn" id="btnLayerPoints" style="display:none;" onclick="switchMapLayer('points')">📍 จุดกริดพยากรณ์ (3,762 จุด)</button>
                    </div>
                </div>
            </div>
            <div style="display:flex; gap:12px; align-items:center; flex-wrap:wrap;">
                <div class="date-selector-map" style="display:flex; align-items:center; gap:8px;">
                    <label style="font-weight:500; color:#0f172a;">📅 เลือกวันที่แสดงบนแผนที่: </label>
                    <select id="mapDateSelect" onchange="updateMapDate()"></select>
                </div>
                <label style="font-weight:600; color:#0f172a; background:#f1f5f9; padding:6px 12px; border-radius:8px; border:1px solid #cbd5e1; cursor:pointer; display:inline-flex; align-items:center; gap:6px;">
                    <input type="checkbox" id="showLabelsToggle" checked onchange="renderMapLayer()" style="cursor:pointer; width:16px; height:16px; accent-color:#0284c7;"> 🏷️ แสดงป้ายชื่อ
                </label>
                <button type="button" class="btn-capture no-capture" onclick="captureElement('map-container-capture', 'แผนที่พยากรณ์ปริมาณฝนภาคตะวันออก')">📸 (.PNG)</button>
            </div>
        </div>
        <div id="rainMap"></div>
        <div class="map-legend">
            <span><i style="background:#FF0000; border:1px solid #cbd5e1"></i> หนักมากสุด (> 90 มม.)</span>
            <span><i style="background:#FF00FF; border:1px solid #cbd5e1"></i> หนักมาก (70-90 มม.)</span>
            <span><i style="background:#FFC000; border:1px solid #cbd5e1"></i> หนัก (50-70 มม.)</span>
            <span><i style="background:#FFFF00; border:1px solid #cbd5e1"></i> ค่อนข้างหนัก (35-50 มม.)</span>
            <span><i style="background:#00B050; border:1px solid #cbd5e1"></i> ปานกลางมาก (20-35 มม.)</span>
            <span><i style="background:#CCFFCC; border:1px solid #cbd5e1"></i> ปานกลาง (10-20 มม.)</span>
            <span><i style="background:#E0F2FE; border:1px solid #cbd5e1"></i> เล็กน้อย (0.1-10 มม.)</span>
            <span><i style="background:#FFFFFF; border:1px solid #cbd5e1"></i> ปลอดฝน (0 มม.)</span>
        </div>
        <div class="card-footer-info" id="map-cycle-info"></div>
    </div>

    <div class="nav-pills" id="province-pills">
        <button class="pill-btn active" onclick="selectProvince('ALL')">🌐 กราฟภาพรวมทุกจังหวัด</button>
    </div>

    <div class="chart-container" id="chart-container-capture">
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:16px; flex-wrap:wrap; gap:10px;">
            <h3 style="color:#0f172a; font-size:1.15rem; font-weight:600; display:flex; align-items:center; gap:8px;">📈 กราฟพยากรณ์ปริมาณฝนล่วงหน้า</h3>
            <button type="button" class="btn-capture no-capture" onclick="captureElement('chart-container-capture', 'กราฟพยากรณ์ปริมาณฝน')">📸 (.PNG)</button>
        </div>
        <div style="position:relative; height:380px; width:100%;">
            <canvas id="mainChart"></canvas>
        </div>
        <div class="card-footer-info" id="chart-cycle-info"></div>
    </div>

    <div class="table-container" id="table-container-capture">
        <div class="table-header">
            <div>
                <h2 id="table-title">📊 ตารางรายงานปริมาณฝนพยากรณ์ (มม.)</h2>
                <div style="display:flex; gap:10px; align-items:center; margin-top:8px; flex-wrap:wrap;" class="no-capture">
                    <span style="color:#475569; font-size:0.9rem; font-weight:600;">เลือกรูปแบบตาราง:</span>
                    <div style="display:flex; background:#ffffff; border:1px solid var(--accent-cyan); border-radius:10px; overflow:hidden; box-shadow: 0 2px 6px rgba(0,0,0,0.03); flex-wrap:wrap;">
                        <button type="button" class="layer-btn active" id="btnTableAmphoe" onclick="switchTableMode('amphoe')">🏢 ฝนรายวัน (รายอำเภอ)</button>
                        <button type="button" class="layer-btn" id="btnTableProvince" onclick="switchTableMode('province')">🌐 ฝนรายวัน (รายจังหวัด)</button>
                        <button type="button" class="layer-btn" id="btnTableAmphoeCum" onclick="switchTableMode('amphoe_cum')">🌧️ ฝนสะสม (รายอำเภอ)</button>
                        <button type="button" class="layer-btn" id="btnTableProvinceCum" onclick="switchTableMode('province_cum')">🌊 ฝนสะสม (รายจังหวัด)</button>
                    </div>
                </div>
            </div>
            <div style="display:flex; gap:10px; align-items:center; flex-wrap:wrap;">
                <input type="text" id="searchInput" class="search-input no-capture" placeholder="🔍 ค้นหาชื่ออำเภอ หรือจังหวัด..." oninput="filterTable()">
                <button type="button" class="btn-capture no-capture" onclick="captureElement('table-container-capture', 'ตารางปริมาณฝนพยากรณ์')">📸 (.PNG)</button>
            </div>
        </div>
        <table id="dataTable">
            <thead>
                <tr id="table-head-row">
                    <th>จังหวัด</th>
                    <th>อำเภอ</th>
                </tr>
            </thead>
            <tbody id="table-body">
            </tbody>
        </table>
        <div class="card-footer-info" id="table-cycle-info"></div>
    </div>

    <!-- Alert Banner Moved to Bottom -->
    <div id="alert-banner-container" style="margin-top: 24px;"></div>

    <!-- Agency Footer -->
    <footer style="margin-top: 36px; padding: 26px 20px; background: linear-gradient(135deg, #ffffff 0%, #f8fafc 100%); border-radius: 16px; border: 1px solid #e2e8f0; text-align: center; box-shadow: 0 4px 15px rgba(0,0,0,0.03);">
        <div style="display: inline-flex; align-items: center; gap: 8px; justify-content: center;">
            <span style="font-size: 1.4rem;">🌊</span>
            <span style="font-weight: 700; font-size: 1.08rem; color: #0284c7; letter-spacing: 0.2px;">ส่วนบริหารจัดการน้ำและบำรุงรักษา สำนักงานชลประทานที่ 9 กรมชลประทาน</span>
        </div>
        <div style="font-size: 0.88rem; color: #475569; margin-top: 6px; font-weight: 500;">ระบบสารสนเทศภูมิศาสตร์สนับสนุนการตัดสินใจบริหารจัดการน้ำลุ่มน้ำภาคตะวันออก (Eastern Water Management Intelligence Portal)</div>
        <div style="font-size: 0.80rem; color: #94a3b8; margin-top: 4px;">ข้อมูลแบบจำลองคณิตศาสตร์ความละเอียดสูง (NWP) จากกรมอุตุนิยมวิทยา | ประมวลผลและแสดงผลเชิงพื้นที่อัตโนมัติ</div>
    </footer>

    <script>
        const RAW_DATA = {json_str};
        const GEOJSON_DATA = {geojson_str};
        const PROV_GEOJSON_DATA = {prov_geojson_str};
        let currentProvince = 'ALL';
        let chartInstance = null;
        let mapInstance = null;
        let geojsonLayer = null;
        let activeMapGroup = null;
        let currentLayerMode = 'amphoe';
        let currentMapDate = '';
        let currentTableMode = 'amphoe';

        function formatDateThai(dateStr) {{
            if (!dateStr || typeof dateStr !== 'string') return dateStr;
            const parts = dateStr.split('-');
            if (parts.length !== 3) return dateStr;
            const year = parseInt(parts[0], 10);
            const month = parseInt(parts[1], 10);
            const day = parseInt(parts[2], 10);
            const thaiMonths = ['', 'ม.ค.', 'ก.พ.', 'มี.ค.', 'เม.ย.', 'พ.ค.', 'มิ.ย.', 'ก.ค.', 'ส.ค.', 'ก.ย.', 'ต.ค.', 'พ.ย.', 'ธ.ค.'];
            const thaiYear = (year + 543) % 100;
            return `${{day}} ${{thaiMonths[month]}} ${{thaiYear}}`;
        }}

        function initDashboard() {{
            document.getElementById('gen-time').innerHTML = `${{RAW_DATA.generated_at}} <span style="background:#f0f9ff; color:#0369a1; border: 1px solid #7dd3fc; padding: 3px 12px; border-radius: 20px; margin-left: 8px; font-weight: 600; font-size: 0.85rem; display: inline-flex; align-items: center; gap: 4px;">🎯 รอบการจำลอง NWP กรมอุตุฯ: <b>${{RAW_DATA.model_updated_at}}</b></span>`;
            
            const cycleFooterText = `🎯 <b>ข้อมูลรอบการจำลอง NWP กรมอุตุนิยมวิทยา:</b> <span style="color:#0284c7; font-weight:600;">${{RAW_DATA.model_updated_at}}</span>`;
            ['map-cycle-info', 'chart-cycle-info', 'table-cycle-info'].forEach(id => {{
                const el = document.getElementById(id);
                if (el) el.innerHTML = cycleFooterText;
            }});
            
            const alertCont = document.getElementById('alert-banner-container');
            if (alertCont) {{
                if (RAW_DATA.extreme_alerts && RAW_DATA.extreme_alerts.length > 0) {{
                    const topAlerts = RAW_DATA.extreme_alerts.slice(0, 6).map(a => `<b>จ.${{a.province}} (อ.${{a.amphoe}})</b> วันที่ ${{formatDateThai(a.date)}} (<span style="color:#b91c1c; font-weight:bold;">${{a.rain.toFixed(1)}} มม.</span>)`).join(', ');
                    alertCont.innerHTML = `
                        <div style="background: #fff5f5; border: 1px solid #fecaca; border-left: 5px solid #ef4444; padding: 14px 18px; border-radius: 12px; font-size: 0.92rem; color: #7f1d1d; box-shadow: 0 2px 8px rgba(0,0,0,0.03);">
                            <div style="display:flex; align-items:flex-start; gap:10px;">
                                <span style="font-size: 1.25rem;">ℹ️</span>
                                <div>
                                    <div style="font-weight: 700; color: #991b1b; font-size: 0.98rem;">ข้อสังเกตพื้นที่เฝ้าระวังปริมาณฝน (คาดการณ์ตามแบบจำลอง > 90 มม./วัน)</div>
                                    <div style="margin-top: 3px; color: #7f1d1d;">พื้นที่ที่มีแนวโน้มฝนตกหนัก: ${{topAlerts}}</div>
                                    <div style="color: #64748b; font-size: 0.82rem; margin-top: 6px; font-style: italic;">📌 หมายเหตุสำคัญ: ข้อมูลปริมาณฝนพยากรณ์ล่วงหน้านี้อ้างอิงจากผลแบบจำลองคณิตศาสตร์ (NWP) ของกรมอุตุนิยมวิทยา จัดทำขึ้นเพื่อใช้สนับสนุนการติดตาม เฝ้าระวัง และเตรียมความพร้อมบริหารจัดการน้ำในพื้นที่รับผิดชอบของสำนักงานชลประทานที่ 9 ทั้งนี้ ค่าพยากรณ์อาจมีความคลาดเคลื่อนหรือเปลี่ยนแปลงตามปัจจัยสภาพอากาศในแต่ละรอบการจำลอง โปรดใช้เพื่อประเมินแนวโน้มและตรวจสอบร่วมกับข้อมูลสถานีตรวจวัดจริงในพื้นที่</div>
                                </div>
                            </div>
                        </div>
                    `;
                }} else {{
                    alertCont.innerHTML = `
                        <div style="background: #f8fafc; border: 1px solid #e2e8f0; border-left: 5px solid #10b981; padding: 14px 18px; border-radius: 12px; font-size: 0.92rem; color: #334155; box-shadow: 0 2px 8px rgba(0,0,0,0.02);">
                            <div style="display:flex; align-items:flex-start; gap:10px;">
                                <span style="font-size: 1.25rem;">ℹ️</span>
                                <div>
                                    <div style="font-weight: 700; color: #065f46; font-size: 0.98rem;">ข้อสังเกตพื้นที่เฝ้าระวังปริมาณฝน</div>
                                    <div style="margin-top: 3px; color: #334155;">ไม่พบพื้นที่พยากรณ์ฝนตกหนักมากเกณฑ์สีแดง (> 90 มม./วัน) ในรอบการจำลองปัจจุบัน</div>
                                    <div style="color: #64748b; font-size: 0.82rem; margin-top: 6px; font-style: italic;">📌 หมายเหตุสำคัญ: ข้อมูลปริมาณฝนพยากรณ์ล่วงหน้านี้อ้างอิงจากผลแบบจำลองคณิตศาสตร์ (NWP) ของกรมอุตุนิยมวิทยา จัดทำขึ้นเพื่อใช้สนับสนุนการติดตาม เฝ้าระวัง และเตรียมความพร้อมบริหารจัดการน้ำในพื้นที่รับผิดชอบของสำนักงานชลประทานที่ 9 ทั้งนี้ ค่าพยากรณ์อาจมีความคลาดเคลื่อนหรือเปลี่ยนแปลงตามปัจจัยสภาพอากาศในแต่ละรอบการจำลอง โปรดใช้เพื่อประเมินแนวโน้มและตรวจสอบร่วมกับข้อมูลสถานีตรวจวัดจริงในพื้นที่</div>
                                </div>
                            </div>
                        </div>
                    `;
                }}
            }}

            // Generate Province Pills
            const pillContainer = document.getElementById('province-pills');
            Object.keys(RAW_DATA.provinces).forEach(prov => {{
                const btn = document.createElement('button');
                btn.className = 'pill-btn';
                btn.innerText = prov;
                btn.onclick = () => selectProvince(prov);
                pillContainer.appendChild(btn);
            }});

            // Setup KPIs & Map Dates
            const dates = RAW_DATA.dates;
            currentMapDate = dates[0];
            const selectEl = document.getElementById('mapDateSelect');
            dates.forEach(d => {{
                const opt = document.createElement('option');
                opt.value = d;
                opt.innerText = `วันที่ ${{formatDateThai(d)}}`;
                selectEl.appendChild(opt);
            }});

            document.getElementById('kpi-date-range').innerText = `${{formatDateThai(dates[0])}} ถึง ${{formatDateThai(dates[dates.length-1])}}`;
            document.getElementById('kpi-days').innerText = `${{dates.length}} วัน`;

            // Max today
            const today = dates[0];
            let maxProv = '-';
            let maxVal = -1;
            Object.entries(RAW_DATA.summary).forEach(([p, dMap]) => {{
                if (dMap[today] > maxVal) {{
                    maxVal = dMap[today];
                    maxProv = p;
                }}
            }});
            document.getElementById('kpi-max-today').innerText = maxProv;
            document.getElementById('kpi-max-today-val').innerText = `เฉลี่ย ${{maxVal}} มม. (${{formatDateThai(today)}})`;

            // Max overall trend
            let peakProv = '-';
            let peakVal = -1;
            let peakDate = '';
            Object.entries(RAW_DATA.summary).forEach(([p, dMap]) => {{
                Object.entries(dMap).forEach(([d, val]) => {{
                    if (val > peakVal) {{
                        peakVal = val;
                        peakProv = p;
                        peakDate = d;
                    }}
                }});
            }});
            document.getElementById('kpi-max-trend').innerText = peakProv;
            document.getElementById('kpi-max-trend-val').innerText = `${{peakVal}} มม. วันที่ ${{formatDateThai(peakDate)}}`;

            // Initial Renders
            initMap();
            renderChart();
            renderTable();
        }}

        function initMap() {{
            const isMobile = window.innerWidth <= 768;
            mapInstance = L.map('rainMap', {{
                preferCanvas: true
            }}).setView(isMobile ? [12.9, 101.8] : [13.4, 102.1], isMobile ? 7.3 : 8);

            L.tileLayer('https://{{s}}.basemaps.cartocdn.com/light_all/{{z}}/{{x}}/{{y}}{{r}}.png', {{
                attribution: '&copy; OpenStreetMap & CartoDB | TMD Forecast API',
                crossOrigin: true
            }}).addTo(mapInstance);

            activeMapGroup = L.layerGroup().addTo(mapInstance);
            renderMapLayer();
        }}

        function getAmphoeRain(prov, amp, dateStr) {{
            return RAW_DATA.matrix[prov]?.[amp]?.[dateStr]?.rain ?? 0.0;
        }}

        function getMapColor(rain) {{
            if (rain >= 90) return '#FF0000';
            if (rain >= 70) return '#FF00FF';
            if (rain >= 50) return '#FFC000';
            if (rain >= 35) return '#FFFF00';
            if (rain >= 20) return '#00B050';
            if (rain >= 10) return '#CCFFCC';
            if (rain >= 0.1) return '#E0F2FE';
            return '#FFFFFF';
        }}

        function getRainDesc(rain) {{
            if (rain >= 90) return '⚠️ ฝนตกหนักมากสุด (>90 มม.)';
            if (rain >= 70) return '⚠️ ฝนตกหนักมาก (70-90 มม.)';
            if (rain >= 50) return '🌧️ ฝนตกหนัก (50-70 มม.)';
            if (rain >= 35) return '🌧️ ฝนค่อนข้างหนัก (35-50 มม.)';
            if (rain >= 20) return '🌦️ ฝนปานกลางมาก (20-35 มม.)';
            if (rain >= 10) return '🌦️ ฝนปานกลาง (10-20 มม.)';
            if (rain >= 0.1) return '🌤️ ฝนเล็กน้อย (0.1-10 มม.)';
            return '☀️ ปลอดฝน';
        }}

        function getTableCellStyle(rain) {{
            if (rain >= 90) return 'background-color:#FF0000; color:#FFFFFF; font-weight:bold;';
            if (rain >= 70) return 'background-color:#FF00FF; color:#FFFFFF; font-weight:bold;';
            if (rain >= 50) return 'background-color:#FFC000; color:#000000; font-weight:bold;';
            if (rain >= 35) return 'background-color:#FFFF00; color:#000000; font-weight:bold;';
            if (rain >= 20) return 'background-color:#00B050; color:#FFFFFF; font-weight:bold;';
            if (rain >= 10) return 'background-color:#CCFFCC; color:#000000; font-weight:600;';
            if (rain >= 0.1) return 'background-color:#E0F2FE; color:#000000; font-weight:500;';
            return 'background-color:#FFFFFF; color:#94a3b8; font-weight:normal;';
        }}

        function switchMapLayer(mode) {{
            currentLayerMode = mode;
            document.querySelectorAll('.layer-btn').forEach(b => b.classList.remove('active'));
            if (mode === 'amphoe') document.getElementById('btnLayerAmphoe').classList.add('active');
            if (mode === 'province') document.getElementById('btnLayerProvince').classList.add('active');
            if (mode === 'smooth') document.getElementById('btnLayerSmooth').classList.add('active');
            if (mode === 'isohyet') document.getElementById('btnLayerIsohyet').classList.add('active');
            if (mode === 'points') document.getElementById('btnLayerPoints').classList.add('active');
            renderMapLayer();
        }}

        function renderMapLayer() {{
            if (!activeMapGroup) return;
            activeMapGroup.clearLayers();

            const PROV_CENTERS = {{
                'ชลบุรี': [13.32, 101.18],
                'ฉะเชิงเทรา': [13.62, 101.35],
                'ระยอง': [12.85, 101.43],
                'จันทบุรี': [12.78, 102.15],
                'ตราด': [12.38, 102.52],
                'สระแก้ว': [13.78, 102.25],
                'ปราจีนบุรี': [14.05, 101.68],
                'นครนายก': [14.22, 101.22]
            }};

            function getMainlandCenter(feature, layer) {{
                try {{
                    if (feature && feature.geometry) {{
                        let bestRing = null;
                        let maxLen = -1;
                        if (feature.geometry.type === 'Polygon') {{
                            bestRing = feature.geometry.coordinates[0];
                        }} else if (feature.geometry.type === 'MultiPolygon') {{
                            feature.geometry.coordinates.forEach(poly => {{
                                if (poly && poly[0] && poly[0].length > maxLen) {{
                                    maxLen = poly[0].length;
                                    bestRing = poly[0];
                                }}
                            }});
                        }}
                        if (bestRing && bestRing.length > 0) {{
                            let sumLat = 0, sumLon = 0;
                            bestRing.forEach(pt => {{
                                sumLon += pt[0];
                                sumLat += pt[1];
                            }});
                            return [sumLat / bestRing.length, sumLon / bestRing.length];
                        }}
                    }}
                }} catch(err) {{
                    console.error(err);
                }}
                return layer.getBounds().getCenter();
            }}


            if (currentLayerMode === 'amphoe') {{
                const layer = L.geoJSON(GEOJSON_DATA, {{
                    style: function(feature) {{
                        const p = feature.properties.prov;
                        const a = feature.properties.amp;
                        const rain = getAmphoeRain(p, a, currentMapDate);
                        return {{
                            fillColor: getMapColor(rain),
                            weight: 1.5,
                            opacity: 1,
                            color: '#475569',
                            fillOpacity: 0.85
                        }};
                    }},
                    onEachFeature: function(feature, layer) {{
                        const p = feature.properties.prov;
                        const a = feature.properties.amp;
                        const rain = getAmphoeRain(p, a, currentMapDate);
                        const condDesc = getRainDesc(rain);

                        layer.bindPopup(`
                            <div style="font-family:'Prompt',sans-serif; min-width:180px;">
                                <h4 style="color:#0284c7; margin-bottom:6px; font-size:1.1rem;">อ.${{a}} (จ.${{p}})</h4>
                                <p style="margin-bottom:4px; color:#475569;">📅 พยากรณ์วันที่: <b>${{formatDateThai(currentMapDate)}}</b></p>
                                <p style="font-size:1.15rem; color:#0f172a;">ปริมาณฝน: <b style="color:#0284c7">${{rain.toFixed(2)}} มม.</b></p>
                                <p style="color:#64748b; font-size:0.85rem; margin-top:4px;">${{condDesc}}</p>
                            </div>
                        `);

                        layer.on({{
                            mouseover: function(e) {{
                                var l = e.target;
                                l.setStyle({{
                                    weight: 3.5,
                                    color: '#0f172a',
                                    fillOpacity: 0.95
                                }});
                                l.bringToFront();
                            }},
                            mouseout: function(e) {{
                                layer.resetStyle(e.target);
                            }}
                        }});

                        if (document.getElementById('showLabelsToggle')?.checked !== false) {{
                            const pos = getMainlandCenter(feature, layer);
                            const lbl = L.tooltip({{
                                permanent: true,
                                direction: 'center',
                                className: 'map-label amphoe-label',
                                interactive: false
                            }}).setLatLng(pos).setContent(a);
                            activeMapGroup.addLayer(lbl);
                        }}
                    }}
                }});
                activeMapGroup.addLayer(layer);
            }} else if (currentLayerMode === 'province') {{
                const layer = L.geoJSON(PROV_GEOJSON_DATA, {{
                    style: function(feature) {{
                        const p = feature.properties.prov;
                        const provRain = RAW_DATA.summary[p]?.[currentMapDate] || 0.0;
                        return {{
                            fillColor: getMapColor(provRain),
                            weight: 2.2,
                            opacity: 1,
                            color: '#1e293b',
                            fillOpacity: 0.85
                        }};
                    }},
                    onEachFeature: function(feature, layer) {{
                        const p = feature.properties.prov;
                        const provRain = RAW_DATA.summary[p]?.[currentMapDate] || 0.0;
                        const condDesc = getRainDesc(provRain);

                        layer.bindPopup(`
                            <div style="font-family:'Prompt',sans-serif; min-width:190px;">
                                <h4 style="color:#0284c7; margin-bottom:6px; font-size:1.15rem;">🌐 จังหวัด${{p}}</h4>
                                <p style="margin-bottom:4px; color:#475569;">📅 พยากรณ์วันที่: <b>${{formatDateThai(currentMapDate)}}</b></p>
                                <p style="font-size:1.15rem; color:#0f172a;">ฝนเฉลี่ยทั้งจังหวัด: <b style="color:#0284c7">${{provRain.toFixed(2)}} มม.</b></p>
                                <p style="color:#64748b; font-size:0.85rem; margin-top:4px;">${{condDesc}}</p>
                            </div>
                        `);

                        layer.on({{
                            mouseover: function(e) {{
                                var l = e.target;
                                l.setStyle({{
                                    weight: 3.5,
                                    color: '#0f172a',
                                    fillOpacity: 0.95
                                }});
                                l.bringToFront();
                            }},
                            mouseout: function(e) {{
                                layer.resetStyle(e.target);
                            }}
                        }});

                        if (document.getElementById('showLabelsToggle')?.checked !== false) {{
                            const pos = PROV_CENTERS[p] || getMainlandCenter(feature, layer);
                            const lbl = L.tooltip({{
                                permanent: true,
                                direction: 'center',
                                className: 'map-label province-label',
                                interactive: false
                            }}).setLatLng(pos).setContent(p);
                            activeMapGroup.addLayer(lbl);
                        }}
                    }}
                }});
                activeMapGroup.addLayer(layer);
            }} else if (currentLayerMode === 'smooth') {{
                // แถบสีฝนโค้งมนสวยงาม (Smooth Continuous Contour Bands)
                if (RAW_DATA.contours && RAW_DATA.contours[currentMapDate]) {{
                    RAW_DATA.contours[currentMapDate].forEach(item => {{
                        const poly = L.polygon(item.coords, {{
                            stroke: false,
                            fillColor: getMapColor(item.level),
                            fillOpacity: 0.85,
                            smoothFactor: 1.2
                        }});
                        poly.bindPopup(`
                            <div style="font-family:'Prompt',sans-serif; text-align:center;">
                                <b>🌈 เส้นชั้นน้ำฝน (Smooth Contour)</b><br>
                                เกณฑ์ปริมาณฝน: <span style="color:#0284c7; font-weight:bold; font-size:1.1rem;">ตั้งแต่ ${{item.level}} มม. ขึ้นไป</span><br>
                                <span style="font-size:0.85rem; color:#64748b;">${{getRainDesc(item.level)}}</span>
                            </div>
                        `);
                        activeMapGroup.addLayer(poly);
                    }});
                }}

                // Overlay ขอบเขตจังหวัดทับด้านบนพร้อมแสดงชื่อ
                const borderLayer = L.geoJSON(PROV_GEOJSON_DATA, {{
                    style: {{ fillColor: 'transparent', weight: 2, color: '#1e293b', fillOpacity: 0 }},
                    onEachFeature: function(feature, layer) {{
                        if (document.getElementById('showLabelsToggle')?.checked !== false) {{
                            const p = feature.properties.prov;
                            const pos = PROV_CENTERS[p] || getMainlandCenter(feature, layer);
                            const lbl = L.tooltip({{
                                permanent: true,
                                direction: 'center',
                                className: 'map-label province-label',
                                interactive: false
                            }}).setLatLng(pos).setContent(p);
                            activeMapGroup.addLayer(lbl);
                        }}
                    }}
                }});
                activeMapGroup.addLayer(borderLayer);
            }} else if (currentLayerMode === 'isohyet') {{
                // เส้นชั้นน้ำฝน (Seamless Continuous Isohyet Surface Mesh)
                if (RAW_DATA.grid_points) {{
                    RAW_DATA.grid_points.forEach(pt => {{
                        const rain = pt.rain[currentMapDate] ?? 0.0;
                        if (rain > 0.05) {{
                            const bounds = [[pt.lat - 0.026, pt.lon - 0.026], [pt.lat + 0.026, pt.lon + 0.026]];
                            const cell = L.rectangle(bounds, {{
                                stroke: false,
                                fillColor: getMapColor(rain),
                                fillOpacity: 0.82
                            }});
                            cell.bindPopup(`
                                <div style="font-family:'Prompt',sans-serif; text-align:center;">
                                    <b>📦 ตารางกริดฝน (5x5 กม.)</b><br>
                                    พิกัด: ${{pt.lat}}, ${{pt.lon}}<br>
                                    ฝนพยากรณ์: <span style="color:#0284c7; font-weight:bold; font-size:1.1rem;">${{rain}} มม.</span><br>
                                    <span style="font-size:0.85rem; color:#64748b;">${{getRainDesc(rain)}}</span>
                                </div>
                            `);
                            activeMapGroup.addLayer(cell);
                        }}
                    }});
                }}

                // Overlay ขอบเขตจังหวัดและอำเภอทับด้านบน
                const borderLayer = L.geoJSON(GEOJSON_DATA, {{
                    style: {{ fillColor: 'transparent', weight: 1.2, color: '#334155', fillOpacity: 0 }}
                }});
                activeMapGroup.addLayer(borderLayer);
            }} else if (currentLayerMode === 'points') {{
                const borderLayer = L.geoJSON(GEOJSON_DATA, {{
                    style: {{ fillColor: '#f8fafc', weight: 1.2, color: '#64748b', fillOpacity: 0.4 }}
                }});
                activeMapGroup.addLayer(borderLayer);

                if (RAW_DATA.grid_points) {{
                    RAW_DATA.grid_points.forEach(pt => {{
                        const rain = pt.rain[currentMapDate] ?? 0.0;
                        const dot = L.circleMarker([pt.lat, pt.lon], {{
                            radius: 4,
                            fillColor: getMapColor(rain),
                            color: '#334155',
                            weight: 0.5,
                            opacity: 0.9,
                            fillOpacity: 0.9
                        }});
                        dot.bindPopup(`
                            <div style="font-family:'Prompt',sans-serif; text-align:center;">
                                <b>📍 จุดกริดพยากรณ์</b><br>
                                Lat: ${{pt.lat}}, Lon: ${{pt.lon}}<br>
                                ฝนพยากรณ์: <span style="color:#0284c7; font-weight:bold; font-size:1.1rem;">${{rain}} มม.</span>
                            </div>
                        `);
                        activeMapGroup.addLayer(dot);
                    }});
                }}
            }}
        }}

        function updateMapDate() {{
            currentMapDate = document.getElementById('mapDateSelect').value;
            renderMapLayer();
        }}

        function selectProvince(prov) {{
            currentProvince = prov;
            document.querySelectorAll('.pill-btn').forEach(btn => {{
                btn.classList.toggle('active', btn.innerText.includes(prov) || (prov === 'ALL' && btn.innerText.includes('ภาพรวม')));
            }});
            renderChart();
            renderTable();
        }}

        function renderChart() {{
            const ctx = document.getElementById('mainChart').getContext('2d');
            if (chartInstance) chartInstance.destroy();

            const dates = RAW_DATA.dates;
            let datasets = [];
            const PROV_COLOR_MAP = {{
                'ฉะเชิงเทรา': '#ef4444',    // Soft Vibrant Red
                'ชลบุรี': '#f97316',        // Soft Vibrant Orange
                'ระยอง': '#eab308',         // Pleasant Sunflower Gold
                'จันทบุรี': '#10b981',      // Pleasant Emerald Green
                'ตราด': '#06b6d4',          // Pleasant Sky Cyan
                'สระแก้ว': '#3b82f6',        // Pleasant Royal Blue
                'ปราจีนบุรี': '#6366f1',     // Pleasant Indigo Blue
                'นครนายก': '#a855f7'        // Pleasant Amethyst Purple
            }};
            const AMPHOE_COLORS = [
                '#ef4444', '#f97316', '#eab308', '#10b981', '#06b6d4', 
                '#3b82f6', '#6366f1', '#a855f7', '#ec4899', '#84cc16', 
                '#14b8a6', '#f43f5e'
            ];

            if (currentProvince === 'ALL') {{
                Object.entries(RAW_DATA.summary).forEach(([prov, dMap], idx) => {{
                    const dailyData = dates.map(d => dMap[d] || 0);
                    let cSum = 0;
                    const cumData = dailyData.map(r => {{
                        cSum += r;
                        return parseFloat(cSum.toFixed(2));
                    }});

                    const col = PROV_COLOR_MAP[prov] || AMPHOE_COLORS[idx % AMPHOE_COLORS.length];

                    datasets.push({{
                        type: 'bar',
                        label: prov,
                        data: dailyData,
                        borderColor: col,
                        backgroundColor: col,
                        borderWidth: 1,
                        yAxisID: 'y',
                        order: 2,
                        linkedGroup: prov
                    }});

                    datasets.push({{
                        type: 'line',
                        label: `สะสม (${{prov}})`,
                        data: cumData,
                        borderColor: col,
                        backgroundColor: col,
                        borderWidth: 2.5,
                        borderDash: [3, 3],
                        pointRadius: 3,
                        pointBackgroundColor: col,
                        tension: 0.3,
                        yAxisID: 'y1',
                        order: 1,
                        linkedGroup: prov
                    }});
                }});
            }} else {{
                const amphoes = RAW_DATA.provinces[currentProvince] || [];
                amphoes.forEach((amp, idx) => {{
                    const ampData = RAW_DATA.matrix[currentProvince]?.[amp.name] || {{}};
                    const dailyData = dates.map(d => ampData[d]?.rain || 0);
                    let cSum = 0;
                    const cumData = dailyData.map(r => {{
                        cSum += r;
                        return parseFloat(cSum.toFixed(2));
                    }});

                    const col = AMPHOE_COLORS[idx % AMPHOE_COLORS.length];

                    datasets.push({{
                        type: 'bar',
                        label: amp.name,
                        data: dailyData,
                        borderColor: col,
                        backgroundColor: col,
                        borderWidth: 1,
                        yAxisID: 'y',
                        order: 2,
                        linkedGroup: amp.name
                    }});

                    datasets.push({{
                        type: 'line',
                        label: `สะสม (${{amp.name}})`,
                        data: cumData,
                        borderColor: col,
                        backgroundColor: col,
                        borderWidth: 2.5,
                        borderDash: [3, 3],
                        pointRadius: 3,
                        pointBackgroundColor: col,
                        tension: 0.3,
                        yAxisID: 'y1',
                        order: 1,
                        linkedGroup: amp.name
                    }});
                }});
            }}

            chartInstance = new Chart(ctx, {{
                type: 'bar',
                data: {{ labels: dates.map(d => formatDateThai(d)), datasets: datasets }},
                options: {{
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {{
                        legend: {{
                            labels: {{
                                color: '#0f172a',
                                font: {{ family: 'Prompt', size: 13 }},
                                filter: function(item, chart) {{
                                    return chart.datasets[item.datasetIndex].type === 'bar';
                                }}
                            }},
                            onClick: function(e, legendItem, legend) {{
                                const index = legendItem.datasetIndex;
                                const ci = legend.chart;
                                const group = ci.data.datasets[index].linkedGroup;

                                ci.data.datasets.forEach((ds, dsIdx) => {{
                                    if (ds.linkedGroup === group) {{
                                        const meta = ci.getDatasetMeta(dsIdx);
                                        meta.hidden = meta.hidden === null ? true : !meta.hidden;
                                    }}
                                }});
                                ci.update();
                            }}
                        }},
                        title: {{
                            display: true,
                            text: currentProvince === 'ALL' ? '📊 ปริมาณฝนรายวันและปริมาณฝนสะสมรายจังหวัด' : `📊 ปริมาณฝนรายวันและปริมาณฝนสะสมรายอำเภอ ในจังหวัด${{currentProvince}}`,
                            color: '#0284c7',
                            font: {{ family: 'Prompt', size: 16, weight: 'bold' }}
                        }},
                        tooltip: {{
                            callbacks: {{
                                label: function(context) {{
                                    const ds = context.dataset;
                                    const val = context.raw;
                                    return ds.type === 'line' ? `${{ds.linkedGroup}} (ฝนสะสม): ${{val}} มม.` : `${{ds.linkedGroup}} (ฝนรายวัน): ${{val}} มม.`;
                                }}
                            }}
                        }}
                    }},
                    scales: {{
                        x: {{ grid: {{ color: 'rgba(0,0,0,0.06)' }}, ticks: {{ color: '#475569' }} }},
                        y: {{ 
                            type: 'linear',
                            position: 'left',
                            grid: {{ color: 'rgba(0,0,0,0.06)' }}, 
                            ticks: {{ color: '#475569' }}, 
                            title: {{ display: true, text: 'ปริมาณฝนรายวัน (มม.)', color: '#475569', font: {{ family: 'Prompt', weight: 'bold' }} }} 
                        }},
                        y1: {{
                            type: 'linear',
                            position: 'right',
                            grid: {{ drawOnChartArea: false }},
                            ticks: {{ color: '#1e3a8a' }},
                            title: {{ display: true, text: 'ฝนสะสมรวม (มม.)', color: '#1e3a8a', font: {{ family: 'Prompt', weight: 'bold' }} }}
                        }}
                    }}
                }}
            }});
        }}

        function getCumulativeCellStyle(r, days) {{
            const avgDaily = r / days;
            if (avgDaily > 70.0) return 'background:#ffcccc; color:#990000; font-weight:700;';
            if (avgDaily > 50.0) return 'background:#ffe6cc; color:#b35900; font-weight:700;';
            if (avgDaily > 35.0) return 'background:#ffffcc; color:#808000; font-weight:600;';
            if (avgDaily > 20.0) return 'background:#d9f2d9; color:#2d862d; font-weight:600;';
            if (avgDaily > 10.0) return 'background:#e6f7ff; color:#006699; font-weight:500;';
            if (r > 0.1) return 'background:#f0f9ff; color:#0369a1; font-weight:500;';
            return 'background:#ffffff; color:#94a3b8;';
        }}

        function switchTableMode(mode) {{
            currentTableMode = mode;
            ['amphoe', 'province', 'amphoe_cum', 'province_cum'].forEach(m => {{
                const btn = document.getElementById(m === 'amphoe' ? 'btnTableAmphoe' : m === 'province' ? 'btnTableProvince' : m === 'amphoe_cum' ? 'btnTableAmphoeCum' : 'btnTableProvinceCum');
                if (btn) btn.classList.toggle('active', mode === m);
            }});
            renderTable();
        }}

        function captureElement(elementId, title) {{
            const el = document.getElementById(elementId);
            if (!el) return;
            
            const origBoxShadow = el.style.boxShadow;
            el.style.boxShadow = 'none';

            html2canvas(el, {{
                useCORS: true,
                allowTaint: true,
                scale: 2,
                backgroundColor: '#ffffff',
                ignoreElements: (element) => element.classList && element.classList.contains('no-capture')
            }}).then(canvas => {{
                el.style.boxShadow = origBoxShadow;
                const link = document.createElement('a');
                const dateStr = new Date().toISOString().slice(0, 10);
                link.download = `${{title}}_${{dateStr}}.png`;
                link.href = canvas.toDataURL('image/png');
                link.click();
            }}).catch(err => {{
                el.style.boxShadow = origBoxShadow;
                alert('ไม่สามารถบันทึกภาพได้: ' + err);
            }});
        }}

        function renderTable() {{
            const dates = RAW_DATA.dates;
            const headRow = document.getElementById('table-head-row');
            const tbody = document.getElementById('table-body');
            tbody.innerHTML = '';

            let targetProvinces = currentProvince === 'ALL' ? Object.keys(RAW_DATA.provinces) : [currentProvince];

            if (currentTableMode === 'province') {{
                headRow.innerHTML = `<th style="text-align:left;">จังหวัด</th><th style="text-align:center;">จำนวนอำเภอ</th>` + 
                    dates.map(d => `<th style="text-align:center;">${{formatDateThai(d)}}</th>`).join('') + `<th style="text-align:center;">เฉลี่ยรวม</th>`;

                targetProvinces.forEach(prov => {{
                    const amphoesCount = (RAW_DATA.provinces[prov] || []).length;
                    const tr = document.createElement('tr');
                    tr.setAttribute('data-search', prov);

                    const provData = RAW_DATA.summary[prov] || {{}};
                    let total = 0;
                    let count = 0;

                    const dateCells = dates.map(d => {{
                        const r = provData[d] || 0;
                        total += r;
                        count++;
                        const cellStyle = getTableCellStyle(r);
                        return `<td style="${{cellStyle}} text-align:center; border:1px solid #e2e8f0; border-radius:4px;">${{r.toFixed(1)}}</td>`;
                    }}).join('');

                    const avg = count > 0 ? total / count : 0.0;
                    const avgStyle = getTableCellStyle(avg);

                    tr.innerHTML = `
                        <td style="font-weight:700; color:#0284c7; background:#f8fafc; border:1px solid #e2e8f0; font-size:1.05rem;">🌐 จังหวัด${{prov}}</td>
                        <td style="text-align:center; color:#475569; background:#ffffff; border:1px solid #e2e8f0; font-weight:600;">${{amphoesCount}} อำเภอ</td>
                        ${{dateCells}}
                        <td style="${{avgStyle}} text-align:center; font-size:1.05rem; border:1px solid #e2e8f0;">${{avg.toFixed(2)}}</td>
                    `;
                    tbody.appendChild(tr);
                }});

                document.getElementById('table-title').innerText = currentProvince === 'ALL' ? 
                    '📋 ตารางสรุปข้อมูลพยากรณ์ฝนรายวันเฉลี่ยรายจังหวัด (8 จังหวัด)' : 
                    `📋 ตารางสรุปข้อมูลพยากรณ์ฝนรายวัน จังหวัด${{currentProvince}}`;
            }} else if (currentTableMode === 'province_cum') {{
                const d3Str = dates.length >= 3 ? `<br><span style="font-size:0.78rem; color:#64748b; font-weight:normal;">(${{formatDateThai(dates[0])}} - ${{formatDateThai(dates[2])}})</span>` : '';
                const d5Str = dates.length >= 5 ? `<br><span style="font-size:0.78rem; color:#64748b; font-weight:normal;">(${{formatDateThai(dates[0])}} - ${{formatDateThai(dates[4])}})</span>` : '';
                const d7Str = dates.length >= 7 ? `<br><span style="font-size:0.78rem; color:#64748b; font-weight:normal;">(${{formatDateThai(dates[0])}} - ${{formatDateThai(dates[6])}})</span>` : '';
                const d10Str = dates.length >= 1 ? `<br><span style="font-size:0.78rem; color:#64748b; font-weight:normal;">(${{formatDateThai(dates[0])}} - ${{formatDateThai(dates[dates.length-1])}})</span>` : '';

                headRow.innerHTML = `<th style="text-align:left;">จังหวัด</th><th style="text-align:center;">จำนวนอำเภอ</th>` +
                    `<th style="text-align:center;">ฝนสะสม 3 วัน${{d3Str}}</th>` +
                    `<th style="text-align:center;">ฝนสะสม 5 วัน${{d5Str}}</th>` +
                    `<th style="text-align:center;">ฝนสะสม 7 วัน${{d7Str}}</th>` +
                    `<th style="text-align:center; background:#e0f2fe; color:#0369a1;">ฝนสะสมรวม 9 วัน${{d10Str}}</th>`;

                targetProvinces.forEach(prov => {{
                    const amphoesCount = (RAW_DATA.provinces[prov] || []).length;
                    const tr = document.createElement('tr');
                    tr.setAttribute('data-search', prov);

                    const provData = RAW_DATA.summary[prov] || {{}};
                    const rainArr = dates.map(d => provData[d] || 0);
                    const s3 = rainArr.slice(0, 3).reduce((a,b)=>a+b, 0);
                    const s5 = rainArr.slice(0, 5).reduce((a,b)=>a+b, 0);
                    const s7 = rainArr.slice(0, 7).reduce((a,b)=>a+b, 0);
                    const s10 = rainArr.slice(0, 10).reduce((a,b)=>a+b, 0);

                    tr.innerHTML = `
                        <td style="font-weight:700; color:#0284c7; background:#f8fafc; border:1px solid #e2e8f0; font-size:1.05rem;">🌐 จังหวัด${{prov}}</td>
                        <td style="text-align:center; color:#475569; background:#ffffff; border:1px solid #e2e8f0; font-weight:600;">${{amphoesCount}} อำเภอ</td>
                        <td style="${{getTableCellStyle(s3)}} text-align:center; font-size:1.05rem; border:1px solid #e2e8f0; border-radius:4px;">${{s3.toFixed(1)}}</td>
                        <td style="${{getTableCellStyle(s5)}} text-align:center; font-size:1.05rem; border:1px solid #e2e8f0; border-radius:4px;">${{s5.toFixed(1)}}</td>
                        <td style="${{getTableCellStyle(s7)}} text-align:center; font-size:1.05rem; border:1px solid #e2e8f0; border-radius:4px;">${{s7.toFixed(1)}}</td>
                        <td style="${{getTableCellStyle(s10)}} text-align:center; font-size:1.08rem; font-weight:700; border:1px solid #e2e8f0; border-radius:4px;">${{s10.toFixed(1)}}</td>
                    `;
                    tbody.appendChild(tr);
                }});

                document.getElementById('table-title').innerText = currentProvince === 'ALL' ? 
                    '📋 ตารางข้อมูลปริมาณฝนสะสมรายจังหวัด (8 จังหวัด)' : 
                    `📋 ตารางข้อมูลปริมาณฝนสะสม จังหวัด${{currentProvince}}`;
            }} else if (currentTableMode === 'amphoe_cum') {{
                const d3Str = dates.length >= 3 ? `<br><span style="font-size:0.78rem; color:#64748b; font-weight:normal;">(${{formatDateThai(dates[0])}} - ${{formatDateThai(dates[2])}})</span>` : '';
                const d5Str = dates.length >= 5 ? `<br><span style="font-size:0.78rem; color:#64748b; font-weight:normal;">(${{formatDateThai(dates[0])}} - ${{formatDateThai(dates[4])}})</span>` : '';
                const d7Str = dates.length >= 7 ? `<br><span style="font-size:0.78rem; color:#64748b; font-weight:normal;">(${{formatDateThai(dates[0])}} - ${{formatDateThai(dates[6])}})</span>` : '';
                const d10Str = dates.length >= 1 ? `<br><span style="font-size:0.78rem; color:#64748b; font-weight:normal;">(${{formatDateThai(dates[0])}} - ${{formatDateThai(dates[dates.length-1])}})</span>` : '';

                headRow.innerHTML = `<th style="text-align:left;">จังหวัด</th><th style="text-align:left;">อำเภอ</th>` +
                    `<th style="text-align:center;">ฝนสะสม 3 วัน${{d3Str}}</th>` +
                    `<th style="text-align:center;">ฝนสะสม 5 วัน${{d5Str}}</th>` +
                    `<th style="text-align:center;">ฝนสะสม 7 วัน${{d7Str}}</th>` +
                    `<th style="text-align:center; background:#e0f2fe; color:#0369a1;">ฝนสะสมรวม 9 วัน${{d10Str}}</th>`;

                targetProvinces.forEach((prov, provIndex) => {{
                    const amphoes = RAW_DATA.provinces[prov] || [];
                    
                    const provHeader = document.createElement('tr');
                    provHeader.setAttribute('data-search', `${{prov}} ${{amphoes.map(a => a.name).join(' ')}}`);
                    const provBannerBg = provIndex % 2 === 0 ? 'linear-gradient(90deg, #e0f2fe 0%, #f8fafc 100%)' : 'linear-gradient(90deg, #f1f5f9 0%, #ffffff 100%)';
                    provHeader.innerHTML = `<td colspan="6" style="background:${{provBannerBg}}; color:#0369a1; font-weight:700; font-size:1.05rem; padding:10px 16px; border-top:2px solid #0284c7; border-bottom:1px solid #cbd5e1;">📍 จังหวัด${{prov}} (มีทั้งหมด ${{amphoes.length}} อำเภอ)</td>`;
                    tbody.appendChild(provHeader);

                    const provCellBg = provIndex % 2 === 0 ? '#f0f9ff' : '#f8fafc';
                    const ampCellBg = provIndex % 2 === 0 ? '#f8fafc' : '#ffffff';

                    amphoes.forEach((amp, ampIndex) => {{
                        const tr = document.createElement('tr');
                        tr.setAttribute('data-search', `${{prov}} ${{amp.name}}`);
                        
                        const ampData = RAW_DATA.matrix[prov]?.[amp.name] || {{}};
                        const rainArr = dates.map(d => ampData[d]?.rain || 0);
                        const s3 = rainArr.slice(0, 3).reduce((a,b)=>a+b, 0);
                        const s5 = rainArr.slice(0, 5).reduce((a,b)=>a+b, 0);
                        const s7 = rainArr.slice(0, 7).reduce((a,b)=>a+b, 0);
                        const s10 = rainArr.slice(0, 10).reduce((a,b)=>a+b, 0);

                        const isLastAmp = (ampIndex === amphoes.length - 1);
                        const bottomBorder = isLastAmp ? 'border-bottom:2px solid #475569;' : 'border-bottom:1px solid #e2e8f0;';

                        tr.innerHTML = `
                            <td style="font-weight:700; color:#0284c7; background:${{provCellBg}}; border:1px solid #e2e8f0; ${{bottomBorder}}">${{prov}}</td>
                            <td style="font-weight:600; color:#0f172a; background:${{ampCellBg}}; border:1px solid #e2e8f0; ${{bottomBorder}}">${{amp.name}}</td>
                            <td style="${{getTableCellStyle(s3)}} text-align:center; font-size:1.05rem; border:1px solid #e2e8f0; ${{bottomBorder}} border-radius:4px;">${{s3.toFixed(1)}}</td>
                            <td style="${{getTableCellStyle(s5)}} text-align:center; font-size:1.05rem; border:1px solid #e2e8f0; ${{bottomBorder}} border-radius:4px;">${{s5.toFixed(1)}}</td>
                            <td style="${{getTableCellStyle(s7)}} text-align:center; font-size:1.05rem; border:1px solid #e2e8f0; ${{bottomBorder}} border-radius:4px;">${{s7.toFixed(1)}}</td>
                            <td style="${{getTableCellStyle(s10)}} text-align:center; font-size:1.08rem; font-weight:700; border:1px solid #e2e8f0; ${{bottomBorder}} border-radius:4px;">${{s10.toFixed(1)}}</td>
                        `;
                        tbody.appendChild(tr);
                    }});
                }});

                document.getElementById('table-title').innerText = currentProvince === 'ALL' ? 
                    '📋 ตารางข้อมูลปริมาณฝนสะสมรายอำเภอ (67 อำเภอ)' : 
                    `📋 ตารางข้อมูลปริมาณฝนสะสม อำเภอในจังหวัด${{currentProvince}}`;
            }} else {{
                headRow.innerHTML = `<th style="text-align:left;">จังหวัด</th><th style="text-align:left;">อำเภอ</th>` + 
                    dates.map(d => `<th style="text-align:center;">${{formatDateThai(d)}}</th>`).join('') + `<th style="text-align:center;">เฉลี่ยรวม</th>`;

                targetProvinces.forEach((prov, provIndex) => {{
                    const amphoes = RAW_DATA.provinces[prov] || [];
                    
                    const provHeader = document.createElement('tr');
                    provHeader.setAttribute('data-search', `${{prov}} ${{amphoes.map(a => a.name).join(' ')}}`);
                    const provBannerBg = provIndex % 2 === 0 ? 'linear-gradient(90deg, #e0f2fe 0%, #f8fafc 100%)' : 'linear-gradient(90deg, #f1f5f9 0%, #ffffff 100%)';
                    provHeader.innerHTML = `<td colspan="${{dates.length + 3}}" style="background:${{provBannerBg}}; color:#0369a1; font-weight:700; font-size:1.05rem; padding:10px 16px; border-top:2px solid #0284c7; border-bottom:1px solid #cbd5e1;">📍 จังหวัด${{prov}} (มีทั้งหมด ${{amphoes.length}} อำเภอ)</td>`;
                    tbody.appendChild(provHeader);

                    const provCellBg = provIndex % 2 === 0 ? '#f0f9ff' : '#f8fafc';
                    const ampCellBg = provIndex % 2 === 0 ? '#f8fafc' : '#ffffff';

                    amphoes.forEach((amp, ampIndex) => {{
                        const tr = document.createElement('tr');
                        tr.setAttribute('data-search', `${{prov}} ${{amp.name}}`);
                        
                        const ampData = RAW_DATA.matrix[prov]?.[amp.name] || {{}};
                        let total = 0;
                        let count = 0;

                        const isLastAmp = (ampIndex === amphoes.length - 1);
                        const bottomBorder = isLastAmp ? 'border-bottom:2px solid #475569;' : 'border-bottom:1px solid #e2e8f0;';

                        const dateCells = dates.map(d => {{
                            const r = ampData[d]?.rain || 0;
                            total += r;
                            count++;
                            const cellStyle = getTableCellStyle(r);
                            return `<td style="${{cellStyle}} text-align:center; border:1px solid #e2e8f0; ${{bottomBorder}} border-radius:4px;">${{r.toFixed(1)}}</td>`;
                        }}).join('');

                        const avg = count > 0 ? total / count : 0.0;
                        const avgStyle = getTableCellStyle(avg);

                        tr.innerHTML = `
                            <td style="font-weight:700; color:#0284c7; background:${{provCellBg}}; border:1px solid #e2e8f0; ${{bottomBorder}}">${{prov}}</td>
                            <td style="font-weight:600; color:#0f172a; background:${{ampCellBg}}; border:1px solid #e2e8f0; ${{bottomBorder}}">${{amp.name}}</td>
                            ${{dateCells}}
                            <td style="${{avgStyle}} text-align:center; font-size:1.05rem; border:1px solid #e2e8f0; ${{bottomBorder}}">${{avg.toFixed(2)}}</td>
                        `;
                        tbody.appendChild(tr);
                    }});
                }});

                document.getElementById('table-title').innerText = currentProvince === 'ALL' ? 
                    '📋 ตารางข้อมูลพยากรณ์ฝนรายวัน รายอำเภอทั้งหมด (67 อำเภอ)' : 
                    `📋 ตารางข้อมูลพยากรณ์ฝนรายวัน อำเภอในจังหวัด${{currentProvince}}`;
            }}

            filterTable();
        }}

        function filterTable() {{
            const query = document.getElementById('searchInput').value.toLowerCase();
            document.querySelectorAll('#table-body tr').forEach(tr => {{
                const text = tr.getAttribute('data-search').toLowerCase();
                tr.style.display = text.includes(query) ? '' : 'none';
            }});
        }}

        window.onload = initDashboard;
    </script>
</body>
</html>
"""
    with open(OUTPUT_HTML, "w", encoding="utf-8") as f:
        f.write(html_content)
    print(f"[*] สร้าง Dashboard พร้อมแผนที่ GIS สำเร็จเรียบร้อย: {OUTPUT_HTML}")

if __name__ == "__main__":
    data = get_dashboard_data()
    geojson_data = get_geojson_data()
    prov_geojson_data = get_province_geojson_data()
    generate_html(data, geojson_data, prov_geojson_data)
