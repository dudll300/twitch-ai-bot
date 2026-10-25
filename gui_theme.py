"""Compatibility exports for the shared semantic studio design system."""
from studio_theme import STYLE, PALETTES, apply_theme

COLORS = {"background": PALETTES["dark"]["bg"], "surface": PALETTES["dark"]["surface"],
          "text": PALETTES["dark"]["text"], "muted": PALETTES["dark"]["muted"],
          "accent": PALETTES["dark"]["accent"], "border": PALETTES["dark"]["border"],
          "chrome": PALETTES["dark"]["surface"], "selected": PALETTES["dark"]["selected"],
          "success": PALETTES["dark"]["success"]}
