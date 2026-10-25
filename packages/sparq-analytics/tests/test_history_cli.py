import asyncio
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
from fakekalshi import FakeKalshi, Rig, market, trade
from sparq_analytics.history import cli
from sparq_analytics.history.downloader import Downloader
from sparq_analytics.history.rates import RateLimitPersisting
from sparq_core.rest import KalshiError

MakeRig = Callable[..., Rig]


def at(day: int, hour: int = 0) -> datetime:
    return datetime(2026, 10, day, hour, tzinfo=UTC)


def use_fake(monkeypatch: pytest.MonkeyPatch, rig: MakeRig) -> None:
    """Make the command line build a fresh downloader over the fake, as it would over the network.

    Each command closes its clients when it finishes, so every command needs its own.
    """

    def create(config: Any, **kwargs: Any) -> Downloader:
        return rig().downloader

    monkeypatch.setattr(Downloader, "create", staticmethod(create))


def test_network_commands_need_the_production_flag(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["trades"]) == 1
    assert "Add --production" in capsys.readouterr().err


def test_the_help_lists_every_command() -> None:
    text = cli.build_parser().format_help()
    for command in ("plan", "series", "markets", "trades", "candles", "status", "verify"):
        assert command in text


def test_status_and_verify_work_on_an_empty_folder(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["--data-dir", str(tmp_path), "status"]) == 0
    assert "trade_days_done: 0" in capsys.readouterr().out
    assert cli.main(["--data-dir", str(tmp_path), "verify"]) == 0
    assert "volume reconciliation: 0 markets checked" in capsys.readouterr().out


def test_the_rates_come_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPARQ_RATE_LIMIT_PER_SECOND", "7")
    monkeypatch.setenv("SPARQ_LIVE_RATE_LIMIT_PER_SECOND", "2")
    args = cli.build_parser().parse_args(["status"])
    assert (args.trades_rate, args.live_rate) == (7.0, 2.0)
    monkeypatch.delenv("SPARQ_RATE_LIMIT_PER_SECOND")
    monkeypatch.delenv("SPARQ_LIVE_RATE_LIMIT_PER_SECOND")
    args = cli.build_parser().parse_args(["--trades-rate", "4", "status"])
    assert (args.trades_rate, args.live_rate) == (4.0, 3.5)


def test_the_config_puts_the_data_under_a_kalshi_folder(tmp_path: Path) -> None:
    args = cli.build_parser().parse_args(
        ["--data-dir", str(tmp_path), "--max-gb", "2", "--since", "2025-01-01", "status"]
    )
    config = cli._config(args)  # noqa: SLF001
    assert config.data_dir == tmp_path / "kalshi"
    assert config.max_bytes == 2 * 10**9
    assert config.since == date(2025, 1, 1)


async def test_every_network_command_runs_over_the_fake(
    monkeypatch: pytest.MonkeyPatch,
    rig: MakeRig,
    fake: FakeKalshi,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake.trades = [trade(1, "KXA-26OCT21-T0", at(21, 3)), trade(2, "KXMVE-X", at(21, 4))]
    fake.series = [
        {"ticker": "KXA", "fee_type": "quadratic", "fee_multiplier": 1, "category": "Sports"}
    ]
    fake.live_markets = [market("KXA-26OCT21-T0", opened=at(21), settled=at(21, 9), volume=100)]
    fake.candles["KXA-26OCT21-T0"] = []
    use_fake(monkeypatch, rig)
    base = ["--production", "--data-dir", "unused"]

    assert await cli._run_network(cli.build_parser().parse_args([*base, "plan"])) == 0  # noqa: SLF001
    plan = capsys.readouterr().out
    assert "newest complete day 2026-10-21" in plan
    assert "rates: historical trades 10.0/s, others 3.5/s" in plan

    for command in (
        ["series"],
        ["markets"],
        ["trades", "--days", "1"],
        ["candles", "--sample", "1"],
    ):
        assert await cli._run_network(cli.build_parser().parse_args([*base, *command])) == 0  # noqa: SLF001
    out = capsys.readouterr().out
    assert "trades: 1 days downloaded (1 rows, 1 combo trades skipped)" in out
    assert "candles: 1 markets fetched" in out


def test_main_turns_stops_and_errors_into_exit_codes(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fail_with(exc: BaseException) -> Callable[..., Any]:
        def run(*args: Any, **kwargs: Any) -> Any:
            if hasattr(args[0], "close"):
                args[0].close()
            raise exc

        return run

    argv = ["--production", "trades"]
    monkeypatch.setattr(asyncio, "run", fail_with(KeyboardInterrupt()))
    assert cli.main(argv) == 130
    assert "run the same command again to resume" in capsys.readouterr().err
    monkeypatch.setattr(asyncio, "run", fail_with(RateLimitPersisting("6 rate-limit responses")))
    assert cli.main(argv) == 2
    assert "kept rate-limiting" in capsys.readouterr().err
    monkeypatch.setattr(asyncio, "run", fail_with(KalshiError("boom")))
    assert cli.main(argv) == 1
    assert "error: boom" in capsys.readouterr().err
    monkeypatch.setattr(asyncio, "run", lambda coroutine: (coroutine.close(), 0)[1])
    assert cli.main(argv) == 0


async def test_verify_exits_2_on_a_problem(
    tmp_path: Path, rig: MakeRig, fake: FakeKalshi, capsys: pytest.CaptureFixture[str]
) -> None:
    fake.trades = [trade(1, "KXA-26OCT21-T0", at(21, 3), count=100)]
    fake.live_markets = [market("KXA-26OCT21-T0", opened=at(21), settled=at(21, 9), volume=999)]
    r = rig(since=date(2026, 10, 21), data_dir=tmp_path / "kalshi")
    await r.downloader.download_trades()
    await r.downloader.download_live_markets()
    assert cli.main(["--data-dir", str(tmp_path), "verify"]) == 2
    assert "1 mismatched" in capsys.readouterr().out
