"""
Streamlit entry point. This file is referenced by Streamlit Cloud's
"main file path" setting. It just imports the real app.
"""

import sys
from pathlib import Path

# Add project root to path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Now import the real app
from frontend.app import *  # noqa: F401, F403
