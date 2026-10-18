import os

# Must be set before the app (and its Settings) is imported by any test module.
os.environ.setdefault("PLANT_SIM_ENABLED", "false")
