"""Run with the existing project Python: .venv/bin/python solve.py data/."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / 'src'))

if __name__ == '__main__':
    try:
        from akfurs.solver import main
    except ImportError as error:
        print('Required project runtime is unavailable: ' + str(error), file=sys.stderr)
        sys.exit(2)
    sys.exit(main())
