"""Run from any working directory: python3 /path/to/agent-harbor/run.py."""
import importlib.util
import sys

missing = [name for name in ("cryptography", "reportlab", "pypdf", "jsonschema") if importlib.util.find_spec(name) is None]
if missing:
    print("Eksik bileşenler: " + ", ".join(missing))
    print("Proje klasöründe Başlat.command dosyasını çalıştır; gerekli bileşenleri kurar.")
    sys.exit(1)

from app.server import main

if __name__ == "__main__":
    main()
