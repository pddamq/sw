import os
from pathlib import Path
import tempfile

# Isolate generated runtime files and temporary environments from source and user data.
ROOT = Path(__file__).resolve().parents[1]
TEMP = ROOT / 'build' / 'test-temp'
TEMP.mkdir(parents=True, exist_ok=True)
os.environ.setdefault('FORMA_DATA_DIR', str(ROOT / 'build' / 'test-runtime'))
os.environ['TEMP'] = os.environ['TMP'] = str(TEMP)
tempfile.tempdir = str(TEMP)
