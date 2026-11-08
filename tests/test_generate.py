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


VANILLA = "lineage-21.0-20260919-UNOFFICIAL-P22H190.zip"
GAPPS = "lineage-21.0-20260919-UNOFFICIAL-gapps-P22H190.zip"
METADATA = (
    "ota-type=BLOCK\npre-device=P22H190\n"
    "post-build=EEBBK/lineage_P22H190/P22H190:14/ABCD/123:user/test-keys\n"
    "post-timestamp=1789777202\n"
)


class GenerateTest(unittest.TestCase):
    def make_package(self, root, metadata=METADATA):
        package = root / "update.zip"
        with zipfile.ZipFile(package, "w") as archive:
            archive.writestr("META-INF/com/android/metadata", metadata)
        return package

    def test_full_ota_is_exposed_and_filtered_by_real_build_timestamp(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            package = self.make_package(root)
            update = generate.parse_ota(package, VANILLA, "https://github.com/owner/repo/releases/download/v1/" + VANILLA, package.stat().st_size)
            self.assertEqual(update["datetime"], 1789777202)
            self.assertEqual(update["version"], "21.0")
            self.assertEqual(update["romtype"], "UNOFFICIAL")
            generate.build({"UNOFFICIAL": [(update, {"body": "修复 <sensor>"})]}, root / "site")
            manifest = json.loads((root / "site/updates/P22H190/UNOFFICIAL.json").read_text())
            self.assertEqual(manifest["response"], [update])
            self.assertTrue((root / "site/updates/P22H190/UNOFFICIAL-gapps.json").exists())
            page = (root / "site/index.html").read_text()
            self.assertIn("修复 &lt;sensor&gt;", page)
            self.assertNotIn("<sensor>", page)

    def test_gapps_ota_keeps_romtype_and_own_version(self):
        with tempfile.TemporaryDirectory() as temp:
            package = self.make_package(Path(temp))
            update = generate.parse_ota(package, GAPPS, "https://example.com/" + GAPPS, package.stat().st_size)
            self.assertEqual(update["filename"], GAPPS)
            self.assertEqual(update["version"], "21.0")
            self.assertEqual(update["romtype"], "UNOFFICIAL")
            self.assertEqual(generate.classify(VANILLA), "UNOFFICIAL")
            self.assertEqual(generate.classify(GAPPS), "UNOFFICIAL-gapps")
            self.assertIsNone(generate.classify("lineage-21.0-20260919-UNOFFICIAL-gapps-OTHER.zip"))

    def test_mismatched_device_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            package = self.make_package(Path(temp), "ota-type=BLOCK\npre-device=other\n")
            with self.assertRaisesRegex(ValueError, "pre-device"):
                generate.parse_ota(package, VANILLA, "https://example.com", package.stat().st_size)

    def test_unknown_filename_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            package = self.make_package(Path(temp))
            with self.assertRaisesRegex(ValueError, "OTA filename must be one of"):
                generate.parse_ota(package, "lineage-21.0-20260919-UNOFFICIAL-P22H190-extra.zip", "https://example.com", package.stat().st_size)

    def test_unpublished_releases_are_not_listed(self):
        release = {
            "draft": False,
            "prerelease": False,
            "assets": [{"name": VANILLA, "size": 42, "browser_download_url": "https://github.com/owner/repo/releases/download/v1/" + VANILLA}],
        }
        update = {"datetime": 1789777202, "filename": VANILLA, "id": "a" * 64,
                  "romtype": "UNOFFICIAL", "size": 42,
                  "url": release["assets"][0]["browser_download_url"], "version": "21.0"}
        with patch.object(generate, "github_json", side_effect=[[dict(release, draft=True), dict(release, prerelease=True), release], []]), \
                patch.object(generate, "download") as download, \
                patch.object(generate, "parse_ota", return_value=update) as parse:
            result = generate.collect("owner/repo", "token")
        self.assertEqual(len(result["UNOFFICIAL"]), 1)
        self.assertEqual(result["UNOFFICIAL-gapps"], [])
        download.assert_called_once()
        parse.assert_called_once()
        original_url = release["assets"][0]["browser_download_url"]
        self.assertEqual(download.call_args.args[0], original_url)
        self.assertEqual(result["UNOFFICIAL"][0][0]["url"], "https://v4.gh-proxy.org/" + original_url)
        with tempfile.TemporaryDirectory() as temp:
            generate.build(result, Path(temp))
            manifest = json.loads((Path(temp) / "updates/P22H190/UNOFFICIAL.json").read_text())
            self.assertEqual(manifest["response"][0]["url"], result["UNOFFICIAL"][0][0]["url"])
            self.assertIn(result["UNOFFICIAL"][0][0]["url"], (Path(temp) / "index.html").read_text())

    def test_both_variants_land_in_separate_feeds(self):
        assets = []
        for name in (VANILLA, GAPPS):
            assets.append({"name": name, "size": 7, "browser_download_url": "https://github.com/owner/repo/releases/download/v1/" + name})
        release = {"draft": False, "prerelease": False, "assets": assets}

        def fake_parse(path, filename, url, expected_size):
            return {"datetime": 1789777202, "filename": filename, "id": "b" * 64,
                    "romtype": "UNOFFICIAL", "size": 7, "url": url, "version": "21.0"}

        with patch.object(generate, "github_json", side_effect=[[release], []]), \
                patch.object(generate, "download"), \
                patch.object(generate, "parse_ota", side_effect=fake_parse) as parse:
            result = generate.collect("owner/repo", "token")
        self.assertEqual(parse.call_count, 2)
        self.assertEqual([entry[0]["filename"] for entry in result["UNOFFICIAL"]], [VANILLA])
        self.assertEqual([entry[0]["filename"] for entry in result["UNOFFICIAL-gapps"]], [GAPPS])
        with tempfile.TemporaryDirectory() as temp:
            generate.build(result, Path(temp))
            vanilla = json.loads((Path(temp) / "updates/P22H190/UNOFFICIAL.json").read_text())
            gapps = json.loads((Path(temp) / "updates/P22H190/UNOFFICIAL-gapps.json").read_text())
            self.assertEqual(vanilla["response"][0]["filename"], VANILLA)
            self.assertEqual(gapps["response"][0]["filename"], GAPPS)

    def test_same_day_rebuild_supersedes_identical_filename(self):
        # Two builds on one day share a filename, so only the newest release may
        # appear in the feed even though both assets exist.
        asset = {"name": VANILLA, "size": 7, "browser_download_url": "https://github.com/owner/repo/releases/download/v1/" + VANILLA}
        newest = {"draft": False, "prerelease": False, "assets": [asset], "id": 2}
        older = {"draft": False, "prerelease": False, "assets": [dict(asset)], "id": 1}

        def fake_parse(path, filename, url, expected_size):
            return {"datetime": 1789777202, "filename": filename, "id": "d" * 64,
                    "romtype": "UNOFFICIAL", "size": 7, "url": url, "version": "21.0"}

        with patch.object(generate, "github_json", side_effect=[[newest, older], []]), \
                patch.object(generate, "download"), \
                patch.object(generate, "parse_ota", side_effect=fake_parse) as parse:
            result = generate.collect("owner/repo", "token")
        self.assertEqual(len(result["UNOFFICIAL"]), 1)
        self.assertEqual(parse.call_count, 1)
        self.assertEqual(result["UNOFFICIAL"][0][1]["id"], 2)


if __name__ == "__main__":
    unittest.main()
