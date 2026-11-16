"""
The following code was generated with Artificial-Intelligence.
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import re
import socket
import sqlite3
import sys
import threading
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from nicegui import ui

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "mission_health.db"

# Day 30 of the medical log is Sol 142 of a Mars transit.
SOL_OF_DAY_1 = 113
N_DAYS = 30
LOG_END = date(2026, 11, 14)
SPE_FLUX = 650.0  # µSv/hr forced by the flare drill button

_LOCK = threading.RLock()

LEVEL_NAME = {
    "green": "Nominal",
    "amber": "Elevated watch",
    "red": "Action required",
}
LEVEL_RANK = {"green": 0, "amber": 1, "red": 2}
COLORS = {"green": "#3DDC97", "amber": "#F5A524", "red": "#FF5A6A"}

# Every named telemetry field. Blood pressure is stored as two columns.
TELEMETRY_FIELDS = [
    "radiation_flux",
    "cumulative_dose",
    "cabin_co2",
    "resting_hr",
    "hrv_rmssd",
    "systolic_bp",
    "diastolic_bp",
    "spo2",
    "body_temp",
    "wbc_count",
    "radiation_symptoms",
    "sleep_hours",
    "reaction_time",
    "mood_score",
    "ocular_headache_flag",
    "exercise_mins",
    "back_joint_pain",
]

FIELD_LABELS = {
    "radiation_flux": ("Radiation flux", "µSv/hr", 0),
    "cumulative_dose": ("Cumulative dose", "mSv", 1),
    "cabin_co2": ("Cabin CO2", "mmHg", 1),
    "resting_hr": ("Resting heart rate", "bpm", 0),
    "hrv_rmssd": ("HRV (RMSSD)", "ms", 0),
    "systolic_bp": ("Systolic blood pressure", "mmHg", 0),
    "diastolic_bp": ("Diastolic blood pressure", "mmHg", 0),
    "spo2": ("Oxygen saturation", "%", 0),
    "body_temp": ("Body temperature", "°C", 1),
    "wbc_count": ("White-cell count", "×10³/µL", 1),
    "radiation_symptoms": ("Radiation symptoms", "0–3", 0),
    "sleep_hours": ("Sleep", "h", 1),
    "reaction_time": ("Reaction time", "ms", 0),
    "mood_score": ("Mood", "1–5", 0),
    "ocular_headache_flag": ("Eyes / headache", "0–2", 0),
    "exercise_mins": ("Exercise", "min", 0),
    "back_joint_pain": ("Back / joint pain", "1–5", 0),
}

UPDATABLE = set(TELEMETRY_FIELDS) | {"notes"}

# Allowed range and input step for manual entry: (min, max, step).
INPUT_LIMITS = {
    "radiation_flux": (0, 5000, 1),
    "cumulative_dose": (0, 5000, 0.1),
    "cabin_co2": (0, 20, 0.1),
    "resting_hr": (20, 220, 1),
    "hrv_rmssd": (0, 300, 1),
    "systolic_bp": (50, 250, 1),
    "diastolic_bp": (30, 150, 1),
    "spo2": (50, 100, 1),
    "body_temp": (30, 43, 0.1),
    "wbc_count": (0, 50, 0.1),
    "radiation_symptoms": (0, 3, 1),
    "sleep_hours": (0, 24, 0.1),
    "reaction_time": (50, 2000, 1),
    "mood_score": (1, 5, 1),
    "ocular_headache_flag": (0, 2, 1),
    "exercise_mins": (0, 600, 1),
    "back_joint_pain": (1, 5, 1),
}
INT_FIELDS = {"radiation_symptoms", "mood_score", "ocular_headache_flag", "back_joint_pain"}

SYSTEMS = [
    {
        "id": "cardiovascular",
        "title": "Cardiovascular & Fluid Shift",
        "svg_label": "HEART",
    },
    {
        "id": "immune",
        "title": "Immune & Hematologic",
        "svg_label": "IMMUNE",
    },
    {
        "id": "neurobehavioral",
        "title": "Neurobehavioral & Ocular",
        "svg_label": "NEURO",
    },
    {
        "id": "musculoskeletal",
        "title": "Musculoskeletal",
        "svg_label": "MSK",
    },
]
SYSTEM_IDS = {s["id"] for s in SYSTEMS}

# Self-triage items a non-physician astronaut can actually do on a transit vehicle.
CHECKS = {
    "cardiovascular": [
        ("cardio-shelter", "Storm shelter: move to the most shielded module until flux is under 100 µSv/hr."),
        ("cardio-hydrate", "Hydration protocol: drink 250 ml of water now. Fluid has shifted toward the chest."),
        ("cardio-recheck", "Sit quietly for 10 minutes, then recheck heart rate and blood pressure."),
        ("cardio-oxygen", "If oxygen stays at or below 92%, start cabin oxygen and call the crew medical officer."),
    ],
    "immune": [
        ("immune-shelter", "Storm shelter relocation: put vehicle mass between you and the Sun."),
        ("immune-symptom", "If you have nausea, rash, or sudden fatigue, log it and start the symptom card."),
        ("immune-eva", "No EVA and no sleep in a thinly shielded module while this system is on watch."),
        ("immune-temp", "Recheck temperature in 4 hours. 38.0 °C or higher is action required."),
    ],
    "neurobehavioral": [
        ("neuro-eye", "SANS eye check: read the acuity card with each eye and log blur or headache."),
        ("neuro-sleep", "Protect the next sleep: dim lights and stop caffeine at least 6 hours before the bunk."),
        ("neuro-reaction", "If reaction time is above 350 ms, repeat the 5-flash test after 20 quiet minutes."),
        ("neuro-buddy", "Tell a crewmate if mood is 2 or below, or if vision is blurry."),
    ],
    "musculoskeletal": [
        ("msk-ared", "Resistive workout: at least 45 minutes with legs and back loaded."),
        ("msk-lbnp", "If the vehicle has lower-body negative pressure or compression garments, use them this shift."),
        ("msk-pain", "Log where the back or joint pain is. Pain of 4 or 5: skip heavy loads and tell medical."),
        ("msk-stretch", "Lumbar stretch protocol: 5 slow rounds, no breath-hold."),
    ],
}

MOOD_WORDS = {1: "Very low", 2: "Low", 3: "Steady", 4: "Good", 5: "Strong"}
PAIN_WORDS = {1: "None", 2: "Mild", 3: "Moderate", 4: "High", 5: "Severe"}
SYM_WORDS = {
    0: "None",
    1: "Mild nausea, rash, or fatigue",
    2: "Clear symptoms",
    3: "Strong symptoms",
}
EYE_WORDS = {0: "Clear", 1: "Mild headache", 2: "Blurry vision / SANS warning"}

COG_NOTE = re.compile(r"Cognitive test avg \d+ ms \(\d+ trials: [^)]*\)\.")

MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()

# Browser-side clock. The stimulus timestamp is taken in the page, so the
# saved milliseconds are not the server round-trip.
FLASH_JS = """
const pad = document.getElementById('astro-pad');
if (pad) {
  pad.textContent = 'TAP';
  pad.style.background = '#14F1D9';
  pad.style.color = '#042026';
  pad.style.borderColor = '#9FF7EA';
}
window.__astroStim = performance.now();
window.__astroArmed = true;
"""

RESET_JS = """
const pad = document.getElementById('astro-pad');
if (pad) {
  pad.textContent = 'WAIT';
  pad.style.background = '#1A2436';
  pad.style.color = '#8AA0B8';
  pad.style.borderColor = '#2E3E56';
}
window.__astroArmed = false;
"""

TAP_JS = """
(e) => {
  const pad = e.target && e.target.closest && e.target.closest('#astro-pad');
  if (!pad) return;
  if (!window.__astroArmed) {
    emit({kind: 'early'});
    return;
  }
  const ms = performance.now() - window.__astroStim;
  window.__astroArmed = false;
  pad.textContent = 'WAIT';
  pad.style.background = '#1A2436';
  pad.style.color = '#8AA0B8';
  pad.style.borderColor = '#2E3E56';
  emit({kind: 'hit', ms: ms});
}
"""

PAGE_CSS = """
:root {
  --bg: #070B14;
  --card: #10182A;
  --line: #2A3850;
  --text: #E8EEF7;
  --muted: #93A4BA;
  --cyan: #14B8C7;
}
html, body {
  background: var(--bg) !important;
  color: var(--text);
  font-family: "Segoe UI", Arial, sans-serif;
}
.q-layout, .q-page, .nicegui-content {
  background: var(--bg) !important;
}
.q-btn { text-transform: none !important; letter-spacing: 0; }
.panel {
  background: var(--card) !important;
  color: var(--text) !important;
  border: 1px solid var(--line);
  border-radius: 16px;
  box-shadow: none !important;
}
.level-green { border-color: #1F8A62 !important; }
.level-amber { border-color: #C4841D !important; }
.level-red { border-color: #C43B4A !important; }
.kicker {
  color: var(--cyan);
  letter-spacing: 0.14em;
  font-size: 12px;
  font-weight: 700;
}
h1.app-title {
  font-size: 28px;
  line-height: 1.15;
  margin: 2px 0 0;
  font-weight: 720;
}
.badge {
  display: inline-block;
  border: 1px solid #2E6F86;
  color: #B7F3FF;
  background: #0E2230;
  border-radius: 999px;
  padding: 4px 10px;
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.04em;
}
.muted { color: var(--muted); }
.tile {
  background: #0C1424;
  border: 1px solid var(--line);
  border-radius: 12px;
  padding: 10px 12px;
  min-width: 140px;
  flex: 1;
}
.tile .val { font-size: 26px; font-weight: 720; line-height: 1.1; }
.tile.green { box-shadow: inset 3px 0 0 var(--green, #3DDC97); }
.tile.amber { box-shadow: inset 3px 0 0 #F5A524; }
.tile.red { box-shadow: inset 3px 0 0 #FF5A6A; }
.section-title { font-size: 18px; font-weight: 700; }
.pill {
  border-radius: 999px;
  padding: 2px 8px;
  font-size: 11px;
  font-weight: 800;
  letter-spacing: 0.04em;
  color: #0B1220;
}
.pill.green { background: #3DDC97; }
.pill.amber { background: #F5A524; }
.pill.red { background: #FF5A6A; }
.metric-row {
  border-top: 1px solid #243044;
  padding: 6px 0;
  gap: 8px;
}
.flare-btn {
  background: #FF5A6A !important;
  color: #2A060C !important;
  font-weight: 800 !important;
}
.flare-btn.on {
  background: #FF5A6A !important;
  color: #2A060C !important;
  box-shadow: 0 0 0 3px #FFE4E8, 0 0 16px #FF5A6A;
}
.sys-btn {
  width: 100%;
  font-weight: 700 !important;
  color: #0B1220 !important;
  border-radius: 10px;
}
svg.body-svg .halo { opacity: 0; }
svg.body-svg.storm .halo { opacity: 0.95; stroke: #F5A524; }
svg.body-svg.spe .halo { opacity: 1; stroke: #FF5A6A; }
svg.storm {
  animation: stormpulse 1.4s ease-in-out infinite;
}
svg.spe {
  animation: spepulse 1.05s ease-in-out infinite;
}
@keyframes stormpulse {
  0%, 100% { filter: drop-shadow(0 0 2px rgba(245, 165, 36, 0.25)); }
  50% { filter: drop-shadow(0 0 14px rgba(245, 165, 36, 0.95)); }
}
@keyframes spepulse {
  0%, 100% { filter: drop-shadow(0 0 2px rgba(255, 90, 106, 0.3)); }
  50% { filter: drop-shadow(0 0 18px rgba(255, 90, 106, 1)); }
}
svg [data-region] { cursor: pointer; }
svg.body-svg .zone { fill: transparent; }
svg.body-svg .zone.sel { fill: rgba(155, 231, 255, 0.07); }
svg.body-svg .sys { opacity: 0.8; transition: opacity 0.2s; }
svg.body-svg .sys:hover { opacity: 1; }
svg.body-svg .sys.sel { opacity: 1; filter: drop-shadow(0 0 2.5px rgba(155, 231, 255, 0.95)); }
svg.body-svg .heart {
  transform-box: fill-box;
  transform-origin: center;
  animation: heartbeat 1.1s ease-in-out infinite;
}
@keyframes heartbeat {
  0%, 48%, 100% { transform: scale(1); }
  12% { transform: scale(1.07); }
  24% { transform: scale(1); }
  36% { transform: scale(1.04); }
}
svg.body-svg .lbl text {
  font: 600 9.5px "Segoe UI", Arial, sans-serif;
  letter-spacing: 0.05em;
  fill: #A9B7CC;
  user-select: none;
}
svg.body-svg .lbl polyline { fill: none; stroke: #55688A; stroke-width: 0.7; }
svg.body-svg .lbl:hover text, svg.body-svg .lbl.sel text { fill: #FFFFFF; }
svg.body-svg .lbl.sel polyline { stroke: #9BE7FF; stroke-width: 0.9; }
.q-checkbox__label, .q-toggle__label, .q-slider { color: var(--text); }
.rules { white-space: pre-wrap; line-height: 1.45; }
"""


def sol_number(day: int) -> int:
    return SOL_OF_DAY_1 + int(day) - 1


def pretty_date(iso: str) -> str:
    year, month, day = (int(part) for part in str(iso).split("-"))
    return f"{day} {MONTHS[month - 1]} {year}"


def worst(*levels: str) -> str:
    return max(levels, key=lambda level: LEVEL_RANK[level])


def dose_mSv_from_flux(flux: float) -> float:
    """mSv collected over 24 hours if the flux (µSv/hr) stayed constant."""
    return float(flux) * 24.0 / 1000.0


def xml_text(value: str) -> str:
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def fmt_num(value, digits: int) -> str:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return "—"
    if digits <= 0:
        return f"{float(value):.0f}"
    return f"{float(value):.{digits}f}"


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

@contextmanager
def db():
    """Short-lived SQLite connection. The lock is re-entrant so callers may nest."""
    with _LOCK:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    found = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    if not found:
        return set()
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def init_db() -> None:
    """Create tables and, on an empty log, write 30 days of synthetic telemetry."""
    with db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS mission_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS checklist (
                day INTEGER NOT NULL,
                item_id TEXT NOT NULL,
                done INTEGER NOT NULL,
                PRIMARY KEY (day, item_id)
            )
            """
        )
        needed = {"day", "date", "notes", *TELEMETRY_FIELDS}
        have = _columns(conn, "daily_telemetry")
        if have and not needed <= have:
            conn.execute("DROP TABLE daily_telemetry")
            have = set()
        if not have:
            conn.execute(
                """
                CREATE TABLE daily_telemetry (
                    day INTEGER PRIMARY KEY,
                    date TEXT NOT NULL,
                    notes TEXT NOT NULL DEFAULT '',
                    radiation_flux REAL NOT NULL,
                    cumulative_dose REAL NOT NULL,
                    cabin_co2 REAL NOT NULL,
                    resting_hr REAL NOT NULL,
                    hrv_rmssd REAL NOT NULL,
                    systolic_bp REAL NOT NULL,
                    diastolic_bp REAL NOT NULL,
                    spo2 REAL NOT NULL,
                    body_temp REAL NOT NULL,
                    wbc_count REAL NOT NULL,
                    radiation_symptoms INTEGER NOT NULL,
                    sleep_hours REAL NOT NULL,
                    reaction_time REAL NOT NULL,
                    mood_score INTEGER NOT NULL,
                    ocular_headache_flag INTEGER NOT NULL,
                    exercise_mins REAL NOT NULL,
                    back_joint_pain INTEGER NOT NULL
                )
                """
            )
        count = conn.execute("SELECT COUNT(*) AS n FROM daily_telemetry").fetchone()["n"]
        if count == 0:
            rows = synthesize()
            cols = ["day", "date", "notes", *TELEMETRY_FIELDS]
            sql = (
                f"INSERT INTO daily_telemetry ({', '.join(cols)}) "
                f"VALUES ({', '.join('?' for _ in cols)})"
            )
            conn.executemany(sql, [tuple(row[col] for col in cols) for row in rows])
            conn.execute(
                "INSERT OR REPLACE INTO mission_state(key, value) VALUES('spe_active', '0')"
            )


def state_get(key: str, default: str | None = None) -> str | None:
    with db() as conn:
        row = conn.execute("SELECT value FROM mission_state WHERE key=?", (key,)).fetchone()
    return default if row is None else row["value"]


def load_df() -> pd.DataFrame:
    with db() as conn:
        frame = pd.read_sql_query(
            "SELECT * FROM daily_telemetry ORDER BY day",
            conn,
        )
    return frame


def latest_row() -> sqlite3.Row:
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM daily_telemetry ORDER BY day DESC LIMIT 1"
        ).fetchone()
    if row is None:
        raise RuntimeError("mission log is empty")
    return row


def update_today(**fields) -> None:
    unknown = set(fields) - UPDATABLE
    if unknown:
        raise ValueError(f"refusing unknown columns: {sorted(unknown)}")
    if not fields:
        return
    assignments = ", ".join(f"{name}=?" for name in fields)
    with db() as conn:
        day = conn.execute("SELECT MAX(day) AS d FROM daily_telemetry").fetchone()["d"]
        conn.execute(
            f"UPDATE daily_telemetry SET {assignments} WHERE day=?",
            [*fields.values(), day],
        )


def start_next_day() -> int:
    """Close the current day and open the next one. Returns the new day number.

    The new row starts as a copy of the day being closed, so the crew member
    edits readings instead of typing all of them. The note starts empty and the
    cumulative dose moves on by 24 hours at the carried flux. An active flare
    drill ends: the drill values stay on the closed day and the new day starts
    from the background flux.
    """
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM daily_telemetry ORDER BY day DESC LIMIT 1"
        ).fetchone()
        if row is None:
            raise RuntimeError("mission log is empty")
        values = {key: row[key] for key in TELEMETRY_FIELDS}
        flux = float(row["radiation_flux"])
        flag = conn.execute(
            "SELECT value FROM mission_state WHERE key='spe_active'"
        ).fetchone()
        if flag is not None and flag["value"] == "1":
            saved_flux = conn.execute(
                "SELECT value FROM mission_state WHERE key='saved_flux'"
            ).fetchone()
            flux = float(saved_flux["value"]) if saved_flux else 19.0
            conn.execute(
                "INSERT OR REPLACE INTO mission_state(key, value) VALUES('spe_active', '0')"
            )
        values["radiation_flux"] = flux
        values["cumulative_dose"] = round(
            float(row["cumulative_dose"]) + dose_mSv_from_flux(flux), 2
        )
        day = int(row["day"]) + 1
        when = date.fromisoformat(str(row["date"])) + timedelta(days=1)
        cols = ["day", "date", "notes", *TELEMETRY_FIELDS]
        conn.execute(
            f"INSERT INTO daily_telemetry ({', '.join(cols)}) "
            f"VALUES ({', '.join('?' for _ in cols)})",
            [day, when.isoformat(), "", *(values[key] for key in TELEMETRY_FIELDS)],
        )
    return day


def checklist_map(day: int) -> dict[str, bool]:
    with db() as conn:
        rows = conn.execute("SELECT item_id, done FROM checklist WHERE day=?", (day,)).fetchall()
    return {row["item_id"]: bool(row["done"]) for row in rows}


def set_check(day: int, item_id: str, done: bool) -> None:
    with db() as conn:
        conn.execute(
            """
            INSERT INTO checklist(day, item_id, done) VALUES(?, ?, ?)
            ON CONFLICT(day, item_id) DO UPDATE SET done=excluded.done
            """,
            (day, item_id, 1 if done else 0),
        )


def set_spe(active: bool) -> None:
    """Spike or restore today's flux and the 24-hour dose that flux implies.

    The original background values are kept in mission_state so the drill can
    be turned off without losing the seeded cruise day.
    """
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM daily_telemetry ORDER BY day DESC LIMIT 1"
        ).fetchone()
        prev = conn.execute(
            "SELECT cumulative_dose FROM daily_telemetry WHERE day=?",
            (row["day"] - 1,),
        ).fetchone()
        flag = conn.execute(
            "SELECT value FROM mission_state WHERE key='spe_active'"
        ).fetchone()
        is_on = flag is not None and flag["value"] == "1"
        if active == is_on:
            return
        if active:
            conn.execute(
                "INSERT OR REPLACE INTO mission_state(key, value) VALUES('saved_flux', ?)",
                (f"{float(row['radiation_flux']):.6f}",),
            )
            conn.execute(
                "INSERT OR REPLACE INTO mission_state(key, value) VALUES('saved_dose', ?)",
                (f"{float(row['cumulative_dose']):.6f}",),
            )
            new_dose = float(prev["cumulative_dose"]) + dose_mSv_from_flux(SPE_FLUX)
            conn.execute(
                "UPDATE daily_telemetry SET radiation_flux=?, cumulative_dose=? WHERE day=?",
                (SPE_FLUX, round(new_dose, 2), row["day"]),
            )
            conn.execute(
                "INSERT OR REPLACE INTO mission_state(key, value) VALUES('spe_active', '1')"
            )
        else:
            saved_flux = conn.execute(
                "SELECT value FROM mission_state WHERE key='saved_flux'"
            ).fetchone()
            saved_dose = conn.execute(
                "SELECT value FROM mission_state WHERE key='saved_dose'"
            ).fetchone()
            flux = float(saved_flux["value"]) if saved_flux else 19.0
            dose = float(saved_dose["value"]) if saved_dose else float(row["cumulative_dose"])
            conn.execute(
                "UPDATE daily_telemetry SET radiation_flux=?, cumulative_dose=? WHERE day=?",
                (flux, dose, row["day"]),
            )
            conn.execute(
                "INSERT OR REPLACE INTO mission_state(key, value) VALUES('spe_active', '0')"
            )


def clear_spe_flag() -> None:
    """Manual values replace the flare drill, so the drill must not restore old ones later."""
    with db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO mission_state(key, value) VALUES('spe_active', '0')"
        )


def write_reaction(average_ms: float, samples: list[float]) -> None:
    rounded = [int(round(sample)) for sample in samples]
    sentence = (
        f"Cognitive test avg {int(round(average_ms))} ms "
        f"({len(rounded)} trials: {', '.join(str(v) for v in rounded)} ms)."
    )
    with db() as conn:
        row = conn.execute(
            "SELECT day, notes FROM daily_telemetry ORDER BY day DESC LIMIT 1"
        ).fetchone()
        note = COG_NOTE.sub("", row["notes"] or "").strip()
        note = f"{note} {sentence}".strip()
        conn.execute(
            "UPDATE daily_telemetry SET reaction_time=?, notes=? WHERE day=?",
            (int(round(average_ms)), note, row["day"]),
        )


# ---------------------------------------------------------------------------
# Synthetic 30-day cruise, with a solar particle event on day 24
# ---------------------------------------------------------------------------

def _clip(rng: np.random.Generator, center: float, spread: float, lo: float, hi: float, digits: int):
    return round(float(np.clip(rng.normal(center, spread), lo, hi)), digits)


def synthesize() -> list[dict]:
    """Build a deterministic log.

    Days 1–22 are a quiet cruise. Days 23–25 are a low-exercise run.
    Day 24 is the particle event (flux 680 µSv/hr, dose jump above 10 mSv).
    White cells bottom out on day 26, after the flux has already fallen.
    Day 30 is nominal so the live flare button is what turns the map red.
    """
    rng = np.random.default_rng(142)
    # Fully specified tail so "today" and the storm do not depend on jitter.
    tail = {
        23: dict(radiation_flux=21, cabin_co2=2.4, resting_hr=60, hrv_rmssd=61,
                 systolic_bp=116, diastolic_bp=74, spo2=98, body_temp=36.6,
                 wbc_count=6.4, radiation_symptoms=0, sleep_hours=7.0,
                 reaction_time=255, mood_score=4, ocular_headache_flag=0,
                 exercise_mins=24, back_joint_pain=2, notes="Light exercise day."),
        24: dict(radiation_flux=680, cabin_co2=4.3, resting_hr=74, hrv_rmssd=18,
                 systolic_bp=138, diastolic_bp=88, spo2=94, body_temp=37.6,
                 wbc_count=5.9, radiation_symptoms=2, sleep_hours=4.4,
                 reaction_time=372, mood_score=2, ocular_headache_flag=1,
                 exercise_mins=16, back_joint_pain=3,
                 notes="Solar particle event. Crew in the storm shelter for 9 hours. Nausea reported."),
        25: dict(radiation_flux=140, cabin_co2=3.1, resting_hr=68, hrv_rmssd=29,
                 systolic_bp=128, diastolic_bp=82, spo2=96, body_temp=37.2,
                 wbc_count=4.8, radiation_symptoms=1, sleep_hours=5.5,
                 reaction_time=330, mood_score=3, ocular_headache_flag=1,
                 exercise_mins=22, back_joint_pain=3,
                 notes="Flux still elevated. Fatigue and a mild headache."),
        26: dict(radiation_flux=32, cabin_co2=2.5, resting_hr=63, hrv_rmssd=38,
                 systolic_bp=122, diastolic_bp=78, spo2=97, body_temp=36.9,
                 wbc_count=3.4, radiation_symptoms=1, sleep_hours=6.6,
                 reaction_time=280, mood_score=3, ocular_headache_flag=0,
                 exercise_mins=50, back_joint_pain=2,
                 notes="White-cell nadir the day after the particle event."),
        27: dict(radiation_flux=22, cabin_co2=2.3, resting_hr=61, hrv_rmssd=50,
                 systolic_bp=118, diastolic_bp=74, spo2=98, body_temp=36.7,
                 wbc_count=3.8, radiation_symptoms=0, sleep_hours=7.1,
                 reaction_time=260, mood_score=4, ocular_headache_flag=0,
                 exercise_mins=65, back_joint_pain=2, notes="Immune count still low. Flux quiet."),
        28: dict(radiation_flux=18, cabin_co2=2.2, resting_hr=60, hrv_rmssd=56,
                 systolic_bp=117, diastolic_bp=73, spo2=98, body_temp=36.6,
                 wbc_count=5.2, radiation_symptoms=0, sleep_hours=7.4,
                 reaction_time=250, mood_score=4, ocular_headache_flag=0,
                 exercise_mins=70, back_joint_pain=1, notes=""),
        29: dict(radiation_flux=20, cabin_co2=2.4, resting_hr=60, hrv_rmssd=58,
                 systolic_bp=118, diastolic_bp=74, spo2=98, body_temp=36.7,
                 wbc_count=6.0, radiation_symptoms=0, sleep_hours=7.2,
                 reaction_time=248, mood_score=5, ocular_headache_flag=0,
                 exercise_mins=68, back_joint_pain=2, notes=""),
        30: dict(radiation_flux=19, cabin_co2=2.3, resting_hr=61, hrv_rmssd=58,
                 systolic_bp=118, diastolic_bp=74, spo2=98, body_temp=36.7,
                 wbc_count=6.4, radiation_symptoms=0, sleep_hours=7.3,
                 reaction_time=252, mood_score=4, ocular_headache_flag=0,
                 exercise_mins=72, back_joint_pain=2,
                 notes="Nominal cruise day."),
    }

    cumulative = 386.0  # mSv already accumulated before this 30-day window
    rows: list[dict] = []
    for day in range(1, N_DAYS + 1):
        if day in tail:
            spec = tail[day]
            flux = float(spec["radiation_flux"])
            row = {
                "cabin_co2": float(spec["cabin_co2"]),
                "resting_hr": float(spec["resting_hr"]),
                "hrv_rmssd": float(spec["hrv_rmssd"]),
                "systolic_bp": int(spec["systolic_bp"]),
                "diastolic_bp": int(spec["diastolic_bp"]),
                "spo2": int(spec["spo2"]),
                "body_temp": float(spec["body_temp"]),
                "wbc_count": float(spec["wbc_count"]),
                "radiation_symptoms": int(spec["radiation_symptoms"]),
                "sleep_hours": float(spec["sleep_hours"]),
                "reaction_time": int(spec["reaction_time"]),
                "mood_score": int(spec["mood_score"]),
                "ocular_headache_flag": int(spec["ocular_headache_flag"]),
                "exercise_mins": int(spec["exercise_mins"]),
                "back_joint_pain": int(spec["back_joint_pain"]),
                "notes": spec["notes"],
            }
        else:
            flux = _clip(rng, 20, 1.6, 16, 25, 1)
            row = {
                "cabin_co2": _clip(rng, 2.3, 0.18, 1.6, 2.9, 2),
                "resting_hr": _clip(rng, 59, 1.8, 52, 66, 0),
                "hrv_rmssd": _clip(rng, 62, 4, 48, 78, 0),
                "systolic_bp": int(_clip(rng, 118, 2.5, 110, 125, 0)),
                "diastolic_bp": int(_clip(rng, 74, 1.8, 70, 80, 0)),
                "spo2": int(_clip(rng, 98, 0.4, 97, 100, 0)),
                "body_temp": _clip(rng, 36.7, 0.08, 36.4, 37.1, 1),
                "wbc_count": _clip(rng, 6.5, 0.35, 5.1, 8.0, 1),
                "radiation_symptoms": 0,
                "sleep_hours": _clip(rng, 7.2, 0.25, 6.6, 8.2, 1),
                "reaction_time": int(_clip(rng, 250, 10, 225, 275, 0)),
                "mood_score": int(rng.integers(4, 6)),
                "ocular_headache_flag": 0,
                "exercise_mins": int(_clip(rng, 68, 7, 48, 90, 0)),
                "back_joint_pain": int(rng.integers(1, 3)),
                "notes": "",
            }
        cumulative = round(cumulative + dose_mSv_from_flux(flux), 2)
        when = LOG_END - timedelta(days=N_DAYS - day)
        row.update(
            {
                "day": day,
                "date": when.isoformat(),
                "radiation_flux": flux,
                "cumulative_dose": cumulative,
            }
        )
        rows.append(row)
    return rows


# ---------------------------------------------------------------------------
# Risk engine
# ---------------------------------------------------------------------------

def prepare(frame: pd.DataFrame) -> pd.DataFrame:
    """Add personal baselines.

    pandas rolling(7).mean() is the 7-day mean ending on each row (today is
    included). shift(1) moves that mean back one day so today's reading is
    judged against the previous seven days only — a bad day cannot water
    down the baseline it is compared with.
    """
    out = frame.sort_values("day").reset_index(drop=True).copy()
    for col in TELEMETRY_FIELDS:
        out[f"base_{col}"] = out[col].rolling(7).mean().shift(1)
    return out


def _base(row: pd.Series, col: str):
    value = row.get(f"base_{col}")
    if value is None or pd.isna(value):
        return None
    return float(value)


def _flux_level(flux: float) -> str:
    if flux > 500:
        return "red"
    if flux > 100:
        return "amber"
    return "green"


def _dose_level(cumulative: float, jump: float) -> str:
    # Spec: red when the 24-hour rise exceeds 10 mSv, or the running total
    # leaves the 0–450 mSv cruise band.
    if jump > 10 or cumulative > 450:
        return "red"
    return "green"


def _co2_level(co2: float) -> str:
    if co2 > 4.0:
        return "amber"
    return "green"


def exercise_streak(minutes: pd.Series) -> int:
    streak = 0
    for value in reversed(list(minutes)):
        if float(value) < 30:
            streak += 1
        else:
            break
    return streak


def _metric(key: str, value, baseline, level: str, note: str = "") -> dict:
    label, unit, digits = FIELD_LABELS[key]
    return {
        "key": key,
        "name": label,
        "value": fmt_num(value, digits),
        "baseline": "—" if baseline is None else fmt_num(baseline, digits),
        "unit": unit,
        "level": level,
        "note": note,
    }


def radiation_floor(level: str, reasons: list[str], flux: float, dose_jump: float, system_name: str) -> str:
    """Couple the radiation environment to immune and cardiovascular status.

    Above 100 µSv/hr those two systems are at least Elevated Watch. Above
    500 µSv/hr they are Action Required, even if the white-cell count and
    heart rate have not moved yet. A 24-hour dose jump above 10 mSv raises
    the same two systems to at least Elevated Watch.
    """
    if flux > 500:
        reasons.append(
            f"Preventative countermeasure: radiation flux is {flux:.0f} µSv/hr "
            f"(above 500). {system_name} stays at Action Required even if the "
            "lab numbers still look normal."
        )
        return worst(level, "red")
    if flux > 100:
        reasons.append(
            f"Preventative countermeasure: radiation flux is {flux:.0f} µSv/hr "
            f"(above 100). {system_name} is raised to at least Elevated Watch "
            "before blood counts change."
        )
        return worst(level, "amber")
    if dose_jump > 10:
        reasons.append(
            f"Preventative countermeasure: dose rose {dose_jump:.1f} mSv in 24 hours "
            f"(threshold 10). {system_name} is raised to at least Elevated Watch."
        )
        return worst(level, "amber")
    return level


def assess(frame: pd.DataFrame) -> dict:
    """Score the last day of `frame` using the history inside it."""
    data = prepare(frame)
    row = data.iloc[-1]
    prev = data.iloc[-2] if len(data) > 1 else row
    flux = float(row["radiation_flux"])
    dose = float(row["cumulative_dose"])
    jump = float(row["cumulative_dose"] - prev["cumulative_dose"]) if len(data) > 1 else dose
    co2 = float(row["cabin_co2"])

    # --- cardiovascular ---
    hr = float(row["resting_hr"])
    hr_base = _base(row, "resting_hr")
    hr_level = "green"
    hr_note = "7-day personal baseline still forming"
    if hr_base:
        pct = (hr / hr_base - 1.0) * 100.0
        hr_note = f"{pct:+.1f}% vs 7-day personal baseline ({hr_base:.0f} bpm)"
        # Amber above a 12% personal rise. A 20% rise, or a rate over 100,
        # is Action Required. A fixed Earth cutoff is not the trigger.
        if pct > 20 or hr > 100:
            hr_level = "red"
        elif pct > 12:
            hr_level = "amber"
    elif hr > 100:
        hr_level = "red"
    elif hr > 90:
        hr_level = "amber"

    hrv = float(row["hrv_rmssd"])
    hrv_base = _base(row, "hrv_rmssd")
    hrv_level = "green"
    hrv_note = ""
    if hrv < 20:
        hrv_level = "red"
    elif hrv < 30:
        hrv_level = "amber"
    if hrv_base:
        hrv_note = f"personal baseline {hrv_base:.0f} ms"
        if hrv < hrv_base * 0.5:
            hrv_level = worst(hrv_level, "red")
        elif hrv < hrv_base * 0.7:
            hrv_level = worst(hrv_level, "amber")

    sys_bp = float(row["systolic_bp"])
    dia_bp = float(row["diastolic_bp"])
    if sys_bp >= 150 or dia_bp >= 100 or sys_bp <= 90 or dia_bp <= 55:
        bp_level = "red"
    elif sys_bp > 135 or dia_bp > 85 or sys_bp < 95 or dia_bp < 60:
        bp_level = "amber"
    else:
        bp_level = "green"

    spo2 = float(row["spo2"])
    if spo2 <= 92:
        o2_level = "red"
    elif spo2 < 96:
        o2_level = "amber"
    else:
        o2_level = "green"

    cardio_reasons = []
    if hr_level != "green":
        cardio_reasons.append(
            f"Resting heart rate is {hr:.0f} bpm ({hr_note}). "
            "The alert is a rise of more than 12% above your own 7-day average."
        )
    if hrv_level != "green":
        cardio_reasons.append(
            f"Heart-rate variability is {hrv:.0f} ms. Under 30 ms is a watch; under 20 ms is action."
        )
    if bp_level != "green":
        cardio_reasons.append(
            f"Blood pressure is {sys_bp:.0f}/{dia_bp:.0f} mmHg. "
            "The watch band is outside 95–135 over 60–85."
        )
    if o2_level != "green":
        cardio_reasons.append(
            f"Oxygen saturation is {spo2:.0f}%. 93–95 is a watch; 92 or below is action."
        )
    cardio_level = worst(hr_level, hrv_level, bp_level, o2_level)
    cardio_level = radiation_floor(cardio_level, cardio_reasons, flux, jump, "Cardiovascular")

    # --- immune ---
    temp = float(row["body_temp"])
    if temp >= 38.0:
        temp_level = "red"
    elif temp >= 37.5:
        temp_level = "amber"
    else:
        temp_level = "green"

    wbc = float(row["wbc_count"])
    if wbc < 3.0:
        wbc_level = "red"
    elif wbc < 4.0:
        wbc_level = "amber"
    else:
        wbc_level = "green"

    symptoms = int(row["radiation_symptoms"])
    if symptoms >= 2:
        sym_level = "red"
    elif symptoms >= 1:
        sym_level = "amber"
    else:
        sym_level = "green"

    immune_reasons = []
    if temp_level != "green":
        immune_reasons.append(f"Temperature is {temp:.1f} °C. 37.5 is a watch; 38.0 is action.")
    if wbc_level != "green":
        immune_reasons.append(
            f"White-cell count is {wbc:.1f} ×10³/µL. Under 4.0 is a watch; under 3.0 is action."
        )
    if sym_level != "green":
        immune_reasons.append(
            f"Radiation symptom score is {symptoms} of 3 (nausea, rash, or sudden fatigue)."
        )
    immune_level = worst(temp_level, wbc_level, sym_level)
    immune_level = radiation_floor(immune_level, immune_reasons, flux, jump, "Immune & hematologic")

    # --- neuro / ocular ---
    sleep = float(row["sleep_hours"])
    if sleep < 5.0:
        sleep_level = "red"
    elif sleep < 6.5:
        sleep_level = "amber"
    else:
        sleep_level = "green"

    reaction = float(row["reaction_time"])
    if reaction > 350:
        rt_level = "red"
    elif reaction >= 290:
        rt_level = "amber"
    else:
        rt_level = "green"

    mood = int(row["mood_score"])
    if mood <= 1:
        mood_level = "red"
    elif mood <= 2:
        mood_level = "amber"
    else:
        mood_level = "green"

    eyes = int(row["ocular_headache_flag"])
    if eyes >= 2:
        eye_level = "red"
    elif eyes >= 1:
        eye_level = "amber"
    else:
        eye_level = "green"

    neuro_reasons = []
    if sleep_level != "green":
        neuro_reasons.append(f"Sleep was {sleep:.1f} h. Under 6.5 h is a watch; under 5 h is action.")
    if rt_level != "green":
        neuro_reasons.append(
            f"Reaction time is {reaction:.0f} ms. 290–340 is a watch; above 350 is action."
        )
    if mood_level != "green":
        neuro_reasons.append(f"Mood is {mood} of 5. 2 or below is a watch; 1 is action.")
    if eye_level != "green":
        neuro_reasons.append(
            "Eye flag is "
            + ("blurry vision / SANS warning." if eyes >= 2 else "a mild headache.")
        )
    neuro_level = worst(sleep_level, rt_level, mood_level, eye_level)

    # --- musculoskeletal ---
    streak = exercise_streak(data["exercise_mins"])
    exercise = float(row["exercise_mins"])
    ex_level = "amber" if streak >= 3 else "green"
    pain = int(row["back_joint_pain"])
    if pain >= 5:
        pain_level = "red"
    elif pain >= 4:
        pain_level = "amber"
    else:
        pain_level = "green"
    msk_reasons = []
    if ex_level != "green":
        msk_reasons.append(
            f"Exercise has been under 30 minutes for {streak} days in a row, ending today."
        )
    if pain_level != "green":
        msk_reasons.append(f"Back / joint pain is {pain} of 5. 4 is a watch; 5 is action.")
    msk_level = worst(ex_level, pain_level)

    def pack(level: str, reasons: list[str], metrics: list[dict]) -> dict:
        if not reasons and level == "green":
            reasons = ["All watched numbers for this system are inside mission limits."]
        return {"level": level, "reasons": reasons, "metrics": metrics}

    systems = {
        "cardiovascular": pack(cardio_level, cardio_reasons, [
            _metric("resting_hr", hr, hr_base, hr_level, hr_note),
            _metric("hrv_rmssd", hrv, hrv_base, hrv_level, hrv_note),
            _metric("systolic_bp", sys_bp, _base(row, "systolic_bp"), bp_level),
            _metric("diastolic_bp", dia_bp, _base(row, "diastolic_bp"), bp_level),
            _metric("spo2", spo2, _base(row, "spo2"), o2_level),
        ]),
        "immune": pack(immune_level, immune_reasons, [
            _metric("body_temp", temp, _base(row, "body_temp"), temp_level),
            _metric("wbc_count", wbc, _base(row, "wbc_count"), wbc_level),
            _metric("radiation_symptoms", symptoms, _base(row, "radiation_symptoms"), sym_level),
        ]),
        "neurobehavioral": pack(neuro_level, neuro_reasons, [
            _metric("sleep_hours", sleep, _base(row, "sleep_hours"), sleep_level),
            _metric("reaction_time", reaction, _base(row, "reaction_time"), rt_level),
            _metric("mood_score", mood, _base(row, "mood_score"), mood_level),
            _metric("ocular_headache_flag", eyes, _base(row, "ocular_headache_flag"), eye_level),
        ]),
        "musculoskeletal": pack(msk_level, msk_reasons, [
            _metric("exercise_mins", exercise, _base(row, "exercise_mins"), ex_level,
                    f"current low-load streak: {streak} day(s)"),
            _metric("back_joint_pain", pain, _base(row, "back_joint_pain"), pain_level),
        ]),
    }

    field_levels = {key: "green" for key in TELEMETRY_FIELDS}
    field_levels["radiation_flux"] = _flux_level(flux)
    field_levels["cumulative_dose"] = _dose_level(dose, jump)
    field_levels["cabin_co2"] = _co2_level(co2)
    for system in systems.values():
        for metric in system["metrics"]:
            field_levels[metric["key"]] = worst(field_levels[metric["key"]], metric["level"])

    return {
        "day": int(row["day"]),
        "sol": sol_number(int(row["day"])),
        "date": str(row["date"]),
        "notes": str(row["notes"] or ""),
        "flux": flux,
        "dose": dose,
        "dose_jump": jump,
        "co2": co2,
        "flux_level": field_levels["radiation_flux"],
        "dose_level": field_levels["cumulative_dose"],
        "co2_level": field_levels["cabin_co2"],
        "systems": systems,
        "field_levels": field_levels,
        "prepared": data,
    }


def explain(system_id: str, report: dict, data: pd.DataFrame) -> str:
    """Plain-language briefing for a crew member who is not a physician."""
    sysrep = report["systems"][system_id]
    by_key = {m["key"]: m for m in sysrep["metrics"]}
    if system_id == "cardiovascular":
        return (
            "In microgravity, body fluid shifts toward the chest and head. Heart rate can "
            "rise without the usual 'I just stood up' signal you have on Earth. This watch "
            f"compares today with your own 7-day average. {by_key['resting_hr']['note']}. "
            f"Variability is {by_key['hrv_rmssd']['value']} ms "
            f"(baseline {by_key['hrv_rmssd']['baseline']} ms). "
            f"Blood pressure is {by_key['systolic_bp']['value']}/{by_key['diastolic_bp']['value']} mmHg "
            f"and oxygen is {by_key['spo2']['value']}%. "
            "A fixed Earth heart-rate table is not what raises this card."
        )
    if system_id == "immune":
        nadir_at = data["wbc_count"].idxmin()
        nadir = data.loc[nadir_at]
        return (
            "Radiation hits dividing blood cells first. A lab draw can still look normal "
            "on the day of a storm, then fall afterward. If flux is above 100 µSv/hr this "
            "system is raised to Elevated Watch immediately, and to Action Required above "
            f"500 µSv/hr, even when the white-cell count has not moved. Today's count is "
            f"{by_key['wbc_count']['value']} ×10³/µL (7-day baseline {by_key['wbc_count']['baseline']}). "
            f"Temperature is {by_key['body_temp']['value']} °C. Symptom score is "
            f"{by_key['radiation_symptoms']['value']} of 3. "
            f"Lowest white-cell count in this log is {float(nadir['wbc_count']):.1f} on "
            f"Sol {sol_number(int(nadir['day']))}."
        )
    if system_id == "neurobehavioral":
        return (
            "This block is sleep, mood, reaction speed, and the eyes. Spaceflight Associated "
            "Neuro-ocular Syndrome (SANS) is swelling and blur from fluid that has moved into "
            "the head. A 2 on the eye flag means blurry vision: do the eye card and tell the "
            f"crew medical officer. Last sleep was {by_key['sleep_hours']['value']} h "
            f"(baseline {by_key['sleep_hours']['baseline']} h). Reaction time is "
            f"{by_key['reaction_time']['value']} ms. Mood is {by_key['mood_score']['value']} of 5. "
            f"Eye flag is {by_key['ocular_headache_flag']['value']} "
            f"({EYE_WORDS[int(float(by_key['ocular_headache_flag']['value']))]}) ."
        )
    streak_note = by_key["exercise_mins"]["note"]
    return (
        "Hips, spine, and leg bones lose mineral if they are not loaded. Fewer than 30 minutes "
        "of exercise for three days in a row raises this watch even if you feel fine. "
        f"Today's exercise is {by_key['exercise_mins']['value']} min "
        f"(baseline {by_key['exercise_mins']['baseline']} min; {streak_note}). "
        f"Back / joint pain is {by_key['back_joint_pain']['value']} of 5. "
        "The countermeasure on this vehicle is resistive exercise, not a pill."
    )


# ---------------------------------------------------------------------------
# Body map and charts
# ---------------------------------------------------------------------------

BODY_CX = 200          # the figure is symmetric about this x
BONE_CORE = "#0B1524"  # hollow interior of bones, dark detail lines
ORGAN_GREY = "#7C8CA6"  # context organs that carry no status


def _n(value: float) -> str:
    return f"{value:.1f}".rstrip("0").rstrip(".")


def _mx(points: list[tuple]) -> list[tuple]:
    """Mirror points across the body midline."""
    return [(2 * BODY_CX - x, y) for x, y in points]


def _smooth(points: list[tuple], closed: bool = False) -> str:
    """Catmull-Rom spline through the points, written as an SVG cubic path."""
    n = len(points)

    def at(i: int) -> tuple:
        return points[i % n] if closed else points[max(0, min(n - 1, i))]

    out = [f"M{_n(points[0][0])},{_n(points[0][1])}"]
    for i in range(n if closed else n - 1):
        p0, p1, p2, p3 = at(i - 1), at(i), at(i + 1), at(i + 2)
        c1 = (p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6)
        c2 = (p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6)
        out.append(
            f"C{_n(c1[0])},{_n(c1[1])} {_n(c2[0])},{_n(c2[1])} {_n(p2[0])},{_n(p2[1])}"
        )
    if closed:
        out.append("Z")
    return " ".join(out)


def _both(points: list[tuple], closed: bool = False) -> list[str]:
    """Path data for a structure and its mirror image."""
    return [_smooth(points, closed), _smooth(_mx(points), closed)]


def _paths(ds: list[str], attrs: str) -> str:
    return "".join(f'<path d="{d}" {attrs}/>' for d in ds)


def _body_outline() -> str:
    half = [
        (200, 22), (188, 24), (178, 32), (174, 46), (174, 60), (177, 74), (183, 86),
        (187, 95), (186, 106), (176, 113), (160, 117), (146, 121), (136, 130),
        (130, 146), (127, 170), (124, 196), (119, 224), (111, 260), (103, 292),
        (98, 312), (97, 330), (101, 338), (107, 336), (112, 322), (117, 300),
        (127, 264), (137, 230), (145, 198), (150, 170), (153, 152), (156, 170),
        (159, 205), (163, 238), (159, 264), (155, 290), (154, 330), (158, 380),
        (164, 425), (162, 452), (161, 480), (167, 530), (172, 562), (165, 578),
        (160, 590), (170, 597), (186, 596), (192, 588), (190, 572), (189, 540),
        (191, 490), (190, 452), (192, 422), (196, 370), (198, 332), (200, 320),
    ]
    return _smooth(half + _mx(half)[-2:0:-1], closed=True)


def _skeleton() -> str:
    """Bones drawn as wide strokes with a dark core, so joints merge cleanly."""
    # (x1, y1, x2, y2, width); zero-length entries are joint knobs.
    left = [
        (196, 123, 152, 119, 3.4),                               # clavicle
        (143.5, 129.5, 143.5, 129.5, 9), (143, 131, 129, 221, 6),  # humerus
        (129, 222, 129, 222, 8),
        (125.5, 228, 106, 294, 3.8), (132, 227, 113, 294, 3.4),  # radius, ulna
        (108.5, 300, 108.5, 300, 8),                             # carpals
        (104, 302, 99.5, 316, 2.2), (106, 304, 99.5, 329, 2.2),  # hand rays
        (107.5, 304, 102, 334, 2.2), (109, 304, 105, 334, 2.2), (110.5, 304, 108, 330, 2.2),
        (171.5, 298.5, 171.5, 298.5, 9), (171, 299, 164, 306, 5),  # femoral head, neck
        (164, 306, 177, 422, 7.5), (177.5, 424, 177.5, 424, 11),   # femur
        (180, 439, 180, 439, 11), (181, 440, 182.5, 560, 6),       # tibia
        (171.5, 442, 174, 562, 2.8),                               # fibula
        (181, 565, 181, 565, 8),                                   # tarsals
        (178, 570, 166, 590, 2.4), (179.5, 571, 171, 592, 2.4), (181, 571, 176, 593, 2.4),
        (182.5, 571, 181, 593, 2.4), (184, 570, 186, 592, 2.4),
    ]
    bones = left + [(2 * BODY_CX - a, b, 2 * BODY_CX - c, d, w) for a, b, c, d, w in left]
    outer = "".join(
        f'<line x1="{_n(a)}" y1="{_n(b)}" x2="{_n(c)}" y2="{_n(d)}" stroke-width="{_n(w)}"/>'
        for a, b, c, d, w in bones
    )
    inner = "".join(
        f'<line x1="{_n(a)}" y1="{_n(b)}" x2="{_n(c)}" y2="{_n(d)}" '
        f'stroke-width="{_n(w - 2 if w >= 5 else max(w - 1.5, 0.6))}"/>'
        for a, b, c, d, w in bones
    )
    parts = [
        f'<g stroke="currentColor" stroke-linecap="round">{outer}</g>',
        f'<g stroke="{BONE_CORE}" stroke-linecap="round">{inner}</g>',
    ]

    solid = f'fill="{BONE_CORE}" stroke="currentColor"'

    # Skull, with nasal aperture and teeth. The orbits belong to the neuro layer.
    skull = [(200, 25), (188, 27), (179, 36), (177, 50), (179, 62), (182, 73),
             (187, 83), (194, 89), (200, 90)]
    parts.append(
        f'<path d="{_smooth(skull + _mx(skull)[-2:0:-1], True)}" {solid} '
        f'fill-opacity="0.55" stroke-width="1.3"/>'
        f'<path d="M200,69 L196.5,77 Q200,79.5 203.5,77 Z" {solid} stroke-width="0.8"/>'
        '<path d="M191,84 H209 M194,82 V86 M197,82 V86 M200,82 V86 M203,82 V86 M206,82 V86 '
        'M181,70 Q185,74 189,72 M219,70 Q215,74 211,72" fill="none" stroke="currentColor" '
        'stroke-width="0.8" stroke-linecap="round"/>'
    )

    # Scapulae, faint, behind the rib cage.
    scapula = [(153, 124), (170, 128), (167, 158), (159, 166), (151, 142)]
    parts.append(_paths(_both(scapula, True),
                        'fill="none" stroke="currentColor" stroke-width="0.9" opacity="0.45"'))

    # Vertebral column: 7 cervical, 12 thoracic, 5 lumbar.
    spine = []
    for i in range(7):
        spine.append((93 + i * 3.6, 2.6, 8))
    for i in range(12):
        spine.append((119.5 + i * 7.9, 5.8, 9 + i * 0.28))
    for i in range(5):
        spine.append((215 + i * 12.6, 9.6, 13 + i * 0.7))
    parts.append("".join(
        f'<rect x="{_n(BODY_CX - w / 2)}" y="{_n(y)}" width="{_n(w)}" height="{_n(h)}" '
        f'rx="1.4" {solid} stroke-width="0.9"/>'
        for y, h, w in spine
    ))

    # Sacrum and pelvis.
    ilium = [(195, 286), (189, 274), (176, 269), (164, 272), (160, 283), (165, 295),
             (173, 301), (179, 311), (187, 320), (196, 318)]
    parts.append(
        f'<path d="M191.5,279 L208.5,279 L203.5,300 L200,305 L196.5,300 Z" {solid} '
        f'stroke-width="1.1" stroke-linejoin="round"/>'
        + _paths(_both(ilium, True), f'{solid} fill-opacity="0.7" stroke-width="1.3"')
        + f'<ellipse cx="188" cy="311" rx="3.2" ry="4.2" {solid} stroke-width="0.8"/>'
        + f'<ellipse cx="212" cy="311" rx="3.2" ry="4.2" {solid} stroke-width="0.8"/>'
    )

    # Rib cage: ten hooked ribs and two floating ribs per side, plus sternum.
    widths = [19, 26, 31, 35, 38, 40, 41, 41, 40, 37, 28, 20]
    ribs = []
    for side in (-1, 1):
        for i, w in enumerate(widths):
            y = 121 + i * 7.9

            def x(dx: float) -> str:
                return _n(BODY_CX + side * dx)

            d = (f"M{x(4)},{_n(y + 1)} C{x(w * 0.5)},{_n(y - 5)} {x(w)},{_n(y - 4)} "
                 f"{x(w)},{_n(y + 5 + i * 0.4)}")
            if i < 10:
                drop = 9 + i * 1.5
                d += (f" C{x(w)},{_n(y + 5 + drop * 0.7)} {x(w * 0.75)},{_n(y + drop)} "
                      f"{x(w * 0.5)},{_n(y + drop)}")
            if i < 7:
                d += f" L{x(4.5)},{_n(127 + i * 8.6)}"
            ribs.append(d)
    parts.append(
        _paths(ribs, 'fill="none" stroke="currentColor" stroke-width="1.5" '
                     'stroke-linecap="round" stroke-linejoin="round" opacity="0.8"')
        + f'<path d="M196,125 Q200,122 204,125 L204.5,181 L200,190 L195.5,181 Z" {solid} '
          f'stroke-width="1.1" stroke-linejoin="round"/>'
    )

    # Patellae sit in front of the knee joint.
    parts.append(
        f'<circle cx="178" cy="431" r="3.6" {solid} stroke-width="1.2"/>'
        f'<circle cx="222" cy="431" r="3.6" {solid} stroke-width="1.2"/>'
    )

    # A few muscle contours: deltoid, vastus lateralis, gastrocnemius.
    muscles = (
        _both([(156, 124), (150, 140), (141, 158), (135, 168)])
        + _both([(159, 322), (161, 370), (167.5, 414)])
        + _both([(165, 450), (164, 480), (169, 520)])
    )
    parts.append(_paths(muscles, 'fill="none" stroke="currentColor" stroke-width="0.9" '
                                 'stroke-linecap="round" opacity="0.5"'))
    return "".join(parts)


def _vessels() -> str:
    """Arterial tree and the central veins. The heart is drawn separately, on top."""
    line = 'fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"'
    arm = [(184, 127), (162, 127), (152, 146), (147, 172), (142.5, 196), (136, 222),
           (118, 258), (107.5, 292)]
    leg = [(193, 296), (187, 314), (185.5, 370), (183.5, 425), (186.5, 470),
           (186.5, 552), (183, 578)]
    carotid = [(192.5, 110), (191.5, 96), (188, 82), (184, 70), (181, 58)]
    jugular = [(186, 82), (189, 100), (190, 118)]
    veins = [
        (_smooth(jugular + [(194, 134), (195, 150)]), 2),
        (_smooth(_mx(jugular) + [(205, 130), (196, 140)]), 2),
        ("M195,184 L195,282", 3),
        ("M195,282 L187,296 M195,282 L209,297", 2),
    ]
    arteries = [
        ("M206,170 L206,282", 4),
        (_smooth([(203, 141)] + arm), 1.9),
        (_smooth([(212, 139)] + _mx(arm)), 1.9),
        (_smooth([(206, 282)] + leg), 2.2),
        (_smooth([(206, 282)] + _mx(leg)), 2.2),
        (_smooth([(201, 141), (194, 128)] + carotid), 1.9),
        (_smooth([(209, 139), (206, 128)] + _mx(carotid)), 1.9),
        ("M206,236 L191,241 M206,236 L221,241", 1.5),   # renal branches
    ]
    return (
        '<g opacity="0.5">'
        + "".join(f'<path d="{d}" {line} stroke-width="{w}"/>' for d, w in veins)
        + "</g>"
        + "".join(f'<path d="{d}" {line} stroke-width="{w}"/>' for d, w in arteries)
    )


def _heart() -> str:
    outline = [(197, 158), (204, 152), (214, 152), (223, 159), (227, 171), (225, 184),
               (220, 191), (210, 189), (200, 182), (195, 170)]
    return (
        '<g class="heart">'
        '<path d="M204,156 C204,138 215,134 215,146 C215,155 208,160 206,172" fill="none" '
        'stroke="currentColor" stroke-width="4.6" stroke-linecap="round"/>'
        '<path d="M210,157 C210,146 201,145 196,150" fill="none" stroke="currentColor" '
        'stroke-width="3.6" stroke-linecap="round" opacity="0.8"/>'
        f'<path d="{_smooth(outline, True)}" fill="currentColor" stroke="{BONE_CORE}" '
        'stroke-width="0.9"/>'
        f'<path d="M203,158 Q208,172 217,188 M197,167 Q210,160 224,164 M210,166 Q219,172 222,182" '
        f'fill="none" stroke="{BONE_CORE}" stroke-width="0.9" stroke-linecap="round" '
        'opacity="0.55"/>'
        "</g>"
    )


def _lymphatics() -> str:
    dotted = ('fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" '
              'stroke-dasharray="0.1 3.1"')
    arm = [(104, 291), (113, 258), (121, 226), (126.5, 196), (130, 168), (137, 140),
           (152, 124), (168, 117), (181, 115)]
    axilla = [(157, 153), (168, 134), (181, 115)]
    neck = [(185, 89), (189, 103), (181, 115)]
    leg = [(188.5, 548), (189.5, 500), (189, 470), (187.5, 438), (190, 372), (190, 330),
           (184, 311), (192, 292), (204, 270), (208, 262)]
    duct = [(208, 262), (211, 240), (211, 180), (212, 140), (216, 124), (219, 115)]
    ducts = (
        _both(arm) + _both(axilla) + _both(neck)
        + [_smooth(leg), _smooth(_mx(leg)[:-1] + [(208, 262)]), _smooth(duct)]
    )
    nodes = [(185, 89), (189, 103), (181, 115), (157, 153), (161, 160), (154, 161),
             (180, 305), (185, 311), (178, 313), (187.5, 438), (186, 250), (184, 262)]
    nodes += _mx(nodes)
    spleen = [(226, 210), (234, 212), (238, 222), (234, 234), (227, 232), (224, 221)]
    marrow = [(165, 312, 176, 414, 2.2), (142, 140, 130.5, 212, 1.8)]
    marrow += [(2 * BODY_CX - a, b, 2 * BODY_CX - c, d, w) for a, b, c, d, w in marrow]
    return (
        "".join(
            f'<line x1="{_n(a)}" y1="{_n(b)}" x2="{_n(c)}" y2="{_n(d)}" stroke="currentColor" '
            f'stroke-width="{w}" stroke-linecap="round" opacity="0.9"/>'
            for a, b, c, d, w in marrow
        )
        + _paths(ducts, dotted)
        + "".join(
            f'<ellipse cx="{_n(x)}" cy="{_n(y)}" rx="2.3" ry="2.9" fill="currentColor" '
            f'stroke="{BONE_CORE}" stroke-width="0.7"/>'
            for x, y in nodes
        )
        + f'<path d="{_smooth(spleen, True)}" fill="currentColor" stroke="{BONE_CORE}" '
          'stroke-width="0.9"/>'
        + f'<path d="M226,216 Q231,222 229,230" fill="none" stroke="{BONE_CORE}" '
          'stroke-width="0.8" opacity="0.5"/>'
        # thymus, two lobes in front of the great vessels
        + f'<ellipse cx="197.4" cy="143" rx="3.3" ry="6.2" fill="currentColor" '
          f'stroke="{BONE_CORE}" stroke-width="0.8"/>'
        + f'<ellipse cx="202.6" cy="143.6" rx="3.3" ry="6.6" fill="currentColor" '
          f'stroke="{BONE_CORE}" stroke-width="0.8"/>'
    )


def _nerves() -> str:
    line = 'fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"'
    median = [(176, 124), (160, 131), (150, 148), (143.5, 172), (139.5, 196), (133, 222),
              (123, 258), (113, 292), (108, 303)]
    sciatic = [(188, 296), (180, 318), (178, 345), (178, 385), (179.5, 420), (177, 445),
               (176, 480), (177, 552)]
    thin = []
    for pts in (median, sciatic):
        thin += _both(pts)
    for side in (-1, 1):
        def x(dx: float) -> str:
            return _n(BODY_CX + side * dx)
        # brachial plexus roots, lumbosacral roots, intercostal stubs, finger branches
        thin += [f"M200,{y} L{x(24)},124" for y in (106, 111, 116, 121)]
        thin += [f"M200,{y} L{x(12)},296" for y in (256, 266, 276)]
        thin += [f"M200,{_n(123 + i * 7.9)} L{x(10)},{_n(126 + i * 7.9)}" for i in range(1, 12)]
        thin += [f"M{x(92)},303 L{x(d)},{y}" for d, y in ((99, 322), (96, 326), (93, 324))]
    hemisphere = [(199, 28), (191, 27), (183, 32), (179, 41), (181, 50), (188, 55), (199, 55)]
    gyri = ("M183,37 q4,-4 8,0 t7,-1 M181,44 q5,-4 9,0 t8,0 M184,50 q4,-3 8,0 t6,0 "
            "M187,31 q4,3 9,1")
    fold = f'fill="none" stroke="{BONE_CORE}" stroke-width="0.85" stroke-linecap="round" opacity="0.6"'
    eyes = "".join(
        f'<circle cx="{cx}" cy="65" r="4.6" fill="{BONE_CORE}" stroke="currentColor" stroke-width="1.2"/>'
        f'<circle cx="{cx}" cy="65" r="2.3" fill="currentColor"/>'
        f'<circle cx="{cx}" cy="65" r="0.9" fill="{BONE_CORE}"/>'
        for cx in (190, 210)
    )
    return (
        _paths(thin, f'{line} stroke-width="1.05" opacity="0.9"')
        # brainstem, spinal cord, cauda equina
        + f'<path d="M200,54 L200,252" {line} stroke-width="2.4"/>'
        + f'<path d="M200,252 L197,284 M200,252 L200,288 M200,252 L203,284" {line} stroke-width="0.9"/>'
        # optic nerves meet at the chiasm
        + f'<path d="M190,65 L199,57 M210,65 L201,57" {line} stroke-width="1.5"/>'
        + _paths(_both(hemisphere, True),
                 f'fill="currentColor" stroke="{BONE_CORE}" stroke-width="0.9"')
        + f'<path d="{gyri}" {fold}/>'
        + f'<path d="{gyri}" {fold} transform="matrix(-1 0 0 1 {2 * BODY_CX} 0)"/>'
        + eyes
    )


def _context_organs() -> str:
    """Lungs, airway, liver and gut: drawn for orientation, carry no status."""
    lung = [(193, 130), (183, 127), (172, 138), (165, 160), (163, 186), (166, 200),
            (180, 196), (193, 188), (194, 160)]
    liver = [(165, 208), (178, 202), (196, 203), (212, 209), (204, 220), (186, 228),
             (170, 230), (164, 220)]
    return (
        f'<g pointer-events="none" stroke="{ORGAN_GREY}" fill="{ORGAN_GREY}">'
        + _paths(_both(lung, True), 'fill-opacity="0.13" stroke-opacity="0.45" stroke-width="0.9"')
        + '<path d="M200,96 L200,128 M200,128 L190,140 M200,128 L210,140" fill="none" '
          'stroke-width="3" stroke-opacity="0.3" stroke-linecap="round"/>'
        + f'<path d="{_smooth(liver, True)}" fill-opacity="0.16" stroke-opacity="0.45" '
          'stroke-width="0.9"/>'
        + '<path d="M172,290 L172,250 Q172,240 182,240 L218,240 Q228,240 228,250 L228,290" '
          'fill="none" stroke-width="5.5" stroke-opacity="0.2" stroke-linecap="round"/>'
        + '<path d="M182,254 q9,-6 18,0 t18,0 M182,265 q9,6 18,0 t18,0 M182,277 q9,-6 18,0 t18,0" '
          'fill="none" stroke-width="3.6" stroke-opacity="0.16" stroke-linecap="round"/>'
        + "</g>"
    )


# Callouts: (side, baseline y, text, system id, target x, target y)
BODY_LABELS = [
    ("L", 30, "BRAIN", "neurobehavioral", 181, 41),
    ("L", 58, "OPTIC NERVE", "neurobehavioral", 193, 62),
    ("L", 86, "CERVICAL NODES", "immune", 185, 89),
    ("L", 112, "CLAVICLE", "musculoskeletal", 166, 120.3),
    ("L", 140, "DELTOID", "musculoskeletal", 150, 140),
    ("L", 168, "AXILLARY NODES", "immune", 156, 156),
    ("L", 196, "HUMERUS", "musculoskeletal", 132.9, 196),
    ("L", 224, "BRACHIAL ARTERY", "cardiovascular", 139.5, 210),
    ("L", 252, "RADIUS · ULNA", "musculoskeletal", 117, 257),
    ("L", 280, "MEDIAN NERVE", "neurobehavioral", 116.5, 281),
    ("L", 366, "INGUINAL NODES", "immune", 178, 313),
    ("L", 394, "BONE MARROW", "immune", 170.5, 362),
    ("L", 422, "FEMUR", "musculoskeletal", 175, 405),
    ("L", 450, "PATELLA", "musculoskeletal", 176, 431),
    ("L", 478, "TIBIAL NERVE", "neurobehavioral", 176, 478),
    ("L", 506, "GASTROCNEMIUS", "musculoskeletal", 165.5, 500),
    ("L", 540, "FIBULA", "musculoskeletal", 173.5, 540),
    ("R", 30, "SKULL", "musculoskeletal", 221, 35),
    ("R", 58, "CAROTID ARTERY", "cardiovascular", 213.5, 78),
    ("R", 86, "JUGULAR VEIN", "cardiovascular", 211, 100),
    ("R", 110, "SPINAL CORD", "neurobehavioral", 200.5, 116),
    ("R", 132, "AORTIC ARCH", "cardiovascular", 214.5, 141),
    ("R", 154, "THYMUS", "immune", 204.5, 147),
    ("R", 176, "HEART", "cardiovascular", 226, 174),
    ("R", 198, "RIB CAGE", "musculoskeletal", 239, 196),
    ("R", 224, "SPLEEN", "immune", 237, 223),
    ("R", 252, "THORACIC DUCT", "immune", 211, 240),
    ("R", 280, "ABDOMINAL AORTA", "cardiovascular", 206, 266),
    ("R", 308, "PELVIS", "musculoskeletal", 238, 286),
    ("R", 366, "SCIATIC NERVE", "neurobehavioral", 222, 345),
    ("R", 394, "FEMORAL ARTERY", "cardiovascular", 214.7, 380),
    ("R", 422, "QUADRICEPS", "musculoskeletal", 233.8, 402),
    ("R", 450, "POPLITEAL NODE", "immune", 212.5, 438),
    ("R", 478, "TIBIA", "musculoskeletal", 218.3, 478),
    ("R", 540, "TIBIAL ARTERY", "cardiovascular", 213.5, 540),
]


def _callouts(levels: dict, selected: str) -> str:
    out = []
    for side, y, text, sid, tx, ty in BODY_LABELS:
        edge, knee, anchor = (-28, 60, "start") if side == "L" else (428, 340, "end")
        klass = "lbl sel" if sid == selected else "lbl"
        out.append(
            f'<g data-region="{sid}" class="{klass}">'
            f'<polyline points="{edge},{y + 3.5} {knee},{y + 3.5} {_n(tx)},{_n(ty)}"/>'
            f'<circle cx="{_n(tx)}" cy="{_n(ty)}" r="1.9" fill="{COLORS[levels[sid]]}"/>'
            f'<rect x="{edge if side == "L" else knee}" y="{y - 10}" width="88" height="15" '
            f'fill="none" pointer-events="all"/>'
            f'<text x="{edge}" y="{y}" text-anchor="{anchor}">{xml_text(text)}</text>'
            "</g>"
        )
    return "".join(out)


def body_svg(levels: dict, selected: str, flux: float) -> str:
    """Anatomical plate of the crew member, front view.

    Each of the four systems is drawn as its own anatomy (skeleton and muscle,
    heart and vessels, lymphatics and marrow, brain and nerves) in that system's
    status color. Everything carrying a data-region id is clickable; clicks are
    handled on the wrapper and the browser reports which region was hit. Broad
    invisible zones (head, chest, abdomen, limbs) catch clicks between the fine
    lines. The body outline glows while flux is in the storm bands.
    """
    if flux > 500:
        klass = "body-svg spe"
    elif flux > 100:
        klass = "body-svg storm"
    else:
        klass = "body-svg"

    outline = _body_outline()

    def layer(sid: str, content: str) -> str:
        meta = next(item for item in SYSTEMS if item["id"] == sid)
        title = xml_text(f"{meta['title']} — {LEVEL_NAME[levels[sid]]}")
        sel = " sel" if sid == selected else ""
        return (
            f'<g data-region="{sid}" class="sys{sel}" style="color:{COLORS[levels[sid]]}">'
            f"<title>{title}</title>{content}</g>"
        )

    def zone(sid: str, shape: str) -> str:
        sel = " sel" if sid == selected else ""
        return f'<g data-region="{sid}" class="zone{sel}" clip-path="url(#body-clip)">{shape}</g>'

    return f"""
<svg class="{klass}" viewBox="-30 0 460 620" width="100%" height="auto"
     xmlns="http://www.w3.org/2000/svg" role="img" aria-label="Crew anatomical body map">
  <defs><clipPath id="body-clip"><path d="{outline}"/></clipPath></defs>
  <path class="halo" d="{outline}" fill="none" stroke-width="7" stroke-linejoin="round"
        pointer-events="none"/>
  <path data-region="musculoskeletal" d="{outline}" fill="#16233A" stroke="#4A6184"
        stroke-width="1.4" stroke-linejoin="round"/>
  {zone("musculoskeletal", f'<path d="{outline}"/>')}
  {zone("cardiovascular", '<path d="M154,110 H246 V208 H154 Z"/>')}
  {zone("immune", '<path d="M154,208 H246 V296 L200,324 L154,296 Z"/>')}
  {zone("neurobehavioral", '<ellipse cx="200" cy="58" rx="30" ry="38"/>')}
  {_context_organs()}
  {layer("musculoskeletal", _skeleton())}
  {layer("cardiovascular", _vessels())}
  {layer("neurobehavioral", _nerves())}
  {layer("cardiovascular", _heart())}
  {layer("immune", _lymphatics())}
  {_callouts(levels, selected)}
</svg>
"""


def _apply_chart_theme(fig: go.Figure, title: str) -> None:
    fig.update_layout(
        title=dict(text=title, font=dict(size=15, color="#E8EEF7")),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="#10182A",
        font=dict(family="Segoe UI, Arial, sans-serif", color="#D5DEEA", size=12),
        margin=dict(l=56, r=56, t=52, b=64),
        height=340,
        legend=dict(orientation="h", yanchor="top", y=-0.22, x=0),
        hovermode="x unified",
    )
    fig.update_xaxes(gridcolor="#243044", title_text="Medical-log day", dtick=2, zeroline=False)
    fig.update_yaxes(gridcolor="#243044", zeroline=False)


def build_hr_figure(data: pd.DataFrame) -> go.Figure:
    # Visual smoother includes each day. The alert line is the prior-7-day
    # mean times 1.12 — the same baseline the scorer uses.
    rolling = data["resting_hr"].rolling(7).mean()
    alert = data["resting_hr"].rolling(7).mean().shift(1) * 1.12
    sols = [sol_number(int(day)) for day in data["day"]]
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=data["day"], y=data["resting_hr"], name="Resting HR",
        customdata=sols, line=dict(color="#7EE0FF", width=2.4),
        hovertemplate="Sol %{customdata}<br>HR %{y:.0f} bpm<extra></extra>",
    ))
    fig.add_trace(go.Scatter(
        x=data["day"], y=rolling, name="7-day rolling mean",
        line=dict(color="#F5A524", width=2, dash="dot"),
        hovertemplate="7-day mean %{y:.0f} bpm<extra></extra>",
    ))
    fig.add_trace(go.Scatter(
        x=data["day"], y=alert, name="Personal +12% alert",
        line=dict(color="#FF5A6A", width=1.6, dash="dash"),
        hovertemplate="+12% line %{y:.0f} bpm<extra></extra>",
    ))
    fig.add_vline(x=24, line_dash="dot", line_color="#F5A524")
    _apply_chart_theme(fig, "Resting heart rate vs personal baseline")
    fig.update_yaxes(title_text="bpm")
    return fig


def build_sleep_figure(data: pd.DataFrame) -> go.Figure:
    sols = [sol_number(int(day)) for day in data["day"]]
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=data["day"], y=data["sleep_hours"], name="Sleep",
        customdata=sols, line=dict(color="#C9B6FF", width=2.4),
        fill="tozeroy", fillcolor="rgba(201,182,255,0.12)",
        hovertemplate="Sol %{customdata}<br>%{y:.1f} h<extra></extra>",
    ))
    fig.add_hline(y=6.5, line_dash="dash", line_color="#F5A524",
                  annotation_text="watch 6.5 h", annotation_font_color="#F5A524")
    fig.add_hline(y=5.0, line_dash="dash", line_color="#FF5A6A",
                  annotation_text="action 5 h", annotation_font_color="#FF5A6A")
    fig.add_vline(x=24, line_dash="dot", line_color="#F5A524")
    _apply_chart_theme(fig, "Sleep hours")
    fig.update_yaxes(title_text="hours", range=[0, 10])
    return fig


def build_radiation_figure(data: pd.DataFrame) -> go.Figure:
    sols = [sol_number(int(day)) for day in data["day"]]
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=data["day"], y=data["radiation_flux"], name="Flux (µSv/hr)",
        customdata=sols, line=dict(color="#FF5A6A", width=2.4),
        hovertemplate="Sol %{customdata}<br>%{y:.0f} µSv/hr<extra></extra>",
    ))
    fig.add_trace(go.Scatter(
        x=data["day"], y=data["cumulative_dose"], name="Cumulative (mSv)",
        yaxis="y2", line=dict(color="#7EE0FF", width=2.4),
        hovertemplate="Cumulative %{y:.1f} mSv<extra></extra>",
    ))
    fig.add_hline(y=100, line_dash="dash", line_color="#F5A524",
                  annotation_text="watch 100", annotation_font_color="#F5A524")
    fig.add_hline(y=500, line_dash="dash", line_color="#FF8A96",
                  annotation_text="action 500", annotation_font_color="#FF8A96")
    fig.add_vline(x=24, line_dash="dot", line_color="#F5A524")
    dose_min = float(data["cumulative_dose"].min()) - 4
    dose_max = max(450.0, float(data["cumulative_dose"].max()) + 4)
    flux_max = max(800.0, float(data["radiation_flux"].max()) * 1.08)
    _apply_chart_theme(fig, "Radiation flux and cumulative dose")
    fig.update_layout(
        yaxis=dict(title="µSv/hr", gridcolor="#243044", range=[0, flux_max], zeroline=False),
        yaxis2=dict(
            title="mSv",
            overlaying="y",
            side="right",
            range=[dose_min, dose_max],
            gridcolor="rgba(0,0,0,0)",
            zeroline=False,
        ),
    )
    # Cumulative red line sits on the dose axis.
    fig.add_shape(
        type="line", xref="paper", x0=0, x1=1,
        yref="y2", y0=450, y1=450,
        line=dict(color="#7EE0FF", width=1, dash="dot"),
    )
    return fig


# ---------------------------------------------------------------------------
# Self-check (no UI, no network)
# ---------------------------------------------------------------------------

def self_check() -> list[str]:
    """Prove the scoring rules on the synthetic log, independent of later edits."""
    raw = pd.DataFrame(synthesize())
    errors: list[str] = []

    day24 = assess(raw[raw["day"] <= 24].copy())
    if day24["systems"]["immune"]["level"] != "red":
        errors.append(f"day 24 immune expected red, got {day24['systems']['immune']}")
    if day24["systems"]["cardiovascular"]["level"] != "red":
        errors.append("day 24 cardiovascular expected red")
    if not (day24["dose_jump"] > 10):
        errors.append(f"day 24 dose jump expected > 10 mSv, got {day24['dose_jump']:.2f}")
    if not (dose_mSv_from_flux(SPE_FLUX) > 10):
        errors.append("650 µSv/hr for 24 h should exceed a 10 mSv jump")

    nominal = assess(raw)
    for sid, sysrep in nominal["systems"].items():
        if sysrep["level"] != "green":
            errors.append(f"day 30 {sid} expected green, got {sysrep['level']}: {sysrep['reasons']}")

    base = float(raw["resting_hr"].rolling(7).mean().shift(1).iloc[-1])
    mild = raw.copy()
    mild.loc[mild.index[-1], "resting_hr"] = base * 1.10
    mild_report = assess(mild)
    if mild_report["systems"]["cardiovascular"]["level"] != "green":
        errors.append(
            "a 10% heart-rate rise should stay nominal; "
            f"got {mild_report['systems']['cardiovascular']['level']}: "
            f"{mild_report['systems']['cardiovascular']['reasons']}"
        )

    spiked = raw.copy()
    spiked.loc[spiked.index[-1], "resting_hr"] = base * 1.13
    spiked_report = assess(spiked)
    if spiked_report["systems"]["cardiovascular"]["level"] != "amber":
        errors.append(
            "a 13% heart-rate rise should be amber; "
            f"got {spiked_report['systems']['cardiovascular']['level']}: "
            f"{spiked_report['systems']['cardiovascular']['reasons']}"
        )

    coupled = raw.copy()
    last = coupled.index[-1]
    prev = coupled.index[-2]
    coupled.loc[last, "radiation_flux"] = 150
    coupled.loc[last, "cumulative_dose"] = float(coupled.loc[prev, "cumulative_dose"]) + 0.4
    coupled_report = assess(coupled)
    if coupled_report["systems"]["immune"]["level"] != "amber":
        errors.append(f"150 µSv/hr should force immune amber, got {coupled_report['systems']['immune']['level']}")
    if coupled_report["systems"]["cardiovascular"]["level"] != "amber":
        errors.append("150 µSv/hr should force cardiovascular amber")
    if coupled_report["systems"]["neurobehavioral"]["level"] != "green":
        errors.append("neuro should stay green when only flux is elevated")
    if coupled_report["systems"]["musculoskeletal"]["level"] != "green":
        errors.append("musculoskeletal should stay green when only flux is elevated")

    coupled.loc[last, "radiation_flux"] = 650
    red = assess(coupled)
    if red["systems"]["immune"]["level"] != "red" or red["systems"]["cardiovascular"]["level"] != "red":
        errors.append("650 µSv/hr should force immune and cardiovascular to red")
    if red["systems"]["neurobehavioral"]["level"] != "green":
        errors.append("neuro should not be force-raised by flux alone")

    tired = raw.copy()
    end = tired.index[-1]
    tired.loc[end - 2:end, "exercise_mins"] = 20
    tired_report = assess(tired)
    if tired_report["systems"]["musculoskeletal"]["level"] == "green":
        errors.append("three days under 30 exercise minutes should not stay nominal")

    return errors


# ---------------------------------------------------------------------------
# Interface
# ---------------------------------------------------------------------------

def _unwrap(event) -> dict:
    payload = getattr(event, "args", None)
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            return {"kind": "raw", "value": payload}
    if isinstance(payload, list) and payload:
        payload = payload[0]
    if isinstance(payload, dict):
        return payload
    return {"kind": "raw", "value": payload}


def _lead(level: str) -> str:
    if level == "red":
        return "Action required. Work the checklist now and tell the crew medical officer."
    if level == "amber":
        return "Elevated watch. Work through the checklist on this shift."
    return "Nominal. No action beyond the usual daily routine."


RULES_TEXT = (
    "Personal baselines use pandas rolling(7).mean(), then shift back one day, "
    "so today is compared with the previous seven days. Resting heart rate alerts "
    "when it is more than 12% above that personal baseline (more than 20% is action).\n\n"
    "Radiation is coupled to immune and cardiovascular status. Flux above 100 µSv/hr "
    "raises both to at least Elevated Watch. Flux above 500 µSv/hr raises both to "
    "Action Required, even if the white-cell count is still normal. A 24-hour dose "
    "jump above 10 mSv marks the dose card Action Required and raises those two "
    "systems to at least Elevated Watch.\n\n"
    "Other cuts: HRV under 30 ms (under 20 ms action), oxygen 93–95 watch and 92 or "
    "below action, blood pressure outside 95–135 / 60–85, temperature 37.5 °C watch "
    "and 38.0 °C action, white cells under 4.0 (under 3.0 action), symptom score 1 or "
    "more, sleep under 6.5 h (under 5 h action), reaction time from 290 ms (above 350 ms "
    "action), mood 2 or below, eye flag 1 watch and 2 action, exercise under 30 min for "
    "3 days in a row, back pain 4 or more."
)


def build_page() -> None:
    ui.dark_mode(True)
    ui.add_head_html(f"<style>{PAGE_CSS}</style>")
    ui.colors(
        primary="#14B8C7",
        secondary="#F5A524",
        accent="#FF5A6A",
        positive="#3DDC97",
        negative="#FF5A6A",
        warning="#F5A524",
        dark="#0B1220",
    )

    state = {
        "suspend": False,
        "selected": "cardiovascular",
        "phase": "idle",  # idle | starting | wait | go
        "run": 0,
        "samples": [],
        "hit": None,
        "false_start": False,
        "mood": None,
        "symptoms": None,
        "pain": None,
        "eyes": None,
        "report": None,
        "df": None,
    }
    boxes: dict = {}

    def refresh() -> None:
        state["suspend"] = True
        try:
            state["df"] = load_df()
            state["report"] = assess(state["df"])
            render_header()
            render_map()
            render_panel()
            render_charts()
            report = state["report"]
            boxes["reaction_caption"].set_text(
                f"Logged reaction time today: {report['prepared'].iloc[-1]['reaction_time']:.0f} ms"
            )
            boxes["day_label"].set_text(
                f"Now entering log day {report['day']}  ·  {pretty_date(report['date'])}"
            )
            sync_inputs(report["prepared"].iloc[-1])
        finally:
            state["suspend"] = False

    def sync_inputs(row) -> None:
        """Make the crew-log sliders and the manual form show what is in the database."""
        state["mood"] = int(row["mood_score"])
        state["eyes"] = int(row["ocular_headache_flag"])
        state["symptoms"] = int(row["radiation_symptoms"])
        state["pain"] = int(row["back_joint_pain"])
        boxes["mood_slider"].value = state["mood"]
        boxes["eye_toggle"].value = state["eyes"]
        boxes["sym_slider"].value = state["symptoms"]
        boxes["pain_slider"].value = state["pain"]
        fill_manual_form(row)

    def fill_manual_form(row) -> None:
        for key, field in boxes["inputs"].items():
            value = row[key]
            field.value = int(value) if key in INT_FIELDS else float(value)
        boxes["notes_input"].value = str(row["notes"] or "")

    def reset_manual_form() -> None:
        fill_manual_form(load_df().iloc[-1])

    def dose_from_flux() -> None:
        flux = boxes["inputs"]["radiation_flux"].value
        if flux is None:
            ui.notify("Enter a radiation flux first.", type="warning")
            return
        frame = state["df"]
        prev_dose = float(frame.iloc[-2]["cumulative_dose"]) if len(frame) > 1 else 0.0
        boxes["inputs"]["cumulative_dose"].value = round(prev_dose + dose_mSv_from_flux(float(flux)), 2)

    def save_manual() -> None:
        values: dict = {}
        problems: list[str] = []
        for key, field in boxes["inputs"].items():
            label = FIELD_LABELS[key][0]
            lo, hi, _step = INPUT_LIMITS[key]
            raw = field.value
            if raw is None or raw == "":
                problems.append(f"{label} is empty")
                continue
            number = float(raw)
            if not lo <= number <= hi:
                problems.append(f"{label} must be between {lo:g} and {hi:g}")
                continue
            values[key] = int(round(number)) if key in INT_FIELDS else number
        if problems:
            ui.notify("Not saved: " + "; ".join(problems), type="negative", multi_line=True)
            return
        values["notes"] = str(boxes["notes_input"].value or "")
        if state_get("spe_active", "0") == "1":
            clear_spe_flag()
        update_today(**values)
        refresh()
        ui.notify("Today's telemetry saved and rescored.", type="positive")

    def confirm_next_day() -> None:
        boxes["next_dialog"].close()
        if state["phase"] != "idle":
            ui.notify("Finish the reaction test before starting the next day.", type="warning")
            return
        day = start_next_day()
        refresh()
        ui.notify(f"Log day {day} started. Enter today's readings and save.", type="positive")

    def select(system_id: str) -> None:
        if state["suspend"] or system_id not in SYSTEM_IDS:
            return
        if state["selected"] == system_id:
            return
        state["selected"] = system_id
        state["suspend"] = True
        try:
            render_map()
            render_panel()
        finally:
            state["suspend"] = False

    def on_svg_click(event) -> None:
        if state["suspend"]:
            return
        region = _unwrap(event).get("value")
        if region is None:
            payload = getattr(event, "args", None)
            region = payload if isinstance(payload, str) else None
        if isinstance(region, str):
            select(region)

    def persist_symptom(column: str, value: int, cache_key: str) -> None:
        if state["suspend"] or state[cache_key] == value:
            return
        state[cache_key] = value
        update_today(**{column: value})
        refresh()

    async def start_cognitive() -> None:
        if state["phase"] != "idle":
            return
        state["phase"] = "starting"
        state["run"] += 1
        run_id = state["run"]
        state["samples"] = []
        state["hit"] = None
        state["false_start"] = False
        boxes["cog_start"].disable()
        boxes["cog_result"].set_text("")
        try:
            while len(state["samples"]) < 5 and state["run"] == run_id:
                await run_trial(run_id)
            if state["run"] == run_id and len(state["samples"]) == 5:
                average = sum(state["samples"]) / len(state["samples"])
                write_reaction(average, state["samples"])
                detail = ", ".join(str(int(round(s))) for s in state["samples"])
                boxes["cog_result"].set_text(
                    f"Average {average:.0f} ms ({detail}). Saved to today’s reaction_time."
                )
                refresh()
        except Exception as exc:  # surface the failure in the panel instead of dying silently
            boxes["cog_prompt"].set_text(f"Test stopped: {exc}")
        finally:
            if state["run"] == run_id:
                state["phase"] = "idle"
                boxes["cog_start"].enable()
                await ui.run_javascript(RESET_JS)

    async def run_trial(run_id: int) -> None:
        state["phase"] = "wait"
        state["hit"] = None
        state["false_start"] = False
        await ui.run_javascript(RESET_JS)
        boxes["cog_prompt"].set_text(
            f"Trial {len(state['samples']) + 1} of 5. Wait for the circle to turn cyan, then tap it."
        )
        delay = random.uniform(1.0, 2.3)
        elapsed = 0.0
        while elapsed < delay:
            await asyncio.sleep(0.05)
            elapsed += 0.05
            if state["run"] != run_id:
                return
            if state["false_start"]:
                state["false_start"] = False
                boxes["cog_prompt"].set_text("Too early — that tap does not count. Wait for cyan.")
                await asyncio.sleep(0.7)
                return
        if state["run"] != run_id:
            return
        await ui.run_javascript(FLASH_JS)
        state["phase"] = "go"
        waited = 0.0
        while waited < 3.0:
            await asyncio.sleep(0.05)
            waited += 0.05
            if state["run"] != run_id:
                return
            if state["hit"] is not None:
                ms = float(state["hit"])
                state["hit"] = None
                state["samples"].append(ms)
                state["phase"] = "wait"
                boxes["cog_prompt"].set_text(
                    f"Trial {len(state['samples'])} of 5 captured: {ms:.0f} ms."
                )
                await ui.run_javascript(RESET_JS)
                await asyncio.sleep(0.35)
                return
        state["phase"] = "wait"
        await ui.run_javascript(RESET_JS)
        boxes["cog_prompt"].set_text("No tap on that flash. Same trial again.")
        await asyncio.sleep(0.55)

    def on_pad(event) -> None:
        if state["suspend"]:
            return
        payload = _unwrap(event)
        kind = payload.get("kind")
        if kind == "hit" and state["phase"] == "go" and state["hit"] is None:
            state["hit"] = float(payload.get("ms", 0))
        elif kind == "early" and state["phase"] == "wait":
            state["false_start"] = True

    def render_header() -> None:
        report = state["report"]
        boxes["header"].clear()
        with boxes["header"]:
            with ui.column().classes("w-full").style("gap:6px"):
                ui.label("ASTROTWIN  ·  DEEP-SPACE HEALTH").classes("kicker")
                ui.html(
                    "<h1 class='app-title'>Deep-Space Health &amp; Radiation Monitor</h1>",
                    sanitize=False,
                )
                ui.html(
                    "<div class='badge'>OFFLINE AUTONOMOUS MODE | SIMULATED CREW TELEMETRY</div>",
                    sanitize=False,
                )
            with ui.row().classes("w-full").style("gap:12px; margin-top:12px; flex-wrap:wrap;"):
                _tile("Radiation flux", f"{report['flux']:.0f}", "µSv/hr", report["flux_level"])
                jump = report["dose_jump"]
                _tile(
                    "Cumulative dose",
                    f"{report['dose']:.1f}",
                    f"mSv   ·   {jump:+.1f} mSv / 24 h",
                    report["dose_level"],
                )
                _tile("Cabin CO₂", f"{report['co2']:.1f}", "mmHg", report["co2_level"])
            peak_at = state["df"]["radiation_flux"].idxmax()
            peak = state["df"].loc[peak_at]
            ui.label(
                f"Highest flux in this log: {float(peak['radiation_flux']):.0f} µSv/hr on "
                f"Sol {sol_number(int(peak['day']))} (log day {int(peak['day'])}). "
                "The day-24 spike is the scripted solar particle event."
            ).classes("muted").style("margin-top:8px;")

    def _tile(title: str, value: str, unit: str, level: str) -> None:
        with ui.element("div").classes(f"tile {level}"):
            ui.label(title).classes("muted").style("font-size:12px;")
            ui.label(value).classes("val")
            ui.label(unit).classes("muted").style("font-size:12px;")

    def render_map() -> None:
        report = state["report"]
        levels = {sid: report["systems"][sid]["level"] for sid in SYSTEM_IDS}
        boxes["map"].clear()
        with boxes["map"]:
            with ui.card().classes("panel w-full"):
                ui.label("Body map").classes("section-title")
                ui.label("Click a structure or label. Color is that system’s status right now.").classes("muted")
                svg = ui.html(body_svg(levels, state["selected"], report["flux"]), sanitize=False)
                svg.on(
                    "click",
                    on_svg_click,
                    js_handler="""
                    (e) => {
                      const node = e.target && e.target.closest && e.target.closest('[data-region]');
                      if (node) emit(node.getAttribute('data-region'));
                    }
                    """,
                )
                ui.label("Green nominal   ·   Amber elevated watch   ·   Red action required").classes("muted").style(
                    "font-size:12px; margin:8px 0;"
                )
                for meta in SYSTEMS:
                    level = levels[meta["id"]]
                    ui.button(
                        f"{meta['title']}  ·  {LEVEL_NAME[level]}",
                        on_click=lambda sid=meta["id"]: select(sid),
                    ).classes("sys-btn").style(
                        f"background:{COLORS[level]}; margin-top:6px;"
                    ).props("no-caps")

    def render_panel() -> None:
        report = state["report"]
        sid = state["selected"]
        sysrep = report["systems"][sid]
        meta = next(item for item in SYSTEMS if item["id"] == sid)
        done_map = checklist_map(report["day"])
        items = CHECKS[sid]
        done_count = sum(1 for item_id, _text in items if done_map.get(item_id))
        boxes["panel"].clear()
        with boxes["panel"]:
            with ui.card().classes(f"panel w-full level-{sysrep['level']}"):
                ui.label(meta["title"]).classes("section-title")
                ui.label(LEVEL_NAME[sysrep["level"]].upper()).classes(f"pill {sysrep['level']}")
                ui.label(_lead(sysrep["level"])).style("margin-top:8px; font-weight:650;")
                ui.label(explain(sid, report, state["df"])).style("margin-top:8px; line-height:1.45;")
                ui.label("Today vs your previous 7 days").style("margin-top:14px; font-weight:700;")
                with ui.row().classes("w-full metric-row").style("font-size:12px;"):
                    ui.label("Metric").style("flex:1.4")
                    ui.label("Now").style("width:72px")
                    ui.label("Baseline").style("width:72px")
                    ui.label("Status").style("width:92px")
                for metric in sysrep["metrics"]:
                    with ui.row().classes("w-full items-center metric-row"):
                        with ui.column().style("flex:1.4; gap:0;"):
                            ui.label(metric["name"])
                            if metric["note"]:
                                ui.label(metric["note"]).classes("muted").style("font-size:11px;")
                        ui.label(f"{metric['value']} {metric['unit']}").style("width:72px;")
                        ui.label(metric["baseline"]).style("width:72px;")
                        ui.label(LEVEL_NAME[metric["level"]]).classes(f"pill {metric['level']}")
                ui.label("Why this color").style("margin-top:14px; font-weight:700;")
                for reason in sysrep["reasons"]:
                    ui.label(f"• {reason}").style("line-height:1.4;")
                if report["notes"]:
                    ui.label("Crew note").style("margin-top:12px; font-weight:700;")
                    ui.label(report["notes"]).classes("muted")
                ui.label("Self-triage checklist").style("margin-top:14px; font-weight:700;")
                ui.label(
                    "Checks are stored in SQLite for this sol. They do not leave the computer."
                ).classes("muted")
                status = ui.label(f"{done_count} of {len(items)} countermeasures logged")
                for item_id, text in items:
                    def _save(event, item=item_id, label=status, total=len(items)):
                        if state["suspend"]:
                            return
                        set_check(report["day"], item, bool(event.value))
                        current = checklist_map(report["day"])
                        logged = sum(1 for iid, _t in items if current.get(iid))
                        label.set_text(f"{logged} of {total} countermeasures logged")

                    ui.checkbox(text, value=done_map.get(item_id, False)).on_value_change(_save)

    def render_charts() -> None:
        data = state["report"]["prepared"]
        boxes["charts"].clear()
        with boxes["charts"]:
            ui.label(f"{len(data)}-day trajectories").classes("section-title").style("margin-top:8px;")
            ui.label(
                "Day 24 is the scripted solar particle event. Heart-rate alerts use the "
                "shifted 7-day mean; the dotted line is the rolling mean itself."
            ).classes("muted")
            with ui.row().classes("w-full").style("gap:12px; flex-wrap:wrap; align-items:stretch;"):
                for fig in (
                    build_hr_figure(data),
                    build_sleep_figure(data),
                    build_radiation_figure(data),
                ):
                    with ui.card().classes("panel").style("flex:1 1 320px; min-width:280px;"):
                        ui.plotly(fig).style("width:100%;")
            with ui.expansion("Every logged parameter today", icon="list").classes("w-full"):
                with ui.row().style("gap:8px; flex-wrap:wrap;"):
                    row = data.iloc[-1]
                    for key in TELEMETRY_FIELDS:
                        label, unit, digits = FIELD_LABELS[key]
                        level = state["report"]["field_levels"][key]
                        _tile(label, fmt_num(row[key], digits), unit, level)
            with ui.expansion("How the score is calculated", icon="info").classes("w-full"):
                ui.label(RULES_TEXT).classes("rules muted")
            ui.label(
                "Synthetic crew. Not a medical device. All telemetry, checklist marks, "
                "and reaction times stay in mission_health.db on this computer."
            ).classes("muted").style("margin:12px 0 24px;")

    with ui.column().style("width:min(1180px, 100%); margin:0 auto; padding:18px 16px 32px; gap:14px;"):
        boxes["header"] = ui.column().classes("w-full")
        with ui.row().classes("w-full").style("gap:14px; align-items:flex-start; flex-wrap:wrap;"):
            boxes["map"] = ui.column().style("flex:1 1 340px; min-width:280px;")
            boxes["panel"] = ui.column().style("flex:1.3 1 420px; min-width:300px;")
        with ui.row().classes("w-full").style("gap:14px; align-items:flex-start; flex-wrap:wrap;"):
            with ui.card().classes("panel").style("flex:1 1 340px; min-width:280px;"):
                ui.label("Cognitive reaction test").classes("section-title")
                ui.label(
                    "Five flashes. Tap the circle as soon as it turns cyan. "
                    "Timing is measured in this browser, then the average is written to today’s "
                    "reaction_time and the neurobehavioral score is recalculated."
                ).classes("muted")
                boxes["cog_prompt"] = ui.label("Ready when you are.")
                pad = ui.html(
                    """
                    <div id="astro-pad" style="width:180px;height:180px;border-radius:999px;
                    display:flex;align-items:center;justify-content:center;font-weight:800;
                    font-size:28px;background:#1A2436;color:#8AA0B8;cursor:pointer;
                    user-select:none;margin:12px auto;border:2px solid #2E3E56;
                    font-family:Segoe UI, Arial, sans-serif;">WAIT</div>
                    """,
                    sanitize=False,
                )
                pad.on("click", on_pad, js_handler=TAP_JS)
                boxes["cog_start"] = ui.button(
                    "Run Cognitive Reaction Test",
                    on_click=start_cognitive,
                ).props("no-caps")
                boxes["cog_result"] = ui.label("")
                boxes["reaction_caption"] = ui.label("").classes("muted")
            with ui.card().classes("panel").style("flex:1 1 340px; min-width:280px;"):
                ui.label("Daily crew log").classes("section-title")
                ui.label(
                    "These four entries write straight to SQLite and recolor the body map."
                ).classes("muted")
                today = latest_row()
                state["mood"] = int(today["mood_score"])
                state["eyes"] = int(today["ocular_headache_flag"])
                state["symptoms"] = int(today["radiation_symptoms"])
                state["pain"] = int(today["back_joint_pain"])

                boxes["mood_caption"] = ui.label(f"Mood — {MOOD_WORDS[state['mood']]}")
                boxes["mood_slider"] = ui.slider(min=1, max=5, step=1, value=state["mood"]).on_value_change(
                    lambda e: (
                        boxes["mood_caption"].set_text(f"Mood — {MOOD_WORDS[int(round(float(e.value)))]}"),
                        persist_symptom("mood_score", int(round(float(e.value))), "mood"),
                    )
                ).props("label-always")

                boxes["eye_caption"] = ui.label(f"Eyes — {EYE_WORDS[state['eyes']]}")
                boxes["eye_toggle"] = ui.toggle(
                    {0: "0 Clear", 1: "1 Mild headache", 2: "2 Blurry / SANS"},
                    value=state["eyes"],
                ).on_value_change(
                    lambda e: (
                        boxes["eye_caption"].set_text(f"Eyes — {EYE_WORDS[int(e.value)]}"),
                        persist_symptom("ocular_headache_flag", int(e.value), "eyes"),
                    )
                )

                boxes["sym_caption"] = ui.label(f"Radiation symptoms — {SYM_WORDS[state['symptoms']]}")
                boxes["sym_slider"] = ui.slider(min=0, max=3, step=1, value=state["symptoms"]).on_value_change(
                    lambda e: (
                        boxes["sym_caption"].set_text(
                            f"Radiation symptoms — {SYM_WORDS[int(round(float(e.value)))]}"
                        ),
                        persist_symptom("radiation_symptoms", int(round(float(e.value))), "symptoms"),
                    )
                )

                boxes["pain_caption"] = ui.label(f"Back / joint pain — {PAIN_WORDS[state['pain']]}")
                boxes["pain_slider"] = ui.slider(min=1, max=5, step=1, value=state["pain"]).on_value_change(
                    lambda e: (
                        boxes["pain_caption"].set_text(
                            f"Back / joint pain — {PAIN_WORDS[int(round(float(e.value)))]}"
                        ),
                        persist_symptom("back_joint_pain", int(round(float(e.value))), "pain"),
                    )
                )
        with ui.card().classes("panel w-full"):
            ui.label("Manual telemetry entry").classes("section-title")
            boxes["day_label"] = ui.label("").style("font-weight:700;")
            ui.label(
                "Type today's readings and press Save. Values overwrite today's row in "
                "mission_health.db and every system is rescored. Saving also ends an active flare drill. "
                "When the day is over, press Start next day to open a new one."
            ).classes("muted")
            with ui.dialog() as next_dialog, ui.card().classes("panel"):
                ui.label("Start the next day?").classes("section-title")
                ui.label(
                    "Today is closed and a new day opens, filled with today's saved readings "
                    "as a starting point. Unsaved edits in the form are discarded, and a "
                    "started day cannot be removed from here."
                ).classes("muted")
                with ui.row().style("gap:10px; margin-top:6px;"):
                    ui.button("Cancel", on_click=next_dialog.close).props("no-caps flat")
                    ui.button("Start next day", on_click=confirm_next_day).props("no-caps")
            boxes["next_dialog"] = next_dialog
            boxes["inputs"] = {}
            with ui.row().classes("w-full").style("gap:10px; flex-wrap:wrap;"):
                for key in TELEMETRY_FIELDS:
                    label, unit, digits = FIELD_LABELS[key]
                    lo, hi, step = INPUT_LIMITS[key]
                    boxes["inputs"][key] = ui.number(
                        label=f"{label} ({unit})",
                        min=lo,
                        max=hi,
                        step=step,
                        format=f"%.{digits}f",
                    ).props("outlined dense").style("width:210px;")
            boxes["notes_input"] = ui.textarea(label="Crew note").props("outlined dense autogrow").classes("w-full")
            with ui.row().style("gap:10px; flex-wrap:wrap; margin-top:6px;"):
                ui.button("Save today's telemetry", on_click=save_manual).props("no-caps")
                ui.button("Calculate dose from flux", on_click=dose_from_flux).props("no-caps outline")
                ui.button("Reset to saved values", on_click=reset_manual_form).props("no-caps flat")
                ui.button("Start next day", on_click=next_dialog.open).props("no-caps outline")
            ui.label(
                "Calculate dose sets cumulative dose to yesterday's total plus 24 hours at the entered flux."
            ).classes("muted").style("font-size:12px;")
        boxes["charts"] = ui.column().classes("w-full")

    refresh()


@ui.page("/")
def index() -> None:
    build_page()


def pick_port(start: int = 8080) -> int:
    for port in range(start, start + 15):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    return start


def main() -> None:
    init_db()
    problems = self_check()
    if "--check" in sys.argv:
        if problems:
            print("SELF-CHECK FAILED")
            for problem in problems:
                print(" -", problem)
            sys.exit(1)
        print("SELF-CHECK OK")
        return
    if problems:
        print("Self-check warnings (the window will still open):")
        for problem in problems:
            print(" -", problem)
    else:
        print("Self-check passed: personal HR delta, SPE coupling, exercise streak.")
    port = int(os.environ.get("ASTROTWIN_PORT", "0") or 0) or pick_port()
    print(f"AstroTwin offline at http://127.0.0.1:{port}")
    ui.run(
        host="127.0.0.1",
        port=port,
        title="AstroTwin — Deep-Space Health & Radiation Monitor",
        reload=False,
        show=os.environ.get("ASTROTWIN_OPEN", "1") != "0",
    )


if __name__ == "__main__":
    main()
