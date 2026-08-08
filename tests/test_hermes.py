"""헤르메스 제어 계층 테스트.

거래소나 tkinter 없이 돌아간다. 가짜 백엔드를 써서 제어 로직만 검증한다.

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import os
import sys
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hermes import HermesAgent, RuntimeConfig  # noqa: E402
from hermes.backend import BackendError  # noqa: E402
from hermes.commands import CommandError, dispatch, parse_and_dispatch  # noqa: E402
from hermes.config import ParamError  # noqa: E402
from hermes.control_server import ControlServer  # noqa: E402
from hermes.state import AgentState, InvalidTransition, StateMachine  # noqa: E402


class FakeBackend:
    """TradingBackend 프로토콜을 만족하는 테스트용 가짜 매매 엔진."""

    def __init__(self) -> None:
        self.running = False
        self.started_with: dict | None = None
        self.start_calls = 0
        self.stop_calls = 0
        self.flatten_calls = 0
        self.position = {"side": "none", "size": 0.0, "entry_price": 0.0}
        self.balance: float | None = 1000.0
        self.fail_start = False
        self.fail_flatten = False

    def start(self, config):
        self.start_calls += 1
        if self.fail_start:
            raise BackendError("거래소 연동 실패")
        self.started_with = dict(config)
        self.running = True

    def stop(self):
        self.stop_calls += 1
        self.running = False

    def is_alive(self):
        return self.running

    def get_position(self):
        return dict(self.position)

    def get_balance(self):
        return self.balance

    def flatten(self):
        self.flatten_calls += 1
        if self.fail_flatten:
            raise BackendError("청산 주문 거부됨")
        if self.position["side"] == "none":
            return {"closed": False, "side": "none", "size": 0.0, "detail": "포지션 없음"}
        closed = {"closed": True, "side": self.position["side"], "size": self.position["size"], "detail": "청산됨"}
        self.position = {"side": "none", "size": 0.0, "entry_price": 0.0}
        return closed


def make_agent(**params):
    """고정 금액 모드로 바로 시작 가능한 에이전트를 만든다."""
    backend = FakeBackend()
    defaults = {"amount_mode": "fixed", "fixed_amount_usdt": 50.0, "leverage": 3}
    defaults.update(params)
    agent = HermesAgent(backend, config=RuntimeConfig(**defaults))
    return agent, backend


# ------------------------------------------------------------------ 상태 머신


class StateMachineTest(unittest.TestCase):
    def test_starts_stopped(self):
        self.assertEqual(StateMachine().state, AgentState.STOPPED)

    def test_allowed_transition(self):
        machine = StateMachine()
        machine.to(AgentState.STARTING)
        machine.to(AgentState.RUNNING)
        self.assertEqual(machine.state, AgentState.RUNNING)

    def test_rejects_illegal_transition(self):
        machine = StateMachine()
        # STOPPED에서 곧바로 RUNNING으로는 갈 수 없다.
        with self.assertRaises(InvalidTransition):
            machine.to(AgentState.RUNNING)

    def test_force_ignores_rules(self):
        machine = StateMachine()
        machine.force(AgentState.RUNNING)
        self.assertEqual(machine.state, AgentState.RUNNING)

    def test_on_change_callback(self):
        seen = []
        machine = StateMachine(on_change=lambda old, new: seen.append((old, new)))
        machine.to(AgentState.STARTING)
        self.assertEqual(seen, [(AgentState.STOPPED, AgentState.STARTING)])


# ------------------------------------------------------------------ 설정


class RuntimeConfigTest(unittest.TestCase):
    def test_coerces_string_values(self):
        config = RuntimeConfig()
        self.assertEqual(config.set("leverage", "7"), 7)
        self.assertIs(config.set("entries_enabled", "false"), False)

    def test_rejects_out_of_range(self):
        config = RuntimeConfig()
        for name, value in [("leverage", 0), ("leverage", 200), ("balance_pct", 0),
                            ("balance_pct", 101), ("poll_sec", 1), ("fixed_amount_usdt", -5)]:
            with self.subTest(name=name, value=value):
                with self.assertRaises(ParamError):
                    config.set(name, value)

    def test_rejects_unknown_param(self):
        with self.assertRaises(ParamError):
            RuntimeConfig().set("무엇이든", 1)

    def test_update_is_all_or_nothing(self):
        config = RuntimeConfig(leverage=5)
        with self.assertRaises(ParamError):
            config.update({"leverage": 9, "poll_sec": 99999})
        # 하나가 실패했으므로 레버리지도 그대로여야 한다.
        self.assertEqual(config.get("leverage"), 5)

    def test_validate_for_start_requires_amount(self):
        with self.assertRaises(ParamError):
            RuntimeConfig(amount_mode="fixed").validate_for_start()
        with self.assertRaises(ParamError):
            RuntimeConfig(amount_mode="balance_pct").validate_for_start()
        RuntimeConfig(amount_mode="balance_pct", balance_pct=25).validate_for_start()


# ------------------------------------------------------------------ 에이전트


class AgentLifecycleTest(unittest.TestCase):
    def test_start_runs_backend(self):
        agent, backend = make_agent()
        result = agent.start()
        self.assertTrue(result["ok"])
        self.assertEqual(agent.state, AgentState.RUNNING)
        self.assertTrue(backend.running)
        self.assertEqual(backend.started_with["leverage"], 3)

    def test_double_start_rejected(self):
        agent, backend = make_agent()
        agent.start()
        result = agent.start()
        self.assertFalse(result["ok"])
        self.assertEqual(backend.start_calls, 1)

    def test_start_blocked_without_amount(self):
        backend = FakeBackend()
        agent = HermesAgent(backend, config=RuntimeConfig(amount_mode="fixed"))
        result = agent.start()
        self.assertFalse(result["ok"])
        self.assertEqual(agent.state, AgentState.STOPPED)
        self.assertEqual(backend.start_calls, 0)

    def test_backend_failure_moves_to_error(self):
        agent, backend = make_agent()
        backend.fail_start = True
        result = agent.start()
        self.assertFalse(result["ok"])
        self.assertEqual(agent.state, AgentState.ERROR)
        # ERROR 상태에서는 reset 전까지 다시 시작할 수 없다.
        self.assertFalse(agent.start()["ok"])
        backend.fail_start = False
        self.assertTrue(agent.reset()["ok"])
        self.assertTrue(agent.start()["ok"])

    def test_stop_keeps_position(self):
        agent, backend = make_agent()
        backend.position = {"side": "long", "size": 2.0, "entry_price": 60000.0}
        agent.start()
        result = agent.stop()
        self.assertTrue(result["ok"])
        self.assertEqual(agent.state, AgentState.STOPPED)
        self.assertEqual(backend.flatten_calls, 0)
        self.assertEqual(backend.position["side"], "long")

    def test_stop_when_not_running_rejected(self):
        agent, _ = make_agent()
        self.assertFalse(agent.stop()["ok"])


class AgentPauseTest(unittest.TestCase):
    def test_pause_blocks_entries_only(self):
        agent, backend = make_agent()
        agent.start()
        self.assertTrue(agent.config.get("entries_enabled"))

        result = agent.pause()
        self.assertTrue(result["ok"])
        self.assertEqual(agent.state, AgentState.PAUSED)
        self.assertFalse(agent.config.get("entries_enabled"))
        # 일시정지는 루프를 죽이지 않는다.
        self.assertTrue(backend.running)

    def test_resume_reenables_entries(self):
        agent, _ = make_agent()
        agent.start()
        agent.pause()
        self.assertTrue(agent.resume()["ok"])
        self.assertEqual(agent.state, AgentState.RUNNING)
        self.assertTrue(agent.config.get("entries_enabled"))

    def test_pause_requires_running(self):
        agent, _ = make_agent()
        self.assertFalse(agent.pause()["ok"])

    def test_start_clears_stale_pause(self):
        agent, _ = make_agent()
        agent.start()
        agent.pause()
        agent.stop()
        agent.start()
        # 일시정지 상태로 껐어도 새로 시작하면 정상 매매로 돌아와야 한다.
        self.assertTrue(agent.config.get("entries_enabled"))


class AgentParamTest(unittest.TestCase):
    def test_set_param_while_running(self):
        agent, _ = make_agent()
        agent.start()
        result = agent.set_param("leverage", "10")
        self.assertTrue(result["ok"])
        self.assertEqual(agent.config.get("leverage"), 10)

    def test_set_param_rejects_bad_value(self):
        agent, _ = make_agent()
        result = agent.set_param("leverage", "999")
        self.assertFalse(result["ok"])
        self.assertEqual(agent.config.get("leverage"), 3)

    def test_set_params_atomic(self):
        agent, _ = make_agent()
        result = agent.set_params({"leverage": 8, "poll_sec": 0})
        self.assertFalse(result["ok"])
        self.assertEqual(agent.config.get("leverage"), 3)


class AgentKillSwitchTest(unittest.TestCase):
    def test_kill_flattens_and_locks(self):
        agent, backend = make_agent()
        backend.position = {"side": "long", "size": 2.0, "entry_price": 60000.0}
        agent.start()

        result = agent.kill_switch(reason="테스트")
        self.assertTrue(result["ok"])
        self.assertEqual(agent.state, AgentState.STOPPED)
        self.assertEqual(backend.flatten_calls, 1)
        self.assertFalse(backend.running)
        self.assertEqual(backend.position["side"], "none")

        # 잠금이 걸려 있으므로 reset 전에는 다시 시작할 수 없다.
        self.assertFalse(agent.start()["ok"])
        self.assertTrue(agent.reset()["ok"])
        self.assertTrue(agent.start()["ok"])

    def test_kill_works_from_any_state(self):
        agent, backend = make_agent()
        # 시작도 안 한 상태에서 눌러도 예외 없이 처리돼야 한다.
        result = agent.kill_switch(reason="패닉")
        self.assertTrue(result["ok"])
        self.assertEqual(backend.stop_calls, 1)

    def test_kill_still_stops_when_flatten_fails(self):
        agent, backend = make_agent()
        backend.position = {"side": "short", "size": 1.0, "entry_price": 60000.0}
        backend.fail_flatten = True
        agent.start()

        result = agent.kill_switch()
        # 청산은 실패했지만 매매 정지는 반드시 이뤄져야 한다.
        self.assertFalse(result["ok"])
        self.assertFalse(backend.running)
        self.assertEqual(agent.state, AgentState.ERROR)
        self.assertIn("청산 실패", result["message"])

    def test_kill_disables_entries_before_flatten(self):
        agent, _ = make_agent()
        agent.start()
        agent.kill_switch()
        self.assertFalse(agent.config.get("entries_enabled"))


class AgentStatusTest(unittest.TestCase):
    def test_status_includes_market_data(self):
        agent, backend = make_agent()
        backend.position = {"side": "long", "size": 1.5, "entry_price": 59000.0}
        agent.start()
        status = agent.status()
        self.assertEqual(status["state"], "running")
        self.assertEqual(status["position"]["side"], "long")
        self.assertEqual(status["balance_usdt"], 1000.0)

    def test_status_survives_exchange_errors(self):
        agent, backend = make_agent()

        def boom():
            raise RuntimeError("네트워크 끊김")

        backend.get_position = boom
        status = agent.status()
        # 거래소 조회가 실패해도 상태 확인 자체는 성공해야 한다.
        self.assertIsNone(status["position"])
        self.assertIn("네트워크 끊김", status["position_error"])

    def test_status_local_skips_market(self):
        agent, _ = make_agent()
        status = agent.status(include_market=False)
        self.assertNotIn("position", status)

    def test_audit_log_records_commands(self):
        agent, _ = make_agent()
        agent.start()
        agent.set_param("leverage", 9)
        entries = agent.audit_log()
        self.assertEqual([e["command"] for e in entries], ["start", "set_param"])


# ------------------------------------------------------------------ 명령 해석


class CommandTest(unittest.TestCase):
    def test_dispatch_start_and_status(self):
        agent, _ = make_agent()
        self.assertTrue(dispatch(agent, "start")["ok"])
        self.assertEqual(dispatch(agent, "status")["status"]["state"], "running")

    def test_unknown_command(self):
        agent, _ = make_agent()
        with self.assertRaises(CommandError):
            dispatch(agent, "자폭")

    def test_kill_requires_confirmation(self):
        agent, backend = make_agent()
        agent.start()
        with self.assertRaises(CommandError):
            dispatch(agent, "kill")
        self.assertEqual(backend.flatten_calls, 0)

        result = dispatch(agent, "kill", ["CONFIRM", "장", "급락"])
        self.assertTrue(result["ok"])
        self.assertEqual(result["reason"], "장 급락")

    def test_set_requires_two_args(self):
        agent, _ = make_agent()
        with self.assertRaises(CommandError):
            dispatch(agent, "set", ["leverage"])

    def test_parse_line(self):
        agent, _ = make_agent()
        result = parse_and_dispatch(agent, "/set leverage 12")
        self.assertTrue(result["ok"])
        self.assertEqual(agent.config.get("leverage"), 12)

    def test_no_arg_commands_reject_args(self):
        agent, _ = make_agent()
        with self.assertRaises(CommandError):
            dispatch(agent, "start", ["지금"])


# ------------------------------------------------------------------ HTTP API


class ControlServerTest(unittest.TestCase):
    def setUp(self):
        self.agent, self.backend = make_agent()
        # port=0으로 임의의 빈 포트를 받아 테스트끼리 충돌하지 않게 한다.
        self.server = ControlServer(self.agent, port=0, token="hermes-test-token")
        self.url = self.server.start()
        self.addCleanup(self.server.stop)

    def _request(self, path, method="GET", body=None, token="hermes-test-token"):
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(self.url + path, data=data, method=method)
        if token is not None:
            request.add_header("Authorization", f"Bearer {token}")
        if data is not None:
            request.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read().decode("utf-8"))

    def test_health_is_open(self):
        status, payload = self._request("/health", token=None)
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])

    def test_status_requires_token(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self._request("/status", token=None)
        self.assertEqual(ctx.exception.code, 401)

    def test_wrong_token_rejected(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self._request("/status", token="wrong-token")
        self.assertEqual(ctx.exception.code, 401)

    def test_status_endpoint(self):
        status, payload = self._request("/status")
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"]["state"], "stopped")

    def test_command_endpoint_starts_bot(self):
        status, payload = self._request("/command", "POST", {"command": "start"})
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertTrue(self.backend.running)

    def test_command_set_param(self):
        _, payload = self._request("/command", "POST", {"command": "set", "args": ["leverage", 15]})
        self.assertTrue(payload["ok"])
        self.assertEqual(self.agent.config.get("leverage"), 15)

    def test_rejected_command_returns_200_with_ok_false(self):
        # 명령이 거부된 것과 요청이 잘못된 것은 다르다.
        _, payload = self._request("/command", "POST", {"command": "stop"})
        self.assertFalse(payload["ok"])

    def test_bad_command_returns_400(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self._request("/command", "POST", {"command": "없는명령"})
        self.assertEqual(ctx.exception.code, 400)

    def test_missing_command_field(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self._request("/command", "POST", {})
        self.assertEqual(ctx.exception.code, 400)

    def test_unknown_path(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self._request("/no-such-path")
        self.assertEqual(ctx.exception.code, 404)

    def test_remote_bind_blocked_by_default(self):
        with self.assertRaises(ValueError):
            ControlServer(self.agent, host="0.0.0.0", port=0, token="x")


# ------------------------------------------------------------------ 어댑터


class StubEntry:
    """tkinter Entry/Spinbox 흉내."""

    def __init__(self, value=""):
        self.value = str(value)

    def get(self):
        return self.value

    def delete(self, start, end=None):
        self.value = ""

    def insert(self, index, text):
        self.value = str(text)

    def config(self, **kwargs):
        pass


class StubVar:
    def __init__(self, value=""):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class StubRoot:
    """after()를 즉시 실행하는 tk.Tk 흉내 (테스트를 단일 스레드로 유지)."""

    def after(self, delay, func=None, *args):
        if func is not None:
            func(*args)


class StubExchange:
    def __init__(self, price=60000.0):
        self.price = price

    def fetch_ticker(self, symbol):
        return {"last": self.price}


class StubBot:
    """어댑터가 기대하는 최소한의 봇 표면만 흉내 낸 객체."""

    def __init__(self):
        self.root = StubRoot()
        self.symbol = "BTC/USDT:USDT"
        self.is_running = False
        self.trade_thread = None
        self.exchange = StubExchange()
        self.runtime_config = None

        self.var_amount_mode = StubVar("fixed")
        self.entry_amount = StubEntry()
        self.entry_balance_pct = StubEntry()
        self.spin_leverage = StubEntry("1")

        self.position = {"side": "none", "size": 0.0, "entry_price": 0.0}
        self.orders = []
        self.trades = []
        self.start_should_fail = False
        self.order_should_fail = False

    def update_amount_mode_ui(self):
        pass

    def log(self, message):
        pass

    def start_bot(self):
        # 실제 봇처럼 실패해도 예외를 던지지 않고 is_running만 False로 남긴다.
        self.is_running = not self.start_should_fail

    def stop_bot(self):
        self.is_running = False

    def get_current_position(self):
        return dict(self.position)

    def fetch_futures_balance(self):
        return 2500.0

    def execute_market_order(self, side, amount, reduce_only=False):
        if self.order_should_fail:
            return None
        self.orders.append({"side": side, "amount": amount, "reduce_only": reduce_only})
        return {"id": "stub-order"}

    def record_trade(self, side, entry_price, exit_price, reason, position_size=0.0):
        self.trades.append({"side": side, "reason": reason, "size": position_size})


class AdapterTest(unittest.TestCase):
    def setUp(self):
        from hermes.adapter import TkBotBackend

        self.bot = StubBot()
        self.config = RuntimeConfig(amount_mode="fixed", fixed_amount_usdt=100.0, leverage=7)
        self.backend = TkBotBackend(self.bot, config=self.config)

    def test_injects_runtime_config(self):
        # 매매 루프가 설정을 읽어갈 수 있어야 한다.
        self.assertIs(self.bot.runtime_config, self.config)

    def test_start_writes_config_to_widgets(self):
        self.backend.start(self.config.snapshot())
        self.assertTrue(self.bot.is_running)
        self.assertEqual(self.bot.spin_leverage.get(), "7")
        self.assertEqual(self.bot.entry_amount.get(), "100.0")
        self.assertEqual(self.bot.var_amount_mode.get(), "fixed")

    def test_start_failure_raises(self):
        self.bot.start_should_fail = True
        with self.assertRaises(BackendError):
            self.backend.start(self.config.snapshot())

    def test_flatten_closes_long_with_reduce_only(self):
        self.bot.position = {"side": "long", "size": 3.0, "entry_price": 59000.0}
        result = self.backend.flatten()
        self.assertTrue(result["closed"])
        self.assertEqual(self.bot.orders, [{"side": "sell", "amount": 3.0, "reduce_only": True}])
        self.assertEqual(self.bot.trades[0]["reason"], "긴급청산(킬스위치)")

    def test_flatten_closes_short_with_buy(self):
        self.bot.position = {"side": "short", "size": 1.0, "entry_price": 61000.0}
        self.backend.flatten()
        self.assertEqual(self.bot.orders[0]["side"], "buy")

    def test_flatten_without_position(self):
        result = self.backend.flatten()
        self.assertFalse(result["closed"])
        self.assertEqual(self.bot.orders, [])

    def test_flatten_raises_when_order_rejected(self):
        self.bot.position = {"side": "long", "size": 3.0, "entry_price": 59000.0}
        self.bot.order_should_fail = True
        with self.assertRaises(BackendError):
            self.backend.flatten()

    def test_requires_exchange(self):
        self.bot.exchange = None
        with self.assertRaises(BackendError):
            self.backend.get_position()

    def test_agent_kill_switch_through_adapter(self):
        # 에이전트 → 어댑터 → 봇까지 실제로 이어지는지 확인한다.
        agent = HermesAgent(self.backend, config=self.config)
        agent.start()
        self.bot.position = {"side": "long", "size": 2.0, "entry_price": 59000.0}

        result = agent.kill_switch(reason="통합테스트")
        self.assertTrue(result["ok"])
        self.assertFalse(self.bot.is_running)
        self.assertEqual(self.bot.orders[0]["reduce_only"], True)


if __name__ == "__main__":
    unittest.main()
