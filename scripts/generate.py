#!/usr/bin/env python3
"""Build a static release list and LineageOS Updater manifest from GitHub Releases."""

import argparse
import hashlib
import html
import json
import re
import shutil
import sys
import tempfile
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path


DEVICE = "P22H190"
ROMTYPE = "UNOFFICIAL"
MAX_UPDATES = 5
OTA_NAME = re.compile(r"^lineage-(\d+\.\d+)-\d{8}-UNOFFICIAL-P22H190\.zip$", re.I)
SITE = Path(__file__).resolve().parent.parent / "site"
API = "https://api.github.com"
DOWNLOAD_MIRROR = "https://v4.gh-proxy.org/"


def github_json(url, token):
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "p22h190-ota-pages"}
    if token:
        headers["Authorization"] = "Bearer " + token
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=45) as response:
        return json.load(response)


def parse_ota(path, filename, url, expected_size):
    match = OTA_NAME.fullmatch(filename)
    if not match:
        raise ValueError("OTA filename must be lineage-<version>-<YYYYMMDD>-UNOFFICIAL-P22H190.zip")

    size = path.stat().st_size
    if size != expected_size:
        raise ValueError(f"asset size differs: expected {expected_size}, downloaded {size}")

    with zipfile.ZipFile(path) as archive:
        metadata = dict(
            line.split("=", 1)
            for line in archive.read("META-INF/com/android/metadata").decode("utf-8").splitlines()
            if "=" in line
        )
    if DEVICE not in metadata.get("pre-device", "").split(","):
        raise ValueError("OTA pre-device does not match P22H190")
    if metadata.get("ota-type") != "BLOCK":
        raise ValueError("expected a complete BLOCK OTA package")
    post_build = metadata.get("post-build", "")
    if f"/{DEVICE}:" not in post_build or ":user/" not in post_build:
        raise ValueError("OTA post-build must target a P22H190 user build")
    timestamp = int(metadata["post-timestamp"])
    if timestamp <= 0:
        raise ValueError("invalid post-timestamp")
    # The date in the name is the build date, not the release upload date.
    name_date = datetime.strptime(filename.split("-")[2], "%Y%m%d").date()
    if datetime.fromtimestamp(timestamp, timezone.utc).date() != name_date:
        raise ValueError("filename date does not match OTA build timestamp (UTC)")

    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return {
        "datetime": timestamp,
        "filename": filename,
        "id": digest.hexdigest(),
        "romtype": ROMTYPE,
        "size": size,
        "url": url,
        "version": match.group(1),
    }


def download(url, path, token):
    headers = {"User-Agent": "p22h190-ota-pages"}
    if token:
        headers["Authorization"] = "Bearer " + token
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=120) as source:
        with path.open("wb") as destination:
            shutil.copyfileobj(source, destination, 4 * 1024 * 1024)


def collect(repository, token):
    results = []
    for page in range(1, 4):
        releases = github_json(f"{API}/repos/{repository}/releases?per_page=100&page={page}", token)
        if not releases:
            break
        for release in releases:
            if release["draft"] or release["prerelease"]:
                continue
            for asset in release["assets"]:
                filename = asset["name"]
                if not filename.lower().endswith(".zip"):
                    continue
                if not OTA_NAME.fullmatch(filename):
                    print(f"Skipping non-OTA ZIP: {filename}", file=sys.stderr)
                    continue
                url = asset["browser_download_url"]
                if not url.startswith(f"https://github.com/{repository}/releases/download/"):
                    raise ValueError(f"Unexpected asset URL: {url}")
                with tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / "update.zip"
                    download(url, path, None)
                    update = parse_ota(path, filename, url, asset["size"])
                update["url"] = DOWNLOAD_MIRROR + url
                results.append((update, release))
                if len(results) >= MAX_UPDATES:
                    return sorted(results, key=lambda entry: entry[0]["datetime"], reverse=True)
    return sorted(results, key=lambda entry: entry[0]["datetime"], reverse=True)


def release_markup(results):
    if not results:
        return '<div class="empty">暂无公开的完整 OTA 包。</div>'
    cards = []
    for update, release in results:
        label = html.escape(f"LineageOS {update['version']} · {DEVICE}")
        date = datetime.fromtimestamp(update["datetime"], timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        size = f"{update['size'] / 1024**3:.2f} GB"
        notes = (release.get("body") or "").strip()[:1800]
        notes_html = f'<p class="release-note">{html.escape(notes)}</p>' if notes else ""
        cards.append(
            '<article class="release"><div>'
            f'<h3 class="release-title">{label}</h3>'
            f'<div class="release-meta"><span>{date}</span><span>{size}</span>'
            f'<span>SHA-256 {html.escape(update["id"][:12])}…</span></div>{notes_html}'
            '</div>'
            f'<a class="download" href="{html.escape(update["url"], quote=True)}" '
            f'aria-label="下载 {label}">下载 OTA <span aria-hidden="true">↓</span></a>'
            '</article>'
        )
    return "\n      ".join(cards)


def build(results, output):
    output.mkdir(parents=True, exist_ok=True)
    manifest = output / "updates" / DEVICE / (ROMTYPE + ".json")
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(
        json.dumps({"response": [update for update, _ in results]}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    template = (SITE / "index.html").read_text(encoding="utf-8")
    start = "<!-- RELEASES -->"
    end = "<!-- /RELEASES -->"
    if template.count(start) != 1 or template.count(end) != 1:
        raise ValueError("Release placeholders must appear exactly once")
    prefix, rest = template.split(start, 1)
    _, suffix = rest.split(end, 1)
    (output / "index.html").write_text(prefix + start + "\n      " + release_markup(results) + "\n      " + end + suffix, encoding="utf-8")
    shutil.copyfile(SITE / "style.css", output / "style.css")
    (output / ".nojekyll").touch()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True, help="GitHub owner/repo")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", args.repository):
        parser.error("repository must be owner/repo")
    import os

    results = collect(args.repository, os.environ.get("GITHUB_TOKEN"))
    build(results, args.output)
    print(f"Published {len(results)} updates to {args.output}")


if __name__ == "__main__":
    main()
