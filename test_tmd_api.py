import os
import json
import requests

def test_fetch_area():
    token_path = os.path.join(os.path.dirname(__file__), "Token_TMD.txt")
    with open(token_path, "r", encoding="utf-8") as f:
        token = f.read().strip()

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json"
    }

    # ทดสอบเรียก /area/place สำหรับจังหวัดชลบุรี
    url = "https://data.tmd.go.th/nwpapi/v1/forecast/area/place"
    params = {
        "province": "ชลบุรี",
        "fields": "rain,cond"
    }
    print(f"กำลังทดสอบเรียก API: {url} (province: ชลบุรี) ...")
    try:
        response = requests.get(url, headers=headers, params=params, timeout=15)
        print(f"Status Code: {response.status_code}")
        if response.status_code == 200:
            data = response.json()
            if "WeatherForecasts" in data:
                forecasts = data["WeatherForecasts"]
            elif isinstance(data, list):
                forecasts = data
            else:
                forecasts = [data]
            
            print(f"จำนวนรายการที่ได้รับ: {len(forecasts)}")
            for i, item in enumerate(forecasts[:5]):
                loc = item.get("location", {})
                f_list = item.get("forecasts", [])
                rain = f_list[0]["data"].get("rain") if f_list else None
                print(f"[{i+1}] {loc.get('areatype')}: {loc.get('name')} (Geocode: {loc.get('geocode')}) - Rain: {rain} มม.")
        else:
            print(f"Error {response.status_code}: {response.text}")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    test_fetch_area()
