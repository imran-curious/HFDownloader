from __future__ import annotations

import sys
from pathlib import Path
from tkinter import messagebox

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

try:
    from hfdownloader.gui import main
except Exception as exc:
    messagebox.showerror("HFDownloader", f"Unable to start the GUI.\n\n{exc}")
    raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
