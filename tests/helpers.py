import shutil
import tomllib
from pathlib import Path

from harness.config import Config, parse

REPO = Path(__file__).resolve().parent.parent


def example_raw() -> dict:
    return tomllib.loads((REPO / "me.example.toml").read_text())


def link_templates(home: Path) -> None:
    """home/templates -> the repo's templates. Windows without Developer Mode can't
    symlink, so copy there."""
    try:
        (home / "templates").symlink_to(REPO / "templates")
    except OSError:
        shutil.copytree(REPO / "templates", home / "templates")


def make_cfg(tmp: Path) -> Config:
    raw = example_raw()
    raw["zones"]["private"]["vault"] = str(tmp / "private")
    raw["zones"]["public"]["vault"] = str(tmp / "public")
    (tmp / "private").mkdir(exist_ok=True)
    (tmp / "public").mkdir(exist_ok=True)
    return parse(raw, REPO)
