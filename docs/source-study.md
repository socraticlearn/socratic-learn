# Source study

`socratic read` prepares one HTML or XHTML page, or a textual PDF, for a
source-bounded learning session. `socratic learn` (or a bare `socratic` launch)
starts general learning without a fixed document.

## Commands

```text
socratic read https://example.org/article
socratic read paper.pdf
socratic read paper.pdf evaluate the main claim and its evidence
socratic read --agent pi paper.pdf explain its evidence
```

For a local development server or private network URL, add the explicit gate:

```text
socratic read --allow-private-network http://localhost/article
```

Explicit read goals and source inputs bypass the bare-launch session chooser.

## What the launcher builds

Socratic Learn creates a private source bundle and a shared study prompt. The
launcher host still owns the provider, model, credentials, quota, and optional
research tools.

### Bundle paths and reuse

Personal bundles:

`~/.socratic-learn/learning/.socratic-learn/workspace/sources/<source-id>/`

With `--here`:

`PROJECT/.socratic-learn/workspace/sources/<source-id>/`

Each bundle contains an index, metadata, normalized sections, and the original
bytes. Identical input reuses a validated bundle instead of publishing a duplicate.

## Supported input

HTTP or HTTPS HTML, XHTML, and regular local PDF files.

| Limit | Value |
| --- | --- |
| Raw input | 20 MiB |
| PDF length | 500 PDF pages |
| Extracted text | 5,000,000 Unicode code points |
| Socket timeout | 20-second read timeout |
| Redirects | five redirects |
| Section chunk | 12,000 code points |

There is no OCR and no JavaScript rendering. Image-only PDFs, encrypted PDFs,
and dynamic pages that need a browser are rejected with an actionable message.

For an image-only PDF, use a textual export. For dynamic pages, save a static
page or PDF.

## Tutor behavior

Study is source-bounded by default. The tutor separates a source assertion,
within-source support, and external verification. External research requires an
explicit learner request, and the tutor must disclose the capability and sources
it used.

Source text is untrusted study material. Prompt-injection resistance and tool
restraint are best-effort controls, not a sandbox. The selected host may still
have network or shell access under its own policy.

## Host and agent changes

A quota or model error belongs to the host provider. To change agents, pass
`--agent` or update `~/.socratic-learn/config.json`. Launch argument details for
read mode are in [Launcher adapters](launcher-adapters.md).
