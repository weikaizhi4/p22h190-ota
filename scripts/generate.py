#!/usr/bin/env python3
"""Build a static release list and LineageOS Updater manifests from GitHub Releases."""

import argparse
import hashlib
import html
import json
import os
import re
import shutil
import sys
import tempfile
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path


DEVICE = "P22H190"
MAX_UPDATES = 5
SITE = Path(__file__).resolve().parent.parent / "site"
API = "https://api.github.com"
DOWNLOAD_MIRROR = "https://v4.gh-proxy.org/"

# Build variant -> feed id. The feed id is what the Updater downloads and what
# names its manifest, so it must match lineage.updater.uri in the device tree.
# The romtype stored inside every manifest stays UNOFFICIAL for both variants,
# because the Updater compares it with ro.lineage.releasetype, which is
# UNOFFICIAL regardless of variant.
VARIANTS = {
    "UNOFFICIAL": {
        "label": "标准版（Vanilla）",
        "pattern": re.compile(r"^lineage-(\d+\.\d+)-(\d{8})-UNOFFICIAL-P22H190\.zip$", re.I),
        "example": "lineage-<version>-<YYYYMMDD>-UNOFFICIAL-P22H190.zip",
    },
    "UNOFFICIAL-gapps": {
        "label": "GApps 版",
        "pattern": re.compile(r"^lineage-(\d+\.\d+)-(\d{8})-UNOFFICIAL-gapps-P22H190\.zip$", re.I),
        "example": "lineage-<version>-<YYYYMMDD>-UNOFFICIAL-gapps-P22H190.zip",
    },
}
ROMTYPE = "UNOFFICIAL"


def classify(filename):
    for feed, spec in VARIANTS.items():
        if spec["pattern"].fullmatch(filename):
            return feed
    return None


def github_json(url, token):
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "p22h190-ota-pages"}
    if token:
        headers["Authorization"] = "Bearer " + token
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=45) as response:
        return json.load(response)


def parse_ota(path, filename, url, expected_size):
    feed = classify(filename)
    if feed is None:
        raise ValueError("OTA filename must be one of: " + ", ".join(spec["example"] for spec in VARIANTS.values()))
    match = VARIANTS[feed]["pattern"].fullmatch(filename)
    version, name_date = match.group(1), match.group(2)

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
    build_date = datetime.fromtimestamp(timestamp, timezone.utc).date()
    if build_date != datetime.strptime(name_date, "%Y%m%d").date():
        raise ValueError(
            f"{filename}: filename says {name_date}, but the package was built on "
            f"{build_date:%Y%m%d} (post-timestamp {timestamp} = {build_date} UTC). "
            f"The build probably reused a stale buildinfo.prop."
        )

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
        "version": version,
    }


def download(url, path, token):
    headers = {"User-Agent": "p22h190-ota-pages"}
    if token:
        headers["Authorization"] = "Bearer " + token
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=120) as source:
        with path.open("wb") as destination:
            shutil.copyfileobj(source, destination, 4 * 1024 * 1024)


def collect(repository, token):
    results = {feed: [] for feed in VARIANTS}
    seen = {feed: set() for feed in VARIANTS}
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
                feed = classify(filename)
                if feed is None:
                    print(f"Skipping non-OTA ZIP: {filename}", file=sys.stderr)
                    continue
                if filename in seen[feed]:
                    # Two builds of one variant on the same day share a filename.
                    # Releases are listed newest first, so keep the newest release.
                    print(f"Skipping duplicate filename: {filename}", file=sys.stderr)
                    continue
                if len(results[feed]) >= MAX_UPDATES:
                    continue
                url = asset["browser_download_url"]
                if not url.startswith(f"https://github.com/{repository}/releases/download/"):
                    raise ValueError(f"Unexpected asset URL: {url}")
                with tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / "update.zip"
                    download(url, path, None)
                    update = parse_ota(path, filename, url, asset["size"])
                update["url"] = DOWNLOAD_MIRROR + url
                results[feed].append((update, release))
                seen[feed].add(filename)
        if all(len(entries) >= MAX_UPDATES for entries in results.values()):
            break
    return {
        feed: sorted(entries, key=lambda entry: entry[0]["datetime"], reverse=True)[:MAX_UPDATES]
        for feed, entries in results.items()
    }


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

    # Always emit every manifest so both links keep resolving even when a
    # variant has nothing published yet; an empty feed is better than a 404.
    for feed in VARIANTS:
        manifest = output / "updates" / DEVICE / (feed + ".json")
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(
            json.dumps({"response": [update for update, _ in results.get(feed, [])]}, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    template = (SITE / "index.html").read_text(encoding="utf-8")
    for feed in VARIANTS:
        start = f"<!-- RELEASES:{feed} -->"
        end = f"<!-- /RELEASES:{feed} -->"
        if template.count(start) != 1 or template.count(end) != 1:
            raise ValueError(f"Release placeholders for {feed} must appear exactly once")
        prefix, rest = template.split(start, 1)
        _, suffix = rest.split(end, 1)
        template = prefix + start + "\n      " + release_markup(results.get(feed, [])) + "\n      " + end + suffix

    (output / "index.html").write_text(template, encoding="utf-8")
    shutil.copyfile(SITE / "style.css", output / "style.css")
    (output / ".nojekyll").touch()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True, help="GitHub owner/repo")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", args.repository):
        parser.error("repository must be owner/repo")

    results = collect(args.repository, os.environ.get("GITHUB_TOKEN"))
    build(results, args.output)
    summary = ", ".join(f"{feed}={len(entries)}" for feed, entries in results.items())
    print(f"Published {summary} updates to {args.output}")


if __name__ == "__main__":
    main()
