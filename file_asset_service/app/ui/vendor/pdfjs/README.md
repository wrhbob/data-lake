# PDF.js vendored runtime

- Version: `5.7.284`
- Source: `pdfjs-dist@5.7.284`
- Project: <https://github.com/mozilla/pdf.js>
- License: Apache-2.0 (see `LICENSE`)

Only the browser runtime, worker, packed CMaps, standard fonts, and WASM helpers required by the local archive viewer are included. The UI loads these files from `/ui-assets/vendor/pdfjs/`; it does not depend on a public CDN.
