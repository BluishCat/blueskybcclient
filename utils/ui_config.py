import json
import os

from .paths import get_app_dir

CONFIG_FILE = os.path.join(get_app_dir(), "ui_config.json")

def save_ui_state(state):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=4)
    except Exception as e:
        print(f"Error saving UI state: {e}")

def load_ui_state():
    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        print(f"Error loading UI state: {e}")
    return None
