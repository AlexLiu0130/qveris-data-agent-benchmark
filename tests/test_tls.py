import os
from pathlib import Path
import ssl
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.request import Request

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from qveris_benchmark import tls


class TLSResolutionTests(unittest.TestCase):
    def test_clear_environment_uses_first_existing_system_bundle(self):
        with tempfile.TemporaryDirectory() as directory:
            bundle = Path(directory) / "system.pem"
            bundle.write_text("placeholder", encoding="utf-8")
            with patch.dict(os.environ, {}, clear=True), patch.object(tls, "SYSTEM_CA_BUNDLE_PATHS", (str(bundle),)):
                self.assertEqual(tls.resolve_ca_file(ca_file=None, environment_ca_file="GATEWAY_CA_BUNDLE"), str(bundle))

    def test_explicit_and_specialized_environment_precede_ssl_cert_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            explicit, specialized, generic, system = (root / name for name in ("explicit.pem", "specialized.pem", "generic.pem", "system.pem"))
            for bundle in (explicit, specialized, generic, system):
                bundle.write_text("placeholder", encoding="utf-8")
            environment = {"GATEWAY_CA_BUNDLE": str(specialized), "SSL_CERT_FILE": str(generic)}
            with patch.dict(os.environ, environment, clear=True), patch.object(tls, "SYSTEM_CA_BUNDLE_PATHS", (str(system),)):
                self.assertEqual(tls.resolve_ca_file(ca_file=str(explicit), environment_ca_file="GATEWAY_CA_BUNDLE"), str(explicit))
                self.assertEqual(tls.resolve_ca_file(ca_file=None, environment_ca_file="GATEWAY_CA_BUNDLE"), str(specialized))

    def test_direct_https_opener_uses_verified_context_and_direct_open_protocol(self):
        context = ssl.create_default_context()
        opener = Mock()
        with patch.object(tls, "verified_ssl_context", return_value=context) as verified, patch.object(tls, "build_opener", return_value=opener) as build:
            transport = tls.DirectHTTPSOpener(ssl_context=None, ca_file="/tmp/ca.pem", environment_ca_file="GATEWAY_CA_BUNDLE")
            request = Request("https://example.test")
            self.assertIs(transport(request, 3.0), opener.open.return_value)
        verified.assert_called_once_with(ssl_context=None, ca_file="/tmp/ca.pem", environment_ca_file="GATEWAY_CA_BUNDLE")
        self.assertEqual(build.call_args.args[0].proxies, {})
        self.assertIs(build.call_args.args[1]._context, context)
        opener.open.assert_called_once_with(request, timeout=3.0)


if __name__ == "__main__":
    unittest.main()
