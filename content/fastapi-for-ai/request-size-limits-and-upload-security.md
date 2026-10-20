---
id: request-size-limits-and-upload-security
module: fastapi-for-ai
name: Request-size limits & upload security
kind: practice
difficulty: core
est_minutes: 17
related:
  - fastapi-for-ai/uploadfile
  - fastapi-for-ai/middleware-ordering
  - fastapi-for-ai/job-queues-and-durable-workers
  - fastapi-for-ai/rate-limiting
  - rag-retrieval/document-parsing
  - safety-security/prompt-injection
  - safety-security/rag-poisoning
  - enterprise-integration/client-document-parsing
sources:
  - OWASP — File Upload Cheat Sheet
  - Official docs: Starlette (Requests)
  - Official docs: nginx (ngx_http_core_module, client_max_body_size)
---

## TL;DR
An AI backend accepts the largest, least trusted inputs in your stack: long prompts, PDFs, images,
audio, zip archives of documents for RAG. Uvicorn and FastAPI put no cap on JSON request body size,
so one oversized or malicious upload can exhaust memory or disk, or a parser can be exploited. Cap
size at every layer, count bytes while streaming, never trust the client's filename or content type,
and parse untrusted files in an isolated, resource-limited worker.

## Why it exists
Upload endpoints are where attackers and accidents meet your most fragile code. Accidentally, a user
drags a 4 GB video into a "chat with your documents" box, and a worker that reads the body into memory
dies, taking every other in-flight request on that process with it. Deliberately, someone sends a
200 KB zip that expands to many gigabytes (a decompression bomb), an image declaring enormous
dimensions so the decoder allocates huge buffers, a "PDF" that is really an HTML or SVG file with
script, a filename like `../../app/main.py`, or a Word document with an XML external-entity payload.
And for AI specifically, the *content* is an attack surface: a valid PDF can contain hidden text that
instructs your model to exfiltrate data once it is retrieved into a prompt.

The naive defences each miss something. "Check `Content-Length`" fails for chunked uploads that send
no length, and for clients that lie. "Check the `.pdf` extension" or the multipart `Content-Type`
trusts values the client chose. "Limit at nginx" is necessary but blind to the file's insides, and
bypassed if the app is ever exposed another way. "We validate after `await file.read()`" is too late:
the bytes are already in memory or on disk. Upload security is defence in depth, because each layer
sees different information.

## How it works
### Layer 1: the edge
Set a body cap at the reverse proxy or gateway (nginx `client_max_body_size`, which defaults to 1 MB,
or your gateway's equivalent). It rejects oversized requests before they consume a Python worker, and
it is the only layer that can cheaply stop a flood of huge bodies. Set it per route: chat JSON might
get 1-2 MB, the document endpoint 50 MB.

### Layer 2: the app, counting bytes
Uvicorn doesn't limit body size, and FastAPI parses JSON bodies fully into memory. Add ASGI
middleware (or a dependency on a streaming route) that:
1. rejects early with **413 Content Too Large** if `Content-Length` exceeds the route's cap, *because*
   that avoids reading anything; and
2. wraps `receive` and counts bytes as `http.request` messages arrive, aborting once the running
   total passes the cap, *because* chunked requests and lying clients have no trustworthy length.

Multipart parsing (used by `UploadFile`) spools files to temporary disk, so a missing cap there
fills the disk instead of RAM. Recent Starlette versions limit the number of multipart fields and
files and the size of non-file parts, but they don't cap total file size for you.

### Layer 3: the file itself
- **Identify by content, not by claims.** Read the first bytes and check the magic number (`%PDF-`,
  PNG's `\x89PNG`, ZIP's `PK\x03\x04`) against an allowlist of types the endpoint accepts. Deny by
  default. Note that DOCX, XLSX and EPUB are all ZIP containers.
- **Rename and isolate storage.** Generate the stored name (a UUID) and keep the original filename
  only as escaped metadata, so path traversal and overwrite are impossible. Store outside any served
  directory, in object storage with private ACLs, scoped by tenant.
- **Bound decompression and decoding.** Cap total uncompressed size, entry count and compression
  ratio when expanding archives. Cap pixel count for images (Pillow raises a decompression-bomb error
  above a configurable `MAX_IMAGE_PIXELS`). Cap page count for PDFs. Disable external entities in XML
  parsers (`defusedxml`).
- **Scan** with antivirus (e.g. ClamAV) when files are shared with other users or downloaded later.

### Layer 4: parsing in isolation
PDF, Office and image parsers are large native codebases with a history of memory-corruption bugs, and
some inputs make them run for minutes. Don't parse in the request worker. Accept and store the file,
return `202` with a job ID, and parse in a separate worker with CPU, memory and wall-clock limits
(container limits or `resource.setrlimit`, plus a timeout), ideally without network access. A crash
or hang then kills one job, not the API.

### Layer 5: the content as untrusted input
Extracted text goes into prompts and indexes. Treat it as data: strip or flag hidden text and
invisible characters, keep per-tenant indexes, and apply prompt-injection defences at retrieval time.
Run PII detection if documents may contain regulated data.

## Example
A pure-ASGI size-limit middleware, plus a type check on the first bytes.

```python
from fastapi import FastAPI, HTTPException, UploadFile
from starlette.types import ASGIApp, Receive, Scope, Send

LIMITS = {"/v1/documents": 50 * 2**20}            # 50 MiB; everything else 2 MiB
DEFAULT = 2 * 2**20

class BodyLimit:
    def __init__(self, app: ASGIApp): self.app = app
    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        cap = LIMITS.get(scope["path"], DEFAULT)
        length = dict(scope["headers"]).get(b"content-length")
        if length and int(length) > cap:
            return await self._413(send)
        seen = 0
        async def counted():
            nonlocal seen
            msg = await receive()
            seen += len(msg.get("body", b""))
            if seen > cap:
                raise HTTPException(413, "request body too large")
            return msg
        await self.app(scope, counted, send)
    async def _413(self, send):
        await send({"type": "http.response.start", "status": 413,
                    "headers": [(b"content-type", b"text/plain")]})
        await send({"type": "http.response.body", "body": b"request body too large"})

app = FastAPI()
app.add_middleware(BodyLimit)
ALLOWED = {b"%PDF-": "application/pdf", b"\x89PNG\r\n\x1a\n": "image/png"}

@app.post("/v1/documents", status_code=202)
async def upload(file: UploadFile):
    head = await file.read(8); await file.seek(0)
    kind = next((t for magic, t in ALLOWED.items() if head.startswith(magic)), None)
    if kind is None:
        raise HTTPException(415, "only PDF and PNG are accepted")
    # store under a generated name in tenant-scoped object storage, enqueue a sandboxed parse job
    return {"job_id": "job_123", "detected_type": kind}
```

The `Content-Length` check rejects an honest 3 GB upload in microseconds. A chunked request lying
about its size is cut off after 50 MiB. Because the counter raises inside FastAPI's form parsing, the
exception surfaces as a 413. A file named `report.pdf` that starts with `<svg` gets 415 regardless
of its name or declared type.

## Trade-offs
- Tight size caps protect workers and cost, but they reject legitimate large documents. Offer a
  separate upload path (direct-to-object-storage with presigned URLs) when real files exceed what the
  API should proxy.
- Parsing synchronously in the request gives users an immediate answer, but it puts native parsers
  and their crashes in your API process. Asynchronous sandboxed parsing is safer and scales, at the
  cost of job status plumbing and a slower first answer.
- Strict allowlists of formats reduce attack surface, but they frustrate users with legitimate .docx
  or .heic files. Widen the list only when you have a hardened parser for each format.
- Antivirus scanning catches known malware in shared files, but it adds latency and infrastructure
  and does nothing against parser exploits or prompt injection, so it pays off mostly when files are
  redistributed.

## Pitfalls
- Relying on `Content-Length` only. The symptom is memory spikes or disk-full alerts from chunked
  uploads that never declared a size.
- Saving files using the client's filename. The symptom is files appearing outside the upload
  directory, or one user's upload overwriting another's identically named file.
- Trusting the multipart `Content-Type` or extension. The symptom is HTML or SVG files with script
  stored as "images" and served back to other users, which is stored XSS.
- Extracting archives without ratio or entry limits. The symptom is a worker killed by the OOM killer,
  or a full disk, from a small upload that expanded.
- Parsing PDFs in the API worker. The symptom is latency spikes and occasional worker crashes that
  correlate with specific customer documents.
- Indexing extracted text without injection handling. The symptom is the assistant following
  instructions that appear nowhere in the visible document.

## When to use
Apply size limits to every endpoint, because JSON bodies are uploads too, and long-context models
tempt clients to send megabytes of prompt. Apply the full upload discipline (magic-byte allowlist,
generated names, private storage, bounded decompression, sandboxed parsing) whenever users submit
files for RAG ingestion, vision or transcription. For files larger than tens of megabytes, have
clients upload directly to object storage with presigned URLs, and give the API only the object key.
Track 413/415 counts, parse-job failures and timeouts by file type, and peak worker RSS. In
interviews, the strong answer names several layers and explains what each one can and can't see.

## Role lens
- **Engineer:** Writes the byte-counting middleware, the magic-byte allowlist and the job hand-off,
  and sets per-route caps in configuration rather than scattering literals.
- **Architect:** Decides the ingestion topology: presigned direct uploads, quarantine buckets, the
  sandboxed parser fleet, and how file content is treated as untrusted at retrieval time.
- **FDE:** Handles a customer's real corpus (scanned 300-page PDFs, password-protected files, odd
  encodings) and adjusts limits and formats without punching holes in the controls.

## Check yourself
**Q:** A customer reports that their document upload endpoint sometimes brings down the whole API pod,
though nginx is configured with a 25 MB limit. Uploads are mostly zipped folders of PDFs. What are the
likely causes and the fixes?
**A:** The nginx cap limits *compressed* bytes, so a 20 MB zip can expand to gigabytes when the app
extracts it. Even a fair archive of PDFs can be heavy once parsed: a 300-page scan rendered for OCR
is huge in memory. If extraction and parsing happen in the API process, one such upload exhausts the
pod's memory and kills all its requests. Fixes: enforce limits on uncompressed size, entry count and
compression ratio while extracting. Move extraction and parsing to a separate worker with memory and
time limits, so the API only stores the file and returns 202 with a job ID. Cap pages per document.
Check magic bytes per entry. Keep the app-level byte counter in case the app is reached around nginx.

## Quiz
### Q: Why is checking `Content-Length` insufficient to enforce an upload size limit?
- [ ] Content-Length is measured in kilobytes, not bytes
- [x] Chunked requests omit it and clients can lie, so the server must count bytes as they arrive
- [ ] FastAPI strips Content-Length before middleware runs
- [ ] Content-Length counts only headers
> The header is client-supplied and absent for chunked transfer encoding. Counting bytes received in
> receive() enforces the cap regardless of what the client claims, while the header check is a fast path.

### Q: Which is the most reliable way to decide what kind of file a user uploaded?
- [ ] The file extension in the filename
- [ ] The Content-Type of the multipart part
- [x] Inspecting the leading magic bytes and checking them against an allowlist
- [ ] Asking the LLM to describe the file
> Extension and Content-Type are both set by the client. Magic bytes come from the content itself, so
> they resist trivial spoofing, though container formats like ZIP still need deeper checks.

### Q: Why parse uploaded PDFs in a separate, resource-limited worker rather than in the request handler?
- [ ] PDF parsing libraries require a separate process to import
- [ ] It makes PDF text extraction more accurate
- [x] Parsers are complex native code that can crash, hang or exhaust memory on hostile inputs, so isolation limits the blast radius
- [ ] FastAPI cannot read files larger than 1 MB in handlers
> Untrusted documents can trigger parser bugs or pathological runtimes. A sandboxed job confines a
> crash or timeout to one file, while the API stays responsive and returns a job ID.
