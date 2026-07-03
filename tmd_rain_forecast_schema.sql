-- ============================================================================
-- ฐานข้อมูลระบบเก็บข้อมูลพยากรณ์ฝนภาคตะวันออก แบบ Grid พิกัดความละเอียดสูง (5x5 กม.)
-- รองรับระบบดึงข้อมูลแบบ Anchor Stations ป้องกันการถูกบล็อก และ IDW Spatial Interpolation
-- ============================================================================

-- ----------------------------------------------------------------------------
-- โครงสร้างตารางสำหรับ SQLite (ใช้จริงใน eastern_rain.db)
-- ----------------------------------------------------------------------------

-- 1. ตารางจุดสถานีหลัก (Anchor Stations) 67 จุดที่ใช้ดึงค่าจาก TMD NWP API
CREATE TABLE IF NOT EXISTS anchor_stations (
    station_id INTEGER PRIMARY KEY AUTOINCREMENT,
    amphoe_name TEXT NOT NULL,
    province_name TEXT NOT NULL,
    lat REAL NOT NULL,
    lon REAL NOT NULL,
    geocode TEXT,
    UNIQUE(amphoe_name, province_name)
);

-- 2. ตารางพยากรณ์ฝนของจุดสถานีหลักจาก API (Anchor Daily Forecast)
CREATE TABLE IF NOT EXISTS anchor_forecast_daily (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    station_id INTEGER REFERENCES anchor_stations(station_id) ON DELETE CASCADE,
    forecast_date TEXT NOT NULL,
    rain_mm REAL DEFAULT 0.0,
    cond_code INTEGER,
    fetched_at TEXT,
    UNIQUE(station_id, forecast_date)
);

-- 3. ตารางจุดพิกัด Grid ความละเอียดสูง 0.05 องศา (~5x5 กม.) รวม 1,216 จุดครอบคลุมภาคตะวันออก
CREATE TABLE IF NOT EXISTS grid_points (
    point_id INTEGER PRIMARY KEY AUTOINCREMENT,
    lat REAL NOT NULL,
    lon REAL NOT NULL,
    amphoe_name TEXT,    -- ขอบเขตอำเภอที่จุดกริดตกอยู่ (จาก Shapefile Point-in-Polygon)
    province_name TEXT,  -- ขอบเขตจังหวัดที่จุดกริดตกอยู่
    UNIQUE(lat, lon)
);

-- 4. ตารางพยากรณ์ฝนตามจุด Grid (1,216 จุด) ที่ได้จาก IDW Spatial Interpolation
CREATE TABLE IF NOT EXISTS grid_forecast_daily (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    point_id INTEGER REFERENCES grid_points(point_id) ON DELETE CASCADE,
    forecast_date TEXT NOT NULL,
    rain_mm REAL DEFAULT 0.0,
    interpolated_at TEXT,
    UNIQUE(point_id, forecast_date)
);

-- 5. ตารางสรุปผลจากการประมวลผลเฉลี่ยจุดกริด 18-25 จุดในแต่ละแปลง Shapefile (รายอำเภอ)
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

-- 6. ตารางสรุปผลจากการประมวลผลเชิงพื้นที่ (รายจังหวัด)
CREATE TABLE IF NOT EXISTS spatial_province_summary (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    province_name TEXT NOT NULL,
    forecast_date TEXT NOT NULL,
    avg_rain_mm REAL DEFAULT 0.0,
    total_points INTEGER DEFAULT 0,
    processed_at TEXT,
    UNIQUE(province_name, forecast_date)
);
