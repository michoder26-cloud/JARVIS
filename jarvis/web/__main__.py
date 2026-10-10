"""Allow ``python -m jarvis.web`` to start the web server."""
import sys

from jarvis.main import main

sys.exit(main(["--web", *sys.argv[1:]]))
