import json
import random
import time
import argparse
from datetime import datetime, timezone
import paho.mqtt.client as mqtt

# --- Configuration ---
BROKER_HOST = "localhost"
BROKER_PORT = 1883

# Normal operating ranges for each sensor
SENSOR_RANGES = {
    "temperature": {"min": 18.0, "max": 28.0, "unit": "C"},
    "humidity":    {"min": 30.0, "max": 70.0, "unit": "%"},
    "power":       {"min": 50.0, "max": 500.0, "unit": "W"},
}

# Anomaly ranges - deliberately outside normal so ML can learn the difference
ANOMALY_RANGES = {
    "temperature": {"min": 40.0, "max": 60.0},
    "humidity":    {"min": 85.0, "max": 99.0},
    "power":       {"min": 1500.0, "max": 3000.0},
}

def generate_reading(sensor, inject_anomaly=False):
    """
    Generate a single sensor reading.
    Normal readings drift gradually. Anomaly readings jump out of range.
    """
    if sensor == "motion":
        if inject_anomaly:
            return 1  # anomaly = motion detected constantly
        return random.choices([0, 1], weights=[90, 10])[0]  # 90% no motion

    ranges = ANOMALY_RANGES[sensor] if inject_anomaly else SENSOR_RANGES[sensor]
    return round(random.uniform(ranges["min"], ranges["max"]), 2)

def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print("Connected to MQTT broker")
    else:
        print(f"Connection failed with code {rc}")

def simulate(rooms, interval, inject_anomaly):
    # Create and connect the MQTT client
    client = mqtt.Client(client_id="sensor-simulator")
    client.on_connect = on_connect
    
    print(f"Connecting to broker at {BROKER_HOST}:{BROKER_PORT}...")
    client.connect(BROKER_HOST, BROKER_PORT, keepalive=60)
    client.loop_start()
    
    time.sleep(3)  # give connection time to establish
    
    print(f"Simulating {len(rooms)} room(s): {', '.join(rooms)}")
    print(f"Anomaly injection: {'ON' if inject_anomaly else 'OFF'}")
    print("Publishing sensor data... (Ctrl+C to stop)\n")

    sensors = ["temperature", "humidity", "motion", "power"]
    tick = 0

    try:
        while True:
            tick += 1
            for room in rooms:
                for sensor in sensors:
                    # 5% chance of anomaly per reading when flag is on
                    is_anomaly = inject_anomaly and (random.random() < 0.05)
                    value = generate_reading(sensor, inject_anomaly=is_anomaly)

                    payload = json.dumps({
                        "room": room,
                        "sensor": sensor,
                        "value": value,
                        "unit": SENSOR_RANGES.get(sensor, {}).get("unit", "bool"),
                        "anomaly_injected": is_anomaly,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    })

                    topic = f"home/{room}/{sensor}"
                    client.publish(topic, payload, qos=1)

                    if is_anomaly:
                        print(f"ANOMALY  {topic}: {value}")
                    elif tick % 5 == 0:  # only print every 5 ticks to keep terminal clean
                        print(f"  {topic}: {value}")

            time.sleep(interval)

    except KeyboardInterrupt:
        print(f"\nStopped after {tick} ticks.")
        client.loop_stop()
        client.disconnect()

# --- Entry point ---
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Smart home sensor simulator")
    parser.add_argument("--rooms",    nargs="+", default=["living_room", "bedroom", "kitchen"])
    parser.add_argument("--interval", type=float, default=1.0, help="Seconds between readings")
    parser.add_argument("--anomaly",  action="store_true",     help="Randomly inject anomalies")
    args = parser.parse_args()

    simulate(args.rooms, args.interval, args.anomaly)