from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
ASSETS_DIR = REPO_ROOT / "assets"
TEMPLATES_DIR = ASSETS_DIR / "templates"
EXPORTS_DIR = ASSETS_DIR / "exports"
EXPORTS_DIR.mkdir(parents=True, exist_ok=True)


def get_template_path(template_name: str) -> Path:
    template_path = TEMPLATES_DIR / template_name
    if not template_path.exists():
        raise FileNotFoundError(f"Template not found: {template_path}")
    return template_path


def get_exports_dir() -> Path:
    return EXPORTS_DIR
