import importlib.util
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "generate.py"
spec = importlib.util.spec_from_file_location("generate", SCRIPT)
generate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(generate)


class GenerateTest(unittest.TestCase):
    def test_full_ota_is_exposed_and_filtered_by_real_build_timestamp(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            package = root / "update.zip"
            with zipfile.ZipFile(package, "w") as archive:
                archive.writestr(
                    "META-INF/com/android/metadata",
                    "ota-type=BLOCK\npre-device=P22H190\npost-build=EEBBK/lineage_P22H190/P22H190:14/ABCD/123:user/test-keys\npost-timestamp=1789777202\n",
                )
            name = "lineage-21.0-20260919-UNOFFICIAL-P22H190.zip"
            update = generate.parse_ota(package, name, "https://github.com/owner/repo/releases/download/v1/" + name, package.stat().st_size)
            self.assertEqual(update["datetime"], 1789777202)
            self.assertEqual(update["version"], "21.0")
            generate.build([(update, {"body": "修复 <sensor>"})], root / "site")
            manifest = json.loads((root / "site/updates/P22H190/UNOFFICIAL.json").read_text())
            self.assertEqual(manifest["response"], [update])
            page = (root / "site/index.html").read_text()
            self.assertIn("修复 &lt;sensor&gt;", page)
            self.assertNotIn("<sensor>", page)

    def test_mismatched_device_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            package = Path(temp) / "update.zip"
            with zipfile.ZipFile(package, "w") as archive:
                archive.writestr("META-INF/com/android/metadata", "ota-type=BLOCK\npre-device=other\n")
            with self.assertRaisesRegex(ValueError, "pre-device"):
                generate.parse_ota(package, "lineage-21.0-20260919-UNOFFICIAL-P22H190.zip", "https://example.com", package.stat().st_size)

    def test_unpublished_releases_are_not_listed(self):
        name = "lineage-21.0-20260919-UNOFFICIAL-P22H190.zip"
        release = {
            "draft": False,
            "prerelease": False,
            "assets": [{"name": name, "size": 42, "browser_download_url": "https://github.com/owner/repo/releases/download/v1/" + name}],
        }
        update = {"datetime": 1789777202, "filename": name, "id": "a" * 64,
                  "romtype": "UNOFFICIAL", "size": 42,
                  "url": release["assets"][0]["browser_download_url"], "version": "21.0"}
        with patch.object(generate, "github_json", side_effect=[[dict(release, draft=True), dict(release, prerelease=True), release], []]), \
                patch.object(generate, "download") as download, \
                patch.object(generate, "parse_ota", return_value=update) as parse:
            result = generate.collect("owner/repo", "token")
        self.assertEqual(len(result), 1)
        download.assert_called_once()
        parse.assert_called_once()
        original_url = release["assets"][0]["browser_download_url"]
        self.assertEqual(download.call_args.args[0], original_url)
        self.assertEqual(result[0][0]["url"], "https://v4.gh-proxy.org/" + original_url)
        with tempfile.TemporaryDirectory() as temp:
            generate.build(result, Path(temp))
            manifest = json.loads((Path(temp) / "updates/P22H190/UNOFFICIAL.json").read_text())
            self.assertEqual(manifest["response"][0]["url"], result[0][0]["url"])
            self.assertIn(result[0][0]["url"], (Path(temp) / "index.html").read_text())


if __name__ == "__main__":
    unittest.main()
