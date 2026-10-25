import sys

from sparq_server.ingest.service import main

sys.exit(main(sys.argv[1:] or None))
