"""Load config.toml into plain dicts."""
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    import tomli as tomllib

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PATH = ROOT / "config.toml"


def load(path=DEFAULT_PATH):
    with open(path, "rb") as f:
        return tomllib.load(f)


def resolve(relative):
    """Resolve a path from config relative to the project root."""
    p = Path(relative)
    return p if p.is_absolute() else ROOT / p
