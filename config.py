"""Configuration loader for Cabby taxi booking agent."""

import os
import sys
from dotenv import load_dotenv

load_dotenv()

# SignalWire
SIGNALWIRE_PROJECT_ID = os.getenv("SIGNALWIRE_PROJECT_ID", "")
SIGNALWIRE_TOKEN = os.getenv("SIGNALWIRE_TOKEN", "")
SIGNALWIRE_SPACE = os.getenv("SIGNALWIRE_SPACE", "")
SIGNALWIRE_PHONE_NUMBER = os.getenv("SIGNALWIRE_PHONE_NUMBER", "")
DISPLAY_PHONE_NUMBER = os.getenv("DISPLAY_PHONE_NUMBER", "")
SWML_BASIC_AUTH_USER = os.getenv("SWML_BASIC_AUTH_USER", "")
SWML_BASIC_AUTH_PASSWORD = os.getenv("SWML_BASIC_AUTH_PASSWORD", "")
SWML_PROXY_URL_BASE = os.getenv("SWML_PROXY_URL_BASE", "")

# Google Maps
GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY", "")

# Fare Model
BASE_FARE = float(os.getenv("BASE_FARE", "3.00"))
PER_MILE_RATE = float(os.getenv("PER_MILE_RATE", "2.40"))
PER_MINUTE_RATE = float(os.getenv("PER_MINUTE_RATE", "0.30"))
MINIMUM_FARE = float(os.getenv("MINIMUM_FARE", "8.00"))
MAX_DISTANCE_MILES = float(os.getenv("MAX_DISTANCE_MILES", "100"))

# AI Model
AI_MODEL = os.getenv("AI_MODEL", "4o-mini")
AI_TOP_P = float(os.getenv("AI_TOP_P", "0.5"))
AI_TEMPERATURE = float(os.getenv("AI_TEMPERATURE", "0.5"))

# Server
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "3000"))
DATABASE_PATH = os.getenv("DATABASE_PATH", "cabby.db")


def validate():
    """Validate required configuration is present."""
    missing = []
    if not GOOGLE_MAPS_API_KEY:
        missing.append("GOOGLE_MAPS_API_KEY")
    if not SIGNALWIRE_PHONE_NUMBER:
        missing.append("SIGNALWIRE_PHONE_NUMBER")
    if missing:
        print(f"WARNING: Missing config: {', '.join(missing)}")
        print("Some features may not work. Copy .env.example to .env and fill in values.")
