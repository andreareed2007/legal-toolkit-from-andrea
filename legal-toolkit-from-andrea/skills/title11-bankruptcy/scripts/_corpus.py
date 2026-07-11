"""Shared loader for the Title 11 corpus bundled with this skill."""
import json, os

_DATA = os.path.join(os.path.dirname(__file__), "..", "assets", "data")
CANVAS_URL = "https://rlfordon.github.io/bankruptcy-canvas/"

def load_sections():
    with open(os.path.join(_DATA, "title11_sections.json"), encoding="utf-8") as f:
        return json.load(f)

def load_terms():
    with open(os.path.join(_DATA, "title11_terms.json"), encoding="utf-8") as f:
        return json.load(f)

def canvas_hint(section=None):
    if section:
        return f"Visual canvas: {CANVAS_URL} (search \"{section}\" in the top bar to open the card)."
    return f"Visual canvas: {CANVAS_URL}"
