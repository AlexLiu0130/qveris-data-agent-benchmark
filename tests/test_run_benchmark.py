import importlib.util
import pathlib
import subprocess
import sys
import unittest


ROOT = pathlib.Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location("run_benchmark", ROOT / "scripts" / "run_benchmark.py")
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class RunBenchmarkTests(unittest.TestCase):
    def test_explicit_fixture_factory_returns_unknown_usage(self):
        plugin = MODULE._factory(MODULE.FIXTURE)
        result = plugin["client"]("fixture query", request_id="request", idempotency_key="idem")
        self.assertEqual(plugin["variant"]["variant_id"], "fixture-get")
        self.assertNotIn("meta", result.public_response)

    def test_runtime_script_imports_neither_scorer_nor_compiler(self):
        program = (
            "import importlib.util, sys; "
            "p=%r; s=importlib.util.spec_from_file_location('runtime_probe', p); "
            "m=importlib.util.module_from_spec(s); s.loader.exec_module(m); "
            "assert 'qveris_benchmark.benchmark_scorer' not in sys.modules; "
            "assert 'qveris_benchmark.v2_compiler' not in sys.modules"
        ) % str(ROOT / "scripts" / "run_benchmark.py")
        subprocess.run([sys.executable, "-c", program], check=True, capture_output=True, text=True)


if __name__ == "__main__":
    unittest.main()
