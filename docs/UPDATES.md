# Reviewed upstream and image updates

The former updater fetched a mutable latest-release archive at startup and every
night, installed files as root, and killed the web UI by parsing `ps`. It could
interrupt recordings and overwrite bundled changes. Version parsing, downloads
and installation were not transactional; the image no longer described the
running engine. It also did not update Alpine/ffmpeg security dependencies.

It has been removed along with its cron entry. The application runs unprivileged
and the bundled engine is root-owned and read-only to it. No runtime job downloads
or installs engine source. Rebuild, validate and replace the image deliberately.
Keep the previous image digest for rollback; back up the profile before deploying
an engine update. Do not replace a production container during an active recording.
No deployment is part of this repository's test suite.

## Import get_iplayer (no submodule)

Start at the repository root with a clean working tree on a review branch.
Choose a specific upstream release from
https://github.com/get-iplayer/get_iplayer/releases and resolve its tag to a full
commit using GitHub's Git references API (dereference an annotated tag if needed).
Record the tag, exact commit, archive URL and SHA-256 in the review. A locally
computed checksum detects later corruption; it is not an upstream signature.
Verify the commit/tag against upstream before approving the import.

Download the archive by **commit**, never `latest`, into a temporary directory.
For example, reproducing this phase's import:

```sh
archive_dir=$(mktemp -d)
curl --fail --location --retry 3 \
  https://api.github.com/repos/get-iplayer/get_iplayer/tarball/df1bc7f3b86f2e994f9a3e1140a50cb7f5d9650f \
  --output "$archive_dir/get_iplayer.tar.gz"
sha256sum "$archive_dir/get_iplayer.tar.gz"
python3 scripts/import-get-iplayer.py \
  --archive "$archive_dir/get_iplayer.tar.gz" \
  --tag v3.36 \
  --commit df1bc7f3b86f2e994f9a3e1140a50cb7f5d9650f \
  --sha256 7140f52ca107ad5a9b818f92f6ee0ab8c15adc405f665195fa4d5ba96064d21d
```

For a new release substitute its reviewed values. The importer checks the archive
hash, rejects unsafe entries, retains the complete source/licence/attribution,
and updates provenance and the pristine file manifest. It refuses uncommitted
vendor changes or an engine tree differing from that manifest. Review the licence
and attribution again on every import. GitHub can regenerate archives: if archive
bytes change, investigate and compare the extracted source rather than bypassing
verification.

Review `git diff -- vendor`, update version expectations in the application metadata and integration
tests, rebuild and run the offline suite. Then test real search/recording in an
isolated output copy before deploying. Commit the upstream import separately from
wrapper changes so future diffs and regressions can be attributed clearly.

When local engine changes are introduced, keep them in separate commits and mark
`local_changes: true` in provenance; the recorded upstream commit remains the
baseline. Preserve these commits before updating. On an update branch, revert the
local engine commits to restore the pristine old source (do not drop the commits),
run the importer, then cherry-pick the local engine commits back and resolve
conflicts. Review and test the combined result. Never regenerate the pristine
manifest from locally patched source just to bypass the importer's protection.
Adapt the pristine-import test when intentional engine patches begin, retaining
the image-versus-working-source comparison.

## Base, packages and AtomicParsley

`Dockerfile` pins Alpine 3.23.5 by multi-platform digest. `docker/build.packages`
and `docker/runtime.packages` lock the **entire resolved package set**, not just
direct dependencies. These locks were resolved and tested on linux/amd64. Other
architectures need their own successful build/test before being claimed supported.

To update, select a supported Alpine release/digest and resolve each stage's
packages in fresh disposable containers using that exact base:

* Build roots: `cmake g++ linux-headers make zlib-dev curl`.
* Runtime roots: `ffmpeg perl-cgi perl-mojolicious perl-lwp-protocol-https
  perl-xml-libxml su-exec tini flock python3 libstdc++`.

Capture `apk list --installed` after `apk add --no-cache` and convert each
`name-version-rN` to `name=version-rN`, including transitive/base packages. Sort
and replace the corresponding lock, review the diff, rebuild without cache and
run the suite. Do not silently loosen constraints when a package disappears.

AtomicParsley is built from `vendor/atomicparsley/source.tar.gz`. Its provenance
and licence sit beside it; the Dockerfile verifies its SHA-256. A future update
must replace the archive, licence/provenance and checksum together after checking
the upstream release commit. The engine itself is always copied from the local
editable `vendor/get_iplayer/` tree, not from an archive during the build.

This gives fixed engine/source/base/package-version inputs, not a guarantee of
bit-for-bit identical image layers or forever-available rebuilds. Alpine mirrors
can remove old APK versions; such a build fails rather than silently upgrading.
For durable releases, retain the resulting image by digest and a package/source
bill of materials. For long-term source rebuilds, archive the exact APKs in a
controlled snapshot (including their signatures) and build against that snapshot.
Refresh dependencies through reviewed, tested image releases; automated tooling
may propose those changes but should not mutate running containers.
