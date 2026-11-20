import sys
from pathlib import Path

# Make the repository root importable so `from src...` works under plain pytest.
sys.path.insert(0, str(Path(__file__).resolve().parent))
