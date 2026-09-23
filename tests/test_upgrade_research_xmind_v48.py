import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from tools.upgrade_research_xmind_v48 import PATCH_TITLE, upgrade, v48_sheet


class UpgradeResearchXmindV48Tests(unittest.TestCase):
    def test_emits_xmind_26_creator_object_and_preserves_sheet_topology(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "v47.xmind"
            destination = root / "v48.xmind"
            source_sheet = {
                "id": "source-sheet",
                "revisionId": "source-revision",
                "class": "sheet",
                "title": "V4.7｜新资产扫链",
                "rootTopic": {
                    "id": "source-root",
                    "class": "topic",
                    "title": "V4.7｜新资产扫链",
                    "children": {"attached": []},
                },
            }
            with zipfile.ZipFile(source, "w") as archive:
                archive.writestr("content.json", json.dumps([source_sheet], ensure_ascii=False))
                archive.writestr(
                    "metadata.json",
                    json.dumps(
                        {
                            "dataStructureVersion": "2",
                            "creator": "OpenAI",
                            "layoutEngineVersion": "4",
                            "modified-by": "legacy generator",
                        }
                    ),
                )
                archive.writestr("manifest.json", json.dumps({"file-entries": {}}))
                archive.writestr("content.xml", "<xmap-content />")

            upgrade(source, destination)

            with zipfile.ZipFile(destination) as archive:
                self.assertIsNone(archive.testzip())
                sheets = json.loads(archive.read("content.json"))
                metadata = json.loads(archive.read("metadata.json"))

            self.assertEqual(1, len(sheets))
            self.assertEqual(
                {"name": "Vana", "version": "26.01.07153"},
                metadata["creator"],
            )
            self.assertEqual(
                {"dataStructureVersion", "creator", "layoutEngineVersion"},
                set(metadata),
            )
            self.assertTrue(sheets[0]["title"].startswith("V4.8｜新资产扫链"))
            titles = [
                child["title"]
                for child in sheets[0]["rootTopic"]["children"]["attached"]
            ]
            self.assertEqual(1, titles.count(PATCH_TITLE))
            self.assertEqual(1, titles.count(v48_sheet()["rootTopic"]["title"]))


if __name__ == "__main__":
    unittest.main()
