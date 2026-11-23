import os
import sys
from pathlib import Path

import tempfile

os.environ["DEMO_MODE"] = "1"  # tests never call the real API
os.environ["BID_OUTPUT_DIR"] = tempfile.mkdtemp(prefix="bidtests-")  # keep test bids out of output/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
