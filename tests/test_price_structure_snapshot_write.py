import ast
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, call


class PriceStructureSnapshotWriteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Importing server reads runtime state; load only the write functions.
        source = Path(__file__).resolve().parents[1].joinpath("server.py")
        tree = ast.parse(source.read_text(encoding="utf-8"))
        functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                     and node.name in {"write_json_cache", "write_price_structure_snapshot"}]
        cls.code = compile(ast.Module(body=functions, type_ignores=[]), "server.py", "exec")

    def setUp(self):
        # All filesystem operations are simulated; no runtime caches are touched.
        self.target = Mock()
        self.target.name = "price_structure_snapshot.json"
        self.files = {"destination": "old snapshot"}
        self.streams = []
        self.paths = {}
        self.replace_error = None
        self.write_error = None
        self.cleanup_error = None
        self.factory = Mock(side_effect=self.make_temp)
        self.sleep = Mock()
        self.ns = dict(Any=object, Path=lambda name: self.paths[name], json=json,
                       tempfile=SimpleNamespace(NamedTemporaryFile=self.factory),
                       time=SimpleNamespace(sleep=self.sleep),
                       MONITOR_BUY=SimpleNamespace(identities=Mock()),
                       PRICE_STRUCTURE_SNAPSHOT_PATH=self.target)
        exec(self.code, self.ns)
        self.write = self.ns["write_json_cache"]
        self.payload = {"cacheKey": "test", "savedAt": 123,
                        "payload": {"items": [{"symbol": "CLO", "name": "结构"}]}}

    def make_temp(self, **kwargs):
        name = f"snapshot-{len(self.streams)}.tmp"
        stream = MagicMock()
        stream.name = name
        stream.__enter__.return_value = stream
        self.files[name] = ""
        closed = []
        stream.__exit__.side_effect = lambda *args: closed.append(True) or False

        def write(text):
            self.files[name] = text
            if self.write_error:
                raise self.write_error

        def replace(target):
            self.assertTrue(closed, "Windows requires the temporary handle to be closed")
            self.assertIs(target, self.target)
            self.assertEqual(json.loads(self.files[name]), self.payload)
            if self.replace_error:
                self.replace_error()
            self.files["destination"] = self.files.pop(name)

        def unlink(**kwargs):
            if self.cleanup_error:
                raise self.cleanup_error
            self.files.pop(name, None)

        stream.write.side_effect = write
        self.paths[name] = Mock(replace=Mock(side_effect=replace), unlink=Mock(side_effect=unlink))
        self.streams.append(stream)
        return stream

    @staticmethod
    def denied():
        error = PermissionError("snapshot is occupied")
        error.winerror = 5
        return error

    def test_success_preserves_format_and_uses_unique_same_directory_temp(self):
        self.write(self.target, self.payload)
        self.write(self.target, self.payload)
        self.assertEqual(self.files, {"destination": json.dumps(self.payload, ensure_ascii=False, indent=2)})
        self.assertEqual(len(self.paths), 2)
        self.factory.assert_called_with(mode="w", encoding="utf-8", dir=self.target.parent,
                                        prefix="price_structure_snapshot.json.", suffix=".tmp", delete=False)
        self.sleep.assert_not_called()

    def test_access_denied_retries_then_succeeds(self):
        def occupied():
            self.assertEqual(self.files["destination"], "old snapshot")
            raise self.denied()
        attempts = []
        def transient():
            attempts.append(1)
            if len(attempts) < 3:
                occupied()
        self.replace_error = transient
        self.write(self.target, self.payload)
        self.assertEqual(len(attempts), 3)
        self.assertEqual(self.sleep.call_args_list, [call(0.05), call(0.1)])
        self.assertEqual(json.loads(self.files["destination"]), self.payload)
        self.assertEqual(len(self.files), 1)

    def test_retry_exhaustion_preserves_old_snapshot_and_cleans_temp(self):
        error = self.denied()
        self.replace_error = Mock(side_effect=error)
        with self.assertRaises(PermissionError) as caught:
            self.write(self.target, self.payload)
        self.assertIs(caught.exception, error)
        self.assertEqual(self.replace_error.call_count, 4)
        self.assertEqual(self.sleep.call_count, 3)
        self.assertEqual(self.files, {"destination": "old snapshot"})

    def test_other_os_errors_are_not_retried(self):
        for error in (OSError("disk failure"), PermissionError("non-Windows denial")):
            with self.subTest(error=error):
                self.replace_error = Mock(side_effect=error)
                with self.assertRaises(OSError) as caught:
                    self.write(self.target, self.payload)
                self.assertIs(caught.exception, error)
                self.replace_error.assert_called_once()
                self.assertEqual(self.files, {"destination": "old snapshot"})
        self.sleep.assert_not_called()

    def test_partial_write_failure_cleans_temp_without_replacing(self):
        self.write_error = OSError("disk full")
        with self.assertRaises(OSError):
            self.write(self.target, self.payload)
        self.assertEqual(self.files, {"destination": "old snapshot"})
        for path in self.paths.values():
            path.replace.assert_not_called()
        self.sleep.assert_not_called()

    def test_cleanup_failure_does_not_mask_original_error(self):
        original = OSError("replace failed")
        self.replace_error = Mock(side_effect=original)
        self.cleanup_error = self.denied()
        with self.assertRaises(OSError) as caught:
            self.write(self.target, self.payload)
        self.assertIs(caught.exception, original)
        self.assertEqual(self.files["destination"], "old snapshot")

    def test_serialization_failure_creates_no_temp(self):
        with self.assertRaises(TypeError):
            self.write(self.target, {"invalid": object()})
        self.factory.assert_not_called()
        self.assertEqual(self.files, {"destination": "old snapshot"})

    def test_other_cache_paths_keep_existing_write_behavior(self):
        other = Mock()
        other.name = "other.json"
        self.write(other, self.payload)
        other.with_name.assert_called_once_with("other.json.tmp")
        other.with_name.return_value.write_text.assert_called_once_with(
            json.dumps(self.payload, ensure_ascii=False, indent=2), encoding="utf-8")
        other.with_name.return_value.replace.assert_called_once_with(other)
        self.factory.assert_not_called()


if __name__ == "__main__":
    unittest.main()
