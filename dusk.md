---
dusk: v1alpha1
namespace: stout
kind: repository
name: binhub.dev
title: BinHub
attributes:
  language: python
  visibility: public
  deploys_to: cloudflare-r2
  url: https://binhub.dev
---

A mirror of common CLI binaries served straight off Cloudflare R2, at one predictable URL per tool, version and platform: `/{letter}/{name}/{version}/{os}-{arch}/{binary}`.
It exists so a CI job or a bootstrap script can `curl` a known-good `kubectl`, `jq` or `tofu` without scraping a vendor's release page and dealing with whatever layout that vendor chose this year.

A tool is one YAML file at `binaries/<first-letter>/<name>.yaml`, naming the upstream download URL, the archive type (`raw`, `zip`, `tar.gz`, `tgz`, `tar`), the path to the binary inside that archive, and a SHA256, per platform.
`processor.py` reads every one of them, downloads and unpacks each binary, writes the nested output tree along with an `api.json` at each level (root lists letters, letter lists names, name lists versions, version lists platforms with sizes and checksums), and generates the static `index.html`.
`.github/workflows/deploy.yml` runs that on every push and `aws s3 sync`s `output/` into the R2 bucket over the S3 API, then purges the Cloudflare zone cache.
Eleven tools are published today.

## Gotchas

**A YAML file carries exactly one version.** The `versions` array in the API comes from grouping files that share a `name` field, so publishing two versions of a tool means two files, for example `binaries/j/jq-1.6.yaml` and `binaries/j/jq-1.7.yaml` both declaring `name: jq`. The README's contributing section reads as though the filename is what matters. It is not; the `name` field is.

**Nothing is ever un-published.** The sync has no `--delete`, and a download that fails is logged rather than fatal, so deleting a YAML file or breaking its upstream URL leaves the last successfully uploaded copy serving from R2 indefinitely.

**Every push re-downloads everything.** There is no caching between runs, so the build's success depends on every upstream release URL in the repository still being alive, including for tools nobody touched.

**`sha256` is optional and skipped silently when absent.** Every entry has one today. One that does not would be downloaded, published and served with no verification at all.
