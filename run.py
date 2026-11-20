"""Entry point: ``python run.py <command>``. See ``python run.py --help``.

It only makes the package under ``src/`` importable and hands over to
``momo.cli``, so nothing needs to be installed with pip first.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from momo.cli import main  # noqa: E402

if __name__ == "__main__":
    main()
