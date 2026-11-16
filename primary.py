"""
The following code was generated with Super-Intelligence.
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

def _region(sid: str, d: str, levels: dict, selected: str) -> str:
    meta = next(item for item in SYSTEMS if item["id"] == sid)
    fill = COLORS[levels[sid]]
    stroke = "#9BE7FF" if selected == sid else "#071018"
    width = 5 if selected == sid else 2
    title = xml_text(f"{meta['title']} — {LEVEL_NAME[levels[sid]]}")
    return (
        f'<path data-region="{sid}" d="{d}" fill="{fill}" stroke="{stroke}" '
        f'stroke-width="{width}" stroke-linejoin="round">'
        f"<title>{title}</title></path>"
    )


def body_svg(levels: dict, selected: str, flux: float) -> str:
    """Anatomical silhouette. Four filled regions carry data-region ids.

    Clicks are handled on the wrapper; the browser reports which region was hit.
    A halo around the figure pulses while flux is in the storm bands.
    """
    if flux > 500:
        klass = "body-svg spe"
    elif flux > 100:
        klass = "body-svg storm"
    else:
        klass = "body-svg"

    arms_l = "M72,136 C36,156 22,210 30,300 C36,324 56,320 62,294 L70,230 C74,190 80,160 96,142 Z"
    arms_r = "M168,136 C204,156 218,210 210,300 C204,324 184,320 178,294 L170,230 C166,190 160,160 144,142 Z"
    legs = (
        "M78,330 C74,380 68,450 62,520 L96,520 L104,400 L120,370 L136,400 "
        "L144,520 L178,520 C172,450 166,380 162,330 C150,348 90,348 78,330 Z"
    )
    chest = "M76,128 C76,118 164,118 164,128 L172,210 C172,228 120,236 120,236 C120,236 68,228 68,210 Z"
    abdomen = "M74,228 L166,228 L174,318 C174,340 120,348 120,348 C120,348 66,340 66,318 Z"
    neuro_fill, neuro_stroke, neuro_w, neuro_title = (
        COLORS[levels["neurobehavioral"]],
        "#9BE7FF" if selected == "neurobehavioral" else "#071018",
        5 if selected == "neurobehavioral" else 2,
        xml_text(f"Neurobehavioral & Ocular — {LEVEL_NAME[levels['neurobehavioral']]}"),
    )
    # Neck shares the cardiovascular color; the head is the neuro region.
    neck = COLORS[levels["cardiovascular"]]
    return f"""
<svg class="{klass}" viewBox="0 0 240 560" width="100%" height="auto"
     xmlns="http://www.w3.org/2000/svg" role="img" aria-label="Crew body map">
  <ellipse class="halo" cx="120" cy="280" rx="112" ry="250" fill="none" stroke-width="4"
           pointer-events="none"/>
  {_region("musculoskeletal", arms_l, levels, selected)}
  {_region("musculoskeletal", arms_r, levels, selected)}
  {_region("musculoskeletal", legs, levels, selected)}
  {_region("immune", abdomen, levels, selected)}
  {_region("cardiovascular", chest, levels, selected)}
  <rect data-region="cardiovascular" x="106" y="108" width="28" height="24" rx="6"
        fill="{neck}" stroke="#071018" stroke-width="2">
    <title>Cardiovascular — neck vessels</title>
  </rect>
  <ellipse data-region="neurobehavioral" cx="120" cy="70" rx="38" ry="44"
           fill="{neuro_fill}" stroke="{neuro_stroke}" stroke-width="{neuro_w}">
    <title>{neuro_title}</title>
  </ellipse>
  <ellipse cx="106" cy="68" rx="4.5" ry="4.5" fill="#071018" pointer-events="none"/>
  <ellipse cx="134" cy="68" rx="4.5" ry="4.5" fill="#071018" pointer-events="none"/>
  <path d="M108,88 Q120,96 132,88" fill="none" stroke="#071018" stroke-width="2"
        pointer-events="none"/>
  <path d="M104,162 l8,6 l-8,14 l16,-8 l8,10 l-6,-16 l12,-2 Z" fill="#071018" opacity="0.55"
        pointer-events="none"/>
  <text x="120" y="74" text-anchor="middle" font-size="11" font-weight="700" fill="#102033"
        pointer-events="none" font-family="Segoe UI, Arial, sans-serif">NEURO</text>
  <text x="120" y="188" text-anchor="middle" font-size="11" font-weight="700" fill="#102033"
        pointer-events="none" font-family="Segoe UI, Arial, sans-serif">HEART</text>
  <text x="120" y="286" text-anchor="middle" font-size="11" font-weight="700" fill="#102033"
        pointer-events="none" font-family="Segoe UI, Arial, sans-serif">IMMUNE</text>
  <text x="120" y="460" text-anchor="middle" font-size="11" font-weight="700" fill="#102033"
        pointer-events="none" font-family="Segoe UI, Arial, sans-serif">MSK</text>
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

    def toggle_spe() -> None:
        if state["suspend"]:
            return
        set_spe(state_get("spe_active", "0") != "1")
        refresh()

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
        spe_on = state_get("spe_active", "0") == "1"
        boxes["header"].clear()
        with boxes["header"]:
            with ui.column().classes("w-full").style("gap:6px"):
                ui.label("ASTROTWIN  ·  DEEP-SPACE HEALTH").classes("kicker")
                ui.html(
                    "<h1 class='app-title'>Deep-Space Health &amp; Radiation Monitor</h1>",
                    sanitize=False,
                )
                ui.label(
                    f"Sol {report['sol']} — Mars Transit"
                ).style("font-size:20px; font-weight:700;")
                ui.label(
                    f"Medical log day {report['day']} of {N_DAYS}  ·  {pretty_date(report['date'])}  ·  "
                    "synthetic subject ASTRO-1"
                ).classes("muted")
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
            with ui.row().classes("items-center").style("gap:12px; margin-top:12px; flex-wrap:wrap;"):
                ui.button(
                    "Simulate Solar Particle Event (Flare)",
                    on_click=toggle_spe,
                ).classes("flare-btn on" if spe_on else "flare-btn")
                if spe_on:
                    ui.label("DRILL ACTIVE — flux locked at 650 µSv/hr. Click again to restore background.").style(
                        "color:#FF8A96; font-weight:700;"
                    )
                else:
                    ui.label("Drill off. Background flux is the stored cruise value.").classes("muted")
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
                ui.label("Click a region. Color is that system’s status right now.").classes("muted")
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
            ui.label("30-day trajectories").classes("section-title").style("margin-top:8px;")
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
            ui.label(
                "Type today's readings and press Save. Values overwrite today's row in "
                "mission_health.db and every system is rescored. Saving also ends an active flare drill."
            ).classes("muted")
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
