import importlib.util
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
import uuid


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

    def test_sandbox_command_has_no_network_or_host_mounts(self):
        command = MODULE.DockerSandboxClient._command("fixture:latest", pathlib.Path("/tmp/cid"))
        self.assertIn("--network", command)
        self.assertEqual(command[command.index("--network") + 1], "none")
        self.assertIn("--read-only", command)
        self.assertIn("--user", command)
        self.assertEqual(command[command.index("--user") + 1], "65534:65534")
        self.assertNotIn("--mount", command)
        self.assertNotIn("--volume", command)
        self.assertNotIn("-v", command)

    def test_sandbox_cleanup_force_removes_only_container_id(self):
        with tempfile.TemporaryDirectory() as directory:
            cidfile = pathlib.Path(directory) / "cid"
            cidfile.write_text("a" * 64, encoding="ascii")
            with patch.object(MODULE.subprocess, "run") as run:
                MODULE.DockerSandboxClient._cleanup(None, cidfile)
            run.assert_called_once_with(["docker", "rm", "--force", "a" * 64], stdin=MODULE.subprocess.DEVNULL, stdout=MODULE.subprocess.DEVNULL, stderr=MODULE.subprocess.DEVNULL, check=False)

    def test_runtime_image_stage_excludes_benchmark_and_oracle(self):
        config = {
            "schema_version": "sandbox-get-runtime-config/v1", "model": "fixture-model",
            "agent_variant_id": "fixture-agent", "agent_version": "fixture-v1",
            "get_variant_id": "fixture-get", "get_version": "fixture-v1",
            "model_version": "fixture-v1", "model_config_digest": "f" * 64,
        }
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            config_path, staged = root / "runtime.json", root / "staged"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            subprocess.run([sys.executable, str(ROOT / "scripts" / "stage_sandbox_image.py"), "--output-dir", str(staged), "--runtime-config", str(config_path)], check=True, capture_output=True, text=True)
            names = {path.relative_to(staged).as_posix() for path in staged.rglob("*")}
            self.assertIn("qveris_benchmark/sandbox_get_entry.py", names)
            self.assertNotIn("benchmarks", names)
            self.assertFalse(any("oracle" in name for name in names))

    @unittest.skipUnless(shutil.which("docker"), "Docker is unavailable")
    def test_staged_runtime_image_builds_without_repository_context(self):
        config = {
            "schema_version": "sandbox-get-runtime-config/v1", "model": "fixture-model",
            "agent_variant_id": "fixture-agent", "agent_version": "fixture-v1",
            "get_variant_id": "fixture-get", "get_version": "fixture-v1",
            "model_version": "fixture-v1", "model_config_digest": "f" * 64,
        }
        tag = "qveris-benchmark-sandbox-runtime-test-" + uuid.uuid4().hex
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = pathlib.Path(directory)
                config_path, staged = root / "runtime.json", root / "staged"
                iidfile = root / "image-id"
                config_path.write_text(json.dumps(config), encoding="utf-8")
                subprocess.run([sys.executable, str(ROOT / "scripts" / "stage_sandbox_image.py"), "--output-dir", str(staged), "--runtime-config", str(config_path)], check=True, capture_output=True, text=True)
                subprocess.run(["docker", "build", "--network", "none", "--iidfile", str(iidfile), "--tag", tag, str(staged)], check=True, capture_output=True, text=True)
                variant = {"variant_id": "fixture-get", "stable_display_order": 1, **{name: config[name] for name in ("agent_variant_id", "agent_version", "get_variant_id", "get_version", "model_version", "model_config_digest")}, "model_identifier": config["model"]}
                self.assertEqual(MODULE.DockerSandboxClient(image=iidfile.read_text(encoding="ascii").strip(), variant=variant, timeout_ms=1_000).image, iidfile.read_text(encoding="ascii").strip())
        finally:
            subprocess.run(["docker", "image", "rm", "--force", tag], check=False, capture_output=True, text=True)

    @unittest.skipUnless(shutil.which("docker"), "Docker is unavailable")
    def test_docker_fixture_denies_network_and_oracle_mounts(self):
        tag = "qveris-benchmark-sandbox-fixture-test-" + uuid.uuid4().hex
        fixture = ROOT / "runner" / "sandbox-fixture"
        try:
            with tempfile.NamedTemporaryFile() as iidfile:
                subprocess.run(["docker", "build", "--network", "none", "--iidfile", iidfile.name, "--tag", tag, str(fixture)], check=True, capture_output=True, text=True)
                image = pathlib.Path(iidfile.name).read_text(encoding="ascii").strip()
            variant = MODULE._sandbox_descriptor(fixture / "variant.v1.json")
            result = MODULE.DockerSandboxClient(image=image, variant=variant, timeout_ms=5_000)("fixture query", request_id="attempt-fixture", idempotency_key="ignored")
            self.assertEqual(result.public_response["terminal_reason"], "semantic_fixture_offline")
            self.assertEqual(result.execution_evidence.tools_used, ())
            self.assertEqual(result.execution_evidence.assurance, "host_observed_sandbox")
        finally:
            subprocess.run(["docker", "image", "rm", "--force", tag], check=False, capture_output=True, text=True)


if __name__ == "__main__":
    unittest.main()
