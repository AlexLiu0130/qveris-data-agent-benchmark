import pathlib
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / "scripts"))

import qveris_connection_audit as audit


class ConnectionAuditCliTests(unittest.TestCase):
    def test_help_exits_before_every_external_action(self):
        with mock.patch.object(audit, "load_key") as load_key, mock.patch.object(audit, "build_ssl_context") as tls, mock.patch.object(audit, "atomic_write") as write:
            with self.assertRaisesRegex(SystemExit, "0"):
                audit.main(["--help"])
        load_key.assert_not_called()
        tls.assert_not_called()
        write.assert_not_called()

    def test_no_live_refuses_without_read_network_or_write(self):
        with tempfile.TemporaryDirectory() as directory:
            output = pathlib.Path(directory) / "audit.json"
            with mock.patch.object(audit, "load_key") as load_key, mock.patch.object(audit, "build_ssl_context") as tls, mock.patch.object(audit, "atomic_write") as write:
                self.assertEqual(audit.main(["--output", str(output)]), 2)
            self.assertFalse(output.exists())
        load_key.assert_not_called()
        tls.assert_not_called()
        write.assert_not_called()

    def test_missing_output_exits_before_every_external_action(self):
        with mock.patch.object(audit, "load_key") as load_key, mock.patch.object(audit, "build_ssl_context") as tls, mock.patch.object(audit, "atomic_write") as write:
            with self.assertRaisesRegex(SystemExit, "2"):
                audit.main(["--live"])
        load_key.assert_not_called()
        tls.assert_not_called()
        write.assert_not_called()

    def test_live_keeps_search_then_inspect_flow(self):
        with mock.patch.object(audit, "load_key", return_value="test-key"), mock.patch.object(audit, "build_ssl_context", return_value=object()), mock.patch.object(audit.urllib.request, "build_opener", return_value=object()), mock.patch.object(audit, "post", side_effect=[({"results": [{"tool_id": "quote"}]}, {"outcome": "success"}), ({"results": []}, {"outcome": "success"})]) as post, mock.patch.object(audit, "atomic_write") as write:
            self.assertEqual(audit.main(["--live", "--output", "audit.json"]), 0)
        self.assertEqual([call.args[1] for call in post.call_args_list], ["/search", "/tools/by-ids"])
        write.assert_called_once()


if __name__ == "__main__":
    unittest.main()
