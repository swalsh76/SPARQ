"""``sparq-admin alert-test``: send one test message through the real path.

It starts the test unit, which runs the watchdog with ``--test`` as the service account with the
stored topic, so it proves the credential, the network and the phone together. It needs root
(it starts a unit) and refuses while the topic is not stored.
"""

from sparq_server import names
from sparq_server.admin import managed
from sparq_server.admin.answers import DatabaseHost
from sparq_server.admin.system import CommandFailed, Host


class AlertTestRefused(Exception):
    """The test cannot be run; the message says why."""


def run_alert_test(host: Host, config: DatabaseHost) -> str:
    """Send the test message; returns a line to print, or raises ``AlertTestRefused``."""
    if not config.alerts.enabled:
        raise AlertTestRefused("alerts are not enabled in the answer file")
    if not host.is_root():
        raise AlertTestRefused("alert-test must run as root (use sudo)")
    if not host.exists(managed.credential_file(names.ALERT_CREDENTIAL)):
        raise AlertTestRefused("the alert topic is not stored yet; see the server guide, step 14")
    if not host.exists(f"{managed.SYSTEMD_DIRECTORY}/{managed.ALERT_TEST_SERVICE}"):
        raise AlertTestRefused("the test unit is not installed; run install first")
    try:
        host.run_checked(["systemctl", "start", managed.ALERT_TEST_SERVICE], timeout=90.0)
    except CommandFailed:
        raise AlertTestRefused(
            "the test message was not delivered; see: "
            f"journalctl -u {managed.ALERT_TEST_SERVICE.removesuffix('.service')} -n 20"
        ) from None
    return f"test message sent through {config.alerts.server}: check your phone"
