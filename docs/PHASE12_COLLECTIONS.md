# Phase 12 — Steam collections

Post-MVP. Native Steam only. Reverse-engineered, not a public Valve contract.

## What this is not

`shortcuts.vdf` `tags` are **not** modern collections. Steam-written shortcuts
on the capture host have empty `tags`. Third-party tools still store names
there. This importer continues to round-trip existing tags and still writes
`tags = {}` for new shortcuts. That is not collection creation.

`localconfig.vdf` has a `user-collections` string. Maintained tools treat it
as a Steam cache. This importer does not write it.

## Where collections live

Under the selected account:

```text
<userdata>/<account_id32>/config/cloudstorage/
  cloud-storage-namespaces.json
  cloud-storage-namespace-1.json
  cloud-storage-namespace-1.modified.json
  cloud-storage-namespace-3.json
```

`cloud-storage-namespaces.json` is `[[namespace_id, "version"], ...]`.
On the capture host that was `[[3,"39"],[1,"9854"]]`. Namespace 1 held
`user-collections.*`. Namespace 3 was unrelated (numeric keys, not
collections). `.modified.json` sidecars were empty `[]` and are left alone.

The importer prefers namespace 1 when that file contains
`user-collections.*` records.

## Record shape

The namespace file is a JSON array of `[key, record]` pairs. Live
collections look like:

```json
[
  "user-collections.uc-XXXXXXXXXXXX",
  {
    "key": "user-collections.uc-XXXXXXXXXXXX",
    "timestamp": 1789078388,
    "value": "{\"id\":\"uc-XXXXXXXXXXXX\",\"name\":\"Linux Apps\",\"added\":[10,3511661831],\"removed\":[]}",
    "version": "9854",
    "conflictResolutionMethod": "custom",
    "strMethodId": "union-collections"
  }
]
```

Deleted records keep `key`, `timestamp`, `is_deleted`, `version` and have no
`value`. They are not resurrected.

`added` is a JSON array of **integers**. On the capture host that mixed
store AppIDs and high-bit non-Steam AppIDs (the same unsigned 32-bit values
as `shortcuts.vdf`). None were 64-bit game IDs. This importer writes the
unsigned 32-bit shortcut AppID.

## Versioning

Steam's own recent writes used the namespaces.json counter (`9854`), not
`max(all versions)+1`. Third-party tools (SRM and some earlier rewrites)
stored `version == unix timestamp` (~1.7e9). The importer bumps the
namespace-1 counter (and collection records it actually edits) using values
below `1_000_000_000`, so a timestamp-as-version record cannot starve
Steam's counter.

New records use `conflictResolutionMethod = custom` and
`strMethodId = union-collections`, matching live collections on the capture
host.

## What the importer will and will not touch

Assignable: `favorite`, `uc-*`, `srm-*`, `boilr*`, `sdi-*`, and anything
else that is live and not skipped.

Skipped: `hidden` (Steam's hidden-games collection) and `from-tag-*`
(store-tag derived). They are listed by `debug collections` as skipped.

New collections created here use an `sdi-` prefix so they are distinguishable
from Steam `uc-` ids and SRM `srm-` ids. Typing a name that already exists
(case-insensitive, assignable only) adds to that collection instead of
creating a duplicate.

Unrelated keys (showcases, rollups, other namespaces) are not edited.
Collections the user did not select are not tombstoned.

## Writes

Same durability rules as `shortcuts.vdf`: Steam closed, same-directory temp,
fsync, parse-back, timestamped backup, `os.replace`. The only writer is
`steam/collection_commit.py`. Import still succeeds if the collection write
fails; the shortcut is already committed. There is no path that edits
collections while Steam is running.

Default import still does **not** invent a collection. The UI starts with
nothing checked and the name field empty. Membership is chosen on a
**Collections** tab next to the application Details inspector: a filterable
checklist and an optional new-name field. Import and Relink stay below the
tabs.

## Capture-host snapshot (structure only, 2026-09-10)

Read-only against account `120415481`:

- namespace 1: 909 records, 713 deleted, 57 live collections
- live prefixes: 42 `uc-*`, 9 `from-tag-*`, 4 `srm-*`, 1 `favorite`, 1 `hidden`
- 2524 `added` integers: 2071 store-range, 453 high-bit, 0 above 32-bit
- 143 of those high-bit ids were also in the current `shortcuts.vdf`
