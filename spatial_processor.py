import os
import sqlite3
import shapefile
from datetime import datetime, timezone, timedelta
THAI_TZ = timezone(timedelta(hours=7))

DB_PATH = os.path.join(os.path.dirname(__file__), "eastern_rain.db")
SHP_PATH = os.path.join(os.path.dirname(__file__), "Shapefile", "Amphoe", "Amphoe_Province_RIO9_WGS1984.shp")

def point_in_ring(x, y, ring):
    n = len(ring)
    inside = False
    p1x, p1y = ring[0]
    for i in range(1, n + 1):
        p2x, p2y = ring[i % n]
        if y > min(p1y, p2y):
            if y <= max(p1y, p2y):
                if x <= max(p1x, p2x):
                    if p1y != p2y:
                        xints = (y - p1y) * (p2x - p1x) / (p2y - p1y) + p1x
                    if p1x == p2x or x <= xints:
                        inside = not inside
        p1x, p1y = p2x, p2y
    return inside

def is_point_in_shape(x, y, shape):
    bbox = shape.bbox
    if not (bbox[0] <= x <= bbox[2] and bbox[1] <= y <= bbox[3]):
        return False
        
    pts = shape.points
    parts = list(shape.parts) + [len(pts)]
    for i in range(len(parts) - 1):
        ring = pts[parts[i]:parts[i+1]]
        if len(ring) >= 3 and point_in_ring(x, y, ring):
            return True
    return False

def generate_grid_points(conn, amphoe_polygons):
    """สร้างตาราง Grid 0.05 องศา (5x5 กม.) ครอบคลุมทั่วภาคตะวันออกและเลยขอบเขตออกไปเล็กน้อย"""
    cursor = conn.cursor()
    cursor.execute("DROP TABLE IF EXISTS grid_forecast_daily")
    cursor.execute("DROP TABLE IF EXISTS grid_points")
    cursor.execute("""
    CREATE TABLE grid_points (
        point_id INTEGER PRIMARY KEY AUTOINCREMENT,
        lat REAL NOT NULL,
        lon REAL NOT NULL,
        amphoe_name TEXT NOT NULL,
        province_name TEXT NOT NULL,
        is_in_poly INTEGER DEFAULT 1,
        UNIQUE(lat, lon)
    );
    """)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS grid_forecast_daily (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        point_id INTEGER NOT NULL,
        forecast_date TEXT NOT NULL,
        rain_mm REAL DEFAULT 0.0,
        interpolated_at TEXT,
        UNIQUE(point_id, forecast_date)
    );
    """)

    print("[*] กำลังสร้างจุดพิกัด Grid 0.05 องศา (~5x5 กม.) ทั่วพื้นที่และเขตบัฟเฟอร์ภาคตะวันออก...")
    
    # BBox ครอบคลุมพื้นที่สี่เหลี่ยมเต็มพื้นที่ตามที่ผู้บริหารต้องการ (ครอบคลุมภาคตะวันออกและรอบนอกเต็มกรอบ)
    xmin, ymin, xmax, ymax = 100.35, 11.40, 103.15, 14.65
    step = 0.05
    
    amp_centers = []
    for poly in amphoe_polygons:
        bb = poly["shape"].bbox
        amp_centers.append({
            "amp": poly["amp"],
            "prov": poly["prov"],
            "cx": (bb[0] + bb[2]) / 2.0,
            "cy": (bb[1] + bb[3]) / 2.0
        })

    grid_count = 0
    in_poly_count = 0
    y = ymin
    while y <= ymax + 1e-7:
        x = xmin
        while x <= xmax + 1e-7:
            matched_amp = None
            matched_prov = None
            is_in = 0
            for poly in amphoe_polygons:
                if is_point_in_shape(x, y, poly["shape"]):
                    matched_amp = poly["amp"]
                    matched_prov = poly["prov"]
                    is_in = 1
                    break
            
            if not matched_amp:
                # สำหรับจุดนอกขอบเขตเขตจังหวัดภาคตะวันออก ให้อ้างอิงอำเภอ/จังหวัดที่ใกล้ที่สุด เพื่อให้แสดงผลเต็มกรอบสี่เหลี่ยม
                min_dist = 999.0
                best_amp, best_prov = None, None
                for c in amp_centers:
                    d = (x - c["cx"])**2 + (y - c["cy"])**2
                    if d < min_dist:
                        min_dist = d
                        best_amp, best_prov = c["amp"], c["prov"]
                matched_amp = best_amp or "นอกเขตพื้นที่"
                matched_prov = best_prov or "รอบนอก"
                is_in = 0

            cursor.execute("""
            INSERT OR IGNORE INTO grid_points (lat, lon, amphoe_name, province_name, is_in_poly)
            VALUES (?, ?, ?, ?, ?)
            """, (round(y, 5), round(x, 5), matched_amp, matched_prov, is_in))
            grid_count += 1
            if is_in == 1:
                in_poly_count += 1
            x += step
        y += step
        
    conn.commit()
    print(f"[*] สร้างจุด Grid สำเร็จทั้งหมด {grid_count} จุด (ในแปลงอำเภอ {in_poly_count} จุด, เขตบัฟเฟอร์รอบนอก {grid_count - in_poly_count} จุด)")

def idw_interpolate(target_lat, target_lon, anchor_list, p=2.0):
    """คำนวณ Inverse Distance Weighting (IDW Interpolation)"""
    num = 0.0
    den = 0.0
    for a_lat, a_lon, rain in anchor_list:
        dist_sq = (target_lat - a_lat)**2 + (target_lon - a_lon)**2
        if dist_sq < 1e-8: # อยู่ตรงสถานีพอดี
            return rain
        weight = 1.0 / (dist_sq ** (p / 2.0))
        num += weight * rain
        den += weight
    return round(num / den, 2) if den > 0 else 0.0

def process_all():
    if not os.path.exists(SHP_PATH):
        print(f"[!] ไม่พบ Shapefile ที่: {SHP_PATH}")
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    sf = shapefile.Reader(SHP_PATH, encoding='cp874')
    records = sf.records()
    shapes = sf.shapes()
    
    amphoe_polygons = []
    for r, s in zip(records, shapes):
        rec = r.as_dict()
        prov = rec.get('PROV_NAM_T', '').replace('จ.', '').strip()
        amp = rec.get('AMP_NAM_T', '').replace('กิ่ง อ.', '').replace('อ.', '').replace('กิ่ง', '').strip()
        amphoe_polygons.append({
            "prov": prov,
            "amp": amp,
            "shape": s
        })

    # 1. สร้างจุดกริด 5x5 กม. (1,216 จุด)
    generate_grid_points(conn, amphoe_polygons)

    # 2. อ่านสถานีหลัก Anchor Stations
    cursor.execute("SELECT station_id, lat, lon FROM anchor_stations")
    anchors_raw = cursor.fetchall()
    if not anchors_raw:
        print("[!] ไม่พบข้อมูลสถานีหลักใน anchor_stations")
        conn.close()
        return

    anchor_coords = {row[0]: (row[1], row[2]) for row in anchors_raw}

    today_str = datetime.now(THAI_TZ).strftime("%Y-%m-%d")
    cursor.execute("SELECT station_id, forecast_date, rain_mm FROM anchor_forecast_daily WHERE forecast_date >= ?", (today_str,))
    anchor_forecasts = cursor.fetchall()
    
    # จัดกลุ่มตามวันที่: date -> [(lat, lon, rain)]
    daily_anchors = {}
    for s_id, f_date, rain in anchor_forecasts:
        if s_id in anchor_coords:
            if f_date not in daily_anchors:
                daily_anchors[f_date] = []
            lat, lon = anchor_coords[s_id]
            daily_anchors[f_date].append((lat, lon, rain))

    # 3. อ่านจุด Grid ทั้งหมด
    cursor.execute("SELECT point_id, lat, lon, amphoe_name, province_name, is_in_poly FROM grid_points")
    grid_points = cursor.fetchall()
    print(f"[*] เริ่มคำนวณ IDW Spatial Interpolation กระจายค่าฝนลงสู่จุดกริดทั้ง {len(grid_points)} จุด...")

    now_str = datetime.now(THAI_TZ).strftime("%Y-%m-%d %H:%M:%S")

    # คำนวณ IDW และสะสมข้อมูลเพื่อเฉลี่ยรายอำเภอ
    amphoe_summary_in = {}
    amphoe_summary_all = {}
    province_summary_in = {}
    province_summary_all = {}

    cursor.execute("DELETE FROM grid_forecast_daily")
    interp_count = 0

    for p_id, g_lat, g_lon, amp, prov, is_in_poly in grid_points:
        key_amp = (amp, prov)
        if key_amp not in amphoe_summary_in:
            amphoe_summary_in[key_amp] = {}
        if key_amp not in amphoe_summary_all:
            amphoe_summary_all[key_amp] = {}
        if prov not in province_summary_in:
            province_summary_in[prov] = {}
        if prov not in province_summary_all:
            province_summary_all[prov] = {}

        for f_date, anchor_list in daily_anchors.items():
            rain_interp = idw_interpolate(g_lat, g_lon, anchor_list)
            
            cursor.execute("""
            INSERT INTO grid_forecast_daily (point_id, forecast_date, rain_mm, interpolated_at)
            VALUES (?, ?, ?, ?)
            """, (p_id, f_date, rain_interp, now_str))
            interp_count += 1

            if f_date not in amphoe_summary_all[key_amp]:
                amphoe_summary_all[key_amp][f_date] = []
            amphoe_summary_all[key_amp][f_date].append(rain_interp)

            if f_date not in province_summary_all[prov]:
                province_summary_all[prov][f_date] = []
            province_summary_all[prov][f_date].append(rain_interp)

            if is_in_poly == 1:
                if f_date not in amphoe_summary_in[key_amp]:
                    amphoe_summary_in[key_amp][f_date] = []
                amphoe_summary_in[key_amp][f_date].append(rain_interp)

                if f_date not in province_summary_in[prov]:
                    province_summary_in[prov][f_date] = []
                province_summary_in[prov][f_date].append(rain_interp)

    print(f"[*] คำนวณ IDW เสร็จสิ้น! บันทึกค่าฝนประจำจุดกริดทั้งหมด {interp_count} รายการ")

    # 4. บันทึกผลเฉลี่ยรายอำเภอและรายจังหวัด
    cursor.execute("DELETE FROM spatial_amphoe_summary")
    amp_count = 0
    all_amp_keys = set(amphoe_summary_in.keys()) | set(amphoe_summary_all.keys())
    for (amp, prov) in sorted(all_amp_keys):
        date_dict = amphoe_summary_in.get((amp, prov))
        if not date_dict:  # กรณีอำเภอเกาะ (เช่น เกาะสีชัง) ไม่มีจุดกริดในโพลีคอน ให้ใช้จุดบัฟเฟอร์ที่ใกล้ที่สุด
            date_dict = amphoe_summary_all.get((amp, prov), {})
        for f_date, rain_list in date_dict.items():
            avg_rain = round(sum(rain_list) / len(rain_list), 2)
            pts = len(rain_list)
            cursor.execute("""
            INSERT INTO spatial_amphoe_summary (amphoe_name, province_name, forecast_date, avg_rain_mm, point_count, processed_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """, (amp, prov, f_date, avg_rain, pts, now_str))
            amp_count += 1

    cursor.execute("DELETE FROM spatial_province_summary")
    prov_count = 0
    all_prov_keys = set(province_summary_in.keys()) | set(province_summary_all.keys())
    for prov in sorted(all_prov_keys):
        date_dict = province_summary_in.get(prov)
        if not date_dict:
            date_dict = province_summary_all.get(prov, {})
        for f_date, rain_list in date_dict.items():
            avg_rain = round(sum(rain_list) / len(rain_list), 2)
            pts = len(rain_list)
            cursor.execute("""
            INSERT INTO spatial_province_summary (province_name, forecast_date, avg_rain_mm, total_points, processed_at)
            VALUES (?, ?, ?, ?, ?)
            """, (prov, f_date, avg_rain, pts, now_str))
            prov_count += 1

    conn.commit()
    conn.close()
    print(f"[*] หาค่าเฉลี่ยฝนจากจุดกริด 5x5 กม. สำเร็จ! บันทึกสรุปรายอำเภอ {amp_count} รายการ, สรุปรายจังหวัด {prov_count} รายการ")

def process_spatial_averages():
    process_all()

if __name__ == "__main__":
    process_all()
