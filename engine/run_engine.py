"""Start the Klipon engine (the Premiere panel starts this automatically)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from clipgenie.server import main  # noqa: E402

if __name__ == "__main__":
    port = int(os.environ.get("KLIPON_PORT") or 0)   # 2.35.5: a second copy runs on its own port
    main(port) if port else main()
