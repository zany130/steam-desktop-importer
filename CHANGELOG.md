# Changelog

## 1.0.0

First stable release. Native Steam is the supported import target.

- Discover FreeDesktop `.desktop` entries and import selected apps as non-Steam shortcuts while Steam is closed.
- Optional SteamGridDB artwork (static by default; NSFW, joke, epilepsy, and animated are opt-in).
- Optional Steam library collections via cloud-storage JSON. Hidden and Dynamic Collections are omitted; store-tag collections are labelled `(tag collection)`.
- Host AppImage packaging (`scripts/build_appimage.sh`).
- Flatpak Steam host launching is experimental and fixture-tested; live TEST-001 is not validated. This importer is not packaged as a Flatpak.
