import os
import sys
import time
import json
import sqlite3
import requests
from datetime import datetime

# ตั้งค่า encoding สำหรับ Windows console ให้รองรับ UTF-8
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

# รายชื่อ 8 จังหวัดภาคตะวันออก พร้อมอำเภอทั้งหมด (67 อำเภอเป้าหมาย)
EASTERN_PROVINCES = {
    "ชลบุรี": ["เมืองชลบุรี", "บ้านบึง", "หนองใหญ่", "บางละมุง", "พานทอง", "พนัสนิคม", "ศรีราชา", "เกาะสีชัง", "สัตหีบ", "บ่อทอง", "เกาะจันทร์"],
    "ระยอง": ["เมืองระยอง", "บ้านฉาง", "แกลง", "วังจันทร์", "บ้านค่าย", "ปลวกแดง", "เขาชะเมา", "นิคมพัฒนา"],
    "จันทบุรี": ["เมืองจันทบุรี", "ขลุง", "ท่าใหม่", "โป่งน้ำร้อน", "มะขาม", "แหลมสิงห์", "สอยดาว", "แก่งหางแมว", "นายายอาม", "เขาคิชฌกูฏ"],
    "ตราด": ["เมืองตราด", "คลองใหญ่", "เขาสมิง", "บ่อไร่", "แหลมงอบ", "เกาะกูด", "เกาะช้าง"],
    "ฉะเชิงเทรา": ["เมืองฉะเชิงเทรา", "บางคล้า", "บางน้ำเปรี้ยว", "บางปะกง", "บ้านโพธิ์", "พนมสารคาม", "ราชสาส์น", "สนามชัยเขต", "แปลงยาว", "ท่าตะเกียบ", "คลองเขื่อน"],
    "ปราจีนบุรี": ["เมืองปราจีนบุรี", "กบินทร์บุรี", "นาดี", "บ้านสร้าง", "ประจันตคาม", "ศรีมหาโพธิ", "ศรีมโหสถ"],
    "สระแก้ว": ["เมืองสระแก้ว", "คลองหาด", "ตาพระยา", "วังน้ำเย็น", "วัฒนานคร", "อรัญประเทศ", "เขาฉกรรจ์", "โคกสูง", "วังสมบูรณ์"],
    "นครนายก": ["เมืองนครนายก", "ปากพลี", "บ้านนา", "องครักษ์"]
}

DB_PATH = os.path.join(os.path.dirname(__file__), "eastern_rain.db")
TOKEN_PATH = os.path.join(os.path.dirname(__file__), "Token_TMD.txt")

def init_database():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # ตารางเข้ากันได้กับระบบเดิม (Legacy Compatibility)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS provinces (
        province_id INTEGER PRIMARY KEY AUTOINCREMENT,
        name_th TEXT UNIQUE
    );
    """)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS amphoes (
        amphoe_id INTEGER PRIMARY KEY AUTOINCREMENT,
        province_name TEXT,
        name_th TEXT UNIQUE,
        lat REAL,
        lon REAL,
        geocode TEXT
    );
    """)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS rain_forecast_daily (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        amphoe_name TEXT,
        province_name TEXT,
        forecast_date TEXT,
        rain_mm REAL,
        cond_code INTEGER,
        fetched_at TEXT,
        UNIQUE(amphoe_name, forecast_date)
    );
    """)

    # --- ตารางระบบใหม่สำหรับ Anchor Stations และ 5x5km Grid ---
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS anchor_stations (
        station_id INTEGER PRIMARY KEY AUTOINCREMENT,
        amphoe_name TEXT NOT NULL,
        province_name TEXT NOT NULL,
        lat REAL NOT NULL,
        lon REAL NOT NULL,
        geocode TEXT,
        UNIQUE(amphoe_name, province_name)
    );
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS anchor_forecast_daily (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        station_id INTEGER REFERENCES anchor_stations(station_id) ON DELETE CASCADE,
        forecast_date TEXT NOT NULL,
        rain_mm REAL DEFAULT 0.0,
        cond_code INTEGER,
        fetched_at TEXT,
        UNIQUE(station_id, forecast_date)
    );
    """)

    # ตารางเก็บประวัติพยากรณ์ทุกรอบ (Historical Audit Log) สำหรับการตรวจสอบย้อนหลัง
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS anchor_forecast_history (
        log_id INTEGER PRIMARY KEY AUTOINCREMENT,
        station_id INTEGER REFERENCES anchor_stations(station_id) ON DELETE CASCADE,
        forecast_date TEXT NOT NULL,
        rain_mm REAL DEFAULT 0.0,
        cond_code INTEGER,
        fetched_at TEXT
    );
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS grid_points (
        point_id INTEGER PRIMARY KEY AUTOINCREMENT,
        lat REAL NOT NULL,
        lon REAL NOT NULL,
        amphoe_name TEXT,
        province_name TEXT,
        UNIQUE(lat, lon)
    );
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS grid_forecast_daily (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        point_id INTEGER REFERENCES grid_points(point_id) ON DELETE CASCADE,
        forecast_date TEXT NOT NULL,
        rain_mm REAL DEFAULT 0.0,
        interpolated_at TEXT,
        UNIQUE(point_id, forecast_date)
    );
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS spatial_amphoe_summary (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        amphoe_name TEXT NOT NULL,
        province_name TEXT NOT NULL,
        forecast_date TEXT NOT NULL,
        avg_rain_mm REAL DEFAULT 0.0,
        point_count INTEGER DEFAULT 0,
        processed_at TEXT,
        UNIQUE(amphoe_name, province_name, forecast_date)
    );
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS spatial_province_summary (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        province_name TEXT NOT NULL,
        forecast_date TEXT NOT NULL,
        avg_rain_mm REAL DEFAULT 0.0,
        total_points INTEGER DEFAULT 0,
        processed_at TEXT,
        UNIQUE(province_name, forecast_date)
    );
    """)
    
    conn.commit()
    conn.close()

def fetch_and_save_data():
    token = os.environ.get("TMD_API_TOKEN", "").strip()
    if not token:
        if not os.path.exists(TOKEN_PATH):
            print(f"[!] ไม่พบไฟล์ Token: {TOKEN_PATH} และไม่พบตัวแปรระบบ TMD_API_TOKEN")
            return
        with open(TOKEN_PATH, "r", encoding="utf-8") as f:
            token = f.read().strip()

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json"
    }
    url = "https://data.tmd.go.th/nwpapi/v1/forecast/location/daily/place"

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    total_fetched_days = 0
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    today_str = datetime.now().strftime("%Y-%m-%d")

    print("[*] เริ่มดึงข้อมูลสถานีหลัก (Anchor Stations 67 จุด) ปลอดภัยจากการถูกบล็อก API...")
    
    for province, amphoes in EASTERN_PROVINCES.items():
        cursor.execute("INSERT OR IGNORE INTO provinces (name_th) VALUES (?)", (province,))
        print(f"\n---> จังหวัด: {province} ({len(amphoes)} อำเภอ)")
        
        for amphoe in amphoes:
            # เช็คว่าวันนี้ดึงของสถานีนี้สำเร็จครบแล้วหรือยัง
            cursor.execute("""
            SELECT count(*) FROM anchor_stations s
            JOIN anchor_forecast_daily f ON s.station_id = f.station_id
            WHERE s.amphoe_name=? AND s.province_name=? AND f.fetched_at LIKE ?
            """, (amphoe, province, f"{today_str}%"))
            count_existing = cursor.fetchone()[0]
            
            if count_existing >= 8:
                print(f"  [.] {amphoe}: มีข้อมูลพยากรณ์ล่วงหน้า {count_existing} วันของวันนี้แล้ว (ข้ามเพื่อประหยัด API)")
                continue

            params = {
                "province": province,
                "amphoe": amphoe,
                "duration": 10,
                "fields": "rain,cond"
            }
            
            success = False
            retries = 3
            while retries > 0 and not success:
                try:
                    resp = requests.get(url, headers=headers, params=params, timeout=15)
                    if resp.status_code == 200:
                        data = resp.json()
                        wf = data.get("WeatherForecasts", [{}])[0] if "WeatherForecasts" in data else data
                        loc = wf.get("location", {})
                        forecasts = wf.get("forecasts", [])
                        
                        lat = loc.get("lat")
                        lon = loc.get("lon")
                        geocode = loc.get("geocode")
                        
                        # 1. บันทึกลงตาราง legacy
                        cursor.execute("""
                        INSERT INTO amphoes (province_name, name_th, lat, lon, geocode) 
                        VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT(name_th) DO UPDATE SET lat=excluded.lat, lon=excluded.lon, geocode=excluded.geocode
                        """, (province, amphoe, lat, lon, geocode))

                        # 2. บันทึกลงตาราง anchor_stations
                        cursor.execute("""
                        INSERT INTO anchor_stations (amphoe_name, province_name, lat, lon, geocode)
                        VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT(amphoe_name, province_name) DO UPDATE SET lat=excluded.lat, lon=excluded.lon, geocode=excluded.geocode
                        """, (amphoe, province, lat, lon, geocode))
                        
                        cursor.execute("SELECT station_id FROM anchor_stations WHERE amphoe_name=? AND province_name=?", (amphoe, province))
                        station_id = cursor.fetchone()[0]
                        
                        for f_item in forecasts:
                            f_time = f_item.get("time", "").split("T")[0]
                            rain = f_item.get("data", {}).get("rain", 0.0)
                            cond = f_item.get("data", {}).get("cond")
                            
                            # บันทึกลง legacy table
                            cursor.execute("""
                            INSERT INTO rain_forecast_daily (amphoe_name, province_name, forecast_date, rain_mm, cond_code, fetched_at)
                            VALUES (?, ?, ?, ?, ?, ?)
                            ON CONFLICT(amphoe_name, forecast_date) DO UPDATE SET rain_mm=excluded.rain_mm, cond_code=excluded.cond_code, fetched_at=excluded.fetched_at
                            """, (amphoe, province, f_time, rain, cond, now_str))

                            # บันทึกลง anchor_forecast_daily (อัปเดตค่าล่าสุดสำหรับใช้งานบน Dashboard)
                            cursor.execute("""
                            INSERT INTO anchor_forecast_daily (station_id, forecast_date, rain_mm, cond_code, fetched_at)
                            VALUES (?, ?, ?, ?, ?)
                            ON CONFLICT(station_id, forecast_date) DO UPDATE SET rain_mm=excluded.rain_mm, cond_code=excluded.cond_code, fetched_at=excluded.fetched_at
                            """, (station_id, f_time, rain, cond, now_str))

                            # บันทึกประวัติลง anchor_forecast_history (สะสมประวัติทุกรอบดึงสำหรับตรวจสอบย้อนหลัง)
                            cursor.execute("""
                            INSERT INTO anchor_forecast_history (station_id, forecast_date, rain_mm, cond_code, fetched_at)
                            VALUES (?, ?, ?, ?, ?)
                            """, (station_id, f_time, rain, cond, now_str))

                            total_fetched_days += 1
                            
                        print(f"  [+] {amphoe}: ดึงสำเร็จ {len(forecasts)} วัน (Station ID: {station_id})")
                        success = True
                    elif resp.status_code == 429:
                        backoff_time = 10 * (4 - retries)
                        print(f"  [!] {amphoe}: ติด Rate Limit (429) ระบบพักรออัตโนมัติ {backoff_time} วินาทีก่อนลองใหม่...")
                        time.sleep(backoff_time)
                        retries -= 1
                    else:
                        print(f"  [-] {amphoe}: ผิดพลาด {resp.status_code}")
                        break
                except Exception as e:
                    print(f"  [!] {amphoe}: เชื่อมต่อล้มเหลว ({e}) รอ ลองใหม่...")
                    time.sleep(3)
                    retries -= 1
            
            # Adaptive Delay ป้องกันการถูกเซิร์ฟเวอร์บล็อก IP
            time.sleep(1.0)
            conn.commit()

    conn.close()
    print(f"\n[*] อัปเดตสถานีหลักเสร็จสิ้น! บันทึกรายการใหม่ทั้งหมด {total_fetched_days} รายการ")

def sync_legacy_to_anchor():
    """ซิงค์ข้อมูลจากตารางดั้งเดิมเข้าสู่ตาราง anchor_stations ในกรณีเปิดระบบครั้งแรก"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT province_name, name_th, lat, lon, geocode FROM amphoes WHERE lat IS NOT NULL")
    amphoes = cursor.fetchall()
    for prov, amp, lat, lon, geocode in amphoes:
        cursor.execute("""
        INSERT OR IGNORE INTO anchor_stations (amphoe_name, province_name, lat, lon, geocode)
        VALUES (?, ?, ?, ?, ?)
        """, (amp, prov, lat, lon, geocode))
        cursor.execute("SELECT station_id FROM anchor_stations WHERE amphoe_name=? AND province_name=?", (amp, prov))
        station_id = cursor.fetchone()[0]
        
        cursor.execute("SELECT forecast_date, rain_mm, cond_code, fetched_at FROM rain_forecast_daily WHERE amphoe_name=?", (amp,))
        for f_date, rain, cond, fetched in cursor.fetchall():
            cursor.execute("""
            INSERT OR IGNORE INTO anchor_forecast_daily (station_id, forecast_date, rain_mm, cond_code, fetched_at)
            VALUES (?, ?, ?, ?, ?)
            """, (station_id, f_date, rain, cond, fetched))
    conn.commit()
    conn.close()

if __name__ == "__main__":
    init_database()
    fetch_and_save_data()
    sync_legacy_to_anchor()
    
    try:
        print("\n[*] กำลังรันระบบสร้าง Grid 5x5 กม. (1,216 จุด) และคำนวณ IDW Spatial Interpolation...")
        import spatial_processor
        spatial_processor.process_all()
    except Exception as e:
        print(f"[!] ไม่สามารถรัน spatial_processor ได้: {e}")
        
    try:
        import generate_dashboard
        data = generate_dashboard.get_dashboard_data()
        geojson = generate_dashboard.get_geojson_data()
        prov_geojson = generate_dashboard.get_province_geojson_data()
        generate_dashboard.generate_html(data, geojson, prov_geojson)
    except Exception as e:
        print(f"[!] ไม่สามารถสร้าง Dashboard ได้: {e}")
