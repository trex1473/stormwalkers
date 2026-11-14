# Stormwalkers 🌍

**The Earth Information Jukebox**

Stormwalkers is a small project where I try turning some data about Earth into sound. You select a place on the map; then we retrieve NASA data for that location and turn it into a listenable WAV file.

The project is still a work in progress. In particular, the results provided by NASA services may vary depending on the internet connection and selected date.

## What's included?

- Select a location on the map
- Display satellite imagery from NASA GIBS
- Retrieve temperature, precipitation, wind, and humidity data from NASA POWER
- Convert data into WAV audio
- A keyboard-accessible interface designed with screen readers in mind

## Technologies used

- Python
- HTML, CSS, and JavaScript
- Leaflet (map)
- NASA GIBS
- NASA POWER
- NumPy and Pillow

## Setup

On Linux, open a terminal in the project folder and run these commands in order:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python server.py
```

Then open this address in your browser:

```text
http://127.0.0.1:8000/frontend/
```

To stop the server, press `Ctrl + C` in the terminal. To exit the virtual environment, enter `deactivate`.

If you get an `Address already in use` error, port 8000 may already be in use. In that case, stop the existing server or start it on another port:

```bash
PORT=8001 python server.py
```

The address will then be `http://127.0.0.1:8001/frontend/`.

## How do I try it?

1. Click a location on the map.
2. You can also change the latitude and longitude manually in the input fields.
3. To try NASA POWER, choose a date range and click **Fetch NASA data**.
4. After the data loads, click **Sonify POWER data**.
5. To sonify the satellite image, click **Sonify selected location**.

You can select a maximum of 31 days at a time for POWER data. Daily data may occasionally be delayed by several days.

## How is the sound created?

There are two separate methods:

**NASA POWER mode:** Temperature affects pitch, precipitation affects rhythm, and wind affects the texture of the sound. Humidity also contributes to the background sound.

**NASA GIBS mode:** The colors of a satellite image taken around the selected location are passed to the sound engine.

A small but important distinction: The sound generated from the satellite image does not directly measure temperature or storm intensity. It converts image colors into sound. For numerical weather measurements, use POWER mode.

## Files

- `server.py` — Python server that runs the web interface and NASA requests
- `stormwalkers_engine.py` — sound engine that generates WAV from images/data
- `frontend/` — web page
- `synthetic_storm.jpg` — sample image for testing, not retrieved from NASA
- `stormwalkers_final.wav` and `storm_sonification.wav` — sample audio files in the project

## About GitHub

This repository is for sharing the project files. GitHub Pages alone cannot run this project because it also requires a Python server for NASA POWER requests and WAV generation. You can first upload the code to GitHub; if you want to make a live website, you will need to deploy it to a separate server that supports Python.

## Sources

- NASA GIBS: https://nasa-gibs.github.io/gibs-api-docs/
- NASA POWER Daily API: https://power.larc.nasa.gov/docs/services/api/temporal/daily/
- Leaflet: https://leafletjs.com/

---
This project is being developed for learning and experimentation. I welcome contributions, suggestions, and bug reports.
