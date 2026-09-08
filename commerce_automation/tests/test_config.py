import os
import tempfile
import unittest
from pathlib import Path

from autocommerce import config as configmod


class TestEnvExpansion(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "s.yaml"

    def tearDown(self):
        for k in ("AC_TEST_KEY", "AC_TEST_ABSENT"):
            os.environ.pop(k, None)
        self.dir.cleanup()

    def write(self, text):
        self.path.write_text(text, encoding="utf-8")
        return configmod.load(self.path, dotenv=self.dir.name + "/.no-env")

    def test_env_reference_substituted(self):
        os.environ["AC_TEST_KEY"] = "real-secret"
        s = self.write('markets:\n  naver:\n    client_id: "${AC_TEST_KEY}"\n')
        self.assertEqual(s.get("markets.naver.client_id"), "real-secret")

    def test_missing_env_becomes_empty_not_literal(self):
        s = self.write('markets:\n  naver:\n    client_id: "${AC_TEST_ABSENT}"\n')
        self.assertEqual(s.get("markets.naver.client_id"), "")

    def test_default_syntax(self):
        s = self.write('runtime:\n  log_level: "${AC_TEST_ABSENT:-DEBUG}"\n')
        self.assertEqual(s.get("runtime.log_level"), "DEBUG")

    def test_secrets_never_committed_as_literals(self):
        """설정 예시 파일에 실제 비밀값이 하드코딩돼 있으면 안 된다."""
        example = Path(__file__).resolve().parents[1] / "config/settings.example.yaml"
        text = example.read_text(encoding="utf-8")
        for key in ("client_secret", "secret_key", "api_token"):
            for line in text.splitlines():
                if line.strip().startswith(f"{key}:"):
                    value = line.split(":", 1)[1].strip().strip('"')
                    self.assertTrue(
                        value.startswith("${"),
                        f"{key} 에 리터럴 값이 들어 있습니다: {line}")


class TestDefaultsAndAccess(unittest.TestCase):
    def test_defaults_applied_when_key_absent(self):
        s = configmod.Settings(raw=dict(configmod.DEFAULTS))
        self.assertTrue(s.dry_run)
        self.assertEqual(s.get("crawler.rate_per_sec"), 0.5)

    def test_deep_merge_keeps_untouched_defaults(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "s.yaml"
            p.write_text("runtime:\n  dry_run: false\n", encoding="utf-8")
            s = configmod.load(p, dotenv=d + "/.no-env")
        self.assertFalse(s.dry_run)
        self.assertEqual(s.get("runtime.concurrency"), 4)   # 기본값 보존

    def test_require_raises_on_empty(self):
        s = configmod.Settings(raw={"a": {"b": ""}})
        with self.assertRaises(configmod.ConfigError):
            s.require("a.b")

    def test_missing_file_raises(self):
        with self.assertRaises(configmod.ConfigError):
            configmod.load("/nonexistent/path.yaml")

    def test_credentials_ready_reports_missing(self):
        s = configmod.Settings(raw={"markets": {"naver": {
            "client_id": "x", "client_secret": ""}}})
        ready, missing = s.credentials_ready("naver")
        self.assertFalse(ready)
        self.assertEqual(missing, ["client_secret"])


class TestDotenv(unittest.TestCase):
    def test_existing_env_wins_over_dotenv(self):
        with tempfile.TemporaryDirectory() as d:
            env = Path(d) / ".env"
            env.write_text('AC_TEST_KEY="from-file"\n', encoding="utf-8")
            os.environ["AC_TEST_KEY"] = "from-shell"
            try:
                configmod.load_dotenv(env)
                self.assertEqual(os.environ["AC_TEST_KEY"], "from-shell")
            finally:
                os.environ.pop("AC_TEST_KEY", None)

    def test_dotenv_fills_when_unset(self):
        with tempfile.TemporaryDirectory() as d:
            env = Path(d) / ".env"
            env.write_text("# comment\nAC_TEST_KEY='from-file'\n\n", encoding="utf-8")
            os.environ.pop("AC_TEST_KEY", None)
            try:
                configmod.load_dotenv(env)
                self.assertEqual(os.environ["AC_TEST_KEY"], "from-file")
            finally:
                os.environ.pop("AC_TEST_KEY", None)


if __name__ == "__main__":
    unittest.main()
