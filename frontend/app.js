(() => {
  "use strict";

  const byId = (id) => document.getElementById(id);
  const statusLine = byId("app-status");
  const latInput = byId("latitude");
  const lonInput = byId("longitude");
  const startInput = byId("start-date");
  const endInput = byId("end-date");
  const fetchButton = byId("fetch-button");
  const powerButton = byId("power-sonify-button");
  const satelliteButton = byId("satellite-sonify-button");
  const audioResult = byId("audio-result");
  const audioPlayer = byId("audio-player");
  const recordsBody = byId("records-body");
  const MAX_PERIOD_DAYS = 31;
  let map;
  let marker;
  let imageryLayer;
  let powerData = null;

  function localDateString(date) {
    const y = date.getFullYear();
    const m = String(date.getMonth() + 1).padStart(2, "0");
    const d = String(date.getDate()).padStart(2, "0");
    return `${y}-${m}-${d}`;
  }

  function setDefaultDates() {
    const end = new Date();
    end.setDate(end.getDate() - 3); // POWER and MODIS products can lag by several days.
    const start = new Date(end);
    start.setDate(start.getDate() - 6);
    startInput.value = localDateString(start);
    endInput.value = localDateString(end);
    endInput.max = localDateString(new Date());
    startInput.max = localDateString(new Date());
  }

  function setStatus(message, kind = "info") {
    statusLine.textContent = message;
    statusLine.className = `status-line ${kind}`;
  }

  function readCoordinates() {
    if (!latInput.value.trim() || !lonInput.value.trim()) {
      throw new Error("Latitude and longitude fields cannot be left empty.");
    }
    const latitude = Number(latInput.value);
    const longitude = Number(lonInput.value);
    if (!Number.isFinite(latitude) || latitude < -90 || latitude > 90) {
      throw new Error("Latitude must be between -90 and 90.");
    }
    if (!Number.isFinite(longitude) || longitude < -180 || longitude > 180) {
      throw new Error("Longitude must be between -180 and 180.");
    }
    return { latitude, longitude };
  }

  function readDates() {
    const start = startInput.value;
    const end = endInput.value;
    if (!start || !end) throw new Error("Select the start and end dates.");
    if (start < "1981-01-01") throw new Error("The NASA POWER daily data range begins on 1981-01-01.");
    if (start > end) throw new Error("The start date cannot be after the end date.");
    const startDate = new Date(`${start}T00:00:00`);
    const endDate = new Date(`${end}T00:00:00`);
    const days = Math.round((endDate - startDate) / 86400000) + 1;
    if (days > MAX_PERIOD_DAYS) throw new Error(`You can request a maximum of ${MAX_PERIOD_DAYS} days of data.`);
    if (end > localDateString(new Date())) throw new Error("The end date cannot be in the future.");
    return { start, end };
  }

  function updateCoordinateReadout() {
    const lat = Number(latInput.value);
    const lon = Number(lonInput.value);
    if (!Number.isFinite(lat) || !Number.isFinite(lon)) return;
    const latHemisphere = lat < 0 ? "S" : "N";
    const lonHemisphere = lon < 0 ? "W" : "E";
    byId("coordinate-readout").textContent = `${Math.abs(lat).toFixed(4)}° ${latHemisphere}, ${Math.abs(lon).toFixed(4)}° ${lonHemisphere}`;
  }

  function invalidatePowerData(reason = "The location or date has changed. You need to load new data.") {
    powerData = null;
    powerButton.disabled = true;
    byId("data-content").hidden = true;
    byId("empty-state").hidden = false;
    byId("empty-state").textContent = reason;
  }

  function syncMarker() {
    try {
      const { latitude, longitude } = readCoordinates();
      const point = [latitude, longitude];
      marker.setLatLng(point);
      map.panTo(point, { animate: true, duration: 0.35 });
      updateCoordinateReadout();
      invalidatePowerData();
    } catch (error) {
      setStatus(error.message, "error");
    }
  }

  function initMap() {
    if (!window.L) {
      setStatus("The map library could not be loaded. Check your internet connection and refresh the page.", "error");
      return;
    }
    map = L.map("map", { center: [39, 35], zoom: 5, scrollWheelZoom: true, keyboard: true });
    map.getContainer().setAttribute("tabindex", "0");
    map.getContainer().setAttribute("aria-label", "World map. Navigate with the arrow keys, zoom with + and -, and click to select a location.");

    const osm = L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 18,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
    }).addTo(map);

    imageryLayer = L.tileLayer.wms("https://gibs.earthdata.nasa.gov/wms/epsg3857/best/wms.cgi", {
      layers: "MODIS_Terra_CorrectedReflectance_TrueColor",
      styles: "",
      format: "image/jpeg",
      transparent: false,
      version: "1.1.1",
      time: endInput.value,
      attribution: 'NASA EOSDIS GIBS · MODIS Terra Corrected Reflectance True Color',
      opacity: 0.88
    }).addTo(map);

    L.control.layers({ "OpenStreetMap": osm }, { "NASA MODIS true color": imageryLayer }, { collapsed: true }).addTo(map);
    L.control.scale({ imperial: false }).addTo(map);
    marker = L.marker([39, 35], { draggable: true, title: "Selected data location", alt: "Selected data location" }).addTo(map);
    marker.bindPopup("Selected point for NASA POWER data");
    map.on("click", (event) => {
      latInput.value = event.latlng.lat.toFixed(4);
      lonInput.value = event.latlng.lng.toFixed(4);
      marker.setLatLng(event.latlng);
      updateCoordinateReadout();
      invalidatePowerData();
      setStatus("New location selected. First load NASA POWER data or sonify the satellite image of the selected location.");
    });
    marker.on("dragend", () => {
      const point = marker.getLatLng();
      latInput.value = point.lat.toFixed(4);
      lonInput.value = point.lng.toFixed(4);
      updateCoordinateReadout();
      invalidatePowerData();
      setStatus("Marker moved. Click the button to fetch NASA POWER data.");
    });
    map.whenReady(() => setStatus("Map ready. Select a point to load NASA POWER data."));
  }

  async function getJson(url, options) {
    const response = await fetch(url, options);
    let result;
    try { result = await response.json(); }
    catch { throw new Error(`Could not read a valid response from the server (HTTP ${response.status}).`); }
    if (!response.ok || result.ok === false) {
      throw new Error(result.error || `Request failed (HTTP ${response.status}).`);
    }
    return result;
  }

  function formatValue(value, digits = 1) {
    return value === null || value === undefined || !Number.isFinite(Number(value))
      ? "—"
      : Number(value).toLocaleString("en-US", { maximumFractionDigits: digits, minimumFractionDigits: digits });
  }

  function setMetric(id, value, suffix = "") {
    byId(id).textContent = value === null || value === undefined ? "—" : `${formatValue(value)}${suffix}`;
  }

  function renderData(data) {
    const summary = data.summary || {};
    setMetric("metric-temp", summary.T2M?.mean, "°");
    setMetric("metric-rain", summary.PRECTOTCORR?.total, "");
    setMetric("metric-wind", summary.WS10M?.mean, "");
    setMetric("metric-humidity", summary.RH2M?.mean, "%");
    byId("period-label").textContent = `${data.period.start} — ${data.period.end}`;
    recordsBody.replaceChildren();

    for (const row of data.records) {
      const tr = document.createElement("tr");
      const cells = [
        row.date,
        formatValue(row.T2M),
        formatValue(row.PRECTOTCORR),
        formatValue(row.WS10M),
        formatValue(row.RH2M)
      ];
      for (const value of cells) {
        const td = document.createElement("td");
        td.textContent = value;
        tr.appendChild(td);
      }
      recordsBody.appendChild(tr);
    }
    byId("power-note").textContent = `${data.source}. ${data.note} Values are daily measurements; averages and totals are calculated using only records with valid values on the selected dates.`;
    byId("empty-state").hidden = true;
    byId("data-content").hidden = false;
    powerButton.disabled = false;
  }

  async function loadPower(event) {
    event?.preventDefault();
    let coords;
    let dates;
    try {
      coords = readCoordinates();
      dates = readDates();
    } catch (error) {
      setStatus(error.message, "error");
      return;
    }

    fetchButton.disabled = true;
    fetchButton.textContent = "Fetching NASA POWER data…";
    powerButton.disabled = true;
    setStatus("Requesting NASA POWER daily data. This may take a few seconds depending on your internet connection.");
    try {
      const query = new URLSearchParams({
        latitude: String(coords.latitude),
        longitude: String(coords.longitude),
        start: dates.start,
        end: dates.end
      });
      const result = await getJson(`/api/power?${query.toString()}`);
      // Do not show a stale response if another point/date was selected while the request was in progress.
      const latestCoords = readCoordinates();
      const latestDates = readDates();
      if (latestCoords.latitude !== coords.latitude || latestCoords.longitude !== coords.longitude ||
          latestDates.start !== dates.start || latestDates.end !== dates.end) {
        throw new Error("The location or date changed during the request. Fetch NASA data again for the new selection.");
      }
      powerData = result.data;
      renderData(powerData);
      setStatus(`${powerData.records.length} daily NASA POWER records loaded. The data can now be sonified.`, "success");
    } catch (error) {
      powerData = null;
      byId("empty-state").hidden = false;
      byId("data-content").hidden = true;
      setStatus(error.message, "error");
    } finally {
      fetchButton.disabled = false;
      fetchButton.innerHTML = 'Fetch NASA data <span aria-hidden="true">↗</span>';
      powerButton.disabled = !powerData;
    }
  }

  async function createPowerAudio() {
    let coords;
    let dates;
    try {
      coords = readCoordinates();
      dates = readDates();
    } catch (error) {
      setStatus(error.message, "error");
      return;
    }
    powerButton.disabled = true;
    powerButton.textContent = "Generating audio…";
    setStatus("Passing NASA POWER data to the sound engine…");
    try {
      const result = await getJson("/api/sonify/power", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...coords, ...dates })
      });
      const point = result.coordinates;
      showAudio(
        result,
        `NASA POWER — ${point.latitude.toFixed(4)}°, ${point.longitude.toFixed(4)}°`,
        `Data for the selected coordinates from ${result.period.start}–${result.period.end} was sonified. Temperature notes, precipitation rhythm, wind texture, and a humidity background were used.`
      );
      setStatus(result.message, "success");
    } catch (error) {
      setStatus(error.message, "error");
    } finally {
      powerButton.disabled = !powerData;
      powerButton.innerHTML = 'Sonify POWER data <span aria-hidden="true">♫</span>';
    }
  }

  function showAudio(result, title, description) {
    // Each response has a unique WAV URL; also clear the previous audio buffer.
    audioPlayer.pause();
    audioPlayer.removeAttribute("src");
    audioPlayer.load();
    const separator = result.audio_url.includes("?") ? "&" : "?";
    const playbackUrl = `${result.audio_url}${separator}v=${Date.now()}`;
    byId("audio-title").textContent = title;
    byId("audio-description").textContent = result.caution ? `${description} ${result.caution}` : description;
    audioPlayer.src = playbackUrl;
    audioPlayer.load();
    byId("audio-download").href = `${result.audio_url}${separator}download=1`;
    byId("audio-download").download = result.file || "stormwalkers.wav";
    audioResult.hidden = false;
  }

  async function createSatelliteAudio() {
    if (!map) {
      setStatus("The map is not ready yet.", "error");
      return;
    }
    let coords;
    let dates;
    try {
      coords = readCoordinates();
      dates = readDates();
    } catch (error) {
      setStatus(error.message, "error");
      return;
    }

    // The old behavior sonified the entire screen; a map click only moved the marker,
    // so the same screen bounds could repeatedly generate the same WAV.
    // Now we keep the area size at the current map zoom and center it on the selected coordinates.
    const viewBounds = map.getBounds();
    const halfLon = Math.min(35, Math.max(1.5, (viewBounds.getEast() - viewBounds.getWest()) / 2));
    const halfLat = Math.min(20, Math.max(1.0, (viewBounds.getNorth() - viewBounds.getSouth()) / 2));
    const bbox = [
      Math.max(-180, coords.longitude - halfLon),
      Math.max(-85, coords.latitude - halfLat),
      Math.min(180, coords.longitude + halfLon),
      Math.min(85, coords.latitude + halfLat)
    ];
    if (bbox[0] >= bbox[2] || bbox[1] >= bbox[3]) {
      setStatus("Could not create a valid satellite area around the selected location.", "error");
      return;
    }

    satelliteButton.disabled = true;
    satelliteButton.textContent = "Fetching selected location…";
    setStatus(`Downloading and sonifying NASA GIBS imagery around ${coords.latitude.toFixed(4)}, ${coords.longitude.toFixed(4)}…`);
    try {
      const result = await getJson("/api/sonify/satellite", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ bbox, date: dates.end, ...coords })
      });
      const point = result.coordinates;
      showAudio(
        result,
        `NASA GIBS — ${point.latitude.toFixed(4)}°, ${point.longitude.toFixed(4)}°`,
        `Color patterns from the MODIS satellite image dated ${dates.end} around the selected coordinates were sonified.`
      );
      setStatus(result.message, "success");
    } catch (error) {
      setStatus(error.message, "error");
    } finally {
      satelliteButton.disabled = false;
      satelliteButton.innerHTML = 'Sonify selected location <span aria-hidden="true">◌</span>';
    }
  }

  function selectMapCenter() {
    if (!map) return;
    const center = map.getCenter();
    latInput.value = center.lat.toFixed(4);
    lonInput.value = center.lng.toFixed(4);
    marker.setLatLng(center);
    updateCoordinateReadout();
    invalidatePowerData();
    setStatus("The map center was selected as the new location. You can reload NASA POWER data.");
  }

  function updateImageryDate() {
    if (!imageryLayer) return;
    imageryLayer.setParams({ time: endInput.value });
  }

  setDefaultDates();
  updateCoordinateReadout();
  initMap();

  byId("query-form").addEventListener("submit", loadPower);
  powerButton.addEventListener("click", createPowerAudio);
  satelliteButton.addEventListener("click", createSatelliteAudio);
  byId("select-center-button").addEventListener("click", selectMapCenter);
  latInput.addEventListener("change", syncMarker);
  lonInput.addEventListener("change", syncMarker);
  endInput.addEventListener("change", () => {
    updateImageryDate();
    invalidatePowerData("The date has changed. Reload NASA POWER data for the new date range.");
  });
  startInput.addEventListener("change", () => {
    if (startInput.value && endInput.value && startInput.value > endInput.value) {
      endInput.value = startInput.value;
      updateImageryDate();
    }
    invalidatePowerData("The date has changed. Reload NASA POWER data for the new date range.");
  });
})();
