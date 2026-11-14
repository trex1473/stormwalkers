#!/usr/bin/env python3
"""Local web server for Stormwalkers + NASA GIBS and NASA POWER."""
from __future__ import annotations

import json
import mimetypes
import os
import re
import sys
import uuid
from datetime import date, datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

from PIL import Image

import stormwalkers_engine as engine

ROOT = Path(__file__).resolve().parent
FRONTEND_DIR = ROOT / "frontend"
POWER_WAV = ROOT / "stormwalkers_power.wav"
SATELLITE_WAV = ROOT / "stormwalkers_satellite.wav"
AUDIO_FILENAME_RE = re.compile(r"stormwalkers_(?:power|satellite)_[a-f0-9]{12}\.wav\Z")

POWER_API = "https://power.larc.nasa.gov/api/temporal/daily/point"
GIBS_WMS = "https://gibs.earthdata.nasa.gov/wms/epsg4326/best/wms.cgi"
POWER_PARAMETERS = ("T2M", "PRECTOTCORR", "WS10M", "RH2M")
POWER_UNITS = {
    "T2M": "°C",
    "PRECTOTCORR": "mm/day",
    "WS10M": "m/s",
    "RH2M": "%",
}
MIN_POWER_DATE = date(1981, 1, 1)
MAX_PERIOD_DAYS = 31


def parse_iso_date(value: str, field_name: str) -> date:
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        raise ValueError(f"{field_name} date must be in YYYY-MM-DD format.")
    return parsed


def validate_period(start_text: str, end_text: str) -> tuple[date, date]:
    start = parse_iso_date(start_text, "Start")
    end = parse_iso_date(end_text, "End")
    today = date.today()
    if start < MIN_POWER_DATE:
        raise ValueError("The NASA POWER daily data range must start on or after 1981-01-01.")
    if end > today:
        raise ValueError("The end date cannot be in the future.")
    if start > end:
        raise ValueError("The start date cannot be after the end date.")
    if (end - start).days + 1 > MAX_PERIOD_DAYS:
        raise ValueError(f"A single request can include at most {MAX_PERIOD_DAYS} days of data.")
    return start, end


def validate_coordinates(latitude, longitude) -> tuple[float, float]:
    try:
        lat = float(latitude)
        lon = float(longitude)
    except (TypeError, ValueError):
        raise ValueError("Latitude and longitude must be numeric.")
    if not (-90 <= lat <= 90):
        raise ValueError("Latitude must be between -90 and 90.")
    if not (-180 <= lon <= 180):
        raise ValueError("Longitude must be between -180 and 180.")
    return lat, lon


def fetch_power(latitude: float, longitude: float, start: date, end: date) -> dict:
    """Fetch a single point's daily observations from the public NASA POWER API."""
    params = {
        "parameters": ",".join(POWER_PARAMETERS),
        "community": "RE",
        "longitude": f"{longitude:.5f}",
        "latitude": f"{latitude:.5f}",
        "start": start.strftime("%Y%m%d"),
        "end": end.strftime("%Y%m%d"),
        "format": "JSON",
        "time-standard": "UTC",
    }
    url = POWER_API + "?" + urlencode(params)
    request = Request(
        url,
        headers={
            "User-Agent": "Stormwalkers/1.0 (educational NASA POWER integration)",
            "Accept": "application/json",
        },
    )
    try:
        with urlopen(request, timeout=45) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"NASA POWER HTTP {exc.code}: {detail or exc.reason}") from exc
    except URLError as exc:
        raise RuntimeError(f"Could not reach the NASA POWER server: {exc.reason}") from exc
    except TimeoutError as exc:
        raise RuntimeError("The NASA POWER request timed out.") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("NASA POWER did not return valid JSON data.") from exc

    try:
        parameter_data = payload["properties"]["parameter"]
    except (KeyError, TypeError) as exc:
        messages = payload.get("messages", []) if isinstance(payload, dict) else []
        detail = "; ".join(str(item) for item in messages) if messages else "Expected data was not found in the API response."
        raise RuntimeError(f"Could not read the NASA POWER response: {detail}") from exc

    date_keys = sorted(parameter_data.get("T2M", {}).keys())
    records = []
    for day_key in date_keys:
        row = {"date": f"{day_key[0:4]}-{day_key[4:6]}-{day_key[6:8]}"}
        for key in POWER_PARAMETERS:
            raw = parameter_data.get(key, {}).get(day_key)
            try:
                value = float(raw) if raw is not None else None
            except (TypeError, ValueError):
                value = None
            if value is not None and (value <= -900 or not (-1e6 < value < 1e6)):
                value = None
            row[key] = value
        records.append(row)

    if not records:
        raise RuntimeError("NASA POWER returned no daily records for the selected date range. Try an earlier date range.")
    if not any(row["T2M"] is not None for row in records):
        raise RuntimeError("NASA POWER returned no usable temperature data for this location and these dates.")

    values = {
        key: [row[key] for row in records if row[key] is not None]
        for key in POWER_PARAMETERS
    }
    summary = {}
    for key, items in values.items():
        if items:
            summary[key] = {
                "mean": round(sum(items) / len(items), 2),
                "min": round(min(items), 2),
                "max": round(max(items), 2),
                "total": round(sum(items), 2) if key == "PRECTOTCORR" else None,
            }
        else:
            summary[key] = None

    return {
        "source": "NASA POWER Daily API",
        "requested_coordinates": {"latitude": latitude, "longitude": longitude},
        "period": {"start": start.isoformat(), "end": end.isoformat()},
        "units": POWER_UNITS,
        "parameters": {
            "T2M": "Air temperature at 2 meters",
            "PRECTOTCORR": "Corrected precipitation amount",
            "WS10M": "Wind speed at 10 meters",
            "RH2M": "Relative humidity at 2 meters",
        },
        "records": records,
        "summary": summary,
        "note": "NASA POWER provides daily point data; the coordinates may represent the nearest cell in the POWER data grid.",
    }


def fetch_gibs_image(bbox: list[float], image_date: date) -> bytes:
    """Get one custom-bounds MODIS true-colour image from NASA GIBS WMS."""
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        raise ValueError("Map bounds must be in west,south,east,north format.")
    west, south, east, north = [float(v) for v in bbox]
    if not all(-180 <= v <= 180 for v in (west, east)):
        raise ValueError("Invalid map longitude bounds.")
    if not all(-85 <= v <= 85 for v in (south, north)) or south >= north or west >= east:
        raise ValueError("Invalid map area. Areas crossing the antimeridian are not supported in this view.")

    params = {
        "SERVICE": "WMS",
        "VERSION": "1.1.1",
        "REQUEST": "GetMap",
        "LAYERS": "MODIS_Terra_CorrectedReflectance_TrueColor",
        "STYLES": "",
        "SRS": "EPSG:4326",
        "BBOX": ",".join(f"{v:.6f}" for v in (west, south, east, north)),
        "WIDTH": "960",
        "HEIGHT": "540",
        "FORMAT": "image/jpeg",
        "TRANSPARENT": "FALSE",
        "TIME": image_date.isoformat(),
    }
    request = Request(
        GIBS_WMS + "?" + urlencode(params),
        headers={"User-Agent": "Stormwalkers/1.0 (educational NASA GIBS integration)", "Accept": "image/jpeg"},
    )
    try:
        with urlopen(request, timeout=60) as response:
            content_type = response.headers.get("Content-Type", "")
            content = response.read()
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:400]
        raise RuntimeError(f"NASA GIBS HTTP {exc.code}: {detail or exc.reason}") from exc
    except URLError as exc:
        raise RuntimeError(f"Could not reach the NASA GIBS server: {exc.reason}") from exc
    except TimeoutError as exc:
        raise RuntimeError("The NASA GIBS image request timed out.") from exc

    if not content_type.lower().startswith("image/"):
        detail = content[:400].decode("utf-8", errors="replace")
        raise RuntimeError("NASA GIBS returned a service message instead of an image. No image may be available for the selected date/region. " + detail)
    try:
        from io import BytesIO
        with Image.open(BytesIO(content)) as image:
            image.verify()
    except Exception as exc:
        raise RuntimeError("The NASA GIBS response is not a valid image.") from exc
    return content


class StormwalkersHandler(SimpleHTTPRequestHandler):
    server_version = "StormwalkersLocal/1.0"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def log_message(self, fmt, *args):
        sys.stdout.write("[Stormwalkers] " + fmt % args + "\n")

    def send_json(self, status: int, payload: dict):
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(encoded)

    def read_json(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise ValueError("Invalid request body size.")
        if length <= 0 or length > 20_000:
            raise ValueError("The request body is empty or larger than expected.")
        try:
            result = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("The request is not in JSON format.") from exc
        if not isinstance(result, dict):
            raise ValueError("The request body must be a JSON object.")
        return result

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self.send_response(302)
            self.send_header("Location", "/frontend/")
            self.end_headers()
            return
        if parsed.path == "/api/health":
            self.send_json(200, {"ok": True, "app": "Stormwalkers", "nasa": ["GIBS WMS", "POWER Daily API"]})
            return
        if parsed.path == "/api/power":
            query = parse_qs(parsed.query)
            try:
                lat, lon = validate_coordinates(
                    query.get("latitude", [None])[0], query.get("longitude", [None])[0]
                )
                start, end = validate_period(
                    query.get("start", [None])[0], query.get("end", [None])[0]
                )
                result = fetch_power(lat, lon, start, end)
                self.send_json(200, {"ok": True, "data": result})
            except ValueError as exc:
                self.send_json(400, {"ok": False, "error": str(exc)})
            except RuntimeError as exc:
                self.send_json(502, {"ok": False, "error": str(exc)})
            return
        if parsed.path == "/api/audio":
            query = parse_qs(parsed.query)
            filename = query.get("file", [None])[0]
            if filename:
                # Allow only unpredictable WAV filenames generated by the server.
                if not AUDIO_FILENAME_RE.fullmatch(filename):
                    self.send_json(400, {"ok": False, "error": "Invalid audio filename."})
                    return
                path = ROOT / filename
            else:
                # Backward compatibility with older links.
                source = query.get("source", ["power"])[0]
                path = POWER_WAV if source == "power" else SATELLITE_WAV if source == "satellite" else None
            if path is None or not path.is_file():
                self.send_json(404, {"ok": False, "error": "The requested audio file has not been generated yet."})
                return
            disposition = "attachment" if query.get("download", ["0"])[0] == "1" else "inline"
            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(path.stat().st_size))
            self.send_header("Content-Disposition", f'{disposition}; filename="{path.name}"')
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
            self.send_header("Pragma", "no-cache")
            self.end_headers()
            with path.open("rb") as handle:
                while chunk := handle.read(64 * 1024):
                    self.wfile.write(chunk)
            return
        return super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        try:
            body = self.read_json()
            if parsed.path == "/api/sonify/power":
                lat, lon = validate_coordinates(body.get("latitude"), body.get("longitude"))
                start, end = validate_period(body.get("start"), body.get("end"))
                result = fetch_power(lat, lon, start, end)
                token = uuid.uuid4().hex[:12]
                output_path = ROOT / f"stormwalkers_power_{token}.wav"
                output = engine.sonify_power_series(result["records"], output_path)
                self.send_json(200, {
                    "ok": True,
                    "audio_url": f"/api/audio?file={Path(output).name}",
                    "file": Path(output).name,
                    "coordinates": {"latitude": lat, "longitude": lon},
                    "period": {"start": start.isoformat(), "end": end.isoformat()},
                    "summary": result["summary"],
                    "units": result["units"],
                    "records_count": len(result["records"]),
                    "message": f"A new WAV was generated using NASA POWER data for {lat:.4f}, {lon:.4f}.",
                })
                return

            if parsed.path == "/api/sonify/satellite":
                image_date = parse_iso_date(body.get("date"), "Satellite imagery")
                lat, lon = validate_coordinates(body.get("latitude"), body.get("longitude"))
                bbox = body.get("bbox")
                image_bytes = fetch_gibs_image(bbox, image_date)
                token = uuid.uuid4().hex[:12]
                image_path = ROOT / f"nasa_gibs_{token}.jpg"
                image_path.write_bytes(image_bytes)
                rgb, source = engine.load_rgb(image_path)
                features = engine.analyze(rgb)
                left, right = engine.synthesize(features)
                output_path = ROOT / f"stormwalkers_satellite_{token}.wav"
                output = engine.write_wav(left, right, output_path)
                engine.print_summary(features, f"NASA GIBS MODIS Terra True Color — {image_date.isoformat()} ({lat:.4f}, {lon:.4f}; {source})", output)
                self.send_json(200, {
                    "ok": True,
                    "audio_url": f"/api/audio?file={Path(output).name}",
                    "file": Path(output).name,
                    "coordinates": {"latitude": lat, "longitude": lon},
                    "period": {"date": image_date.isoformat()},
                    "message": f"A new WAV was generated from NASA GIBS imagery around the selected location ({lat:.4f}, {lon:.4f}).",
                    "caution": "This audio represents color patterns in satellite imagery around the selected coordinates; it is not a direct measurement of temperature, precipitation, or storm intensity.",
                })
                return

            self.send_json(404, {"ok": False, "error": "Unknown API path."})
        except ValueError as exc:
            self.send_json(400, {"ok": False, "error": str(exc)})
        except RuntimeError as exc:
            self.send_json(502, {"ok": False, "error": str(exc)})
        except Exception as exc:
            self.send_json(500, {"ok": False, "error": f"Unexpected server-side error: {exc}"})


def main():
    host = os.environ.get("STORMWALKERS_HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8000"))
    if not FRONTEND_DIR.is_dir():
        raise SystemExit("The frontend/ folder was not found. Run the application from the project root.")
    server = ThreadingHTTPServer((host, port), StormwalkersHandler)
    print("\nStormwalkers ready")
    print(f"Interface: http://{host}:{port}/frontend/")
    print(f"Health check: http://{host}:{port}/api/health")
    print("Press Ctrl+C to stop\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down Stormwalkers...")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
