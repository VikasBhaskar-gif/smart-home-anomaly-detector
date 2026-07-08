from flask import Flask, jsonify, request
from flask_socketio import SocketIO
from flask_cors import CORS
import sqlite3
import pickle
import pandas as pd
import numpy as np
import paho.mqtt.client as mqtt
import threading
import json
from datetime import datetime

app = Flask(__name__)
CORS(app)
app.config['SECRET_KEY'] = 'smarthomedashboard'
socketio = SocketIO(app, cors_allowed_origins="*")

DB_PATH = '../simulator/sensor_data.db'
MODEL_PATH = '../ml/anomaly_model.pkl'

with open(MODEL_PATH, 'rb') as f:
    model = pickle.load(f)

print("Model loaded successfully")

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

@app.route('/api/readings')
def get_readings():
    limit = request.args.get('limit', 100, type=int)
    conn = get_db()
    rows = conn.execute(
        f'SELECT * FROM readings ORDER BY timestamp DESC LIMIT {limit}'
    ).fetchall()
    conn.close()
    return jsonify([dict(row) for row in rows])

@app.route('/api/anomalies')
def get_anomalies():
    conn = get_db()
    rows = conn.execute(
        'SELECT * FROM readings WHERE anomaly_injected = 1 ORDER BY timestamp DESC LIMIT 50'
    ).fetchall()
    conn.close()
    return jsonify([dict(row) for row in rows])

@app.route('/api/stats')
def get_stats():
    conn = get_db()
    total = conn.execute('SELECT COUNT(*) FROM readings').fetchone()[0]
    anomalies = conn.execute(
        'SELECT COUNT(*) FROM readings WHERE anomaly_injected = 1'
    ).fetchone()[0]
    conn.close()
    return jsonify({
        'total_readings': total,
        'total_anomalies': anomalies,
        'anomaly_rate': round(anomalies / total * 100, 2)
    })

# ── NEW: endpoint so the frontend can discover rooms dynamically ──────────────
@app.route('/api/rooms')
def get_rooms():
    conn = get_db()
    rows = conn.execute('SELECT DISTINCT room FROM readings ORDER BY room').fetchall()
    conn.close()
    return jsonify([r['room'] for r in rows])

def on_message(client, userdata, msg):
    try:
        payload = json.loads(msg.payload.decode())
        topic_parts = msg.topic.split('/')
        # topic pattern:  home/<room>/<sensor>   e.g. home/living_room/temperature
        #            or:  home/<room>/sensors     e.g. home/esp32_room/sensors  (ESP32)

        # ── Format 1: simulator  (home/living_room/temperature) ──────────────
        # Sensor type comes from the last part of the topic.
        # Payload: {"room": "living_room", "value": 23.5, "timestamp": ...,
        #           "anomaly_injected": 0}
        if topic_parts[-1] in ('temperature', 'humidity', 'motion', 'power'):
            sensor = topic_parts[-1]
            is_anomaly = int(payload.get('anomaly_injected', 0))

            reading = {
                'timestamp':        payload.get('timestamp'),
                'room':             payload.get('room'),
                'sensor':           sensor,
                'value':            payload.get('value'),
                'ml_anomaly':       is_anomaly,
                'anomaly_injected': is_anomaly,
            }
            socketio.emit('sensor_reading', reading, namespace='/')
            if is_anomaly:
                socketio.emit('anomaly_alert', reading, namespace='/')

        # ── Format 2: ESP32  (home/esp32_room/sensors) ───────────────────────
        # Sensor types come from keys inside the payload.
        # Payload: {"room": "esp32_room", "temperature": 26.3, "humidity": 58.1,
        #           "uptime_s": 120}
        elif topic_parts[-1] == 'sensors':
            room      = payload.get('room') or topic_parts[1]
            timestamp = datetime.now().timestamp()   # ESP32 has no RTC

            for sensor in ('temperature', 'humidity'):
                if sensor not in payload:
                    continue

                value = float(payload[sensor])

                # We have no anomaly_injected flag from real hardware —
                # default to 0.  You can wire in your ML model here later.
                reading = {
                    'timestamp':        timestamp,
                    'room':             room,
                    'sensor':           sensor,
                    'value':            value,
                    'ml_anomaly':       0,
                    'anomaly_injected': 0,
                }
                socketio.emit('sensor_reading', reading, namespace='/')

        else:
            return   # unrecognised topic shape — ignore silently

    except Exception as e:
        print(f"MQTT error: {e}")

def start_mqtt():
    try:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1)
        client.on_message = on_message
        client.on_connect = lambda c, u, f, rc: print(f"MQTT connected with code {rc}")
        client.on_disconnect = lambda c, u, rc: print(f"MQTT disconnected with code {rc}")
        client.connect("localhost", 1883, 60)
        client.subscribe("home/#")
        print("MQTT thread started, waiting for messages...")
        client.loop_forever()
    except Exception as e:
        print(f"MQTT thread error: {e}")

mqtt_thread = threading.Thread(target=start_mqtt, daemon=True)
mqtt_thread.start()

if __name__ == '__main__':
    socketio.run(app, debug=True, port=5000, use_reloader=False)