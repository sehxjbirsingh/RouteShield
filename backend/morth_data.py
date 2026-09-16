import csv
from pathlib import Path

DATA_FILE = (
    Path(__file__).resolve().parent.parent
    / "data"
    / "morth_accidents.csv"
)

MORTH_DATA = {}


def load_morth_data():
    global MORTH_DATA

    if not DATA_FILE.exists():
        raise FileNotFoundError(
            f"MoRTH dataset not found: {DATA_FILE}"
        )

    with open(DATA_FILE, "r", encoding="utf-8-sig") as file:
        reader = csv.DictReader(file)

        for row in reader:
            state = row.get("State/UT")

            if not state:
                continue

            MORTH_DATA[state.strip()] = row

    return MORTH_DATA


def get_state_accident_data(state):
    if not MORTH_DATA:
        load_morth_data()

    return MORTH_DATA.get(state)


def calculate_historical_risk(state):
    data = get_state_accident_data(state)

    if not data:
        return 0

    try:
        accidents = float(
            data.get("Total Number of Road Accidents", 0) or 0
        )
    except ValueError:
        accidents = 0

    return accidents