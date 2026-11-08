# P22H190 OTA Pages

Static release page and LineageOS Updater feed for EEBBK A2 / X2 / X2Pro.

Site: `https://weikaizhi4.github.io/p22h190-ota/`

Updater feeds:

| Variant | Package name | Feed |
| --- | --- | --- |
| Standard (vanilla) | `lineage-<ver>-<date>-UNOFFICIAL-P22H190.zip` | `updates/P22H190/UNOFFICIAL.json` |
| GApps | `lineage-<ver>-<date>-UNOFFICIAL-gapps-P22H190.zip` | `updates/P22H190/UNOFFICIAL-gapps.json` |

Both manifests use `romtype=UNOFFICIAL`; the variant is carried by the feed URL, never by `romtype`, because the Updater compares that field with `ro.lineage.releasetype`, which is `UNOFFICIAL` for both builds.

## Set up GitHub Pages

1. Create a **public** repository named `weikaizhi4/p22h190-ota` on GitHub, without generated README or license files.
2. Initialize this directory as a Git repository and push it to the new repository:

   ```sh
   cd ~/AOSP/LineageOS-19/p22h190-ota
   git init -b main
   git add README.md site scripts tests .github .gitignore
   git commit -m "Add P22H190 OTA site and release feed"
   git remote add origin https://github.com/weikaizhi4/p22h190-ota.git
   git push -u origin main
   ```

3. In GitHub repository **Settings → Pages → Build and deployment**, select **GitHub Actions**.
4. Run **Actions → Publish OTA page → Run workflow** once. The first deployment generates an empty feed.

The workflow runs again when a Release is published, edited or deleted. You can also run it manually after changing Release assets.

## Publish an update

1. Build a complete, recovery-installable OTA ZIP signed with a key trusted by the installed system's OTA certificate. An image ZIP or a target-files ZIP is not an OTA package.
2. Name the file after the variant you built, using the **UTC build date** inside `META-INF/com/android/metadata` (`post-timestamp`):
   - standard: `lineage-21.0-YYYYMMDD-UNOFFICIAL-P22H190.zip`
   - GApps: `lineage-21.0-YYYYMMDD-UNOFFICIAL-gapps-P22H190.zip`

   `lineage_p22h190_gapps.mk` sets `TARGET_UNOFFICIAL_BUILD_ID := gapps`, so `mka bacon` already produces the second name. Keep the correct LineageOS version as the ROM version changes.
3. Upload the ZIP to a **published** GitHub Release in this repository. Add release notes to the description. Drafts and prereleases are not exposed in the Updater.
4. Check the Pages workflow status and confirm the generated JSON includes the release. The ZIP must have `pre-device=P22H190`, `ota-type=BLOCK`, and a `post-build` targeting P22H190/user.

Each publish downloads up to the five most recent matching Release ZIPs on GitHub's runner to verify their metadata and compute SHA-256 identifiers. Non-OTA ZIP assets are ignored. Invalid matching OTA packages fail the workflow rather than appearing as an unsafe update. GitHub Releases has per-asset upload limits; check GitHub's current limit before uploading a large ZIP.

The page and Updater feed use `https://v4.gh-proxy.org/` followed by each original GitHub Release download URL. The workflow still downloads the original URL to verify the package before publishing it. To switch mirrors, change `DOWNLOAD_MIRROR` in `scripts/generate.py` and rerun the Pages workflow.

Each variant's image carries its own feed URL in `lineage.updater.uri`, which lives in `/system/build.prop`:

- `device/eebbk/p22h190/device.mk` defaults `P22H190_UPDATER_FEED` to `UNOFFICIAL.json` (standard).
- `device/eebbk/p22h190/lineage_p22h190_gapps.mk` sets `P22H190_UPDATER_FEED := UNOFFICIAL-gapps.json` before inheriting the common product.

Because the property ships inside the system image, an OTA update installs the URI of the variant it came from, so a device stays on its own variant across updates. `ro.p22h190.variant` records which one is installed. Changing these values requires a new system image/OTA to take effect.

The two manifests are structurally identical to the Updater: it compares `romtype` to `ro.lineage.releasetype`, `version` to `ro.lineage.build.version`, and `datetime` to `ro.build.date.utc`; only **newer** builds are offered. Since both variants report `UNOFFICIAL` and `21.0`, the feed URL is the only thing that separates them. This repository does not sign packages: the device's OTA certificate must already trust the release signing key.

## Local check

```sh
python3 -m unittest discover -s tests -v
GITHUB_TOKEN=... python3 scripts/generate.py --repository weikaizhi4/p22h190-ota --output /tmp/p22h190-pages
```

Open the generated `index.html` in a browser. No web server is required.
