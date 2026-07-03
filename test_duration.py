import os
import json
import requests

def test_params():
    token_path = os.path.join(os.path.dirname(__file__), "Token_TMD.txt")
    with open(token_path, "r", encoding="utf-8") as f:
        token = f.read().strip()

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json"
    }

    url_daily = "https://data.tmd.go.th/nwpapi/v1/forecast/location/daily/place"
    url_hourly = "https://data.tmd.go.th/nwpapi/v1/forecast/location/hourly/place"
    
    # ทดสอบ daily พารามิเตอร์ต่างๆ
    test_cases = [
        ("Daily: duration=10", url_daily, {"province": "ชลบุรี", "amphoe": "เมืองชลบุรี", "duration": 10, "fields": "rain"}),
        ("Daily: days=10", url_daily, {"province": "ชลบุรี", "amphoe": "เมืองชลบุรี", "days": 10, "fields": "rain"}),
        ("Daily: starttime/endtime", url_daily, {"province": "ชลบุรี", "amphoe": "เมืองชลบุรี", "starttime": "2026-06-30T00:00:00", "endtime": "2026-07-09T00:00:00", "fields": "rain"}),
        ("Hourly: duration=240", url_hourly, {"province": "ชลบุรี", "amphoe": "เมืองชลบุรี", "duration": 240, "fields": "rain"})
    ]

    for name, url, params in test_cases:
        print(f"\n---> ทดสอบ: {name}")
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=10)
            print(f"Status: {resp.status_code}")
            if resp.status_code == 200:
                data = resp.json()
                wf = data.get("WeatherForecasts", [{}])[0] if "WeatherForecasts" in data else data
                forecasts = wf.get("forecasts", [])
                print(f"จำนวนข้อมูลที่ได้: {len(forecasts)} รายการ")
                if forecasts:
                    print(f"วันเริ่มต้น: {forecasts[0]['time']} | วันสิ้นสุด: {forecasts[-1]['time']}")
            else:
                print(f"Error: {resp.text}")
        except Exception as e:
            print(f"Exception: {e}")

if __name__ == "__main__":
    test_params()
