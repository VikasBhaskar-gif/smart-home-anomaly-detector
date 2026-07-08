"""
Data Logger
-----------
Subscribes to all home/# MQTT topics and writes every
reading to a SQLite database for ML training later.

Usage:
  python data_logger.py
  python data_logger.py --db ../ml/sensor_data.db   (custom location)
  python data_logger.py --reset                      (wipe and start fresh)
"""

import argparse
import json
import logging
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

import paho.mqtt.client as mqtt

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

BROKER_HOST = "localhost"
BROKER_PORT = 1883
DEFAULT_DB  = "sensor_data.db"


# ── Database setup ─────────────────────────────────────────────────────────────
def init_db(db_path: str) -> sqlite3.Connection:
    """
    Create the database and the readings table if they don't exist yet.
    We use check_same_thread=False so the MQTT callback (which runs on
    a background thread) can safely write to the same connection.
    """
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS readings (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp        TEXT    NOT NULL,
            room             TEXT    NOT NULL,
            sensor           TEXT    NOT NULL,
            value            REAL    NOT NULL,
            unit             TEXT,
            anomaly_injected INTEGER NOT NULL DEFAULT 0
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_room_sensor ON readings (room, sensor)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_timestamp   ON readings (timestamp)")
    conn.commit()
    log.info("Database ready: %s", db_path)
    return conn


# ── MQTT callbacks ─────────────────────────────────────────────────────────────
def on_connect(client, userdata, flags, rc):
    if rc == 0:
        client.subscribe("home/#")   # subscribe to ALL sensor topics
        log.info("Connected to broker — subscribed to home/#")
    else:
        log.error("Connection failed (rc=%d)", rc)


def on_message(client, userdata, msg):
    conn = userdata["conn"]
    stats = userdata["stats"]

    try:
        data = json.loads(msg.payload.decode())
    except json.JSONDecodeError:
        log.warning("Bad JSON on topic %s — skipping", msg.topic)
        return

    # Pull fields out of the payload
    timestamp        = data.get("timestamp", datetime.now(timezone.utc).isoformat())
    room             = data.get("room",    "unknown")
    sensor           = data.get("sensor",  "unknown")
    value            = data.get("value",   0.0)
    unit             = data.get("unit",    "")
    anomaly_injected = int(data.get("anomaly_injected", False))

    # Write to database
    conn.execute("""
        INSERT INTO readings (timestamp, room, sensor, value, unit, anomaly_injected)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (timestamp, room, sensor, value, unit, anomaly_injected))
    conn.commit()

    # Update in-memory stats for the status print
    stats["total"] += 1
    if anomaly_injected:
        stats["anomalies"] += 1

    # Print a status line every 50 rows so the terminal doesn't flood
    if stats["total"] % 50 == 0:
        log.info(
            "Saved %d readings  (%d anomalies)",
            stats["total"], stats["anomalies"]
        )


# ── Main ───────────────────────────────────────────────────────────────────────
def run(args):
    # Wipe the database if --reset was passed
    if args.reset and Path(args.db).exists():
        Path(args.db).unlink()
        log.info("Database reset — old data deleted.")

    conn  = init_db(args.db)
    stats = {"total": 0, "anomalies": 0}

    client = mqtt.Client(
        client_id="data-logger",
        userdata={"conn": conn, "stats": stats},
    )
    client.on_connect = on_connect
    client.on_message = on_message

    log.info("Connecting to broker at %s:%d ...", BROKER_HOST, BROKER_PORT)
    try:
        client.connect(BROKER_HOST, BROKER_PORT, keepalive=60)
    except ConnectionRefusedError:
        log.error("Could not reach broker. Is Docker running? (cd broker → docker compose up -d)")
        return

    log.info("Logging to: %s", Path(args.db).resolve())
    log.info("Press Ctrl+C to stop.\n")

    try:
        client.loop_forever()
    except KeyboardInterrupt:
        pass
    finally:
        client.disconnect()
        conn.close()
        log.info("Stopped. Total saved: %d readings (%d anomalies).", stats["total"], stats["anomalies"])
        print_summary(args.db)


# ── Summary ────────────────────────────────────────────────────────────────────
def print_summary(db_path: str):
    """Print a quick breakdown of what's in the database."""
    conn = sqlite3.connect(db_path)
    total    = conn.execute("SELECT COUNT(*) FROM readings").fetchone()[0]
    anomalies= conn.execute("SELECT COUNT(*) FROM readings WHERE anomaly_injected=1").fetchone()[0]
    rooms    = conn.execute("SELECT DISTINCT room FROM readings").fetchall()
    sensors  = conn.execute("SELECT DISTINCT sensor FROM readings").fetchall()

    print("\n── Database summary ──────────────────────────")
    print(f"  Total readings : {total}")
    print(f"  Anomalies      : {anomalies}  ({100*anomalies/total:.1f}% of total)" if total else "  No data yet.")
    print(f"  Rooms          : {', '.join(r[0] for r in rooms)}")
    print(f"  Sensors        : {', '.join(s[0] for s in sensors)}")
    print("──────────────────────────────────────────────\n")
    conn.close()


# ── CLI ────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    p = argparse.ArgumentParser(description="MQTT data logger → SQLite")
    p.add_argument("--db",    default=DEFAULT_DB, help="Path to SQLite database file")
    p.add_argument("--reset", action="store_true", help="Delete existing DB and start fresh")
    run(p.parse_args())