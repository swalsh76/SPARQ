"""Names that more than one program must agree on, written once.

``sparq-admin install`` writes the units and settings files, the ingest and the watchdog read them,
and ``verify`` and ``ingest-status`` look for them, each from its own process. A name changed in one
place and not another would break the pair silently (a watchdog looking at the wrong unit sees a
dead ingest as healthy), so each one lives here. The keys of the ingest's stored state are in
:mod:`sparq_server.ingest.state` and the gap kinds and statuses in :mod:`sparq_server.ingest.gaps`.
"""

INGEST_UNIT = "sparq-ingest.service"
INGEST_CONFIG = "/etc/sparq/ingest.toml"
ALERTS_CONFIG = "/etc/sparq/alerts.toml"

# systemd credentials: the names a unit loads them under and the program reads them by
KEY_ID_CREDENTIAL = "kalshi-key-id"
KEY_CREDENTIAL = "kalshi-private-key"
INGEST_CREDENTIALS = (KEY_ID_CREDENTIAL, KEY_CREDENTIAL)
ALERT_CREDENTIAL = "ntfy-topic"

# exit statuses the ingest unit does not restart on (RestartPreventExitStatus)
EXIT_DISK_FULL = 75  # EX_TEMPFAIL: the data drive is past the stop level
EXIT_REFUSED = 78  # EX_CONFIG: a prerequisite is wrong; restarting would not help
