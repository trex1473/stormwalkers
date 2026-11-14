#!/usr/bin/env python3
# Stormwalkers — The Earth Information Jukebox
# Scans the satellite image from left to right, converts its colors into a C minor pentatonic
# stereo piece, and writes stormwalkers_final.wav.

import hashlib
import os
import struct
import wave

import numpy as np
from PIL import Image

# Fixed audio quality and duration.
SAMPLE_RATE = 44100
DURATION = 10.0

# C minor pentatonic (C, Eb, F, G, Bb). From bottom to top, in the same order as the y-axis.
# Frequencies are taken from equal temperament with A4 = 440 Hz.
SCALE_HZ = (
    130.8128,  # C3
    155.5635,  # Eb3
    174.6141,  # F3
    195.9977,  # G3
    233.0819,  # Bb3
    261.6256,  # C4
    311.1270,  # Eb4
    349.2282,  # F4
    391.9954,  # G4
    466.1638,  # Bb4
    523.2511,  # C5
)
SCALE_NAMES = (
    "C3", "Eb3", "F3", "G3", "Bb3",
    "C4", "Eb4", "F4", "G4", "Bb4", "C5",
)
# The tension bass also uses low notes of the same key; no out-of-scale high note is generated.
SUB_C1 = 32.7032
SUB_G1 = 48.9994
PULSE_BB1 = 58.2705
PULSE_C2 = 65.4064

NOTE_COUNT = len(SCALE_HZ)


def script_dir():
    # The folder containing the script; images and WAV files are read/written here.
    return os.path.dirname(os.path.abspath(__file__))


def find_satellite_image():
    # First look for the NASA image, then the local sample image in the ZIP. Return None if neither exists.
    folder = script_dir()
    for name in ("nasa_storm.jpg", "nasa_storm.png", "synthetic_storm.jpg"):
        path = os.path.join(folder, name)
        if os.path.isfile(path):
            return path
    return None


def make_synthetic_storm(width=960, height=540):
    # A colorful hurricane for testing: blue ocean, white spiral arms, orange eyewall.
    y, x = np.mgrid[0:height, 0:width]
    cx = (width - 1) * 0.46
    cy = (height - 1) * 0.50
    dx = (x - cx) / (width * 0.40)
    dy = (y - cy) / (height * 0.40)
    radius = np.sqrt(dx * dx + dy * dy) + 1e-6
    theta = np.arctan2(dy, dx)

    eye = np.exp(-(radius ** 2) / (2.0 * 0.055 ** 2))
    eyewall = np.exp(-((radius - 0.18) ** 2) / (2.0 * 0.048 ** 2))
    spiral = np.clip(np.cos(theta * 2.0 - 2.6 * np.log(radius)), 0.0, 1.0) ** 2
    spiral *= np.exp(-((radius - 0.50) ** 2) / (2.0 * 0.22 ** 2))
    spiral *= 1.0 - eye
    outer = np.exp(-((radius - 0.78) ** 2) / (2.0 * 0.12 ** 2))
    outer *= 0.45 + 0.55 * (0.5 + 0.5 * np.sin(3.0 * theta + 4.0 * radius))

    # Cloud cover thickens toward the east; the west remains calmer.
    grow = np.clip((dx + 1.15) / 1.90, 0.22, 1.0)
    cloud = np.clip((0.98 * spiral + 0.62 * outer) * grow, 0.0, 1.0)
    # The warm core is in the eastern half. As dx increases, the image moves right, toward the east.
    hot_spot = np.exp(-((dx - 0.72) ** 2 + (dy - 0.02) ** 2) / (2.0 * 0.16 ** 2))
    heat = np.clip(hot_spot + 0.30 * eyewall * np.clip(dx, 0.0, 1.0), 0.0, 1.0)
    heat *= 1.0 - eye

    ocean_b = 0.18 + 0.06 * np.sin(x / 33.0 + y / 21.0)
    red = np.clip(0.02 + cloud * 0.92 + heat * 1.05, 0.0, 1.0)
    green = np.clip(0.05 + cloud * 0.96 + heat * 0.36, 0.0, 1.0)
    # Heat suppresses the ocean blue; red/orange danger remains distinct.
    blue = np.clip(ocean_b * (1.0 - 0.85 * heat) + cloud * 0.98 + heat * 0.06, 0.0, 1.0)

    red *= 1.0 - 0.94 * eye
    green *= 1.0 - 0.94 * eye
    blue = np.clip(blue * (1.0 - 0.80 * eye) + eye * 0.03, 0.0, 1.0)

    pixels = np.dstack((red, green, blue))
    pixels = (pixels * 255.0).astype(np.uint8)
    return Image.fromarray(pixels, mode="RGB")


def load_rgb(image_path=None):
    # An optional explicit file path makes the NASA GIBS request process this exact image.
    # If no parameter is given, the first available image is used for local execution.
    path = os.path.abspath(os.fspath(image_path)) if image_path else find_satellite_image()
    if path is None:
        print("nasa_storm.jpg / nasa_storm.png not found. A synthetic colorful hurricane was generated.")
        image = make_synthetic_storm()
        source = "synthetic colorful hurricane"
    else:
        try:
            image = Image.open(path)
            image.load()
            source = path
        except (OSError, ValueError):
            print("Could not read the image. A synthetic colorful hurricane was generated.")
            image = make_synthetic_storm()
            source = "synthetic colorful hurricane"

    image = image.convert("RGB")
    image.thumbnail((960, 540), Image.Resampling.LANCZOS)
    rgb = np.asarray(image, dtype=np.float64) / 255.0
    return rgb, source


def smooth(values, window):
    # Smooth control curves to avoid clicks at column boundaries.
    window = int(window)
    if window < 3 or values.size < window:
        return values
    if window % 2 == 0:
        window += 1
    kernel = np.hanning(window)
    kernel /= kernel.sum()
    return np.convolve(values, kernel, mode="same")


def column_strength(field, fraction):
    # Brightest slice of the column. Prevent a thin eyewall from disappearing into the dark sea.
    height = field.shape[0]
    keep = max(1, int(round(height * fraction)))
    chosen = np.partition(field, height - keep, axis=0)[-keep:, :]
    return chosen.mean(axis=0)


def analyze(rgb):
    # Each column represents a time interval from west to east.
    red = rgb[:, :, 0]
    green = rgb[:, :, 1]
    blue = rgb[:, :, 2]
    height, width, _ = rgb.shape

    # Red/orange: heat and danger. Not considered dangerous if blue dominates.
    danger_px = np.clip(red - blue, 0.0, 1.0) * (0.75 + 0.25 * green)
    danger = column_strength(danger_px, 0.06)

    # Blue: precipitation and ocean. Red heat suppresses this.
    rain = column_strength(np.clip(blue - 0.45 * red, 0.0, 1.0), 0.10)

    # White: storm cloud when all three channels are bright.
    white_px = np.min(rgb, axis=2)
    cloud = column_strength(white_px, 0.10)

    # Vertical center of the cloud. 0 is the bottom edge, 1 is the top edge (higher note).
    row_height = np.linspace(1.0, 0.0, height)
    weight = white_px + 1e-6
    cloud_height = (weight * row_height[:, None]).sum(axis=0) / weight.sum(axis=0)

    # Row bands are assigned to notes: C5 at the top of the image, C3 at the bottom.
    bands = np.zeros((width, NOTE_COUNT), dtype=np.float64)
    for band in range(NOTE_COUNT):
        start = int(round(band * height / NOTE_COUNT))
        end = int(round((band + 1) * height / NOTE_COUNT))
        end = min(height, max(start + 1, end))
        note = NOTE_COUNT - 1 - band
        bands[:, note] = column_strength(white_px[start:end], 0.40)

    # Concentrate energy on the actual note closest to the cloud height.
    focus = cloud_height * (NOTE_COUNT - 1)
    offsets = np.arange(NOTE_COUNT)[None, :] - focus[:, None]
    emphasis = np.exp(-0.5 * (offsets / 0.60) ** 2)
    bands *= 0.30 + 0.70 * emphasis

    for note in range(NOTE_COUNT):
        bands[:, note] = smooth(bands[:, note], 25)
    danger = smooth(danger, 25)
    rain = smooth(rain, 25)
    cloud = smooth(cloud, 25)
    cloud_height = smooth(cloud_height, 25)

    return {
        "danger": np.clip(danger, 0.0, 1.0),
        "rain": np.clip(rain, 0.0, 1.0),
        "cloud": np.clip(cloud, 0.0, 1.0),
        "height": np.clip(cloud_height, 0.0, 1.0),
        "bands": np.clip(bands, 0.0, 1.0),
    }


def to_samples(curve, count):
    # Distribute the column measurements smoothly over a 10-second sample timeline.
    if curve.size == 1:
        return np.full(count, float(curve[0]))
    src = np.arange(curve.size, dtype=np.float64)
    dst = np.linspace(0.0, curve.size - 1.0, count)
    return np.interp(dst, src, curve)


def synthesize(features):
    # All layers are phase-continuous sine waves at exact note frequencies.
    count = int(DURATION * SAMPLE_RATE)
    time = np.arange(count, dtype=np.float64) / SAMPLE_RATE
    danger = smooth(to_samples(features["danger"], count), 1323)
    rain = smooth(to_samples(features["rain"], count), 1323)
    cloud = smooth(to_samples(features["cloud"], count), 1323)

    choir = np.zeros(count, dtype=np.float64)
    for note, freq in enumerate(SCALE_HZ):
        envelope = smooth(to_samples(features["bands"][:, note], count), 1764)
        choir += envelope * np.sin(2.0 * np.pi * freq * time)
    choir *= 0.34

    # Blue areas gradually swell the two lowest notes of the scale (ocean / precipitation).
    swell = 0.70 + 0.30 * np.sin(2.0 * np.pi * 0.11 * time)
    ocean = rain * swell * (
        0.65 * np.sin(2.0 * np.pi * SCALE_HZ[0] * time)
        + 0.40 * np.sin(2.0 * np.pi * SCALE_HZ[2] * time)
    )
    ocean *= 0.22

    # As red increases, the C1 root bass and G1 tension fifth swell.
    sub = (0.12 + 0.88 * danger) * (
        np.sin(2.0 * np.pi * SUB_C1 * time)
        + 0.42 * danger * np.sin(2.0 * np.pi * SUB_G1 * time)
    )
    sub *= 0.50

    # As danger increases, the pulse accelerates from 52 BPM to 128 BPM. The beats use Bb1 and C2.
    bpm = 52.0 + danger * 76.0
    beat = np.cumsum(bpm / 60.0 / SAMPLE_RATE) % 1.0
    pulse = (thump(beat, 0.050) * np.sin(2.0 * np.pi * PULSE_BB1 * time)
             + 0.72 * thump((beat - 0.20) % 1.0, 0.038) * np.sin(2.0 * np.pi * PULSE_C2 * time))
    pulse *= danger * 0.55

    wind = wind_layer(count, cloud, time)

    mix = choir + ocean + sub + pulse + wind
    # Fade in and out over the first and last 80 ms to prevent speaker clicks.
    fade = min(int(0.08 * SAMPLE_RATE), count // 2)
    ramp = np.linspace(0.0, 1.0, fade)
    mix[:fade] *= ramp
    mix[-fade:] *= ramp[::-1]

    peak = float(np.max(np.abs(mix))) if mix.size else 0.0
    if peak > 0.0:
        mix *= 0.89 / peak
    return pan_stereo(mix)


def thump(phase, width):
    # Short attack + exponential decay. A deep thump, not a high-pitched beep.
    attack = np.clip(phase / 0.010, 0.0, 1.0)
    body = np.exp(-phase / width)
    return attack * body * (phase < 0.42)


def wind_layer(count, cloud, time):
    # Soft wind: a mid-band of white noise opens up with cloud intensity.
    rng = np.random.default_rng(20261114)
    white = rng.uniform(-1.0, 1.0, count)
    body = smooth(white, 81)
    rumble = smooth(white, 1201)
    band = body - rumble
    power = float(np.sqrt(np.mean(band * band)))
    if power > 0.0:
        band /= power
    howl = 0.75 + 0.25 * np.sin(2.0 * np.pi * 0.13 * time)
    howl *= 0.85 + 0.15 * np.sin(2.0 * np.pi * 0.05 * time)
    return band * howl * (0.04 + 0.96 * cloud) * 0.16


def pan_stereo(mono):
    # As the scan progresses, energy moves from the left ear to the right ear.
    pan = np.linspace(0.0, 1.0, mono.size)
    angle = pan * (np.pi / 2.0)
    left_gain = np.cos(angle)
    right_gain = np.sin(angle)
    bleed = 0.12
    left = mono * (left_gain + bleed * right_gain)
    right = mono * (right_gain + bleed * left_gain)
    return left, right


def write_wav(left, right, output_path=None):
    # 16-bit, 2 channels, 44100 Hz. Samples are packed using struct.
    output = os.fspath(output_path) if output_path else os.path.join(script_dir(), "stormwalkers_final.wav")
    output = os.path.abspath(output)
    os.makedirs(os.path.dirname(output), exist_ok=True)
    interleaved = np.empty(left.size * 2, dtype=np.int16)
    interleaved[0::2] = np.clip(np.rint(left * 32767.0), -32768, 32767)
    interleaved[1::2] = np.clip(np.rint(right * 32767.0), -32768, 32767)

    with wave.open(output, "wb") as handle:
        handle.setnchannels(2)
        handle.setsampwidth(2)
        handle.setframerate(SAMPLE_RATE)
        chunk = 4096
        for start in range(0, interleaved.size, chunk):
            block = interleaved[start:start + chunk]
            handle.writeframes(struct.pack("<" + "h" * int(block.size), *block.tolist()))
    return output


def level_word(value):
    # Reduce numbers to three levels for the listener.
    if value >= 0.66:
        return "high"
    if value >= 0.33:
        return "medium"
    return "low"


def region_name(position):
    # 0 west / left ear, 1 east / right ear.
    if position < 0.33:
        return "western part (left ear)"
    if position > 0.66:
        return "eastern part (right ear)"
    return "center (between both ears)"


def print_summary(features, source, output_path=None):
    # Most intense column: combined peak of danger, cloud, and precipitation.
    danger = features["danger"]
    rain = features["rain"]
    cloud = features["cloud"]
    severity = 0.50 * danger + 0.35 * cloud + 0.15 * rain
    peak = int(np.argmax(severity))
    last = max(1, severity.size - 1)
    position = peak / last

    thirds = [float(np.max(part)) for part in np.array_split(severity, 3)]
    strongest = int(np.argmax(thirds))
    if strongest == 2:
        trend = "The storm intensifies from west to east."
    elif strongest == 0:
        trend = "The strongest part is in the west; the system weakens toward the east."
    else:
        trend = "The core is concentrated in the center of the image."

    strong = severity >= np.percentile(severity, 75)
    strong_danger = float(danger[strong].mean())
    strong_cloud = float(cloud[strong].mean())
    strong_rain = float(rain[strong].mean())

    peak_danger = float(danger[peak])
    peak_cloud = float(cloud[peak])
    peak_rain = float(rain[peak])
    heard = SCALE_NAMES[int(np.argmax(features["bands"][peak]))]
    if peak_cloud < 0.22 and peak_danger >= 0.45:
        pitch_line = "The cloud melody is in the background; the red core produces bass and pulse."
    else:
        pitch_line = f"The pitch heard at this height is {heard}."
    print()
    print("Accessibility summary")
    print(f"Source: {source}")
    print(
        f"The storm's most intense region is in the {region_name(position)} of the image, "
        f"at {position * 100:.0f}% of the left-to-right scan."
    )
    print(
        f"In this region, danger is {level_word(peak_danger)}, "
        f"cloud intensity is {level_word(peak_cloud)}, "
        f"and precipitation is {level_word(peak_rain)}. {pitch_line}"
    )
    print(
        f"Overall: in the strongest sections, danger is {level_word(strong_danger)}, "
        f"cloud cover is {level_word(strong_cloud)}, "
        f"and precipitation is {level_word(strong_rain)}. {trend}"
    )
    print("Stereo: the sound moves from the left ear to the right ear over 10 seconds.")
    output_name = os.path.basename(os.fspath(output_path)) if output_path else "stormwalkers_final.wav"
    print(f"Output: {output_name} (10 sec, 44100 Hz, stereo).")


def main():
    rgb, source = load_rgb()
    features = analyze(rgb)
    left, right = synthesize(features)
    write_wav(left, right)
    print_summary(features, source)


if __name__ == "__main__":
    main()


# --- NASA POWER sonification -------------------------------------------------
# This path maps actual NASA POWER daily values to sound. It is separate from
# the original image-color sonification above, so the original algorithm stays
# available and its visual proxies are not confused with measured weather data.

def _interpolate_observation_field(observations, key, default=None):
    """Return one finite value per observation, filling isolated gaps linearly."""
    values = []
    for row in observations:
        raw = row.get(key)
        try:
            value = float(raw) if raw is not None else np.nan
        except (TypeError, ValueError):
            value = np.nan
        if not np.isfinite(value) or value <= -900:
            value = np.nan
        values.append(value)

    array = np.asarray(values, dtype=np.float64)
    valid = np.flatnonzero(np.isfinite(array))
    if valid.size == 0:
        if default is None:
            raise ValueError(f"NASA POWER returned no valid data for field {key}.")
        return np.full(len(observations), float(default), dtype=np.float64)
    positions = np.arange(array.size, dtype=np.float64)
    return np.interp(positions, valid.astype(np.float64), array[valid])


def sonify_power_series(observations, output_path=None):
    """Sonify NASA POWER daily records into a 10-second stereo WAV file.

    Expected row keys: T2M (°C), PRECTOTCORR (mm/day), WS10M (m/s), RH2M (%).
    Temperature controls pitch, precipitation controls rhythmic activity,
    wind speed controls filtered noise, and humidity controls a soft sustained
    layer. Missing non-temperature parameters are filled conservatively.
    """
    if not observations:
        raise ValueError("No NASA POWER daily data was found to sonify.")

    temperature = _interpolate_observation_field(observations, "T2M")
    precipitation = _interpolate_observation_field(observations, "PRECTOTCORR", 0.0)
    wind_speed = _interpolate_observation_field(observations, "WS10M", 0.0)
    humidity = _interpolate_observation_field(observations, "RH2M", 50.0)

    count = int(DURATION * SAMPLE_RATE)
    time = np.arange(count, dtype=np.float64) / SAMPLE_RATE
    day_axis = np.linspace(0.0, max(0, len(observations) - 1), count)

    temp_curve = np.interp(day_axis, np.arange(temperature.size), temperature)
    rain_curve = np.interp(day_axis, np.arange(precipitation.size), precipitation)
    wind_curve = np.interp(day_axis, np.arange(wind_speed.size), wind_speed)
    humidity_curve = np.interp(day_axis, np.arange(humidity.size), humidity)

    # Absolute temperature-to-pitch mapping: -10 °C is the lowest scale note,
    # 40 °C is the highest. Values outside that range are safely clamped.
    note_position = np.clip((temp_curve + 10.0) / 50.0, 0.0, 1.0) * (NOTE_COUNT - 1)
    frequency = np.interp(note_position, np.arange(NOTE_COUNT), np.asarray(SCALE_HZ))
    phase = np.cumsum((2.0 * np.pi * frequency) / SAMPLE_RATE)
    temperature_voice = 0.23 * np.sin(phase) + 0.055 * np.sin(2.0 * phase)

    # Rain amount controls both beat rate and the strength of short percussion.
    rain_level = np.clip(np.log1p(np.maximum(rain_curve, 0.0)) / np.log1p(30.0), 0.0, 1.0)
    bpm = 48.0 + 64.0 * rain_level
    beat_phase = np.cumsum(bpm / 60.0 / SAMPLE_RATE) % 1.0
    percussion = (
        thump(beat_phase, 0.050) * np.sin(2.0 * np.pi * PULSE_BB1 * time)
        + 0.62 * thump((beat_phase - 0.20) % 1.0, 0.038)
        * np.sin(2.0 * np.pi * PULSE_C2 * time)
    ) * rain_level * 0.38

    # If the input series differs, the micro-pattern of the noise texture also changes.
    # The same NASA series always produces the same WAV (reproducible result).
    seed_text = "|".join(
        str(row.get("date", "")) + ":" + ",".join(
            "NA" if row.get(key) is None else f"{float(row[key]):.6f}"
            for key in ("T2M", "PRECTOTCORR", "WS10M", "RH2M")
        )
        for row in observations
    )
    seed = int.from_bytes(hashlib.sha256(seed_text.encode("utf-8")).digest()[:8], "little")
    rng = np.random.default_rng(seed)
    white = rng.uniform(-1.0, 1.0, count)

    # Wind: a soft, band-limited noise layer whose level follows WS10M.
    wind_noise = white - smooth(white, 801)
    wind_rms = float(np.sqrt(np.mean(wind_noise * wind_noise)))
    if wind_rms > 0.0:
        wind_noise /= wind_rms
    wind_level = np.clip(np.maximum(wind_curve, 0.0) / 20.0, 0.0, 1.0)
    wind_layer_power = wind_noise * (0.015 + 0.10 * wind_level) * wind_level

    # Humidity adds a gentle sustained chord, rather than being presented as
    # precipitation or cloud-cover measurement.
    humidity_level = np.clip(humidity_curve / 100.0, 0.0, 1.0)
    humidity_pad = humidity_level * (
        0.035 * np.sin(2.0 * np.pi * SCALE_HZ[2] * time)
        + 0.025 * np.sin(2.0 * np.pi * SCALE_HZ[5] * time)
    )

    # Small filtered rain texture keeps the rhythm audible without masking pitch.
    rain_texture = (white - smooth(white, 41)) * rain_level * 0.025
    mix = temperature_voice + percussion + wind_layer_power + humidity_pad + rain_texture

    fade = min(int(0.08 * SAMPLE_RATE), count // 2)
    ramp = np.linspace(0.0, 1.0, fade)
    mix[:fade] *= ramp
    mix[-fade:] *= ramp[::-1]

    peak = float(np.max(np.abs(mix))) if mix.size else 0.0
    if peak > 0.0:
        mix *= 0.89 / peak

    output = output_path or os.path.join(script_dir(), "stormwalkers_power.wav")
    left, right = pan_stereo(mix)
    return write_wav(left, right, output)
